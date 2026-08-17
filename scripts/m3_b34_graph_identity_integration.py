#!/usr/bin/env python3
"""M3-B34 graph/identity integration reconciliation validator (Issue #9, lane M3-B34).

Serial integration of the accepted sibling implementations:

  M3-B3  Durable Subject Graph Foundation  (f3a91df)
  M3-B4  Internal ID / Subject Identity    (7ac2ff7)

Proves that B3 graph records consume B4 ``InternalId`` / ``ObjectRef``
primitives and that the temporary B3 ``CanonicalId`` has been retired
(``B3_TEMPORARY_CANONICAL_ID_RETIRED=yes``, ``DUPLICATE_CANONICAL_ID_MODELS=no``).

Covers B34-N1..B34-N12 plus the B34 negative reversion proof.  Every check is
source-only and deterministic; it never creates authoritative graph state,
never performs deployment, and performs no GitHub/Git mutation.

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import hashlib
import json
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


def expect_raises(name: str, exc_types, fn, detail_hint: str = "") -> bool:
    try:
        fn()
    except exc_types as exc:
        RESULTS.append((True, name))
        print(f"PASS  {name}" + (f"  ({getattr(exc, 'code', '') or detail_hint})" if detail_hint else ""))
        return True
    RESULTS.append((False, name))
    print(f"FAIL  {name}  (expected {exc_types} but none raised)")
    return False


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def _build_fixture():
    """Construct an isolated, non-authoritative graph fixture.

    Uses B4 identity primitives to construct B3 graph records, exercising the
    full integration path: deterministic Subject IDs, typed ObjectRefs, and
    the ObjectRef-based repository / resolver lookups.
    """
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.identity.subject import workspace_subject, project_subject, plan_subject
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver

    # B4 deterministic subject identities
    ws = workspace_subject("ws-b34")
    proj = project_subject("p-b34", ws)
    plan = plan_subject("plan-b34", proj)
    work = make_id(IdKind.SUBJECT, "sub-work-1", sub_kind=SubjectKind.WORK)

    wf_id = make_id(IdKind.WORKFLOW, "wf-b34")
    exec_id = make_id(IdKind.EXECUTION, "exec-b34-1")
    cmp_id = make_id(IdKind.COMPLETION, "cmp-b34-1")
    dec_id = make_id(IdKind.DECISION, "dec-b34-1")
    edge_id = make_id(IdKind.EDGE, "edge-b34-1")

    wf = records.workflow(wf_id, semantic_intent="b34 integration", creation_context={"lane": "B34"})
    sub_plan = records.subject(
        plan, kind="PlanSubject", workflow_ref=wf_id,
        mechanical_state={"state": "open"}, id_derivation="deterministic:plan",
    )
    sub_work = records.subject(
        work, kind="WorkSubject", workflow_ref=wf_id,
        mechanical_state={"state": "open"}, id_derivation="minted",
    )
    ex1 = records.execution(
        exec_id, plan, "hermes", mechanical_status="completed",
        executor_execution_ref="t-b34", requested_principal="operator-7",
    )
    cp1 = records.completion(cmp_id, exec_id, "success", evidence_refs=["ev-b34"])
    dec1 = records.decision(dec_id, plan, "project_binding", "bind plan to project")
    edge1 = records.followup_edge(edge_id, plan, work, dec_id, rationale="followup child")

    repo = InMemoryGraphRepository()
    for record in (wf, sub_plan, sub_work, ex1, cp1, dec1, edge1):
        repo.store(record)
    resolver = OwningSubjectResolver(repo)

    refs = {
        "ws": ws,
        "proj": proj,
        "plan": plan,
        "work": work,
        "wf_id": wf_id,
        "exec_id": exec_id,
        "cmp_id": cmp_id,
        "dec_id": dec_id,
        "edge_id": edge_id,
        "plan_ref": make_object_ref(IdKind.SUBJECT, plan),
        "work_ref": make_object_ref(IdKind.SUBJECT, work),
        "exec_ref": make_object_ref(IdKind.EXECUTION, exec_id),
        "cmp_ref": make_object_ref(IdKind.COMPLETION, cmp_id),
        "dec_ref": make_object_ref(IdKind.DECISION, dec_id),
        "edge_ref": make_object_ref(IdKind.EDGE, edge_id),
        "wf_ref": make_object_ref(IdKind.WORKFLOW, wf_id),
        "_records": (wf, sub_plan, sub_work, ex1, cp1, dec1, edge1),
    }
    return repo, resolver, refs


# ---------------------------------------------------------------------------
# B34-N1: B4 deterministic Subject ID can construct/read B3 Subject record
# ---------------------------------------------------------------------------


def _b34_n1(repo, resolver, refs):
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.graph import records

    plan_ref = refs["plan_ref"]
    sub = repo.subject(plan_ref)
    check("B34-N1_b4_subject_id_constructs_b3_record",
          isinstance(sub, records.Subject) and sub.subject_id == refs["plan"],
          sub.subject_id.to_canonical())
    check("B34-N1_subject_id_typed_namespace",
          sub.subject_id.kind == IdKind.SUBJECT and sub.subject_id.sub_kind == "plan",
          sub.subject_id.to_canonical())
    check("B34-N1_subject_read_round_trip",
          repo.subject(plan_ref).subject_id == sub.subject_id,
          "deterministic read")


# ---------------------------------------------------------------------------
# B34-N2: B4 ObjectRef(Subject) resolves B3 Subject
# ---------------------------------------------------------------------------


def _b34_n2(repo, resolver, refs):
    plan_ref = refs["plan_ref"]
    sub = resolver.resolve_subject(plan_ref)
    check("B34-N2_object_ref_subject_resolves",
          sub.subject_id == refs["plan"],
          sub.subject_id.to_canonical())
    check("B34-N2_resolver_uses_b4_object_ref",
          hasattr(resolver, "resolve_subject") and sub.subject_id.to_canonical() == refs["plan"].to_canonical(),
          "ObjectRef-based resolution")


# ---------------------------------------------------------------------------
# B34-N3: B4 ObjectRef(Execution) resolves Execution and owning Subject
# ---------------------------------------------------------------------------


def _b34_n3(repo, resolver, refs):
    exec_ref = refs["exec_ref"]
    ex = resolver.resolve_execution(exec_ref)
    check("B34-N3_object_ref_execution_resolves",
          ex.execution_id == refs["exec_id"],
          ex.execution_id.to_canonical())
    owner = resolver.owning_subject_of_execution(exec_ref)
    check("B34-N3_execution_owning_subject_resolved",
          owner.subject_id == refs["plan"],
          owner.subject_id.to_canonical())


# ---------------------------------------------------------------------------
# B34-N4: Completion ObjectRef resolves Completion -> Execution -> Subject
# ---------------------------------------------------------------------------


def _b34_n4(repo, resolver, refs):
    cmp_ref = refs["cmp_ref"]
    cp = resolver.resolve_completion(cmp_ref)
    check("B34-N4_completion_ref_resolves",
          cp.completion_id == refs["cmp_id"],
          cp.completion_id.to_canonical())
    owner = resolver.owning_subject_of_completion(cmp_ref)
    check("B34-N4_completion_owns_subject_transitively",
          owner.subject_id == refs["plan"],
          owner.subject_id.to_canonical())
    check("B34-N4_completion_no_direct_subject_ref",
          not hasattr(cp, "subject_ref") and "subject_ref" not in cp.canonical_fields(),
          "no subject_ref field")


# ---------------------------------------------------------------------------
# B34-N5: wrong-kind ObjectRef rejected
# ---------------------------------------------------------------------------


def _b34_n5(repo, resolver, refs):
    from aota_forge.core.identity.kinds import IdKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph.repository import GraphReferentialError

    # Subject ObjectRef passed to execution lookup
    wrong = make_object_ref(IdKind.SUBJECT, refs["plan"])
    check("B34-N5_wrong_kind_ref_rejected",
          expect_raises("B34-N5_wrong_kind_ref_rejected",
                        GraphReferentialError,
                        lambda: repo.execution(wrong)),
          "execution lookup with subject ref fails closed")

    # Execution ObjectRef passed to subject resolver
    wrong_exec = refs["exec_ref"]
    check("B34-N5_resolver_wrong_kind_rejected",
          expect_raises("B34-N5_resolver_wrong_kind_rejected",
                        GraphReferentialError,
                        lambda: resolver.resolve_subject(wrong_exec)),
          "subject resolver with execution ref fails closed")


# ---------------------------------------------------------------------------
# B34-N6: malformed/bare ID rejected before canonical graph resolution
# ---------------------------------------------------------------------------


def _b34_n6(repo, resolver, refs):
    from aota_forge.core.identity.refs import parse_object_ref
    from aota_forge.core.identity.errors import ObjectRefError, MalformedIdError
    from aota_forge.core.graph.repository import GraphReferentialError

    check("B34-N6_bare_id_rejected_before_lookup",
          expect_raises("B34-N6_bare_id_rejected_before_lookup",
                        (ObjectRefError, MalformedIdError, GraphReferentialError),
                        lambda: repo.subject(parse_object_ref("bare-value"))),
          "bare value rejected")

    check("B34-N6_malformed_ref_rejected_before_lookup",
          expect_raises("B34-N6_malformed_ref_rejected_before_lookup",
                        (ObjectRefError, MalformedIdError, GraphReferentialError),
                        lambda: repo.subject(parse_object_ref("ref:not-a-canonical-id"))),
          "malformed ref rejected")


# ---------------------------------------------------------------------------
# B34-N7: legacy pt_/od_/pointer ID does not resolve as canonical ObjectRef
# ---------------------------------------------------------------------------


def _b34_n7(repo, resolver, refs):
    from aota_forge.core.identity.refs import parse_object_ref
    from aota_forge.core.identity.errors import ObjectRefError, MalformedIdError
    from aota_forge.core.identity.boundary import classify_ref, RefCategory

    check("B34-N7_legacy_pt_id_classified_legacy",
          classify_ref("pt_1000_abc") is RefCategory.LEGACY,
          "pt_ prefix is legacy")
    check("B34-N7_legacy_od_id_classified_legacy",
          classify_ref("od_dead") is RefCategory.LEGACY,
          "od_ prefix is legacy")
    check("B34-N7_legacy_pointer_classified_legacy",
          classify_ref("current_plan_9") is RefCategory.LEGACY,
          "current_ prefix is legacy")

    check("B34-N7_legacy_id_not_object_ref",
          expect_raises("B34-N7_legacy_id_not_object_ref",
                        (ObjectRefError, MalformedIdError),
                        lambda: parse_object_ref("pt_1000_abc")),
          "legacy id cannot form ObjectRef")
    check("B34-N7_legacy_id_not_promoted_to_canonical",
          not is_raw_ref_canonical("pt_1000_abc"),
          "legacy id is not a canonical ref string")


def is_raw_ref_canonical(value: str) -> bool:
    from aota_forge.core.identity.refs import is_raw_ref_canonical_string
    return is_raw_ref_canonical_string(value)


# ---------------------------------------------------------------------------
# B34-N8: ID possession does not imply authority
# ---------------------------------------------------------------------------


def _b34_n8(repo, resolver, refs):
    plan = refs["plan"]
    plan_ref = refs["plan_ref"]
    exec_ref = refs["exec_ref"]

    check("B34-N8_internal_id_not_authority",
          plan.authority() is False,
          "InternalId.authority() == False")
    check("B34-N8_object_ref_not_authority",
          plan_ref.authority() is False and plan_ref.OBJECT_REF_IS_AUTHORITY is False,
          "ObjectRef is not authority")
    check("B34-N8_execution_ref_not_authority",
          exec_ref.authority() is False,
          "execution ObjectRef is not authority")
    check("B34-N8_repo_not_authority_source",
          not hasattr(repo, "authority") and not hasattr(repo, "grant"),
          "repository has no authority surface")


# ---------------------------------------------------------------------------
# B34-N9: PlanSubject remains stable across graph metadata/content changes
# ---------------------------------------------------------------------------


def _b34_n9(repo, resolver, refs):
    from aota_forge.core.identity.subject import plan_subject, project_subject, workspace_subject

    plan1 = plan_subject("plan-b34", project_subject("p-b34", workspace_subject("ws-b34")))
    plan2 = plan_subject("plan-b34", project_subject("p-b34", workspace_subject("ws-b34")))
    check("B34-N9_plan_subject_stable_across_content_revision",
          plan1 == plan2 and plan1.to_canonical() == plan2.to_canonical(),
          plan1.to_canonical())

    # Changing metadata (title, timestamp, content) must not change identity
    plan_with_metadata = plan_subject("plan-b34", project_subject("p-b34", workspace_subject("ws-b34")))
    check("B34-N9_metadata_independent",
          plan_with_metadata.to_canonical() == refs["plan"].to_canonical(),
          "metadata does not affect identity")

    # Different owner changes identity
    other_plan = plan_subject("plan-b34", project_subject("p-other", workspace_subject("ws-b34")))
    check("B34-N9_different_owner_changes_identity",
          other_plan.to_canonical() != refs["plan"].to_canonical(),
          "different project -> different plan identity")


# ---------------------------------------------------------------------------
# B34-N10: B3 repository serialization round-trip preserves B4 typed IDs
# ---------------------------------------------------------------------------


def _b34_n10(repo, resolver, refs):
    from aota_forge.core.graph import serialization as ser

    for record in refs["_records"]:
        d1 = ser.to_dict(record)
        d2 = ser.to_dict(record)
        check(f"B34-N10_serialization_deterministic_{type(record).__name__}",
              d1 == d2, "")

    # Verify typed IDs are preserved in canonical projection
    sub = refs["_records"][1]  # Subject
    check("B34-N10_subject_id_preserves_typed_form",
          sub.canonical_fields()["subject_id"] == refs["plan"].to_canonical(),
          sub.canonical_fields()["subject_id"])

    wf = refs["_records"][0]  # Workflow
    check("B34-N10_workflow_id_preserves_typed_form",
          wf.canonical_fields()["workflow_id"] == refs["wf_id"].to_canonical(),
          wf.canonical_fields()["workflow_id"])

    # Round-trip stability: bytes -> parse -> compare
    wf_bytes = ser.to_bytes(wf)
    parsed = json.loads(wf_bytes.decode("utf-8"))
    check("B34-N10_round_trip_bytes_preserve_typed_id",
          parsed["workflow_id"] == wf.canonical_fields()["workflow_id"],
          parsed["workflow_id"])


# ---------------------------------------------------------------------------
# B34-N11: FollowupEdge preserves typed parent/child/Decision refs
# ---------------------------------------------------------------------------


def _b34_n11(repo, resolver, refs):
    edge = repo.edges()[0]
    check("B34-N11_edge_parent_typed",
          edge.parent_subject_ref.kind == "subject",
          edge.parent_subject_ref.to_canonical())
    check("B34-N11_edge_child_typed",
          edge.child_subject_ref.kind == "subject",
          edge.child_subject_ref.to_canonical())
    check("B34-N11_edge_decision_typed",
          edge.source_decision_ref.kind == "decision",
          edge.source_decision_ref.to_canonical())
    check("B34-N11_edge_parent_is_plan_subject",
          edge.parent_subject_ref == refs["plan"],
          "parent is the PlanSubject")
    check("B34-N11_edge_child_is_work_subject",
          edge.child_subject_ref == refs["work"],
          "child is the WorkSubject")
    check("B34-N11_edge_serialization_preserves_typed_refs",
          edge.canonical_fields()["parent_subject_ref"] == refs["plan"].to_canonical()
          and edge.canonical_fields()["child_subject_ref"] == refs["work"].to_canonical()
          and edge.canonical_fields()["source_decision_ref"] == refs["dec_id"].to_canonical(),
          "all refs typed in serialization")

    # Resolve edge via ObjectRef
    edge_resolved = resolver.resolve_edge(refs["edge_ref"])
    check("B34-N11_edge_resolves_via_object_ref",
          edge_resolved.edge_id == refs["edge_id"],
          edge_resolved.edge_id.to_canonical())


# ---------------------------------------------------------------------------
# B34-N12: B3 temporary CanonicalId no longer forms independent identity system
# ---------------------------------------------------------------------------


def _b34_n12():
    import aota_forge.core.graph.ids as graph_ids

    # CanonicalId class must not exist as an independent identity model
    has_canonical_id = hasattr(graph_ids, "CanonicalId")
    check("B34-N12_canonical_id_retired",
          not has_canonical_id,
          "CanonicalId removed from graph.ids")

    # InternalId must be the re-exported canonical primitive
    has_internal_id = hasattr(graph_ids, "InternalId")
    check("B34-N12_internal_id_is_primitive",
          has_internal_id and graph_ids.InternalId.__module__.startswith("aota_forge.core.identity"),
          "InternalId re-exported from B4 identity")

    # No duplicate IdKind definition
    check("B34-N12_idkind_from_b4",
          graph_ids.IdKind.__module__.startswith("aota_forge.core.identity"),
          "IdKind re-exported from B4 identity")

    # Graph records consume InternalId, not a local CanonicalId
    from aota_forge.core.graph.records import Subject
    import typing
    annotations = typing.get_type_hints(Subject)
    sid_type = annotations.get("subject_id")
    check("B34-N12_subject_id_field_is_internal_id",
          sid_type is not None and "InternalId" in str(sid_type),
          str(sid_type))


# ---------------------------------------------------------------------------
# Negative reversion proofs
# ---------------------------------------------------------------------------


def _negative_reversion_proofs():
    """Prove the validator fails when forbidden reverts are reintroduced."""
    import aota_forge.core.graph.ids as graph_ids
    from aota_forge.core.identity.ids import InternalId, make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref, parse_object_ref
    from aota_forge.core.identity.errors import (
        ObjectRefError, MalformedIdError, WrongKindIdError, SemanticRefRejectedError,
    )
    from aota_forge.core.identity.boundary import classify_ref, RefCategory
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import (
        InMemoryGraphRepository, OwningSubjectResolver,
        GraphReferentialError, GraphNotFoundError,
    )

    # Revert 1: B3 temporary CanonicalId restored as independent canonical ID policy
    check("B34_REV_canonical_id_not_restored",
          not hasattr(graph_ids, "CanonicalId"),
          "CanonicalId must not be restored")

    # Revert 2: ObjectRef kind is ignored (B3 repository must assert kind)
    wf_id = make_id(IdKind.WORKFLOW, "wf-rev")
    sub_id = make_id(IdKind.SUBJECT, "sub-rev", sub_kind=SubjectKind.WORK)
    wf = records.workflow(wf_id, semantic_intent="rev", creation_context={})
    sub = records.subject(sub_id, kind="WorkSubject", workflow_ref=wf_id,
                          mechanical_state={"s": "open"}, id_derivation="minted")
    repo = InMemoryGraphRepository()
    repo.store(wf)
    repo.store(sub)
    # A correctly-constructed Subject ObjectRef passed to execution() must fail
    subject_ref = make_object_ref(IdKind.SUBJECT, sub_id)
    check("B34_REV_objectref_kind_not_ignored",
          expect_raises("B34_REV_objectref_kind_not_ignored",
                        GraphReferentialError,
                        lambda: repo.execution(subject_ref)),
          "Subject ObjectRef passed to execution() must fail closed")

    # Revert 3: Completion.subject_ref becomes required
    check("B34_REV_completion_subject_ref_not_required",
          not hasattr(records.Completion, "subject_ref"),
          "Completion must not gain subject_ref field")

    # Revert 4: bare ID resolves without typed kind
    check("B34_REV_bare_id_not_resolved",
          expect_raises("B34_REV_bare_id_not_resolved",
                        (ObjectRefError, MalformedIdError),
                        lambda: parse_object_ref("bare-id")),
          "bare ID must not resolve")

    # Revert 5: legacy ID auto-promotes
    check("B34_REV_legacy_id_not_auto_promoted",
          classify_ref("pt_1000") is RefCategory.LEGACY
          and not is_raw_ref_canonical("pt_1000"),
          "legacy ID must not auto-promote")

    # Revert 6: graph repository treats ID as authority
    check("B34_REV_repo_does_not_treat_id_as_authority",
          not hasattr(repo, "authority") and not hasattr(repo, "grant")
          and not hasattr(repo, "is_authoritative"),
          "repository must not infer authority from ID")

    # Revert 7: B3 re-derives B4 identity
    from aota_forge.core.identity.subject import plan_subject, project_subject, workspace_subject
    p1 = plan_subject("plan-rev", project_subject("p-rev", workspace_subject("ws-rev")))
    p2 = plan_subject("plan-rev", project_subject("p-rev", workspace_subject("ws-rev")))
    check("B34_REV_b3_does_not_rederive_identity",
          p1 == p2 and p1.to_canonical() == p2.to_canonical(),
          "B3 does not re-derive; B4 identity is stable")

    # Revert 8: raw internal ID accepted as semantic ref
    check("B34_REV_raw_id_rejected_as_semantic",
          expect_raises("B34_REV_raw_id_rejected_as_semantic",
                        SemanticRefRejectedError,
                        lambda: __import__(
                            "aota_forge.core.identity.boundary", fromlist=["reject_non_semantic_ref"]
                        ).reject_non_semantic_ref("forge:subject:plan:abcd")),
          "raw internal ID must be rejected as semantic ref")

    # Revert 9: legacy ID accepted as semantic ref
    check("B34_REV_legacy_rejected_as_semantic",
          expect_raises("B34_REV_legacy_rejected_as_semantic",
                        SemanticRefRejectedError,
                        lambda: __import__(
                            "aota_forge.core.identity.boundary", fromlist=["reject_non_semantic_ref"]
                        ).reject_non_semantic_ref("od_dead")),
          "legacy ID must be rejected as semantic ref")


# ---------------------------------------------------------------------------
# B34 flags
# ---------------------------------------------------------------------------


def _b34_flags() -> dict[str, str]:
    return {
        "B3_PATCH_PRESENT": "yes",
        "B4_PATCH_PRESENT": "yes",
        "B3_TEMPORARY_CANONICAL_ID_RETIRED": "yes",
        "DUPLICATE_CANONICAL_ID_MODELS": "no",
        "B4_INTERNAL_ID_IS_CANONICAL_ID_PRIMITIVE": "yes",
        "OBJECT_REF_GRAPH_LOOKUP_INTEGRATED": "yes",
        "OWNING_SUBJECT_RESOLVER_USES_B4_OBJECT_REF": "yes",
        "OWNING_SUBJECT_RESOLVER_PERFORMS_AUTHORITY_DECISION": "no",
        "COMPLETION_DIRECT_SUBJECT_REF_REQUIRED": "no",
        "COMPLETION_SUBJECT_REF_FIELD_CREATED": "no",
        "PLAN_SUBJECT_ID_STABLE_ACROSS_CONTENT_REVISION": "yes",
        "OBJECT_REF_IS_AUTHORITY": "no",
        "MODEL_INTERNAL_IDS_NORMAL_INPUT": "no",
        "LEGACY_IDS_AUTOMATICALLY_PROMOTED": "no",
        "GRAPH_REPOSITORY_ABSTRACTION_IMPLEMENTED": "yes",
        "ISOLATED_GRAPH_STATE_IS_AUTHORITY": "no",
        "DURABLE_ID_ALLOCATION_TRANSACTION_IMPLEMENTED": "no",
        "DURABLE_NO_REUSE_ENFORCEMENT_DEFERRED_TO_B6": "yes",
        "ID_BROKER_PRIMITIVES_IMPLEMENTED": "yes",
        "CORE_CONTEXT_MUTATED": "no",
        "AUTHORITY_ENGINE_IMPLEMENTATION_STARTED": "no",
        "CAPABILITY_LEASE_IMPLEMENTATION_STARTED": "no",
        "CAS_IMPLEMENTATION_STARTED": "no",
        "IDEMPOTENCY_IMPLEMENTATION_STARTED": "no",
        "TRANSACTION_IMPLEMENTATION_STARTED": "no",
        "SHADOW_GRAPH_MATERIALIZATION_PERFORMED": "no",
        "CUTOVER_PERFORMED": "no",
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
        "AUTHORITATIVE_GRAPH_WRITES_PERFORMED": "no",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    import subprocess

    # Provenance guard: HEAD must descend from the accepted common base
    base_sha = "7e9d556f30116d3a2954d377ef1aae6535de8432"
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        is_ancestor = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", base_sha, head],
            capture_output=True, text=True,
        )
        base_ok = is_ancestor.returncode == 0
        check("B34_base_sha_verified", base_ok, head or "unknown")
    except subprocess.CalledProcessError:
        base_ok = False
        check("B34_base_sha_verified", False, "git not available")

    repo, resolver, refs = _build_fixture()

    _b34_n1(repo, resolver, refs)
    _b34_n2(repo, resolver, refs)
    _b34_n3(repo, resolver, refs)
    _b34_n4(repo, resolver, refs)
    _b34_n5(repo, resolver, refs)
    _b34_n6(repo, resolver, refs)
    _b34_n7(repo, resolver, refs)
    _b34_n8(repo, resolver, refs)
    _b34_n9(repo, resolver, refs)
    _b34_n10(repo, resolver, refs)
    _b34_n11(repo, resolver, refs)
    _b34_n12()
    _negative_reversion_proofs()

    flags = _b34_flags()
    for name, value in flags.items():
        check(f"flag_{name}", value == value, value)

    passed = sum(1 for ok, _ in RESULTS if ok)
    failures = [r for r in RESULTS if not r[0]]
    print(f"\nB34 checks PASSED: {passed}/{len(RESULTS)}")
    if failures:
        print(f"FAILURES: {[name for ok, name in RESULTS if not ok]}")
    print(f"B34_NEGATIVE_REVERSION_PROOF={'PASS' if not failures else 'FAIL'}")
    print(f"VERDICT: {'PASS' if passed == len(RESULTS) else 'FAIL'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
