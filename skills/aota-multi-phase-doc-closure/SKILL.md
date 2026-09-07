---
name: aota-multi-phase-doc-closure
description: Canonical AOTA multi-phase documentation closure — work item closure evidence, residual risk, checkpoint completion, and follow-up SPEC boundaries.
category: orchestration
tags: [aota, closure, documentation, multi-phase, evidence]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-multi-phase-doc-closure/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Multi-Phase Doc Closure

This is the canonical reference for AOTA multi-phase documentation closure.
It defines the evidence, residual risk recording, checkpoint completion,
and follow-up SPEC boundaries for closing Work Items and milestones. The
orchestration Skill routes closure situations here; this Skill does not
itself close Work Items or mutate Plans.

## Closure evidence

Before closing a Work Item, verify:

1. Terminal state: the worker outcome is `completed` (not `needs_input`,
   `failed`, or running).
2. Acceptance criteria: every criterion in the frozen SPEC is met.
3. Validation tier: the required validation tier has been satisfied.
4. Scope compliance: no write outside `write_scope`, no touch in
   `forbidden_scope`.
5. Review requirement: if a Reviewer was required, the review verdict is
   `pass` and task-main has made a durable decision.
6. Residual risk: any remaining risk is explicitly recorded.
7. Human Checkpoint: all required checkpoints are complete.
8. Evidence references: task ID, SPEC revision/SHA, result artifact,
   review artifact, validation receipt are all recorded.

If any criterion is not satisfied, use `needs_fix → ready` and create a
revised/new SPEC. Do not alter a frozen SPEC.

## Residual risk recording

Residual risk is the risk that remains after the Work Item is closed. It
must be explicitly recorded in the Plan decision or closure handoff:

- Risk description
- Impact assessment
- Mitigation or monitoring plan
- Whether a follow-up Work Item is recommended

A closure without residual risk recording is incomplete.

## Checkpoint completion

Each Human Checkpoint in the SPEC must be explicitly completed before
closure. A checkpoint is complete when:

- The human has confirmed the operation
- The confirmation is recorded in the Plan decision or handoff
- The resume condition is met

No reply means `NEEDS_INPUT`, never self-continue.

## Follow-up SPEC boundaries

A follow-up SPEC is a new SPEC that addresses remaining work from a closed
Work Item. It must:

- Have its own `spec_id` (not reuse the closed SPEC's ID)
- Reference the closed Work Item in `context_refs`
- Have its own frozen scope, acceptance criteria, and validation strategy
- Not modify the closed SPEC's frozen content

A follow-up SPEC is not an extension of the closed SPEC; it is a new
bounded execution contract.

## Multi-phase milestone closure

For P1/P2 Plans with multiple milestones, milestone closure requires:

- All Work Items in the milestone are closed
- Milestone evidence is recorded in the Plan
- The next milestone is selected or the Plan is ready for completion
- A durable handoff is created at the milestone boundary

Only task-main may move a Plan to `completed`, after every required Work
Item is closed, required evidence and acceptance are present, residual risk
is recorded, and checkpoints are complete.

## Routing integration

The orchestration Skill routes closure situations to this Skill via the
reference routing map. This Skill does not duplicate the orchestration
flow; it is a reference document for multi-phase doc closure rules.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `reference` (loaded by task-main; not in active_skills)
- Owning profile: task-main

## Deployment and Runtime Guidance

- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`AOTA_MULTI_PHASE_DOC_CLOSURE_SKILL_PASS`

---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
