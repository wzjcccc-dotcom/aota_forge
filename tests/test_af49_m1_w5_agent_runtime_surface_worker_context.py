"""AF #49 M1/W5 — Agent runtime surface & Worker context consumption repair.

Repairs:
  I49-B001: canonical normal-path handoff/task operations absent from the
            production MCP exposure catalog, role surfaces and normal guidance.
  I49-B003: role.bootstrap over the inline bound returned by_ref with no
            reachable deterministic model-visible hydration path.

Target production shape (exposure is not authority):

    canonical operation exists
      -> role surface allows visibility
      -> trusted runtime binding accepts surface (subset of the catalog)
      -> real MCP transport exposes it through the single aota.invoke tool
      -> model receives usable guidance
      -> server-side trusted authority still enforces permission

and:

    role.bootstrap
      -> inline OR governed by_ref result
      -> by_ref model-visible representation carries canonical ref claims
         + deterministic result.hydrate instruction
      -> result.hydrate
      -> exact original bootstrap payload

Proof boundary (honest):
  PROVES=deterministic component integration across the real MCP transport
         adapter seam (create_shared_mcp_server -> aota.invoke -> canonical
         ingress), the real role surfaces/bindings, the governed by_ref
         round-trip, and the fail-closed negative matrix.
  DOES_NOT_PROVE=real Hermes task-main/Worker behavior, real workspace effect,
         real task.return/completion, or M1 V3 rerun (post-W6).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG,
    EXPOSURE_IS_NOT_AUTHORITY,
    HANDOFF_TASK_OPERATIONS,
    HERMES_AGENT_FACING_AOTA_TOOL_COUNT,
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
    SUPPORTED_OPERATIONS,
    create_shared_mcp_server,
)
from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.role_bootstrap import (
    ALL_ROLE_BOOTSTRAPS_CONSUMABLE_INLINE_OR_BY_REF,
    ALL_ROLE_BOOTSTRAPS_INLINE_WITHIN_BOUND,
    MODEL_CAN_DETERMINISTICALLY_HYDRATE_BY_REF,
    handle_role_bootstrap,
)
from aota_forge.work_plane.task_main_descriptors import (
    SUBMIT_WORK_PROJECTION_AGENT_NORMAL_PATH,
    SUBMIT_WORK_PROJECTION_INTERNAL_COMPATIBILITY_ONLY,
    SUBMIT_WORK_PROJECTION_REMOVED,
    build_task_main_operation_guidance,
)
from aota_forge.work_plane.tool_result_governance import TOOL_INLINE_OUTPUT_MAX_BYTES
from aota_forge.work_plane.tool_surface import create_role_tool_surface

PROJECT_ID = "proj_af49w5"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
ENTRY_BASE = "a" * 40
MARKER_W1 = "AF49W5_W1_AUTHORITATIVE_SOURCE_MARKER"
MARKER_W2 = "AF49W5_W2_AUTHORITATIVE_SOURCE_MARKER"

TASK_MAIN_NORMAL_OPS = ("handoff.write", "handoff.open", "task.start")
WORKER_LIFECYCLE_OPS = ("handoff.open", "handoff.write", "task.return")


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W5 fixture

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

#### M1/W2 — Authoritative beta slice
Implement {MARKER_W2} bounded behavior exactly as the Plan describes.
"""


def _live():
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af49w5")
    live, _next = project_milestone_views(
        doc, plan_authority=PLAN_AUTH, plan_digest="d" * 64, plan_source_revision="rev-af49w5"
    )
    return live


def _worker_handoff(
    role: str = "coder",
    *,
    objective: str | None = None,
    bounded_scope: str | None = None,
) -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="af49w5-worker",
        objective=objective or "bounded implementation task",
        bounded_scope=bounded_scope or "bounded scope from trusted handoff",
        validation_expectations=("focused validation",),
        semantic_stop_expectations=("stop if unclear",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M1"),
    )


def _large_worker_binding(tmp_path: Path, role: str = "coder"):
    """Worker binding whose bootstrap payload exceeds the inline bound."""
    root = tmp_path / f"wt_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    handoff = _worker_handoff(
        role,
        objective="Deliver the bounded artifact. " + ("OBJECTIVE-DETAIL " * 220),
        bounded_scope="bounded scope " + ("SCOPE-DETAIL " * 220),
    )
    return build_worker_binding(
        root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-w5",
        canonical_task_id="task-w5-worker",
        handoff=handoff,
    )


def _project_manifest() -> str:
    return (
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n"
    )


def _make_task_main_binding(tmp_path: Path, view):
    """Real trusted task-main binding built through the production bootstrap."""
    root = tmp_path / f"wt_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
    coord_path = root / ".aota" / "coord.json"
    exec_path = root / ".aota" / "exec.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{uuid.uuid4().hex[:6]}.json"
    cfg_path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/false",
                "concurrency": 2,
                "provider": "opencode-go",
                "model": "m",
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
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-1",
        coordinator_store_path=coord_path,
        execution_store_path=exec_path,
        runtime_config_path=cfg_path,
        origin_task_main_session_ref="sess-af49w5",
        live_plan_view=view,
        next_milestone_view=None,
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
    return binding, root, exec_path


class _RecordingReferenceAdapter(ReferenceFakeExecutorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.packages: list = []

    def dispatch(self, package):
        self.packages.append(package)
        return super().dispatch(package)


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


def _call(server, operation: str, arguments: dict):
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "aota.invoke")
    res = tool.fn(operation=operation, arguments=arguments)
    structured = res.structuredContent if hasattr(res, "structuredContent") else None
    text = res.content[0].text if getattr(res, "content", None) else None
    return structured, text


def _bootstrap_payload(server):
    """Full bootstrap payload via inline projection or governed hydration."""
    structured, text = _call(server, "role.bootstrap", {})
    assert structured["ok"] is True, structured
    if structured["output_mode"] == "inline":
        return structured["payload"], structured, text
    hydration = structured["hydration"]
    assert hydration["operation"] == "result.hydrate"
    hydrated, _ = _call(server, "result.hydrate", dict(hydration["arguments"]))
    assert hydrated["ok"] is True, hydrated
    return json.loads(hydrated["payload"]["content"]), structured, text


# ---------------------------------------------------------------------------
# 1-3. Exposure catalog and role surfaces through the real MCP transport
# ---------------------------------------------------------------------------


class TestExposureCatalog:
    def test_transport_catalog_includes_normal_path_ops(self) -> None:
        assert set(TASK_MAIN_NORMAL_OPS).issubset(SUPPORTED_OPERATIONS)
        assert set(WORKER_LIFECYCLE_OPS).issubset(SUPPORTED_OPERATIONS)
        assert SUPPORTED_OPERATIONS == CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG
        assert EXPOSURE_IS_NOT_AUTHORITY is True
        assert set(HANDOFF_TASK_OPERATIONS) == {"handoff.write", "handoff.open", "task.start", "task.return"}

    def test_task_main_surface_contains_normal_path_ops(self) -> None:
        surface = get_tool_surface_for_role("task-main")
        for op in TASK_MAIN_NORMAL_OPS + ("result.hydrate",):
            assert op in surface.all_capability_names(), op
        assert "workspace.write" not in surface.all_capability_names()
        assert "test.run" not in surface.all_capability_names()

    def test_worker_surfaces_contain_lifecycle_ops(self) -> None:
        for role in ("coder", "analyst", "reviewer", "project-steward"):
            surface = get_tool_surface_for_role(role)
            for op in WORKER_LIFECYCLE_OPS + ("workspace.read", "workspace.search"):
                assert op in surface.all_capability_names(), (role, op)
        coder = get_tool_surface_for_role("coder")
        assert {"workspace.read", "workspace.search", "workspace.write"} <= set(coder.all_capability_names())
        # task-main progression ops are never exposed to Worker roles.
        for role in ("coder", "analyst", "reviewer", "project-steward"):
            surface = get_tool_surface_for_role(role)
            assert not any(op.startswith("task_main.") for op in surface.all_capability_names()), role

    def test_role_surfaces_subset_of_canonical_catalog(self) -> None:
        for role in ("task-main", "coder", "analyst", "reviewer", "project-steward"):
            surface = get_tool_surface_for_role(role)
            assert set(surface.all_capability_names()).issubset(CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG), role

    def test_transport_single_entry_preserved(self) -> None:
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1
        assert HERMES_AGENT_FACING_AOTA_TOOL_COUNT == 1
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"

    def test_task_main_normal_ops_usable_through_real_mcp(self, tmp_path: Path) -> None:
        binding, _root, exec_path = _make_task_main_binding(tmp_path, _live())
        recording = _RecordingReferenceAdapter()
        registry = ExecutorRegistry()
        registry.register(recording)
        dispatcher = ExecutionDispatcher(registry, state_store=FileBackedExecutionStateStore(exec_path))
        bind_execution_dispatcher(dispatcher)
        server = create_shared_mcp_server(binding)
        written, _ = _call(
            server,
            "handoff.write",
            {
                "mode": "work_item",
                "payload": {
                    "objective": "Implement the bounded W1 behavior",
                    "bounded_scope": "bounded scope from authoritative source",
                    "validation_expectations": ["focused validation"],
                    "semantic_stop_expectations": ["stop if unclear"],
                },
            },
        )
        assert written["ok"] is True, written
        assert written["output_mode"] == "inline"
        ref = written["payload"]["ref"]
        started, _ = _call(server, "task.start", {"role": "coder", "handoff_ref": ref})
        assert started["ok"] is True, started
        assert started["payload"]["status"] == "ACCEPTED"
        assert len(recording.packages) == 1
        # handoff.open is exposed and readable through the same transport.
        opened, _ = _call(server, "handoff.open", {"ref": ref, "view": "full"})
        assert opened["ok"] is True, opened
        assert opened["payload"]["envelope"]["work_item_id"] == "W1"

    def test_worker_cannot_use_task_main_or_wrong_mode_ops(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        denied, _ = _call(server, "task.start", {"role": "coder", "handoff_ref": "handoff:x:y"})
        assert denied["ok"] is False and denied["error"]["code"] == "AUTHORITY_DENIED"
        denied2, _ = _call(
            server,
            "handoff.write",
            {"mode": "work_item", "payload": {"objective": "forged"}},
        )
        assert denied2["ok"] is False and denied2["error"]["code"] == "AUTHORITY_DENIED"

    def test_task_main_cannot_return_or_write_result_handoff(self, tmp_path: Path) -> None:
        binding, _root, _exec_path = _make_task_main_binding(tmp_path, _live())
        server = create_shared_mcp_server(binding)
        denied, _ = _call(server, "task.return", {"status": "completed", "result_ref": "handoff:x:y"})
        assert denied["ok"] is False and denied["error"]["code"] == "AUTHORITY_DENIED"
        denied2, _ = _call(
            server,
            "handoff.write",
            {"mode": "result", "payload": {"summary": "not allowed for task-main"}},
        )
        assert denied2["ok"] is False and denied2["error"]["code"] == "AUTHORITY_DENIED"

    def test_worker_task_return_role_path_retained(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        result, _ = _call(server, "task.return", {"status": "completed", "result_ref": "handoff:x:y"})
        # Role/mode authority accepts a one-shot Worker; the failure is about
        # the (deliberately invalid) result ref, not role denial.
        assert result["ok"] is False
        assert result["error"]["code"] not in ("AUTHORITY_DENIED", "WRONG_ROLE")

    def test_unknown_operation_fails_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        result, _ = _call(server, "handoff.nope", {})
        assert result["ok"] is False and result["error"]["code"] == "UNKNOWN_OPERATION"

    def test_binding_rejects_surface_operation_absent_from_catalog(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        surface = create_role_tool_surface("coder", eager=["not.a.canonical.operation"])
        with pytest.raises(TrustedBindingError):
            dataclasses.replace(binding, tool_surface=surface)


# ---------------------------------------------------------------------------
# 4-5. Normal-path guidance (submit_work_projection is compatibility only)
# ---------------------------------------------------------------------------


class TestNormalPathGuidance:
    def test_submit_work_projection_is_not_normal_path(self) -> None:
        assert SUBMIT_WORK_PROJECTION_REMOVED is False
        assert SUBMIT_WORK_PROJECTION_INTERNAL_COMPATIBILITY_ONLY is True
        assert SUBMIT_WORK_PROJECTION_AGENT_NORMAL_PATH is False
        guidance = build_task_main_operation_guidance()
        submit = guidance["task_main.submit_work_projection"]
        assert submit["compatibility_only"] is True
        assert submit["agent_normal_path"] is False
        assert "compatibility" in submit["note"]
        assert "normal_path" in guidance

    def test_normal_guidance_names_handoff_write_and_task_start(self) -> None:
        guidance = build_task_main_operation_guidance()
        flow = guidance["normal_path"]["flow"]
        assert "handoff.write" in flow and "task.start" in flow
        assert "handoff.write" in guidance and "task.start" in guidance

    def test_task_main_bootstrap_guidance_teaches_normal_path(self, tmp_path: Path) -> None:
        binding, _root, _exec_path = _make_task_main_binding(tmp_path, _live())
        payload = handle_role_bootstrap(binding, {})
        guidance = payload["OPERATION_GUIDANCE"]
        assert guidance["normal_path"]["steps"] == ["handoff.write", "task.start"]
        assert guidance["task_main.submit_work_projection"]["agent_normal_path"] is False
        # Eager curated guidance also teaches the normal path, not submit-first.
        eager = " ".join(e["materialized"] for e in payload["BASE_SKILLS"])
        assert "handoff.write" in eager and "task.start" in eager
        assert "submit_work_projection=compatibility only" in eager

    def test_skill_markdown_teaches_normal_path_not_submit_first(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "skills" / "aota-task-main-control" / "SKILL.md").read_text(encoding="utf-8")
        assert 'operation="handoff.write"' in text
        assert 'operation="task.start"' in text
        assert "submit_work_projection` is internal compatibility only" in text


# ---------------------------------------------------------------------------
# 6-9. I49-B003: forced by_ref bootstrap hydration from model-visible claims
# ---------------------------------------------------------------------------


class TestByRefBootstrapConsumption:
    def test_forced_by_ref_bootstrap_returns_usable_hydration_claims(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        structured, text = _call(server, "role.bootstrap", {})
        assert structured["ok"] is True
        assert structured["output_mode"] == "by_ref"
        # The model-visible representation itself carries the full claims.
        visible = json.loads(text)
        assert set(visible["output_ref"]) == {"ref", "digest", "project_id", "worktree_id", "byte_length"}
        hydration = visible["hydration"]
        assert hydration["operation"] == "result.hydrate"
        claims = hydration["arguments"]
        assert set(claims) == {"ref", "digest", "project_id", "worktree_id", "byte_length"}
        assert claims["ref"] == visible["output_ref"]["ref"]
        assert claims["digest"] == visible["output_ref"]["digest"]
        assert claims["byte_length"] == visible["output_ref"]["byte_length"]
        assert claims["byte_length"] == structured["output_byte_length"] > TOOL_INLINE_OUTPUT_MAX_BYTES

    def test_exact_round_trip_from_model_visible_claims_only(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        original = handle_role_bootstrap(binding, {})
        structured, text = _call(server, "role.bootstrap", {})
        assert structured["output_mode"] == "by_ref"
        # Hydrate using ONLY the claims parsed from the model-visible text.
        claims = json.loads(text)["hydration"]["arguments"]
        hydrated, _ = _call(server, "result.hydrate", dict(claims))
        assert hydrated["ok"] is True, hydrated
        payload = json.loads(hydrated["payload"]["content"])
        assert payload == original
        canonical = json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == claims["digest"]
        assert len(canonical) == claims["byte_length"]

    def test_hydrated_worker_bootstrap_exposes_canonical_workspace_write_guidance(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        payload, _structured, _text = _bootstrap_payload(server)
        eager = " ".join(e["materialized"] for e in payload["BASE_SKILLS"])
        assert "create_only|replace_existing|create_or_replace" in eager
        # Worker lifecycle ops usable via the same bootstrap guidance.
        assert "handoff.open" in eager and "handoff.write" in eager and "task.return" in eager
        tool_ops = {ref["capability_name"] for ref in payload["TOOL_SURFACE"]["eager"]}
        assert {"workspace.write", "handoff.open", "handoff.write", "task.return"}.issubset(tool_ops)
        assert MODEL_CAN_DETERMINISTICALLY_HYDRATE_BY_REF is True

    def test_digest_mismatch_fails_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        claims = json.loads(_call(server, "role.bootstrap", {})[1])["hydration"]["arguments"]
        tampered = dict(claims, digest="f" * 64)
        result, _ = _call(server, "result.hydrate", tampered)
        assert result["ok"] is False

    def test_project_mismatch_fails_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        claims = json.loads(_call(server, "role.bootstrap", {})[1])["hydration"]["arguments"]
        tampered = dict(claims, project_id="foreign-project")
        result, _ = _call(server, "result.hydrate", tampered)
        assert result["ok"] is False
        assert result["error"]["code"] == "CROSS_SCOPE_DENIED"

    def test_worktree_mismatch_fails_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        claims = json.loads(_call(server, "role.bootstrap", {})[1])["hydration"]["arguments"]
        tampered = dict(claims, worktree_id="foreign-worktree")
        result, _ = _call(server, "result.hydrate", tampered)
        assert result["ok"] is False
        assert result["error"]["code"] == "CROSS_SCOPE_DENIED"

    def test_unknown_ref_fails_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        claims = json.loads(_call(server, "role.bootstrap", {})[1])["hydration"]["arguments"]
        unknown = dict(claims, ref="tool_output:role.bootstrap:0000000000000000", digest="a" * 64)
        result, _ = _call(server, "result.hydrate", unknown)
        assert result["ok"] is False

    def test_tampered_by_ref_envelope_claims_fail_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        structured, text = _call(server, "role.bootstrap", {})
        summary = json.loads(text)
        # Model tampers with the visible claim set (ref claims another payload).
        summary["hydration"]["arguments"]["ref"] = "tool_output:role.bootstrap:deadbeefdeadbeef"
        result, _ = _call(server, "result.hydrate", summary["hydration"]["arguments"])
        assert result["ok"] is False

    def test_model_invented_hydration_claims_fail_closed(self, tmp_path: Path) -> None:
        binding = _large_worker_binding(tmp_path, "coder")
        server = create_shared_mcp_server(binding)
        invented = {
            "ref": "tool_output:role.bootstrap:invented",
            "digest": "b" * 64,
            "project_id": PROJECT_ID,
            "worktree_id": "wt-w5",
            "byte_length": 123,
        }
        result, _ = _call(server, "result.hydrate", invented)
        assert result["ok"] is False

    def test_inline_bound_not_increased_as_workaround(self) -> None:
        assert TOOL_INLINE_OUTPUT_MAX_BYTES == 4096
        from aota_forge.work_plane import tool_result_governance as trg

        assert trg.TOOL_INLINE_OUTPUT_MAX_BYTES == 4096
        assert ALL_ROLE_BOOTSTRAPS_INLINE_WITHIN_BOUND is False
        assert ALL_ROLE_BOOTSTRAPS_CONSUMABLE_INLINE_OR_BY_REF is True
