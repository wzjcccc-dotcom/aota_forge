"""Focused acceptance tests for M5-R-R2B Reference adapter replay safety."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aota_forge.adapters.execution.reference import (
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    REFERENCE_EXECUTOR_TEST_ONLY,
    ReferenceFakeExecutorAdapter,
)
from aota_forge.core.execution import (
    CANCEL_UNSUPPORTED,
    CANONICAL_ROLES,
    RESUME_UNSUPPORTED,
    TASK_NOT_FOUND,
    CanonicalTaskState,
    ExecutionPackage,
    ExecutorCapabilities,
)


def make_package(
    task_id: str = "r2b-task",
    *,
    instruction: str = "execute R2B replay test",
    operation: str = "task_dispatch",
    idempotency_key: str = "r2b-dispatch-key",
    package_id: str = "r2b-package",
    correlation_id: str = "r2b-correlation",
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=instruction,
        operation=operation,
        idempotency_key=idempotency_key,
        package_id=package_id,
        correlation_id=correlation_id,
    )


def make_capabilities(
    *,
    supports_task_cancellation: bool = True,
    supports_task_resume: bool = True,
) -> ExecutorCapabilities:
    return ExecutorCapabilities(
        executor_id="r2b-limited-fake",
        adapter_kind="in_process_test_double",
        supported_execution_modes=("sync",),
        supports_streaming_events=True,
        supports_task_cancellation=supports_task_cancellation,
        supports_task_resume=supports_task_resume,
        supports_structured_result=True,
        supported_canonical_roles=CANONICAL_ROLES,
        supported_isolation_modes=("process",),
        supports_working_directory=True,
        supports_artifact_transport=True,
    )


def test_cancel_replay_is_equivalent_and_has_one_effect() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    dispatch = adapter.dispatch(make_package())
    adapter.simulate_running(dispatch.canonical_task_id)

    first = adapter.cancel(dispatch.canonical_task_id, dispatch.adapter_handle)
    replay = adapter.cancel(dispatch.canonical_task_id, dispatch.adapter_handle)

    assert replay == first
    assert first.cancelled is True
    assert first.state == CanonicalTaskState.CANCELLED
    assert adapter.status(dispatch.canonical_task_id, dispatch.adapter_handle).state == CanonicalTaskState.CANCELLED
    assert adapter.cancel_count == 1


def test_preexisting_cancelled_state_is_not_a_cancel_replay() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    dispatch = adapter.dispatch(make_package(task_id="r2b-preexisting-cancel"))
    adapter.simulate_running(dispatch.canonical_task_id)
    adapter.simulate_transition(dispatch.canonical_task_id, CanonicalTaskState.CANCELLED)

    with pytest.raises(ValueError, match="TASK_ALREADY_TERMINAL"):
        adapter.cancel(dispatch.canonical_task_id, dispatch.adapter_handle)

    assert adapter.cancel_count == 0


def test_unsupported_cancel_fails_typed_without_effect() -> None:
    adapter = ReferenceFakeExecutorAdapter(
        capabilities=make_capabilities(supports_task_cancellation=False)
    )
    dispatch = adapter.dispatch(make_package(task_id="r2b-unsupported-cancel"))

    with pytest.raises(ValueError, match=CANCEL_UNSUPPORTED):
        adapter.cancel(dispatch.canonical_task_id, dispatch.adapter_handle)

    assert adapter.cancel_count == 0


def test_resume_replay_is_equivalent_and_has_one_effect() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    dispatch = adapter.dispatch(make_package(task_id="r2b-resume"))
    adapter.simulate_waiting(dispatch.canonical_task_id)
    first_package = make_package(
        task_id=dispatch.canonical_task_id,
        instruction="resume with approved input",
        operation="task_resume",
        idempotency_key="r2b-resume-key",
        package_id="r2b-resume-package",
    )
    replay_package = replace(
        first_package,
        package_id="r2b-resume-replay-package",
        correlation_id="r2b-resume-replay-correlation",
    )

    first = adapter.resume(
        dispatch.canonical_task_id, dispatch.adapter_handle, first_package
    )
    replay = adapter.resume(
        dispatch.canonical_task_id, dispatch.adapter_handle, replay_package
    )

    assert replay == first
    assert first.state == CanonicalTaskState.RUNNING
    assert adapter.status(dispatch.canonical_task_id, dispatch.adapter_handle).state == CanonicalTaskState.RUNNING
    assert adapter.get_task_record(dispatch.canonical_task_id).resume_history == [first_package]
    assert adapter.resume_count == 1


def test_resume_changed_intent_is_not_an_automatic_success() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    dispatch = adapter.dispatch(make_package(task_id="r2b-resume-conflict"))
    adapter.simulate_waiting(dispatch.canonical_task_id)
    first_package = make_package(
        task_id=dispatch.canonical_task_id,
        instruction="resume original input",
        operation="task_resume",
        idempotency_key="r2b-resume-conflict-key",
    )
    adapter.resume(dispatch.canonical_task_id, dispatch.adapter_handle, first_package)
    conflicting_package = make_package(
        task_id=dispatch.canonical_task_id,
        instruction="resume changed input",
        operation="task_resume",
        idempotency_key=first_package.idempotency_key,
    )

    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        adapter.resume(
            dispatch.canonical_task_id,
            dispatch.adapter_handle,
            conflicting_package,
        )

    assert adapter.resume_count == 1


def test_unsupported_resume_fails_typed_without_effect() -> None:
    adapter = ReferenceFakeExecutorAdapter(
        capabilities=make_capabilities(supports_task_resume=False)
    )
    dispatch = adapter.dispatch(make_package(task_id="r2b-unsupported-resume"))
    adapter.simulate_waiting(dispatch.canonical_task_id)
    resume_package = make_package(
        task_id=dispatch.canonical_task_id,
        operation="task_resume",
        idempotency_key="r2b-unsupported-resume-key",
    )

    with pytest.raises(ValueError, match=RESUME_UNSUPPORTED):
        adapter.resume(
            dispatch.canonical_task_id, dispatch.adapter_handle, resume_package
        )

    assert adapter.resume_count == 0


def test_wrong_resume_task_identity_fails_closed() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    dispatch = adapter.dispatch(make_package(task_id="r2b-identity"))
    adapter.simulate_waiting(dispatch.canonical_task_id)
    wrong_task_package = make_package(
        task_id="r2b-other-task",
        operation="task_resume",
        idempotency_key="r2b-wrong-task-key",
    )

    with pytest.raises(ValueError, match="TASK_ID_MISMATCH"):
        adapter.resume(
            dispatch.canonical_task_id,
            dispatch.adapter_handle,
            wrong_task_package,
        )

    assert adapter.resume_count == 0


def test_unknown_task_fails_closed_for_cancel_and_resume() -> None:
    adapter = ReferenceFakeExecutorAdapter()
    resume_package = make_package(
        task_id="r2b-unknown",
        operation="task_resume",
        idempotency_key="r2b-unknown-resume-key",
    )

    with pytest.raises(KeyError, match=TASK_NOT_FOUND):
        adapter.cancel("r2b-unknown", "r2b-unknown-handle")
    with pytest.raises(KeyError, match=TASK_NOT_FOUND):
        adapter.resume("r2b-unknown", "r2b-unknown-handle", resume_package)

    assert adapter.cancel_count == 0
    assert adapter.resume_count == 0


def test_reference_adapter_is_not_production_default() -> None:
    assert REFERENCE_EXECUTOR_TEST_ONLY is True
    assert REFERENCE_EXECUTOR_PRODUCTION_DEFAULT is False
