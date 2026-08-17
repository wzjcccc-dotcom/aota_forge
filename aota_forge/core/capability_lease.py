"""Executor-neutral capability lease mechanics for M3-B5.

This module defines the bounded lease value used by the authority engine.  It
does not persist leases, advance revisions, perform CAS, or consume a lease
atomically.  ``revoke`` and ``consume_for_fixture`` return isolated copies for
deterministic state-machine fixtures only; B6 owns durable lifecycle state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from types import MappingProxyType
from typing import Mapping

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
LEASE_ISSUED = "issued"
LEASE_REVOKED = "revoked"
LEASE_CONSUMED = "consumed"
LEASE_ACTIVE = frozenset({LEASE_ISSUED})
_SAFE_LEASE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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
    LEASE_ID_IS_AUTHORITY = False
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
            or not re.fullmatch(r"[0-9a-f]{64}", self.contract_hash)
        ):
            raise ValueError("contract_hash must be a SHA-256 hex digest when supplied")
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

    def revoke(self) -> "CapabilityLease":
        """Return a revoked fixture copy; this is not durable revocation storage."""
        return replace(self, revocation_state=LEASE_REVOKED)

    def consume_for_fixture(self) -> "CapabilityLease":
        """Return a consumed fixture copy; this is not atomic one-time consumption."""
        return replace(self, consumption_state=LEASE_CONSUMED)

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
            "revocation_state": self.revocation_state,
            "consumption_state": self.consumption_state,
        }


__all__ = [
    "CapabilityLease",
    "LEASE_ACTIVE",
    "LEASE_CONSUMED",
    "LEASE_ISSUED",
    "LEASE_REVOKED",
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
