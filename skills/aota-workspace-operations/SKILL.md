---
name: aota-workspace-operations
description: AOTA workspace operations — bounded search/read/write via aota.invoke with progressive Skill disclosure.
category: forge
tags: [aota, workspace, operations, search, read, write, progressive-disclosure]
---

# AOTA Workspace Operations

> **Canonical Skill — aota_forge is authority**
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> SKILL_IS_AUTHORITY=no
> TOOL_SCHEMA_SECOND_AUTHORITY=no
> SKILL_SECOND_AUTHORITY=no
> ```
> This Skill is **informational only**. It tells the model **how to request** a workspace operation via `aota.invoke`. AF runtime decides whether it is valid/authorized. The Skill never grants permission, project/worktree authority, trusted binding, approval state, or provider selection.

This Skill is the **progressive disclosure** Knowledge for M1 normal path:
`workspace.search`, `workspace.read`, `workspace.write`.
The Agent initially knows only the Skill name and compact description. Full argument schemas are loaded on demand by reading this Skill.

## Discovery (no eager catalog)

- **Skill ID**: `aota-workspace-operations`
- **Version**: `1.0.0`
- **Discovery hint**: Search for `workspace` in the Skill registry, or resolve the progressive Tool reference `workspace.search` / `workspace.read` / `workspace.write`. No full operation catalog is eagerly loaded in bootstrap.
- **Load**: `open_skill(registry, namespace, skill_id, version, reader)` with authorized reader over `content_ref`.

## Operations — semantic shape (authoritative descriptor is `.aota/contracts/operations.yaml`)

> The following projection is **derived** from the canonical `OperationContractDescriptor` via deterministic parity check. Skill text does NOT create runtime descriptors.

```json
{
  "operations": [
    {
      "name": "workspace.search",
      "description": "Bounded workspace lexical search (worktree-scoped, read-only)",
      "inputs": [
        {"name": "query", "type": "str", "required": true},
        {"name": "max_results", "type": "int?", "required": false},
        {"name": "scope", "type": "str?", "required": false}
      ],
      "restrictions": "worktree-scoped, bounded max_results 1..50, lexical, no symlink follow, no persistent index, result is content evidence not authority",
      "example": "aota.invoke(operation=\"workspace.search\", arguments={\"query\": \"needle\", \"max_results\": 10})"
    },
    {
      "name": "workspace.read",
      "description": "Bounded workspace file read (text, worktree-scoped)",
      "inputs": [
        {"name": "path", "type": "str", "required": true},
        {"name": "max_bytes", "type": "int?", "required": false},
        {"name": "offset", "type": "int?", "required": false}
      ],
      "restrictions": "worktree-scoped, bounded 32KiB, UTF-8, revalidated at read, symlink escape fail-closed, no absolute/traversal",
      "example": "aota.invoke(operation=\"workspace.read\", arguments={\"path\": \"src/main.py\", \"max_bytes\": 8192})"
    },
    {
      "name": "workspace.write",
      "description": "Bounded workspace file write (worktree-scoped, mutation)",
      "inputs": [
        {"name": "path", "type": "str", "required": true},
        {"name": "content", "type": "str", "required": true},
        {"name": "mode", "type": "str", "required": true}
      ],
      "restrictions": "worktree-scoped, bounded 4096 bytes, modes create_only|replace_existing|create_or_replace, parent symlink escape fail-closed, atomic replace, digest-bound artifact ref, no authority via result",
      "example": "aota.invoke(operation=\"workspace.write\", arguments={\"path\": \"notes.txt\", \"content\": \"hello\", \"mode\": \"create_only\"})"
    }
  ]
}
```

## Human-readable details

### workspace.search
- **Purpose**: Bounded lexical search inside the trusted worktree (read-only).
- **Arguments**:
  - `query` `str` **required** — non-empty, max 256, NUL-free, not absolute path.
  - `max_results` `int?` **optional** — 1..50, bounded; omitted → 50.
  - `scope` `str?` **optional** — logical project-relative scope, no `..`, no `//`, no absolute, no backslash.
- **AF restrictions**: Search root is derived exclusively from trusted `WorktreeSandboxBoundary`; caller-supplied absolute roots rejected; cross-project search fail-closed; symlink escape fail-closed; persistent index not created; result is evidence, not authority.
- **Example**: `aota.invoke(operation="workspace.search", arguments={"query": "TODO", "scope": "src"})`

### workspace.read
- **Purpose**: Bounded read of a text file inside the trusted worktree (read-only).
- **Arguments**:
  - `path` `str` **required** — project-relative, no absolute, no `..`, no backslash, NUL-free.
  - `max_bytes` `int?` **optional** — 1..32768, bounded.
  - `offset` `int?` **optional** — >=0, bounded.
- **AF restrictions**: Worktree-scoped via `WorktreeSandboxBoundary` + `TaskHandoff` + policy evidence; revalidated at read; symlink escape fail-closed; bounded output; UTF-8 strict; no binary fallback.
- **Example**: `aota.invoke(operation="workspace.read", arguments={"path": "README.md"})`

### workspace.write
- **Purpose**: Bounded file write inside the trusted worktree (mutation).
- **Arguments**:
  - `path` `str` **required** — project-relative, no absolute, no traversal.
  - `content` `str` **required** — bounded 4096 bytes UTF-8.
  - `mode` `str` **required** — `create_only` | `replace_existing` | `create_or_replace`.
- **AF restrictions**: Worktree-scoped, requires `WorkspaceMutationAuthority` (sandbox + handoff + policy + write descriptor); parent symlink escape fail-closed; cross-project/write fail-closed; atomic temp→replace; result is digest-bound artifact ref, not authority; not idempotent.
- **Example**: `aota.invoke(operation="workspace.write", arguments={"path": "out.txt", "content": "data", "mode": "create_or_replace"})`

## Authority separation (read before write)

- Skill teaches **how to request** an operation. It does **not** grant:
  - `permission grants`
  - `project/worktree authority`
  - `trusted binding`
  - `approval state`
  - `provider selection authority`
  - `runtime operation creation`
- AF runtime (`validate_inputs`, `WorktreeSandboxBoundary`, `TaskHandoff`, authority evidence, `BoundedWorkspace*Provider`) decides validity and authorization. Unknown operation, unknown input, oversized input, authority mismatch all fail closed with typed errors.

## Progressive disclosure proof

- Reference Skill is discoverable via `SkillSearchIndex` / `AllowedSkillUniverse` / `ToolRoleSurface` progressive refs **without** loading full Skill body.
- Full Skill body (argument schemas, restrictions, examples) is loaded on demand via `open_skill` with digest verification.
- Full operation catalog is **not** eagerly loaded in bootstrap/bundle; only `aota.invoke` typed dispatch + Skill reference is eager.

---
*Teams: prefer specialized AOTA operations first; load `aota-workspace-operations` Skill only when workspace access is needed. Full catalog not eagerly exposed.*
