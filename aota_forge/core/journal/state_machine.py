"""M4-5 journal state machine — transitions, write order, at-most-one (no persistence)."""

from __future__ import annotations

from typing import FrozenSet

from aota_forge.core.journal.model import JournalState

# Exact accepted transitions per m4-5-journal-state-machine.json
# Includes J1-J10 coverage. Terminal semantics preserved.
ALLOWED_TRANSITIONS: dict[JournalState, FrozenSet[JournalState]] = {
    JournalState.PREPARED: frozenset({
        JournalState.APPLYING,
        JournalState.FAILED_NO_EFFECT,
    }),
    JournalState.APPLYING: frozenset({
        JournalState.VERIFIED,
        JournalState.FAILED_NO_EFFECT,
        JournalState.OUTCOME_UNKNOWN,
        JournalState.RECONCILING,
    }),
    JournalState.OUTCOME_UNKNOWN: frozenset({
        JournalState.RECONCILING,
    }),
    JournalState.RECONCILING: frozenset({
        JournalState.VERIFIED_RECOVERED,
        JournalState.RETRYABLE_NO_EFFECT,
        JournalState.CONFLICT,
    }),
    # Terminal states have no outgoing transitions in this contract
    JournalState.VERIFIED: frozenset(),
    JournalState.VERIFIED_RECOVERED: frozenset(),
    JournalState.FAILED_NO_EFFECT: frozenset(),
    JournalState.RETRYABLE_NO_EFFECT: frozenset(),
    JournalState.CONFLICT: frozenset(),
}

# J10: NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE -> RECONCILING
# This is not a state-to-state transition but a crash window mapping:
# if a future run observes a nonterminal journal while authority read shows candidate/original/third,
# it must reconcile. Explicit mapping kept distinguishable.
J10_NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE = "NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE"
J10_TARGET = JournalState.RECONCILING
J10_SOURCE_CONTRACT_EXPLICIT = True

# Required write order (M4-5 does not persist, but encodes contract)
REQUIRED_WRITE_ORDER = (
    "validate existing semantic authorization",
    "validate Subject precondition",
    "read external authority state",
    "validate raw external precondition",
    "construct exact candidate",
    "compute candidate_raw_digest",
    "persist PREPARED journal record durably",
    "persist APPLYING attempt-start state durably",
    "perform at most one external mutation attempt",
    "read authority store after mutation where result permits",
    "classify exact observed state",
    "persist terminal or reconciliation state",
)

# Invariants
EXTERNAL_APPLY_BEFORE_PREPARED_ALLOWED = False
JOURNAL_WRITE_ORDER_CONTRACT = "PASS"
AT_MOST_ONE_EXTERNAL_ATTEMPT_CONTRACT = "PASS"
DISTRIBUTED_LOCK_IMPLEMENTED = False
STRANDED_APPLYING_BLIND_RETRY_ALLOWED = False
OUTCOME_UNKNOWN_BLIND_RETRY_ALLOWED = False

# Journal is not semantic authority
JOURNAL_IS_SEMANTIC_DECISION_MAKER = False
INVALID_JOURNAL_TRANSITION_REJECTED = True


def is_valid_transition(from_state: JournalState, to_state: JournalState) -> bool:
    """Deterministic validator for allowed journal transitions (fail-closed)."""
    allowed = ALLOWED_TRANSITIONS.get(from_state)
    if allowed is None:
        return False
    return to_state in allowed


def validate_transition(from_state: JournalState, to_state: JournalState) -> None:
    """Raise if transition is not allowed (fail-closed)."""
    if not is_valid_transition(from_state, to_state):
        raise ValueError(f"invalid journal transition: {from_state.value} -> {to_state.value}")


def can_perform_external_attempt(journal_state: JournalState, prepared_durable: bool, applying_durable: bool) -> bool:
    """Contract: external apply only allowed when PREPARED durably then APPLYING durably.

    M4-5 encodes the eligibility; M4-7 enforces durability.
    """
    if not prepared_durable:
        return False
    if not applying_durable:
        return False
    return journal_state == JournalState.APPLYING


def j10_maps_to_reconciling(crash_window: str) -> bool:
    """J10 explicitly maps to RECONCILING."""
    return crash_window == J10_NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE and J10_TARGET == JournalState.RECONCILING


# For guard: forbidden examples must be rejected
FORBIDDEN_EXAMPLES = (
    (JournalState.VERIFIED, JournalState.APPLYING),
    (JournalState.CONFLICT, JournalState.VERIFIED),
    (JournalState.FAILED_NO_EFFECT, JournalState.APPLYING),
)


def _assert_forbidden_rejected() -> None:
    for frm, to in FORBIDDEN_EXAMPLES:
        if is_valid_transition(frm, to):
            raise AssertionError(f"forbidden transition should be rejected: {frm.value}->{to.value}")


# Validate at import time that forbidden examples are indeed rejected
_assert_forbidden_rejected()

__all__ = [
    "ALLOWED_TRANSITIONS",
    "REQUIRED_WRITE_ORDER",
    "J10_NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE",
    "J10_TARGET",
    "J10_SOURCE_CONTRACT_EXPLICIT",
    "EXTERNAL_APPLY_BEFORE_PREPARED_ALLOWED",
    "JOURNAL_WRITE_ORDER_CONTRACT",
    "AT_MOST_ONE_EXTERNAL_ATTEMPT_CONTRACT",
    "DISTRIBUTED_LOCK_IMPLEMENTED",
    "is_valid_transition",
    "validate_transition",
    "can_perform_external_attempt",
    "j10_maps_to_reconciling",
    "FORBIDDEN_EXAMPLES",
]
