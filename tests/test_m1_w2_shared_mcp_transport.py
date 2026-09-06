"""M1/W2 restricted shared MCP transport contract and adversarial tests."""

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
        tool_surface=create_role_tool_surface("coder", eager=MCP_PUBLIC_TOOLS),
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


def _call(server, name: str, arguments: dict):
    async def run():
        tool = next(t for t in server._tool_manager.list_tools() if t.name == name)
        return tool.fn(**arguments)

    return asyncio.run(run())


def test_one_shared_server_has_exactly_three_typed_tools(tmp_path: Path):
    server = create_shared_mcp_server(_binding(tmp_path))
    tools = server._tool_manager.list_tools()
    assert [tool.name for tool in tools] == list(MCP_PUBLIC_TOOLS)
    assert all(tool.parameters["type"] == "object" for tool in tools)
    assert not any(tool.name == "aota_cli" for tool in tools)
    assert {name for name in tools[0].parameters["properties"]} == {"query", "max_results", "scope"}
    assert {name for name in tools[1].parameters["properties"]} == {"path", "max_bytes", "offset"}
    assert {name for name in tools[2].parameters["properties"]} == {"path", "content", "mode"}


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
    tool = next(
        tool for tool in create_shared_mcp_server(_binding(tmp_path, with_write=True))._tool_manager.list_tools()
        if tool.name == "workspace.write"
    )
    with pytest.raises(TypeError):
        tool.fn(
            path="safe.txt",
            content="safe",
            mode="create_only",
            project="attacker-project",
            worktree="/tmp/attacker-worktree",
        )
    assert not (tmp_path / "safe.txt").exists()


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


@pytest.mark.parametrize("tool, arguments", [
    ("workspace.read", {"path": "../outside.txt"}),
    ("workspace.write", {"path": "/tmp/outside.txt", "content": "x", "mode": "create_only"}),
])
def test_path_escape_is_fail_closed(tmp_path: Path, tool: str, arguments: dict):
    result = _call(create_shared_mcp_server(_binding(tmp_path, with_write=True)), tool, arguments)
    assert result["ok"] is False
    assert result["error"]["code"] in {"INVALID_PATH", "PATH_ESCAPE", "SYMLINK_ESCAPE"}


def test_malformed_binding_fails_closed(tmp_path: Path):
    with pytest.raises(TrustedBindingError):
        create_shared_mcp_server(None)  # type: ignore[arg-type]


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
    # The fixture performs the standard client/server tools/list and tools/call
    # exchange before exiting; stderr is retained only for diagnostics.
    assert result.returncode == 0, result.stderr
