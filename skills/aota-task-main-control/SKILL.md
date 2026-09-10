---
name: aota-task-main-control
description: Task-main milestone orchestration — activate, recover, advance via aota.invoke
category: orchestration
tags: [aota, task-main, control, milestone]
---

# AOTA Task-Main Control — Normal Cycle

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. How to request controls. AF runtime decides approval, Plan truth, session truth.

## Activate

`aota.invoke(operation="task_main.activate_milestone", arguments={})`. Empty intent only. Succeeds only when trusted live view says approval satisfied. On `USER_GATE_REQUIRED`, stop; do not retry until approval changes. Never supply `live_plan_view`, approval, or project fields.

## Recover

`aota.invoke(operation="task_main.recover_coordinator", arguments={})`. After restart/re-entry or `SESSION_RECOVERY_REQUIRED`. Server re-binds to current Plan/session/revision. Fail-closed on drift or session loss. Stale observed Plan never replaces live truth.

## Advance

`aota.invoke(operation="task_main.advance_once", arguments={})`. One bounded iteration; AF decides transition. Observe `next_action`: `DISPATCHED_WORK`, `WAITING_FOR_WORKERS`, `RECONCILED`, `INTEGRATED_REVIEW_REQUIRED`, `DISPATCHED_REVIEW`, `REPAIR_REQUIRED`, `BLOCKED`, `MILESTONE_CLOSURE_READY`, `USER_GATE_REQUIRED`, `SESSION_RECOVERY_REQUIRED`, etc. Never call internal reconcile/dispatch. Never override `next_action`.

## Work projection (normal pre-dispatch semantic action)

If a ready Work lacks a projection, `activate`/`recover`/`advance` returns `projection_required` with `work_item_id` plus bounded `governed_work_semantics` (objective plus semantic context from canonical Plan authority). Read that context, reason about bounded execution semantics, then call once:

`aota.invoke(operation="task_main.submit_work_projection", arguments={"work_item_id": "...", "objective": "...", "bounded_scope": "...", "validation_expectations": [...], "semantic_stop_expectations": [...]})`

Exact contract is in `role.bootstrap` `OPERATION_GUIDANCE` (eager, no `skill.open` needed). `work_item_id` is correlation only, not authority. Model text is not authority and cannot expand scope. Then call `advance_once`. Do not guess arguments from errors. Do not read `operations.yaml` from workspace. Do not read GitHub Issue body. Do not open schema MCP resources. Do not use `skill.open` before ordinary projection submission.

## Normal reasoning

Milestone objective, DAG/dependencies (durably projected, not LLM memory), risk (9 dims control depth/escalation, never authority), review gates (one integrated RV), Human Brake (`NEEDS_INPUT`/`CHECKPOINT`/`USER_GATE`/`BLOCKED` with affected/subgraph/milestone scope), result/card reconciliation (card-first, no raw transcript), retry needs progress rationale else escalate, user gates mandatory stop.

## Worker dispatch

AF supplies Role, Handoff (`bounded_scope` sole scope source), Tool surface, Skill universe, startup prompt. task-main never expands worker scope via freeform text. Workers start with `role.bootstrap`; open progressive Skill only on `use_when`.

## Stop

Stop at `USER_GATE_REQUIRED`, `PLAN_DRIFT`, `SESSION_RECOVERY_REQUIRED`, or insufficient evidence. Emit `needs_input`, do not guess.
