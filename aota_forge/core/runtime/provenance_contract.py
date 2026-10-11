"""Convenience carrier for one runtime identity contract."""
from __future__ import annotations
from typing import Any
from .provenance_helpers import as_mapping, get_field
from .provenance_fingerprints import RuntimeFingerprintSet
from .provenance_stage import stage_fingerprint

class RuntimeIdentityContract:
    _is_contract = True
    def __init__(self, source: Any = None, configured: Any = None, deployed: Any = None, running: Any = None, provenance: Any = None, required_capabilities: Any = None, configured_capabilities: Any = None, deployed_capabilities: Any = None, running_capabilities: Any = None, expected_generation: Any = None, max_age_seconds: float | None = None, *, fingerprints: Any = None, source_fingerprint: Any = None, model_source_fingerprint: Any = None, configured_fingerprint: Any = None, deployed_fingerprint: Any = None, running_fingerprint: Any = None, capabilities: Any = None) -> None:
        if fingerprints is not None:
            source = source if source is not None else get_field(fingerprints, "source", "model_source", "source_fingerprint", "model_source_fingerprint")
            configured = configured if configured is not None else get_field(fingerprints, "configured", "configured_fingerprint")
            deployed = deployed if deployed is not None else get_field(fingerprints, "deployed", "deployed_fingerprint")
            running = running if running is not None else get_field(fingerprints, "running", "running_fingerprint")
        self.source = source if source is not None else source_fingerprint if source_fingerprint is not None else model_source_fingerprint
        self.configured = configured if configured is not None else configured_fingerprint
        self.deployed = deployed if deployed is not None else deployed_fingerprint
        self.running = running if running is not None else running_fingerprint
        self.provenance = provenance
        self.required_capabilities = required_capabilities if required_capabilities is not None else capabilities
        self.configured_capabilities, self.deployed_capabilities, self.running_capabilities = configured_capabilities, deployed_capabilities, running_capabilities
        self.expected_generation, self.max_age_seconds = expected_generation, max_age_seconds
    def evaluate(self, **kwargs: Any) -> Any:
        from .provenance_evaluator import evaluate_active_runtime
        return evaluate_active_runtime(self, **kwargs)
    def validate(self, **kwargs: Any) -> Any:
        from .provenance_api import validate_active_runtime
        return validate_active_runtime(self, **kwargs)
    def to_dict(self) -> dict[str, Any]:
        return {"fingerprints": RuntimeFingerprintSet(source=stage_fingerprint(self.source), configured=stage_fingerprint(self.configured), deployed=stage_fingerprint(self.deployed), running=stage_fingerprint(self.running)).to_dict(), "provenance": as_mapping(self.provenance) or self.provenance}
