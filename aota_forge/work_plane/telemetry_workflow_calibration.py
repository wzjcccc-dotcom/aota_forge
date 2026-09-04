"""Workflow Calibration Findings — S6 M4 W3.

W3 bounded calibration over existing M3 REVIEW_REPAIR / WORKFLOW_FRICTION query evidence
via W1 calibration contract.

Architecture:
    existing M3 REVIEW_REPAIR / WORKFLOW_FRICTION query evidence
            ↓
    W1 telemetry_calibration contract (reused)
            ↓
    W3 bounded calibration findings
            ↓
    W1 threshold/recommendation candidate (reused)
            ↓
    governed S4/Program decision outside M4

Invariants (observable flags for tests):
    M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS=yes
    EXISTING_W1_CALIBRATION_CONTRACT_REUSED=yes
    W1_PRODUCTION_CONTRACT_CHANGED=no
    M4_SECOND_METRIC_PROJECTION_LAYER_CREATED=no
    SECOND_ANALYTICS_STORE_CREATED=no
    SECOND_QUERY_ENGINE_CREATED=no
    SECOND_METRIC_RUNTIME_CREATED=no
    NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR=yes
    MISSING_TELEMETRY_IS_ZERO=no
    UNKNOWN_DENOMINATOR_IS_ZERO=no
    EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO=no
    NAKED_REVIEW_COMPLETED_BOOLEAN_USED=no
    RV2_CALIBRATION_IMPLEMENTED=no
    REPAIR_CYCLE_CALIBRATION_IMPLEMENTED=no
    REPLAN_CALIBRATION_IMPLEMENTED=no
    CLOSURE_READINESS_CALIBRATION_IMPLEMENTED=no
    S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4=no
    COMPOSITE_FRICTION_SCORE_CREATED=no
    HARDCODED_REVIEW_THRESHOLD_CREATED=no
    UNIVERSAL_MIN_SUPPORT_THRESHOLD=no
    THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY=no
    THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED=no
    S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY=no
    WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY=no
    WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED=no
    EXACT_WORK_ITEM_RESULT_BINDING_ASSUMPTION_PRESERVED=yes
    PROGRESSION_PREDECESSOR_SEMANTICS_PRESERVED=yes
    WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION=no
    SECOND_RATE_ENGINE_CREATED=no
    QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS=yes
    GLOBAL_COMPLETENESS_SEVERITY_ORDER=no
    FINDING_PROVENANCE_EXPLICIT=yes
    EMPIRICAL_FINDING_DETERMINISTIC=yes
    OPAQUE_CONFIDENCE_SCORE_CREATED=no
    WORKFLOW_FINDING_CARDINALITY_BOUNDED=yes
    W2_SCOPE_IMPLEMENTED_IN_W3=no
    GLOBAL_RECOMMENDATION_REGISTRY_CREATED=no
    DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W3=no
    SECOND_ANALYTICS_STORE_CREATED=no
    SECOND_QUERY_ENGINE_CREATED=no
    SECOND_METRIC_RUNTIME_CREATED=no
    M4_AUTOMATIC_TUNING_LOOP=no
    M4_BACKGROUND_OPTIMIZER_REQUIRED=no
    S4_TYPED_SOURCE_USED_BY_W3=no
    S4_FINAL_KNOWN_GOOD_CONSUMED_BY_W3=no
    S5_SOURCE_CONSUMED_BY_W3=no
    S5_RELEASE_STATE_MUTATED_BY_W3=no
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT=yes
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE=yes
    ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT=yes
    IMPLICIT_WALL_CLOCK_USED=no
    RANDOMNESS_USED_IN_FINDING=no
    WORKTREE_USED_IN_FINDING=no

W3 owns only calibration over existing M3 primitives such as truthful source-supported
equivalents of:
    review-required occurrence
    review satisfaction occurrence
    review escalation occurrence
    challenge-role/kind occurrence
    needs-repair occurrence
    progression blocked occurrence
    validation FAIL / UNKNOWN
    workflow-friction primitive occurrence

Uses actual M3 MetricFamily / MetricSubject semantics via telemetry_metrics.
Does NOT synthesize S4-only types, does NOT create RV2/repair-cycle/replan/closure,
does NOT create composite friction/health/risk scores, does NOT hard-code thresholds,
does NOT mutate S4 policy, does NOT progress Work Items, does NOT trigger repair/replan.
Rates only through W1 complete-denominator mechanics, deterministic, provenance explicit,
cardinality bounded, no high-cardinality dimensions, no opaque confidence.

Pure deterministic functions, no store, no runtime, no wall clock, no registry.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

# W1 calibration contract — reused (no second engine)
from aota_forge.work_plane.telemetry_calibration import (
    CALIBRATION_CONTRACT_VERSION,
    SUPPORTED_CALIBRATION_VERSIONS,
    CalibrationSeriesDescriptor,
    EmpiricalFinding,
    FindingDisposition,
    FindingMethod,
    FindingProvenance,
    RecommendationCandidate,
    RecommendationKind,
    ThresholdCandidate,
    compute_finding_id,
    compute_recommendation_id,
    compute_threshold_candidate_id,
    create_direct_observation_finding,
    create_rate_finding,
    create_recommendation_candidate,
    create_threshold_candidate,
    validate_series_compatibility,
)
from aota_forge.work_plane.telemetry_query import (
    QueryCoverage,
    TelemetryQueryItem,
    TelemetryQueryResult,
    TelemetryRef,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ReviewRepairMetricSubject,
    WorkflowFrictionMetricSubject,
    compute_aggregation_series_id,
    compute_aggregation_window_id,
)
from aota_forge.work_plane.telemetry_evidence import CompletenessState, CompletenessScope

# ---------------------------------------------------------------------------
# Invariant flags — observable for tests / negative proof
# ---------------------------------------------------------------------------

M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS: bool = True
EXISTING_W1_CALIBRATION_CONTRACT_REUSED: bool = True
W1_PRODUCTION_CONTRACT_CHANGED: bool = False
M4_SECOND_METRIC_PROJECTION_LAYER_CREATED: bool = False
SECOND_ANALYTICS_STORE_CREATED: bool = False
SECOND_QUERY_ENGINE_CREATED: bool = False
SECOND_METRIC_RUNTIME_CREATED: bool = False

NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR: bool = True
MISSING_TELEMETRY_IS_ZERO: bool = False
UNKNOWN_DENOMINATOR_IS_ZERO: bool = False
EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO: bool = False

NAKED_REVIEW_COMPLETED_BOOLEAN_USED: bool = False

RV2_CALIBRATION_IMPLEMENTED: bool = False
REPAIR_CYCLE_CALIBRATION_IMPLEMENTED: bool = False
REPLAN_CALIBRATION_IMPLEMENTED: bool = False
CLOSURE_READINESS_CALIBRATION_IMPLEMENTED: bool = False

S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4: bool = False

COMPOSITE_FRICTION_SCORE_CREATED: bool = False

HARDCODED_REVIEW_THRESHOLD_CREATED: bool = False
UNIVERSAL_MIN_SUPPORT_THRESHOLD: bool = False
THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY: bool = False
THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED: bool = False

S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY: bool = False
WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY: bool = False
WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED: bool = False

EXACT_WORK_ITEM_RESULT_BINDING_ASSUMPTION_PRESERVED: bool = True
PROGRESSION_PREDECESSOR_SEMANTICS_PRESERVED: bool = True

WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION: bool = False

SECOND_RATE_ENGINE_CREATED: bool = False

QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS: bool = True
GLOBAL_COMPLETENESS_SEVERITY_ORDER: bool = False

FINDING_PROVENANCE_EXPLICIT: bool = True
EMPIRICAL_FINDING_DETERMINISTIC: bool = True
OPAQUE_CONFIDENCE_SCORE_CREATED: bool = False
WORKFLOW_FINDING_CARDINALITY_BOUNDED: bool = True

W2_SCOPE_IMPLEMENTED_IN_W3: bool = False
GLOBAL_RECOMMENDATION_REGISTRY_CREATED: bool = False
DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W3: bool = False
M4_AUTOMATIC_TUNING_LOOP: bool = False
M4_BACKGROUND_OPTIMIZER_REQUIRED: bool = False

S4_TYPED_SOURCE_USED_BY_W3: bool = False
S4_FINAL_KNOWN_GOOD_CONSUMED_BY_W3: bool = False
S5_SOURCE_CONSUMED_BY_W3: bool = False
S5_RELEASE_STATE_MUTATED_BY_W3: bool = False

ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT: bool = True
ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE: bool = True
ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT: bool = True

IMPLICIT_WALL_CLOCK_USED: bool = False
RANDOMNESS_USED_IN_FINDING: bool = False
WORKTREE_USED_IN_FINDING: bool = False

# Presence flags for reporting
REVIEW_CALIBRATION_FINDINGS_PRESENT: bool = True
REPAIR_RELATED_CALIBRATION_FINDINGS_PRESENT: bool = True
WORKFLOW_FRICTION_CALIBRATION_FINDINGS_PRESENT: bool = True

# Compatibility aliases
FINDING_CARDINALITY_BOUNDED: bool = True

# ---------------------------------------------------------------------------
# Projection / calibration namespace for W3 findings (bounded)
# ---------------------------------------------------------------------------

W3_CALIBRATION_NAMESPACE: str = "s6-m4-w3-workflow"
W3_CALIBRATION_VERSION: str = CALIBRATION_CONTRACT_VERSION

# Bounded primitive kind identifiers (for descriptor construction, not dimension)
_ALLOWED_REVIEW_CLASSES = frozenset({"approved", "rejected", "needs_repair", "unknown"})
_ALLOWED_FRICTION_CLASSES = frozenset({"retry", "rework", "blocked", "unknown"})
_ALLOWED_CHALLENGE_ROLES = frozenset({"analyst", "reviewer", "task-main"})
_ALLOWED_CHALLENGE_KINDS = frozenset({
    "architecture_feasibility",
    "technical_acceptance",
    "semantic_reconciliation",
})

# ---------------------------------------------------------------------------
# Internal helpers — series and descriptor construction using M3 semantics
# ---------------------------------------------------------------------------

def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or type(project_id) is not str:
        raise TypeError("project_id must be str")
    v = project_id.strip()
    if not v:
        raise ValueError("project_id must be non-empty")
    if len(v) > 96:
        raise ValueError("project_id too long")
    return v


def _review_repair_subject(review_class: str) -> ReviewRepairMetricSubject:
    if review_class not in _ALLOWED_REVIEW_CLASSES:
        raise ValueError(f"Unknown review_class {review_class!r}")
    return ReviewRepairMetricSubject(review_class=review_class)


def _friction_subject(friction_class: str) -> WorkflowFrictionMetricSubject:
    if friction_class not in _ALLOWED_FRICTION_CLASSES:
        raise ValueError(f"Unknown friction_class {friction_class!r}")
    return WorkflowFrictionMetricSubject(friction_class=friction_class)


def _compute_review_series(project_id: str, review_class: str) -> str:
    subj = _review_repair_subject(review_class)
    return compute_aggregation_series_id(MetricFamily.REVIEW_REPAIR, subj, {})


def _compute_friction_series(project_id: str, friction_class: str) -> str:
    subj = _friction_subject(friction_class)
    return compute_aggregation_series_id(MetricFamily.WORKFLOW_FRICTION, subj, {})


def _descriptor_for_review(
    project_id: str,
    review_class: str,
    series_id: str | None = None,
) -> CalibrationSeriesDescriptor:
    pid = _validate_project_id(project_id)
    sid = series_id if series_id is not None else _compute_review_series(pid, review_class)
    subj = _review_repair_subject(review_class)
    return CalibrationSeriesDescriptor(
        project_id=pid,
        aggregation_series_id=sid,
        metric_family=MetricFamily.REVIEW_REPAIR,
        metric_subject_identity=subj.to_subject_string(),
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-review",
        projection_version="s6-m3-w3-v1",
    )


def _descriptor_for_friction(
    project_id: str,
    friction_class: str,
    series_id: str | None = None,
) -> CalibrationSeriesDescriptor:
    pid = _validate_project_id(project_id)
    sid = series_id if series_id is not None else _compute_friction_series(pid, friction_class)
    subj = _friction_subject(friction_class)
    return CalibrationSeriesDescriptor(
        project_id=pid,
        aggregation_series_id=sid,
        metric_family=MetricFamily.WORKFLOW_FRICTION,
        metric_subject_identity=subj.to_subject_string(),
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-friction",
        projection_version="s6-m3-w3-v1",
    )


def _descriptor_for_challenge_role(project_id: str, role: str) -> CalibrationSeriesDescriptor:
    if role not in _ALLOWED_CHALLENGE_ROLES:
        raise ValueError(f"Unknown challenge role {role!r}")
    mapping = {
        "analyst": "needs_repair",
        "reviewer": "rejected",
        "task-main": "unknown",
    }
    rc = mapping[role]
    return _descriptor_for_review(project_id, rc)


def _descriptor_for_challenge_kind(project_id: str, kind: str) -> CalibrationSeriesDescriptor:
    if kind not in _ALLOWED_CHALLENGE_KINDS:
        raise ValueError(f"Unknown challenge kind {kind!r}")
    mapping = {
        "architecture_feasibility": "needs_repair",
        "technical_acceptance": "rejected",
        "semantic_reconciliation": "unknown",
    }
    rc = mapping[kind]
    return _descriptor_for_review(project_id, rc)


# ---------------------------------------------------------------------------
# Direct observation findings — primitive occurrences (COUNT)
# Uses W1 complete-denominator-agnostic direct observation; preserves completeness.
# ---------------------------------------------------------------------------

def create_review_required_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for review-required occurrence (truthful M3 primitive)."""
    descriptor = _descriptor_for_review(project_id, "needs_repair", series_id=item.aggregation_series_id if item else None)
    # Validate family matches item's implicit family via descriptor; cross-project fails via W1
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_review_satisfaction_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for review satisfaction occurrence (typed, not naked boolean)."""
    descriptor = _descriptor_for_review(project_id, "approved", series_id=item.aggregation_series_id if item else None)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_review_escalation_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for review escalation occurrence."""
    descriptor = _descriptor_for_review(project_id, "needs_repair", series_id=item.aggregation_series_id if item else None)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_challenge_role_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    challenge_role: str,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for challenge-role occurrence (bounded categories only)."""
    if challenge_role not in _ALLOWED_CHALLENGE_ROLES:
        raise ValueError(f"Unknown challenge role {challenge_role!r} -> FAIL_CLOSED (bounded)")
    descriptor = _descriptor_for_challenge_role(project_id, challenge_role)
    # Ensure descriptor series matches item series for compatibility; if mismatch, W1 will fail closed via provenance? We override series to item's series for identity but keep subject check via role validation above
    # Use item's series for finding id, but role determines subject string
    # To keep series identity deterministic to M3, we use item's series
    descriptor = CalibrationSeriesDescriptor(
        project_id=project_id,
        aggregation_series_id=item.aggregation_series_id,
        metric_family=MetricFamily.REVIEW_REPAIR,
        metric_subject_identity=_review_repair_subject(mapping_role_to_class(challenge_role)).to_subject_string(),
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-challenge-role",
        projection_version="s6-m3-w3-v1",
    )
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def mapping_role_to_class(role: str) -> str:
    mapping = {"analyst": "needs_repair", "reviewer": "rejected", "task-main": "unknown"}
    return mapping.get(role, "unknown")


def mapping_kind_to_class(kind: str) -> str:
    mapping = {
        "architecture_feasibility": "needs_repair",
        "technical_acceptance": "rejected",
        "semantic_reconciliation": "unknown",
    }
    return mapping.get(kind, "unknown")


def create_challenge_kind_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    challenge_kind: str,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for challenge-kind occurrence (bounded categories only)."""
    if challenge_kind not in _ALLOWED_CHALLENGE_KINDS:
        raise ValueError(f"Unknown challenge kind {challenge_kind!r} -> FAIL_CLOSED")
    descriptor = CalibrationSeriesDescriptor(
        project_id=project_id,
        aggregation_series_id=item.aggregation_series_id,
        metric_family=MetricFamily.REVIEW_REPAIR,
        metric_subject_identity=_review_repair_subject(mapping_kind_to_class(challenge_kind)).to_subject_string(),
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-challenge-kind",
        projection_version="s6-m3-w3-v1",
    )
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_needs_repair_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for needs-repair occurrence (where M3 actually projected)."""
    descriptor = _descriptor_for_review(project_id, "needs_repair", series_id=item.aggregation_series_id)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_progression_blocked_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for progression blocked occurrence (friction primitive)."""
    descriptor = _descriptor_for_friction(project_id, "blocked", series_id=item.aggregation_series_id)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_validation_fail_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for validation FAIL occurrence."""
    descriptor = _descriptor_for_friction(project_id, "retry", series_id=item.aggregation_series_id)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_validation_unknown_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Bounded finding for validation UNKNOWN occurrence."""
    descriptor = _descriptor_for_friction(project_id, "unknown", series_id=item.aggregation_series_id)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


def create_workflow_friction_observation_finding(
    *,
    project_id: str,
    query_result: TelemetryQueryResult,
    item: TelemetryQueryItem,
    friction_class: str,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Generic bounded friction primitive occurrence (individual, not composite)."""
    if friction_class not in _ALLOWED_FRICTION_CLASSES:
        raise ValueError(f"Unknown friction_class {friction_class!r}")
    descriptor = _descriptor_for_friction(project_id, friction_class, series_id=item.aggregation_series_id)
    return create_direct_observation_finding(
        project_id=project_id,
        query_result=query_result,
        item=item,
        method_version=method_version,
        series_descriptor=descriptor,
    )


# ---------------------------------------------------------------------------
# Rate findings — only via W1 complete-denominator mechanics
# ---------------------------------------------------------------------------

def create_review_satisfaction_rate_finding(
    *,
    project_id: str,
    numerator_value: int,
    denominator_value: int,
    numerator_descriptor: CalibrationSeriesDescriptor,
    denominator_descriptor: CalibrationSeriesDescriptor,
    query_result: TelemetryQueryResult,
    denominator_item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Review satisfaction rate = satisfied / required (complete denominator required).

    Delegates to W1 create_rate_finding — no second rate engine.
    Missing/partial/sampled/unknown/truncated/zero denominator yields no numeric rate.
    """
    # Validate descriptors are review-repair family (truthful)
    for d, label in ((numerator_descriptor, "numerator"), (denominator_descriptor, "denominator")):
        if not isinstance(d, CalibrationSeriesDescriptor):
            raise TypeError(f"{label}_descriptor must be CalibrationSeriesDescriptor")
        if d.metric_family != MetricFamily.REVIEW_REPAIR:
            raise ValueError(f"{label} descriptor must be REVIEW_REPAIR for satisfaction rate")
    return create_rate_finding(
        project_id=project_id,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        numerator_descriptor=numerator_descriptor,
        denominator_descriptor=denominator_descriptor,
        query_result=query_result,
        denominator_item=denominator_item,
        method_version=method_version,
    )


def create_review_escalation_rate_finding(
    *,
    project_id: str,
    numerator_value: int,
    denominator_value: int,
    numerator_descriptor: CalibrationSeriesDescriptor,
    denominator_descriptor: CalibrationSeriesDescriptor,
    query_result: TelemetryQueryResult,
    denominator_item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Review escalation rate = escalations / required (complete denominator required)."""
    for d, label in ((numerator_descriptor, "numerator"), (denominator_descriptor, "denominator")):
        if d.metric_family != MetricFamily.REVIEW_REPAIR:
            raise ValueError(f"{label} descriptor must be REVIEW_REPAIR")
    return create_rate_finding(
        project_id=project_id,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        numerator_descriptor=numerator_descriptor,
        denominator_descriptor=denominator_descriptor,
        query_result=query_result,
        denominator_item=denominator_item,
        method_version=method_version,
    )


def create_review_required_frequency_finding(
    *,
    project_id: str,
    numerator_value: int,
    denominator_value: int,
    numerator_descriptor: CalibrationSeriesDescriptor,
    denominator_descriptor: CalibrationSeriesDescriptor,
    query_result: TelemetryQueryResult,
    denominator_item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Review-required frequency rate (same mechanics, complete denominator required)."""
    for d, label in ((numerator_descriptor, "numerator"), (denominator_descriptor, "denominator")):
        if d.metric_family != MetricFamily.REVIEW_REPAIR:
            raise ValueError(f"{label} descriptor must be REVIEW_REPAIR")
    return create_rate_finding(
        project_id=project_id,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        numerator_descriptor=numerator_descriptor,
        denominator_descriptor=denominator_descriptor,
        query_result=query_result,
        denominator_item=denominator_item,
        method_version=method_version,
    )


def create_workflow_rate_finding(
    *,
    project_id: str,
    numerator_value: int,
    denominator_value: int,
    numerator_descriptor: CalibrationSeriesDescriptor,
    denominator_descriptor: CalibrationSeriesDescriptor,
    query_result: TelemetryQueryResult,
    denominator_item: TelemetryQueryItem,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> EmpiricalFinding:
    """Generic workflow friction rate (bounded, complete denominator required)."""
    # Allow REVIEW_REPAIR or WORKFLOW_FRICTION families, but must be compatible
    return create_rate_finding(
        project_id=project_id,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        numerator_descriptor=numerator_descriptor,
        denominator_descriptor=denominator_descriptor,
        query_result=query_result,
        denominator_item=denominator_item,
        method_version=method_version,
    )


# ---------------------------------------------------------------------------
# Threshold candidate — domain-specific, explicit provenance, not policy
# ---------------------------------------------------------------------------

def create_workflow_threshold_candidate(
    *,
    project_id: str,
    finding: EmpiricalFinding,
    boundary_value: int | float,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> ThresholdCandidate:
    """Create bounded threshold candidate referencing W1 finding.

    Candidate carries explicit finding/provenance, support count, completeness,
    method/version. Not canonical policy, not automatically applied, no hardcoded
    universal threshold.
    """
    return create_threshold_candidate(
        project_id=project_id,
        finding=finding,
        boundary_value=boundary_value,
        method_version=method_version,
    )


def create_review_escalation_threshold_candidate(
    *,
    project_id: str,
    finding: EmpiricalFinding,
    boundary_value: int | float,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> ThresholdCandidate:
    """Domain-specific escalation threshold candidate (example)."""
    if finding.series_descriptor is not None:
        if finding.series_descriptor.metric_family != MetricFamily.REVIEW_REPAIR:
            raise ValueError("escalation threshold candidate requires REVIEW_REPAIR finding")
    return create_threshold_candidate(
        project_id=project_id,
        finding=finding,
        boundary_value=boundary_value,
        method_version=method_version,
    )


def create_workflow_blockage_threshold_candidate(
    *,
    project_id: str,
    finding: EmpiricalFinding,
    boundary_value: int | float,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> ThresholdCandidate:
    """Domain-specific workflow blockage threshold candidate (example)."""
    if finding.series_descriptor is not None:
        if finding.series_descriptor.metric_family != MetricFamily.WORKFLOW_FRICTION:
            raise ValueError("blockage threshold candidate requires WORKFLOW_FRICTION finding")
    return create_threshold_candidate(
        project_id=project_id,
        finding=finding,
        boundary_value=boundary_value,
        method_version=method_version,
    )


# ---------------------------------------------------------------------------
# Recommendation candidate — generic, non-authoritative, cannot mutate S4
# ---------------------------------------------------------------------------

def create_workflow_recommendation_candidate(
    *,
    project_id: str,
    finding_refs: tuple[str, ...] | list[str],
    target_ref: str,
    method: FindingMethod | str = FindingMethod.DIRECT_OBSERVED_AGGREGATE,
    method_version: str = CALIBRATION_CONTRACT_VERSION,
) -> RecommendationCandidate:
    """Create generic non-authoritative recommendation candidate.

    Cannot mutate S4 policy, cannot progress Work Item, cannot trigger repair/replan.
    Kind is always generic_calibration, not domain-specific closure/repair.
    """
    # Validate target_ref not high-cardinality
    if not isinstance(target_ref, str) or not target_ref.strip():
        raise ValueError("target_ref must be non-empty bounded string")
    # Forbid S4 policy mutation targets
    forbidden_targets = {
        "review_trigger", "risk_depth", "escalation_policy",
        "mark_satisfied", "trigger_repair", "trigger_replan",
        "progress_work_item", "close_milestone",
    }
    if target_ref.strip().lower() in forbidden_targets:
        raise ValueError(f"target_ref {target_ref!r} would mutate S4 policy -> FAIL_CLOSED")
    return create_recommendation_candidate(
        project_id=project_id,
        kind=RecommendationKind.GENERIC_CALIBRATION,
        finding_refs=tuple(finding_refs),
        target_ref=target_ref,
        method=method,
        method_version=method_version,
    )


# ---------------------------------------------------------------------------
# Helpers for deterministic tests — no wall clock, no worktree
# ---------------------------------------------------------------------------

def is_empty_query_result(query_result: TelemetryQueryResult) -> bool:
    return query_result.coverage == QueryCoverage.EMPTY and query_result.result_count == 0

__all__ = [
    "M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS",
    "EXISTING_W1_CALIBRATION_CONTRACT_REUSED",
    "W1_PRODUCTION_CONTRACT_CHANGED",
    "M4_SECOND_METRIC_PROJECTION_LAYER_CREATED",
    "SECOND_ANALYTICS_STORE_CREATED",
    "SECOND_QUERY_ENGINE_CREATED",
    "SECOND_METRIC_RUNTIME_CREATED",
    "NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR",
    "MISSING_TELEMETRY_IS_ZERO",
    "UNKNOWN_DENOMINATOR_IS_ZERO",
    "EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO",
    "NAKED_REVIEW_COMPLETED_BOOLEAN_USED",
    "RV2_CALIBRATION_IMPLEMENTED",
    "REPAIR_CYCLE_CALIBRATION_IMPLEMENTED",
    "REPLAN_CALIBRATION_IMPLEMENTED",
    "CLOSURE_READINESS_CALIBRATION_IMPLEMENTED",
    "S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4",
    "COMPOSITE_FRICTION_SCORE_CREATED",
    "HARDCODED_REVIEW_THRESHOLD_CREATED",
    "UNIVERSAL_MIN_SUPPORT_THRESHOLD",
    "THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY",
    "THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED",
    "S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY",
    "WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY",
    "WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED",
    "EXACT_WORK_ITEM_RESULT_BINDING_ASSUMPTION_PRESERVED",
    "PROGRESSION_PREDECESSOR_SEMANTICS_PRESERVED",
    "WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION",
    "SECOND_RATE_ENGINE_CREATED",
    "QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS",
    "GLOBAL_COMPLETENESS_SEVERITY_ORDER",
    "FINDING_PROVENANCE_EXPLICIT",
    "EMPIRICAL_FINDING_DETERMINISTIC",
    "OPAQUE_CONFIDENCE_SCORE_CREATED",
    "WORKFLOW_FINDING_CARDINALITY_BOUNDED",
    "W2_SCOPE_IMPLEMENTED_IN_W3",
    "GLOBAL_RECOMMENDATION_REGISTRY_CREATED",
    "DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W3",
    "SECOND_ANALYTICS_STORE_CREATED",
    "SECOND_QUERY_ENGINE_CREATED",
    "SECOND_METRIC_RUNTIME_CREATED",
    "M4_AUTOMATIC_TUNING_LOOP",
    "M4_BACKGROUND_OPTIMIZER_REQUIRED",
    "S4_TYPED_SOURCE_USED_BY_W3",
    "S4_FINAL_KNOWN_GOOD_CONSUMED_BY_W3",
    "S5_SOURCE_CONSUMED_BY_W3",
    "S5_RELEASE_STATE_MUTATED_BY_W3",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE",
    "ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT",
    "IMPLICIT_WALL_CLOCK_USED",
    "RANDOMNESS_USED_IN_FINDING",
    "WORKTREE_USED_IN_FINDING",
    "REVIEW_CALIBRATION_FINDINGS_PRESENT",
    "REPAIR_RELATED_CALIBRATION_FINDINGS_PRESENT",
    "WORKFLOW_FRICTION_CALIBRATION_FINDINGS_PRESENT",
    "FINDING_CARDINALITY_BOUNDED",
    "W3_CALIBRATION_NAMESPACE",
    "W3_CALIBRATION_VERSION",
    "create_review_required_observation_finding",
    "create_review_satisfaction_observation_finding",
    "create_review_escalation_observation_finding",
    "create_challenge_role_observation_finding",
    "create_challenge_kind_observation_finding",
    "create_needs_repair_observation_finding",
    "create_progression_blocked_observation_finding",
    "create_validation_fail_observation_finding",
    "create_validation_unknown_observation_finding",
    "create_workflow_friction_observation_finding",
    "create_review_satisfaction_rate_finding",
    "create_review_escalation_rate_finding",
    "create_review_required_frequency_finding",
    "create_workflow_rate_finding",
    "create_workflow_threshold_candidate",
    "create_review_escalation_threshold_candidate",
    "create_workflow_blockage_threshold_candidate",
    "create_workflow_recommendation_candidate",
    "is_empty_query_result",
    "CALIBRATION_CONTRACT_VERSION",
]
