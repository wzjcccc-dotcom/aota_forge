"""AF #52 M1/W1 — Task-main integrated-review transition guidance (I40-B009).

Repairs the task-main-visible contract gap: after the final source Work Item is
reconciled, `task_main.advance_once` returns `INTEGRATED_REVIEW_REQUIRED`; the
normal protocol is to call `task_main.advance_once` again in the same session
so the runtime resolves the governed reviewer handoff and dispatches the
integrated reviewer. Manual review-handoff construction paths remain invalid.

Proof boundary (honest):
  PROVES=Skill contract, curated eager guidance, model-visible advance_once
         result guidance, reason-text truthfulness, preserved two-phase
         deterministic behavior, and rejected manual bypass paths.
  DOES_NOT_PROVE=real Hermes model/Worker production V3 (M1/W2 owns that).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.execution.reference import REFERENCE_EXECUTOR_ID
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core, resolve_descriptor
from aota_forge.mcp_transport import SUPPORTED_OPERATIONS, _SharedAotaMcpAdapter
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import activate_milestone
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedTaskMainRuntimeContext,
    TrustedWorkerBinding,
)
from aota_forge.work_plane.af_roles import curated_eager_guidance
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.role_bootstrap import BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from tests.test_af51_m1_w1_normal_path_progression_binding import (
    PROJECT_ID,
    _RecordingReferenceAdapter,
    _complete_dispatched_record,
    _evidence_resolver,
    _live,
    _sandbox,
    _single_work_view,
    _start,
    _state_handoff_resolver,
    _write_grounded,
)

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "aota-task-main-control" / "SKILL.md"
EXPECTED_GUIDANCE = "call task_main.advance_once again to dispatch the governed integrated reviewer"
ORIGIN_SESSION = "sess-af52w1"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _reviewer_handoff() -> TaskHandoff:
    return TaskHandoff(
        work_role="reviewer",
        task_kind="af52-integrated-review",
        objective="execute integrated milestone review",
        bounded_scope="bounded review scope",
        validation_expectations=("integrated review validation",),
        semantic_stop_expectations=("stop if review scope unclear",),
        work_item_ref=SemanticReference(ref="M1/RV1"),
        milestone_ref=SemanticReference(ref="M1"),
    )


def _task_main_context(live, sandbox, service, coordinator_id):
    def unused_resolver(work_item_id: str):
        raise RuntimeError(f"unused handoff resolver for {work_item_id!r}")

    return TrustedTaskMainRuntimeContext(
        control_service=service,
        live_plan_view=live,
        origin_task_main_session_ref=ORIGIN_SESSION,
        executor_id=REFERENCE_EXECUTOR_ID,
        handoff_resolver=unused_resolver,
        coordinator_id=coordinator_id,
    )


def _task_main_binding(live, sandbox, service, coordinator_id) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-control",
        objective="AF52 W1 task-main control",
        bounded_scope="milestone coordination only",
        validation_expectations=("task-main control validation",),
        semantic_stop_expectations=("stop at user gate",),
        work_item_ref=SemanticReference(ref=f"{live.milestone_id}/task-main"),
        milestone_ref=SemanticReference(ref=live.milestone_id),
    )
    surface = create_role_tool_surface(
        "task-main",
        eager=(
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
            "handoff.write",
            "handoff.open",
            "task.start",
        ),
        progressive=(),
    )
    return CanonicalDispatchBinding(
        canonical_task_id=f"{PROJECT_ID}:{live.milestone_id}:task-main",
        project_id=PROJECT_ID,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        trusted_task_main_context=_task_main_context(live, sandbox, service, coordinator_id),
    )


def _make_env(tmp_path: Path, live):
    """Deterministic task-main control env aligned with the reference adapter id."""
    tag = uuid.uuid4().hex[:8]
    coord_path = tmp_path / f"coord_{tag}.json"
    exec_path = tmp_path / f"exec_{tag}.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    coord_store = FileBackedTaskMainCoordinatorStore(coord_path)
    exec_store = FileBackedExecutionStateStore(exec_path)
    sandbox = _sandbox(tmp_path, tag=tag)
    registry = ExecutorRegistry()
    recording = _RecordingReferenceAdapter()
    registry.register(recording)
    dispatcher = ExecutionDispatcher(registry, state_store=exec_store)
    service = TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
    )
    handle = activate_milestone(
        store=coord_store,
        plan_view=live,
        origin_task_main_session_ref=ORIGIN_SESSION,
        execution_dispatcher=dispatcher,
        executor_id=REFERENCE_EXECUTOR_ID,
        project_id=PROJECT_ID,
    )
    return SimpleNamespace(
        live=live,
        sandbox=sandbox,
        coord_store=coord_store,
        exec_store=exec_store,
        dispatcher=dispatcher,
        recording=recording,
        service=service,
        coordinator_id=handle.coordinator_id,
        binding=_task_main_binding(live, sandbox, service, handle.coordinator_id),
    )


def _mcp_binding(env, ctx) -> TrustedWorkerBinding:
    return TrustedWorkerBinding(
        canonical_task_id=env.binding.canonical_task_id,
        project_id=env.binding.project_id,
        worktree_id=env.binding.worktree_id,
        trusted_context=bind_trusted_context(
            principal_id="task-main", principal_type="hermes-agent", channel="mcp"
        ),
        handoff=env.binding.handoff,
        sandbox=env.binding.sandbox,
        tool_surface=env.binding.tool_surface,
        read_authorities=tuple(env.binding.read_authorities),
        trusted_task_main_context=ctx,
    )


def _prepared_env(tmp_path: Path):
    """Single-Work-Item milestone plus a genuinely completed normal-path W1."""
    live = _single_work_view(_live()[0])
    env = _make_env(tmp_path, live)
    bind_execution_dispatcher(env.dispatcher)
    written = _write_grounded(env)
    assert written.ok, written.error
    started = _start(env, written.payload["ref"])
    assert started.ok, started.error
    _complete_dispatched_record(env, started.payload["task_id"])
    ctx = dataclasses.replace(
        env.binding.trusted_task_main_context,
        handoff_resolver=_state_handoff_resolver(env),
        governed_evidence_resolver=_evidence_resolver,
    )
    return env, ctx, written.payload["ref"]


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# A. Skill guidance
# ---------------------------------------------------------------------------


class TestSkillGuidance:
    def test_skill_teaches_second_advance_once_and_prohibits_manual_review(self) -> None:
        text = _skill_text()
        assert "INTEGRATED_REVIEW_REQUIRED" in text
        assert (
            'call `aota.invoke(operation="task_main.advance_once", arguments={})` again in the same session'
            in text
        )
        assert "Never construct the review handoff manually" in text
        assert 'no `handoff.write(mode="review")`' in text
        assert "no fabricated review Work Item" in text
        assert 'no `task.start(role="reviewer")` from a source Work handoff' in text
        assert "DISPATCHED_REVIEW" in text

    def test_skill_does_not_claim_manual_dispatch(self) -> None:
        text = _skill_text()
        assert "reviewer is not auto-dispatched" not in text
        assert "manually dispatch" not in text


# ---------------------------------------------------------------------------
# B. Eager task-main guidance
# ---------------------------------------------------------------------------


class TestEagerGuidance:
    def test_eager_guidance_carries_minimal_transition_hint(self) -> None:
        guidance = curated_eager_guidance("aota-task-main-control")
        assert "INTEGRATED_REVIEW_REQUIRED" in guidance
        assert "call advance_once again" in guidance
        assert "never build a review handoff manually" in guidance
        assert len(guidance) <= BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS


# ---------------------------------------------------------------------------
# C+D. Model-visible result + preserved two-phase behavior
# ---------------------------------------------------------------------------


class TestReviewTransitionReachability:
    def test_model_visible_result_and_second_advance_dispatches_reviewer(self, tmp_path: Path) -> None:
        env, ctx1, _ = _prepared_env(tmp_path)
        adapter1 = _SharedAotaMcpAdapter(_mcp_binding(env, ctx1))

        first = adapter1.invoke("task_main.advance_once", {})
        assert first["ok"] is True, first
        payload = first["payload"]
        assert payload["disposition"] == "INTEGRATED_REVIEW_REQUIRED"
        assert payload["integrated_review_required"] is True
        # Existing stable disposition-like next_action stays intact.
        assert payload["next_action"] == "INTEGRATED_REVIEW_REQUIRED"
        # Additive model-visible guidance states the actual next protocol action.
        assert payload["next_action_guidance"] == EXPECTED_GUIDANCE
        assert payload["review_dispatch_mode"] == "runtime_resolved"
        # Misleading reason removed; truthful two-phase reason present.
        blob = json.dumps(first)
        assert "reviewer is not auto-dispatched" not in blob
        assert any("call task_main.advance_once again" in reason for reason in payload["reasons"])
        # Phase 1 performs no reviewer dispatch.
        assert len(env.recording.packages) == 1
        assert not any(":RV" in pkg.canonical_task_id for pkg in env.recording.packages)

        # Phase 2: same live session/state, runtime resolves the reviewer handoff.
        ctx2 = dataclasses.replace(ctx1, reviewer_handoff_resolver=_reviewer_handoff)
        adapter2 = _SharedAotaMcpAdapter(_mcp_binding(env, ctx2))
        second = adapter2.invoke("task_main.advance_once", {})
        assert second["ok"] is True, second
        payload2 = second["payload"]
        assert payload2["disposition"] == "DISPATCHED_REVIEW"
        assert payload2["integrated_review_required"] is True
        assert any(":RV1" in str(item) for item in payload2["dispatched"])
        assert any(":RV1" in pkg.canonical_task_id for pkg in env.recording.packages)


# ---------------------------------------------------------------------------
# E. Manual bypass paths remain rejected; no new manual dispatch route
# ---------------------------------------------------------------------------


class TestManualReviewPathsRejected:
    def test_manual_review_handoff_write_rejected(self, tmp_path: Path) -> None:
        env, _ctx, _ref = _prepared_env(tmp_path)
        review_write = dispatch_via_core(
            "handoff.write",
            {"mode": "review", "payload": {"objective": "fabricated review"}},
            env.binding,
        )
        assert review_write.ok is False

    def test_reviewer_task_start_from_source_work_handoff_rejected(self, tmp_path: Path) -> None:
        env, ctx1, work_ref = _prepared_env(tmp_path)
        adapter = _SharedAotaMcpAdapter(_mcp_binding(env, ctx1))
        first = adapter.invoke("task_main.advance_once", {})
        assert first["ok"] is True, first
        assert first["payload"]["disposition"] == "INTEGRATED_REVIEW_REQUIRED"

        manual_reviewer = adapter.invoke("task.start", {"role": "reviewer", "handoff_ref": work_ref})
        assert manual_reviewer["ok"] is False
        assert manual_reviewer["error"]["code"] == "WORK_ITEM_MISMATCH"
        assert len(env.recording.packages) == 1

    def test_no_manual_reviewer_dispatch_operation_added(self) -> None:
        with pytest.raises(Exception):
            resolve_descriptor("task_main.dispatch_reviewer")
        assert not any(
            "dispatch_reviewer" in op or "reviewer.start" in op for op in SUPPORTED_OPERATIONS
        )
