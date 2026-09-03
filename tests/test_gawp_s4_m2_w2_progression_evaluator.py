"""W2 Acceptance Tests — Deterministic Progression & Escalation Evaluator (S4 M2 W2)."""

import hashlib
import pytest
from enum import Enum

from aota_forge.work_plane.progression import (
    MilestoneWorkItemGraph,
    WorkItemProgressEvidence,
    FocusedValidationEvidence,
    FocusedValidationVerdict,
    ReviewSatisfactionEvidence,
    ProgressionDisposition,
    PerWorkItemChallengeRouting,
    evaluate_milestone_progression,
    PROGRESSION_DISPOSITION_IS_OPERATION_AUTHORITY,
    PROGRESSION_DISPOSITION_IS_PLAN_AUTHORITY,
    PROGRESSION_DISPOSITION_IS_MILESTONE_ACCEPTANCE,
    PROGRESSION_DISPOSITION_IS_WORK_ITEM_ACCEPTANCE,
    AUTO_PROGRESSION_ALLOWED_IS_OPERATION_AUTHORITY,
    PROGRESSION_DERIVED_FROM_EVIDENCE,
    PROGRESSION_EVALUATOR_STATELESS,
    PROGRESSION_EVALUATOR_RUNS_GIT,
    WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY,
    WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY,
    AUTOMATIC_MILESTONE_APPROVAL,
    AUTOMATIC_MILESTONE_CLOSURE,
    M2_AUTO_RETRY_ENGINE_CREATED,
    M2_REPAIR_LOOP_CREATED,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.risk_review import (
    ChallengeRole,
    ChallengeKind,
    ReviewTrigger,
    ReviewEscalationDisposition,
    MilestoneRiskEnvelope,
    WorkItemRiskDelta,
    ProcessDepth,
    evaluate_work_item_risk_policy,
)
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultOutcome
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.stop import SemanticStop, MechanicalFailure


def _handoff(ref: str, digest: str | None = None) -> ResultHandoffRef:
    return ResultHandoffRef(ref=ref, digest=digest)


def _semantic(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)


def _graph(work_items, deps, milestone_ref="S4/M2"):
    return MilestoneWorkItemGraph(milestone_ref=milestone_ref, work_items=tuple(work_items), dependencies=tuple(deps))


def _make_card(task_id: str, executor_id: str = "e1", correlation_id: str = "corr1", outcome_success: bool = True, role: AgentWorkRole = AgentWorkRole.CODER, semantic_stop=None, mechanical=None, next_hint=None):
    # Use CanonicalResult and governance to build card
    if outcome_success:
        cr = CanonicalResult.success(canonical_task_id=task_id, executor_id=executor_id, correlation_id=correlation_id)
        gov = ResultGovernanceProjection.success()
    else:
        cr = CanonicalResult.failure(canonical_task_id=task_id, executor_id=executor_id, error_code="ERR", error_message="fail", correlation_id=correlation_id)
        gov = ResultGovernanceProjection.failure({"code": "ERR", "message": "fail", "retryable": False})
        # For failure we need to create card with failure outcome
        # Directly construct WorkerResultCard with failure outcome
    # For UNKNOWN or other, we can construct directly
    if outcome_success:
        return WorkerResultCard(
            task_ref=task_id,
            agent_work_role=role,
            summary="done",
            outcome=gov.outcome,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
            primary_evidence_refs=(),
            output_artifact_refs=(),
            next_hint=next_hint,
            semantic_stop=semantic_stop,
            mechanical_failure=mechanical,
        )
    else:
        # For failure, we need to bypass semantic_stop conflict if any
        return WorkerResultCard(
            task_ref=task_id,
            agent_work_role=role,
            summary="failed",
            outcome=ResultOutcome.FAILURE,
            blocking_finding_count=1,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
            primary_evidence_refs=(),
            output_artifact_refs=(),
            next_hint=next_hint,
            semantic_stop=semantic_stop,
            mechanical_failure=mechanical,
        )


def _make_unknown_card(task_id: str, role=AgentWorkRole.CODER):
    cr = CanonicalResult(
        ok=False,
        status="unknown",
        canonical_task_id=task_id,
        executor_id="e1",
        canonical_task_state="UNKNOWN",
        exit_code=None,
        result_data={},
        output_artifacts=(),
        stdout_summary=None,
        stderr_summary=None,
        error={"code": "UNKNOWN", "message": "unknown", "retryable": False},
        execution_stats={},
        correlation_id="corr-unknown",
    )
    gov = ResultGovernanceProjection(
        governance_version="1.0",
        outcome=ResultOutcome.UNKNOWN,
        error={"code": "UNKNOWN", "message": "unknown", "retryable": False},
    )
    return WorkerResultCard(
        task_ref=task_id,
        agent_work_role=role,
        summary="unknown",
        outcome=ResultOutcome.UNKNOWN,
        blocking_finding_count=0,
        non_blocking_finding_count=0,
        result_handoff_ref=ResultHandoffRef(ref=task_id, digest="corr-unknown"),
        primary_evidence_refs=(),
        output_artifact_refs=(),
    )


def _envelope(depth=ProcessDepth.STANDARD):
    return MilestoneRiskEnvelope(milestone_ref="S4/M2", default_process_depth=depth, minimum_process_depth=ProcessDepth.FAST)


def _disp_eligible_no_review():
    env = _envelope()
    return evaluate_work_item_risk_policy(envelope=env)


def _disp_with_review(trigger=ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY):
    env = _envelope()
    return evaluate_work_item_risk_policy(envelope=env, review_triggers=(trigger,))


def _disp_semantic_escalation():
    env = _envelope()
    # Use escalation triggers to force semantic escalation
    return evaluate_work_item_risk_policy(envelope=env, escalation_triggers=("NEW_MILESTONE_APPROVAL",))


def _disp_not_eligible():
    # Create disposition with auto_continuation false via unresolved uncertainty? Simpler: manually construct
    return ReviewEscalationDisposition(
        selected_process_depth=ProcessDepth.DEEP,
        formal_review_required=False,
        challenge_role=None,
        challenge_kind=None,
        semantic_escalation_required=False,
        auto_continuation_eligible=False,
        identified_risk=None,
        justification=None,
        end_condition=None,
        reasons=(),
        evidence_refs=(),
    )


def _valid_evidence_sets_for_w(wi, task_id, role=AgentWorkRole.CODER, include_review=False, review_disp=None):
    card = _make_card(task_id, role=role)
    pe = WorkItemProgressEvidence(work_item_ref=wi, worker_result_ref=ResultHandoffRef(ref=task_id, digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref=wi, verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:"+wi))
    return card, pe, ve


# ===========================================================================
# Basic progression
# ===========================================================================

def test_single_root_no_evidence_ready():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    res = evaluate_milestone_progression(
        graph=g,
        progress_evidence=(),
        validation_evidence=(),
        review_dispositions={"W1": disp},
        satisfaction_evidence=(),
        worker_cards={},
        review_result_cards={},
    )
    assert res.progression_complete_work_item_refs == ()
    assert res.ready_work_item_refs == ("W1",)
    assert res.blocked_work_item_refs == ()
    assert res.milestone_review_ready is False
    assert res.auto_progression_allowed is True


def test_linear_deterministic_progression():
    g = _graph(["W1", "W2", "W3"], [("W1", "W2"), ("W2", "W3")])
    disp = _disp_eligible_no_review()
    # Initial
    res0 = evaluate_milestone_progression(g, (), (), {"W1": disp, "W2": disp, "W3": disp}, (), {}, {})
    assert res0.ready_work_item_refs == ("W1",)
    assert res0.progression_complete_work_item_refs == ()
    # After W1 complete
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res1 = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1}, {})
    assert res1.progression_complete_work_item_refs == ("W1",)
    assert res1.ready_work_item_refs == ("W2",)
    assert "W3" in res1.blocked_work_item_refs
    # After W1+W2 complete
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res2 = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    assert set(res2.progression_complete_work_item_refs) == {"W1", "W2"}
    assert res2.ready_work_item_refs == ("W3",)
    # All complete
    card3, pe3, ve3 = _valid_evidence_sets_for_w("W3", "task-w3")
    res3 = evaluate_milestone_progression(g, (pe1, pe2, pe3), (ve1, ve2, ve3), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1, "task-w2": card2, "task-w3": card3}, {})
    assert set(res3.progression_complete_work_item_refs) == {"W1", "W2", "W3"}
    assert res3.ready_work_item_refs == ()
    assert res3.milestone_review_ready is True
    assert res3.auto_progression_allowed is False


def test_multiple_roots_ready():
    g = _graph(["W1", "W2", "W3", "W4"], [("W1", "W3"), ("W2", "W4")])
    disp = _disp_eligible_no_review()
    res = evaluate_milestone_progression(g, (), (), {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, (), {}, {})
    assert set(res.ready_work_item_refs) == {"W1", "W2"}
    assert "W3" in res.blocked_work_item_refs
    assert "W4" in res.blocked_work_item_refs


def test_disconnected_branch_independence():
    g = _graph(["W1", "W2", "W3", "W4"], [("W1", "W3"), ("W2", "W4")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, (), {"task-w1": card1}, {})
    # W1 complete, W3 should become ready, W2 still ready, W4 still blocked
    assert "W1" in res.progression_complete_work_item_refs
    assert "W2" in res.ready_work_item_refs
    assert "W3" in res.ready_work_item_refs
    assert "W4" in res.blocked_work_item_refs


def test_fork():
    g = _graph(["W1", "W2", "W3"], [("W1", "W2"), ("W1", "W3")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1}, {})
    assert set(res.ready_work_item_refs) == {"W2", "W3"}


def test_join():
    g = _graph(["W1", "W2", "W3"], [("W1", "W3"), ("W2", "W3")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    # Only W1 complete -> W3 not ready
    res1 = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1}, {})
    assert "W3" not in res1.ready_work_item_refs
    assert "W3" in res1.blocked_work_item_refs
    # Both W1,W2 complete -> W3 ready
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res2 = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    assert res2.ready_work_item_refs == ("W3",)


def test_diamond():
    g = _graph(["W1", "W2", "W3", "W4"], [("W1", "W2"), ("W1", "W3"), ("W2", "W4"), ("W3", "W4")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res1 = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, (), {"task-w1": card1}, {})
    assert set(res1.ready_work_item_refs) == {"W2", "W3"}
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res2 = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    assert "W4" not in res2.ready_work_item_refs
    card3, pe3, ve3 = _valid_evidence_sets_for_w("W3", "task-w3")
    res3 = evaluate_milestone_progression(g, (pe1, pe2, pe3), (ve1, ve2, ve3), {"W1": disp, "W2": disp, "W3": disp, "W4": disp}, (), {"task-w1": card1, "task-w2": card2, "task-w3": card3}, {})
    assert res3.ready_work_item_refs == ("W4",)


def test_canonical_ready_set_ordering():
    g = _graph(["W1", "W2", "W3"], [("W1", "W2"), ("W1", "W3")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card1}, {})
    assert res.ready_work_item_refs == ("W2", "W3")  # sorted


# ===========================================================================
# Success gate
# ===========================================================================

def test_success_gate_complete():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ("W1",)
    assert res.milestone_review_ready is True


# ===========================================================================
# Result failures
# ===========================================================================

def test_failure_blocked():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", outcome_success=False)
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs
    assert "W2" in res.blocked_work_item_refs
    assert res.ready_work_item_refs == ()


def test_unknown_blocked():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card = _make_unknown_card("task-w1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr-unknown"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_semantic_stop_blocked():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    # Create card with semantic_stop but outcome must be FAILURE per WorkerResultCard rule (cannot have SUCCESS with stop)
    # So use failure card with semantic_stop
    stop = SemanticStop(reason="SCOPE_AMBIGUOUS", task_ref="task-w1")
    card = WorkerResultCard(
        task_ref="task-w1",
        agent_work_role=AgentWorkRole.CODER,
        summary="stop",
        outcome=ResultOutcome.FAILURE,
        blocking_finding_count=1,
        non_blocking_finding_count=0,
        result_handoff_ref=ResultHandoffRef(ref="task-w1", digest="corr1"),
        primary_evidence_refs=(),
        output_artifact_refs=(),
        semantic_stop=stop,
    )
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    assert "W1" in res.blocked_work_item_refs
    assert "W1" in res.semantic_escalation_required_work_item_refs


def test_mechanical_failure_blocked():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    mf = MechanicalFailure(task_ref="task-w1", error_code="E_TIMEOUT", retryable=False)
    card = WorkerResultCard(
        task_ref="task-w1",
        agent_work_role=AgentWorkRole.CODER,
        summary="mf",
        outcome=ResultOutcome.FAILURE,
        blocking_finding_count=1,
        non_blocking_finding_count=0,
        result_handoff_ref=ResultHandoffRef(ref="task-w1", digest="corr1"),
        primary_evidence_refs=(),
        output_artifact_refs=(),
        mechanical_failure=mf,
    )
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    assert "W1" in res.blocked_work_item_refs


def test_failed_attempt_does_not_become_ready():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", outcome_success=False)
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    # W1 failed, W2 should not be ready, W1 should not be ready
    assert res.ready_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


# ===========================================================================
# Validation
# ===========================================================================

def test_validation_pass_continue():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ("W1",)


def test_validation_fail_blocked():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.FAIL, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_validation_unknown_blocked():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.UNKNOWN, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_validation_missing_incomplete():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_validation_wrong_w_fail_closed():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    # validation for W2 when we have progress for W1 should not satisfy W1
    with pytest.raises((ValueError, TypeError)):
        # This is unknown W evidence? Actually validation for unknown W is fail-closed
        ve = FocusedValidationEvidence(work_item_ref="W99", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
        disp = _disp_eligible_no_review()
        card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
        evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})


# ===========================================================================
# Risk disposition
# ===========================================================================

def test_auto_continuation_false_block():
    g = _graph(["W1"], [])
    disp = _disp_not_eligible()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_semantic_escalation_block():
    g = _graph(["W1"], [])
    disp = _disp_semantic_escalation()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs
    assert "W1" in res.semantic_escalation_required_work_item_refs


def test_unresolved_risk_uncertainty_block():
    g = _graph(["W1"], [])
    env = _envelope()
    disp = evaluate_work_item_risk_policy(envelope=env, review_triggers=(ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY,))
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


# ===========================================================================
# Review satisfaction
# ===========================================================================

def test_formal_review_not_required_no_satisfaction():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    assert disp.formal_review_required is False
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ("W1",)


def test_formal_review_required_with_satisfaction_pass():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    assert disp.formal_review_required is True
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    # Create review result card
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(
        graph=g,
        progress_evidence=(pe,),
        validation_evidence=(ve,),
        review_dispositions={"W1": disp},
        satisfaction_evidence=(sat,),
        worker_cards={"task-w1": card},
        review_result_cards={"review-w1": review_card},
    )
    assert res.progression_complete_work_item_refs == ("W1",)


def test_formal_review_missing_satisfaction_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs
    assert len(res.challenge_routings) == 1
    assert res.challenge_routings[0].work_item_ref == "W1"


def test_review_wrong_w_block():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W2",  # wrong W
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    # This will be considered unknown W? Actually W2 is known, but satisfaction for W2 when we evaluate W1 missing -> W1 blocked
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert "W1" in res.blocked_work_item_refs
    assert "W1" not in res.progression_complete_work_item_refs


def test_review_wrong_disposition_digest_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest="f"*64,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_review_wrong_role_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()  # expects REVIEWER
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.ANALYST, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=ChallengeRole.ANALYST,  # wrong
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


def test_review_wrong_trigger_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    # Use different trigger
    other = ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE if disp.identified_risk != ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE else ReviewTrigger.IRREVERSIBLE_EFFECT
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=other,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


def test_review_wrong_result_ref_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="wrong-ref", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.reconciliation_required_work_item_refs


def test_review_wrong_result_digest_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest="a"*64,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


def test_review_result_failure_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1", outcome_success=False)
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


def test_review_result_unknown_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_unknown_card("review-w1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="corr-unknown"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


def test_review_wrong_agent_role_block():
    g = _graph(["W1"], [])
    disp = _disp_with_review()  # expects reviewer
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.ANALYST, correlation_id="rev1")
    sat = ReviewSatisfactionEvidence(
        work_item_ref="W1",
        review_disposition_digest=disp.digest,
        required_challenge_role=disp.challenge_role,
        review_trigger=disp.identified_risk,
        review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"),
        review_result_digest=review_card.card_digest,
    )
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat,), {"task-w1": card}, {"review-w1": review_card})
    assert res.progression_complete_work_item_refs == ()


# ===========================================================================
# Evidence conflict
# ===========================================================================

def test_identical_duplicate_deterministic_dedupe():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    # duplicate same evidence twice
    res = evaluate_milestone_progression(g, (pe, pe), (ve, ve), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ("W1",)
    assert res.reconciliation_required_work_item_refs == ()


def test_conflicting_progress_reconciliation():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card1 = _make_card("task-w1", correlation_id="corr1", role=AgentWorkRole.CODER)
    card2 = _make_card("task-w1", correlation_id="corr2", role=AgentWorkRole.CODER)
    # Use different digests to simulate conflict
    pe1 = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card1.card_digest)
    pe2 = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr2"), worker_result_digest=card2.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    # Both cards available
    res = evaluate_milestone_progression(g, (pe1, pe2), (ve,), {"W1": disp}, (), {"task-w1": card1, "task-w1-2": card2}, {})
    # But worker_by_ref dedup will keep last, still conflict because progress digests differ -> reconciliation
    assert "W1" in res.reconciliation_required_work_item_refs
    assert res.progression_complete_work_item_refs == ()


def test_conflicting_validation_reconciliation():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, _ = _valid_evidence_sets_for_w("W1", "task-w1")
    ve1 = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    ve2 = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.FAIL, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve1, ve2), {"W1": disp}, (), {"task-w1": card}, {})
    assert "W1" in res.reconciliation_required_work_item_refs


def test_conflicting_satisfaction_reconciliation():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    review_card = _make_card("review-w1", role=AgentWorkRole.REVIEWER, correlation_id="rev1")
    sat1 = ReviewSatisfactionEvidence(work_item_ref="W1", review_disposition_digest=disp.digest, required_challenge_role=disp.challenge_role, review_trigger=disp.identified_risk, review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"), review_result_digest=review_card.card_digest)
    # second satisfaction with different digest (different disposition)
    sat2 = ReviewSatisfactionEvidence(work_item_ref="W1", review_disposition_digest="a"*64, required_challenge_role=disp.challenge_role, review_trigger=disp.identified_risk, review_result_ref=ResultHandoffRef(ref="review-w1", digest="rev1"), review_result_digest=review_card.card_digest)
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (sat1, sat2), {"task-w1": card}, {"review-w1": review_card})
    assert "W1" in res.reconciliation_required_work_item_refs


def test_evidence_ordering_deterministic():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res1 = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    res2 = evaluate_milestone_progression(g, (pe2, pe1), (ve2, ve1), {"W2": disp, "W1": disp}, (), {"task-w2": card2, "task-w1": card1}, {})
    assert res1.digest == res2.digest
    assert res1.progression_complete_work_item_refs == res2.progression_complete_work_item_refs


def test_unknown_w_evidence_fail_closed():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, _, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    pe_unknown = WorkItemProgressEvidence(work_item_ref="W99", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    with pytest.raises(ValueError):
        evaluate_milestone_progression(g, (pe_unknown,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})


# ===========================================================================
# Worker hint attack
# ===========================================================================

def test_next_hint_cannot_skip_dag():
    g = _graph(["W1", "W2", "W3"], [("W1", "W2"), ("W2", "W3")])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1", next_hint="run W3")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp, "W3": disp}, (), {"task-w1": card}, {})
    # W3 should not be ready, only W2
    assert res.ready_work_item_refs == ("W2",)
    assert "W3" not in res.ready_work_item_refs


def test_summary_counts_cannot_change_ready():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    # card has summary, blocking counts etc but they shouldn't affect progression
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp, "W2": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ("W1",)
    assert res.ready_work_item_refs == ("W2",)


# ===========================================================================
# Frontier
# ===========================================================================

def test_matching_expected_frontier_ok():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest, source_frontier_ref=_semantic("frontier:abc"))
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {}, expected_source_frontiers={"W1": "frontier:abc"})
    assert res.progression_complete_work_item_refs == ("W1",)


def test_mismatch_frontier_blocked():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest, source_frontier_ref=_semantic("frontier:abc"))
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {}, expected_source_frontiers={"W1": "frontier:xyz"})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.reconciliation_required_work_item_refs


def test_required_frontier_missing_block():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest, source_frontier_ref=None)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {}, expected_source_frontiers={"W1": "frontier:abc"})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.blocked_work_item_refs


def test_no_expectation_validation_only_allowed():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card.card_digest, source_frontier_ref=None)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {}, expected_source_frontiers=None)
    assert res.progression_complete_work_item_refs == ("W1",)


def test_evaluator_runs_no_git():
    assert PROGRESSION_EVALUATOR_RUNS_GIT is False


# ===========================================================================
# Milestone
# ===========================================================================

def test_all_complete_milestone_review_ready():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    assert res.milestone_review_ready is True
    assert res.ready_work_item_refs == ()


def test_one_unresolved_no_milestone():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe1,), (ve1,), {"W1": disp, "W2": disp}, (), {"task-w1": card1}, {})
    assert res.milestone_review_ready is False


def test_one_challenge_no_milestone():
    g = _graph(["W1"], [])
    disp = _disp_with_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.milestone_review_ready is False


def test_one_semantic_escalation_no_milestone():
    g = _graph(["W1"], [])
    disp = _disp_semantic_escalation()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.milestone_review_ready is False


def test_one_conflict_no_milestone():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card1 = _make_card("task-w1", correlation_id="corr1")
    card2 = _make_card("task-w1", correlation_id="corr2")
    pe1 = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest=card1.card_digest)
    pe2 = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr2"), worker_result_digest=card2.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe1, pe2), (ve,), {"W1": disp}, (), {"task-w1": card1, "task-w1-2": card2}, {})
    assert res.milestone_review_ready is False


# ===========================================================================
# Determinism / Immutability
# ===========================================================================

def test_reorder_determinism_digest():
    g = _graph(["W1", "W2"], [("W1", "W2")])
    disp = _disp_eligible_no_review()
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    res1 = evaluate_milestone_progression(g, (pe1, pe2), (ve1, ve2), {"W1": disp, "W2": disp}, (), {"task-w1": card1, "task-w2": card2}, {})
    res2 = evaluate_milestone_progression(g, (pe2, pe1), (ve2, ve1), {"W2": disp, "W1": disp}, (), {"task-w2": card2, "task-w1": card1}, {})
    assert res1.digest == res2.digest
    assert res1.canonical_json() == res2.canonical_json()


def test_progression_disposition_immutable():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    with pytest.raises((AttributeError, TypeError)):
        res.milestone_review_ready = True  # type: ignore


# ===========================================================================
# Authority firewall
# ===========================================================================

def test_progression_disposition_not_operation_authority():
    assert PROGRESSION_DISPOSITION_IS_OPERATION_AUTHORITY is False
    assert PROGRESSION_DISPOSITION_IS_PLAN_AUTHORITY is False
    assert PROGRESSION_DISPOSITION_IS_WORK_ITEM_ACCEPTANCE is False
    assert PROGRESSION_DISPOSITION_IS_MILESTONE_ACCEPTANCE is False
    assert AUTO_PROGRESSION_ALLOWED_IS_OPERATION_AUTHORITY is False

    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.is_operation_authority is False
    assert res.is_plan_authority is False
    assert res.is_milestone_acceptance is False


def test_s2_authority_bypass_not_created():
    from aota_forge.work_plane.workspace_tools import BoundedWorkspaceToolProvider
    from aota_forge.work_plane.test_execution import BoundedTestExecutionToolProvider
    from aota_forge.work_plane.git_tools import BoundedGitToolProvider
    from aota_forge.work_plane.restricted_shell import BoundedRestrictedShellProvider

    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    for obj in [res, pe, ve, g]:
        with pytest.raises(Exception):
            BoundedWorkspaceToolProvider(authority=obj)  # type: ignore
        with pytest.raises(Exception):
            BoundedTestExecutionToolProvider(authority=obj)  # type: ignore
        with pytest.raises(Exception):
            BoundedGitToolProvider(authority=obj)  # type: ignore
        with pytest.raises(Exception):
            BoundedRestrictedShellProvider(authority=obj)  # type: ignore
    # Per-work routing also not authority
    assert WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY is False
    assert WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY is False


def test_no_scheduler_or_engine():
    assert M2_AUTO_RETRY_ENGINE_CREATED is False
    assert M2_REPAIR_LOOP_CREATED is False
    import aota_forge.work_plane.progression as prog
    assert not hasattr(prog, "run_RV1")
    assert not hasattr(prog, "retry_counter")


def test_worker_result_binding_required():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe_wrong_digest = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="task-w1", digest="corr1"), worker_result_digest="f"*64)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe_wrong_digest,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.reconciliation_required_work_item_refs


def test_worker_result_ref_mismatch_reconciliation():
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card = _make_card("task-w1", correlation_id="corr1")
    pe = WorkItemProgressEvidence(work_item_ref="W1", worker_result_ref=ResultHandoffRef(ref="other-ref", digest="corr1"), worker_result_digest=card.card_digest)
    ve = FocusedValidationEvidence(work_item_ref="W1", verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=_semantic("val:1"))
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.progression_complete_work_item_refs == ()
    assert "W1" in res.reconciliation_required_work_item_refs


def test_milestone_acceptance_not_auto():
    assert AUTOMATIC_MILESTONE_APPROVAL is False
    assert AUTOMATIC_MILESTONE_CLOSURE is False
    g = _graph(["W1"], [])
    disp = _disp_eligible_no_review()
    card, pe, ve = _valid_evidence_sets_for_w("W1", "task-w1")
    res = evaluate_milestone_progression(g, (pe,), (ve,), {"W1": disp}, (), {"task-w1": card}, {})
    assert res.milestone_review_ready is True
    assert res.is_milestone_acceptance is False


def test_progression_derived_from_evidence_stateless():
    assert PROGRESSION_DERIVED_FROM_EVIDENCE is True
    assert PROGRESSION_EVALUATOR_STATELESS is True


def test_per_w_challenge_routing_independent():
    # Fork with different challenge roles - test that blocked W with missing satisfaction produce per-W routing
    g = _graph(["W1", "W2", "W3"], [("W1", "W2"), ("W1", "W3")])
    disp_no = _disp_eligible_no_review()
    env = _envelope()
    disp_w2 = evaluate_work_item_risk_policy(envelope=env, review_triggers=(ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE,))  # analyst
    disp_w3 = evaluate_work_item_risk_policy(envelope=env, review_triggers=(ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY,))  # reviewer
    card1, pe1, ve1 = _valid_evidence_sets_for_w("W1", "task-w1")
    # Create progress for W2 and W3 but missing satisfaction -> they should be blocked with routing, not ready
    card2, pe2, ve2 = _valid_evidence_sets_for_w("W2", "task-w2")
    card3, pe3, ve3 = _valid_evidence_sets_for_w("W3", "task-w3")
    res = evaluate_milestone_progression(g, (pe1, pe2, pe3), (ve1, ve2, ve3), {"W1": disp_no, "W2": disp_w2, "W3": disp_w3}, (), {"task-w1": card1, "task-w2": card2, "task-w3": card3}, {})
    # W1 complete, W2,W3 have progress but missing review -> blocked, not ready
    assert set(res.ready_work_item_refs) == set()
    assert "W2" in res.blocked_work_item_refs
    assert "W3" in res.blocked_work_item_refs
    refs = {cr.work_item_ref for cr in res.challenge_routings}
    assert "W2" in refs
    assert "W3" in refs
    role_map = {cr.work_item_ref: cr.challenge_role for cr in res.challenge_routings}
    assert role_map["W2"] == ChallengeRole.ANALYST
    assert role_map["W3"] == ChallengeRole.REVIEWER
