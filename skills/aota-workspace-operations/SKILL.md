---
name: aota-workspace-operations
description: Shared bounded workspace operations via aota.invoke
category: forge
tags: [aota, workspace, operations, search, read, write, edit, test, lifecycle]
---

# AOTA Workspace Operations — Shared Canonical Procedure

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Per-role workflow lives in your Role Skill. AF decides authorization; visibility is never authority.

## Context and reads

Unbound task-main uses `plan_ref` only as a locator; `plan_ref` is normally NOT required for a bound task-main. The trusted binding supplies authority. Plan authority is read with `governance.plan`, never `workspace.read`. Workers use their trusted project/worktree binding. `root_ref` is a granted capability name, never a path.

Use `workspace.search` for bounded discovery and `workspace.read` for project-relative UTF-8 content. Check `truncated` and `offset`; `file_sha256` identifies the full file even for ranged reads. Paths are revalidated and symlink escapes fail closed. Use `help(operation=...)` for exact current input details.

## Mutation

- `workspace.write` creates a file or performs a bounded whole-file replacement.
- `workspace.edit` is coder-only and active-worktree-only. It makes one exact literal UTF-8 compare-and-swap edit to an existing file and requires the full-file SHA-256 from `workspace.read`. Prefer edit for small changes to existing large files rather than reconstructing the whole file.
- `workspace.edit` is not regex or generic patch language. Neither operation supports file delete, move, or rename. There is no arbitrary path or shell.
- Mutation authority is trusted and operation-specific; `workspace.write` authority cannot authorize `workspace.edit`. Visibility and Skill text do not grant authority.

## Validation and lifecycle

Use `test.run` when authorized by your Role Skill. Open the Worker handoff, implement only its bounded scope, write a result with `handoff.write(mode=result)`, then complete with `task.return`; process exit alone is not completion. Task-main reconciles completion cards and governed results.

## Role policy

- task-main: read-only workspace; dispatch guidance is in the task-main base Skill.
- coder: source mutation through `workspace.write` / `workspace.edit`; `test.run` for validation.
- analyst: conditional artifact-only output; no source mutation.
- reviewer: read/test per Role Skill; no source mutation.
- project-steward: read-only workspace; no generic source mutation.

Stop on `AUTHORITY_DENIED`; do not retry with wider scope. Stop on unresolved scope gaps. Never guess absolute paths or use shell when a specialized operation exists.
