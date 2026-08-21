"""CanonicalTaskState enum and state transition machine (M5-1).

Defines canonical non-Hermes state vocabulary:
CREATED, ACCEPTED, QUEUED, RUNNING, WAITING, COMPLETED, FAILED, CANCELLED, UNKNOWN.
Only COMPLETED, FAILED, CANCELLED are terminal.
UNKNOWN is non-terminal and requires reconciliation (never silent completion).
"""

from __future__ import annotations

from enum import Enum


class CanonicalTaskState(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATES

    def can_transition_to(self, target: CanonicalTaskState | str) -> bool:
        target_state = parse_state(target)
        return target_state in ALLOWED_STATE_TRANSITIONS.get(self, ())


CANONICAL_TASK_STATES: tuple[CanonicalTaskState, ...] = (
    CanonicalTaskState.CREATED,
    CanonicalTaskState.ACCEPTED,
    CanonicalTaskState.QUEUED,
    CanonicalTaskState.RUNNING,
    CanonicalTaskState.WAITING,
    CanonicalTaskState.COMPLETED,
    CanonicalTaskState.FAILED,
    CanonicalTaskState.CANCELLED,
    CanonicalTaskState.UNKNOWN,
)

CANONICAL_TASK_STATE_SET: frozenset[str] = frozenset(s.value for s in CANONICAL_TASK_STATES)

TERMINAL_STATES: frozenset[CanonicalTaskState] = frozenset({
    CanonicalTaskState.COMPLETED,
    CanonicalTaskState.FAILED,
    CanonicalTaskState.CANCELLED,
})

TERMINAL_STATE_STRINGS: frozenset[str] = frozenset(s.value for s in TERMINAL_STATES)

ALLOWED_STATE_TRANSITIONS: dict[CanonicalTaskState, tuple[CanonicalTaskState, ...]] = {
    CanonicalTaskState.CREATED: (
        CanonicalTaskState.ACCEPTED,
        CanonicalTaskState.FAILED,
    ),
    CanonicalTaskState.ACCEPTED: (
        CanonicalTaskState.QUEUED,
        CanonicalTaskState.RUNNING,
        CanonicalTaskState.UNKNOWN,
    ),
    CanonicalTaskState.QUEUED: (
        CanonicalTaskState.RUNNING,
        CanonicalTaskState.CANCELLED,
    ),
    CanonicalTaskState.RUNNING: (
        CanonicalTaskState.WAITING,
        CanonicalTaskState.COMPLETED,
        CanonicalTaskState.FAILED,
        CanonicalTaskState.CANCELLED,
        CanonicalTaskState.UNKNOWN,
    ),
    CanonicalTaskState.WAITING: (
        CanonicalTaskState.RUNNING,
        CanonicalTaskState.CANCELLED,
    ),
    CanonicalTaskState.COMPLETED: (),
    CanonicalTaskState.FAILED: (),
    CanonicalTaskState.CANCELLED: (),
    CanonicalTaskState.UNKNOWN: (
        CanonicalTaskState.COMPLETED,
        CanonicalTaskState.FAILED,
        CanonicalTaskState.RUNNING,
    ),
}


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.code = "TASK_STATE_UNKNOWN"
        super().__init__(
            f"Invalid state transition from {from_state!r} to {to_state!r}"
        )


def parse_state(state: CanonicalTaskState | str) -> CanonicalTaskState:
    if isinstance(state, CanonicalTaskState):
        return state
    if not isinstance(state, str):
        raise TypeError(f"state must be CanonicalTaskState or str, got {type(state).__name__}")
    normalized = state.strip().upper()
    try:
        return CanonicalTaskState(normalized)
    except ValueError:
        raise ValueError(
            f"Unknown CanonicalTaskState: {state!r}. Must be one of {sorted(CANONICAL_TASK_STATE_SET)}"
        )


def is_terminal(state: CanonicalTaskState | str) -> bool:
    parsed = parse_state(state)
    return parsed in TERMINAL_STATES


def can_transition(from_state: CanonicalTaskState | str, to_state: CanonicalTaskState | str) -> bool:
    parsed_from = parse_state(from_state)
    parsed_to = parse_state(to_state)
    return parsed_to in ALLOWED_STATE_TRANSITIONS.get(parsed_from, ())


def validate_transition(from_state: CanonicalTaskState | str, to_state: CanonicalTaskState | str) -> None:
    parsed_from = parse_state(from_state)
    parsed_to = parse_state(to_state)
    if not can_transition(parsed_from, parsed_to):
        raise InvalidStateTransitionError(parsed_from.value, parsed_to.value)


def allowed_transitions(from_state: CanonicalTaskState | str) -> tuple[CanonicalTaskState, ...]:
    parsed_from = parse_state(from_state)
    return ALLOWED_STATE_TRANSITIONS.get(parsed_from, ())
