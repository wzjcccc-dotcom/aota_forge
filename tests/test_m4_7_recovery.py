"""M4-7 recovery tests — T05-T12, T22-T24, J1-J10, duplicate executor, idempotency, fresh auth."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.port import PortablePlanMutationRequest
from aota_forge.adapters.plan_authority.fake_port import FakePlanAuthorityAdapter, FixtureAuthority, InjectionHooks
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import InMemoryDurableJournalStore, FileBackedDurableJournalStore, StaleJournalRevisionError
from aota_forge.core.journal.executor import RecoveryExecutor
from aota_forge.core.journal.recovery import RecoveryScanner
from aota_forge.core.journal.retry_handoff import create_retry_journal
from aota_forge.core.journal.reconcile import classify_three_way
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import make_object_ref


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _make_target(name="tgt"):
    return make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN))


def _contract_hash_for(op: str) -> str:
    try:
        from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR, PLAN_RETIREMENT_DESCRIPTOR
        if op == "plan_init":
            return PLAN_INIT_DESCRIPTOR.contract_hash()
        if op == "plan_retirement":
            return PLAN_RETIREMENT_DESCRIPTOR.contract_hash()
    except Exception:
        pass
    return _sha(f"contract-{op}")


def _make_record(
    journal_id=None,
    correlation_id=None,
    attempt_id=None,
    operation="plan_init",
    target=None,
    principal="tester",
    subject_rev=1,
    auth_rev="1",
    observed_digest=None,
    candidate_digest=None,
    normalized_digest=None,
    state=JournalState.PREPARED,
    idempotency_key=None,
    intent_fp=None,
):
    target = target or _make_target()
    observed_digest = observed_digest or _sha("original")
    candidate_digest = candidate_digest or _sha("candidate")
    normalized_digest = normalized_digest or _sha("normalized")
    if observed_digest == normalized_digest:
        normalized_digest = _sha("normalized-alt")
    return JournalRecord(
        journal_id=journal_id or f"journal-{uuid.uuid4().hex[:12]}",
        correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:12]}",
        attempt_id=attempt_id or f"attempt-{uuid.uuid4().hex[:8]}",
        operation=operation,
        typed_target=target,
        principal=principal,
        contract_hash=_contract_hash_for(operation),
        idempotency_key=idempotency_key or f"key-{uuid.uuid4().hex[:8]}",
        intent_fingerprint=intent_fp or _sha(f"intent-{operation}"),
        subject_expected_revision=subject_rev,
        authority_source_revision=auth_rev,
        authority_observed_raw_digest=observed_digest,
        candidate_raw_digest=candidate_digest,
        normalized_plan_digest=normalized_digest,
        authorization_reference="auth-ref-1",
        lease_reference="lease-ref-1",
        journal_state=state,
    )


def _make_request_from_record(rec: JournalRecord, candidate_body="candidate-body"):
    return PortablePlanMutationRequest(
        operation=rec.operation,
        typed_target=rec.typed_target,
        correlation_id=rec.correlation_id,
        contract_hash=rec.contract_hash,
        idempotency_key=rec.idempotency_key,
        intent_fingerprint=rec.intent_fingerprint,
        subject_expected_revision=rec.subject_expected_revision,
        authority_source_revision=rec.authority_source_revision,
        authority_observed_raw_digest=rec.authority_observed_raw_digest,
        candidate_raw_digest=rec.candidate_raw_digest,
        normalized_plan_digest=rec.normalized_plan_digest,
        principal=rec.principal,
        authorization_reference=rec.authorization_reference,
        lease_reference=rec.lease_reference,
        attempt_reference=rec.attempt_id,
        candidate_raw_body=candidate_body,
    )


# T05 APPLYING restart does not blind retry
def test_T05_applying_restart_no_blind_retry():
    store = InMemoryDurableJournalStore()
    fake = FakePlanAuthorityAdapter()
    executor = RecoveryExecutor(store, fake)
    rec = _make_record(journal_id="journal-T05-a", correlation_id="corr-T05", attempt_id="attempt-T05a")
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    # Simulate restart: executor should not allow blind retry via attempt_external_mutation without reconciliation
    # Trying to claim APPLYING again via PREPARED CAS should fail, and attempting to directly mutate without RECONCILING should be denied
    # Recovery should go via RECONCILING
    assert applying.record.journal_state == JournalState.APPLYING
    # Recovery path: must CAS to RECONCILING before any new attempt
    recovered = executor.recover_one(applying.record.journal_id)
    # After recovery, should be in RECONCILING or terminal after classify? Since fake fixture still has original body, verify will return original -> RETRYABLE
    assert recovered is not None
    # Should not have performed blind retry (no second mutate)
    assert fake.mutate_call_count == 0


# T06 candidate observed -> VERIFIED_RECOVERED
def test_T06_candidate_observed():
    store = InMemoryDurableJournalStore()
    # Setup fixture where candidate will be observed
    orig_body = "original-body-T06"
    cand_body = "candidate-body-T06"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T06-a", correlation_id="corr-T06", attempt_id="attempt-T06a", observed_digest=orig_digest, candidate_digest=cand_digest)
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    # Simulate external effect already happened: set fixture to candidate
    fixture = FixtureAuthority(body=cand_body, revision="2")
    # But need original digest preserved in fake's original tracking; create fake with orig then write candidate
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    # Mutate to candidate via fixture directly to simulate effect
    fake.fixture.body = cand_body
    fake.fixture.revision = "2"
    # Now recovery should observe candidate
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    # Should go APPLYING -> RECONCILING -> VERIFIED_RECOVERED
    assert recovered is not None
    assert recovered.record.journal_state == JournalState.VERIFIED_RECOVERED
    assert recovered.record.observed_raw_digest == cand_digest


# T07 original observed -> RETRYABLE_NO_EFFECT
def test_T07_original_observed():
    orig_body = "original-body-T07"
    cand_body = "candidate-body-T07"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T07-a", correlation_id="corr-T07", attempt_id="attempt-T07a", observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    # Should be RETRYABLE_NO_EFFECT
    assert recovered is not None
    assert recovered.record.journal_state == JournalState.RETRYABLE_NO_EFFECT


# T08 third state -> CONFLICT
def test_T08_third_state_conflict():
    orig_body = "original-body-T08"
    cand_body = "candidate-body-T08"
    third_body = "third-state-body-unrelated"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T08-a", correlation_id="corr-T08", attempt_id="attempt-T08a", observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    # Inject third state via hook
    fake.hooks.verify_returns_third = True
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    assert recovered is not None
    assert recovered.record.journal_state == JournalState.CONFLICT


# T09 UNKNOWN_OUTCOME does not blind retry
def test_T09_unknown_no_blind_retry():
    from aota_forge.core.journal.retry import is_retry_allowed
    assert not is_retry_allowed(
        current_state=JournalState.OUTCOME_UNKNOWN,
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    )
    # Via executor, OUTCOME_UNKNOWN should go to RECONCILING not retry
    orig_body = "original-body-T09"
    orig_digest = _sha(orig_body)
    rec = _make_record(journal_id="journal-T09-a", correlation_id="corr-T09", attempt_id="attempt-T09a", observed_digest=orig_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    _, unknown = store.cas_transition(applying.record.journal_id, applying.journal_revision, JournalState.APPLYING, JournalState.OUTCOME_UNKNOWN)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(unknown.record.journal_id)
    assert recovered is not None
    # Should be RECONCILING -> RETRYABLE or CONFLICT, not blind retry to APPLYING
    assert recovered.record.journal_state in (JournalState.RETRYABLE_NO_EFFECT, JournalState.VERIFIED_RECOVERED, JournalState.CONFLICT)
    # Ensure executor does not allow direct retry without fresh auth
    from aota_forge.core.journal.retry import FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY
    assert FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY is True


# T10 RETRYABLE_NO_EFFECT requires fresh auth
def test_T10_retryable_requires_fresh():
    orig_body = "original-body-T10"
    orig_digest = _sha(orig_body)
    cand_digest = _sha("candidate-T10")
    rec = _make_record(journal_id="journal-T10-a", correlation_id="corr-T10", attempt_id="attempt-T10a", observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    assert recovered.record.journal_state == JournalState.RETRYABLE_NO_EFFECT
    # Try to create retry without fresh auth -> should fail
    with pytest.raises(Exception):
        create_retry_journal(
            store,
            recovered,
            new_journal_id="journal-T10-retry",
            new_attempt_id="attempt-T10-retry",
            new_authorization_reference="auth-ref-2",
            new_lease_reference="lease-ref-1",  # reuse old lease -> should fail
            has_fresh_authorization=False,
            has_fresh_subject_precondition=False,
            has_fresh_raw_authority_precondition=False,
            has_new_bounded_lease=False,
        )
    # With fresh auth should succeed
    retry_entry = create_retry_journal(
        store,
        recovered,
        new_journal_id="journal-T10-retry2",
        new_attempt_id="attempt-T10-retry2",
        new_authorization_reference="auth-ref-2",
        new_lease_reference="lease-ref-2",
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    )
    assert retry_entry.record.journal_state == JournalState.PREPARED
    assert retry_entry.record.correlation_id == recovered.record.correlation_id


# T11 old authorization evidence preserved
def test_T11_old_auth_preserved():
    orig_body = "original-body-T11"
    orig_digest = _sha(orig_body)
    rec = _make_record(journal_id="journal-T11-a", correlation_id="corr-T11", attempt_id="attempt-T11a", observed_digest=orig_digest, candidate_digest=_sha("cand"))
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    assert recovered.record.journal_state == JournalState.RETRYABLE_NO_EFFECT
    old_auth = recovered.record.authorization_reference
    retry_entry = create_retry_journal(
        store,
        recovered,
        new_journal_id="journal-T11-retry",
        new_attempt_id="attempt-T11-retry",
        new_authorization_reference="auth-ref-new",
        new_lease_reference="lease-ref-new",
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    )
    # Old entry unchanged
    old_again = store.get(recovered.record.journal_id)
    assert old_again.record.authorization_reference == old_auth
    assert old_again.record.authorization_reference != retry_entry.record.authorization_reference
    assert old_again.record.journal_id != retry_entry.record.journal_id


# T12 fresh auth retry lineage created correctly
def test_T12_fresh_auth_lineage():
    orig_body = "original-body-T12"
    rec = _make_record(journal_id="journal-T12-a", correlation_id="corr-T12", attempt_id="attempt-T12a", observed_digest=_sha(orig_body), candidate_digest=_sha("cand"))
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    executor = RecoveryExecutor(store, fake)
    recovered = executor.recover_one(applying.record.journal_id)
    retry_entry = create_retry_journal(
        store,
        recovered,
        new_journal_id="journal-T12-retry",
        new_attempt_id="attempt-T12-retry",
        new_authorization_reference="auth-ref-new",
        new_lease_reference="lease-ref-new",
        has_fresh_authorization=True,
        has_fresh_subject_precondition=True,
        has_fresh_raw_authority_precondition=True,
        has_new_bounded_lease=True,
    )
    assert retry_entry.record.correlation_id == rec.correlation_id
    assert retry_entry.record.journal_id != rec.journal_id
    # Check lineage evidence preserved
    assert "retry_lineage_from" in retry_entry.record.evidence
    assert retry_entry.record.evidence["retry_lineage_from"] == recovered.record.journal_id
    # Old evidence not overwritten
    assert store.get(recovered.record.journal_id).record.evidence == recovered.record.evidence


# T22 crash after external effect before verify
def test_T22_crash_after_external_before_verify():
    orig_body = "original-body-T22"
    cand_body = "candidate-body-T22"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T22-a", correlation_id="corr-T22", attempt_id="attempt-T22a", observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    # Simulate crash after external before verify: fixture already has candidate, but journal still APPLYING
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    fake.fixture.body = cand_body
    fake.fixture.revision = "2"
    executor = RecoveryExecutor(store, fake)
    # Recovery should handle stranded APPLYING without duplicate write
    before_mutate = fake.mutate_call_count
    recovered = executor.recover_one(applying.record.journal_id)
    assert recovered.record.journal_state == JournalState.VERIFIED_RECOVERED
    # No external mutate performed during recovery
    assert fake.mutate_call_count == before_mutate


# T23 J10 terminal persistence failure
def test_T23_j10_terminal_failure():
    orig_body = "original-body-T23"
    cand_body = "candidate-body-T23"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T23-a", correlation_id="corr-T23", attempt_id="attempt-T23a", observed_digest=orig_digest, candidate_digest=cand_digest)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.json"
        store = FileBackedDurableJournalStore(path)
        fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
        fake.fixture.body = cand_body
        fake.fixture.revision = "2"
        entry = store.create_prepared(rec)
        _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
        # Persist RECONCILING
        _, recon = store.cas_transition(applying.record.journal_id, applying.journal_revision, JournalState.APPLYING, JournalState.RECONCILING)
        # Inject terminal persist failure
        store.inject_fail_next_persist()
        executor = RecoveryExecutor(store, fake)
        # Attempt to recover should try to persist VERIFIED_RECOVERED but fail, leaving RECONCILING
        recovered = executor.recover_one(recon.record.journal_id)
        # After failure, should still be RECONCILING (J10 nonterminal_after_terminal)
        cur = store.get(recon.record.journal_id)
        assert cur.record.journal_state == JournalState.RECONCILING
        # Future run after fixing persistence should succeed via J10
        # Clear failure flag and retry
        executor2 = RecoveryExecutor(store, fake)
        recovered2 = executor2.recover_one(cur.record.journal_id)
        assert recovered2.record.journal_state == JournalState.VERIFIED_RECOVERED
        assert fake.mutate_call_count == 0  # blind reapply denied


# T24 two recovery workers race deterministically
def test_T24_two_recovery_workers_race():
    orig_body = "original-body-T24"
    cand_body = "candidate-body-T24"
    orig_digest = _sha(orig_body)
    cand_digest = _sha(cand_body)
    rec = _make_record(journal_id="journal-T24-a", correlation_id="corr-T24", attempt_id="attempt-T24a", observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body=orig_body, revision="1"))
    fake.fixture.body = cand_body
    fake.fixture.revision = "2"
    executor1 = RecoveryExecutor(store, fake)
    executor2 = RecoveryExecutor(store, fake)
    # Both race to recover same APPLYING
    # First wins CAS APPLYING->RECONCILING
    # Second should see CAS conflict and observe current
    # We simulate by calling recover_one concurrently: first succeeds, second should handle stale
    r1 = executor1.recover_one(applying.record.journal_id)
    # r1 should be VERIFIED_RECOVERED
    assert r1.record.journal_state == JournalState.VERIFIED_RECOVERED
    # Second worker now sees terminal, should return None or current terminal
    r2 = executor2.recover_one(applying.record.journal_id)
    # Since already terminal, recover_one should return None (not reprocessed)
    assert r2 is None or r2.record.journal_state == JournalState.VERIFIED_RECOVERED
    # Ensure only one terminal persisted deterministically
    cur = store.get(applying.record.journal_id)
    assert cur.record.journal_state == JournalState.VERIFIED_RECOVERED


# Test J1-J10 coverage existence
def test_j1_j10_coverage():
    from aota_forge.core.journal.model import CRASH_WINDOW_COUNT, CRASH_WINDOWS
    assert CRASH_WINDOW_COUNT == 10
    assert set(CRASH_WINDOWS) == {"J1","J2","J3","J4","J5","J6","J7","J8","J9","J10"}


# Test process restart durability across close/reopen already covered in T01+T02, but also terminal not rediscovered after reopen
def test_process_restart_terminal_not_rediscovered():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.json"
        store = FileBackedDurableJournalStore(path)
        rec = _make_record(journal_id="journal-restart-term", correlation_id="corr-restart", attempt_id="attempt-restart")
        entry = store.create_prepared(rec)
        _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
        _, recon = store.cas_transition(applying.record.journal_id, applying.journal_revision, JournalState.APPLYING, JournalState.RECONCILING)
        _, terminal = store.cas_transition(recon.record.journal_id, recon.journal_revision, JournalState.RECONCILING, JournalState.VERIFIED_RECOVERED)
        store.close()
        store2 = FileBackedDurableJournalStore(path)
        # Terminal should still be terminal after reopen
        got = store2.get(terminal.record.journal_id)
        assert got.record.journal_state == JournalState.VERIFIED_RECOVERED
        # Scan should not return terminal
        assert all(e.record.journal_id != terminal.record.journal_id for e in store2.scan_requiring_recovery())
        store2.close()


# Test scan ordering deterministic
def test_scan_ordering_deterministic():
    store = InMemoryDurableJournalStore()
    # Create 3 PREPARED with different IDs
    recs = [
        _make_record(journal_id="journal-ord-c", correlation_id="corr-c", attempt_id="attempt-c"),
        _make_record(journal_id="journal-ord-a", correlation_id="corr-a", attempt_id="attempt-a"),
        _make_record(journal_id="journal-ord-b", correlation_id="corr-b", attempt_id="attempt-b"),
    ]
    for r in recs:
        store.create_prepared(r)
    scanned = store.scan_requiring_recovery()
    ids = [e.record.journal_id for e in scanned]
    assert ids == sorted(ids)


# Test durable idempotency via executor
def test_durable_idempotency_same_key_same_identity_replay():
    target = _make_target("idem-tgt")
    key = "idem-key-replay"
    intent = _sha("intent-replay")
    orig_digest = _sha("original-replay")
    cand_digest = _sha("candidate-replay")
    rec = _make_record(journal_id="journal-idem-1", correlation_id="corr-idem", attempt_id="attempt-idem-1", target=target, idempotency_key=key, intent_fp=intent, observed_digest=orig_digest, candidate_digest=cand_digest)
    store = InMemoryDurableJournalStore()
    fake = FakePlanAuthorityAdapter(fixture=FixtureAuthority(body="candidate-body", revision="2"))
    fake.fixture.body = "candidate-body"
    # Create and drive to VERIFIED
    entry = store.create_prepared(rec)
    _, applying = store.cas_transition(entry.record.journal_id, entry.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
    _, recon = store.cas_transition(applying.record.journal_id, applying.journal_revision, JournalState.APPLYING, JournalState.RECONCILING)
    _, terminal = store.cas_transition(recon.record.journal_id, recon.journal_revision, JournalState.RECONCILING, JournalState.VERIFIED_RECOVERED, observed_raw_digest=cand_digest)
    executor = RecoveryExecutor(store, fake)
    # Same key + same complete identity should be replay
    found = executor.check_idempotency(key, rec.complete_external_identity())
    assert found is not None
    assert found.record.journal_state == JournalState.VERIFIED_RECOVERED


# Test no GitHub implementation
def test_T25_no_github_in_executor():
    from aota_forge.core.journal.store import GITHUB_API_CALL_COUNT, GITHUB_ADAPTER_IMPLEMENTED
    from aota_forge.core.journal.executor import GITHUB_API_CALL_COUNT as EXEC_GITHUB, GITHUB_ADAPTER_IMPLEMENTED as EXEC_GITHUB_IMPL
    assert GITHUB_API_CALL_COUNT == 0
    assert GITHUB_ADAPTER_IMPLEMENTED is False
    # Executor should not import github adapter; verify by checking import statements
    import pathlib
    exec_path = pathlib.Path(__file__).resolve().parents[1] / "aota_forge" / "core" / "journal" / "executor.py"
    text = exec_path.read_text()
    assert "from aota_forge.adapters.plan_authority.github" not in text
    assert "import github" not in text.lower()
    # Also check recovery and store have no github adapter imports
    for rel in ["aota_forge/core/journal/store.py", "aota_forge/core/journal/recovery.py", "aota_forge/core/journal/retry_handoff.py"]:
        p = pathlib.Path(__file__).resolve().parents[1] / rel
        t = p.read_text()
        assert "from aota_forge.adapters.plan_authority.github" not in t
        # It's okay for comments to mention github as what is NOT owned, but ensure no actual import
