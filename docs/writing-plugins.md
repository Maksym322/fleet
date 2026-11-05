# Writing your own skill or plugin

A plugin is a directory with `plugin.json` (Claude Code) and/or `.codex-plugin/plugin.json` (Codex)
plus a `skills/` folder. Each skill is `skills/<name>/SKILL.md`.

Template for `skills/my-skill/SKILL.md`:

    ---
    name: my-skill
    description: One sentence saying when an agent should use this skill.
    ---

    Resolve the selected project. Read references/workflow.md.
    Run: python <PLUGIN_ROOT>/scripts/fleet.py status --repo <REPO>
    Return: what you did, evidence, and next user action.

Rules: name equals the folder name; keep instructions short and imperative; link shared docs with
relative paths (`../../references/...`); never embed credentials or personal paths.
Keep both manifests' `name` and `version` identical (tests/test_package.py checks this).
