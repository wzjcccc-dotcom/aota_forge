"""M3-BG2 Authority-Cutover Readiness Gate Model & Invariants.

Scope (Issue #9, Gate M3-BG2):
- Gate decision enums, eligibility input modes, and proof statuses.
- Exact governance constants and invariants freezing cutover execution prohibitions.
- Assertion that BG2 is a readiness gate only and cannot authorize or perform cutover.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

# Governance and Gate Identity Constants
PROJECT_ID: Final[str] = "aota_forge"
MILESTONE: Final[str] = "M3"
PHASE: Final[str] = "M3-B"
GATE_ID: Final[str] = "M3-BG2"

# Authorization & Boundary Invariants
M3_BG2_EXECUTION_AUTHORIZED: Final[bool] = True
M3_B14_EXECUTION_AUTHORIZED: Final[bool] = False
BG2_IS_GATE: Final[bool] = True
BG2_IS_CUTOVER_EXECUTION: Final[bool] = False
CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED: Final[bool] = True
CUTOVER_AUTHORIZED: Final[bool] = False
CUTOVER_PERFORMED: Final[bool] = False
CUTOVER_COMPLETED: Final[bool] = False
PRODUCTION_GRAPH_AUTHORITY_ACTIVE: Final[bool] = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED: Final[bool] = False
ACTUAL_CUTOVER_REQUIRES_SEPARATE_GOVERNANCE_TRANSITION: Final[bool] = True
ACTUAL_CUTOVER_REQUIRES_EXPLICIT_AUTHORIZATION: Final[bool] = True
DEPLOY_PERFORMED: Final[bool] = False
RUNTIME_RELOAD_PERFORMED: Final[bool] = False
PRODUCTION_CUTOVER_SERVICE_ACTIVATED: Final[bool] = False
LIVE_PRODUCTION_CUTOVER_PERFORMED: Final[bool] = False
LIVE_PRODUCTION_MIGRATION_PERFORMED: Final[bool] = False
PUSH_PERFORMED: Final[bool] = False
GITHUB_MUTATION_PERFORMED: Final[bool] = False

# Production Isolation Metrics
BG2_PRODUCTION_GRAPH_WRITE_COUNT: Final[int] = 0
BG2_PRODUCTION_BINDING_WRITE_COUNT: Final[int] = 0
BG2_PRODUCTION_REVISION_WRITE_COUNT: Final[int] = 0
BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT: Final[int] = 0

# Checkpoints
EXPECTED_BASE_COMMIT: Final[str] = "bb38cc24fc3bf6dbb002a4d30c8ceace5cc3eb90"
ACCEPTED_B12_CHECKPOINT: Final[str] = "f39f292038f2663d07ee80546b5ab2ada719214f"
ACCEPTED_B13_CHECKPOINT: Final[str] = "bb38cc24fc3bf6dbb002a4d30c8ceace5cc3eb90"

# Gate Feature Declarations
BG2_GATE_REPORT_IMPLEMENTED: Final[bool] = True
BG2_GATE_DECISION_MODEL_IMPLEMENTED: Final[bool] = True
BG2_EXACT_CUTOVER_CANDIDATE_MODEL_IMPLEMENTED: Final[bool] = True
BG2_FUTURE_AUTHORIZATION_PACKAGE_IMPLEMENTED: Final[bool] = True
BG2_FUTURE_AUTHORIZATION_PACKAGE_IS_AUTHORIZATION: Final[bool] = False
BG2_CANDIDATE_IS_CUTOVER_AUTHORIZATION: Final[bool] = False


class GateDecision(str, Enum):
    """Explicit deterministic gate decision enum."""

    READY_FOR_EXPLICIT_CUTOVER_AUTHORIZATION = "READY_FOR_EXPLICIT_CUTOVER_AUTHORIZATION"
    READY_WITH_NON_BLOCKING_FINDINGS = "READY_WITH_NON_BLOCKING_FINDINGS"
    NOT_READY = "NOT_READY"
    BLOCKED = "BLOCKED"


class EligibilityInputMode(str, Enum):
    """Bounded evidence input mode for candidate evaluation."""

    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    VALIDATED_SHADOW_CANDIDATE = "validated_shadow_candidate"
    BOUNDED_READONLY_CURRENT_STATE = "bounded_readonly_current_state"
    MIXED_BOUNDED_EVIDENCE = "mixed_bounded_evidence"


class CutoverEligibilityProof(str, Enum):
    """Cutover eligibility proof verdict."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


__all__ = [
    "PROJECT_ID",
    "MILESTONE",
    "PHASE",
    "GATE_ID",
    "M3_BG2_EXECUTION_AUTHORIZED",
    "M3_B14_EXECUTION_AUTHORIZED",
    "BG2_IS_GATE",
    "BG2_IS_CUTOVER_EXECUTION",
    "CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED",
    "CUTOVER_AUTHORIZED",
    "CUTOVER_PERFORMED",
    "CUTOVER_COMPLETED",
    "PRODUCTION_GRAPH_AUTHORITY_ACTIVE",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED",
    "ACTUAL_CUTOVER_REQUIRES_SEPARATE_GOVERNANCE_TRANSITION",
    "ACTUAL_CUTOVER_REQUIRES_EXPLICIT_AUTHORIZATION",
    "DEPLOY_PERFORMED",
    "RUNTIME_RELOAD_PERFORMED",
    "PRODUCTION_CUTOVER_SERVICE_ACTIVATED",
    "LIVE_PRODUCTION_CUTOVER_PERFORMED",
    "LIVE_PRODUCTION_MIGRATION_PERFORMED",
    "PUSH_PERFORMED",
    "GITHUB_MUTATION_PERFORMED",
    "BG2_PRODUCTION_GRAPH_WRITE_COUNT",
    "BG2_PRODUCTION_BINDING_WRITE_COUNT",
    "BG2_PRODUCTION_REVISION_WRITE_COUNT",
    "BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT",
    "EXPECTED_BASE_COMMIT",
    "ACCEPTED_B12_CHECKPOINT",
    "ACCEPTED_B13_CHECKPOINT",
    "BG2_GATE_REPORT_IMPLEMENTED",
    "BG2_GATE_DECISION_MODEL_IMPLEMENTED",
    "BG2_EXACT_CUTOVER_CANDIDATE_MODEL_IMPLEMENTED",
    "BG2_FUTURE_AUTHORIZATION_PACKAGE_IMPLEMENTED",
    "BG2_FUTURE_AUTHORIZATION_PACKAGE_IS_AUTHORIZATION",
    "BG2_CANDIDATE_IS_CUTOVER_AUTHORIZATION",
    "GateDecision",
    "EligibilityInputMode",
    "CutoverEligibilityProof",
]
