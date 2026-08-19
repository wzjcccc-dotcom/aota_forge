#!/usr/bin/env python3
"""Review-only negative matrix for the repaired M4-4 lifecycle source."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aota_forge.core.bootstrap import ensure_handlers_bound
from aota_forge.core.context import ProjectBinding
from aota_forge.core.contracts.mutation import MutationEffect, MutationIntent, MutationPreconditions
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.regression.fixtures import (
    TempWorkspaceFixture,
    fixture_time,
    make_test_context,
    make_test_lease,
    make_test_store,
    seed_test_subject,
)
from aota_forge.core.revision import set_revision_number
from aota_forge.core.transitions import (
    M4_4_CAPABILITY_LEASE_ISSUER_IMPLEMENTED,
    M4_4_DURABLE_JOURNAL_IMPLEMENTED,
    M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED,
    M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED,
    PlanInitRequest,
    PlanRetirementRequest,
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
)


def make_plan(store, name: str, state: str = "initialized", **extra):
    mechanical = {
        "state": state,
        "authority_source_revision": "7",
        "authority_observed_raw_digest": "a" * 64,
    }
    mechanical.update(extra)
    subject_id = make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN)
    return seed_test_subject(store, subject_id, kind="plan", mechanical_state=mechanical)


def preconditions(revision: int = 1):
    return MutationPreconditions(
        subject_expected_revision=revision,
        authority_source_revision="7",
        authority_observed_raw_digest="a" * 64,
    )


def intent(operation: str, target, key: str, **semantic):
    return MutationIntent(
        operation=operation,
        semantic_inputs=semantic,
        logical_target=target.serialize(),
        mutation_scope={"aggregate": "plan_subject"},
        idempotency_key=key,
    )


def retirement_request(context, target, snapshot, key: str, kind: str, successor=None, lease=True):
    semantic = {"retirement_kind": kind}
    if successor is not None:
        semantic["successor_ref"] = successor.serialize()
    return PlanRetirementRequest(
        trusted_context=context,
        plan_ref=target,
        snapshot=snapshot,
        intent=intent("plan_retirement", target, key, **semantic),
        preconditions=preconditions(snapshot.source_revision),
        trusted_time=fixture_time(),
        retirement_kind=kind,
        successor_ref=successor,
        lease=(
            make_test_lease(
                operation="plan_retirement",
                target_ref=target,
                expected_revision=snapshot.source_revision,
                lease_id=key,
            )
            if lease
            else None
        ),
    )


def binding_fixture(workspace):
    workspace.create_project("p1")
    registry = workspace.create_registry("w1")
    evidence = resolve_project_candidates("w1", registry, "p1")
    candidate = evidence.candidates[0]
    binding = ProjectBinding(
        workspace_id=candidate.workspace_id,
        workspace_root=candidate.workspace_root,
        project_id=candidate.project_id,
        project_root=candidate.project_root,
        manifest_path=candidate.manifest_path,
        registry_fingerprint=candidate.registry_fingerprint,
        candidate_fingerprint=candidate.candidate_fingerprint,
        semantic_decision_ref="decision:project:p1",
    )
    return evidence, binding


def init_request(context, target, key, evidence, binding, revision=1):
    return PlanInitRequest(
        trusted_context=context,
        plan_ref=target,
        intent=intent("plan_init", target, key, project_id="p1", requested_state="initialized"),
        preconditions=preconditions(revision),
        trusted_time=fixture_time(),
        project_evidence=evidence,
        project_binding=binding,
        lease=make_test_lease(
            operation="plan_init",
            target_ref=target,
            expected_revision=revision,
            lease_id=key,
        ),
    )


def case_missing_successor(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-01-target")
    successor = make_plan(store, "neg-r2-01-successor")
    store._subjects.pop(successor.internal_id.value)
    snapshot = capture_retirement_snapshot(store, target)
    try:
        result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-01", "superseded", successor, False))
    except Exception:
        return False
    return result.code == "RETIREMENT_SUCCESSOR_INVALID"


def case_missing_successor_no_mutation(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-02-target")
    successor = make_plan(store, "neg-r2-02-successor")
    store._subjects.pop(successor.internal_id.value)
    snapshot = capture_retirement_snapshot(store, target)
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-02", "superseded", successor, False))
    return (
        result.mutation_effect is MutationEffect.NO_EFFECT
        and store.read_subject(target).mechanical_state["state"] == "initialized"
        and store.current_revision(target).revision_number == 1
    )


def case_unexpected_error_surfaces(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-03-target")
    successor = make_plan(store, "neg-r2-03-successor")
    snapshot = capture_retirement_snapshot(store, target)
    original_read = store.read_subject

    def failing_read(ref):
        if ref == successor:
            raise RuntimeError("injected successor infrastructure failure")
        return original_read(ref)

    store.read_subject = failing_read
    try:
        retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-03", "superseded", successor))
    except RuntimeError:
        return True
    except Exception:
        return False
    return False


def case_successor_removed(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-04-target")
    successor = make_plan(store, "neg-r2-04-successor")
    snapshot = capture_retirement_snapshot(store, target)
    store._subjects.pop(successor.internal_id.value)
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-04", "superseded", successor))
    return result.code == "RETIREMENT_STALE_SNAPSHOT" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_successor_revision_drift(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-05-target")
    successor = make_plan(store, "neg-r2-05-successor")
    snapshot = capture_retirement_snapshot(store, target)
    store._put_staged(set_revision_number(store.read_subject(successor), 2))
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-05", "superseded", successor))
    return result.code == "RETIREMENT_STALE_SNAPSHOT" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_successor_eligibility_drift(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-06-target")
    successor = make_plan(store, "neg-r2-06-successor")
    snapshot = capture_retirement_snapshot(store, target)
    changed = dict(store.read_subject(successor).mechanical_state)
    changed.update({"state": "cancelled", "retirement_kind": "abandoned"})
    store._put_staged(set_revision_number(replace(store.read_subject(successor), mechanical_state=changed), 2))
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-06", "superseded", successor))
    return result.code == "RETIREMENT_STALE_SNAPSHOT" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_no_automatic_reselection(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-07-target")
    successor = make_plan(store, "neg-r2-07-successor")
    snapshot = capture_retirement_snapshot(store, target)
    replacement = make_plan(store, "neg-r2-07-replacement")
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-07", "superseded", successor))
    return (
        result.code == "RETIREMENT_STALE_SNAPSHOT"
        and store.read_subject(target).mechanical_state["state"] == "initialized"
        and store.read_subject(replacement).mechanical_state["state"] == "initialized"
    )


def case_self_successor(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-08-target")
    snapshot = capture_retirement_snapshot(store, target)
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-08", "superseded", target, False))
    return result.code == "RETIREMENT_SELF_SUCCESSOR" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_stale_target_snapshot(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-09-target")
    snapshot = capture_retirement_snapshot(store, target)
    changed = dict(store.read_subject(target).mechanical_state)
    changed["changed_after_snapshot"] = True
    store._put_staged(replace(store.read_subject(target), mechanical_state=changed))
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-09", "abandoned"))
    return result.code == "RETIREMENT_STALE_SNAPSHOT" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_active_target(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-10-target", active=True, current=True)
    snapshot = capture_retirement_snapshot(store, target)
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-10", "abandoned", lease=False))
    return result.code == "RETIREMENT_TARGET_PROTECTED" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_running_target(context):
    store = make_test_store()
    target = make_plan(store, "neg-r2-11-target", running_task=True)
    snapshot = capture_retirement_snapshot(store, target)
    result = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-11", "abandoned", lease=False))
    return result.code == "RETIREMENT_RUNNING_TASK_PROTECTED" and store.read_subject(target).mechanical_state["state"] == "initialized"


def case_no_resurrection(context, evidence, binding):
    store = make_test_store()
    target = make_plan(store, "neg-r2-12-target")
    snapshot = capture_retirement_snapshot(store, target)
    retired = retire_plan(store, retirement_request(context, target, snapshot, "neg-r2-12-retire", "abandoned"))
    result = plan_init(store, init_request(context, target, "neg-r2-12-init", evidence, binding, 2))
    return retired.code == "RETIREMENT_APPLIED" and result.code == "PLAN_INIT_INVALID_PREDECESSOR" and store.read_subject(target).mechanical_state["state"] == "cancelled"


def case_plan_init_reentry(context, evidence, binding):
    store = make_test_store()
    target = make_plan(store, "neg-r2-13-target")
    result = plan_init(store, init_request(context, target, "neg-r2-13", evidence, binding))
    return result.code == "PLAN_INIT_ALREADY_INITIALIZED" and store.current_revision(target).revision_number == 1


def case_no_write_ingress():
    ensure_handlers_bound()
    lifecycle_names = {"plan_init", "plan_retirement"}
    registered = set(DEFAULT_REGISTRY.names())
    return (
        M4_4_CAPABILITY_LEASE_ISSUER_IMPLEMENTED is False
        and M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED is False
        and M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
        and M4_4_DURABLE_JOURNAL_IMPLEMENTED is False
        and not registered.intersection(lifecycle_names)
        and all(DEFAULT_REGISTRY.require(name).read_write == "read" for name in registered)
    )


def main() -> int:
    context = make_test_context()
    with TempWorkspaceFixture(prefix="m4-4-r2-independent-") as workspace:
        evidence, binding = binding_fixture(workspace)
        cases = (
            ("NEG-R2-01 missing successor does not leak SubjectNotFoundError", lambda: case_missing_successor(context)),
            ("NEG-R2-02 missing successor does not mutate target", lambda: case_missing_successor_no_mutation(context)),
            ("NEG-R2-03 unexpected successor error surfaces", lambda: case_unexpected_error_surfaces(context)),
            ("NEG-R2-04 removed successor fails closed", lambda: case_successor_removed(context)),
            ("NEG-R2-05 successor revision drift fails closed", lambda: case_successor_revision_drift(context)),
            ("NEG-R2-06 successor eligibility drift fails closed", lambda: case_successor_eligibility_drift(context)),
            ("NEG-R2-07 alternate successor is not selected", lambda: case_no_automatic_reselection(context)),
            ("NEG-R2-08 self successor is rejected", lambda: case_self_successor(context)),
            ("NEG-R2-09 stale target snapshot is rejected", lambda: case_stale_target_snapshot(context)),
            ("NEG-R2-10 active/current target is protected", lambda: case_active_target(context)),
            ("NEG-R2-11 running-task target is protected", lambda: case_running_target(context)),
            ("NEG-R2-12 retired Plan is not resurrected", lambda: case_no_resurrection(context, evidence, binding)),
            ("NEG-R2-13 PLAN_INIT reentry is denied", lambda: case_plan_init_reentry(context, evidence, binding)),
            ("NEG-R2-14 M4-4 does not enable write ingress", case_no_write_ingress),
        )
        outcomes = []
        for name, case in cases:
            try:
                passed = bool(case())
            except Exception as exc:
                passed = False
                print(f"FAIL {name}: unexpected {type(exc).__name__}: {exc}")
            outcomes.append(passed)
            if passed:
                print(f"PASS {name}")
            else:
                print(f"FAIL {name}")

    rejected = sum(outcomes)
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(outcomes)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={len(outcomes) - rejected}")
    return 0 if rejected == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
