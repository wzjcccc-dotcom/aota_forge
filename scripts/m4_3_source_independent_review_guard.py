#!/usr/bin/env python3
"""Independent M4-3 source review probes.

This guard constructs fresh requests and genuine M4-2 leases.  It is review
evidence tooling only and uses the non-authoritative fixture store.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aota_forge.core.authority import (
    ApprovalEvidence,
    AuthorityEngine,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
)
from aota_forge.core.authorization import CapabilityLeaseIssuer
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import ProjectBinding
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.mutation import MutationIntent, MutationPreconditions
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import OwningSubjectResolver
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef
from aota_forge.core.ingress import MutationIngressRequest, execute_mutation
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
    PlanInitRequest,
    PlanRetirementRequest,
    capture_retirement_snapshot,
)


NOW = fixture_time()
WRITE_SCOPE = {"mode": "write"}
OBSERVED_DIGEST = "a" * 64
CANDIDATE_DIGEST = "b" * 64
NORMALIZED_DIGEST = "c" * 64
_USE_ISSUED = object()


@dataclass
class ReviewFixture:
    context: object
    store: object
    plan_ref: ObjectRef
    evidence: object
    binding: ProjectBinding
    workspace: object
    registry: object


@dataclass
class Issued:
    intent: MutationIntent
    authorization: TrustedMutationAuthorization
    issuer: CapabilityLeaseIssuer
    lease: CapabilityLease


@contextmanager
def isolated(name: str, *, state: str = "uninitialized"):
    with TempWorkspaceFixture(prefix=f"m43-independent-{name}-") as workspace:
        workspace.create_project("review-project")
        registry = workspace.create_registry("review-workspace")
        evidence = resolve_project_candidates("review-workspace", registry, "review-project")
        candidate = evidence.candidates[0]
        binding = ProjectBinding(
            workspace_id=candidate.workspace_id,
            workspace_root=candidate.workspace_root,
            project_id=candidate.project_id,
            project_root=candidate.project_root,
            manifest_path=candidate.manifest_path,
            registry_fingerprint=candidate.registry_fingerprint,
            candidate_fingerprint=candidate.candidate_fingerprint,
            semantic_decision_ref=f"review-decision-{name}",
        )
        store = make_test_store()
        plan_id = make_id(IdKind.SUBJECT, f"review-plan-{name}", sub_kind=SubjectKind.PLAN)
        plan_ref = seed_test_subject(
            store,
            plan_id,
            kind="plan",
            mechanical_state={
                "state": state,
                "authority_source_revision": "7",
                "authority_observed_raw_digest": OBSERVED_DIGEST,
            },
        )
        yield ReviewFixture(
            context=make_test_context(
                principal_id="independent-reviewer",
                principal_type="review_operator",
                provenance="independent_review",
                channel="isolated_review_fixture",
                freshness="review-epoch-1",
            ),
            store=store,
            plan_ref=plan_ref,
            evidence=evidence,
            binding=binding,
            workspace=workspace,
            registry=registry,
        )


def make_plan(fixture: ReviewFixture, name: str, *, state: str = "uninitialized") -> ObjectRef:
    plan_id = make_id(IdKind.SUBJECT, f"review-{name}", sub_kind=SubjectKind.PLAN)
    return seed_test_subject(
        fixture.store,
        plan_id,
        kind="plan",
        mechanical_state={
            "state": state,
            "authority_source_revision": "7",
            "authority_observed_raw_digest": OBSERVED_DIGEST,
        },
    )


def make_intent(
    operation: str,
    target: ObjectRef,
    key: str,
    **semantic: Any,
) -> MutationIntent:
    return MutationIntent(
        operation=operation,
        semantic_inputs=semantic,
        logical_target=target.serialize(),
        mutation_scope=dict(WRITE_SCOPE),
        idempotency_key=key,
    )


def make_preconditions(
    *,
    revision: int = 1,
    source_revision: str | int = "7",
    observed: str = OBSERVED_DIGEST,
    candidate: str = CANDIDATE_DIGEST,
) -> MutationPreconditions:
    return MutationPreconditions(
        subject_expected_revision=revision,
        authority_source_revision=source_revision,
        authority_observed_raw_digest=observed,
        candidate_raw_digest=candidate,
    )


def issue_genuine(
    fixture: ReviewFixture,
    *,
    descriptor,
    intent: MutationIntent,
    target: ObjectRef | None = None,
    revision: int = 1,
    external: str | None = "source-revision-7",
    source_revision: str | int = "7",
    observed: str = OBSERVED_DIGEST,
    candidate: str = CANDIDATE_DIGEST,
    normalized: str | None = NORMALIZED_DIGEST,
    lease_id: str | None = None,
) -> Issued:
    target = target or fixture.plan_ref
    decision_id = make_id(
        IdKind.DECISION,
        f"review-auth-{descriptor.name}-{intent.idempotency_key}",
    )
    decision = records.decision(
        decision_id,
        target.internal_id,
        "authorization",
        "independent exact lifecycle authorization",
        target_refs=[target.serialize()],
    )
    fixture.store._put_staged(decision)
    decision_evidence = MaterializedDecisionEvidence.from_decision(
        decision,
        operation=descriptor.name,
        target=target,
        expected_revision=revision,
        scope=WRITE_SCOPE,
    )
    approval = ApprovalEvidence(
        approver=fixture.context.principal,
        trusted_context=fixture.context,
        operation=descriptor.name,
        target=target,
        expected_revision=revision,
        scope=WRITE_SCOPE,
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(fixture.context.principal))
    authorization = TrustedMutationAuthorization(
        principal=fixture.context.principal,
        operation=descriptor.name,
        target=target,
        mutation_scope=WRITE_SCOPE,
        contract_hash=descriptor.contract_hash(),
        intent_fingerprint=intent.intent_fingerprint(),
        subject_expected_revision=revision,
        external_authority_precondition=external,
        authority_source_revision=source_revision,
        authority_observed_raw_digest=observed,
        candidate_raw_digest=candidate,
        normalized_plan_digest=normalized,
        authorization_basis="materialized_decision_evidence",
        approval_basis=approval,
        decision_basis=decision_evidence,
        authorization_id=f"review-auth-{intent.idempotency_key}",
        trusted_context=fixture.context,
    )
    issuer = CapabilityLeaseIssuer(AuthorityEngine(OwningSubjectResolver(fixture.store)))
    lease = issuer.issue(
        authorization,
        descriptor,
        intent=intent,
        trusted_context=fixture.context,
        now=NOW,
        lease_id=lease_id or f"review-lease-{intent.idempotency_key}",
        attempt_id=f"review-attempt-{intent.idempotency_key}",
    )
    return Issued(intent, authorization, issuer, lease)


def plan_init_request(
    fixture: ReviewFixture,
    issued: Issued,
    *,
    plan_ref: ObjectRef | None = None,
    intent: MutationIntent | None = None,
    lease: CapabilityLease | None | object = _USE_ISSUED,
    evidence: object | None = None,
    binding: ProjectBinding | None = None,
    preconditions: MutationPreconditions | None = None,
    external: str | None | object = _USE_ISSUED,
    normalized: str | None | object = _USE_ISSUED,
) -> PlanInitRequest:
    return PlanInitRequest(
        trusted_context=fixture.context,
        plan_ref=plan_ref or fixture.plan_ref,
        intent=intent or issued.intent,
        preconditions=preconditions or make_preconditions(),
        trusted_time=NOW,
        project_evidence=fixture.evidence if evidence is None else evidence,
        project_binding=fixture.binding if binding is None else binding,
        lease=issued.lease if lease is _USE_ISSUED else lease,
        external_authority_precondition=(
            issued.authorization.external_authority_precondition if external is _USE_ISSUED else external
        ),
        normalized_plan_digest=(
            issued.authorization.normalized_plan_digest if normalized is _USE_ISSUED else normalized
        ),
    )


def retirement_request(
    fixture: ReviewFixture,
    issued: Issued,
    snapshot,
    *,
    intent: MutationIntent | None = None,
    lease: CapabilityLease | None | object = _USE_ISSUED,
    successor: ObjectRef | None = None,
    retirement_kind: str = "abandoned",
    preconditions: MutationPreconditions | None = None,
    external: str | None | object = _USE_ISSUED,
    normalized: str | None | object = _USE_ISSUED,
) -> PlanRetirementRequest:
    return PlanRetirementRequest(
        trusted_context=fixture.context,
        plan_ref=fixture.plan_ref,
        snapshot=snapshot,
        intent=intent or issued.intent,
        preconditions=preconditions or make_preconditions(
            revision=snapshot.source_revision,
            source_revision=snapshot.authority_source_revision,
            observed=snapshot.authority_observed_raw_digest or OBSERVED_DIGEST,
        ),
        trusted_time=NOW,
        retirement_kind=retirement_kind,
        successor_ref=successor,
        lease=issued.lease if lease is _USE_ISSUED else lease,
        external_authority_precondition=(
            issued.authorization.external_authority_precondition if external is _USE_ISSUED else external
        ),
        normalized_plan_digest=(
            issued.authorization.normalized_plan_digest if normalized is _USE_ISSUED else normalized
        ),
    )


def run(fixture: ReviewFixture, operation: str, request: object) -> dict[str, Any]:
    return execute_mutation(MutationIngressRequest(operation, fixture.store, request))


def code_of(result: dict[str, Any]) -> str:
    return str(result.get("lifecycle_code") or result.get("error", {}).get("code") or "<missing-code>")


def effect_of(result: dict[str, Any]) -> str:
    return str(result.get("mutation_effect") or "<missing-effect>")


def no_verified_effect(result: dict[str, Any]) -> bool:
    return effect_of(result) not in {"APPLIED_VERIFIED", "REPLAYED_VERIFIED"}


def run_positive_cases(summary: dict[str, Any]) -> None:
    cases: list[tuple[str, bool, str]] = []

    with isolated("positive-init") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "positive-init-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(fixture, "plan_init", plan_init_request(fixture, issued))
        state = fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"]
        cases.append(
            (
                "POS-01 genuine lease PLAN_INIT",
                result.get("ok") is True
                and code_of(result) == "PLAN_INIT_APPLIED"
                and effect_of(result) == "APPLIED_VERIFIED"
                and state == "initialized"
                and fixture.store.current_revision(fixture.plan_ref).revision_number == 2,
                code_of(result),
            )
        )

    with isolated("positive-abandoned", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "positive-abandoned-key",
            retirement_kind="abandoned",
        )
        issued = issue_genuine(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_retirement",
            retirement_request(fixture, issued, snapshot),
        )
        state = fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"]
        cases.append(
            (
                "POS-02 genuine lease abandoned retirement",
                result.get("ok") is True
                and code_of(result) == "RETIREMENT_APPLIED"
                and effect_of(result) == "APPLIED_VERIFIED"
                and state == "cancelled"
                and fixture.store.current_revision(fixture.plan_ref).revision_number == 2,
                code_of(result),
            )
        )

    with isolated("positive-successor", state="initialized") as fixture:
        successor = make_plan(fixture, "positive-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "positive-successor-key",
            retirement_kind="superseded",
            successor_ref=successor.serialize(),
        )
        issued = issue_genuine(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_retirement",
            retirement_request(
                fixture,
                issued,
                snapshot,
                successor=successor,
                retirement_kind="superseded",
            ),
        )
        subject = fixture.store.read_subject(fixture.plan_ref)
        cases.append(
            (
                "POS-03 genuine lease exact successor retirement",
                result.get("ok") is True
                and code_of(result) == "RETIREMENT_APPLIED"
                and effect_of(result) == "APPLIED_VERIFIED"
                and subject.mechanical_state["state"] == "superseded"
                and subject.mechanical_state["successor_ref"] == successor.serialize(),
                code_of(result),
            )
        )

    summary["positive_cases"] = [
        {"name": name, "pass": passed, "code": code} for name, passed, code in cases
    ]


def run_binding_matrix(summary: dict[str, Any]) -> None:
    cases: list[dict[str, Any]] = []

    for name in (
        "B1 intent_fingerprint",
        "B2 contract_hash",
        "B3 external_authority_precondition",
        "B4 authority_source_revision",
        "B5 authority_observed_raw_digest",
        "B6 candidate_raw_digest",
        "B7 normalized_plan_digest",
        "B8 typed target",
        "B9 operation",
        "B10 scope",
    ):
        slug = name.split(" ", 1)[0].lower()
        with isolated(f"binding-{slug}") as fixture:
            intent = make_intent("plan_init", fixture.plan_ref, f"binding-{slug}-key", project_id="review-project")
            issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
            request = plan_init_request(fixture, issued)
            expected = "rejected"
            if slug == "b1":
                request = replace(
                    request,
                    intent=make_intent("plan_init", fixture.plan_ref, "binding-b1-changed", project_id="other"),
                )
            elif slug == "b2":
                request = replace(request, lease=replace(issued.lease, contract_hash="f" * 64))
            elif slug == "b3":
                request = replace(request, external_authority_precondition="request-authority-8")
            elif slug == "b4":
                request = replace(request, preconditions=replace(request.preconditions, authority_source_revision="8"))
            elif slug == "b5":
                request = replace(request, preconditions=replace(request.preconditions, authority_observed_raw_digest="d" * 64))
            elif slug == "b6":
                request = replace(request, preconditions=replace(request.preconditions, candidate_raw_digest="e" * 64))
            elif slug == "b7":
                request = replace(request, normalized_plan_digest="d" * 64)
            elif slug == "b8":
                other = make_plan(fixture, "binding-other-target")
                request = replace(
                    request,
                    plan_ref=other,
                    intent=make_intent("plan_init", other, "binding-b8-target", project_id="review-project"),
                )
            elif slug == "b9":
                snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
                retirement_intent = make_intent(
                    "plan_retirement",
                    fixture.plan_ref,
                    "binding-b9-operation",
                    retirement_kind="abandoned",
                )
                request = retirement_request(
                    fixture,
                    issued,
                    snapshot,
                    intent=retirement_intent,
                )
            elif slug == "b10":
                request = replace(
                    request,
                    intent=replace(issued.intent, mutation_scope={"mode": "different"}),
                )
            result = run(fixture, request.intent.operation, request)
            passed = no_verified_effect(result)
            cases.append(
                {
                    "name": name,
                    "pass": passed,
                    "code": code_of(result),
                    "effect": effect_of(result),
                    "expected": expected,
                }
            )

    summary["binding_matrix"] = cases


def run_request_value_probe(summary: dict[str, Any]) -> None:
    captured: dict[str, Any] = {}
    original_validate = CapabilityLeaseIssuer.validate_lease

    with isolated("request-wins") as fixture:
        intent_a = make_intent("plan_init", fixture.plan_ref, "request-wins-a", project_id="lease-A")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent_a)
        other = make_plan(fixture, "request-wins-target")
        intent_b = make_intent("plan_init", other, "request-wins-b", project_id="request-B")
        request = plan_init_request(
            fixture,
            issued,
            plan_ref=other,
            intent=intent_b,
            lease=replace(issued.lease, contract_hash="f" * 64),
            preconditions=make_preconditions(
                source_revision="request-source",
                observed="d" * 64,
                candidate="e" * 64,
            ),
            external="request-authority-B",
            normalized="f" * 64,
        )

        def capture(self, lease, **kwargs):
            captured.update(kwargs)
            return original_validate(self, lease, **kwargs)

        CapabilityLeaseIssuer.validate_lease = capture
        try:
            result = run(fixture, "plan_init", request)
        finally:
            CapabilityLeaseIssuer.validate_lease = original_validate

        observed = {
            "intent_fingerprint": captured.get("intent_fingerprint"),
            "contract_hash": captured.get("contract_hash"),
            "external_authority_precondition": captured.get("external_authority_precondition"),
            "authority_source_revision": captured.get("authority_source_revision"),
            "authority_observed_raw_digest": captured.get("authority_observed_raw_digest"),
            "candidate_raw_digest": captured.get("candidate_raw_digest"),
            "normalized_plan_digest": captured.get("normalized_plan_digest"),
            "target": captured.get("target").serialize() if captured.get("target") else None,
            "operation": captured.get("operation"),
            "scope": captured.get("mutation_scope"),
        }
        expected = {
            "intent_fingerprint": intent_b.intent_fingerprint(),
            "contract_hash": PLAN_INIT_DESCRIPTOR.contract_hash(),
            "external_authority_precondition": "request-authority-B",
            "authority_source_revision": "request-source",
            "authority_observed_raw_digest": "d" * 64,
            "candidate_raw_digest": "e" * 64,
            "normalized_plan_digest": "f" * 64,
            "target": other.serialize(),
            "operation": "plan_init",
            "scope": WRITE_SCOPE,
        }
        summary["request_value_probe"] = {
            "pass": observed == expected and no_verified_effect(result),
            "observed": observed,
            "expected": expected,
            "result_code": code_of(result),
        }


def run_authority_request_probe(summary: dict[str, Any]) -> None:
    captured: list[Any] = []
    original_evaluate = AuthorityEngine.evaluate

    with isolated("authority-request") as fixture:
        subject = fixture.store.read_subject(fixture.plan_ref)
        fixture.store._put_staged(
            replace(
                subject,
                mechanical_state={
                    **subject.mechanical_state,
                    "authority_source_revision": "request-source",
                    "authority_observed_raw_digest": "d" * 64,
                },
            )
        )
        intent = make_intent("plan_init", fixture.plan_ref, "authority-request-key", project_id="review-project")
        issued = issue_genuine(
            fixture,
            descriptor=PLAN_INIT_DESCRIPTOR,
            intent=intent,
            external="request-authority",
            source_revision="request-source",
            observed="d" * 64,
            candidate="e" * 64,
            normalized="f" * 64,
        )
        request = plan_init_request(
            fixture,
            issued,
            preconditions=make_preconditions(
                source_revision="request-source",
                observed="d" * 64,
                candidate="e" * 64,
            ),
            external="request-authority",
            normalized="f" * 64,
        )

        def capture(self, authority_request):
            captured.append(authority_request)
            return original_evaluate(self, authority_request)

        AuthorityEngine.evaluate = capture
        try:
            result = run(fixture, "plan_init", request)
        finally:
            AuthorityEngine.evaluate = original_evaluate

        authority_request = captured[0] if captured else None
        observed = {
            "intent_fingerprint": authority_request.intent_fingerprint if authority_request else None,
            "contract_hash": authority_request.contract_hash if authority_request else None,
            "external_authority_precondition": authority_request.external_authority_precondition if authority_request else None,
            "authority_source_revision": authority_request.authority_source_revision if authority_request else None,
            "authority_observed_raw_digest": authority_request.authority_observed_raw_digest if authority_request else None,
            "candidate_raw_digest": authority_request.candidate_raw_digest if authority_request else None,
            "normalized_plan_digest": authority_request.normalized_plan_digest if authority_request else None,
            "target": authority_request.target.serialize() if authority_request and authority_request.target else None,
            "operation": authority_request.operation if authority_request else None,
            "scope": dict(authority_request.requested_scope) if authority_request else None,
        }
        expected = {
            "intent_fingerprint": intent.intent_fingerprint(),
            "contract_hash": PLAN_INIT_DESCRIPTOR.contract_hash(),
            "external_authority_precondition": "request-authority",
            "authority_source_revision": "request-source",
            "authority_observed_raw_digest": "d" * 64,
            "candidate_raw_digest": "e" * 64,
            "normalized_plan_digest": "f" * 64,
            "target": fixture.plan_ref.serialize(),
            "operation": "plan_init",
            "scope": WRITE_SCOPE,
        }
        summary["authority_request_probe"] = {
            "pass": observed == expected and result.get("ok") is True,
            "observed": observed,
            "expected": expected,
            "result_code": code_of(result),
        }


def idempotency_variant(variant: str) -> tuple[bool, str]:
    with isolated(f"idem-{variant}") as fixture:
        first_intent = make_intent(
            "plan_init", fixture.plan_ref, f"idem-{variant}-key", project_id="review-project"
        )
        first_issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=first_intent)
        first = run(fixture, "plan_init", plan_init_request(fixture, first_issued))
        if code_of(first) != "PLAN_INIT_APPLIED":
            return False, f"first={code_of(first)}"

        if variant == "intent":
            changed_intent = make_intent(
                "plan_init", fixture.plan_ref, first_intent.idempotency_key, project_id="changed"
            )
            second_issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=changed_intent)
            second = run(fixture, "plan_init", plan_init_request(fixture, second_issued))
            return code_of(second) == "CONFLICT", code_of(second)

        if variant == "target":
            other = make_plan(fixture, "idem-target-other")
            changed_intent = make_intent(
                "plan_init", other, first_intent.idempotency_key, project_id="review-project"
            )
            second_issued = issue_genuine(
                fixture,
                descriptor=PLAN_INIT_DESCRIPTOR,
                intent=changed_intent,
                target=other,
            )
            second = run(
                fixture,
                "plan_init",
                plan_init_request(fixture, second_issued, plan_ref=other),
            )
            return code_of(second) == "CONFLICT", code_of(second)

        if variant == "operation":
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            changed_intent = make_intent(
                "plan_retirement",
                fixture.plan_ref,
                first_intent.idempotency_key,
                retirement_kind="abandoned",
            )
            second_issued = issue_genuine(
                fixture,
                descriptor=PLAN_RETIREMENT_DESCRIPTOR,
                intent=changed_intent,
                revision=snapshot.source_revision,
            )
            second = run(
                fixture,
                "plan_retirement",
                retirement_request(fixture, second_issued, snapshot),
            )
            return code_of(second) == "CONFLICT", code_of(second)

        if variant == "authorization":
            changed_auth = replace(
                first_issued.authorization,
                external_authority_precondition="changed-authorization-binding",
            )
            changed_lease = first_issued.issuer.issue(
                changed_auth,
                PLAN_INIT_DESCRIPTOR,
                intent=first_intent,
                trusted_context=fixture.context,
                now=NOW,
                lease_id="idem-changed-authorization-lease",
                attempt_id="idem-changed-authorization-attempt",
            )
            second = run(
                fixture,
                "plan_init",
                plan_init_request(
                    fixture,
                    first_issued,
                    lease=changed_lease,
                    external=changed_auth.external_authority_precondition,
                ),
            )
            return code_of(second) == "CONFLICT", code_of(second)

        raise ValueError(variant)


def run_negative_cases(summary: dict[str, Any]) -> None:
    cases: list[dict[str, Any]] = []

    def add(name: str, passed: bool, code: str, detail: str = "") -> None:
        cases.append({"name": name, "pass": passed, "code": code, "detail": detail})

    with isolated("neg-01") as fixture:
        result = execute_mutation(MutationIngressRequest("not_registered", fixture.store, None))
        add("NEG-01 unknown mutation operation", no_verified_effect(result), code_of(result))

    with isolated("neg-02") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-02-key", project_id="review-project")
        issued = issue_genuine(
            fixture,
            descriptor=PLAN_INIT_DESCRIPTOR,
            intent=intent,
            external="request-authority",
            source_revision="request-source",
            observed="d" * 64,
            candidate="e" * 64,
            normalized="f" * 64,
        )
        result = run(fixture, "plan_init", plan_init_request(fixture, issued, lease=None))
        add("NEG-02 missing lease", no_verified_effect(result), code_of(result))

    with isolated("neg-03") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-03-key", project_id="review-project")
        real_issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        fake = make_test_lease(
            principal_id=fixture.context.principal.id,
            operation="plan_init",
            target_ref=fixture.plan_ref,
            expected_revision=1,
            issued_at=NOW,
            lease_id="neg-03-fake-lease",
        )
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, real_issued, lease=fake),
        )
        add("NEG-03 fake unbound lease", no_verified_effect(result), code_of(result))

    with isolated("neg-04") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-04-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        changed = make_intent("plan_init", fixture.plan_ref, "neg-04-changed", project_id="changed")
        result = run(fixture, "plan_init", plan_init_request(fixture, issued, intent=changed))
        add("NEG-04 intent mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-05") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-05-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, lease=replace(issued.lease, contract_hash="f" * 64)),
        )
        add("NEG-05 contract mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-06") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-06-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, external="request-authority-8"),
        )
        add("NEG-06 authority precondition mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-07") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-07-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(
                fixture,
                issued,
                preconditions=make_preconditions(source_revision="8"),
            ),
        )
        add("NEG-07 authority source revision mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-08") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-08-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(
                fixture,
                issued,
                preconditions=make_preconditions(observed="d" * 64),
            ),
        )
        add("NEG-08 authority raw digest mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-09") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-09-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(
                fixture,
                issued,
                preconditions=make_preconditions(candidate="e" * 64),
            ),
        )
        add("NEG-09 candidate raw digest mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-10") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-10-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, normalized="d" * 64),
        )
        add("NEG-10 normalized plan digest mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-11") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-11-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        other = make_plan(fixture, "neg-11-other-target")
        changed = make_intent("plan_init", other, "neg-11-other-key", project_id="review-project")
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, plan_ref=other, intent=changed),
        )
        add("NEG-11 target mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-12", state="initialized") as fixture:
        init_intent = make_intent("plan_init", fixture.plan_ref, "neg-12-init", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=init_intent)
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        retirement_intent = make_intent("plan_retirement", fixture.plan_ref, "neg-12-retire", retirement_kind="abandoned")
        request = retirement_request(fixture, issued, snapshot, intent=retirement_intent)
        result = run(fixture, "plan_retirement", request)
        add("NEG-12 operation mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-13") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-13-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        changed = replace(intent, mutation_scope={"mode": "different"})
        result = run(fixture, "plan_init", plan_init_request(fixture, issued, intent=changed))
        add("NEG-13 scope mismatch", no_verified_effect(result), code_of(result))

    with isolated("neg-14", state="initialized") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-14-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = run(fixture, "plan_init", plan_init_request(fixture, issued))
        after = fixture.store.read_subject(fixture.plan_ref)
        add(
            "NEG-14 PLAN_INIT re-entry",
            no_verified_effect(result) and before == after and code_of(result) == "PLAN_INIT_ALREADY_INITIALIZED",
            code_of(result),
        )

    with isolated("neg-15") as fixture:
        duplicate_parent = fixture.workspace.workdir / "duplicate"
        duplicate_parent.mkdir()
        fixture.workspace.create_project("review-project", parent_dir=duplicate_parent)
        ambiguous = resolve_project_candidates("review-workspace", fixture.registry, "review-project")
        intent = make_intent("plan_init", fixture.plan_ref, "neg-15-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, evidence=ambiguous, binding=None),
        )
        add(
            "NEG-15 ambiguous project",
            no_verified_effect(result) and code_of(result) == "NEEDS_SEMANTIC_CHOICE",
            code_of(result),
        )

    with isolated("neg-16", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = make_intent(
            "plan_retirement", fixture.plan_ref, "neg-16-key", retirement_kind="superseded"
        )
        issued = issue_genuine(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_retirement",
            retirement_request(fixture, issued, snapshot, retirement_kind="superseded"),
        )
        add(
            "NEG-16 missing retirement successor",
            no_verified_effect(result) and code_of(result) == "RETIREMENT_SUCCESSOR_REQUIRED",
            code_of(result),
        )

    with isolated("neg-17", state="initialized") as fixture:
        successor = make_plan(fixture, "neg-17-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        successor_subject = fixture.store.read_subject(successor)
        fixture.store._put_staged(set_revision_number(successor_subject, 2))
        intent = make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "neg-17-key",
            retirement_kind="superseded",
            successor_ref=successor.serialize(),
        )
        issued = issue_genuine(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_retirement",
            retirement_request(fixture, issued, snapshot, successor=successor, retirement_kind="superseded"),
        )
        add(
            "NEG-17 successor revision drift",
            no_verified_effect(result) and code_of(result) == "RETIREMENT_STALE_SNAPSHOT",
            code_of(result),
        )

    with isolated("neg-18", state="initialized") as fixture:
        subject = fixture.store.read_subject(fixture.plan_ref)
        fixture.store._put_staged(
            replace(subject, mechanical_state={**subject.mechanical_state, "active": True, "current": True})
        )
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = make_intent("plan_retirement", fixture.plan_ref, "neg-18-key", retirement_kind="abandoned")
        issued = issue_genuine(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = run(fixture, "plan_retirement", retirement_request(fixture, issued, snapshot))
        add(
            "NEG-18 protected retirement target",
            no_verified_effect(result) and code_of(result) == "RETIREMENT_TARGET_PROTECTED",
            code_of(result),
        )

    idempotency_details = {}
    for variant in ("intent", "target", "operation", "authorization"):
        passed, code = idempotency_variant(variant)
        idempotency_details[variant] = {"pass": passed, "observed": code, "expected": "CONFLICT"}
    add(
        "NEG-19 same idempotency key changed intent/target/operation/authorization",
        all(item["pass"] for item in idempotency_details.values()),
        "composite",
        detail=str(idempotency_details),
    )

    with isolated("neg-20") as fixture:
        intent = make_intent("plan_init", fixture.plan_ref, "neg-20-key", project_id="review-project")
        issued = issue_genuine(fixture, descriptor=PLAN_INIT_DESCRIPTOR, intent=intent)
        result = run(
            fixture,
            "plan_init",
            plan_init_request(fixture, issued, lease=issued.lease.mark_outcome_unknown()),
        )
        add("NEG-20 unknown outcome blindly replayed", no_verified_effect(result), code_of(result))

    summary["negative_cases"] = cases
    summary["idempotency_variants"] = idempotency_details


def main() -> int:
    summary: dict[str, Any] = {}
    run_positive_cases(summary)
    run_binding_matrix(summary)
    run_request_value_probe(summary)
    run_authority_request_probe(summary)
    run_negative_cases(summary)

    positives = summary["positive_cases"]
    bindings = summary["binding_matrix"]
    negatives = summary["negative_cases"]
    summary["counts"] = {
        "positive_cases": len(positives),
        "positive_pass": sum(item["pass"] for item in positives),
        "binding_cases": len(bindings),
        "binding_reject": sum(item["pass"] for item in bindings),
        "negative_cases": len(negatives),
        "negative_reject": sum(item["pass"] for item in negatives),
        "negative_unexpected_accept": sum(not item["pass"] for item in negatives),
    }
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(negatives)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={summary['counts']['negative_reject']}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={summary['counts']['negative_unexpected_accept']}")
    print(f"INDEPENDENT_POSITIVE_CASE_COUNT={len(positives)}")
    print(f"INDEPENDENT_POSITIVE_CASE_PASS_COUNT={summary['counts']['positive_pass']}")
    print(f"LEASE_BINDING_REVIEW_CASE_COUNT={len(bindings)}")
    print(f"LEASE_BINDING_REVIEW_REJECT_COUNT={summary['counts']['binding_reject']}")
    print("IDEMPOTENCY_SEMANTIC_BINDING_REVIEW=" + ("PASS" if summary["idempotency_variants"]["authorization"]["pass"] else "FAIL"))
    print("REQUEST_VALUE_PROBE=" + ("PASS" if summary["request_value_probe"]["pass"] else "FAIL"))
    print("AUTHORITY_REQUEST_PROBE=" + ("PASS" if summary["authority_request_probe"]["pass"] else "FAIL"))
    print("REVIEW_PROBE_JSON_BEGIN")
    import json

    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    print("REVIEW_PROBE_JSON_END")
    return 0 if all(
        (
            summary["counts"]["positive_pass"] == summary["counts"]["positive_cases"],
            summary["counts"]["binding_reject"] == summary["counts"]["binding_cases"],
            summary["counts"]["negative_reject"] == summary["counts"]["negative_cases"],
            summary["request_value_probe"]["pass"],
            summary["authority_request_probe"]["pass"],
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
