---
name: aota-workspace-operations
description: Shared bounded workspace operations via aota.invoke
category: forge
tags: [aota, workspace, operations, search, read, write, test, lifecycle]
---

# AOTA Workspace Operations — Shared Canonical Procedure

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Per-role workflow lives in your Role Skill. AF decides authorization; visibility is never authority.

## Contexts

```text
Task-main unbound: supply the canonical plan_ref locator.
  workspace.search { plan_ref, root_ref?, query, ... }
  workspace.read   { plan_ref, root_ref?, path, ... }
  plan_ref locates trusted Project server-side; root_ref is a granted-root
  capability name, never a path. Bound task-main: plan_ref is normally NOT required;
  trusted binding supplies scope.
  Plan authority is read with `governance.plan`, never with `workspace.read`.
Worker / trusted bound: project, worktree and handoff scope come from the
  trusted binding; do not restate authority fields.
```

## Search

`workspace.search {"plan_ref": "owner/repo#59", "root_ref": "active-worktree", "query": "needle", "max_results": 10}`. Query is non-empty <=256; max_results 1..50; scope is project-relative. Lexical, bounded evidence; no symlink follow.

## Read

`workspace.read {"plan_ref": "owner/repo#59", "root_ref": "active-worktree", "path": "src/main.py", "max_bytes": 8192}`. Path is project-relative, no traversal; max_bytes 1..32768; offset >=0. Revalidated, symlink fail-closed, UTF-8 strict. If scope cannot resolve, stop with needs_input/blocked.

## Write

`workspace.write {"path", "content", "mode"}`. Modes exactly `create_only|replace_existing|create_or_replace`. Bounded 4096 bytes, atomic replace, digest-bound ref. Requires trusted mutation authority for your role. Fail-closed on traversal/cross-project/symlink. There is no delete/move operation: stop and report.

## Test

`test.run {"runner": "pytest", "targets": ["tests/..."], "timeout": 30}`. Trusted `pytest` only, worktree cwd, timeout 1..300, bounded output. Nonzero exit is a test-failure payload, not a provider failure. Whether you hold test authority at all — and under what condition — is decided server-side per role; your Role Skill states your concrete policy.

## Worker lifecycle (coder / analyst / reviewer / project-steward)

- `handoff.open {"ref", "view"}` consumes your Work handoff (`view=card` compact).
- `handoff.write {"mode": "result", "payload": {"summary": "...", ...}}` records your bounded result; `work_item`/`milestone` are task-main only.
- `task.return {"status": "completed|blocked|failed", "result_ref"}` is your required governed completion. Process exit alone is never completion.

Task-main consumes child results through completion cards / `result.hydrate`, not `task.return` (task-main is denied that operation).

## Role policy (server-enforced; this prose never grants)

- task-main: read-only workspace located by `plan_ref`; dispatch lives in the task-main base Skill.
- coder: bounded `workspace.write` within handoff scope; `test.run` normal for validation.
- analyst: artifact write only when the handoff explicitly requires it.
- reviewer: read/test per Role Skill policy; no source writes.
- project-steward: read-only; no write/test/shell.

## Stop

Stop on `AUTHORITY_DENIED` (a boundary, not a puzzle; do not retry with wider scope). `needs_input` on scope gap. Never guess absolute paths. Never use shell when a specialized operation exists.
