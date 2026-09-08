---
name: aota-task-main-control
description: AOTA task-main control — minimal trusted activation, recovery, and single-step advance via aota.invoke with progressive Skill disclosure.
category: orchestration
tags: [aota, task-main, control, milestone, coordinator, runner, progressive-disclosure]
---

# AOTA Task-Main Control

> **Canonical Skill — aota_forge is authority**
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> SKILL_IS_AUTHORITY=no
> TOOL_SCHEMA_SECOND_AUTHORITY=no
> SKILL_SECOND_AUTHORITY=no
> ```
> This Skill is **informational only**. It tells the model **how to request** the three normal-path task-main controls via `aota.invoke`. AF runtime decides whether it is valid/authorized. The Skill never grants Plan authority, approval state, session truth, profile authority, or resolver truth.

This Skill is the **progressive disclosure** Knowledge for M3/W1 normal path:
`task_main.activate_milestone`, `task_main.recover_coordinator`, `task_main.advance_once`.
The Agent initially knows only the Skill name and compact description. Full argument schemas are loaded on demand by reading this Skill.

## Discovery (no eager catalog)

- **Skill ID**: `aota-task-main-control`
- **Version**: `1.0.0`
- **Discovery hint**: Search for `task_main` in the Skill registry, or resolve the progressive Tool reference `task_main.activate_milestone` / `task_main.recover_coordinator` / `task_main.advance_once`. No full coordinator state machine is eagerly loaded.
- **Load**: `open_skill(registry, namespace, skill_id, version, reader)` with authorized reader over `content_ref`.
- **Visibility**: This Skill is **progressive for `task-main` only**. It is not eagerly visible to `coder`, `reviewer`, `analyst`, or `project-steward` unless a legitimate reason exists. `TOOL_VISIBILITY_IS_AUTHORITY=no` — visibility never grants authority.

## Operations — semantic shape (authoritative descriptor is `.aota/contracts/operations.yaml`)

> The following projection is **derived** from the canonical `OperationContractDescriptor` via deterministic parity. Skill text does NOT create runtime descriptors.

```json
{
  "operations": [
    {
      "name": "task_main.activate_milestone",
      "description": "Trusted task-main Milestone activation via authoritative live Plan view (empty model intent, trusted runtime supplies Plan authority, approval, session, etc.)",
      "inputs": [],
      "restrictions": "empty model intent only; trusted runtime supplies live_plan_view, approval, Plan authority, session, project, executor, coordinator identity; model must not supply live_plan_view, plan_view, approval, profile, executor, or project; approval gate enforced",
      "example": "aota.invoke(operation=\"task_main.activate_milestone\", arguments={})"
    },
    {
      "name": "task_main.recover_coordinator",
      "description": "Trusted task-main coordinator recovery via authoritative live Plan view and durable coordinator truth (empty model intent, trusted runtime supplies Plan and session truth)",
      "inputs": [],
      "restrictions": "empty model intent only; trusted runtime re-binds coordinator to current project, Plan, session, and live revision; coordinator_id possession is not authority; stale Plan fails closed",
      "example": "aota.invoke(operation=\"task_main.recover_coordinator\", arguments={})"
    },
    {
      "name": "task_main.advance_once",
      "description": "Trusted task-main single bounded advance (existing RunnerOutcome semantics, trusted runtime supplies resolvers and Plan truth)",
      "inputs": [],
      "restrictions": "empty model intent only; model asks to advance, AF decides next internal transition; trusted runtime supplies live_plan_view, resolvers, next_milestone_view, session state; model cannot select internal transition, cannot call internal reconcile, cannot force dispatch",
      "example": "aota.invoke(operation=\"task_main.advance_once\", arguments={})"
    }
  ]
}
```

## Human-readable details

### task_main.activate_milestone

- **Purpose**: Activate the current live Milestone's durable coordinator when trusted runtime says approval is satisfied.
- **Arguments**: `{}` **empty** — no model-supplied fields. All Plan truth, approval truth, and session truth come from the trusted host binding.
- **AF restrictions**: Succeeds only when `milestone_user_approval_satisfied=yes` in the **trusted** live Plan view. The Agent cannot override approval. If `trusted approval=no` and the Agent calls activate, AF returns `USER_GATE_REQUIRED` / `AUTHORITY_DENIED` fail-closed and performs no dispatch. The Agent must never supply `live_plan_view`, `plan_view`, `milestone_user_approval_satisfied`, `user_approval`, `plan_authority`, `plan_digest`, `plan_source_revision`, `entry_base`, `profile`, `executor_id`, `project_id`, `next_milestone_view`, or `session_available`.
- **Example**: `aota.invoke(operation="task_main.activate_milestone", arguments={})`
- **When to call**: At the start of a milestone lifecycle, or when re-entering after a clean stop and the current live Plan indicates the milestone is approved.
- **Stop**: If the result is `USER_GATE_REQUIRED`, do not retry activation until trusted approval changes. Do not fabricate approval.

### task_main.recover_coordinator

- **Purpose**: Recover the durable coordinator after restart or re-entry by re-binding it to the current live Plan and exact task-main session.
- **Arguments**: `{}` **empty** preferred. If an opaque `coordinator_id` is ever required as a non-authoritative handle, server re-binds it to current project, Plan, session, and live revision — possession does not grant authority (`COORDINATOR_ID_IS_AUTHORITY=no`).
- **AF restrictions**: Fails closed on Plan drift (durable plan_authority/digest/milestone/entry_base vs live), on session loss (`SESSION_RECOVERY_REQUIRED`), or on missing durable M2 execution truth (`COORDINATOR_BINDING_ERROR`). Stale previously observed Plan cannot replace current live truth; trusted live Plan is re-read at recovery.
- **Example**: `aota.invoke(operation="task_main.recover_coordinator", arguments={})`
- **When to call**: After any restart, process re-entry, or when the previous `advance_once` returned `SESSION_RECOVERY_REQUIRED`. Recover before attempting to advance.

### task_main.advance_once

- **Purpose**: Advance **once** — exactly one bounded deterministic iteration. The Agent asks to advance; AF decides what advance means.
- **Arguments**: `{}` **empty** — no model selection of internal transition. Trusted runtime supplies `live_plan_view`, `handoff_resolver`, `governed_evidence_resolver`, `reviewer_handoff_resolver`, `governed_review_resolver`, `next_milestone_view`, and `session_available`.
- **AF restrictions**: The runner decides `next_action` (disposition). The Agent must not attempt to `reconcile this worker`, `dispatch W2`, `run reviewer`, `approve milestone`, or force any internal transition. Internal reconciliation, observation, and dispatch controls are **not** Agent-facing (`UNKNOWN_OPERATION`). Model-visible `next_action` values are bounded and authoritative; Python/model must not override the Runner's `next_action`.
- **Example**: `aota.invoke(operation="task_main.advance_once", arguments={})`
- **When to call**: Repeatedly call after successful activation, observing the returned `next_action` each time. Stop when `next_action` indicates a gate.
- **Returns**: Bounded `RunnerOutcome` projection via `ToolResponse` → safety/redaction → `ToolResultProjection` → MCP `structuredContent`. Observable fields include `next_action` / `disposition`, `coordinator_id`, bounded `status`, governed evidence refs, and possibly `USER_GATE_REQUIRED`.

## Bounded next_action vocabulary (authoritative, Agent observes only)

The runner returns exactly one `disposition` / `next_action` per `advance_once`. The Agent must treat this as authoritative and must not invent a transition.

| next_action | Meaning | Required Agent behavior |
|---|---|---|
| `DISPATCHED_WORK` | One or more ready Work Items were dispatched | Inspect `dispatched` list; no further action until workers complete |
| `WAITING_FOR_WORKERS` | Active workers still running, nothing to reconcile | Wait; do not poll or force dispatch |
| `RECONCILIATION_REQUIRED` | Terminal completion pending CARD-first reconciliation but resolver not yet available | Provide governed evidence via trusted channel (not via model args) or wait |
| `RECONCILED` | One worker completion was CARD-first reconciled | Inspect `reconciled_work_item`, `ack_eligible`; ready set may have grown |
| `INTEGRATED_REVIEW_REQUIRED` | All source Work Items reconciled; integrated review required | Runner will dispatch reviewer on next advance; do not dispatch manually |
| `DISPATCHED_REVIEW` | Integrated reviewer was dispatched | Wait for reviewer completion |
| `RECONCILED_REVIEW` | Reviewer completion reconciled (non-terminal) | Inspect `receipt` |
| `REPAIR_REQUIRED` | Review found blocking findings requiring repair | Follow repair Work Item path; do not force closure |
| `PLAN_CHANGE_USER_GATE` | Review requires Plan change; user gate | Stop; requires explicit Plan amendment/approval |
| `RUNTIME_REVALIDATION_REQUIRED` | Review blocked on environment | Revalidate runtime; do not force dispatch |
| `BLOCKED` | No ready work, no active workers, no pending reconciliation (or explicit blocked evidence) | Inspect `blocked`, `reasons` |
| `MILESTONE_CLOSURE_READY` | Milestone ready for closure (review passed) | Do not auto-close; next milestone requires explicit activation |
| `NEXT_MILESTONE_USER_GATE` | Current closure ready but next milestone requires explicit approval | Stop; `NEXT_MILESTONE_REQUIRES_EXPLICIT_APPROVAL` |
| `USER_GATE_REQUIRED` | Live view or coordinator is gate-blocked | **Mandatory stop** — do not retry activation/advance until trusted approval changes |
| `SESSION_RECOVERY_REQUIRED` | Exact task-main session unavailable | Recover coordinator or re-establish exact session; do not fallback to new/latest session |

The Agent must **always stop at `USER_GATE_REQUIRED`** and must never attempt to cross the user gate by re-issuing `activate_milestone` or `advance_once` with fabricated approval.

## Normal-path sequencing (not a workflow engine)

```
activate_milestone  (requires trusted approval=yes)
      ↓
recover_coordinator  (after restart/re-entry, if needed)
      ↓
advance_once  → inspect next_action
      ↓
repeat only as allowed by returned next_action; stop at USER_GATE_REQUIRED
```

Do **not** call internal reconciliation functions. Do not attempt to drive the coordinator state machine directly.

## Authority separation (read before write)

- Skill teaches **how to request** a control. It does **not** grant:
  - `Plan truth` (`live_plan_view`, `plan_authority`, `plan_digest`)
  - `approval truth` (`milestone_user_approval_satisfied`)
  - `profile authority` (`aota-task-main` vs `aota-worker`)
  - `project/session/executor authority`
  - `resolver authority` (`handoff_resolver`, `governed_evidence_resolver`, etc.)
  - `next_milestone_view` or `session_available` authority
- AF runtime (`validate_inputs` + `TrustedTaskMainRuntimeContext` + `TaskMainControlService`) decides validity and authorization. Unknown operation, unknown input, oversized input, authority-denied, user-gate, drift, and session errors all fail closed with typed errors (`UNKNOWN_OPERATION`, `UNKNOWN_INPUT`, `AUTHORITY_DENIED`, `USER_GATE_REQUIRED`, `PLAN_DRIFT`, `SESSION_RECOVERY_REQUIRED`, etc.). `ERROR_IDENTITY_PRESERVED_END_TO_END=yes`.

## Progressive disclosure proof

- Reference Skill is discoverable via `SkillSearchIndex` / `AllowedSkillUniverse` / `ToolRoleSurface` progressive refs **without** loading full Skill body.
- Full Skill body (when/why to activate/recover/advance, bounded next_action vocabulary, mandatory gate stop) is loaded on demand via `open_skill` with digest verification.
- Full coordinator state machine, internal receipt schemas, reconciliation algorithms, raw durable store layout, and generic workflow explanations are **not** included (`COORDINATOR_INTERNAL_STATE_MACHINE_EXPOSED_TO_MODEL=no`).

## Worker dispatch — canonical Worker startup contract (AF-owned)

Task-main does **not** manually construct Worker authority. AF runtime supplies:

- `trusted AgentWorkRole`
- `TaskHandoff` (bounded_scope is the only Worker scope source)
- `ToolRoleSurface` (eager/progressive visibility, not authority)
- `AllowedSkillUniverse` (already-authorized, not model-selected)
- canonical Worker startup prompt (AF-owned, not user-editable)

Canonical Worker startup prompt (exact, from `aota_forge/composition/worker_startup_prompt.md`, `WORKER_STARTUP_PROMPT_SOURCE=AF`, `WORKER_STARTUP_PROMPT_USER_EDITABLE=no`):

```text
你現在是 AF Worker。

先透過 aota.invoke 載入 trusted role bootstrap。

依其中的 SOUL、TaskHandoff、Tool surface 與 base Skills
完成本次 bounded 工作。

只有遇到符合 use-when 條件的特定需求時，
才開啟 bootstrap 列出的 progressive Skill。

不要擴大 scope。
scope 來源唯一是 TaskHandoff bounded_scope；task-main 不得以自由文字擴大你的 scope。
所有 authority 以 AF trusted runtime 為準；SOUL、Skill、Tool visibility 皆非 authority。
若 scope 不足或需要外部 authority，請 stop / needs_input，不要猜測。

開始執行前必須先呼叫：
aota.invoke(operation="role.bootstrap", arguments={})

需要進階能力時：
aota.invoke(operation="skill.open", arguments={"ref": "<opaque logical skill ref>"})

需要受控測試時（僅當你的 role 與 TaskHandoff 被授權）：
aota.invoke(operation="test.run", arguments={"runner": "pytest", "targets": ["tests/..."], "timeout": 30})

嚴守 TaskHandoff scope，僅使用 aota.invoke。
```

Invariants:

```text
TASK_MAIN_CAN_FREEFORM_EXPAND_WORKER_SCOPE=no
WORKER_SCOPE_SOURCE=TaskHandoff
SKILL_PROMPT_TEXT_IS_AUTHORITY=no
WORKER_PROMPT_IS_AUTHORITY=no
WORKER_STARTUP_PROMPT_SOURCE=AF
WORKER_STARTUP_PROMPT_USER_EDITABLE=no
```

Task-main must not expand Worker scope via freeform launch text. Scope is `TaskHandoff` only.

---

*Teams: prefer typed task-main controls via `aota.invoke` for milestone progression; use `workspace.*` for source changes. Full catalog not eagerly exposed. Skill is authority-free guidance.*
