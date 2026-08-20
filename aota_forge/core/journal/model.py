"""M4-5 journal model contract — record/schema, fields, invariants (no persistence)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.identity.refs import ObjectRef

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# Journal state vocabulary — exactly 9 states per accepted plan
class JournalState(str, Enum):
    PREPARED = "PREPARED"
    APPLYING = "APPLYING"
    FAILED_NO_EFFECT = "FAILED_NO_EFFECT"
    VERIFIED = "VERIFIED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILING = "RECONCILING"
    VERIFIED_RECOVERED = "VERIFIED_RECOVERED"
    RETRYABLE_NO_EFFECT = "RETRYABLE_NO_EFFECT"
    CONFLICT = "CONFLICT"

JOURNAL_STATE_COUNT = len(JournalState)  # 9

# Invariants: journal is not semantic authority
JOURNAL_IS_SEMANTIC_DECISION_MAKER = False
JOURNAL_CONTRACT_IMPLEMENTED = True
DURABLE_JOURNAL_PERSISTENCE_IMPLEMENTED = False

# Crash windows J1-J10 vocabulary (all distinguishable)
CRASH_WINDOWS = (
    "J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8", "J9", "J10",
)
CRASH_WINDOW_COUNT = len(CRASH_WINDOWS)  # 10
J10_SOURCE_CONTRACT_EXPLICIT = True
# J10: NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE -> RECONCILING
J10_DESCRIBED = "NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE maps deterministically to RECONCILING"

# Journal fields required for reconciliation (per D4/D3)
JOURNAL_FIELDS_REQUIRED_FOR_RECONCILIATION = (
    "candidate_raw_digest",
    "observed_raw_digest",
    "original_raw_digest",
    "normalized_plan_digest",
    "raw_source_revision",
    "subject_expected_revision",
    "journal_state",
    "operation",
    "principal",
    "typed_target",
    "correlation_id",
    "contract_hash",
    "idempotency_key",
    "intent_fingerprint",
    "authorization_basis",
    "lease_or_attempt_reference",
)

# Opaque identifier contract
OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT = 0


def _require_sha256(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be SHA-256 hex")
    return value


@dataclass(frozen=True)
class JournalRecord:
    """Durable mutation journal record contract (M4-5 owns schema only).

    Fields mirror D4 snapshot + D3 states + linkage. M4-7 will persist this;
    M4-5 defines shape and invariants only.

    Opaque IDs (journal_id, correlation_id, attempt_id) are mechanical only,
    never semantic authority (Approval != Decision, lease issuer != decision maker).
    """

    # Identity (opaque, not authority)
    journal_id: str
    correlation_id: str
    attempt_id: str  # reservation/attempt identity (at-most-one)

    # Operation binding (reuse canonical plan_init/plan_retirement only)
    operation: str
    typed_target: ObjectRef
    principal: str
    contract_hash: str
    idempotency_key: str
    intent_fingerprint: str

    # Multi-domain preconditions (distinct CAS domains)
    subject_expected_revision: int
    authority_source_revision: str | int | None  # external CAS token (opaque)
    authority_observed_raw_digest: str | None  # original_raw_digest at PREPARED time
    candidate_raw_digest: str | None
    normalized_plan_digest: str | None  # evidence only, never external CAS

    # Authorization linkage (complete M4-3 identity, not intent alone)
    authorization_reference: str | None = None
    lease_reference: str | None = None
    authorization_basis: str | None = None  # e.g., trusted_scope_no_extra_approval etc.

    # Journal protocol state
    journal_state: JournalState = JournalState.PREPARED

    # Observed outcome (populated after verify/readback)
    observed_raw_digest: str | None = None
    observed_revision: str | int | None = None
    verification_state: str | None = None  # terminal or reconciliation

    # Reconciliation classification evidence
    original_raw_digest: str | None = None  # alias for authority_observed_raw_digest at PREPARED

    # Evidence / audit (bounded)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.journal_id, str) or not _SAFE_RE.fullmatch(self.journal_id):
            raise ValueError("journal_id must be bounded safe identifier")
        if not isinstance(self.correlation_id, str) or not _SAFE_RE.fullmatch(self.correlation_id):
            raise ValueError("correlation_id must be bounded safe identifier")
        if not isinstance(self.attempt_id, str) or not _SAFE_RE.fullmatch(self.attempt_id):
            raise ValueError("attempt_id must be bounded safe identifier")
        if self.operation not in ("plan_init", "plan_retirement"):
            raise ValueError("journal operation must be plan_init or plan_retirement")
        if not isinstance(self.typed_target, ObjectRef):
            raise ValueError("typed_target must be ObjectRef")
        if not isinstance(self.principal, str) or not self.principal:
            raise ValueError("principal must be bounded non-empty")
        if not isinstance(self.contract_hash, str) or not _SHA256_RE.fullmatch(self.contract_hash):
            raise ValueError("contract_hash must be SHA-256")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("idempotency_key must be bounded")
        if not isinstance(self.intent_fingerprint, str) or not _SHA256_RE.fullmatch(self.intent_fingerprint):
            raise ValueError("intent_fingerprint must be SHA-256")
        if isinstance(self.subject_expected_revision, bool) or not isinstance(self.subject_expected_revision, int) or self.subject_expected_revision < 0:
            raise ValueError("subject_expected_revision must be non-negative int")
        _require_sha256(self.authority_observed_raw_digest, "authority_observed_raw_digest")
        _require_sha256(self.candidate_raw_digest, "candidate_raw_digest")
        _require_sha256(self.normalized_plan_digest, "normalized_plan_digest")
        _require_sha256(self.observed_raw_digest, "observed_raw_digest")
        if (
            self.authority_observed_raw_digest is not None
            and self.normalized_plan_digest is not None
            and self.authority_observed_raw_digest == self.normalized_plan_digest
        ):
            raise ValueError("raw and normalized digests are separate domains")
        if not isinstance(self.journal_state, JournalState):
            raise ValueError("journal_state must be JournalState")
        # Preserve original alias
        if self.original_raw_digest is None and self.authority_observed_raw_digest is not None:
            # allow via object.__setattr__ for frozen dataclass convenience, but we require caller to pass correctly
            pass

    def with_state(self, new_state: JournalState, **overrides) -> "JournalRecord":
        """Return a new record with updated state (copy, no mutation of authority)."""
        from dataclasses import replace
        return replace(self, journal_state=new_state, **overrides)

    def complete_external_identity(self) -> str:
        """External journal identity reusing M4-3 complete authorization-bound identity.

        Not intent_fingerprint alone. Includes operation, target, principal,
        contract_hash, intent_fingerprint, subject revision, authority source revision,
        observed digest, candidate digest, normalized digest, correlation, lease/attempt.
        """
        payload = {
            "operation": self.operation,
            "typed_target": self.typed_target.to_canonical(),
            "principal": self.principal,
            "contract_hash": self.contract_hash,
            "idempotency_key": self.idempotency_key,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.subject_expected_revision,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "correlation_id": self.correlation_id,
            "attempt_id": self.attempt_id,
            "authorization_reference": self.authorization_reference,
            "lease_reference": self.lease_reference,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal_id": self.journal_id,
            "correlation_id": self.correlation_id,
            "attempt_id": self.attempt_id,
            "operation": self.operation,
            "typed_target": self.typed_target.to_canonical(),
            "principal": self.principal,
            "contract_hash": self.contract_hash,
            "idempotency_key": self.idempotency_key,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.subject_expected_revision,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "authorization_reference": self.authorization_reference,
            "lease_reference": self.lease_reference,
            "journal_state": self.journal_state.value,
            "observed_raw_digest": self.observed_raw_digest,
            "observed_revision": self.observed_revision,
            "original_raw_digest": self.original_raw_digest or self.authority_observed_raw_digest,
        }


# Downstream consumable: M4-7 journal contract
M4_7_CONSUMABLE_JOURNAL_CONTRACT_IMPLEMENTED = True

__all__ = [
    "JournalState",
    "JOURNAL_STATE_COUNT",
    "JournalRecord",
    "JOURNAL_IS_SEMANTIC_DECISION_MAKER",
    "JOURNAL_CONTRACT_IMPLEMENTED",
    "DURABLE_JOURNAL_PERSISTENCE_IMPLEMENTED",
    "CRASH_WINDOWS",
    "CRASH_WINDOW_COUNT",
    "J10_SOURCE_CONTRACT_EXPLICIT",
    "JOURNAL_FIELDS_REQUIRED_FOR_RECONCILIATION",
    "OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT",
    "M4_7_CONSUMABLE_JOURNAL_CONTRACT_IMPLEMENTED",
]
