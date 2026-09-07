---
name: aota-result-hydration
description: Durable selective hydration of bounded governed result references via aota.invoke(result.hydrate) — restart-durable, scope-reauthorized, digest-verified.
category: forge
tags: [aota, result, hydration, selective, durable, bounded, digest, scope]
---

# AOTA Result Hydration

> **Canonical Skill — aota_forge is authority**
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> SKILL_IS_AUTHORITY=no
> TOOL_SCHEMA_SECOND_AUTHORITY=no
> SKILL_SECOND_AUTHORITY=no
> HYDRATION_IS_AUTHORITY=no
> REF_POSSESSION_IS_HYDRATION_AUTHORITY=no
> DIGEST_IS_AUTHORITY=no
> ```
> This Skill is **informational only**. It tells the model **when** hydration is needed and **how to request** `result.hydrate` via `aota.invoke`. AF runtime decides validity, scope reauthorization, digest verification, and bounds. The Skill never grants hydration authority, project/worktree authority, or result authority.

This Skill is the **progressive disclosure** Knowledge for M2/W1 durable selective hydration:
`result.hydrate`.

Agent initial context knows only the Skill name and compact description. Full argument schemas are loaded on demand by reading this Skill.

## Discovery (no eager catalog)

- **Skill ID**: `aota-result-hydration`
- **Version**: `1.0.0`
- **Discovery hint**: Search for `result.hydrate` in the Skill registry, or resolve the progressive Tool reference `result.hydrate`. No full operation catalog is eagerly loaded.
- **Load**: `open_skill(registry, namespace, skill_id, version, reader)` with authorized reader over `content_ref`.

## Operations — semantic shape (authoritative descriptor is `.aota/contracts/operations.yaml`)

> The following projection is **derived** from the canonical `OperationContractDescriptor` via deterministic parity check. Skill text does NOT create runtime descriptors.

```json
{
  "operations": [
    {
      "name": "result.hydrate",
      "description": "Durable selective hydration of a bounded governed result reference (worktree-scoped, digest-verified, reauthorized)",
      "inputs": [
        {"name": "ref", "type": "str", "required": true},
        {"name": "digest", "type": "str", "required": true},
        {"name": "project_id", "type": "str", "required": true},
        {"name": "worktree_id", "type": "str", "required": true},
        {"name": "kind", "type": "str?", "required": false},
        {"name": "byte_length", "type": "int?", "required": false}
      ],
      "restrictions": "worktree/project-scoped, digest-verified, current scope reauthorized, tamper fail-closed, bounded (64 KiB durable, 4096 selective), never eager all refs, no authority via ref possession or digest",
      "example": "aota.invoke(operation=\"result.hydrate\", arguments={\"ref\": \"tool_output:workspace.search:abc123\", \"digest\": \"<64 hex>\", \"project_id\": \"proj_a\", \"worktree_id\": \"wt_a\"})"
    }
  ]
}
```

## Human-readable details

### result.hydrate
- **Purpose**: Selectively hydrate one bounded governed result reference that was previously returned as `ToolOutputRef` / `GovernedReference` (by-ref). Durable payload survives process/server restart via file-backed store under `worktree_root/.aota/durable_payloads`.
- **When needed**:
  - A prior `aota.invoke` returned `output_mode=by_ref` with `output_ref` (governed ref) because output exceeded inline bound (4096).
  - Agent needs the actual bounded content evidenced by the ref. Hydration is explicit and selective — never eager hydrate-all.
- **Arguments** (minimum to identify governed ref; model does NOT supply current scope authority):
  - `ref` `str` **required** — governed logical ref identity (e.g., `tool_output:<cap>:<digest_prefix>` or artifact logical path). Bounded ≤512, no NUL.
  - `digest` `str` **required** — 64 lower hex sha256 of the durable payload (binds integrity).
  - `project_id` `str` **required** — claimed project scope from the original ref. Server compares to current trusted `WorktreeSandboxBoundary.project_id`; mismatch → `CROSS_SCOPE_DENIED` fail-closed.
  - `worktree_id` `str` **required** — claimed worktree scope. Server compares to current `WorktreeSandboxBoundary.worktree_id`; mismatch → fail-closed.
  - `kind` `str?` **optional** — `artifact` | `evidence` (default `evidence`). `tool_output:*` refs are treated as `evidence`. Unsupported kind → `UNSUPPORTED_REF_KIND` fail-closed.
  - `byte_length` `int?` **optional** — expected payload byte length for extra tamper check; mismatch → `TAMPERED_PAYLOAD` fail-closed.
- **AF restrictions** (server-side, not Skill-granted):
  - Current scope comes from trusted `WorktreeSandboxBoundary` (runtime), not model arguments. Model-claimed `project_id`/`worktree_id` are compared, never trusted.
  - Digest is verified via sha256 recomputation; tampered digest or tampered payload → fail-closed (`DIGEST_MISMATCH` / `TAMPERED_PAYLOAD`), never returned with `completeness=false`.
  - Cross-project / cross-worktree / foreign ref → fail-closed.
  - Unknown ref / missing durable file → `UNKNOWN_REF` fail-closed.
  - Hydration is bounded: request is one selective ref (bounded), output is bounded (`4096` for artifact/evidence selective, `64 KiB` durable max). Oversized → `OVERSIZED_HYDRATION` fail-closed.
  - Eager hydrate-all is prohibited — use selective `result.hydrate` per bounded ref.
  - Possessing a ref or knowing a digest does **not** grant hydration authority; only current scope reauthorization + digest verification grants content.
- **Example**:
  ```python
  # Prior by-ref result
  ref = "tool_output:workspace.search:4a8f...ab"
  digest = "4a8f...64hex..."
  # Hydrate (trusted scope supplied by server)
  aota.invoke(operation="result.hydrate", arguments={
    "ref": ref,
    "digest": digest,
    "project_id": "proj_a",
    "worktree_id": "wt_a"
  })
  # Success: {"content": "bounded text", "digest": "...", "byte_length": 123, ...}
  # Failure: {"code": "CROSS_SCOPE_DENIED", "message": "..."} etc.
  ```

## Authority separation

- Skill teaches **how to request** hydration. It does **not** grant:
  - `hydration authority`
  - `project/worktree authority`
  - `result authority`
  - `retry/side-effect replay authority`
  - `eager catalog`
- AF runtime (`WorktreeSandboxBoundary`, `ResultHydrateProvider`, `FileBackedHydrationSource`, `hydrate_one`) decides validity, scope, digest, bounds. Unknown operation, tampered ref/payload, cross-scope all fail closed with typed errors.

## Progressive disclosure proof

- Reference Skill is discoverable via `SkillSearchIndex` / `AllowedSkillUniverse` / `ToolRoleSurface` progressive refs **without** loading full Skill body.
- Full Skill body (argument schemas, restrictions, examples) is loaded on demand via `open_skill` with digest verification.
- Full operation catalog is **not** eagerly loaded; only `aota.invoke` typed dispatch + Skill reference is eager.
- Hydration does **not** replay side effects; `HYDRATION_REPLAYS_SIDE_EFFECT=no`.

## Retention / GC (explicit)

- Durable payloads live under `worktree_root/.aota/durable_payloads/<digest>.bin` (file-backed, atomic persist).
- Retention boundary = existing project/worktree runtime lifecycle.
- No automatic time-based GC. Explicit cleanup via `clear_durable_payloads(sandbox)` where lifecycle cleanup exists.
- Survives `process restart=yes`, `server restart=yes`, `worktree deletion=removed`, `project cleanup=removed`.

---
*Teams: prefer bounded inline results; use `result.hydrate` only when a prior `by_ref` governs your need. Full catalog not eagerly exposed.*
