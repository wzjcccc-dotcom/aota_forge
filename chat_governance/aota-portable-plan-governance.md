---
name: aota-portable-plan-governance
description: Shared executor-neutral governance core for Portable Plans, S/M/W hierarchy, Milestone-first development, 5-role managed comments, body normative contract, Steward checkpoints, Git checkpoints, and transitional Friday/task-main.
---

# AOTA Portable Plan Governance — Shared Core

This is the single shared semantic governance core for Chat planning.
Entry adapters may reference it but must not copy or redefine its
authority model. This file is a source contract only: it does not add
tools, dispatch work, or claim runtime support that has not been
implemented.

```text
GOVERNANCE_REVISION=chat-canonical-p1
COMMENT_PLAN_AUTHORITY=no
EFFECTIVE_PLAN_COMMENT_MODEL=deprecated
```

Provenance: semantic rewrite of
`aota-hermes-tools/skills/aota-portable-plan-governance/SKILL.md`
(revision `portable-planning-governance`) plus new S→M→W and
Milestone-first models. The ChatGPT Project upload, if present, is
snapshot/bootstrap/reference only (see `GOVERNANCE_INDEX.md`).

## Authority boundaries

| Concern | Authority |
| --- | --- |
| Portable Plan semantics | GitHub Issue body (normative contract) |
| Compact operational projection | 5 managed comments (mutable, never Plan authority) |
| Material history / rationale | decision_change_log entries (append-only) |
| Project truth and lifecycle reconciliation | Project Steward / deterministic project authority |
| Executor-specific execution | selected executor adapter and its private artifacts |
| Source history and checkpoints | Git |
| Deployment / runtime rollback | Forge-managed backup and deployment receipts |

```text
ONE_SHARED_GOVERNANCE_AUTHORITY=yes
NO_DUPLICATE_SKILL_AUTHORITY=yes
PORTABLE_PLAN_AUTHORITY=yes
ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no
UPLOADED_SNAPSHOT_CANONICAL=no
LOCAL_CANONICAL_LIVE_READ_PREFERRED=yes
```

Chat history is not canonical. Control comments are not Plan authority.
Executor private state is not Portable Plan authority. A Forge backup is
not Git source history.

## Portable Plan types and hierarchy

### Supported Plan types

A Portable Plan declares its tier via `PLAN_TYPE`:

1. `PLAN_TYPE=umbrella_program_plan` — multi-subplan coordinating program
   plan. Coordinates subplans S1..Sn, tracks cross-subplan dependencies
   and completion propagation.
2. `PLAN_TYPE=child_portable_plan` — bounded subplan delegated from an
   umbrella plan. Scoped to one S<n>.
3. `PLAN_TYPE=portable_plan` — standalone single-level plan.

### S → M → W hierarchy

New identifier model (breaking semantic change):

```text
SUBPLAN_IDENTIFIER=S<n>
MILESTONE_IDENTIFIER=M<n>
WORK_ITEM_IDENTIFIER=W<n>
FORMAL_GOVERNANCE_DEPTH=S_M_W
```

Example — Umbrella Plan:

```text
Umbrella Plan
├── S1
├── S2
└── S3

S2 Child Plan:
├── M1
│   ├── W1
│   ├── W2
│   └── W3
├── M2
└── M3

Full reference: S2/M3/W4
Standalone Plan: M3/W4
```

Formal governance stops at W. Executor-local decomposition below W uses:

```text
TASK_BELOW_WORK_ITEM_EXECUTOR_LOCAL=yes
T<n> = executor-local task, e.g. S2/M3/W4/T1
```

T does not enter the Umbrella Plan, does not require GitHub control
state, Steward, or independent review, and does not create new Portable
Plan authority.

### Legacy compatibility

Previous governance used `W<n>` = Workstream at umbrella level.

```text
LEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE=yes
NEW_PLAN_W_AS_WORK_ITEM=yes
NEW_PLAN_WORKSTREAM_IDENTIFIER_DEPRECATED=yes
```

Existing Issues/Plans using `W` as Workstream remain readable/hydratable
without migration. New Plans must not generate the old hierarchy.
Parent milestone `M` + Workstream `W` dual-namespace is retired.

### Parent vs child authority boundary

- **Parent owns**: program objective, cross-subplan architecture,
  S<n> / parent milestone definitions, child Issue mapping,
  cross-subplan dependencies, program handoff state.
- **Child owns**: child objective/scope, internal milestones M1..Mn,
  work items W1..Wn, acceptance criteria, verification gates, closure.

```text
PARENT_OWNS_CROSS_WORKSTREAM_GOVERNANCE=yes
PARENT_MIRRORS_CHILD_EXECUTION_DETAILS=no
CHILD_OWNS_INTERNAL_SCOPE_AND_MILESTONES=yes
CHILD_REDEFINES_PARENT_WORKSTREAM=no
PARENT_CHILD_MILESTONE_NAMESPACES_INDEPENDENT=yes
```

Parent `#9 S2` delegating to Child `#12` is illustrative only.
`#12 M1` is not `#9 M1`; only the string `M1` collides.

### Identifier namespaces

| Pattern | Meaning / Scope | Example |
| --- | --- | --- |
| `S<n>` | Subplan / Child Plan boundary | `S1`, `S2/M3` |
| `M<n>` | Milestone scoped to one Plan Issue | `S2/M1`, `M1` |
| `W<n>` | Work Item within one Milestone | `S2/M3/W4` |
| `T<n>` | Executor-local task below Work Item | `S2/M3/W4/T1` |
| `RV<n>` | Integrated review cycle for a Milestone | `RV1`, `RV2` |
| `R<n>` | Legacy review stage alias (read-compatible) | `W1-R1` |

```text
IDENTIFIERS_NOT_INTERCHANGEABLE=yes
WORKSTREAM_IS_NOT_MILESTONE_ID=yes
```

### Child Plan creation rule

A normal Work Item does not get its own Issue. A Milestone delegates to
exactly one Child Plan Issue only when the parent explicitly defines that
S<n> boundary.

```text
NORMAL_WORK_ITEM_SEPARATE_ISSUE=no
MILESTONE_DELEGATES_TO_CHILD_PLAN_ONLY_WHEN_EXPLICIT=yes
```

### Hierarchical hydration order

1. Governing Issue body (normative Portable Plan)
2. `milestone_progress_index` (Comment #1)
3. `development_notes` (Comment #2)
4. `defect_register` (Comment #3)
5. Local evidence references (on demand)

For cross-subplan context, hydrate the umbrella parent first, then only
the relevant Child Plan. Do not recursively hydrate all children.

```text
HYDRATE_PARENT_FIRST_FOR_CROSS_WORKSTREAM=yes
RECURSIVE_ALL_CHILD_HYDRATION=no
```

## Issue materialization model

A canonical Plan Issue has exactly one normative surface and five
managed operational projections:

```text
Plan Issue
├── Body                         (normative contract)
├── Managed Comment #1  milestone_progress_index
├── Managed Comment #2  development_notes
├── Managed Comment #3  defect_register
├── Managed Comment #4  plan_appendix
└── Managed Comment #5  decision_change_log
```

Human ordering is `#1..#5`. Machine identity is always:

```text
COMMENT_ROLE + stable comment ID
```

Ordinal position is never authority. Duplicate role comments are a hard
stop. Malformed role markers fail closed.

```text
PLAN_ISSUE_EXPECTED_MANAGED_COMMENT_COUNT=5
```

### Fixed managed-comment roles

| # | COMMENT_ROLE | Mutability | Authority | Purpose |
| --- | --- | --- | --- | --- |
| 1 | `milestone_progress_index` | mutable | `COMMENT_PLAN_AUTHORITY=no` | Current Subplan/Milestone, status, completed milestone compact vector, current blocker, next action, current integration/checkpoint reference if needed. The only near-operational ledger; keep concise. |
| 2 | `development_notes` | mutable | `COMMENT_PLAN_AUTHORITY=no` | Currently-valid engineering info only: frozen architecture decisions, implementation constraints, current technical debt, carry-forward risks, unresolved engineering decisions, compatibility constraints, genuine integration frontier. |
| 3 | `defect_register` | mutable | `COMMENT_PLAN_AUTHORITY=no` | Defect index only: defect ID, summary, severity/materiality, state, owner/work-item ref, stable evidence ref. No full transcripts. |
| 4 | `plan_appendix` | mutable | `COMMENT_PLAN_AUTHORITY=no` | Supporting technical material that does not change Plan semantics (see below). |
| 5 | `decision_change_log` | entries append-only | `COMMENT_PLAN_AUTHORITY=no` | Material historical reasons only (see below). |

All five carry `COMMENT_PLAN_AUTHORITY=no` and never
`EFFECTIVE_PLAN=yes`.

#### Role #1 — milestone_progress_index

Compact projection. Never paste: reviewer full reports, raw test matrix,
repair chronology, disposable DB names, worktree history, command output,
every commit.

#### Role #2 — development_notes

Must not become chronology. Explicitly forbidden:

- R1/R2/R3 chronology
- every source review result
- raw validation output
- lane commit history
- temporary debug logs
- every Work Item completion receipt

#### Role #3 — defect_register

Stable Issue-scoped IDs:

- `I<issue>-B<nnn>` code defects (`I12-B001`)
- `I<issue>-G<nnn>` governance defects

Milestone number is never primary defect identity.

Fields may include: `DEFECT_ID`, `STATUS`, `SEVERITY`, `SUMMARY`,
`STATE`, `OWNER/WORK_ITEM_REF`, `STABLE_EVIDENCE_REF`,
`SOURCE_FINDING_ALIAS`, `CHILD_PLAN_DEFECT_REFERENCE`,
`DEFECT_AUTHORITY`.

#### Role #4 — plan_appendix

This role replaces the old body-appendix model.

Appendix is supporting material only. It may hold:

- implementation details, interface examples, technical reconnaissance,
  source-structure notes, alternatives, bounded fixture/spec
  explanations, evidence pointers, stable technical references.

It must not silently rewrite Plan semantics.

```text
PLAN_APPENDIX_LOCATION=managed_comment
PLAN_APPENDIX_IS_PLAN_AUTHORITY=no
PLAN_APPENDIX_SUPPORTS_PLAN=yes
```

If a change would alter any of: objective, scope, non-goals,
acceptance, authority boundary, canonical truth, security/privacy
contract, schema semantics, dependencies/DAG, lifecycle/concurrency
semantics — that is a Plan amendment and must not be done via #4
alone (see Body update semantics).

#### Role #5 — decision_change_log

Replaces ad-hoc Event Log comment sprawl. The comment itself is
mutable, but each historical entry inside it is append-only.

Record only material reasons:

- Plan amendment, architecture direction change, scope change,
  user decision, material authority change, important blocked/unblocked
  turning point, intentionally abandoned approach with future
  significance.

Do not record: normal construction, review PASS, Work Item completion,
control sync.

```text
DECISION_CHANGE_LOG_ENTRY_APPEND_ONLY=yes
ROUTINE_EXECUTION_EVENT_LOG=no
NORMAL_WORK_ITEM_COMMENT_CREATION=no
```

Not every historical comment previously called "Event Log" survives
under the new model. `ROUTINE_EXECUTION_EVENT_LOG=no` is the
canonical rule — general execution history must not generate new Issue
comments. Legacy `EFFECTIVE_PLAN=yes` records remain readable, are not
rewritten, and never supersede the Issue body.

### Comment creation ceiling and overflow

Normal Plan Issue: exactly 5 managed comments. Executor must not create
`#6/#7/#8`.

If a managed comment approaches the GitHub comment size limit, stop all
additive materialization:

```text
MANAGED_COMMENT_OVERFLOW_AUTO_CREATE=no
MANAGED_COMMENT_OVERFLOW_USER_APPROVAL_REQUIRED=yes
```

Continuation, only with explicit user approval, requires the same role
plus continuation metadata:

```text
COMMENT_ROLE=plan_appendix
CONTINUATION_OF=<stable comment id>
```

The eight-field grouping metadata (`RECORD_VERSION`, `UPDATE_ID`,
`COMMENT_ROLE`, `PARENT_COMMENT_ID`, `APPENDIX_TYPE`,
`SUPERSEDES_COMMENT_ID`, `SUPERSEDES_UPDATE_ID`,
`CONTINUATION_OF`) groups evidence and corrections. It never creates
Plan authority.

## Issue body role

Issue Body = Portable Plan normative contract. It answers:
"What are we building and what constitutes success?"

It may contain:

- Plan identity / type, objective, context, scope, non-goals,
  requirements, constraints, authority model, Subplan/Milestone
  structure, acceptance criteria, dependencies, material risks,
  validation expectations.

It must not contain routine execution or progress ledgers.

```text
ISSUE_BODY_ROLE=portable_plan_normative_contract
ISSUE_BODY_EXECUTION_LEDGER=no
ISSUE_BODY_REVIEW_LEDGER=no
ISSUE_BODY_ROUTINE_PROGRESS_LEDGER=no
BODY_IS_PLAN_NOT_EXECUTION_HISTORY=yes
```

Removed operational fields (these now live only in Comment #1):

- current milestone, current status, current next action,
  routine handoff state, every Work Item receipt.

## Body update semantics

Bounded body mutability — opposite of the retired
milestone-closure-must-update-body rule, which is no longer canonical.

```text
MILESTONE_COMPLETION_UPDATES_BODY=no
MILESTONE_COMPLETION_UPDATES_PROGRESS_INDEX=yes
BODY_ROUTINE_EXECUTION_UPDATE=no
BODY_MILESTONE_COMPLETION_UPDATE=no
BODY_MATERIAL_PLAN_AMENDMENT_UPDATE=yes
IN_PLACE_PLAN_SEMANTIC_UPDATE_PREFERRED=yes
```

- Normal Milestone completion updates only Comment #1 (`progress_index`).
  Body is not touched for progress.
- Only a material Plan amendment mutates the Body. Amendment is:
  objective/scope/acceptance change, binding architecture change,
  authority boundary change, canonical model change, security/privacy
  semantic change, schema/migration semantic change, material
  DAG/dependency change, material lifecycle/concurrency semantic change.

Amendment procedure:

1. Update Body in place (current truth, not timeline).
2. Add one append-only entry into Comment #5.
3. If supporting technical material changes, update Comment #4.

## Body and comments are not append-only documents

Body is current truth. `#1/#2/#3/#4` are mutable current
projections/references. Only individual entries inside `#5` are
append-only.

```text
CURRENT_STATE_MUTABLE_PROJECTION=yes
HISTORICAL_APPEND_ONLY_REASON_LOG=decision_change_log entries only
```

The retired blanket append-only Event Log model for all comments
is not canonical for any managed comment role. Control comments are
resolved by durable `COMMENT_ROLE` markers, never ordinal alone:

```text
CONTROL_COMMENTS_MUTABLE=yes
CONTROL_COMMENTS_PLAN_AUTHORITY=no
EVENT_LOG_APPEND_ONLY=decision_change_log entries only
EVENT_LOG_PLAN_AUTHORITY=no
```

Optimistic concurrency still applies to mutable comments:

```text
CONTROL_COMMENT_READ_BEFORE_WRITE=yes
CONTROL_COMMENT_VERIFY_AFTER_WRITE=yes
CONTROL_COMMENT_STALE_WRITE_HARD_STOP=PASS
```

Duplicate role comments or malformed markers: hard stop.

## Body size governance

Soft health guard, not bureaucracy. This is AOTA governance limits,
not GitHub platform limits.

```text
BODY_HEALTHY_TARGET<=80KiB
BODY_WARNING_THRESHOLD=80KiB
BODY_ADDITIVE_MATERIALIZATION_HARD_STOP=96KiB
```

- `> 80 KiB`: perform a materialization audit. Check whether execution
  history entered the body, appendix entered the body, stale sections
  or duplicates exist, or semantic-preserving compaction is possible.
- `> 96 KiB`: additive materialization is hard-stopped. Only allowed:
  semantic-preserving compaction, minimal authority repair, true Plan
  boundary split, or a closure-critical minimal correction.

## Minimum Portable Plan contract

A complete Portable Plan can express, at minimum:

```text
plan_type (umbrella_program_plan | child_portable_plan | portable_plan)
status, objective, background/context, scope, non-goals
requirements, constraints, authority model
Subplans S1..Sn (if umbrella), milestones M1..Mn, acceptance criteria
risks, decisions, dependencies, validation expectations
```

The Issue must remain understandable without any private identifier:
executor session ID, internal Plan ID, SPEC hash, task ID, artifact
path, Profile ID, runtime revision, receipt ID.

Each Milestone records: `ID / semantic name, objective, scope, expected
W1..Wn, acceptance criteria, evidence expectations, stop conditions,
dependencies, status (planned | in-progress | blocked | review |
completed | cancelled)`.

## Milestone-first development workflow

Canonical normal workflow:

```text
Milestone Entry
→ source reconnaissance
→ Milestone planning (Friday/task-main)
→ Work Item decomposition (W1..Wn)
→ bounded Codex feasibility review (advisory)
→ Friday reconciliation
→ User approval
→ execute Work Items (cheap validation each)
→ ONE integrated Milestone review (RV1 → RV2 if needed)
→ required smoke/E2E
→ Milestone accepted checkpoint
→ compact managed-comment sync (updates #1, and #2/#3/#4 as needed)
→ next Milestone
```

### Milestone planning

At Milestone entry, Friday/task-main must:

- read accepted Plan authority
- inspect relevant source/current state
- use AOTA Reader or equivalent read-only project observation
- if deeper local inspection is needed, request Codex read-only
  reconnaissance
- produce Milestone implementation plan, decompose W1..Wn, define
  dependencies and Milestone acceptance/review expectations.

Codex performs one bounded feasibility review.

```text
CODEX_RECOMMENDATION_IS_ADVISORY=yes
FRIDAY_RECONCILIATION_REQUIRED=yes
BLINDLY_COPY_REVIEWER_RECOMMENDATION=no
USER_MILESTONE_APPROVAL_REQUIRED=yes
```

Friday must reconcile user feedback, Codex feedback, source evidence,
accepted Plan authority, and over-design risk before requesting approval.

### Work Item workflow

Normal Work Item is an execution unit, not a governance ceremony:

```text
W → SPEC → construction → cheap bounded validation → source-ready
```

```text
NORMAL_WORK_ITEM_SEPARATE_ISSUE=no
NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=no
NORMAL_WORK_ITEM_COMPLETION_APPENDIX_DEFAULT=no
NORMAL_WORK_ITEM_KNOWN_GOOD_CHECKPOINT_DEFAULT=no
NORMAL_WORK_ITEM_ACCEPTED_BASE_CEREMONY_DEFAULT=no
NORMAL_WORK_ITEM_PROJECT_STEWARD_CHECKPOINT=no
WORK_ITEM_IS_EXECUTION_UNIT_NOT_GOVERNANCE_CEREMONY=yes
```

### Cheap construction validation

Per Work Item, bounded cheap validation only:

- syntax, import/load, typecheck, lint, build, targeted unit test,
  targeted contract probe — limited to what directly relates to that W.

Do not by default: full regression, full E2E, independent reviewer
ceremony, or Steward ceremony.

```text
CHEAP_VALIDATION_EARLY=yes
EXPENSIVE_REVIEW_LATE=yes
```

### Integrated Milestone Review

After all W are source-ready, run one integrated Milestone review.
Check: Milestone objective, acceptance criteria, integration behavior,
source consistency, relevant regression, smoke/E2E if required,
unintended scope drift, architecture contract violations.

Review cycles are numbered `RV1`, `RV2`, ... — `RV` is not hierarchy.

- `RV1 PASS` → acceptance.
- `RV1 FAIL` → Friday/task-main triages all findings together, removes
  false positives / style-only noise / out-of-scope suggestions, groups
  actual defects, creates repair Work Items, executes all repair Work
  Items, then `RV2`.

```text
REVIEW_BATCH_ORIENTED=yes
FINDING_BY_FINDING_REVIEW_LOOP_DEFAULT=no
REVIEW_COUNT_MUST_BE_JUSTIFIED_BY_NEW_INFORMATION=yes
```

### Risk-scoped exception

Only genuinely high-risk / late-detection-cost changes may add an extra
pre-integration gate. Examples: destructive/irreversible migration,
security boundary, privacy contract, canonical authority transition,
critical production topology.

Such a gate must be explicit:

```text
EXTRA_GATE_ID, IDENTIFIED_RISK, JUSTIFICATION, SCOPE, END_CONDITION
```

```text
NORMAL_WORK_ITEM_EXTRA_REVIEW=no
RISK_SCOPED_EXTRA_GATE_ALLOWED=yes
EXTRA_GATE_EXPLICIT_JUSTIFICATION_REQUIRED=yes
```

"Reviewer feels safer" never justifies a permanent extra gate.

## Project Steward scope

Steward is retightened to exactly three formal checkpoints:

```text
PLAN_INIT
MILESTONE_CLOSE
PLAN_CLOSE
```

```text
PLAN_INIT_SCOPE=Portable_Plan_initialization
PLAN_INIT_PER_MILESTONE=no
PLAN_INIT_PER_WORK_ITEM=no
MILESTONE_CLOSE=integrated acceptance completed for the entire Milestone
PLAN_CLOSE=all required Milestones accepted, final reconciliation, docs aligned, final baseline
```

```text
NORMAL_WORK_ITEM_PROJECT_STEWARD_CHECKPOINT=no
PROJECT_STEWARD_PLAN_INIT=yes
PROJECT_STEWARD_MILESTONE_CLOSE=yes
PROJECT_STEWARD_PLAN_CLOSE=yes
```

Two common false equivalences:

```text
WORK_ITEM_COMPLETION != MILESTONE_CLOSE
WORK_ITEM_INTEGRATION_RECEIPT != PROJECT_STEWARD_RECONCILIATION
```

## Git / checkpoint semantics

Do not forbid commits during a Milestone. Two distinct levels:

**A. Local / mechanical checkpoint** — for rollback, diff isolation,
session continuation, Work Item isolation. Allowed local commits.

```text
NOT_ACCEPTED=yes
NOT_MILESTONE_KNOWN_GOOD=yes
GITHUB_MATERIALIZATION_REQUIRED=no
PROJECT_STEWARD_REQUIRED=no
```

**B. Accepted Milestone checkpoint** — only after integrated review
`PASS` + acceptance `PASS` + Steward reconciliation. May squash
intermediate local commits. Forms the known-good baseline for that
Milestone (commit SHA, tag, or documented reference).

```text
LOCAL_EXECUTION_CHECKPOINT != MILESTONE_KNOWN_GOOD_CHECKPOINT
GIT_CHECKPOINT_MODEL=yes
GIT_SPEC_COMMIT_SEMANTIC=yes
GIT_MILESTONE_CHECKPOINT=yes
GIT_PLAN_FINAL_BASELINE=yes
```

Completion propagation (umbrella → child) remains deterministic and
staged: child internal milestones `M1..Mn` → final closure review
`PASS` → child Issue body materialized & readback verified → parent
propagation task updates parent milestone to `completed`.

```text
CHILD_COMPLETION_AUTO_MUTATES_PARENT=no
CHILD_COMPLETION_PREREQUISITE_FOR_PARENT=yes
DETERMINISTIC_COMPLETION_PROPAGATION=yes
```

Git source history vs Forge backup remain separate:

```text
Forge managed backup != Git source history
```

## Local evidence vs GitHub

Three-layer model:

```text
GitHub Issue          — durable semantic + compact operational projection
Local Project Evidence — reusable engineering evidence
Executor Private Artifacts — raw logs / commands / temporary probes
```

GitHub must remain compact. Local evidence may retain:

- accepted Milestone decomposition, Work Item SPECs, necessary
  construction evidence, final integrated review, important test
  evidence.

It must not become a 200 KiB chronological log.

```text
GITHUB_MATERIALIZATION_MUST_BE_MORE_COMPACT_THAN_LOCAL_EVIDENCE=yes
LOCAL_EVIDENCE_IS_NOT_RAW_LOG_DUMP=yes
LOCAL_EVIDENCE_LINK_POLICY=stable_repo_relative_path_plus_checkpoint
```

Portable handoff carries only semantic context: Issue identity,
`plan_type`, current Milestone, Plan status, blocker, completed
Milestones, project context, relevant references, next action,
executor responsibility. Private session IDs / hashes / task IDs /
receipt IDs are not required.

## Transitional Friday / task-main boundary

AOTA Forge has not yet fully assumed `task-main` runtime. This core
formally declares the transitional manual orchestration model.

| Role | Responsibility |
| --- | --- |
| User | semantic authority, Milestone approval, material amendment approval |
| Friday / ChatGPT | temporary task-main semantic orchestrator |
| AOTA Reader | read-only local project / Issue observation |
| Codex | local mechanical executor + bounded reconnaissance/review |
| GitHub | durable Plan + compact managed projections |
| Git / local evidence | source history + reusable engineering evidence |
| AOTA Forge | under construction; do not claim unavailable runtime functions |

```text
FRIDAY_TEMPORARY_TASK_MAIN=yes
FRIDAY_ROLE=semantic_orchestration
CODEX_ROLE=mechanical_execution
AOTA_READER_ROLE=readonly_observation
TRANSITIONAL_CHATGPT_CONTROL_PLANE=yes
LONG_TERM_EXECUTION_OWNER=AOTA_Forge_task_main
TRANSITIONAL_MODEL_DOES_NOT_DEFINE_FINAL_RUNTIME_IMPLEMENTATION=yes
```

Friday duties: requirement convergence, Plan/Milestone planning, Work
Item decomposition, Codex feedback reconciliation, construction SPEC
generation, final review triage, repair Work Item planning, Milestone
acceptance recommendation, governance materialization planning. Friday
does not need to write source directly via terminal.

This transitional model must not be described as the permanent AOTA
Forge runtime architecture.

## Anti-bloat invariants

```text
WORK_ITEM_IS_EXECUTION_UNIT_NOT_GOVERNANCE_CEREMONY=yes
REVIEW_COUNT_MUST_BE_JUSTIFIED_BY_NEW_INFORMATION=yes
GITHUB_MATERIALIZATION_MUST_BE_MORE_COMPACT_THAN_LOCAL_EVIDENCE=yes
NO_NEW_GOVERNANCE_OBJECT_WITHOUT_NEW_SEMANTIC_AUTHORITY=yes
NORMAL_EXECUTION_DOES_NOT_CREATE_NEW_GITHUB_COMMENT=yes
BODY_IS_PLAN_NOT_EXECUTION_HISTORY=yes
NORMAL_WORK_ITEM_COMMENT_CREATION=no
ROUTINE_EXECUTION_EVENT_LOG=no
BODY_ROUTINE_EXECUTION_UPDATE=no
```

## Executor portability and worktree boundary

```text
CHANGE_EXECUTOR != CHANGE_PLAN
PORTABLE_PLAN_WORKTREE_MECHANICS_EXCLUDED=yes
WORKTREE_STATE_NOT_PORTABLE_PLAN_AUTHORITY=yes
```

Executor-neutral: switching executors preserves Issue identity, Plan,
Milestones, Work Items, acceptance, and next action. Worktree paths,
branch naming, lane lifecycle, and private runtime identifiers remain
executor-private. Parallel development model `Plan → Milestone → Work
Item → isolated executor worktree` is preserved.

For runtime worktree authority and lane lifecycle, the long-term
canonical contract is `aota-worktree-governance` (task-main owned).
The ChatGPT manual-Codex worktree policy is transitional.

## Contract invariants and stop conditions

```text
ONE_SHARED_GOVERNANCE_AUTHORITY=yes
NO_DUPLICATE_SKILL_AUTHORITY=yes
PORTABLE_PLAN_AUTHORITY=yes
ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
CONTROL_COMMENTS_MUTABLE=yes
CONTROL_COMMENTS_PLAN_AUTHORITY=no
PLAN_ISSUE_EXPECTED_MANAGED_COMMENT_COUNT=5
MANAGED_COMMENT_OVERFLOW_AUTO_CREATE=no
MANAGED_COMMENT_OVERFLOW_USER_APPROVAL_REQUIRED=yes
SPEC_EXECUTOR_SPECIFIC=yes
PROJECT_STEWARD_CHECKPOINTS=yes
ISSUE_SCOPED_DEFECT_TAXONOMY=yes
HIERARCHICAL_HYDRATION_ORDER=yes
PARALLEL_DEVELOPMENT_MODEL_PRESERVED=yes
EXECUTOR_PORTABILITY=yes
```

Stop rather than guess when: Issue body conflicts with historical
comments, canonical project identity is ambiguous, required acceptance
or review is missing, an entry tries to turn a private executor artifact
into Plan authority, duplicate `COMMENT_ROLE` exists, a bounded gate
would be added without an explicit `IDENTIFIED_RISK`, or body size
would breach the hard stop without a true boundary split.
