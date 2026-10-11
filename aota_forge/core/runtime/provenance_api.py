"""Strict and boolean runtime provenance APIs."""
from __future__ import annotations
import time
from typing import Any
from .provenance_base import *
from .provenance_decision import ActiveRuntimeEligibility
from .provenance_evaluator import evaluate_active_runtime
from .provenance_validate import validate_provenance

def validate_active_runtime(*args: Any, raise_on_failure: bool = True, **kwargs: Any) -> ActiveRuntimeEligibility:
    result = evaluate_active_runtime(*args, **kwargs)
    if raise_on_failure and not result.eligible:
        raise RuntimeEligibilityError("active runtime is not eligible: " + ",".join(result.reason_codes), result.reason_codes)
    return result
validate_active_runtime_eligibility = validate_active_runtime
ensure_active_runtime = validate_active_runtime

def is_active_runtime_eligible(*args: Any, **kwargs: Any) -> bool:
    try: return evaluate_active_runtime(*args, **kwargs).eligible
    except (RuntimeContractError, TypeError, ValueError): return False
active_runtime_eligible = is_active_runtime_eligible

def validate_runtime_provenance(provenance: Any, *, expected_fingerprint: str | None = None, expected_generation: Any = None, now: Any = None, max_age_seconds: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    reasons = validate_provenance(provenance, expected_fingerprint, expected_generation, current, max_age_seconds)
    if reasons: raise RuntimeProvenanceError("runtime provenance is not trusted/current", reasons)
    return True

class RuntimeIdentityValidator:
    evaluate = staticmethod(evaluate_active_runtime)
    validate = staticmethod(validate_active_runtime)
    is_eligible = staticmethod(is_active_runtime_eligible)
