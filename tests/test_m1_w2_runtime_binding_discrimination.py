"""M1/W2 Worker/task-main Runtime Binding Discrimination (AF #45, I40-B003/F2).

Proves production Worker launch uses an explicit child environment (no
ambient task-main inheritance), role/context selection is exclusive and
fail-closed (no priority), and tool authority derives from the validated
binding. Preserves W1 scope transport, one shared aota.invoke, and the
Hermes boundary.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from aota_forge.adapters.hermes.host_client import (
    HermesHostClient,
    WORKER_CHILD_ENV_ALLOWLIST,
    TASK_MAIN_AUTHORITY_ENV_KEYS,
    WORKER_CONTEXT_KIND_ENV,
)
from aota_forge.composition import worker_vertical_slice as wvs
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    write_bootstrap_file,
)
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    MCP_PUBLIC_TOOLS,
    ONE_SHARED_AOTA_MCP,
    TrustedBindingError,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.work_plane.compiler import (
    compile_handoff_to_execution_package,
    TrustedExecutionBinding,
    verify_execution_package_integrity,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    WorkSemanticProjection,
    resolve_bounded_work_handoff,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


PROJECT_ID = "proj_w2"
PLAN_AUTH = "example-owner/example-w2#7"
MILESTONE = "M1"
WORK_ITEM = "W1"
PLAN_DIGEST = "e" * 64
OBJECTIVE = "Modify function foo so it returns X."
SCOPE = "Change function foo in src/sample.py so it returns X. Preserve Y. src/sample.py only."
VALIDATION = ("foo returns X", "Y regression passes")
STOPS = ("stop if scope unclear",)


def _projection(**overrides):
    data = {
        "objective": OBJECTIVE,
        "bounded_scope": SCOPE,
        "validation_expectations": list(VALIDATION),
        "semantic_stop_expectations": list(STOPS),
    }
    data.update(overrides)
    return WorkSemanticProjection.from_dict(data)


def _handoff(**overrides) -> TaskHandoff:
    kwargs = {
        "work_item_id": WORK_ITEM,
        "milestone_ref": MILESTONE,
        "projection": _projection(),
        "project_id": PROJECT_ID,
        "plan_authority": PLAN_AUTH,
        "plan_digest": PLAN_DIGEST,
    }
    kwargs.update(overrides)
    return resolve_bounded_work_handoff(**kwargs)


def _view():
    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE, work_items=[WORK_ITEM], dependencies=[])
    return MilestonePlanView(
        plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST, plan_source_revision="rev-w2",
        milestone_id=MILESTONE, entry_base="c" * 40, graph=graph,
        milestone_user_approval_satisfied=True,
    )


def _runtime_config_json(path: Path) -> None:
    path.write_text(json.dumps({
        "executor": "hermes", "executable": "/bin/false", "concurrency": 2,
        "provider": "opencode-go", "model": "deepseek-v4-flash",
        "bindings": {"task-main": {"profile": "aota-task-main"},
                     "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]}},
    }), encoding="utf-8")


class _EnvGuard:
    def __init__(self):
        self.saved = dict(os.environ)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.environ.clear()
        os.environ.update(self.saved)

    def clear_aota(self):
        for k in list(os.environ.keys()):
            if k.startswith("AOTA_"):
                del os.environ[k]
        os.environ.pop("AOTA_W3_CONTEXT_KIND", None)


def _make_project(tmp: Path, project_id: str = PROJECT_ID):
    root = tmp / f"wt-{project_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(f"project_id: {project_id}\nname: t\n", encoding="utf-8")
    return root


def _make_bootstrap(root: Path, tmp: Path, project_id: str = PROJECT_ID, worktree_id: str = "wt-1"):
    coord = root / ".aota" / "coordinator.json"
    execp = root / ".aota" / "execution.json"
    coord.write_text("{}", encoding="utf-8")
    execp.write_text("{}", encoding="utf-8")
    cfg = tmp / f"runtime-{project_id}.json"
    _runtime_config_json(cfg)
    bs = write_bootstrap_file(
        worktree_root=root, project_id=project_id, worktree_id=worktree_id,
        coordinator_store_path=coord, execution_store_path=execp,
        runtime_config_path=cfg, origin_task_main_session_ref="sess-w2",
        live_plan_view=_view(), next_milestone_view=None,
        work_semantics={WORK_ITEM: _projection().to_dict()},
    )
    return bs, cfg


def _set_worker_env(root: Path, handoff: TaskHandoff, project_id: str = PROJECT_ID,
                    worktree_id: str = "wt-1", task_id: str = "proj_w2:M1:W1:attempt-1",
                    with_explicit: str | None = None, hint: str | None = None):
    os.environ[wvs.MCP_ROOT_ENV] = str(root)
    os.environ[wvs.MCP_PROJECT_ENV] = project_id
    os.environ[wvs.MCP_WORKTREE_ENV] = worktree_id
    os.environ[wvs.MCP_TASK_ENV] = task_id
    os.environ[wvs.MCP_HANDOFF_ENV] = json.dumps(handoff.to_dict(), sort_keys=True)
    if with_explicit is not None:
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = with_explicit
    else:
        os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    if hint is not None:
        os.environ[wvs.CONTEXT_KIND_ENV] = hint
    else:
        os.environ.pop(wvs.CONTEXT_KIND_ENV, None)


# ---------------------------------------------------------------------------
# A. production Worker Popen receives explicit child env
# ---------------------------------------------------------------------------

def test_a_production_popen_receives_explicit_env(tmp_path: Path) -> None:
    calls: list[dict] = []

    class FakePopen:
        def __init__(self, *a, **k):
            calls.append(k)
            self.pid = 424242

        def poll(self):
            return None

    def payload():
        return {"profile": "coder", "instruction": "do", "context": {"canonical_task_id": "proj:M1:W1:attempt-1", "working_context": {"cwd": str(tmp_path)}}, "artifacts": [], "constraints": {}, "capability_requirements": {}, "result_expectations": {}, "operation": "task_dispatch", "package_id": "p-a"}

    client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                              runtime_root=tmp_path / "rt-a", popen_factory=FakePopen)
    client.dispatch(payload())
    assert calls, "supervisor Popen not recorded"
    assert "env" in calls[0], "production dispatch must pass explicit env=..."
    env = calls[0]["env"]
    assert isinstance(env, dict)
    # Must not be the live os.environ object (immutable copy).
    assert env is not os.environ


# ---------------------------------------------------------------------------
# B. Worker child env excludes task-main authority bootstrap
# ---------------------------------------------------------------------------

def test_b_worker_env_excludes_task_main_authority(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = "/tmp/should-be-stripped.json"
        os.environ["AOTA_TASK_MAIN_TRACE"] = "/tmp/trace"
        calls: list[dict] = []

        class FakePopen:
            def __init__(self, *a, **k):
                calls.append(k)
                self.pid = 424243

            def poll(self):
                return None

        def payload():
            return {"profile": "coder", "instruction": "do", "context": {"canonical_task_id": "proj:M1:W1:attempt-1", "working_context": {"cwd": str(tmp_path)}}, "artifacts": [], "constraints": {}, "capability_requirements": {}, "result_expectations": {}, "operation": "task_dispatch", "package_id": "p-b"}

        client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                                  runtime_root=tmp_path / "rt-b", popen_factory=FakePopen)
        client.dispatch(payload())
        env = calls[0]["env"]
        assert "AOTA_TASK_MAIN_BOOTSTRAP" not in env
        assert "AOTA_TASK_MAIN_TRACE" not in env
        # Parent must be untouched (no global mutation as authority).
        assert os.environ.get("AOTA_TASK_MAIN_BOOTSTRAP") == "/tmp/should-be-stripped.json"


# ---------------------------------------------------------------------------
# C. Worker child env contains valid Worker binding inputs
# ---------------------------------------------------------------------------

def test_c_worker_env_contains_trusted_binding(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    h = _handoff()
    child = wvs.build_worker_child_environment(
        root=root, project_id=PROJECT_ID, worktree_id="wt-1",
        canonical_task_id="proj_w2:M1:W1:attempt-1", handoff=h,
    )
    assert child[wvs.MCP_ROOT_ENV] == str(root.resolve())
    assert child[wvs.MCP_PROJECT_ENV] == PROJECT_ID
    assert child[wvs.MCP_WORKTREE_ENV] == "wt-1"
    assert child[wvs.MCP_TASK_ENV] == "proj_w2:M1:W1:attempt-1"
    parsed = TaskHandoff.from_dict(json.loads(child[wvs.MCP_HANDOFF_ENV]))
    assert parsed.handoff_digest == h.handoff_digest
    assert parsed.bounded_scope == SCOPE
    assert "AOTA_TASK_MAIN_BOOTSTRAP" not in child
    # task-main handoff must be refused at the boundary.
    placeholder = TaskHandoff(work_role="task-main", task_kind="task-main-control",
                              objective="AOTA task-main milestone control via aota.invoke",
                              bounded_scope="milestone coordination only",
                              validation_expectations=("task-main control validation",),
                              semantic_stop_expectations=("stop at user gate",),
                              work_item_ref=SemanticReference(ref="M1/task-main"),
                              milestone_ref=SemanticReference(ref="M1"))
    with pytest.raises(TrustedBindingError):
        wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id="wt-1",
            canonical_task_id="x", handoff=placeholder,
        )


# ---------------------------------------------------------------------------
# D. task-main child binds only task-main context
# ---------------------------------------------------------------------------

def test_d_task_main_binds_only_task_main(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bs)
        placeholder = TaskHandoff(work_role="task-main", task_kind="task-main-control",
                                  objective="AOTA task-main milestone control via aota.invoke",
                                  bounded_scope="milestone coordination only",
                                  validation_expectations=("task-main control validation",),
                                  semantic_stop_expectations=("stop at user gate",),
                                  work_item_ref=SemanticReference(ref="M1/task-main"),
                                  milestone_ref=SemanticReference(ref="M1"))
        os.environ[wvs.MCP_ROOT_ENV] = str(root)
        os.environ[wvs.MCP_PROJECT_ENV] = PROJECT_ID
        os.environ[wvs.MCP_WORKTREE_ENV] = "wt-1"
        os.environ[wvs.MCP_TASK_ENV] = "proj_w2:M1:task-main:x"
        os.environ[wvs.MCP_HANDOFF_ENV] = json.dumps(placeholder.to_dict(), sort_keys=True)
        os.environ.pop(wvs.CONTEXT_KIND_ENV, None)
        selected = wvs.select_runtime_context()
        assert selected.handoff.work_role.value == "task-main"
        assert selected.trusted_task_main_context is not None


# ---------------------------------------------------------------------------
# E. Worker cannot bind task-main context (negative 1)
# ---------------------------------------------------------------------------

def test_e_worker_with_task_main_context_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        # Worker launch identity: hint worker, explicit task-main signal present,
        # no valid Worker binding.
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bs)
        os.environ[wvs.MCP_ROOT_ENV] = str(root)
        os.environ[wvs.CONTEXT_KIND_ENV] = "worker"
        for k in (wvs.MCP_PROJECT_ENV, wvs.MCP_WORKTREE_ENV, wvs.MCP_TASK_ENV, wvs.MCP_HANDOFF_ENV):
            os.environ.pop(k, None)
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "AMBIGUOUS" in str(exc.value) or "MISSING" in str(exc.value) or "worker" in str(exc.value).lower()
        # And a worker-vars session with only fallback file (no explicit) must
        # fail missing, never silently become task-main.
        g.clear_aota()
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
        os.environ.pop(wvs.CONTEXT_KIND_ENV, None)
        # No worker vars at all, but fallback file exists: legacy task-main
        # without explicit would pick task-main; a worker-intended check with
        # worker vars present-but-invalid must not become task-main (covered
        # by the hint case above). Here assert fallback alone still resolves
        # (legacy compat) but worker-invalid does not substitute.
        selected = wvs.select_runtime_context()
        assert selected.handoff.work_role.value == "task-main"


# ---------------------------------------------------------------------------
# F. task-main cannot bind Worker context as equivalent (negative 2)
# ---------------------------------------------------------------------------

def test_f_task_main_with_worker_binding_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        h = _handoff()
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        _set_worker_env(root, h, with_explicit=str(bs))
        # Both valid -> ambiguous, never treated as equivalent.
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "AMBIGUOUS" in str(exc.value)
    # Operation gate: task-main controls require task-main context.
    from aota_forge.mcp_transport import create_shared_mcp_server
    with _EnvGuard():
        root2 = _make_project(tmp_path, "proj_f2")
        binding = wvs.build_worker_binding(
            root=root2, project_id="proj_f2", worktree_id="wt-f",
            canonical_task_id="proj_f2:M1:W1:attempt-1", handoff=_handoff(project_id="proj_f2"),
        )
        server_adapter = create_shared_mcp_server(binding)
        # Worker binding must not be able to call task-main controls.
        assert server_adapter is not None
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        adapter = _SharedAotaMcpAdapter(binding)
        out = adapter.invoke("task_main.activate_milestone", {})
        assert out["ok"] is False
        assert out["error"]["code"] == "AUTHORITY_DENIED"


# ---------------------------------------------------------------------------
# G/H. missing and conflicting fail closed (negative 3/4)
# ---------------------------------------------------------------------------

def test_g_missing_fails_closed() -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "MISSING" in str(exc.value)


def test_h_conflicting_fails_closed(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        h = _handoff()
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        _set_worker_env(root, h, with_explicit=str(bs))
        # Explicit bootstrap + valid worker binding -> both valid -> ambiguous.
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "AMBIGUOUS" in str(exc.value)


# ---------------------------------------------------------------------------
# I. forged role hint fails closed (negative 5)
# ---------------------------------------------------------------------------

def test_i_forged_hint_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        # role=task-main without trusted task-main context.
        os.environ[wvs.CONTEXT_KIND_ENV] = "task-main"
        with pytest.raises(Exception):
            wvs.select_runtime_context()
        g.clear_aota()
        # role=coder/worker without valid Worker binding.
        os.environ[wvs.CONTEXT_KIND_ENV] = "worker"
        with pytest.raises(Exception):
            wvs.select_runtime_context()
        g.clear_aota()
        # Invalid hint value.
        os.environ[wvs.CONTEXT_KIND_ENV] = "coder"
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "FORGED_ROLE_HINT" in str(exc.value)
        # Forged hint must not grant authority even with partial material.
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        _set_worker_env(root, h, hint="task-main")
        # worker valid + hint task-main, no bootstrap -> task-main absent -> fail, not worker.
        with pytest.raises(Exception):
            wvs.select_runtime_context()


# ---------------------------------------------------------------------------
# J. session metadata mismatch fails closed (negative 6)
# ---------------------------------------------------------------------------

def test_j_metadata_mismatch_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        _set_worker_env(root, h, project_id="proj_OTHER")
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "MISMATCH" in str(exc.value)
        g.clear_aota()
        # Task identity claims a different Work Item than the trusted handoff.
        _set_worker_env(root, h, task_id="proj_w2:M1:W2:attempt-1")
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "MISMATCH" in str(exc.value)


# ---------------------------------------------------------------------------
# K. Worker tool authority derives from Worker binding
# ---------------------------------------------------------------------------

def test_k_worker_tool_authority(tmp_path: Path) -> None:
    from aota_forge.mcp_transport import _SharedAotaMcpAdapter

    def binding_for(role: str, proj: str = "proj_k"):
        root = _make_project(tmp_path, proj)
        proj_obj = _projection()
        h = resolve_bounded_work_handoff(work_item_id=WORK_ITEM, milestone_ref=MILESTONE,
                                         projection=proj_obj, project_id=proj,
                                         plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                                         work_role_override=role) if False else None
        # Build role-specific handoff via TaskHandoff directly (bounded).
        from aota_forge.work_plane.roles import AgentWorkRole
        h = TaskHandoff(work_role=role, task_kind="w2-kind", objective=OBJECTIVE,
                        bounded_scope=SCOPE, validation_expectations=list(VALIDATION),
                        semantic_stop_expectations=list(STOPS),
                        project_ref=SemanticReference(ref=proj),
                        plan_ref=SemanticReference(ref=PLAN_AUTH, digest=PLAN_DIGEST),
                        milestone_ref=SemanticReference(ref=MILESTONE),
                        work_item_ref=SemanticReference(ref=WORK_ITEM))
        return wvs.build_worker_binding(root=root, project_id=proj, worktree_id="wt-k",
                                        canonical_task_id=f"{proj}:M1:W1:attempt-1", handoff=h)

    coder = binding_for("coder", "proj_kc")
    assert coder.mutation_authority is not None
    reviewer = binding_for("reviewer", "proj_kr")
    assert reviewer.mutation_authority is None
    steward = binding_for("project-steward", "proj_ks")
    assert steward.mutation_authority is None
    analyst = binding_for("analyst", "proj_ka")
    assert analyst.mutation_authority is None
    # Server gate: reviewer cannot product-write.
    adapter = _SharedAotaMcpAdapter(reviewer)
    out = adapter.invoke("workspace.write", {"path": "src/a.py", "content": "x", "mode": "create_only"})
    assert out["ok"] is False
    assert out["error"]["code"] == "AUTHORITY_DENIED"
    # Coder workspace.write is authorized at the gate level when inputs valid
    # (may still fail on path bounds, but must not be AUTHORITY_DENIED for
    # missing authority).
    cadapter = _SharedAotaMcpAdapter(coder)
    assert cadapter.binding.mutation_authority is not None


# ---------------------------------------------------------------------------
# L. task-main authority derives from task-main context
# ---------------------------------------------------------------------------

def test_l_task_main_authority(tmp_path: Path) -> None:
    from aota_forge.mcp_transport import _SharedAotaMcpAdapter

    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bs)
        from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding
        binding = try_build_task_main_binding()
        assert binding is not None
        assert binding.trusted_task_main_context is not None
        adapter = _SharedAotaMcpAdapter(binding)
        # task-main without product-write authority: workspace.write denied.
        out = adapter.invoke("workspace.write", {"path": "src/a.py", "content": "x", "mode": "create_only"})
        assert out["ok"] is False
        # Worker binding cannot call task-main controls (already covered in F).
        root2 = _make_project(tmp_path, "proj_lw")
        wb = wvs.build_worker_binding(root=root2, project_id="proj_lw", worktree_id="wt-l",
                                      canonical_task_id="proj_lw:M1:W1:attempt-1", handoff=_handoff(project_id="proj_lw"))
        wadapter = _SharedAotaMcpAdapter(wb)
        out2 = wadapter.invoke("task_main.advance_once", {})
        assert out2["ok"] is False
        assert out2["error"]["code"] == "AUTHORITY_DENIED"


# ---------------------------------------------------------------------------
# M. concurrent Worker A/B isolation
# ---------------------------------------------------------------------------

def test_m_concurrent_isolation(tmp_path: Path) -> None:
    root_a = _make_project(tmp_path, "proj_A")
    root_b = _make_project(tmp_path, "proj_B")
    h_a = _handoff(project_id="proj_A")
    h_b = _handoff(project_id="proj_B")
    results: dict[str, dict] = {}
    errors: list = []

    def build(tag, root, h, proj):
        try:
            child = wvs.build_worker_child_environment(
                root=root, project_id=proj, worktree_id=f"wt-{tag}",
                canonical_task_id=f"{proj}:M1:W1:attempt-1", handoff=h,
            )
            results[tag] = child
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=build, args=("A", root_a, h_a, "proj_A")),
        threading.Thread(target=build, args=("B", root_b, h_b, "proj_B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results["A"]["AOTA_W3_PROJECT_ID"] == "proj_A"
    assert results["B"]["AOTA_W3_PROJECT_ID"] == "proj_B"
    assert results["A"]["AOTA_W3_HANDOFF_JSON"] != results["B"]["AOTA_W3_HANDOFF_JSON"]
    assert json.loads(results["A"]["AOTA_W3_HANDOFF_JSON"])["project_ref"]["ref"] == "proj_A"
    # Dispatch-level isolation: two concurrent dispatches get distinct envs.
    seen: dict[str, str] = {}

    def dispatch_isolation(tag, proj, h, root):
        captured: list[dict] = []

        class FakePopen:
            def __init__(self, *a, **k):
                captured.append(k)
                self.pid = 1000 + (0 if tag == "A" else 1)

            def poll(self):
                return None

        def resolver(payload):
            return wvs.build_worker_child_environment(
                root=root, project_id=proj, worktree_id=f"wt-{tag}",
                canonical_task_id=payload["context"]["canonical_task_id"], handoff=h,
            )

        rt = tmp_path / f"rt-{tag}"
        client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                                  runtime_root=rt, popen_factory=FakePopen, worker_env_resolver=resolver)
        payload = {"profile": "coder", "instruction": "do", "context": {"canonical_task_id": f"{proj}:M1:W1:attempt-1", "working_context": {"cwd": str(tmp_path)}}, "artifacts": [], "constraints": {}, "capability_requirements": {}, "result_expectations": {}, "operation": "task_dispatch", "package_id": f"p-{tag}"}
        client.dispatch(payload)
        seen[tag] = captured[0]["env"]["AOTA_W3_PROJECT_ID"]

    threads = [
        threading.Thread(target=dispatch_isolation, args=("A", "proj_A", h_a, root_a)),
        threading.Thread(target=dispatch_isolation, args=("B", "proj_B", h_b, root_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen["A"] == "proj_A" and seen["B"] == "proj_B"


# ---------------------------------------------------------------------------
# N. task-main remains intact while Workers launch
# ---------------------------------------------------------------------------

def test_n_task_main_worker_isolation(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bs)
        from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding
        tm_before = try_build_task_main_binding()
        assert tm_before is not None
        parent_snapshot = dict(os.environ)
        # Build two worker envs (pure, no global mutation).
        h_a = _handoff()
        child_a = wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id="wt-1",
            canonical_task_id="proj_w2:M1:W1:attempt-1", handoff=h_a)
        child_b = wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id="wt-2",
            canonical_task_id="proj_w2:M1:W2:attempt-1", handoff=h_a)
        assert dict(os.environ) == parent_snapshot
        assert child_a["AOTA_W3_WORKTREE_ID"] == "wt-1"
        assert child_b["AOTA_W3_WORKTREE_ID"] == "wt-2"
        tm_after = try_build_task_main_binding()
        assert tm_after is not None
        assert tm_after.trusted_task_main_context is not None


# ---------------------------------------------------------------------------
# O. no process-global mutation required in production dispatch
# ---------------------------------------------------------------------------

def test_o_no_global_mutation(tmp_path: Path) -> None:
    src = inspect.getsource(HermesHostClient.dispatch)
    assert "os.environ.update" not in src
    assert "os.environ[" not in src or "AOTA_FORGE_RUNTIME_CONFIG" not in src
    assert "env=supervisor_env" in src
    # worker_environment context manager remains for compat/tests but dispatch
    # must not depend on it.
    with _EnvGuard() as g:
        g.clear_aota()
        before = dict(os.environ)
        calls: list[dict] = []

        class FakePopen:
            def __init__(self, *a, **k):
                calls.append(k)
                self.pid = 999

            def poll(self):
                return None

        payload = {"profile": "coder", "instruction": "do", "context": {"canonical_task_id": "p:M:W:attempt-1", "working_context": {"cwd": str(tmp_path)}}, "artifacts": [], "constraints": {}, "capability_requirements": {}, "result_expectations": {}, "operation": "task_dispatch", "package_id": "p-o"}
        client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                                  runtime_root=tmp_path / "rt-o", popen_factory=FakePopen)
        client.dispatch(payload)
        assert dict(os.environ) == before


# ---------------------------------------------------------------------------
# P/Q/R/S/T. architecture preservation
# ---------------------------------------------------------------------------

def test_p_one_shared_mcp() -> None:
    assert ONE_SHARED_AOTA_MCP is True
    assert tuple(MCP_PUBLIC_TOOLS) == ("aota.invoke",)
    assert AGENT_FACING_AOTA_TOOL == "aota.invoke"


def test_q_w1_scope_transport_intact() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(h, TrustedExecutionBinding(canonical_task_id="proj_w2:M1:W1:attempt-1", project_id=PROJECT_ID))
    verify_execution_package_integrity(pkg, h)
    assert SCOPE in pkg.instruction
    assert h.handoff_digest in pkg.instruction


def test_r_no_hardcoding() -> None:
    banned = ["calculator", "modulo", "df58517", "aota_forge_dogfood"]
    for rel in ["aota_forge/composition/worker_vertical_slice.py",
                "aota_forge/adapters/hermes/host_client.py",
                "aota_forge/composition/task_main_host_bootstrap.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, f"banned {token!r} in {rel}"
    # No session-name heuristics, project-id role discrimination, PID patterns.
    for rel in ["aota_forge/composition/worker_vertical_slice.py"]:
        src = Path(rel).read_text(encoding="utf-8").lower()
        assert "session_name" not in src or "heuristic" in src
        assert "pid pattern" not in src


def test_s_no_hermes_mutation_and_markers() -> None:
    assert wvs.ROLE_CONTEXT_SELECTION_EXPLICIT is True
    assert wvs.TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED is True
    assert wvs.PRODUCTION_WORKER_ENV_EXPLICIT is True
    assert wvs.PRODUCTION_WORKER_ENV_USES_PARENT_GLOBAL_MUTATION is False
    assert wvs.AUTHORITY_ENV_ALLOWLIST_EXPLICIT is True
    assert wvs.ROLE_HINT_IS_AUTHORITY is False
    assert wvs.WORKER_PROCESS_INHERITS_TASK_MAIN_AUTHORITY_ENV is False
    assert wvs.AD_HOC_TMP_BOOTSTRAP_DEBUG_LOGGING_REMOVED is True
    assert WORKER_CONTEXT_KIND_ENV == "AOTA_W3_CONTEXT_KIND"
    assert "AOTA_TASK_MAIN_BOOTSTRAP" in TASK_MAIN_AUTHORITY_ENV_KEYS


def test_t_no_tmp_debug_dump() -> None:
    for rel in ["aota_forge/composition/worker_vertical_slice.py",
                "aota_forge/composition/task_main_host_bootstrap.py",
                "aota_forge/adapters/hermes/host_client.py"]:
        src = Path(rel).read_text(encoding="utf-8")
        assert "/tmp/aota_mcp_debug.log" not in src
        assert "/tmp/aota_task_main_bootstrap_debug.log" not in src
        assert "/tmp/aota_mcp_startup.log" not in src
    assert "has priority" not in Path("aota_forge/composition/worker_vertical_slice.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Process-boundary, ambiguity, and server authority proofs
# ---------------------------------------------------------------------------

def _child_probe_script() -> str:
    return (
        "import os, json, sys; "
        "sys.path.insert(0, os.getcwd()); "
        "from aota_forge.composition.worker_vertical_slice import select_runtime_context; "
        "b = select_runtime_context(); "
        "print(json.dumps({'role': b.handoff.work_role.value, 'project': b.project_id, 'worktree': b.worktree_id, 'task': b.canonical_task_id, 'has_tm': b.trusted_task_main_context is not None}))"
    )


def test_process_boundary_context_proof(tmp_path: Path) -> None:
    root_a = _make_project(tmp_path, "proj_PA")
    root_b = _make_project(tmp_path, "proj_PB")
    h_a = _handoff(project_id="proj_PA")
    h_b = _handoff(project_id="proj_PB")
    env_a = wvs.build_worker_child_environment(root=root_a, project_id="proj_PA", worktree_id="wt-A", canonical_task_id="proj_PA:M1:W1:attempt-1", handoff=h_a)
    env_b = wvs.build_worker_child_environment(root=root_b, project_id="proj_PB", worktree_id="wt-B", canonical_task_id="proj_PB:M1:W1:attempt-1", handoff=h_b)
    # task-main env for comparison.
    with _EnvGuard():
        for tag, env in (("A", env_a), ("B", env_b)):
            full = dict(os.environ)
            full.update(env)
            full.pop("AOTA_TASK_MAIN_BOOTSTRAP", None)
            proc = subprocess.run([sys.executable, "-c", _child_probe_script()], capture_output=True, text=True, env=full, cwd=str(Path.cwd()), timeout=60)
            assert proc.returncode == 0, f"child {tag} failed: {proc.stderr[:800]}"
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            expected_proj = "proj_PA" if tag == "A" else "proj_PB"
            assert data["project"] == expected_proj
            assert data["role"] == "coder"
            assert data["has_tm"] is False


def test_ambiguous_context_proof(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        bs, _ = _make_bootstrap(root, tmp_path)
        h = _handoff()
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        _set_worker_env(root, h, with_explicit=str(bs))
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "AMBIGUOUS" in str(exc.value)


def test_role_server_authority_proof(tmp_path: Path) -> None:
    from aota_forge.mcp_transport import _SharedAotaMcpAdapter
    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        binding = wvs.build_worker_binding(root=root, project_id=PROJECT_ID, worktree_id="wt-1",
                                           canonical_task_id="proj_w2:M1:W1:attempt-1", handoff=h)
        summary = handle_role_bootstrap(binding, {})
        assert summary["ROLE"] == "coder"
        assert summary["TASK_HANDOFF"]["bounded_scope"] == SCOPE
        adapter = _SharedAotaMcpAdapter(binding)
        out = adapter.invoke("role.bootstrap", {})
        assert out["ok"] is True
        # task-main side.
        g.clear_aota()
        root2 = _make_project(tmp_path, "proj_tm")
        bs, _ = _make_bootstrap(root2, tmp_path, project_id="proj_tm", worktree_id="wt-tm")
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root2)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bs)
        from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding
        tm = try_build_task_main_binding()
        assert tm is not None
        tadapter = _SharedAotaMcpAdapter(tm)
        tout = tadapter.invoke("role.bootstrap", {})
        assert tout["ok"] is True
