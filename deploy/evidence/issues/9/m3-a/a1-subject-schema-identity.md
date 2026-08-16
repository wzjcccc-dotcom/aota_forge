# Issue #9 · M3-A1 Subject Graph Canonical Schema & Identity — Summary

The machine-readable artifact `a1-subject-schema-identity.json` is authoritative for this lane;
this file is a review digest only.

- Base: `10b8018ecf618cd7ff4c2b73f962356af044669f` (A0 accepted-base, verified)
- Branch: `aota/m3/a1-subject-schema-identity` · Worktree: `.aota-worktrees/aota_forge/m3/a1-subject-schema-identity`
- Scope: design proof only — no storage engine, no graph mutation implementation,
  no authority/lease engine, no runtime behavior, no current-* cutover.

## Canonical schema (executor-neutral logical records)

| Record | Purpose | Notes |
|---|---|---|
| Workflow | durable semantic intent (what & why) | new intent = new Workflow + lineage, never in-place rewrite |
| Subject | durable identity anchor + small mechanical state | kinds: Workspace / Project / Plan / Work; state PROPOSED/ACTIVE/COMPLETED/SUPERSEDED |
| Execution | one attempt to advance a Subject | retry/executor change = new Execution under same Subject |
| Completion | immutable terminal outcome of one Execution | append-only; authoritative failure surface (B014-F) |
| Decision | durable semantic decision owned by one Subject | no authoritative current pointer; supersede = new record |
| FollowupEdge | durable parent→child lineage (alias ParentChildEdge) | requires `source_decision_ref`; no heuristic parents, no silent cycles |

## Identity rule

- Every Subject ID is **deterministic** (canonical hash of authoritative bootstrap input:
  workspaces.json, project manifest, Portable Plan) **or minted** (ID Broker, issued once,
  non-inferable).
- `SUBJECT_ID_IS_AUTHORITY=no` — IDs identify only, never authorize.
- **PlanSubject identity is stable across Plan document revisions**: `plan_id + owning
  ProjectSubject identity + subject namespace`; content digest is revision/evidence data,
  never identity. Ordinary Plan revisions keep the same PlanSubject ID as revisioned
  records/decisions; only an explicitly materialized new semantic Plan intent creates a
  child Subject with decision-backed lineage (`PLAN_IDENTITY_REVISION_POLICY`).
- Identity never depends on title, timestamps alone, newest matching, executor, Hermes
  session, Profile Task, filesystem, current pointers, LLM similarity, or Git SHA alone.
- Profile Task ID / Hermes session ID / current pointers are **not** canonical Subject
  identity; Hermes identity is not ontology; executor refs (`executor_kind`,
  `executor_execution_ref`) are opaque adapter-private.
- Canonical record fields are partitioned exactly: `immutable_fields ∪ mutable_fields ==
  canonical_fields`, no overlap; Completion/Decision/FollowupEdge are fully immutable;
  Subject `mechanical_state` and Execution `mechanical_status` are the only mutable
  mechanical fields; Workflow mutability is limited to named non-semantic annotations.

## Resolved decisions (OQ-M3A1-01..06)

| Question | Decision |
|---|---|
| 01 Work Item == Subject? | No — Work Item is MIGRATION_EVIDENCE_ONLY read-model; successor is WorkSubject (minted) |
| 02 Plan/Milestone aggregate? | Plan is one aggregate (PlanSubject); milestone = Plan-internal reference, not a subject kind |
| 03 Review a Subject? | No special ReviewSubject ontology kind. Context-dependent: (1) review of an existing Subject outcome = Decision record on the reviewed Subject targeting the Execution/outcome; (2) new semantic review task = generic WorkSubject with standard Workflow/Execution/Completion lifecycle (normally child lineage), result as Decision; (3) independent review identity only via a separately materialized review task — reviewer role/executor identity/review artifacts alone never create identity |
| 04 Legacy decisions → Subjects? | No — Decision record owned by one Subject; no authoritative current pointer |
| 05 Followup Subject or projection? | Child Subject with explicit decision-backed FollowupEdge; `SUBJECT_BINDING_ZERO_ONE_MANY` governs bindings |
| 06 Workspace/Project kinds? | Separate kinds with parent/child edge; project is not the top aggregate |

Additional semantic decisions (D-M3A1-01..07): new semantic intent → new Subject with
lineage; `RETRY_CREATES_NEW_SUBJECT=no`; `CHANGE_EXECUTOR != CHANGE_SUBJECT`; Completion
immutable/append-only; Decision durable without current pointer; followup lineage required;
review distinctions context-sensitive.

## A0-driven regression design proofs

B011, B013, B014, B014-F, ACTIVATE-R-current-binding, WCTX-1, BIND-1, RECOVERY-1 — each
carries `A1_DESIGN_INVARIANT` + `A1_PROOF_STATUS=DESIGN_PROVEN` + `FUTURE_IMPLEMENTATION_REQUIRED=yes`.
A1 proves schema/identity only; no implementation-dependent failure is claimed solved.

## Guard & negative proofs

`scripts/m3_a1_subject_schema_guard.py` (stdlib-only, deterministic) validates the artifact,
exact base SHA, exact top-level markers (`GRAPH_CANONICAL_SCHEMA=defined`,
`SUBJECT_IDENTITY_RULE=defined`), stable PlanSubject identity/revision policy, record field
partition, identity flags, OQ decisions, review semantic cases (REVIEW-CASE-1..3),
executor-private boundary, durable graph authority, no storage engine, no graph mutation
implementation (static scan), and preserved A0 classifications (cross-checked against
`a0-input-map.json`). Negative proofs (guard exit 1) flip each of
`PROFILE_TASK_ID_IS_SUBJECT_ID`, `CURRENT_POINTERS_ARE_AUTHORITY`,
`CHANGE_EXECUTOR_CREATES_NEW_SUBJECT`, `HEURISTIC_PARENT_SELECTION_ALLOWED` to `yes`.
