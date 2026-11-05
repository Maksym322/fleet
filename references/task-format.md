# Project and task v1

Project `.fleet/project.json` contains integer `schema_version: 1`, project_id,
relative context_files, checks (unique name, argv list, timeout_seconds) and secret_names.
Secret names are environment variable names, never credential values.

Task `.fleet/tasks/<ID>.json` contains integer schema_version 1, id, title, outcome,
non-empty acceptance strings, relative scope paths, dependencies as task IDs,
base_sha, check_names, max_attempts (1..10), provider and nullable model.
IDs use letters/digits/underscore/hyphen, start with letter/digit and are <=64 characters.
Paths must resolve inside the selected product; absolute paths, escapes and escaping symlinks are invalid.
Each dependency must have current passing check evidence before checks for this task execute.

Use `new-task --repo <REPO> --id <ID> --title <TEXT> --outcome <TEXT>
--criterion <TEXT> [--criterion <TEXT>]` to create a task with current HEAD and all project checks.
The project-specific task template in a product may add narrative, but the JSON is the helper input.
Use observable behavior, include a negative case, and declare files/contracts owned by this task.
Do not edit criteria during implementation merely to make checks pass.
