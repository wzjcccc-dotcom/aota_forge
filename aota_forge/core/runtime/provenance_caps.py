"""Capability-parity projection used by runtime eligibility."""
from __future__ import annotations
from typing import Any
from .provenance_base import *
from .provenance_helpers import normalize_capabilities
from .provenance_decision import CapabilityParity, check_capability_parity

def capability_check(required: Any, caps: dict[str, Any], common: Any = None) -> tuple[CapabilityParity | None, list[str]]:
    reasons: list[str] = []
    if common is not None: caps = {name: value if value is not None else common for name, value in caps.items()}
    if required is None and not any(value is not None for value in caps.values()): return None, reasons
    missing = [name for name in ("configured", "deployed", "running") if caps[name] is None]
    if missing: reasons.append(REASON_CAPABILITY_UNKNOWN)
    try:
        expected = normalize_capabilities(required, "required capabilities") if required is not None else next((normalize_capabilities(caps[name], name) for name in ("configured", "deployed", "source") if caps[name] is not None), None)
        normalized = {name: normalize_capabilities(value, f"{name} capabilities") if value is not None else None for name, value in caps.items()}
    except RuntimeContractError as exc: return None, reasons + list(exc.reason_codes or (REASON_CAPABILITY_UNKNOWN,))
    parity = check_capability_parity(expected, normalized["running"])
    reasons.extend(parity.reason_codes)
    if normalized["running"] is not None: reasons.extend(REASON_CAPABILITY_PARITY for name in ("source", "configured", "deployed") if normalized[name] is not None and normalized[name] != normalized["running"])
    return parity, reasons
