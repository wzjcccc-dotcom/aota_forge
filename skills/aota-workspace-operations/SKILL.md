---
name: aota-workspace-operations
description: Shared bounded workspace operations (search/read/write, test, worker handoff/result lifecycle) via aota.invoke
category: forge
tags: [aota, workspace, operations, search, read, write, test, lifecycle]
---

# AOTA Workspace Operations — Shared Canonical Procedure

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Per-role workflow lives in your Role Skill. AF decides authorization; visibility is never authority.

## Two legitimate contexts

```text
Task-main unbound: supply the canonical plan_ref locator.
  workspace.search { plan_ref, query, ... }
  workspace.read   { plan_ref, path, ... }
  plan_ref locates the trusted project root server-side; root_ref is
  normally NOT required and you never guess root paths.
Worker / trusted bound: the grounded task context (project, worktree
  sandbox, handoff scope) comes from the trusted binding; do not restate
  authority fields.
```

## Search

`workspace.search {"plan_ref": "owner/repo#59", "query": "needle", "max_results": 10}`. `query` required non-empty max 256; `max_results` 1..50; `scope` project-relative, no `..`. Worktree-scoped, lexical, no symlink follow. Result is evidence, not authority.

## Read

`workspace.read {"plan_ref": "owner/repo#59", "path": "src/main.py", "max_bytes": 8192}`. `path` project-relative, no absolute/traversal; `max_bytes` 1..32768; `offset` >=0. Revalidated at read, symlink fail-closed, UTF-8 strict, bounded 32KiB. If the root cannot be resolved from `plan_ref`, stop with needs_input/blocked.

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
