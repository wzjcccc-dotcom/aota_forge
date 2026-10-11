"""Capability and eligibility decision models."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .provenance_fingerprints import RuntimeFingerprintSet
from .provenance_helpers import normalize_capabilities
from .provenance_base import *

@dataclass(frozen=True)
class CapabilityParity:
    expected: frozenset[str] | None
    observed: frozenset[str] | None
    missing: frozenset[str] = frozenset()
    unexpected: frozenset[str] = frozenset()
    parity: bool = False
    reason_codes: tuple[str, ...] = ()
    def __bool__(self) -> bool:
        return self.parity
    @property
    def matches(self) -> bool:
        return self.parity
    def to_dict(self) -> dict[str, Any]:
        return {"expected": sorted(self.expected) if self.expected is not None else None, "observed": sorted(self.observed) if self.observed is not None else None, "missing": sorted(self.missing), "unexpected": sorted(self.unexpected), "parity": self.parity, "reason_codes": list(self.reason_codes)}

def check_capability_parity(expected: Any, observed: Any) -> CapabilityParity:
    try:
        left, right = normalize_capabilities(expected, "expected capabilities"), normalize_capabilities(observed, "observed capabilities")
    except RuntimeContractError as exc:
        return CapabilityParity(None, None, reason_codes=exc.reason_codes or (REASON_CAPABILITY_UNKNOWN,))
    if left is None or right is None:
        return CapabilityParity(left, right, reason_codes=(REASON_CAPABILITY_UNKNOWN,))
    missing, unexpected = left - right, right - left
    ok = not missing and not unexpected
    return CapabilityParity(left, right, frozenset(missing), frozenset(unexpected), ok, () if ok else (REASON_CAPABILITY_PARITY,))

def validate_capability_parity(expected: Any, observed: Any) -> CapabilityParity:
    result = check_capability_parity(expected, observed)
    if not result.parity:
        raise RuntimeContractError("capability evidence is unknown or mismatched", result.reason_codes or (REASON_CAPABILITY_PARITY,))
    return result

def capabilities_match(expected: Any, observed: Any) -> bool:
    return check_capability_parity(expected, observed).parity

@dataclass(frozen=True)
class ActiveRuntimeEligibility:
    eligible: bool
    reason_codes: tuple[str, ...] = ()
    fingerprints: RuntimeFingerprintSet | None = None
    provenance_valid: bool = False
    capability_parity: CapabilityParity | None = None
    external_evidence_present: bool = False
    def __bool__(self) -> bool:
        return self.eligible
    @property
    def reasons(self) -> tuple[str, ...]:
        return self.reason_codes
    @property
    def active(self) -> bool:
        return self.eligible
    def to_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "active": self.eligible, "reason_codes": list(self.reason_codes), "fingerprints": self.fingerprints.to_dict() if self.fingerprints else None, "provenance_valid": self.provenance_valid, "capability_parity": self.capability_parity.to_dict() if self.capability_parity else None, "external_evidence_present": self.external_evidence_present}
RuntimeEligibilityDecision = ActiveRuntimeEligibility
