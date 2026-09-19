"""AF #57 M3/W3 legacy-only Steward production composition proofs."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.composition.stewardship import (
    LEGACY_STEWARD_PRODUCTION_PATH_ONLY,
    StewardshipExecutionState,
    StewardshipProductionError,
    create_legacy_stewardship_executor,
)
from aota_forge.composition.task_main import create_task_main_runner
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectPlanRecord,
    STEWARD_REPLAY_STATE_ACTIVE,
    STEWARD_REPLAY_STATE_COMPLETED,
    STEWARD_REPLAY_STATE_FAILED_CLOSED,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import local_plan_authority_reference
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    MaterializationRequest,
    SemanticFactSet,
    StewardshipCheckpoint,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY, TASK_MAIN_RUNTIME_PATH_THIN
import aota_forge.runtime.task_main.runner as runner_module
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_MILESTONE_CLOSURE_READY,
    RunnerOutcome,
)
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
    FinalizerError,
    FinalizerFailure,
    InMemoryGitHubPort,
    InMemoryGitPort,
    InMemoryReceiptStore,
    TrustedPlanIdentity,
    TrustedStewardFinalizer,
    TrustedUserGateState,
    make_trusted_binding_for_test,
)
from aota_forge.work_plane.materialization_receipt import MaterializationReceipt
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "aota_forge"
PLAN_ID = "plan_wzjcccc_dotcom_aota_hermes_tools_57"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#57"
GOVERNING_REPO = "wzjcccc-dotcom/aota-hermes-tools"
MILESTONE = "M3"
ENTRY_BASE = hashlib.sha1(b"w3-entry").hexdigest()
REVIEWED = hashlib.sha1(b"w3-reviewed").hexdigest()


def _checkpoint(
    *,
    semantic: bool,
    checkpoint_id: str = "cp-w3-close",
    semantic_ref: str = "ref:architecture-question",
) -> StewardshipCheckpoint:
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
            SemanticFactSet(architecture_question_refs=(semantic_ref,))
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
    governance_store=None,
    coordinator_store=None,
    live_plan_view=None,
    result_sandbox=None,
    finalizer=None,
    local_plan_lifecycle_coordinator=None,
):
    if result_sandbox is None:
        result_sandbox = _sandbox(tmp_path)
    if governance_store is None:
        governance_store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    if coordinator_store is None or live_plan_view is None:
        coordinator_store, live_plan_view = _coordinator_world(tmp_path)
    if finalizer is None:
        finalizer = TrustedStewardFinalizer(
            git=InMemoryGitPort(),
            github=InMemoryGitHubPort(),
            receipt_store=None if result_sandbox is not None else InMemoryReceiptStore(),
        )
    executor = create_legacy_stewardship_executor(
        runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
        governance_store=governance_store,
        coordinator_store=coordinator_store,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        live_plan_view=live_plan_view,
        finalizer=finalizer,
        semantic_dispatch=dispatch,
        result_sandbox=result_sandbox,
        origin_session_ref="session:w3",
        local_plan_lifecycle_coordinator=local_plan_lifecycle_coordinator,
    )
    return executor, governance_store, coordinator_store, live_plan_view


def _sandbox(tmp_path: Path) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="ws-w3",
        workspace_root=str(tmp_path),
        project_id=PROJECT_ID,
        project_root=str(tmp_path),
        worktree_id="wt-w3",
        worktree_root=str(tmp_path),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


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


def test_plan_close_retires_local_plan_only_after_trusted_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_ref = local_plan_authority_reference(PROJECT_ID, PLAN_ID)
    local_binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_ref,
    )
    trusted_plan = TrustedPlanIdentity(
        governing_repo=None,
        plan_issue_number=None,
        milestone_ref=MILESTONE,
        plan_ref=local_ref,
        managed_comments={},
        authority_binding=local_binding,
    )
    checkpoint = replace(
        _checkpoint(semantic=False, checkpoint_id="cp-w3-plan-close"),
        kind=GovernanceCheckpointKind.PLAN_CLOSE,
        trusted_plan=trusted_plan,
        closure_phase=ClosurePhase.ACCEPTED_CLOSURE,
        user_gate=TrustedUserGateState(user_approval_satisfied=True),
        all_milestones_closed=True,
    )
    coordinator_store, live_plan_view = _coordinator_world(tmp_path)
    live_plan_view = replace(live_plan_view, plan_authority=local_ref)
    state = coordinator_store.get(f"{PROJECT_ID}:{MILESTONE}")
    assert state is not None
    coordinator_store.close()
    coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "local-coordinator.json")
    coordinator_store.create(replace(state, plan_authority=local_ref))
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=local_binding,
        )
    )
    executor_probe, _, _, _ = _executor(
        tmp_path,
        lambda _handoff: None,
        governance_store=governance,
        coordinator_store=coordinator_store,
        live_plan_view=live_plan_view,
    )
    monkeypatch.setattr(
        runner_module.TaskMainMilestoneRunner,
        "advance_once",
        lambda self, *, session_available=True: RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=self.coordinator_id,
            coordinator_revision=2,
            milestone_closure_ready=True,
        ),
    )
    composed = create_task_main_runner(
        coordinator_store=coordinator_store,
        execution_store=InMemoryExecutionStateStore(),
        execution_dispatcher=ExecutionDispatcher(ExecutorRegistry()),
        live_plan_view=live_plan_view,
        handoff_resolver=lambda _ref: pytest.fail("Plan Close must not dispatch a Worker"),
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        stewardship_checkpoint=checkpoint,
        stewardship_dispatch=lambda _handoff: None,
        stewardship_sandbox=_sandbox(tmp_path),
        stewardship_finalizer=executor_probe.finalizer,
        governance_store=governance,
    )

    outcome = composed.advance_once()

    assert outcome.milestone_closure_ready is True
    assert composed.stewardship_outcome is not None
    assert composed.stewardship_outcome.execution_state is StewardshipExecutionState.FINALIZED
    retired = governance.get_plan(PROJECT_ID, PLAN_ID)
    assert retired is not None
    assert retired.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    assert retired.revision == 2

    replay = composed.advance_once()
    assert replay.milestone_closure_ready is True
    assert composed.stewardship_outcome is not None
    assert composed.stewardship_outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert governance.get_plan(PROJECT_ID, PLAN_ID).revision == 2
    governance.close()


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
    durable = governance.get_steward_replay(PROJECT_ID, PLAN_ID, outcome.replay.lineage_id)
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
    first.result_owner.persist_steward_result(first_outcome.replay, _steward_result())

    governance.close()
    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("active replay must resolve or wait, never redispatch blindly")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
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


def test_production_factory_owns_steward_composition_at_closure_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def dispatch(handoff):
        calls.append(handoff)
        return _steward_result()

    _executor_probe, governance, coordinator, live_view = _executor(tmp_path, dispatch)
    sandbox = _sandbox(tmp_path)

    def closure_ready(self, *, session_available=True):
        return RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=self.coordinator_id,
            coordinator_revision=1,
            milestone_closure_ready=True,
        )

    monkeypatch.setattr(runner_module.TaskMainMilestoneRunner, "advance_once", closure_ready)
    composed = create_task_main_runner(
        coordinator_store=coordinator,
        execution_store=InMemoryExecutionStateStore(),
        execution_dispatcher=ExecutionDispatcher(ExecutorRegistry()),
        live_plan_view=live_view,
        handoff_resolver=lambda _ref: pytest.fail("legacy dispatch must not run in closure seam"),
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        stewardship_checkpoint=_checkpoint(semantic=True),
        stewardship_dispatch=dispatch,
        stewardship_sandbox=sandbox,
        stewardship_finalizer=_executor_probe.finalizer,
        governance_store=governance,
    )

    outcome = composed.advance_once()
    assert outcome.milestone_closure_ready is True
    assert composed.stewardship_outcome is not None
    assert composed.stewardship_outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert len(calls) == 1
    governance.close()


def test_replay_identity_includes_checkpoint_kind_frontier_and_residual_digest(tmp_path: Path) -> None:
    executor, governance, *_ = _executor(tmp_path, lambda _handoff: None)
    first = executor._new_replay_record(
        _checkpoint(semantic=True, semantic_ref="ref:architecture-question-a"),
        PLAN_ID,
    )
    second = executor._new_replay_record(
        _checkpoint(semantic=True, semantic_ref="ref:architecture-question-b"),
        PLAN_ID,
    )

    assert first.lineage_id != second.lineage_id
    assert first.request_digest != second.request_digest
    assert "residual:" in first.lineage_id
    governance.close()


def test_completed_replay_hydrates_durable_result_without_redispatch(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    checkpoint = _checkpoint(semantic=True)
    first_calls: list[object] = []

    def first_dispatch(handoff):
        first_calls.append(handoff)
        return _steward_result()

    first, governance, coordinator, live_view = _executor(
        tmp_path,
        first_dispatch,
        result_sandbox=sandbox,
    )
    first_outcome = first.execute(checkpoint)
    assert first_outcome.execution_state is StewardshipExecutionState.FINALIZED
    governance.close()
    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("completed replay must hydrate durable result, not redispatch")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
        governance_store=reopened,
        coordinator_store=coordinator,
        live_plan_view=live_view,
        result_sandbox=sandbox,
    )
    second_outcome = second.execute(checkpoint)

    assert second_outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert second_outcome.steward_result == _steward_result()
    assert first_calls and second_calls == []
    reopened.close()


def test_completed_replay_without_durable_result_fails_closed(tmp_path: Path) -> None:
    checkpoint = _checkpoint(semantic=True)
    first_calls: list[object] = []

    def first_dispatch(handoff):
        first_calls.append(handoff)
        return _steward_result()

    first, governance, coordinator, live_view = _executor(tmp_path, first_dispatch)
    first_outcome = first.execute(checkpoint)
    assert first_outcome.execution_state is StewardshipExecutionState.FINALIZED
    for receipt_path in (tmp_path / ".aota" / "task_return_receipts").glob("*.json"):
        receipt_path.unlink()
    governance.close()
    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("missing completed result must not redispatch")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
        governance_store=reopened,
        coordinator_store=coordinator,
        live_plan_view=live_view,
    )
    second_outcome = second.execute(checkpoint)

    assert second_outcome.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert second_outcome.error_code == "COMPLETED_RESULT_UNAVAILABLE"
    assert first_calls and second_calls == []
    reopened.close()


class _PartialThenCompleteFinalizer(TrustedStewardFinalizer):
    def __init__(self, partial: MaterializationReceipt, completed: MaterializationReceipt) -> None:
        super().__init__(
            git=InMemoryGitPort(),
            github=InMemoryGitHubPort(),
            receipt_store=InMemoryReceiptStore(),
        )
        self.calls = 0
        self.partial = partial
        self.completed = completed

    def finalize(self, finalizer_input, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise FinalizerError(
                FinalizerFailure.MATERIALIZATION_PARTIAL_FAILURE,
                "one typed operation was applied before the crash window",
                receipt=self.partial,
            )
        return self.completed


def _receipt_for(
    executor, result: StewardResult, *, final_status: str, receipt_id: str
) -> MaterializationReceipt:
    return MaterializationReceipt(
        receipt_id=receipt_id,
        project_id=PROJECT_ID,
        plan_ref=PLAN_REF,
        milestone_ref=MILESTONE,
        closure_phase=ClosurePhase.REVIEWED_CLOSURE.value,
        steward_digest=executor._steward_result_digest(result),
        binding_digest="b" * 64,
        materialization_scope={},
        idempotency_key="w3-partial-recovery",
        final_status=final_status,
        reconciled_at="2026-09-18T00:00:00+00:00",
        receipt_digest="",
    ).with_digest()


def test_partial_finalizer_receipt_reenters_without_redispatch(tmp_path: Path) -> None:
    result = _steward_result()
    probe, governance, coordinator, live_view = _executor(tmp_path, lambda _handoff: result)
    sandbox = _sandbox(tmp_path)
    partial = _receipt_for(probe, result, final_status="PARTIAL", receipt_id="partial-w3")
    completed = _receipt_for(probe, result, final_status="APPLIED", receipt_id="complete-w3")
    finalizer = _PartialThenCompleteFinalizer(partial, completed)
    dispatch_calls: list[object] = []

    def dispatch(handoff):
        dispatch_calls.append(handoff)
        return result

    executor = create_legacy_stewardship_executor(
        runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
        governance_store=governance,
        coordinator_store=coordinator,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        live_plan_view=live_view,
        finalizer=finalizer,
        semantic_dispatch=dispatch,
        result_sandbox=sandbox,
        origin_session_ref="session:w3",
    )
    checkpoint = _checkpoint(semantic=True)

    first = executor.execute(checkpoint)
    second = executor.execute(checkpoint)

    assert first.execution_state is StewardshipExecutionState.IN_FLIGHT
    assert first.replay is not None and first.replay.state == STEWARD_REPLAY_STATE_ACTIVE
    assert second.execution_state is StewardshipExecutionState.FINALIZED
    assert second.replay is not None and second.replay.state == STEWARD_REPLAY_STATE_COMPLETED
    assert len(dispatch_calls) == 1
    assert finalizer.calls == 2
    governance.close()
