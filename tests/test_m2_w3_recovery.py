"""M2/W3 — bounded startup recovery and integrated restart challenges.

Deterministic R1–R8 over the W1 durable seam + W2 mechanical boundary
(fake-adapter world / W2 supervisor-pad emulation; the REAL Hermes variant of
R8/RV lives in tests/test_m2_w3_real_hermes_delivery.py, opt-in):

- recover_once is bounded, store-driven, and safe to run repeatedly
- RUNNING keeps nonterminal durable state; TERMINAL persists CanonicalResult
  + governance + CARD via the normal seams; UNKNOWN stays UNKNOWN with no
  fabricated completion and no blind redispatch
- the PREPARED crash window stays exactly-once-limited: durable UNKNOWN,
  reconciliation error on the same idempotency attempt, never a silent redo
- R1 route restart / R2 terminal-before-delivery restart / R3 pending claim
  restart / R4 ACK restart / R5 busy origin / R6 missing origin /
  R7 concurrency restart / R8 AF crash while Hermes child is running
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from hermes_durable_support import DurableSupervisorPad
from m2_w3_support import (
    TEST_SCOPE,
    ack_completed,
    build_coordinator,
    build_dispatcher,
    claim_delivery,
    make_package,
    open_store,
    retryable_busy,
)
from m2_w3_support import ControlledTransport, DurableWorldAdapter, not_found_missing_session

from aota_forge.adapters.hermes.executor import HERMES_EXECUTOR_ID, HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import DispatchOutcomeUnresolvedError, ExecutionDispatcher
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPhase,
    OriginSessionRef,
)
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.roles import CANONICAL_ROLES, RoleMapping
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.runtime.completion import (
    RECOVER_PREPARED_UNRESOLVED_UNKNOWN,
    RECOVER_TERMINAL_ALREADY_DURABLE,
    RECOVER_TERMINAL_PERSISTED,
    RECOVER_UNKNOWN_PERSISTED,
    RECOVER_RUNNING,
)

R8_TASK = "m2-w3-r8-real-boundary"

FAKE_HERMES_R8 = """#!/bin/sh
instr=""
usage=""
while [ $# -gt 0 ]; do
  case "$1" in
    --usage-file) usage="$2"; shift 2;;
    -z) instr="$2"; shift 2;;
    *) shift;;
  esac
done
echo "r8 worker completed: $instr"
if [ -n "$usage" ]; then
  printf '{"session_id":"20260906_120000_r8dable","failed":false,"completed":true}\\n' > "$usage"
fi
exit 0
"""


@pytest.fixture(autouse=True)
def _fresh_world():
    DurableWorldAdapter.reset_world()
    yield
    DurableWorldAdapter.reset_world()


def terminal_record(store, task_id="task-w3-recover"):
    dispatcher = build_dispatcher(store, DurableWorldAdapter())
    coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
    dispatcher.dispatch(make_package(task_id), target_executor_id="durable-fake")
    coordinator.recover_once()
    return dispatcher, coordinator


class TestBoundedRecovery:
    def test_recover_once_is_bounded_and_repeatable(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package("rep-1"), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        first = coordinator.recover_once()
        second = coordinator.recover_once()
        assert first.observations["rep-1"] == RECOVER_TERMINAL_PERSISTED
        assert second.observations["rep-1"] == RECOVER_TERMINAL_ALREADY_DURABLE
        assert DurableWorldAdapter.total_dispatches == 1  # recovery NEVER redispatches

    def test_running_stays_nonterminal(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package("run-1", outcome="running"), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        report = coordinator.recover_once()
        assert report.observations["run-1"] == RECOVER_RUNNING
        record = store.get("run-1")
        assert record.canonical_task_state == CanonicalTaskState.RUNNING
        assert record.terminal_result is None

    def test_unknown_never_fabricates_completion(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package("unk-1"), target_executor_id="durable-fake")
        # Fresh adapter instance whose transport world is unreachable.
        class Unreachable(DurableWorldAdapter):
            def status(self, canonical_task_id, adapter_handle):
                raise RuntimeError("executor unreachable at recovery time")

            def result(self, canonical_task_id, adapter_handle):
                raise RuntimeError("executor unreachable at recovery time")

        dispatcher_b = build_dispatcher(store, Unreachable())
        coordinator_b = build_coordinator(dispatcher_b, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        report = coordinator_b.recover_once()
        assert report.observations["unk-1"] == RECOVER_UNKNOWN_PERSISTED
        record = store.get("unk-1")
        assert record.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert record.terminal_result is None  # no fabricated completion
        assert Unreachable.total_dispatches == 1  # class counter: zero new dispatches

    def test_failed_terminal_persists_failure_card(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package("fail-1", outcome="failed"), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        coordinator.recover_once()
        record = store.get("fail-1")
        assert record.canonical_task_state == CanonicalTaskState.FAILED
        assert record.terminal_result is not None
        assert record.terminal_result.status == "failed"
        assert record.worker_result_card is not None
        assert record.delivery_state == DeliveryState.PENDING

    def test_hermes_exit_zero_is_not_result_authority(self, tmp_path):
        # A canonical result projection decides semantics; governance never
        # reads process exit codes (no exit_code token in the coordinator).
        import inspect

        import aota_forge.runtime.completion as completion

        source = inspect.getsource(completion.governance_projection_for_result)
        assert "exit_code" not in source


class TestPreparedCrashWindow:
    def test_prepared_without_locator_becomes_durable_unknown(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        record = DurableExecutionRecord(
            canonical_task_id="prepared-1",
            executor_id="durable-fake",
            package_id="pkg-1",
            correlation_id="corr-1",
            dispatch_attempt_id="att-1",
            idempotency_key="idem-prepared-1",
            intent_fingerprint="f" * 64,
            execution_phase=ExecutionPhase.PREPARED,
            admission_scope=TEST_SCOPE,
        )
        store.create(record)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        report = coordinator.recover_once()
        assert report.observations["prepared-1"] == RECOVER_PREPARED_UNRESOLVED_UNKNOWN
        after = store.get("prepared-1")
        assert after.canonical_task_state == CanonicalTaskState.UNKNOWN
        assert after.execution_phase == ExecutionPhase.PREPARED
        assert DurableWorldAdapter.total_dispatches == 0  # never blindly redispatched

    def test_same_idempotency_attempt_stays_unresolved(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        package = make_package("prepared-2", idem_key="idem-p2")
        record = DurableExecutionRecord(
            canonical_task_id=package.canonical_task_id,
            executor_id="durable-fake",
            package_id=package.package_id,
            correlation_id=package.correlation_id,
            dispatch_attempt_id="att-2",
            idempotency_key=package.idempotency_key,
            intent_fingerprint=package.intent_fingerprint,
            execution_phase=ExecutionPhase.PREPARED,
            admission_scope=TEST_SCOPE,
        )
        store.create(record)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 8})
        coordinator.recover_once()
        with pytest.raises(DispatchOutcomeUnresolvedError):
            coordinator.admit_dispatch(package, target_executor_id="durable-fake")
        assert DurableWorldAdapter.total_dispatches == 0


class TestIntegratedRestarts:
    """R1–R8 as full runtime-object destroy/recreate chains on one store."""

    def test_r1_route_restart(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        dispatcher_a = build_dispatcher(store_a, DurableWorldAdapter())
        dispatcher_a.dispatch(make_package("r1"), target_executor_id="durable-fake")
        store_a.close()

        store_b = open_store(tmp_path / "state.json")
        adapter_b = DurableWorldAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        route = dispatcher_b.get_route("r1")  # route reconstructed, no old objects
        assert route.adapter_handle.startswith("durable-fake::")
        status = dispatcher_b.status("r1")
        assert status.state.is_terminal
        assert adapter_b.physical_dispatch_count == 0

    def test_r2_terminal_before_delivery_restart(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        dispatcher_a = build_dispatcher(store_a, DurableWorldAdapter())
        dispatcher_a.dispatch(make_package("r2"), target_executor_id="durable-fake")
        coordinator_a = build_coordinator(dispatcher_a, store_a, ControlledTransport(), limits={TEST_SCOPE: 4})
        coordinator_a.recover_once()  # terminal result + CARD persisted; delivery not yet attempted
        store_a.close()

        store_b = open_store(tmp_path / "state.json")
        dispatcher_b = build_dispatcher(store_b, DurableWorldAdapter())
        record = store_b.get("r2")
        assert record.terminal_result is not None and record.worker_result_card is not None
        assert record.delivery_state == DeliveryState.PENDING
        digest = record.worker_result_card_digest
        transport_b = ControlledTransport(default=ack_completed("r2", digest))
        coordinator_b = build_coordinator(dispatcher_b, store_b, transport_b, limits={TEST_SCOPE: 4})
        assert coordinator_b.deliver_pending_once().outcomes["r2"] == "acknowledged"

    def test_r3_pending_delivery_restart_then_ack(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        dispatcher_a = build_dispatcher(store_a, DurableWorldAdapter())
        dispatcher_a.dispatch(make_package("r3"), target_executor_id="durable-fake")
        coordinator_a = build_coordinator(dispatcher_a, store_a, ControlledTransport(), limits={TEST_SCOPE: 4})
        coordinator_a.recover_once()
        # Crash right after claiming (before any ACK): stale claim is durable.
        claim_delivery(store_a, "r3", owner="crashed-process", until="2000-01-01T00:00:00+00:00", attempt=1)
        store_a.close()

        store_b = open_store(tmp_path / "state.json")
        dispatcher_b = build_dispatcher(store_b, DurableWorldAdapter())
        digest = store_b.get("r3").worker_result_card_digest
        coordinator_b = build_coordinator(
            dispatcher_b, store_b, ControlledTransport(script=[retryable_busy(), ack_completed("r3", digest)])
        )
        first = coordinator_b.deliver_pending_once()
        assert first.reclaimed_expired_claims == ["r3"]
        assert first.outcomes["r3"] == "released_retryable"
        second = coordinator_b.deliver_pending_once()
        assert second.outcomes["r3"] == "acknowledged"
        assert store_b.get("r3").delivery_state == DeliveryState.ACKNOWLEDGED

    def test_r4_ack_restart_no_redelivery(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        dispatcher_a, coordinator_a = terminal_record(store_a, "r4")
        digest = store_a.get("r4").worker_result_card_digest
        coordinator_a._transport = ControlledTransport(default=ack_completed("r4", digest))
        assert coordinator_a.deliver_pending_once().outcomes["r4"] == "acknowledged"
        store_a.close()

        # Restart AFTER the ACK was durable: no scan, no delivery, no attempt.
        store_b = open_store(tmp_path / "state.json")
        dispatcher_b = build_dispatcher(store_b, DurableWorldAdapter())
        coordinator_b = build_coordinator(dispatcher_b, store_b, ControlledTransport(default=retryable_busy()))
        transport = coordinator_b._transport
        recovery = coordinator_b.recover_once()
        delivery = coordinator_b.deliver_pending_once()
        assert delivery.outcomes.get("r4") is None  # ACKNOWLEDGED: out of every scan
        assert all(r.canonical_task_id != "r4" for r in store_b.scan_requiring_recovery())
        assert transport.attempts == []
        assert recovery.observations.get("r4") is None
        assert store_b.get("r4").delivery_state == DeliveryState.ACKNOWLEDGED
        assert store_b.get("r4").delivery_attempt == 1
        assert dispatcher_b.registry.get("durable-fake").physical_dispatch_count == 0

    def test_r5_busy_origin_session_keeps_pending_then_later_succeeds(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        terminal_record(store, "r5")
        record = store.get("r5")
        digest = record.worker_result_card_digest
        coordinator = build_coordinator(
            build_dispatcher(store, DurableWorldAdapter()),
            store,
            ControlledTransport(script=[retryable_busy(), retryable_busy(), ack_completed("r5", digest)]),
        )
        assert coordinator.deliver_pending_once().outcomes["r5"] == "released_retryable"
        after_busy = store.get("r5")
        assert after_busy.delivery_state == DeliveryState.PENDING
        assert after_busy.terminal_result is not None  # result persisted, nothing lost
        assert coordinator.deliver_pending_once().outcomes["r5"] == "released_retryable"
        assert coordinator.deliver_pending_once().outcomes["r5"] == "acknowledged"

    def test_r6_missing_origin_session_fail_closed(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        dispatcher.dispatch(make_package("r6"), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
        coordinator.recover_once()
        coordinator._transport = ControlledTransport(default=not_found_missing_session())
        report = coordinator.deliver_pending_once()
        assert report.outcomes["r6"] == "dropped_session_missing"
        final = store.get("r6")
        assert final.terminal_result is not None and final.worker_result_card is not None
        assert final.delivery_state == DeliveryState.DROPPED
        # No new session: the transport recorded exactly one bounded attempt.
        assert len(coordinator._transport.attempts) == 1

    def test_r7_concurrency_survives_restart(self, tmp_path):
        store_a = open_store(tmp_path / "state.json")
        adapter_a = DurableWorldAdapter()
        dispatcher_a = build_dispatcher(store_a, adapter_a)
        coordinator_a = build_coordinator(dispatcher_a, store_a, ControlledTransport(), limits={TEST_SCOPE: 2})
        coordinator_a.recover_once()
        from aota_forge.runtime.completion import AdmissionDecision

        assert coordinator_a.admit_dispatch(make_package("r7-1", outcome="running"), target_executor_id="durable-fake")
        assert coordinator_a.admit_dispatch(make_package("r7-2", outcome="running"), target_executor_id="durable-fake")
        assert DurableWorldAdapter.total_dispatches == 2
        store_a.close()

        # Fresh runtime objects; same durable store.
        store_b = open_store(tmp_path / "state.json")
        adapter_b = DurableWorldAdapter()
        dispatcher_b = build_dispatcher(store_b, adapter_b)
        coordinator_b = build_coordinator(dispatcher_b, store_b, ControlledTransport(), limits={TEST_SCOPE: 2})
        coordinator_b.recover_once()  # active-set reconstructed
        third = coordinator_b.admit_dispatch(make_package("r7-3"), target_executor_id="durable-fake")
        assert isinstance(third, AdmissionDecision) and third.admitted is False
        assert third.active_count == 2 and third.limit == 2
        assert adapter_b.physical_dispatch_count == 0  # physical dispatch count unchanged

    def test_restart_admission_after_one_terminal(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 2})
        coordinator.recover_once()
        coordinator.admit_dispatch(make_package("slot-1", outcome="success"), target_executor_id="durable-fake")
        coordinator.admit_dispatch(make_package("slot-2", outcome="running"), target_executor_id="durable-fake")
        # slot-1 completes through the durable terminal seam
        coordinator.recover_once()
        coordinator.admit_dispatch(make_package("slot-3", outcome="running"), target_executor_id="durable-fake")
        blocked = coordinator.admit_dispatch(make_package("slot-4"), target_executor_id="durable-fake")
        from aota_forge.runtime.completion import AdmissionDecision

        assert isinstance(blocked, AdmissionDecision) and blocked.admitted is False
        assert blocked.active_count == 2


class TestR8CrashWhileHermesRunning:
    """Real durable boundary mechanics: HermesHostClient + supervisor emulator."""

    @staticmethod
    def _hermes_registry(store, launcher, runtime_root):
        host = HermesHostClient(
            launcher,
            default_cwd=str(runtime_root.parent),
            runtime_root=runtime_root,
            validate_launcher=True,
        )
        capabilities = ExecutorCapabilities(
            executor_id=HERMES_EXECUTOR_ID,
            adapter_kind="hermes_host_adapter",
            supported_execution_modes=("async",),
            supports_streaming_events=False,
            supports_task_cancellation=True,
            supports_task_resume=False,
            supports_structured_result=False,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=False,
            max_timeout_seconds=300,
            concurrency_limit=2,
        )
        adapter = HermesAdapter(
            host_client=host,
            capabilities=capabilities,
            role_mapping=RoleMapping.create(HERMES_EXECUTOR_ID, {"coder": "aota-worker"}),
        )
        registry = ExecutorRegistry()
        registry.register(adapter)
        dispatcher = ExecutionDispatcher(
            registry,
            state_store=store,
            origin_session_ref=OriginSessionRef(value="20260906_120000_r8dable"),
            admission_scope_resolver=lambda package: "hermes:coder",
        )
        return dispatcher, host

    def _package(self, task_id: str):
        # Real Hermes host seam: no durable-fake constraints; execution_mode/
        # isolation are the only declared requirements.
        return ExecutionPackage.create(
            canonical_task_id=task_id,
            project_id="aota_forge",
            canonical_role="coder",
            instruction="m2-w3 r8 real-boundary proof",
            capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
            constraints={},
            idempotency_key=f"idem-{task_id}",
            correlation_id=f"corr-{task_id}",
        )

    def test_r8_af_runtime_vanishes_hermes_child_recovers(self, tmp_path: Path):
        launcher = tmp_path / "fake-hermes-r8.sh"
        launcher.write_text(FAKE_HERMES_R8)
        launcher.chmod(0o755)
        runtime_root = tmp_path / "af-runtime"

        store_a = open_store(tmp_path / "state.json")
        pad = DurableSupervisorPad()
        dispatcher_a, host_a = self._hermes_registry(store_a, str(launcher), runtime_root)
        host_a._popen_factory = pad  # spawn seam replaced; file protocol is REAL
        coordinator_a = build_coordinator(dispatcher_a, store_a, ControlledTransport(), limits={"hermes:coder": 4})
        coordinator_a.recover_once()
        package = self._package(R8_TASK)
        dispatched = dispatcher_a.dispatch(package, target_executor_id=HERMES_EXECUTOR_ID)
        assert dispatched.adapter_handle.startswith("hermes-host-")
        record_a = store_a.get(R8_TASK)
        assert record_a.execution_phase == ExecutionPhase.DISPATCHED

        # AF runtime A disappears entirely: objects destroyed, no Popen reuse.
        del coordinator_a, dispatcher_a, host_a, store_a

        # Fresh B runtime over the same durable store + same runtime root.
        store_b = open_store(tmp_path / "state.json")
        dispatcher_b, host_b = self._hermes_registry(store_b, str(launcher), runtime_root)
        coordinator_b = build_coordinator(dispatcher_b, store_b, ControlledTransport(), limits={"hermes:coder": 4})
        report = coordinator_b.recover_once()
        kind = report.observations[R8_TASK]
        assert kind in {RECOVER_RUNNING, RECOVER_TERMINAL_PERSISTED, RECOVER_UNKNOWN_PERSISTED}
        record = store_b.get(R8_TASK)
        assert record.canonical_task_state != CanonicalTaskState.COMPLETED or record.terminal_result is not None

        # Let the supervised child finish for real; recovery then persists
        # canonical truth through the normal seams — no old Popen required.
        assert pad.last is not None
        pad.last.release(0)  # synchronous finalize: the real receipt file lands now
        deadline = time.monotonic() + 10
        while store_b.get(R8_TASK).terminal_result is None and time.monotonic() < deadline:
            report = coordinator_b.recover_once()
            time.sleep(0.05)
        record = store_b.get(R8_TASK)
        assert record.terminal_result is not None, f"terminal truth never reconciled: {report.observations}"
        assert record.terminal_result.canonical_task_id == R8_TASK
        assert record.worker_result_card is not None
        assert record.delivery_state == DeliveryState.PENDING
        digest = record.worker_result_card_digest
        coordinator_b._transport = ControlledTransport(default=ack_completed(R8_TASK, digest))
        assert coordinator_b.deliver_pending_once().outcomes[R8_TASK] == "acknowledged"
        # One supervised launch only: no duplicate dispatch on recovery.
        assert len(pad.launches) == 1
        host_b.close()
