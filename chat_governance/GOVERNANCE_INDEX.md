# AOTA Forge - Chat Governance Canonical Index

This index is the only map of the canonical ChatGPT/Friday governance
adapters. It does not duplicate their semantic rules. The shared Portable
Plan core is carrier-neutral; the runtime and a trusted authority binding
select the current carrier.

```text
CANONICAL_CHAT_GOVERNANCE_ROOT=chat_governance

SHARED_CORE=aota-portable-plan-governance.md
CHATGPT_PLANNING_ENTRY=aota-chatgpt-project-planning.md
CHATGPT_VALIDATION_GOVERNANCE=aota-chatgpt-validation-governance.md
PARALLEL_DEVELOPMENT_ADAPTER=aota-chatgpt-parallel-development.md
WORKTREE_MANUAL_ADAPTER=aota-chatgpt-worktree-governance.md
LEGACY_GITHUB_ENTRY_ALIAS=aota-github-issue-planning.md

NORMAL_CHATGPT_PLAN_AUTHORITY_MODEL=local_governance
LEGACY_GITHUB_IS_NORMAL_AUTHORITY=no
PORTABLE_PLAN_SEMANTICS_SINGLE_CORE=yes
PORTABLE_PLAN_AUTHORITY_CARRIER_NEUTRAL=yes
GOVERNANCE_INDEX_IS_CANONICAL_MAP=yes
```

## Authority Map

| Role | Canonical file | Boundary |
| --- | --- | --- |
| Shared semantic core | `aota-portable-plan-governance.md` | Portable Plan, Milestone, Work Item, approval, review, evidence, and stewardship semantics |
| ChatGPT planning entry | `aota-chatgpt-project-planning.md` | Project/Plan proposal, approval, lifecycle handoff, readback, and construction gate |
| Validation planning | `aota-chatgpt-validation-governance.md` | Change Surface, risk/process depth, and proof planning only |
| Parallel development adapter | `aota-chatgpt-parallel-development.md` | Work Item scheduling inside an approved Milestone |
| Manual worktree adapter | `aota-chatgpt-worktree-governance.md` | Transitional mechanical Codex worktree handoff |
| Legacy GitHub entry alias | `aota-github-issue-planning.md` | Explicit Gov1 compatibility routing only |

All adapters reference the shared core and must not create a second Plan
ontology, authority model, mutation protocol, or runtime Skill contract.

```text
ONE_SHARED_GOVERNANCE_AUTHORITY=yes
DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no
CHATGPT_GOVERNANCE_DUPLICATES_RUNTIME_SKILL=no
```

## Current Truth Resolution

ChatGPT/Friday reads this index and the shared core from the local repository,
then reads the current Project/Plan authority through the canonical AF
lifecycle. A ChatGPT Project upload is only a snapshot/bootstrap/reference.
It is never current authority.

For a normal new Plan:

```text
Portable Plan semantic document
+ trusted PlanAuthorityBinding
        -> authority_kind=local_governance
        -> local Governance 2.0 lifecycle
```

For an explicitly legacy Plan:

```text
Portable Plan semantic document
+ trusted PlanAuthorityBinding
        -> authority_kind=github_issue
        -> bounded Gov1 compatibility adapter
```

The legacy alias may describe GitHub Issue bodies and five managed comments,
but that representation is not the normal entry and does not create local
comment files.

```text
LOCAL_CANONICAL_LIVE_READ_PREFERRED=yes
NORMAL_NEW_PLAN_ENTRY=aota-chatgpt-project-planning.md
LEGACY_GITHUB_ENTRY_ALIAS=yes
NORMAL_NEW_PLAN_REQUIRES_GITHUB_ISSUE=no
```

## Runtime Boundary

Chat governance describes the adapter boundary, not a replacement runtime.
The canonical runtime contract is obtained from `role.bootstrap`, progressive
Skills, and `help(operation=...)`. The normal AF ingress remains:

```text
CHATGPT / FRIDAY semantic reasoning
        -> user review and approval
        -> Codex mechanical ingress when a governed ChatGPT writer is absent
        -> aota.invoke
        -> governance.project / governance.plan
        -> local authority readback and reconcile
```

```text
MCP_PUBLIC_TOOL_COUNT=1
MCP_PUBLIC_TOOL=aota.invoke
CHATGPT_DIRECT_GENERIC_GOVERNANCE_WRITE=no
AOTA_READER_ROLE=readonly_observation
AOTA_READER_GOVERNANCE_WRITE=no
```

## W5 Thin Execution Boundary

ChatGPT/Friday owns semantic planning, user review, and supervision. It does
not become the execution scheduler. The normal path is AF-native thin
execution owned by task-main; Codex is only a mechanical relay, and the
headless launcher is only an operator/bootstrap adapter.

```text
AF_NATIVE_THIN_EXECUTION=yes
AF_NATIVE_EXECUTION_OWNER=task-main
CHATGPT_FRIDAY_ROLE=planning_supervision
CHATGPT_FRIDAY_EXECUTION_AUTHORITY=no
CODEX_IS_MECHANICAL_RELAY_ONLY=yes
DAILY_TASK_MAIN_LAUNCHER_ROLE=headless_operator_bootstrap_adapter
DAILY_TASK_MAIN_LAUNCHER_EXCLUSIVE_AUTHORITY=no

AF_NATIVE_THIN_EXECUTION_PATH=role.bootstrap -> task_main.context(bind) -> handoff.write -> task.start -> task.return -> runtime-owned parent reconciliation -> exact-parent CARD
TASK_MAIN_OWNS_WORK_SELECTION=yes
TASK_MAIN_OWNS_HANDOFF=yes
TASK_MAIN_OWNS_WORKER_ROLE=yes
TASK_MAIN_OWNS_TASK_START=yes
TASK_MAIN_OWNS_PARENT_RECONCILIATION=yes
TASK_MAIN_OWNS_RETRY_REPAIR=yes
TASK_MAIN_OWNS_DAG_DECISIONS=yes

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

The exact AF-native path is:

```text
role.bootstrap -> task_main.context(bind) -> handoff.write -> task.start -> task.return -> runtime-owned parent reconciliation -> exact-parent CARD
```

`activate_milestone`, `advance_once`, and a non-null
`TrustedTaskMainRuntimeContext` are not normal thin-path readiness
requirements. `trusted_task_main_context=None` is not a thin execution
blocker. `aota_forge-unbound` is only a worktree/runtime label, never an
authority, readiness gate, scheduler, or Work selector. AF-native task-main
owns Work selection, handoff, Worker role, start, reconciliation, retry/repair,
and DAG decisions.

`workspace.write` is create-or-bounded-whole-file replacement. `workspace.edit`
is a coder-only exact local CAS edit. Neither workspace operation substitutes
for AF-native task-main execution.

If this map conflicts with an implementation contract, the trusted runtime
descriptor and result contract win; do not document an unavailable mode or
operation here.
