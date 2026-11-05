---
name: fleet-status
description: Show the actual task progress, current check evidence and next user test action in a selected Git project.
---

Resolve the selected project and optional task ID. Read its current `.fleet/project.json` and task files.
Use the helper `status` and `report` commands in [workflow](../../references/workflow.md).
Report branch/head and distinguish current verified evidence from stale or absent evidence.
Do not mark a task accepted, merged or deployed based on passing checks alone.
If project setup, a dependency, authentication or a tool is missing, state the specific next action.
Keep the response concise and actionable; do not modify source during a status request.
