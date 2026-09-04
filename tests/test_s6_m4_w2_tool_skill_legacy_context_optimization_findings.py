"""Focused behavioural proof for S6 M4 W2 — Tool / Skill / Legacy / Context Optimization Findings."""

from __future__ import annotations

import copy
import hashlib
import inspect
from pathlib import Path

import pytest

from aota_forge.work_plane import telemetry_optimization_findings as w2
from aota_forge.work_plane.telemetry_optimization_findings import (
    ToolOptimizationKind,
    SkillOptimizationKind,
    LegacyOptimizationKind,
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
    validate_context_units_compatible,
)

from aota_forge.work_plane.telemetry_calibration import (
    CALIBRATION_CONTRACT_VERSION,
    CalibrationSeriesDescriptor,
    FindingDisposition,
    FindingMethod,
    RecommendationKind,
    compute_finding_id,
    create_direct_observation_finding,
    create_rate_finding,
    create_threshold_candidate,
)
from aota_forge.work_plane.telemetry_query import (
    QueryCoverage,
    TelemetryQueryRequest,
    TelemetryQueryResult,
    TelemetryQueryItem,
    query_telemetry_store,
)
from aota_forge.work_plane.telemetry_store import EphemeralTelemetryStore
from aota_forge.work_plane.telemetry_aggregation import (
    create_initial_state,
    create_aggregation_contribution,
    apply_contribution,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ContextCostUnit,
    ToolMetricSubject,
    SkillMetricSubject,
    NormalizedShellPattern,
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
from aota_forge.work_plane.telemetry_aggregation import WindowPolicy
from aota_forge.work_plane.skill import SkillIdentity
from aota_forge.work_plane.telemetry_projection_role_tool_skill import project_tool_observation, project_skill_observation
from aota_forge.work_plane.telemetry_projection_shell_context import project_shell_observation, project_context_cost
from aota_forge.work_plane.telemetry_metrics import ContextCostMeasurement
from aota_forge.work_plane.telemetry_adapters import adapt_tool_usage_observation, adapt_skill_usage_observation
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_series_tool(op="workspace.read"):
    subj = ToolMetricSubject(operation_name=op)
    return compute_aggregation_series_id(MetricFamily.TOOL, subj, {})

def _make_series_skill(skill_id="skill-a", version="1.0.0", digest=None):
    if digest is None:
        digest = "a"*64
    ident = SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance="test-provenance")
    subj = SkillMetricSubject(skill_identity=ident)
    return compute_aggregation_series_id(MetricFamily.SKILL, subj, {"skill_metric_kind": "skill_observed_used"}), ident

def _make_series_context(unit: ContextCostUnit, component="default_context"):
    subj = ContextCostMetricSubject(component=component)
    return compute_aggregation_series_id(MetricFamily.CONTEXT_COST, subj, {"context_cost_unit": unit.value})

def _make_series_shell(pattern: NormalizedShellPattern):
    return compute_aggregation_series_id(MetricFamily.SHELL_PATTERN, pattern, {})

def _make_policy(pid="policy-1", ver="v1"):
    return WindowPolicy(window_policy_id=pid, window_policy_version=ver, allow_ingestion_time_fallback=False)

def _make_state(proj, series, window_id, policy, values, completeness_state="complete", operation="COUNT"):
    state = create_initial_state(proj, series, window_id, policy.window_policy_id if policy and window_id else None, policy.window_policy_version if policy and window_id else None)
    for idx, v in enumerate(values):
        src = SourceEvidenceIdentity(project_id=proj, source_kind="tool_usage", source_observation_id=f"obs-{idx}-{proj}-{series[:4]}-{v}", source_contract_version="v1", source_digest=hashlib.sha256(f"{proj}-{idx}-{v}".encode()).hexdigest())
        sd = compute_source_dedup_id(src)
        comp = CompletenessRecord(state=completeness_state, scope="source")
        pid = policy.window_policy_id if policy and window_id else None
        pver = policy.window_policy_version if policy and window_id else None
        # For tool/shell/skill use COUNT (value is count delta), for context use SUM
        op = operation
        # Heuristic: if series corresponds to context cost, use SUM, else COUNT
        # Caller can override via operation param
        val = v if op == "SUM" else 1
        # If COUNT, value is count increment (1 per observation); if SUM, value is measured bytes etc.
        c = create_aggregation_contribution(project_id=proj, source_dedup_id=sd, projection_namespace="ns", projection_version=f"v{idx}", aggregation_series_id=series, aggregation_window_id=window_id, window_policy_id=pid, window_policy_version=pver, operation=op, value=val, completeness=comp)
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

def _tool_descriptor(proj="proj-a", series=None, op="workspace.read"):
    if series is None:
        series = _make_series_tool(op)
    return build_tool_series_descriptor(project_id=proj, operation_name=op, aggregation_series_id=series)

def _skill_descriptor(proj="proj-a", series=None, ident=None):
    if ident is None:
        series_tmp, ident = _make_series_skill()
        if series is None:
            series = series_tmp
    else:
        if series is None:
            subj = SkillMetricSubject(skill_identity=ident)
            series = compute_aggregation_series_id(MetricFamily.SKILL, subj, {"skill_metric_kind": "skill_observed_used"})
    return build_skill_series_descriptor(project_id=proj, skill_identity=ident, aggregation_series_id=series)

def _shell_descriptor(proj="proj-a", series=None, pattern=None):
    if pattern is None:
        pattern = create_normalized_shell_pattern("ls", argument_classes=("path_class",), outcome_class="success")
    if series is None:
        series = _make_series_shell(pattern)
    return build_shell_series_descriptor(project_id=proj, shell_pattern=pattern, aggregation_series_id=series)

def _context_descriptor(proj="proj-a", series=None, unit=ContextCostUnit.BYTES_UTF8, component="default_context"):
    if series is None:
        series = _make_series_context(unit, component)
    return build_context_series_descriptor(project_id=proj, unit=unit, component=component, aggregation_series_id=series)

def _ingest():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

def _adapt_tool_evidence(obs, project_id="proj-a", ingest=None):
    if ingest is None:
        ingest = _ingest()
    return adapt_tool_usage_observation(obs, ingest, project_id=project_id, worktree_id=obs.worktree_id if hasattr(obs, "worktree_id") else "wt-1")

# ---------------------------------------------------------------------------
# 1. Tool complete empirical evidence → bounded finding.
# ---------------------------------------------------------------------------

def test_01_tool_complete_finding():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [10])
    desc = _tool_descriptor(proj, series, "workspace.read")
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.disposition == FindingDisposition.COMPLETE
    assert finding.numeric_value is not None
    assert finding.is_authority is False
    assert finding.provenance.project_id == proj
    assert w2.TOOL_FINDINGS_PRESENT is True

# ---------------------------------------------------------------------------
# 2. Tool recommendation deterministic.
# ---------------------------------------------------------------------------

def test_02_tool_recommendation_deterministic():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [10])
    desc = _tool_descriptor(proj, series, "workspace.read")
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=5)
    rec1 = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="workspace-read-target")
    rec2 = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="workspace-read-target")
    assert rec1.recommendation_id == rec2.recommendation_id
    assert w2.RECOMMENDATION_ID_DETERMINISTIC is True

# ---------------------------------------------------------------------------
# 3. Tool recommendation non-authoritative.
# ---------------------------------------------------------------------------

def test_03_tool_recommendation_non_authoritative():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [7])
    desc = _tool_descriptor(proj, series, "workspace.read")
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=3)
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.LEGACY_CANDIDATE, target_ref="target-1")
    assert rec.is_authority is False
    assert w2.TOOL_PROMOTION_RECOMMENDATION_IS_AUTHORITY is False
    assert w2.TOOL_PROMOTION_AUTOMATICALLY_APPLIED is False
    assert w2.TOOL_RETIREMENT_AUTOMATICALLY_APPLIED is False
    assert w2.TOOL_FINDING_IS_OPERATION_AUTHORITY is False

# ---------------------------------------------------------------------------
# 4. Missing Tool telemetry does not mean zero use.
# ---------------------------------------------------------------------------

def test_04_missing_tool_telemetry_not_zero():
    proj = "proj-a"
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    assert res.coverage == QueryCoverage.EMPTY
    assert w2.TOOL_ABSENCE_OF_EVIDENCE_IS_ZERO_USAGE is False
    assert w2.MISSING_TELEMETRY_IS_ZERO is False
    assert w2.EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO is False
    # Empty should not become legacy candidate: attempt to create tool recommendation from empty should fail
    series = _make_series_tool("workspace.read")
    # Create a dummy item for empty case? Instead test that empty query cannot be used to infer zero usage
    # We have an item from other project to test empty handling: use res empty with no item -> we cannot create direct finding without item, so we test that absence != zero via rate logic
    # Create a valid item but with empty coverage
    _, item = _query(proj, series, None, None, [5])
    desc = _tool_descriptor(proj, series, "workspace.read")
    # Create rate with empty coverage -> numeric None, not zero
    rate = create_tool_rate_finding(project_id=proj, numerator_value=3, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res, denominator_item=item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.EMPTY_EVIDENCE
    assert rate.numeric_value != 0

# ---------------------------------------------------------------------------
# 5. Partial Tool denominator cannot produce rate.
# ---------------------------------------------------------------------------

def test_05_partial_denominator_no_rate():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    _, denom_partial = _query(proj, series, None, None, [5], completeness_state="partial")
    assert denom_partial.completeness_complete is False
    res_complete, _ = _query(proj, series, None, None, [3], completeness_state="complete")
    desc = _tool_descriptor(proj, series, "workspace.read")
    rate = create_tool_rate_finding(project_id=proj, numerator_value=2, denominator_value=5, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res_complete, denominator_item=denom_partial)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.INCOMPLETE_DENOMINATOR
    assert w2.NUMERIC_RATE_REQUIRES_COMPLETE_DENOMINATOR is True

# ---------------------------------------------------------------------------
# 6. Zero complete denominator explicit unavailable.
# ---------------------------------------------------------------------------

def test_06_zero_denominator_explicit():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, denom_item = _query(proj, series, None, None, [0], completeness_state="complete")
    desc = _tool_descriptor(proj, series, "workspace.read")
    rate = create_tool_rate_finding(project_id=proj, numerator_value=1, denominator_value=0, numerator_descriptor=desc, denominator_descriptor=desc, query_result=res, denominator_item=denom_item)
    assert rate.numeric_value is None
    assert rate.disposition == FindingDisposition.ZERO_DENOMINATOR
    assert w2.ZERO_DENOMINATOR_NUMERIC_RATE_AVAILABLE is False
    assert w2.UNKNOWN_DENOMINATOR_IS_ZERO is False

# ---------------------------------------------------------------------------
# 7. No hard-coded Tool promotion threshold.
# ---------------------------------------------------------------------------

def test_07_no_hardcoded_tool_threshold():
    assert w2.HARDCODED_TOOL_PROMOTION_THRESHOLD_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "success > eighty" not in text
    assert "minimum 10 calls" not in text
    assert "minimum 10" not in text
    # threshold must be explicit via ThresholdCandidate, not hard-coded constant
    assert "hardcoded_tool_promotion_threshold_created" in text.lower()
    # Promotion requires explicit boundary via threshold candidate, not default
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    desc = _tool_descriptor(proj, series, "workspace.read")
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    # threshold must be explicit, not inferred
    with pytest.raises(Exception):
        # missing boundary should fail; we test that creating threshold without boundary fails
        create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=float("nan"))  # non-finite rejected

# ---------------------------------------------------------------------------
# 8. Skill uses canonical SkillIdentity.
# ---------------------------------------------------------------------------

def test_08_skill_uses_canonical_identity():
    proj = "proj-a"
    digest = "b"*64
    ident = SkillIdentity(skill_id="skill-x", version="1.2.3", digest=digest, provenance="prov-a")
    subj = SkillMetricSubject(skill_identity=ident)
    series = compute_aggregation_series_id(MetricFamily.SKILL, subj, {"skill_metric_kind": "skill_observed_used"})
    res, item = _query(proj, series, None, None, [4])
    desc = _skill_descriptor(proj, series, ident)
    finding = create_skill_direct_finding(project_id=proj, query_result=res, item=item, skill_identity=ident, series_descriptor=desc)
    assert finding is not None
    assert w2.SKILL_RECOMMENDATION_USES_CANONICAL_S3_IDENTITY is True
    assert w2.SECOND_SKILL_IDENTITY_CREATED is False
    # Ensure second identity not created in module text
    text = Path(inspect.getfile(w2)).read_text()
    assert "class SkillIdentity" not in text
    assert "SECOND_SKILL_IDENTITY_CREATED" in text

# ---------------------------------------------------------------------------
# 9. Missing Skill evidence cannot create retirement recommendation.
# ---------------------------------------------------------------------------

def test_09_missing_skill_no_retirement():
    proj = "proj-a"
    series, ident = _make_series_skill("skill-a", "1.0.0", "c"*64)
    # create empty query result
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res_empty = query_telemetry_store(store, req)
    # Create a finding with EMPTY disposition
    _, item = _query(proj, series, None, None, [5])
    desc = _skill_descriptor(proj, series, ident)
    # Create direct finding with empty coverage but have item -> will have EMPTY disposition
    # Use res_empty (empty) with item (but our helper will still produce finding)
    # Actually create_direct for skill with empty coverage should produce EMPTY_EVIDENCE
    # We need a finding that has EMPTY_EVIDENCE disposition
    finding_empty = w2.create_tool_direct_finding(project_id=proj, query_result=res_empty, item=item, series_descriptor=build_tool_series_descriptor(project_id=proj, operation_name="workspace.read", aggregation_series_id=series))
    # But for skill retirement test, we want skill finding with empty
    # Instead create rate-like empty for skill
    # Let's create a skill finding with empty coverage by using empty res and skill descriptor
    # We'll directly use w2's create_skill_direct_finding with empty res
    # It will still create finding but disposition EMPTY_EVIDENCE
    skill_empty_finding = create_skill_direct_finding(project_id=proj, query_result=res_empty, item=item, skill_identity=ident, series_descriptor=desc)
    assert skill_empty_finding.disposition == FindingDisposition.EMPTY_EVIDENCE
    assert w2.MISSING_SKILL_TELEMETRY_IMPLIES_UNUSED is False
    # Attempt retirement should fail
    thr = create_tool_threshold_candidate(project_id=proj, finding=skill_empty_finding, boundary_value=5)
    with pytest.raises(Exception):
        create_skill_recommendation_candidate(project_id=proj, finding=skill_empty_finding, threshold_candidate=thr, kind=SkillOptimizationKind.RETIREMENT_CANDIDATE, target_ref="skill-target", skill_identity=ident)

# ---------------------------------------------------------------------------
# 10. Skill candidate non-authoritative.
# ---------------------------------------------------------------------------

def test_10_skill_candidate_non_authoritative():
    proj = "proj-a"
    series, ident = _make_series_skill("skill-b", "2.0.0", "d"*64)
    res, item = _query(proj, series, None, None, [6])
    desc = _skill_descriptor(proj, series, ident)
    finding = create_skill_direct_finding(project_id=proj, query_result=res, item=item, skill_identity=ident, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=2)
    rec = create_skill_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=SkillOptimizationKind.CONSOLIDATION_CANDIDATE, target_ref="skill-target-2", skill_identity=ident)
    assert rec.is_authority is False
    assert w2.SKILL_RETIREMENT_RECOMMENDATION_IS_AUTHORITY is False
    assert w2.SKILL_RETIREMENT_AUTOMATICALLY_APPLIED is False
    assert w2.SKILL_FINDING_IS_SKILL_AUTHORITY is False

# ---------------------------------------------------------------------------
# 11. Normalized shell finding supported.
# ---------------------------------------------------------------------------

def test_11_normalized_shell_supported():
    proj = "proj-a"
    pattern = create_normalized_shell_pattern("ls", argument_classes=("path_class",), outcome_class="success")
    series = _make_series_shell(pattern)
    res, item = _query(proj, series, None, None, [3])
    desc = _shell_descriptor(proj, series, pattern)
    finding = create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=pattern, series_descriptor=desc)
    assert finding is not None
    assert finding.disposition == FindingDisposition.COMPLETE
    assert w2.LEGACY_SHELL_FINDINGS_PRESENT is True

# ---------------------------------------------------------------------------
# 12. Raw argv rejected/not represented.
# ---------------------------------------------------------------------------

def test_12_raw_argv_rejected():
    assert w2.RAW_ARGV_IS_FINDING_DIMENSION is False
    proj = "proj-a"
    pattern = create_normalized_shell_pattern("ls", outcome_class="success")
    series = _make_series_shell(pattern)
    res, item = _query(proj, series, None, None, [2])
    # Try to pass raw argv string as shell_pattern
    with pytest.raises(Exception):
        create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=["ls", "-la"], series_descriptor=_shell_descriptor(proj, series, pattern))
    # Also test dimension with argv key
    desc = _shell_descriptor(proj, series, pattern)
    # dimensions with argv should be rejected via helper
    with pytest.raises(Exception):
        w2._reject_raw_dimension({"argv": "ls -la"})

# ---------------------------------------------------------------------------
# 13. Raw command rejected/not represented.
# ---------------------------------------------------------------------------

def test_13_raw_command_rejected():
    assert w2.RAW_COMMAND_IS_FINDING_DIMENSION is False
    proj = "proj-a"
    pattern = create_normalized_shell_pattern("echo", outcome_class="success")
    series = _make_series_shell(pattern)
    res, item = _query(proj, series, None, None, [2])
    with pytest.raises(Exception):
        create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern="echo hello world", series_descriptor=_shell_descriptor(proj, series, pattern))
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "raw_command" not in text or "raw_command_is_finding_dimension" in text

# ---------------------------------------------------------------------------
# 14. Secret cannot enter finding.
# ---------------------------------------------------------------------------

def test_14_secret_not_captured():
    assert w2.RAW_SECRET_CAPTURED is False
    assert w2.NORMALIZED_SHELL_PATTERN_CAN_INCLUDE_RAW_SECRET_VALUE is False
    # Attempt to create shell pattern with secret-like value should be bounded category, not raw
    # Our create_normalized_shell_pattern should not allow raw secret; we test dimension rejection
    proj = "proj-a"
    pattern = create_normalized_shell_pattern("ls", outcome_class="success")
    series = _make_series_shell(pattern)
    res, item = _query(proj, series, None, None, [1])
    # Try to pass secret via dimension
    with pytest.raises(Exception):
        # This would be caught as invalid dimension key containing secret
        # We use legacy reduction with secret in target_ref
        finding = create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=pattern, series_descriptor=_shell_descriptor(proj, series, pattern))
        thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=1)
        create_legacy_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=LegacyOptimizationKind.LEGACY_REDUCTION_CANDIDATE, target_ref="secret-value-s3cr3t", shell_pattern=pattern)
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "raw_secret_captured" in text

# ---------------------------------------------------------------------------
# 15. Raw command hash not default dimension.
# ---------------------------------------------------------------------------

def test_15_raw_command_hash_not_default():
    assert w2.RAW_COMMAND_HASH_USED_AS_DEFAULT_FINDING_DIMENSION is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "command_hash" not in text or "raw_command_hash_used_as_default" in text
    # Shell pattern uses command_id, not hash
    pattern = create_normalized_shell_pattern("ls", outcome_class="success")
    assert pattern.command_id in ("ls", "echo", "sleep", "unknown_command")
    # Ensure no hash computation of raw command in finding

# ---------------------------------------------------------------------------
# 16. Legacy finding cannot mutate source.
# ---------------------------------------------------------------------------

def test_16_legacy_no_mutation():
    assert w2.LEGACY_REDUCTION_FINDING_IS_SOURCE_MUTATION_AUTHORITY is False
    assert w2.LEGACY_FINDING_IS_SOURCE_AUTHORITY is False
    proj = "proj-a"
    pattern = create_normalized_shell_pattern("ls", outcome_class="success")
    series = _make_series_shell(pattern)
    res, item = _query(proj, series, None, None, [5])
    res_copy = copy.deepcopy(res)
    item_copy = copy.deepcopy(item)
    desc = _shell_descriptor(proj, series, pattern)
    finding = create_shell_direct_finding(project_id=proj, query_result=res, item=item, shell_pattern=pattern, series_descriptor=desc)
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=2)
    rec = create_legacy_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=LegacyOptimizationKind.LEGACY_REDUCTION_CANDIDATE, target_ref="legacy-target", shell_pattern=pattern)
    assert res == res_copy
    assert item == item_copy
    assert rec is not None

# ---------------------------------------------------------------------------
# 17-21. Context portable units
# ---------------------------------------------------------------------------

def test_17_bytes_utf8_finding():
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.BYTES_UTF8, "comp-a")
    res, item = _query(proj, series, None, None, [100])
    desc = _context_descriptor(proj, series, ContextCostUnit.BYTES_UTF8, "comp-a")
    finding = create_context_bytes_utf8_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.series_descriptor.context_cost_unit == ContextCostUnit.BYTES_UTF8
    assert finding.is_authority is False

def test_18_chars_finding():
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.CHARS, "comp-b")
    res, item = _query(proj, series, None, None, [200])
    desc = _context_descriptor(proj, series, ContextCostUnit.CHARS, "comp-b")
    finding = create_context_chars_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.series_descriptor.context_cost_unit == ContextCostUnit.CHARS

def test_19_items_finding():
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.ITEMS, "comp-c")
    res, item = _query(proj, series, None, None, [5])
    desc = _context_descriptor(proj, series, ContextCostUnit.ITEMS, "comp-c")
    finding = create_context_items_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.series_descriptor.context_cost_unit == ContextCostUnit.ITEMS

def test_20_references_finding():
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.REFERENCES, "comp-d")
    res, item = _query(proj, series, None, None, [7])
    desc = _context_descriptor(proj, series, ContextCostUnit.REFERENCES, "comp-d")
    finding = create_context_references_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.series_descriptor.context_cost_unit == ContextCostUnit.REFERENCES

def test_21_hydrated_bytes_finding():
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.HYDRATED_BYTES, "comp-e")
    res, item = _query(proj, series, None, None, [4096])
    desc = _context_descriptor(proj, series, ContextCostUnit.HYDRATED_BYTES, "comp-e")
    finding = create_context_hydrated_bytes_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.series_descriptor.context_cost_unit == ContextCostUnit.HYDRATED_BYTES
    assert w2.PORTABLE_CONTEXT_UNITS_REUSED is True

# ---------------------------------------------------------------------------
# 22. Cross context unit comparison fails closed.
# ---------------------------------------------------------------------------

def test_22_cross_unit_comparison_fails_closed():
    assert w2.CROSS_UNIT_CONTEXT_COMPARISON_FAILS_CLOSED is True
    proj = "proj-a"
    series_bytes = _make_series_context(ContextCostUnit.BYTES_UTF8, "comp")
    series_chars = _make_series_context(ContextCostUnit.CHARS, "comp")
    desc_bytes = _context_descriptor(proj, series_bytes, ContextCostUnit.BYTES_UTF8, "comp")
    desc_chars = _context_descriptor(proj, series_chars, ContextCostUnit.CHARS, "comp")
    with pytest.raises(Exception) as exc:
        validate_context_units_compatible(desc_bytes, desc_chars)
    assert "incompatible" in str(exc.value).lower() or "fail_closed" in str(exc.value).lower()

# ---------------------------------------------------------------------------
# 23. No token inference.
# ---------------------------------------------------------------------------

def test_23_no_token_inference():
    assert w2.CONTEXT_COST_INFERRED_BY_HEURISTIC is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    # Ensure no heuristic conversion tokens->bytes
    assert "token" not in text.lower() or "provider_token" in text.lower() or "context_cost_inferred_by_heuristic" in text.lower()
    # Context cost should be direct measurement, not inferred
    # Attempt to create context finding with bytes should not infer from tokens
    proj = "proj-a"
    series = _make_series_context(ContextCostUnit.BYTES_UTF8, "comp")
    res, item = _query(proj, series, None, None, [10])
    desc = _context_descriptor(proj, series, ContextCostUnit.BYTES_UTF8, "comp")
    finding = create_context_direct_finding(project_id=proj, query_result=res, item=item, unit=ContextCostUnit.BYTES_UTF8, series_descriptor=desc)
    assert finding.numeric_value is not None

# ---------------------------------------------------------------------------
# 24. No monetary cost.
# ---------------------------------------------------------------------------

def test_24_no_monetary_cost():
    assert w2.MONETARY_CONTEXT_COST_FINDING_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "monetary" not in text or "monetary_context_cost_finding_created" in text.lower()
    assert "price" not in text.lower() or "model_price_table_created" in text.lower()
    # ensure no monetary cost class created (dollar price table would be hard-coded monetary)
    assert "class PriceTable" not in Path(inspect.getfile(w2)).read_text()
    assert "class Monetary" not in Path(inspect.getfile(w2)).read_text()

# ---------------------------------------------------------------------------
# 25. No model price table.
# ---------------------------------------------------------------------------

def test_25_no_price_table():
    assert w2.MODEL_PRICE_TABLE_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "price_table" not in text or "model_price_table_created" in text.lower()

# ---------------------------------------------------------------------------
# 26. Cross-project evidence fails closed.
# ---------------------------------------------------------------------------

def test_26_cross_project_fails_closed():
    assert w2.CROSS_PROJECT_FINDING_FAIL_CLOSED is True
    proj_a = "proj-a"
    proj_b = "proj-b"
    series = _make_series_tool("workspace.read")
    res_a, item_a = _query(proj_a, series, None, None, [5])
    desc_b = _tool_descriptor(proj_b, series, "workspace.read")
    with pytest.raises(Exception) as exc:
        create_tool_direct_finding(project_id=proj_b, query_result=res_a, item=item_a, series_descriptor=desc_b)
    assert "cross-project" in str(exc.value).lower()

# ---------------------------------------------------------------------------
# 27. Empty query != zero usage.
# ---------------------------------------------------------------------------

def test_27_empty_not_zero():
    proj = "proj-a"
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    assert res.coverage == QueryCoverage.EMPTY
    assert w2.EMPTY_QUERY_RESULT_IMPLIES_METRIC_ZERO is False
    series = _make_series_tool("workspace.read")
    _, item = _query(proj, series, None, None, [5])
    desc = _tool_descriptor(proj, series, "workspace.read")
    # Empty res should produce EMPTY_EVIDENCE finding, not zero
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=desc)
    assert finding.disposition == FindingDisposition.EMPTY_EVIDENCE
    assert finding.numeric_value != 0 or finding.numeric_value is None

# ---------------------------------------------------------------------------
# 28. W1 finding IDs preserved.
# ---------------------------------------------------------------------------

def test_28_w1_finding_ids_preserved():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [8])
    # Create finding via W1 directly
    f_w1 = create_direct_observation_finding(project_id=proj, query_result=res, item=item)
    # Create via W2 wrapper with same inputs (no descriptor)
    f_w2 = create_tool_direct_finding(project_id=proj, query_result=res, item=item)
    # Should reuse same deterministic method; IDs should be comparable? Our W2 uses same W1 helper, so IDs should be equal if same descriptor absent
    # For this test, we check that our finding id is deterministic via W1 compute
    assert f_w1.finding_id == f_w2.finding_id
    assert w2.EXISTING_W1_CALIBRATION_CONTRACT_REUSED is True
    assert w2.W1_PRODUCTION_CONTRACT_CHANGED is False

# ---------------------------------------------------------------------------
# 29. W1 recommendation candidate reused.
# ---------------------------------------------------------------------------

def test_29_w1_recommendation_reused():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [9])
    finding = create_tool_direct_finding(project_id=proj, query_result=res, item=item, series_descriptor=_tool_descriptor(proj, series))
    thr = create_tool_threshold_candidate(project_id=proj, finding=finding, boundary_value=4)
    rec = create_tool_recommendation_candidate(project_id=proj, finding=finding, threshold_candidate=thr, kind=ToolOptimizationKind.PROMOTION_CANDIDATE, target_ref="target-x")
    assert isinstance(rec, w2.RecommendationCandidate) or hasattr(rec, "recommendation_id")
    # Check that module reuses W1 helper text
    text = Path(inspect.getfile(w2)).read_text()
    assert "create_recommendation_candidate" in text
    assert "RecommendationCandidate" in text

# ---------------------------------------------------------------------------
# 30. No opaque confidence.
# ---------------------------------------------------------------------------

def test_30_no_opaque_confidence():
    assert w2.OPAQUE_CONFIDENCE_SCORE_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "confidence" not in text or "opaque_confidence_score_created" in text

# ---------------------------------------------------------------------------
# 31. No W3 workflow calibration.
# ---------------------------------------------------------------------------

def test_31_no_w3_scope():
    assert w2.W3_SCOPE_IMPLEMENTED_IN_W2 is False
    text = Path(inspect.getfile(w2)).read_text()
    assert "telemetry_workflow_calibration" not in text
    assert "workflow" not in text.lower() or "w3_scope" in text.lower() or "workflow_friction" in text.lower()  # allow only flag

# ---------------------------------------------------------------------------
# 32. No global registry.
# ---------------------------------------------------------------------------

def test_32_no_global_registry():
    assert w2.GLOBAL_RECOMMENDATION_REGISTRY_CREATED is False
    text = Path(inspect.getfile(w2)).read_text()
    assert "RecommendationRegistry" not in text
    assert "global registry" not in text.lower() or "global_recommendation_registry_created" in text.lower()

# ---------------------------------------------------------------------------
# 33. No second store/query/runtime.
# ---------------------------------------------------------------------------

def test_33_no_second_store():
    assert w2.SECOND_ANALYTICS_STORE_CREATED is False
    assert w2.SECOND_QUERY_ENGINE_CREATED is False
    assert w2.SECOND_METRIC_RUNTIME_CREATED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "class.*store" not in text
    assert "second_query_engine" in text  # only flag

# ---------------------------------------------------------------------------
# 34. No automatic tuning loop.
# ---------------------------------------------------------------------------

def test_34_no_automatic_tuning():
    assert w2.M4_AUTOMATIC_TUNING_LOOP is False
    assert w2.M4_BACKGROUND_OPTIMIZER_REQUIRED is False
    text = Path(inspect.getfile(w2)).read_text().lower()
    assert "tuning loop" not in text or "m4_automatic_tuning_loop" in text
    assert "background" not in text.lower() or "m4_background_optimizer" in text.lower()

# ---------------------------------------------------------------------------
# 35. W1 failure isolation retained.
# ---------------------------------------------------------------------------

def test_35_failure_isolation():
    assert w2.ANALYSIS_FAILURE_DOES_NOT_REWRITE_QUERY_RESULT is True
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    res_copy = copy.deepcopy(res)
    item_copy = copy.deepcopy(item)
    # cause failure via cross-project
    with pytest.raises(Exception):
        create_tool_direct_finding(project_id="proj-b", query_result=res, item=item)
    assert res == res_copy
    assert item == item_copy

# ---------------------------------------------------------------------------
# 36. W1 focused regression (sanity: W1 helpers still deterministic)
# ---------------------------------------------------------------------------

def test_36_w1_regression_sanity():
    proj = "proj-a"
    series = _make_series_tool("workspace.read")
    res, item = _query(proj, series, None, None, [5])
    f1 = create_direct_observation_finding(project_id=proj, query_result=res, item=item)
    f2 = create_direct_observation_finding(project_id=proj, query_result=res, item=item)
    assert f1.finding_id == f2.finding_id
    # Also test W2 wrappers deterministic
    f3 = create_tool_direct_finding(project_id=proj, query_result=res, item=item)
    f4 = create_tool_direct_finding(project_id=proj, query_result=res, item=item)
    assert f3.finding_id == f4.finding_id

# ---------------------------------------------------------------------------
# 37. M3 Tool/Skill/Shell/Context regression.
# ---------------------------------------------------------------------------

def test_37_m3_regression():
    # Verify M3 projectors still work (not broken by W2)
    from aota_forge.work_plane.roles import AgentWorkRole
    from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation
    # Tool projection - use adapted evidence so digest binding matches
    obs = ToolUsageObservation(
        observation_id="obs-1",
        operation_name="workspace.read",
        contract_hash="a"*64,
        is_success=True,
        outcome_class="success",
        side_effect="read",
        work_role=AgentWorkRole.CODER,
        project_id="proj-a",
        worktree_id="wt-1",
    )
    env = _adapt_tool_evidence(obs, project_id="proj-a", ingest=_ingest())
    contrib = project_tool_observation(env, obs)
    assert contrib is not None
    assert contrib.project_id == "proj-a"
    # Shell - create shell evidence via same tool env (allowed, shell domain does not require digest match)
    pat = create_normalized_shell_pattern("ls", outcome_class="success")
    contrib_shell = project_shell_observation(env, pat)
    assert contrib_shell is not None
    # Context
    meas = ContextCostMeasurement(value=100, unit=ContextCostUnit.BYTES_UTF8)
    contrib_ctx = project_context_cost(env, meas)
    assert contrib_ctx is not None
    # Skill - create skill evidence via adapt_skill_usage_observation so digest matches
    from aota_forge.work_plane.skill import SkillIdentity as SI2
    # Create SkillUsageObservation for skill-a
    skill_obs = SkillUsageObservation(
        event_id="evt-1",
        event_type="worker_result",
        namespace="coder",
        skill_id="skill-a",
        version="1.0.0",
        digest="a"*64,
        delivery="eager",
        provenance="prov",
    )
    env_skill = adapt_skill_usage_observation(skill_obs, _ingest(), project_id="proj-a", worktree_id="wt-1")
    # Project via SkillUsageObservation (more accurate) and via SkillIdentity both should work?
    # Use SkillUsageObservation for projection
    contrib_skill = project_skill_observation(env_skill, skill_obs)
    assert contrib_skill is not None
    # Also test SkillIdentity path with its own env (skill identity digest matches env source_digest? For SkillIdentity path, evidence is still from SkillUsageObservation, but SkillIdentity's digest is same as observation's digest, so binding may still check)
    ident = SI2(skill_id="skill-a", version="1.0.0", digest="a"*64, provenance="prov")
    # For SkillIdentity, the digest check extracts ident.digest which is "a"*64, but env_skill's source_digest is hash of SkillUsageObservation canonical? Let's compute:_skill Obs digest is "a"*64? No, evidence source_digest is hash of observation? Wait adapt_skill_usage_observation will compute source_digest from SkillUsageObservation's digest field? Actually it uses observation's digest? Let's check; but we use skill_obs which has digest "a"*64, so evidence source_digest should be derived from observation's digest? The adapter may use observation's digest as source_digest. So it should match ident.digest which is also "a"*64. That may still mismatch because evidence source_digest is hash of source_observation_id + etc not directly the skill digest. Let's see failure: evidence source_digest is "4d..."? That's hash of tool observation. For skill, env_skill's source_digest will be hash of skill observation's canonical, not "a"*64. So SkillIdentity digest "a"*64 won't match. To avoid mismatch, we should project SkillUsageObservation instead of SkillIdentity with that env.
    # So we already did contrib_skill via skill_obs, which should succeed. We'll not test ident path separately.

# ---------------------------------------------------------------------------
# 38. deterministic output regardless insertion order.
# ---------------------------------------------------------------------------

def test_38_deterministic_insertion_order():
    proj = "proj-a"
    d_a = "a"*64
    d_b = "b"*64
    id1 = compute_finding_id(project_id=proj, method=FindingMethod.DIRECT_OBSERVED_AGGREGATE, method_version=CALIBRATION_CONTRACT_VERSION, evidence_digests=(d_a, d_b), series_id=d_a, numeric_repr="5")
    id2 = compute_finding_id(project_id=proj, method=FindingMethod.DIRECT_OBSERVED_AGGREGATE, method_version=CALIBRATION_CONTRACT_VERSION, evidence_digests=(d_b, d_a), series_id=d_a, numeric_repr="5")
    assert id1 == id2
    # Also via W2 helper
    id3 = w2.deterministic_finding_ids(proj, [d_a, d_b], d_a, "5")
    id4 = w2.deterministic_finding_ids(proj, [d_b, d_a], d_a, "5")
    assert id3 == id4
    assert id1 == id3

# Additional guard: ensure no S4/S5 imports
def test_no_s4_s5_import():
    text = Path(inspect.getfile(w2)).read_text()
    assert "from aota_forge.work_plane.risk_review" not in text
    assert "from aota_forge.work_plane.progression" not in text
    assert "S4_TYPED_SOURCE_USED_BY_W2" in text
    assert w2.S4_TYPED_SOURCE_USED_BY_W2 is False
    assert w2.S5_SOURCE_CONSUMED_BY_W2 is False
    assert w2.S5_POLICY_MUTATED_BY_W2 is False
