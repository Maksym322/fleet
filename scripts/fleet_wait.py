"""Fleet wait: block until local AI sessions, Fleet runs, projects, pids, files or URLs are idle.

Metadata only. Session transcripts, prompts and histories are never opened: session
files are stat()-ed, and only small whitelisted JSON sidecar files are parsed.
Pure standard library; clock and sleep are injectable for tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EXIT_DONE, EXIT_TIMEOUT, EXIT_FAILED, EXIT_USAGE = 0, 1, 2, 3
RUNNING, IDLE, DONE, FAILED, UNKNOWN = "running", "idle", "done", "failed", "unknown"
MAX_NOTE = 300
MAX_FILES = 2000
MAX_SIDECAR = 64 * 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SESSION_ENV = {"claude": "FLEET_CLAUDE_DIR", "codex": "FLEET_CODEX_DIR", "gemini": "FLEET_GEMINI_DIR"}
DEFAULT_DIRS = {"claude": ".claude", "codex": ".codex", "gemini": ".gemini"}
FAIL_WORDS = {"failed", "error", "errored", "timed_out", "interrupted", "stale", "unavailable", "crashed"}
RUN_WORDS = {"running", "in_progress", "active", "busy", "working", "started", "pending"}
DONE_WORDS = {"passed", "done", "completed", "complete", "success", "succeeded", "finished", "ok"}
SESSION_GLOBS = {
    "claude": ["projects/*/*.jsonl"],
    "codex": ["sessions/*/*/*/rollout-*.jsonl", "sessions/rollout-*.jsonl"],
    "gemini": ["tmp/*/chats/*.json", "antigravity/conversations/*.pb"],
}
SIDECAR_GLOBS = {"claude": ["sessions/*.json"], "codex": ["fleet-sessions/*.json"], "gemini": ["fleet-sessions/*.json"]}


class UsageError(Exception):
    pass


def _redact(text: Any) -> str | None:
    if text is None:
        return None
    from fleet import collect_env_secrets, redact_text
    out = redact_text(str(text), collect_env_secrets([]))
    out = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}", "[REDACTED]", out)
    out = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]", out)
    return " ".join(out.split())[:MAX_NOTE]


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            p = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (p if p.tzinfo else p.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            return None
    return None


def _count(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            return re.search(rf"\b{pid}\b", out) is not None
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def norm_status(value: Any) -> str:
    s = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in FAIL_WORDS:
        return FAILED
    if s in RUN_WORDS:
        return RUNNING
    if s == "idle":
        return IDLE
    if s in DONE_WORDS:
        return DONE
    return UNKNOWN


def record(kind: str, id_: str, label: str | None = None, status: str = UNKNOWN, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"kind": kind, "id": id_, "label": label or id_, "status": status, "started_at": None,
                         "running_seconds": None, "last_seen": None, "model": None, "provider": None,
                         "tokens": None, "parent": None, "children": [], "note": None}
    r.update(kw)
    r["note"] = _redact(r["note"])
    return r


class Env:
    """Injectable environment: clock, environment variables and repo root."""

    def __init__(self, clock: Callable[[], float] = time.time, environ: dict[str, str] | None = None,
                 repo: str = ".", active_seconds: float | None = None) -> None:
        self.clock = clock
        self.environ = os.environ if environ is None else environ
        self.repo = Path(repo)
        if active_seconds is None:
            try:
                active_seconds = float(self.environ.get("FLEET_ACTIVE_SECONDS", "120"))
            except ValueError:
                active_seconds = 120.0
        self.active_seconds = active_seconds

    def tool_dir(self, provider: str) -> Path:
        override = self.environ.get(SESSION_ENV[provider])
        if override:
            return Path(override)
        home = self.environ.get("USERPROFILE") or self.environ.get("HOME") or str(Path.home())
        return Path(home) / DEFAULT_DIRS[provider]


def _glob_limited(base: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    if not base.is_dir():
        return out
    for pat in patterns:
        for p in base.glob(pat):
            out.append(p)
            if len(out) >= MAX_FILES:
                return out
    return out


def _read_sidecar(path: Path) -> dict[str, Any]:
    """Parse a small status sidecar, keeping only whitelisted metadata fields."""
    try:
        if path.stat().st_size > MAX_SIDECAR:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens: dict[str, int | None] | None = {k: _count(usage.get(k)) for k in ("input_tokens", "output_tokens", "total_tokens")}
    if all(v is None for v in tokens.values()):
        tokens = None
    return {"id": data.get("sessionId") or data.get("id"), "pid": _count(data.get("pid")),
            "started": _parse_ts(data.get("startedAt") or data.get("started_at")),
            "status": data.get("status"), "model": data.get("model") if isinstance(data.get("model"), str) else None,
            "parent": data.get("parentSessionId") or data.get("parent"), "tokens": tokens,
            "note": data.get("progress") if isinstance(data.get("progress"), str) else None}


def discover_sessions(env: Env) -> list[dict[str, Any]]:
    now = env.clock()
    found: dict[tuple[str, str], dict[str, Any]] = {}
    mtimes: dict[str, float] = {}
    for provider in SESSION_ENV:
        base = env.tool_dir(provider)
        for p in _glob_limited(base, SESSION_GLOBS[provider]):
            try:
                st = p.stat()  # stat only: transcripts are never opened
            except OSError:
                continue
            sid = p.stem.replace("rollout-", "")
            rec = record("session", f"{provider}:{sid}", f"{provider} session {sid[:12]}",
                         RUNNING if now - st.st_mtime <= env.active_seconds else IDLE,
                         last_seen=_iso(st.st_mtime), provider=provider)
            found[(provider, sid)] = rec
            mtimes[rec["id"]] = st.st_mtime
        for p in _glob_limited(base, SIDECAR_GLOBS[provider]):
            meta = _read_sidecar(p)
            if not meta:
                continue
            sid = str(meta.get("id") or p.stem)
            rec = found.get((provider, sid)) or record("session", f"{provider}:{sid}", f"{provider} session {sid[:12]}",
                                                       IDLE, provider=provider)
            found[(provider, sid)] = rec
            try:
                mt = p.stat().st_mtime
                if mt > mtimes.get(rec["id"], 0):
                    mtimes[rec["id"]] = mt
                    rec["last_seen"] = _iso(mt)
            except OSError:
                pass
            if meta["started"] is not None:
                rec["started_at"] = _iso(meta["started"])
            declared = norm_status(meta["status"]) if meta["status"] else None
            fresh = now - mtimes.get(rec["id"], 0) <= env.active_seconds
            if declared in (FAILED, DONE, IDLE):
                rec["status"] = declared
            elif meta["pid"] is not None:
                rec["status"] = RUNNING if pid_alive(meta["pid"]) else IDLE
            elif declared == RUNNING:
                rec["status"] = RUNNING if fresh else IDLE
            rec["model"] = meta["model"]
            rec["tokens"] = meta["tokens"]
            rec["note"] = meta["note"]
            if meta["parent"]:
                rec["parent"] = f"{provider}:{meta['parent']}"
    recs = list(found.values())
    by_id = {r["id"]: r for r in recs}
    for r in recs:
        if r["parent"] in by_id:
            by_id[r["parent"]]["children"].append(r["id"])
        ts = _parse_ts(r["started_at"])
        if ts is not None:
            end = now if r["status"] == RUNNING else mtimes.get(r["id"], now)
            r["running_seconds"] = round(max(0.0, end - ts), 1)
        r["note"] = _redact(r["note"])
    return sorted(recs, key=lambda r: r["id"])


def _read_run(path: Path, now: float) -> dict[str, Any]:
    task = path.parent.name
    try:
        mt = path.stat().st_mtime
    except OSError:
        mt = now
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return record("workflow", path.stem, f"{task}/{path.stem}", RUNNING, last_seen=_iso(mt),
                      parent=task, note="run file not yet complete")
    if not data.get("end_time"):
        status = FAILED if norm_status(data.get("status")) == FAILED else RUNNING
    else:
        status = norm_status(data.get("status"))
        if status == UNKNOWN:
            status = FAILED
    st, en = _parse_ts(data.get("start_time")), _parse_ts(data.get("end_time"))
    run_sec = round(max(0.0, (en if en is not None else now) - st), 1) if st is not None else None
    return record("workflow", str(data.get("run_id") or path.stem), f"{task}/{path.stem}", status,
                  started_at=_iso(st), running_seconds=run_sec, last_seen=_iso(en or st or mt),
                  note=data.get("reason"), parent=task)


def discover_runs(repo: Path, now: float) -> list[dict[str, Any]]:
    root = repo / ".fleet" / "runs"
    if not root.is_dir():
        return []
    return [_read_run(p, now) for p in sorted(root.glob("*/*.json"))[:MAX_FILES]]


def probe_project(repo: Path, env: Env) -> list[dict[str, Any]]:
    now = env.clock()
    if not repo.is_dir():
        return [record("project", repo.name or str(repo), str(repo), UNKNOWN, note="path not found")]
    runs = discover_runs(repo, now)
    latest: dict[str, dict[str, Any]] = {}
    for r in runs:
        t = r["parent"]
        if t not in latest or (r["last_seen"] or "") >= (latest[t]["last_seen"] or ""):
            latest[t] = r
    running = sorted(r["id"] for r in latest.values() if r["status"] == RUNNING)
    locks = [p for pat in (".git/*.lock", ".fleet/*.lock", ".fleet/runs/*/*.lock") for p in repo.glob(pat)]
    newest = None
    for lock in locks:
        try:
            newest = max(newest or 0, lock.stat().st_mtime)
        except OSError:
            pass
    if newest is None:
        for p in (repo / ".git" / "index", repo / ".git" / "HEAD"):
            try:
                if now - p.stat().st_mtime <= env.active_seconds:
                    newest = p.stat().st_mtime  # recent git activity
            except OSError:
                pass
    status = RUNNING if (running or locks or newest is not None) else IDLE
    return [record("project", repo.name or str(repo), str(repo), status, last_seen=_iso(newest), children=running,
                   note=f"{len(runs)} run file(s); {len(running)} running; {len(locks)} lock file(s)")]


def check_url(url: str, env: Env) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UsageError("url target must be http(s)")
    if u.hostname not in LOCAL_HOSTS and env.environ.get("FLEET_WAIT_ALLOW_URL", "") != u.hostname:
        raise UsageError("url target is localhost-only; set FLEET_WAIT_ALLOW_URL=<host> to allow that exact host")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


def fetch_status(url: str) -> tuple[str, str | None]:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=10) as resp:
            body = resp.read(MAX_SIDECAR)
        data = json.loads(body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        exc.close()
        return UNKNOWN, ("redirect refused" if 300 <= exc.code < 400 else f"HTTP {exc.code}")
    except Exception as exc:
        return UNKNOWN, f"unreachable: {type(exc).__name__}"
    if not isinstance(data, dict) or "status" not in data:
        return UNKNOWN, "response has no status field"
    note = data.get("progress") or data.get("message")
    return norm_status(data["status"]), note if isinstance(note, str) else None


class Target:
    KINDS = {"orchestrators", "sessions", "runs", "session", "workflow", "project", "pid", "file", "url"}

    def __init__(self, spec: str, env: Env) -> None:
        self.spec, self.env = spec, env
        self.tracked: set[str] = set()
        self.baseline: Any = None
        kind, _, arg = spec.partition(":")
        self.kind, self.arg = kind.lower(), arg
        if self.kind not in self.KINDS:
            raise UsageError(f"unknown target kind: {spec!r}")
        if self.kind not in ("orchestrators", "sessions", "runs") and not arg:
            raise UsageError(f"target {self.kind!r} needs a value, e.g. {self.kind}:<value>")
        if self.kind == "pid" and not (arg.isdigit() and int(arg) > 0):
            raise UsageError("pid target needs a positive integer")
        if self.kind == "url":
            check_url(arg, env)

    def poll(self) -> list[dict[str, Any]]:
        e, now, k = self.env, self.env.clock(), self.kind
        if k in ("orchestrators", "sessions", "runs"):
            allr = discover_sessions(e) if k != "runs" else discover_runs(e.repo, now)
            self.tracked.update(r["id"] for r in allr if r["status"] == RUNNING)
            return [r for r in allr if r["id"] in self.tracked]
        if k == "session":
            recs = [r for r in discover_sessions(e) if r["id"] == self.arg or r["id"].split(":", 1)[1] == self.arg]
            return recs or [record("session", self.arg, status=UNKNOWN, note="session not found")]
        if k == "workflow":
            recs = [r for r in discover_runs(e.repo, now) if r["id"] == self.arg or r["parent"] == self.arg]
            if not recs:
                return [record("workflow", self.arg, status=UNKNOWN, note="run not found")]
            exact = [r for r in recs if r["id"] == self.arg]
            return exact or [max(recs, key=lambda r: r["last_seen"] or "")]
        if k == "project":
            return probe_project(Path(self.arg), e)
        if k == "pid":
            alive = pid_alive(int(self.arg))
            return [record("pid", self.arg, f"pid {self.arg}", RUNNING if alive else DONE, last_seen=_iso(now))]
        if k == "file":
            return [self._file()]
        status, note = fetch_status(self.arg)
        return [record("url", self.arg, self.arg, status, last_seen=_iso(now), note=note)]

    def _file(self) -> dict[str, Any]:
        try:
            st = Path(self.arg).stat()
            sig: Any = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        if self.baseline is None:
            self.baseline = ("missing",) if sig is None else sig
        changed = sig is not None and sig != self.baseline
        note = "file exists/changed" if changed else ("waiting for file to appear" if sig is None else "waiting for file to change")
        return record("file", self.arg, self.arg, DONE if changed else RUNNING,
                      last_seen=_iso(sig[0] / 1e9) if sig else None, note=note)


def target_state(recs: list[dict[str, Any]]) -> str:
    sts = {r["status"] for r in recs}
    if FAILED in sts:
        return FAILED
    if RUNNING in sts:
        return RUNNING
    if UNKNOWN in sts:
        return UNKNOWN
    return IDLE


def summarize(recs: list[dict[str, Any]], outcome: str, waited: float) -> str:
    c: dict[str, int] = {}
    for r in recs:
        c[r["status"]] = c.get(r["status"], 0) + 1
    parts = ", ".join(f"{n} {s}" for s, n in sorted(c.items())) or "nothing active"
    return f"fleet wait: {outcome} after {waited:.0f}s ({parts})"


def render(recs: list[dict[str, Any]]) -> str:
    lines = []
    for r in recs:
        lines.append(f"[{r['status']}] {r['kind']} {r['id']} ({r['label']})")
        lines.append(f"    started_at={r['started_at']} running_seconds={r['running_seconds']} last_seen={r['last_seen']}")
        lines.append(f"    model={r['model']} provider={r['provider']} tokens={json.dumps(r['tokens']) if r['tokens'] else None}")
        lines.append(f"    parent={r['parent']} children={r['children'] or None}")
        if r["note"]:
            lines.append(f"    note: {r['note']}")
    return "\n".join(lines)


def run_wait(specs: list[str], env: Env, mode: str = "all", timeout: float = 3600, interval: float = 5,
             stable: int = 1, as_json: bool = False, quiet: bool = False, fail_on_error: bool = False,
             sleep: Callable[[float], None] = time.sleep, out: Any = None) -> int:
    out = out or sys.stdout
    if mode not in ("any", "all") or interval <= 0 or stable < 1 or timeout < 0:
        raise UsageError("invalid --any/--all, --interval, --stable or --timeout value")
    targets = [Target(s, env) for s in specs] or [Target("orchestrators", env), Target("runs", env)]
    start = env.clock()
    streak = [0] * len(targets)
    outcome, code = "timeout", EXIT_TIMEOUT
    recs: list[dict[str, Any]] = []
    while True:
        recs, states = [], []
        for i, t in enumerate(targets):
            rs = t.poll()
            for r in rs:
                r["target"] = t.spec
            recs.extend(rs)
            st = target_state(rs)
            states.append(st)
            streak[i] = streak[i] + 1 if st == IDLE else 0
        done_flags = [s >= stable for s in streak]
        if any(s == FAILED for s in states) or (fail_on_error and UNKNOWN in states):
            outcome, code = "failed", EXIT_FAILED
            break
        if all(done_flags) if mode == "all" else any(done_flags):
            outcome, code = "done", EXIT_DONE
            break
        elapsed = env.clock() - start
        if timeout and elapsed >= timeout:
            break
        if not quiet and not as_json:
            print(f"waiting... {sum(done_flags)}/{len(targets)} targets finished, {elapsed:.0f}s elapsed", file=sys.stderr)
        sleep(interval)
    waited = env.clock() - start
    summary = summarize(recs, outcome, waited)
    if as_json:
        print(json.dumps({"outcome": outcome, "exit_code": code, "waited_seconds": round(waited, 1), "mode": mode,
                          "targets": recs, "summary": summary}, indent=2), file=out)
    else:
        if not quiet:
            print(render(recs), file=out)
        print(summary, file=out)
    return code


def run_list(env: Env, as_json: bool = False, out: Any = None) -> int:
    out = out or sys.stdout
    recs = [r for r in discover_sessions(env) + discover_runs(env.repo, env.clock()) if r["status"] == RUNNING]
    if as_json:
        print(json.dumps({"active": recs, "summary": f"{len(recs)} active"}, indent=2), file=out)
    else:
        if recs:
            print(render(recs), file=out)
        print(f"fleet wait --list: {len(recs)} active", file=out)
    return EXIT_DONE
