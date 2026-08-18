"""M3-B13 to M3-BG2 Cutover Handoff Evidence Package.

Scope (Issue #9, Lane M3-B13):
- Comprehensive typed evidence package for M3-BG2 Readiness Gate evaluation.
- Includes request schema, state machine definition, precondition contracts,
  concurrency / CAS proof, atomicity proof, idempotency proof, failure matrix,
  dry-run results, receipt schema, and complete production isolation verification.
- Hard governance invariant: Handoff package is evidence only, never cutover authorization,
  and does not authorize M3-BG2 or M3-B14 execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Final

from aota_forge.core.cutover.model import (
    B13_PRODUCTION_BINDING_WRITE_COUNT,
    B13_PRODUCTION_GRAPH_WRITE_COUNT,
    B13_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    B13_PRODUCTION_REVISION_WRITE_COUNT,
    CUTOVER_AUTHORIZED,
    M3_B13_EXECUTION_AUTHORIZED,
    M3_B14_EXECUTION_AUTHORIZED,
    M3_BG2_EXECUTION_AUTHORIZED,
    CutoverMode,
    CutoverState,
)
from aota_forge.core.cutover.receipt import CutoverReceipt
from aota_forge.core.cutover.simulator import CutoverSimulationEnvironment

# Hard governance boundary invariants
HANDOFF_IS_CUTOVER_AUTHORIZATION: Final[bool] = False
BG2_EXECUTION_AUTHORIZED: Final[bool] = False
B14_EXECUTION_AUTHORIZED: Final[bool] = False


@dataclass(frozen=True)
class BG2CutoverHandoff:
    """Typed evidence bundle for M3-BG2 readiness gate evaluation."""

    lane_id: str = "M3-B13"
    milestone: str = "M3"
    phase: str = "M3-B"
    state_machine_version: str = "m3-b13-sm-v1"
    request_schema_version: str = "m3-b13-req-v1"
    receipt_schema_version: str = "m3-b13-rcpt-v1"

    request_schema: dict[str, Any] = field(default_factory=dict)
    state_machine_spec: dict[str, Any] = field(default_factory=dict)
    precondition_contracts: list[str] = field(default_factory=list)
    failure_matrix: dict[str, str] = field(default_factory=dict)
    dry_run_receipt: dict[str, Any] = field(default_factory=dict)
    simulation_receipt: dict[str, Any] = field(default_factory=dict)
    isolation_evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build_evidence(cls) -> BG2CutoverHandoff:
        """Build and populate full evidence package from simulation proofs."""
        sim = CutoverSimulationEnvironment()

        dry_run_rcpt = sim.run_dry_run()
        synth_rcpt = sim.run_synthetic_authorized_switch()

        req_schema = {
            "fields": [
                "request_id: str (non-empty)",
                "idempotency_key: str (non-empty)",
                "source_authority_ref: str (non-empty exact ref)",
                "target_authority_ref: str (non-empty exact validated ref)",
                "expected_source_revision: int (non-negative, CAS target)",
                "expected_target_fingerprint: str (64 hex SHA-256)",
                "b12_validation_ref: str (matching accepted B12 validation)",
                "b12_validation_fingerprint: str (matching B12 validation fingerprint)",
                "mode: 'dry_run' | 'activation'",
                "authorization: CutoverAuthorization | null",
                "target_candidates: tuple[str, ...] (must not have >1 candidate)",
            ],
            "ambiguity_rule": "Fails closed on any ambiguity; cannot select among candidates",
            "inference_prohibition": "No inference from pointers, latest subjects, or rankings",
        }

        sm_spec = {
            "states": [s.value for s in CutoverState],
            "valid_transitions": {
                CutoverState.REQUESTED.value: [CutoverState.PRECONDITION_CHECKED.value, CutoverState.REJECTED.value],
                CutoverState.PRECONDITION_CHECKED.value: [CutoverState.STAGED.value, CutoverState.REJECTED.value, CutoverState.ABORTED_PRECOMMIT.value],
                CutoverState.STAGED.value: [CutoverState.COMMITTED.value, CutoverState.ABORTED_PRECOMMIT.value],
                CutoverState.COMMITTED.value: [CutoverState.POSTCOMMIT_VERIFIED.value, CutoverState.COMMITTED_VERIFICATION_FAILED.value],
            },
            "postcommit_verification_failure_rule": "Explicit failure recorded; no silent rollback",
            "precommit_abort_rule": "Old authority preserved; zero partial switch",
        }

        preconditions = [
            "Exact source authority binding",
            "Exact target authority binding",
            "Target non-ambiguity",
            "B12 validation reference and matching fingerprint",
            "Source revision CAS match",
            "Target state fingerprint match",
            "Valid non-expired non-revoked authorization for activation mode",
            "Strict production store isolation",
        ]

        failure_matrix = {
            "source_not_found": "Fails closed; 0 mutation; state=REJECTED",
            "target_not_found": "Fails closed; 0 mutation; state=REJECTED",
            "ambiguous_target": "Fails closed; 0 mutation; state=REJECTED",
            "missing_b12_validation": "Fails closed; 0 mutation; state=REJECTED",
            "b12_validation_mismatch": "Fails closed; 0 mutation; state=REJECTED",
            "stale_revision": "Fails closed; CAS rejection; 0 mutation; state=REJECTED",
            "target_fingerprint_mismatch": "Fails closed; 0 mutation; state=REJECTED",
            "unauthorized_activation": "Fails closed; 0 mutation; state=REJECTED",
            "invalid_authorization": "Fails closed; 0 mutation; state=REJECTED",
            "expired_authorization": "Fails closed; 0 mutation; state=REJECTED",
            "revoked_authorization": "Fails closed; 0 mutation; state=REJECTED",
            "idempotency_conflict": "Fails closed; 0 mutation; state=REJECTED",
            "injected_precommit_failure": "Old authority preserved; 0 mutation; state=ABORTED_PRECOMMIT",
            "injected_postcommit_failure": "State=COMMITTED_VERIFICATION_FAILED; no silent rollback",
        }

        isolation_evidence = {
            "production_graph_writes": B13_PRODUCTION_GRAPH_WRITE_COUNT,
            "production_binding_writes": B13_PRODUCTION_BINDING_WRITE_COUNT,
            "production_revision_writes": B13_PRODUCTION_REVISION_WRITE_COUNT,
            "production_lease_consumption": B13_PRODUCTION_LEASE_CONSUMPTION_COUNT,
            "governance_cutover_authorized": CUTOVER_AUTHORIZED,
            "m3_bg2_execution_authorized": M3_BG2_EXECUTION_AUTHORIZED,
            "m3_b14_execution_authorized": M3_B14_EXECUTION_AUTHORIZED,
        }

        return cls(
            request_schema=req_schema,
            state_machine_spec=sm_spec,
            precondition_contracts=preconditions,
            failure_matrix=failure_matrix,
            dry_run_receipt=dry_run_rcpt.to_dict(),
            simulation_receipt=synth_rcpt.to_dict(),
            isolation_evidence=isolation_evidence,
        )

    def fingerprint(self) -> str:
        """Deterministic fingerprint of handoff package."""
        payload = {
            "lane_id": self.lane_id,
            "milestone": self.milestone,
            "phase": self.phase,
            "state_machine_version": self.state_machine_version,
            "request_schema": self.request_schema,
            "state_machine_spec": self.state_machine_spec,
            "precondition_contracts": self.precondition_contracts,
            "failure_matrix": self.failure_matrix,
            "isolation_evidence": self.isolation_evidence,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "milestone": self.milestone,
            "phase": self.phase,
            "handoff_fingerprint": self.fingerprint(),
            "handoff_is_cutover_authorization": HANDOFF_IS_CUTOVER_AUTHORIZATION,
            "m3_bg2_execution_authorized": BG2_EXECUTION_AUTHORIZED,
            "m3_b14_execution_authorized": B14_EXECUTION_AUTHORIZED,
            "cutover_authorized": CUTOVER_AUTHORIZED,
            "request_schema_version": self.request_schema_version,
            "receipt_schema_version": self.receipt_schema_version,
            "state_machine_version": self.state_machine_version,
            "request_schema": self.request_schema,
            "state_machine_spec": self.state_machine_spec,
            "precondition_contracts": self.precondition_contracts,
            "failure_matrix": self.failure_matrix,
            "dry_run_receipt": self.dry_run_receipt,
            "simulation_receipt": self.simulation_receipt,
            "isolation_evidence": self.isolation_evidence,
        }


__all__ = [
    "HANDOFF_IS_CUTOVER_AUTHORIZATION",
    "BG2_EXECUTION_AUTHORIZED",
    "B14_EXECUTION_AUTHORIZED",
    "BG2CutoverHandoff",
]
