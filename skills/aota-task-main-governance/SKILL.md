---
name: aota-task-main-governance
description: Governance 1.x procedure for task-main — Plan Issue body authority, exactly five managed comments, ref-scoped read-before-write reconciliation, progress/defect/appendix/decision records, Milestone close, known-good checkpoint and frontier integration
category: governance
tags: [aota, task-main, governance, plan-issue, managed-comments, git-lifecycle]
---

# AOTA Task-Main Governance 1.x (task-main-owned)

> `SKILL_IS_AUTHORITY=no`. This is the canonical detailed procedure for
> operational governance that task-main performs **directly** during the
> Governance 1.x transition. The AF runtime decides authorization; guidance
> is never authority. Tool primitive contracts live in the canonical
> operation descriptors (`role.bootstrap` OPERATION_GUIDANCE / `help` / this
> file's §Tools). GitHub transport mechanics have exactly one canonical owner
> — this file — and are never duplicated into the base task-main Skill or
> SOUL.

## Why task-main owns this now

`TASK_MAIN_TEMPORARY_STEWARDSHIP=yes`
`PROJECT_STEWARD_NEW_NORMAL_DELEGATION=no`
`PROJECT_STEWARD_LEGACY_COMPATIBILITY=yes`

You do NOT delegate governance reconciliation to a project-steward child in
the normal path. You read the authoritative state yourself, decide the
semantic update, and write it through the governed AF primitives. The legacy
project-steward role/Skill remain loadable for compatibility only;
Governance 2.0 will later convert stewardship into a subsystem/plugin.
`AOTA_TASK_LIFECYCLE_REINTRODUCED=no` — the historical lifecycle Skill stays
outside your normal path.

## Authoritative model (current #59 truth)

1. **The Plan GitHub Issue body is the normative Plan authority.**
   An ordinary task-main session is **unbound**: it carries no Plan or
   session binding. Every Plan-bound governance/Git operation supplies the
   canonical `plan_ref` (`owner/repo#number`) in its arguments. The ref
   locates the current server-side authority (live Plan + trusted project +
   lifecycle authority); a ref is an authority **locator**, never authority
   itself, and a session/directory/profile grants nothing.
2. **Exactly five canonical managed comments** carry the living governance
   state. Each managed comment's body carries the marker
   `COMMENT_ROLE=<role>` as its first line, where `<role>` is one of:
   - `milestone_progress_index` — current milestone/work/status truth
   - `development_notes` — currently-valid engineering truth per work item
   - `defect_register` — open/closed plan defects (`I<issue>-B<nnn>` ids)
   - `plan_appendix` — evidence references (smoke/observation artifacts)
   - `decision_change_log` — material decisions only (`D<nn>` entries)
   YOU decide which comment is which by reading comment content. The Control
   Plane never assigns semantic names — `github.issue.comments.read` returns
   raw facts (`comment_id`, `author`, `updated_at`, bounded body). If a role
   is missing or duplicated, that is a governance defect: record it in the
   defect register and reconcile deliberately; never guess silently.

## Core loop: read-before-write, always

Before ANY governed mutation:

1. `github.issue.read {plan_ref}` (card view) — Plan body truth
   (`state`, `updated_at`, `body_digest`).
2. `github.issue.comments.read {plan_ref}` — current managed comments
   (`comment_id`, `updated_at`, `body`, `body_digest`). Hydrate a truncated
   body through the attached `output_ref` claims (`result.hydrate`) before
   rewriting that comment wholesale.
3. Compose the new content from the freshest read + your semantic judgment.
4. Write with the CAS token from the read:
   - comment update: `github.issue.comment.update {plan_ref, comment_id,
     body, expected_digest}` — `expected_digest` is the `body_digest` you read;
   - issue body/state update: `github.issue.update {plan_ref, …,
     expected_updated_at}` with `body`, or a bounded
     `section_marker`/`section_content` upsert, or `state` for Plan closure.
5. A `GITHUB_CAS_CONFLICT` means someone (or something) moved the state
   after your read. Re-read, re-reason, re-apply. Never blind-retry the old
   write, and never weaken the CAS to "make progress".

Stale snapshots cannot silently overwrite newer authoritative content;
identical content is an idempotent no-op (`already_applied=true`).

## Destructive body semantics (M2/W3 — explicit)

```text
github.issue.update.body            = WHOLE BODY REPLACEMENT
  not a patch, not a merge, not an append. The candidate replaces the
  complete normative Plan body. For a recognized Portable Plan Issue the
  candidate MUST be a complete structurally valid Portable Plan preserving
  the Plan identity (PROJECT_ID + kind); an invalid/incomplete candidate is
  rejected with INVALID_INPUT / PLAN_BODY_INVALID BEFORE any GitHub mutation.
  Whole-body replacement is only for genuinely necessary full rewrites.

github.issue.update.section_marker + section_content
  = BOUNDED_SECTION_UPSERT (preferred for bounded Plan-state changes)

github.issue.update.state
  = open|closed (Plan closure; not a body rewrite)

expected_updated_at                 = CAS precondition (required)
```

Default guidance: use `section_marker` + `section_content` for bounded
Plan-state changes; use `state=closed` for closure; send a whole body only
when the complete replacement genuinely is the Plan.

## No live mutation probing (M2/W3)

Never probe mutation schemas against live authoritative Plan data. If an
operation shape is unclear:

1. read the relevant Skill (this file / the base Skill),
2. call `help(operation=...)` for the exact canonical mechanics,
3. if still unclear, stop with `needs_input`.

Do not send synthetic live mutation payloads to learn semantics. A failed
experiment can corrupt normative Plan authority; there is no "test write"
against the real Plan.

## Milestone procedures

### Entry (bounded refinement)
- Verify the milestone is already materialized in the existing Plan; extend
  the current Plan rather than inventing successor issues.
- Refine the milestone into ordered Work Items with explicit DAG +
  acceptance. Record the refinement in `milestone_progress_index`; a
  material change of direction also earns a `decision_change_log` entry.
- Record the user's approval truth verbatim-semantic (e.g.
  `M5_USER_APPROVAL_SATISFIED=yes`) with its basis. You NEVER mint approval
  yourself; absent approval means stop at the user gate.

### During
- Keep `milestone_progress_index` current: `CURRENT_WORK_ITEM`, statuses,
  approval facts, base SHA/entry state.
- Keep `development_notes` as the currently-valid engineering truth per Work
  Item (what was built, seams touched, validation evidence) — replace
  superseded statements, keep it short.
- Register real defects with stable ids (`I<plan-number>-B<nnn>`) in the
  defect register; close them with the repair evidence reference.
- Put run/artifact references (smoke summaries, observation cards, session
  ids) in `plan_appendix` — references, not transcripts.
- Log only MATERIAL decisions (architecture, disposition changes, approval
  events) in `decision_change_log`. No routine operational noise.

### Close
1. Integrated review outcome recorded (e.g. `M5_RV1=PASS`).
2. Known-good checkpoint: commit the accepted worktree frontier
   (`git.checkpoint {plan_ref, message, expected_head?}`), then FF-only
   integration (`git.integrate {plan_ref, expected_old_sha}` with the
   `expected_old_sha` you observed via `git.status`), then sync the trusted
   remote (`git.push {plan_ref, expected_remote_sha?}`) when governance
   requires local == remote. Verify `LOCAL_MAIN == ORIGIN_MAIN ==
   <checkpoint sha>` from operation results; never force.
3. Update the progress index (`*_STATUS=completed`, `*_ACCEPTED=yes`,
   `KNOWN_GOOD_CHECKPOINT=<sha>`), development notes final state, appendix
   with the checkpoint/integration evidence.
4. Only for PLAN closure: update the issue body status section
   (`github.issue.update` with `section_marker`/`section_content`) and, when
   the Plan is genuinely complete, `state=closed`.
5. Checkpoint reconciliation at any session start: compare the recorded
   known-good SHA against `git.status` reality; a mismatch is a defect or a
   drift signal, not something to paper over.

## Git lifecycle stance

You own the semantic decisions: *this Work is accepted*, *a checkpoint is
appropriate now*, *the Milestone should integrate*. The Control Plane
mechanically enforces: your authority, the trusted worktree located by
`plan_ref`, expected-old CAS, FF-only, the configured remote/ref. The
lifecycle operations additionally require the **current** Milestone approval
server-side; `MILESTONE_APPROVAL_REQUIRED` is the user gate — stop, report,
never bypass. `AUTHORITY_DENIED` means "not configured/not yours", never a
puzzle to bypass. Do not create meaningless commits to exercise the tools;
commit accepted work at real decision points. Destructive verbs (reset
--hard, clean, force push, rebase, arbitrary argv) do not exist here — do not
seek them via restricted shell (`GIT_VIA_RESTRICTED_SHELL=no`,
`GITHUB_VIA_RESTRICTED_SHELL=no`; `aota-restricted-shell` is residual
fallback only).

## Tools (thin primitive contract — mechanics canonical here for the transition; descriptions stay in OPERATION_GUIDANCE / help)

```text
github.issue.read            {plan_ref, view? card|full}
github.issue.comments.read   {plan_ref, max_comments?}
github.issue.update          {plan_ref, body? | section_marker+section_content? | state? , expected_updated_at}
github.issue.comment.update  {plan_ref, comment_id, body, expected_digest?}
git.status / git.diff        {plan_ref} (bounded reads of the trusted worktree)
git.checkpoint               {plan_ref, message, expected_head?}
git.integrate                {plan_ref, expected_old_sha}     (FF-only, CAS)
git.push                     {plan_ref, expected_remote_sha?} (FF-only, never force)
```

All through `aota.invoke` — the only Agent-facing AF MCP tool. Large results
arrive `by_ref`; hydrate via the attached claims. For the exact current
canonical arguments call `help(operation=...)`.

## Stop conditions

- Missing user approval for the next construction phase → stop at the gate
  and report exactly what approval is needed.
- Governance state you cannot reconcile confidently from reads → re-read,
  then register a defect; do not mutate speculatively.
- `AUTHORITY_DENIED` is a boundary, not a puzzle: it never licenses an
  alternative transport (raw gh, generic API, shell) for the same goal.
