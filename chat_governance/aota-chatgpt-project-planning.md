---
name: aota-chatgpt-project-planning
description: ChatGPT/Friday planning entry adapter for semantic Project and Plan proposals, trusted Governance 2.0 lifecycle handoff, authority-aware readback, and the explicit construction-prompt gate.
---

# AOTA ChatGPT Project Planning - Gov2 Entry Adapter

This is the canonical ChatGPT/Friday planning entry. It is an adapter, not a
second runtime Skill or mutation implementation. Read the index and the
carrier-neutral shared core first.

```text
SHARED_CORE=aota-portable-plan-governance.md
CHATGPT_ENTRY_DUPLICATES_SHARED_CORE=no
CHATGPT_GOVERNANCE_DUPLICATES_RUNTIME_SKILL=no
NORMAL_CHATGPT_PLAN_AUTHORITY=local_governance
NEW_CHATGPT_PLAN_DEFAULT_AUTHORITY=local_governance
NEW_CHATGPT_PLAN_REQUIRES_GITHUB_ISSUE=no
CHATGPT_DIRECT_GOV2_GENERIC_WRITE=no
PLAN_AUTHORITY_RESOLVED_BY_BINDING=yes
PLAN_AUTHORITY_BINDING_MODEL=plan_id + source_kind + authority_ref + source_revision + source_digest
```

## Runtime Contract Boundary

The actual runtime exposes one public entry:

```text
MCP_PUBLIC_TOOL_COUNT=1
MCP_PUBLIC_TOOL=aota.invoke
```

The accepted Governance 2.0 operation families are:

```text
governance.project: init | inspect | reconcile
governance.plan: init | inspect | amend | close
```

These modes are the runtime contract currently implemented by W2/W4. Exact
inputs, required context, typed errors and result mechanics come from
`help(operation=...)` and the canonical descriptors; this adapter does not
copy a second operation schema.

```text
PROJECT_GOVERNANCE_SKILL=aota-project-governance@1.0.0
PLAN_GOVERNANCE_SKILL=aota-plan-governance@1.0.0
CHATGPT_DIRECT_GENERIC_GOVERNANCE_WRITE=no
```

`governance.project` requires trusted intake context and approval/decision
handling on the runtime side. Its result is read back before continuing and
contains Project identity evidence plus generated projection, ContextRoute and
Card proof. `governance.plan` likewise requires trusted Project context and
returns durable status, Plan identity, authority/projection references and
revision evidence. A result that is not verified is not a successful
materialization.

## Friday's Normal Planning Loop

For every new request, Friday/ChatGPT remains the semantic orchestrator:

```text
read canonical Chat governance
-> resolve/read current Project and Plan context
-> card-first, bounded hydration where available
-> AOTA Reader read-only source/current-state inspection
-> duplicate/adjacent Plan reconciliation
-> semantic Project/Plan proposal
-> user review and approval when required
-> canonical AF lifecycle operation
-> local authority readback/reconcile
-> stop before source construction until Milestone approval
```

Do not infer current state from Chat history, an uploaded snapshot, a folder,
an Issue number, a session name or a branch name. Use logical Project/Plan
references and the current trusted binding.

```text
CARD_FIRST=yes
FULL_PLAN_EAGER_HYDRATION=no
AUTHORITY_KIND_DRIVES_ADAPTER_SPECIFIC_HYDRATION=yes
AOTA_READER_ROLE=readonly_observation
AOTA_READER_GOVERNANCE_WRITE=no
USER_MILESTONE_APPROVAL_REQUIRED=yes
```

## New Project Workflow

For a request such as `建立/納管一個 Project`:

1. Determine the semantic Project intent and canonical `PROJECT_ID`.
2. Inspect whether that Project is already registered through the trusted
   Project governance context. Ask when identity or repository scope is
   ambiguous.
3. Gather only the required semantic facts: name, kind, summary and bounded
   capabilities when needed. Do not ask the user to construct `project.yaml`
   manually as the normal workflow.
4. Present the material durable Project decision and obtain the required user
   approval.
5. Invoke `aota.invoke(operation="governance.project", arguments={"mode":
   "init", "project_id": PROJECT_ID, ...})` through the canonical AF
   lifecycle. If no governed ChatGPT writer is available, Codex is only the
   mechanical ingress to this same operation.
6. Read the result and verify `PROJECT_ID`, Project Registry evidence, trusted
   Project scope, generated `MAP.md`/`STATUS.md` references and available
   Project/ContextRoute proof.

If the Project already exists, use `inspect` and, only for verified drift,
`reconcile`; do not re-run `init` to repair or refresh it.

```text
CHATGPT_PROJECT_INIT_CONTRACT_ALIGNED=yes
PROJECT_INIT_USES_CANONICAL_AF_LIFECYCLE=yes
PROJECT_INIT_MANUAL_PROJECT_YAML=no
PROJECT_INIT_WORKFLOW=inspect -> approval -> governance.project(init) -> readback -> verify
MAP_STATUS_MANUAL_MAINTENANCE=no
```

The source `.aota/project.yaml` remains a bounded Project identity manifest.
It does not become Plan, session or root-capability authority.

## New Plan Workflow

For a new bounded initiative:

1. Inspect the trusted Project governance and active Plan projection.
2. Reconcile duplicate or adjacent Plan intent before proposing another Plan.
   An existing matching Plan is inspected or amended; it is not duplicated.
3. Produce a semantic Plan proposal containing, at minimum:
   `PLAN_NUMBER` as display/provenance only, canonical `PLAN_ID`, title, goal,
   scope, non-goals, Milestones/DAG, acceptance, validation, and authority
   intent.
4. Establish the intended binding explicitly:

   ```text
   SOURCE_KIND=portable_plan_local
   AUTHORITY_KIND=local_governance
   ```

   The binding fields are `plan_id`, `source_kind`, `authority_ref`,
   `source_revision` and `source_digest`. `PLAN_NUMBER`, folder name,
   `root_ref` and session identity do not select authority.
5. Present the proposal for explicit user approval. Plan approval authorizes
   Plan materialization only; it does not authorize source construction.
6. Invoke `aota.invoke(operation="governance.plan", arguments={"mode":
   "init", "project_id": PROJECT_ID, "plan_id": PLAN_ID,
   "candidate_body": COMPLETE_PORTABLE_PLAN, ...})`.
7. Read back the durable result and verify the local binding, logical
   authority reference, source revision/digest, `plan.md` authority,
   ProjectPlanRecord, journal/CAS evidence, generated projections and Card /
   ContextRoute proof.
8. Stop. Continue to Milestone planning, and source construction only after
   the relevant explicit Milestone approval.

```text
CHATGPT_PLAN_INIT_CONTRACT_ALIGNED=yes
NEW_CHATGPT_PLAN_DEFAULT_AUTHORITY=local_governance
NEW_CHATGPT_PLAN_REQUIRES_GITHUB_ISSUE=no
PLAN_AUTHORITY_BINDING_MODEL=plan_id + source_kind + authority_ref + source_revision + source_digest
PLAN_AUTHORITY_BINDING_REQUIRED=yes
LOCAL_GOV2_PLAN_INIT_WORKFLOW=proposal -> approval -> governance.plan(init) -> readback -> stop before construction
```

The normal local semantic authority resolves through the trusted project
governance root to `<plan_id>/plan.md`. ChatGPT reasons with the logical
`authority_ref`, not a host path. It does not translate the legacy five-role
comment model into local files.

## Plan Amendment Workflow

For a material semantic delta on a normal local Plan:

```text
read current Plan authority and binding
-> propose semantic delta
-> obtain user approval when required by governance
-> invoke governance.plan(mode=amend)
-> CAS/readback verification
-> verify new source revision and digest
-> reconcile generated views and Cards
```

Use the fresh `expected_plan_revision` and semantic decision/authorization
references required by the current descriptor/help contract. The candidate is
the complete semantic Plan document expected by the local lifecycle, not a
manual patch to a file. Stop on stale revision, stale authority, ambiguous
identity, invalid candidate or unknown durable outcome; do not blind-retry.

```text
CHATGPT_PLAN_AMEND_CONTRACT_ALIGNED=yes
LOCAL_GOV2_PLAN_AMEND_WORKFLOW=read -> semantic delta -> approval -> governance.plan(amend) -> CAS/readback -> projection reconcile
PLAN_AMENDMENT_ISSUE_BODY_MUTATION=no
PLAN_AUTHORITY_RESOLVED_BY_BINDING=yes
```

Only when the current trusted binding explicitly has
`authority_kind=github_issue` may the legacy adapter use its Issue-body CAS
amendment. That is not a fallback for local Gov2.

## Authority-Aware Inspection and Hydration

Start with known compact navigation and hydrate only what the question needs:

```text
known Cards / ContextRoute
-> PlanCard
-> current MilestoneCard
-> explicit relevant authority ref
-> bounded plan.md section
-> MAP.md / STATUS.md when useful
-> evidence on demand
```

```text
CARD_FIRST=yes
KNOWN_CONTEXT_REUSE_FIRST=yes
FULL_PLAN_EAGER_HYDRATION=no
AUTHORITY_KIND_DRIVES_ADAPTER_SPECIFIC_HYDRATION=yes
```

For `local_governance`, read the bound local Plan authority and its current
revision/digest. For explicit `github_issue` compatibility, use bounded Gov1
hydration including legacy managed comments when the adapter requires them.
Do not make GitHub five-comment hydration universal.

`MAP.md` is generated Project navigation and `STATUS.md` is generated Project
progress. Both are useful projections, never authority. When stale, call the
canonical Project/Plan reconcile or projection lifecycle through the trusted
AF path; never prepare a manual maintenance prompt.

## Construction Prompt Gate

Friday produces a complete Codex construction prompt only when the user
explicitly asks, for example:

```text
給我施工提示詞
```

Without that intent, stay in semantic planning, reconciliation or review. A
construction prompt must be bound to the current local Plan authority,
explicit Milestone/Work Item scope, acceptance and required proof. It must
carry the logical `PROJECT_ID`, `PLAN_ID`, current authority revision/digest
evidence and the exact authority refs needed for the work; it must not assume
that a GitHub Issue body is current truth.

```text
USER_MILESTONE_APPROVAL_REQUIRED=yes
CONSTRUCTION_PROMPT_BOUND_TO_CURRENT_PLAN_AUTHORITY=yes
CONSTRUCTION_PROMPT_REQUIRES_EXPLICIT_USER_REQUEST=yes
```

### AF-native thin execution gate

A construction prompt is a bounded mechanical relay package, not a second
scheduler or execution authority. AF-native task-main owns the normal Work
execution after the semantic planning and approval gate; Codex only relays the
approved package mechanically.

```text
TASK_MAIN_OWNS_EXECUTION=yes
AF_NATIVE_EXECUTION_OWNER=task-main
CONSTRUCTION_PROMPT_AF_NATIVE_EXECUTION_OWNER=task-main
CODEX_MECHANICAL_RELAY_ONLY=yes
DAILY_TASK_MAIN_LAUNCHER_ROLE=headless_operator_bootstrap_adapter
DAILY_TASK_MAIN_LAUNCHER_EXCLUSIVE_AUTHORITY=no

AF_NATIVE_THIN_EXECUTION_PATH=role.bootstrap -> task_main.context(bind) -> handoff.write -> task.start -> task.return -> runtime-owned parent reconciliation -> exact-parent CARD
ACTIVATE_MILESTONE_NORMAL_READINESS_REQUIRED=no
ADVANCE_ONCE_NORMAL_READINESS_REQUIRED=no
TRUSTED_TASK_MAIN_RUNTIME_CONTEXT_NON_NULL_NORMAL_READINESS_REQUIRED=no
TRUSTED_TASK_MAIN_CONTEXT_NONE_IS_THIN_EXECUTION_BLOCKER=no

AOTA_FORGE_UNBOUND_ROLE=worktree_runtime_label_only
AOTA_FORGE_UNBOUND_IS_AUTHORITY=no
AOTA_FORGE_UNBOUND_IS_READINESS=no
WORKSPACE_WRITE_SEMANTICS=create_or_bounded_whole_file_replacement
WORKSPACE_EDIT_SEMANTICS=coder_only_exact_local_CAS_edit
WORKSPACE_WRITE_EDIT_SUBSTITUTE_FOR_THIN_EXECUTION=no
```

The AF-native thin path is exactly `role.bootstrap -> task_main.context(bind)
-> handoff.write -> task.start -> task.return -> runtime-owned parent
reconciliation -> exact-parent CARD`. `activate_milestone`, `advance_once`, and
non-null `TrustedTaskMainRuntimeContext` are not normal readiness requirements;
`trusted_task_main_context=None` is not a thin execution blocker. The
`DailyTaskMainLauncher` is a headless/operator bootstrap adapter, not exclusive
authority. `aota_forge-unbound` is only a worktree/runtime label. AF-native
task-main owns Work selection, handoff, Worker role, start, reconciliation,
retry/repair, and DAG decisions.

`workspace.write` means create or bounded whole-file replacement. `workspace.edit`
means a coder-only exact local CAS edit. Neither operation substitutes for the
AF-native execution path.

Codex is mechanical execution only. It must use the same canonical AF
lifecycle for Project/Plan governance and must not implement a second
`project.init`/`plan.init` path, create directories by hand, edit the
governance store, append the journal, or maintain generated views manually.

```text
CODEX_ROLE=mechanical_execution
CODEX_USES_CANONICAL_AF_LIFECYCLE=yes
SECOND_CODEX_PLAN_INIT_IMPLEMENTATION=no
SECOND_CODEX_PROJECT_INIT_IMPLEMENTATION=no
```

## Stewardship and AGENTS Boundary

Project Stewardship is a deterministic-first subsystem, not routine ChatGPT
work assignment. Do not assign a Project Steward to routine MAP/STATUS
updates, notes, Plan Init bookkeeping or Work Item completion.

```text
PROJECT_STEWARD_IS_NORMAL_WORKER=no
PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN=yes
DETERMINISTIC_FIRST_STEWARDSHIP=yes
SEMANTIC_STEWARD_ONLY_FOR_RESIDUAL=yes
```

Source/worktree `AGENTS.md` is Project policy. AF discovers and projects its
applicability. Do not create a governance-root duplicate
`/workspace/plans/<project>/AGENTS.md` as part of normal Gov2 planning.

## Explicit Legacy Route

`aota-github-issue-planning.md` is a compatibility alias, not this entry's
normal implementation. If the current binding is explicitly `github_issue`,
route through the bounded Gov1 read-before-write adapter and preserve its
five managed roles. Otherwise route every new Project/Plan request here and
the local Gov2 lifecycle.

```text
LEGACY_GITHUB_ENTRY_ALIAS=yes
GOV1_COMPATIBILITY_PRESERVED=yes
GOV1_IS_NORMAL_NEW_PLAN_PATH=no
GITHUB_5_COMMENT_MODEL_ADAPTER_SPECIFIC=yes
LOCAL_GOV2_REQUIRES_FIVE_COMMENT_FILES=no
GITHUB_5_COMMENT_MODEL_ROLE=legacy_adapter_only
```

## Validation Planning

Use `aota-chatgpt-validation-governance.md` for Change Surface, risk/process
depth, V1/V2/V3/V4 proof selection and evidence non-escalation. Validation
semantics are independent of whether the current Plan carrier is local or
legacy GitHub. This adapter does not redefine them.

Stop rather than infer when a binding, approval, current revision, source
fact, or runtime result is missing. `AOTA Reader` is observation only and
cannot replace the canonical lifecycle writer.
