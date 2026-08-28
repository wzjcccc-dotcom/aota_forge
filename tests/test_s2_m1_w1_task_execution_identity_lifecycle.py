"""S2/M1/W1 proof of the Core task and execution lifecycle boundary."""

from __future__ import annotations

import pytest

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
from aota_forge.core.execution.dispatcher import (
    AdapterProtocolError,
    ExecutionDispatcher,
    IdempotencyConflictError,
)
from aota_forge.core.execution.registry import ExecutorRegistry


class NeutralAdversarialAdapter(ExecutorAdapter):
    """Test-only adapter whose identity and state responses are controllable."""

    executor_id = "neutral-adversarial"

    def __init__(self, initial_state: CanonicalTaskState = CanonicalTaskState.RUNNING) -> None:
        self._capabilities = ExecutorCapabilities(
            executor_id=self.executor_id,
            adapter_kind="neutral_adversarial_test_double",
            supported_execution_modes=("async", "sync"),
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
        self.dispatch_count = 0
        self.status_count = 0
        self.result_count = 0
        self.cancel_count = 0
        self.resume_count = 0
        self.dispatch_task_id: str | None = None
        self.status_task_id: str | None = None
        self.result_task_id: str | None = None
        self.result_executor_id: str | None = None
        self.cancel_task_id: str | None = None
        self.resume_task_id: str | None = None
        self.status_state = initial_state
        self.result_state = initial_state
        self.cancel_state = CanonicalTaskState.CANCELLED
        self.resume_state = CanonicalTaskState.RUNNING
        self.status_error: Exception | None = None

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.dispatch_count += 1
        return DispatchResult(
            canonical_task_id=self.dispatch_task_id or package.canonical_task_id,
            adapter_handle=f"neutral-handle-{self.dispatch_count}",
            initial_state=self.initial_state,
            dispatch_time="2026-08-28T00:00:00Z",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self.status_count += 1
        if self.status_error is not None:
            raise self.status_error
        return TaskStatusResult(
            canonical_task_id=self.status_task_id or canonical_task_id,
            state=self.status_state,
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self.result_count += 1
        result_task_id = self.result_task_id or canonical_task_id
        executor_id = self.result_executor_id or self.executor_id
        if self.result_state == CanonicalTaskState.COMPLETED:
            return CanonicalResult.success(
                canonical_task_id=result_task_id,
                executor_id=executor_id,
                correlation_id=f"corr-{result_task_id}",
            )
        return CanonicalResult.failure(
            canonical_task_id=result_task_id,
            executor_id=executor_id,
            error_code="TASK_STATE_UNKNOWN",
            error_message="test result state",
            retryable=True,
            status="unknown",
            canonical_task_state=self.result_state.value,
            correlation_id=f"corr-{result_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self.cancel_count += 1
        return CancelResult(
            canonical_task_id=self.cancel_task_id or canonical_task_id,
            cancelled=True,
            state=self.cancel_state,
        )

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        self.resume_count += 1
        return ResumeResult(
            canonical_task_id=self.resume_task_id or canonical_task_id,
            state=self.resume_state,
        )


def make_dispatcher(
    adapter: NeutralAdversarialAdapter | None = None,
) -> tuple[ExecutionDispatcher, NeutralAdversarialAdapter]:
    adapter = adapter or NeutralAdversarialAdapter()
    registry = ExecutorRegistry()
    registry.register(adapter)
    return ExecutionDispatcher(registry), adapter


def make_package(
    task_id: str,
    *,
    instruction: str = "execute task",
    operation: str = "task_dispatch",
    idempotency_key: str | None = None,
) -> ExecutionPackage:
    return ExecutionPackage.create(
        package_id=f"package-{task_id}-{instruction}",
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation=operation,
        capability_requirements={"execution_mode": "sync", "isolation_mode": "none"},
        idempotency_key=idempotency_key or f"idempotency-{task_id}",
        correlation_id=f"correlation-{task_id}",
    )


def test_identity_domains_are_core_owned_and_distinct() -> None:
    dispatcher, _adapter = make_dispatcher()
    package = make_package("task-identity")

    dispatch = dispatcher.dispatch(package, target_executor_id="neutral-adversarial")
    route = dispatcher.get_route(package.canonical_task_id)

    assert route.canonical_task_id == package.canonical_task_id
    assert route.dispatch_attempt_id != route.canonical_task_id
    assert route.dispatch_attempt_id != route.adapter_handle
    assert route.package_id == package.package_id
    assert route.correlation_id == package.correlation_id
    assert route.executor_id == "neutral-adversarial"
    assert dispatch.adapter_handle == route.adapter_handle


def test_idempotency_replay_conflict_cross_task_replay_and_duplicate_task() -> None:
    dispatcher, adapter = make_dispatcher()
    first = make_package("task-replay", idempotency_key="shared-key")
    replay = make_package("task-replay", idempotency_key="shared-key")
    cross_task_replay = make_package("task-other", idempotency_key="shared-key")

    first_result = dispatcher.dispatch(first, target_executor_id=adapter.executor_id)
    replay_result = dispatcher.dispatch(replay, target_executor_id=adapter.executor_id)
    assert replay_result == first_result
    assert adapter.dispatch_count == 1
    assert [route.canonical_task_id for route in dispatcher.list_routes()] == ["task-replay"]
    assert dispatcher.get_route("task-replay").dispatch_attempt_id

    assert dispatcher.dispatch(cross_task_replay, target_executor_id=adapter.executor_id) == first_result
    assert adapter.dispatch_count == 1
    assert not dispatcher.has_route("task-other")

    with pytest.raises(IdempotencyConflictError):
        dispatcher.dispatch(
            make_package("task-conflict", instruction="changed", idempotency_key="shared-key"),
            target_executor_id=adapter.executor_id,
        )
    assert adapter.dispatch_count == 1

    with pytest.raises(Exception, match="DUPLICATE_CANONICAL_TASK_ID"):
        dispatcher.dispatch(
            make_package("task-replay", idempotency_key="unused-key"),
            target_executor_id=adapter.executor_id,
        )
    assert adapter.dispatch_count == 1


def test_valid_resume_preserves_the_current_route_and_attempt() -> None:
    dispatcher, adapter = make_dispatcher(
        NeutralAdversarialAdapter(initial_state=CanonicalTaskState.WAITING)
    )
    package = make_package("task-resume")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)
    before = dispatcher.get_route(package.canonical_task_id)

    assert dispatcher.resume(
        package.canonical_task_id,
        make_package("task-resume", instruction="input", operation="task_resume"),
    ).state == CanonicalTaskState.RUNNING
    after = dispatcher.get_route(package.canonical_task_id)
    assert len(dispatcher.list_routes()) == 1
    assert after.canonical_task_id == before.canonical_task_id
    assert after.dispatch_attempt_id == before.dispatch_attempt_id
    assert after.adapter_handle == before.adapter_handle
    assert adapter.resume_count == 1


def test_route_snapshots_isolate_all_mutable_route_fields_and_binding() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-snapshot")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)
    original = dispatcher.get_route(package.canonical_task_id)

    original.canonical_task_id = "tampered-task"
    original.executor_id = "tampered-executor"
    original.adapter_handle = "tampered-handle"
    original.package_id = "tampered-package"
    original.correlation_id = "tampered-correlation"
    original.dispatch_attempt_id = "tampered-attempt"
    original.last_known_state = CanonicalTaskState.FAILED
    original._adapter = NeutralAdversarialAdapter()  # type: ignore[assignment]

    listed = dispatcher.list_routes()[0]
    listed.executor_id = "tampered-list-executor"
    listed.last_known_state = CanonicalTaskState.CANCELLED

    internal = dispatcher.get_route(package.canonical_task_id)
    assert internal.canonical_task_id == package.canonical_task_id
    assert internal.executor_id == adapter.executor_id
    assert internal.adapter_handle == "neutral-handle-1"
    assert internal.package_id == package.package_id
    assert internal.correlation_id == package.correlation_id
    assert internal.dispatch_attempt_id == dispatcher.get_route(package.canonical_task_id).dispatch_attempt_id
    assert internal.last_known_state == CanonicalTaskState.RUNNING
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.RUNNING
    assert adapter.status_count == 1


def test_dispatch_identity_mismatch_commits_no_route_or_indexes() -> None:
    adapter = NeutralAdversarialAdapter()
    adapter.dispatch_task_id = "wrong-task"
    dispatcher, adapter = make_dispatcher(adapter)
    package = make_package("task-dispatch-mismatch")

    with pytest.raises(AdapterProtocolError, match="canonical_task_id"):
        dispatcher.dispatch(package, target_executor_id=adapter.executor_id)

    assert not dispatcher.has_route(package.canonical_task_id)
    assert not dispatcher.has_route("wrong-task")
    assert package.idempotency_key not in dispatcher._idempotency_index
    assert package.canonical_task_id not in dispatcher._dispatch_results


def test_status_identity_mismatch_does_not_mutate_route_state() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-status-mismatch")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)
    adapter.status_task_id = "wrong-task"

    with pytest.raises(AdapterProtocolError):
        dispatcher.status(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.RUNNING


def test_result_identity_and_executor_mismatch_do_not_mutate_route_state() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-result-mismatch")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)

    adapter.result_task_id = "wrong-task"
    with pytest.raises(AdapterProtocolError):
        dispatcher.result(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.RUNNING

    adapter.result_task_id = None
    adapter.result_executor_id = "wrong-executor"
    with pytest.raises(AdapterProtocolError):
        dispatcher.result(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.RUNNING


def test_cancel_identity_mismatch_does_not_cache_successful_cancel() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-cancel-mismatch")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)
    adapter.cancel_task_id = "wrong-task"

    with pytest.raises(AdapterProtocolError):
        dispatcher.cancel(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.RUNNING
    assert not hasattr(dispatcher.get_route(package.canonical_task_id), "_successful_cancel_result")

    adapter.cancel_task_id = None
    assert dispatcher.cancel(package.canonical_task_id).state == CanonicalTaskState.CANCELLED
    assert dispatcher.cancel(package.canonical_task_id).state == CanonicalTaskState.CANCELLED
    assert adapter.cancel_count == 2


def test_resume_response_identity_mismatch_does_not_mutate_route_state() -> None:
    dispatcher, adapter = make_dispatcher(
        NeutralAdversarialAdapter(initial_state=CanonicalTaskState.WAITING)
    )
    package = make_package("task-resume-response")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)
    adapter.resume_task_id = "wrong-task"

    with pytest.raises(AdapterProtocolError):
        dispatcher.resume(
            package.canonical_task_id,
            make_package(
                "task-resume-response", instruction="input", operation="task_resume"
            ),
        )
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.WAITING


def test_terminal_state_is_sticky_without_enforcing_intermediate_transitions() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-terminal")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)

    adapter.status_state = CanonicalTaskState.COMPLETED
    assert dispatcher.status(package.canonical_task_id).state == CanonicalTaskState.COMPLETED

    for contradictory_state in (
        CanonicalTaskState.RUNNING,
        CanonicalTaskState.UNKNOWN,
        CanonicalTaskState.FAILED,
    ):
        adapter.status_state = contradictory_state
        with pytest.raises(AdapterProtocolError):
            dispatcher.status(package.canonical_task_id)
        assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.COMPLETED

    adapter.result_state = CanonicalTaskState.RUNNING
    with pytest.raises(AdapterProtocolError):
        dispatcher.result(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.COMPLETED


def test_reconcile_maps_uncertain_adapter_failure_to_unknown_but_not_protocol_mismatch() -> None:
    dispatcher, adapter = make_dispatcher()
    package = make_package("task-reconcile")
    dispatcher.dispatch(package, target_executor_id=adapter.executor_id)

    adapter.status_error = RuntimeError("transport disconnected")
    assert dispatcher.reconcile_status(package.canonical_task_id) == CanonicalTaskState.UNKNOWN

    adapter.status_error = None
    adapter.status_task_id = "wrong-task"
    with pytest.raises(AdapterProtocolError):
        dispatcher.reconcile_status(package.canonical_task_id)
    assert dispatcher.get_route(package.canonical_task_id).last_known_state == CanonicalTaskState.UNKNOWN
