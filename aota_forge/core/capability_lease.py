"""Executor-neutral capability lease mechanics for M3-B5.

This module defines the bounded lease value used by the authority engine.  It
does not persist leases, advance revisions, perform CAS, or consume a lease
atomically.  ``revoke`` and ``consume_for_fixture`` return isolated copies for
deterministic state-machine fixtures only; B6 owns durable lifecycle state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from aota_forge.core.context import Principal
from aota_forge.core.identity.refs import ObjectRef

MAX_LEASE_TTL_SECONDS = 300
LEASE_EXPIRY_BOUNDARY = "now_greater_or_equal_expires_at_denied"
TIME_SOURCE_INJECTABLE_FOR_TESTS = True
LEASE_EXPECTED_REVISION_FIELD_IMPLEMENTED = True
LEASE_SCOPE_EXPANSION_DENIED = True
EXPIRED_LEASE_DENIED = True
REVOKED_LEASE_DENIED = True
PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY = False
CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY = False
LEASE_ISSUED = "issued"
LEASE_VALID = "valid"
LEASE_EXPIRED = "expired"
LEASE_OUTCOME_UNKNOWN = "outcome_unknown"
LEASE_REVOKED = "revoked"
LEASE_CONSUMED = "consumed"
LEASE_ACTIVE = frozenset({LEASE_ISSUED})
LEASE_LIFECYCLE_STATES = frozenset(
    {LEASE_ISSUED, LEASE_VALID, LEASE_CONSUMED, LEASE_EXPIRED, LEASE_REVOKED}
)
_SAFE_LEASE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AUTHORIZATION_BASES = frozenset(
    {
        "trusted_scope_no_extra_approval",
        "approval_evidence",
        "materialized_decision_evidence",
        "project_milestone_semantic_decision",
    }
)


def normalize_scope(scope: Mapping[str, str] | None) -> MappingProxyType:
    """Return a bounded, immutable, deterministically ordered scope."""
    if not isinstance(scope, Mapping) or not scope:
        raise ValueError("lease scope must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for key, value in scope.items():
        if (
            not isinstance(key, str)
            or not _SAFE_LEASE_TOKEN.fullmatch(key)
            or not isinstance(value, str)
            or not value
            or len(value) > 256
        ):
            raise ValueError("lease scope must contain bounded string key/value pairs")
        normalized[key] = value
    return MappingProxyType(dict(sorted(normalized.items())))


def scope_contains(granted: Mapping[str, str], requested: Mapping[str, str]) -> bool:
    """Return whether a requested scope is a subset of a granted scope."""
    return all(granted.get(key) == value for key, value in requested.items())


def _require_timestamp(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CapabilityLease:
    """A principal-, operation-, object-, scope-, time-, and revision-bound grant."""

    lease_id: str
    principal: Principal
    operation: str
    target: ObjectRef
    scope: Mapping[str, str]
    issued_at: datetime
    expires_at: datetime
    expected_revision: int
    contract_hash: str | None = None
    authority_basis: tuple[str, ...] = ("authority_engine",)
    approval_basis: object | None = None
    decision_basis: object | None = None
    revocation_state: str = LEASE_ISSUED
    consumption_state: str = LEASE_ISSUED
    intent_fingerprint: str | None = None
    external_authority_precondition: str | None = None
    authority_source_revision: str | int | None = None
    authority_observed_raw_digest: str | None = None
    candidate_raw_digest: str | None = None
    normalized_plan_digest: str | None = None
    authorization_basis: str | None = None
    reservation_ref: str | None = None
    candidate_identity: str | None = None
    attempt_id: str | None = None
    outcome_state: str = LEASE_ISSUED
    LEASE_ID_IS_AUTHORITY = False
    CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY = False
    ATOMIC_LEASE_CONSUMPTION_IMPLEMENTED = False
    LEASE_CONSUMPTION_TRANSACTION_DEFERRED_TO_B6 = True
    REPLAY_PERSISTENCE_DEFERRED_TO_B6 = True
    IDEMPOTENCY_PERSISTENCE_DEFERRED_TO_B6 = True

    def __post_init__(self) -> None:
        if not isinstance(self.lease_id, str) or not _SAFE_LEASE_TOKEN.fullmatch(self.lease_id):
            raise ValueError("lease_id must be a bounded safe identifier")
        if not isinstance(self.principal, Principal):
            raise ValueError("lease principal must reuse the B2 Principal")
        if not isinstance(self.operation, str) or not _SAFE_LEASE_TOKEN.fullmatch(self.operation):
            raise ValueError("lease operation must be a bounded safe identifier")
        if not isinstance(self.target, ObjectRef):
            raise ValueError("lease target must be a canonical ObjectRef")
        object.__setattr__(self, "scope", normalize_scope(self.scope))
        issued_at = _require_timestamp("issued_at", self.issued_at)
        expires_at = _require_timestamp("expires_at", self.expires_at)
        if expires_at <= issued_at:
            raise ValueError("lease expires_at must be after issued_at")
        if (expires_at - issued_at).total_seconds() > MAX_LEASE_TTL_SECONDS:
            raise ValueError("lease TTL exceeds the bounded maximum")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        if isinstance(self.expected_revision, bool) or not isinstance(self.expected_revision, int):
            raise ValueError("expected_revision must be a non-negative integer")
        if self.expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if self.contract_hash is not None and (
            not isinstance(self.contract_hash, str)
            or not _SHA256.fullmatch(self.contract_hash)
        ):
            raise ValueError("contract_hash must be a SHA-256 hex digest when supplied")
        for name, value in (
            ("intent_fingerprint", self.intent_fingerprint),
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
            ("reservation_ref", self.reservation_ref),
            ("candidate_identity", self.candidate_identity),
            ("attempt_id", self.attempt_id),
        ):
            if value is not None and (not isinstance(value, str) or not _SAFE_LEASE_TOKEN.fullmatch(value)):
                raise ValueError(f"{name} must be a bounded safe identifier when supplied")
        if self.authorization_basis is not None and self.authorization_basis not in _AUTHORIZATION_BASES:
            raise ValueError("authorization_basis is not a recognized authorization basis")
        if (
            not isinstance(self.authority_basis, tuple)
            or not self.authority_basis
            or any(not isinstance(item, str) or not item for item in self.authority_basis)
        ):
            raise ValueError("authority_basis must contain bounded evidence labels")
        if self.revocation_state not in {LEASE_ISSUED, LEASE_REVOKED}:
            raise ValueError("invalid lease revocation state")
        if self.consumption_state not in {LEASE_ISSUED, LEASE_CONSUMED}:
            raise ValueError("invalid lease consumption state")
        if self.outcome_state not in {LEASE_ISSUED, LEASE_OUTCOME_UNKNOWN}:
            raise ValueError("invalid lease outcome state")
        if self.approval_basis is not None or self.decision_basis is not None:
            # Lazy imports avoid a module cycle: authority imports this schema,
            # while validation occurs only after both modules are loaded.
            from aota_forge.core.authority import ApprovalEvidence, MaterializedDecisionEvidence

            if self.approval_basis is not None and not isinstance(self.approval_basis, ApprovalEvidence):
                raise ValueError("approval_basis must be typed ApprovalEvidence")
            if self.decision_basis is not None and not isinstance(self.decision_basis, MaterializedDecisionEvidence):
                raise ValueError("decision_basis must be typed MaterializedDecisionEvidence")

    def validate_structural(self) -> None:
        """Re-run structural validation for a value crossing a trust boundary."""
        self.__post_init__()

    def is_expired(self, now: datetime) -> bool:
        return _require_timestamp("now", now) >= self.expires_at

    @property
    def subject_expected_revision(self) -> int:
        """M4-2 name for the existing M3 Subject revision binding."""
        return self.expected_revision

    @property
    def mutation_scope(self) -> Mapping[str, str]:
        """M4-2 name for the exact operation scope carried by the lease."""
        return self.scope

    @property
    def semantic_intent_identity(self) -> str | None:
        """Stable semantic identity; lease/attempt IDs remain mechanical."""
        return self.intent_fingerprint

    @property
    def is_outcome_unknown(self) -> bool:
        return self.outcome_state == LEASE_OUTCOME_UNKNOWN

    def lifecycle_state(self, now: datetime | None = None) -> str:
        """Return deterministic lifecycle state without mutating shared authority."""
        if self.revocation_state == LEASE_REVOKED:
            return LEASE_REVOKED
        if self.consumption_state == LEASE_CONSUMED:
            return LEASE_CONSUMED
        if self.outcome_state == LEASE_OUTCOME_UNKNOWN:
            return LEASE_OUTCOME_UNKNOWN
        if now is not None and self.is_expired(now):
            return LEASE_EXPIRED
        return LEASE_VALID

    def binding_dict(self) -> dict[str, Any]:
        """Return the exact authority tuple, excluding mechanical lease identity."""
        return {
            "principal": self.principal.to_audit(),
            "operation": self.operation,
            "typed_target": self.target.to_canonical(),
            "mutation_scope": dict(self.scope),
            "contract_hash": self.contract_hash,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.expected_revision,
            "external_authority_precondition": self.external_authority_precondition,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "authorization_basis": self.authorization_basis,
            "reservation_ref": self.reservation_ref,
            "candidate_identity": self.candidate_identity,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def binding_digest(self) -> str:
        """Return a deterministic digest of all mutation-authority bindings."""
        encoded = json.dumps(self.binding_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def revoke(self) -> "CapabilityLease":
        """Return a revoked fixture copy; this is not durable revocation storage."""
        return replace(self, revocation_state=LEASE_REVOKED)

    def consume_for_fixture(self) -> "CapabilityLease":
        """Return a consumed fixture copy; this is not atomic one-time consumption."""
        return replace(self, consumption_state=LEASE_CONSUMED)

    def mark_outcome_unknown(self) -> "CapabilityLease":
        """Invalidate this lease for retry after an uncertain external outcome."""
        return replace(self, outcome_state=LEASE_OUTCOME_UNKNOWN)

    def to_audit(self) -> dict[str, object]:
        """Return bounded evidence without credentials or filesystem data."""
        return {
            "lease_id": self.lease_id,
            "operation": self.operation,
            "target": self.target.to_canonical(),
            "scope": dict(self.scope),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "expected_revision": self.expected_revision,
            "contract_hash": self.contract_hash,
            "intent_fingerprint": self.intent_fingerprint,
            "authorization_basis": self.authorization_basis,
            "reservation_ref": self.reservation_ref,
            "candidate_identity": self.candidate_identity,
            "attempt_id": self.attempt_id,
            "external_authority_precondition": self.external_authority_precondition,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "revocation_state": self.revocation_state,
            "consumption_state": self.consumption_state,
            "outcome_state": self.outcome_state,
        }


__all__ = [
    "CapabilityLease",
    "CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY",
    "LEASE_ACTIVE",
    "LEASE_CONSUMED",
    "LEASE_EXPIRED",
    "LEASE_ISSUED",
    "LEASE_LIFECYCLE_STATES",
    "LEASE_OUTCOME_UNKNOWN",
    "LEASE_REVOKED",
    "LEASE_VALID",
    "LEASE_EXPIRY_BOUNDARY",
    "TIME_SOURCE_INJECTABLE_FOR_TESTS",
    "LEASE_EXPECTED_REVISION_FIELD_IMPLEMENTED",
    "LEASE_SCOPE_EXPANSION_DENIED",
    "EXPIRED_LEASE_DENIED",
    "REVOKED_LEASE_DENIED",
    "PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY",
    "MAX_LEASE_TTL_SECONDS",
    "normalize_scope",
    "scope_contains",
]
