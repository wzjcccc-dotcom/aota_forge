"""AF #49 M1/W1 — Production path contract & test-double boundary (focused).

Proves the frozen boundary between the test/component lifecycle seam and the
production execution seam:

* canonical task.start / task.return without a trusted production
  ExecutionDispatcher fail closed (typed); no ReferenceFakeExecutorAdapter or
  InMemoryExecutionStateStore is silently constructed or reachable;
* explicit test/component dispatcher injection still exercises the façade;
* the canonical Agent-facing ingress supplies the trusted production
  dispatcher from the existing core.ingress binding seam;
* process-local completion state is not a production durability channel;
  observation is only through an explicitly injected sink.

Proof boundary: V1 contract + targeted V2 (canonical ingress <-> task façade
<-> existing ExecutionDispatcher seam). This does NOT prove real Hermes
dispatch, real parent re-entry, or production completion durability
(those belong to M1/W3 and the M1 V3 gate).
"""
from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

import aota_forge.core_ingress as core_ingress
import aota_forge.work_plane.task_facade as task_facade
from aota_forge.adapters.execution.reference import (
    REFERENCE_EXECUTOR_TEST_ONLY,
    ReferenceFakeExecutorAdapter,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.task_facade import (
    MISSING_PRODUCTION_DISPATCHER_FAIL_CLOSED,
    PARENT_SIDE_DURABLE_RECONCILIATION_OWNS_TERMINAL_TRUTH,
    PRODUCTION_IN_MEMORY_EXECUTION_STORE_FALLBACK,
    PRODUCTION_PROCESS_LOCAL_COMPLETION_CHANNEL,
    PRODUCTION_REFERENCE_FAKE_EXECUTOR_FALLBACK,
    TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE,
    TEST_DOUBLE_EXPLICIT_INJECTION_ALLOWED,
    TEST_DOUBLE_PRODUCTION_REACHABLE,
    TEST_DOUBLE_SILENT_PRODUCTION_FALLBACK_ALLOWED,
    ProductionDispatcherUnavailableError,
    ProductionExecutionStoreUnavailableError,
    get_completion,
    task_return,
    task_start,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


def _sandbox(project_id: str = "projAF49", worktree_id: str = "wtAF49") -> WorktreeSandboxBoundary:
    td = Path(tempfile.mkdtemp(prefix="af49-w1-"))
    return WorktreeSandboxBoundary(
        workspace_id="ws1",
        workspace_root=str(td),
        project_id=project_id,
        project_root=str(td),
        worktree_id=worktree_id,
        worktree_root=str(td),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _test_dispatcher(*, with_store: bool = True) -> ExecutionDispatcher:
    """Explicit test/component dispatcher injection (the only test double path)."""
    registry = ExecutorRegistry()
    registry.register(ReferenceFakeExecutorAdapter())
    store = InMemoryExecutionStateStore() if with_store else None
    return ExecutionDispatcher(registry, state_store=store)


def _work_item_handoff(sandbox: WorktreeSandboxBoundary):
    semantic = {
        "objective": "AF49 W1 boundary probe",
        "bounded_scope": "focused contract probe",
        "validation_expectations": ["focused"],
        "semantic_stop_expectations": ["stop"],
        "work_role": "coder",
        "task_kind": "impl",
    }
    return handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=sandbox,
    )


def _start(sandbox: WorktreeSandboxBoundary, dispatcher: ExecutionDispatcher):
    wref = _work_item_handoff(sandbox)
    start = task_start(
        role="coder",
        handoff_ref=wref.ref,
        caller_role="task-main",
        sandbox=sandbox,
        dispatcher=dispatcher,
    )
    return start, wref


def _result_handoff(sandbox: WorktreeSandboxBoundary, task_id: str):
    return handoff_write(
        mode="result",
        semantic={"summary": "boundary probe result", "work_done": "done", "validation": "ok"},
        caller_role="coder",
        sandbox=sandbox,
        task_id=task_id,
    )


def _role_binding(
    sandbox: WorktreeSandboxBoundary,
    *,
    work_role: str,
    canonical_task_id: str,
) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role=work_role,
        task_kind="test",
        objective="obj",
        bounded_scope="scope",
        validation_expectations=["v"],
        semantic_stop_expectations=["s"],
    )
    surface = create_role_tool_surface(
        work_role, eager=("workspace.search", "workspace.read"), progressive=()
    )
    return CanonicalDispatchBinding(
        canonical_task_id=canonical_task_id,
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
    )


class _FakeHermesHost:
    """In-memory Hermes host double; no real process is spawned."""

    def __init__(self) -> None:
        self.dispatched_payloads: list[dict] = []

    def dispatch(self, payload):
        self.dispatched_payloads.append(dict(payload))
        return {
            "adapter_handle": "af49-hermes-private-1",
            "status": "pending",
            "dispatch_time": "2026-09-11T00:00:00Z",
        }


class TestFailClosedWithoutTrustedDispatcher:
    def test_task_start_without_dispatcher_typed_fail_closed(self):
        sb = _sandbox()
        wref = _work_item_handoff(sb)
        with pytest.raises(ProductionDispatcherUnavailableError) as excinfo:
            task_start(role="coder", handoff_ref=wref.ref, caller_role="task-main", sandbox=sb)
        assert excinfo.value.code == "PRODUCTION_DISPATCHER_UNAVAILABLE"

    def test_task_return_without_dispatcher_typed_fail_closed(self):
        sb = _sandbox()
        task_id = "af49-task-return-no-dispatcher"
        rref = _result_handoff(sb, task_id)
        with pytest.raises(ProductionDispatcherUnavailableError) as excinfo:
            task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="coder",
                caller_task_id=task_id,
                sandbox=sb,
            )
        assert excinfo.value.code == "PRODUCTION_DISPATCHER_UNAVAILABLE"

    def test_task_return_with_storeless_dispatcher_typed_fail_closed(self):
        sb = _sandbox()
        task_id = "af49-task-return-no-store"
        rref = _result_handoff(sb, task_id)
        with pytest.raises(ProductionExecutionStoreUnavailableError) as excinfo:
            task_return(
                status="completed",
                result_ref=rref.ref,
                caller_role="coder",
                caller_task_id=task_id,
                sandbox=sb,
                dispatcher=_test_dispatcher(with_store=False),
            )
        assert excinfo.value.code == "PRODUCTION_EXECUTION_STORE_UNAVAILABLE"

    def test_missing_dispatcher_never_constructs_test_doubles(self, monkeypatch):
        """Negative probe: fail-closed path never reaches the test-double constructors."""
        import aota_forge.adapters.execution.reference as reference_mod
        import aota_forge.core.execution.durable_state as durable_mod

        class _ExplodingAdapter(reference_mod.ReferenceFakeExecutorAdapter):
            def __init__(self, *args, **kwargs):  # noqa: D107
                raise AssertionError("test double constructed on production path")

        class _ExplodingStore(durable_mod.InMemoryExecutionStateStore):
            def __init__(self, *args, **kwargs):  # noqa: D107
                raise AssertionError("in-memory store constructed on production path")

        monkeypatch.setattr(reference_mod, "ReferenceFakeExecutorAdapter", _ExplodingAdapter)
        monkeypatch.setattr(durable_mod, "InMemoryExecutionStateStore", _ExplodingStore)

        sb = _sandbox()
        wref = _work_item_handoff(sb)
        with pytest.raises(ProductionDispatcherUnavailableError):
            task_start(role="coder", handoff_ref=wref.ref, caller_role="task-main", sandbox=sb)


class TestExplicitTestInjection:
    def test_explicit_dispatcher_task_start_and_return(self):
        sb = _sandbox()
        disp = _test_dispatcher()
        start, wref = _start(sb, disp)
        assert start["handoff_digest"] == wref.digest
        task_id = start["task_id"]
        assert disp.has_route(task_id)

        rref = _result_handoff(sb, task_id)
        comp = task_return(
            status="completed",
            result_ref=rref.ref,
            caller_role="coder",
            caller_task_id=task_id,
            sandbox=sb,
            dispatcher=disp,
        )
        assert comp["task_id"] == task_id
        assert comp["status"] == "completed"
        assert "card" in comp
        record = disp.state_store.get(task_id)
        assert record is not None
        assert record.canonical_task_state.is_terminal

    def test_explicit_completion_sink_observes_completion(self):
        sb = _sandbox()
        disp = _test_dispatcher()
        start, _ = _start(sb, disp)
        task_id = start["task_id"]
        rref = _result_handoff(sb, task_id)
        sink: dict = {}
        comp = task_return(
            status="completed",
            result_ref=rref.ref,
            caller_role="coder",
            caller_task_id=task_id,
            sandbox=sb,
            dispatcher=disp,
            completion_sink=sink,
        )
        assert comp["process_local_completion_recorded"] is True
        fetched = get_completion(task_id, completion_sink=sink)
        assert fetched is not None
        assert fetched["task_id"] == task_id

    def test_w1_boundary_markers_frozen(self):
        assert PRODUCTION_REFERENCE_FAKE_EXECUTOR_FALLBACK is False
        assert PRODUCTION_IN_MEMORY_EXECUTION_STORE_FALLBACK is False
        assert PRODUCTION_PROCESS_LOCAL_COMPLETION_CHANNEL is False
        assert TEST_DOUBLE_EXPLICIT_INJECTION_ALLOWED is True
        assert TEST_DOUBLE_SILENT_PRODUCTION_FALLBACK_ALLOWED is False
        assert MISSING_PRODUCTION_DISPATCHER_FAIL_CLOSED is True
        assert TEST_DOUBLE_PRODUCTION_REACHABLE is False

    def test_existing_foundations_remain_available_for_explicit_test_use(self):
        assert REFERENCE_EXECUTOR_TEST_ONLY is True
        adapter = ReferenceFakeExecutorAdapter()
        store = InMemoryExecutionStateStore()
        assert adapter.capabilities().executor_id == "reference-fake"
        assert store.list_all() == []

    def test_no_new_engine_or_ontology_surface_added(self):
        import aota_forge.work_plane.agent_facing_contract as agent_facing_contract

        assert agent_facing_contract.NEW_EXECUTION_ENGINE_CREATED is False
        forbidden_tokens = (
            "ExecutionEngine",
            "Coordinator",
            "AuthorityEngine",
            "StateMachine",
            "ResultOntology",
            "DispatcherRegistry",
        )
        offenders = [name for name in dir(task_facade) if any(t in name for t in forbidden_tokens)]
        assert offenders == []


class TestProcessLocalCompletionIsNotProductionDurability:
    def test_task_return_without_sink_records_no_process_local_state(self):
        sb = _sandbox()
        disp = _test_dispatcher()
        start, _ = _start(sb, disp)
        task_id = start["task_id"]
        rref = _result_handoff(sb, task_id)
        comp = task_return(
            status="completed",
            result_ref=rref.ref,
            caller_role="coder",
            caller_task_id=task_id,
            sandbox=sb,
            dispatcher=disp,
        )
        assert comp["durable_completion"] == "not_established_in_W1"
        assert comp["process_local_completion_recorded"] is False
        assert get_completion(task_id, completion_sink={}) is None
        assert not hasattr(task_facade, "_GLOBAL_COMPLETIONS")
        assert PARENT_SIDE_DURABLE_RECONCILIATION_OWNS_TERMINAL_TRUTH is True
        assert TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE is False


class TestCanonicalIngressBoundary:
    def teardown_method(self):
        reset_execution_dispatcher()

    def test_canonical_task_start_without_trusted_dispatcher_fails_closed(self):
        reset_execution_dispatcher()
        sb = _sandbox()
        binding = _role_binding(sb, work_role="task-main", canonical_task_id="af49-task-main-1")
        wref = _work_item_handoff(sb)
        resp = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": wref.ref}, binding)
        assert not resp.ok
        assert resp.error["code"] == "PRODUCTION_DISPATCHER_UNAVAILABLE"

    def test_canonical_task_start_honors_trusted_dispatcher(self):
        reset_execution_dispatcher()
        sb = _sandbox()
        binding = _role_binding(sb, work_role="task-main", canonical_task_id="af49-task-main-2")
        wref = _work_item_handoff(sb)
        disp = _test_dispatcher()
        bind_execution_dispatcher(disp)
        resp = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": wref.ref}, binding)
        assert resp.ok, f"task.start via trusted ingress dispatcher failed: {resp.error}"
        task_id = resp.payload["task_id"]
        assert disp.has_route(task_id)
        assert disp.get_route(task_id).executor_id == "reference-fake"

    def test_canonical_task_return_uses_trusted_dispatcher_without_process_local_channel(self):
        reset_execution_dispatcher()
        sb = _sandbox()
        disp = _test_dispatcher()
        start, _ = _start(sb, disp)
        task_id = start["task_id"]
        rref = _result_handoff(sb, task_id)
        bind_execution_dispatcher(disp)
        binding = _role_binding(sb, work_role="coder", canonical_task_id=task_id)
        resp = dispatch_via_core(
            "task.return", {"status": "completed", "result_ref": rref.ref}, binding
        )
        assert resp.ok, f"task.return via trusted ingress dispatcher failed: {resp.error}"
        assert resp.payload["task_id"] == task_id
        assert resp.payload["process_local_completion_recorded"] is False
        assert not hasattr(task_facade, "_GLOBAL_COMPLETIONS")
        record = disp.state_store.get(task_id)
        assert record is not None
        assert record.terminal_result is not None

    def test_canonical_task_start_uses_existing_production_composition_seam(self):
        reset_execution_dispatcher()
        from aota_forge.composition.execution import bind_production_execution_dispatcher

        host = _FakeHermesHost()
        disp = bind_production_execution_dispatcher(host_client=host)
        sb = _sandbox()
        binding = _role_binding(sb, work_role="task-main", canonical_task_id="af49-task-main-3")
        wref = _work_item_handoff(sb)
        resp = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": wref.ref}, binding)
        assert resp.ok, f"production composition task.start failed: {resp.error}"
        task_id = resp.payload["task_id"]
        assert disp.has_route(task_id)
        assert disp.get_route(task_id).executor_id == "hermes"
        assert len(host.dispatched_payloads) == 1


class TestSourceBoundaryContract:
    def test_task_facade_source_has_no_test_double_fallback(self):
        source = inspect.getsource(task_facade)
        for token in (
            "ReferenceFakeExecutorAdapter",
            "InMemoryExecutionStateStore",
            "_GLOBAL_COMPLETIONS",
            "_GLOBAL_DISPATCHERS",
            "_GLOBAL_STORES",
            "_get_dispatcher",
            "_get_store",
            "OriginSessionRef",
        ):
            assert token not in source, f"production fallback token still present: {token}"
        assert "PRODUCTION_DISPATCHER_UNAVAILABLE" in source

    def test_core_ingress_supplies_trusted_production_dispatcher(self):
        source = inspect.getsource(core_ingress)
        assert "_trusted_production_execution_dispatcher" in source
        assert "PRODUCTION_DISPATCHER_UNAVAILABLE" in source
        assert source.count("dispatcher=trusted_dispatcher") == 2
