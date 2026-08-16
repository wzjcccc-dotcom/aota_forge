# Issue #9 · M3-A0 Accepted-Base Reconnaissance — Summary

The machine-readable artifact `a0-input-map.json` is authoritative for this lane;
this file is a review digest only.

- Base: `ccce2e02c40ec92fe1717744e801ddcb5b01095c` (M2 known-good checkpoint, verified)
- Branch: `aota/m3/a0-reconnaissance` · Worktree: `.aota-worktrees/aota_forge/m3/a0-reconnaissance`
- Scope: reconnaissance + classification only — no graph/authority/lease/CAS implementation,
  no lifecycle mutation, no current-* cutover.

## What was mapped

| Inventory | Count | Notes |
|---|---|---|
| Source seams (canonical concepts in Forge source) | 14 | project/workspace/Plan/milestone/work-item/operation/context/principal/correlation/result/trusted metadata/trusted resources/runtime/git/host/drift seams |
| Subject-identity ambiguities | 16 | Work Item, Profile Task, SPEC, decision, followup, handoff, Plan, Milestone, project, workspace, … |
| Authority candidates | 14 | possession of ID, current task pointer, project binding/decision, approval, principal, trusted config, revisions, registries |
| Current-pointer inventory | 6 | manifest `active_plan_id`, Plan projection fields, legacy session `current_*`, legacy plan/spec bindings, legacy plan.json pointers |
| Migration inputs | 25 | 20 evidence-only, 3 conservative bootstrap candidates, 1 historical, 1 projection, 3 forbidden |
| Open design questions | 13 | Work Item == Subject?, review-as-subject, decision-as-subject, lease schema, principal type, transaction scope, bootstrap ordering, cutover rule … |

## Classification highlights

- **AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE (conservative, 3):** project manifests
  (`project.yaml`), normalized Portable Plan document, workspace registry.
- **PROJECTION_ONLY:** `current_*` family (manifest `active_plan_id`, Plan body
  `CURRENT_MILESTONE`/`PLAN_STATUS`/`CURRENT_BLOCKER`/`HANDOFF_STATE`), control projections,
  legacy operator inbox (pure derived view).
- **FORBIDDEN_AUTHORITY_SOURCE (3):** Git history, Issue Event Log, control comments —
  each with bounded evidence utility.
- **MIGRATION_EVIDENCE_ONLY:** all legacy lifecycle material (Profile Task, SPEC,
  completion, decision, followup, handoff, plan.json, decisions store, handoffs,
  audit ledgers, authority docs, verify scripts, receipts).

## Carried-forward design inputs (not fixed)

- **NF2** — trusted raw-path library context lacks principal enforcement: principal is a
  free-form self-asserted string (`library`/`cli`/`hermes`), never enforced, absent from
  audit, uncoupled from trusted roots; M3-A2 decides typed principal, lease-scoped
  trusted channel, audit inclusion.
- **I9-B008** — ingress success normalization assumes a dict handler payload outside any
  guard (`payload.get(...)` after the guarded call in `core/ingress.py`); non-dict payload
  escapes the canonical envelope. M3-B prerequisite: yes.

## Regression ownership (16 classes, dispositions unchanged)

M3-relevant (9): `B011`, `B013`, `B014`, `B014-F`, `ACTIVATE-R-current-binding`, `WCTX-1`,
`BIND-1`, `RC2-1`, `RECOVERY-1` → proposed owner **M3-A** (design input only).
Not M3 (7): `B014-F1`, `RUNNER-1`, `E2E-1` (M5), 4 eliminated-by-construction classes (M2).
`REGRESSION_NOT_YET_IMPLEMENTED_COUNT=12`, `UNSUPPORTED_SUCCESS_CLAIMS=0`.

## Validation

- `m3_a0_reconnaissance_guard.py` — **PASS** (277 checks)
- `m2_regression_corpus.py` — **PASS** (16 classes, no disposition drift)
- `m2_regression_corpus.py --self-test` — **PASS**
- Negative guard proof (isolated /tmp copy: Git history promoted to bootstrap candidate +
  current pointer classified as future authority) — guard **FAILS** as required
  (`M3_A0_NEGATIVE_GUARD_PROOF=PASS`)
