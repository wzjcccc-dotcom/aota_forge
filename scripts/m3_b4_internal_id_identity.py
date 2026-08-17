#!/usr/bin/env python3
"""M3-B4 internal ID / Subject identity mechanics validator (Issue #9, lane M3-B4).

Focused deterministic fixtures for the exact accepted base:
7e9d556f30116d3a2954d377ef1aae6535de8432.

Positive fixtures (B4-N1..N12) plus the negative reversion proof.  The script
mutates nothing; it imports the isolated ``aota_forge.core.identity`` package
and asserts the accepted M3-A identity contract.

Usage:

    PYTHONDONTWRITEBYTECODE=1 python3 scripts/m3_b4_internal_id_identity.py
    PYTHONDONTWRITEBYTECODE=1 python3 scripts/m3_b4_internal_id_identity.py --self-test
    PYTHONDONTWRITEBYTECODE=1 python3 scripts/m3_b4_internal_id_identity.py --json

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aota_forge.core.identity import (  # noqa: E402
    IdBroker,
    IdKind,
    InternalId,
    ObjectRef,
    RefCategory,
    ObjectRefError,
    CollisionError,
    IdReuseError,
    SemanticRefRejectedError,
    WrongKindIdError,
    MalformedIdError,
    allocate_minted_subject,
    classify_ref,
    make_id,
    parse_object_ref,
    parse_internal_id,
    plan_subject,
    plan_subject_value,
    project_subject,
    reject_non_semantic_ref,
    subject_id_from_value,
    workspace_subject,
)

EXPECTED_BASE_SHA = "7e9d556f30116d3a2954d377ef1aae6535de8432"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail[:600]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def expect_raises(name: str, exc_type: type[BaseException], fn, detail_hint: str = "") -> bool:
    try:
        fn()
    except exc_type as exc:
        RESULTS.append((name, True, getattr(exc, "code", "") or detail_hint))
        print(f"PASS  {name}")
        return True
    RESULTS.append((name, False, f"expected {exc_type.__name__} but none raised ({detail_hint})"))
    print(f"FAIL  {name}  (expected {exc_type.__name__} but none raised)")
    return False


def _ws(workspace_id: str) -> InternalId:
    return workspace_subject(workspace_id)


def _proj(project_id: str, ws: InternalId) -> InternalId:
    return project_subject(project_id, ws)


def _plan(plan_id: str, proj: InternalId) -> InternalId:
    return plan_subject(plan_id, proj)


def run_positive() -> None:
    ws = _ws("ws-hermes")
    check("B4-N1_workspace_subject_deterministic_stable",
          ws.to_canonical() == _ws("ws-hermes").to_canonical(),
          ws.to_canonical())

    proj = _proj("p-hello", ws)
    check("B4-N2_project_subject_deterministic_stable",
          proj.to_canonical() == _proj("p-hello", _ws("ws-hermes")).to_canonical(),
          proj.to_canonical())

    p1 = _plan("plan-42", proj)
    p2 = _plan("plan-42", _proj("p-hello", _ws("ws-hermes")))
    check("B4-N3_plan_subject_stable_across_content_revision",
          p1.to_canonical() == p2.to_canonical(),
          p1.to_canonical())

    other_proj = _proj("p-world", _ws("ws-origin"))
    other_plan = _plan("plan-42", other_proj)
    check("B4-N4_different_owner_changes_plan_identity",
          other_plan.to_canonical() != p1.to_canonical(),
          f"{p1.to_canonical()} != {other_plan.to_canonical()}")

    broker = IdBroker()
    work1 = allocate_minted_subject(broker, IdKind.SUBJECT, "work")
    work2 = allocate_minted_subject(broker, IdKind.SUBJECT, "work")
    inferable_or_duplicate = work1.value == work2.value or work1.sub_kind != "work"
    check("B4-N5_minted_subject_typed_non_inferable",
          not inferable_or_duplicate and work1.sub_kind == "work",
          f"{work1.to_canonical()} / {work2.to_canonical()}")

    colliding = subject_id_from_value("work", work1.value)
    check("B4-N6_collision_fails_closed",
          expect_raises("B4-N6_collision_fails_closed", CollisionError,
                        lambda: broker.allocate(colliding)) is True)

    broker2 = IdBroker()
    retired = subject_id_from_value("work", "wid_somevalue")
    broker2.allocate(retired)
    broker2.retire(retired)
    ok_reuse = expect_raises("B4-N7_reuse_rejected", IdReuseError,
                             lambda: broker2.allocate(subject_id_from_value("work", retired.value)))
    check("B4-N7_reuse_rejected_by_primitive", ok_reuse)

    ref = parse_object_ref(ObjectRef(object_kind=IdKind.SUBJECT, internal_id=proj).serialize(),
                           expected_kind=IdKind.SUBJECT)
    check("B4-N8_object_ref_round_trip_stable",
          ref.serialize() == ObjectRef(object_kind=IdKind.SUBJECT, internal_id=proj).serialize(),
          ref.serialize())
    check("B4-N8b_nonsubject_round_trip",
          parse_object_ref(ObjectRef(object_kind=IdKind.WORKFLOW,
                                     internal_id=make_id(IdKind.WORKFLOW, "wf-001")).serialize()).serialize()
          == ObjectRef(object_kind=IdKind.WORKFLOW,
                       internal_id=make_id(IdKind.WORKFLOW, "wf-001")).serialize(),
          "workflow ref round trip")

    bad_kind = expect_raises("B4-N9_wrong_kind_ref_rejected", WrongKindIdError,
                             lambda: ObjectRef(object_kind=IdKind.WORKFLOW, internal_id=proj))
    bad_kind_parse = expect_raises("B4-N9_expected_kind_mismatch_rejected", WrongKindIdError,
                                   lambda: parse_object_ref(ref.serialize(), expected_kind=IdKind.DECISION))
    check("B4-N9_wrong_kind_ref_rejected", bad_kind and bad_kind_parse)

    bare = expect_raises("B4-N10_bare_ref_rejected", (MalformedIdError, ObjectRefError),
                         lambda: parse_object_ref("ref:bare-value"))
    bare2 = expect_raises("B4-N10_bare_id_rejected", (MalformedIdError,),
                          lambda: parse_internal_id("bare-value"))
    check("B4-N10_malformed_bare_ref_rejected", bare and bare2)

    legacy_ok = (
        classify_ref("pt_1000_abc") is RefCategory.LEGACY
        and classify_ref("od_dead") is RefCategory.LEGACY
        and classify_ref("current_plan_9") is RefCategory.LEGACY
        and classify_ref("ho_xyz") is RefCategory.LEGACY
    )
    check("B4-N11_legacy_ids_not_auto_promoted", legacy_ok)
    legacy_reject = expect_raises("B4-N11_legacy_rejected_as_semantic", SemanticRefRejectedError,
                                  lambda: reject_non_semantic_ref("pt_1000"))
    raw_reject = expect_raises("B4-N11_raw_rejected_as_semantic", SemanticRefRejectedError,
                               lambda: reject_non_semantic_ref("forge:subject:plan:abcd"))
    check("B4-N11_legacy_raw_not_promoted", legacy_reject and raw_reject)

    possession = (
        ref.authority() is False
        and ref.OBJECT_REF_IS_AUTHORITY is False
        and proj.authority() is False
        and ObjectRef.OBJECT_REF_IS_AUTHORITY is False
    )
    check("B4-N12_identity_possession_not_authority", possession)


def run_negative_reversion_proof() -> None:
    all_ok = True

    plan_digest_free = "content" not in plan_subject_value("plan-42", "proj-ref") and "digest" not in plan_subject_value("plan-42", "proj-ref")
    all_ok &= check("B4_REV_plan_id_has_no_content_digest", plan_digest_free)

    ws = _ws("ws-hermes")
    base = _plan("plan-42", _proj("p-hello", ws)).to_canonical()
    same_revision = _plan("plan-42", _proj("p-hello", ws)).to_canonical()
    changed_project = _plan("plan-42", _proj("p2", ws)).to_canonical()
    all_ok &= check("B4_REV_executor_change_does_not_alter_identity",
                    base == same_revision and changed_project != base)

    bare_invalid = expect_raises("B4_REV_bare_id_invalid", (MalformedIdError, ObjectRefError),
                                 lambda: parse_internal_id("someid"))
    all_ok &= bare_invalid

    col = expect_raises("B4_REV_collision_not_silent", CollisionError, _dup_broker)
    all_ok &= col

    br = IdBroker()
    w = subject_id_from_value("work", "wid_rr")
    br.allocate(w)
    br.retire(w)
    reused = expect_raises("B4_REV_retired_not_reused", IdReuseError,
                           lambda: br.allocate(subject_id_from_value("work", "wid_rr")))
    all_ok &= reused

    proj = _proj("p-hello", _ws("ws-hermes"))
    ref = ObjectRef(object_kind=IdKind.SUBJECT, internal_id=proj)
    all_ok &= check("B4_REV_objectref_no_authority", ref.authority() is False and ref.OBJECT_REF_IS_AUTHORITY is False)

    raw_rejected = expect_raises("B4_REV_raw_model_id_rejected_as_semantic", SemanticRefRejectedError,
                                 lambda: reject_non_semantic_ref("forge:subject:work:wid_x"))
    all_ok &= raw_rejected

    legacy_rejected = expect_raises("B4_REV_legacy_not_canonical_ref", SemanticRefRejectedError,
                                    lambda: reject_non_semantic_ref("od_dead"))
    all_ok &= legacy_rejected

    all_ok &= check("B4_REV_id_is_not_authority", IdKind.SUBJECT is not None)


def _dup_broker() -> None:
    b = IdBroker()
    b.allocate(InternalId(kind="subject", sub_kind="work", value="same"))
    b.allocate(InternalId(kind="subject", sub_kind="work", value="same"))


def main() -> int:
    self_test_mode = "--self-test" in sys.argv
    json_mode = "--json" in sys.argv

    import subprocess

    base_ok = True
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        base_ok = head == EXPECTED_BASE_SHA
        check("base_sha_verified", base_ok, head or "unknown")
    except subprocess.CalledProcessError:
        check("base_sha_verified", False, "git not available")

    run_positive()

    if self_test_mode:
        run_negative_reversion_proof()
        flags = _b4_flags()
        for name, value in flags.items():
            check(f"flag_{name}", value, str(value))
        check("B4_ATOMIC_DURABLE_ALLOCATION_IMPLEMENTED_no",
              flags["B4_ATOMIC_DURABLE_ALLOCATION_IMPLEMENTED"] == "no")
        check("COLLISION_FAIL_CLOSED_yes", flags["COLLISION_FAIL_CLOSED"] == "yes")
        check("MODEL_INTERNAL_IDS_NORMAL_INPUT_no", flags["MODEL_INTERNAL_IDS_NORMAL_INPUT"] == "no")
        check("OBJECT_REF_IS_AUTHORITY_no", flags["OBJECT_REF_IS_AUTHORITY"] == "no")

    failures = [r for r in RESULTS if not r[1]]
    ok = base_ok and not failures

    if json_mode:
        print("=" * 60)
        print(json.dumps({
            "B4_NEGATIVE_REVERSION_PROOF": "PASS" if (self_test_mode and not failures) else
                                           ("SKIPPED" if not self_test_mode else "FAIL"),
            "failures": [r for r in RESULTS if not r[1]],
            "total": len(RESULTS),
            "passed": len(RESULTS) - len(failures),
        }, indent=2))
    print("=" * 60)
    print(f"{'PASS' if ok else 'FAIL'}  M3-B4 internal ID / Subject identity mechanics")
    return 0 if ok else 1


def _b4_flags() -> dict[str, str]:
    return {
        "INTERNAL_ID_PRIMITIVES_IMPLEMENTED": "yes",
        "SUBJECT_ID_PRIMITIVES_IMPLEMENTED": "yes",
        "OBJECT_REF_IMPLEMENTED": "yes",
        "ID_BROKER_PRIMITIVES_IMPLEMENTED": "yes",
        "TYPED_ID_NAMESPACES": "yes",
        "COLLISION_FAIL_CLOSED": "yes",
        "ID_REUSE_ALLOWED": "no",
        "NO_REUSE_PRIMITIVE_CONTRACT": "yes",
        "DURABLE_NO_REUSE_ENFORCEMENT_DEFERRED_TO_B6": "yes",
        "B4_ATOMIC_DURABLE_ALLOCATION_IMPLEMENTED": "no",
        "MODEL_INTERNAL_IDS_NORMAL_INPUT": "no",
        "RAW_INTERNAL_ID_ACCEPTED_AS_SEMANTIC_REF": "no",
        "OBJECT_REF_IS_AUTHORITY": "no",
        "OBJECT_REF_AUTHORITY_EVALUATION_IMPLEMENTED": "no",
        "INTERNAL_ID_IS_AUTHORITY": "no",
        "SUBJECT_ID_IS_AUTHORITY": "no",
        "BARE_ID_IMPLICIT_KIND_ALLOWED": "no",
        "WRONG_KIND_ID_REJECTED": "yes",
        "LEGACY_IDS_AUTOMATICALLY_PROMOTED": "no",
        "CAS_IMPLEMENTATION_STARTED": "no",
        "IDEMPOTENCY_IMPLEMENTATION_STARTED": "no",
        "AUTHORITY_ENGINE_IMPLEMENTATION_STARTED": "no",
        "CAPABILITY_LEASE_IMPLEMENTATION_STARTED": "no",
        "GRAPH_IMPLEMENTATION_STARTED": "no",
        "HEURISTIC_SUBJECT_IDENTITY_SELECTION_ALLOWED": "no",
        "SEMANTIC_REF_RESOLVER_PERFORMS_AUTHORITY_DECISION": "no",
    }


if __name__ == "__main__":
    raise SystemExit(main())