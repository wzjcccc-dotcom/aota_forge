"""Deterministic, executor-neutral M3-B5 authority evaluation.

The engine validates trusted facts supplied by the caller.  It never selects
Subjects, infers scope, creates approval or Decision evidence, or performs a
graph write.  Record targets are mechanically resolved to their owning Subject
by the accepted B3 resolver; the Subject is the authority and revision root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Mapping

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
        if lease.contract_hash is not None and request.contract_hash != lease.contract_hash:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_OPERATION_MISMATCH)
        if request.contract_hash is not None and lease.contract_hash != request.contract_hash:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_OPERATION_MISMATCH)
        if lease.target != request.target:
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_TARGET_MISMATCH)
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
        if not scope_contains(lease.scope, requested_scope):
            return self._result(AuthorityDecision.DENY, AuthorityReason.LEASE_SCOPE_MISMATCH)

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
    "resolve_authority_target",
]
