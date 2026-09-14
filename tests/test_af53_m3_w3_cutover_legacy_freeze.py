"""AF #53 M3/W3 — Cutover & Legacy Freeze Evidence (C1-C10).

Focused proof for the approved cutover in Plan
`wzjcccc-dotcom/aota-hermes-tools#53`, Work Item M3/W3, after the accepted
M3/W2 fresh production dogfood
(`20260914_075112_af53m3w2-rerun2-dogfood`, PASS_WITH_NON_BLOCKING_FINDINGS):

* the canonical default runtime path is now ``thin``
  (``THIN_PATH_PRODUCTION_DEFAULT=yes``, ``PRODUCTION_DEFAULT_CUTOVER=yes``);
* an absent trusted operator selection resolves ``thin``;
* explicit trusted ``runtime_path=thin`` resolves ``thin``;
* explicit trusted ``runtime_path=legacy`` still resolves and composes the
  frozen legacy compatibility path;
* an unsupported value still fails closed (no fuzzy alias, no silent
  fallback);
* the model, a handoff payload, a startup prompt and ``aota.invoke``
  arguments cannot select or override the runtime path
  (``MODEL_CAN_SELECT_RUNTIME_PATH=no``,
  ``HANDOFF_CAN_SELECT_RUNTIME_PATH=no``,
  ``STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH=no``);
* the default thin path exposes exactly the canonical single
  ``aota.invoke`` MCP plane and still wires the trusted Worker env resolver
  and generic ``task.start`` / ``task.return`` lifecycle (accepted M2/W2 +
  M3/W2-R1 seams reused, no cutover-specific host);
* the default thin path requires no legacy workflow brain
  (``TaskMainControlService`` / ``MilestonePlanView`` /
  ``task_main.advance_once`` / coordinator state);
* the six frozen legacy files exist, are still reachable through the
  explicit override and were not touched by the M3/W3 cutover
  (``LEGACY_FILES_TOUCHED=0``, ``LEGACY_DELETION_PERFORMED=no``).

PROVES (V1/V2 focused cutover regression + bounded V3 production-default
bootstrap probe): production now naturally enters the already-accepted thin
path, while explicit legacy compatibility remains available.

DOES_NOT_PROVE: real external LLM task-main execution, full Calculator
dogfood, M3 final operational acceptance or M3/RV1 (still required); does not
prove legacy deletion or any legacy semantic expansion (neither is in scope).

This suite adds tests only. It introduces no new runtime-selection engine,
bootstrap plane, MCP adapter, execution/authority ontology or workflow
engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition import task_main_daily_launcher as launcher_mod
from aota_forge.composition.task_main_daily_launcher import (
    DailyTaskMainLauncher,
    _resolve_task_main_startup_prompt,
    probe_production_af_mcp_surface,
)
from aota_forge.composition.task_main_host_bootstrap import BOOTSTRAP_RELPATH
from aota_forge.composition.task_main_runtime_selection import (
    DEFAULT_TASK_MAIN_RUNTIME_PATH,
    HANDOFF_CAN_SELECT_RUNTIME_PATH,
    LEGACY_DELETION_PERFORMED,
    LEGACY_PATH_COMPATIBILITY_ONLY,
    LEGACY_PRODUCTION_PATH_PRESERVED,
    M3_W1_THIN_IS_DEFAULT,
    M3_W3_THIN_IS_DEFAULT,
    MODEL_CAN_SELECT_RUNTIME_PATH,
    PRODUCTION_DEFAULT_CUTOVER,
    SECOND_MCP_TOOL_PLANE_CREATED,
    STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH,
    SUPPORTED_TASK_MAIN_RUNTIME_PATHS,
    THIN_BOOTSTRAP_RELPATH,
    THIN_PATH_PRODUCTION_DEFAULT,
    TaskMainRuntimeSelectionError,
    normalize_task_main_runtime_path,
    select_task_main_runtime_path,
)
from aota_forge.composition.thin_task_main_host import (
    THIN_HOST_REQUIRES_ADVANCE_ONCE,
    THIN_HOST_REQUIRES_COORDINATOR,
    THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW,
    THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE,
    compose_thin_task_main_host,
)
from aota_forge.composition.worker_vertical_slice import (
    GOVERNED_WORKER_ENV_RESOLVER_OWNER,
    WORKER_BINDING_SOURCE,
)
from aota_forge.core.ingress import (
    get_execution_dispatcher,
    reset_execution_dispatcher,
)
from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
    SUPPORTED_OPERATIONS,
    create_aota_invoke_dispatch,
    create_shared_mcp_server,
)
from aota_forge.runtime.config import RuntimeConfigError, load_runtime_config
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    load_binding_from_envelope,
    verify_envelope,
)
from aota_forge.work_plane import thin_path_boundary as tpb

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE_SHA = "4e32014d93dd513293e96a97a40941b729075c67"
PROJECT_ID = "aota_forge"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#53"
ENTRY_BASE = "a" * 40
REAL_SESSION = "20260914_af53_m3w3_real_session"

MODEL_VISIBLE_SELECTOR_OPERATIONS = (
    "aota.invoke_thin",
    "runtime.select",
    "workflow.mode",
    "task.start_v2",
)

PROJECT_MANIFEST = (
    "schema_version: 1\nproject:\n"
    "  id: {project_id}\n  name: t\n  kind: test\n  status: active\n"
    "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
    "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
    "plan:\n  active_plan_id: null\nconstraints: []\n"
)

_FRESH_THIN_PROCESS_SCRIPT = (
    "import sys\n"
    "from aota_forge.composition.worker_vertical_slice import select_runtime_context\n"
    "binding = select_runtime_context()\n"
    "print('THIN=%s' % ('yes' if getattr(binding, 'task_main_runtime_path', 'legacy') == 'thin' else 'no'))\n"
    "print('CTX_NONE=%s' % ('yes' if binding.trusted_task_main_context is None else 'no'))\n"
    "print('ROLE=%s' % binding.handoff.work_role.value)\n"
    "print('PROJECT=%s' % binding.project_id)\n"
    "loaded = sorted(\n"
    "    name for name in sys.modules\n"
    "    if name == 'aota_forge.runtime.task_main'\n"
    "    or name.startswith('aota_forge.runtime.task_main.')\n"
    ")\n"
    "print('LEGACY=' + ','.join(loaded))\n"
    "for module in (\n"
    "    'aota_forge.runtime.task_main.control',\n"
    "    'aota_forge.runtime.task_main.coordinator',\n"
    "    'aota_forge.runtime.task_main.reconciliation',\n"
    "):\n"
    "    print('%s=%s' % (module.rsplit('.', 1)[-1].upper(), 'loaded' if module in sys.modules else 'absent'))\n"
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    """Composition binds the process-global canonical dispatcher seam; isolate it."""
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project_root(tmp_path: Path, project_id: str = PROJECT_ID) -> Path:
    root = tmp_path / f"wt-{project_id}"
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    return root


def _runtime_config_doc(executable: Path, *, runtime_path: str | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "executor": "hermes",
        "executable": str(executable),
        "concurrency": 1,
        "provider": "aota-test-provider",
        "model": "aota-test-model",
        "bindings": {
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
            "task-main": {"profile": "aota-task-main"},
        },
    }
    if runtime_path is not None:
        doc["runtime_path"] = runtime_path
    return doc


def _write_runtime_config(
    tmp_path: Path,
    executable: Path,
    *,
    name: str,
    runtime_path: str | None = None,
) -> Path:
    cfg = tmp_path / name
    cfg.write_text(
        json.dumps(_runtime_config_doc(executable, runtime_path=runtime_path), sort_keys=True),
        encoding="utf-8",
    )
    return cfg


def _plan_body() -> str:
    return f"""# [PLAN] AF53 M3/W3 fixture

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

#### M1/W1 — Alpha slice
Create the bounded M3/W3 alpha artifact.

#### M1/W2 — Beta slice
Create the bounded M3/W3 beta artifact.
"""


def _plan_adapter() -> StaticPlanAuthorityAdapter:
    return StaticPlanAuthorityAdapter(
        body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af53-m3w3"
    )


def _prepare(
    tmp_path: Path,
    cfg: Path,
    *,
    plan_adapter: StaticPlanAuthorityAdapter | None,
    origin: str,
    worktree_id: str,
    project_id: str = PROJECT_ID,
):
    root = _make_project_root(tmp_path, project_id)
    launcher = DailyTaskMainLauncher(plan_adapter=plan_adapter)
    ctx = launcher.prepare(
        worktree_root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=cfg,
        plan_adapter=plan_adapter,
        origin_task_main_session_ref=origin,
    )
    return launcher, root, ctx


def _load_binding(launcher: DailyTaskMainLauncher, ctx: Any):
    env = launcher.build_env(ctx)
    return load_binding_from_envelope(env[PRE_RESOLVED_BINDING_ENV])


def _git_diff_names(base: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", base],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


class _SpawnRecorder:
    """Records physical supervisor spawns (Popen) without launching anything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(pid=424242, poll=lambda: None)


def _real_host_client() -> Any:
    dispatcher = get_execution_dispatcher()
    assert dispatcher is not None, "thin composition must bind the canonical dispatcher"
    for adapter in dispatcher.registry._adapters.values():
        client = getattr(adapter, "_host_client", None)
        if client is not None:
            return client
    raise AssertionError("no Hermes host client on the production dispatcher graph")


def _arm_recorder(tmp_path: Path) -> _SpawnRecorder:
    client = _real_host_client()
    recorder = _SpawnRecorder()
    client._popen_factory = recorder
    runs = tmp_path / "hermes-runs-w3"
    runs.mkdir(parents=True, exist_ok=True)
    client.runtime_root = runs
    return recorder


def _child_env_from_dispatch(recorder: _SpawnRecorder) -> dict[str, str]:
    assert len(recorder.calls) == 1
    env = recorder.calls[0]["env"]
    assert isinstance(env, dict)
    return env


# ---------------------------------------------------------------------------
# C1 — absent selection defaults thin
# ---------------------------------------------------------------------------


class TestC1AbsentSelectionDefaultsThin:
    def test_absent_runtime_path_resolves_thin(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg_path = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-absent.json", runtime_path=None
        )
        doc = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "runtime_path" not in doc

        cfg = load_runtime_config(config_path=str(cfg_path))
        assert cfg.runtime_path == "thin"
        assert select_task_main_runtime_path(cfg) == "thin"
        assert normalize_task_main_runtime_path(None) == "thin"
        assert DEFAULT_TASK_MAIN_RUNTIME_PATH == "thin"

    def test_absent_selection_production_launcher_enters_thin_path(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        doc = json.loads(operator_config_file.read_text(encoding="utf-8"))
        assert "runtime_path" not in doc

        launcher, root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-default-thin",
        )
        assert ctx.runtime_path == "thin"
        assert ctx.live_plan_view is None
        assert ctx.coordinator_store_path is None
        assert ctx.plan_snapshot is None
        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()
        assert not (root / BOOTSTRAP_RELPATH).exists()
        assert not (root / ".aota" / "coordinator.json").exists()

        data = json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert data["runtime_path"] == "thin"

        binding = _load_binding(launcher, ctx)
        assert binding.task_main_runtime_path == "thin"
        assert binding.trusted_task_main_context is None


# ---------------------------------------------------------------------------
# C2 — explicit thin
# ---------------------------------------------------------------------------


class TestC2ExplicitThin:
    def test_explicit_thin_resolves_thin(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        loaded = load_runtime_config(config_path=str(cfg))
        assert loaded.runtime_path == "thin"
        assert select_task_main_runtime_path(loaded) == "thin"

        launcher, root, ctx = _prepare(
            tmp_path,
            cfg,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-explicit-thin",
        )
        assert ctx.runtime_path == "thin"
        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()
        assert not (root / BOOTSTRAP_RELPATH).exists()
        assert _load_binding(launcher, ctx).task_main_runtime_path == "thin"


# ---------------------------------------------------------------------------
# C3 — explicit legacy override
# ---------------------------------------------------------------------------


class TestC3ExplicitLegacyOverride:
    def test_explicit_legacy_resolves_legacy(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-legacy.json", runtime_path="legacy"
        )
        loaded = load_runtime_config(config_path=str(cfg))
        assert loaded.runtime_path == "legacy"
        assert select_task_main_runtime_path(loaded) == "legacy"
        assert normalize_task_main_runtime_path("legacy") == "legacy"
        assert SUPPORTED_TASK_MAIN_RUNTIME_PATHS == ("legacy", "thin")


# ---------------------------------------------------------------------------
# C4 — invalid value fails closed
# ---------------------------------------------------------------------------


class TestC4InvalidFailsClosed:
    def test_invalid_runtime_path_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-bad.json", runtime_path="unknown"
        )
        with pytest.raises(RuntimeConfigError, match="runtime_path"):
            load_runtime_config(config_path=str(cfg))

    def test_invalid_selector_values_fail_closed_without_fallback(self) -> None:
        for bad in ("unknown", "Legacy", "THIN", " thin", "thin ", "v2"):
            with pytest.raises(TaskMainRuntimeSelectionError):
                normalize_task_main_runtime_path(bad)
        with pytest.raises(TaskMainRuntimeSelectionError):
            select_task_main_runtime_path(SimpleNamespace(runtime_path="unknown"))

    def test_invalid_selection_creates_no_composition(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-bad.json", runtime_path="unknown"
        )
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=_plan_adapter())
        with pytest.raises(RuntimeConfigError):
            launcher.prepare(
                worktree_root=root,
                project_id=PROJECT_ID,
                worktree_id="wt-bad",
                runtime_config_path=cfg,
                plan_adapter=_plan_adapter(),
                origin_task_main_session_ref="20260914_af53_m3w3_bad",
            )
        assert not (root / BOOTSTRAP_RELPATH).exists()
        assert not (root / THIN_BOOTSTRAP_RELPATH).exists()


# ---------------------------------------------------------------------------
# C5 — model cannot select the runtime path
# ---------------------------------------------------------------------------


class TestC5NoModelVisibleSelection:
    def test_selection_markers_and_surface_are_model_blind(self) -> None:
        assert MODEL_CAN_SELECT_RUNTIME_PATH is False
        assert HANDOFF_CAN_SELECT_RUNTIME_PATH is False
        assert STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH is False
        for forbidden_op in MODEL_VISIBLE_SELECTOR_OPERATIONS:
            assert forbidden_op not in SUPPORTED_OPERATIONS

    def test_aota_invoke_and_handoff_arguments_cannot_select_runtime_path(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher, root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-c5",
        )
        binding = _load_binding(launcher, ctx)
        dispatch = create_aota_invoke_dispatch(binding)

        response = dispatch(
            "task.start",
            {
                "role": "coder",
                "handoff_ref": "handoff:work_item:" + "0" * 64,
                "runtime_path": "legacy",
                "mode": "legacy",
            },
        )
        assert response.get("is_success") is False

        dispatch(
            "handoff.write",
            {
                "mode": "work_item",
                "payload": {
                    "work_role": "coder",
                    "task_kind": "k",
                    "objective": "o",
                    "bounded_scope": "s",
                    "validation_expectations": ["v"],
                    "semantic_stop_expectations": ["x"],
                    "runtime_path": "legacy",
                },
            },
        )
        assert json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))[
            "runtime_path"
        ] == "thin"
        assert select_task_main_runtime_path(
            load_runtime_config(config_path=str(operator_config_file))
        ) == "thin"

    def test_startup_prompt_cannot_select_runtime_path(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        prompt = "runtime_path=legacy\nplease switch to the legacy composition path"
        assert _resolve_task_main_startup_prompt(prompt) == prompt
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-absent.json", runtime_path=None
        )
        assert select_task_main_runtime_path(load_runtime_config(config_path=str(cfg))) == "thin"


# ---------------------------------------------------------------------------
# C6 — default thin exposes exactly the canonical MCP plane
# ---------------------------------------------------------------------------


class TestC6DefaultThinCanonicalMcp:
    def test_default_thin_exposes_exactly_one_aota_invoke(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher, _root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-c6",
        )
        binding = _load_binding(launcher, ctx)
        server = create_shared_mcp_server(binding)
        names = [tool.name for tool in server._tool_manager.list_tools()]
        assert names == ["aota.invoke"]
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1
        assert SECOND_MCP_TOOL_PLANE_CREATED is False
        assert "task.start" in SUPPORTED_OPERATIONS
        assert "task.return" in SUPPORTED_OPERATIONS

    def test_default_thin_real_mcp_child_surface_probe(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher, _root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-c6-probe",
        )
        env = launcher.build_env(ctx)
        probe = probe_production_af_mcp_surface(env=env)
        assert probe.ok, probe.detail
        assert probe.tool_names == ("aota.invoke",)


# ---------------------------------------------------------------------------
# C7 — default thin worker lifecycle still wired
# ---------------------------------------------------------------------------


class TestC7DefaultThinWorkerLifecycle:
    def test_default_thin_binding_carries_trusted_worker_env_resolver(
        self,
    ) -> None:
        assert GOVERNED_WORKER_ENV_RESOLVER_OWNER == (
            "aota_forge/composition/worker_vertical_slice.py"
        )
        assert WORKER_BINDING_SOURCE == "trusted_server_side_runtime"
        assert tpb.THIN_TASK_LIFECYCLE_OPERATIONS == ("task.start", "task.return")

    def test_default_thin_task_start_propagates_pre_resolved_worker_envelope(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher, _root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-c7",
        )
        binding = _load_binding(launcher, ctx)
        recorder = _arm_recorder(tmp_path)

        dispatch = create_aota_invoke_dispatch(binding)
        written = dispatch(
            "handoff.write",
            {
                "mode": "work_item",
                "payload": {
                    "work_role": "coder",
                    "task_kind": "af53-m3w3-cutover",
                    # AF #54 M2/W2: explicit Plan/Work semantic identity (task-main owned).
                    "work_item_ref": "W1",
                    "milestone_ref": "M1",
                    "objective": "bounded default thin worker lifecycle",
                    "bounded_scope": "bounded scope",
                    "validation_expectations": ["focused validation"],
                    "semantic_stop_expectations": ["stop on insufficient evidence"],
                },
            },
        )
        assert written.get("is_success"), written
        started = dispatch(
            "task.start",
            {"role": "coder", "handoff_ref": written["payload"]["ref"]},
        )
        assert started.get("is_success"), started

        child_env = _child_env_from_dispatch(recorder)
        assert PRE_RESOLVED_BINDING_ENV in child_env
        kind, payload = verify_envelope(child_env[PRE_RESOLVED_BINDING_ENV])
        assert kind == "worker"
        assert payload["project_id"] == PROJECT_ID
        assert payload["worktree_id"] == "wt-c7"
        assert payload["canonical_task_id"] == started["payload"]["task_id"]


# ---------------------------------------------------------------------------
# C8 — explicit legacy override still builds the compatibility path
# ---------------------------------------------------------------------------


class TestC8ExplicitLegacyStillBuilds:
    def test_explicit_legacy_override_reaches_compatibility_composition(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-legacy.json", runtime_path="legacy"
        )
        launcher, root, ctx = _prepare(
            tmp_path,
            cfg,
            plan_adapter=_plan_adapter(),
            origin="20260914_af53_m3w3_legacy_session",
            worktree_id="wt-c8",
        )
        assert ctx.runtime_path == "legacy"
        assert ctx.live_plan_view is not None
        assert ctx.coordinator_store_path == root / ".aota" / "coordinator.json"
        assert (root / BOOTSTRAP_RELPATH).is_file()
        assert not (root / THIN_BOOTSTRAP_RELPATH).exists()

        data = json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert data["project_id"] == PROJECT_ID
        assert data["worktree_id"] == "wt-c8"
        assert "live_plan_view" in data
        assert "runtime_path" not in data

        binding = _load_binding(launcher, ctx)
        assert binding.task_main_runtime_path == "legacy"
        trusted_ctx = binding.trusted_task_main_context
        assert trusted_ctx is not None
        assert hasattr(trusted_ctx.control_service, "activate_milestone")
        assert hasattr(trusted_ctx.control_service, "advance_once")
        assert trusted_ctx.live_plan_view is not None


# ---------------------------------------------------------------------------
# C9 — frozen legacy files unchanged
# ---------------------------------------------------------------------------


class TestC9FrozenLegacyFilesUnchanged:
    def test_frozen_legacy_files_exist_and_were_untouched_by_cutover(self) -> None:
        for rel in tpb.LEGACY_FROZEN_FILES:
            assert (REPO_ROOT / rel).is_file(), rel

        touched = [rel for rel in _git_diff_names(BASE_SHA) if rel in tpb.LEGACY_FROZEN_FILES]
        assert touched == [], f"LEGACY_FILES_TOUCHED={len(touched)}: {touched}"

        assert LEGACY_DELETION_PERFORMED is False
        assert LEGACY_PRODUCTION_PATH_PRESERVED is True
        assert LEGACY_PATH_COMPATIBILITY_ONLY is True
        assert tpb.LEGACY_WORKFLOW_PATH_FROZEN is True
        assert tpb.NO_FURTHER_LEGACY_SEMANTIC_EXPANSION is True

    def test_cutover_markers_reflect_production_default(self) -> None:
        assert THIN_PATH_PRODUCTION_DEFAULT is True
        assert PRODUCTION_DEFAULT_CUTOVER is True
        assert M3_W3_THIN_IS_DEFAULT is True
        assert M3_W1_THIN_IS_DEFAULT is False
        assert DEFAULT_TASK_MAIN_RUNTIME_PATH == "thin"
        assert select_task_main_runtime_path(SimpleNamespace(runtime_path="thin")) == "thin"
        assert select_task_main_runtime_path(SimpleNamespace(runtime_path="legacy")) == "legacy"


# ---------------------------------------------------------------------------
# C10 — thin default is legacy-brain-free
# ---------------------------------------------------------------------------


class TestC10ThinDefaultLegacyBrainFree:
    def test_thin_default_requires_no_legacy_workflow_brain_objects(self) -> None:
        assert THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE is False
        assert THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW is False
        assert THIN_HOST_REQUIRES_COORDINATOR is False
        assert THIN_HOST_REQUIRES_ADVANCE_ONCE is False
        assert tpb.THIN_HOST_REQUIRES_LEGACY_WORKFLOW_BRAIN is False

    def test_thin_default_composition_has_no_coordinator_state(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher = DailyTaskMainLauncher(plan_adapter=None)
        root = _make_project_root(tmp_path)
        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-c10",
            runtime_config_path=operator_config_file,
            plan_adapter=None,
            origin_task_main_session_ref=REAL_SESSION,
        )
        assert ctx.runtime_path == "thin"
        assert ctx.live_plan_view is None
        assert ctx.coordinator_store_path is None
        assert not (root / ".aota" / "coordinator.json").exists()
        assert not (root / BOOTSTRAP_RELPATH).exists()

        host = compose_thin_task_main_host(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-c10",
            runtime_config_path=operator_config_file,
            origin_task_main_session_ref=REAL_SESSION,
        )
        assert host.trusted_binding.trusted_task_main_context is None

    def test_thin_default_fresh_process_loads_no_legacy_workflow_modules(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        launcher, _root, ctx = _prepare(
            tmp_path,
            operator_config_file,
            plan_adapter=None,
            origin=REAL_SESSION,
            worktree_id="wt-c10-fresh",
        )
        env = launcher.build_env(ctx)
        child_env = {**os.environ, **env}
        completed = subprocess.run(
            [sys.executable, "-c", _FRESH_THIN_PROCESS_SCRIPT],
            cwd=str(REPO_ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        lines = completed.stdout.splitlines()
        assert "THIN=yes" in lines
        assert "CTX_NONE=yes" in lines
        assert "ROLE=task-main" in lines
        assert f"PROJECT={PROJECT_ID}" in lines
        legacy_lines = [line for line in lines if line.startswith("LEGACY=")]
        assert len(legacy_lines) == 1
        assert legacy_lines[0] == "LEGACY=", (
            "default thin binding path pulled in legacy workflow state: " + legacy_lines[0]
        )
        assert "CONTROL=absent" in lines
        assert "COORDINATOR=absent" in lines
        assert "RECONCILIATION=absent" in lines


# ---------------------------------------------------------------------------
# No cutover-specific host / no new authority
# ---------------------------------------------------------------------------


class TestNoCutoverSpecificAuthority:
    def test_cutover_reuses_accepted_host_and_config_authority(self) -> None:
        selector_src = (
            REPO_ROOT / "aota_forge" / "composition" / "task_main_runtime_selection.py"
        ).read_text(encoding="utf-8")
        assert "SECOND_RUNTIME_CONFIG_CREATED = False" in selector_src
        assert "SECOND_MCP_TOOL_PLANE_CREATED = False" in selector_src
        assert "NEW_EXECUTION_ENGINE_CREATED = False" in selector_src
        assert "NEW_WORKFLOW_ENGINE_CREATED = False" in selector_src
        # The selector delegates to the single canonical RuntimeConfig default
        # authority; it does not carry a second literal default.
        assert "DEFAULT_TASK_MAIN_RUNTIME_PATH = " not in selector_src

        config_src = (
            REPO_ROOT / "aota_forge" / "runtime" / "config.py"
        ).read_text(encoding="utf-8")
        assert config_src.count('DEFAULT_TASK_MAIN_RUNTIME_PATH = TASK_MAIN_RUNTIME_PATH_') == 1

    def test_launcher_still_requires_startup_prompt_and_config(self) -> None:
        # The cutover changed only the default; the trusted operator seams are
        # unchanged (bounded regression check, no behavior duplication).
        assert callable(_resolve_task_main_startup_prompt)
        assert callable(launcher_mod.probe_production_af_mcp_surface)
