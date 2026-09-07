"""M3/W1 Persistent task-main Coordinator — deterministic acceptance tests.

Proves the restart-safe, Plan-bound, durable coordinator foundation over the
accepted M2 runtime without rebuilding it:

* approved Milestone activates; unapproved refuses (USER_GATE_REQUIRED)
* coordinator state persists / reopens; Plan authority + session ref persist
* ready Work Items are deterministic from the DAG (no LLM choice)
* dispatch reuses TaskHandoff compiler + M2 dispatcher idempotency + admission
* restart recovery C1-C4 with FRESH objects (never the same Python object)
* user gate stops dispatch; approval is trusted input, never self-set
* Plan drift + session loss fail closed
* ExecutionStateStore / Hermes session DB / CARD semantics stay out of W1

``PERSISTENCE_REQUIRES_FOREVER_PROCESS=no``: every restart test closes the
stores and rebuilds all runtime objects from the same files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.roles import CANONICAL_ROLES
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    PlanDriftError,
    SessionRecoveryRequiredError,
    TaskMainCoordinator,
    activate_milestone,
    dispatch_identity_for,
    evaluate_ready_work_items,
    recover_coordinator,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    CoordinatorNotFoundError,
    FileBackedTaskMainCoordinatorStore,
    StaleCoordinatorRevisionError,
    TaskMainCoordinatorStore,
)
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.result_card import project_worker_result_card

FAKE_EXECUTOR_ID = "m3w1-fake"
TEST_SCOPE = "m3w1-fake:coder"
ORIGIN_SESSION = "20260907_taskmain_m3w1_disposable"
PLAN_AUTHORITY = "wzjcccc-dotcom/aota-hermes-tools#36"
ENTRY_BASE = "94206e90f0769c60127c5fbb0cb9a8ef2b88fa64"
PROJECT_ID = "aota_forge"
DISPATCH_STAMP = "2026-09-07T00:00:00+00:00"


def _body(marker: str) -> str:
    return (
        "# [PLAN] M3/W1 fixture body\n\n"
        "## 1. Current State\n"
        "```text\n"
        "PLAN_STATUS=in-progress\n"
        "CURRENT_MILESTONE=M3\n"
        "M3_STATUS=in_progress\n"
        f"CURRENT_BLOCKER={marker}\n"
        "```\n"
    )


def _plan_digest(marker: str = "none", *, revision: str = "rev-m3w1-a") -> tuple[str, str | None]:
    doc = normalize_portable_plan(_body(marker), source_revision=revision)
    return portable_plan_digest(doc), doc.source_revision


def _graph(work_items: list[str], dependencies: list[list[str]] | None = None) -> MilestoneWorkItemGraph:
    return MilestoneWorkItemGraph(
        milestone_ref="M3",
        work_items=list(work_items),
        dependencies=[list(edge) for edge in (dependencies or [])],
    )


def _view(
    work_items: list[str],
    dependencies: list[list[str]] | None = None,
    *,
    approved: bool = True,
    marker: str = "none",
    amendment: bool = False,
) -> MilestonePlanView:
    digest, revision = _plan_digest(marker)
    return MilestonePlanView(
        plan_authority=PLAN_AUTHORITY,
        plan_digest=digest,
        plan_source_revision=revision,
        milestone_id="M3",
        entry_base=ENTRY_BASE,
        graph=_graph(work_items, dependencies),
        milestone_user_approval_satisfied=approved,
        plan_amendment_required=amendment,
    )


def _handoff(work_item_id: str) -> TaskHandoff:
    return TaskHandoff(
        work_role="coder",
        task_kind="m3w1-proof",
        objective=f"m3w1 governed proof objective for {work_item_id}",
        bounded_scope=f"m3w1 bounded scope for {work_item_id}",
        validation_expectations=(f"cheap validation for {work_item_id}",),
        semantic_stop_expectations=(f"semantic stop for {work_item_id}",),
        work_item_ref=SemanticReference(ref=work_item_id),
    )


def _resolver(work_items: list[str]) -> Callable[[str], TaskHandoff]:
    table = {wi: _handoff(wi) for wi in work_items}

    def _resolve(work_item_id: str) -> TaskHandoff:
        return table[work_item_id]

    return _resolve


class M3W1FakeAdapter(ExecutorAdapter):
    """Executor-neutral stable-handle fake; class counter proves no redispatch."""

    total_dispatches = 0

    def __init__(self) -> None:
        self.physical_dispatch_count = 0

    @classmethod
    def reset_world(cls) -> None:
        cls.total_dispatches = 0

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id=FAKE_EXECUTOR_ID,
            adapter_kind="m3w1_fake_test_double",
            supported_execution_modes=("async",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    @staticmethod
    def handle_for(package: ExecutionPackage) -> str:
        outcome = package.constraints.get("fake_outcome", "running")
        return f"m3w1-fake||{package.canonical_task_id}||{outcome}"

    @staticmethod
    def _parse(handle: str) -> tuple[str, str]:
        _, task_id, outcome = handle.split("||")
        return task_id, outcome

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.physical_dispatch_count += 1
        type(self).total_dispatches += 1
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=self.handle_for(package),
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time=DISPATCH_STAMP,
        )

    def _state(self, outcome: str) -> CanonicalTaskState:
        if outcome in {"running", "slow"}:
            return CanonicalTaskState.RUNNING
        if outcome == "failed":
            return CanonicalTaskState.FAILED
        return CanonicalTaskState.COMPLETED

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        task_id, outcome = self._parse(adapter_handle)
        return TaskStatusResult(canonical_task_id=task_id, state=self._state(outcome))

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        task_id, outcome = self._parse(adapter_handle)
        state = self._state(outcome)
        if outcome == "failed":
            return CanonicalResult.failure(
                canonical_task_id=task_id,
                executor_id=FAKE_EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="m3w1 fake scripted failure",
                correlation_id=f"corr-{task_id}",
                canonical_task_state=state.value,
            )
        return CanonicalResult.success(
            canonical_task_id=task_id,
            executor_id=FAKE_EXECUTOR_ID,
            result_data={"proof": "m3-w1-durable"},
            stdout_summary="raw-worker-stdout-stays-mechanical",
            stderr_summary="raw-worker-stderr-stays-mechanical",
            execution_stats={"duration_ms": 5},
            correlation_id=f"corr-{task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(
            canonical_task_id=canonical_task_id, cancelled=True, state=CanonicalTaskState.CANCELLED
        )

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


class World:
    """Fresh-object world: file stores + fresh dispatcher per construction."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        name: str = "default",
        limits: dict[str, int] | None = None,
        use_completion: bool = False,
    ) -> None:
        self.coord_path = tmp_path / f"{name}.coordinator.json"
        self.exec_path = tmp_path / f"{name}.execution.json"
        self.coord_store = FileBackedTaskMainCoordinatorStore(self.coord_path)
        self.exec_store = FileBackedExecutionStateStore(self.exec_path)
        self.adapter = M3W1FakeAdapter()
        registry = ExecutorRegistry()
        registry.register(self.adapter)
        from aota_forge.core.execution.durable_state import OriginSessionRef

        self.dispatcher = ExecutionDispatcher(
            registry,
            state_store=self.exec_store,
            origin_session_ref=OriginSessionRef(value=ORIGIN_SESSION),
            admission_scope_resolver=(lambda package: TEST_SCOPE),
        )
        self.completion: DurableCompletionCoordinator | None = None
        if use_completion:
            self.completion = DurableCompletionCoordinator(
                dispatcher=self.dispatcher,
                store=self.exec_store,
                transport=None,
                admission_limits=limits if limits is not None else {TEST_SCOPE: 10},
            )

    def close(self) -> None:
        self.coord_store.close()
        self.exec_store.close()

    def reopen(self, *, limits: dict[str, int] | None = None, use_completion: bool = False) -> World:
        """Simulate process restart: close files, rebuild every object fresh."""
        tmp_path = self.coord_path.parent
        name = self.coord_path.stem.replace(".coordinator", "")
        self.close()
        return World(tmp_path, name=name, limits=limits, use_completion=use_completion)

    def activate(self, view: MilestonePlanView, **kwargs: Any) -> TaskMainCoordinator:
        return activate_milestone(
            store=self.coord_store,
            plan_view=view,
            origin_task_main_session_ref=ORIGIN_SESSION,
            execution_dispatcher=self.dispatcher,
            completion_coordinator=self.completion,
            executor_id=FAKE_EXECUTOR_ID,
            project_id=PROJECT_ID,
            **kwargs,
        )

    def recover(self, view: MilestonePlanView, coordinator_id: str, **kwargs: Any) -> TaskMainCoordinator:
        return recover_coordinator(
            store=self.coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=view,
            execution_dispatcher=self.dispatcher,
            completion_coordinator=self.completion,
            session_available=True,
            **kwargs,
        )


@pytest.fixture(autouse=True)
def _reset_fake_world() -> None:
    M3W1FakeAdapter.reset_world()


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(tmp_path)
    yield w
    w.close()


# ---------------------------------------------------------------------------
# Activation + approval gate
# ---------------------------------------------------------------------------


class TestActivation:
    def test_approved_milestone_activates(self, world: World) -> None:
        view = _view(["W1", "W2"], [["W1", "W2"]])
        handle = world.activate(view)
        state = handle.refresh()
        assert state.status == CoordinatorStatus.ACTIVE
        assert state.plan_authority == PLAN_AUTHORITY
        assert state.milestone_id == "M3"
        assert state.entry_base == ENTRY_BASE
        assert state.origin_task_main_session_ref == ORIGIN_SESSION
        assert state.user_approval_satisfied is True
        assert state.wi_status == {"W1": "PENDING", "W2": "PENDING"}
        assert state.bindings == {}
        assert state.coordinator_revision == 1
        assert world.coord_store.list_active() != []

    def test_unapproved_milestone_refuses_activation(self, world: World) -> None:
        view = _view(["W1"], approved=False)
        handle = world.activate(view)
        assert handle.refresh().status == CoordinatorStatus.USER_GATE_REQUIRED
        report = handle.dispatch_ready(_resolver(["W1"]))
        assert report.gate_reason == "USER_GATE_REQUIRED"
        assert report.ready == () and report.dispatched == () and report.deferred == ()
        assert M3W1FakeAdapter.total_dispatches == 0, "user gate must stop physical dispatch"

    def test_trusted_approval_resumes_gated_coordinator(self, world: World) -> None:
        gated = world.activate(_view(["W1"], approved=False))
        assert gated.refresh().status == CoordinatorStatus.USER_GATE_REQUIRED
        # Approval arrives as new trusted governed input — never set by task-main.
        resumed = world.activate(_view(["W1"], approved=True))
        assert resumed.refresh().status == CoordinatorStatus.ACTIVE
        assert world.coord_store.list_all() != [] and len(world.coord_store.list_all()) == 1
        report = resumed.dispatch_ready(_resolver(["W1"]))
        assert [d.work_item_id for d in report.dispatched] == ["W1"]

    def test_reactivation_is_idempotent(self, world: World) -> None:
        view = _view(["W1"])
        first = world.activate(view)
        second = world.activate(view)
        assert first.coordinator_id == second.coordinator_id
        assert len(world.coord_store.list_all()) == 1, "no duplicate activation"

    def test_dispatcher_without_store_fails_closed(self, world: World) -> None:
        from aota_forge.runtime.task_main.coordinator import CoordinatorRuntimeError

        registry = ExecutorRegistry()
        registry.register(M3W1FakeAdapter())
        bare = ExecutionDispatcher(registry, state_store=None)
        with pytest.raises(CoordinatorRuntimeError, match="restart-safe"):
            activate_milestone(
                store=world.coord_store,
                plan_view=_view(["W1"]),
                origin_task_main_session_ref=ORIGIN_SESSION,
                execution_dispatcher=bare,
                executor_id=FAKE_EXECUTOR_ID,
                project_id=PROJECT_ID,
            )

    def test_graph_milestone_mismatch_fails_closed(self, world: World) -> None:
        digest, revision = _plan_digest()
        with pytest.raises(ValueError, match="contradicts"):
            MilestonePlanView(
                plan_authority=PLAN_AUTHORITY,
                plan_digest=digest,
                plan_source_revision=revision,
                milestone_id="M9",
                entry_base=ENTRY_BASE,
                graph=_graph(["W1"]),
                milestone_user_approval_satisfied=True,
            )


# ---------------------------------------------------------------------------
# Deterministic ready evaluation
# ---------------------------------------------------------------------------


class TestReadyEvaluation:
    def test_roots_ready_chain_blocked(self, world: World) -> None:
        handle = world.activate(_view(["W1", "W2", "W3"], [["W1", "W2"], ["W2", "W3"]]))
        assert handle.ready_work_items() == ("W1",)

    def test_multiple_ready_represented(self, world: World) -> None:
        handle = world.activate(_view(["W1", "W2", "W3"], [["W1", "W3"], ["W2", "W3"]]))
        assert handle.ready_work_items() == ("W1", "W2")

    def test_active_not_ready_and_no_redispatch(self, world: World) -> None:
        handle = world.activate(_view(["W1", "W2"], [["W1", "W2"]]))
        first = handle.dispatch_ready(_resolver(["W1", "W2"]))
        assert [d.work_item_id for d in first.dispatched] == ["W1"]
        assert handle.ready_work_items() == ()
        second = handle.dispatch_ready(_resolver(["W1", "W2"]))
        assert second.dispatched == () and second.deferred == ()
        assert M3W1FakeAdapter.total_dispatches == 1

    def test_completion_pending_unblocks_successor(self, world: World) -> None:
        handle = world.activate(_view(["W1", "W2"], [["W1", "W2"]]))
        handle.dispatch_ready(_resolver(["W1", "W2"]))
        stored = world.exec_store.get(handle.active_bindings()["W1"]["canonical_task_id"])
        assert stored is not None and not stored.canonical_task_state.is_terminal
        # Mechanically observe terminal truth through M2 (success + CARD durable).
        canonical = world.dispatcher.result(stored.canonical_task_id)
        card = project_worker_result_card(
            canonical,
            ResultGovernanceProjection.success(),
            "coder",
            summary="m3w1 terminal proof for W1",
        )
        world.dispatcher.attach_worker_result_card(stored.canonical_task_id, card)
        observed = handle.observe_terminal_completions()
        assert observed == ("W1",)
        assert handle.ready_work_items() == ("W2",)

    def test_gate_yields_empty_ready(self, world: World) -> None:
        handle = world.activate(_view(["W1"], approved=False))
        assert handle.ready_work_items() == ()
        assert evaluate_ready_work_items(
            graph=_graph(["W1"]), wi_status={"W1": "PENDING"}, gate_blocked=True
        ) == ()


# ---------------------------------------------------------------------------
# Dispatch identity + M2 idempotency reuse
# ---------------------------------------------------------------------------


class TestDispatchIdentity:
    def test_deterministic_identity_and_binding(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        report = handle.dispatch_ready(_resolver(["W1"]))
        (dispatched,) = report.dispatched
        expected_cid, expected_key, _ = dispatch_identity_for(
            project_id=PROJECT_ID,
            plan_authority=PLAN_AUTHORITY,
            milestone_id="M3",
            work_item_id="W1",
        )
        assert dispatched.canonical_task_id == expected_cid
        assert dispatched.idempotency_key == expected_key
        binding = handle.active_bindings()["W1"]
        assert binding["canonical_task_id"] == expected_cid
        assert binding["idempotency_key"] == expected_key
        assert binding["attempt"] == 1
        stored = world.exec_store.get(expected_cid)
        assert stored is not None and stored.idempotency_key == expected_key

    def test_crash_before_local_update_replays_without_redispatch(self, world: World) -> None:
        """Coordinator crashes after M2 dispatch but before its own CAS.

        The M2 record exists; the coordinator binding does not. Recovery must
        resolve the existing execution via deterministic idempotency — zero
        additional physical dispatches.
        """
        handle = world.activate(_view(["W1"]))
        canonical_task_id, idempotency_key, correlation_id = dispatch_identity_for(
            project_id=PROJECT_ID,
            plan_authority=PLAN_AUTHORITY,
            milestone_id="M3",
            work_item_id="W1",
        )
        package = compile_handoff_to_execution_package(
            _handoff("W1"),
            TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=PROJECT_ID),
            package_id=f"{canonical_task_id}:pkg",
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        world.dispatcher.dispatch(package, FAKE_EXECUTOR_ID)
        assert M3W1FakeAdapter.total_dispatches == 1
        assert handle.active_bindings() == {}, "simulated crash: binding never persisted"

        report = handle.dispatch_ready(_resolver(["W1"]))
        assert [d.work_item_id for d in report.dispatched] == ["W1"]
        assert M3W1FakeAdapter.total_dispatches == 1, "COORDINATOR_RESTART_DUPLICATE_DISPATCH=no"
        assert handle.active_bindings()["W1"]["canonical_task_id"] == canonical_task_id

    def test_handoff_identity_mismatch_fails_closed(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))

        def _bad_resolver(work_item_id: str) -> TaskHandoff:
            assert work_item_id == "W1"
            return _handoff("W2")

        with pytest.raises(CoordinatorBindingError, match="contradicts"):
            handle.dispatch_ready(_bad_resolver)
        assert M3W1FakeAdapter.total_dispatches == 0

    def test_admission_defers_without_dispatch(self, tmp_path: Path) -> None:
        w = World(tmp_path, name="admit", limits={TEST_SCOPE: 1}, use_completion=True)
        try:
            handle = w.activate(_view(["W1", "W2"]))
            report = handle.dispatch_ready(_resolver(["W1", "W2"]))
            assert [d.work_item_id for d in report.dispatched] == ["W1"]
            assert report.deferred == ("W2",), "M2 concurrency bound defers, never oversubscribes"
            assert M3W1FakeAdapter.total_dispatches == 1
        finally:
            w.close()


# ---------------------------------------------------------------------------
# Restart recovery C1-C4 (fresh objects every time)
# ---------------------------------------------------------------------------


class TestRestartRecovery:
    def test_c1_activation_before_dispatch(self, tmp_path: Path) -> None:
        w1 = World(tmp_path, name="c1")
        view = _view(["W1", "W2"], [["W1", "W2"]])
        first = w1.activate(view)
        coordinator_id = first.coordinator_id
        w2 = w1.reopen()
        try:
            recovered = w2.recover(view, coordinator_id)
            assert recovered.refresh().status == CoordinatorStatus.ACTIVE
            assert recovered.ready_work_items() == ("W1",), "ready recomputed, no duplicate activation"
            assert len(w2.coord_store.list_all()) == 1
            report = recovered.dispatch_ready(_resolver(["W1", "W2"]))
            assert [d.work_item_id for d in report.dispatched] == ["W1"]
            assert M3W1FakeAdapter.total_dispatches == 1
        finally:
            w2.close()

    def test_c2_after_one_dispatch(self, tmp_path: Path) -> None:
        w1 = World(tmp_path, name="c2")
        view = _view(["W1", "W2"], [["W1", "W2"]])
        first = w1.activate(view)
        first.dispatch_ready(_resolver(["W1", "W2"]))
        coordinator_id = first.coordinator_id
        expected_cid = first.active_bindings()["W1"]["canonical_task_id"]
        w2 = w1.reopen()
        try:
            recovered = w2.recover(view, coordinator_id)
            bindings = recovered.active_bindings()
            assert list(bindings) == ["W1"]
            assert bindings["W1"]["canonical_task_id"] == expected_cid
            report = recovered.dispatch_ready(_resolver(["W1", "W2"]))
            assert report.dispatched == (), "same active canonical_task_id recovered, no redispatch"
            assert M3W1FakeAdapter.total_dispatches == 1
        finally:
            w2.close()

    def test_c3_multiple_active(self, tmp_path: Path) -> None:
        w1 = World(tmp_path, name="c3", limits={TEST_SCOPE: 5}, use_completion=True)
        view = _view(["W1", "W2", "W3"], [["W1", "W3"], ["W2", "W3"]])
        first = w1.activate(view)
        report = first.dispatch_ready(_resolver(["W1", "W2", "W3"]))
        assert sorted(d.work_item_id for d in report.dispatched) == ["W1", "W2"]
        coordinator_id = first.coordinator_id
        before = dict(first.active_bindings())
        w2 = w1.reopen(limits={TEST_SCOPE: 5}, use_completion=True)
        try:
            recovered = w2.recover(view, coordinator_id)
            after = recovered.active_bindings()
            assert sorted(after) == ["W1", "W2"]
            assert {wi: after[wi]["canonical_task_id"] for wi in after} == {
                wi: before[wi]["canonical_task_id"] for wi in before
            }
            again = recovered.dispatch_ready(_resolver(["W1", "W2", "W3"]))
            assert again.dispatched == () and M3W1FakeAdapter.total_dispatches == 2
        finally:
            w2.close()

    def test_c4_terminal_pending_reconciliation(self, tmp_path: Path) -> None:
        """Worker terminal + CARD durable, not yet semantically reconciled.

        W1 preserves/identifies completion-pending-reconciliation across
        restart and never applies CARD semantics.
        """
        w1 = World(tmp_path, name="c4")
        view = _view(["W1", "W2"], [["W1", "W2"]])
        first = w1.activate(view)
        first.dispatch_ready(_resolver(["W1", "W2"]))
        task_id = first.active_bindings()["W1"]["canonical_task_id"]
        canonical = w1.dispatcher.result(task_id)
        card = project_worker_result_card(
            canonical,
            ResultGovernanceProjection.success(),
            "coder",
            summary="m3w1 c4 terminal proof",
        )
        w1.dispatcher.attach_worker_result_card(task_id, card)
        coordinator_id = first.coordinator_id
        w2 = w1.reopen()
        try:
            recovered = w2.recover(view, coordinator_id)
            observed = recovered.observe_terminal_completions()
            assert observed == ("W1",)
            state = recovered.refresh()
            assert state.wi_status["W1"] == WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value
            binding = state.bindings["W1"]
            assert binding["completion_ref"] == task_id
            assert binding["completion_card_digest"] == card.compute_card_digest()
            # No semantic application: coordinator state carries no outcome,
            # acceptance, review, or closure fields.
            payload = state.to_dict()
            for forbidden in ("outcome", "acceptance", "review", "closure", "repair", "ack"):
                assert forbidden not in payload
                assert forbidden not in str(payload.get("wi_status", {}))
            assert recovered.ready_work_items() == ("W2",)
        finally:
            w2.close()


# ---------------------------------------------------------------------------
# Plan drift + session loss (fail closed)
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_plan_drift_fails_closed(self, world: World) -> None:
        handle = world.activate(_view(["W1"], marker="none"))
        coordinator_id = handle.coordinator_id
        drifted = _view(["W1"], marker="drifted")
        with pytest.raises(PlanDriftError):
            world.recover(drifted, coordinator_id)
        assert world.coord_store.get(coordinator_id).status == CoordinatorStatus.ACTIVE

    def test_plan_authority_change_fails_closed(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        coordinator_id = handle.coordinator_id
        digest, revision = _plan_digest()
        foreign = MilestonePlanView(
            plan_authority="other/repo#99",
            plan_digest=digest,
            plan_source_revision=revision,
            milestone_id="M3",
            entry_base=ENTRY_BASE,
            graph=_graph(["W1"]),
            milestone_user_approval_satisfied=True,
        )
        with pytest.raises(PlanDriftError):
            world.recover(foreign, coordinator_id)

    def test_next_milestone_requires_explicit_activation(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        coordinator_id = handle.coordinator_id
        digest, revision = _plan_digest()
        next_view = MilestonePlanView(
            plan_authority=PLAN_AUTHORITY,
            plan_digest=digest,
            plan_source_revision=revision,
            milestone_id="M4",
            entry_base=ENTRY_BASE,
            graph=MilestoneWorkItemGraph(milestone_ref="M4", work_items=["W1"]),
            milestone_user_approval_satisfied=True,
        )
        with pytest.raises(PlanDriftError):
            world.recover(next_view, coordinator_id)

    def test_session_loss_fails_closed(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        coordinator_id = handle.coordinator_id
        with pytest.raises(SessionRecoveryRequiredError):
            recover_coordinator(
                store=world.coord_store,
                coordinator_id=coordinator_id,
                live_plan_view=_view(["W1"]),
                execution_dispatcher=world.dispatcher,
                session_available=False,
            )
        assert world.coord_store.get(coordinator_id).status == CoordinatorStatus.SESSION_RECOVERY_REQUIRED
        # No latest/new-session fallback exists: recovery with the exact
        # session back clears the gate; anything else keeps failing.
        recovered = world.recover(_view(["W1"]), coordinator_id)
        assert recovered.refresh().status == CoordinatorStatus.ACTIVE

    def test_missing_m2_truth_fails_closed(self, tmp_path: Path) -> None:
        w1 = World(tmp_path, name="lost")
        view = _view(["W1"])
        handle = w1.activate(view)
        handle.dispatch_ready(_resolver(["W1"]))
        coordinator_id = handle.coordinator_id
        w1.close()
        # Restart with the SAME coordinator file but an EMPTY execution store:
        # coordinator truth survived, M2 truth is gone -> fail closed.
        coord_store = FileBackedTaskMainCoordinatorStore(tmp_path / "lost.coordinator.json")
        empty_exec = FileBackedExecutionStateStore(tmp_path / "lost-empty.execution.json")
        registry = ExecutorRegistry()
        registry.register(M3W1FakeAdapter())
        from aota_forge.core.execution.durable_state import OriginSessionRef

        dispatcher = ExecutionDispatcher(
            registry,
            state_store=empty_exec,
            origin_session_ref=OriginSessionRef(value=ORIGIN_SESSION),
            admission_scope_resolver=(lambda package: TEST_SCOPE),
        )
        try:
            with pytest.raises(CoordinatorBindingError, match="lost durable M2"):
                recover_coordinator(
                    store=coord_store,
                    coordinator_id=coordinator_id,
                    live_plan_view=view,
                    execution_dispatcher=dispatcher,
                    session_available=True,
                )
        finally:
            coord_store.close()
            empty_exec.close()

    def test_unknown_coordinator_fails_closed(self, world: World) -> None:
        with pytest.raises(CoordinatorNotFoundError):
            world.recover(_view(["W1"]), "aota_forge:M9")

    def test_stale_revision_fails_closed(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        stale_revision = handle.refresh().coordinator_revision
        handle.dispatch_ready(_resolver(["W1"]))
        with pytest.raises(StaleCoordinatorRevisionError):
            world.coord_store.compare_and_swap(
                handle.coordinator_id, stale_revision, {"status": "CLOSED"}
            )


# ---------------------------------------------------------------------------
# Authority boundaries (no self-approval, no semantic stores, no CARD apply)
# ---------------------------------------------------------------------------


class TestAuthorityBoundaries:
    def test_task_main_cannot_set_user_approval(self, world: World) -> None:
        handle = world.activate(_view(["W1"], approved=False))
        assert not hasattr(handle, "set_user_approval")
        assert not hasattr(handle, "approve_milestone")
        assert not hasattr(TaskMainCoordinator, "set_user_approval")
        with pytest.raises(AttributeError):
            handle.set_user_approval(True)  # type: ignore[attr-defined]

    def test_task_handoff_carries_no_session_authority(self) -> None:
        with pytest.raises(TypeError):
            TaskHandoff(
                work_role="coder",
                task_kind="k",
                objective="o",
                bounded_scope="s",
                validation_expectations=("v",),
                semantic_stop_expectations=("t",),
                origin_task_main_session_ref="smuggled",  # type: ignore[call-arg]
            )
        package = compile_handoff_to_execution_package(
            _handoff("W1"),
            TrustedExecutionBinding(canonical_task_id="cid-1", project_id=PROJECT_ID),
        )
        assert not hasattr(package, "origin_task_main_session_ref")
        assert "session" not in str(package.working_context).lower()

    def test_execution_store_not_used_as_coordinator_state(self, world: World) -> None:
        handle = world.activate(_view(["W1"]))
        handle.dispatch_ready(_resolver(["W1"]))
        state = world.coord_store.get(handle.coordinator_id)
        assert isinstance(state, TaskMainCoordinatorState)
        payload = state.to_dict()
        for forbidden in ("delivery_state", "terminal_result", "worker_result_card", "canonical_task_state"):
            assert forbidden not in payload
        assert isinstance(world.coord_store, TaskMainCoordinatorStore)
        assert not isinstance(world.coord_store, FileBackedExecutionStateStore)

    def test_hermes_session_db_is_not_coordinator_authority(self) -> None:
        import aota_forge.runtime.task_main.coordinator as coordinator_module
        import aota_forge.runtime.task_main.coordinator_state as state_module
        import aota_forge.runtime.task_main.coordinator_store as store_module

        for module in (coordinator_module, state_module, store_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "adapters.hermes" not in source
            assert "adapters/hermes" not in source
            assert "session_turn_leases" not in source
            assert "state.db" not in source

    def test_no_semantic_engine_in_w1(self) -> None:
        import aota_forge.runtime.task_main.coordinator as coordinator_module

        source = Path(coordinator_module.__file__).read_text(encoding="utf-8")
        imported = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        wiring = "\n".join(imported)
        for forbidden in (
            "milestone_review_workflow",
            "milestone_closure",
            "work_plane.result_card",
            "evaluate_milestone_progression",
            "evaluate_milestone_review",
            "evaluate_milestone_closure",
        ):
            assert forbidden not in wiring, f"W1 must not wire {forbidden}"
        assert "WorkerResultCard" not in wiring
        assert "project_worker_result_card" not in wiring

    def test_state_module_flags(self) -> None:
        import aota_forge.runtime.task_main.coordinator_state as state_module

        assert state_module.COORDINATOR_STATE_IS_PLAN_AUTHORITY is False
        assert state_module.EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE is False
        assert state_module.HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY is False
        assert state_module.W1_CARD_SEMANTIC_APPLICATION_IMPLEMENTED is False
        assert state_module.ACK_AFTER_SEMANTIC_RECONCILIATION_IMPLEMENTED is False
        assert state_module.REVIEW_AUTOMATION_IMPLEMENTED is False
        assert state_module.REPAIR_AUTOMATION_IMPLEMENTED is False
        assert state_module.MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED is False


# ---------------------------------------------------------------------------
# Store contract
# ---------------------------------------------------------------------------


class TestCoordinatorStore:
    def test_roundtrip_and_cas(self, tmp_path: Path) -> None:
        store = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        digest, revision = _plan_digest()
        fresh = TaskMainCoordinatorState(
            coordinator_id="aota_forge:M3",
            plan_authority=PLAN_AUTHORITY,
            plan_digest=digest,
            plan_source_revision=revision,
            milestone_id="M3",
            entry_base=ENTRY_BASE,
            origin_task_main_session_ref=ORIGIN_SESSION,
            project_id=PROJECT_ID,
            executor_id=FAKE_EXECUTOR_ID,
            work_items=("W1",),
            wi_status={"W1": "PENDING"},
        )
        created = store.create(fresh)
        assert created.coordinator_revision == 1 and created.revision_token != ""
        store.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get("aota_forge:M3")
        assert loaded is not None and loaded.to_dict() == created.to_dict()
        updated = reopened.compare_and_swap(
            "aota_forge:M3", 1, {"status": "CLOSED"}, created.revision_token
        )
        assert updated.coordinator_revision == 2 and updated.status == CoordinatorStatus.CLOSED
        reopened.close()

    def test_corrupt_record_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text('{"x": {"schema_version": 999}}', encoding="utf-8")
        from aota_forge.runtime.task_main.coordinator_store import (
            CoordinatorPersistenceFailureError,
        )

        with pytest.raises(CoordinatorPersistenceFailureError):
            FileBackedTaskMainCoordinatorStore(path)
