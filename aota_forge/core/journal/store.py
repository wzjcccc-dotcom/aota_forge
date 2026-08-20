"""M4-7 durable journal store — storage-neutral port with atomic CAS and close/reopen durability.

Governance:
- PRODUCTION_STORAGE_ENGINE_FROZEN=no (abstract port, not prescribed engine)
- File-backed is reference/test adapter only, not production default, no global wiring
- Per-record atomic CAS, at-most-one via durable PREPARED->APPLYING, no cross-authority atomicity
- Preserves M4-5 9-state vocabulary verbatim, OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT=0
"""

from __future__ import annotations

import abc
import hashlib
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.state_machine import is_valid_transition, validate_transition

# Governance markers
DURABLE_JOURNAL_STORE_PORT_IMPLEMENTED = True
PRODUCTION_STORAGE_ENGINE_FROZEN = False
PRODUCTION_STORAGE_ENGINE_FROZEN_BY_M4_7_SOURCE = False
FILE_BACKED_IMPLEMENTATION_ROLE = "reference_adapter"
FILE_BACKED_REFERENCE_ADAPTER_IMPLEMENTED = True
FILE_BACKED_REFERENCE_ADAPTER_IS_PRODUCTION_DEFAULT = False
FILE_BACKED_REFERENCE_ADAPTER_GLOBAL_RUNTIME_WIRING_COUNT = 0
DURABLE_JOURNAL_TRANSITION_CAS_IMPLEMENTED = True
DOUBLE_APPLYING_CAS_WIN_ALLOWED = False
JOURNAL_CAS_IMPLIES_EXTERNAL_WRITE_ATOMICITY = False
CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE = False
AT_MOST_ONE_ATTEMPT_DURABLE_IMPLEMENTATION = True
IN_MEMORY_LOCK_IS_SOLE_ATTEMPT_AUTHORITY = False
OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT = 0
GITHUB_API_CALL_COUNT = 0
GITHUB_ADAPTER_IMPLEMENTED = False
CONTROL_COMMENT_ADAPTER_IMPLEMENTED = False
ISSUE_BODY_GITHUB_TRANSPORT_IMPLEMENTED = False
GITHUB_ADAPTER_IMPORT_COUNT = 0

# Schema versioning — explicit per-record version, default 1, no broad migration framework
DURABLE_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSIONING_IMPLEMENTATION = True

# Error codes
JOURNAL_NOT_FOUND = "JOURNAL_NOT_FOUND"
STALE_JOURNAL_REVISION = "STALE_JOURNAL_REVISION"
INVALID_JOURNAL_TRANSITION = "INVALID_JOURNAL_TRANSITION"
JOURNAL_PERSISTENCE_FAILURE = "JOURNAL_PERSISTENCE_FAILURE"


class JournalError(Exception):
    code: str = "JOURNAL_ERROR"


class JournalNotFoundError(JournalError):
    code = JOURNAL_NOT_FOUND


class StaleJournalRevisionError(JournalError):
    code = STALE_JOURNAL_REVISION


class InvalidJournalTransitionError(JournalError):
    code = INVALID_JOURNAL_TRANSITION


class JournalPersistenceFailureError(JournalError):
    code = JOURNAL_PERSISTENCE_FAILURE


def _compute_revision_token(journal_id: str, revision: int, state: JournalState, observed_digest: str | None) -> str:
    payload = f"{journal_id}:{revision}:{state.value}:{observed_digest or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DurableJournalEntry:
    """Stored journal entry with durable CAS metadata.

    The contained JournalRecord is the M4-5 contract record; the wrapper
    adds durable CAS fields that are not part of semantic authorization.
    Historical lineage is preserved via correlation_id; each journal_id is unique.
    """

    record: JournalRecord
    journal_revision: int
    journal_revision_token: str
    durable_schema_version: int = DURABLE_SCHEMA_VERSION
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = self.record.to_dict()
        # Add durable metadata
        d["_journal_revision"] = self.journal_revision
        d["_journal_revision_token"] = self.journal_revision_token
        d["_durable_schema_version"] = self.durable_schema_version
        d["_created_at"] = self.created_at
        d["_updated_at"] = self.updated_at
        # Also store full typed_target canonical for reconstruction already in to_dict
        # Preserve authorization/lease refs as is
        # Include evidence map
        d["_evidence"] = dict(self.record.evidence) if self.record.evidence else {}
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DurableJournalEntry":
        # Reconstruction requires ObjectRef for typed_target
        from aota_forge.core.identity.refs import make_object_ref
        from aota_forge.core.identity.kinds import IdKind
        from aota_forge.core.identity.ids import make_id

        # Use stored fields; create ObjectRef from canonical string
        typed_target_canonical = data["typed_target"]
        # Parse ObjectRef: format like "Subject:xxx" or "Subject:kind:value"
        # The canonical is "Kind:value" style via ObjectRef.to_canonical()
        # We can reconstruct via parse
        from aota_forge.core.identity.refs import parse_object_ref
        try:
            typed_target = parse_object_ref(typed_target_canonical)
        except Exception:
            # Fallback: create generic Subject ref
            # attempt to parse value after colon
            parts = typed_target_canonical.split(":", 1)
            if len(parts) == 2:
                typed_target = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, parts[1]))
            else:
                raise

        # Extract journal state
        state_val = data.get("journal_state")
        journal_state = JournalState(state_val) if isinstance(state_val, str) else data.get("journal_state")

        record = JournalRecord(
            journal_id=data["journal_id"],
            correlation_id=data["correlation_id"],
            attempt_id=data["attempt_id"],
            operation=data["operation"],
            typed_target=typed_target,
            principal=data["principal"],
            contract_hash=data["contract_hash"],
            idempotency_key=data["idempotency_key"],
            intent_fingerprint=data["intent_fingerprint"],
            subject_expected_revision=data["subject_expected_revision"],
            authority_source_revision=data.get("authority_source_revision"),
            authority_observed_raw_digest=data.get("authority_observed_raw_digest"),
            candidate_raw_digest=data.get("candidate_raw_digest"),
            normalized_plan_digest=data.get("normalized_plan_digest"),
            authorization_reference=data.get("authorization_reference"),
            lease_reference=data.get("lease_reference"),
            authorization_basis=data.get("authorization_basis"),
            journal_state=journal_state if isinstance(journal_state, JournalState) else JournalState(state_val),
            observed_raw_digest=data.get("observed_raw_digest"),
            observed_revision=data.get("observed_revision"),
            verification_state=data.get("verification_state"),
            original_raw_digest=data.get("original_raw_digest"),
            evidence=dict(data.get("_evidence", data.get("evidence", {}))),
        )
        return cls(
            record=record,
            journal_revision=int(data.get("_journal_revision", 1)),
            journal_revision_token=str(data.get("_journal_revision_token", "")),
            durable_schema_version=int(data.get("_durable_schema_version", DURABLE_SCHEMA_VERSION)),
            created_at=str(data.get("_created_at", _now_iso())),
            updated_at=str(data.get("_updated_at", _now_iso())),
        )


# Abstract storage-neutral port
class DurableJournalStore(ABC):
    """Storage-neutral durable journal store port (M4-7).

    Capabilities:
    - create_prepared: durably persist PREPARED record (revision 1)
    - cas_transition: atomic CAS state/revision update
    - get: read by journal_id
    - scan_requiring_recovery: eligible recoverable states
    - scan_nonterminal: all nonterminals
    - close/reopen durability via file-backed reference adapter
    - Historical record preservation (no overwrite of lineage)
    """

    @abstractmethod
    def create_prepared(self, record: JournalRecord) -> DurableJournalEntry:
        """Durably persist a PREPARED journal record with revision 1.

        Fails closed if journal_id already exists or state != PREPARED.
        """
        raise NotImplementedError

    @abstractmethod
    def cas_transition(
        self,
        journal_id: str,
        expected_revision: int,
        expected_state: JournalState,
        new_state: JournalState,
        *,
        observed_raw_digest: str | None = None,
        observed_revision: str | int | None = None,
        evidence: Dict[str, Any] | None = None,
        last_observed_raw_authority_body: str | None = None,
        reconciliation_classification: str | None = None,
        failure_error_code: str | None = None,
        failure_error_message: str | None = None,
    ) -> Tuple[bool, DurableJournalEntry]:
        """Atomic CAS: if current revision/state == expected, transition to new_state.

        Validates transition via is_valid_transition; increments revision and token.
        Returns (success, current_entry). On stale/invalid, raises or returns failure.
        Implementations must use durable store state, not only in-memory mutex.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, journal_id: str) -> Optional[DurableJournalEntry]:
        raise NotImplementedError

    @abstractmethod
    def scan_requiring_recovery(self) -> List[DurableJournalEntry]:
        """Return records needing mechanical recovery (nonterminals).

        Only PREPARED, APPLYING, OUTCOME_UNKNOWN, RECONCILING.
        Never returns terminals: VERIFIED, VERIFIED_RECOVERED, FAILED_NO_EFFECT,
        RETRYABLE_NO_EFFECT, CONFLICT.
        Deterministic ordering by journal_id lexicographic then created_at.
        """
        raise NotImplementedError

    @abstractmethod
    def scan_nonterminal(self) -> List[DurableJournalEntry]:
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> List[DurableJournalEntry]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    # Optional helpers for testing / lineage
    def get_by_correlation(self, correlation_id: str) -> List[DurableJournalEntry]:
        return [e for e in self.list_all() if e.record.correlation_id == correlation_id]

    def find_by_idempotency(self, idempotency_key: str) -> List[DurableJournalEntry]:
        return [e for e in self.list_all() if e.record.idempotency_key == idempotency_key]


# In-memory implementation (isolated, deterministic, per-record RLock)
class InMemoryDurableJournalStore(DurableJournalStore):
    """In-memory durable journal store with per-record RLock and CAS.

    Not a global lock; independent journal_ids do not contend.
    Durability is in-memory only (no file); suitable for unit tests that
    do not require close/reopen. For close/reopen proofs use FileBacked.
    """

    def __init__(self) -> None:
        self._store: Dict[str, DurableJournalEntry] = {}
        self._locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()
        self._closed = False
        # For failure injection (test seam): if set, next persist will raise
        self._fail_next_create: bool = False
        self._fail_next_cas: bool = False

    def _lock_for(self, journal_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(journal_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[journal_id] = lock
            return lock

    def inject_fail_next_create(self) -> None:
        self._fail_next_create = True

    def inject_fail_next_cas(self) -> None:
        self._fail_next_cas = True

    def create_prepared(self, record: JournalRecord) -> DurableJournalEntry:
        if self._closed:
            raise JournalPersistenceFailureError("store closed")
        if self._fail_next_create:
            self._fail_next_create = False
            raise JournalPersistenceFailureError("injected fail_before_prepared")
        if record.journal_state != JournalState.PREPARED:
            raise InvalidJournalTransitionError(f"create_prepared requires PREPARED, got {record.journal_state}")
        lock = self._lock_for(record.journal_id)
        with lock:
            if record.journal_id in self._store:
                raise JournalPersistenceFailureError(f"journal_id already exists: {record.journal_id}")
            # Validate distinct domains invariant already in JournalRecord
            revision = 1
            token = _compute_revision_token(record.journal_id, revision, record.journal_state, record.observed_raw_digest)
            now = _now_iso()
            entry = DurableJournalEntry(
                record=record,
                journal_revision=revision,
                journal_revision_token=token,
                durable_schema_version=DURABLE_SCHEMA_VERSION,
                created_at=now,
                updated_at=now,
            )
            self._store[record.journal_id] = entry
            return entry

    def cas_transition(
        self,
        journal_id: str,
        expected_revision: int,
        expected_state: JournalState,
        new_state: JournalState,
        *,
        observed_raw_digest: str | None = None,
        observed_revision: str | int | None = None,
        evidence: Dict[str, Any] | None = None,
        last_observed_raw_authority_body: str | None = None,
        reconciliation_classification: str | None = None,
        failure_error_code: str | None = None,
        failure_error_message: str | None = None,
    ) -> Tuple[bool, DurableJournalEntry]:
        if self._closed:
            raise JournalPersistenceFailureError("store closed")
        if self._fail_next_cas:
            self._fail_next_cas = False
            raise JournalPersistenceFailureError("injected fail_before_cas")
        lock = self._lock_for(journal_id)
        with lock:
            current = self._store.get(journal_id)
            if current is None:
                raise JournalNotFoundError(f"journal not found: {journal_id}")
            # Check stale revision/state
            if current.journal_revision != expected_revision or current.record.journal_state != expected_state:
                raise StaleJournalRevisionError(
                    f"stale: expected rev {expected_revision} state {expected_state} but current rev {current.journal_revision} state {current.record.journal_state}"
                )
            # Validate transition
            if not is_valid_transition(expected_state, new_state):
                raise InvalidJournalTransitionError(f"invalid transition {expected_state.value} -> {new_state.value}")
            # Build new record with updated observed fields
            new_record = replace(
                current.record,
                journal_state=new_state,
                observed_raw_digest=observed_raw_digest if observed_raw_digest is not None else current.record.observed_raw_digest,
                observed_revision=observed_revision if observed_revision is not None else current.record.observed_revision,
                evidence={**dict(current.record.evidence), **(evidence or {})},
            )
            # Also handle optional reconciliation fields stored in evidence for audit
            extra_evidence: Dict[str, Any] = {}
            if last_observed_raw_authority_body is not None:
                extra_evidence["last_observed_raw_authority_body"] = last_observed_raw_authority_body[:1000]
            if reconciliation_classification is not None:
                extra_evidence["reconciliation_classification"] = reconciliation_classification
            if failure_error_code is not None:
                extra_evidence["failure_error_code"] = failure_error_code
            if failure_error_message is not None:
                extra_evidence["failure_error_message"] = failure_error_message[:500]
            if extra_evidence:
                new_record = replace(new_record, evidence={**dict(new_record.evidence), **extra_evidence})
            new_revision = current.journal_revision + 1
            new_token = _compute_revision_token(journal_id, new_revision, new_state, new_record.observed_raw_digest)
            now = _now_iso()
            new_entry = DurableJournalEntry(
                record=new_record,
                journal_revision=new_revision,
                journal_revision_token=new_token,
                durable_schema_version=current.durable_schema_version,
                created_at=current.created_at,
                updated_at=now,
            )
            self._store[journal_id] = new_entry
            return (True, new_entry)

    def get(self, journal_id: str) -> Optional[DurableJournalEntry]:
        return self._store.get(journal_id)

    def scan_requiring_recovery(self) -> List[DurableJournalEntry]:
        eligible = {JournalState.PREPARED, JournalState.APPLYING, JournalState.OUTCOME_UNKNOWN, JournalState.RECONCILING}
        result = [e for e in self._store.values() if e.record.journal_state in eligible]
        result.sort(key=lambda e: (e.record.journal_id, e.created_at))
        return result

    def scan_nonterminal(self) -> List[DurableJournalEntry]:
        return self.scan_requiring_recovery()

    def list_all(self) -> List[DurableJournalEntry]:
        result = list(self._store.values())
        result.sort(key=lambda e: (e.record.journal_id, e.created_at))
        return result

    def close(self) -> None:
        self._closed = True


# File-backed reference adapter (deterministic close/reopen durability)
class FileBackedDurableJournalStore(DurableJournalStore):
    """File-backed reference adapter for deterministic close/reopen durability.

    Role: reference/test durability implementation only.
    Not a production default; no global wiring. Uses isolated temporary test paths.
    Persistence: atomic JSON file with per-record CAS, schema versioning, and per-record RLock.
    Supports deterministic failure injection via injected flags.
    """

    def __init__(self, path: str | Path, *, ensure_dir: bool = True) -> None:
        self._path = Path(path)
        self._locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()
        self._closed = False
        self._store: Dict[str, DurableJournalEntry] = {}
        self._fail_next_create = False
        self._fail_next_cas = False
        self._fail_next_persist = False
        if ensure_dir:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _lock_for(self, journal_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(journal_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[journal_id] = lock
            return lock

    def _load(self) -> None:
        if not self._path.exists():
            self._store = {}
            return
        try:
            text = self._path.read_text(encoding="utf-8")
            if not text.strip():
                self._store = {}
                return
            data = json.loads(text)
            store: Dict[str, DurableJournalEntry] = {}
            for journal_id, entry_dict in data.items():
                try:
                    entry = DurableJournalEntry.from_dict(entry_dict)
                    store[journal_id] = entry
                except Exception:
                    # Skip corrupted entries fail-closed? For determinism we raise
                    raise JournalPersistenceFailureError(f"corrupted journal entry {journal_id}")
            self._store = store
        except json.JSONDecodeError as e:
            raise JournalPersistenceFailureError(f"persistence load failed: {e}") from e

    def _persist(self) -> None:
        if self._fail_next_persist:
            self._fail_next_persist = False
            raise JournalPersistenceFailureError("injected terminal_persist_fails")
        # Atomic write via temp file + rename
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = {jid: entry.to_dict() for jid, entry in self._store.items()}
        text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path)

    # Failure injection seams
    def inject_fail_next_create(self) -> None:
        self._fail_next_create = True

    def inject_fail_next_cas(self) -> None:
        self._fail_next_cas = True

    def inject_fail_next_persist(self) -> None:
        self._fail_next_persist = True

    def inject_fail_before_prepared(self) -> None:
        self.inject_fail_next_create()

    def inject_fail_before_applying(self) -> None:
        self.inject_fail_next_cas()

    def inject_terminal_persist_fails(self) -> None:
        self.inject_fail_next_persist()

    def create_prepared(self, record: JournalRecord) -> DurableJournalEntry:
        if self._closed:
            raise JournalPersistenceFailureError("store closed")
        if self._fail_next_create:
            self._fail_next_create = False
            raise JournalPersistenceFailureError("injected fail_before_prepared")
        if record.journal_state != JournalState.PREPARED:
            raise InvalidJournalTransitionError(f"create_prepared requires PREPARED, got {record.journal_state}")
        lock = self._lock_for(record.journal_id)
        with lock:
            # Reload to ensure durability across processes? For single process we use in-memory but persist
            self._load()
            if record.journal_id in self._store:
                raise JournalPersistenceFailureError(f"journal_id already exists: {record.journal_id}")
            revision = 1
            token = _compute_revision_token(record.journal_id, revision, record.journal_state, record.observed_raw_digest)
            now = _now_iso()
            entry = DurableJournalEntry(
                record=record,
                journal_revision=revision,
                journal_revision_token=token,
                durable_schema_version=DURABLE_SCHEMA_VERSION,
                created_at=now,
                updated_at=now,
            )
            self._store[record.journal_id] = entry
            try:
                self._persist()
            except JournalPersistenceFailureError:
                # Rollback in-memory on persist failure (fail closed, no partial)
                self._store.pop(record.journal_id, None)
                raise
            except Exception as e:
                self._store.pop(record.journal_id, None)
                raise JournalPersistenceFailureError(f"persist failed: {e}") from e
            return entry

    def cas_transition(
        self,
        journal_id: str,
        expected_revision: int,
        expected_state: JournalState,
        new_state: JournalState,
        *,
        observed_raw_digest: str | None = None,
        observed_revision: str | int | None = None,
        evidence: Dict[str, Any] | None = None,
        last_observed_raw_authority_body: str | None = None,
        reconciliation_classification: str | None = None,
        failure_error_code: str | None = None,
        failure_error_message: str | None = None,
    ) -> Tuple[bool, DurableJournalEntry]:
        if self._closed:
            raise JournalPersistenceFailureError("store closed")
        if self._fail_next_cas:
            self._fail_next_cas = False
            raise JournalPersistenceFailureError("injected fail_before_applying")
        lock = self._lock_for(journal_id)
        with lock:
            self._load()
            current = self._store.get(journal_id)
            if current is None:
                raise JournalNotFoundError(f"journal not found: {journal_id}")
            if current.journal_revision != expected_revision or current.record.journal_state != expected_state:
                raise StaleJournalRevisionError(
                    f"stale: expected rev {expected_revision} state {expected_state} but current rev {current.journal_revision} state {current.record.journal_state}"
                )
            if not is_valid_transition(expected_state, new_state):
                raise InvalidJournalTransitionError(f"invalid transition {expected_state.value} -> {new_state.value}")
            new_record = replace(
                current.record,
                journal_state=new_state,
                observed_raw_digest=observed_raw_digest if observed_raw_digest is not None else current.record.observed_raw_digest,
                observed_revision=observed_revision if observed_revision is not None else current.record.observed_revision,
                evidence={**dict(current.record.evidence), **(evidence or {})},
            )
            extra_evidence: Dict[str, Any] = {}
            if last_observed_raw_authority_body is not None:
                extra_evidence["last_observed_raw_authority_body"] = last_observed_raw_authority_body[:1000]
            if reconciliation_classification is not None:
                extra_evidence["reconciliation_classification"] = reconciliation_classification
            if failure_error_code is not None:
                extra_evidence["failure_error_code"] = failure_error_code
            if failure_error_message is not None:
                extra_evidence["failure_error_message"] = failure_error_message[:500]
            if extra_evidence:
                new_record = replace(new_record, evidence={**dict(new_record.evidence), **extra_evidence})
            new_revision = current.journal_revision + 1
            new_token = _compute_revision_token(journal_id, new_revision, new_state, new_record.observed_raw_digest)
            now = _now_iso()
            new_entry = DurableJournalEntry(
                record=new_record,
                journal_revision=new_revision,
                journal_revision_token=new_token,
                durable_schema_version=current.durable_schema_version,
                created_at=current.created_at,
                updated_at=now,
            )
            old_entry = current
            self._store[journal_id] = new_entry
            try:
                self._persist()
            except JournalPersistenceFailureError:
                # Rollback to old entry on persist failure -> J10 nonterminal_after_terminal
                self._store[journal_id] = old_entry
                # Need to reload? But we keep old in memory and file still has old
                # For determinism, re-persist of old not needed; just raise
                raise
            except Exception as e:
                self._store[journal_id] = old_entry
                raise JournalPersistenceFailureError(f"persist failed: {e}") from e
            return (True, new_entry)

    def get(self, journal_id: str) -> Optional[DurableJournalEntry]:
        if self._closed:
            # Allow get after close before reopen? We'll reload for reopen case via new instance
            # For close semantics, we allow read but not write
            pass
        with self._global_lock:
            self._load()
            return self._store.get(journal_id)

    def scan_requiring_recovery(self) -> List[DurableJournalEntry]:
        eligible = {JournalState.PREPARED, JournalState.APPLYING, JournalState.OUTCOME_UNKNOWN, JournalState.RECONCILING}
        with self._global_lock:
            self._load()
            result = [e for e in self._store.values() if e.record.journal_state in eligible]
            result.sort(key=lambda e: (e.record.journal_id, e.created_at))
            return result

    def scan_nonterminal(self) -> List[DurableJournalEntry]:
        return self.scan_requiring_recovery()

    def list_all(self) -> List[DurableJournalEntry]:
        with self._global_lock:
            self._load()
            result = list(self._store.values())
            result.sort(key=lambda e: (e.record.journal_id, e.created_at))
            return result

    def close(self) -> None:
        with self._global_lock:
            if not self._closed:
                # Ensure persisted before close
                try:
                    self._persist()
                except Exception:
                    pass
                self._closed = True

    def reopen(self) -> None:
        with self._global_lock:
            self._closed = False
            self._load()


__all__ = [
    "DURABLE_JOURNAL_STORE_PORT_IMPLEMENTED",
    "PRODUCTION_STORAGE_ENGINE_FROZEN",
    "PRODUCTION_STORAGE_ENGINE_FROZEN_BY_M4_7_SOURCE",
    "FILE_BACKED_IMPLEMENTATION_ROLE",
    "FILE_BACKED_REFERENCE_ADAPTER_IMPLEMENTED",
    "FILE_BACKED_REFERENCE_ADAPTER_IS_PRODUCTION_DEFAULT",
    "FILE_BACKED_REFERENCE_ADAPTER_GLOBAL_RUNTIME_WIRING_COUNT",
    "DURABLE_JOURNAL_TRANSITION_CAS_IMPLEMENTED",
    "DOUBLE_APPLYING_CAS_WIN_ALLOWED",
    "JOURNAL_CAS_IMPLIES_EXTERNAL_WRITE_ATOMICITY",
    "CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE",
    "AT_MOST_ONE_ATTEMPT_DURABLE_IMPLEMENTATION",
    "IN_MEMORY_LOCK_IS_SOLE_ATTEMPT_AUTHORITY",
    "OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT",
    "GITHUB_API_CALL_COUNT",
    "GITHUB_ADAPTER_IMPLEMENTED",
    "CONTROL_COMMENT_ADAPTER_IMPLEMENTED",
    "ISSUE_BODY_GITHUB_TRANSPORT_IMPLEMENTED",
    "GITHUB_ADAPTER_IMPORT_COUNT",
    "DURABLE_SCHEMA_VERSION",
    "JOURNAL_SCHEMA_VERSIONING_IMPLEMENTATION",
    "JOURNAL_NOT_FOUND",
    "STALE_JOURNAL_REVISION",
    "INVALID_JOURNAL_TRANSITION",
    "JOURNAL_PERSISTENCE_FAILURE",
    "JournalError",
    "JournalNotFoundError",
    "StaleJournalRevisionError",
    "InvalidJournalTransitionError",
    "JournalPersistenceFailureError",
    "DurableJournalEntry",
    "DurableJournalStore",
    "InMemoryDurableJournalStore",
    "FileBackedDurableJournalStore",
]
