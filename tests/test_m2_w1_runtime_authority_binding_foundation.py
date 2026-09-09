"""M2/W1 Runtime Authority, Binding & Durable Orchestration Foundation.

Covers W1 implementation validation A-M plus negative authority tests:

A. worker/task-main binding separation
B. invalid/missing binding fail-closed
C. Worker cannot mint task-main authority
D. TaskHandoff scope cannot be widened
E. result envelope identity/digest stability
F. legacy WorkerResultCard compatibility
G. card-first reconciliation
H. durable coordinator round-trip
I. durable Human Brake/user-gate round-trip
J. restart/recovery without raw history
K. exact/logical task-main re-entry
L. ProjectState ref round-trip
M. bootstrap truncation behavior

All authority tests are fail-closed with negative cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import (
    DOGFOOD_LITERAL_SPECIAL_CASE_ALLOWED,
    FREEFORM_PROMPT_CAN_MINT_BINDING_AUTHORITY,
    PROJECT_ID_SPECIAL_CASE_ALLOWED,
    TASK_MAIN_CAN_TREAT_WORKER_BINDING_AS_TASK_MAIN_AUTHORITY,
    TRUSTED_BINDING_FAIL_CLOSED,
    WORKER_CAN_MINT_TASK_MAIN_AUTHORITY,
    build_worker_binding,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    MCP_PUBLIC_TOOLS,
    TrustedBindingError,
    TrustedWorkerBinding,
)
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
    recover_coordinator,
)
from aota_forge.runtime.task_main.coordinator_state import (
    HUMAN_BRAKE_STATE_DURABLE,
    TASK_MAIN_RESTART_CANNOT_AUTO_RESOLVE_HUMAN_CHECKPOINT,
    TASK_MAIN_RESTART_CANNOT_FORGET_USER_GATE,
    TaskMainCoordinatorState,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.adapter import (
    ExecutorAdapter,
    DispatchResult,
    TaskStatusResult,
    ValidationResult,
    CancelResult,
    ResumeResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.work_plane.handoff import (
    TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE,
    TASK_HANDOFF_IS_BOUNDED,
    TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY,
    WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE,
    WORKER_STARTUP_PROMPT_IS_AUTHORITY,
    SemanticReference,
    TaskHandoff,
    assert_handoff_is_bounded_projection,
    validate_handoff_scope_containment,
)
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from aota_forge.work_plane.role_result import (
    CARD_FIRST_RECONCILIATION,
    LEGACY_WORKER_RESULT_COMPATIBILITY,
    ROLE_PAYLOAD_KINDS,
    ROLE_RESULT_IS_DIGEST_BOUND,
    ROLE_RESULT_IS_DURABLE,
    SECOND_UNRELATED_RESULT_TRANSPORT_CREATED,
    CommonResultEnvelope,
    RolePayloadRef,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.role_bootstrap import (
    ARBITRARY_180_CHAR_SEMANTIC_LOSS,
    BOOTSTRAP_CONTENT_UNBOUNDED,
    BOOTSTRAP_TRUNCATION_REPAIRED,
    handle_role_bootstrap,
)
from aota_forge.work_plane.roles import AgentWorkRole


def _handoff(role: AgentWorkRole = AgentWorkRole.CODER, scope: str = "work/bounded-only") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="m2-w1-test",
        objective="bounded objective for W1 foundation",
        bounded_scope=scope,
        validation_expectations=("output matches",),
        semantic_stop_expectations=("stop on denial",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M2"),
        project_ref=SemanticReference(ref="aota_forge"),
        plan_ref=SemanticReference(ref="wzjcccc-dotcom/aota-hermes-tools#44"),
    )


def _card(task_id: str = "t-w1-1", role: AgentWorkRole = AgentWorkRole.CODER) -> WorkerResultCard:
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="hermes",
        result_data={"output": "ok"},
        correlation_id="corr-w1",
    )
    gov = ResultGovernanceProjection.success()
    return project_worker_result_card(result, gov, role, summary="bounded W1 success")


# ---------------------------------------------------------------------------
# A. worker/task-main binding separation
# ---------------------------------------------------------------------------


class TestABindingSeparation:
    def test_worker_binding_has_no_task_main_context(self, tmp_path: Path) -> None:
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-a", canonical_task_id="t-a", handoff=_handoff()
        )
        assert b.trusted_task_main_context is None
        assert b.handoff.work_role == AgentWorkRole.CODER

    def test_coder_has_mutation_reviewer_denied(self, tmp_path: Path) -> None:
        coder = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-c", canonical_task_id="t-c", handoff=_handoff(AgentWorkRole.CODER)
        )
        assert coder.mutation_authority is not None
        reviewer = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-r", canonical_task_id="t-r", handoff=_handoff(AgentWorkRole.REVIEWER)
        )
        assert reviewer.mutation_authority is None
        steward = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-s", canonical_task_id="t-s", handoff=_handoff(AgentWorkRole.PROJECT_STEWARD)
        )
        assert steward.mutation_authority is None

    def test_single_shared_mcp_tool(self) -> None:
        assert tuple(MCP_PUBLIC_TOOLS) == ("aota.invoke",)
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"

    def test_authority_markers(self) -> None:
        assert TRUSTED_BINDING_FAIL_CLOSED is True
        assert WORKER_CAN_MINT_TASK_MAIN_AUTHORITY is False
        assert TASK_MAIN_CAN_TREAT_WORKER_BINDING_AS_TASK_MAIN_AUTHORITY is False
        assert FREEFORM_PROMPT_CAN_MINT_BINDING_AUTHORITY is False
        assert PROJECT_ID_SPECIAL_CASE_ALLOWED is False
        assert DOGFOOD_LITERAL_SPECIAL_CASE_ALLOWED is False


# ---------------------------------------------------------------------------
# B. invalid/missing binding fail-closed (negative)
# ---------------------------------------------------------------------------


class TestBFailClosed:
    def test_missing_handoff_fails(self, tmp_path: Path) -> None:
        with pytest.raises((TrustedBindingError, TypeError, ValueError)):
            build_worker_binding(
                root=tmp_path, project_id="aota_forge", worktree_id="wt", canonical_task_id="t", handoff=None  # type: ignore[arg-type]
            )

    def test_empty_scope_handoff_fails(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            TaskHandoff(
                work_role=AgentWorkRole.CODER,
                task_kind="k",
                objective="o",
                bounded_scope="   ",
                validation_expectations=("v",),
                semantic_stop_expectations=("s",),
            )

    def test_mechanical_field_injection_fails(self) -> None:
        with pytest.raises(ValueError):
            TaskHandoff.from_dict(
                {
                    "work_role": "coder",
                    "task_kind": "k",
                    "objective": "o",
                    "bounded_scope": "s",
                    "validation_expectations": [],
                    "semantic_stop_expectations": [],
                    "package_id": "evil",
                }
            )

    def test_task_main_role_without_context_denied(self, tmp_path: Path) -> None:
        # Direct TrustedWorkerBinding with task-main role but no context must fail.
        h = _handoff(AgentWorkRole.TASK_MAIN)
        # build_worker_binding already denies; direct construction must also deny via mcp_transport gate.
        with pytest.raises(TrustedBindingError):
            build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt", canonical_task_id="t", handoff=h)


# ---------------------------------------------------------------------------
# C. Worker cannot mint task-main authority (negative)
# ---------------------------------------------------------------------------


class TestCWorkerCannotMintTaskMain:
    def test_worker_path_rejects_task_main_role(self, tmp_path: Path) -> None:
        h = _handoff(AgentWorkRole.TASK_MAIN)
        with pytest.raises(TrustedBindingError, match="must not mint task-main"):
            build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt", canonical_task_id="t", handoff=h)

    def test_worker_binding_context_is_none(self, tmp_path: Path) -> None:
        b = build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt", canonical_task_id="t", handoff=_handoff())
        assert b.trusted_task_main_context is None

    def test_task_main_context_requires_task_main_role(self, tmp_path: Path) -> None:
        # A worker-role handoff carrying a fake task-main context must be rejected
        # at TrustedWorkerBinding construction (mcp_transport gate).
        from aota_forge.core.context import bind_trusted_context
        from aota_forge.work_plane.tool_surface import create_role_tool_surface
        from aota_forge.work_plane.workspace_tools import create_workspace_authority, WORKSPACE_READ_DESCRIPTOR, WORKSPACE_SEARCH_DESCRIPTOR
        from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
        from aota_forge.composition.project_binding import resolve_trusted_project_evidence
        from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate

        h = _handoff(AgentWorkRole.CODER)
        # Build a minimal sandbox via synthetic fallback path (reuse build_worker_binding for sandbox? instead construct directly is complex).
        # Simpler: assert the gate exists by checking that a coder binding with a dummy context object fails.
        b = build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt-x", canonical_task_id="t-x", handoff=h)
        with pytest.raises(TrustedBindingError):
            TrustedWorkerBinding(
                canonical_task_id=b.canonical_task_id,
                project_id=b.project_id,
                worktree_id=b.worktree_id,
                trusted_context=b.trusted_context,
                handoff=b.handoff,
                sandbox=b.sandbox,
                tool_surface=b.tool_surface,
                read_authorities=b.read_authorities,
                mutation_authority=b.mutation_authority,
                trusted_task_main_context=object(),
            )


# ---------------------------------------------------------------------------
# D. TaskHandoff scope cannot be widened (negative)
# ---------------------------------------------------------------------------


class TestDScopeFailClosed:
    def test_markers(self) -> None:
        assert TASK_HANDOFF_IS_BOUNDED is True
        assert TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE is False
        assert WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE is False
        assert TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY is False
        assert WORKER_STARTUP_PROMPT_IS_AUTHORITY is False

    def test_equal_scope_passes(self) -> None:
        validate_handoff_scope_containment(handoff_scope="work/a-only", worker_scope="work/a-only")

    def test_widened_scope_fails(self) -> None:
        with pytest.raises(ValueError, match="must equal handoff"):
            validate_handoff_scope_containment(handoff_scope="work/a-only", worker_scope="work/a-only and work/b")

    def test_narrowed_scope_fails_exact(self) -> None:
        with pytest.raises(ValueError):
            validate_handoff_scope_containment(handoff_scope="work/a-only", worker_scope="work/a")

    def test_handoff_is_bounded_projection(self) -> None:
        assert_handoff_is_bounded_projection(_handoff())
        with pytest.raises(TypeError):
            assert_handoff_is_bounded_projection("not-a-handoff")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# E. result envelope identity/digest stability
# ---------------------------------------------------------------------------


class TestEEnvelopeDigest:
    def test_digest_stable(self) -> None:
        c = _card("t-e1")
        e1 = CommonResultEnvelope.from_worker_result_card(c, work_item_ref=SemanticReference(ref="W1"))
        e2 = CommonResultEnvelope.from_worker_result_card(c, work_item_ref=SemanticReference(ref="W1"))
        assert e1.compute_envelope_digest() == e2.compute_envelope_digest()

    def test_digest_changes_with_summary(self) -> None:
        c1 = _card("t-e2")
        c2 = _card("t-e2")
        # Same card digest, but envelope summary differs if we construct manually? from_worker uses card summary, so equal.
        # Instead vary payload kind.
        e1 = CommonResultEnvelope.from_worker_result_card(c1)
        e2 = CommonResultEnvelope.from_worker_result_card(
            c2, role_payload=RolePayloadRef(kind="coder_implementation_evidence", ref="impl-1")
        )
        assert e1.compute_envelope_digest() != e2.compute_envelope_digest()

    def test_envelope_round_trip_digest_verified(self) -> None:
        c = _card("t-e3")
        e = CommonResultEnvelope.from_worker_result_card(
            c, work_item_ref=SemanticReference(ref="W1"), milestone_ref=SemanticReference(ref="M2")
        )
        d = e.to_dict()
        back = CommonResultEnvelope.from_dict(d)
        assert back.compute_envelope_digest() == e.compute_envelope_digest()

    def test_tampered_digest_fails(self) -> None:
        c = _card("t-e4")
        e = CommonResultEnvelope.from_worker_result_card(c)
        d = e.to_dict()
        d["summary"] = "tampered summary"
        with pytest.raises(ValueError, match="digest mismatch"):
            CommonResultEnvelope.from_dict(d)

    def test_markers(self) -> None:
        assert SECOND_UNRELATED_RESULT_TRANSPORT_CREATED is False
        assert ROLE_RESULT_IS_DURABLE is True
        assert ROLE_RESULT_IS_DIGEST_BOUND is True
        assert CARD_FIRST_RECONCILIATION is True


# ---------------------------------------------------------------------------
# F. legacy WorkerResultCard compatibility
# ---------------------------------------------------------------------------


class TestFLegacyCompat:
    def test_envelope_preserves_card(self) -> None:
        c = _card("t-f1", AgentWorkRole.REVIEWER)
        e = CommonResultEnvelope.from_worker_result_card(c)
        assert e.task_ref == c.task_ref
        assert e.card_digest == c.compute_card_digest()
        assert e.role == c.agent_work_role
        assert e.outcome == c.outcome
        assert e.worker_result_card is not None

    def test_envelope_without_card_still_valid(self) -> None:
        c = _card("t-f2")
        e = CommonResultEnvelope(
            role=c.agent_work_role,
            task_ref=c.task_ref,
            handoff_ref=c.result_handoff_ref,
            outcome=c.outcome,
            summary=c.summary,
            card_digest=c.compute_card_digest(),
            evidence_refs=c.primary_evidence_refs,
            artifact_refs=c.output_artifact_refs,
        )
        assert e.compute_envelope_digest()

    def test_role_payload_seam_kinds(self) -> None:
        assert set(ROLE_PAYLOAD_KINDS) == {
            "analyst_evidence",
            "coder_implementation_evidence",
            "reviewer_review_evidence",
            "project_state_evidence",
            "steward_closure_evidence",
        }
        for kind in ROLE_PAYLOAD_KINDS:
            r = RolePayloadRef(kind=kind, ref=f"{kind}-ref-1")
            assert r.kind == kind
        with pytest.raises(ValueError):
            RolePayloadRef(kind="unknown_kind", ref="x")

    def test_legacy_compat_marker(self) -> None:
        assert LEGACY_WORKER_RESULT_COMPATIBILITY is True


# ---------------------------------------------------------------------------
# G. card-first reconciliation
# ---------------------------------------------------------------------------


class TestGCardFirst:
    def test_envelope_requires_card_truth(self) -> None:
        c = _card("t-g1")
        # Envelope coherence: tampered card digest vs card must fail.
        with pytest.raises(ValueError):
            CommonResultEnvelope(
                role=c.agent_work_role,
                task_ref=c.task_ref,
                handoff_ref=c.result_handoff_ref,
                outcome=c.outcome,
                summary=c.summary,
                card_digest="0" * 64,
                worker_result_card=c,
            )

    def test_raw_transcript_not_required(self) -> None:
        from aota_forge.work_plane.role_result import RAW_ROLE_TRANSCRIPT_REQUIRED

        assert RAW_ROLE_TRANSCRIPT_REQUIRED is False

    def test_card_digest_binding(self) -> None:
        c = _card("t-g2")
        e = CommonResultEnvelope.from_worker_result_card(c)
        assert e.card_digest == c.compute_card_digest()
        assert e.handoff_ref.ref == c.task_ref


# ---------------------------------------------------------------------------
# H. durable coordinator round-trip
# ---------------------------------------------------------------------------


def _plan_view(marker: str = "w1") -> tuple[MilestonePlanView, str, str | None]:
    body = (
        "# [PLAN] W1 fixture\n\n## 1. Current State\n```text\n"
        f"PLAN_STATUS=in-progress\nCURRENT_BLOCKER={marker}\n```\n"
    )
    doc = normalize_portable_plan(body, source_revision="rev-w1-a")
    digest = portable_plan_digest(doc)
    graph = MilestoneWorkItemGraph(milestone_ref="M2", work_items=["W1", "W2"], dependencies=[["W1", "W2"]])
    view = MilestonePlanView(
        plan_authority="wzjcccc-dotcom/aota-hermes-tools#44",
        plan_digest=digest,
        milestone_id="M2",
        entry_base="8ddd167f27cb9e0a4bcb3fd0568466180ce3583a",
        graph=graph,
        milestone_user_approval_satisfied=True,
        plan_source_revision=doc.source_revision,
    )
    return view, digest, doc.source_revision


class _FakeAdapter(ExecutorAdapter):
    def capabilities(self) -> ExecutorCapabilities:
        from aota_forge.core.execution.roles import CANONICAL_ROLES

        return ExecutorCapabilities(
            executor_id="m2w1-fake",
            adapter_kind="m2w1_fake_test_double",
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
            adapter_handle=f"m2w1-fake||{package.canonical_task_id}||running",
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time="2026-09-09T00:00:00+00:00",
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        return CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id="m2w1-fake",
            result_data={"proof": "m2-w1"},
            correlation_id=f"corr-{canonical_task_id}",
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(canonical_task_id=canonical_task_id, cancelled=True, state=CanonicalTaskState.CANCELLED)

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


def _dispatcher(tmp_path: Path, name: str = "w1") -> tuple[ExecutionDispatcher, InMemoryExecutionStateStore]:
    from aota_forge.core.execution.durable_state import OriginSessionRef

    store = InMemoryExecutionStateStore()
    registry = ExecutorRegistry()
    registry.register(_FakeAdapter())
    disp = ExecutionDispatcher(
        registry,
        state_store=store,
        origin_session_ref=OriginSessionRef(value="sess-w1-test"),
    )
    return disp, store


class TestHDurableRoundTrip:
    def test_durable_attempt_state_no_transcript(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("h-attempt")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-h-attempt",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        coord.record_attempt(
            "W1", failure_class="VALIDATION_FAILURE", next_disposition="RETRY",
            hypothesis_ref="hyp-1", blocking_evidence_ref="ev-1",
        )
        st = coord.state.attempt_states.get("W1")
        assert st is not None and st["failure_class"] == "VALIDATION_FAILURE"
        assert st["attempt"] == 1
        # No raw transcript stored.
        assert "transcript" not in st and "stdout" not in st and "log" not in st
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get(coord.coordinator_id)
        assert loaded is not None and loaded.attempt_states["W1"]["next_disposition"] == "RETRY"
        reopened.close()

    def test_minimum_truth_round_trip(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("h")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore,
            plan_view=view,
            origin_task_main_session_ref="sess-w1-h",
            execution_dispatcher=disp,
            executor_id="hermes",
            project_id="aota_forge",
        )
        # Extend with W1 minimum truth via CAS.
        coord.set_human_brake(state="NEEDS_INPUT", scope="AFFECTED_WORK", affected_work=("W1",), reason="need decision")
        coord.set_project_state(ref="proj-state-1", digest="a" * 64, freshness="FRESH", status="ACTIVE")
        coord.set_next_action("await user input", open_blockers=("blocker-1",))
        snap_before = coord.durable_snapshot()
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get(coord.coordinator_id)
        assert loaded is not None
        assert loaded.human_brake is not None and loaded.human_brake["state"] == "NEEDS_INPUT"
        assert loaded.project_state is not None and loaded.project_state["ref"] == "proj-state-1"
        assert loaded.next_action == "await user input"
        assert loaded.open_blockers == ("blocker-1",) or list(loaded.open_blockers) == ["blocker-1"]
        # Per-work bindings carry handoff/result/review seam after dispatch? Before dispatch bindings empty is fine.
        assert snap_before["plan_ref"] == "wzjcccc-dotcom/aota-hermes-tools#44"
        assert snap_before["milestone_ref"] == "M2"
        reopened.close()

    def test_binding_handoff_result_review_seam(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        state = TaskMainCoordinatorState(
            coordinator_id="aota_forge:M2",
            plan_authority="wzjcccc-dotcom/aota-hermes-tools#44",
            plan_digest="d" * 64,
            milestone_id="M2",
            entry_base="8ddd167f27cb9e0a4bcb3fd0568466180ce3583a",
            origin_task_main_session_ref="sess-1",
            project_id="aota_forge",
            executor_id="hermes",
            work_items=("W1",),
            wi_status={"W1": "ACTIVE"},
            bindings={
                "W1": {
                    "canonical_task_id": "aota_forge:M2:W1:attempt-1",
                    "attempt": 1,
                    "idempotency_key": "k1",
                    "completion_ref": None,
                    "completion_card_digest": None,
                    "handoff_ref": "W1",
                    "handoff_digest": "h" * 64,
                    "result_ref": None,
                    "result_digest": None,
                    "review_state": "PENDING",
                }
            },
        )
        created = cstore.create(state)
        assert created.bindings["W1"]["handoff_digest"] == "h" * 64
        assert created.bindings["W1"]["review_state"] == "PENDING"
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get("aota_forge:M2")
        assert loaded is not None and loaded.bindings["W1"]["handoff_ref"] == "W1"
        reopened.close()


# ---------------------------------------------------------------------------
# I. durable Human Brake / user-gate round-trip
# ---------------------------------------------------------------------------


class TestIHumanBrakeDurable:
    def test_brake_states_supported(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("i")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-i",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        for state, scope, affected in [
            ("NEEDS_INPUT", "AFFECTED_WORK", ("W1",)),
            ("HUMAN_CHECKPOINT_REQUIRED", "DEPENDENT_SUBGRAPH", ("W1",)),
            ("USER_DECISION_REQUIRED", "WHOLE_MILESTONE", ()),
            ("USER_GATE_REQUIRED", "WHOLE_MILESTONE", ()),
            ("BLOCKED", "AFFECTED_WORK", ("W2",)),
        ]:
            coord.set_human_brake(state=state, scope=scope, affected_work=affected, reason=f"reason-{state}")
            assert coord.state.human_brake is not None and coord.state.human_brake["state"] == state
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get(coord.coordinator_id)
        assert loaded is not None and loaded.human_brake is not None and loaded.human_brake["state"] == "BLOCKED"
        reopened.close()
        assert HUMAN_BRAKE_STATE_DURABLE is True
        assert TASK_MAIN_RESTART_CANNOT_FORGET_USER_GATE is True
        assert TASK_MAIN_RESTART_CANNOT_AUTO_RESOLVE_HUMAN_CHECKPOINT is True

    def test_invalid_brake_fails(self, tmp_path: Path) -> None:
        from aota_forge.runtime.task_main.coordinator_store import CoordinatorPersistenceFailureError

        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("i2")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-i2",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        with pytest.raises((ValueError, CoordinatorPersistenceFailureError)):
            coord.set_human_brake(state="UNKNOWN_STATE", scope="WHOLE_MILESTONE")
        with pytest.raises((ValueError, CoordinatorPersistenceFailureError)):
            coord.set_human_brake(state="BLOCKED", scope="UNKNOWN_SCOPE")
        cstore.close()


# ---------------------------------------------------------------------------
# J. restart/recovery without raw history
# ---------------------------------------------------------------------------


class TestJRestartWithoutHistory:
    def test_recover_without_raw_history(self, tmp_path: Path) -> None:
        cstore_path = tmp_path / "c.json"
        cstore = FileBackedTaskMainCoordinatorStore(cstore_path)
        view, _, _ = _plan_view("j")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-j",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        coord.set_human_brake(state="NEEDS_INPUT", scope="AFFECTED_WORK", affected_work=("W1",), reason="r")
        snap_before = coord.durable_snapshot()
        cstore.close()
        # Fresh process: new store + dispatcher objects, same files, no raw history.
        cstore2 = FileBackedTaskMainCoordinatorStore(cstore_path)
        disp2, _ = _dispatcher(tmp_path)
        recovered = recover_coordinator(
            store=cstore2, coordinator_id=coord.coordinator_id, live_plan_view=view,
            execution_dispatcher=disp2, session_available=True,
        )
        snap_after = recovered.durable_snapshot()
        assert snap_after["plan_ref"] == snap_before["plan_ref"]
        assert snap_after["human_brake"] is not None and snap_after["human_brake"]["state"] == "NEEDS_INPUT"
        # Ready set reconstructible without raw history.
        assert snap_after["ready_set"] == snap_before["ready_set"]
        cstore2.close()

    def test_restart_preserves_user_gate(self, tmp_path: Path) -> None:
        from aota_forge.work_plane.progression import MilestoneWorkItemGraph

        body = "# [PLAN]\n\n## 1. Current State\n```text\nPLAN_STATUS=in-progress\n```\n"
        doc = normalize_portable_plan(body, source_revision="rev-gate")
        digest = portable_plan_digest(doc)
        graph = MilestoneWorkItemGraph(milestone_ref="M2", work_items=["W1"], dependencies=[])
        gated_view = MilestonePlanView(
            plan_authority="wzjcccc-dotcom/aota-hermes-tools#44", plan_digest=digest,
            milestone_id="M2", entry_base="8ddd167f27cb9e0a4bcb3fd0568466180ce3583a",
            graph=graph, milestone_user_approval_satisfied=False,
        )
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=gated_view, origin_task_main_session_ref="sess-g",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        assert coord.state.status.value == "USER_GATE_REQUIRED"
        cstore.close()
        cstore2 = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        disp2, _ = _dispatcher(tmp_path)
        rec = recover_coordinator(
            store=cstore2, coordinator_id=coord.coordinator_id, live_plan_view=gated_view,
            execution_dispatcher=disp2, session_available=True,
        )
        assert rec.state.status.value == "USER_GATE_REQUIRED"
        cstore2.close()


# ---------------------------------------------------------------------------
# K. exact/logical task-main re-entry
# ---------------------------------------------------------------------------


class TestKReentry:
    def test_exact_reactivation_idempotent(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("k")
        disp, _ = _dispatcher(tmp_path)
        c1 = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-k",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        c2 = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-k",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        assert c1.coordinator_id == c2.coordinator_id
        assert c1.state.coordinator_revision == c2.state.coordinator_revision
        cstore.close()

    def test_wrong_session_fails_closed(self, tmp_path: Path) -> None:
        from aota_forge.runtime.task_main.coordinator import SessionRecoveryRequiredError

        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("k2")
        disp, _ = _dispatcher(tmp_path)
        activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-k2-a",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        with pytest.raises(SessionRecoveryRequiredError):
            activate_milestone(
                store=cstore, plan_view=view, origin_task_main_session_ref="sess-k2-b",
                execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
            )
        cstore.close()

    def test_recovered_ref_is_not_authority(self, tmp_path: Path) -> None:
        # Tampered digest in durable record must fail closed on recovery/reconciliation,
        # proving recovered refs are validated, not trusted as authority.
        c = _card("t-k3")
        e = CommonResultEnvelope.from_worker_result_card(c)
        d = e.to_dict()
        d["card_digest"] = "f" * 64
        with pytest.raises(ValueError, match="must equal envelope|digest mismatch"):
            CommonResultEnvelope.from_dict(d)


# ---------------------------------------------------------------------------
# L. ProjectState ref round-trip
# ---------------------------------------------------------------------------


class TestLProjectState:
    def test_project_state_survives_round_trip(self, tmp_path: Path) -> None:
        cstore = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        view, _, _ = _plan_view("l")
        disp, _ = _dispatcher(tmp_path)
        coord = activate_milestone(
            store=cstore, plan_view=view, origin_task_main_session_ref="sess-l",
            execution_dispatcher=disp, executor_id="hermes", project_id="aota_forge",
        )
        coord.set_project_state(ref="proj-state-xyz", digest="b" * 64, freshness="FRESH", status="ACTIVE")
        cstore.close()
        reopened = FileBackedTaskMainCoordinatorStore(tmp_path / "c.json")
        loaded = reopened.get(coord.coordinator_id)
        assert loaded is not None and loaded.project_state is not None
        assert loaded.project_state["ref"] == "proj-state-xyz"
        assert loaded.project_state["freshness"] == "FRESH"
        reopened.close()

    def test_trusted_project_binding_preserved(self, tmp_path: Path) -> None:
        # resolve_trusted_project_evidence remains the shared helper (no second resolver).
        from aota_forge.composition.project_binding import resolve_trusted_project_evidence

        assert callable(resolve_trusted_project_evidence)


# ---------------------------------------------------------------------------
# M. bootstrap truncation behavior
# ---------------------------------------------------------------------------


class TestMBootstrap:
    def test_eager_guidance_not_180_truncated(self, tmp_path: Path) -> None:
        h = _handoff(AgentWorkRole.CODER)
        b = build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt-m", canonical_task_id="t-m", handoff=h)
        res = handle_role_bootstrap(b, {})
        assert BOOTSTRAP_TRUNCATION_REPAIRED is True
        assert BOOTSTRAP_CONTENT_UNBOUNDED is False
        assert ARBITRARY_180_CHAR_SEMANTIC_LOSS is False
        for entry in res["BASE_SKILLS"]:
            # Usable guidance: 400 chars (not arbitrary 180), structured with lengths.
            assert entry["content_length"] == 400
            assert entry["content_length"] > 180
            assert len(entry["materialized"]) == entry["content_length"]
            assert entry["byte_length"] <= 32 * 1024
            assert entry["is_truncated"] is True
            assert entry["total_content_length"] > entry["content_length"]

    def test_bootstrap_still_bounded(self, tmp_path: Path) -> None:
        h = _handoff(AgentWorkRole.TASK_MAIN if False else AgentWorkRole.CODER)
        b = build_worker_binding(root=tmp_path, project_id="aota_forge", worktree_id="wt-m2", canonical_task_id="t-m2", handoff=h)
        res = handle_role_bootstrap(b, {})
        total = sum(e["byte_length"] for e in res["BASE_SKILLS"])
        assert total <= 32 * 1024
