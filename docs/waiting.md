# Waiting for work to finish

`python scripts/fleet.py wait` blocks until the things you name are no longer active. It is local only, uses the Python standard library, and reads metadata only: session transcripts, prompts and histories are never opened.

## Targets

| Target | Meaning |
| --- | --- |
| `orchestrators`, `sessions` | Local AI sessions found under `~/.claude`, `~/.codex`, `~/.gemini` (override with `FLEET_CLAUDE_DIR`, `FLEET_CODEX_DIR`, `FLEET_GEMINI_DIR`). Sessions running when the wait starts are tracked until idle. |
| `session:<id>` | One specific session, with ids shown as `<provider>:<id>`. |
| `runs` | Fleet check runs under `.fleet/runs` of `--repo` that are running. |
| `workflow:<run-id>` | One run, or the latest run of a task id. An incomplete run file counts as running. |
| `project:<path>` | Another project: running if it has a running Fleet run, a lock file, or very recent git activity. |
| `pid:<n>` | A process; done when it exits. |
| `file:<path>` | Done when the file appears (if missing) or changes (if present). |
| `url:<endpoint>` | A localhost http(s) endpoint returning JSON with a `status` field. Redirects are refused. Another host needs `FLEET_WAIT_ALLOW_URL=<host>`. |

How sessions are detected: session files are only `stat()`-ed, and a session is `running` if it was modified within `FLEET_ACTIVE_SECONDS` (default 120). Optional small JSON sidecar files (`~/.claude/sessions/*.json`) may supply `sessionId`, `pid`, `startedAt`, `status`, `model`, `usage` counters, `parentSessionId` and `progress`; only those whitelisted fields are used. Anything not reported stays `null`.

## Options

`--any` / `--all` (default all), `--timeout SECONDS` (default 3600, 0 = no limit), `--interval SECONDS` (default 5), `--stable N` (consecutive idle polls required, default 1), `--json`, `--quiet`, `--fail-on-error` (treat unknown or unreachable targets as failures), `--repo PATH`, and `--list` to show what is active without waiting.

Exit codes: `0` done, `1` timeout, `2` a target failed, `3` usage error.

## Examples

```
python scripts/fleet.py wait --list
python scripts/fleet.py wait sessions --stable 3 --interval 15
python scripts/fleet.py wait session:claude:3f2a --timeout 1800
python scripts/fleet.py wait sessions workflow:DEMO-001 --all --json
python scripts/fleet.py wait project:../other-repo --timeout 600
python scripts/fleet.py wait pid:4242 file:build/out.zip --any
```

All free text (progress notes) is redacted with the same rules as check evidence and limited to 300 characters.
