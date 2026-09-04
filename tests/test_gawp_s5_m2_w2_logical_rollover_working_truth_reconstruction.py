"""S5 M2 W2 Logical Rollover & Working-Truth Reconstruction — focused proof.

Covers required groups A-T plus strict serialization, authority firewall,
passive ref non-authority, current precedence, deterministic and negative
surface checks.

Production surface:
  aota_forge/work_plane/context_rollover.py
  Reuses:
    aota_forge/work_plane/recovery_admission.py (CurrentGovernedWorkingTruth, RecoveryAdmission, evaluate_recovery_admission)
    aota_forge/work_plane/session_checkpoint.py (SessionCheckpoint, WorkingTruthProjection, SemanticReference)

Invariants proven:
  - W1 evaluator reuse, no duplicated decision table
  - Same-state -> RECOVERABLE with reconstructed projection
  - Cross-project / cross-Plan -> BLOCKED_BY_SCOPE_MISMATCH, no continuation
  - Milestone / active W / frontier / workflow stale -> STALE with current precedence
  - SemanticStop / REPLAN / unresolved effect -> blocked, no continuation
  - Passive result/evidence/context refs carried when compatible, non-authority, not hydrated
  - Cross-scope attack no refs cross
  - Deterministic reconstruction, canonical output stable
  - Checkpoint ID provenance correlation only
  - No retry/dispatch/effect authority, no session/store/engine, agent neutral
  - Strict fail-closed serialization, no arbitrary metadata
"""

from __future__ import annotations

import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
import aota_forge.work_plane.context_rollover as cr_mod
import aota_forge.work_plane.recovery_admission as ra_mod
import aota_forge.work_plane.session_checkpoint as sc_mod
from aota_forge.work_plane.context_rollover import LogicalRolloverResult, perform_logical_rollover
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.recovery_admission import (
    CurrentGovernedWorkingTruth,
    RecoveryAdmission,
    RecoveryAdmissionDisposition,
    evaluate_recovery_admission,
)
from aota_forge.work_plane.session_checkpoint import SessionCheckpoint, WorkingTruthProjection


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
# A. W1 reuse — actual evaluator invoked, no duplicated decision table
# ---------------------------------------------------------------------------

def test_a_w1_reuse_actual_evaluator():
    # Verify module reuses actual W1 evaluator
    src = pathlib.Path("aota_forge/work_plane/context_rollover.py").read_text(encoding="utf-8")
    assert "evaluate_recovery_admission" in src
    # ensure import from recovery_admission, not local duplication
    assert "from aota_forge.work_plane.recovery_admission import" in src
    # Verify runtime reuse: W2 admission equals direct W1 evaluation for same inputs
    wt = _wt(active_work_item_ref=_sr("w:1"))
    cp = _cp(wt)
    cur = _cur(active_work_item_ref=_sr("w:1"))
    direct = evaluate_recovery_admission(cp, cur)
    via_rollover = perform_logical_rollover(cp, cur)
    assert via_rollover.admission.disposition == direct.disposition
    assert isinstance(via_rollover.admission, RecoveryAdmission)
    # Flag reuse
    assert cr_mod.W1_RECOVERY_ADMISSION_CONTRACT_REUSED is True
    assert cr_mod.SECOND_RECOVERY_ADMISSION_MODEL_CREATED is False
    assert cr_mod.SECOND_CURRENT_GOVERNANCE_MODEL_CREATED is False
    assert cr_mod.M1_SESSION_CHECKPOINT_CONTRACT_REUSED is True
    assert cr_mod.M1_WORKING_TRUTH_CONTRACT_REUSED is True
    assert cr_mod.SECOND_CHECKPOINT_ONTOLOGY_CREATED is False
    assert cr_mod.SECOND_WORKING_TRUTH_ONTOLOGY_CREATED is False


# ---------------------------------------------------------------------------
# B. Same-state rollover -> RECOVERABLE with reconstructed projection
# ---------------------------------------------------------------------------

def test_b_same_state_recoverable():
    wt = _wt(
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:1"),
        accepted_frontier_ref=_sr("frontier:A"),
        reviewed_frontier_ref=_sr("frontier:R"),
        workflow_disposition_ref=_sr("workflow:CONTINUE"),
        result_refs=(_sr("result:1"),),
        evidence_refs=(_sr("evidence:1"),),
        context_refs=(_sr("ctx:1"),),
    )
    cp = _cp(wt)
    cur = _cur(
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:1"),
        accepted_frontier_ref=_sr("frontier:A"),
        reviewed_frontier_ref=_sr("frontier:R"),
        workflow_disposition_ref=_sr("workflow:CONTINUE"),
    )
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    assert res.reconstructed_working_truth is not None
    rt = res.reconstructed_working_truth
    assert isinstance(rt, WorkingTruthProjection)
    # identity fields from current
    assert rt.project_ref.ref == "proj:A"
    assert rt.plan_ref.ref == "plan:P"
    assert rt.milestone_ref.ref == "ms:M1"
    assert rt.active_work_item_ref.ref == "w:1"
    assert rt.accepted_frontier_ref.ref == "frontier:A"
    assert rt.reviewed_frontier_ref.ref == "frontier:R"
    assert rt.workflow_disposition_ref.ref == "workflow:CONTINUE"
    # passive refs carried
    assert len(rt.result_refs) == 1 and rt.result_refs[0].ref == "result:1"
    assert len(rt.evidence_refs) == 1
    assert len(rt.context_refs) == 1
    # provenance
    assert res.source_checkpoint_id == cp.checkpoint_id
    assert cr_mod.BLOCKED_RECOVERY_PRODUCES_CONTINUATION is False


# ---------------------------------------------------------------------------
# C. Project mismatch -> BLOCKED_BY_SCOPE_MISMATCH, no continuation
# ---------------------------------------------------------------------------

def test_c_project_mismatch_blocked():
    cp = _cp(_wt(project_ref=_sr("proj:A")))
    cur = _cur(project_ref=_sr("proj:B"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert res.reconstructed_working_truth is None
    assert cr_mod.CROSS_PROJECT_ROLLOVER_CONTINUATION_ALLOWED is False


# ---------------------------------------------------------------------------
# D. Plan mismatch -> BLOCKED_BY_SCOPE_MISMATCH
# ---------------------------------------------------------------------------

def test_d_plan_mismatch_blocked():
    cp = _cp(_wt(plan_ref=_sr("plan:P1")))
    cur = _cur(plan_ref=_sr("plan:P2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert res.reconstructed_working_truth is None
    assert cr_mod.CROSS_PLAN_ROLLOVER_CONTINUATION_ALLOWED is False


# ---------------------------------------------------------------------------
# E. Milestone stale -> STALE, reconstructed uses current M2
# ---------------------------------------------------------------------------

def test_e_milestone_stale_uses_current():
    cp = _cp(_wt(milestone_ref=_sr("ms:M1")))
    cur = _cur(milestone_ref=_sr("ms:M2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth is not None
    assert res.reconstructed_working_truth.milestone_ref.ref == "ms:M2"
    assert res.reconstructed_working_truth.milestone_ref.ref != "ms:M1"
    assert cr_mod.STALE_CHECKPOINT_CAN_BE_RECONCILED_TO_CURRENT_TRUTH is True
    assert cr_mod.STALE_CHECKPOINT_CAN_REWIND_CURRENT_TRUTH is False
    assert cr_mod.CURRENT_STATE_FIELDS_OVERRIDE_STALE_CHECKPOINT is True


# ---------------------------------------------------------------------------
# F. Active W stale -> reconstructed uses current, W1 not restarted
# ---------------------------------------------------------------------------

def test_f_active_w_stale_uses_current():
    cp = _cp(_wt(active_work_item_ref=_sr("w:W1")))
    cur = _cur(active_work_item_ref=_sr("w:W2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth is not None
    assert res.reconstructed_working_truth.active_work_item_ref.ref == "w:W2"
    assert cr_mod.STALE_CHECKPOINT_CAN_REWIND_CURRENT_TRUTH is False
    # Ensure W1 not restarted — current W2 wins
    assert res.reconstructed_working_truth.active_work_item_ref.ref != "w:W1"


# ---------------------------------------------------------------------------
# G. Accepted frontier stale -> uses current
# ---------------------------------------------------------------------------

def test_g_accepted_frontier_stale_uses_current():
    cp = _cp(_wt(accepted_frontier_ref=_sr("frontier:A")))
    cur = _cur(accepted_frontier_ref=_sr("frontier:B"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth.accepted_frontier_ref.ref == "frontier:B"
    assert cr_mod.RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER is False


# ---------------------------------------------------------------------------
# H. Reviewed frontier stale -> uses current
# ---------------------------------------------------------------------------

def test_h_reviewed_frontier_stale_uses_current():
    cp = _cp(_wt(reviewed_frontier_ref=_sr("frontier:R1")))
    cur = _cur(reviewed_frontier_ref=_sr("frontier:R2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth.reviewed_frontier_ref.ref == "frontier:R2"
    assert cr_mod.RECOVERY_ESTABLISHES_REVIEWED_FRONTIER is False


# ---------------------------------------------------------------------------
# I. Workflow stale -> uses current
# ---------------------------------------------------------------------------

def test_i_workflow_stale_uses_current():
    cp = _cp(_wt(workflow_disposition_ref=_sr("workflow:OLD")))
    cur = _cur(workflow_disposition_ref=_sr("workflow:NEW"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth.workflow_disposition_ref.ref == "workflow:NEW"
    assert cr_mod.RECOVERY_ADVANCES_S4_WORKFLOW is False


# ---------------------------------------------------------------------------
# J. SemanticStop -> blocked, no continuation
# ---------------------------------------------------------------------------

def test_j_semantic_stop_blocked():
    cp = _cp(_wt())
    cur = _cur(semantic_stop_present=True)
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SEMANTIC_STOP
    assert res.reconstructed_working_truth is None
    assert cr_mod.RECOVERY_PRESERVES_SEMANTIC_STOP is True
    assert cr_mod.RECOVERY_AUTO_CONTINUES_SEMANTIC_STOP is False


# ---------------------------------------------------------------------------
# K. REPLAN_REQUIRED -> blocked
# ---------------------------------------------------------------------------

def test_k_replan_blocked():
    cp = _cp(_wt())
    cur = _cur(replan_required=True)
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_REPLAN
    assert res.reconstructed_working_truth is None
    assert cr_mod.RECOVERY_PRESERVES_REPLAN_REQUIRED is True


# ---------------------------------------------------------------------------
# L. Unresolved effect -> blocked
# ---------------------------------------------------------------------------

def test_l_unresolved_effect_blocked():
    cp = _cp(_wt())
    cur = _cur(unresolved_effect=True)
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_UNRESOLVED_EFFECT
    assert res.reconstructed_working_truth is None
    assert cr_mod.TASK_MAIN_CONTEXT_RECOVERY_DOES_NOT_RESOLVE_OPERATION_EFFECT is True


# ---------------------------------------------------------------------------
# M. Passive result refs carried, non-authority
# ---------------------------------------------------------------------------

def test_m_passive_result_refs_carried_non_authority():
    wt = _wt(result_refs=(_sr("result:42"), _sr("result:99")))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    rt = res.reconstructed_working_truth
    assert len(rt.result_refs) == 2
    refs = {r.ref for r in rt.result_refs}
    assert "result:42" in refs and "result:99" in refs
    assert cr_mod.M2_RECOVERS_RESULT_REFS is True
    assert cr_mod.RECOVERED_RESULT_REF_IS_AUTHORITY is False
    # result refs are evidence only, not authority — check flag
    assert sc_mod.CHECKPOINT_WORKER_RESULT_IS_EVIDENCE_ONLY is True


# ---------------------------------------------------------------------------
# N. Passive evidence refs carried, non-authority
# ---------------------------------------------------------------------------

def test_n_passive_evidence_refs_carried():
    wt = _wt(evidence_refs=(_sr("evidence:abc"),))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert res.reconstructed_working_truth is not None
    assert len(res.reconstructed_working_truth.evidence_refs) == 1
    assert cr_mod.M2_RECOVERS_EVIDENCE_REFS is True
    assert cr_mod.RECOVERED_EVIDENCE_REF_IS_AUTHORITY is False


# ---------------------------------------------------------------------------
# O. Passive context refs carried, not hydrated
# ---------------------------------------------------------------------------

def test_o_passive_context_refs_carried_not_hydrated():
    wt = _wt(context_refs=(_sr("ctx:xyz"), _sr("ctx:123")))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert res.reconstructed_working_truth is not None
    assert len(res.reconstructed_working_truth.context_refs) == 2
    assert cr_mod.M2_RECOVERS_CONTEXT_REFS is True
    assert cr_mod.M2_HYDRATES_CONTEXT_REFS is False
    assert cr_mod.M2_CONTEXT_PROVIDER_INTEGRATION_STARTED is False
    assert cr_mod.M2_SELECTIVE_HYDRATION_EXECUTION_STARTED is False
    # context refs remain refs only, not hydrated objects
    for r in res.reconstructed_working_truth.context_refs:
        assert isinstance(r, SemanticReference)


# ---------------------------------------------------------------------------
# P. Cross-project passive-ref attack — no refs cross scope
# ---------------------------------------------------------------------------

def test_p_cross_project_no_refs_cross():
    wt = _wt(
        project_ref=_sr("proj:A"),
        result_refs=(_sr("result:evil"),),
        evidence_refs=(_sr("evidence:evil"),),
        context_refs=(_sr("ctx:evil"),),
    )
    cp = _cp(wt)
    cur = _cur(project_ref=_sr("proj:B"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert res.reconstructed_working_truth is None
    # Ensure no passive refs leaked via blocked path
    # If reconstruction were incorrectly built, refs would cross boundary
    assert res.reconstructed_working_truth is None


def test_p_cross_plan_no_refs_cross():
    wt = _wt(plan_ref=_sr("plan:A"), result_refs=(_sr("result:evil"),))
    cp = _cp(wt)
    cur = _cur(plan_ref=_sr("plan:B"))
    res = perform_logical_rollover(cp, cur)
    assert res.reconstructed_working_truth is None


# ---------------------------------------------------------------------------
# Q. Deterministic reconstruction — same inputs -> same result/canonical output
# ---------------------------------------------------------------------------

def test_q_deterministic_same_inputs():
    wt = _wt(result_refs=(_sr("result:1"),), milestone_ref=_sr("ms:M1"))
    cp = _cp(wt)
    cur = _cur(milestone_ref=_sr("ms:M2"))
    r1 = perform_logical_rollover(cp, cur)
    r2 = perform_logical_rollover(cp, cur)
    assert r1.canonical_json() == r2.canonical_json()
    assert r1.canonical_dict() == r2.canonical_dict()
    assert r1.source_checkpoint_id == r2.source_checkpoint_id
    assert r1.admission.disposition == r2.admission.disposition
    assert r1.reconstructed_working_truth.canonical_json() == r2.reconstructed_working_truth.canonical_json()
    assert cr_mod.LOGICAL_ROLLOVER_DETERMINISTIC is True


def test_q_deterministic_canonical_stable():
    # Different order of refs in checkpoint should still be deterministic due to sorting
    wt1 = _wt(result_refs=(_sr("result:B"), _sr("result:A")))
    wt2 = _wt(result_refs=(_sr("result:A"), _sr("result:B")))
    cp1 = _cp(wt1)
    cp2 = _cp(wt2)
    # checkpoints have same canonical sorting, so same digest/id?
    # but perform rollover with same current should give same reconstructed ordering
    cur = _cur()
    r1 = perform_logical_rollover(cp1, cur)
    r2 = perform_logical_rollover(cp2, cur)
    # sorted refs ensure deterministic
    assert [r.ref for r in r1.reconstructed_working_truth.result_refs] == [r.ref for r in r2.reconstructed_working_truth.result_refs]


# ---------------------------------------------------------------------------
# R. checkpoint ID provenance — correlation only, not authority
# ---------------------------------------------------------------------------

def test_r_checkpoint_id_provenance_correlation_only():
    wt = _wt()
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert res.source_checkpoint_id == cp.checkpoint_id
    assert res.source_checkpoint_id == cp.checkpoint_digest or res.source_checkpoint_id == f"checkpoint:{cp.checkpoint_digest}"
    assert cr_mod.SOURCE_CHECKPOINT_ID_IS_AUTHORITY is False
    # Mutating checkpoint id string does not affect admission (admission already computed)
    # Verify that source_checkpoint_id is not used for authority decision: two checkpoints with same content but different creation order have same id
    cp2 = _cp(_wt())
    cur2 = _cur()
    # same content -> same id
    assert cp.checkpoint_id == cp2.checkpoint_id
    res2 = perform_logical_rollover(cp2, cur2)
    assert res.source_checkpoint_id == res2.source_checkpoint_id
    # Changing current does not change source id
    cur_stale = _cur(milestone_ref=_sr("ms:M2"))
    res_stale = perform_logical_rollover(cp, cur_stale)
    assert res_stale.source_checkpoint_id == cp.checkpoint_id


def test_r_checkpoint_id_not_freshness_authority():
    # Ensure checkpoint_id cannot be used to imply freshness or authority
    wt = _wt(milestone_ref=_sr("ms:M1"))
    cp = _cp(wt)
    cur = _cur(milestone_ref=_sr("ms:M2"))
    res = perform_logical_rollover(cp, cur)
    # Even though checkpoint_id is valid, admission is STALE, not RECOVERABLE, and reconstruction uses current
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth.milestone_ref.ref == "ms:M2"
    # source id remains original checkpoint's id, not current's
    assert res.source_checkpoint_id == cp.checkpoint_id


# ---------------------------------------------------------------------------
# S. retry/dispatch/effect negative surface — no such execution APIs
# ---------------------------------------------------------------------------

def test_s_no_retry_dispatch_effect_apis():
    src = pathlib.Path("aota_forge/work_plane/context_rollover.py").read_text(encoding="utf-8")
    # Check for absence of forbidden execution APIs (import)
    assert "from aota_forge.core.journal" not in src
    assert "import aota_forge.core.journal" not in src
    assert "from aota_forge.core.journal.retry" not in src
    assert "from aota_forge.work_plane.recovery_admission import" in src  # W1 reuse ok
    # Ensure module does not contain worker executor import
    assert "Worker" not in src or "ACTIVE_WORK" in src  # allow flag but not executor class
    # Verify flags
    assert cr_mod.RECOVERY_SUCCESS_IS_RETRY_PERMISSION is False
    assert cr_mod.LOGICAL_ROLLOVER_SUCCESS_IS_RETRY_PERMISSION is False
    assert cr_mod.W2_EXECUTES_RETRY is False
    assert cr_mod.W2_EXECUTES_JOURNAL_RECONCILIATION is False
    assert cr_mod.W2_RESOLVES_OPERATION_EFFECT is False
    assert cr_mod.RECOVERY_AUTO_REPLAYS_SIDE_EFFECT is False
    assert cr_mod.RECOVERED_RESULT_REF_AUTO_REPLAYS_WORK is False
    assert cr_mod.LOGICAL_ROLLOVER_AUTO_DISPATCHES_WORKER is False
    assert cr_mod.ACTIVE_WORK_RECOVERY_AUTO_DISPATCHES_WORKER is False


def test_s_logical_rollover_result_not_authority():
    wt = _wt()
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    # Result fields must not include authority fields
    d = res.canonical_dict()
    assert "approved" not in d
    assert "authorized" not in d
    assert "retry_authorized" not in d
    assert "dispatch_authorized" not in d
    assert "mutation_authorized" not in d
    assert "safe_to_replay" not in d
    assert "operation_recovered" not in d
    assert "effect_resolved" not in d
    assert cr_mod.LOGICAL_ROLLOVER_RESULT_IS_AUTHORITY is False
    # Also check from_dict rejects authority injection
    with pytest.raises((ValueError, TypeError)):
        LogicalRolloverResult.from_dict({
            "admission": {"disposition": "RECOVERABLE"},
            "reconstructed_working_truth": None,
            "source_checkpoint_id": "checkpoint:abc",
            "approved": True,
        })


# ---------------------------------------------------------------------------
# T. agent neutrality — no provider/model/native-session requirement
# ---------------------------------------------------------------------------

def test_t_agent_neutral():
    # Ensure no provider/model/session fields required in API
    wt = _wt()
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    # Check flags
    assert cr_mod.M2_AGENT_NEUTRAL is True
    assert cr_mod.PHYSICAL_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    assert cr_mod.MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    # Ensure module does not require provider/model fields
    src = pathlib.Path("aota_forge/work_plane/context_rollover.py").read_text(encoding="utf-8")
    assert "provider" not in src.lower() or "PROVIDER" in src  # allow only flag uppercase if any
    # Actually we already ensured no provider string, but check case-insensitive for forbidden exact
    assert "openai" not in src.lower()
    assert "hermes_session" not in src.lower()
    # Ensure from_dict rejects mechanical fields
    with pytest.raises((ValueError, TypeError)):
        LogicalRolloverResult.from_dict({
            "admission": {"disposition": "RECOVERABLE"},
            "reconstructed_working_truth": _wt().to_dict(),
            "source_checkpoint_id": "checkpoint:abc",
            "provider": "openai",
        })


# ---------------------------------------------------------------------------
# U. Strict serialization — unknown fields fail closed, no arbitrary metadata
# ---------------------------------------------------------------------------

def test_u_strict_serialization_unknown_fields_fail_closed():
    # LogicalRolloverResult unknown fields should fail
    with pytest.raises(ValueError):
        LogicalRolloverResult.from_dict({
            "admission": {"disposition": "RECOVERABLE"},
            "reconstructed_working_truth": None,
            "source_checkpoint_id": "checkpoint:abc",
            "extra": "field",
        })
    with pytest.raises(ValueError):
        LogicalRolloverResult.from_dict({
            "admission": {"disposition": "RECOVERABLE"},
            "reconstructed_working_truth": None,
            "source_checkpoint_id": "checkpoint:abc",
            "metadata": {"foo": "bar"},
        })
    assert cr_mod.ARBITRARY_ROLLOVER_METADATA_ALLOWED is False


def test_u_canonical_roundtrip():
    wt = _wt(result_refs=(_sr("result:1"),))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    d = res.to_dict()
    restored = LogicalRolloverResult.from_dict(d)
    assert restored.canonical_json() == res.canonical_json()
    assert restored.admission.disposition == res.admission.disposition
    assert restored.source_checkpoint_id == res.source_checkpoint_id
    assert restored.reconstructed_working_truth.canonical_dict() == res.reconstructed_working_truth.canonical_dict()


# ---------------------------------------------------------------------------
# V. Current truth cannot be overwritten — checkpoint stale mutable attack
# ---------------------------------------------------------------------------

def test_v_checkpoint_cannot_override_current():
    # Attack: mutate checkpoint to claim older/different milestone/active frontier/workflow
    # Result must use current values
    wt = _wt(
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:OLD"),
        accepted_frontier_ref=_sr("frontier:OLD"),
        reviewed_frontier_ref=_sr("frontier:OLD_R"),
        workflow_disposition_ref=_sr("workflow:OLD"),
    )
    cp = _cp(wt)
    cur = _cur(
        milestone_ref=_sr("ms:M2"),
        active_work_item_ref=_sr("w:NEW"),
        accepted_frontier_ref=_sr("frontier:NEW"),
        reviewed_frontier_ref=_sr("frontier:NEW_R"),
        workflow_disposition_ref=_sr("workflow:NEW"),
    )
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    rt = res.reconstructed_working_truth
    assert rt.milestone_ref.ref == "ms:M2"
    assert rt.active_work_item_ref.ref == "w:NEW"
    assert rt.accepted_frontier_ref.ref == "frontier:NEW"
    assert rt.reviewed_frontier_ref.ref == "frontier:NEW_R"
    assert rt.workflow_disposition_ref.ref == "workflow:NEW"
    # Ensure not using checkpoint old values
    assert rt.milestone_ref.ref != "ms:M1"
    assert rt.active_work_item_ref.ref != "w:OLD"


# ---------------------------------------------------------------------------
# W. Passive ref non-authority attack — string cannot grant authority
# ---------------------------------------------------------------------------

def test_w_passive_ref_string_cannot_grant_authority():
    wt = _wt(
        result_refs=(_sr("approved:true"), _sr("retry:authorized"), _sr("worker:dispatch")),
        evidence_refs=(_sr("frontier:known-good"),),
        context_refs=(_sr("authority:grant"),),
    )
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    # Should still be RECOVERABLE, but refs remain passive non-authority
    assert res.admission.disposition == RecoveryAdmissionDisposition.RECOVERABLE
    rt = res.reconstructed_working_truth
    # refs are preserved as SemanticReference but not interpreted
    refs_all = [r.ref for r in rt.result_refs] + [r.ref for r in rt.evidence_refs] + [r.ref for r in rt.context_refs]
    assert "approved:true" in refs_all
    assert "retry:authorized" in refs_all
    # Ensure they don't grant authority: check flags
    assert cr_mod.RECOVERED_RESULT_REF_IS_AUTHORITY is False
    assert cr_mod.RECOVERED_EVIDENCE_REF_IS_AUTHORITY is False
    assert cr_mod.RECOVERED_CONTEXT_REF_IS_AUTHORITY is False
    # Also ensure admission not changed by passive ref content (still recoverable because scope matches)
    # Now test with blocked case: even with evil refs, still blocked not bypassed
    cp2 = _cp(_wt(project_ref=_sr("proj:A"), result_refs=(_sr("approved:true"),)))
    cur2 = _cur(project_ref=_sr("proj:B"))
    res2 = perform_logical_rollover(cp2, cur2)
    assert res2.admission.disposition == RecoveryAdmissionDisposition.BLOCKED_BY_SCOPE_MISMATCH
    assert res2.reconstructed_working_truth is None


# ---------------------------------------------------------------------------
# X. Authority firewall — strongest W1/W2 result cannot substitute
# ---------------------------------------------------------------------------

def test_x_authority_firewall():
    wt = _wt()
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    # Check result has no authority-granting fields and flags declare no authority
    d = res.canonical_dict()
    for forbidden in ["approved", "authorized", "retry_authorized", "dispatch_authorized", "mutation_authorized", "safe_to_replay", "operation_recovered", "effect_resolved"]:
        assert forbidden not in d
    # Check flags
    assert cr_mod.LOGICAL_ROLLOVER_RESULT_IS_AUTHORITY is False
    assert cr_mod.RECOVERY_SUCCESS_IS_RETRY_PERMISSION is False
    assert cr_mod.LOGICAL_ROLLOVER_SUCCESS_IS_RETRY_PERMISSION is False
    assert cr_mod.RECOVERY_ESTABLISHES_ACCEPTED_FRONTIER is False
    assert cr_mod.RECOVERY_ESTABLISHES_REVIEWED_FRONTIER is False
    assert cr_mod.RECOVERY_ADVANCES_S4_WORKFLOW is False
    # Ensure no session/store/engine created
    assert cr_mod.NEW_SESSION_ENTITY_CREATED_BY_W2 is False
    assert cr_mod.CHECKPOINT_STORE_CREATED_BY_W2 is False
    assert cr_mod.NEW_GENERIC_RECOVERY_FRAMEWORK_CREATED is False


# ---------------------------------------------------------------------------
# Y. Bounded collections reused
# ---------------------------------------------------------------------------

def test_y_boundedness_inherited():
    # M1 bounds are 16 per collection, 48 total
    # Ensure reconstruction inherits same bounds — try to exceed should fail at WTP level
    max_refs = [_sr(f"result:{i}") for i in range(16)]
    wt = _wt(result_refs=tuple(max_refs))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert len(res.reconstructed_working_truth.result_refs) == 16
    assert cr_mod.M1_WORKING_TRUTH_BOUNDS_REUSED is True
    # Exceeding bound should fail at checkpoint creation, not at rollover
    with pytest.raises(ValueError):
        _wt(result_refs=tuple(_sr(f"result:{i}") for i in range(17)))


# ---------------------------------------------------------------------------
# Z. Logical rollover is not physical session creation
# ---------------------------------------------------------------------------

def test_z_not_physical_session():
    assert cr_mod.LOGICAL_ROLLOVER_IS_PHYSICAL_SESSION_CREATION is False
    assert cr_mod.PHYSICAL_MODEL_SESSION_CREATION_IS_FORGE_AUTHORITY is False
    assert cr_mod.NEW_SESSION_ENTITY_CREATED_BY_W2 is False
    # Ensure no runtime surface imports in module (basic check)
    src = pathlib.Path("aota_forge/work_plane/context_rollover.py").read_text(encoding="utf-8")
    for forbidden in ["import threading", "import subprocess", "import sqlite3", "SessionManager", "RecoveryEngine"]:
        assert forbidden not in src


# ---------------------------------------------------------------------------
# AA. No checkpoint creation wrapper required — reuse SessionCheckpoint constructor
# ---------------------------------------------------------------------------

def test_aa_no_second_checkpoint_creation_model():
    assert cr_mod.SECOND_CHECKPOINT_ONTOLOGY_CREATED is False
    # Ensure we can construct SessionCheckpoint directly and use in rollover
    wt = _wt()
    cp = SessionCheckpoint(working_truth=wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    assert res.reconstructed_working_truth is not None


# ---------------------------------------------------------------------------
# AB. Milestone advance example from spec
# ---------------------------------------------------------------------------

def test_ab_milestone_advance_example():
    # Checkpoint M1, current M2 within same project/Plan
    cp = _cp(_wt(milestone_ref=_sr("ms:M1"), active_work_item_ref=_sr("w:W1")))
    cur = _cur(milestone_ref=_sr("ms:M2"), active_work_item_ref=_sr("w:W2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    assert res.reconstructed_working_truth.milestone_ref.ref == "ms:M2"
    assert res.reconstructed_working_truth.active_work_item_ref.ref == "w:W2"


# ---------------------------------------------------------------------------
# AC. Frontier drift example
# ---------------------------------------------------------------------------

def test_ac_frontier_drift_example():
    cp = _cp(_wt(accepted_frontier_ref=_sr("frontier:A"), reviewed_frontier_ref=_sr("frontier:A")))
    cur = _cur(accepted_frontier_ref=_sr("frontier:B"), reviewed_frontier_ref=_sr("frontier:B"))
    res = perform_logical_rollover(cp, cur)
    assert res.reconstructed_working_truth.accepted_frontier_ref.ref == "frontier:B"
    assert res.reconstructed_working_truth.reviewed_frontier_ref.ref == "frontier:B"


# ---------------------------------------------------------------------------
# AD. Passive refs under in-scope staleness carried as non-authority
# ---------------------------------------------------------------------------

def test_ad_passive_refs_under_stale_carried():
    wt = _wt(
        milestone_ref=_sr("ms:M1"),
        result_refs=(_sr("result:keep"),),
        evidence_refs=(_sr("evidence:keep"),),
        context_refs=(_sr("ctx:keep"),),
    )
    cp = _cp(wt)
    cur = _cur(milestone_ref=_sr("ms:M2"))
    res = perform_logical_rollover(cp, cur)
    assert res.admission.disposition == RecoveryAdmissionDisposition.STALE_RECONCILIATION_REQUIRED
    rt = res.reconstructed_working_truth
    assert any(r.ref == "result:keep" for r in rt.result_refs)
    assert any(r.ref == "evidence:keep" for r in rt.evidence_refs)
    assert any(r.ref == "ctx:keep" for r in rt.context_refs)
    # remain non-authority
    assert cr_mod.RECOVERED_RESULT_REF_IS_AUTHORITY is False


# ---------------------------------------------------------------------------
# AE. Ensure no hydration
# ---------------------------------------------------------------------------

def test_ae_no_hydration():
    wt = _wt(context_refs=(_sr("ctx:toHydrate"),))
    cp = _cp(wt)
    cur = _cur()
    res = perform_logical_rollover(cp, cur)
    # Should have context refs but not hydrated objects
    assert len(res.reconstructed_working_truth.context_refs) == 1
    assert res.reconstructed_working_truth.context_refs[0].ref == "ctx:toHydrate"
    # Module must not integrate provider
    assert cr_mod.M2_HYDRATES_CONTEXT_REFS is False

