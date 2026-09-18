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
from aota_forge.composition.stewardship import (
    LegacyStewardshipExecutor,
    LegacyStewardshipOutcome,
    StewardshipProductionError,
    create_legacy_stewardship_executor,
)
from aota_forge.governance.project_store import ProjectGovernanceStore
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.governance.stewardship import StewardshipCheckpoint
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY
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
from aota_forge.work_plane.steward_dispatch import StewardResult
from aota_forge.work_plane.steward_finalizer import (
    GhCliGitHubPort,
    SubprocessGitPort,
    TrustedStewardFinalizer,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


PRODUCTION_STEWARD_FACTORY_OWNED = True
MANUAL_STEWARD_EXECUTOR_INJECTION_REQUIRED = False
MANUAL_OPTIONAL_RESOLVER_REQUIRED = False
SECOND_STEWARD_RESULT_STORE = False
SECOND_RECEIPT_STORE = False
SECOND_STEWARD_REPLAY_STORE = False
PRODUCTION_COMPOSITION_OWNER_PATH = "aota_forge/composition/task_main.py"
PRODUCTION_COMPOSITION_OWNER_FUNCTION = "create_task_main_runner"


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


class StewardshipTaskMainComposition:
    """Composition-only delegation around the accepted legacy runner.

    The runtime runner remains unchanged.  Stewardship is invoked only after
    that runner reports the existing closure-ready lifecycle result.
    """

    def __init__(
        self,
        legacy_runner: TaskMainMilestoneRunner,
        stewardship_checkpoint: StewardshipCheckpoint,
        stewardship_executor: LegacyStewardshipExecutor,
        owned_governance_store: ProjectGovernanceStore | None = None,
    ) -> None:
        if not isinstance(legacy_runner, TaskMainMilestoneRunner):
            raise TypeError("legacy_runner must be TaskMainMilestoneRunner")
        if not isinstance(stewardship_checkpoint, StewardshipCheckpoint):
            raise TypeError("stewardship_checkpoint must be StewardshipCheckpoint")
        if not isinstance(stewardship_executor, LegacyStewardshipExecutor):
            raise TypeError("stewardship_executor must be LegacyStewardshipExecutor")
        self._legacy_runner = legacy_runner
        self._stewardship_checkpoint = stewardship_checkpoint
        self._stewardship_executor = stewardship_executor
        self._owned_governance_store = owned_governance_store
        self._last_stewardship_outcome: LegacyStewardshipOutcome | None = None

    @property
    def coordinator_id(self) -> str:
        return self._legacy_runner.coordinator_id

    @property
    def stewardship_executor(self) -> LegacyStewardshipExecutor:
        return self._stewardship_executor

    @property
    def stewardship_outcome(self) -> LegacyStewardshipOutcome | None:
        return self._last_stewardship_outcome

    @property
    def legacy_runner(self) -> TaskMainMilestoneRunner:
        return self._legacy_runner

    def advance_once(self, *, session_available: bool = True):
        runner_outcome = self._legacy_runner.advance_once(session_available=session_available)
        if runner_outcome.milestone_closure_ready:
            self._last_stewardship_outcome = self._stewardship_executor.execute(
                self._stewardship_checkpoint
            )
        return runner_outcome

    def update_live_plan_view(self, view: MilestonePlanView) -> None:
        self._legacy_runner.update_live_plan_view(view)

    def update_next_milestone_view(self, view: MilestonePlanView | None) -> None:
        self._legacy_runner.update_next_milestone_view(view)

    def update_stewardship_checkpoint(self, checkpoint: StewardshipCheckpoint) -> None:
        if not isinstance(checkpoint, StewardshipCheckpoint):
            raise TypeError("checkpoint must be StewardshipCheckpoint")
        self._stewardship_checkpoint = checkpoint

    def close(self) -> None:
        if self._owned_governance_store is not None:
            self._owned_governance_store.close()


def _coordinator_storage_path(
    coordinator_store: TaskMainCoordinatorStore | str | Path,
) -> Path | None:
    if isinstance(coordinator_store, (str, Path)):
        return Path(coordinator_store)
    raw_path = getattr(coordinator_store, "_path", None)
    return Path(raw_path) if isinstance(raw_path, (str, Path)) else None


def _open_stewardship_governance_store(
    governance_store: ProjectGovernanceStore | str | Path | None,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
) -> tuple[ProjectGovernanceStore, bool]:
    if isinstance(governance_store, ProjectGovernanceStore):
        return governance_store, False
    if isinstance(governance_store, (str, Path)):
        return SQLiteProjectGovernanceStore(governance_store), True
    coordinator_path = _coordinator_storage_path(coordinator_store)
    if coordinator_path is None:
        raise StewardshipProductionError(
            "GOVERNANCE_STORE_REQUIRED",
            "production Steward composition requires an existing governance store or a file-backed coordinator path",
        )
    return SQLiteProjectGovernanceStore(
        coordinator_path.with_name("governance.sqlite3")
    ), True


def _default_stewardship_dispatch(_handoff: TaskHandoff) -> StewardResult | None:
    raise StewardshipProductionError(
        "SEMANTIC_DISPATCH_UNWIRED",
        "semantic Steward dispatch is not configured for this production checkpoint",
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
    reviewer_canonical_task_id_resolver: Callable[[], str] | None = None,
    stewardship_checkpoint: StewardshipCheckpoint | None = None,
    stewardship_dispatch: Callable[[TaskHandoff], StewardResult | None] | None = None,
    stewardship_sandbox: WorktreeSandboxBoundary | None = None,
    stewardship_finalizer: TrustedStewardFinalizer | None = None,
    stewardship_repo_path: str | Path | None = None,
    stewardship_origin_session_ref: str | None = None,
    governance_store: ProjectGovernanceStore | str | Path | None = None,
) -> TaskMainMilestoneRunner | StewardshipTaskMainComposition:
    """Bounded autonomous runner over already-wired M2/W1/W2 stores (ONE iteration)."""
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )
    legacy_runner = TaskMainMilestoneRunner(
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
        reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
    )
    if stewardship_checkpoint is None:
        return legacy_runner
    if not isinstance(stewardship_checkpoint, StewardshipCheckpoint):
        raise TypeError("stewardship_checkpoint must be StewardshipCheckpoint")
    if stewardship_sandbox is None:
        raise StewardshipProductionError(
            "RESULT_SANDBOX_REQUIRED",
            "production Steward composition requires the trusted worktree sandbox",
        )
    if not isinstance(stewardship_sandbox, WorktreeSandboxBoundary):
        raise TypeError("stewardship_sandbox must be WorktreeSandboxBoundary")
    if stewardship_sandbox.project_id != stewardship_checkpoint.project_id:
        raise StewardshipProductionError(
            "RESULT_OWNER_PROJECT_MISMATCH",
            "Steward checkpoint and trusted result sandbox belong to different projects",
        )
    state = store.get(legacy_runner.coordinator_id)
    if state is not None:
        if state.project_id != stewardship_checkpoint.project_id:
            raise StewardshipProductionError(
                "CHECKPOINT_PROJECT_MISMATCH",
                "Steward checkpoint project does not match the active coordinator project",
            )
        if state.milestone_id != stewardship_checkpoint.trusted_plan.milestone_ref:
            raise StewardshipProductionError(
                "CHECKPOINT_MILESTONE_MISMATCH",
                "Steward checkpoint milestone does not match the active coordinator milestone",
            )
    governance, owns_governance = _open_stewardship_governance_store(
        governance_store, coordinator_store
    )
    finalizer = stewardship_finalizer
    if finalizer is None:
        finalizer = TrustedStewardFinalizer(
            git=SubprocessGitPort(),
            github=GhCliGitHubPort(),
            repo_path=Path(stewardship_repo_path)
            if stewardship_repo_path is not None
            else Path(stewardship_sandbox.project_root),
        )
    if not isinstance(finalizer, TrustedStewardFinalizer):
        raise TypeError("stewardship_finalizer must be TrustedStewardFinalizer")
    dispatch = stewardship_dispatch or _default_stewardship_dispatch
    if not callable(dispatch):
        raise TypeError("stewardship_dispatch must be callable or None")
    origin_session_ref = stewardship_origin_session_ref
    if origin_session_ref is None and state is not None:
        origin_session_ref = state.origin_task_main_session_ref
    try:
        executor = create_legacy_stewardship_executor(
            runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
            governance_store=governance,
            coordinator_store=store,
            coordinator_id=legacy_runner.coordinator_id,
            live_plan_view=live_plan_view,
            finalizer=finalizer,
            semantic_dispatch=dispatch,
            result_sandbox=stewardship_sandbox,
            origin_session_ref=origin_session_ref,
        )
    except Exception:
        if owns_governance:
            governance.close()
        raise
    return StewardshipTaskMainComposition(
        legacy_runner=legacy_runner,
        stewardship_checkpoint=stewardship_checkpoint,
        stewardship_executor=executor,
        owned_governance_store=governance if owns_governance else None,
    )


def create_task_main_control_service(
    *,
    coordinator_store: TaskMainCoordinatorStore | str | Path,
    execution_store: ExecutionStateStore,
    execution_dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    stewardship_checkpoint: StewardshipCheckpoint | None = None,
    stewardship_dispatch: Callable[[TaskHandoff], StewardResult | None] | None = None,
    stewardship_sandbox: WorktreeSandboxBoundary | None = None,
    stewardship_finalizer: TrustedStewardFinalizer | None = None,
    stewardship_repo_path: str | Path | None = None,
    stewardship_origin_session_ref: str | None = None,
    governance_store: ProjectGovernanceStore | str | Path | None = None,
) -> TaskMainControlService:
    """Typed task-main-only control service (``aota-task-main`` only)."""
    store = (
        open_task_main_coordinator_store(coordinator_store)
        if isinstance(coordinator_store, (str, Path))
        else coordinator_store
    )

    def production_runner_factory(**runner_inputs: Any) -> Any:
        return create_task_main_runner(
            **runner_inputs,
            stewardship_checkpoint=stewardship_checkpoint,
            stewardship_dispatch=stewardship_dispatch,
            stewardship_sandbox=stewardship_sandbox,
            stewardship_finalizer=stewardship_finalizer,
            stewardship_repo_path=stewardship_repo_path,
            stewardship_origin_session_ref=stewardship_origin_session_ref,
            governance_store=governance_store,
        )

    return TaskMainControlService(
        coordinator_store=store,
        execution_store=execution_store,
        execution_dispatcher=execution_dispatcher,
        completion_coordinator=completion_coordinator,
        runner_factory=production_runner_factory,
    )


__all__ = [
    "MANUAL_OPTIONAL_RESOLVER_REQUIRED",
    "MANUAL_STEWARD_EXECUTOR_INJECTION_REQUIRED",
    "PRODUCTION_COMPOSITION_OWNER_FUNCTION",
    "PRODUCTION_COMPOSITION_OWNER_PATH",
    "PRODUCTION_STEWARD_FACTORY_OWNED",
    "SECOND_RECEIPT_STORE",
    "SECOND_STEWARD_REPLAY_STORE",
    "SECOND_STEWARD_RESULT_STORE",
    "StewardshipTaskMainComposition",
    "advance_milestone_once",
    "create_task_main_control_service",
    "create_task_main_coordinator_handle",
    "create_task_main_runner",
    "open_task_main_coordinator_store",
    "reconcile_task_main_completion",
    "reconcile_task_main_review",
]
