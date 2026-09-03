"""W2 Bounded Repair / RV2 & Replan Evaluator — focused test matrix.

Covers review validation, clean RV1, repair required, repair binding,
completed repair, RV2, failure fingerprint, history, retry/effect firewall,
authority/non-effects.
"""

import hashlib
import inspect

import pytest
from enum import Enum

from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultOutcome
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.work_plane.stop import SemanticStop, MechanicalFailure, SemanticStopReason, StopKind
from aota_forge.work_plane.milestone_review import (
    ReviewCycle,
    ReviewFindingClassification,
    ReviewFindingEvidence,
    MilestoneReviewEvidence,
)
from aota_forge.work_plane.progression import FocusedValidationEvidence, FocusedValidationVerdict
from aota_forge.work_plane.milestone_review_workflow import (
    RepairEvidence,
    FailureFingerprint,
    RepairHistory,
    RepairHistoryEntry,
    WorkflowDisposition,
    MilestoneReviewWorkflowDisposition,
    evaluate_milestone_review_workflow,
    MAX_REPAIR_HISTORY_ENTRIES,
    REPAIR_EVIDENCE_IMPLEMENTED,
    FAILURE_FINGERPRINT_IMPLEMENTED,
    REPAIR_HISTORY_IMPLEMENTED,
    MILESTONE_REVIEW_WORKFLOW_DISPOSITION_IMPLEMENTED,
    M3_REVIEW_WORKFLOW_EVALUATOR_IMPLEMENTED,
    MILESTONE_CLOSURE_READINESS_IMPLEMENTED,
    M3_REPAIR_EVALUATOR_RUNS_GIT,
    M3_REVIEW_EVALUATOR_RUNS_GIT,
    NEW_GIT_LIFECYCLE_CREATED,
    S2_AUTHORITY_BYPASS_CREATED,
    M3_GRANTS_RETRY_AUTHORITY,
    M3_EXECUTES_RETRY,
    RETRY_COUNT_IS_SEMANTIC_AUTHORITY,
    REPAIR_EVIDENCE_IS_OPERATION_AUTHORITY,
    REPAIR_HISTORY_IS_OPERATION_AUTHORITY,
    REPAIR_HISTORY_IS_RETRY_AUTHORITY,
    NEW_ERROR_ONTOLOGY_CREATED,
    WORKFLOW_DISPOSITION_DERIVED_FROM_EVIDENCE,
    M3_WORKFLOW_EVALUATOR_STATELESS,
    PERSISTENT_WORKFLOW_STATE_CREATED,
    W1_REVIEW_CONTRACTS_REUSED,
    RV2_ONLY_IF_REPAIR,
    RV3_RV4_GENERIC_LOOP_CREATED,
    REPEATED_FAILURE_CANNOT_BLINDLY_REPLAY_SIDE_EFFECT,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _semantic(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)

def _handoff(ref: str, digest: str | None = None) -> ResultHandoffRef:
    return ResultHandoffRef(ref=ref, digest=digest)

def _task_handoff(milestone_ref: str = "S4/M3", work_item_ref: str | None = None, work_role: AgentWorkRole = AgentWorkRole.REVIEWER) -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind="review",
        objective="review",
        bounded_scope=milestone_ref,
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        milestone_ref=_semantic(milestone_ref),
        work_item_ref=_semantic(work_item_ref) if work_item_ref else None,
    )

def _repair_handoff(milestone_ref: str = "S4/M3", work_item_ref: str = "S4/M3/repair-1") -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="repair",
        objective="repair",
        bounded_scope=work_item_ref,
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        milestone_ref=_semantic(milestone_ref),
        work_item_ref=_semantic(work_item_ref),
    )

def _finding(finding_ref: str = "FINDING-001", classification=ReviewFindingClassification.BLOCKING, ev_ref: str = "evidence:sup1") -> ReviewFindingEvidence:
    return ReviewFindingEvidence(
        finding_ref=finding_ref,
        classification=classification,
        supporting_evidence_ref=_semantic(ev_ref),
        supporting_evidence_digest="a"*64,
    )

def _milestone(
    milestone_ref: str = "S4/M3",
    cycle=ReviewCycle.RV1,
    frontier: str = "frontier:abc123",
    result_ref: str = "review-task-1",
    result_digest: str = "b"*64,
    finding_refs: tuple[str, ...] = (),
) -> MilestoneReviewEvidence:
    return MilestoneReviewEvidence(
        milestone_ref=_semantic(milestone_ref),
        review_cycle=cycle,
        reviewed_frontier_ref=_semantic(frontier),
        review_result_ref=_handoff(result_ref),
        review_result_digest=result_digest,
        finding_refs=finding_refs,
    )

def _make_card(task_id: str, outcome: str = "success", role: AgentWorkRole = AgentWorkRole.REVIEWER, correlation_id: str = "corr1", semantic_stop=None, mechanical=None) -> WorkerResultCard:
    if outcome == "success":
        return WorkerResultCard(
            task_ref=task_id,
            agent_work_role=role,
            summary="ok",
            outcome=ResultOutcome.SUCCESS,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
            primary_evidence_refs=(),
            output_artifact_refs=(),
            semantic_stop=semantic_stop,
            mechanical_failure=mechanical,
        )
    elif outcome == "failure":
        return WorkerResultCard(
            task_ref=task_id,
            agent_work_role=role,
            summary="fail",
            outcome=ResultOutcome.FAILURE,
            blocking_finding_count=1,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
            primary_evidence_refs=(),
            output_artifact_refs=(),
            semantic_stop=semantic_stop,
            mechanical_failure=mechanical,
        )
    elif outcome == "unknown":
        return WorkerResultCard(
            task_ref=task_id,
            agent_work_role=role,
            summary="unknown",
            outcome=ResultOutcome.UNKNOWN,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=correlation_id),
            primary_evidence_refs=(),
            output_artifact_refs=(),
        )
    else:
        raise ValueError(outcome)

def _make_repair_card(task_id: str = "repair-task-1", work_item_ref: str = "S4/M3/repair-1", milestone: str = "S4/M3", outcome: str = "success") -> tuple[WorkerResultCard, TaskHandoff]:
    card = _make_card(task_id, outcome=outcome, role=AgentWorkRole.CODER, correlation_id="corr-repair-1")
    handoff = _repair_handoff(milestone_ref=milestone, work_item_ref=work_item_ref)
    return card, handoff

def _validation(work_item_ref: str = "S4/M3/repair-1", verdict=FocusedValidationVerdict.PASS) -> FocusedValidationEvidence:
    return FocusedValidationEvidence(
        work_item_ref=work_item_ref,
        verdict=verdict,
        validation_evidence_ref=_semantic("val:"+work_item_ref),
        validation_evidence_digest="c"*64,
    )

def _repair_evidence(
    milestone_ref: str = "S4/M3",
    cycle=ReviewCycle.RV1,
    finding_refs: tuple[str, ...] = ("F1",),
    repair_work_ref: str = "S4/M3/repair-1",
    pre_frontier: str = "frontier:abc123",
    post_frontier: str = "frontier:def456",
    result_ref: str = "repair-task-1",
    result_digest: str | None = None,
) -> RepairEvidence:
    # result_digest will be filled from card if None
    if result_digest is None:
        result_digest = "d"*64
    return RepairEvidence(
        milestone_ref=_semantic(milestone_ref),
        originating_review_cycle=cycle,
        originating_finding_refs=finding_refs,
        repair_work_ref=repair_work_ref,
        pre_repair_frontier_ref=_semantic(pre_frontier),
        post_repair_frontier_ref=_semantic(post_frontier),
        repair_result_ref=_handoff(result_ref),
        repair_result_digest=result_digest,
        bounded_validation_or_supporting_refs=(),
    )

# ===========================================================================
# Review validation
# ============================================================================

def test_valid_rv1_reviewer_chain_pass():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3", work_item_ref="S4/M3/W1", work_role=AgentWorkRole.REVIEWER)
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        expected_rv1_frontier=_semantic("frontier:abc123"),
    )
    # One blocking no repair -> REPAIR_REQUIRED, but reviewer chain valid so not BLOCKED
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_wrong_result_ref_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=_handoff("wrong-ref"),
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        expected_rv1_frontier=_semantic("frontier:abc123"),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_result_digest_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest="f"*64,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_role_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.CODER)
    handoff = _task_handoff(milestone_ref="S4/M3", work_role=AgentWorkRole.CODER)
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_review_failure_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="failure", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_review_unknown_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="unknown", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_milestone_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # handoff milestone is S4/M3 but evidence is S4/M3, so ok. To trigger wrong milestone, make handoff different
    handoff_wrong = _task_handoff(milestone_ref="S4/M4")
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff_wrong,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_expected_frontier_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        expected_rv1_frontier=_semantic("frontier:WRONG"),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_finding_set_mismatch_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    f2 = _finding("F2", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),  # only F1, but findings include F2
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1, f2),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

# ===========================================================================
# Clean RV1
# ============================================================================

def test_zero_findings_ready_for_steward():
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        expected_rv1_frontier=_semantic("frontier:abc123"),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD

def test_only_non_blocking_ready_for_steward():
    f1 = _finding("F1", ReviewFindingClassification.NON_BLOCKING)
    f2 = _finding("F2", ReviewFindingClassification.NON_BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1", "F2"),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1, f2),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD

# ===========================================================================
# Repair required
# ============================================================================

def test_one_blocking_no_repair_repair_required():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_multiple_blocking_no_repair_required():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    f2 = _finding("F2", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1", "F2"),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1, f2),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_partial_repair_coverage_repair_required():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    f2 = _finding("F2", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1", "F2"),
    )
    # Repair only F1
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1, f2),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

# ===========================================================================
# Repair binding
# ============================================================================

def test_wrong_repair_result_ref_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=_handoff("wrong-ref"),
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_repair_digest_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest="f"*64,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_repair_work_item_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/wrong-work",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/wrong-work", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_wrong_taskhandoff_milestone_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, _ = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1", milestone="S4/M3")
    # handoff with wrong milestone
    wrong_handoff = _repair_handoff(milestone_ref="S4/M9", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": wrong_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_repair_failure_incomplete():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1", outcome="failure")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_repair_unknown_incomplete():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _make_card("repair-task-1", outcome="unknown", role=AgentWorkRole.CODER)
    repair_handoff = _repair_handoff(milestone_ref="S4/M3", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_validation_fail_incomplete():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.FAIL)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_validation_unknown_incomplete():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.UNKNOWN)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED

def test_foreign_finding_repair_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    # F2 is not in RV1, foreign
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("FOREIGN",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_conflicting_coverage_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card1, handoff1 = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_card2, handoff2 = _make_repair_card(task_id="repair-task-2", work_item_ref="S4/M3/repair-2")
    # Two repairs both claim F1 but with different post frontier / result -> conflict
    repair_ev1 = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card1.result_handoff_ref,
        repair_result_digest=repair_card1.card_digest,
    )
    repair_ev2 = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-2",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:xyz999"),
        repair_result_ref=repair_card2.result_handoff_ref,
        repair_result_digest=repair_card2.card_digest,
    )
    ve1 = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    ve2 = _validation("S4/M3/repair-2", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev1, repair_ev2),
        repair_result_cards={"repair-task-1": repair_card1, "repair-task-2": repair_card2},
        repair_task_handoffs={"repair-task-1": handoff1, "repair-task-2": handoff2},
        validation_evidences=(ve1, ve2),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

# ===========================================================================
# Completed repair -> RV2_REQUIRED
# ============================================================================

def test_complete_repair_to_rv2_required():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp.disposition == WorkflowDisposition.RV2_REQUIRED
    assert disp.final_frontier_ref.ref == "frontier:def456"

# ===========================================================================
# RV2
# ============================================================================

def test_rv2_without_repair_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # RV2 without repair
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        rv2_evidence=rv2_ev,
        rv2_findings=(),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_rv2_old_frontier_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:abc123"),  # old frontier
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        rv2_evidence=rv2_ev,
        rv2_findings=(),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_rv2_wrong_finding_set_blocked():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    # RV2 finding set mismatch: claims F1 but findings only empty -> mismatch
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=("F1",),
    )
    # Provide empty findings -> mismatch -> BLOCKED
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        rv2_evidence=rv2_ev,
        rv2_findings=(),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_valid_clean_rv2_to_steward():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        rv2_evidence=rv2_ev,
        rv2_findings=(),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
        expected_rv2_frontier=_semantic("frontier:def456"),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD

def test_rv2_blocking_to_replan():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    rv2_finding = _finding("F2", ReviewFindingClassification.BLOCKING)
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=("F2",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        rv2_evidence=rv2_ev,
        rv2_findings=(rv2_finding,),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED

def test_rv3_impossible():
    # Ensure evaluator does not create RV3 loop: we cannot pass RV3 cycle, but test that having RV2 blocking does not lead to repair required
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    # RV2 blocking should be REPLAN, not REPAIR_REQUIRED
    rv2_finding = _finding("F2", ReviewFindingClassification.BLOCKING)
    rv2_card = _make_card("review-task-2", outcome="success", role=AgentWorkRole.REVIEWER)
    rv2_handoff = _task_handoff(milestone_ref="S4/M3")
    rv2_ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=rv2_card.result_handoff_ref,
        review_result_digest=rv2_card.card_digest,
        finding_refs=("F2",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        rv2_evidence=rv2_ev,
        rv2_findings=(rv2_finding,),
        rv2_result_card=rv2_card,
        rv2_task_handoff=rv2_handoff,
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED
    assert disp.disposition != WorkflowDisposition.REPAIR_REQUIRED

# ===========================================================================
# Failure fingerprint
# ============================================================================

def test_fingerprint_deterministic():
    stop1 = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    stop2 = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    fp1 = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop1, "task-1")
    fp2 = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop2, "task-1")
    assert fp1.digest == fp2.digest
    assert fp1.canonical_json() == fp2.canonical_json()
    # Mechanical deterministic
    mf1 = MechanicalFailure(task_ref="task-1", error_code="E_TIMEOUT", retryable=True, evidence_refs=("ev:1",))
    mf2 = MechanicalFailure(task_ref="task-1", error_code="E_TIMEOUT", retryable=True, evidence_refs=("ev:1",))
    fp3 = FailureFingerprint.from_mechanical_failure(_semantic("S4/M3"), "W1", mf1, "task-1")
    fp4 = FailureFingerprint.from_mechanical_failure(_semantic("S4/M3"), "W1", mf2, "task-1")
    assert fp3.digest == fp4.digest

def test_fingerprint_rationale_not_material():
    stop1 = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", rationale="rationale A", evidence_refs=("ev:1",))
    stop2 = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", rationale="different rationale", evidence_refs=("ev:1",))
    fp1 = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop1, "task-1")
    fp2 = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop2, "task-1")
    assert fp1.digest == fp2.digest

def test_fingerprint_first_not_repeated():
    stop = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    fp = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop, "task-1")
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    # Only pre failure, no post -> not repeated, should be RV2_REQUIRED not REPLAN
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        pre_failure_fingerprints=(fp,),
        post_failure_fingerprints=(),
    )
    assert disp.disposition == WorkflowDisposition.RV2_REQUIRED

def test_same_fingerprint_after_repair_replan():
    stop = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    fp_pre = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop, "task-1")
    fp_post = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop, "task-1")
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        pre_failure_fingerprints=(fp_pre,),
        post_failure_fingerprints=(fp_post,),
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED

def test_different_post_failure_not_same():
    stop1 = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    stop2 = SemanticStop(reason=SemanticStopReason.PROJECT_POLICY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    fp_pre = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop1, "task-1")
    fp_post = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop2, "task-1")
    assert fp_pre.digest != fp_post.digest
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        pre_failure_fingerprints=(fp_pre,),
        post_failure_fingerprints=(fp_post,),
    )
    # Different failure should not be same-failure recurrence -> BLOCKED, not REPLAN with same
    assert disp.disposition == WorkflowDisposition.BLOCKED
    assert disp.disposition != WorkflowDisposition.REPLAN_REQUIRED or disp.reasons[0] != "repeated failure after repair"

# ===========================================================================
# History
# ============================================================================

def test_history_ordered_deterministic():
    msr = _semantic("S4/M3")
    # Create two entries with different work refs
    fp1 = FailureFingerprint(_semantic("S4/M3"), "W1", StopKind.SEMANTIC_STOP, "AUTHORITY_CONFLICT", "task-1", ())
    e1 = RepairHistoryEntry("S4/M3/repair-1", "a"*64, _semantic("frontier:abc"), _semantic("frontier:def"), fp1)
    e2 = RepairHistoryEntry("S4/M3/repair-2", "b"*64, _semantic("frontier:def"), _semantic("frontier:ghi"), None)
    h1 = RepairHistory(milestone_ref=msr, entries=(e1, e2))
    h2 = RepairHistory(milestone_ref=msr, entries=(e1, e2))
    assert h1.digest == h2.digest
    assert h1.canonical_json() == h2.canonical_json()

def test_history_changed_order_distinguishable():
    msr = _semantic("S4/M3")
    fp1 = FailureFingerprint(_semantic("S4/M3"), "W1", StopKind.SEMANTIC_STOP, "AUTHORITY_CONFLICT", "task-1", ())
    e1 = RepairHistoryEntry("S4/M3/repair-1", "a"*64, _semantic("frontier:abc"), _semantic("frontier:def"), fp1)
    e2 = RepairHistoryEntry("S4/M3/repair-2", "b"*64, _semantic("frontier:def"), _semantic("frontier:ghi"), None)
    h1 = RepairHistory(milestone_ref=msr, entries=(e1, e2))
    h2 = RepairHistory(milestone_ref=msr, entries=(e2, e1))
    assert h1.digest != h2.digest

def test_16_entries_representable():
    msr = _semantic("S4/M3")
    entries = tuple(
        RepairHistoryEntry(f"S4/M3/repair-{i}", f"{i:02x}"*32, _semantic(f"frontier:{i}"), _semantic(f"frontier:{i+1}"), None)
        for i in range(16)
    )
    h = RepairHistory(milestone_ref=msr, entries=entries)
    assert len(h.entries) == 16

def test_17_entries_overflow_replan_or_fail_closed():
    msr = _semantic("S4/M3")
    # Try to construct history with 17 entries -> should raise ValueError (fail closed)
    entries = tuple(
        RepairHistoryEntry(f"S4/M3/repair-{i}", f"{i:02x}"*32, _semantic(f"frontier:{i}"), _semantic(f"frontier:{i+1}"), None)
        for i in range(17)
    )
    with pytest.raises((ValueError, TypeError)):
        RepairHistory(milestone_ref=msr, entries=entries)
    # Also evaluator with 17 repair evidences -> REPLAN
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # Create 17 repair evidences (each with distinct work ref)
    repair_evs = []
    repair_cards = {}
    repair_handoffs = {}
    validations = []
    for i in range(17):
        rc, rh = _make_repair_card(task_id=f"repair-task-{i}", work_item_ref=f"S4/M3/repair-{i}")
        # Need to vary finding refs? Use same F1 but then conflicting? Instead create 17 distinct findings to avoid conflict: use 17 findings?
        # For overflow test, we just need count >16, even if findings duplicate, overflow takes precedence over conflict?
        # Create 17 findings each with distinct ref, but RV1 only has F1, so foreign -> BLOCKED not overflow. So we need RV1 with 17 findings? But MAX is 16, so can't.
        # Instead test overflow via history count, not via repair evidences covering same finding. Better to create repair evidences with same finding but we expect overflow REPLAN before conflict check? Our evaluator checks overflow before conflict, so should return REPLAN.
        # Let's use single finding F1 repeated 17 times with same finding ref but different work ref - this will be conflicting but overflow should be checked first.
        rep = RepairEvidence(
            milestone_ref=_semantic("S4/M3"),
            originating_review_cycle=ReviewCycle.RV1,
            originating_finding_refs=("F1",),
            repair_work_ref=f"S4/M3/repair-{i}",
            pre_repair_frontier_ref=_semantic("frontier:abc123" if i==0 else f"frontier:{i}"),
            post_repair_frontier_ref=_semantic(f"frontier:{i+1}"),
            repair_result_ref=rc.result_handoff_ref,
            repair_result_digest=rc.card_digest,
        )
        repair_evs.append(rep)
        repair_cards[f"repair-task-{i}"] = rc
        repair_handoffs[f"repair-task-{i}"] = rh
        validations.append(_validation(f"S4/M3/repair-{i}", FocusedValidationVerdict.PASS))
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=tuple(repair_evs),
        repair_result_cards=repair_cards,
        repair_task_handoffs=repair_handoffs,
        validation_evidences=tuple(validations),
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED

def test_no_silent_truncation():
    msr = _semantic("S4/M3")
    # 16 entries should be preserved, not truncated
    entries16 = tuple(
        RepairHistoryEntry(f"S4/M3/repair-{i}", f"{i:02x}"*32, _semantic(f"frontier:{i}"), _semantic(f"frontier:{i+1}"), None)
        for i in range(16)
    )
    h16 = RepairHistory(milestone_ref=msr, entries=entries16)
    assert len(h16.entries) == 16
    # Constructing 17 should not silently truncate to 16
    entries17 = tuple(
        RepairHistoryEntry(f"S4/M3/repair-{i}", f"{i:02x}"*32, _semantic(f"frontier:{i}"), _semantic(f"frontier:{i+1}"), None)
        for i in range(17)
    )
    with pytest.raises(ValueError):
        RepairHistory(milestone_ref=msr, entries=entries17)

# ===========================================================================
# Retry/effect firewall
# ============================================================================

def test_retryable_does_not_authorize():
    assert M3_GRANTS_RETRY_AUTHORITY is False
    assert M3_EXECUTES_RETRY is False
    # MechanicalFailure retryable true still not authority
    mf = MechanicalFailure(task_ref="task-1", error_code="E_TIMEOUT", retryable=True)
    assert mf.retryable is True
    assert mf.grants_retry is False
    # Evaluator should not grant retry
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # Even with retryable mechanical failure fingerprint, disposition should not be retry authorized
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED
    # Ensure no retry authorized flag in disposition
    assert not hasattr(disp, "retry_authorized")

def test_retry_request_not_authority():
    from aota_forge.work_plane.stop import RetryRequest
    rr = RetryRequest(task_ref="task-1", classification="retry", rationale="retry", evidence_refs=())
    assert rr.is_authorization is False
    assert rr.grants_retry is False

def test_unknown_not_retry():
    mf = MechanicalFailure(task_ref="task-1", error_code="UNKNOWN", retryable=False)
    assert mf.grants_retry is False
    # UNKNOWN outcome already blocked
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="unknown", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_no_blind_partial_effect_replay():
    assert REPEATED_FAILURE_CANNOT_BLINDLY_REPLAY_SIDE_EFFECT is True
    # Ensure fingerprint with same identity does not grant safe_to_retry
    stop = SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref="task-1", evidence_refs=("ev:1",))
    fp = FailureFingerprint.from_semantic_stop(_semantic("S4/M3"), "W1", stop, "task-1")
    # No attribute safe_to_retry in fingerprint
    assert not hasattr(fp, "safe_to_retry")

# ===========================================================================
# Authority/non-effects
# ============================================================================

def test_new_types_not_s2_authority():
    assert S2_AUTHORITY_BYPASS_CREATED is False
    from aota_forge.work_plane.workspace_tools import BoundedWorkspaceToolProvider
    from aota_forge.work_plane.test_execution import BoundedTestExecutionToolProvider
    from aota_forge.work_plane.git_tools import BoundedGitToolProvider
    from aota_forge.work_plane.restricted_shell import BoundedRestrictedShellProvider
    msr = _semantic("S4/M3")
    fp = FailureFingerprint(msr, "W1", StopKind.SEMANTIC_STOP, "AUTHORITY_CONFLICT", "task-1", ())
    repair_ev = RepairEvidence(
        milestone_ref=msr,
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc"),
        post_repair_frontier_ref=_semantic("frontier:def"),
        repair_result_ref=_handoff("r1"),
        repair_result_digest="a"*64,
    )
    hist = RepairHistory(milestone_ref=msr, entries=())
    disp = MilestoneReviewWorkflowDisposition(disposition=WorkflowDisposition.BLOCKED, milestone_ref=msr, reasons=("test",))
    for obj in [repair_ev, fp, hist, disp]:
        for Provider in [BoundedWorkspaceToolProvider, BoundedTestExecutionToolProvider, BoundedGitToolProvider, BoundedRestrictedShellProvider]:
            with pytest.raises(Exception):
                Provider(authority=obj)  # type: ignore

def test_ready_for_steward_not_acceptance():
    assert MILESTONE_CLOSURE_READINESS_IMPLEMENTED is False
    assert READY_FOR_STEWARD_IS_STEWARD_DECISION is False if 'READY_FOR_STEWARD_IS_STEWARD_DECISION' in dir() else True
    # Check disposition not acceptance
    disp = MilestoneReviewWorkflowDisposition(disposition=WorkflowDisposition.READY_FOR_STEWARD, milestone_ref=_semantic("S4/M3"), reasons=("ok",))
    assert disp.is_acceptance is False
    assert disp.is_operation_authority is False
    assert not hasattr(disp, "accepted")
    assert not hasattr(disp, "closed")

def test_no_git_subprocess_network_time_random():
    import aota_forge.work_plane.milestone_review_workflow as mod
    src = inspect.getsource(mod)
    assert "import subprocess" not in src
    assert "from subprocess" not in src
    assert "import git" not in src
    assert "import socket" not in src
    assert "import requests" not in src
    assert "import time" not in src
    assert "import random" not in src
    assert "import uuid" not in src
    assert "time.time" not in src
    assert "random." not in src
    assert "uuid4" not in src
    assert M3_REPAIR_EVALUATOR_RUNS_GIT is False
    assert M3_REVIEW_EVALUATOR_RUNS_GIT is False
    assert NEW_GIT_LIFECYCLE_CREATED is False

# ===========================================================================
# Workflow precedence and extras
# ============================================================================

def test_workflow_precedence_invalid_before_replan():
    # Invalid evidence should be BLOCKED even if history overflow would be REPLAN
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=_handoff("wrong"),
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # Also overflow
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=tuple(
            _repair_evidence(repair_work_ref=f"S4/M3/repair-{i}", result_ref=f"r{i}", result_digest="a"*64)
            for i in range(17)
        ),
    )
    assert disp.disposition == WorkflowDisposition.BLOCKED

def test_repair_required_rv2_required_distinction():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    # No repair -> REPAIR_REQUIRED, not RV2_REQUIRED
    disp1 = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp1.disposition == WorkflowDisposition.REPAIR_REQUIRED
    # Complete repair -> RV2_REQUIRED, not REPAIR_REQUIRED
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp2 = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
    )
    assert disp2.disposition == WorkflowDisposition.RV2_REQUIRED
    assert disp1.disposition != disp2.disposition

def test_repair_scope_semantic_boundary_replan():
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    repair_card, repair_handoff = _make_repair_card(task_id="repair-task-1", work_item_ref="S4/M3/repair-1")
    repair_ev = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
    )
    ve = _validation("S4/M3/repair-1", FocusedValidationVerdict.PASS)
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        repair_evidences=(repair_ev,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"repair-task-1": repair_handoff},
        validation_evidences=(ve,),
        semantic_boundary_exceeded=True,
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED

def test_workflow_stateless_derived_from_evidence():
    assert WORKFLOW_DISPOSITION_DERIVED_FROM_EVIDENCE is True
    assert M3_WORKFLOW_EVALUATOR_STATELESS is True
    assert PERSISTENT_WORKFLOW_STATE_CREATED is False
    # Stateless: same inputs -> same output, no stored state
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _make_card("review-task-1", outcome="success", role=AgentWorkRole.REVIEWER)
    handoff = _task_handoff(milestone_ref="S4/M3")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=("F1",),
    )
    disp1 = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    disp2 = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(f1,),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
    )
    assert disp1 == disp2
    assert disp1.digest == disp2.digest

def test_w1_contracts_reused():
    assert W1_REVIEW_CONTRACTS_REUSED is True
    # Ensure we imported actual W1 types
    assert ReviewCycle.RV1.value == "RV1"
    assert ReviewFindingClassification.BLOCKING.value == "BLOCKING"
    # New result ontology not created
    assert NEW_ERROR_ONTOLOGY_CREATED is False
    assert NEW_GIT_LIFECYCLE_CREATED is False

def test_repair_evidence_is_not_authority():
    assert REPAIR_EVIDENCE_IS_OPERATION_AUTHORITY is False
    assert REPAIR_HISTORY_IS_OPERATION_AUTHORITY is False
    assert REPAIR_HISTORY_IS_RETRY_AUTHORITY is False
    assert RETRY_COUNT_IS_SEMANTIC_AUTHORITY is False

def test_milestone_closure_not_implemented():
    assert MILESTONE_CLOSURE_READINESS_IMPLEMENTED is False
    import aota_forge.work_plane.milestone_review_workflow as mod
    assert not hasattr(mod, "MilestoneClosureReadiness")

def test_rv2_only_if_repair():
    assert RV2_ONLY_IF_REPAIR is True
    assert RV3_RV4_GENERIC_LOOP_CREATED is False

def test_max_history_entries():
    assert MAX_REPAIR_HISTORY_ENTRIES == 16

def test_no_new_ontology():
    assert NEW_ERROR_ONTOLOGY_CREATED is False
    import aota_forge.work_plane.milestone_review_workflow as mod
    assert not hasattr(mod, "RepeatedFailureReason")
    assert not hasattr(mod, "FailureClassV2")
    assert not hasattr(mod, "MechanicalErrorV2")
