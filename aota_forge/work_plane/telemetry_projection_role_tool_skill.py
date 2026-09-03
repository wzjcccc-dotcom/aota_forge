"""Role / Tool / Skill Metric Projections — S6 M3 W1.

Pure projection of accepted Role/Tool/Skill evidence into metric families:

    accepted TelemetryEvidence (with source_dedup_id, completeness, temporal)
        +
    accepted typed evidence (AgentWorkRole / ToolUsageObservation / ToolResultGovernance / SkillIdentity / SkillUsageObservation)
        ↓
    M3 pure projector (telemetry_projection_core)
        ↓
    existing M1 metric taxonomy / identity (MetricFamily, MetricSubject, dimensions)
        ↓
    existing M2 AggregationContribution (COUNT primitive)

Constraints
-----------
* M3_PROJECTION_LAYER_ONLY=yes, no second runtime/store/query
* Uses canonical AgentWorkRole, bounded Tool identity (operation_name), canonical S3 SkillIdentity
* SKILL_METRIC_USES_CANONICAL_S3_IDENTITY=yes
* Primitive COUNT only, no rate, no calibration, no recommendation
* Role metric non-authority, Tool not operation authority, Skill not skill authority
* No raw payload, no argv, no secret, bounded cardinality
* Cross-project fail-closed, domain source binding verified
* Deterministic, fail-closed, no wall clock
* W2/W3 scope not implemented, S4 workflow not used
"""

from __future__ import annotations

from typing import Any, Mapping

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill import SkillIdentity
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from aota_forge.work_plane.telemetry_evidence import TelemetryEvidenceEnvelope
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    RoleMetricSubject,
    SkillMetricSubject,
    ToolMetricSubject,
)
from aota_forge.work_plane.telemetry_aggregation import AggregateOperation, AggregationContribution
from aota_forge.work_plane.tool_result_governance import ToolOutputRef, ToolResultProjection
from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation

from aota_forge.work_plane.telemetry_projection_core import (
    MetricSourceProvenance,
    ROLE_PROJECTION_NAMESPACE,
    SKILL_PROJECTION_NAMESPACE,
    TOOL_PROJECTION_NAMESPACE,
    W1_PROJECTION_VERSION,
    project_evidence_to_contribution,
)

# ---------------------------------------------------------------------------
# Invariant flags — W1 family specific
# ---------------------------------------------------------------------------

ROLE_PROJECTION_PRESENT: bool = True
TOOL_PROJECTION_PRESENT: bool = True
SKILL_PROJECTION_PRESENT: bool = True

M3_PROJECTION_LAYER_ONLY: bool = True
SECOND_METRIC_RUNTIME_CREATED: bool = False
SECOND_TELEMETRY_STORE_CREATED: bool = False
SECOND_QUERY_ENGINE_CREATED: bool = False

EXISTING_AGGREGATION_CONTRIBUTION_REUSED: bool = True

ROLE_CANONICAL_IDENTITY_REUSED: bool = True
TOOL_CANONICAL_IDENTITY_REUSED: bool = True
SKILL_METRIC_USES_CANONICAL_S3_IDENTITY: bool = True

DOMAIN_SOURCE_BINDING_VERIFIED: bool = True
CROSS_PROJECT_PROJECTION_FAIL_CLOSED: bool = True
PROJECTION_OUTPUT_DETERMINISTIC: bool = True
M3_METRIC_CARDINALITY_BOUNDED: bool = True

ROLE_METRIC_IS_DISPATCH_AUTHORITY: bool = False
ROLE_METRIC_AUTOMATICALLY_SELECTS_WORKER: bool = False
TOOL_METRIC_IS_OPERATION_AUTHORITY: bool = False
TOOL_METRIC_AUTO_PROMOTES_TOOL: bool = False
SKILL_METRIC_IS_SKILL_AUTHORITY: bool = False
SKILL_METRIC_AUTO_RETIRES_SKILL: bool = False

RAW_TOOL_PAYLOAD_CAPTURED: bool = False
RAW_SECRET_CAPTURED: bool = False

PROJECTION_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT: bool = True
PROJECTION_FAILURE_DOES_NOT_REWRITE_TOOL_RESULT: bool = True
PROJECTION_FAILURE_DOES_NOT_REWRITE_WORKER_RESULT: bool = True

W2_SCOPE_IMPLEMENTED_IN_W1: bool = False
S4_WORKFLOW_EVIDENCE_USED_BY_W1: bool = False
W3_SCOPE_IMPLEMENTED_IN_W1: bool = False
SHARED_REGISTRY_UPDATE_IN_W1: bool = False
M3_CALIBRATION_THRESHOLD_CREATED: bool = False
M3_RECOMMENDATION_ENGINE_CREATED: bool = False

PRIMITIVE_METRICS_FIRST: bool = True
RATE_METRIC_IMPLEMENTED_IN_W1: bool = False
TEMPORAL_RATE_OR_LATENCY_METRIC_CREATED: bool = False

# ---------------------------------------------------------------------------
# Helpers — bounded, deterministic, no raw payload
# ---------------------------------------------------------------------------

def _ensure_role(value: object) -> AgentWorkRole:
    return parse_agent_work_role(value)


def _role_subject_from_observation(
    observation: ToolUsageObservation | SkillUsageObservation | AgentWorkRole | str,
) -> RoleMetricSubject:
    # Extract canonical role from observation
    if isinstance(observation, ToolUsageObservation):
        if observation.work_role is None:
            raise ValueError("ToolUsageObservation has no work_role for role projection → FAIL_CLOSED")
        return RoleMetricSubject(role=observation.work_role)
    if isinstance(observation, SkillUsageObservation):
        # SkillUsageObservation.namespace is AgentWorkRole
        return RoleMetricSubject(role=parse_agent_work_role(observation.namespace))
    if isinstance(observation, AgentWorkRole):
        return RoleMetricSubject(role=observation)
    if isinstance(observation, str) and type(observation) is str:
        return RoleMetricSubject(role=parse_agent_work_role(observation))
    raise TypeError(f"Unsupported role source: {type(observation).__name__}")


def _tool_subject_from_observation(
    observation: ToolUsageObservation | ToolResultProjection | ToolOutputRef,
) -> ToolMetricSubject:
    if isinstance(observation, ToolUsageObservation):
        return ToolMetricSubject(operation_name=observation.operation_name)
    if isinstance(observation, ToolResultProjection):
        return ToolMetricSubject(operation_name=observation.capability_name)
    if isinstance(observation, ToolOutputRef):
        # ToolOutputRef has no capability, but we can derive from ref? Not safe.
        # For this projection, we require ToolOutputRef to have been accompanied by capability context.
        # Since ref is like "tool:capability:digest", we could parse capability, but spec says canonical bounded Tool identity required.
        # To avoid guessing, we require that ToolOutputRef alone is not sufficient for tool metric without capability.
        # However for completeness, we try to extract capability from ref prefix before colon
        ref = observation.ref
        # Expected format from tool_result_governance: f"{capability_name}:{digest[:16]}" or similar
        # But ToolOutputRef.ref is arbitrary? In teammate, it's validated bounded but not necessarily capability:digest
        # To avoid arbitrary parsing, we FAIL_CLOSED for ToolOutputRef without explicit capability.
        raise ValueError("ToolOutputRef alone does not carry canonical Tool identity → FAIL_CLOSED (use ToolUsageObservation or ToolResultProjection)")
    raise TypeError(f"Unsupported tool source: {type(observation).__name__}")


def _skill_subject_from_observation(
    observation: SkillUsageObservation | SkillIdentity,
) -> SkillMetricSubject:
    if isinstance(observation, SkillUsageObservation):
        # Build canonical SkillIdentity from observation fields; provenance is bounded required
        prov = observation.provenance if observation.provenance is not None else "skill-observation-provenance"
        ident = SkillIdentity(
            skill_id=observation.skill_id,
            version=observation.version,
            digest=observation.digest,
            provenance=prov,
        )
        return SkillMetricSubject(skill_identity=ident)
    if isinstance(observation, SkillIdentity):
        return SkillMetricSubject(skill_identity=observation)
    raise TypeError(f"Unsupported skill source: {type(observation).__name__}")


def _dimensions_for_role(
    observation: Any,
    completeness: Any,
) -> dict[str, str]:
    # Role primitive: use bounded completeness state/scope as dimensions? But completeness already in contribution.
    # For primitive role observation count, we keep dimensions empty or with outcome_class if available
    # To keep bounded and deterministic, we use empty dict for now.
    # If observation is ToolUsageObservation, we could include outcome_class/side_effect as dimensions, but role family should not mix tool dimensions.
    # Keep empty to avoid cardinality explosion.
    return {}


def _dimensions_for_tool(
    observation: ToolUsageObservation | ToolResultProjection,
) -> dict[str, str]:
    # Use bounded outcome_class and side_effect_class from ToolUsageObservation
    if isinstance(observation, ToolUsageObservation):
        # Map to allowed dimension keys: outcome_class, side_effect_class
        # These are already bounded enums in observation, and validated by telemetry_metrics
        dims: dict[str, str] = {}
        # Only include bounded categories, not raw error
        if observation.outcome_class in {"success", "success_domain_failure", "failure_authority_denied", "failure_invalid_input", "failure_timeout", "failure_provider"}:
            dims["outcome_class"] = observation.outcome_class
        if observation.side_effect in {"read", "write_mutation", "test_execution", "git_read", "shell_process", "unknown"}:
            dims["side_effect_class"] = observation.side_effect
        return dims
    if isinstance(observation, ToolResultProjection):
        # For ToolResultProjection, we have no outcome_class; use generic success? But to keep primitive, empty
        return {}
    return {}


def _dimensions_for_skill(
    observation: SkillUsageObservation,
) -> dict[str, str]:
    # Skill primitive: use skill_metric_kind bounded
    # Map SkillUsageObservation.delivery/selection to skill_metric_kind?
    # telemetry_metrics ALLOWED_SKILL_METRIC_KINDS: skill_selected, skill_delivered_or_loaded, skill_observed_used
    # SkillUsageObservation represents observed usage, so kind = skill_observed_used
    # If delivery is progressive vs eager, could be delivered vs selected, but we treat as observed_used for W1
    dims: dict[str, str] = {}
    # Use observed_used for skill usage observation
    dims["skill_metric_kind"] = "skill_observed_used"
    return dims


# ---------------------------------------------------------------------------
# Public projectors — pure, deterministic, fail-closed
# ---------------------------------------------------------------------------

def project_role_observation(
    evidence: TelemetryEvidenceEnvelope,
    role_observation: ToolUsageObservation | SkillUsageObservation | AgentWorkRole | str,
    *,
    projection_version: str = W1_PROJECTION_VERSION,
    source_provenance: MetricSourceProvenance | str = MetricSourceProvenance.RUNTIME_OBSERVATION,
    normalized_dimensions: Mapping[str, Any] | None = None,
) -> AggregationContribution:
    """Project role observation to AggregationContribution (COUNT primitive).

    Args:
        evidence: TelemetryEvidenceEnvelope (must be from same project and bound to role_observation)
        role_observation: typed domain evidence carrying AgentWorkRole (ToolUsageObservation, SkillUsageObservation, or direct AgentWorkRole)
        projection_version: W1 projection version (bounded)
        source_provenance: must be RUNTIME_OBSERVATION or RESULT_GOVERNANCE_EVIDENCE for W1
        normalized_dimensions: optional bounded dimensions (if None, derived empty)

    Returns:
        AggregationContribution with MetricFamily.ROLE, primitive COUNT.

    Fail-closed on cross-project, binding mismatch, high-cardinality, raw payload.
    """
    subject = _role_subject_from_observation(role_observation)
    # Determine dimensions: if caller provides explicit, use it (validated); else derive empty/bounded
    dims = dict(normalized_dimensions) if normalized_dimensions is not None else _dimensions_for_role(role_observation, evidence.completeness)
    # Ensure we don't leak raw fields: dimensions are validated by core
    return project_evidence_to_contribution(
        evidence,
        metric_family=MetricFamily.ROLE,
        metric_subject=subject,
        source_provenance=source_provenance,
        projection_namespace=ROLE_PROJECTION_NAMESPACE,
        projection_version=projection_version,
        normalized_dimensions=dims,
        operation=AggregateOperation.COUNT,
        value=1,
        domain_evidence=role_observation,
    )


def project_tool_observation(
    evidence: TelemetryEvidenceEnvelope,
    tool_observation: ToolUsageObservation | ToolResultProjection,
    *,
    projection_version: str = W1_PROJECTION_VERSION,
    source_provenance: MetricSourceProvenance | str = MetricSourceProvenance.RUNTIME_OBSERVATION,
    normalized_dimensions: Mapping[str, Any] | None = None,
) -> AggregationContribution:
    """Project tool usage observation to AggregationContribution.

    Supports:
        ToolUsageObservation -> TOOL family, ToolMetricSubject via operation_name
        ToolResultProjection  -> TOOL family, via capability_name (RESULT_GOVERNANCE_EVIDENCE)

    Dimensions derived from bounded outcome/side_effect where applicable.

    Fail-closed on cross-project, binding mismatch, raw payload.
    """
    # Determine provenance automatically if not supplied? But require explicit
    # For ToolResultProjection, expected provenance is RESULT_GOVERNANCE_EVIDENCE
    # Enforce check in core will fail if wrong provenance for family? Actually core allows both RUNTIME and RESULT for TOOL.
    # So we allow caller to specify, but if they pass ToolResultProjection with RUNTIME, it will still be allowed (both are allowed).
    # To be strict, we could auto-select provenance based on type, but we keep explicit.

    subject = _tool_subject_from_observation(tool_observation)
    dims = dict(normalized_dimensions) if normalized_dimensions is not None else _dimensions_for_tool(tool_observation)
    return project_evidence_to_contribution(
        evidence,
        metric_family=MetricFamily.TOOL,
        metric_subject=subject,
        source_provenance=source_provenance,
        projection_namespace=TOOL_PROJECTION_NAMESPACE,
        projection_version=projection_version,
        normalized_dimensions=dims,
        operation=AggregateOperation.COUNT,
        value=1,
        domain_evidence=tool_observation,
    )


def project_tool_result_projection(
    evidence: TelemetryEvidenceEnvelope,
    tool_result_projection: ToolResultProjection,
    *,
    projection_version: str = W1_PROJECTION_VERSION,
    source_provenance: MetricSourceProvenance | str = MetricSourceProvenance.RESULT_GOVERNANCE_EVIDENCE,
    normalized_dimensions: Mapping[str, Any] | None = None,
) -> AggregationContribution:
    """Project governed Tool result projection (RESULT_GOVERNANCE_EVIDENCE)."""
    return project_tool_observation(
        evidence,
        tool_result_projection,
        projection_version=projection_version,
        source_provenance=source_provenance,
        normalized_dimensions=normalized_dimensions,
    )


def project_skill_observation(
    evidence: TelemetryEvidenceEnvelope,
    skill_observation: SkillUsageObservation | SkillIdentity,
    *,
    projection_version: str = W1_PROJECTION_VERSION,
    source_provenance: MetricSourceProvenance | str = MetricSourceProvenance.RUNTIME_OBSERVATION,
    normalized_dimensions: Mapping[str, Any] | None = None,
) -> AggregationContribution:
    """Project skill usage observation to AggregationContribution.

    Uses canonical S3 SkillIdentity (via SkillUsageObservation fields).

    Dimensions include skill_metric_kind bounded.
    """
    subject = _skill_subject_from_observation(skill_observation)
    # Derive dimensions if not provided
    if isinstance(skill_observation, SkillUsageObservation):
        dims = dict(normalized_dimensions) if normalized_dimensions is not None else _dimensions_for_skill(skill_observation)
    else:
        # SkillIdentity alone: use selected?
        dims = dict(normalized_dimensions) if normalized_dimensions is not None else {"skill_metric_kind": "skill_selected"}
    return project_evidence_to_contribution(
        evidence,
        metric_family=MetricFamily.SKILL,
        metric_subject=subject,
        source_provenance=source_provenance,
        projection_namespace=SKILL_PROJECTION_NAMESPACE,
        projection_version=projection_version,
        normalized_dimensions=dims,
        operation=AggregateOperation.COUNT,
        value=1,
        domain_evidence=skill_observation,
    )


# ---------------------------------------------------------------------------
# Additional helpers for completeness / determinism proofs
# ---------------------------------------------------------------------------

def is_projection_deterministic(
    evidence: TelemetryEvidenceEnvelope,
    observation: Any,
    projector: Any,
    version: str = W1_PROJECTION_VERSION,
) -> bool:
    """Check determinism for given evidence+observation+projector."""
    c1 = projector(evidence, observation, projection_version=version)
    c2 = projector(evidence, observation, projection_version=version)
    return c1.projection_id == c2.projection_id and c1.aggregation_series_id == c2.aggregation_series_id


__all__ = [
    "ROLE_PROJECTION_PRESENT",
    "TOOL_PROJECTION_PRESENT",
    "SKILL_PROJECTION_PRESENT",
    "M3_PROJECTION_LAYER_ONLY",
    "SECOND_METRIC_RUNTIME_CREATED",
    "SECOND_TELEMETRY_STORE_CREATED",
    "SECOND_QUERY_ENGINE_CREATED",
    "EXISTING_AGGREGATION_CONTRIBUTION_REUSED",
    "ROLE_CANONICAL_IDENTITY_REUSED",
    "TOOL_CANONICAL_IDENTITY_REUSED",
    "SKILL_METRIC_USES_CANONICAL_S3_IDENTITY",
    "DOMAIN_SOURCE_BINDING_VERIFIED",
    "CROSS_PROJECT_PROJECTION_FAIL_CLOSED",
    "PROJECTION_OUTPUT_DETERMINISTIC",
    "M3_METRIC_CARDINALITY_BOUNDED",
    "ROLE_METRIC_IS_DISPATCH_AUTHORITY",
    "ROLE_METRIC_AUTOMATICALLY_SELECTS_WORKER",
    "TOOL_METRIC_IS_OPERATION_AUTHORITY",
    "TOOL_METRIC_AUTO_PROMOTES_TOOL",
    "SKILL_METRIC_IS_SKILL_AUTHORITY",
    "SKILL_METRIC_AUTO_RETIRES_SKILL",
    "RAW_TOOL_PAYLOAD_CAPTURED",
    "RAW_SECRET_CAPTURED",
    "PROJECTION_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT",
    "W2_SCOPE_IMPLEMENTED_IN_W1",
    "S4_WORKFLOW_EVIDENCE_USED_BY_W1",
    "W3_SCOPE_IMPLEMENTED_IN_W1",
    "SHARED_REGISTRY_UPDATE_IN_W1",
    "M3_CALIBRATION_THRESHOLD_CREATED",
    "M3_RECOMMENDATION_ENGINE_CREATED",
    "PRIMITIVE_METRICS_FIRST",
    "RATE_METRIC_IMPLEMENTED_IN_W1",
    "project_role_observation",
    "project_tool_observation",
    "project_tool_result_projection",
    "project_skill_observation",
    "is_projection_deterministic",
]
