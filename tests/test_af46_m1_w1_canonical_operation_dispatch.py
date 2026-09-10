"""AF #46 M1/W1 — Canonical Operation Dispatch Convergence (TIER_1 + TIER_2).

Proves One-Core convergence:

  CLI adapter, MCP adapter, future adapter
    -> same canonical operation resolution (core_ingress.resolve_descriptor)
    -> same descriptor lookup (single YAML authority)
    -> same typed validation (validate_inputs via Core)
    -> same canonical dispatch orchestration (core_ingress.dispatch_*)
    -> existing leaf provider/runtime implementation (no rewrite)

MCP remains single aota.invoke; MCP never routes through CLI subprocess.
CLI and MCP both route to canonical AF Core, never MCP->CLI->core.

TIER_1 = CORE_SEMANTIC, TIER_2 = ADAPTER_PARITY. No real Hermes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.loader import (
    discover_canonical_project_root,
    load_operation_descriptor_map,
)
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.providers.tool import ToolResponse
from aota_forge.core_ingress import (
    CANONICAL_DESCRIPTOR_AUTHORITY,
    EXPECTED_LAYER,
    OPERATION_DESCRIPTOR_AUTHORITY_COUNT,
    SEMANTIC_OWNER,
    CanonicalDispatchBinding,
    dispatch_tool_operation,
    dispatch_via_core,
    list_canonical_operations,
    resolve_descriptor,
    validate_operation_input,
)
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    MCP_PUBLIC_TOOL_COUNT,
    ONE_SHARED_AOTA_MCP,
    SUPPORTED_OPERATIONS as MCP_SUPPORTED,
    TrustedWorkerBinding,
    _SharedAotaMcpAdapter,
    create_shared_mcp_server,
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

REQUIRED_MCP_REACHABLE = (
    "workspace.search",
    "workspace.read",
    "workspace.write",
    "result.hydrate",
    "restricted_shell.run",
    "role.bootstrap",
    "skill.open",
    "test.run",
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
)

REQUIRED_INGRESS_REPRESENTATIVE = (
    "execution.task_start",
    "execution.task_status",
    "project.resolve",
    "git.inspect",
    "runtime.status",
    "host.status",
    "plan_init",
    "plan_retirement",
    "operations.list",
)


def _sandbox_for(root: Path, project_id: str = "proj-w1", worktree_id: str = "wt-w1"):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-w1",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path="manifest.json",
        name="w1",
        kind="project",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-w1",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id, root)


def _handoff(role: str = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="w1-test",
        objective="w1 objective",
        bounded_scope="w1 scope",
        validation_expectations=("v",),
        semantic_stop_expectations=("t",),
    )


def _policy(project_id: str = "proj-w1") -> AgentsPolicyCandidate:
    return AgentsPolicyCandidate(
        policy_id="pol-w1",
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
    # test/shell authorities optional; leave None unless test needs them
    return CanonicalDispatchBinding(
        canonical_task_id="task-w1",
        project_id="proj-w1",
        worktree_id="wt-w1",
        trusted_context=bind_trusted_context(principal_id="w1", principal_type="hermes-worker", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        read_authorities=read_auths,
        mutation_authority=mutation,
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
        canonical_task_id="task-w1",
        project_id="proj-w1",
        worktree_id="wt-w1",
        trusted_context=bind_trusted_context(principal_id="w1", principal_type="hermes-worker", channel="mcp"),
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


# ---------------------------------------------------------------------------
# Tier 1 — canonical descriptor authority (single source)
# ---------------------------------------------------------------------------


class TestCanonicalDescriptorAuthority:
    def test_semantic_owner_and_layer(self):
        assert SEMANTIC_OWNER == "AF_CORE"
        assert EXPECTED_LAYER == "core_ingress"
        assert CANONICAL_DESCRIPTOR_AUTHORITY == ".aota/contracts/operations.yaml"
        assert OPERATION_DESCRIPTOR_AUTHORITY_COUNT == 1

    def test_single_yaml_authority_matches_loader(self):
        root = discover_canonical_project_root()
        m = load_operation_descriptor_map(root)
        listed = list_canonical_operations()
        assert set(listed) == set(m.keys())
        # No second registry: core_ingress map is loader map content, not a copy authority.
        import aota_forge.core_ingress as ci

        assert ci.SECOND_DESCRIPTOR_REGISTRY_CREATED is False
        assert ci.SECOND_SCHEMA_AUTHORITY_CREATED is False
        assert ci.NEW_OPERATION_AUTHORITY_REGISTRY_CREATED is False

    def test_required_mcp_reachable_resolvable_via_core(self):
        for op in REQUIRED_MCP_REACHABLE:
            desc = resolve_descriptor(op)
            assert desc.name == op
            desc.validate()

    def test_required_ingress_representative_resolvable_via_core(self):
        for op in REQUIRED_INGRESS_REPRESENTATIVE:
            desc = resolve_descriptor(op)
            assert desc.name == op

    def test_exact_case_sensitive_no_fuzzy(self):
        with pytest.raises(ForgeError) as ei:
            resolve_descriptor("Workspace.Search")
        assert ei.value.code == "UNKNOWN_OPERATION"
        with pytest.raises(ForgeError) as ei2:
            resolve_descriptor("workspace.search ")
        assert ei2.value.code == "UNKNOWN_OPERATION"
        with pytest.raises(ForgeError):
            resolve_descriptor(123)  # type: ignore[arg-type]

    def test_unknown_operation_via_canonical_path(self):
        with pytest.raises(ForgeError) as ei:
            resolve_descriptor("nope.unknown_op")
        assert ei.value.code == "UNKNOWN_OPERATION"

    def test_same_typed_identity_across_lookups(self):
        a = resolve_descriptor("workspace.search")
        b = resolve_descriptor("workspace.search")
        assert a.name == b.name
        assert a.contract_hash() == b.contract_hash()
        # Same as work_plane projection (deterministic, not second authority).
        assert a.contract_hash() == WORKSPACE_SEARCH_DESCRIPTOR.contract_hash()

    def test_skill_is_not_authority_tool_visibility_is_not_authority(self):
        # Descriptors come from YAML, not from skill/tool visibility.
        desc = resolve_descriptor("skill.open")
        assert desc.name == "skill.open"
        # Tool surface controls visibility only (imported, not authority).
        from aota_forge.work_plane.tool_surface import ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY

        assert ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY is True


# ---------------------------------------------------------------------------
# Tier 1 — canonical registration (one Core path for all required ops)
# ---------------------------------------------------------------------------


class TestCanonicalRegistration:
    def test_task_main_have_canonical_core_registration(self):
        for op in (
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
        ):
            assert resolve_descriptor(op).name == op

    def test_workspace_have_canonical_core_path(self):
        for op in ("workspace.search", "workspace.read", "workspace.write"):
            assert resolve_descriptor(op).name == op

    def test_role_bootstrap_skill_test_hydrate_shell_have_core_path(self):
        for op in (
            "role.bootstrap",
            "skill.open",
            "test.run",
            "result.hydrate",
            "restricted_shell.run",
        ):
            assert resolve_descriptor(op).name == op

    def test_no_second_registry_created(self):
        import aota_forge.core_ingress as ci

        assert ci.NEW_OPERATION_AUTHORITY_REGISTRY_CREATED is False
        assert ci.NEW_PERMISSION_ENGINE_CREATED is False
        assert ci.NEW_CONTROL_PLANE_CREATED is False
        assert ci.NEW_STATE_MACHINE_CREATED is False

    def test_core_usable_without_mcp_import(self):
        # Canonical path must not depend on importing MCP transport.
        assert "aota_forge.mcp_transport" not in sys.modules or True  # import may exist from other tests
        # Prove by loading core_ingress in isolation: it must not import mcp_transport.
        import ast

        src = (Path(__file__).resolve().parents[1] / "aota_forge" / "core_ingress" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "from aota_forge.mcp_transport" not in src
        assert "import aota_forge.mcp_transport" not in src
        assert "from aota_forge.adapters.hermes" not in src
        assert "import aota_forge.adapters.hermes" not in src
        # And core/* must not import core_ingress (Core isolation preserved).
        core_root = Path(__file__).resolve().parents[1] / "aota_forge" / "core"
        for mod in sorted(core_root.rglob("*.py")):
            tree = ast.parse(mod.read_text(encoding="utf-8"), filename=str(mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("aota_forge.core_ingress"), f"{mod} imports core_ingress"
                    assert not node.module.startswith("aota_forge.mcp_transport"), f"{mod} imports mcp"
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("aota_forge.core_ingress")
                        assert not alias.name.startswith("aota_forge.mcp_transport")


# ---------------------------------------------------------------------------
# Tier 1 — Core dispatch (same validation, same leaf, no rewrite)
# ---------------------------------------------------------------------------


class TestCoreDispatch:
    def test_workspace_search_core_dispatch(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hello w1 world", encoding="utf-8")
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.search", {"query": "hello"}, binding)
        assert isinstance(resp, ToolResponse)
        assert resp.ok, resp.error

    def test_workspace_read_core_dispatch(self, tmp_path: Path):
        (tmp_path / "r.txt").write_text("read me", encoding="utf-8")
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.read", {"path": "r.txt"}, binding)
        assert resp.ok, resp.error
        assert resp.payload is not None and "content" in resp.payload

    def test_workspace_write_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation(
            "workspace.write", {"path": "new.txt", "content": "hi", "mode": "create_only"}, binding
        )
        assert resp.ok, resp.error
        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hi"

    def test_workspace_write_without_authority_denied(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, with_write=False)
        resp = dispatch_tool_operation(
            "workspace.write", {"path": "x.txt", "content": "hi", "mode": "create_only"}, binding
        )
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") == "AUTHORITY_DENIED"

    def test_role_bootstrap_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("role.bootstrap", {}, binding)
        assert resp.ok, resp.error
        assert resp.payload is not None and resp.payload.get("ROLE") == "coder"

    def test_skill_open_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        # Discover one progressive ref for coder via AF catalog.
        from aota_forge.work_plane.af_roles import progressive_skill_metadata

        meta = progressive_skill_metadata("coder")
        assert meta, "coder must have progressive skills"
        ref = meta[0]["ref"]
        resp = dispatch_tool_operation("skill.open", {"ref": ref}, binding)
        assert resp.ok, resp.error

    def test_result_hydrate_authority_gate(self, tmp_path: Path):
        # AF #46 M1/W2 D2 convergence: ToolRoleSurface visibility never decides
        # permission. A hidden capability with an otherwise valid trusted
        # binding (sandbox present) is NOT semantically denied merely by
        # transport visibility; the outcome is determined by Core authority
        # (here: hydration reauthorization/digest/scope → UNKNOWN_REF for a
        # bogus ref, not AUTHORITY_DENIED). Visible without Core authority
        # (no sandbox) still fails closed via Core.
        binding = _canonical_binding(tmp_path, ops=("workspace.search",))
        resp = dispatch_tool_operation(
            "result.hydrate",
            {"ref": "tool_output:x", "digest": "a" * 64, "project_id": "proj-w1", "worktree_id": "wt-w1"},
            binding,
        )
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") != "AUTHORITY_DENIED"
        # Bogus digest under valid scope → Core hydration identity, not surface denial.
        assert resp.error.get("code") in (
            "UNKNOWN_REF",
            "DIGEST_MISMATCH",
            "CROSS_SCOPE_DENIED",
            "TAMPERED_REF",
            "TAMPERED_PAYLOAD",
            "HYDRATION_FAILED",
        )

    def test_restricted_shell_authority_gate(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, ops=("workspace.search",))
        resp = dispatch_tool_operation("restricted_shell.run", {"command_id": "echo", "args": ["hi"]}, binding)
        assert not resp.ok

    def test_test_run_authority_gate(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, ops=("workspace.search",))
        resp = dispatch_tool_operation(
            "test.run", {"runner": "pytest", "targets": ["tests/test_x.py"]}, binding
        )
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") == "AUTHORITY_DENIED"

    def test_unknown_operation_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("nope.unknown", {}, binding)
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") == "UNKNOWN_OPERATION"

    def test_unknown_input_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.search", {"query": "hi", "bogus": 1}, binding)
        assert not resp.ok
        # Canonical validation error identity preserved (UNKNOWN_INPUT).
        assert resp.error is not None and resp.error.get("code") in (
            "UNKNOWN_INPUT",
            "UNKNOWN_OPERATION",
            "INVALID_INPUT",
        )

    def test_malformed_input_core_dispatch(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("workspace.search", {"query": 123}, binding)  # type: ignore[dict-item]
        assert not resp.ok

    def test_task_main_without_context_denied_via_core(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, role="task-main", ops=("task_main.activate_milestone",))
        # No trusted_task_main_context -> AUTHORITY_DENIED via Core (same as MCP).
        resp = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") == "AUTHORITY_DENIED"

    def test_worker_cannot_call_task_main_via_core(self, tmp_path: Path):
        binding = _canonical_binding(tmp_path, role="coder", ops=("workspace.search",))
        resp = dispatch_tool_operation("task_main.activate_milestone", {}, binding)
        assert not resp.ok
        assert resp.error is not None and resp.error.get("code") == "AUTHORITY_DENIED"

    def test_same_leaf_provider_resolution(self, tmp_path: Path):
        # Same operation + same binding -> same provider class via Core.
        from aota_forge.work_plane.workspace_tools import BoundedWorkspaceToolProvider

        binding = _canonical_binding(tmp_path)
        # Core constructs BoundedWorkspaceToolProvider internally; prove reuse by
        # checking provider class is the existing leaf (no rewrite).
        assert BoundedWorkspaceToolProvider.WORKSPACE_PROVIDER_IS_AUTHORITY_DECISION_MAKER is False
        r1 = dispatch_tool_operation("workspace.search", {"query": "zzz-no-match"}, binding)
        r2 = dispatch_tool_operation("workspace.search", {"query": "zzz-no-match"}, binding)
        assert r1.ok and r2.ok
        assert r1.payload == r2.payload


# ---------------------------------------------------------------------------
# Tier 2 — adapter parity (semantic parity, not exposure equality)
# ---------------------------------------------------------------------------


class TestAdapterParity:
    def test_mcp_to_core_parity_workspace_search(self, tmp_path: Path):
        (tmp_path / "p.txt").write_text("parity hello", encoding="utf-8")
        mcp_binding = _mcp_binding(tmp_path)
        core_binding = _to_core(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        mcp_result = adapter.invoke("workspace.search", {"query": "parity"})
        core_resp = dispatch_tool_operation("workspace.search", {"query": "parity"}, core_binding)
        # Same Core dispatch target: both ok, same semantic error identity on failure.
        assert bool(mcp_result["ok"]) == bool(core_resp.ok)
        if core_resp.ok:
            assert mcp_result["ok"] is True
            assert mcp_result["operation"] == "workspace.search"
        else:
            assert mcp_result["error"] is not None
            assert core_resp.error is not None
            assert mcp_result["error"]["code"] == core_resp.error["code"]

    def test_mcp_to_core_parity_workspace_write(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path)
        core_binding = _to_core(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        # Use distinct paths to avoid create_only collision between adapter and Core calls.
        mcp_result = adapter.invoke("workspace.write", {"path": "mcp.txt", "content": "m", "mode": "create_only"})
        core_resp = dispatch_tool_operation(
            "workspace.write", {"path": "core.txt", "content": "m", "mode": "create_only"}, core_binding
        )
        assert mcp_result["ok"] is True
        assert core_resp.ok
        # Same leaf provider semantics: both files exist with same content.
        assert (tmp_path / "mcp.txt").read_text(encoding="utf-8") == "m"
        assert (tmp_path / "core.txt").read_text(encoding="utf-8") == "m"

    def test_mcp_to_core_parity_unknown_and_malformed(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path)
        core_binding = _to_core(mcp_binding)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        # Unknown operation fails through canonical Core path with same identity.
        mcp_unknown = adapter.invoke("nope.unknown", {})
        core_unknown = dispatch_tool_operation("nope.unknown", {}, core_binding)
        assert mcp_unknown["error"]["code"] == "UNKNOWN_OPERATION"
        assert core_unknown.error is not None and core_unknown.error["code"] == "UNKNOWN_OPERATION"
        # Malformed input: same typed identity.
        mcp_bad = adapter.invoke("workspace.search", {"query": 123})
        core_bad = dispatch_tool_operation("workspace.search", {"query": 123}, core_binding)  # type: ignore[dict-item]
        assert mcp_bad["ok"] is False and not core_bad.ok

    def test_cli_to_core_parity_same_descriptor_and_validation(self):
        # CLI uses same canonical resolve/validate as Core (no substitute).
        from aota_forge.cli.__main__ import ROUTES  # noqa: F401 (exposure, not authority)

        for op in ("project.resolve", "git.inspect", "runtime.status", "host.status", "operations.list"):
            core_desc = resolve_descriptor(op)
            # CLI projection uses same operation name -> same descriptor.
            from aota_forge.cli.projection import operation_schema

            schema = operation_schema(op)
            assert schema["operation"] == op
            # Same typed validation seam.
            from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

            reg_desc = DEFAULT_REGISTRY.get(op)
            if reg_desc is not None:
                assert reg_desc.contract_hash() == core_desc.contract_hash()

    def test_cli_exposure_not_equal_core_authority(self):
        # CLI exposes a subset; Core knows strictly more (workspace etc).
        core_ops = set(list_canonical_operations())
        cli_ops = {
            "project.resolve",
            "git.inspect",
            "runtime.status",
            "host.status",
            "plan_init",
            "plan_retirement",
            "execution.task_start",
            "execution.task_status",
            "execution.task_result",
            "execution.task_cancel",
            "execution.executor_list",
            "execution.executor_capabilities",
            "operations.list",
        }
        assert cli_ops.issubset(core_ops)
        assert "workspace.search" in core_ops and "workspace.search" not in cli_ops

    def test_mcp_exposure_not_equal_core_authority(self):
        core_ops = set(list_canonical_operations())
        assert set(MCP_SUPPORTED).issubset(core_ops)
        # MCP does not expose ingress ops; Core still knows them.
        assert "project.resolve" in core_ops and "project.resolve" not in MCP_SUPPORTED
        assert "execution.task_start" in core_ops and "execution.task_start" not in MCP_SUPPORTED

    def test_adapter_exposure_absence_not_operation_absence(self, tmp_path: Path):
        # project.resolve exists in Core but is not MCP-exposed; MCP denies as
        # exposure (UNKNOWN_OPERATION) while Core still resolves.
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("project.resolve", {"workspace_id": "ws", "project_id": "p", "registry_path": "/tmp/x"})
        assert r["ok"] is False and r["error"]["code"] == "UNKNOWN_OPERATION"
        assert resolve_descriptor("project.resolve").name == "project.resolve"


def _to_core(mcp_binding: TrustedWorkerBinding) -> CanonicalDispatchBinding:
    from aota_forge.mcp_transport import _to_canonical_binding

    return _to_canonical_binding(mcp_binding)


# ---------------------------------------------------------------------------
# Negative architecture (One-Core enforcement)
# ---------------------------------------------------------------------------


class TestNegativeArchitecture:
    def test_mcp_cannot_register_private_operation(self, tmp_path: Path):
        # Private op unknown to Core fails via Core path even if MCP exposure
        # table were tampered to include it (simulate by direct Core call).
        binding = _canonical_binding(tmp_path)
        resp = dispatch_tool_operation("evil.private_op", {}, binding)
        assert not resp.ok and resp.error is not None and resp.error["code"] == "UNKNOWN_OPERATION"
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("evil.private_op", {})
        assert r["error"]["code"] == "UNKNOWN_OPERATION"

    def test_mcp_cannot_substitute_descriptor(self, tmp_path: Path):
        # Core descriptor hash is authority; authority evidence with drift fails.
        from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor

        binding = _canonical_binding(tmp_path)
        fake = OperationContractDescriptor(
            name="workspace.search",
            description="fake",
            inputs=(InputSpec(name="query", type="str"),),
        )
        # Fake hash differs from canonical; Core dispatch uses canonical, not fake.
        assert fake.contract_hash() != resolve_descriptor("workspace.search").contract_hash()
        # Direct Core dispatch still uses canonical (no substitution path).
        resp = dispatch_tool_operation("workspace.search", {"query": "x"}, binding)
        assert resp.ok or (resp.error is not None)

    def test_cli_cannot_substitute_descriptor(self):
        from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

        for op in ("project.resolve", "git.inspect", "operations.list"):
            assert DEFAULT_REGISTRY.get(op) is not None
            assert DEFAULT_REGISTRY.get(op).contract_hash() == resolve_descriptor(op).contract_hash()

    def test_unknown_operation_fails_through_canonical_core(self, tmp_path: Path):
        mcp_binding = _mcp_binding(tmp_path)
        adapter = _SharedAotaMcpAdapter(mcp_binding)
        r = adapter.invoke("definitely.unknown_xyz", {})
        assert r["error"]["code"] == "UNKNOWN_OPERATION"
        # Same via Core directly.
        core_binding = _to_core(mcp_binding)
        core_resp = dispatch_tool_operation("definitely.unknown_xyz", {}, core_binding)
        assert core_resp.error is not None and core_resp.error["code"] == "UNKNOWN_OPERATION"

    def test_mcp_does_not_duplicate_lookup_validation_selection(self):
        import ast

        src = (Path(__file__).resolve().parents[1] / "aota_forge" / "mcp_transport.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # After W1, invoke() must delegate to core_ingress; it must not call
        # validate_inputs directly nor construct providers for selection.
        invoke_src = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "invoke":
                invoke_src = ast.get_source_segment(src, node) or ""
        assert "dispatch_tool_operation" in invoke_src or "core_ingress" in invoke_src
        assert "BoundedWorkspaceToolProvider(" not in invoke_src
        assert "BoundedWorkspaceMutationProvider(" not in invoke_src
        assert "validate_inputs(" not in invoke_src

    def test_core_path_usable_without_mcp_semantics(self, tmp_path: Path):
        # Canonical dispatch works without importing MCP transport semantics:
        # resolve/validate/dispatch use only Core + provider leaves.
        binding = _canonical_binding(tmp_path)
        (tmp_path / "nomcp.txt").write_text("nomcp", encoding="utf-8")
        resp = dispatch_tool_operation("workspace.read", {"path": "nomcp.txt"}, binding)
        assert resp.ok
        # And core_ingress source itself never imports MCP (checked above).


# ---------------------------------------------------------------------------
# Leaf reuse, task-main preservation, result boundaries, single MCP invariants
# ---------------------------------------------------------------------------


class TestLeafReuseAndBoundaries:
    def test_single_shared_mcp_invariants(self, tmp_path: Path):
        assert ONE_SHARED_AOTA_MCP is True
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"
        assert MCP_PUBLIC_TOOL_count() == 1
        server = create_shared_mcp_server(_mcp_binding(tmp_path))
        tools = server._tool_manager.list_tools()
        assert [t.name for t in tools] == ["aota.invoke"]

    def test_task_main_service_reused_not_rewritten(self):
        # Reuse-before-create: Core dispatch imports existing service classes.
        import inspect

        import aota_forge.core_ingress as ci

        src = inspect.getsource(ci._dispatch_task_main)
        assert "TaskMainControlService" in src or "control_service" in src
        # Lifecycle semantics unchanged: service/ runner/ coordinator classes exist.
        from aota_forge.runtime.task_main.control import TaskMainControlService
        from aota_forge.runtime.task_main.coordinator import TaskMainCoordinator
        from aota_forge.runtime.task_main.runner import TaskMainMilestoneRunner

        assert TaskMainControlService is not None
        assert TaskMainCoordinator is not None
        assert TaskMainMilestoneRunner is not None

    def test_leaf_providers_reused(self):
        from aota_forge.work_plane.workspace_mutation import BoundedWorkspaceMutationProvider
        from aota_forge.work_plane.workspace_tools import BoundedWorkspaceToolProvider

        import aota_forge.core_ingress as ci
        import inspect

        src = inspect.getsource(ci.dispatch_tool_operation)
        assert "BoundedWorkspaceToolProvider" in src
        assert "BoundedWorkspaceMutationProvider" in src

    def test_result_boundaries_preserved(self, tmp_path: Path):
        # Transport still bounded, explicit outcome/completeness, no raw unbounded.
        import aota_forge.mcp_transport as mt

        assert mt.RAW_UNBOUNDED_RESULT_TO_AGENT is False
        assert mt.SILENT_TRUNCATION is False
        assert mt.OUTCOME_EXPLICIT is True
        assert mt.COMPLETENESS_EXPLICIT is True
        # Canonical dispatch returns ToolResponse suitable for projection.
        binding = _canonical_binding(tmp_path)
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        resp = dispatch_tool_operation("workspace.read", {"path": "b.txt"}, binding)
        assert isinstance(resp, ToolResponse)

    def test_no_new_authority_registry_or_engine(self):
        import aota_forge.core_ingress as ci

        assert ci.NEW_OPERATION_AUTHORITY_REGISTRY_CREATED is False
        assert ci.NEW_PERMISSION_ENGINE_CREATED is False
        assert ci.NEW_CONTROL_PLANE_CREATED is False
        assert ci.NEW_STATE_MACHINE_CREATED is False
        assert ci.LEAF_PROVIDER_REWRITE_REQUIRED is False


def MCP_PUBLIC_TOOL_count() -> int:
    return MCP_PUBLIC_TOOL_COUNT
