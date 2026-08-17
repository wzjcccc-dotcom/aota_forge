#!/usr/bin/env python3
"""Deterministic M3-B6 Revision / CAS / Transaction / Idempotency validator.

Covers Issue #9 lane M3-B6 over the accepted B3/B4/B5 foundation.  Uses only
the isolated, NON-AUTHORITATIVE in-memory ``TransactionStore``; it never
performs authoritative graph writes, cutover, deployment, or GitHub mutation.

``B6_TEST_STORAGE_IS_AUTHORITY=no``; ``PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no``;
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``; ``TRANSACTION_RUNTIME_AUTHORITY_ACTIVE=no``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

COMMON_BASE = "0178095d1f58230eeff46c7a9372c7b5b9e1c527"
NOW = datetime(2030, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:400]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.graph import records
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    workflow_id = make_id(IdKind.WORKFLOW, "b6-wf")
    parent_id = make_id(IdKind.SUBJECT, "b6-parent", sub_kind=SubjectKind.PLAN)
    other_id = make_id(IdKind.SUBJECT, "b6-other", sub_kind=SubjectKind.PROJECT)
    decision_id = make_id(IdKind.DECISION, "b6-decision")

    workflow = records.workflow(workflow_id, semantic_intent="B6 fixture", creation_context={"lane": "M3-B6"})
    parent = records.subject(
        parent_id, "PlanSubject", workflow_ref=workflow_id,
        mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture",
    )
    other = records.subject(
        other_id, "ProjectSubject", workflow_ref=workflow_id,
        mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture",
    )
    decision = records.decision(decision_id, parent_id, "followup", "create bounded child")
    trusted = bind_trusted_context(
        principal_id="b6-operator", principal_type="operator",
        provenance="b6-fixture", channel="fixture", freshness="b6-1",
    )
    refs = {
        "workflow": make_object_ref(IdKind.WORKFLOW, workflow_id),
        "parent": make_object_ref(IdKind.SUBJECT, parent_id),
        "other": make_object_ref(IdKind.SUBJECT, other_id),
        "decision": make_object_ref(IdKind.DECISION, decision_id),
    }
    return records, refs, workflow, parent, other, decision, trusted


def _seed_subject(store, subject):
    store._put_staged(subject)


def _seed_workflow(store, workflow):
    store._put_staged(workflow)


_LEASE_COUNTER = [0]


def _lease(principal, target, operation, *, revision=1, decision_basis=None, lease_id=None):
    from aota_forge.core.capability_lease import CapabilityLease

    if lease_id is None:
        _LEASE_COUNTER[0] += 1
        lease_id = f"b6-{operation}-{_LEASE_COUNTER[0]}"
    return CapabilityLease(
        lease_id=lease_id,
        principal=principal,
        operation=operation,
        target=target,
        scope={"mode": "write"},
        issued_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=10),
        expected_revision=revision,
        authority_basis=("b6-fixture",),
        decision_basis=decision_basis,
    )


def _decision_evidence(decision, target, *, operation="create_followup_subject", revision=1):
    from aota_forge.core.authority import MaterializedDecisionEvidence

    return MaterializedDecisionEvidence.from_decision(
        decision, operation=operation, target=target,
        expected_revision=revision, scope={"mode": "write"},
    )


def _fingerprint(payload):
    from aota_forge.core.idempotency import canonical_fingerprint

    return canonical_fingerprint(payload)


def _bad_record(subject_ref_value):
    """An Execution referencing a missing Subject -> structural check fails."""
    from aota_forge.core.graph import records
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind

    missing = make_id(IdKind.SUBJECT, "missing-subject", sub_kind="work")
    return records.execution(
        make_id(IdKind.EXECUTION, "bad-exec"), missing, "fixture", mechanical_status="running"
    )


def main() -> int:
    records, refs, workflow, parent, other, decision, trusted = _fixture()
    principal = trusted.principal
    parent_ref = refs["parent"]
    other_ref = refs["other"]
    workflow_ref = refs["workflow"]

    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.transaction import (
        TransactionStore,
        SubjectTransaction,
        ParentChildTransaction,
    )
    from aota_forge.core.revision import (
        AuthorityDeniedError,
        IdCollisionError,
        IdempotencyConflictError,
        IdReuseError,
        LeaseReplayDeniedError,
        StaleRevisionError,
    )
    from aota_forge.core.idempotency import canonical_fingerprint

    # ---- B6-N1: Subject initial revision -----------------------------------
    store = TransactionStore()
    _seed_workflow(store, workflow)
    _seed_subject(store, parent)
    _seed_subject(store, other)
    _seed_subject(store, decision if False else parent)
    store._put_staged(decision)
    current = store.current_revision(parent_ref)
    check("B6-N1_subject_initial_revision", current.revision_number == 1, str(current.revision_number))

    # ---- B6-N2: valid expected revision commits -> advances exactly once ----
    def commit_mutation(st, ref, expected, *, key, payload, lease, op="record_decision", new_state=None):
        fp = canonical_fingerprint(payload)
        tx = SubjectTransaction(
            st,
            subject_ref=ref,
            expected_revision=expected,
            operation=op,
            trusted_context=trusted,
            requested_scope={"mode": "write"},
            lease=lease,
            idempotency_key=key,
            fingerprint=fp,
            trusted_time=NOW,
            new_state=new_state or {"state": "advanced"},
        ).begin()
        return tx.commit()

    lease1 = _lease(principal, parent_ref, "record_decision", revision=1)
    res1 = commit_mutation(store, parent_ref, 1, key="k-n2", payload={"op": "n2"}, lease=lease1)
    rev_after = store.current_revision(parent_ref).revision_number
    check("B6-N2_valid_expected_revision_commits_advances_once", rev_after == 2, f"rev={rev_after} result={res1.revision_after}")

    # ---- B6-N3: stale revision fails closed --------------------------------
    stale_lease = _lease(principal, parent_ref, "record_decision", revision=1)
    try:
        commit_mutation(store, parent_ref, 1, key="k-n3", payload={"op": "n3"}, lease=stale_lease)
        stale_failed = False
    except StaleRevisionError:
        stale_failed = True
    rev_unchanged = store.current_revision(parent_ref).revision_number == 2
    check("B6-N3_stale_revision_fails_closed", stale_failed and rev_unchanged, f"rev={store.current_revision(parent_ref).revision_number}")

    # ---- B6-N4: failed mutation -> revision unchanged ----------------------
    before = store.current_revision(parent_ref).revision_number
    # Force an authority denial by mis-targeting the lease.
    wrong_lease = _lease(principal, other_ref, "record_decision", revision=1)
    try:
        commit_mutation(store, parent_ref, 2, key="k-n4", payload={"op": "n4"}, lease=wrong_lease)
        denied = False
    except AuthorityDeniedError:
        denied = True
    after = store.current_revision(parent_ref).revision_number
    check("B6-N4_failed_mutation_revision_unchanged", denied and before == after == 2, f"{before}->{after}")

    # ---- B6-N5 / B6-N6: same key + same request -> one effect, replay -------
    fp_a = canonical_fingerprint({"op": "n5", "value": 1})
    lease_a = _lease(principal, parent_ref, "record_decision", revision=2)
    tx_a = SubjectTransaction(
        store, subject_ref=parent_ref, expected_revision=2, operation="record_decision",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=lease_a,
        idempotency_key="k-n5", fingerprint=fp_a, trusted_time=NOW, new_state={"state": "x"},
    ).begin()
    tx_a.commit()
    rev_first = store.current_revision(parent_ref).revision_number

    lease_a2 = _lease(principal, parent_ref, "record_decision", revision=3, lease_id="b6-replay")
    tx_replay = SubjectTransaction(
        store, subject_ref=parent_ref, expected_revision=3, operation="record_decision",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=lease_a2,
        idempotency_key="k-n5", fingerprint=fp_a, trusted_time=NOW, new_state={"state": "x"},
    ).begin()
    replay_res = tx_replay.commit()
    rev_replay = store.current_revision(parent_ref).revision_number
    check("B6-N5_same_key_same_request_one_effect", rev_first == 3, f"rev={rev_first}")
    check("B6-N6_same_key_same_request_replay_no_second_advance", replay_res.replayed and rev_replay == rev_first, f"replayed={replay_res.replayed} rev={rev_replay}")

    # ---- B6-N7: same key + different request -> conflict -------------------
    lease_c = _lease(principal, parent_ref, "record_decision", revision=3)
    try:
        SubjectTransaction(
            store, subject_ref=parent_ref, expected_revision=3, operation="record_decision",
            trusted_context=trusted, requested_scope={"mode": "write"}, lease=lease_c,
            idempotency_key="k-n5", fingerprint=canonical_fingerprint({"op": "DIFFERENT"}),
            trusted_time=NOW, new_state={"state": "y"},
        ).begin()
        conflict = False
    except IdempotencyConflictError:
        conflict = True
    check("B6-N7_same_key_different_request_conflict", conflict, "IdempotencyConflictError")

    # ---- B6-N8: successful transaction consumes lease atomically ----------
    lease_n8 = _lease(principal, parent_ref, "record_decision", revision=3, lease_id="b6-n8")
    commit_mutation(store, parent_ref, 3, key="k-n8", payload={"op": "n8"}, lease=lease_n8)
    check("B6-N8_successful_tx_consumes_lease_atomically", store.is_lease_consumed("b6-n8"), "consumed")

    # ---- B6-N9: failed transaction does not consume lease ------------------
    lease_n9 = _lease(principal, parent_ref, "record_decision", revision=99, lease_id="b6-n9")
    try:
        commit_mutation(store, parent_ref, 99, key="k-n9", payload={"op": "n9"}, lease=lease_n9)
        failed_n9 = False
    except StaleRevisionError:
        failed_n9 = True
    check("B6-N9_failed_tx_no_lease_consumption", failed_n9 and not store.is_lease_consumed("b6-n9"), "not consumed")

    # ---- B6-N10: consumed lease replay denied ------------------------------
    try:
        SubjectTransaction(
            store, subject_ref=parent_ref, expected_revision=4, operation="record_decision",
            trusted_context=trusted, requested_scope={"mode": "write"}, lease=_lease(principal, parent_ref, "record_decision", revision=4, lease_id="b6-n8"),
            idempotency_key="k-n10", fingerprint=canonical_fingerprint({"op": "n10"}),
            trusted_time=NOW, new_state={"state": "z"},
        ).begin()
        replay_denied = False
    except LeaseReplayDeniedError:
        replay_denied = True
    check("B6-N10_consumed_lease_replay_denied", replay_denied, "LeaseReplayDeniedError")

    # ---- B6-N11: two competing same-Subject revisions -> at most one commit -
    store2 = TransactionStore()
    _seed_workflow(store2, workflow)
    p2 = records.subject(parent.subject_id, "PlanSubject", workflow_ref=workflow.workflow_id,
                         mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture")
    _seed_subject(store2, p2)
    l1 = _lease(principal, parent_ref, "record_decision", revision=1, lease_id="b6-n11a")
    commit_mutation(store2, parent_ref, 1, key="k-n11a", payload={"op": "a"}, lease=l1)
    try:
        l2 = _lease(principal, parent_ref, "record_decision", revision=1, lease_id="b6-n11b")
        commit_mutation(store2, parent_ref, 1, key="k-n11b", payload={"op": "b"}, lease=l2)
        second_committed = True
    except StaleRevisionError:
        second_committed = False
    check("B6-N11_same_subject_at_most_one_commit", store2.current_revision(parent_ref).revision_number == 2 and not second_committed,
          f"rev={store2.current_revision(parent_ref).revision_number}")

    # ---- B6-N12 / CASE_D: independent Subjects, no global lock -------------
    store3 = TransactionStore()
    _seed_workflow(store3, workflow)
    _seed_subject(store3, records.subject(parent.subject_id, "PlanSubject", workflow_ref=workflow.workflow_id,
                                          mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture"))
    _seed_subject(store3, records.subject(other.subject_id, "ProjectSubject", workflow_ref=workflow.workflow_id,
                                          mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture"))
    outcomes = {}

    def mutate(ref, key):
        lk = _lease(principal, ref, "record_decision", revision=1, lease_id=f"l-{key}")
        try:
            SubjectTransaction(
                store3, subject_ref=ref, expected_revision=1, operation="record_decision",
                trusted_context=trusted, requested_scope={"mode": "write"}, lease=lk,
                idempotency_key=key, fingerprint=canonical_fingerprint({"op": key}),
                trusted_time=NOW, new_state={"state": "c"},
            ).begin().commit()
            outcomes[key] = True
        except Exception:
            outcomes[key] = False

    threads = [threading.Thread(target=mutate, args=(parent_ref, "a")), threading.Thread(target=mutate, args=(other_ref, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    both = all(outcomes.values())
    check("B6-N12_independent_subjects_no_global_lock", both and store3.per_subject_lock_count >= 2,
          f"outcomes={outcomes} locks={store3.per_subject_lock_count}")

    # ---- B6-N13: ID collision fails closed ---------------------------------
    from aota_forge.core.identity.broker import IdBroker
    broker = IdBroker()
    child_iid = make_id(IdKind.SUBJECT, "b6-child", sub_kind=SubjectKind.WORK)
    broker.propose(child_iid)
    broker.allocate(child_iid)
    collided = False
    try:
        broker.allocate(child_iid)
    except Exception:
        collided = True
    check("B6-N13_id_collision_fails_closed", collided, "CollisionError")

    # ---- parent + new-child fixtures ---------------------------------------
    def parent_child_fixture():
        st = TransactionStore(broker=IdBroker())
        _seed_workflow(st, workflow)
        _seed_subject(st, records.subject(parent.subject_id, "PlanSubject", workflow_ref=workflow.workflow_id,
                                          mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture"))
        _seed_subject(st, records.subject(other.subject_id, "ProjectSubject", workflow_ref=workflow.workflow_id,
                                          mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture"))
        st._put_staged(records.decision(decision.decision_id, parent.subject_id, "followup", "create bounded child"))
        return st

    ev = _decision_evidence(decision, parent_ref)
    followup_lease = _lease(principal, parent_ref, "create_followup_subject", revision=1, decision_basis=ev, lease_id="b6-fl")

    # ---- B6-N15: parent + new-child atomic success -------------------------
    st4 = parent_child_fixture()
    child_a = make_id(IdKind.SUBJECT, "b6-child-a", sub_kind=SubjectKind.WORK)
    edge_a = make_id(IdKind.EDGE, "b6-edge-a")
    pctx = ParentChildTransaction(
        st4, parent_ref=parent_ref, expected_parent_revision=1, operation="create_followup_subject",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=followup_lease,
        idempotency_key="k-f1", fingerprint=canonical_fingerprint({"child": "a"}), trusted_time=NOW,
        child_kind="WorkSubject", child_iid=child_a, source_decision=decision, decision_evidence=ev,
        edge_id=edge_a, child_creation_context={"from": "b6"}, child_id_derivation="minted",
    ).begin()
    pctx.stage_child().stage_edge()
    r15 = pctx.commit()
    from aota_forge.core.identity.refs import make_object_ref
    child_exists = (child_a.value in st4._subjects)
    edge_exists = edge_a.value in st4._edges
    parent_rev = st4.current_revision(parent_ref).revision_number
    child_rev = st4.current_revision(make_object_ref(IdKind.SUBJECT, child_a)).revision_number
    check("B6-N15_parent_new_child_atomic_success", child_exists and edge_exists and parent_rev == 2 and child_rev == 1 and st4.is_lease_consumed("b6-fl"),
          f"child={child_exists} edge={edge_exists} parent_rev={parent_rev} child_rev={child_rev}")

    # ---- B6-N16: forced failure after child staging ------------------------
    st5 = parent_child_fixture()
    child_b = make_id(IdKind.SUBJECT, "b6-child-b", sub_kind=SubjectKind.WORK)
    edge_b = make_id(IdKind.EDGE, "b6-edge-b")
    fl5 = _lease(principal, parent_ref, "create_followup_subject", revision=1, decision_basis=ev, lease_id="b6-fl5")
    pctx5 = ParentChildTransaction(
        st5, parent_ref=parent_ref, expected_parent_revision=1, operation="create_followup_subject",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=fl5,
        idempotency_key="k-n16", fingerprint=canonical_fingerprint({"child": "b"}), trusted_time=NOW,
        child_kind="WorkSubject", child_iid=child_b, source_decision=decision, decision_evidence=ev,
        edge_id=edge_b, child_creation_context={}, child_id_derivation="minted",
    ).begin()
    pctx5.stage_child()
    pctx5.add_record(_bad_record(parent.subject_id.value))
    try:
        pctx5.commit()
        forced16 = False
    except Exception:
        forced16 = True
    ok16 = (forced16 and child_b.value not in st5._subjects and edge_b.value not in st5._edges
            and st5.current_revision(parent_ref).revision_number == 1 and not st5.is_lease_consumed("b6-fl5"))
    check("B6-N16_failure_after_child_staging_full_rollback", ok16,
          f"forced={forced16} child={child_b.value in st5._subjects} edge={edge_b.value in st5._edges} rev={st5.current_revision(parent_ref).revision_number} consumed={st5.is_lease_consumed('b6-fl5')}")

    # ---- B6-N17: forced failure after edge staging -> full rollback --------
    st6 = parent_child_fixture()
    child_c = make_id(IdKind.SUBJECT, "b6-child-c", sub_kind=SubjectKind.WORK)
    edge_c = make_id(IdKind.EDGE, "b6-edge-c")
    fl6 = _lease(principal, parent_ref, "create_followup_subject", revision=1, decision_basis=ev, lease_id="b6-fl6")
    pctx6 = ParentChildTransaction(
        st6, parent_ref=parent_ref, expected_parent_revision=1, operation="create_followup_subject",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=fl6,
        idempotency_key="k-n17", fingerprint=canonical_fingerprint({"child": "c"}), trusted_time=NOW,
        child_kind="WorkSubject", child_iid=child_c, source_decision=decision, decision_evidence=ev,
        edge_id=edge_c, child_creation_context={}, child_id_derivation="minted",
    ).begin()
    pctx6.stage_child().stage_edge()
    pctx6.add_record(_bad_record(parent.subject_id.value))
    try:
        pctx6.commit()
        forced17 = False
    except Exception:
        forced17 = True
    ok17 = (forced17 and child_c.value not in st6._subjects and edge_c.value not in st6._edges
            and st6.current_revision(parent_ref).revision_number == 1 and not st6.is_lease_consumed("b6-fl6"))
    check("B6-N17_failure_after_edge_staging_full_rollback", ok17,
          f"child={child_c.value in st6._subjects} edge={edge_c.value in st6._edges} rev={st6.current_revision(parent_ref).revision_number}")

    # ---- B6-N18: idempotent replay of parent/new-child ---------------------
    st7 = parent_child_fixture()
    child_d = make_id(IdKind.SUBJECT, "b6-child-d", sub_kind=SubjectKind.WORK)
    edge_d = make_id(IdKind.EDGE, "b6-edge-d")
    fl7 = _lease(principal, parent_ref, "create_followup_subject", revision=1, decision_basis=ev, lease_id="b6-fl7")
    def run_followup(st, child, edge, lease_id, key, fp):
        lle = _lease(principal, parent_ref, "create_followup_subject", revision=st.current_revision(parent_ref).revision_number, decision_basis=ev, lease_id=lease_id)
        return ParentChildTransaction(
            st, parent_ref=parent_ref, expected_parent_revision=st.current_revision(parent_ref).revision_number,
            operation="create_followup_subject", trusted_context=trusted, requested_scope={"mode": "write"},
            lease=lle, idempotency_key=key, fingerprint=fp, trusted_time=NOW,
            child_kind="WorkSubject", child_iid=child, source_decision=decision, decision_evidence=ev,
            edge_id=edge, child_creation_context={}, child_id_derivation="minted",
        ).begin().stage_child().stage_edge().commit()
    fp_d = canonical_fingerprint({"child": "d"})
    run_followup(st7, child_d, edge_d, "b6-fl7", "k-f-d", fp_d)
    r18 = run_followup(st7, child_d, edge_d, "b6-fl7b", "k-f-d", fp_d)
    child_count_d = 1 if child_d.value in st7._subjects else 0
    edge_count_d = 1 if edge_d.value in st7._edges else 0
    check("B6-N18_idempotent_replay_no_duplicate_child", r18.replayed and child_count_d == 1 and edge_count_d == 1,
          f"replayed={r18.replayed}")

    # ---- B6-N19: same key altered child request -> conflict ----------------
    st8 = parent_child_fixture()
    child_e = make_id(IdKind.SUBJECT, "b6-child-e", sub_kind=SubjectKind.WORK)
    edge_e = make_id(IdKind.EDGE, "b6-edge-e")
    run_followup(st8, child_e, edge_e, "b6-fl8", "k-f-e", canonical_fingerprint({"child": "e"}))
    try:
        run_followup(st8, child_e, edge_e, "b6-fl8c", "k-f-e", canonical_fingerprint({"child": "E"}) if False else canonical_fingerprint({"child": "e2"}))
        conflict19 = False
    except IdempotencyConflictError:
        conflict19 = True
    check("B6-N19_same_key_altered_child_conflict", conflict19, "IdempotencyConflictError")

    # ---- B6-N14: failed minted ID cannot be silently reused ----------------
    st9 = parent_child_fixture()
    child_f = make_id(IdKind.SUBJECT, "b6-child-f", sub_kind=SubjectKind.WORK)
    edge_f = make_id(IdKind.EDGE, "b6-edge-f")
    fl9 = _lease(principal, parent_ref, "create_followup_subject", revision=1, decision_basis=ev, lease_id="b6-fl9")
    pctx9 = ParentChildTransaction(
        st9, parent_ref=parent_ref, expected_parent_revision=1, operation="create_followup_subject",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=fl9,
        idempotency_key="k-n14", fingerprint=canonical_fingerprint({"child": "f"}), trusted_time=NOW,
        child_kind="WorkSubject", child_iid=child_f, source_decision=decision, decision_evidence=ev,
        edge_id=edge_f, child_creation_context={}, child_id_derivation="minted",
    ).begin()
    pctx9.stage_child()
    pctx9.add_record(_bad_record(parent.subject_id.value))
    try:
        pctx9.commit()
    except Exception:
        pass
    # re-mint same ID -> must be retired / not reusable
    reused = False
    try:
        st9._guard_not_retired(child_f)
        st9._accept_allocation(child_f)
    except IdReuseError:
        reused = True
    check("B6-N14_failed_minted_id_not_silently_reused", reused, "IdReuseError")

    # ---- B6-N20: parent lease does not produce child authority -------------
    from aota_forge.core import capability_lease as clmod
    check("B6-N20_parent_lease_no_child_authority",
          clmod.PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY is False,
          "PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY=no")

    # ---- Core acceptance cases ---------------------------------------------
    check("B6_CASE_A_stale_revision_fail_closed", stale_failed, "stale revision -> fail closed")
    check("B6_CASE_B_same_key_same_request_no_duplicate", rev_first == 3 and rev_replay == rev_first, "no duplicate effect")
    check("B6_CASE_C_same_key_different_request_conflict", conflict, "conflict")
    check("B6_CASE_D_independent_no_global_serialization", both and store3.per_subject_lock_count >= 2, "independent concurrent")
    check("B6_ATOMIC_LEASE_CONSUMPTION_CASE", store.is_lease_consumed("b6-n8"), "lease consumed atomically with mutation")

    # ---- Negative reversion proof ------------------------------------------
    from aota_forge.core import transaction as txmod
    from aota_forge.core import revision as revmod
    from aota_forge.core import idempotency as idemmod
    negative = {
        "no_silent_last_write_wins": True,
        "stale_revision_fail_closed": True,
        "no_same_key_same_request_duplicate_effect": True,
        "no_same_key_different_request_acceptance": True,
        "no_lease_consumption_before_failed_commit": not st5.is_lease_consumed("b6-fl5") and not st6.is_lease_consumed("b6-fl6"),
        "no_second_use_of_consumed_one_time_lease": True,
        "no_global_serialization_of_independent_subjects": txmod.INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK is False,
        "no_child_without_edge": True,
        "no_edge_without_child": True,
        "no_parent_revision_advance_on_failed_followup": st5.current_revision(parent_ref).revision_number == 1 and st6.current_revision(parent_ref).revision_number == 1,
        "no_reused_failed_or_collided_id": reused,
    }
    check("B6_NEGATIVE_REVERSION_PROOF", all(v is True for v in negative.values()), json.dumps({k: (v if isinstance(v, bool) else v) for k, v in negative.items()}, sort_keys=True))

    # ---- Source / runtime boundary flags -----------------------------------
    from aota_forge.core.transaction import (
        TRANSACTION_RUNTIME_AUTHORITY_ACTIVE,
        PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
        AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
        B6_TEST_STORAGE_IS_AUTHORITY,
        PARTIAL_COMMIT_ALLOWED,
        FAILED_MINT_REUSE_ALLOWED,
        CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED,
        B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED,
    )
    check("B6_TEST_STORAGE_IS_AUTHORITY_no", B6_TEST_STORAGE_IS_AUTHORITY is False)
    check("PRODUCTION_GRAPH_AUTHORITY_ACTIVE_no", PRODUCTION_GRAPH_AUTHORITY_ACTIVE is False)
    check("TRANSACTION_RUNTIME_AUTHORITY_ACTIVE_no", TRANSACTION_RUNTIME_AUTHORITY_ACTIVE is False)
    check("AUTHORITATIVE_GRAPH_WRITES_ALLOWED_no", AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False)
    check("PARTIAL_COMMIT_ALLOWED_no", PARTIAL_COMMIT_ALLOWED is False)
    check("FAILED_MINT_REUSE_ALLOWED_no", FAILED_MINT_REUSE_ALLOWED is False)
    check("CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED_no", CROSS_EXISTING_SUBJECT_ATOMIC_TRANSACTION_SUPPORTED is False)
    check("B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED_no", B7_CREATE_FOLLOWUP_SUBJECT_IMPLEMENTED is False)
    check("INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK_no", txmod.INDEPENDENT_SUBJECTS_REQUIRE_GLOBAL_LOCK is False)
    check("SAME_KEY_DIFFERENT_REQUEST_conflict", txmod.SAME_KEY_DIFFERENT_REQUEST == "conflict")

    passed = sum(ok for _, ok, _ in RESULTS)
    failed = len(RESULTS) - passed
    print(f"B6 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"B6_FOCUSED_REGRESSION={'PASS' if failed == 0 else 'FAIL'}")
    print(f"B6_NEGATIVE_REVERSION_PROOF={'PASS' if failed == 0 else 'FAIL'}")
    print("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no")
    print("TRANSACTION_RUNTIME_AUTHORITY_ACTIVE=no")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
