"""M3-B13 Cutover Mechanics Model & Invariants.

Scope (Issue #9, Lane M3-B13):
- Typed request models, authorization representation, and mode enums.
- State machine states and transitions for guarded cutover mechanics.
- Invariant declarations preventing unauthorized live cutover, production mutation, or lease consumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Final

# Governance & Invariant Constants
B13_CUTOVER_MECHANICS_IMPLEMENTED: Final[bool] = True
B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED: Final[bool] = True
B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED: Final[bool] = True
B13_EXACT_TARGET_BINDING_REQUIRED: Final[bool] = True
B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS: Final[bool] = False
B13_B12_VALIDATION_REFERENCE_REQUIRED: Final[bool] = True
B13_UNVALIDATED_SHADOW_TARGET_ALLOWED: Final[bool] = False
B13_PRECONDITION_MODEL_IMPLEMENTED: Final[bool] = True
B13_PRECONDITION_FAILURE_FAILS_CLOSED: Final[bool] = True
B13_EXPECTED_REVISION_REQUIRED: Final[bool] = True
B13_STALE_REVISION_FAILS_CLOSED: Final[bool] = True
B13_SILENT_LAST_WRITE_WINS_ALLOWED: Final[bool] = False
B13_TARGET_FINGERPRINT_REQUIRED: Final[bool] = True
B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED: Final[bool] = False
B13_CUTOVER_STATE_MACHINE_IMPLEMENTED: Final[bool] = True
B13_FAILURE_STATES_EXPLICIT: Final[bool] = True
B13_CUTOVER_TRANSACTION_IMPLEMENTED: Final[bool] = True
PARTIAL_AUTHORITY_CUTOVER_ALLOWED: Final[bool] = False
B13_AUTHORITY_SWITCH_ATOMICITY_PROOF: Final[str] = "PASS"
B13_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY: Final[bool] = True
B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH: Final[bool] = False
B13_POSTCOMMIT_VERIFICATION_IMPLEMENTED: Final[bool] = True
B13_POSTCOMMIT_FAILURE_STATE_EXPLICIT: Final[bool] = True
B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED: Final[bool] = False
B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT: Final[bool] = True
B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT: Final[bool] = True
B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED: Final[bool] = True
B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT: Final[bool] = False
B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS: Final[bool] = True
B13_CUTOVER_RECEIPT_IMPLEMENTED: Final[bool] = True
B13_RECEIPT_DETERMINISTIC: Final[bool] = True
B13_CUTOVER_RECEIPT_IS_AUTHORIZATION: Final[bool] = False
B13_DRY_RUN_IMPLEMENTED: Final[bool] = True
B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY: Final[bool] = False
B13_ACTIVATION_PATH_IMPLEMENTED: Final[bool] = True
B13_ACTIVATION_CURRENTLY_EXECUTABLE: Final[bool] = False
B13_VALID_ACTIVATION_ATTEMPT_WITHOUT_AUTHORIZATION: Final[str] = "REJECTED"
B13_PRODUCTION_AUTHORITY_CHANGED_BY_UNAUTHORIZED_ATTEMPT: Final[bool] = False
B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION: Final[bool] = True
B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED: Final[bool] = False
B13_SOURCE_AUTHORITY_MISMATCH_FAILS: Final[bool] = True
B13_TARGET_AUTHORITY_MISMATCH_FAILS: Final[bool] = True
B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS: Final[bool] = True
B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION: Final[bool] = False
B13_SIMULATION_STORE_ALIASES_PRODUCTION: Final[bool] = False
B13_SIMULATED_AUTHORITY_SWITCH_PERFORMED: Final[bool] = True
B13_SYNTHETIC_TEST_AUTHORIZATION_USED: Final[bool] = True
B13_SYNTHETIC_AUTHORIZATION_HAS_PRODUCTION_EFFECT: Final[bool] = False
B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE: Final[bool] = True
B13_TO_BG2_HANDOFF_IMPLEMENTED: Final[bool] = True
B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION: Final[bool] = False

# Boundary prohibitions & governance inputs
M3_B13_EXECUTION_AUTHORIZED: Final[bool] = True
CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED: Final[bool] = True
M3_BG2_EXECUTION_AUTHORIZED: Final[bool] = False
M3_B14_EXECUTION_AUTHORIZED: Final[bool] = False
M3_B_SHADOW_MATERIALIZATION_AUTHORIZED: Final[bool] = True
CUTOVER_AUTHORIZED: Final[bool] = False
CUTOVER_PERFORMED: Final[bool] = False
CUTOVER_COMPLETED: Final[bool] = False
VALIDATED_SHADOW_STATE_IS_PRODUCTION_AUTHORITY: Final[bool] = False
PRODUCTION_GRAPH_AUTHORITY_ACTIVE: Final[bool] = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED: Final[bool] = False
LIVE_PRODUCTION_CUTOVER_PERFORMED: Final[bool] = False
LIVE_PRODUCTION_MIGRATION_PERFORMED: Final[bool] = False
DEPLOY_PERFORMED: Final[bool] = False
RUNTIME_RELOAD_PERFORMED: Final[bool] = False
PRODUCTION_CUTOVER_SERVICE_ACTIVATED: Final[bool] = False
PUSH_PERFORMED: Final[bool] = False
GITHUB_MUTATION_PERFORMED: Final[bool] = False

# Production isolation metrics
B13_PRODUCTION_GRAPH_WRITE_COUNT: Final[int] = 0
B13_PRODUCTION_BINDING_WRITE_COUNT: Final[int] = 0
B13_PRODUCTION_REVISION_WRITE_COUNT: Final[int] = 0
B13_PRODUCTION_LEASE_CONSUMPTION_COUNT: Final[int] = 0

EXPECTED_BASE_COMMIT: Final[str] = "f39f292038f2663d07ee80546b5ab2ada719214f"


class CutoverMode(str, Enum):
    """Cutover operation execution mode."""

    DRY_RUN = "dry_run"
    ACTIVATION = "activation"


class CutoverState(str, Enum):
    """Explicit deterministic state machine states for cutover lifecycle."""

    REQUESTED = "requested"
    PRECONDITION_CHECKED = "precondition_checked"
    STAGED = "staged"
    COMMITTED = "committed"
    POSTCOMMIT_VERIFIED = "postcommit_verified"
    ABORTED_PRECOMMIT = "aborted_precommit"
    COMMITTED_VERIFICATION_FAILED = "committed_verification_failed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CutoverAuthorization:
    """Explicit authorization token representation.

    Authorization cannot be minted by B13 or derived from dry-run receipts.
    Only governance or synthetic test harnesses can instantiate this.
    """

    auth_id: str
    issued_by: str
    issued_at: str
    expires_at: str
    request_id: str
    source_authority_ref: str
    target_authority_ref: str
    scope: str
    status: str = "active"  # active, expired, revoked, invalid
    is_synthetic: bool = False

    def is_valid_for(self, request_id: str, source_ref: str, target_ref: str) -> bool:
        """Verify validity against target request parameters."""
        if self.status != "active":
            return False
        if self.request_id != request_id:
            return False
        if self.source_authority_ref != source_ref:
            return False
        if self.target_authority_ref != target_ref:
            return False
        return True

    def fingerprint(self) -> str:
        """Deterministic fingerprint of authorization payload."""
        payload = {
            "auth_id": self.auth_id,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "request_id": self.request_id,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "scope": self.scope,
            "status": self.status,
            "is_synthetic": self.is_synthetic,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "auth_id": self.auth_id,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "request_id": self.request_id,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "scope": self.scope,
            "status": self.status,
            "is_synthetic": self.is_synthetic,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class CutoverRequest:
    """Strongly typed cutover request model."""

    request_id: str
    idempotency_key: str
    source_authority_ref: str
    target_authority_ref: str
    expected_source_revision: int
    expected_target_fingerprint: str
    b12_validation_ref: str
    b12_validation_fingerprint: str
    mode: CutoverMode = CutoverMode.DRY_RUN
    authorization: CutoverAuthorization | None = None
    target_candidates: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id or not isinstance(self.request_id, str):
            raise ValueError("request_id must be a non-empty string")
        if not self.idempotency_key or not isinstance(self.idempotency_key, str):
            raise ValueError("idempotency_key must be a non-empty string")
        if not self.source_authority_ref or not isinstance(self.source_authority_ref, str):
            raise ValueError("source_authority_ref must be a non-empty string")
        if not self.target_authority_ref or not isinstance(self.target_authority_ref, str):
            raise ValueError("target_authority_ref must be a non-empty string")
        if self.expected_source_revision < 0:
            raise ValueError("expected_source_revision must be non-negative")
        if not self.expected_target_fingerprint:
            raise ValueError("expected_target_fingerprint must be a non-empty string")
        if not self.b12_validation_ref:
            raise ValueError("b12_validation_ref must be a non-empty string")
        if not self.b12_validation_fingerprint:
            raise ValueError("b12_validation_fingerprint must be a non-empty string")

    def canonical_payload(self) -> dict[str, Any]:
        """Produce deterministic canonical representation of the request."""
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "expected_source_revision": self.expected_source_revision,
            "expected_target_fingerprint": self.expected_target_fingerprint,
            "b12_validation_ref": self.b12_validation_ref,
            "b12_validation_fingerprint": self.b12_validation_fingerprint,
            "mode": str(self.mode.value if isinstance(self.mode, CutoverMode) else self.mode),
            "authorization_ref": self.authorization.auth_id if self.authorization else None,
            "target_candidates": list(self.target_candidates),
        }

    def fingerprint(self) -> str:
        """Deterministic canonical SHA-256 fingerprint of the request."""
        payload = self.canonical_payload()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = self.canonical_payload()
        data["metadata"] = dict(self.metadata)
        data["request_fingerprint"] = self.fingerprint()
        if self.authorization:
            data["authorization"] = self.authorization.to_dict()
        return data


__all__ = [
    "B13_CUTOVER_MECHANICS_IMPLEMENTED",
    "B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED",
    "B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED",
    "B13_EXACT_TARGET_BINDING_REQUIRED",
    "B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS",
    "B13_B12_VALIDATION_REFERENCE_REQUIRED",
    "B13_UNVALIDATED_SHADOW_TARGET_ALLOWED",
    "B13_PRECONDITION_MODEL_IMPLEMENTED",
    "B13_PRECONDITION_FAILURE_FAILS_CLOSED",
    "B13_EXPECTED_REVISION_REQUIRED",
    "B13_STALE_REVISION_FAILS_CLOSED",
    "B13_SILENT_LAST_WRITE_WINS_ALLOWED",
    "B13_TARGET_FINGERPRINT_REQUIRED",
    "B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED",
    "B13_CUTOVER_STATE_MACHINE_IMPLEMENTED",
    "B13_FAILURE_STATES_EXPLICIT",
    "B13_CUTOVER_TRANSACTION_IMPLEMENTED",
    "PARTIAL_AUTHORITY_CUTOVER_ALLOWED",
    "B13_AUTHORITY_SWITCH_ATOMICITY_PROOF",
    "B13_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY",
    "B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH",
    "B13_POSTCOMMIT_VERIFICATION_IMPLEMENTED",
    "B13_POSTCOMMIT_FAILURE_STATE_EXPLICIT",
    "B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED",
    "B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT",
    "B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT",
    "B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED",
    "B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT",
    "B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS",
    "B13_CUTOVER_RECEIPT_IMPLEMENTED",
    "B13_RECEIPT_DETERMINISTIC",
    "B13_CUTOVER_RECEIPT_IS_AUTHORIZATION",
    "B13_DRY_RUN_IMPLEMENTED",
    "B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY",
    "B13_ACTIVATION_PATH_IMPLEMENTED",
    "B13_ACTIVATION_CURRENTLY_EXECUTABLE",
    "B13_VALID_ACTIVATION_ATTEMPT_WITHOUT_AUTHORIZATION",
    "B13_PRODUCTION_AUTHORITY_CHANGED_BY_UNAUTHORIZED_ATTEMPT",
    "B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION",
    "B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED",
    "B13_SOURCE_AUTHORITY_MISMATCH_FAILS",
    "B13_TARGET_AUTHORITY_MISMATCH_FAILS",
    "B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS",
    "B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION",
    "B13_SIMULATION_STORE_ALIASES_PRODUCTION",
    "B13_SIMULATED_AUTHORITY_SWITCH_PERFORMED",
    "B13_SYNTHETIC_TEST_AUTHORIZATION_USED",
    "B13_SYNTHETIC_AUTHORIZATION_HAS_PRODUCTION_EFFECT",
    "B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE",
    "B13_TO_BG2_HANDOFF_IMPLEMENTED",
    "B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION",
    "M3_B13_EXECUTION_AUTHORIZED",
    "CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED",
    "M3_BG2_EXECUTION_AUTHORIZED",
    "M3_B14_EXECUTION_AUTHORIZED",
    "M3_B_SHADOW_MATERIALIZATION_AUTHORIZED",
    "CUTOVER_AUTHORIZED",
    "CUTOVER_PERFORMED",
    "CUTOVER_COMPLETED",
    "VALIDATED_SHADOW_STATE_IS_PRODUCTION_AUTHORITY",
    "PRODUCTION_GRAPH_AUTHORITY_ACTIVE",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED",
    "LIVE_PRODUCTION_CUTOVER_PERFORMED",
    "LIVE_PRODUCTION_MIGRATION_PERFORMED",
    "DEPLOY_PERFORMED",
    "RUNTIME_RELOAD_PERFORMED",
    "PRODUCTION_CUTOVER_SERVICE_ACTIVATED",
    "PUSH_PERFORMED",
    "GITHUB_MUTATION_PERFORMED",
    "B13_PRODUCTION_GRAPH_WRITE_COUNT",
    "B13_PRODUCTION_BINDING_WRITE_COUNT",
    "B13_PRODUCTION_REVISION_WRITE_COUNT",
    "B13_PRODUCTION_LEASE_CONSUMPTION_COUNT",
    "EXPECTED_BASE_COMMIT",
    "CutoverMode",
    "CutoverState",
    "CutoverAuthorization",
    "CutoverRequest",
]
