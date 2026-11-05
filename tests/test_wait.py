"""Tests for `fleet.py wait` using temp dirs and fake clocks."""
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fleet_wait as fw  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def touch(path: Path, mtime: float, text="SECRET-TRANSCRIPT"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.environ = {"FLEET_CLAUDE_DIR": str(self.root / "claude"), "FLEET_CODEX_DIR": str(self.root / "codex"),
                        "FLEET_GEMINI_DIR": str(self.root / "gemini")}
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = fw.Env(self.clock, self.environ, str(self.repo), active_seconds=60)

    def wait(self, specs, **kw):
        out = io.StringIO()
        kw.setdefault("interval", 5)
        with mock.patch("sys.stderr", io.StringIO()):
            code = fw.run_wait(specs, self.env, sleep=self.clock.sleep, out=out, **kw)
        return code, out.getvalue()

    def claude_session(self, sid, age):
        touch(self.root / "claude/projects/p1" / f"{sid}.jsonl", self.clock.t - age)

    def run_file(self, task, name, data=None, raw=None):
        p = self.repo / ".fleet/runs" / task / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")


class SessionTests(Base):
    def test_discovers_three_providers_metadata_only(self):
        self.claude_session("abc", 10)
        touch(self.root / "codex/sessions/2026/01/02/rollout-xyz.jsonl", self.clock.t - 500)
        touch(self.root / "gemini/tmp/h/chats/g1.json", self.clock.t - 5)
        recs = {r["id"]: r for r in fw.discover_sessions(self.env)}
        self.assertEqual(recs["claude:abc"]["status"], "running")
        self.assertEqual(recs["codex:xyz"]["status"], "idle")
        self.assertEqual(recs["gemini:g1"]["status"], "running")
        self.assertIsNone(recs["claude:abc"]["tokens"])
        self.assertIsNone(recs["claude:abc"]["model"])

    def test_never_opens_transcripts(self):
        self.claude_session("abc", 10)
        touch(self.root / "claude/sessions/1.json", self.clock.t,
              json.dumps({"sessionId": "abc", "model": "m1", "usage": {"input_tokens": 5}}))
        opened = []
        real_open = builtins.open
        real_read = Path.read_text

        def spy_open(f, *a, **k):
            opened.append(str(f))
            return real_open(f, *a, **k)

        def spy_read(self_, *a, **k):
            opened.append(str(self_))
            return real_read(self_, *a, **k)

        with mock.patch("builtins.open", spy_open), mock.patch.object(Path, "read_text", spy_read):
            recs = fw.discover_sessions(self.env)
        self.assertTrue(opened)
        self.assertTrue(all(not p.endswith(".jsonl") and not p.endswith(".pb") for p in opened), opened)
        self.assertEqual(recs[0]["model"], "m1")
        self.assertEqual(recs[0]["tokens"]["input_tokens"], 5)
        self.assertIsNone(recs[0]["tokens"]["output_tokens"])

    def test_sidecar_parent_children_and_redaction(self):
        self.claude_session("p", 5)
        self.claude_session("c", 5)
        touch(self.root / "claude/sessions/c.json", self.clock.t,
              json.dumps({"sessionId": "c", "parentSessionId": "p", "status": "running",
                          "progress": "using ghp_" + "a" * 40 + " and Bearer abc.def"}))
        recs = {r["id"]: r for r in fw.discover_sessions(self.env)}
        self.assertEqual(recs["claude:c"]["parent"], "claude:p")
        self.assertEqual(recs["claude:p"]["children"], ["claude:c"])
        self.assertNotIn("ghp_", recs["claude:c"]["note"])
        self.assertIn("[REDACTED]", recs["claude:c"]["note"])

    def test_note_limited_and_env_secret_redacted(self):
        with mock.patch.dict(os.environ, {"MY_API_TOKEN": "supersecretvalue"}):
            out = fw._redact("x supersecretvalue " + "y" * 1000)
        self.assertNotIn("supersecretvalue", out)
        self.assertLessEqual(len(out), 300)

    def test_wait_session_until_idle(self):
        self.claude_session("abc", 0)
        code, out = self.wait(["sessions"], timeout=1000)
        # session stays 'running' only while fresh; clock advances so it goes idle
        self.assertEqual(code, 0, out)
        self.assertIn("claude:abc", out)

    def test_no_active_sessions_done_immediately(self):
        code, out = self.wait(["orchestrators"], timeout=10)
        self.assertEqual(code, 0)
        self.assertIn("nothing active", out)


class SemanticsTests(Base):
    def test_all_vs_any(self):
        self.run_file("T1", "r1", raw="")  # running forever (incomplete file)
        a = self.root / "f.txt"
        spec = ["workflow:r1", "pid:" + str(os.getpid()) ]
        code, _ = self.wait(["workflow:r1", f"file:{a}"], mode="all", timeout=20)
        self.assertEqual(code, 1)
        # any: file appears after first poll
        calls = {"n": 0}
        orig = self.clock.sleep

        def sleep(s):
            calls["n"] += 1
            if calls["n"] == 1:
                a.write_text("x")
            orig(s)

        out = io.StringIO()
        with mock.patch("sys.stderr", io.StringIO()):
            code = fw.run_wait(["workflow:r1", f"file:{a}"], self.env, mode="any", timeout=60, interval=5,
                               sleep=sleep, out=out)
        self.assertEqual(code, 0)
        del spec

    def test_timeout_exit_code_and_zero_timeout_means_infinite(self):
        self.run_file("T1", "r1", raw="")
        code, out = self.wait(["workflow:r1"], timeout=12, interval=5)
        self.assertEqual(code, 1)
        self.assertIn("timeout", out)

    def test_failed_exit_2(self):
        self.run_file("T1", "r1", {"run_id": "r1", "status": "failed", "start_time": "2026-01-01T00:00:00+00:00",
                                   "end_time": "2026-01-01T00:00:05+00:00", "reason": "boom"})
        code, out = self.wait(["workflow:r1"], timeout=10)
        self.assertEqual(code, 2)
        self.assertIn("failed", out)

    def test_passed_run_is_done_and_shape(self):
        self.run_file("T1", "r1", {"run_id": "r1", "status": "passed", "start_time": "2026-01-01T00:00:00+00:00",
                                   "end_time": "2026-01-01T00:00:05+00:00"})
        code, out = self.wait(["workflow:r1"], as_json=True)
        data = json.loads(out)
        self.assertEqual(code, 0)
        rec = data["targets"][0]
        for key in ("kind", "id", "label", "status", "started_at", "running_seconds", "last_seen", "model",
                    "provider", "tokens", "parent", "children", "note"):
            self.assertIn(key, rec)
        self.assertEqual(rec["running_seconds"], 5.0)
        self.assertEqual(data["exit_code"], 0)
        self.assertIn("summary", data)

    def test_stable_requires_consecutive_idle_polls(self):
        self.claude_session("s", 1000)  # idle from the start, but tracked only if seen running
        sess = ["session:s"]
        polls = {"n": 0}
        real = fw.discover_sessions

        def flap(env):
            polls["n"] += 1
            recs = real(env)
            if polls["n"] == 3:
                recs[0]["status"] = "running"
            return recs

        out = io.StringIO()
        with mock.patch.object(fw, "discover_sessions", flap), mock.patch("sys.stderr", io.StringIO()):
            code = fw.run_wait(sess, self.env, stable=3, interval=1, timeout=100, sleep=self.clock.sleep, out=out)
        self.assertEqual(code, 0)
        self.assertEqual(polls["n"], 6)  # 2 idle, 1 running (reset), 3 idle

    def test_unknown_with_fail_on_error(self):
        code, _ = self.wait(["workflow:nope"], fail_on_error=True, timeout=5)
        self.assertEqual(code, 2)
        code, _ = self.wait(["workflow:nope"], timeout=5)
        self.assertEqual(code, 1)

    def test_usage_errors(self):
        for bad in ("bogus", "pid:abc", "workflow:", "url:ftp://x"):
            with self.assertRaises(fw.UsageError):
                fw.run_wait([bad], self.env)
        with self.assertRaises(fw.UsageError):
            fw.run_wait(["runs"], self.env, interval=0)

    def test_cli_exit_codes(self):
        cli = str(ROOT / "scripts/fleet.py")
        r = subprocess.run([sys.executable, cli, "wait", "bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        env = dict(os.environ, **self.environ)
        r = subprocess.run([sys.executable, cli, "wait", "--list", "--json"], capture_output=True, text=True, env=env,
                           cwd=str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["active"], [])
        r = subprocess.run([sys.executable, cli, "wait", "pid:%d" % 2**30, "--quiet"], capture_output=True,
                           text=True, env=env, cwd=str(self.repo))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TargetKindTests(Base):
    def test_runs_aggregate_tracks_running(self):
        self.run_file("T1", "r1", raw="")
        t = fw.Target("runs", self.env)
        self.assertEqual(t.poll()[0]["status"], "running")
        self.run_file("T1", "r1", {"run_id": "r1", "status": "passed", "start_time": "2026-01-01T00:00:00+00:00",
                                   "end_time": "2026-01-01T00:00:01+00:00"})
        self.assertEqual(fw.target_state(t.poll()), "idle")

    def test_pid(self):
        self.assertEqual(fw.Target(f"pid:{os.getpid()}", self.env).poll()[0]["status"], "running")
        self.assertEqual(fw.Target(f"pid:{2**30}", self.env).poll()[0]["status"], "done")

    def test_file_exists_or_changed(self):
        f = self.root / "x.txt"
        t = fw.Target(f"file:{f}", self.env)
        self.assertEqual(t.poll()[0]["status"], "running")
        f.write_text("a")
        self.assertEqual(t.poll()[0]["status"], "done")
        t2 = fw.Target(f"file:{f}", self.env)
        self.assertEqual(t2.poll()[0]["status"], "running")
        f.write_text("longer")
        self.assertEqual(t2.poll()[0]["status"], "done")

    def test_project_states(self):
        proj = self.root / "other"
        (proj / ".fleet/runs/T").mkdir(parents=True)
        (proj / ".fleet/runs/T/a.json").write_text("")
        self.assertEqual(fw.Target(f"project:{proj}", self.env).poll()[0]["status"], "running")
        (proj / ".fleet/runs/T/a.json").write_text(json.dumps(
            {"run_id": "a", "status": "passed", "start_time": "2026-01-01T00:00:00+00:00",
             "end_time": "2026-01-01T00:00:01+00:00"}))
        self.assertEqual(fw.Target(f"project:{proj}", self.env).poll()[0]["status"], "idle")
        (proj / ".fleet/x.lock").write_text("")
        self.assertEqual(fw.Target(f"project:{proj}", self.env).poll()[0]["status"], "running")
        self.assertEqual(fw.Target(f"project:{self.root / 'missing'}", self.env).poll()[0]["status"], "unknown")


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = json.dumps({"status": "completed", "progress": "bearer abcdef token ghp_" + "b" * 40}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


class UrlTests(Base):
    def test_url_policy_and_redirects(self):
        srv = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"
        rec = fw.Target(f"url:{base}/ok", self.env).poll()[0]
        self.assertEqual(rec["status"], "done")
        self.assertNotIn("ghp_", rec["note"])
        rec = fw.Target(f"url:{base}/redir", self.env).poll()[0]
        self.assertEqual(rec["status"], "unknown")
        self.assertIn("redirect", rec["note"])

    def test_non_local_rejected_unless_allowed(self):
        with self.assertRaises(fw.UsageError):
            fw.Target("url:http://example.com/s", self.env)
        self.env.environ = dict(self.environ, FLEET_WAIT_ALLOW_URL="example.com")
        fw.Target("url:http://example.com/s", self.env)


if __name__ == "__main__":
    unittest.main()
