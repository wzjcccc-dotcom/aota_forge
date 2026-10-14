from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.core.execution.durable_state import card_digest_for
from aota_forge.core.plan.read_model import PortablePlanDocument
from aota_forge.core.result_governance import ResultOutcome
from aota_forge.governance import milestone_closure_evidence as closure
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.steward_finalizer import TrustedUserGateState

PROJECT = "aota_forge"
PLAN = "plan_005_governance_self_hosting_routing_runtime_lifecycle"
PARENT = "ses_edc63efd5ffeuHX8gWSHH8UFkk"
FRONTIER = "9896d92fc3da6ab06ff739a67ddd557387df02cf"
TREE = "ec7c2d64a3e812c87402c6680fe685c7f43e9254"
W2_FRONTIER = "fac1ad03ce7508afa1f3ac97755d4806dc3d7005"
W2_TREE = "dc51e93b1e330afaf607e631b06c4f9e6444660f"
W3_FRONTIER = "792fa3b5e3e101e65ce7cc3cb3e2a2015c522c2c"
HEAD = "f" * 40
W2_RECEIPT_DIGEST = "3010b7213e7e1eb2ea6b5831dacd562b8afb44cd35b52aa950f90c26468f7ad6"
FINGERPRINT = "38ab063dea988ea437350d1b5e339f92088654934969e970f55514786d3b15da"
WORK_ITEMS = ("W1", "W2", "W3", "W4", "W5")
SOURCE = {"available": True, "complete": True, "is_dirty": True, "head_sha": HEAD}


class MemoryReceipts:
    def __init__(self) -> None:
        self.items = []

    def put_once(self, receipt):
        self.items.append(receipt)
        return f"parent-work-acceptance:{receipt.receipt_digest}"

    def list_for_milestone(self, project_id, plan_id, milestone_id):
        return tuple(
            item for item in self.items
            if (item.project_id, item.plan_id, item.milestone_id) == (project_id, plan_id, milestone_id)
        )


class MemoryExecutions:
    def __init__(self) -> None:
        self.records = {}

    def add(self, task_id: str, *, summary: str | None = None, outcome=ResultOutcome.SUCCESS):
        digest = hashlib.sha256(task_id.encode()).hexdigest()
        card = WorkerResultCard(
            task_ref=task_id,
            agent_work_role=AgentWorkRole.CODER,
            summary=summary or f"completed {task_id}",
            outcome=outcome,
            blocking_finding_count=0,
            non_blocking_finding_count=0,
            result_handoff_ref=ResultHandoffRef(ref=task_id, digest=digest),
            primary_evidence_refs=(),
            output_artifact_refs=(),
        )
        record = SimpleNamespace(
            canonical_task_id=task_id,
            canonical_task_state="COMPLETED",
            worker_result_card=card.to_dict(),
            worker_result_card_digest=card_digest_for(card.canonical_dict()),
        )
        self.records[task_id] = record
        return record, digest

    def get(self, task_id: str):
        return self.records.get(task_id)


class MemoryParentDecisionReader:
    def __init__(self) -> None:
        self.messages = []

    def __call__(self, session_ref: str):
        assert session_ref == PARENT
        return tuple(self.messages)


class FakeGit:
    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        chain = (W2_FRONTIER, W3_FRONTIER, FRONTIER, HEAD)
        if ancestor == "1" * 40 and descendant == HEAD:
            return True
        return ancestor in chain and descendant in chain and chain.index(ancestor) <= chain.index(descendant)

    def resolve_tree(self, repo: Path, commit: str) -> str:
        return {FRONTIER: TREE, W2_FRONTIER: W2_TREE}.get(commit, hashlib.sha256(commit.encode()).hexdigest())


def _adapter(monkeypatch, *, project_root: Path | None = None):
    binding = PlanAuthorityBinding(
        plan_id=PLAN,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=f"local-governance:{PROJECT}/{PLAN}",
        source_revision="6a8293ac6e8cbd3b7da6570ad2967093d835b472bfa18c33c0dc16fa3c88e1d0",
        source_digest="6a8293ac6e8cbd3b7da6570ad2967093d835b472bfa18c33c0dc16fa3c88e1d0",
    )
    sandbox = SimpleNamespace(project_id=PROJECT, worktree_id="aota_forge-unbound", digest="a" * 64)
    executions = MemoryExecutions()
    digests = {}
    parent_decision_reader = MemoryParentDecisionReader()

    def read_handoff(ref, *, sandbox):
        digest = ref["digest"]
        task_id = next(key for key, value in digests.items() if value == digest)
        return {
            "digest": digest,
            "envelope": {
                "artifact_id": f"artifact-{task_id.rsplit(':', 1)[-1]}",
                "project_id": PROJECT,
                "task_id": task_id,
            },
        }

    adapter = closure.ThinMilestoneClosureEvidenceAdapter(
        project_id=PROJECT,
        project_root=project_root or Path("/tmp/aota-forge-test"),
        governance_root=SimpleNamespace(project_id=PROJECT),
        plan_binding=binding,
        parent_session_ref=PARENT,
        execution_store=executions,
        sandbox=sandbox,
        receipt_store=MemoryReceipts(),
        parent_decision_reader=parent_decision_reader,
        host_config=object(),
        git_port=FakeGit(),
        handoff_reader=read_handoff,
    )
    adapter._current_plan = lambda: (SimpleNamespace(current_milestone="M2"), WORK_ITEMS)

    def production_ref(resource_id, config):
        assert resource_id == closure.M2_W2_PRODUCTION_RUNTIME_ID
        return {
            "schema_version": 1,
            "runtime_provenance_ref": {
                "resource_id": resource_id,
                "resource_kind": "managed_deployment_receipt",
                "sha256": W2_RECEIPT_DIGEST,
            },
        }

    def production_eval(resource_id, source_root, config):
        assert resource_id == closure.M2_W2_PRODUCTION_RUNTIME_ID
        return {
            "fingerprints": {
                "source_fingerprint": FINGERPRINT,
                "configured_fingerprint": FINGERPRINT,
                "deployed_fingerprint": FINGERPRINT,
                "running_fingerprint": FINGERPRINT,
            },
            "eligibility": {
                "provenance_valid": True,
                "eligible": True,
                "capability_parity": {"parity": True},
            },
            "runtime_subject": {"project_id": PROJECT, "source_commit": W2_FRONTIER, "source_tree": W2_TREE},
        }

    monkeypatch.setattr(closure.host_resources, "deployment_receipt_reference_ref", production_ref)
    monkeypatch.setattr(closure.host_resources, "evaluate_runtime_provenance_ref", production_eval)
    return adapter, executions, digests


def _task(work: str) -> str:
    return f"{PROJECT}:{PLAN}:M2:{work}:accept-{work.lower()}"


def _add_accepted(
    adapter,
    executions,
    digests,
    work: str,
    *,
    frontier: str = FRONTIER,
    summary: str | None = None,
    production_proof=None,
):
    task_id = _task(work)
    _, digest = executions.add(task_id, summary=summary)
    digests[task_id] = digest
    decision_id = f"msg_{work.lower()}"
    decision_text = f"accepted:{task_id}"
    decision_digest = hashlib.sha256(decision_text.encode()).hexdigest()
    adapter.parent_decision_reader.messages.append(
        {
            "session_ref": PARENT,
            "message_id": decision_id,
            "role": "assistant",
            "finish": "stop",
            "text": decision_text,
        }
    )
    return adapter.materialize_parent_acceptance(
        work_item_id=work,
        canonical_task_id=task_id,
        acceptance_decision="accepted",
        accepted_frontier=frontier,
        decision_ref=f"task-main-decision:{PARENT}:{decision_id}:{work}",
        decision_digest=decision_digest,
        production_proof=production_proof,
        source_observation=SOURCE,
    )


def _populate(adapter, executions, digests, *, omit=(), mismatched_frontier=None, w2_proof=None):
    for work in WORK_ITEMS:
        if work in omit:
            continue
        frontier = {
            "W1": W2_FRONTIER,
            "W2": W2_FRONTIER,
            "W3": mismatched_frontier if work == "W3" and mismatched_frontier else W3_FRONTIER,
            "W4": W3_FRONTIER,
            "W5": FRONTIER,
        }[work]
        _add_accepted(
            adapter,
            executions,
            digests,
            work,
            frontier=frontier,
            summary="production proof external/not included" if work == "W2" else None,
            production_proof=w2_proof if work == "W2" else None,
        )


def test_t1_external_w2_production_owner_is_discovered_by_trusted_resolver(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is True
    assert result.production_evidence_ref == (
        f"managed_deployment_receipt:{closure.M2_W2_PRODUCTION_RUNTIME_ID}#sha256:{W2_RECEIPT_DIGEST}"
    )


def test_t2_w2_source_only_worker_result_does_not_erase_external_production_proof(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    proof = {
        "fingerprints": {
            "source_fingerprint": FINGERPRINT,
            "configured_fingerprint": FINGERPRINT,
            "deployed_fingerprint": FINGERPRINT,
            "running_fingerprint": FINGERPRINT,
        },
        "eligibility": {
            "provenance_valid": True,
            "eligible": True,
            "capability_parity": {"parity": True},
        },
        "runtime_subject": {"project_id": PROJECT, "source_commit": W2_FRONTIER, "source_tree": W2_TREE},
    }
    monkeypatch.setattr(closure.host_resources, "evaluate_runtime_provenance_ref", lambda *_args, **_kwargs: pytest.fail("W2 proof must not be rerun"))
    _populate(adapter, executions, digests, w2_proof=proof)
    w2 = next(item for item in adapter.project(SOURCE).work_acceptances if item.work_item_id == "W2")
    assert "external/not included" in executions.get(w2.canonical_task_id).worker_result_card["summary"]
    assert w2.production_evidence_digest == W2_RECEIPT_DIGEST
    assert w2.production_provenance is not None


def test_t3_failed_historical_reviewer_does_not_override_accepted_w5(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    failed_task = f"{PROJECT}:{PLAN}:M2:W5:failed-reviewer"
    executions.add(failed_task, outcome=ResultOutcome.FAILURE)
    result = adapter.project(SOURCE)
    w5 = next(item for item in result.work_acceptances if item.work_item_id == "W5")
    assert w5.canonical_task_id == _task("W5")
    assert w5.canonical_task_id != failed_task


def test_t4_exact_parent_lineage_selects_accepted_w5_result(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    w5 = next(item for item in adapter.project(SOURCE).work_acceptances if item.work_item_id == "W5")
    assert w5.canonical_task_id == "aota_forge:plan_005_governance_self_hosting_routing_runtime_lifecycle:M2:W5:accept-w5"
    assert w5.completion_card_digest == executions.get(w5.canonical_task_id).worker_result_card_digest


def test_t5_latest_by_time_selection_is_forbidden(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    executions.add(f"{PROJECT}:{PLAN}:M2:W5:latest-failed", outcome=ResultOutcome.FAILURE)
    result = adapter.project(SOURCE)
    assert closure.LATEST_BY_TIME_SELECTION_ALLOWED is False
    assert next(item for item in result.work_acceptances if item.work_item_id == "W5").canonical_task_id == _task("W5")


def test_t6_parent_acceptance_requires_bound_plan_identity(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    task_id = f"{PROJECT}:foreign-plan:M2:W1:attempt"
    executions.add(task_id)
    with pytest.raises(closure.MilestoneClosureEvidenceError, match="canonical task"):
        adapter.materialize_parent_acceptance(
            work_item_id="W1",
            canonical_task_id=task_id,
            acceptance_decision="accepted",
            accepted_frontier=FRONTIER,
            decision_ref=f"task-main-decision:{PARENT}:msg_w1:W1",
            decision_digest="a" * 64,
            source_observation=SOURCE,
        )


def test_t7_foreign_session_acceptance_is_rejected(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    original = next(item for item in adapter.receipt_store.items if item.work_item_id == "W3")
    data = original.canonical_payload()
    data["parent_session_ref"] = "ses_foreign"
    data["decision_ref"] = "task-main-decision:ses_foreign:msg_w3:W3"
    adapter.receipt_store.items = [
        closure.ParentWorkAcceptanceProjection.from_dict(data)
        if item.work_item_id == "W3"
        else item
        for item in adapter.receipt_store.items
    ]
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("foreign parent session" in reason for reason in result.blocking_reasons)


def test_t8_stale_plan_revision_acceptance_is_rejected(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    original = next(item for item in adapter.receipt_store.items if item.work_item_id == "W4")
    data = original.canonical_payload()
    data["plan_source_revision"] = "0" * 64
    adapter.receipt_store.items.append(closure.ParentWorkAcceptanceProjection.from_dict(data))
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("stale Plan source revision" in reason for reason in result.blocking_reasons)


def test_t9_result_digest_mismatch_is_rejected(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    original = next(item for item in adapter.receipt_store.items if item.work_item_id == "W1")
    data = original.canonical_payload()
    data["result_digest"] = "0" * 64
    adapter.receipt_store.items = [
        closure.ParentWorkAcceptanceProjection.from_dict(data)
        if item.work_item_id == "W1"
        else item
        for item in adapter.receipt_store.items
    ]
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("WORK_CARD_MISMATCH" in reason for reason in result.blocking_reasons)


def test_t10_accepted_source_frontier_mismatch_is_rejected(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests, mismatched_frontier="1" * 40)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert "accepted source frontier mismatch for W3" in result.blocking_reasons


def test_t11_missing_production_receipt_fails_closed(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("receipt absent")

    monkeypatch.setattr(closure.host_resources, "deployment_receipt_reference_ref", missing)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("PRODUCTION_RECEIPT_MISSING" in reason for reason in result.blocking_reasons)


def test_t12_missing_parent_acceptance_projection_fails_closed(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests, omit=("W5",))
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert "missing parent acceptance projection: W5" in result.blocking_reasons


def test_t13_parent_receipt_is_immutable_and_no_work_progression_ledger_is_created(monkeypatch, tmp_path):
    adapter, executions, digests = _adapter(monkeypatch, project_root=tmp_path)
    receipt = _add_accepted(adapter, executions, digests, "W1")
    store = closure.FileBackedParentWorkAcceptanceProjectionStore(tmp_path)
    ref = store.put_once(receipt)
    assert store.put_once(receipt) == ref
    readback = store.list_for_milestone(PROJECT, PLAN, "M2")
    assert readback == (receipt,)
    assert closure.NEW_WORK_LEDGER_CREATED is False
    assert closure.PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION is False
    assert store.root.name == "parent-work-acceptances"
    assert store.root.suffix != ".sqlite3"
    assert sorted(path.suffix for path in store.root.iterdir()) == [".json"]


def test_t14_legacy_coordinator_is_not_required(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    assert closure.LEGACY_COORDINATOR_REQUIRED is False
    assert not hasattr(adapter, "coordinator_store")


def test_t15_readiness_composes_cross_owner_evidence(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is True
    assert result.readiness is not None
    assert result.readiness.ready_for_project_steward is True
    assert result.accepted_frontier == FRONTIER
    assert result.accepted_tree == TREE
    assert len(result.work_acceptances) == 5


def test_t16_parent_decision_message_digest_is_reverified_on_readback(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    original = next(item for item in adapter.receipt_store.items if item.work_item_id == "W1")
    payload = original.canonical_payload()
    payload["decision_digest"] = "0" * 64
    tampered = closure.ParentWorkAcceptanceProjection.from_dict(payload)
    adapter.receipt_store.items = [
        tampered if item.work_item_id == "W1" else item
        for item in adapter.receipt_store.items
    ]

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "W1: EVIDENCE_DIGEST_MISMATCH" in result.blocking_reasons


def test_t17_missing_exact_parent_message_fails_readiness_closed(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader.messages.clear()

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "W1: PARENT_DECISION_MISSING" in result.blocking_reasons


def test_t18_nonterminal_parent_message_cannot_back_acceptance(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader.messages[0]["finish"] = "length"

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "W1: PARENT_DECISION_INVALID" in result.blocking_reasons


def test_t19_duplicate_parent_message_id_fails_readiness_closed(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader.messages.append(
        dict(adapter.parent_decision_reader.messages[0])
    )

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "parent decision source: PARENT_DECISION_SOURCE_INVALID" in result.blocking_reasons


def test_t20_wrong_role_parent_message_cannot_back_acceptance(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader.messages[0]["role"] = "user"

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "W1: PARENT_DECISION_INVALID" in result.blocking_reasons


def test_t21_foreign_session_parent_message_cannot_back_acceptance(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader.messages[0]["session_ref"] = "ses_foreign"

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "W1: FOREIGN_PARENT_DECISION" in result.blocking_reasons


def test_t22_unavailable_parent_history_fails_with_typed_source_code(monkeypatch):
    adapter, executions, digests = _adapter(monkeypatch)
    _populate(adapter, executions, digests)
    adapter.parent_decision_reader = None

    result = adapter.project(SOURCE)

    assert result.ready_for_project_steward is False
    assert "parent decision source: PARENT_DECISION_SOURCE_UNAVAILABLE" in result.blocking_reasons


def test_milestone_close_requires_existing_user_gate_and_writes_steward_receipt(monkeypatch, tmp_path):
    adapter, executions, digests = _adapter(monkeypatch, project_root=tmp_path)
    _populate(adapter, executions, digests)
    host_bootstrap = __import__("aota_forge.composition.task_main_host_bootstrap", fromlist=["x"])
    monkeypatch.setattr(
        host_bootstrap,
        "resolve_milestone_close_user_gate",
        lambda *_args, **_kwargs: TrustedUserGateState(user_approval_satisfied=True),
    )
    adapter.governance_store = object()
    result = adapter.close(source_observation=SOURCE)
    assert result["status"] == "accepted_closure"
    assert result["accepted_frontier"] == FRONTIER
    assert (tmp_path / ".aota" / "materialization_receipts.json").is_file()


HISTORICAL_PLAN_REVISION = "b" * 64
HISTORICAL_PARENT = "ses_historicalW6"


def _historical_plan(revision: str, *, include_w6: bool, changed_work: str | None = None):
    fields = {f"M2_{changed_work}_ACCEPTANCE": "tampered"} if changed_work else {}
    work_items = WORK_ITEMS + (("W6",) if include_w6 else ())
    return PortablePlanDocument(
        current_milestone="M2",
        current_status="active",
        milestone_status={"M2": "IN_PROGRESS"},
        milestone_approvals={"M2": True},
        milestone_work_items={"M2": work_items},
        current_fields=fields,
        source_revision=revision,
        source_digest=revision,
    )


def _historical_amendment():
    return {
        "kind": "add_work_item",
        "milestone_id": "M2",
        "work_item": {
            "id": "W6",
            "title": "Historical closure repair",
            "objective": "Reconstruct bounded predecessor acceptance evidence",
            "scope": ["M2 closure evidence"],
            "dependencies": ["W5"],
            "acceptance": ["same-Plan predecessor relation is verified"],
        },
    }


def _historical_relation(*, state="VERIFIED", project_id=PROJECT, plan_id=PLAN, wrong_w6=False):
    amendment = _historical_amendment()
    if wrong_w6:
        amendment["work_item"]["id"] = "W5"
    return {
        "project_id": project_id,
        "plan_id": plan_id,
        "milestone_id": "M2",
        "work_item_id": "W6",
        "authority_source_revision_before": HISTORICAL_PLAN_REVISION,
        "authority_source_revision_after": "6a8293ac6e8cbd3b7da6570ad2967093d835b472bfa18c33c0dc16fa3c88e1d0",
        "plan_revision_before": 9,
        "plan_revision_after": 10,
        "journal_state": state,
        "amendment": amendment,
        "amendment_digest": closure.canonical_plan_amendment_digest(amendment),
    }


def _prepare_historical_projection(monkeypatch, *, changed_work=None, relation=None, historical_reader=True):
    adapter, executions, digests = _adapter(monkeypatch)
    current = _historical_plan(adapter.plan_binding.source_revision, include_w6=True, changed_work=changed_work)
    historical = _historical_plan(HISTORICAL_PLAN_REVISION, include_w6=False)
    adapter._current_plan = lambda: (current, current.milestone_work_items["M2"])
    _populate(adapter, executions, digests)
    source_items = tuple(adapter.receipt_store.items)
    historical_messages = []
    historical_items = []
    for item in source_items:
        work = item.work_item_id
        message_id = f"msg_{work.lower()}"
        text = f"accepted:{item.canonical_task_id}"
        historical_messages.append(
            {
                "session_ref": HISTORICAL_PARENT,
                "message_id": message_id,
                "role": "assistant",
                "finish": "stop",
                "text": text,
            }
        )
        payload = item.canonical_payload()
        payload["parent_session_ref"] = HISTORICAL_PARENT
        payload["plan_source_revision"] = HISTORICAL_PLAN_REVISION
        payload["decision_ref"] = f"task-main-decision:{HISTORICAL_PARENT}:{message_id}:{work}"
        historical_items.append(closure.ParentWorkAcceptanceProjection.from_dict(payload))
    adapter.receipt_store.items = historical_items
    adapter.parent_decision_reader = lambda session_ref: tuple(
        historical_messages if session_ref == HISTORICAL_PARENT else ()
    )
    adapter.historical_plan_reader = (
        (lambda revision: historical if revision == HISTORICAL_PLAN_REVISION else None)
        if historical_reader
        else None
    )
    adapter.plan_lineage_reader = lambda *_args: relation or _historical_relation()
    return adapter, executions, digests


def test_v1_historical_w1_w5_acceptances_reconstruct_through_same_plan_w6_predecessor(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(monkeypatch)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is True
    assert tuple(item.work_item_id for item in result.work_acceptances) == WORK_ITEMS
    assert all(item.parent_session_ref == HISTORICAL_PARENT for item in result.work_acceptances)


def test_v2_historical_plan_source_missing_fails_closed(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(monkeypatch, historical_reader=False)
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("HISTORICAL_PLAN_SOURCE_UNAVAILABLE" in reason for reason in result.blocking_reasons)


def test_v3_changed_historical_work_acceptance_semantics_fails_closed(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(monkeypatch, changed_work="W3")
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("HISTORICAL_WORK_SEMANTICS_CHANGED" in reason for reason in result.blocking_reasons)


def test_v4_foreign_plan_lineage_fails_closed(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(
        monkeypatch,
        relation=_historical_relation(plan_id="plan_foreign"),
    )
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("PLAN_LINEAGE_IDENTITY_MISMATCH" in reason for reason in result.blocking_reasons)


def test_v5_non_w6_amendment_cannot_authorize_historical_exception(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(
        monkeypatch,
        relation=_historical_relation(wrong_w6=True),
    )
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("PLAN_LINEAGE_NOT_W6_REPAIR" in reason for reason in result.blocking_reasons)


def test_v6_unverified_w6_journal_state_fails_closed(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(
        monkeypatch,
        relation=_historical_relation(state="CONFLICT"),
    )
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("PLAN_LINEAGE_JOURNAL_INVALID" in reason for reason in result.blocking_reasons)


def test_v7_historical_parent_session_decision_is_required(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(monkeypatch)
    adapter.parent_decision_reader = lambda _session_ref: ()
    result = adapter.project(SOURCE)
    assert result.ready_for_project_steward is False
    assert any("PARENT_DECISION_MISSING" in reason for reason in result.blocking_reasons)


def test_v8_inspector_session_is_not_acceptance_authority_and_w6_is_not_predecessor_receipt(monkeypatch):
    adapter, _executions, _digests = _prepare_historical_projection(monkeypatch)
    result = adapter.project(SOURCE)
    assert closure.CURRENT_INSPECTOR_SESSION_IS_ACCEPTANCE_AUTHORITY is False
    assert closure.W6_SAME_PLAN_PREDECESSOR_REVISION_EXCEPTION_ONLY is True
    assert "W6" not in {item.work_item_id for item in result.work_acceptances}
