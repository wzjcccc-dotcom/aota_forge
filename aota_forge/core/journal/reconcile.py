"""M4-5 three-way reconciliation classifier — pure, deterministic, no executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from aota_forge.core.journal.model import JournalState

# No heuristic selection, no auto merge, no rollback
HEURISTIC_THIRD_STATE_SELECTION_ALLOWED = False
THIRD_STATE_AUTO_MERGE_ALLOWED = False
SEMANTIC_ROLLBACK_ALLOWED = False
SEMANTIC_COMPENSATING_MUTATION_IMPLEMENTED = False
RECONCILIATION_EXECUTOR_IMPLEMENTED = False

# Normalized equality supporting-evidence-only
NORMALIZED_EQUALITY_PROVES_EXACT_EXTERNAL_EFFECT = False

# Three-way precedence
RECONCILIATION_PRECEDENCE = (
    "exact candidate raw identity",
    "exact original raw identity",
    "third raw state",
)


class ReconciliationClassification(str, Enum):
    """Pure classification result of OBSERVED vs ORIGINAL vs CANDIDATE."""

    CANDIDATE_OBSERVED = "CANDIDATE_OBSERVED"  # observed == candidate -> VERIFIED_RECOVERED
    ORIGINAL_OBSERVED = "ORIGINAL_OBSERVED"    # observed == original -> RETRYABLE_NO_EFFECT (if fresh auth)
    CONFLICT_THIRD = "CONFLICT_THIRD"          # third state -> CONFLICT


@dataclass(frozen=True)
class ClassificationResult:
    classification: ReconciliationClassification
    journal_state: JournalState
    needs_fresh_authorization: bool
    needs_semantic_choice: bool


def classify_three_way(
    *,
    observed_raw_digest: str | None,
    original_raw_digest: str | None,
    candidate_raw_digest: str | None,
    normalized_equal: bool = False,
) -> ClassificationResult:
    """Deterministic pure classifier for ORIGINAL/CANDIDATE/OBSERVED.

    Semantics per accepted plan:
    - OBSERVED == CANDIDATE (exact raw identity) -> intended candidate observed -> VERIFIED_RECOVERED
    - OBSERVED == ORIGINAL (exact raw identity) -> no effect observed -> RETRYABLE_NO_EFFECT (requires fresh auth)
    - OBSERVED != ORIGINAL and != CANDIDATE -> third state -> CONFLICT

    Normalized equality alone -> CONFLICT (J8), never VERIFIED_RECOVERED.
    No heuristic merge, no silent rebase/overwrite.
    """
    if observed_raw_digest is None or candidate_raw_digest is None or original_raw_digest is None:
        # Missing digests cannot be verified -> treat as conflict/requires choice; but pure classifier requires all three
        # For contract testing, we fail closed as conflict if any is None and not matching
        # However to keep deterministic, if any is None we return CONFLICT
        return ClassificationResult(
            classification=ReconciliationClassification.CONFLICT_THIRD,
            journal_state=JournalState.CONFLICT,
            needs_fresh_authorization=False,
            needs_semantic_choice=True,
        )

    # Precedence: candidate first
    if observed_raw_digest == candidate_raw_digest:
        return ClassificationResult(
            classification=ReconciliationClassification.CANDIDATE_OBSERVED,
            journal_state=JournalState.VERIFIED_RECOVERED,
            needs_fresh_authorization=False,
            needs_semantic_choice=False,
        )
    if observed_raw_digest == original_raw_digest:
        # Original observed -> RETRYABLE_NO_EFFECT only after fresh auth etc.
        # The pure classifier returns RETRYABLE_NO_EFFECT; the caller must still enforce fresh authorization before retry.
        return ClassificationResult(
            classification=ReconciliationClassification.ORIGINAL_OBSERVED,
            journal_state=JournalState.RETRYABLE_NO_EFFECT,
            needs_fresh_authorization=True,
            needs_semantic_choice=False,
        )
    # J8: normalized equality does NOT prove exact external effect
    if normalized_equal:
        return ClassificationResult(
            classification=ReconciliationClassification.CONFLICT_THIRD,
            journal_state=JournalState.CONFLICT,
            needs_fresh_authorization=False,
            needs_semantic_choice=True,
        )
    # Third state
    return ClassificationResult(
        classification=ReconciliationClassification.CONFLICT_THIRD,
        journal_state=JournalState.CONFLICT,
        needs_fresh_authorization=False,
        needs_semantic_choice=True,
    )


def classify_with_normalized_disambiguation(
    observed_raw_digest: str | None,
    original_raw_digest: str | None,
    candidate_raw_digest: str | None,
    observed_normalized_digest: str | None,
    candidate_normalized_digest: str | None,
) -> ClassificationResult:
    """Wrapper that handles J8: normalized equality is supporting evidence only."""
    normalized_equal = (
        observed_normalized_digest is not None
        and candidate_normalized_digest is not None
        and observed_normalized_digest == candidate_normalized_digest
    )
    # If raw matches candidate already handled; if raw is third but normalized matches, still CONFLICT
    return classify_three_way(
        observed_raw_digest=observed_raw_digest,
        original_raw_digest=original_raw_digest,
        candidate_raw_digest=candidate_raw_digest,
        normalized_equal=normalized_equal,
    )


# Crash window contract for guard
CRASH_WINDOW_RECONCILIATION_RULES = {
    "C1": "PREPARED without APPLYING -> FAILED_NO_EFFECT",
    "C2": "APPLYING before external call -> RECONCILING",
    "C3": "external known failure -> FAILED_NO_EFFECT",
    "C4": "external succeeds but dies before verify -> RECONCILING -> VERIFIED_RECOVERED if candidate (J3/J10)",
    "C5": "timeout -> OUTCOME_UNKNOWN -> RECONCILING",
    "C6": "verify candidate -> VERIFIED_RECOVERED",
    "C7": "verify original -> RETRYABLE_NO_EFFECT (with fresh auth)",
    "C8": "verify third -> CONFLICT",
    "C9": "restart during RECONCILING -> re-read idempotent",
    "J10": "NONTERMINAL_AFTER_TERMINAL_PERSISTENCE_FAILURE -> RECONCILING",
}

THREE_WAY_RECONCILIATION_CLASSIFIER_IMPLEMENTED = True
J10_SOURCE_CONTRACT_EXPLICIT = True

__all__ = [
    "ReconciliationClassification",
    "ClassificationResult",
    "classify_three_way",
    "classify_with_normalized_disambiguation",
    "HEURISTIC_THIRD_STATE_SELECTION_ALLOWED",
    "THIRD_STATE_AUTO_MERGE_ALLOWED",
    "SEMANTIC_ROLLBACK_ALLOWED",
    "SEMANTIC_COMPENSATING_MUTATION_IMPLEMENTED",
    "RECONCILIATION_EXECUTOR_IMPLEMENTED",
    "RECONCILIATION_PRECEDENCE",
    "THREE_WAY_RECONCILIATION_CLASSIFIER_IMPLEMENTED",
    "J10_SOURCE_CONTRACT_EXPLICIT",
]
