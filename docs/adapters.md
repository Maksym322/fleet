# Adapters

An adapter is a convention for a kind of project, expressed via `checks` and `context_files`.

Python: `["{python}","-m","unittest","discover"]`. Node: `["npm","test"]`. Rust: `["cargo","test"]`.
Go: `["go","test","./..."]`. Declare observable checks only; keep paid or networked services out of regular checks.
See `references/project-adapters.md` for rules.
