"""Offline acceptance tests for the M5-R-R2A Hermes repair."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import pytest

from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    HermesAdapterError,
    HermesDispatchRejectedError,
    default_hermes_capabilities,
)
from aota_forge.core.execution import (
    CancelResult,
    CanonicalTaskState,
    ExecutionPackage,
    ResumeResult,
)


class CountingHermesHost:
    """Offline host double that records physical mutation calls."""

    def __init__(self) -> None:
        self.dispatches: list[dict[str, Any]] = []
        self.cancels: list[str] = []
        self.resumes: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatches.append(dict(payload))
        handle = f"hermes-r2a-{len(self.dispatches)}"
        return {
            "adapter_handle": handle,
            "status": "pending",
            "dispatch_time": "2026-08-22T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "pending"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "running"}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.cancels.append(adapter_handle)
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.resumes.append((adapter_handle, dict(payload)))
        return {"status": "running"}


def make_package(
    task_id: str = "r2a-task",
    instruction: str = "execute R2A test task",
    *,
    operation: str = "task_dispatch",
    idempotency_key: str = "r2a-idempotency",
    package_id: str = "r2a-package",
    correlation_id: str = "r2a-correlation",
    capability_requirements: dict[str, Any] | None = None,
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
        capability_requirements=capability_requirements,
    )


def test_dispatch_identical_replay_has_one_physical_host_call() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    first_package = make_package()
    replay_package = make_package(package_id="r2a-replay-package", correlation_id="r2a-replay-correlation")

    first = adapter.dispatch(first_package)
    replay = adapter.dispatch(replay_package)

    assert isinstance(first, type(replay))
    assert replay == first
    assert len(host.dispatches) == 1


def test_dispatch_conflicting_idempotency_reuse_fails_closed() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    adapter.dispatch(make_package())

    conflicting = make_package(instruction="different canonical intent")
    with pytest.raises(HermesAdapterError, match="IDEMPOTENCY_CONFLICT") as exc_info:
        adapter.dispatch(conflicting)

    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(host.dispatches) == 1


def test_cancel_identical_replay_has_one_physical_host_call() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    dispatch = adapter.dispatch(make_package())

    first = adapter.cancel("r2a-task", dispatch.adapter_handle)
    replay = adapter.cancel("r2a-task", dispatch.adapter_handle)

    assert isinstance(first, CancelResult)
    assert replay == first
    assert len(host.cancels) == 1


def test_resume_identical_replay_has_one_physical_host_call() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    dispatch = adapter.dispatch(make_package())
    first_package = make_package(
        operation="task_resume",
        idempotency_key="r2a-resume-idempotency",
        package_id="r2a-resume-package",
    )
    replay_package = make_package(
        operation="task_resume",
        idempotency_key="r2a-resume-idempotency",
        package_id="r2a-resume-replay-package",
        correlation_id="r2a-resume-replay-correlation",
    )

    first = adapter.resume("r2a-task", dispatch.adapter_handle, first_package)
    replay = adapter.resume("r2a-task", dispatch.adapter_handle, replay_package)

    assert isinstance(first, ResumeResult)
    assert replay == first
    assert len(host.resumes) == 1


def test_cross_task_resume_is_rejected_before_host_call() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    dispatch = adapter.dispatch(make_package())
    wrong_task_package = make_package(
        task_id="r2a-other-task",
        operation="task_resume",
        idempotency_key="r2a-wrong-task-resume",
    )

    with pytest.raises(HermesAdapterError, match="TASK_ID_MISMATCH"):
        adapter.resume("r2a-task", dispatch.adapter_handle, wrong_task_package)

    assert host.resumes == []


def test_unknown_capability_requirement_is_package_invalid() -> None:
    adapter = HermesAdapter(host_client=CountingHermesHost())
    package = make_package(capability_requirements={"requires_gpu": True})

    validation = adapter.validate_package(package)

    assert validation.valid is False
    assert any("PACKAGE_INVALID" in error for error in validation.errors)
    with pytest.raises(HermesDispatchRejectedError):
        adapter.dispatch(package)


def test_unsupported_known_capability_requirement_is_capability_mismatch() -> None:
    adapter = HermesAdapter(host_client=CountingHermesHost())
    streaming_package = make_package(capability_requirements={"requires_streaming_events": True})
    structured_adapter = HermesAdapter(
        host_client=CountingHermesHost(),
        capabilities=replace(default_hermes_capabilities(), supports_structured_result=False),
    )
    structured_package = make_package(
        capability_requirements={"requires_structured_result": True},
        idempotency_key="r2a-structured",
    )

    streaming_validation = adapter.validate_package(streaming_package)
    structured_validation = structured_adapter.validate_package(structured_package)

    assert streaming_validation.valid is False
    assert any("CAPABILITY_MISMATCH" in error for error in streaming_validation.errors)
    assert structured_validation.valid is False
    assert any("CAPABILITY_MISMATCH" in error for error in structured_validation.errors)


def test_valid_supported_capability_requirements_are_accepted() -> None:
    adapter = HermesAdapter(host_client=CountingHermesHost())
    package = make_package(
        capability_requirements={
            "execution_mode": "async",
            "isolation": "worktree",
            "requires_cancellation": True,
            "requires_resume": True,
            "requires_structured_result": True,
            "requires_streaming_events": False,
            "requires_working_directory": True,
            "requires_artifact_transport": True,
        }
    )

    validation = adapter.validate_package(package)

    assert validation.valid is True
    assert validation.errors == ()


def test_unsupported_cancel_is_typed_failure() -> None:
    host = CountingHermesHost()
    capabilities = replace(default_hermes_capabilities(), supports_task_cancellation=False)
    adapter = HermesAdapter(host_client=host, capabilities=capabilities)
    dispatch = adapter.dispatch(make_package())

    result = adapter.cancel("r2a-task", dispatch.adapter_handle)

    assert isinstance(result, CancelResult)
    assert result.cancelled is False
    assert result.state == CanonicalTaskState.UNKNOWN
    assert host.cancels == []


def test_unsupported_resume_is_typed_failure() -> None:
    host = CountingHermesHost()
    capabilities = replace(default_hermes_capabilities(), supports_task_resume=False)
    adapter = HermesAdapter(host_client=host, capabilities=capabilities)
    dispatch = adapter.dispatch(make_package())
    resume_package = make_package(
        operation="task_resume",
        idempotency_key="r2a-unsupported-resume",
    )

    with pytest.raises(HermesAdapterError) as exc_info:
        adapter.resume("r2a-task", dispatch.adapter_handle, resume_package)

    assert exc_info.value.code == "RESUME_UNSUPPORTED"
    assert host.resumes == []
