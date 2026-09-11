---
name: aota-task-lifecycle
description: AOTA Profile Task binding contract from frozen SPEC through result, handoff, decision, and acknowledgement.
category: orchestration
---
> **W0 Canonical Migration — aota_forge is authority**
>
> This Skill's semantic authority converges to `aota_forge/skills/aota-task-lifecycle/SKILL.md` (M1/W0).
> Legacy `aota-hermes-tools/skills/aota-task-lifecycle/SKILL.md` is non-authoritative projection after W0.
> Bounded rewrite: removed Hermes host-specific loader path assumptions (`~/.hermes/profiles/.../skills`, `plugins/aota-tools` mount)
> and aligned tool references to AF canonical seams (`workspace.read` / `workspace.search` / `workspace.write` via `aota.invoke`,
> `OperationContractDescriptor` / `work_plane/workspace_tools.py`, `work_plane/skill_registry.py`).
> `SKILL_IS_AUTHORITY=no` preserved — Skill remains guidance, not authority.


# AOTA Task Lifecycle

Profile Task start derives `AOTA_WORKSPACE_ID`, `AOTA_PROJECT_ID`, roots,
workspace decision ID, and frozen SPEC identity from the frozen SPEC; callers
cannot override them.  `meta.json`, Card, Result, and Handoff must preserve
the same binding.  On any mismatch workers stop with `needs_input` and use
`binding_invalid`; they never guess another workspace.  task-main validates
the handoff binding and records the durable decision before acknowledgement.

The launcher also preserves a separate Hermes path binding:
`global_hermes_home`, `parent_profile_home`, and `target_profile_home`.
`HERMES_HOME` may identify the current parent Profile, but it is never passed
to credential bootstrap as the global authority. Early bootstrap/finalizer
failure must leave the bounded redacted worker log, canonical failure receipt,
and failure handoff; the fallback handoff does not require worker CARD/RESULT.
Canonical `spec_hash` and legacy `spec_sha256` are separate frozen bindings and
must both remain intact in receipts and handoffs.  `approval_status` is
`not_required` for diagnosis, review, architecture, and stewardship; only
implementation uses the approval API after freeze.

Credential bootstrap is global-authority-only: API keys come from the global
Hermes `.env`, OAuth is discovered from global `auth.json`, and no target,
default, parent-local, or arbitrary caller `.env` is read. The worker child
environment is bounded and explicitly sets `HERMES_HOME` to the normalized
global root; `-p <resolved_profile>` is the sole Profile selector.

The launcher manifest also freezes `resolved_profile`, provider, model,
Profile-config digest, and credential source type. A digest/provider/model
mismatch is `profile_launch_binding_mismatch`; parent model/provider values are
not a fallback. Early launcher failures always leave a bounded worker log,
failure receipt, and failure handoff, even when no worker artifact exists.

## Active worker context preflight

Every worker reads its own frozen `SPEC`, `SCOPE`, and bounded `BINDING` through
`aota_active_task_artifact_open` before project-tier work. The three results
must agree on workspace/task/start/profile/spec identity. Missing, mismatched,
or denied reads fail closed with `needs_input`/`blocked`; workers never guess,
switch Profile, or use terminal/file fallback. Subject reads are available only
when the frozen reviewer/debugger/architect binding explicitly permits them.

## Canonical scope and task isolation

The frozen project scope is read from `spec.payload` by the shared
`_task_spec_scope.py` extractor. Payload keys take precedence even when their
value is `[]`; the bounded legacy top-level fallback is observable through
`scope_source=legacy_top_level`. `scope.json` is an atomic, immutable projection
bound to the SPEC id, revision, hashes, task/start identity, and `scope_digest`.

Task start captures `workspace-baseline.json`, including pre-existing tracked
dirty and untracked paths plus content fingerprints. Finalization treats
trusted, identity-bound `scope-events.jsonl` as the primary worker evidence and
reports only baseline deltas as postflight observations. A workspace-wide Git
diff is never a current-task worker action by itself. Missing or foreign event
identity is counted and unattributed project deltas fail closed.

## Project Lifecycle Contract

The minimal project lifecycle contract has one canonical source:
`aota_forge/work_plane/* and .aota/contracts/operations.yaml/_project_lifecycle_contract.py`, also declared in
`deploy/aota-lifecycle-inventory.yaml` under `project_lifecycle_contract`.

Key definitions (authoritative in the canonical source):

- **Dispatch invariant** `PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH`:
  protected project/profile mutation requires a task-main dispatched Profile
  Task with a frozen SPEC.  This covers ONLY protected mutation classes, NOT
  all filesystem writes.
- **Protected mutation classes**: project_initialization, project_git_lifecycle,
  project_codegraph_lifecycle, project_registry_mutation, project_closure,
  project_metadata_mutation.
- **Runtime artifact write exemptions**: CARD, RESULT, worker_outcome,
  completion_receipt, handoff, review_decision, task_runtime_meta,
  bounded_logs, bounded_temporary_state -- NOT protected mutations.
- **task-main dispatch authority** vs **Project Steward execution ownership**:
  task-main originates dispatch; Project Steward executes dispatched
  operations but cannot originate un-dispatched protected mutation.
- **Initialization states**: `initialized_core` (scaffold/metadata/registry
  complete, Git/CodeGraph not yet guaranteed) vs `initialized`
  (initialized_core + Git + CodeGraph + aggregate verification +
  authoritative receipt).
- **Initialization receipt authority**: CARD/RESULT are worker observations;
  the trusted finalizer / authoritative receipt is the final lifecycle
  result.  Project Steward cannot self-declare authoritative success.
- **Codex escalation boundary**: Codex is a Host/infra escalation path, not a
  general project operator.  Current Git/init gap requires Codex fallback
  (REQUIRED); target state is NOT_REQUIRED once bounded tools are implemented.
- **Fail-closed**: reject non-task-main protected mutation SPEC, unfrozen SPEC,
  binding mismatch, profile mismatch, artifact exemption used for project
  source write, steward mutation missing binding, initialized receipt missing
  Git/CodeGraph summary, receipt authoritative but wrong reconciliation source,
  unknown initialization state, unknown protected mutation class.

## Wait mode semantics

The task lifecycle distinguishes four wait modes for task completion
retrieval, classified by transport capability:

- **wakeup_capable_normal**: The transport supports wakeup notifications.
  Polling is PROHIBITED. The system must wait for a wakeup signal, not poll
  for completion.
- **non_wakeup_transport**: The transport does not support wakeup
  notifications. Explicit retrieval (checking status on demand via
  `aota_profile_task_status`) is allowed. The API Server transport has
  `supports_async_delivery=False`, which classifies it as
  `non_wakeup_transport` — it is NOT a handoff wakeup channel.
- **isolated_probe**: A bounded, one-time status check. Polling is allowed
  ONLY when the `POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY` marker is present.
  Without the marker, isolated probe polling is rejected.
- **recovery**: Retry after a failure. Requires an explicit reason string.
  Recovery without a reason is rejected.

The canonical wait mode rules are defined in
`aota_forge/work_plane/* and .aota/contracts/operations.yaml/_skill_authority_contract.py` under `WAIT_MODE_RULES`.
The lifecycle inventory mirrors these as governance metadata. No second
authority is created.

## WebUI task-main session wait-mode classification

When task-main starts a Profile Task from a WebUI session, the wait mode is
classified from runtime evidence — not from model self-inference about the
transport.

### Classification rules

| Session context | Transport evidence | Wait mode |
|---|---|---|
| WebUI task-main session | Transport supports async delivery (wakeup) | `wakeup_capable_normal` |
| WebUI task-main session | API Server (`supports_async_delivery=False`) | `non_wakeup_transport` |
| CLI session | Hermes CLI with handoff wakeup | `wakeup_capable_normal` |
| Any session | No wakeup channel available | `non_wakeup_transport` |
| One-time check | Explicit isolated probe with marker | `isolated_probe` |
| Post-failure retry | Explicit reason provided | `recovery` |

The model must NOT self-classify the transport. The wait mode is resolved from
runtime evidence during the orchestration preflight (see
`aota-profile-task-orchestration` Skill, preflight step 1).

### START_RESULT=running / WAIT_MODE=wakeup_capable_normal

When `aota_profile_task_start` returns `running` and the wait mode is
`wakeup_capable_normal`:

- **NEXT_ALLOWED_ACTION**: Wait for the wakeup signal (handoff notification).
  Do not call `aota_profile_task_status` unless a valid `retrieval_reason`
  is provided.
- **FORBIDDEN_PROGRESS_ACTIONS** (all prohibited without `retrieval_reason`):
  1. Calling `aota_profile_task_status` without a `retrieval_reason`.
  2. Reading worker logs (`worker.<task_id>.log`) to check progress.
  3. Reading process output or inspecting process registry for progress.
  4. Inspecting repo changes (git diff/status) to infer worker activity.
  5. Checking artifact existence (CARD.json, RESULT.md, etc.) to infer
     completion.
  6. Reading output files or checking file sizes to gauge progress.
  7. Using `workspace.read path validation (via WorktreeSandboxBoundary)` or `workspace.read (via aota.invoke)` on task directories to poll
     for new artifacts.

### Non-recovery conditions

The following observations are NOT recovery conditions and do NOT authorize
polling:

- Running status returned by a prior status query.
- Unchanged worker log size (the worker may simply not have written yet).
- Absence of new files or artifacts (the worker may still be working).
- Curiosity about worker health or progress.

### One authorized check does not authorize repeated polling

A single status query with a valid `retrieval_reason` is an authorized
recovery check. It does NOT authorize a second query. A second query within
the minimum interval is rejected as `repeated_progress_poll_forbidden`.

### non_wakeup_transport explicit retrieval conditions

When the wait mode is `non_wakeup_transport` (e.g., API Server with
`supports_async_delivery=False`), explicit retrieval via
`aota_profile_task_status` is allowed, but ONLY under these conditions:

1. **External re-entry**: A new session or turn that is not a busy-wait
   continuation of the same turn that started the task.
2. **Declared timeout**: The completion notification timeout has elapsed
   (`retrieval_reason=completion_notification_timeout`).
3. **Scheduled retrieval**: A scheduled or cron-triggered check
   (`retrieval_reason=non_wakeup_reentry`).
4. **User-triggered check**: The user explicitly requests a status check
   (`retrieval_reason=user_requested`).

Explicit retrieval must NOT happen within the same turn as task start. Do
not busy-wait in the same turn — start the task, then exit and wait for
re-entry.

## Binding, receipt, and handoff authority

The completion receipt is the trusted finalizer / authoritative result.
`done` requires exit code zero, a valid terminal worker outcome, and a
canonical receipt with `status=done`. Wakeups must never invent `done` when
a receipt is absent. CARD/RESULT are worker observations; they are not
authoritative lifecycle results.

task-main validates the handoff binding and records the durable decision
before acknowledgement. Handoff acknowledgment only means the orchestration
layer consumed the completion. It does NOT mean: task approved, source
correct, review passed, user accepted, or next task started. Auto-dispatch
is never implied by ack.

## Handoff + Task Lifecycle Thin Façade (AF #48 M1/W2)

W2 implements the W1-frozen thin Agent-facing façade via One-Core `aota.invoke`:

* `handoff.write(mode=milestone|work_item|result, payload=bounded semantic)` — LLM-owned semantic, Control Plane fills trusted envelope (`artifact_id`, `project_id`, `worktree_id`, `plan_ref`, `milestone_id`, `work_item_id`, `source_role`, `target_role`, `task_id`, `attempt_id`, `created_at`, `schema_version`, `provenance`, `digest`). Digest-bound durable artifact under `.aota/handoffs/<digest>.json`; restart-readable, tamper fail-closed, cross-project/worktree fail-closed. `LLM_MUTATES_CONTROL_FIELDS=no`, `CONTROL_PLANE_REWRITES_LLM_SEMANTICS=no`; `HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD=yes`. Modes: `milestone`/`work_item` writer = `task-main` only; `result` writer = `coder|analyst|reviewer|project-steward` (one full result, `WORKER_AUTHORS_RESULT_CARD=no`, single write). Reuses `TaskHandoff` semantic contract, `CanonicalResult`/`ResultGovernanceProjection`/`WorkerResultCard`/`ResultHandoffRef` (no second Result ontology).

* `handoff.open(ref, view=card|full)` — `full` = bounded semantic artifact + necessary model-visible control metadata (artifact_id, project/worktree/plan/milestone/work_item, source/target role, task/attempt, created_at, digest); never full internal storage/secret/host internals. `card` = deterministic compact projection (`card_digest` over canonical card). Digest + project/worktree binding verified.

* `task.start(role=target AgentWorkRole, handoff_ref=work_item handoff)` — `task-main` only. Validates dispatch authority, target role, handoff existence/digest/binding, resolves semantic `TaskHandoff`, compiles via `compile_handoff_to_execution_package`, invokes existing `execution.task_start` seam (reuses `ExecutionDispatcher`, no new engine/state-machine). Returns `{task_id, status}`.

* `task.return(status=completed|blocked|failed, result_ref=durable result handoff)` — `coder|analyst|reviewer|project-steward` only (child → parent terminal return). Distinct from `handoff.write` and `execution.task_result`. Validates child identity, active task/attempt, result_ref ownership/digest/binding, derives `ResultGovernanceProjection` + deterministic `WorkerResultCard` from existing `CanonicalResult` seam, marks terminal via existing `ExecutionStateStore` CAS, delivers inline compact card + `full_result_ref` via parent wakeup (existing `DurableCompletionCoordinator` / `ExecutionDispatcher` recovery, not new coordinator). `NORMAL_TASK_MAIN_CARD_EXTRA_READ_CALL=0`.

* Worker startup separation: `role.bootstrap` (who am I / tools / Skill / trusted identity) then `handoff.open(work_item_ref, view=full)` (what exact task am I doing). Full Work handoff never re-injected into `role.bootstrap`.

* Durability: reuse `ExecutionStateStore` + `durable_result_store` patterns; minimal bounded adapter `.aota/handoffs` (one file per digest, atomic temp+rename, digest-verified). `handoff` never dispatches worker, never terminates task; `task.start` never invents semantics; `task.return` never creates work semantics. `task_main.submit_work_projection` remains internal compatibility (`WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED=no`).

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/plugins/`,
  `AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/skills/`).
- Config declaration is not runtime availability evidence. A toolset may be
  declared in config but fail to register at runtime.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence or actual Profile
  Task execution. Hash parity between source and runtime is deploy evidence,
  not runtime evidence.
- Managed deploy and recreate requirements apply per the lifecycle inventory.
  All importing processes must be recreated after changes.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source implementation.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source.
- No generic `/aota-runtime` access; use bounded runtime tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).


---
*W0 provenance: migrated from legacy `aota-hermes-tools` (SHA c2e0c4d) to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`.*
