---
name: aota-multi-phase-doc-closure
description: Closure evidence, residual risk, and follow-up boundaries (reference)
category: orchestration
tags: [aota, closure, evidence]
---

# AOTA Multi-Phase Doc Closure — Reference

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Reference only; does not close Work or mutate Plan. Steward progressive reference.

## Closure evidence

Before closure verify: worker `completed`, every frozen acceptance met, required validation tier satisfied, no write outside `write_scope` / touch in `forbidden_scope`, reviewer `pass` + task-main durable decision if review required, residual risk recorded, checkpoints complete, evidence refs present (task ID, SPEC revision, result/review/validation receipts). Else `needs_fix` with new SPEC; never alter frozen SPEC.

## Residual risk

Record description, impact, mitigation/monitoring, follow-up recommendation. Closure without risk record is incomplete.

## Checkpoints

Each Human Checkpoint complete only on human confirmation recorded + resume condition met. No reply means `NEEDS_INPUT`, never self-continue.

## Follow-up

New `spec_id`, reference closed Work in `context_refs`, own scope/acceptance/validation. Not an extension.

## Milestone

All Work closed, milestone evidence recorded, next selected or Plan ready, durable handoff at boundary. Only task-main moves Plan to `completed` after all required evidence.
