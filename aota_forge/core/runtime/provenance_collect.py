"""Fingerprint/stage collection for runtime validation."""
from __future__ import annotations
from typing import Any
from .provenance_base import *
from .provenance_helpers import get_field
from .provenance_fingerprints import RuntimeFingerprintSet
from .provenance_stage import stage_fingerprint

def collect_stages(fingerprints: Any, configured: Any, deployed: Any, running: Any, *, source: Any = None, model_source: Any = None, source_fingerprint: Any = None, model_source_fingerprint: Any = None, configured_fingerprint: Any = None, deployed_fingerprint: Any = None, running_fingerprint: Any = None) -> tuple[dict[str, Any], RuntimeFingerprintSet | None, list[str]]:
    reasons: list[str] = []
    if isinstance(fingerprints, RuntimeFingerprintSet):
        container = {name: getattr(fingerprints, name) for name in ("source", "configured", "deployed", "running")}
    elif isinstance(fingerprints, dict):
        container = {name: get_field(fingerprints, name, f"{name}_fingerprint") for name in ("source", "configured", "deployed", "running")}
    elif isinstance(fingerprints, str):
        container = {"source": fingerprints, "configured": configured, "deployed": deployed, "running": running}
    elif fingerprints is None:
        container = {name: None for name in ("source", "configured", "deployed", "running")}
    else:
        container = {name: get_field(fingerprints, name, f"{name}_fingerprint") for name in ("source", "configured", "deployed", "running")}
    explicit = {"source": source if source is not None else model_source if model_source is not None else source_fingerprint if source_fingerprint is not None else model_source_fingerprint, "configured": configured if configured is not None else configured_fingerprint, "deployed": deployed if deployed is not None else deployed_fingerprint, "running": running if running is not None else running_fingerprint}
    stages: dict[str, Any] = {}
    for name, value in explicit.items():
        if value is not None and container[name] is not None and stage_fingerprint(value) != stage_fingerprint(container[name]): reasons.extend((REASON_FINGERPRINT_CONFLICT, REASON_CONFLICTING_EVIDENCE))
        stages[name] = container[name] if container[name] is not None else value
    try: result = RuntimeFingerprintSet(**{name: stage_fingerprint(value) for name, value in stages.items()})
    except RuntimeContractError as exc: result = None; reasons.extend(exc.reason_codes or (REASON_UNKNOWN_FINGERPRINT,))
    if result is None or not result.complete: reasons.append(REASON_UNKNOWN_FINGERPRINT)
    elif len(set(result.to_tuple())) != 1: reasons.append(REASON_FINGERPRINT_PARITY)
    return stages, result, reasons
