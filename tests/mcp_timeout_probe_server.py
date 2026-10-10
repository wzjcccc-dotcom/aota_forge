"""Deterministic stdio-MCP fixture for the bounded timeout contract test."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.mcp_transport import TrustedWorkerBinding, run_shared_mcp_server
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.test_execution import (
    TEST_RUN_DESCRIPTOR,
    create_test_execution_authority,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


PROJECT_ID = "proj_timeout_contract"
WORKTREE_ID = "wt_timeout_contract"
TASK_ID = "timeout-contract-probe-task"
SIMULATED_LONG_DURATION_SECONDS = 20
SCALED_LONG_DURATION_SECONDS = SIMULATED_LONG_DURATION_SECONDS / 1000


class _ProbeProcess:
    def __init__(self, mode: str, argv: list[str], **_kwargs: Any) -> None:
        self._mode = mode
        self.args = argv
        self.returncode: int | None = None
        self.pid = 2**30
        self._terminated = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self._mode == "inner-timeout" and not self._terminated:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self._terminated:
            self.returncode = -15
            return b"bounded test process terminated", b""
        time.sleep(SCALED_LONG_DURATION_SECONDS)
        self.returncode = 0
        return (
            f"SIMULATED_LEGAL_TEST_DURATION_SECONDS={SIMULATED_LONG_DURATION_SECONDS}".encode(),
            b"",
        )

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self._terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self._terminated = True
        self.returncode = -9


def _binding(root: Path) -> tuple[TrustedWorkerBinding, Any]:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws_timeout_contract",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        manifest_path=".aota/project.yaml",
        name=PROJECT_ID,
        kind="test-fixture",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws_timeout_contract",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, WORKTREE_ID, root)
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="opencode-mcp-timeout-contract-probe",
        objective="Verify bounded test.run and semantic return over the shared MCP transport",
        bounded_scope="one temporary MCP timeout fixture",
        validation_expectations=("test.run remains inside its requested bound",),
        semantic_stop_expectations=("return a governed blocked result after validation timeout",),
    )
    policy = AgentsPolicyCandidate(
        policy_id="policy_timeout_contract",
        project_id=PROJECT_ID,
        scope="",
        content="temporary bounded transport fixture",
        provenance_ref="test-fixture:timeout-contract",
    )
    binding = TrustedWorkerBinding(
        canonical_task_id=TASK_ID,
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        trusted_context=bind_trusted_context(
            principal_id="worker_timeout_contract",
            principal_type="opencode-worker",
            channel="mcp",
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface(
            "coder",
            eager=("workspace.read", "test.run", "handoff.write", "task.return"),
        ),
        read_authorities=(
            create_workspace_authority(
                sandbox, handoff, (policy,), WORKSPACE_READ_DESCRIPTOR
            ),
        ),
        test_execution_authority=create_test_execution_authority(
            sandbox, handoff, (policy,), TEST_RUN_DESCRIPTOR
        ),
    )
    return binding, sandbox


async def _serve() -> None:
    mode = os.environ["AOTA_MCP_TIMEOUT_PROBE_MODE"]
    if mode not in {"long-legal-run", "inner-timeout"}:
        raise ValueError("unknown timeout probe mode")
    root = Path(os.environ["AOTA_MCP_TIMEOUT_PROBE_ROOT"]).resolve()
    import aota_forge.work_plane.test_execution as test_execution

    test_execution.subprocess.Popen = lambda argv, **kwargs: _ProbeProcess(  # type: ignore[assignment]
        mode, list(argv), **kwargs
    )
    binding, _sandbox = _binding(root)
    await run_shared_mcp_server(binding)


if __name__ == "__main__":
    asyncio.run(_serve())
