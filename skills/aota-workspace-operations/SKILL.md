---
name: aota-workspace-operations
description: Bounded workspace search/read/write plus test policy via aota.invoke
category: forge
tags: [aota, workspace, operations, search, read, write, test]
---

# AOTA Workspace Operations — Normal Path

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. How to request ops via `aota.invoke`. AF runtime decides authorization.

## Search

`aota.invoke(operation="workspace.search", arguments={"query": "needle", "max_results": 10})`. Args: `query` required non-empty max 256; `max_results` 1..50; `scope` project-relative no `..`. Worktree-scoped, lexical, no symlink follow. Result is evidence, not authority.

## Read

`aota.invoke(operation="workspace.read", arguments={"path": "src/main.py", "max_bytes": 8192})`. Args: `path` project-relative no absolute/traversal; `max_bytes` 1..32768; `offset` >=0. Revalidated at read, symlink fail-closed, UTF-8 strict, bounded 32KiB.

## Write

`aota.invoke(operation="workspace.write", arguments={"path": "out.txt", "content": "data", "mode": "create_or_replace"})`. Modes: `create_only|replace_existing|create_or_replace`. Bounded 4096 bytes, atomic replace, digest-bound ref. Requires `WorkspaceMutationAuthority`. Fail-closed on traversal, cross-project, symlink.

Role policy: coder normal bounded (within `bounded_scope`); analyst artifact-only when TaskHandoff explicitly requires, else denied (fail-closed product write); reviewer forbidden product write; steward forbidden; task-main no workspace mutation.

## Test policy

`aota.invoke(operation="test.run", arguments={"runner": "pytest", "targets": ["tests/..."], "timeout": 30})`. Trusted `pytest` only, worktree cwd, bounded timeout 1..300, bounded output. Coder normal path (required when validation expects). Reviewer eager-visible but conditionally authorized only when review TaskHandoff carries `validation_expectations`; else denied. Analyst/steward/task-main no automatic. Nonzero exit is test failure payload, not provider failure.

## Stop

Stop on `AUTHORITY_DENIED` (do not retry with wider scope). `needs_input` on scope gap or missing target. Never guess absolute paths. Never use shell for workspace work when specialized op exists.
