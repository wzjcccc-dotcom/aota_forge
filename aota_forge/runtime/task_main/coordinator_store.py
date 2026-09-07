"""Storage-neutral coordinator store port + bounded file reference adapter (M3/W1).

``TaskMainCoordinatorStore`` is the smallest storage-neutral contract the
persistent task-main coordinator needs: create / get / CAS-update /
list-active / close. It is not a generic event store, workflow DB, agent DB,
or queue framework.

``FileBackedTaskMainCoordinatorStore`` is the bounded single-host reference
adapter for restart tests. It mirrors the accepted M2
``FileBackedExecutionStateStore`` pattern (atomic temp+rename persist,
per-record locks, revision tokens, fail-closed strict load with rollback on
persist failure). Reference/test adapter only:
``FILE_BACKED_IS_PRODUCTION_DEFAULT=no``; the production engine stays
unfrozen (``PRODUCTION_COORDINATOR_STORE_FROZEN=no``).
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATE_SCHEMA_VERSION,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    _compute_coordinator_revision_token,
    _now_iso,
)

FILE_BACKED_IS_PRODUCTION_DEFAULT = False
PRODUCTION_COORDINATOR_STORE_FROZEN = False


class CoordinatorNotFoundError(LookupError):
    def __init__(self, coordinator_id: str) -> None:
        self.coordinator_id = coordinator_id
        super().__init__(f"task-main coordinator not found: {coordinator_id!r}")


class StaleCoordinatorRevisionError(ValueError):
    def __init__(self, coordinator_id: str, expected: int, actual: int) -> None:
        self.coordinator_id = coordinator_id
        super().__init__(
            f"stale coordinator revision for {coordinator_id!r}: expected {expected}, durable is {actual}"
        )


class CoordinatorPersistenceFailureError(IOError):
    pass


class CoordinatorIdempotencyConflictError(ValueError):
    pass


def _check_expected_revision(
    current: TaskMainCoordinatorState,
    expected_revision: int,
    expected_revision_token: str | None,
) -> None:
    if type(expected_revision) is not int or expected_revision != current.coordinator_revision:
        raise StaleCoordinatorRevisionError(
            current.coordinator_id, expected_revision, current.coordinator_revision
        )
    if expected_revision_token is not None and expected_revision_token != current.revision_token:
        raise StaleCoordinatorRevisionError(
            current.coordinator_id, expected_revision, current.coordinator_revision
        )


class TaskMainCoordinatorStore(ABC):
    """Storage-neutral port for durable task-main coordinator state."""

    @abstractmethod
    def create(self, state: TaskMainCoordinatorState) -> TaskMainCoordinatorState:
        """Persist a fresh coordinator (revision normalized to 1)."""
        raise NotImplementedError

    @abstractmethod
    def get(self, coordinator_id: str) -> TaskMainCoordinatorState | None:
        """Reload the durable coordinator, or None when absent."""
        raise NotImplementedError

    @abstractmethod
    def compare_and_swap(
        self,
        coordinator_id: str,
        expected_revision: int,
        updates: Mapping[str, Any],
        expected_revision_token: str | None = None,
    ) -> TaskMainCoordinatorState:
        """Apply a CAS update; stale revisions fail closed."""
        raise NotImplementedError

    @abstractmethod
    def list_active(self) -> list[TaskMainCoordinatorState]:
        """List coordinators whose lifecycle status is ACTIVE."""
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> list[TaskMainCoordinatorState]:
        """List all durable coordinators (bounded recovery surface)."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Persist and release the store."""
        raise NotImplementedError


class FileBackedTaskMainCoordinatorStore(TaskMainCoordinatorStore):
    """File-backed reference adapter proving close/reopen durability.

    Single-file JSON map keyed by coordinator_id. Atomic temp-write + fsync +
    rename persist, per-record locks, revision tokens, fail-closed strict load
    with in-memory rollback on persist failure.
    """

    def __init__(self, path: str | Path, *, ensure_dir: bool = True) -> None:
        self._path = Path(path)
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()
        self._closed = False
        self._store: dict[str, TaskMainCoordinatorState] = {}
        if ensure_dir:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _lock_for(self, coordinator_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(coordinator_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[coordinator_id] = lock
            return lock

    def _load(self) -> None:
        if not self._path.exists():
            self._store = {}
            return
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CoordinatorPersistenceFailureError(f"coordinator persistence read failed: {exc}") from exc
        if not text.strip():
            self._store = {}
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CoordinatorPersistenceFailureError(f"coordinator persistence load failed: {exc}") from exc
        if not isinstance(data, dict):
            raise CoordinatorPersistenceFailureError("coordinator persistence root must be a JSON object")
        store: dict[str, TaskMainCoordinatorState] = {}
        for coordinator_id, entry in data.items():
            try:
                state = TaskMainCoordinatorState.from_dict(entry)
            except (ValueError, TypeError) as exc:
                raise CoordinatorPersistenceFailureError(
                    f"corrupted or unsupported coordinator record {coordinator_id}: {exc}"
                ) from exc
            if state.schema_version != COORDINATOR_STATE_SCHEMA_VERSION:
                raise CoordinatorPersistenceFailureError(
                    f"unsupported coordinator schema_version for {coordinator_id}: {state.schema_version}"
                )
            if state.coordinator_id != coordinator_id:
                raise CoordinatorPersistenceFailureError(
                    f"coordinator record key {coordinator_id!r} contradicts coordinator_id "
                    f"{state.coordinator_id!r}"
                )
            store[coordinator_id] = state
        self._store = store

    def _persist(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = {coordinator_id: state.to_dict() for coordinator_id, state in self._store.items()}
        text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True)
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            raise CoordinatorPersistenceFailureError(f"coordinator persist failed: {exc}") from exc

    def create(self, state: TaskMainCoordinatorState) -> TaskMainCoordinatorState:
        if self._closed:
            raise CoordinatorPersistenceFailureError("coordinator store closed")
        if not isinstance(state, TaskMainCoordinatorState):
            raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
        with self._lock_for(state.coordinator_id):
            self._load()
            if state.coordinator_id in self._store:
                raise CoordinatorIdempotencyConflictError(
                    f"coordinator_id already durable: {state.coordinator_id}"
                )
            now = _now_iso()
            fresh = replace(
                state,
                coordinator_revision=1,
                revision_token=_compute_coordinator_revision_token(
                    state.coordinator_id, 1, state.status
                ),
                created_at=now,
                updated_at=now,
            )
            self._store[state.coordinator_id] = fresh
            try:
                self._persist()
            except CoordinatorPersistenceFailureError:
                self._store.pop(state.coordinator_id, None)
                raise
            except Exception as exc:
                self._store.pop(state.coordinator_id, None)
                raise CoordinatorPersistenceFailureError(f"coordinator persist failed: {exc}") from exc
            return fresh

    def get(self, coordinator_id: str) -> TaskMainCoordinatorState | None:
        with self._global_lock:
            self._load()
            return self._store.get(coordinator_id)

    def compare_and_swap(
        self,
        coordinator_id: str,
        expected_revision: int,
        updates: Mapping[str, Any],
        expected_revision_token: str | None = None,
    ) -> TaskMainCoordinatorState:
        if self._closed:
            raise CoordinatorPersistenceFailureError("coordinator store closed")
        with self._lock_for(coordinator_id):
            self._load()
            current = self._store.get(coordinator_id)
            if current is None:
                raise CoordinatorNotFoundError(coordinator_id)
            _check_expected_revision(current, expected_revision, expected_revision_token)
            try:
                candidate = current.with_cas_updates(updates)
            except (ValueError, TypeError) as exc:
                raise CoordinatorPersistenceFailureError(f"coordinator update invalid: {exc}") from exc
            old_entry = current
            self._store[coordinator_id] = candidate
            try:
                self._persist()
            except CoordinatorPersistenceFailureError:
                self._store[coordinator_id] = old_entry
                raise
            except Exception as exc:
                self._store[coordinator_id] = old_entry
                raise CoordinatorPersistenceFailureError(f"coordinator persist failed: {exc}") from exc
            return candidate

    def list_active(self) -> list[TaskMainCoordinatorState]:
        with self._global_lock:
            self._load()
            result = [s for s in self._store.values() if s.status == CoordinatorStatus.ACTIVE]
            result.sort(key=lambda s: s.coordinator_id)
            return result

    def list_all(self) -> list[TaskMainCoordinatorState]:
        with self._global_lock:
            self._load()
            result = list(self._store.values())
            result.sort(key=lambda s: s.coordinator_id)
            return result

    def close(self) -> None:
        with self._global_lock:
            if not self._closed:
                with suppress(CoordinatorPersistenceFailureError):
                    self._persist()
                self._closed = True


__all__ = [
    "FILE_BACKED_IS_PRODUCTION_DEFAULT",
    "PRODUCTION_COORDINATOR_STORE_FROZEN",
    "CoordinatorIdempotencyConflictError",
    "CoordinatorNotFoundError",
    "CoordinatorPersistenceFailureError",
    "FileBackedTaskMainCoordinatorStore",
    "StaleCoordinatorRevisionError",
    "TaskMainCoordinatorStore",
]
