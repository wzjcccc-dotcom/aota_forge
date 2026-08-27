"""S1/M3/W3 - heterogeneous executor challenge.

The three executor shapes are exercised through the production registry and
dispatcher.  The remote executor is deliberately test-local: it proves that
submit/poll/complete semantics fit the existing adapter contract without
creating a production remote runtime or a new Core identity field.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import uuid
from typing import Any, Mapping

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapter,
)
from aota_forge.core.execution import (
    CancelResult,
    CanonicalResult,
    CanonicalTaskState,
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
    is_terminal,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import validate_transition


HETEROGENEOUS_EXECUTOR_MATRIX: tuple[dict[str, str], ...] = (
    {
        "shape": "Hermes async/background",
        "adapter": "HermesAdapter",
        "execution_mode": "async",
        "dispatch_behavior": "host submit returns adapter handle",
        "initial_state": "QUEUED",
        "completion": "later host status and result",
        "runtime_locator": "Hermes adapter_handle",
        "result_timing": "after host completion",
        "failure": "CanonicalResult.failure projection",
        "core_change": "no",
        "result": "PASS",
    },
    {
        "shape": "local sync/direct",
        "adapter": "ReferenceFakeExecutorAdapter(auto_complete=True)",
        "execution_mode": "sync",
        "dispatch_behavior": "invoke returns terminal result path",
        "initial_state": "COMPLETED",
        "completion": "direct during dispatch",
        "runtime_locator": "reference adapter_handle",
        "result_timing": "immediately after dispatch",
        "failure": "existing CanonicalResult failure semantics",
        "core_change": "no",
        "result": "PASS",
    },
    {
        "shape": "remote async/polled",
        "adapter": "RemotePollingFakeAdapter (test-only)",
        "execution_mode": "async",
        "dispatch_behavior": "submit returns opaque remote handle",
        "initial_state": "QUEUED",
        "completion": "test-controlled completion after polling",
        "runtime_locator": "opaque remote-job:// handle",
        "result_timing": "not ready until terminal status",
        "failure": "CanonicalResult.failure projection",
        "core_change": "no",
        "result": "PASS",
    },
)


class BackgroundHermesHost:
    """Injected Hermes host seam; no daemon or production process is started."""

    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []
        self.statuses: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.status_polls = 0

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatched.append(dict(payload))
        handle = "hermes-process-registry:background-1"
        self.statuses[handle] = {"status": "pending", "details": "background queued"}
        return {
            "adapter_handle": handle,
            "status": "pending",
            "dispatch_time": "2026-08-27T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.status_polls += 1
        return self.statuses[adapter_handle]

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return self.results[adapter_handle]

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.statuses[adapter_handle] = {"status": "running"}
        return {"status": "running"}


@dataclass
class _RemoteJob:
    package: ExecutionPackage
    state: CanonicalTaskState = CanonicalTaskState.QUEUED
    result: CanonicalResult | None = None
    details: str = "remote submission queued"


class RemotePollingFakeAdapter(ExecutorAdapter):
    """Test-only submit/poll adapter with a lifecycle unlike the reference fake."""

    EXECUTOR_ID = "remote-polling-test"

    def __init__(self) -> None:
        self._capabilities = ExecutorCapabilities(
            executor_id=self.EXECUTOR_ID,
            adapter_kind="remote_polling_test_double",
            supported_execution_modes=("async",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=False,
            supports_artifact_transport=True,
        )
        self._jobs: dict[str, _RemoteJob] = {}
        self._next_job_number = 1
        self.status_polls = 0
        self.result_requests = 0

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        compatible, reasons = ExecutorRegistry.check_compatibility(self._capabilities, package)
        return ValidationResult(valid=compatible, errors=reasons)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        validation = self.validate_package(package)
        if not validation.valid:
            raise ValueError(f"PACKAGE_INVALID: {validation.errors}")

        handle = f"remote-job://job-{self._next_job_number}"
        self._next_job_number += 1
        self._jobs[handle] = _RemoteJob(package=package)
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=handle,
            initial_state=CanonicalTaskState.QUEUED,
            dispatch_time="2026-08-27T00:00:00Z",
        )

    def _job(self, canonical_task_id: str, adapter_handle: str) -> _RemoteJob:
        job = self._jobs.get(adapter_handle)
        if job is None or job.package.canonical_task_id != canonical_task_id:
            raise ValueError("ADAPTER_PROTOCOL_ERROR: remote handle/task mismatch")
        return job

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self.status_polls += 1
        job = self._job(canonical_task_id, adapter_handle)
        return TaskStatusResult(
            canonical_task_id=canonical_task_id,
            state=job.state,
            details=job.details,
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self.result_requests += 1
        job = self._job(canonical_task_id, adapter_handle)
        if job.state == CanonicalTaskState.UNKNOWN:
            return CanonicalResult.unknown(
                canonical_task_id=canonical_task_id,
                executor_id=self.EXECUTOR_ID,
                error_message=job.details,
                correlation_id=job.package.correlation_id,
            )
        if not job.state.is_terminal:
            raise ValueError(
                f"TASK_NOT_TERMINAL: Task {canonical_task_id!r} is in non-terminal state {job.state.value}"
            )
        if job.result is None:
            raise ValueError("RESULT_MALFORMED: terminal remote job has no result")
        return job.result

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self._job(canonical_task_id, adapter_handle)
        return CancelResult(canonical_task_id, False, CanonicalTaskState.UNKNOWN)

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        self._job(canonical_task_id, adapter_handle)
        raise ValueError("RESUME_UNSUPPORTED: remote polling fake does not support resume")

    def set_provider_status(self, canonical_task_id: str, adapter_handle: str, raw_status: str) -> None:
        """Project provider status to the existing canonical state vocabulary."""
        job = self._job(canonical_task_id, adapter_handle)
        normalized = raw_status.strip().lower()
        if normalized == "running":
            if job.state == CanonicalTaskState.QUEUED:
                validate_transition(job.state, CanonicalTaskState.RUNNING)
            job.state = CanonicalTaskState.RUNNING
            job.details = "remote worker running"
        elif normalized in {"queued", "pending"}:
            job.state = CanonicalTaskState.QUEUED
            job.details = "remote submission queued"
        else:
            job.state = CanonicalTaskState.UNKNOWN
            job.details = f"unrecognized remote provider state: {raw_status}"

    def complete(self, canonical_task_id: str, adapter_handle: str, result_data: dict[str, Any]) -> None:
        job = self._job(canonical_task_id, adapter_handle)
        if job.state == CanonicalTaskState.QUEUED:
            self.set_provider_status(canonical_task_id, adapter_handle, "running")
        validate_transition(job.state, CanonicalTaskState.COMPLETED)
        job.state = CanonicalTaskState.COMPLETED
        job.details = "remote execution completed"
        job.result = CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id=self.EXECUTOR_ID,
            result_data=result_data,
            correlation_id=job.package.correlation_id,
        )

    def fail(self, canonical_task_id: str, adapter_handle: str, message: str) -> None:
        job = self._job(canonical_task_id, adapter_handle)
        if job.state == CanonicalTaskState.QUEUED:
            self.set_provider_status(canonical_task_id, adapter_handle, "running")
        validate_transition(job.state, CanonicalTaskState.FAILED)
        job.state = CanonicalTaskState.FAILED
        job.details = message
        job.result = CanonicalResult.failure(
            canonical_task_id=canonical_task_id,
            executor_id=self.EXECUTOR_ID,
            error_code="EXECUTION_FAILED",
            error_message=message,
            details={"provider_reason": "simulated remote failure"},
            correlation_id=job.package.correlation_id,
        )


def _package(task_id: str, execution_mode: str, package_id: str) -> ExecutionPackage:
    return ExecutionPackage.create(
        package_id=package_id,
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=f"exercise {task_id}",
        capability_requirements={"execution_mode": execution_mode, "isolation": "process"},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"correlation-{task_id}",
    )


def _dispatcher(adapter: ExecutorAdapter) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry)


def test_comparison_matrix_covers_three_materially_different_shapes() -> None:
    assert tuple(row["shape"] for row in HETEROGENEOUS_EXECUTOR_MATRIX) == (
        "Hermes async/background",
        "local sync/direct",
        "remote async/polled",
    )
    required_columns = {
        "shape",
        "adapter",
        "execution_mode",
        "dispatch_behavior",
        "initial_state",
        "completion",
        "runtime_locator",
        "result_timing",
        "failure",
        "core_change",
        "result",
    }
    assert all(set(row) == required_columns for row in HETEROGENEOUS_EXECUTOR_MATRIX)
    assert all(row["core_change"] == "no" and row["result"] == "PASS" for row in HETEROGENEOUS_EXECUTOR_MATRIX)


def test_three_shapes_register_and_match_through_existing_capability_model() -> None:
    hermes = HermesAdapter(host_client=BackgroundHermesHost())
    local = ReferenceFakeExecutorAdapter(auto_complete=True)
    remote = RemotePollingFakeAdapter()
    registry = ExecutorRegistry()
    for adapter in (hermes, local, remote):
        registry.register(adapter)

    assert registry.list_executor_ids() == ["hermes", "reference-fake", "remote-polling-test"]
    expected_modes = {
        HERMES_EXECUTOR_ID: {"async", "sync"},
        "reference-fake": {"async", "batch", "sync"},
        RemotePollingFakeAdapter.EXECUTOR_ID: {"async"},
    }
    for executor_id, modes in expected_modes.items():
        capabilities = registry.get_capabilities(executor_id)
        assert set(capabilities.supported_execution_modes) == modes
        package = _package(f"capability-{executor_id}", "sync" if executor_id == "reference-fake" else "async", f"package-capability-{executor_id}")
        resolution = registry.resolve(package, target_executor_id=executor_id)
        assert resolution.is_resolved
        assert resolution.selected_executor_id == executor_id


def test_hermes_async_background_lifecycle_uses_dispatcher() -> None:
    host = BackgroundHermesHost()
    dispatcher = _dispatcher(HermesAdapter(host_client=host))
    package = _package("hermes-async-shape", "async", "package-hermes-async")

    dispatch = dispatcher.dispatch(package, target_executor_id=HERMES_EXECUTOR_ID)
    assert dispatch.initial_state == CanonicalTaskState.QUEUED
    assert host.dispatched

    host.statuses[dispatch.adapter_handle] = {"status": "running", "details": "background worker active"}
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.RUNNING
    host.statuses[dispatch.adapter_handle] = {"status": "done"}
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.COMPLETED
    host.results[dispatch.adapter_handle] = {
        "status": "done",
        "result_data": {"shape": "hermes", "completed": True},
        "correlation_id": package.correlation_id,
    }
    result = dispatcher.result(package.canonical_task_id)
    assert isinstance(result, CanonicalResult)
    assert result.ok is True
    assert result.result_data == {"shape": "hermes", "completed": True}
    assert host.status_polls == 2


def test_local_sync_executor_completes_directly_through_dispatcher() -> None:
    adapter = ReferenceFakeExecutorAdapter(auto_complete=True)
    dispatcher = _dispatcher(adapter)
    package = _package("local-sync-shape", "sync", "package-local-sync")

    dispatch = dispatcher.dispatch(package, target_executor_id="reference-fake")
    assert dispatch.initial_state == CanonicalTaskState.COMPLETED
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.COMPLETED
    result = dispatcher.result(package.canonical_task_id)
    assert result.ok is True
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert adapter.status_count == 1
    assert adapter.result_count == 1


def test_remote_submit_poll_and_later_success_use_existing_contracts() -> None:
    adapter = RemotePollingFakeAdapter()
    dispatcher = _dispatcher(adapter)
    package = _package("remote-success", "async", "package-remote-success")

    dispatch = dispatcher.dispatch(package, target_executor_id=adapter.EXECUTOR_ID)
    assert dispatch.initial_state == CanonicalTaskState.QUEUED
    assert dispatch.adapter_handle.startswith("remote-job://")
    with pytest.raises(ValueError, match="TASK_NOT_TERMINAL"):
        dispatcher.result(package.canonical_task_id)
    assert adapter.result_requests == 1

    adapter.set_provider_status(package.canonical_task_id, dispatch.adapter_handle, "running")
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.RUNNING
    adapter.complete(package.canonical_task_id, dispatch.adapter_handle, {"shape": "remote", "answer": 42})
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.COMPLETED
    result = dispatcher.result(package.canonical_task_id)
    assert result.ok is True
    assert result.result_data == {"shape": "remote", "answer": 42}
    assert adapter.status_polls == 2


def test_remote_failure_projects_into_existing_canonical_error_result() -> None:
    adapter = RemotePollingFakeAdapter()
    dispatcher = _dispatcher(adapter)
    package = _package("remote-failure", "async", "package-remote-failure")
    dispatch = dispatcher.dispatch(package, target_executor_id=adapter.EXECUTOR_ID)

    adapter.set_provider_status(package.canonical_task_id, dispatch.adapter_handle, "running")
    adapter.fail(package.canonical_task_id, dispatch.adapter_handle, "remote worker rejected workload")
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.FAILED
    result = dispatcher.result(package.canonical_task_id)
    assert isinstance(result, CanonicalResult)
    assert result.ok is False
    assert result.status == "failed"
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.error == {
        "code": "EXECUTION_FAILED",
        "message": "remote worker rejected workload",
        "retryable": False,
        "details": {"provider_reason": "simulated remote failure"},
    }


def test_remote_identity_domains_remain_distinct_and_core_owns_attempt_id() -> None:
    adapter = RemotePollingFakeAdapter()
    dispatcher = _dispatcher(adapter)
    package = _package("remote-identity", "async", "package-remote-identity")
    dispatch = dispatcher.dispatch(package, target_executor_id=adapter.EXECUTOR_ID)
    route = dispatcher.get_route(package.canonical_task_id)

    identity_values = (
        route.canonical_task_id,
        route.dispatch_attempt_id,
        route.adapter_handle,
        route.executor_id,
        route.package_id,
        route.correlation_id,
    )
    assert len(set(identity_values)) == 6
    assert route.canonical_task_id != route.adapter_handle
    assert route.canonical_task_id != route.executor_id
    assert route.adapter_handle != route.executor_id
    assert route.dispatch_attempt_id != route.adapter_handle
    assert route.dispatch_attempt_id != route.canonical_task_id
    assert route.package_id == package.package_id
    assert route.correlation_id == package.correlation_id
    assert uuid.UUID(route.dispatch_attempt_id).version == 4
    assert dispatch.adapter_handle == route.adapter_handle
    assert dispatch.adapter_handle != package.canonical_task_id


def test_remote_unknown_provider_state_stays_unknown_and_never_false_completes() -> None:
    adapter = RemotePollingFakeAdapter()
    dispatcher = _dispatcher(adapter)
    package = _package("remote-unknown", "async", "package-remote-unknown")
    dispatch = dispatcher.dispatch(package, target_executor_id=adapter.EXECUTOR_ID)
    adapter.set_provider_status(package.canonical_task_id, dispatch.adapter_handle, "running")
    adapter.set_provider_status(package.canonical_task_id, dispatch.adapter_handle, "provider_state_not_yet_known")

    reconciled = dispatcher.reconcile_status(package.canonical_task_id)
    assert reconciled == CanonicalTaskState.UNKNOWN
    assert is_terminal(reconciled) is False
    status = dispatcher.status(package.canonical_task_id)
    assert status.state == CanonicalTaskState.UNKNOWN
    result = dispatcher.result(package.canonical_task_id)
    assert result.ok is False
    assert result.status == "unknown"
    assert result.error["code"] == "TASK_STATE_UNKNOWN"


def test_core_schema_has_no_new_remote_or_duplicate_request_result_fields() -> None:
    canonical_types = (
        ExecutionPackage,
        DispatchResult,
        TaskStatusResult,
        CanonicalResult,
        ExecutorCapabilities,
    )
    schema_fields = {field.name for model in canonical_types for field in fields(model)}
    forbidden_new_fields = {"execution_ref", "remote_job_id", "provider_job_id", "session_identity"}
    assert schema_fields.isdisjoint(forbidden_new_fields)
    assert not hasattr(ExecutionPackage, "ExecutionRequest")
    assert not hasattr(CanonicalResult, "ExecutionResult")
