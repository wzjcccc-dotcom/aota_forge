"""One-shot Role lifecycle vs logical long-lived task-main (M2/W2 runtime).

M1 frozen (see #44 M1/W1, M1/W3):

    TASK_MAIN_IS_ONLY_LONG_LIVED_AGENT=yes
    OTHER_ROLES_ARE_ONE_SHOT_CONTEXT_OFFLOADERS=yes
    ONE_SHOT_ROLE_PERSISTS_AFTER_RESULT=no

Runtime semantics wired here (orchestration semantics, never arbitrary
session timeouts):

    task-main:
        logical lifecycle persists across Work executions,
        context rollover and restart (durable coordinator truth).
    analyst / coder / reviewer / project-steward:
        one TaskHandoff -> bounded execution -> Result -> terminate.

Every one-shot Role converges on:

    TaskHandoff -> Role -> Result/Card -> durable evidence
    -> task-main exact/logical re-entry -> Role terminates.

Hard invariants:

    TASK_MAIN_LONG_LIVED_LOGICAL_LIFECYCLE_WIRED=yes
    ANALYST/CODER/REVIEWER/PROJECT_STEWARD_ONE_SHOT_LIFECYCLE_WIRED=yes
    ONE_SHOT_ROLE_PERSISTS_AFTER_RESULT=no
    RAW_ROLE_TRANSCRIPT_REQUIRED_BY_TASK_MAIN=no
    TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT=no
    EXACT_LOGICAL_REENTRY_PRESERVED=yes

Reuses existing contracts only (no second lifecycle engine, no new
workflow database):

    AgentWorkRole, TaskHandoff, CommonResultEnvelope / WorkerResultCard,
    TaskMainCoordinatorState durable truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

TASK_MAIN_LONG_LIVED_LOGICAL_LIFECYCLE_WIRED = True
ANALYST_ONE_SHOT_LIFECYCLE_WIRED = True
CODER_ONE_SHOT_LIFECYCLE_WIRED = True
REVIEWER_ONE_SHOT_LIFECYCLE_WIRED = True
PROJECT_STEWARD_ONE_SHOT_LIFECYCLE_WIRED = True

ONE_SHOT_ROLE_PERSISTS_AFTER_RESULT = False
ONE_SHOT_ROLE_OWNS_LONG_TERM_PROJECT_STATE = False
ONE_SHOT_ROLE_CAN_MUTATE_TASK_MAIN_CONTEXT_DIRECTLY = False
ONE_SHOT_ROLE_RESULT_MUST_BE_BOUNDED = True

RAW_ROLE_TRANSCRIPT_REQUIRED_BY_TASK_MAIN = False
TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT = False
EXACT_LOGICAL_REENTRY_PRESERVED = True
CARD_FIRST_REENTRY = True

# Lifetime is orchestration semantics, never a wall-clock timeout.
LIFETIME_IS_SESSION_TIMEOUT = False
LIFETIME_IS_ORCHESTRATION_SEMANTICS = True

ONE_SHOT_ROLES: frozenset[str] = frozenset({"analyst", "coder", "reviewer", "project-steward"})
LONG_LIVED_ROLES: frozenset[str] = frozenset({"task-main"})


@unique
class RoleLifecycleKind(str, Enum):
    """Lifecycle kind per Role (frozen M1 vocabulary)."""

    LONG_LIVED_LOGICAL = "LONG_LIVED_LOGICAL"
    ONE_SHOT = "ONE_SHOT"


@unique
class OneShotSessionState(str, Enum):
    """Bounded one-shot Role session states (orchestration only)."""

    DISPATCHED = "DISPATCHED"
    EXECUTING = "EXECUTING"
    RESULT_RECORDED = "RESULT_RECORDED"
    TERMINATED = "TERMINATED"


_ONE_SHOT_SESSION_STATES: frozenset[str] = frozenset(v.value for v in OneShotSessionState)

# Allowed forward transitions only; no resurrection after TERMINATED,
# no post-result work after RESULT_RECORDED except terminate.
_ONE_SHOT_TRANSITIONS: dict[str, frozenset[str]] = {
    OneShotSessionState.DISPATCHED.value: frozenset({OneShotSessionState.EXECUTING.value}),
    OneShotSessionState.EXECUTING.value: frozenset({OneShotSessionState.RESULT_RECORDED.value}),
    OneShotSessionState.RESULT_RECORDED.value: frozenset({OneShotSessionState.TERMINATED.value}),
    OneShotSessionState.TERMINATED.value: frozenset(),
}


def lifecycle_for_role(role: AgentWorkRole | str) -> RoleLifecycleKind:
    """Return the frozen lifecycle kind for a canonical Role."""
    parsed = parse_agent_work_role(role) if not isinstance(role, AgentWorkRole) else role
    if parsed.value in LONG_LIVED_ROLES:
        return RoleLifecycleKind.LONG_LIVED_LOGICAL
    if parsed.value in ONE_SHOT_ROLES:
        return RoleLifecycleKind.ONE_SHOT
    raise ValueError(f"Unknown Role lifecycle for {parsed.value!r}")


def is_one_shot_role(role: AgentWorkRole | str) -> bool:
    return lifecycle_for_role(role) is RoleLifecycleKind.ONE_SHOT


def is_long_lived_role(role: AgentWorkRole | str) -> bool:
    return lifecycle_for_role(role) is RoleLifecycleKind.LONG_LIVED_LOGICAL


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    return stripped


@dataclass(frozen=True)
class OneShotRoleSession:
    """Durable identity for one bounded one-shot Role execution.

    Carries only orchestration identity (never transcripts, never full
    reports): which Role, which TaskHandoff digest, which task-main logical
    session it must re-enter, and its lifecycle state.

    The task-main logical session ref is opaque here; persistence lives in
    the durable coordinator (W1 foundation). This type only binds the
    one-shot session to that logical session so re-entry is exact.
    """

    role: AgentWorkRole
    handoff_digest: str
    task_main_logical_session_ref: str
    state: OneShotSessionState = OneShotSessionState.DISPATCHED
    result_digest: str | None = None

    def __post_init__(self) -> None:
        role = self.role
        if isinstance(role, AgentWorkRole):
            pass
        elif isinstance(role, str) and type(role) is str:
            object.__setattr__(self, "role", parse_agent_work_role(role))
        else:
            raise TypeError(f"role must be AgentWorkRole or str, got {type(role).__name__}")
        if not is_one_shot_role(self.role):
            raise ValueError(
                f"OneShotRoleSession requires a one-shot Role, got {self.role.value!r}; "
                "task-main is long-lived, never a one-shot session"
            )
        object.__setattr__(self, "handoff_digest", _require_non_empty_str(self.handoff_digest, "handoff_digest"))
        object.__setattr__(
            self,
            "task_main_logical_session_ref",
            _require_non_empty_str(self.task_main_logical_session_ref, "task_main_logical_session_ref"),
        )
        if isinstance(self.state, OneShotSessionState):
            pass
        elif isinstance(self.state, str) and type(self.state) is str:
            try:
                object.__setattr__(self, "state", OneShotSessionState(self.state))
            except ValueError as exc:
                raise ValueError(f"Unknown OneShotSessionState: {self.state!r}") from exc
        else:
            raise TypeError(f"state must be OneShotSessionState or str, got {type(self.state).__name__}")
        if self.result_digest is not None:
            object.__setattr__(self, "result_digest", _require_non_empty_str(self.result_digest, "result_digest"))
        # RESULT_RECORDED/TERMINATED require a result identity (card-first).
        if self.state in (OneShotSessionState.RESULT_RECORDED, OneShotSessionState.TERMINATED):
            if self.result_digest is None:
                raise ValueError(f"state {self.state.value} requires result_digest (card-first re-entry)")

    def advance(self, next_state: OneShotSessionState | str, *, result_digest: str | None = None) -> OneShotRoleSession:
        """Advance the session one forward step (pure, fail-closed)."""
        target = (
            next_state
            if isinstance(next_state, OneShotSessionState)
            else OneShotSessionState(_require_non_empty_str(next_state, "next_state"))
        )
        allowed = _ONE_SHOT_TRANSITIONS[self.state.value]
        if target.value not in allowed:
            raise ValueError(
                f"One-shot {self.role.value} session cannot transition "
                f"{self.state.value} -> {target.value}; a one-shot Role terminates after its Result"
            )
        digest = self.result_digest if result_digest is None else _require_non_empty_str(result_digest, "result_digest")
        if target in (OneShotSessionState.RESULT_RECORDED, OneShotSessionState.TERMINATED) and digest is None:
            raise ValueError(f"transition to {target.value} requires result_digest (card-first re-entry)")
        return OneShotRoleSession(
            role=self.role,
            handoff_digest=self.handoff_digest,
            task_main_logical_session_ref=self.task_main_logical_session_ref,
            state=target,
            result_digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role.value,
            "handoff_digest": self.handoff_digest,
            "task_main_logical_session_ref": self.task_main_logical_session_ref,
            "state": self.state.value,
        }
        if self.result_digest is not None:
            d["result_digest"] = self.result_digest
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OneShotRoleSession:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {"role", "handoff_digest", "task_main_logical_session_ref", "state", "result_digest"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in OneShotRoleSession: {sorted(extra)}")
        for req in ("role", "handoff_digest", "task_main_logical_session_ref", "state"):
            if req not in data:
                raise ValueError(f"Missing required field in OneShotRoleSession: {req!r}")
        return cls(
            role=parse_agent_work_role(data["role"]),
            handoff_digest=data["handoff_digest"],
            task_main_logical_session_ref=data["task_main_logical_session_ref"],
            state=data["state"],
            result_digest=data.get("result_digest"),
        )


@dataclass(frozen=True)
class TaskMainLogicalSession:
    """Logical long-lived task-main session identity.

    The logical session persists across Work executions, context rollover
    and restart. Durable progression truth lives in the coordinator (W1);
    this type only names the logical continuity so one-shot completions can
    re-enter the *same* logical session exactly.
    """

    logical_session_ref: str
    milestone_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "logical_session_ref", _require_non_empty_str(self.logical_session_ref, "logical_session_ref")
        )
        object.__setattr__(self, "milestone_id", _require_non_empty_str(self.milestone_id, "milestone_id", max_length=128))

    def survives_worker_completion(self, session: OneShotRoleSession) -> bool:
        """True when a one-shot completion re-enters this logical session.

        The logical session is unchanged by the completion; the one-shot
        session must be TERMINATED with a Result and bound to this session.
        """
        if not isinstance(session, OneShotRoleSession):
            raise TypeError(f"session must be OneShotRoleSession, got {type(session).__name__}")
        return (
            session.task_main_logical_session_ref == self.logical_session_ref
            and session.state is OneShotSessionState.TERMINATED
            and session.result_digest is not None
        )


def assert_exact_logical_reentry(
    *,
    logical_session: TaskMainLogicalSession,
    session: OneShotRoleSession,
    result_card_digest: str,
) -> None:
    """Fail closed unless a terminated one-shot Result re-enters the exact logical session.

    Card-first: the recorded result digest must equal the durable card
    digest; raw transcripts are never required.
    """
    if not isinstance(logical_session, TaskMainLogicalSession):
        raise TypeError(f"logical_session must be TaskMainLogicalSession, got {type(logical_session).__name__}")
    if not isinstance(session, OneShotRoleSession):
        raise TypeError(f"session must be OneShotRoleSession, got {type(session).__name__}")
    digest = _require_non_empty_str(result_card_digest, "result_card_digest")
    if session.state is not OneShotSessionState.TERMINATED:
        raise ValueError(
            f"one-shot {session.role.value} session must be TERMINATED before task-main re-entry, "
            f"got {session.state.value}"
        )
    if session.task_main_logical_session_ref != logical_session.logical_session_ref:
        raise ValueError("one-shot Result re-enters a foreign logical task-main session (fail closed)")
    if session.result_digest != digest:
        raise ValueError("one-shot Result digest does not match durable card digest (fail closed)")


__all__ = [
    "TASK_MAIN_LONG_LIVED_LOGICAL_LIFECYCLE_WIRED",
    "ANALYST_ONE_SHOT_LIFECYCLE_WIRED",
    "CODER_ONE_SHOT_LIFECYCLE_WIRED",
    "REVIEWER_ONE_SHOT_LIFECYCLE_WIRED",
    "PROJECT_STEWARD_ONE_SHOT_LIFECYCLE_WIRED",
    "ONE_SHOT_ROLE_PERSISTS_AFTER_RESULT",
    "ONE_SHOT_ROLE_OWNS_LONG_TERM_PROJECT_STATE",
    "ONE_SHOT_ROLE_CAN_MUTATE_TASK_MAIN_CONTEXT_DIRECTLY",
    "ONE_SHOT_ROLE_RESULT_MUST_BE_BOUNDED",
    "RAW_ROLE_TRANSCRIPT_REQUIRED_BY_TASK_MAIN",
    "TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT",
    "EXACT_LOGICAL_REENTRY_PRESERVED",
    "CARD_FIRST_REENTRY",
    "LIFETIME_IS_SESSION_TIMEOUT",
    "LIFETIME_IS_ORCHESTRATION_SEMANTICS",
    "ONE_SHOT_ROLES",
    "LONG_LIVED_ROLES",
    "RoleLifecycleKind",
    "OneShotSessionState",
    "OneShotRoleSession",
    "TaskMainLogicalSession",
    "lifecycle_for_role",
    "is_one_shot_role",
    "is_long_lived_role",
    "assert_exact_logical_reentry",
]
