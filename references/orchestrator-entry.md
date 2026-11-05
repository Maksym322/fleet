# Orchestrator entry (generic)

The orchestrator is the coordinating agent session. Its loop:

1. Resolve the project (`--repo <your-repo>`) and read `.fleet/project.json`.
2. Run `fleet.py status` to see which tasks are planned, verified, stale or blocked.
3. Plan one bounded task (skill `fleet-plan`), implement (`fleet-implement`),
   review in a separate session (`fleet-review`), assess release (`fleet-release`).
4. Report with `fleet.py status` / `report`; separate implemented, verified, merged, deployed and accepted.

Never merge, deploy, spend money or delete data unless the user explicitly asks.
