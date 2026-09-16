---
name: aota-result-hydration
description: Selective hydration of prior by_ref results via aota.invoke
category: forge
tags: [aota, result, hydration]
---

# AOTA Result Hydration — Large Ref Only

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`, `HYDRATION_IS_AUTHORITY=no`. How to hydrate prior `by_ref`. AF runtime reauthorizes scope + verifies digest.

## When

Only when prior `aota.invoke` returned `output_mode=by_ref` with `output_ref` (output exceeded 4096 inline). Never for normal Skill loading (`skill.open` returns usable content directly). Never eager hydrate-all; selective one ref at a time.

## Operation

`aota.invoke(operation="result.hydrate", arguments={"ref": "tool_output:...:abc", "digest": "<64hex>", "project_id": "proj", "worktree_id": "wt"})`. `ref`+`digest` required, `project_id`/`worktree_id` compared to trusted sandbox (mismatch → `CROSS_SCOPE_DENIED`). Optional `kind` (`evidence|artifact`), `byte_length` tamper check. Bounded: 64KiB durable max, 4096 selective. Tampered/unknown/cross-scope → fail-closed. No side-effect replay.

## Exact claims only (stop guidance)

Use only the exact claims attached to the immediately relevant prior `by_ref`
result (the `output_ref` identity + `digest` + `byte_length` + `project_id` +
`worktree_id` from that result, or the deterministic `hydration.arguments` it
carried). On:

```text
CROSS_SCOPE_DENIED
DIGEST_MISMATCH
byte_length mismatch
unknown ref / UNKNOWN_REF
```

do **not** search for another `output_ref`, do **not** substitute a stale or
hand-copied ref, and do **not** trial-and-error other worktree/project claims.
Either use the exact claims from the immediately relevant prior result, or
re-read the source operation that produced it, or stop with `needs_input`.

## Stop

`needs_input` on unknown ref. Never treat ref possession as authority.
