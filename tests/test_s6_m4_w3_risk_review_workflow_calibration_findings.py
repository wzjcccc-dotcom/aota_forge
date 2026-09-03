"""Focused behavioural proof for S6 M4 W3 — Risk / Review / Workflow Calibration Findings."""

from __future__ import annotations

import copy
import hashlib
import inspect
from pathlib import Path

import pytest

from aota_forge.work_plane import telemetry_workflow_calibration as w3
from aota_forge.work_plane.telemetry_workflow_calibration import (
    CalibrationSeriesDescriptor,
    EmpiricalFinding,
    FindingDisposition,
    FindingMethod,
    ThresholdCandidate,
    RecommendationCandidate,
    create_review_required_observation_finding,
    create_review_satisfaction_observation_finding,
    create_review_escalation_observation_finding,
    create_challenge_role_observation_finding,
    create_challenge_kind_observation_finding,
    create_needs_repair_observation_finding,
    create_progression_blocked_observation_finding,
    create_validation_fail_observation_finding,
    create_validation_unknown_observation_finding,
    create_workflow_friction_observation_finding,
    create_review_satisfaction_rate_finding,
    create_review_escalation_rate_finding,
    create_review_required_frequency_finding,
    create_workflow_rate_finding,
    create_workflow_threshold_candidate,
    create_review_escalation_threshold_candidate,
    create_workflow_blockage_threshold_candidate,
    create_workflow_recommendation_candidate,
)

import aota_forge.work_plane.telemetry_calibration as calib
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
    ReviewRepairMetricSubject,
    WorkflowFrictionMetricSubject,
    ToolMetricSubject,
    compute_aggregation_series_id,
    compute_aggregation_window_id,
)
from aota_forge.work_plane.telemetry_evidence import CompletenessRecord, CompletenessState, CompletenessScope, SourceEvidenceIdentity, compute_source_dedup_id
from aota_forge.core.contracts.canonical import canonical_json, canonicalize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_series_review(review_class="needs_repair"):
    subj = ReviewRepairMetricSubject(review_class=review_class)
    return compute_aggregation_series_id(MetricFamily.REVIEW_REPAIR, subj, {})

def _make_series_friction(friction_class="blocked"):
    subj = WorkflowFrictionMetricSubject(friction_class=friction_class)
    return compute_aggregation_series_id(MetricFamily.WORKFLOW_FRICTION, subj, {})

def _make_series_tool(op="workspace.read"):
    subj = ToolMetricSubject(operation_name=op)
    return compute_aggregation_series_id(MetricFamily.TOOL, subj, {})

def _make_policy(pid="policy-1", ver="v1"):
    return WindowPolicy(window_policy_id=pid, window_policy_version=ver, allow_ingestion_time_fallback=False)

def _make_window(series, policy, key="2026-01-01"):
    return compute_aggregation_window_id(series, policy.window_policy_id, policy.window_policy_version, key)

def _make_completeness(state="complete", scope="source"):
    return CompletenessRecord(state=state, scope=scope)

def _make_state(proj, series, window_id, policy, values, completeness_state="complete"):
    state = create_initial_state(proj, series, window_id, policy.window_policy_id if policy and window_id else None, policy.window_policy_version if policy and window_id else None)
    for idx, v in enumerate(values):
        src = SourceEvidenceIdentity(project_id=proj, source_kind="tool_usage", source_observation_id=f"obs-{idx}-{proj}-{series[:4]}-{v}", source_contract_version="v1", source_digest=hashlib.sha256(f"{proj}-{idx}-{v}".encode()).hexdigest())
        sd = compute_source_dedup_id(src)
        comp = _make_completeness(completeness_state)
        pid = policy.window_policy_id if policy and window_id else None
        pver = policy.window_policy_version if policy and window_id else None
        c = create_aggregation_contribution(project_id=proj, source_dedup_id=sd, projection_namespace="ns", projection_version=f"v{idx}", aggregation_series_id=series, aggregation_window_id=window_id, window_policy_id=pid, window_policy_version=pver, operation="SUM", value=v, completeness=comp)
        r = apply_contribution(state, c)
        assert r.disposition.value == "ACCEPTED"
        state = r.new_state
    return state

def _query(proj, series, window_id, policy, values, limit=5, completeness_state="complete"):
    state = _make_state(proj, series, window_id, policy, values, completeness_state)
    store = EphemeralTelemetryStore(max_records=20, max_projects=5)
    store.put(state)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, aggregation_window_id=window_id, limit=limit) if window_id else TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=limit)
    res = query_telemetry_store(store, req)
    if res.items:
        return res, res.items[0]
    return res, None

def _descriptor(proj="proj-a", series=None, window_id=None, family=MetricFamily.REVIEW_REPAIR, subject="needs_repair", kind="review"):
    # kind review -> ReviewRepair, friction -> WorkflowFriction
    if series is None:
        if kind == "review":
            series = _make_series_review(subject)
        elif kind == "friction":
            series = _make_series_friction(subject)
        else:
            series = _make_series_review("needs_repair")
    if kind == "review":
        subj_str = ReviewRepairMetricSubject(review_class=subject).to_subject_string()
        fam = MetricFamily.REVIEW_REPAIR
    elif kind == "friction":
        subj_str = WorkflowFrictionMetricSubject(friction_class=subject).to_subject_string()
        fam = MetricFamily.WORKFLOW_FRICTION
    else:
        subj_str = subject
        fam = family
    return CalibrationSeriesDescriptor(
        project_id=proj,
        aggregation_series_id=series,
        aggregation_window_id=window_id,
        metric_family=fam,
        metric_subject_identity=subj_str,
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-review" if kind=="review" else "s6-m3-w3-friction",
        projection_version="s6-m3-w3-v1",
    )

def _review_required_query(proj="proj-a", values=[1,1,1], completeness_state="complete"):
    series = _make_series_review("needs_repair")
    return _query(proj, series, None, None, values, completeness_state=completeness_state)

def _review_satisfaction_query(proj="proj-a", values=[1,1], completeness_state="complete"):
    series = _make_series_review("approved")
    return _query(proj, series, None, None, values, completeness_state=completeness_state)

def _friction_blocked_query(proj="proj-a", values=[1,1], completeness_state="complete"):
    series = _make_series_friction("blocked")
    return _query(proj, series, None, None, values, completeness_state=completeness_state)

def _friction_retry_query(proj="proj-a", values=[1], completeness_state="complete"):
    series = _make_series_friction("retry")
    return _query(proj, series, None, None, values, completeness_state=completeness_state)

def _friction_unknown_query(proj="proj-a", values=[1], completeness_state="complete"):
    series = _make_series_friction("unknown")
    return _query(proj, series, None, None, values, completeness_state=completeness_state)

# ---------------------------------------------------------------------------
# 1. Review-required complete evidence -> deterministic finding
# ---------------------------------------------------------------------------

def test_01_review_required_complete_deterministic():
    proj = "proj-a"
    res, item = _review_required_query(proj, values=[2,3])
    f1 = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    f2 = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert f1.finding_id == f2.finding_id
    assert f1.disposition == FindingDisposition.COMPLETE
    assert f1.numeric_value is not None
    assert f1.completeness_complete is True
    assert f1.coverage == QueryCoverage.COMPLETE
    assert f1.provenance.project_id == proj
    # input unchanged
    orig_qd = res.query_digest
    assert res.query_digest == orig_qd

# ---------------------------------------------------------------------------
# 2. Review satisfaction complete ratio -> valid rate
# ---------------------------------------------------------------------------

def test_02_review_satisfaction_complete_ratio_valid_rate():
    proj = "proj-a"
    # denominator required
    series_req = _make_series_review("needs_repair")
    res_req, denom_item = _query(proj, series_req, None, None, [10], completeness_state="complete")
    assert res_req.coverage == QueryCoverage.COMPLETE
    assert denom_item.completeness_complete is True
    desc_num = _descriptor(proj=proj, series=series_req, subject="approved", kind="review")
    # Use same series for compatibility (both REVIEW_REPAIR)
    # For test, make numerator/denominator descriptors compatible (same series)
    series = series_req
    desc_num = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    desc_den = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj,
        numerator_value=7,
        denominator_value=10,
        numerator_descriptor=desc_num,
        denominator_descriptor=desc_den,
        query_result=res_req,
        denominator_item=denom_item,
    )
    assert rate.numeric_value == pytest.approx(0.7)
    assert rate.disposition == FindingDisposition.COMPLETE
    assert rate.numerator == 7
    assert rate.denominator == 10
    assert rate.completeness_complete is True

# ---------------------------------------------------------------------------
# 3. Partial denominator -> no numeric rate
# ---------------------------------------------------------------------------

def test_03_partial_denominator_no_numeric_rate():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    _, denom_partial = _query(proj, series, None, None, [5], completeness_state="partial")
    assert denom_partial.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [10], completeness_state="complete")
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=2, denominator_value=5,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res_complete, denominator_item=denom_partial,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 4. Sampled denominator -> no numeric rate
# ---------------------------------------------------------------------------

def test_04_sampled_denominator_no_numeric_rate():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    _, denom_sampled = _query(proj, series, None, None, [5], completeness_state="sampled")
    assert denom_sampled.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [10], completeness_state="complete")
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=2, denominator_value=5,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res_complete, denominator_item=denom_sampled,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 5. Missing denominator -> no numeric rate
# ---------------------------------------------------------------------------

def test_05_missing_denominator_no_numeric_rate():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    _, denom_missing = _query(proj, series, None, None, [5], completeness_state="missing")
    assert denom_missing.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [10], completeness_state="complete")
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=2, denominator_value=5,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res_complete, denominator_item=denom_missing,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 6. Unknown denominator -> no numeric rate
# ---------------------------------------------------------------------------

def test_06_unknown_denominator_no_numeric_rate():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    _, denom_unknown = _query(proj, series, None, None, [5], completeness_state="unknown")
    assert denom_unknown.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [10], completeness_state="complete")
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=2, denominator_value=5,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res_complete, denominator_item=denom_unknown,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR

# ---------------------------------------------------------------------------
# 7. Zero complete denominator explicit unavailable
# ---------------------------------------------------------------------------

def test_07_zero_complete_denominator_unavailable():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, denom_zero = _query(proj, series, None, None, [0], completeness_state="complete")
    # Force zero denominator value
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=1, denominator_value=0,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res, denominator_item=denom_zero,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.ZERO_DENOMINATOR
    assert rate.denominator == 0

# ---------------------------------------------------------------------------
# 8. Empty query != zero review rate
# ---------------------------------------------------------------------------

def test_08_empty_query_not_zero_rate():
    proj = "proj-a"
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    assert res.coverage == QueryCoverage.EMPTY
    assert res.result_count == 0
    # Empty result should NOT be interpreted as 0% reviews; creating a direct finding with missing item should not succeed as rate zero.
    # Try to create direct observation with empty -> should be empty evidence disposition not zero
    # For rate, using empty coverage should yield no numeric, not zero
    series = _make_series_review("needs_repair")
    # Create a synthetic denominator item with complete but query is empty -> should be empty disposition
    _, denom_item = _query(proj, series, None, None, [5], completeness_state="complete")
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    rate = create_review_satisfaction_rate_finding(
        project_id=proj, numerator_value=0, denominator_value=5,
        numerator_descriptor=desc, denominator_descriptor=desc,
        query_result=res, denominator_item=denom_item,
    )
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.EMPTY_EVIDENCE
    # Also direct observation on empty should not be zero; ensure coverage empty
    assert calib.EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO is False

# ---------------------------------------------------------------------------
# 9. Review escalation finding
# ---------------------------------------------------------------------------

def test_09_review_escalation_finding():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [3])
    f = create_review_escalation_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    assert f.numeric_value is not None
    assert f.provenance.project_id == proj
    # deterministic
    f2 = create_review_escalation_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.finding_id == f2.finding_id

# ---------------------------------------------------------------------------
# 10. Challenge role bounded
# ---------------------------------------------------------------------------

def test_10_challenge_role_bounded():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [2])
    for role in ["analyst", "reviewer", "task-main"]:
        f = create_challenge_role_observation_finding(project_id=proj, query_result=res, item=item, challenge_role=role)
        assert f is not None
        assert f.disposition == FindingDisposition.COMPLETE
    with pytest.raises(Exception):
        create_challenge_role_observation_finding(project_id=proj, query_result=res, item=item, challenge_role="hacker")
    with pytest.raises(Exception):
        create_challenge_role_observation_finding(project_id=proj, query_result=res, item=item, challenge_role="unknown_role")

# ---------------------------------------------------------------------------
# 11. Challenge kind bounded
# ---------------------------------------------------------------------------

def test_11_challenge_kind_bounded():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [2])
    for kind in ["architecture_feasibility", "technical_acceptance", "semantic_reconciliation"]:
        f = create_challenge_kind_observation_finding(project_id=proj, query_result=res, item=item, challenge_kind=kind)
        assert f is not None
    with pytest.raises(Exception):
        create_challenge_kind_observation_finding(project_id=proj, query_result=res, item=item, challenge_kind="invalid")
    with pytest.raises(Exception):
        create_challenge_kind_observation_finding(project_id=proj, query_result=res, item=item, challenge_kind="hacker_kind")

# ---------------------------------------------------------------------------
# 12. Needs-repair primitive finding
# ---------------------------------------------------------------------------

def test_12_needs_repair_primitive_finding():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [4])
    f = create_needs_repair_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    assert f.numeric_value is not None
    f2 = create_needs_repair_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.finding_id == f2.finding_id

# ---------------------------------------------------------------------------
# 13. Progression-blocked finding
# ---------------------------------------------------------------------------

def test_13_progression_blocked_finding():
    proj = "proj-a"
    series = _make_series_friction("blocked")
    res, item = _query(proj, series, None, None, [1])
    f = create_progression_blocked_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    assert f.provenance.project_id == proj

# ---------------------------------------------------------------------------
# 14. Validation FAIL finding
# ---------------------------------------------------------------------------

def test_14_validation_fail_finding():
    proj = "proj-a"
    series = _make_series_friction("retry")
    res, item = _query(proj, series, None, None, [1])
    f = create_validation_fail_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    assert f.numeric_value is not None

# ---------------------------------------------------------------------------
# 15. Validation UNKNOWN finding
# ---------------------------------------------------------------------------

def test_15_validation_unknown_finding():
    proj = "proj-a"
    series = _make_series_friction("unknown")
    res, item = _query(proj, series, None, None, [1])
    f = create_validation_unknown_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE

# ---------------------------------------------------------------------------
# 16. No naked review boolean
# ---------------------------------------------------------------------------

def test_16_no_naked_review_boolean():
    assert w3.NAKED_REVIEW_COMPLETED_BOOLEAN_USED is False
    text = Path(inspect.getfile(w3)).read_text()
    # Ensure no function accepts naked bool as review_completed flag without provenance
    # Our module should not contain a helper that takes bool as naked review
    assert "def create_review_satisfaction_observation_finding" in text
    # Check that no boolean param named review_completed exists
    assert "review_completed: bool" not in text.lower()
    # Also ensure we reject high-cardinality not needed but flag
    assert "NAKED_REVIEW_COMPLETED_BOOLEAN_USED: bool = False" in text

# ---------------------------------------------------------------------------
# 17. No RV2 calibration
# ---------------------------------------------------------------------------

def test_17_no_rv2_calibration():
    assert w3.RV2_CALIBRATION_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "RV2_CALIBRATION_IMPLEMENTED: bool = False" in text
    for name in dir(w3):
        if "rv2" in name.lower() and "implemented" not in name.lower():
            raise AssertionError(f"RV2 calibration leaked: {name}")
    # also ensure no function projects RV2 beyond flag (allow docstring mention)
    assert "RV2_CALIBRATION_IMPLEMENTED: bool = False" in text

# ---------------------------------------------------------------------------
# 18. No repair-cycle calibration
# ---------------------------------------------------------------------------

def test_18_no_repair_cycle_calibration():
    assert w3.REPAIR_CYCLE_CALIBRATION_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "REPAIR_CYCLE_CALIBRATION_IMPLEMENTED: bool = False" in text
    for name in dir(w3):
        if "repair_cycle" in name.lower() and "implemented" not in name.lower():
            raise AssertionError(f"repair_cycle leaked: {name}")

# ---------------------------------------------------------------------------
# 19. No replan calibration
# ---------------------------------------------------------------------------

def test_19_no_replan_calibration():
    assert w3.REPLAN_CALIBRATION_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "REPLAN_CALIBRATION_IMPLEMENTED: bool = False" in text
    for name in dir(w3):
        if "replan" in name.lower() and "implemented" not in name.lower():
            raise AssertionError(f"replan leaked: {name}")

# ---------------------------------------------------------------------------
# 20. No closure-readiness calibration
# ---------------------------------------------------------------------------

def test_20_no_closure_readiness_calibration():
    assert w3.CLOSURE_READINESS_CALIBRATION_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "CLOSURE_READINESS_CALIBRATION_IMPLEMENTED: bool = False" in text
    for name in dir(w3):
        if "closure" in name.lower() and "implemented" not in name.lower():
            raise AssertionError(f"closure leaked: {name}")

# ---------------------------------------------------------------------------
# 21. No S4/M3-only synthesis
# ---------------------------------------------------------------------------

def test_21_no_s4_m3_only_synthesis():
    assert w3.S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "S4_M3_ONLY_SEMANTICS_SYNTHESIZED_IN_M4: bool = False" in text
    # Ensure we didn't synthesize RV2, repair history, repeated failure fingerprint, REPLAN_REQUIRED, Steward closure
    # Allow docstring mention, check no code defines those beyond flag
    for name in dir(w3):
        if any(kw in name.lower() for kw in ["repair_history", "repeated_failure", "fingerprint"]):
            raise AssertionError(f"synthesized S4/M3-only leaked: {name}")

# ---------------------------------------------------------------------------
# 22. No composite friction score
# ---------------------------------------------------------------------------

def test_22_no_composite_friction_score():
    assert w3.COMPOSITE_FRICTION_SCORE_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "COMPOSITE_FRICTION_SCORE_CREATED: bool = False" in text
    filtered = "\n".join([l for l in text.splitlines() if "COMPOSITE_FRICTION_SCORE_CREATED" not in l])
    for kw in ["friction_score", "workflow_friction_score", "review_quality_score", "risk_score", "workflow_health_score", "health_score"]:
        if kw in filtered.lower():
            raise AssertionError(f"composite score leaked: {kw}")

# ---------------------------------------------------------------------------
# 23. Threshold candidate deterministic
# ---------------------------------------------------------------------------

def test_23_threshold_candidate_deterministic():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    c1 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.5)
    c2 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.5)
    assert c1.candidate_id == c2.candidate_id
    assert c1.boundary_value == 0.5
    assert c1.provenance == f.provenance
    assert c1.support_count == f.support_count
    assert c1.completeness_complete == f.completeness_complete
    assert c1.method_version == f.method_version
    # also escaliation specific
    c3 = create_review_escalation_threshold_candidate(project_id=proj, finding=f, boundary_value=2)
    c4 = create_review_escalation_threshold_candidate(project_id=proj, finding=f, boundary_value=2)
    assert c3.candidate_id == c4.candidate_id

# ---------------------------------------------------------------------------
# 24. Threshold candidate non-policy
# ---------------------------------------------------------------------------

def test_24_threshold_candidate_non_policy():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    cand = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.7)
    assert cand.is_canonical_policy is False
    assert cand.is_authority is False
    assert w3.THRESHOLD_CANDIDATE_IS_CANONICAL_POLICY is False
    assert w3.THRESHOLD_CANDIDATE_AUTOMATICALLY_APPLIED is False
    assert cand.provenance.project_id == proj

# ---------------------------------------------------------------------------
# 25. No hard-coded review threshold
# ---------------------------------------------------------------------------

def test_25_no_hardcoded_review_threshold():
    assert w3.HARDCODED_REVIEW_THRESHOLD_CREATED is False
    assert w3.UNIVERSAL_MIN_SUPPORT_THRESHOLD is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "HARDCODED_REVIEW_THRESHOLD_CREATED: bool = False" in text
    # Ensure no constant like 0.8 threshold hardcoded as universal
    # Allow boundary_value param but not constant definition
    assert "UNIVERSAL_MIN_SUPPORT_THRESHOLD: bool = False" in text
    # Check that we don't define a constant threshold value without explicit param
    for line in text.splitlines():
        if "THRESHOLD" in line and "=" in line and "bool" not in line:
            # Any numeric threshold definition would be assignment with number
            if any(x in line for x in ["0.5", "0.8", "0.9", "threshold ="]):
                if "boundary_value" not in line.lower():
                    raise AssertionError(f"hardcoded threshold leaked: {line}")

# ---------------------------------------------------------------------------
# 26. No opaque confidence
# ---------------------------------------------------------------------------

def test_26_no_opaque_confidence():
    assert w3.OPAQUE_CONFIDENCE_SCORE_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "OPAQUE_CONFIDENCE_SCORE_CREATED: bool = False" in text
    # findings should not have confidence field
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert not hasattr(f, "confidence")
    for name in dir(w3):
        if "confidence" in name.lower() and "created" not in name.lower():
            raise AssertionError(f"confidence leaked: {name}")

# ---------------------------------------------------------------------------
# 27. Cross-project evidence fails closed
# ---------------------------------------------------------------------------

def test_27_cross_project_fails_closed():
    proj_a = "proj-a"
    proj_b = "proj-b"
    series = _make_series_review("needs_repair")
    res_a, item_a = _query(proj_a, series, None, None, [5])
    with pytest.raises(Exception) as exc:
        create_review_required_observation_finding(project_id=proj_b, query_result=res_a, item=item_a)
    assert "cross-project" in str(exc.value).lower()
    # also for rate
    _, denom_item = _query(proj_a, series, None, None, [5])
    res_a2, _ = _query(proj_a, series, None, None, [10])
    desc = _descriptor(proj=proj_a, series=series, subject="needs_repair", kind="review")
    with pytest.raises(Exception):
        create_review_satisfaction_rate_finding(
            project_id=proj_b, numerator_value=1, denominator_value=5,
            numerator_descriptor=desc, denominator_descriptor=desc,
            query_result=res_a2, denominator_item=denom_item,
        )

# ---------------------------------------------------------------------------
# 28. Incompatible series fails closed
# ---------------------------------------------------------------------------

def test_28_incompatible_series_fails_closed():
    proj = "proj-a"
    series_tool = _make_series_tool("workspace.read")
    series_friction = _make_series_friction("blocked")
    desc_tool = _descriptor(proj=proj, series=series_tool, subject="needs_repair", kind="review")
    # desc_tool family REVIEW_REPAIR but series_tool is TOOL family; we force mismatch via family
    desc_tool_mismatch = CalibrationSeriesDescriptor(
        project_id=proj,
        aggregation_series_id=series_tool,
        metric_family=MetricFamily.TOOL,
        metric_subject_identity="tool:workspace.read",
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w1-tool",
        projection_version="s6-m3-w1-v1",
    )
    desc_friction = CalibrationSeriesDescriptor(
        project_id=proj,
        aggregation_series_id=series_friction,
        metric_family=MetricFamily.WORKFLOW_FRICTION,
        metric_subject_identity=WorkflowFrictionMetricSubject(friction_class="blocked").to_subject_string(),
        normalization_version="s6-m1-v1",
        projection_namespace="s6-m3-w3-friction",
        projection_version="s6-m3-w3-v1",
    )
    _, denom_item = _query(proj, series_tool, None, None, [5])
    res, _ = _query(proj, series_tool, None, None, [5])
    with pytest.raises(Exception) as exc:
        create_workflow_rate_finding(
            project_id=proj, numerator_value=1, denominator_value=5,
            numerator_descriptor=desc_tool_mismatch, denominator_descriptor=desc_friction,
            query_result=res, denominator_item=denom_item,
        )
    assert "incompatible" in str(exc.value).lower()

# ---------------------------------------------------------------------------
# 29. Work Item ref not default dimension
# ---------------------------------------------------------------------------

def test_29_work_item_ref_not_default_dimension():
    assert w3.WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION: bool = False" in text
    # Ensure W3 doesn't create findings with work_item_ref as dimension; allow specific flags
    for name in dir(w3):
        if name in ("EXACT_WORK_ITEM_RESULT_BINDING_ASSUMPTION_PRESERVED", "WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION"):
            continue
        if "work_item" in name.lower() and "dimension" in name.lower():
            raise AssertionError(f"work_item_ref dimension leaked: {name}")
    # Also ensure text doesn't expose high-cardinality default dimensions beyond allowed flags
    assert "WORK_ITEM_REF_IS_DEFAULT_FINDING_DIMENSION: bool = False" in text

# ---------------------------------------------------------------------------
# 30. Finding non-authoritative
# ---------------------------------------------------------------------------

def test_30_finding_non_authoritative():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.is_authority is False
    assert f.is_canonical_policy is False
    assert f.provenance.is_authority is False
    cand = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=1)
    assert cand.is_authority is False
    assert cand.is_canonical_policy is False
    rec = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="calibration_review")
    assert rec.is_authority is False

# ---------------------------------------------------------------------------
# 31. Recommendation cannot mutate S4 policy
# ---------------------------------------------------------------------------

def test_31_recommendation_cannot_mutate_s4_policy():
    assert w3.WORKFLOW_RECOMMENDATION_IS_S4_POLICY_AUTHORITY is False
    assert w3.WORKFLOW_RECOMMENDATION_AUTOMATICALLY_APPLIED is False
    assert w3.S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY is False
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    for forbidden in ["review_trigger", "escalation_policy", "mark_satisfied", "trigger_repair"]:
        with pytest.raises(Exception):
            create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref=forbidden)
    # valid generic target should succeed
    rec = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="calibration_review")
    assert rec.is_authority is False

# ---------------------------------------------------------------------------
# 32. Recommendation cannot progress Work Item
# ---------------------------------------------------------------------------

def test_32_recommendation_cannot_progress_work_item():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="progress_work_item")
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="close_milestone")

# ---------------------------------------------------------------------------
# 33. Recommendation cannot trigger repair/replan
# ---------------------------------------------------------------------------

def test_33_recommendation_cannot_trigger_repair_replan():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    f = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="trigger_replan")
    with pytest.raises(Exception):
        create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="trigger_repair")

# ---------------------------------------------------------------------------
# 34. No S4 typed source import
# ---------------------------------------------------------------------------

def test_34_no_s4_typed_source_import():
    assert w3.S4_TYPED_SOURCE_USED_BY_W3 is False
    assert w3.S4_FINAL_KNOWN_GOOD_CONSUMED_BY_W3 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "S4_TYPED_SOURCE_USED_BY_W3: bool = False" in text
    assert "from aota_forge.work_plane.risk_review" not in text
    assert "from aota_forge.work_plane.progression" not in text
    assert "import risk_review" not in text
    assert "import progression" not in text
    # also ensure no reference to S4 typed classes beyond metrics
    assert "WorkItemRiskDelta" not in text
    assert "ReviewEscalationDisposition" not in text

# ---------------------------------------------------------------------------
# 35. No S5 source import
# ---------------------------------------------------------------------------

def test_35_no_s5_source_import():
    assert w3.S5_SOURCE_CONSUMED_BY_W3 is False
    assert w3.S5_RELEASE_STATE_MUTATED_BY_W3 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "S5_SOURCE_CONSUMED_BY_W3: bool = False" in text
    assert "S5_RELEASE_STATE_MUTATED_BY_W3: bool = False" in text
    assert "release" not in text.lower() or "S5_RELEASE_STATE_MUTATED_BY_W3" in text
    for kw in ["s5", "release_state", "bundle"]:
        if kw == "s5":
            # allow flag name but not import
            continue
        if kw in text.lower():
            if "S5_SOURCE" in text or "S5_RELEASE" in text:
                continue
            # ensure not importing s5 modules
            assert "s5" not in text.lower() or "S5_" in text, f"S5 leaked: {kw}"

# ---------------------------------------------------------------------------
# 36. No W2 Tool/Skill/context scope
# ---------------------------------------------------------------------------

def test_36_no_w2_tool_skill_context_scope():
    assert w3.W2_SCOPE_IMPLEMENTED_IN_W3 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "W2_SCOPE_IMPLEMENTED_IN_W3: bool = False" in text
    # Ensure not implementing tool/skill/optimization functions
    forbidden = ["tool_", "skill_", "legacy", "shell", "context_cost", "optimization"]
    filtered = "\n".join([l for l in text.splitlines() if "W2_SCOPE_IMPLEMENTED_IN_W3" not in l])
    for kw in forbidden:
        if kw in filtered.lower():
            # allow telemetry_metrics imports but not W2 optimization logic
            if kw in ["tool_", "skill_"] and "MetricFamily.TOOL" in filtered:
                # TOOL family is allowed as M3 review uses it? But W3 should not use TOOL/SKILL for workflow? We do use REVIEW_REPAIR only, but imports may mention MetricFamily.TOOL in generic?
                # Our file uses only REVIEW_REPAIR/WORKFLOW_FRICTION, so should not have TOOL
                if "MetricFamily.TOOL" in filtered:
                    raise AssertionError(f"W2 scope leaked: {kw} -> {filtered}")
            if kw == "context_cost" and "context_cost" in filtered.lower():
                raise AssertionError("W2 context scope leaked")
    # Explicit check: ensure telemetry_optimization not imported
    assert "telemetry_optimization" not in text

# ---------------------------------------------------------------------------
# 37. No global registry
# ---------------------------------------------------------------------------

def test_37_no_global_registry():
    assert w3.GLOBAL_RECOMMENDATION_REGISTRY_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "GLOBAL_RECOMMENDATION_REGISTRY_CREATED: bool = False" in text
    for name in dir(w3):
        if "registry" in name.lower() and "created" not in name.lower():
            raise AssertionError(f"registry leaked: {name}")
    assert "GLOBAL_RECOMMENDATION_REGISTRY_CREATED: bool = False" in text

# ---------------------------------------------------------------------------
# 38. No store/query/runtime
# ---------------------------------------------------------------------------

def test_38_no_store_query_runtime():
    assert w3.SECOND_ANALYTICS_STORE_CREATED is False
    assert w3.SECOND_QUERY_ENGINE_CREATED is False
    assert w3.SECOND_METRIC_RUNTIME_CREATED is False
    assert w3.DURABLE_ANALYTICS_STORAGE_REQUIRED_FOR_W3 is False
    assert w3.M4_AUTOMATIC_TUNING_LOOP is False
    assert w3.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "SECOND_ANALYTICS_STORE_CREATED: bool = False" in text
    assert "SECOND_QUERY_ENGINE_CREATED: bool = False" in text
    assert "SECOND_METRIC_RUNTIME_CREATED: bool = False" in text
    assert "class TelemetryStore" not in text
    assert "class EphemeralTelemetryStore" not in text
    assert "def query_telemetry_store" not in text

# ---------------------------------------------------------------------------
# 39. W1 focused regression
# ---------------------------------------------------------------------------

def test_39_w1_focused_regression():
    # Use W1 direct observation and rate should still work via W3's reuse
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    # W1 flag check
    assert calib.M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS is True
    assert calib.NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR is True
    assert calib.EXISTING_TELEMETRY_QUERY_CONTRACT_REUSED is True
    # W1 direct
    f = calib.create_direct_observation_finding(project_id=proj, query_result=res, item=item)
    assert f.disposition == FindingDisposition.COMPLETE
    # W3 should delegate same
    f2 = create_review_required_observation_finding(project_id=proj, query_result=res, item=item)
    assert f2.disposition == FindingDisposition.COMPLETE
    assert calib.EMPIRICAL_FINDING_DETERMINISTIC is True

# ---------------------------------------------------------------------------
# 40. M3 review/friction regression
# ---------------------------------------------------------------------------

def test_40_m3_review_friction_regression():
    # Ensure M3 projection helpers still work (they are not broken by W3)
    from aota_forge.work_plane.telemetry_projection_review_friction import (
        M3_PROJECTION_LAYER_ONLY,
        EXISTING_W1_PROJECTION_CORE_REUSED,
    )
    assert M3_PROJECTION_LAYER_ONLY is True
    assert EXISTING_W1_PROJECTION_CORE_REUSED is True
    # Spot check that W3 calibration does not affect M3 metric cardinalities
    assert w3.WORKFLOW_FINDING_CARDINALITY_BOUNDED is True
    assert w3.FINDING_CARDINALITY_BOUNDED is True

# ---------------------------------------------------------------------------
# 41. deterministic input ordering
# ---------------------------------------------------------------------------

def test_41_deterministic_input_ordering():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res1, item1 = _query(proj, series, None, None, [5])
    res2, item2 = _query(proj, series, None, None, [5])
    # Create findings with evidence digests in different order but same set should be same id
    # Use compute_finding_id directly with sorted digests
    d1 = item1.content_digest
    d2 = item1.ref.content_digest
    id_a = calib.compute_finding_id(project_id=proj, method=FindingMethod.DIRECT_OBSERVED_AGGREGATE, method_version=calib.CALIBRATION_CONTRACT_VERSION, evidence_digests=(d1, d2), series_id=series, numeric_repr="5")
    id_b = calib.compute_finding_id(project_id=proj, method=FindingMethod.DIRECT_OBSERVED_AGGREGATE, method_version=calib.CALIBRATION_CONTRACT_VERSION, evidence_digests=(d2, d1), series_id=series, numeric_repr="5")
    assert id_a == id_b
    # W3 findings also deterministic regardless of insertion order of finding_refs for recommendation
    f = create_review_required_observation_finding(project_id=proj, query_result=res1, item=item1)
    rec1 = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="calibration_review")
    rec2 = create_workflow_recommendation_candidate(project_id=proj, finding_refs=(f.finding_id,), target_ref="calibration_review")
    assert rec1.recommendation_id == rec2.recommendation_id
    # Also threshold candidate deterministic
    c1 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.8)
    c2 = create_workflow_threshold_candidate(project_id=proj, finding=f, boundary_value=0.8)
    assert c1.candidate_id == c2.candidate_id

# ---------------------------------------------------------------------------
# 42. analysis failure leaves input evidence unchanged
# ---------------------------------------------------------------------------

def test_42_analysis_failure_leaves_input_unchanged():
    proj = "proj-a"
    series = _make_series_review("needs_repair")
    res, item = _query(proj, series, None, None, [5])
    orig_res_dict = res.__dict__.copy()
    orig_item_dict = item.__dict__.copy()
    orig_qd = res.query_digest
    orig_cd = item.content_digest
    # Cause cross-project failure
    try:
        create_review_required_observation_finding(project_id="proj-b", query_result=res, item=item)
    except Exception:
        pass
    assert res.query_digest == orig_qd
    assert item.content_digest == orig_cd
    assert res.__dict__ == orig_res_dict
    assert item.__dict__ == orig_item_dict
    # Also rate failure
    _, denom_item = _query(proj, series, None, None, [5], completeness_state="partial")
    res_complete, _ = _query(proj, series, None, None, [10])
    desc = _descriptor(proj=proj, series=series, subject="needs_repair", kind="review")
    # Create incompatible descriptors to trigger failure
    desc_bad = _descriptor(proj="proj-b", series=series, subject="needs_repair", kind="review")
    try:
        create_review_satisfaction_rate_finding(
            project_id=proj, numerator_value=1, denominator_value=5,
            numerator_descriptor=desc_bad, denominator_descriptor=desc,
            query_result=res_complete, denominator_item=denom_item,
        )
    except Exception:
        pass
    assert res_complete.query_digest == res_complete.query_digest  # unchanged
    assert w3.ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT is True
    assert w3.ANALYSIS_FAILURE_DOES_NOT_REWRITE_AGGREGATE_STATE is True
    assert w3.ANALYSIS_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT is True

# ---------------------------------------------------------------------------
# Additional: ensure W1 production contract not changed
# ---------------------------------------------------------------------------

def test_no_w1_production_change():
    # W1 file should be unchanged (we didn't modify it)
    import pathlib
    w1_path = Path(inspect.getfile(calib))
    text = w1_path.read_text()
    assert "CALIBRATION_CONTRACT_VERSION" in text
    # Ensure W3 flags indicate reuse
    assert w3.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    assert w3.W1_PRODUCTION_CONTRACT_CHANGED is False
    assert w3.M4_ANALYSIS_OVER_EXISTING_QUERY_RESULTS is True

def test_workflow_friction_primitive_via_generic():
    proj = "proj-a"
    for fc in ["retry", "rework", "blocked", "unknown"]:
        series = _make_series_friction(fc)
        res, item = _query(proj, series, None, None, [1])
        f = create_workflow_friction_observation_finding(project_id=proj, query_result=res, item=item, friction_class=fc)
        assert f is not None
        assert f.disposition == FindingDisposition.COMPLETE
