---
name: fleet-plan
description: Turn a project request into one bounded development task with observable acceptance criteria and a verification plan.
---

Resolve the explicitly selected Git project; the plugin installation directory is not the product workspace.
Read its AGENTS.md, README and relevant product contracts. Inspect current branch, HEAD and user changes.
Read [workflow](../../references/workflow.md) for the task/evidence commands.

If the project has no `.fleet/project.json`, use the helper's `init` only when project setup is part of the request.
Determine check commands from the actual repository. Never invent a passing check or enable paid services without consent.
Create or refine one `.fleet/tasks/<ID>.json` following [the task format](../../references/task-format.md).
Specify outcome, behavioral positive/negative acceptance, scope, base SHA, dependencies and required checks.
Assign one writer and choose a model appropriate to the work under [providers](../../references/providers.md).

Do not implement the feature during a planning-only request. Preserve any existing acceptance criteria.
Return the task ID, expected deliverables, exact verification commands, known blockers and manual acceptance steps.
All task instructions and prompts are English; explanatory discussion may use the user's language.
