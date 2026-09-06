"""M2/W3 — bounded concurrency admission across restart (deterministic).

Ownership per the accepted plan (RuntimeConfig -> configured bound,
ExecutionStateStore -> durable active-set truth, W3 -> admission enforcement):

- new physical dispatch is refused when durable active >= configured limit
- UNKNOWN and unresolved PREPARED count as active (conservative restart truth)
- terminal executions free capacity even while delivery is pending
- idempotent REPLAY bypasses admission (no new physical Worker)
- restart reconstructs the active-set from the store: no capacity leak
- the accounting scope comes ONLY from the trusted server-side binding; model
  and TaskHandoff cannot set it; an ambiguous scope fails closed
"""

from __future__ import annotations

import pytest
from m2_w3_support import (
    FAKE_EXECUTOR_ID,
    ORIGIN_SESSION,
    TEST_SCOPE,
    build_coordinator,
    build_dispatcher,
    make_package,
    open_store,
)
from m2_w3_support import ControlledTransport
from m2_w3_support import DurableWorldAdapter

from aota_forge.core.execution.durable_state import (
    CAS_MUTABLE_EXECUTION_FIELDS,
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPhase,
)
from aota_forge.runtime.completion import (
    AdmissionDecision,
    CompletionAdmissionScopeError,
    CompletionRecoveryRequiredError,
)


@pytest.fixture(autouse=True)
def _fresh_world():
    DurableWorldAdapter.reset_world()
    yield
    DurableWorldAdapter.reset_world()


def new_runtime(store, *, limits):
    adapter = DurableWorldAdapter()
    dispatcher = build_dispatcher(store, adapter)
    coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits=limits)
    return dispatcher, coordinator


class TestAdmissionEnforcement:
    def test_gate_requires_recovery_first(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        with pytest.raises(CompletionRecoveryRequiredError):
            coordinator.admit_dispatch(make_package("early"), target_executor_id=FAKE_EXECUTOR_ID)
        assert DurableWorldAdapter.total_dispatches == 0
        coordinator.recover_once()
        assert coordinator.admit_dispatch(make_package("early"), target_executor_id=FAKE_EXECUTOR_ID)
        assert DurableWorldAdapter.total_dispatches == 1

    def test_limit_one_blocks_second_new_dispatch(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        first = coordinator.admit_dispatch(make_package("c-1", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID)
        assert not isinstance(first, AdmissionDecision)
        second = coordinator.admit_dispatch(make_package("c-2"), target_executor_id=FAKE_EXECUTOR_ID)
        assert isinstance(second, AdmissionDecision) and second.admitted is False
        assert second.reason == "ADMISSION_CAPACITY_EXCEEDED"
        assert second.active_count == 1 and second.limit == 1
        assert DurableWorldAdapter.total_dispatches == 1

    def test_terminal_frees_capacity_while_delivery_pending(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        coordinator.admit_dispatch(make_package("t-1", outcome="success"), target_executor_id=FAKE_EXECUTOR_ID)
        # reconcile to terminal through the durable seam (no delivery yet)
        coordinator.recover_once()
        record = store.get("t-1")
        assert record.canonical_task_state.is_terminal
        assert record.delivery_state == DeliveryState.PENDING
        second = coordinator.admit_dispatch(make_package("t-2", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID)
        assert not isinstance(second, AdmissionDecision)

    def test_unknown_counts_active(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        coordinator.admit_dispatch(make_package("u-1"), target_executor_id=FAKE_EXECUTOR_ID)

        class Unreachable(DurableWorldAdapter):
            def status(self, canonical_task_id, adapter_handle):
                raise RuntimeError("unreachable during recovery")

        dispatcher_b = build_dispatcher(store, Unreachable())
        coordinator_b = build_coordinator(dispatcher_b, store, ControlledTransport(), limits={TEST_SCOPE: 1})
        report = coordinator_b.recover_once()
        assert report.observations["u-1"] == "unknown_persisted"
        blocked = coordinator_b.admit_dispatch(make_package("u-2"), target_executor_id=FAKE_EXECUTOR_ID)
        assert isinstance(blocked, AdmissionDecision) and not blocked.admitted
        assert store.get("u-1").canonical_task_state.value == "UNKNOWN"

    def test_prepared_unresolved_counts_active(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        record = DurableExecutionRecord(
            canonical_task_id="p-1",
            executor_id=FAKE_EXECUTOR_ID,
            package_id="pkg-p1",
            correlation_id="corr-p1",
            dispatch_attempt_id="att-p1",
            idempotency_key="idem-p1",
            intent_fingerprint="e" * 64,
            execution_phase=ExecutionPhase.PREPARED,
            admission_scope=TEST_SCOPE,
        )
        store.create(record)
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        blocked = coordinator.admit_dispatch(make_package("p-2"), target_executor_id=FAKE_EXECUTOR_ID)
        assert isinstance(blocked, AdmissionDecision) and not blocked.admitted
        assert blocked.active_count == 1

    def test_replay_bypasses_admission(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher, coordinator = new_runtime(store, limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        package = make_package("r-1", outcome="running")
        coordinator.admit_dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        replay = coordinator.admit_dispatch(package, target_executor_id=FAKE_EXECUTOR_ID)
        assert not isinstance(replay, AdmissionDecision)  # REPLAY of the existing execution
        assert DurableWorldAdapter.total_dispatches == 1

    def test_scope_isolation_preserves_per_binding_semantics(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        adapter = DurableWorldAdapter()
        from aota_forge.core.execution.dispatcher import ExecutionDispatcher

        dispatcher = ExecutionDispatcher(
            _registry_with(adapter),
            state_store=store,
            origin_session_ref=ORIGIN_SESSION,
            admission_scope_resolver=lambda pkg: f"{FAKE_EXECUTOR_ID}:{'reviewer' if pkg.canonical_role == 'reviewer' else 'coder'}",
        )
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 1, f"{FAKE_EXECUTOR_ID}:reviewer": 1})
        coordinator.recover_once()
        ok = coordinator.admit_dispatch(make_package("s-1", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID)
        assert not isinstance(ok, AdmissionDecision)
        blocked = coordinator.admit_dispatch(make_package("s-2"), target_executor_id=FAKE_EXECUTOR_ID)
        assert isinstance(blocked, AdmissionDecision) and not blocked.admitted
        other = coordinator.admit_dispatch(
            make_package("s-3", canonical_role="reviewer", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID
        )
        assert not isinstance(other, AdmissionDecision)  # different bound bucket unaffected


def _registry_with(adapter):
    from aota_forge.core.execution.registry import ExecutorRegistry

    registry = ExecutorRegistry()
    registry.register(adapter)
    return registry


class TestScopeIntegrity:
    def test_missing_scope_fails_closed_when_limits_exist(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter(), scope_resolver=False)
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 1})
        coordinator.recover_once()
        with pytest.raises(CompletionAdmissionScopeError):
            coordinator.admit_dispatch(make_package("x-1"), target_executor_id=FAKE_EXECUTOR_ID)
        assert DurableWorldAdapter.total_dispatches == 0

    def test_admission_scope_not_cas_mutable(self):
        assert "admission_scope" not in CAS_MUTABLE_EXECUTION_FIELDS

    def test_scope_recorded_at_dispatch_and_survives_restart(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        dispatcher_a, coordinator_a = new_runtime(store_a, limits={TEST_SCOPE: 2})
        coordinator_a.recover_once()
        dispatcher_a.dispatch(make_package("scope-1", outcome="running"), target_executor_id=FAKE_EXECUTOR_ID)
        assert store_a.get("scope-1").admission_scope == TEST_SCOPE
        store_b = open_store(tmp_path / "state.json")
        assert store_b.get("scope-1").admission_scope == TEST_SCOPE  # deterministic reconstruction
