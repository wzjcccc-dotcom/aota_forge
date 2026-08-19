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

BASE_SHA = "d74953be16b103fbd09b0ee18b203881244c4f95"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
M4_2_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/authority.py",
    "aota_forge/core/capability_lease.py",
    "aota_forge/core/authorization.py",
}
ALLOWED_NON_SOURCE_PATHS = {
    "scripts/m4_2_source_guard.py",
}
EVIDENCE_PREFIX = "deploy/evidence/issues/9/m4-2-source/"
RESULTS: list[tuple[str, bool, str]] = []


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
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    changed = {
        line[3:] if len(line) >= 4 else line
        for line in status
        if line
    }
    forbidden = {
        path
        for path in changed
        if path not in M4_2_EXCLUSIVE_WRITE_PATHS
        and path not in ALLOWED_NON_SOURCE_PATHS
        and not path.startswith(EVIDENCE_PREFIX)
    }
    return check("SOURCE_PARTITION_EXACT", not forbidden, ", ".join(sorted(forbidden)))


def main() -> int:
    from aota_forge.core.authorization import (
        AuthorizationErrorCode,
        CapabilityLeaseIssuer,
    )
    from aota_forge.core.authority import (
        ApprovalEvidence,
        AuthorityDecision,
        AuthorityEngine,
        AuthorityRequest,
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
    check("EXACT_BASE_VERIFIED", head == BASE_SHA, head)
    _partition_check()

    context, target_a, target_b = _fixture()
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
    approval_auth = _authorization(
        context,
        target_a,
        decision_descriptor,
        external=False,
        basis="approval_evidence",
        approval=approval,
    )
    check("T8_APPROVAL_DOES_NOT_SUBSTITUTE_DECISION", _expect_failure(lambda: _issue(issuer, approval_auth, decision_descriptor, context, target_a), AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value))
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
