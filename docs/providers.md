# Providers

A task's `provider` is `manual` or the name of an AI CLI you use (claude, codex, gemini, ...).
Fleet never contacts a provider itself and never reads credentials: it produces prompts and records evidence.
Run `fleet.py doctor` to see installed CLIs. Guidance on model choice, delegation and review is in
`references/providers.md`. Set spending limits in your provider accounts.
