#!/usr/bin/env python3
"""Deterministic M3-B8 Projection Reconstruction validator (Issue #9, lane M3-B8).

Proves that a storage-neutral projection can be rebuilt deterministically and
exclusively from canonical AOTA Forge graph state, without any graph write,
lease, authority engine, persistence, or B9 binding behavior.

Primary acceptance signals:
  B8_DETERMINISTIC_FULL_REBUILD_CASE
  B8_STALE_REVISION_CASE
  B8_GRAPH_READ_ONLY_CASE
  B8_LEGACY_POINTER_NON_AUTHORITY_CASE
  B8_FOLLOWUP_PROJECTION_CASE

Everything runs against the isolated, NON-AUTHORITATIVE B6 ``TransactionStore``.
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``; ``PROJECTION_RUNTIME_PRODUCTION_ACTIVE=no``;
``PROJECTION_PERSISTENCE_IMPLEMENTED=no``; ``CUTOVER_PERFORMED=no``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

NOW = datetime(2030, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []
_LEASE_COUNTER = [0]


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:400]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_store():
    from aota_forge.core.identity.broker import IdBroker
    from aota_forge.core.transaction import TransactionStore

    return TransactionStore(broker=IdBroker())


def _ids_and_refs():
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    workflow_id = make_id(IdKind.WORKFLOW, "b8-wf")
    parent_id = make_id(IdKind.SUBJECT, "b8-parent", sub_kind=SubjectKind.PLAN)
    child_id = make_id(IdKind.SUBJECT, "b8-child", sub_kind=SubjectKind.WORK)
    other_id = make_id(IdKind.SUBJECT, "b8-other", sub_kind=SubjectKind.PROJECT)
    wf_ref = make_object_ref(IdKind.WORKFLOW, workflow_id)
    parent_ref = make_object_ref(IdKind.SUBJECT, parent_id)
    other_ref = make_object_ref(IdKind.SUBJECT, other_id)
    return workflow_id, parent_id, child_id, other_id, wf_ref, parent_ref, other_ref


# --- direct seeding (fixed IDs) for determinism / insertion-order ----------


def _seed_workflow(store, wf_id):
    from aota_forge.core.graph import records

    store._put_staged(records.workflow(wf_id, semantic_intent="b8 fixture", creation_context={"lane": "M3-B8"}))


def _seed_subject(store, iid, kind, wf_id, revision=1):
    from aota_forge.core.graph import records

    store._put_staged(
        records.subject(
            iid, kind, workflow_ref=wf_id,
            mechanical_state={"revision": revision, "state": "open"}, id_derivation="fixture",
        )
    )


def _seed_execution(store, exec_iid, subj_iid, started_at, status="running"):
    from aota_forge.core.graph import records

    store._put_staged(
        records.execution(
            exec_iid, subj_iid, "test", mechanical_status=status, started_at=started_at,
        )
    )


def _seed_completion(store, comp_iid, exec_iid, outcome):
    from aota_forge.core.graph import records

    store._put_staged(records.completion(comp_iid, exec_iid, outcome, recorded_at=_iso(NOW)))


def _seed_decision(store, dec_iid, subj_iid, kind="followup", statement="s"):
    from aota_forge.core.graph import records

    store._put_staged(records.decision(dec_iid, subj_iid, kind, statement, decision_time=_iso(NOW)))


def _seed_edge(store, edge_iid, parent_iid, child_iid, dec_iid):
    from aota_forge.core.graph import records

    store._put_staged(
        records.followup_edge(edge_iid, parent_iid, child_iid, dec_iid, rationale="b8", created_at=_iso(NOW))
    )


# --- B7-transition based graph (faithful canonical source) -----------------


def _lease(principal, target, operation, *, revision, decision_basis=None):
    from aota_forge.core.capability_lease import CapabilityLease

    _LEASE_COUNTER[0] += 1
    lease_id = f"b8-{operation}-{_LEASE_COUNTER[0]}"
    return CapabilityLease(
        lease_id=lease_id, principal=principal, operation=operation, target=target,
        scope={"mode": "write"}, issued_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=10), expected_revision=revision,
        authority_basis=("b8-fixture",), decision_basis=decision_basis,
    )


def _decision_evidence(decision, target, *, operation="create_followup_subject", revision):
    from aota_forge.core.authority import MaterializedDecisionEvidence

    return MaterializedDecisionEvidence.from_decision(
        decision, operation=operation, target=target,
        expected_revision=revision, scope={"mode": "write"},
    )


def _parse_ref(text: str):
    from aota_forge.core.identity.refs import parse_object_ref

    return parse_object_ref(text)


def _lifecycle_store():
    """Full canonical lifecycle graph built through B7 transitions."""
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.transitions import (
        CreateExecutionRequest, RecordCompletionRequest, RecordDecisionRequest,
        CreateFollowupSubjectRequest, create_execution, record_completion,
        record_decision, create_followup_subject,
    )

    trusted = bind_trusted_context(
        principal_id="b8-operator", principal_type="operator",
        provenance="b8-fixture", channel="fixture", freshness="b8-1",
    )
    principal = trusted.principal
    workflow_id, parent_id, child_id, other_id, wf_ref, parent_ref, other_ref = _ids_and_refs()
    store = _make_store()
    _seed_workflow(store, workflow_id)
    _seed_subject(store, parent_id, "PlanSubject", workflow_id, revision=1)

    r_ce = create_execution(
        store, CreateExecutionRequest(
            trusted_context=trusted, subject_ref=parent_ref,
            lease=_lease(principal, parent_ref, "create_execution", revision=1),
            expected_revision=1, idempotency_key="b8-ce", trusted_time=NOW,
            executor_kind="test", mechanical_status="running",
        ),
    )
    exec_ref = _parse_ref(r_ce.created_objects[0])
    rev_after_exec = store.current_revision(parent_ref).revision_number

    r_comp = record_completion(
        store, RecordCompletionRequest(
            trusted_context=trusted, execution_ref=exec_ref,
            lease=_lease(principal, parent_ref, "record_completion", revision=rev_after_exec),
            expected_revision=rev_after_exec, idempotency_key="b8-comp", trusted_time=NOW,
            outcome="success",
        ),
    )
    comp_ref = _parse_ref(r_comp.created_objects[0])
    rev_after_comp = store.current_revision(parent_ref).revision_number

    r_dec = record_decision(
        store, RecordDecisionRequest(
            trusted_context=trusted, subject_ref=parent_ref,
            lease=_lease(principal, parent_ref, "record_decision", revision=rev_after_comp),
            expected_revision=rev_after_comp, idempotency_key="b8-dec", trusted_time=NOW,
            decision_kind="followup", statement="create bounded child",
        ),
    )
    dec_ref = _parse_ref(r_dec.created_objects[0])
    decision = store.decision(dec_ref)
    rev_after_dec = store.current_revision(parent_ref).revision_number

    ev = _decision_evidence(decision, parent_ref, revision=rev_after_dec)
    r_fu = create_followup_subject(
        store, CreateFollowupSubjectRequest(
            trusted_context=trusted, parent_ref=parent_ref, decision_ref=dec_ref,
            lease=_lease(principal, parent_ref, "create_followup_subject", revision=rev_after_dec, decision_basis=ev),
            expected_revision=rev_after_dec, idempotency_key="b8-fu", trusted_time=NOW,
            child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK, rationale="followup",
        ),
    )
    child_ref = _parse_ref(r_fu.created_objects[0])
    rev_after_fu = store.current_revision(parent_ref).revision_number
    return store, parent_ref, exec_ref, comp_ref, dec_ref, child_ref, rev_after_fu


def _store_snapshot(store) -> str:
    """Deterministic snapshot of canonical graph + revision state."""
    def keyed(records_iter):
        return sorted(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in records_iter)

    return json.dumps(
        {
            "subjects": keyed(s.canonical_fields() for s in store.subjects()),
            "executions": keyed(e.canonical_fields() for e in store._executions.values()),
            "completions": keyed(c.canonical_fields() for c in store._completions.values()),
            "decisions": keyed(d.canonical_fields() for d in store._decisions.values()),
            "edges": keyed(e.canonical_fields() for e in store._edges.values()),
            "revision_tokens": dict(store._revision_tokens),
            "retired": sorted(store._retired_ids),
            "accepted": sorted(store._accepted_ids),
        },
        sort_keys=True, separators=(",", ":"),
    )


def main() -> int:
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.projection import ProjectionRebuildService, ProjectionResultCode
    from aota_forge.core.projection import status as status_mod
    from aota_forge.core.projection import rebuild as rebuild_mod
    import aota_forge.core.projection as pmod

    workflow_id, parent_id, child_id, other_id, wf_ref, parent_ref, other_ref = _ids_and_refs()

    # =====================================================================
    # B8-N1 / B8-N2 / B8-N16 : single subject deterministic full rebuild
    # =====================================================================
    store1 = _make_store()
    _seed_workflow(store1, workflow_id)
    _seed_subject(store1, parent_id, "PlanSubject", workflow_id, revision=1)
    svc = ProjectionRebuildService(store1)
    r1 = svc.rebuild(parent_ref)
    check("B8-N1_single_subject_projects_deterministically",
          r1.code == ProjectionResultCode.PROJECTED
          and r1.projection.subject_ref == parent_ref.serialize()
          and r1.projection.subject_revision == 1
          and r1.projection.kind == "PlanSubject",
          f"code={r1.code} rev={r1.projection.subject_revision if r1.projection else None}")

    # B8-N2: rebuild twice -> byte/field-identical projection
    r1b = svc.rebuild(parent_ref)
    check("B8-N2_rebuild_twice_identical_projection",
          r1.projection.canonical_json() == r1b.projection.canonical_json(),
          f"len={len(r1.projection.canonical_json())}")

    # B8-N16: output independent of insertion order (fixed identical records)
    def build_insertion_order(reverse: bool):
        st = _make_store()
        _seed_workflow(st, workflow_id)
        _seed_subject(st, parent_id, "PlanSubject", workflow_id, revision=1)
        exec1 = make_id(IdKind.EXECUTION, "b8-exec-1")
        exec2 = make_id(IdKind.EXECUTION, "b8-exec-2")
        dec1 = make_id(IdKind.DECISION, "b8-dec-1")
        steps = [
            ("exec1", lambda: _seed_execution(st, exec1, parent_id, _iso(NOW - timedelta(seconds=5)), status="done")),
            ("exec2", lambda: _seed_execution(st, exec2, parent_id, _iso(NOW), status="running")),
            ("dec1", lambda: _seed_decision(st, dec1, parent_id, kind="review", statement="x")),
        ]
        for _, fn in (reversed(steps) if reverse else steps):
            fn()
        return st

    st_fwd = build_insertion_order(False)
    st_rev = build_insertion_order(True)
    pfwd = ProjectionRebuildService(st_fwd).rebuild(parent_ref)
    prev_ = ProjectionRebuildService(st_rev).rebuild(parent_ref)
    check("B8-N16_output_independent_of_insertion_order",
          pfwd.code == ProjectionResultCode.PROJECTED
          and pfwd.projection.canonical_json() == prev_.projection.canonical_json(),
          "canonical projections equal")

    # =====================================================================
    # B8-N3 / B8-N4 / B8-N5 : revision capture, staleness, no mutation
    # =====================================================================
    store_lc, parent_ref_lc, exec_ref_lc, comp_ref_lc, dec_ref_lc, child_ref_lc, rev_after_fu = _lifecycle_store()
    lc_svc = ProjectionRebuildService(store_lc)
    rev_before = store_lc.current_revision(parent_ref_lc).revision_number
    r_lc = lc_svc.rebuild(parent_ref_lc)
    rev_after = store_lc.current_revision(parent_ref_lc).revision_number
    check("B8-N3_subject_revision_captured_in_projection",
          r_lc.projection.subject_revision == rev_after_fu
          and r_lc.projection.subject_revision == rev_before,
          f"proj_rev={r_lc.projection.subject_revision} rev_before={rev_before}")

    # B8-N4: stale when source Subject revision changes
    built_from = r_lc.projection.subject_revision  # e.g. 5
    # advance the subject once more via a fresh decision transition
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.transitions import RecordDecisionRequest, record_decision
    trusted = bind_trusted_context(
        principal_id="b8-operator", principal_type="operator",
        provenance="b8-fixture", channel="fixture", freshness="b8-1",
    )
    principal = trusted.principal
    from aota_forge.core.capability_lease import CapabilityLease
    _LEASE_COUNTER[0] += 1
    adv_lease = CapabilityLease(
        lease_id=f"b8-advance-{_LEASE_COUNTER[0]}", principal=principal,
        operation="record_decision", target=parent_ref_lc, scope={"mode": "write"},
        issued_at=NOW - timedelta(seconds=10), expires_at=NOW + timedelta(seconds=10),
        expected_revision=rev_after_fu, authority_basis=("b8-fixture",),
    )
    record_decision(
        store_lc, RecordDecisionRequest(
            trusted_context=trusted, subject_ref=parent_ref_lc, lease=adv_lease,
            expected_revision=rev_after_fu, idempotency_key="b8-advance", trusted_time=NOW,
            decision_kind="note", statement="advance",
        ),
    )
    current_now = store_lc.current_revision(parent_ref_lc).revision_number
    stale_rev = status_mod.compare_revision(parent_ref_lc.serialize(), built_from, current_now)
    check("B8-N4_projection_stale_when_source_revision_changes",
          stale_rev.stale is True and built_from != current_now and stale_rev.built_from_revision == built_from,
          f"built_from={built_from} current={current_now}")

    # B8-N5: rebuild does not mutate revision
    store5 = _make_store()
    _seed_workflow(store5, workflow_id)
    _seed_subject(store5, parent_id, "PlanSubject", workflow_id, revision=1)
    svc5 = ProjectionRebuildService(store5)
    pre_snap = _store_snapshot(store5)
    pre_rev = store5.current_revision(parent_ref).revision_number
    svc5.rebuild(parent_ref)
    svc5.rebuild(parent_ref)
    post_rev = store5.current_revision(parent_ref).revision_number
    post_snap = _store_snapshot(store5)
    check("B8-N5_rebuild_does_not_mutate_revision",
          pre_rev == post_rev and pre_snap == post_snap,
          f"rev={pre_rev}->{post_rev} snapshots_equal={pre_snap == post_snap}")

    # =====================================================================
    # B8-N6 / B8-N7 / B8-N8 / B8-N9 : execution/completion/decision/followup
    # =====================================================================
    check("B8-N6_execution_projection_belongs_to_correct_subject",
          len(r_lc.projection.executions) == 1
          and r_lc.projection.executions[0].subject_ref == parent_ref_lc.internal_id.to_canonical()
          and r_lc.projection.current_execution is not None
          and r_lc.projection.current_execution.execution_ref == r_lc.projection.executions[0].execution_ref,
          f"execs={len(r_lc.projection.executions)} current={r_lc.projection.current_execution is not None}")

    check("B8-N7_completion_projected_through_execution_ownership",
          r_lc.projection.executions[0].completion is not None
          and r_lc.projection.executions[0].completion.outcome == "success"
          and not hasattr(r_lc.projection.executions[0].completion, "subject_ref"),
          f"completion={r_lc.projection.executions[0].completion is not None}")

    check("B8-N8_decision_projected_under_owning_subject",
          len(r_lc.projection.decisions) == 1
          and r_lc.projection.decisions[0].subject_ref == parent_ref_lc.internal_id.to_canonical()
          and r_lc.projection.decisions[0].decision_kind == "followup",
          f"decisions={len(r_lc.projection.decisions)}")

    edge = store_lc.edges()[0]
    fup = r_lc.projection.followups[0]
    check("B8-N9_followup_edge_projects_exact_relation",
          len(r_lc.projection.followups) == 1
          and fup.parent_subject_ref == edge.parent_subject_ref.to_canonical()
          and fup.child_subject_ref == edge.child_subject_ref.to_canonical()
          and fup.source_decision_ref == edge.source_decision_ref.to_canonical()
          and fup.child_subject_ref == child_ref_lc.internal_id.to_canonical(),
          f"parent={fup.parent_subject_ref} child={fup.child_subject_ref} dec={fup.source_decision_ref}")

    # =====================================================================
    # B8-N10 / B8-N11 : not found / wrong-kind fail closed
    # =====================================================================
    missing_ref = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "missing", sub_kind="work"))
    r_missing = svc5.rebuild(missing_ref)
    check("B8-N10_missing_subject_returns_not_found",
          r_missing.code == ProjectionResultCode.NOT_FOUND, f"code={r_missing.code}")

    wrong_kind_ref = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "not-subject"))
    r_wrong = svc5.rebuild(wrong_kind_ref)
    r_bare = svc5.rebuild("not-a-ref")
    check("B8-N11_wrong_kind_object_ref_fails_closed",
          r_wrong.code == ProjectionResultCode.INVALID_REFERENCE
          and r_bare.code == ProjectionResultCode.INVALID_REFERENCE,
          f"wrong_kind={r_wrong.code} bare={r_bare.code}")

    # =====================================================================
    # B8-N12 / B8-N15 : referential corruption does not silently disappear
    # =====================================================================
    st_corrupt = _make_store()
    _seed_workflow(st_corrupt, workflow_id)
    _seed_subject(st_corrupt, parent_id, "PlanSubject", workflow_id, revision=1)
    _seed_subject(st_corrupt, child_id, "WorkSubject", workflow_id, revision=1)
    dec_c = make_id(IdKind.DECISION, "b8-dec-corrupt")
    edge_c = make_id(IdKind.EDGE, "b8-edge-corrupt")
    _seed_decision(st_corrupt, dec_c, parent_id)
    _seed_edge(st_corrupt, edge_c, parent_id, child_id, dec_c)
    # corrupt: point the edge's source decision at a NON-existent decision
    from dataclasses import replace
    _orig_edge = st_corrupt._edges[edge_c.value]
    st_corrupt._edges[edge_c.value] = replace(
        _orig_edge, source_decision_ref=make_id(IdKind.DECISION, "missing-dec")
    )
    snap_before = _store_snapshot(st_corrupt)
    r_corrupt = ProjectionRebuildService(st_corrupt).rebuild(parent_ref)
    snap_after = _store_snapshot(st_corrupt)
    check("B8-N12_referential_corruption_not_silently_disappeared",
          r_corrupt.code == ProjectionResultCode.REFERENTIAL_ERROR,
          f"code={r_corrupt.code}")
    check("B8-N15_projection_failure_leaves_graph_unchanged",
          r_corrupt.code == ProjectionResultCode.REFERENTIAL_ERROR and snap_before == snap_after,
          f"snapshots_equal={snap_before == snap_after}")

    # =====================================================================
    # B8-N13 : legacy current_* not consumed as authority
    # =====================================================================
    st_legacy = _make_store()
    _seed_workflow(st_legacy, workflow_id)
    # legacy-style mechanical field claims status=open and a stale blocker, but
    # the graph has a completed execution -> derived status must come from graph.
    from aota_forge.core.graph import records
    st_legacy._put_staged(
        records.subject(
            parent_id, "PlanSubject", workflow_ref=workflow_id,
            mechanical_state={"revision": 1, "state": "open", "current_status": "open", "blocker": "stale-blocker"},
            id_derivation="fixture",
        )
    )
    exec_l = make_id(IdKind.EXECUTION, "b8-legacy-exec")
    comp_l = make_id(IdKind.COMPLETION, "b8-legacy-comp")
    _seed_execution(st_legacy, exec_l, parent_id, _iso(NOW), status="running")
    _seed_completion(st_legacy, comp_l, exec_l, "success")
    r_legacy = ProjectionRebuildService(st_legacy).rebuild(parent_ref)
    compat = r_legacy.projection.legacy_compat
    check("B8-N13_legacy_current_pointer_not_consumed_as_authority",
          r_legacy.projection.derived_lifecycle_status == "completed"
          and compat.get("current_status") == "completed"
          and compat.get("current_blocker") == "stale-blocker",
          f"derived={r_legacy.projection.derived_lifecycle_status} compat_status={compat.get('current_status')}")

    # =====================================================================
    # B8-N14 : projection cannot grant mutation authority
    # =====================================================================
    check("B8-N14_projection_cannot_grant_mutation_authority",
          r1.projection.mutation_authority() is False
          and not hasattr(svc, "store") and not hasattr(svc, "commit"),
          "no mutation authority / no write methods")

    # =====================================================================
    # B8-N17 / N18 / N19 / N20 : read-only + separation flags
    # =====================================================================
    proj_src = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "aota_forge" / "core" / "projection").glob("*.py"))
    )
    check("B8-N17_no_capability_lease_in_projection",
          "CapabilityLease" not in proj_src and "capability_lease" not in proj_src,
          "no lease import")
    check("B8-N18_no_authority_engine_for_read_projection",
          "AuthorityEngine" not in proj_src and "evaluate(" not in proj_src
          and "AuthorityRequest" not in proj_src,
          "no authority engine import")
    check("B8-N19_no_graph_write_method_invoked",
          ".store(" not in proj_src and "SubjectTransaction" not in proj_src
          and "ParentChildTransaction" not in proj_src and "create_execution(" not in proj_src,
          "no write/transition call in projection source")
    candidate_defs = [
        line.strip() for line in proj_src.splitlines()
        if line.strip().startswith("def ")
        and any(k in line for k in ("candidate", "bind", "recover", "enumerate_subject"))
    ]
    check("B8-N20_no_b9_candidate_binding_behavior",
          pmod.BINDING_RECOVERY_IMPLEMENTED is False
          and pmod.SUBJECT_CANDIDATE_QUERY_IMPLEMENTED_BY_B8 is False
          and not candidate_defs,
          f"candidate_defs={candidate_defs}")

    # =====================================================================
    # Primary acceptance signals
    # =====================================================================
    check("B8_DETERMINISTIC_FULL_REBUILD_CASE",
          r1b.projection.canonical_json() == r1.projection.canonical_json()
          and pfwd.projection.canonical_json() == prev_.projection.canonical_json(),
          "deterministic + insertion-order independent")
    check("B8_STALE_REVISION_CASE", stale_rev.stale is True, "stale on revision change")
    check("B8_GRAPH_READ_ONLY_CASE",
          pre_rev == post_rev and pre_snap == post_snap and ".store(" not in proj_src,
          "rebuild never writes")
    check("B8_LEGACY_POINTER_NON_AUTHORITY_CASE",
          r_legacy.projection.derived_lifecycle_status == "completed",
          "derived from graph, not legacy pointer")
    check("B8_FOLLOWUP_PROJECTION_CASE",
          len(r_lc.projection.followups) == 1
          and fup.source_decision_ref == edge.source_decision_ref.to_canonical(),
          "exact edge lineage")

    # =====================================================================
    # Negative reversion proof
    # =====================================================================
    negative = {
        "projection_writes_graph": ".store(" not in proj_src and snap_before == snap_after,
        "projection_advances_subject_revision": pre_rev == post_rev,
        "projection_uses_current_pointer_as_authority":
            r_legacy.projection.derived_lifecycle_status == "completed",
        "projection_selects_subject": pmod.SUBJECT_CANDIDATE_QUERY_IMPLEMENTED_BY_B8 is False,
        "projection_grants_mutation_authority": r1.projection.mutation_authority() is False,
        "projection_mutates_b7_transition_state":
            "create_execution(" not in proj_src and "record_decision(" not in proj_src,
        "projection_silently_ignores_broken_references":
            r_corrupt.code == ProjectionResultCode.REFERENTIAL_ERROR,
        "projection_creates_persisted_authority_store":
            pmod.PROJECTION_PERSISTENCE_IMPLEMENTED is False,
        "projection_implements_b9_binding": pmod.BINDING_RECOVERY_IMPLEMENTED is False,
    }
    check("B8_NEGATIVE_REVERSION_PROOF", all(v is True for v in negative.values()),
          json.dumps({k: bool(v) for k, v in negative.items()}, sort_keys=True))

    # =====================================================================
    # Module boundary / end-state flags
    # =====================================================================
    check("PROJECTION_RECONSTRUCTION_IMPLEMENTED_yes", pmod.PROJECTION_RECONSTRUCTION_IMPLEMENTED is True)
    check("GRAPH_IS_AUTHORITY_FOR_PROJECTION_yes", pmod.GRAPH_IS_AUTHORITY_FOR_PROJECTION is True)
    check("PROJECTION_IS_SUBJECT_AUTHORITY_no", pmod.PROJECTION_IS_SUBJECT_AUTHORITY is False)
    check("CURRENT_POINTER_AUTHORITY_no", pmod.CURRENT_POINTER_AUTHORITY is False)
    check("B8_GRAPH_ONLY_DETERMINISTIC_REBUILD_yes", pmod.B8_GRAPH_ONLY_DETERMINISTIC_REBUILD is True)
    check("PROJECTION_FULL_REBUILD_IMPLEMENTED_yes", pmod.PROJECTION_FULL_REBUILD_IMPLEMENTED is True)
    check("PROJECTION_REBUILD_DETERMINISTIC_yes", pmod.PROJECTION_REBUILD_DETERMINISTIC is True)
    check("PROJECTION_REVISION_SOURCE_subject_aggregate",
          pmod.PROJECTION_REVISION_SOURCE == "Subject_aggregate_revision")
    check("PROJECTION_STALENESS_USES_SUBJECT_REVISION_yes",
          pmod.PROJECTION_STALENESS_USES_SUBJECT_REVISION is True)
    check("B8_PERSISTED_PROJECTION_STORE_REQUIRED_no",
          pmod.B8_PERSISTED_PROJECTION_STORE_REQUIRED is False)
    check("PROJECTION_PERSISTENCE_IMPLEMENTED_no", pmod.PROJECTION_PERSISTENCE_IMPLEMENTED is False)
    check("PROJECTION_FAILURE_CAN_CORRUPT_CANONICAL_GRAPH_no",
          pmod.PROJECTION_FAILURE_CAN_CORRUPT_CANONICAL_GRAPH is False)
    check("PROJECTION_GRAPH_WRITE_COUNT_0", pmod.PROJECTION_GRAPH_WRITE_COUNT == 0)
    check("PROJECTION_CAPABILITY_LEASE_REQUIRED_no", pmod.PROJECTION_CAPABILITY_LEASE_REQUIRED is False)
    check("PROJECTION_AUTHORITY_ENGINE_REQUIRED_no", pmod.PROJECTION_AUTHORITY_ENGINE_REQUIRED is False)
    check("B8_GRAPH_REPOSITORY_WRITE_ALLOWED_no", pmod.B8_GRAPH_REPOSITORY_WRITE_ALLOWED is False)
    check("B8_SHARED_ERRORS_WRITE_ALLOWED_no", pmod.B8_SHARED_ERRORS_WRITE_ALLOWED is False)
    check("B8_SHARED_RESULTS_WRITE_ALLOWED_no", pmod.B8_SHARED_RESULTS_WRITE_ALLOWED is False)
    check("B8_LEGACY_PLAN_FILES_WRITE_ALLOWED_no", pmod.B8_LEGACY_PLAN_FILES_WRITE_ALLOWED is False)
    check("LEGACY_COMPATIBILITY_IS_COMPARISON_ONLY_yes", pmod.LEGACY_COMPATIBILITY_IS_COMPARISON_ONLY is True)
    check("LEGACY_CURRENT_POINTER_INPUT_AUTHORITY_no",
          pmod.LEGACY_CURRENT_POINTER_INPUT_TO_PROJECTION_AUTHORITY is False)
    check("FOLLOWUP_PROJECTION_REQUIRES_CANONICAL_EDGE_yes",
          pmod.FOLLOWUP_PROJECTION_REQUIRES_CANONICAL_EDGE is True)
    check("FOLLOWUP_PROJECTION_INVENTS_DECISION_no", pmod.FOLLOWUP_PROJECTION_INVENTS_DECISION is False)
    check("PROJECTION_RUNTIME_PRODUCTION_ACTIVE_no", pmod.PROJECTION_RUNTIME_PRODUCTION_ACTIVE is False)
    check("AUTHORITATIVE_GRAPH_WRITES_ALLOWED_no", pmod.AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False)
    check("CUTOVER_AUTHORIZED_no", pmod.CUTOVER_AUTHORIZED is False)

    # =====================================================================
    # Regression matrix (read-only, never mutated)
    # =====================================================================
    matrix_path = REPO_ROOT / "deploy/evidence/issues/9/m2-successor-regression-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    rows = matrix.get("failure_classes", [])
    check("REGRESSION_MATRIX_CLASS_COUNT_16", len(rows) == 16)
    allowed_dispositions = {
        "ELIMINATED_BY_CONSTRUCTION",
        "DETERMINISTICALLY_RECONCILED",
        "NEEDS_SEMANTIC_CHOICE",
        "NOT_YET_IMPLEMENTED",
    }
    unsupported = [
        row.get("failure_class") for row in rows
        if row.get("expected_successor_disposition") not in allowed_dispositions
    ]
    check("UNSUPPORTED_SUCCESS_CLAIMS_ZERO", not unsupported, json.dumps(unsupported))

    passed = sum(ok for _, ok, _ in RESULTS)
    failed = len(RESULTS) - passed
    print(f"\nB8 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"B8_FOCUSED_REGRESSION={'PASS' if failed == 0 else 'FAIL'}")
    print(f"B8_NEGATIVE_REVERSION_PROOF={'PASS' if all(v is True for v in negative.values()) else 'FAIL'}")
    print("PROJECTION_RUNTIME_PRODUCTION_ACTIVE=no")
    print("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no")
    print("CUTOVER_PERFORMED=no")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
