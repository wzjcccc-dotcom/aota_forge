"""S2/M2/W2 proof: Hermes Completion -> Forge Reconciliation Integration.

Bounded offline integration proof on the frozen joint source lineage
(S2/M1 dcff60b + S3/M1 204b740 + accepted W1 239dfa7):

    FakeHermesHost (deterministic host projection)
    -> real HermesAdapter (accepted S3/M1 projection, unmodified)
    -> real ExecutorRegistry
    -> real ExecutionDispatcher
    -> Core route / status / result reconciliation

Proven here: completion, failure, non-terminal, unknown/unreachable, and
protocol-integrity observations enter Forge/Core reconciliation without
weakening S2/M1 identity authority, terminal monotonicity, or fail-closed
semantics. The active-result empirical probe records the frozen Hermes
TASK_STILL_RUNNING envelope and the ReferenceAdapter TASK_NOT_TERMINAL
contrast; no repair is performed here.

Not exercised: real Hermes subprocess/runtime, S3/M2 sources, resume
ingress, cancellation expansion, or any persistence (those belong to
W3/JRV1 or are explicitly out of W2 scope). Durability stays D0 /
process-local.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapterError,
)
from aota_forge.composition.execution import (
    bind_production_execution_dispatcher,
    create_production_execution_dispatcher,
)
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
    AdapterProtocolError,
    ExecutionDispatcher,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import execute, reset_execution_dispatcher

TARGET_TASK_MARKER = "W2-MARKER-DONE"


class ScriptedHermesHost:
    """Deterministic fake HermesHostClient; no subprocess, no real Hermes.

    Conforms to the frozen S3/M1 HermesHostClient protocol. Responses are
    scripted per adapter_handle; an unset handle projects "unreachable"
    (the accepted ordinary-uncertainty projection).
    """

    def __init__(self) -> None:
        self.dispatched_payloads: list[dict[str, Any]] = []
        self.status_by_handle: dict[str, Any] = {}
        self.result_by_handle: dict[str, Any] = {}

    @staticmethod
    def handle_for(canonical_task_id: str) -> str:
        return f"w2-host-handle-{canonical_task_id}"

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatched_payloads.append(dict(payload))
        task_id = payload["context"]["canonical_task_id"]
        return {
            "adapter_handle": self.handle_for(task_id),
            "status": "pending",
            "dispatch_time": "2026-08-28T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        scripted = self.status_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return dict(scripted)

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        scripted = self.result_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return dict(scripted)

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": False, "status": "running", "details": "not W2 scope"}

    def resume_task(
        self, adapter_handle: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return {
            "status": "error",
            "error": {"code": "RESUME_UNSUPPORTED", "message": "out of W2 scope"},
        }


def hermes_package(task_id: str, instruction: str = "prove W2 completion reconciliation") -> ExecutionPackage:
    """Canonical task_dispatch package compatible with the production Hermes vector."""
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation="task_dispatch",
        capability_requirements={"execution_mode": "async"},
        idempotency_key=f"w2-idem-{task_id}",
        correlation_id=f"w2-corr-{task_id}",
    )


class ContradictionProbeAdapter(ExecutorAdapter):
    """Core-seam double used ONLY to reach the S2/M1 dispatcher identity guards.

    The frozen HermesAdapter rebinds every status/result projection to the
    Core-supplied canonical task identity, so a contradicted response cannot
    be produced through the Hermes path. This probe exists solely to verify
    the closest reachable Core protocol-identity invariants; the primary W2
    reconciliation proofs above always run against the real HermesAdapter.
    """

    def __init__(self, violation: str) -> None:
        self.violation = violation
        self._capabilities = ExecutorCapabilities(
            executor_id="w2-probe",
            adapter_kind="core_guard_probe",
            supported_execution_modes=("async",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=False,
            supported_canonical_roles=("coder", "executor", "planner", "reviewer", "steward"),
            supported_isolation_modes=("none",),
            supports_working_directory=False,
            supports_artifact_transport=False,
        )

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True, errors=())

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=f"w2-probe-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.QUEUED,
            dispatch_time="2026-08-28T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        if self.violation == "ordinary_failure":
            raise RuntimeError("probe ordinary transport noise")
        reported_id = (
            "w2-unrelated-task" if self.violation == "status_task_id" else canonical_task_id
        )
        return TaskStatusResult(canonical_task_id=reported_id, state=CanonicalTaskState.RUNNING)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        reported_id = (
            "w2-unrelated-task" if self.violation == "result_task_id" else canonical_task_id
        )
        reported_executor = (
            "not-the-routed-executor" if self.violation == "result_executor" else "w2-probe"
        )
        return CanonicalResult.success(
            canonical_task_id=reported_id,
            executor_id=reported_executor,
            result_data={},
            correlation_id="probe-corr",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        raise AssertionError("cancel is not part of the W2 probe surface")

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        raise AssertionError("resume is explicitly out of W2 scope")


class W2ProductionHermesReconciliationTestCase(unittest.TestCase):
    """Primary proofs: real HermesAdapter + real dispatcher via accepted production composition."""

    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.host = ScriptedHermesHost()
        # Preferred §9 topology: accepted S3/M1 production wiring with a bounded fake host.
        self.dispatcher = create_production_execution_dispatcher(host_client=self.host)
        self.adapter = self.dispatcher.registry.get(HERMES_EXECUTOR_ID)

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def _dispatch(self, task_id: str) -> DispatchResult:
        result = self.dispatcher.dispatch(hermes_package(task_id), target_executor_id=HERMES_EXECUTOR_ID)
        self.assertEqual(result.canonical_task_id, task_id)
        return result

    def test_completed_result_reconciles_to_core_terminal(self) -> None:
        task_id = "s2-m2-w2-completed-01"
        dispatch = self._dispatch(task_id)
        handle = dispatch.adapter_handle
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.initial_state, CanonicalTaskState.QUEUED)
        self.assertEqual(route.last_known_state, CanonicalTaskState.QUEUED)

        self.host.status_by_handle[handle] = {"status": "done", "details": TARGET_TASK_MARKER}
        status_res = self.dispatcher.status(task_id)
        self.assertIsInstance(status_res, TaskStatusResult)
        self.assertEqual(status_res.state, CanonicalTaskState.COMPLETED)
        self.assertEqual(status_res.canonical_task_id, task_id)

        self.host.result_by_handle[handle] = {
            "status": "done",
            "exit_code": 0,
            "stdout_summary": TARGET_TASK_MARKER,
        }
        result = self.dispatcher.result(task_id)
        self.assertIsInstance(result, CanonicalResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.COMPLETED.value)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_summary, TARGET_TASK_MARKER)
        # identity domains stay distinct and correct
        self.assertEqual(result.canonical_task_id, task_id)
        self.assertEqual(result.executor_id, HERMES_EXECUTOR_ID)
        self.assertEqual(self.dispatcher.get_route(task_id).executor_id, HERMES_EXECUTOR_ID)
        self.assertNotEqual(task_id, route.adapter_handle)
        # terminal truth recorded on the Core route
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )
        # coherent terminal replay does not raise
        self.assertTrue(self.dispatcher.result(task_id).ok)

    def test_completed_reconciliation_through_canonical_ingress(self) -> None:
        task_id = "s2-m2-w2-ingress-completed"
        dispatcher = bind_production_execution_dispatcher(host_client=self.host)

        start = execute(
            "execution.task_start",
            {
                "canonical_task_id": task_id,
                "executor": HERMES_EXECUTOR_ID,
                "role": "coder",
                "instruction": "prove completion reaches Forge reconciliation via ingress",
                "project_id": "aota_forge",
            },
        )
        self.assertTrue(start["ok"], f"task_start failed: {start}")
        handle = start["data"]["adapter_handle"]
        self.assertEqual(handle, ScriptedHermesHost.handle_for(task_id))

        self.host.status_by_handle[handle] = {"status": "done"}
        status_env = execute(
            "execution.task_status", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID}
        )
        self.assertTrue(status_env["ok"], f"task_status failed: {status_env}")
        self.assertEqual(status_env["data"]["state"], CanonicalTaskState.COMPLETED.value)

        self.host.result_by_handle[handle] = {
            "status": "done",
            "exit_code": 0,
            "stdout_summary": TARGET_TASK_MARKER,
        }
        result_env = execute(
            "execution.task_result", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID}
        )
        self.assertTrue(result_env["ok"], f"task_result failed: {result_env}")
        self.assertEqual(result_env["data"]["status"], "completed")
        self.assertEqual(result_env["data"]["canonical_task_id"], task_id)
        self.assertEqual(result_env["data"]["executor_id"], HERMES_EXECUTOR_ID)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.COMPLETED
        )

    def test_failed_completion_reconciles_as_failed_not_unknown(self) -> None:
        task_id = "s2-m2-w2-failed-01"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = {"status": "failed", "details": "bounded failure"}
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.state, CanonicalTaskState.FAILED)
        self.assertNotEqual(status_res.state, CanonicalTaskState.UNKNOWN)

        self.host.result_by_handle[handle] = {
            "status": "failed",
            "exit_code": 7,
            "stderr_summary": "bounded-error",
            "error": {
                "code": "TASK_EXIT_NONZERO",
                "message": "bounded failure output",
                "retryable": False,
            },
        }
        result = self.dispatcher.result(task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.FAILED.value)
        self.assertEqual(result.error["code"], "TASK_EXIT_NONZERO")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stderr_summary, "bounded-error")
        # failure is a definite terminal truth, never projected as UNKNOWN
        self.assertNotEqual(result.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertNotEqual(
            self.dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.UNKNOWN
        )
        # canonical identities preserved on the failure envelope
        self.assertEqual(result.canonical_task_id, task_id)
        self.assertEqual(result.executor_id, HERMES_EXECUTOR_ID)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.FAILED
        )

    def test_nonterminal_status_reconciles_without_fabricating_terminal(self) -> None:
        task_id = "s2-m2-w2-nonterminal-01"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = {"status": "pending"}
        self.assertEqual(
            self.dispatcher.status(task_id).state, CanonicalTaskState.QUEUED
        )

        self.host.status_by_handle[handle] = {"status": "running"}
        self.assertEqual(self.dispatcher.status(task_id).state, CanonicalTaskState.RUNNING)
        route = self.dispatcher.get_route(task_id)
        self.assertFalse(route.last_known_state.is_terminal)
        self.assertEqual(route.last_known_state, CanonicalTaskState.RUNNING)

        # terminal result is not fabricated from a live task: reconciliation
        # stays non-terminal and no completion truth is recorded.
        self.host.result_by_handle[handle] = {"status": "running", "stdout_summary": "partial"}
        active = self.dispatcher.result(task_id)
        self.assertFalse(active.ok)
        self.assertEqual(active.canonical_task_state, CanonicalTaskState.RUNNING.value)
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

        # polling sequence QUEUED/RUNNING -> later COMPLETED works on the same route
        self.host.status_by_handle[handle] = {"status": "done"}
        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.COMPLETED
        )
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_known_terminal_state_cannot_regress(self) -> None:
        task_id = "s2-m2-w2-monotonic-01"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = {"status": "done"}
        self.dispatcher.status(task_id)
        self.host.result_by_handle[handle] = {"status": "done", "exit_code": 0}
        self.assertTrue(self.dispatcher.result(task_id).ok)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

        # once Core holds a known terminal truth, contradictory projections
        # fail closed and never regress or mutate the route.
        for contradictory in ("running", "pending", "weird-status", "failed"):
            self.host.status_by_handle[handle] = {"status": contradictory}
            with self.assertRaises(AdapterProtocolError):
                self.dispatcher.status(task_id)
            self.assertEqual(
                self.dispatcher.get_route(task_id).last_known_state,
                CanonicalTaskState.COMPLETED,
            )
            with self.assertRaises(AdapterProtocolError):
                self.dispatcher.reconcile_status(task_id)
            self.assertEqual(
                self.dispatcher.get_route(task_id).last_known_state,
                CanonicalTaskState.COMPLETED,
            )

        # a contradictory terminal result is rejected the same way
        self.host.result_by_handle[handle] = {"status": "failed", "exit_code": 3}
        with self.assertRaises(AdapterProtocolError):
            self.dispatcher.result(task_id)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_host_projection_cannot_override_canonical_identity(self) -> None:
        task_id = "s2-m2-w2-identity-rebind"
        handle = self._dispatch(task_id).adapter_handle

        self.host.status_by_handle[handle] = {
            "status": "done",
            "canonical_task_id": "evil-unrelated-task",
        }
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.canonical_task_id, task_id)

        self.host.result_by_handle[handle] = {
            "status": "done",
            "exit_code": 0,
            "canonical_task_id": "evil-unrelated-task",
            "executor_id": "evil-executor",
            "hermes_session": {"forbidden": True},
        }
        result = self.dispatcher.result(task_id)
        self.assertEqual(result.canonical_task_id, task_id)
        self.assertEqual(result.executor_id, HERMES_EXECUTOR_ID)
        # Hermes-private envelope material may not escape into the result data
        self.assertNotIn("hermes_session", result.result_data)
        self.assertNotIn("hermes_session", result.execution_stats)
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.COMPLETED,
        )

    def test_unknown_or_unreachable_stays_uncertain_and_recovers(self) -> None:
        task_id = "s2-m2-w2-unknown-01"
        handle = self._dispatch(task_id).adapter_handle

        # accepted S3 projection: unreachable maps to UNKNOWN, never completion
        self.host.status_by_handle[handle] = {"status": "unreachable"}
        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )
        self.assertEqual(
            self.dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.UNKNOWN,
        )

        # host transport exception is ordinary runtime uncertainty -> UNKNOWN
        self.host.status_by_handle[handle] = RuntimeError("host connection refused")
        status_res = self.dispatcher.status(task_id)
        self.assertEqual(status_res.state, CanonicalTaskState.UNKNOWN)
        self.assertNotEqual(status_res.state, CanonicalTaskState.COMPLETED)

        # unknown result envelope is non-ok; no terminal truth is fabricated
        self.host.result_by_handle[handle] = {
            "status": "unreachable",
            "error": {"message": "host unreachable"},
        }
        unknown_result = self.dispatcher.result(task_id)
        self.assertFalse(unknown_result.ok)
        self.assertEqual(unknown_result.status, "unknown")
        self.assertEqual(unknown_result.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertEqual(unknown_result.error["code"], "TASK_STATE_UNKNOWN")
        self.assertFalse(
            self.dispatcher.get_route(task_id).last_known_state.is_terminal
        )

        # uncertainty is reconcilable: later recovery reaches COMPLETED
        self.host.status_by_handle[handle] = {"status": "done"}
        self.assertEqual(
            self.dispatcher.reconcile_status(task_id), CanonicalTaskState.COMPLETED
        )

    def test_adapter_binding_guard_rejects_crossed_task_handle_identity(self) -> None:
        """Frozen HermesAdapter raises on crossed identity instead of projecting UNKNOWN."""
        task_a = "s2-m2-w2-binding-a"
        task_b = "s2-m2-w2-binding-b"
        handle_a = self._dispatch(task_a).adapter_handle
        handle_b = self._dispatch(task_b).adapter_handle

        before_a = self.dispatcher.get_route(task_a).last_known_state
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status(task_a, handle_b)
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.result(task_b, handle_a)
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status(task_a, "w2-never-dispatched")
        self.assertEqual(ctx.exception.code, "TASK_ID_MISMATCH")
        with self.assertRaises(HermesAdapterError) as ctx:
            self.adapter.status("w2-never-task", "w2-never-dispatched")
        self.assertEqual(ctx.exception.code, "TASK_HANDLE_NOT_FOUND")

        # the binding guard raises; it is never swallowed into UNKNOWN
        route_a = self.dispatcher.get_route(task_a)
        self.assertEqual(route_a.last_known_state, before_a)
        self.assertNotEqual(route_a.last_known_state, CanonicalTaskState.UNKNOWN)

    def test_active_result_empirical_offline_probe(self) -> None:
        """Record exact frozen S3/M1 Hermes behavior for result() on an active task."""
        task_id = "s2-m2-w2-active-result"
        handle = self._dispatch(task_id).adapter_handle
        self.host.status_by_handle[handle] = {"status": "running"}
        self.dispatcher.status(task_id)
        route_before = self.dispatcher.get_route(task_id)

        self.host.result_by_handle[handle] = {
            "status": "running",
            "stdout_summary": "partial work",
        }
        result = self.dispatcher.result(task_id)

        # exact observable behavior of the frozen Hermes projection
        self.assertIsInstance(result, CanonicalResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.canonical_task_state, CanonicalTaskState.RUNNING.value)
        self.assertEqual(result.error["code"], "TASK_STILL_RUNNING")
        self.assertTrue(result.error["retryable"])
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.canonical_task_id, task_id)
        self.assertEqual(result.executor_id, HERMES_EXECUTOR_ID)
        self.assertEqual(result.stdout_summary, "partial work")

        route_after = self.dispatcher.get_route(task_id)
        self.assertEqual(route_after.last_known_state, CanonicalTaskState.RUNNING)
        self.assertFalse(route_after.last_known_state.is_terminal)
        self.assertEqual(route_after.adapter_handle, route_before.adapter_handle)
        self.assertEqual(route_after.dispatch_attempt_id, route_before.dispatch_attempt_id)

        # ReferenceAdapter contrast: active result raises instead of returning
        # an envelope. The difference is observed only; no repair is performed.
        reference = ReferenceFakeExecutorAdapter()
        registry = ExecutorRegistry()
        registry.register(reference)
        reference_dispatcher = ExecutionDispatcher(registry)
        reference_dispatcher.dispatch(hermes_package("s2-m2-w2-reference-active"))
        with self.assertRaises(ValueError) as ctx:
            reference_dispatcher.result("s2-m2-w2-reference-active")
        self.assertIn("TASK_NOT_TERMINAL", str(ctx.exception))
        self.assertFalse(
            reference_dispatcher.get_route("s2-m2-w2-reference-active")
            .last_known_state.is_terminal
        )

    def test_result_identity_and_capability_vector_preserved(self) -> None:
        """Positive Core guards that the frozen Hermes projection must not bypass."""
        task_id = "s2-m2-w2-guard-positive"
        handle = self._dispatch(task_id).adapter_handle
        self.host.result_by_handle[handle] = {"status": "done", "exit_code": 0}
        result = self.dispatcher.result(task_id)
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(result.canonical_task_id, route.canonical_task_id)
        self.assertEqual(result.executor_id, route.executor_id)
        descriptor = self.dispatcher.registry.get_descriptor(HERMES_EXECUTOR_ID)
        self.assertEqual(tuple(descriptor.supported_execution_modes), ("async",))
        self.assertFalse(descriptor.supports_task_resume)


class W2CoreProtocolGuardTestCase(unittest.TestCase):
    """Closest reachable Core seams for contradicted adapter responses.

    The frozen HermesAdapter cannot emit contradicted identities (proved in
    the tests above), so these guards are reached with a minimal Core-seam
    probe double. This does not weaken the primary Hermes-backed proofs; it
    exercises the accepted S2/M1 dispatcher guards W2 must not regress.
    """

    def _probe_dispatcher(self, violation: str) -> ExecutionDispatcher:
        registry = ExecutorRegistry()
        registry.register(ContradictionProbeAdapter(violation))
        dispatcher = ExecutionDispatcher(registry)
        dispatcher.dispatch(
            hermes_package("s2-m2-w2-probe-task"), target_executor_id="w2-probe"
        )
        return dispatcher

    def test_status_identity_mismatch_fails_closed_without_route_mutation(self) -> None:
        dispatcher = self._probe_dispatcher("status_task_id")
        task_id = "s2-m2-w2-probe-task"
        with self.assertRaises(AdapterProtocolError):
            dispatcher.status(task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.QUEUED
        )

    def test_result_task_identity_mismatch_fails_closed_without_route_mutation(self) -> None:
        dispatcher = self._probe_dispatcher("result_task_id")
        task_id = "s2-m2-w2-probe-task"
        with self.assertRaises(AdapterProtocolError):
            dispatcher.result(task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.QUEUED
        )

    def test_result_executor_identity_mismatch_fails_closed_without_route_mutation(self) -> None:
        dispatcher = self._probe_dispatcher("result_executor")
        task_id = "s2-m2-w2-probe-task"
        with self.assertRaises(AdapterProtocolError):
            dispatcher.result(task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.QUEUED
        )

    def test_protocol_mismatch_is_not_swallowed_as_unknown(self) -> None:
        dispatcher = self._probe_dispatcher("status_task_id")
        task_id = "s2-m2-w2-probe-task"
        with self.assertRaises(AdapterProtocolError):
            dispatcher.reconcile_status(task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.QUEUED
        )

    def test_ordinary_adapter_failure_reconciles_to_unknown(self) -> None:
        dispatcher = self._probe_dispatcher("ordinary_failure")
        task_id = "s2-m2-w2-probe-task"
        self.assertEqual(
            dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state, CanonicalTaskState.UNKNOWN
        )


if __name__ == "__main__":
    unittest.main()
