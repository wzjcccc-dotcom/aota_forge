"""Focused W2 proofs for candidate admission and active-orphan recovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

import aota_forge.governance.local_lifecycle as local_lifecycle
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
)
from aota_forge.core.contracts.descriptor import PLAN_INIT_OPERATION
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.governance.intake import (
    GovernanceIntakeContext,
    GovernanceIntakeService,
    GOVERNANCE_PLAN_MODES,
)
from aota_forge.governance.local_lifecycle import (
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecycleError,
    LocalPlanLifecycleRequest,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectGovernancePersistenceError,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


PROJECT_ID = "w2-project"
PLAN_ID = "plan_w2_candidate_admission"
RECOVERY_REASON = "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_WITHOUT_DURABLE_AMENDMENT_LINEAGE"

VALID_PLAN_BODY = f"""# W2 candidate fixture

## Plan Identity
PROJECT_ID={PROJECT_ID}
PLAN_ID={PLAN_ID}
PLAN_REVISION=R1

## Current State
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress

## M1 — Semantic admission
Initial milestone objective.

#### M1/W1 — Focused fixture
Bounded test work.
"""

AMENDED_PLAN_BODY = VALID_PLAN_BODY.replace(
    "Initial milestone objective.", "Amended milestone objective."
)

WRONG_SECTION_BODY = f"""# W2 candidate fixture

## Plan Identity
PROJECT_ID={PROJECT_ID}
PLAN_ID={PLAN_ID}
PLAN_REVISION=R1

## Historical State
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress

## M1 — Semantic admission
Initial milestone objective.
"""

MISSING_MILESTONE_BODY = f"""# W2 candidate fixture

## Plan Identity
PROJECT_ID={PROJECT_ID}
PLAN_ID={PLAN_ID}
PLAN_REVISION=R1

## Current State
PLAN_STATUS=active

## M1 — Semantic admission
Initial milestone objective.
"""

FOREIGN_PROJECT_BODY = VALID_PLAN_BODY.replace(
    f"PROJECT_ID={PROJECT_ID}", "PROJECT_ID=foreign-project", 1
)
FOREIGN_PLAN_BODY = VALID_PLAN_BODY.replace(
    f"PLAN_ID={PLAN_ID}", "PLAN_ID=plan_foreign", 1
)

LEGACY_HISTORICAL_BODY = """# Legacy Plan snapshot

## Historical State
PLAN_STATUS=completed
CURRENT_MILESTONE=M1
M1_STATUS=completed
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class World:
    base: Path
    destination: LocalPlanAuthorityDestination
    store_path: Path
    journal_path: Path
    governance: SQLiteProjectGovernanceStore
    journal: FileBackedDurableJournalStore
    coordinator: LocalPlanLifecycleCoordinator

    def request(
        self,
        *,
        key: str = "w2-init",
        revision: int | None = None,
        destination: LocalPlanAuthorityDestination | None = None,
    ) -> LocalPlanLifecycleRequest:
        return LocalPlanLifecycleRequest(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            destination=destination or self.destination,
            principal="w2-test-operator",
            authorization_reference="w2-approved-decision",
            lease_reference="w2-test-lease",
            idempotency_key=key,
            correlation_id=f"corr-{key}",
            intent_fingerprint=_digest(key),
            expected_plan_revision=revision,
        )

    def close(self) -> None:
        self.journal.close()
        self.governance.close()


def _world(tmp_path: Path) -> World:
    base = tmp_path / "plans"
    project_scope = base / PROJECT_ID
    project_scope.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(base, project_id=PROJECT_ID)
    target = object_ref_subject(
        make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)
    )
    destination = LocalPlanAuthorityDestination(binding, PLAN_ID, target)
    store_path = tmp_path / "governance.sqlite3"
    journal_path = tmp_path / "governance-journal.json"
    governance = SQLiteProjectGovernanceStore(store_path)
    journal = FileBackedDurableJournalStore(journal_path)
    return World(
        base=base,
        destination=destination,
        store_path=store_path,
        journal_path=journal_path,
        governance=governance,
        journal=journal,
        coordinator=LocalPlanLifecycleCoordinator(governance, journal),
    )


@pytest.fixture
def world(tmp_path: Path):
    result = _world(tmp_path)
    try:
        yield result
    finally:
        result.close()


def _orphan_world(world: World) -> tuple[PlanAuthorityBinding, LocalPlanLifecycleRequest]:
    predecessor_digest = _digest(VALID_PLAN_BODY)
    predecessor = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=world.destination.authority_ref,
        source_revision=predecessor_digest,
        source_digest=predecessor_digest,
    )
    world.governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=predecessor,
        )
    )
    plan_path = world.destination.plan_document_path()
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(AMENDED_PLAN_BODY, encoding="utf-8")
    return predecessor, world.request(key="w2-orphan-recovery", revision=1)


def _recover(
    world: World,
    predecessor: PlanAuthorityBinding,
    request: LocalPlanLifecycleRequest,
    *,
    orphan_digest: str | None = None,
):
    return world.coordinator.recover_active_local_plan_orphan_body(
        request,
        expected_authority=predecessor,
        expected_orphan_digest=orphan_digest or _digest(AMENDED_PLAN_BODY),
        recovery_reason=RECOVERY_REASON,
    )


def _candidate_journal(
    world: World,
    *,
    digest: str,
    journal_id: str,
) -> JournalRecord:
    return JournalRecord(
        journal_id=journal_id,
        correlation_id=f"corr-{journal_id}",
        attempt_id=f"attempt-{journal_id}",
        operation=PLAN_INIT_OPERATION,
        typed_target=world.destination.expected_ref,
        principal="w2-test-operator",
        contract_hash="1" * 64,
        idempotency_key=f"key-{journal_id}",
        intent_fingerprint="2" * 64,
        subject_expected_revision=1,
        authority_source_revision=digest,
        authority_observed_raw_digest=digest,
        candidate_raw_digest=digest,
        normalized_plan_digest="3" * 64,
        authorization_reference="w2-existing-decision",
        lease_reference="w2-existing-lease",
        authorization_basis="trusted_test_linkage",
        journal_state=JournalState.PREPARED,
        original_raw_digest=digest,
        evidence={
            "project_id": PROJECT_ID,
            "plan_id": PLAN_ID,
            "local_plan_lifecycle": "local_plan_amendment",
            "candidate_raw_digest": digest,
        },
    )


def test_T1_valid_init_is_accepted_with_usable_normalized_state(world: World) -> None:
    result = world.coordinator.initialize(world.request(), VALID_PLAN_BODY)

    assert result.ok
    assert result.plan_record is not None
    assert result.plan_record.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    assert result.journal_entry is not None
    assert result.journal_entry.record.journal_state in {
        JournalState.VERIFIED,
        JournalState.VERIFIED_RECOVERED,
    }
    assert world.destination.plan_document_path().read_text(encoding="utf-8") == VALID_PLAN_BODY


def test_T2_wrong_section_current_keys_fail_before_every_mutation(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    mutations: list[str] = []
    original_adapter = local_lifecycle.LocalPlanAuthorityAdapter

    class TrackingAdapter(original_adapter):
        def mutate(self, request):
            mutations.append("body")
            return super().mutate(request)

    monkeypatch.setattr(local_lifecycle, "LocalPlanAuthorityAdapter", TrackingAdapter)
    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.coordinator.initialize(world.request(), WRONG_SECTION_BODY)

    assert excinfo.value.code == "PLAN_STATUS_MISSING"
    assert world.governance.list_plans(PROJECT_ID) == ()
    assert world.journal.list_all() == []
    assert not world.destination.plan_document_path().exists()
    assert mutations == []


def test_T3_normalization_success_without_initial_milestone_is_blocked(world: World) -> None:
    document = normalize_portable_plan(
        MISSING_MILESTONE_BODY,
        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
    )
    assert document.plan_status == "active"
    assert document.current_milestone is None

    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.coordinator.initialize(world.request(), MISSING_MILESTONE_BODY)

    assert excinfo.value.code == "CURRENT_MILESTONE_MISSING"
    assert world.governance.list_plans(PROJECT_ID) == ()
    assert world.journal.list_all() == []
    assert not world.destination.plan_document_path().exists()


@pytest.mark.parametrize(
    ("candidate_body", "finding"),
    (
        (FOREIGN_PROJECT_BODY, "CANDIDATE_PROJECT_ID_MISMATCH"),
        (FOREIGN_PLAN_BODY, "CANDIDATE_PLAN_ID_MISMATCH"),
    ),
)
def test_candidate_identity_echo_must_match_trusted_request(
    world: World,
    candidate_body: str,
    finding: str,
) -> None:
    result = local_lifecycle.preflight_local_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=candidate_body,
    )
    assert result.semantic_readiness == "BLOCKED"
    assert finding in result.finding_codes

    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.coordinator.initialize(world.request(), candidate_body)

    assert excinfo.value.code == finding
    assert world.governance.list_plans(PROJECT_ID) == ()
    assert world.journal.list_all() == []
    assert not world.destination.plan_document_path().exists()


def test_T4_T5_T6_invalid_init_has_zero_store_journal_and_body_effects(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    mutation_attempts: list[str] = []
    original_adapter = local_lifecycle.LocalPlanAuthorityAdapter
    original_put = world.governance.put_plan

    class TrackingAdapter(original_adapter):
        def mutate(self, request):
            mutation_attempts.append("plan_body")
            return super().mutate(request)

    def tracked_put(*args, **kwargs):
        mutation_attempts.append("governance_store")
        return original_put(*args, **kwargs)

    monkeypatch.setattr(local_lifecycle, "LocalPlanAuthorityAdapter", TrackingAdapter)
    monkeypatch.setattr(world.governance, "put_plan", tracked_put)

    with pytest.raises(LocalPlanLifecycleError):
        world.coordinator.initialize(world.request(), WRONG_SECTION_BODY)

    assert mutation_attempts == []
    assert world.governance.list_plans(PROJECT_ID) == ()
    assert world.journal.list_all() == []
    assert not world.destination.plan_document_path().exists()


def test_T7_valid_amend_is_accepted_and_uses_the_same_readiness_core(world: World) -> None:
    initialized = world.coordinator.initialize(world.request(), VALID_PLAN_BODY)
    assert initialized.ok and initialized.plan_record is not None

    preflight = local_lifecycle.preflight_local_plan_candidate(
        candidate_kind="amend",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=AMENDED_PLAN_BODY,
    )
    assert preflight.semantic_readiness == "READY"
    assert preflight.normalized_current_state_usable is True

    amended = world.coordinator.amend(
        world.request(key="w2-amend", revision=initialized.plan_record.revision),
        AMENDED_PLAN_BODY,
        expected_authority=initialized.plan_record.authority,
        amendment_event={"event": "plan_amendment", "decision_ref": "w2-approved-amendment"},
    )

    assert amended.ok
    assert amended.plan_record is not None
    assert amended.plan_record.revision == initialized.plan_record.revision + 1
    assert world.destination.plan_document_path().read_text(encoding="utf-8") == AMENDED_PLAN_BODY


def test_T8_invalid_amend_has_no_journal_store_or_body_effect(world: World) -> None:
    initialized = world.coordinator.initialize(world.request(), VALID_PLAN_BODY)
    assert initialized.ok and initialized.plan_record is not None
    before_record = world.governance.get_plan(PROJECT_ID, PLAN_ID)
    before_body = world.destination.plan_document_path().read_bytes()
    before_journals = world.journal.list_all()

    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.coordinator.amend(
            world.request(key="w2-invalid-amend", revision=before_record.revision),
            MISSING_MILESTONE_BODY,
            expected_authority=before_record.authority,
            amendment_event={"event": "plan_amendment", "decision_ref": "w2-amend"},
        )

    assert excinfo.value.code == "CURRENT_MILESTONE_MISSING"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == before_record
    assert world.journal.list_all() == before_journals
    assert world.destination.plan_document_path().read_bytes() == before_body


def test_T9_legacy_historical_plan_read_remains_compatible() -> None:
    document = normalize_portable_plan(
        LEGACY_HISTORICAL_BODY,
        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
    )

    assert document.plan_status is None
    assert document.current_milestone is None
    assert any(
        finding.get("code") == "CURRENT_KEY_OUTSIDE_CURRENT_SECTION"
        and finding.get("severity") == "info"
        for finding in document.diagnostics
    )


def test_T10_preflight_and_mutations_share_candidate_validator(world: World) -> None:
    invalid = local_lifecycle.preflight_local_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=MISSING_MILESTONE_BODY,
    )
    assert invalid.finding_codes[0] == "CURRENT_MILESTONE_MISSING"
    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.coordinator.initialize(world.request(), MISSING_MILESTONE_BODY)
    assert excinfo.value.code == invalid.finding_codes[0]


def test_T11_T15_intake_preflight_is_bounded_and_read_only(tmp_path: Path) -> None:
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    service = GovernanceIntakeService(
        GovernanceIntakeContext(
            workspace_id="w2-test-workspace",
            governance_base=governance_base,
        )
    )

    ready = service.preflight_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=VALID_PLAN_BODY,
    )
    blocked = service.preflight_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=MISSING_MILESTONE_BODY,
    )

    assert ready.to_dict() == {
        "candidate_kind": "init",
        "normalization_valid": True,
        "semantic_readiness": "READY",
        "finding_codes": [],
        "normalized_current_state_usable": True,
        "mutation_authorization": "NOT_EVALUATED",
        "durable_materialization": "NOT_ATTEMPTED",
    }
    assert blocked.semantic_readiness == "BLOCKED"
    assert blocked.finding_codes == ("CURRENT_MILESTONE_MISSING",)
    assert list(governance_base.iterdir()) == []
    assert not (tmp_path / "governance.sqlite3").exists()
    assert not (tmp_path / "governance-journal.json").exists()


def test_T16_preflight_pass_does_not_bypass_existing_plan_admission(world: World) -> None:
    preflight = world.coordinator
    ready = local_lifecycle.preflight_local_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=VALID_PLAN_BODY,
    )
    assert ready.semantic_readiness == "READY"
    assert ready.mutation_authorization == "NOT_EVALUATED"

    first = preflight.initialize(world.request(), VALID_PLAN_BODY)
    assert first.ok and first.plan_record is not None
    before_body = world.destination.plan_document_path().read_bytes()
    before_journals = world.journal.list_all()
    second_request = world.request(key="w2-second-init", revision=first.plan_record.revision)

    second = preflight.initialize(second_request, VALID_PLAN_BODY)

    assert not second.ok
    assert second.error_code == "PLAN_ALREADY_INITIALIZED"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == first.plan_record
    assert world.journal.list_all() == before_journals
    assert world.destination.plan_document_path().read_bytes() == before_body


def test_preflight_and_recovery_add_no_public_plan_mode_or_mutation_protocol() -> None:
    assert GOVERNANCE_PLAN_MODES == frozenset({"init", "inspect", "amend", "close"})
    assert local_lifecycle.SECOND_PLAN_MUTATION_PROTOCOL_CREATED is False
    assert local_lifecycle.NEW_AGENT_OPERATION_CREATED is False
    assert local_lifecycle.NEW_GOVERNANCE_PLAN_MODE_CREATED is False
    assert local_lifecycle.MODEL_FACING_RECOVERY_CAPABILITY_CREATED is False
    assert local_lifecycle.NEW_JOURNAL_STATE_CREATED is False


def test_orphan_recovery_does_not_reconstruct_missing_or_incomplete_body(
    world: World,
) -> None:
    predecessor, request = _orphan_world(world)
    path = world.destination.plan_document_path()
    before_record = world.governance.get_plan(PROJECT_ID, PLAN_ID)
    path.unlink()

    missing = _recover(world, predecessor, request, orphan_digest=_digest(""))

    assert not missing.ok and missing.error_code == "PLAN_BODY_INVALID"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == before_record
    assert world.journal.list_all() == []
    assert not path.exists()

    incomplete = "# Incomplete body\n\n## Current State\nPLAN_STATUS=active\n"
    path.write_text(incomplete, encoding="utf-8")
    incomplete_result = _recover(
        world,
        predecessor,
        request,
        orphan_digest=_digest(incomplete),
    )

    assert not incomplete_result.ok
    assert incomplete_result.error_code == "CURRENT_MILESTONE_MISSING"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == before_record
    assert world.journal.list_all() == []
    assert path.read_text(encoding="utf-8") == incomplete


def test_T17_T19_active_orphan_recovery_converges_without_overwriting_body(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    predecessor, request = _orphan_world(world)
    path = world.destination.plan_document_path()
    before_stat = path.stat()
    before_body = path.read_bytes()
    adapters = []
    original_adapter = local_lifecycle.LocalPlanAuthorityAdapter

    class TrackingAdapter(original_adapter):
        def __init__(self, destination, **kwargs):
            super().__init__(destination, **kwargs)
            adapters.append(self)

    monkeypatch.setattr(local_lifecycle, "LocalPlanAuthorityAdapter", TrackingAdapter)

    result = _recover(world, predecessor, request)

    current = world.governance.get_plan(PROJECT_ID, PLAN_ID)
    after_stat = path.stat()
    assert result.ok
    assert current is not None and current.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    assert current.revision == 2
    assert current.authority.source_digest == _digest(AMENDED_PLAN_BODY)
    assert current.authority.source_revision == _digest(AMENDED_PLAN_BODY)
    assert path.read_bytes() == before_body
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert sum(adapter.write_count for adapter in adapters) == 0
    assert result.journal_entry is not None
    assert result.journal_entry.record.evidence["recovery_phase"] == "verified"


def test_T18_recovery_replay_is_idempotent(world: World) -> None:
    predecessor, request = _orphan_world(world)
    first = _recover(world, predecessor, request)
    assert first.ok and first.plan_record is not None
    first_journals = world.journal.list_all()

    replay = _recover(world, predecessor, request)

    assert replay.ok
    assert replay.journal_id == first.journal_id
    assert world.journal.list_all() == first_journals
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == first.plan_record


def test_T20_retired_plan_recovery_is_denied(world: World) -> None:
    predecessor, request = _orphan_world(world)
    world.governance.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        1,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    before_body = world.destination.plan_document_path().read_bytes()

    result = _recover(world, predecessor, request)

    assert not result.ok and result.error_code == "PLAN_NOT_ACTIVE"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).lifecycle_state == PLAN_LIFECYCLE_RETIRED
    assert world.journal.list_all() == []
    assert world.destination.plan_document_path().read_bytes() == before_body


@pytest.mark.parametrize("foreign_identity", ("project", "plan"))
def test_T21_foreign_project_or_plan_destination_is_denied(
    world: World,
    tmp_path: Path,
    foreign_identity: str,
) -> None:
    predecessor, request = _orphan_world(world)
    foreign_base = tmp_path / "foreign-plans"
    foreign_project_id = "other-project" if foreign_identity == "project" else PROJECT_ID
    foreign_plan_id = "plan_foreign" if foreign_identity == "plan" else PLAN_ID
    (foreign_base / foreign_project_id).mkdir(parents=True)
    foreign_binding = LocalGovernanceRootBinding.from_trusted_base(
        foreign_base, project_id=foreign_project_id
    )
    foreign_destination = LocalPlanAuthorityDestination(
        foreign_binding,
        foreign_plan_id,
        object_ref_subject(
            make_id(IdKind.SUBJECT, foreign_plan_id, sub_kind=SubjectKind.PLAN)
        ),
    )

    with pytest.raises(LocalPlanLifecycleError) as excinfo:
        world.request(
            key="w2-foreign-recovery",
            revision=1,
            destination=foreign_destination,
        )

    assert excinfo.value.code == "DESTINATION_MISMATCH"
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).authority == predecessor
    assert world.journal.list_all() == []
    assert not foreign_destination.plan_document_path().exists()
    assert request.project_id == PROJECT_ID


@pytest.mark.parametrize("stale_fact", ("revision", "digest"))
def test_T22_stale_revision_or_orphan_digest_fails_closed(
    world: World, stale_fact: str
) -> None:
    predecessor, request = _orphan_world(world)
    before_record = world.governance.get_plan(PROJECT_ID, PLAN_ID)
    before_body = world.destination.plan_document_path().read_bytes()
    if stale_fact == "revision":
        request = world.request(key="w2-stale-revision", revision=2)
        result = _recover(world, predecessor, request)
        assert result.error_code == "STALE_PLAN_REVISION"
    else:
        result = _recover(world, predecessor, request, orphan_digest="f" * 64)
        assert result.error_code == "ORPHAN_BODY_DIGEST_MISMATCH"

    assert not result.ok
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID) == before_record
    assert world.journal.list_all() == []
    assert world.destination.plan_document_path().read_bytes() == before_body


def test_existing_nonterminal_journal_blocks_a_second_orphan_lineage(world: World) -> None:
    predecessor, request = _orphan_world(world)
    record = _candidate_journal(
        world,
        digest=_digest("different candidate"),
        journal_id="w2-existing-nonterminal",
    )
    world.journal.create_prepared(record)

    result = _recover(world, predecessor, request)

    assert not result.ok
    assert result.error_code == "ACTIVE_PLAN_RECOVERY_CONCURRENT_MUTATION"
    assert len(world.journal.list_all()) == 1
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).revision == 1


def test_existing_candidate_journal_lineage_is_not_duplicated(world: World) -> None:
    predecessor, request = _orphan_world(world)
    record = _candidate_journal(
        world,
        digest=_digest(AMENDED_PLAN_BODY),
        journal_id="w2-existing-orphan-candidate",
    )
    world.journal.create_prepared(record)

    result = _recover(world, predecessor, request)

    assert not result.ok
    assert result.error_code == "ORPHAN_RECOVERY_EXISTING_CANDIDATE_LINEAGE"
    assert len(world.journal.list_all()) == 1
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).revision == 1


def test_T23_restart_recovery_resumes_durable_journal_after_store_failure(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    predecessor, request = _orphan_world(world)

    def fail_store_cas(*args, **kwargs):
        raise ProjectGovernancePersistenceError("injected temporary persistence failure")

    monkeypatch.setattr(world.governance, "compare_and_swap_plan", fail_store_cas)
    first = _recover(world, predecessor, request)
    assert not first.ok
    assert first.error_code == "ACTIVE_ORPHAN_STORE_CAS_UNKNOWN"
    assert first.journal_entry is not None
    assert first.journal_entry.record.journal_state in {
        JournalState.VERIFIED,
        JournalState.VERIFIED_RECOVERED,
    }
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).revision == 1

    world.journal.close()
    world.governance.close()
    world.journal = FileBackedDurableJournalStore(world.journal_path)
    world.governance = SQLiteProjectGovernanceStore(world.store_path)
    world.coordinator = LocalPlanLifecycleCoordinator(world.governance, world.journal)

    resumed = world.coordinator.recover(request)

    assert resumed.ok
    assert resumed.journal_id == first.journal_id
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).revision == 2
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).authority.source_digest == _digest(
        AMENDED_PLAN_BODY
    )
    assert len(world.journal.list_all()) == 1


def test_T24_partial_adapter_effect_never_reports_verified(world: World, monkeypatch) -> None:
    predecessor, request = _orphan_world(world)
    original_adapter = local_lifecycle.LocalPlanAuthorityAdapter
    attempts: list[int] = []

    class ApplyThenLoseResponseAdapter(original_adapter):
        def mutate(self, mutation_request):
            response = super().mutate(mutation_request)
            attempts.append(1)
            raise TimeoutError("injected lost response after adapter returned")

    monkeypatch.setattr(
        local_lifecycle, "LocalPlanAuthorityAdapter", ApplyThenLoseResponseAdapter
    )

    result = _recover(world, predecessor, request)

    assert not result.ok
    assert result.phase.value == "unknown"
    assert result.journal_entry is not None
    assert result.journal_entry.record.journal_state is JournalState.OUTCOME_UNKNOWN
    assert world.governance.get_plan(PROJECT_ID, PLAN_ID).revision == 1
    assert world.destination.plan_document_path().read_text(encoding="utf-8") == AMENDED_PLAN_BODY
    assert len(attempts) == 1
