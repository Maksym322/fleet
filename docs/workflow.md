# Workflow

1. `doctor` - verify python 3.12+, git, optional provider CLIs.
2. `init --project-id ID` - create `.fleet/project.json`; edit in your real checks.
3. `new-task ...` - create a task bound to current HEAD.
4. `validate` - validate project and tasks.
5. `prompt --task ID --mode plan|implement|review|release` - emit a handoff prompt for an agent.
6. `check --task ID` - run declared checks, write redacted evidence to `.fleet/runs/` (git-ignored).
7. `status [--task ID]`, `report --task ID` - show progress and the latest evidence JSON.

`check` exits 0 only if all checks pass, 1 on failure/timeout/stale changes, 2 on invalid configuration.
Evidence is tied to the exact commit (or a content fingerprint if the tree is dirty); new commits invalidate it.
Use one branch/worktree and one writer per task; review the final head in a separate session.
The runner uses an allowlisted environment and redacts secrets, but it is not a security sandbox.
