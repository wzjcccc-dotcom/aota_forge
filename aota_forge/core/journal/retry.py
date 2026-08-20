"""M4-5 retry authorization contract — fresh authorization required, blind reuse denied."""

from __future__ import annotations

from aota_forge.core.journal.model import JournalState

# Contracts
UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED = False
UNKNOWN_OUTCOME_BLIND_LEASE_REUSE = False
RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION = False
RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE = False
FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY = True
UNKNOWN_OUTCOME_CONTRACT = "PASS"

# Outcome unknown semantics: cannot be classified, not success, not failure, not retry permission
UNKNOWN_IS_SUCCESS = False
UNKNOWN_IS_FAILURE = False
UNKNOWN_IS_AUTO_RETRY_PERMISSION = False

# Semantic rollback denied
SEMANTIC_ROLLBACK_ALLOWED = False


def is_retry_allowed(
    *,
    current_state: JournalState,
    has_fresh_authorization: bool,
    has_fresh_subject_precondition: bool,
    has_fresh_raw_authority_precondition: bool,
    has_new_bounded_lease: bool,
    is_outcome_unknown: bool = False,
) -> bool:
    """Contract rule: retry after RETRYABLE_NO_EFFECT is distinct from authorization to retry.

    - UNKNOWN_OUTCOME blind retry is never allowed.
    - RETRYABLE_NO_EFFECT requires fresh authorization + Subject + raw preconditions + new bounded lease.
    - Same logic applies to stranded APPLYING after reconciliation to RETRYABLE.

    Returns True only if all fresh bindings are present.
    """
    if is_outcome_unknown or current_state == JournalState.OUTCOME_UNKNOWN:
        return False
    if current_state == JournalState.RETRYABLE_NO_EFFECT:
        return bool(
            has_fresh_authorization
            and has_fresh_subject_precondition
            and has_fresh_raw_authority_precondition
            and has_new_bounded_lease
        )
    # Other states never grant automatic retry permission
    return False


def requires_fresh_authorization(state: JournalState) -> bool:
    """Whether a given state requires fresh authorization before any new attempt."""
    if state in (JournalState.RETRYABLE_NO_EFFECT, JournalState.OUTCOME_UNKNOWN, JournalState.RECONCILING, JournalState.APPLYING):
        return True
    return False


def can_reuse_old_lease(state: JournalState, is_outcome_unknown: bool = False) -> bool:
    """Blind old lease reuse is never allowed after unknown or retryable."""
    if is_outcome_unknown or state in (JournalState.OUTCOME_UNKNOWN, JournalState.RETRYABLE_NO_EFFECT):
        return False
    return False


# For guard: explicit contract that RETRYABLE does not grant automatic authorization
RETRYABLE_GRANTS_AUTO_AUTH = False

__all__ = [
    "UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED",
    "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE",
    "RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION",
    "RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE",
    "FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY",
    "UNKNOWN_OUTCOME_CONTRACT",
    "SEMANTIC_ROLLBACK_ALLOWED",
    "is_retry_allowed",
    "requires_fresh_authorization",
    "can_reuse_old_lease",
]
