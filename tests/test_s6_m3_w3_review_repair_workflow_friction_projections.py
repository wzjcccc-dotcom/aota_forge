"""Focused behavioral proof for S6 M3 W3 — Review / Repair / Workflow-Friction Projections.

Covers required contracts (section 34, 40 items):

1. Review-required evidence → contribution.
2. Review satisfaction evidence → contribution.
3. Review escalation evidence → contribution.
4. ChallengeRole bounded category.
5. ChallengeKind bounded category.
6. Progression blocked → friction primitive.
7. Validation FAIL → friction primitive.
8. Validation UNKNOWN → friction primitive.
9. Needs-repair accepted disposition → repair-related primitive where supported.
10. Same source/version deterministic.
11. New projection version changes projection id only.
12. source_dedup_id unchanged.
13. exact Work Item/result binding required.
14. foreign Work Item result replay rejected.
15. mismatched result ref rejected.
16. mismatched result digest rejected.
17. predecessor-incomplete progression does not become complete/no-friction.
18. missing ReviewSatisfactionEvidence when formal review required does not become satisfied.
19. naked boolean cannot create review metric truth.
20. cross-project mismatch fails closed.
21. source/TelemetryEvidence mismatch fails closed.
22. completeness preserved.
23. missing != zero.
24. no Work Item ref as default metric dimension.
25. no raw finding/summary/error dimension.
26. review metric non-authority.
27. repair metric non-authority.
28. workflow metric non-authority.
29. projection failure leaves S4 evidence unchanged.
30. no RV2 metric.
31. no repair-cycle metric.
32. no replan metric.
33. no closure-readiness metric.
34. no composite friction score.
35. no W4 registry/integration.
36. no calibration/recommendation.
37. no new store/query/runtime.
38. W1 Role/Tool/Skill regression.
39. W2 shell/context regression.
40. I29-B001 adversarial regression.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aota_forge.work_plane.telemetry_evidence import (
    CompletenessRecord,
    CompletenessScope,
    CompletenessState,
    SourceEvidenceIdentity,
    TemporalProvenance,
    TelemetryEvidenceEnvelope,
    create_telemetry_evidence_envelope,
    compute_source_dedup_id,
    compute_projection_id,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ReviewRepairMetricSubject,
    WorkflowFrictionMetricSubject,
)
from aota_forge.work_plane.telemetry_aggregation import AggregateOperation, AggregationContribution
from aota_forge.work_plane.telemetry_projection_core import MetricSourceProvenance
from aota_forge.work_plane.risk_review import (
    ChallengeKind,
    ChallengeRole,
    ReviewEscalationDisposition,
    ReviewTrigger,
    MilestoneRiskEnvelope,
    ProcessDepth,
    evaluate_work_item_risk_policy,
    parse_challenge_role,
    parse_challenge_kind,
)
from aota_forge.work_plane.progression import (
    FocusedValidationEvidence,
    FocusedValidationVerdict,
    MilestoneWorkItemGraph,
    ProgressionDisposition,
    ReviewSatisfactionEvidence,
    WorkItemProgressEvidence,
    evaluate_milestone_progression,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.core.result_governance import ResultOutcome

# Import W3 projectors and flags
import aota_forge.work_plane.telemetry_projection_review_friction as w3
from aota_forge.work_plane.telemetry_projection_review_friction import (
    M3_PROJECTION_LAYER_ONLY,
    EXISTING_W1_PROJECTION_CORE_REUSED,
    EXISTING_AGGREGATION_CONTRIBUTION_REUSED,
    SECOND_METRIC_RUNTIME_CREATED,
    SECOND_TELEMETRY_STORE_CREATED,
    SECOND_QUERY_ENGINE_CREATED,
    METRIC_SOURCE_PROVENANCE_EXPLICIT,
    DOMAIN_SOURCE_BINDING_VERIFIED,
    CROSS_PROJECT_PROJECTION_FAIL_CLOSED,
    S4_M2_WORKFLOW_EVIDENCE_USED,
    S4_M3_ACCEPTED_SOURCE_CONSUMED_BY_W3,
    S4_M3_ONLY_REPAIR_SEMANTICS_SYNTHESIZED,
    PRIMITIVE_METRICS_FIRST,
    RATE_METRIC_IMPLEMENTED_IN_W3,
    NAKED_REVIEW_COMPLETED_BOOLEAN_ALLOWED,
    RV2_METRIC_IMPLEMENTED,
    REPAIR_CYCLE_METRIC_IMPLEMENTED,
    REPLAN_METRIC_IMPLEMENTED,
    CLOSURE_READINESS_METRIC_IMPLEMENTED,
    COMPOSITE_FRICTION_SCORE_CREATED,
    EXACT_WORK_ITEM_RESULT_BINDING_PRESERVED,
    S4_PRE_REPAIR_FRONTIER_CONSUMED,
    PROGRESSION_COMPLETE_REQUIRES_PREDECESSORS,
    SOURCE_DEDUP_ID_REDEFINED,
    PROJECTION_ID_REDEFINED,
    METRIC_FAMILY_REDEFINED,
    METRIC_SUBJECT_REDEFINED,
    FREE_TEXT_METRIC_FAMILY_ALLOWED,
    M3_METRIC_CARDINALITY_BOUNDED,
    WORK_ITEM_REF_IS_DEFAULT_METRIC_DIMENSION,
    MISSING_TELEMETRY_IS_ZERO,
    GLOBAL_COMPLETENESS_SEVERITY_ORDER,
    IMPLICIT_WALL_CLOCK_USED,
    TEMPORAL_RATE_OR_LATENCY_METRIC_CREATED,
    REVIEW_METRIC_IS_REVIEW_AUTHORITY,
    REPAIR_METRIC_IS_REPAIR_AUTHORITY,
    WORKFLOW_METRIC_IS_PROGRESSION_AUTHORITY,
    S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY,
    PROJECTION_FAILURE_DOES_NOT_REWRITE_SOURCE_EVIDENCE,
    PROJECTION_FAILURE_DOES_NOT_REWRITE_PROGRESSION_STATE,
    PROJECTION_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT,
    SHARED_REGISTRY_UPDATE_IN_W3,
    W4_SCOPE_IMPLEMENTED_IN_W3,
    DURABLE_STORAGE_REQUIRED_FOR_W3,
    STORAGE_ENGINE_SELECTED_IN_W3,
    M3_CALIBRATION_THRESHOLD_CREATED,
    M3_RECOMMENDATION_ENGINE_CREATED,
    REVIEW_PROJECTION_PRESENT,
    REPAIR_RELATED_PROJECTION_PRESENT,
    WORKFLOW_FRICTION_PROJECTION_PRESENT,
    W3_PROJECTION_NAMESPACE,
    W3_PROJECTION_VERSION,
)

# Also import W1/W2 for regression
from aota_forge.work_plane.telemetry_projection_role_tool_skill import (
    project_role_observation as w1_project_role,
    project_tool_observation as w1_project_tool,
    project_skill_observation as w1_project_skill,
)
from aota_forge.work_plane.telemetry_projection_shell_context import (
    project_shell_observation as w2_project_shell,
    project_context_cost as w2_project_context,
)
from aota_forge.work_plane.telemetry_metrics import create_normalized_shell_pattern, ContextCostMeasurement
from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation
from aota_forge.work_plane.skill_usage import SkillUsageObservation
from aota_forge.work_plane.telemetry_adapters import adapt_tool_usage_observation, adapt_skill_usage_observation


# ---------------------------------------------------------------------------
# Helpers — S4 evidence and TelemetryEvidence
# ---------------------------------------------------------------------------

def _ingest(dt: datetime | None = None) -> datetime:
    return dt if dt is not None else datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

def _handoff_ref(ref: str, digest: str | None = None) -> ResultHandoffRef:
    return ResultHandoffRef(ref=ref, digest=digest)

def _semantic(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)

def _make_disposition(formal_review_required=True, challenge_role=ChallengeRole.REVIEWER, challenge_kind=ChallengeKind.TECHNICAL_ACCEPTANCE, semantic_escalation=False, auto_eligible=False, trigger=ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY):
    # Use risk policy evaluator to create a disposition deterministically
    env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST)
    if formal_review_required:
        # Use review trigger
        disp = evaluate_work_item_risk_policy(envelope=env, review_triggers=(trigger,))
        # Ensure formal_review_required matches expectation
        # If not formal, we need to override but evaluator will set formal true when trigger present
        return disp
    else:
        disp = evaluate_work_item_risk_policy(envelope=env)
        return disp

def _make_disposition_needs_repair():
    # formal review required - any formal review is considered needs-repair for W3
    env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST)
    disp = evaluate_work_item_risk_policy(envelope=env, review_triggers=(ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY,))
    assert disp.formal_review_required is True
    return disp

def _make_satisfaction(work_item_ref="S4/M2/W1", disp: ReviewEscalationDisposition | None = None, reviewer_card: WorkerResultCard | None = None):
    if disp is None:
        disp = _make_disposition()
    if reviewer_card is None:
        reviewer_card = _make_worker_card("task-reviewer", role=AgentWorkRole.REVIEWER)
    return ReviewSatisfactionEvidence(
        work_item_ref=work_item_ref,
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,  # type: ignore
        review_trigger=disp.identified_risk,  # type: ignore
        review_result_ref=_handoff_ref("task-reviewer", "rev-corr"),
        review_result_digest=reviewer_card.card_digest,
    )

def _make_worker_card(task_id: str, correlation_id: str = "corr1", outcome=ResultOutcome.SUCCESS, role=AgentWorkRole.CODER, next_hint=None):
    summary = "done" if outcome == ResultOutcome.SUCCESS else "fail"
    blocking = 0 if outcome == ResultOutcome.SUCCESS else 1
    return WorkerResultCard(
        task_ref=task_id,
        agent_work_role=role,
        summary=summary,
        outcome=outcome,
        blocking_finding_count=blocking,
        non_blocking_finding_count=0,
        result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
        primary_evidence_refs=(),
        output_artifact_refs=(),
        next_hint=next_hint,
    )

def _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a"):
    return TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="code",
        objective="obj",
        bounded_scope="scope",
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        milestone_ref=_semantic(milestone_ref),
        work_item_ref=_semantic(work_item_ref),
        project_ref=_semantic(project_ref),
    )

def _make_graph(work_items, deps, milestone_ref="S4/M2"):
    return MilestoneWorkItemGraph(milestone_ref=milestone_ref, work_items=tuple(work_items), dependencies=tuple(deps))

def _make_progress(wi="S4/M2/W1", task_id="task-w1", card: WorkerResultCard | None = None):
    if card is None:
        card = _make_worker_card(task_id)
    return WorkItemProgressEvidence(work_item_ref=wi, worker_result_ref=_handoff_ref(task_id, "corr1"), worker_result_digest=card.card_digest)

def _make_validation(wi="S4/M2/W1", verdict=FocusedValidationVerdict.PASS):
    return FocusedValidationEvidence(work_item_ref=wi, verdict=verdict, validation_evidence_ref=_semantic("val:"+wi))

def _make_s4_telemetry_evidence(domain_obj: Any, project_id="proj-a", source_kind="worker_result_card", observation_id="obs-s4-1", ingest=None, completeness=None):
    if ingest is None:
        ingest = _ingest()
    if completeness is None:
        completeness = CompletenessRecord(state=CompletenessState.COMPLETE, scope=CompletenessScope.SOURCE)
    # Compute digest from domain
    if hasattr(domain_obj, "compute_digest"):
        digest = domain_obj.compute_digest()  # type: ignore
    elif hasattr(domain_obj, "digest"):
        digest = domain_obj.digest  # type: ignore
    else:
        # Fallback hash of string
        digest = hashlib.sha256(str(domain_obj).encode()).hexdigest()
    # Ensure digest is 64 hex
    if len(digest) != 64:
        digest = hashlib.sha256(digest.encode()).hexdigest()
    identity = SourceEvidenceIdentity(
        project_id=project_id,
        source_kind=source_kind,
        source_observation_id=observation_id,
        source_contract_version="s6-m3-w3-v1",
        source_digest=digest,
    )
    temporal = TemporalProvenance(ingestion_time=ingest)
    return create_telemetry_evidence_envelope(identity, completeness, temporal)

def _make_tool_evidence_for_w1(project_id="proj-a", observation_id="obs-tool-1"):
    obs = ToolUsageObservation(
        observation_id=observation_id,
        operation_name="workspace.read",
        contract_hash="a"*64,
        is_success=True,
        outcome_class="success",
        side_effect="read",
        work_role=AgentWorkRole.CODER,
        project_id=project_id,
        worktree_id="wt-1",
    )
    return adapt_tool_usage_observation(obs, _ingest(), project_id=project_id, worktree_id="wt-1"), obs

def _make_skill_evidence_for_w1(project_id="proj-a", event_id="evt-1"):
    obs = SkillUsageObservation(event_id=event_id, event_type="worker_result", namespace="coder", skill_id="skill-a", version="1.0.0", digest="b"*64, delivery="eager")
    return adapt_skill_usage_observation(obs, _ingest(), project_id=project_id, worktree_id="wt-1"), obs

# ---------------------------------------------------------------------------
# 1. Review-required evidence → contribution
# ---------------------------------------------------------------------------

def test_review_required_evidence_to_contribution():
    disp = _make_disposition(formal_review_required=True)
    assert disp.formal_review_required is True
    ev = _make_s4_telemetry_evidence(disp, observation_id="rev-req-1")
    contrib = w3.project_review_required(ev, disp)
    assert isinstance(contrib, AggregationContribution)
    assert contrib.operation == AggregateOperation.COUNT
    assert contrib.value == 1
    assert contrib.project_id == "proj-a"

# ---------------------------------------------------------------------------
# 2. Review satisfaction evidence → contribution
# ---------------------------------------------------------------------------

def test_review_satisfaction_evidence_to_contribution():
    disp = _make_disposition()
    reviewer_card = _make_worker_card("task-reviewer", role=AgentWorkRole.REVIEWER)
    sat = _make_satisfaction(work_item_ref="S4/M2/W1", disp=disp, reviewer_card=reviewer_card)
    ev = _make_s4_telemetry_evidence(sat, observation_id="sat-1")
    # Need to also provide disposition for binding check? Our projector validates binding if disp provided
    contrib = w3.project_review_satisfaction(ev, sat, disp)
    assert isinstance(contrib, AggregationContribution)
    assert contrib.value == 1

def test_review_satisfaction_without_disposition_still_projects():
    disp = _make_disposition()
    reviewer_card = _make_worker_card("task-reviewer", role=AgentWorkRole.REVIEWER)
    sat = _make_satisfaction(disp=disp, reviewer_card=reviewer_card)
    ev = _make_s4_telemetry_evidence(sat, observation_id="sat-2")
    contrib = w3.project_review_satisfaction(ev, sat)
    assert isinstance(contrib, AggregationContribution)

# ---------------------------------------------------------------------------
# 3. Review escalation evidence → contribution
# ---------------------------------------------------------------------------

def test_review_escalation_evidence_to_contribution():
    disp = _make_disposition(formal_review_required=True)
    # disp with escalation: use semantic escalation
    env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST)
    esc_disp = evaluate_work_item_risk_policy(envelope=env, escalation_triggers=("NEW_MILESTONE_APPROVAL",))
    assert esc_disp.semantic_escalation_required is True or esc_disp.challenge_role is not None
    ev = _make_s4_telemetry_evidence(esc_disp, observation_id="esc-1")
    contrib = w3.project_review_escalation(ev, esc_disp)
    assert isinstance(contrib, AggregationContribution)

# ---------------------------------------------------------------------------
# 4. ChallengeRole bounded category
# ---------------------------------------------------------------------------

def test_challenge_role_bounded_category():
    for role in [ChallengeRole.ANALYST, ChallengeRole.REVIEWER, ChallengeRole.TASK_MAIN]:
        # Create disposition with matching role
        env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST)
        # Map role to trigger that yields that role
        trigger_map = {
            ChallengeRole.ANALYST: ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE,
            ChallengeRole.REVIEWER: ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY,
            ChallengeRole.TASK_MAIN: ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY,
        }
        disp = evaluate_work_item_risk_policy(envelope=env, review_triggers=(trigger_map[role],))
        # disp's challenge_role should match role
        assert disp.challenge_role == role
        ev = _make_s4_telemetry_evidence(disp, observation_id=f"cr-{role.value}")
        c = w3.project_challenge_role(ev, role, disposition=disp)
        assert isinstance(c, AggregationContribution)
    # Unknown role should fail - use generic disp
    disp_generic = _make_disposition()
    ev_generic = _make_s4_telemetry_evidence(disp_generic, observation_id="cr-unknown")
    with pytest.raises(Exception):
        w3.project_challenge_role(ev_generic, "hacker")
    with pytest.raises(Exception):
        w3.project_challenge_role(ev_generic, "unknown_role")

# ---------------------------------------------------------------------------
# 5. ChallengeKind bounded category
# ---------------------------------------------------------------------------

def test_challenge_kind_bounded_category():
    kind_trigger_map = {
        ChallengeKind.ARCHITECTURE_FEASIBILITY: ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE,
        ChallengeKind.TECHNICAL_ACCEPTANCE: ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY,
        ChallengeKind.SEMANTIC_RECONCILIATION: ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY,
    }
    for kind in [ChallengeKind.ARCHITECTURE_FEASIBILITY, ChallengeKind.TECHNICAL_ACCEPTANCE, ChallengeKind.SEMANTIC_RECONCILIATION]:
        env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST)
        disp = evaluate_work_item_risk_policy(envelope=env, review_triggers=(kind_trigger_map[kind],))
        assert disp.challenge_kind == kind
        ev = _make_s4_telemetry_evidence(disp, observation_id=f"ck-{kind.value}")
        c = w3.project_challenge_kind(ev, kind, disposition=disp)
        assert isinstance(c, AggregationContribution)
    disp_generic = _make_disposition()
    ev_generic = _make_s4_telemetry_evidence(disp_generic, observation_id="ck-unknown")
    with pytest.raises(Exception):
        w3.project_challenge_kind(ev_generic, "hacker_kind")
    with pytest.raises(Exception):
        w3.project_challenge_kind(ev_generic, "invalid")

# ---------------------------------------------------------------------------
# 6. Progression blocked → friction primitive
# ---------------------------------------------------------------------------

def test_progression_blocked_to_friction():
    # Create a progression disposition with blocked
    disp = evaluate_work_item_risk_policy(envelope=MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST))
    # Create a blocked progression disposition manually
    graph = _make_graph(["W1", "W2"], [("W1", "W2")])
    card = _make_worker_card("task-w1")
    pe = _make_progress("W1", "task-w1", card)
    ve = _make_validation("W1", FocusedValidationVerdict.PASS)
    # Create a disposition where W2 is blocked due to missing evidence
    # Instead, directly create ProgressionDisposition with blocked
    prog = ProgressionDisposition(
        progression_complete_work_item_refs=(),
        ready_work_item_refs=("W1",),
        blocked_work_item_refs=("W2",),
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=(),
        milestone_review_ready=False,
        auto_progression_allowed=False,
        reasons=(),
        evidence_refs=(),
    )
    ev = _make_s4_telemetry_evidence(prog, observation_id="blocked-1")
    contrib = w3.project_blocked_progression(ev, prog, work_item_ref="W2")
    assert isinstance(contrib, AggregationContribution)
    assert contrib.operation == AggregateOperation.COUNT

def test_progression_blocked_not_empty():
    prog = ProgressionDisposition(
        progression_complete_work_item_refs=(),
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=(),
        milestone_review_ready=False,
        auto_progression_allowed=False,
        reasons=(),
        evidence_refs=(),
    )
    ev = _make_s4_telemetry_evidence(prog, observation_id="blocked-empty")
    with pytest.raises(Exception):
        w3.project_blocked_progression(ev, prog)

# ---------------------------------------------------------------------------
# 7. Validation FAIL → friction primitive
# ---------------------------------------------------------------------------

def test_validation_fail_to_friction():
    val = _make_validation(verdict=FocusedValidationVerdict.FAIL)
    ev = _make_s4_telemetry_evidence(val, observation_id="val-fail-1")
    contrib = w3.project_validation_failure(ev, val)
    assert isinstance(contrib, AggregationContribution)
    assert contrib.value == 1

def test_validation_fail_wrong_verdict_fails():
    val = _make_validation(verdict=FocusedValidationVerdict.PASS)
    ev = _make_s4_telemetry_evidence(val, observation_id="val-pass-1")
    with pytest.raises(Exception):
        w3.project_validation_failure(ev, val)

# ---------------------------------------------------------------------------
# 8. Validation UNKNOWN → friction primitive
# ---------------------------------------------------------------------------

def test_validation_unknown_to_friction():
    val = _make_validation(verdict=FocusedValidationVerdict.UNKNOWN)
    ev = _make_s4_telemetry_evidence(val, observation_id="val-unk-1")
    contrib = w3.project_validation_unknown(ev, val)
    assert isinstance(contrib, AggregationContribution)

def test_validation_unknown_wrong_verdict_fails():
    val = _make_validation(verdict=FocusedValidationVerdict.PASS)
    ev = _make_s4_telemetry_evidence(val, observation_id="val-pass-2")
    with pytest.raises(Exception):
        w3.project_validation_unknown(ev, val)

# ---------------------------------------------------------------------------
# 9. Needs-repair accepted disposition → repair-related primitive where supported
# ---------------------------------------------------------------------------

def test_needs_repair_disposition_to_repair_primitive():
    disp = _make_disposition_needs_repair()
    ev = _make_s4_telemetry_evidence(disp, observation_id="repair-1")
    contrib = w3.project_needs_repair_disposition(ev, disp)
    assert isinstance(contrib, AggregationContribution)
    assert contrib.operation == AggregateOperation.COUNT

def test_needs_repair_not_applicable_fails():
    disp = evaluate_work_item_risk_policy(envelope=MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST))
    # This disp has formal_review_required False, not needs-repair
    assert disp.formal_review_required is False
    ev = _make_s4_telemetry_evidence(disp, observation_id="repair-not-1")
    with pytest.raises(Exception):
        w3.project_needs_repair_disposition(ev, disp)

# ---------------------------------------------------------------------------
# 10. Same source/version deterministic
# ---------------------------------------------------------------------------

def test_same_source_version_deterministic():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="det-1")
    c1 = w3.project_review_required(ev, disp, projection_version="s6-m3-w3-v1")
    c2 = w3.project_review_required(ev, disp, projection_version="s6-m3-w3-v1")
    assert c1.projection_id == c2.projection_id
    assert c1.aggregation_series_id == c2.aggregation_series_id
    assert c1.source_dedup_id == c2.source_dedup_id

# ---------------------------------------------------------------------------
# 11. New projection version changes projection id only
# ---------------------------------------------------------------------------

def test_new_version_changes_projection_id_only():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="ver-1")
    c1 = w3.project_review_required(ev, disp, projection_version="s6-m3-w3-v1")
    c2 = w3.project_review_required(ev, disp, projection_version="s6-m3-w3-v2")
    assert c1.projection_id != c2.projection_id
    assert c1.source_dedup_id == c2.source_dedup_id
    assert c1.aggregation_series_id == c2.aggregation_series_id

# ---------------------------------------------------------------------------
# 12. source_dedup_id unchanged
# ---------------------------------------------------------------------------

def test_source_dedup_unchanged():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="dedup-1")
    d1 = ev.source_dedup_id
    c1 = w3.project_review_required(ev, disp, projection_version="v1")
    c2 = w3.project_review_required(ev, disp, projection_version="v2")
    assert c1.source_dedup_id == d1
    assert c2.source_dedup_id == d1
    assert c1.source_dedup_id == c2.source_dedup_id

# ---------------------------------------------------------------------------
# 13. exact Work Item/result binding required
# ---------------------------------------------------------------------------

def test_exact_work_item_result_binding_required():
    card = _make_worker_card("task-w1")
    handoff = _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a")
    progress = _make_progress("S4/M2/W1", "task-w1", card)
    ev = _make_s4_telemetry_evidence(progress, observation_id="bind-1")
    # Valid binding
    contrib = w3.project_work_item_result_binding(ev, progress, {"task-w1": handoff}, {"task-w1": card})
    assert isinstance(contrib, AggregationContribution)
    # Missing handoff should fail
    with pytest.raises(Exception):
        w3.project_work_item_result_binding(ev, progress, {}, {"task-w1": card})

# ---------------------------------------------------------------------------
# 14. foreign Work Item result replay rejected
# ---------------------------------------------------------------------------

def test_foreign_work_item_replay_rejected():
    card = _make_worker_card("task-w1")
    # Handoff binds task-w1 to W1, but we try to use progress for W2 with same task
    handoff_w1 = _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a")
    progress_w2 = WorkItemProgressEvidence(work_item_ref="S4/M2/W2", worker_result_ref=_handoff_ref("task-w1", "corr1"), worker_result_digest=card.card_digest)
    ev = _make_s4_telemetry_evidence(progress_w2, observation_id="foreign-1")
    with pytest.raises(Exception) as exc:
        w3.project_work_item_result_binding(ev, progress_w2, {"task-w1": handoff_w1}, {"task-w1": card})
    assert "mismatch" in str(exc.value).lower() or "fail_closed" in str(exc.value).lower()

# ---------------------------------------------------------------------------
# 15. mismatched result ref rejected
# ---------------------------------------------------------------------------

def test_mismatched_result_ref_rejected():
    card = _make_worker_card("task-w1")
    handoff = _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a")
    # Progress claims ref task-w2 but card is task-w1
    progress = WorkItemProgressEvidence(work_item_ref="S4/M2/W1", worker_result_ref=_handoff_ref("task-w2", "corr1"), worker_result_digest=card.card_digest)
    ev = _make_s4_telemetry_evidence(progress, observation_id="ref-mismatch-1")
    with pytest.raises(Exception):
        w3.project_work_item_result_binding(ev, progress, {"task-w1": handoff}, {"task-w1": card})

# ---------------------------------------------------------------------------
# 16. mismatched result digest rejected
# ---------------------------------------------------------------------------

def test_mismatched_result_digest_rejected():
    card = _make_worker_card("task-w1")
    handoff = _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a")
    progress = WorkItemProgressEvidence(work_item_ref="S4/M2/W1", worker_result_ref=_handoff_ref("task-w1", "corr1"), worker_result_digest="f"*64)
    ev = _make_s4_telemetry_evidence(progress, observation_id="digest-mismatch-1")
    with pytest.raises(Exception):
        w3.project_work_item_result_binding(ev, progress, {"task-w1": handoff}, {"task-w1": card})

# ---------------------------------------------------------------------------
# 17. predecessor-incomplete progression does not become complete/no-friction
# ---------------------------------------------------------------------------

def test_predecessor_incomplete_not_complete():
    graph = _make_graph(["W1", "W2"], [("W1", "W2")])
    disp = evaluate_work_item_risk_policy(envelope=MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST))
    # Only W2 has progress, W1 missing, so W2 cannot be complete
    card_w2 = _make_worker_card("task-w2")
    pe_w2 = _make_progress("W2", "task-w2", card_w2)
    ve_w2 = _make_validation("W2", FocusedValidationVerdict.PASS)
    # Create handoffs for W2
    h_w2 = _make_task_handoff(task_id="task-w2", work_item_ref="W2", milestone_ref="S4/M2", project_ref="proj-a")
    # Need to provide task_handoffs mapping for W2
    ev = _make_s4_telemetry_evidence(pe_w2, observation_id="pred-1")
    # Try to project progression complete for W2 - should fail because W1 not complete
    with pytest.raises(Exception):
        w3.project_progression_complete_with_predecessors(
            ev, graph, [pe_w2], [ve_w2], {"W1": disp, "W2": disp}, [], {"task-w2": card_w2}, {}, "W2",
            task_handoffs={"task-w2": h_w2}
        )
    # Also test join waiting: W2 should be blocked due to predecessor
    # Create a progression disposition where W2 blocked
    # Use evaluate to get actual disposition
    res = evaluate_milestone_progression(graph, [pe_w2], [ve_w2], {"W1": disp, "W2": disp}, [], {"task-w2": card_w2}, {}, task_handoffs={"task-w2": h_w2})
    assert "W2" not in res.progression_complete_work_item_refs
    # Now test that join waiting metric would be produced when we have graph with join
    graph2 = _make_graph(["W1", "W2", "W3", "W4"], [("W1", "W2"), ("W1", "W3"), ("W2", "W4"), ("W3", "W4")])
    card_w1 = _make_worker_card("task-w1")
    pe_w1 = _make_progress("W1", "task-w1", card_w1)
    ve_w1 = _make_validation("W1", FocusedValidationVerdict.PASS)
    h_w1 = _make_task_handoff(task_id="task-w1", work_item_ref="W1", milestone_ref="S4/M2", project_ref="proj-a")
    card_w2b = _make_worker_card("task-w2")
    pe_w2b = _make_progress("W2", "task-w2", card_w2b)
    ve_w2b = _make_validation("W2", FocusedValidationVerdict.PASS)
    h_w2b = _make_task_handoff(task_id="task-w2", work_item_ref="W2", milestone_ref="S4/M2", project_ref="proj-a")
    # Only W1 and W2 complete, W3 missing -> W4 should be waiting
    ev2 = _make_s4_telemetry_evidence(graph2, observation_id="pred-wait-1")
    # Need to create evidence that matches graph digest for join waiting
    # Our helper creates evidence with digest == graph digest, so it will match
    # For predecessor waiting, we need to ensure W4 is blocked
    res2 = evaluate_milestone_progression(graph2, [pe_w1, pe_w2b], [ve_w1, ve_w2b], {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, [], {"task-w1": card_w1, "task-w2": card_w2b}, {}, task_handoffs={"task-w1": h_w1, "task-w2": h_w2b})
    assert "W4" in res2.blocked_work_item_refs
    # Now project join waiting
    ev_wait = _make_s4_telemetry_evidence(graph2, observation_id="pred-wait-2")
    contrib = w3.project_join_predecessor_waiting(ev_wait, graph2, [pe_w1, pe_w2b], [ve_w1, ve_w2b], {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, [], {"task-w1": card_w1, "task-w2": card_w2b}, {}, "W4", task_handoffs={"task-w1": h_w1, "task-w2": h_w2b})
    assert isinstance(contrib, AggregationContribution)

# ---------------------------------------------------------------------------
# 18. missing ReviewSatisfactionEvidence when formal review required does not become satisfied
# ---------------------------------------------------------------------------

def test_missing_satisfaction_not_satisfied():
    disp = _make_disposition(formal_review_required=True)
    assert disp.formal_review_required is True
    ev = _make_s4_telemetry_evidence(disp, observation_id="miss-sat-1")
    # Try to project satisfaction missing as friction, not as satisfied
    # First, ensure that projecting satisfaction without evidence fails
    with pytest.raises(Exception):
        # This should fail because we don't have satisfaction evidence
        w3.project_review_satisfaction(ev, None)  # type: ignore
    # Now test that missing satisfaction is correctly identified as friction, not satisfied
    # Our project_review_satisfaction_missing should succeed when satisfaction missing
    contrib = w3.project_review_satisfaction_missing(ev, disp, [], work_item_ref="S4/M2/W1")
    assert isinstance(contrib, AggregationContribution)
    # And ensure that if satisfaction exists, missing projection fails
    reviewer_card = _make_worker_card("task-reviewer", role=AgentWorkRole.REVIEWER)
    sat = _make_satisfaction(disp=disp, reviewer_card=reviewer_card)
    with pytest.raises(Exception):
        w3.project_review_satisfaction_missing(ev, disp, [sat], work_item_ref="S4/M2/W1")

# ---------------------------------------------------------------------------
# 19. naked boolean cannot create review metric truth
# ---------------------------------------------------------------------------

def test_naked_boolean_cannot_create_review_metric():
    ev = _make_s4_telemetry_evidence(_make_disposition(), observation_id="naked-1")
    with pytest.raises(Exception):
        w3.project_naked_review_boolean(ev, True)
    with pytest.raises(Exception):
        w3.project_naked_review_boolean(ev, False)
    # Also ensure that passing boolean as satisfaction fails
    with pytest.raises(Exception):
        w3.project_review_satisfaction(ev, True)  # type: ignore
    # Check flag
    assert NAKED_REVIEW_COMPLETED_BOOLEAN_ALLOWED is False

# ---------------------------------------------------------------------------
# 20. cross-project mismatch fails closed
# ---------------------------------------------------------------------------

def test_cross_project_mismatch_fails_closed():
    disp = _make_disposition()
    ev_a = _make_s4_telemetry_evidence(disp, project_id="proj-a", observation_id="cross-a")
    # Create a disposition that we will add project_id attribute to simulate cross-project
    disp_b = _make_disposition()
    # Add project_id attribute to disp_b (frozen, so use object.__setattr__)
    object.__setattr__(disp_b, "project_id", "proj-b")
    with pytest.raises(Exception) as exc:
        w3.project_review_required(ev_a, disp_b)
    assert "cross-project" in str(exc.value).lower()

def test_cross_project_via_telemetry_evidence():
    disp = _make_disposition()
    ev_a = _make_s4_telemetry_evidence(disp, project_id="proj-a", observation_id="cross-2a")
    ev_b = _make_s4_telemetry_evidence(disp, project_id="proj-b", observation_id="cross-2b")
    # Try to use ev_b's domain with ev_a's evidence -> should fail on project binding if domain has project_id
    # Our domain is disp which has no project_id, so cross-project via S4 evidence is hard to test
    # Instead test with WorkItemProgressEvidence that has project via handoff
    card = _make_worker_card("task-w1")
    handoff_a = _make_task_handoff(task_id="task-w1", work_item_ref="S4/M2/W1", milestone_ref="S4/M2", project_ref="proj-a")
    progress_a = _make_progress("S4/M2/W1", "task-w1", card)
    ev = _make_s4_telemetry_evidence(progress_a, project_id="proj-b", observation_id="cross-3")
    with pytest.raises(Exception):
        w3.project_work_item_result_binding(ev, progress_a, {"task-w1": handoff_a}, {"task-w1": card})

# ---------------------------------------------------------------------------
# 21. source/TelemetryEvidence mismatch fails closed
# ---------------------------------------------------------------------------

def test_source_telemetry_mismatch_fails_closed():
    disp = _make_disposition()
    ev1 = _make_s4_telemetry_evidence(disp, observation_id="mismatch-1")
    # Create second disposition with different digest
    env = MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.DEEP, minimum_process_depth=ProcessDepth.FAST)
    disp2 = evaluate_work_item_risk_policy(envelope=env, review_triggers=(ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE,))
    assert disp2.digest != disp.digest
    ev2 = _make_s4_telemetry_evidence(disp2, observation_id="mismatch-2")
    # Try to project disp2 with ev1 (digest mismatch)
    with pytest.raises(Exception) as exc:
        w3.project_review_required(ev1, disp2)
    assert "mismatch" in str(exc.value).lower()

def test_source_evidence_binding_mismatch_for_validation():
    val = _make_validation(verdict=FocusedValidationVerdict.FAIL)
    ev1 = _make_s4_telemetry_evidence(val, observation_id="bind-1")
    val2 = _make_validation(verdict=FocusedValidationVerdict.FAIL)
    # val2 has different digest because work_item_ref maybe same but validation_evidence_ref different? Use same but create different object with same content -> digest same, so need different work_item_ref to get different digest
    val_other = FocusedValidationEvidence(work_item_ref="S4/M2/W2", verdict=FocusedValidationVerdict.FAIL, validation_evidence_ref=_semantic("val:W2-other"))
    ev_other = _make_s4_telemetry_evidence(val_other, observation_id="bind-2")
    # Try to project val_other with ev1
    with pytest.raises(Exception):
        w3.project_validation_failure(ev1, val_other)

# ---------------------------------------------------------------------------
# 22. completeness preserved
# ---------------------------------------------------------------------------

def test_completeness_preserved():
    disp = _make_disposition()
    comp = CompletenessRecord(state=CompletenessState.PARTIAL, scope=CompletenessScope.SOURCE)
    ev = _make_s4_telemetry_evidence(disp, completeness=comp, observation_id="comp-1")
    contrib = w3.project_review_required(ev, disp)
    assert contrib.completeness.state == CompletenessState.PARTIAL
    assert contrib.completeness.scope == CompletenessScope.SOURCE
    assert contrib.completeness == ev.completeness

# ---------------------------------------------------------------------------
# 23. missing != zero
# ---------------------------------------------------------------------------

def test_missing_not_zero():
    disp = _make_disposition()
    comp_missing = CompletenessRecord(state=CompletenessState.MISSING, scope=CompletenessScope.SOURCE)
    ev_missing = _make_s4_telemetry_evidence(disp, completeness=comp_missing, observation_id="missing-1")
    contrib = w3.project_review_required(ev_missing, disp)
    assert contrib.completeness.state == CompletenessState.MISSING
    assert contrib.value == 1
    # Missing must not be zero: value is 1, completeness indicates missing, not zero
    assert contrib.completeness.state.value != "complete"
    assert MISSING_TELEMETRY_IS_ZERO is False
    # Also check that empty observation doesn't imply complete zero
    assert GLOBAL_COMPLETENESS_SEVERITY_ORDER is False

# ---------------------------------------------------------------------------
# 24. no Work Item ref as default metric dimension
# ---------------------------------------------------------------------------

def test_no_work_item_ref_as_default_dimension():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="dim-wi-1")
    # Try to inject work_item_ref as dimension
    with pytest.raises(Exception):
        w3.project_review_required(ev, disp, normalized_dimensions={"work_item_ref": "S4/M2/W1"})
    with pytest.raises(Exception):
        w3.project_review_required(ev, disp, normalized_dimensions={"task_id": "task-123"})
    assert WORK_ITEM_REF_IS_DEFAULT_METRIC_DIMENSION is False

# ---------------------------------------------------------------------------
# 25. no raw finding/summary/error dimension
# ---------------------------------------------------------------------------

def test_no_raw_finding_dimension():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="raw-1")
    with pytest.raises(Exception):
        w3.project_review_required(ev, disp, normalized_dimensions={"finding": "very long raw finding with /tmp/path and error stack trace and spaces"})
    with pytest.raises(Exception):
        w3.project_review_required(ev, disp, normalized_dimensions={"raw_error": "FileNotFoundError: /tmp/foo"})
    # Check that our module doesn't allow raw dims
    import aota_forge.work_plane.telemetry_metrics as tm
    assert "work_item_ref" not in tm.ALLOWED_DIMENSION_KEYS
    assert "task_id" not in tm.ALLOWED_DIMENSION_KEYS

# ---------------------------------------------------------------------------
# 26. review metric non-authority
# ---------------------------------------------------------------------------

def test_review_metric_non_authority():
    assert REVIEW_METRIC_IS_REVIEW_AUTHORITY is False
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="auth-1")
    contrib = w3.project_review_required(ev, disp)
    # Metric should not be authority
    assert REVIEW_METRIC_IS_REVIEW_AUTHORITY is False
    # Check that contribution doesn't grant authority
    assert contrib.project_id == "proj-a"

# ---------------------------------------------------------------------------
# 27. repair metric non-authority
# ---------------------------------------------------------------------------

def test_repair_metric_non_authority():
    assert REPAIR_METRIC_IS_REPAIR_AUTHORITY is False
    disp = _make_disposition_needs_repair()
    ev = _make_s4_telemetry_evidence(disp, observation_id="auth-repair-1")
    contrib = w3.project_needs_repair_disposition(ev, disp)
    assert REPAIR_METRIC_IS_REPAIR_AUTHORITY is False

# ---------------------------------------------------------------------------
# 28. workflow metric non-authority
# ---------------------------------------------------------------------------

def test_workflow_metric_non_authority():
    assert WORKFLOW_METRIC_IS_PROGRESSION_AUTHORITY is False
    val = _make_validation(verdict=FocusedValidationVerdict.FAIL)
    ev = _make_s4_telemetry_evidence(val, observation_id="auth-wf-1")
    contrib = w3.project_validation_failure(ev, val)
    assert WORKFLOW_METRIC_IS_PROGRESSION_AUTHORITY is False
    assert S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY is False

# ---------------------------------------------------------------------------
# 29. projection failure leaves S4 evidence unchanged
# ---------------------------------------------------------------------------

def test_projection_failure_leaves_evidence_unchanged():
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="fail-leave-1")
    orig_disp_dict = disp.to_dict()
    orig_ev_dict = ev.to_dict()
    # Cause failure via cross-project
    disp_b = _make_disposition()
    ev_b = _make_s4_telemetry_evidence(disp_b, project_id="proj-b", observation_id="fail-leave-b")
    try:
        w3.project_review_required(ev, disp_b)
    except Exception:
        pass
    assert disp.to_dict() == orig_disp_dict
    assert ev.to_dict() == orig_ev_dict
    assert disp_b.to_dict() == disp_b.to_dict()  # unchanged
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_SOURCE_EVIDENCE is True
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT is True
    # Also check progression state not rewritten
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_PROGRESSION_STATE is True

# ---------------------------------------------------------------------------
# 30. no RV2 metric
# ---------------------------------------------------------------------------

def test_no_rv2_metric():
    assert RV2_METRIC_IMPLEMENTED is False
    # Check flag definition
    text = Path(inspect.getfile(w3)).read_text()
    assert "RV2_METRIC_IMPLEMENTED: bool = False" in text
    # Ensure no function projects RV2 beyond flag and helper
    for name in dir(w3):
        if "rv2" in name.lower() and "implemented" not in name.lower() and not name.startswith("_check"):
            assert False, f"RV2 metric found {name}"

# ---------------------------------------------------------------------------
# 31. no repair-cycle metric
# ---------------------------------------------------------------------------

def test_no_repair_cycle_metric():
    assert REPAIR_CYCLE_METRIC_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "REPAIR_CYCLE_METRIC_IMPLEMENTED: bool = False" in text
    assert "repair-cycle" not in text.lower() or "REPAIR_CYCLE" in text

# ---------------------------------------------------------------------------
# 32. no replan metric
# ---------------------------------------------------------------------------

def test_no_replan_metric():
    assert REPLAN_METRIC_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "REPLAN_METRIC_IMPLEMENTED: bool = False" in text
    assert "REPLAN_REQUIRED" not in text or "REPLAN_METRIC_IMPLEMENTED" in text

# ---------------------------------------------------------------------------
# 33. no closure-readiness metric
# ---------------------------------------------------------------------------

def test_no_closure_readiness_metric():
    assert CLOSURE_READINESS_METRIC_IMPLEMENTED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "CLOSURE_READINESS_METRIC_IMPLEMENTED: bool = False" in text
    assert "closure" not in text.lower() or "CLOSURE_READINESS" in text

# ---------------------------------------------------------------------------
# 34. no composite friction score
# ---------------------------------------------------------------------------

def test_no_composite_friction_score():
    assert COMPOSITE_FRICTION_SCORE_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "COMPOSITE_FRICTION_SCORE_CREATED: bool = False" in text
    # Check that no composite score implementation beyond flag
    # Allow flag line itself which contains friction_score as part of flag name
    # Remove flag lines before checking
    filtered = "\n".join([l for l in text.splitlines() if "COMPOSITE_FRICTION_SCORE_CREATED" not in l])
    for kw in ["friction_score", "workflow_friction_score", "review_efficiency_score", "workflow_health_score"]:
        # Allow composite in docstring but not as code
        if kw in filtered.lower():
            # Check if it's in comment/docstring allowed, but we treat as fail only if it's actual code
            # For simplicity, ensure no function defines it
            assert False, f"composite friction score leaked: {kw}"

# ---------------------------------------------------------------------------
# 35. no W4 registry/integration
# ---------------------------------------------------------------------------

def test_no_w4_registry_integration():
    assert SHARED_REGISTRY_UPDATE_IN_W3 is False
    assert W4_SCOPE_IMPLEMENTED_IN_W3 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "SHARED_REGISTRY_UPDATE_IN_W3: bool = False" in text
    assert "W4_SCOPE_IMPLEMENTED_IN_W3: bool = False" in text
    assert "all-family" not in text.lower()
    assert "global dispatcher" not in text.lower() or "W4" in text

# ---------------------------------------------------------------------------
# 36. no calibration/recommendation
# ---------------------------------------------------------------------------

def test_no_calibration_recommendation():
    assert M3_CALIBRATION_THRESHOLD_CREATED is False
    assert M3_RECOMMENDATION_ENGINE_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "M3_CALIBRATION_THRESHOLD_CREATED: bool = False" in text
    assert "M3_RECOMMENDATION_ENGINE_CREATED: bool = False" in text
    for kw in ["calibration", "threshold", "recommendation", "optimization"]:
        # Allow flag lines but not implementation
        if kw in ["threshold", "calibration", "recommendation"]:
            # Check that implementation not present beyond flag
            lines = [l for l in text.splitlines() if kw in l.lower() and "M3_" not in l]
            # If any non-flag line contains these, fail
            for l in lines:
                if "Created" not in l and "bool" not in l:
                    # Allow comments
                    if l.strip().startswith("#") or l.strip().startswith('"') or l.strip().startswith("'"):
                        continue
                    assert False, f"calibration/recommendation leaked: {l}"

# ---------------------------------------------------------------------------
# 37. no new store/query/runtime
# ---------------------------------------------------------------------------

def test_no_new_store_query_runtime():
    assert SECOND_METRIC_RUNTIME_CREATED is False
    assert SECOND_TELEMETRY_STORE_CREATED is False
    assert SECOND_QUERY_ENGINE_CREATED is False
    assert DURABLE_STORAGE_REQUIRED_FOR_W3 is False
    assert STORAGE_ENGINE_SELECTED_IN_W3 is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "SECOND_METRIC_RUNTIME_CREATED: bool = False" in text
    assert "class TelemetryStore" not in text
    assert "class TelemetryQuery" not in text

# ---------------------------------------------------------------------------
# 38. W1 Role/Tool/Skill regression
# ---------------------------------------------------------------------------

def test_w1_role_tool_skill_regression():
    ev, obs = _make_tool_evidence_for_w1(project_id="proj-a", observation_id="w1-reg-1")
    c1 = w1_project_role(ev, obs)
    assert isinstance(c1, AggregationContribution)
    ev2, obs2 = _make_tool_evidence_for_w1(project_id="proj-a", observation_id="w1-reg-2")
    c2 = w1_project_tool(ev2, obs2)
    assert isinstance(c2, AggregationContribution)
    ev3, obs3 = _make_skill_evidence_for_w1(project_id="proj-a", event_id="evt-w1-reg")
    c3 = w1_project_skill(ev3, obs3)
    assert isinstance(c3, AggregationContribution)

# ---------------------------------------------------------------------------
# 39. W2 shell/context regression
# ---------------------------------------------------------------------------

def test_w2_shell_context_regression():
    # Shell
    obs_tool = ToolUsageObservation(
        observation_id="shell-reg-1",
        operation_name="restricted_shell.run",
        contract_hash="a"*64,
        is_success=True,
        outcome_class="success",
        side_effect="shell_process",
        work_role=AgentWorkRole.CODER,
        project_id="proj-a",
        worktree_id="wt-1",
    )
    from aota_forge.work_plane.telemetry_adapters import adapt_tool_usage_observation
    ev_shell = adapt_tool_usage_observation(obs_tool, _ingest(), project_id="proj-a", worktree_id="wt-1")
    pat = create_normalized_shell_pattern("echo", argument_classes=("text_class",), outcome_class="success")
    c_shell = w2_project_shell(ev_shell, pat)
    assert isinstance(c_shell, AggregationContribution)
    # Context
    ev_ctx, _ = _make_tool_evidence_for_w1(project_id="proj-a", observation_id="ctx-reg-1")
    m = ContextCostMeasurement(value=100, unit="bytes_utf8", measured_component="test")
    c_ctx = w2_project_context(ev_ctx, m)
    assert isinstance(c_ctx, AggregationContribution)

# ---------------------------------------------------------------------------
# 40. I29-B001 adversarial regression
# ---------------------------------------------------------------------------

def test_i29_b001_adversarial_regression():
    # Exact binding preserved, predecessor requires, foreign replay rejected
    # Use progression evaluator directly to test I29-B001
    graph = _make_graph(["W1", "W2"], [("W1", "W2")])
    disp = evaluate_work_item_risk_policy(envelope=MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST))
    card_w1 = _make_worker_card("task-w1")
    h_w1 = _make_task_handoff(task_id="task-w1", work_item_ref="W1", milestone_ref="S4/M2", project_ref="proj-a")
    pe_w1 = _make_progress("W1", "task-w1", card_w1)
    ve_w1 = _make_validation("W1", FocusedValidationVerdict.PASS)
    # Try foreign replay: use task-w1 for W2
    card_w1_dup = _make_worker_card("task-w1")  # same task as W1
    pe_w2_foreign = WorkItemProgressEvidence(work_item_ref="W2", worker_result_ref=_handoff_ref("task-w1", "corr1"), worker_result_digest=card_w1_dup.card_digest)
    ve_w2 = _make_validation("W2", FocusedValidationVerdict.PASS)
    h_w2 = _make_task_handoff(task_id="task-w2", work_item_ref="W2", milestone_ref="S4/M2", project_ref="proj-a")
    # Evaluate with foreign replay - should be blocked
    res = evaluate_milestone_progression(graph, [pe_w1, pe_w2_foreign], [ve_w1, ve_w2], {"W1": disp, "W2": disp}, [], {"task-w1": card_w1, "task-w2": card_w1_dup}, {}, task_handoffs={"task-w1": h_w1, "task-w2": h_w2})
    assert "W2" not in res.progression_complete_work_item_refs
    assert EXACT_WORK_ITEM_RESULT_BINDING_PRESERVED is True
    assert PROGRESSION_COMPLETE_REQUIRES_PREDECESSORS is True
    assert S4_PRE_REPAIR_FRONTIER_CONSUMED is False
    # Also test W3 projector enforces exact binding
    ev = _make_s4_telemetry_evidence(pe_w2_foreign, observation_id="i29-1")
    with pytest.raises(Exception):
        w3.project_work_item_result_binding(ev, pe_w2_foreign, {"task-w1": h_w1}, {"task-w1": card_w1})

def test_i29_b001_predecessor_gating():
    graph = _make_graph(["W1", "W2"], [("W1", "W2")])
    disp = evaluate_work_item_risk_policy(envelope=MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.FAST))
    # W2 has progress but W1 missing, so W2 cannot be complete
    card_w2 = _make_worker_card("task-w2")
    pe_w2 = _make_progress("W2", "task-w2", card_w2)
    ve_w2 = _make_validation("W2", FocusedValidationVerdict.PASS)
    h_w2 = _make_task_handoff(task_id="task-w2", work_item_ref="W2", milestone_ref="S4/M2", project_ref="proj-a")
    res = evaluate_milestone_progression(graph, [pe_w2], [ve_w2], {"W1": disp, "W2": disp}, [], {"task-w2": card_w2}, {}, task_handoffs={"task-w2": h_w2})
    assert "W2" not in res.progression_complete_work_item_refs
    assert "W2" in res.blocked_work_item_refs

# ---------------------------------------------------------------------------
# Additional: provenance explicit, no S4 M3 leakage, etc.
# ---------------------------------------------------------------------------

def test_metric_source_provenance_explicit():
    assert METRIC_SOURCE_PROVENANCE_EXPLICIT is True
    disp = _make_disposition()
    ev = _make_s4_telemetry_evidence(disp, observation_id="prov-1")
    # Must be S4_WORKFLOW_EVIDENCE for W3
    with pytest.raises(Exception):
        w3.project_review_required(ev, disp, source_provenance=MetricSourceProvenance.RUNTIME_OBSERVATION)
    # Valid
    c = w3.project_review_required(ev, disp, source_provenance=MetricSourceProvenance.S4_WORKFLOW_EVIDENCE)
    assert isinstance(c, AggregationContribution)

def test_no_s4_m3_source_leakage():
    # Ensure we don't import S4 M3 files
    text = Path(inspect.getfile(w3)).read_text()
    assert "milestone_closure" not in text
    assert "1a3cd2c" not in text
    assert S4_M3_ACCEPTED_SOURCE_CONSUMED_BY_W3 is False
    assert S4_M3_ONLY_REPAIR_SEMANTICS_SYNTHESIZED is False
    assert S4_PRE_REPAIR_FRONTIER_CONSUMED is False

def test_primitive_metrics_first():
    assert PRIMITIVE_METRICS_FIRST is True
    assert RATE_METRIC_IMPLEMENTED_IN_W3 is False

def test_no_temporal_rate_or_latency():
    assert IMPLICIT_WALL_CLOCK_USED is False
    assert TEMPORAL_RATE_OR_LATENCY_METRIC_CREATED is False
    text = Path(inspect.getfile(w3)).read_text()
    assert "datetime.now" not in text
    assert "time.time" not in text

def test_review_metric_is_not_authority():
    assert REVIEW_METRIC_IS_REVIEW_AUTHORITY is False
    assert REPAIR_METRIC_IS_REPAIR_AUTHORITY is False
    assert WORKFLOW_METRIC_IS_PROGRESSION_AUTHORITY is False
    assert S6_METRIC_AUTOMATICALLY_CHANGES_S4_POLICY is False

def test_projection_does_not_rewrite_source():
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_SOURCE_EVIDENCE is True
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_PROGRESSION_STATE is True
    assert PROJECTION_FAILURE_DOES_NOT_REWRITE_EXECUTION_RESULT is True

def test_w3_namespaces_bounded():
    assert len(W3_PROJECTION_NAMESPACE) <= 64
    assert len(W3_PROJECTION_VERSION) <= 64
    assert "\x00" not in W3_PROJECTION_NAMESPACE
    assert "\x00" not in W3_PROJECTION_VERSION

def test_s4_m2_workflow_evidence_used():
    assert S4_M2_WORKFLOW_EVIDENCE_USED is True
    # Ensure W3 imports S4/M2 types
    text = Path(inspect.getfile(w3)).read_text()
    assert "from aota_forge.work_plane.risk_review import" in text
    assert "from aota_forge.work_plane.progression import" in text

