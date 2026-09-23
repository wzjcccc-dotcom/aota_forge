---
name: aota-portable-plan-governance
description: Shared executor-neutral semantic core for Portable Plans, the S/M/W hierarchy, Milestone-first development, approvals, review and repair, evidence, Git checkpoints, and stewardship. Authority-carrier details are adapter-specific.
---

# AOTA Portable Plan Governance - Shared Semantic Core

This is the single shared semantic core for ChatGPT/Friday planning. It owns
what a Portable Plan means, not where a particular Plan is stored. Entry
adapters may specialize representation and transport, but they must not create
a second ontology or authority model.

```text
GOVERNANCE_REVISION=chat-canonical-gov2
PORTABLE_PLAN_SEMANTICS_SINGLE_CORE=yes
PORTABLE_PLAN_AUTHORITY_CARRIER_NEUTRAL=yes
GITHUB_ISSUE_IS_PORTABLE_PLAN_ONTOLOGY=no
CHATGPT_GOVERNANCE_DUPLICATES_RUNTIME_SKILL=no
ONE_SHARED_GOVERNANCE_AUTHORITY=yes
DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no
```

## Semantic Authority Model

A Portable Plan is a semantic document. Its current authoritative carrier is
selected only by a trusted runtime `PlanAuthorityBinding` and the server-side
authority behind that binding:

```text
Portable Plan semantic document
+ trusted PlanAuthorityBinding
        -> current authority carrier
           local_governance
        or legacy github_issue compatibility
```

The binding identifies the carrier; it is not a free-form write API and it is
not authority merely because the model can see it. `plan_id`, `PLAN_NUMBER`,
folder names, `root_ref`, branch names, session identity, paths, cards and
projection files are not authority.

```text
PLAN_AUTHORITY_RESOLVED_BY_BINDING=yes
AUTHORITY_SELECTED_BY_PLAN_AUTHORITY_BINDING=yes
PLAN_AUTHORITY_BINDING_IS_AUTHORITY=no
PLAN_ID_IS_AUTHORITY=no
PLAN_NUMBER_IS_AUTHORITY=no
AUTHORITY_REF_IS_AUTHORITY=no
ROOT_REF_IS_AUTHORITY=no
SESSION_ID_IS_AUTHORITY=no
ONE_CURRENT_BOUND_PLAN_AUTHORITY_PER_PLAN=yes
BOUND_AUTHORITY_SOURCE_REQUIRED_FOR_AUTHORITATIVE_READ=yes
```

The binding contract carries exactly these semantic fields:

```text
PlanAuthorityBinding
  plan_id
  source_kind
  authority_ref
  source_revision
  source_digest
```

The normal new-Plan mapping is:

```text
SOURCE_KIND=portable_plan_local
AUTHORITY_KIND=local_governance
PLAN_AUTHORITY_BINDING_SOURCE_KIND=local_governance
LOCAL_GOVERNANCE_NORMAL_SUPPORTED=yes
```

`SOURCE_KIND=portable_plan_local` describes the normalized Portable Plan
document. The runtime binding uses `source_kind=local_governance` to select
the local authority adapter. The logical `authority_ref` is preferred in Chat
reasoning, for example `local-governance:<PROJECT_ID>/<PLAN_ID>`. Trusted
composition resolves that reference to the project-scoped governance root and
`<plan_id>/plan.md`; ChatGPT must not mint or teach a host path as authority.

The legacy mapping is retained only for an already-bound Gov1 Plan:

```text
LEGACY_GITHUB_ISSUE_COMPAT_SUPPORTED=yes
LEGACY_GITHUB_BINDING_SOURCE_KIND=github_issue
GOV1_COMPATIBILITY_PRESERVED=yes
GOV1_IS_NORMAL_NEW_PLAN_PATH=no
```

## Portable Plan Semantics

The shared core owns the following semantics independently of carrier:

- Plan objective, context, scope, non-goals, requirements and constraints.
- Plan authority boundary, dependencies, risks and validation expectations.
- S/M/W hierarchy and identifier meaning.
- Milestone-first development and human approval gates.
- Work Item bounded execution and cheap early validation.
- Integrated review, batch repair and acceptance.
- Steward checkpoints and deterministic-first stewardship.
- Git/source checkpoints and reusable evidence principles.
- Material Plan amendment rules and readback expectations.

It does not require an Issue body, a comment ID, a comment ordinal, a local
file named after a comment role, or any particular remote provider.

## Plan Hierarchy

```text
SUBPLAN_IDENTIFIER=S<n>
MILESTONE_IDENTIFIER=M<n>
WORK_ITEM_IDENTIFIER=W<n>
FORMAL_GOVERNANCE_DEPTH=S_M_W
NEW_PLAN_W_AS_WORK_ITEM=yes
LEGACY_W_AS_WORKSTREAM_READ_COMPATIBLE=yes
NEW_PLAN_WORKSTREAM_IDENTIFIER_DEPRECATED=yes
```

An umbrella Plan may coordinate child Plans through `S<n>`. A standalone Plan
uses `M<n>/W<n>`. A Work Item is the formal execution unit. Executor-local
tasks below a Work Item do not enter the Plan hierarchy and do not create Plan
authority.

```text
TASK_BELOW_WORK_ITEM_EXECUTOR_LOCAL=yes
NORMAL_WORK_ITEM_SEPARATE_ISSUE=no
PARENT_OWNS_CROSS_SUBPLAN_GOVERNANCE=yes
CHILD_OWNS_INTERNAL_SCOPE_AND_MILESTONES=yes
PARENT_MIRRORS_CHILD_EXECUTION_DETAILS=no
IDENTIFIERS_NOT_INTERCHANGEABLE=yes
```

Parent semantics own program objective, cross-Plan dependencies, child Plan
mapping and coordination. A child owns its own objective, scope, Milestones,
Work Items, acceptance and closure. Existing Gov1 documents using the former
Workstream meaning remain readable as compatibility input; new Plans use
Work Item semantics.

## Minimum Semantic Contract

A complete Portable Plan can express:

```text
plan_type
status
objective
background/context
scope
non-goals
requirements
constraints
authority model
subplans, when applicable
milestones
acceptance criteria
risks
decisions
dependencies
validation expectations
```

Each Milestone records its semantic name, objective, scope, expected Work
Items, acceptance criteria, evidence expectations, stop conditions,
dependencies and lifecycle status. Carrier identifiers such as issue numbers,
comment IDs, session IDs, receipt IDs and host paths are not required for the
Portable Plan to remain understandable.

## Milestone-First Development

Normal planning and construction follows this semantic order:

```text
Milestone entry
-> source/current-state reconnaissance
-> Milestone plan and W1..Wn decomposition
-> bounded Codex feasibility input
-> Friday reconciliation
-> explicit user Milestone approval
-> Work Item construction and cheap validation
-> one integrated Milestone review
-> repair batch when needed
-> acceptance and Steward checkpoint
-> next Milestone
```

```text
MILESTONE_FIRST=yes
USER_MILESTONE_APPROVAL_REQUIRED=yes
WORK_ITEM_USER_APPROVAL_DEFAULT=no
CODEX_RECOMMENDATION_IS_ADVISORY=yes
FRIDAY_RECONCILIATION_REQUIRED=yes
BLINDLY_COPY_REVIEWER_RECOMMENDATION=no
```

Plan materialization approval is not Milestone construction approval. A new
Plan is read back and then planning stops before source construction until the
relevant Milestone approval is explicit.

## Work Items and Review

A Work Item is an execution unit, not a governance ceremony:

```text
W -> bounded SPEC -> construction -> cheap validation -> source-ready
```

```text
WORK_ITEM_IS_EXECUTION_UNIT_NOT_GOVERNANCE_CEREMONY=yes
NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=no
NORMAL_WORK_ITEM_EXTRA_REVIEW=no
CHEAP_VALIDATION_EARLY=yes
EXPENSIVE_REVIEW_LATE=yes
```

Once all required Work Items are source-ready, perform one integrated
Milestone review (`RV1`). Friday/task-main triages findings as a batch,
removes false positives and out-of-scope suggestions, creates bounded repair
Work Items for real defects, and runs `RV2` only when new information justifies
it.

```text
NORMAL_MILESTONE_FORMAL_REVIEW_COUNT=1
REVIEW_BATCH_ORIENTED=yes
FINDING_BY_FINDING_REVIEW_LOOP_DEFAULT=no
REVIEW_COUNT_MUST_BE_JUSTIFIED_BY_NEW_INFORMATION=yes
```

## Authority-Aware Hydration

Hydration is selected by the current authority binding. It is not a universal
GitHub-comment algorithm:

```text
CARD_FIRST=yes
KNOWN_CONTEXT_REUSE_FIRST=yes
FULL_PLAN_EAGER_HYDRATION=no
EXPLICIT_REF_BEFORE_DEEP_HYDRATION=yes
HYDRATE_ON_DEMAND=yes
AUTHORITY_KIND_DRIVES_ADAPTER_SPECIFIC_HYDRATION=yes
```

Normal local Governance 2.0 routing is:

```text
known Cards / ContextRoute
-> PlanCard
-> current MilestoneCard
-> explicit relevant authority refs
-> bounded plan.md sections as needed
-> generated MAP.md / STATUS.md when navigation or compact status helps
-> local evidence on demand
```

Cards and ContextRoute are derived navigation, never authority. A local
`plan.md` read requires the current bound `local_governance` source and its
revision/digest evidence. A legacy `github_issue` binding may route to bounded
Issue-body and managed-comment compatibility hydration, but those projections
must not be used for a local Plan.

## Project and Generated Views

Project identity is established by the trusted source `.aota/project.yaml`
and Project Registry. The source manifest does not own active Plan state,
session state, root capability or execution state.

```text
PROJECT_MANIFEST_OWNS_PLAN_STATE=no
PROJECT_REGISTRY_OWNS_TRUSTED_PHYSICAL_REGISTRATION=yes
ROOT_REF_IS_PATH=no
AUTHORIZED_ROOT_SET_IS_DERIVED_FROM_TRUSTED_BINDINGS=yes
GOVERNANCE_ROOT_AGENTS_DUPLICATE=no
```

The runtime-generated files are projections only:

```text
MAP.md=generated Project navigation projection
STATUS.md=generated Project progress projection
MAP_GENERATED=yes
STATUS_GENERATED=yes
MAP_AUTHORITY=no
STATUS_AUTHORITY=no
MAP_STATUS_MANUAL_MAINTENANCE=no
```

When these views are stale, use the canonical Project/Plan reconcile or
projection lifecycle. ChatGPT must not prepare a prompt for manual MAP/STATUS
maintenance and must not teach a user to create
`/workspace/plans/<project>/AGENTS.md` as a Gov2 file. Source/worktree
`AGENTS.md` remains Project policy; AF owns applicability and discovery/ref
projection.

## Project Stewardship

Project Stewardship is an event-driven subsystem boundary, not a normal Work
Item worker. Deterministic checks run first; a semantic Steward is used only
for a residual that the deterministic engine cannot close.

```text
PROJECT_STEWARD_IS_NORMAL_WORKER=no
PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN=yes
PROJECT_STEWARDSHIP_IS_EVENT_DRIVEN=yes
DETERMINISTIC_FIRST_STEWARDSHIP=yes
SEMANTIC_STEWARD_ONLY_FOR_RESIDUAL=yes
```

Normal instructions must not assign the Project Steward to routine MAP/STATUS
updates, notes maintenance, Plan Init bookkeeping or ordinary Work Item
completion. The compatible Steward checkpoints are bounded to Plan Init,
integrated Milestone close and Plan close; they do not make the Steward the
acceptance or user-approval authority.

```text
PLAN_INIT_PER_MILESTONE=no
PLAN_INIT_PER_WORK_ITEM=no
NORMAL_WORK_ITEM_PROJECT_STEWARD_CHECKPOINT=no
```

## Amendments and Checkpoints

A material semantic amendment changes objective, scope, non-goals,
acceptance, authority boundaries, canonical model, security/privacy contract,
schema semantics or material dependencies/DAG. It is a current-state update,
not routine execution history.

```text
PLAN_SEMANTIC_AMENDMENT_REQUIRES_APPROVAL_WHEN_GOVERNANCE_REQUIRES=yes
PLAN_AMENDMENT_IS_CURRENT_STATE_NOT_EXECUTION_HISTORY=yes
CAS_OR_CURRENT_REVISION_REQUIRED_FOR_MUTATION=yes
READBACK_REQUIRED_AFTER_MUTATION=yes
```

The adapter for the current binding performs the amendment. For local Gov2,
that means `governance.plan(mode=amend)` with a fresh revision/authority
precondition and semantic decision reference, followed by digest/revision
readback and projection reconciliation. For legacy Gov1 only, the GitHub
adapter may mutate the Issue body under its own CAS contract.

Local Git commits can be mechanical rollback or integration checkpoints. They
are not automatically accepted Milestone checkpoints. An accepted Milestone
checkpoint requires integrated review, acceptance and Steward reconciliation.

```text
LOCAL_EXECUTION_CHECKPOINT != MILESTONE_KNOWN_GOOD_CHECKPOINT
GIT_CHECKPOINT_MODEL=yes
GITHUB_MATERIALIZATION_REQUIRED=no
```

## Legacy Gov1 Representation Boundary

The following representation is retained only when the current trusted
binding explicitly has `authority_kind=github_issue`. It is not the Portable
Plan ontology and it is not the normal new-Plan path.

```text
LEGACY_GOV1_ADAPTER_REPRESENTATION=yes
GITHUB_5_COMMENT_MODEL_ADAPTER_SPECIFIC=yes
GITHUB_5_COMMENT_MODEL_GOV2_REQUIRED=no
LOCAL_GOV2_CREATES_FIVE_COMMENT_FILES=no
COMMENT_PLAN_AUTHORITY=no
GITHUB_ISSUE_NORMAL_NEW_PLAN_PATH=no
```

For that explicit legacy branch only:

```text
ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
ISSUE_BODY_ROLE=portable_plan_normative_contract
PLAN_ISSUE_EXPECTED_PRIMARY_MANAGED_COMMENT_COUNT=5
ONE_PRIMARY_MANAGED_COMMENT_PER_ROLE=yes
```

The five adapter-specific roles may remain readable:

```text
milestone_progress_index
development_notes
defect_register
plan_appendix
decision_change_log
```

They are compact projections/support/history and carry no Plan authority.
Comment IDs and ordinal positions belong to this adapter only. A local Gov2
Plan does not translate these roles into five local files, and local lifecycle
operations do not create comments. A legacy body amendment is valid only when
the current binding is explicitly `github_issue`; it must never be taught as
the local amendment workflow.

```text
GITHUB_5_COMMENT_MODEL_LEGACY_ONLY=yes
LOCAL_GOV2_COMMENT_IDS_REQUIRED=no
LEGACY_ISSUE_BODY_MUTATION_ONLY_WHEN_BOUND=yes
```

## ChatGPT, AOTA Reader and Codex Boundary

ChatGPT/Friday remains the semantic orchestrator: it interprets intent,
proposes semantics, asks for approval, chooses bounded retrieval, reconciles
evidence and decides when to stop. It does not become a generic filesystem,
SQLite, journal or GitHub writer.

```text
FRIDAY_TEMPORARY_TASK_MAIN=yes
FRIDAY_ROLE=semantic_orchestration
CHATGPT_DIRECT_GENERIC_GOVERNANCE_WRITE=no
CHATGPT_DIRECT_GOV2_GENERIC_WRITE=no
CONSTRUCTION_PROMPT_BOUND_TO_CURRENT_PLAN_AUTHORITY=yes
CODEX_ROLE=mechanical_execution
CODEX_USES_CANONICAL_AF_LIFECYCLE=yes
SECOND_CODEX_PLAN_INIT_IMPLEMENTATION=no
SECOND_CODEX_PROJECT_INIT_IMPLEMENTATION=no
AOTA_READER_ROLE=readonly_observation
AOTA_READER_GOVERNANCE_WRITE=no
```

Until a governed ChatGPT writer is actually available, the bounded
transitional path is:

```text
ChatGPT / Friday semantic decision
-> construction or mutation instruction
-> Codex trusted mechanical ingress
-> same canonical AF lifecycle service
-> governance.project / governance.plan
-> result readback and reconcile
```

Codex must not manually create a Plan directory, write `plan.md`, edit
`governance.sqlite3`, append the journal, or maintain MAP/STATUS when the
canonical lifecycle is available. This is a mechanical bridge, not a second
Project/Plan implementation.

## Stop Conditions

Stop and request input rather than guessing when Project/Plan identity is
ambiguous, the current authority binding is absent or stale, a duplicate or
adjacent Plan cannot be reconciled, required approval is missing, the runtime
returns an unknown durable outcome, or the result cannot be verified by
readback. `AUTHORITY_DENIED` is a boundary, not a reason to try another
transport.

```text
AUTHORITY_DENIED_IS_FAIL_CLOSED=yes
UNKNOWN_DURABLE_OUTCOME_IS_NOT_SUCCESS=yes
NO_SILENT_DUAL_AUTHORITY=yes
NO_SECOND_PLAN_MUTATION_PROTOCOL=yes
```
