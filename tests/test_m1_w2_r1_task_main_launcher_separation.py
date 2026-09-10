"""M1/W2-R1 task-main launcher context separation repair (AF #45, I45-B001).

Proves the production task-main launcher emits task-main authority only
(no Worker-channel placeholder), the repaired context discrimination still
fails closed, and the actual production launcher output drives the actual
context selector to a valid task-main binding.

No synthetic substitute for launcher env: tests B/C use the actual
DailyTaskMainLauncher.prepare + build_env output.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
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
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition import worker_vertical_slice as wvs
from aota_forge.composition.task_main_daily_launcher import (
    DailyTaskMainLauncher,
    TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_HANDOFF,
    TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_AUTHORITY_KEYS,
    TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF,
    TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION,
    TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED,
    TASK_MAIN_REQUIRED_ENV,
)
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
    _SharedAotaMcpAdapter,
    create_shared_mcp_server,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
    verify_execution_package_integrity,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    WorkSemanticProjection,
    resolve_bounded_work_handoff,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


PROJECT_ID = "proj_w2r1"
PLAN_AUTH = "example-owner/example-w2-r1#7"
MILESTONE = "M1"
WORK_ITEM = "W1"
PLAN_DIGEST = "e" * 64
OBJECTIVE = "Modify function foo so it returns X."
SCOPE = "Change function foo in src/sample.py so it returns X. Preserve Y. src/sample.py only."
VALIDATION = ("foo returns X", "Y regression passes")
STOPS = ("stop if scope unclear",)

WORKER_ENV_KEYS = (
    "AOTA_W3_MCP_ROOT",
    "AOTA_W3_PROJECT_ID",
    "AOTA_W3_WORKTREE_ID",
    "AOTA_W3_TASK_ID",
    "AOTA_W3_HANDOFF_JSON",
)

HISTORICAL_BAD_HANDOFF_JSON = '{"work_role":"task-main","task_kind":"task-main-control"}'


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
    root = tmp / f"wt-r1-{project_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(f"project_id: {project_id}\nname: t\n", encoding="utf-8")
    return root


def _launcher_plan_body() -> str:
    return """# [PLAN] W2-R1 launcher separation proof

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
M1_WORK_ITEMS=W1, W2
M1_DAG=W1 -> W2
```
"""


def _make_launcher_ctx(tmp: Path, project_id: str = PROJECT_ID, worktree_id: str = "wt-r1"):
    root = _make_project(tmp, project_id)
    cfg = tmp / f"runtime-r1-{project_id}.json"
    _runtime_config_json(cfg)
    adapter = StaticPlanAuthorityAdapter(
        body=_launcher_plan_body(),
        plan_authority=PLAN_AUTH,
        revision="rev-w2-r1",
    )
    launcher = DailyTaskMainLauncher(plan_adapter=adapter)
    ctx = launcher.prepare(
        worktree_root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=cfg,
        origin_task_main_session_ref="sess-w2-r1-proof",
    )
    env = launcher.build_env(ctx)
    return launcher, ctx, env, root


def _set_worker_env(root: Path, handoff: TaskHandoff, project_id: str = PROJECT_ID,
                    worktree_id: str = "wt-1", task_id: str = "proj_w2r1:M1:W1:attempt-1",
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
# A. actual task-main launcher env has no Worker authority placeholder
# ---------------------------------------------------------------------------

def test_r1_a_launcher_env_has_no_worker_authority(tmp_path: Path) -> None:
    _, _, env, _ = _make_launcher_ctx(tmp_path)
    assert "AOTA_W3_HANDOFF_JSON" not in env
    for key in WORKER_ENV_KEYS:
        assert key not in env, f"task-main launch env must not contain Worker key {key}"
    assert "AOTA_W3_CONTEXT_KIND" not in env
    assert "AOTA_W3_TOOL_TRACE" not in env
    assert "AOTA_TASK_MAIN_BOOTSTRAP" in env
    assert TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_HANDOFF is False
    assert TASK_MAIN_LAUNCH_ENV_CONTAINS_WORKER_AUTHORITY_KEYS is False
    assert TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF is False
    assert TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION is True
    assert TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED is False
    for key in WORKER_ENV_KEYS:
        assert key not in TASK_MAIN_REQUIRED_ENV
    assert "AOTA_TASK_MAIN_BOOTSTRAP" in TASK_MAIN_REQUIRED_ENV
    assert wvs.TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION is True
    assert wvs.TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF is False
    # Worker and task-main authority sets are disjoint.
    assert set(wvs.WORKER_AUTHORITY_ENV_KEYS).isdisjoint(set(wvs.TASK_MAIN_AUTHORITY_ENV_KEYS))


# ---------------------------------------------------------------------------
# B. actual launcher env selects task-main context (critical cross-module proof)
# ---------------------------------------------------------------------------

def test_r1_b_actual_launcher_env_selects_task_main(tmp_path: Path) -> None:
    _, _, env, _ = _make_launcher_ctx(tmp_path)
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        selected = wvs.select_runtime_context()
        assert selected.handoff.work_role.value == "task-main"
        assert selected.trusted_task_main_context is not None
        assert selected.trusted_task_main_context.live_plan_view.milestone_id == "M1"


# ---------------------------------------------------------------------------
# C. actual launcher env constructs shared aota.invoke MCP + role.bootstrap
# ---------------------------------------------------------------------------

def test_r1_c_actual_launcher_env_builds_shared_mcp(tmp_path: Path) -> None:
    _, _, env, _ = _make_launcher_ctx(tmp_path)
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        selected = wvs.select_runtime_context()
        server = create_shared_mcp_server(selected)
        assert server is not None
        assert ONE_SHARED_AOTA_MCP is True
        assert tuple(MCP_PUBLIC_TOOLS) == ("aota.invoke",)
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"
        adapter = _SharedAotaMcpAdapter(selected)
        out = adapter.invoke("role.bootstrap", {})
        assert out["ok"] is True


# ---------------------------------------------------------------------------
# D/E. actual Worker launch env selects Worker + carries valid TaskHandoff
# ---------------------------------------------------------------------------

def test_r1_d_worker_child_env_selects_worker(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    h = _handoff()
    child = wvs.build_worker_child_environment(
        root=root, project_id=PROJECT_ID, worktree_id="wt-1",
        canonical_task_id="proj_w2r1:M1:W1:attempt-1", handoff=h,
    )
    assert "AOTA_TASK_MAIN_BOOTSTRAP" not in child
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(child)
        os.environ.pop("AOTA_TASK_MAIN_BOOTSTRAP", None)
        selected = wvs.select_runtime_context()
        assert selected.handoff.work_role.value == "coder"
        assert selected.trusted_task_main_context is None
        assert selected.canonical_task_id == "proj_w2r1:M1:W1:attempt-1"


def test_r1_e_worker_env_contains_valid_handoff(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    h = _handoff()
    child = wvs.build_worker_child_environment(
        root=root, project_id=PROJECT_ID, worktree_id="wt-1",
        canonical_task_id="proj_w2r1:M1:W1:attempt-1", handoff=h,
    )
    parsed = TaskHandoff.from_dict(json.loads(child[wvs.MCP_HANDOFF_ENV]))
    assert parsed.objective == OBJECTIVE
    assert parsed.bounded_scope == SCOPE
    assert tuple(parsed.validation_expectations) == tuple(VALIDATION)
    assert tuple(parsed.semantic_stop_expectations) == tuple(STOPS)
    assert parsed.handoff_digest == h.handoff_digest


def test_r1_d2_dispatch_worker_env_selects_worker(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    h = _handoff()
    calls: list[dict] = []

    class FakePopen:
        def __init__(self, *a, **k):
            calls.append(k)
            self.pid = 424244

        def poll(self):
            return None

    def resolver(payload):
        return wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id="wt-1",
            canonical_task_id=payload["context"]["canonical_task_id"], handoff=h,
        )

    rt = tmp_path / "rt-r1-d2"
    client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                              runtime_root=rt, popen_factory=FakePopen,
                              worker_env_resolver=resolver)
    payload = {"profile": "coder", "instruction": "do",
               "context": {"canonical_task_id": "proj_w2r1:M1:W1:attempt-1",
                           "working_context": {"cwd": str(tmp_path)}},
               "artifacts": [], "constraints": {}, "capability_requirements": {},
               "result_expectations": {}, "operation": "task_dispatch", "package_id": "p-r1-d2"}
    client.dispatch(payload)
    assert calls and "env" in calls[0]
    child = calls[0]["env"]
    assert "AOTA_TASK_MAIN_BOOTSTRAP" not in child
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(child)
        os.environ.pop("AOTA_TASK_MAIN_BOOTSTRAP", None)
        selected = wvs.select_runtime_context()
        assert selected.handoff.work_role.value == "coder"


# ---------------------------------------------------------------------------
# F. malformed historical placeholder manually injected still fails closed
# ---------------------------------------------------------------------------

def test_r1_f_historical_bad_shape_fails_closed(tmp_path: Path) -> None:
    _, _, env, root = _make_launcher_ctx(tmp_path)
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        # Manually inject the exact historical bad shape on top of a valid
        # task-main context: full Worker channel present but malformed.
        os.environ[wvs.MCP_ROOT_ENV] = str(root)
        os.environ[wvs.MCP_PROJECT_ENV] = PROJECT_ID
        os.environ[wvs.MCP_WORKTREE_ENV] = "wt-1"
        os.environ[wvs.MCP_TASK_ENV] = "proj_w2r1:M1:W1:attempt-1"
        os.environ[wvs.MCP_HANDOFF_ENV] = HISTORICAL_BAD_HANDOFF_JSON
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        text = str(exc.value)
        assert ("objective" in text.lower() or "missing" in text.lower()
                or "failed" in text.lower() or "binding" in text.lower())


def test_r1_f2_repaired_launcher_never_generates_bad_shape(tmp_path: Path) -> None:
    _, _, env, _ = _make_launcher_ctx(tmp_path)
    for key in WORKER_ENV_KEYS:
        assert key not in env
    src = Path("aota_forge/composition/task_main_daily_launcher.py").read_text(encoding="utf-8")
    assert '{"work_role":"task-main","task_kind":"task-main-control"}' not in src
    assert '{"work_role": "task-main"' not in src


# ---------------------------------------------------------------------------
# G/H. valid+valid ambiguous, neither missing
# ---------------------------------------------------------------------------

def test_r1_g_valid_task_main_plus_valid_worker_ambiguous(tmp_path: Path) -> None:
    _, _, env, root = _make_launcher_ctx(tmp_path)
    h = _handoff()
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        os.environ[wvs.MCP_ROOT_ENV] = str(root)
        os.environ[wvs.MCP_PROJECT_ENV] = PROJECT_ID
        os.environ[wvs.MCP_WORKTREE_ENV] = "wt-1"
        os.environ[wvs.MCP_TASK_ENV] = "proj_w2r1:M1:W1:attempt-1"
        os.environ[wvs.MCP_HANDOFF_ENV] = json.dumps(h.to_dict(), sort_keys=True)
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "AMBIGUOUS" in str(exc.value)


def test_r1_h_neither_fails_closed() -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        with pytest.raises(Exception) as exc:
            wvs.select_runtime_context()
        assert "MISSING" in str(exc.value)


# ---------------------------------------------------------------------------
# I/J. forged hint + metadata mismatch
# ---------------------------------------------------------------------------

def test_r1_i_forged_hint_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ[wvs.CONTEXT_KIND_ENV] = "coder"
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "FORGED_ROLE_HINT" in str(exc.value)
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        _set_worker_env(root, h, hint="task-main")
        with pytest.raises(Exception):
            wvs.select_runtime_context()


def test_r1_j_metadata_mismatch_rejected(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        _set_worker_env(root, h, project_id="proj_OTHER")
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "MISMATCH" in str(exc.value)
    with _EnvGuard() as g:
        g.clear_aota()
        root = _make_project(tmp_path)
        h = _handoff()
        _set_worker_env(root, h, task_id="proj_w2r1:M1:W2:attempt-1")
        with pytest.raises(TrustedBindingError) as exc:
            wvs.select_runtime_context()
        assert "MISMATCH" in str(exc.value)


# ---------------------------------------------------------------------------
# K. TaskHandoff required fields remain required (not weakened for placeholder)
# ---------------------------------------------------------------------------

def test_r1_k_handoff_required_fields_intact() -> None:
    assert wvs.TASK_HANDOFF_REQUIRED_FIELDS_WEAKENED is False
    with pytest.raises(Exception):
        TaskHandoff.from_dict({"work_role": "task-main", "task_kind": "task-main-control"})
    with pytest.raises(Exception):
        TaskHandoff.from_dict(json.loads(HISTORICAL_BAD_HANDOFF_JSON))
    src = Path("aota_forge/work_plane/handoff.py").read_text(encoding="utf-8")
    for field in ("objective", "bounded_scope", "validation_expectations", "semantic_stop_expectations"):
        assert field in src


# ---------------------------------------------------------------------------
# L. Worker explicit Popen env remains isolated
# ---------------------------------------------------------------------------

def test_r1_l_worker_popen_env_isolated(tmp_path: Path) -> None:
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = "/tmp/should-be-stripped.json"
        calls: list[dict] = []

        class FakePopen:
            def __init__(self, *a, **k):
                calls.append(k)
                self.pid = 424245

            def poll(self):
                return None

        payload = {"profile": "coder", "instruction": "do",
                   "context": {"canonical_task_id": "p:M:W:attempt-1",
                               "working_context": {"cwd": str(tmp_path)}},
                   "artifacts": [], "constraints": {}, "capability_requirements": {},
                   "result_expectations": {}, "operation": "task_dispatch", "package_id": "p-r1-l"}
        client = HermesHostClient("/bin/false", default_cwd=str(tmp_path), validate_launcher=False,
                                  runtime_root=tmp_path / "rt-r1-l", popen_factory=FakePopen)
        before = dict(os.environ)
        client.dispatch(payload)
        env = calls[0]["env"]
        assert "AOTA_TASK_MAIN_BOOTSTRAP" not in env
        assert "AOTA_TASK_MAIN_TRACE" not in env
        assert dict(os.environ) == before
        assert wvs.PRODUCTION_WORKER_ENV_EXPLICIT is True
        assert wvs.PRODUCTION_WORKER_ENV_USES_PARENT_GLOBAL_MUTATION is False
        assert wvs.WORKER_PROCESS_INHERITS_TASK_MAIN_AUTHORITY_ENV is False
        assert wvs.AUTHORITY_ENV_ALLOWLIST_EXPLICIT is True
        src = inspect.getsource(HermesHostClient.dispatch)
        assert "env=supervisor_env" in src


# ---------------------------------------------------------------------------
# M/N. concurrent + simultaneous isolation
# ---------------------------------------------------------------------------

def test_r1_m_concurrent_worker_isolation(tmp_path: Path) -> None:
    root_a = _make_project(tmp_path, "proj_RA")
    root_b = _make_project(tmp_path, "proj_RB")
    h_a = _handoff(project_id="proj_RA")
    h_b = _handoff(project_id="proj_RB")
    results: dict[str, dict] = {}
    errors: list = []

    def build(tag, root, h, proj):
        try:
            results[tag] = wvs.build_worker_child_environment(
                root=root, project_id=proj, worktree_id=f"wt-{tag}",
                canonical_task_id=f"{proj}:M1:W1:attempt-1", handoff=h,
            )
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [
        threading.Thread(target=build, args=("A", root_a, h_a, "proj_RA")),
        threading.Thread(target=build, args=("B", root_b, h_b, "proj_RB")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results["A"]["AOTA_W3_PROJECT_ID"] == "proj_RA"
    assert results["B"]["AOTA_W3_PROJECT_ID"] == "proj_RB"


def test_r1_n_task_main_worker_simultaneous_isolation(tmp_path: Path) -> None:
    _, _, env, root = _make_launcher_ctx(tmp_path)
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding
        tm_before = try_build_task_main_binding()
        assert tm_before is not None
        parent_snapshot = dict(os.environ)
        h = _handoff(project_id=PROJECT_ID)
        child = wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id="wt-1",
            canonical_task_id="proj_w2r1:M1:W1:attempt-1", handoff=h)
        assert dict(os.environ) == parent_snapshot
        assert "AOTA_W3_HANDOFF_JSON" not in parent_snapshot
        tm_after = try_build_task_main_binding()
        assert tm_after is not None
        assert tm_after.trusted_task_main_context is not None
        assert child["AOTA_W3_PROJECT_ID"] == PROJECT_ID


# ---------------------------------------------------------------------------
# O. W1 scope projection regression
# ---------------------------------------------------------------------------

def test_r1_o_w1_scope_projection_intact() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(
        h, TrustedExecutionBinding(canonical_task_id="proj_w2r1:M1:W1:attempt-1", project_id=PROJECT_ID))
    verify_execution_package_integrity(pkg, h)
    assert SCOPE in pkg.instruction
    assert h.handoff_digest in pkg.instruction


# ---------------------------------------------------------------------------
# P. no role/project/session special case
# ---------------------------------------------------------------------------

def test_r1_p_no_placeholder_special_case() -> None:
    launcher_src = Path("aota_forge/composition/task_main_daily_launcher.py").read_text(encoding="utf-8")
    assert 'if handoff.work_role ==' not in launcher_src
    assert 'if task_kind ==' not in launcher_src
    assert "treat malformed handoff as task-main" not in launcher_src.lower()
    assert "ignore missing objective" not in launcher_src.lower()
    wvs_src = Path("aota_forge/composition/worker_vertical_slice.py").read_text(encoding="utf-8")
    assert "ignore missing objective" not in wvs_src.lower()
    assert "treat malformed handoff as task-main" not in wvs_src.lower()
    assert wvs.TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED is False
    assert wvs.ROLE_CONTEXT_SELECTION_EXPLICIT is True
    assert wvs.TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED is True
    assert wvs.MISSING_CONTEXT_FAILS_CLOSED is True
    assert wvs.CONFLICTING_CONTEXTS_FAIL_CLOSED is True
    assert wvs.MALFORMED_CONFLICTING_AUTHORITY_IGNORED is False


# ---------------------------------------------------------------------------
# Q. one aota.invoke surface preserved
# ---------------------------------------------------------------------------

def test_r1_q_one_shared_mcp() -> None:
    assert ONE_SHARED_AOTA_MCP is True
    assert tuple(MCP_PUBLIC_TOOLS) == ("aota.invoke",)
    assert AGENT_FACING_AOTA_TOOL == "aota.invoke"


# ---------------------------------------------------------------------------
# R. production launcher env not classified as conflicting (regression core)
# ---------------------------------------------------------------------------

def test_r1_r_launcher_env_not_conflicting(tmp_path: Path) -> None:
    _, _, env, _ = _make_launcher_ctx(tmp_path)
    with _EnvGuard() as g:
        g.clear_aota()
        os.environ.update(env)
        try:
            selected = wvs.select_runtime_context()
        except Exception as exc:
            pytest.fail(f"repaired launcher env must not fail closed: {exc}")
        assert selected.trusted_task_main_context is not None
        # And it must not be ambiguous either (exactly one valid context).
        assert selected.handoff.work_role.value == "task-main"
