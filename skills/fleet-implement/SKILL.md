---
name: fleet-implement
description: Implement one selected Git task, run its declared checks and produce revision-linked evidence for user testing.
---

Use the selected project and task ID. Read its applicable AGENTS.md, task, README and affected contracts.
Read [workflow](../../references/workflow.md) and [providers](../../references/providers.md).
Inspect branch, HEAD and working changes. Preserve unrelated edits and use a dedicated task branch/worktree.
One writer owns the task; delegate only disjoint work with explicit file usership and bounded context.
Check required dependencies and reproduce the reported behavior before editing when feasible.

Implement the outcome and all acceptance criteria without weakening tests to obtain green results.
Run the task's declared checks with the bundled helper. A timeout, missing executable, denied tool or
empty model response is a limitation/failure, not successful implementation. Limit automatic repair to two attempts;
then report a resumable blocker or replan instead of looping.

Update affected contracts/docs. For an authorized commit workflow, commit the implementation and task specification,
then run final checks again so evidence references that exact commit. Runtime evidence is not source code.
Use a separate review session for material logic/auth/concurrency changes. Initial reviewer is read-only.

Return changed files, commit/PR, actual check outcomes and report path, unresolved findings and user test steps.
Distinguish implemented, verified, merged, deployed and user-accepted. Do not modify running deployments
or buy/enable services as a side effect of implementing a task.
