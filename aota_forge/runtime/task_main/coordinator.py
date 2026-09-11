"""Persistent task-main Milestone coordinator (M3/W1).

Restart-safe, Plan-bound, durable coordinator / working-state foundation:

* activates an approved Milestone from trusted governed identities
* determines ready Work Items deterministically from the DAG
* compiles governed Work Items through the existing TaskHandoff compiler
* dispatches through the existing M2 runtime (dispatcher + durable store,
  admission-gated when a completion coordinator is wired)
* recovers coordinator progression after restart without duplicate dispatch
* stops fail-closed at the user gate

Reuse-only M2 surface (``M2_RUNTIME_REDESIGN_REQUIRED=no``):

* ``ExecutionStateStore`` — worker execution identity / dispatch idempotency /
  terminal result / delivery state (never coordinator semantic state)
* ``ExecutionDispatcher`` — durable intent-first dispatch + idempotent replay
* ``DurableCompletionCoordinator`` — ``recover_once()`` / admission bounds
  (physical dispatch bound; semantic readiness stays here)
* ``TrustedExecutionBinding`` + ``compile_handoff_to_execution_package``
* ``MilestoneWorkItemGraph`` — deterministic DAG progression
* ``origin_session_ref`` — opaque trusted runtime session identity

Authority boundaries
--------------------
* Coordinator state is execution working truth, never Plan authority
  (``COORDINATOR_STATE_IS_PLAN_AUTHORITY=no``).
* Live Plan authority + accepted Milestone approval + durable coordinator
  state must reconcile at activation/recovery; incompatibility fails closed.
* ``TASK_MAIN_CAN_SET_USER_APPROVAL=no``: approval arrives only as trusted
  governed input (``MilestonePlanView``); the coordinator never self-approves.
* ``TASK_MAIN_CAN_CROSS_USER_GATE=no``: an active gate yields zero ready
  Work Items and zero physical dispatches.
* task-main chooses which governed Work Item is semantically ready; provider /
  model / profile / executable / raw toolset stay operator RuntimeConfig
  authority (``TASK_MAIN_RAW_SHELL_REQUIRED=no``,
  ``TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED=no``).
* No public MCP surface is added here (W3 decides final runtime exposure).

Not W1 (M3/W2 + M3/W3 domain): CARD semantic apply, ACK-after-reconciliation,
review automation, repair/RV2 orchestration, Milestone closure automation.
``observe_terminal_completions`` only preserves/identifies terminal
completion pending W2 reconciliation from durable M2 record fields; it never
parses or applies WorkerResultCard semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aota_forge.core.execution.adapter import DispatchResult
from aota_forge.core.execution.dispatcher import (
    DispatchOutcomeUnresolvedError,
    ExecutionDispatcher,
    IdempotencyConflictError,
)
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.runtime.completion import (
    AdmissionDecision,
    DurableCompletionCoordinator,
)
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATE_IS_PLAN_AUTHORITY,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    CoordinatorNotFoundError,
    TaskMainCoordinatorStore,
)
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
    verify_execution_package_integrity,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.progression import MilestoneWorkItemGraph

# Single dispatch per Work Item in W1. Repair dispatch / retries are W2
# domain; W1 never invents a second attempt for the same governed identity.
COORDINATOR_DISPATCH_ATTEMPT = 1

TASK_MAIN_CAN_SET_USER_APPROVAL = False
TASK_MAIN_CAN_CROSS_USER_GATE = False
DAG_PROGRESSION_DETERMINISTIC = True
PERSISTENCE_REQUIRES_FOREVER_PROCESS = False
TASK_MAIN_RAW_SHELL_REQUIRED = False
TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED = False

# M2/W1 durable orchestration foundation markers.
DURABLE_COORDINATOR_STATE_IS_PROGRESS_TRUTH = True
TASK_MAIN_CHAT_CONTEXT_IS_PROGRESS_TRUTH = False
NEW_SECOND_COORDINATOR_STATE_MODEL = False
DURABLE_MINIMUM_TRUTH_WIRED = True
DURABLE_ATTEMPT_STATE_WIRED = True
HUMAN_BRAKE_STATE_DURABLE = True
TASK_MAIN_RESTART_FROM_DURABLE_STATE_SUPPORTED = True
TASK_MAIN_RESTART_REQUIRES_RAW_HISTORY = False
TASK_MAIN_CONTEXT_COMPRESSION_CAN_RECOVER_FROM_DURABLE_STATE = True
SESSION_ROLLOVER_DOES_NOT_RESET_WORKFLOW_AUTHORITY = True
RECOVERED_REF_IS_AUTHORITY = False
EXACT_LOGICAL_REENTRY_SUPPORTED = True
PROJECT_STATE_RUNTIME_FOUNDATION = True
PROJECT_STATE_ALWAYS_REFRESHED_EVERY_MILESTONE = False
CARD_FIRST_RECONCILIATION = True
RAW_WORKER_RESULT_REQUIRED_BY_TASK_MAIN = False
FULL_RESULT_HYDRATION_DEFAULT = False
NEW_WORKFLOW_DATABASE_CREATED = False
NEW_EVENT_BUS_CREATED = False
NEW_PUBLIC_MCP_TOOL_CREATED = False

# M1/W1-R1 task-main-owned bounded Work projection (AF #45 repair, I40-B003/F1).
# Authority: task-main planning layer produces the bounded WorkSemanticProjection;
# the deterministic AF runtime validates, binds to trusted Plan/Milestone/Work,
# persists durably in the existing coordinator store, and transports via the
# existing TaskHandoff. No operator work_semantics required on the normal path,
# no Codex per-Work injection, no operator refresh between Work Items, no
# restart reinjection. The projection is bounded execution projection only,
# never Plan authority; the handoff cannot expand Plan authority.
TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION = True
TASK_MAIN_SEMANTIC_LAYER_PRODUCES_WORK_PROJECTION = True
WORK_PROJECTION_DURABLE = True
WORK_PROJECTION_BOUND_TO_TRUSTED_PLAN_IDENTITY = True
WORK_PROJECTION_BOUND_TO_WORK_ITEM = True
WORK_SEMANTIC_PROJECTION_IS_PLAN_AUTHORITY = False
PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED = False
OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
CODEX_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
MANUAL_PER_WORK_SCOPE_INJECTION_REQUIRED = False
OPERATOR_REFRESH_REQUIRED_BETWEEN_WORK_ITEMS = False
TASK_MAIN_RESTART_REQUIRES_OPERATOR_WORK_SEMANTICS_REINJECTION = False
DETERMINISTIC_RUNTIME_REINTERPRETS_PLAN_PROSE = False
TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY = False

USER_GATE_REASON = "USER_GATE_REQUIRED"
SESSION_GATE_REASON = "SESSION_RECOVERY_REQUIRED"
NEXT_MILESTONE_GUARD = "NEXT_MILESTONE_REQUIRES_EXPLICIT_APPROVAL"


class TaskMainCoordinatorError(Exception):
    pass


class PlanDriftError(TaskMainCoordinatorError):
    """Live Plan authority is materially incompatible with durable binding."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"PLAN_DRIFT_FAILS_CLOSED: {detail}")


class SessionRecoveryRequiredError(TaskMainCoordinatorError):
    """Exact task-main session is unavailable; no silent fallback permitted."""

    def __init__(self, coordinator_id: str) -> None:
        self.coordinator_id = coordinator_id
        super().__init__(
            f"SESSION_RECOVERY_REQUIRED for {coordinator_id!r}: exact origin task-main "
            "session unavailable; refusing new/latest-session fallback"
        )


class CoordinatorBindingError(TaskMainCoordinatorError):
    pass


class CoordinatorRuntimeError(TaskMainCoordinatorError):
    pass


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


def dispatch_identity_for(
    *,
    project_id: str,
    plan_authority: str,
    milestone_id: str,
    work_item_id: str,
    attempt: int = COORDINATOR_DISPATCH_ATTEMPT,
) -> tuple[str, str, str]:
    """Deterministic dispatch identity from governed identities.

    Returns ``(canonical_task_id, idempotency_key, correlation_id)``. No
    random UUID participates in semantic dispatch identity, so a restart
    recomputes the same key and resolves the existing M2 execution instead
    of physically dispatching twice.
    """
    project_id = _require_non_empty_str(project_id, "project_id")
    plan_authority = _require_non_empty_str(plan_authority, "plan_authority")
    milestone_id = _require_non_empty_str(milestone_id, "milestone_id", max_length=128)
    work_item_id = _require_non_empty_str(work_item_id, "work_item_id", max_length=128)
    if type(attempt) is not int or attempt < 1:
        raise ValueError(f"attempt must be an int >= 1, got {attempt!r}")
    canonical_task_id = f"{project_id}:{milestone_id}:{work_item_id}:attempt-{attempt}"
    idempotency_key = (
        f"taskmain-coordinator|{plan_authority}|{milestone_id}|{work_item_id}|attempt-{attempt}"
    )
    correlation_id = f"{idempotency_key}|corr"
    return canonical_task_id, idempotency_key, correlation_id


@dataclass(frozen=True)
class MilestonePlanView:
    """Trusted governed Milestone input (operator / Friday channel, never model).

    Carries already-observed Plan authority binding, Milestone identity, DAG,
    and approval state. The coordinator records and checks these values; it
    never invents them and never mutates Plan authority.

    M3/W1-R1 F2: also carries bounded Work semantic authority
    (work_semantics, projection of canonical Plan normalization). The Plan
    remains authority; views are bounded (no full Plan body) and bound to
    plan_digest via the parent view + digest-covered document source.
    """

    plan_authority: str
    plan_digest: str
    milestone_id: str
    entry_base: str
    graph: MilestoneWorkItemGraph
    milestone_user_approval_satisfied: bool
    plan_source_revision: str | None = None
    plan_amendment_required: bool = False
    work_semantics: tuple[Any, ...] = ()
    # W4 bounded Work source slices (structural, faithful, digest-bound). The task-main LLM
    # reasons over these slices; Control Plane never synthesizes objective.
    work_source_slices: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_authority", _require_non_empty_str(self.plan_authority, "plan_authority"))
        object.__setattr__(self, "plan_digest", _require_non_empty_str(self.plan_digest, "plan_digest"))
        object.__setattr__(self, "milestone_id", _require_non_empty_str(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "entry_base", _require_non_empty_str(self.entry_base, "entry_base"))
        if not isinstance(self.graph, MilestoneWorkItemGraph):
            raise TypeError(f"graph must be MilestoneWorkItemGraph, got {type(self.graph).__name__}")
        if self.graph.milestone_ref != self.milestone_id:
            raise ValueError(
                f"graph milestone_ref {self.graph.milestone_ref!r} contradicts "
                f"milestone_id {self.milestone_id!r}"
            )
        object.__setattr__(
            self,
            "milestone_user_approval_satisfied",
            _require_strict_bool(self.milestone_user_approval_satisfied, "milestone_user_approval_satisfied"),
        )
        if self.plan_source_revision is not None:
            object.__setattr__(
                self,
                "plan_source_revision",
                _require_non_empty_str(self.plan_source_revision, "plan_source_revision"),
            )
        object.__setattr__(
            self,
            "plan_amendment_required",
            _require_strict_bool(self.plan_amendment_required, "plan_amendment_required"),
        )
        # work_semantics: tuple of GovernedWorkSemanticView (or empty for
        # legacy direct construction). Validated lightly to avoid importing
        # Plan layer at module load (cycle-safe duck typing).
        ws = self.work_semantics
        if not isinstance(ws, (tuple, list)):
            raise TypeError("work_semantics must be tuple/list")
        cleaned: list[Any] = []
        for idx, v in enumerate(ws):
            try:
                wid = getattr(v, "work_item_id", None)
                mid = getattr(v, "milestone_id", None)
                obj = getattr(v, "objective", None)
                ctx = getattr(v, "semantic_context", None)
            except Exception:
                raise TypeError(f"work_semantics[{idx}] must be GovernedWorkSemanticView")
            if not isinstance(wid, str) or not wid.strip():
                raise ValueError(f"work_semantics[{idx}].work_item_id must be non-empty")
            if not isinstance(mid, str) or mid.strip() != self.milestone_id:
                raise ValueError(
                    f"work_semantics[{idx}].milestone_id {mid!r} contradicts view {self.milestone_id!r}"
                )
            if not isinstance(obj, str) or not obj.strip():
                raise ValueError(f"work_semantics[{idx}].objective must be non-empty")
            if not isinstance(ctx, str) or not ctx.strip():
                raise ValueError(f"work_semantics[{idx}].semantic_context must be non-empty")
            if wid.strip() not in set(self.graph.work_items):
                raise ValueError(f"work_semantics[{idx}] unknown Work Item {wid!r}")
            cleaned.append(v)
        # Deduplicate by work_item_id, keep first, deterministic order by graph.
        seen: set[str] = set()
        ordered: list[Any] = []
        for wid_ordered in self.graph.work_items:
            for v in cleaned:
                if getattr(v, "work_item_id").strip() == wid_ordered and wid_ordered not in seen:
                    seen.add(wid_ordered)
                    ordered.append(v)
        object.__setattr__(self, "work_semantics", tuple(ordered))
        # work_source_slices: tuple of WorkSourceSlice (structural, faithful).
        # Validated lightly (duck typing) to avoid Plan layer import cycles.
        wss = getattr(self, "work_source_slices", ())
        if not isinstance(wss, (tuple, list)):
            raise TypeError("work_source_slices must be tuple/list")
        cleaned_s: list[Any] = []
        for idx, v in enumerate(wss):
            try:
                wid = getattr(v, "work_item_id", None)
                mid = getattr(v, "milestone_id", None)
                title = getattr(v, "title", None)
                source = getattr(v, "source_text", None)
            except Exception:
                raise TypeError(f"work_source_slices[{idx}] must be WorkSourceSlice")
            if not isinstance(wid, str) or not wid.strip():
                raise ValueError(f"work_source_slices[{idx}].work_item_id must be non-empty")
            if not isinstance(mid, str) or mid.strip() != self.milestone_id:
                raise ValueError(
                    f"work_source_slices[{idx}].milestone_id {mid!r} contradicts view {self.milestone_id!r}"
                )
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"work_source_slices[{idx}].title must be non-empty")
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"work_source_slices[{idx}].source_text must be non-empty")
            if wid.strip() not in set(self.graph.work_items):
                raise ValueError(f"work_source_slices[{idx}] unknown Work Item {wid!r}")
            # Structural slice must be bounded and not contain sibling leakage already enforced at creation,
            # but we enforce non-empty and not full Plan dump (bounded check implicit via WorkSourceSlice validation)
            cleaned_s.append(v)
        seen_s: set[str] = set()
        ordered_s: list[Any] = []
        for wid_ordered in self.graph.work_items:
            for v in cleaned_s:
                if getattr(v, "work_item_id").strip() == wid_ordered and wid_ordered not in seen_s:
                    seen_s.add(wid_ordered)
                    ordered_s.append(v)
        object.__setattr__(self, "work_source_slices", tuple(ordered_s))

    @property
    def user_gate_blocked(self) -> bool:
        """True when progression must stop before the user gate."""
        return (not self.milestone_user_approval_satisfied) or self.plan_amendment_required

    def get_work_source_slice(self, work_item_id: str) -> Any | None:
        """Return the bounded faithful source slice for one Work Item, or None.

        This is the W4 model-visible path: task-main reads this slice and
        reasons via LLM (no Control Plane semantic interpretation).
        """
        if not isinstance(work_item_id, str):
            return None
        wid = work_item_id.strip()
        for v in self.work_source_slices:
            try:
                if getattr(v, "work_item_id", "").strip() == wid:
                    return v
            except Exception:
                continue
        return None

    def get_work_semantic_view(self, work_item_id: str) -> Any | None:
        """Return the bounded governed view for one Work Item, or None."""
        if not isinstance(work_item_id, str):
            return None
        wid = work_item_id.strip()
        for v in self.work_semantics:
            try:
                if getattr(v, "work_item_id", "").strip() == wid:
                    return v
            except Exception:
                continue
        return None


def evaluate_ready_work_items(
    *,
    graph: MilestoneWorkItemGraph,
    wi_status: Mapping[str, str],
    gate_blocked: bool,
) -> tuple[str, ...]:
    """Deterministic ready-Work-Item evaluation (no LLM, no model input).

    A Work Item is ready exactly when: no user gate blocks progression, it
    is not already dispatched/completed, and every DAG predecessor has
    terminal completion observed (pending W2 semantic reconciliation).
    Predecessor *semantic* completion is W2 domain; W1 readiness is dispatch
    progression only.
    """
    if not isinstance(graph, MilestoneWorkItemGraph):
        raise TypeError(f"graph must be MilestoneWorkItemGraph, got {type(graph).__name__}")
    if _require_strict_bool(gate_blocked, "gate_blocked"):
        return ()
    if not isinstance(wi_status, Mapping):
        raise TypeError(f"wi_status must be a mapping, got {type(wi_status).__name__}")
    ready: list[str] = []
    for work_item_id in graph.work_items:
        if work_item_id not in wi_status:
            raise ValueError(f"wi_status missing Work Item entry: {work_item_id!r}")
        if wi_status[work_item_id] != WorkItemCoordinatorStatus.PENDING.value:
            continue
        predecessors = graph.predecessors_of(work_item_id)
        if all(
            wi_status.get(pred) == WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value
            for pred in predecessors
        ):
            ready.append(work_item_id)
    return tuple(ready)


def build_projection_required_context(
    live_plan_view: MilestonePlanView,
    *,
    wi_status: Mapping[str, str] | None = None,
    work_projections: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One shared Core projector for governed Work context (M3/W1-R1 F2).

    Returns (projection_required, missing_semantics):
    - projection_required: [{work_item_id, governed_work_semantics(dict)}]
      for ready Works lacking durable projections but having governed views.
    - missing_semantics: [work_item_id] for ready Works lacking projections
      AND lacking governed views (fail-closed, no heuristic).

    Empty when gate blocked, when no ready Works, or when all ready Works
    already have projections. Never invents scope; never requires extra read.
    """
    try:
        gate = bool(live_plan_view.user_gate_blocked)
    except Exception:
        gate = True
    if gate:
        return [], []
    try:
        graph = live_plan_view.graph
        work_items = tuple(graph.work_items)
    except Exception:
        return [], []
    if wi_status is None:
        try:
            from aota_forge.runtime.task_main.coordinator_state import WorkItemCoordinatorStatus as _WICS

            wi_status = {wid: _WICS.PENDING.value for wid in work_items}
        except Exception:
            return [], []
    try:
        ready = evaluate_ready_work_items(graph=graph, wi_status=wi_status, gate_blocked=False)
    except Exception:
        return [], []
    try:
        proj_table = dict(work_projections or {})
    except Exception:
        proj_table = {}
    required: list[dict[str, Any]] = []
    missing: list[str] = []
    for wid in ready:
        if wid in proj_table:
            continue
        view = None
        try:
            view = live_plan_view.get_work_semantic_view(wid)
        except Exception:
            view = None
        if view is None:
            # Only report missing when the view carries new authority
            # (non-empty work_semantics). Legacy empty views stay silent for
            # backward compat (old tests construct views without semantics).
            try:
                carries = len(tuple(live_plan_view.work_semantics or ())) > 0
            except Exception:
                carries = False
            if carries:
                missing.append(wid)
            continue
        try:
            gov = view.to_dict() if hasattr(view, "to_dict") else {
                "work_item_id": getattr(view, "work_item_id"),
                "milestone_id": getattr(view, "milestone_id"),
                "objective": getattr(view, "objective"),
                "semantic_context": getattr(view, "semantic_context"),
            }
        except Exception:
            missing.append(wid)
            continue
        required.append({"work_item_id": wid, "governed_work_semantics": gov})
    return required, missing


@dataclass(frozen=True)
class DispatchedWork:
    work_item_id: str
    canonical_task_id: str
    idempotency_key: str
    outcome: str  # DISPATCHED | UNRESOLVED


@dataclass(frozen=True)
class DispatchReport:
    gate_reason: str | None
    ready: tuple[str, ...]
    dispatched: tuple[DispatchedWork, ...]
    deferred: tuple[str, ...]

    @property
    def physical_dispatch_attempts(self) -> int:
        return len(self.dispatched)


def _default_coordinator_id(*, project_id: str, milestone_id: str) -> str:
    return f"{project_id}:{milestone_id}"


def _binding_matches(state: TaskMainCoordinatorState, view: MilestonePlanView) -> bool:
    return (
        state.plan_authority == view.plan_authority
        and state.plan_digest == view.plan_digest
        and state.milestone_id == view.milestone_id
        and state.entry_base == view.entry_base
        and state.work_items == tuple(sorted(view.graph.work_items))
        and state.dependencies == tuple(sorted(view.graph.dependencies))
    )


def _check_live_binding(state: TaskMainCoordinatorState, view: MilestonePlanView) -> None:
    """Fail closed when live Plan authority diverges from durable binding."""
    if state.plan_authority != view.plan_authority:
        raise PlanDriftError(
            f"plan authority changed: durable {state.plan_authority!r} vs live {view.plan_authority!r}"
        )
    if state.plan_digest != view.plan_digest:
        raise PlanDriftError(
            f"plan digest changed for {state.plan_authority}: durable {state.plan_digest[:16]}... "
            "vs live projection; refusing to continue stale execution truth"
        )
    if state.milestone_id != view.milestone_id:
        raise PlanDriftError(
            f"milestone changed: durable {state.milestone_id!r} vs live {view.milestone_id!r}; "
            f"{NEXT_MILESTONE_GUARD}"
        )
    if state.entry_base != view.entry_base:
        raise PlanDriftError(
            f"entry base changed: durable {state.entry_base!r} vs live {view.entry_base!r}"
        )
    if state.work_items != tuple(sorted(view.graph.work_items)) or state.dependencies != tuple(
        sorted(view.graph.dependencies)
    ):
        raise PlanDriftError("governed Milestone DAG changed under a durable coordinator")


def _require_durable_dispatcher(dispatcher: ExecutionDispatcher) -> ExecutionStateStore:
    if not isinstance(dispatcher, ExecutionDispatcher):
        raise TypeError(f"execution_dispatcher must be ExecutionDispatcher, got {type(dispatcher).__name__}")
    store = dispatcher.state_store
    if store is None:
        raise CoordinatorRuntimeError(
            "restart-safe coordination requires the M2 ExecutionDispatcher wired to an "
            "ExecutionStateStore; process-local dispatch cannot recover"
        )
    return store


def commit_task_main_work_projection(
    *,
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    work_item_id: str,
    projection: Any,
) -> Any:
    """Task-main-owned bounded Work projection commit (M1/W1-R1, AF #45).

    Smallest internal/runtime operation for task-main to commit a bounded Work
    projection. Called by the task-main semantic/planning layer (which has
    already reasoned about the current Plan/Milestone/Work); the deterministic
    runtime validates, binds to trusted identities, persists durably in the
    existing coordinator store, and never re-interprets Plan prose.

    M3/W1 canonical writer semantics (same function, now also reachable via
    the canonical model-facing operation task_main.submit_work_projection):

    * work_item_id must be a governed Work Item of the durable coordinator.
    * live_plan_view must match the durable coordinator binding exactly
      (stale Plan identity fails closed via PlanDriftError).
    * projection must be a bounded WorkSemanticProjection (malformed/oversized
      fails closed via WorkScopeInsufficientError) and must yield a
      Worker-usable handoff (generic boilerplate or objective-only scope fails
      closed; no generic fallback is restored).
    * The bound record persists plan_authority/plan_digest/milestone/project/
      work identities alongside the projection; a projection for W1 can never
      be reused as W2 (work_item_ref binding enforced at resolve).
    * Idempotent retry: a repeated identical submission for the same Work
      Item returns the current durable state without error and without
      corrupting state (no duplicate records).
    * Conflicting write: a repeated submission with different semantics is
      allowed only while the Work Item is still PENDING (explicit
      pre-dispatch replacement). Once the Work Item has left PENDING
      (dispatched/in-flight), a conflicting write fails closed with
      WorkProjectionConflictError (code PROJECTION_CONFLICT) — never silent
      last-write-wins.
    * Stale coordinator: a CAS revision mismatch fails closed with the
      store's StaleCoordinatorRevisionError, except that an identical retry
      against a newer revision still returns the current state (idempotent).

    Durable: the bound record survives coordinator reload/restart via the
    existing FileBackedTaskMainCoordinatorStore; restart never requires
    operator work_semantics reinjection. No raw LLM transcript is stored.
    """
    from aota_forge.work_plane.handoff_runtime import (
        WorkProjectionConflictError,
        WorkScopeInsufficientError,
        WorkSemanticProjection,
        resolve_bounded_work_handoff,
    )

    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    if not isinstance(work_item_id, str) or type(work_item_id) is not str:
        raise TypeError(f"work_item_id must be a string, got {type(work_item_id).__name__}")
    wid = work_item_id.strip()
    if not wid:
        raise ValueError("work_item_id must be a non-empty string")

    state = store.get(coordinator_id)
    if state is None:
        from aota_forge.runtime.task_main.coordinator_store import CoordinatorNotFoundError

        raise CoordinatorNotFoundError(coordinator_id)
    # Stale Plan identity fails closed (no silent expansion, no cross-Plan reuse).
    _check_live_binding(state, live_plan_view)
    if wid not in set(state.work_items):
        raise CoordinatorBindingError(f"unknown Work Item {wid!r} for coordinator {coordinator_id!r}")
    # M3/W1-R1 F2: missing governed Work semantics fails closed (no heuristic
    # invention). Only enforced when the trusted view carries new authority
    # (non-empty work_semantics); legacy empty views stay permissive for
    # backward compat (existing writer tests construct views directly).
    try:
        carries_new = len(tuple(getattr(live_plan_view, "work_semantics", ()) or ())) > 0
    except Exception:
        carries_new = False
    if carries_new:
        try:
            has_view = live_plan_view.get_work_semantic_view(wid) is not None
        except Exception:
            has_view = False
        if not has_view:
            from aota_forge.work_plane.handoff_runtime import WorkScopeInsufficientError as _WSSIE

            raise _WSSIE(
                f"missing governed Work semantics for Work Item {wid!r} "
                f"(Milestone {live_plan_view.milestone_id!r}); refusing heuristic scope"
            )

    # Typed bounded projection (malformed/oversized fails closed).
    if isinstance(projection, WorkSemanticProjection):
        typed = projection
    elif isinstance(projection, Mapping):
        proj_map = dict(projection)
        # work_item_id correlation (never authority): when present it must
        # match the governed wid, then is dropped before projection parse.
        if "work_item_id" in proj_map:
            supplied = proj_map.pop("work_item_id")
            if not isinstance(supplied, str) or supplied.strip() != wid:
                raise WorkScopeInsufficientError(
                    f"work_item_id correlation {supplied!r} contradicts governed Work Item {wid!r}"
                )
        try:
            typed = WorkSemanticProjection.from_dict(proj_map)
        except WorkScopeInsufficientError:
            raise
        except Exception as exc:
            raise WorkScopeInsufficientError(f"invalid Work projection for {wid!r}: {exc}") from exc
    else:
        raise WorkScopeInsufficientError(
            f"projection must be WorkSemanticProjection or mapping, got {type(projection).__name__}"
        )

    # Usability gate: must yield a Worker-usable handoff with trusted refs.
    # This rejects generic boilerplate, objective-carries-everything, empty
    # expectations, and missing refs without restoring a generic fallback.
    try:
        resolve_bounded_work_handoff(
            work_item_id=wid,
            milestone_ref=live_plan_view.milestone_id,
            projection=typed,
            project_id=state.project_id,
            plan_authority=live_plan_view.plan_authority,
            plan_digest=live_plan_view.plan_digest,
        )
    except WorkScopeInsufficientError:
        raise
    except Exception as exc:
        raise WorkScopeInsufficientError(
            f"Work projection for {wid!r} is not Worker-usable: {exc}"
        ) from exc

    bound: dict[str, Any] = {
        "projection": typed.to_dict(),
        "plan_authority": live_plan_view.plan_authority,
        "plan_digest": live_plan_view.plan_digest,
        "milestone_id": live_plan_view.milestone_id,
        "project_id": state.project_id,
        "work_item_id": wid,
    }
    existing_table = dict(getattr(state, "work_projections", {}) or {})
    existing_record = existing_table.get(wid)
    if isinstance(existing_record, Mapping):
        try:
            same_projection = dict(existing_record.get("projection", {})) == bound["projection"]
        except Exception:
            same_projection = False
        same_identity = (
            existing_record.get("plan_authority") == bound["plan_authority"]
            and existing_record.get("plan_digest") == bound["plan_digest"]
            and existing_record.get("milestone_id") == bound["milestone_id"]
            and existing_record.get("project_id") == bound["project_id"]
            and existing_record.get("work_item_id") == bound["work_item_id"]
        )
        if same_projection and same_identity:
            # Identical retry: idempotent, no mutation, no error.
            return state
        if not same_projection or not same_identity:
            # Conflicting write: allowed only while still PENDING
            # (explicit pre-dispatch replacement). Post-dispatch conflicts
            # fail closed — never silent last-write-wins.
            wi_status = dict(getattr(state, "wi_status", {}) or {})
            current_status = wi_status.get(wid)
            if current_status != WorkItemCoordinatorStatus.PENDING.value:
                raise WorkProjectionConflictError(
                    f"conflicting Work projection for {wid!r} after dispatch "
                    f"(status {current_status!r}); refusing silent overwrite"
                )
    merged = dict(existing_table)
    if len(merged) >= 64 and wid not in merged:
        raise WorkScopeInsufficientError(
            f"too many durable Work projections ({len(merged)}); refusing {wid!r}"
        )
    merged[wid] = bound
    try:
        updated = store.compare_and_swap(
            coordinator_id,
            state.coordinator_revision,
            {"work_projections": merged},
            state.revision_token,
        )
    except Exception as exc:
        # Identical retry against a newer revision stays idempotent: reload
        # and return current state when the durable record already matches.
        try:
            from aota_forge.runtime.task_main.coordinator_store import (
                StaleCoordinatorRevisionError,
            )
        except Exception:
            StaleCoordinatorRevisionError = None  # type: ignore
        if StaleCoordinatorRevisionError is not None and isinstance(exc, StaleCoordinatorRevisionError):
            try:
                fresh = store.get(coordinator_id)
            except Exception:
                fresh = None
            if fresh is not None:
                fresh_table = dict(getattr(fresh, "work_projections", {}) or {})
                fresh_record = fresh_table.get(wid)
                if isinstance(fresh_record, Mapping):
                    try:
                        fresh_same = dict(fresh_record.get("projection", {})) == bound["projection"]
                    except Exception:
                        fresh_same = False
                    fresh_identity = (
                        fresh_record.get("plan_authority") == bound["plan_authority"]
                        and fresh_record.get("plan_digest") == bound["plan_digest"]
                        and fresh_record.get("milestone_id") == bound["milestone_id"]
                        and fresh_record.get("project_id") == bound["project_id"]
                        and fresh_record.get("work_item_id") == bound["work_item_id"]
                    )
                    if fresh_same and fresh_identity:
                        return fresh
        raise
    return updated


def resolve_task_main_work_handoff(
    *,
    state: Any,
    work_item_id: str,
    live_plan_view: MilestonePlanView,
) -> TaskHandoff:
    """Resolve the existing TaskHandoff from a durable task-main projection.

    Reads the durable bound record for work_item_id, verifies it is still bound
    to the current trusted Plan/Milestone/Project/Work identities (wrong or
    stale identity fails closed), and builds the existing TaskHandoff via the
    reused bounded resolver. Missing projection fails closed with
    WORK_SCOPE_INSUFFICIENT (never a generic scope fallback).
    """
    from aota_forge.work_plane.handoff_runtime import (
        WORK_SCOPE_INSUFFICIENT,
        WorkScopeInsufficientError,
        WorkSemanticProjection,
        resolve_bounded_work_handoff,
    )

    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        raise ValueError("work_item_id must be a non-empty string")
    wid = work_item_id.strip()
    table = getattr(state, "work_projections", None) or {}
    if not isinstance(table, Mapping) or wid not in table:
        raise WorkScopeInsufficientError(
            f"{WORK_SCOPE_INSUFFICIENT}: no task-main-owned Work projection for "
            f"Work Item {wid!r} (Milestone {live_plan_view.milestone_id!r}); "
            "refusing scope-free dispatch"
        )
    record = table[wid]
    if not isinstance(record, Mapping):
        raise WorkScopeInsufficientError(
            f"corrupt durable Work projection for {wid!r}; refusing dispatch"
        )
    try:
        proj = WorkSemanticProjection.from_dict(record["projection"])
    except Exception as exc:
        raise WorkScopeInsufficientError(
            f"durable Work projection for {wid!r} is malformed: {exc}"
        ) from exc
    # Bound-identity checks (wrong/stale fails closed; no cross-Work reuse).
    for field, live_value in (
        ("plan_authority", live_plan_view.plan_authority),
        ("plan_digest", live_plan_view.plan_digest),
        ("milestone_id", live_plan_view.milestone_id),
        ("project_id", getattr(state, "project_id", None)),
        ("work_item_id", wid),
    ):
        stored = record.get(field)
        if not isinstance(stored, str) or stored.strip() != str(live_value).strip():
            raise WorkScopeInsufficientError(
                f"durable Work projection for {wid!r} is not bound to current "
                f"trusted {field} (stored {stored!r}); refusing stale/cross-Work dispatch"
            )
    return resolve_bounded_work_handoff(
        work_item_id=wid,
        milestone_ref=live_plan_view.milestone_id,
        projection=proj,
        project_id=getattr(state, "project_id", None),
        plan_authority=live_plan_view.plan_authority,
        plan_digest=live_plan_view.plan_digest,
    )


class TaskMainCoordinator:
    """Live handle over one durable Milestone coordinator.

    The handle holds no progression truth itself; every operation refreshes
    from the coordinator store first, so a fresh handle after process restart
    observes exactly the same durable state (``PERSISTENCE_REQUIRES_FOREVER_PROCESS=no``).
    """

    def __init__(
        self,
        *,
        store: TaskMainCoordinatorStore,
        coordinator_id: str,
        execution_dispatcher: ExecutionDispatcher,
        completion_coordinator: DurableCompletionCoordinator | None,
        executor_id: str,
        project_id: str,
    ) -> None:
        if not isinstance(store, TaskMainCoordinatorStore):
            raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
        _require_durable_dispatcher(execution_dispatcher)
        if completion_coordinator is not None and not isinstance(
            completion_coordinator, DurableCompletionCoordinator
        ):
            raise TypeError(
                "completion_coordinator must be DurableCompletionCoordinator or None, "
                f"got {type(completion_coordinator).__name__}"
            )
        self._store = store
        self._coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
        self._dispatcher = execution_dispatcher
        self._completion = completion_coordinator
        self._executor_id = _require_non_empty_str(executor_id, "executor_id")
        self._project_id = _require_non_empty_str(project_id, "project_id")
        self._state = self._reload()

    @property
    def coordinator_id(self) -> str:
        return self._coordinator_id

    @property
    def state(self) -> TaskMainCoordinatorState:
        return self._state

    def _reload(self) -> TaskMainCoordinatorState:
        state = self._store.get(self._coordinator_id)
        if state is None:
            raise CoordinatorNotFoundError(self._coordinator_id)
        self._state = state
        return state

    def refresh(self) -> TaskMainCoordinatorState:
        """Reload durable state (fresh-process truth, never cached across calls)."""
        return self._reload()

    def _cas(self, updates: Mapping[str, Any]) -> TaskMainCoordinatorState:
        try:
            self._state = self._store.compare_and_swap(
                self._coordinator_id,
                self._state.coordinator_revision,
                updates,
                self._state.revision_token,
            )
        except Exception:
            self._reload()
            raise
        return self._state

    def _is_gate(self, live_plan_view: MilestonePlanView | None) -> tuple[bool, str | None]:
        if self._state.status != CoordinatorStatus.ACTIVE:
            if self._state.status == CoordinatorStatus.USER_GATE_REQUIRED:
                return True, USER_GATE_REASON
            if self._state.status == CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
                return True, SESSION_GATE_REASON
            return True, f"COORDINATOR_{self._state.status.value}"
        if live_plan_view is not None and live_plan_view.user_gate_blocked:
            return True, USER_GATE_REASON
        if not self._state.user_approval_satisfied:
            return True, USER_GATE_REASON
        return False, None

    def _graph(self) -> MilestoneWorkItemGraph:
        return MilestoneWorkItemGraph(
            milestone_ref=self._state.milestone_id,
            work_items=list(self._state.work_items),
            dependencies=[list(edge) for edge in self._state.dependencies],
        )

    def ready_work_items(
        self, *, live_plan_view: MilestonePlanView | None = None
    ) -> tuple[str, ...]:
        """Deterministic ready set; empty under any gate (no dispatch)."""
        self.refresh()
        if live_plan_view is not None and not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError(f"live_plan_view must be MilestonePlanView or None, got {type(live_plan_view).__name__}")
        gate, _ = self._is_gate(live_plan_view)
        return evaluate_ready_work_items(
            graph=self._graph(), wi_status=dict(self._state.wi_status), gate_blocked=gate
        )

    def active_bindings(self) -> dict[str, dict[str, Any]]:
        """Recovered active dispatch bindings (Work Item -> execution identity)."""
        self.refresh()
        return {
            wi: dict(entry)
            for wi, entry in self._state.bindings.items()
            if self._state.wi_status.get(wi) == WorkItemCoordinatorStatus.ACTIVE.value
        }

    def dispatch_ready(
        self,
        handoff_resolver: Callable[[str], TaskHandoff],
        *,
        live_plan_view: MilestonePlanView | None = None,
    ) -> DispatchReport:
        """Dispatch every ready Work Item through the M2 runtime.

        ``handoff_resolver`` is trusted caller-supplied governed semantics
        (Work Item -> TaskHandoff); the coordinator decides *which* Work
        Items are ready, never *what* they mean. Physical dispatch stays
        under M2 idempotency + admission bounds: a restart that recomputes
        the same deterministic identity replays instead of redispatching.
        """
        if not callable(handoff_resolver):
            raise TypeError("handoff_resolver must be callable")
        self.refresh()
        gate, reason = self._is_gate(live_plan_view)
        if gate:
            return DispatchReport(gate_reason=reason, ready=(), dispatched=(), deferred=())
        if self._completion is not None:
            self._completion.recover_once()
        ready = evaluate_ready_work_items(
            graph=self._graph(), wi_status=dict(self._state.wi_status), gate_blocked=False
        )
        dispatched: list[DispatchedWork] = []
        deferred: list[str] = []
        for work_item_id in ready:
            handoff = handoff_resolver(work_item_id)
            if not isinstance(handoff, TaskHandoff):
                raise TypeError(
                    f"handoff_resolver must return TaskHandoff for {work_item_id!r}, "
                    f"got {type(handoff).__name__}"
                )
            if handoff.work_item_ref is not None and handoff.work_item_ref.ref != work_item_id:
                raise CoordinatorBindingError(
                    f"handoff work_item_ref {handoff.work_item_ref.ref!r} contradicts "
                    f"ready Work Item {work_item_id!r}"
                )
            canonical_task_id, idempotency_key, correlation_id = dispatch_identity_for(
                project_id=self._project_id,
                plan_authority=self._state.plan_authority,
                milestone_id=self._state.milestone_id,
                work_item_id=work_item_id,
            )
            package = compile_handoff_to_execution_package(
                handoff,
                TrustedExecutionBinding(
                    canonical_task_id=canonical_task_id, project_id=self._project_id
                ),
                package_id=f"{canonical_task_id}:pkg",
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            # M1/W1: the execution-visible package about to be dispatched must
            # match its validated handoff (scope tampering fails closed here,
            # at the last mile before physical dispatch).
            verify_execution_package_integrity(package, handoff)
            try:
                if self._completion is not None:
                    outcome = self._completion.admit_dispatch(package, self._executor_id)
                else:
                    outcome = self._dispatcher.dispatch(package, self._executor_id)
            except DispatchOutcomeUnresolvedError:
                # Durable PREPARED intent exists but the physical outcome is
                # unknown: do NOT create orphan ACTIVE. ACTIVE requires durable
                # DISPATCHED truth (adapter_handle) sufficient to recover/replay
                # idempotently without operator mutation. Keeping PENDING avoids
                # stranded ACTIVE (ACTIVE_WORK_ITEM_MUST_HAVE_DURABLE_EXECUTION_TRUTH).
                # The durable PREPARED remains for diagnostics but coordinator
                # stays PENDING (typed fail-closed non-ACTIVE), so restart can
                # continue deterministically without manual ACTIVE->PENDING reset
                # and without duplicate physical dispatch (retry will again hit
                # Unresolved and stay PENDING, which is non-ACTIVE).
                deferred.append(work_item_id)
                continue
            except IdempotencyConflictError as exc:
                raise CoordinatorBindingError(
                    f"governed intent for {work_item_id!r} changed under a durable "
                    "dispatch identity; refusing a second physical dispatch"
                ) from exc
            except Exception:
                # Generic physical dispatch failure (gateway restart, adapter
                # exception, store persistence failure, etc.): keep PENDING,
                # do not create ACTIVE orphan, do not lose intent, do not
                # duplicate. The dispatcher may have left a PREPARED record;
                # coordinator stays PENDING so STRANDED_ACTIVE_AFTER_DISPATCH_FAILURE==no.
                # Next advance will remain PENDING (non-ACTIVE fail-closed) and
                # will not blind-redispatch, satisfying idempotency.
                deferred.append(work_item_id)
                continue
            if isinstance(outcome, AdmissionDecision) and not outcome.admitted:
                deferred.append(work_item_id)
                continue
            if not isinstance(outcome, DispatchResult):
                raise CoordinatorRuntimeError(
                    f"unexpected dispatch outcome for {work_item_id!r}: {type(outcome).__name__}"
                )
            # Verify durable DISPATCHED truth before claiming ACTIVE (intent-first).
            # ACTIVE requires durable execution identity sufficient to recover.
            _verify = self._dispatcher.state_store
            if _verify is not None:
                _rec = _verify.get(canonical_task_id)
                if _rec is None or _rec.execution_phase.value != "DISPATCHED" or _rec.adapter_handle is None:
                    # No sufficient durable truth: do not go ACTIVE, stay PENDING (fail-closed)
                    deferred.append(work_item_id)
                    continue
            self._persist_binding(
                work_item_id,
                canonical_task_id=canonical_task_id,
                idempotency_key=idempotency_key,
                handoff=handoff,
            )
            dispatched.append(
                DispatchedWork(
                    work_item_id=work_item_id,
                    canonical_task_id=canonical_task_id,
                    idempotency_key=idempotency_key,
                    outcome="DISPATCHED",
                )
            )
        return DispatchReport(gate_reason=None, ready=ready, dispatched=tuple(dispatched), deferred=tuple(deferred))

    def _persist_binding(
        self,
        work_item_id: str,
        *,
        canonical_task_id: str,
        idempotency_key: str,
        handoff: TaskHandoff | None = None,
    ) -> None:
        wi_status = dict(self._state.wi_status)
        wi_status[work_item_id] = WorkItemCoordinatorStatus.ACTIVE.value
        bindings = {wi: dict(entry) for wi, entry in self._state.bindings.items()}
        # M2/W1 minimum truth: per-Work handoff_ref/digest is durable at dispatch;
        # result_ref/digest arrive via observe_terminal_completions; review_state
        # starts PENDING (W2 owns review policy, W1 only stores the seam).
        handoff_ref: str | None = None
        handoff_digest: str | None = None
        if handoff is not None:
            try:
                handoff_digest = handoff.compute_handoff_digest()
                handoff_ref = handoff.work_item_ref.ref if handoff.work_item_ref is not None else work_item_id
            except Exception:
                handoff_ref = work_item_id
                handoff_digest = None
        bindings[work_item_id] = {
            "canonical_task_id": canonical_task_id,
            "attempt": COORDINATOR_DISPATCH_ATTEMPT,
            "idempotency_key": idempotency_key,
            "completion_ref": None,
            "completion_card_digest": None,
            "handoff_ref": handoff_ref,
            "handoff_digest": handoff_digest,
            "result_ref": None,
            "result_digest": None,
            "review_state": "PENDING",
        }
        # Physical dispatch already happened under M2 idempotency; a stale
        # revision fails closed here and a refreshed retry replays instead
        # of redispatching.
        self._cas({"wi_status": wi_status, "bindings": bindings})

    def observe_terminal_completions(self) -> tuple[str, ...]:
        """Preserve/identify terminal completion pending W2 reconciliation.

        Reads durable M2 record fields only (terminal state + opaque CARD
        digest placeholder). Never parses, judges, or applies WorkerResultCard
        semantics — that is M3/W2 domain.
        """
        self.refresh()
        if self._state.status != CoordinatorStatus.ACTIVE:
            return ()
        execution_store = self._dispatcher.state_store
        assert execution_store is not None
        wi_status = dict(self._state.wi_status)
        bindings = {wi: dict(entry) for wi, entry in self._state.bindings.items()}
        observed: list[str] = []
        changed = False
        for work_item_id, entry in bindings.items():
            if wi_status.get(work_item_id) != WorkItemCoordinatorStatus.ACTIVE.value:
                continue
            record = execution_store.get(entry["canonical_task_id"])
            if record is None:
                raise CoordinatorBindingError(
                    f"active Work Item {work_item_id!r} has no durable M2 execution truth "
                    f"for {entry['canonical_task_id']!r}; refusing to guess completion"
                )
            if not record.canonical_task_state.is_terminal:
                continue
            wi_status[work_item_id] = WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value
            entry["completion_ref"] = record.canonical_task_id
            entry["completion_card_digest"] = record.worker_result_card_digest
            # M2/W1 minimum truth mirrors completion as result_ref/digest so
            # per-Work result identity is explicit durable truth (card-first).
            entry["result_ref"] = record.canonical_task_id
            entry["result_digest"] = record.worker_result_card_digest
            observed.append(work_item_id)
            changed = True
        if changed:
            self._cas({"wi_status": wi_status, "bindings": bindings})
        return tuple(sorted(observed))

    def close(self) -> TaskMainCoordinatorState:
        """Close the coordinator lifecycle (composition seam for W2/W3).

        This is lifecycle close only — not Milestone closure automation,
        which stays W3 domain.
        """
        self.refresh()
        if self._state.status == CoordinatorStatus.CLOSED:
            return self._state
        return self._cas({"status": CoordinatorStatus.CLOSED.value})

    def set_human_brake(
        self,
        *,
        state: str,
        scope: str,
        affected_work: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> TaskMainCoordinatorState:
        """Durably record Human Brake / user-gate state (W1 foundation).

        W1 stores the brake truth; W2 owns progression/decision policy.
        Survives restart; restart never auto-resolves checkpoints.
        """
        self.refresh()
        from datetime import datetime, timezone

        brake = {
            "state": state,
            "scope": scope,
            "affected_work": list(affected_work),
            "reason": reason,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        return self._cas({"human_brake": brake})

    def clear_human_brake(self) -> TaskMainCoordinatorState:
        """Clear brake to NONE (explicit operator/user action only, never auto)."""
        self.refresh()
        return self._cas(
            {"human_brake": {"state": "NONE", "scope": "NONE", "affected_work": [], "reason": None, "reported_at": ""}}
        )

    def record_attempt(
        self,
        work_item_id: str,
        *,
        failure_class: str,
        next_disposition: str,
        hypothesis_ref: str | None = None,
        risk_delta_ref: str | None = None,
        blocking_evidence_ref: str | None = None,
    ) -> TaskMainCoordinatorState:
        """Durably record compact attempt truth (no raw transcript, no giant log).

        Protects task-main context from retry noise; large logs remain as
        referenced evidence/artifacts.
        """
        self.refresh()
        if work_item_id not in self._state.work_items:
            raise CoordinatorBindingError(f"unknown Work Item {work_item_id!r}")
        current = dict(self._state.attempt_states.get(work_item_id, {}))
        attempt = int(current.get("attempt", 0)) + 1
        entry = {
            "attempt": attempt,
            "failure_class": failure_class,
            "hypothesis_ref": hypothesis_ref,
            "risk_delta_ref": risk_delta_ref,
            "blocking_evidence_ref": blocking_evidence_ref,
            "next_disposition": next_disposition,
        }
        states = {k: dict(v) for k, v in self._state.attempt_states.items()}
        states[work_item_id] = entry
        return self._cas({"attempt_states": states})

    def set_project_state(
        self,
        *,
        ref: str,
        digest: str | None = None,
        freshness: str | None = None,
        status: str | None = None,
    ) -> TaskMainCoordinatorState:
        """Durably record ProjectState ref + freshness/status (W1 foundation only).

        W2 owns conditional refresh/reuse lifecycle; W1 only stores the ref
        with trusted project binding (project_id already in state).
        """
        self.refresh()
        payload: dict[str, Any] = {"ref": ref}
        if digest is not None:
            payload["digest"] = digest
        if freshness is not None:
            payload["freshness"] = freshness
        if status is not None:
            payload["status"] = status
        return self._cas({"project_state": payload})

    def set_next_action(self, action: str | None, *, open_blockers: tuple[str, ...] = ()) -> TaskMainCoordinatorState:
        """Durably record next action + open blockers (compact, no raw history)."""
        self.refresh()
        updates: dict[str, Any] = {"next_action": action, "open_blockers": list(open_blockers)}
        return self._cas(updates)

    def commit_work_projection(
        self,
        *,
        work_item_id: str,
        projection: Any,
        live_plan_view: MilestonePlanView,
    ) -> Any:
        """Task-main-owned bounded Work projection commit (M1/W1-R1).

        Thin handle wrapper over commit_task_main_work_projection using this
        handle's durable store + coordinator id. The caller is the task-main
        semantic/planning layer; the runtime validates/binds/persists.
        Refreshes durable truth first so restart + concurrent CAS fail closed.
        """
        self.refresh()
        updated = commit_task_main_work_projection(
            store=self._store,
            coordinator_id=self._coordinator_id,
            live_plan_view=live_plan_view,
            work_item_id=work_item_id,
            projection=projection,
        )
        self._state = updated
        return updated

    def get_work_projection(self, work_item_id: str) -> dict[str, Any] | None:
        """Return the durable bound Work projection record, or None when absent."""
        self.refresh()
        table = getattr(self._state, "work_projections", {}) or {}
        entry = table.get(work_item_id.strip() if isinstance(work_item_id, str) else work_item_id)
        return dict(entry) if isinstance(entry, Mapping) else None

    def resolve_work_handoff(
        self, work_item_id: str, *, live_plan_view: MilestonePlanView
    ) -> TaskHandoff:
        """Resolve the existing TaskHandoff from the durable task-main projection."""
        self.refresh()
        return resolve_task_main_work_handoff(
            state=self._state, work_item_id=work_item_id, live_plan_view=live_plan_view
        )

    def durable_snapshot(self) -> dict[str, Any]:
        """Reconstruct orchestration truth from durable state only (no raw history).

        Returns plan/milestone/DAG, per-Work truth, derived ready_set,
        risk/review/project/frontier/blockers/brake/next-action. Proves
        TASK_MAIN_RESTART_REQUIRES_RAW_HISTORY=no and logical re-entry from
        durable + card refs + trusted Plan.
        """
        self.refresh()
        s = self._state
        graph = self._graph()
        ready = evaluate_ready_work_items(graph=graph, wi_status=dict(s.wi_status), gate_blocked=False)
        return {
            "plan_ref": s.plan_authority,
            "plan_digest": s.plan_digest,
            "milestone_ref": s.milestone_id,
            "entry_base": s.entry_base,
            "work_items": list(s.work_items),
            "dependencies": [list(e) for e in s.dependencies],
            "wi_status": dict(s.wi_status),
            "bindings": {k: dict(v) for k, v in s.bindings.items()},
            "ready_set": list(ready),
            "risk_projection": dict(s.risk_projection) if s.risk_projection else None,
            "review_gates": dict(s.review_gates) if s.review_gates else None,
            "integrated_review": dict(s.integrated_review) if s.integrated_review else None,
            "project_state": dict(s.project_state) if s.project_state else None,
            "frontier_ref": dict(s.frontier_ref) if s.frontier_ref else None,
            "open_blockers": list(s.open_blockers),
            "human_brake": dict(s.human_brake) if s.human_brake else None,
            "attempt_states": {k: dict(v) for k, v in s.attempt_states.items()},
            "next_action": s.next_action,
            "status": s.status.value,
        }

    def reconcile_completion(
        self,
        canonical_task_id: str,
        card_digest: str,
        live_plan_view: MilestonePlanView | None,
        governed_evidence: Any,
        handoff_resolver: Callable[[str], TaskHandoff] | None = None,
    ) -> Any:
        """CARD-first governed semantic reconciliation for one Worker completion.

        Narrow M3/W2 integration: delegates to
        ``aota_forge.runtime.task_main.reconciliation.reconcile_worker_completion``
        with this handle's durable stores. The coordinator itself never
        judges CARD semantics; the reconciliation module hydrates trusted
        M2 truth, runs existing progression evaluators, CAS-persists the
        receipt, and only then reports ACK eligibility. (Local import keeps
        the module dependency direction acyclic: reconciliation may import
        coordinator, never the reverse at module level.)
        """
        from aota_forge.runtime.task_main.reconciliation import (
            reconcile_worker_completion,
        )

        if live_plan_view is not None and not isinstance(live_plan_view, MilestonePlanView):
            raise TypeError(
                f"live_plan_view must be MilestonePlanView or None, got {type(live_plan_view).__name__}"
            )
        if live_plan_view is None:
            raise CoordinatorBindingError(
                "reconciliation requires the live governed plan view; refusing viewless semantic apply"
            )
        execution_store = self._dispatcher.state_store
        assert execution_store is not None
        return reconcile_worker_completion(
            store=self._store,
            execution_store=execution_store,
            coordinator_id=self._coordinator_id,
            canonical_task_id=canonical_task_id,
            card_digest=card_digest,
            live_plan_view=live_plan_view,
            governed_evidence=governed_evidence,
            handoff_resolver=handoff_resolver,
        )


def activate_milestone(
    *,
    store: TaskMainCoordinatorStore,
    plan_view: MilestonePlanView,
    origin_task_main_session_ref: str,
    execution_dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    executor_id: str,
    project_id: str,
    coordinator_id: str | None = None,
) -> TaskMainCoordinator:
    """Activate an approved Milestone into a durable coordinator.

    Activation is idempotent for the same governed binding: re-activating an
    already-active coordinator returns the existing handle without duplicate
    activation. A coordinator parked at the user gate resumes when a trusted
    view carries approval (the coordinator itself can never set approval).
    Anything materially incompatible fails closed.
    """
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(plan_view, MilestonePlanView):
        raise TypeError(f"plan_view must be MilestonePlanView, got {type(plan_view).__name__}")
    session_ref = _require_non_empty_str(
        origin_task_main_session_ref, "origin_task_main_session_ref"
    )
    _require_durable_dispatcher(execution_dispatcher)
    if completion_coordinator is not None and not isinstance(
        completion_coordinator, DurableCompletionCoordinator
    ):
        raise TypeError(
            "completion_coordinator must be DurableCompletionCoordinator or None, "
            f"got {type(completion_coordinator).__name__}"
        )
    executor_id = _require_non_empty_str(executor_id, "executor_id")
    project_id = _require_non_empty_str(project_id, "project_id")
    resolved_id = (
        _require_non_empty_str(coordinator_id, "coordinator_id")
        if coordinator_id is not None
        else _default_coordinator_id(project_id=project_id, milestone_id=plan_view.milestone_id)
    )

    existing = store.get(resolved_id)
    if existing is not None:
        if (
            existing.project_id != project_id
            or existing.executor_id != executor_id
            or existing.origin_task_main_session_ref != session_ref
        ):
            raise SessionRecoveryRequiredError(resolved_id)
        _check_live_binding(existing, plan_view)
        handle = TaskMainCoordinator(
            store=store,
            coordinator_id=resolved_id,
            execution_dispatcher=execution_dispatcher,
            completion_coordinator=completion_coordinator,
            executor_id=executor_id,
            project_id=project_id,
        )
        if existing.status == CoordinatorStatus.CLOSED:
            raise CoordinatorBindingError(
                f"coordinator {resolved_id!r} is CLOSED; a new Milestone activation "
                "requires explicit approval, never silent reuse"
            )
        if existing.status == CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
            if plan_view.user_gate_blocked:
                handle._cas({"status": CoordinatorStatus.USER_GATE_REQUIRED.value})
            else:
                handle._cas({"status": CoordinatorStatus.ACTIVE.value})
            return handle
        if existing.status == CoordinatorStatus.USER_GATE_REQUIRED:
            if plan_view.user_gate_blocked:
                return handle
            handle._cas(
                {
                    "status": CoordinatorStatus.ACTIVE.value,
                    "user_approval_satisfied": True,
                }
            )
            return handle
        return handle

    status = (
        CoordinatorStatus.ACTIVE
        if not plan_view.user_gate_blocked
        else CoordinatorStatus.USER_GATE_REQUIRED
    )
    fresh = TaskMainCoordinatorState(
        coordinator_id=resolved_id,
        plan_authority=plan_view.plan_authority,
        plan_digest=plan_view.plan_digest,
        plan_source_revision=plan_view.plan_source_revision,
        milestone_id=plan_view.milestone_id,
        entry_base=plan_view.entry_base,
        origin_task_main_session_ref=session_ref,
        project_id=project_id,
        executor_id=executor_id,
        user_approval_satisfied=plan_view.milestone_user_approval_satisfied and not plan_view.plan_amendment_required,
        status=status,
        work_items=tuple(sorted(plan_view.graph.work_items)),
        dependencies=tuple(sorted(plan_view.graph.dependencies)),
        wi_status={wi: WorkItemCoordinatorStatus.PENDING.value for wi in plan_view.graph.work_items},
        bindings={},
    )
    store.create(fresh)
    return TaskMainCoordinator(
        store=store,
        coordinator_id=resolved_id,
        execution_dispatcher=execution_dispatcher,
        completion_coordinator=completion_coordinator,
        executor_id=executor_id,
        project_id=project_id,
    )


def recover_coordinator(
    *,
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    execution_dispatcher: ExecutionDispatcher,
    completion_coordinator: DurableCompletionCoordinator | None = None,
    session_available: bool,
) -> TaskMainCoordinator:
    """Recover a durable coordinator after restart (bounded, no loop).

    Reconciles live Plan authority + accepted approval + durable coordinator
    state + M2 execution truth. Stale authority, a missing exact session, or
    lost M2 execution truth all fail closed — never silent continuation.
    """
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    _require_non_empty_str(coordinator_id, "coordinator_id")
    execution_store = _require_durable_dispatcher(execution_dispatcher)
    session_available = _require_strict_bool(session_available, "session_available")

    state = store.get(coordinator_id)
    if state is None:
        raise CoordinatorNotFoundError(coordinator_id)
    _check_live_binding(state, live_plan_view)

    handle = TaskMainCoordinator(
        store=store,
        coordinator_id=coordinator_id,
        execution_dispatcher=execution_dispatcher,
        completion_coordinator=completion_coordinator,
        executor_id=state.executor_id,
        project_id=state.project_id,
    )

    if not session_available:
        if state.status != CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
            handle._cas({"status": CoordinatorStatus.SESSION_RECOVERY_REQUIRED.value})
        raise SessionRecoveryRequiredError(coordinator_id)

    # Reconcile M2 execution truth for every active binding: the durable
    # execution record must still exist, otherwise recovery fails closed
    # instead of guessing dispatch state.
    for work_item_id, entry in state.bindings.items():
        if state.wi_status.get(work_item_id) != WorkItemCoordinatorStatus.ACTIVE.value:
            continue
        if execution_store.get(entry["canonical_task_id"]) is None:
            raise CoordinatorBindingError(
                f"active Work Item {work_item_id!r} lost durable M2 execution truth for "
                f"{entry['canonical_task_id']!r} across restart"
            )

    # Reconcile lifecycle: session back + gate clear -> ACTIVE; live gate ->
    # USER_GATE_REQUIRED; CLOSED stays closed.
    if state.status == CoordinatorStatus.SESSION_RECOVERY_REQUIRED:
        if live_plan_view.user_gate_blocked:
            handle._cas({"status": CoordinatorStatus.USER_GATE_REQUIRED.value})
        else:
            handle._cas({"status": CoordinatorStatus.ACTIVE.value})
    elif state.status == CoordinatorStatus.ACTIVE and live_plan_view.user_gate_blocked:
        handle._cas({"status": CoordinatorStatus.USER_GATE_REQUIRED.value})
    elif state.status == CoordinatorStatus.USER_GATE_REQUIRED and not live_plan_view.user_gate_blocked:
        handle._cas(
            {"status": CoordinatorStatus.ACTIVE.value, "user_approval_satisfied": True}
        )
    return handle


def build_work_item_handoff_ref(work_item_id: str) -> SemanticReference:
    """Bounded semantic reference helper for test/governed handoff construction."""
    return SemanticReference(ref=_require_non_empty_str(work_item_id, "work_item_id", max_length=128))


__all__ = [
    "CODEX_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH",
    "COORDINATOR_DISPATCH_ATTEMPT",
    "COORDINATOR_STATE_IS_PLAN_AUTHORITY",
    "DAG_PROGRESSION_DETERMINISTIC",
    "DETERMINISTIC_RUNTIME_REINTERPRETS_PLAN_PROSE",
    "MANUAL_PER_WORK_SCOPE_INJECTION_REQUIRED",
    "NEXT_MILESTONE_GUARD",
    "OPERATOR_REFRESH_REQUIRED_BETWEEN_WORK_ITEMS",
    "OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH",
    "PERSISTENCE_REQUIRES_FOREVER_PROCESS",
    "PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED",
    "SESSION_GATE_REASON",
    "TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY",
    "TASK_MAIN_CAN_CROSS_USER_GATE",
    "TASK_MAIN_CAN_SET_USER_APPROVAL",
    "TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION",
    "TASK_MAIN_RAW_SHELL_REQUIRED",
    "TASK_MAIN_RESTART_REQUIRES_OPERATOR_WORK_SEMANTICS_REINJECTION",
    "TASK_MAIN_SEMANTIC_LAYER_PRODUCES_WORK_PROJECTION",
    "TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED",
    "USER_GATE_REASON",
    "WORK_PROJECTION_BOUND_TO_TRUSTED_PLAN_IDENTITY",
    "WORK_PROJECTION_BOUND_TO_WORK_ITEM",
    "WORK_PROJECTION_DURABLE",
    "WORK_SEMANTIC_PROJECTION_IS_PLAN_AUTHORITY",
    "CoordinatorBindingError",
    "CoordinatorRuntimeError",
    "DispatchReport",
    "DispatchedWork",
    "MilestonePlanView",
    "PlanDriftError",
    "SessionRecoveryRequiredError",
    "TaskMainCoordinator",
    "TaskMainCoordinatorError",
    "activate_milestone",
    "build_work_item_handoff_ref",
    "commit_task_main_work_projection",
    "dispatch_identity_for",
    "evaluate_ready_work_items",
    "recover_coordinator",
    "resolve_task_main_work_handoff",
]
