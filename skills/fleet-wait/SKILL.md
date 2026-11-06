---
name: fleet-wait
description: Wait for a sub-agent, orchestrator session, Fleet run, other project, process, file or local endpoint to finish before continuing.
---

Use this skill when your next step depends on work that is still running elsewhere: a Claude Code, Codex or Antigravity/Gemini session, a Fleet check run, another project directory, a process, a file, or a local status endpoint.
`<PLUGIN_ROOT>` is the plugin directory (two levels above this skill); see [workflow](../../references/workflow.md).
Do not busy-poll with ad-hoc sleeps; use the helper, which polls metadata only and never reads transcripts or prompts.

1. See what is active: `python <PLUGIN_ROOT>/scripts/fleet.py wait --list` (add `--json` for machine output).
2. Wait for it: `python <PLUGIN_ROOT>/scripts/fleet.py wait <targets> [--any|--all] [--timeout S] [--interval S] [--stable N] [--json] [--quiet] [--fail-on-error]`.
   Targets: `orchestrators` or `sessions` (local AI sessions), `session:<id>`, `runs`, `workflow:<run-id or task-id>`, `project:<path>`, `pid:<n>`, `file:<path>` (waits for it to appear or change), `url:<http://localhost...>` (JSON with a `status` field; localhost only, no redirects).
3. Choose settings. Interval 5 to 30 seconds is plenty; use `--stable 2` or `3` for sessions that pause between tool calls so a brief idle is not mistaken for completion. Always set a `--timeout` that fits the job (default 3600 seconds; `0` means no limit).
4. Read the exit code: `0` all done, `1` timeout, `2` a target failed (or was unreachable with `--fail-on-error`), `3` usage error.
   On `1`, report that the work is still running and decide whether to wait again; do not assume success.
   On `2`, inspect the failing target in the output and fix or escalate; never continue as if it passed.
5. A finished wait only means the work stopped. Verify results with `fleet-status` or `fleet.py check` before claiming a task is done.

Output lists, per target: kind, id, status (running, idle, done, failed, unknown), start time, running seconds, last seen, model and provider, token usage (null when the source does not report it), parent and children, and a redacted progress note. Keep output short in your report; do not paste secrets.
