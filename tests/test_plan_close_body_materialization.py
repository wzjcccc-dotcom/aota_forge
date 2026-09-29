"""Focused proofs for the local PLAN_CLOSE body cutover/recovery seam."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PlanAuthorityBinding,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.model import JournalState
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.governance.local_lifecycle import (
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecycleRequest,
    PlanCloseMaterializationEvidence,
    project_terminal_plan_body,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


PROJECT_ID = "plan-close-test"
PLAN_ID = "plan_terminal_body_test"
BODY = """# Local Plan fixture

## Plan Identity
PROJECT_ID=plan-close-test
PLAN_ID=plan_terminal_body_test
PLAN_REVISION=R1

## Current State
PROJECT_ID=plan-close-test
PLAN_ID=plan_terminal_body_test
PLAN_REVISION=R1
PLAN_STATUS=active
PLAN_ACCEPTED=yes
CURRENT_MILESTONE=M3
CURRENT_STATUS=m3_user_acceptance_ready
CURRENT_BLOCKER=none
M1_STATUS=completed
M1_ACCEPTED=yes
M2_STATUS=completed
M2_ACCEPTED=yes
M3_STATUS=in_progress
M3_ACCEPTED=no
M3_USER_APPROVAL_SATISFIED=yes
M3_CONSTRUCTION_APPROVED=yes
M3_SOURCE_CONSTRUCTION_STARTED=yes
HANDOFF_STATE=m3_user_acceptance_ready
CURRENT_NEXT_ACTION=Await closure
NEXT_CHECKPOINT=M3/user_acceptance

## History
The accepted implementation history remains unchanged.
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _evidence(
    *,
    project_id: str = PROJECT_ID,
    plan_id: str = PLAN_ID,
    accepted_frontier: str = "a" * 40,
    final_known_good: str | None = None,
    frontier_relation: str = "SAME_AS_ACCEPTED",
    closure_phase: str = "ACCEPTED_CLOSURE",
) -> PlanCloseMaterializationEvidence:
    return PlanCloseMaterializationEvidence(
        project_id=project_id,
        plan_id=plan_id,
        milestone_id="M3",
        accepted_frontier=accepted_frontier,
        final_known_good=final_known_good or accepted_frontier,
        frontier_relation=frontier_relation,
        milestone_checkpoint_id="stewardship:" + "b" * 64,
        milestone_checkpoint_digest="c" * 64,
        milestone_receipt_id="mfr-" + "d" * 16,
        milestone_receipt_digest="e" * 64,
        milestone_steward_digest="f" * 64,
        plan_close_checkpoint_id="stewardship:" + "1" * 64,
        plan_close_checkpoint_digest="2" * 64,
        plan_close_receipt_id="mfr-" + "3" * 16,
        plan_close_receipt_digest="4" * 64,
        plan_close_steward_digest="5" * 64,
        closure_phase=closure_phase,
    )


def _world(tmp_path: Path):
    governance_base = tmp_path / "plans"
    (governance_base / PROJECT_ID).mkdir(parents=True)
    target = object_ref_subject(make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN))
    destination = LocalPlanAuthorityDestination(
        LocalGovernanceRootBinding.from_trusted_base(governance_base, project_id=PROJECT_ID),
        PLAN_ID,
        target,
    )
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "governance-journal.json")
    coordinator = LocalPlanLifecycleCoordinator(governance, journal)
    request = LocalPlanLifecycleRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        destination=destination,
        principal="plan-close-test",
        authorization_reference="accepted-plan-close",
        lease_reference="plan-close-lease",
        idempotency_key="plan-close-init",
        correlation_id="plan-close-init",
        intent_fingerprint=_digest("plan-close-init"),
    )
    initialized = coordinator.initialize(request, BODY)
    assert initialized.ok
    current = governance.get_plan(PROJECT_ID, PLAN_ID)
    assert current is not None and current.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    return governance_base, destination, governance, journal, coordinator, current


def _close_request(destination, revision: int, key: str) -> LocalPlanLifecycleRequest:
    return LocalPlanLifecycleRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        destination=destination,
        principal="plan-close-test",
        authorization_reference="accepted-plan-close",
        lease_reference="plan-close-lease",
        idempotency_key=key,
        correlation_id=key,
        intent_fingerprint=_digest(key),
        expected_plan_revision=revision,
    )


def test_terminal_projection_preserves_history_and_advances_revision() -> None:
    candidate = project_terminal_plan_body(BODY, _evidence())

    assert "The accepted implementation history remains unchanged." in candidate
    assert candidate.count("PLAN_REVISION=R2") == 2
    for marker in (
        "PLAN_STATUS=completed",
        "PLAN_ACCEPTED=yes",
        "CURRENT_STATUS=completed",
        "CURRENT_BLOCKER=none",
        "M1_STATUS=completed",
        "M1_ACCEPTED=yes",
        "M2_STATUS=completed",
        "M2_ACCEPTED=yes",
        "M3_STATUS=completed",
        "M3_ACCEPTED=yes",
        "M3_CLOSURE_STATUS=APPLIED",
        "M3_ACCEPTED_FRONTIER=" + "a" * 40,
        "PLAN_FINAL_KNOWN_GOOD=" + "a" * 40,
    ):
        assert marker in candidate


def test_active_plan_close_materializes_body_before_retirement_and_converges(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    evidence = _evidence()
    candidate = project_terminal_plan_body(BODY, evidence)

    result = coordinator.close_local_plan(
        _close_request(destination, current.revision, "plan-close-success"),
        candidate,
        expected_authority=current.authority,
        closure_evidence=evidence,
    )

    assert result.ok
    assert result.plan_record is not None
    assert result.plan_record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    assert result.plan_record.revision == current.revision + 2
    assert destination.plan_document_path().read_text(encoding="utf-8") == candidate
    assert result.plan_record.authority.source_digest == _digest(candidate)
    assert result.journal_entry is not None
    assert result.journal_entry.record.journal_state is JournalState.VERIFIED_RECOVERED
    assert result.journal_entry.record.evidence["retirement_phase"] == "verified"
    assert result.journal_entry.record.evidence["closure_semantic_decision_reexecuted"] is False
    governance.close()
    journal.close()


def test_materialization_failure_cannot_retire_plan(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    evidence = _evidence()
    candidate = project_terminal_plan_body(BODY, evidence)
    journal.inject_fail_before_applying()

    result = coordinator.close_local_plan(
        _close_request(destination, current.revision, "plan-close-failure"),
        candidate,
        expected_authority=current.authority,
        closure_evidence=evidence,
    )

    observed = governance.get_plan(PROJECT_ID, PLAN_ID)
    assert not result.ok
    assert observed is not None and observed.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    assert observed.revision == current.revision
    governance.close()
    journal.close()


def test_close_uses_exact_authority_cas(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    evidence = _evidence()
    candidate = project_terminal_plan_body(BODY, evidence)
    stale = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=current.authority.authority_ref,
        source_revision="0" * 64,
        source_digest="0" * 64,
    )

    result = coordinator.close_local_plan(
        _close_request(destination, current.revision, "plan-close-cas-conflict"),
        candidate,
        expected_authority=stale,
        closure_evidence=evidence,
    )

    observed = governance.get_plan(PROJECT_ID, PLAN_ID)
    assert not result.ok
    assert result.error_code in {"AMENDMENT_BINDING_STALE", "STALE_AUTHORITY"}
    assert observed is not None and observed.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
    assert destination.plan_document_path().read_text(encoding="utf-8") == BODY
    governance.close()
    journal.close()


def test_retired_stale_body_recovery_keeps_store_retired(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    retired = governance.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    evidence = _evidence()
    candidate = project_terminal_plan_body(BODY, evidence)

    result = coordinator.recover_retired_final_body(
        _close_request(destination, retired.revision, "plan-close-recovery"),
        candidate,
        expected_authority=retired.authority,
        closure_evidence=evidence,
    )

    assert result.ok
    assert result.plan_record is not None
    assert result.plan_record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    assert result.plan_record.revision == retired.revision + 1
    assert result.plan_record.authority.source_digest == _digest(candidate)
    assert destination.plan_document_path().read_text(encoding="utf-8") == candidate
    assert result.journal_entry is not None
    assert result.journal_entry.record.evidence["recovery_executed"] is True
    governance.close()
    journal.close()


def test_normal_retired_amendment_remains_denied(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    retired = governance.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )

    result = coordinator.amend(
        _close_request(destination, retired.revision, "normal-retired-amend"),
        BODY.replace("CURRENT_STATUS=m3_user_acceptance_ready", "CURRENT_STATUS=other"),
        expected_authority=retired.authority,
        amendment_event={"event": "plan_amendment"},
    )

    assert not result.ok
    assert result.error_code == "PLAN_NOT_ACTIVE"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).lifecycle_state == PLAN_LIFECYCLE_RETIRED
    governance.close()
    journal.close()


def test_recovery_rejects_nonaccepted_or_foreign_evidence(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    retired = governance.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    with pytest.raises(ValueError):
        _evidence(closure_phase="REVIEWED_CLOSURE")
    foreign = _evidence(plan_id="plan_foreign")
    candidate = project_terminal_plan_body(BODY, _evidence())

    result = coordinator.recover_retired_final_body(
        _close_request(destination, retired.revision, "foreign-recovery"),
        candidate,
        expected_authority=retired.authority,
        closure_evidence=foreign,
    )

    assert not result.ok
    assert result.error_code == "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).lifecycle_state == PLAN_LIFECYCLE_RETIRED
    governance.close()
    journal.close()


def test_recovery_rejects_store_body_preclose_digest_mismatch(tmp_path: Path) -> None:
    _, destination, governance, journal, coordinator, current = _world(tmp_path)
    retired = governance.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    wrong = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=retired.authority.authority_ref,
        source_revision="1" * 64,
        source_digest="1" * 64,
    )
    evidence = _evidence()
    candidate = project_terminal_plan_body(BODY, evidence)

    result = coordinator.recover_retired_final_body(
        _close_request(destination, retired.revision, "digest-conflict"),
        candidate,
        expected_authority=wrong,
        closure_evidence=evidence,
    )

    assert not result.ok
    assert result.error_code == "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT"
    assert governance.get_plan(PROJECT_ID, PLAN_ID).revision == retired.revision
    governance.close()
    journal.close()


def test_distinct_final_frontier_requires_explicit_dual_frontier_relation() -> None:
    with pytest.raises(ValueError):
        _evidence(final_known_good="b" * 40)
    evidence = _evidence(
        final_known_good="b" * 40,
        frontier_relation="GOVERNANCE_ONLY_DESCENDANT",
    )
    assert evidence.final_known_good == "b" * 40
