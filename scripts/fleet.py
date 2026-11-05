#!/usr/bin/env python3
"""Fleet: deterministic helper CLI for task-based AI-assisted development.

Guides an agent through planning, implementation, verification, and review
for Git tasks without requiring external LLM or daemon dependencies.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import shlex
import shutil
import subprocess
import sys
from typing import Any
import uuid

# Valid identifier pattern: [A-Za-z0-9][A-Za-z0-9_-]{0,63}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Redaction patterns
SECRET_NAME_PATTERN = re.compile(r"(TOKEN|KEY|PASSWORD|SECRET|CREDENTIAL)", re.IGNORECASE)
BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9_\-\.~+/]+=*)")
GITHUB_TOKEN_PATTERN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b")
GITHUB_PAT_PATTERN = re.compile(r"\b(github_pat_[A-Za-z0-9_]{22,})\b")

MAX_OUTPUT_BYTES = 64 * 1024  # 64 KiB

ALLOWLISTED_ENV_VARS = {
    # System execution & path
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "SHELL",
    "TERM",
    # User & home
    "HOME",
    "USER",
    "LOGNAME",
    "USERNAME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    # Temporary files
    "TMP",
    "TEMP",
    "TMPDIR",
    # Locale & charset
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_COLLATE",
    "LC_TIME",
    "LC_NUMERIC",
    "LC_MONETARY",
    # Windows system environment
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "ALLUSERSPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_ARCHITEW6432",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "OS",
    "PUBLIC",
    # Python essentials
    "PYTHONIOENCODING",
    "PYTHONUTF8",
}

MODE_BOUNDARIES = {
    "plan": (
        "- Analyze requirements, outcome, scope, and context files.\n"
        "- Outline architectural approach, sub-steps, and verification test cases.\n"
        "- Do not modify production source code or tests during planning.\n"
        "- Establish a clear verification strategy before moving to implementation."
    ),
    "implement": (
        "- Make minimal, correct changes strictly within declared scope.\n"
        "- Implement tests and functionality to satisfy all acceptance criteria.\n"
        "- Maintain existing project conventions, comments, and structure.\n"
        "- Do not alter configuration or out-of-scope files."
    ),
    "review": (
        "- Conduct a read-only review of implemented changes against acceptance criteria.\n"
        "- Check for regressions, edge cases, and code maintainability.\n"
        "- Verify that automated checks are passing and current.\n"
        "- Do not perform file edits in review mode."
    ),
    "release": (
        "- Verify all automated checks have current passing evidence.\n"
        "- Verify all task dependencies are verified.\n"
        "- Ensure clean working tree without leftover temporary files.\n"
        "- Prepare concise release summary; do not bypass verification checks."
    ),
}


class ConfigError(Exception):
    """Configuration or validation error resulting in exit code 2."""
    pass


def run_git_cmd(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Execute a local git command within the specified repository directory."""
    return subprocess.run(
        ["git", "-C", str(repo_dir)] + args,
        capture_output=True,
        text=False,
        check=False,
    )


def assert_git_repo_and_head(repo_dir: Path) -> str:
    """Ensure directory is a git working tree and resolve current HEAD commit SHA."""
    if not repo_dir.exists() or not repo_dir.is_dir():
        raise ConfigError(f"Repository directory does not exist: {repo_dir}")

    res = run_git_cmd(repo_dir, ["rev-parse", "--is-inside-work-tree"])
    if res.returncode != 0 or res.stdout.strip() != b"true":
        raise ConfigError(f"Not a valid Git working tree: {repo_dir}")

    res = run_git_cmd(repo_dir, ["rev-parse", "--verify", "HEAD"])
    if res.returncode != 0:
        raise ConfigError(f"Cannot resolve Git HEAD in repository: {repo_dir}")

    return res.stdout.strip().decode("utf-8", errors="replace")


def compute_fingerprint(repo_dir: Path) -> str:
    """Compute deterministic SHA-256 fingerprint of tracked and non-ignored untracked files.

    Excludes .fleet/runs/ and .git/ directories.
    """
    res = run_git_cmd(repo_dir, ["ls-files", "-c", "-o", "--exclude-standard", "-z"])
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"git ls-files failed: {err}")

    raw_entries = [entry for entry in res.stdout.split(b"\0") if entry]
    clean_paths: list[str] = []

    for raw in raw_entries:
        p_str = raw.decode("utf-8", errors="replace").replace("\\", "/").strip("/")
        if not p_str:
            continue
        # Exclude .fleet/runs and .git even if not in .gitignore
        if p_str == ".fleet/runs" or p_str.startswith(".fleet/runs/"):
            continue
        if p_str == ".git" or p_str.startswith(".git/"):
            continue
        clean_paths.append(p_str)

    clean_paths = sorted(set(clean_paths))

    hasher = hashlib.sha256()
    for rel_path in clean_paths:
        file_path = repo_dir / rel_path
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(b"\0")

        if file_path.is_symlink():
            try:
                target = os.readlink(file_path)
                hasher.update(b"SYMLINK:")
                hasher.update(target.encode("utf-8", errors="replace"))
            except OSError:
                hasher.update(b"UNREADABLE_SYMLINK")
        elif file_path.is_file():
            try:
                file_hasher = hashlib.sha256()
                with open(file_path, "rb") as f:
                    while chunk := f.read(65536):
                        file_hasher.update(chunk)
                hasher.update(file_hasher.digest())
            except OSError:
                hasher.update(b"UNREADABLE_FILE")
        elif file_path.is_dir():
            hasher.update(b"DIR")
        elif not file_path.exists():
            hasher.update(b"MISSING")
        else:
            hasher.update(b"SPECIAL")
        hasher.update(b"\0")

    return hasher.hexdigest()


def collect_env_secrets(declared_secret_names: list[str]) -> list[str]:
    """Collect sensitive environment variable values to redact."""
    secrets = set()
    for k, v in os.environ.items():
        if SECRET_NAME_PATTERN.search(k):
            val = v.strip()
            if len(val) >= 4:
                secrets.add(val)
    for name in declared_secret_names:
        if name in os.environ:
            val = os.environ[name].strip()
            if len(val) >= 4:
                secrets.add(val)
    return sorted(secrets, key=len, reverse=True)


def redact_text(text: str, secrets: list[str]) -> str:
    """Redact known secret values and common token patterns from text."""
    if not text:
        return text
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = GITHUB_TOKEN_PATTERN.sub("[REDACTED]", text)
    text = GITHUB_PAT_PATTERN.sub("[REDACTED]", text)
    return text


def truncate_and_redact(data: str | bytes, secrets: list[str]) -> str:
    """Redact text and ensure output length does not exceed 64 KiB."""
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data or ""
    redacted = redact_text(text, secrets)
    encoded = redacted.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        redacted = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return redacted


def redact_argv(argv: list[str], secrets: list[str]) -> list[str]:
    """Redact secret strings in command arguments."""
    return [redact_text(arg, secrets) for arg in argv]


def build_subprocess_env(declared_secret_names: list[str]) -> dict[str, str]:
    """Build restricted subprocess environment with allowlisted system variables and declared secrets."""
    sub_env: dict[str, str] = {}
    upper_allowlist = {k.upper() for k in ALLOWLISTED_ENV_VARS}
    declared_upper = {name.upper(): name for name in declared_secret_names}

    for k, v in os.environ.items():
        k_upper = k.upper()
        if k_upper in upper_allowlist:
            sub_env[k] = v
        elif k_upper in declared_upper or k in declared_secret_names:
            sub_env[k] = v
    return sub_env


class WindowsJob:
    """Own a check process tree, including descendants of an exited parent.

    A waiting launcher is assigned before it can spawn the user's command.
    See https://learn.microsoft.com/windows/win32/procthread/job-objects
    """
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, proc):
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            proc.kill()
            proc.wait()
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        proc._fleet_job = self

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        try:
            self.terminate()
        finally:
            self.api.CloseHandle(self.handle)


def kill_process_tree(proc: subprocess.Popen) -> None:
    if getattr(proc, '_fleet_job', None) is not None:
        proc._fleet_job.terminate()
        return
    """Terminate the process and all descendant child processes cross-platform."""
    if sys.platform == "win32" and proc.poll() is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
    else:
        try:
            # A session leader can exit while descendants still hold stdout open.
            # The group ID remains its original PID, even after that leader exits.
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def check_schema_version(obj: dict[str, Any], file_description: str) -> None:
    """Validate that schema_version is integer 1."""
    if "schema_version" not in obj:
        raise ConfigError(f"{file_description} missing required 'schema_version' field.")
    val = obj["schema_version"]
    if type(val) is not int or val != 1:
        raise ConfigError(
            f"Unsupported schema_version {val!r} in {file_description}. "
            f"Only integer schema_version 1 is supported."
        )


def check_repo_path_containment(repo_dir: Path, rel_path: str, context: str) -> None:
    """Ensure relative path stays strictly within repository root after symlink resolution."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ConfigError(f"{context}: path must be a non-empty string.")
    p = Path(rel_path)
    if p.is_absolute():
        raise ConfigError(f"{context}: path must be relative to repository root, got absolute path {rel_path!r}")

    resolved_repo = repo_dir.resolve()
    target = (repo_dir / p).resolve()
    try:
        target.relative_to(resolved_repo)
    except ValueError:
        raise ConfigError(f"{context}: path {rel_path!r} escapes repository root {resolved_repo}")


def validate_project_data(data: dict[str, Any], repo_dir: Path, file_description: str) -> None:
    """Validate project descriptor structure and constraints."""
    if not isinstance(data, dict):
        raise ConfigError(f"{file_description}: must be a JSON object.")

    check_schema_version(data, file_description)

    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not ID_PATTERN.match(project_id):
        raise ConfigError(
            f"{file_description}: 'project_id' must match pattern [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}, got {project_id!r}"
        )

    context_files = data.get("context_files")
    if not isinstance(context_files, list):
        raise ConfigError(f"{file_description}: 'context_files' must be a list of paths.")
    for idx, cf in enumerate(context_files):
        if not isinstance(cf, str) or not cf.strip():
            raise ConfigError(f"{file_description}: context_files[{idx}] must be a non-empty string.")
        check_repo_path_containment(repo_dir, cf, f"{file_description} context_files[{idx}]")

    checks = data.get("checks")
    if not isinstance(checks, list):
        raise ConfigError(f"{file_description}: 'checks' must be a list.")

    seen_check_names = set()
    for idx, chk in enumerate(checks):
        if not isinstance(chk, dict):
            raise ConfigError(f"{file_description}: checks[{idx}] must be an object.")
        name = chk.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{file_description}: checks[{idx}] 'name' must be a non-empty string.")
        if name in seen_check_names:
            raise ConfigError(f"{file_description}: duplicate check name {name!r}.")
        seen_check_names.add(name)

        argv = chk.get("argv")
        if not isinstance(argv, list) or len(argv) == 0:
            raise ConfigError(f"{file_description}: check {name!r} 'argv' must be a non-empty list of strings.")
        for a_idx, arg in enumerate(argv):
            if not isinstance(arg, str) or not arg.strip():
                raise ConfigError(f"{file_description}: check {name!r} argv[{a_idx}] must be a non-empty string.")

        timeout = chk.get("timeout_seconds")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
            raise ConfigError(
                f"{file_description}: check {name!r} 'timeout_seconds' must be a positive finite number <= 3600, got {timeout!r}"
            )

    secret_names = data.get("secret_names")
    if not isinstance(secret_names, list):
        raise ConfigError(f"{file_description}: 'secret_names' must be a list of environment variable names.")
    for idx, s in enumerate(secret_names):
        if not isinstance(s, str) or not s.strip():
            raise ConfigError(f"{file_description}: secret_names[{idx}] must be a non-empty string.")


def validate_task_data(
    data: dict[str, Any],
    repo_dir: Path,
    expected_id: str | None,
    project_data: dict[str, Any],
    file_description: str,
) -> None:
    """Validate task specification structure, constraints, and scope containment."""
    if not isinstance(data, dict):
        raise ConfigError(f"{file_description}: must be a JSON object.")

    check_schema_version(data, file_description)

    task_id = data.get("id")
    if not isinstance(task_id, str) or not ID_PATTERN.match(task_id):
        raise ConfigError(
            f"{file_description}: 'id' must match pattern [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}, got {task_id!r}"
        )
    if expected_id is not None and task_id != expected_id:
        raise ConfigError(
            f"{file_description}: task 'id' {task_id!r} does not match expected filename ID {expected_id!r}"
        )

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ConfigError(f"{file_description}: 'title' must be a non-empty string.")

    outcome = data.get("outcome")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ConfigError(f"{file_description}: 'outcome' must be a non-empty string.")

    acceptance = data.get("acceptance")
    if not isinstance(acceptance, list) or len(acceptance) == 0:
        raise ConfigError(f"{file_description}: 'acceptance' must be a non-empty list of criteria strings.")
    for idx, crit in enumerate(acceptance):
        if not isinstance(crit, str) or not crit.strip():
            raise ConfigError(f"{file_description}: acceptance[{idx}] must be a non-empty string.")

    scope = data.get("scope")
    if not isinstance(scope, list) or len(scope) == 0:
        raise ConfigError(f"{file_description}: 'scope' must be a non-empty list of paths.")
    for idx, sc in enumerate(scope):
        if not isinstance(sc, str) or not sc.strip():
            raise ConfigError(f"{file_description}: scope[{idx}] must be a non-empty string.")
        check_repo_path_containment(repo_dir, sc, f"{file_description} scope[{idx}]")

    deps = data.get("dependencies")
    if not isinstance(deps, list):
        raise ConfigError(f"{file_description}: 'dependencies' must be a list of task IDs.")
    for idx, dep in enumerate(deps):
        if not isinstance(dep, str) or not ID_PATTERN.match(dep):
            raise ConfigError(f"{file_description}: dependency[{idx}] {dep!r} is not a valid task ID.")

    base_sha = data.get("base_sha")
    if not isinstance(base_sha, str) or not base_sha.strip():
        raise ConfigError(f"{file_description}: 'base_sha' must be a non-empty Git SHA string.")

    check_names = data.get("check_names")
    if not isinstance(check_names, list):
        raise ConfigError(f"{file_description}: 'check_names' must be a list of check names.")

    project_check_names = {c["name"] for c in project_data.get("checks", [])}
    for idx, cn in enumerate(check_names):
        if not isinstance(cn, str) or not cn.strip():
            raise ConfigError(f"{file_description}: check_names[{idx}] must be a non-empty string.")
        if cn not in project_check_names:
            raise ConfigError(
                f"{file_description}: check_names[{idx}] {cn!r} does not exist in project checks: {sorted(project_check_names)}"
            )

    max_attempts = data.get("max_attempts")
    if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 10:
        raise ConfigError(
            f"{file_description}: 'max_attempts' must be an integer between 1 and 10, got {max_attempts!r}"
        )

    provider = data.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ConfigError(f"{file_description}: 'provider' must be a non-empty string.")

    model = data.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"{file_description}: 'model' must be a string or null, got {model!r}")

    # Tasks must not contain secret values
    env_secrets = collect_env_secrets(project_data.get("secret_names", []))
    if env_secrets:
        task_serialized = json.dumps(data)
        for s in env_secrets:
            if s in task_serialized:
                raise ConfigError(f"{file_description}: task specification must not contain secret values.")


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new minimal project descriptor; refuse existing file."""
    repo_dir = Path(args.repo).resolve()
    if not repo_dir.exists() or not repo_dir.is_dir():
        raise ConfigError(f"Repository path does not exist or is not a directory: {repo_dir}")

    project_id = args.project_id
    if not isinstance(project_id, str) or not ID_PATTERN.match(project_id):
        raise ConfigError(f"Invalid project_id {project_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")

    fleet_dir = repo_dir / ".fleet"
    project_file = fleet_dir / "project.json"

    if project_file.exists():
        raise ConfigError(f"Project descriptor already exists at {project_file}. Refusing to overwrite.")

    fleet_dir.mkdir(parents=True, exist_ok=True)

    project_data = {
        "schema_version": 1,
        "project_id": project_id,
        "context_files": ["README.md"],
        "checks": [],
        "secret_names": [],
    }

    with open(project_file, "x", encoding="utf-8") as f:
        json.dump(project_data, f, indent=2)
        f.write("\n")

    print(f"Initialized project descriptor at {project_file}")
    return 0


def cmd_new_task(args: argparse.Namespace) -> int:
    """Create a new task specification using current Git HEAD; refuse existing file."""
    repo_dir = Path(args.repo).resolve()
    head_sha = assert_git_repo_and_head(repo_dir)

    task_id = args.id
    if not isinstance(task_id, str) or not ID_PATTERN.match(task_id):
        raise ConfigError(f"Invalid task id {task_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")

    if not args.title or not args.title.strip():
        raise ConfigError("Task title must not be empty.")
    if not args.outcome or not args.outcome.strip():
        raise ConfigError("Task outcome must not be empty.")
    if not args.criterion or len(args.criterion) == 0:
        raise ConfigError("At least one criterion is required (--criterion).")
    for c in args.criterion:
        if not c or not c.strip():
            raise ConfigError("Criterion cannot be empty.")

    project_file = repo_dir / ".fleet" / "project.json"
    if not project_file.exists():
        raise ConfigError(f"Project descriptor not found at {project_file}. Run init first.")

    try:
        with open(project_file, "r", encoding="utf-8") as f:
            project_data = json.load(f)
    except Exception as exc:
        raise ConfigError(f"Failed to read project descriptor {project_file}: {exc}")

    validate_project_data(project_data, repo_dir, str(project_file))

    tasks_dir = repo_dir / ".fleet" / "tasks"
    task_file = tasks_dir / f"{task_id}.json"

    if task_file.exists():
        raise ConfigError(f"Task file already exists at {task_file}. Refusing to overwrite.")

    tasks_dir.mkdir(parents=True, exist_ok=True)

    check_names = [c["name"] for c in project_data.get("checks", [])]

    task_data = {
        "schema_version": 1,
        "id": task_id,
        "title": args.title.strip(),
        "outcome": args.outcome.strip(),
        "acceptance": [c.strip() for c in args.criterion],
        "scope": ["."],
        "dependencies": [],
        "base_sha": head_sha,
        "check_names": check_names,
        "max_attempts": 2,
        "provider": "manual",
        "model": None,
    }

    validate_task_data(task_data, repo_dir, task_id, project_data, str(task_file))

    with open(task_file, "x", encoding="utf-8") as f:
        json.dump(task_data, f, indent=2)
        f.write("\n")

    print(f"Created task descriptor at {task_file}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate project descriptor and all task specifications."""
    repo_dir = Path(args.repo).resolve()
    project_file = repo_dir / ".fleet" / "project.json"
    if not project_file.exists():
        raise ConfigError(f"Project descriptor not found at {project_file}")

    try:
        with open(project_file, "r", encoding="utf-8") as f:
            project_data = json.load(f)
    except Exception as exc:
        raise ConfigError(f"Failed to parse {project_file}: {exc}")

    validate_project_data(project_data, repo_dir, str(project_file))

    tasks_dir = repo_dir / ".fleet" / "tasks"
    all_tasks: dict[str, dict[str, Any]] = {}
    if tasks_dir.exists() and tasks_dir.is_dir():
        for tf in sorted(tasks_dir.glob("*.json")):
            file_id = tf.stem
            try:
                with open(tf, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
            except Exception as exc:
                raise ConfigError(f"Failed to parse task file {tf}: {exc}")
            validate_task_data(tdata, repo_dir, file_id, project_data, str(tf))
            all_tasks[file_id] = tdata

    # Check dependencies exist
    for tid, tdata in all_tasks.items():
        for dep in tdata.get("dependencies", []):
            if dep not in all_tasks:
                raise ConfigError(
                    f"Task {tid!r} depends on {dep!r}, but task file for {dep!r} does not exist in {tasks_dir}"
                )

    print("Project and task specifications are valid.")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    """Emit an English handoff prompt for agent mode without side effects."""
    repo_dir = Path(args.repo).resolve()
    project_file = repo_dir / ".fleet" / "project.json"
    if not project_file.exists():
        raise ConfigError(f"Project descriptor not found at {project_file}")
    with open(project_file, "r", encoding="utf-8") as f:
        project_data = json.load(f)
    validate_project_data(project_data, repo_dir, str(project_file))

    task_id = args.task
    if not ID_PATTERN.match(task_id):
        raise ConfigError(f"Invalid task id {task_id!r}")
    task_file = repo_dir / ".fleet" / "tasks" / f"{task_id}.json"
    if not task_file.exists():
        raise ConfigError(f"Task descriptor not found at {task_file}")
    with open(task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    validate_task_data(task_data, repo_dir, task_id, project_data, str(task_file))

    mode = args.mode.lower()
    if mode not in MODE_BOUNDARIES:
        raise ConfigError(f"Invalid mode {mode!r}. Must be one of: {list(MODE_BOUNDARIES.keys())}")

    criteria_lines = "\n".join(f"- {c}" for c in task_data.get("acceptance", []))
    scope_lines = "\n".join(f"- {s}" for s in task_data.get("scope", []))
    context_lines = "\n".join(f"- {cf}" for cf in project_data.get("context_files", []))
    def helper_command(command):
        argv = [sys.executable, str(Path(__file__).resolve()), command,
                "--repo", str(repo_dir), "--task", task_id]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    prompt_text = f"""# Task Handoff: {task_id} — {task_data.get('title')}

Mode: {mode.upper()}
Base Revision (HEAD SHA): {task_data.get('base_sha')}

## Outcome
{task_data.get('outcome')}

## Acceptance Criteria
{criteria_lines}

## Declared Scope
{scope_lines}

## Applicable Context Paths
{context_lines}

## Mode-Specific Boundaries ({mode.upper()})
{MODE_BOUNDARIES[mode]}

## Verification and Report Commands
Run task checks:
  {helper_command('check')}
View latest report:
  {helper_command('report')}
Inspect status:
  {helper_command('status')}
"""
    print(prompt_text)
    return 0


def get_task_status(
    repo_dir: Path,
    task_id: str,
    task_data: dict[str, Any],
    current_head: str,
    current_fingerprint: str,
) -> dict[str, Any]:
    """Inspect latest run evidence and truthful verification status for a task."""
    title = task_data.get("title", "")
    runs_dir = repo_dir / ".fleet" / "runs" / task_id
    if not runs_dir.exists() or not runs_dir.is_dir():
        return {
            "task_id": task_id,
            "title": title,
            "latest_run_path": None,
            "status": "unverified",
            "check_status": None,
            "is_current": False,
            "reason": "No evidence runs recorded.",
        }

    run_files = sorted(runs_dir.glob("*.json"))
    if not run_files:
        return {
            "task_id": task_id,
            "title": title,
            "latest_run_path": None,
            "status": "unverified",
            "check_status": None,
            "is_current": False,
            "reason": "No evidence runs recorded.",
        }

    latest_file = None
    latest_time = None
    latest_data = None
    for rf in run_files:
        try:
            with open(rf, "r", encoding="utf-8") as f:
                data = json.load(f)
            t = data.get("end_time") or data.get("start_time") or ""
            mtime = rf.stat().st_mtime
            sort_key = (t, mtime)
            if latest_time is None or sort_key > latest_time:
                latest_time = sort_key
                latest_file = rf
                latest_data = data
        except Exception:
            continue

    if latest_data is None or latest_file is None:
        return {
            "task_id": task_id,
            "title": title,
            "latest_run_path": None,
            "status": "unverified",
            "check_status": None,
            "is_current": False,
            "reason": "No valid evidence runs found.",
        }

    run_head = latest_data.get("head_sha")
    run_fp = latest_data.get("content_fingerprint")
    run_status = latest_data.get("status")

    is_current = (run_head == current_head) and (run_fp == current_fingerprint)
    checks = latest_data.get("checks", [])
    valid_pass = (
        latest_data.get("schema_version") == 1
        and latest_data.get("task_id") == task_id
        and isinstance(checks, list) and bool(checks)
        and {c.get("name") for c in checks if isinstance(c, dict)} == set(task_data["check_names"])
        and all(isinstance(c, dict) and c.get("status") == "passed" and c.get("exit_code") == 0 for c in checks)
    )

    # Truthful reporting: verified only on passing current evidence; never owner-accepted or deployed
    if run_status == "passed" and valid_pass and is_current:
        verification_status = "verified"
    elif not is_current:
        verification_status = "stale"
    else:
        verification_status = "unverified"

    try:
        rel_path = str(latest_file.relative_to(repo_dir)).replace("\\", "/")
    except ValueError:
        rel_path = str(latest_file)

    return {
        "task_id": task_id,
        "title": title,
        "latest_run_path": rel_path,
        "status": verification_status,
        "check_status": run_status,
        "is_current": is_current,
        "reason": "Evidence no longer matches the current revision/content." if not is_current else latest_data.get("reason"),
    }


def cmd_status(args: argparse.Namespace) -> int:
    """Output JSON status showing task ID/title, latest run path, and verification."""
    repo_dir = Path(args.repo).resolve()
    current_head = assert_git_repo_and_head(repo_dir)
    current_fingerprint = compute_fingerprint(repo_dir)

    project_file = repo_dir / ".fleet" / "project.json"
    if not project_file.exists():
        raise ConfigError(f"Project descriptor not found at {project_file}")
    with open(project_file, "r", encoding="utf-8") as f:
        project_data = json.load(f)
    validate_project_data(project_data, repo_dir, str(project_file))

    tasks_dir = repo_dir / ".fleet" / "tasks"

    if args.task:
        task_id = args.task
        if not ID_PATTERN.match(task_id):
            raise ConfigError(f"Invalid task id {task_id!r}")
        task_file = tasks_dir / f"{task_id}.json"
        if not task_file.exists():
            raise ConfigError(f"Task descriptor not found at {task_file}")
        with open(task_file, "r", encoding="utf-8") as f:
            task_data = json.load(f)
        validate_task_data(task_data, repo_dir, task_id, project_data, str(task_file))
        status_info = get_task_status(repo_dir, task_id, task_data, current_head, current_fingerprint)
        print(json.dumps(status_info, indent=2))
        return 0
    else:
        all_statuses = []
        if tasks_dir.exists() and tasks_dir.is_dir():
            for tf in sorted(tasks_dir.glob("*.json")):
                task_id = tf.stem
                try:
                    with open(tf, "r", encoding="utf-8") as f:
                        task_data = json.load(f)
                    validate_task_data(task_data, repo_dir, task_id, project_data, str(tf))
                    st = get_task_status(repo_dir, task_id, task_data, current_head, current_fingerprint)
                    all_statuses.append(st)
                except (ValueError, OSError, ConfigError) as exc:
                    raise ConfigError(f"Cannot read task {task_id}: {exc}") from exc
        print(json.dumps(all_statuses, indent=2))
        return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Print the latest redacted JSON run result; exit 2 if none."""
    repo_dir = Path(args.repo).resolve()
    task_id = args.task
    if not ID_PATTERN.match(task_id):
        raise ConfigError(f"Invalid task id {task_id!r}")

    runs_dir = repo_dir / ".fleet" / "runs" / task_id
    if not runs_dir.exists() or not runs_dir.is_dir():
        print(f"No run evidence found for task {task_id} at {runs_dir}", file=sys.stderr)
        return 2

    run_files = sorted(runs_dir.glob("*.json"))
    if not run_files:
        print(f"No run evidence files found for task {task_id} in {runs_dir}", file=sys.stderr)
        return 2

    latest_file = None
    latest_time = None
    for rf in run_files:
        try:
            with open(rf, "r", encoding="utf-8") as f:
                data = json.load(f)
            t = data.get("end_time") or data.get("start_time") or ""
            mtime = rf.stat().st_mtime
            sort_key = (t, mtime)
            if latest_time is None or sort_key > latest_time:
                latest_time = sort_key
                latest_file = rf
        except Exception:
            continue

    if latest_file is None:
        print(f"No valid run evidence found for task {task_id}", file=sys.stderr)
        return 2

    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(json.dumps(data, indent=2))
        return 0
    except Exception as exc:
        print(f"Failed to read report {latest_file}: {exc}", file=sys.stderr)
        return 2


def cmd_check(args: argparse.Namespace) -> int:
    """Execute task checks in project order and record unique JSON evidence."""
    repo_dir = Path(args.repo).resolve()
    head_sha_before = assert_git_repo_and_head(repo_dir)

    project_file = repo_dir / ".fleet" / "project.json"
    if not project_file.exists():
        raise ConfigError(f"Project descriptor not found at {project_file}")
    with open(project_file, "r", encoding="utf-8") as f:
        project_data = json.load(f)
    validate_project_data(project_data, repo_dir, str(project_file))

    task_id = args.task
    if not ID_PATTERN.match(task_id):
        raise ConfigError(f"Invalid task id {task_id!r}")
    task_file = repo_dir / ".fleet" / "tasks" / f"{task_id}.json"
    if not task_file.exists():
        raise ConfigError(f"Task descriptor not found at {task_file}")
    with open(task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    validate_task_data(task_data, repo_dir, task_id, project_data, str(task_file))

    fingerprint_before = compute_fingerprint(repo_dir)

    # Dependency check: all dependencies must have passing, current evidence (verified dependencies)
    dependencies = task_data.get("dependencies", [])
    for dep_id in dependencies:
        dep_task_file = repo_dir / ".fleet" / "tasks" / f"{dep_id}.json"
        if not dep_task_file.exists():
            raise ConfigError(f"Dependency task specification {dep_id!r} not found at {dep_task_file}.")
        with open(dep_task_file, "r", encoding="utf-8") as f:
            dep_task_data = json.load(f)
        dep_status = get_task_status(repo_dir, dep_id, dep_task_data, head_sha_before, fingerprint_before)
        if dep_status["status"] != "verified":
            raise ConfigError(
                f"Refusing execution: dependency {dep_id!r} is unverified (status: {dep_status['status']!r}, "
                f"check_status: {dep_status['check_status']!r}, is_current: {dep_status['is_current']!r}). "
                f"Check execution requires passing, current automated evidence for all dependencies (verified dependencies). "
                f"Note: Verified dependencies reflect automated check evidence under current revision and content fingerprint, "
                f"not manual product acceptance or deployment approval."
            )

    task_check_names = task_data.get("check_names", [])
    if not task_check_names:
        raise ConfigError(
            f"Zero checks configured for task {task_id!r}. At least one check is required to execute checks."
        )

    project_checks = project_data.get("checks", [])
    checks_to_run = [c for c in project_checks if c["name"] in task_check_names]
    if not checks_to_run:
        raise ConfigError(
            f"No matching project checks found for task {task_id!r} checks: {task_check_names}"
        )

    declared_secrets = project_data.get("secret_names", [])
    secrets = collect_env_secrets(declared_secrets)
    sub_env = build_subprocess_env(declared_secrets)

    run_start_dt = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    runs_dir = repo_dir / ".fleet" / "runs" / task_id
    check_repo_path_containment(repo_dir, str(runs_dir.relative_to(repo_dir)), "Evidence directory")
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_file = runs_dir / f"{run_id}.json"
    # Reserve evidence before executing any child-side effects.
    run_handle = open(run_file, "x", encoding="utf-8")
    if os.name != "nt":
        os.chmod(run_file, 0o600)
    json.dump({"schema_version": 1, "task_id": task_id, "run_id": run_id,
               "status": "running", "start_time": run_start_dt.isoformat()}, run_handle)
    run_handle.flush()

    recorded_checks: list[dict[str, Any]] = []
    overall_status = "passed"
    overall_reason = None
    interrupted = False

    for check_def in checks_to_run:
        chk_name = check_def["name"]
        raw_argv = check_def["argv"]
        # Substitute {python} only when it is a whole argv element
        resolved_argv = [sys.executable if arg == "{python}" else arg for arg in raw_argv]
        timeout_seconds = float(check_def.get("timeout_seconds", 60))

        is_win = (sys.platform == "win32")
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(repo_dir),
            "env": sub_env,
            "shell": False,
        }
        if is_win:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        chk_start = datetime.now(timezone.utc)
        chk_status = "passed"
        chk_reason = None
        chk_exit = None
        stdout_str = ""
        stderr_str = ""
        job = None

        try:
            launch_argv = resolved_argv
            if is_win:
                executable = Path(resolved_argv[0])
                candidate = executable if executable.is_absolute() else repo_dir / executable
                if not candidate.is_file() and not shutil.which(resolved_argv[0], path=sub_env.get('PATH')):
                    raise FileNotFoundError(f"Executable unavailable: {resolved_argv[0]}")
                job = WindowsJob()
                kwargs['stdin'] = subprocess.PIPE
                launcher = 'import sys,subprocess; sys.stdin.buffer.read(1); sys.exit(subprocess.call(sys.argv[1:],stdin=subprocess.DEVNULL))'
                launch_argv = [sys.executable, '-c', launcher, *resolved_argv]
            proc = subprocess.Popen(launch_argv, **kwargs)
            if job is not None:
                job.attach(proc)
            try:
                stdout_bytes, stderr_bytes = proc.communicate(input=b'1' if is_win else None, timeout=timeout_seconds)
                chk_exit = proc.returncode
                stdout_str = truncate_and_redact(stdout_bytes, secrets)
                stderr_str = truncate_and_redact(stderr_bytes, secrets)
                if chk_exit != 0:
                    chk_status = "failed"
                    chk_reason = f"Process exited with code {chk_exit}"
                else:
                    chk_status = "passed"
            except subprocess.TimeoutExpired as exc:
                kill_process_tree(proc)
                chk_status = "timed_out"
                chk_reason = f"Process timed out after {timeout_seconds} seconds"
                try:
                    out_rem, err_rem = proc.communicate(timeout=3)
                    partial_out = out_rem if out_rem is not None else (exc.stdout or b"")
                    partial_err = err_rem if err_rem is not None else (exc.stderr or b"")
                except Exception:
                    partial_out = exc.stdout or b""
                    partial_err = exc.stderr or b""
                stdout_str = truncate_and_redact(partial_out, secrets)
                stderr_str = truncate_and_redact(partial_err, secrets)
                chk_exit = proc.returncode
            except KeyboardInterrupt:
                kill_process_tree(proc)
                chk_status = "interrupted"
                chk_reason = "Execution interrupted by user"
                try:
                    out_rem, err_rem = proc.communicate(timeout=3)
                    stdout_str = truncate_and_redact(out_rem or b"", secrets)
                    stderr_str = truncate_and_redact(err_rem or b"", secrets)
                except Exception:
                    stdout_str = ""
                    stderr_str = ""
                chk_exit = proc.returncode
                interrupted = True
        except FileNotFoundError as exc:
            chk_status = "unavailable"
            chk_reason = f"Missing executable: {exc}"
            stderr_str = truncate_and_redact(str(exc), secrets)
        except Exception as exc:
            chk_status = "unavailable"
            chk_reason = f"Execution failed: {exc}"
            stderr_str = truncate_and_redact(str(exc), secrets)
        finally:
            if job is not None:
                job.close()

        chk_end = datetime.now(timezone.utc)
        chk_duration = round((chk_end - chk_start).total_seconds(), 4)

        recorded_checks.append({
            "name": chk_name,
            "argv": redact_argv(resolved_argv, secrets),
            "exit_code": chk_exit,
            "status": chk_status,
            "duration_seconds": chk_duration,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "reason": redact_text(chk_reason, secrets) if chk_reason else None,
        })

        if chk_status == "unavailable":
            overall_status = "unavailable"
            overall_reason = chk_reason
        elif chk_status == "interrupted":
            overall_status = "interrupted"
            overall_reason = chk_reason
            break
        elif chk_status == "timed_out" and overall_status not in ("unavailable", "interrupted"):
            overall_status = "timed_out"
            overall_reason = chk_reason
        elif chk_status == "failed" and overall_status not in ("unavailable", "interrupted", "timed_out"):
            overall_status = "failed"
            overall_reason = chk_reason

        if interrupted:
            break

    run_end_dt = datetime.now(timezone.utc)
    total_duration = round((run_end_dt - run_start_dt).total_seconds(), 4)

    head_sha_after = assert_git_repo_and_head(repo_dir)
    fingerprint_after = compute_fingerprint(repo_dir)

    # Check for stale evidence: working tree content or HEAD revision changed
    if overall_status == "passed":
        if head_sha_before != head_sha_after or fingerprint_before != fingerprint_after:
            overall_status = "stale"
            overall_reason = "Working-tree content or HEAD revision changed during check execution."

    evidence_data = {
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "machine": platform.node(),
        "platform": platform.platform(),
        "start_time": run_start_dt.isoformat(),
        "end_time": run_end_dt.isoformat(),
        "duration_seconds": total_duration,
        "base_sha": task_data.get("base_sha", head_sha_before),
        "head_sha": head_sha_after,
        "content_fingerprint": fingerprint_after,
        "status": overall_status,
        "reason": redact_text(overall_reason, secrets) if overall_reason else None,
        "checks": recorded_checks,
    }

    with run_handle:
        run_handle.seek(0)
        run_handle.truncate()
        json.dump(evidence_data, run_handle, indent=2)
        run_handle.write("\n")

    print(f"Check run completed: status={overall_status}. Evidence written to {run_file}")

    if overall_status == "passed":
        return 0
    elif overall_status in ("failed", "timed_out", "interrupted", "stale"):
        return 1
    elif overall_status == "unavailable":
        return 2
    else:
        return 1


VERSION = "0.1.0"
PROVIDER_CLIS = ["claude", "codex", "gemini", "agy", "aider", "opencode"]


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report environment readiness. Local only: no network, no secrets read."""
    ok = True
    py_ok = sys.version_info >= (3, 12)
    print(f"[{'ok' if py_ok else 'FAIL'}] python {platform.python_version()} (need 3.12+)")
    ok &= py_ok
    git = shutil.which("git")
    print(f"[{'ok' if git else 'FAIL'}] git: {git or 'not found on PATH'}")
    ok &= bool(git)
    print("Provider CLIs (optional, any one is enough):")
    for name in PROVIDER_CLIS:
        path = shutil.which(name)
        print(f"  [{'found' if path else 'missing'}] {name}" + (f" -> {path}" if path else ""))
    print("Fleet works in manual mode (provider \"manual\") without any provider CLI.")
    return 0 if ok else 1


def main() -> None:
    """CLI entry point for fleet helper."""
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Fleet: plan, implement, verify and report on Git tasks with AI workers.",
        epilog="First run: fleet.py doctor, then fleet.py init --project-id demo, then fleet.py new-task ... and fleet.py check --task ID. Docs: docs/",
    )
    parser.add_argument("--version", action="version", version="fleet " + VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    subparsers.add_parser("doctor", help="Check local environment (python, git, installed provider CLIs); no network")

    # init
    p_init = subparsers.add_parser("init", help="Initialize project descriptor")
    p_init.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_init.add_argument("--project-id", required=True, help="Project identifier")

    # new-task
    p_new = subparsers.add_parser("new-task", help="Create new task specification")
    p_new.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_new.add_argument("--id", required=True, help="Task identifier")
    p_new.add_argument("--title", required=True, help="Task title")
    p_new.add_argument("--outcome", required=True, help="Observable outcome")
    p_new.add_argument(
        "--criterion",
        action="append",
        required=True,
        help="Acceptance criterion (repeatable)",
    )

    # validate
    p_val = subparsers.add_parser("validate", help="Validate project and task specifications")
    p_val.add_argument("--repo", default=".", help="Repository root path (default: current directory)")

    # prompt
    p_prm = subparsers.add_parser("prompt", help="Emit handoff prompt for agent mode")
    p_prm.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_prm.add_argument("--task", required=True, help="Task identifier")
    p_prm.add_argument(
        "--mode",
        required=True,
        choices=["plan", "implement", "review", "release"],
        help="Agent mode",
    )

    # check
    p_chk = subparsers.add_parser("check", help="Execute task checks and record evidence")
    p_chk.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_chk.add_argument("--task", required=True, help="Task identifier")

    # status
    p_st = subparsers.add_parser("status", help="Show task status and verification")
    p_st.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_st.add_argument("--task", required=False, default=None, help="Optional task identifier")

    # report
    p_rep = subparsers.add_parser("report", help="Print latest redacted run evidence JSON")
    p_rep.add_argument("--repo", default=".", help="Repository root path (default: current directory)")
    p_rep.add_argument("--task", required=True, help="Task identifier")

    p_wait = subparsers.add_parser("wait", help="Wait for sessions, runs, projects, pids, files or URLs to finish")
    p_wait.add_argument("targets", nargs="*", help="orchestrators|sessions|runs|session:ID|workflow:RUN|project:PATH|pid:N|file:PATH|url:URL")
    p_wait.add_argument("--repo", default=".", help="Repository root for workflow/runs targets")
    grp = p_wait.add_mutually_exclusive_group()
    grp.add_argument("--any", action="store_true", help="Return when any target is done")
    grp.add_argument("--all", action="store_true", help="Return when all targets are done (default)")
    p_wait.add_argument("--timeout", type=float, default=3600, help="Seconds before giving up (default 3600; 0 = no limit)")
    p_wait.add_argument("--interval", type=float, default=5, help="Poll interval seconds (default 5)")
    p_wait.add_argument("--stable", type=int, default=1, help="Consecutive idle polls required (default 1)")
    p_wait.add_argument("--json", action="store_true", help="Machine-readable output")
    p_wait.add_argument("--quiet", action="store_true", help="Only the summary line")
    p_wait.add_argument("--fail-on-error", action="store_true", help="Treat unknown/unreachable targets as failures")
    p_wait.add_argument("--list", action="store_true", help="List what is active now and exit")

    try:
        args = parser.parse_args()
        if args.command == "wait":
            import fleet_wait
            try:
                env = fleet_wait.Env(repo=args.repo)
                if args.list:
                    sys.exit(fleet_wait.run_list(env, args.json))
                sys.exit(fleet_wait.run_wait(args.targets, env, "any" if args.any else "all", args.timeout,
                                             args.interval, args.stable, args.json, args.quiet, args.fail_on_error))
            except fleet_wait.UsageError as err:
                print(f"fleet wait: {err}", file=sys.stderr)
                sys.exit(3)
        if args.command == "doctor":
            sys.exit(cmd_doctor(args))
        repo_root = Path(args.repo).resolve()
        control_paths = [".fleet", ".fleet/project.json", ".fleet/tasks", ".fleet/runs"]
        control_paths += [str(p.relative_to(repo_root)) for p in (repo_root / '.fleet/tasks').glob('*.json')]
        for relative in control_paths:
            check_repo_path_containment(repo_root, relative, "Workflow control path")
        if args.command == "init":
            code = cmd_init(args)
        elif args.command == "new-task":
            code = cmd_new_task(args)
        elif args.command == "validate":
            code = cmd_validate(args)
        elif args.command == "prompt":
            code = cmd_prompt(args)
        elif args.command == "check":
            code = cmd_check(args)
        elif args.command == "status":
            code = cmd_status(args)
        elif args.command == "report":
            code = cmd_report(args)
        else:
            parser.print_help(sys.stderr)
            code = 2
        sys.exit(code)
    except ConfigError as err:
        print(f"Configuration/Validation error: {err}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
