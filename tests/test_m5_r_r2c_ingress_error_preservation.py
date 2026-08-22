"""M5-R-R2C ingress preservation and exception-boundary regression tests."""

from __future__ import annotations

import json

from aota_forge.adapters.execution.reference import REFERENCE_EXECUTOR_ID, ReferenceFakeExecutorAdapter
from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    HermesDispatchRejectedError,
)
from aota_forge.core.execution import (
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, execute, reset_execution_dispatcher


class _RaisingHost:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def dispatch(self, payload):
        raise self.error

    def query_status(self, adapter_handle):
        return {"status": "pending"}

    def fetch_result(self, adapter_handle):
        return {"status": "done"}

    def cancel_task(self, adapter_handle):
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle, payload):
        return {"status": "running"}


class _UnexpectedAdapter(ReferenceFakeExecutorAdapter):
    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        raise RuntimeError("private adapter/runtime object")


def _bind(adapter: ExecutorAdapter) -> None:
    registry = ExecutorRegistry()
    registry.register(adapter)
    bind_execution_dispatcher(ExecutionDispatcher(registry))


def setup_function() -> None:
    reset_execution_dispatcher()


def teardown_function() -> None:
    reset_execution_dispatcher()


def test_executor_unavailable_is_preserved_at_ingress() -> None:
    _bind(HermesAdapter(host_client=None))

    result = execute(
        "execution.task_start",
        {
            "executor": "hermes",
            "role": "coder",
            "instruction": "unavailable host",
            "project_id": "p1",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "EXECUTOR_UNAVAILABLE"
    assert result["errors"][0]["code"] == "EXECUTOR_UNAVAILABLE"


def test_dispatch_rejected_is_preserved_at_ingress() -> None:
    _bind(HermesAdapter(host_client=_RaisingHost(HermesDispatchRejectedError("dispatch denied"))))

    result = execute(
        "execution.task_start",
        {
            "executor": "hermes",
            "role": "coder",
            "instruction": "rejected dispatch",
            "project_id": "p1",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "DISPATCH_REJECTED"
    assert result["errors"][0]["code"] == "DISPATCH_REJECTED"


def test_unexpected_exception_maps_to_internal_mechanical_error() -> None:
    _bind(_UnexpectedAdapter())

    result = execute(
        "execution.task_start",
        {
            "executor": REFERENCE_EXECUTOR_ID,
            "role": "coder",
            "instruction": "unexpected adapter failure",
            "project_id": "p1",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INTERNAL_MECHANICAL_ERROR"


def test_unexpected_exception_details_are_not_leaked() -> None:
    _bind(_UnexpectedAdapter())

    result = execute(
        "execution.task_start",
        {
            "executor": REFERENCE_EXECUTOR_ID,
            "role": "coder",
            "instruction": "private detail probe",
            "project_id": "p1",
        },
    )
    serialized = json.dumps(result, sort_keys=True)

    assert "private adapter/runtime object" not in serialized
    assert "RuntimeError" not in serialized
    assert "Traceback" not in serialized
    assert result["error"] == {
        "code": "INTERNAL_MECHANICAL_ERROR",
        "message": "internal mechanical execution error",
        "retryable": False,
    }


def test_ingress_cancel_replay_remains_idempotent() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    package = ExecutionPackage.create(
        canonical_task_id="r2c-cancel-replay",
        project_id="p1",
        canonical_role="executor",
        instruction="cancel exactly once",
    )
    registry = ExecutorRegistry()
    registry.register(adapter)
    dispatcher = ExecutionDispatcher(registry)
    bind_execution_dispatcher(dispatcher)
    dispatcher.dispatch(package, target_executor_id=REFERENCE_EXECUTOR_ID)
    adapter.simulate_running(package.canonical_task_id)

    params = {"task_id": package.canonical_task_id, "executor": REFERENCE_EXECUTOR_ID}
    first = execute("execution.task_cancel", params)
    replay = execute("execution.task_cancel", params)

    assert first["ok"] is True
    assert replay["ok"] is True
    assert replay["data"] == first["data"]
    assert adapter.cancel_count == 1
