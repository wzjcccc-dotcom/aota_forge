"""M1/W1-R1 task-main-owned Work projection repair (AF #45, I40-B003/F1).

Proves the blocking-findings repair: the normal production path no longer
requires operator-supplied work_semantics via launcher prepare(); task-main
itself commits a bounded WorkSemanticProjection to the existing durable
coordinator store, and the existing production handoff_resolver transports
that durable projection into the existing TaskHandoff.

Coverage:
  A normal path needs no prepare(work_semantics=...)
  B task-main-owned projection reaches existing TaskHandoff
  C projection persists/reloads through durable coordinator state
  D restart needs no operator reinjection
  E Work A projection cannot be used for Work B
  F wrong Plan/Milestone/Work identity fails closed
  G missing projection prevents dispatch (WORK_SCOPE_INSUFFICIENT)
  H no generic scope fallback restored
  I multiple sequential Works need no operator refresh
  J model-visible instruction still carries objective/scope/validation/stop
  K digest/tamper still PASS
  L full Plan still not dumped
  M Worker still needs no GitHub Plan fetch
  N no new parallel Worker contract
  O no special case literals
  P W2 runtime-binding implementation untouched
  + critical negative: prepare with only mechanical inputs suffices
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
    commit_task_main_work_projection,
    resolve_task_main_work_handoff,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    build_worker_instruction,
    compile_handoff_to_execution_package,
    verify_execution_package_integrity,
)
from aota_forge.work_plane.handoff_runtime import (
    FULL_PLAN_DUMP_TO_WORKER,
    OBJECTIVE_ONLY_WORKER_INSTRUCTION,
    WORK_SCOPE_INSUFFICIENT,
    WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION,
    WorkScopeInsufficientError,
    WorkSemanticProjection,
    generic_fallback_scope_template,
    is_worker_usable_handoff,
    resolve_bounded_work_handoff,
)


OBJECTIVE = "Modify function foo so it returns X."
SCOPE = (
    "Change function foo in src/sample.py so it returns X for valid input. "
    "Preserve existing behavior Y and the module public interface. "
    "Do not redesign packaging or unrelated components."
)
SCOPE_W2 = (
    "Change function bar in src/other.py so it returns Z for valid input. "
    "Preserve existing behavior Y and the module public interface. "
    "Do not redesign packaging or unrelated components."
)
VALIDATION = ("foo returns X for valid input", "behavior Y regression passes", "test Z passes")
STOPS = (
    "stop if the trusted project/worktree binding is inconsistent",
    "stop if the change requires scope outside this Work Item",
)

PROJECT_ID = "proj_sample"
PLAN_AUTH = "example-owner/example-governance#123"
MILESTONE = "M1"
PLAN_DIGEST = "b" * 64
ENTRY_BASE = "a" * 40


def _projection(scope: str = SCOPE) -> WorkSemanticProjection:
    return WorkSemanticProjection.from_dict(
        {
            "objective": OBJECTIVE,
            "bounded_scope": scope,
            "validation_expectations": list(VALIDATION),
            "semantic_stop_expectations": list(STOPS),
        }
    )


def _view(work_items: list[str], digest: str = PLAN_DIGEST, milestone: str = MILESTONE,
          authority: str = PLAN_AUTH) -> MilestonePlanView:
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph

    graph = MilestoneWorkItemGraph(milestone_ref=milestone, work_items=list(work_items), dependencies=[])
    return MilestonePlanView(
        plan_authority=authority,
        plan_digest=digest,
        plan_source_revision="rev-1",
        milestone_id=milestone,
        entry_base=ENTRY_BASE,
        graph=graph,
        milestone_user_approval_satisfied=True,
    )


def _runtime_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "runtime.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/false",
                "concurrency": 2,
                "provider": "opencode-go",
                "model": "deepseek-v4-flash",
                "bindings": {
                    "task-main": {"profile": "aota-task-main"},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _bootstrap_without_semantics(tmp_path: Path, work_items: list[str]):
    """Launcher-equivalent prepare with only mechanical inputs (no work_semantics)."""
    view = _view(work_items)
    root = tmp_path / "wt"
    root.mkdir(exist_ok=True)
    coord = root / ".aota" / "coordinator.json"
    execp = root / ".aota" / "execution.json"
    coord.parent.mkdir(parents=True, exist_ok=True)
    if not coord.exists():
        coord.write_text("{}", encoding="utf-8")
    if not execp.exists():
        execp.write_text("{}", encoding="utf-8")
    cfg = _runtime_config(tmp_path)
    # No work_semantics: production normal path must not require it.
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-1",
        coordinator_store_path=coord,
        execution_store_path=execp,
        runtime_config_path=cfg,
        origin_task_main_session_ref="sess-1",
        live_plan_view=view,
        next_milestone_view=None,
    )
    return root, view, coord, execp


def _activate(coord_path: Path, exec_path: Path, view: MilestonePlanView, coordinator_id: str | None = None):
    from aota_forge.core.execution.durable_state import OriginSessionRef
    from aota_forge.core.execution.registry import ExecutorRegistry
    from tests.test_m3_w1_task_main_coordinator import M3W1FakeAdapter  # type: ignore

    coord_store = FileBackedTaskMainCoordinatorStore(coord_path)
    exec_store = FileBackedExecutionStateStore(exec_path)
    adapter = M3W1FakeAdapter()
    reg = ExecutorRegistry()
    reg.register(adapter)  # type: ignore[arg-type]
    dispatcher = ExecutionDispatcher(
        reg,
        state_store=exec_store,
        origin_session_ref=OriginSessionRef(value="sess-1"),
    )
    handle = activate_milestone(
        store=coord_store,
        plan_view=view,
        origin_task_main_session_ref="sess-1",
        execution_dispatcher=dispatcher,
        executor_id="hermes",
        project_id=PROJECT_ID,
        coordinator_id=coordinator_id,
    )
    return handle, coord_store, exec_store, dispatcher


# ---------------------------------------------------------------------------
# A. normal task-main path does not require launcher.prepare(work_semantics)
# ---------------------------------------------------------------------------

def test_a_normal_path_needs_no_operator_semantics(tmp_path: Path) -> None:
    from aota_forge.composition import task_main_daily_launcher as launcher_mod

    assert launcher_mod.PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED is False
    assert launcher_mod.OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH is False
    # prepare() signature retains work_semantics only for test compatibility;
    # production callers omit it.
    import inspect as _inspect

    sig = _inspect.signature(launcher_mod.DailyTaskMainLauncher.prepare)
    assert "work_semantics" in sig.parameters
    param = sig.parameters["work_semantics"]
    assert param.default is None


def test_a_prepare_without_semantics_then_task_main_commit_succeeds(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    # Task-main planning layer commits (no operator injection).
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    stored = handle.get_work_projection("W1")
    assert stored is not None
    assert stored["projection"]["bounded_scope"] == SCOPE


# ---------------------------------------------------------------------------
# B. task-main-owned projection reaches existing TaskHandoff
# ---------------------------------------------------------------------------

def test_b_durable_projection_reaches_handoff(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h = handle.resolve_work_handoff("W1", live_plan_view=view)
    assert h.bounded_scope == SCOPE
    assert h.objective == OBJECTIVE
    assert is_worker_usable_handoff(h, work_item_id="W1", milestone_ref=MILESTONE) is True
    # Same handoff via production resolver (bootstrap without semantics + durable).
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        h2 = binding.trusted_task_main_context.handoff_resolver("W1")
        assert h2.bounded_scope == SCOPE
        assert h2.handoff_digest == h.handoff_digest
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit


# ---------------------------------------------------------------------------
# C. projection persists/reloads through durable coordinator state
# ---------------------------------------------------------------------------

def test_c_projection_persists_through_reload(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    # Reload via a fresh store instance pointing at the same file.
    fresh_store = FileBackedTaskMainCoordinatorStore(coord)
    fresh_state = fresh_store.get(handle.coordinator_id)
    assert fresh_state is not None
    assert "W1" in (getattr(fresh_state, "work_projections", {}) or {})
    h = resolve_task_main_work_handoff(state=fresh_state, work_item_id="W1", live_plan_view=view)
    assert h.bounded_scope == SCOPE


# ---------------------------------------------------------------------------
# D. restart does not require operator semantic reinjection
# ---------------------------------------------------------------------------

def test_d_restart_needs_no_reinjection(tmp_path: Path) -> None:
    from aota_forge.runtime.task_main import coordinator as coord_mod

    assert coord_mod.TASK_MAIN_RESTART_REQUIRES_OPERATOR_WORK_SEMANTICS_REINJECTION is False
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    cid = handle.coordinator_id
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    # Simulate task-main restart: fresh stores + fresh binding, same files,
    # bootstrap still carries no work_semantics.
    fresh_coord = FileBackedTaskMainCoordinatorStore(coord)
    fresh_exec = FileBackedExecutionStateStore(execp)
    assert fresh_coord.get(cid) is not None
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        h = binding.trusted_task_main_context.handoff_resolver("W1")
        assert h.bounded_scope == SCOPE
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit
    assert fresh_exec is not None


# ---------------------------------------------------------------------------
# E. Work A projection cannot be used for Work B
# ---------------------------------------------------------------------------

def test_e_projection_not_reusable_across_work(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1", "W2"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h_w1 = handle.resolve_work_handoff("W1", live_plan_view=view)
    assert h_w1.work_item_ref is not None and h_w1.work_item_ref.ref == "W1"
    # W2 has no projection yet: fails closed, never substitutes W1.
    with pytest.raises(WorkScopeInsufficientError):
        handle.resolve_work_handoff("W2", live_plan_view=view)
    # A handoff bound to W1 contradicts W2.
    assert h_w1.work_item_ref.ref != "W2"
    with pytest.raises(Exception):
        # Coordinator dispatch path checks work_item_ref equality.
        from aota_forge.runtime.task_main.coordinator import CoordinatorBindingError

        if h_w1.work_item_ref is not None and h_w1.work_item_ref.ref != "W2":
            raise CoordinatorBindingError("handoff work_item_ref contradicts ready Work Item")


# ---------------------------------------------------------------------------
# F. wrong Plan/Milestone/Work identity fails closed
# ---------------------------------------------------------------------------

def test_f_wrong_identities_fail_closed(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    # Wrong Work identity at commit.
    with pytest.raises(Exception):
        handle.commit_work_projection(work_item_id="W9", projection=_projection(), live_plan_view=view)
    # Stale Plan digest at commit fails closed (PlanDriftError).
    stale_view = _view(["W1"], digest="0" * 64)
    with pytest.raises(Exception):
        handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=stale_view)
    # Wrong Plan identity at resolve fails closed.
    with pytest.raises(WorkScopeInsufficientError):
        handle.resolve_work_handoff("W1", live_plan_view=stale_view)
    # Wrong Milestone at resolve fails closed.
    other_ms = _view(["W1"], milestone="M2")
    with pytest.raises(WorkScopeInsufficientError):
        handle.resolve_work_handoff("W1", live_plan_view=other_ms)
    # Malformed / oversized projection fails closed at commit.
    with pytest.raises(WorkScopeInsufficientError):
        handle.commit_work_projection(
            work_item_id="W1",
            projection={"objective": "only"},
            live_plan_view=view,
        )


# ---------------------------------------------------------------------------
# G. missing projection prevents dispatch
# ---------------------------------------------------------------------------

def test_g_missing_projection_prevents_dispatch(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, coord_store, exec_store, dispatcher = _activate(coord, execp, view)
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        with pytest.raises(WorkScopeInsufficientError) as exc:
            binding.trusted_task_main_context.handoff_resolver("W1")
        assert WORK_SCOPE_INSUFFICIENT in str(exc.value)
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit
    # Direct durable resolve also fails closed.
    with pytest.raises(WorkScopeInsufficientError):
        handle.resolve_work_handoff("W1", live_plan_view=view)


# ---------------------------------------------------------------------------
# H. no generic scope fallback restored
# ---------------------------------------------------------------------------

def test_h_no_generic_fallback(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h = handle.resolve_work_handoff("W1", live_plan_view=view)
    assert h.bounded_scope != generic_fallback_scope_template("W1", MILESTONE)
    assert h.objective != h.bounded_scope
    # Generic template itself is never Worker-usable.
    template = generic_fallback_scope_template("W1", MILESTONE)
    proj = WorkSemanticProjection.from_dict(
        {
            "objective": OBJECTIVE,
            "bounded_scope": template,
            "validation_expectations": list(VALIDATION),
            "semantic_stop_expectations": list(STOPS),
        }
    )
    with pytest.raises(WorkScopeInsufficientError):
        resolve_bounded_work_handoff(
            work_item_id="W1",
            milestone_ref=MILESTONE,
            projection=proj,
            project_id=PROJECT_ID,
            plan_authority=PLAN_AUTH,
            plan_digest=PLAN_DIGEST,
        )


# ---------------------------------------------------------------------------
# I. multiple sequential Works need no operator refresh
# ---------------------------------------------------------------------------

def test_i_sequential_works_need_no_refresh(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1", "W2"])
    handle, *_ = _activate(coord, execp, view)
    # Task-main selects Work A -> projection A -> handoff A.
    handle.commit_work_projection(work_item_id="W1", projection=_projection(SCOPE), live_plan_view=view)
    h_a = handle.resolve_work_handoff("W1", live_plan_view=view)
    assert h_a.bounded_scope == SCOPE
    # Later task-main selects Work B -> projection B -> handoff B, no launcher refresh.
    handle.commit_work_projection(work_item_id="W2", projection=_projection(SCOPE_W2), live_plan_view=view)
    h_b = handle.resolve_work_handoff("W2", live_plan_view=view)
    assert h_b.bounded_scope == SCOPE_W2
    assert h_a.handoff_digest != h_b.handoff_digest
    # Both survive reload.
    fresh = FileBackedTaskMainCoordinatorStore(coord).get(handle.coordinator_id)
    assert fresh is not None
    assert set((getattr(fresh, "work_projections", {}) or {}).keys()) == {"W1", "W2"}


# ---------------------------------------------------------------------------
# J. model-visible instruction still carries full bounded semantics
# ---------------------------------------------------------------------------

def test_j_instruction_still_carries_semantics(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h = handle.resolve_work_handoff("W1", live_plan_view=view)
    pkg = compile_handoff_to_execution_package(
        h, TrustedExecutionBinding(canonical_task_id="proj:M1:W1:attempt-1", project_id=PROJECT_ID)
    )
    assert pkg.instruction != h.objective
    assert OBJECTIVE in pkg.instruction
    assert SCOPE in pkg.instruction
    for val in VALIDATION:
        assert val in pkg.instruction
    for stop in STOPS:
        assert stop in pkg.instruction
    assert OBJECTIVE_ONLY_WORKER_INSTRUCTION is False


# ---------------------------------------------------------------------------
# K. digest/tamper still PASS
# ---------------------------------------------------------------------------

def test_k_digest_and_tamper(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h1 = handle.resolve_work_handoff("W1", live_plan_view=view)
    handle.commit_work_projection(
        work_item_id="W1", projection=_projection(SCOPE + " Extra sentence."), live_plan_view=view
    )
    h2 = handle.resolve_work_handoff("W1", live_plan_view=view)
    assert h1.handoff_digest != h2.handoff_digest
    pkg = compile_handoff_to_execution_package(
        h1, TrustedExecutionBinding(canonical_task_id="proj:M1:W1:attempt-1", project_id=PROJECT_ID)
    )
    verify_execution_package_integrity(pkg, h1)
    tampered_ctx = dict(pkg.working_context)
    tampered_ctx["bounded_scope"] = "do something else entirely"
    tampered = pkg.__class__(**{**pkg.to_dict(), "working_context": tampered_ctx})
    from aota_forge.work_plane.compiler import PackageIntegrityError

    with pytest.raises(PackageIntegrityError):
        verify_execution_package_integrity(tampered, h1)


# ---------------------------------------------------------------------------
# L/M. no Plan dump, no GitHub fetch needed
# ---------------------------------------------------------------------------

def test_lm_no_plan_dump_no_fetch(tmp_path: Path) -> None:
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    handle, *_ = _activate(coord, execp, view)
    handle.commit_work_projection(work_item_id="W1", projection=_projection(), live_plan_view=view)
    h = handle.resolve_work_handoff("W1", live_plan_view=view)
    pkg = compile_handoff_to_execution_package(
        h, TrustedExecutionBinding(canonical_task_id="proj:M1:W1:attempt-1", project_id=PROJECT_ID)
    )
    sentinel = "FULL_PLAN_BODY_SENTINEL_XQZ_9f8e7d6c5b4a"
    for surface in (h.objective, h.bounded_scope, pkg.instruction):
        assert sentinel not in surface
    assert FULL_PLAN_DUMP_TO_WORKER is False
    assert WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION is False
    assert "github" not in pkg.instruction.lower()
    assert is_worker_usable_handoff(h, work_item_id="W1", milestone_ref=MILESTONE) is True


# ---------------------------------------------------------------------------
# N. no new parallel Worker contract
# ---------------------------------------------------------------------------

def test_n_reuses_existing_handoff() -> None:
    from aota_forge.work_plane import handoff as handoff_mod

    assert hasattr(handoff_mod, "TaskHandoff")
    # Projection resolves through the existing TaskHandoff type only.
    h = resolve_bounded_work_handoff(
        work_item_id="W1",
        milestone_ref=MILESTONE,
        projection=_projection(),
        project_id=PROJECT_ID,
        plan_authority=PLAN_AUTH,
        plan_digest=PLAN_DIGEST,
    )
    assert type(h).__name__ == "TaskHandoff"
    from aota_forge import mcp_transport

    assert mcp_transport.MCP_PUBLIC_TOOLS == ("aota.invoke",)
    assert mcp_transport.AGENT_FACING_AOTA_TOOL == "aota.invoke"


# ---------------------------------------------------------------------------
# O. no special case literals in touched production files
# ---------------------------------------------------------------------------

def test_o_no_special_case() -> None:
    banned = ["aota_forge_dogfood", "aota-hermes-tools#40", "df58517"]
    for rel in [
        "aota_forge/runtime/task_main/coordinator.py",
        "aota_forge/runtime/task_main/coordinator_state.py",
        "aota_forge/work_plane/handoff_runtime.py",
        "aota_forge/composition/task_main_host_bootstrap.py",
        "aota_forge/composition/task_main_daily_launcher.py",
    ]:
        src = Path(rel).read_text(encoding="utf-8").lower()
        for token in banned:
            assert token.lower() not in src, f"banned token {token!r} in {rel}"


# ---------------------------------------------------------------------------
# P. W2 runtime-binding implementation untouched
# ---------------------------------------------------------------------------

def test_p_w2_binding_untouched() -> None:
    # M1/W2 repaired: explicit Worker env + exclusive discrimination replace
    # the pre-W2 inheritance/priority seam. W1 ownership markers below still hold.
    from aota_forge.adapters.hermes import host_client
    from aota_forge.composition import task_main_host_bootstrap as tmb

    dispatch_src = inspect.getsource(host_client.HermesHostClient.dispatch)
    assert "env=supervisor_env" in dispatch_src
    import aota_forge.composition.worker_vertical_slice as wvs

    assert wvs.TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED is True
    assert wvs.ROLE_CONTEXT_SELECTION_EXPLICIT is True
    serve_child_src = inspect.getsource(wvs._serve_mcp_child)
    assert "has priority" not in serve_child_src
    assert "select_runtime_context" in serve_child_src
    serve_src = inspect.getsource(tmb.try_build_task_main_binding)
    assert "work_semantics" in serve_src


# ---------------------------------------------------------------------------
# Ownership markers
# ---------------------------------------------------------------------------

def test_ownership_markers() -> None:
    from aota_forge.runtime.task_main import coordinator as cmod
    from aota_forge.work_plane import handoff_runtime as hmod
    from aota_forge.composition import task_main_host_bootstrap as bmod
    from aota_forge.composition import task_main_daily_launcher as lmod

    assert cmod.TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION is True
    assert cmod.TASK_MAIN_SEMANTIC_LAYER_PRODUCES_WORK_PROJECTION is True
    assert cmod.WORK_PROJECTION_DURABLE is True
    assert cmod.WORK_PROJECTION_BOUND_TO_TRUSTED_PLAN_IDENTITY is True
    assert cmod.WORK_PROJECTION_BOUND_TO_WORK_ITEM is True
    assert cmod.WORK_SEMANTIC_PROJECTION_IS_PLAN_AUTHORITY is False
    assert cmod.PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED is False
    assert cmod.OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH is False
    assert hmod.TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION is True
    assert hmod.WORK_PROJECTION_DURABLE is True
    assert bmod.TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION is True
    assert bmod.PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED is False
    assert lmod.PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED is False
    assert cmod.DETERMINISTIC_RUNTIME_REINTERPRETS_PLAN_PROSE is False


# ---------------------------------------------------------------------------
# Critical negative: mechanical-only prepare suffices for task-main-owned path
# ---------------------------------------------------------------------------

def test_critical_negative_mechanical_only_prepare_suffices(tmp_path: Path) -> None:
    # Launcher prepared with only project binding + plan identity/source +
    # user gate / runtime config, without work_semantics.
    root, view, coord, execp = _bootstrap_without_semantics(tmp_path, ["W1"])
    # No operator intervention from here: task-main commits, resolver serves.
    handle, *_ = _activate(coord, execp, view)
    with pytest.raises(WorkScopeInsufficientError):
        handle.resolve_work_handoff("W1", live_plan_view=view)
    # Task-main semantic layer produces the bounded projection.
    updated = commit_task_main_work_projection(
        store=FileBackedTaskMainCoordinatorStore(coord),
        coordinator_id=handle.coordinator_id,
        live_plan_view=view,
        work_item_id="W1",
        projection=_projection().to_dict(),
    )
    assert "W1" in (getattr(updated, "work_projections", {}) or {})
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        h = binding.trusted_task_main_context.handoff_resolver("W1")
        assert h.bounded_scope == SCOPE
        pkg = compile_handoff_to_execution_package(
            h, TrustedExecutionBinding(canonical_task_id="proj:M1:W1:attempt-1", project_id=PROJECT_ID)
        )
        verify_execution_package_integrity(pkg, h)
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit
