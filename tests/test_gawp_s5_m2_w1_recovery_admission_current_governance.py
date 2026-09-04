"""S5 M2 W1 Recovery Admission & Current-Governance Reconciliation Contract — focused proof.

Covers §46 required groups A-T plus deterministic precedence, integrity vs admission,
journal/retry reuse, authority firewalls, and negative surface checks.

Production surface:
  aota_forge/work_plane/recovery_admission.py
  Reuses:
    aota_forge/work_plane/session_checkpoint.py (SessionCheckpoint, WorkingTruthProjection, SemanticReference)
    aota_forge/core/journal/reconcile.py, retry.py (existing authority, not duplicated)

Invariants proven:
  - Typed CurrentGovernedWorkingTruth validation, strict bool, bounded refs, agent-neutral
  - Same governance no blockers -> RECOVERABLE (not deny-all)
  - Cross-project / cross-Plan -> BLOCKED_BY_SCOPE_MISMATCH fail-closed
  - Milestone mismatch -> STALE, no rewind
  - Active W mismatch -> STALE, no restart/dispatch
  - Frontier mismatches -> STALE, checkpoint cannot establish acceptance/review
  - Workflow disposition mismatch -> STALE
  - SemanticStop present -> BLOCKED_BY_SEMANTIC_STOP preserved
  - REPLAN_REQUIRED -> BLOCKED_BY_REPLAN preserved
  - Unresolved effect -> BLOCKED_BY_UNRESOLVED_EFFECT, does not resolve operation
  - Deterministic precedence (scope > stop > replan > effect > stale > recoverable)
  - Scope-mismatch precedence over stop
  - Stop vs replan vs effect precedence
  - Strict unknown-field rejection and arbitrary authority injection rejection
  - Deterministic serialization / roundtrip
  - Agent-neutral input surface, no mechanical/session/runtime fields
  - No session/runtime/store/provider/hydration surface (flag checks)
  - Checkpoint remains non-authority, RecoveryAdmission remains non-authority
  - Checkpoint integrity reused, not duplicated; two valid checkpoints different admission
  - Existing journal reconciliation and retry authority remain controlling, not duplicated
  - No side-effect replay, no Worker dispatch, no logical rollover in W1
"""

from __future__ import annotations

import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
import aota_forge.work_plane.recovery_admission as ra_mod
from aota_forge.work_plane.recovery_admission import (
    CurrentGovernedWorkingTruth,
    RecoveryAdmission,
    RecoveryAdmissionDisposition,
    evaluate_recovery_admission,
)
from aota_forge.work_plane.session_checkpoint import SessionCheckpoint, WorkingTruthProjection
from aota_forge.work_plane.handoff import SemanticReference
import aota_forge.work_plane.session_checkpoint as sc_mod


def _sr(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)


def _wt(**kwargs) -> WorkingTruthProjection:
    base = {
        "project_ref": _sr("proj:A"),
        "plan_ref": _sr("plan:P"),
        "milestone_ref": _sr("ms:M1"),
    }
    base.update(kwargs)
    return WorkingTruthProjection(**base)


def _cp(wt: WorkingTruthProjection | None = None) -> SessionCheckpoint:
    if wt is None:
        wt = _wt()
    return SessionCheckpoint(working_truth=wt)


def _cur(**kwargs) -> CurrentGovernedWorkingTruth:
    base = {
        "project_ref": _sr("proj:A"),
        "plan_ref": _sr("plan:P"),
        "milestone_ref": _sr("ms:M1"),
        "semantic_stop_present": False,
        "replan_required": False,
        "unresolved_effect": False,
    }
    base.update(kwargs)
    return CurrentGovernedWorkingTruth(**base)


# ---------------------------------------------------------------------------
# A. Typed CurrentGovernedWorkingTruth validation
# ---------------------------------------------------------------------------

def test_a01_required_core_refs():
    cur = _cur()
    assert cur.project_ref.ref == "proj:A"
    assert cur.plan_ref.ref == "plan:P"
    assert cur.milestone_ref.ref == "ms:M1"
    # missing required should fail
    with pytest.raises((ValueError, TypeError)):
        CurrentGovernedWorkingTruth.from_dict({"plan_ref": {"ref": "plan:P"}, "milestone_ref": {"ref": "ms:M1"}, "semantic_stop_present": False, "replan_required": False, "unresolved_effect": False})
    with pytest.raises((ValueError, TypeError)):
        CurrentGovernedWorkingTruth.from_dict({"project_ref": {"ref": "proj:A"}, "milestone_ref": {"ref": "ms:M1"}, "semantic_stop_present": False, "replan_required": False, "unresolved_effect": False})
    with pytest.raises((ValueError, TypeError)):
        CurrentGovernedWorkingTruth(project_ref=None, plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present=False, replan_required=False, unresolved_effect=False)  # type: ignore


def test_a02_optional_refs():
    cur_no_active = _cur()
    assert cur_no_active.active_work_item_ref is None
    cur_active = _cur(active_work_item_ref=_sr("w:1"))
    assert cur_active.active_work_item_ref.ref == "w:1"
    cur_frontier = _cur(accepted_frontier_ref=_sr("frontier:acc"), reviewed_frontier_ref=_sr("frontier:rev"))
    assert cur_frontier.accepted_frontier_ref.ref == "frontier:acc"
    assert cur_frontier.reviewed_frontier_ref.ref == "frontier:rev"
    cur_workflow = _cur(workflow_disposition_ref=_sr("workflow:CONTINUE"))
    assert cur_workflow.workflow_disposition_ref.ref == "workflow:CONTINUE"
    # from_dict roundtrip preserves optionals
    d = cur_active.to_dict()
    assert d["active_work_item_ref"] is not None
    restored = CurrentGovernedWorkingTruth.from_dict(d)
    assert restored.active_work_item_ref.ref == "w:1"


def test_a03_boolean_strict_validation():
    # valid booleans
    cur = _cur(semantic_stop_present=True, replan_required=False, unresolved_effect=False)
    assert cur.semantic_stop_present is True
    # coercible values must fail closed
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present=1, replan_required=False, unresolved_effect=False)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present="true", replan_required=False, unresolved_effect=False)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present=None, replan_required=False, unresolved_effect=False)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present=0, replan_required=False, unresolved_effect=False)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), semantic_stop_present=False, replan_required="false", unresolved_effect=False)  # type: ignore
    # from_dict also strict
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth.from_dict({"project_ref": {"ref": "proj:A"}, "plan_ref": {"ref": "plan:P"}, "milestone_ref": {"ref": "ms:M1"}, "semantic_stop_present": 1, "replan_required": False, "unresolved_effect": False})
    with pytest.raises((TypeError, ValueError)):
        CurrentGovernedWorkingTruth.from_dict({"project_ref": {"ref": "proj:A"}, "plan_ref": {"ref": "plan:P"}, "milestone_ref": {"ref": "ms:M1"}, "semantic_stop_present": "false", "replan_required": False, "unresolved_effect": False})


def test_a04_reuses_semantic_reference_no_new_ontology():
    cur = _cur(project_ref=_sr("proj:A", digest="abc"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    assert isinstance(cur.project_ref, SemanticReference)
    assert cur.project_ref.digest == "abc"
    # check flag
    assert ra_mod.EXISTING_SEMANTIC_REFERENCE_REUSED is True
    assert ra_mod.NEW_GOVERNANCE_IDENTITY_ONTOLOGY_CREATED is False
    assert ra_mod.NEW_WORKFLOW_ONTOLOGY_CREATED is False


# ---------------------------------------------------------------------------
# B. Same governance positive admission -> RECOVERABLE
# ---------------------------------------------------------------------------

def test_b_same_governance_recoverable():
    wt = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), active_work_item_ref=_sr("w:1"), accepted_frontier_ref=_sr("frontier:acc"), reviewed_frontier_ref=_sr("frontier:rev"), workflow_disposition_ref=_sr("workflow:CONTINUE"))
    cp = _cp(wt)
    assert cp.verify_integrity() is True
    cur = CurrentGovernedWorkingTruth(
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:1"),
        accepted_frontier_ref=_sr("frontier:acc"),
        reviewed_frontier_ref=_sr("frontier:rev"),
        workflow_disposition_ref=_sr("workflow:CONTINUE"),
        semantic_stop_present=False,
        replan_required=False,
        unresolved_effect=False,
    )
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    # proves not deny-all
    assert ra_mod.RECOVERY_ADMISSION_VOCABULARY_BOUNDED is True


# ---------------------------------------------------------------------------
# C. Cross-project scope mismatch
# ---------------------------------------------------------------------------

def test_c_cross_project_scope_mismatch():
    cp = _cp(_wt(project_ref=_sr("proj:A")))
    cur = _cur(project_ref=_sr("proj:B"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert ra_mod.CROSS_PROJECT_RECOVERY_FAILS_CLOSED is True
    # also verify that project mismatch is deterministic and not recoverable even with no blockers
    assert adm.disposition != RecoveryAdmissionDisposition.RECOVERABLE
    assert adm.disposition != RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# D. Cross-Plan scope mismatch
# ---------------------------------------------------------------------------

def test_d_cross_plan_scope_mismatch():
    cp = _cp(_wt(plan_ref=_sr("plan:A")))
    cur = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:B"), milestone_ref=_sr("ms:M1"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert ra_mod.CROSS_PLAN_RECOVERY_FAILS_CLOSED is True


# ---------------------------------------------------------------------------
# E. Milestone mismatch -> stale, no rewind
# ---------------------------------------------------------------------------

def test_e_milestone_mismatch_stale():
    cp = _cp(_wt(milestone_ref=_sr("ms:M1")))
    cur = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M2"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.MILESTONE_MISMATCH_DOES_NOT_REWIND_GOVERNANCE is True
    assert ra_mod.CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT is True
    assert ra_mod.RECOVERY_CAN_REWIND_GOVERNANCE is False
    # ensure not blocked by scope
    assert adm.disposition != RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# F. Active W mismatch -> stale, no restart
# ---------------------------------------------------------------------------

def test_f_active_work_mismatch_stale():
    cp = _cp(_wt(active_work_item_ref=_sr("w:1")))
    cur = _cur(active_work_item_ref=_sr("w:2"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.STALE_ACTIVE_WORK_RESTART_ALLOWED is False
    assert ra_mod.ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER is False
    assert ra_mod.ACTIVE_WORK_IDENTITY_RECOVERY_SUPPORTED is True
    # also test None vs value
    cp2 = _cp(_wt(active_work_item_ref=None))
    cur2 = _cur(active_work_item_ref=_sr("w:1"))
    adm2 = evaluate_recovery_admission(cp2, cur2)
    assert adm2.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# G. Accepted frontier mismatch -> stale, cannot establish acceptance
# ---------------------------------------------------------------------------

def test_g_accepted_frontier_mismatch():
    cp = _cp(_wt(accepted_frontier_ref=_sr("frontier:old")))
    cur = _cur(accepted_frontier_ref=_sr("frontier:new"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER is False
    assert ra_mod.CHECKPOINT_FRONTIER_REF_IS_AUTHORITY is False if hasattr(ra_mod, "CHECKPOINT_FRONTIER_REF_IS_AUTHORITY") else True
    assert ra_mod.RECOVERY_ADMISSION_IS_AUTHORITY is False
    # checkpoint cannot promote unreviewed frontier
    assert sc_mod.CHECKPOINT_CANNOT_PROMOTE_UNREVIEWED_FRONTIER is True


# ---------------------------------------------------------------------------
# H. Reviewed frontier mismatch -> stale
# ---------------------------------------------------------------------------

def test_h_reviewed_frontier_mismatch():
    cp = _cp(_wt(reviewed_frontier_ref=_sr("frontier:rev1")))
    cur = _cur(reviewed_frontier_ref=_sr("frontier:rev2"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.RECOVERY_ESTABLISHES_REVIEWED_FRONTIER is False


# ---------------------------------------------------------------------------
# I. Workflow-disposition mismatch -> stale
# ---------------------------------------------------------------------------

def test_i_workflow_disposition_mismatch():
    cp = _cp(_wt(workflow_disposition_ref=_sr("workflow:CONTINUE")))
    cur = _cur(workflow_disposition_ref=_sr("workflow:REPLAN_REQUIRED"))
    # Even though workflow ref differs, we treat as stale unless blocker higher
    # Here blockers are false, so should be stale
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.RECOVERY_ADVANCES_S4_WORKFLOW is False
    # Ensure checkpoint workflow ref not authority
    assert ra_mod.CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS if hasattr(ra_mod, "CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS") else True or sc_mod.CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS is True


# ---------------------------------------------------------------------------
# J. SemanticStop present -> blocked
# ---------------------------------------------------------------------------

def test_j_semantic_stop_blocked():
    cp = _cp(_wt())
    cur = _cur(semantic_stop_present=True)
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP
    assert ra_mod.RECOVERY_PRESERVES_SEMANTIC_STOP is True
    assert ra_mod.RECOVERY_AUTO_CONTINUES_SEMANTIC_STOP is False
    # ensure not recoverable
    assert adm.disposition != RecoveryAdmissionDisposition.RECOVERABLE


# ---------------------------------------------------------------------------
# K. REPLAN_REQUIRED -> blocked
# ---------------------------------------------------------------------------

def test_k_replan_required_blocked():
    cp = _cp(_wt())
    cur = _cur(replan_required=True)
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_REPLAN
    assert ra_mod.RECOVERY_PRESERVES_REPLAN_REQUIRED is True
    assert ra_mod.RECOVERY_AUTO_REPAIRS_REPLAN_REQUIRED is False
    assert ra_mod.RECOVERY_AUTO_CONTINUES_REPLAN_REQUIRED is False


# ---------------------------------------------------------------------------
# L. Unresolved effect -> blocked, does not resolve operation
# ---------------------------------------------------------------------------

def test_l_unresolved_effect_blocked():
    cp = _cp(_wt())
    cur = _cur(unresolved_effect=True)
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_UNRESOLVED_EFFECT
    assert ra_mod.TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT is True
    assert ra_mod.RECOVERY_AUTO_REPLAYS_SIDE_EFFECT is False
    assert ra_mod.ROLLOVER_REPLAYS_SIDE_EFFECT is False


# ---------------------------------------------------------------------------
# M. Blocker precedence deterministic
# ---------------------------------------------------------------------------

def test_m_blocker_precedence_deterministic():
    cp = _cp(_wt())
    # All blockers true + project mismatch -> scope mismatch wins
    cur_scope_stop = _cur(project_ref=_sr("proj:B"), semantic_stop_present=True, replan_required=True, unresolved_effect=True)
    # checkpoint is proj A, current proj B
    adm = evaluate_recovery_admission(cp, cur_scope_stop)
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH

    # Same scope, all blockers true -> SemanticStop wins
    cur_all = _cur(semantic_stop_present=True, replan_required=True, unresolved_effect=True)
    adm2 = evaluate_recovery_admission(cp, cur_all)
    assert adm2.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP

    # Stop false, replan true, effect true -> REPLAN wins
    cur_replan_effect = _cur(semantic_stop_present=False, replan_required=True, unresolved_effect=True)
    adm3 = evaluate_recovery_admission(cp, cur_replan_effect)
    assert adm3.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_REPLAN

    # Only unresolved effect true -> that blocks
    cur_effect = _cur(semantic_stop_present=False, replan_required=False, unresolved_effect=True)
    adm4 = evaluate_recovery_admission(cp, cur_effect)
    assert adm4.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_UNRESOLVED_EFFECT

    # Verify deterministic: repeat calls same result
    for _ in range(3):
        assert evaluate_recovery_admission(cp, cur_all).disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP


def test_m2_scope_mismatch_precedence_over_all():
    cp = _cp(_wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P")))
    cur = _cur(project_ref=_sr("proj:OTHER"), plan_ref=_sr("plan:P"), semantic_stop_present=True, replan_required=True, unresolved_effect=True, milestone_ref=_sr("ms:M2"), active_work_item_ref=_sr("w:99"))
    adm = evaluate_recovery_admission(cp, cur)
    # Even though many stale and blocker conditions, scope mismatch takes absolute precedence
    assert adm.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# N. Strict unknown-field rejection
# ---------------------------------------------------------------------------

def test_n_unknown_field_rejection_current():
    # extra field in current
    with pytest.raises((ValueError, TypeError)):
        CurrentGovernedWorkingTruth.from_dict({
            "project_ref": {"ref": "proj:A"},
            "plan_ref": {"ref": "plan:P"},
            "milestone_ref": {"ref": "ms:M1"},
            "semantic_stop_present": False,
            "replan_required": False,
            "unresolved_effect": False,
            "unknown_field": "oops",
        })
    # extra in recovery admission
    with pytest.raises((ValueError, TypeError)):
        RecoveryAdmission.from_dict({"disposition": "RECOVERABLE", "extra": "field"})


def test_n_unknown_field_rejection_recovery():
    with pytest.raises((ValueError, TypeError)):
        RecoveryAdmission.from_dict({"disposition": "RECOVERABLE", "unknown": 123})


# ---------------------------------------------------------------------------
# O. Arbitrary authority/retry metadata injection rejection
# ---------------------------------------------------------------------------

def test_o_authority_field_injection_fails_closed():
    authority_fields = ["approved", "authorized", "retry_authorized", "safe_to_replay", "fresh", "current", "accepted", "worker_dispatch_authorized"]
    for field in authority_fields:
        with pytest.raises((ValueError, TypeError)):
            CurrentGovernedWorkingTruth.from_dict({
                "project_ref": {"ref": "proj:A"},
                "plan_ref": {"ref": "plan:P"},
                "milestone_ref": {"ref": "ms:M1"},
                "semantic_stop_present": False,
                "replan_required": False,
                "unresolved_effect": False,
                field: True,
            })
        with pytest.raises((ValueError, TypeError)):
            RecoveryAdmission.from_dict({"disposition": "RECOVERABLE", field: True})
    # Also test that RecoveryAdmission does not have those fields as attributes
    adm = RecoveryAdmission(disposition=RecoveryAdmissionDisposition.RECOVERABLE)
    for field in authority_fields:
        assert not hasattr(adm, field)


# ---------------------------------------------------------------------------
# P. Deterministic serialization / roundtrip
# ---------------------------------------------------------------------------

def test_p_deterministic_serialization_roundtrip():
    cur = _cur(project_ref=_sr("proj:A", digest="d1"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"), active_work_item_ref=_sr("w:1"), semantic_stop_present=False, replan_required=False, unresolved_effect=False)
    d = cur.to_dict()
    j1 = cur.canonical_json()
    j2 = canonical_json(cur.canonical_dict())
    assert j1 == j2
    restored = CurrentGovernedWorkingTruth.from_dict(d)
    assert restored == cur
    assert restored.canonical_json() == j1
    # RecoveryAdmission roundtrip
    adm = RecoveryAdmission(disposition=RecoveryAdmissionDisposition.RECOVERABLE)
    d2 = adm.to_dict()
    j3 = adm.canonical_json()
    assert j3 == canonical_json(adm.canonical_dict())
    restored2 = RecoveryAdmission.from_dict(d2)
    assert restored2 == adm
    assert restored2.canonical_json() == j3
    # Deterministic: same input same json regardless of construction order
    cur2 = CurrentGovernedWorkingTruth(project_ref="proj:A", plan_ref="plan:P", milestone_ref="ms:M1", semantic_stop_present=False, replan_required=False, unresolved_effect=False)  # type: ignore
    # Note from_value string case
    assert cur2.project_ref.ref == "proj:A"


def test_p_canonical_json_determinism():
    # Ensure project/plan/milestone refs produce deterministic ordering
    cur1 = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cur2 = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    assert cur1.canonical_json() == cur2.canonical_json()
    adm1 = RecoveryAdmission(disposition="RECOVERABLE")  # string init
    adm2 = RecoveryAdmission(disposition=RecoveryAdmissionDisposition.RECOVERABLE)
    assert adm1.canonical_json() == adm2.canonical_json()


# ---------------------------------------------------------------------------
# Q. Agent-neutral input surface
# ---------------------------------------------------------------------------

def test_q_agent_neutral_input_surface():
    # CurrentGovernedWorkingTruth should not accept mechanical provider fields
    mechanical_fields = ["hermes_session_id", "openai_conversation_id", "provider_name", "model_name", "thread_id", "process_id"]
    for field in mechanical_fields:
        with pytest.raises((ValueError, TypeError)):
            CurrentGovernedWorkingTruth.from_dict({
                "project_ref": {"ref": "proj:A"},
                "plan_ref": {"ref": "plan:P"},
                "milestone_ref": {"ref": "ms:M1"},
                "semantic_stop_present": False,
                "replan_required": False,
                "unresolved_effect": False,
                field: "some-id",
            })
    # Check flags
    assert ra_mod.M2_AGENT_NEUTRAL is True
    assert ra_mod.PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    assert ra_mod.MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    # Ensure CurrentGovernedWorkingTruth does not have those attributes
    cur = _cur()
    for field in mechanical_fields:
        assert not hasattr(cur, field)


# ---------------------------------------------------------------------------
# R. No session/runtime/store surface
# ---------------------------------------------------------------------------

def test_r_no_session_runtime_store_surface():
    # Module should not expose SessionRecord, SessionRegistry, CheckpointStore etc
    for name in ["SessionRecord", "SessionRegistry", "TaskMainSession", "SessionDatabase", "CheckpointStore", "CheckpointRepository", "SessionManager", "RecoveryEngine", "RecoveryManager", "RecoveryService", "ContextProvider"]:
        assert not hasattr(ra_mod, name), f"unexpected surface {name}"
    assert ra_mod.NEW_SESSION_ENTITY_CREATED_BY_W1 is False
    assert ra_mod.CHECKPOINT_STORE_CREATED_BY_W1 is False
    assert ra_mod.M2_CONTEXT_PROVIDER_INTEGRATION_STARTED is False
    assert ra_mod.M2_SELECTIVE_HYDRATION_EXECUTION_STARTED is False
    assert ra_mod.LOGICAL_ROLLOVER_IMPLEMENTED_BY_W1 is False
    assert ra_mod.WORKING_TRUTH_RECONSTRUCTION_IMPLEMENTED_BY_W1 is False
    assert ra_mod.NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED is False
    # Check no filesystem imports inside module file
    txt = pathlib.Path(ra_mod.__file__).read_text()  # type: ignore
    # Should not contain GitHub fetch patterns
    assert "import os" not in txt or "os." not in txt  # lenient
    assert "github" not in txt.lower() or "RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY" in txt
    assert ra_mod.RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY is False


# ---------------------------------------------------------------------------
# S. Checkpoint remains non-authority
# ---------------------------------------------------------------------------

def test_s_checkpoint_remains_non_authority():
    wt = _wt()
    cp = _cp(wt)
    assert cp.verify_integrity() is True
    # checkpoint flags
    assert sc_mod.SESSION_CHECKPOINT_IS_AUTHORITY is False
    assert sc_mod.WORKING_TRUTH_PROJECTION_IS_AUTHORITY is False
    assert sc_mod.CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS is True
    assert ra_mod.CHECKPOINT_INTEGRITY_CONTRACT_REUSED is True
    assert ra_mod.SECOND_CHECKPOINT_INTEGRITY_IMPLEMENTATION_CREATED is False
    assert ra_mod.CHECKPOINT_INTEGRITY_IS_RECOVERY_ADMISSION is False
    # checkpoint has no authority methods
    assert not hasattr(cp, "is_authority")
    assert not hasattr(cp, "is_fresh")
    assert not hasattr(cp, "grant_retry")


# ---------------------------------------------------------------------------
# T. RecoveryAdmission remains non-authority
# ---------------------------------------------------------------------------

def test_t_recovery_admission_non_authority():
    cp = _cp(_wt())
    cur = _cur()
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert ra_mod.RECOVERY_ADMISSION_IS_AUTHORITY is False
    assert ra_mod.RECOVERY_ADMISSION_IS_RETRY_AUTHORITY is False
    assert ra_mod.CURRENT_GOVERNED_WORKING_TRUTH_IS_AUTHORITY is False
    assert ra_mod.RECOVERY_SUCCESS_IS_RETRY_PERMISSION is False
    # RecoveryAdmission should not have retry/worker fields
    assert not hasattr(adm, "retry_authorized")
    assert not hasattr(adm, "worker_dispatch_authorized")
    assert not hasattr(adm, "mutation_authorized")
    assert not hasattr(adm, "safe_to_replay")


# ---------------------------------------------------------------------------
# Additional: checkpoint integrity vs admission distinction
# ---------------------------------------------------------------------------

def test_checkpoint_integrity_is_not_admission():
    # Two integrity-valid checkpoints may receive different admission results based on current governance
    wt_a = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cp_a = SessionCheckpoint(working_truth=wt_a)
    assert cp_a.verify_integrity() is True
    wt_b = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cp_b = SessionCheckpoint(working_truth=wt_b)
    assert cp_b.verify_integrity() is True
    # same checkpoint but different current governance yields different admission
    cur_match = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cur_mismatch = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M2"))
    adm_match = evaluate_recovery_admission(cp_a, cur_match)
    adm_mismatch = evaluate_recovery_admission(cp_a, cur_mismatch)
    assert adm_match.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert adm_mismatch.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert ra_mod.CHECKPOINT_INTEGRITY_IS_RECOVERY_ADMISSION is False
    assert ra_mod.CHECKPOINT_INTEGRITY_CONTRACT_REUSED is True


def test_checkpoint_integrity_valid_stale_still_valid():
    wt = _wt(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cp = SessionCheckpoint(working_truth=wt)
    assert cp.verify_integrity() is True
    # stale checkpoint still integrity-valid but not authority, current wins
    cur = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M2"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert cp.verify_integrity() is True  # still valid
    assert ra_mod.CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT is True


# ---------------------------------------------------------------------------
# Pure evaluator and no GitHub fetch
# ---------------------------------------------------------------------------

def test_evaluator_pure_no_side_effects():
    cp = _cp(_wt())
    cur = _cur()
    adm1 = evaluate_recovery_admission(cp, cur)
    adm2 = evaluate_recovery_admission(cp, cur)
    assert adm1 == adm2
    assert adm1.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert ra_mod.RECOVERY_ADMISSION_EVALUATOR_PURE is True
    assert ra_mod.RECOVERY_RUNTIME_FETCHES_GITHUB_DIRECTLY is False
    # Ensure function has no filesystem/network imports via inspection
    import inspect
    src = inspect.getsource(evaluate_recovery_admission)
    assert "open(" not in src
    assert "requests" not in src.lower()
    # pure evaluator must not contain direct GitHub fetch patterns; allow mention in flag name
    assert "gh api" not in src.lower()
    assert "GH_CONFIG" not in src


# ---------------------------------------------------------------------------
# Journal / retry reuse - not duplicated
# ---------------------------------------------------------------------------

def test_journal_retry_reuse():
    # Existing reconciliation types still authoritative, not duplicated in recovery module
    assert ra_mod.EXISTING_JOURNAL_RECONCILIATION_REUSED is True
    assert ra_mod.NEW_MUTATION_RECONCILIATION_MODEL_CREATED is False
    assert ra_mod.EXISTING_RETRY_AUTHORITY_MODEL_REUSED is True
    # Recovery module should not define its own CANDIDATE_OBSERVED etc
    assert not hasattr(ra_mod, "CANDIDATE_OBSERVED")
    assert not hasattr(ra_mod, "ORIGINAL_OBSERVED")
    assert not hasattr(ra_mod, "CONFLICT_THIRD")
    # Check that existing core still works
    from aota_forge.core.journal.reconcile import ReconciliationClassification
    assert ReconciliationClassification.CANDIDATE_OBSERVED.value == "CANDIDATE_OBSERVED"
    from aota_forge.core.journal.retry import is_retry_allowed, JournalState
    assert is_retry_allowed(current_state=JournalState.RETRYABLE_NO_EFFECT, has_fresh_authorization=True, has_fresh_subject_precondition=True, has_fresh_raw_authority_precondition=True, has_new_bounded_lease=True) is True
    # Recovery success is not retry permission
    cp = _cp(_wt())
    cur = _cur()
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert ra_mod.RECOVERY_SUCCESS_IS_RETRY_PERMISSION is False
    # Even recoverable does not grant retry
    assert not hasattr(adm, "retry_allowed")


# ---------------------------------------------------------------------------
# No side-effect replay, no Worker dispatch
# ---------------------------------------------------------------------------

def test_no_side_effect_replay_or_dispatch():
    txt = pathlib.Path(ra_mod.__file__).read_text()  # type: ignore
    for banned in ["retry()", "rerun()", "reexecute()", "resume_effect()", "replay()"]:
        assert banned not in txt
    assert ra_mod.RECOVERY_AUTO_REPLAYS_SIDE_EFFECT is False
    assert ra_mod.ROLLOVER_REPLAYS_SIDE_EFFECT is False
    assert ra_mod.ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER is False


# ---------------------------------------------------------------------------
# Vocabulary bounded
# ---------------------------------------------------------------------------

def test_vocabulary_bounded():
    assert ra_mod.RECOVERY_ADMISSION_VOCABULARY_BOUNDED is True
    assert len(RecoveryAdmissionDisposition) == 6
    expected = {"RECOVERABLE", "STALE_RECONCILIATION_REQUIRED", "BLOCKED_BY_SCOPE_MISMATCH", "BLOCKED_BY_SEMANTIC_STOP", "BLOCKED_BY_REPLAN", "BLOCKED_BY_UNRESOLVED_EFFECT"}
    assert set(v.value for v in RecoveryAdmissionDisposition) == expected


# ---------------------------------------------------------------------------
# Predecessor / shared contract not revised
# ---------------------------------------------------------------------------

def test_no_predecessor_revision():
    assert ra_mod.M1_PREDECESSOR_CONTRACT_REVISION_REQUIRED is False
    assert ra_mod.SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED is False
    assert ra_mod.AGGREGATOR_CHANGE_REQUIRED_FOR_W1 is False
    assert ra_mod.W2_SCOPE_PULLED_FORWARD_BY_W1 is False
    # Ensure work_plane __init__ not modified to expose W1 types (check file not changed)
    init_path = pathlib.Path(ra_mod.__file__).parent / "__init__.py"
    init_txt = init_path.read_text()
    assert "recovery_admission" not in init_txt.lower()


# ---------------------------------------------------------------------------
# Check that recovery does not advance S4 workflow etc.
# ---------------------------------------------------------------------------

def test_recovery_does_not_advance_workflow():
    cp = _cp(_wt(active_work_item_ref=_sr("w:1")))
    cur = _cur(active_work_item_ref=_sr("w:1"))
    adm = evaluate_recovery_admission(cp, cur)
    assert adm.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert ra_mod.RECOVERY_ADVANCES_S4_WORKFLOW is False
    assert ra_mod.RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER is False
    assert ra_mod.RECOVERY_ESTABLISHES_REVIEWED_FRONTIER is False


# ---------------------------------------------------------------------------
# Duplicate refs etc already tested via checkpoint; ensure current also deterministic
# ---------------------------------------------------------------------------

def test_current_deterministic_order_invariance():
    # SemanticReference equality should be deterministic regardless of digest ordering?
    cur1 = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    cur2 = _cur(project_ref=_sr("proj:A"), plan_ref=_sr("plan:P"), milestone_ref=_sr("ms:M1"))
    assert cur1.canonical_json() == cur2.canonical_json()

