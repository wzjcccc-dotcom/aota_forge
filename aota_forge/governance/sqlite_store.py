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

AF #57 M2/W4 additive extension: the bounded cross-project grant record
(``cross_project_grant``) is durable in this same store.  It is *not* a
second grant database and not a schema-version bump: the plan core keeps its
v1 table/version/handling unchanged, while the grant table is a bounded
additive table materialized on first grant write and revalidated fail-closed
whenever it exists.  Grants are never written to ``project_plan_governance``
as fake Plan rows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from aota_forge.adapters.plan_authority.binding import (
    PlanAuthorityBinding,
    PlanAuthorityBindingError,
)
from aota_forge.governance.cross_project_grant import (
    GRANT_STATE_ACTIVE,
    GRANT_STATE_REVOKED,
    CrossProjectGrant,
    CrossProjectGrantAlreadyExistsError,
    CrossProjectGrantNotFoundError,
    CrossProjectGrantRecordError,
    StaleCrossProjectGrantRevisionError,
)
from aota_forge.governance.project_store import (
    ArchitectureMetadataRecord,
    PROJECT_GOVERNANCE_SCHEMA_VERSION,
    GovernanceMetadataAlreadyExistsError,
    GovernanceMetadataNotFoundError,
    PlanRecordAlreadyExistsError,
    PlanRecordNotFoundError,
    ProjectGovernanceCorruptStateError,
    ProjectGovernancePersistenceError,
    ProjectGovernanceRecordError,
    ProjectGovernanceStore,
    ProjectGovernanceStoreClosedError,
    ProjectPlanRecord,
    ProjectPlanUserGateRecord,
    StewardLogicalReplayRecord,
    StaleGovernanceMetadataRevisionError,
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

# AF #57 M3/W1 bounded additive foundation tables. The v1 plan table and
# schema marker stay unchanged; each table is materialized on first write.
_USER_GATE_TABLE = "project_plan_user_gates"
_USER_GATE_COLUMNS = (
    "project_id",
    "plan_id",
    "gate_kind",
    "gate_scope",
    "approval_ref",
    "approval_digest",
    "state",
    "revision",
)
_USER_GATE_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_USER_GATE_TABLE} (
    project_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    gate_kind TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    approval_ref TEXT NOT NULL,
    approval_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (project_id, plan_id)
)
"""
_USER_GATE_INSERT_SQL = (
    f"INSERT INTO {_USER_GATE_TABLE} ({', '.join(_USER_GATE_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _USER_GATE_COLUMNS)})"
)
_USER_GATE_SELECT_SQL = (
    f"SELECT {', '.join(_USER_GATE_COLUMNS)} FROM {_USER_GATE_TABLE} "
    "WHERE project_id = ? AND plan_id = ?"
)
_USER_GATE_UPDATE_SQL = (
    f"UPDATE {_USER_GATE_TABLE} SET gate_kind = ?, gate_scope = ?, approval_ref = ?, "
    "approval_digest = ?, state = ?, revision = ? "
    "WHERE project_id = ? AND plan_id = ? AND revision = ?"
)

_ARCHITECTURE_TABLE = "project_architecture_metadata"
_ARCHITECTURE_COLUMNS = (
    "project_id",
    "current_version",
    "current_digest",
    "accepted_delta_ref",
    "accepted_delta_digest",
    "promotion_receipt_ref",
    "revision",
)
_ARCHITECTURE_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_ARCHITECTURE_TABLE} (
    project_id TEXT NOT NULL PRIMARY KEY,
    current_version TEXT NOT NULL,
    current_digest TEXT NOT NULL,
    accepted_delta_ref TEXT,
    accepted_delta_digest TEXT,
    promotion_receipt_ref TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1)
)
"""
_ARCHITECTURE_INSERT_SQL = (
    f"INSERT INTO {_ARCHITECTURE_TABLE} ({', '.join(_ARCHITECTURE_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _ARCHITECTURE_COLUMNS)})"
)
_ARCHITECTURE_SELECT_SQL = (
    f"SELECT {', '.join(_ARCHITECTURE_COLUMNS)} FROM {_ARCHITECTURE_TABLE} "
    "WHERE project_id = ?"
)
_ARCHITECTURE_UPDATE_SQL = (
    f"UPDATE {_ARCHITECTURE_TABLE} SET current_version = ?, current_digest = ?, "
    "accepted_delta_ref = ?, accepted_delta_digest = ?, promotion_receipt_ref = ?, revision = ? "
    "WHERE project_id = ? AND revision = ?"
)

_REPLAY_TABLE = "steward_logical_replays"
_REPLAY_COLUMNS = (
    "project_id",
    "plan_id",
    "lineage_id",
    "request_digest",
    "checkpoint_ref",
    "checkpoint_digest",
    "state",
    "revision",
)
_REPLAY_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_REPLAY_TABLE} (
    project_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    checkpoint_ref TEXT NOT NULL,
    checkpoint_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (project_id, plan_id, lineage_id)
)
"""
_REPLAY_INSERT_SQL = (
    f"INSERT INTO {_REPLAY_TABLE} ({', '.join(_REPLAY_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _REPLAY_COLUMNS)})"
)
_REPLAY_SELECT_SQL = (
    f"SELECT {', '.join(_REPLAY_COLUMNS)} FROM {_REPLAY_TABLE} "
    "WHERE project_id = ? AND plan_id = ? AND lineage_id = ?"
)
_REPLAY_UPDATE_SQL = (
    f"UPDATE {_REPLAY_TABLE} SET request_digest = ?, checkpoint_ref = ?, "
    "checkpoint_digest = ?, state = ?, revision = ? "
    "WHERE project_id = ? AND plan_id = ? AND lineage_id = ? AND revision = ?"
)

# AF #57 M2/W4 additive bounded grant table (same store, no version bump; the
# plan core table, version marker, and semantics are unchanged).
_GRANT_TABLE = "cross_project_grants"
_GRANT_COLUMNS = (
    "grant_id",
    "requesting_project",
    "target_project",
    "root_kind",
    "capabilities",
    "bounded_scope",
    "authority_basis",
    "authority_anchor_plan_id",
    "authority_ref",
    "authority_digest",
    "end_condition",
    "bound_plan_id",
    "target_resolution_project_id",
    "target_resolution_digest",
    "state",
    "revision",
)
_GRANT_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_GRANT_TABLE} (
    grant_id TEXT NOT NULL PRIMARY KEY,
    requesting_project TEXT NOT NULL,
    target_project TEXT NOT NULL,
    root_kind TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    bounded_scope TEXT NOT NULL,
    authority_basis TEXT NOT NULL,
    authority_anchor_plan_id TEXT NOT NULL,
    authority_ref TEXT NOT NULL,
    authority_digest TEXT NOT NULL,
    end_condition TEXT NOT NULL,
    bound_plan_id TEXT,
    target_resolution_project_id TEXT NOT NULL,
    target_resolution_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1)
)
"""
_GRANT_INSERT_SQL = (
    f"INSERT INTO {_GRANT_TABLE} ({', '.join(_GRANT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _GRANT_COLUMNS)})"
)
_GRANT_SELECT_ONE_SQL = (
    f"SELECT {', '.join(_GRANT_COLUMNS)} FROM {_GRANT_TABLE} WHERE grant_id = ?"
)
_GRANT_SELECT_PROJECT_SQL = (
    f"SELECT {', '.join(_GRANT_COLUMNS)} FROM {_GRANT_TABLE} "
    "WHERE requesting_project = ? ORDER BY grant_id"
)
_GRANT_REVOKE_SQL = (
    f"UPDATE {_GRANT_TABLE} SET state = ?, revision = ? "
    "WHERE grant_id = ? AND revision = ?"
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
            existing_object = None
            if version == 0 and table is None:
                existing_object = self._connection.execute(
                    "SELECT type, name FROM sqlite_master LIMIT 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise ProjectGovernanceCorruptStateError(
                f"cannot inspect governance store schema: {exc}"
            ) from exc

        if version == 0 and table is None:
            if existing_object is not None:
                raise ProjectGovernanceCorruptStateError(
                    "non-empty governance store has no canonical schema; fail closed"
                )
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
        if self._grants_table_exists():
            try:
                grant_rows = self._connection.execute(
                    f"SELECT {', '.join(_GRANT_COLUMNS)} FROM {_GRANT_TABLE}"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernanceCorruptStateError(
                    f"cannot read governance grant records: {exc}"
                ) from exc
            for grant_row in grant_rows:
                self._row_to_grant(grant_row)
        if self._table_exists(_USER_GATE_TABLE):
            try:
                rows = self._connection.execute(
                    f"SELECT {', '.join(_USER_GATE_COLUMNS)} FROM {_USER_GATE_TABLE}"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernanceCorruptStateError(
                    f"cannot read user-gate records: {exc}"
                ) from exc
            for row in rows:
                self._row_to_user_gate(row)
        if self._table_exists(_ARCHITECTURE_TABLE):
            try:
                rows = self._connection.execute(
                    f"SELECT {', '.join(_ARCHITECTURE_COLUMNS)} FROM {_ARCHITECTURE_TABLE}"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernanceCorruptStateError(
                    f"cannot read architecture metadata: {exc}"
                ) from exc
            for row in rows:
                self._row_to_architecture(row)
        if self._table_exists(_REPLAY_TABLE):
            try:
                rows = self._connection.execute(
                    f"SELECT {', '.join(_REPLAY_COLUMNS)} FROM {_REPLAY_TABLE}"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernanceCorruptStateError(
                    f"cannot read steward replay records: {exc}"
                ) from exc
            active_replays: set[tuple[str, str]] = set()
            for row in rows:
                replay = self._row_to_replay(row)
                if replay.state == "active":
                    key = (replay.project_id, replay.plan_id)
                    if key in active_replays:
                        raise ProjectGovernanceCorruptStateError(
                            "corrupted steward replay state: multiple active lineages for "
                            f"{replay.project_id}/{replay.plan_id}"
                        )
                    active_replays.add(key)

    def _table_exists(self, table_name: str) -> bool:
        try:
            row = self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ProjectGovernanceCorruptStateError(
                f"cannot inspect governance table {table_name!r}: {exc}"
            ) from exc
        return row is not None

    def _grants_table_exists(self) -> bool:
        return self._table_exists(_GRANT_TABLE)

    def _ensure_grants_table(self) -> None:
        try:
            self._connection.execute(_GRANT_CREATE_TABLE_SQL)
        except sqlite3.Error as exc:
            raise ProjectGovernancePersistenceError(
                f"cannot materialize the bounded governance grant table: {exc}"
            ) from exc

    def _ensure_foundation_table(self, sql: str, table_name: str) -> None:
        try:
            self._connection.execute(sql)
        except sqlite3.Error as exc:
            raise ProjectGovernancePersistenceError(
                f"cannot materialize governance foundation table {table_name}: {exc}"
            ) from exc

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

    @staticmethod
    def _row_to_user_gate(row: sqlite3.Row) -> ProjectPlanUserGateRecord:
        try:
            return ProjectPlanUserGateRecord(
                project_id=row["project_id"],
                plan_id=row["plan_id"],
                gate_kind=row["gate_kind"],
                gate_scope=row["gate_scope"],
                approval_ref=row["approval_ref"],
                approval_digest=row["approval_digest"],
                state=row["state"],
                revision=row["revision"],
            )
        except Exception as exc:
            raise ProjectGovernanceCorruptStateError(
                f"corrupted user-gate record for project {row['project_id']!r} "
                f"plan {row['plan_id']!r}: {exc}"
            ) from exc

    @staticmethod
    def _row_to_architecture(row: sqlite3.Row) -> ArchitectureMetadataRecord:
        try:
            return ArchitectureMetadataRecord(
                project_id=row["project_id"],
                current_version=row["current_version"],
                current_digest=row["current_digest"],
                accepted_delta_ref=row["accepted_delta_ref"],
                accepted_delta_digest=row["accepted_delta_digest"],
                promotion_receipt_ref=row["promotion_receipt_ref"],
                revision=row["revision"],
            )
        except Exception as exc:
            raise ProjectGovernanceCorruptStateError(
                f"corrupted architecture metadata for project {row['project_id']!r}: {exc}"
            ) from exc

    @staticmethod
    def _row_to_replay(row: sqlite3.Row) -> StewardLogicalReplayRecord:
        try:
            return StewardLogicalReplayRecord(
                project_id=row["project_id"],
                plan_id=row["plan_id"],
                lineage_id=row["lineage_id"],
                request_digest=row["request_digest"],
                checkpoint_ref=row["checkpoint_ref"],
                checkpoint_digest=row["checkpoint_digest"],
                state=row["state"],
                revision=row["revision"],
            )
        except Exception as exc:
            raise ProjectGovernanceCorruptStateError(
                f"corrupted steward replay for project {row['project_id']!r} "
                f"plan {row['plan_id']!r} lineage {row['lineage_id']!r}: {exc}"
            ) from exc

    # -- grant row / record conversion (AF #57 M2/W4) ---------------------------

    @staticmethod
    def _encode_capabilities(record: CrossProjectGrant) -> str:
        return json.dumps(sorted(record.capabilities), ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _decode_capabilities(value: Any) -> frozenset[str]:
        if not isinstance(value, str):
            raise CrossProjectGrantRecordError("stored capabilities must be TEXT")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CrossProjectGrantRecordError("stored capabilities are not JSON") from exc
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) for item in decoded
        ):
            raise CrossProjectGrantRecordError("stored capabilities must be a JSON string list")
        return frozenset(decoded)

    @classmethod
    def _grant_params(cls, record: CrossProjectGrant) -> tuple[Any, ...]:
        return (
            record.grant_id,
            record.requesting_project,
            record.target_project,
            record.root_kind,
            cls._encode_capabilities(record),
            record.bounded_scope,
            record.authority.basis,
            record.authority.anchor_plan_id,
            record.authority.authority_ref,
            record.authority.authority_digest,
            record.end_condition,
            record.bound_plan_id or None,
            record.target_resolution.project_id,
            record.target_resolution.resolution_digest,
            record.state,
            record.revision,
        )

    @staticmethod
    def _row_to_grant(row: sqlite3.Row) -> CrossProjectGrant:
        grant_id = row["grant_id"]
        try:
            return CrossProjectGrant.from_dict(
                {
                    "grant_id": grant_id,
                    "requesting_project": row["requesting_project"],
                    "target_project": row["target_project"],
                    "root_kind": row["root_kind"],
                    "capabilities": sorted(
                        SQLiteProjectGovernanceStore._decode_capabilities(row["capabilities"])
                    ),
                    "bounded_scope": row["bounded_scope"],
                    "authority": {
                        "basis": row["authority_basis"],
                        "anchor_plan_id": row["authority_anchor_plan_id"],
                        "authority_ref": row["authority_ref"],
                        "authority_digest": row["authority_digest"],
                    },
                    "end_condition": row["end_condition"],
                    "bound_plan_id": row["bound_plan_id"] or "",
                    "target_resolution": {
                        "project_id": row["target_resolution_project_id"],
                        "status": "RESOLVED",
                        "candidate_count": 1,
                        "resolution_digest": row["target_resolution_digest"],
                    },
                    "state": row["state"],
                    "revision": row["revision"],
                }
            )
        except ProjectGovernanceCorruptStateError:
            raise
        except Exception as exc:
            raise ProjectGovernanceCorruptStateError(
                f"corrupted governance grant record {grant_id!r}: {exc}"
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

    # -- AF #57 M3/W1 bounded foundation records -------------------------------

    def put_user_gate(self, record: ProjectPlanUserGateRecord) -> ProjectPlanUserGateRecord:
        if not isinstance(record, ProjectPlanUserGateRecord):
            raise ProjectGovernanceRecordError("record must be a ProjectPlanUserGateRecord")
        if record.revision != 1:
            raise ProjectGovernanceRecordError("put_user_gate creates a record at revision 1")
        with self._lock:
            self._require_open()
            with self._write_transaction():
                self._ensure_foundation_table(_USER_GATE_CREATE_TABLE_SQL, _USER_GATE_TABLE)
                try:
                    self._connection.execute(
                        _USER_GATE_INSERT_SQL,
                        (
                            record.project_id,
                            record.plan_id,
                            record.gate_kind,
                            record.gate_scope,
                            record.approval_ref,
                            record.approval_digest,
                            record.state,
                            record.revision,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise GovernanceMetadataAlreadyExistsError(
                        f"user-gate record already exists: {record.project_id}/{record.plan_id}"
                    ) from exc
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"user-gate write failed: {exc}"
                    ) from exc
                self._inject_write_failure()
        return record

    def get_user_gate(
        self, project_id: str, plan_id: str
    ) -> ProjectPlanUserGateRecord | None:
        with self._lock:
            self._require_open()
            if not self._table_exists(_USER_GATE_TABLE):
                return None
            try:
                row = self._connection.execute(
                    _USER_GATE_SELECT_SQL, (project_id, plan_id)
                ).fetchone()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"user-gate read failed: {exc}"
                ) from exc
            return None if row is None else self._row_to_user_gate(row)

    def compare_and_swap_user_gate(
        self,
        project_id: str,
        plan_id: str,
        expected_revision: int,
        *,
        state: str | None = None,
        approval_ref: str | None = None,
        approval_digest: str | None = None,
    ) -> ProjectPlanUserGateRecord:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ProjectGovernanceRecordError("expected_revision must be positive")
        if (approval_ref is None) != (approval_digest is None):
            raise ProjectGovernanceRecordError(
                "approval_ref and approval_digest must be updated together"
            )
        with self._lock:
            self._require_open()
            if not self._table_exists(_USER_GATE_TABLE):
                raise GovernanceMetadataNotFoundError(
                    f"user-gate record not found: {project_id}/{plan_id}"
                )
            with self._write_transaction():
                try:
                    row = self._connection.execute(
                        _USER_GATE_SELECT_SQL, (project_id, plan_id)
                    ).fetchone()
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"user-gate read failed: {exc}"
                    ) from exc
                if row is None:
                    raise GovernanceMetadataNotFoundError(
                        f"user-gate record not found: {project_id}/{plan_id}"
                    )
                current = self._row_to_user_gate(row)
                if current.revision != expected_revision:
                    raise StaleGovernanceMetadataRevisionError(
                        f"user-gate revision {current.revision} does not match expected "
                        f"revision {expected_revision}"
                    )
                candidate = ProjectPlanUserGateRecord(
                    project_id=current.project_id,
                    plan_id=current.plan_id,
                    gate_kind=current.gate_kind,
                    gate_scope=current.gate_scope,
                    approval_ref=approval_ref if approval_ref is not None else current.approval_ref,
                    approval_digest=(
                        approval_digest if approval_digest is not None else current.approval_digest
                    ),
                    state=state if state is not None else current.state,
                    revision=current.revision + 1,
                )
                try:
                    cursor = self._connection.execute(
                        _USER_GATE_UPDATE_SQL,
                        (
                            candidate.gate_kind,
                            candidate.gate_scope,
                            candidate.approval_ref,
                            candidate.approval_digest,
                            candidate.state,
                            candidate.revision,
                            project_id,
                            plan_id,
                            expected_revision,
                        ),
                    )
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"user-gate write failed: {exc}"
                    ) from exc
                if cursor.rowcount != 1:
                    raise StaleGovernanceMetadataRevisionError(
                        f"user-gate changed during mutation: {project_id}/{plan_id}"
                    )
                self._inject_write_failure()
        return candidate

    def put_architecture_metadata(
        self, record: ArchitectureMetadataRecord
    ) -> ArchitectureMetadataRecord:
        if not isinstance(record, ArchitectureMetadataRecord):
            raise ProjectGovernanceRecordError("record must be an ArchitectureMetadataRecord")
        if record.revision != 1:
            raise ProjectGovernanceRecordError(
                "put_architecture_metadata creates a record at revision 1"
            )
        with self._lock:
            self._require_open()
            with self._write_transaction():
                self._ensure_foundation_table(_ARCHITECTURE_CREATE_TABLE_SQL, _ARCHITECTURE_TABLE)
                try:
                    self._connection.execute(
                        _ARCHITECTURE_INSERT_SQL,
                        (
                            record.project_id,
                            record.current_version,
                            record.current_digest,
                            record.accepted_delta_ref,
                            record.accepted_delta_digest,
                            record.promotion_receipt_ref,
                            record.revision,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise GovernanceMetadataAlreadyExistsError(
                        f"architecture metadata already exists: {record.project_id}"
                    ) from exc
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"architecture metadata write failed: {exc}"
                    ) from exc
                self._inject_write_failure()
        return record

    def get_architecture_metadata(
        self, project_id: str
    ) -> ArchitectureMetadataRecord | None:
        with self._lock:
            self._require_open()
            if not self._table_exists(_ARCHITECTURE_TABLE):
                return None
            try:
                row = self._connection.execute(
                    _ARCHITECTURE_SELECT_SQL, (project_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"architecture metadata read failed: {exc}"
                ) from exc
            return None if row is None else self._row_to_architecture(row)

    def compare_and_swap_architecture_metadata(
        self,
        project_id: str,
        expected_revision: int,
        *,
        current_version: str | None = None,
        current_digest: str | None = None,
        accepted_delta_ref: str | None = None,
        accepted_delta_digest: str | None = None,
        promotion_receipt_ref: str | None = None,
    ) -> ArchitectureMetadataRecord:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ProjectGovernanceRecordError("expected_revision must be positive")
        with self._lock:
            self._require_open()
            if not self._table_exists(_ARCHITECTURE_TABLE):
                raise GovernanceMetadataNotFoundError(
                    f"architecture metadata not found: {project_id}"
                )
            with self._write_transaction():
                try:
                    row = self._connection.execute(
                        _ARCHITECTURE_SELECT_SQL, (project_id,)
                    ).fetchone()
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"architecture metadata read failed: {exc}"
                    ) from exc
                if row is None:
                    raise GovernanceMetadataNotFoundError(
                        f"architecture metadata not found: {project_id}"
                    )
                current = self._row_to_architecture(row)
                if current.revision != expected_revision:
                    raise StaleGovernanceMetadataRevisionError(
                        f"architecture metadata revision {current.revision} does not match "
                        f"expected revision {expected_revision}"
                    )
                candidate = ArchitectureMetadataRecord(
                    project_id=current.project_id,
                    current_version=current_version or current.current_version,
                    current_digest=current_digest or current.current_digest,
                    accepted_delta_ref=(
                        accepted_delta_ref
                        if accepted_delta_ref is not None
                        else current.accepted_delta_ref
                    ),
                    accepted_delta_digest=(
                        accepted_delta_digest
                        if accepted_delta_digest is not None
                        else current.accepted_delta_digest
                    ),
                    promotion_receipt_ref=promotion_receipt_ref or current.promotion_receipt_ref,
                    revision=current.revision + 1,
                )
                try:
                    cursor = self._connection.execute(
                        _ARCHITECTURE_UPDATE_SQL,
                        (
                            candidate.current_version,
                            candidate.current_digest,
                            candidate.accepted_delta_ref,
                            candidate.accepted_delta_digest,
                            candidate.promotion_receipt_ref,
                            candidate.revision,
                            project_id,
                            expected_revision,
                        ),
                    )
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"architecture metadata write failed: {exc}"
                    ) from exc
                if cursor.rowcount != 1:
                    raise StaleGovernanceMetadataRevisionError(
                        f"architecture metadata changed during mutation: {project_id}"
                    )
                self._inject_write_failure()
        return candidate

    def put_steward_replay(
        self, record: StewardLogicalReplayRecord
    ) -> StewardLogicalReplayRecord:
        if not isinstance(record, StewardLogicalReplayRecord):
            raise ProjectGovernanceRecordError("record must be a StewardLogicalReplayRecord")
        if record.revision != 1:
            raise ProjectGovernanceRecordError(
                "put_steward_replay creates a record at revision 1"
            )
        with self._lock:
            self._require_open()
            with self._write_transaction():
                self._ensure_foundation_table(_REPLAY_CREATE_TABLE_SQL, _REPLAY_TABLE)
                if record.state == "active":
                    active = self._connection.execute(
                        f"SELECT 1 FROM {_REPLAY_TABLE} WHERE project_id = ? AND plan_id = ? "
                        "AND state = 'active' LIMIT 1",
                        (record.project_id, record.plan_id),
                    ).fetchone()
                    if active is not None:
                        raise GovernanceMetadataAlreadyExistsError(
                            f"an active steward replay already exists for "
                            f"{record.project_id}/{record.plan_id}"
                        )
                try:
                    self._connection.execute(
                        _REPLAY_INSERT_SQL,
                        (
                            record.project_id,
                            record.plan_id,
                            record.lineage_id,
                            record.request_digest,
                            record.checkpoint_ref,
                            record.checkpoint_digest,
                            record.state,
                            record.revision,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise GovernanceMetadataAlreadyExistsError(
                        f"steward replay already exists: {record.project_id}/"
                        f"{record.plan_id}/{record.lineage_id}"
                    ) from exc
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"steward replay write failed: {exc}"
                    ) from exc
                self._inject_write_failure()
        return record

    def get_steward_replay(
        self, project_id: str, plan_id: str, lineage_id: str
    ) -> StewardLogicalReplayRecord | None:
        with self._lock:
            self._require_open()
            if not self._table_exists(_REPLAY_TABLE):
                return None
            try:
                row = self._connection.execute(
                    _REPLAY_SELECT_SQL, (project_id, plan_id, lineage_id)
                ).fetchone()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"steward replay read failed: {exc}"
                ) from exc
            return None if row is None else self._row_to_replay(row)

    def compare_and_swap_steward_replay(
        self,
        project_id: str,
        plan_id: str,
        lineage_id: str,
        expected_revision: int,
        *,
        state: str | None = None,
        checkpoint_ref: str | None = None,
        checkpoint_digest: str | None = None,
    ) -> StewardLogicalReplayRecord:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ProjectGovernanceRecordError("expected_revision must be positive")
        if (checkpoint_ref is None) != (checkpoint_digest is None):
            raise ProjectGovernanceRecordError(
                "checkpoint_ref and checkpoint_digest must be updated together"
            )
        with self._lock:
            self._require_open()
            if not self._table_exists(_REPLAY_TABLE):
                raise GovernanceMetadataNotFoundError(
                    f"steward replay not found: {project_id}/{plan_id}/{lineage_id}"
                )
            with self._write_transaction():
                row = self._connection.execute(
                    _REPLAY_SELECT_SQL, (project_id, plan_id, lineage_id)
                ).fetchone()
                if row is None:
                    raise GovernanceMetadataNotFoundError(
                        f"steward replay not found: {project_id}/{plan_id}/{lineage_id}"
                    )
                current = self._row_to_replay(row)
                if current.revision != expected_revision:
                    raise StaleGovernanceMetadataRevisionError(
                        f"steward replay revision {current.revision} does not match "
                        f"expected revision {expected_revision}"
                    )
                candidate = StewardLogicalReplayRecord(
                    project_id=current.project_id,
                    plan_id=current.plan_id,
                    lineage_id=current.lineage_id,
                    request_digest=current.request_digest,
                    checkpoint_ref=checkpoint_ref or current.checkpoint_ref,
                    checkpoint_digest=checkpoint_digest or current.checkpoint_digest,
                    state=state if state is not None else current.state,
                    revision=current.revision + 1,
                )
                if candidate.state == "active":
                    active = self._connection.execute(
                        f"SELECT 1 FROM {_REPLAY_TABLE} WHERE project_id = ? AND plan_id = ? "
                        "AND state = 'active' AND lineage_id != ? LIMIT 1",
                        (project_id, plan_id, lineage_id),
                    ).fetchone()
                    if active is not None:
                        raise GovernanceMetadataAlreadyExistsError(
                            f"an active steward replay already exists for "
                            f"{project_id}/{plan_id}"
                        )
                cursor = self._connection.execute(
                    _REPLAY_UPDATE_SQL,
                    (
                        candidate.request_digest,
                        candidate.checkpoint_ref,
                        candidate.checkpoint_digest,
                        candidate.state,
                        candidate.revision,
                        project_id,
                        plan_id,
                        lineage_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleGovernanceMetadataRevisionError(
                        f"steward replay changed during mutation: {project_id}/"
                        f"{plan_id}/{lineage_id}"
                    )
                self._inject_write_failure()
        return candidate

    # -- cross-project grants (AF #57 M2/W4) -------------------------------------

    def put_cross_project_grant(self, record: CrossProjectGrant) -> CrossProjectGrant:
        if not isinstance(record, CrossProjectGrant):
            raise CrossProjectGrantRecordError("record must be a CrossProjectGrant")
        if record.revision != 1:
            raise CrossProjectGrantRecordError(
                "put_cross_project_grant creates a new record at revision 1; "
                "revocation is the only mutation"
            )
        with self._lock:
            self._require_open()
            with self._write_transaction():
                self._ensure_grants_table()
                try:
                    self._connection.execute(_GRANT_INSERT_SQL, self._grant_params(record))
                except sqlite3.IntegrityError as exc:
                    raise CrossProjectGrantAlreadyExistsError(
                        f"cross-project grant already exists: {record.grant_id}"
                    ) from exc
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"grant write failed: {exc}"
                    ) from exc
                self._inject_write_failure()
        return record

    def get_cross_project_grant(self, grant_id: str) -> CrossProjectGrant | None:
        with self._lock:
            self._require_open()
            if not self._grants_table_exists():
                return None
            try:
                row = self._connection.execute(
                    _GRANT_SELECT_ONE_SQL, (grant_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"grant read failed: {exc}"
                ) from exc
            if row is None:
                return None
            return self._row_to_grant(row)

    def list_cross_project_grants(
        self, requesting_project: str, *, active_only: bool = False
    ) -> tuple[CrossProjectGrant, ...]:
        with self._lock:
            self._require_open()
            if not self._grants_table_exists():
                return ()
            try:
                rows = self._connection.execute(
                    _GRANT_SELECT_PROJECT_SQL, (requesting_project,)
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProjectGovernancePersistenceError(
                    f"grant read failed: {exc}"
                ) from exc
            grants = tuple(self._row_to_grant(row) for row in rows)
            if active_only:
                grants = tuple(
                    grant for grant in grants if grant.state == GRANT_STATE_ACTIVE
                )
            return grants

    def revoke_cross_project_grant(
        self, grant_id: str, expected_revision: int
    ) -> CrossProjectGrant:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise CrossProjectGrantRecordError("expected_revision must be a positive integer")
        with self._lock:
            self._require_open()
            with self._write_transaction():
                self._ensure_grants_table()
                try:
                    row = self._connection.execute(
                        _GRANT_SELECT_ONE_SQL, (grant_id,)
                    ).fetchone()
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"grant read failed: {exc}"
                    ) from exc
                if row is None:
                    raise CrossProjectGrantNotFoundError(
                        f"cross-project grant not found: {grant_id}"
                    )
                current = self._row_to_grant(row)
                if current.revision != expected_revision:
                    raise StaleCrossProjectGrantRevisionError(
                        f"cross-project grant {grant_id} revision {current.revision} "
                        f"does not match expected revision {expected_revision}"
                    )
                if current.state != GRANT_STATE_ACTIVE:
                    raise CrossProjectGrantRecordError(
                        f"cross-project grant {grant_id} is already {current.state}"
                    )
                candidate = replace(current, state=GRANT_STATE_REVOKED, revision=current.revision + 1)
                try:
                    cursor = self._connection.execute(
                        _GRANT_REVOKE_SQL,
                        (candidate.state, candidate.revision, grant_id, expected_revision),
                    )
                except sqlite3.Error as exc:
                    raise ProjectGovernancePersistenceError(
                        f"grant write failed: {exc}"
                    ) from exc
                if cursor.rowcount != 1:
                    raise StaleCrossProjectGrantRevisionError(
                        f"cross-project grant {grant_id} changed during mutation; fail closed"
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
