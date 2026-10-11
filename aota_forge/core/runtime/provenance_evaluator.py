"""Public active-runtime eligibility evaluator."""
from __future__ import annotations
from typing import Any
from .provenance_base import *
from .provenance_collect import collect_stages
from .provenance_runtime import evaluate_stages
from .provenance_decision import ActiveRuntimeEligibility

def evaluate_active_runtime(fingerprints: Any = None, configured: Any = None, deployed: Any = None, running: Any = None, *, source: Any = None, model_source: Any = None, source_fingerprint: Any = None, model_source_fingerprint: Any = None, configured_fingerprint: Any = None, deployed_fingerprint: Any = None, running_fingerprint: Any = None, provenance: Any = None, required_capabilities: Any = None, expected_capabilities: Any = None, capabilities: Any = None, configured_capabilities: Any = None, deployed_capabilities: Any = None, running_capabilities: Any = None, expected_generation: Any = None, now: Any = None, max_age_seconds: float | None = None) -> ActiveRuntimeEligibility:
    if getattr(fingerprints, "_is_contract", False):
        contract = fingerprints
        source, configured, deployed, running = contract.source, contract.configured, contract.deployed, contract.running
        provenance = contract.provenance if provenance is None else provenance
        required_capabilities = contract.required_capabilities if required_capabilities is None else required_capabilities
        configured_capabilities = contract.configured_capabilities if configured_capabilities is None else configured_capabilities
        deployed_capabilities = contract.deployed_capabilities if deployed_capabilities is None else deployed_capabilities
        running_capabilities = contract.running_capabilities if running_capabilities is None else running_capabilities
        expected_generation = contract.expected_generation if expected_generation is None else expected_generation
        max_age_seconds = contract.max_age_seconds if max_age_seconds is None else max_age_seconds
        fingerprints = None
    stages, identity, reasons = collect_stages(fingerprints, configured, deployed, running, source=source, model_source=model_source, source_fingerprint=source_fingerprint, model_source_fingerprint=model_source_fingerprint, configured_fingerprint=configured_fingerprint, deployed_fingerprint=deployed_fingerprint, running_fingerprint=running_fingerprint)
    result = evaluate_stages(stages, identity, provenance=provenance, required_capabilities=required_capabilities, expected_capabilities=expected_capabilities, capabilities=capabilities, configured_capabilities=configured_capabilities, deployed_capabilities=deployed_capabilities, running_capabilities=running_capabilities, expected_generation=expected_generation, now=now, max_age_seconds=max_age_seconds)
    if reasons:
        codes = tuple(dict.fromkeys((*reasons, *result.reason_codes)))
        return ActiveRuntimeEligibility(not codes, codes, result.fingerprints, result.provenance_valid, result.capability_parity, result.external_evidence_present)
    return result
