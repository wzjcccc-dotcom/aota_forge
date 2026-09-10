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
    CoordinatorBindingError,
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

# Host-edge mechanical Hermes profile names (not authority).
# The mapping Hermes profile → neutral AF role is owned by the host adapter
# only (HERMES_PROFILE_TO_AF_ROLE_MAPPING_OWNER=HOST_EDGE_ONLY). Core/runtime
# authority must not branch on these literals (CORE_AUTHORITY_BRANCHES_ON_HERMES_LITERAL=no).
TASK_MAIN_PROFILE = "aota-task-main"
WORKER_PROFILE = "aota-worker"

# AF-neutral principal/role for semantic decisions (D8).
AF_TASK_MAIN_ROLE = "task-main"
AF_WORKER_ROLE = "worker"
# For backward compat, expose neutral as principal identity
AF_PRINCIPAL_IDENTITY_EXECUTOR_NEUTRAL = True

TASK_MAIN_CONTROL_OPERATIONS: tuple[str, ...] = (
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
    "task_main.submit_work_projection",
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
    # AF-neutral authority check (D8): Core/runtime must not depend on Hermes
    # profile literal (AF_CORE_DEPENDS_ON_HERMES_PROFILE_NAME=no,
    # AF_RUNTIME_AUTHORITY_DEPENDS_ON_HERMES_PROFILE_NAME=no).
    # Host representation is mechanically mapped at the outer edge to neutral role
    # before reaching Core. The neutral role is "task-main".
    if profile != AF_TASK_MAIN_ROLE:
        raise TaskMainControlAuthorityError(
            f"task-main control requires AF role {AF_TASK_MAIN_ROLE!r}, got {profile!r}: "
            "worker cannot access task-main control"
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

    # ---- canonical ownership seam (AF #46 M1/W2, D4) ----
    # Core/runtime owns Plan gate interpretation, coordinator identity
    # format, and coordinator discovery. Canonical ingress delegates here
    # instead of reading live_plan_view.user_gate_blocked directly,
    # inventing "{project}:{milestone}" strings, or reaching into the
    # private coordinator store. Adapter layers never call these with
    # model-supplied authority; inputs are trusted binding + live view.

    def is_user_gate_blocked(self, live_plan_view: MilestonePlanView) -> bool:
        """Core-owned live Plan gate interpretation (fail-closed).

        Returns True when progression must stop before the user gate.
        Delegates to MilestonePlanView.user_gate_blocked; any malformed
        view fails closed as blocked (never open).
        """
        try:
            return bool(live_plan_view.user_gate_blocked)
        except Exception:
            return True

    def default_coordinator_id(
        self, *, project_id: str, live_plan_view: MilestonePlanView
    ) -> str:
        """Core-owned coordinator identity format (no adapter knowledge).

        Single format authority: "{project_id}:{milestone_id}".
        """
        if not isinstance(project_id, str) or not project_id.strip():
            raise CoordinatorBindingError("project_id must be non-empty for coordinator identity")
        try:
            milestone_id = live_plan_view.milestone_id
        except Exception as exc:
            raise CoordinatorBindingError(f"live plan view missing milestone identity: {exc}") from exc
        if not isinstance(milestone_id, str) or not milestone_id.strip():
            raise CoordinatorBindingError("live plan view milestone_id must be non-empty")
        return f"{project_id.strip()}:{milestone_id.strip()}"

    def resolve_coordinator_id(
        self,
        *,
        project_id: str,
        live_plan_view: MilestonePlanView,
        coordinator_id: str | None,
    ) -> str:
        """Core-owned coordinator identity resolution (None -> default)."""
        if coordinator_id is not None and isinstance(coordinator_id, str) and coordinator_id.strip():
            if len(coordinator_id.strip()) > 512:
                raise CoordinatorBindingError("coordinator_id exceeds bound")
            return coordinator_id.strip()
        return self.default_coordinator_id(project_id=project_id, live_plan_view=live_plan_view)

    def discover_matching_coordinator_id(
        self,
        *,
        project_id: str,
        live_plan_view: MilestonePlanView,
        coordinator_id: str | None,
    ) -> str:
        """Core-owned coordinator discovery via public store boundary.

        Resolves the requested id; when absent durably, scans the public
        list_all() surface for a coordinator matching the live
        milestone_id + plan_authority (deterministic first match).
        Never exposes store layout; callers learn only the resolved id.
        """
        resolved = self.resolve_coordinator_id(
            project_id=project_id, live_plan_view=live_plan_view, coordinator_id=coordinator_id
        )
        try:
            if self._coord_store.get(resolved) is not None:
                return resolved
        except Exception:
            return resolved
        try:
            want_milestone = live_plan_view.milestone_id
            want_authority = live_plan_view.plan_authority
        except Exception:
            return resolved
        try:
            for cand in self._coord_store.list_all():
                if (
                    getattr(cand, "milestone_id", None) == want_milestone
                    and getattr(cand, "plan_authority", None) == want_authority
                ):
                    return cand.coordinator_id
        except Exception:
            pass
        return resolved

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

    def submit_work_projection(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
        work_item_id: str,
        projection: object,
    ):
        """Canonical task-main Work semantic submission (M3/W1).

        Thin typed wrapper over the coordinator's commit_task_main_work_projection.
        The caller is the task-main model via the canonical operation
        task_main.submit_work_projection; trusted binding (coordinator_id,
        live_plan_view) arrives server-side from the pre-resolved runtime
        context, never from model authority. The model supplies only
        work_item_id (correlation, validated against governed Work Items) plus
        the four bounded semantic fields; Core validates, binds trusted
        Plan/Milestone/Project/Work identities, and persists durably.
        """
        from aota_forge.runtime.task_main.coordinator import commit_task_main_work_projection

        _require_task_main_profile(profile)
        if not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError("live_plan_view must be MilestonePlanView")
        return commit_task_main_work_projection(
            store=self._coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            work_item_id=work_item_id,
            projection=projection,
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
    # Neutral check (not Hermes literal)
    return profile == AF_TASK_MAIN_ROLE


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
