"""Typed task-main control transport (M3/W3).

One restricted task-main-only typed control surface over the W1/W2/RUNNER
seams. No generic CLI, no stringly aota_cli, no per-role MCP architecture,
no raw shell. The same bounded MCP server (or internal host integration) may
host both the shared worker ``workspace.*`` and these task-main controls, but
server-side profile/authority binding guarantees:

* ``aota-task-main`` → may access bounded task-main control
* ``aota-worker``     → cannot access task-main control (fail-closed)

Workers retain bounded ``workspace.search / read / write / edit`` via the
shared AOTA MCP; source mutation remains coder-only and active-worktree-bound.

The semantic coordinator modules (``coordinator.py`` / ``reconciliation.py`` /
``runner.py``) remain Hermes-free — they never import ``adapters.hermes``.
This transport is the only place that may reference Hermes profiles when
exposing an MCP/host boundary, and it contains zero planning semantic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    activate_milestone,
    coordinator_matches_live_plan,
    default_coordinator_id as canonical_default_coordinator_id,
    recover_coordinator,
)
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.runtime.task_main.context_binding import DurableTaskMainContextBinding
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
)
from aota_forge.runtime.task_main.review_adoption import (
    AcceptedFrontierAuthorization,
    TrustedPlanAuthority,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.steward_finalizer import GitPort
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


RunnerFactory = Callable[..., Any]
PlanRouteEligibilityResolver = Callable[
    [str, MilestonePlanView, DurableTaskMainContextBinding], bool
]


def _legacy_runner_factory(**runner_inputs: Any) -> TaskMainMilestoneRunner:
    """Keep direct TaskMainControlService callers source-compatible."""
    return TaskMainMilestoneRunner(**runner_inputs)


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


class TaskMainProjectionConvergenceError(RuntimeError):
    """Separates verified/unknown authority mutation from projection convergence."""

    code = "PROJECTION_CONVERGENCE_BLOCKED"

    def __init__(
        self,
        *,
        authority_mutation: str,
        projection_convergence: str = "BLOCKED",
        cause_code: str,
        cause_type: str,
    ) -> None:
        self.authority_mutation = authority_mutation
        if projection_convergence not in {"BLOCKED", "NOT_ATTEMPTED"}:
            raise ValueError("projection_convergence must be BLOCKED or NOT_ATTEMPTED")
        self.projection_convergence = projection_convergence
        self.cause_code = cause_code
        self.cause_type = cause_type
        self.code = (
            "PROJECTION_CONVERGENCE_BLOCKED"
            if projection_convergence == "BLOCKED"
            else "PROJECTION_CONVERGENCE_NOT_ATTEMPTED"
        )
        super().__init__(
            f"AUTHORITY_MUTATION={authority_mutation}; "
            f"PROJECTION_CONVERGENCE={projection_convergence}; "
            f"cause={cause_code or cause_type}"
        )


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
        runner_factory: RunnerFactory | None = None,
        projection_refresh_hook: Callable[[], Any] | None = None,
        accepted_frontier_authorization: AcceptedFrontierAuthorization | None = None,
        review_adoption_worktree_root: WorktreeSandboxBoundary | None = None,
        review_adoption_git: GitPort | None = None,
        trusted_plan_authority: TrustedPlanAuthority | None = None,
        context_binding: DurableTaskMainContextBinding | None = None,
        current_plan_eligibility_resolver: PlanRouteEligibilityResolver | None = None,
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
        if runner_factory is not None and not callable(runner_factory):
            raise TypeError("runner_factory must be callable or None")
        self._runner_factory = runner_factory or _legacy_runner_factory
        if projection_refresh_hook is not None and not callable(projection_refresh_hook):
            raise TypeError("projection_refresh_hook must be callable or None")
        self._projection_refresh_hook = projection_refresh_hook
        if accepted_frontier_authorization is not None and not isinstance(
            accepted_frontier_authorization, AcceptedFrontierAuthorization
        ):
            raise TypeError("accepted_frontier_authorization must be AcceptedFrontierAuthorization or None")
        if review_adoption_worktree_root is not None and not isinstance(
            review_adoption_worktree_root, WorktreeSandboxBoundary
        ):
            raise TypeError("review_adoption_worktree_root must be WorktreeSandboxBoundary or None")
        if review_adoption_git is not None and review_adoption_worktree_root is None:
            raise TypeError("review_adoption_git requires review_adoption_worktree_root")
        if accepted_frontier_authorization is not None and (
            review_adoption_worktree_root is None or trusted_plan_authority is None
        ):
            raise TypeError(
                "accepted_frontier_authorization requires trusted review adoption context"
            )
        if trusted_plan_authority is not None and not isinstance(
            trusted_plan_authority, TrustedPlanAuthority
        ):
            raise TypeError("trusted_plan_authority must be TrustedPlanAuthority or None")
        self._accepted_frontier_authorization = accepted_frontier_authorization
        self._review_adoption_worktree_root = review_adoption_worktree_root
        self._review_adoption_git = review_adoption_git
        self._trusted_plan_authority = trusted_plan_authority
        if context_binding is not None and not isinstance(
            context_binding, DurableTaskMainContextBinding
        ):
            raise TypeError(
                "context_binding must be DurableTaskMainContextBinding or None"
            )
        self._context_binding = context_binding
        if current_plan_eligibility_resolver is not None and not callable(
            current_plan_eligibility_resolver
        ):
            raise TypeError(
                "current_plan_eligibility_resolver must be callable or None"
            )
        self._current_plan_eligibility_resolver = current_plan_eligibility_resolver

    def _refresh_projection_after_transition(
        self, *, authority_mutation: str = "NOT_APPLICABLE"
    ) -> None:
        """Refresh derived views without hiding a post-mutation convergence gap."""
        if self._projection_refresh_hook is not None:
            try:
                self._projection_refresh_hook()
            except Exception as exc:
                cause_code = getattr(exc, "code", None)
                raise TaskMainProjectionConvergenceError(
                    authority_mutation=(
                        authority_mutation
                        if authority_mutation in {"VERIFIED", "UNKNOWN", "NOT_APPLICABLE"}
                        else "UNKNOWN"
                    ),
                    cause_code=cause_code if isinstance(cause_code, str) else "",
                    cause_type=type(exc).__name__,
                ) from exc

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

    def _trusted_plan_id_for_live_view(
        self, *, project_id: str, live_plan_view: MilestonePlanView
    ) -> str | None:
        """Validate the current typed Plan binding before selecting a route."""
        context_binding = self._context_binding
        if context_binding is None:
            return None
        if not isinstance(live_plan_view, MilestonePlanView):
            raise CoordinatorBindingError("live_plan_view must be MilestonePlanView")
        try:
            context_binding.assert_live_plan(
                project_id=project_id,
                plan_authority=live_plan_view.plan_authority,
                plan_digest=live_plan_view.plan_digest,
                plan_source_revision=live_plan_view.plan_source_revision,
                origin_task_main_session_ref=context_binding.origin_task_main_session_ref,
            )
        except Exception as exc:
            raise CoordinatorBindingError(
                f"trusted PlanAuthorityBinding does not match the live Plan view: {exc}"
            ) from exc
        plan_binding = context_binding.plan_binding
        if (
            plan_binding.authority_ref != live_plan_view.plan_authority
            or plan_binding.source_digest != live_plan_view.plan_digest
            or (
                plan_binding.source_revision is None
            )
            != (live_plan_view.plan_source_revision is None)
            or (
                plan_binding.source_revision is not None
                and str(plan_binding.source_revision)
                != str(live_plan_view.plan_source_revision)
            )
        ):
            raise CoordinatorBindingError(
                "trusted PlanAuthorityBinding source revision/digest is stale or inconsistent"
            )
        if self._trusted_plan_authority is not None:
            trusted = self._trusted_plan_authority
            if (
                trusted.project_id != project_id
                or trusted.plan_id != plan_binding.plan_id
                or trusted.authority_source_kind != plan_binding.source_kind
                or trusted.authority_ref != plan_binding.authority_ref
                or trusted.source_digest != plan_binding.source_digest
                or str(trusted.source_revision) != str(plan_binding.source_revision)
            ):
                raise CoordinatorBindingError(
                    "trusted Plan authority does not match the current PlanAuthorityBinding"
                )
        if plan_binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            resolver = self._current_plan_eligibility_resolver
            if resolver is None:
                raise CoordinatorBindingError(
                    "current local Plan lifecycle resolver is unavailable"
                )
            try:
                eligible = resolver(project_id, live_plan_view, context_binding)
            except Exception as exc:
                raise CoordinatorBindingError(
                    f"current local Plan authority could not be verified: {exc}"
                ) from exc
            if eligible is not True:
                raise CoordinatorBindingError(
                    "current local Plan lifecycle/source is not eligible for coordinator routing"
                )
        return plan_binding.plan_id

    def _require_selected_coordinator(
        self,
        *,
        coordinator_id: str,
        project_id: str,
        live_plan_view: MilestonePlanView,
    ) -> None:
        plan_id = self._trusted_plan_id_for_live_view(
            project_id=project_id, live_plan_view=live_plan_view
        )
        if plan_id is None:
            return
        expected_id = canonical_default_coordinator_id(
            project_id=project_id,
            plan_id=plan_id,
            milestone_id=live_plan_view.milestone_id,
        )
        if coordinator_id != expected_id:
            raise CoordinatorBindingError(
                "selected coordinator ID is not the current Plan-aware identity"
            )
        try:
            state = self._coord_store.get(coordinator_id)
        except Exception as exc:
            raise CoordinatorBindingError(
                f"selected coordinator could not be read: {exc}"
            ) from exc
        if state is not None and not coordinator_matches_live_plan(
            state,
            project_id=project_id,
            live_plan_view=live_plan_view,
            context_binding=self._context_binding,
        ):
            raise CoordinatorBindingError(
                "durable coordinator does not match the current trusted Project/Plan/Milestone"
            )

    def default_coordinator_id(
        self, *, project_id: str, live_plan_view: MilestonePlanView
    ) -> str:
        """Core-owned Plan-aware identity, with plan-less legacy compatibility."""
        try:
            milestone_id = live_plan_view.milestone_id
        except Exception as exc:
            raise CoordinatorBindingError(f"live plan view missing milestone identity: {exc}") from exc
        plan_id = self._trusted_plan_id_for_live_view(
            project_id=project_id, live_plan_view=live_plan_view
        )
        return canonical_default_coordinator_id(
            project_id=project_id,
            plan_id=plan_id,
            milestone_id=milestone_id,
        )

    def resolve_coordinator_id(
        self,
        *,
        project_id: str,
        live_plan_view: MilestonePlanView,
        coordinator_id: str | None,
    ) -> str:
        """Resolve only a coordinator whose durable identity matches the live Plan."""
        plan_id = self._trusted_plan_id_for_live_view(
            project_id=project_id, live_plan_view=live_plan_view
        )
        default_id = canonical_default_coordinator_id(
            project_id=project_id,
            plan_id=plan_id,
            milestone_id=live_plan_view.milestone_id,
        )
        if plan_id is None:
            if coordinator_id is not None and isinstance(coordinator_id, str) and coordinator_id.strip():
                if len(coordinator_id.strip()) > 512:
                    raise CoordinatorBindingError("coordinator_id exceeds bound")
                return coordinator_id.strip()
            return default_id
        try:
            candidates = self._coord_store.list_all()
        except Exception as exc:
            raise CoordinatorBindingError(
                f"durable coordinator discovery failed closed: {exc}"
            ) from exc
        for candidate in candidates:
            if (
                candidate.coordinator_id == default_id
                and coordinator_matches_live_plan(
                    candidate,
                    project_id=project_id,
                    live_plan_view=live_plan_view,
                    context_binding=self._context_binding,
                )
            ):
                return candidate.coordinator_id
        return default_id

    def discover_matching_coordinator_id(
        self,
        *,
        project_id: str,
        live_plan_view: MilestonePlanView,
        coordinator_id: str | None,
    ) -> str:
        """Discover a durable coordinator only after full trusted identity checks."""
        plan_id = self._trusted_plan_id_for_live_view(
            project_id=project_id, live_plan_view=live_plan_view
        )
        resolved = canonical_default_coordinator_id(
            project_id=project_id,
            plan_id=plan_id,
            milestone_id=live_plan_view.milestone_id,
        )
        if plan_id is None:
            if coordinator_id is not None and isinstance(coordinator_id, str) and coordinator_id.strip():
                if len(coordinator_id.strip()) > 512:
                    raise CoordinatorBindingError("coordinator_id exceeds bound")
                resolved = coordinator_id.strip()
            try:
                direct = self._coord_store.get(resolved)
            except Exception:
                direct = None
            if direct is not None and coordinator_matches_live_plan(
                direct,
                project_id=project_id,
                live_plan_view=live_plan_view,
                context_binding=None,
            ):
                return resolved
        try:
            candidates = self._coord_store.list_all()
        except Exception as exc:
            raise CoordinatorBindingError(
                f"durable coordinator discovery failed closed: {exc}"
            ) from exc
        matching = [
            candidate
            for candidate in candidates
            if candidate.coordinator_id == resolved
            and coordinator_matches_live_plan(
                candidate,
                project_id=project_id,
                live_plan_view=live_plan_view,
                context_binding=self._context_binding,
            )
        ]
        if len(matching) > 1:
            raise CoordinatorBindingError(
                "multiple durable coordinators match the current Plan identity"
            )
        if matching:
            return matching[0].coordinator_id
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
        if (
            self._context_binding is not None
            and self._context_binding.origin_task_main_session_ref
            != origin_task_main_session_ref
        ):
            raise CoordinatorBindingError(
                "activation session differs from the trusted Plan context binding"
            )
        coordinator_id = self.resolve_coordinator_id(
            project_id=project_id,
            live_plan_view=plan_view,
            coordinator_id=coordinator_id,
        )
        result = activate_milestone(
            store=self._coord_store,
            plan_view=plan_view,
            origin_task_main_session_ref=origin_task_main_session_ref,
            execution_dispatcher=self._dispatcher,
            completion_coordinator=self._completion,
            executor_id=executor_id,
            project_id=project_id,
            coordinator_id=coordinator_id,
            context_binding=self._context_binding,
        )
        self._refresh_projection_after_transition()
        return result

    def recover_coordinator(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
        session_available: bool = True,
    ):
        _require_task_main_profile(profile)
        state = self._coord_store.get(coordinator_id)
        if state is not None:
            self._require_selected_coordinator(
                coordinator_id=coordinator_id,
                project_id=state.project_id,
                live_plan_view=live_plan_view,
            )
        result = recover_coordinator(
            store=self._coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            execution_dispatcher=self._dispatcher,
            completion_coordinator=self._completion,
            session_available=session_available,
            context_binding=self._context_binding,
        )
        self._refresh_projection_after_transition()
        return result

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
        reviewer_dispatch_resolver: Callable[[], Any] | None = None,
        accepted_frontier_authorization: AcceptedFrontierAuthorization | None = None,
    ) -> RunnerOutcome:
        _require_task_main_profile(profile)
        selected_state = self._coord_store.get(coordinator_id)
        self._require_selected_coordinator(
            coordinator_id=coordinator_id,
            project_id=(
                selected_state.project_id
                if selected_state is not None
                else self._context_binding.project_id
                if self._context_binding is not None
                else ""
            ),
            live_plan_view=live_plan_view,
        )
        runner = self.create_runner(
            profile=profile,
            live_plan_view=live_plan_view,
            handoff_resolver=handoff_resolver,
            governed_evidence_resolver=governed_evidence_resolver,
            reviewer_handoff_resolver=reviewer_handoff_resolver,
            governed_review_resolver=governed_review_resolver,
            next_milestone_view=next_milestone_view,
            coordinator_id=coordinator_id,
            reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
            reviewer_dispatch_resolver=reviewer_dispatch_resolver,
            accepted_frontier_authorization=accepted_frontier_authorization,
            review_adoption_worktree_root=self._review_adoption_worktree_root,
            review_adoption_git=self._review_adoption_git,
            trusted_plan_authority=self._trusted_plan_authority,
        )
        outcome = runner.advance_once(session_available=session_available)
        authority_mutation = "NOT_APPLICABLE"
        trusted_authority = self._trusted_plan_authority
        stewardship_outcome = getattr(runner, "stewardship_outcome", None)
        checkpoint_outcome = getattr(stewardship_outcome, "checkpoint_outcome", None)
        checkpoint_kind = getattr(checkpoint_outcome, "kind", None)
        checkpoint_kind = getattr(checkpoint_kind, "value", checkpoint_kind)
        if (
            trusted_authority is not None
            and trusted_authority.authority_source_kind
            == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
            and checkpoint_kind == "PLAN_CLOSE"
        ):
            execution_state = getattr(stewardship_outcome, "execution_state", None)
            execution_state = getattr(execution_state, "value", execution_state)
            if execution_state == "FINALIZED":
                authority_mutation = "VERIFIED"
            else:
                raise TaskMainProjectionConvergenceError(
                    authority_mutation="UNKNOWN",
                    projection_convergence="NOT_ATTEMPTED",
                    cause_code=(
                        getattr(stewardship_outcome, "error_code", None)
                        or "LOCAL_PLAN_CLOSE_OUTCOME_UNKNOWN"
                    ),
                    cause_type="StewardshipOutcomeNotFinalized",
                )
        self._refresh_projection_after_transition(authority_mutation=authority_mutation)
        return outcome

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
        state = self._coord_store.get(coordinator_id)
        if state is not None:
            self._require_selected_coordinator(
                coordinator_id=coordinator_id,
                project_id=state.project_id,
                live_plan_view=live_plan_view,
            )
        result = commit_task_main_work_projection(
            store=self._coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            work_item_id=work_item_id,
            projection=projection,
        )
        self._refresh_projection_after_transition()
        return result

    def adopt_normal_path_task_start(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
        work_item_id: str,
        canonical_task_id: str,
        projection: object,
        handoff_ref: str | None = None,
        handoff_digest: str | None = None,
    ):
        """Mechanically reconcile the normal-path task.start into coordinator state.

        AF #51 M1/W1 (I40-B007). Called by canonical ingress immediately after a
        successful grounded normal ``handoff.write(mode=work_item) ->
        task.start``; the exact actual canonical task id is adopted into the
        existing durable Work projection/binding/ACTIVE state. This is not a
        model-facing operation and adds no new agent-facing surface.
        """
        from aota_forge.runtime.task_main.coordinator import (
            adopt_normal_path_task_start as _adopt,
        )

        _require_task_main_profile(profile)
        if not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError("live_plan_view must be MilestonePlanView")
        result = _adopt(
            store=self._coord_store,
            coordinator_id=coordinator_id,
            live_plan_view=live_plan_view,
            work_item_id=work_item_id,
            canonical_task_id=canonical_task_id,
            projection=projection,
            execution_store=self._exec_store,
            handoff_ref=handoff_ref,
            handoff_digest=handoff_digest,
        )
        self._refresh_projection_after_transition()
        return result

    def get_projection_required_context(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """One shared projector accessor (M3/W1-R1 F2, no new operation).

        Returns (projection_required, missing_semantics) for ready Works
        lacking durable projections, via the single Core projector. Used by
        canonical ingress to surface governed context in activate/recover/
        advance results without creating a new agent-facing read operation.
        """
        from typing import Any as _Any

        from aota_forge.runtime.task_main.coordinator import build_projection_required_context

        _require_task_main_profile(profile)
        if not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError("live_plan_view must be MilestonePlanView")
        try:
            state = self._coord_store.get(coordinator_id)
        except Exception:
            state = None
        if state is None:
            return build_projection_required_context(live_plan_view)
        try:
            wi_status = dict(getattr(state, "wi_status", {}) or {})
            work_projs = dict(getattr(state, "work_projections", {}) or {})
        except Exception:
            wi_status = {}
            work_projs = {}
        return build_projection_required_context(
            live_plan_view, wi_status=wi_status, work_projections=work_projs
        )

    def get_model_visible_work_context(
        self,
        *,
        profile: str,
        coordinator_id: str,
        live_plan_view: MilestonePlanView,
    ) -> dict[str, Any] | None:
        """Mechanical model-visible authoritative Work context (M1/W2).

        Selects the current/ready Work Item from the durable coordinator state
        (ready -> in-flight -> completion pending) and returns the trusted
        bounded WorkSourceSlice fields. Never interprets Work meaning, never
        falls back to README/generic/neighbor content; missing structural
        source returns the explicit typed insufficient state.
        """
        from aota_forge.runtime.task_main.coordinator import build_model_visible_work_context

        _require_task_main_profile(profile)
        if not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError("live_plan_view must be MilestonePlanView")
        try:
            state = self._coord_store.get(coordinator_id)
        except Exception:
            state = None
        if state is None:
            return build_model_visible_work_context(live_plan_view)
        try:
            wi_status = dict(getattr(state, "wi_status", {}) or {})
            wi_semantic_status = dict(getattr(state, "wi_semantic_status", {}) or {})
        except Exception:
            wi_status = {}
            wi_semantic_status = {}
        return build_model_visible_work_context(
            live_plan_view,
            wi_status=wi_status or None,
            wi_semantic_status=wi_semantic_status or None,
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
        result = reconcile_worker_completion(
            store=self._coord_store,
            execution_store=self._exec_store,
            coordinator_id=coordinator_id,
            canonical_task_id=canonical_task_id,
            card_digest=card_digest,
            live_plan_view=live_plan_view,
            governed_evidence=governed_evidence,
            handoff_resolver=handoff_resolver,
        )
        self._refresh_projection_after_transition()
        return result

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
        result = reconcile_review_completion(
            store=self._coord_store,
            execution_store=self._exec_store,
            coordinator_id=coordinator_id,
            reviewer_canonical_task_id=reviewer_canonical_task_id,
            card_digest=card_digest,
            live_plan_view=live_plan_view,
            governed_review=governed_review,
            review_adoption_worktree_root=self._review_adoption_worktree_root,
            review_adoption_git=self._review_adoption_git,
            trusted_plan_authority=self._trusted_plan_authority,
        )
        self._refresh_projection_after_transition()
        return result

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
        reviewer_dispatch_resolver: Callable[[], Any] | None = None,
        accepted_frontier_authorization: AcceptedFrontierAuthorization | None = None,
        review_adoption_worktree_root: WorktreeSandboxBoundary | None = None,
        review_adoption_git: GitPort | None = None,
        trusted_plan_authority: TrustedPlanAuthority | None = None,
    ) -> Any:
        _require_task_main_profile(profile)
        selected_authorization = self._accepted_frontier_authorization
        if accepted_frontier_authorization is not None:
            if not isinstance(accepted_frontier_authorization, AcceptedFrontierAuthorization):
                raise TypeError(
                    "accepted_frontier_authorization must be AcceptedFrontierAuthorization or None"
                )
            if (
                selected_authorization is not None
                and accepted_frontier_authorization != selected_authorization
            ):
                raise CoordinatorBindingError(
                    "accepted_frontier_authorization conflicts with the trusted control binding"
                )
            selected_authorization = accepted_frontier_authorization
        selected_worktree_root = (
            self._review_adoption_worktree_root
            if review_adoption_worktree_root is None
            else review_adoption_worktree_root
        )
        selected_review_git = self._review_adoption_git if review_adoption_git is None else review_adoption_git
        selected_plan_authority = (
            self._trusted_plan_authority
            if trusted_plan_authority is None
            else trusted_plan_authority
        )
        runner = self._runner_factory(
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
            reviewer_dispatch_resolver=reviewer_dispatch_resolver,
            accepted_frontier_authorization=selected_authorization,
            review_adoption_worktree_root=selected_worktree_root,
            review_adoption_git=selected_review_git,
            trusted_plan_authority=selected_plan_authority,
        )
        if not callable(getattr(runner, "advance_once", None)):
            raise TypeError("runner_factory must return a runner with advance_once")
        return runner


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
    "TaskMainProjectionConvergenceError",
    "is_worker_allowed_to_call_task_main_control",
]
