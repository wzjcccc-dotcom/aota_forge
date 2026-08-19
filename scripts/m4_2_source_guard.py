#!/usr/bin/env python3
"""Focused M4-2 source guard.

The guard exercises the source implementation directly.  It does not execute
an ingress handler, write a graph, persist lease state, or contact GitHub.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
M4_2_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/authority.py",
    "aota_forge/core/capability_lease.py",
    "aota_forge/core/authorization.py",
}
ALLOWED_NON_SOURCE_PATHS = {
    "scripts/m4_2_source_guard.py",
}
EVIDENCE_PREFIXES = (
    "deploy/evidence/issues/9/m4-2-source/",
    "deploy/evidence/issues/9/m4-2-source-repair/",
    "deploy/evidence/issues/9/m4-2-source-r2-repair/",
    "deploy/evidence/issues/9/m4-2-source-r3-repair/",
)
RESULTS: list[tuple[str, bool, str]] = []
RECOMPUTED_DIGEST_RESULTS: list[tuple[str, bool, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id="m4-2-operator",
        principal_type="operator",
        provenance="m4-2-guard",
        channel="fixture",
    )
    target_a = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-subject-a", sub_kind=SubjectKind.WORK),
    )
    target_b = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-subject-b", sub_kind=SubjectKind.WORK),
    )
    return context, target_a, target_b


def _descriptor(*, approval_required: bool = False, decision_required: bool = False, external: bool = True):
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY

    return OperationContractDescriptor(
        name="m4_2_mutate_subject",
        description="M4-2 bounded mutation fixture",
        inputs=(),
        required_context=(),
        optional_context=(),
        read_write=WRITE_ONLY,
        mutation_scope="write",
        required_authority="trusted",
        approval_required=approval_required,
        decision_required=decision_required,
        valid_predecessor_state="open",
        valid_successor_state="open",
        subject_revision_precondition=True,
        external_authority_precondition=external,
        idempotency="same-key-same-intent-replay",
        result_contract="canonical-mutation-result",
        errors=(),
        protocol_version="1",
    )


def _intent(target, *, key: str = "intent-key"):
    from aota_forge.core.contracts.mutation import MutationIntent

    return MutationIntent(
        operation="m4_2_mutate_subject",
        semantic_inputs={"value": "fixture"},
        logical_target=target.to_canonical(),
        mutation_scope={"mode": "write"},
        idempotency_key=key,
    )


def _authorization(context, target, descriptor, *, external: bool = True, basis: str = "trusted_scope_no_extra_approval", approval=None, decision=None):
    from aota_forge.core.authority import TrustedMutationAuthorization

    intent = _intent(target)
    return TrustedMutationAuthorization(
        principal=context.principal,
        operation=descriptor.name,
        target=target,
        mutation_scope={"mode": "write"},
        contract_hash=descriptor.contract_hash(),
        intent_fingerprint=intent.intent_fingerprint(),
        subject_expected_revision=3,
        external_authority_precondition="raw-revision-3" if external else None,
        authority_source_revision="3" if external else None,
        authority_observed_raw_digest="a" * 64 if external else None,
        candidate_raw_digest="b" * 64 if external else None,
        normalized_plan_digest="c" * 64,
        authorization_basis=basis,
        approval_basis=approval,
        decision_basis=decision,
        authorization_id="m4-2-auth",
        trusted_context=context,
    )


def _issue(issuer, authorization, descriptor, context, base_target, *, now=NOW, **kwargs):
    return issuer.issue(
        authorization,
        descriptor,
        intent=_intent(base_target),
        trusted_context=context,
        now=now,
        **kwargs,
    )


def _expect_failure(call, code: str) -> bool:
    from aota_forge.core.authorization import AuthorizationFailure

    try:
        call()
    except AuthorizationFailure as exc:
        return exc.code == code
    return False


def _partition_check() -> bool:
    committed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", f"{COMMON_SOURCE_BASE}..HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    changed = set(committed.stdout.splitlines())
    changed.update(
        line[3:] if len(line) >= 4 else line
        for line in status.stdout.splitlines()
        if line
    )
    forbidden = {
        path
        for path in changed
        if path not in M4_2_EXCLUSIVE_WRITE_PATHS
        and path not in ALLOWED_NON_SOURCE_PATHS
        and not any(path.startswith(prefix) for prefix in EVIDENCE_PREFIXES)
    }
    return check("SOURCE_PARTITION_EXACT", not forbidden, ", ".join(sorted(forbidden)))


def main() -> int:
    from aota_forge.core.authorization import (
        AuthorizationErrorCode,
        AuthorizationFailure,
        CapabilityLeaseIssuer,
    )
    from aota_forge.core.authority import (
        ApprovalEvidence,
        AuthorityDecision,
        AuthorityEngine,
        AuthorityRequest,
        MaterializedDecisionEvidence,
        TrustedMutationAuthorization,
    )
    from aota_forge.core.capability_lease import CapabilityLease
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    base_exists = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{COMMON_SOURCE_BASE}^{{commit}}"],
        check=False,
    ).returncode == 0
    is_descendant = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", COMMON_SOURCE_BASE, "HEAD"],
        check=False,
    ).returncode == 0
    check(
        "COMMON_BASE_ANCESTRY_VERIFIED",
        bool(head) and base_exists and is_descendant,
        f"base={COMMON_SOURCE_BASE} head={head}",
    )
    _partition_check()

    context, target_a, target_b = _fixture()
    same_value_different_kind = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-subject-a", sub_kind=SubjectKind.PROJECT),
    )
    descriptor = _descriptor()
    authorization = _authorization(context, target_a, descriptor)
    intent = _intent(target_a)
    issuer = CapabilityLeaseIssuer()

    equivalent = _authorization(context, target_a, descriptor)
    check("T1_TRUSTED_AUTHORIZATION_DETERMINISTIC", authorization.to_canonical_json() == equivalent.to_canonical_json() and authorization.binding_digest() == equivalent.binding_digest())
    check("T2_ISSUER_CANNOT_WIDEN_OPERATION", _expect_failure(lambda: _issue(issuer, authorization, descriptor, context, target_a, operation="other"), AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH.value))
    check("T3_ISSUER_CANNOT_WIDEN_TARGET", _expect_failure(lambda: _issue(issuer, authorization, descriptor, context, target_a, target=target_b), AuthorizationErrorCode.AUTHORIZATION_TARGET_MISMATCH.value))
    check("T4_ISSUER_CANNOT_WIDEN_SCOPE", _expect_failure(lambda: _issue(issuer, authorization, descriptor, context, target_a, mutation_scope={"mode": "write", "extra": "scope"}), AuthorizationErrorCode.AUTHORIZATION_SCOPE_MISMATCH.value))

    lease = _issue(issuer, authorization, descriptor, context, target_a, lease_id="m4-2-lease-a", attempt_id="m4-2-attempt-a")
    check("T5_LEASE_VALIDATION_ACCEPTS_EXACT_BINDING", issuer.validate_lease(lease, trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", authority_source_revision="3", authority_observed_raw_digest="a" * 64, candidate_raw_digest="b" * 64, normalized_plan_digest="c" * 64, now=NOW) is lease)
    check("T5_LEASE_EXACT_INTENT_BINDING", _expect_failure(lambda: issuer.validate_lease(lease, trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint="d" * 64, subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.LEASE_INTENT_MISMATCH.value))
    check("T6_LEASE_EXACT_TARGET_BINDING", _expect_failure(lambda: issuer.validate_lease(lease, trusted_context=context, operation=descriptor.name, target=target_b, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.LEASE_TARGET_MISMATCH.value))
    check("T7_LEASE_CONTRACT_HASH_BINDING", _expect_failure(lambda: issuer.validate_lease(lease, trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash="d" * 64, intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.AUTHORIZATION_CONTRACT_DRIFT.value))

    approval = ApprovalEvidence(
        approver=context.principal,
        trusted_context=context,
        operation=descriptor.name,
        target=target_a,
        expected_revision=3,
        scope={"mode": "write"},
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(context.principal))
    decision_descriptor = _descriptor(decision_required=True, external=False)
    decision_repo = InMemoryGraphRepository()
    decision_workflow_id = make_id(IdKind.WORKFLOW, "m4-2-decision-workflow")
    decision_repo.store(records.workflow(decision_workflow_id, semantic_intent="M4-2 decision fixture", creation_context={}))
    project_a = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-project-a", sub_kind=SubjectKind.PROJECT),
    )
    project_b = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-project-b", sub_kind=SubjectKind.PROJECT),
    )
    milestone_a = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-milestone-a", sub_kind=SubjectKind.PLAN),
    )
    milestone_b = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-milestone-b", sub_kind=SubjectKind.PLAN),
    )
    for ref, kind in (
        (target_a, "WorkSubject"),
        (target_b, "WorkSubject"),
        (project_a, "ProjectSubject"),
        (project_b, "ProjectSubject"),
        (milestone_a, "PlanSubject"),
        (milestone_b, "PlanSubject"),
    ):
        decision_repo.store(
            records.subject(
                ref.internal_id,
                kind=kind,
                workflow_ref=decision_workflow_id,
                mechanical_state={"revision": 3, "state": "open"},
                id_derivation="fixture",
            )
        )
    decision_a = records.decision(
        make_id(IdKind.DECISION, "m4-2-decision-a"),
        target_a.internal_id,
        "authorization",
        "authorize exact mutation",
        target_refs=[target_a.serialize()],
    )
    decision_b = records.decision(
        make_id(IdKind.DECISION, "m4-2-decision-b"),
        target_b.internal_id,
        "authorization",
        "authorize another target",
        target_refs=[target_b.serialize()],
    )
    project_decision_a = records.decision(
        make_id(IdKind.DECISION, "m4-2-project-decision-a"),
        project_a.internal_id,
        "authorization",
        "authorize project A",
        target_refs=[project_a.serialize()],
    )
    project_decision_b = records.decision(
        make_id(IdKind.DECISION, "m4-2-project-decision-b"),
        project_b.internal_id,
        "authorization",
        "authorize project B",
        target_refs=[project_b.serialize()],
    )
    milestone_decision_a = records.decision(
        make_id(IdKind.DECISION, "m4-2-milestone-decision-a"),
        milestone_a.internal_id,
        "authorization",
        "authorize milestone A",
        target_refs=[milestone_a.serialize()],
    )
    milestone_decision_b = records.decision(
        make_id(IdKind.DECISION, "m4-2-milestone-decision-b"),
        milestone_b.internal_id,
        "authorization",
        "authorize milestone B",
        target_refs=[milestone_b.serialize()],
    )
    for decision_record in (
        decision_a,
        decision_b,
        project_decision_a,
        project_decision_b,
        milestone_decision_a,
        milestone_decision_b,
    ):
        decision_repo.store(decision_record)
    decision_resolver = OwningSubjectResolver(decision_repo)
    decision_engine = AuthorityEngine(decision_resolver)

    def decision_evidence(decision_record, target, *, operation=decision_descriptor.name, scope=None):
        return MaterializedDecisionEvidence.from_decision(
            decision_record,
            operation=operation,
            target=target,
            expected_revision=3,
            scope=scope or {"mode": "write"},
        )

    valid_decision = decision_evidence(decision_a, target_a)
    valid_decision_auth = _authorization(
        context,
        target_a,
        decision_descriptor,
        external=False,
        basis="materialized_decision_evidence",
        decision=valid_decision,
    )
    project_descriptor = _descriptor(external=False)
    valid_project_decision = decision_evidence(project_decision_a, project_a, operation=project_descriptor.name)
    valid_project_auth = _authorization(
        context,
        project_a,
        project_descriptor,
        external=False,
        basis="project_milestone_semantic_decision",
        decision=valid_project_decision,
    )
    valid_milestone_decision = decision_evidence(
        milestone_decision_a,
        milestone_a,
        operation=project_descriptor.name,
    )
    valid_milestone_auth = _authorization(
        context,
        milestone_a,
        project_descriptor,
        external=False,
        basis="project_milestone_semantic_decision",
        decision=valid_milestone_decision,
    )
    project_only_auth = replace(valid_project_auth, decision_basis=None)
    reservation_only_auth = replace(
        project_only_auth,
        authorization_id=None,
        reservation_ref="m4-2-reservation-only",
    )
    approval_auth = _authorization(
        context,
        target_a,
        decision_descriptor,
        external=False,
        basis="approval_evidence",
        approval=approval,
    )

    def expect_recomputed_rejection(
        name,
        auth,
        contract,
        *,
        resolved_decision,
        original_decision,
    ) -> None:
        resolved_id = resolved_decision.decision_id.value
        try:
            # Materialize the exact record referenced by the evidence.  Without
            # this replacement the resolver would return the baseline record and
            # a recomputed digest case would test only digest mismatch.
            decision_repo.store(resolved_decision)
            if decision_resolver.resolve_decision(auth.decision_basis.decision_ref) != resolved_decision:
                raise AssertionError(f"resolver returned a different record: {resolved_id}")
            _issue(CapabilityLeaseIssuer(decision_engine), auth, contract, context, auth.target)
        except AuthorizationFailure as exc:
            passed = exc.code == AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value
            detail = f"{exc.code};resolved={resolved_id}"
        except Exception as exc:
            passed = False
            detail = f"{type(exc).__name__};resolved={resolved_id}"
        else:
            passed = False
            detail = f"lease issued;resolved={resolved_id}"
        finally:
            decision_repo.store(original_decision)
        RECOMPUTED_DIGEST_RESULTS.append((name, passed, True))
        check(name, passed, detail)

    # Every malformed candidate below gets a fresh digest from its own content.
    expect_recomputed_rejection(
        "D1_RECOMPUTED_MISSING_STATEMENT_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(replace(decision_a, statement=""), target_a),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, statement=""),
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "R2_EXACT_MALFORMED_DECISION_RECOMPUTED_DIGEST_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(replace(decision_a, decision_kind="", statement=""), target_a),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, decision_kind="", statement=""),
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D2_RECOMPUTED_WRONG_OPERATION_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_a, target_a, operation="other_operation"),
        ),
        decision_descriptor,
        resolved_decision=decision_a,
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D3_RECOMPUTED_WRONG_TARGET_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_a, target_b),
        ),
        decision_descriptor,
        resolved_decision=decision_a,
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D4_RECOMPUTED_WRONG_SCOPE_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_a, target_a, scope={"mode": "other"}),
        ),
        decision_descriptor,
        resolved_decision=decision_a,
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D5_RECOMPUTED_WRONG_PROJECT_REJECTED",
        replace(
            valid_project_auth,
            decision_basis=decision_evidence(
                project_decision_a,
                project_b,
                operation=project_descriptor.name,
            ),
        ),
        project_descriptor,
        resolved_decision=project_decision_a,
        original_decision=project_decision_a,
    )
    expect_recomputed_rejection(
        "D6_RECOMPUTED_WRONG_MILESTONE_REJECTED",
        replace(
            valid_milestone_auth,
            decision_basis=decision_evidence(
                milestone_decision_a,
                milestone_b,
                operation=project_descriptor.name,
            ),
        ),
        project_descriptor,
        resolved_decision=milestone_decision_a,
        original_decision=milestone_decision_a,
    )
    expect_recomputed_rejection(
        "D7_RECOMPUTED_WRONG_SUBJECT_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_b, target_a),
        ),
        decision_descriptor,
        resolved_decision=decision_b,
        original_decision=decision_b,
    )
    expect_recomputed_rejection(
        "D8_RECOMPUTED_INCOMPATIBLE_DECISION_KIND_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(replace(decision_a, decision_kind="review_result"), target_a),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, decision_kind="review_result"),
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D9_RECOMPUTED_INCOMPATIBLE_NESTED_TARGET_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(
                replace(decision_a, target_refs=[target_b.serialize()]),
                target_a,
            ),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, target_refs=[target_b.serialize()]),
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D10_RECOMPUTED_UNSUPPORTED_DECISION_KIND_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(replace(decision_a, decision_kind="unsupported_kind"), target_a),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, decision_kind="unsupported_kind"),
        original_decision=decision_a,
    )
    expect_recomputed_rejection(
        "D11_RECOMPUTED_NESTED_TARGET_KIND_MISMATCH_REJECTED",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(
                replace(decision_a, target_refs=[same_value_different_kind.serialize()]),
                target_a,
            ),
        ),
        decision_descriptor,
        resolved_decision=replace(decision_a, target_refs=[same_value_different_kind.serialize()]),
        original_decision=decision_a,
    )
    try:
        _issue(CapabilityLeaseIssuer(decision_engine), valid_decision_auth, decision_descriptor, context, target_a)
    except Exception as exc:
        valid_recomputed = False
        valid_recomputed_detail = type(exc).__name__
    else:
        valid_recomputed = True
        valid_recomputed_detail = f"accepted;resolved={decision_a.decision_id.value}"
    RECOMPUTED_DIGEST_RESULTS.append(("D12_EXACT_VALID_CONTENT_AND_DIGEST_ACCEPTED", valid_recomputed, False))
    check("D12_EXACT_VALID_CONTENT_AND_DIGEST_ACCEPTED", valid_recomputed, valid_recomputed_detail)
    invalid_digest_rejected = _expect_failure(
        lambda: _issue(
            CapabilityLeaseIssuer(decision_engine),
            replace(
                valid_decision_auth,
                decision_basis=replace(valid_decision, evidence_digest="0" * 64),
            ),
            decision_descriptor,
            context,
            target_a,
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    check(
        "INTEGRITY_INVALID_DIGEST_REJECTED",
        invalid_digest_rejected,
        f"MATERIALIZED_DECISION_REQUIRED;resolved={decision_a.decision_id.value}",
    )

    decision_cases: list[tuple[str, bool]] = []

    def expect_decision_failure(name, auth, contract, expected_code, *, decision_issuer=None):
        try:
            _issue(decision_issuer or CapabilityLeaseIssuer(decision_engine), auth, contract, context, auth.target)
        except AuthorizationFailure as exc:
            decision_cases.append((name, exc.code == expected_code))
        except Exception:
            decision_cases.append((name, False))
        else:
            decision_cases.append((name, False))

    expect_decision_failure(
        "invalid_decision",
        replace(valid_decision_auth, decision_basis=replace(valid_decision, evidence_digest="0" * 64)),
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    unresolved = replace(
        valid_decision,
        decision_ref=make_object_ref(
            IdKind.DECISION,
            make_id(IdKind.DECISION, "m4-2-unresolved-decision"),
        ),
    )
    expect_decision_failure(
        "unresolved_decision",
        replace(valid_decision_auth, decision_basis=unresolved),
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "missing_authority_engine",
        valid_decision_auth,
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
        decision_issuer=CapabilityLeaseIssuer(),
    )
    expect_decision_failure(
        "wrong_target_decision",
        _authorization(
            context,
            target_b,
            decision_descriptor,
            external=False,
            basis="materialized_decision_evidence",
            decision=valid_decision,
        ),
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "wrong_operation_decision",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_a, target_a, operation="other_operation"),
        ),
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "wrong_scope_decision",
        replace(
            valid_decision_auth,
            decision_basis=decision_evidence(decision_a, target_a, scope={"mode": "other"}),
        ),
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "arbitrary_authorization_id",
        project_only_auth,
        project_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "arbitrary_reservation_id",
        reservation_only_auth,
        project_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "wrong_project_decision",
        replace(
            valid_project_auth,
            decision_basis=decision_evidence(project_decision_b, project_b, operation=project_descriptor.name),
        ),
        project_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "wrong_milestone_decision",
        _authorization(
            context,
            milestone_a,
            project_descriptor,
            external=False,
            basis="project_milestone_semantic_decision",
            decision=decision_evidence(milestone_decision_b, milestone_b, operation=project_descriptor.name),
        ),
        project_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    expect_decision_failure(
        "approval_substitutes_decision",
        approval_auth,
        decision_descriptor,
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    try:
        CapabilityLeaseIssuer(decision_engine).issue(
            valid_decision_auth,
            decision_descriptor,
            intent=_intent(target_a),
            trusted_context=context,
            now=NOW,
        )
        decision_cases.append(("valid_decision", True))
    except Exception:
        decision_cases.append(("valid_decision", False))
    try:
        CapabilityLeaseIssuer(decision_engine).issue(
            valid_project_auth,
            project_descriptor,
            intent=_intent(project_a),
            trusted_context=context,
            now=NOW,
        )
        decision_cases.append(("valid_project_decision", True))
    except Exception:
        decision_cases.append(("valid_project_decision", False))
    decision_failures = [name for name, passed in decision_cases if not passed]
    check(
        "T8_SEMANTIC_DECISION_EVIDENCE_MATRIX",
        len(decision_cases) == 13 and not decision_failures and valid_milestone_decision.target == milestone_a,
        ",".join(decision_failures),
    )
    check("T9_EXPIRED_LEASE_REJECTED", _expect_failure(lambda: issuer.validate_lease(lease, trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW + timedelta(seconds=60)), AuthorizationErrorCode.LEASE_EXPIRED.value))
    check("T10_CONSUMED_LEASE_REJECTED", _expect_failure(lambda: issuer.validate_lease(lease.consume_for_fixture(), trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.LEASE_CONSUMED.value))
    check("T11_REVOKED_LEASE_REJECTED", _expect_failure(lambda: issuer.validate_lease(lease.revoke(), trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.LEASE_REVOKED.value))
    check("T12_UNKNOWN_OUTCOME_REJECTS_OLD_LEASE", _expect_failure(lambda: issuer.validate_lease(lease.mark_outcome_unknown(), trusted_context=context, operation=descriptor.name, target=target_a, mutation_scope={"mode": "write"}, contract_hash=descriptor.contract_hash(), intent_fingerprint=intent.intent_fingerprint(), subject_expected_revision=3, external_authority_precondition="raw-revision-3", now=NOW), AuthorizationErrorCode.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION.value))

    lease_b = _issue(issuer, authorization, descriptor, context, target_a, now=NOW + timedelta(seconds=1), lease_id="m4-2-lease-b", attempt_id="m4-2-attempt-b")
    check("T13_FRESH_ATTEMPT_PRESERVES_SEMANTIC_IDENTITY", lease.intent_fingerprint == lease_b.intent_fingerprint and lease.lease_id != lease_b.lease_id and lease.attempt_id != lease_b.attempt_id)
    no_external_auth = _authorization(context, target_a, descriptor, external=False)
    check("T14_NORMALIZED_DIGEST_NOT_EXTERNAL_CAS", _expect_failure(lambda: _issue(issuer, no_external_auth, descriptor, context, target_a), AuthorizationErrorCode.AUTHORIZATION_MISSING.value))
    check("T15_UNTRUSTED_INPUT_CANNOT_MANUFACTURE_LEASE", _expect_failure(lambda: issuer.issue({"principal": context.principal}, descriptor, now=NOW), AuthorizationErrorCode.AUTHORIZATION_MISSING.value))
    check("T16_ISSUER_DOES_NOT_SELECT_AMBIGUOUS_SUBJECT", _expect_failure(lambda: issuer.issue(authorization, descriptor, trusted_context=context, now=NOW, target=[target_a, target_b]), AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE.value))
    changed_descriptor = replace(descriptor, description="contract drift")
    check("T17_CONTRACT_DRIFT_FAILS_CLOSED", _expect_failure(lambda: _issue(issuer, authorization, changed_descriptor, context, target_a), AuthorizationErrorCode.AUTHORIZATION_CONTRACT_DRIFT.value))

    workflow_id = make_id(IdKind.WORKFLOW, "m4-2-regression-workflow")
    subject_id = make_id(IdKind.SUBJECT, "m4-2-regression-subject", sub_kind=SubjectKind.WORK)
    subject_ref = make_object_ref(IdKind.SUBJECT, subject_id)
    repo = InMemoryGraphRepository()
    repo.store(records.workflow(workflow_id, semantic_intent="M4-2 regression", creation_context={}))
    repo.store(records.subject(subject_id, kind="WorkSubject", workflow_ref=workflow_id, mechanical_state={"revision": 1}, id_derivation="fixture"))
    old_lease = CapabilityLease(
        lease_id="m4-2-m3-compatible",
        principal=context.principal,
        operation="record_decision",
        target=subject_ref,
        scope={"mode": "write"},
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=10),
        expected_revision=1,
    )
    old_result = AuthorityEngine(OwningSubjectResolver(repo)).evaluate(
        AuthorityRequest(
            principal=context.principal,
            trusted_context=context,
            operation="record_decision",
            target=subject_ref,
            requested_scope={"mode": "write"},
            lease=old_lease,
            current_revision=1,
            trusted_time=NOW,
        )
    )
    check("T18_M3_AUTHORITY_LEASE_COMPATIBILITY", old_result.decision == AuthorityDecision.ALLOW)

    check("T19_REVISION_DOMAINS_REMAIN_SEPARATE", lease.authority_source_revision == "3" and lease.normalized_plan_digest == "c" * 64 and lease.external_authority_precondition == "raw-revision-3" and lease.binding_dict()["normalized_plan_digest"] != lease.binding_dict()["external_authority_precondition"])
    check("T20_DURABLE_AUTHORITY_IS_NOT_LEASE_AUTHORITY", authorization.DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED and not lease.CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY and lease.semantic_intent_identity == authorization.intent_fingerprint)

    recomputed_invalid = [
        name for name, passed, invalid in RECOMPUTED_DIGEST_RESULTS if invalid and not passed
    ]
    recomputed_invalid_case_count = sum(1 for _, _, invalid in RECOMPUTED_DIGEST_RESULTS if invalid)
    recomputed_invalid_reject_count = recomputed_invalid_case_count - len(recomputed_invalid)
    print(f"RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT={len(RECOMPUTED_DIGEST_RESULTS)}")
    print(f"RECOMPUTED_DIGEST_INVALID_CASE_REJECT_COUNT={recomputed_invalid_reject_count}")
    if recomputed_invalid:
        print("RECOMPUTED_DIGEST_FAILED_CASES=" + ",".join(recomputed_invalid))

    failed = [name for name, passed, _ in RESULTS if not passed]
    passed = len(RESULTS) - len(failed)
    print(f"FOCUSED_TEST_COUNT={len(RESULTS)}")
    print(f"FOCUSED_TEST_RESULT={'PASS' if not failed else 'FAIL'}")
    print(f"FOCUSED_TEST_PASSED={passed}")
    if failed:
        print("FAILED_CASES=" + ",".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
