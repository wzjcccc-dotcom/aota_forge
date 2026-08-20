"""M4-5 journal contract package — contract only, plus M4-7 durable store re-export (read-only)."""

from aota_forge.core.journal.model import (
    JOURNAL_STATE_COUNT,
    JournalRecord,
    JournalState,
)
from aota_forge.core.journal.state_machine import (
    ALLOWED_TRANSITIONS,
    is_valid_transition,
    validate_transition,
)
from aota_forge.core.journal.reconcile import (
    ReconciliationClassification,
    classify_three_way,
)
from aota_forge.core.journal.retry import (
    is_retry_allowed,
)

# M4-7 durable store re-exports (authorized helpers per plan: journal/__init__.py is allowed test helper)
try:
    from aota_forge.core.journal.store import (
        DurableJournalStore,
        DurableJournalEntry,
        InMemoryDurableJournalStore,
        FileBackedDurableJournalStore,
        DURABLE_JOURNAL_STORE_PORT_IMPLEMENTED,
    )
    from aota_forge.core.journal.recovery import RecoveryScanner
    from aota_forge.core.journal.executor import RecoveryExecutor
except ImportError:
    DurableJournalStore = None  # type: ignore
    DurableJournalEntry = None  # type: ignore
    InMemoryDurableJournalStore = None  # type: ignore
    FileBackedDurableJournalStore = None  # type: ignore
    RecoveryScanner = None  # type: ignore
    RecoveryExecutor = None  # type: ignore
    DURABLE_JOURNAL_STORE_PORT_IMPLEMENTED = False  # type: ignore

__all__ = [
    "JournalState",
    "JournalRecord",
    "JOURNAL_STATE_COUNT",
    "ALLOWED_TRANSITIONS",
    "is_valid_transition",
    "validate_transition",
    "ReconciliationClassification",
    "classify_three_way",
    "is_retry_allowed",
    "DurableJournalStore",
    "DurableJournalEntry",
    "InMemoryDurableJournalStore",
    "FileBackedDurableJournalStore",
    "RecoveryScanner",
    "RecoveryExecutor",
]
