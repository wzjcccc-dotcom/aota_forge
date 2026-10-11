"""Focused M2/W2 source-side runtime identity/provenance proofs."""
from __future__ import annotations
import hashlib
import pytest
from aota_forge.core.runtime.provenance import (
    REASON_CAPABILITY_PARITY, REASON_FINGERPRINT_PARITY, REASON_HISTORICAL_RUNTIME,
    REASON_STALE_RUNTIME, RuntimeEligibilityError, RuntimeFingerprintSet,
    check_capability_parity, compute_source_fingerprint, evaluate_active_runtime,
    validate_active_runtime,
)
FP = "a" * 64
CAPS = frozenset({"invoke", "read"})

def trusted(fp: str = FP, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {"source": "task-main-runtime", "authority_ref": "deployment:test", "evidence_ref": "observation:test-1", "trusted": True, "active": True, "current": True, "historical": False, "stale": False, "status": "active", "fingerprint": fp}
    value.update(overrides)
    return value

def valid(**kwargs: object):
    kwargs.setdefault("provenance", trusted())
    kwargs.setdefault("required_capabilities", CAPS)
    kwargs.setdefault("configured_capabilities", CAPS)
    kwargs.setdefault("deployed_capabilities", CAPS)
    kwargs.setdefault("running_capabilities", CAPS)
    return evaluate_active_runtime(RuntimeFingerprintSet(source=FP, configured=FP, deployed=FP, running=FP), **kwargs)

def test_source_fingerprint_is_deterministic_and_canonical() -> None:
    assert compute_source_fingerprint({"b": 2, "a": 1}) == compute_source_fingerprint({"a": 1, "b": 2})
    assert compute_source_fingerprint("model") == hashlib.sha256(b"model").hexdigest()

def test_matching_four_stage_identity_and_capabilities_is_eligible() -> None:
    result = valid()
    assert result.eligible is True
    assert result.fingerprints is not None
    assert result.fingerprints.to_dict()["running_fingerprint"] == FP
    assert result.capability_parity is not None and result.capability_parity.parity is True

def test_fingerprint_parity_mismatch_fails_closed() -> None:
    result = evaluate_active_runtime(RuntimeFingerprintSet(source=FP, configured=FP, deployed="b" * 64, running=FP), provenance=trusted())
    assert result.eligible is False
    assert REASON_FINGERPRINT_PARITY in result.reason_codes

@pytest.mark.parametrize(("override", "reason"), [({"stale": True}, REASON_STALE_RUNTIME), ({"current": False}, REASON_STALE_RUNTIME), ({"status": "historical"}, REASON_HISTORICAL_RUNTIME)])
def test_stale_or_historical_runtime_evidence_is_not_active(override: dict[str, object], reason: str) -> None:
    result = valid(provenance=trusted(**override))
    assert result.eligible is False
    assert reason in result.reason_codes

def test_unknown_or_untrusted_provenance_fails_closed() -> None:
    result = evaluate_active_runtime(RuntimeFingerprintSet(source=FP, configured=FP, deployed=FP, running=FP))
    assert result.eligible is False
    assert result.provenance_valid is False
    with pytest.raises(RuntimeEligibilityError):
        validate_active_runtime(RuntimeFingerprintSet(source=FP, configured=FP, deployed=FP, running=FP))

def test_capability_parity_rejects_missing_or_extra_capability() -> None:
    result = valid(running_capabilities={"invoke"})
    assert result.eligible is False
    assert REASON_CAPABILITY_PARITY in result.reason_codes
    assert check_capability_parity({"invoke"}, {"invoke", "read"}).parity is False

def test_stale_observation_fails_when_freshness_is_required() -> None:
    result = valid(provenance=trusted(observed_at=100), max_age_seconds=10, now=200)
    assert result.eligible is False
    assert REASON_STALE_RUNTIME in result.reason_codes
