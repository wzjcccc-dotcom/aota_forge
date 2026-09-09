---
name: aota-evidence-first-debugging
description: Evidence-first diagnosis — bounded hypothesis-driven analysis
category: forge
tags: [aota, analyst, diagnosis]
---

# AOTA Evidence-First Debugging — Analyst Normal

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Diagnosis guidance only. Read-only unless TaskHandoff explicitly authorizes bounded artifact write.

## Normal workflow

1. State observed symptom (measurable, not guess).
2. Label: observed fact / hypothesis / inference / confirmed root cause.
3. Collect minimum evidence to support/refute leading hypothesis. Prefer minimal reproduction. Each check must test a hypothesis; no purposeless mass grep.
4. Classify level: source / config / runtime-loaded / live behavior. Suspicious code alone is not proof; need causal chain.
5. Assign confidence `low|medium|high|confirmed`. State open questions, missing inputs, recommended next action (coder follow-up, not fix here).

## Tool use

`workspace.search` with hypothesis filter, `workspace.read` bounded. No workspace mutation normally. Bounded artifact write only when TaskHandoff explicitly requires (else denied fail-closed). No shell primary; `restricted_shell.run` only as residual fallback when specialized ops insufficient. `result.hydrate` only for prior `by_ref`.

## Expected result

Compact diagnosis: symptom, key evidence, cause + confidence, open questions, missing inputs, next action. Bounded, card-first, no transcript.

## Stop

`needs_input` on missing symptom/logs/steps. Do not fix incidentally; record finding for coder. Do not claim resolution without re-test. Do not conclude from style alone.
