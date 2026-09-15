"""AF #57 M1/W3 — local SQLite adapter for the Project Governance Store.

One local adapter over the standard library ``sqlite3`` only.  No ORM, no
PostgreSQL, no event sourcing/CQRS/event bus, no migration framework.

Mechanics:

* atomic mutation: explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` transactions with
  rollback on any failure (a bounded injected-failure seam proves the rollback
  leaves no partial state);
* CAS / revision protection: ``compare_and_swap_plan`` updates only when both
  the observed record revision and the SQL ``WHERE revision = ?`` predicate
  agree; stale expectations fail closed;
* close/reopen durability including real-process reopen (the DB is a real
  SQLite file; no in-memory cache is authoritative);
* fail-closed malformed/corrupt state: unsupported schema version, missing
  table, non-SQLite bytes, or any row that cannot be revalidated as a
  canonical ``ProjectPlanRecord`` aborts the open with a bounded error;
* bounded deterministic errors (typed codes in ``project_store``).

The store owns project / Plan governance only.  It does not create tables or
fields for execution attempts, worker results, work progression, completion
queue, or Human Brake (see the negative guards in ``project_store``).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from aota_forge.adapters.plan_authority.binding import (
    PlanAuthorityBinding,
    PlanAuthorityBindingError,
)
from aota_forge.governance.project_store import (
    PROJECT_GOVERNANCE_SCHEMA_VERSION,
    PlanRecordAlreadyExistsError,
    PlanRecordNotFoundError,
    ProjectGovernanceCorruptStateError,
    ProjectGovernancePersistenceError,
    ProjectGovernanceRecordError,
    ProjectGovernanceStore,
    ProjectGovernanceStoreClosedError,
    ProjectPlanRecord,
    StalePlanRevisionError,
)

_TABLE = "project_plan_governance"
_COLUMNS = (
    "project_id",
    "plan_id",
    "lifecycle_state",
    "source_kind",
    "authority_ref",
    "source_revision",
    "source_digest",
    "revision",
)
_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    project_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    authority_ref TEXT NOT NULL,
    source_revision TEXT,
    source_digest TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (project_id, plan_id)
)
"""
_INSERT_SQL = (
    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)})"
)
_SELECT_ONE_SQL = f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE project_id = ? AND plan_id = ?"
_SELECT_PROJECT_SQL = f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE project_id = ? ORDER BY plan_id"
_UPDATE_SQL = (
    f"UPDATE {_TABLE} SET lifecycle_state = ?, source_kind = ?, authority_ref = ?, "
    "source_revision = ?, source_digest = ?, revision = ? "
    "WHERE project_id = ? AND plan_id = ? AND revision = ?"
)

# SQLite error codes that mean the persisted bytes are not a usable database.
_CORRUPTION_ERROR_CODES = frozenset(
    {
        getattr(sqlite3, "SQLITE_CORRUPT", 11),
        getattr(sqlite3, "SQLITE_NOTADB", 26),
    }
)


def _encode_source_revision(value: str | int | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _decode_source_revision(value: Any) -> str | int | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectGovernanceRecordError("stored source_revision must be TEXT or NULL")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProjectGovernanceRecordError("stored source_revision is not a JSON scalar") from exc
    if isinstance(decoded, bool) or not isinstance(decoded, (str, int)):
        raise ProjectGovernanceRecordError("stored source_revision must be a str|int scalar")
    return decoded


class SQLiteProjectGovernanceStore(ProjectGovernanceStore):
    """Local SQLite implementation of the Project Governance Store port."""

    def __init__(self, path: str | Path, *, ensure_dir: bool = True) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._fail_next_write = False
        if ensure_dir:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ProjectGovernancePersistenceError(
                    f"cannot prepare governance store directory: {exc}"
                ) from exc
        self._connection = self._open_connection()
        try:
            self._ensure_schema()
            self._verify_existing_records()
        except BaseException:
            self._connection.close()
            raise

    # -- failure injection seam (tests only; bounded and explicit) -----------

    def inject_fail_next_write(self) -> None:
        """Raise inside the next write transaction, after SQL, before COMMIT."""
        self._fail_next_write = True

    # -- open / schema --------------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(self._path), isolation_level=None, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA user_version")
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", None) in _CORRUPTION_ERROR_CODES:
                raise ProjectGovernanceCorruptStateError(
                    f"governance store {self._path} is not a usable database: {exc}"
                ) from exc
            raise ProjectGovernancePersistenceError(
                f"cannot open governance store {self._path}: {exc}"
            ) from exc
        return connection

    def _ensure_schema(self) -> None:
        try:
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            table = self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (_TABLE,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise ProjectGovernanceCorruptStateError(
                f"cannot inspect governance store schema: {exc}"
            ) from exc

        if version == 0 and table is None:
            self._initialize_schema()
            return
        if version != PROJECT_GOVERNANCE_SCHEMA_VERSION:
            raise ProjectGovernanceCorruptStateError(
                f"unsupported governance store schema version {version}; fail closed"
            )
        if table is None:
            raise ProjectGovernanceCorruptStateError(
                "governance store table is missing for the declared schema version; fail closed"
            )

    def _initialize_schema(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(_CREATE_TABLE_SQL)
            self._connection.execute(f"PRAGMA user_version = {PROJECT_GOVERNANCE_SCHEMA_VERSION}")
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback_safely()
            raise ProjectGovernancePersistenceError(
                f"cannot initialize governance store: {exc}"
            ) from exc

    def _verify_existing_records(self) -> None:
        try:
            rows = self._connection.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectGovernanceCorruptStateError(
                f"cannot read governance store records: {exc}"
            ) from exc
        for row in rows:
            self._row_to_record(row)

    # -- transaction mechanics -------------------------------------------------

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise ProjectGovernancePersistenceError(
                f"cannot begin governance transaction: {exc}"
            ) from exc
        try:
            yield
        except BaseException:
            self._rollback_safely()
            raise
        try:
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback_safely()
            raise ProjectGovernancePersistenceError(
                f"governance transaction commit failed: {exc}"
            ) from exc

    def _rollback_safely(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _inject_write_failure(self) -> None:
        if self._fail_next_write:
            self._fail_next_write = False
            raise ProjectGovernancePersistenceError("injected governance write failure")

    def _require_open(self) -> None:
        if self._closed:
            raise ProjectGovernanceStoreClosedError("governance store is closed")

    # -- row / record conversion ------------------------------------------------

    @staticmethod
    def _record_params(record: ProjectPlanRecord) -> tuple[Any, ...]:
        return (
            record.project_id,
            record.plan_id,
            record.lifecycle_state,
            record.authority.source_kind,
            record.authority.authority_ref,
            _encode_source_revision(record.authority.source_revision),
            record.authority.source_digest,
            record.revision,
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ProjectPlanRecord:
        project_id = row["project_id"]
        plan_id = row["plan_id"]
        try:
            authority = PlanAuthorityBinding(
                plan_id=plan_id,
                source_kind=row["source_kind"],
                authority_ref=row["authority_ref"],
                source_revision=_decode_source_revision(row["source_revision"]),
                source_digest=row["source_digest"],
            )
            return ProjectPlanRecord(
                project_id=project_id,
                plan_id=plan_id,
                lifecycle_state=row["lifecycle_state"],
                authority=authority,
                revision=row["revision"],
            )
        except (PlanAuthorityBindingError, ProjectGovernanceRecordError, ValueError, TypeError) as exc:
            raise ProjectGovernanceCorruptStateError(
                f"corrupted governance record for project {project_id!r} plan {plan_id!r}: {exc}"
            ) from exc

    # -- port --------------------------------------------------------------------

    def put_plan(self, record: ProjectPlanRecord) -> ProjectPlanRecord:
        if not isinstance(record, ProjectPlanRecord):
            raise ProjectGovernanceRecordError("record must be a ProjectPlanRecord")
        if record.revision != 1:
            raise ProjectGovernanceRecordError(
                "put_plan creates a new record at revision 1; use compare_and_swap_plan to mutate"
            )
        with self._lock:
            self._require_open()
            with self._write_transaction():
                try:
                    self._connection.execute(_INSERT_SQL, self._record_params(record))
                except sqlite3.IntegrityError as exc:
                    raise PlanRecordAlreadyExistsError(
                        f"plan record already exists: {record.project_id}/{record.plan_id}"
                    ) from exc
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"governance write failed: {exc}"
                    ) from exc
                self._inject_write_failure()
        return record

    def get_plan(self, project_id: str, plan_id: str) -> ProjectPlanRecord | None:
        with self._lock:
            self._require_open()
            try:
                row = self._connection.execute(_SELECT_ONE_SQL, (project_id, plan_id)).fetchone()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"governance read failed: {exc}"
                ) from exc
            if row is None:
                return None
            return self._row_to_record(row)

    def list_plans(self, project_id: str) -> tuple[ProjectPlanRecord, ...]:
        with self._lock:
            self._require_open()
            try:
                rows = self._connection.execute(_SELECT_PROJECT_SQL, (project_id,)).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"governance read failed: {exc}"
                ) from exc
            return tuple(self._row_to_record(row) for row in rows)

    def compare_and_swap_plan(
        self,
        project_id: str,
        plan_id: str,
        expected_revision: int,
        *,
        lifecycle_state: str | None = None,
        authority: PlanAuthorityBinding | None = None,
    ) -> ProjectPlanRecord:
        if lifecycle_state is None and authority is None:
            raise ProjectGovernanceRecordError(
                "compare_and_swap_plan requires a lifecycle_state or authority update"
            )
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ProjectGovernanceRecordError("expected_revision must be a positive integer")
        with self._lock:
            self._require_open()
            with self._write_transaction():
                try:
                    row = self._connection.execute(
                        _SELECT_ONE_SQL, (project_id, plan_id)
                    ).fetchone()
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"governance read failed: {exc}"
                    ) from exc
                if row is None:
                    raise PlanRecordNotFoundError(
                        f"plan record not found: {project_id}/{plan_id}"
                    )
                current = self._row_to_record(row)
                if current.revision != expected_revision:
                    raise StalePlanRevisionError(
                        f"plan record {project_id}/{plan_id} revision {current.revision} "
                        f"does not match expected revision {expected_revision}"
                    )
                candidate = ProjectPlanRecord(
                    project_id=current.project_id,
                    plan_id=current.plan_id,
                    lifecycle_state=(
                        lifecycle_state if lifecycle_state is not None else current.lifecycle_state
                    ),
                    authority=authority if authority is not None else current.authority,
                    revision=current.revision + 1,
                )
                try:
                    cursor = self._connection.execute(
                        _UPDATE_SQL,
                        (
                            candidate.lifecycle_state,
                            candidate.authority.source_kind,
                            candidate.authority.authority_ref,
                            _encode_source_revision(candidate.authority.source_revision),
                            candidate.authority.source_digest,
                            candidate.revision,
                            project_id,
                            plan_id,
                            expected_revision,
                        ),
                    )
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"governance write failed: {exc}"
                    ) from exc
                if cursor.rowcount != 1:
                    raise StalePlanRevisionError(
                        f"plan record {project_id}/{plan_id} changed during mutation; fail closed"
                    )
                self._inject_write_failure()
        return candidate

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._connection.close()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"governance store close failed: {exc}"
                ) from exc
