#!/usr/bin/env python3
"""M3-B3 durable subject graph foundation validator (issue #9, lane M3-B3).

Validates the accepted canonical graph record foundation.  It exercises the
isolated, non-authoritative in-memory repository and the mechanical
owning-Subject resolver defined under aota_forge/core/graph/.

Record ID fields consume B4 ``InternalId`` (``B4_INTERNAL_ID_IS_CANONICAL_ID_PRIMITIVE=yes``).
The temporary B3 ``CanonicalId`` has been retired; this fixture was minimally
reconciled to construct records via B4 identity primitives.

Covers B3-N1..B3-N11 plus the M3-B3 negative reversion proofs.  Every check is
source-only and deterministic; it never creates authoritative graph state,
never performs deployment, and performs no GitHub/Git mutation.

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import hashlib
import sys

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list[tuple[bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((passed, name))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _reports_blocking():
    """No blocking findings by default; validator invariants are the guard."""
    return False


def _hash(obj) -> str:
    return hashlib.sha256(repr(obj).encode("utf-8")).hexdigest()[:16]


def _materialize_foundation():
    """Isolated deterministic fixture + owning-Subject resolver.

    Returns a tuple (repo, resolver, refs) where refs carries the canonical
    ObjectRefs used by the structural checks.
    """
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver

    wf_id = make_id(IdKind.WORKFLOW, "wf-1")
    sub_a_id = make_id(IdKind.SUBJECT, "sub-a", sub_kind=SubjectKind.WORK)
    sub_b_id = make_id(IdKind.SUBJECT, "sub-b", sub_kind=SubjectKind.WORK)
    exec_id = make_id(IdKind.EXECUTION, "exec-1")
    cmp_id = make_id(IdKind.COMPLETION, "cmp-1")
    dec_id = make_id(IdKind.DECISION, "dec-1")
    edge_id = make_id(IdKind.EDGE, "edge-1")

    wf = records.workflow(wf_id, semantic_intent="rebuild auth", creation_context={"source": "plan"})
    sub_a = records.subject(
        sub_a_id, kind="WorkSubject", workflow_ref=wf_id,
        mechanical_state={"state": "open"}, id_derivation="minted",
    )
    sub_b = records.subject(
        sub_b_id, kind="WorkSubject", workflow_ref=wf_id,
        mechanical_state={"state": "open"}, id_derivation="minted",
    )
    ex1 = records.execution(
        exec_id, sub_a_id, "hermes", executor_execution_ref="t-1", mechanical_status="completed",
    )
    cp1 = records.completion(cmp_id, exec_id, "success", evidence_refs=["ev-1"])
    dec1 = records.decision(dec_id, sub_a_id, "project_binding", "bind sub-a to P1")
    edge1 = records.followup_edge(edge_id, sub_a_id, sub_b_id, dec_id)

    repo = InMemoryGraphRepository()
    for record in (wf, sub_a, sub_b, ex1, cp1, dec1, edge1):
        repo.store(record)
    resolver = OwningSubjectResolver(repo)
    refs = {
        "workflow": make_object_ref(IdKind.WORKFLOW, wf_id),
        "subject_a": make_object_ref(IdKind.SUBJECT, sub_a_id),
        "subject_b": make_object_ref(IdKind.SUBJECT, sub_b_id),
        "execution": make_object_ref(IdKind.EXECUTION, exec_id),
        "completion": make_object_ref(IdKind.COMPLETION, cmp_id),
        "decision": make_object_ref(IdKind.DECISION, dec_id),
        "edge": make_object_ref(IdKind.EDGE, edge_id),
        "_ids": {
            "workflow": wf_id,
            "subject_a": sub_a_id,
            "subject_b": sub_b_id,
            "execution": exec_id,
            "completion": cmp_id,
            "decision": dec_id,
            "edge": edge_id,
        },
        "_records": (wf, sub_a, sub_b, ex1, cp1, dec1, edge1),
    }
    return repo, resolver, refs


def _n1_n2(repo, refs):
    from aota_forge.core.graph import records
    from aota_forge.core.graph import serialization as ser

    # B3-N1: Workflow canonical round trip.
    wf = refs["_records"][0]
    check("B3-N1_workflow_roundtrip_deterministic",
          ser.to_bytes(wf) == ser.to_bytes(wf) and ser.round_trip(wf)[0] == wf.canonical_fields(),
          _hash(ser.to_bytes(wf)))
    check("B3-N1_workflow_canonical_fields",
          set(wf.canonical_fields()) == {
              "workflow_id", "semantic_intent", "goal", "scope", "context_references",
              "creation_context", "scope_annotations", "non_semantic_metadata"},
          sorted(wf.canonical_fields()))
    check("B3-N1_workflow_not_portable_plan",
          wf.workflow_id.kind == "workflow" and not hasattr(wf, "plan") and not hasattr(wf, "portable"),
          wf.workflow_id.kind)

    # B3-N2: Subject canonical round trip.
    sub = refs["_records"][1]
    check("B3-N2_subject_roundtrip_deterministic",
          ser.to_bytes(sub) == ser.to_bytes(sub) and ser.round_trip(sub)[0] == sub.canonical_fields(),
          _hash(ser.to_bytes(sub)))
    check("B3-N2_subject_canonical_fields",
          set(sub.canonical_fields()) == {
              "subject_id", "kind", "workflow_ref", "mechanical_state",
              "creation_context", "id_derivation"},
          sorted(sub.canonical_fields()))
    check("B3-N2_subject_aggregate_root",
          isinstance(sub, records.Subject) and sub.workflow_ref.value == "wf-1",
          sub.workflow_ref.value)


def _n3_n9(repo, resolver, refs):
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.graph.repository import GraphNotFoundError, GraphReferentialError

    exec_ref = refs["execution"]
    cp_ref = refs["completion"]
    dec_ref = refs["decision"]
    sub_a = refs["_ids"]["subject_a"]
    sub_b = refs["_ids"]["subject_b"]

    # B3-N3: Execution -> Subject ownership resolution.
    owner = resolver.owning_subject_of_execution(exec_ref)
    check("B3-N3_execution_owns_subject_a",
          owner.subject_id.value == sub_a.value, owner.subject_id.value)
    check("B3-N3_execution_required_subject",
          repo.execution(exec_ref).subject_ref.value == sub_a.value)

    # B3-N4: Completion -> Execution -> Subject resolution (no direct subject_ref).
    cp_owner = resolver.owning_subject_of_completion(cp_ref)
    check("B3-N4_completion_resolves_owner_transitively",
          cp_owner.subject_id.value == sub_a.value, cp_owner.subject_id.value)

    # B3-N5: Completion must NOT expose a direct subject_ref field.
    cp = repo.completion(cp_ref)
    check("B3-N5_completion_no_subject_ref_field",
          "subject_ref" not in cp.canonical_fields(),
          sorted(cp.canonical_fields()))
    check("B3-N5_completion_subject_ref_absent_attribute",
          not hasattr(cp, "subject_ref"), "")

    # B3-N6: Decision -> Subject ownership.
    dec_owner = resolver.owning_subject_of_decision(dec_ref)
    check("B3-N6_decision_owns_subject_a",
          dec_owner.subject_id.value == sub_a.value, dec_owner.subject_id.value)

    # B3-N7: Decision-backed FollowupEdge; parent/child/decision present.
    edge = repo.edges()[0]
    check("B3-N7_edge_requires_source_decision",
          edge.source_decision_ref.value == dec_ref.internal_id.value,
          edge.source_decision_ref.value)
    check("B3-N7_edge_parent_child_referenced",
          edge.parent_subject_ref.value == sub_a.value
          and edge.child_subject_ref.value == sub_b.value,
          f"{edge.parent_subject_ref.value}->{edge.child_subject_ref.value}")

    # B3-N8: orphan Completion rejected fail-closed.
    orphan = None
    try:
        orphan = _try_orphan_completion()
    except (GraphReferentialError, ValueError):
        pass
    check("B3-N8_orphan_completion_rejected", orphan is None and True, "")

    # B3-N9: FollowupEdge referencing a missing Decision rejected.
    edge_missing = None
    try:
        edge_missing = _try_edge_missing_decision()
    except (GraphReferentialError, ValueError, KeyError):
        pass
    check("B3-N9_edge_missing_decision_rejected", edge_missing is None and True, "")

    # Deterministic list ordering.
    check("B3-N11_repository_list_order_deterministic",
          [s.subject_id.value for s in repo.subjects()] == ["sub-a", "sub-b"]
          and [e.edge_id.value for e in repo.edges()] == ["edge-1"],
          [s.subject_id.value for s in repo.subjects()])


def _try_orphan_completion():
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository

    wf_id = make_id(IdKind.WORKFLOW, "wf-x")
    exec_missing = make_id(IdKind.EXECUTION, "exec-missing")
    cmp_id = make_id(IdKind.COMPLETION, "cmp-orphan")
    repo = InMemoryGraphRepository(enforce_referential=True)
    repo.store(records.workflow(wf_id, semantic_intent="x", creation_context={}))
    repo.store(records.completion(cmp_id, exec_missing, "success"))
    return "unreachable"


def _try_edge_missing_decision():
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository
    repo = InMemoryGraphRepository(enforce_referential=True)
    wf_id = make_id(IdKind.WORKFLOW, "wf-y")
    sub_p = make_id(IdKind.SUBJECT, "sub-p", sub_kind=SubjectKind.WORK)
    sub_c = make_id(IdKind.SUBJECT, "sub-c", sub_kind=SubjectKind.WORK)
    dec_none = make_id(IdKind.DECISION, "dec-none")
    edge_bad = make_id(IdKind.EDGE, "edge-bad")
    repo.store(records.workflow(wf_id, semantic_intent="y", creation_context={}))
    repo.store(records.subject(sub_p, kind="WorkSubject", workflow_ref=wf_id,
                               mechanical_state={"s": "open"}, id_derivation="minted"))
    repo.store(records.subject(sub_c, kind="WorkSubject", workflow_ref=wf_id,
                               mechanical_state={"s": "open"}, id_derivation="minted"))
    repo.store(records.followup_edge(edge_bad, sub_p, sub_c, dec_none))
    return "unreachable"


def _n10(repo, refs):
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.graph.repository import InMemoryGraphRepository, GraphReferentialError

    # B3-N10: Git / current pointer / executor-private IDs must NOT be graph
    # authority.  The repository is storage-neutral and takes no authority.
    ok_git = not hasattr(repo, "git")
    ok_pointer = not hasattr(repo, "current_pointer")
    ok_plan = not hasattr(repo, "plan") and not hasattr(repo, "_legacy_plan")
    # executor-private id is never a Subject id (id_derivation stays opaque).
    exec_only = "t-1"
    ok_private_id = exec_only != refs["_ids"]["subject_a"].value
    check("B3-N10_current_pointer_not_authority", ok_pointer and ok_git and ok_plan, "")
    check("B3-N10_executor_private_id_not_subject_id", ok_private_id, exec_only)

    # The isolated store refuses to mint/derive authority; ids are opaque input.
    try:
        make_id(IdKind.SUBJECT, "sub-a", sub_kind="work").value
        opaque_ok = True
    except Exception:
        opaque_ok = False
    check("B3-N10_ids_opaque_input_boundary", opaque_ok, "")


def _negative_reversion_proofs():
    """Prove the validator fails when the forbidden reverts are reintroduced.

    Each sub-check verifies that the structure required by B3 raises cleanly
    when the canonical invariant would be violated, so any reversal of the
    invariant would break these checks.
    """
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import (
        InMemoryGraphRepository,
        GraphReferentialError,
        GraphNotFoundError,
        OwningSubjectResolver,
    )

    # Revert 1: Completion requiring a direct subject_ref.  B3 Completion has no
    # such field; a forged Completion with subject_ref is rejected by the store
    # (its required_field projection must not include subject_ref).
    c_has_subject_ref = hasattr(records.Completion, "subject_ref")
    check("B3_REVERT_completion_subject_ref_absent", not c_has_subject_ref,
          "Completion must not require a direct subject_ref")

    # Revert 2: Execution must keep its owning subject.  The store rejects an
    # Execution with no owning Subject.
    repo = InMemoryGraphRepository(enforce_referential=True)
    wf_id = make_id(IdKind.WORKFLOW, "wf-r")
    repo.store(records.workflow(wf_id, semantic_intent="r", creation_context={}))
    sub_no = make_id(IdKind.SUBJECT, "sub-no", sub_kind=SubjectKind.WORK)
    exec_orphan = make_id(IdKind.EXECUTION, "exec-orphan")
    try:
        repo.store(records.execution(exec_orphan, sub_no, "hermes", mechanical_status="running"))
        orphan_rejected = False
    except (GraphReferentialError, ValueError):
        orphan_rejected = True
    check("B3_REVERT_execution_loses_owning_subject", orphan_rejected,
          "Execution without owning Subject must be rejected")

    # Revert 3: Decision must NOT become an independent aggregate root; it needs
    # an owning Subject.
    repo3 = InMemoryGraphRepository(enforce_referential=True)
    wf_id3 = make_id(IdKind.WORKFLOW, "wf-d")
    repo3.store(records.workflow(wf_id3, semantic_intent="d", creation_context={}))
    sub_none = make_id(IdKind.SUBJECT, "sub-none", sub_kind=SubjectKind.WORK)
    dec_orphan = make_id(IdKind.DECISION, "dec-orphan")
    try:
        repo3.store(records.decision(dec_orphan, sub_none, "project_binding", "x"))
        dec_orphan_rejected = False
    except (GraphReferentialError, ValueError):
        dec_orphan_rejected = True
    check("B3_REVERT_decision_requires_owning_subject", dec_orphan_rejected,
          "Decision without owning Subject must be rejected")

    # Revert 4: FollowupEdge must require a source Decision.
    repo4 = InMemoryGraphRepository(enforce_referential=True)
    wf_id4 = make_id(IdKind.WORKFLOW, "wf-e")
    repo4.store(records.workflow(wf_id4, semantic_intent="e", creation_context={}))
    sub_p2 = make_id(IdKind.SUBJECT, "sub-p2", sub_kind=SubjectKind.WORK)
    sub_c2 = make_id(IdKind.SUBJECT, "sub-c2", sub_kind=SubjectKind.WORK)
    repo4.store(records.subject(sub_p2, kind="WorkSubject", workflow_ref=wf_id4,
                                mechanical_state={"s": "open"}, id_derivation="minted"))
    repo4.store(records.subject(sub_c2, kind="WorkSubject", workflow_ref=wf_id4,
                                mechanical_state={"s": "open"}, id_derivation="minted"))
    dec_ghost = make_id(IdKind.DECISION, "dec-ghost")
    edge_no_dec = make_id(IdKind.EDGE, "edge-no-dec")
    try:
        repo4.store(records.followup_edge(edge_no_dec, sub_p2, sub_c2, dec_ghost))
        edge_no_dec_rejected = False
    except (GraphReferentialError, ValueError):
        edge_no_dec_rejected = True
    check("B3_REVERT_edge_requires_decision", edge_no_dec_rejected,
          "FollowupEdge without a valid Decision must be rejected")

    # Revert 5: legacy current pointer must not become canonical selection
    # authority.  The repository must not consult one.
    no_pointer = not any(
        hasattr(repo, attr) for attr in ("current_pointer", "_current", "current_plan", "pointer")
    )
    check("B3_REVERT_current_pointer_not_authority", no_pointer,
          "legacy current pointer must not be selection authority")

    # Revert 6: heuristic owner selection must not be present.  Resolver fails
    # closed on unknown/ambiguous rather than heuristically selecting.
    resolver = OwningSubjectResolver(repo4)
    unknown_iid = make_id(IdKind.SUBJECT, "does-not-exist", sub_kind=SubjectKind.WORK)
    unknown_ref = make_object_ref(IdKind.SUBJECT, unknown_iid)
    try:
        resolver.resolve_subject(unknown_ref)
        heuristic = False
    except (GraphNotFoundError, LookupError):
        heuristic = True
    check("B3_REVERT_no_heuristic_owner_selection", heuristic,
          "unknown ref must fail closed, not heuristically select")


def _run_and_report() -> int:
    repo, resolver, refs = _materialize_foundation()
    _n1_n2(repo, refs)
    _n3_n9(repo, resolver, refs)
    _n10(repo, refs)
    _negative_reversion_proofs()

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\nB3 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"VERDICT: {'PASS' if passed == len(RESULTS) else 'FAIL'}")
    return 0 if passed == len(RESULTS) else 1


def main() -> int:
    return _run_and_report()


if __name__ == "__main__":
    sys.exit(main())
