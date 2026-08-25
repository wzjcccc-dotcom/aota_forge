---
name: aota-chatgpt-parallel-development
description: Thin ChatGPT adapter for W execution scheduling within one approved Milestone. Depends on shared core for Plan authority, body/comment contract, Steward, and checkpoints.
---

# AOTA ChatGPT Parallel Development — Execution Adapter

This is a **Milestone-internal W scheduling adapter**. It does not own Plan
authority, Issue materialization semantics, Steward rules, Body/comment
contract, Milestone creation, or Plan amendment semantics. All those are owned
by:

```text
SHARED_CORE=aota-portable-plan-governance.md
PARALLEL_ADAPTER_DUPLICATES_CORE=no
ADAPTER_MAY_REFERENCE_SHARED_CORE=yes
ADAPTER_MAY_REDEFINE_SHARED_CORE=no
```

Read the shared core first for `S/M/W` identifiers, Body normative contract,
fixed 5 managed comments, body update semantics, Milestone-first workflow,
Steward checkpoints (`PLAN_INIT / MILESTONE_CLOSE / PLAN_CLOSE`), Git
checkpoint levels, and materialization timing.

## Entry condition

Parallel scheduling is only valid for an **approved Milestone Plan**:

```text
PARALLEL_ADAPTER_REQUIRES_APPROVED_MILESTONE_PLAN=yes
```

Approved means: source reconnaissance completed, `W1..Wn` decomposed, Codex
feasibility input reconciled, user approved. This adapter must not invent new
`W` scope. If execution discovers a required Plan change, stop and route to:

```text
aota-chatgpt-project-planning.md (Friday planning entry)
```

## Scope owned by this adapter

- W execution scheduling within one approved Milestone
- dependency DAG, `READY_SET / DISPATCH_SET / DEFERRED_READY_SET`
- safe parallelism, source ownership, `SHARED_HOT_FILES` control
- manual Codex prompt packages for isolated `W`
- execution result reconciliation and mechanical integration
- transition into one integrated Milestone review

Explicitly **not** owned: Plan authority, Body/comment contract, Steward,
Milestone creation/amendment, or body mutation.

Execution adapter identity:

```text
EXECUTION_ADAPTER=chatgpt-manual-codex
DIRECT_WORKER_DISPATCH=no
MANUAL_CODEX_DISPATCH=yes
```

## Formal identity

```text
FORMAL_GOVERNANCE_IDS=S_M_W
S<n>=Subplan  M<n>=Milestone  W<n>=Work Item
WORK_ITEM_REF=S2/M3/W1  (or M3/W1 for standalone Plan)
LANE_ID_IS_GOVERNANCE_ID=no
WORK_ITEM_REF_IS_GOVERNANCE_ID=yes
```

Physical execution may use `LANE_ID` (e.g. `w1-api`) only as an
executor-local alias. If no extra slug is needed, `LANE_ID` is derived from
`W`. Never generate `M2-A / W1-R1 / W1-R2` as governance identity. Never use
`W` to mean Workstream for new Plans (`LEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE=yes` only).

## DAG

Each W is one DAG node:

```text
WORK_ITEM_REF
OBJECTIVE
DEPENDENCIES
READ_SCOPE
WRITE_SCOPE
SHARED_HOT_FILES
VALIDATION_SCOPE
```

Do not model review, Steward, or closure as DAG Work Item nodes.

```text
PARALLEL_DAG_IS_PLAN_AUTHORITY=no
READY_SET=dependencies satisfied, authority available, write scopes non-conflicting
DISPATCH_SET=bounded subset chosen for coupling / merge risk / capacity
DEFERRED_READY_SET=ready but deferred with reason
```

The DAG is an executor artifact, not Plan authority.

## Parallelism rule

Allowed in parallel:

- independent W with non-overlapping write scopes
- partially coupled W with isolated implementation, shared wiring deferred to integration

Serialized:

- dependency-required W
- shared hot-file final wiring
- integrated Milestone review `RV1` / repair `RV2`
- `MILESTONE_CLOSE` and deployment/runtime activation

```text
PARALLELISM_IS_OPTIMIZATION=yes
PARALLELISM_MUST_NOT_CHANGE_PLAN=yes
PARALLELISM_MUST_NOT_INCREASE_AUTHORITY=yes
```

Do not artificially split one tightly-coupled W into many lanes for throughput.

## Work Item cheap validation

Each W does bounded cheap construction validation only:

```text
syntax / import-load / typecheck / lint / build / targeted unit test / targeted contract probe
(choose minimal subset relevant to that W)
```

Per lane / per W **do not** run by default:

- independent Plan Review, independent source review, full regression, E2E,
  Steward ceremony, or `KNOWN_GOOD` checkpoint.

```text
CHEAP_VALIDATION_EARLY=yes
EXPENSIVE_REVIEW_LATE=yes
NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=no
```

If cheap validation FAILs, bounded source fix within the same W is allowed
until `source-ready`. This is not `RV1/RV2` and not a governance repair
ceremony.

## Parallel wave / dependency semantics

Independent W may dispatch from the same verified base. If `W2` depends on
`W1`'s source output, `W2`'s assigned base must actually contain that source.

```text
DEPENDENCY_REQUIRES_SOURCE_IN_BASE=yes
REPORTED_PASS_IS_NOT_SOURCE_PRESENCE=yes
```

Valid options: mechanical integration of predecessor source then dispatch, or
dispatch from an explicit verified frontier commit that includes the predecessor.

Any mid-Milestone mechanical integration advancement:

```text
PARALLEL_WAVE_INTEGRATION_IS_REVIEW=no
PARALLEL_WAVE_INTEGRATION_IS_MILESTONE_ACCEPTANCE=no
PARALLEL_WAVE_INTEGRATION_REQUIRES_STEWARD=no
```

Not a closure or acceptance ceremony.

## Returned result

Compact executor evidence only:

```text
WORK_ITEM_REF
LANE_ID
STATUS
BASE_SHA
WORKTREE
BRANCH
LOCAL_COMMIT
CHANGED_FILES
CHEAP_VALIDATION
SHARED_HOT_FILES_TOUCHED
UNRELATED_DIRTY_FILES_PRESERVED
BLOCKING_FINDINGS
INTEGRATION_NOTES
```

Not a large test matrix. Not auto-written to GitHub.

## Integration and ONE Milestone review

When all required W are `source-ready` and necessary source has been
mechanically integrated, create one final integrated source state, then enter:

```text
RV1  (one integrated Milestone review)
```

Review covers: Milestone objective, acceptance criteria, cross-W consistency,
source integration, scope drift, architecture/security constraints, relevant
regression, smoke/E2E only when Milestone requires it.

```text
NORMAL_MILESTONE_FORMAL_REVIEW_COUNT=1
REVIEW_BATCH_ORIENTED=yes
FINDING_BY_FINDING_REVIEW_LOOP_DEFAULT=no
```

Do not run: per-lane review, per-W review, per-integration-wave review, plus
final review. If `RV1 FAIL`: return to Friday, aggregate findings, drop false
positives / style-only / out-of-scope noise, group real defects, create repair
W, execute, integrate, then `RV2` (`REVIEW_COUNT_MUST_BE_JUSTIFIED_BY_NEW_INFORMATION=yes`).

## GitHub materialization

This adapter does not create GitHub comments directly.

```text
ROUTINE_WORK_ITEM_GITHUB_COMMENT=no
ROUTINE_WORK_ITEM_PROGRESS_SYNC_AT_MILESTONE_BOUNDARY=yes
```

Routine W completion: no GitHub sync. Material durable findings route to the
shared materialization policy: `#2` currently-valid engineering info, `#3`
material defect, `#4` supporting technical info, Body + `#5` only for material
Plan amendment. The legacy `major historical event -> append arbitrary Event Log
comment` rule is retired; fixed managed-comment model applies.
