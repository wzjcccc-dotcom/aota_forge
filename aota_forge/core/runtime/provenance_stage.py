"""Runtime observation stage helpers."""
from __future__ import annotations
from typing import Any
from .provenance_base import *
from .provenance_helpers import get_field

def stage_fingerprint(stage: Any) -> Any:
    if stage is None or isinstance(stage, str): return stage
    return get_field(stage, "fingerprint", "source", "configured", "deployed", "running", "source_fingerprint", "configured_fingerprint", "deployed_fingerprint", "running_fingerprint", "model_source_fingerprint")

def stage_provenance(stage: Any) -> Any:
    return get_field(stage, "provenance", "runtime_provenance", "trusted_provenance")

def stage_capabilities(stage: Any) -> Any:
    return get_field(stage, "capabilities", "supported_capabilities", "running_capabilities")

def stage_flags(stage: Any) -> list[str]:
    if stage is None or isinstance(stage, str): return []
    result: list[str] = []
    flags = (("active", get_field(stage, "active", "is_active")), ("current", get_field(stage, "current", "is_current")), ("historical", get_field(stage, "historical", "is_historical")), ("stale", get_field(stage, "stale", "is_stale")), ("conflict", get_field(stage, "conflict", "conflicting", "ambiguous")))
    for name, flag in flags:
        if flag is not None and type(flag) is not bool: result.append(REASON_CONFLICTING_EVIDENCE)
        elif name == "active" and flag is False: result.append(REASON_INACTIVE_RUNTIME)
        elif name == "current" and flag is False: result.append(REASON_STALE_RUNTIME)
        elif name == "historical" and flag is True: result.append(REASON_HISTORICAL_RUNTIME)
        elif name == "stale" and flag is True: result.append(REASON_STALE_RUNTIME)
        elif name == "conflict" and flag is True: result.append(REASON_CONFLICTING_EVIDENCE)
    status = get_field(stage, "status", "lifecycle", "lifecycle_state")
    if status is not None:
        if not isinstance(status, str): result.append(REASON_CONFLICTING_EVIDENCE)
        elif status.strip().lower() in RETIRED_STATUSES: result.append(REASON_HISTORICAL_RUNTIME if status.strip().lower() == "historical" else REASON_STALE_RUNTIME)
        elif status.strip().lower() not in ACTIVE_STATUSES: result.append(REASON_PROVENANCE_CURRENTNESS)
    return result
