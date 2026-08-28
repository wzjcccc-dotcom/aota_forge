"""S2/M1/W2 proof of Core-owned resume authority guards."""

from __future__ import annotations

import pytest

from aota_forge.core.execution import (
    CanonicalTaskState,
    DispatchResult,
    ExecutionPackage,
    ExecutorAdapter,
    ExecutorCapabilities,
    ResumeResult,
    ValidationResult,
)
from aota_forge.core.execution.dispatcher import (
    DispatcherError,
    ExecutionDispatcher,
    PackageInvalidError,
    TaskNotFoundError,
)
from aota_forge.core.execution.registry import ExecutorRegistry


class NeutralPermissiveAdapter(ExecutorAdapter):
    """Test-only adapter with no task-identity guard of its own."""

    executor_id = "neutral-permissive-resume"

    def __init__(self, initial_state: CanonicalTaskState = CanonicalTaskState.WAITING) -> None:
        self._capabilities = ExecutorCapabilities(
            executor_id=self.executor_id,
            adapter_kind="neutral_permissive_test_double",
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
        self.initial_state = initial_state
        self.resume_call_count = 0
        self.resume_calls: list[tuple[str, str, str]] = []
        self.response_task_id: str | None = None
        self.resume_state = CanonicalTaskState.RUNNING

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle="neutral-resume-handle",
            initial_state=self.initial_state,
            dispatch_time="2026-08-28T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str):  # pragma: no cover
        raise NotImplementedError

    def result(self, canonical_task_id: str, adapter_handle: str):  # pragma: no cover
        raise NotImplementedError

    def cancel(self, canonical_task_id: str, adapter_handle: str):  # pragma: no cover
        raise NotImplementedError

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        # Deliberately records and accepts any package identity; Core must guard it.
        self.resume_call_count += 1
        self.resume_calls.append(
            (canonical_task_id, adapter_handle, resume_package.canonical_task_id)
        )
        return ResumeResult(
            canonical_task_id=self.response_task_id or canonical_task_id,
            state=self.resume_state,
        )


def make_dispatcher(
    adapter: NeutralPermissiveAdapter | None = None,
) -> tuple[ExecutionDispatcher, NeutralPermissiveAdapter]:
    adapter = adapter or NeutralPermissiveAdapter()
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry), adapter


def make_package(
    task_id: str,
    *,
    operation: str = "task_dispatch",
    instruction: str = "resume input",
) -> ExecutionPackage:
    return ExecutionPackage.create(
        package_id=f"package-{task_id}-{operation}-{instruction}",
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation=operation,
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=f"idempotency-{task_id}-{operation}-{instruction}",
        correlation_id=f"correlation-{task_id}-{operation}-{instruction}",
    )


def dispatch_task(
    dispatcher: ExecutionDispatcher,
    task_id: str = "task-a",
) -> None:
    dispatcher.dispatch(make_package(task_id), target_executor_id=NeutralPermissiveAdapter.executor_id)


def test_cross_task_resume_is_rejected_by_core_before_permissive_adapter() -> None:
    dispatcher, adapter = make_dispatcher()
    dispatch_task(dispatcher, "task-a")
    before = dispatcher.get_route("task-a")

    with pytest.raises(PackageInvalidError, match="canonical_task_id"):
        dispatcher.resume("task-a", make_package("task-b", operation="task_resume"))

    after = dispatcher.get_route("task-a")
    assert adapter.resume_call_count == 0
    assert after.to_dict() == before.to_dict()
    assert after.adapter_handle == before.adapter_handle
    assert after.dispatch_attempt_id == before.dispatch_attempt_id


def test_dispatch_operation_is_rejected_at_core_resume_boundary() -> None:
    dispatcher, adapter = make_dispatcher()
    dispatch_task(dispatcher, "task-operation")
    before = dispatcher.get_route("task-operation")

    with pytest.raises(PackageInvalidError, match="task_resume"):
        dispatcher.resume("task-operation", make_package("task-operation"))

    assert adapter.resume_call_count == 0
    assert dispatcher.get_route("task-operation").to_dict() == before.to_dict()


@pytest.mark.parametrize(
    "terminal_state",
    [
        CanonicalTaskState.COMPLETED,
        CanonicalTaskState.FAILED,
        CanonicalTaskState.CANCELLED,
    ],
)
def test_known_terminal_state_is_rejected_before_adapter_resume(
    terminal_state: CanonicalTaskState,
) -> None:
    dispatcher, adapter = make_dispatcher(NeutralPermissiveAdapter(initial_state=terminal_state))
    dispatch_task(dispatcher, f"task-{terminal_state.value.lower()}")
    task_id = f"task-{terminal_state.value.lower()}"
    before = dispatcher.get_route(task_id)

    with pytest.raises(DispatcherError, match="TASK_ALREADY_TERMINAL"):
        dispatcher.resume(task_id, make_package(task_id, operation="task_resume"))

    assert adapter.resume_call_count == 0
    assert dispatcher.get_route(task_id).to_dict() == before.to_dict()
    assert dispatcher.get_route(task_id).last_known_state == terminal_state


def test_missing_route_fails_closed_without_adapter_call() -> None:
    dispatcher, adapter = make_dispatcher()

    with pytest.raises(TaskNotFoundError):
        dispatcher.resume("missing-task", make_package("missing-task", operation="task_resume"))

    assert adapter.resume_call_count == 0


def test_valid_resume_preserves_route_identity_and_uses_existing_route() -> None:
    dispatcher, adapter = make_dispatcher()
    task_id = "task-valid-resume"
    dispatch_task(dispatcher, task_id)
    before = dispatcher.get_route(task_id)

    result = dispatcher.resume(task_id, make_package(task_id, operation="task_resume"))

    after = dispatcher.get_route(task_id)
    assert result.canonical_task_id == task_id
    assert result.state == CanonicalTaskState.RUNNING
    assert adapter.resume_call_count == 1
    assert adapter.resume_calls == [(task_id, before.adapter_handle, task_id)]
    assert len(dispatcher.list_routes()) == 1
    assert after.canonical_task_id == before.canonical_task_id
    assert after.adapter_handle == before.adapter_handle
    assert after.dispatch_attempt_id == before.dispatch_attempt_id
    assert after.package_id == before.package_id
    assert after.last_known_state == CanonicalTaskState.RUNNING


def test_resume_response_identity_mismatch_preserves_route_state() -> None:
    dispatcher, adapter = make_dispatcher()
    task_id = "task-response-mismatch"
    dispatch_task(dispatcher, task_id)
    before = dispatcher.get_route(task_id)
    adapter.response_task_id = "task-b"

    with pytest.raises(DispatcherError, match="canonical_task_id"):
        dispatcher.resume(task_id, make_package(task_id, operation="task_resume"))

    assert adapter.resume_call_count == 1
    assert dispatcher.get_route(task_id).to_dict() == before.to_dict()
