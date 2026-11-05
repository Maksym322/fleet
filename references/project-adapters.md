# Project adapters

An adapter tells Fleet how a specific kind of project is built and verified.
In v0.1 an adapter is a convention, not code: it is expressed through the
`checks` list in `.fleet/project.json` and the `context_files` agents should read.
See docs/adapters.md for examples. Rules:

- Select the project explicitly before any mutation; a keyword alone does not identify it.
- Declare observable checks (argv + timeout) and any secret env var names.
- A missing tool must produce a precise "unavailable / setup required" result, not a guess.
- Installing a plugin or tool does not prove working access; verify connectivity separately.
- Never inspect login credentials or silently change security settings.
