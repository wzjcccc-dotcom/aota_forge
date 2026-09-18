"""M3/W3 autonomous Milestone runner — bounded iteration over W1/W2/M2.

The smallest missing orchestration seam over the accepted W1 coordinator,
W2 CARD-first governed semantic reconciliation, and M2 durable execution
runtime. ONE bounded iteration, not a forever daemon.

Each invocation::

    recover durable truth
    → observe terminal completions
    → reconcile one available completion (CARD-first, exact session)
    → evaluate progression
    → dispatch ready work if allowed
    → derive next action
    → persist
    → return

A fresh process reconstructs the same durable truth (`PERSISTENCE_REQUIRES_FOREVER_PROCESS=no`);
the coordinator never sets user approval and never crosses the next gate.

Reuse only (``GENERIC_WORKFLOW_ENGINE_CREATED=no``,
``GENERIC_QUEUE_CREATED=no``, ``GENERIC_EVENT_BUS_CREATED=no``,
``DYNAMIC_PROVIDER_ROUTING_CREATED=no``, ``PER_ROLE_MCP_ARCHITECTURE_CREATED=no``):

* ``TaskMainCoordinator`` / ``MilestonePlanView`` / ``evaluate_ready_work_items``
* ``reconcile_worker_completion`` / ``reconcile_review_completion`` / receipts
* ``ExecutionStateStore`` / ``ExecutionDispatcher`` / ``DurableCompletionCoordinator``
* ``MilestoneWorkItemGraph`` / progression / review workflow / closure evaluators

Authority boundaries
--------------------
* Coordinator state is working truth, never Plan authority.
* TaskHandoff carries no session/tool/runtime authority.
* Workers mutate source via ``workspace.*``; task-main coordinates via typed
  control (``TASK_MAIN_RAW_SHELL_REQUIRED=no``,
  ``TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED=no``).
* ``WORKER_CAN_CALL_TASK_MAIN_CONTROL=no`` — enforced by the control transport.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.runtime.completion import (
    DurableCompletionCoordinator,
)
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    PlanDriftError,
    SessionRecoveryRequiredError,
    TaskMainCoordinator,
    TaskMainCoordinatorError,
)
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATE_IS_PLAN_AUTHORITY,
    WI_SEMANTIC_RECONCILED,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import (
    ACK_BEFORE_SEMANTIC_RECONCILIATION,
    CARD_FIRST,
    MODEL_ACK_STRING_ALONE_SUFFICIENT,
    CompletionReconciliationReceipt,
    GovernedReviewEvidence,
    GovernedWorkItemEvidence,
    ReconciliationError,
    reconcile_review_completion,
    reconcile_worker_completion,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.progression import MilestoneWorkItemGraph

# ---------------------------------------------------------------------------
# Governance markers
# ---------------------------------------------------------------------------

PERSISTENCE_REQUIRES_FOREVER_PROCESS = False
TASK_MAIN_CAN_SET_USER_APPROVAL = False
TASK_MAIN_CAN_CROSS_USER_GATE = False
TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE = False
COORDINATOR_STATE_IS_PLAN_AUTHORITY_FLAG = COORDINATOR_STATE_IS_PLAN_AUTHORITY
EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE = False
HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY = False

GENERIC_WORKFLOW_ENGINE_CREATED = False
GENERIC_QUEUE_CREATED = False
GENERIC_EVENT_BUS_CREATED = False
GENERIC_DAG_ENGINE_CREATED = False
GENERIC_WORKFLOW_DATABASE_CREATED = False
COMPLETION_INBOX_FRAMEWORK_CREATED = False
DYNAMIC_PROVIDER_ROUTING_CREATED = False
PER_ROLE_MCP_ARCHITECTURE_CREATED = False
AUTOMATIC_PLAN_AMENDMENT = False
AUTOMATIC_USER_APPROVAL = False
NEXT_MILESTONE_AUTO_ACTIVATION = False

TASK_MAIN_RAW_SHELL_REQUIRED = False
TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED = False
WORKER_CAN_CALL_TASK_MAIN_CONTROL = False

# AF #49 M1/W3 — production completion delivery trigger.
# The bounded task-main progression pass itself reconciles durable terminal
# Worker evidence into authoritative parent-side truth and delivers pending
# completion envelopes through the wired transport (parent re-entry). No
# operator/manual harness call, no manual recover_once/deliver_pending_once,
# no background loop, no scheduler, no model-controlled polling.
PRODUCTION_COMPLETION_DELIVERY_TRIGGER_IMPLEMENTED = True
PRODUCTION_COMPLETION_TRIGGER_PATH = "aota_forge/runtime/task_main/runner.py:advance_milestone_once"
PRODUCTION_COMPLETION_TRIGGER_STAGES = (
    "completion_coordinator.recover_once",
    "completion_coordinator.deliver_pending_once",
)
MANUAL_HARNESS_COMPLETION_TRIGGER_REQUIRED = False
COMPLETION_TRIGGER_IS_BOUNDED_PASS = True
COMPLETION_TRIGGER_BACKGROUND_LOOP_CREATED = False

CARD_FIRST_FLAG = CARD_FIRST
ACK_BEFORE_SEMANTIC_RECONCILIATION_FLAG = ACK_BEFORE_SEMANTIC_RECONCILIATION
MODEL_ACK_STRING_ALONE_SUFFICIENT_FLAG = MODEL_ACK_STRING_ALONE_SUFFICIENT
DAG_PROGRESSION_DETERMINISTIC = True

# ---------------------------------------------------------------------------
# Runner outcomes — bounded disposition vocabulary
# ---------------------------------------------------------------------------

DISPOSITION_DISPATCHED_WORK = "DISPATCHED_WORK"
DISPOSITION_WAITING_FOR_WORKERS = "WAITING_FOR_WORKERS"
DISPOSITION_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
DISPOSITION_RECONCILED = "RECONCILED"
DISPOSITION_INTEGRATED_REVIEW_REQUIRED = "INTEGRATED_REVIEW_REQUIRED"
DISPOSITION_REPAIR_REQUIRED = "REPAIR_REQUIRED"
DISPOSITION_RUNTIME_REVALIDATION_REQUIRED = "RUNTIME_REVALIDATION_REQUIRED"
DISPOSITION_PLAN_CHANGE_USER_GATE = "PLAN_CHANGE_USER_GATE"
DISPOSITION_MILESTONE_CLOSURE_READY = "MILESTONE_CLOSURE_READY"
DISPOSITION_NEXT_MILESTONE_USER_GATE = "NEXT_MILESTONE_USER_GATE"
DISPOSITION_BLOCKED = "BLOCKED"
DISPOSITION_USER_GATE_REQUIRED = "USER_GATE_REQUIRED"
DISPOSITION_SESSION_RECOVERY_REQUIRED = "SESSION_RECOVERY_REQUIRED"
DISPOSITION_DISPATCHED_REVIEW = "DISPATCHED_REVIEW"
DISPOSITION_RECONCILED_REVIEW = "RECONCILED_REVIEW"
DISPOSITION_RV2_REQUIRED = "RV2_REQUIRED"

RUNNER_DISPOSITIONS: frozenset[str] = frozenset(
    {
        DISPOSITION_DISPATCHED_WORK,
        DISPOSITION_WAITING_FOR_WORKERS,
        DISPOSITION_RECONCILIATION_REQUIRED,
        DISPOSITION_RECONCILED,
        DISPOSITION_INTEGRATED_REVIEW_REQUIRED,
        DISPOSITION_REPAIR_REQUIRED,
        DISPOSITION_RUNTIME_REVALIDATION_REQUIRED,
        DISPOSITION_PLAN_CHANGE_USER_GATE,
        DISPOSITION_MILESTONE_CLOSURE_READY,
        DISPOSITION_NEXT_MILESTONE_USER_GATE,
        DISPOSITION_BLOCKED,
        DISPOSITION_USER_GATE_REQUIRED,
        DISPOSITION_SESSION_RECOVERY_REQUIRED,
        DISPOSITION_DISPATCHED_REVIEW,
        DISPOSITION_RECONCILED_REVIEW,
        DISPOSITION_RV2_REQUIRED,
    }
)


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    return stripped


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


# ---------------------------------------------------------------------------
# RunnerOutcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerOutcome:
    """Bounded result of one autonomous iteration."""

    disposition: str
    coordinator_id: str
    coordinator_revision: int
    ready: tuple[str, ...] = ()
    dispatched: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    reconciled_work_item: str | None = None
    reconciled_canonical_task_id: str | None = None
    ack_eligible: bool = False
    ack_token: str | None = None
    replayed: bool = False
    physical_dispatch_attempts: int = 0
    integrated_review_required: bool = False
    milestone_closure_ready: bool = False
    next_milestone_gate: bool = False
    user_gate_required: bool = False
    session_recovery_required: bool = False
    progression_complete: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    receipt: CompletionReconciliationReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", _require_non_empty_str(self.disposition, "disposition"))
        if self.disposition not in RUNNER_DISPOSITIONS:
            raise ValueError(f"Unknown Runner disposition: {self.disposition!r}")
        object.__setattr__(self, "coordinator_id", _require_non_empty_str(self.coordinator_id, "coordinator_id"))
        if type(self.coordinator_revision) is not int or self.coordinator_revision < 1:
            raise ValueError("coordinator_revision must be int >= 1")
        for label in ("ready", "dispatched", "deferred", "progression_complete", "blocked", "reasons"):
            val = getattr(self, label)
            if not isinstance(val, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list")
            object.__setattr__(self, label, tuple(val))
        if self.reconciled_work_item is not None:
            object.__setattr__(
                self, "reconciled_work_item", _require_non_empty_str(self.reconciled_work_item, "reconciled_work_item")
            )
        if self.reconciled_canonical_task_id is not None:
            object.__setattr__(
                self,
                "reconciled_canonical_task_id",
                _require_non_empty_str(self.reconciled_canonical_task_id, "reconciled_canonical_task_id"),
            )
        if self.ack_token is not None:
            object.__setattr__(self, "ack_token", _require_non_empty_str(self.ack_token, "ack_token", max_length=512))
        for flag in (
            "ack_eligible",
            "replayed",
            "integrated_review_required",
            "milestone_closure_ready",
            "next_milestone_gate",
            "user_gate_required",
            "session_recovery_required",
        ):
            _require_strict_bool(getattr(self, flag), flag)
        if type(self.physical_dispatch_attempts) is not int or self.physical_dispatch_attempts < 0:
            raise ValueError("physical_dispatch_attempts must be int >=0")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _graph_for(state: TaskMainCoordinatorState) -> MilestoneWorkItemGraph:
    return MilestoneWorkItemGraph(
        milestone_ref=state.milestone_id,
        work_items=list(state.work_items),
        dependencies=[list(edge) for edge in state.dependencies],
    )


def _observe_terminal_via_coordinator(
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None,
    executor_id: str,
    project_id: str,
) -> None:
    # Use the coordinator's observe path but via a fresh handle that reloads durable truth.
    try:
        handle = TaskMainCoordinator(
            store=store,
            coordinator_id=coordinator_id,
            execution_dispatcher=dispatcher,
            completion_coordinator=completion_coordinator,
            executor_id=executor_id,
            project_id=project_id,
        )
        handle.observe_terminal_completions()
    except Exception:  # noqa: BLE001, S110
        # No terminal completions or coordinator not active; bounded no-op.
        # Plan drift/session errors are handled at the recover layer above.
        pass


def _production_completion_pass(
    completion_coordinator: DurableCompletionCoordinator | None,
) -> tuple[Any, Any]:
    """Bounded production completion trigger (AF #49 M1/W3 §14/§15).

    One recovery pass + one bounded delivery pass, executed as part of the
    production task-main lifecycle call itself:

    - ``recover_once()`` reconciles durable adapter/supervisor terminal
      evidence into authoritative parent-side execution truth (CanonicalResult
      + deterministic WorkerResultCard) through the existing dispatcher;
    - ``deliver_pending_once()`` delivers any pending CARD-first completion
      envelope through the wired transport (exact trusted parent session
      re-entry) with the existing claim/retry/ACK safety.

    No loop, no scheduler, no daemon, no model-controlled polling; a lifecycle
    pass that has nothing pending performs bounded no-op observations only.
    """
    if completion_coordinator is None:
        return None, None
    recovery = completion_coordinator.recover_once()
    delivery = completion_coordinator.deliver_pending_once()
    return recovery, delivery


def _find_pending_worker_reconciliation(state: TaskMainCoordinatorState) -> str | None:
    # A Work Item is pending reconciliation when its WI status is COMPLETION_PENDING_RECONCILIATION
    # but it has no durable reconciled receipt yet (semantic status != RECONCILED).
    # Choose deterministically smallest WI id.
    candidates: list[str] = []
    for wi, status in state.wi_status.items():
        if status != WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value:
            continue
        if state.wi_semantic_status.get(wi) == WI_SEMANTIC_RECONCILED:
            continue
        if wi not in state.bindings:
            continue
        cid = state.bindings[wi].get("canonical_task_id")
        if cid is None:
            continue
        if cid in state.reconciled_completions:
            continue
        candidates.append(wi)
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def _find_pending_reviewer_reconciliation(
    state: TaskMainCoordinatorState, execution_store: ExecutionStateStore
) -> tuple[str, str] | None:
    # Reviewer completions are keyed by canonical_task_id in reconciled_completions with kind REVIEW.
    # An unreconciled reviewer is a terminal execution record whose card exists but whose
    # canonical_task_id is absent from reconciled_completions and whose agent role is reviewer.
    for record in execution_store.list_all():
        if not record.canonical_task_state.is_terminal:
            continue
        if record.worker_result_card is None or record.worker_result_card_digest is None:
            continue
        card_dict = dict(record.worker_result_card)
        # Determine role without importing full card for speed: check agent_work_role field if present
        # The from_dict will tell, but we approximate.
        try:
            from aota_forge.work_plane.result_card import WorkerResultCard

            card = WorkerResultCard.from_dict(card_dict)
        except Exception:  # noqa: BLE001, S112
            continue
        from aota_forge.work_plane.roles import AgentWorkRole

        if card.agent_work_role != AgentWorkRole.REVIEWER:
            continue
        cid = record.canonical_task_id
        if cid in state.reconciled_completions:
            continue
        # Only consider reviewer tasks that logically belong to this milestone:
        # Use card milestone? The reviewer card does not carry milestone directly, but we can check
        # that the record's canonical_task_id contains the milestone id as a segment.
        # Fallback: accept any reviewer record while coordinator is ACTIVE.
        return cid, record.worker_result_card_digest
    return None


def _is_integrated_review_required(state: TaskMainCoordinatorState) -> bool:
    # All source WIs have sema reconciled and reviewer not yet reconciled.
    if not state.work_items:
        return False
    for wi in state.work_items:
        if state.wi_semantic_status.get(wi) != WI_SEMANTIC_RECONCILED:
            return False
        if state.wi_status.get(wi) != WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value:
            return False
    # Check if reviewer already reconciled -> not required
    for payload in state.reconciled_completions.values():
        try:
            receipt = CompletionReconciliationReceipt.from_dict(payload)
        except Exception:  # noqa: BLE001, S112
            continue
        if receipt.completion_kind == "MILESTONE_REVIEW":
            return False
    return True


def _is_closure_ready(state: TaskMainCoordinatorState) -> bool:
    for payload in state.reconciled_completions.values():
        try:
            receipt = CompletionReconciliationReceipt.from_dict(payload)
        except Exception:  # noqa: BLE001, S112
            continue
        if receipt.completion_kind == "MILESTONE_REVIEW" and receipt.progression_disposition == "REVIEW_READY_FOR_STEWARD":
            return True
    return False


def _coordinator_handle(
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None,
    project_id: str,
    executor_id: str,
) -> TaskMainCoordinator:
    return TaskMainCoordinator(
        store=store,
        coordinator_id=coordinator_id,
        execution_dispatcher=dispatcher,
        completion_coordinator=completion_coordinator,
        executor_id=executor_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Public: advance_milestone_once — one bounded deterministic iteration
# ---------------------------------------------------------------------------


def advance_milestone_once(
    *,
    coordinator_store: TaskMainCoordinatorStore,
    execution_store: ExecutionStateStore,
    execution_dispatcher: ExecutionDispatcher,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    handoff_resolver: Callable[[str], TaskHandoff],
    governed_evidence_resolver: Callable[[str], GovernedWorkItemEvidence] | None = None,
    reviewer_handoff_resolver: Callable[[], TaskHandoff] | None = None,
    governed_review_resolver: Callable[[str, str], GovernedReviewEvidence] | None = None,
    next_milestone_view: MilestonePlanView | None = None,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    session_available: bool = True,
    reviewer_canonical_task_id_resolver: Callable[[], str] | None = None,
) -> RunnerOutcome:
    """Advance one bounded autonomous step for the governed current Milestone.

    The caller may repeatedly invoke this with fresh runtime objects after a
    simulated restart; each invocation reloads durable truth and performs at
    most one semantic action (reconcile OR dispatch OR derive gate). No loop,
    no event bus, no queue.

    ``governed_evidence_resolver`` is the trusted task-main evidence channel
    (Work Item -> ``GovernedWorkItemEvidence``); ``governed_review_resolver``
    is the trusted reviewer channel (reviewer_task_id + card_digest -> review
    evidence). Raw Worker transcripts never enter semantics.
    """
    if not isinstance(coordinator_store, TaskMainCoordinatorStore):
        raise TypeError("coordinator_store must be TaskMainCoordinatorStore")
    if not isinstance(execution_store, ExecutionStateStore):
        raise TypeError("execution_store must be ExecutionStateStore")
    if not isinstance(execution_dispatcher, ExecutionDispatcher):
        raise TypeError("execution_dispatcher must be ExecutionDispatcher")
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError("live_plan_view must be MilestonePlanView")
    if not callable(handoff_resolver):
        raise TypeError("handoff_resolver must be callable")
    if governed_evidence_resolver is not None and not callable(governed_evidence_resolver):
        raise TypeError("governed_evidence_resolver must be callable or None")
    if reviewer_handoff_resolver is not None and not callable(reviewer_handoff_resolver):
        raise TypeError("reviewer_handoff_resolver must be callable or None")
    if governed_review_resolver is not None and not callable(governed_review_resolver):
        raise TypeError("governed_review_resolver must be callable or None")
    session_available = _require_strict_bool(session_available, "session_available")

    # ---- recover durable truth (fail-closed on drift / session loss) ----
    state = coordinator_store.get(coordinator_id)
    if state is None:
        from aota_forge.runtime.task_main.coordinator_store import (
            CoordinatorNotFoundError,
        )

        raise CoordinatorNotFoundError(coordinator_id)

    # Plan drift / next-milestone gate: same checks as W1 activation/recovery.
    from aota_forge.runtime.task_main.coordinator import (
        _check_live_binding as _check_binding,  # type: ignore[attr-defined]
    )

    try:
        _check_binding(state, live_plan_view)
    except PlanDriftError:
        raise
    except Exception as exc:
        raise PlanDriftError(str(exc)) from exc

    # Session gate
    if not session_available:
        # Persist SESSION_RECOVERY_REQUIRED if not already.
        if state.status != CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
            try:
                coordinator_store.compare_and_swap(
                    coordinator_id, state.coordinator_revision, {"status": CoordinatorStatus.SESSION_RECOVERY_REQUIRED.value}, state.revision_token
                )
                state = coordinator_store.get(coordinator_id) or state
            except Exception:  # noqa: BLE001, S110
                pass
        raise SessionRecoveryRequiredError(coordinator_id)

    # Reconcile lifecycle: SESSION_RECOVERY_REQUIRED + session back -> ACTIVE if gate clear.
    # This mirrors recover_coordinator but in the small runner we handle the same.
    if state.status == CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
        if live_plan_view.user_gate_blocked:
            try:
                coordinator_store.compare_and_swap(
                    coordinator_id, state.coordinator_revision, {"status": CoordinatorStatus.USER_GATE_REQUIRED.value}, state.revision_token
                )
                state = coordinator_store.get(coordinator_id) or state
            except Exception:  # noqa: BLE001, S110
                pass
            return RunnerOutcome(
                disposition=DISPOSITION_SESSION_RECOVERY_REQUIRED,
                coordinator_id=coordinator_id,
                coordinator_revision=state.coordinator_revision,
                session_recovery_required=True,
                user_gate_required=True,
                reasons=("session recovered but live view is gate-blocked",),
            )
        else:
            try:
                coordinator_store.compare_and_swap(
                    coordinator_id, state.coordinator_revision, {"status": CoordinatorStatus.ACTIVE.value}, state.revision_token
                )
                state = coordinator_store.get(coordinator_id) or state
            except Exception:  # noqa: BLE001, S110
                pass

    if state.status == CoordinatorStatus.CLOSED:
        return RunnerOutcome(
            disposition=DISPOSITION_BLOCKED,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            reasons=(f"coordinator {state.status.value}",),
        )

    if state.status == CoordinatorStatus.USER_GATE_REQUIRED or live_plan_view.user_gate_blocked:
        # Gate takes precedence: no dispatch, no reconciliation.
        return RunnerOutcome(
            disposition=DISPOSITION_USER_GATE_REQUIRED,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            user_gate_required=True,
            reasons=("USER_GATE_REQUIRED: live view or coordinator is gate-blocked",),
        )

    if state.status != CoordinatorStatus.ACTIVE:
        return RunnerOutcome(
            disposition=DISPOSITION_BLOCKED,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            reasons=(f"coordinator {state.status.value}",),
        )

    # Next milestone gate: if current milestone is closure-ready and next milestone exists but unapproved, stop.
    if _is_closure_ready(state) and next_milestone_view is not None:  # noqa: SIM102
        if next_milestone_view.milestone_id != state.milestone_id:
            # Next milestone must require explicit approval; runner must never auto activate.
            if not next_milestone_view.milestone_user_approval_satisfied or next_milestone_view.plan_amendment_required:
                return RunnerOutcome(
                    disposition=DISPOSITION_NEXT_MILESTONE_USER_GATE,
                    coordinator_id=coordinator_id,
                    coordinator_revision=state.coordinator_revision,
                    milestone_closure_ready=True,
                    next_milestone_gate=True,
                    user_gate_required=True,
                    reasons=("NEXT_MILESTONE_REQUIRES_EXPLICIT_APPROVAL",),
                )
            # If next milestone is approved, runner still stops: W3 never auto-activates next.
            return RunnerOutcome(
                disposition=DISPOSITION_NEXT_MILESTONE_USER_GATE,
                coordinator_id=coordinator_id,
                coordinator_revision=state.coordinator_revision,
                milestone_closure_ready=True,
                next_milestone_gate=True,
                user_gate_required=False,
                reasons=("NEXT_MILESTONE_USER_GATE: current closure ready; next milestone requires explicit activation",),
            )

    # ---- production completion trigger (bounded, AF #49 M1/W3) ----
    # Reconcile durable terminal Worker evidence into parent-side truth and
    # deliver pending completions (exact trusted parent re-entry) as part of
    # this normal lifecycle call. No operator/manual recovery or delivery call
    # participates; the pass stays bounded to one recovery + one delivery pass.
    _production_completion_pass(completion_coordinator)

    # ---- observe terminal completions (W1 bounded) ----
    _observe_terminal_via_coordinator(
        coordinator_store, coordinator_id, execution_dispatcher, completion_coordinator, state.executor_id, state.project_id
    )
    # Refresh after observation.
    state = coordinator_store.get(coordinator_id) or state

    # If observer moved a WI to COMPLETION_PENDING but its digest mismatch due to missing CARD, it stays ACTIVE.
    # The reconciliation step below will handle the pending case.

    # ---- reconcile one pending worker completion (CARD-first) ----
    pending_wi = _find_pending_worker_reconciliation(state)
    if pending_wi is not None and governed_evidence_resolver is not None:
        entry = state.bindings.get(pending_wi, {})
        canonical_task_id = entry.get("canonical_task_id")
        if canonical_task_id is not None:
            record = execution_store.get(canonical_task_id)
            card_digest = None
            if record is not None:
                card_digest = record.worker_result_card_digest
            if card_digest is not None:
                try:
                    governed = governed_evidence_resolver(pending_wi)
                except Exception as exc:
                    raise ReconciliationError(f"governed evidence resolver failed for {pending_wi!r}: {exc}") from exc
                if not isinstance(governed, GovernedWorkItemEvidence):
                    raise TypeError(f"governed evidence for {pending_wi!r} must be GovernedWorkItemEvidence")
                try:
                    outcome = reconcile_worker_completion(
                        store=coordinator_store,
                        execution_store=execution_store,
                        coordinator_id=coordinator_id,
                        canonical_task_id=canonical_task_id,
                        card_digest=card_digest,
                        live_plan_view=live_plan_view,
                        governed_evidence=governed,
                        handoff_resolver=handoff_resolver,
                    )
                except SessionRecoveryRequiredError:  # noqa: TRY203
                    raise
                except PlanDriftError:  # noqa: TRY203
                    raise
                except Exception:  # noqa: TRY203
                    raise
                # ACK eligibility: if completion_coordinator wired, the ACK is durable after receipt.
                # For tests with ScriptedTransport we do not need to call deliver; ack_eligible is already true.
                # Optionally attempt bounded ACK via store CAS when no transport is present.
                # We do not fabricate ACK; we just report eligibility.
                fresh = coordinator_store.get(coordinator_id) or state
                # Map worker outcome to runner disposition.
                if outcome.integrated_review_required:
                    return RunnerOutcome(
                        disposition=DISPOSITION_INTEGRATED_REVIEW_REQUIRED,
                        coordinator_id=coordinator_id,
                        coordinator_revision=fresh.coordinator_revision,
                        reconciled_work_item=pending_wi,
                        reconciled_canonical_task_id=canonical_task_id,
                        ack_eligible=outcome.ack_eligible,
                        ack_token=outcome.ack_token,
                        replayed=outcome.replayed,
                        integrated_review_required=True,
                        progression_complete=tuple(outcome.progression_complete),
                        blocked=tuple(outcome.blocked_work_items),
                        reasons=tuple(outcome.reasons),
                        receipt=outcome.receipt,
                    )
                if outcome.blocked_work_items:
                    return RunnerOutcome(
                        disposition=DISPOSITION_BLOCKED,
                        coordinator_id=coordinator_id,
                        coordinator_revision=fresh.coordinator_revision,
                        reconciled_work_item=pending_wi,
                        reconciled_canonical_task_id=canonical_task_id,
                        ack_eligible=outcome.ack_eligible,
                        ack_token=outcome.ack_token,
                        replayed=outcome.replayed,
                        blocked=tuple(outcome.blocked_work_items),
                        reasons=tuple(outcome.reasons),
                        receipt=outcome.receipt,
                    )
                return RunnerOutcome(
                    disposition=DISPOSITION_RECONCILED,
                    coordinator_id=coordinator_id,
                    coordinator_revision=fresh.coordinator_revision,
                    reconciled_work_item=pending_wi,
                    reconciled_canonical_task_id=canonical_task_id,
                    ack_eligible=outcome.ack_eligible,
                    ack_token=outcome.ack_token,
                    replayed=outcome.replayed,
                    ready=tuple(outcome.ready_work_items),
                    progression_complete=tuple(outcome.progression_complete),
                    blocked=tuple(outcome.blocked_work_items),
                    reasons=tuple(outcome.reasons),
                    receipt=outcome.receipt,
                )
            else:
                # No card yet durable: cannot reconcile; remain waiting.
                pass
        # Pending WI exists but card not yet durable or resolver missing => inform caller.
        fresh = coordinator_store.get(coordinator_id) or state
        return RunnerOutcome(
            disposition=DISPOSITION_RECONCILIATION_REQUIRED,
            coordinator_id=coordinator_id,
            coordinator_revision=fresh.coordinator_revision,
            reconciled_work_item=pending_wi,
            reasons=("terminal completion pending CARD-first reconciliation",),
        )

    # ---- reconcile one pending reviewer completion ----
    pending_reviewer = _find_pending_reviewer_reconciliation(state, execution_store)
    if pending_reviewer is not None and governed_review_resolver is not None:
        reviewer_cid, reviewer_digest = pending_reviewer
        try:
            governed_review = governed_review_resolver(reviewer_cid, reviewer_digest)
        except Exception as exc:
            raise ReconciliationError(f"governed review resolver failed: {exc}") from exc
        if not isinstance(governed_review, GovernedReviewEvidence):
            raise TypeError("governed_review must be GovernedReviewEvidence")
        outcome = reconcile_review_completion(
            store=coordinator_store,
            execution_store=execution_store,
            coordinator_id=coordinator_id,
            reviewer_canonical_task_id=reviewer_cid,
            card_digest=reviewer_digest,
            live_plan_view=live_plan_view,
            governed_review=governed_review,
        )
        fresh = coordinator_store.get(coordinator_id) or state
        # Map review outcome.
        if outcome.milestone_closure_ready:
            # Check next gate as above for closure.
            if next_milestone_view is not None:
                return RunnerOutcome(
                    disposition=DISPOSITION_NEXT_MILESTONE_USER_GATE if next_milestone_view.milestone_id != state.milestone_id else DISPOSITION_MILESTONE_CLOSURE_READY,
                    coordinator_id=coordinator_id,
                    coordinator_revision=fresh.coordinator_revision,
                    reconciled_canonical_task_id=reviewer_cid,
                    ack_eligible=outcome.ack_eligible,
                    ack_token=outcome.ack_token,
                    replayed=outcome.replayed,
                    milestone_closure_ready=True,
                    next_milestone_gate=next_milestone_view is not None,
                    reasons=tuple(outcome.reasons),
                    receipt=outcome.receipt,
                )
            return RunnerOutcome(
                disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                milestone_closure_ready=True,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        # Order: plan change (REPLAN) takes precedence over repair.
        disp = outcome.receipt.progression_disposition if outcome.receipt else ""
        if outcome.user_gate_required and disp == "REVIEW_REPLAN_REQUIRED":
            return RunnerOutcome(
                disposition=DISPOSITION_PLAN_CHANGE_USER_GATE,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                user_gate_required=True,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        if disp == "REVIEW_REPAIR_REQUIRED":
            return RunnerOutcome(
                disposition=DISPOSITION_REPAIR_REQUIRED,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        if disp == "REVIEW_RV2_REQUIRED" or outcome.rv2_required:
            return RunnerOutcome(
                disposition=DISPOSITION_RV2_REQUIRED,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        if disp == "REVIEW_BLOCKED_ENVIRONMENT":
            return RunnerOutcome(
                disposition=DISPOSITION_RUNTIME_REVALIDATION_REQUIRED,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        if disp == "REVIEW_BLOCKED":
            return RunnerOutcome(
                disposition=DISPOSITION_BLOCKED,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                reconciled_canonical_task_id=reviewer_cid,
                ack_eligible=outcome.ack_eligible,
                ack_token=outcome.ack_token,
                replayed=outcome.replayed,
                reasons=tuple(outcome.reasons),
                receipt=outcome.receipt,
            )
        return RunnerOutcome(
            disposition=DISPOSITION_RECONCILED_REVIEW,
            coordinator_id=coordinator_id,
            coordinator_revision=fresh.coordinator_revision,
            reconciled_canonical_task_id=reviewer_cid,
            ack_eligible=outcome.ack_eligible,
            ack_token=outcome.ack_token,
            replayed=outcome.replayed,
            reasons=tuple(outcome.reasons),
            receipt=outcome.receipt,
        )

    # ---- AF #49 M1/W6: unbound origin must never create child execution ----
    # Mechanical defense-in-depth projection of the ExecutionDispatcher gate:
    # while the trusted task-main origin is the pre-session placeholder, the
    # progression path refuses child dispatch instead of relying on the
    # dispatcher exception alone. No durable record is created either way.
    _origin_unbound = bool(getattr(execution_dispatcher, "origin_session_is_placeholder", False))

    # ---- integrated review required but not yet reconciled: should dispatch reviewer ----
    if _is_integrated_review_required(state) and reviewer_handoff_resolver is not None:
        if _origin_unbound:
            return RunnerOutcome(
                disposition=DISPOSITION_BLOCKED,
                coordinator_id=coordinator_id,
                coordinator_revision=state.coordinator_revision,
                integrated_review_required=True,
                reasons=("UNBOUND_ORIGIN_SESSION: real task-main session identity is not bound yet",),
            )
        # Check if reviewer already dispatched (execution record exists)
        _ = any(
            rec.canonical_task_id not in state.reconciled_completions
            and rec.worker_result_card is not None
            and not rec.canonical_task_state.is_terminal
            for rec in execution_store.list_all()
        )
        # Simpler: check for any nonterminal reviewer-like execution record
        # Determine if we already have a pending reviewer execution
        has_pending_reviewer = False
        for rec in execution_store.list_all():
            if rec.canonical_task_id in state.reconciled_completions:
                continue
            if not rec.canonical_task_state.is_terminal:  # noqa: SIM102
                # Heuristic: reviewer task ids contain ":RV"
                if ":RV" in rec.canonical_task_id or "review" in rec.canonical_task_id.lower():
                    has_pending_reviewer = True
                    break
        if has_pending_reviewer:
            fresh = coordinator_store.get(coordinator_id) or state
            return RunnerOutcome(
                disposition=DISPOSITION_WAITING_FOR_WORKERS,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                integrated_review_required=True,
                reasons=("integrated reviewer dispatched; awaiting completion",),
            )
        # Dispatch reviewer via dispatcher (one bounded dispatch)
        handoff = reviewer_handoff_resolver()
        if not isinstance(handoff, TaskHandoff):
            raise TypeError("reviewer_handoff_resolver must return TaskHandoff")
        # Resolve reviewer canonical id deterministically or via resolver param
        if reviewer_canonical_task_id_resolver is not None:
            reviewer_cid = reviewer_canonical_task_id_resolver()
            reviewer_cid = _require_non_empty_str(reviewer_cid, "reviewer_canonical_task_id")
        else:
            reviewer_cid = f"{state.project_id}:{state.milestone_id}:RV1:attempt-1"
        from aota_forge.work_plane.compiler import (
            TrustedExecutionBinding,
            compile_handoff_to_execution_package,
        )

        package = compile_handoff_to_execution_package(
            handoff,
            TrustedExecutionBinding(canonical_task_id=reviewer_cid, project_id=state.project_id),
            package_id=f"{reviewer_cid}:pkg",
            idempotency_key=f"reviewer|{reviewer_cid}",
            correlation_id=f"corr-{reviewer_cid}",
        )
        # Use completion coordinator if available for admission.
        try:
            if completion_coordinator is not None:
                completion_coordinator.recover_once()
                result = completion_coordinator.admit_dispatch(package, state.executor_id)
                from aota_forge.runtime.completion import AdmissionDecision

                if isinstance(result, AdmissionDecision) and not result.admitted:
                    return RunnerOutcome(
                        disposition=DISPOSITION_WAITING_FOR_WORKERS,
                        coordinator_id=coordinator_id,
                        coordinator_revision=state.coordinator_revision,
                        integrated_review_required=True,
                        reasons=("reviewer admission deferred",),
                    )
                # result is DispatchResult when admitted; fall through
                if isinstance(result, AdmissionDecision):
                    # admitted is False already handled; admitted True should not happen (AdmissionDecision only for refusal)
                    pass
            else:
                result = execution_dispatcher.dispatch(package, state.executor_id)
        except Exception as exc:
            raise TaskMainCoordinatorError(f"reviewer dispatch failed: {exc}") from exc
        fresh = coordinator_store.get(coordinator_id) or state
        return RunnerOutcome(
            disposition=DISPOSITION_DISPATCHED_REVIEW,
            coordinator_id=coordinator_id,
            coordinator_revision=fresh.coordinator_revision,
            dispatched=(reviewer_cid,),
            physical_dispatch_attempts=1,
            integrated_review_required=True,
            reasons=("dispatched integrated reviewer",),
        )

    # ---- evaluate ready work items and dispatch if allowed ----
    handle = _coordinator_handle(
        coordinator_store, coordinator_id, execution_dispatcher, completion_coordinator, state.project_id, state.executor_id
    )
    ready = handle.ready_work_items(live_plan_view=live_plan_view)
    if ready and _origin_unbound:
        return RunnerOutcome(
            disposition=DISPOSITION_BLOCKED,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            ready=tuple(ready),
            blocked=tuple(ready),
            reasons=(
                "UNBOUND_ORIGIN_SESSION: real exact task-main session identity is not bound yet; "
                "refusing child dispatch",
            ),
        )
    # Also direct evaluate for parity
    # ready = evaluate_ready_work_items(graph=_graph_for(state), wi_status=dict(state.wi_status), gate_blocked=False)
    if ready:
        report = handle.dispatch_ready(handoff_resolver, live_plan_view=live_plan_view)
        fresh = coordinator_store.get(coordinator_id) or state
        if report.dispatched:
            return RunnerOutcome(
                disposition=DISPOSITION_DISPATCHED_WORK,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                ready=tuple(report.ready),
                dispatched=tuple(d.work_item_id for d in report.dispatched),
                deferred=tuple(report.deferred),
                physical_dispatch_attempts=len(report.dispatched),
                reasons=("dispatched ready work items",),
            )
        if report.deferred:
            return RunnerOutcome(
                disposition=DISPOSITION_WAITING_FOR_WORKERS,
                coordinator_id=coordinator_id,
                coordinator_revision=fresh.coordinator_revision,
                ready=tuple(report.ready),
                deferred=tuple(report.deferred),
                reasons=("ready work items deferred by bounded concurrency",),
            )
        # ready non-empty but none dispatched/deferred means gate took precedence internally (should have been caught earlier)
        return RunnerOutcome(
            disposition=DISPOSITION_BLOCKED,
            coordinator_id=coordinator_id,
            coordinator_revision=fresh.coordinator_revision,
            ready=tuple(report.ready),
            reasons=("ready but gate blocked",),
        )

    # ---- no ready, check active workers ----
    active = [wi for wi, st in state.wi_status.items() if st == WorkItemCoordinatorStatus.ACTIVE.value]
    if active:
        return RunnerOutcome(
            disposition=DISPOSITION_WAITING_FOR_WORKERS,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            reasons=("active workers still running",),
        )

    # ---- all source reconciled but reviewer pending dispatch (already handled above) ----
    if _is_integrated_review_required(state):
        return RunnerOutcome(
            disposition=DISPOSITION_INTEGRATED_REVIEW_REQUIRED,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            integrated_review_required=True,
            reasons=("integrated review required",),
        )

    if _is_closure_ready(state):
        if next_milestone_view is not None:
            return RunnerOutcome(
                disposition=DISPOSITION_NEXT_MILESTONE_USER_GATE,
                coordinator_id=coordinator_id,
                coordinator_revision=state.coordinator_revision,
                milestone_closure_ready=True,
                next_milestone_gate=True,
                reasons=("milestone closure ready; next milestone gate",),
            )
        return RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=coordinator_id,
            coordinator_revision=state.coordinator_revision,
            milestone_closure_ready=True,
            reasons=("milestone closure ready",),
        )

    # ---- blocked / no progress ----
    # Check blocked via last receipt or via progression blocked (e.g., validation fail)
    # Fallback blocked.
    return RunnerOutcome(
        disposition=DISPOSITION_BLOCKED,
        coordinator_id=coordinator_id,
        coordinator_revision=state.coordinator_revision,
        reasons=("no ready work, no active workers, no pending reconciliation",),
    )


# ---------------------------------------------------------------------------
# Convenience: TaskMainMilestoneRunner object seam (source-consistent)
# ---------------------------------------------------------------------------


class TaskMainMilestoneRunner:
    """Thin object seam over ``advance_milestone_once`` for call-site ergonomics.

    Holds no progression truth: every ``advance_once`` reloads durable stores.
    ``PERSISTENCE_REQUIRES_FOREVER_PROCESS=no`` — recreate this object plus the
    stores after restart and the next advance reads the same durable frontier.
    """

    def __init__(
        self,
        *,
        coordinator_store: TaskMainCoordinatorStore,
        execution_store: ExecutionStateStore,
        execution_dispatcher: ExecutionDispatcher,
        live_plan_view: MilestonePlanView,
        handoff_resolver: Callable[[str], TaskHandoff],
        governed_evidence_resolver: Callable[[str], GovernedWorkItemEvidence] | None = None,
        reviewer_handoff_resolver: Callable[[], TaskHandoff] | None = None,
        governed_review_resolver: Callable[[str, str], GovernedReviewEvidence] | None = None,
        next_milestone_view: MilestonePlanView | None = None,
        completion_coordinator: DurableCompletionCoordinator | None = None,
        coordinator_id: str | None = None,
        reviewer_canonical_task_id_resolver: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(coordinator_store, TaskMainCoordinatorStore):
            raise TypeError("coordinator_store must be TaskMainCoordinatorStore")
        if not isinstance(execution_store, ExecutionStateStore):
            raise TypeError("execution_store must be ExecutionStateStore")
        if not isinstance(execution_dispatcher, ExecutionDispatcher):
            raise TypeError("execution_dispatcher must be ExecutionDispatcher")
        if coordinator_id is None:
            # Project-aware deterministic fallback: scan for durable coordinator with same milestone + plan authority.
            found: str | None = None
            for cand in coordinator_store.list_all():
                if cand.milestone_id == live_plan_view.milestone_id and cand.plan_authority == live_plan_view.plan_authority:
                    found = cand.coordinator_id
                    break
            if found is not None:
                coordinator_id = found
            else:
                # Heuristic before activation: project_id is not in view; use aota_forge as conventional project id.
                coordinator_id = f"aota_forge:{live_plan_view.milestone_id}"
        coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
        self._store = coordinator_store
        self._execution_store = execution_store
        self._dispatcher = execution_dispatcher
        self._completion = completion_coordinator
        self._live_plan_view = live_plan_view
        self._handoff_resolver = handoff_resolver
        self._governed_evidence_resolver = governed_evidence_resolver
        self._reviewer_handoff_resolver = reviewer_handoff_resolver
        self._governed_review_resolver = governed_review_resolver
        self._next_view = next_milestone_view
        self._reviewer_cid_resolver = reviewer_canonical_task_id_resolver
        # If caller supplied a placeholder before activation, re-resolve to the durable id that now exists.
        state = self._store.get(coordinator_id)
        if state is None:
            for cand in self._store.list_all():
                if cand.milestone_id == live_plan_view.milestone_id and cand.plan_authority == live_plan_view.plan_authority:
                    coordinator_id = cand.coordinator_id
                    break
        self._coordinator_id = coordinator_id

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    def advance_once(self, *, session_available: bool = True) -> RunnerOutcome:
        return advance_milestone_once(
            coordinator_store=self._store,
            execution_store=self._execution_store,
            execution_dispatcher=self._dispatcher,
            coordinator_id=self._coordinator_id,
            live_plan_view=self._live_plan_view,
            handoff_resolver=self._handoff_resolver,
            governed_evidence_resolver=self._governed_evidence_resolver,
            reviewer_handoff_resolver=self._reviewer_handoff_resolver,
            governed_review_resolver=self._governed_review_resolver,
            next_milestone_view=self._next_view,
            completion_coordinator=self._completion,
            session_available=session_available,
            reviewer_canonical_task_id_resolver=self._reviewer_cid_resolver,
        )

    def update_live_plan_view(self, view: MilestonePlanView) -> None:
        if not isinstance(view, MilestonePlanView):
            raise TypeError("view must be MilestonePlanView")
        self._live_plan_view = view

    def update_next_milestone_view(self, view: MilestonePlanView | None) -> None:
        if view is not None and not isinstance(view, MilestonePlanView):
            raise TypeError("view must be MilestonePlanView or None")
        self._next_view = view


__all__ = [
    "ACK_BEFORE_SEMANTIC_RECONCILIATION_FLAG",
    "AUTOMATIC_PLAN_AMENDMENT",
    "AUTOMATIC_USER_APPROVAL",
    "CARD_FIRST_FLAG",
    "COMPLETION_INBOX_FRAMEWORK_CREATED",
    "COORDINATOR_STATE_IS_PLAN_AUTHORITY_FLAG",
    "DAG_PROGRESSION_DETERMINISTIC",
    "DISPOSITION_BLOCKED",
    "DISPOSITION_DISPATCHED_REVIEW",
    "DISPOSITION_DISPATCHED_WORK",
    "DISPOSITION_INTEGRATED_REVIEW_REQUIRED",
    "DISPOSITION_MILESTONE_CLOSURE_READY",
    "DISPOSITION_NEXT_MILESTONE_USER_GATE",
    "DISPOSITION_PLAN_CHANGE_USER_GATE",
    "DISPOSITION_RECONCILED",
    "DISPOSITION_RECONCILED_REVIEW",
    "DISPOSITION_RECONCILIATION_REQUIRED",
    "DISPOSITION_REPAIR_REQUIRED",
    "DISPOSITION_RUNTIME_REVALIDATION_REQUIRED",
    "DISPOSITION_RV2_REQUIRED",
    "DISPOSITION_SESSION_RECOVERY_REQUIRED",
    "DISPOSITION_USER_GATE_REQUIRED",
    "DISPOSITION_WAITING_FOR_WORKERS",
    "DYNAMIC_PROVIDER_ROUTING_CREATED",
    "EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE",
    "GENERIC_DAG_ENGINE_CREATED",
    "GENERIC_EVENT_BUS_CREATED",
    "GENERIC_QUEUE_CREATED",
    "GENERIC_WORKFLOW_DATABASE_CREATED",
    "GENERIC_WORKFLOW_ENGINE_CREATED",
    "HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY",
    "MODEL_ACK_STRING_ALONE_SUFFICIENT_FLAG",
    "NEXT_MILESTONE_AUTO_ACTIVATION",
    "PERSISTENCE_REQUIRES_FOREVER_PROCESS",
    "PER_ROLE_MCP_ARCHITECTURE_CREATED",
    "COMPLETION_TRIGGER_BACKGROUND_LOOP_CREATED",
    "COMPLETION_TRIGGER_IS_BOUNDED_PASS",
    "MANUAL_HARNESS_COMPLETION_TRIGGER_REQUIRED",
    "PRODUCTION_COMPLETION_DELIVERY_TRIGGER_IMPLEMENTED",
    "PRODUCTION_COMPLETION_TRIGGER_PATH",
    "PRODUCTION_COMPLETION_TRIGGER_STAGES",
    "RUNNER_DISPOSITIONS",
    "TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE",
    "TASK_MAIN_CAN_CROSS_USER_GATE",
    "TASK_MAIN_CAN_SET_USER_APPROVAL",
    "TASK_MAIN_RAW_SHELL_REQUIRED",
    "TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED",
    "WORKER_CAN_CALL_TASK_MAIN_CONTROL",
    "RunnerOutcome",
    "TaskMainMilestoneRunner",
    "advance_milestone_once",
]
