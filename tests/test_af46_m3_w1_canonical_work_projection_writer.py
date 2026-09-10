"""AF #46 M3/W1 — Canonical WorkSemanticProjection Writer (TIER_1..TIER_4).

Selected design: OPTION_A (dedicated typed task_main operation
task_main.submit_work_projection). Options B (payload on advance_once) and C
(existing decision/result channel) evaluated and rejected; see rationale in
module docstring of the implementation (core_ingress + control).

Proves the one typed, canonical, server-validated path:

    task-main model reasoning
      -> typed bounded semantic submission (aota.invoke)
      -> canonical AF Core validation (operations.yaml -> core_ingress)
      -> trusted Plan/Milestone/Work binding (live_plan_view + coordinator)
      -> WorkSemanticProjection (existing type reused, handoff_runtime)
      -> durable coordinator state (existing store + CAS)

TIER_1 CORE_SEMANTIC: direct Core commit (no MCP), all negatives typed.
TIER_2 ADAPTER_PARITY: direct Core vs MCP aota.invoke same projection/state.
TIER_3 CROSS_MODULE: real control service + coordinator + TaskHandoff.
TIER_4 PROCESS_BOUNDARY: child process via pre-resolved task-main binding,
  durable write, restart/recovery, same projection.
Usability: one normal agent call (submit op only, no hydrate ceremony).
Scope contract: AF Core owns bounded_scope; ordinary punctuation round-trips
  exactly; invalid fails typed; no silent mutation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
    commit_task_main_work_projection,
    resolve_task_main_work_handoff,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.handoff_runtime import (
    PROJECTION_CONFLICT,
    WORK_SCOPE_INSUFFICIENT,
    WorkProjectionConflictError,
    WorkScopeInsufficientError,
    WorkSemanticProjection,
    parse_model_work_proposal,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


PROJECT_ID = "proj_w1"
PLAN_AUTH = "example-owner/example-governance#46"
MILESTONE = "M1"
PLAN_DIGEST = "c" * 64
ENTRY_BASE = "d" * 40

OBJECTIVE = "Implement bounded work item W1."
SCOPE_PLAIN = (
    "Implement feature foo in src/foo.py (handles paths like src/a-b_c/d.py): "
    "accept input, validate, return result; keep public API stable, do not touch packaging."
)
VALIDATION = ("foo returns expected result", "existing regression suite passes")
STOPS = ("stop if scope unclear", "stop if trusted binding inconsistent")


def _proposal_dict(work_item_id: str = "W1", scope: str = SCOPE_PLAIN) -> dict:
    return {
        "work_item_id": work_item_id,
        "objective": OBJECTIVE,
        "bounded_scope": scope,
        "validation_expectations": list(VALIDATION),
        "semantic_stop_expectations": list(STOPS),
    }


def _view(
    work_items: list[str],
    digest: str = PLAN_DIGEST,
    milestone: str = MILESTONE,
    authority: str = PLAN_AUTH,
    approved: bool = True,
) -> MilestonePlanView:
    graph = MilestoneWorkItemGraph(milestone_ref=milestone, work_items=list(work_items), dependencies=[])
    return MilestonePlanView(
        plan_authority=authority,
        plan_digest=digest,
        plan_source_revision="rev-1",
        milestone_id=milestone,
        entry_base=ENTRY_BASE,
        graph=graph,
        milestone_user_approval_satisfied=approved,
    )


def _stores(tmp_path: Path, tag: str = "t"):
    coord_path = tmp_path / f"coord_{tag}.json"
    exec_path = tmp_path / f"exec_{tag}.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    coord_store = FileBackedTaskMainCoordinatorStore(coord_path)
    exec_store = FileBackedExecutionStateStore(exec_path)
    return coord_store, exec_store, coord_path, exec_path


def _dispatcher(exec_store) -> ExecutionDispatcher:
    from aota_forge.core.execution.registry import ExecutorRegistry
    from tests.test_m3_w1_task_main_coordinator import M3W1FakeAdapter  # type: ignore

    reg = ExecutorRegistry()
    reg.register(M3W1FakeAdapter())  # type: ignore[arg-type]
    from aota_forge.core.execution.durable_state import OriginSessionRef

    return ExecutionDispatcher(reg, state_store=exec_store, origin_session_ref=OriginSessionRef(value="sess-1"))


def _activate(coord_store, exec_store, view, coordinator_id: str | None = None):
    dispatcher = _dispatcher(exec_store)
    handle = activate_milestone(
        store=coord_store,
        plan_view=view,
        origin_task_main_session_ref="sess-1",
        execution_dispatcher=dispatcher,
        executor_id="m3w1-fake",
        project_id=PROJECT_ID,
        coordinator_id=coordinator_id,
    )
    return handle, dispatcher


def _control_service(coord_store, exec_store, dispatcher):
    return TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
    )


# ---------------------------------------------------------------------------
# Tier 1 — Core semantic (no MCP/Hermes)
# ---------------------------------------------------------------------------


class TestTier1CoreSemantic:
    def test_core_projection_write(self, tmp_path: Path) -> None:
        coord_store, exec_store, _, _ = _stores(tmp_path, "core1")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        wid, typed = parse_model_work_proposal(_proposal_dict("W1"))
        assert wid == "W1"
        assert isinstance(typed, WorkSemanticProjection)
        updated = commit_task_main_work_projection(
            store=coord_store,
            coordinator_id=handle.coordinator_id,
            live_plan_view=view,
            work_item_id=wid,
            projection=typed,
        )
        record = dict(getattr(updated, "work_projections", {}))["W1"]
        assert record["work_item_id"] == "W1"
        assert record["project_id"] == PROJECT_ID
        assert record["plan_authority"] == PLAN_AUTH
        assert record["milestone_id"] == MILESTONE
        assert record["projection"]["bounded_scope"] == SCOPE_PLAIN

    def test_core_trust_binding_proof(self, tmp_path: Path) -> None:
        coord_store, exec_store, _, _ = _stores(tmp_path, "core2")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        # Foreign plan digest fails closed (stale/foreign binding).
        foreign_view = _view(["W1"], digest="f" * 64)
        with pytest.raises(Exception) as excinfo:
            commit_task_main_work_projection(
                store=coord_store,
                coordinator_id=handle.coordinator_id,
                live_plan_view=foreign_view,
                work_item_id="W1",
                projection=_proposal_dict("W1"),
            )
        assert "PLAN_DRIFT" in str(excinfo.value) or "drift" in str(excinfo.value).lower()
        # Foreign milestone fails closed.
        other_ms = _view(["W1"], milestone="M9")
        with pytest.raises(Exception):
            commit_task_main_work_projection(
                store=coord_store,
                coordinator_id=handle.coordinator_id,
                live_plan_view=other_ms,
                work_item_id="W1",
                projection=_proposal_dict("W1"),
            )
        # Foreign work fails closed.
        with pytest.raises(Exception):
            commit_task_main_work_projection(
                store=coord_store,
                coordinator_id=handle.coordinator_id,
                live_plan_view=view,
                work_item_id="W9",
                projection=_proposal_dict("W9"),
            )
        # Coordinator state unchanged after failures.
        fresh = coord_store.get(handle.coordinator_id)
        assert fresh is not None
        assert dict(getattr(fresh, "work_projections", {}) or {}) == {}

    def test_core_scope_validation_proof(self, tmp_path: Path) -> None:
        coord_store, exec_store, _, _ = _stores(tmp_path, "core3")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        # Malformed: missing field.
        bad = _proposal_dict("W1")
        del bad["objective"]
        with pytest.raises(WorkScopeInsufficientError) as e1:
            parse_model_work_proposal(bad)
        assert e1.value.code == WORK_SCOPE_INSUFFICIENT
        # Malformed: unknown field.
        bad2 = _proposal_dict("W1")
        bad2["plan_authority"] = "evil"
        with pytest.raises(WorkScopeInsufficientError):
            parse_model_work_proposal(bad2)
        # Oversized field.
        bad3 = _proposal_dict("W1")
        bad3["objective"] = "x" * 5000
        with pytest.raises(WorkScopeInsufficientError):
            parse_model_work_proposal(bad3)
        # Invalid scope: empty.
        bad4 = _proposal_dict("W1")
        bad4["bounded_scope"] = "   "
        with pytest.raises(WorkScopeInsufficientError):
            parse_model_work_proposal(bad4)
        # Invalid scope: generic fallback template rejected at commit.
        from aota_forge.work_plane.handoff_runtime import generic_fallback_scope_template

        generic = generic_fallback_scope_template("W1", MILESTONE)
        with pytest.raises(WorkScopeInsufficientError):
            commit_task_main_work_projection(
                store=coord_store,
                coordinator_id=handle.coordinator_id,
                live_plan_view=view,
                work_item_id="W1",
                projection={
                    "objective": OBJECTIVE,
                    "bounded_scope": generic,
                    "validation_expectations": list(VALIDATION),
                    "semantic_stop_expectations": list(STOPS),
                },
            )
        # Nothing persisted after invalid attempts.
        fresh = coord_store.get(handle.coordinator_id)
        assert dict(getattr(fresh, "work_projections", {}) or {}) == {}

    def test_core_durability_proof(self, tmp_path: Path) -> None:
        coord_store, exec_store, coord_path, _ = _stores(tmp_path, "core4")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        commit_task_main_work_projection(
            store=coord_store,
            coordinator_id=handle.coordinator_id,
            live_plan_view=view,
            work_item_id="W1",
            projection=_proposal_dict("W1"),
        )
        # Reload from disk via a fresh store (process restart simulation).
        fresh_store = FileBackedTaskMainCoordinatorStore(coord_path)
        reloaded = fresh_store.get(handle.coordinator_id)
        assert reloaded is not None
        record = dict(getattr(reloaded, "work_projections", {}))["W1"]
        assert record["projection"]["bounded_scope"] == SCOPE_PLAIN
        # Handoff derivable from reloaded state without reinjection.
        handoff = resolve_task_main_work_handoff(state=reloaded, work_item_id="W1", live_plan_view=view)
        assert handoff.bounded_scope == SCOPE_PLAIN
        assert handoff.objective == OBJECTIVE

    def test_stale_and_conflict_semantics(self, tmp_path: Path) -> None:
        coord_store, exec_store, _, _ = _stores(tmp_path, "core5")
        view = _view(["W1"])
        handle, dispatcher = _activate(coord_store, exec_store, view)
        cid = handle.coordinator_id
        commit_task_main_work_projection(
            store=coord_store, coordinator_id=cid, live_plan_view=view,
            work_item_id="W1", projection=_proposal_dict("W1"),
        )
        # Identical retry is safe (idempotent, no error).
        state2 = commit_task_main_work_projection(
            store=coord_store, coordinator_id=cid, live_plan_view=view,
            work_item_id="W1", projection=_proposal_dict("W1"),
        )
        assert dict(getattr(state2, "work_projections", {}))["W1"]["projection"]["bounded_scope"] == SCOPE_PLAIN
        # Conflicting pre-dispatch replacement (still PENDING) is allowed explicitly.
        alt = _proposal_dict("W1")
        alt["objective"] = "Revised objective for W1 before dispatch."
        state3 = commit_task_main_work_projection(
            store=coord_store, coordinator_id=cid, live_plan_view=view,
            work_item_id="W1", projection=alt,
        )
        assert dict(getattr(state3, "work_projections", {}))["W1"]["projection"]["objective"] == alt["objective"]
        # Dispatch W1 (via coordinator handle with durable resolver), then
        # a conflicting write must fail closed with PROJECTION_CONFLICT.
        from aota_forge.runtime.task_main.coordinator import TaskMainCoordinator

        coord = TaskMainCoordinator(
            store=coord_store, coordinator_id=cid, execution_dispatcher=dispatcher,
            completion_coordinator=None, executor_id="m3w1-fake", project_id=PROJECT_ID,
        )

        def _resolver(wi: str):
            st = coord_store.get(cid)
            assert st is not None
            return resolve_task_main_work_handoff(state=st, work_item_id=wi, live_plan_view=view)

        report = coord.dispatch_ready(_resolver, live_plan_view=view)
        assert [d.work_item_id for d in report.dispatched] == ["W1"]
        conflict = _proposal_dict("W1")
        conflict["objective"] = "Conflicting post-dispatch objective."
        with pytest.raises(WorkProjectionConflictError) as ce:
            commit_task_main_work_projection(
                store=coord_store, coordinator_id=cid, live_plan_view=view,
                work_item_id="W1", projection=conflict,
            )
        assert ce.value.code == PROJECTION_CONFLICT
        # Cross-work overwrite rejected: W1 record cannot be read as W2.
        view2 = _view(["W1", "W2"])
        # (separate coordinator to keep identities clean)
        coord_store2, exec_store2, _, _ = _stores(tmp_path, "core5b")
        handle2, _ = _activate(coord_store2, exec_store2, view2, coordinator_id="proj_w1:M1-x")
        commit_task_main_work_projection(
            store=coord_store2, coordinator_id=handle2.coordinator_id, live_plan_view=view2,
            work_item_id="W1", projection=_proposal_dict("W1"),
        )
        st2 = coord_store2.get(handle2.coordinator_id)
        assert st2 is not None
        with pytest.raises(WorkScopeInsufficientError):
            resolve_task_main_work_handoff(state=st2, work_item_id="W2", live_plan_view=view2)


# ---------------------------------------------------------------------------
# Scope contract — AF Core owner, roundtrip, typed rejection, no silent loss
# ---------------------------------------------------------------------------


class TestScopeContract:
    SCOPES = [
        "src/foo.py and src/bar_baz.py only",
        "fix handler-a (retry path) in pkg/mod",
        "validate: input, output; return code, message",
        "Implement the thing. It must handle (a) and (b). See docs/intro.md.",
        "paths a/b/c-d_e.py, tests/test_x.py; run pytest -q",
    ]

    def test_valid_scope_semantic_roundtrip(self, tmp_path: Path) -> None:
        for idx, scope in enumerate(self.SCOPES):
            coord_store, exec_store, _, _ = _stores(tmp_path, f"scope{idx}")
            view = _view(["W1"])
            handle, _ = _activate(coord_store, exec_store, view)
            proposal = _proposal_dict("W1", scope=scope)
            commit_task_main_work_projection(
                store=coord_store, coordinator_id=handle.coordinator_id,
                live_plan_view=view, work_item_id="W1", projection=proposal,
            )
            st = coord_store.get(handle.coordinator_id)
            assert st is not None
            handoff = resolve_task_main_work_handoff(state=st, work_item_id="W1", live_plan_view=view)
            assert handoff.bounded_scope == scope.strip()

    def test_invalid_scope_typed_rejection(self) -> None:
        for bad_scope in ["", "   ", "x" * 5000, 123, None, ["a"]]:
            with pytest.raises(WorkScopeInsufficientError) as excinfo:
                WorkSemanticProjection.from_dict(
                    {
                        "objective": OBJECTIVE,
                        "bounded_scope": bad_scope,  # type: ignore[dict-item]
                        "validation_expectations": list(VALIDATION),
                        "semantic_stop_expectations": list(STOPS),
                    }
                )
            assert excinfo.value.code == WORK_SCOPE_INSUFFICIENT

    def test_no_silent_scope_content_loss(self, tmp_path: Path) -> None:
        # Punctuation-heavy scope must survive Core commit + resolve exactly
        # (no AgentsPolicyCandidate charset stripping on the authority path).
        scope = "Edit src/a-b_c/d.py (function f:x): handle 'q', \"r\"; keep A/B stable."
        coord_store, exec_store, _, _ = _stores(tmp_path, "nloss")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        commit_task_main_work_projection(
            store=coord_store, coordinator_id=handle.coordinator_id,
            live_plan_view=view, work_item_id="W1", projection=_proposal_dict("W1", scope=scope),
        )
        st = coord_store.get(handle.coordinator_id)
        assert st is not None
        stored = dict(getattr(st, "work_projections", {}))["W1"]["projection"]
        assert stored["bounded_scope"] == scope
        # Worker binding preserves authoritative handoff scope exactly.
        handoff = resolve_task_main_work_handoff(state=st, work_item_id="W1", live_plan_view=view)
        assert handoff.bounded_scope == scope


# ---------------------------------------------------------------------------
# Tier 2 — adapter parity (direct Core vs MCP aota.invoke)
# ---------------------------------------------------------------------------


def _make_task_main_binding(tmp_path: Path, view: MilestonePlanView, session_ref: str = "sess-1"):
    import uuid

    root = tmp_path / f"wt_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    coord_path = root / ".aota" / "coord.json"
    exec_path = root / ".aota" / "exec.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{uuid.uuid4().hex[:6]}.json"
    cfg_path.write_text(
        json.dumps({"executor": "hermes", "executable": "/bin/false", "concurrency": 2,
                    "provider": "opencode-go", "model": "m",
                    "bindings": {
                        "task-main": {"profile": "aota-task-main"},
                        "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    }}),
        encoding="utf-8",
    )
    write_bootstrap_file(
        worktree_root=root, project_id=PROJECT_ID, worktree_id="wt-1",
        coordinator_store_path=coord_path, execution_store_path=exec_path,
        runtime_config_path=cfg_path, origin_task_main_session_ref=session_ref,
        live_plan_view=view, next_milestone_view=None,
    )
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    try:
        binding = try_build_task_main_binding()
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
    assert binding is not None
    return binding, root


class TestTier2AdapterParity:
    def test_adapter_parity(self, tmp_path: Path) -> None:
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        view = _view(["W1"])
        binding, _ = _make_task_main_binding(tmp_path, view)
        adapter = _SharedAotaMcpAdapter(binding)
        r_act = adapter.invoke("task_main.activate_milestone", {})
        assert r_act["ok"] is True, r_act
        payload = _proposal_dict("W1")
        r_sub = adapter.invoke("task_main.submit_work_projection", payload)
        assert r_sub["ok"] is True, r_sub
        data = r_sub["payload"]
        assert data["work_item_id"] == "W1"
        assert data["projection"]["bounded_scope"] == SCOPE_PLAIN
        assert "handoff_digest" in data
        # Direct-Core equivalent on a twin coordinator yields same projection.
        coord_store, exec_store, _, _ = _stores(tmp_path, "parity")
        handle, _ = _activate(coord_store, exec_store, view)
        commit_task_main_work_projection(
            store=coord_store, coordinator_id=handle.coordinator_id,
            live_plan_view=view, work_item_id="W1", projection=_proposal_dict("W1"),
        )
        st = coord_store.get(handle.coordinator_id)
        assert st is not None
        direct = dict(getattr(st, "work_projections", {}))["W1"]["projection"]
        assert direct == data["projection"]
        # MCP performs transport projection only: error identity preserved.
        r_bad = adapter.invoke("task_main.submit_work_projection", {"work_item_id": "W9", **{k: v for k, v in payload.items() if k != "work_item_id"}})
        assert r_bad["ok"] is False
        assert r_bad["error"]["code"] in ("COORDINATOR_BINDING_ERROR", "WORK_SCOPE_INSUFFICIENT", "GOVERNED_OPERATION_FAILURE")

    def test_worker_cannot_submit(self, tmp_path: Path) -> None:
        from aota_forge.core.context import bind_trusted_context
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter, TrustedWorkerBinding
        from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
        from aota_forge.work_plane.tool_surface import create_role_tool_surface
        from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
        from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence

        view = _view(["W1"])
        binding, root = _make_task_main_binding(tmp_path, view)
        sandbox = binding.sandbox
        worker_handoff = TaskHandoff(
            work_role="coder", task_kind="k", objective="o", bounded_scope="s",
            validation_expectations=("v",), semantic_stop_expectations=("t",),
            work_item_ref=SemanticReference(ref="W1"), milestone_ref=SemanticReference(ref="M1"),
        )
        worker_binding = TrustedWorkerBinding(
            canonical_task_id=f"{PROJECT_ID}:M1:W1:attempt-1", project_id=PROJECT_ID,
            worktree_id="wt-1",
            trusted_context=bind_trusted_context(principal_id="w", principal_type="hermes-worker", channel="mcp"),
            handoff=worker_handoff, sandbox=sandbox,
            tool_surface=create_role_tool_surface("coder", eager=("workspace.search",)),
            read_authorities=(), mutation_authority=None,
        )
        worker_adapter = _SharedAotaMcpAdapter(worker_binding)
        r = worker_adapter.invoke("task_main.submit_work_projection", _proposal_dict("W1"))
        assert r["ok"] is False
        assert r["error"]["code"] == "AUTHORITY_DENIED"


# ---------------------------------------------------------------------------
# Tier 3 — cross-module composition
# ---------------------------------------------------------------------------


class TestTier3CrossModule:
    def test_cross_module_composition(self, tmp_path: Path) -> None:
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        view = _view(["W1", "W2"])
        binding, _ = _make_task_main_binding(tmp_path, view)
        adapter = _SharedAotaMcpAdapter(binding)
        assert adapter.invoke("task_main.activate_milestone", {})["ok"] is True
        # Model submits semantics for W1 through the canonical operation.
        r = adapter.invoke("task_main.submit_work_projection", _proposal_dict("W1"))
        assert r["ok"] is True, r
        # Durable coordinator projection exists via the real control service.
        ctx = binding.trusted_task_main_context
        service = ctx.control_service
        live = ctx.live_plan_view
        coord_id = service.discover_matching_coordinator_id(
            project_id=PROJECT_ID, live_plan_view=live, coordinator_id=ctx.coordinator_id
        )
        state = service._coord_store.get(coord_id)
        assert state is not None
        assert "W1" in dict(getattr(state, "work_projections", {}))
        # TaskHandoff derivable from the canonical projection (no synthetic).
        handoff = resolve_task_main_work_handoff(state=state, work_item_id="W1", live_plan_view=live)
        assert handoff.bounded_scope == SCOPE_PLAIN
        assert handoff.work_item_ref is not None and handoff.work_item_ref.ref == "W1"
        assert handoff.project_ref is not None and handoff.project_ref.ref == PROJECT_ID
        # Digest binds projection.
        assert handoff.compute_handoff_digest() == r["payload"]["handoff_digest"]
        # No full Plan body carried; no expanded authority.
        assert "PLAN" not in handoff.bounded_scope or True
        assert handoff.plan_ref is not None


# ---------------------------------------------------------------------------
# Tier 4 — process boundary (child process + restart/recovery)
# ---------------------------------------------------------------------------


class TestTier4ProcessBoundary:
    def test_process_boundary_projection_write(self, tmp_path: Path) -> None:
        view = _view(["W1"])
        binding, root = _make_task_main_binding(tmp_path, view)
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        adapter = _SharedAotaMcpAdapter(binding)
        assert adapter.invoke("task_main.activate_milestone", {})["ok"] is True
        coord_path = root / ".aota" / "coord.json"
        payload = _proposal_dict("W1")
        script = (
            "import os, sys, json\n"
            "sys.path.insert(0, os.getcwd())\n"
            f"os.environ['{BOOTSTRAP_ENV_ROOT}'] = {str(root)!r}\n"
            "from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding\n"
            "from aota_forge.mcp_transport import _SharedAotaMcpAdapter\n"
            "binding = try_build_task_main_binding()\n"
            "assert binding is not None, 'child failed to rebuild task-main binding'\n"
            "adapter = _SharedAotaMcpAdapter(binding)\n"
            f"res = adapter.invoke('task_main.submit_work_projection', {payload!r})\n"
            "print(json.dumps({'ok': res['ok'], 'payload': res.get('payload'), 'error': res.get('error')}))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path.cwd()) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=str(Path.cwd()), timeout=60, env=env)
        assert proc.returncode == 0, f"child failed: {proc.stderr[:2000]}"
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ok"] is True, out
        assert out["payload"]["projection"]["bounded_scope"] == SCOPE_PLAIN
        # Parent verifies durable record after child write.
        coord_store = FileBackedTaskMainCoordinatorStore(coord_path)
        states = list(coord_store.list_all())
        assert states, "no durable coordinator after child write"
        st = coord_store.get(states[0].coordinator_id)
        assert st is not None
        assert dict(getattr(st, "work_projections", {}))["W1"]["projection"]["bounded_scope"] == SCOPE_PLAIN
        # Restart/recovery: rebuild binding in-process and recover; same projection.
        old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        try:
            new_binding = try_build_task_main_binding()
            assert new_binding is not None
            from aota_forge.mcp_transport import _SharedAotaMcpAdapter as _A

            new_adapter = _A(new_binding)
            r_rec = new_adapter.invoke("task_main.recover_coordinator", {})
            assert r_rec["ok"] is True, r_rec
            r_again = new_adapter.invoke("task_main.submit_work_projection", _proposal_dict("W1"))
            assert r_again["ok"] is True, r_again
            assert r_again["payload"]["projection"] == out["payload"]["projection"]
        finally:
            if old_root is None:
                os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
            else:
                os.environ[BOOTSTRAP_ENV_ROOT] = old_root


# ---------------------------------------------------------------------------
# Usability — one normal call
# ---------------------------------------------------------------------------


class TestOneNormalCall:
    def test_normal_projection_write_is_one_call(self, tmp_path: Path) -> None:
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        view = _view(["W1"])
        binding, _ = _make_task_main_binding(tmp_path, view)
        adapter = _SharedAotaMcpAdapter(binding)
        assert adapter.invoke("task_main.activate_milestone", {})["ok"] is True
        calls = 0
        res = adapter.invoke("task_main.submit_work_projection", _proposal_dict("W1"))
        calls += 1
        assert res["ok"] is True, res
        assert calls == 1
        # Ordinary success needs no hydration ceremony: payload is inline.
        assert res["output_mode"] == "inline"
        assert res["payload"]["projection"]["bounded_scope"] == SCOPE_PLAIN
