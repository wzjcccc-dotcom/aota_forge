"""M4-7 recovery discovery — deterministic scanner for nonterminals.

Governance:
- Recovery discovery enumerates only nonterminals requiring mechanical attention
- Never reprocesses terminals
- Deterministic ordering, no daemon/runtime lifecycle
"""

from __future__ import annotations

from typing import List

from aota_forge.core.journal.model import JournalState
from aota_forge.core.journal.store import DurableJournalEntry, DurableJournalStore

RECOVERY_DISCOVERY_IMPLEMENTED = True
TERMINAL_STATE_REPROCESSING_ALLOWED = False
RECOVERY_SCANNER_IS_DAEMON = False

# Eligible states for recovery (nonterminals)
ELIGIBLE_RECOVERY_STATES = frozenset(
    {
        JournalState.PREPARED,
        JournalState.APPLYING,
        JournalState.OUTCOME_UNKNOWN,
        JournalState.RECONCILING,
    }
)

TERMINAL_STATES = frozenset(
    {
        JournalState.VERIFIED,
        JournalState.VERIFIED_RECOVERED,
        JournalState.FAILED_NO_EFFECT,
        JournalState.RETRYABLE_NO_EFFECT,
        JournalState.CONFLICT,
    }
)


def is_eligible_for_recovery(state: JournalState) -> bool:
    return state in ELIGIBLE_RECOVERY_STATES


def is_terminal(state: JournalState) -> bool:
    return state in TERMINAL_STATES


class RecoveryScanner:
    """Deterministic recovery scanner (explicit invocation, not daemon)."""

    def __init__(self, store: DurableJournalStore) -> None:
        self._store = store

    def scan_requiring_recovery(self) -> List[DurableJournalEntry]:
        """Scan durable store for nonterminals requiring attention.

        Deterministic ordering by journal_id lexicographic then created_at.
        Read-only; repeated scans without state change return same set.
        """
        return self._store.scan_requiring_recovery()

    def scan_nonterminal(self) -> List[DurableJournalEntry]:
        return self._store.scan_nonterminal()

    def discover_for_restart(self) -> List[DurableJournalEntry]:
        """Alias for restart-triggered discovery."""
        return self.scan_requiring_recovery()


# Convenience function for executor
def discover_recoverable_entries(store: DurableJournalStore) -> List[DurableJournalEntry]:
    return store.scan_requiring_recovery()


__all__ = [
    "RECOVERY_DISCOVERY_IMPLEMENTED",
    "TERMINAL_STATE_REPROCESSING_ALLOWED",
    "RECOVERY_SCANNER_IS_DAEMON",
    "ELIGIBLE_RECOVERY_STATES",
    "TERMINAL_STATES",
    "is_eligible_for_recovery",
    "is_terminal",
    "RecoveryScanner",
    "discover_recoverable_entries",
]
