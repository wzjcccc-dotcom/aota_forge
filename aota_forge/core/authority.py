"""Deterministic, executor-neutral M3-B5 authority evaluation.

The engine validates trusted facts supplied by the caller.  It never selects
Subjects, infers scope, creates approval or Decision evidence, or performs a
graph write.  Record targets are mechanically resolved to their owning Subject
by the accepted B3 resolver; the Subject is the authority and revision root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

from aota_forge.core.capability_lease import (
    CapabilityLease,
    LEASE_CONSUMED,
    LEASE_REVOKED,
    normalize_scope,
    scope_contains,
)
from aota_forge.core.context import (
    Principal,
    TrustedContext,
    resolve_principal_binding,
)
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import OwningSubjectResolver
from aota_forge.core.identity.errors import ObjectRefError
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref

CAS_IMPLEMENTATION_STARTED = False
TRANSACTION_IMPLEMENTATION_STARTED = False
SHADOW_GRAPH_MATERIALIZATION_PERFORMED = False
CUTOVER_PERFORMED = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
AUTHORITY_ENGINE_RUNTIME_AUTHORITY_ACTIVE = False
CAPABILITY_LEASE_RUNTIME_AUTHORITY_ACTIVE = False
NORMAL_LIFECYCLE_AUTHORITY_TARGET = "Subject"
B5_REUSES_B2_PRINCIPAL = True
LEASE_PRINCIPAL_MISMATCH_DENIED = True
LEASE_OPERATION_MISMATCH_DENIED = True
LEASE_TARGET_MISMATCH_DENIED = True
STALE_EXPECTED_REVISION_DENIED_OR_BLOCKED = True
FOLLOWUP_REQUIRES_MATERIALIZED_DECISION = True
TRUSTED_MUTATION_AUTHORIZATION_IS_SEMANTIC_DECISION = False
DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED = True
APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE = False
MODEL_MAY_SELF_ASSERT_TRUSTED_AUTHORIZATION = False
NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN = False

_SAFE_AUTHORIZATION_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
AUTHORIZATION_BASIS_VALUES = frozenset(
    {
        "trusted_scope_no_extra_approval",
        "approval_evidence",
        "materialized_decision_evidence",
        "project_milestone_semantic_decision",
    }
)
FOLLOWUP_DECISION_MISMATCH_DENIED = True


class AuthorityDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    BLOCKED = "BLOCKED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class AuthorityReason(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    PRINCIPAL_REQUIRED = "PRINCIPAL_REQUIRED"
    PRINCIPAL_UNTRUSTED = "PRINCIPAL_UNTRUSTED"
    TARGET_REQUIRED = "TARGET_REQUIRED"
    TARGET_KIND_INVALID = "TARGET_KIND_INVALID"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    OWNING_SUBJECT_MISMATCH = "OWNING_SUBJECT_MISMATCH"
    LEASE_REQUIRED = "LEASE_REQUIRED"
    LEASE_INVALID = "LEASE_INVALID"
    LEASE_PRINCIPAL_MISMATCH = "LEASE_PRINCIPAL_MISMATCH"
    LEASE_OPERATION_MISMATCH = "LEASE_OPERATION_MISMATCH"
    LEASE_TARGET_MISMATCH = "LEASE_TARGET_MISMATCH"
    LEASE_SCOPE_MISMATCH = "LEASE_SCOPE_MISMATCH"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_REVOKED = "LEASE_REVOKED"
    LEASE_REPLAY_DENIED = "LEASE_REPLAY_DENIED"
    CURRENT_REVISION_REQUIRED = "CURRENT_REVISION_REQUIRED"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    SCOPE_REQUIRED = "SCOPE_REQUIRED"
    TRUSTED_TIME_REQUIRED = "TRUSTED_TIME_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    AUTHORIZATION_SCOPE_MISMATCH = "AUTHORIZATION_SCOPE_MISMATCH"
    AUTHORIZATION_TARGET_MISMATCH = "AUTHORIZATION_TARGET_MISMATCH"
    AUTHORIZATION_OPERATION_MISMATCH = "AUTHORIZATION_OPERATION_MISMATCH"
    AUTHORIZATION_CONTRACT_DRIFT = "AUTHORIZATION_CONTRACT_DRIFT"
    MATERIALIZED_DECISION_REQUIRED = "MATERIALIZED_DECISION_REQUIRED"
    SUBJECT_REVISION_STALE = "SUBJECT_REVISION_STALE"
    AUTHORITY_PRECONDITION_STALE = "AUTHORITY_PRECONDITION_STALE"
    LEASE_CONSUMED = "LEASE_CONSUMED"
    LEASE_INTENT_MISMATCH = "LEASE_INTENT_MISMATCH"
    OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION = "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION"


@dataclass(frozen=True)
class AuthorityResult:
    decision: AuthorityDecision
    reason_code: AuthorityReason
    evidence_refs: tuple[str, ...] = ()
    detail: tuple[tuple[str, str], ...] = ()

    def to_audit(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "evidence_refs": list(self.evidence_refs),
            "detail": {key: value for key, value in self.detail},
        }


@dataclass(frozen=True)
class AuthorityTarget:
    requested_ref: ObjectRef
    owning_subject_ref: ObjectRef
    subject: records.Subject


class AuthorityTargetError(ValueError):
    """The typed target kind cannot be a normal lifecycle authority target."""


def _canonical_scope(scope: Mapping[str, str]) -> dict[str, str]:
    return dict(normalize_scope(scope))


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalEvidence:
    """Mechanically bound approval evidence; trust comes from B2 context."""

    approver: object
    trusted_context: TrustedContext | None
    operation: str
    target: ObjectRef
    expected_revision: int
    scope: Mapping[str, str]
    evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation:
            raise ValueError("approval operation is required")
        if not isinstance(self.target, ObjectRef):
            raise ValueError("approval target must be an ObjectRef")
        if isinstance(self.expected_revision, bool) or not isinstance(self.expected_revision, int):
            raise ValueError("approval expected_revision must be an integer")
        object.__setattr__(self, "scope", normalize_scope(self.scope))
        if not isinstance(self.evidence_digest, str) or len(self.evidence_digest) != 64:
            raise ValueError("approval evidence_digest must be a SHA-256 hex digest")

    def computed_digest(self, principal: Principal) -> str:
        return _digest(
            {
                "approver": principal.to_audit(),
                "operation": self.operation,
                "target": self.target.to_canonical(),
                "expected_revision": self.expected_revision,
                "scope": dict(self.scope),
            }
        )


@dataclass(frozen=True)
class MaterializedDecisionEvidence:
    """Exact durable Decision plus its mechanical followup binding facts."""

    decision_ref: ObjectRef
    operation: str
    target: ObjectRef
    expected_revision: int
    scope: Mapping[str, str]
    evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision_ref, ObjectRef) or self.decision_ref.object_kind != IdKind.DECISION:
            raise ValueError("decision evidence requires a Decision ObjectRef")
        if not isinstance(self.operation, str) or not self.operation:
            raise ValueError("decision evidence operation is required")
        if not isinstance(self.target, ObjectRef):
            raise ValueError("decision evidence target must be an ObjectRef")
        if isinstance(self.expected_revision, bool) or not isinstance(self.expected_revision, int):
            raise ValueError("decision evidence expected_revision must be an integer")
        object.__setattr__(self, "scope", normalize_scope(self.scope))
        if not isinstance(self.evidence_digest, str) or len(self.evidence_digest) != 64:
            raise ValueError("decision evidence_digest must be a SHA-256 hex digest")

    @classmethod
    def from_decision(
        cls,
        decision: records.Decision,
        *,
        operation: str,
        target: ObjectRef,
        expected_revision: int,
        scope: Mapping[str, str],
    ) -> "MaterializedDecisionEvidence":
        decision_ref = make_object_ref(IdKind.DECISION, decision.decision_id)
        normalized_scope = _canonical_scope(scope)
        digest = _digest(
            {
                "decision": decision.canonical_fields(),
                "operation": operation,
                "target": target.to_canonical(),
                "expected_revision": expected_revision,
                "scope": normalized_scope,
            }
        )
        return cls(decision_ref, operation, target, expected_revision, normalized_scope, digest)

    def computed_digest(self, decision: records.Decision) -> str:
        return _digest(
            {
                "decision": decision.canonical_fields(),
                "operation": self.operation,
                "target": self.target.to_canonical(),
                "expected_revision": self.expected_revision,
                "scope": dict(self.scope),
            }
        )


@dataclass(frozen=True)
class TrustedMutationAuthorization:
    """Already-decided mutation authority, never a semantic decision engine.

    This value is durable evidence for an exact semantic decision.  A runtime
    bound ``TrustedContext`` is retained separately from the canonical
    representation so a model-supplied ``Principal`` cannot promote itself to
    trusted authority.
    """

    principal: Principal
    operation: str
    target: ObjectRef
    mutation_scope: Mapping[str, str]
    contract_hash: str
    intent_fingerprint: str
    subject_expected_revision: int | None = None
    external_authority_precondition: str | None = None
    authority_source_revision: str | int | None = None
    authority_observed_raw_digest: str | None = None
    candidate_raw_digest: str | None = None
    normalized_plan_digest: str | None = None
    authorization_basis: str = "trusted_scope_no_extra_approval"
    approval_basis: ApprovalEvidence | None = None
    decision_basis: MaterializedDecisionEvidence | None = None
    reservation_ref: str | None = None
    candidate_identity: str | None = None
    authorization_id: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    trusted_context: TrustedContext | None = field(default=None, repr=False, compare=False)

    TRUSTED_MUTATION_AUTHORIZATION_IS_SEMANTIC_DECISION = False
    DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED = True
    APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE = False
    MODEL_MAY_SELF_ASSERT_TRUSTED_PRINCIPAL = False
    MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE = False

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal):
            raise ValueError("trusted authorization principal must be a Principal")
        if not isinstance(self.operation, str) or not _SAFE_AUTHORIZATION_TOKEN.fullmatch(self.operation):
            raise ValueError("trusted authorization operation must be a bounded safe identifier")
        if not isinstance(self.target, ObjectRef):
            raise ValueError("trusted authorization target must be an ObjectRef")
        object.__setattr__(self, "mutation_scope", normalize_scope(self.mutation_scope))
        if not isinstance(self.contract_hash, str) or not _SHA256.fullmatch(self.contract_hash):
            raise ValueError("trusted authorization contract_hash must be a SHA-256 hex digest")
        if not isinstance(self.intent_fingerprint, str) or not _SHA256.fullmatch(self.intent_fingerprint):
            raise ValueError("trusted authorization intent_fingerprint must be a SHA-256 hex digest")
        if self.subject_expected_revision is not None and (
            isinstance(self.subject_expected_revision, bool)
            or not isinstance(self.subject_expected_revision, int)
            or self.subject_expected_revision < 0
        ):
            raise ValueError("subject_expected_revision must be a non-negative integer when supplied")
        if self.external_authority_precondition is not None and (
            not isinstance(self.external_authority_precondition, str)
            or not self.external_authority_precondition
            or len(self.external_authority_precondition) > 4096
        ):
            raise ValueError("external_authority_precondition must be a bounded string when supplied")
        if self.authority_source_revision is not None and (
            isinstance(self.authority_source_revision, bool)
            or not isinstance(self.authority_source_revision, (str, int))
            or (isinstance(self.authority_source_revision, str) and not self.authority_source_revision)
        ):
            raise ValueError("authority_source_revision must be a bounded string or integer when supplied")
        for name, value in (
            ("authority_observed_raw_digest", self.authority_observed_raw_digest),
            ("candidate_raw_digest", self.candidate_raw_digest),
            ("normalized_plan_digest", self.normalized_plan_digest),
        ):
            if value is not None and (not isinstance(value, str) or not _SHA256.fullmatch(value)):
                raise ValueError(f"{name} must be a SHA-256 hex digest when supplied")
        if (
            self.authority_observed_raw_digest is not None
            and self.normalized_plan_digest is not None
            and self.authority_observed_raw_digest == self.normalized_plan_digest
        ):
            raise ValueError("raw source and normalized plan digests are separate domains")
        if self.authorization_basis not in AUTHORIZATION_BASIS_VALUES:
            raise ValueError("authorization_basis is not a recognized authorization basis")
        if self.approval_basis is not None and not isinstance(self.approval_basis, ApprovalEvidence):
            raise ValueError("approval_basis must be typed ApprovalEvidence")
        if self.decision_basis is not None and not isinstance(self.decision_basis, MaterializedDecisionEvidence):
            raise ValueError("decision_basis must be typed MaterializedDecisionEvidence")
        for name, value in (
            ("reservation_ref", self.reservation_ref),
            ("candidate_identity", self.candidate_identity),
            ("authorization_id", self.authorization_id),
        ):
            if value is not None and (not isinstance(value, str) or not _SAFE_AUTHORIZATION_TOKEN.fullmatch(value)):
                raise ValueError(f"{name} must be a bounded safe identifier when supplied")
        if self.issued_at is not None:
            issued_at = _require_authorization_timestamp("issued_at", self.issued_at)
            object.__setattr__(self, "issued_at", issued_at)
        if self.expires_at is not None:
            expires_at = _require_authorization_timestamp("expires_at", self.expires_at)
            object.__setattr__(self, "expires_at", expires_at)
        if self.issued_at is not None and self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("trusted authorization expires_at must be after issued_at")

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return the deterministic exact binding representation."""
        return {
            "principal": self.principal.to_audit(),
            "operation": self.operation,
            "typed_target": self.target.to_canonical(),
            "mutation_scope": dict(self.mutation_scope),
            "contract_hash": self.contract_hash,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.subject_expected_revision,
            "external_authority_precondition": self.external_authority_precondition,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "authorization_basis": self.authorization_basis,
            "approval_basis": _approval_canonical(self.approval_basis),
            "decision_basis": _decision_canonical(self.decision_basis),
            "reservation_ref": self.reservation_ref,
            "candidate_identity": self.candidate_identity,
            "authorization_id": self.authorization_id,
            "issued_at": self.issued_at.isoformat() if self.issued_at is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }

    @property
    def typed_target(self) -> ObjectRef:
        return self.target

    @property
    def external_authority_precondition_digest_or_reference(self) -> str | None:
        return self.external_authority_precondition

    @property
    def exact_candidate_or_exact_mutation_identity(self) -> str | None:
        return self.candidate_identity

    @property
    def materialized_decision_basis(self) -> MaterializedDecisionEvidence | None:
        return self.decision_basis

    @property
    def issuance_correlation_or_reservation_reference(self) -> str | None:
        return self.reservation_ref or self.authorization_id

    def to_dict(self) -> dict[str, Any]:
        return self.to_canonical_dict()

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def binding_digest(self) -> str:
        return _digest(self.to_canonical_dict())

    def authorization_fingerprint(self) -> str:
        return self.binding_digest()

    def to_audit(self) -> dict[str, Any]:
        """Return bounded authority evidence without trusted-context tokens."""
        return {
            "authorization_fingerprint": self.authorization_fingerprint(),
            "operation": self.operation,
            "typed_target": self.target.to_canonical(),
            "mutation_scope": dict(self.mutation_scope),
            "contract_hash": self.contract_hash,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.subject_expected_revision,
            "authorization_basis": self.authorization_basis,
            "reservation_ref": self.reservation_ref,
            "candidate_identity": self.candidate_identity,
        }


def _require_authorization_timestamp(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _approval_canonical(evidence: ApprovalEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    approver = evidence.approver.to_audit() if isinstance(evidence.approver, Principal) else None
    return {
        "approver": approver,
        "operation": evidence.operation,
        "target": evidence.target.to_canonical(),
        "expected_revision": evidence.expected_revision,
        "scope": dict(evidence.scope),
        "evidence_digest": evidence.evidence_digest,
    }


def _decision_canonical(evidence: MaterializedDecisionEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "decision_ref": evidence.decision_ref.to_canonical(),
        "operation": evidence.operation,
        "target": evidence.target.to_canonical(),
        "expected_revision": evidence.expected_revision,
        "scope": dict(evidence.scope),
        "evidence_digest": evidence.evidence_digest,
    }


@dataclass(frozen=True)
class AuthorityRequest:
    principal: object = None
    trusted_context: TrustedContext | None = None
    operation: str = ""
    target: ObjectRef | None = None
    owning_subject: ObjectRef | None = None
    requested_scope: Mapping[str, str] | None = None
    lease: CapabilityLease | None = None
    current_revision: int | None = None
    trusted_time: datetime | None = None
    contract_hash: str | None = None
    approval_required: bool = False
    approval: ApprovalEvidence | None = None
    decision: MaterializedDecisionEvidence | None = None
    authorization: TrustedMutationAuthorization | None = None
    intent_fingerprint: str | None = None
    external_authority_precondition: str | None = None
    candidate_identity: str | None = None
    reservation_ref: str | None = None
    authority_source_revision: str | int | None = None
    authority_observed_raw_digest: str | None = None
    candidate_raw_digest: str | None = None
    normalized_plan_digest: str | None = None


def resolve_authority_target(target: ObjectRef, resolver: OwningSubjectResolver) -> AuthorityTarget:
    """Resolve a typed target to its owning Subject without selection heuristics."""
    if not isinstance(target, ObjectRef):
        raise ValueError("authority target must be an ObjectRef")
    if target.object_kind == IdKind.SUBJECT:
        subject = resolver.resolve_subject(target)
    elif target.object_kind == IdKind.EXECUTION:
        subject = resolver.owning_subject_of_execution(target)
    elif target.object_kind == IdKind.COMPLETION:
        subject = resolver.owning_subject_of_completion(target)
    elif target.object_kind == IdKind.DECISION:
        subject = resolver.owning_subject_of_decision(target)
    else:
        raise AuthorityTargetError("target kind has no lifecycle Subject authority root")
    subject_ref = make_object_ref(IdKind.SUBJECT, subject.subject_id)
    return AuthorityTarget(target, subject_ref, subject)


class AuthorityEngine:
    """Pure deterministic authority evaluator over a read-only graph resolver."""

    AUTHORITY_ENGINE_IS_SEMANTIC_REASONER = False
    AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING = False
    AUTHORITY_ENGINE_SELECTS_SUBJECT = False
    AUTHORITY_ENGINE_INVENTS_SCOPE = False
    AUTHORITY_ENGINE_INVENTS_DECISION = False
    AUTHORITY_ENGINE_CREATES_APPROVAL = False
    AUTHORITY_ENGINE_CREATES_DECISION = False
    GRAPH_MODULE_PERFORMS_AUTHORITY_DECISION = False
    IDENTITY_MODULE_PERFORMS_AUTHORITY_DECISION = False
    SUBJECT_ROOTED_AUTHORITY_IMPLEMENTED = True
    RECORD_TARGET_RESOLVES_TO_OWNING_SUBJECT = True
    EXECUTION_IS_INDEPENDENT_AUTHORITY_ROOT = False
    COMPLETION_IS_INDEPENDENT_AUTHORITY_ROOT = False
    DECISION_IS_INDEPENDENT_AUTHORITY_ROOT = False

    def __init__(self, resolver: OwningSubjectResolver) -> None:
        self._resolver = resolver

    def validate_materialized_decision_evidence(
        self,
        evidence: MaterializedDecisionEvidence,
        *,
        operation: str,
        target: ObjectRef,
        expected_revision: int,
        scope: Mapping[str, str],
    ) -> bool:
        """Validate exact Decision evidence through the trusted graph resolver."""
        if not isinstance(evidence, MaterializedDecisionEvidence):
            return False
        try:
            normalized_scope = _canonical_scope(scope)
            resolved = resolve_authority_target(target, self._resolver)
            decision = self._resolver.resolve_decision(evidence.decision_ref)
        except (LookupError, ObjectRefError, TypeError, ValueError):
            return False
        if not isinstance(decision, records.Decision):
            return False
        try:
            return (
                evidence.operation == operation
                and evidence.target == target
                and evidence.expected_revision == expected_revision
                and dict(evidence.scope) == normalized_scope
                and evidence.decision_ref.internal_id == decision.decision_id
                and decision.subject_ref == resolved.owning_subject_ref.internal_id
                and evidence.evidence_digest == evidence.computed_digest(decision)
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _result(
        decision: AuthorityDecision,
        reason: AuthorityReason,
        *evidence_refs: str,
        detail: tuple[tuple[str, str], ...] = (),
    ) -> AuthorityResult:
        return AuthorityResult(decision, reason, tuple(evidence_refs), detail)

    def evaluate(self, request: AuthorityRequest) -> AuthorityResult:
        if not isinstance(request.operation, str) or not request.operation:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_OPERATION_MISMATCH)
        if request.target is None:
            return self._result(AuthorityDecision.BLOCKED, AuthorityReason.TARGET_REQUIRED)
        if request.requested_scope is None:
            return self._result(AuthorityDecision.BLOCKED, AuthorityReason.SCOPE_REQUIRED)
        try:
            requested_scope = normalize_scope(request.requested_scope)
        except (TypeError, ValueError):
            return self._result(AuthorityDecision.DENY, AuthorityReason.SCOPE_REQUIRED)

        binding = resolve_principal_binding(request.principal, request.trusted_context)
        if binding.trust != "trusted" or not isinstance(binding.principal, Principal):
            reason = (
                AuthorityReason.PRINCIPAL_REQUIRED
                if request.principal is None and request.trusted_context is None
                else AuthorityReason.PRINCIPAL_UNTRUSTED
            )
            return self._result(AuthorityDecision.DENY, reason)
        principal = binding.principal

        authorization = request.authorization
        if authorization is not None:
            if not isinstance(authorization, TrustedMutationAuthorization):
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_MISSING)
            authorization_binding = resolve_principal_binding(
                authorization.principal,
                authorization.trusted_context or request.trusted_context,
            )
            if authorization_binding.trust != "trusted" or authorization_binding.principal != principal:
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_MISSING)
            if authorization.operation != request.operation:
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_OPERATION_MISMATCH)
            if authorization.target != request.target:
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_TARGET_MISMATCH)
            if dict(authorization.mutation_scope) != dict(requested_scope):
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_SCOPE_MISMATCH)
            if request.contract_hash != authorization.contract_hash:
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_CONTRACT_DRIFT)
            if (
                request.intent_fingerprint is not None
                and request.intent_fingerprint != authorization.intent_fingerprint
            ):
                return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_INTENT_MISMATCH)
            if (
                request.external_authority_precondition is not None
                and request.external_authority_precondition != authorization.external_authority_precondition
            ):
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORITY_PRECONDITION_STALE)
            for authorization_value, request_value in (
                (authorization.authority_source_revision, request.authority_source_revision),
                (authorization.authority_observed_raw_digest, request.authority_observed_raw_digest),
                (authorization.candidate_raw_digest, request.candidate_raw_digest),
                (authorization.normalized_plan_digest, request.normalized_plan_digest),
            ):
                if authorization_value is not None and authorization_value != request_value:
                    return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORITY_PRECONDITION_STALE)
            if (
                authorization.subject_expected_revision is not None
                and request.current_revision is not None
                and authorization.subject_expected_revision != request.current_revision
            ):
                return self._result(AuthorityDecision.DENY, AuthorityReason.SUBJECT_REVISION_STALE)
            if authorization.expires_at is not None:
                if request.trusted_time is None:
                    return self._result(AuthorityDecision.BLOCKED, AuthorityReason.TRUSTED_TIME_REQUIRED)
                if _require_authorization_timestamp("trusted_time", request.trusted_time) >= authorization.expires_at:
                    return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORIZATION_MISSING)

        lease = request.lease
        if lease is None:
            return self._result(AuthorityDecision.BLOCKED, AuthorityReason.LEASE_REQUIRED)
        try:
            lease.validate_structural()
        except (TypeError, ValueError):
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_INVALID)
        if lease.principal != principal:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_PRINCIPAL_MISMATCH)
        if lease.operation != request.operation:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_OPERATION_MISMATCH)
        if lease.intent_fingerprint is not None and lease.intent_fingerprint != request.intent_fingerprint:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_INTENT_MISMATCH)
        if (
            lease.external_authority_precondition is not None
            and lease.external_authority_precondition != request.external_authority_precondition
        ):
            return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORITY_PRECONDITION_STALE)
        for lease_value, request_value in (
            (lease.authority_source_revision, request.authority_source_revision),
            (lease.authority_observed_raw_digest, request.authority_observed_raw_digest),
            (lease.candidate_raw_digest, request.candidate_raw_digest),
            (lease.normalized_plan_digest, request.normalized_plan_digest),
        ):
            if lease_value is not None and lease_value != request_value:
                return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORITY_PRECONDITION_STALE)
        if lease.contract_hash is not None and request.contract_hash != lease.contract_hash:
            reason = (
                AuthorityReason.AUTHORIZATION_CONTRACT_DRIFT
                if authorization is not None
                else AuthorityReason.LEASE_OPERATION_MISMATCH
            )
            return self._result(AuthorityDecision.DENY, reason)
        if request.contract_hash is not None and lease.contract_hash != request.contract_hash:
            reason = (
                AuthorityReason.AUTHORIZATION_CONTRACT_DRIFT
                if authorization is not None
                else AuthorityReason.LEASE_OPERATION_MISMATCH
            )
            return self._result(AuthorityDecision.DENY, reason)
        if lease.target != request.target:
            reason = (
                AuthorityReason.AUTHORIZATION_TARGET_MISMATCH
                if authorization is not None
                else AuthorityReason.LEASE_TARGET_MISMATCH
            )
            return self._result(AuthorityDecision.DENY, reason)
        if authorization is not None and lease.intent_fingerprint != authorization.intent_fingerprint:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_INTENT_MISMATCH)
        if (
            authorization is not None
            and lease.external_authority_precondition != authorization.external_authority_precondition
        ):
            return self._result(AuthorityDecision.DENY, AuthorityReason.AUTHORITY_PRECONDITION_STALE)
        if lease.is_outcome_unknown:
            return self._result(
                AuthorityDecision.DENY,
                AuthorityReason.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION,
                lease.lease_id,
            )
        if lease.revocation_state == LEASE_REVOKED:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_REVOKED, lease.lease_id)
        if lease.consumption_state == LEASE_CONSUMED:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_REPLAY_DENIED, lease.lease_id)
        if request.trusted_time is None:
            return self._result(AuthorityDecision.BLOCKED, AuthorityReason.TRUSTED_TIME_REQUIRED)
        try:
            if lease.is_expired(request.trusted_time):
                return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_EXPIRED, lease.lease_id)
        except (TypeError, ValueError):
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_INVALID)
        scope_matches = (
            dict(lease.scope) == dict(requested_scope)
            if lease.intent_fingerprint is not None
            else scope_contains(lease.scope, requested_scope)
        )
        if not scope_matches:
            reason = (
                AuthorityReason.AUTHORIZATION_SCOPE_MISMATCH
                if authorization is not None
                else AuthorityReason.LEASE_SCOPE_MISMATCH
            )
            return self._result(AuthorityDecision.DENY, reason)

        try:
            resolved = resolve_authority_target(request.target, self._resolver)
        except AuthorityTargetError:
            return self._result(AuthorityDecision.DENY, AuthorityReason.TARGET_KIND_INVALID)
        except (LookupError, ObjectRefError, TypeError, ValueError):
            return self._result(AuthorityDecision.DENY, AuthorityReason.TARGET_NOT_FOUND)
        if request.owning_subject is not None:
            if (
                not isinstance(request.owning_subject, ObjectRef)
                or request.owning_subject.object_kind != IdKind.SUBJECT
                or request.owning_subject != resolved.owning_subject_ref
            ):
                return self._result(AuthorityDecision.DENY, AuthorityReason.OWNING_SUBJECT_MISMATCH)

        if request.current_revision is None:
            return self._result(AuthorityDecision.BLOCKED, AuthorityReason.CURRENT_REVISION_REQUIRED)
        if isinstance(request.current_revision, bool) or not isinstance(request.current_revision, int):
            return self._result(AuthorityDecision.DENY, AuthorityReason.REVISION_MISMATCH)
        if lease.expected_revision != request.current_revision:
            return self._result(AuthorityDecision.DENY, AuthorityReason.REVISION_MISMATCH)

        approval = request.approval
        if approval is None and lease.approval_basis is not None:
            approval = lease.approval_basis if isinstance(lease.approval_basis, ApprovalEvidence) else None
        if request.approval_required:
            if approval is None:
                return self._result(AuthorityDecision.NEEDS_APPROVAL, AuthorityReason.APPROVAL_REQUIRED)
            if not self._approval_matches(approval, request, principal, requested_scope):
                return self._result(AuthorityDecision.DENY, AuthorityReason.APPROVAL_MISMATCH)

        if request.operation == "create_followup_subject":
            if lease.decision_basis is not None and request.decision is not None and lease.decision_basis != request.decision:
                return self._result(AuthorityDecision.DENY, AuthorityReason.DECISION_MISMATCH)
            decision = request.decision or lease.decision_basis
            if decision is None:
                return self._result(AuthorityDecision.DENY, AuthorityReason.DECISION_REQUIRED)
            if not self._decision_matches(decision, request, resolved, requested_scope):
                return self._result(AuthorityDecision.DENY, AuthorityReason.DECISION_MISMATCH)

        return self._result(AuthorityDecision.ALLOW, AuthorityReason.AUTHORIZED, lease.lease_id)

    def _approval_matches(
        self,
        approval: ApprovalEvidence,
        request: AuthorityRequest,
        principal: Principal,
        requested_scope: Mapping[str, str],
    ) -> bool:
        if approval.operation != request.operation or approval.target != request.target:
            return False
        if approval.expected_revision != request.current_revision or dict(approval.scope) != dict(requested_scope):
            return False
        approver = resolve_principal_binding(approval.approver, approval.trusted_context)
        if approver.trust != "trusted" or not isinstance(approver.principal, Principal):
            return False
        return approval.evidence_digest == approval.computed_digest(approver.principal)

    def _decision_matches(
        self,
        evidence: MaterializedDecisionEvidence,
        request: AuthorityRequest,
        resolved: AuthorityTarget,
        requested_scope: Mapping[str, str],
    ) -> bool:
        if (
            evidence.operation != request.operation
            or evidence.target != request.target
            or evidence.expected_revision != request.current_revision
            or dict(evidence.scope) != dict(requested_scope)
        ):
            return False
        try:
            decision = self._resolver.resolve_decision(evidence.decision_ref)
        except (LookupError, ObjectRefError, TypeError, ValueError):
            return False
        if decision.subject_ref != resolved.owning_subject_ref.internal_id:
            return False
        return evidence.evidence_digest == evidence.computed_digest(decision)


__all__ = [
    "ApprovalEvidence",
    "AuthorityDecision",
    "AuthorityEngine",
    "AuthorityReason",
    "AuthorityRequest",
    "AuthorityResult",
    "AuthorityTarget",
    "AuthorityTargetError",
    "MaterializedDecisionEvidence",
    "TrustedMutationAuthorization",
    "APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE",
    "AUTHORIZATION_BASIS_VALUES",
    "DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED",
    "MODEL_MAY_SELF_ASSERT_TRUSTED_AUTHORIZATION",
    "NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN",
    "TRUSTED_MUTATION_AUTHORIZATION_IS_SEMANTIC_DECISION",
    "resolve_authority_target",
]
