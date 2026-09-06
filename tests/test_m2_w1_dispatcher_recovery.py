"""M2/W1 — Agent-neutral durable execution runtime state: dispatcher restart proofs.

Deterministic executor-neutral durable fake + restart chains through a
FileBackedExecutionStateStore prove:

- ROUTE_SURVIVES_RESTART=yes, ADAPTER_OBJECT_PERSISTED=no,
  ADAPTER_RE_RESOLVED_FROM_REGISTRY=yes
- durable idempotency: same key+intent -> REPLAY with no physical redispatch;
  same key+different intent -> CONFLICT
- terminal CanonicalResult + WorkerResultCard survive close/reopen
- origin_session_ref (runtime-side) survives close/reopen
- delivery structural state survives while terminal truth stays distinct
- bounded recovery scan supports W3 startup reconciliation
- sticky UNKNOWN on unresolvable recovery; records are never deleted and
  never blindly redispatched
- crash window: DISPATCHED persist failure leaves PREPARED + reconciliation
  error, not redispatch (no exactly-once claim)
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

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
from aota_forge.core.execution.dispatcher import (
    DispatchOutcomeUnresolvedError,
    DuplicateCanonicalTaskIdError,
    ExecutionDispatcher,
    IdempotencyConflictError,
    RouteRecord,
)
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    ExecutionPersistenceFailureError,
    ExecutionPhase,
    FileBackedExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import (
    ExecutorNotFoundError,
    ExecutorRegistry,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.roles import CANONICAL_ROLES
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.work_plane.result_card import WorkerResultCard
from aota_forge.work_plane.roles import AgentWorkRole

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FAKE_EXECUTOR_ID = "durable-fake"
DISPATCH_STAMP = "2026-09-06T00:00:00+00:00"


class StableHandleDurableFakeAdapter(ExecutorAdapter):
    """Executor-neutral durable fake with an intentionally stable handle.

    The adapter_handle encodes everything status()/result() need, so a fresh
    adapter instance (or an instance in a recreated process) mechanically
    recovers the same execution truth — the generic recovery seam W1 promises.
    The adapter object itself is never persisted.
    """

    def __init__(self, *, fail_dispatch: bool = False, unreachable: bool = False) -> None:
        self.physical_dispatch_count = 0
        self._fail_dispatch = fail_dispatch
        self._unreachable = unreachable

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id=FAKE_EXECUTOR_ID,
            adapter_kind="durable_fake_test_double",
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
        outcome = package.constraints.get("fake_outcome", "success")
        return f"durable-fake::{package.canonical_task_id}::{package.correlation_id}::{outcome}"

    @staticmethod
    def _parse(handle: str) -> tuple[str, str, str]:
        _, task_id, correlation_id, outcome = handle.split("::")
        return task_id, correlation_id, outcome

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.physical_dispatch_count += 1
        if self._fail_dispatch:
            raise RuntimeError("injected durable-fake dispatch failure")
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=self.handle_for(package),
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time=DISPATCH_STAMP,
        )

    def _current_state(self, outcome: str) -> CanonicalTaskState:
        if self._unreachable:
            raise RuntimeError("injected adapter unavailability")
        if outcome == "running":
            return CanonicalTaskState.RUNNING
        if outcome == "failed":
            return CanonicalTaskState.FAILED
        return CanonicalTaskState.COMPLETED

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        task_id, _corr, outcome = self._parse(adapter_handle)
        return TaskStatusResult(
            canonical_task_id=task_id,
            state=self._current_state(outcome),
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        task_id, correlation_id, outcome = self._parse(adapter_handle)
        if self._unreachable:
            raise RuntimeError("injected adapter unavailability")
        if outcome == "failed":
            return CanonicalResult.failure(
                canonical_task_id=task_id,
                executor_id=FAKE_EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="durable fake scripted failure",
                correlation_id=correlation_id,
            )
        return CanonicalResult.success(
            canonical_task_id=task_id,
            executor_id=FAKE_EXECUTOR_ID,
            result_data={"proof": "m2-w1-durable"},
            stdout_summary="ok",
            execution_stats={"duration_ms": 7},
            correlation_id=correlation_id,
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(
            canonical_task_id=canonical_task_id,
            cancelled=True,
            state=CanonicalTaskState.CANCELLED,
        )

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


def make_package(task_id: str = "task-m2w1-1", idem_key: str | None = None, outcome: str = "success") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=f"m2w1 durable proof {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={"fake_outcome": outcome},
        idempotency_key=idem_key or f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


@pytest.fixture
def store_path(tmp_path) -> pathlib.Path:
    return tmp_path / "m2w1-execution-state.json"


def open_store(path) -> FileBackedExecutionStateStore:
    return FileBackedExecutionStateStore(path)


def build_dispatcher(store: FileBackedExecutionStateStore, adapter: ExecutorAdapter | None = None, **kwargs) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(adapter or StableHandleDurableFakeAdapter())
    return ExecutionDispatcher(registry, state_store=store, **kwargs)


# ---------------------------------------------------------------------------
# §25 Route survives restart
# ---------------------------------------------------------------------------


class TestRouteSurvivesRestart:
    def test_route_reconstructed_in_fresh_dispatcher(self, store_path):
        store_a = open_store(store_path)
        adapter_a = StableHandleDurableFakeAdapter()
        dispatcher_a = build_dispatcher(store_a, adapter_a)
        package = make_package()
        dispatched = dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        assert adapter_a.physical_dispatch_count == 1
        store_a.close()

        store_b = open_store(store_path)
        adapter_b = StableHandleDurableFakeAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        assert dispatcher_b._routes == {}  # fresh process-local state

        route = dispatcher_b.get_route(package.canonical_task_id)
        assert isinstance(route, RouteRecord)
        assert route.adapter_handle == dispatched.adapter_handle
        assert route.last_known_state == CanonicalTaskState.RUNNING
        assert route._adapter is adapter_b  # re-resolved from registry, never deserialized
        assert adapter_b.physical_dispatch_count == 0

        status = dispatcher_b.status(package.canonical_task_id)
        assert status.state == CanonicalTaskState.COMPLETED
        result = dispatcher_b.result(package.canonical_task_id)
        assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
        assert adapter_b.physical_dispatch_count == 0  # status/result never redispatch

    def test_adapter_object_never_persisted(self, store_path):
        store = open_store(store_path)
        dispatcher = build_dispatcher(store)
        dispatcher.dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)
        raw = store_path.read_text()
        payload = json.loads(raw)  # JSON-native surface proves serialization
        assert "_adapter" not in raw
        assert "StableHandleDurableFakeAdapter" not in raw
        record = payload["task-m2w1-1"]
        assert isinstance(record["adapter_handle"], str)

    def test_unregistered_executor_fails_closed_on_recovery(self, store_path):
        store_a = open_store(store_path)
        build_dispatcher(store_a).dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)
        store_a.close()

        store_b = open_store(store_path)
        empty_registry = ExecutorRegistry()
        dispatcher_b = ExecutionDispatcher(empty_registry, state_store=store_b)
        with pytest.raises(ExecutorNotFoundError):
            dispatcher_b.get_route("task-m2w1-1")
        # The durable record is preserved for reconciliation, not deleted.
        assert store_b.get("task-m2w1-1") is not None

    def test_process_local_mode_still_forgets_routes(self, store_path):
        registry_a = ExecutorRegistry()
        registry_a.register(StableHandleDurableFakeAdapter())
        dispatcher_a = ExecutionDispatcher(registry_a)  # no store: M1 behavior
        dispatcher_a.dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)
        registry_b = ExecutorRegistry()
        registry_b.register(StableHandleDurableFakeAdapter())
        dispatcher_b = ExecutionDispatcher(registry_b)
        from aota_forge.core.execution.dispatcher import TaskNotFoundError

        with pytest.raises(TaskNotFoundError):
            dispatcher_b.get_route("task-m2w1-1")


# ---------------------------------------------------------------------------
# §26 Durable idempotency across restart
# ---------------------------------------------------------------------------


class TestIdempotencyRestart:
    def test_replay_after_restart_no_second_physical_dispatch(self, store_path):
        store_a = open_store(store_path)
        adapter_a = StableHandleDurableFakeAdapter()
        dispatcher_a = build_dispatcher(store_a, adapter_a)
        original = dispatcher_a.dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)
        store_a.close()

        store_b = open_store(store_path)
        adapter_b = StableHandleDurableFakeAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        replayed = dispatcher_b.dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)

        assert replayed.adapter_handle == original.adapter_handle
        assert replayed.initial_state == original.initial_state
        assert replayed.dispatch_time == original.dispatch_time
        assert adapter_a.physical_dispatch_count == 1
        assert adapter_b.physical_dispatch_count == 0  # REPLAY: no new dispatch

    def test_conflict_after_restart(self, store_path):
        store_a = open_store(store_path)
        dispatcher_a = build_dispatcher(store_a)
        dispatcher_a.dispatch(make_package(idem_key="key-shared"), target_executor_id=FAKE_EXECUTOR_ID)
        store_a.close()

        store_b = open_store(store_path)
        adapter_b = StableHandleDurableFakeAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        conflict_package = make_package(task_id="task-other-intent", idem_key="key-shared", outcome="failed")
        with pytest.raises(IdempotencyConflictError):
            dispatcher_b.dispatch(conflict_package, target_executor_id=FAKE_EXECUTOR_ID)
        assert adapter_b.physical_dispatch_count == 0

    def test_duplicate_task_id_survives_restart(self, store_path):
        store_a = open_store(store_path)
        build_dispatcher(store_a).dispatch(make_package(), target_executor_id=FAKE_EXECUTOR_ID)
        store_a.close()
        dispatcher_b = build_dispatcher(open_store(store_path))
        with pytest.raises(DuplicateCanonicalTaskIdError):
            dispatcher_b.dispatch(make_package(idem_key="brand-new-key"), target_executor_id=FAKE_EXECUTOR_ID)


# ---------------------------------------------------------------------------
# §27 Terminal result + CARD restart
# ---------------------------------------------------------------------------


class TestTerminalPersistenceRestart:
    def test_terminal_result_and_card_exact_reconstruction(self, store_path):
        from aota_forge.core.result_governance import ResultGovernanceProjection
        from aota_forge.work_plane.result_card import project_worker_result_card

        store_a = open_store(store_path)
        dispatcher_a = build_dispatcher(store_a)
        package = make_package()
        dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        dispatcher_a.status(package.canonical_task_id)
        canonical_result = dispatcher_a.result(package.canonical_task_id)
        assert canonical_result.status == "completed"
        card = project_worker_result_card(
            canonical_result,
            ResultGovernanceProjection.success(),
            AgentWorkRole.CODER,
            summary="m2w1 durable terminal proof",
        )
        dispatcher_a.attach_worker_result_card(package.canonical_task_id, card)
        stored_a = store_a.get(package.canonical_task_id)
        store_a.close()

        store_b = open_store(store_path)
        stored_b = store_b.get(package.canonical_task_id)
        assert stored_b is not None
        assert stored_b.terminal_result is not None
        assert stored_b.terminal_result.to_json() == canonical_result.to_json()
        assert stored_b.terminal_result.result_data == canonical_result.result_data
        assert stored_b.terminal_result.execution_stats == canonical_result.execution_stats
        assert stored_b.worker_result_card_digest == card.compute_card_digest()
        rebuilt = WorkerResultCard.from_dict(dict(stored_b.worker_result_card))
        assert rebuilt.compute_card_digest() == card.compute_card_digest()
        assert rebuilt.result_handoff_ref.ref == package.canonical_task_id
        assert stored_b.canonical_task_state == CanonicalTaskState.COMPLETED
        assert stored_a.record_revision == stored_b.record_revision

    def test_result_idempotent_persistence_conflict_guard(self, store_path):
        store = open_store(store_path)
        dispatcher = build_dispatcher(store)
        package = make_package()
        dispatcher.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        first = dispatcher.result(package.canonical_task_id)
        again = dispatcher.result(package.canonical_task_id)
        assert first.to_json() == again.to_json()
        # A contradictory terminal truth fails closed and never overwrites.
        from aota_forge.core.execution.dispatcher import AdapterProtocolError

        record = store.get(package.canonical_task_id)
        conflict = CanonicalResult.failure(
            canonical_task_id=package.canonical_task_id,
            executor_id=FAKE_EXECUTOR_ID,
            correlation_id=package.correlation_id,
        )
        with pytest.raises(AdapterProtocolError):
            dispatcher._persist_terminal_result(package.canonical_task_id, conflict)
        assert store.get(package.canonical_task_id).terminal_result.to_json() == first.to_json()
        assert record is not None


# ---------------------------------------------------------------------------
# §28 origin_session_ref restart
# ---------------------------------------------------------------------------


class TestOriginSessionRestart:
    def test_runtime_origin_binding_survives_restart(self, store_path):
        store_a = open_store(store_path)
        dispatcher_a = build_dispatcher(store_a, origin_session_ref="task-main-opaque-session-9f3a")
        package = make_package()
        dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        rec = store_a.get(package.canonical_task_id)
        assert rec.origin_session_ref == OriginSessionRef(value="task-main-opaque-session-9f3a")
        store_a.close()

        store_b = open_store(store_path)
        rec_b = store_b.get(package.canonical_task_id)
        assert rec_b.origin_session_ref.value == "task-main-opaque-session-9f3a"
        # bind-once semantics survive the restart too
        from aota_forge.core.execution.durable_state import (
            ExecutionPersistenceFailureError,
        )

        with pytest.raises(ExecutionPersistenceFailureError):
            store_b.compare_and_swap(
                package.canonical_task_id,
                rec_b.record_revision,
                {"origin_session_ref": "other-session"},
            )
        assert rec_b.origin_session_ref == OriginSessionRef(value="task-main-opaque-session-9f3a")

    def test_bind_later_and_no_semantic_surface_contamination(self, store_path):
        from aota_forge.work_plane.handoff import TaskHandoff

        store = open_store(store_path)
        dispatcher = build_dispatcher(store)  # no origin at construction
        package = make_package()
        dispatcher.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        bound = dispatcher.bind_origin_session_ref(package.canonical_task_id, "sess-later-01")
        assert bound.origin_session_ref == OriginSessionRef(value="sess-later-01")
        # TaskHandoff semantic contract untouched
        handoff_fields = {f.name for f in dataclasses.fields(TaskHandoff)}
        assert not [f for f in handoff_fields if "session" in f or "origin" in f]
        # ExecutionPackage contract untouched
        assert "origin_session_ref" not in package.to_dict()


# ---------------------------------------------------------------------------
# §29 Delivery structural persistence (no coordinator behavior)
# ---------------------------------------------------------------------------


class TestDeliveryStructuralPersistence:
    def test_terminal_pending_survives_then_cas_ack(self, store_path):
        store_a = open_store(store_path)
        dispatcher_a = build_dispatcher(store_a)
        package = make_package()
        dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        dispatcher_a.result(package.canonical_task_id)  # attaches terminal truth
        record = store_a.get(package.canonical_task_id)
        assert record.delivery_state == DeliveryState.PENDING
        store_a.close()

        store_b = open_store(store_path)
        reopened = store_b.get(package.canonical_task_id)
        assert reopened.terminal_result is not None
        assert reopened.canonical_task_state == CanonicalTaskState.COMPLETED
        assert reopened.delivery_state == DeliveryState.PENDING

        acked = store_b.compare_and_swap(
            package.canonical_task_id, reopened.record_revision, {"delivery_state": "acknowledged"}
        )
        assert acked.delivery_state == DeliveryState.ACKNOWLEDGED
        # ACK never fabricates or alters terminal truth.
        assert acked.canonical_task_state == CanonicalTaskState.COMPLETED
        assert acked.terminal_result.to_json() == reopened.terminal_result.to_json()


# ---------------------------------------------------------------------------
# §30 Bounded recovery scan
# ---------------------------------------------------------------------------


class TestRecoveryScan:
    def test_scan_partitions_execution_truth(self, store_path):
        store = open_store(store_path)
        d1 = build_dispatcher(store)
        d1.dispatch(make_package("task-running", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID)
        d1.dispatch(make_package("task-terminal-unacked"), target_executor_id=FAKE_EXECUTOR_ID)
        d1.result("task-terminal-unacked")
        d1.dispatch(make_package("task-acked"), target_executor_id=FAKE_EXECUTOR_ID)
        d1.result("task-acked")
        rec = store.get("task-acked")
        store.compare_and_swap("task-acked", rec.record_revision, {"delivery_state": "acknowledged"})

        ids = [r.canonical_task_id for r in store.scan_requiring_recovery()]
        assert ids == ["task-running", "task-terminal-unacked"]

        reopened = open_store(store_path)
        ids2 = [r.canonical_task_id for r in reopened.scan_requiring_recovery()]
        assert ids2 == ids


# ---------------------------------------------------------------------------
# §22 Sticky UNKNOWN + no blind redispatch
# ---------------------------------------------------------------------------


class TestUnknownRecoverySafety:
    def test_unreachable_adapter_persists_sticky_unknown(self, store_path):
        store_a = open_store(store_path)
        dispatcher_a = build_dispatcher(store_a)
        package = make_package("task-flaky")
        dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        store_a.close()

        store_b = open_store(store_path)
        unreachable = StableHandleDurableFakeAdapter(unreachable=True)
        dispatcher_b = build_dispatcher(store_b, unreachable)
        state = dispatcher_b.reconcile_status("task-flaky")
        assert state == CanonicalTaskState.UNKNOWN
        record = store_b.get("task-flaky")
        assert record is not None
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        store_b.close()

        store_c = open_store(store_path)
        rec_c = store_c.get("task-flaky")
        assert rec_c.canonical_task_state == CanonicalTaskState.UNKNOWN  # sticky across restart

        adapter_c = StableHandleDurableFakeAdapter()
        dispatcher_c = build_dispatcher(store_c, adapter_c)
        replay = dispatcher_c.dispatch(make_package("task-flaky"), target_executor_id=FAKE_EXECUTOR_ID)
        assert adapter_c.physical_dispatch_count == 0  # no blind redispatch on uncertainty
        assert replay.adapter_handle == rec_c.adapter_handle

    def test_prepared_crash_tail_never_replayed_as_dispatched(self, store_path):
        store_a = open_store(store_path)
        store_a.inject_fail_next_cas()  # DISPATCHED persist fails after physical dispatch
        adapter_a = StableHandleDurableFakeAdapter()
        dispatcher_a = build_dispatcher(store_a, adapter_a)
        package = make_package("task-crash-window")
        with pytest.raises(ExecutionPersistenceFailureError):
            dispatcher_a.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        assert adapter_a.physical_dispatch_count == 1
        stuck = store_a.get("task-crash-window")
        assert stuck.execution_phase == ExecutionPhase.PREPARED
        assert stuck.adapter_handle is None
        store_a.close()

        store_b = open_store(store_path)
        adapter_b = StableHandleDurableFakeAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        with pytest.raises(DispatchOutcomeUnresolvedError):
            dispatcher_b.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        assert adapter_b.physical_dispatch_count == 0  # truthful bounded suppression, no redispatch
        assert store_b.get("task-crash-window") is not None  # record preserved, not deleted
        # The crash tail is visible to recovery scanners.
        assert "task-crash-window" in [r.canonical_task_id for r in store_b.scan_requiring_recovery()]

    def test_dispatch_adapter_failure_keeps_prepared_identity(self, store_path):
        store = open_store(store_path)
        adapter = StableHandleDurableFakeAdapter(fail_dispatch=True)
        dispatcher = build_dispatcher(store, adapter)
        package = make_package("task-dispatch-throws")
        with pytest.raises(RuntimeError, match="injected durable-fake dispatch failure"):
            dispatcher.dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        record = store.get("task-dispatch-throws")
        assert record.execution_phase == ExecutionPhase.PREPARED
        assert record.dispatch_attempt_id  # durable identity exists before/after failure

    def test_unresolved_error_carries_accepted_code(self):
        err = DispatchOutcomeUnresolvedError("task-x")
        assert err.code == "TASK_STATE_UNKNOWN"


# ---------------------------------------------------------------------------
# Boundary audits
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_no_hermes_imports_in_w1_surface(self):
        offenders: list[str] = []
        for rel in (
            "aota_forge/core/execution/durable_state.py",
            "aota_forge/core/execution/dispatcher.py",
            "aota_forge/core/execution/__init__.py",
        ):
            tree = ast.parse((REPO_ROOT / rel).read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
                elif isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                else:
                    continue
                offenders.extend(f"{rel}:{name}" for name in names if "hermes" in name.lower())
        assert offenders == []

    def test_journal_record_operations_unchanged(self):
        from aota_forge.core.journal import model as journal_model
        from aota_forge.core.journal.model import JournalRecord  # noqa: F401

        src = pathlib.Path(journal_model.__file__).read_text()
        assert 'self.operation not in ("plan_init", "plan_retirement")' in src

    def test_delivery_coordinator_absent(self):
        import aota_forge.core.execution.durable_state as ds

        tree = ast.parse(pathlib.Path(ds.__file__).read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        forbidden_markers = ("retry_loop", "coordinator", "ack_session", "resume_delivery", "attempt_cap")
        assert not [
            n for n in names if any(m in n.lower() for m in forbidden_markers)
        ]
