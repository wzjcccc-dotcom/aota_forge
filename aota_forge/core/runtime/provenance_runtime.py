"""Runtime-side portion of eligibility evaluation."""
from __future__ import annotations
import time
from typing import Any
from .provenance_base import *
from .provenance_decision import ActiveRuntimeEligibility
from .provenance_stage import stage_provenance, stage_capabilities, stage_flags
from .provenance_validate import validate_provenance
from .provenance_caps import capability_check

def _items(value: Any) -> list[Any]:
    if value is None: return []
    return list(value) if isinstance(value, (list, tuple, frozenset)) else [value]

def evaluate_stages(stages: dict[str, Any], fingerprints: Any, *, provenance: Any = None, required_capabilities: Any = None, expected_capabilities: Any = None, capabilities: Any = None, configured_capabilities: Any = None, deployed_capabilities: Any = None, running_capabilities: Any = None, expected_generation: Any = None, now: Any = None, max_age_seconds: float | None = None) -> ActiveRuntimeEligibility:
    reasons: list[str] = []
    entries = [(item, fingerprints.running if fingerprints else None, "common") for item in _items(provenance)]
    for name, stage in stages.items():
        expected = fingerprints.to_dict().get(f"{name}_fingerprint") if fingerprints else None
        entries.extend((item, expected, name) for item in _items(stage_provenance(stage)))
    if not entries: reasons.append(REASON_UNKNOWN_PROVENANCE)
    current = time.time() if now is None else float(now) if isinstance(now, (int, float)) and not isinstance(now, bool) else None
    if current is None: reasons.append(REASON_PROVENANCE_CURRENTNESS); current = time.time()
    provenance_valid = bool(entries)
    for item, expected, _ in entries:
        found = validate_provenance(item, expected, expected_generation, current, max_age_seconds)
        reasons.extend(found); provenance_valid = provenance_valid and not found
    if not any(name == "common" or name in {"configured", "deployed", "running"} for _, _, name in entries): reasons.append(REASON_UNKNOWN_PROVENANCE); provenance_valid = False
    for stage in stages.values(): reasons.extend(stage_flags(stage))
    required = required_capabilities if required_capabilities is not None else expected_capabilities
    caps = {"source": stage_capabilities(stages["source"]), "configured": configured_capabilities if configured_capabilities is not None else stage_capabilities(stages["configured"]), "deployed": deployed_capabilities if deployed_capabilities is not None else stage_capabilities(stages["deployed"]), "running": running_capabilities if running_capabilities is not None else stage_capabilities(stages["running"])}
    parity, cap_reasons = capability_check(required, caps, capabilities)
    reasons.extend(cap_reasons)
    reason_codes = tuple(dict.fromkeys(x for x in reasons if x))
    return ActiveRuntimeEligibility(not reason_codes, reason_codes, fingerprints, provenance_valid, parity, bool(entries))
