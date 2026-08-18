"""M3-BG2 Exact Cutover Candidate & Future Authorization Package Models.

Scope (Issue #9, Gate M3-BG2):
- Typed exact cutover candidate representation.
- Typed future authorization package representation.
- Deterministic canonical hashing ensuring repeatability across environments.
- Invariants asserting candidate is evidence only and never production cutover authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Final

# Invariant Declarations
CANDIDATE_IS_CUTOVER_AUTHORIZATION: Final[bool] = False
CANDIDATE_DEPENDS_ON_CURRENT_POINTER: Final[bool] = False
CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER: Final[bool] = False
CANDIDATE_DEPENDS_ON_PROJECTION_AUTHORITY: Final[bool] = False
CANDIDATE_DEPENDS_ON_LATEST_HEURISTIC: Final[bool] = False
FUTURE_PACKAGE_IS_AUTHORIZATION: Final[bool] = False


@dataclass(frozen=True)
class ExactCutoverCandidate:
    """Exact, typed cutover candidate model.

    Candidate represents accepted and verified evidence for a single bounded cutover target.
    It carries zero authority and cannot execute cutover.
    """

    candidate_id: str
    source_authority_ref: str
    target_authority_ref: str
    expected_source_revision: int
    validated_target_fingerprint: str
    b12_validation_reference: str
    b13_mechanics_checkpoint: str
    authorization_requirement: str
    idempotency_replay_domain: str
    precondition_fingerprint: str
    candidate_evidence_fingerprint: str
    input_mode: str = "validated_shadow_candidate"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if not self.source_authority_ref:
            raise ValueError("source_authority_ref must not be empty")
        if not self.target_authority_ref:
            raise ValueError("target_authority_ref must not be empty")
        if self.expected_source_revision < 0:
            raise ValueError("expected_source_revision must be non-negative")
        if not self.validated_target_fingerprint:
            raise ValueError("validated_target_fingerprint must not be empty")
        if not self.b12_validation_reference:
            raise ValueError("b12_validation_reference must not be empty")
        if not self.b13_mechanics_checkpoint:
            raise ValueError("b13_mechanics_checkpoint must not be empty")
        if not self.authorization_requirement:
            raise ValueError("authorization_requirement must not be empty")
        if not self.idempotency_replay_domain:
            raise ValueError("idempotency_replay_domain must not be empty")

    @property
    def B12_validation_reference(self) -> str:
        return self.b12_validation_reference

    @property
    def B13_mechanics_checkpoint(self) -> str:
        return self.b13_mechanics_checkpoint

    def canonical_payload(self) -> dict[str, Any]:
        """Deterministic canonical representation of candidate payload."""
        return {
            "candidate_id": self.candidate_id,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "expected_source_revision": self.expected_source_revision,
            "validated_target_fingerprint": self.validated_target_fingerprint,
            "b12_validation_reference": self.b12_validation_reference,
            "b13_mechanics_checkpoint": self.b13_mechanics_checkpoint,
            "authorization_requirement": self.authorization_requirement,
            "idempotency_replay_domain": self.idempotency_replay_domain,
            "precondition_fingerprint": self.precondition_fingerprint,
            "input_mode": self.input_mode,
        }

    def fingerprint(self) -> str:
        """Deterministic SHA-256 fingerprint of the candidate."""
        payload = self.canonical_payload()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def is_complete(self) -> bool:
        """Verify completeness of all required candidate attributes."""
        return bool(
            self.candidate_id
            and self.source_authority_ref
            and self.target_authority_ref
            and self.expected_source_revision >= 0
            and len(self.validated_target_fingerprint) == 64
            and self.b12_validation_reference
            and len(self.b13_mechanics_checkpoint) == 40
            and self.authorization_requirement
            and self.idempotency_replay_domain
            and len(self.precondition_fingerprint) == 64
            and len(self.candidate_evidence_fingerprint) == 64
        )

    def to_dict(self) -> dict[str, Any]:
        data = self.canonical_payload()
        data["candidate_evidence_fingerprint"] = self.candidate_evidence_fingerprint
        data["candidate_fingerprint"] = self.fingerprint()
        data["is_complete"] = self.is_complete()
        data["candidate_is_cutover_authorization"] = CANDIDATE_IS_CUTOVER_AUTHORIZATION
        data["depends_on_current_pointer"] = CANDIDATE_DEPENDS_ON_CURRENT_POINTER
        data["depends_on_filesystem_order"] = CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class FutureAuthorizationPackage:
    """Bounded package for future governance authorization consideration.

    Contains accepted BG2 evidence, candidate fingerprint, source, target,
    expected revision, validated target fingerprint, B12 and B13 checkpoints,
    the pending authorization identity, and the live binding requirement.
    This package is NOT authorization.
    """

    package_id: str
    candidate_id: str
    candidate_fingerprint: str
    source_authority_ref: str
    target_authority_ref: str
    expected_source_revision: int
    validated_target_fingerprint: str
    b12_validation_checkpoint: str
    b13_mechanics_checkpoint: str
    accepted_bg2_evidence_fingerprint: str
    pending_authorization_identity: str
    additional_live_binding_required_before_actual_cutover: bool = True
    is_authorization: bool = False
    notes: str = (
        "Actual cutover requires separate governance transition and explicit authorization. "
        "BG2 PASS never authorizes B14 or live cutover."
    )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "source_authority_ref": self.source_authority_ref,
            "target_authority_ref": self.target_authority_ref,
            "expected_source_revision": self.expected_source_revision,
            "validated_target_fingerprint": self.validated_target_fingerprint,
            "b12_validation_checkpoint": self.b12_validation_checkpoint,
            "b13_mechanics_checkpoint": self.b13_mechanics_checkpoint,
            "accepted_bg2_evidence_fingerprint": self.accepted_bg2_evidence_fingerprint,
            "pending_authorization_identity": self.pending_authorization_identity,
            "additional_live_binding_required_before_actual_cutover": self.additional_live_binding_required_before_actual_cutover,
            "is_authorization": False,
        }

    def fingerprint(self) -> str:
        payload = self.canonical_payload()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = self.canonical_payload()
        data["package_fingerprint"] = self.fingerprint()
        data["notes"] = self.notes
        return data


__all__ = [
    "CANDIDATE_IS_CUTOVER_AUTHORIZATION",
    "CANDIDATE_DEPENDS_ON_CURRENT_POINTER",
    "CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER",
    "CANDIDATE_DEPENDS_ON_PROJECTION_AUTHORITY",
    "CANDIDATE_DEPENDS_ON_LATEST_HEURISTIC",
    "FUTURE_PACKAGE_IS_AUTHORIZATION",
    "ExactCutoverCandidate",
    "FutureAuthorizationPackage",
]
