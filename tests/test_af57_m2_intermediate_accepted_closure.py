"""AF #002 M2/R1 intermediate accepted Milestone closure semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.composition import task_main_host_bootstrap as host
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.governance.cross_project_grant import UserGateApproval
from aota_forge.governance.project_store import (
    USER_GATE_STATE_REQUIRED,
    USER_GATE_STATE_REVOKED,
    ProjectPlanUserGateRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    SemanticFactSet,
    evaluate_checkpoint,
    produce_deterministic_steward_result,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
)
from aota_forge.runtime.task_main.reconciliation import (
    COMPLETION_KIND_REVIEW,
    DISPOSITION_REVIEW_READY_FOR_STEWARD,
    CompletionReconciliationReceipt,
)
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_NEXT_MILESTONE_USER_GATE,
    RunnerOutcome,
    advance_milestone_once,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.result_card import ResultHandoffRef
from aota_forge.work_plane.steward_dispatch import StewardClosureVerdict
from aota_forge.work_plane.steward_finalizer import ClosurePhase, TrustedPlanIdentity
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "aota_forge"
PLAN_ID = "plan_002_session_first_context_activation"
PLAN_REF = local_plan_authority_reference(PROJECT_ID, PLAN_ID)
PLAN_DIGEST = "d" * 64
PLAN_SOURCE_REVISION = "R5"
MILESTONE = "M2"
ENTRY_BASE = "e" * 40
REVIEWED_FRONTIER = "dc63f17fcb96db1b5ac4050fa7419b245ea2fdaf"


def _view(milestone: str = MILESTONE, *, approved: bool = True) -> MilestonePlanView:
    return MilestonePlanView(
        plan_authority=PLAN_REF,
        plan_digest=PLAN_DIGEST,
        plan_source_revision=PLAN_SOURCE_REVISION,
        milestone_id=milestone,
        entry_base=ENTRY_BASE,
        graph=MilestoneWorkItemGraph(milestone_ref=milestone, work_items=["W1"], dependencies=[]),
        milestone_user_approval_satisfied=approved,
    )


def _review_state() -> TaskMainCoordinatorState:
    review = MilestoneReviewEvidence(
        milestone_ref=SemanticReference(ref=MILESTONE),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref=REVIEWED_FRONTIER),
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
        working_truth_digest="t" * 64,
        governed_evidence={"review_evidence": review.to_dict(), "closure_ready": True},
        reconciled_at="2026-09-26T00:00:00+00:00",
    ).with_digest()
    return TaskMainCoordinatorState(
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        plan_authority=PLAN_REF,
        plan_digest=PLAN_DIGEST,
        plan_source_revision=PLAN_SOURCE_REVISION,
        milestone_id=MILESTONE,
        entry_base=ENTRY_BASE,
        origin_task_main_session_ref="session:m2-r1",
        project_id=PROJECT_ID,
        executor_id="hermes",
        work_items=("W1",),
        wi_status={"W1": "PENDING"},
        reconciled_completions={receipt.canonical_task_id: receipt.to_dict()},
        user_approval_satisfied=True,
        coordinator_revision=2,
    )


def _trusted_plan() -> TrustedPlanIdentity:
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=PLAN_REF,
    )
    return TrustedPlanIdentity(
        governing_repo=None,
        plan_issue_number=None,
        milestone_ref=MILESTONE,
        plan_ref=PLAN_REF,
        managed_comments={},
        authority_binding=binding,
    )


def _sandbox(tmp_path: Path) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="ws-m2-r1",
        workspace_root=str(tmp_path),
        project_id=PROJECT_ID,
        project_root=str(tmp_path),
        worktree_id="wt-m2-r1",
        worktree_root=str(tmp_path),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _checkpoint(
    tmp_path: Path,
    *,
    gate=None,
    all_milestones_closed: bool | None = False,
):
    state = _review_state()
    return host._build_stewardship_checkpoint(
        runner_outcome=RunnerOutcome(
            disposition="MILESTONE_CLOSURE_READY",
            coordinator_id=state.coordinator_id,
            coordinator_revision=state.coordinator_revision,
            milestone_closure_ready=True,
        ),
        state=state,
        live_view=_view(),
        sandbox=_sandbox(tmp_path),
        trusted_plan=_trusted_plan(),
        semantic_facts=SemanticFactSet(),
        plan_id=PLAN_ID,
        all_milestones_closed=all_milestones_closed,
        milestone_close_user_gate=gate,
    )


def _put_gate(
    store: SQLiteProjectGovernanceStore,
    *,
    project_id: str = PROJECT_ID,
    plan_id: str = PLAN_ID,
    gate_kind: str = "milestone_close",
    gate_scope: str = MILESTONE,
    state: str = "satisfied",
) -> None:
    store.put_user_gate(
        ProjectPlanUserGateRecord.from_approval(
            project_id,
            plan_id,
            gate_kind,
            gate_scope,
            UserGateApproval("approval:m2-close", "a" * 64),
            state=state,
        )
    )


def test_missing_gate_stays_reviewed_and_ignores_construction_approval(tmp_path: Path) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        gate = host.resolve_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id=MILESTONE,
        )
        checkpoint = _checkpoint(tmp_path, gate=gate)
        result = produce_deterministic_steward_result(checkpoint)
        assert _review_state().user_approval_satisfied is True
        assert checkpoint.kind is GovernanceCheckpointKind.MILESTONE_CLOSE
        assert checkpoint.closure_phase is ClosurePhase.REVIEWED_CLOSURE
        assert checkpoint.user_gate.user_approval_satisfied is False
        assert result.accepted_frontier_ref is None
    finally:
        store.close()


def test_matching_satisfied_gate_accepts_intermediate_milestone(tmp_path: Path) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        _put_gate(store)
        gate = host.resolve_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id=MILESTONE,
        )
        checkpoint = _checkpoint(tmp_path, gate=gate)
        evaluation = evaluate_checkpoint(checkpoint)
        result = produce_deterministic_steward_result(checkpoint)
        assert checkpoint.kind is GovernanceCheckpointKind.MILESTONE_CLOSE
        assert checkpoint.closure_phase is ClosurePhase.ACCEPTED_CLOSURE
        assert evaluation.disposition.value == "DETERMINISTIC_FINALIZABLE"
        assert result.verdict is StewardClosureVerdict.GOVERNANCE_SYNCED
        assert result.accepted_frontier_ref == result.reviewed_frontier_ref
        assert result.accepted_frontier_ref == REVIEWED_FRONTIER
        assert checkpoint.all_milestones_closed is None
    finally:
        store.close()


@pytest.mark.parametrize(
    "record_kwargs",
    [
        {"project_id": "foreign_project"},
        {"plan_id": "plan_other"},
        {"gate_kind": "construction"},
        {"gate_scope": "M3"},
        {"state": USER_GATE_STATE_REQUIRED},
        {"state": USER_GATE_STATE_REVOKED},
        None,
    ],
    ids=[
        "wrong-project",
        "wrong-plan",
        "wrong-kind",
        "wrong-scope",
        "required",
        "revoked",
        "missing",
    ],
)
def test_mismatched_or_inactive_gate_fails_closed(tmp_path: Path, record_kwargs: dict | None) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        if record_kwargs is not None:
            _put_gate(store, **record_kwargs)
        gate = host.resolve_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id=MILESTONE,
        )
        checkpoint = _checkpoint(tmp_path, gate=gate)
        result = produce_deterministic_steward_result(checkpoint)
        assert gate.user_approval_satisfied is False
        assert checkpoint.kind is GovernanceCheckpointKind.MILESTONE_CLOSE
        assert checkpoint.closure_phase is ClosurePhase.REVIEWED_CLOSURE
        assert result.accepted_frontier_ref is None
    finally:
        store.close()


def test_user_gate_materialization_replays_and_rolls_scope_with_cas(tmp_path: Path) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        first = host.materialize_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id="M2",
            approval=UserGateApproval("friday-user-gate:m2", "a" * 64),
        )
        replay = host.materialize_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id="M2",
            approval=UserGateApproval("friday-user-gate:m2", "a" * 64),
        )
        assert replay == first
        assert replay.revision == 1

        rolled = host.materialize_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id="M3",
            approval=UserGateApproval("friday-user-gate:m3", "b" * 64),
        )
        assert rolled.gate_scope == "M3"
        assert rolled.approval_ref == "friday-user-gate:m3"
        assert rolled.revision == 2

        with pytest.raises(host.TrustedBindingError, match="same milestone-close gate"):
            host.materialize_milestone_close_user_gate(
                store,
                project_id=PROJECT_ID,
                plan_id=PLAN_ID,
                milestone_id="M3",
                approval=UserGateApproval("friday-user-gate:foreign", "c" * 64),
            )
    finally:
        store.close()


def test_user_gate_materialization_satisfies_existing_required_gate(tmp_path: Path) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        _put_gate(store, state=USER_GATE_STATE_REQUIRED)
        satisfied = host.materialize_milestone_close_user_gate(
            store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id=MILESTONE,
            approval=UserGateApproval("approval:m2-close", "a" * 64),
        )
        assert satisfied.state == "satisfied"
        assert satisfied.revision == 2
    finally:
        store.close()


def test_foreign_user_gate_kind_is_not_overwritten(tmp_path: Path) -> None:
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        _put_gate(store, gate_kind="construction")
        with pytest.raises(host.TrustedBindingError, match="foreign gate kind"):
            host.materialize_milestone_close_user_gate(
                store,
                project_id=PROJECT_ID,
                plan_id=PLAN_ID,
                milestone_id=MILESTONE,
                approval=UserGateApproval("friday-user-gate:m2", "a" * 64),
            )
    finally:
        store.close()


def test_plan_close_branch_remains_bounded_and_accepted(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path, all_milestones_closed=True)
    assert checkpoint.kind is GovernanceCheckpointKind.PLAN_CLOSE
    assert checkpoint.closure_phase is ClosurePhase.ACCEPTED_CLOSURE
    assert checkpoint.all_milestones_closed is True


def test_accepted_m2_does_not_approve_or_start_m3(tmp_path: Path) -> None:
    store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    execution = InMemoryExecutionStateStore()
    try:
        state = _review_state()
        store.create(state)
        current = _view()
        next_view = _view("M3", approved=False)
        dispatcher = ExecutionDispatcher(
            ExecutorRegistry(),
            state_store=execution,
            origin_session_ref="session:m2-r1",
        )
        outcome = advance_milestone_once(
            coordinator_store=store,
            execution_store=execution,
            execution_dispatcher=dispatcher,
            coordinator_id=state.coordinator_id,
            live_plan_view=current,
            handoff_resolver=lambda _ref: (_ for _ in ()).throw(
                AssertionError("M3 must not dispatch")
            ),
            next_milestone_view=next_view,
        )
        assert outcome.disposition == DISPOSITION_NEXT_MILESTONE_USER_GATE
        assert outcome.milestone_closure_ready is True
        assert outcome.next_milestone_gate is True
        assert outcome.user_gate_required is True
        assert next_view.milestone_user_approval_satisfied is False
        assert store.get(state.coordinator_id).wi_status["W1"] == "PENDING"
    finally:
        store.close()
