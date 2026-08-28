"""Focused S3/M1/W2 tests for Hermes task/adapter-handle identity safety."""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from aota_forge.adapters.hermes.executor import HermesAdapter, HermesAdapterError
from aota_forge.core.execution import ExecutionPackage


class CountingHermesHost:
    def __init__(self) -> None:
        self.calls = {"status": 0, "result": 0, "cancel": 0, "resume": 0}
        self._next_handle = 1

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        handle = f"s3-w2-handle-{self._next_handle}"
        self._next_handle += 1
        return {"adapter_handle": handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["status"] += 1
        return {"status": "running"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["result"] += 1
        return {"status": "done", "result_data": {"ok": True}}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["cancel"] += 1
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls["resume"] += 1
        return {"status": "running"}


def package(
    task_id: str,
    *,
    operation: str = "task_dispatch",
    idempotency_key: str | None = None,
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction=f"S3 W2 {task_id}",
        operation=operation,
        idempotency_key=idempotency_key,
    )


@pytest.fixture
def dispatched() -> tuple[HermesAdapter, CountingHermesHost, str, str, str, str]:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    first = adapter.dispatch(package("task-a"))
    second = adapter.dispatch(package("task-b"))
    return (
        adapter,
        host,
        first.canonical_task_id,
        first.adapter_handle,
        second.canonical_task_id,
        second.adapter_handle,
    )


def resume_package(task_id: str) -> ExecutionPackage:
    return package(task_id, operation="task_resume")


@pytest.mark.parametrize("operation", ["status", "result", "cancel"])
def test_exact_known_pair_is_allowed(
    dispatched: tuple[HermesAdapter, CountingHermesHost, str, str, str, str],
    operation: str,
) -> None:
    adapter, host, task_id, handle, *_ = dispatched

    result = getattr(adapter, operation)(task_id, handle)

    assert result.canonical_task_id == task_id
    assert host.calls[operation] == 1


def test_exact_known_pair_is_allowed_for_resume(
    dispatched: tuple[HermesAdapter, CountingHermesHost, str, str, str, str],
) -> None:
    adapter, host, task_id, handle, *_ = dispatched

    result = adapter.resume(task_id, handle, resume_package(task_id))

    assert result.canonical_task_id == task_id
    assert host.calls["resume"] == 1


@pytest.mark.parametrize("operation", ["status", "result", "cancel", "resume"])
@pytest.mark.parametrize(
    "pair",
    [
        "known_task_wrong_handle",
        "wrong_task_known_handle",
        "known_task_unknown_handle",
        "unknown_task_unknown_handle",
    ],
)
def test_rejected_identity_never_calls_host(
    dispatched: tuple[HermesAdapter, CountingHermesHost, str, str, str, str],
    operation: str,
    pair: str,
) -> None:
    adapter, host, task_a, handle_a, task_b, handle_b = dispatched
    pairs = {
        "known_task_wrong_handle": (task_a, handle_b),
        "wrong_task_known_handle": (task_b, handle_a),
        "known_task_unknown_handle": (task_a, "unknown-handle"),
        "unknown_task_unknown_handle": ("unknown-task", "unknown-handle"),
    }
    task_id, handle = pairs[pair]

    with pytest.raises(HermesAdapterError):
        if operation == "resume":
            adapter.resume(task_id, handle, resume_package(task_id))
        else:
            getattr(adapter, operation)(task_id, handle)

    assert host.calls[operation] == 0


@pytest.mark.parametrize("operation", ["status", "result", "cancel", "resume"])
def test_inconsistent_reverse_binding_fails_closed(
    dispatched: tuple[HermesAdapter, CountingHermesHost, str, str, str, str],
    operation: str,
) -> None:
    adapter, host, task_id, handle, *_ = dispatched
    adapter._task_handles[task_id] = "corrupt-reverse-handle"  # type: ignore[attr-defined]

    with pytest.raises(HermesAdapterError):
        if operation == "resume":
            adapter.resume(task_id, handle, resume_package(task_id))
        else:
            getattr(adapter, operation)(task_id, handle)

    assert host.calls[operation] == 0


def test_dispatch_replay_requires_intact_binding() -> None:
    host = CountingHermesHost()
    adapter = HermesAdapter(host_client=host)
    adapter.dispatch(package("replay-task", idempotency_key="replay-idempotency"))
    adapter._task_handles["replay-task"] = "corrupt-reverse-handle"  # type: ignore[attr-defined]

    with pytest.raises(HermesAdapterError):
        adapter.dispatch(package("replay-task", idempotency_key="replay-idempotency"))
