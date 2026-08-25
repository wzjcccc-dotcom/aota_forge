---
name: aota-github-issue-planning
description: Legacy compatibility alias for the former GitHub planning entry. Routes to GOVERNANCE_INDEX and the shared core; defines no independent authority.
---

# AOTA GitHub Issue Planning — Legacy Compatibility Alias

```text
LEGACY_COMPATIBILITY_ALIAS_ONLY=yes
GITHUB_ALIAS_DUPLICATES_CORE=no
COMMENT_PLAN_AUTHORITY=no
EFFECTIVE_PLAN_COMMENT_MODEL=deprecated
```

This file exists only so old references to `aota-github-issue-planning`
remain readable. It is not a second governance authority and it does not
redefine Portable Plan semantics.

## Routing

Resolve authority in this order:

```text
1 GOVERNANCE_INDEX.md
2 aota-portable-plan-governance.md   (SHARED_CORE — single normative authority)
3 aota-chatgpt-project-planning.md   (ChatGPT planning entry, if ChatGPT workflow)
```

That is the entire resolution. Do not treat this alias as an alternative
source for hierarchy, Body semantics, comments, identifiers, review, or
Steward.

## Retired models — do not reintroduce

The following are **not** canonical and must not be revived through this
alias:

```text
GitHub Issue body + structured appendices = Portable Plan
Comments are an append-only Event Log only
EFFECTIVE_PLAN=yes as current authority
```

Canonical under the shared core is:

```text
Issue Body = Portable Plan normative contract (current truth)
5 managed comments = compact projections/support/history, not Plan authority
  #1 milestone_progress_index  #2 development_notes  #3 defect_register
  #4 plan_appendix  #5 decision_change_log (entries append-only)
ROUTINE_EXECUTION_EVENT_LOG=no
```

This alias must not copy the full 5-role table, `S/M/W` hierarchy, or
Steward definitions — reference the shared core instead.

## Write-safety note

GitHub write capability, when present, is governed by the target repository's
Reader/Writer safety contract (target verification, allowlist, explicit user
authorization, read-before-write, receipt verification). This alias does not
grant or claim such capability; it only preserves the routing name.
