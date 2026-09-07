---
name: aota-work-classify-and-plan-gate
description: Canonical AOTA work classification and plan gate — P0/P1/P2 depth, A0/A1/A2 architect gate, delivery path, and convergence rules.
category: orchestration
tags: [aota, classification, plan, gate, intake]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-work-classify-and-plan-gate/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Work Classify and Plan Gate

This is the canonical reference for AOTA work classification and the plan
gate. It defines the planning depth (P0/P1/P2), architect gate (A0/A1/A2),
delivery path (fast/standard/deep), and proportional convergence rules.
The orchestration Skill routes intake situations here; this Skill does not
itself create Plans or dispatch tasks.

## Classification dimensions

`aota_work_classify` returns three independent dimensions:

- **planning_depth**: P0, P1, P2
- **architect_gate**: A0, A1, A2
- **delivery_path**: fast, standard, deep

P0+A2 and P2+A0 are valid combinations. The dimensions are independent; one
does not force another.

## Planning depth

| Depth | Applies to | Requirements |
|-------|-----------|--------------|
| P0 | Clear goal, low risk, standalone | Exact goal, write scope, acceptance, validation tier, stop conditions, Human Checkpoint only |
| P1 | Medium complexity, bounded Work Items | Additionally: bounded Work Items, dependencies, expected sessions, medium-risk choices, goal and next action |
| P2 | High complexity, multi-milestone | Additionally: non-goals, milestones, architecture/rollout risks, checkpoints, review scope, initial queue |

P0 does not create an administrative Plan. P1 creates a lightweight Plan
with one bounded milestone. P2 creates a full Plan with milestones, Work
Items, and dependencies.

## Architect gate

| Gate | Requirement |
|------|-------------|
| A0 | No Architect review |
| A1 | task-main may invoke Architect for unresolved designs, uncertainty, cross-runtime/repo impact, durable contract, or user request; record a deliberate skip |
| A2 | Architect review required before P1/P2 approval or high-risk SPEC freeze |

A2 does not force P1/P2; P0+A2 is valid. P2 does not by itself force A2.
P2 preflight is selective: require it for `spec_preflight=required`,
high-risk, security/schema/migration, architecture contracts,
cross-service protocols, deployment topology, a first design specimen, or
an explicit Plan finding.

## Delivery path

| Path | Applies to | Flow |
|------|-----------|------|
| Fast | Clear requirements, low risk, local changes | SPEC → approval → coder → minimal validation → decision |
| Standard | Multi-file, new features, medium risk | Convergence → SPEC → architect preflight (if risk warrants) → coder → reviewer (if needed) → validation → decision |
| Deep | Control plane, auth, migrations, high blast radius | Convergence → DESIGN → architect design_review → SPEC → architect spec_preflight → Human approval → coder → reviewer → E2E → close |

## Proportional convergence

1. Capture only enough bounded facts for preliminary routing: title, desired
   outcome, known scope/deliverable/constraints/risk signals, explicit Plan
   or Architect request, expected live/deploy work.
2. Call `aota_work_classify` with task-main's bounded facts; never pass a
   raw conversation. It has no side effects.
3. If `classification_status=needs_input`, convert its fixed `question_code`s
   into 1-3 highest-impact `clarify` questions. Clarify only when the answer
   can change planning depth, architect gate, delivery path, scope,
   acceptance, write boundary, architecture, or Human Checkpoint.
4. Proportionally converge to the appropriate depth candidate.
5. Call `aota_work_classify` again after convergence. This final result
   drives the current flow.

## Flow selection

| Final classification | Required route |
|---|---|
| P0 | Standalone SPEC; do not create an administrative Plan |
| P1 | Lightweight Plan: one bounded milestone and required Work Items |
| P2 | Full Plan: milestones, Work Items, dependencies, and progress updates |
| A0 | No Architect review |
| A1 | task-main may invoke Architect; record a deliberate skip |
| A2 | Architect review required before P1/P2 approval or high-risk SPEC freeze |

## Routing integration

The orchestration Skill routes intake situations to this Skill via the
reference routing map. This Skill does not duplicate the orchestration flow;
it is a reference document for classification and plan gate rules.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `reference` (loaded by task-main; not in active_skills)
- Owning profile: task-main

## Deployment and Runtime Guidance

- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`AOTA_WORK_CLASSIFY_AND_PLAN_GATE_SKILL_PASS`

---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
