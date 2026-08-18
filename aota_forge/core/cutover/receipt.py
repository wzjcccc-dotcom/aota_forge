"""M3-B13 Cutover Receipt Model.

Scope (Issue #9, Lane M3-B13):
- Typed, deterministic receipt capturing all cutover execution evidence.
- Captures before/after fingerprints, CAS revision state, precondition outcomes,
  commit state, postcommit verification state, and replay metadata.
- Explicit invariant: Receipt is evidence only, never authorization, cannot self-promote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Final

# Invariant properties
RECEIPT_IS_AUTHORIZATION: Final[bool] = False
RECEIPT_CAN_PROMOTE_SHADOW: Final[bool] = False
RECEIPT_CAN_MUTATE_PRODUCTION: Final[bool] = False


@dataclass(frozen=True)
class CutoverReceipt:
    """Strongly typed immutable cutover execution receipt."""

    receipt_id: str
    request_id: str
    idempotency_key: str
    mode: str  # "dry_run" | "activation"
    source_authority_ref: str
    target_authority_ref: str
    expected_source_revision: int
    observed_source_revision: int
    expected_target_fingerprint: str
    observed_target_fingerprint: str
    authorization_state: str
    authorization_ref: str | None
    precondition_outcomes: dict[str, Any]
    transaction_id: str | None
    before_fingerprint: str
    after_fingerprint: str
    commit_state: str  # "not_committed" | "staged_aborted" | "committed"
    postcommit_verification_state: str  # "not_applicable" | "verified" | "failed"
    failure_envelope: dict[str, Any] | None = None
    failure_code: str | None = None
    is_replay: bool = False
    created_at: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        """Deterministic canonical representation of receipt semantic content."""
        return {
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "mode": self.mode,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "expected_source_revision": self.expected_source_revision,
            "observed_source_revision": self.observed_source_revision,
            "expected_target_fingerprint": self.expected_target_fingerprint,
            "observed_target_fingerprint": self.observed_target_fingerprint,
            "authorization_state": self.authorization_state,
            "authorization_ref": self.authorization_ref,
            "precondition_outcomes": self.precondition_outcomes,
            "transaction_id": self.transaction_id,
            "before_fingerprint": self.before_fingerprint,
            "after_fingerprint": self.after_fingerprint,
            "commit_state": self.commit_state,
            "postcommit_verification_state": self.postcommit_verification_state,
            "failure_code": self.failure_code,
            "failure_envelope": self.failure_envelope,
        }

    def fingerprint(self) -> str:
        """Deterministic SHA-256 fingerprint of the semantic receipt payload."""
        payload = self.canonical_payload()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = self.canonical_payload()
        data["is_replay"] = self.is_replay
        data["created_at"] = self.created_at
        data["receipt_fingerprint"] = self.fingerprint()
        data["is_authorization"] = RECEIPT_IS_AUTHORIZATION
        data["can_promote_shadow"] = RECEIPT_CAN_PROMOTE_SHADOW
        data["can_mutate_production"] = RECEIPT_CAN_MUTATE_PRODUCTION
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


__all__ = [
    "RECEIPT_IS_AUTHORIZATION",
    "RECEIPT_CAN_PROMOTE_SHADOW",
    "RECEIPT_CAN_MUTATE_PRODUCTION",
    "CutoverReceipt",
]
