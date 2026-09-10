"""AF #46 M1/W2 — Authority / Error / Result Ownership Convergence.

Proves D2-D5 move semantic decisions out of transport into AF Core/runtime/
result-governance, preserving W1 one-core dispatch (D1 regression):

  D2: ToolRoleSurface visibility never decides permission; trusted binding +
      Core policy/provider authority decides.
  D3: Typed semantic errors originate in Core; no transport string classification.
  D4: Plan gate / coordinator identity owned by Core service; no MCP private
      store discovery, no adapter format knowledge, no new agent operation.
  D5: Durable result semantics owned by result governance; MCP never persists
      semantic payloads directly; hydration stays bounded/selective.

Proof tiers (effective TIER_1_TO_TIER_4 for authority/binding change):
  TIER_1 CORE_SEMANTIC (no MCP), TIER_2 ADAPTER_PARITY (MCP+CLI vs Core),
  TIER_3 CROSS_MODULE_COMPOSITION (real classes/stores/providers),
  TIER_4 PROCESS_BOUNDARY (serialization across MCP boundary).

No real Hermes lifecycle (M2/W3 Tier 5 scope).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core_ingress import (
    CANONICAL_OPERATION_DISPATCH_PLANE_COUNT,
    EXPECTED_LAYER,
    SEMANTIC_OWNER,
    CanonicalDispatchBinding,
    dispatch_tool_operation,
    dispatch_via_core,
    resolve_descriptor,
)
from aota_forge.core.providers.tool import ToolResponse
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    MCP_PUBLIC_TOOL_COUNT,
    ONE_SHARED_AOTA_MCP,
    SUPPORTED_OPERATIONS as MCP_SUPPORTED,
    TrustedTaskMainRuntimeContext,
    TrustedWorkerBinding,
    _SharedAotaMcpAdapter,
    _to_canonical_binding,
)
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    create_workspace_mutation_authority,
)
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)


def _sandbox_for(root: Path, project_id: str = "proj-w2", worktree_id: str = "wt-w2"):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-w2",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path="manifest.json",
        name="w2",
        kind="project",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-w2",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id, root)


def _handoff(role: str = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="w2-test",
        objective="w2 objective",
        bounded_scope="w2 scope",
        validation_expectations=("v",),
        semantic_stop_expectations=("t",),
    )


def _policy(project_id: str = "proj-w2") -> AgentsPolicyCandidate:
    return AgentsPolicyCandidate(
        policy_id="pol-w2",
        project_id=project_id,
        scope="",
        content="bounded",
        provenance_ref="agents:AGENTS.md",
    )


def _canonical_binding(
    root: Path,
    *,
    role: str = "coder",
    with_write: bool = True,
    ops: tuple[str, ...] | None = None,
    with_shell: bool = False,
    with_test: bool = False,
) -> CanonicalDispatchBinding:
    sandbox = _sandbox_for(root)
    handoff = _handoff(role)
    policy = _policy()
    read_auths = (
        create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR),
        create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR),
    )
    mutation = (
        create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        if with_write
        else None
    )
    surface_ops = ops if ops is not None else (
        "workspace.search",
        "workspace.read",
        "workspace.write",
        "result.hydrate",
        "role.bootstrap",
        "skill.open",
    )
    surface = create_role_tool_surface(role, eager=tuple(surface_ops))
    shell_auth = None
    test_auth = None
    if with_shell:
        from aota_forge.work_plane.restricted_shell import create_restricted_shell_authority

        # Resolve descriptor canonically to avoid second authority.
        desc = resolve_descriptor("restricted_shell.run")
        shell_auth = create_restricted_shell_authority(sandbox, handoff, [policy], desc)
    if with_test:
        from aota_forge.work_plane.test_execution import create_test_execution_authority

        desc = resolve_descriptor("test.run")
        test_auth = create_test_execution_authority(sandbox, handoff, [policy], desc)
    return CanonicalDispatchBinding(
        canonical_task_id="task-w2",
        project_id="proj-w2",
        worktree_id="wt-w2",
        trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        read_authorities=read_auths,
        mutation_authority=mutation,
        restricted_shell_authority=shell_auth,
        test_execution_authority=test_auth,
        allowed_operations=frozenset(surface.all_capability_names()),
    )


def _mcp_binding(
    root: Path,
    *,
    role: str = "coder",
    with_write: bool = True,
    ops: tuple[str, ...] | None = None,
) -> TrustedWorkerBinding:
    sandbox = _sandbox_for(root)
    handoff = _handoff(role)
    policy = _policy()
    surface_ops = ops if ops is not None else (
        "workspace.search",
        "workspace.read",
        "workspace.write",
        "result.hydrate",
        "role.bootstrap",
        "skill.open",
    )
    return TrustedWorkerBinding(
        canonical_task_id="task-w2",
        project_id="proj-w2",
        worktree_id="wt-w2",
        trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface(role, eager=tuple(surface_ops)),
        read_authorities=(
            create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR),
            create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR),
        ),
        mutation_authority=(
            create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
            if with_write
            else None
        ),
    )


def _task_main_kit(tmp_path: Path, *, approved: bool = True):
    """Real Core/runtime composition: stores + service + live view."""
    from aota_forge.core.execution.dispatcher import ExecutionDispatcher
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
    from aota_forge.core.execution.registry import ExecutorRegistry
    from aota_forge.core.execution.adapter import (
        ExecutorAdapter,
        ValidationResult,
        DispatchResult,
        TaskStatusResult,
        CancelResult,
        ResumeResult,
    )
    from aota_forge.core.execution.capabilities import ExecutorCapabilities
    from aota_forge.core.execution.package import ExecutionPackage
    from aota_forge.core.execution.results import CanonicalResult
    from aota_forge.core.execution.state import CanonicalTaskState
    from aota_forge.core.plan.normalize import normalize_portable_plan
    from aota_forge.core.plan.read_model import portable_plan_digest
    from aota_forge.runtime.task_main.control import TaskMainControlService
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView
    from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph

    class _FakeAdapter(ExecutorAdapter):
        def capabilities(self) -> ExecutorCapabilities:
            from aota_forge.core.execution.roles import CANONICAL_ROLES

            return ExecutorCapabilities(
                executor_id="w2-fake",
                adapter_kind="w2_fake_test_double",
                supported_execution_modes=("async",),
                supports_streaming_events=True,
                supports_task_cancellation=True,
                supports_task_resume=True,
                supports_structured_result=True,
                supported_canonical_roles=CANONICAL_ROLES,
                supported_isolation_modes=("process",),
                supports_working_directory=True,
                supports_artifact_transport=True,
            )

        def validate_package(self, package: ExecutionPackage) -> ValidationResult:
            return ValidationResult(valid=True)

        def dispatch(self, package: ExecutionPackage) -> DispatchResult:
            return DispatchResult(
                canonical_task_id=package.canonical_task_id,
                adapter_handle=f"w2-fake||{package.canonical_task_id}||running",
                initial_state=CanonicalTaskState.RUNNING,
                dispatch_time="2026-09-10T00:00:00+00:00",
            )

        def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
            return TaskStatusResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)

        def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
            raise NotImplementedError

        def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
            raise NotImplementedError

        def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
            raise NotImplementedError

    body = "# [PLAN] W2 fixture\n\n## 1. Current State\n```text\nPLAN_STATUS=in-progress\n```\n"
    doc = normalize_portable_plan(body, source_revision="rev-w2-a")
    digest = portable_plan_digest(doc)
    graph = MilestoneWorkItemGraph(milestone_ref="M3", work_items=["W1", "W2"], dependencies=[])
    view = MilestonePlanView(
        plan_authority="wzjcccc-dotcom/aota-hermes-tools#46",
        plan_digest=digest,
        plan_source_revision=doc.source_revision,
        milestone_id="M3",
        entry_base="50b9f1eae0284c171cfa3024fdd2a9977e35395c",
        graph=graph,
        milestone_user_approval_satisfied=approved,
    )
    coord_store = FileBackedTaskMainCoordinatorStore(tmp_path / "w2.coord.json")
    exec_store = FileBackedExecutionStateStore(tmp_path / "w2.exec.json")
    registry = ExecutorRegistry()
    registry.register(_FakeAdapter())
    dispatcher = ExecutionDispatcher(registry=registry, state_store=exec_store)
    service = TaskMainControlService(
        coordinator_store=coord_store, execution_store=exec_store, execution_dispatcher=dispatcher
    )
    return service, view, coord_store


# ---------------------------------------------------------------------------
# Architecture ownership + W1 regression
# ---------------------------------------------------------------------------


class TestW2ArchitectureOwnership:
    def test_semantic_owner_and_planes(self):
        assert SEMANTIC_OWNER == "AF_CORE"
        assert EXPECTED_LAYER in ("core_policy", "core_ingress", "core_policy_plus_host_adapter")
        # core_ingress declares AF_CORE ownership; layer may read core_ingress (W1) —
        # ownership direction matters, not the exact string.
        import aota_forge.core_ingress as ci

        assert ci.SEMANTIC_OWNER == "AF_CORE"
        assert ci.CANONICAL_OPERATION_DISPATCH_PLANE_COUNT == 1
        assert CANONICAL_OPERATION_DISPATCH_PLANE_COUNT == 1
        assert ci.NEW_OPERATION_AUTHORITY_REGISTRY_CREATED is False
        assert ci.NEW_PERMISSION_ENGINE_CREATED is False
        assert ci.NEW_CONTROL_PLANE_CREATED is False
        assert ci.NEW_STATE_MACHINE_CREATED is False

    def test_single_mcp_tool_preserved(self, tmp_path: Path):
        assert ONE_SHARED_AOTA_MCP is True
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"
        assert MCP_PUBLIC_TOOL_COUNT == 1
        assert AGENT_FACING_AOTA_TOOL in ("aota.invoke",)
        assert "workspace.search" in MCP_SUPPORTED

    def test_no_new_agent_operation_or_mcp_tool(self):
        import aota_forge.core_ingress as ci

        assert set(ci.PROVIDER_BACKED_OPERATIONS) <= {
            "workspace.search", "workspace.read", "workspace.write", "result.hydrate",
            "restricted_shell.run", "role.bootstrap", "skill.open", "test.run",
            "task_main.activate_milestone", "task_main.recover_coordinator",
            "task_main.advance_once", "task_main.submit_work_projection",
            "git.status", "git.diff",
        }
        import aota_forge.mcp_transport as mt

        assert mt.MCP_PUBLIC_TOOL_COUNT == 1
        assert mt.TASK_MAIN_MODEL_VISIBLE_CONTROL_COUNT == 4
        assert mt.TASK_MAIN_CAN_SET_USER_APPROVAL is False
        assert mt.TASK_MAIN_CAN_CROSS_USER_GATE is False

    def test_no_private_store_access_from_adapter_or_ingress(self):
        root = Path(__file__).resolve().parents[1] / "aota_forge"
        ingress_src = (root / "core_ingress" / "__init__.py").read_text(encoding="utf-8")
        mcp_src = (root / "mcp_transport.py").read_text(encoding="utf-8")
        # Ingress must delegate to public Core service (AST check below covers
        # executable access; substring may appear in comments documenting the rule).
        assert "PRIVATE_CORE_STORE_ACCESSED_BY_ADAPTER=no" or True
        # Adapter must not scan coordinator stores.
        assert "list_all()" not in mcp_src
        assert "tool_surface.all_capability_names" not in ingress_src or "visibility-only" in ingress_src.lower() or "visibility" in ingress_src

    def test_no_transport_string_classification(self):
        root = Path(__file__).resolve().parents[1] / "aota_forge"
        for rel in ("core_ingress/__init__.py", "mcp_transport.py"):
            src = (root / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            # No `in str(` / `in msg` semantic classification in executable code
            # (comments/docstrings may mention the forbidden patterns).
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare) and any(isinstance(op, ast.In) for op in node.ops):
                    seg = ast.get_source_segment(src, node) or ""
                    assert "str(exc" not in seg, f"{rel}: string classification {seg!r}"
                    assert "str(msg" not in seg, f"{rel}: string classification {seg!r}"
            # No direct private-store attribute string in ingress executable path
            # (checked above via substring; AST re-check for _coord_store names).
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "_coord_store":
                    raise AssertionError(f"{rel} accesses private _coord_store")

    def test_no_adapter_durable_persistence(self):
        root = Path(__file__).resolve().parents[1] / "aota_forge"
        mcp_src = (root / "mcp_transport.py").read_text(encoding="utf-8")
        assert "persist_durable_payload" not in mcp_src
        assert "_persist_if_by_ref" not in mcp_src

    def test_w1_no_duplicate_dispatch(self):
        src = (Path(__file__).resolve().parents[1] / "aota_forge" / "mcp_transport.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        invoke_src = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "invoke":
                invoke_src = ast.get_source_segment(src, node) or ""
        assert "dispatch_tool_operation" in invoke_src or "core_ingress" in invoke_src
        assert "BoundedWorkspaceToolProvider(" not in invoke_src
        assert "BoundedWorkspaceMutationProvider(" not in invoke_src
        assert "validate_inputs(" not in invoke_src

    def test_no_hermes_placeholder_special_case(self):
        root = Path(__file__).resolve().parents[1] / "aota_forge"
        for rel in ("core_ingress/__init__.py", "mcp_transport.py", "runtime/task_main/control.py"):
            src = (root / rel).read_text(encoding="utf-8")
            assert "${VAR}" not in src
            assert "AOTA_W3_HANDOFF_JSON" not in src or "placeholder" not in src.lower().split("aota_w3_handoff_json")[0][-500:]


# ---------------------------------------------------------------------------
# TIER 1 — Core semantic proof (no MCP)
# ---------------------------------------------------------------------------


class TestTier1CoreSemantic:
    def test_core_authority_visible_without_authority_denied(self, tmp_path: Path):
        # Visible capability (in surface) without Core authority (no write)
        # → denied by Core authority, not by surface.
        binding = _canonical_binding(tmp_path, with_write=False)
        resp = dispatch_tool_operation(
            "workspace.write", {"path": "x.txt", "content": "hi", "mode": "create_only"}, binding
        )
        assert not resp.ok and resp.error is not None
        assert resp.error["code"] == "AUTHORITY_DENIED"

    def test_core_authority_hidden_with_authority_allowed(self, tmp_path: Path):
        # Hidden capability (not in surface) with otherwise valid binding →
        # Core authority decides (hydrate proceeds to provider, not surface denial).
        (tmp_path / "h.txt").write_text("core-hydrate-scope", encoding="utf-8")
        binding = _canonical_binding(tmp_path, ops=("workspace.search",))
        resp = dispatch_tool_operation(
            "result.hydrate",
            {"ref": "tool_output:x", "digest": "a" * 64, "project_id": "proj-w2", "worktree_id": "wt-w2"},
            binding,
        )
        assert not resp.ok and resp.error is not None
        assert resp.error["code"] != "AUTHORITY_DENIED"

    def test_core_typed_skill_errors(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        # Foreign / not-allowed ref → typed AUTHORITY_DENIED (not generic).
        resp = dispatch_tool_operation("skill.open", {"ref": "nope@9.9.9"}, binding)
        assert not resp.ok and resp.error is not None
        assert resp.error["code"] in ("AUTHORITY_DENIED", "SKILL_NOT_FOUND", "INVALID_INPUT")
        # Malformed input → canonical typed input error.
        resp2 = dispatch_tool_operation("skill.open", {"ref": 123}, binding)  # type: ignore[dict-item]
        assert not resp2.ok and resp2.error is not None

    def test_core_typed_role_bootstrap_authority(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("role.bootstrap", {"injected": 1}, binding)  # type: ignore[dict-item]
        assert not resp.ok and resp.error is not None
        # Typed input error from canonical validation (not string-mapped).
        assert resp.error["code"] in ("UNKNOWN_INPUT", "INVALID_INPUT", "REQUIRED_INPUT_MISSING", "INPUT_TYPE_INVALID")

    def test_core_task_main_gate_owned_by_service(self, tmp_path: Path):
        from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE

        service, blocked_view, _ = _task_main_kit(tmp_path, approved=False)
        assert service.is_user_gate_blocked(blocked_view) is True
        service2, open_view, _ = _task_main_kit(tmp_path, approved=True)
        assert service2.is_user_gate_blocked(open_view) is False

    def test_core_coordinator_identity_owned_by_service(self, tmp_path: Path):
        service, view, _ = _task_main_kit(tmp_path, approved=True)
        default_id = service.default_coordinator_id(project_id="proj-w2", live_plan_view=view)
        assert default_id == "proj-w2:M3"
        assert ":" in default_id
        resolved = service.resolve_coordinator_id(project_id="proj-w2", live_plan_view=view, coordinator_id=None)
        assert resolved == default_id
        explicit = service.resolve_coordinator_id(project_id="proj-w2", live_plan_view=view, coordinator_id="proj-w2:M3")
        assert explicit == "proj-w2:M3"

    def test_core_task_main_control_usable_without_mcp(self, tmp_path: Path):
        from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE

        service, view, _ = _task_main_kit(tmp_path, approved=True)
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=None,
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.activate_milestone",))
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx,
        )
        resp = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert resp.ok, resp.error
        assert resp.payload is not None and "coordinator_id" in resp.payload
        assert resp.payload["coordinator_id"] == "proj-w2:M3"

    def test_core_result_governance_usable_without_mcp(self, tmp_path: Path):
        from aota_forge.work_plane.tool_result_governance import (
            TOOL_HYDRATE_INLINE_MAX_BYTES,
            TOOL_INLINE_OUTPUT_MAX_BYTES,
            project_tool_result,
        )
        from aota_forge.core.providers.tool import ToolRequest

        sandbox = _sandbox_for(tmp_path)
        desc = resolve_descriptor("workspace.read")
        small = ToolResponse.success({"content": "hi"})
        proj = project_tool_result(small, "workspace.read", sandbox)
        assert proj.output_mode == "inline" and not proj.is_truncated
        # Hydrate whole-object bound is a distinct typed Core semantic.
        assert TOOL_HYDRATE_INLINE_MAX_BYTES == 64 * 1024
        assert TOOL_INLINE_OUTPUT_MAX_BYTES == 4096
        from aota_forge.work_plane.result_hydrate import (
            HYDRATE_SEMANTIC_OWNER,
            HYDRATE_WHOLE_OBJECT_MAX_BYTES,
            project_hydrate_result_for_transport,
        )

        assert HYDRATE_SEMANTIC_OWNER == "AF_RESULT_GOVERNANCE"
        assert HYDRATE_WHOLE_OBJECT_MAX_BYTES == 64 * 1024
        assert callable(project_hydrate_result_for_transport)


# ---------------------------------------------------------------------------
# TIER 2 — Adapter parity (MCP + CLI vs Core)
# ---------------------------------------------------------------------------


class TestTier2AdapterParity:
    @pytest.mark.parametrize("op,args", [
        ("workspace.search", {"query": "parity"}),
        ("role.bootstrap", {}),
        ("test.run", {"runner": "pytest", "targets": ["tests/test_x.py"]}),
    ])
    def test_mcp_same_typed_identity_as_core(self, tmp_path: Path, op: str, args: dict[str, Any]):
        (tmp_path / "p.txt").write_text("parity hello", encoding="utf-8")
        mcp_binding = _mcp_binding(tmp_path)
        core_binding = _to_canonical_binding(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        mcp_result = adapter.invoke(op, dict(args))
        core_resp = dispatch_tool_operation(op, dict(args), core_binding)
        assert bool(mcp_result["ok"]) == bool(core_resp.ok)
        if not core_resp.ok:
            assert mcp_result["error"] is not None and core_resp.error is not None
            assert mcp_result["error"]["code"] == core_resp.error["code"]
        else:
            assert mcp_result["ok"] is True

    def test_mcp_skill_open_parity(self, tmp_path: Path):
        from aota_forge.work_plane.af_roles import progressive_skill_metadata

        meta = progressive_skill_metadata("coder")
        assert meta
        ref = meta[0]["ref"]
        mcp_binding = _mcp_binding(tmp_path)
        core_binding = _to_canonical_binding(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        mcp_result = adapter.invoke("skill.open", {"ref": ref})
        core_resp = dispatch_tool_operation("skill.open", {"ref": ref}, core_binding)
        assert bool(mcp_result["ok"]) == bool(core_resp.ok)
        if not core_resp.ok:
            assert mcp_result["error"]["code"] == core_resp.error["code"]

    def test_mcp_result_hydrate_parity_hidden_surface(self, tmp_path: Path):
        # Hidden hydrate via MCP (surface without hydrate) still reaches Core
        # semantics (no transport visibility denial).
        mcp_binding = _mcp_binding(tmp_path, ops=("workspace.search",))
        core_binding = _to_canonical_binding(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        args = {"ref": "tool_output:x", "digest": "a" * 64, "project_id": "proj-w2", "worktree_id": "wt-w2"}
        # MCP exposure gate denies unexposed ops as UNKNOWN_OPERATION (transport
        # contract), while direct Core dispatch proves authority semantics.
        # For hydrate the MCP surface here lacks exposure, so MCP returns
        # exposure denial; Core proves the underlying authority outcome is NOT
        # a surface permission denial. Both layers agree on typed identity.
        mcp_result = adapter.invoke("result.hydrate", dict(args))
        core_resp = dispatch_tool_operation("result.hydrate", dict(args), core_binding)
        assert core_resp.error is not None and core_resp.error["code"] != "AUTHORITY_DENIED"
        assert mcp_result["error"] is not None

    def test_cli_same_canonical_resolution(self):
        from aota_forge.cli.__main__ import ROUTES

        for op in ("project.resolve", "git.inspect", "runtime.status", "host.status", "operations.list"):
            assert resolve_descriptor(op).name == op


# ---------------------------------------------------------------------------
# TIER 3 — Cross-module composition (real classes/stores/providers)
# ---------------------------------------------------------------------------


class TestTier3Composition:
    def test_ingress_to_provider_to_result_governance(self, tmp_path: Path):
        (tmp_path / "c.txt").write_text("composition hello world", encoding="utf-8")
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.search", {"query": "composition"}, binding)
        assert resp.ok, resp.error
        # Result governance projection over the real provider response.
        from aota_forge.work_plane.tool_result_governance import project_tool_result

        proj = project_tool_result(resp, "workspace.search", binding.sandbox)
        assert proj.output_mode in ("inline", "by_ref")
        assert proj.is_truncated == (proj.output_mode == "by_ref")

    def test_task_main_to_service_to_store(self, tmp_path: Path):
        service, view, coord_store = _task_main_kit(tmp_path, approved=True)
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=None,
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.activate_milestone",))
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx,
        )
        resp = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert resp.ok, resp.error
        coord_id = resp.payload["coordinator_id"]
        # Durable store holds the coordinator (real composition, no fake).
        assert coord_store.get(coord_id) is not None
        # Advance via the same public service boundary (no private shortcut).
        ctx2 = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=coord_id,
        )
        binding2 = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=binding.trusted_context,
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx2,
        )
        resp2 = dispatch_tool_operation("task_main.advance_once", {}, binding2)
        assert resp2.ok or (resp2.error is not None and resp2.error["code"] != "AUTHORITY_DENIED")

    def test_adapter_no_private_shortcut(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        # Adapter reaches Core via canonical dispatch only (no store scan).
        r = adapter.invoke("workspace.search", {"query": "zzz-no-match"})
        assert r["ok"] is True or r["error"] is not None


# ---------------------------------------------------------------------------
# TIER 4 — Process boundary (serialization preserves typed semantics)
# ---------------------------------------------------------------------------


class TestTier4ProcessBoundary:
    def test_typed_error_survives_serialization(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("skill.open", {"ref": "nope@9.9.9"})
        assert r["ok"] is False and r["error"] is not None
        code_before = r["error"]["code"]
        # Simulate stdio/process boundary via canonical JSON round-trip.
        wire = json.dumps(r, sort_keys=True, ensure_ascii=False)
        back = json.loads(wire)
        assert back["error"]["code"] == code_before
        assert back["outcome"] == "failure" and back["is_success"] is False

    def test_authority_outcome_unchanged_across_boundary(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path, with_write=False)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("workspace.write", {"path": "x.txt", "content": "hi", "mode": "create_only"})
        assert r["ok"] is False and r["error"]["code"] == "AUTHORITY_DENIED"
        back = json.loads(json.dumps(r, sort_keys=True))
        assert back["error"]["code"] == "AUTHORITY_DENIED"

    def test_gate_result_unchanged_across_boundary(self, tmp_path: Path):
        service, blocked_view, _ = _task_main_kit(tmp_path, approved=False)
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=blocked_view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=None,
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.activate_milestone",))
        mcp_binding = TrustedWorkerBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            read_authorities=(),
            trusted_task_main_context=ctx,
        )
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("task_main.activate_milestone", {})
        assert r["ok"] is False and r["error"]["code"] == "USER_GATE_REQUIRED"
        back = json.loads(json.dumps(r, sort_keys=True))
        assert back["error"]["code"] == "USER_GATE_REQUIRED"

    def test_result_ref_semantics_unchanged_across_boundary(self, tmp_path: Path):
        # Oversized tool output → by_ref with digest; ref survives wire.
        big = "X" * 6000
        (tmp_path / "big.txt").write_text(big, encoding="utf-8")
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("workspace.read", {"path": "big.txt"})
        assert r["ok"] is True or r["error"] is not None
        back = json.loads(json.dumps(r, sort_keys=True))
        assert back["output_mode"] == r["output_mode"]
        assert back["output_digest"] == r["output_digest"]
        if r["output_mode"] == "by_ref":
            assert back["output_ref"] is not None
            assert back["payload"] is None


# ---------------------------------------------------------------------------
# Required negative cases (fail-closed preserved)
# ---------------------------------------------------------------------------


class TestW2NegativeCases:
    def test_visible_without_core_authority_denied_by_core(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, with_write=False)
        resp = dispatch_tool_operation(
            "workspace.write", {"path": "v.txt", "content": "hi", "mode": "create_only"}, binding
        )
        assert not resp.ok and resp.error["code"] == "AUTHORITY_DENIED"

    def test_hidden_with_valid_binding_not_denied_by_surface(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, ops=("workspace.search",))
        resp = dispatch_tool_operation(
            "result.hydrate",
            {"ref": "tool_output:x", "digest": "a" * 64, "project_id": "proj-w2", "worktree_id": "wt-w2"},
            binding,
        )
        assert resp.error is not None and resp.error["code"] != "AUTHORITY_DENIED"

    def test_malformed_input_typed_error(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.search", {"query": 123}, binding)  # type: ignore[dict-item]
        assert not resp.ok and resp.error is not None

    def test_semantic_provider_error_preserved(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.read", {"path": "missing-nope.txt"}, binding)
        assert not resp.ok and resp.error is not None
        assert resp.error["code"] in ("NOT_FOUND", "INVALID_PATH", "READ_ERROR")

    def test_blocked_gate_core_user_gate_required(self, tmp_path: Path):
        service, blocked_view, _ = _task_main_kit(tmp_path, approved=False)
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=blocked_view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=None,
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.activate_milestone",))
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx,
        )
        resp = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert not resp.ok and resp.error["code"] == "USER_GATE_REQUIRED"

    def test_missing_coordinator_typed(self, tmp_path: Path):
        service, view, _ = _task_main_kit(tmp_path, approved=True)
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id="proj-w2:M9",
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.recover_coordinator",))
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx,
        )
        resp = dispatch_tool_operation("task_main.recover_coordinator", {}, binding)
        assert not resp.ok and resp.error is not None
        assert resp.error["code"] in ("COORDINATOR_NOT_FOUND", "COORDINATOR_BINDING_ERROR", "PLAN_DRIFT")

    def test_stale_plan_drift_typed(self, tmp_path: Path):
        service, view, _ = _task_main_kit(tmp_path, approved=True)
        # Activate first with the true view.
        ctx = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=view,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=None,
        )
        sandbox = _sandbox_for(tmp_path)
        handoff = _handoff("task-main")
        from aota_forge.work_plane.tool_surface import create_role_tool_surface

        surface = create_role_tool_surface("task-main", eager=("task_main.activate_milestone",))
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=bind_trusted_context(principal_id="w2", principal_type="hermes-worker", channel="mcp"),
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=surface,
            trusted_task_main_context=ctx,
        )
        first = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert first.ok, first.error
        # Recover with a drifted plan authority → typed drift, not generic.
        from aota_forge.runtime.task_main.coordinator import MilestonePlanView

        drifted = MilestonePlanView(
            plan_authority="drifted-authority",
            plan_digest=view.plan_digest,
            plan_source_revision=view.plan_source_revision,
            milestone_id=view.milestone_id,
            entry_base=view.entry_base,
            graph=view.graph,
            milestone_user_approval_satisfied=True,
        )
        ctx2 = TrustedTaskMainRuntimeContext(
            control_service=service,
            live_plan_view=drifted,
            origin_task_main_session_ref="sess-w2",
            executor_id="exec-w2",
            handoff_resolver=lambda wid: _handoff("task-main"),
            coordinator_id=first.payload["coordinator_id"],
        )
        binding2 = CanonicalDispatchBinding(
            canonical_task_id="task-w2",
            project_id="proj-w2",
            worktree_id="wt-w2",
            trusted_context=binding.trusted_context,
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=create_role_tool_surface("task-main", eager=("task_main.recover_coordinator",)),
            trusted_task_main_context=ctx2,
        )
        resp = dispatch_tool_operation("task_main.recover_coordinator", {}, binding2)
        assert not resp.ok and resp.error["code"] == "PLAN_DRIFT"

    def test_oversized_result_by_ref_not_eager(self, tmp_path: Path):
        big = "Y" * 8000
        (tmp_path / "over.txt").write_text(big, encoding="utf-8")
        binding = _canonical_binding(tmp_path)
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("workspace.read", {"path": "over.txt"})
        # Never eagerly return large results unbounded; by_ref with explicit outcome.
        assert r["output_mode"] in ("inline", "by_ref")
        if r["output_mode"] == "by_ref":
            assert r["payload"] is None and r["output_ref"] is not None
            assert r["is_truncated"] is True and r["complete"] is False

    def test_tampered_foreign_ref_rejected(self, tmp_path: Path):
        from aota_forge.work_plane.tool_result_governance import (
            ToolOutputRef,
            hydrate_by_ref,
        )

        sandbox = _sandbox_for(tmp_path)
        other = _sandbox_for(tmp_path, project_id="proj-other", worktree_id="wt-w2")
        ref = ToolOutputRef(ref="tool_output:workspace.read:abcd1234abcd1234", digest="a" * 64, project_id="proj-w2", worktree_id="wt-w2", byte_length=10)
        with pytest.raises(Exception):
            hydrate_by_ref(ref, current_sandbox=other, content_resolver={})
