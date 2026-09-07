"""Narrow M3/W1+W2+W3 task-main composition factory.

W1: opens the bounded file-backed coordinator store and activates the
Milestone coordinator over an already-wired M2 execution graph (dispatcher +
durable store, optional admission-gating completion coordinator).
W2: CARD-first governed semantic reconciliation service over the same stores:
exact completion identity in, deterministic reconciliation + durable receipt
out, ACK eligibility only after persistence.
W3: bounded autonomous runner (ONE iteration over W1/W2/M2) and the
task-main-only typed control service (no generic workflow engine, no raw shell,
no per-role MCP).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    TaskMainCoordinator,
    activate_milestone,
)
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
    TaskMainCoordinatorStore,
)
from aota_forge.runtime.task_main.reconciliation import (
    GovernedReviewEvidence,
    GovernedWorkItemEvidence,
    ReconciliationOutcome,
    reconcile_review_completion,
    reconcile_worker_completion,
)
from aota_forge.runtime.task_main.runner import (
    TaskMainMilestoneRunner,
    advance_milestone_once,
)
from aota_forge.work_plane.handoff import TaskHandoff


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


def reconcile_task_main_completion(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    canonical_task_id: str,
    card_digest: str,
    live_plan_view: MilestonePlanView,
    governed_evidence: GovernedWorkItemEvidence,
    handoff_resolver: Callable[[str], TaskHandoff] | None = None,
) -> ReconciliationOutcome:
    """CARD-first governed semantic reconciliation over already-wired stores.

    Internal typed task-main service (no MCP exposure in W2, inaccessible
    to ``aota-worker``): the exact task-main session supplies the completion
    identity plus trusted governed evidence; the service hydrates everything
    else from durable truth and returns the reconciliation outcome with ACK
    eligibility bound to the persisted receipt.
    """
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    return reconcile_worker_completion(
        store=store,
        execution_store=execution_store,
        coordinator_id=coordinator_id,
        canonical_task_id=canonical_task_id,
        card_digest=card_digest,
        live_plan_view=live_plan_view,
        governed_evidence=governed_evidence,
        handoff_resolver=handoff_resolver,
    )


def reconcile_task_main_review(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    reviewer_canonical_task_id: str,
    card_digest: str,
    live_plan_view: MilestonePlanView,
    governed_review: GovernedReviewEvidence,
) -> ReconciliationOutcome:
    """Milestone-level reviewer completion reconciliation (derivation only)."""
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    return reconcile_review_completion(
        store=store,
        execution_store=execution_store,
        coordinator_id=coordinator_id,
        reviewer_canonical_task_id=reviewer_canonical_task_id,
        card_digest=card_digest,
        live_plan_view=live_plan_view,
        governed_review=governed_review,
    )


def create_task_main_runner(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_store: ExecutionStateStore,
    execution_dispatcher: ExecutionDispatcher,
    live_plan_view: MilestonePlanView,
    handoff_resolver: Callable[[str], TaskHandoff],
    governed_evidence_resolver: Any | None = None,
    reviewer_handoff_resolver: Any | None = None,
    governed_review_resolver: Any | None = None,
    next_milestone_view: MilestonePlanView | None = None,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    coordinator_id: str | None = None,
) -> TaskMainMilestoneRunner:
    """Bounded autonomous runner over already-wired M2/W1/W2 stores (ONE iteration)."""
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    return TaskMainMilestoneRunner(
        coordinator_store=store,
        execution_store=execution_store,
        execution_dispatcher=execution_dispatcher,
        live_plan_view=live_plan_view,
        handoff_resolver=handoff_resolver,
        governed_evidence_resolver=governed_evidence_resolver,
        reviewer_handoff_resolver=reviewer_handoff_resolver,
        governed_review_resolver=governed_review_resolver,
        next_milestone_view=next_milestone_view,
        completion_coordinator=completion_coordinator,
        coordinator_id=coordinator_id,
    )


def create_task_main_control_service(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_store: ExecutionStateStore,
    execution_dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None = None,
) -> TaskMainControlService:
    """Typed task-main-only control service (``aota-task-main`` only)."""
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    return TaskMainControlService(
        coordinator_store=store,
        execution_store=execution_store,
        execution_dispatcher=execution_dispatcher,
        completion_coordinator=completion_coordinator,
    )


__all__ = [
    "advance_milestone_once",
    "create_task_main_control_service",
    "create_task_main_coordinator_handle",
    "create_task_main_runner",
    "open_task_main_coordinator_store",
    "reconcile_task_main_completion",
    "reconcile_task_main_review",
]
