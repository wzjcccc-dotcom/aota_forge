# AOTA Forge — Chat Governance Canonical Index

This index declares the canonical authority map for Chat governance.
It does not duplicate governance rules. Normative rules live in the
shared core; other files only reference / specialize / adapt.

```text
CANONICAL_CHAT_GOVERNANCE_ROOT=/home/latios/workspace/aota_forge/chat_governance

SHARED_CORE=aota-portable-plan-governance.md

CHATGPT_PLANNING_ENTRY=aota-chatgpt-project-planning.md
CHATGPT_VALIDATION_GOVERNANCE=aota-chatgpt-validation-governance.md

PARALLEL_DEVELOPMENT_ADAPTER=aota-chatgpt-parallel-development.md
WORKTREE_MANUAL_ADAPTER=aota-chatgpt-worktree-governance.md
LEGACY_GITHUB_ENTRY_ALIAS=aota-github-issue-planning.md

P2_ADAPTERS_CANONICALIZED=yes
```

## Authority declaration

- `aota-portable-plan-governance.md` is the single shared semantic
  governance core for Portable Plans, Milestones, Work Items, review,
  Steward, Git checkpoints, and materialization.
- All other Chat governance files must reference / specialize / adapt
  the shared core. They must not copy the full authority model.
- ChatGPT Project uploads, if present, are snapshot / bootstrap /
  reference only. They are not current canonical authority.

```text
DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no
UPLOADED_SNAPSHOT_CANONICAL=no
LOCAL_CANONICAL_LIVE_READ_PREFERRED=yes
```

## Canonical truth resolution

Canonical current truth is determined by the local source in this
directory plus a live read of the governing Issue and local evidence.
An uploaded snapshot never overrides the local canonical file plus live
read.

An old source file may continue to exist during the transitional period
for read-compatibility, but the repository must not maintain two
manually-synced normative authorities for the same concern.

## Canonical map

```text
SHARED_CORE=aota-portable-plan-governance.md
CHATGPT_PLANNING_ENTRY=aota-chatgpt-project-planning.md
CHATGPT_VALIDATION_GOVERNANCE=aota-chatgpt-validation-governance.md
PARALLEL_DEVELOPMENT_ADAPTER=aota-chatgpt-parallel-development.md
WORKTREE_MANUAL_ADAPTER=aota-chatgpt-worktree-governance.md
LEGACY_GITHUB_ENTRY_ALIAS=aota-github-issue-planning.md
ONE_SHARED_GOVERNANCE_AUTHORITY=yes
DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no
```

`aota-chatgpt-validation-governance.md` is the canonical
change/risk/validation/evidence planning specialization. It references the
shared core and does not replace it.

```text
CHATGPT_VALIDATION_GOVERNANCE_SCOPE=change/risk/validation/evidence_planning_specialization
VALIDATION_GOVERNANCE_IS_SHARED_CORE_REPLACEMENT=no
VALIDATION_GOVERNANCE_CREATES_SECOND_PLAN_AUTHORITY=no
```

P2 adapters are canonicalized. P3 owns wrapper/alias consistency and drift
guards so duplicate normative authority cannot reappear undetected.

This index is the only place that maps canonical roles to files.
If a file is not listed here, it is not canonical Chat governance.
