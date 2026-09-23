"""AF #001 M1/R3A — existing local Plan metadata rehydration proof."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
)
from aota_forge.composition.project_binding import resolve_trusted_project_binding
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectGovernanceCorruptStateError,
    ProjectPlanRecord,
)
from aota_forge.governance.rehydration import (
    DIRECT_SQLITE_MUTATION_USED,
    FAKE_HISTORICAL_RECORD_REVISION,
    FAKE_PLAN_INIT_JOURNAL_EVENT,
    NEW_PUBLIC_OPERATION,
    PLAN_AUTHORITY_MUTATED,
    PlanRecordRehydrationError,
    RAW_PUT_PLAN_OPERATOR_REPAIR,
    RAW_SQL_INSERT_USED,
    REHYDRATION_IS_PLAN_INIT,
    SECOND_GOVERNANCE_STORE,
    SECOND_PLAN_MUTATION_PROTOCOL,
    rehydrate_existing_local_plan_record,
)
from aota_forge.governance.intake import GOVERNANCE_PLAN_MODES
from aota_forge.mcp_transport import MCP_PUBLIC_TOOL_COUNT
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


PROJECT_ID = "aota_forge_r3a"
PLAN_ID = "plan_001_r3a_fixture"

MANIFEST = f"""schema_version: 1
project:
  id: {PROJECT_ID}
  name: R3A fixture
  kind: test
  status: active
summary: bounded R3A fixture
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph
plan:
  active_plan_id: null
constraints: []
"""

PLAN_BODY = f"""# R3A local authority fixture

## Plan Identity

```text
PROJECT_ID={PROJECT_ID}
PLAN_ID=
{PLAN_ID}
PLAN_REVISION=R3
PLAN_STATUS=active
```

## Current State

```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=ready
```

The fixture has no M1 DAG because R3A restores metadata only.
"""


def _fixture(tmp_path: Path, *, body: str = PLAN_BODY):
    project_root = tmp_path / "source"
    (project_root / ".aota").mkdir(parents=True)
    (project_root / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    governance_base = tmp_path / "plans"
    (governance_base / PROJECT_ID / PLAN_ID).mkdir(parents=True)
    (governance_base / PROJECT_ID / PLAN_ID / "plan.md").write_text(body, encoding="utf-8")
    store_path = project_root / ".aota" / "governance.sqlite3"
    store_path.write_bytes(b"")
    project_binding = resolve_trusted_project_binding(
        project_id=PROJECT_ID,
        workspace_root=project_root,
    )
    governance_root = LocalGovernanceRootBinding.from_trusted_base(
        governance_base,
        project_id=PROJECT_ID,
    )
    return project_root, governance_base, store_path, project_binding, governance_root


def _destination(
    governance_root: LocalGovernanceRootBinding,
    plan_id: str = PLAN_ID,
) -> LocalPlanAuthorityDestination:
    expected_ref = object_ref_subject(
        make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)
    )
    return LocalPlanAuthorityDestination(governance_root, plan_id, expected_ref)


def _authority(governance_root: LocalGovernanceRootBinding, **overrides) -> PlanAuthorityBinding:
    destination = _destination(governance_root)
    values = {
        "plan_id": PLAN_ID,
        "source_kind": PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        "authority_ref": destination.authority_ref,
    }
    values.update(overrides)
    return PlanAuthorityBinding(**values)


def _rehydrate(
    project_binding,
    governance_root: LocalGovernanceRootBinding,
    store,
    *,
    project_id: str = PROJECT_ID,
    plan_id: str = PLAN_ID,
    authority_binding: PlanAuthorityBinding | None = None,
) -> ProjectPlanRecord:
    return rehydrate_existing_local_plan_record(
        project_id=project_id,
        plan_id=plan_id,
        project_binding=project_binding,
        governance_root=governance_root,
        governance_store=store,
        authority_binding=authority_binding,
    )


def test_zero_byte_target_rehydrates_without_plan_mutation_or_projection(tmp_path: Path) -> None:
    project_root, governance_base, store_path, project_binding, governance_root = _fixture(tmp_path)
    before = (governance_base / PROJECT_ID / PLAN_ID / "plan.md").read_bytes()
    before_digest = hashlib.sha256(before).hexdigest()
    store = open_project_governance_store(store_path)
    try:
        record = _rehydrate(project_binding, governance_root, store)
        assert store_path.stat().st_size > 0
        assert record.project_id == PROJECT_ID
        assert record.plan_id == PLAN_ID
        assert record.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert record.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert record.authority.authority_ref == f"local-governance:{PROJECT_ID}/{PLAN_ID}"
        assert record.authority.source_revision == before_digest
        assert record.authority.source_digest == before_digest
        assert record.revision == 1
        assert hashlib.sha256(
            (governance_base / PROJECT_ID / PLAN_ID / "plan.md").read_bytes()
        ).hexdigest() == before_digest
        assert not (governance_base / PROJECT_ID / "MAP.md").exists()
        assert not (governance_base / PROJECT_ID / "STATUS.md").exists()
    finally:
        store.close()

    reopened = open_project_governance_store(store_path)
    try:
        assert reopened.get_plan(PROJECT_ID, PLAN_ID) == record
    finally:
        reopened.close()

    script = """
import sys
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
store = SQLiteProjectGovernanceStore(sys.argv[1])
record = store.get_plan(sys.argv[2], sys.argv[3])
assert record is not None
assert record.lifecycle_state == "active"
assert record.authority.source_kind == "local_governance"
assert record.authority.authority_ref == f"local-governance:{sys.argv[2]}/{sys.argv[3]}"
assert record.authority.source_revision == sys.argv[4]
assert record.authority.source_digest == sys.argv[4]
assert record.revision == 1
store.close()
print("R3A_FRESH_PROCESS_READBACK_PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(store_path), PROJECT_ID, PLAN_ID, before_digest],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "R3A_FRESH_PROCESS_READBACK_PASS" in completed.stdout


def test_rehydration_replay_is_exactly_idempotent(tmp_path: Path) -> None:
    _, _, store_path, project_binding, governance_root = _fixture(tmp_path)
    store = open_project_governance_store(store_path)
    try:
        first = _rehydrate(project_binding, governance_root, store)
        second = _rehydrate(project_binding, governance_root, store)
        assert second == first
        assert second.revision == 1
        assert len(store.list_plans(PROJECT_ID)) == 1
    finally:
        store.close()


def test_authority_change_before_write_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, governance_base, store_path, project_binding, governance_root = _fixture(tmp_path)
    authority_path = governance_base / PROJECT_ID / PLAN_ID / "plan.md"
    original_load = LocalPlanAuthorityReadAdapter.load
    load_count = 0

    def load_then_change(adapter):
        nonlocal load_count
        snapshot = original_load(adapter)
        load_count += 1
        if load_count == 1:
            authority_path.write_text(authority_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return snapshot

    monkeypatch.setattr(LocalPlanAuthorityReadAdapter, "load", load_then_change)
    store = open_project_governance_store(store_path)
    try:
        with pytest.raises(PlanRecordRehydrationError) as excinfo:
            _rehydrate(project_binding, governance_root, store)
        assert excinfo.value.code == "PLAN_AUTHORITY_CHANGED_DURING_REHYDRATION"
        assert store.list_plans(PROJECT_ID) == ()
    finally:
        store.close()


def test_conflicting_record_fails_closed_without_mutation(tmp_path: Path) -> None:
    _, _, store_path, project_binding, governance_root = _fixture(tmp_path)
    store = open_project_governance_store(store_path)
    conflicting = ProjectPlanRecord(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
        authority=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="owner/repo#1",
        ),
    )
    try:
        store.put_plan(conflicting)
        with pytest.raises(PlanRecordRehydrationError) as excinfo:
            _rehydrate(project_binding, governance_root, store)
        assert excinfo.value.code == "CONFLICTING_PROJECT_PLAN_RECORD"
        assert store.get_plan(PROJECT_ID, PLAN_ID) == conflicting
    finally:
        store.close()


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("foreign_project", "LOCAL_AUTHORITY_INVALID"),
        ("foreign_plan", "AUTHORITY_DOCUMENT_MISSING"),
        ("missing_authority", "AUTHORITY_DOCUMENT_MISSING"),
        ("wrong_authority_kind", "AUTHORITY_BINDING_MISMATCH"),
        ("source_digest_mismatch", "AUTHORITY_BINDING_STALE"),
    ),
)
def test_identity_authority_and_source_guards_fail_closed(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    _, governance_base, store_path, project_binding, governance_root = _fixture(tmp_path)
    store = open_project_governance_store(store_path)
    try:
        authority = None
        project_id = PROJECT_ID
        plan_id = PLAN_ID
        if case == "foreign_project":
            project_id = "foreign_project"
        elif case == "foreign_plan":
            plan_id = "plan_foreign"
        elif case == "missing_authority":
            (governance_base / PROJECT_ID / PLAN_ID / "plan.md").unlink()
        elif case == "wrong_authority_kind":
            authority = _authority(
                governance_root,
                source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                authority_ref="owner/repo#1",
            )
        elif case == "source_digest_mismatch":
            authority = _authority(governance_root, source_digest="a" * 64)
        with pytest.raises(PlanRecordRehydrationError) as excinfo:
            _rehydrate(
                project_binding,
                governance_root,
                store,
                project_id=project_id,
                plan_id=plan_id,
                authority_binding=authority,
            )
        assert excinfo.value.code == expected
        assert store.list_plans(PROJECT_ID) == ()
    finally:
        store.close()


def test_retired_plan_is_not_rehydratable_as_active(tmp_path: Path) -> None:
    _, _, store_path, project_binding, governance_root = _fixture(
        tmp_path,
        body=PLAN_BODY.replace("PLAN_STATUS=active", "PLAN_STATUS=retired"),
    )
    store = open_project_governance_store(store_path)
    try:
        with pytest.raises(PlanRecordRehydrationError) as excinfo:
            _rehydrate(project_binding, governance_root, store)
        assert excinfo.value.code == "PLAN_LIFECYCLE_NOT_REHYDRATABLE_AS_ACTIVE"
        assert store.list_plans(PROJECT_ID) == ()
    finally:
        store.close()


def test_corrupt_nonempty_target_is_still_rejected_by_existing_adapter(tmp_path: Path) -> None:
    _, _, store_path, _, _ = _fixture(tmp_path)
    store_path.write_bytes(b"not a sqlite database")
    with pytest.raises(ProjectGovernanceCorruptStateError):
        open_project_governance_store(store_path)


def test_plan_authority_reader_and_project_resolver_are_the_trusted_inputs(tmp_path: Path) -> None:
    _, governance_base, store_path, project_binding, governance_root = _fixture(tmp_path)
    store = open_project_governance_store(store_path)
    try:
        record = _rehydrate(project_binding, governance_root, store)
        destination = _destination(governance_root)
        reader = LocalPlanAuthorityReadAdapter(destination, binding=record.authority)
        snapshot = reader.load()
        assert snapshot.body == PLAN_BODY
        assert project_binding.project_id == PROJECT_ID
        assert project_binding.resolution.status == "RESOLVED"
        assert len(project_binding.resolution.candidates) == 1
        assert Path(record.authority.authority_ref).name == PLAN_ID
        assert str(governance_base) not in record.authority.authority_ref
    finally:
        store.close()


def test_r3a_negative_guards_preserve_the_existing_governance_boundaries() -> None:
    assert REHYDRATION_IS_PLAN_INIT is False
    assert PLAN_AUTHORITY_MUTATED is False
    assert SECOND_GOVERNANCE_STORE is False
    assert SECOND_PLAN_MUTATION_PROTOCOL is False
    assert RAW_SQL_INSERT_USED is False
    assert DIRECT_SQLITE_MUTATION_USED is False
    assert RAW_PUT_PLAN_OPERATOR_REPAIR is False
    assert FAKE_HISTORICAL_RECORD_REVISION is False
    assert FAKE_PLAN_INIT_JOURNAL_EVENT is False
    assert NEW_PUBLIC_OPERATION is False
    assert MCP_PUBLIC_TOOL_COUNT == 1
    assert "rehydrate" not in GOVERNANCE_PLAN_MODES
