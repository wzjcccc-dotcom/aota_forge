"""M4-2 trusted mutation authorization and bounded lease issuance.

This module only validates already-materialized authority and creates a
short-lived, exact-binding value object.  It does not choose semantic targets,
write an authority store, execute a handler, or reconcile an external write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from aota_forge.core.authority import (
    ApprovalEvidence,
    AuthorityEngine,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
    resolve_authority_target,
)
from aota_forge.core.capability_lease import (
    CapabilityLease,
    LEASE_CONSUMED,
    LEASE_REVOKED,
    MAX_LEASE_TTL_SECONDS,
    normalize_scope,
)
from aota_forge.core.context import TrustedContext, resolve_principal_binding
from aota_forge.core.contracts.descriptor import OperationContractDescriptor, READ_ONLY
from aota_forge.core.contracts.mutation import MutationIntent
from aota_forge.core.identity.refs import ObjectRef


LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER = False
APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE = False
CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY = False
DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED = True
M4_2_WRITE_INGRESS_IMPLEMENTED = False
M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED = False
M4_2_DURABLE_JOURNAL_IMPLEMENTED = False
UNKNOWN_OUTCOME_BLIND_LEASE_REUSE = False
LEASE_MAY_CROSS_UNKNOWN_OUTCOME_RETRY = False
NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN = False
DEFAULT_LEASE_TTL_SECONDS = 60

_UNSET = object()


class AuthorizationErrorCode(str, Enum):
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    AUTHORIZATION_SCOPE_MISMATCH = "AUTHORIZATION_SCOPE_MISMATCH"
    AUTHORIZATION_TARGET_MISMATCH = "AUTHORIZATION_TARGET_MISMATCH"
    AUTHORIZATION_OPERATION_MISMATCH = "AUTHORIZATION_OPERATION_MISMATCH"
    AUTHORIZATION_CONTRACT_DRIFT = "AUTHORIZATION_CONTRACT_DRIFT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    MATERIALIZED_DECISION_REQUIRED = "MATERIALIZED_DECISION_REQUIRED"
    SUBJECT_REVISION_STALE = "SUBJECT_REVISION_STALE"
    AUTHORITY_PRECONDITION_STALE = "AUTHORITY_PRECONDITION_STALE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_REVOKED = "LEASE_REVOKED"
    LEASE_CONSUMED = "LEASE_CONSUMED"
    LEASE_INTENT_MISMATCH = "LEASE_INTENT_MISMATCH"
    LEASE_TARGET_MISMATCH = "LEASE_TARGET_MISMATCH"
    LEASE_SCOPE_MISMATCH = "LEASE_SCOPE_MISMATCH"
    OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION = "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION"
    NEEDS_SEMANTIC_CHOICE = "NEEDS_SEMANTIC_CHOICE"


_ERROR_RETRYABLE = {
    AuthorizationErrorCode.SUBJECT_REVISION_STALE.value: True,
    AuthorizationErrorCode.AUTHORITY_PRECONDITION_STALE.value: True,
}


class AuthorizationFailure(ValueError):
    """Canonical machine-readable failure at the authorization boundary."""

    def __init__(
        self,
        code: AuthorizationErrorCode | str,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, AuthorizationErrorCode) else str(code)
        self.message = message or self.code
        self.retryable = _ERROR_RETRYABLE.get(self.code, False)
        self.details = dict(details or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


AuthorizationError = AuthorizationFailure


@dataclass(frozen=True)
class AuthorizationResult:
    """Non-throwing projection for adapters that prefer result envelopes."""

    lease: CapabilityLease | None = None
    error: AuthorizationFailure | None = None

    @property
    def ok(self) -> bool:
        return self.lease is not None and self.error is None

    @property
    def code(self) -> str | None:
        return None if self.error is None else self.error.code

    def to_dict(self) -> dict[str, Any]:
        if self.error is not None:
            return {"ok": False, "error": self.error.to_dict()}
        return {"ok": True, "lease": self.lease.to_audit() if self.lease else None}


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _ambiguous_target(value: object) -> bool:
    return isinstance(value, (list, tuple, set, frozenset))


def _scope_equal(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    return dict(left) == dict(right)


def _external_precondition_present(authorization: TrustedMutationAuthorization) -> bool:
    return any(
        value is not None
        for value in (
            authorization.external_authority_precondition,
            authorization.authority_source_revision,
            authorization.authority_observed_raw_digest,
            authorization.candidate_raw_digest,
        )
    )


def _intent_target_matches(intent: MutationIntent, target: ObjectRef) -> bool:
    logical_target = intent.logical_target
    if isinstance(logical_target, ObjectRef):
        return logical_target == target
    if isinstance(logical_target, Mapping) and "typed_target" in logical_target:
        typed_target = logical_target["typed_target"]
        if isinstance(typed_target, ObjectRef):
            return typed_target == target
        return typed_target == target.to_canonical()
    if isinstance(logical_target, str) and logical_target.startswith("ref:"):
        return logical_target == target.to_canonical()
    return True


def _intent_scope_matches(intent: MutationIntent, scope: Mapping[str, str]) -> bool:
    if isinstance(intent.mutation_scope, Mapping):
        return _scope_equal(intent.mutation_scope, scope)
    if isinstance(intent.mutation_scope, str):
        declared = scope.get("mutation_scope") or scope.get("mode")
        return declared is None or declared == intent.mutation_scope
    return False


def _approval_is_exact(
    evidence: ApprovalEvidence,
    authorization: TrustedMutationAuthorization,
) -> bool:
    if authorization.subject_expected_revision is None:
        return False
    if (
        evidence.operation != authorization.operation
        or evidence.target != authorization.target
        or evidence.expected_revision != authorization.subject_expected_revision
        or not _scope_equal(evidence.scope, authorization.mutation_scope)
    ):
        return False
    approver = resolve_principal_binding(evidence.approver, evidence.trusted_context)
    return (
        approver.trust == "trusted"
        and evidence.evidence_digest == evidence.computed_digest(approver.principal)
    )


def _decision_is_exact(
    evidence: MaterializedDecisionEvidence,
    authorization: TrustedMutationAuthorization,
    authority_engine: AuthorityEngine | None,
) -> bool:
    if authorization.subject_expected_revision is None:
        return False
    if (
        evidence.operation != authorization.operation
        or evidence.target != authorization.target
        or evidence.expected_revision != authorization.subject_expected_revision
        or not _scope_equal(evidence.scope, authorization.mutation_scope)
    ):
        return False
    if authority_engine is None:
        return True
    try:
        resolved = resolve_authority_target(authorization.target, authority_engine._resolver)
        decision = authority_engine._resolver.resolve_decision(evidence.decision_ref)
    except Exception:
        return False
    return (
        decision.subject_ref == resolved.owning_subject_ref.internal_id
        and evidence.evidence_digest == evidence.computed_digest(decision)
    )


class CapabilityLeaseIssuer:
    """Issue exact, short-lived leases from already-valid trusted authority."""

    LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER = False
    CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY = False
    CONSUMED_LEASE_REUSE_ALLOWED = False
    EXPIRED_LEASE_REUSE_ALLOWED = False
    REVOKED_LEASE_REUSE_ALLOWED = False
    UNKNOWN_OUTCOME_BLIND_LEASE_REUSE = False
    LEASE_MAY_CROSS_UNKNOWN_OUTCOME_RETRY = False

    def __init__(self, authority_engine: AuthorityEngine | None = None) -> None:
        self._authority_engine = authority_engine

    def _validate_common(
        self,
        authorization: TrustedMutationAuthorization,
        descriptor: OperationContractDescriptor,
        *,
        intent: MutationIntent | None,
        trusted_context: TrustedContext | None,
        operation: str,
        target: object,
        scope: Mapping[str, str],
        now: datetime,
    ) -> None:
        if not isinstance(authorization, TrustedMutationAuthorization):
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "typed authorization is required")
        if not isinstance(descriptor, OperationContractDescriptor):
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "operation descriptor is required")
        try:
            descriptor.validate()
        except (TypeError, ValueError) as exc:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_CONTRACT_DRIFT,
                "operation descriptor is invalid",
                details={"reason": type(exc).__name__},
            ) from exc
        if descriptor.read_write == READ_ONLY:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_MISSING,
                "a read-only descriptor cannot authorize a mutation",
            )

        effective_context = trusted_context or authorization.trusted_context
        binding = resolve_principal_binding(authorization.principal, effective_context)
        if binding.trust != "trusted" or binding.principal != authorization.principal:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_MISSING,
                "runtime-bound trusted context is required",
            )
        if operation != authorization.operation or descriptor.name != authorization.operation:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH,
                "authorization, descriptor, and request operation differ",
            )
        if _ambiguous_target(target):
            raise AuthorizationFailure(
                AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE,
                "exact typed target selection is required",
            )
        if not isinstance(target, ObjectRef) or target != authorization.target:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_TARGET_MISMATCH,
                "authorization is bound to a different typed target",
            )
        if not _scope_equal(scope, authorization.mutation_scope):
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_SCOPE_MISMATCH,
                "authorization scope cannot be widened or changed",
            )
        if descriptor.contract_hash() != authorization.contract_hash:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_CONTRACT_DRIFT,
                "descriptor contract hash differs from authorization",
            )
        if intent is not None:
            if not isinstance(intent, MutationIntent):
                raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "typed MutationIntent is required")
            if intent.operation != authorization.operation:
                raise AuthorizationFailure(
                    AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH,
                    "intent operation differs from authorization",
                )
            if intent.intent_fingerprint() != authorization.intent_fingerprint:
                raise AuthorizationFailure(
                    AuthorizationErrorCode.LEASE_INTENT_MISMATCH,
                    "intent fingerprint differs from authorization",
                )
            if _ambiguous_target(intent.logical_target):
                raise AuthorizationFailure(
                    AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE,
                    "intent target is ambiguous",
                )
            if not _intent_target_matches(intent, authorization.target):
                raise AuthorizationFailure(
                    AuthorizationErrorCode.AUTHORIZATION_TARGET_MISMATCH,
                    "intent target differs from authorization",
                )
            if not _intent_scope_matches(intent, authorization.mutation_scope):
                raise AuthorizationFailure(
                    AuthorizationErrorCode.AUTHORIZATION_SCOPE_MISMATCH,
                    "intent scope differs from authorization",
                )
        if descriptor.subject_revision_precondition and authorization.subject_expected_revision is None:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_MISSING,
                "Subject revision precondition is required",
            )
        if descriptor.external_authority_precondition and not _external_precondition_present(authorization):
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_MISSING,
                "external authority precondition is required",
            )
        if authorization.authorization_basis == "approval_evidence" or descriptor.approval_required:
            if authorization.approval_basis is None or not _approval_is_exact(
                authorization.approval_basis, authorization
            ):
                raise AuthorizationFailure(
                    AuthorizationErrorCode.APPROVAL_REQUIRED,
                    "exact ApprovalEvidence is required",
                )
        if authorization.authorization_basis == "materialized_decision_evidence" or descriptor.decision_required:
            if authorization.decision_basis is None or not _decision_is_exact(
                authorization.decision_basis, authorization, self._authority_engine
            ):
                raise AuthorizationFailure(
                    AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED,
                    "exact MaterializedDecisionEvidence is required",
                )
        if authorization.authorization_basis == "project_milestone_semantic_decision" and not (
            authorization.authorization_id or authorization.reservation_ref
        ):
            raise AuthorizationFailure(
                AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE,
                "project or milestone semantic decision basis is missing",
            )
        if authorization.issued_at is not None and now < authorization.issued_at:
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "authorization is not yet valid")
        if authorization.expires_at is not None and now >= authorization.expires_at:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_EXPIRED, "authorization validity has expired")

    def issue(
        self,
        authorization: TrustedMutationAuthorization,
        descriptor: OperationContractDescriptor,
        *,
        intent: MutationIntent | None = None,
        trusted_context: TrustedContext | None = None,
        operation: str | None = None,
        target: object = _UNSET,
        mutation_scope: Mapping[str, str] | object = _UNSET,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        lease_id: str | None = None,
        attempt_id: str | None = None,
    ) -> CapabilityLease:
        """Validate exact authority and issue one bounded lease; never writes."""
        if not isinstance(authorization, TrustedMutationAuthorization):
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "typed authorization is required")
        now = _utc("now", now or datetime.now(timezone.utc))
        requested_operation = authorization.operation if operation is None else operation
        requested_target = authorization.target if target is _UNSET else target
        requested_scope = authorization.mutation_scope if mutation_scope is _UNSET else mutation_scope
        try:
            requested_scope = normalize_scope(requested_scope)
        except (TypeError, ValueError) as exc:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORIZATION_SCOPE_MISMATCH,
                "requested mutation scope is invalid",
            ) from exc
        self._validate_common(
            authorization,
            descriptor,
            intent=intent,
            trusted_context=trusted_context,
            operation=requested_operation,
            target=requested_target,
            scope=requested_scope,
            now=now,
        )
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not (0 < ttl_seconds <= MAX_LEASE_TTL_SECONDS):
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "lease TTL is outside the bounded maximum")
        expires_at = now + timedelta(seconds=ttl_seconds)
        if authorization.expires_at is not None:
            expires_at = min(expires_at, authorization.expires_at)
        if expires_at <= now:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_EXPIRED, "lease would be expired at issuance")
        expected_revision = authorization.subject_expected_revision
        if expected_revision is None:
            expected_revision = 0
        attempt_seed = attempt_id or _digest(
            {"authorization": authorization.binding_digest(), "issued_at": now.isoformat()}
        )[:32]
        resolved_lease_id = lease_id or f"lease-{_digest({'seed': attempt_seed, 'time': now.isoformat()})[:32]}"
        return CapabilityLease(
            lease_id=resolved_lease_id,
            principal=authorization.principal,
            operation=authorization.operation,
            target=authorization.target,
            scope=authorization.mutation_scope,
            issued_at=now,
            expires_at=expires_at,
            expected_revision=expected_revision,
            contract_hash=authorization.contract_hash,
            authority_basis=(authorization.authorization_id or "m4-2-authorization",),
            approval_basis=authorization.approval_basis,
            decision_basis=authorization.decision_basis,
            intent_fingerprint=authorization.intent_fingerprint,
            external_authority_precondition=authorization.external_authority_precondition,
            authority_source_revision=authorization.authority_source_revision,
            authority_observed_raw_digest=authorization.authority_observed_raw_digest,
            candidate_raw_digest=authorization.candidate_raw_digest,
            normalized_plan_digest=authorization.normalized_plan_digest,
            authorization_basis=authorization.authorization_basis,
            reservation_ref=authorization.reservation_ref,
            candidate_identity=authorization.candidate_identity,
            attempt_id=attempt_seed,
        )

    def issue_result(self, *args: Any, **kwargs: Any) -> AuthorizationResult:
        try:
            return AuthorizationResult(lease=self.issue(*args, **kwargs))
        except AuthorizationFailure as exc:
            return AuthorizationResult(error=exc)

    authorize = issue

    def validate_lease(
        self,
        lease: CapabilityLease,
        *,
        trusted_context: TrustedContext,
        operation: str,
        target: object,
        mutation_scope: Mapping[str, str],
        contract_hash: str,
        intent_fingerprint: str,
        subject_expected_revision: int | None = None,
        external_authority_precondition: str | None = None,
        authority_source_revision: str | int | None = None,
        authority_observed_raw_digest: str | None = None,
        candidate_raw_digest: str | None = None,
        normalized_plan_digest: str | None = None,
        now: datetime | None = None,
    ) -> CapabilityLease:
        """Validate one exact lease use without consuming or executing it."""
        if not isinstance(lease, CapabilityLease):
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "typed CapabilityLease is required")
        try:
            lease.validate_structural()
        except (TypeError, ValueError) as exc:
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "lease is structurally invalid") from exc
        binding = resolve_principal_binding(lease.principal, trusted_context)
        if binding.trust != "trusted" or binding.principal != lease.principal:
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_MISSING, "trusted context is required")
        if operation != lease.operation:
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_OPERATION_MISMATCH, "lease operation mismatch")
        if _ambiguous_target(target):
            raise AuthorizationFailure(AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE, "exact target is required")
        if not isinstance(target, ObjectRef) or target != lease.target:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_TARGET_MISMATCH, "lease target mismatch")
        try:
            requested_scope = normalize_scope(mutation_scope)
        except (TypeError, ValueError) as exc:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_SCOPE_MISMATCH, "lease scope mismatch") from exc
        if not _scope_equal(requested_scope, lease.scope):
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_SCOPE_MISMATCH, "lease scope mismatch")
        if lease.contract_hash != contract_hash:
            raise AuthorizationFailure(AuthorizationErrorCode.AUTHORIZATION_CONTRACT_DRIFT, "lease contract drift")
        if lease.intent_fingerprint != intent_fingerprint:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_INTENT_MISMATCH, "lease intent mismatch")
        now = _utc("now", now or datetime.now(timezone.utc))
        if lease.is_outcome_unknown:
            raise AuthorizationFailure(
                AuthorizationErrorCode.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION,
                "unknown outcome requires reconciliation before retry",
            )
        if lease.revocation_state == LEASE_REVOKED:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_REVOKED, "lease is revoked")
        if lease.consumption_state == LEASE_CONSUMED:
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_CONSUMED, "lease is consumed")
        if lease.is_expired(now):
            raise AuthorizationFailure(AuthorizationErrorCode.LEASE_EXPIRED, "lease is expired")
        if lease.intent_fingerprint is not None and lease.expected_revision != subject_expected_revision:
            raise AuthorizationFailure(AuthorizationErrorCode.SUBJECT_REVISION_STALE, "Subject revision is stale")
        if subject_expected_revision is not None and lease.expected_revision != subject_expected_revision:
            raise AuthorizationFailure(AuthorizationErrorCode.SUBJECT_REVISION_STALE, "Subject revision is stale")
        if lease.external_authority_precondition != external_authority_precondition:
            raise AuthorizationFailure(
                AuthorizationErrorCode.AUTHORITY_PRECONDITION_STALE,
                "external authority precondition is stale",
            )
        for lease_value, request_value in (
            (lease.authority_source_revision, authority_source_revision),
            (lease.authority_observed_raw_digest, authority_observed_raw_digest),
            (lease.candidate_raw_digest, candidate_raw_digest),
            (lease.normalized_plan_digest, normalized_plan_digest),
        ):
            if lease_value != request_value:
                raise AuthorizationFailure(
                    AuthorizationErrorCode.AUTHORITY_PRECONDITION_STALE,
                    "authority precondition binding differs",
                )
        return lease


TrustedMutationAuthorizationIssuer = CapabilityLeaseIssuer


__all__ = [
    "APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE",
    "AuthorizationError",
    "AuthorizationErrorCode",
    "AuthorizationFailure",
    "AuthorizationResult",
    "CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY",
    "CapabilityLeaseIssuer",
    "DEFAULT_LEASE_TTL_SECONDS",
    "DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED",
    "LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER",
    "M4_2_DURABLE_JOURNAL_IMPLEMENTED",
    "M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED",
    "M4_2_WRITE_INGRESS_IMPLEMENTED",
    "NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN",
    "TrustedMutationAuthorizationIssuer",
    "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE",
    "LEASE_MAY_CROSS_UNKNOWN_OUTCOME_RETRY",
]
