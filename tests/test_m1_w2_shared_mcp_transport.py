"""M1/W1 single-entry transport + W2 shared MCP transport contract and adversarial tests.

W1 converges the three workspace operations into a single Agent-facing tool:

    aota.invoke(operation, arguments)

This file preserves the adversarial coverage of the original W2 transport
tests but exercises the single-entry surface.  It validates:

* tools/list exposes exactly aota.invoke
* workspace.search/read/write are NOT separate tools
* exact operation resolution → UNKNOWN_OPERATION
* unknown input → UNKNOWN_INPUT, oversized/deep → INPUT_SIZE_EXCEEDED
* authority separation (write without mutation authority → AUTHORITY_DENIED)
* project/worktree override attempt fails (UNKNOWN_INPUT)
* path escape fails closed
* provider reuse and no canonical_path leakage
* transport vs logical surface separation
* real MCP protocol smoke (in-process and via fixture)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOLS,
    MCP_PUBLIC_TOOL_COUNT,
    WORKSPACE_OPERATIONS,
    AGENT_FACING_AOTA_TOOL,
    HERMES_AGENT_FACING_AOTA_TOOL_COUNT,
    SUPPORTED_OPERATIONS,
    TRANSPORT_OPERATION_IDENTITY_SEPARATED,
    TOOL_VISIBILITY_IS_AUTHORITY,
    TrustedBindingError,
    TrustedWorkerBinding,
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


def _binding(root: Path, *, with_write: bool = False) -> TrustedWorkerBinding:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-test",
        workspace_root=str(root),
        project_id="proj-test",
        project_root=str(root),
        manifest_path="manifest.json",
        name="test",
        kind="project",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-test",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, "wt-test", root)
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="m1-w2-test",
        objective="exercise restricted transport",
        bounded_scope="one trusted worktree",
        validation_expectations=("targeted tests",),
        semantic_stop_expectations=("stop on authority failure",),
    )
    policy = AgentsPolicyCandidate(
        policy_id="policy-test",
        project_id="proj-test",
        scope="",
        content="bounded policy",
        provenance_ref="agents:AGENTS.md",
    )
    return TrustedWorkerBinding(
        canonical_task_id="task-test",
        project_id="proj-test",
        worktree_id="wt-test",
        trusted_context=bind_trusted_context(
            principal_id="worker-test",
            principal_type="hermes-worker",
            channel="mcp",
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface("coder", eager=WORKSPACE_OPERATIONS),
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


def _call(server, operation: str, arguments: dict):
    async def run():
        # Single-entry tool is always aota.invoke
        tool = next(t for t in server._tool_manager.list_tools() if t.name == "aota.invoke")
        return tool.fn(operation=operation, arguments=arguments)
    return asyncio.run(run())


def test_one_shared_server_has_exactly_one_typed_tool(tmp_path: Path):
    server = create_shared_mcp_server(_binding(tmp_path))
    tools = server._tool_manager.list_tools()
    assert [tool.name for tool in tools] == list(MCP_PUBLIC_TOOLS) == ["aota.invoke"]
    assert MCP_PUBLIC_TOOL_COUNT == 1
    assert HERMES_AGENT_FACING_AOTA_TOOL_COUNT == 1
    assert AGENT_FACING_AOTA_TOOL == "aota.invoke"
    # M2 convergence: logical surface expands to 5 ops (workspace.* + result.hydrate + restricted_shell.run)
    # M1 regression: workspace.* must remain subset of supported (not necessarily exact equality)
    assert frozenset(WORKSPACE_OPERATIONS).issubset(SUPPORTED_OPERATIONS)
    # For M2, full set is 5; for M1 backward compat, keep at least workspace.* subset
    from aota_forge.mcp_transport import LOGICAL_OPERATIONS as _LO
    assert set(_LO).issuperset(set(WORKSPACE_OPERATIONS))
    # transport vs logical separation
    assert TRANSPORT_OPERATION_IDENTITY_SEPARATED is True
    assert TOOL_VISIBILITY_IS_AUTHORITY is False
    # ensure logical operations are NOT separate MCP tools
    assert "workspace.search" not in [tool.name for tool in tools]
    assert "workspace.read" not in [tool.name for tool in tools]
    assert "workspace.write" not in [tool.name for tool in tools]
    # schema is fixed-cost: aota.invoke has operation:string + arguments:object
    assert tools[0].parameters["type"] == "object"
    props = tools[0].parameters["properties"]
    assert "operation" in props and props["operation"]["type"] == "string"
    assert "arguments" in props
    # no per-operation schemas exposed as separate tools
    assert not any(tool.name == "aota_cli" for tool in tools)
    # server via async list_tools also exactly one
    listed = asyncio.run(server.list_tools())
    assert [t.name for t in listed] == ["aota.invoke"]


def test_visible_write_without_mutation_authority_is_rejected(tmp_path: Path):
    result = _call(
        create_shared_mcp_server(_binding(tmp_path, with_write=False)),
        "workspace.write",
        {"path": "new.txt", "content": "no", "mode": "create_only"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "AUTHORITY_DENIED"
    assert not (tmp_path / "new.txt").exists()


def test_model_arguments_cannot_override_trusted_project_or_worktree(tmp_path: Path):
    # After single-entry, override attempt is via arguments object, not top-level tool params.
    # The adapter must reject unknown inputs like project/worktree as UNKNOWN_INPUT.
    result = _call(
        create_shared_mcp_server(_binding(tmp_path, with_write=True)),
        "workspace.write",
        {"path": "safe.txt", "content": "safe", "mode": "create_only", "project": "attacker-project"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_INPUT"
    assert not (tmp_path / "safe.txt").exists()

    result2 = _call(
        create_shared_mcp_server(_binding(tmp_path, with_write=True)),
        "workspace.search",
        {"query": "hi", "project_id": "attacker"},
    )
    assert result2["ok"] is False
    assert result2["error"]["code"] == "UNKNOWN_INPUT"

    # Also ensure top-level MCP transport cannot be tricked: calling the MCP tool
    # with an extra top-level kwarg that is not operation/arguments must fail at
    # the MCP signature level (TypeError).
    server = create_shared_mcp_server(_binding(tmp_path, with_write=True))
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "aota.invoke")
    with pytest.raises(TypeError):
        tool.fn(operation="workspace.write", arguments={"path": "safe.txt", "content": "x", "mode": "create_only"}, project="attacker-project")  # type: ignore[call-arg]


def test_read_search_and_write_reuse_existing_providers(tmp_path: Path):
    (tmp_path / "source.txt").write_text("needle", encoding="utf-8")
    server = create_shared_mcp_server(_binding(tmp_path, with_write=True))
    read = _call(server, "workspace.read", {"path": "source.txt"})
    search = _call(server, "workspace.search", {"query": "needle"})
    write = _call(server, "workspace.write", {"path": "out.txt", "content": "x", "mode": "create_only"})
    assert read["ok"] is True and read["payload"]["content"] == "needle"
    assert search["ok"] is True and search["payload"]["total_matches"] == 1
    assert write["ok"] is True and (tmp_path / "out.txt").read_text() == "x"
    assert "canonical_path" not in read["payload"]
    assert "canonical_path" not in write["payload"]


@pytest.mark.parametrize("operation, arguments", [
    ("workspace.read", {"path": "../outside.txt"}),
    ("workspace.write", {"path": "/tmp/outside.txt", "content": "x", "mode": "create_only"}),
])
def test_path_escape_is_fail_closed(tmp_path: Path, operation: str, arguments: dict):
    result = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), operation, arguments)
    assert result["ok"] is False
    assert result["error"]["code"] in {"INVALID_PATH", "PATH_ESCAPE", "SYMLINK_ESCAPE", "INVALID_PATH"}


def test_malformed_binding_fails_closed(tmp_path: Path):
    with pytest.raises(TrustedBindingError):
        create_shared_mcp_server(None)  # type: ignore[arg-type]


def test_unknown_operation_is_unknown_operation(tmp_path: Path):
    result = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), "workspace.unknown", {"path": "x"})
    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_OPERATION"
    # case-sensitive
    result2 = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), "Workspace.Search", {"query": "hi"})
    assert result2["ok"] is False
    assert result2["error"]["code"] == "UNKNOWN_OPERATION"


def test_unknown_input_is_unknown_input(tmp_path: Path):
    result = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), "workspace.search", {"query": "hi", "bad": "field"})
    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_INPUT"


def test_oversized_input_is_input_size_exceeded(tmp_path: Path):
    big = "x" * 5000
    result = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), "workspace.search", {"query": big})
    assert result["ok"] is False
    assert result["error"]["code"] == "INPUT_SIZE_EXCEEDED"
    many = {f"k{i}": "v" for i in range(130)}
    result2 = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), "workspace.search", many)
    assert result2["ok"] is False
    assert result2["error"]["code"] == "INPUT_SIZE_EXCEEDED"


def test_transport_surface_not_equal_logical_surface(tmp_path: Path):
    # W1 separation: MCP_PUBLIC_TOOLS (transport) != WORKSPACE_OPERATIONS (logical)
    assert set(MCP_PUBLIC_TOOLS) != set(WORKSPACE_OPERATIONS)
    assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
    assert set(WORKSPACE_OPERATIONS) == {"workspace.search", "workspace.read", "workspace.write"}
    # ToolRoleSurface must carry logical, not transport
    binding = _binding(tmp_path, with_write=True)
    assert set(binding.tool_surface.all_capability_names()) == set(WORKSPACE_OPERATIONS)
    assert "aota.invoke" not in binding.tool_surface.all_capability_names()


def test_protocol_smoke_is_available_with_hermes_mcp_sdk(tmp_path: Path):
    pytest.importorskip("mcp")
    fixture = Path(__file__).with_name("mcp_server_process.py")
    env = dict(os.environ)
    env["AOTA_MCP_TEST_ROOT"] = str(tmp_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(fixture)],
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
