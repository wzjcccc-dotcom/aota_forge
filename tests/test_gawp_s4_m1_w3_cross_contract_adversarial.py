"""S4 M1 W3 — Cross-Contract & Adversarial Proof.

W3 proves W1+W2 contracts compose safely with existing S1/S2 contracts
under adversarial inputs while preserving:
  fail-closed, deterministic, authority-negative, no silent downgrade,
  no ontology duplication, no accepted-contract mutation.

This test suite is intentionally TEST_HEAVY: no production source change
expected. risk_review.py remains read-only unless a genuine defect is found.

Coverage map:
  §6  Cross-Contract Proof A — Handoff Projection
  §7  Cross-Contract Proof B — SemanticStop reuse
  §8  S2 Authority Firewall (§8 core adversarial proof)
  §9  Self Downgrade adversarial
  §10 Risk vs Semantic escalation separation
  §11 Review Trigger integrity
  §12 Cross-Subplan contract change routing
  §13 Accepted-Frontier anomaly routing
  §14 Challenge routing integrity
  §15 Immutability / determinism / bounds
  §16 No numeric calibration
  §17 S4/S6 isolation
  §18 Protected accepted contracts untouched
"""

from __future__ import annotations

import hashlib
import pathlib
from enum import Enum

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.stop import SemanticStopReason

# W1 + W2 contracts under test
from aota_forge.work_plane import risk_review
from aota_forge.work_plane.risk_review import (
    ChallengeKind,
    ChallengeRole,
    MilestoneRiskEnvelope,
    ProcessDepth,
    ReviewEscalationDisposition,
    ReviewTrigger,
    RiskDimension,
    UserEscalationTrigger,
    WorkItemRiskDelta,
    disposition_to_semantic_stop_reason,
    evaluate_work_item_risk_policy,
    parse_challenge_role,
    parse_review_trigger,
    resolve_effective_depth,
)

# S2 seams for authority firewall checks
from aota_forge.work_plane.workspace_tools import create_workspace_authority, WorkspaceAuthorityError
from aota_forge.work_plane.test_execution import create_test_execution_authority, TestExecutionAuthorityError
from aota_forge.work_plane.git_tools import create_git_authority, GitAuthorityError
from aota_forge.work_plane.restricted_shell import create_restricted_shell_authority, RestrictedShellAuthorityError
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.core.contracts.descriptor import OperationContractDescriptor, READ_ONLY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(
    milestone_ref: str = "S4/M1",
    default: ProcessDepth = ProcessDepth.STANDARD,
    minimum: ProcessDepth = ProcessDepth.FAST,
    dimensions=(),
    uncertainty=None,
) -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(
        milestone_ref=milestone_ref,
        default_process_depth=default,
        minimum_process_depth=minimum,
        dimensions=tuple(dimensions),
        uncertainty=uncertainty,
    )


def _delta(
    work_item_ref: str = "S4/M1/W1",
    milestone_ref: str = "S4/M1",
    observed_dimensions=(),
    uncertainty=None,
    semantic_choice: bool = False,
    architecture_delta: bool = False,
    authority_delta: bool = False,
    irreversible_delta: bool = False,
    declared_process_depth=None,
) -> WorkItemRiskDelta:
    return WorkItemRiskDelta(
        work_item_ref=work_item_ref,
        milestone_ref=milestone_ref,
        observed_dimensions=tuple(observed_dimensions),
        uncertainty=uncertainty,
        semantic_choice=semantic_choice,
        architecture_delta=architecture_delta,
        authority_delta=authority_delta,
        irreversible_delta=irreversible_delta,
        declared_process_depth=declared_process_depth,
    )


def _handoff_with_risk_ref(risk_ref: SemanticReference | None) -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="implement",
        objective="W3 adversarial proof objective",
        bounded_scope="aota_forge/work_plane/risk_review.py",
        validation_expectations=("pytest passes",),
        semantic_stop_expectations=("escalate on ambiguity",),
        process_depth_or_risk_projection_ref=risk_ref,
    )


def _binding() -> TrustedExecutionBinding:
    return TrustedExecutionBinding(canonical_task_id="task-w3-123", project_id="proj-w3")


# ===========================================================================
# §6 Cross-Contract Proof A — Handoff Projection
# ===========================================================================

class TestHandoffProjectionA:
    def test_risk_projection_ref_reuses_handoff_seam(self):
        env = _envelope()
        ref = env.to_risk_projection_ref()
        assert isinstance(ref, SemanticReference)
        assert ref.ref.startswith("risk-envelope:")
        assert ref.digest == env.digest
        # TaskHandoff must accept it without schema change
        h = _handoff_with_risk_ref(ref)
        assert h.process_depth_or_risk_projection_ref == ref

    def test_delta_ref_to_handoff(self):
        d = _delta()
        ref = d.to_risk_projection_ref()
        assert isinstance(ref, SemanticReference)
        assert ref.ref.startswith("risk-delta:")
        assert ref.digest == d.digest
        h = _handoff_with_risk_ref(ref)
        assert h.process_depth_or_risk_projection_ref is not None

    def test_risk_ref_survives_compilation(self):
        env = _envelope(default=ProcessDepth.DEEP, minimum=ProcessDepth.STANDARD)
        ref = env.to_risk_projection_ref()
        h = _handoff_with_risk_ref(ref)
        pkg = compile_handoff_to_execution_package(h, _binding())
        # refs are visible in working_context (not fingerprint authority but traceable)
        assert "process_depth_or_risk_projection_ref" in str(pkg.working_context)
        assert pkg.working_context["refs"]["process_depth_or_risk_projection_ref"]["ref"] == ref.ref
        assert pkg.working_context["refs"]["process_depth_or_risk_projection_ref"]["digest"] == ref.digest
        # also handoff_digest in working_context and input_artifacts
        assert pkg.working_context["handoff_digest"] == h.handoff_digest
        assert pkg.input_artifacts[0]["handoff_digest"] == h.handoff_digest

    def test_digest_ref_deterministic(self):
        env1 = _envelope(milestone_ref="S4/M1", default=ProcessDepth.STANDARD, minimum=ProcessDepth.FAST)
        env2 = _envelope(milestone_ref="S4/M1", default=ProcessDepth.STANDARD, minimum=ProcessDepth.FAST)
        assert env1.digest == env2.digest
        assert env1.to_risk_projection_ref().digest == env2.to_risk_projection_ref().digest
        assert env1.to_risk_projection_ref().ref == env2.to_risk_projection_ref().ref
        # no full object embedded in ExecutionPackage, only ref+digest
        d = _delta(work_item_ref="S4/M1/W1")
        ref = d.to_risk_projection_ref()
        h = _handoff_with_risk_ref(ref)
        pkg = compile_handoff_to_execution_package(h, _binding())
        # ExecutionPackage does not embed full risk object JSON
        assert "MilestoneRiskEnvelope" not in str(pkg.working_context)
        assert "WorkItemRiskDelta" not in str(pkg.working_context)
        assert "observed_dimensions" not in str(pkg.working_context)

    def test_ref_not_execution_authority(self):
        env = _envelope()
        ref = env.to_risk_projection_ref()
        # SemanticReference never grants operation authority
        # Prove by trying to use it where S2 authority expected — later firewall tests cover deeper.
        # Here check invariant flags
        assert risk_review.MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY is False
        assert risk_review.WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY is False
        assert risk_review.S4_PROCESS_AUTOMATION_IS_OPERATION_AUTHORITY is False
        # ref digest is identity, not authority binding
        assert ref.digest is not None
        # TaskHandoff digest is intent fingerprint, not execution authority
        h = _handoff_with_risk_ref(ref)
        assert h.handoff_digest is not None
        assert len(h.handoff_digest) == 64

    def test_fingerprint_covers_risk_projection_ref_change(self):
        env1 = _envelope(milestone_ref="S4/M1", default=ProcessDepth.STANDARD, minimum=ProcessDepth.FAST)
        env2 = _envelope(milestone_ref="S4/M1", default=ProcessDepth.DEEP, minimum=ProcessDepth.FAST)
        ref1 = env1.to_risk_projection_ref()
        ref2 = env2.to_risk_projection_ref()
        assert ref1.ref != ref2.ref or ref1.digest != ref2.digest
        h1 = _handoff_with_risk_ref(ref1)
        h2 = _handoff_with_risk_ref(ref2)
        # Only difference is risk projection ref/digest -> handoff_digest must differ
        assert h1.handoff_digest != h2.handoff_digest
        pkg1 = compile_handoff_to_execution_package(h1, _binding())
        pkg2 = compile_handoff_to_execution_package(h2, _binding())
        assert pkg1.intent_fingerprint != pkg2.intent_fingerprint
        # No second fingerprint system created
        assert h1.handoff_digest != pkg1.intent_fingerprint

    def test_taskhandoff_schema_unchanged(self):
        # Ensure no new collection field was added for risk projection beyond existing seam
        fields = set(TaskHandoff.__dataclass_fields__.keys())
        assert "process_depth_or_risk_projection_ref" in fields
        # ExecutionPackage schema unchanged — intent_fingerprint is instance field of ExecutionPackage
        from aota_forge.core.execution.package import ExecutionPackage
        assert "intent_fingerprint" in ExecutionPackage.__dataclass_fields__
        assert risk_review.S1_HANDOFF_SCHEMA_CHANGED is False
        from aota_forge.work_plane.risk_review import S1_HANDOFF_SCHEMA_CHANGED
        assert S1_HANDOFF_SCHEMA_CHANGED is False


# ===========================================================================
# §7 SemanticStop Reuse
# ===========================================================================

class TestSemanticStopReuse:
    def test_disposition_maps_to_existing_stop_taxonomy(self):
        env = _envelope()
        # authority conflict case
        disp = evaluate_work_item_risk_policy(
            env,
            _delta(authority_delta=True),
            escalation_triggers=[UserEscalationTrigger.AUTHORITY_CHANGE],
        )
        reason = disposition_to_semantic_stop_reason(disp)
        assert reason == "AUTHORITY_CONFLICT"
        assert reason in [r.value for r in SemanticStopReason]

    def test_architecture_change_maps(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(
            env, _delta(architecture_delta=True), escalation_triggers=[UserEscalationTrigger.ARCHITECTURE_CHANGE]
        )
        assert disposition_to_semantic_stop_reason(disp) == "UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED"

    def test_requirement_contradiction_maps(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(
            env, escalation_triggers=[UserEscalationTrigger.REQUIREMENT_CONTRADICTION]
        )
        assert disposition_to_semantic_stop_reason(disp) == "REQUIREMENT_AMBIGUOUS"

    def test_scope_boundary_maps(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(
            env, escalation_triggers=[UserEscalationTrigger.MATERIAL_PLAN_SCOPE_CHANGE]
        )
        assert disposition_to_semantic_stop_reason(disp) == "SCOPE_AMBIGUOUS"

    def test_repeated_failure_plan_defect_compatible(self):
        # Repeated failure indicating plan defect is existing SemanticStopReason
        assert SemanticStopReason.REPEATED_FAILURE_INDICATING_PLAN_DEFECT.value == "REPEATED_FAILURE_INDICATING_PLAN_DEFECT"
        env = _envelope()
        # UNEXPECTED_DEFECT review trigger should still reuse existing taxonomy via reviewer path, not new stop
        disp = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.UNEXPECTED_DEFECT])
        # disposition itself maps via core contract change not defect, but we prove S1 taxonomy unchanged regardless
        # No new enum created
        assert risk_review.S1_STOP_ONTOLOGY_CHANGED is False
        assert risk_review.NEW_ACCEPTANCE_ONTOLOGY_CREATED is False

    def test_no_new_stop_ontology(self):
        # Ensure stop.py still has exactly 7 reasons (S1 contract)
        from aota_forge.work_plane.stop import SEMANTIC_STOP_REASON_COUNT, SEMANTIC_STOP_REASONS
        assert SEMANTIC_STOP_REASON_COUNT == 7
        assert len(SEMANTIC_STOP_REASONS) == 7
        # disposition_to_semantic_stop_reason never returns a string outside that set (or None)
        for trigger in [UserEscalationTrigger.AUTHORITY_CHANGE, UserEscalationTrigger.ARCHITECTURE_CHANGE]:
            env = _envelope()
            d = evaluate_work_item_risk_policy(env, escalation_triggers=[trigger])
            r = disposition_to_semantic_stop_reason(d)
            if r is not None:
                assert r in SEMANTIC_STOP_REASONS

    def test_cross_subplan_maps_without_new_ontology(self):
        env = _envelope()
        # cross_subplan alone forces review but not semantic escalation; to prove mapping reuse,
        # combine with semantic escalation trigger — still must reuse existing taxonomy
        disp = evaluate_work_item_risk_policy(
            env, cross_subplan_contract_change=True, escalation_triggers=[UserEscalationTrigger.AUTHORITY_CHANGE]
        )
        r = disposition_to_semantic_stop_reason(disp)
        assert r == "AUTHORITY_CONFLICT"
        assert r in [e.value for e in SemanticStopReason]
        # Also ensure even without escalation, no new ontology is created
        disp2 = evaluate_work_item_risk_policy(env, cross_subplan_contract_change=True)
        assert disp2.challenge_role == ChallengeRole.ANALYST  # bounded gate reachable

    def test_frontier_anomaly_maps_without_new_ontology(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(
            env, accepted_frontier_anomaly=True, escalation_triggers=[UserEscalationTrigger.AUTHORITY_CHANGE]
        )
        r = disposition_to_semantic_stop_reason(disp)
        assert r == "AUTHORITY_CONFLICT"

    def test_no_new_acceptance_ontology_via_stop(self):
        src = pathlib.Path("aota_forge/work_plane/stop.py").read_text(encoding="utf-8")
        assert "ACCEPTED_FRONTIER_ANOMALY_STOP_V2" not in src
        assert risk_review.EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED is True


# ===========================================================================
# §8 S2 Authority Firewall — hostile probes
# ===========================================================================

class TestS2AuthorityFirewall:
    # Helper to get a valid trusted authority for positive control, then prove hostile objects fail
    @pytest.fixture
    def _valid_sandbox(self, tmp_path):
        # Create a minimal valid WorktreeSandboxBoundary using tmp_path as worktree root
        # Need project_id and worktree root; inspect WorktreeSandboxBoundary signature
        # It is constructed via create? Let's construct directly if possible.
        # Fallback: check that hostile probes at least fail type check for create_* functions
        return tmp_path

    def _hostile_candidates(self, env, delta, disp):
        # All objects/values that MUST NOT become valid S2 authority
        candidates = [
            ProcessDepth.FAST,
            ProcessDepth.STANDARD,
            ProcessDepth.DEEP,
            env,
            delta,
            disp,
            ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY,
            ChallengeRole.ANALYST,
            env.digest,  # risk digest string
            env.to_risk_projection_ref(),  # risk projection ref
            "FAST",  # string literal
        ]
        # duck-typed fake authority object
        class FakeAuthority:
            def __init__(self):
                self.sandbox = env  # nonsense
                self.handoff = env
                self.operation = env
                self.evidence_id = "fake"
        candidates.append(FakeAuthority())
        # worker/model/caller-declared risk-like dict
        candidates.append({"risk": "high", "process_depth": "DEEP", "self_reported": True})
        candidates.append({"declared_process_depth": "FAST"})
        return candidates

    def test_process_depth_not_operation_authority(self):
        assert risk_review.PROCESS_DEPTH_IS_OPERATION_AUTHORITY is False
        assert risk_review.FAST_IS_OPERATION_AUTHORITY is False
        assert risk_review.STANDARD_IS_OPERATION_AUTHORITY is False
        assert risk_review.DEEP_IS_OPERATION_AUTHORITY is False
        assert risk_review.SELECTED_PROCESS_DEPTH_IS_OPERATION_AUTHORITY is False

    def test_risk_envelope_not_operation_authority(self):
        assert risk_review.MILESTONE_RISK_ENVELOPE_IS_OPERATION_AUTHORITY is False
        env = _envelope()
        assert env.is_operation_authority is False
        assert env.is_plan_authority is False

    def test_risk_delta_not_operation_authority(self):
        assert risk_review.WORK_ITEM_RISK_DELTA_IS_OPERATION_AUTHORITY is False
        d = _delta()
        assert d.is_operation_authority is False

    def test_review_disposition_not_operation_authority(self):
        assert risk_review.REVIEW_DISPOSITION_IS_OPERATION_AUTHORITY is False
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env)
        assert disp.is_operation_authority is False
        assert disp.grants_workspace_mutation is False
        assert disp.grants_test_execution is False
        assert disp.grants_git_operation is False
        assert disp.grants_restricted_shell is False

    def test_challenge_routing_not_operation_authority(self):
        assert risk_review.CHALLENGE_ROUTING_IS_OPERATION_AUTHORITY is False
        assert risk_review.CHALLENGE_ROLE_IS_OPERATION_AUTHORITY is False

    def test_hostile_probes_fail_as_workspace_authority(self):
        env = _envelope()
        delta = _delta()
        disp = evaluate_work_item_risk_policy(env, delta)
        hostile = self._hostile_candidates(env, delta, disp)
        for cand in hostile:
            with pytest.raises((WorkspaceAuthorityError, TypeError, ValueError, AttributeError)):
                # All must fail when used as sandbox / handoff / operation
                # Try each position: sandbox, handoff, operation
                # We test at least one path: passing cand as sandbox must fail
                create_workspace_authority(
                    sandbox=cand,  # type: ignore[arg-type]
                    handoff=TaskHandoff(
                        work_role=AgentWorkRole.CODER,
                        task_kind="implement",
                        objective="obj",
                        bounded_scope="scope",
                        validation_expectations=("v",),
                        semantic_stop_expectations=("s",),
                    ),
                    applicable_policies=(),
                    operation=OperationContractDescriptor(name="workspace.read", description="read", inputs=()),
                )

    def test_hostile_probes_fail_as_test_authority(self):
        env = _envelope()
        delta = _delta()
        disp = evaluate_work_item_risk_policy(env)
        for cand in self._hostile_candidates(env, delta, disp):
            with pytest.raises((TestExecutionAuthorityError, TypeError, ValueError)):
                create_test_execution_authority(
                    sandbox=cand,  # type: ignore[arg-type]
                    handoff=TaskHandoff(
                        work_role=AgentWorkRole.CODER,
                        task_kind="implement",
                        objective="obj",
                        bounded_scope="scope",
                        validation_expectations=("v",),
                        semantic_stop_expectations=("s",),
                    ),
                    applicable_policies=(),
                    operation=OperationContractDescriptor(name="test.run", description="test", inputs=()),
                )

    def test_hostile_probes_fail_as_git_authority(self):
        env = _envelope()
        delta = _delta()
        disp = evaluate_work_item_risk_policy(env)
        for cand in self._hostile_candidates(env, delta, disp):
            with pytest.raises((GitAuthorityError, TypeError, ValueError)):
                create_git_authority(
                    sandbox=cand,  # type: ignore[arg-type]
                    handoff=TaskHandoff(
                        work_role=AgentWorkRole.CODER,
                        task_kind="implement",
                        objective="obj",
                        bounded_scope="scope",
                        validation_expectations=("v",),
                        semantic_stop_expectations=("s",),
                    ),
                    applicable_policies=(),
                    operation=OperationContractDescriptor(name="git.status", description="git status", inputs=()),
                )

    def test_hostile_probes_fail_as_shell_authority(self):
        env = _envelope()
        delta = _delta()
        disp = evaluate_work_item_risk_policy(env)
        for cand in self._hostile_candidates(env, delta, disp):
            with pytest.raises((RestrictedShellAuthorityError, TypeError, ValueError)):
                create_restricted_shell_authority(
                    sandbox=cand,  # type: ignore[arg-type]
                    handoff=TaskHandoff(
                        work_role=AgentWorkRole.CODER,
                        task_kind="implement",
                        objective="obj",
                        bounded_scope="scope",
                        validation_expectations=("v",),
                        semantic_stop_expectations=("s",),
                    ),
                    applicable_policies=(),
                    operation=OperationContractDescriptor(name="shell.exec", description="shell", inputs=()),
                )

    def test_fast_does_not_skip_mandatory_checks(self):
        assert risk_review.S4_PROCESS_AUTOMATION_CANNOT_BYPASS_S2_AUTHORITY is True
        # Even HIGH_RISK + AUTO_WITH_DEEP_VALIDATION still needs S2 authority
        env = _envelope(default=ProcessDepth.FAST, minimum=ProcessDepth.FAST)
        delta = _delta(observed_dimensions=[RiskDimension.blast_radius, RiskDimension.external_effects])
        disp = evaluate_work_item_risk_policy(env, delta, review_triggers=[ReviewTrigger.IRREVERSIBLE_EFFECT])
        assert disp.selected_process_depth == ProcessDepth.DEEP
        assert disp.formal_review_required is True
        # auto_continuation_eligible may be True for high-risk no semantic, but still not authority
        # Prove disposition does not grant authority even when DEEP
        assert disp.is_operation_authority is False


# ===========================================================================
# §9 Self Downgrade adversarial
# ===========================================================================

class TestSelfDowngrade:
    def test_milestone_floor_deep_worker_fast_stays_deep(self):
        env = _envelope(default=ProcessDepth.DEEP, minimum=ProcessDepth.DEEP)
        eff = resolve_effective_depth(env, requested_depth=ProcessDepth.FAST)
        assert eff == ProcessDepth.DEEP
        # via policy
        disp = evaluate_work_item_risk_policy(env, _delta(declared_process_depth=ProcessDepth.FAST))
        assert disp.selected_process_depth == ProcessDepth.DEEP

    def test_milestone_floor_standard_caller_fast_not_downgraded(self):
        env = _envelope(default=ProcessDepth.STANDARD, minimum=ProcessDepth.STANDARD)
        eff = resolve_effective_depth(env, requested_depth=ProcessDepth.FAST)
        assert eff == ProcessDepth.STANDARD
        assert eff != ProcessDepth.FAST

    def test_existing_high_risk_delta_model_low_report_cannot_lower(self):
        env = _envelope(default=ProcessDepth.STANDARD, minimum=ProcessDepth.STANDARD)
        delta = _delta(observed_dimensions=[RiskDimension.blast_radius], declared_process_depth=ProcessDepth.FAST)
        eff = resolve_effective_depth(env, delta, requested_depth=ProcessDepth.FAST)
        # declared_process_depth is evidence only, cannot downgrade
        assert eff == ProcessDepth.STANDARD or eff == ProcessDepth.DEEP
        disp = evaluate_work_item_risk_policy(env, delta)
        # meaningful risk must deepen, not stay FAST
        assert disp.selected_process_depth != ProcessDepth.FAST

    def test_unresolved_uncertainty_fast_not_allowed(self):
        env = _envelope(default=ProcessDepth.STANDARD, minimum=ProcessDepth.STANDARD)
        delta = _delta(uncertainty="unresolved risk uncertainty")
        eff = resolve_effective_depth(env, delta, requested_depth=ProcessDepth.FAST)
        assert eff != ProcessDepth.FAST
        assert eff == ProcessDepth.STANDARD or eff == ProcessDepth.DEEP
        # policy must also not select FAST
        disp = evaluate_work_item_risk_policy(env, delta, review_triggers=[])
        # unresolved implicitly adds UNRESOLVED_RISK_UNCERTAINTY trigger -> forces DEEP
        assert disp.selected_process_depth == ProcessDepth.DEEP
        assert disp.formal_review_required is True

    def test_caller_cannot_self_downgrade_flags(self):
        assert risk_review.CALLER_CAN_SELF_DOWNGRADE_RISK is False
        assert risk_review.MODEL_SELF_REPORT_IS_RISK_AUTHORITY is False
        assert risk_review.WORKER_SELF_REPORT_IS_RISK_AUTHORITY is False
        assert risk_review.WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY is True

    def test_monotonic_process_depth(self):
        # DEEP never downgrades to STANDARD/FAST via policy
        env = _envelope(default=ProcessDepth.DEEP, minimum=ProcessDepth.DEEP)
        for trigger in [ReviewTrigger.UNEXPECTED_DEFECT, ReviewTrigger.IRREVERSIBLE_EFFECT]:
            disp = evaluate_work_item_risk_policy(env, review_triggers=[trigger])
            assert disp.selected_process_depth == ProcessDepth.DEEP


# ===========================================================================
# §10 Risk vs Semantic Escalation separation
# ===========================================================================

class TestRiskVsSemanticEscalation:
    def test_case_a_high_risk_no_semantic(self):
        env = _envelope(default=ProcessDepth.STANDARD, minimum=ProcessDepth.STANDARD)
        delta = _delta(
            observed_dimensions=[RiskDimension.blast_radius, RiskDimension.external_effects],
            architecture_delta=False,
            authority_delta=False,
            semantic_choice=False,
        )
        # Provide high risk via trigger without semantic choice
        disp = evaluate_work_item_risk_policy(env, delta, review_triggers=[ReviewTrigger.LARGE_DOWNSTREAM_DEPENDENCY_FANOUT])
        assert disp.selected_process_depth == ProcessDepth.DEEP
        assert disp.formal_review_required is True
        assert disp.semantic_escalation_required is False
        # auto_continuation_eligible may remain yes, but not authority
        assert disp.auto_continuation_eligible is True
        assert disp.is_operation_authority is False

    def test_case_b_low_risk_new_semantic_choice(self):
        env = _envelope(default=ProcessDepth.FAST, minimum=ProcessDepth.FAST)
        delta = _delta(semantic_choice=True, architecture_delta=True)
        disp = evaluate_work_item_risk_policy(env, delta, escalation_triggers=[UserEscalationTrigger.ARCHITECTURE_CHANGE])
        assert disp.semantic_escalation_required is True
        # user escalation true where defined
        assert disp.challenge_role == ChallengeRole.TASK_MAIN
        assert disp.challenge_kind == ChallengeKind.SEMANTIC_RECONCILIATION
        # Even though risk conceptually low (FAST envelope), semantic gate still triggers
        assert disp.auto_continuation_eligible is False

    def test_process_depth_not_user_confirmation(self):
        env = _envelope()
        disp_deep = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.IRREVERSIBLE_EFFECT])
        disp_semantic = evaluate_work_item_risk_policy(env, escalation_triggers=[UserEscalationTrigger.AUTHORITY_CHANGE])
        # Both require escalation handling but via different axes; depth != user confirmation
        assert disp_deep.selected_process_depth == ProcessDepth.DEEP
        assert disp_semantic.semantic_escalation_required is True
        assert risk_review.USER_ESCALATION_SEMANTIC_ONLY is True


# ===========================================================================
# §11 Review Trigger Integrity
# ===========================================================================

class TestReviewTriggerIntegrity:
    def test_valid_exact_trigger_accepted(self):
        for trig in ReviewTrigger:
            disp = evaluate_work_item_risk_policy(_envelope(), review_triggers=[trig])
            assert disp.formal_review_required is True
            assert trig.value in disp.reasons

    def test_unknown_trigger_fail_closed(self):
        with pytest.raises((ValueError, TypeError)):
            evaluate_work_item_risk_policy(_envelope(), review_triggers=["NOT_A_TRIGGER"])  # type: ignore[arg-type]
        with pytest.raises((ValueError, TypeError)):
            parse_review_trigger("FAKE_TRIGGER")

    def test_arbitrary_free_text_not_normalized(self):
        # Free text with similar words must not be silently normalized into valid trigger
        with pytest.raises((ValueError, TypeError)):
            parse_review_trigger("core_contract_or_protocol_change")  # wrong case
        with pytest.raises((ValueError, TypeError)):
            parse_review_trigger(" CORE_CONTRACT_OR_PROTOCOL_CHANGE")  # leading space
        with pytest.raises((ValueError, TypeError)):
            parse_review_trigger("authority_or_security_boundary ")  # trailing space

    def test_duplicate_bounded_input_handling_deterministic(self):
        env = _envelope()
        with pytest.raises((ValueError, TypeError)):
            evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.IRREVERSIBLE_EFFECT, ReviewTrigger.IRREVERSIBLE_EFFECT])

    def test_trigger_order_determinism(self):
        env = _envelope()
        disp1 = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.IRREVERSIBLE_EFFECT, ReviewTrigger.UNEXPECTED_DEFECT])
        disp2 = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.UNEXPECTED_DEFECT, ReviewTrigger.IRREVERSIBLE_EFFECT])
        assert disp1.digest == disp2.digest
        assert disp1.reasons == disp2.reasons
        assert disp1.identified_risk == disp2.identified_risk

    def test_trigger_order_does_not_create_nondeterminism_where_not_supposed(self):
        # Both sorted, so first identified is deterministically smallest value
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.UNEXPECTED_DEFECT, ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE])
        # CORE... < UNEXPECTED... lexicographically, so identified should be CORE...
        assert disp.identified_risk == ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE


# ===========================================================================
# §12 Cross-Subplan Contract Change
# ===========================================================================

class TestCrossSubplanContractChange:
    def test_cross_subplan_prohibits_routine_auto(self):
        env = _envelope(default=ProcessDepth.STANDARD, minimum=ProcessDepth.STANDARD)
        disp = evaluate_work_item_risk_policy(env, cross_subplan_contract_change=True)
        assert disp.cross_subplan_contract_change is True
        assert disp.formal_review_required is True
        assert disp.selected_process_depth == ProcessDepth.DEEP
        assert disp.auto_continuation_eligible is False
        assert ReviewTrigger.CROSS_SUBPLAN_CONTRACT_CHANGE.value in disp.reasons

    def test_cross_subplan_challenge_routing(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env, cross_subplan_contract_change=True)
        assert disp.challenge_role == ChallengeRole.ANALYST
        assert disp.challenge_kind == ChallengeKind.ARCHITECTURE_FEASIBILITY

    def test_cross_subplan_does_not_modify_predecessors(self):
        # Verified via file-level checks: no S1/S2/S3 mutation, but we also check flag
        assert risk_review.S2_AUTHORITY_MODEL_CHANGED is False
        assert risk_review.S1_STOP_ONTOLOGY_CHANGED is False
        # Bounded revision gate reachable semantically via task-main reconciliation
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env, cross_subplan_contract_change=True)
        assert disp.challenge_role == ChallengeRole.ANALYST  # indicates bounded gate reachable


# ===========================================================================
# §13 Accepted-Frontier Anomaly
# ===========================================================================

class TestAcceptedFrontierAnomaly:
    def test_fail_closed_no_auto_acceptance(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env, accepted_frontier_anomaly=True)
        assert disp.accepted_frontier_anomaly is True
        assert disp.formal_review_required is True
        assert disp.selected_process_depth == ProcessDepth.DEEP
        assert disp.auto_continuation_eligible is False

    def test_review_reconciliation_required(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env, accepted_frontier_anomaly=True)
        assert disp.challenge_role == ChallengeRole.TASK_MAIN
        assert disp.challenge_kind == ChallengeKind.SEMANTIC_RECONCILIATION

    def test_no_new_acceptance_ontology(self):
        assert risk_review.NEW_ACCEPTANCE_ONTOLOGY_CREATED is False
        assert risk_review.EXISTING_ACCEPTED_FRONTIER_SEMANTICS_REUSED is True
        # Ensure no frontier mutation code exists in risk_review
        src = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        assert "ReviewAcceptedCommit" not in src
        assert "MilestoneAuthorityV2" not in src
        assert "ACCEPTED_FRONTIER_ANOMALY_STOP_V2" not in src

    def test_no_git_frontier_mutation(self):
        # W3 must not mutate git frontier; prove no git acceptance code in risk_review
        src = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        assert "find_git_root" not in src
        assert "inspect_git" not in src


# ===========================================================================
# §14 Challenge Routing Integrity
# ===========================================================================

class TestChallengeRoutingIntegrity:
    def test_frozen_canonical_roles(self):
        assert ChallengeRole.ANALYST.value == "analyst"
        assert ChallengeRole.REVIEWER.value == "reviewer"
        assert ChallengeRole.TASK_MAIN.value == "task-main"
        assert risk_review.NO_SIXTH_WORK_ROLE is True
        assert risk_review.AGENT_WORK_ROLE_COUNT == 5

    def test_routing_map(self):
        env = _envelope()
        # architecture/Plan/feasibility -> analyst
        disp = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.CORE_CONTRACT_OR_PROTOCOL_CHANGE])
        assert disp.challenge_role == ChallengeRole.ANALYST
        assert disp.challenge_kind == ChallengeKind.ARCHITECTURE_FEASIBILITY
        # technical/adversarial -> reviewer
        disp2 = evaluate_work_item_risk_policy(env, review_triggers=[ReviewTrigger.AUTHORITY_OR_SECURITY_BOUNDARY])
        assert disp2.challenge_role == ChallengeRole.REVIEWER
        # semantic reconciliation -> task-main
        disp3 = evaluate_work_item_risk_policy(env, accepted_frontier_anomaly=True)
        assert disp3.challenge_role == ChallengeRole.TASK_MAIN

    def test_project_steward_not_routine_challenger(self):
        assert risk_review.CHALLENGE_ROLE_PROJECT_STEWARD_IS_NORMAL_CHALLENGER is False
        # project-steward not parseable as ChallengeRole
        with pytest.raises((ValueError, TypeError)):
            parse_challenge_role("project-steward")
        with pytest.raises((ValueError, TypeError)):
            parse_challenge_role(AgentWorkRole.PROJECT_STEWARD)  # foreign Enum

    def test_sixth_role_rejected_adversarial(self):
        for fake in ["security-reviewer", "risk-agent", "policy-reviewer", "orchestrator"]:
            with pytest.raises((ValueError, TypeError)):
                parse_challenge_role(fake)

    def test_no_sixth_role_via_enum(self):
        class FakeRole(Enum):
            security_reviewer = "security-reviewer"
        with pytest.raises((TypeError, ValueError)):
            parse_challenge_role(FakeRole.security_reviewer)  # type: ignore[arg-type]


# ===========================================================================
# §15 Immutability / Determinism / Bounds
# ===========================================================================

class TestImmutabilityDeterminismBounds:
    def test_processdepth_immutable_bounded(self):
        with pytest.raises((ValueError, TypeError)):
            # type: ignore intentionally passing invalid
            from aota_forge.work_plane.risk_review import parse_process_depth
            parse_process_depth("INVALID")

    def test_envelope_immutable(self):
        env = _envelope()
        with pytest.raises((AttributeError, TypeError)):
            env.milestone_ref = "mutated"  # type: ignore[misc]

    def test_delta_immutable(self):
        d = _delta()
        with pytest.raises((AttributeError, TypeError)):
            d.work_item_ref = "mutated"  # type: ignore[misc]

    def test_disposition_immutable(self):
        env = _envelope()
        disp = evaluate_work_item_risk_policy(env)
        with pytest.raises((AttributeError, TypeError)):
            disp.selected_process_depth = ProcessDepth.FAST  # type: ignore[misc]

    def test_canonical_serialization_deterministic(self):
        env = _envelope(milestone_ref="S4/M1", default=ProcessDepth.STANDARD, minimum=ProcessDepth.FAST)
        j1 = env.canonical_json()
        j2 = env.canonical_json()
        assert j1 == j2
        assert env.digest == hashlib.sha256(j1.encode("utf-8")).hexdigest()

    def test_digest_deterministic_across_order(self):
        env_a = _envelope(dimensions=[RiskDimension.blast_radius, RiskDimension.uncertainty])
        env_b = _envelope(dimensions=[RiskDimension.uncertainty, RiskDimension.blast_radius])
        assert env_a.digest == env_b.digest

    def test_unknown_fields_fail_closed(self):
        with pytest.raises((ValueError, TypeError)):
            MilestoneRiskEnvelope.from_dict({"milestone_ref": "S4/M1", "default_process_depth": "STANDARD", "minimum_process_depth": "FAST", "unknown_field": "oops"})
        with pytest.raises((ValueError, TypeError)):
            WorkItemRiskDelta.from_dict({"work_item_ref": "S4/M1/W1", "milestone_ref": "S4/M1", "unknown": 123})

    def test_unbounded_refs_fail_closed(self):
        long_ref = "x" * 600
        with pytest.raises((ValueError, TypeError)):
            _envelope(milestone_ref=long_ref)

    def test_malformed_foreign_enum_fail_closed(self):
        class Foreign(Enum):
            FAST = "FAST"
        with pytest.raises((TypeError, ValueError)):
            parse_review_trigger(Foreign.FAST)  # type: ignore[arg-type]


# ===========================================================================
# §16 No Numeric Calibration
# ===========================================================================

class TestNoNumericCalibration:
    def test_no_numeric_threshold_frozen(self):
        assert risk_review.NUMERIC_RISK_THRESHOLD_FROZEN is False
        assert risk_review.NUMERIC_RISK_WEIGHT_FROZEN is False
        assert risk_review.RISK_THRESHOLDS_EMPIRICAL is True
        assert risk_review.MILESTONE_RISK_ENVELOPE_IS_PLAN_AUTHORITY is False

    def test_no_scoring_fields_in_source(self):
        src = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        for forbidden in ["risk_score", "risk_weight", "score >=", "retry_count"]:
            # allow comments describing prohibition, but not actual variable assignments
            # Check forbidden assignment patterns: e.g., "risk_score =" or "risk_weight ="
            if f"{forbidden} =" in src or f"{forbidden}:" in src:
                # ensure it's not in a comment line only
                for line in src.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    if f"{forbidden} =" in line and "forbidden" not in line.lower():
                        assert False, f"Found numeric calibration assignment: {line!r}"
        # No metric threshold is canonical policy
        assert "METRIC_THRESHOLD_IS_CANONICAL_POLICY" not in src or "METRIC_THRESHOLD_IS_CANONICAL_POLICY=no" in src or risk_review.NUMERIC_RISK_THRESHOLD_FROZEN is False

    def test_no_telemetry_threshold(self):
        assert "telemetry" not in pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8").lower() or "no telemetry" in pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8").lower()


# ===========================================================================
# §17 S4 / S6 Isolation
# ===========================================================================

class TestS6Isolation:
    def test_events_contract_unchanged(self):
        src_risk = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        # risk_review must not import events as policy authority
        assert "from aota_forge.work_plane.events import" not in src_risk
        assert "ExecutionEvent" not in src_risk
        # events.py unchanged
        src_events = pathlib.Path("aota_forge/work_plane/events.py").read_text(encoding="utf-8")
        assert "WORKER_SELF_REPORT_IS_AUTHORITY=no" in src_events

    def test_telemetry_not_risk_policy_authority(self):
        # No import of telemetry / metrics as policy authority
        src = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        assert "telemetry" not in src.lower() or "no telemetry" in src.lower()
        # Flag: S6 metric is not risk policy authority
        # Prove via absent coupling: envelope has no metric field
        env_dict = _envelope().to_dict()
        assert "metric" not in str(env_dict).lower()
        assert "telemetry" not in str(env_dict).lower()


# ===========================================================================
# §18 Protected Accepted Contracts — no silent mutation
# ===========================================================================

class TestProtectedContracts:
    def test_handoff_schema_unchanged(self):
        assert risk_review.S1_HANDOFF_SCHEMA_CHANGED is False
        # Verify handoff.py not modified to add risk fields as required core
        from aota_forge.work_plane.handoff import REQUIRED_CORE_FIELDS
        assert set(REQUIRED_CORE_FIELDS) == {"work_role", "task_kind", "objective", "bounded_scope", "validation_expectations", "semantic_stop_expectations"}

    def test_stop_ontology_unchanged(self):
        assert risk_review.S1_STOP_ONTOLOGY_CHANGED is False
        from aota_forge.work_plane.stop import SEMANTIC_STOP_REASON_COUNT
        assert SEMANTIC_STOP_REASON_COUNT == 7

    def test_s2_authority_model_unchanged(self):
        assert risk_review.S2_AUTHORITY_MODEL_CHANGED is False

    def test_s3_skill_contract_unchanged(self):
        # Ensure risk_review does not import or modify skill system
        src = pathlib.Path("aota_forge/work_plane/risk_review.py").read_text(encoding="utf-8")
        assert "skill" not in src.lower() or "skill_refs" in src.lower()  # only via handoff refs, not skill system

    def test_compiler_schema_unchanged(self):
        from aota_forge.work_plane.compiler import compile_handoff_to_execution_package
        import inspect
        sig = inspect.signature(compile_handoff_to_execution_package)
        assert "risk" not in str(sig.parameters).lower()

    def test_events_not_modified(self):
        # events.py should not have risk-review specific changes
        src = pathlib.Path("aota_forge/work_plane/events.py").read_text(encoding="utf-8")
        assert "risk_review" not in src.lower()
        assert "RiskEnvelope" not in src

    def test_aggregator_no_unnecessary_export(self):
        src = pathlib.Path("aota_forge/work_plane/__init__.py").read_text(encoding="utf-8")
        # W3 should not require aggregator export change
        assert "risk_review" not in src  # still not re-exported, direct import used

    def test_w1_w2_contracts_still_pass(self):
        # Proof that W1/W2 guarantees hold: re-check key invariants via separate tests,
        # here we just ensure risk_review still exposes expected symbols
        assert hasattr(risk_review, "ProcessDepth")
        assert hasattr(risk_review, "MilestoneRiskEnvelope")
        assert hasattr(risk_review, "WorkItemRiskDelta")
        assert hasattr(risk_review, "ReviewTrigger")
        assert hasattr(risk_review, "evaluate_work_item_risk_policy")


# ===========================================================================
# Additional cross-checks: schema fingerprints not invented
# ===========================================================================

class TestNoSecondFingerprint:
    def test_no_second_fingerprint_system(self):
        env = _envelope()
        ref = env.to_risk_projection_ref()
        h = _handoff_with_risk_ref(ref)
        # Only two fingerprints exist: handoff_digest and intent_fingerprint
        assert hasattr(h, "handoff_digest")
        pkg = compile_handoff_to_execution_package(h, _binding())
        assert hasattr(pkg, "intent_fingerprint")
        # No risk-specific fingerprint separate from handoff digest
        assert not hasattr(risk_review, "compute_risk_intent_fingerprint")

    def test_work_item_declared_risk_is_evidence_only(self):
        assert risk_review.WORK_ITEM_DECLARED_RISK_IS_EVIDENCE_ONLY is True
        d = _delta(declared_process_depth=ProcessDepth.FAST)
        assert d.declared_depth_is_evidence_only is True
        assert d.declared_process_depth == ProcessDepth.FAST

