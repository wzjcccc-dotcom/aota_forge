"""S2/M1/W3 proof of the process-local route and idempotency boundary.

These tests intentionally exercise two fresh Core dispatcher instances rather
than simulating an operating-system restart.  They prove that a new
ExecutionDispatcher has no route, adapter binding, dispatch-result cache, or
idempotency knowledge from the old instance.  They do not claim worker death
or recovery outside the Forge dispatcher process.
"""

from __future__ import annotations

import pytest

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
)
from aota_forge.core.execution.dispatcher import (
    ExecutionDispatcher,
    TaskNotFoundError,
)
from aota_forge.core.execution.registry import ExecutorRegistry


class NeutralDurabilityAdapter(ExecutorAdapter):
    """Bounded, persistence-free adapter used only to observe Core calls."""

    executor_id = "neutral-durability"

    def __init__(self) -> None:
        self._capabilities = ExecutorCapabilities(
            executor_id=self.executor_id,
            adapter_kind="neutral_durability_test_double",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("none",),
            supports_working_directory=False,
            supports_artifact_transport=False,
        )
        self.state = CanonicalTaskState.RUNNING
        self.dispatch_count = 0
        self.status_count = 0
        self.result_count = 0
        self.cancel_count = 0
        self.resume_count = 0

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.dispatch_count += 1
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=f"durability-handle-{self.dispatch_count}",
            initial_state=self.state,
            dispatch_time="2026-08-28T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self.status_count += 1
        return TaskStatusResult(canonical_task_id=canonical_task_id, state=self.state)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self.result_count += 1
        if self.state == CanonicalTaskState.CANCELLED:
            return CanonicalResult.cancelled(
                canonical_task_id=canonical_task_id,
                executor_id=self.executor_id,
                correlation_id=f"corr-{canonical_task_id}",
            )
        if self.state == CanonicalTaskState.COMPLETED:
            return CanonicalResult.success(
                canonical_task_id=canonical_task_id,
                executor_id=self.executor_id,
                correlation_id=f"corr-{canonical_task_id}",
            )
        return CanonicalResult.unknown(
            canonical_task_id=canonical_task_id,
            executor_id=self.executor_id,
            correlation_id=f"corr-{canonical_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self.cancel_count += 1
        self.state = CanonicalTaskState.CANCELLED
        return CancelResult(
            canonical_task_id=canonical_task_id,
            cancelled=True,
            state=self.state,
        )

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        self.resume_count += 1
        self.state = CanonicalTaskState.RUNNING
        return ResumeResult(canonical_task_id=canonical_task_id, state=self.state)


def make_dispatcher(
    adapter: NeutralDurabilityAdapter,
) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry)


def make_package(
    task_id: str,
    *,
    instruction: str = "execute durability probe",
    operation: str = "task_dispatch",
    idempotency_key: str | None = None,
) -> ExecutionPackage:
    return ExecutionPackage.create(
        package_id=f"package-{task_id}-{instruction}-{operation}",
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation=operation,
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=idempotency_key or f"idempotency-{task_id}",
        correlation_id=f"correlation-{task_id}-{operation}",
    )


def test_fresh_dispatcher_has_no_prior_route_binding_state() -> None:
    adapter_a = NeutralDurabilityAdapter()
    dispatcher_a = make_dispatcher(adapter_a)
    package = make_package("task-route-local")

    dispatcher_a.dispatch(package, target_executor_id=adapter_a.executor_id)
    assert dispatcher_a.has_route(package.canonical_task_id) is True
    assert dispatcher_a.get_route(package.canonical_task_id)._adapter is adapter_a

    # An equivalent registry still creates a distinct Core process-local state.
    adapter_b = NeutralDurabilityAdapter()
    dispatcher_b = make_dispatcher(adapter_b)
    assert dispatcher_b.has_route(package.canonical_task_id) is False
    assert dispatcher_b.list_routes() == []
    assert dispatcher_b._routes == {}
    assert dispatcher_b._idempotency_index == {}
    assert dispatcher_b._dispatch_results == {}

    with pytest.raises(TaskNotFoundError):
        dispatcher_b.get_route(package.canonical_task_id)

    # A route mutation in A remains invisible to the new dispatcher.
    adapter_a.state = CanonicalTaskState.WAITING
    assert dispatcher_a.status(package.canonical_task_id).state == CanonicalTaskState.WAITING
    assert dispatcher_a.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.WAITING
    assert dispatcher_b.has_route(package.canonical_task_id) is False


def test_fresh_dispatcher_lifecycle_operations_fail_closed_without_broadcast() -> None:
    adapter_a = NeutralDurabilityAdapter()
    dispatcher_a = make_dispatcher(adapter_a)
    task_id = "task-lifecycle-local"
    dispatcher_a.dispatch(make_package(task_id), target_executor_id=adapter_a.executor_id)

    adapter_b = NeutralDurabilityAdapter()
    dispatcher_b = make_dispatcher(adapter_b)
    resume_package = make_package(
        task_id,
        instruction="resume durability probe",
        operation="task_resume",
    )

    with pytest.raises(TaskNotFoundError):
        dispatcher_b.status(task_id)
    with pytest.raises(TaskNotFoundError):
        dispatcher_b.result(task_id)
    with pytest.raises(TaskNotFoundError):
        dispatcher_b.cancel(task_id, executor=adapter_b.executor_id)
    with pytest.raises(TaskNotFoundError):
        dispatcher_b.resume(task_id, resume_package)

    assert adapter_b.status_count == 0
    assert adapter_b.result_count == 0
    assert adapter_b.cancel_count == 0
    assert adapter_b.resume_count == 0
    assert adapter_b.dispatch_count == 0

    # B's failed lookups do not disturb A's still-live route and binding.
    adapter_a.state = CanonicalTaskState.WAITING
    assert dispatcher_a.status(task_id).state == CanonicalTaskState.WAITING
    assert dispatcher_a.resume(task_id, resume_package).state == CanonicalTaskState.RUNNING
    assert dispatcher_a.cancel(task_id).state == CanonicalTaskState.CANCELLED
    assert dispatcher_a.result(task_id).canonical_task_state == CanonicalTaskState.CANCELLED.value
    assert adapter_a.status_count == 1
    assert adapter_a.resume_count == 1
    assert adapter_a.cancel_count == 1
    assert adapter_a.result_count == 1


def test_idempotency_index_is_dispatcher_local_and_not_cross_process_guaranteed() -> None:
    adapter_a = NeutralDurabilityAdapter()
    dispatcher_a = make_dispatcher(adapter_a)
    package = make_package("task-idempotency-local", idempotency_key="shared-idempotency-key")

    first = dispatcher_a.dispatch(package, target_executor_id=adapter_a.executor_id)
    replay = dispatcher_a.dispatch(package, target_executor_id=adapter_a.executor_id)
    assert replay == first
    assert adapter_a.dispatch_count == 1

    # B has no knowledge of A's idempotency index and must not infer A's route.
    adapter_b = NeutralDurabilityAdapter()
    dispatcher_b = make_dispatcher(adapter_b)
    assert package.idempotency_key not in dispatcher_b._idempotency_index
    assert dispatcher_b.has_route(package.canonical_task_id) is False

    # This is an isolated neutral probe of the D0 boundary, not an execution
    # recommendation: a fresh Core can dispatch the same package again.
    second = dispatcher_b.dispatch(package, target_executor_id=adapter_b.executor_id)
    assert second.canonical_task_id == package.canonical_task_id
    assert adapter_b.dispatch_count == 1
    assert dispatcher_b.get_route(package.canonical_task_id)._adapter is adapter_b
