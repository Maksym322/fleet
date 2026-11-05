# Task format

Files live in your repository under `.fleet/` and are versioned source.

`.fleet/project.json`: `schema_version` (1), `project_id`, `context_files` (relative paths),
`checks` (unique `name`, `argv` list, `timeout_seconds`), `secret_names` (env var NAMES only, never values).
`{python}` as a whole argv element means the current interpreter.

`.fleet/tasks/<ID>.json`: `schema_version`, `id`, `title`, `outcome`, `acceptance` (non-empty strings),
`scope` (relative paths), `dependencies` (task IDs), `base_sha`, `check_names`, `max_attempts` (1..10),
`provider`, `model` (nullable). IDs match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
Paths must stay inside the repo (no absolute paths, escapes or escaping symlinks).
A dependency needs current passing evidence before this task's checks run.

Examples: `examples/project.json`, `examples/tasks/`. Create tasks with
`python scripts/fleet.py new-task --id ID --title T --outcome O --criterion C`.
Write observable acceptance criteria, include a negative case, and never edit criteria just to get green checks.
