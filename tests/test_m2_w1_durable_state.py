"""M2/W1 — Agent-neutral durable execution runtime state: record + store semantics.

Covers: DurableExecutionRecord v1 schema strictness, CAS/recovery mechanics,
terminal-truth vs delivery-truth separation, CARD digest verification,
runtime-private field non-persistence, idempotency ownership, close/reopen
durability of the file-backed reference adapter, bounded recovery scan, and
the explicit JournalRecord ontology separation (JOURNAL_RECORD_ONTOLOGY_UNCHANGED).
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from aota_forge.core.execution.durable_state import (
    ADAPTER_OBJECT_PERSISTED,
    CARD_POSSESSION_IS_AUTHORITY,
    CAS_MUTABLE_EXECUTION_FIELDS,
    CROSS_PROCESS_ATOMIC_TRANSACTION_GUARANTEED,
    DELIVERY_COORDINATOR_IMPLEMENTED_IN_W1,
    EXACTLY_ONCE_DISPATCH_CLAIMED,
    EXECUTION_DURABLE_SCHEMA_VERSION,
    FILE_BACKED_IS_PRODUCTION_DEFAULT,
    JOURNAL_RECORD_ONTOLOGY_UNCHANGED,
    ORIGIN_SESSION_REF_IS_AUTHORITY,
    PRODUCTION_STORAGE_ENGINE_FROZEN,
    THIRD_CARD_ONTOLOGY_CREATED,
    DeliveryState,
    DurableExecutionRecord,
    ExecutionIdempotencyConflictError,
    ExecutionPersistenceFailureError,
    ExecutionPhase,
    ExecutionRecordNotFoundError,
    ExecutionStateStore,
    FileBackedExecutionStateStore,
    InMemoryExecutionStateStore,
    OriginSessionRef,
    StaleExecutionRevisionError,
    card_digest_for,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.journal.model import JournalRecord, JournalState

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
W1_MODULES = (
    REPO_ROOT / "aota_forge" / "core" / "execution" / "durable_state.py",
    REPO_ROOT / "aota_forge" / "core" / "execution" / "dispatcher.py",
)

_FP = "a" * 64


def _record(task_id: str = "task-w1-001", **overrides) -> DurableExecutionRecord:
    kwargs: dict = dict(
        canonical_task_id=task_id,
        executor_id="reference-fake",
        package_id="pkg-w1-001",
        correlation_id="corr-w1-001",
        dispatch_attempt_id="att-w1-001",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint=_FP,
    )
    kwargs.update(overrides)
    return DurableExecutionRecord(**kwargs)


def _dispatched(store, task_id: str = "task-w1-001"):
    rec = store.create(_record(task_id))
    return store.compare_and_swap(
        task_id,
        rec.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": f"handle-{task_id}",
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-09-06T00:00:00+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING.value,
        },
    )


def _terminal_result(task_id: str = "task-w1-001") -> CanonicalResult:
    return CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="reference-fake",
        result_data={"files_changed": ["src/x.py"]},
        stdout_summary="ok",
        execution_stats={"duration_ms": 12},
        correlation_id="corr-w1-001",
    )


@pytest.fixture
def mem_store() -> InMemoryExecutionStateStore:
    return InMemoryExecutionStateStore()


@pytest.fixture
def file_store(tmp_path) -> FileBackedExecutionStateStore:
    return FileBackedExecutionStateStore(tmp_path / "exec-state.json")


# ---------------------------------------------------------------------------
# 1. Schema v1 strictness (fail closed)
# ---------------------------------------------------------------------------


class TestSchemaStrictness:
    def test_roundtrip_exact(self):
        rec = _record()
        again = DurableExecutionRecord.from_dict(rec.to_dict())
        assert again.to_dict() == rec.to_dict()

    def test_json_native_serialization(self):
        rec = _record()
        loaded = json.loads(rec.to_json())
        assert isinstance(loaded, dict)
        assert loaded["schema_version"] == EXECUTION_DURABLE_SCHEMA_VERSION == 1

    def test_newer_schema_fails_closed(self):
        data = _record().to_dict()
        data["schema_version"] = 2
        with pytest.raises(ValueError, match="[Uu]nsupported schema_version"):
            DurableExecutionRecord.from_dict(data)

    def test_unknown_schema_field_fails_closed(self):
        data = _record().to_dict()
        data["future_field"] = 1
        with pytest.raises(ValueError, match="Unknown field"):
            DurableExecutionRecord.from_dict(data)

    def test_missing_required_fails_closed(self):
        data = _record().to_dict()
        del data["intent_fingerprint"]
        with pytest.raises(ValueError, match="Missing required field"):
            DurableExecutionRecord.from_dict(data)

    def test_governance_markers(self):
        assert PRODUCTION_STORAGE_ENGINE_FROZEN is False
        assert FILE_BACKED_IS_PRODUCTION_DEFAULT is False
        assert ADAPTER_OBJECT_PERSISTED is False
        assert ORIGIN_SESSION_REF_IS_AUTHORITY is False
        assert CARD_POSSESSION_IS_AUTHORITY is False
        assert THIRD_CARD_ONTOLOGY_CREATED is False
        assert CROSS_PROCESS_ATOMIC_TRANSACTION_GUARANTEED is False
        assert EXACTLY_ONCE_DISPATCH_CLAIMED is False
        assert DELIVERY_COORDINATOR_IMPLEMENTED_IN_W1 is False
        assert JOURNAL_RECORD_ONTOLOGY_UNCHANGED is True


# ---------------------------------------------------------------------------
# 2. Field classification — runtime-private never persisted
# ---------------------------------------------------------------------------


class TestRuntimePrivateNeverPersisted:
    def test_serializable_surface_is_bounded_primitives(self):
        data = _record().to_dict()
        # No adapter object, thread, fd, or process surface in the durable shape;
        # only the opaque adapter_handle string is ever stored.
        assert set(data) <= CAS_MUTABLE_EXECUTION_FIELDS | {
            "schema_version",
            "canonical_task_id",
            "executor_id",
            "package_id",
            "correlation_id",
            "dispatch_attempt_id",
            "idempotency_key",
            "intent_fingerprint",
            "created_at",
            "updated_at",
            "record_revision",
            "revision_token",
        }
        assert not [k for k in data if k.startswith("_adapter")]
        assert "adapter_handle" in data and data["adapter_handle"] is None

    def test_runtime_objects_rejected_by_canonicalization(self):
        with pytest.raises((TypeError, ValueError)):
            _record(adapter_handle=object())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            _record(origin_session_ref=object())  # type: ignore[arg-type]

    def test_identity_fields_are_immutable_under_cas(self, mem_store):
        created = mem_store.create(_record())
        for identity in (
            "canonical_task_id",
            "executor_id",
            "package_id",
            "correlation_id",
            "dispatch_attempt_id",
            "idempotency_key",
            "intent_fingerprint",
        ):
            with pytest.raises(ExecutionPersistenceFailureError):
                mem_store.compare_and_swap(
                    created.canonical_task_id,
                    created.record_revision,
                    {identity: "rewritten"},
                )

    def test_full_execution_package_not_stored(self):
        # ExecutionPackage content is derivable/reconstructible; the record
        # stores package identity only.
        fields = DurableExecutionRecord.from_dict(_record().to_dict()).to_dict()
        assert "package_id" in fields
        assert "instruction" not in json.dumps(fields)


# ---------------------------------------------------------------------------
# 3. CAS mechanics
# ---------------------------------------------------------------------------


class TestCasMechanics:
    def test_stale_revision_fails_deterministically(self, mem_store):
        rec = _dispatched(mem_store)
        with pytest.raises(StaleExecutionRevisionError):
            mem_store.compare_and_swap(rec.canonical_task_id, 1, {"canonical_task_state": "RUNNING"})

    def test_stale_revision_token_fails(self, mem_store):
        rec = _dispatched(mem_store)
        with pytest.raises(StaleExecutionRevisionError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {"canonical_task_state": CanonicalTaskState.RUNNING.value},
                expected_revision_token="bogus",
            )

    def test_revision_and_token_monotonic(self, mem_store):
        rec = _dispatched(mem_store)
        moved = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"canonical_task_state": CanonicalTaskState.WAITING.value},
        )
        assert moved.record_revision == rec.record_revision + 1
        assert moved.revision_token != rec.revision_token

    def test_duplicate_canonical_task_id_create_rejected(self, mem_store):
        mem_store.create(_record())
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.create(_record())

    def test_create_requires_prepared_phase(self, mem_store):
        dispatched = DurableExecutionRecord.from_dict(_dispatched(mem_store).to_dict())
        assert dispatched.execution_phase == ExecutionPhase.DISPATCHED
        not_fresh = DurableExecutionRecord.from_dict(
            {**dispatched.to_dict(), "canonical_task_id": "task-notprepared",
             "idempotency_key": "idem-notprepared"}
        )
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.create(not_fresh)

    def test_get_missing_returns_none_and_cas_raises(self, mem_store):
        assert mem_store.get("nope") is None
        with pytest.raises(ExecutionRecordNotFoundError):
            mem_store.compare_and_swap("nope", 1, {"canonical_task_state": "RUNNING"})

    def test_closed_store_rejects_writes(self):
        store = InMemoryExecutionStateStore()
        store.close()
        with pytest.raises(ExecutionPersistenceFailureError):
            store.create(_record())


# ---------------------------------------------------------------------------
# 4. Durable idempotency ownership
# ---------------------------------------------------------------------------


class TestDurableIdempotencyOwnership:
    def test_same_key_different_task_ownership_conflict(self, mem_store):
        mem_store.create(_record("task-a"))
        with pytest.raises(ExecutionIdempotencyConflictError):
            mem_store.create(_record("task-b", idempotency_key="idem-task-a"))

    def test_lookup_by_idempotency_survives_file_reopen(self, tmp_path):
        path = tmp_path / "idem.json"
        store = FileBackedExecutionStateStore(path)
        store.create(_record())
        store.close()
        reopened = FileBackedExecutionStateStore(path)
        found = reopened.get_by_idempotency("idem-task-w1-001")
        assert found is not None
        assert found.canonical_task_id == "task-w1-001"
        assert found.intent_fingerprint == _FP


# ---------------------------------------------------------------------------
# 5. Terminal truth distinct from delivery truth (I13)
# ---------------------------------------------------------------------------


class TestTerminalTruthSeparation:
    def test_terminal_state_requires_result_agreement(self, mem_store):
        rec = _dispatched(mem_store)
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {
                    "terminal_result": _terminal_result("other-task").to_dict(),
                    "canonical_task_state": "COMPLETED",
                },
            )

    def test_terminal_state_is_sticky(self, mem_store):
        rec = _dispatched(mem_store)
        done = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"terminal_result": _terminal_result().to_dict(), "canonical_task_state": "COMPLETED"},
        )
        assert done.canonical_task_state == CanonicalTaskState.COMPLETED
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                done.canonical_task_id,
                done.record_revision,
                {"canonical_task_state": "RUNNING"},
            )

    def test_terminal_result_attaches_exactly_once(self, mem_store):
        rec = _dispatched(mem_store)
        done = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"terminal_result": _terminal_result().to_dict(), "canonical_task_state": "COMPLETED"},
        )
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                done.canonical_task_id,
                done.record_revision,
                {"terminal_result": _terminal_result().to_dict(), "canonical_task_state": "COMPLETED"},
            )

    def test_acknowledged_without_terminal_result_rejected(self, mem_store):
        rec = _dispatched(mem_store)
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {"delivery_state": "acknowledged"},
            )

    def test_delivery_state_never_alters_task_state(self, mem_store):
        rec = _dispatched(mem_store)
        done = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"terminal_result": _terminal_result().to_dict(), "canonical_task_state": "COMPLETED"},
        )
        pending = mem_store.compare_and_swap(
            done.canonical_task_id,
            done.record_revision,
            {"delivery_state": "pending"},
        )
        assert pending.canonical_task_state == CanonicalTaskState.COMPLETED
        acked = mem_store.compare_and_swap(
            pending.canonical_task_id,
            pending.record_revision,
            {"delivery_state": "acknowledged"},
        )
        assert acked.canonical_task_state == CanonicalTaskState.COMPLETED
        assert acked.delivery_state == DeliveryState.ACKNOWLEDGED

    def test_acknowledged_and_dropped_are_immutable(self, mem_store):
        rec = _dispatched(mem_store)
        done = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"terminal_result": _terminal_result().to_dict(), "canonical_task_state": "COMPLETED"},
        )
        acked = mem_store.compare_and_swap(
            done.canonical_task_id,
            done.record_revision,
            {"delivery_state": "acknowledged"},
        )
        for bad in ("pending", "claimed", "dropped"):
            with pytest.raises(ExecutionPersistenceFailureError):
                mem_store.compare_and_swap(
                    acked.canonical_task_id,
                    acked.record_revision,
                    {"delivery_state": bad},
                )

    def test_claimed_requires_owner(self, mem_store):
        rec = _dispatched(mem_store)
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {"delivery_state": "claimed"},
            )
        claimed = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"delivery_state": "claimed", "delivery_claim_owner": "w3-coordinator", "delivery_attempt": 1},
        )
        assert claimed.delivery_claim_owner == "w3-coordinator"
        assert claimed.delivery_attempt == 1


# ---------------------------------------------------------------------------
# 6. Worker Result CARD (existing ontology, digest-verified)
# ---------------------------------------------------------------------------


class TestCardPersistence:
    def _card_payload(self):
        from aota_forge.core.result_governance import ResultGovernanceProjection
        from aota_forge.work_plane.result_card import project_worker_result_card
        from aota_forge.work_plane.roles import AgentWorkRole

        cr = _terminal_result()
        gp = ResultGovernanceProjection.success()
        card = project_worker_result_card(cr, gp, AgentWorkRole.CODER, summary="m2w1 durable card")
        return card

    def test_card_digest_pair_required(self, mem_store):
        card = self._card_payload()
        rec = _dispatched(mem_store)
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {"worker_result_card": card.canonical_dict()},
            )

    def test_card_digest_mismatch_rejected(self, mem_store):
        card = self._card_payload()
        rec = _dispatched(mem_store)
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {
                    "worker_result_card": card.canonical_dict(),
                    "worker_result_card_digest": "0" * 64,
                },
            )

    def test_card_survives_reopen_and_verifies(self, tmp_path):
        card = self._card_payload()
        path = tmp_path / "card.json"
        store = FileBackedExecutionStateStore(path)
        rec = _dispatched(store)
        stored = store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {
                "worker_result_card": card.canonical_dict(),
                "worker_result_card_digest": card.compute_card_digest(),
            },
        )
        assert stored.worker_result_card_digest == card_digest_for(dict(card.canonical_dict()))
        store.close()

        reopened = FileBackedExecutionStateStore(path)
        loaded = reopened.get(stored.canonical_task_id)
        assert loaded is not None
        from aota_forge.work_plane.result_card import WorkerResultCard

        rebuilt = WorkerResultCard.from_dict(dict(loaded.worker_result_card))
        assert rebuilt.compute_card_digest() == loaded.worker_result_card_digest
        assert rebuilt.result_handoff_ref.ref == loaded.canonical_task_id

    def test_tampered_card_fails_closed_on_load(self, tmp_path):
        card = self._card_payload()
        path = tmp_path / "tamper.json"
        store = FileBackedExecutionStateStore(path)
        rec = _dispatched(store)
        stored = store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {
                "worker_result_card": card.canonical_dict(),
                "worker_result_card_digest": card.compute_card_digest(),
            },
        )
        raw = json.loads(path.read_text())
        raw[stored.canonical_task_id]["worker_result_card"]["summary"] = "tampered"
        path.write_text(json.dumps(raw))
        with pytest.raises(ExecutionPersistenceFailureError):
            FileBackedExecutionStateStore(path)

    def test_card_rewrites_rejected(self, mem_store):
        card = self._card_payload()
        rec = _dispatched(mem_store)
        stored = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {
                "worker_result_card": card.canonical_dict(),
                "worker_result_card_digest": card.compute_card_digest(),
            },
        )
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                stored.canonical_task_id,
                stored.record_revision,
                {
                    "worker_result_card": card.canonical_dict(),
                    "worker_result_card_digest": card.compute_card_digest(),
                },
            )


# ---------------------------------------------------------------------------
# 7. origin_session_ref durability
# ---------------------------------------------------------------------------


class TestOriginSessionRef:
    def test_opaque_value_type_minimal(self):
        ref = OriginSessionRef(value="sess-opaque-§¥# anything/no-structure-parsing")
        assert OriginSessionRef.from_value(ref) is ref
        assert OriginSessionRef.from_value("raw") == OriginSessionRef(value="raw")
        with pytest.raises(ValueError):
            OriginSessionRef(value="   ")

    def test_binds_once_never_rewritten(self, mem_store):
        rec = _dispatched(mem_store)
        bound = mem_store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {"origin_session_ref": "task-main-session-abc"},
        )
        assert bound.origin_session_ref == OriginSessionRef(value="task-main-session-abc")
        with pytest.raises(ExecutionPersistenceFailureError):
            mem_store.compare_and_swap(
                bound.canonical_task_id,
                bound.record_revision,
                {"origin_session_ref": "other-session"},
            )


# ---------------------------------------------------------------------------
# 8. File-backed restart durability + rollback
# ---------------------------------------------------------------------------


class TestFileBackedDurability:
    def test_close_reopen_roundtrip_all_fields(self, tmp_path):
        path = tmp_path / "all.json"
        store = FileBackedExecutionStateStore(path)
        rec = _dispatched(store)
        done = store.compare_and_swap(
            rec.canonical_task_id,
            rec.record_revision,
            {
                "terminal_result": _terminal_result().to_dict(),
                "canonical_task_state": "COMPLETED",
                "origin_session_ref": "sess-origin",
            },
        )
        assert done.to_json() == store.get("task-w1-001").to_json()
        store.close()
        reopened = FileBackedExecutionStateStore(path)
        loaded = reopened.get("task-w1-001")
        assert loaded.to_dict() == done.to_dict()
        assert loaded.origin_session_ref == OriginSessionRef(value="sess-origin")

    def test_persist_failure_rolls_back(self, mem_store, tmp_path):
        store = FileBackedExecutionStateStore(tmp_path / "rb.json")
        rec = _dispatched(store)
        store.inject_fail_next_cas()
        with pytest.raises(ExecutionPersistenceFailureError):
            store.compare_and_swap(
                rec.canonical_task_id,
                rec.record_revision,
                {"canonical_task_state": "WAITING"},
            )
        again = store.get(rec.canonical_task_id)
        assert again.canonical_task_state == CanonicalTaskState.RUNNING
        assert again.record_revision == rec.record_revision

    def test_corrupted_store_load_fails_closed(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(ExecutionPersistenceFailureError):
            FileBackedExecutionStateStore(path)

    def test_port_is_abstract(self):
        with pytest.raises(TypeError):
            ExecutionStateStore()  # type: ignore[abstract]
        assert issubclass(InMemoryExecutionStateStore, ExecutionStateStore)
        assert issubclass(FileBackedExecutionStateStore, ExecutionStateStore)


# ---------------------------------------------------------------------------
# 9. Bounded recovery scan
# ---------------------------------------------------------------------------


class TestRecoveryScan:
    def test_scan_membership_rules(self, file_store):
        a = _dispatched(file_store, "task-a")  # RUNNING nonterminal
        b = _dispatched(file_store, "task-b")
        b_done = file_store.compare_and_swap(
            "task-b",
            b.record_revision,
            {
                "terminal_result": _terminal_result("task-b").to_dict(),
                "canonical_task_state": "COMPLETED",
            },
        )
        assert a.record_revision == b.record_revision == 2
        ids = [r.canonical_task_id for r in file_store.scan_requiring_recovery()]
        assert ids == ["task-a", "task-b"]  # deterministic order; terminal-but-unacknowledged included
        file_store.compare_and_swap(
            "task-b", b_done.record_revision, {"delivery_state": "acknowledged"}
        )
        ids = [r.canonical_task_id for r in file_store.scan_requiring_recovery()]
        assert ids == ["task-a"]

    def test_prepared_crash_tail_is_recoverable(self, file_store):
        file_store.create(_record("task-prepared"))
        ids = [r.canonical_task_id for r in file_store.scan_requiring_recovery()]
        assert "task-prepared" in ids
        reopened = FileBackedExecutionStateStore(file_store._path)
        rec = reopened.get("task-prepared")
        assert rec.execution_phase == ExecutionPhase.PREPARED
        assert rec.adapter_handle is None


# ---------------------------------------------------------------------------
# 10. Journal ontology separation (§31 / I8)
# ---------------------------------------------------------------------------


class TestJournalSeparation:
    def test_journal_record_operations_remain_plan_lifecycle_only(self):
        # Execution operations must NOT be valid mutation-journal operations.
        from aota_forge.core.identity.ids import make_id
        from aota_forge.core.identity.kinds import IdKind
        from aota_forge.core.identity.refs import make_object_ref
        from aota_forge.core.identity.subject import SubjectKind

        target = make_object_ref(
            IdKind.SUBJECT, make_id(IdKind.SUBJECT, "journal-sep-test", sub_kind=SubjectKind.PLAN)
        )
        for op in ("task_dispatch", "execution_state", "result_delivery", "ack"):
            with pytest.raises(ValueError):
                JournalRecord(
                    journal_id="j-1",
                    correlation_id="c-1",
                    attempt_id="a-1",
                    operation=op,
                    typed_target=target,
                    principal="p",
                    contract_hash="b" * 64,
                    idempotency_key="k",
                    intent_fingerprint="f" * 64,
                    subject_expected_revision=1,
                    authority_source_revision=None,
                    authority_observed_raw_digest=None,
                    candidate_raw_digest=None,
                    normalized_plan_digest=None,
                )
        accepted = {
            r
            for r in (
                "plan_init",
                "plan_retirement",
            )
        }
        src = (REPO_ROOT / "aota_forge" / "core" / "journal" / "model.py").read_text()
        assert 'self.operation not in ("plan_init", "plan_retirement")' in src
        assert accepted == {"plan_init", "plan_retirement"}
        assert JOURNAL_RECORD_ONTOLOGY_UNCHANGED is True

    def test_execution_record_is_not_a_journal_record(self):
        import aota_forge.core.execution.durable_state as ds

        journal_symbols = {"JournalRecord", "JournalState", "JournalStateError", "plan_init", "plan_retirement"}
        module_names = set(vars(ds))
        assert not (module_names & journal_symbols)
        tree = ast.parse(pathlib.Path(ds.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "journal" not in (node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "journal" not in alias.name

    def test_journal_9_state_vocabulary_untouched(self):
        assert len(JournalState) == 9
        assert set(s.value for s in JournalState) == {
            "PREPARED",
            "APPLYING",
            "FAILED_NO_EFFECT",
            "VERIFIED",
            "OUTCOME_UNKNOWN",
            "RECONCILING",
            "VERIFIED_RECOVERED",
            "RETRYABLE_NO_EFFECT",
            "CONFLICT",
        }


# ---------------------------------------------------------------------------
# 11. Hermes import gate (§36)
# ---------------------------------------------------------------------------


class TestNoHermesImports:
    def test_w1_modules_have_zero_hermes_imports(self):
        offenders: list[str] = []
        for module_path in W1_MODULES:
            tree = ast.parse(module_path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    target = node.module or ""
                    if "hermes" in target.lower():
                        offenders.append(f"{module_path.name}:{target}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "hermes" in alias.name.lower():
                            offenders.append(f"{module_path.name}:{alias.name}")
        assert offenders == []
        assert len(offenders) == 0
