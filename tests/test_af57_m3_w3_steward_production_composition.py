"""AF #57 M3/W3 legacy-only Steward production composition proofs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aota_forge.composition.stewardship import (
    LEGACY_STEWARD_PRODUCTION_PATH_ONLY,
    StewardshipExecutionState,
    StewardshipProductionError,
    create_legacy_stewardship_executor,
)
from aota_forge.governance.project_store import (
    STEWARD_REPLAY_STATE_ACTIVE,
    STEWARD_REPLAY_STATE_COMPLETED,
    STEWARD_REPLAY_STATE_FAILED_CLOSED,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    MaterializationRequest,
    SemanticFactSet,
    StewardshipCheckpoint,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY, TASK_MAIN_RUNTIME_PATH_THIN
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.milestone_review import ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.steward_dispatch import StewardClosureVerdict, StewardResult
from aota_forge.work_plane.steward_finalizer import (
    ClosurePhase,
    InMemoryGitHubPort,
    InMemoryGitPort,
    InMemoryReceiptStore,
    TrustedPlanIdentity,
    TrustedStewardFinalizer,
    TrustedUserGateState,
    make_trusted_binding_for_test,
)

PROJECT_ID = "aota_forge"
PLAN_ID = "plan_wzjcccc_dotcom_aota_hermes_tools_57"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#57"
GOVERNING_REPO = "wzjcccc-dotcom/aota-hermes-tools"
MILESTONE = "M3"
ENTRY_BASE = hashlib.sha1(b"w3-entry").hexdigest()
REVIEWED = hashlib.sha1(b"w3-reviewed").hexdigest()


def _checkpoint(*, semantic: bool, checkpoint_id: str = "cp-w3-close") -> StewardshipCheckpoint:
    readiness = MilestoneClosureReadiness(
        milestone_ref=SemanticReference(ref=MILESTONE),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref=REVIEWED),
        supporting_evidence_refs=("evidence:rv1",),
        blocking_reasons=(),
    )
    return StewardshipCheckpoint(
        checkpoint_id=checkpoint_id,
        kind=GovernanceCheckpointKind.MILESTONE_CLOSE,
        project_id=PROJECT_ID,
        trusted_binding=make_trusted_binding_for_test(PROJECT_ID),
        trusted_plan=TrustedPlanIdentity(
            governing_repo=GOVERNING_REPO,
            plan_issue_number=57,
            milestone_ref=MILESTONE,
            plan_ref=PLAN_REF,
            managed_comments={},
        ),
        plan_id=PLAN_ID,
        milestone_ref=MILESTONE,
        closure_phase=ClosurePhase.REVIEWED_CLOSURE,
        readiness=readiness,
        user_gate=TrustedUserGateState(user_approval_satisfied=False),
        materialization=MaterializationRequest(),
        semantic_facts=(
            SemanticFactSet(architecture_question_refs=("ref:architecture-question",))
            if semantic
            else SemanticFactSet()
        ),
    )


def _coordinator_world(tmp_path: Path):
    coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    live_plan_view = MilestonePlanView(
        plan_authority=PLAN_REF,
        plan_digest="d" * 64,
        milestone_id=MILESTONE,
        entry_base=ENTRY_BASE,
        graph=MilestoneWorkItemGraph(milestone_ref=MILESTONE, work_items=["W1"], dependencies=[]),
        milestone_user_approval_satisfied=False,
    )
    coordinator_store.create(
        TaskMainCoordinatorState(
            coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
            plan_authority=PLAN_REF,
            plan_digest="d" * 64,
            milestone_id=MILESTONE,
            entry_base=ENTRY_BASE,
            origin_task_main_session_ref="session:w3",
            project_id=PROJECT_ID,
            executor_id="hermes",
            work_items=("W1",),
            wi_status={"W1": "PENDING"},
            bindings={},
        )
    )
    return coordinator_store, live_plan_view


def _executor(
    tmp_path: Path,
    dispatch,
    *,
    result_resolver=None,
    governance_store=None,
    coordinator_store=None,
    live_plan_view=None,
):
    if governance_store is None:
        governance_store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    if coordinator_store is None or live_plan_view is None:
        coordinator_store, live_plan_view = _coordinator_world(tmp_path)
    finalizer = TrustedStewardFinalizer(
        git=InMemoryGitPort(),
        github=InMemoryGitHubPort(),
        receipt_store=InMemoryReceiptStore(),
    )
    executor = create_legacy_stewardship_executor(
        runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
        governance_store=governance_store,
        coordinator_store=coordinator_store,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        live_plan_view=live_plan_view,
        finalizer=finalizer,
        semantic_dispatch=dispatch,
        result_resolver=result_resolver,
        origin_session_ref="session:w3",
    )
    return executor, governance_store, coordinator_store, live_plan_view


def _steward_result() -> StewardResult:
    return StewardResult(
        milestone_ref=MILESTONE,
        reviewed_frontier_ref=REVIEWED,
        verdict=StewardClosureVerdict.GOVERNANCE_SYNCED,
        governance_evidence_refs=(REVIEWED,),
    )


def test_deterministic_path_never_dispatches_or_creates_replay(tmp_path: Path) -> None:
    calls: list[object] = []

    def dispatch(_handoff):
        calls.append(_handoff)
        raise AssertionError("deterministic checkpoint must not dispatch Project Steward")

    executor, governance, *_ = _executor(tmp_path, dispatch)
    outcome = executor.execute(_checkpoint(semantic=False))

    assert outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert outcome.receipt is not None
    assert outcome.reentry_evidence is not None
    assert calls == []
    assert governance.get_steward_replay(PROJECT_ID, PLAN_ID, "checkpoint:cp-w3-close") is None


def test_semantic_path_uses_existing_dispatch_and_coordinator_finalizer(tmp_path: Path) -> None:
    calls: list[object] = []

    def dispatch(handoff):
        calls.append(handoff)
        return _steward_result()

    executor, governance, coordinator, _ = _executor(tmp_path, dispatch)
    outcome = executor.execute(_checkpoint(semantic=True))

    assert outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert len(calls) == 1
    assert outcome.semantic_handoff is calls[0]
    assert outcome.steward_result == _steward_result()
    assert outcome.replay is not None
    assert outcome.replay.state == STEWARD_REPLAY_STATE_COMPLETED
    assert outcome.reentry_evidence is not None
    state = coordinator.get(f"{PROJECT_ID}:{MILESTONE}")
    assert state is not None
    assert state.coordinator_id == f"{PROJECT_ID}:{MILESTONE}"
    durable = governance.get_steward_replay(PROJECT_ID, PLAN_ID, "checkpoint:cp-w3-close")
    assert durable is not None and durable.state == STEWARD_REPLAY_STATE_COMPLETED


def test_active_replay_reentry_resolves_result_without_blind_redispatch(tmp_path: Path) -> None:
    first_calls: list[object] = []

    def first_dispatch(handoff):
        first_calls.append(handoff)
        return None

    first, governance, coordinator, live_view = _executor(tmp_path, first_dispatch)
    first_outcome = first.execute(_checkpoint(semantic=True))
    assert first_outcome.execution_state is StewardshipExecutionState.IN_FLIGHT
    assert first_outcome.replay is not None
    assert first_outcome.replay.state == STEWARD_REPLAY_STATE_ACTIVE

    governance.close()
    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("active replay must resolve or wait, never redispatch blindly")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
        result_resolver=lambda _record: _steward_result(),
        governance_store=reopened,
        coordinator_store=coordinator,
        live_plan_view=live_view,
    )
    second_outcome = second.execute(_checkpoint(semantic=True))

    assert second_outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert second_outcome.replay is not None
    assert second_outcome.replay.state == STEWARD_REPLAY_STATE_COMPLETED
    assert first_calls and second_calls == []
    reopened.close()


def test_unknown_dispatch_outcome_is_failed_closed_and_not_retried(tmp_path: Path) -> None:
    calls: list[object] = []

    def unknown_dispatch(handoff):
        calls.append(handoff)
        raise RuntimeError("transport outcome unknown")

    executor, governance, *_ = _executor(tmp_path, unknown_dispatch)
    checkpoint = _checkpoint(semantic=True)
    outcome = executor.execute(checkpoint)

    assert outcome.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert outcome.error_code == "SEMANTIC_DISPATCH_UNKNOWN_OUTCOME"
    assert outcome.replay is not None
    assert outcome.replay.state == STEWARD_REPLAY_STATE_FAILED_CLOSED

    retry = executor.execute(checkpoint)
    assert retry.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert len(calls) == 1
    governance.close()


def test_thin_runtime_is_rejected_without_fallback(tmp_path: Path) -> None:
    executor, governance, coordinator, live_view = _executor(tmp_path, lambda _handoff: None)
    with pytest.raises(StewardshipProductionError, match="legacy-only"):
        create_legacy_stewardship_executor(
            runtime_path=TASK_MAIN_RUNTIME_PATH_THIN,
            governance_store=governance,
            coordinator_store=coordinator,
            coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
            live_plan_view=live_view,
            finalizer=executor.finalizer,
            semantic_dispatch=lambda _handoff: None,
        )
    assert LEGACY_STEWARD_PRODUCTION_PATH_ONLY is True
    governance.close()
