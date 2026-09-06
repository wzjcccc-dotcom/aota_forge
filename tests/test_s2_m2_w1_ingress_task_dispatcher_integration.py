"""S2/M2/W1 proof: Canonical Ingress -> Task -> Dispatcher Integration.

Topology proven (terminal boundary is dispatcher integration):

    execution.task_start
    -> canonical ingress validation
    -> ExecutionPackage (operation=task_dispatch)
    -> ExecutionDispatcher.dispatch()
    -> ExecutorRegistry selection
    -> selected adapter

W1 is test-only integration proof on the converged S2/M1 + S3/M1 source base.
No production behavior is added here; S2/M1 core task authority and S3 Hermes
runtime ownership are asserted to remain intact. No real Hermes subprocess,
JRV1 rendezvous, completion reconciliation, resume ingress, or persistence is
exercised (those belong downstream of W1).
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping

from aota_forge.composition.execution import bind_production_execution_dispatcher
from aota_forge.core.execution import (
    CanonicalResult,
    CanonicalTaskState,
    CancelResult,
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import (
    bind_execution_dispatcher,
    execute,
    reset_execution_dispatcher,
)


class W1RecordingAdapter(ExecutorAdapter):
    """Test-only adapter recording exactly what reaches it through the boundary."""

    def __init__(self, executor_id: str, handle_prefix: str = "adapter-private") -> None:
        self.executor_id = executor_id
        self.handle_prefix = handle_prefix
        self.fixed_handle: str | None = None
        self._capabilities = ExecutorCapabilities(
            executor_id=executor_id,
            adapter_kind="w1_integration_test_double",
            supported_execution_modes=("async", "sync"),
            supports_streaming_events=False,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=False,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("none",),
            supports_working_directory=True,
            supports_artifact_transport=False,
        )
        self.validate_packages: list[ExecutionPackage] = []
        self.dispatched_packages: list[ExecutionPackage] = []

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        self.validate_packages.append(package)
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.dispatched_packages.append(package)
        handle = self.fixed_handle or f"{self.handle_prefix}-{len(self.dispatched_packages)}"
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=handle,
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time="2026-08-28T00:00:00Z",
        )

    def _out_of_boundary(self, operation: str) -> None:
        raise AssertionError(f"{operation} is outside the W1 dispatcher-integration boundary")

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self._out_of_boundary("status")

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self._out_of_boundary("result")

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self._out_of_boundary("cancel")

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        package: ExecutionPackage,
    ) -> ResumeResult:
        self._out_of_boundary("resume")


class FakeW1HermesHost:
    """In-memory HermesHostClient double; no real Hermes process is spawned."""

    def __init__(self) -> None:
        self.dispatched_payloads: list[dict[str, Any]] = []

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatched_payloads.append(dict(payload))
        return {
            "adapter_handle": "hermes-private-42",
            "status": "pending",
            "dispatch_time": "2026-08-28T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "running", "details": "fake host"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "TASK_NOT_FOUND", "message": "fake host"}}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "cancelled", "details": "fake host"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "fake host"}}


class W1IngressDispatcherTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.alpha = W1RecordingAdapter("w1-alpha", handle_prefix="w1-alpha-private")
        self.beta = W1RecordingAdapter("w1-beta", handle_prefix="w1-beta-private")
        registry = ExecutorRegistry()
        registry.register(self.alpha)
        registry.register(self.beta)
        self.dispatcher = ExecutionDispatcher(registry)
        bind_execution_dispatcher(self.dispatcher)

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def _valid_start_params(self, task_id: str = "s2-m2-w1-task-01") -> dict[str, Any]:
        return {
            "canonical_task_id": task_id,
            "executor": "w1-alpha",
            "role": "coder",
            "instruction": "prove canonical ingress reaches the bound dispatcher",
            "project_id": "aota_forge",
        }

    def test_task_start_reaches_bound_dispatcher_with_canonical_package(self) -> None:
        """Primary positive proof: ingress -> ExecutionPackage -> bound dispatcher."""
        params = self._valid_start_params()
        envelope = execute("execution.task_start", params)

        self.assertTrue(envelope["ok"], f"ingress validation failed: {envelope}")
        self.assertEqual(envelope["audit"]["validation"], "ok")
        self.assertEqual(envelope["audit"]["handler"], "success")

        # canonical task identity preserved end-to-end
        task_id = params["canonical_task_id"]
        self.assertEqual(envelope["data"]["canonical_task_id"], task_id)

        # exactly one package reached the selected adapter through the dispatcher
        self.assertEqual(len(self.alpha.dispatched_packages), 1)
        package = self.alpha.dispatched_packages[0]
        self.assertIsInstance(package, ExecutionPackage)
        self.assertEqual(package.operation, "task_dispatch")
        self.assertEqual(package.canonical_task_id, task_id)

        # dispatch route created on the bound dispatcher
        self.assertTrue(self.dispatcher.has_route(task_id))
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.canonical_task_id, task_id)
        self.assertEqual(route.executor_id, "w1-alpha")
        self.assertEqual(route.adapter_handle, envelope["data"]["adapter_handle"])

    def test_canonical_task_ownership_is_core_and_adapter_handle_is_opaque(self) -> None:
        """canonical_task_id stays Core identity; adapter handle stays private locator."""
        self.alpha.fixed_handle = "adapter-private-123"
        task_id = "s2-m2-w1-task-ownership"
        envelope = execute("execution.task_start", self._valid_start_params(task_id))

        self.assertTrue(envelope["ok"])
        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.canonical_task_id, task_id)
        self.assertEqual(route.adapter_handle, "adapter-private-123")
        # distinct identity domains: Core never adopts the adapter-private locator
        self.assertNotEqual(route.canonical_task_id, route.adapter_handle)
        self.assertNotIn("adapter-private", route.canonical_task_id)

    def test_explicit_executor_selection_without_broadcast(self) -> None:
        """Only the explicitly selected registered executor is reached."""
        envelope = execute("execution.task_start", self._valid_start_params())
        self.assertTrue(envelope["ok"])

        self.assertEqual(len(self.alpha.dispatched_packages), 1)
        self.assertEqual(len(self.alpha.validate_packages), 1)
        self.assertEqual(len(self.beta.dispatched_packages), 0)
        self.assertEqual(len(self.beta.validate_packages), 0)

    def test_invalid_required_input_never_reaches_dispatch(self) -> None:
        """Missing required ingress input fails at canonical validation with zero side effects."""
        params = self._valid_start_params("s2-m2-w1-invalid-missing")
        del params["instruction"]
        envelope = execute("execution.task_start", params)

        self.assertFalse(envelope["ok"])
        self.assertNotEqual(envelope["audit"]["validation"], "ok")
        self.assertEqual(len(self.alpha.dispatched_packages), 0)
        self.assertEqual(len(self.beta.dispatched_packages), 0)
        self.assertEqual(len(self.alpha.validate_packages), 0)
        self.assertFalse(self.dispatcher.has_route("s2-m2-w1-invalid-missing"))

    def test_unknown_canonical_role_fails_closed_before_dispatch(self) -> None:
        """Existing canonical role rule rejects the request before adapter side effects."""
        params = self._valid_start_params("s2-m2-w1-invalid-role")
        params["role"] = "not_a_canonical_role"
        envelope = execute("execution.task_start", params)

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "PACKAGE_INVALID")
        self.assertEqual(len(self.alpha.dispatched_packages), 0)
        self.assertEqual(len(self.beta.dispatched_packages), 0)
        self.assertFalse(self.dispatcher.has_route("s2-m2-w1-invalid-role"))

    def test_unbound_dispatcher_fails_closed_at_ingress(self) -> None:
        """task_start requires the accepted dispatcher boundary; no implicit fallback."""
        reset_execution_dispatcher()
        envelope = execute("execution.task_start", self._valid_start_params("s2-m2-w1-unbound"))

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["audit"]["handler"], "dispatcher_unbound")
        self.assertEqual(len(self.alpha.dispatched_packages), 0)
        self.assertEqual(len(self.beta.dispatched_packages), 0)


class W1ProductionHermesCompositionTestCase(unittest.TestCase):
    """Ingress -> dispatcher -> accepted production hermes executor via fake host only."""

    def setUp(self) -> None:
        reset_execution_dispatcher()
        self.host = FakeW1HermesHost()
        self.dispatcher = bind_production_execution_dispatcher(host_client=self.host)

    def tearDown(self) -> None:
        reset_execution_dispatcher()

    def test_production_composition_ingress_task_dispatch(self) -> None:
        task_id = "s2-m2-w1-production-hermes-task"
        envelope = execute(
            "execution.task_start",
            {
                "canonical_task_id": task_id,
                "executor": "hermes",
                "role": "coder",
                "instruction": "prove production composition routes canonical task_start",
                "project_id": "aota_forge",
            },
        )

        self.assertTrue(envelope["ok"], f"production ingress path failed: {envelope}")
        self.assertEqual(envelope["data"]["canonical_task_id"], task_id)
        self.assertEqual(envelope["data"]["adapter_handle"], "hermes-private-42")

        route = self.dispatcher.get_route(task_id)
        self.assertEqual(route.executor_id, "hermes")
        self.assertEqual(route.canonical_task_id, task_id)
        self.assertNotEqual(route.adapter_handle, route.canonical_task_id)

        self.assertEqual(len(self.host.dispatched_payloads), 1)
        payload = self.host.dispatched_payloads[0]
        # M1 accepted architecture: all Worker roles share the aota-worker
        # profile with the shared AOTA MCP toolset pin.
        self.assertEqual(payload["profile"], "aota-worker")
        self.assertEqual(payload["toolsets"], ["aota"])
        self.assertEqual(payload["context"]["canonical_task_id"], task_id)

    def test_production_hermes_capabilities_unchanged(self) -> None:
        """The accepted production Hermes capability vector is read-only preserved for W1."""
        desc = self.dispatcher.registry.get_descriptor("hermes")
        self.assertEqual(desc.executor_id, "hermes")
        self.assertEqual(tuple(desc.supported_execution_modes), ("async",))
        # M1/W1 binds the four Worker canonical roles on the shared profile.
        self.assertEqual(tuple(desc.supported_canonical_roles), ("coder", "planner", "reviewer", "steward"))
        self.assertTrue(desc.supports_task_cancellation)
        self.assertFalse(desc.supports_task_resume)
        self.assertFalse(desc.supports_structured_result)
        self.assertFalse(desc.supports_artifact_transport)
        self.assertTrue(desc.supports_working_directory)


if __name__ == "__main__":
    unittest.main()
