---
name: fleet-release
description: Assess a project's task and revision for release readiness, including current checks, migrations and manual acceptance, without deploying.
---

Read applicable project instructions and release/configuration/migration documents. Inspect actual Git state.
Use [workflow](../../references/workflow.md) for revision-linked evidence. Identify the exact candidate commit.
Check required task reports, compatibility, secrets/config prerequisites, persistent data, backups and rollback
only as relevant to this release. Separate code rollback from database restore.

Return a readiness assessment with concrete blockers, checks performed, skipped/unavailable checks,
release notes and manual acceptance instructions. Pending manual acceptance remains pending.
Do not publish, merge, deploy, delete data or call paid providers merely because release preparation was requested.
