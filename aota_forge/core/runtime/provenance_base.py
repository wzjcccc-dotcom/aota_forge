"""Runtime identity/provenance contract constants and errors."""
from __future__ import annotations
from collections.abc import Iterable

PROVENANCE_CONTRACT_VERSION = "runtime-identity-provenance-v1"
FINGERPRINT_ALGORITHM = "sha256"
REASON_UNKNOWN_FINGERPRINT = "UNKNOWN_FINGERPRINT"
REASON_FINGERPRINT_CONFLICT = "CONFLICTING_FINGERPRINT"
REASON_FINGERPRINT_PARITY = "FINGERPRINT_PARITY_MISMATCH"
REASON_UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"
REASON_UNTRUSTED_PROVENANCE = "UNTRUSTED_PROVENANCE"
REASON_PROVENANCE_CONFLICT = "CONFLICTING_PROVENANCE"
REASON_PROVENANCE_FINGERPRINT = "PROVENANCE_FINGERPRINT_MISMATCH"
REASON_PROVENANCE_CURRENTNESS = "PROVENANCE_CURRENTNESS_UNKNOWN"
REASON_STALE_RUNTIME = "STALE_RUNTIME_EVIDENCE"
REASON_HISTORICAL_RUNTIME = "HISTORICAL_RUNTIME_EVIDENCE"
REASON_INACTIVE_RUNTIME = "INACTIVE_RUNTIME_EVIDENCE"
REASON_PROVENANCE_EXPIRED = "EXPIRED_RUNTIME_PROVENANCE"
REASON_PROVENANCE_GENERATION = "PROVENANCE_GENERATION_MISMATCH"
REASON_CAPABILITY_UNKNOWN = "UNKNOWN_CAPABILITY_EVIDENCE"
REASON_CAPABILITY_PARITY = "CAPABILITY_PARITY_MISMATCH"
REASON_CONFLICTING_EVIDENCE = "CONFLICTING_RUNTIME_EVIDENCE"
UNKNOWN_FINGERPRINT_MARKERS = frozenset({"", "unknown", "unavailable", "undefined", "none", "null", "n/a", "na"})
ACTIVE_STATUSES = frozenset({"active", "current", "running", "eligible", "valid"})
RETIRED_STATUSES = frozenset({"stale", "historical", "retired", "superseded", "revoked", "expired", "inactive"})

class RuntimeContractError(ValueError):
    code = "RUNTIME_CONTRACT_INVALID"
    def __init__(self, message: str, reason_codes: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
class RuntimeEligibilityError(RuntimeContractError):
    code = "RUNTIME_NOT_ELIGIBLE"
class RuntimeProvenanceError(RuntimeContractError):
    code = "RUNTIME_PROVENANCE_INVALID"
RuntimeIdentityError = RuntimeContractError
RuntimeEvidenceError = RuntimeContractError
