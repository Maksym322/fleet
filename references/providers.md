# Providers and delegation

A task's `provider` field names who does the work: `manual` (a human or the
current agent session) or the name of an AI CLI you have installed (for example
`claude`, `codex`, `gemini`). Fleet does not call providers itself; the skills
hand a prompt (`fleet.py prompt`) to the provider you choose. Run
`python <PLUGIN_ROOT>/scripts/fleet.py doctor` to see which CLIs are on your PATH.

Guidelines:
- Use a small/cheap model for scans and docs, a stronger one for auth, concurrency and migrations.
- Use a separate session (ideally a different provider) for review, read-only at first.
- One writer per worktree. Give each delegate explicit file ownership, the task ID, base SHA and criteria.
- Stop and replan after two failed implementation attempts.
- An exit code 0 with denied tools or an empty response is not task completion; inspect the artifacts.
- Do not enable global "skip permissions" modes silently; prefer scoped tool permissions.
- Verify authentication and quota separately from executable discovery. If a CLI is not on PATH,
  check its usual install directory (for example `~/.local/bin`) before concluding it is missing.
- Record the actual model used and attempts. Unknown cost is unknown, never zero.
- Set your own spending limits in your provider accounts; Fleet enforces none.
