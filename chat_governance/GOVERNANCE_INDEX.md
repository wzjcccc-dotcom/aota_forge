# AOTA Forge — Chat Governance Canonical Index

This index declares the canonical authority map for Chat governance.
It does not duplicate governance rules. Normative rules live in the
shared core; other files only reference / specialize / adapt.

```text
CANONICAL_CHAT_GOVERNANCE_ROOT=/home/latios/workspace/aota_forge/chat_governance

SHARED_CORE=aota-portable-plan-governance.md

CHATGPT_PLANNING_ENTRY=aota-chatgpt-project-planning.md

# P2 adapters (declared, deferred)
PARALLEL_DEVELOPMENT_ADAPTER=aota-chatgpt-parallel-development.md
WORKTREE_MANUAL_ADAPTER=aota-chatgpt-worktree-governance.md
LEGACY_GITHUB_ENTRY_ALIAS=aota-github-issue-planning.md
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

## P2 / P3 obligations

P2 must complete thin adapter / alias alignment for:

- `aota-chatgpt-parallel-development.md`
- `aota-chatgpt-worktree-governance.md`
- `aota-github-issue-planning.md` (legacy alias)

P2 must also align transitional Hermes-tools sources:

- `aota-project-plan-intake`
- `aota-worktree-governance`
- compatibility aliases and validators

P3 must establish wrapper / alias consistency and drift guards so that
no duplicate normative authority can reappear without detection.

This index is the only place that maps canonical roles to files.
If a file is not listed here, it is not canonical Chat governance.
