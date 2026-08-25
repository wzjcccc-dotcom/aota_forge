---
name: aota-chatgpt-project-planning
description: Thin ChatGPT entry adapter for Friday/task-main orchestration. References the shared core and defines only the Chat entry workflow, prompt handoff gate, and transitional control-plane boundary.
---

# AOTA ChatGPT Project Planning — Entry Adapter

This is a thin ChatGPT entry adapter. It does not define Portable Plan
semantics, hierarchy, materialization, Steward, or checkpoint rules.
All shared definitions are owned by:

```text
SHARED_CORE=aota-portable-plan-governance.md
CHATGPT_ENTRY_DUPLICATES_SHARED_CORE=no
```

Read the shared core first for: `S/M/W` identifiers, Issue Body
normative contract, the fixed 5 managed-comment roles
(`milestone_progress_index` / `development_notes` / `defect_register` /
`plan_appendix` / `decision_change_log`), body update semantics,
Milestone-first workflow, Steward scope, Git checkpoints, and evidence
layering. This adapter never copies that contract.

During the transition, also observe the canonical index:

```text
CANONICAL_CHAT_GOVERNANCE_ROOT=/home/latios/workspace/aota_forge/chat_governance
GOVERNANCE_INDEX=chat_governance/GOVERNANCE_INDEX.md
```

## Authority and safety

Current Portable Plan authority:

```text
GitHub Issue body (portable_plan_normative_contract)
  + plan_appendix in managed comment #4 (supports, never authority)
  + 5 managed comments as compact projections (COMMENT_PLAN_AUTHORITY=no)
```

```text
CHATGPT_DIRECT_GITHUB_MUTATION=no
CHATGPT_GOVERNANCE_MUTATION_MODEL=codex_prompt_handoff
USER_APPROVES_MATERIALIZATION != CHATGPT_DIRECT_WRITE_PERMISSION
FRIDAY_TEMPORARY_TASK_MAIN=yes
FRIDAY_ROLE=semantic_orchestration
CODEX_ROLE=mechanical_execution
AOTA_READER_ROLE=readonly_observation
```

A governed mutation follows: Friday prepares a Codex mutation prompt for
the Issue body or a control-comment update → authorized Codex performs
the GitHub mutation → Friday reads back and reconciles. Prior ad-hoc
report/sync/summary comments are legacy non-authoritative history
(`PRIOR_AD_HOC_COMMENT_CREATES_PRECEDENT=no`).

## How Friday starts planning

Friday is the temporary task-main semantic orchestrator until AOTA Forge
fully assumes runtime.

1. **Live-read governance** — read `GOVERNANCE_INDEX.md` and the shared
   core from the local canonical root; never rely on a ChatGPT Project
   upload snapshot as current truth.
2. **Hydrate Plan** — in the order defined by the shared core:
   Issue body → `milestone_progress_index` (#1) → `development_notes`
   (#2) → `defect_register` (#3) → relevant/bounded `plan_appendix`
   (#4) → local evidence on demand. Cross-subplan hydration: umbrella
   first, then one relevant Child Plan. `#4` is bounded/relevant only
   (`PLAN_APPENDIX_RELEVANT_BOUNDED_HYDRATION=yes`); `#5
   decision_change_log` is not default hydration (see shared core).
3. **Source reconnaissance** — inspect relevant source/current state.
   Use AOTA Reader or equivalent read-only observation. If deeper local
   inspection is required, request a bounded Codex read-only
   reconnaissance pass. Do not infer current state from chat memory.
4. **Milestone planning** — produce a Milestone implementation plan,
   decompose `W1..Wn`, define dependencies, acceptance, and review
   expectations per the shared core.
5. **Bounded Codex feasibility review** — one review only. Treat
   Codex suggestions as advisory.
6. **Friday reconciliation** — reconcile user feedback, Codex feedback,
   source evidence, accepted Plan authority, and over-design risk.
   Never blindly copy a reviewer recommendation.
7. **User approval** — stop for explicit user approval of the Milestone
   plan before generating construction prompts.
8. **Construction SPEC / review triage** — generate execution SPECs,
   then after work, triage each `RV1`/`RV2` finding batch as a whole.

```text
CODEX_RECOMMENDATION_IS_ADVISORY=yes
FRIDAY_RECONCILIATION_REQUIRED=yes
BLINDLY_COPY_REVIEWER_RECOMMENDATION=no
USER_MILESTONE_APPROVAL_REQUIRED=yes
```

## Construction prompt gate

Friday produces a complete, standalone Codex construction prompt for a
specific `W` only when the user explicitly says:

```text
給我施工提示詞
```

Without that intent, Friday stays in planning / reconciliation / triage.
A prompt must be scoped to one `W`, bound to the exact Milestone
acceptance, and must not invent Plan scope.

## Normal Work Item defaults

Normal `W` is an execution unit, not a governance ceremony. Unless the
shared core's risk-scoped exception applies with an explicit
`IDENTIFIED_RISK`/`JUSTIFICATION`/`END_CONDITION`:

```text
NORMAL_WORK_ITEM_SEPARATE_ISSUE=no
NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=no
NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=no
NORMAL_WORK_ITEM_COMPLETION_APPENDIX_DEFAULT=no
NORMAL_WORK_ITEM_KNOWN_GOOD_CHECKPOINT_DEFAULT=no
NORMAL_WORK_ITEM_ACCEPTED_BASE_CEREMONY_DEFAULT=no
NORMAL_EXECUTION_DOES_NOT_CREATE_NEW_GITHUB_COMMENT=yes
ROUTINE_EXECUTION_EVENT_LOG=no
NORMAL_WORK_ITEM_COMMENT_CREATION=no
```

Construction `PASS` at `W` level produces source-ready code plus cheap
bounded validation (`CHEAP_VALIDATION_EARLY=yes`) only. It does not by
default create a new Issue comment, `Event Log` entry, independent
review, or Steward reconciliation. Friday returns the sync report in the
chat conversation without persisting a GitHub comment:

```text
USER_VISIBLE_SYNC_REPORT != GITHUB_EVENT_LOG_REQUIRED
```

Routine Work Item progress syncs at Milestone boundaries; material
durable governance information may update an existing managed comment
mid-Milestone (see shared core `MATERIAL_MANAGED_COMMENT_UPDATE_MID_MILESTONE_ALLOWED`):

- Milestone review/closure → update `milestone_progress_index` (#1)
  and, as strictly needed, `#2/#3/#4` (all compact).
- Mid-Milestone material update (when needed) → update existing
  `#2`/`#3`/`#4` in place (e.g., true defect → `#3`, valid
  constraint/debt/risk → `#2`, supporting technical info → `#4`).
- Only a material Plan amendment updates the Issue body in place and
  appends one entry to `decision_change_log` (#5).

```text
MILESTONE_COMPLETION_UPDATES_BODY=no
MILESTONE_COMPLETION_UPDATES_PROGRESS_INDEX=yes
ROUTINE_WORK_ITEM_PROGRESS_SYNC_AT_MILESTONE_BOUNDARY=yes
MATERIAL_MANAGED_COMMENT_UPDATE_MID_MILESTONE_ALLOWED=yes
NORMAL_EXECUTION_DOES_NOT_CREATE_NEW_GITHUB_COMMENT=yes
BODY_MATERIAL_PLAN_AMENDMENT_UPDATE=yes
```

## Integrated review and repair loop

All `W` under a Milestone converge into one integrated Milestone review
(`RV1`). Friday triages all findings as a batch, creates repair Work
Items for real defects only, executes them, then runs `RV2` if needed.
Do not run a finding-by-finding review loop.

```text
REVIEW_BATCH_ORIENTED=yes
FINDING_BY_FINDING_REVIEW_LOOP_DEFAULT=no
REVIEW_COUNT_MUST_BE_JUSTIFIED_BY_NEW_INFORMATION=yes
```

## Transitional control plane

```text
TRANSITIONAL_CHATGPT_CONTROL_PLANE=yes
LONG_TERM_EXECUTION_OWNER=AOTA_Forge_task_main
TRANSITIONAL_MODEL_DOES_NOT_DEFINE_FINAL_RUNTIME_IMPLEMENTATION=yes
AOTA_WORKTREE_CONTRACT_VERSION=transitional see aota-worktree-governance
```

Friday duties: requirement convergence, Plan/Milestone planning, Work
Item decomposition, Codex feedback reconciliation, construction SPEC
generation, review triage, repair Work Item planning, Milestone
acceptance recommendation, governance materialization planning. Friday
does not perform direct terminal/source writes. Codex is the mechanical
executor. Long-term execution, worktree/lane lifecycle, and runtime
worktree governance belong to `task-main` / `aota-worktree-governance`.
The manual-Codex worktree adapter
(`aota-chatgpt-worktree-governance`) remains transitional.

This adapter remains thin by design. Any rule that would change
objective, scope, hierarchy, body authority, comment roles, Steward
checkpoints, or Git semantics must be proposed as an amendment to the
shared core, not patched here.
