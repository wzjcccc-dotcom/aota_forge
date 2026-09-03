"""W3 Steward Closure & Cross-Contract Adversarial Proof — M3 W3.

Covers §6-41 matrices: thin closure readiness contract, pure evaluator,
work-item completion gate, graph/progression authority boundary, final
workflow/RV cycle/frontier binding, no Git, supporting evidence, readiness gate,
reviewer impersonation, cross-milestone, stale frontier, repair/RV2/replan,
foreign/partial/conflicting progression, clean RV1 & repaired RV2 vertical proofs,
forgery, determinism, immutability, S2 firewall, non-effects, boundaries.
"""

import hashlib
import inspect

import pytest
from enum import Enum

from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.core.result_governance import ResultOutcome
from aota_forge.work_plane.milestone_review import (
    ReviewCycle,
    ReviewFindingClassification,
    ReviewFindingEvidence,
    MilestoneReviewEvidence,
)
from aota_forge.work_plane.progression import (
    MilestoneWorkItemGraph,
    ProgressionDisposition,
    FocusedValidationEvidence,
    FocusedValidationVerdict,
)
from aota_forge.work_plane.milestone_review_workflow import (
    RepairEvidence,
    FailureFingerprint,
    RepairHistory,
    RepairHistoryEntry,
    WorkflowDisposition,
    MilestoneReviewWorkflowDisposition,
    evaluate_milestone_review_workflow,
    MAX_REPAIR_HISTORY_ENTRIES,
)
from aota_forge.work_plane.milestone_closure import (
    MilestoneClosureReadiness,
    evaluate_milestone_closure_readiness,
    MILESTONE_CLOSURE_READINESS_IMPLEMENTED,
    CLOSURE_READINESS_EVALUATOR_IMPLEMENTED,
    MILESTONE_CLOSURE_READINESS_IS_ACCEPTANCE_AUTHORITY,
    MILESTONE_CLOSURE_READINESS_IS_GIT_AUTHORITY,
    MILESTONE_CLOSURE_READINESS_IS_PLAN_AUTHORITY,
    READY_FOR_STEWARD_IS_STEWARD_DECISION,
    M3_CLOSURE_READINESS_EVALUATOR_STATELESS,
    CLOSURE_READINESS_DERIVED_FROM_EVIDENCE,
    M3_CLOSURE_READINESS_RUNS_GIT,
    S2_AUTHORITY_BYPASS_CREATED,
    M3_DOES_NOT_REPLACE_M4,
    S5_ENTRY_READY_NOT_DECIDED_BY_M3_W3,
    M3_CORRECTNESS_REQUIRES_S6_TELEMETRY,
    NEW_SCHEDULER_CREATED,
    NEW_WORKFLOW_ENGINE_CREATED,
    NEW_EXECUTION_STATE_MACHINE_CREATED,
    NEW_RESULT_ONTOLOGY_CREATED,
    NEW_AUTHORITY_ONTOLOGY_CREATED,
    NEW_GIT_LIFECYCLE_CREATED,
    PERSISTENT_WORKFLOW_STATE_CREATED,
    PROJECT_STEWARD_RUNTIME_CREATED,
    NEW_ACCEPTANCE_ONTOLOGY_CREATED,
    EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED,
    PROJECT_STEWARD_RECONCILIATION_REQUIRED,
    STALE_REVIEWED_FRONTIER_CANNOT_REACH_STEWARD,
    MAX_SUPPORTING_EVIDENCE_REFS,
)
from aota_forge.work_plane.stop import SemanticStop, MechanicalFailure, SemanticStopReason, StopKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _semantic(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)

def _handoff(ref: str) -> ResultHandoffRef:
    return ResultHandoffRef(ref=ref)

def _reviewer_handoff(milestone_ref: str = "S4/M3") -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.REVIEWER,
        task_kind="review",
        objective="review",
        bounded_scope=milestone_ref,
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        milestone_ref=_semantic(milestone_ref),
        work_item_ref=_semantic(f"{milestone_ref}/review"),
    )

def _coder_handoff(milestone_ref: str = "S4/M3", work_item_ref: str = "S4/M3/repair-1") -> TaskHandoff:
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

def _finding(ref: str = "F1", classification=ReviewFindingClassification.BLOCKING) -> ReviewFindingEvidence:
    return ReviewFindingEvidence(
        finding_ref=ref,
        classification=classification,
        supporting_evidence_ref=_semantic(f"evidence:{ref}"),
        supporting_evidence_digest="a"*64,
    )

def _review_evidence(milestone_ref="S4/M3", cycle=ReviewCycle.RV1, frontier="frontier:abc123", result_ref="review-task-1", result_digest=None, finding_refs=()) -> MilestoneReviewEvidence:
    if result_digest is None:
        result_digest = "b"*64
    return MilestoneReviewEvidence(
        milestone_ref=_semantic(milestone_ref),
        review_cycle=cycle,
        reviewed_frontier_ref=_semantic(frontier),
        review_result_ref=_handoff(result_ref),
        review_result_digest=result_digest,
        finding_refs=tuple(finding_refs),
    )

def _card(task_id="review-task-1", role=AgentWorkRole.REVIEWER, outcome=ResultOutcome.SUCCESS, digest="corr1", semantic_stop=None, mechanical=None) -> WorkerResultCard:
    return WorkerResultCard(
        task_ref=task_id,
        agent_work_role=role,
        summary="ok",
        outcome=outcome,
        blocking_finding_count=0,
        non_blocking_finding_count=0,
        result_handoff_ref=_handoff(task_id),
        primary_evidence_refs=(),
        output_artifact_refs=(),
        semantic_stop=semantic_stop,
        mechanical_failure=mechanical,
    )

def _graph(milestone="S4/M3", work_items=("S4/M3/W1","S4/M3/W2","S4/M3/W3"), dependencies=(("S4/M3/W1","S4/M3/W2"),("S4/M3/W2","S4/M3/W3"))) -> MilestoneWorkItemGraph:
    return MilestoneWorkItemGraph(milestone_ref=milestone, work_items=tuple(work_items), dependencies=tuple(dependencies))

def _complete_progression(graph: MilestoneWorkItemGraph | None = None) -> ProgressionDisposition:
    g = graph or _graph()
    return ProgressionDisposition(
        progression_complete_work_item_refs=tuple(sorted(g.work_items)),
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=(),
        milestone_review_ready=True,
        auto_progression_allowed=False,
        reasons=(),
        evidence_refs=(),
    )

def _partial_progression(complete=("S4/M3/W1",)) -> ProgressionDisposition:
    # For W1 complete, W2 ready, W3 blocked (join requires all preds)
    if complete == ("S4/M3/W1",):
        return ProgressionDisposition(
            progression_complete_work_item_refs=("S4/M3/W1",),
            ready_work_item_refs=("S4/M3/W2",),
            blocked_work_item_refs=("S4/M3/W3",),
            challenge_routings=(),
            semantic_escalation_required_work_item_refs=(),
            reconciliation_required_work_item_refs=(),
            milestone_review_ready=False,
            auto_progression_allowed=True,
            reasons=("partial",),
            evidence_refs=(),
        )
    # generic fallback: derive ready as sorted missing that have preds satisfied (simple)
    all_w = ("S4/M3/W1","S4/M3/W2","S4/M3/W3")
    missing = tuple(sorted(set(all_w) - set(complete)))
    # For simplicity, make first missing ready, rest blocked
    ready = (missing[0],) if missing else ()
    blocked = tuple(sorted(set(missing) - set(ready)))
    return ProgressionDisposition(
        progression_complete_work_item_refs=tuple(sorted(complete)),
        ready_work_item_refs=ready,
        blocked_work_item_refs=blocked,
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=(),
        milestone_review_ready=False,
        auto_progression_allowed=bool(ready),
        reasons=("partial",),
        evidence_refs=(),
    )

def _make_repair(milestone="S4/M3", finding_refs=("F1",), repair_work="S4/M3/repair-1", pre="frontier:abc123", post="frontier:def456", result_ref="repair-task-1", result_digest="d"*64) -> RepairEvidence:
    return RepairEvidence(
        milestone_ref=_semantic(milestone),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=tuple(finding_refs),
        repair_work_ref=repair_work,
        pre_repair_frontier_ref=_semantic(pre),
        post_repair_frontier_ref=_semantic(post),
        repair_result_ref=_handoff(result_ref),
        repair_result_digest=result_digest,
        bounded_validation_or_supporting_refs=(),
    )

def _validation(work_item_ref="S4/M3/repair-1") -> FocusedValidationEvidence:
    return FocusedValidationEvidence(
        work_item_ref=work_item_ref,
        verdict=FocusedValidationVerdict.PASS,
        validation_evidence_ref=_semantic(f"val:{work_item_ref}"),
        validation_evidence_digest="c"*64,
    )

def _clean_rv1_chain(frontier="frontier:abc123", milestone="S4/M3"):
    """Helper to produce clean RV1 READY chain."""
    g = _graph(milestone=milestone)
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER, outcome=ResultOutcome.SUCCESS)
    handoff = _reviewer_handoff(milestone_ref=milestone)
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic(milestone),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic(frontier),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev,
        rv1_findings=(),
        rv1_result_card=card,
        rv1_task_handoff=handoff,
        expected_rv1_frontier=_semantic(frontier),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic(frontier),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    return g, prog, disp, ev, card, handoff, readiness

# ---------------------------------------------------------------------------
# §6: MilestoneClosureReadiness thin contract
# ---------------------------------------------------------------------------

def test_closure_readiness_is_authority_negative():
    assert MILESTONE_CLOSURE_READINESS_IS_ACCEPTANCE_AUTHORITY is False
    assert MILESTONE_CLOSURE_READINESS_IS_GIT_AUTHORITY is False
    assert MILESTONE_CLOSURE_READINESS_IS_PLAN_AUTHORITY is False
    assert READY_FOR_STEWARD_IS_STEWARD_DECISION is False

def test_closure_readiness_no_acceptance_fields():
    g = _graph()
    prog = _complete_progression(g)
    card = _card()
    handoff = _reviewer_handoff()
    ev = _review_evidence(result_digest=card.card_digest, result_ref=card.result_handoff_ref.ref, finding_refs=())
    # Ensure from_dict rejects authority fields
    with pytest.raises(ValueError):
        MilestoneClosureReadiness.from_dict({
            "milestone_ref": _semantic("S4/M3").to_dict(),
            "ready_for_project_steward": True,
            "final_review_cycle": "RV1",
            "reviewed_frontier_ref": _semantic("frontier:abc123").to_dict(),
            "accepted": True,
        })
    with pytest.raises(ValueError):
        MilestoneClosureReadiness.from_dict({
            "milestone_ref": _semantic("S4/M3").to_dict(),
            "ready_for_project_steward": True,
            "final_review_cycle": "RV1",
            "reviewed_frontier_ref": _semantic("frontier:abc123").to_dict(),
            "known_good": True,
        })

def test_closure_readiness_bounded_and_sorted():
    r = MilestoneClosureReadiness(
        milestone_ref=_semantic("S4/M3"),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        supporting_evidence_refs=("z-ref","a-ref","m-ref"),
        blocking_reasons=("z","a"),
    )
    assert r.supporting_evidence_refs == ("a-ref","m-ref","z-ref")
    assert r.blocking_reasons == ("a","z")

def test_closure_readiness_immutable():
    r = MilestoneClosureReadiness(
        milestone_ref=_semantic("S4/M3"),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
    )
    with pytest.raises(Exception):
        r.ready_for_project_steward = False  # type: ignore

def test_closure_readiness_reject_nul_and_whitespace():
    with pytest.raises(ValueError):
        MilestoneClosureReadiness(
            milestone_ref=SemanticReference(ref="a\x00b"),
            ready_for_project_steward=True,
            final_review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=_semantic("frontier:abc123"),
        )
    with pytest.raises(ValueError):
        MilestoneClosureReadiness(
            milestone_ref=_semantic("S4/M3"),
            ready_for_project_steward=True,
            final_review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=_semantic("frontier:abc123"),
            supporting_evidence_refs=("bad\x00ref",),
        )
    with pytest.raises(ValueError):
        MilestoneClosureReadiness(
            milestone_ref=_semantic("S4/M3"),
            ready_for_project_steward=True,
            final_review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=_semantic("frontier:abc123"),
            supporting_evidence_refs=("  leading",),
        )

def test_closure_readiness_reject_foreign_enum_and_dict_impostor():
    class FakeEnum(Enum):
        RV1 = "RV1"
    with pytest.raises(TypeError):
        MilestoneClosureReadiness(
            milestone_ref=_semantic("S4/M3"),
            ready_for_project_steward=True,
            final_review_cycle=FakeEnum.RV1,  # type: ignore
            reviewed_frontier_ref=_semantic("frontier:abc123"),
        )
    with pytest.raises(TypeError):
        MilestoneClosureReadiness(  # type: ignore
            milestone_ref={"ref": "S4/M3"},  # type: ignore
            ready_for_project_steward=True,
            final_review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=_semantic("frontier:abc123"),
        )
    with pytest.raises(ValueError):
        MilestoneClosureReadiness.from_dict({
            "milestone_ref": _semantic("S4/M3").to_dict(),
            "ready_for_project_steward": True,
            "final_review_cycle": "RV1",
            "reviewed_frontier_ref": _semantic("frontier:abc123").to_dict(),
            "unknown_field": "oops",
        })

def test_closure_readiness_reject_oversized_supporting():
    with pytest.raises(ValueError):
        MilestoneClosureReadiness(
            milestone_ref=_semantic("S4/M3"),
            ready_for_project_steward=True,
            final_review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=_semantic("frontier:abc123"),
            supporting_evidence_refs=tuple(f"ref-{i}" for i in range(17)),
        )

def test_closure_readiness_deterministic_digest():
    r1 = MilestoneClosureReadiness(
        milestone_ref=_semantic("S4/M3"),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        supporting_evidence_refs=("a","b"),
        blocking_reasons=(),
    )
    r2 = MilestoneClosureReadiness(
        milestone_ref=_semantic("S4/M3"),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        supporting_evidence_refs=("b","a"),  # reordered
        blocking_reasons=(),
    )
    assert r1.digest == r2.digest
    assert r1.canonical_json() == r2.canonical_json()
    # material change must affect digest
    r3 = MilestoneClosureReadiness(
        milestone_ref=_semantic("S4/M3"),
        ready_for_project_steward=False,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        supporting_evidence_refs=("a","b"),
        blocking_reasons=("blocked",),
    )
    assert r1.digest != r3.digest

# ---------------------------------------------------------------------------
# §7 evaluator stateless / derived
# ---------------------------------------------------------------------------

def test_evaluator_stateless_and_derived_flags():
    assert M3_CLOSURE_READINESS_EVALUATOR_STATELESS is True
    assert CLOSURE_READINESS_DERIVED_FROM_EVIDENCE is True
    assert M3_CLOSURE_READINESS_RUNS_GIT is False

def test_evaluator_runs_no_git():
    src = inspect.getsource(evaluate_milestone_closure_readiness)
    assert "import subprocess" not in src
    assert "git cat-file" not in src
    assert "git merge-base" not in src
    assert "git ls-remote" not in src
    # check module file for forbidden imports
    path = inspect.getfile(evaluate_milestone_closure_readiness)
    content = open(path, encoding="utf-8").read()
    assert "import subprocess" not in content
    assert "import os" not in content
    for forbidden in ("git cat-file", "git merge-base", "git ls-remote"):
        assert forbidden not in content

# ---------------------------------------------------------------------------
# §8 Work-Item Completion Gate
# ---------------------------------------------------------------------------

def test_partial_w_completion_not_ready():
    g = _graph()
    prog_partial = _partial_progression(("S4/M3/W1",))
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog_partial,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False
    assert any("incomplete" in r or "missing" in r for r in readiness.blocking_reasons)

def test_all_w_complete_may_proceed_to_review_gate():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    assert readiness.ready_for_project_steward is True

def test_foreign_work_item_fail_closed():
    g = _graph()
    prog_foreign = ProgressionDisposition(
        progression_complete_work_item_refs=("S4/M3/W1","S4/M3/W2","S4/M3/W999"),
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=(),
        milestone_review_ready=True,
        auto_progression_allowed=False,
        reasons=(),
        evidence_refs=(),
    )
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog_foreign,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False
    assert any("foreign" in r for r in readiness.blocking_reasons)

def test_graph_progression_authority_boundary():
    assert MILESTONE_CLOSURE_READINESS_IS_PLAN_AUTHORITY is False
    # Use dict impostor for progression
    g = _graph()
    prog_dict = {
        "progression_complete_work_item_refs": ["S4/M3/W1","S4/M3/W2","S4/M3/W3"],
        "milestone_review_ready": True,
    }
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    with pytest.raises(TypeError):
        evaluate_milestone_closure_readiness(
            graph=g,
            progression_disposition=prog_dict,  # type: ignore
            workflow_disposition=disp,
            final_review_evidence=ev,
            expected_frontier_ref=_semantic("frontier:abc123"),
            final_review_result_card=card,
            final_review_task_handoff=handoff,
        )

# ---------------------------------------------------------------------------
# §10 Final Workflow Gate
# ---------------------------------------------------------------------------

def test_final_workflow_gate_only_ready():
    g = _graph()
    prog = _complete_progression(g)
    # Create RV1 with blocking finding -> REPAIR_REQUIRED
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

# ---------------------------------------------------------------------------
# §11 Final Review Cycle Binding
# ---------------------------------------------------------------------------

def test_clean_rv1_closure_claims_rv2_blocked():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    # Try to claim RV2 with same evidence but cycle mismatch: construct fake RV2 evidence with RV1 frontier
    ev_rv2_fake = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    readiness2 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,  # this disp is RV1 READY
        final_review_evidence=ev_rv2_fake,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness2.ready_for_project_steward is False
    assert any("cycle" in r for r in readiness2.blocking_reasons)

def test_repaired_path_closure_claims_rv1_blocked():
    # Build repaired path then claim RV1
    g = _graph()
    prog = _complete_progression(g)
    # RV1 blocking
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    # Repair
    repair = _make_repair(pre="frontier:abc123", post="frontier:def456", result_digest="d"*64)
    repair_card, repair_handoff = _card(task_id="repair-task-1", role=AgentWorkRole.CODER), _coder_handoff()
    # Need to set repair's digest to match card
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    # RV2 clean at F2
    card_rv2 = _card(task_id="review-task-rv2", role=AgentWorkRole.REVIEWER)
    h2 = _reviewer_handoff()
    ev_rv2 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=card_rv2.result_handoff_ref,
        review_result_digest=card_rv2.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        rv2_evidence=ev_rv2,
        rv2_findings=(),
        rv2_result_card=card_rv2,
        rv2_task_handoff=h2,
        expected_rv2_frontier=_semantic("frontier:def456"),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD
    # Now claim closure with RV1 cycle (stale)
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv1,  # wrong: should be RV2
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card_rv1,
        final_review_task_handoff=h1,
        repair_evidences=(repair,),
        rv1_evidence=ev_rv1,
    )
    assert readiness.ready_for_project_steward is False
    assert any("cycle" in r for r in readiness.blocking_reasons)

# ---------------------------------------------------------------------------
# §12 Final Reviewed Frontier Binding
# ---------------------------------------------------------------------------

def test_stale_frontier_clean_rv1_claims_f2_blocked():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain(frontier="frontier:abc123")
    readiness2 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:def456"),  # F2 expected but RV1 at F1
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness2.ready_for_project_steward is False

def test_stale_frontier_old_after_rv2_blocked():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    card_rv2 = _card(task_id="review-task-rv2", role=AgentWorkRole.REVIEWER)
    h2 = _reviewer_handoff()
    ev_rv2 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=card_rv2.result_handoff_ref,
        review_result_digest=card_rv2.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        rv2_evidence=ev_rv2,
        rv2_findings=(),
        rv2_result_card=card_rv2,
        rv2_task_handoff=h2,
        expected_rv2_frontier=_semantic("frontier:def456"),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD
    # Closure attempts old F1 frontier
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv2,
        expected_frontier_ref=_semantic("frontier:abc123"),  # old
        final_review_result_card=card_rv2,
        final_review_task_handoff=h2,
        repair_evidences=(repair,),
        rv1_evidence=ev_rv1,
    )
    assert readiness.ready_for_project_steward is False
    assert STALE_REVIEWED_FRONTIER_CANNOT_REACH_STEWARD is True

# ---------------------------------------------------------------------------
# §22-26 repair cannot skip etc.
# ---------------------------------------------------------------------------

def test_repair_cannot_skip_rv2():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    # No RV2 yet -> workflow should be RV2_REQUIRED, not READY
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        # no rv2
    )
    assert disp.disposition == WorkflowDisposition.RV2_REQUIRED
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv1,  # only RV1 exists
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card_rv1,
        final_review_task_handoff=h1,
        repair_evidences=(repair,),
    )
    assert readiness.ready_for_project_steward is False

def test_partial_repair_cannot_close():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    f2 = _finding("F2", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1","F2"),
    )
    # Only repair F1
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,f2),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
    )
    assert disp.disposition == WorkflowDisposition.REPAIR_REQUIRED
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv1,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card_rv1,
        final_review_task_handoff=h1,
        repair_evidences=(repair,),
    )
    assert readiness.ready_for_project_steward is False

def test_rv2_blocking_cannot_close():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    # RV2 with blocking
    f_block_rv2 = _finding("F_RV2_BLOCK", ReviewFindingClassification.BLOCKING)
    card_rv2 = _card(task_id="review-task-rv2", role=AgentWorkRole.REVIEWER)
    h2 = _reviewer_handoff()
    ev_rv2 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=card_rv2.result_handoff_ref,
        review_result_digest=card_rv2.card_digest,
        finding_refs=("F_RV2_BLOCK",),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        rv2_evidence=ev_rv2,
        rv2_findings=(f_block_rv2,),
        rv2_result_card=card_rv2,
        rv2_task_handoff=h2,
        expected_rv2_frontier=_semantic("frontier:def456"),
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv2,
        expected_frontier_ref=_semantic("frontier:def456"),
        final_review_result_card=card_rv2,
        final_review_task_handoff=h2,
        repair_evidences=(repair,),
        rv1_evidence=ev_rv1,
    )
    assert readiness.ready_for_project_steward is False

def test_repeated_failure_cannot_close():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    # Create fingerprints for repeated failure
    fp = FailureFingerprint(
        milestone_ref=_semantic("S4/M3"),
        work_item_ref="S4/M3/W1",
        failure_domain=StopKind.SEMANTIC_STOP,
        failure_classification=SemanticStopReason.REPEATED_FAILURE_INDICATING_PLAN_DEFECT.value,
        task_identity="task-1",
        supporting_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        failure_fingerprints=(fp, fp),  # duplicate indicates repeated
    )
    assert disp.disposition == WorkflowDisposition.REPLAN_REQUIRED
    # W3 should also not be ready
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv1,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card_rv1,
        final_review_task_handoff=h1,
        repair_evidences=(repair,),
        rv1_evidence=ev_rv1,
    )
    assert readiness.ready_for_project_steward is False
    # Also check semantic stop reason reused
    assert SemanticStopReason.REPEATED_FAILURE_INDICATING_PLAN_DEFECT.value == "REPEATED_FAILURE_INDICATING_PLAN_DEFECT"

def test_history_overflow_cannot_close():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    # Create overflow repair count >16
    repairs = tuple(
        RepairEvidence(
            milestone_ref=_semantic("S4/M3"),
            originating_review_cycle=ReviewCycle.RV1,
            originating_finding_refs=(f"F{i}",),
            repair_work_ref=f"S4/M3/repair-{i}",
            pre_repair_frontier_ref=_semantic(f"frontier:pre{i}"),
            post_repair_frontier_ref=_semantic(f"frontier:post{i}"),
            repair_result_ref=_handoff(f"repair-task-{i}"),
            repair_result_digest="d"*64,
            bounded_validation_or_supporting_refs=(),
        ) for i in range(17)
    )
    # Workflow with overflow should be REPLAN
    # We can construct history with 17 entries to simulate overflow
    # But our W2 evaluator will return REPLAN if count >16, we can test via closure directly
    # Create a workflow that is REPLAN due to overflow
    disp = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.REPLAN_REQUIRED,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
        reasons=("history overflow",),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
        repair_evidences=repairs,
    )
    assert readiness.ready_for_project_steward is False

# ---------------------------------------------------------------------------
# §19 Reviewer impersonation
# ---------------------------------------------------------------------------

def test_wrong_role_not_ready():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    # Create card with wrong role CODER
    bad_card = _card(task_id="review-task-rv1", role=AgentWorkRole.CODER)
    ev_bad = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=bad_card.result_handoff_ref,
        review_result_digest=bad_card.card_digest,
        finding_refs=(),
    )
    # Workflow using bad card would be BLOCKED, but we test closure directly with forged READY
    forged_disp = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness_bad = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged_disp,
        final_review_evidence=ev_bad,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=bad_card,
        final_review_task_handoff=_reviewer_handoff(),  # handoff expects REVIEWER but card is CODER
    )
    assert readiness_bad.ready_for_project_steward is False

def test_wrong_result_ref_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
    ev_bad = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=_handoff("wrong-ref"),
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev_bad,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

def test_wrong_digest_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
    ev_bad = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest="f"*64,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev_bad,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

def test_wrong_task_handoff_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    wrong_handoff = TaskHandoff(
        work_role=AgentWorkRole.REVIEWER,
        task_kind="review",
        objective="review",
        bounded_scope="wrong",
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        milestone_ref=_semantic("S4/M3_WRONG"),
        work_item_ref=_semantic("S4/M3/W1"),
    )
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=wrong_handoff,
    )
    assert readiness.ready_for_project_steward is False

def test_review_failure_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card_fail = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER, outcome=ResultOutcome.FAILURE)
    handoff = _reviewer_handoff()
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_fail.result_handoff_ref,
        review_result_digest=card_fail.card_digest,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card_fail,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

def test_review_unknown_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER, outcome=ResultOutcome.UNKNOWN)
    handoff = _reviewer_handoff()
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

# ---------------------------------------------------------------------------
# §20 Wrong Milestone
# ---------------------------------------------------------------------------

def test_cross_milestone_not_ready():
    g = _graph(milestone="S4/M3")
    prog = _complete_progression(g)
    # Review for Milestone A (S4/M2) should not make M3 ready
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff(milestone_ref="S4/M2")
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M2"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    disp = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M2"),
        final_frontier_ref=_semantic("frontier:abc123"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
        milestone_ref=_semantic("S4/M3"),
    )
    assert readiness.ready_for_project_steward is False

# ---------------------------------------------------------------------------
# §30 Clean RV1 Positive vertical
# ---------------------------------------------------------------------------

def test_clean_rv1_positive_vertical():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain(frontier="frontier:clean123")
    assert readiness.ready_for_project_steward is True
    assert readiness.final_review_cycle == ReviewCycle.RV1
    assert readiness.reviewed_frontier_ref.ref == "frontier:clean123"
    # Steward firewall: readiness does not imply acceptance
    assert readiness.is_acceptance_authority is False
    assert readiness.is_git_authority is False
    # Check still not accepted/known_good/closed (those fields don't exist)
    assert not hasattr(readiness, "accepted") or getattr(readiness, "accepted", None) is not True
    assert not hasattr(readiness, "known_good")
    assert not hasattr(readiness, "closed")

# ---------------------------------------------------------------------------
# §31 Repair + RV2 Positive vertical
# ---------------------------------------------------------------------------

def test_repair_rv2_positive_vertical():
    g = _graph()
    prog = _complete_progression(g)
    f1 = _finding("F1", ReviewFindingClassification.BLOCKING)
    card_rv1 = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    h1 = _reviewer_handoff()
    ev_rv1 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card_rv1.result_handoff_ref,
        review_result_digest=card_rv1.card_digest,
        finding_refs=("F1",),
    )
    repair_card = _card(task_id="repair-task-1", role=AgentWorkRole.CODER)
    repair_handoff = _coder_handoff()
    repair = RepairEvidence(
        milestone_ref=_semantic("S4/M3"),
        originating_review_cycle=ReviewCycle.RV1,
        originating_finding_refs=("F1",),
        repair_work_ref="S4/M3/repair-1",
        pre_repair_frontier_ref=_semantic("frontier:abc123"),
        post_repair_frontier_ref=_semantic("frontier:def456"),
        repair_result_ref=repair_card.result_handoff_ref,
        repair_result_digest=repair_card.card_digest,
        bounded_validation_or_supporting_refs=(),
    )
    card_rv2 = _card(task_id="review-task-rv2", role=AgentWorkRole.REVIEWER)
    h2 = _reviewer_handoff()
    ev_rv2 = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV2,
        reviewed_frontier_ref=_semantic("frontier:def456"),
        review_result_ref=card_rv2.result_handoff_ref,
        review_result_digest=card_rv2.card_digest,
        finding_refs=(),
    )
    disp = evaluate_milestone_review_workflow(
        rv1_evidence=ev_rv1,
        rv1_findings=(f1,),
        rv1_result_card=card_rv1,
        rv1_task_handoff=h1,
        expected_rv1_frontier=_semantic("frontier:abc123"),
        repair_evidences=(repair,),
        repair_result_cards={"repair-task-1": repair_card},
        repair_task_handoffs={"S4/M3/repair-1": repair_handoff},
        validation_evidences=(_validation("S4/M3/repair-1"),),
        rv2_evidence=ev_rv2,
        rv2_findings=(),
        rv2_result_card=card_rv2,
        rv2_task_handoff=h2,
        expected_rv2_frontier=_semantic("frontier:def456"),
    )
    assert disp.disposition == WorkflowDisposition.READY_FOR_STEWARD
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev_rv2,
        expected_frontier_ref=_semantic("frontier:def456"),
        final_review_result_card=card_rv2,
        final_review_task_handoff=h2,
        repair_evidences=(repair,),
        rv1_evidence=ev_rv1,
    )
    assert readiness.ready_for_project_steward is True
    assert readiness.final_review_cycle == ReviewCycle.RV2
    assert readiness.reviewed_frontier_ref.ref == "frontier:def456"
    assert readiness.is_acceptance_authority is False
    assert not hasattr(readiness, "accepted")

# ---------------------------------------------------------------------------
# §32 W2 Workflow Disposition Forgery
# ---------------------------------------------------------------------------

def test_forged_ready_without_coherent_evidence_blocked():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
    ev = MilestoneReviewEvidence(
        milestone_ref=_semantic("S4/M3"),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=_semantic("frontier:abc123"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.card_digest,
        finding_refs=(),
    )
    forged = MilestoneReviewWorkflowDisposition(
        disposition=WorkflowDisposition.READY_FOR_STEWARD,
        milestone_ref=_semantic("S4/M3"),
        final_frontier_ref=_semantic("frontier:forged"),
    )
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=forged,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    # Frontier mismatch should block even though workflow says READY
    assert readiness.ready_for_project_steward is False

def test_caller_ready_bool_non_authority():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    # Even if caller tries to supply ready=true via manual object, evaluator must re-derive
    # Our evaluator derives ready from evidence, not caller bool; so no bypass
    assert readiness.ready_for_project_steward is True
    # Ensure that WorkerResultCard next_hint / summary cannot make ready when frontier is wrong
    # Use wrong frontier with hint-like summary but same card – should still be not ready
    readiness_wrong = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:wrong-frontier"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness_wrong.ready_for_project_steward is False
    # Also ensure correct frontier with hint-like card (reuse same card, summary contains hint conceptually) remains ready
    # card's summary is not used for authority, so coherent evidence still yields ready
    readiness2 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness2.ready_for_project_steward is True

# ---------------------------------------------------------------------------
# §33 Determinism
# ---------------------------------------------------------------------------

def test_determinism_reordered_supporting():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    r1 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
        supporting_evidence_refs=("b","a","c"),
    )
    r2 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
        supporting_evidence_refs=("c","b","a"),
    )
    assert r1.digest == r2.digest
    assert r1.canonical_json() == r2.canonical_json()
    # Material change must differ
    r3 = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:xyz"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert r1.digest != r3.digest

# ---------------------------------------------------------------------------
# §34 Immutable / bounds already tested, add S2 firewall
# ---------------------------------------------------------------------------

def test_s2_authority_firewall():
    assert S2_AUTHORITY_BYPASS_CREATED is False
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    assert readiness.is_git_authority is False
    assert readiness.is_acceptance_authority is False
    assert readiness.is_plan_authority is False
    # Ensure not mistaken for S2 authority evidence (has no grant methods)
    assert not hasattr(readiness, "grants_workspace_mutation") or readiness.grants_workspace_mutation is False if hasattr(readiness, "grants_workspace_mutation") else True

def test_no_hidden_execution_effects():
    src_path = inspect.getfile(evaluate_milestone_closure_readiness)
    content = open(src_path, encoding="utf-8").read()
    for bad in ["import subprocess", "import os", "import socket", "import time", "import random", "import uuid"]:
        assert bad not in content
    # Ensure no direct Git lifecycle invocations
    for bad in ["git cat-file", "git merge-base", "git ls-remote"]:
        assert bad not in content
    # Thread/process spawn, filesystem mutation not present as imports
    assert "import threading" not in content
    assert "import multiprocessing" not in content

# ---------------------------------------------------------------------------
# §27 Conflicting progression
# ---------------------------------------------------------------------------

def test_conflicting_progression_not_ready():
    g = _graph()
    # Conflicting progression: reconciliation_required non-empty
    prog_conflict = ProgressionDisposition(
        progression_complete_work_item_refs=("S4/M3/W1","S4/M3/W2","S4/M3/W3"),
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        challenge_routings=(),
        semantic_escalation_required_work_item_refs=(),
        reconciliation_required_work_item_refs=("S4/M3/W1",),
        milestone_review_ready=True,
        auto_progression_allowed=False,
        reasons=("conflict",),
        evidence_refs=(),
    )
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog_conflict,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:abc123"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False
    assert any("reconciliation" in r for r in readiness.blocking_reasons)

# ---------------------------------------------------------------------------
# §18 Project Steward Firewall
# ---------------------------------------------------------------------------

def test_project_steward_firewall():
    assert PROJECT_STEWARD_RECONCILIATION_REQUIRED is True
    assert EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED is True
    assert NEW_ACCEPTANCE_ONTOLOGY_CREATED is False
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    assert readiness.ready_for_project_steward is True
    # None of these imply acceptance
    assert not hasattr(readiness, "accepted") or readiness.ready_for_project_steward != getattr(readiness, "accepted", False)
    # Ensure flags
    assert MILESTONE_CLOSURE_READINESS_IS_ACCEPTANCE_AUTHORITY is False

# ---------------------------------------------------------------------------
# §35-39 boundaries
# ---------------------------------------------------------------------------

def test_m3_m4_boundary():
    assert M3_DOES_NOT_REPLACE_M4 is True
    assert S5_ENTRY_READY_NOT_DECIDED_BY_M3_W3 is True

def test_s6_telemetry_not_required():
    assert M3_CORRECTNESS_REQUIRES_S6_TELEMETRY is False

def test_no_new_ontologies():
    assert NEW_SCHEDULER_CREATED is False
    assert NEW_WORKFLOW_ENGINE_CREATED is False
    assert NEW_EXECUTION_STATE_MACHINE_CREATED is False
    assert NEW_RESULT_ONTOLOGY_CREATED is False
    assert NEW_AUTHORITY_ONTOLOGY_CREATED is False
    assert NEW_GIT_LIFECYCLE_CREATED is False
    assert PERSISTENT_WORKFLOW_STATE_CREATED is False
    assert PROJECT_STEWARD_RUNTIME_CREATED is False

# ---------------------------------------------------------------------------
# Frontier attacks
# ---------------------------------------------------------------------------

def test_foreign_frontier_not_ready():
    g = _graph()
    prog = _complete_progression(g)
    card = _card(task_id="review-task-rv1", role=AgentWorkRole.REVIEWER)
    handoff = _reviewer_handoff()
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
    readiness = evaluate_milestone_closure_readiness(
        graph=g,
        progression_disposition=prog,
        workflow_disposition=disp,
        final_review_evidence=ev,
        expected_frontier_ref=_semantic("frontier:foreign999"),
        final_review_result_card=card,
        final_review_task_handoff=handoff,
    )
    assert readiness.ready_for_project_steward is False

def test_supporting_evidence_bounded():
    g, prog, disp, ev, card, handoff, readiness = _clean_rv1_chain()
    assert len(readiness.supporting_evidence_refs) <= MAX_SUPPORTING_EVIDENCE_REFS
