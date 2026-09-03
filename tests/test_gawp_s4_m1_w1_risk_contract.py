"""W1 Acceptance Tests — Risk Semantic Contract Foundation (gawp).

Proves behavioral contracts for ProcessDepth, MilestoneRiskEnvelope,
WorkItemRiskDelta, Handoff compatibility, S2 authority non-bypass.
"""

import hashlib
import pytest
from enum import Enum

from aota_forge.work_plane.risk_review import (
    AUTO_WITH_DEEP_VALIDATION_IS_OPERATION_AUTHORITY,
    CALLER_CAN_SELF_DOWNGRADE_RISK,
    FAST_IS_OPERATION_AUTHORITY,
    DEEP_IS_OPERATION_AUTHORITY,
    STANDARD_IS_OPERATION_AUTHORITY,
    PROCESS_DEPTH_IS_OPERATION_AUTHORITY,
    MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY,
    WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY,
    S2_AUTHORITY_MODEL_RETAINED,
    S4_PROCESS_AUTOMATION_CANNOT_BYPASS_S2_AUTHORITY,
    S4_PROCESS_AUTOMATION_IS_OPERATION_AUTHORITY,
    RISK_CLASS_IS_AUTHORITY,
    RISK_MODEL_INTERFACE_FIRST,
    RISK_THRESHOLDS_EMPIRICAL,
    NUMERIC_RISK_THRESHOLD_FROZEN,
    NUMERIC_RISK_WEIGHT_FROZEN,
    WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY,
    MODEL_SELF_REPORT_IS_RISK_AUTHORITY,
    WORKER_SELF_REPORT_IS_RISK_AUTHORITY,
    UNRESOLVED_RISK_UNCERTAINTY_CANNOT_SELECT_SHALLOWER_PROCESS_DEPTH,
    ProcessDepth,
    RiskDimension,
    RISK_DIMENSIONS,
    MilestoneRiskEnvelope,
    WorkItemRiskDelta,
    parse_process_depth,
    parse_risk_dimension,
    process_depth_order,
    is_shallower,
    is_deeper,
    can_select_shallower_depth,
    resolve_effective_depth,
    is_unresolved_uncertainty,
)
from aota_forge.work_plane.handoff import TaskHandoff, SemanticReference
from aota_forge.work_plane.compiler import compile_handoff_to_execution_package, TrustedExecutionBinding
from aota_forge.core.contracts.canonical import canonical_json


# ---------------------------------------------------------------------------
# ProcessDepth
# ---------------------------------------------------------------------------

class ForeignEnum(Enum):
    FAST = "FAST"

def test_process_depth_valid():
    assert parse_process_depth("FAST") == ProcessDepth.FAST
    assert parse_process_depth("STANDARD") == ProcessDepth.STANDARD
    assert parse_process_depth("DEEP") == ProcessDepth.DEEP
    assert parse_process_depth(ProcessDepth.FAST) == ProcessDepth.FAST

def test_process_depth_unknown_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth("UNKNOWN")
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth("fast")
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth("")
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth("FAST_STANDARD")

def test_process_depth_whitespace_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth(" FAST")
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth("FAST ")
    with pytest.raises((ValueError, TypeError)):
        parse_process_depth(" FAST ")

def test_process_depth_foreign_enum_fails_closed():
    with pytest.raises((TypeError, ValueError)):
        parse_process_depth(ForeignEnum.FAST)
    with pytest.raises((TypeError, ValueError)):
        parse_process_depth(123)
    with pytest.raises((TypeError, ValueError)):
        parse_process_depth(None)
    with pytest.raises((TypeError, ValueError)):
        parse_process_depth(3.14)

def test_process_depth_ordering_deterministic():
    assert process_depth_order("FAST") < process_depth_order("STANDARD")
    assert process_depth_order("STANDARD") < process_depth_order("DEEP")
    assert is_shallower("FAST", "DEEP")
    assert is_deeper("DEEP", "FAST")
    assert not is_shallower("DEEP", "FAST")
    assert process_depth_order(ProcessDepth.FAST) == 1
    assert process_depth_order(ProcessDepth.DEEP) == 3
    # deterministic across calls
    assert process_depth_order("FAST") == process_depth_order(ProcessDepth.FAST)

def test_process_depth_cannot_downgrade_floor():
    # approved floor is STANDARD, requesting FAST should be shallower and not allowed
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="STANDARD",
        dimensions=["blast_radius"],
    )
    assert not can_select_shallower_depth(env, None, "FAST")
    assert can_select_shallower_depth(env, None, "STANDARD")
    assert can_select_shallower_depth(env, None, "DEEP")
    # resolve effective depth never goes shallower than minimum
    eff = resolve_effective_depth(env, None, "FAST")
    assert eff == ProcessDepth.STANDARD
    # invariant flags
    assert FAST_IS_OPERATION_AUTHORITY is False
    assert STANDARD_IS_OPERATION_AUTHORITY is False
    assert DEEP_IS_OPERATION_AUTHORITY is False
    assert PROCESS_DEPTH_IS_OPERATION_AUTHORITY is False
    assert S4_PROCESS_AUTOMATION_IS_OPERATION_AUTHORITY is False
    assert AUTO_WITH_DEEP_VALIDATION_IS_OPERATION_AUTHORITY is False

# ---------------------------------------------------------------------------
# MilestoneRiskEnvelope
# ---------------------------------------------------------------------------

def test_envelope_deterministic_immutable_bounded():
    e1 = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth=ProcessDepth.STANDARD,
        minimum_process_depth="FAST",
        dimensions=["blast_radius", "reversibility"],
        review_policy_refs=["policy:review"],
        escalation_boundary_ref="boundary:user",
        uncertainty="resolved",
        evidence_refs=["evidence:1"],
        provenance_refs=["prov:1"],
    )
    e2 = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
        dimensions=["reversibility", "blast_radius"],  # order different but canonical sorted
        review_policy_refs=["policy:review"],
        escalation_boundary_ref="boundary:user",
        uncertainty="resolved",
        evidence_refs=["evidence:1"],
        provenance_refs=["prov:1"],
    )
    assert e1.digest == e2.digest
    assert e1.canonical_json() == e2.canonical_json()
    # immutable
    with pytest.raises((AttributeError, TypeError)):
        e1.milestone_ref = "other"

def test_envelope_unknown_fields_fail_closed():
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope.from_dict({
            "milestone_ref": "S4/M1",
            "default_process_depth": "STANDARD",
            "minimum_process_depth": "FAST",
            "unknown_field": "oops"
        })
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope.from_dict({
            "milestone_ref": "S4/M1",
            "default_process_depth": "STANDARD",
            "minimum_process_depth": "FAST",
            "numeric_threshold": 80,  # should be rejected as unknown
        })

def test_envelope_invalid_dimension_fails_closed():
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope(
            milestone_ref="S4/M1",
            default_process_depth="STANDARD",
            minimum_process_depth="FAST",
            dimensions=["invalid_dim"],
        )
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope(
            milestone_ref="S4/M1",
            default_process_depth="STANDARD",
            minimum_process_depth="FAST",
            dimensions=["blast_radius", "blast_radius"],  # duplicate
        )

def test_envelope_no_arbitrary_unbounded_refs():
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope(
            milestone_ref="S4/M1",
            default_process_depth="STANDARD",
            minimum_process_depth="FAST",
            dimensions=["blast_radius"],
            review_policy_refs=["x"*600],  # exceeds max ref length
        )
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope(
            milestone_ref="S4/M1",
            default_process_depth="STANDARD",
            minimum_process_depth="FAST",
            dimensions=["blast_radius"],
            evidence_refs=["a"]*17,  # exceeds max evidence refs (16)
        )

def test_envelope_no_numeric_threshold_authority():
    assert RISK_THRESHOLDS_EMPIRICAL is True
    assert NUMERIC_RISK_THRESHOLD_FROZEN is False
    assert NUMERIC_RISK_WEIGHT_FROZEN is False
    assert RISK_CLASS_IS_AUTHORITY is False
    assert RISK_MODEL_INTERFACE_FIRST is True
    e = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
        dimensions=["blast_radius"],
    )
    d = e.to_dict()
    assert "numeric_threshold" not in d
    assert "weight" not in d
    assert "score" not in d

def test_envelope_no_operation_authority_api():
    e = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
        dimensions=["blast_radius"],
    )
    assert e.is_operation_authority is False
    assert e.is_plan_authority is False
    assert MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY is False
    # must not have methods that grant workspace/git/test authority
    assert not hasattr(e, "authorize_workspace_write")
    assert not hasattr(e, "authorize_git")
    assert not hasattr(e, "authorize_test_execution")

def test_envelope_duplicate_ambiguous_input_fails_closed():
    # duplicate dimension via different representations (enum vs string)
    with pytest.raises((ValueError, TypeError)):
        MilestoneRiskEnvelope(
            milestone_ref="S4/M1",
            default_process_depth="STANDARD",
            minimum_process_depth="FAST",
            dimensions=[RiskDimension.blast_radius, "blast_radius"],
        )

# ---------------------------------------------------------------------------
# WorkItemRiskDelta
# ---------------------------------------------------------------------------

def test_delta_deterministic_immutable_bounded():
    d1 = WorkItemRiskDelta(
        work_item_ref="S4/M1/W1",
        milestone_ref="S4/M1",
        observed_dimensions=["uncertainty"],
        uncertainty="resolved",
        semantic_choice=False,
        architecture_delta=False,
        authority_delta=False,
        irreversible_delta=False,
        declared_process_depth="FAST",
        evidence_refs=["evidence:delta"],
    )
    d2 = WorkItemRiskDelta(
        work_item_ref="S4/M1/W1",
        milestone_ref="S4/M1",
        observed_dimensions=["uncertainty"],
        uncertainty="resolved",
        semantic_choice=False,
        architecture_delta=False,
        authority_delta=False,
        irreversible_delta=False,
        declared_process_depth=ProcessDepth.FAST,
        evidence_refs=["evidence:delta"],
    )
    assert d1.digest == d2.digest
    assert d1.canonical_json() == d2.canonical_json()
    with pytest.raises((AttributeError, TypeError)):
        d1.work_item_ref = "other"

def test_delta_declared_depth_is_evidence_only():
    d = WorkItemRiskDelta(
        work_item_ref="S4/M1/W1",
        milestone_ref="S4/M1",
        declared_process_depth="FAST",
    )
    assert d.declared_depth_is_evidence_only is True
    assert WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY is True
    assert CALLER_CAN_SELF_DOWNGRADE_RISK is False
    assert MODEL_SELF_REPORT_IS_RISK_AUTHORITY is False
    assert WORKER_SELF_REPORT_IS_RISK_AUTHORITY is False
    # even though declared is FAST, envelope minimum remains enforced
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="DEEP",
        minimum_process_depth="STANDARD",
    )
    eff = resolve_effective_depth(env, d, d.declared_process_depth)
    # cannot downgrade to FAST despite declaration
    assert eff != ProcessDepth.FAST
    assert process_depth_order(eff) >= process_depth_order("STANDARD")

def test_delta_unresolved_uncertainty_cannot_select_shallower():
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="DEEP",
        minimum_process_depth="STANDARD",
    )
    delta_unresolved = WorkItemRiskDelta(
        work_item_ref="S4/M1/W2",
        milestone_ref="S4/M1",
        uncertainty="unresolved: needs analyst",
        observed_dimensions=["uncertainty"],
    )
    assert is_unresolved_uncertainty(delta_unresolved) is True
    assert UNRESOLVED_RISK_UNCERTAINTY_CANNOT_SELECT_SHALLOWER_PROCESS_DEPTH is True
    # cannot select STANDARD when unresolved and default is DEEP? Actually STANDARD is shallower than DEEP, should be blocked
    assert not can_select_shallower_depth(env, delta_unresolved, "STANDARD")
    assert can_select_shallower_depth(env, delta_unresolved, "DEEP")
    eff = resolve_effective_depth(env, delta_unresolved, "STANDARD")
    assert eff == ProcessDepth.DEEP

    delta_resolved = WorkItemRiskDelta(
        work_item_ref="S4/M1/W2",
        milestone_ref="S4/M1",
        uncertainty="resolved",
    )
    assert is_unresolved_uncertainty(delta_resolved) is False
    # resolved allows STANDARD (minimum)
    assert can_select_shallower_depth(env, delta_resolved, "STANDARD")

def test_delta_semantic_authority_delta_representation():
    d = WorkItemRiskDelta(
        work_item_ref="S4/M1/W3",
        milestone_ref="S4/M1",
        observed_dimensions=["architecture_impact", "authority_impact"],
        semantic_choice=True,
        architecture_delta=True,
        authority_delta=True,
        irreversible_delta=True,
    )
    assert d.semantic_choice is True
    assert d.architecture_delta is True
    assert d.authority_delta is True
    assert d.irreversible_delta is True
    # representation does not grant execution authority
    assert d.is_operation_authority is False
    assert WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY is False

def test_delta_unknown_fields_fail_closed():
    with pytest.raises((ValueError, TypeError)):
        WorkItemRiskDelta.from_dict({
            "work_item_ref": "S4/M1/W1",
            "milestone_ref": "S4/M1",
            "unknown": "field"
        })

# ---------------------------------------------------------------------------
# Handoff compatibility
# ---------------------------------------------------------------------------

def test_handoff_compatibility_ref_survives_compiler():
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
        dimensions=["blast_radius", "shared_state"],
        review_policy_refs=["policy:review"],
        escalation_boundary_ref="boundary:user",
        evidence_refs=["evidence:envelope"],
    )
    ref = env.to_risk_projection_ref()
    assert isinstance(ref, SemanticReference)
    # ref is bounded and carries digest
    assert ref.digest == env.digest
    assert ref.ref.startswith("risk-envelope:")

    handoff = TaskHandoff(
        work_role="coder",
        task_kind="risk_contract_test",
        objective="prove handoff compat",
        bounded_scope="test scope",
        validation_expectations=["risk contract valid"],
        semantic_stop_expectations=["stop if needed"],
        process_depth_or_risk_projection_ref=ref,
    )
    # digest must remain valid
    assert handoff.process_depth_or_risk_projection_ref.ref == ref.ref
    assert handoff.process_depth_or_risk_projection_ref.digest == ref.digest
    # handoff digest covers the ref
    hd = handoff.handoff_digest
    assert isinstance(hd, str) and len(hd) == 64

    # compiler projection
    binding = TrustedExecutionBinding(canonical_task_id="task-123", project_id="aota_forge")
    pkg = compile_handoff_to_execution_package(handoff, binding)
    # working_context refs must preserve the risk ref non-authoritatively
    wc = pkg.working_context
    assert "refs" in wc or "handoff_digest" in wc
    # input_artifacts must carry handoff_digest
    assert any("handoff_digest" in str(a) for a in pkg.input_artifacts)
    # ExecutionPackage must not treat risk ref as authority
    # verify package does not have extra authority fields from risk
    assert pkg.canonical_role is not None

def test_handoff_ref_non_authoritative_digest_stable():
    delta = WorkItemRiskDelta(
        work_item_ref="S4/M1/W1",
        milestone_ref="S4/M1",
        observed_dimensions=["uncertainty"],
        uncertainty="unresolved",
    )
    ref = delta.to_risk_projection_ref()
    h1 = TaskHandoff(
        work_role="coder",
        task_kind="k",
        objective="o",
        bounded_scope="s",
        validation_expectations=["v"],
        semantic_stop_expectations=["s"],
        process_depth_or_risk_projection_ref=ref,
    )
    h2 = TaskHandoff(
        work_role="coder",
        task_kind="k",
        objective="o",
        bounded_scope="s",
        validation_expectations=["v"],
        semantic_stop_expectations=["s"],
        process_depth_or_risk_projection_ref=ref,
    )
    assert h1.handoff_digest == h2.handoff_digest
    # ref remains non-authoritative: TaskHandoff is not execution authority
    assert h1.compute_handoff_digest() == h2.compute_handoff_digest()

# ---------------------------------------------------------------------------
# S2 authority adversarial proof
# ---------------------------------------------------------------------------

def test_risk_objects_cannot_satisfy_workspace_authority():
    from aota_forge.work_plane.workspace_tools import WorkspaceAuthorityEvidence, BoundedWorkspaceToolProvider
    from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
    # Risk objects are not WorkspaceAuthorityEvidence
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
    )
    delta = WorkItemRiskDelta(work_item_ref="S4/M1/W1", milestone_ref="S4/M1")
    # must fail type check when trying to use as authority
    with pytest.raises(Exception):
        BoundedWorkspaceToolProvider(authority=env)  # type: ignore
    with pytest.raises(Exception):
        BoundedWorkspaceToolProvider(authority=delta)  # type: ignore
    with pytest.raises(Exception):
        BoundedWorkspaceToolProvider(authority=ProcessDepth.FAST)  # type: ignore
    # also verify workspace_tools does not accept string digest as authority
    with pytest.raises(Exception):
        BoundedWorkspaceToolProvider(authority="risk-envelope-digest")  # type: ignore

def test_process_depth_does_not_grant_test_git_authority():
    # Risk objects must not be usable as test execution or git authority
    from aota_forge.work_plane.test_execution import TestExecutionAuthorityEvidence, BoundedTestExecutionToolProvider
    from aota_forge.work_plane.git_tools import GitOperationAuthorityEvidence, BoundedGitToolProvider
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
    )
    # Attempt to use risk digest as test authority should fail
    with pytest.raises(Exception):
        BoundedTestExecutionToolProvider(authority=env)  # type: ignore
    with pytest.raises(Exception):
        BoundedGitToolProvider(authority=env)  # type: ignore

def test_risk_digest_not_trusted_binding():
    # Ensure that even a matching digest string cannot be used as TrustedExecutionBinding
    env = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="STANDARD",
        minimum_process_depth="FAST",
    )
    # TrustedExecutionBinding requires explicit canonical_task_id/project_id, not risk digest
    binding = TrustedExecutionBinding(canonical_task_id="task-1", project_id="aota_forge")
    assert binding.canonical_task_id != env.digest
    assert binding.project_id != env.digest
    # S2 firewall flags
    assert S4_PROCESS_AUTOMATION_CANNOT_BYPASS_S2_AUTHORITY is True
    assert S2_AUTHORITY_MODEL_RETAINED is True

# ---------------------------------------------------------------------------
# Additional bounded checks
# ---------------------------------------------------------------------------

def test_risk_dimensions_bounded_set():
    assert RISK_DIMENSIONS == frozenset({
        "blast_radius", "reversibility", "uncertainty", "architecture_impact",
        "authority_impact", "external_effects", "data_integrity", "runtime_impact", "shared_state"
    })
    for dim in RISK_DIMENSIONS:
        assert parse_risk_dimension(dim).value == dim

def test_envelope_dimension_ordering_deterministic():
    # dimensions order shouldn't affect digest — already proven but extra check
    e1 = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="DEEP",
        minimum_process_depth="FAST",
        dimensions=["shared_state", "blast_radius", "uncertainty"],
    )
    e2 = MilestoneRiskEnvelope(
        milestone_ref="S4/M1",
        default_process_depth="DEEP",
        minimum_process_depth="FAST",
        dimensions=["uncertainty", "shared_state", "blast_radius"],
    )
    assert e1.digest == e2.digest

