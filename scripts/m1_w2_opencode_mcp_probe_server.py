"""M1/W2 bounded OpenCode <-> AOTA MCP probe seam (not a production entry).

Runs the real shared AOTA MCP transport (`mcp_transport.run_shared_mcp_server`)
with an in-process test binding built from the accepted AF test fixture pattern
(see tests/mcp_server_process.py). Used only to empirically prove the pinned
OpenCode v1.18.30 MCP contract (connect / tools.list / tools.call) against the
dedicated AF reference server. Binding authority remains AF-owned; this seam is
bounded probe transport, not a second AOTA server implementation.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.mcp_transport import TrustedWorkerBinding, run_shared_mcp_server
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


def _binding(root: Path) -> TrustedWorkerBinding:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-m1probe", workspace_root=str(root), project_id="proj-m1probe",
        project_root=str(root), manifest_path="manifest.json", name="m1-probe",
        kind="project", status="active", registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED", workspace_id="ws-m1probe", workspace_root=str(root),
        registry_fingerprint="a" * 64, listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, "wt-m1probe", root)
    handoff = TaskHandoff(
        work_role="coder", task_kind="m1-w2-opencode-mcp-probe", objective="MCP contract probe",
        bounded_scope="one bounded probe root", validation_expectations=("probe",),
        semantic_stop_expectations=("stop on failure",),
    )
    policy = AgentsPolicyCandidate(
        policy_id="policy-m1probe", project_id="proj-m1probe", scope="", content="policy",
        provenance_ref="agents:AGENTS.md",
    )
    return TrustedWorkerBinding(
        canonical_task_id="task-m1probe", project_id="proj-m1probe", worktree_id="wt-m1probe",
        trusted_context=bind_trusted_context(
            principal_id="worker-m1probe", principal_type="opencode-worker", channel="mcp"
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


def main() -> None:
    root = Path(os.environ["AOTA_MCP_TEST_ROOT"])
    asyncio.run(run_shared_mcp_server(_binding(root)))


if __name__ == "__main__":
    main()
