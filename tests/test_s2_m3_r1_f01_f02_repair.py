"""S2/M3/R1 bounded repair proof for W1-F01 and W1-F02 (S2 control-plane defects).

Repaired defects (classified in S2/M3/W2 on base 662e030e):

    W1-F01  Duplicate canonical_task_id dispatch with a FRESH idempotency key
            was projected by the canonical ingress as
            INTERNAL_MECHANICAL_ERROR. The rejection now crosses ingress as
            the already-accepted canonical dispatch-rejection semantic
            DISPATCH_REJECTED; the DUPLICATE_CANONICAL_TASK_ID detail stays
            in the message and is NOT added to the canonical vocabulary.

    W1-F02  ExecutionDispatcher.reconcile_status collapsed executor-emitted
            typed protocol/binding integrity violations (accepted canonical
            code ADAPTER_PROTOCOL_ERROR) into UNKNOWN. The reconcile filter
            is now executor-neutral: it fails closed on exceptions carrying
            the accepted canonical protocol code while ordinary runtime
            uncertainty (e.g. TASK_HANDLE_NOT_FOUND absence, transport noise)
            still reconciles to UNKNOWN.

Scope guards proven here:

    - no second adapter dispatch side effect and no route mutation on either
      rejected path;
    - idempotency semantics unchanged:
      same key + same fingerprint -> REPLAY, same key + changed fingerprint
      -> CONFLICT;
    - a duplicate task id with a fresh idempotency key remains a rejection,
      never a REPLAY.

This file does NOT re-run the W1 adversarial matrix and performs no F03
(S3 adapter runtime) repair. All scenarios run inside ONE live process
(D0 boundary: no restart, no cross-process claim).
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapterError,
)
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core import ingress
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
    DispatcherError,
    DuplicateCanonicalTaskIdError,
    ExecutionDispatcher,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import (
    bind_execution_dispatcher,
    execute,
    reset_execution_dispatcher,
)

REPAIR_INSTRUCTION = "S2/M3/R1 repair probe"


class _CanonicalCodeBearingError(Exception):
    """Executor-neutral exception carrying a canonical taxonomy code.

    Mirrors the shape of adapter-owned errors (plain exception + ``code``
    attribute) without depending on any Hermes class, so the repaired
    reconcile filter is proven at the generic contract surface.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _RepairHermesHost:
    """Deterministic offline HermesHostClient double; no subprocess."""

    def __init__(self) -> None:
        self.dispatch_payloads: list[dict[str, Any]] = []
        self.status_calls: list[str] = []

    @staticmethod
    def handle_for(canonical_task_id: str) -> str:
        return f"r1-host-handle-{canonical_task_id}"

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatch_payloads.append(dict(payload))
        task_id = payload["context"]["canonical_task_id"]
        return {
            "adapter_handle": self.handle_for(task_id),
            "status": "pending",
            "dispatch_time": "2026-08-29T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.status_calls.append(adapter_handle)
        return {"status": "unreachable"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "unreachable"}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": False, "status": "running", "details": "not an R1 case"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "not an R1 case"}}


class _RaisingProbeAdapter(ExecutorAdapter):
    """Adapter whose status() raises a caller-selected exception.

    Challenges the production ExecutionDispatcher reconcile filter only;
    implements no reconciliation logic of its own.
    """

    def __init__(self, raise_exc: Exception) -> None:
        self._raise_exc = raise_exc
        self._capabilities = ExecutorCapabilities(
            executor_id="r1-probe",
            adapter_kind="r1_repair_probe",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=False,
            supported_canonical_roles=("coder",),
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
            adapter_handle=f"r1-probe-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.QUEUED,
            dispatch_time="2026-08-29T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        raise self._raise_exc

    def result(self, canonical_task_id: str, adapter_handle: str) -> Any:
        raise AssertionError("result is not part of the R1 probe surface")

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        raise AssertionError("cancel is not part of the R1 probe surface")

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        raise AssertionError("resume is not part of the R1 probe surface")


def _probe_package(task_id: str, *, idempotency_key: str | None = None) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=REPAIR_INSTRUCTION,
        operation="task_dispatch",
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=idempotency_key or f"r1-probe-idem-{task_id}",
        correlation_id=f"r1-probe-corr-{task_id}",
    )


def _probe_dispatcher(raise_exc: Exception) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(_RaisingProbeAdapter(raise_exc))
    return ExecutionDispatcher(registry)


def _hermes_package(task_id: str, *, idempotency_key: str | None = None) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=REPAIR_INSTRUCTION,
        operation="task_dispatch",
        capability_requirements={"execution_mode": "async"},
        idempotency_key=idempotency_key or f"r1-hermes-idem-{task_id}",
        correlation_id=f"r1-hermes-corr-{task_id}",
    )


def _hermes_start_params(task_id: str, *, idempotency_key: str | None = None, instruction: str = REPAIR_INSTRUCTION) -> dict[str, Any]:
    params: dict[str, Any] = {
        "canonical_task_id": task_id,
        "executor": HERMES_EXECUTOR_ID,
        "role": "coder",
        "instruction": instruction,
        "project_id": "aota_forge",
    }
    if idempotency_key is not None:
        params["idempotency_key"] = idempotency_key
    return params


class S2M3R1F01DuplicateDispatchIngressRepair(unittest.TestCase):
    """W1-F01: duplicate dispatch crosses canonical ingress as DISPATCH_REJECTED."""

    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.host = _RepairHermesHost()
        self.dispatcher = create_production_execution_dispatcher(host_client=self.host)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def test_duplicate_task_start_projects_typed_dispatch_rejected(self) -> None:
        task_id = "s2-m3-r1-f01-ingress"
        first = execute("execution.task_start", _hermes_start_params(task_id))
        self.assertTrue(first["ok"], first)

        duplicate = execute("execution.task_start", _hermes_start_params(task_id))
        self.assertFalse(duplicate["ok"])
        # repaired projection: accepted typed dispatch rejection, not
        # INTERNAL_MECHANICAL_ERROR (the pre-repair W1-F01 collapse)
        self.assertEqual(duplicate["error"]["code"], "DISPATCH_REJECTED")
        self.assertNotEqual(duplicate["error"]["code"], "INTERNAL_MECHANICAL_ERROR")
        # the rejection detail is preserved inside the message
        self.assertIn("DUPLICATE_CANONICAL_TASK_ID", duplicate["error"]["message"])
        self.assertIn(task_id, duplicate["error"]["message"])
        self.assertFalse(duplicate["error"]["retryable"])
        self.assertEqual(duplicate["audit"]["handler"], "dispatch_rejected")
        # evidence that the generic vocabulary was NOT extended
        self.assertIn("DISPATCH_REJECTED", ingress._CANONICAL_EXECUTION_ERROR_CODES)
        self.assertNotIn("DUPLICATE_CANONICAL_TASK_ID", ingress._CANONICAL_EXECUTION_ERROR_CODES)

    def test_duplicate_rejection_has_zero_second_adapter_side_effect(self) -> None:
        task_id = "s2-m3-r1-f01-side-effect"
        first = execute("execution.task_start", _hermes_start_params(task_id))
        self.assertTrue(first["ok"], first)
        route_before = self.dispatcher.get_route(task_id)

        duplicate = execute("execution.task_start", _hermes_start_params(task_id))
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["error"]["code"], "DISPATCH_REJECTED")

        # exactly one host dispatch; route count and original route untouched
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(len(self.dispatcher.list_routes()), 1)
        route_after = self.dispatcher.get_route(task_id)
        self.assertEqual(route_after.adapter_handle, route_before.adapter_handle)
        self.assertEqual(route_after.dispatch_attempt_id, route_before.dispatch_attempt_id)
        self.assertEqual(route_after.last_known_state, route_before.last_known_state)
        # original dispatch result preserved
        self.assertEqual(first["data"]["adapter_handle"], route_after.adapter_handle)

    def test_dispatcher_level_duplicate_rejection_is_typed(self) -> None:
        task_id = "s2-m3-r1-f01-dispatcher"
        first = self.dispatcher.dispatch(
            _hermes_package(task_id), target_executor_id=HERMES_EXECUTOR_ID
        )
        with self.assertRaises(DispatcherError) as ctx:
            self.dispatcher.dispatch(
                _hermes_package(task_id, idempotency_key="r1-fresh-key"),
                target_executor_id=HERMES_EXECUTOR_ID,
            )
        self.assertIsInstance(ctx.exception, DuplicateCanonicalTaskIdError)
        self.assertEqual(ctx.exception.code, "DISPATCH_REJECTED")
        self.assertIn("DUPLICATE_CANONICAL_TASK_ID", str(ctx.exception))
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(
            self.dispatcher.get_route(task_id).adapter_handle, first.adapter_handle
        )

    def test_replay_semantics_unchanged(self) -> None:
        task_id = "s2-m3-r1-f01-replay"
        first = execute(
            "execution.task_start",
            _hermes_start_params(task_id, idempotency_key="r1-replay-key"),
        )
        replay = execute(
            "execution.task_start",
            _hermes_start_params(task_id, idempotency_key="r1-replay-key"),
        )
        self.assertTrue(first["ok"], first)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(first["data"], replay["data"])
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(len(self.dispatcher.list_routes()), 1)

    def test_conflict_semantics_unchanged(self) -> None:
        task_id = "s2-m3-r1-f01-conflict"
        first = execute(
            "execution.task_start",
            _hermes_start_params(task_id, idempotency_key="r1-conflict-key"),
        )
        self.assertTrue(first["ok"], first)
        conflict = execute(
            "execution.task_start",
            _hermes_start_params(
                "s2-m3-r1-f01-conflict-b",
                instruction="a semantically different intent",
                idempotency_key="r1-conflict-key",
            ),
        )
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.host.dispatch_payloads), 1)
        self.assertEqual(len(self.dispatcher.list_routes()), 1)


class S2M3R1F02ReconcileProtocolIntegrityRepair(unittest.TestCase):
    """W1-F02: reconcile_status fails closed on typed protocol integrity codes."""

    def test_executor_neutral_protocol_code_fails_closed_and_route_unchanged(self) -> None:
        integrity = _CanonicalCodeBearingError(
            "inconsistent task/handle binding", code="ADAPTER_PROTOCOL_ERROR"
        )
        dispatcher = _probe_dispatcher(integrity)
        task_id = "s2-m3-r1-f02-neutral"
        dispatcher.dispatch(_probe_package(task_id), target_executor_id="r1-probe")
        before = dispatcher.get_route(task_id)

        with self.assertRaises(_CanonicalCodeBearingError):
            dispatcher.reconcile_status(task_id)

        after = dispatcher.get_route(task_id)
        self.assertEqual(after.canonical_task_id, before.canonical_task_id)
        self.assertEqual(after.executor_id, before.executor_id)
        self.assertEqual(after.adapter_handle, before.adapter_handle)
        self.assertEqual(after.dispatch_attempt_id, before.dispatch_attempt_id)
        self.assertEqual(after.last_known_state, before.last_known_state)
        # the violation must never be recorded as ordinary uncertainty
        self.assertNotEqual(after.last_known_state, CanonicalTaskState.UNKNOWN)

    def test_core_adapter_protocol_error_still_propagates(self) -> None:
        dispatcher = _probe_dispatcher(
            AdapterProtocolError("core-recognized protocol contradiction")
        )
        task_id = "s2-m3-r1-f02-core"
        dispatcher.dispatch(_probe_package(task_id), target_executor_id="r1-probe")
        with self.assertRaises(AdapterProtocolError):
            dispatcher.reconcile_status(task_id)
        self.assertEqual(
            dispatcher.get_route(task_id).last_known_state,
            CanonicalTaskState.QUEUED,
        )

    def test_ordinary_absence_still_reconciles_to_unknown(self) -> None:
        absence = _CanonicalCodeBearingError("no binding", code="TASK_HANDLE_NOT_FOUND")
        dispatcher = _probe_dispatcher(absence)
        task_id = "s2-m3-r1-f02-absence"
        dispatcher.dispatch(_probe_package(task_id), target_executor_id="r1-probe")
        self.assertEqual(
            dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )

    def test_ordinary_transport_uncertainty_still_reconciles_to_unknown(self) -> None:
        dispatcher = _probe_dispatcher(RuntimeError("ordinary transport noise"))
        task_id = "s2-m3-r1-f02-transport"
        dispatcher.dispatch(_probe_package(task_id), target_executor_id="r1-probe")
        self.assertEqual(
            dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )

    def test_real_hermes_partial_binding_fails_closed_without_route_mutation(self) -> None:
        host = _RepairHermesHost()
        dispatcher = create_production_execution_dispatcher(host_client=host)
        adapter = dispatcher.registry.get(HERMES_EXECUTOR_ID)
        task_id = "s2-m3-r1-f02-partial"
        handle = dispatcher.dispatch(
            _hermes_package(task_id), target_executor_id=HERMES_EXECUTOR_ID
        ).adapter_handle
        # adapter forgets only the handle->task side: inconsistent binding
        adapter._handle_tasks.pop(handle)

        before = dispatcher.get_route(task_id)
        with self.assertRaises(HermesAdapterError) as ctx:
            dispatcher.reconcile_status(task_id)
        self.assertEqual(ctx.exception.code, "ADAPTER_PROTOCOL_ERROR")

        after = dispatcher.get_route(task_id)
        self.assertEqual(after.canonical_task_id, before.canonical_task_id)
        self.assertEqual(after.executor_id, before.executor_id)
        self.assertEqual(after.adapter_handle, before.adapter_handle)
        self.assertEqual(after.dispatch_attempt_id, before.dispatch_attempt_id)
        self.assertEqual(after.last_known_state, before.last_known_state)
        self.assertNotEqual(after.last_known_state, CanonicalTaskState.UNKNOWN)
        self.assertEqual(host.status_calls, [])

    def test_real_hermes_full_handle_loss_still_reconciles_to_unknown(self) -> None:
        host = _RepairHermesHost()
        dispatcher = create_production_execution_dispatcher(host_client=host)
        adapter = dispatcher.registry.get(HERMES_EXECUTOR_ID)
        task_id = "s2-m3-r1-f02-full-loss"
        handle = dispatcher.dispatch(
            _hermes_package(task_id), target_executor_id=HERMES_EXECUTOR_ID
        ).adapter_handle
        adapter._handle_tasks.pop(handle)
        adapter._task_handles.pop(task_id)

        # accepted contract: full handle absence is ordinary runtime uncertainty
        self.assertEqual(
            dispatcher.reconcile_status(task_id), CanonicalTaskState.UNKNOWN
        )
        self.assertEqual(host.status_calls, [])


if __name__ == "__main__":
    unittest.main()
