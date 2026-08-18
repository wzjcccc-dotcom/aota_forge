"""M3-B13 Cutover Preconditions Evaluation Engine.

Scope (Issue #9, Lane M3-B13):
- Strict fail-closed verification of all cutover preconditions.
- Source/target exact binding, no heuristic/ambiguity selection.
- Matching B12 validation reference and snapshot fingerprint verification.
- Expected revision / CAS validation.
- Authorization verification (fails closed on invalid, expired, revoked, or unauthorized).
- Deterministic failure envelopes with explicit failure codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from aota_forge.core.cutover.model import (
    CUTOVER_AUTHORIZED,
    CutoverAuthorization,
    CutoverMode,
    CutoverRequest,
)


class PreconditionFailureCode(str, Enum):
    """Explicit machine-readable failure codes for precondition rejections."""

    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_MISMATCH = "source_mismatch"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_MISMATCH = "target_mismatch"
    AMBIGUOUS_TARGET = "ambiguous_target"
    MISSING_B12_VALIDATION = "missing_b12_validation"
    B12_VALIDATION_MISMATCH = "b12_validation_mismatch"
    STALE_REVISION = "stale_revision"
    TARGET_FINGERPRINT_MISMATCH = "target_fingerprint_mismatch"
    UNAUTHORIZED_ACTIVATION = "unauthorized_activation"
    INVALID_AUTHORIZATION = "invalid_authorization"
    EXPIRED_AUTHORIZATION = "expired_authorization"
    REVOKED_AUTHORIZATION = "revoked_authorization"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PRODUCTION_WRITE_PROHIBITED = "production_write_prohibited"


@dataclass(frozen=True)
class PreconditionOutcome:
    """Individual precondition check result."""

    name: str
    passed: bool
    detail: str
    failure_code: PreconditionFailureCode | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "failure_code": self.failure_code.value if self.failure_code else None,
        }


@dataclass(frozen=True)
class PreconditionEnvelope:
    """Envelope summarizing complete precondition outcome."""

    passed: bool
    outcomes: dict[str, PreconditionOutcome]
    primary_failure_code: PreconditionFailureCode | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "primary_failure_code": self.primary_failure_code.value if self.primary_failure_code else None,
            "error_message": self.error_message,
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
        }


class CutoverPreconditionEvaluator:
    """Evaluates all guarded cutover preconditions against transaction store."""

    def evaluate(
        self,
        request: CutoverRequest,
        store: Any,
        *,
        allow_synthetic_auth: bool = False,
    ) -> PreconditionEnvelope:
        """Evaluate all preconditions fail-closed."""
        outcomes: dict[str, PreconditionOutcome] = {}
        primary_code: PreconditionFailureCode | None = None
        error_msg: str | None = None

        # 1. Exact Source Authority Binding
        src_exists = store.has_authority_ref(request.source_authority_ref)
        if not src_exists:
            code = PreconditionFailureCode.SOURCE_NOT_FOUND
            outcomes["exact_source_binding"] = PreconditionOutcome(
                name="exact_source_binding",
                passed=False,
                detail=f"Source authority '{request.source_authority_ref}' not found in store.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["exact_source_binding"].detail
        else:
            outcomes["exact_source_binding"] = PreconditionOutcome(
                name="exact_source_binding",
                passed=True,
                detail=f"Exact source authority '{request.source_authority_ref}' verified.",
            )

        # 2. Exact Target Authority Binding & Ambiguity Check
        if request.target_candidates and len(request.target_candidates) > 1:
            code = PreconditionFailureCode.AMBIGUOUS_TARGET
            outcomes["exact_target_binding"] = PreconditionOutcome(
                name="exact_target_binding",
                passed=False,
                detail=f"Target ambiguity detected: {len(request.target_candidates)} candidate targets provided. Cannot select among ambiguous targets.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["exact_target_binding"].detail
        else:
            target_exists = store.has_target_ref(request.target_authority_ref)
            if not target_exists:
                code = PreconditionFailureCode.TARGET_NOT_FOUND
                outcomes["exact_target_binding"] = PreconditionOutcome(
                    name="exact_target_binding",
                    passed=False,
                    detail=f"Target authority '{request.target_authority_ref}' not found in store.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["exact_target_binding"].detail
            else:
                outcomes["exact_target_binding"] = PreconditionOutcome(
                    name="exact_target_binding",
                    passed=True,
                    detail=f"Exact target authority '{request.target_authority_ref}' verified without ambiguity.",
                )

        # 3. Matching B12 Validation Reference & Fingerprint
        b12_val = store.get_b12_validation(request.b12_validation_ref)
        if not b12_val:
            code = PreconditionFailureCode.MISSING_B12_VALIDATION
            outcomes["b12_validation"] = PreconditionOutcome(
                name="b12_validation",
                passed=False,
                detail=f"B12 validation reference '{request.b12_validation_ref}' not found.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["b12_validation"].detail
        else:
            stored_val_fp = b12_val.get("snapshot_fingerprint") or b12_val.get("rebuild_fingerprint")
            val_status = b12_val.get("status", "")
            if val_status != "ACCEPTED" or stored_val_fp != request.b12_validation_fingerprint:
                code = PreconditionFailureCode.B12_VALIDATION_MISMATCH
                outcomes["b12_validation"] = PreconditionOutcome(
                    name="b12_validation",
                    passed=False,
                    detail=f"B12 validation reference '{request.b12_validation_ref}' status={val_status}, expected fp={request.b12_validation_fingerprint}, actual={stored_val_fp}",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["b12_validation"].detail
            else:
                outcomes["b12_validation"] = PreconditionOutcome(
                    name="b12_validation",
                    passed=True,
                    detail=f"B12 validation reference '{request.b12_validation_ref}' accepted and fingerprint matched.",
                )

        # 4. Expected Source Revision / CAS Validation
        observed_rev = store.get_revision(request.source_authority_ref)
        if observed_rev != request.expected_source_revision:
            code = PreconditionFailureCode.STALE_REVISION
            outcomes["expected_revision_cas"] = PreconditionOutcome(
                name="expected_revision_cas",
                passed=False,
                detail=f"Source revision mismatch: expected {request.expected_source_revision}, observed {observed_rev}. CAS rejected.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["expected_revision_cas"].detail
        else:
            outcomes["expected_revision_cas"] = PreconditionOutcome(
                name="expected_revision_cas",
                passed=True,
                detail=f"Source revision CAS verified at rev={observed_rev}.",
            )

        # 5. Target State & Fingerprint Validation
        observed_target_fp = store.get_target_fingerprint(request.target_authority_ref)
        if observed_target_fp != request.expected_target_fingerprint:
            code = PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH
            outcomes["target_fingerprint"] = PreconditionOutcome(
                name="target_fingerprint",
                passed=False,
                detail=f"Target fingerprint mismatch: expected {request.expected_target_fingerprint}, observed {observed_target_fp}.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["target_fingerprint"].detail
        else:
            outcomes["target_fingerprint"] = PreconditionOutcome(
                name="target_fingerprint",
                passed=True,
                detail=f"Target fingerprint matched: {observed_target_fp[:16]}...",
            )

        # 6. Authorization Validity
        mode_val = request.mode.value if isinstance(request.mode, CutoverMode) else request.mode
        if mode_val == CutoverMode.DRY_RUN.value:
            outcomes["activation_authorization"] = PreconditionOutcome(
                name="activation_authorization",
                passed=True,
                detail="Dry-run mode requires no activation authorization; mutates zero authority.",
            )
        else:
            # Activation mode
            if not CUTOVER_AUTHORIZED and not (allow_synthetic_auth and request.authorization and request.authorization.is_synthetic):
                code = PreconditionFailureCode.UNAUTHORIZED_ACTIVATION
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=False,
                    detail="Live cutover authorization is not active in governance (CUTOVER_AUTHORIZED=no). Fail-closed.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["activation_authorization"].detail
            elif request.authorization is None:
                code = PreconditionFailureCode.INVALID_AUTHORIZATION
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=False,
                    detail="Activation mode requires explicit non-null CutoverAuthorization.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["activation_authorization"].detail
            elif request.authorization.status == "expired":
                code = PreconditionFailureCode.EXPIRED_AUTHORIZATION
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=False,
                    detail="CutoverAuthorization is expired.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["activation_authorization"].detail
            elif request.authorization.status == "revoked":
                code = PreconditionFailureCode.REVOKED_AUTHORIZATION
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=False,
                    detail="CutoverAuthorization has been revoked.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["activation_authorization"].detail
            elif request.authorization.status != "active" or not request.authorization.is_valid_for(
                request.request_id, request.source_authority_ref, request.target_authority_ref
            ):
                code = PreconditionFailureCode.INVALID_AUTHORIZATION
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=False,
                    detail="CutoverAuthorization is invalid for this request/source/target scope.",
                    failure_code=code,
                )
                if not primary_code:
                    primary_code = code
                    error_msg = outcomes["activation_authorization"].detail
            else:
                outcomes["activation_authorization"] = PreconditionOutcome(
                    name="activation_authorization",
                    passed=True,
                    detail=f"Valid {'synthetic ' if request.authorization.is_synthetic else ''}authorization verified.",
                )

        # 7. Production Isolation
        if getattr(store, "namespace", "") == "production":
            code = PreconditionFailureCode.PRODUCTION_WRITE_PROHIBITED
            outcomes["production_isolation"] = PreconditionOutcome(
                name="production_isolation",
                passed=False,
                detail="Store aliases production namespace. Authoritative production writes prohibited.",
                failure_code=code,
            )
            if not primary_code:
                primary_code = code
                error_msg = outcomes["production_isolation"].detail
        else:
            outcomes["production_isolation"] = PreconditionOutcome(
                name="production_isolation",
                passed=True,
                detail=f"Store namespace '{getattr(store, 'namespace', 'isolated')}' strictly isolated from production.",
            )

        all_passed = all(o.passed for o in outcomes.values())
        return PreconditionEnvelope(
            passed=all_passed,
            outcomes=outcomes,
            primary_failure_code=primary_code if not all_passed else None,
            error_message=error_msg if not all_passed else None,
        )


__all__ = [
    "PreconditionFailureCode",
    "PreconditionOutcome",
    "PreconditionEnvelope",
    "CutoverPreconditionEvaluator",
]
