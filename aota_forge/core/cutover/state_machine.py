"""M3-B13 Cutover State Machine.

Scope (Issue #9, Lane M3-B13):
- Deterministic finite state machine tracking cutover lifecycle.
- Distinguishes verified commit, precommit abort, committed-but-verification-failed,
  authorization rejection, and precondition rejection.
- Enforces strict unidirectional and valid transition rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aota_forge.core.cutover.model import CutoverState


class CutoverStateTransitionError(RuntimeError):
    """Raised when an illegal state transition is attempted."""


@dataclass(frozen=True)
class StateTransitionRecord:
    """Audit log entry for state transitions."""

    from_state: CutoverState
    to_state: CutoverState
    reason: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


class CutoverStateMachine:
    """Deterministic state machine governing cutover progression."""

    VALID_TRANSITIONS: dict[CutoverState, frozenset[CutoverState]] = {
        CutoverState.REQUESTED: frozenset(
            {
                CutoverState.PRECONDITION_CHECKED,
                CutoverState.REJECTED,
            }
        ),
        CutoverState.PRECONDITION_CHECKED: frozenset(
            {
                CutoverState.STAGED,
                CutoverState.REJECTED,
                CutoverState.ABORTED_PRECOMMIT,
            }
        ),
        CutoverState.STAGED: frozenset(
            {
                CutoverState.COMMITTED,
                CutoverState.ABORTED_PRECOMMIT,
            }
        ),
        CutoverState.COMMITTED: frozenset(
            {
                CutoverState.POSTCOMMIT_VERIFIED,
                CutoverState.COMMITTED_VERIFICATION_FAILED,
            }
        ),
        CutoverState.POSTCOMMIT_VERIFIED: frozenset(),
        CutoverState.ABORTED_PRECOMMIT: frozenset(),
        CutoverState.COMMITTED_VERIFICATION_FAILED: frozenset(),
        CutoverState.REJECTED: frozenset(),
    }

    def __init__(self, initial_state: CutoverState = CutoverState.REQUESTED) -> None:
        self._current_state = initial_state
        self._history: list[StateTransitionRecord] = []

    @property
    def current_state(self) -> CutoverState:
        return self._current_state

    @property
    def is_terminal(self) -> bool:
        return len(self.VALID_TRANSITIONS.get(self._current_state, frozenset())) == 0

    @property
    def history(self) -> tuple[StateTransitionRecord, ...]:
        return tuple(self._history)

    def transition_to(self, target_state: CutoverState, reason: str = "") -> CutoverState:
        """Execute a state transition if valid, or raise CutoverStateTransitionError."""
        allowed = self.VALID_TRANSITIONS.get(self._current_state, frozenset())
        if target_state not in allowed:
            raise CutoverStateTransitionError(
                f"Illegal state transition from {self._current_state.value} to {target_state.value}. "
                f"Allowed target states: {[s.value for s in allowed]}."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        rec = StateTransitionRecord(
            from_state=self._current_state,
            to_state=target_state,
            reason=reason,
            timestamp=now_iso,
        )
        self._history.append(rec)
        self._current_state = target_state
        return self._current_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_state": self._current_state.value,
            "is_terminal": self.is_terminal,
            "history": [h.to_dict() for h in self._history],
        }


__all__ = [
    "CutoverStateTransitionError",
    "StateTransitionRecord",
    "CutoverStateMachine",
]
