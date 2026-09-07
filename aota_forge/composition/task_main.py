"""Narrow M3/W1 task-main composition factory.

Opens the bounded file-backed coordinator store and activates the Milestone
coordinator over an already-wired M2 execution graph (dispatcher + durable
store, optional admission-gating completion coordinator). No CARD semantic
progression is wired here — that composition belongs to M3/W2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    TaskMainCoordinator,
    activate_milestone,
)
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
    TaskMainCoordinatorStore,
)


def open_task_main_coordinator_store(path: str | Path) -> FileBackedTaskMainCoordinatorStore:
    """Open the bounded single-host coordinator store (restart-test adapter)."""
    return FileBackedTaskMainCoordinatorStore(path)


def create_task_main_coordinator_handle(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_dispatcher: ExecutionDispatcher,
    plan_view: MilestonePlanView,
    origin_task_main_session_ref: str,
    executor_id: str,
    project_id: str,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    coordinator_id: str | None = None,
    **kwargs: Any,
) -> TaskMainCoordinator:
    """Activate (or idempotently re-attach to) the durable Milestone coordinator."""
    if kwargs:
        raise TypeError(f"unknown composition options: {sorted(kwargs)}")
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    return activate_milestone(
        store=store,
        plan_view=plan_view,
        origin_task_main_session_ref=origin_task_main_session_ref,
        execution_dispatcher=execution_dispatcher,
        completion_coordinator=completion_coordinator,
        executor_id=executor_id,
        project_id=project_id,
        coordinator_id=coordinator_id,
    )


__all__ = [
    "create_task_main_coordinator_handle",
    "open_task_main_coordinator_store",
]
