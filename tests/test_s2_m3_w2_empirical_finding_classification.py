"""S2/M3/W2 proof: Empirical Finding Classification.

Bounded classification evidence for the three accepted S2/M3/W1 findings
(W1-F01, W1-F02, W1-F03). This file does NOT re-run the W1 adversarial
matrix; it pins down only the semantic questions W1 evidence left open and
that W2 needs in order to assign a primary defect class with unambiguous
ownership:

    W1-F01  Can the ACCEPTED canonical ingress/error vocabulary already
            represent a typed duplicate/dispatch-rejection outcome, or is
            the loss a contract insufficiency?
            -> proves: DISPATCH_REJECTED is accepted (ExecutionErrorCode
               + execution.task_start descriptor), the ingress already
               preserves any exception carrying that canonical code, and
               the duplicate rejection is raised without such a code.
               Loss is at the S2 dispatcher/ingress projection seam; the
               generic contract is sufficient. CLASS=S2_CONTROL_PLANE_DEFECT.

    W1-F02  Is the collapse specific to adapter exception IDENTITY (would
            require an adapter-side change) or to the reconcile_status
            exception FILTER (fixable entirely inside S2)?
            -> proves: reconcile_status re-raises a Core AdapterProtocolError
               by type but collapses an executor-neutral exception carrying
               the canonical ADAPTER_PROTOCOL_ERROR code into UNKNOWN, while
               dispatcher.status propagates the identical exception. The
               ordinary TASK_HANDLE_NOT_FOUND absence remains accepted
               uncertainty. Narrow repair boundary lives in S2.
               CLASS=S2_CONTROL_PLANE_DEFECT.

    W1-F03  Can the existing generic contract express "result read failed
            transiently, terminal truth not yet trustworthy" without
            falsely declaring terminal failure?
            -> proves: CanonicalResult.unknown (state=UNKNOWN, non-terminal,
               retryable error) is contract-valid, and the SAME frozen
               HermesAdapter already uses uncertainty for transient
               query_status failures while projecting transient fetch_result
               failures as terminal FAILED/retryable=False. The first
               incorrect semantic decision is the adapter projection choice;
               Core then correctly honors a contract-valid terminal result.
               No S1 contract insufficiency. CLASS=S3_ADAPTER_RUNTIME_DEFECT.

S1/M4 escalation threshold check (Plan #19 M3 / #16 feedback trigger):
no finding below requires a canonical schema change; each is repairable
within its owning layer while preserving the accepted contracts.

Durability boundary unchanged (D0): no restart, no cross-process claim;
this file only reads behavior of live in-process objects.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from aota_forge.core import ingress
from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapter,
    HermesAdapterError,
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
    DispatcherError,
    ExecutionDispatcher,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState


class _CanonicalCodeBearingError(Exception):
    """Executor-neutral exception that carries a canonical taxonomy code.

    Mirrors the shape of HermesAdapterError (a plain exception with a
    ``code`` attribute) without depending on Hermes, so the ingress
    pass-through and the reconcile filter are challenged at the generic
    contract surface.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _HostDouble:
    """Minimal conforming HermesHostClient for the W2 projection probes."""

    def __init__(self, fail_fetch: bool = False) -> None:
        self.fail_fetch = fail_fetch

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = payload["context"]["canonical_task_id"]
        return {
            "adapter_handle": f"w2-handle-{task_id}",
            "status": "pending",
            "dispatch_time": "2026-08-29T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        raise RuntimeError("w2 transient transport noise")

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        raise RuntimeError("w2 transient transport noise")

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        raise AssertionError("cancel is not part of the W2 surface")

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError("resume is not part of the W2 surface")


class _FilterProbeAdapter(ExecutorAdapter):
    """Adapter whose status() raises a caller-selected exception.

    Used only to challenge the production ExecutionDispatcher's own
    reconcile/status exception handling; implements no reconciliation.
    """

    def __init__(self, raise_exc: Exception) -> None:
        self._raise_exc = raise_exc
        self._capabilities = ExecutorCapabilities(
            executor_id="w2-filter-probe",
            adapter_kind="w2_filter_probe",
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
            adapter_handle=f"w2-probe-handle-{package.canonical_task_id}",
            initial_state=CanonicalTaskState.QUEUED,
            dispatch_time="2026-08-29T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        raise self._raise_exc

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        raise AssertionError("result is not part of the W2 filter probe")

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        raise AssertionError("cancel is not part of the W2 filter probe")

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        raise AssertionError("resume is not part of the W2 filter probe")


def _probe_package(task_id: str) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="S2/M3/W2 classification probe",
        operation="task_dispatch",
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=f"w2-probe-idem-{task_id}",
        correlation_id=f"w2-probe-corr-{task_id}",
    )


def _probe_dispatcher(raise_exc: Exception) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(_FilterProbeAdapter(raise_exc))
    return ExecutionDispatcher(registry)


class W2F01IngressVocabularyClassification(unittest.TestCase):
    """W1-F01: the loss point and the accepted vocabulary's expressiveness."""

    def test_duplicate_prefix_is_outside_accepted_vocabulary(self) -> None:
        self.assertNotIn(
            "DUPLICATE_CANONICAL_TASK_ID",
            ingress._CANONICAL_EXECUTION_ERROR_CODES,
        )
        # the exact ingress projection path: the typed prefix degrades here
        degraded = ingress._typed_execution_error(
            DispatcherError(
                "DUPLICATE_CANONICAL_TASK_ID: Task 'w2-f01' has already been dispatched"
            )
        )
        self.assertIsNone(degraded)

    def test_accepted_vocabulary_already_represents_typed_dispatch_rejection(self) -> None:
        # DISPATCH_REJECTED is part of the accepted canonical ingress/error
        # vocabulary both by enum and by the declarative task_start contract:
        # the required semantics (typed, non-retryable dispatch rejection)
        # ARE representable without any S1 contract change.
        self.assertIn("DISPATCH_REJECTED", ingress._CANONICAL_EXECUTION_ERROR_CODES)
        descriptor = ingress.get_execution_descriptor("execution.task_start")
        self.assertIsNotNone(descriptor)
        self.assertIn("DISPATCH_REJECTED", descriptor.errors)
        preserved = ingress._typed_execution_error(
            _CanonicalCodeBearingError(
                "duplicate canonical_task_id dispatch rejected",
                code="DISPATCH_REJECTED",
            )
        )
        self.assertIsNotNone(preserved)
        self.assertEqual(preserved[0], "DISPATCH_REJECTED")

    def test_duplicate_rejection_raise_shape_after_r1_repair(self) -> None:
        # W2 classification recorded the defect here: the raised error was the
        # untyped base DispatcherError whose typed semantics lived only in the
        # message string, so ingress could not project it (first incorrect
        # semantic decision inside S2, not the canonical contract).
        # S2/M3/R1 closed the seam: the rejection is now raised as a typed
        # DispatcherError carrying the ACCEPTED canonical code
        # DISPATCH_REJECTED, with the DUPLICATE_CANONICAL_TASK_ID detail
        # preserved in the message; no new canonical code was introduced.
        dispatcher = _probe_dispatcher(RuntimeError("unused"))
        package = _probe_package("w2-f01-duplicate")
        dispatcher.dispatch(package, target_executor_id="w2-filter-probe")

        second = ExecutionPackage.create(
            canonical_task_id="w2-f01-duplicate",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="S2/M3/W2 classification probe",
            operation="task_dispatch",
            capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
            idempotency_key="w2-f01-duplicate-fresh-key",
            correlation_id="w2-f01-duplicate-corr-2",
        )
        with self.assertRaises(DispatcherError) as ctx:
            dispatcher.dispatch(second, target_executor_id="w2-filter-probe")
        self.assertEqual(getattr(ctx.exception, "code", None), "DISPATCH_REJECTED")
        self.assertIn("DUPLICATE_CANONICAL_TASK_ID", str(ctx.exception))
        # the ingress projection accepts it without any vocabulary change
        projected = ingress._typed_execution_error(ctx.exception)
        self.assertIsNotNone(projected)
        self.assertEqual(projected[0], "DISPATCH_REJECTED")
        # fresh key + duplicate task id is still a rejection, never a REPLAY:
        # no second route was committed
        self.assertEqual(len(dispatcher.list_routes()), 1)


class W2F02ReconcileFilterClassification(unittest.TestCase):
    """W1-F02: the collapse lives in the S2 reconcile_status filter."""

    def test_core_typed_protocol_error_propagates_from_reconcile(self) -> None:
        dispatcher = _probe_dispatcher(
            AdapterProtocolError("core-recognized protocol contradiction")
        )
        dispatcher.dispatch(
            _probe_package("w2-f02-core-typed"), target_executor_id="w2-filter-probe"
        )
        with self.assertRaises(AdapterProtocolError):
            dispatcher.reconcile_status("w2-f02-core-typed")

    def test_adapter_emitted_canonical_protocol_code_fails_closed_after_r1_repair(self) -> None:
        # W2 classification recorded the collapse here: reconcile_status turned
        # this ACCEPTED canonical protocol code into ordinary UNKNOWN while
        # status() propagated the identical exception, proving the minimal
        # repair lives in S2's exception filter (executor-neutral), not in any
        # adapter. S2/M3/R1 closed the seam: the filter now fails closed on
        # the canonical protocol code while the ordinary TASK_HANDLE_NOT_FOUND
        # contrast below still reconciles to UNKNOWN.
        integrity = _CanonicalCodeBearingError(
            "inconsistent task/handle binding",
            code="ADAPTER_PROTOCOL_ERROR",
        )
        self.assertIn("ADAPTER_PROTOCOL_ERROR", ingress._CANONICAL_EXECUTION_ERROR_CODES)
        dispatcher = _probe_dispatcher(integrity)
        dispatcher.dispatch(
            _probe_package("w2-f02-adapter-code"), target_executor_id="w2-filter-probe"
        )

        # status() propagates the identical exception: fail closed
        with self.assertRaises(_CanonicalCodeBearingError):
            dispatcher.status("w2-f02-adapter-code")

        # repaired: reconcile_status propagates the typed integrity violation
        # instead of recording UNKNOWN; the route state is left unmutated
        route_before = dispatcher.get_route("w2-f02-adapter-code")
        with self.assertRaises(_CanonicalCodeBearingError):
            dispatcher.reconcile_status("w2-f02-adapter-code")
        route_after = dispatcher.get_route("w2-f02-adapter-code")
        self.assertNotEqual(
            route_after.last_known_state, CanonicalTaskState.UNKNOWN
        )
        self.assertEqual(
            route_after.last_known_state, route_before.last_known_state
        )

    def test_ordinary_handle_absence_reconciles_to_unknown_as_accepted(self) -> None:
        # Contract boundary: TASK_HANDLE_NOT_FOUND is NOT a canonical
        # protocol-integrity code; reconciling ordinary absence to UNKNOWN
        # is accepted behavior and must survive the F02 repair.
        absence = HermesAdapterError(
            "TASK_HANDLE_NOT_FOUND: no binding", code="TASK_HANDLE_NOT_FOUND"
        )
        self.assertNotIn("TASK_HANDLE_NOT_FOUND", ingress._CANONICAL_EXECUTION_ERROR_CODES)
        dispatcher = _probe_dispatcher(absence)
        dispatcher.dispatch(
            _probe_package("w2-f02-absence"), target_executor_id="w2-filter-probe"
        )
        self.assertEqual(
            dispatcher.reconcile_status("w2-f02-absence"), CanonicalTaskState.UNKNOWN
        )


class W1F03ProjectionClassification(unittest.TestCase):
    """W1-F03: contract-expressible recoverability; first bad decision is S3."""

    def test_contract_expresses_recoverable_result_read_uncertainty(self) -> None:
        # The existing canonical result contract already represents
        # "result not trustworthy yet, retry later" without touching any
        # schema: non-terminal UNKNOWN + typed retryable error envelope.
        env = CanonicalResult.unknown(
            canonical_task_id="w2-f03-expressibility",
            executor_id=HERMES_EXECUTOR_ID,
            correlation_id="w2-f03-corr",
        )
        self.assertFalse(env.ok)
        self.assertEqual(env.status, "unknown")
        self.assertEqual(env.canonical_task_state, CanonicalTaskState.UNKNOWN.value)
        self.assertFalse(CanonicalTaskState(env.canonical_task_state).is_terminal)
        self.assertEqual(env.error["code"], "TASK_STATE_UNKNOWN")
        self.assertTrue(env.error["retryable"])

    def test_same_frozen_adapter_splits_identical_transient_fault_inconsistently(self) -> None:
        # Identical transient transport failure on the same live binding:
        # status() projects uncertainty, result() projects terminal failure.
        # The wrong semantic decision is made inside the adapter projection,
        # upstream of Core; Core only consumes the contract-valid result.
        adapter = HermesAdapter(host_client=_HostDouble())
        package = ExecutionPackage.create(
            canonical_task_id="w2-f03-inconsistency",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="S2/M3/W2 classification probe",
            operation="task_dispatch",
            capability_requirements={"execution_mode": "async"},
        )
        handle = adapter.dispatch(package).adapter_handle

        status_res = adapter.status(package.canonical_task_id, handle)
        self.assertEqual(status_res.state, CanonicalTaskState.UNKNOWN)

        result_res = adapter.result(package.canonical_task_id, handle)
        self.assertEqual(result_res.canonical_task_state, CanonicalTaskState.FAILED.value)
        self.assertEqual(result_res.error["code"], "ADAPTER_PROTOCOL_ERROR")
        self.assertFalse(result_res.error["retryable"])
        self.assertTrue(CanonicalTaskState(result_res.canonical_task_state).is_terminal)


if __name__ == "__main__":
    unittest.main()
