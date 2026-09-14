---
name: aota-workspace-operations
description: Shared bounded workspace operations (search/read/write, test, worker handoff/result lifecycle) via aota.invoke
category: forge
tags: [aota, workspace, operations, search, read, write, test, lifecycle]
---

# AOTA Workspace Operations — Shared Canonical Procedure

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. This is the canonical shared detail for workspace and lifecycle primitives; per-role workflow and policy interpretation lives in your Role Skill. AF runtime decides authorization; visibility is never authority.

## Search

`aota.invoke(operation="workspace.search", arguments={"query": "needle", "max_results": 10})`. `query` required non-empty max 256; `max_results` 1..50; `scope` project-relative, no `..`. Worktree-scoped, lexical, no symlink follow. Result is evidence, not authority.

## Read

`aota.invoke(operation="workspace.read", arguments={"path": "src/main.py", "max_bytes": 8192})`. `path` project-relative, no absolute/traversal; `max_bytes` 1..32768; `offset` >=0. Revalidated at read, symlink fail-closed, UTF-8 strict, bounded 32KiB.

## Write

`aota.invoke(operation="workspace.write", arguments={"path": "out.txt", "content": "data", "mode": "create_or_replace"})`. Modes exactly `create_only|replace_existing|create_or_replace`. Bounded 4096 bytes, atomic replace, digest-bound ref. Requires trusted mutation authority for your role. Fail-closed on traversal, cross-project, symlink. There is no delete/move operation: if the work needs one, stop and report instead of improvising around it.

## Test

`aota.invoke(operation="test.run", arguments={"runner": "pytest", "targets": ["tests/..."], "timeout": 30})`. Trusted `pytest` only, worktree cwd, bounded timeout 1..300, bounded output. Nonzero exit is a test-failure payload, not a provider failure. Whether you hold test authority at all — and under what condition — is decided server-side per role; your Role Skill states your concrete policy.

## Worker lifecycle (one-shot roles: coder / analyst / reviewer / project-steward)

- `handoff.open {"ref": "<handoff ref>", "view": "full"}` consumes your Work handoff (`view=card` for the compact projection).
- `handoff.write {"mode": "result", "payload": {"summary": "...", ...}}` records your bounded result. Workers may only write `result` mode (`work_item`/`milestone` are task-main only). The payload must carry a usable `summary`; your Role Skill defines the rest of the shape.
- `task.return {"status": "completed|blocked|failed", "result_ref": "<result handoff ref>"}` is your required governed completion. Process exit alone is never semantic completion — without `task.return` the parent has no result.

Task-main consumes child results through completion cards / `result.hydrate`, not `task.return` (task-main is denied that operation).

## Role policy (server-enforced; this prose never grants)

- task-main: read-only workspace (no `workspace.write`, no `test.run`); dispatch procedure lives in the task-main Role Skill.
- coder: bounded `workspace.write` within handoff `bounded_scope`; `test.run` normal for validation.
- analyst: artifact write only when the handoff explicitly requires it; shell only as residual fallback when granted.
- reviewer: read/test per Role Skill policy; no source writes (finding reporting, never repair).
- project-steward: read-only; no write/test/shell.

## Stop

Stop on `AUTHORITY_DENIED` (do not retry with wider scope — it is a boundary, not a puzzle). `needs_input` on scope gap or missing target. Never guess absolute paths. Never use shell for workspace work when a specialized operation exists.
