---
name: aota-chatgpt-worktree-governance
description: Transitional ChatGPT Friday -> manual Codex mechanical Git worktree adapter. Decision authority stays with task-main; Codex only materializes explicit bindings.
---

# AOTA ChatGPT Worktree Governance — Transitional Mechanical Adapter

This is the **transitional ChatGPT / Friday -> manual Codex mechanical adapter**.
It does not define the long-term AOTA runtime worktree contract. That contract
remains:

```text
AOTA_RUNTIME_WORKTREE_AUTHORITY=aota-worktree-governance
  (aota-hermes-tools/skills/aota-worktree-governance/SKILL.md)
CHATGPT_MANUAL_ADAPTER_ALLOWS_RUNTIME_DUPLICATION=no
```

This file references the shared core for hierarchy, authority, and checkpoints:

```text
SHARED_CORE=aota-portable-plan-governance.md
GOVERNANCE_INDEX=chat_governance/GOVERNANCE_INDEX.md
TRANSITIONAL_CHATGPT_CONTROL_PLANE=yes
LONG_TERM_WORKTREE_EXECUTION_OWNER=task-main
WORKTREE_CHATGPT_ADAPTER_DUPLICATES_RUNTIME_CORE=no
```

Read `aota-worktree-governance` for the full runtime root/path/branch/
lifecycle/cleanup rules. This adapter keeps only the transitional
decision-vs-mechanical boundary and ChatGPT-specific base semantics.

## Decision ownership vs mechanical execution

The confusion this adapter fixes: lifecycle decision ownership is not shell
execution ownership.

```text
WORKTREE_LIFECYCLE_DECISION_OWNER=task-main
FRIDAY_ASSIGNES_WORKTREE_BINDING=yes
CODEX_AUTONOMOUS_WORKTREE_SELECTION=no
CODEX_AUTONOMOUS_BRANCH_SELECTION=no
CODEX_AUTONOMOUS_BASE_SELECTION=no
CODEX_AUTONOMOUS_CLEANUP_DECISION=no
CODEX_MAY_MATERIALIZE_ASSIGNED_WORKTREE=yes
```

Friday/task-main decides and explicitly assigns:

```text
BASE_SHA
WORKTREE_PATH
BRANCH
WORK_ITEM_REF
WRITE_SCOPE
```

Codex may then **mechanically** execute the assigned `git worktree add ...`
and branch creation. This is not Codex owning worktree lifecycle authority.
The distinction is:

```text
semantic/decision authority != mechanical command execution
```

Cleanup is the same: Codex never autonomously decides cleanup. Only an
explicitly authorized cleanup task may trigger mechanical removal, still
subject to non-force safety gates below.

## Serial Work Item — no forced worktree

```text
WORKTREE_REQUIRED_FOR_CONCURRENT_WRITERS=yes
WORKTREE_REQUIRED_FOR_SINGLE_SERIAL_WORK_ITEM=no
```

One serial `W` may execute directly in the canonical worktree when:

- base is explicit and git-verified
- unrelated dirty state can be preserved
- Friday explicitly assigned the canonical worktree

Do not create a branch/worktree for a small single `W` merely for formal
consistency.

## Parallel writers — isolation required

With 2+ concurrent writable `W` / lanes:

```text
CONCURRENT_WRITER_ISOLATION_REQUIRED=yes
WORKTREE_PATH_PATTERN=<workspace>/.aota-worktrees/<project-id>/<milestone-id>/<work-item-or-lane-slug>
BRANCH_PATTERN=aota/<milestone-id>/<work-item-or-lane-slug>
```

`milestone-id = M<n>`, `work-item identity = W<n>`. For a Child Plan,
include `S<n>` in Plan context deterministically; do not force an
excessively long path — Plan context must remain deterministic, not verbose.
Lane slug may equal `W<n>` if no extra alias is needed; `LANE_IS_FORMAL_WORK_ITEM_ID=no`.

## BASE semantics

Retire the oversimplified rules:

- "always use latest known-good Milestone checkpoint"
- "use accepted writable base from Issue Body"

```text
WORKTREE_BASE_MUST_BE_EXPLICIT=yes
WORKTREE_BASE_MUST_BE_GIT_VERIFIED=yes
COMMON_BASE_SHA_REQUIRED=yes
COMMON_BASE_SHA_MUST_BE_EXPLICIT=yes
COMMON_BASE_SHA_MUST_BE_GIT_VERIFIED=yes
CURRENT_EXECUTION_BASE_ROLE=current_verified_integration_frontier
CURRENT_EXECUTION_BASE != MILESTONE_KNOWN_GOOD_CHECKPOINT
```

At Milestone start the base is usually the prior Milestone's known-good
checkpoint. Mid-Milestone, a mechanically integrated frontier commit may
become the exact base for dependent `W`. Advancing to that frontier:

```text
MID_MILESTONE_BASE_ADVANCEMENT_IS_ACCEPTANCE=no
MID_MILESTONE_BASE_ADVANCEMENT_REQUIRES_STEWARD=no
MID_MILESTONE_BASE_ADVANCEMENT_UPDATES_BODY=no
MID_MILESTONE_BASE_ADVANCEMENT_KNOWN_GOOD=no
```

Never invent a per-`W` `accepted-base` ceremony. Only Milestone acceptance/
closure creates a `known-good` checkpoint.

## Local checkpoint

A `W` may create a local/mechanical commit for rollback, diff isolation,
integration, or continuation:

```text
LOCAL_COMMIT_IS_MILESTONE_KNOWN_GOOD=no
LOCAL_COMMIT_REQUIRES_STEWARD=no
LOCAL_COMMIT_REQUIRES_GITHUB_MATERIALIZATION=no
```

Known-good Milestone checkpoint is established only after `RV PASS` +
acceptance `PASS` + `MILESTONE_CLOSE` per the shared core.

## Worktree safety

Retained from runtime authority, even in this transitional adapter:

```text
WORKTREE_RESET_ALLOWED=no
WORKTREE_CLEAN_ALLOWED=no
WORKTREE_STASH_ALLOWED=no
WORKTREE_CLEANUP_FORCE_ALLOWED=no
UNRELATED_DIRTY_FILES_PRESERVED=yes
WORKTREE_IS_PLAN_AUTHORITY=no
WORKTREE_IS_SPEC_AUTHORITY=no
WORKTREE_IS_PROJECT=no
WORKTREE_CODEGRAPH_AUTHORITY=no
SHARED_HOT_FILE_POLICY_DEFAULT=integration_only
```

Shared hot files are integration-only by default; give explicit ownership to
one `W` only when safe.

```text
DEDICATED_INTEGRATION_WORKTREE_ALLOWED=yes
INTEGRATION_IS_REVIEW=no
INTEGRATION_IS_MILESTONE_CLOSE=no
INTEGRATION_REQUIRES_STEWARD=no
WORKTREE_REMOVAL != BRANCH_DELETION
```

`aota-worktree-governance` remains the single detailed runtime authority;
this file must not diverge from it.
