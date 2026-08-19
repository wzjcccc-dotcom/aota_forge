#!/usr/bin/env python3
"""Review-only behavioral checks for the M4-2 source candidate.

This guard imports the candidate's implementation and exercises its public
issuance and validation behavior.  It does not modify candidate source, graph
state, ingress registration, or durable authority state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "d74953be16b103fbd09b0ee18b203881244c4f95"
TARGET_SHA = "44150acf551edf8b77101eb5c64d74d090f9a7b5"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []
_UNSET = object()
sys.path.insert(0, str(ROOT))


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))
    return passed


def expect_failure(call, code: str) -> bool:
    from aota_forge.core.authorization import AuthorizationFailure

    try:
        call()
    except AuthorizationFailure as exc:
        return exc.code == code
    return False


def fixture(principal_id: str = "m4-2-independent-reviewer"):
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id=principal_id,
        principal_type="operator",
        provenance="m4-2-independent-review",
        channel="fixture",
    )
    target_a = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-independent-a", sub_kind=SubjectKind.WORK),
    )
    target_b = make_object_ref(
        IdKind.SUBJECT,
        make_id(IdKind.SUBJECT, "m4-2-independent-b", sub_kind=SubjectKind.WORK),
    )
    return context, target_a, target_b


def descriptor(*, approval_required: bool = False, decision_required: bool = False, external: bool = True):
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY

    return OperationContractDescriptor(
        name="m4_2_independent_mutation",
        description="independent M4-2 review fixture",
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


def intent(target, *, value: str = "one", key: str = "review-key"):
    from aota_forge.core.contracts.mutation import MutationIntent

    return MutationIntent(
        operation="m4_2_independent_mutation",
        semantic_inputs={"value": value},
        logical_target=target.to_canonical(),
        mutation_scope={"mode": "write"},
        idempotency_key=key,
    )


def authorization(
    context,
    target,
    contract,
    *,
    external: bool = True,
    basis: str = "trusted_scope_no_extra_approval",
    approval=None,
    decision=None,
    trusted_context=_UNSET,
    normalized_digest: str | None = "c" * 64,
):
    from aota_forge.core.authority import TrustedMutationAuthorization

    value = intent(target)
    if trusted_context is _UNSET:
        trusted_context = context
    return TrustedMutationAuthorization(
        principal=context.principal,
        operation=contract.name,
        target=target,
        mutation_scope={"mode": "write"},
        contract_hash=contract.contract_hash(),
        intent_fingerprint=value.intent_fingerprint(),
        subject_expected_revision=3,
        external_authority_precondition="raw-revision-3" if external else None,
        authority_source_revision="3" if external else None,
        authority_observed_raw_digest="a" * 64 if external else None,
        candidate_raw_digest="b" * 64 if external else None,
        normalized_plan_digest=normalized_digest,
        authorization_basis=basis,
        approval_basis=approval,
        decision_basis=decision,
        reservation_ref="review-reservation" if basis == "project_milestone_semantic_decision" else None,
        authorization_id="review-authorization",
        trusted_context=trusted_context,
    )


def issue(issuer, auth, contract, context, target, *, now=NOW, **kwargs):
    return issuer.issue(
        auth,
        contract,
        intent=intent(target),
        trusted_context=context,
        now=now,
        **kwargs,
    )


def main() -> int:
    from aota_forge.core.authorization import AuthorizationErrorCode, CapabilityLeaseIssuer
    from aota_forge.core.authority import ApprovalEvidence, MaterializedDecisionEvidence
    from aota_forge.core.capability_lease import LEASE_OUTCOME_UNKNOWN
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    check("TARGET_COMMIT_EXISTS", subprocess.call(["git", "cat-file", "-e", f"{TARGET_SHA}^{{commit}}"], cwd=ROOT) == 0)
    check("TARGET_DESCENDS_FROM_COMMON_BASE", subprocess.call(["git", "merge-base", "--is-ancestor", BASE_SHA, TARGET_SHA], cwd=ROOT) == 0)
    source_paths = subprocess.check_output(
        ["git", "diff", "--name-only", BASE_SHA, TARGET_SHA], cwd=ROOT, text=True
    ).splitlines()
    check(
        "TARGET_SOURCE_SCOPE_EXACT",
        set(source_paths)
        == {
            "aota_forge/core/authority.py",
            "aota_forge/core/authorization.py",
            "aota_forge/core/capability_lease.py",
            "deploy/evidence/issues/9/m4-2-source/m4-2-source-implementation.json",
            "deploy/evidence/issues/9/m4-2-source/m4-2-source-regression-results.json",
            "deploy/evidence/issues/9/m4-2-source/m4-2-source-scope-proof.json",
            "scripts/m4_2_source_guard.py",
        },
        ",".join(source_paths),
    )

    context, target_a, target_b = fixture()
    contract = descriptor()
    auth = authorization(context, target_a, contract)
    issuer = CapabilityLeaseIssuer()
    lease = issue(issuer, auth, contract, context, target_a, lease_id="review-lease-a", attempt_id="review-attempt-a")
    intent_a = intent(target_a)

    check(
        "NEG-M42-SRC-01",
        expect_failure(
            lambda: issue(issuer, auth, contract, context, target_a, operation="other-operation"),
            AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH.value,
        ),
        "issuer widens operation",
    )
    check(
        "NEG-M42-SRC-02",
        expect_failure(
            lambda: issue(
                issuer,
                auth,
                contract,
                context,
                target_a,
                mutation_scope={"mode": "write", "extra": "scope"},
            ),
            AuthorizationErrorCode.AUTHORIZATION_SCOPE_MISMATCH.value,
        ),
        "issuer widens scope",
    )
    check(
        "NEG-M42-SRC-03",
        expect_failure(
            lambda: issuer.validate_lease(
                lease,
                trusted_context=context,
                operation=contract.name,
                target=target_b,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                authority_source_revision="3",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW,
            ),
            AuthorizationErrorCode.LEASE_TARGET_MISMATCH.value,
        ),
        "lease used for another target",
    )
    check(
        "NEG-M42-SRC-04",
        expect_failure(
            lambda: issuer.validate_lease(
                lease,
                trusted_context=context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint="d" * 64,
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                now=NOW,
            ),
            AuthorizationErrorCode.LEASE_INTENT_MISMATCH.value,
        ),
        "lease used for another intent",
    )

    approval_contract = descriptor(decision_required=True, external=False)
    approval = ApprovalEvidence(
        approver=context.principal,
        trusted_context=context,
        operation=approval_contract.name,
        target=target_a,
        expected_revision=3,
        scope={"mode": "write"},
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(context.principal))
    approval_auth = authorization(
        context,
        target_a,
        approval_contract,
        external=False,
        basis="approval_evidence",
        approval=approval,
    )
    check(
        "NEG-M42-SRC-05",
        expect_failure(
            lambda: issue(CapabilityLeaseIssuer(), approval_auth, approval_contract, context, target_a),
            AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
        ),
        "approval substitutes Decision",
    )
    check(
        "NEG-M42-SRC-06",
        expect_failure(
            lambda: issuer.validate_lease(
                lease,
                trusted_context=context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                authority_source_revision="3",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW + timedelta(seconds=60),
            ),
            AuthorizationErrorCode.LEASE_EXPIRED.value,
        ),
        "expired lease reused",
    )
    check(
        "NEG-M42-SRC-07",
        expect_failure(
            lambda: issuer.validate_lease(
                lease.consume_for_fixture(),
                trusted_context=context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                authority_source_revision="3",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW,
            ),
            AuthorizationErrorCode.LEASE_CONSUMED.value,
        ),
        "consumed lease reused",
    )
    check(
        "NEG-M42-SRC-08",
        expect_failure(
            lambda: issuer.validate_lease(
                lease.revoke(),
                trusted_context=context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                authority_source_revision="3",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW,
            ),
            AuthorizationErrorCode.LEASE_REVOKED.value,
        ),
        "revoked lease reused",
    )
    check(
        "NEG-M42-SRC-09",
        expect_failure(
            lambda: issuer.validate_lease(
                lease.mark_outcome_unknown(),
                trusted_context=context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                subject_expected_revision=3,
                external_authority_precondition="raw-revision-3",
                authority_source_revision="3",
                authority_observed_raw_digest="a" * 64,
                candidate_raw_digest="b" * 64,
                normalized_plan_digest="c" * 64,
                now=NOW,
            ),
            AuthorizationErrorCode.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION.value,
        ),
        "unknown outcome reuses unexpired lease",
    )
    digest_only_auth = authorization(
        context,
        target_a,
        contract,
        normalized_digest="c" * 64,
    )
    digest_only_auth = replace(
        digest_only_auth,
        external_authority_precondition=None,
        authority_source_revision=None,
        authority_observed_raw_digest=None,
        candidate_raw_digest=None,
    )
    check(
        "NEG-M42-SRC-10",
        expect_failure(
            lambda: issue(CapabilityLeaseIssuer(), digest_only_auth, contract, context, target_a),
            AuthorizationErrorCode.AUTHORIZATION_MISSING.value,
        ),
        "normalized Plan digest used as external CAS",
    )
    untrusted_auth = authorization(context, target_a, contract, trusted_context=None)
    check(
        "NEG-M42-SRC-11",
        expect_failure(
            lambda: CapabilityLeaseIssuer().issue(untrusted_auth, contract, intent=intent(target_a), now=NOW),
            AuthorizationErrorCode.AUTHORIZATION_MISSING.value,
        ),
        "model self-asserts trusted lease",
    )
    from aota_forge.core.authorization import M4_2_DURABLE_JOURNAL_IMPLEMENTED, M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED, M4_2_WRITE_INGRESS_IMPLEMENTED

    check(
        "NEG-M42-SRC-12",
        not M4_2_WRITE_INGRESS_IMPLEMENTED
        and not M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED
        and not M4_2_DURABLE_JOURNAL_IMPLEMENTED
        and "aota_forge/core/ingress.py" not in source_paths,
        "source enables write ingress",
    )

    check(
        "L1_LEASE_OPERATION_BINDING",
        expect_failure(
            lambda: issuer.validate_lease(
                lease,
                trusted_context=context,
                operation="other-operation",
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                now=NOW,
            ),
            AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH.value,
        ),
    )
    other_context, _, _ = fixture("m4-2-other-reviewer")
    check(
        "L6_LEASE_PRINCIPAL_BINDING",
        expect_failure(
            lambda: issuer.validate_lease(
                lease,
                trusted_context=other_context,
                operation=contract.name,
                target=target_a,
                mutation_scope={"mode": "write"},
                contract_hash=contract.contract_hash(),
                intent_fingerprint=intent_a.intent_fingerprint(),
                now=NOW,
            ),
            AuthorizationErrorCode.AUTHORIZATION_MISSING.value,
        ),
    )
    fresh = issue(issuer, auth, contract, context, target_a, now=NOW + timedelta(seconds=1), lease_id="review-lease-b", attempt_id="review-attempt-b")
    check(
        "IDEMPOTENCY_SEMANTIC_IDENTITY_STABLE",
        lease.semantic_intent_identity == fresh.semantic_intent_identity
        and lease.lease_id != fresh.lease_id
        and lease.attempt_id != fresh.attempt_id,
    )
    check(
        "REVISION_DOMAIN_SEPARATION",
        lease.normalized_plan_digest != lease.authority_observed_raw_digest
        and lease.normalized_plan_digest != lease.external_authority_precondition,
    )
    check("UNKNOWN_OUTCOME_STATE_IS_EXPLICIT", lease.mark_outcome_unknown().outcome_state == LEASE_OUTCOME_UNKNOWN)
    check("SHORT_TTL_IS_BOUNDED", (lease.expires_at - lease.issued_at).total_seconds() <= 300)

    # These are source-level probes beyond the required 12 cases.  They expose
    # whether the issuer validates the semantic evidence it claims to consume.
    decision_contract = descriptor(decision_required=True, external=False)
    decision_ref = make_object_ref(
        IdKind.DECISION,
        make_id(IdKind.DECISION, "m4-2-unresolved-decision"),
    )
    invalid_decision = MaterializedDecisionEvidence(
        decision_ref=decision_ref,
        operation=decision_contract.name,
        target=target_a,
        expected_revision=3,
        scope={"mode": "write"},
        evidence_digest="0" * 64,
    )
    invalid_decision_auth = authorization(
        context,
        target_a,
        decision_contract,
        external=False,
        basis="materialized_decision_evidence",
        decision=invalid_decision,
    )
    invalid_decision_result = True
    try:
        CapabilityLeaseIssuer().issue(
            invalid_decision_auth,
            decision_contract,
            intent=intent(target_a),
            trusted_context=context,
            now=NOW,
        )
    except Exception:
        invalid_decision_result = False
    check("EXTRA_INVALID_DECISION_EVIDENCE_REJECTED", not invalid_decision_result)

    project_contract = descriptor(external=False)
    project_auth = authorization(
        context,
        target_a,
        project_contract,
        external=False,
        basis="project_milestone_semantic_decision",
    )
    project_result = True
    try:
        CapabilityLeaseIssuer().issue(
            project_auth,
            project_contract,
            intent=intent(target_a),
            trusted_context=context,
            now=NOW,
        )
    except Exception:
        project_result = False
    check("EXTRA_PROJECT_DECISION_BASIS_NOT_INVENTED", not project_result)

    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"INDEPENDENT_CASE_COUNT=12")
    print(f"INDEPENDENT_CASE_REJECT_COUNT={sum(ok for name, ok, _ in RESULTS if name.startswith('NEG-M42-SRC-'))}")
    print(f"INDEPENDENT_CASE_UNEXPECTED_ACCEPT_COUNT={12 - sum(ok for name, ok, _ in RESULTS if name.startswith('NEG-M42-SRC-'))}")
    print(f"INDEPENDENT_GUARD_RESULT={'PASS' if not failed else 'FAIL'}")
    if failed:
        print("FAILED_CHECKS=" + ",".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
