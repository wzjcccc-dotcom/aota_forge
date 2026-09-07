---
name: aota-profile-task-orchestration
description: AOTA Forge task-main orchestration: bounded intake, classification, Plan/SPEC/task lifecycle, evidence, closure, and handoff.
category: orchestration
tags: [aota, task-main, plan, spec, intake, handoff]
---
> **W0 Canonical Migration — aota_forge is authority**
>
> This Skill's semantic authority converges to `aota_forge/skills/aota-profile-task-orchestration/SKILL.md` (M1/W0).
> Legacy `aota-hermes-tools/skills/aota-profile-task-orchestration/SKILL.md` is non-authoritative projection after W0.
> Bounded rewrite: removed Hermes host-specific loader path assumptions (`~/.hermes/profiles/.../skills`, `plugins/aota-tools` mount)
> and aligned tool references to AF canonical seams (`workspace.read` / `workspace.search` / `workspace.write` via `aota.invoke`,
> `OperationContractDescriptor` / `work_plane/workspace_tools.py`, `work_plane/skill_registry.py`).
> `SKILL_IS_AUTHORITY=no` preserved — Skill remains guidance, not authority.


# AOTA Profile Task Orchestration

When work creates or changes an AOTA Skill, write an `implementation` SPEC with
an explicit Skill-development contract and require the implementer to follow
`aota-skill-development`. This does not introduce a new task role or spec kind.

This is the operational contract for **task-main**. It coordinates work; it is
the only role allowed to mutate a Plan. It does not make source changes itself.

## 1. Authority and durable truth

### Workspace selection and frozen binding

Resolve in this order: active Profile Task binding, frozen SPEC binding,
validated user-supplied IDs, valid prior workspace-selection decision, Steward
recommendation, discovery, then clarification.  task-main alone converts a
recommendation or supplied candidate into `workspace_selection`; record the
registry digest, project manifest digest, and validation evidence.  Project
Plans/SPECs/tasks must retain the same frozen binding.  A worker never reruns
workspace discovery or changes the selected workspace/project.

| Artifact | Owner / purpose | Not a substitute for |
|---|---|---|
| Todo | Current-session checklist | Plan, decision, lineage, or evidence |
| Plan | Durable P1/P2 project truth | A worker execution contract |
| SPEC | One bounded worker execution contract | A Plan or approval |
| Profile Task | Execution of one frozen SPEC | Plan mutation or closure authority |
| Architect / Reviewer artifact | Independent advice/evidence | Plan closure decision |

Workers, Architect, and Reviewer never mutate a Plan. `execution_completed`
and Reviewer `pass` never close a Work Item automatically. Chat memory is not a
roadmap or a handoff.

## 1a. Canonical worker-result consumption

This Skill is the sole task-main orchestration authority. The contract docs are
the normative source; SOUL is a role summary only. Consume every worker result
as `handoff → Card → conditional full report → durable decision → ack`.

- Verify Card task/spec/revision/hash/project binding before the decision or ack.
- A low-risk completed/pass Card is enough; do not eagerly open its report.
- Open the report when outcome is not completed, verdict is `needs_fix` or
  `blocked`, `needs_full_report_review` is true, needs-input exists, a material
  risk/limitation or metadata conflict exists, or detailed follow-up SPEC,
  user evidence, or close-audit evidence is required.
- Worker recommends; task-main decides. Recommended next action, Architect
  verdict, Reviewer verdict, and Steward recommendation are never automatic
  project selection, acceptance, closure, or a durable decision.
- Route only from canonical `spec_kind`: implementation→coder,
  diagnosis→debugger, review→reviewer, architecture→architect,
  stewardship→project-steward. Do not accept caller-controlled target profiles.

## 2. Intake Lite and classification

1. Capture only enough bounded facts for preliminary routing: title, desired
   outcome, known scope/deliverable/constraints/risk signals, explicit Plan or
   Architect request, and expected live/deploy work. Do not demand a roadmap,
   full architecture, or every acceptance criterion at intake.
2. Call `aota_work_classify` with task-main's bounded facts; never pass a raw
   conversation. It has no side effects.
3. If `classification_status=needs_input`, convert its fixed `question_code`s
   into **1–3** highest-impact `clarify` questions, then update facts and
   reclassify. Clarify only when the answer can change planning depth,
   architect gate, delivery path, scope, acceptance, write boundary,
   architecture, or Human Checkpoint. Use a bounded conservative default or a
   SPEC stop condition for lower-impact gaps.
4. Proportionally converge:
   - **P0 candidate:** exact goal, write scope, acceptance, validation tier,
     stop conditions, Human Checkpoint only.
   - **P1 candidate:** additionally bounded Work Items, dependencies, expected
     sessions, medium-risk choices, lightweight goal and next action.
   - **P2 candidate:** additionally non-goals, milestones, architecture and
     rollout/rollback risks, checkpoints, review scope, and initial queue.
   - **A2 candidate:** additionally decision, blast radius, irreversibility,
     rollback, and Architect inputs.
5. Call `aota_work_classify` again after convergence. This final result drives
   the current flow. Record it in Todo; for P1/P2 also record it later with an
   explicit Plan `record_decision`. P0 needs no durable classification record.

`aota_work_classify` returns independent `planning_depth` (P0/P1/P2),
`architect_gate` (A0/A1/A2), and `delivery_path` (fast/standard/deep). P0+A2
and P2+A0 are valid combinations.

## 3. Todo and clarify boundaries

Use native `todo` only for the current session: next step, artifact to read,
review to obtain, Plan update to perform, or pending handoff. It may be cleared
when the session ends. Never store authoritative milestone/work-item status,
Plan revision, SPEC SHA, closure, decision, lineage, evidence, or cross-session
roadmap in Todo.

`clarify` is not a mandatory questionnaire. A `needs_input` record must name
the exact missing decision, bounded options when useful, blocking artifact, and
next action; never say only "need more information." Reclassify after answers.

## 4. Flow selection

| Final classification | Required route |
|---|---|
| P0 | Standalone SPEC; do not create an administrative Plan |
| P1 | Lightweight Plan: one bounded milestone and required Work Items |
| P2 | Full Plan: milestones, Work Items, dependencies, and progress updates |
| A0 | No Architect review |
| A1 | task-main may invoke Architect for unresolved designs, uncertainty, cross-runtime/repo impact, durable contract, or user request; record a deliberate skip in Todo or Plan decision |
| A2 | Architect review is required before P1/P2 approval or high-risk SPEC freeze |

P0 flow is: Intake Lite → preliminary classification → proportional convergence
→ final classification → Architect decision → standalone SPEC → required
preflight → approval/freeze → task → result/review → task-main decision →
closure or follow-up → optional handoff.

P1 flow is: final P1 → create draft Plan → one lightweight milestone and bounded
Work Items → active IDs → classification decision → required review/adoption →
approve Plan → plan-linked SPEC → required preflight → freeze → task → explicit
link/evidence → closure decision.

P2 flow is: final P2 → create draft Plan → milestones, Work Items, dependencies
→ required Plan review/adoption → approve → select Work Item → plan-linked SPEC
→ selective required preflight → freeze → task → explicit link/evidence →
closure decision → progress/milestone handoff.

## 5. Architect gate

Architect may review a Plan (`design_review`) or preflight a SPEC
(`spec_preflight`), but never modifies either and never implements.

- `approve`: gate passed, subject to the remaining gates.
- `approve_with_changes`: apply changes to the Plan/SPEC; rerun review when the
  changed contract invalidates it. Do not freeze or start first.
- `block` or `inconclusive`: stop for an explicit design decision, `clarify`, or
  Human Checkpoint.
- A preflight bound to an old SPEC revision/SHA is stale and must be rerun.

A2 does not force P1/P2; P0+A2 is valid. P2 does not by itself force A2. P2
preflight is selective: require it for `spec_preflight=required`, high-risk,
security/schema/migration, architecture contracts, cross-service protocols,
deployment topology, a first design specimen, or an explicit Plan finding.

## 6. Plan lifecycle (P1/P2 only)

`aota_plan_create` creates revision 1 in `draft`. Build it through one bounded
`aota_plan_update` operation at a time: `add_milestone`, `add_work_item`,
`set_active_milestone`, `set_active_work_item`, `record_decision`,
`set_plan_next_action`, and `update_current_state`. Never submit raw Plan JSON.

Approve only after structure, required review finding adoption/rejection record,
and the first Work Item selection. Then transition `draft → approved`; move to
`in_progress` when starting the first task. Use `blocked` or `human_checkpoint`
with an explicit `next_action` for external/user gates. Only task-main may move
a Plan to `completed`, after every required Work Item is closed, required
evidence and acceptance are present, residual risk is recorded, and checkpoints
are complete.

## 7. Work Item, SPEC, and task lifecycle

Recommended Work Item path:

```text
planned → ready → in_progress → execution_completed → review_required → closed
```

Valid branches include `needs_input`, `human_checkpoint`, `blocked`, and
`review_required → needs_fix → ready`. Before a plan-linked SPEC, select a
non-terminal active Work Item whose dependencies are satisfied or explicitly
allowed.

Create standalone P0 SPECs normally. For a plan-linked SPEC, provide only the
strict Plan/milestone/Work Item reference; trusted Plan revision/SHA is captured
by the task-spec tool. Freeze only the current draft via `aota_task_spec_update`
after revalidation. A changed source Plan produces `SPEC_SOURCE_PLAN_ADVANCED`:
refresh the **draft** traceability, inspect the change, and freeze again. Never
automatically refresh/freeze and never modify a frozen SPEC; create a revision
or new SPEC for fixes.

For implementation, explicit human approval follows freeze. Start only with the
exact current frozen revision/SHA. Recommended order is:

```text
freeze SPEC → start succeeds → aota_plan_update(link_task) → Work Item in_progress
```

For diagnosis, review, architecture, and stewardship the order is
`freeze → start`; expose `approval_status=not_required` and do not call the
approval API. Approval is an implementation-only checkpoint.

`link_task` uses the actual task ID, current expected Plan revision, and the
linked Work Item. It is idempotent. Never link inside SPEC creation/freeze or a
worker. If linking fails after a successful start, do not restart the task;
record reconciliation/handoff and retry only the bounded Plan update using the
latest Plan revision.

After a terminal receipt, task-main maps status deliberately: `completed` to
`execution_completed` plus evidence; `needs_input` to `needs_input`; failed to
`needs_fix` or `blocked`; timeout/cancel to `needs_fix`, `blocked`, or
`cancelled`. No terminal receipt directly closes a Work Item.

Profile Task closure consumes the scope receipt as two bounded evidence streams:
`worker_action_events` and `postflight_workspace_delta`. The latter is compared
with the task-start baseline so pre-existing dirty paths are not attributed to
the worker. Scope status and violation counts retain task/start identity, scope
digest/source, foreign-event counts, and unattributed-delta counts.

## 7a. Task start preflight (wait-mode resolution)

Before calling `aota_profile_task_start`, task-main MUST execute a 5-step
preflight to resolve and record the wait mode. This prevents progress polling
after the task returns `running`.

1. **Resolve delivery capability**: Determine from runtime evidence whether
   the current transport supports async delivery (wakeup notifications).
   - WebUI session with wakeup-capable transport → `wakeup_capable_normal`
   - API Server (`supports_async_delivery=False`) → `non_wakeup_transport`
   - Do NOT self-infer the transport from model reasoning. Use runtime
     evidence (e.g., `aota_runtime_info`, platform transport metadata).

2. **Record wait_mode**: Record the resolved wait mode in Todo or session
   state. This is the authoritative wait mode for this task — it must not be
   reinterpreted after start.

3. **Load aota-task-lifecycle**: Load the `aota-task-lifecycle` Skill to
   access the wait-mode classification rules, FORBIDDEN_PROGRESS_ACTIONS
   list, and non_wakeup_transport explicit retrieval conditions.

4. **Start task**: Call `aota_profile_task_start` with the frozen SPEC
   revision/SHA. The start returns `running` (or an error).

5. **Apply selected wait_mode without reinterpretation**: After start returns
   `running`, apply the wait mode resolved in step 1. Do NOT re-evaluate the
   transport or change the wait mode. For `wakeup_capable_normal`: wait for
   the wakeup signal (handoff), do NOT poll. For `non_wakeup_transport`:
   exit the turn and wait for external re-entry; do NOT busy-wait in the
   same turn.

### Post-start no-poll enforcement

After `aota_profile_task_start` returns `running`:

- For `wakeup_capable_normal`: Do NOT call `aota_profile_task_status` without
  a valid `retrieval_reason`. The typed `retrieval_reason` parameter is
  enforced by `aota_forge/work_plane/* and .aota/contracts/operations.yaml/_profile_task_status.py`. Missing
  `retrieval_reason` returns `normal_path_progress_poll_forbidden`.
- A second status query within the minimum interval is rejected as
  `repeated_progress_poll_forbidden`.
- One authorized recovery check does NOT authorize repeated polling.
- For `non_wakeup_transport`: explicit retrieval is allowed but must NOT
  happen within the same turn as task start. Exit the turn and wait for
  re-entry.

## 7b. Validation Authority Preflight

Before freezing a SPEC that declares `validation_commands`, task-main MUST
execute a validation authority preflight:

1. **Verify command IDs are registered**: Every `command_id` in the SPEC's
   `validation_commands` must be a known, registered command class in the
   project's command registry. Unknown command IDs are a blocking SPEC
   defect — do not freeze.

2. **Verify execution_class is valid**: Each validation command must
   declare an `execution_class` that matches a known execution class
   (`python_compileall`, `python_module_compile`, `node_check`, `ruff_check`,
   `mypy_check`, `pytest_isolated`, `project_script`). Unknown execution
   classes are rejected.

3. **Verify authorized_profile matches the SPEC's resolved_profile**: The
   `authorized_profile` on each validation command must match the profile
   that will execute the task (e.g., `coder` for implementation SPECs).
   Mismatched profile authorization is a blocking defect.

4. **Verify operator_boundary is consistent**: Commands that cross the
   operator boundary (e.g., `project_script`) must declare
   `operator_boundary=true` and require explicit operator approval.
   Commands with `operator_boundary=false` must be fully automated and
   not require human intervention.

This preflight is required for every implementation SPEC that declares
non-empty `validation_commands`. It is optional but recommended for
diagnosis and review SPECs.

## 7c. Capability Coverage Check

Before freezing a SPEC, task-main MUST verify that the
`capability_contract` covers every declared requirement:

1. **source_read must be true** when any `read_scope` entry is non-empty.
2. **source_write must be true** when any `write_scope` entry is non-empty.
3. **command_execution must be true** when `validation_commands` is
   non-empty and the SPEC requires command execution.
4. Every boolean in `capability_contract` must be explicitly `true` or
   `false` — never missing, `null`, or a non-boolean value.

A SPEC whose `capability_contract` is missing a required capability or
contains an invalid value is a blocking defect. Do not freeze. The
canonical contract definition in `aota-canonical-spec-contract` is the
single authority for required capabilities per `spec_kind`.

## 8. Evidence, review, and closure

Use `record_work_item_evidence` for bounded references only: task ID and
SPEC revision/SHA/start receipt; result artifact/status/summary; review
artifact/verdict/summary; or validation/deploy/smoke receipt. Never store raw
reports, logs, diffs, stack traces, environment, or credentials in a Plan.

Invoke Reviewer for medium/high risk, multi-file/cross-module work, scope drift,
substantial acceptance criteria, insufficient card evidence, security/schema/
runtime contracts, or user request. It may be skipped for small deterministic,
low-risk, directly verifiable work. Reviewer returns evidence only; task-main
chooses `closed`, `needs_fix`, `needs_input`, or `blocked`.

Before closure verify terminal state, acceptance criteria, validation tier, scope,
review requirement, residual risk, Human Checkpoint, evidence references, and
whether a follow-up Work Item is needed. If not satisfied, use `needs_fix →
ready` and create a revised/new SPEC; do not alter a frozen one.

## 9. Failure and reconciliation

Profile Task launchers distinguish `global_hermes_home`, the parent
Profile-local home, and the target Profile-local home. A Named parent must
never become the credential authority: credential bootstrap receives an
explicit canonical global root and target home. Launcher initialization,
profile/home resolution, credential bootstrap, runner resolution, worker
execution, and finalization all use the bounded redacted
`worker.<task_id>.log`. An early launcher failure creates the canonical
completion receipt and failure handoff even when no worker Card/Result exists;
the handoff preserves the frozen task/spec/workspace binding and sets
`needs_diagnosis=true`.

- Revision conflict: reopen the canonical Plan, reevaluate the intended single
  operation, and never blind-retry.
- Audit reconciliation required: stop Plan mutation and require a Human
  Checkpoint/dedicated reconciliation task.
- Result exists but Plan evidence update fails: retain the result, record
  reconciliation pending, and never rerun the worker.
- Projection is stale: `aota_plan_open` canonical output remains truth; record
  repair work without blocking unrelated reads.

## 10. Human Checkpoint and session handoff

Stop for explicit human confirmation before deploy, restart, reload, runtime
profile activation, live worker work, host filesystem work, WebUI override,
Compose, runtime env/principal/authority injection, migration, credential or
permission change, irreversible operation, or audit reconciliation. State the
operation, why, exact bounded action, expected evidence, and resume condition.
No reply means `NEEDS_INPUT`, never self-continue.

Create a durable session handoff only after a decision closure plus durable
artifact. Good cuts: Plan review findings adopted, Plan approved, frozen SPEC
before large work, closed Work Item, completed milestone, or noisy runtime
debug. Do not cut during unstable classification, unresolved review/SPEC
questions, a running task, or before consuming a Reviewer result.

A P1/P2 handoff contains `handoff_id`, project/workspace/Plan/revision, completed
work, state, active milestone/work item/SPEC/task, confirmed decisions, open
questions, artifacts, next action/executor, checkpoint requirement, and
`do_not_reopen`. P0 may use a lighter handoff, but not a chat summary alone.

## 11. Tool table and forbidden behavior

| Moment | Explicit tool/action |
|---|---|
| bounded routing | `aota_work_classify` |
| P1/P2 create | `aota_plan_create` |
| Plan structure/status/decision | `aota_plan_update` one operation |
| Plan-linked draft / refresh / freeze | `aota_task_spec_create` / `aota_task_spec_update` |
| successful task start | `aota_plan_update(operation=link_task)` |
| task/review result | `record_work_item_evidence` |
| closure | `set_work_item_status=closed` by task-main |

Never make every request a Plan, every Plan an Architect review, P2 automatically
A2, P0 automatically A0, or task completion/reviewer pass a closure. Never give
Plan write to workers/Architect/Reviewer, auto-dispatch tasks, auto-link,
auto-record evidence/closure, bypass an audit gate, retry a failed link by
restarting work, or claim source preparation is runtime activation.

## 12. Current activation status

Source configuration enables task-main's `aota_work_intake`, `aota_plan_read`,
and `aota_plan_write` toolsets under the fail-closed authority boundary. No
trusted principal or authority is injected by this skill or configuration.
Runtime deployment, reload, profile activation, and live Plan mutation remain
deferred and require a Human Checkpoint.

## Global credential authority

Every Named Profile Task uses one global Hermes credential authority. The
launcher keeps `HERMES_HOME` at the normalized global root and selects the
Named Profile only with `hermes -p <profile>`. API-key providers read only
`<global>/.env`; OAuth providers let Hermes discover `<global>/auth.json`.
Profile-local `.env`, `profiles/default/.env`, inherited parent credentials,
and caller-selected env paths are not fallback sources. Missing or unsupported
credentials fail closed at `credential_bootstrap` with a bounded log,
completion receipt, and failure handoff.

Profile Task launch is shell-free: `_profile_task_start.py` writes an atomic,
task-local launch manifest and starts only `_profile_task_launcher.py
--manifest <path>` through the background rail. The launcher resolves the
global credential authority, builds a closed child environment, and starts the
Hermes worker with `argv` and `shell=False`. A prompt is read from a task-local
file and passed as one literal `-z` argument. Python supervision owns timeout,
process-group termination, primary finalization, fallback finalization, and
durable redacted diagnostics. `done` requires exit code zero, a valid terminal
worker outcome, and a canonical receipt with `status=done`; wakeups must never
invent `done` when a receipt is absent.

## Skill Loading Map

This is a minimal routing map. It does NOT duplicate the full field matrix,
artifact reference schema, full lifecycle Phase, all pitfalls, or full
reviewer contract. Each entry names the phase, the required Skill or
reference, the load timing, and a fallback.

For SPEC routing, preserve the canonical `spec_id` and `spec_kind` fields;
the reference Skill owns their full contract.

| Phase | Required Skill / Reference | Load Timing | Fallback |
|-------|---------------------------|-------------|----------|
| Intake / classification | `aota-work-classify-and-plan-gate` (reference) | Before SPEC creation | Inline classification rules in this Skill (Section 2) |
| SPEC create / freeze | `aota-canonical-spec-contract` (reference) | During SPEC creation | Inline SPEC rules in this Skill (Section 7) |
| SPEC pre-submit checklist | `aota-canonical-spec-contract` § Pre-submit Checklist (reference) | Before SPEC freeze | Inline SPEC rules in this Skill (Section 7) |
| SPEC validation / structured error | `aota-canonical-spec-pitfalls` (reference) | On validation/review failure | Inline stop conditions in this Skill |
| SPEC structured error | `aota-tool-failure-fallback` (reference) | On tool failure / structured error | Inline fallback guidance in this Skill |
| Task start / wait / handoff | `aota-task-lifecycle` (reference, also active in project-steward) | After freeze, during execution | Inline lifecycle rules in this Skill (Section 7) |
| Coder execution | `aota-spec-driven-implementation` (active in coder) | Dispatched to coder | N/A — coder owns execution |
| Review execution | `aota-implementation-review` (active in reviewer) | Dispatched to reviewer | N/A — reviewer owns review |
| File access strategy | `workspace-file-access-strategy` (reference) | When reading workspace files | Inline read-tool guidance |
| Closure | `aota-multi-phase-doc-closure` (reference) | Before Work Item closure | Inline closure rules in this Skill (Section 8) |
| Tool failure / structured error | `aota-tool-failure-fallback` (reference, also active in project-steward) | On first tool failure — do not wait for repeated failures | Inline fallback guidance |
| Workspace diagnostics | `aota-workspace-diagnostics` (reference, also active in project-steward) | On missing workspace context | Inline workspace model rules |
| Workspace model | `aota-workspace-model` (reference, also active in project-steward) | When resolving workspace/project IDs | Inline workspace model rules |
| Plugin/tool development | `aota-plugin-tool-development` (reference in task-main, coder) | When creating a SPEC for plugin/tool/toolset development | N/A — development Skill owns the full SOP |
| Skill development | `aota-skill-development` (reference in task-main, project-steward) | When creating a SPEC for Skill development or binding | N/A — development Skill owns the full SOP |

Reference Skills are loaded by task-main but are not in `active_skills` in
the profile runtime assembly. They provide detailed contract content that
this orchestration Skill routes to without duplicating.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active and reference Skills
  per Profile; runtime projections are built from this manifest, not from
  file-system enumeration.
- Profile-local plugin and Skill projections are required when the Hermes
  loader uses profile home (`AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/plugins/`,
  `AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/skills/`). Verify the projection exists with hash
  parity against the canonical source.
- Config declaration (`profiles/<name>/config.yaml`) is not runtime availability
  evidence. A toolset may be declared in config but fail to register.
- SOUL declaration is not Skill-load evidence. A Skill may be referenced in
  SOUL.md but not loaded if the Skill file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence (tool list, dispatch
  result, Skill-loaded prompt inspection) or actual Profile Task execution.
  Hash parity is deploy evidence, not runtime evidence.
- Managed deploy and recreate requirements: after a managed deploy, all
  importing processes must be recreated. Agent-only recreate is allowed only
  when evidence proves WebUI is unaffected.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply deploy PASS, and deploy PASS does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source
  implementation. Skill content and lifecycle belong to the owning Profile.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source and must not be added to the managed manifest.
- No generic `/aota-runtime` file access; use bounded tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`). Architect/reviewer
  subject review uses `aota_subject_task_artifact_open` with only
  `SPEC.md`, `scope.json`, and `meta.json`; it never accepts a runtime path.


---
*W0 provenance: migrated from legacy `aota-hermes-tools` (SHA c2e0c4d) to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`.*
