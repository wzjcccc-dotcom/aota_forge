"""AF #57 M3/W3 trusted host Stewardship bootstrap proofs."""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.composition import task_main_host_bootstrap as host
from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    SemanticFactSet,
    build_semantic_steward_handoff,
    evaluate_checkpoint,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.reconciliation import (
    COMPLETION_KIND_REVIEW,
    DISPOSITION_REVIEW_READY_FOR_STEWARD,
    CompletionReconciliationReceipt,
)
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_MILESTONE_CLOSURE_READY,
    RunnerOutcome,
)
from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.handoff_store import HANDOFF_CONTROL_FIELDS, handoff_write
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.result_card import ResultHandoffRef
from aota_forge.work_plane.steward_finalizer import (
    TrustedPlanIdentity,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.work_plane.task_facade import task_start


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_wzjcccc_dotcom_aota_hermes_tools_57"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#57"
MILESTONE = "M3"


def _sandbox(tmp_path: Path) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="ws-w3-host",
        workspace_root=str(tmp_path),
        project_id=PROJECT_ID,
        project_root=str(tmp_path),
        worktree_id="wt-w3-host",
        worktree_root=str(tmp_path),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _plan_view() -> MilestonePlanView:
    return MilestonePlanView(
        plan_authority=PLAN_REF,
        plan_digest="d" * 64,
        milestone_id=MILESTONE,
        entry_base="e" * 40,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE,
            work_items=["W1"],
            dependencies=[],
        ),
        milestone_user_approval_satisfied=True,
    )


def _state_with_review_receipt() -> TaskMainCoordinatorState:
    review = MilestoneReviewEvidence(
        milestone_ref=SemanticReference(ref=MILESTONE),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref="frontier:rv1"),
        review_result_ref=ResultHandoffRef(ref="review-task", digest="card-digest"),
        review_result_digest="card-digest",
    )
    receipt = CompletionReconciliationReceipt(
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        plan_authority=PLAN_REF,
        milestone_id=MILESTONE,
        work_item_id=None,
        completion_kind=COMPLETION_KIND_REVIEW,
        canonical_task_id="review-task",
        card_digest="card-digest",
        result_handoff_ref="review-task",
        result_digest="card-digest",
        prev_coordinator_revision=1,
        next_coordinator_revision=2,
        progression_revision=1,
        progression_disposition=DISPOSITION_REVIEW_READY_FOR_STEWARD,
        validation_evidence_digest=None,
        risk_disposition_digest=None,
        review_workflow_disposition="PASS",
        working_truth_digest="truth-digest",
        governed_evidence={
            "review_evidence": review.to_dict(),
            "closure_ready": True,
        },
        reconciled_at="2026-09-18T00:00:00+00:00",
    ).with_digest()
    return TaskMainCoordinatorState(
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        plan_authority=PLAN_REF,
        plan_digest="d" * 64,
        milestone_id=MILESTONE,
        entry_base="e" * 40,
        origin_task_main_session_ref="session:w3-host",
        project_id=PROJECT_ID,
        executor_id="hermes",
        work_items=("W1",),
        wi_status={"W1": "COMPLETION_PENDING_RECONCILIATION"},
        reconciled_completions={receipt.canonical_task_id: receipt.to_dict()},
        user_approval_satisfied=True,
        coordinator_revision=2,
    )


def _trusted_plan() -> TrustedPlanIdentity:
    return TrustedPlanIdentity(
        governing_repo="wzjcccc-dotcom/aota-hermes-tools",
        plan_issue_number=57,
        milestone_ref=MILESTONE,
        plan_ref=PLAN_REF,
        managed_comments={},
    )


def test_host_rebuilds_checkpoint_from_durable_review_receipt(tmp_path: Path) -> None:
    state = _state_with_review_receipt()
    checkpoint = host._build_stewardship_checkpoint(
        runner_outcome=RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=state.coordinator_id,
            coordinator_revision=state.coordinator_revision,
            milestone_closure_ready=True,
        ),
        state=state,
        live_view=_plan_view(),
        sandbox=_sandbox(tmp_path),
        trusted_plan=_trusted_plan(),
        semantic_facts=SemanticFactSet(),
        plan_id=PLAN_ID,
    )

    assert checkpoint.kind is GovernanceCheckpointKind.MILESTONE_CLOSE
    assert checkpoint.plan_id == PLAN_ID
    assert checkpoint.readiness is not None
    assert checkpoint.readiness.ready_for_project_steward is True
    assert checkpoint.readiness.reviewed_frontier_ref.ref == "frontier:rv1"


def test_host_rejects_governance_store_outside_trusted_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    monkeypatch.setenv(host.BOOTSTRAP_ENV_ROOT, str(worktree_root))
    data = {
        "project_id": PROJECT_ID,
        "worktree_id": "wt-w3-host",
        "origin_task_main_session_ref": "session:w3-host",
        "worktree_root": str(worktree_root),
        "coordinator_store_path": str(worktree_root / ".aota" / "coordinator.json"),
        "execution_store_path": str(worktree_root / ".aota" / "execution.json"),
        "runtime_config_path": str(worktree_root / "runtime.json"),
        "governance_store_path": str(tmp_path / "foreign.sqlite3"),
        "live_plan_view": {"plan_digest": "d" * 64},
    }

    with pytest.raises(TrustedBindingError, match="governance_store_path outside"):
        host._validate_bootstrap_trust_boundary(data, None)


def test_host_semantic_dispatch_keeps_task_start_identity(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoint = host._build_stewardship_checkpoint(
        runner_outcome=RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
            coordinator_revision=2,
            milestone_closure_ready=True,
        ),
        state=_state_with_review_receipt(),
        live_view=_plan_view(),
        sandbox=_sandbox(tmp_path),
        trusted_plan=_trusted_plan(),
        semantic_facts=SemanticFactSet(architecture_question_refs=("architecture:q1",)),
        plan_id=PLAN_ID,
    )
    writes: list[dict[str, object]] = []
    starts: list[dict[str, object]] = []

    def fake_write(**kwargs):
        writes.append(kwargs)
        return "handoff-ref"

    def fake_start(**kwargs):
        starts.append(kwargs)
        return {"task_id": writes[-1]["task_id"]}

    from aota_forge.work_plane import handoff_store, task_facade

    monkeypatch.setattr(handoff_store, "handoff_write", fake_write)
    monkeypatch.setattr(task_facade, "task_start", fake_start)
    factory = host._production_steward_dispatch_factory(
        dispatcher=object(),
        sandbox=_sandbox(tmp_path),
        plan_id=PLAN_ID,
    )
    dispatch = factory(checkpoint)
    evaluation = evaluate_checkpoint(checkpoint)
    assert evaluation.residual is not None
    expected_handoff = build_semantic_steward_handoff(checkpoint, evaluation.residual)

    assert dispatch(expected_handoff) is None
    assert writes[0]["mode"] == "work_item"
    assert "plan_ref" not in writes[0]["semantic"]
    assert writes[0]["plan_ref"] == PLAN_REF
    assert writes[0]["task_id"].startswith("steward:")
    assert starts[0]["role"] == "project-steward"
    assert starts[0]["plan_id"] == PLAN_ID


def test_steward_task_start_identity_is_durable_not_process_local(tmp_path: Path) -> None:
    checkpoint = host._build_stewardship_checkpoint(
        runner_outcome=RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
            coordinator_revision=2,
            milestone_closure_ready=True,
        ),
        state=_state_with_review_receipt(),
        live_view=_plan_view(),
        sandbox=_sandbox(tmp_path),
        trusted_plan=_trusted_plan(),
        semantic_facts=SemanticFactSet(architecture_question_refs=("architecture:q1",)),
        plan_id=PLAN_ID,
    )
    evaluation = evaluate_checkpoint(checkpoint)
    assert evaluation.residual is not None
    handoff = build_semantic_steward_handoff(checkpoint, evaluation.residual)
    task_id = host._steward_task_id(checkpoint, handoff)
    semantic = {
        key: value for key, value in handoff.to_dict().items() if key not in HANDOFF_CONTROL_FIELDS
    }
    sandbox = _sandbox(tmp_path)
    ref = handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=sandbox,
        plan_ref=PLAN_REF,
        milestone_id=MILESTONE,
        target_role="project-steward",
        task_id=task_id,
    )
    execution_path = tmp_path / "execution.json"
    store = FileBackedExecutionStateStore(execution_path)
    registry = ExecutorRegistry()
    registry.register(ReferenceFakeExecutorAdapter())
    started = task_start(
        role="project-steward",
        handoff_ref=ref,
        caller_role="task-main",
        sandbox=sandbox,
        dispatcher=ExecutionDispatcher(registry, state_store=store),
        plan_id=PLAN_ID,
    )

    assert started["task_id"] == task_id
    durable = store.get(task_id)
    assert durable is not None
    assert durable.canonical_task_id == task_id
    store.close()

    reopened = FileBackedExecutionStateStore(execution_path)
    resolver = host._production_steward_result_task_id_resolver_factory(reopened)(checkpoint)
    assert resolver(object()) == task_id
    reopened.close()
