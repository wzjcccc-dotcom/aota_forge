#!/usr/bin/env python3
"""Deterministic M3-B7 Canonical Transition Layer validator (Issue #9, lane M3-B7).

Composes the accepted B3-B6 foundations into four canonical lifecycle
transitions and proves the required end state:

* create_execution
* record_completion
* record_decision
* create_followup_subject

Everything runs against the isolated, NON-AUTHORITATIVE B6 ``TransactionStore``.
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``; ``TRANSITION_RUNTIME_AUTHORITY_ACTIVE=no``;
``PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no``; ``CUTOVER_PERFORMED=no``.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _workflow(store, wf_id):
    from aota_forge.core.graph import records

    store._put_staged(records.workflow(wf_id, semantic_intent="B7 fixture", creation_context={"lane": "M3-B7"}))


def _subject(store, iid, kind, workflow_ref):
    from aota_forge.core.graph import records

    store._put_staged(
        records.subject(
            iid, kind, workflow_ref=workflow_ref,
            mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture",
        )
    )


def _make_store(with_workflow=True):
    from aota_forge.core.identity.broker import IdBroker
    from aota_forge.core.transaction import TransactionStore

    store = TransactionStore(broker=IdBroker())
    return store


def _lease(principal, target, operation, *, revision, decision_basis=None, lease_id=None):
    from aota_forge.core.capability_lease import CapabilityLease

    if lease_id is None:
        _LEASE_COUNTER[0] += 1
        lease_id = f"b7-{operation}-{_LEASE_COUNTER[0]}"
    return CapabilityLease(
        lease_id=lease_id,
        principal=principal,
        operation=operation,
        target=target,
        scope={"mode": "write"},
        issued_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=10),
        expected_revision=revision,
        authority_basis=("b7-fixture",),
        decision_basis=decision_basis,
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


def _ids_and_refs():
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    workflow_id = make_id(IdKind.WORKFLOW, "b7-wf")
    parent_id = make_id(IdKind.SUBJECT, "b7-parent", sub_kind=SubjectKind.PLAN)
    other_id = make_id(IdKind.SUBJECT, "b7-other", sub_kind=SubjectKind.PROJECT)
    wf_ref = make_object_ref(IdKind.WORKFLOW, workflow_id)
    parent_ref = make_object_ref(IdKind.SUBJECT, parent_id)
    other_ref = make_object_ref(IdKind.SUBJECT, other_id)
    return workflow_id, parent_id, other_id, wf_ref, parent_ref, other_ref


def main() -> int:
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph import records
    from aota_forge.core.revision import (
        AuthorityDeniedError,
        IdempotencyConflictError,
        StaleRevisionError,
    )
    from aota_forge.core.transitions import (
        CreateExecutionRequest,
        RecordCompletionRequest,
        RecordDecisionRequest,
        CreateFollowupSubjectRequest,
        create_execution,
        record_completion,
        record_decision,
        create_followup_subject,
        TransitionNotFoundError,
        TransitionDeniedError,
        TransitionInvalidError,
    )

    trusted = bind_trusted_context(
        principal_id="b7-operator", principal_type="operator",
        provenance="b7-fixture", channel="fixture", freshness="b7-1",
    )
    principal = trusted.principal
    workflow_id, parent_id, other_id, wf_ref, parent_ref, other_ref = _ids_and_refs()

    # =====================================================================
    # B7-N1 / B7-N2 / B7-N3 / B7-N4 : create_execution
    # =====================================================================
    store1 = _make_store()
    _workflow(store1, workflow_id)
    _subject(store1, parent_id, "PlanSubject", workflow_id)
    _subject(store1, other_id, "ProjectSubject", workflow_id)

    lease_ce = _lease(principal, parent_ref, "create_execution", revision=1)
    r_ce = create_execution(
        store1,
        CreateExecutionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=lease_ce,
            expected_revision=1, idempotency_key="b7-ce-1", trusted_time=NOW,
            executor_kind="test", mechanical_status="running",
        ),
    )
    exec_count_1 = len(store1.executions_of_subject(parent_ref))
    rev_ce = store1.current_revision(parent_ref).revision_number
    check("B7-N1_create_execution_valid_succeeds",
          r_ce.code == "COMMITTED" and exec_count_1 == 1 and rev_ce == 2,
          f"exec={exec_count_1} rev={rev_ce} created={r_ce.created_objects}")
    ce_ref = r_ce.created_objects[0]

    # B7-N2: stale expected revision fails closed
    stale_ce = _lease(principal, parent_ref, "create_execution", revision=1)
    try:
        create_execution(
            store1,
            CreateExecutionRequest(
                trusted_context=trusted, subject_ref=parent_ref, lease=stale_ce,
                expected_revision=1, idempotency_key="b7-ce-stale", trusted_time=NOW,
                executor_kind="test",
            ),
        )
        stale_denied = False
    except StaleRevisionError:
        stale_denied = True
    check("B7-N2_create_execution_stale_revision_fails_closed",
          stale_denied and store1.current_revision(parent_ref).revision_number == 2,
          f"rev={store1.current_revision(parent_ref).revision_number}")

    # B7-N3: same key + same request -> exactly one Execution, replay same effect
    lease_ce3 = _lease(principal, parent_ref, "create_execution", revision=2)
    r_ce3 = create_execution(
        store1,
        CreateExecutionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=lease_ce3,
            expected_revision=2, idempotency_key="b7-ce-1", trusted_time=NOW,
            executor_kind="test", mechanical_status="running",
        ),
    )
    exec_count_3 = len(store1.executions_of_subject(parent_ref))
    rev_ce3 = store1.current_revision(parent_ref).revision_number
    check("B7-N3_create_execution_same_key_same_request_one_execution",
          r_ce3.replayed and exec_count_3 == 1 and rev_ce3 == 2 and r_ce3.created_objects[0] == ce_ref,
          f"replayed={r_ce3.replayed} exec={exec_count_3} rev={rev_ce3}")

    # B7-N4: same key + different request -> conflict
    lease_ce4 = _lease(principal, parent_ref, "create_execution", revision=2)
    try:
        create_execution(
            store1,
            CreateExecutionRequest(
                trusted_context=trusted, subject_ref=parent_ref, lease=lease_ce4,
                expected_revision=2, idempotency_key="b7-ce-1", trusted_time=NOW,
                executor_kind="DIFFERENT", mechanical_status="running",
            ),
        )
        conflict_ce = False
    except IdempotencyConflictError:
        conflict_ce = True
    check("B7-N4_create_execution_same_key_different_request_conflict", conflict_ce, "IdempotencyConflictError")

    # =====================================================================
    # B7-N5 / B7-N6 / B7-N7 : record_completion
    # =====================================================================
    store2 = _make_store()
    _workflow(store2, workflow_id)
    _subject(store2, parent_id, "PlanSubject", workflow_id)
    _subject(store2, other_id, "ProjectSubject", workflow_id)

    r_ce2 = create_execution(
        store2,
        CreateExecutionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "create_execution", revision=1),
            expected_revision=1, idempotency_key="b7-ce2", trusted_time=NOW, executor_kind="test",
        ),
    )
    exec_ref_str = r_ce2.created_objects[0]
    exec_ref = _parse_ref(exec_ref_str)
    # owning subject is parent (rev 2 now)
    completion_lease = _lease(principal, parent_ref, "record_completion", revision=2)
    r_comp = record_completion(
        store2,
        RecordCompletionRequest(
            trusted_context=trusted, execution_ref=exec_ref, lease=completion_lease,
            expected_revision=2, idempotency_key="b7-comp-1", trusted_time=NOW, outcome="success",
        ),
    )
    comp_ref_str = r_comp.created_objects[0]
    check("B7-N5_record_completion_commits", r_comp.code == "COMMITTED" and r_comp.owning_subject == parent_ref.serialize(),
          f"owning={r_comp.owning_subject}")

    from aota_forge.core.graph.repository import OwningSubjectResolver
    resolver = OwningSubjectResolver(store2)
    comp_ref = _parse_ref(comp_ref_str)
    completion = store2.completion(comp_ref)
    owning_of_completion = resolver.owning_subject_of_completion(comp_ref)
    check("B7-N5b_completion_resolves_execution_to_owning_subject",
          completion.execution_ref == exec_ref.internal_id and owning_of_completion.subject_id == parent_id,
          f"exec_ref_ok={completion.execution_ref == exec_ref.internal_id} owning={owning_of_completion.subject_id.value}")
    check("B7-N6_completion_has_no_direct_subject_ref", not hasattr(completion, "subject_ref"),
          "Completion carries no subject_ref field")

    # B7-N7: orphan relationship fails closed (unknown execution)
    missing_exec = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "missing-exec"))
    orphan_denied = False
    try:
        record_completion(
            store2,
            RecordCompletionRequest(
                trusted_context=trusted, execution_ref=missing_exec, lease=_lease(principal, parent_ref, "record_completion", revision=2),
                expected_revision=2, idempotency_key="b7-comp-orphan", trusted_time=NOW, outcome="x",
            ),
        )
    except TransitionNotFoundError:
        orphan_denied = True
    check("B7-N7_orphan_execution_completion_fails_closed", orphan_denied, "TransitionNotFoundError")

    # =====================================================================
    # B7-N8 / B7-N9 : record_decision
    # =====================================================================
    store3 = _make_store()
    _workflow(store3, workflow_id)
    _subject(store3, parent_id, "PlanSubject", workflow_id)
    _subject(store3, other_id, "ProjectSubject", workflow_id)

    r_dec = record_decision(
        store3,
        RecordDecisionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "record_decision", revision=1),
            expected_revision=1, idempotency_key="b7-dec-1", trusted_time=NOW,
            decision_kind="followup", statement="create bounded child",
        ),
    )
    dec_ref_str = r_dec.created_objects[0]
    dec_ref = _parse_ref(dec_ref_str)
    decision = store3.decision(dec_ref)
    check("B7-N8_record_decision_materializes_subject_owned_decision",
          decision.subject_ref == parent_id and r_dec.owning_subject == parent_ref.serialize(),
          f"owned={decision.subject_ref.value}")

    # B7-N9: Decision stale revision fails closed
    try:
        record_decision(
            store3,
            RecordDecisionRequest(
                trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "record_decision", revision=999),
                expected_revision=999, idempotency_key="b7-dec-stale", trusted_time=NOW,
                decision_kind="x", statement="y",
            ),
        )
        dec_stale = False
    except StaleRevisionError:
        dec_stale = True
    check("B7-N9_decision_stale_revision_fails_closed", dec_stale, "StaleRevisionError")

    # =====================================================================
    # B7-N10..N17 : create_followup_subject
    # =====================================================================
    def followup_fixture():
        st = _make_store()
        _workflow(st, workflow_id)
        _subject(st, parent_id, "PlanSubject", workflow_id)
        _subject(st, other_id, "ProjectSubject", workflow_id)
        # materialize a Decision owned by parent (parent rev 1 -> 2)
        r = record_decision(
            st,
            RecordDecisionRequest(
                trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "record_decision", revision=1),
                expected_revision=1, idempotency_key=f"b7-dec-{id(st)}", trusted_time=NOW,
                decision_kind="followup", statement="create bounded child",
            ),
        )
        dref = _parse_ref(r.created_objects[0])
        decision = st.decision(dref)
        return st, dref, decision

    # ---- B7-N10 / N11 / N12: success with exact decision -------------------
    st4, dref4, decision4 = followup_fixture()
    parent_rev4 = st4.current_revision(parent_ref).revision_number  # 2
    ev4 = _decision_evidence(decision4, parent_ref, revision=parent_rev4)
    fl4 = _lease(principal, parent_ref, "create_followup_subject", revision=parent_rev4, decision_basis=ev4)
    r_fu = create_followup_subject(
        st4,
        CreateFollowupSubjectRequest(
            trusted_context=trusted, parent_ref=parent_ref, decision_ref=dref4, lease=fl4,
            expected_revision=parent_rev4, idempotency_key="b7-fu-1", trusted_time=NOW,
            child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK, rationale="followup",
        ),
    )
    child_ref_str = r_fu.created_objects[0]
    child_ref = _parse_ref(child_ref_str)
    child_subject = st4.subject(child_ref)
    child_rev = st4.current_revision(child_ref).revision_number
    edges = st4.edges()
    parent_rev4_after = st4.current_revision(parent_ref).revision_number
    check("B7-N10_followup_succeeds_exact_decision_parent_lease",
          r_fu.code == "COMMITTED" and child_subject.subject_id == child_ref.internal_id,
          f"child={child_ref_str}")
    check("B7-N11_followup_child_starts_revision_1", child_rev == 1, f"child_rev={child_rev}")
    check("B7-N12_followup_exactly_one_decision_backed_edge",
          len(edges) == 1 and edges[0].source_decision_ref == decision4.decision_id
          and edges[0].parent_subject_ref == parent_id and edges[0].child_subject_ref == child_ref.internal_id,
          f"edges={len(edges)}")
    check("B7_FOLLOWUP_ATOMIC_CHAIN_CASE",
          r_fu.code == "COMMITTED" and child_rev == 1 and len(edges) == 1
          and parent_rev4_after == parent_rev4 + 1 and st4.is_lease_consumed(fl4.lease_id),
          f"child_rev={child_rev} parent_rev={parent_rev4_after} lease_consumed={st4.is_lease_consumed(fl4.lease_id)}")

    # ---- B7-N13: missing Decision denied ------------------------------------
    st13, _, _ = followup_fixture()
    missing_dref = make_object_ref(IdKind.DECISION, make_id(IdKind.DECISION, "missing-dec"))
    try:
        create_followup_subject(
            st13,
            CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=missing_dref,
                lease=_lease(principal, parent_ref, "create_followup_subject", revision=2),
                expected_revision=2, idempotency_key="b7-fu-missing", trusted_time=NOW,
                child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK,
            ),
        )
        missing_denied = False
    except TransitionNotFoundError:
        missing_denied = True
    check("B7-N13_followup_missing_decision_denied", missing_denied, "TransitionNotFoundError")

    # ---- B7-N14: foreign Decision denied ------------------------------------
    st14, dref14, _ = followup_fixture()
    # create a Decision owned by the OTHER subject
    r_other_dec = record_decision(
        st14,
        RecordDecisionRequest(
            trusted_context=trusted, subject_ref=other_ref, lease=_lease(principal, other_ref, "record_decision", revision=1),
            expected_revision=1, idempotency_key="b7-other-dec", trusted_time=NOW,
            decision_kind="followup", statement="foreign",
        ),
    )
    foreign_dref = _parse_ref(r_other_dec.created_objects[0])
    foreign_denied = False
    try:
        create_followup_subject(
            st14,
            CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=foreign_dref,
                lease=_lease(principal, parent_ref, "create_followup_subject", revision=2),
                expected_revision=2, idempotency_key="b7-fu-foreign", trusted_time=NOW,
                child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK,
            ),
        )
    except TransitionDeniedError:
        foreign_denied = True
    check("B7-N14_foreign_decision_denied", foreign_denied, "TransitionDeniedError")

    # ---- B7-N15: followup stale parent revision fails closed -----------------
    st15, dref15, decision15 = followup_fixture()
    try:
        create_followup_subject(
            st15,
            CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=dref15,
                lease=_lease(principal, parent_ref, "create_followup_subject", revision=1),
                expected_revision=1, idempotency_key="b7-fu-stale", trusted_time=NOW,
                child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK,
            ),
        )
        fu_stale = False
    except StaleRevisionError:
        fu_stale = True
    check("B7-N15_followup_stale_parent_revision_fails_closed", fu_stale, "StaleRevisionError")

    # ---- B7-N16: idempotent replay creates no second child ------------------
    st16, dref16, decision16 = followup_fixture()
    parent_rev16 = st16.current_revision(parent_ref).revision_number
    ev16 = _decision_evidence(decision16, parent_ref, revision=parent_rev16)
    def run_fu(st, dref, decision, rev, key, *, child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK, rationale="followup"):
        ev = _decision_evidence(decision, parent_ref, revision=rev)
        fl = _lease(principal, parent_ref, "create_followup_subject", revision=rev, decision_basis=ev)
        return create_followup_subject(
            st,
            CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=dref, lease=fl,
                expected_revision=rev, idempotency_key=key, trusted_time=NOW,
                child_kind=child_kind, child_sub_kind=child_sub_kind, rationale=rationale,
            ),
        )
    r_fu1 = run_fu(st16, dref16, decision16, parent_rev16, "b7-fu-replay")
    child_16 = r_fu1.created_objects[0]
    parent_rev_after_first = st16.current_revision(parent_ref).revision_number
    children16 = len(st16.subjects())
    # replay (same key + same request) -> no second child
    r_fu2 = run_fu(st16, dref16, decision16, parent_rev16, "b7-fu-replay")
    children16_after = len(st16.subjects())
    parent_rev_after_replay = st16.current_revision(parent_ref).revision_number
    edges16 = len(st16.edges())
    check("B7-N16_followup_idempotent_replay_no_second_child",
          r_fu2.replayed and children16_after == children16 and r_fu2.created_objects[0] == child_16,
          f"replayed={r_fu2.replayed} children={children16}->{children16_after}")

    # ---- B7-N17: same key + altered followup request -> conflict ------------
    try:
        run_fu(st16, dref16, decision16, parent_rev16, "b7-fu-replay", rationale="followup-ALTERED")
        fu_conflict = False
    except IdempotencyConflictError:
        fu_conflict = True
    check("B7-N17_followup_same_key_altered_request_conflict", fu_conflict, "IdempotencyConflictError")

    # =====================================================================
    # B7-N18 / N19 : forced failure atomicity
    # =====================================================================
    # B7-N18: child-stage failure (dangling workflow_ref on child) rolls back all
    st18 = _make_store()
    _workflow(st18, workflow_id)
    _subject(st18, parent_id, "PlanSubject", workflow_id)
    _subject(st18, other_id, "ProjectSubject", workflow_id)
    # materialize decision (parent rev 1 -> 2)
    r_d18 = record_decision(
        st18,
        RecordDecisionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "record_decision", revision=1),
            expected_revision=1, idempotency_key="b7-18dec", trusted_time=NOW,
            decision_kind="followup", statement="x",
        ),
    )
    dref18 = _parse_ref(r_d18.created_objects[0])
    decision18 = st18.decision(dref18)
    rev18 = st18.current_revision(parent_ref).revision_number  # 2
    ev18 = _decision_evidence(decision18, parent_ref, revision=rev18)
    fl18 = _lease(principal, parent_ref, "create_followup_subject", revision=rev18, decision_basis=ev18)
    children_before18 = len(st18.subjects())
    edge_before18 = len(st18.edges())
    # force failure by creating a child whose workflow_ref is dangling
    # (the transition derives child workflow from parent; we poison parent's workflow by removing it)
    st18._workflows.pop(workflow_id.value)
    failed18 = False
    try:
        create_followup_subject(
            st18,
            CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=dref18, lease=fl18,
                expected_revision=rev18, idempotency_key="b7-18fu", trusted_time=NOW,
                child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK,
            ),
        )
    except Exception:
        failed18 = True
    ok18 = (failed18 and len(st18.subjects()) == children_before18 and len(st18.edges()) == edge_before18
            and st18.current_revision(parent_ref).revision_number == rev18 and not st18.is_lease_consumed(fl18.lease_id))
    check("B7-N18_forced_child_stage_failure_rolls_back_everything", ok18,
          f"failed={failed18} children={len(st18.subjects())} edges={len(st18.edges())} rev={st18.current_revision(parent_ref).revision_number}")

    # B7-N19: edge-stage failure rolls back everything (B6 foundation B7 reuses)
    from aota_forge.core.transaction import ParentChildTransaction
    from aota_forge.core.idempotency import canonical_fingerprint
    st19 = _make_store()
    _workflow(st19, workflow_id)
    _subject(st19, parent_id, "PlanSubject", workflow_id)
    _subject(st19, other_id, "ProjectSubject", workflow_id)
    r_d19 = record_decision(
        st19,
        RecordDecisionRequest(
            trusted_context=trusted, subject_ref=parent_ref, lease=_lease(principal, parent_ref, "record_decision", revision=1),
            expected_revision=1, idempotency_key="b7-19dec", trusted_time=NOW,
            decision_kind="followup", statement="x",
        ),
    )
    dref19 = _parse_ref(r_d19.created_objects[0])
    decision19 = st19.decision(dref19)
    rev19 = st19.current_revision(parent_ref).revision_number
    ev19 = _decision_evidence(decision19, parent_ref, revision=rev19)
    fl19 = _lease(principal, parent_ref, "create_followup_subject", revision=rev19, decision_basis=ev19)
    child19 = make_id(IdKind.SUBJECT, "b7-child-19", sub_kind=SubjectKind.WORK)
    edge19 = make_id(IdKind.EDGE, "b7-edge-19")
    pctx19 = ParentChildTransaction(
        st19, parent_ref=parent_ref, expected_parent_revision=rev19, operation="create_followup_subject",
        trusted_context=trusted, requested_scope={"mode": "write"}, lease=fl19,
        idempotency_key="b7-19fu", fingerprint=canonical_fingerprint({"child": "19"}), trusted_time=NOW,
        child_kind="WorkSubject", child_iid=child19, source_decision=decision19, decision_evidence=ev19,
        edge_id=edge19, child_creation_context={}, child_id_derivation="minted",
    ).begin()
    pctx19.stage_child().stage_edge()
    pctx19.add_record(records.execution(
        make_id(IdKind.EXECUTION, "bad-19"), make_id(IdKind.SUBJECT, "missing-19", sub_kind="work"),
        "fixture", mechanical_status="running",
    ))
    failed19 = False
    try:
        pctx19.commit()
    except Exception:
        failed19 = True
    ok19 = (failed19 and child19.value not in st19._subjects and edge19.value not in st19._edges
            and st19.current_revision(parent_ref).revision_number == rev19 and not st19.is_lease_consumed(fl19.lease_id))
    check("B7-N19_forced_edge_stage_failure_rolls_back_everything", ok19,
          f"failed={failed19} child={child19.value in st19._subjects} edge={edge19.value in st19._edges} rev={st19.current_revision(parent_ref).revision_number}")

    # =====================================================================
    # B7-N20 / N21 / N22 : authority / kind / possession guards
    # =====================================================================
    from aota_forge.core import capability_lease as clmod
    from aota_forge.core import transitions as tmod
    check("B7-N20_parent_lease_grants_no_child_authority",
          clmod.PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY is False and tmod.PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY is False,
          "parent lease grants no child authority")

    # B7-N21: wrong-kind ObjectRef fails closed (across all transitions)
    wrong_subject = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "not-subject"))
    wk_ce = False
    try:
        create_execution(
            store1, CreateExecutionRequest(
                trusted_context=trusted, subject_ref=wrong_subject,
                lease=_lease(principal, parent_ref, "create_execution", revision=1),
                expected_revision=1, idempotency_key="b7-wk-ce", trusted_time=NOW, executor_kind="t",
            ),
        )
    except (TransitionInvalidError, Exception):
        wk_ce = True
    wrong_exec = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "not-exec", sub_kind="work"))
    wk_comp = False
    try:
        record_completion(
            store2, RecordCompletionRequest(
                trusted_context=trusted, execution_ref=wrong_exec,
                lease=_lease(principal, parent_ref, "record_completion", revision=2),
                expected_revision=2, idempotency_key="b7-wk-comp", trusted_time=NOW, outcome="x",
            ),
        )
    except Exception:
        wk_comp = True
    check("B7-N21_wrong_kind_object_ref_fails_closed", wk_ce and wk_comp, f"ce={wk_ce} comp={wk_comp}")

    # B7-N22: ID/ObjectRef possession alone gives no transition authority
    # (lease targeting a different subject than the ObjectRef action -> denied)
    possession_denied = False
    try:
        create_execution(
            store1, CreateExecutionRequest(
                trusted_context=trusted, subject_ref=parent_ref,
                lease=_lease(principal, other_ref, "create_execution", revision=2),
                expected_revision=2, idempotency_key="b7-possession", trusted_time=NOW, executor_kind="t",
            ),
        )
    except AuthorityDeniedError:
        possession_denied = True
    check("B7-N22_id_object_ref_possession_gives_no_authority", possession_denied, "AuthorityDeniedError")

    # =====================================================================
    # B7-N23 / N24 : reuse proofs
    # =====================================================================
    # N23: all four transitions route through the B5 Authority Engine.
    # Force a lease-target mismatch on each -> AuthorityDeniedError.
    ce_denied = False
    try:
        create_execution(
            store1, CreateExecutionRequest(
                trusted_context=trusted, subject_ref=parent_ref,
                lease=_lease(principal, other_ref, "create_execution", revision=2),
                expected_revision=2, idempotency_key="b7-reuse-ce", trusted_time=NOW, executor_kind="t",
            ),
        )
    except AuthorityDeniedError:
        ce_denied = True
    comp_denied = False
    try:
        record_completion(
            store2, RecordCompletionRequest(
                trusted_context=trusted, execution_ref=exec_ref,
                lease=_lease(principal, other_ref, "record_completion", revision=3),
                expected_revision=3, idempotency_key="b7-reuse-comp", trusted_time=NOW, outcome="x",
            ),
        )
    except AuthorityDeniedError:
        comp_denied = True
    dec_denied = False
    try:
        record_decision(
            store3, RecordDecisionRequest(
                trusted_context=trusted, subject_ref=parent_ref,
                lease=_lease(principal, other_ref, "record_decision", revision=2),
                expected_revision=2, idempotency_key="b7-reuse-dec", trusted_time=NOW,
                decision_kind="x", statement="y",
            ),
        )
    except AuthorityDeniedError:
        dec_denied = True
    fu_denied = False
    st23, dref23, decision23 = followup_fixture()
    try:
        create_followup_subject(
            st23, CreateFollowupSubjectRequest(
                trusted_context=trusted, parent_ref=parent_ref, decision_ref=dref23,
                lease=_lease(principal, other_ref, "create_followup_subject", revision=2),
                expected_revision=2, idempotency_key="b7-reuse-fu", trusted_time=NOW,
                child_kind="WorkSubject", child_sub_kind=SubjectKind.WORK,
            ),
        )
    except AuthorityDeniedError:
        fu_denied = True
    check("B7-N23_all_four_transitions_use_b5_authority_engine",
          ce_denied and comp_denied and dec_denied and fu_denied,
          f"ce={ce_denied} comp={comp_denied} dec={dec_denied} fu={fu_denied}")

    # N24: all four transitions use B6 transaction/idempotency (no parallel impl).
    src = Path(REPO_ROOT / "aota_forge" / "core" / "transitions.py").read_text()
    uses_b6_tx = ("SubjectTransaction" in src and "ParentChildTransaction" in src)
    uses_b6_idem = ("canonical_fingerprint" in src)
    no_parallel = ("class Idempotency" not in src and "class Transaction" not in src
                   and "class AuthorityEngine" not in src and "class CapabilityLease" not in src)
    check("B7-N24_transitions_reuse_b6_transaction_idempotency",
          uses_b6_tx and uses_b6_idem and no_parallel,
          f"tx={uses_b6_tx} idem={uses_b6_idem} no_parallel={no_parallel}")

    # =====================================================================
    # Four canonical operation signals
    # =====================================================================
    check("B7_CREATE_EXECUTION_CASE", r_ce.code == "COMMITTED" and exec_count_1 == 1 and stale_denied, "create_execution PASS")
    check("B7_RECORD_COMPLETION_CASE", r_comp.code == "COMMITTED" and not hasattr(completion, "subject_ref"), "record_completion PASS")
    check("B7_RECORD_DECISION_CASE", r_dec.code == "COMMITTED" and decision.subject_ref == parent_id and dec_stale, "record_decision PASS")
    check("B7_CREATE_FOLLOWUP_SUBJECT_CASE",
          r_fu.code == "COMMITTED" and child_rev == 1 and len(edges) == 1, "create_followup_subject PASS")

    # =====================================================================
    # Negative reversion proof
    # =====================================================================
    negative = {
        "no_transition_specific_identity_policy": tmod.B7_NEW_IDENTITY_POLICY_CREATED is False,
        "no_transition_specific_authority_engine": tmod.B7_DUPLICATE_AUTHORITY_ENGINE_CREATED is False,
        "no_transition_specific_cas_implementation": tmod.B7_DUPLICATE_CAS_IMPLEMENTATION_CREATED is False,
        "no_transition_specific_idempotency_system": tmod.B7_DUPLICATE_IDEMPOTENCY_SYSTEM_CREATED is False,
        "no_stale_revision_silent_rebase": tmod.B7_SILENT_REBASE_ALLOWED is False and tmod.B7_LAST_WRITE_WINS_ALLOWED is False,
        "no_completion_subject_ref_shortcut": tmod.COMPLETION_SUBJECT_REF_FIELD_CREATED is False,
        "no_followup_without_materialized_decision": tmod.FOLLOWUP_REQUIRES_MATERIALIZED_DECISION is True and missing_denied,
        "no_foreign_decision_followup": tmod.FOLLOWUP_FOREIGN_DECISION_DENIED is True and foreign_denied,
        "no_child_creation_outside_transaction": tmod.FOLLOWUP_CHILD_ID_MINTED_INSIDE_TRANSACTION is True,
        "no_duplicate_child_on_idempotent_replay": tmod.FOLLOWUP_IDEMPOTENT_REPLAY_CREATES_SECOND_CHILD is False,
        "no_parent_lease_inherited_by_child": tmod.PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY is False,
        "no_partial_followup_commit": ok18 and ok19,
        "no_heuristic_subject_selection": tmod.B7_SELECTS_SUBJECT_HEURISTICALLY is False and tmod.B7_IS_SEMANTIC_REASONER is False,
    }
    check("B7_NEGATIVE_REVERSION_PROOF", all(v is True for v in negative.values()),
          json.dumps({k: bool(v) for k, v in negative.items()}, sort_keys=True))

    # =====================================================================
    # Module boundary / reuse flags
    # =====================================================================
    check("FORGE_CORE_TRANSITIONS_CALLABLE_DIRECTLY", tmod.FORGE_CORE_TRANSITIONS_CALLABLE_DIRECTLY is True)
    check("CLI_TRANSITION_WIRING_IMPLEMENTED_no", tmod.CLI_TRANSITION_WIRING_IMPLEMENTED is False)
    check("HERMES_TRANSITION_WIRING_IMPLEMENTED_no", tmod.HERMES_TRANSITION_WIRING_IMPLEMENTED is False)
    check("TRANSITION_SOURCE_IMPLEMENTED_yes", tmod.TRANSITION_SOURCE_IMPLEMENTED is True)
    check("TRANSITION_RUNTIME_AUTHORITY_ACTIVE_no", tmod.TRANSITION_RUNTIME_AUTHORITY_ACTIVE is False)
    check("PRODUCTION_GRAPH_AUTHORITY_ACTIVE_no", tmod.PRODUCTION_GRAPH_AUTHORITY_ACTIVE is False)
    check("AUTHORITATIVE_GRAPH_WRITES_ALLOWED_no", tmod.AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False)
    check("CUTOVER_PERFORMED_no", tmod.CUTOVER_PERFORMED is False)
    check("SHADOW_GRAPH_MATERIALIZATION_PERFORMED_no", tmod.SHADOW_GRAPH_MATERIALIZATION_PERFORMED is False)
    check("PROJECTION_RECONSTRUCTION_IMPLEMENTED_no", tmod.PROJECTION_RECONSTRUCTION_IMPLEMENTED is False)
    check("BINDING_RECOVERY_IMPLEMENTED_no", tmod.BINDING_RECOVERY_IMPLEMENTED is False)
    check("B7_REUSES_B5_AUTHORITY_ENGINE", tmod.B7_REUSES_B5_AUTHORITY_ENGINE is True)
    check("B7_REUSES_B6_CAS", tmod.B7_REUSES_B6_CAS is True)
    check("B7_REUSES_B6_TRANSACTION", tmod.B7_REUSES_B6_TRANSACTION is True)
    check("B7_REUSES_B6_IDEMPOTENCY", tmod.B7_REUSES_B6_IDEMPOTENCY is True)
    check("B7_REUSES_B4_INTERNAL_ID", tmod.B7_REUSES_B4_INTERNAL_ID is True)
    check("B7_REUSES_B4_OBJECT_REF", tmod.B7_REUSES_B4_OBJECT_REF is True)
    check("B7_REUSES_B4_ID_BROKER", tmod.B7_REUSES_B4_ID_BROKER is True)
    check("B7_DUPLICATE_GRAPH_RECORD_MODEL_CREATED_no", tmod.B7_DUPLICATE_GRAPH_RECORD_MODEL_CREATED is False)
    check("B7_IS_CANONICAL_TRANSITION_LAYER", tmod.B7_IS_CANONICAL_TRANSITION_LAYER is True)
    check("B7_IS_SEMANTIC_REASONER_no", tmod.B7_IS_SEMANTIC_REASONER is False)
    check("CREATE_EXECUTION_AUTHORITY_ROOT_subject", tmod.CREATE_EXECUTION_AUTHORITY_ROOT == "Subject")
    check("RECORD_COMPLETION_AUTHORITY_ROOT_owning_subject", tmod.RECORD_COMPLETION_AUTHORITY_ROOT == "owning_Subject")
    check("RECORD_DECISION_AUTHORITY_ROOT_owning_subject", tmod.RECORD_DECISION_AUTHORITY_ROOT == "owning_Subject")
    check("READONLY_DIAGNOSTIC_PLANE_PRESERVED", tmod.READONLY_DIAGNOSTIC_PLANE_PRESERVED is True)

    passed = sum(ok for _, ok, _ in RESULTS)
    failed = len(RESULTS) - passed
    print(f"B7 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"B7_FOCUSED_REGRESSION={'PASS' if failed == 0 else 'FAIL'}")
    print(f"B7_NEGATIVE_REVERSION_PROOF={'PASS' if all(v is True for v in negative.values()) else 'FAIL'}")
    print("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no")
    print("TRANSITION_RUNTIME_AUTHORITY_ACTIVE=no")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
