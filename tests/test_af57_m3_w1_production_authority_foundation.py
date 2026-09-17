"""AF #57 M3/W1 production authority foundation proof.

The tests stay bounded to the W1 slice: local authority cutover/recovery,
shared Plan safety, durable user-gate/architecture/replay metadata, and the
trusted lookup seam. They do not exercise downstream workflow behavior or
GitHub writes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority import (
    PlanAuthoritySnapshot,
    StaticPlanAuthorityAdapter,
)
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.fake_github import FixtureGitHubStore
from aota_forge.adapters.plan_authority.github import GitHubAuthorityAdapter
from aota_forge.adapters.plan_authority.local_governance import (
    ABSENT_RAW_DOCUMENT_DIGEST,
    LocalPlanAuthorityAdapter,
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    local_plan_authority_reference,
)
from aota_forge.adapters.plan_authority.port import PortablePlanMutationRequest
from aota_forge.composition.plan_authority import resolve_bound_plan_authority
from aota_forge.composition.task_main_daily_launcher import (
    DailyLaunchContext,
    DailyTaskMainLauncher,
    TaskMainSessionContinuationError,
)
from aota_forge.composition.task_main_runtime_selection import (
    build_thin_task_main_binding_from_envelope_bootstrap,
    materialize_thin_task_main_bootstrap,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.model import JournalState
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.governance.local_lifecycle import (
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecyclePhase,
    LocalPlanLifecycleRequest,
)
from aota_forge.governance.project_store import (
    ArchitectureMetadataRecord,
    GovernanceMetadataAlreadyExistsError,
    ProjectGovernanceCorruptStateError,
    ProjectPlanRecord,
    ProjectPlanUserGateRecord,
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    StewardLogicalReplayRecord,
    StaleGovernanceMetadataRevisionError,
    STEWARD_REPLAY_STATE_COMPLETED,
    USER_GATE_STATE_REVOKED,
    architecture_authority_reference,
)
from aota_forge.governance.cross_project_grant import UserGateApproval
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


PROJECT_ID = "af57m3w1"
PLAN_ID = "plan_af57_m3_w1"
SOURCE_AUTHORITY_REF = "wzjcccc-dotcom/aota-hermes-tools#57"

PLAN_BODY = """# [PLAN] AF #57 M3/W1 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_KIND=portable_plan
PROJECT_ID=af57m3w1
PLAN_STATUS=active
CURRENT_MILESTONE=M3
M3_STATUS=in_progress
M3_USER_APPROVAL_SATISFIED=yes
M3_DAG=W1 -> W2
M3_WORK_ITEMS=W1, W2
```

## M3

#### M3/W1 - Production authority foundation
Bounded foundation proof.
"""

HOST_MANIFEST = """schema_version: 1
project:
  id: af57m3w1
  name: af57
  kind: test
  status: active
summary: af57
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target(plan_id: str = PLAN_ID):
    return object_ref_subject(
        make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)
    )


def _destination(tmp_path: Path) -> LocalPlanAuthorityDestination:
    base = tmp_path / "governance"
    scope = base / PROJECT_ID
    scope.mkdir(parents=True)
    return LocalPlanAuthorityDestination(
        governance_root=LocalGovernanceRootBinding.from_trusted_base(
            base, project_id=PROJECT_ID
        ),
        plan_id=PLAN_ID,
        expected_ref=_target(),
    )


def _source_binding() -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
        authority_ref=SOURCE_AUTHORITY_REF,
        source_revision="source-r1",
        source_digest=_digest(PLAN_BODY),
    )


def _request(
    destination: LocalPlanAuthorityDestination,
    key: str = "cutover-key",
    *,
    expected_plan_revision: int | None = None,
):
    return LocalPlanLifecycleRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        destination=destination,
        principal="operator-af57",
        authorization_reference="approval-af57",
        lease_reference="lease-af57",
        idempotency_key=key,
        correlation_id=f"correlation-{key}",
        intent_fingerprint=_digest(f"intent:{key}"),
        expected_plan_revision=expected_plan_revision,
    )


def _seed_source_plan(store: SQLiteProjectGovernanceStore) -> PlanAuthorityBinding:
    binding = _source_binding()
    store.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=binding,
        )
    )
    return binding


def _stores(tmp_path: Path):
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "journal.json")
    return governance, journal


def _close(*stores) -> None:
    for store in stores:
        store.close()


class _FakeHostClient:
    def dispatch(self, payload):
        return {"adapter_handle": "fake", "status": "running"}


def test_cutover_materializes_then_switches_and_records_terminal_evidence(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )

    result = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        _request(destination), source_binding, source
    )

    assert result.ok is True
    assert destination.plan_document_path().read_text(encoding="utf-8") == PLAN_BODY
    assert result.plan_record is not None
    assert result.plan_record.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
    assert result.journal_entry is not None
    assert result.journal_entry.record.journal_state is JournalState.VERIFIED_RECOVERED
    assert result.journal_entry.record.evidence["authority_switch"] == "complete"
    assert result.journal_entry.record.evidence["cutover_phase"] == "verified"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).revision == 2
    _close(governance, journal)


def test_cutover_accepts_operator_bound_github_adapter(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = GitHubAuthorityAdapter(
        FixtureGitHubStore(body=PLAN_BODY, revision="source-r1"),
        plan_authority=SOURCE_AUTHORITY_REF,
    )

    result = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        _request(destination, key="github-cutover"), source_binding, source
    )

    assert result.ok is True
    assert result.plan_record is not None
    assert result.plan_record.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
    assert destination.plan_document_path().read_text(encoding="utf-8") == PLAN_BODY
    _close(governance, journal)


def test_cutover_rejects_source_without_exact_bound_identity(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(PLAN_BODY, revision="source-r1")

    result = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        _request(destination), source_binding, source
    )

    assert result.phase is LocalPlanLifecyclePhase.FAILED_CLOSED
    assert result.error_code == "SOURCE_AUTHORITY_MISMATCH"
    assert not destination.plan_document_path().exists()
    assert governance.get_plan(PROJECT_ID, PLAN_ID).authority == source_binding
    _close(governance, journal)


def test_cutover_recovery_after_durable_prepared_before_external_attempt(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )
    journal.inject_fail_before_applying()
    request = _request(destination, key="prepared-crash")
    first = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        request, source_binding, source
    )
    assert first.ok is False
    assert first.journal_entry is not None
    assert first.journal_entry.record.journal_state is JournalState.PREPARED
    assert not destination.plan_document_path().exists()

    _close(governance, journal)
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "journal.json")
    recovered = LocalPlanLifecycleCoordinator(governance, journal).recover(
        request, source_binding=source_binding, source_adapter=source
    )

    assert recovered.ok is False
    assert recovered.phase is LocalPlanLifecyclePhase.FAILED_CLOSED
    assert recovered.error_code == "JOURNAL_PERSISTENCE_FAILURE_BEFORE_EXTERNAL_WRITE"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).authority == source_binding
    assert not destination.plan_document_path().exists()
    _close(governance, journal)


def test_prepared_recovery_is_reconciled_by_a_fresh_process(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )
    journal.inject_fail_before_applying()
    request = _request(destination, key="fresh-process-prepared")
    first = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        request, source_binding, source
    )
    assert first.journal_entry is not None
    assert first.journal_entry.record.journal_state is JournalState.PREPARED
    _close(governance, journal)

    script = f"""
from pathlib import Path
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import LocalPlanAuthorityDestination
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.governance.local_lifecycle import LocalPlanLifecycleCoordinator, LocalPlanLifecycleRequest
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

project_id, plan_id, governance_base, governance_path, journal_path = __import__('sys').argv[1:]
target = object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))
destination = LocalPlanAuthorityDestination(
    LocalGovernanceRootBinding.from_trusted_base(Path(governance_base), project_id=project_id),
    plan_id,
    target,
)
source_binding = PlanAuthorityBinding(
    plan_id=plan_id,
    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    authority_ref={SOURCE_AUTHORITY_REF!r},
    source_revision='source-r1',
    source_digest={_digest(PLAN_BODY)!r},
)
request = LocalPlanLifecycleRequest(
    project_id=project_id,
    plan_id=plan_id,
    destination=destination,
    principal='operator-af57',
    authorization_reference='approval-af57',
    lease_reference='lease-af57',
    idempotency_key='fresh-process-prepared',
    correlation_id='correlation-fresh-process-prepared',
    intent_fingerprint={_digest('intent:fresh-process-prepared')!r},
)
source = StaticPlanAuthorityAdapter(
    {PLAN_BODY!r}, revision='source-r1', digest={_digest(PLAN_BODY)!r},
    plan_authority={SOURCE_AUTHORITY_REF!r},
)
governance = SQLiteProjectGovernanceStore(governance_path)
journal = FileBackedDurableJournalStore(journal_path)
result = LocalPlanLifecycleCoordinator(governance, journal).recover(
    request, source_binding=source_binding, source_adapter=source,
)
print(result.error_code or 'OK')
governance.close()
journal.close()
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            PROJECT_ID,
            PLAN_ID,
            str(destination.governance_root.governance_base),
            str(tmp_path / "governance.sqlite3"),
            str(tmp_path / "journal.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "JOURNAL_PERSISTENCE_FAILURE_BEFORE_EXTERNAL_WRITE"
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    assert governance.get_plan(PROJECT_ID, PLAN_ID).authority == source_binding
    assert not destination.plan_document_path().exists()
    governance.close()


def test_cutover_recovery_after_materialization_before_binding_cas(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )
    governance.inject_fail_next_write()
    request = _request(destination, key="binding-crash")
    first = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        request, source_binding, source
    )
    assert first.ok is False
    assert first.error_code == "AUTHORITY_SWITCH_PERSISTENCE_UNKNOWN"
    assert destination.plan_document_path().is_file()
    assert governance.get_plan(PROJECT_ID, PLAN_ID).authority == source_binding
    assert first.journal_entry is not None
    assert first.journal_entry.record.evidence["authority_switch"] == "pending"

    _close(governance, journal)
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "journal.json")
    recovered = LocalPlanLifecycleCoordinator(governance, journal).recover(
        request, source_binding=source_binding, source_adapter=source
    )

    assert recovered.ok is True
    assert recovered.plan_record is not None
    assert recovered.plan_record.authority.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
    assert recovered.journal_entry is not None
    assert recovered.journal_entry.record.evidence["authority_switch"] == "complete"
    _close(governance, journal)


def test_identical_cutover_replays_one_durable_lineage_without_rewriting(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )
    request = _request(destination, key="replay", expected_plan_revision=1)
    coordinator = LocalPlanLifecycleCoordinator(governance, journal)
    first = coordinator.cutover(request, source_binding, source)
    before = destination.plan_document_path().stat().st_mtime_ns
    second = coordinator.cutover(request, source_binding, source)

    assert first.ok is True
    assert second.ok is True
    assert destination.plan_document_path().stat().st_mtime_ns == before
    assert len(journal.list_all()) == 1
    assert governance.get_plan(PROJECT_ID, PLAN_ID).revision == 2
    _close(governance, journal)


def test_duplicate_durable_idempotency_matches_fail_closed(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)
    source = StaticPlanAuthorityAdapter(
        PLAN_BODY,
        revision="source-r1",
        digest=_digest(PLAN_BODY),
        plan_authority=SOURCE_AUTHORITY_REF,
    )
    request = _request(destination, key="duplicate-lineage")
    coordinator = LocalPlanLifecycleCoordinator(governance, journal)
    first = coordinator.cutover(request, source_binding, source)
    assert first.journal_entry is not None
    duplicate = replace(
        first.journal_entry.record,
        journal_id="local-lifecycle-duplicate",
        attempt_id="attempt-duplicate",
        journal_state=JournalState.PREPARED,
        observed_raw_digest=None,
        observed_revision=None,
        verification_state=None,
    )
    journal.create_prepared(duplicate)

    conflict = coordinator.cutover(request, source_binding, source)

    assert conflict.ok is False
    assert conflict.phase is LocalPlanLifecyclePhase.FAILED_CLOSED
    assert conflict.error_code == "IDEMPOTENCY_CONFLICT"
    _close(governance, journal)


def test_source_drift_after_materialization_keeps_old_binding(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    source_binding = _seed_source_plan(governance)

    class DriftingSource(StaticPlanAuthorityAdapter):
        def __init__(self):
            super().__init__(
                PLAN_BODY,
                revision="source-r1",
                digest=_digest(PLAN_BODY),
                plan_authority=SOURCE_AUTHORITY_REF,
            )
            self.calls = 0

        def load(self) -> PlanAuthoritySnapshot:
            self.calls += 1
            if self.calls == 1:
                return super().load()
            changed = PLAN_BODY.replace("M3_STATUS=in_progress", "M3_STATUS=completed")
            return PlanAuthoritySnapshot(
                body=changed,
                revision="source-r2",
                digest=_digest(changed),
            )

    source = DriftingSource()
    result = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        _request(destination, key="source-drift"), source_binding, source
    )

    assert result.ok is False
    assert result.error_code == "SOURCE_CHANGED_AFTER_MATERIALIZATION"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).authority == source_binding
    assert destination.plan_document_path().is_file()
    _close(governance, journal)


def test_destination_cannot_pair_one_plan_id_with_another_plan_ref(tmp_path: Path):
    base = tmp_path / "governance"
    scope = base / PROJECT_ID
    scope.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(base, project_id=PROJECT_ID)
    with pytest.raises(ValueError, match="identify the destination plan_id"):
        LocalPlanAuthorityDestination(binding, PLAN_ID, _target("plan_other"))


def test_store_foundation_records_are_durable_cas_and_bounded(tmp_path: Path):
    db = tmp_path / "foundation.sqlite3"
    store = SQLiteProjectGovernanceStore(db)
    connection = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert tables == {"project_plan_governance"}

    gate = ProjectPlanUserGateRecord.from_approval(
        PROJECT_ID,
        PLAN_ID,
        "milestone_close",
        "M3",
        UserGateApproval("approval:af57m3w1", "a" * 64),
    )
    architecture = ArchitectureMetadataRecord(
        PROJECT_ID,
        "v1",
        "b" * 64,
        accepted_delta_ref="delta:af57m3w1",
        accepted_delta_digest="c" * 64,
        promotion_receipt_ref="receipt:af57m3w1",
    )
    replay = StewardLogicalReplayRecord(
        PROJECT_ID,
        PLAN_ID,
        "lineage-af57",
        "d" * 64,
        "checkpoint:af57m3w1",
        "e" * 64,
    )
    store.put_user_gate(gate)
    store.put_architecture_metadata(architecture)
    store.put_steward_replay(replay)
    assert architecture.authority_ref == architecture_authority_reference(PROJECT_ID)
    assert store.compare_and_swap_user_gate(
        PROJECT_ID, PLAN_ID, 1, state=USER_GATE_STATE_REVOKED
    ).revision == 2
    assert store.compare_and_swap_architecture_metadata(
        PROJECT_ID, 1, current_version="v2"
    ).revision == 2
    assert store.compare_and_swap_steward_replay(
        PROJECT_ID,
        PLAN_ID,
        "lineage-af57",
        1,
        state=STEWARD_REPLAY_STATE_COMPLETED,
    ).revision == 2
    with pytest.raises(StaleGovernanceMetadataRevisionError):
        store.compare_and_swap_user_gate(PROJECT_ID, PLAN_ID, 1, state="required")
    _close(store)

    reopened = SQLiteProjectGovernanceStore(db)
    assert reopened.get_user_gate(PROJECT_ID, PLAN_ID).state == USER_GATE_STATE_REVOKED
    assert reopened.get_architecture_metadata(PROJECT_ID).current_version == "v2"
    assert (
        reopened.get_steward_replay(PROJECT_ID, PLAN_ID, "lineage-af57").state
        == STEWARD_REPLAY_STATE_COMPLETED
    )
    _close(reopened)


def test_steward_replay_allows_at_most_one_active_lineage(tmp_path: Path):
    store = SQLiteProjectGovernanceStore(tmp_path / "foundation.sqlite3")
    store.put_steward_replay(
        StewardLogicalReplayRecord(
            PROJECT_ID,
            PLAN_ID,
            "lineage-one",
            "a" * 64,
            "checkpoint:one",
            "b" * 64,
        )
    )
    with pytest.raises(GovernanceMetadataAlreadyExistsError):
        store.put_steward_replay(
            StewardLogicalReplayRecord(
                PROJECT_ID,
                PLAN_ID,
                "lineage-two",
                "c" * 64,
                "checkpoint:two",
                "d" * 64,
            )
        )
    store.compare_and_swap_steward_replay(
        PROJECT_ID,
        PLAN_ID,
        "lineage-one",
        1,
        state=STEWARD_REPLAY_STATE_COMPLETED,
    )
    created = store.put_steward_replay(
        StewardLogicalReplayRecord(
            PROJECT_ID,
            PLAN_ID,
            "lineage-two",
            "c" * 64,
            "checkpoint:two",
            "d" * 64,
        )
    )
    assert created.state == "active"
    _close(store)


def test_bound_local_lookup_requires_durable_record(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    coordinator = LocalPlanLifecycleCoordinator(governance, journal)
    result = coordinator.initialize(_request(destination, key="initialize"), PLAN_BODY)
    assert result.ok is True
    assert result.plan_record is not None
    binding = result.plan_record.authority
    reader = resolve_bound_plan_authority(
        binding,
        local_destination=destination,
        governance_store=governance,
    )
    assert isinstance(reader, LocalPlanAuthorityReadAdapter)
    assert reader.plan_authority == binding.authority_ref
    assert reader.load().body == PLAN_BODY
    assert governance.get_plan(PROJECT_ID, PLAN_ID).lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    _close(governance, journal)


def test_thin_host_resolves_durable_local_authority_before_exposing_reader(
    tmp_path: Path, operator_config_file: Path
):
    worktree = tmp_path / "worktree"
    (worktree / ".aota").mkdir(parents=True)
    (worktree / ".aota" / "project.yaml").write_text(HOST_MANIFEST, encoding="utf-8")
    governance_base = tmp_path / "plans"
    (governance_base / PROJECT_ID).mkdir(parents=True)
    database = tmp_path / "governance.sqlite3"
    governance = SQLiteProjectGovernanceStore(database)
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN_ID),
    )
    governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=binding,
        )
    )
    governance.close()

    try:
        host = compose_thin_task_main_host(
            worktree_root=worktree,
            project_id=PROJECT_ID,
            worktree_id="wt-af57-m3-w1",
            runtime_config_path=operator_config_file,
            origin_task_main_session_ref="20260917_af57_m3_w1_session",
            host_client=_FakeHostClient(),
            plan_id=PLAN_ID,
            governance_base=governance_base,
            governance_store_path=database,
        )
        assert host.plan_authority_binding == binding
        assert host.plan_authority_reader is not None
        assert host.plan_authority_reader.plan_authority == binding.authority_ref
        assert host.governance_store is not None
    finally:
        if "host" in locals() and host.governance_store is not None:
            host.governance_store.close()
        reset_execution_dispatcher()


def test_local_retirement_is_store_only_and_does_not_require_destination_path(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    result = LocalPlanLifecycleCoordinator(governance, journal).initialize(
        _request(destination, key="retire"), PLAN_BODY
    )
    assert result.ok is True
    retired = LocalPlanLifecycleCoordinator(governance, journal).retire(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_revision=result.plan_record.revision,
    )
    assert retired.ok is True
    assert retired.plan_record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    _close(governance, journal)


def test_cutover_preserves_exact_crlf_plan_bytes(tmp_path: Path):
    destination = _destination(tmp_path)
    governance, journal = _stores(tmp_path)
    crlf_body = PLAN_BODY.replace("\n", "\r\n")
    source_binding = replace(_source_binding(), source_digest=_digest(crlf_body))
    governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=source_binding,
        )
    )
    source = StaticPlanAuthorityAdapter(
        crlf_body,
        revision="source-r1",
        digest=_digest(crlf_body),
        plan_authority=SOURCE_AUTHORITY_REF,
    )

    result = LocalPlanLifecycleCoordinator(governance, journal).cutover(
        _request(destination, key="crlf-cutover"), source_binding, source
    )

    assert result.ok is True
    assert destination.plan_document_path().read_bytes() == crlf_body.encode("utf-8")
    _close(governance, journal)


def test_local_mutation_rejects_plan_over_byte_bound_without_effect(tmp_path: Path):
    destination = _destination(tmp_path)
    adapter = LocalPlanAuthorityAdapter(destination)
    body = "x" * (256 * 1024 + 1)
    request = PortablePlanMutationRequest(
        operation="plan_init",
        typed_target=destination.expected_ref,
        correlation_id="oversized-plan",
        contract_hash="a" * 64,
        idempotency_key="oversized-plan",
        intent_fingerprint="b" * 64,
        subject_expected_revision=0,
        authority_source_revision=None,
        authority_observed_raw_digest=ABSENT_RAW_DOCUMENT_DIGEST,
        candidate_raw_digest=_digest(body),
        normalized_plan_digest=_digest("normalized-oversized-plan"),
        principal="operator-af57",
        authorization_reference="approval-oversized",
        lease_reference="lease-oversized",
        attempt_reference="attempt-oversized",
        candidate_raw_body=body,
    )

    response = adapter.mutate(request)

    assert response.adapter_success is False
    assert response.error_code == "KNOWN_REJECTION"
    assert "bounded Plan size" in (response.error_message or "")
    assert adapter.write_count == 0
    assert not destination.plan_document_path().exists()


def test_nonempty_malformed_sqlite_store_fails_closed(tmp_path: Path):
    database = tmp_path / "malformed.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ProjectGovernanceCorruptStateError, match="non-empty governance store"):
        SQLiteProjectGovernanceStore(database)


def test_thin_bootstrap_round_trips_explicit_durable_local_store(
    tmp_path: Path, operator_config_file: Path
):
    from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError

    worktree = tmp_path / "worktree"
    (worktree / ".aota").mkdir(parents=True)
    (worktree / ".aota" / "project.yaml").write_text(HOST_MANIFEST, encoding="utf-8")
    governance_base = tmp_path / "plans"
    (governance_base / PROJECT_ID).mkdir(parents=True)
    database = tmp_path / "governance.sqlite3"
    governance = SQLiteProjectGovernanceStore(database)
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN_ID),
    )
    governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=binding,
        )
    )
    governance.close()

    launcher = DailyTaskMainLauncher()
    context = launcher.prepare(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-bootstrap-local",
        runtime_config_path=operator_config_file,
        origin_task_main_session_ref="20260917_af57_m3_w1_bootstrap",
        plan_id=PLAN_ID,
        governance_base=governance_base,
        governance_store_path=database,
    )
    assert context.runtime_path == "thin"
    bootstrap = worktree / ".aota" / "task-main-thin-bootstrap.json"
    payload = json.loads(bootstrap.read_text(encoding="utf-8"))
    assert payload["governance_store_path"] == str(database.resolve())

    try:
        child_binding = build_thin_task_main_binding_from_envelope_bootstrap(
            payload, envelope_worktree_root=worktree
        )
        assert child_binding.plan_authority_binding == binding

        missing_record_db = tmp_path / "missing-record.sqlite3"
        missing_store = SQLiteProjectGovernanceStore(missing_record_db)
        missing_store.close()
        missing_payload = dict(payload)
        missing_payload["governance_store_path"] = str(missing_record_db)
        with pytest.raises(TrustedBindingError, match="durable local Plan record is missing"):
            build_thin_task_main_binding_from_envelope_bootstrap(
                missing_payload, envelope_worktree_root=worktree
            )
    finally:
        reset_execution_dispatcher()


def test_build_env_fails_closed_when_binding_envelope_creation_fails(
    tmp_path: Path, operator_config_file: Path, monkeypatch: pytest.MonkeyPatch
):
    import aota_forge.runtime.trusted_runtime_binding as runtime_binding
    from aota_forge.runtime.config import load_runtime_config

    worktree = tmp_path / "worktree"
    (worktree / ".aota").mkdir(parents=True)
    (worktree / ".aota" / "project.yaml").write_text(HOST_MANIFEST, encoding="utf-8")
    materialize_thin_task_main_bootstrap(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-envelope-failure",
        runtime_config_path=operator_config_file,
        origin_task_main_session_ref="20260917_af57_m3_w1_envelope",
    )
    ctx = DailyLaunchContext(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-envelope-failure",
        runtime_config_path=operator_config_file,
        coordinator_store_path=None,
        execution_store_path=worktree / ".aota" / "execution.json",
        live_plan_view=None,
        next_milestone_view=None,
        plan_snapshot=None,
        runtime_config=load_runtime_config(config_path=str(operator_config_file)),
        hermes_bin="",
        runtime_path="thin",
    )

    def fail_envelope(**kwargs):
        raise RuntimeError("injected envelope failure")

    monkeypatch.setattr(runtime_binding, "create_task_main_envelope", fail_envelope)
    with pytest.raises(TaskMainSessionContinuationError, match="refusing launch"):
        DailyTaskMainLauncher().build_env(ctx)
