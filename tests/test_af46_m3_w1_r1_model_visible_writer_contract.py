"""AF #46 M3/W1-R1 — Model-visible writer contract & governed Work semantics (TIER_1..4).

F1: submit contract must be eager (OPERATION_GUIDANCE derived from canonical
descriptor, zero extra calls). F2: authoritative Work semantics must reach the
model via bounded GovernedWorkSemanticView carried in MilestonePlanView and
surfaced through activate/recover/advance (no new read operation).

Generic fixtures only (no dogfood literals). Unique marker proves Plan->model
flow comes from Plan authority, not startup prompt.
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
from aota_forge.core.plan.normalize import PlanNormalizationError, normalize_portable_plan
from aota_forge.core.plan.projection import (
    get_governed_work_semantic_view,
    get_milestone_work_semantic_views,
    project_milestone_views,
)
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    activate_milestone,
    build_projection_required_context,
    commit_task_main_work_projection,
    resolve_task_main_work_handoff,
)
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.handoff_runtime import (
    WORK_SCOPE_INSUFFICIENT,
    WorkScopeInsufficientError,
    WorkSemanticProjection,
    parse_model_work_proposal,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph

PROJECT_ID = "proj_w1r1"
PLAN_AUTH = "example-owner/example-governance#999"
ENTRY_BASE = "e" * 40
MARKER = "W1R1_ALPHA_7Q2Z"


def _body(marker: str = MARKER) -> str:
    return f"""# [PLAN] W1-R1 generic
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_STATUS=in_progress
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```

## M7 — Generic milestone for {marker}

Goal: implement {marker} feature for composition proof.

A — first work implements {marker} alpha part.

B — second work implements {marker} beta part.
"""


def _doc(marker: str = MARKER):
    return normalize_portable_plan(_body(marker), source_revision="rev-1")


def _live(marker: str = MARKER):
    doc = _doc(marker)
    live, _ = project_milestone_views(doc, plan_authority=PLAN_AUTH)
    return doc, live


def _stores(tmp_path: Path, tag: str = "t"):
    coord_path = tmp_path / f"coord_{tag}.json"
    exec_path = tmp_path / f"exec_{tag}.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    return FileBackedTaskMainCoordinatorStore(coord_path), FileBackedExecutionStateStore(exec_path)


def _dispatcher(exec_store) -> ExecutionDispatcher:
    from aota_forge.core.execution.registry import ExecutorRegistry
    from tests.test_m3_w1_task_main_coordinator import M3W1FakeAdapter  # type: ignore

    reg = ExecutorRegistry()
    reg.register(M3W1FakeAdapter())  # type: ignore[arg-type]
    from aota_forge.core.execution.durable_state import OriginSessionRef

    return ExecutionDispatcher(reg, state_store=exec_store, origin_session_ref=OriginSessionRef(value="sess-1"))


def _activate(coord_store, exec_store, view):
    disp = _dispatcher(exec_store)
    h = activate_milestone(
        store=coord_store, plan_view=view, origin_task_main_session_ref="sess-1",
        execution_dispatcher=disp, executor_id="m3w1-fake", project_id=PROJECT_ID,
    )
    return h, disp


# ---------------------------------------------------------------------------
# Tier 1 — Core work semantic view
# ---------------------------------------------------------------------------


class TestTier1CoreWorkSemanticView:
    def test_view_bounded_and_tied(self) -> None:
        doc, live = _live()
        assert len(live.work_semantics) == 2
        ids = sorted(v.work_item_id for v in live.work_semantics)
        assert ids == ["A", "B"]
        for v in live.work_semantics:
            assert v.milestone_id == "M7"
            assert MARKER in v.objective
            assert MARKER in v.semantic_context
            assert len(v.objective) <= 1024
            assert len(v.semantic_context) <= 2048
        # Tied to correct Work ID (A view mentions A, not B beta).
        va = live.get_work_semantic_view("A")
        assert va is not None and "alpha" in va.objective.lower()
        vb = live.get_work_semantic_view("B")
        assert vb is not None and "beta" in vb.objective.lower()

    def test_digest_covers_semantics(self) -> None:
        doc1 = _doc(MARKER)
        doc2 = _doc("W1R1_BETA_9X8Y")
        assert portable_plan_digest(doc1) != portable_plan_digest(doc2)
        # Digest in live view equals document digest.
        live, _ = project_milestone_views(_doc(), plan_authority=PLAN_AUTH)
        assert live.plan_digest == portable_plan_digest(_doc())

    def test_missing_semantics_fails_closed(self) -> None:
        body = f"""# [PLAN] missing
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M9
M9_STATUS=in_progress
M9_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M9_DAG=X -> Y
M9_WORK_ITEMS=X, Y
```
"""
        doc = normalize_portable_plan(body, source_revision="r")
        # No milestone prose at all -> no views (best-effort empty).
        assert get_milestone_work_semantic_views(doc, "M9") == ()
        with pytest.raises(PlanNormalizationError) as e:
            get_governed_work_semantic_view(doc, "M9", "X")
        assert e.value.diagnostic_code == "MISSING_WORK_SEMANTICS"

    def test_foreign_work_fails_closed(self) -> None:
        doc, _ = _live()
        with pytest.raises(PlanNormalizationError):
            get_governed_work_semantic_view(doc, "M7", "Z9")

    def test_writer_regression_still_passes(self, tmp_path: Path) -> None:
        # Existing writer contract unchanged (D9 preserved).
        from tests.test_af46_m3_w1_canonical_work_projection_writer import _proposal_dict, _view

        coord_store, exec_store = _stores(tmp_path, "reg")
        view = _view(["W1"])
        handle, _ = _activate(coord_store, exec_store, view)
        wid, typed = parse_model_work_proposal(_proposal_dict("W1"))
        updated = commit_task_main_work_projection(
            store=coord_store, coordinator_id=handle.coordinator_id,
            live_plan_view=view, work_item_id=wid, projection=typed,
        )
        assert "W1" in dict(getattr(updated, "work_projections", {}))

    def test_missing_governed_semantics_rejects_submit(self, tmp_path: Path) -> None:
        # New path: view carries semantics for A/B; submitting unknown Z fails.
        # And a view with semantics for M7 but submitting for a Work without
        # view (simulate by stripping B) fails closed (no heuristic).
        doc, live = _live()
        coord_store, exec_store = _stores(tmp_path, "miss")
        handle, _ = _activate(coord_store, exec_store, live)
        # Unknown Work -> binding error (existing).
        with pytest.raises(Exception):
            commit_task_main_work_projection(
                store=coord_store, coordinator_id=handle.coordinator_id,
                live_plan_view=live, work_item_id="Z9",
                projection={"objective": "o", "bounded_scope": "s x", "validation_expectations": ["v"], "semantic_stop_expectations": ["t"]},
            )
        # View with only A (strip B) + submit B -> missing semantics fail-closed.
        from dataclasses import replace

        live_a_only = replace(live, work_semantics=tuple(v for v in live.work_semantics if v.work_item_id == "A"))
        with pytest.raises(WorkScopeInsufficientError) as e2:
            commit_task_main_work_projection(
                store=coord_store, coordinator_id=handle.coordinator_id,
                live_plan_view=live_a_only, work_item_id="B",
                projection={"objective": "Goal B", "bounded_scope": "Scope B in src/b.py.", "validation_expectations": ["B ok"], "semantic_stop_expectations": ["stop"]},
            )
        assert e2.value.code == WORK_SCOPE_INSUFFICIENT


# ---------------------------------------------------------------------------
# Tier 2 — bootstrap/descriptor parity
# ---------------------------------------------------------------------------


class TestTier2BootstrapParity:
    def test_parity_required_fields(self) -> None:
        from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map
        from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap
        from aota_forge.work_plane.task_main_descriptors import build_task_main_operation_guidance
        from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        m = load_operation_descriptor_map(discover_canonical_project_root())
        desc = m["task_main.submit_work_projection"]
        yaml_fields = sorted(s.name for s in desc.inputs)
        assert yaml_fields == ["bounded_scope", "objective", "semantic_stop_expectations", "validation_expectations", "work_item_id"]
        guid = build_task_main_operation_guidance()["task_main.submit_work_projection"]
        boot_fields = sorted(guid["required"].keys())
        assert boot_fields == yaml_fields
        # Core parser accepts exactly those fields.
        payload = {
            "work_item_id": "A",
            "objective": "Goal",
            "bounded_scope": "Scope in src/a.py.",
            "validation_expectations": ["ok"],
            "semantic_stop_expectations": ["stop"],
        }
        wid, _ = parse_model_work_proposal(payload)
        assert wid == "A"
        # Bootstrap exposes contract eager (no skill.open needed).
        _handoff = TaskHandoff(
            work_role="task-main", task_kind="task-main-control", objective="o",
            bounded_scope="s distinct", validation_expectations=("v",),
            semantic_stop_expectations=("t",),
            work_item_ref=SemanticReference(ref="A"), milestone_ref=SemanticReference(ref="M7"),
        )
        _surface = create_role_tool_surface(
            "task-main",
            eager=["task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once", "task_main.submit_work_projection"],
            progressive=["result.hydrate"],
        )

        class _SB:
            worktree_root = "/tmp"
            project_id = "p"
            worktree_id = "w"

        class _B:
            pass

        _B.handoff = _handoff
        _B.tool_surface = _surface
        _B.sandbox = _SB()
        _B.project_id = "p"
        _B.worktree_id = "w"
        _B.canonical_task_id = "t"

        out = handle_role_bootstrap(_B(), {})
        assert "OPERATION_GUIDANCE" in out
        assert "task_main.submit_work_projection" in out["OPERATION_GUIDANCE"]
        # TOOL_SURFACE preserved (this synthetic binding carries the existing
        # 4-control surface; W5 production surfaces add the normal-path ops).
        eager_names = sorted(e.get("capability_name") for e in out["TOOL_SURFACE"]["eager"])
        assert eager_names == sorted(["task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once", "task_main.submit_work_projection"])
        # W5 amended contract: inline when it fits, otherwise the MCP transport
        # projects a real governed by_ref result with hydration claims (no
        # semantic truncation, no inline-limit increase; bounded by the
        # existing durable payload bound).
        assert len(json.dumps(out).encode("utf-8")) <= 64 * 1024

    def test_no_duplicate_schema_authority(self) -> None:
        import aota_forge.work_plane.task_main_descriptors as dmod
        import aota_forge.work_plane.role_bootstrap as rbmod

        assert getattr(dmod, "TOOL_SCHEMA_SECOND_AUTHORITY", "no") == "no"
        # Guidance derived (function reads descriptor, not hardcoded list).
        import inspect

        src = inspect.getsource(dmod.build_task_main_operation_guidance)
        assert "TASK_MAIN_SUBMIT_DESCRIPTOR" in src


# ---------------------------------------------------------------------------
# Tier 3 — deterministic semantic composition with unique marker
# ---------------------------------------------------------------------------


class TestTier3Composition:
    def test_plan_to_handoff_marker_flow(self, tmp_path: Path) -> None:
        doc, live = _live()
        coord_store, exec_store = _stores(tmp_path, "comp")
        handle, _ = _activate(coord_store, exec_store, live)
        # Activate exposes projection_required with marker (no extra read).
        req, miss = build_projection_required_context(
            live, wi_status=dict(handle.state.wi_status),
            work_projections=dict(getattr(handle.state, "work_projections", {}) or {}),
        )
        assert miss == []
        assert [r["work_item_id"] for r in req] == ["A"]
        assert MARKER in req[0]["governed_work_semantics"]["semantic_context"]
        # Model reasoning (deterministic, uses marker from governed context).
        gov = req[0]["governed_work_semantics"]
        assert MARKER in gov["objective"] or MARKER in gov["semantic_context"]
        payload = {
            "work_item_id": "A",
            "objective": f"Implement {MARKER} alpha part",
            "bounded_scope": f"Implement {MARKER} alpha in src/alpha.py, validate.",
            "validation_expectations": ["alpha returns expected"],
            "semantic_stop_expectations": ["stop if scope unclear"],
        }
        wid, typed = parse_model_work_proposal(payload)
        updated = commit_task_main_work_projection(
            store=coord_store, coordinator_id=handle.coordinator_id,
            live_plan_view=live, work_item_id=wid, projection=typed,
        )
        assert "A" in dict(getattr(updated, "work_projections", {}))
        handoff = resolve_task_main_work_handoff(state=updated, work_item_id="A", live_plan_view=live)
        assert MARKER in handoff.objective or MARKER in handoff.bounded_scope
        # Marker did not come from startup prompt (prompt is generic).
        assert MARKER not in open("prompts/task-main-startup.default.md", encoding="utf-8").read()

    def test_drift_fails_closed(self, tmp_path: Path) -> None:
        from dataclasses import replace

        doc, live = _live()
        coord_store, exec_store = _stores(tmp_path, "drift")
        handle, _ = _activate(coord_store, exec_store, live)
        payload = {
            "work_item_id": "A",
            "objective": "Goal",
            "bounded_scope": "Scope in src/a.py.",
            "validation_expectations": ["ok"],
            "semantic_stop_expectations": ["stop"],
        }
        wid, typed = parse_model_work_proposal(payload)
        commit_task_main_work_projection(
            store=coord_store, coordinator_id=handle.coordinator_id,
            live_plan_view=live, work_item_id=wid, projection=typed,
        )
        drifted = replace(live, plan_digest="f" * 64)
        with pytest.raises(Exception):
            commit_task_main_work_projection(
                store=coord_store, coordinator_id=handle.coordinator_id,
                live_plan_view=drifted, work_item_id="A", projection=payload,
            )


# ---------------------------------------------------------------------------
# Tier 4 — subprocess model-visible roundtrip (blind client, no source read)
# ---------------------------------------------------------------------------


def _make_task_main_binding_for_view(tmp_path: Path, view: MilestonePlanView, session_ref: str = "sess-1"):
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


class TestTier4ProcessBoundary:
    def test_blind_client_roundtrip(self, tmp_path: Path) -> None:
        # Blind client may inspect ONLY model-visible payloads (bootstrap +
        # activate). No source, no operations.yaml, no Skill files, no Plan
        # body, no fixtures with schema. Marker must flow from Plan authority.
        doc, live = _live()
        binding, root = _make_task_main_binding_for_view(tmp_path, live)
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        adapter = _SharedAotaMcpAdapter(binding)
        rb = adapter.invoke("role.bootstrap", {})
        assert rb["ok"] is True
        if rb["output_mode"] == "inline":
            boot = rb["payload"]
        else:
            # W5: an over-bound bootstrap is consumed through its model-visible
            # hydration claims (existing result.hydrate; no guessing).
            hydration = rb["hydration"]
            assert hydration["operation"] == "result.hydrate"
            hb = adapter.invoke("result.hydrate", dict(hydration["arguments"]))
            assert hb["ok"] is True, hb
            boot = json.loads(hb["payload"]["content"])
        assert "OPERATION_GUIDANCE" in boot
        guid = boot["OPERATION_GUIDANCE"]["task_main.submit_work_projection"]
        assert sorted(guid["required"].keys()) == ["bounded_scope", "objective", "semantic_stop_expectations", "validation_expectations", "work_item_id"]
        ra = adapter.invoke("task_main.activate_milestone", {})
        assert ra["ok"] is True, ra
        assert "projection_required" in ra["payload"]
        preq = ra["payload"]["projection_required"]
        assert len(preq) == 1 and preq[0]["work_item_id"] == "A"
        assert MARKER in preq[0]["governed_work_semantics"]["semantic_context"]
        # One typed call suffices (no skill.open, no hydrate, no describe).
        gov = preq[0]["governed_work_semantics"]
        payload = {
            "work_item_id": gov["work_item_id"],
            "objective": f"Implement {MARKER} alpha part",
            "bounded_scope": f"Implement {MARKER} alpha in src/alpha.py, validate.",
            "validation_expectations": ["alpha returns expected"],
            "semantic_stop_expectations": ["stop if scope unclear"],
        }
        script = (
            "import os, sys, json\n"
            "sys.path.insert(0, os.getcwd())\n"
            f"os.environ['{BOOTSTRAP_ENV_ROOT}'] = {str(root)!r}\n"
            "from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding\n"
            "from aota_forge.mcp_transport import _SharedAotaMcpAdapter\n"
            "binding = try_build_task_main_binding()\n"
            "adapter = _SharedAotaMcpAdapter(binding)\n"
            f"res = adapter.invoke('task_main.submit_work_projection', {payload!r})\n"
            "print(json.dumps({'ok': res['ok'], 'payload': res.get('payload'), 'error': res.get('error')}))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path.cwd()) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=str(Path.cwd()), timeout=60, env=env)
        assert proc.returncode == 0, proc.stderr[:2000]
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ok"] is True, out
        # Restart/recover durability (no retransmission of semantics needed).
        old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        try:
            nb = try_build_task_main_binding()
            na = _SharedAotaMcpAdapter(nb)
            assert na.invoke("task_main.recover_coordinator", {})["ok"] is True
            again = na.invoke("task_main.submit_work_projection", payload)
            assert again["ok"] is True
        finally:
            if old_root is None:
                os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
            else:
                os.environ[BOOTSTRAP_ENV_ROOT] = old_root

    def test_advance_missing_exposes_context(self, tmp_path: Path) -> None:
        doc, live = _live()
        binding, _ = _make_task_main_binding_for_view(tmp_path, live)
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        adapter = _SharedAotaMcpAdapter(binding)
        assert adapter.invoke("task_main.activate_milestone", {})["ok"] is True
        r_adv = adapter.invoke("task_main.advance_once", {})
        # Fail-closed (no dispatch without projection) but exposes context.
        assert r_adv["ok"] is False, r_adv
        assert r_adv["error"]["code"] == WORK_SCOPE_INSUFFICIENT
        assert "projection_required" in r_adv["error"]
        assert MARKER in json.dumps(r_adv["error"]["projection_required"])


# ---------------------------------------------------------------------------
# Regression of the exact old failure class (I46-B002 F1/F2)
# ---------------------------------------------------------------------------


class TestI46B002Regression:
    def test_f1_old_name_only_insufficient(self, tmp_path: Path) -> None:
        # Before: model saw operation name only -> guessed fields -> impossible.
        # After: bootstrap contains exact contract -> valid payload constructible.
        doc, live = _live()
        binding, _ = _make_task_main_binding_for_view(tmp_path, live)
        from aota_forge.mcp_transport import _SharedAotaMcpAdapter

        adapter = _SharedAotaMcpAdapter(binding)
        rb = adapter.invoke("role.bootstrap", {})
        if rb["output_mode"] == "inline":
            boot = rb["payload"]
        else:
            # W5: an over-bound bootstrap is consumed through its model-visible
            # hydration claims (existing result.hydrate; no guessing).
            hydration = rb["hydration"]
            hb = adapter.invoke("result.hydrate", dict(hydration["arguments"]))
            assert hb["ok"] is True, hb
            boot = json.loads(hb["payload"]["content"])
        assert "OPERATION_GUIDANCE" in boot
        req = boot["OPERATION_GUIDANCE"]["task_main.submit_work_projection"]["required"]
        assert set(req.keys()) == {"work_item_id", "objective", "bounded_scope", "validation_expectations", "semantic_stop_expectations"}
        # Guessed old shape (e.g. string expectations) now fails typed.
        bad = {
            "work_item_id": "A",
            "objective": "Goal",
            "bounded_scope": "Scope.",
            "validation_expectations": "should be list, not string",
            "semantic_stop_expectations": ["stop"],
        }
        with pytest.raises(WorkScopeInsufficientError):
            parse_model_work_proposal(bad)

    def test_f2_identity_only_insufficient(self, tmp_path: Path) -> None:
        # Before: Milestone identity + Work ID visible, product objective not.
        # After: control response carries governed Work semantics.
        doc, live = _live()
        assert live.plan_authority == PLAN_AUTH
        assert live.milestone_id == "M7"
        assert MARKER not in live.milestone_id
        assert MARKER in live.get_work_semantic_view("A").semantic_context
        coord_store, exec_store = _stores(tmp_path, "f2")
        handle, _ = _activate(coord_store, exec_store, live)
        req, _ = build_projection_required_context(
            live, wi_status=dict(handle.state.wi_status),
            work_projections=dict(getattr(handle.state, "work_projections", {}) or {}),
        )
        assert req and MARKER in req[0]["governed_work_semantics"]["semantic_context"]
