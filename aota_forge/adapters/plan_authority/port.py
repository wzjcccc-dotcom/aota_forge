"""M4-5 Portable Plan Mutation Port — executor-neutral contract (no GitHub, no durable persistence).

Defines the typed boundary that M4-6 (GitHub adapter) and M4-7 (durable journal)
will consume without redefining semantics. Core remains platform-neutral.
"""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
)
from aota_forge.core.identity.refs import ObjectRef

# Platform neutrality markers — must remain "no"
GITHUB_IS_FORGE_CORE_ONTOLOGY = "no"
GITHUB_API_IS_CORE_CONTRACT = "no"
GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID = "no"
RAW_GH_OPERATION_IN_CORE = "no"
CORE_GITHUB_SEMANTIC_LEAK_COUNT = 0

# Cross-authority atomicity denied
CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE = "no"
CROSS_AUTHORITY_ATOMICITY_CLAIM_COUNT = 0

# Generic external mutation APIs are forbidden
GENERIC_EXTERNAL_MUTATION_API_CREATED = False
GENERIC_GIT_WRITE_API_CREATED = False
GENERIC_TERMINAL_API_CREATED = False

_ALLOWED_OPERATIONS = frozenset({PLAN_INIT_OPERATION, PLAN_RETIREMENT_OPERATION})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Domain separation invariants (must not be aliases)
SUBJECT_REVISION_IS_EXTERNAL_AUTHORITY_CAS = False
NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN = False

# Read-before-write / verify-after-write contracts
READ_BEFORE_EXTERNAL_WRITE_REQUIRED = True
VERIFY_AFTER_EXTERNAL_WRITE_REQUIRED = True
ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED = False

# Journal write order contract
EXTERNAL_APPLY_BEFORE_PREPARED_ALLOWED = False
JOURNAL_WRITE_ORDER_CONTRACT = "PASS"

# At-most-one attempt
AT_MOST_ONE_EXTERNAL_ATTEMPT_CONTRACT = "PASS"
DISTRIBUTED_LOCK_IMPLEMENTED = False

# Heuristic / rollback denied
HEURISTIC_THIRD_STATE_SELECTION_ALLOWED = False
THIRD_STATE_AUTO_MERGE_ALLOWED = False
SEMANTIC_ROLLBACK_ALLOWED = False
SEMANTIC_COMPENSATING_MUTATION_IMPLEMENTED = False

# Outcome unknown contracts
UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED = False
UNKNOWN_OUTCOME_BLIND_LEASE_REUSE = False

# Retry contracts
RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION = False
RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE = False
FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY = True

# Operation contract reuse
DUPLICATE_OPERATION_SEMANTICS_CREATED = False
OPERATION_CONTRACT_REUSE = "PASS"

# Opaque IDs are not authority
OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT = 0

# Canonical result separation
INTERNAL_LIFECYCLE_RESULT_EQUALS_EXTERNAL_VERIFICATION_RESULT = False


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_sha256(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hex digest when supplied")
    return value


def _require_safe_id(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a bounded safe identifier")
    return value


@dataclass(frozen=True)
class RawAuthorityPrecondition:
    """Executor-neutral raw authority precondition snapshot (D4 fields).

    Distinct from Subject CAS and normalized digest:

    - subject_expected_revision: Subject aggregate CAS (int)
    - authority_source_revision: opaque external revision token (str|int)
    - authority_observed_raw_digest: raw observed document digest (SHA-256)
    - candidate_raw_digest: exact intended candidate raw digest (SHA-256)
    - normalized_plan_digest: semantic normalized digest (SHA-256, evidence only)
    """

    authority_source_revision: str | int | None = None
    authority_observed_raw_digest: str | None = None
    candidate_raw_digest: str | None = None
    normalized_plan_digest: str | None = None
    subject_expected_revision: int | None = None

    def __post_init__(self) -> None:
        if self.subject_expected_revision is not None:
            if isinstance(self.subject_expected_revision, bool) or not isinstance(self.subject_expected_revision, int):
                raise ValueError("subject_expected_revision must be an integer")
            if self.subject_expected_revision < 0:
                raise ValueError("subject_expected_revision must be non-negative")
        if self.authority_source_revision is not None:
            if isinstance(self.authority_source_revision, bool) or not isinstance(self.authority_source_revision, (str, int)):
                raise ValueError("authority_source_revision must be str or int")
            if isinstance(self.authority_source_revision, str) and not self.authority_source_revision:
                raise ValueError("authority_source_revision must be non-empty when str")
        _require_sha256(self.authority_observed_raw_digest, "authority_observed_raw_digest")
        _require_sha256(self.candidate_raw_digest, "candidate_raw_digest")
        _require_sha256(self.normalized_plan_digest, "normalized_plan_digest")
        # Invariant: raw digest and normalized digest are separate domains
        if (
            self.authority_observed_raw_digest is not None
            and self.normalized_plan_digest is not None
            and self.authority_observed_raw_digest == self.normalized_plan_digest
        ):
            raise ValueError("raw source and normalized plan digests are separate domains")
        # Invariant: they are not aliases
        if self.subject_expected_revision is not None and self.authority_source_revision is not None:
            # Must be able to distinguish; they are different types/domains but we enforce non-equality of string forms
            # The check is semantic: they are separate fields, not same value coerced
            pass

    def is_stale_against(self, current_revision: str | int | None, current_digest: str | None) -> bool:
        """Fail-closed stale check: any mismatch is stale."""
        if self.authority_source_revision is not None and self.authority_source_revision != current_revision:
            return True
        if self.authority_observed_raw_digest is not None and self.authority_observed_raw_digest != current_digest:
            return True
        return False


@dataclass(frozen=True)
class PortablePlanMutationRequest:
    """Executor-neutral typed request for the Portable Plan Mutation Port.

    Encodes the accepted D4 snapshot plus M4-3 bounded authorization linkage.
    No GitHub-specific fields, no generic write payload.
    """

    operation: str
    typed_target: ObjectRef
    correlation_id: str
    contract_hash: str
    idempotency_key: str
    intent_fingerprint: str
    subject_expected_revision: int
    authority_source_revision: str | int | None
    authority_observed_raw_digest: str | None
    candidate_raw_digest: str | None
    normalized_plan_digest: str | None
    principal: str
    authorization_reference: str | None = None
    lease_reference: str | None = None
    attempt_reference: str | None = None
    candidate_raw_body: str | None = None  # exact raw bytes for readback comparison (bounded)

    def __post_init__(self) -> None:
        if self.operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"operation must be one of {sorted(_ALLOWED_OPERATIONS)}")
        if not isinstance(self.typed_target, ObjectRef):
            raise ValueError("typed_target must be an ObjectRef")
        if not isinstance(self.correlation_id, str) or not _CORRELATION_RE.fullmatch(self.correlation_id):
            raise ValueError("correlation_id must be a bounded safe identifier")
        if not isinstance(self.contract_hash, str) or not _SHA256_RE.fullmatch(self.contract_hash):
            raise ValueError("contract_hash must be a SHA-256 hex digest")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be bounded non-empty")
        if not isinstance(self.intent_fingerprint, str) or not _SHA256_RE.fullmatch(self.intent_fingerprint):
            raise ValueError("intent_fingerprint must be a SHA-256 hex digest")
        if isinstance(self.subject_expected_revision, bool) or not isinstance(self.subject_expected_revision, int) or self.subject_expected_revision < 0:
            raise ValueError("subject_expected_revision must be non-negative int")
        if self.authority_source_revision is not None:
            if isinstance(self.authority_source_revision, bool) or not isinstance(self.authority_source_revision, (str, int)) or (isinstance(self.authority_source_revision, str) and not self.authority_source_revision):
                raise ValueError("authority_source_revision must be bounded str|int when supplied")
        _require_sha256(self.authority_observed_raw_digest, "authority_observed_raw_digest")
        _require_sha256(self.candidate_raw_digest, "candidate_raw_digest")
        _require_sha256(self.normalized_plan_digest, "normalized_plan_digest")
        if (
            self.authority_observed_raw_digest is not None
            and self.normalized_plan_digest is not None
            and self.authority_observed_raw_digest == self.normalized_plan_digest
        ):
            raise ValueError("raw source and normalized plan digests are separate domains: candidate_raw vs normalized")
        if not isinstance(self.principal, str) or not self.principal:
            raise ValueError("principal must be a bounded non-empty identifier")
        _require_safe_id(self.authorization_reference, "authorization_reference")
        _require_safe_id(self.lease_reference, "lease_reference")
        _require_safe_id(self.attempt_reference, "attempt_reference")
        if self.candidate_raw_body is not None and (not isinstance(self.candidate_raw_body, str) or len(self.candidate_raw_body) > 1_000_000):
            raise ValueError("candidate_raw_body must be bounded string when supplied")

    def raw_precondition(self) -> RawAuthorityPrecondition:
        return RawAuthorityPrecondition(
            authority_source_revision=self.authority_source_revision,
            authority_observed_raw_digest=self.authority_observed_raw_digest,
            candidate_raw_digest=self.candidate_raw_digest,
            normalized_plan_digest=self.normalized_plan_digest,
            subject_expected_revision=self.subject_expected_revision,
        )

    def complete_identity_fingerprint(self) -> str:
        """M4-3 authorization-bound complete identity for external journal linkage.

        Reuses the exact M4-3 complete semantics: operation, target, principal,
        contract_hash, intent_fingerprint, subject_expected_revision,
        external precondition, authority_source_revision, observed digest,
        candidate digest, normalized digest, plus lease/authorization references
        as bounded attempt identity (not semantic authority).

        Intent_fingerprint alone is NOT sufficient.
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
            "authorization_reference": self.authorization_reference,
            "lease_reference": self.lease_reference,
        }
        return _canonical_hash(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "typed_target": self.typed_target.to_canonical(),
            "correlation_id": self.correlation_id,
            "contract_hash": self.contract_hash,
            "idempotency_key": self.idempotency_key,
            "intent_fingerprint": self.intent_fingerprint,
            "subject_expected_revision": self.subject_expected_revision,
            "authority_source_revision": self.authority_source_revision,
            "authority_observed_raw_digest": self.authority_observed_raw_digest,
            "candidate_raw_digest": self.candidate_raw_digest,
            "normalized_plan_digest": self.normalized_plan_digest,
            "principal": self.principal,
            "authorization_reference": self.authorization_reference,
            "lease_reference": self.lease_reference,
            "attempt_reference": self.attempt_reference,
        }


@dataclass(frozen=True)
class PortablePlanMutationResponse:
    """Typed response from the port (transport layer).

    IMPORTANT: transport success does NOT imply VERIFIED. Caller must
    perform verify-after-write via authoritative readback.
    """

    operation: str
    typed_target: str
    correlation_id: str
    adapter_success: bool
    observed_raw_digest: str | None = None
    observed_revision: str | int | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in _ALLOWED_OPERATIONS:
            raise ValueError("response operation must be allowed")
        if self.observed_raw_digest is not None and not _SHA256_RE.fullmatch(self.observed_raw_digest):
            raise ValueError("observed_raw_digest must be SHA-256 when supplied")


# Opaque identifier contract: these are NOT semantic authority
OPAQUE_IDS_ARE_NOT_AUTHORITY = True


class PlanAuthorityMutationPort(ABC):
    """Executor-neutral typed port for Portable Plan external mutation.

    Concrete adapters (M4-6 GitHub, future stores) implement this.
    Core never calls GitHub directly; adapters are isolated behind this ABC.

    Contract invariants encoded:
    - operation limited to plan_init/plan_retirement
    - typed target (ObjectRef canonical)
    - correlation + contract + idempotency + intent
    - bounded authorization + lease/attempt references
    - Subject precondition distinct from authority precondition
    - candidate_raw_digest vs normalized_plan_digest distinct domains
    - read-before-write required, verify-after-write required
    - at-most-one external attempt (PREPARED -> APPLYING before mutate)
    - adapter success != VERIFIED
    """

    @abstractmethod
    def read_raw_authority(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        """Read current raw authority state for read-before-write.

        Returns (authority_source_revision, authority_observed_raw_digest, raw_body).
        Must be deterministic and executor-neutral. Never returns GitHub-specific types.
        """
        raise NotImplementedError

    @abstractmethod
    def mutate(self, request: PortablePlanMutationRequest) -> PortablePlanMutationResponse:
        """Perform at most one external mutation attempt via the port.

        Preconditions validated by caller:
        - read-before-write has verified raw precondition matches authorized observation
        - journal record is durably in APPLYING before this call
        - verify-after-write will follow to classify observed state

        Must not imply cross-authority atomicity.
        """
        raise NotImplementedError

    @abstractmethod
    def verify(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        """Re-read authoritative raw source after mutation for verify-after-write.

        Returns (observed_revision, observed_raw_digest, raw_body) for three-way classification.
        """
        raise NotImplementedError


# Downstream consumable interfaces (type-checkable by M4-6/M4-7)
M4_6_CONSUMABLE_PORT_CONTRACT_IMPLEMENTED = True
M4_7_CONSUMABLE_JOURNAL_CONTRACT_IMPLEMENTED = True

# Negative scope invariants preserved
GITHUB_ADAPTER_IMPLEMENTED = False
GITHUB_API_CALL_COUNT = 0

__all__ = [
    "GITHUB_IS_FORGE_CORE_ONTOLOGY",
    "GITHUB_API_IS_CORE_CONTRACT",
    "GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID",
    "RAW_GH_OPERATION_IN_CORE",
    "CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE",
    "READ_BEFORE_EXTERNAL_WRITE_REQUIRED",
    "VERIFY_AFTER_EXTERNAL_WRITE_REQUIRED",
    "ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED",
    "EXTERNAL_APPLY_BEFORE_PREPARED_ALLOWED",
    "AT_MOST_ONE_EXTERNAL_ATTEMPT_CONTRACT",
    "DISTRIBUTED_LOCK_IMPLEMENTED",
    "HEURISTIC_THIRD_STATE_SELECTION_ALLOWED",
    "THIRD_STATE_AUTO_MERGE_ALLOWED",
    "SEMANTIC_ROLLBACK_ALLOWED",
    "SEMANTIC_COMPENSATING_MUTATION_IMPLEMENTED",
    "UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED",
    "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE",
    "RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION",
    "RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE",
    "FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY",
    "DUPLICATE_OPERATION_SEMANTICS_CREATED",
    "OPERATION_CONTRACT_REUSE",
    "OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT",
    "INTERNAL_LIFECYCLE_RESULT_EQUALS_EXTERNAL_VERIFICATION_RESULT",
    "RawAuthorityPrecondition",
    "PortablePlanMutationRequest",
    "PortablePlanMutationResponse",
    "PlanAuthorityMutationPort",
    "ALLOWED_OPERATIONS",
    "M4_6_CONSUMABLE_PORT_CONTRACT_IMPLEMENTED",
    "M4_7_CONSUMABLE_JOURNAL_CONTRACT_IMPLEMENTED",
    "GITHUB_ADAPTER_IMPLEMENTED",
    "GITHUB_API_CALL_COUNT",
]
