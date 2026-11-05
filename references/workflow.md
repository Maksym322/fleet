# Task workflow

Resolve PLUGIN_ROOT as two directories above the loaded skill directory:
`<PLUGIN_ROOT>/skills/<name>/SKILL.md`. The bundled helper is
`<PLUGIN_ROOT>/scripts/fleet.py`. Use Python 3.12+ (`python3` on Ubuntu,
`python` on Windows). Always pass the product path with `--repo`.

```text
python <PLUGIN_ROOT>/scripts/fleet.py init --repo <REPO> --project-id <ID>
python <PLUGIN_ROOT>/scripts/fleet.py validate --repo <REPO>
python <PLUGIN_ROOT>/scripts/fleet.py prompt --repo <REPO> --task <TASK-ID> --mode implement
python <PLUGIN_ROOT>/scripts/fleet.py check --repo <REPO> --task <TASK-ID>
python <PLUGIN_ROOT>/scripts/fleet.py status --repo <REPO> --task <TASK-ID>
python <PLUGIN_ROOT>/scripts/fleet.py report --repo <REPO> --task <TASK-ID>
```

Project configuration and task specifications are versioned source. Configure real check argv
and read them before execution. `{python}` as a complete argv element uses the current interpreter.
Add `.fleet/runs/` to the project's ignore rules; reports are local runtime artifacts.
Never use production data as a test fixture. Regular checks must run without paid APIs.

Use one branch/worktree and one writer per task. Check dependency evidence first.
Commit code/task changes when authorized, then run final checks; commits and file changes
invalidate older evidence. A dirty run is labeled with a content fingerprint and is not
evidence for a later commit. Review the actual final head in a separate session.

`check` returns 0 only when declared checks pass, 1 for failures/timeouts/stale changes,
and 2 for invalid/unavailable configuration or no checks. Read the JSON status too.
The command runner uses an allowlisted environment and redacts declared/env secret patterns;
it is not a security sandbox or exhaustive personal-data filter.

Final handoff: outcome; changed files; head/PR; report path; actual checks; limitations;
user actions with input, expected behavior and cleanup. This plugin provides a local
development workflow. Remote queueing, autonomous merge/deployment and a web panel are later work.
