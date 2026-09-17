"""AF #53 M3/W1 — Side-by-Side Production Activation (A–K).

Focused proof for the approved `foundation_plus_plugins` boundary convergence
(Plan `wzjcccc-dotcom/aota-hermes-tools#53`, Work Item M3/W1).

Mission proven here:

* bounded trusted operator/runtime selection between the existing legacy
  compatibility path and the accepted M2 thin production candidate
  (`LEGACY_PRODUCTION_PATH_PRESERVED=yes`,
  `THIN_PRODUCTION_CANDIDATE_ACTIVATABLE=yes`,
  `DEFAULT_RUNTIME_PATH=legacy` at M3/W1; M3/W3 later cut the default over to
  `thin` after the accepted M3/W2 dogfood — the default assertions in this
  suite were updated accordingly);
* selection is a trusted deployment mechanic in the existing operator
  RuntimeConfig authority, never a model/handoff/startup-prompt/aota.invoke
  argument; unsupported values fail closed with no fuzzy alias and no silent
  legacy fallback;
* the thin production candidate is rebuilt through the accepted M2 thin
  composition, carries ``trusted_task_main_context=None`` and requires no
  MilestonePlanView / TaskMainControlService / coordinator store /
  task_main.advance_once; the thin executed path loads no legacy
  workflow-brain module;
* the legacy compatibility path still composes its existing
  TrustedTaskMainRuntimeContext (control service / live Plan view) unchanged;
* both paths share the canonical single ``aota.invoke`` agent surface, the
  canonical RuntimeConfig authority and the hard project/worktree boundary;
* a production-wiring preflight shows thin selection reaching launch-ready
  production composition through the real launcher/bootstrap seam and the
  real production AF MCP child (deterministic process/session doubles only
  for the external Hermes session boundary).

PROVES (V0 + deterministic V1 + bounded V2 over real composed components +
production-wiring preflight):

* side-by-side coexistence of legacy and thin production compositions at one
  source revision (M3/W1 default was legacy; M3/W3 cut the default to thin);
* trusted selection authority, fail-closed invalid handling, M2 reuse and
  legacy-free thin executed path.

DOES_NOT_PROVE:

* does not prove a real external LLM task-main, real Calculator dogfood,
  real Worker execution or real production cutover (M3/W2 owns fresh
  production dogfood; M3/W3 owns cutover and owns the dedicated cutover
  suite `tests/test_af53_m3_w3_cutover_legacy_freeze.py`);
* does not prove the legacy path's full historical behavior matrix (covered
  by the existing accepted suites, rerun alongside this one);
* does not claim ``M3_W2_DOGFOOD_PASS`` or ``V4``; the M3/W3 production
  default cutover evidence is owned by
  ``tests/test_af53_m3_w3_cutover_legacy_freeze.py``.

This suite adds tests only. It introduces no execution engine, authority
engine, result ontology, session engine, workflow engine, second
RuntimeConfig or generic plugin framework.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    HermesReentryResult,
    PersistedSessionToolSurface,
)
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.adapters.plan_authority.binding import PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
from aota_forge.composition import task_main_daily_launcher as launcher_mod
from aota_forge.composition.task_main_daily_launcher import (
    PHASE1_SESSION_BOOTSTRAP_PROMPT,
    DailyTaskMainLauncher,
    _resolve_task_main_startup_prompt,
    probe_production_af_mcp_surface,
)
from aota_forge.composition.task_main_host_bootstrap import BOOTSTRAP_RELPATH
from aota_forge.composition.task_main_runtime_selection import (
    DEFAULT_TASK_MAIN_RUNTIME_PATH,
    HANDOFF_CAN_SELECT_RUNTIME_PATH,
    M3_W1_THIN_IS_DEFAULT,
    M3_W3_THIN_IS_DEFAULT,
    MODEL_CAN_SELECT_RUNTIME_PATH,
    PRODUCTION_DEFAULT_CUTOVER,
    STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH,
    SUPPORTED_TASK_MAIN_RUNTIME_PATHS,
    THIN_BOOTSTRAP_RELPATH,
    THIN_PATH_PRODUCTION_DEFAULT,
    TaskMainRuntimeSelectionError,
    materialize_thin_task_main_bootstrap,
    normalize_task_main_runtime_path,
    select_task_main_runtime_path,
)
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
    SUPPORTED_OPERATIONS,
    create_aota_invoke_dispatch,
    create_shared_mcp_server,
)
from aota_forge.runtime.config import (
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
)
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    create_task_main_envelope,
    load_binding_from_envelope,
    verify_envelope,
)
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

REPO_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ID = "aota_forge"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#53"
ENTRY_BASE = "a" * 40
REAL_SESSION = "20260913_af53_m3w1_real_session"

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

FORBIDDEN_THIN_BOOTSTRAP_KEYS = (
    "live_plan_view",
    "next_milestone_view",
    "coordinator_store_path",
    "coordinator_id",
    "work_semantics",
    "plan_authority",
    "milestone_id",
)

MODEL_VISIBLE_SELECTOR_OPERATIONS = (
    "aota.invoke_thin",
    "task.start_v2",
    "runtime.select",
    "workflow.mode",
)

_FRESH_THIN_PROCESS_SCRIPT = textwrap.dedent(
    """
    import sys

    from aota_forge.composition.worker_vertical_slice import select_runtime_context

    binding = select_runtime_context()
    print("THIN=%s" % ("yes" if getattr(binding, "task_main_runtime_path", "legacy") == "thin" else "no"))
    print("CTX_NONE=%s" % ("yes" if binding.trusted_task_main_context is None else "no"))
    print("ROLE=%s" % binding.handoff.work_role.value)
    print("PROJECT=%s" % binding.project_id)

    loaded = sorted(
        name
        for name in sys.modules
        if name == "aota_forge.runtime.task_main"
        or name.startswith("aota_forge.runtime.task_main.")
    )
    print("LEGACY=" + ",".join(loaded))
    for module in (
        "aota_forge.runtime.task_main.control",
        "aota_forge.runtime.task_main.coordinator",
        "aota_forge.runtime.task_main.reconciliation",
    ):
        print("%s=%s" % (module.rsplit(".", 1)[-1].upper(), "loaded" if module in sys.modules else "absent"))
    """
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    """Composition binds the process-global canonical dispatcher seam; isolate it."""
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


def _runtime_config_doc(executable: Path, *, runtime_path: str | None = None) -> dict[str, Any]:
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
    name: str = "runtime.json",
    runtime_path: str | None = None,
) -> Path:
    cfg = tmp_path / name
    cfg.write_text(
        json.dumps(_runtime_config_doc(executable, runtime_path=runtime_path), sort_keys=True),
        encoding="utf-8",
    )
    return cfg


def _write_legacy_runtime_config(tmp_path: Path, executable: Path) -> Path:
    """Explicit trusted legacy compatibility selection (post-M3/W3 override)."""
    return _write_runtime_config(
        tmp_path, executable, name="runtime-legacy.json", runtime_path="legacy"
    )


def _plan_body() -> str:
    return f"""# [PLAN] AF53 M3/W1 fixture

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
Create the bounded M3/W1 alpha artifact.

#### M1/W2 — Beta slice
Create the bounded M3/W1 beta artifact.
"""


def _plan_adapter() -> StaticPlanAuthorityAdapter:
    return StaticPlanAuthorityAdapter(
        body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af53-m3w1"
    )


def _prepare_legacy(tmp_path: Path, cfg: Path):
    root = _make_project_root(tmp_path)
    launcher = DailyTaskMainLauncher(plan_adapter=_plan_adapter())
    ctx = launcher.prepare(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-legacy",
        runtime_config_path=cfg,
        plan_adapter=_plan_adapter(),
        origin_task_main_session_ref="20260913_af53_m3w1_legacy_session",
    )
    return launcher, root, ctx


def _prepare_thin(
    tmp_path: Path,
    cfg: Path,
    *,
    worktree_id: str = "wt-thin",
    project_id: str = PROJECT_ID,
    governance_context=None,
):
    root = _make_project_root(tmp_path, project_id)
    launcher = DailyTaskMainLauncher(plan_adapter=None)
    ctx = launcher.prepare(
        worktree_root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=cfg,
        plan_adapter=None,
        origin_task_main_session_ref="20260913_af53_m3w1_thin_session",
        governance_context=governance_context,
    )
    return launcher, root, ctx


def _load_binding(launcher: DailyTaskMainLauncher, ctx: Any):
    env = launcher.build_env(ctx)
    return load_binding_from_envelope(env[PRE_RESOLVED_BINDING_ENV])


def _foreign_handoff(binding: Any) -> Any:
    foreign_root = Path(binding.sandbox.worktree_root) / "_foreign"
    foreign_root.mkdir(parents=True, exist_ok=True)
    foreign_sandbox = WorktreeSandboxBoundary(
        workspace_id="ws-foreign",
        workspace_root=str(foreign_root),
        project_id="foreign_project",
        project_root=str(foreign_root),
        worktree_id="wt-foreign",
        worktree_root=str(foreign_root.resolve()),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )
    return handoff_write(
        mode="work_item",
        semantic={
            "work_role": "coder",
            "task_kind": "foreign",
            "objective": "foreign objective",
            "bounded_scope": "foreign bounded scope",
            "validation_expectations": ["v"],
            "semantic_stop_expectations": ["stop"],
        },
        caller_role="task-main",
        sandbox=foreign_sandbox,
    )


# ---------------------------------------------------------------------------
# A — default selection (M3/W3 cutover: absent selection resolves thin)
# ---------------------------------------------------------------------------


class TestADefaultSelection:
    def test_no_explicit_selection_defaults_to_thin_composition(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        cfg = load_runtime_config(config_path=str(operator_config_file))
        assert cfg.runtime_path == "thin"
        assert select_task_main_runtime_path(cfg) == "thin"
        assert normalize_task_main_runtime_path(None) == "thin"

        launcher, root, ctx = _prepare_thin(tmp_path, operator_config_file)
        assert ctx.runtime_path == "thin"
        assert ctx.live_plan_view is None
        assert ctx.coordinator_store_path is None

        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()
        data = json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert data["runtime_path"] == "thin"
        assert not (root / BOOTSTRAP_RELPATH).exists()

        binding = _load_binding(launcher, ctx)
        assert binding.task_main_runtime_path == "thin"
        assert binding.trusted_task_main_context is None

    def test_selector_authority_is_the_operator_runtime_config(
        self, operator_config_file: Path
    ) -> None:
        cfg = load_runtime_config(config_path=str(operator_config_file))
        assert SUPPORTED_TASK_MAIN_RUNTIME_PATHS == ("legacy", "thin")
        assert select_task_main_runtime_path(cfg) == cfg.runtime_path


# ---------------------------------------------------------------------------
# B — explicit trusted thin selection
# ---------------------------------------------------------------------------


class TestBExplicitTrustedThinSelection:
    def test_launcher_round_trips_explicit_plan_authority_inputs(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path)
        governance_base = tmp_path / "plans"
        (governance_base / PROJECT_ID).mkdir(parents=True)
        plan_id = "plan_af53_m3w1"
        launcher = DailyTaskMainLauncher(plan_adapter=None)

        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-thin-authority",
            runtime_config_path=cfg,
            origin_task_main_session_ref="20260913_af53_m3w1_authority_session",
            plan_id=plan_id,
            governance_base=governance_base,
        )

        payload = json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert payload["plan_id"] == plan_id
        assert payload["governance_base"] == str(governance_base.resolve())
        binding = _load_binding(launcher, ctx)
        assert binding.plan_authority_binding is not None
        assert binding.plan_authority_binding.plan_id == plan_id
        assert binding.plan_authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert binding.authorized_roots is not None
        assert binding.authorized_roots.get("local-governance").root_path == str(
            (governance_base / PROJECT_ID).resolve()
        )

    def test_launcher_without_plan_id_preserves_github_legacy_binding(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=None)

        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-thin-legacy-plan",
            runtime_config_path=cfg,
            origin_task_main_session_ref="20260913_af53_m3w1_legacy_session",
            plan_ref="wzjcccc-dotcom/aota-hermes-tools#57",
        )

        binding = _load_binding(launcher, ctx)
        assert binding.plan_authority_binding is None
        assert binding.plan_binding is not None
        assert binding.plan_binding.plan_ref == "wzjcccc-dotcom/aota-hermes-tools#57"

    def test_launcher_plan_id_without_authority_source_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=None)

        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-thin-missing-authority",
            runtime_config_path=cfg,
            origin_task_main_session_ref="20260913_af53_m3w1_missing_authority_session",
            plan_id="plan_af53_m3w1",
        )

        with pytest.raises(TrustedBindingError) as excinfo:
            _load_binding(launcher, ctx)
        assert "PLAN_AUTHORITY_SOURCE_UNAVAILABLE" in str(excinfo.value)

    def test_launcher_github_and_local_authority_fail_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path)
        governance_base = tmp_path / "plans"
        (governance_base / PROJECT_ID).mkdir(parents=True)
        launcher = DailyTaskMainLauncher(plan_adapter=None)

        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-thin-dual-authority",
            runtime_config_path=cfg,
            origin_task_main_session_ref="20260913_af53_m3w1_dual_authority_session",
            plan_id="plan_af53_m3w1",
            plan_ref="wzjcccc-dotcom/aota-hermes-tools#57",
            governance_base=governance_base,
        )

        with pytest.raises(TrustedBindingError) as excinfo:
            _load_binding(launcher, ctx)
        assert "PLAN_AUTHORITY_SOURCE_AMBIGUOUS" in str(excinfo.value)

    def test_operator_selects_thin_production_candidate(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, root, ctx = _prepare_thin(tmp_path, cfg)

        assert ctx.runtime_path == "thin"
        assert ctx.live_plan_view is None
        assert ctx.next_milestone_view is None
        assert ctx.plan_snapshot is None
        assert ctx.coordinator_store_path is None
        assert not (root / ".aota" / "coordinator.json").exists()
        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()
        assert not (root / BOOTSTRAP_RELPATH).exists()

    def test_thin_bootstrap_carries_no_legacy_workflow_state(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        _launcher, root, ctx = _prepare_thin(tmp_path, cfg)
        data = json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert data["runtime_path"] == "thin"
        assert data["origin_task_main_session_ref"] == "20260913_af53_m3w1_thin_session"
        for forbidden in FORBIDDEN_THIN_BOOTSTRAP_KEYS:
            assert forbidden not in data, forbidden
        source = (REPO_ROOT / "aota_forge" / "composition" / "task_main_runtime_selection.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not (
            (names | attributes)
            & {
                "MilestonePlanView",
                "TaskMainControlService",
                "FileBackedTaskMainCoordinatorStore",
                "advance_once",
            }
        )
        assert not any(
            module.startswith("aota_forge.runtime.task_main") for module in imported_modules
        )

    def test_thin_binding_is_classified_thin_without_legacy_context(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, root, ctx = _prepare_thin(tmp_path, cfg)
        env = launcher.build_env(ctx)
        kind, _payload = verify_envelope(env[PRE_RESOLVED_BINDING_ENV])
        assert kind == "task-main"

        binding = load_binding_from_envelope(env[PRE_RESOLVED_BINDING_ENV])
        assert binding.task_main_runtime_path == "thin"
        assert binding.trusted_task_main_context is None
        assert binding.project_id == PROJECT_ID
        assert binding.sandbox.worktree_id == "wt-thin"
        assert binding.handoff.work_role.value == "task-main"

        for forbidden_op in MODEL_VISIBLE_SELECTOR_OPERATIONS:
            assert forbidden_op not in SUPPORTED_OPERATIONS
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)


# ---------------------------------------------------------------------------
# C — invalid value fails closed
# ---------------------------------------------------------------------------


class TestCInvalidRuntimePathFailsClosed:
    def test_runtime_config_invalid_value_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-bad.json", runtime_path="banana"
        )
        with pytest.raises(RuntimeConfigError, match="runtime_path"):
            load_runtime_config(config_path=str(cfg))

    def test_selector_rejects_unknown_values_without_silent_fallback(self) -> None:
        for bad in ("banana", "Legacy", "THIN", " thin", "thin ", "LEGACY", "v2"):
            with pytest.raises(TaskMainRuntimeSelectionError):
                normalize_task_main_runtime_path(bad)
        with pytest.raises(TaskMainRuntimeSelectionError):
            select_task_main_runtime_path(SimpleNamespace(runtime_path="banana"))

    def test_invalid_selection_creates_no_composition(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-bad.json", runtime_path="banana"
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
                origin_task_main_session_ref="20260913_af53_m3w1_bad",
            )
        assert not (root / BOOTSTRAP_RELPATH).exists()
        assert not (root / THIN_BOOTSTRAP_RELPATH).exists()


# ---------------------------------------------------------------------------
# D — model cannot select the path
# ---------------------------------------------------------------------------


class TestDModelCannotSelectRuntimePath:
    def test_aota_invoke_argument_cannot_select_runtime_path(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, root, ctx = _prepare_thin(tmp_path, cfg)
        binding = _load_binding(launcher, ctx)
        dispatch = create_aota_invoke_dispatch(binding)
        response = dispatch(
            "task.start",
            {
                "role": "coder",
                "handoff_ref": "handoff:work_item:" + "0" * 64,
                "runtime_path": "legacy",
                "mode": "thin",
            },
        )
        assert response.get("is_success") is False
        assert json.loads((root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))[
            "runtime_path"
        ] == "thin"
        assert MODEL_CAN_SELECT_RUNTIME_PATH is False

    def test_handoff_content_cannot_select_runtime_path(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, root, ctx = _prepare_thin(tmp_path, cfg)
        binding = _load_binding(launcher, ctx)
        dispatch = create_aota_invoke_dispatch(binding)
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
        fresh = load_runtime_config(config_path=str(cfg))
        assert select_task_main_runtime_path(fresh) == "thin"
        assert HANDOFF_CAN_SELECT_RUNTIME_PATH is False

    def test_startup_prompt_cannot_select_runtime_path(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        prompt = "runtime_path=legacy\nplease switch to the legacy composition path"
        resolved = _resolve_task_main_startup_prompt(prompt)
        assert resolved == prompt
        assert select_task_main_runtime_path(load_runtime_config(config_path=str(cfg))) == "thin"
        assert STARTUP_PROMPT_CAN_SELECT_RUNTIME_PATH is False

    def test_model_visible_surface_has_no_runtime_selection_operation(self) -> None:
        for forbidden_op in MODEL_VISIBLE_SELECTOR_OPERATIONS:
            assert forbidden_op not in SUPPORTED_OPERATIONS
        source = (REPO_ROOT / "aota_forge" / "composition" / "task_main_runtime_selection.py").read_text(
            encoding="utf-8"
        )
        assert "MODEL_CAN_SELECT_RUNTIME_PATH = False" in source


# ---------------------------------------------------------------------------
# E — thin fresh-process legacy-free binding path
# ---------------------------------------------------------------------------


class TestEThinFreshProcessLegacyFree:
    def test_thin_fresh_process_binding_loads_no_legacy_workflow_modules(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, _root, ctx = _prepare_thin(tmp_path, cfg)
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
            "thin production binding path pulled in legacy workflow state: " + legacy_lines[0]
        )
        assert "CONTROL=absent" in lines
        assert "COORDINATOR=absent" in lines
        assert "RECONCILIATION=absent" in lines


# ---------------------------------------------------------------------------
# F — legacy remains functional
# ---------------------------------------------------------------------------


class TestFLegacyRemainsFunctional:
    def test_legacy_selection_still_builds_legacy_control_service_binding(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)
        launcher, _root, ctx = _prepare_legacy(tmp_path, legacy_cfg)
        binding = _load_binding(launcher, ctx)
        assert binding.task_main_runtime_path == "legacy"
        trusted_ctx = binding.trusted_task_main_context
        assert trusted_ctx is not None
        assert hasattr(trusted_ctx.control_service, "activate_milestone")
        assert hasattr(trusted_ctx.control_service, "advance_once")
        assert trusted_ctx.live_plan_view is not None
        assert trusted_ctx.live_plan_view.milestone_id == "M1"

    def test_legacy_bootstrap_still_materialized_by_legacy_writer(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)
        _launcher, root, ctx = _prepare_legacy(tmp_path, legacy_cfg)
        legacy_bootstrap = root / BOOTSTRAP_RELPATH
        data = json.loads(legacy_bootstrap.read_text(encoding="utf-8"))
        assert data["project_id"] == PROJECT_ID
        assert data["worktree_id"] == "wt-legacy"
        assert "runtime_path" not in data
        assert ctx.runtime_config.runtime_path == "legacy"


# ---------------------------------------------------------------------------
# G — same canonical agent surface
# ---------------------------------------------------------------------------


class TestGSameCanonicalAgentSurface:
    def test_both_paths_expose_exactly_one_aota_invoke_tool(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)
        legacy_launcher, _root, legacy_ctx = _prepare_legacy(tmp_path, legacy_cfg)
        legacy_binding = _load_binding(legacy_launcher, legacy_ctx)

        thin_cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        thin_launcher, _thin_root, thin_ctx = _prepare_thin(tmp_path, thin_cfg)
        thin_binding = _load_binding(thin_launcher, thin_ctx)

        for binding in (legacy_binding, thin_binding):
            server = create_shared_mcp_server(binding)
            names = [tool.name for tool in server._tool_manager.list_tools()]
            assert names == ["aota.invoke"]
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1


# ---------------------------------------------------------------------------
# G2 — side-by-side isolation (same worktree, no migration/deletion)
# ---------------------------------------------------------------------------


class TestSideBySideIsolation:
    def test_selecting_thin_does_not_mutate_legacy_state_and_vice_versa(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=_plan_adapter())
        legacy_origin = "20260913_af53_m3w1_legacy_session"
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)

        legacy_ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-side-by-side",
            runtime_config_path=legacy_cfg,
            plan_adapter=_plan_adapter(),
            origin_task_main_session_ref=legacy_origin,
        )
        legacy_bootstrap = root / BOOTSTRAP_RELPATH
        coordinator_file = root / ".aota" / "coordinator.json"
        legacy_bytes = legacy_bootstrap.read_bytes()
        coordinator_bytes = coordinator_file.read_bytes()

        thin_cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        thin_ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-side-by-side",
            runtime_config_path=thin_cfg,
            plan_adapter=None,
            origin_task_main_session_ref="20260913_af53_m3w1_thin_session",
        )
        assert thin_ctx.runtime_path == "thin"
        # Selecting thin does not mutate or delete legacy state.
        assert legacy_bootstrap.read_bytes() == legacy_bytes
        assert coordinator_file.read_bytes() == coordinator_bytes
        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()

        # Selecting legacy again neither deletes thin implementation nor
        # changes its classification.
        legacy_again = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-side-by-side",
            runtime_config_path=legacy_cfg,
            plan_adapter=_plan_adapter(),
            origin_task_main_session_ref=legacy_origin,
        )
        assert legacy_again.runtime_path == "legacy"
        assert (root / THIN_BOOTSTRAP_RELPATH).is_file()
        legacy_binding = _load_binding(launcher, legacy_again)
        assert legacy_binding.trusted_task_main_context is not None
        thin_binding = _load_binding(launcher, thin_ctx)
        assert thin_binding.task_main_runtime_path == "thin"
        assert thin_binding.trusted_task_main_context is None
        assert thin_ctx.runtime_config_path == thin_cfg

    def test_no_repository_migration_or_deletion_is_performed(self) -> None:
        frozen = (
            "aota_forge/runtime/task_main/runner.py",
            "aota_forge/runtime/task_main/coordinator.py",
            "aota_forge/runtime/task_main/coordinator_state.py",
            "aota_forge/runtime/task_main/coordinator_store.py",
            "aota_forge/runtime/task_main/reconciliation.py",
            "aota_forge/runtime/task_main/control.py",
        )
        for rel in frozen:
            assert (REPO_ROOT / rel).is_file(), rel


# ---------------------------------------------------------------------------
# H — trusted project/worktree identity
# ---------------------------------------------------------------------------


class TestHHardProjectBoundary:
    def test_thin_foreign_project_handoff_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, _root, ctx = _prepare_thin(tmp_path, cfg)
        binding = _load_binding(launcher, ctx)
        foreign = _foreign_handoff(binding)
        dispatch = create_aota_invoke_dispatch(binding)
        response = dispatch("task.start", {"role": "coder", "handoff_ref": foreign.ref})
        assert response.get("is_success") is False

    def test_legacy_foreign_project_handoff_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)
        launcher, _root, ctx = _prepare_legacy(tmp_path, legacy_cfg)
        binding = _load_binding(launcher, ctx)
        foreign = _foreign_handoff(binding)
        dispatch = create_aota_invoke_dispatch(binding)
        response = dispatch("task.start", {"role": "coder", "handoff_ref": foreign.ref})
        assert response.get("is_success") is False

    def test_thin_bootstrap_project_must_match_worktree_evidence(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path, PROJECT_ID)
        bootstrap = materialize_thin_task_main_bootstrap(
            worktree_root=root,
            project_id="foreign_project",
            worktree_id="wt-foreign",
            runtime_config_path=cfg,
            origin_task_main_session_ref="20260913_af53_m3w1_foreign",
        )
        envelope = create_task_main_envelope(worktree_root=root, bootstrap_path=bootstrap)
        with pytest.raises(TrustedBindingError):
            load_binding_from_envelope(envelope)
        assert not (root / BOOTSTRAP_RELPATH).exists()

    def test_thin_bootstrap_round_trips_trusted_governance_context(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        context = {
            "CONTEXT_ROUTE": {"is_authority": False},
            "CARDS_PRESENT": False,
            "IS_AUTHORITY": False,
            "GOVERNANCE_CONTEXT_IS_AUTHORITY": False,
        }
        launcher, root, ctx = _prepare_thin(
            tmp_path, cfg, governance_context=context
        )
        bootstrap = json.loads(
            (root / THIN_BOOTSTRAP_RELPATH).read_text(encoding="utf-8")
        )
        assert bootstrap["governance_context"] == context

        binding = _load_binding(launcher, ctx)
        assert binding.governance_context == context
        dispatch = create_aota_invoke_dispatch(binding)
        response = dispatch("role.bootstrap", {})
        assert response["is_success"] is True, response
        if response["payload"] is not None:
            payload = response["payload"]
        else:
            hydrated = dispatch("result.hydrate", dict(response["output_ref"]))
            assert hydrated["is_success"] is True, hydrated
            payload = json.loads(hydrated["payload"]["content"])
        assert payload["GOVERNANCE_CONTEXT"] == context


# ---------------------------------------------------------------------------
# I — RuntimeConfig reuse
# ---------------------------------------------------------------------------


class TestIRuntimeConfigReuse:
    def test_both_paths_use_the_shared_operator_runtime_config(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        legacy_cfg = _write_legacy_runtime_config(tmp_path, test_hermes_executable)
        legacy_launcher, _root, legacy_ctx = _prepare_legacy(tmp_path, legacy_cfg)
        assert isinstance(legacy_ctx.runtime_config, RuntimeConfig)
        assert legacy_ctx.runtime_config.to_dict() == load_runtime_config(
            config_path=str(legacy_cfg)
        ).to_dict()
        legacy_binding = _load_binding(legacy_launcher, legacy_ctx)
        assert legacy_binding.task_main_runtime_path == "legacy"

        thin_cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        thin_launcher, _thin_root, thin_ctx = _prepare_thin(tmp_path, thin_cfg)
        assert isinstance(thin_ctx.runtime_config, RuntimeConfig)
        assert thin_ctx.runtime_config.to_dict() == load_runtime_config(
            config_path=str(thin_cfg)
        ).to_dict()
        assert thin_ctx.runtime_config.runtime_path == "thin"
        thin_binding = _load_binding(thin_launcher, thin_ctx)
        assert thin_binding.handoff.work_role.value == "task-main"
        # The envelope/binding construction consumed the same operator config
        # file; there is no second config authority on either path.
        assert load_runtime_config(config_path=str(thin_cfg)).runtime_path == "thin"

    def test_no_second_config_authority_is_created(self) -> None:
        for rel in (
            "aota_forge/composition/task_main_runtime_selection.py",
            "aota_forge/composition/thin_task_main_host.py",
        ):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for forbidden in ("SecondRuntimeConfig", "ThinRuntimeConfig", "WorkflowModeConfig"):
                assert forbidden not in source, (rel, forbidden)
        selector = (
            REPO_ROOT / "aota_forge" / "composition" / "task_main_runtime_selection.py"
        ).read_text(encoding="utf-8")
        assert "from aota_forge.runtime.config import" in selector


# ---------------------------------------------------------------------------
# J — thin production-candidate preflight (real launcher seam)
# ---------------------------------------------------------------------------


class TestJThinProductionPreflight:
    def test_thin_selection_reaches_real_mcp_child_launch_ready_composition(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        launcher, _root, ctx = _prepare_thin(tmp_path, cfg)
        assert ctx.runtime_path == "thin"
        env = launcher.build_env(ctx)
        probe = probe_production_af_mcp_surface(env=env)
        assert probe.ok, probe.detail
        assert "aota.invoke" in probe.tool_names

    def test_thin_launch_binds_exact_session_without_coordinator_or_legacy_bootstrap(
        self, tmp_path: Path, test_hermes_executable: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, name="runtime-thin.json", runtime_path="thin"
        )
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=None)

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=True, timeout=None, env=None, **kwargs):
            subprocess_calls.append(list(cmd))
            usage_path = Path(cmd[cmd.index("--usage-file") + 1])
            usage_path.write_text(
                json.dumps({"session_id": REAL_SESSION, "completed": True}), encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="session ready", stderr="")

        monkeypatch.setattr(launcher_mod.subprocess, "run", fake_run)

        reentry_calls: list[dict[str, Any]] = []

        class RecordingReentry:
            def reenter(self, session_id, payload):
                reentry_calls.append({"session_id": session_id, "payload": payload})
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt="continued",
                    stderr_excerpt=None,
                )

        monkeypatch.setattr(
            launcher,
            "_build_exact_session_reentry",
            lambda *, ctx, timeout_seconds: RecordingReentry(),
        )
        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: PersistedSessionToolSurface(False, (), None),
        )

        ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-thin-launch",
            runtime_config_path=cfg,
            plan_adapter=None,
            initial_prompt="M3/W1 THIN PHASE2 OPERATOR KICKOFF",
            timeout_seconds=30,
        )

        assert session_id == REAL_SESSION
        assert len(subprocess_calls) == 1, "phase 1 must be the only session creation call"
        assert PHASE1_SESSION_BOOTSTRAP_PROMPT in subprocess_calls[0]
        assert "M3/W1 THIN PHASE2 OPERATOR KICKOFF" not in subprocess_calls[0]
        assert len(reentry_calls) == 1
        assert reentry_calls[0]["session_id"] == REAL_SESSION
        assert reentry_calls[0]["payload"] == "M3/W1 THIN PHASE2 OPERATOR KICKOFF"

        assert ctx.runtime_path == "thin"
        assert ctx.live_plan_view is None
        assert ctx.coordinator_store_path is None
        thin_bootstrap = root / THIN_BOOTSTRAP_RELPATH
        data = json.loads(thin_bootstrap.read_text(encoding="utf-8"))
        assert data["origin_task_main_session_ref"] == REAL_SESSION
        assert data["runtime_path"] == "thin"
        assert not (root / BOOTSTRAP_RELPATH).exists()
        assert not (root / ".aota" / "coordinator.json").exists()

        post_launch = load_binding_from_envelope(
            launcher.build_env(ctx)[PRE_RESOLVED_BINDING_ENV]
        )
        assert post_launch.task_main_runtime_path == "thin"
        assert post_launch.trusted_task_main_context is None


# ---------------------------------------------------------------------------
# K — M3/W3 cutover: production default is now thin
# ---------------------------------------------------------------------------


class TestKDefaultCutover:
    def test_production_default_cutover_is_yes(self, operator_config_file: Path) -> None:
        assert DEFAULT_TASK_MAIN_RUNTIME_PATH == "thin"
        assert THIN_PATH_PRODUCTION_DEFAULT is True
        assert M3_W1_THIN_IS_DEFAULT is False
        assert M3_W3_THIN_IS_DEFAULT is True
        assert PRODUCTION_DEFAULT_CUTOVER is True
        cfg = load_runtime_config(config_path=str(operator_config_file))
        assert select_task_main_runtime_path(cfg) == "thin"
        assert normalize_task_main_runtime_path(None) == "thin"

    def test_thin_default_launch_requires_no_plan_adapter(
        self, tmp_path: Path, operator_config_file: Path
    ) -> None:
        root = _make_project_root(tmp_path)
        launcher = DailyTaskMainLauncher(plan_adapter=None)
        ctx = launcher.prepare(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-k",
            runtime_config_path=operator_config_file,
            plan_adapter=None,
            origin_task_main_session_ref="20260913_af53_m3w1_k",
        )
        assert ctx.runtime_path == "thin"
        assert not (root / BOOTSTRAP_RELPATH).exists()
