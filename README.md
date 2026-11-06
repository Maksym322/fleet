# Fleet

Fleet is a Claude Code / Codex plugin that plans, implements, reviews, releases and reports on Git tasks with multiple AI workers.
Tasks are small JSON files in your repo; checks produce revision-linked, redacted evidence so "done" means verified.
Everything is local: no daemon, no network calls, no credentials handled.

## Features

- Six skills: `fleet-plan`, `fleet-implement`, `fleet-review`, `fleet-release`, `fleet-status`, `fleet-wait`.
- Deterministic helper `scripts/fleet.py`: `doctor`, `init`, `new-task`, `validate`, `prompt`, `check`, `status`, `report`, `wait`.
- Evidence tied to the exact commit; secrets redacted; environment allowlisted; dependencies gate checks.
- Provider-neutral: use any AI CLI, or work manually.

## Waiting for work

`fleet.py wait` blocks until sessions, runs, projects, processes, files or local endpoints are idle (metadata only; see [docs/waiting.md](docs/waiting.md)).

```
python $FLEET/scripts/fleet.py wait --list                          # what is active now
python $FLEET/scripts/fleet.py wait sessions --stable 3             # wait for local Claude/Codex/Gemini sessions
python $FLEET/scripts/fleet.py wait session:codex:<id> workflow:DEMO-001 --json
python $FLEET/scripts/fleet.py wait project:../other-repo --timeout 600
```

Exit codes: 0 done, 1 timeout, 2 failed, 3 usage error.

## Quickstart (5 minutes)

Prerequisites: Python 3.12+ and git. An AI CLI (Claude Code or Codex) is needed for the skills, not for the helper.

### Install

| Tool | Install | Status |
| --- | --- | --- |
| Claude Code | `/plugin marketplace add Maksym322/fleet` then `/plugin install fleet@fleet` (CLI: `claude plugin marketplace add Maksym322/fleet && claude plugin install fleet@fleet`) | Tested: installs, lists 6 skills, runs the helper |
| Codex CLI | `codex plugin marketplace add Maksym322/fleet` then `codex plugin add fleet@fleet` | Tested: installs and lays out skills and scripts |
| Antigravity / Gemini | `agy plugin install <path to a clone of this repo>` | Manifest validated with `agy plugin validate --strict`; install not exercised end to end |
| Cursor, Copilot and any tool that reads `SKILL.md` folders | `python scripts/install_skills.py --dest <that tool's skills directory>` | Installer tested; each tool's own skill loading is not tested here |

Local development without installing: `claude --plugin-dir /path/to/fleet`.
Skills are plain `SKILL.md` folders (the common Agent Skills layout), so any tool that reads them can use Fleet.
The helper script works on its own and needs only Python 3.12+ and git.

First run, inside any Git repo of yours (`FLEET=/path/to/fleet`):

```
python $FLEET/scripts/fleet.py doctor
python $FLEET/scripts/fleet.py init --project-id demo
python $FLEET/scripts/fleet.py new-task --id DEMO-001 --title "Add greet()" \
  --outcome "greet(name) returns a greeting" \
  --criterion "greet('Ada') returns Hello, Ada!" \
  --criterion "greet('') raises ValueError"
python $FLEET/scripts/fleet.py validate
python $FLEET/scripts/fleet.py check --task DEMO-001
python $FLEET/scripts/fleet.py status
```

Edit `.fleet/project.json` first to put your real test command in `checks` (see `examples/project.json` and
`examples/tasks/`). Add `.fleet/runs/` to your `.gitignore`. Then ask your agent to use the `fleet-plan` skill.
`--repo` defaults to the current directory. Use `python3` on Linux/macOS.

## Writing your own plugin or skill

See [docs/writing-plugins.md](docs/writing-plugins.md) for a template. Other docs:
[task format](docs/task-format.md), [workflow](docs/workflow.md), [providers](docs/providers.md), [adapters](docs/adapters.md).

## FAQ / Troubleshooting

- `check` exits 2: configuration invalid or no checks declared. Run `validate` and read the message.
- Evidence is "stale": you committed or edited after the last check. Rerun `check`.
- Provider CLI not found: run `doctor`; make sure its install directory is on PATH, or use provider `manual`.
- Run the tests: `python -m unittest discover -s tests`.

## Security notes

- Never commit tokens or `.env` files. `project.json` lists secret environment variable NAMES only, never values.
- Local data: `.fleet/project.json` and tasks (versioned by you) and `.fleet/runs/` (git-ignored evidence).
  Fleet sends nothing anywhere.
- `check` executes commands from `project.json`: treat it as code. Redaction is best-effort, not a sandbox.
- Report issues per [SECURITY.md](SECURITY.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
