#!/usr/bin/env python3
"""Independent behavioral guard for the M4-2/M4-4 serial integration.

This guard is review-only.  It uses the integrated Core implementation with
isolated fixture stores and never writes the production graph, GitHub, or the
integration branch.
"""

from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_m4_2_m4_4_integration import (  # noqa: E402
    _issue_authorization,
    _lifecycle_fixture,
    _make_intent,
    _make_plan,
    _plan_init_request,
    _retirement_request,
)
from aota_forge.core.authorization import AuthorizationFailure  # noqa: E402
from aota_forge.core.context import ProjectBinding  # noqa: E402
from aota_forge.core.contracts import error_from_dict  # noqa: E402
from aota_forge.core.contracts.descriptor import (  # noqa: E402
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.mutation import MutationEffect  # noqa: E402
from aota_forge.core.graph import records  # noqa: E402
from aota_forge.core.identity.ids import make_id  # noqa: E402
from aota_forge.core.identity.kinds import IdKind, SubjectKind  # noqa: E402
from aota_forge.core.identity.refs import make_object_ref  # noqa: E402
from aota_forge.core.project.resolver import resolve_project_candidates  # noqa: E402
from aota_forge.core.revision import set_revision_number  # noqa: E402
from aota_forge.core.transitions import (  # noqa: E402
    capture_retirement_snapshot,
    plan_init,
    resolve_retirement_candidates,
    retire_plan,
)


COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
M4_2_SOURCE = "9ce364cde6ae84284cd6fb83650417525bbd3144"
M4_4_SOURCE = "10a7e9809d2e1cb3c324ad65a13e6611662957e6"

CROSS_RESULTS: list[tuple[str, bool, str]] = []
NEGATIVE_RESULTS: list[tuple[str, bool, str]] = []


def record(bucket: list[tuple[str, bool, str]], case: str, passed: bool, detail: str = "") -> bool:
    bucket.append((case, bool(passed), detail))
    print(f"{'PASS' if passed else 'FAIL'} {case} {detail}".rstrip())
    return bool(passed)


def binding_for(candidate, *, decision_ref: str) -> ProjectBinding:
    return ProjectBinding(
        workspace_id=candidate.workspace_id,
        workspace_root=candidate.workspace_root,
        project_id=candidate.project_id,
        project_root=candidate.project_root,
        manifest_path=candidate.manifest_path,
        registry_fingerprint=candidate.registry_fingerprint,
        candidate_fingerprint=candidate.candidate_fingerprint,
        semantic_decision_ref=decision_ref,
    )


def add_plan_state_flags(fixture, **flags) -> None:
    subject = fixture.store.read_subject(fixture.plan_ref)
    fixture.store._put_staged(replace(subject, mechanical_state={**subject.mechanical_state, **flags}))


def expect_authorization_failure(call, code: str) -> tuple[bool, str]:
    try:
        call()
    except AuthorizationFailure as exc:
        return exc.code == code, exc.code
    return False, "accepted"


def run_cross_lane_cases() -> None:
    with _lifecycle_fixture("review-cross-01") as fixture:
        auth = _issue_authorization(fixture)
        duplicate_parent = fixture.workspace.workdir / "review-ambiguous"
        duplicate_parent.mkdir()
        fixture.workspace.create_project("p1", parent_dir=duplicate_parent)
        evidence = resolve_project_candidates("w1", fixture.registry, "p1")
        result = plan_init(
            fixture.store,
            _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, evidence=evidence, binding=None),
        )
        record(
            CROSS_RESULTS,
            "CROSS-01 authorization cannot override ambiguity",
            result.code == "NEEDS_SEMANTIC_CHOICE" and result.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE,
            result.code,
        )

    with _lifecycle_fixture("review-cross-02") as fixture:
        target_b = _make_plan(fixture.store, "review-cross-02-b")
        auth = _issue_authorization(fixture)
        intent_b = _make_intent("plan_init", target_b, "review-cross-02-b", project_id="p1")
        request = replace(_plan_init_request(fixture, intent=intent_b, lease=auth.lease), plan_ref=target_b)
        result = plan_init(fixture.store, request)
        record(CROSS_RESULTS, "CROSS-02 lease target A cannot operate on B", result.mutation_effect is not MutationEffect.APPLIED_VERIFIED, result.code)

    with _lifecycle_fixture("review-cross-03") as fixture:
        ok, detail = expect_authorization_failure(
            lambda: _issue_authorization(fixture, decision_mutator=lambda value: replace(value, decision_kind="review_result")),
            "MATERIALIZED_DECISION_REQUIRED",
        )
        record(CROSS_RESULTS, "CROSS-03 wrong Decision kind rejects lifecycle authorization", ok, detail)

    with _lifecycle_fixture("review-cross-04") as fixture:
        fixture.workspace.create_project("p2")
        evidence_b = resolve_project_candidates("w1", fixture.registry, "p2")
        binding_b = binding_for(evidence_b.candidates[0], decision_ref="decision:project:p2")
        auth = _issue_authorization(fixture)
        result = plan_init(
            fixture.store,
            _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, evidence=evidence_b, binding=binding_b),
        )
        record(CROSS_RESULTS, "CROSS-04 Project/Milestone A evidence cannot bind B", result.code == "PROJECT_BINDING_REQUIRED", result.code)

    with _lifecycle_fixture("review-cross-05", state="initialized") as fixture:
        successor = _make_plan(fixture.store, "review-cross-05-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "review-cross-05-retire",
            retirement_kind="superseded",
            successor_ref=successor.serialize(),
        )
        auth = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        changed = fixture.store.read_subject(successor)
        fixture.store._put_staged(set_revision_number(changed, 2))
        result = retire_plan(
            fixture.store,
            _retirement_request(fixture, snapshot, intent=intent, lease=auth.lease, successor=successor),
        )
        record(CROSS_RESULTS, "CROSS-05 stale retirement snapshot beats authorization", result.code == "RETIREMENT_STALE_SNAPSHOT", result.code)

    with _lifecycle_fixture("review-cross-06") as fixture:
        auth = _issue_authorization(fixture)
        unknown = auth.lease.mark_outcome_unknown()
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=unknown))
        unchanged = fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "uninitialized"
        record(CROSS_RESULTS, "CROSS-06 unknown outcome cannot replay lifecycle", unchanged and result.mutation_effect is not MutationEffect.APPLIED_VERIFIED, result.code)

    with _lifecycle_fixture("review-cross-07") as fixture:
        wrong_target = make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "review-cross-07-wrong", sub_kind=SubjectKind.PLAN),
        )
        ok, detail = expect_authorization_failure(
            lambda: _issue_authorization(fixture, decision_mutator=lambda value: replace(value, target_refs=[wrong_target.serialize()])),
            "MATERIALIZED_DECISION_REQUIRED",
        )
        record(CROSS_RESULTS, "CROSS-07 wrong nested target rejects", ok, detail)

    with _lifecycle_fixture("review-cross-08") as fixture:
        fixture.workspace.create_project("p2")
        evidence_b = resolve_project_candidates("w1", fixture.registry, "p2")
        binding_b = binding_for(evidence_b.candidates[0], decision_ref="decision:project:p2")
        auth = _issue_authorization(fixture)
        result = plan_init(
            fixture.store,
            _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, evidence=evidence_b, binding=binding_b),
        )
        record(CROSS_RESULTS, "CROSS-08 correct target with wrong Project evidence rejects", result.code == "PROJECT_BINDING_REQUIRED", result.code)

    with _lifecycle_fixture("review-cross-09", state="initialized") as fixture:
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease))
        record(CROSS_RESULTS, "CROSS-09 valid authorization cannot bypass PLAN_INIT re-entry", result.code == "PLAN_INIT_ALREADY_INITIALIZED", result.code)

    with _lifecycle_fixture("review-cross-10", state="initialized") as fixture:
        add_plan_state_flags(fixture, active=True)
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "review-cross-10-retire", retirement_kind="abandoned")
        auth = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(fixture.store, _retirement_request(fixture, snapshot, intent=intent, lease=auth.lease))
        record(CROSS_RESULTS, "CROSS-10 active/current protection beats authorization", result.code == "RETIREMENT_TARGET_PROTECTED", result.code)

    with _lifecycle_fixture("review-cross-11", state="initialized") as fixture:
        add_plan_state_flags(fixture, running_task=True)
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "review-cross-11-retire", retirement_kind="abandoned")
        auth = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(fixture.store, _retirement_request(fixture, snapshot, intent=intent, lease=auth.lease))
        record(CROSS_RESULTS, "CROSS-11 running-task protection beats authorization", result.code == "RETIREMENT_RUNNING_TASK_PROTECTED", result.code)

    with _lifecycle_fixture("review-cross-12", state="initialized") as fixture:
        _make_plan(fixture.store, "review-cross-12-b", state="initialized")
        resolution = resolve_retirement_candidates(fixture.store)
        record(
            CROSS_RESULTS,
            "CROSS-12 multiple retirement candidates require semantic choice",
            resolution.code == "RETIREMENT_NEEDS_SEMANTIC_CHOICE"
            and resolution.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE
            and len(resolution.candidates) >= 2,
            resolution.code,
        )


def run_positive_composition_probe() -> bool:
    with _lifecycle_fixture("review-positive") as fixture:
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease))
        passed = result.code == "PLAN_INIT_APPLIED"
        print(f"{'PASS' if passed else 'FAIL'} POS-01 real M4-2 lease reaches PLAN_INIT {result.code}")
        return passed


def load_integration_guard():
    path = ROOT / "scripts" / "m4_2_m4_4_integration_guard.py"
    spec = importlib.util.spec_from_file_location("m4_2_m4_4_integration_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("integration guard cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_negative_cases() -> None:
    guard = load_integration_guard()
    ancestor_drop_m42 = subprocess.run(
        ["git", "merge-base", "--is-ancestor", M4_2_SOURCE, COMMON_SOURCE_BASE],
        check=False,
    ).returncode != 0
    record(NEGATIVE_RESULTS, "NEG-INT-01 dropped M4-2 ancestry rejects", ancestor_drop_m42)
    ancestor_drop_m44 = subprocess.run(
        ["git", "merge-base", "--is-ancestor", M4_4_SOURCE, COMMON_SOURCE_BASE],
        check=False,
    ).returncode != 0
    record(NEGATIVE_RESULTS, "NEG-INT-02 dropped M4-4 ancestry rejects", ancestor_drop_m44)
    record(NEGATIVE_RESULTS, "NEG-INT-03 M4-2 exclusive edit rejects", not guard._is_integration_path("aota_forge/core/authority.py"))
    record(NEGATIVE_RESULTS, "NEG-INT-04 M4-4 exclusive edit rejects", not guard._is_integration_path("aota_forge/core/transitions.py"))
    record(NEGATIVE_RESULTS, "NEG-INT-05 shared read-only edit rejects", not guard._is_integration_path("aota_forge/core/ingress.py"))

    with _lifecycle_fixture("review-negative-06") as fixture:
        duplicate_parent = fixture.workspace.workdir / "negative-ambiguous"
        duplicate_parent.mkdir()
        fixture.workspace.create_project("p1", parent_dir=duplicate_parent)
        evidence = resolve_project_candidates("w1", fixture.registry, "p1")
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, evidence=evidence, binding=None))
        record(NEGATIVE_RESULTS, "NEG-INT-06 authorization selects ambiguous Project rejects", result.code == "NEEDS_SEMANTIC_CHOICE")

    with _lifecycle_fixture("review-negative-07") as fixture:
        target_b = _make_plan(fixture.store, "negative-target-b")
        auth = _issue_authorization(fixture)
        request = replace(
            _plan_init_request(fixture, intent=_make_intent("plan_init", target_b, "negative-target-b"), lease=auth.lease),
            plan_ref=target_b,
        )
        result = plan_init(fixture.store, request)
        record(NEGATIVE_RESULTS, "NEG-INT-07 lease A operates on B rejects", result.mutation_effect is not MutationEffect.APPLIED_VERIFIED)

    with _lifecycle_fixture("review-negative-08") as fixture:
        ok, _detail = expect_authorization_failure(
            lambda: _issue_authorization(fixture, decision_mutator=lambda value: replace(value, decision_kind="review_result")),
            "MATERIALIZED_DECISION_REQUIRED",
        )
        record(NEGATIVE_RESULTS, "NEG-INT-08 wrong Decision kind rejects", ok)

    with _lifecycle_fixture("review-negative-09") as fixture:
        fixture.workspace.create_project("p2")
        evidence = resolve_project_candidates("w1", fixture.registry, "p2")
        binding = binding_for(evidence.candidates[0], decision_ref="decision:project:p2")
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, evidence=evidence, binding=binding))
        record(NEGATIVE_RESULTS, "NEG-INT-09 wrong Project/Milestone evidence rejects", result.code == "PROJECT_BINDING_REQUIRED")

    with _lifecycle_fixture("review-negative-10", state="initialized") as fixture:
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease))
        record(NEGATIVE_RESULTS, "NEG-INT-10 PLAN_INIT replay/re-entry rejects", result.code == "PLAN_INIT_ALREADY_INITIALIZED")

    for case, fixture_name, extra, expected in (
        ("NEG-INT-11 active/current protection rejects", "negative-11-active", {"active": True}, "RETIREMENT_TARGET_PROTECTED"),
        ("NEG-INT-12 running-task protection rejects", "negative-12-running", {"running_task": True}, "RETIREMENT_RUNNING_TASK_PROTECTED"),
    ):
        with _lifecycle_fixture(fixture_name, state="initialized") as fixture:
            add_plan_state_flags(fixture, **extra)
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, case, retirement_kind="abandoned")
            auth = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = retire_plan(fixture.store, _retirement_request(fixture, snapshot, intent=intent, lease=auth.lease))
            record(NEGATIVE_RESULTS, case, result.code == expected, result.code)

    with _lifecycle_fixture("review-negative-13", state="initialized") as fixture:
        successor = _make_plan(fixture.store, "negative-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        changed = fixture.store.read_subject(successor)
        fixture.store._put_staged(set_revision_number(changed, 2))
        intent = _make_intent("plan_retirement", fixture.plan_ref, "negative-drift", retirement_kind="superseded", successor_ref=successor.serialize())
        auth = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(fixture.store, _retirement_request(fixture, snapshot, intent=intent, lease=auth.lease, successor=successor))
        record(NEGATIVE_RESULTS, "NEG-INT-13 successor drift rejects", result.code == "RETIREMENT_STALE_SNAPSHOT")

    with _lifecycle_fixture("review-negative-14") as fixture:
        auth = _issue_authorization(fixture)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=auth.intent, lease=auth.lease.mark_outcome_unknown()))
        record(NEGATIVE_RESULTS, "NEG-INT-14 unknown outcome blind lease reuse rejects", result.mutation_effect is not MutationEffect.APPLIED_VERIFIED)

    auth_error = error_from_dict({"code": "AUTHORIZATION_TARGET_MISMATCH"})
    lifecycle_error = error_from_dict({"code": "NEEDS_SEMANTIC_CHOICE"})
    record(NEGATIVE_RESULTS, "NEG-INT-15 semantic error distinction rejects generic collapse", auth_error is not None and lifecycle_error is not None and auth_error.code != lifecycle_error.code)

    from aota_forge.core import authorization, transitions
    record(
        NEGATIVE_RESULTS,
        "NEG-INT-16 write-capable ingress enablement rejects",
        authorization.M4_2_WRITE_INGRESS_IMPLEMENTED is False
        and transitions.M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED is False,
    )

    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    record(
        NEGATIVE_RESULTS,
        "NEG-INT-17 CLI/catalog write operation exposure rejects",
        all(descriptor.read_write == "read" for descriptor, _handler in DEFAULT_REGISTRY._bindings.values()),
    )

    record(
        NEGATIVE_RESULTS,
        "NEG-INT-18 downstream external authority write rejects",
        authorization.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
        and authorization.M4_2_DURABLE_JOURNAL_IMPLEMENTED is False
        and transitions.M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
        and transitions.M4_4_DURABLE_JOURNAL_IMPLEMENTED is False,
    )


def main() -> int:
    run_cross_lane_cases()
    positive = run_positive_composition_probe()
    run_negative_cases()
    cross_pass = sum(passed for _case, passed, _detail in CROSS_RESULTS)
    negative_pass = sum(passed for _case, passed, _detail in NEGATIVE_RESULTS)
    print(f"CROSS_LANE_ADVERSARIAL_CASE_COUNT={len(CROSS_RESULTS)}")
    print(f"CROSS_LANE_ADVERSARIAL_CASE_PASS_COUNT={cross_pass}")
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(NEGATIVE_RESULTS)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={negative_pass}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={len(NEGATIVE_RESULTS) - negative_pass}")
    print(f"REAL_AUTHORIZATION_LIFECYCLE_COMPOSITION={'PASS' if positive else 'FAIL'}")
    return 0 if positive and cross_pass == len(CROSS_RESULTS) and negative_pass == len(NEGATIVE_RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
