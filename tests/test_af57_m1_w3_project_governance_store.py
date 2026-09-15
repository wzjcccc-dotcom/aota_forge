"""AF #57 M1/W3 — Minimal Project Governance Store & Manifest State Decoupling (V1/V2).

One coherent W3 proof surface for the frozen Governance 2.0 boundaries:

  W3-A  storage-neutral Project Governance Store port + one SQLite adapter
  W3-B  minimum project/Plan governance record (identity + Plan lifecycle +
        reused W1 PlanAuthorityBinding + CAS revision)
  W3-C  atomic mutation, revision/CAS, stale-mutation fail-closed
  W3-D  close/reopen durability, including real-process reopen
  W3-E  malformed/corrupt state fails closed with bounded errors
  W3-F  explicit negative ownership (no execution/coordinator/progression
        duplication) and no ExecutionStateStore / Coordinator replacement
  W3-G  ``.aota/project.yaml plan.active_plan_id`` compatibility-only
        reconciliation: retained schema-v1 field, non-authoritative, never
        read by governance logic as Plan lifecycle truth
  W3-H  bounded composition seam (no W2 hot-path wiring; W4 owns integration)

PROVES (V1 behavior/type-level and V2 local integration over real modules):

* record validation (identity grammar, lifecycle states, authority binding
  consistency, bounded revision);
* create-only revision 1, CAS transitions, stale mutation rejection with
  unchanged state, explicit failure codes;
* atomicity: an injected failure inside a write transaction rolls back
  completely (no partial state);
* invalid/corrupt persisted state (garbage bytes, unsupported schema version,
  missing table, malformed rows) fails closed at open;
* same-process close/reopen and new real-process reopen recover the same
  project/Plan binding, lifecycle state, and revision; CAS remains valid;
* the implementation imports neither ExecutionStateStore nor
  TaskMainCoordinatorState and redefines neither domain;
* schema v1 of the project manifest keeps ``plan.active_plan_id`` validated
  for compatibility while governance truth stays store-owned.

DOES_NOT_PROVE:

* does not implement or prove W2 local-governance root / local Plan adapter;
* does not implement or prove W4 cross-cutting runtime wiring, plan-aware
  worktree layout, or the Governance 1.x cutover;
* does not run the integrated Governance V3 review (RV1 owns it);
* does not prove production runtime behavior.
"""

from __future__ import annotations

import ast
import dataclasses
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.core.execution.durable_state import DurableExecutionRecord
from aota_forge.core.project import manifest as project_manifest
from aota_forge.core.project.manifest import (
    ACTIVE_PLAN_ID_IS_AUTHORITY,
    ACTIVE_PLAN_ID_IS_PLAN_LIFECYCLE_TRUTH,
    ACTIVE_PLAN_ID_RETAINED_FOR_SCHEMA_V1_COMPATIBILITY,
    PROJECT_MANIFEST_OWNS_ACTIVE_PLAN_STATE,
    ProjectManifestInvalidError,
    validate_project,
)
from aota_forge.governance import project_store as governance
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    PLAN_LIFECYCLE_STATES,
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
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState

REPO_ROOT = Path(__file__).resolve().parents[1]
AF_ROOT = REPO_ROOT / "aota_forge"
GOVERNANCE_PACKAGE = AF_ROOT / "governance"
COMPOSITION_SEAM = AF_ROOT / "composition" / "project_governance.py"

W3_SOURCE_FILES = (
    GOVERNANCE_PACKAGE / "__init__.py",
    GOVERNANCE_PACKAGE / "project_store.py",
    GOVERNANCE_PACKAGE / "sqlite_store.py",
    COMPOSITION_SEAM,
)

GOVERNANCE_TABLE = "project_plan_governance"
GOVERNANCE_COLUMNS = {
    "project_id",
    "plan_id",
    "lifecycle_state",
    "source_kind",
    "authority_ref",
    "source_revision",
    "source_digest",
    "revision",
}

LEGACY_UPPERCASE_PLAN_ID = "plan_20260729T075202_ff38a6a0"

_REOPEN_SCRIPT = """
import sys

from aota_forge.governance.project_store import PLAN_LIFECYCLE_RETIRED
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

store = SQLiteProjectGovernanceStore(sys.argv[1])
record = store.get_plan("aota_forge", "plan_af57_pilot")
assert record is not None, "W3_REOPEN_MISSING"
assert record.revision == 2, f"W3_REOPEN_REVISION={record.revision}"
assert record.lifecycle_state == "active", f"W3_REOPEN_STATE={record.lifecycle_state}"
assert record.authority.source_kind == "local_governance", record.authority.source_kind
assert record.authority.authority_ref == "aota_forge/plans", record.authority.authority_ref
assert record.authority.source_revision == "r7", record.authority.source_revision
assert record.authority.source_digest == "b" * 64, record.authority.source_digest

updated = store.compare_and_swap_plan(
    "aota_forge", "plan_af57_pilot", 2, lifecycle_state=PLAN_LIFECYCLE_RETIRED
)
assert updated.revision == 3, updated.revision
store.close()
print(f"W3_REOPEN_OK revision={updated.revision} state={updated.lifecycle_state}")
"""


def _binding(
    plan_id: str = "plan_af57_pilot",
    *,
    source_kind: str = PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    authority_ref: str = "aota_forge/plans",
    source_revision: str | int | None = "r7",
    source_digest: str | None = "b" * 64,
) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=source_kind,
        authority_ref=authority_ref,
        source_revision=source_revision,
        source_digest=source_digest,
    )


def _record(
    project_id: str = "aota_forge",
    plan_id: str = "plan_af57_pilot",
    *,
    lifecycle_state: str = PLAN_LIFECYCLE_ACTIVE,
    authority: PlanAuthorityBinding | None = None,
    revision: int = 1,
) -> ProjectPlanRecord:
    return ProjectPlanRecord(
        project_id=project_id,
        plan_id=plan_id,
        lifecycle_state=lifecycle_state,
        authority=authority if authority is not None else _binding(plan_id),
        revision=revision,
    )


def _store(tmp_path: Path, name: str = "governance.sqlite3") -> SQLiteProjectGovernanceStore:
    return SQLiteProjectGovernanceStore(tmp_path / name)


def _manifest_document(active_plan_id):
    return {
        "schema_version": 1,
        "project": {"id": "proj", "name": "proj", "kind": "test", "status": "active"},
        "summary": "bounded manifest",
        "capabilities": [],
        "paths": {
            "source_root": ".",
            "source": [],
            "docs": [],
            "scripts": [],
            "profiles": [],
            "skills": [],
            "tests": [],
        },
        "commands": {"validate": [], "deploy": [], "verify_deploy": []},
        "runtime": {"deployment_type": "manual", "requires_human_checkpoint": False},
        "codegraph": {"enabled": False, "index_location": ".codegraph/"},
        "plan": {"active_plan_id": active_plan_id},
        "constraints": [],
    }


def _raw_connection(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def _corrupt_row(db_path: Path, column: str, value: object) -> None:
    connection = _raw_connection(db_path)
    try:
        connection.execute(f"UPDATE {GOVERNANCE_TABLE} SET {column} = ?", (value,))
        connection.commit()
    finally:
        connection.close()


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestRecordValidation:
    """W3-B: minimum record validation with reused W1 authority semantics."""

    def test_valid_record_shape_and_default_revision(self):
        record = _record()
        assert {item.name for item in dataclasses.fields(ProjectPlanRecord)} == {
            "project_id",
            "plan_id",
            "lifecycle_state",
            "authority",
            "revision",
        }
        assert record.revision == 1
        assert record.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert record.authority.plan_id == record.plan_id
        assert isinstance(record.authority, PlanAuthorityBinding)

    def test_project_identity_validation(self):
        for value in ("aota_forge", "aota-forge", "aota_forge-2", "x"):
            record = ProjectPlanRecord(
                project_id=value,
                plan_id="plan_af57_pilot",
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=_binding(),
            )
            assert record.project_id == value
        for value in ("", "  ", "AOTA", "aota forge", "aota/forge", "aota#57", "x" * 97, None, 57):
            with pytest.raises(ProjectGovernanceRecordError):
                ProjectPlanRecord(
                    project_id=value,
                    plan_id="plan_af57_pilot",
                    lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                    authority=_binding(),
                )

    def test_plan_identity_validation_uses_w1_canonical_grammar(self):
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord(
                project_id="aota_forge",
                plan_id=LEGACY_UPPERCASE_PLAN_ID,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=_binding(),
            )
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord(
                project_id="aota_forge",
                plan_id="57",
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=_binding("plan_af57_pilot"),
            )

    def test_lifecycle_state_is_plan_level_and_bounded(self):
        assert PLAN_LIFECYCLE_STATES == {"active", "retired"}
        for value in ("pending", "in-progress", "completed", "blocked", "M1", "", None):
            with pytest.raises(ProjectGovernanceRecordError):
                ProjectPlanRecord(
                    project_id="aota_forge",
                    plan_id="plan_af57_pilot",
                    lifecycle_state=value,
                    authority=_binding(),
                )

    def test_authority_binding_reuse_and_plan_consistency(self):
        for value in (None, {"source_kind": "local_governance"}, "plan_af57_pilot"):
            with pytest.raises(ProjectGovernanceRecordError):
                ProjectPlanRecord(
                    project_id="aota_forge",
                    plan_id="plan_af57_pilot",
                    lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                    authority=value,
                )
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord(
                project_id="aota_forge",
                plan_id="plan_af57_pilot",
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=_binding("plan_other"),
            )

    def test_revision_must_be_a_positive_integer(self):
        for value in (0, -1, True, "1", None):
            with pytest.raises(ProjectGovernanceRecordError):
                ProjectPlanRecord(
                    project_id="aota_forge",
                    plan_id="plan_af57_pilot",
                    lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                    authority=_binding(),
                    revision=value,
                )

    @pytest.mark.parametrize("source_revision", [7, "r7", None])
    def test_dict_round_trip_is_lossless(self, source_revision):
        record = _record(authority=_binding(source_revision=source_revision))
        assert ProjectPlanRecord.from_dict(record.to_dict()) == record

    def test_from_dict_rejects_unknown_and_missing_fields(self):
        payload = _record().to_dict()
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord.from_dict({**payload, "milestone": "M1"})
        incomplete = {key: value for key, value in payload.items() if key != "lifecycle_state"}
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord.from_dict(incomplete)
        with pytest.raises(ProjectGovernanceRecordError):
            ProjectPlanRecord.from_dict({**payload, "authority": "local_governance"})


class TestRevisionAndCas:
    """W3-C: create-only revision 1, CAS transitions, stale fail-closed."""

    def test_create_is_revision_one_and_duplicate_fails(self, tmp_path: Path):
        store = _store(tmp_path)
        created = store.put_plan(_record())
        assert created.revision == 1
        assert store.get_plan("aota_forge", "plan_af57_pilot") == created
        with pytest.raises(PlanRecordAlreadyExistsError):
            store.put_plan(_record())
        with pytest.raises(ProjectGovernanceRecordError):
            store.put_plan(_record(revision=2))
        store.close()

    def test_cas_lifecycle_transition_and_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        updated = store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        assert updated.revision == 2
        assert updated.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        assert store.get_plan("aota_forge", "plan_af57_pilot") == updated
        with pytest.raises(PlanRecordNotFoundError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_missing", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )
        with pytest.raises(PlanRecordNotFoundError):
            store.compare_and_swap_plan(
                "other_project", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )
        store.close()

    def test_cas_authority_rebind_keeps_internal_plan_identity(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(
            _record(
                authority=_binding(
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
                    source_revision=7,
                    source_digest=None,
                )
            )
        )
        rebound = store.compare_and_swap_plan(
            "aota_forge",
            "plan_af57_pilot",
            1,
            authority=_binding(source_revision="r8"),
        )
        assert rebound.plan_id == "plan_af57_pilot"
        assert rebound.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert rebound.authority.authority_ref == "aota_forge/plans"
        assert rebound.revision == 2
        store.close()

    def test_stale_mutation_is_rejected_and_state_is_preserved(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        with pytest.raises(StalePlanRevisionError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_ACTIVE
            )
        preserved = store.get_plan("aota_forge", "plan_af57_pilot")
        assert preserved is not None
        assert preserved.revision == 2
        assert preserved.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        store.close()

    def test_cas_requires_an_explicit_update(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        with pytest.raises(ProjectGovernanceRecordError):
            store.compare_and_swap_plan("aota_forge", "plan_af57_pilot", 1)
        with pytest.raises(ProjectGovernanceRecordError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 0, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )
        assert store.get_plan("aota_forge", "plan_af57_pilot").revision == 1
        store.close()

    def test_invalid_cas_update_fails_closed_without_write(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        with pytest.raises(ProjectGovernanceRecordError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, lifecycle_state="in-progress"
            )
        with pytest.raises(ProjectGovernanceRecordError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, authority=_binding("plan_other")
            )
        preserved = store.get_plan("aota_forge", "plan_af57_pilot")
        assert preserved is not None
        assert preserved.revision == 1
        assert preserved.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        store.close()

    def test_list_plans_is_scoped_and_sorted(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.list_plans("aota_forge") == ()
        store.put_plan(_record(plan_id="plan_zz"))
        store.put_plan(_record(plan_id="plan_aa"))
        store.put_plan(_record(project_id="other_project", plan_id="plan_cc"))
        listed = store.list_plans("aota_forge")
        assert [record.plan_id for record in listed] == ["plan_aa", "plan_zz"]
        assert store.list_plans("other_project")[0].plan_id == "plan_cc"
        store.close()


class TestAtomicity:
    """W3-C: a failing transaction leaves no partial state."""

    def test_put_plan_rolls_back_on_injected_write_failure(self, tmp_path: Path):
        store = _store(tmp_path)
        store.inject_fail_next_write()
        with pytest.raises(ProjectGovernancePersistenceError):
            store.put_plan(_record())
        assert store.get_plan("aota_forge", "plan_af57_pilot") is None
        store.put_plan(_record())
        assert store.get_plan("aota_forge", "plan_af57_pilot").revision == 1
        store.close()

    def test_cas_rolls_back_on_injected_write_failure(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        store.inject_fail_next_write()
        with pytest.raises(ProjectGovernancePersistenceError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )
        preserved = store.get_plan("aota_forge", "plan_af57_pilot")
        assert preserved is not None
        assert preserved.revision == 1
        assert preserved.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        retried = store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        assert retried.revision == 2
        store.close()

    def test_closed_store_operations_fail_closed(self, tmp_path: Path):
        store = _store(tmp_path)
        store.put_plan(_record())
        store.close()
        store.close()
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.get_plan("aota_forge", "plan_af57_pilot")
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.put_plan(_record(plan_id="plan_af57_two"))
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.list_plans("aota_forge")
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )


class TestDurabilityReopen:
    """W3-D: same-process and real-process reopen recover binding + revision."""

    def test_same_process_close_reopen_recovers_binding_and_revision(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(
            _record(
                authority=_binding(
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
                    source_revision=7,
                    source_digest=None,
                )
            )
        )
        store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, authority=_binding(source_revision="r7")
        )
        store.close()

        reopened = SQLiteProjectGovernanceStore(db)
        recovered = reopened.get_plan("aota_forge", "plan_af57_pilot")
        assert recovered is not None
        assert recovered.revision == 2
        assert recovered.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert recovered.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert recovered.authority.source_revision == "r7"
        # the recovered CAS token remains valid; a stale token does not
        with pytest.raises(StalePlanRevisionError):
            reopened.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
            )
        retired = reopened.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 2, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        assert retired.revision == 3
        reopened.close()

    def test_real_process_reopen_recovers_and_cas_remains_valid(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(
            _record(
                authority=_binding(
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
                    source_revision=7,
                    source_digest=None,
                )
            )
        )
        store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, authority=_binding(source_revision="r7")
        )
        store.close()

        completed = subprocess.run(
            [sys.executable, "-c", _REOPEN_SCRIPT, str(db)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        assert "W3_REOPEN_OK revision=3 state=retired" in completed.stdout

        reopened = SQLiteProjectGovernanceStore(db)
        recovered = reopened.get_plan("aota_forge", "plan_af57_pilot")
        assert recovered is not None
        assert recovered.revision == 3
        assert recovered.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        assert recovered.authority.authority_ref == "aota_forge/plans"
        with pytest.raises(StalePlanRevisionError):
            reopened.compare_and_swap_plan(
                "aota_forge", "plan_af57_pilot", 2, lifecycle_state=PLAN_LIFECYCLE_ACTIVE
            )
        assert reopened.get_plan("aota_forge", "plan_af57_pilot").revision == 3
        reopened.close()


class TestFailClosedCorruption:
    """W3-E: malformed or corrupt persisted state fails closed with bounds."""

    def test_fresh_file_is_initialized_safely(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.close()
        assert db.is_file()
        connection = _raw_connection(db)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == PROJECT_GOVERNANCE_SCHEMA_VERSION
        finally:
            connection.close()

    def test_garbage_bytes_fail_closed(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        db.write_bytes(b"this is not a sqlite database")
        with pytest.raises(ProjectGovernanceCorruptStateError):
            SQLiteProjectGovernanceStore(db)

    def test_unopenable_path_fails_with_bounded_persistence_error(self, tmp_path: Path):
        with pytest.raises(ProjectGovernancePersistenceError):
            SQLiteProjectGovernanceStore(
                tmp_path / "missing" / "governance.sqlite3", ensure_dir=False
            )

    def test_unsupported_schema_version_fails_closed(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.close()
        connection = _raw_connection(db)
        try:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(ProjectGovernanceCorruptStateError):
            SQLiteProjectGovernanceStore(db)

    def test_declared_version_without_table_fails_closed(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        connection = sqlite3.connect(str(db))
        try:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(ProjectGovernanceCorruptStateError):
            SQLiteProjectGovernanceStore(db)

    @pytest.mark.parametrize(
        ("column", "value"),
        (
            ("project_id", "AOTA"),
            ("plan_id", "plan_AF57"),
            ("lifecycle_state", "pending"),
            ("source_kind", "sqlite"),
            ("source_revision", "not-json"),
            ("source_revision", "true"),
            ("source_digest", "not-a-digest"),
        ),
    )
    def test_malformed_row_fails_closed_on_reopen(self, tmp_path: Path, column, value):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_record())
        store.close()
        _corrupt_row(db, column, value)
        with pytest.raises(ProjectGovernanceCorruptStateError):
            SQLiteProjectGovernanceStore(db)


class TestNegativeOwnership:
    """W3-F: no duplicated execution/coordinator/progression ownership."""

    def test_module_negative_ownership_guards(self):
        assert governance.PROJECT_GOVERNANCE_STORE_IMPLEMENTED is True
        assert governance.PROJECT_GOVERNANCE_STORE_SCOPE == "project_and_cross_plan_governance_only"
        for name in (
            "PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS",
            "PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS",
            "PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION",
            "PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE",
            "PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE",
            "GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE",
            "GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE",
        ):
            assert getattr(governance, name) is False

    def test_sqlite_schema_contains_only_governance_state(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.close()
        connection = _raw_connection(db)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({GOVERNANCE_TABLE})")
            }
        finally:
            connection.close()
        assert tables == {GOVERNANCE_TABLE}
        assert columns == GOVERNANCE_COLUMNS

    def test_record_holds_no_execution_or_coordinator_truth(self):
        governance_fields = {item.name for item in dataclasses.fields(ProjectPlanRecord)}
        forbidden_tokens = (
            "attempt",
            "worker",
            "result",
            "delivery",
            "brake",
            "queue",
            "dispatch",
            "milestone",
            "blocker",
            "next_action",
            "frontier",
        )
        assert not [
            name
            for name in governance_fields
            if any(token in name for token in forbidden_tokens)
        ]
        execution_fields = {item.name for item in dataclasses.fields(DurableExecutionRecord)}
        coordinator_fields = {item.name for item in dataclasses.fields(TaskMainCoordinatorState)}
        assert {"dispatch_attempt_id", "execution_phase", "terminal_result", "delivery_state"} <= execution_fields
        assert {"human_brake", "attempt_states", "work_projections", "open_blockers", "next_action"} <= coordinator_fields
        execution_owned = {
            "canonical_task_id",
            "canonical_task_state",
            "dispatch_attempt_id",
            "execution_phase",
            "terminal_result",
            "delivery_state",
        }
        coordinator_owned = {
            "human_brake",
            "attempt_states",
            "work_projections",
            "open_blockers",
            "next_action",
            "frontier_ref",
        }
        assert not governance_fields & (execution_owned | coordinator_owned)

    def test_implementation_does_not_require_execution_or_coordinator_replacement(self):
        forbidden = (
            "aota_forge.core.execution.durable_state",
            "aota_forge.runtime.task_main.coordinator_state",
        )
        for path in W3_SOURCE_FILES:
            imports = _direct_imports(path)
            for name in imports:
                assert not any(name.startswith(prefix) for prefix in forbidden), (path, name)


class TestManifestStateDecoupling:
    """W3-G: active_plan_id is schema-v1 compatibility only, never authority."""

    def test_manifest_declares_non_authoritative_compatibility_field(self):
        assert project_manifest.SCHEMA_VERSION == 1
        assert PROJECT_MANIFEST_OWNS_ACTIVE_PLAN_STATE is False
        assert ACTIVE_PLAN_ID_IS_AUTHORITY is False
        assert ACTIVE_PLAN_ID_IS_PLAN_LIFECYCLE_TRUTH is False
        assert ACTIVE_PLAN_ID_RETAINED_FOR_SCHEMA_V1_COMPATIBILITY is True

    def test_schema_v1_field_retained_and_validated(self):
        validate_project(_manifest_document(None))
        validate_project(_manifest_document("plan_af57_pilot"))
        with pytest.raises(ProjectManifestInvalidError):
            validate_project(_manifest_document(LEGACY_UPPERCASE_PLAN_ID))
        missing_field = _manifest_document(None)
        del missing_field["plan"]["active_plan_id"]
        with pytest.raises(ProjectManifestInvalidError):
            validate_project(missing_field)

    def test_governance_source_never_reads_active_plan_id(self):
        for path in W3_SOURCE_FILES:
            text = path.read_text(encoding="utf-8")
            assert "active_plan_id" not in text, path

    def test_store_plan_lifecycle_truth_is_independent_of_manifest_pointer(self, tmp_path: Path):
        # A manifest pointer never creates, retires, or otherwise alters
        # governance state; store lifecycle truth stands on its own.
        manifest = _manifest_document("plan_other_plan")
        validate_project(manifest)
        store = _store(tmp_path)
        assert store.get_plan("aota_forge", "plan_other_plan") is None
        store.put_plan(_record())
        active = store.get_plan("aota_forge", "plan_af57_pilot")
        assert active is not None
        assert active.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        retired = store.compare_and_swap_plan(
            "aota_forge", "plan_af57_pilot", 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        assert retired.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        assert manifest["plan"]["active_plan_id"] == "plan_other_plan"
        assert store.get_plan("aota_forge", "plan_other_plan") is None
        store.close()


class TestCompositionIsolation:
    """W3-H: bounded seam, no W2 hot-path wiring, port surface only."""

    def test_port_surface_is_minimal(self):
        assert ProjectGovernanceStore.__abstractmethods__ == {
            "put_plan",
            "get_plan",
            "list_plans",
            "compare_and_swap_plan",
            "close",
        }

    def test_seam_returns_the_port_backed_by_the_sqlite_adapter(self, tmp_path: Path):
        store = open_project_governance_store(tmp_path / "governance.sqlite3")
        assert isinstance(store, ProjectGovernanceStore)
        assert isinstance(store, SQLiteProjectGovernanceStore)
        store.put_plan(_record())
        assert store.get_plan("aota_forge", "plan_af57_pilot").revision == 1
        store.close()

    def test_w3_source_does_not_wire_w2_hot_paths(self):
        forbidden_prefixes = (
            "aota_forge.work_plane.authorized_roots",
            "aota_forge.composition.thin_task_main_host",
            "aota_forge.adapters.plan_authority.port",
            "aota_forge.adapters.plan_authority.github",
            "aota_forge.adapters.plan_authority.github_read",
            "aota_forge.adapters.plan_authority.fake",
        )
        for path in W3_SOURCE_FILES:
            imports = _direct_imports(path)
            for name in imports:
                assert not any(name.startswith(prefix) for prefix in forbidden_prefixes), (
                    path,
                    name,
                )

    def test_w3_source_uses_the_reused_w1_binding_contract(self):
        imports = _direct_imports(GOVERNANCE_PACKAGE / "project_store.py")
        assert "aota_forge.adapters.plan_authority.binding" in imports
