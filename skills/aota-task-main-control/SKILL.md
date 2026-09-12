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

## Normal work path (source → handoff → task.start)

`activate`/`recover`/`advance` return the authoritative bounded Work source in `work_context` (`work_item_id`, `plan_ref`, `plan_digest`, `milestone_id`, source text or trusted by-ref identity). Read it, reason, then:

1. `aota.invoke(operation="handoff.write", arguments={"mode": "work_item", "payload": {"objective": "...", "bounded_scope": "...", "validation_expectations": [...], "semantic_stop_expectations": [...]}})`

AF fills the trusted Plan/Milestone/Work/source envelope; model refs are correlation only. The semantic payload is stored verbatim.

2. `aota.invoke(operation="task.start", arguments={"role": "coder|analyst|reviewer|project-steward", "handoff_ref": "<ref from handoff.write>"})`

AF verifies the durable handoff is grounded to the current authoritative Work source before dispatch. A `by_ref` result carries `hydration` claims for `aota.invoke(operation="result.hydrate", arguments={...})`.

`task_main.submit_work_projection` is internal compatibility only: not the normal path, not Plan authority, cannot bypass trusted Work-source grounding. Exact contracts are in `role.bootstrap` `OPERATION_GUIDANCE`. Do not guess arguments from errors. Do not read `operations.yaml`. Do not read the GitHub Issue body. Do not open schema MCP resources.

## Normal reasoning

Milestone objective, DAG/dependencies (durably projected, not LLM memory), risk (9 dims control depth/escalation, never authority), review gates (one integrated RV), Human Brake (`NEEDS_INPUT`/`CHECKPOINT`/`USER_GATE`/`BLOCKED` with affected/subgraph/milestone scope), result/card reconciliation (card-first, no raw transcript), retry needs progress rationale else escalate, user gates mandatory stop.

## Worker dispatch

AF supplies Role, Handoff (`bounded_scope` sole scope source), Tool surface, Skill universe, startup prompt. task-main never expands worker scope via freeform text. Workers start with `role.bootstrap`; open progressive Skill only on `use_when`.

## Stop

Stop at `USER_GATE_REQUIRED`, `PLAN_DRIFT`, `SESSION_RECOVERY_REQUIRED`, or insufficient evidence. Emit `needs_input`, do not guess.
