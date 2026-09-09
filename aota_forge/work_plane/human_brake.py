"""Human Brake runtime progression (M2/W2 runtime).

W1 already made Brake state durable (coordinator human_brake). This module
wires the decision progression for four distinct outcomes (never collapsed
into one generic blocked flag):

    NEEDS_INPUT:
        Before interrupting the user: can a trusted source / Analyst /
        Steward resolve it? If yes USER_INTERRUPT_REQUIRED=no and the
        outcome is a HUMAN_CHECKPOINT (bounded decision context returned
        by task-main: decision, reason, 1-3 options when applicable,
        material consequence, recommendation when supportable, resume
        condition).
    HUMAN_CHECKPOINT_REQUIRED / USER_DECISION_REQUIRED:
        requires a user decision; task-main cannot auto-select.
    USER_GATE_REQUIRED:
        formal governance gate; task-main cannot set user approval and
        cannot retry through the gate. Survives restart.
    BLOCKED:
        no automatic scope/authority bypass; not auto-retried as a
        preference problem.

Brake scope (affected_work | dependent_subgraph | whole_milestone):
unrelated Work may continue only when dependencies / shared-state /
architecture / authority are genuinely unaffected. Architecture /
acceptance / shared-state decisions normally stop the affected Milestone
graph broadly enough to remain safe.

Hard:

    HUMAN_BRAKE_RUNTIME_WIRED=yes
    NEEDS_INPUT/HUMAN_CHECKPOINT/USER_GATE/BLOCKED_RUNTIME_WIRED=yes
    TASK_MAIN_CAN_AUTO_SELECT_WHEN_CHECKPOINT_REQUIRED=no
    TASK_MAIN_CAN_SET_USER_APPROVAL=no
    TASK_MAIN_CAN_RETRY_THROUGH_USER_GATE=no
    HUMAN_BRAKE_DOES_NOT_ALWAYS_STOP_WHOLE_MILESTONE=yes
    BRAKE_SCOPE_RUNTIME_WIRED=yes

Reuses existing contracts only: coordinator HUMAN_BRAKE_STATES/SCOPES
vocabulary, MilestoneWorkItemGraph (scope reachability).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

from aota_forge.runtime.task_main.coordinator_state import HUMAN_BRAKE_SCOPES, HUMAN_BRAKE_STATES
from aota_forge.work_plane.progression import MilestoneWorkItemGraph

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

HUMAN_BRAKE_RUNTIME_WIRED = True
NEEDS_INPUT_RUNTIME_WIRED = True
HUMAN_CHECKPOINT_RUNTIME_WIRED = True
USER_GATE_RUNTIME_WIRED = True
BLOCKED_RUNTIME_WIRED = True
BRAKE_SCOPE_RUNTIME_WIRED = True
HUMAN_BRAKE_DOES_NOT_ALWAYS_STOP_WHOLE_MILESTONE = True

TASK_MAIN_CAN_AUTO_SELECT_WHEN_CHECKPOINT_REQUIRED = False
TASK_MAIN_CAN_SET_USER_APPROVAL = False
TASK_MAIN_CAN_RETRY_THROUGH_USER_GATE = False
TASK_MAIN_CAN_CROSS_USER_GATE = False
BLOCKED_AUTO_RETRY_AS_PREFERENCE_DENIED = True

USER_INTERRUPT_DEFAULT_FOR_NEEDS_INPUT = False


@unique
class BrakeState(str, Enum):
    NONE = "NONE"
    NEEDS_INPUT = "NEEDS_INPUT"
    HUMAN_CHECKPOINT_REQUIRED = "HUMAN_CHECKPOINT_REQUIRED"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
    USER_GATE_REQUIRED = "USER_GATE_REQUIRED"
    BLOCKED = "BLOCKED"


@unique
class BrakeScope(str, Enum):
    NONE = "NONE"
    AFFECTED_WORK = "AFFECTED_WORK"
    DEPENDENT_SUBGRAPH = "DEPENDENT_SUBGRAPH"
    WHOLE_MILESTONE = "WHOLE_MILESTONE"


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 1024) -> str:
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


def parse_brake_state(value: Any) -> BrakeState:
    if isinstance(value, BrakeState):
        return value
    if isinstance(value, str) and type(value) is str:
        if value not in HUMAN_BRAKE_STATES:
            raise ValueError(f"Unknown brake state: {value!r}")
        try:
            return BrakeState(value)
        except ValueError as exc:
            raise ValueError(f"Unknown brake state: {value!r}") from exc
    raise TypeError(f"brake state must be BrakeState or str, got {type(value).__name__}")


def parse_brake_scope(value: Any) -> BrakeScope:
    if isinstance(value, BrakeScope):
        return value
    if isinstance(value, str) and type(value) is str:
        if value not in HUMAN_BRAKE_SCOPES:
            raise ValueError(f"Unknown brake scope: {value!r}")
        try:
            return BrakeScope(value)
        except ValueError as exc:
            raise ValueError(f"Unknown brake scope: {value!r}") from exc
    raise TypeError(f"brake scope must be BrakeScope or str, got {type(value).__name__}")


@dataclass(frozen=True)
class NeedsInputTriage:
    """NEEDS_INPUT triage: prefer trusted resolution over user interrupt."""

    resolvable_by_trusted_source: bool = False
    resolvable_by_analyst: bool = False
    resolvable_by_steward: bool = False

    def __post_init__(self) -> None:
        _require_strict_bool(self.resolvable_by_trusted_source, "resolvable_by_trusted_source")
        _require_strict_bool(self.resolvable_by_analyst, "resolvable_by_analyst")
        _require_strict_bool(self.resolvable_by_steward, "resolvable_by_steward")

    @property
    def user_interrupt_required(self) -> bool:
        """USER_INTERRUPT_REQUIRED=no when any trusted Role/source resolves it."""
        return not (self.resolvable_by_trusted_source or self.resolvable_by_analyst or self.resolvable_by_steward)

    @property
    def resolution_lane(self) -> str:
        if self.resolvable_by_trusted_source:
            return "TRUSTED_SOURCE"
        if self.resolvable_by_analyst:
            return "ANALYST"
        if self.resolvable_by_steward:
            return "STEWARD"
        return "USER"


@dataclass(frozen=True)
class CheckpointDecisionContext:
    """Bounded decision context task-main returns at a human checkpoint.

    Carries decision + reason + 1-3 options when applicable + material
    consequence + recommendation when supportable + resume condition.
    task-main cannot auto-select (the user decides).
    """

    decision: str
    reason: str
    options: tuple[str, ...] = ()
    material_consequence: str | None = None
    recommendation: str | None = None
    resume_condition: str | None = None

    MAX_OPTIONS = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", _require_non_empty_str(self.decision, "decision"))
        object.__setattr__(self, "reason", _require_non_empty_str(self.reason, "reason"))
        options = tuple(self.options)
        if len(options) > self.MAX_OPTIONS:
            raise ValueError(f"options count ({len(options)}) exceeds maximum {self.MAX_OPTIONS}")
        for opt in options:
            if not isinstance(opt, str) or not opt.strip():
                raise ValueError("options must contain non-empty strings")
        object.__setattr__(self, "options", options)
        if self.material_consequence is not None:
            object.__setattr__(
                self,
                "material_consequence",
                _require_non_empty_str(self.material_consequence, "material_consequence"),
            )
        if self.recommendation is not None:
            object.__setattr__(
                self, "recommendation", _require_non_empty_str(self.recommendation, "recommendation")
            )
        if self.resume_condition is not None:
            object.__setattr__(
                self, "resume_condition", _require_non_empty_str(self.resume_condition, "resume_condition")
            )

    def auto_select(self, index: int = 0) -> str:
        """Always denied: task-main cannot auto-select when a checkpoint is required."""
        raise ValueError(
            "TASK_MAIN_CAN_AUTO_SELECT_WHEN_CHECKPOINT_REQUIRED=no: "
            "a human checkpoint requires an explicit user decision"
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "options": list(self.options),
        }
        if self.material_consequence is not None:
            d["material_consequence"] = self.material_consequence
        if self.recommendation is not None:
            d["recommendation"] = self.recommendation
        if self.resume_condition is not None:
            d["resume_condition"] = self.resume_condition
        return d


def compute_brake_scope(
    graph: MilestoneWorkItemGraph,
    affected_work: Sequence[str],
    *,
    architecture_or_acceptance_impact: bool = False,
    shared_state_impact: bool = False,
    authority_impact: bool = False,
) -> BrakeScope:
    """Pure brake-scope policy over the Milestone DAG.

    - No affected work -> NONE (no brake).
    - Architecture/acceptance/shared-state/authority impact -> WHOLE_MILESTONE
      (stop broadly enough to remain safe).
    - Affected work whose dependency closure (predecessors + successors)
      reaches further work -> DEPENDENT_SUBGRAPH.
    - Otherwise -> AFFECTED_WORK (unrelated Work may continue).
    """
    if not isinstance(graph, MilestoneWorkItemGraph):
        raise TypeError(f"graph must be MilestoneWorkItemGraph, got {type(graph).__name__}")
    affected = tuple(affected_work)
    for work in affected:
        if work not in graph.work_items:
            raise ValueError(f"affected Work Item unknown to Milestone DAG: {work!r}")
    if not affected:
        return BrakeScope.NONE
    if architecture_or_acceptance_impact or shared_state_impact or authority_impact:
        return BrakeScope.WHOLE_MILESTONE
    affected_set = set(affected)
    closure: set[str] = set(affected_set)
    for work in affected_set:
        closure.update(graph.predecessors_of(work))
        closure.update(graph.successors_of(work))
    if closure - affected_set:
        return BrakeScope.DEPENDENT_SUBGRAPH
    return BrakeScope.AFFECTED_WORK


def brake_halts_work(*, scope: BrakeScope, work_item_id: str, affected_work: Sequence[str]) -> bool:
    """Whether a brake at the given scope halts a specific Work Item.

    AFFECTED_WORK halts only affected Work; DEPENDENT_SUBGRAPH halts the
    affected closure (computed by the caller via the DAG); WHOLE_MILESTONE
    halts everything. Unrelated Work continues only when genuinely
    unaffected (caller must pass the true closure for subgraphs).
    """
    if not isinstance(scope, BrakeScope):
        raise TypeError(f"scope must be BrakeScope, got {type(scope).__name__}")
    affected = tuple(affected_work)
    if scope is BrakeScope.NONE:
        return False
    if scope is BrakeScope.WHOLE_MILESTONE:
        return True
    return work_item_id in affected


def assert_user_gate_survives_restart(*, status_before: str, status_after: str) -> None:
    """USER_GATE survives restart: a gated coordinator cannot come back ACTIVE
    without trusted approval, and task-main can never cross it by retry."""
    if status_before == BrakeState.USER_GATE_REQUIRED.value and status_after == "ACTIVE":
        raise ValueError(
            "USER_GATE_REQUIRED cannot transition to ACTIVE across restart without "
            "trusted user approval (TASK_MAIN_CAN_RETRY_THROUGH_USER_GATE=no)"
        )


__all__ = [
    "HUMAN_BRAKE_RUNTIME_WIRED",
    "NEEDS_INPUT_RUNTIME_WIRED",
    "HUMAN_CHECKPOINT_RUNTIME_WIRED",
    "USER_GATE_RUNTIME_WIRED",
    "BLOCKED_RUNTIME_WIRED",
    "BRAKE_SCOPE_RUNTIME_WIRED",
    "HUMAN_BRAKE_DOES_NOT_ALWAYS_STOP_WHOLE_MILESTONE",
    "TASK_MAIN_CAN_AUTO_SELECT_WHEN_CHECKPOINT_REQUIRED",
    "TASK_MAIN_CAN_SET_USER_APPROVAL",
    "TASK_MAIN_CAN_RETRY_THROUGH_USER_GATE",
    "TASK_MAIN_CAN_CROSS_USER_GATE",
    "BLOCKED_AUTO_RETRY_AS_PREFERENCE_DENIED",
    "USER_INTERRUPT_DEFAULT_FOR_NEEDS_INPUT",
    "BrakeState",
    "BrakeScope",
    "parse_brake_state",
    "parse_brake_scope",
    "NeedsInputTriage",
    "CheckpointDecisionContext",
    "compute_brake_scope",
    "brake_halts_work",
    "assert_user_gate_survives_restart",
]
