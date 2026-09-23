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

If this map conflicts with an implementation contract, the trusted runtime
descriptor and result contract win; do not document an unavailable mode or
operation here.
