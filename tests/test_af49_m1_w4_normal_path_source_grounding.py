"""AF #49 M1/W4 — Normal-path cutover & trusted source grounding (targeted V2).

Repairs the remaining I40-B004 sub-finding ``UNGROUNDED_WORK_PROJECTION_ACCEPTED``.
The normal production path must converge to::

    authoritative WorkSource
      -> trusted task-main model-visible context
      -> LLM semantic reasoning
      -> handoff.write(mode=work_item)
      -> mechanically source-grounded durable handoff
      -> task.start
      -> existing ExecutionDispatcher path

Grounding is mechanical identity/digest binding only (plan_ref / plan_digest /
milestone_id / work_item_id / work_source_digest). The Control Plane performs
no natural-language scoring, no semantic rewrite, and no LLM judge.

Proof boundary (honest):
  PROVES=deterministic component integration: trusted task-main binding ->
         canonical ingress grounding bind/verify -> durable handoff ->
         task.start -> existing dispatcher; and fail-closed identity probes
         for stale Plan, foreign Plan, wrong Milestone, cross-Work/source,
         tampered digest, and legacy generic ungrounded projections.
  DOES_NOT_PROVE=real Hermes model visibility, real Worker dispatch, real
         parent re-entry (M1 V3), or production operational acceptance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.core.plan.read_model import WorkSourceSlice
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import (
    CONTROL_PLANE_NATURAL_LANGUAGE_SCORING,
    CONTROL_PLANE_SEMANTIC_INTERPRETATION,
    CONTROL_PLANE_SEMANTIC_REWRITE,
    GENERIC_UNGROUNDED_PROJECTION_CANNOT_BYPASS_NORMAL_PATH,
    MILESTONE_MISMATCH,
    PLAN_DIGEST_MISMATCH,
    PLAN_REF_MISMATCH,
    SOURCE_GROUNDING_KIND,
    TASK_START_SEMANTIC_INTERPRETER,
    WORK_HANDOFF_BINDS_TO_THE_SOURCE_SEEN_BY_TASK_MAIN,
    WORK_ITEM_MISMATCH,
    WORK_SOURCE_DIGEST_MISMATCH,
    WORK_SOURCE_GROUNDING_MISSING,
    MilestonePlanView,
    WorkSourceGroundingError,
    activate_milestone,
    build_model_visible_work_context,
    commit_task_main_work_projection,
    compute_work_source_digest,
    resolve_task_main_work_handoff,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.trusted_runtime_binding import TrustedTaskMainRuntimeContext
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_store import handoff_open, handoff_write
from aota_forge.work_plane.handoff_runtime import (
    WorkScopeInsufficientError,
    WorkSemanticProjection,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "proj_af49w4"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
PLAN_AUTH_FOREIGN = "wzjcccc-dotcom/aota-hermes-tools#999"
PLAN_DIGEST = "d" * 64
PLAN_DIGEST_STALE = "e" * 64
ENTRY_BASE = "a" * 40

MARKER_W1 = "AF49W4_W1_SOURCE_ALPHA"
MARKER_W2 = "AF49W4_W2_SOURCE_BETA"
MARKER_W3 = "AF49W4_W3_SOURCE_GAMMA"
MARKER_FOREIGN_M2 = "AF49W4_M2_FOREIGN_TOKEN"

OBJECTIVE_REAL = "Implement the bounded behavior described by the authoritative source slice"


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W4 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2 -> W3
M1_WORK_ITEMS=W1, W2, W3
M2_DAG=W1 -> W2
M2_WORK_ITEMS=W1, W2
M2_USER_APPROVAL_SATISFIED=no
```

## M1

#### M1/W1 — Authoritative alpha slice
Implement {MARKER_W1} bounded behavior exactly as the Plan describes.
Acceptance:
AF49W4_ALPHA_AC=PASS

#### M1/W2 — Authoritative beta slice
Implement {MARKER_W2} bounded behavior exactly as the Plan describes.

#### M1/W3 — Authoritative gamma slice
Implement {MARKER_W3} bounded behavior exactly as the Plan describes.

## M2

#### M2/W1 — Foreign milestone slice
Implement {MARKER_FOREIGN_M2} behavior.
"""


def _live(*, plan_authority: str = PLAN_AUTH, plan_digest: str = PLAN_DIGEST):
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af49w4")
    live, next_view = project_milestone_views(
        doc,
        plan_authority=plan_authority,
        plan_digest=plan_digest,
        plan_source_revision="rev-af49w4",
    )
    return live, next_view


def _semantic(*, work_item_ref: str | None = None, objective: str = OBJECTIVE_REAL) -> dict:
    sem: dict = {
        "work_role": "coder",
        "task_kind": "af49w4-normal-path",
        "objective": objective,
        "bounded_scope": "bounded source-grounded implementation scope",
        "validation_expectations": ["focused validation"],
        "semantic_stop_expectations": ["stop if trusted binding inconsistent"],
    }
    if work_item_ref is not None:
        sem["work_item_ref"] = {"ref": work_item_ref}
    return sem


def _source_digest(live: MilestonePlanView, work_item_id: str) -> str:
    source = live.get_work_source_slice(work_item_id)
    assert source is not None
    return hashlib.sha256(source.source_text.encode("utf-8")).hexdigest()


def _sandbox(tmp_path: Path, *, tag: str | None = None) -> WorktreeSandboxBoundary:
    root = tmp_path / f"wt_{tag or uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af49w4",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        worktree_id="wt-1",
        worktree_root=str(root),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


class _RecordingReferenceAdapter(ReferenceFakeExecutorAdapter):
    """Explicit test double that records dispatched packages (component proof)."""

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
) -> CanonicalDispatchBinding:
    service = TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
    )
    ctx = TrustedTaskMainRuntimeContext(
        control_service=service,
        live_plan_view=live,
        origin_task_main_session_ref="sess-af49w4",
        executor_id="af49w4-test",
        handoff_resolver=lambda wi: (_ for _ in ()).throw(
            WorkScopeInsufficientError(f"unused handoff resolver for {wi!r}")
        ),
        coordinator_id=coordinator_id,
    )
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-control",
        objective="AF49 W4 task-main control",
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


def _env(tmp_path: Path, live: MilestonePlanView, *, activate: bool = False, state_updates: dict | None = None):
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
    coordinator_id: str | None = None
    if activate:
        handle = activate_milestone(
            store=coord_store,
            plan_view=live,
            origin_task_main_session_ref="sess-af49w4",
            execution_dispatcher=dispatcher,
            executor_id="af49w4-test",
            project_id=PROJECT_ID,
        )
        coordinator_id = handle.coordinator_id
        if state_updates:
            state = coord_store.get(coordinator_id)
            assert state is not None
            coord_store.compare_and_swap(
                coordinator_id,
                state.coordinator_revision,
                state_updates,
                state.revision_token,
            )
    binding = _task_main_binding(sandbox, live, coord_store, exec_store, dispatcher, coordinator_id)
    return SimpleNamespace(
        sandbox=sandbox,
        coord_store=coord_store,
        exec_store=exec_store,
        dispatcher=dispatcher,
        recording=recording,
        binding=binding,
        coordinator_id=coordinator_id,
    )


def store_state(env):
    state = env.coord_store.get(env.coordinator_id)
    assert state is not None
    return state


def _write_grounded(env, *, payload: dict | None = None, live: MilestonePlanView | None = None):
    binding = env.binding if live is None else _task_main_binding(
        env.sandbox, live, env.coord_store, env.exec_store, env.dispatcher, env.coordinator_id
    )
    return dispatch_via_core(
        "handoff.write",
        {"mode": "work_item", "payload": payload or _semantic(work_item_ref="W1")},
        binding,
    )


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# 1-3. AC-W4-1: normal handoff.write binds trusted Plan/Work/source identity
# ---------------------------------------------------------------------------


class TestGroundedHandoffWrite:
    def test_work_item_write_binds_trusted_plan_identity(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        opened = handoff_open(resp.payload["ref"], "full", sandbox=env.sandbox)
        envelope = opened["envelope"]
        assert envelope["plan_ref"] == live.plan_authority
        assert envelope["milestone_id"] == "M1"
        assert envelope["work_item_id"] == "W1"
        assert envelope["provenance"]["plan_digest"] == live.plan_digest
        assert envelope["provenance"]["grounding"] == "task_main_authoritative_work_source"

    def test_work_item_write_binds_current_work_identity(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(
            tmp_path,
            live,
            activate=True,
            state_updates={
                "wi_status": {
                    "W1": "COMPLETION_PENDING_RECONCILIATION",
                    "W2": "PENDING",
                    "W3": "PENDING",
                },
                "wi_semantic_status": {"W1": "RECONCILED"},
            },
        )
        context = build_model_visible_work_context(
            live,
            wi_status={
                "W1": "COMPLETION_PENDING_RECONCILIATION",
                "W2": "PENDING",
                "W3": "PENDING",
            },
            wi_semantic_status={"W1": "RECONCILED"},
        )
        assert context is not None and context["work_item_id"] == "W2"
        resp = _write_grounded(env, payload=_semantic(work_item_ref="W2"))
        assert resp.ok, resp.error
        opened = handoff_open(resp.payload["ref"], "full", sandbox=env.sandbox)
        assert opened["envelope"]["work_item_id"] == "W2"
        assert opened["envelope"]["milestone_id"] == "M1"
        assert opened["envelope"]["provenance"]["work_source_digest"] == _source_digest(live, "W2")
        # The LLM semantic payload is stored verbatim (Control Plane does not rewrite).
        assert opened["semantic"]["objective"] == OBJECTIVE_REAL

    def test_source_digest_matches_w2_authoritative_definition(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        opened = handoff_open(resp.payload["ref"], "full", sandbox=env.sandbox)
        w2_context = build_model_visible_work_context(live)
        assert w2_context is not None
        assert opened["envelope"]["provenance"]["work_source_digest"] == w2_context["work_source_digest"]
        assert compute_work_source_digest(
            live.get_work_source_slice("W1").source_text
        ) == w2_context["work_source_digest"]
        # W1 source digest != sibling W2 source digest (identity, not similarity).
        assert _source_digest(live, "W1") != _source_digest(live, "W2")


# ---------------------------------------------------------------------------
# 4-5. AC-W4-2: model cannot mint/override trusted grounding
# ---------------------------------------------------------------------------


class TestModelCannotMintAuthority:
    def test_model_forged_plan_ref_rejected(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        payload = _semantic(work_item_ref="W1")
        payload["plan_ref"] = {"ref": PLAN_AUTH_FOREIGN, "digest": "f" * 64}
        resp = _write_grounded(env, payload=payload)
        assert not resp.ok
        assert resp.error["code"] == "INPUT_TYPE_INVALID"

    def test_model_forged_plan_digest_rejected(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        payload = _semantic(work_item_ref="W1")
        payload["provenance"] = {"plan_digest": PLAN_DIGEST_STALE, "work_source_digest": "f" * 64}
        resp = _write_grounded(env, payload=payload)
        assert not resp.ok
        assert resp.error["code"] == "INPUT_TYPE_INVALID"

    def test_model_forged_work_item_id_rejected(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        payload = _semantic(work_item_ref="W1")
        payload["work_item_id"] = "W2"
        resp = _write_grounded(env, payload=payload)
        assert not resp.ok
        assert resp.error["code"] == "INPUT_TYPE_INVALID"

    def test_model_forged_work_item_ref_cannot_override_current_work(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        # Current authoritative selection is W1 (ready); model claims W2.
        resp = _write_grounded(env, payload=_semantic(work_item_ref="W2"))
        assert not resp.ok
        assert resp.error["code"] == WORK_ITEM_MISMATCH

    def test_model_forged_milestone_ref_rejected(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        payload = _semantic(work_item_ref="W1")
        payload["milestone_ref"] = {"ref": "M2"}
        resp = _write_grounded(env, payload=payload)
        assert not resp.ok
        assert resp.error["code"] == MILESTONE_MISMATCH

    def test_model_forged_project_ref_rejected(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        payload = _semantic(work_item_ref="W1")
        payload["project_ref"] = {"ref": "foreign-project"}
        resp = _write_grounded(env, payload=payload)
        assert not resp.ok
        assert resp.error["code"] in ("WORK_SOURCE_GROUNDING_MISMATCH", "CROSS_SCOPE_DENIED")


# ---------------------------------------------------------------------------
# 6-7. AC-W4-3: stale Plan / wrong source fail closed at task.start
# ---------------------------------------------------------------------------


class TestTaskStartGroundingFailClosed:
    def test_stale_plan_digest_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        ref = resp.payload["ref"]
        stale_live, _ = _live(plan_digest=PLAN_DIGEST_STALE)
        stale_binding = _task_main_binding(
            env.sandbox, stale_live, env.coord_store, env.exec_store, env.dispatcher, env.coordinator_id
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": ref}, stale_binding)
        assert not denied.ok
        assert denied.error["code"] == PLAN_DIGEST_MISMATCH
        assert env.recording.packages == []

    def test_source_digest_mismatch_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        ref = resp.payload["ref"]
        tampered_slice = WorkSourceSlice(
            milestone_id="M1",
            work_item_id="W1",
            title="M1/W1 — Authoritative alpha slice",
            source_text="AF49W4_TAMPERED_SOURCE content",
        )
        tampered_live = dataclasses.replace(live, work_source_slices=(tampered_slice,))
        tampered_binding = _task_main_binding(
            env.sandbox, tampered_live, env.coord_store, env.exec_store, env.dispatcher, env.coordinator_id
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": ref}, tampered_binding)
        assert not denied.ok
        assert denied.error["code"] == WORK_SOURCE_DIGEST_MISMATCH
        assert env.recording.packages == []

    def test_tampered_durable_handoff_digest_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        ref = resp.payload["ref"]
        digest = ref.rsplit("/", 1)[-1]
        path = Path(env.sandbox.worktree_root) / ".aota" / "handoffs" / f"{digest}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["semantic"]["objective"] = "tampered by an attacker"
        path.write_text(json.dumps(data), encoding="utf-8")
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": ref}, env.binding)
        assert not denied.ok
        assert denied.error["code"] == "DIGEST_MISMATCH"
        assert env.recording.packages == []

    def test_cross_project_handoff_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        foreign_sandbox = WorktreeSandboxBoundary(
            workspace_id="ws-foreign",
            workspace_root=str(tmp_path / "foreign-root"),
            project_id="foreign-project",
            project_root=str(tmp_path / "foreign-root"),
            worktree_id="foreign-wt",
            worktree_root=str(tmp_path / "foreign-root"),
            registry_fingerprint="0" * 64,
            candidate_fingerprint="1" * 64,
        )
        foreign_handoffs = Path(foreign_sandbox.worktree_root) / ".aota" / "handoffs"
        foreign_handoffs.mkdir(parents=True, exist_ok=True)
        digest = resp.payload["ref"].rsplit("/", 1)[-1]
        artifact = Path(env.sandbox.worktree_root) / ".aota" / "handoffs" / f"{digest}.json"
        (foreign_handoffs / f"{digest}.json").write_text(
            artifact.read_text(encoding="utf-8"), encoding="utf-8"
        )
        foreign_binding = _task_main_binding(
            foreign_sandbox, live, env.coord_store, env.exec_store, env.dispatcher, env.coordinator_id
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": resp.payload["ref"]}, foreign_binding)
        assert not denied.ok
        assert denied.error["code"] == "CROSS_SCOPE_DENIED"
        assert env.recording.packages == []


# ---------------------------------------------------------------------------
# 8-10. AC-W4-4/5/6: cross-Work, cross-Milestone, cross-Plan fail closed
# ---------------------------------------------------------------------------


class TestCrossIdentityReuse:
    def test_sibling_work_reuse_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env, payload=_semantic(work_item_ref="W1"))
        assert resp.ok, resp.error
        w1_ref = resp.payload["ref"]
        # W1 completes; current authoritative Work becomes W2.
        state = env.coord_store.get(env.coordinator_id)
        assert state is not None
        env.coord_store.compare_and_swap(
            env.coordinator_id,
            state.coordinator_revision,
            {
                "wi_status": {
                    "W1": "COMPLETION_PENDING_RECONCILIATION",
                    "W2": "PENDING",
                    "W3": "PENDING",
                },
                "wi_semantic_status": {"W1": "RECONCILED"},
            },
            state.revision_token,
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": w1_ref}, env.binding)
        assert not denied.ok
        assert denied.error["code"] == WORK_ITEM_MISMATCH
        assert env.recording.packages == []

    def test_cross_milestone_reuse_fails_closed(self, tmp_path: Path) -> None:
        live, next_view = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        assert next_view is not None and next_view.milestone_id == "M2"
        m2_binding = _task_main_binding(
            env.sandbox, next_view, env.coord_store, env.exec_store, env.dispatcher, None
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": resp.payload["ref"]}, m2_binding)
        assert not denied.ok
        assert denied.error["code"] == MILESTONE_MISMATCH
        assert env.recording.packages == []

    def test_cross_plan_reuse_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        resp = _write_grounded(env)
        assert resp.ok, resp.error
        foreign_live, _ = _live(plan_authority=PLAN_AUTH_FOREIGN)
        foreign_binding = _task_main_binding(
            env.sandbox, foreign_live, env.coord_store, env.exec_store, env.dispatcher, None
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": resp.payload["ref"]}, foreign_binding)
        assert not denied.ok
        assert denied.error["code"] == PLAN_REF_MISMATCH
        assert env.recording.packages == []


# ---------------------------------------------------------------------------
# 11-12. AC-W4-7: generic ungrounded projection cannot bypass grounding
# ---------------------------------------------------------------------------


class TestUngroundedProjectionCannotBypass:
    def test_generic_ungrounded_handoff_cannot_task_start(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        # Legacy/component write path without trusted grounding fields.
        raw = handoff_write(
            mode="work_item",
            semantic=_semantic(work_item_ref="W1"),
            caller_role="task-main",
            sandbox=env.sandbox,
        )
        bind_execution_dispatcher(env.dispatcher)
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": raw.ref}, env.binding)
        assert not denied.ok
        assert denied.error["code"] == WORK_SOURCE_GROUNDING_MISSING
        assert env.recording.packages == []

    def test_submit_work_projection_binds_and_verifies_source_digest(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        state = env.coord_store.get(env.coordinator_id)
        assert state is not None
        projection = WorkSemanticProjection(
            objective=OBJECTIVE_REAL,
            bounded_scope="bounded source-grounded implementation scope",
            validation_expectations=("focused validation",),
            semantic_stop_expectations=("stop if trusted binding inconsistent",),
        )
        updated = commit_task_main_work_projection(
            store=env.coord_store,
            coordinator_id=env.coordinator_id,
            live_plan_view=live,
            work_item_id="W1",
            projection=projection,
        )
        record = dict(updated.work_projections)["W1"]
        assert record["work_source_digest"] == _source_digest(live, "W1")
        # Grounded projection resolves under the same trusted source.
        handoff = resolve_task_main_work_handoff(
            state=store_state(env), work_item_id="W1", live_plan_view=live
        )
        assert handoff.plan_ref is not None and handoff.plan_ref.digest == live.plan_digest
        # A durable projection cannot satisfy a changed authoritative source.
        tampered_slice = WorkSourceSlice(
            milestone_id="M1",
            work_item_id="W1",
            title="M1/W1 — Authoritative alpha slice",
            source_text="AF49W4_TAMPERED_SOURCE content",
        )
        tampered_live = dataclasses.replace(live, work_source_slices=(tampered_slice,))
        with pytest.raises(WorkSourceGroundingError) as excinfo:
            resolve_task_main_work_handoff(
                state=store_state(env), work_item_id="W1", live_plan_view=tampered_live
            )
        assert excinfo.value.code == WORK_SOURCE_DIGEST_MISMATCH

    def test_ungrounded_legacy_projection_cannot_resolve_under_authoritative_source(
        self, tmp_path: Path
    ) -> None:
        legacy_live, _ = _live()
        legacy_live = dataclasses.replace(legacy_live, work_source_slices=())
        env = _env(tmp_path, legacy_live, activate=True)
        projection = WorkSemanticProjection(
            objective=OBJECTIVE_REAL,
            bounded_scope="bounded source-grounded implementation scope",
            validation_expectations=("focused validation",),
            semantic_stop_expectations=("stop if trusted binding inconsistent",),
        )
        updated = commit_task_main_work_projection(
            store=env.coord_store,
            coordinator_id=env.coordinator_id,
            live_plan_view=legacy_live,
            work_item_id="W1",
            projection=projection,
        )
        assert "work_source_digest" not in dict(updated.work_projections)["W1"]
        authoritative_live, _ = _live()
        with pytest.raises(WorkSourceGroundingError) as excinfo:
            resolve_task_main_work_handoff(
                state=store_state(env), work_item_id="W1", live_plan_view=authoritative_live
            )
        assert excinfo.value.code == WORK_SOURCE_GROUNDING_MISSING

    def test_projection_for_work_without_authoritative_source_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        sibling_only = dataclasses.replace(
            live,
            work_source_slices=tuple(
                s for s in live.work_source_slices if s.work_item_id == "W2"
            ),
        )
        env = _env(tmp_path, sibling_only, activate=True)
        projection = WorkSemanticProjection(
            objective=OBJECTIVE_REAL,
            bounded_scope="bounded source-grounded implementation scope",
            validation_expectations=("focused validation",),
            semantic_stop_expectations=("stop if trusted binding inconsistent",),
        )
        with pytest.raises(WorkSourceGroundingError) as excinfo:
            commit_task_main_work_projection(
                store=env.coord_store,
                coordinator_id=env.coordinator_id,
                live_plan_view=sibling_only,
                work_item_id="W1",
                projection=projection,
            )
        assert excinfo.value.code == WORK_SOURCE_GROUNDING_MISSING


# ---------------------------------------------------------------------------
# 13. AC-W4-8: normal path requires no submit_work_projection
# ---------------------------------------------------------------------------


class TestNormalPathWithoutSyntheticProjection:
    def test_normal_path_needs_no_submit_work_projection(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        state = env.coord_store.get(env.coordinator_id)
        assert state is not None and dict(state.work_projections) == {}
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        assert written.ok, written.error
        started = dispatch_via_core(
            "task.start", {"role": "coder", "handoff_ref": written.payload["ref"]}, env.binding
        )
        assert started.ok, started.error
        assert started.payload["handoff_digest"] == written.payload["digest"]
        assert len(env.recording.packages) == 1
        # Still no synthetic projection step was required.
        reloaded = env.coord_store.get(env.coordinator_id)
        assert reloaded is not None and dict(reloaded.work_projections) == {}

    def test_normal_path_without_coordinator_state_grounds_current_ready_work(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=False)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env)
        assert written.ok, written.error
        started = dispatch_via_core(
            "task.start", {"role": "coder", "handoff_ref": written.payload["ref"]}, env.binding
        )
        assert started.ok, started.error
        assert len(env.recording.packages) == 1


# ---------------------------------------------------------------------------
# 14. AC-W4-9: Worker receives semantic handoff + trusted control envelope
# ---------------------------------------------------------------------------


class TestWorkerReceivesTrustedControl:
    def test_worker_package_carries_semantic_and_trusted_refs(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        bind_execution_dispatcher(env.dispatcher)
        semantic = _semantic(work_item_ref="W1", objective="AF49W4_LLM_AUTHORED_OBJECTIVE")
        written = _write_grounded(env, payload=semantic)
        assert written.ok, written.error
        started = dispatch_via_core(
            "task.start", {"role": "coder", "handoff_ref": written.payload["ref"]}, env.binding
        )
        assert started.ok, started.error
        assert len(env.recording.packages) == 1
        package = env.recording.packages[0]
        refs = package.working_context["refs"]
        assert refs["plan_ref"]["ref"] == live.plan_authority
        assert refs["plan_ref"]["digest"] == live.plan_digest
        assert refs["milestone_ref"]["ref"] == "M1"
        assert refs["work_item_ref"]["ref"] == "W1"
        assert refs["project_ref"]["ref"] == PROJECT_ID
        # LLM semantic content travels in the Worker-visible context/instruction.
        assert package.working_context["bounded_scope"] == semantic["bounded_scope"]
        assert "AF49W4_LLM_AUTHORED_OBJECTIVE" in package.instruction
        # Grounding metadata is not a second semantic payload.
        assert "work_source_digest" not in package.working_context
        assert MARKER_W1 not in package.instruction


# ---------------------------------------------------------------------------
# 15-16. AC-W4-10 + targeted V2 integration + mechanical-only discipline
# ---------------------------------------------------------------------------


class TestControlPlaneIsMechanical:
    def test_grounding_markers_and_no_semantic_scoring(self) -> None:
        assert SOURCE_GROUNDING_KIND == "mechanical_identity_and_digest_binding"
        assert CONTROL_PLANE_NATURAL_LANGUAGE_SCORING is False
        assert CONTROL_PLANE_SEMANTIC_REWRITE is False
        assert CONTROL_PLANE_SEMANTIC_INTERPRETATION is False
        assert TASK_START_SEMANTIC_INTERPRETER is False
        assert WORK_HANDOFF_BINDS_TO_THE_SOURCE_SEEN_BY_TASK_MAIN is True
        assert GENERIC_UNGROUNDED_PROJECTION_CANNOT_BYPASS_NORMAL_PATH is True

    def test_grounding_logic_has_no_nlp_or_similarity_machinery(self) -> None:
        import inspect

        from aota_forge.core_ingress import (
            _grounded_work_item_write_binding,
            _verify_grounded_task_start,
        )

        source = inspect.getsource(_grounded_work_item_write_binding) + inspect.getsource(
            _verify_grounded_task_start
        )
        for token in (
            "SequenceMatcher",
            "difflib",
            "fuzz",
            "embedding",
            "cosine",
            "similarity",
            "score",
            "judge",
            "natural_language",
        ):
            assert token not in source, f"non-mechanical token in grounding logic: {token!r}"

    def test_arbitrary_prose_is_never_quality_scored(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _env(tmp_path, live, activate=True)
        # Unrelated prose and source-identical prose are both accepted; only
        # identity/digest binding decides executability.
        gibberish = _write_grounded(env, payload=_semantic(work_item_ref="W1", objective="zzz unrelated prose"))
        assert gibberish.ok, gibberish.error
        source = live.get_work_source_slice("W1").source_text
        verbatim = _write_grounded(env, payload=_semantic(work_item_ref="W1", objective=source))
        assert verbatim.ok, verbatim.error
        for resp in (gibberish, verbatim):
            opened = handoff_open(resp.payload["ref"], "full", sandbox=env.sandbox)
            assert opened["envelope"]["plan_ref"] == live.plan_authority
            assert opened["envelope"]["work_item_id"] == "W1"

    def test_targeted_v2_normal_path_end_to_end_components(self, tmp_path: Path) -> None:
        # normalized Plan -> WorkSourceSlice -> MilestonePlanView
        live, _ = _live()
        slices = {s.work_item_id: s for s in live.work_source_slices}
        assert slices["W1"].milestone_id == "M1"
        # -> trusted task-main model-visible Work context
        context = build_model_visible_work_context(live)
        assert context is not None
        assert context["state"] == "AUTHORITATIVE_SOURCE_AVAILABLE"
        assert context["plan_ref"] == PLAN_AUTH and context["plan_digest"] == PLAN_DIGEST
        assert context["work_source_digest"] == _source_digest(live, "W1")
        # -> LLM-equivalent deterministic semantic payload -> handoff.write
        env = _env(tmp_path, live, activate=True)
        bind_execution_dispatcher(env.dispatcher)
        written = _write_grounded(env, payload=_semantic(work_item_ref=context["work_item_id"]))
        assert written.ok, written.error
        grounded = handoff_open(written.payload["ref"], "full", sandbox=env.sandbox)
        assert grounded["envelope"]["work_item_id"] == context["work_item_id"]
        assert grounded["envelope"]["provenance"]["work_source_digest"] == context["work_source_digest"]
        # -> task.start resolution -> existing ExecutionDispatcher path
        started = dispatch_via_core(
            "task.start", {"role": "coder", "handoff_ref": written.payload["ref"]}, env.binding
        )
        assert started.ok, started.error
        assert started.payload["status"] == "ACCEPTED"
        assert len(env.recording.packages) == 1
        # The same arbitrary semantics without grounding cannot execute.
        raw = handoff_write(
            mode="work_item", semantic=_semantic(work_item_ref="W1"), caller_role="task-main", sandbox=env.sandbox
        )
        denied = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": raw.ref}, env.binding)
        assert not denied.ok
        assert denied.error["code"] == WORK_SOURCE_GROUNDING_MISSING
        assert len(env.recording.packages) == 1


# ---------------------------------------------------------------------------
# 17. AC-W4-11: predecessor boundary markers preserved
# ---------------------------------------------------------------------------


class TestPredecessorBoundariesPreserved:
    def test_w1_w2_w3_w4_boundary_markers(self) -> None:
        from aota_forge.work_plane import task_facade

        assert task_facade.PRODUCTION_REFERENCE_FAKE_EXECUTOR_FALLBACK is False
        assert task_facade.PRODUCTION_IN_MEMORY_EXECUTION_STORE_FALLBACK is False
        assert task_facade.PRODUCTION_PROCESS_LOCAL_COMPLETION_CHANNEL is False
        assert task_facade.MISSING_PRODUCTION_DISPATCHER_FAIL_CLOSED is True
        assert task_facade.PARENT_SIDE_DURABLE_RECONCILIATION_OWNS_TERMINAL_TRUTH is True
        assert task_facade.TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE is False
        assert WORK_HANDOFF_BINDS_TO_THE_SOURCE_SEEN_BY_TASK_MAIN is True

    def test_ungrounded_view_work_item_write_fails_closed(self, tmp_path: Path) -> None:
        live, _ = _live()
        legacy_live = dataclasses.replace(live, work_source_slices=())
        env = _env(tmp_path, legacy_live, activate=True)
        resp = _write_grounded(env)
        assert not resp.ok
        assert resp.error["code"] == WORK_SOURCE_GROUNDING_MISSING
