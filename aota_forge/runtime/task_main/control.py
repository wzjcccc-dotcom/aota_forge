"""Typed task-main control transport (M3/W3).

One restricted task-main-only typed control surface over the W1/W2/RUNNER
seams. No generic CLI, no stringly aota_cli, no per-role MCP architecture,
no raw shell. The same bounded MCP server (or internal host integration) may
host both the shared worker ``workspace.*`` and these task-main controls, but
server-side profile/authority binding guarantees:

* ``aota-task-main`` → may access bounded task-main control
* ``aota-worker``     → cannot access task-main control (fail-closed)

Workers retain only ``workspace.search / read / write`` via the shared AOTA MCP.

The semantic coordinator modules (``coordinator.py`` / ``reconciliation.py`` /
``runner.py``) remain Hermes-free — they never import ``adapters.hermes``.
This transport is the only place that may reference Hermes profiles when
exposing an MCP/host boundary, and it contains zero planning semantic.
"""

from __future__ import annotations

from collections.abc import Callable

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
    recover_coordinator,
)
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import (
    GovernedReviewEvidence,
    GovernedWorkItemEvidence,
    ReconciliationOutcome,
    reconcile_review_completion,
    reconcile_worker_completion,
)
from aota_forge.runtime.task_main.runner import (
    RunnerOutcome,
    TaskMainMilestoneRunner,
    advance_milestone_once,
)
from aota_forge.work_plane.handoff import TaskHandoff

TASK_MAIN_PROFILE = "aota-task-main"
WORKER_PROFILE = "aota-worker"

TASK_MAIN_CONTROL_OPERATIONS: tuple[str, ...] = (
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
    "task_main.reconcile_worker_completion",
    "task_main.reconcile_review_completion",
    "task_main.observe_terminal_completions",
    "task_main.dispatch_ready",
)

WORKER_CAN_CALL_TASK_MAIN_CONTROL = False
TASK_MAIN_RAW_SHELL_REQUIRED = False
TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED = False
PER_ROLE_MCP_ARCHITECTURE_CREATED = False
GENERIC_WORKFLOW_ENGINE_CREATED = False
DYNAMIC_PROVIDER_ROUTING_CREATED = False


class TaskMainControlAuthorityError(PermissionError):
    """Caller lacks task-main profile authority for this typed control."""


def _require_task_main_profile(profile: str | None) -> None:
    if profile != TASK_MAIN_PROFILE:
        raise TaskMainControlAuthorityError(
            f"task-main control requires profile {TASK_MAIN_PROFILE!r}, got {profile!r}: "
            "aota-worker cannot access task-main control"
        )


class TaskMainControlService:
    """Bounded typed control service for the exact task-main session.

    Each method is a thin typed wrapper over the existing W1/W2/runner
    seams. The caller must supply the trusted runtime profile (server-side
    binding) on every call; no model-supplied profile is trusted.
    """

    def __init__(
        self,
        *,
        coordinator_store: TaskMainCoordinatorStore,
        execution_store: ExecutionStateStore,
        execution_dispatcher: ExecutionDispatcher,
        completion_coordinator: DurableCompletionCoordinator | None = None,
    ) -> None:
        if not isinstance(coordinator_store, TaskMainCoordinatorStore):
            raise TypeError("coordinator_store must be TaskMainCoordinatorStore")
        if not isinstance(execution_store, ExecutionStateStore):
            raise TypeError("execution_store must be ExecutionStateStore")
        if not isinstance(execution_dispatcher, ExecutionDispatcher):
            raise TypeError("execution_dispatcher must be ExecutionDispatcher")
        self._coord_store = coordinator_store
        self._exec_store = execution_store
        self._dispatcher = execution_dispatcher
        self._completion = completion_coordinator

    # ---- typed operations ----

    def activate_milestone(
        self,
        *,
        profile: str,
        plan_view: MilestonePlanView,
        origin_task_main_session_ref: str,
        executor_id: str,
        project_id: str,
        coordinator_id: str | None = None,
    ):
        _require_task_main_profile(profile)
        if not isinstance(plan_view, MilestonePlanView):
            raise TypeError("plan_view must be MilestonePlanView")
        return activate_milestone(
            store=self._coord_store,
            plan_view=plan_view,
            origin_task_main_session_ref=origin_task_main_session_ref,
            execution_dispatcher=self._dispatcher,
            completion_coordinator=self._completion,
            executor_id=executor_id,
            project_id=project_id,
            coordinator_id=coordinator_id,
        )

    def recover_coordinator(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
        session_available: bool = True,
    ):
        _require_task_main_profile(profile)
        return recover_coordinator(
            store=self._coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            execution_dispatcher=self._dispatcher,
            completion_coordinator=self._completion,
            session_available=session_available,
        )

    def advance_once(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
        handoff_resolver: Callable[[str], TaskHandoff],
        governed_evidence_resolver: Callable[[str], GovernedWorkItemEvidence] | None = None,
        reviewer_handoff_resolver: Callable[[], TaskHandoff] | None = None,
        governed_review_resolver: Callable[[str, str], GovernedReviewEvidence] | None = None,
        next_milestone_view: MilestonePlanView | None = None,
        session_available: bool = True,
        reviewer_canonical_task_id_resolver: Callable[[], str] | None = None,
    ) -> RunnerOutcome:
        _require_task_main_profile(profile)
        return advance_milestone_once(
            coordinator_store=self._coord_store,
            execution_store=self._exec_store,
            execution_dispatcher=self._dispatcher,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            handoff_resolver=handoff_resolver,
            governed_evidence_resolver=governed_evidence_resolver,
            reviewer_handoff_resolver=reviewer_handoff_resolver,
            governed_review_resolver=governed_review_resolver,
            next_milestone_view=next_milestone_view,
            completion_coordinator=self._completion,
            session_available=session_available,
            reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
        )

    def reconcile_worker_completion(
        self,
        *,
        profile: str,
        coordinator_id: str,
        canonical_task_id: str,
        card_digest: str,
        live_plan_view: MilestonePlanView,
        governed_evidence: GovernedWorkItemEvidence,
        handoff_resolver: Callable[[str], TaskHandoff] | None = None,
    ) -> ReconciliationOutcome:
        _require_task_main_profile(profile)
        return reconcile_worker_completion(
            store=self._coord_store,
            execution_store=self._exec_store,
            coordinator_id=coordinator_id,
            canonical_task_id=canonical_task_id,
            card_digest=card_digest,
            live_plan_view=live_plan_view,
            governed_evidence=governed_evidence,
            handoff_resolver=handoff_resolver,
        )

    def reconcile_review_completion(
        self,
        *,
        profile: str,
        coordinator_id: str,
        reviewer_canonical_task_id: str,
        card_digest: str,
        live_plan_view: MilestonePlanView,
        governed_review: GovernedReviewEvidence,
    ) -> ReconciliationOutcome:
        _require_task_main_profile(profile)
        return reconcile_review_completion(
            store=self._coord_store,
            execution_store=self._exec_store,
            coordinator_id=coordinator_id,
            reviewer_canonical_task_id=reviewer_canonical_task_id,
            card_digest=card_digest,
            live_plan_view=live_plan_view,
            governed_review=governed_review,
        )

    def create_runner(
        self,
        *,
        profile: str,
        live_plan_view: MilestonePlanView,
        handoff_resolver: Callable[[str], TaskHandoff],
        governed_evidence_resolver: Callable[[str], GovernedWorkItemEvidence] | None = None,
        reviewer_handoff_resolver: Callable[[], TaskHandoff] | None = None,
        governed_review_resolver: Callable[[str, str], GovernedReviewEvidence] | None = None,
        next_milestone_view: MilestonePlanView | None = None,
        coordinator_id: str | None = None,
        reviewer_canonical_task_id_resolver: Callable[[], str] | None = None,
    ) -> TaskMainMilestoneRunner:
        _require_task_main_profile(profile)
        return TaskMainMilestoneRunner(
            coordinator_store=self._coord_store,
            execution_store=self._exec_store,
            execution_dispatcher=self._dispatcher,
            live_plan_view=live_plan_view,
            handoff_resolver=handoff_resolver,
            governed_evidence_resolver=governed_evidence_resolver,
            reviewer_handoff_resolver=reviewer_handoff_resolver,
            governed_review_resolver=governed_review_resolver,
            next_milestone_view=next_milestone_view,
            completion_coordinator=self._completion,
            coordinator_id=coordinator_id,
            reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
        )


def is_worker_allowed_to_call_task_main_control(profile: str) -> bool:
    """Helper for MCP server-side binding: workers are never allowed."""
    return profile == TASK_MAIN_PROFILE


__all__ = [
    "DYNAMIC_PROVIDER_ROUTING_CREATED",
    "GENERIC_WORKFLOW_ENGINE_CREATED",
    "PER_ROLE_MCP_ARCHITECTURE_CREATED",
    "TASK_MAIN_CONTROL_OPERATIONS",
    "TASK_MAIN_PROFILE",
    "TASK_MAIN_RAW_SHELL_REQUIRED",
    "TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED",
    "WORKER_CAN_CALL_TASK_MAIN_CONTROL",
    "WORKER_PROFILE",
    "TaskMainControlAuthorityError",
    "TaskMainControlService",
    "is_worker_allowed_to_call_task_main_control",
]
