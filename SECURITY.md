# Security policy

Report vulnerabilities privately via the repository's "Report a vulnerability" (GitHub security advisories)
feature; do not open a public issue. Maintainer: set a contact before publishing.

Scope notes: `fleet.py check` runs commands declared in `.fleet/project.json`; treat that file as code and review it
before running checks on untrusted repositories. The runner filters the environment and redacts secrets but is not a sandbox.
Never commit tokens. Fleet stores everything locally (see README).
