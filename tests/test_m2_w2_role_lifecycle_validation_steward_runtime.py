"""M2/W2 One-shot Role Lifecycle, Validation & Steward Dispatch Runtime.

Focused tests A-Y plus negative authority tests, extending (never
replacing) the W1 foundation suite:

A.  task-main logical lifecycle survives one-shot Role completion
B.  analyst/coder/reviewer/steward sessions terminate after Result
C.  ProjectState refresh occurs when stale/missing
D.  fresh ProjectState is reused without duplicate Steward dispatch
E.  Coder test.run available on normal bounded path
F.  Coder bounded self-repair continues without task-main round-trip
G.  Coder cannot accept own Work
H.  Reviewer test.run visible and authorized only under review condition
I.  Reviewer cannot workspace.write product source
J.  Reviewer taxonomy PASS/PASS_WITH_FINDINGS/NEEDS_FIX/BLOCKED-INCONCLUSIVE
K.  Reviewer verifies product-effect evidence, not Coder PASS alone
L.  NEEDS_INPUT may resolve via trusted Role/source without user interrupt
M.  HUMAN_CHECKPOINT requires user decision, cannot auto-select
N.  USER_GATE survives restart and cannot be crossed
O.  BLOCKED cannot be auto-retried as preference problem
P.  Brake scope affected-work/subgraph/milestone behavior
Q.  no-progress retry escalates
R.  Steward Mode A dispatch/result/re-entry
S.  Steward Mode A does not plan Work
T.  Steward Mode B only after closure readiness
U.  Steward cannot set user approval
V.  Steward cannot invent accepted frontier
W.  Steward has no generic Git/GitHub mutation path
X.  all role payloads use the common envelope (no second transport)
Y.  card-first / exact logical re-entry preserved

Negative authority tests are fail-closed throughout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.runtime.task_main.coordinator import MilestonePlanView, activate_milestone, recover_coordinator
from aota_forge.runtime.task_main.coordinator_state import CoordinatorStatus
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane import coder_lifecycle as coder_mod
from aota_forge.work_plane import human_brake as brake_mod
from aota_forge.work_plane import project_state_lifecycle as ps_mod
from aota_forge.work_plane import retry_policy as retry_mod
from aota_forge.work_plane import reviewer_runtime as reviewer_mod
from aota_forge.work_plane import role_lifecycle as lifecycle_mod
from aota_forge.work_plane import steward_dispatch as steward_mod
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.coder_lifecycle import (
    CoderContinuation,
    CoderEscalation,
    CoderRepairAttempt,
    assert_acceptance_expectations_frozen,
    assert_test_modifications_justified,
    evaluate_coder_continuation,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    CODER_CAN_REDEFINE_ACCEPTANCE,
    TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS,
    TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS,
    assert_result_preserves_frozen_expectations,
    frozen_acceptance_expectations,
    frozen_validation_expectations,
)
from aota_forge.work_plane.human_brake import (
    BrakeScope,
    BrakeState,
    CheckpointDecisionContext,
    NeedsInputTriage,
    assert_user_gate_survives_restart,
    brake_halts_work,
    compute_brake_scope,
)
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.milestone_review import ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.project_state_lifecycle import (
    PlanningJoinGate,
    ProjectStateDisposition,
    ProjectStateFreshnessInput,
    evaluate_project_state_need,
    project_state_input_from_coordinator_state,
)
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from aota_forge.work_plane.retry_policy import (
    AttemptEvidence,
    RetryDecisionKind,
    RetryEscalation,
    evaluate_retry,
)
from aota_forge.work_plane.reviewer_runtime import (
    ProductEffectEvidence,
    ReviewVerdict,
    ReviewerReviewPayload,
    parse_review_verdict,
    reviewer_test_run_authorized,
    verify_product_effect,
)
from aota_forge.work_plane.role_lifecycle import (
    OneShotRoleSession,
    OneShotSessionState,
    TaskMainLogicalSession,
    assert_exact_logical_reentry,
    is_long_lived_role,
    is_one_shot_role,
    lifecycle_for_role,
)
from aota_forge.work_plane.role_result import (
    PAYLOAD_KIND_ANALYST_EVIDENCE,
    PAYLOAD_KIND_CODER_IMPLEMENTATION_EVIDENCE,
    PAYLOAD_KIND_PROJECT_STATE_EVIDENCE,
    PAYLOAD_KIND_REVIEWER_REVIEW_EVIDENCE,
    PAYLOAD_KIND_STEWARD_CLOSURE_EVIDENCE,
    SECOND_UNRELATED_RESULT_TRANSPORT_CREATED,
    CommonResultEnvelope,
    RolePayloadRef,
)
from aota_forge.work_plane.roles import AgentWorkRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handoff(role: AgentWorkRole = AgentWorkRole.CODER, scope: str = "work/bounded-only") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="m2-w2-test",
        objective="bounded objective for W2 lifecycle runtime",
        bounded_scope=scope,
        validation_expectations=("output matches",),
        semantic_stop_expectations=("stop on denial",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M2"),
        project_ref=SemanticReference(ref="aota_forge"),
        plan_ref=SemanticReference(ref="wzjcccc-dotcom/aota-hermes-tools#44"),
    )


def _review_handoff(*, with_validation: bool = True) -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.REVIEWER,
        task_kind="m2-w2-review",
        objective="verify bounded implementation against frozen expectations",
        bounded_scope="work/bounded-only review frontier",
        validation_expectations=("output matches",) if with_validation else (),
        semantic_stop_expectations=("stop when verdict emitted",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M2"),
    )


def _card(task_id: str = "t-w2-1", role: AgentWorkRole = AgentWorkRole.CODER) -> WorkerResultCard:
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="hermes",
        result_data={"output": "ok"},
        correlation_id="corr-w2",
    )
    gov = ResultGovernanceProjection.success()
    return project_worker_result_card(result, gov, role, summary="bounded W2 success")


class _FakeAdapter(ExecutorAdapter):
    def capabilities(self) -> ExecutorCapabilities:
        from aota_forge.core.execution.roles import CANONICAL_ROLES

        return ExecutorCapabilities(
            executor_id="m2w2-fake",
            adapter_kind="m2w2_fake_test_double",
            supported_execution_modes=("async",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=f"m2w2-fake||{package.canonical_task_id}||running",
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time="2026-09-09T00:00:00+00:00",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        return CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id="m2w2-fake",
            result_data={"proof": "m2-w2"},
            correlation_id=f"corr-{canonical_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(canonical_task_id=canonical_task_id, cancelled=True, state=CanonicalTaskState.CANCELLED)

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


def _plan_view(*, approved: bool = True) -> MilestonePlanView:
    body = "# [PLAN] W2 fixture\n\n## 1. Current State\n```text\nPLAN_STATUS=in-progress\n```\n"
    doc = normalize_portable_plan(body, source_revision="rev-w2-a")
    digest = portable_plan_digest(doc)
    graph = MilestoneWorkItemGraph(milestone_ref="M2", work_items=["W1", "W2"], dependencies=[["W1", "W2"]])
    return MilestonePlanView(
        plan_authority="wzjcccc-dotcom/aota-hermes-tools#44",
        plan_digest=digest,
        milestone_id="M2",
        entry_base="1473a1ec30daab1422d35e54b4f7a9d9fff74ea7",
        graph=graph,
        milestone_user_approval_satisfied=approved,
        plan_source_revision=doc.source_revision,
    )


def _dispatcher() -> ExecutionDispatcher:
    from aota_forge.core.execution.durable_state import OriginSessionRef

    store = InMemoryExecutionStateStore()
    registry = ExecutorRegistry()
    registry.register(_FakeAdapter())
    return ExecutionDispatcher(registry, state_store=store, origin_session_ref=OriginSessionRef(value="sess-w2-test"))


def _ready_readiness() -> MilestoneClosureReadiness:
    return MilestoneClosureReadiness(
        milestone_ref=SemanticReference(ref="M2"),
        ready_for_project_steward=True,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref="frontier-w2"),
    )


def _not_ready_readiness() -> MilestoneClosureReadiness:
    return MilestoneClosureReadiness(
        milestone_ref=SemanticReference(ref="M2"),
        ready_for_project_steward=False,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref="frontier-w2"),
        blocking_reasons=("review incomplete",),
    )


# ---------------------------------------------------------------------------
# A. task-main logical lifecycle survives one-shot Role completion
# ---------------------------------------------------------------------------


class TestALogicalLifecycle:
    def test_lifecycle_kinds_frozen(self) -> None:
        assert lifecycle_for_role(AgentWorkRole.TASK_MAIN).value == "LONG_LIVED_LOGICAL"
        for role in (AgentWorkRole.ANALYST, AgentWorkRole.CODER, AgentWorkRole.REVIEWER, AgentWorkRole.PROJECT_STEWARD):
            assert lifecycle_for_role(role).value == "ONE_SHOT"
        assert is_long_lived_role("task-main") and not is_one_shot_role("task-main")
        assert lifecycle_mod.TASK_MAIN_LONG_LIVED_LOGICAL_LIFECYCLE_WIRED is True
        assert lifecycle_mod.LIFETIME_IS_SESSION_TIMEOUT is False
        assert lifecycle_mod.LIFETIME_IS_ORCHESTRATION_SEMANTICS is True

    def test_logical_session_survives_terminated_one_shot(self) -> None:
        logical = TaskMainLogicalSession(logical_session_ref="sess-logical-1", milestone_id="M2")
        session = OneShotRoleSession(
            role=AgentWorkRole.CODER, handoff_digest="h" * 64, task_main_logical_session_ref="sess-logical-1"
        )
        session = session.advance(OneShotSessionState.EXECUTING)
        session = session.advance(OneShotSessionState.RESULT_RECORDED, result_digest="c" * 64)
        session = session.advance(OneShotSessionState.TERMINATED)
        assert logical.survives_worker_completion(session) is True
        # Non-terminated session does not count as survived completion.
        open_session = OneShotRoleSession(
            role=AgentWorkRole.CODER, handoff_digest="h" * 64, task_main_logical_session_ref="sess-logical-1"
        )
        assert logical.survives_worker_completion(open_session) is False

    def test_coordinator_durable_truth_survives_worker_completion(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        coord = activate_milestone(
            store=cstore, plan_view=_plan_view(), origin_task_main_session_ref="sess-logical-1",
            execution_dispatcher=_dispatcher(), executor_id="hermes", project_id="aota_forge",
        )
        snap = coord.durable_snapshot()
        assert snap["status"] == "ACTIVE"
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get(coord.coordinator_id)
        assert loaded is not None and loaded.origin_task_main_session_ref == "sess-logical-1"
        reopened.close()


# ---------------------------------------------------------------------------
# B. one-shot sessions terminate after Result
# ---------------------------------------------------------------------------


class TestBOneShotTermination:
    @pytest.mark.parametrize("role", ["analyst", "coder", "reviewer", "project-steward"])
    def test_one_shot_terminates_after_result(self, role: str) -> None:
        session = OneShotRoleSession(
            role=role, handoff_digest="h" * 64, task_main_logical_session_ref="sess-1"
        )
        assert session.state is OneShotSessionState.DISPATCHED
        session = session.advance("EXECUTING")
        session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
        terminated = session.advance("TERMINATED")
        assert terminated.state is OneShotSessionState.TERMINATED
        assert lifecycle_mod.ONE_SHOT_ROLE_PERSISTS_AFTER_RESULT is False

    def test_task_main_cannot_be_one_shot_session(self) -> None:
        with pytest.raises(ValueError):
            OneShotRoleSession(
                role=AgentWorkRole.TASK_MAIN, handoff_digest="h" * 64, task_main_logical_session_ref="sess-1"
            )

    def test_terminated_session_cannot_resurrect(self) -> None:
        session = OneShotRoleSession(
            role="coder", handoff_digest="h" * 64, task_main_logical_session_ref="sess-1"
        )
        session = session.advance("EXECUTING")
        session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
        terminated = session.advance("TERMINATED")
        with pytest.raises(ValueError):
            terminated.advance("EXECUTING")
        with pytest.raises(ValueError):
            terminated.advance("DISPATCHED")

    def test_result_recorded_requires_card_digest(self) -> None:
        session = OneShotRoleSession(
            role="coder", handoff_digest="h" * 64, task_main_logical_session_ref="sess-1"
        ).advance("EXECUTING")
        with pytest.raises(ValueError):
            session.advance("RESULT_RECORDED")

    def test_skip_executing_denied(self) -> None:
        session = OneShotRoleSession(
            role="coder", handoff_digest="h" * 64, task_main_logical_session_ref="sess-1"
        )
        with pytest.raises(ValueError):
            session.advance("RESULT_RECORDED", result_digest="c" * 64)


# ---------------------------------------------------------------------------
# C/D. ProjectState conditional refresh / reuse + JOIN gate
# ---------------------------------------------------------------------------


class TestCProjectStateRefresh:
    def test_missing_evidence_refreshes(self) -> None:
        evaluation = evaluate_project_state_need(ProjectStateFreshnessInput(has_prior_ref=False))
        assert evaluation.disposition is ProjectStateDisposition.REFRESH

    @pytest.mark.parametrize(
        "field",
        [
            "new_logical_session",
            "new_project",
            "identity_ambiguous",
            "evidence_stale",
            "external_state_changed",
            "continuity_uncertain",
            "binding_unresolved",
        ],
    )
    def test_each_trigger_refreshes(self, field: str) -> None:
        observed = ProjectStateFreshnessInput(has_prior_ref=True, prior_sufficient=True, **{field: True})
        evaluation = evaluate_project_state_need(observed, prior_ref="ps-1")
        assert evaluation.disposition is ProjectStateDisposition.REFRESH
        assert evaluation.reason is not None

    def test_markers(self) -> None:
        assert ps_mod.PROJECT_STATE_CONDITIONAL_REFRESH_WIRED is True
        assert ps_mod.PROJECT_STATE_REUSE_WIRED is True
        assert ps_mod.DUPLICATE_STEWARD_STATE_INSPECTION_REQUIRED is False
        assert ps_mod.PROJECT_STATE_REFRESH_EVERY_MILESTONE is False
        assert ps_mod.NEW_PROJECT_STATE_STORE_CREATED is False


class TestDProjectStateReuse:
    def test_fresh_sufficient_prior_reuses(self) -> None:
        evaluation = evaluate_project_state_need(
            ProjectStateFreshnessInput(has_prior_ref=True, prior_sufficient=True), prior_ref="ps-1"
        )
        assert evaluation.disposition is ProjectStateDisposition.REUSE
        assert evaluation.prior_ref == "ps-1"

    def test_coordinator_fresh_state_reuses(self) -> None:
        observed = project_state_input_from_coordinator_state(
            {"project_state": {"ref": "ps-1", "freshness": "fresh", "status": "sufficient"}}
        )
        assert evaluate_project_state_need(observed, prior_ref="ps-1").disposition is ProjectStateDisposition.REUSE

    def test_coordinator_missing_state_refreshes(self) -> None:
        observed = project_state_input_from_coordinator_state({"project_state": None})
        assert evaluate_project_state_need(observed).disposition is ProjectStateDisposition.REFRESH

    def test_join_gate_requires_project_state(self) -> None:
        gate = PlanningJoinGate(preliminary_planning_done=True, required_project_state_satisfied=False)
        assert gate.final_dag_allowed is False
        assert gate.worker_dispatch_allowed is False
        with pytest.raises(ValueError):
            gate.assert_final_dag_allowed()
        with pytest.raises(ValueError):
            gate.assert_worker_dispatch_allowed()
        assert ps_mod.WORKER_DISPATCH_BEFORE_REQUIRED_PROJECT_STATE is False

    def test_join_gate_opens_when_state_satisfied(self) -> None:
        gate = PlanningJoinGate(preliminary_planning_done=True, required_project_state_satisfied=True)
        gate.assert_final_dag_allowed()
        gate.assert_worker_dispatch_allowed()


# ---------------------------------------------------------------------------
# E/F/G. Coder validation runtime
# ---------------------------------------------------------------------------


class TestECoderTestRun:
    def test_coder_test_run_normal_path(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-e",
            canonical_task_id="t-e", handoff=_handoff(AgentWorkRole.CODER),
        )
        assert "test.run" in binding.tool_surface.all_capability_names()
        assert binding.test_execution_authority is not None
        assert coder_mod.CODER_SELF_VALIDATION_RUNTIME_WIRED is True

    def test_coder_workspace_tools_bounded(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-e2",
            canonical_task_id="t-e2", handoff=_handoff(AgentWorkRole.CODER),
        )
        assert binding.mutation_authority is not None
        assert binding.restricted_shell_authority is not None
        # No unrestricted shell: authority is the bounded restricted provider.
        assert type(binding.restricted_shell_authority).__name__ != "UnrestrictedShell"


class TestFSelfRepair:
    def test_bounded_repair_continues_in_session(self) -> None:
        decision = evaluate_coder_continuation(
            [CoderRepairAttempt(failure_class="VALIDATION_FAILURE", new_evidence=True)]
        )
        assert decision.continuation is CoderContinuation.CONTINUE_SELF_REPAIR
        assert coder_mod.BOUNDED_SELF_REPAIR_ALLOWED is True
        assert coder_mod.UNBOUNDED_REPAIR_LOOP_ALLOWED is False

    def test_changed_hypothesis_continues(self) -> None:
        decision = evaluate_coder_continuation(
            [CoderRepairAttempt(failure_class="TIMEOUT", changed_hypothesis=True, changed_approach=True)]
        )
        assert decision.continuation is CoderContinuation.CONTINUE_SELF_REPAIR

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"scope_expansion_needed": True},
            {"architecture_decision_needed": True},
            {"authority_expansion_needed": True},
            {"acceptance_redefinition_needed": True},
            {"material_ambiguity": True},
        ],
    )
    def test_stop_conditions_escalate(self, kwargs: dict) -> None:
        decision = evaluate_coder_continuation([], **kwargs)
        assert decision.continuation is CoderContinuation.STOP_ESCALATE
        assert decision.escalation is not None

    def test_repeated_same_class_no_progress_escalates(self) -> None:
        attempts = [
            CoderRepairAttempt(failure_class="VALIDATION_FAILURE"),
            CoderRepairAttempt(failure_class="VALIDATION_FAILURE"),
        ]
        decision = evaluate_coder_continuation(attempts)
        assert decision.continuation is CoderContinuation.STOP_ESCALATE
        assert decision.escalation is CoderEscalation.REPEATED_FAILURE_NO_PROGRESS


class TestGCoderCannotAccept:
    def test_coder_cannot_accept_own_work(self) -> None:
        assert coder_mod.CODER_CAN_ACCEPT_OWN_WORK is False
        assert coder_mod.CODER_SELF_VALIDATION_IS_ACCEPTANCE_AUTHORITY is False

    def test_reviewer_is_not_coder(self) -> None:
        assert AgentWorkRole.REVIEWER != AgentWorkRole.CODER


# ---------------------------------------------------------------------------
# Firewall: frozen acceptance/validation + justified test mods
# ---------------------------------------------------------------------------


class TestFirewall:
    def test_handoff_freezes_expectations(self) -> None:
        assert TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS is True
        assert TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS is True
        assert CODER_CAN_REDEFINE_ACCEPTANCE is False
        handoff = _handoff()
        assert frozen_validation_expectations(handoff) == ("output matches",)
        assert "bounded objective for W2 lifecycle runtime" in frozen_acceptance_expectations(handoff)[0]

    def test_exact_expectations_pass(self) -> None:
        handoff = _handoff()
        assert_result_preserves_frozen_expectations(
            handoff=handoff,
            claimed_acceptance=list(frozen_acceptance_expectations(handoff)),
            claimed_validation=["output matches"],
        )

    def test_weakened_validation_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            assert_result_preserves_frozen_expectations(
                handoff=_handoff(), claimed_acceptance=list(frozen_acceptance_expectations(_handoff())),
                claimed_validation=[],
            )

    def test_redefined_acceptance_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            assert_result_preserves_frozen_expectations(
                handoff=_handoff(), claimed_acceptance=["redefined objective"],
                claimed_validation=["output matches"],
            )

    def test_direct_firewall_api(self) -> None:
        with pytest.raises(ValueError):
            assert_acceptance_expectations_frozen(
                handoff_acceptance=("a",), handoff_validation=("v",),
                claimed_acceptance=("a", "extra"), claimed_validation=("v",),
            )

    def test_test_mod_requires_justification(self) -> None:
        record = coder_mod.TestModificationRecord(
            test_path="tests/test_x.py", kind=coder_mod.TestModificationKind.UPDATE,
            justification="assertion corrected to frozen expectation", evidence_ref="ev-1",
        )
        assert_test_modifications_justified([record])
        with pytest.raises(ValueError):
            coder_mod.TestModificationRecord(
                test_path="tests/test_x.py", kind="UPDATE", justification="  ", evidence_ref="ev-1",
            )

    def test_bare_mapping_without_justification_fails(self) -> None:
        with pytest.raises(ValueError):
            assert_test_modifications_justified([{"test_path": "t.py", "kind": "UPDATE"}])


# ---------------------------------------------------------------------------
# H/I/J/K. Reviewer independence
# ---------------------------------------------------------------------------


class TestHReviewerTestRun:
    def test_reviewer_surface_exposes_test_run(self) -> None:
        surface = get_tool_surface_for_role("reviewer")
        assert "test.run" in surface.all_capability_names()
        assert reviewer_mod.REVIEWER_TEST_RUN_POLICY == "eager_visible+conditionally_authorized"
        assert reviewer_mod.REVIEWER_CAN_USE_VALIDATION_TOOL is True
        assert reviewer_mod.REVIEWER_TEST_RUN_EVERY_REVIEW_REQUIRED is False

    def test_reviewer_authorized_when_review_requires_evidence(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-h",
            canonical_task_id="t-h", handoff=_review_handoff(with_validation=True),
        )
        assert "test.run" in binding.tool_surface.all_capability_names()
        assert binding.test_execution_authority is not None

    def test_reviewer_denied_without_review_evidence_need(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-h2",
            canonical_task_id="t-h2", handoff=_review_handoff(with_validation=False),
        )
        assert binding.test_execution_authority is None

    def test_policy_function(self) -> None:
        assert reviewer_test_run_authorized(work_role="reviewer", validation_expectations=("v",)) is True
        assert reviewer_test_run_authorized(work_role="reviewer", validation_expectations=()) is False
        assert reviewer_test_run_authorized(work_role="coder", validation_expectations=("v",)) is False


class TestIReviewerCannotMutate:
    def test_reviewer_has_no_mutation_authority(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-i",
            canonical_task_id="t-i", handoff=_review_handoff(),
        )
        assert binding.mutation_authority is None
        assert reviewer_mod.REVIEWER_CAN_MUTATE_PRODUCT_SOURCE is False
        assert reviewer_mod.REVIEWER_WORKSPACE_WRITE_PRODUCT_SOURCE is False
        assert reviewer_mod.REVIEWER_UNRESTRICTED_SHELL is False
        assert binding.restricted_shell_authority is None


class TestJReviewTaxonomy:
    def test_all_verdicts_parse(self) -> None:
        for raw in ("PASS", "PASS_WITH_FINDINGS", "NEEDS_FIX", "BLOCKED", "INCONCLUSIVE"):
            assert parse_review_verdict(raw).value == raw
        assert reviewer_mod.REVIEW_RESULT_TAXONOMY_WIRED is True

    def test_unknown_verdict_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            parse_review_verdict("APPROVED")

    def test_taxonomy_maps_to_canonical_finding_classes(self) -> None:
        assert reviewer_mod.VERDICT_TO_FINDING_CLASS["PASS"] is None
        assert reviewer_mod.VERDICT_TO_FINDING_CLASS["PASS_WITH_FINDINGS"] == "NON_BLOCKING"
        assert reviewer_mod.VERDICT_TO_FINDING_CLASS["NEEDS_FIX"] == "BLOCKING"

    def test_payload_taxonomy_coherence(self) -> None:
        payload = ReviewerReviewPayload(
            verdict=ReviewVerdict.PASS_WITH_FINDINGS, reviewed_handoff_digest="h" * 64,
            finding_refs=("finding-1",), independent_validation_performed=True,
        )
        assert payload.finding_class() == "NON_BLOCKING"
        with pytest.raises(ValueError):
            ReviewerReviewPayload(verdict=ReviewVerdict.PASS, reviewed_handoff_digest="h" * 64, finding_refs=("f",))
        with pytest.raises(ValueError):
            ReviewerReviewPayload(verdict=ReviewVerdict.NEEDS_FIX, reviewed_handoff_digest="h" * 64)

    def test_reviewer_is_not_final_authority(self) -> None:
        assert reviewer_mod.REVIEWER_IS_FINAL_ACCEPTANCE_AUTHORITY is False
        assert reviewer_mod.REVIEW_PASS_AUTOMATICALLY_CLOSES_MILESTONE is False


class TestKProductEffect:
    def test_coder_pass_alone_proves_nothing(self) -> None:
        assert reviewer_mod.CODER_RESULT_PASS_IS_NOT_REVIEWER_PROOF is True
        evidence = ProductEffectEvidence(
            handoff_acceptance_expectations=("output matches",),
            coder_result_ref="card-1", coder_claimed_pass=True,
            repo_delta_refs=(), test_evidence_refs=("test-ev",),
        )
        verdict = verify_product_effect(evidence, satisfied_expectations=("output matches",))
        assert verdict.verdict is ReviewVerdict.NEEDS_FIX

    def test_full_evidence_passes(self) -> None:
        evidence = ProductEffectEvidence(
            handoff_acceptance_expectations=("output matches",),
            coder_result_ref="card-1", coder_claimed_pass=True,
            repo_delta_refs=("delta-1",), test_evidence_refs=("test-ev",),
            negative_behavior_evidence_refs=("neg-1",),
        )
        verdict = verify_product_effect(evidence, satisfied_expectations=("output matches",))
        assert verdict.verdict is ReviewVerdict.PASS
        assert reviewer_mod.REVIEWER_MUST_VERIFY_PRODUCT_EFFECT is True

    def test_unsatisfied_expectation_needs_fix(self) -> None:
        evidence = ProductEffectEvidence(
            handoff_acceptance_expectations=("a", "b"), coder_result_ref="card-1",
            repo_delta_refs=("delta-1",), test_evidence_refs=("test-ev",),
        )
        verdict = verify_product_effect(evidence, satisfied_expectations=("a",), unsatisfied_expectations=("b",))
        assert verdict.verdict is ReviewVerdict.NEEDS_FIX

    def test_missing_artifact_blocked(self) -> None:
        verdict = verify_product_effect(ProductEffectEvidence(missing_artifact=True))
        assert verdict.verdict is ReviewVerdict.BLOCKED

    def test_inconclusive_when_evidence_insufficient(self) -> None:
        verdict = verify_product_effect(ProductEffectEvidence(), review_inconclusive=True)
        assert verdict.verdict is ReviewVerdict.INCONCLUSIVE


# ---------------------------------------------------------------------------
# L/M/N/O/P. Human Brake progression + scope
# ---------------------------------------------------------------------------


class TestLNeedsInput:
    def test_trusted_source_avoids_user_interrupt(self) -> None:
        triage = NeedsInputTriage(resolvable_by_trusted_source=True)
        assert triage.user_interrupt_required is False
        assert triage.resolution_lane == "TRUSTED_SOURCE"

    def test_analyst_lane_avoids_user_interrupt(self) -> None:
        triage = NeedsInputTriage(resolvable_by_analyst=True)
        assert triage.user_interrupt_required is False
        assert triage.resolution_lane == "ANALYST"

    def test_steward_lane_avoids_user_interrupt(self) -> None:
        triage = NeedsInputTriage(resolvable_by_steward=True)
        assert triage.user_interrupt_required is False

    def test_user_owned_fact_interrupts(self) -> None:
        triage = NeedsInputTriage()
        assert triage.user_interrupt_required is True
        assert triage.resolution_lane == "USER"


class TestMCheckpoint:
    def test_checkpoint_context_bounded(self) -> None:
        context = CheckpointDecisionContext(
            decision="choose repair strategy",
            reason="two materially different fixes",
            options=("patch in place", "revert and redo"),
            material_consequence="revert costs one Work cycle",
            recommendation="patch in place",
            resume_condition="user selects an option",
        )
        assert len(context.options) == 2
        assert context.to_dict()["recommendation"] == "patch in place"

    def test_checkpoint_options_capped_at_three(self) -> None:
        with pytest.raises(ValueError):
            CheckpointDecisionContext(decision="d", reason="r", options=("a", "b", "c", "d"))

    def test_task_main_cannot_auto_select(self) -> None:
        assert brake_mod.TASK_MAIN_CAN_AUTO_SELECT_WHEN_CHECKPOINT_REQUIRED is False
        context = CheckpointDecisionContext(decision="d", reason="r", options=("a",))
        with pytest.raises(ValueError):
            context.auto_select()


class TestNUserGate:
    def test_gate_blocks_dispatch_and_survives_recovery(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        coord = activate_milestone(
            store=cstore, plan_view=_plan_view(approved=False),
            origin_task_main_session_ref="sess-n", execution_dispatcher=_dispatcher(),
            executor_id="hermes", project_id="aota_forge",
        )
        assert coord.state.status is CoordinatorStatus.USER_GATE_REQUIRED
        report = coord.dispatch_ready(lambda _wi: _handoff(), live_plan_view=_plan_view(approved=False))
        assert report.physical_dispatch_attempts == 0
        assert brake_mod.TASK_MAIN_CAN_SET_USER_APPROVAL is False
        assert brake_mod.TASK_MAIN_CAN_RETRY_THROUGH_USER_GATE is False
        # Restart cannot cross the gate: approval arrives only as trusted input.
        recovered = recover_coordinator(
            store=cstore, coordinator_id=coord.coordinator_id, live_plan_view=_plan_view(approved=False),
            execution_dispatcher=_dispatcher(), session_available=True,
        )
        assert recovered.state.status is CoordinatorStatus.USER_GATE_REQUIRED
        approved = recover_coordinator(
            store=cstore, coordinator_id=coord.coordinator_id, live_plan_view=_plan_view(approved=True),
            execution_dispatcher=_dispatcher(), session_available=True,
        )
        assert approved.state.status is CoordinatorStatus.ACTIVE
        cstore.close()

    def test_gate_transition_guard(self) -> None:
        with pytest.raises(ValueError):
            assert_user_gate_survives_restart(status_before="USER_GATE_REQUIRED", status_after="ACTIVE")


class TestOBlocked:
    def test_blocked_cannot_auto_retry_as_preference(self) -> None:
        assert brake_mod.BLOCKED_AUTO_RETRY_AS_PREFERENCE_DENIED is True
        decision = evaluate_retry(
            [AttemptEvidence(attempt=1, failure_class="UNKNOWN")],
            AttemptEvidence(attempt=2, failure_class="UNKNOWN"),
            default_escalation=RetryEscalation.BLOCKED,
        )
        assert decision.kind is RetryDecisionKind.ESCALATE
        assert decision.escalation is RetryEscalation.BLOCKED


class TestPBrakeScope:
    def _graph(self) -> MilestoneWorkItemGraph:
        return MilestoneWorkItemGraph(
            milestone_ref="M2", work_items=["W1", "W2", "W3"], dependencies=[["W1", "W2"]]
        )

    def test_isolated_work_stays_narrow(self) -> None:
        scope = compute_brake_scope(self._graph(), ["W3"])
        assert scope is BrakeScope.AFFECTED_WORK
        assert brake_halts_work(scope=scope, work_item_id="W3", affected_work=["W3"]) is True
        assert brake_halts_work(scope=scope, work_item_id="W1", affected_work=["W3"]) is False
        assert brake_mod.HUMAN_BRAKE_DOES_NOT_ALWAYS_STOP_WHOLE_MILESTONE is True

    def test_dependent_subgraph_reaches(self) -> None:
        scope = compute_brake_scope(self._graph(), ["W1"])
        assert scope is BrakeScope.DEPENDENT_SUBGRAPH

    def test_architecture_impact_stops_milestone(self) -> None:
        scope = compute_brake_scope(self._graph(), ["W3"], architecture_or_acceptance_impact=True)
        assert scope is BrakeScope.WHOLE_MILESTONE
        assert brake_halts_work(scope=scope, work_item_id="W1", affected_work=["W3"]) is True

    def test_shared_state_impact_stops_milestone(self) -> None:
        scope = compute_brake_scope(self._graph(), ["W3"], shared_state_impact=True)
        assert scope is BrakeScope.WHOLE_MILESTONE

    def test_empty_affected_is_none(self) -> None:
        assert compute_brake_scope(self._graph(), []) is BrakeScope.NONE

    def test_unknown_work_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            compute_brake_scope(self._graph(), ["W9"])

    def test_scope_runtime_wired(self) -> None:
        assert brake_mod.BRAKE_SCOPE_RUNTIME_WIRED is True


# ---------------------------------------------------------------------------
# Q. retry / no-progress
# ---------------------------------------------------------------------------


class TestQRetry:
    def test_progress_retry_allowed(self) -> None:
        decision = evaluate_retry(
            [AttemptEvidence(attempt=1, failure_class="TIMEOUT")],
            AttemptEvidence(attempt=2, failure_class="TIMEOUT", new_evidence=True),
        )
        assert decision.kind is RetryDecisionKind.RETRY
        assert retry_mod.RETRY_REQUIRES_PROGRESS_RATIONALE is True
        assert retry_mod.NO_PROGRESS_RETRY_ALLOWED is False

    def test_no_progress_retry_denied(self) -> None:
        decision = evaluate_retry(
            [AttemptEvidence(attempt=1, failure_class="TIMEOUT")],
            AttemptEvidence(attempt=2, failure_class="TIMEOUT"),
        )
        assert decision.kind is RetryDecisionKind.ESCALATE

    def test_repeated_no_progress_escalates(self) -> None:
        assert retry_mod.REPEATED_NO_PROGRESS_REQUIRES_ESCALATION is True
        decision = evaluate_retry(
            [
                AttemptEvidence(attempt=1, failure_class="VALIDATION_FAILURE"),
                AttemptEvidence(attempt=2, failure_class="VALIDATION_FAILURE"),
            ],
            AttemptEvidence(attempt=3, failure_class="VALIDATION_FAILURE", progress_rationale="will try harder"),
        )
        assert decision.kind is RetryDecisionKind.ESCALATE
        assert decision.escalation is RetryEscalation.REVIEW_REQUIRED

    def test_no_universal_numeric_count(self) -> None:
        assert retry_mod.UNIVERSAL_NUMERIC_RETRY_COUNT_FROZEN is False


# ---------------------------------------------------------------------------
# R/S/T/U/V/W. Steward dispatch
# ---------------------------------------------------------------------------


class TestRModeA:
    def test_mode_a_handoff_bounded(self) -> None:
        handoff = steward_mod.build_mode_a_handoff(
            objective="inspect project continuity",
            bounded_scope="project-state inspection only",
            project_ref="aota_forge",
        )
        assert handoff.work_role is AgentWorkRole.PROJECT_STEWARD
        assert handoff.task_kind == steward_mod.STEWARD_TASK_KIND_MODE_A
        assert steward_mod.PROJECT_STEWARD_MODE_A_DISPATCH_WIRED is True

    def test_mode_a_result_compact_round_trip(self) -> None:
        payload = steward_mod.ProjectStatePayload(
            project_id="aota_forge", binding_ref="bind-1", existence="RESOLVED",
            accepted_frontier_ref="abc123", freshness="fresh",
        )
        restored = steward_mod.ProjectStatePayload.from_dict(payload.to_dict())
        assert restored.project_id == "aota_forge"
        assert restored.freshness == "fresh"
        assert steward_mod.PROJECT_STATE_RESULT_IS_COMPACT is True

    def test_mode_a_reentry_exact(self) -> None:
        logical = TaskMainLogicalSession(logical_session_ref="sess-r", milestone_id="M2")
        session = OneShotRoleSession(
            role="project-steward", handoff_digest="h" * 64, task_main_logical_session_ref="sess-r"
        )
        session = session.advance("EXECUTING")
        session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
        terminated = session.advance("TERMINATED")
        assert_exact_logical_reentry(logical_session=logical, session=terminated, result_card_digest="c" * 64)


class TestSModeANoPlanning:
    def test_mode_a_plans_no_work(self) -> None:
        assert steward_mod.PROJECT_STEWARD_PROJECT_STATE_MODE_PLANS_WORK is False
        handoff = steward_mod.build_mode_a_handoff(objective="o", bounded_scope="s")
        stops = " ".join(handoff.semantic_stop_expectations).lower()
        assert "do not plan" in stops


class TestTModeB:
    def test_mode_b_requires_readiness(self) -> None:
        handoff = steward_mod.build_mode_b_handoff(
            readiness=_ready_readiness(), objective="reconcile governance sync", bounded_scope="closure scope only"
        )
        assert handoff.work_role is AgentWorkRole.PROJECT_STEWARD
        assert handoff.task_kind == steward_mod.STEWARD_TASK_KIND_MODE_B
        assert steward_mod.PROJECT_STEWARD_MODE_B_DISPATCH_WIRED is True

    def test_mode_b_denied_before_readiness(self) -> None:
        with pytest.raises(ValueError):
            steward_mod.build_mode_b_handoff(
                readiness=_not_ready_readiness(), objective="o", bounded_scope="s"
            )

    def test_normal_work_item_needs_no_steward(self) -> None:
        assert steward_mod.NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT is False


class TestUStewardCannotSetApproval:
    def test_steward_result_cannot_set_approval(self) -> None:
        assert steward_mod.PROJECT_STEWARD_CAN_SET_USER_APPROVAL is False
        with pytest.raises(ValueError):
            steward_mod.StewardResult(
                milestone_ref="M2", reviewed_frontier_ref="f-1",
                verdict=steward_mod.StewardClosureVerdict.GOVERNANCE_SYNCED, user_approval_set=True,
            )

    def test_synced_result_round_trip(self) -> None:
        result = steward_mod.StewardResult(
            milestone_ref="M2", reviewed_frontier_ref="f-1",
            verdict="GOVERNANCE_SYNCED", governance_evidence_refs=("f-1",),
        )
        restored = steward_mod.StewardResult.from_dict(result.to_dict())
        assert restored.verdict is steward_mod.StewardClosureVerdict.GOVERNANCE_SYNCED
        assert steward_mod.STEWARD_RESULT_SEAM_WIRED is True

    def test_blocked_result_requires_reasons(self) -> None:
        with pytest.raises(ValueError):
            steward_mod.StewardResult(
                milestone_ref="M2", reviewed_frontier_ref="f-1", verdict="GOVERNANCE_BLOCKED"
            )


class TestVStewardCannotInventFrontier:
    def test_accepted_frontier_must_equal_reviewed(self) -> None:
        assert steward_mod.PROJECT_STEWARD_CAN_INVENT_ACCEPTED_FRONTIER is False
        with pytest.raises(ValueError):
            steward_mod.StewardResult(
                milestone_ref="M2", reviewed_frontier_ref="f-1",
                verdict="GOVERNANCE_SYNCED", accepted_frontier_ref="invented-frontier",
            )
        ok_result = steward_mod.StewardResult(
            milestone_ref="M2", reviewed_frontier_ref="f-1",
            verdict="GOVERNANCE_SYNCED", accepted_frontier_ref="f-1",
        )
        assert ok_result.accepted_frontier_ref == "f-1"

    def test_finalizer_cannot_exceed_result_scope(self) -> None:
        assert steward_mod.FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE is False
        result = steward_mod.StewardResult(
            milestone_ref="M2", reviewed_frontier_ref="f-1",
            verdict="GOVERNANCE_SYNCED", governance_evidence_refs=("f-1",),
        )
        result.assert_finalizer_scope(operation_refs=["f-1"])
        with pytest.raises(ValueError):
            result.assert_finalizer_scope(operation_refs=["outside-scope-op"])


class TestWStewardNoGenericMutation:
    def test_no_git_github_shell_markers(self) -> None:
        assert steward_mod.PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION is False
        assert steward_mod.PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION is False
        assert steward_mod.PROJECT_STEWARD_UNRESTRICTED_SHELL is False
        assert steward_mod.STEWARD_MUTATION_ARCHITECTURE == "OPTION_B"

    def test_steward_binding_has_no_mutation_or_shell(self, tmp_path: Path) -> None:
        binding = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-w",
            canonical_task_id="t-w",
            handoff=steward_mod.build_mode_a_handoff(objective="o", bounded_scope="s"),
        )
        assert binding.mutation_authority is None
        assert binding.restricted_shell_authority is None
        assert binding.test_execution_authority is None


# ---------------------------------------------------------------------------
# X. role payloads in the common envelope
# ---------------------------------------------------------------------------


class TestXRolePayloads:
    def _envelope(self, role: AgentWorkRole, task_id: str, kind: str) -> CommonResultEnvelope:
        card = _card(task_id, role)
        return CommonResultEnvelope.from_worker_result_card(
            card, role_payload=RolePayloadRef(kind=kind, ref=f"payload-{task_id}", digest="d" * 64)
        )

    def test_all_five_kinds_integrated(self) -> None:
        cases = [
            (AgentWorkRole.ANALYST, "t-x-a", PAYLOAD_KIND_ANALYST_EVIDENCE),
            (AgentWorkRole.CODER, "t-x-c", PAYLOAD_KIND_CODER_IMPLEMENTATION_EVIDENCE),
            (AgentWorkRole.REVIEWER, "t-x-r", PAYLOAD_KIND_REVIEWER_REVIEW_EVIDENCE),
            (AgentWorkRole.PROJECT_STEWARD, "t-x-sa", PAYLOAD_KIND_PROJECT_STATE_EVIDENCE),
            (AgentWorkRole.PROJECT_STEWARD, "t-x-sb", PAYLOAD_KIND_STEWARD_CLOSURE_EVIDENCE),
        ]
        for role, task_id, kind in cases:
            envelope = self._envelope(role, task_id, kind)
            assert envelope.role_payload is not None and envelope.role_payload.kind == kind
            restored = CommonResultEnvelope.from_dict(envelope.to_dict())
            assert restored.role_payload is not None and restored.role_payload.kind == kind
            assert restored.envelope_digest == envelope.envelope_digest

    def test_no_second_transport(self) -> None:
        assert SECOND_UNRELATED_RESULT_TRANSPORT_CREATED is False
        assert steward_mod.SECOND_RESULT_TRANSPORT_CREATED is False

    def test_typed_bodies_round_trip(self) -> None:
        analyst = steward_mod.AnalystEvidencePayload(summary="bounded findings", finding_refs=("f-1",))
        assert steward_mod.AnalystEvidencePayload.from_dict(analyst.to_dict()).summary == "bounded findings"
        coder = steward_mod.CoderImplementationPayload(summary="implemented", validation_performed=("tests",))
        assert steward_mod.CoderImplementationPayload.from_dict(coder.to_dict()).summary == "implemented"
        project_state = steward_mod.ProjectStatePayload(
            project_id="p", binding_ref="b", existence="RESOLVED"
        )
        assert steward_mod.ProjectStatePayload.from_dict(project_state.to_dict()).existence == "RESOLVED"

    def test_unknown_payload_kind_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            RolePayloadRef(kind="second_transport_payload", ref="x")


# ---------------------------------------------------------------------------
# Y. card-first / exact logical re-entry
# ---------------------------------------------------------------------------


class TestYReentry:
    def test_exact_reentry_all_roles(self) -> None:
        logical = TaskMainLogicalSession(logical_session_ref="sess-y", milestone_id="M2")
        for role in ("analyst", "coder", "reviewer", "project-steward"):
            session = OneShotRoleSession(role=role, handoff_digest="h" * 64, task_main_logical_session_ref="sess-y")
            session = session.advance("EXECUTING")
            session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
            terminated = session.advance("TERMINATED")
            assert_exact_logical_reentry(
                logical_session=logical, session=terminated, result_card_digest="c" * 64
            )

    def test_foreign_session_reentry_denied(self) -> None:
        logical = TaskMainLogicalSession(logical_session_ref="sess-y", milestone_id="M2")
        session = OneShotRoleSession(role="coder", handoff_digest="h" * 64, task_main_logical_session_ref="sess-other")
        session = session.advance("EXECUTING")
        session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
        terminated = session.advance("TERMINATED")
        with pytest.raises(ValueError):
            assert_exact_logical_reentry(
                logical_session=logical, session=terminated, result_card_digest="c" * 64
            )

    def test_digest_mismatch_denied(self) -> None:
        logical = TaskMainLogicalSession(logical_session_ref="sess-y", milestone_id="M2")
        session = OneShotRoleSession(role="coder", handoff_digest="h" * 64, task_main_logical_session_ref="sess-y")
        session = session.advance("EXECUTING")
        session = session.advance("RESULT_RECORDED", result_digest="c" * 64)
        terminated = session.advance("TERMINATED")
        with pytest.raises(ValueError):
            assert_exact_logical_reentry(
                logical_session=logical, session=terminated, result_card_digest="x" * 64
            )

    def test_card_first_markers(self) -> None:
        assert lifecycle_mod.RAW_ROLE_TRANSCRIPT_REQUIRED_BY_TASK_MAIN is False
        assert lifecycle_mod.TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT is False
        assert lifecycle_mod.EXACT_LOGICAL_REENTRY_PRESERVED is True
        assert lifecycle_mod.CARD_FIRST_REENTRY is True


# ---------------------------------------------------------------------------
# SOUL lifetime alignment (source semantics agree with runtime)
# ---------------------------------------------------------------------------


class TestSoulAlignment:
    @staticmethod
    def _soul_text(role: str) -> str:
        from aota_forge.work_plane.af_roles import _roles_dir

        return (_roles_dir() / f"{role}.md").read_text(encoding="utf-8")

    def test_task_main_soul_long_lived(self) -> None:
        text = self._soul_text("task-main")
        assert "Long-lived" in text or "long-lived" in text
        assert "SOUL is guidance, not authority" in text or "SOUL_IS_AUTHORITY" in text or "guidance" in text

    @pytest.mark.parametrize("role", ["analyst", "coder", "reviewer", "project-steward"])
    def test_one_shot_souls_bounded(self, role: str) -> None:
        text = self._soul_text(role)
        assert "one-shot" in text
        assert "terminate" in text

    def test_soul_is_not_authority(self) -> None:
        for role in ("task-main", "analyst", "coder", "reviewer", "project-steward"):
            text = self._soul_text(role)
            assert "SOUL_IS_AUTHORITY=no" in text or "SOUL is guidance, not authority" in text
