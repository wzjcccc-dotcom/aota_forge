"""AF #51 M1/W1 — Normal-path durable progression binding (bounded V1/V2).

Repairs I40-B007. The normal accepted task-main path::

    authoritative Work
      -> handoff.write(mode=work_item)
      -> task.start
      -> Worker execution
      -> task.return / Card / exact parent reentry
      -> task_main.advance_once

must mechanically reconcile the durable coordinator state (bounded Work
projection + Work Item ACTIVE status + execution binding) that
``task_main.advance_once`` requires. The binding carries the exact canonical
task id the normal path actually produced; no parallel identity is recomputed
and no model-facing ``submit_work_projection`` step is required.

Proof boundary (honest):
  PROVES=deterministic component integration: trusted task-main binding ->
         grounded durable handoff -> normal task.start -> exact-identity
         coordinator adoption -> durable state observability -> advance_once
         reconciliation of the exact execution identity; plus failure
         atomicity / idempotency / fail-closed conflict probes.
  DOES_NOT_PROVE=real Hermes model/Worker, real parent re-entry, real
         production V3 (M1/W3 owns the integrated production proof).
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import aota_forge.runtime.task_main.runner as runner_module
from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    FileBackedExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    NORMAL_PATH_ADOPTION_INVENTS_SEMANTICS,
    NORMAL_PATH_ADOPTION_REQUIRES_DURABLE_DISPATCHED_TRUTH,
    NORMAL_PATH_ADOPTION_WRITES_PROJECTION_STATUS_BINDING_ATOMICALLY,
    NORMAL_PATH_BINDING_USES_ACTUAL_TASK_START_CANONICAL_TASK_ID,
    NORMAL_TASK_START_PERSISTS_COORDINATOR_BINDING,
    NORMAL_TASK_START_PERSISTS_WORK_PROJECTION,
    activate_milestone,
    resolve_task_main_work_handoff,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import GovernedWorkItemEvidence
from aota_forge.runtime.trusted_runtime_binding import TrustedTaskMainRuntimeContext
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    WorkProjectionConflictError,
    WorkSemanticProjection,
)
from aota_forge.work_plane.handoff_store import handoff_open, handoff_write
from aota_forge.work_plane.progression import (
    FocusedValidationEvidence,
    FocusedValidationVerdict,
    MilestoneWorkItemGraph,
)
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "proj_af51w1"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#51"
PLAN_DIGEST = "d" * 64
ENTRY_BASE = "a" * 40
MILESTONE_ID = "M1"
MARKER_W1 = "AF51W1_W1_SOURCE_ALPHA"
MARKER_W2 = "AF51W1_W2_SOURCE_BETA"

OBJECTIVE_REAL = "Implement the bounded behavior described by the authoritative source slice"
BOUNDED_SCOPE_REAL = "bounded source-grounded implementation scope"


def _plan_body() -> str:
    return f"""# [PLAN] AF51 W1 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1

#### M1/W1 — Authoritative alpha slice
Implement {MARKER_W1} bounded behavior exactly as the Plan describes.
Acceptance:
AF51W1_ALPHA_AC=PASS

#### M1/W2 — Authoritative beta slice
Implement {MARKER_W2} bounded behavior exactly as the Plan describes.
"""


def _live(*, plan_authority: str = PLAN_AUTH, plan_digest: str = PLAN_DIGEST):
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af51w1")
    live, next_view = project_milestone_views(
        doc,
        plan_authority=plan_authority,
        plan_digest=plan_digest,
        plan_source_revision="rev-af51w1",
    )
    return live, next_view


def _single_work_view(live: MilestonePlanView) -> MilestonePlanView:
    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE_ID, work_items=["W1"], dependencies=[])
    slices = tuple(s for s in live.work_source_slices if s.work_item_id == "W1")
    return dataclasses.replace(live, graph=graph, work_source_slices=slices)


def _semantic(*, work_item_ref: str = "W1", objective: str = OBJECTIVE_REAL) -> dict:
    return {
        "work_role": "coder",
        "task_kind": "af51w1-normal-path",
        "objective": objective,
        "bounded_scope": BOUNDED_SCOPE_REAL,
        "validation_expectations": ["focused validation"],
        "semantic_stop_expectations": ["stop if trusted binding inconsistent"],
        "work_item_ref": {"ref": work_item_ref},
    }


def _source_digest(live: MilestonePlanView, work_item_id: str) -> str:
    source = live.get_work_source_slice(work_item_id)
    assert source is not None
    return hashlib.sha256(source.source_text.encode("utf-8")).hexdigest()


def _sandbox(tmp_path: Path, *, tag: str | None = None) -> WorktreeSandboxBoundary:
    root = tmp_path / f"wt_{tag or uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af51w1",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        worktree_id="wt-1",
        worktree_root=str(root),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


class _RecordingReferenceAdapter(ReferenceFakeExecutorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.packages: list = []

    def dispatch(self, package):
        self.packages.append(package)
        return super().dispatch(package)


def _task_main_binding(
    sandbox: WorktreeSandboxBoundary,
    live: MilestonePlanView,
    coord_store: FileBackedTaskMainCoordinatorStore,
    exec_store,
    dispatcher: ExecutionDispatcher,
    coordinator_id: str | None,
    service: TaskMainControlService,
) -> CanonicalDispatchBinding:
    ctx = TrustedTaskMainRuntimeContext(
        control_service=service,
        live_plan_view=live,
        origin_task_main_session_ref="sess-af51w1",
        executor_id="af51w1-test",
        handoff_resolver=lambda wi: (_ for _ in ()).throw(
            RuntimeError(f"unused handoff resolver for {wi!r}")
        ),
        coordinator_id=coordinator_id,
    )
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-control",
        objective="AF51 W1 task-main control",
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
        trusted_task_main_context=ctx,
    )


def _env(
    tmp_path: Path,
    live: MilestonePlanView,
    *,
    activate: bool = True,
    register_adapter: bool = True,
):
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
    if register_adapter:
        registry.register(recording)
    dispatcher = ExecutionDispatcher(registry, state_store=exec_store)
    service = TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
    )
    coordinator_id: str | None = None
    if activate:
        handle = activate_milestone(
            store=coord_store,
            plan_view=live,
            origin_task_main_session_ref="sess-af51w1",
            execution_dispatcher=dispatcher,
            executor_id="af51w1-test",
            project_id=PROJECT_ID,
        )
        coordinator_id = handle.coordinator_id
    binding = _task_main_binding(
        sandbox, live, coord_store, exec_store, dispatcher, coordinator_id, service
    )
    return SimpleNamespace(
        live=live,
        sandbox=sandbox,
        coord_store=coord_store,
        exec_store=exec_store,
        dispatcher=dispatcher,
        recording=recording,
        binding=binding,
        service=service,
        coordinator_id=coordinator_id,
    )


def _state(env):
    state = env.coord_store.get(env.coordinator_id)
    assert state is not None
    return state


def _write_grounded(env, *, semantic: dict | None = None):
    return dispatch_via_core(
        "handoff.write",
        {"mode": "work_item", "payload": semantic or _semantic()},
        env.binding,
    )


def _start(env, ref: str, *, role: str = "coder"):
    return dispatch_via_core("task.start", {"role": role, "handoff_ref": ref}, env.binding)


def _seed_dispatched_record(env, task_id: str) -> None:
    """Seed a durable DISPATCHED execution record without coordinator adoption."""
    store = env.exec_store
    record = DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="af51w1-test",
        package_id=f"{task_id}:pkg",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"attempt-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint="f" * 32,
        execution_phase=ExecutionPhase.PREPARED,
        canonical_task_state=CanonicalTaskState.CREATED,
        origin_session_ref=OriginSessionRef(value="sess-af51w1"),
        admission_scope="af51w1-test",
    )
    store.create(record)
    store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": f"ref-handle-{task_id}",
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-09-13T00:00:00+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING,
        },
    )


def _complete_dispatched_record(env, task_id: str) -> None:
    """Project durable terminal truth + a deterministic CARD for the exact id."""
    store = env.exec_store
    record = store.get(task_id)
    assert record is not None
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="af51w1-test",
        result_data={"proof": "af51w1"},
        correlation_id=f"corr-{task_id}",
    )
    card = project_worker_result_card(
        result, ResultGovernanceProjection.success(), "coder", summary="af51 seeded terminal"
    )
    store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "canonical_task_state": CanonicalTaskState.COMPLETED,
            "terminal_result": result.to_dict(),
            "worker_result_card": card.canonical_dict(),
            "worker_result_card_digest": card.compute_card_digest(),
        },
    )


def _state_handoff_resolver(env):
    def resolve(work_item_id: str) -> TaskHandoff:
        state = env.coord_store.get(env.coordinator_id)
        assert state is not None
        return resolve_task_main_work_handoff(
            state=state, work_item_id=work_item_id, live_plan_view=env.live
        )

    return resolve


def _evidence_resolver(work_item_id: str) -> GovernedWorkItemEvidence:
    return GovernedWorkItemEvidence(
        validation_evidence=FocusedValidationEvidence(
            work_item_ref=work_item_id,
            verdict=FocusedValidationVerdict.PASS,
            validation_evidence_ref=SemanticReference(ref=f"val:{work_item_id}"),
        ),
        risk_envelope=MilestoneRiskEnvelope(
            milestone_ref=MILESTONE_ID,
            default_process_depth=ProcessDepth.STANDARD,
            minimum_process_depth=ProcessDepth.STANDARD,
        ),
    )


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# 1. Normal task.start persists the durable progression state (V1)
# ---------------------------------------------------------------------------


class TestNormalPathDurableAdoption:
    def test_normal_task_start_persists_projection_status_and_exact_binding(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        assert dict(_state(env).work_projections) == {}
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        assert written.ok, written.error
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        task_id = started.payload["task_id"]
        assert task_id
        assert len(env.recording.packages) == 1

        state = _state(env)
        record = dict(state.work_projections)["W1"]
        assert record["work_item_id"] == "W1"
        assert record["milestone_id"] == live.milestone_id
        assert record["project_id"] == PROJECT_ID
        assert record["work_source_digest"] == _source_digest(live, "W1")
        assert dict(state.wi_status)["W1"] == "ACTIVE"
        binding = dict(state.bindings)["W1"]
        assert binding["canonical_task_id"] == task_id
        assert binding["handoff_ref"] == written.payload["ref"]
        assert binding["handoff_digest"] == written.payload["digest"]

    def test_grounding_identity_preserved_from_grounded_handoff(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        opened = handoff_open(written.payload["ref"], "full", sandbox=env.sandbox)
        assert _start(env, written.payload["ref"]).ok
        record = dict(_state(env).work_projections)["W1"]
        assert record["plan_authority"] == opened["envelope"]["plan_ref"] == live.plan_authority
        assert record["plan_digest"] == opened["envelope"]["provenance"]["plan_digest"] == live.plan_digest
        assert record["work_item_id"] == opened["envelope"]["work_item_id"] == "W1"
        assert (
            record["work_source_digest"]
            == opened["envelope"]["provenance"]["work_source_digest"]
            == _source_digest(live, "W1")
        )
        assert record["projection"]["objective"] == OBJECTIVE_REAL
        assert record["projection"]["bounded_scope"] == BOUNDED_SCOPE_REAL


# ---------------------------------------------------------------------------
# 2. Failure atomicity, idempotency, fail-closed conflicts (V1)
# ---------------------------------------------------------------------------


class TestFailureAtomicityAndConflicts:
    def test_failed_task_start_leaves_no_false_active_or_binding(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, register_adapter=False)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        assert written.ok, written.error
        started = _start(env, written.payload["ref"])
        assert not started.ok
        state = _state(env)
        assert dict(state.work_projections) == {}
        assert dict(state.bindings) == {}
        assert set(dict(state.wi_status).values()) == {"PENDING"}
        assert len(env.recording.packages) == 0

    def test_adoption_requires_durable_dispatched_truth(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        projection = WorkSemanticProjection(
            objective=OBJECTIVE_REAL,
            bounded_scope=BOUNDED_SCOPE_REAL,
            validation_expectations=("focused validation",),
            semantic_stop_expectations=("stop if trusted binding inconsistent",),
        )
        with pytest.raises(CoordinatorBindingError):
            env.service.adopt_normal_path_task_start(
                profile="task-main",
                coordinator_id=env.coordinator_id,
                live_plan_view=live,
                work_item_id="W1",
                canonical_task_id=f"{PROJECT_ID}:{MILESTONE_ID}:W1:ghost",
                projection=projection,
            )
        state = _state(env)
        assert dict(state.work_projections) == {}
        assert dict(state.bindings) == {}
        assert set(dict(state.wi_status).values()) == {"PENDING"}

    def test_identical_retry_is_idempotent(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        before = _state(env)
        binding = dict(before.bindings)["W1"]
        record = dict(before.work_projections)["W1"]
        retried = env.service.adopt_normal_path_task_start(
            profile="task-main",
            coordinator_id=env.coordinator_id,
            live_plan_view=live,
            work_item_id="W1",
            canonical_task_id=started.payload["task_id"],
            projection=WorkSemanticProjection.from_dict(record["projection"]),
            handoff_ref=binding["handoff_ref"],
            handoff_digest=binding["handoff_digest"],
        )
        assert retried.coordinator_revision == before.coordinator_revision
        assert dict(retried.bindings)["W1"]["canonical_task_id"] == started.payload["task_id"]

    def test_conflicting_semantic_retry_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        conflicting = WorkSemanticProjection(
            objective="a contradictory objective",
            bounded_scope=BOUNDED_SCOPE_REAL,
            validation_expectations=("focused validation",),
            semantic_stop_expectations=("stop if trusted binding inconsistent",),
        )
        with pytest.raises(WorkProjectionConflictError) as excinfo:
            env.service.adopt_normal_path_task_start(
                profile="task-main",
                coordinator_id=env.coordinator_id,
                live_plan_view=live,
                work_item_id="W1",
                canonical_task_id=started.payload["task_id"],
                projection=conflicting,
            )
        assert excinfo.value.code == "PROJECTION_CONFLICT"
        assert dict(_state(env).bindings)["W1"]["canonical_task_id"] == started.payload["task_id"]

    def test_conflicting_execution_identity_retry_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        second_task_id = f"{PROJECT_ID}:{MILESTONE_ID}:W1:second"
        _seed_dispatched_record(env, second_task_id)
        record = dict(_state(env).work_projections)["W1"]
        with pytest.raises(WorkProjectionConflictError):
            env.service.adopt_normal_path_task_start(
                profile="task-main",
                coordinator_id=env.coordinator_id,
                live_plan_view=live,
                work_item_id="W1",
                canonical_task_id=second_task_id,
                projection=WorkSemanticProjection.from_dict(record["projection"]),
            )
        assert dict(_state(env).bindings)["W1"]["canonical_task_id"] == started.payload["task_id"]


# ---------------------------------------------------------------------------
# 3. V2 component path: exact execution identity reconciles through advance_once
# ---------------------------------------------------------------------------


class TestNormalPathAdvanceReconciliation:
    def test_advance_once_reconciles_exact_task_start_execution_identity(self, tmp_path: Path) -> None:
        live, _ = _live()
        single = _single_work_view(live)
        env = _env(tmp_path, single)
        bind_execution_dispatcher(env.dispatcher)
        written = dispatch_via_core(
            "handoff.write", {"mode": "work_item", "payload": _semantic()}, env.binding
        )
        assert written.ok, written.error
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        task_id = started.payload["task_id"]

        # Exact identity is already durable in the coordinator state.
        state = _state(env)
        assert dict(state.bindings)["W1"]["canonical_task_id"] == task_id
        assert resolve_task_main_work_handoff(
            state=state, work_item_id="W1", live_plan_view=single
        ) is not None

        # The Worker completed and its durable terminal truth exists for the
        # exact same canonical task id.
        _complete_dispatched_record(env, task_id)

        outcome = runner_module.advance_milestone_once(
            coordinator_store=env.coord_store,
            execution_store=env.exec_store,
            execution_dispatcher=env.dispatcher,
            coordinator_id=env.coordinator_id,
            live_plan_view=single,
            handoff_resolver=_state_handoff_resolver(env),
            governed_evidence_resolver=_evidence_resolver,
            completion_coordinator=None,
        )
        assert outcome.disposition in runner_module.RUNNER_DISPOSITIONS
        assert outcome.reconciled_canonical_task_id == task_id
        fresh = _state(env)
        assert fresh.wi_status["W1"] == "COMPLETION_PENDING_RECONCILIATION"
        assert dict(fresh.bindings)["W1"]["canonical_task_id"] == task_id

    def test_normal_path_does_not_require_submit_work_projection(self, tmp_path: Path) -> None:
        """The projection is established mechanically; no model submit step."""

        live, _ = _live()
        env = _env(tmp_path, live)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        assert written.ok, written.error
        started = _start(env, written.payload["ref"])
        assert started.ok, started.error
        assert dict(_state(env).work_projections)


# ---------------------------------------------------------------------------
# 4. No parallel ontology / architecture preservation
# ---------------------------------------------------------------------------


class TestNoParallelOntology:
    def test_normal_path_markers(self) -> None:
        assert NORMAL_TASK_START_PERSISTS_WORK_PROJECTION is True
        assert NORMAL_TASK_START_PERSISTS_COORDINATOR_BINDING is True
        assert NORMAL_PATH_BINDING_USES_ACTUAL_TASK_START_CANONICAL_TASK_ID is True
        assert NORMAL_PATH_ADOPTION_REQUIRES_DURABLE_DISPATCHED_TRUTH is True
        assert NORMAL_PATH_ADOPTION_WRITES_PROJECTION_STATUS_BINDING_ATOMICALLY is True
        assert NORMAL_PATH_ADOPTION_INVENTS_SEMANTICS is False

    def test_no_second_projection_contract_or_coordinator(self) -> None:
        import aota_forge.runtime.completion as completion_module
        import aota_forge.runtime.task_main.coordinator as coordinator_module

        coordinator_names = [
            name for name in dir(completion_module) if name.endswith("Coordinator")
        ]
        assert coordinator_names == ["DurableCompletionCoordinator"]
        assert coordinator_module.NEW_SECOND_COORDINATOR_STATE_MODEL is False
        assert coordinator_module.TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION is True
        assert completion_module.BACKGROUND_AUTONOMOUS_LOOP_IMPLEMENTED is False
        assert runner_module.GENERIC_WORKFLOW_ENGINE_CREATED is False
