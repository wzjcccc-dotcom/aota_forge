"""Protocol-smoke child: standard MCP client talks to the shared server."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.mcp_transport import TrustedWorkerBinding
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


def _is_error(result) -> bool:
    return result.is_error if hasattr(result, "is_error") else result.isError


def _structured_content(result):
    return result.structured_content if hasattr(result, "structured_content") else result.structuredContent


def _binding(root: Path):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-test", workspace_root=str(root), project_id="proj-test",
        project_root=str(root), manifest_path="manifest.json", name="test",
        kind="project", status="active", registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED", workspace_id="ws-test", workspace_root=str(root),
        registry_fingerprint="a" * 64, listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, "wt-test", root)
    handoff = TaskHandoff(
        work_role="coder", task_kind="m1-w2-smoke", objective="protocol smoke",
        bounded_scope="one trusted worktree", validation_expectations=("smoke",),
        semantic_stop_expectations=("stop on failure",),
    )
    policy = AgentsPolicyCandidate(
        policy_id="policy-test", project_id="proj-test", scope="", content="policy",
        provenance_ref="agents:AGENTS.md",
    )
    return TrustedWorkerBinding(
        canonical_task_id="task-smoke", project_id="proj-test", worktree_id="wt-test",
        trusted_context=bind_trusted_context(
            principal_id="worker-smoke", principal_type="hermes-worker", channel="mcp"
        ),
        handoff=handoff, sandbox=sandbox,
        tool_surface=create_role_tool_surface(
            "coder", eager=("workspace.search", "workspace.read", "workspace.write")
        ),
        read_authorities=(
            create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_SEARCH_DESCRIPTOR),
            create_workspace_authority(sandbox, handoff, [policy], WORKSPACE_READ_DESCRIPTOR),
        ),
    )


async def main() -> None:
    root = Path(os.environ["AOTA_MCP_TEST_ROOT"])
    # Use the same executable and a tiny child command so the smoke test uses
    # the real stdio MCP transport, not an in-process shortcut.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "import asyncio, os; from pathlib import Path; from aota_forge.mcp_transport import run_shared_mcp_server; from mcp_server_process import _binding; asyncio.run(run_shared_mcp_server(_binding(Path(os.environ['AOTA_MCP_TEST_ROOT']))))"],
        env={
            **os.environ,
            "AOTA_MCP_TEST_ROOT": str(root),
            "PYTHONPATH": os.environ.get("PYTHONPATH", "") + os.pathsep + str(Path(__file__).resolve().parent),
        },
    )
    async with stdio_client(params) as (read_stream, write_stream), ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["aota.invoke"]
            # also ensure logical operations are NOT separate tools
            assert "workspace.search" not in [tool.name for tool in listed.tools]
            assert "workspace.read" not in [tool.name for tool in listed.tools]
            assert "workspace.write" not in [tool.name for tool in listed.tools]
            called = await session.call_tool("aota.invoke", {"operation": "workspace.search", "arguments": {"query": "missing"}})
            assert _is_error(called) is False
            assert _structured_content(called)["ok"] is True
            denied = await session.call_tool(
                "aota.invoke", {"operation": "workspace.write", "arguments": {"path": "not-authorized.txt", "content": "x", "mode": "create_only"}}
            )
            assert _is_error(denied) is False
            assert _structured_content(denied)["ok"] is False
            assert _structured_content(denied)["error"]["code"] == "AUTHORITY_DENIED"


if __name__ == "__main__":
    asyncio.run(main())
