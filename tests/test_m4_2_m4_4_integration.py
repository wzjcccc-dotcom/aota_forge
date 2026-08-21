from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import unittest

from aota_forge.core.authority import (
    ApprovalEvidence,
    AuthorityDecision,
    AuthorityEngine,
    AuthorityRequest,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
)
from aota_forge.core.authorization import (
    AuthorizationErrorCode,
    AuthorizationFailure,
    CapabilityLeaseIssuer,
)
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import ProjectBinding
from aota_forge.core.contracts import (
    M4_2_AUTHORIZATION_ERROR_CODES,
    M4_4_LIFECYCLE_ERROR_CODES,
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
    error_from_dict,
)
from aota_forge.core.contracts.mutation import MutationEffect, MutationIntent, MutationPreconditions
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import OwningSubjectResolver
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.regression.fixtures import (
    TempWorkspaceFixture,
    fixture_time,
    make_test_context,
    make_test_lease,
    make_test_store,
    seed_test_subject,
)
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.revision import set_revision_number
from aota_forge.core.transitions import (
    PlanInitRequest,
    PlanRetirementRequest,
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
)


NOW = fixture_time()


@dataclass
class LifecycleFixture:
    context: object
    store: object
    plan_ref: ObjectRef
    project_evidence: object
    project_binding: ProjectBinding
    workspace: object
    registry: object


@dataclass
class AuthorizationFixture:
    intent: MutationIntent
    decision: records.Decision
    decision_evidence: MaterializedDecisionEvidence
    approval: ApprovalEvidence
    authorization: TrustedMutationAuthorization
    issuer: CapabilityLeaseIssuer
    lease: CapabilityLease


def _make_plan(store, name: str, state: str = "uninitialized", **extra) -> ObjectRef:
    mechanical_state = {
        "state": state,
        "authority_source_revision": "7",
        "authority_observed_raw_digest": "a" * 64,
    }
    mechanical_state.update(extra)
    subject_id = make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN)
    return seed_test_subject(store, subject_id, kind="plan", mechanical_state=mechanical_state)


def _make_intent(operation: str, target: ObjectRef, key: str, **semantic) -> MutationIntent:
    return MutationIntent(
        operation=operation,
        semantic_inputs=semantic,
        logical_target=target.serialize(),
        mutation_scope={"mode": "write"},
        idempotency_key=key,
    )


def _make_preconditions(revision: int = 1, source_revision: str = "7") -> MutationPreconditions:
    return MutationPreconditions(
        subject_expected_revision=revision,
        authority_source_revision=source_revision,
        authority_observed_raw_digest="a" * 64,
        candidate_raw_digest="b" * 64,
    )


def _make_lease(context, operation: str, target: ObjectRef, revision: int = 1, lease_id: str = "integration-lease"):
    return make_test_lease(
        principal_id=context.principal.id,
        operation=operation,
        target_ref=target,
        expected_revision=revision,
        issued_at=NOW,
        lease_id=lease_id,
    )


@contextmanager
def _lifecycle_fixture(name: str, state: str = "uninitialized"):
    with TempWorkspaceFixture(prefix=f"m4-integration-{name}-") as workspace:
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
        store = make_test_store()
        plan_ref = _make_plan(store, f"plan-{name}", state)
        yield LifecycleFixture(
            context=make_test_context(principal_id="integration-operator"),
            store=store,
            plan_ref=plan_ref,
            project_evidence=evidence,
            project_binding=binding,
            workspace=workspace,
            registry=registry,
        )


def _issue_authorization(
    fixture: LifecycleFixture,
    *,
    descriptor=PLAN_INIT_DESCRIPTOR,
    target: ObjectRef | None = None,
    intent: MutationIntent | None = None,
    decision_mutator=None,
    decision_subject: ObjectRef | None = None,
) -> AuthorizationFixture:
    target = target or fixture.plan_ref
    intent = intent or _make_intent(
        descriptor.name,
        target,
        f"{descriptor.name}-{target.internal_id.value}",
        project_id="p1",
        requested_state="initialized",
    )
    decision_id = make_id(IdKind.DECISION, f"decision-{target.internal_id.value}")
    decision = records.decision(
        decision_id,
        (decision_subject or target).internal_id,
        "authorization",
        "authorize exact lifecycle operation",
        target_refs=[target.serialize()],
    )
    if decision_mutator is not None:
        decision = decision_mutator(decision)
    fixture.store._put_staged(decision)
    decision_evidence = MaterializedDecisionEvidence.from_decision(
        decision,
        operation=descriptor.name,
        target=target,
        expected_revision=1,
        scope={"mode": "write"},
    )
    approval = ApprovalEvidence(
        approver=fixture.context.principal,
        trusted_context=fixture.context,
        operation=descriptor.name,
        target=target,
        expected_revision=1,
        scope={"mode": "write"},
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(fixture.context.principal))
    authorization = TrustedMutationAuthorization(
        principal=fixture.context.principal,
        operation=descriptor.name,
        target=target,
        mutation_scope={"mode": "write"},
        contract_hash=descriptor.contract_hash(),
        intent_fingerprint=intent.intent_fingerprint(),
        subject_expected_revision=1,
        external_authority_precondition="source-revision-7",
        authority_source_revision="7",
        authority_observed_raw_digest="a" * 64,
        candidate_raw_digest="b" * 64,
        normalized_plan_digest="c" * 64,
        authorization_basis="materialized_decision_evidence",
        approval_basis=approval,
        decision_basis=decision_evidence,
        authorization_id=f"auth-{target.internal_id.value}",
        trusted_context=fixture.context,
    )
    issuer = CapabilityLeaseIssuer(AuthorityEngine(OwningSubjectResolver(fixture.store)))
    lease = issuer.issue(
        authorization,
        descriptor,
        intent=intent,
        trusted_context=fixture.context,
        now=NOW,
        lease_id=f"m42-{descriptor.name}-{target.internal_id.value}",
        attempt_id=f"attempt-{target.internal_id.value}",
    )
    return AuthorizationFixture(
        intent=intent,
        decision=decision,
        decision_evidence=decision_evidence,
        approval=approval,
        authorization=authorization,
        issuer=issuer,
        lease=lease,
    )


def _plan_init_request(fixture: LifecycleFixture, *, intent, lease, evidence=None, binding=None, revision=1, external_authority_precondition=None, normalized_plan_digest=None):
    if external_authority_precondition is None and lease is not None:
        external_authority_precondition = getattr(lease, "external_authority_precondition", None)
    if normalized_plan_digest is None and lease is not None:
        normalized_plan_digest = getattr(lease, "normalized_plan_digest", None)
    return PlanInitRequest(
        trusted_context=fixture.context,
        plan_ref=fixture.plan_ref,
        intent=intent,
        preconditions=_make_preconditions(revision),
        trusted_time=NOW,
        project_evidence=fixture.project_evidence if evidence is None else evidence,
        project_binding=fixture.project_binding if binding is None else binding,
        lease=lease,
        external_authority_precondition=external_authority_precondition,
        normalized_plan_digest=normalized_plan_digest,
    )


def _retirement_request(fixture: LifecycleFixture, snapshot, *, intent, lease, successor=None, revision=1, external_authority_precondition=None, normalized_plan_digest=None):
    if external_authority_precondition is None and lease is not None:
        external_authority_precondition = getattr(lease, "external_authority_precondition", None)
    if normalized_plan_digest is None and lease is not None:
        normalized_plan_digest = getattr(lease, "normalized_plan_digest", None)
    return PlanRetirementRequest(
        trusted_context=fixture.context,
        plan_ref=fixture.plan_ref,
        snapshot=snapshot,
        intent=intent,
        preconditions=_make_preconditions(revision),
        trusted_time=NOW,
        retirement_kind="superseded" if successor is not None else "abandoned",
        successor_ref=successor,
        lease=lease,
        external_authority_precondition=external_authority_precondition,
        normalized_plan_digest=normalized_plan_digest,
    )


class M42M44IntegrationTests(unittest.TestCase):
    def test_shared_error_registry_preserves_both_lanes(self):
        required = set(M4_2_AUTHORIZATION_ERROR_CODES) | set(M4_4_LIFECYCLE_ERROR_CODES)
        for code in required:
            error = error_from_dict({"code": code, "message": code, "retryable": False})
            self.assertIsNotNone(error)
            self.assertEqual(error.code, code)
        self.assertNotEqual(
            type(error_from_dict({"code": "AUTHORIZATION_TARGET_MISMATCH"})),
            type(error_from_dict({"code": "RETIREMENT_STALE_SNAPSHOT"})),
        )

    def test_i1_valid_semantic_decision_issues_bounded_lease(self):
        with _lifecycle_fixture("i1") as fixture:
            auth = _issue_authorization(fixture)
            self.assertIsInstance(auth.lease, CapabilityLease)
            self.assertLessEqual((auth.lease.expires_at - auth.lease.issued_at).total_seconds(), 300)
            validated = auth.issuer.validate_lease(
                auth.lease,
                trusted_context=fixture.context,
                operation=PLAN_INIT_DESCRIPTOR.name,
                target=fixture.plan_ref,
                mutation_scope={"mode": "write"},
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                intent_fingerprint=auth.intent.intent_fingerprint(),
                subject_expected_revision=1,
                external_authority_precondition="source-revision-7",
                authority_source_revision="7",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW,
            )
            self.assertIs(validated, auth.lease)

    def test_i2_malformed_decision_rejected_before_lifecycle_mutation(self):
        with _lifecycle_fixture("i2") as fixture:
            before = fixture.store.read_subject(fixture.plan_ref)
            with self.assertRaises(AuthorizationFailure) as raised:
                _issue_authorization(fixture, decision_mutator=lambda value: replace(value, statement=""))
            self.assertEqual(raised.exception.code, AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref), before)

    def test_i3_wrong_decision_kind_rejected_before_lifecycle_mutation(self):
        with _lifecycle_fixture("i3") as fixture:
            before = fixture.store.read_subject(fixture.plan_ref)
            with self.assertRaises(AuthorizationFailure) as raised:
                _issue_authorization(fixture, decision_mutator=lambda value: replace(value, decision_kind="review_result"))
            self.assertEqual(raised.exception.code, AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref), before)

    def test_i4_wrong_nested_target_rejected_before_lifecycle_mutation(self):
        with _lifecycle_fixture("i4") as fixture:
            wrong_target = make_object_ref(
                IdKind.SUBJECT,
                make_id(IdKind.SUBJECT, "wrong-target", sub_kind=SubjectKind.PLAN),
            )
            before = fixture.store.read_subject(fixture.plan_ref)
            with self.assertRaises(AuthorizationFailure) as raised:
                _issue_authorization(
                    fixture,
                    decision_mutator=lambda value: replace(value, target_refs=[wrong_target.serialize()]),
                )
            self.assertEqual(raised.exception.code, AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref), before)

    def test_i5_valid_authorization_and_plan_init_are_compatible(self):
        with _lifecycle_fixture("i5") as fixture:
            auth = _issue_authorization(fixture)
            authority_result = auth.issuer._authority_engine.evaluate(
                AuthorityRequest(
                    principal=fixture.context.principal,
                    trusted_context=fixture.context,
                    operation=PLAN_INIT_DESCRIPTOR.name,
                    target=fixture.plan_ref,
                    requested_scope={"mode": "write"},
                    lease=auth.lease,
                    current_revision=1,
                    trusted_time=NOW,
                    contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                    approval_required=True,
                    approval=auth.approval,
                    decision=auth.decision_evidence,
                    intent_fingerprint=auth.intent.intent_fingerprint(),
                    external_authority_precondition="source-revision-7",
                    authority_source_revision="7",
                    authority_observed_raw_digest="a" * 64,
                    candidate_raw_digest="b" * 64,
                    normalized_plan_digest="c" * 64,
                )
            )
            self.assertEqual(authority_result.decision, AuthorityDecision.ALLOW)
            lifecycle_result = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=auth.intent,
                    lease=auth.lease,
                ),
            )
            self.assertEqual(lifecycle_result.code, "PLAN_INIT_APPLIED")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")

    def test_i6_ambiguous_project_needs_choice_without_lease_selection(self):
        with _lifecycle_fixture("i6") as fixture:
            ambiguous_parent = fixture.workspace.workdir / "ambiguous"
            ambiguous_parent.mkdir()
            fixture.workspace.create_project("p1", parent_dir=ambiguous_parent)
            ambiguous = resolve_project_candidates("w1", fixture.registry, "p1")
            intent = _make_intent("plan_init", fixture.plan_ref, "i6-choice", project_id="p1")
            result = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=intent,
                    lease=_make_lease(fixture.context, "plan_init", fixture.plan_ref, lease_id="i6-lease"),
                    evidence=ambiguous,
                    binding=None,
                ),
            )
            self.assertEqual(ambiguous.status, "NEEDS_SEMANTIC_CHOICE")
            self.assertEqual(result.code, "NEEDS_SEMANTIC_CHOICE")
            self.assertEqual(result.mutation_effect, MutationEffect.NEEDS_SEMANTIC_CHOICE)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "uninitialized")

    def test_i7_plan_init_reentry_is_denied(self):
        with _lifecycle_fixture("i7", state="initialized") as fixture:
            intent = _make_intent("plan_init", fixture.plan_ref, "i7-reentry", project_id="p1")
            result = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=intent,
                    lease=None,
                ),
            )
            self.assertEqual(result.code, "PLAN_INIT_ALREADY_INITIALIZED")
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 1)

    def test_i8_abandoned_retirement_preserves_cancelled_semantics(self):
        with _lifecycle_fixture("i8", state="initialized") as fixture:
            subject = fixture.store.read_subject(fixture.plan_ref)
            patched = replace(subject, mechanical_state={**subject.mechanical_state, "evidence_history": ["e1"]})
            fixture.store._put_staged(patched)
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, "i8-retire", retirement_kind="abandoned")
            auth = _issue_authorization(
                fixture,
                descriptor=PLAN_RETIREMENT_DESCRIPTOR,
                intent=intent,
            )
            result = retire_plan(
                fixture.store,
                _retirement_request(
                    fixture,
                    snapshot,
                    intent=intent,
                    lease=auth.lease,
                ),
            )
            state = fixture.store.read_subject(fixture.plan_ref).mechanical_state
            self.assertEqual(result.code, "RETIREMENT_APPLIED")
            self.assertEqual(state["state"], "cancelled")
            self.assertEqual(state["evidence_history"], ["e1"])

    def test_i9_exact_successor_retirement_is_valid(self):
        with _lifecycle_fixture("i9", state="initialized") as fixture:
            successor = _make_plan(fixture.store, "i9-successor", state="initialized")
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent(
                "plan_retirement",
                fixture.plan_ref,
                "i9-retire",
                retirement_kind="superseded",
                successor_ref=successor.serialize(),
            )
            auth = _issue_authorization(
                fixture,
                descriptor=PLAN_RETIREMENT_DESCRIPTOR,
                intent=intent,
            )
            result = retire_plan(
                fixture.store,
                _retirement_request(
                    fixture,
                    snapshot,
                    intent=intent,
                    successor=successor,
                    lease=auth.lease,
                ),
            )
            self.assertEqual(result.code, "RETIREMENT_APPLIED")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "superseded")

    def test_i10_successor_revision_drift_has_no_retirement_effect(self):
        with _lifecycle_fixture("i10", state="initialized") as fixture:
            successor = _make_plan(fixture.store, "i10-successor", state="initialized")
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            successor_subject = fixture.store.read_subject(successor)
            fixture.store._put_staged(set_revision_number(successor_subject, 2))
            intent = _make_intent(
                "plan_retirement",
                fixture.plan_ref,
                "i10-retire",
                retirement_kind="superseded",
                successor_ref=successor.serialize(),
            )
            result = retire_plan(
                fixture.store,
                _retirement_request(
                    fixture,
                    snapshot,
                    intent=intent,
                    successor=successor,
                    lease=_make_lease(fixture.context, "plan_retirement", fixture.plan_ref, lease_id="i10-lease"),
                ),
            )
            self.assertEqual(result.code, "RETIREMENT_STALE_SNAPSHOT")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")

    def test_i11_unknown_outcome_cannot_blind_retry_lifecycle(self):
        with _lifecycle_fixture("i11") as fixture:
            lease = _make_lease(fixture.context, "plan_init", fixture.plan_ref, lease_id="i11-lease").mark_outcome_unknown()
            intent = _make_intent("plan_init", fixture.plan_ref, "i11-init", project_id="p1")
            result = plan_init(
                fixture.store,
                _plan_init_request(fixture, intent=intent, lease=lease),
            )
            self.assertNotEqual(result.mutation_effect, MutationEffect.APPLIED_VERIFIED)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "uninitialized")

    def test_i12_changed_intent_conflicts_on_same_idempotency_identity(self):
        with _lifecycle_fixture("i12") as fixture:
            first_intent = _make_intent("plan_init", fixture.plan_ref, "i12-key", project_id="p1")
            first_auth = _issue_authorization(fixture, intent=first_intent)
            first = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=first_intent,
                    lease=first_auth.lease,
                ),
            )
            changed_intent = _make_intent("plan_init", fixture.plan_ref, "i12-key", project_id="other")
            second_auth = _issue_authorization(fixture, intent=changed_intent)
            second = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=changed_intent,
                    lease=second_auth.lease,
                    revision=2,
                ),
            )
            self.assertEqual(first.code, "PLAN_INIT_APPLIED")
            self.assertEqual(second.code, "CONFLICT")
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 2)

    def test_cross01_authorization_cannot_override_ambiguous_project(self):
        with _lifecycle_fixture("cross01") as fixture:
            auth = _issue_authorization(fixture)
            ambiguous_parent = fixture.workspace.workdir / "ambiguous"
            ambiguous_parent.mkdir()
            fixture.workspace.create_project("p1", parent_dir=ambiguous_parent)
            ambiguous = resolve_project_candidates("w1", fixture.registry, "p1")
            result = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=auth.intent,
                    lease=auth.lease,
                    evidence=ambiguous,
                    binding=None,
                ),
            )
            self.assertEqual(result.code, "NEEDS_SEMANTIC_CHOICE")
            self.assertEqual(result.mutation_effect, MutationEffect.NEEDS_SEMANTIC_CHOICE)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "uninitialized")

    def test_cross02_lease_target_must_match_lifecycle_target(self):
        with _lifecycle_fixture("cross02") as fixture:
            target_a = fixture.plan_ref
            target_b = _make_plan(fixture.store, "cross02-b")
            lease_for_a = _make_lease(fixture.context, "plan_init", target_a, lease_id="cross02-a")
            intent_for_b = _make_intent("plan_init", target_b, "cross02-b-init", project_id="p1")
            request = replace(
                _plan_init_request(fixture, intent=intent_for_b, lease=lease_for_a),
                plan_ref=target_b,
            )
            result = plan_init(fixture.store, request)
            self.assertNotEqual(result.mutation_effect, MutationEffect.APPLIED_VERIFIED)
            self.assertEqual(fixture.store.read_subject(target_b).mechanical_state["state"], "uninitialized")

    def test_cross03_wrong_decision_kind_cannot_authorize_lifecycle_operation(self):
        with _lifecycle_fixture("cross03") as fixture:
            with self.assertRaises(AuthorizationFailure) as raised:
                _issue_authorization(fixture, decision_mutator=lambda value: replace(value, decision_kind="review_result"))
            self.assertEqual(raised.exception.code, AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value)

    def test_cross04_project_decision_cannot_authorize_plan_lifecycle(self):
        with _lifecycle_fixture("cross04") as fixture:
            project_ref = make_object_ref(
                IdKind.SUBJECT,
                make_id(IdKind.SUBJECT, "cross04-project", sub_kind=SubjectKind.PROJECT),
            )
            with self.assertRaises(AuthorizationFailure) as raised:
                _issue_authorization(fixture, decision_subject=project_ref)
            self.assertEqual(raised.exception.code, AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value)

    def test_cross05_retirement_snapshot_cannot_be_bypassed_by_authorization(self):
        with _lifecycle_fixture("cross05", state="initialized") as fixture:
            successor = _make_plan(fixture.store, "cross05-successor", state="initialized")
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            auth_intent = _make_intent(
                "plan_retirement",
                fixture.plan_ref,
                "cross05-retire",
                retirement_kind="superseded",
                successor_ref=successor.serialize(),
            )
            auth = _issue_authorization(
                fixture,
                descriptor=PLAN_RETIREMENT_DESCRIPTOR,
                intent=auth_intent,
            )
            successor_subject = fixture.store.read_subject(successor)
            fixture.store._put_staged(set_revision_number(successor_subject, 2))
            result = retire_plan(
                fixture.store,
                _retirement_request(
                    fixture,
                    snapshot,
                    intent=auth_intent,
                    successor=successor,
                    lease=auth.lease,
                ),
            )
            self.assertEqual(result.code, "RETIREMENT_STALE_SNAPSHOT")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")

    def test_cross06_unknown_outcome_denies_authorization_and_replay(self):
        with _lifecycle_fixture("cross06") as fixture:
            auth = _issue_authorization(fixture)
            unknown = auth.lease.mark_outcome_unknown()
            with self.assertRaises(AuthorizationFailure) as raised:
                auth.issuer.validate_lease(
                    unknown,
                    trusted_context=fixture.context,
                    operation=PLAN_INIT_DESCRIPTOR.name,
                    target=fixture.plan_ref,
                    mutation_scope={"mode": "write"},
                    contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                    intent_fingerprint=auth.intent.intent_fingerprint(),
                    subject_expected_revision=1,
                    external_authority_precondition="source-revision-7",
                    authority_source_revision="7",
                    authority_observed_raw_digest="a" * 64,
                    candidate_raw_digest="b" * 64,
                    normalized_plan_digest="c" * 64,
                    now=NOW,
                )
            self.assertEqual(
                raised.exception.code,
                AuthorizationErrorCode.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION.value,
            )


if __name__ == "__main__":
    unittest.main()
