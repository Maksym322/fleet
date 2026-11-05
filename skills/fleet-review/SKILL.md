---
name: fleet-review
description: Independently review one task diff and its verification evidence against the current acceptance criteria, without editing source.
---

Resolve the product repository, task, review base and proposed head. Read applicable AGENTS.md and affected contracts.
Use [workflow](../../references/workflow.md) to inspect task status/evidence; verify the evidence is current.
Inspect the actual diff and relevant surrounding code, including untracked files when part of the assignment.
Do not edit source or fix findings during the initial review.

For each finding provide severity, file/line, concrete trigger, impact and reproduction/verification.
Prioritize behavioral defects, authorization, concurrency, migrations and acceptance gaps.
Separate reproduced defects, evidence gaps and unconfirmed hypotheses. Do not treat a runner exit code
or an author's narrative as independent proof. If checks were not run, say so.

Return findings, checks actually performed, criteria coverage and owner acceptance gaps.
Use an independent session and bounded context; see
[providers](../../references/providers.md) for choosing a reviewer.
