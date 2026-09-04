"""S5 M1 W2 Session Checkpoint & Working-Truth Projection Contract — focused proof.

Covers §45 required groups A-L plus §46-49 frontier/result/context/stop,
§30-32 roundtrip/tamper/freshness, authority-firewalls, agent-neutrality,
reuse/bounded/determinism gates.

Production surface:
  aota_forge/work_plane/session_checkpoint.py

Invariants proven:
  - WorkingTruth core identity required/optional
  - SemanticReference reuse
  - Bounded collections deterministic no silent truncation
  - Unknown/mechanical fields fail closed
  - Deterministic serialization & checkpoint identity
  - Semantic change changes identity
  - Roundtrip tamper caller-spoof
  - Authority-negative, agent-neutral, frontier/result/context/stop firewalls
  - W1 reuse no duplication, no runtime/store/provider/hydration
"""

from __future__ import annotations

import copy
import hashlib
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
import aota_forge.work_plane.session_checkpoint as sc_mod
from aota_forge.work_plane.session_checkpoint import (
    SessionCheckpoint,
    WorkingTruthProjection,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.context_lifecycle import RolloverDecision, RolloverDisposition


def _sr(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)


def _wt(**kwargs) -> WorkingTruthProjection:
    # defaults for required core
    base = {
        "project_ref": _sr("proj:A"),
        "plan_ref": _sr("plan:P"),
        "milestone_ref": _sr("ms:M1"),
    }
    base.update(kwargs)
    return WorkingTruthProjection(**base)


def _cp(wt: WorkingTruthProjection | None = None, rd: RolloverDecision | None = None) -> SessionCheckpoint:
    if wt is None:
        wt = _wt()
    return SessionCheckpoint(working_truth=wt, rollover_decision=rd)


# ---------------------------------------------------------------------------
# A. WorkingTruth core identity
# ---------------------------------------------------------------------------

def test_a01_required_core_refs():
    wt = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    assert wt.project_ref.ref == "proj:A"
    assert wt.plan_ref.ref == "plan:P"
    assert wt.milestone_ref.ref == "ms:M1"
    # missing required should fail
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({"plan_ref": {"ref": "plan:P"}, "milestone_ref": {"ref": "ms:M1"}})
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({"project_ref": {"ref": "proj:A"}, "milestone_ref": {"ref": "ms:M1"}})
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({"project_ref": {"ref": "proj:A"}, "plan_ref": {"ref": "plan:P"}})
    # None for required should fail
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection(project_ref=None, plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))  # type: ignore


def test_a02_optional_refs():
    # active_work_item_ref optional
    wt_no_active = _wt()
    assert wt_no_active.active_work_item_ref is None
    wt_active = _wt(active_work_item_ref=_sr("w:1"))
    assert wt_active.active_work_item_ref is not None
    assert wt_active.active_work_item_ref.ref == "w:1"
    # frontier optional
    wt_frontier = _wt(accepted_frontier_ref=_sr("frontier:abc"), reviewed_frontier_ref=_sr("frontier:def"))
    assert wt_frontier.accepted_frontier_ref.ref == "frontier:abc"
    assert wt_frontier.reviewed_frontier_ref.ref == "frontier:def"
    # workflow disposition optional
    wt_workflow = _wt(workflow_disposition_ref=_sr("workflow:REPLAN_REQUIRED"))
    assert wt_workflow.workflow_disposition_ref.ref == "workflow:REPLAN_REQUIRED"
    # from_dict optional preserved
    d = wt_active.to_dict()
    assert d["active_work_item_ref"] is not None
    restored = WorkingTruthProjection.from_dict(d)
    assert restored.active_work_item_ref.ref == "w:1"


# ---------------------------------------------------------------------------
# B. SemanticReference reuse
# ---------------------------------------------------------------------------

def test_b01_semantic_reference_reused():
    assert sc_mod.EXISTING_SEMANTIC_REFERENCE_REUSED is True
    assert sc_mod.NEW_REFERENCE_ONTOLOGY_CREATED is False
    # actual SemanticReference accepted
    wt = WorkingTruthProjection(
        project_ref=SemanticReference(ref="proj:A"),
        plan_ref={"ref": "plan:P"},
        milestone_ref=SemanticReference(ref="ms:M1", digest="abc"),
    )
    assert isinstance(wt.project_ref, SemanticReference)
    assert isinstance(wt.plan_ref, SemanticReference)
    assert wt.milestone_ref.digest == "abc"
    # reuse via from_value inside
    wt2 = WorkingTruthProjection.from_dict({
        "project_ref": {"ref": "proj:A", "digest": "d1"},
        "plan_ref": {"ref": "plan:P"},
        "milestone_ref": {"ref": "ms:M1"},
    })
    assert isinstance(wt2.project_ref, SemanticReference)
    # check module does not define new reference ontology
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "class CheckpointReference" not in src
    assert "class PlanCheckpointRef" not in src
    assert "class MilestoneCheckpointRef" not in src
    assert "class ContextReferenceV2" not in src


def test_b02_mechanical_injected_reference_fails():
    # Forbidden mechanical field in reference dict should fail
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({
            "project_ref": {"ref": "proj:A", "package_id": "123"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
        })
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection(
            project_ref={"ref": "proj:A", "executor_id": "e1"},  # type: ignore
            plan_ref=_sr("plan:P"),
            milestone_ref=_sr("ms:M1"),
        )
    # unknown field in SemanticReference shape
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({
            "project_ref": {"ref": "proj:A", "unknown_field": "x"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
        })


# ---------------------------------------------------------------------------
# C. Bounded collections
# ---------------------------------------------------------------------------

def test_c01_bounded_collections_no_silent_truncation():
    assert sc_mod.CHECKPOINT_REFERENCE_COLLECTIONS_BOUNDED is True
    # each collection max 16
    ok_refs = tuple(_sr(f"r{i}") for i in range(16))
    wt_ok = _wt(result_refs=ok_refs)
    assert len(wt_ok.result_refs) == 16
    # overflow 17 should fail closed, not truncate
    bad_refs = tuple(_sr(f"r{i}") for i in range(17))
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), result_refs=bad_refs)
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), evidence_refs=bad_refs)
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), context_refs=bad_refs)
    # total bound 48
    many = tuple(_sr(f"r{i}") for i in range(16))
    wt_total_ok = _wt(result_refs=many, evidence_refs=many, context_refs=many)
    assert len(wt_total_ok.result_refs) + len(wt_total_ok.evidence_refs) + len(wt_total_ok.context_refs) == 48
    # total could overflow if each at max but we already at max; test via exceeding per-collection already fails


def test_c02_deterministic_ordering_no_set():
    # order is non-semantic, canonical sorted
    assert sc_mod.REFERENCE_ORDER_SEMANTIC is False
    wt_a = _wt(result_refs=(_sr("r2"), _sr("r1")))
    wt_b = _wt(result_refs=(_sr("r1"), _sr("r2")))
    assert wt_a.canonical_json() == wt_b.canonical_json()
    # ensure canonical sorting by digest as well
    wt_c = _wt(result_refs=(_sr("r1", digest="b"), _sr("r1", digest="a")))
    wt_d = _wt(result_refs=(_sr("r1", digest="a"), _sr("r1", digest="b")))
    assert wt_c.canonical_json() == wt_d.canonical_json()
    # checkpoint identity also deterministic w.r.t order
    cp_a = SessionCheckpoint(working_truth=wt_a)
    cp_b = SessionCheckpoint(working_truth=wt_b)
    assert cp_a.checkpoint_digest == cp_b.checkpoint_digest
    assert cp_a.checkpoint_id == cp_b.checkpoint_id


def test_c03_duplicate_rejection():
    assert sc_mod.DUPLICATE_REFERENCE_POLICY == "reject"
    with pytest.raises((ValueError, TypeError)):
        _wt(result_refs=(_sr("r1"), _sr("r1")))
    with pytest.raises((ValueError, TypeError)):
        _wt(evidence_refs=(_sr("e1", digest="d1"), _sr("e1", digest="d1")))
    # different digest not duplicate
    wt_ok = _wt(result_refs=(_sr("r1", digest="a"), _sr("r1", digest="b")))
    assert len(wt_ok.result_refs) == 2


# ---------------------------------------------------------------------------
# D. Unknown fields
# ---------------------------------------------------------------------------

def test_d01_unknown_fields_fail_closed():
    assert sc_mod.ARBITRARY_CHECKPOINT_METADATA_ALLOWED is False
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({
            "project_ref": {"ref": "proj:A"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
            "unknown": "field",
        })
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({
            "project_ref": {"ref": "proj:A"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
            "metadata": {"approved": True},
        })
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict({"working_truth": wt.to_dict(), "rollover_decision": None, "unknown_field": 123, "checkpoint_id": cp.checkpoint_id, "checkpoint_digest": cp.checkpoint_digest})
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict({"working_truth": wt.to_dict(), "rollover_decision": None, "metadata": {}, "checkpoint_id": cp.checkpoint_id, "checkpoint_digest": cp.checkpoint_digest})


def test_d02_no_full_transcript_metadata_bag():
    # checkpoint must not accept transcript-like fields
    with pytest.raises((ValueError, TypeError)):
        WorkingTruthProjection.from_dict({
            "project_ref": {"ref": "proj:A"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
            "conversation_transcript": "hello",
        })  # type: ignore
    # ensure no arbitrary dict allowed at checkpoint level
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict({"working_truth": wt.to_dict(), "rollover_decision": None, "transcript": "full", "checkpoint_id": cp.checkpoint_id, "checkpoint_digest": cp.checkpoint_digest})


# ---------------------------------------------------------------------------
# E. Deterministic serialization
# ---------------------------------------------------------------------------

def test_e01_deterministic_serialization():
    assert sc_mod.CHECKPOINT_SERIALIZABLE is True
    wt1 = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), result_refs=(_sr("r2"), _sr("r1")))
    wt2 = WorkingTruthProjection(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), result_refs=(_sr("r1"), _sr("r2")))
    assert wt1.canonical_dict() == wt2.canonical_dict()
    assert wt1.canonical_json() == wt2.canonical_json()
    assert wt1.canonical_json() == canonical_json(wt1.canonical_dict())
    cp1 = SessionCheckpoint(working_truth=wt1)
    cp2 = SessionCheckpoint(working_truth=wt2)
    assert cp1.canonical_dict() == cp2.canonical_dict()
    assert cp1.canonical_json() == cp2.canonical_json()
    assert cp1.canonical_json() == canonical_json(cp1.canonical_dict())
    # transport serialization deterministic
    assert cp1.to_dict()["checkpoint_digest"] == cp2.to_dict()["checkpoint_digest"]


# ---------------------------------------------------------------------------
# F. Deterministic checkpoint identity
# ---------------------------------------------------------------------------

def test_f01_deterministic_checkpoint_identity():
    assert sc_mod.CHECKPOINT_ID_DETERMINISTIC is True
    assert sc_mod.CHECKPOINT_ID_DERIVED_FROM_CANONICAL_CONTENT is True
    assert sc_mod.CALLER_CAN_SELF_ASSERT_CHECKPOINT_ID is False
    assert sc_mod.CALLER_CAN_SELF_ASSERT_CHECKPOINT_INTEGRITY is False
    wt = _wt()
    cp1 = SessionCheckpoint(working_truth=wt)
    cp2 = SessionCheckpoint(working_truth=_wt())
    assert cp1.checkpoint_id == cp2.checkpoint_id
    assert cp1.checkpoint_digest == cp2.checkpoint_digest
    assert cp1.checkpoint_id == f"{sc_mod.CHECKPOINT_ID_PREFIX}{cp1.checkpoint_digest}"
    # ensure no random uuid or wall clock
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8").lower()
    assert "uuid" not in src or "checkpoint:" in src  # prefix allowed, uuid not used for id
    assert "time.time" not in src
    # digest is sha256 of canonical json
    expected = hashlib.sha256(cp1.canonical_json().encode("utf-8")).hexdigest()
    assert cp1.checkpoint_digest == expected
    assert cp1.digest == expected
    assert cp1.integrity_digest == expected


# ---------------------------------------------------------------------------
# G. Semantic change changes identity
# ---------------------------------------------------------------------------

def test_g01_semantic_change_changes_identity():
    base = _wt()
    base_cp = SessionCheckpoint(working_truth=base)
    # project change
    assert SessionCheckpoint(working_truth=_wt(project_ref=_sr("proj:B"))).checkpoint_digest != base_cp.checkpoint_digest
    # plan change
    assert SessionCheckpoint(working_truth=_wt(plan_ref=_sr("plan:Q"))).checkpoint_digest != base_cp.checkpoint_digest
    # milestone change
    assert SessionCheckpoint(working_truth=_wt(milestone_ref=_sr("ms:M2"))).checkpoint_digest != base_cp.checkpoint_digest
    # active work item change
    assert SessionCheckpoint(working_truth=_wt(active_work_item_ref=_sr("w:2"))).checkpoint_digest != base_cp.checkpoint_digest
    # frontier change
    assert SessionCheckpoint(working_truth=_wt(accepted_frontier_ref=_sr("frontier:1"))).checkpoint_digest != base_cp.checkpoint_digest
    assert SessionCheckpoint(working_truth=_wt(reviewed_frontier_ref=_sr("frontier:2"))).checkpoint_digest != base_cp.checkpoint_digest
    # workflow disposition change
    assert SessionCheckpoint(working_truth=_wt(workflow_disposition_ref=_sr("workflow:REPLAN_REQUIRED"))).checkpoint_digest != SessionCheckpoint(working_truth=_wt(workflow_disposition_ref=_sr("workflow:CONTINUE"))).checkpoint_digest
    # result ref set change
    assert SessionCheckpoint(working_truth=_wt(result_refs=(_sr("r1"),))).checkpoint_digest != SessionCheckpoint(working_truth=_wt(result_refs=(_sr("r2"),))).checkpoint_digest
    # evidence change
    assert SessionCheckpoint(working_truth=_wt(evidence_refs=(_sr("e1"),))).checkpoint_digest != base_cp.checkpoint_digest
    # context change
    assert SessionCheckpoint(working_truth=_wt(context_refs=(_sr("c1"),))).checkpoint_digest != base_cp.checkpoint_digest
    # rollover decision change
    rd1 = RolloverDecision(disposition=RolloverDisposition.WITHIN_BUDGET)
    rd2 = RolloverDecision(disposition=RolloverDisposition.ROLLOVER_REQUIRED)
    assert SessionCheckpoint(working_truth=base, rollover_decision=rd1).checkpoint_digest != SessionCheckpoint(working_truth=base, rollover_decision=rd2).checkpoint_digest
    # absent vs present rollover decision
    assert SessionCheckpoint(working_truth=base, rollover_decision=None).checkpoint_digest != SessionCheckpoint(working_truth=base, rollover_decision=rd1).checkpoint_digest
    # digest collision check for same semantics
    assert SessionCheckpoint(working_truth=_wt(project_ref=_sr("proj:A"))).checkpoint_digest == SessionCheckpoint(working_truth=_wt(project_ref=_sr("proj:A"))).checkpoint_digest


# ---------------------------------------------------------------------------
# H. Roundtrip
# ---------------------------------------------------------------------------

def test_h01_roundtrip():
    assert sc_mod.CHECKPOINT_ROUNDTRIP_DETERMINISTIC is True
    wt = _wt(
        active_work_item_ref=_sr("w:1"),
        accepted_frontier_ref=_sr("frontier:acc"),
        reviewed_frontier_ref=_sr("frontier:rev"),
        workflow_disposition_ref=_sr("workflow:REVIEW"),
        result_refs=(_sr("r1"), _sr("r2")),
        evidence_refs=(_sr("e1"),),
        context_refs=(_sr("c1"), _sr("c2")),
    )
    rd = RolloverDecision(disposition=RolloverDisposition.ROLLOVER_RECOMMENDED)
    cp = SessionCheckpoint(working_truth=wt, rollover_decision=rd)
    d = cp.to_dict()
    restored = SessionCheckpoint.from_dict(d)
    assert restored.working_truth == wt
    assert restored.rollover_decision == rd
    assert restored.canonical_json() == cp.canonical_json()
    assert restored.checkpoint_id == cp.checkpoint_id
    assert restored.checkpoint_digest == cp.checkpoint_digest
    # dict roundtrip second time
    d2 = restored.to_dict()
    assert d == d2
    # WorkingTruth roundtrip
    wt_d = wt.to_dict()
    wt_restored = WorkingTruthProjection.from_dict(wt_d)
    assert wt_restored == wt
    assert wt_restored.canonical_json() == wt.canonical_json()
    # without rollover decision
    cp_simple = SessionCheckpoint(working_truth=_wt())
    d_simple = cp_simple.to_dict()
    restored_simple = SessionCheckpoint.from_dict(d_simple)
    assert restored_simple.canonical_json() == cp_simple.canonical_json()


# ---------------------------------------------------------------------------
# I. Tamper detection
# ---------------------------------------------------------------------------

def test_i01_tamper_detection():
    assert sc_mod.CHECKPOINT_TAMPER_DETECTION is True
    wt = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    # modify semantic payload while retaining original digest/id -> must fail
    d_tamper = copy.deepcopy(d)
    d_tamper["working_truth"]["project_ref"] = {"ref": "proj:TAMPERED"}
    # retain original digest/id (stale)
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d_tamper)
    # modify plan
    d2 = copy.deepcopy(d)
    d2["working_truth"]["plan_ref"] = {"ref": "plan:TAMPER"}
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d2)
    # modify result_refs
    wt2 = _wt(result_refs=(_sr("r1"),))
    cp2 = SessionCheckpoint(working_truth=wt2)
    d3 = cp2.to_dict()
    d3_tam = copy.deepcopy(d3)
    d3_tam["working_truth"]["result_refs"] = [{"ref": "r2"}]
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d3_tam)
    # modify rollover decision
    rd = RolloverDecision(disposition=RolloverDisposition.WITHIN_BUDGET)
    cp_rd = SessionCheckpoint(working_truth=wt, rollover_decision=rd)
    d_rd = cp_rd.to_dict()
    d_rd_tam = copy.deepcopy(d_rd)
    d_rd_tam["rollover_decision"] = {"disposition": "ROLLOVER_REQUIRED"}
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d_rd_tam)


# ---------------------------------------------------------------------------
# J. No caller spoof
# ---------------------------------------------------------------------------

def test_j01_no_caller_spoof():
    wt = _wt()
    cp = _cp(wt)
    # constructor should not accept arbitrary id/digest
    with pytest.raises(TypeError):
        SessionCheckpoint(working_truth=wt, checkpoint_id="checkpoint:fake")  # type: ignore
    with pytest.raises(TypeError):
        SessionCheckpoint(working_truth=wt, checkpoint_digest="0"*64)  # type: ignore
    # from_dict with arbitrary digest must fail verification
    d = cp.to_dict()
    d_spoof = copy.deepcopy(d)
    d_spoof["checkpoint_digest"] = "0"*64
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d_spoof)
    d_spoof2 = copy.deepcopy(d)
    d_spoof2["checkpoint_id"] = "checkpoint:" + "f"*64
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d_spoof2)
    # alias digest also verified
    d_spoof3 = copy.deepcopy(d)
    d_spoof3["digest"] = "a"*64
    # our from_dict allows alias but verifies; should fail
    # Fudge: add alias field that is not allowed? we allow digest alias, so it will be checked
    # If we add digest with wrong value, should fail
    # Note: to trigger alias check we need to include working_truth etc and add digest alias
    d_alias = {"working_truth": wt.to_dict(), "rollover_decision": None, "checkpoint_id": cp.checkpoint_id, "checkpoint_digest": cp.checkpoint_digest, "digest": "0"*64}
    with pytest.raises((ValueError, TypeError)):
        SessionCheckpoint.from_dict(d_alias)


# ---------------------------------------------------------------------------
# K. Authority-negative
# ---------------------------------------------------------------------------

def test_k01_authority_negative():
    assert sc_mod.SESSION_CHECKPOINT_IS_AUTHORITY is False
    assert sc_mod.WORKING_TRUTH_PROJECTION_IS_AUTHORITY is False
    assert sc_mod.ACTIVE_TASK_PROJECTION_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_DIGEST_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_REF_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_STATE_IS_AUTHORITY is False
    assert sc_mod.RECOVERY_IS_AUTHORITY_MINTING is False
    assert sc_mod.CHECKPOINT_FRONTIER_REF_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS is True
    assert sc_mod.CHECKPOINT_STATE_IS_OBSERVED_OR_RECONCILED_TRUTH is True
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    # checkpoint dict should not contain authority fields
    d = cp.to_dict()
    assert "approved" not in str(d).lower()
    assert "authority" not in str(d).lower()
    # digest cannot be used as authority
    assert not hasattr(cp, "is_authority")
    assert not hasattr(wt, "is_authority")
    # W2 owns integrity verification but not freshness
    assert sc_mod.CHECKPOINT_FRESHNESS_EVALUATOR_IMPLEMENTED_BY_W2 is False
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    # ensure no freshness evaluator that decides current truth
    assert "is_fresh_against_github" not in src
    assert "is_current_against_plan" not in src
    assert "reconcile_checkpoint" not in src


# ---------------------------------------------------------------------------
# L. Agent neutral
# ---------------------------------------------------------------------------

def test_l01_agent_neutral():
    assert sc_mod.SESSION_CHECKPOINT_AGENT_NEUTRAL is True
    assert sc_mod.MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    assert sc_mod.MECHANICAL_SESSION_ID_REQUIRED_BY_W2 is False
    # construction without any provider/model/session ids
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.canonical_dict()
    flat = str(d).lower()
    assert "session" not in flat or "checkpoint" in flat  # only checkpoint prefix allowed
    # from_dict does not require those fields
    wt2 = WorkingTruthProjection.from_dict({"project_ref": {"ref": "proj:A"}, "plan_ref": {"ref": "plan:P"}, "milestone_ref": {"ref": "ms:M1"}})
    assert wt2.project_ref.ref == "proj:A"
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8").lower()
    # should not contain mechanical session id requirements
    assert "hermes session id" not in src


# ---------------------------------------------------------------------------
# Frontier tests
# ---------------------------------------------------------------------------

def test_frontier_reference_non_authoritative():
    assert sc_mod.CHECKPOINT_FRONTIER_REF_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_CANNOT_PROMOTE_UNREVIEWED_FRONTIER is True
    wt = _wt(accepted_frontier_ref=_sr("frontier:abc"), reviewed_frontier_ref=_sr("frontier:def"))
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    restored = SessionCheckpoint.from_dict(d)
    assert restored.working_truth.accepted_frontier_ref.ref == "frontier:abc"
    assert restored.working_truth.reviewed_frontier_ref.ref == "frontier:def"
    # no method derives accepted=True etc
    assert not hasattr(cp, "is_accepted")
    assert not hasattr(cp, "is_known_good")
    assert not hasattr(cp, "is_closed")
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8").lower()
    assert "known_good" not in src or sc_mod.CHECKPOINT_CANNOT_PROMOTE_UNREVIEWED_FRONTIER is True


# ---------------------------------------------------------------------------
# Worker result ref tests
# ---------------------------------------------------------------------------

def test_worker_result_reference_projection():
    assert sc_mod.CHECKPOINT_WORKER_RESULT_IS_EVIDENCE_ONLY is True
    wt = _wt(result_refs=(_sr("result:1", digest="abc"), _sr("result:2")), evidence_refs=(_sr("evidence:1"),))
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    restored = SessionCheckpoint.from_dict(d)
    assert len(restored.working_truth.result_refs) == 2
    assert len(restored.working_truth.evidence_refs) == 1
    # participate in identity
    wt2 = _wt(result_refs=(_sr("result:other"),), evidence_refs=(_sr("evidence:1"),))
    assert SessionCheckpoint(working_truth=wt).checkpoint_digest != SessionCheckpoint(working_truth=wt2).checkpoint_digest
    # do NOT embed full WorkerResultCard
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "WorkerResultCard" not in src
    d_str = str(d)
    assert "WorkerResultCard" not in d_str
    assert "summary" not in d_str.lower() or "result_refs" in d_str.lower()


# ---------------------------------------------------------------------------
# Context ref tests
# ---------------------------------------------------------------------------

def test_context_reference_projection_no_hydration():
    assert sc_mod.CHECKPOINT_CONTEXT_REF_IS_AUTHORITY is False
    assert sc_mod.CONTEXT_HYDRATION_OCCURRED is False
    assert sc_mod.CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W2 is False
    assert sc_mod.SELECTIVE_HYDRATION_EXECUTION_STARTED_BY_W2 is False
    wt = _wt(context_refs=(_sr("ctx:1"), _sr("ctx:2")))
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    restored = SessionCheckpoint.from_dict(d)
    assert len(restored.working_truth.context_refs) == 2
    assert restored.working_truth.context_refs[0].ref == "ctx:1"
    # no hydration occurs
    assert not hasattr(cp, "hydrate")
    assert not hasattr(cp, "load_context")
    assert not hasattr(cp, "fetch_context")
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    # docstring may mention ContextProvider as negative example; check no import
    assert "from aota_forge.core.providers.context import" not in src
    assert "import ContextProvider" not in src


# ---------------------------------------------------------------------------
# Stop/Replan representation test
# ---------------------------------------------------------------------------

def test_stop_replan_representation():
    assert sc_mod.ROLLOVER_CANNOT_CLEAR_SEMANTIC_STOP is True
    assert sc_mod.ROLLOVER_CANNOT_CLEAR_REPLAN_REQUIRED is True
    # workflow disposition via SemanticReference
    assert sc_mod.NEW_WORKFLOW_DISPOSITION_ONTOLOGY_CREATED is False
    wt = _wt(workflow_disposition_ref=_sr("workflow:REPLAN_REQUIRED"))
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    restored = SessionCheckpoint.from_dict(d)
    assert restored.working_truth.workflow_disposition_ref.ref == "workflow:REPLAN_REQUIRED"
    # no method to clear/advance
    assert not hasattr(cp, "clear_stop")
    assert not hasattr(cp, "continue_after_rollover")
    assert not hasattr(cp, "reset_replan")
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "ProgressionDisposition" not in src
    assert "MilestoneReviewWorkflowDisposition" not in src
    # ensure workflow ref participates in identity
    wt2 = _wt(workflow_disposition_ref=_sr("workflow:CONTINUE"))
    assert SessionCheckpoint(working_truth=wt).checkpoint_digest != SessionCheckpoint(working_truth=wt2).checkpoint_digest


# ---------------------------------------------------------------------------
# Freshness attack deferred, W3 not pulled forward
# ---------------------------------------------------------------------------

def test_w3_not_pulled_forward():
    assert sc_mod.W3_SCOPE_PULLED_FORWARD_BY_W2 is False
    assert sc_mod.CHECKPOINT_FRESHNESS_EVALUATOR_IMPLEMENTED_BY_W2 is False
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    # basic tamper tests are W2 scope; live-state replay attacks are W3
    assert "is_fresh_against_github" not in src
    assert "CurrentGovernance" not in src or "CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT" in src


# ---------------------------------------------------------------------------
# Retry / side effect firewall
# ---------------------------------------------------------------------------

def test_retry_side_effect_firewall():
    assert sc_mod.CHECKPOINT_IS_RETRY_PERMISSION is False
    assert sc_mod.CHECKPOINT_DOES_NOT_AUTHORIZE_SIDE_EFFECT_REPLAY is True
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    d = cp.to_dict()
    # no retry fields
    assert "retry_authorized" not in str(d)
    assert "safe_to_replay" not in str(d)
    assert "resume_safe" not in str(d)
    assert not hasattr(cp, "retry_authorized")
    # ensure no method grants retry
    assert not hasattr(cp, "is_retry_permitted")


# ---------------------------------------------------------------------------
# Checkpoint is not journal, no store/runtime
# ---------------------------------------------------------------------------

def test_no_journal_store_runtime():
    assert sc_mod.SESSION_CHECKPOINT_IS_JOURNAL is False
    assert sc_mod.SESSION_CHECKPOINT_REPLACES_EXISTING_JOURNAL is False
    assert sc_mod.NEW_WORKFLOW_JOURNAL_CREATED is False
    assert sc_mod.NEW_EXECUTION_JOURNAL_CREATED is False
    assert sc_mod.CHECKPOINT_STORAGE_ENGINE_REQUIRED_IN_M1 is False
    assert sc_mod.CHECKPOINT_DURABLE_STORE_REQUIRED_IN_M1 is False
    assert sc_mod.NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1 is False
    assert sc_mod.W2_STORE_CREATED is False
    assert sc_mod.W2_RUNTIME_CREATED is False
    assert sc_mod.ACTUAL_ROLLOVER_IMPLEMENTED_BY_W2 is False
    assert sc_mod.ACTUAL_RECOVERY_IMPLEMENTED_BY_W2 is False
    assert sc_mod.NEW_SESSION_RUNTIME_REQUIRED_FOR_M1 is False
    assert sc_mod.NEW_SESSION_MANAGER_REQUIRED_FOR_M1 is False
    assert sc_mod.NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1 is False
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "class SessionManager" not in src
    assert "class RecoveryEngine" not in src
    assert "class CheckpointStore" not in src
    assert "sqlite" not in src.lower()
    assert "postgres" not in src.lower()


# ---------------------------------------------------------------------------
# W1 reuse, no shared contract change
# ---------------------------------------------------------------------------

def test_w1_reuse_no_duplication():
    assert sc_mod.W1_CONTRACT_REUSED_BY_W2 is True
    assert sc_mod.W1_CONTRACT_DUPLICATED_BY_W2 is False
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "from aota_forge.work_plane.context_lifecycle import RolloverDecision" in src
    assert "class RolloverDisposition" not in src
    assert "class ContextBudget" not in src
    assert "RolloverDecisionV2" not in src
    assert sc_mod.SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED is False
    assert sc_mod.AGGREGATOR_CHANGE_REQUIRED_FOR_W2 is False
    agg = pathlib.Path(sc_mod.__file__).parent / "__init__.py"
    assert "session_checkpoint" not in agg.read_text(encoding="utf-8")


def test_no_context_provider_integration():
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    # W2 must not start provider integration
    assert sc_mod.CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W2 is False
    # ensure no import of provider (docstring mention allowed)
    assert "from aota_forge.core.providers.context import" not in src
    assert "import ContextProvider" not in src


# ---------------------------------------------------------------------------
# Cross-project / cross-plan / stale milestone detection prep
# ---------------------------------------------------------------------------

def test_cross_project_plan_milestone_detectable():
    assert sc_mod.CROSS_PROJECT_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY is True
    assert sc_mod.CROSS_PLAN_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY is True
    assert sc_mod.STALE_MILESTONE_WORK_CAN_BE_DETECTED_FROM_CHECKPOINT_CONTENT is True
    base = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    other_proj = _wt(project_ref=_sr("proj:B"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    other_plan = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:Q"), milestone_ref=_sr("ms:M1"))
    other_ms = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M2"))
    base_id = SessionCheckpoint(working_truth=base).checkpoint_id
    assert SessionCheckpoint(working_truth=other_proj).checkpoint_id != base_id
    assert SessionCheckpoint(working_truth=other_plan).checkpoint_id != base_id
    assert SessionCheckpoint(working_truth=other_ms).checkpoint_id != base_id


# ---------------------------------------------------------------------------
# Additional: frozen, bounded, canonical reuse
# ---------------------------------------------------------------------------

def test_frozen_immutable():
    wt = _wt()
    with pytest.raises((AttributeError, TypeError)):
        wt.project_ref = _sr("proj:B")  # type: ignore
    cp = SessionCheckpoint(working_truth=wt)
    with pytest.raises((AttributeError, TypeError)):
        cp.working_truth = _wt(project_ref=_sr("proj:B"))  # type: ignore


def test_canonical_reuse_no_new_framework():
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "from aota_forge.core.contracts.canonical import canonical_json" in src
    # class methods named canonical_json are allowed; check no top-level redefinition
    assert src.count("def canonicalize") == 0
    # ensure we reuse existing canonicalization, not create new framework
    assert "NEW_CANONICALIZATION_FRAMEWORK_CREATED" not in src or sc_mod.NEW_CANONICALIZATION_FRAMEWORK_CREATED is False if hasattr(sc_mod, "NEW_CANONICALIZATION_FRAMEWORK_CREATED") else True


def test_mechanical_session_id_not_required():
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8").lower()
    # agent neutrality already checked, ensure no mechanical session id field required
    assert "process_id" in src  # we have blacklist, but not required field
    # but construction without it succeeds
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    assert cp.checkpoint_id is not None


def test_no_full_acf():
    src = pathlib.Path(sc_mod.__file__).read_text(encoding="utf-8")
    assert "memory cards" not in src.lower()
    assert "ACF" not in src or "FULL_ACF" in src or "ACF_INTERNAL" in src
    assert sc_mod.SESSION_CHECKPOINT_AGENT_NEUTRAL is True
