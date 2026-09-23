---
name: aota-github-issue-planning
description: Explicit legacy Governance 1.x GitHub planning compatibility alias. It routes normal new planning to the ChatGPT Project planning entry and never creates an independent authority.
---

# AOTA GitHub Issue Planning - Legacy Compatibility Alias

This file remains so historical references to the former GitHub planning entry
stay readable. It is a bounded adapter alias, not a second governance core.

```text
LEGACY_COMPATIBILITY_ALIAS_ONLY=yes
LEGACY_GITHUB_ENTRY_ALIAS=yes
NORMAL_NEW_PLAN_ENTRY=no
NORMAL_CHATGPT_PLAN_AUTHORITY_MODEL=local_governance
GITHUB_ISSUE_NORMAL_NEW_PLAN_PATH=no
GOV1_COMPATIBILITY_PRESERVED=yes
GITHUB_ALIAS_DUPLICATES_CORE=no
```

## Routing

Resolve Chat governance in this order:

1. `GOVERNANCE_INDEX.md` for the canonical map.
2. `aota-portable-plan-governance.md` for the shared semantic core and
   authority-carrier rules.
3. `aota-chatgpt-project-planning.md` for all normal new Project/Plan
   proposals and local Governance 2.0 materialization.

If the current trusted `PlanAuthorityBinding` explicitly has
`source_kind=github_issue`, this alias may route the existing Plan through the
bounded Gov1 compatibility adapter. The binding must already select that
carrier; an Issue number, folder, session or this alias does not grant
authority.

```text
PLAN_ID_IS_AUTHORITY=no
AUTHORITY_SELECTED_BY_PLAN_AUTHORITY_BINDING=yes
LEGACY_GITHUB_BINDING_SOURCE_KIND=github_issue
```

## Normal New Planning Is Not GitHub-First

Do not instruct a user to create a GitHub Issue for every new Plan. Normal
planning is:

```text
semantic Project/Plan proposal
-> user approval
-> governance.project / governance.plan through aota.invoke
-> local_governance authority
-> readback/reconcile
```

```text
NEW_CHATGPT_PLAN_REQUIRES_GITHUB_ISSUE=no
LOCAL_GOVERNANCE_NORMAL_SUPPORTED=yes
GOV1_IS_NORMAL_NEW_PLAN_PATH=no
```

The GitHub adapter is not a replacement subsystem and must not be used to
avoid a missing local binding or a local `governance.plan` error.

## Legacy Representation Only

When and only when the current Plan binding is `github_issue`, the old
representation may be inspected and mutated under its own trusted read-before-
write/CAS contract:

```text
LEGACY_GOV1_ADAPTER_REPRESENTATION=yes
ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
ISSUE_BODY_ROLE=portable_plan_normative_contract
GITHUB_5_COMMENT_MODEL_ADAPTER_SPECIFIC=yes
GITHUB_5_COMMENT_MODEL_GOV2_REQUIRED=no
LOCAL_GOV2_CREATES_FIVE_COMMENT_FILES=no
COMMENT_PLAN_AUTHORITY=no
PLAN_ISSUE_EXPECTED_PRIMARY_MANAGED_COMMENT_COUNT=5
```

The five legacy roles are:

```text
milestone_progress_index
development_notes
defect_register
plan_appendix
decision_change_log
```

These are adapter-specific projections/support/history. Comment IDs and
ordinal positions are not Portable Plan semantics. They must not be
translated into five local files, and their presence does not alter a local
Gov2 binding.

Legacy hydration is bounded and authority-aware. It may read the Issue body
and relevant managed comments only after the explicit `github_issue` binding
has been resolved. A material amendment may update the Issue body only on
that same explicit legacy binding; it is never the local Gov2 amendment
workflow.

```text
LEGACY_ISSUE_BODY_MUTATION_ONLY_WHEN_BOUND=yes
GITHUB_5_COMMENT_MODEL_LEGACY_ONLY=yes
```

This alias does not grant GitHub access, generic writer capability, or an
alternative transport. If the runtime has no authorized GitHub adapter, stop
with the typed boundary instead of attempting `gh`, a generic API or a direct
filesystem substitute.
