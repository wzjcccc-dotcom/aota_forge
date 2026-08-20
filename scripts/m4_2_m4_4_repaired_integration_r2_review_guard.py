#!/usr/bin/env python3
"""Independent genuine-lease rereview for the M4-2/M4-4 seam.

This guard is review-only.  It builds trusted decision evidence locally, obtains
leases through the M4-2 issuer, and never constructs a CapabilityLease value.
The source root can be redirected to the original integration candidate so the
same scenario reproduces the R1 blocker without changing that worktree.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any


REVIEW_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REVIEW_ROOT
if "--source-root" in sys.argv:
    index = sys.argv.index("--source-root")
    if index + 1 >= len(sys.argv):
        raise SystemExit("--source-root requires a path")
    SOURCE_ROOT = Path(sys.argv[index + 1]).resolve()
sys.path.insert(0, str(SOURCE_ROOT))

from aota_forge.core.authority import (  # noqa: E402
    ApprovalEvidence,
    AuthorityDecision,
    AuthorityEngine,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
)
from aota_forge.core.authorization import CapabilityLeaseIssuer  # noqa: E402
from aota_forge.core.capability_lease import CapabilityLease  # noqa: E402
from aota_forge.core.context import ProjectBinding  # noqa: E402
from aota_forge.core.contracts.descriptor import (  # noqa: E402
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.mutation import (  # noqa: E402
    MutationEffect,
    MutationIntent,
    MutationPreconditions,
)
from aota_forge.core.graph import records  # noqa: E402
from aota_forge.core.graph.repository import OwningSubjectResolver  # noqa: E402
from aota_forge.core.identity.ids import make_id  # noqa: E402
from aota_forge.core.identity.kinds import IdKind, SubjectKind  # noqa: E402
from aota_forge.core.identity.refs import ObjectRef, make_object_ref  # noqa: E402
from aota_forge.core.project.resolver import resolve_project_candidates  # noqa: E402
from aota_forge.core.regression.fixtures import (  # noqa: E402
    TempWorkspaceFixture,
    fixture_time,
    make_test_context,
    make_test_store,
    seed_test_subject,
)
from aota_forge.core.revision import AuthorityDeniedError, set_revision_number  # noqa: E402
from aota_forge.core.transaction import SubjectTransaction  # noqa: E402
from aota_forge.core.transitions import (  # noqa: E402
    PlanInitRequest,
    PlanRetirementRequest,
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
)


NOW = fixture_time()
SCOPE = {"mode": "write"}
EXTERNAL_P1 = "source-revision-7"
SOURCE_R1 = "7"
OBSERVED_R1 = "a" * 64
CANDIDATE_R1 = "b" * 64
NORMALIZED_R1 = "c" * 64

RESULTS: list[tuple[str, bool, str]] = []
POSITIVE_RESULTS: list[bool] = []
NEGATIVE_RESULTS: list[bool] = []


@dataclass
class Fixture:
    context: object
    store: object
    plan_ref: ObjectRef
    project_evidence: object
    project_binding: ProjectBinding
    workspace: object
    registry: object


@dataclass
class Issued:
    intent: MutationIntent
    authorization: TrustedMutationAuthorization
    issuer: CapabilityLeaseIssuer
    lease: CapabilityLease


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))
    return passed


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=SOURCE_ROOT, capture_output=True, text=True, check=False)


def _make_plan(store: object, name: str, state: str = "uninitialized", **extra: object) -> ObjectRef:
    mechanical_state = {
        "state": state,
        "authority_source_revision": SOURCE_R1,
        "authority_observed_raw_digest": OBSERVED_R1,
    }
    mechanical_state.update(extra)
    subject_id = make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN)
    return seed_test_subject(store, subject_id, kind="plan", mechanical_state=mechanical_state)


def _make_intent(operation: str, target: ObjectRef, key: str, **semantic: object) -> MutationIntent:
    return MutationIntent(
        operation=operation,
        semantic_inputs=semantic,
        logical_target=target.serialize(),
        mutation_scope=SCOPE,
        idempotency_key=key,
    )


def _make_preconditions(
    revision: int = 1,
    *,
    source_revision: str | int = SOURCE_R1,
    observed_raw_digest: str = OBSERVED_R1,
    candidate_raw_digest: str = CANDIDATE_R1,
) -> MutationPreconditions:
    return MutationPreconditions(
        subject_expected_revision=revision,
        authority_source_revision=source_revision,
        authority_observed_raw_digest=observed_raw_digest,
        candidate_raw_digest=candidate_raw_digest,
    )


@contextmanager
def _fixture(name: str, state: str = "uninitialized", **state_fields: object):
    with TempWorkspaceFixture(prefix=f"m4-r2-review-{name}-") as workspace:
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
            semantic_decision_ref=f"review-decision:{name}",
        )
        store = make_test_store()
        plan_ref = _make_plan(store, f"plan-{name}", state, **state_fields)
        yield Fixture(
            context=make_test_context(principal_id="m4-r2-reviewer"),
            store=store,
            plan_ref=plan_ref,
            project_evidence=evidence,
            project_binding=binding,
            workspace=workspace,
            registry=registry,
        )


def _issue_real_lease(
    fixture: Fixture,
    *,
    descriptor=PLAN_INIT_DESCRIPTOR,
    intent: MutationIntent | None = None,
    target: ObjectRef | None = None,
) -> Issued:
    target = target or fixture.plan_ref
    intent = intent or _make_intent(
        descriptor.name,
        target,
        f"{descriptor.name}-{target.internal_id.value}",
        project_id="p1",
        requested_state="initialized",
    )
    decision_id = make_id(IdKind.DECISION, f"review-decision-{descriptor.name}-{target.internal_id.value}")
    decision = records.decision(
        decision_id,
        target.internal_id,
        "authorization",
        "authorize exact reviewed lifecycle operation",
        target_refs=[target.serialize()],
    )
    fixture.store._put_staged(decision)
    decision_evidence = MaterializedDecisionEvidence.from_decision(
        decision,
        operation=descriptor.name,
        target=target,
        expected_revision=1,
        scope=SCOPE,
    )
    approval = ApprovalEvidence(
        approver=fixture.context.principal,
        trusted_context=fixture.context,
        operation=descriptor.name,
        target=target,
        expected_revision=1,
        scope=SCOPE,
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(fixture.context.principal))
    authorization = TrustedMutationAuthorization(
        principal=fixture.context.principal,
        operation=descriptor.name,
        target=target,
        mutation_scope=SCOPE,
        contract_hash=descriptor.contract_hash(),
        intent_fingerprint=intent.intent_fingerprint(),
        subject_expected_revision=1,
        external_authority_precondition=EXTERNAL_P1,
        authority_source_revision=SOURCE_R1,
        authority_observed_raw_digest=OBSERVED_R1,
        candidate_raw_digest=CANDIDATE_R1,
        normalized_plan_digest=NORMALIZED_R1,
        authorization_basis="materialized_decision_evidence",
        approval_basis=approval,
        decision_basis=decision_evidence,
        authorization_id=f"review-auth-{descriptor.name}-{target.internal_id.value}",
        trusted_context=fixture.context,
    )
    issuer = CapabilityLeaseIssuer(AuthorityEngine(OwningSubjectResolver(fixture.store)))
    lease = issuer.issue(
        authorization,
        descriptor,
        intent=intent,
        trusted_context=fixture.context,
        now=NOW,
        lease_id=f"review-m42-{descriptor.name}-{target.internal_id.value}",
        attempt_id=f"review-attempt-{descriptor.name}-{target.internal_id.value}",
    )
    return Issued(intent, authorization, issuer, lease)


def _lease_bindings(issued: Issued) -> dict[str, object]:
    lease = issued.lease
    return {
        "contract_hash": lease.contract_hash,
        "intent_fingerprint": lease.intent_fingerprint,
        "external_authority_precondition": lease.external_authority_precondition,
        "authority_source_revision": lease.authority_source_revision,
        "authority_observed_raw_digest": lease.authority_observed_raw_digest,
        "candidate_raw_digest": lease.candidate_raw_digest,
        "normalized_plan_digest": lease.normalized_plan_digest,
    }


def _assert_real_lease(issued: Issued) -> bool:
    lease = issued.lease
    authorization = issued.authorization
    result = (
        isinstance(lease, CapabilityLease)
        and lease.intent_fingerprint == authorization.intent_fingerprint
        and lease.contract_hash == authorization.contract_hash
        and lease.external_authority_precondition == authorization.external_authority_precondition
        and lease.authority_source_revision == authorization.authority_source_revision
        and lease.authority_observed_raw_digest == authorization.authority_observed_raw_digest
        and lease.candidate_raw_digest == authorization.candidate_raw_digest
        and lease.normalized_plan_digest == authorization.normalized_plan_digest
    )
    check("REAL_LEASE_CREATED_VIA_M4_2_ISSUER", result)
    check("REAL_LEASE_HAS_INTENT_BINDING", lease.intent_fingerprint is not None)
    check("REAL_LEASE_HAS_CONTRACT_BINDING", lease.contract_hash is not None)
    check("REAL_LEASE_HAS_AUTHORITY_PRECONDITION_BINDING", lease.external_authority_precondition is not None)
    return result


@contextmanager
def _authority_spy(fixture: Fixture):
    observed: list[tuple[object, object]] = []
    original = fixture.store._authority.evaluate

    def evaluate(request):
        result = original(request)
        observed.append((request, result))
        return result

    fixture.store._authority.evaluate = evaluate
    try:
        yield observed
    finally:
        fixture.store._authority.evaluate = original


def _reason(observed: list[tuple[object, object]]) -> str | None:
    if not observed:
        return None
    return observed[-1][1].reason_code.value


def _request_kwargs(request_type: type, values: dict[str, object]) -> dict[str, object]:
    accepted = inspect.signature(request_type).parameters
    return {key: value for key, value in values.items() if key in accepted}


def _plan_init_request(
    fixture: Fixture,
    *,
    intent: MutationIntent,
    lease: CapabilityLease,
    evidence: object | None = None,
    binding: ProjectBinding | None = None,
    preconditions: MutationPreconditions | None = None,
    external_authority_precondition: str | None = EXTERNAL_P1,
    normalized_plan_digest: str | None = NORMALIZED_R1,
) -> PlanInitRequest:
    values = {
        "trusted_context": fixture.context,
        "plan_ref": fixture.plan_ref,
        "intent": intent,
        "preconditions": preconditions or _make_preconditions(),
        "trusted_time": NOW,
        "project_evidence": fixture.project_evidence if evidence is None else evidence,
        "project_binding": fixture.project_binding if binding is None else binding,
        "lease": lease,
        "external_authority_precondition": external_authority_precondition,
        "normalized_plan_digest": normalized_plan_digest,
    }
    return PlanInitRequest(**_request_kwargs(PlanInitRequest, values))


def _retirement_request(
    fixture: Fixture,
    snapshot: object,
    *,
    intent: MutationIntent,
    lease: CapabilityLease,
    successor: ObjectRef | None = None,
    retirement_kind: str | None = None,
    preconditions: MutationPreconditions | None = None,
    external_authority_precondition: str | None = EXTERNAL_P1,
    normalized_plan_digest: str | None = NORMALIZED_R1,
) -> PlanRetirementRequest:
    values = {
        "trusted_context": fixture.context,
        "plan_ref": fixture.plan_ref,
        "snapshot": snapshot,
        "intent": intent,
        "preconditions": preconditions or _make_preconditions(),
        "trusted_time": NOW,
        "retirement_kind": retirement_kind or ("superseded" if successor is not None else "abandoned"),
        "successor_ref": successor,
        "lease": lease,
        "external_authority_precondition": external_authority_precondition,
        "normalized_plan_digest": normalized_plan_digest,
    }
    return PlanRetirementRequest(**_request_kwargs(PlanRetirementRequest, values))


def _direct_denial(
    fixture: Fixture,
    issued: Issued,
    *,
    target: ObjectRef | None = None,
    operation: str = "plan_init",
    requested_scope: dict[str, str] | None = None,
    bindings: dict[str, object] | None = None,
    label: str,
) -> tuple[str | None, object | None, bool]:
    target = target or fixture.plan_ref
    actual = _lease_bindings(issued)
    actual.update(bindings or {})
    values = {
        "store": fixture.store,
        "subject_ref": target,
        "expected_revision": 1,
        "operation": operation,
        "trusted_context": fixture.context,
        "requested_scope": requested_scope or SCOPE,
        "lease": issued.lease,
        **actual,
        "idempotency_key": f"direct-{label}-{issued.lease.lease_id}",
        "fingerprint": issued.intent.intent_fingerprint(),
        "trusted_time": NOW,
        "new_state": {"review_probe": label},
    }
    tx_values = _request_kwargs(SubjectTransaction, values)
    before = fixture.store.read_subject(target)
    with _authority_spy(fixture) as observed:
        try:
            SubjectTransaction(**tx_values).begin()
        except AuthorityDeniedError as exc:
            unchanged = fixture.store.read_subject(target) == before
            details = exc.details if isinstance(exc.details, dict) else {}
            request = observed[-1][0] if observed else None
            return details.get("reason_code"), request, unchanged
        except Exception as exc:  # pragma: no cover - failure envelope
            return type(exc).__name__, observed[-1][0] if observed else None, fixture.store.read_subject(target) == before
    return "ACCEPTED", observed[-1][0] if observed else None, fixture.store.read_subject(target) == before


def _positive_plan_init() -> bool:
    with _fixture("positive-plan-init") as fixture:
        issued = _issue_real_lease(fixture)
        lease_ok = _assert_real_lease(issued)
        with _authority_spy(fixture) as observed:
            result = plan_init(
                fixture.store,
                _plan_init_request(fixture, intent=issued.intent, lease=issued.lease),
            )
        request = observed[-1][0] if observed else None
        expected = {
            "intent_fingerprint": issued.intent.intent_fingerprint(),
            "contract_hash": PLAN_INIT_DESCRIPTOR.contract_hash(),
            "external_authority_precondition": EXTERNAL_P1,
            "authority_source_revision": SOURCE_R1,
            "authority_observed_raw_digest": OBSERVED_R1,
            "candidate_raw_digest": CANDIDATE_R1,
            "normalized_plan_digest": NORMALIZED_R1,
        }
        forwarded = request is not None and all(getattr(request, key, object()) == value for key, value in expected.items())
        passed = (
            lease_ok
            and result.code == "PLAN_INIT_APPLIED"
            and result.mutation_effect is MutationEffect.APPLIED_VERIFIED
            and forwarded
            and observed[-1][1].decision is AuthorityDecision.ALLOW
            and fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "initialized"
            and fixture.store.current_revision(fixture.plan_ref).revision_number == 2
            and fixture.store.is_lease_consumed(issued.lease.lease_id)
        )
        return check("REAL_M4_2_LEASE_PLAN_INIT_REREVIEW", passed, result.code)


def _positive_retirement() -> bool:
    with _fixture("positive-retirement", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "positive-retirement", retirement_kind="abandoned")
        issued = _issue_real_lease(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        lease_ok = _assert_real_lease(issued)
        with _authority_spy(fixture) as observed:
            result = retire_plan(
                fixture.store,
                _retirement_request(fixture, snapshot, intent=intent, lease=issued.lease),
            )
        request = observed[-1][0] if observed else None
        expected = {
            "intent_fingerprint": intent.intent_fingerprint(),
            "contract_hash": PLAN_RETIREMENT_DESCRIPTOR.contract_hash(),
            "external_authority_precondition": EXTERNAL_P1,
            "authority_source_revision": SOURCE_R1,
            "authority_observed_raw_digest": OBSERVED_R1,
            "candidate_raw_digest": CANDIDATE_R1,
            "normalized_plan_digest": NORMALIZED_R1,
        }
        forwarded = request is not None and all(getattr(request, key, object()) == value for key, value in expected.items())
        passed = (
            lease_ok
            and result.code == "RETIREMENT_APPLIED"
            and result.mutation_effect is MutationEffect.APPLIED_VERIFIED
            and forwarded
            and observed[-1][1].decision is AuthorityDecision.ALLOW
            and fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "cancelled"
            and fixture.store.is_lease_consumed(issued.lease.lease_id)
        )
        return check("REAL_M4_2_LEASE_RETIREMENT_REREVIEW", passed, result.code)


def _binding_matrix() -> None:
    cases = (
        ("LEASE_INTENT_MISMATCH_NEGATIVE_REREVIEW", "intent_fingerprint", "d" * 64, "LEASE_INTENT_MISMATCH"),
        ("LEASE_CONTRACT_MISMATCH_NEGATIVE_REREVIEW", "contract_hash", PLAN_RETIREMENT_DESCRIPTOR.contract_hash(), "LEASE_OPERATION_MISMATCH"),
        ("LEASE_AUTHORITY_PRECONDITION_MISMATCH_REREVIEW", "external_authority_precondition", "source-revision-8", "AUTHORITY_PRECONDITION_STALE"),
        ("AUTHORITY_SOURCE_REVISION_MISMATCH_REREVIEW", "authority_source_revision", "8", "AUTHORITY_PRECONDITION_STALE"),
        ("RAW_AUTHORITY_DIGEST_BINDING_REREVIEW", "authority_observed_raw_digest", "d" * 64, "AUTHORITY_PRECONDITION_STALE"),
        ("CANDIDATE_RAW_DIGEST_BINDING_REREVIEW", "candidate_raw_digest", "e" * 64, "AUTHORITY_PRECONDITION_STALE"),
        ("NORMALIZED_PLAN_DIGEST_BINDING_REREVIEW", "normalized_plan_digest", "f" * 64, "AUTHORITY_PRECONDITION_STALE"),
    )
    for name, field, changed, expected in cases:
        with _fixture(f"binding-{field}") as fixture:
            issued = _issue_real_lease(fixture)
            reason, request, unchanged = _direct_denial(
                fixture,
                issued,
                bindings={field: changed},
                label=field,
            )
            forwarded = request is not None and getattr(request, field, object()) == changed
            passed = reason == expected and forwarded and unchanged
            NEGATIVE_RESULTS.append(check(name, passed, f"reason={reason},forwarded={forwarded}"))

    with _fixture("binding-target") as fixture:
        target_b = _make_plan(fixture.store, "binding-target-b")
        issued = _issue_real_lease(fixture)
        reason, request, unchanged = _direct_denial(fixture, issued, target=target_b, label="target")
        passed = reason == "LEASE_TARGET_MISMATCH" and request is not None and request.target == target_b and unchanged
        NEGATIVE_RESULTS.append(check("LEASE_TARGET_CROSS_LIFECYCLE_BINDING_REREVIEW", passed, str(reason)))

    with _fixture("binding-operation") as fixture:
        issued = _issue_real_lease(fixture)
        reason, request, unchanged = _direct_denial(
            fixture,
            issued,
            operation="plan_retirement",
            bindings={"contract_hash": PLAN_RETIREMENT_DESCRIPTOR.contract_hash()},
            label="operation",
        )
        passed = reason == "LEASE_OPERATION_MISMATCH" and request is not None and request.operation == "plan_retirement" and unchanged
        NEGATIVE_RESULTS.append(check("LEASE_OPERATION_CROSS_LIFECYCLE_BINDING_REREVIEW", passed, str(reason)))

    with _fixture("binding-scope") as fixture:
        issued = _issue_real_lease(fixture)
        reason, request, unchanged = _direct_denial(
            fixture,
            issued,
            requested_scope={"mode": "different"},
            label="scope",
        )
        passed = reason == "LEASE_SCOPE_MISMATCH" and request is not None and dict(request.requested_scope) == {"mode": "different"} and unchanged
        NEGATIVE_RESULTS.append(check("LEASE_SCOPE_CROSS_LIFECYCLE_BINDING_REREVIEW", passed, str(reason)))


def _lifecycle_matrix() -> None:
    with _fixture("reentry", state="initialized") as fixture:
        issued = _issue_real_lease(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=issued.intent, lease=issued.lease))
        passed = result.code == "PLAN_INIT_ALREADY_INITIALIZED" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("PLAN_INIT_REENTRY_REAL_LEASE_REGRESSION", passed, result.code))

    with _fixture("ambiguity") as fixture:
        issued = _issue_real_lease(fixture)
        duplicate_parent = fixture.workspace.workdir / "ambiguous"
        duplicate_parent.mkdir()
        fixture.workspace.create_project("p1", parent_dir=duplicate_parent)
        evidence = resolve_project_candidates("w1", fixture.registry, "p1")
        result = plan_init(
            fixture.store,
            _plan_init_request(fixture, intent=issued.intent, lease=issued.lease, evidence=evidence, binding=None),
        )
        passed = result.code == "NEEDS_SEMANTIC_CHOICE" and result.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE
        NEGATIVE_RESULTS.append(check("PROJECT_BINDING_REAL_LEASE_REGRESSION", passed, result.code))

    for name, fields, expected in (
        ("active-current", {"active": True}, "RETIREMENT_TARGET_PROTECTED"),
        ("running-task", {"running_task": True}, "RETIREMENT_RUNNING_TASK_PROTECTED"),
    ):
        with _fixture(name, state="initialized", **fields) as fixture:
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, name, retirement_kind="abandoned")
            issued = _issue_real_lease(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = retire_plan(
                fixture.store,
                _retirement_request(fixture, snapshot, intent=intent, lease=issued.lease),
            )
            NEGATIVE_RESULTS.append(check(f"{name.upper().replace('-', '_')}_PROTECTION_REAL_LEASE_REGRESSION", result.code == expected, result.code))

    with _fixture("missing-successor", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "missing-successor", retirement_kind="superseded")
        issued = _issue_real_lease(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(
            fixture.store,
            _retirement_request(
                fixture,
                snapshot,
                intent=intent,
                lease=issued.lease,
                retirement_kind="superseded",
                successor=None,
            ),
        )
        NEGATIVE_RESULTS.append(check("RETIREMENT_MISSING_SUCCESSOR_REAL_LEASE_REGRESSION", result.code == "RETIREMENT_SUCCESSOR_REQUIRED", result.code))

    with _fixture("successor-drift", state="initialized") as fixture:
        successor = _make_plan(fixture.store, "successor-drift-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        successor_subject = fixture.store.read_subject(successor)
        fixture.store._put_staged(set_revision_number(successor_subject, 2))
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "successor-drift",
            retirement_kind="superseded",
            successor_ref=successor.serialize(),
        )
        issued = _issue_real_lease(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(
            fixture.store,
            _retirement_request(fixture, snapshot, intent=intent, lease=issued.lease, successor=successor),
        )
        NEGATIVE_RESULTS.append(check("RETIREMENT_SUCCESSOR_DRIFT_REAL_LEASE_REGRESSION", result.code == "RETIREMENT_STALE_SNAPSHOT", result.code))

    with _fixture("no-resurrection", state="cancelled") as fixture:
        issued = _issue_real_lease(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(fixture.store, _plan_init_request(fixture, intent=issued.intent, lease=issued.lease))
        passed = result.code == "PLAN_INIT_INVALID_PREDECESSOR" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("NO_RESURRECTION_REAL_LEASE_REGRESSION", passed, result.code))

    with _fixture("unknown-outcome") as fixture:
        issued = _issue_real_lease(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(
            fixture.store,
            _plan_init_request(fixture, intent=issued.intent, lease=issued.lease.mark_outcome_unknown()),
        )
        passed = result.code == "PLAN_INIT_AUTHORIZATION_DENIED" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("UNKNOWN_OUTCOME_LIFECYCLE_REPLAY_DENIED", passed, result.code))


def _old_blocker_probe() -> bool:
    with _fixture("old-plan-init") as fixture:
        issued = _issue_real_lease(fixture)
        with _authority_spy(fixture) as observed:
            result = plan_init(fixture.store, _plan_init_request(fixture, intent=issued.intent, lease=issued.lease))
        request = observed[-1][0] if observed else None
        missing = request is not None and request.intent_fingerprint is None and request.contract_hash is None
        plan_passed = result.code == "PLAN_INIT_AUTHORIZATION_DENIED" and _reason(observed) == "LEASE_INTENT_MISMATCH" and missing

    with _fixture("old-retirement", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "old-retirement", retirement_kind="abandoned")
        issued = _issue_real_lease(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        with _authority_spy(fixture) as observed:
            result = retire_plan(
                fixture.store,
                _retirement_request(fixture, snapshot, intent=intent, lease=issued.lease),
            )
        request = observed[-1][0] if observed else None
        missing = request is not None and request.intent_fingerprint is None and request.contract_hash is None
        retirement_passed = result.code == "RETIREMENT_AUTHORIZATION_DENIED" and _reason(observed) == "LEASE_INTENT_MISMATCH" and missing

    return check(
        "ORIGINAL_R1_BLOCKER_REPRODUCED_ON_OLD_SHA",
        plan_passed and retirement_passed,
        f"plan={plan_passed},retirement={retirement_passed}",
    )


def _repair_static_probe() -> bool:
    source = inspect.getsource(SubjectTransaction)
    required = all(f"self._{field}" in source for field in (
        "contract_hash",
        "intent_fingerprint",
        "external_authority_precondition",
        "authority_source_revision",
        "authority_observed_raw_digest",
        "candidate_raw_digest",
        "normalized_plan_digest",
    ))
    copied = any(f"self._lease.{field}" in source for field in (
        "contract_hash",
        "intent_fingerprint",
        "external_authority_precondition",
        "authority_source_revision",
        "authority_observed_raw_digest",
        "candidate_raw_digest",
        "normalized_plan_digest",
    ))
    return check(
        "AUTHORITY_REQUEST_DATAFLOW_STATIC_PROBE",
        required and not copied,
        f"required={required},lease_field_copy={copied}",
    )


def _repaired_positive_probe() -> bool:
    with _fixture("repaired-plan-init") as fixture:
        issued = _issue_real_lease(fixture)
        with _authority_spy(fixture) as observed:
            result = plan_init(fixture.store, _plan_init_request(fixture, intent=issued.intent, lease=issued.lease))
        request = observed[-1][0] if observed else None
        actual = {
            "contract_hash": PLAN_INIT_DESCRIPTOR.contract_hash(),
            "intent_fingerprint": issued.intent.intent_fingerprint(),
            "external_authority_precondition": EXTERNAL_P1,
            "authority_source_revision": SOURCE_R1,
            "authority_observed_raw_digest": OBSERVED_R1,
            "candidate_raw_digest": CANDIDATE_R1,
            "normalized_plan_digest": NORMALIZED_R1,
        }
        forwarded = request is not None and all(getattr(request, key, object()) == value for key, value in actual.items())
        return check(
            "ORIGINAL_R1_BLOCKER_CLOSED_ON_REPAIRED_SHA",
            result.code == "PLAN_INIT_APPLIED" and forwarded,
            result.code,
        )


def _run_current() -> int:
    head = _git(["git", "rev-parse", "HEAD"]).stdout.strip()
    check("REVIEW_SOURCE_HEAD_RESOLVED", bool(head), head)
    _repair_static_probe()
    _repaired_positive_probe()
    _binding_matrix()
    _lifecycle_matrix()
    positives = sum(POSITIVE_RESULTS)
    negatives = sum(NEGATIVE_RESULTS)
    print(f"REAL_LEASE_POSITIVE_CASE_COUNT={len(POSITIVE_RESULTS) or 2}")
    print(f"REAL_LEASE_POSITIVE_CASE_PASS_COUNT={positives or 0}")
    print(f"REAL_LEASE_NEGATIVE_CASE_COUNT={len(NEGATIVE_RESULTS)}")
    print(f"REAL_LEASE_NEGATIVE_CASE_PASS_COUNT={negatives}")
    failures = [name for name, passed, _ in RESULTS if not passed]
    print(f"CURRENT_SEMANTIC_INVARIANT_FAILURE_COUNT={len(failures)}")
    print(f"REPAIRED_INDEPENDENT_REVIEW_GUARD={'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


def main() -> int:
    old_mode = SOURCE_ROOT != REVIEW_ROOT
    if old_mode:
        return 0 if _old_blocker_probe() and not [name for name, passed, _ in RESULTS if not passed] else 1
    positive_plan = _positive_plan_init()
    positive_retirement = _positive_retirement()
    POSITIVE_RESULTS.extend((positive_plan, positive_retirement))
    return _run_current()


if __name__ == "__main__":
    raise SystemExit(main())
