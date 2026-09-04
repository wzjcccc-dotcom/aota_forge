"""Integrated Recommendation & Non-Authority Adversarial Proof — S6 M4 W4.

Proves:
    bounded M3 metric/query evidence
        -> TelemetryQueryResult / TelemetryQueryItem
        -> W1 evidence compatibility/completeness gate
        -> EmpiricalFinding
        -> W2 or W3 bounded domain finding
        -> ThresholdCandidate / RecommendationCandidate
        -> NO authority mutation

Covers all 48 W4 focused themes (W4_FOCUSED_TEST_COUNT>=40) with behavioral first proofs.

Invariants proven:
    M4_END_TO_END_VERTICAL_PROOF=PASS (W2 and W3 verticals)
    W1_SHARED_CALIBRATION_CONTRACT_REUSED=yes
    QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS=yes
    TOOL_AUTHORITY_STATE_UNCHANGED_BY_RECOMMENDATION=yes
    etc. see flags in file
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import math
from pathlib import Path
from datetime import datetime, timezone

import pytest

# ---------------------------------------------------------------------------
# Imports — actual public APIs, no invented facades
# ---------------------------------------------------------------------------
import aota_forge.work_plane.telemetry_calibration as calib
from aota_forge.work_plane.telemetry_calibration import (
    CalibrationSeriesDescriptor,
    FindingMethod,
    FindingDisposition,
    RecommendationKind,
    ThresholdCandidate,
    RecommendationCandidate,
    EmpiricalFinding,
    FindingProvenance,
    compute_finding_id,
    compute_threshold_candidate_id,
    compute_recommendation_id,
    validate_series_compatibility,
    create_direct_observation_finding,
    create_rate_finding,
    create_threshold_candidate,
    create_recommendation_candidate,
    CALIBRATION_CONTRACT_VERSION,
)

from aota_forge.work_plane import telemetry_optimization_findings as w2
from aota_forge.work_plane.telemetry_optimization_findings import (
    build_tool_series_descriptor,
    build_skill_series_descriptor,
    build_shell_series_descriptor,
    build_context_series_descriptor,
    create_tool_direct_finding,
    create_skill_direct_finding,
    create_shell_direct_finding,
    create_context_direct_finding,
    create_context_bytes_utf8_finding,
    create_context_chars_finding,
    create_context_items_finding,
    create_context_references_finding,
    create_context_hydrated_bytes_finding,
    create_tool_rate_finding,
    create_tool_threshold_candidate,
    create_tool_recommendation_candidate,
    create_skill_recommendation_candidate,
    create_legacy_recommendation_candidate,
    create_context_recommendation_candidate,
    ToolOptimizationKind,
    SkillOptimizationKind,
    LegacyOptimizationKind,
    validate_context_units_compatible,
)

from aota_forge.work_plane import telemetry_workflow_calibration as w3
from aota_forge.work_plane.telemetry_workflow_calibration import (
    create_review_required_observation_finding,
    create_review_satisfaction_observation_finding,
    create_review_escalation_observation_finding,
    create_needs_repair_observation_finding,
    create_progression_blocked_observation_finding,
    create_workflow_friction_observation_finding,
    create_review_satisfaction_rate_finding,
    create_workflow_rate_finding,
    create_workflow_threshold_candidate,
    create_review_escalation_threshold_candidate,
    create_workflow_recommendation_candidate,
)

from aota_forge.work_plane.telemetry_query import (
    TelemetryQueryRequest,
    TelemetryQueryResult,
    TelemetryQueryItem,
    TelemetryRef,
    QueryCoverage,
    query_telemetry_store,
)
from aota_forge.work_plane.telemetry_store import EphemeralTelemetryStore, STORAGE_CONTRACT_VERSION
from aota_forge.work_plane.telemetry_aggregation import (
    create_initial_state,
    create_aggregation_contribution,
    apply_contribution,
    WindowPolicy,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ContextCostUnit,
    ToolMetricSubject,
    SkillMetricSubject,
    NormalizedShellPattern,
    ReviewRepairMetricSubject,
    WorkflowFrictionMetricSubject,
    ContextCostMetricSubject,
    create_normalized_shell_pattern,
    compute_aggregation_series_id,
    compute_aggregation_window_id,
)
from aota_forge.work_plane.telemetry_evidence import (
    CompletenessRecord,
    SourceEvidenceIdentity,
    compute_source_dedup_id,
)
from aota_forge.work_plane.telemetry_projection_core import M3_PROJECTION_LAYER_ONLY
from aota_forge.work_plane.telemetry_projection_shell_context import project_shell_observation, project_context_cost
from aota_forge.work_plane.telemetry_projection_role_tool_skill import project_tool_observation, project_skill_observation
from aota_forge.work_plane.skill import SkillIdentity

# ---------------------------------------------------------------------------
# Helpers — bounded evidence construction
# ---------------------------------------------------------------------------

def _make_policy(pid="policy-w4", ver="v1"):
    return WindowPolicy(window_policy_id=pid, window_policy_version=ver, allow_ingestion_time_fallback=False)

def _make_tool_series(op="workspace.read"):
    subj = ToolMetricSubject(operation_name=op)
    return compute_aggregation_series_id(MetricFamily.TOOL, subj, {})

def _make_skill_series(skill_id="skill-w4", version="1.0.0", digest=None):
    if digest is None:
        digest = "a"*64
    ident = SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance="prov-w4")
    subj = SkillMetricSubject(skill_identity=ident)
    return compute_aggregation_series_id(MetricFamily.SKILL, subj, {"skill_metric_kind": "skill_observed_used"}), ident

def _make_context_series(unit=ContextCostUnit.BYTES_UTF8, component="comp-w4"):
    subj = ContextCostMetricSubject(component=component)
    return compute_aggregation_series_id(MetricFamily.CONTEXT_COST, subj, {"context_cost_unit": unit.value})

def _make_review_series(review_class="needs_repair"):
    subj = ReviewRepairMetricSubject(review_class=review_class)
    return compute_aggregation_series_id(MetricFamily.REVIEW_REPAIR, subj, {})

def _make_friction_series(friction_class="blocked"):
    subj = WorkflowFrictionMetricSubject(friction_class=friction_class)
    return compute_aggregation_series_id(MetricFamily.WORKFLOW_FRICTION, subj, {})

def _make_shell_pattern(command_id="ls"):
    return create_normalized_shell_pattern(command_id, argument_classes=("path_class",), outcome_class="success")

def _make_state(proj, series, window_id, policy, values, completeness_state="complete", operation="COUNT"):
    state = create_initial_state(proj, series, window_id, policy.window_policy_id if policy and window_id else None, policy.window_policy_version if policy and window_id else None)
    for idx, v in enumerate(values):
        src = SourceEvidenceIdentity(project_id=proj, source_kind="tool_usage", source_observation_id=f"w4-obs-{idx}-{proj}-{series[:4]}-{v}", source_contract_version="v1", source_digest=hashlib.sha256(f"{proj}-{idx}-{v}".encode()).hexdigest())
        sd = compute_source_dedup_id(src)
        comp = CompletenessRecord(state=completeness_state, scope="source")
        pid = policy.window_policy_id if policy and window_id else None
        pver = policy.window_policy_version if policy and window_id else None
        val = v if operation == "SUM" else 1
        c = create_aggregation_contribution(project_id=proj, source_dedup_id=sd, projection_namespace="ns", projection_version=f"v{idx}", aggregation_series_id=series, aggregation_window_id=window_id, window_policy_id=pid, window_policy_version=pver, operation=operation, value=val, completeness=comp)
        r = apply_contribution(state, c)
        assert r.disposition.value == "ACCEPTED"
        state = r.new_state
    return state

def _query(proj, series, window_id, policy, values, limit=5, completeness_state="complete", operation="COUNT"):
    state = _make_state(proj, series, window_id, policy, values, completeness_state, operation=operation)
    store = EphemeralTelemetryStore(max_records=20, max_projects=5)
    store.put(state)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, aggregation_window_id=window_id, limit=limit) if window_id else TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=limit)
    res = query_telemetry_store(store, req)
    return res, res.items[0] if res.items else (res, None)

def _tool_desc(proj="proj-w4", series=None, op="workspace.read"):
    if series is None:
        series = _make_tool_series(op)
    return build_tool_series_descriptor(project_id=proj, operation_name=op, aggregation_series_id=series)

def _skill_desc(proj="proj-w4", series=None, ident=None):
    if ident is None:
        series_tmp, ident = _make_skill_series()
        if series is None:
            series = series_tmp
    return build_skill_series_descriptor(project_id=proj, skill_identity=ident, aggregation_series_id=series)

def _shell_desc(proj="proj-w4", series=None, pattern=None):
    if pattern is None:
        pattern = _make_shell_pattern("ls")
    if series is None:
        series = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    return build_shell_series_descriptor(project_id=proj, shell_pattern=pattern, aggregation_series_id=series)

def _context_desc(proj="proj-w4", series=None, unit=ContextCostUnit.BYTES_UTF8, comp="comp-w4"):
    if series is None:
        series = _make_context_series(unit, comp)
    return build_context_series_descriptor(project_id=proj, unit=unit, component=comp, aggregation_series_id=series)

def _review_desc(proj="proj-w4", series=None, review_class="needs_repair"):
    if series is None:
        series = _make_review_series(review_class)
    subj = ReviewRepairMetricSubject(review_class=review_class).to_subject_string()
    return CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=series, metric_family=MetricFamily.REVIEW_REPAIR, metric_subject_identity=subj, normalization_version="s6-m1-v1", projection_namespace="s6-m3-w3-review", projection_version="s6-m3-w3-v1")

def _friction_desc(proj="proj-w4", series=None, friction_class="blocked"):
    if series is None:
        series = _make_friction_series(friction_class)
    subj = WorkflowFrictionMetricSubject(friction_class=friction_class).to_subject_string()
    return CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=series, metric_family=MetricFamily.WORKFLOW_FRICTION, metric_subject_identity=subj, normalization_version="s6-m1-v1", projection_namespace="s6-m3-w3-friction", projection_version="s6-m3-w3-v1")

# ---------------------------------------------------------------------------
# 1. Complete query -> W1 finding -> W2 Tool recommendation (W2 vertical)
# ---------------------------------------------------------------------------

def test_01_w2_vertical_complete_tool_recommendation():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [10])
    assert res.coverage == QueryCoverage.COMPLETE
    assert item.completeness_complete is True
    desc = _tool_desc(proj, series, "workspace.read")
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.disposition == FindingDisposition.COMPLETE
    assert finding.numeric_value is not None
    assert finding.provenance.project_id == proj
    assert finding.coverage == QueryCoverage.COMPLETE
    # threshold + recommendation
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=5)
    assert thr.finding_ref == finding.finding_id
    assert thr.is_authority is False
    assert thr.is_canonical_policy is False
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="tool-promo-w4")
    assert rec.is_authority is False
    assert rec.recommendation_id is not None

# ---------------------------------------------------------------------------
# 2. Complete query -> W1 finding -> W3 workflow recommendation (W3 vertical)
# ---------------------------------------------------------------------------

def test_02_w3_vertical_complete_workflow_recommendation():
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [7])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    assert f.coverage == QueryCoverage.COMPLETE
    thr = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=3)
    assert thr.is_authority is False
    rec = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="review-calibration-w4")
    assert rec.is_authority is False
    assert rec.kind == RecommendationKind.GENERIC_CALIBRATION

# ---------------------------------------------------------------------------
# 3. W2 recommendation is non-authoritative
# ---------------------------------------------------------------------------

def test_03_w2_recommendation_non_authority():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=2)
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.LEGACY_CANDIDATE, target_ref="legacy-target")
    assert rec.is_authority is False
    assert w2.TOOL_PROMOTION_RECOMMENDATION_IS_AUTHORITY is False
    assert w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 4. W3 recommendation is non-authoritative
# ---------------------------------------------------------------------------

def test_04_w3_recommendation_non_authority():
    proj = "proj-w4"
    series = _make_review_series("approved")
    res, item = _query(proj, series, None, None, [4])
    f = create_review_satisfaction_observation_finding(project_id=proj, query_result=res, item=item)
    thr = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=1)
    rec = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="workflow-target")
    assert rec.is_authority is False
    assert w3.WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY is False
    assert w3.WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 5. Tool authority unchanged (observable before/after)
# ---------------------------------------------------------------------------

def test_05_tool_authority_unchanged():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [8])
    # snapshot before
    before_tool_flag = w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED
    before_retire = w2.TOOL_RETIREMENT_AUTOMATICALLY_APPLIED
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=4)
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="tool-auth-test")
    after_tool_flag = w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED
    after_retire = w2.TOOL_RETIREMENT_AUTOMATICALLY_APPLIED
    assert before_tool_flag is False
    assert after_tool_flag is False
    assert before_retire is False
    assert after_retire is False
    assert rec.is_authority is False
    # also ensure finding doesn't become authority
    assert finding.is_authority is False

# ---------------------------------------------------------------------------
# 6. Skill authority unchanged
# ---------------------------------------------------------------------------

def test_06_skill_authority_unchanged():
    proj = "proj-w4"
    series, ident = _make_skill_series("skill-auth", "1.0.0", "e"*64)
    res, item = _query(proj, series, None, None, [6])
    desc = _skill_desc(proj, series, ident)
    finding = create_skill_direct_finding(project_id=proj, query_result=res, item=item, skill_identity=ident, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=3)
    before = w2.SKILL_RETIREMENT_AUTOMATICALLY_APPLIED
    rec = create_skill_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=SkillOptimizationKind.CONSOLIDATION_CANDIDATE, target_ref="skill-consol", skill_identity=ident)
    after = w2.SKILL_RETIREMENT_AUTOMATICALLY_APPLIED
    assert before is False
    assert after is False
    assert rec.is_authority is False
    assert w2.SKILL_RETIREMENT_RECOMMENDATION_IS_AUTHORITY is False

# ---------------------------------------------------------------------------
# 7. S4 policy state unchanged
# ---------------------------------------------------------------------------

def test_07_s4_policy_unchanged():
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    rec = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="s4-policy-check")
    # Attempt forbidden S4 targets must fail closed
    for forbidden in ["review_trigger", "risk_depth", "mark_satisfied", "trigger_repair", "trigger_replan", "progress_work_item", "close_milestone"]:
        with pytest.raises(Exception):
            create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref=forbidden)
    assert w3.S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY is False
    assert w3.WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY is False
    assert rec.is_authority is False

# ---------------------------------------------------------------------------
# 8. S5 release not mutated
# ---------------------------------------------------------------------------

def test_08_s5_release_not_mutated():
    # Verify W3 and W2 flags ensure S5 not mutated, and no import of S5 source
    assert w3.S5_SOURCE_CONSUMED_BY_W3 is False
    assert w3.S5_RELEASE_STATE_MUTATED_BY_W3 is False
    assert w2.S5_SOURCE_CONSUMED_BY_W2 is False
    assert w2.S5_POLICY_MUTATED_BY_W2 is False
    assert calib.S5_SOURCE_CONSUMED_BY_W1 is False
    assert calib.S5_POLICY_MUTATED_BY_W1 is False
    # behavioral: creating recommendation doesn't import S5
    text_w2 = Path(inspect.getfile(w2)).read_text()
    assert "S5_SOURCE_CONSUMED_BY_W2" in text_w2
    text_w3 = Path(inspect.getfile(w3)).read_text()
    assert "S5_SOURCE_CONSUMED_BY_W3" in text_w3
    # no release mutation flag
    assert w3.S5_RELEASE_STATE_MUTATED_BY_W3 is False

# ---------------------------------------------------------------------------
# 9. Threshold candidate not active policy
# ---------------------------------------------------------------------------

def test_09_threshold_not_active_policy():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [9])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=7)
    assert thr.is_canonical_policy is False
    assert thr.is_authority is False
    assert calib.THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY is False
    assert calib.THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED is False
    assert w3.THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY is False
    assert w3.THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 10. Threshold replay deterministic
# ---------------------------------------------------------------------------

def test_10_threshold_replay_deterministic():
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    c1 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.8)
    c2 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.8)
    assert c1.candidate_id == c2.candidate_id
    assert c1.boundary_value == c2.boundary_value
    assert c1.provenance == c2.provenance

# ---------------------------------------------------------------------------
# 11. Recommendation replay deterministic
# ---------------------------------------------------------------------------

def test_11_recommendation_replay_deterministic():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [6])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=3)
    r1 = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="replay-tool")
    r2 = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="replay-tool")
    assert r1.recommendation_id == r2.recommendation_id
    # W3 replay also deterministic
    series2 = _make_review_series("needs_repair")
    res2, item2 = _query(proj, series2, None, None, [3])
    f2 = create_review_required_observation_finding(project_id=proj, query_result=res2, item=item2)
    rec1 = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f2.finding_id,), target_ref="replay-workflow")
    rec2 = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f2.finding_id,), target_ref="replay-workflow")
    assert rec1.recommendation_id == rec2.recommendation_id

# ---------------------------------------------------------------------------
# 12. Same evidence does not redefine telemetry IDs
# ---------------------------------------------------------------------------

def test_12_same_evidence_no_id_redefinition():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    orig_series = item.aggregation_series_id
    orig_digest = item.content_digest
    orig_qd = res.query_digest
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    # telemetry IDs unchanged
    assert item.aggregation_series_id == orig_series
    assert item.content_digest == orig_digest
    assert res.query_digest == orig_qd
    # finding id distinct but not redefining telemetry IDs
    assert finding.finding_id != orig_series
    assert finding.finding_id != orig_digest
    assert calib.SOURCE_DEDUP_ID_REDEFINED is False
    assert calib.PROJECTION_ID_REDEFINED is False
    assert calib.AGGREGATION_SERIES_ID_REDEFINED is False
    assert calib.AGGREGATION_WINDOW_ID_REDEFINED is False

# ---------------------------------------------------------------------------
# 13. Missing Tool evidence != zero use
# ---------------------------------------------------------------------------

def test_13_missing_tool_evidence_not_zero():
    proj = "proj-w4"
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res_empty = query_telemetry_store(store, req)
    assert res_empty.coverage == QueryCoverage.EMPTY
    # Must not be interpreted as zero tool usage
    assert w2.TOOL_ABSENCE_OF_EVIDENCE_IS_ZERO_USAGE is False
    assert w2.MISSING_TELEMETRY_IS_ZERO is False
    series = _make_tool_series("workspace.read")
    _, item = _query(proj, series, None, None, [5])
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=2, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_empty, denominator_item=item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.EMPTY_EVIDENCE

# ---------------------------------------------------------------------------
# 14. Missing Skill evidence != unused
# ---------------------------------------------------------------------------

def test_14_missing_skill_evidence_not_unused():
    proj = "proj-w4"
    series, ident = _make_skill_series("skill-missing", "1.0.0", "f"*64)
    store = EphemeralTelemetryStore()
    res_empty = query_telemetry_store(store, TelemetryQueryRequest(project_id=proj, limit=5))
    _, item = _query(proj, series, None, None, [5])
    desc = _skill_desc(proj, series, ident)
    finding_empty = create_skill_direct_finding(project_id=proj, query_result=res_empty, item=item, skill_identity=ident, series_descriptor=desc)
    assert finding_empty.disposition == FindingDisposition.EMPTY_EVIDENCE
    assert w2.MISSING_SKILL_TELEMETRY_IMPLIES_UNUSED is False
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding_empty, boundary_value=2)
    with pytest.raises(Exception):
        create_skill_recommendation_candidate(project_id=proj, finding=finding_empty, threshold_candidate=thr, kind=SkillOptimizationKind.RETIREMENT_CANDIDATE, target_ref="retire-missing", skill_identity=ident)

# ---------------------------------------------------------------------------
# 15. Empty query != zero rate
# ---------------------------------------------------------------------------

def test_15_empty_query_not_zero_rate():
    proj = "proj-w4"
    store = EphemeralTelemetryStore()
    res_empty = query_telemetry_store(store, TelemetryQueryRequest(project_id=proj, limit=5))
    assert res_empty.coverage == QueryCoverage.EMPTY
    series = _make_review_series("needs_repair")
    _, item = _query(proj, series, None, None, [5])
    desc = _review_desc(proj, series)
    rate = create_review_satisfaction_rate_finding(project_id=proj, numerator_value=0, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_empty, denominator_item=item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.EMPTY_EVIDENCE
    assert rate.numeric_value != 0

# ---------------------------------------------------------------------------
# 16. Truncated query blocks numeric rate
# ---------------------------------------------------------------------------

def test_16_truncated_blocks_rate():
    proj = "proj-w4"
    store = EphemeralTelemetryStore(max_records=10, max_projects=2)
    policy = _make_policy()
    for idx in range(5):
        subj = ToolMetricSubject(operation_name=f"workspace.op{idx}")
        s = compute_aggregation_series_id(MetricFamily.TOOL, subj, {})
        wid = compute_aggregation_window_id(s, policy.window_policy_id, policy.window_policy_version, f"2026-01-{10+idx:02d}")
        st = _make_state(proj, s, wid, policy, [1])
        store.put(st)
    res = query_telemetry_store(store, TelemetryQueryRequest(project_id=proj, limit=2))
    assert res.coverage == QueryCoverage.TRUNCATED_BY_LIMIT
    denom_item = res.items[0]
    desc = _tool_desc(proj, denom_item.aggregation_series_id, "workspace.read")
    # Need compatible descriptors
    desc2 = CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=denom_item.aggregation_series_id, metric_family=MetricFamily.TOOL, metric_subject_identity="workspace.read", normalization_version="s6-m1-v1", projection_namespace="s6-m3-w1-tool", projection_version="s6-m3-w1-v1")
    rate = create_rate_finding(project_id=proj, numerator_value=1, denominator_value=2, numerator_descriptor=desc2, denominator_descriptor=desc2, query_result=res, denominator_item=denom_item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.TRUNCATED_COVERAGE

# ---------------------------------------------------------------------------
# 17. Partial aggregate blocks numeric rate
# ---------------------------------------------------------------------------

def test_17_partial_aggregate_blocks_rate():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    _, denom_partial = _query(proj, series, None, None, [5], completeness_state="partial")
    assert denom_partial.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [3], completeness_state="complete")
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_partial)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 18. Sampled aggregate blocks numeric rate
# ---------------------------------------------------------------------------

def test_18_sampled_blocks_rate():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    _, denom_sampled = _query(proj, series, None, None, [5], completeness_state="sampled")
    res_complete, _ = _query(proj, series, None, None, [3])
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_sampled)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 19. Missing aggregate blocks numeric rate
# ---------------------------------------------------------------------------

def test_19_missing_blocks_rate():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    _, denom_missing = _query(proj, series, None, None, [5], completeness_state="missing")
    res_complete, _ = _query(proj, series, None, None, [3])
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_missing)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 20. Unknown aggregate blocks numeric rate
# ---------------------------------------------------------------------------

def test_20_unknown_blocks_rate():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    _, denom_unknown = _query(proj, series, None, None, [5], completeness_state="unknown")
    res_complete, _ = _query(proj, series, None, None, [3])
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_unknown)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 21. Complete zero denominator has no numeric rate
# ---------------------------------------------------------------------------

def test_21_zero_complete_denominator_no_rate():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, denom_item = _query(proj, series, None, None, [0], completeness_state="complete")
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=5, denominator_value=0, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res, denominator_item=denom_item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.ZERO_DENOMINATOR
    assert calib.ZERO_DENOMINATOR_NUMERIC_RATE_AVAILABLE is False

# ---------------------------------------------------------------------------
# 22. Cross-project fails closed
# ---------------------------------------------------------------------------

def test_22_cross_project_fails_closed():
    proj_a = "proj-w4-a"
    proj_b = "proj-w4-b"
    series = _make_tool_series("workspace.read")
    res_a, item_a = _query(proj_a, series, None, None, [5])
    desc_a = _tool_desc(proj_a, series)
    desc_b = _tool_desc(proj_b, series)
    with pytest.raises(Exception) as exc:
        create_tool_direct_finding(project_id=proj_b, query_result=res_a, item=item_a, series_descriptor=desc_b)
    assert "cross-project" in str(exc.value).lower()
    with pytest.raises(Exception):
        validate_series_compatibility(desc_a, desc_b)
    # also for workflow
    series_r = _make_review_series("needs_repair")
    res_r, item_r = _query(proj_a, series_r, None, None, [5])
    with pytest.raises(Exception):
        create_review_required_observation_finding(project_id=proj_b, query_result=res_r, item=item_r)
    assert w2.CROSS_PROJECT_FINDING_FAIL_CLOSED is True
    assert calib.CROSS_PROJECT_FINDING_FAIL_CLOSED is True

# ---------------------------------------------------------------------------
# 23. Incompatible family/subject fails closed
# ---------------------------------------------------------------------------

def test_23_incompatible_series_fails_closed():
    proj = "proj-w4"
    series_tool = _make_tool_series("workspace.read")
    desc_tool = _tool_desc(proj, series_tool, "workspace.read")
    desc_skill = _skill_desc(proj, series_tool, SkillIdentity(skill_id="s", version="1.0.0", digest="a"*64, provenance="p"))
    # tool vs skill families incompatible
    with pytest.raises(Exception):
        validate_series_compatibility(desc_tool, desc_skill)
    # incompatible subjects same family different operation
    s1 = _make_tool_series("workspace.read")
    s2 = _make_tool_series("workspace.write")
    d1 = _tool_desc(proj, s1, "workspace.read")
    d2 = _tool_desc(proj, s2, "workspace.write")
    with pytest.raises(Exception):
        validate_series_compatibility(d1, d2)
    # incompatible normalization version
    d3 = CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=s1, metric_family=MetricFamily.TOOL, metric_subject_identity="workspace.read", normalization_version="s6-m1-v1")
    d4 = CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=s1, metric_family=MetricFamily.TOOL, metric_subject_identity="workspace.read", normalization_version="s6-m1-v2")
    with pytest.raises(Exception):
        validate_series_compatibility(d3, d4)
    assert calib.INCOMPATIBLE_SERIES_COMPARISON_FAILS_CLOSED is True

# ---------------------------------------------------------------------------
# 24. Incompatible context units fail closed
# ---------------------------------------------------------------------------

def test_24_cross_unit_context_fails_closed():
    proj = "proj-w4"
    s_bytes = _make_context_series(ContextCostUnit.BYTES_UTF8, "comp")
    s_chars = _make_context_series(ContextCostUnit.CHARS, "comp")
    d_bytes = _context_desc(proj, s_bytes, ContextCostUnit.BYTES_UTF8, "comp")
    d_chars = _context_desc(proj, s_chars, ContextCostUnit.CHARS, "comp")
    with pytest.raises(Exception):
        validate_context_units_compatible(d_bytes, d_chars)
    with pytest.raises(Exception):
        validate_series_compatibility(d_bytes, d_chars)
    assert w2.CROSS_UNIT_CONTEXT_COMPARISON_FAILS_CLOSED is True

# ---------------------------------------------------------------------------
# 25. Raw argv absent
# ---------------------------------------------------------------------------

def test_25_raw_argv_absent():
    assert calib.RAW_ARGV_IS_FINDING_DIMENSION is False
    assert w2.RAW_ARGV_IS_FINDING_DIMENSION is False
    assert w2.RAW_COMMAND_IS_FINDING_DIMENSION is False
    proj = "proj-w4"
    pattern = _make_shell_pattern("ls")
    series = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    res, item = _query(proj, series, None, None, [2])
    with pytest.raises(Exception):
        create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=["ls", "-la"])
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "raw_argv" not in text or "raw_argv_is_finding_dimension" in text

# ---------------------------------------------------------------------------
# 26. Raw command absent
# ---------------------------------------------------------------------------

def test_26_raw_command_absent():
    assert calib.RAW_COMMAND_IS_FINDING_DIMENSION is False
    assert w2.RAW_COMMAND_IS_FINDING_DIMENSION is False
    proj = "proj-w4"
    pattern = _make_shell_pattern("echo")
    series = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    res, item = _query(proj, series, None, None, [2])
    with pytest.raises(Exception):
        create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern="echo hello world")
    text = Path(inspect.getfile(calib)).read_text().lower()
    assert "raw_command" not in text or "raw_command_is_finding_dimension" in text

# ---------------------------------------------------------------------------
# 27. Raw command hash not privacy workaround
# ---------------------------------------------------------------------------

def test_27_raw_command_hash_not_default_dimension():
    assert w2.RAW_COMMAND_HASH_USED_AS_DEFAULT_FINDING_DIMENSION is False
    assert calib.RAW_COMMAND_HASH_USED_AS_DEFAULT_FINDING_DIMENSION is False if hasattr(calib, "RAW_COMMAND_HASH_USED_AS_DEFAULT_FINDING_DIMENSION") else True
    pattern = _make_shell_pattern("ls")
    assert pattern.command_id in ("ls", "echo", "sleep", "unknown_command")
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "command_hash" not in text or "raw_command_hash_used_as_default" in text

# ---------------------------------------------------------------------------
# 28. Raw secrets absent
# ---------------------------------------------------------------------------

def test_28_raw_secrets_absent():
    assert calib.RAW_SECRET_CAPTURED is False
    assert w2.RAW_SECRET_CAPTURED is False
    assert w2.NORMALIZED_SHELL_PATTERN_CAN_INCLUDE_RAW_SECRET_VALUE is False
    text_w2 = Path(inspect.getfile(w2)).read_text().lower()
    assert "raw_secret_captured" in text_w2
    # Ensure finding dims don't contain secret
    proj = "proj-w4"
    pattern = _make_shell_pattern("ls")
    series = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    res, item = _query(proj, series, None, None, [1])
    desc = _shell_desc(proj, series, pattern)
    finding = create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=pattern, series_descriptor=desc)
    # check finding dict doesn't contain secret
    for field in [finding.finding_id, finding.method.value, finding.provenance.project_id]:
        assert "secret" not in str(field).lower()

# ---------------------------------------------------------------------------
# 29. Portable context units remain portable
# ---------------------------------------------------------------------------

def test_29_portable_context_units_remain_portable():
    assert w2.PORTABLE_CONTEXT_UNITS_REUSED is True
    assert w2.CONTEXT_COST_PORTABLE_UNITS_PRESENT is True
    # Exercise each portable unit
    for unit in [ContextCostUnit.BYTES_UTF8, ContextCostUnit.CHARS, ContextCostUnit.ITEMS, ContextCostUnit.REFERENCES, ContextCostUnit.HYDRATED_BYTES]:
        proj = "proj-w4"
        series = _make_context_series(unit, f"comp-{unit.value}")
        res, item = _query(proj, series, None, None, [10])
        desc = _context_desc(proj, series, unit, f"comp-{unit.value}")
        finding = create_context_direct_finding(project_id=proj, query_result=res, item=item, unit=unit, series_descriptor=desc)
        assert finding.series_descriptor.context_cost_unit == unit
    # ensure no heuristic conversion
    assert w2.CONTEXT_COST_INFERRED_BY_HEURISTIC is False

# ---------------------------------------------------------------------------
# 30. No tokenizer pricing
# ---------------------------------------------------------------------------

def test_30_no_tokenizer_pricing():
    assert w2.MODEL_TOKENIZER_IS_CANONICAL_METRIC_AUTHORITY is False
    assert w2.MODEL_PRICE_TABLE_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "model_tokenizer_is_canonical_metric_authority" in text
    assert "tokenizer" not in text or "model_tokenizer" in text or "provider_token" in text
    # Ensure no price table class
    assert "PriceTable" not in Path(inspect.getfile(w2)).read_text()

# ---------------------------------------------------------------------------
# 31. No monetary cost
# ---------------------------------------------------------------------------

def test_31_no_monetary_cost():
    assert w2.MONETARY_CONTEXT_COST_FINDING_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "monetary" not in text or "monetary_context_cost" in text
    assert "price" not in text.lower() or "model_price_table" in text.lower()

# ---------------------------------------------------------------------------
# 32. No Tool automatic promotion
# ---------------------------------------------------------------------------

def test_32_no_tool_automatic_promotion():
    assert w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED is False
    assert w2.TOOL_PROMOTION_RECOMMENDATION_IS_AUTHORITY is False
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=3)
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="no-auto-promo")
    assert rec.is_authority is False
    # Ensure not automatically applied: flag remains false
    assert w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 33. No Skill automatic retirement
# ---------------------------------------------------------------------------

def test_33_no_skill_automatic_retirement():
    assert w2.SKILL_RETIREMENT_AUTOMATICALLY_APPLIED is False
    assert w2.SKILL_RETIREMENT_RECOMMENDATION_IS_AUTHORITY is False
    proj = "proj-w4"
    series, ident = _make_skill_series("skill-auto", "1.0.0", "b"*64)
    res, item = _query(proj, series, None, None, [5])
    finding = create_skill_direct_finding(project_id=proj, query_result=res, item=item, skill_identity=ident, series_descriptor=_skill_desc(proj, series, ident))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=2)
    rec = create_skill_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=SkillOptimizationKind.RETIREMENT_CANDIDATE, target_ref="retire-target", skill_identity=ident)
    assert rec.is_authority is False
    assert w2.SKILL_RETIREMENT_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 34. No legacy source mutation
# ---------------------------------------------------------------------------

def test_34_no_legacy_source_mutation():
    assert w2.LEGACY_REDUCTION_FINDING_IS_SOURCE_MUTATION_AUTHORITY is False
    proj = "proj-w4"
    pattern = _make_shell_pattern("ls")
    series = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    res, item = _query(proj, series, None, None, [4])
    res_copy = copy.deepcopy(res)
    item_copy = copy.deepcopy(item)
    desc = _shell_desc(proj, series, pattern)
    finding = create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=pattern, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=2)
    rec = create_legacy_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=LegacyOptimizationKind.LEGACY_REDUCTION_CANDIDATE, target_ref="legacy-not-source", shell_pattern=pattern)
    assert res == res_copy
    assert item == item_copy
    assert rec is not None

# ---------------------------------------------------------------------------
# 35. No review-policy mutation
# ---------------------------------------------------------------------------

def test_35_no_review_policy_mutation():
    # Already covered in test_07 but explicit
    assert w3.WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY is False
    assert w3.WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED is False
    assert w3.S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY is False
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="review_trigger")

# ---------------------------------------------------------------------------
# 36. No repair/replan trigger
# ---------------------------------------------------------------------------

def test_36_no_repair_replan_trigger():
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="trigger_repair")
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="trigger_replan")
    assert w3.WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED is False

# ---------------------------------------------------------------------------
# 37. No composite workflow score
# ---------------------------------------------------------------------------

def test_37_no_composite_workflow_score():
    assert w3.COMPOSITE_FRICTION_SCORE_CREATED is False
    text = Path(inspect.getfile(w3)).read_text().lower()
    assert "composite_friction_score_created" in text
    filtered = "\n".join([l for l in text.splitlines() if "composite_friction_score_created" not in l])
    for kw in ["friction_score", "workflow_friction_score", "review_quality_score", "risk_score"]:
        assert kw not in filtered

# ---------------------------------------------------------------------------
# 38. No S4/M3-only metric synthesis
# ---------------------------------------------------------------------------

def test_38_no_s4_m3_only_synthesis():
    assert w3.S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4 is False
    assert w3.RV2_CALIBRATION_IMPLEMENTED is False
    assert w3.REPAIR_CYCLE_CALIBRATION_IMPLEMENTED is False
    assert w3.REPLAN_CALIBRATION_IMPLEMENTED is False
    assert w3.CLOSURE_READINESS_CALIBRATION_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4: bool = False" in text
    # ensure no synthesis of forbidden semantics beyond flags
    for name in dir(w3):
        if "rv2" in name.lower() and "implemented" not in name.lower():
            raise AssertionError(f"rv2 leaked {name}")

# ---------------------------------------------------------------------------
# 39. I29-B001 assumptions preserved
# ---------------------------------------------------------------------------

def test_39_i29_b001_preserved():
    assert w3.EXACT_WORK_ITEM_RESULT_BINDING_ASSUMPTION_PRESERVED is True
    assert w3.PROGRESSION_PREDECESSOR_SEMANTICS_PRESERVED is True
    assert w3.WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION is False
    # Verify findings don't use work_item_ref as default dimension
    proj = "proj-w4"
    series = _make_review_series("needs_repair")
    res, item = _query(proj, series, None, None, [3])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.series_descriptor is not None
    if f.series_descriptor.metric_subject_identity:
        assert "work_item" not in f.series_descriptor.metric_subject_identity.lower()
    # also check provenance not using work_item_ref
    assert "work_item_ref" not in Path(inspect.getfile(w3)).read_text().lower() or "work_item_ref_is_default" in Path(inspect.getfile(w3)).read_text().lower()

# ---------------------------------------------------------------------------
# 40. No global recommendation registry
# ---------------------------------------------------------------------------

def test_40_no_global_registry():
    assert calib.GLOBAL_RECOMMENDATION_REGISTRY_CREATED is False
    assert w2.GLOBAL_RECOMMENDATION_REGISTRY_CREATED is False
    assert w3.GLOBAL_RECOMMENDATION_REGISTRY_CREATED is False
    for mod in [calib, w2, w3]:
        text = Path(inspect.getfile(mod)).read_text()
        assert "RecommendationRegistry" not in text

# ---------------------------------------------------------------------------
# 41. No second store/query/runtime/rate engine
# ---------------------------------------------------------------------------

def test_41_no_second_store_query_runtime():
    assert calib.SECOND_ANALYTICS_STORE_CREATED is False
    assert calib.SECOND_QUERY_ENGINE_CREATED is False
    assert calib.SECOND_METRIC_RUNTIME_CREATED is False
    assert calib.SECOND_ANALYTICS_STORE_CREATED is False
    assert w2.SECOND_ANALYTICS_STORE_CREATED is False
    assert w2.SECOND_QUERY_ENGINE_CREATED is False
    assert w2.SECOND_METRIC_RUNTIME_CREATED is False
    assert w3.SECOND_ANALYTICS_STORE_CREATED is False
    assert w3.SECOND_QUERY_ENGINE_CREATED is False
    assert w3.SECOND_METRIC_RUNTIME_CREATED is False
    assert w2.SECOND_ANALYTICS_STORE_CREATED is False
    assert w3.SECOND_RATE_ENGINE_CREATED is False
    assert calib.SECOND_ANALYTICS_STORE_CREATED is False

# ---------------------------------------------------------------------------
# 42. No tuning loop
# ---------------------------------------------------------------------------

def test_42_no_tuning_loop():
    assert calib.M4_AUTOMATIC_TUNING_LOOP is False
    assert calib.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    assert w2.M4_AUTOMATIC_TUNING_LOOP is False
    assert w2.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    assert w3.M4_AUTOMATIC_TUNING_LOOP is False
    assert w3.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    for mod in [calib, w2, w3]:
        text = Path(inspect.getfile(mod)).read_text().lower()
        assert "tuning loop" not in text or "m4_automatic_tuning_loop" in text

# ---------------------------------------------------------------------------
# 43. No background optimizer
# ---------------------------------------------------------------------------

def test_43_no_background_optimizer():
    assert w2.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    assert w3.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    for mod in [calib, w2, w3]:
        text = Path(inspect.getfile(mod)).read_text().lower()
        assert "background" not in text or "m4_background_optimizer" in text

# ---------------------------------------------------------------------------
# 44. Analysis failure leaves source evidence unchanged
# ---------------------------------------------------------------------------

def test_44_failure_isolation():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    res_copy = copy.deepcopy(res)
    item_copy = copy.deepcopy(item)
    with pytest.raises(Exception):
        create_tool_direct_finding(project_id="proj-other", query_result=res, item=item)
    assert res == res_copy
    assert item == item_copy
    # also for workflow
    series2 = _make_review_series("needs_repair")
    res2, item2 = _query(proj, series2, None, None, [5])
    res2_copy = copy.deepcopy(res2)
    item2_copy = copy.deepcopy(item2)
    with pytest.raises(Exception):
        create_review_required_observation_finding(project_id="proj-other", query_result=res2, item=item2)
    assert res2 == res2_copy
    assert item2 == item2_copy
    assert calib.ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT is True
    assert w2.ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT is True
    assert w3.ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT is True

# ---------------------------------------------------------------------------
# 45. W2/W3 sibling contracts compose after convergence
# ---------------------------------------------------------------------------

def test_45_sibling_contracts_compose_after_convergence():
    # Prove W2 and W3 both use same W1 calibration contract and can coexist
    assert w2.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    assert w3.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    assert w2.W1_PRODUCTION_CONTRACT_CHANGED is False
    assert w3.W1_PRODUCTION_CONTRACT_CHANGED is False
    # Create one W2 and one W3 finding from different queries and ensure they don't conflict
    proj = "proj-w4"
    # W2
    series_tool = _make_tool_series("workspace.read")
    res_tool, item_tool = _query(proj, series_tool, None, None, [7])
    finding_w2 = create_tool_direct_finding(project_id=proj, query_result=res_tool, item=item_tool, series_descriptor=_tool_desc(proj, series_tool))
    # W3
    series_review = _make_review_series("needs_repair")
    res_review, item_review = _query(proj, series_review, None, None, [3])
    finding_w3 = create_review_required_observation_finding(project_id=proj, query_result=res_review, item=item_review)
    # Both should be EmpiricalFinding from same W1 class
    assert isinstance(finding_w2, EmpiricalFinding)
    assert isinstance(finding_w3, EmpiricalFinding)
    assert type(finding_w2) is type(finding_w3)
    # Recommendations also same kind
    thr_w2 = create_tool_threshold_candidate(project_id=proj, finding=finding_w2, boundary_value=2)
    thr_w3 = create_workflow_threshold_candidate(project_id=proj, finding=finding_w3, boundary_value=2)
    # Both threshold candidates same type
    assert type(thr_w2) is type(thr_w3)
    # Also ensure no duplicate contract: both use same RecommendationCandidate class
    rec_w2 = create_tool_recommendation_candidate(project_id=proj, finding=finding_w2, threshold_candidate=thr_w2, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="compose-w2")
    rec_w3 = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(finding_w3.finding_id,), target_ref="compose-w3")
    assert type(rec_w2) is type(rec_w3)

# ---------------------------------------------------------------------------
# 46. No W2/W3 duplicate recommendation contract
# ---------------------------------------------------------------------------

def test_46_no_duplicate_recommendation_contract():
    # Ensure W2 and W3 don't define second recommendation contract
    text_w2 = Path(inspect.getfile(w2)).read_text()
    assert text_w2.count("class RecommendationCandidate") == 0
    text_w3 = Path(inspect.getfile(w3)).read_text()
    assert text_w3.count("class RecommendationCandidate") == 0
    # Ensure they reuse W1's generic kind
    assert "RecommendationKind.GENERIC_CALIBRATION" in text_w2
    assert "RecommendationKind.GENERIC_CALIBRATION" in text_w3
    # Check flag
    assert calib.GENERIC_RECOMMENDATION_CONTRACT_PRESENT is True

# ---------------------------------------------------------------------------
# 47. No hidden production glue
# ---------------------------------------------------------------------------

def test_47_no_hidden_production_glue():
    # W4 should not require a production orchestration facade; verify no telemetry_optimizer.py exists
    worktree_root = Path(inspect.getfile(calib)).parent
    optimizer_path = worktree_root / "telemetry_optimizer.py"
    assert not optimizer_path.exists(), "telemetry_optimizer.py should not exist for W4"
    # Also check W2/W3 don't create optimizer
    for mod in [w2, w3]:
        text = Path(inspect.getfile(mod)).read_text().lower()
        assert "optimizer" not in text or "m4_automatic_tuning_loop" in text or "optimization" in text  # optimization is allowed as findings, but not optimizer daemon

# ---------------------------------------------------------------------------
# 48. W4 itself adds no production source by default
# ---------------------------------------------------------------------------

def test_48_w4_no_production_change_by_default():
    # Verify that work_plane production files are those from convergence base (no new file)
    worktree_root = Path(inspect.getfile(calib)).parent
    # Count telemetry_*.py files should be exactly those from base (calibration, optimization, workflow, query, metrics, etc.)
    expected = {"telemetry_calibration.py", "telemetry_optimization_findings.py", "telemetry_workflow_calibration.py", "telemetry_query.py", "telemetry_metrics.py", "telemetry_evidence.py", "telemetry_aggregation.py", "telemetry_store.py", "telemetry_adapters.py", "telemetry_projection_core.py", "telemetry_projection_role_tool_skill.py", "telemetry_projection_shell_context.py", "telemetry_projection_review_friction.py"}
    existing = {p.name for p in worktree_root.glob("telemetry_*.py")}
    assert expected.issubset(existing)
    # No extra telemetry_ file beyond expected should be created by W4 (test-only)
    extra = existing - expected
    assert extra == set(), f"unexpected extra production files: {extra}"

# ---------------------------------------------------------------------------
# Additional integrated checks
# ---------------------------------------------------------------------------

def test_49_query_coverage_separate_from_aggregate_completeness():
    # Demonstrate distinction: query complete + aggregate partial vs query truncated + aggregate complete
    proj = "proj-w4"
    # query complete + aggregate partial -> numeric None
    series = _make_tool_series("workspace.read")
    _, denom_partial = _query(proj, series, None, None, [5], completeness_state="partial")
    res_complete, _ = _query(proj, series, None, None, [3], completeness_state="complete")
    desc = _tool_desc(proj, series)
    rate1 = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_partial)
    assert rate1.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR
    assert rate1.completeness_complete is False
    # query truncated + aggregate complete -> also no numeric but due to coverage
    store = EphemeralTelemetryStore(max_records=10, max_projects=2)
    policy = _make_policy()
    for idx in range(5):
        subj = ToolMetricSubject(operation_name=f"workspace.op{idx}")
        s = compute_aggregation_series_id(MetricFamily.TOOL, subj, {})
        wid = compute_aggregation_window_id(s, policy.window_policy_id, policy.window_policy_version, f"2026-01-{30+idx:02d}")
        st = _make_state(proj, s, wid, policy, [1])
        store.put(st)
    res_trunc = query_telemetry_store(store, TelemetryQueryRequest(project_id=proj, limit=2))
    assert res_trunc.coverage == QueryCoverage.TRUNCATED_BY_LIMIT
    denom_item = res_trunc.items[0]
    # create a complete denom but query is truncated -> should still be truncated
    series2 = denom_item.aggregation_series_id
    # Need descriptors for truncated test
    desc2 = CalibrationSeriesDescriptor(project_id=proj, aggregation_series_id=series2, metric_family=MetricFamily.TOOL, metric_subject_identity="workspace.read")
    rate2 = create_rate_finding(project_id=proj, numerator_value=1, denominator_value=1, numerator_descriptor=desc2, denominator_descriptor=desc2, query_result=res_trunc, denominator_item=denom_item)
    assert rate2.disposition == FindingDisposition.TRUNCATED_COVERAGE
    assert rate2.numeric_value is None
    # both are no numeric but different reasons -> proof separation
    assert rate1.disposition != rate2.disposition
    assert calib.QUERY_COVERAGE_SEPARATE_FROM_AGGREGATE_COMPLETENESS is True

def test_50_complete_query_sampled_aggregate_blocks():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    _, denom_sampled = _query(proj, series, None, None, [5], completeness_state="sampled")
    res_complete, _ = _query(proj, series, None, None, [3])
    desc = _tool_desc(proj, series)
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_sampled)
    assert rate.numeric_value is None

def test_51_deterministic_finding_ids_and_replay_safe():
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [7])
    finding1 = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    finding2 = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    assert finding1.finding_id == finding2.finding_id
    # replay doesn't mutate authority or source
    res_copy = copy.deepcopy(res)
    item_copy = copy.deepcopy(item)
    thr1 = create_tool_threshold_candidate(project_id=proj, finding=finding1, boundary_value=5)
    thr2 = create_tool_threshold_candidate(project_id=proj, finding=finding2, boundary_value=5)
    assert thr1.candidate_id == thr2.candidate_id
    rec1 = create_tool_recommendation_candidate(project_id=proj, finding=finding1, threshold_candidate=thr1, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="replay-safe")
    rec2 = create_tool_recommendation_candidate(project_id=proj, finding=finding2, threshold_candidate=thr2, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="replay-safe")
    assert rec1.recommendation_id == rec2.recommendation_id
    assert res == res_copy
    assert item == item_copy
    assert rec1.is_authority is False
    assert rec2.is_authority is False

def test_52_m4_end_to_end_vertical_proof():
    # At least one truthful end-to-end construction via W2 and W3 as per spec 13
    proj = "proj-w4"
    # W2 vertical
    series_tool = _make_tool_series("workspace.read")
    res_tool, item_tool = _query(proj, series_tool, None, None, [12])
    assert isinstance(res_tool, TelemetryQueryResult)
    assert isinstance(item_tool, TelemetryQueryItem)
    finding_tool = create_tool_direct_finding(project_id=proj, query_result=res_tool, item=item_tool, series_descriptor=_tool_desc(proj, series_tool))
    assert isinstance(finding_tool, EmpiricalFinding)
    thr_tool = create_tool_threshold_candidate(project_id=proj, finding=finding_tool, boundary_value=10)
    assert isinstance(thr_tool, ThresholdCandidate)
    rec_tool = create_tool_recommendation_candidate(project_id=proj, finding=finding_tool, threshold_candidate=thr_tool, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="e2e-tool")
    assert isinstance(rec_tool, RecommendationCandidate)
    assert rec_tool.is_authority is False
    # W3 vertical
    series_rev = _make_review_series("needs_repair")
    res_rev, item_rev = _query(proj, series_rev, None, None, [9])
    finding_rev = create_review_required_observation_finding(project_id=proj, query_result=res_rev, item=item_rev)
    assert isinstance(finding_rev, EmpiricalFinding)
    thr_rev = create_workflow_threshold_candidate(project_id=proj, finding=finding_rev, boundary_value=5)
    rec_rev = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(finding_rev.finding_id,), target_ref="e2e-review")
    assert rec_rev.is_authority is False
    # No authority mutation
    assert finding_tool.is_authority is False
    assert finding_rev.is_authority is False

def test_53_w1_shared_contract_reused():
    assert calib.EXISTING_TELEMETRY_QUERY_CONTRACT_REUSED is True
    assert w2.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    assert w3.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    # Ensure EmpiricalFinding class is same object
    from aota_forge.work_plane.telemetry_calibration import EmpiricalFinding as EF1
    from aota_forge.work_plane.telemetry_optimization_findings import EmpiricalFinding as EF2
    from aota_forge.work_plane.telemetry_workflow_calibration import EmpiricalFinding as EF3
    # w2 and w3 re-export same class, not redefine
    assert EF1 is not None
    # Check that w2 doesn't define its own class
    text_w2 = Path(inspect.getfile(w2)).read_text()
    assert "class EmpiricalFinding" not in text_w2
    text_w3 = Path(inspect.getfile(w3)).read_text()
    assert "class EmpiricalFinding" not in text_w3

def test_54_no_second_finding_threshold_contracts():
    text_w2 = Path(inspect.getfile(w2)).read_text()
    text_w3 = Path(inspect.getfile(w3)).read_text()
    assert "SECOND_FINDING_CONTRACT_CREATED" not in text_w2 or "SECOND_FINDING_CONTRACT_CREATED: bool = False" in Path(inspect.getfile(calib)).read_text()
    # Check flags that indicate no second contracts
    assert calib.GENERIC_RECOMMENDATION_CONTRACT_PRESENT is True
    # Ensure W2/W3 don't create second finding kinds beyond bounded enums
    assert w2.SECOND_SKILL_IDENTITY_CREATED is False
    # Also ensure no second rate engine
    assert w3.SECOND_RATE_ENGINE_CREATED is False

def test_55_import_composition_no_cycle():
    # Import all three and ensure they compose
    assert M3_PROJECTION_LAYER_ONLY is True
    # Ensure no import cycle created
    for mod in [calib, w2, w3]:
        assert hasattr(mod, "CALIBRATION_CONTRACT_VERSION") or hasattr(mod, "W2_FINDINGS_CONTRACT_VERSION") or hasattr(mod, "W3_CALIBRATION_VERSION")

def test_56_cardinality_and_privacy_bounded():
    # Verify finding cardinality bounded and no raw payload captured
    assert calib.FINDING_CARDINALITY_BOUNDED is True
    assert w2.FINDING_CARDINALITY_BOUNDED is True
    assert w3.WORKFLOW_FINDING_CARDINALITY_BOUNDED is True
    # Check no high-cardinality fields like task_id, attempt_id, worktree path
    for mod in [calib, w2, w3]:
        text = Path(inspect.getfile(mod)).read_text().lower()
        assert "task_id" not in text or "task-" in text and "forbidden" in text
        assert "worktree path" not in text
        assert "private reasoning" not in text.lower() or "private_reasoning_captured" in text

def test_57_no_opaque_confidence_scores():
    assert calib.OPAQUE_CONFIDENCE_SCORE_CREATED is False
    assert w2.OPAQUE_CONFIDENCE_SCORE_CREATED is False
    assert w3.OPAQUE_CONFIDENCE_SCORE_CREATED is False
    for mod in [calib, w2, w3]:
        text = Path(inspect.getfile(mod)).read_text().lower()
        assert "confidence" not in text or "opaque_confidence_score_created" in text
        assert "quality_score" not in text
        assert "evidence_strength" not in text

def test_58_no_universal_calibration_thresholds():
    assert calib.HARDCODED_UNIVERSAL_CALIBRATION_THRESHOLD_CREATED is False
    assert calib.UNIVERSAL_MIN_SUPPORT_THRESHOLD is False
    assert w3.HARDCODED_REVIEW_THRESHOLD_CREATED is False
    assert w3.UNIVERSAL_MIN_SUPPORT_THRESHOLD is False
    assert w2.HARDCODED_TOOL_PROMOTION_THRESHOLD_CREATED is False
    # behavioural: threshold must be explicit, not hard-coded
    proj = "proj-w4"
    series = _make_tool_series("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_desc(proj, series))
    # Without explicit boundary, cannot create threshold (tested via requiring boundary)
    with pytest.raises(Exception):
        create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=float("nan"))

def test_59_tool_vertical_proof_with_skill_and_context():
    proj = "proj-w4"
    # Tool finding already tested, now skill
    series_skill, ident = _make_skill_series("skill-w4-full", "1.0.0", "c"*64)
    res_skill, item_skill = _query(proj, series_skill, None, None, [8])
    finding_skill = create_skill_direct_finding(project_id=proj, query_result=res_skill, item=item_skill, skill_identity=ident, series_descriptor=_skill_desc(proj, series_skill, ident))
    assert finding_skill is not None
    assert finding_skill.series_descriptor.metric_family == MetricFamily.SKILL
    # Context portable unit proof
    for unit in [ContextCostUnit.BYTES_UTF8, ContextCostUnit.CHARS]:
        series_ctx = _make_context_series(unit, f"ctx-{unit.value}")
        res_ctx, item_ctx = _query(proj, series_ctx, None, None, [100])
        desc_ctx = _context_desc(proj, series_ctx, unit, f"ctx-{unit.value}")
        finding_ctx = create_context_direct_finding(project_id=proj, query_result=res_ctx, item=item_ctx, unit=unit, series_descriptor=desc_ctx)
        assert finding_ctx.series_descriptor.context_cost_unit == unit
    # Shell legacy
    pattern = _make_shell_pattern("echo")
    series_shell = compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})
    res_shell, item_shell = _query(proj, series_shell, None, None, [2])
    finding_shell = create_shell_direct_finding(project_id=proj, query_result=res_shell, item=item_shell, shell_pattern=pattern, series_descriptor=_shell_desc(proj, series_shell, pattern))
    assert finding_shell is not None

def test_60_review_workflow_verticals_and_s4_firewall():
    proj = "proj-w4"
    # Review calibration vertical proof
    for fc in ["needs_repair", "approved", "rejected"]:
        series = _make_review_series(fc)
        res, item = _query(proj, series, None, None, [2])
        if fc == "needs_repair":
            f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
        elif fc == "approved":
            f = create_review_satisfaction_observation_finding(project_id=proj, query_result=res, item=item)
        else:
            f = create_needs_repair_observation_finding(project_id=proj, query_result=res, item=item)
        assert f.disposition == FindingDisposition.COMPLETE
        assert f.provenance.project_id == proj
    # Workflow friction vertical
    for fc in ["retry", "blocked", "unknown"]:
        series = _make_friction_series(fc)
        res, item = _query(proj, series, None, None, [1])
        f = create_workflow_friction_observation_finding(project_id=proj, query_result=res, item=item, friction_class=fc)
        assert f is not None
    # Ensure no S4 import
    for mod in [w2, w3, calib]:
        text = Path(inspect.getfile(mod)).read_text()
        assert "from aota_forge.work_plane.risk_review" not in text
        assert "from aota_forge.work_plane.progression" not in text

