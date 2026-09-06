"""M1/W3 one-shot Worker composition and real Hermes smoke entrypoint.

This module wires existing contracts only:

    TaskHandoff -> ExecutionPackage -> W1 Hermes dispatcher -> W2 MCP server
    -> CanonicalResult -> ResultGovernanceProjection -> WorkerResultCard

The MCP child receives its binding through process environment populated by
this trusted composition boundary. Model-facing tool arguments never contain
project, worktree, or authority fields.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.mcp_transport import TrustedWorkerBinding
from aota_forge.runtime.config import RuntimeBinding, RuntimeConfig
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.result_card import (
    WorkerResultCard,
    project_worker_result_card,
)
from aota_forge.work_plane.roles import AgentWorkRole
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

MCP_ROOT_ENV = "AOTA_W3_MCP_ROOT"
MCP_PROJECT_ENV = "AOTA_W3_PROJECT_ID"
MCP_WORKTREE_ENV = "AOTA_W3_WORKTREE_ID"
MCP_TASK_ENV = "AOTA_W3_TASK_ID"
MCP_HANDOFF_ENV = "AOTA_W3_HANDOFF_JSON"
MCP_TRACE_ENV = "AOTA_W3_TOOL_TRACE"

REAL_HERMES_VERSION = "Hermes Agent v0.21.0"
MCP_PROFILE_NAME = "aota-worker"
MCP_SERVER_MODULE = "aota_forge.composition.worker_vertical_slice"


@dataclass(frozen=True)
class WorkerSliceResult:
    handoff: TaskHandoff
    package: Any
    runtime_binding: RuntimeBinding
    canonical_result: CanonicalResult
    governance_projection: ResultGovernanceProjection
    worker_result_card: WorkerResultCard
    tool_trace: tuple[str, ...]


def _project_evidence(root: Path, project_id: str) -> ProjectResolutionEvidence:
    candidate = ProjectCandidateEvidence(
        workspace_id=f"w3-{project_id}",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path="runtime-smoke/input.txt",
        name=project_id,
        kind="disposable-smoke",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    return ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id=f"w3-{project_id}",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )


def build_worker_binding(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
) -> TrustedWorkerBinding:
    """Build one trusted server-side W2 binding from typed existing evidence."""
    sandbox = bind_worktree_sandbox(_project_evidence(root, project_id), worktree_id, root)
    policy = AgentsPolicyCandidate(
        policy_id="m1-w3-disposable-smoke",
        project_id=project_id,
        scope="runtime-smoke",
        content="Only the bounded runtime-smoke fixture is in scope.",
        provenance_ref="m1/w3",
    )
    read_authorities = (
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_SEARCH_DESCRIPTOR),
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_READ_DESCRIPTOR),
    )
    mutation_authority = create_workspace_mutation_authority(
        sandbox,
        handoff,
        (policy,),
        WORKSPACE_WRITE_DESCRIPTOR,
    )
    return TrustedWorkerBinding(
        canonical_task_id=canonical_task_id,
        project_id=project_id,
        worktree_id=worktree_id,
        trusted_context=bind_trusted_context(
            principal_id="hermes-worker",
            principal_type="hermes-worker",
            channel="mcp",
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface("coder", eager=("workspace.search", "workspace.read", "workspace.write")),
        read_authorities=read_authorities,
        mutation_authority=mutation_authority,
    )


def _read_worker_binding_from_environment() -> TrustedWorkerBinding:
    root = Path(os.environ[MCP_ROOT_ENV]).resolve()
    handoff = TaskHandoff.from_dict(json.loads(os.environ[MCP_HANDOFF_ENV]))
    return build_worker_binding(
        root=root,
        project_id=os.environ[MCP_PROJECT_ENV],
        worktree_id=os.environ[MCP_WORKTREE_ENV],
        canonical_task_id=os.environ[MCP_TASK_ENV],
        handoff=handoff,
    )


async def _serve_mcp_child() -> None:
    """Run the actual W2 server used by Hermes, with server-side binding."""
    from aota_forge import mcp_transport

    server = mcp_transport.create_shared_mcp_server(_read_worker_binding_from_environment())
    trace_path = Path(os.environ[MCP_TRACE_ENV])
    for tool in server._tool_manager.list_tools():
        original = tool.fn
        name = tool.name

        def traced(*args: Any, _original=original, _name=name, **kwargs: Any):
            with trace_path.open("a", encoding="utf-8") as trace:
                trace.write(f"{_name}\n")
            return _original(*args, **kwargs)

        tool.fn = traced
    await server.run_stdio_async()


def _write_smoke_fixture(root: Path, token: str) -> None:
    fixture = root / "runtime-smoke"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "input.txt").write_text(f"AOTA_M1_W3_SENTINEL={token}\n", encoding="utf-8")


@contextmanager
def _worker_environment(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    trace_path: Path,
) -> Iterator[None]:
    names = {
        MCP_ROOT_ENV: str(root),
        MCP_PROJECT_ENV: project_id,
        MCP_WORKTREE_ENV: worktree_id,
        MCP_TASK_ENV: canonical_task_id,
        MCP_HANDOFF_ENV: json.dumps(handoff.to_dict(), sort_keys=True),
        MCP_TRACE_ENV: str(trace_path),
    }
    old = {key: os.environ.get(key) for key in names}
    old_pythonpath = os.environ.get("PYTHONPATH")
    os.environ.update(names)
    repo_root = str(Path(__file__).resolve().parents[2])
    os.environ["PYTHONPATH"] = repo_root + (os.pathsep + old_pythonpath if old_pythonpath else "")
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _wait_for_result(dispatcher: Any, task_id: str, timeout_seconds: float) -> CanonicalResult:
    deadline = time.monotonic() + timeout_seconds
    while True:
        status = dispatcher.status(task_id)
        if status.state.is_terminal:
            return dispatcher.result(task_id)
        if time.monotonic() >= deadline:
            raise TimeoutError("bounded Hermes one-shot worker did not reach a terminal state")
        time.sleep(0.5)


def run_one_shot_worker(
    *,
    root: Path,
    token: str,
    canonical_task_id: str = "m1-w3-real-worker",
    project_id: str = "aota_forge",
    worktree_id: str = "m1-w3-real-one-shot-worker-slice",
    runtime_config: RuntimeConfig,
    trace_path: Path,
    timeout_seconds: float = 300.0,
) -> WorkerSliceResult:
    """Execute the bounded real Hermes Worker path and project its CARD."""
    root = root.resolve()
    _write_smoke_fixture(root, token)
    expected = f"AOTA_M1_W3_OUTPUT={token}\n"
    handoff = TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="m1-w3-real-one-shot-worker",
        objective=(
            "Use only workspace.search, workspace.read, and workspace.write. "
            f"Find the exact sentinel AOTA_M1_W3_SENTINEL={token} in runtime-smoke/input.txt, "
            "read the file, then write exactly "
            f"{expected!r} to runtime-smoke/output.txt. Do not use terminal, shell, git, or network."
        ),
        bounded_scope="runtime-smoke/input.txt and runtime-smoke/output.txt only",
        validation_expectations=("output contains the exact transformed sentinel",),
        semantic_stop_expectations=("stop if any governed workspace operation is denied",),
    )
    build_worker_binding(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
    )
    trusted_binding = TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=project_id)
    package = compile_handoff_to_execution_package(handoff, trusted_binding)
    runtime_binding = runtime_config.get_binding(AgentWorkRole.CODER)
    with _worker_environment(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
        trace_path=trace_path,
    ):
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            runtime_config=runtime_config,
        )
        try:
            dispatcher.dispatch(package, target_executor_id=runtime_binding.executor)
            canonical_result = _wait_for_result(dispatcher, canonical_task_id, timeout_seconds)
        finally:
            adapter = dispatcher.get_route(canonical_task_id)._adapter if dispatcher.has_route(canonical_task_id) else None
            if adapter is not None and hasattr(adapter, "_host_client"):
                host = adapter._host_client
                if hasattr(host, "close"):
                    host.close()

    if canonical_result.status != "completed":
        raise RuntimeError(f"real Hermes Worker did not complete: {canonical_result.to_dict()}")
    output = root / "runtime-smoke" / "output.txt"
    if not output.is_file() or output.read_text(encoding="utf-8") != expected:
        raise RuntimeError("real Hermes Worker did not produce the exact bounded output")

    governance = ResultGovernanceProjection.success()
    card = project_worker_result_card(
        canonical_result,
        governance,
        AgentWorkRole.CODER,
        summary="Real Hermes one-shot Worker completed the governed MCP smoke target.",
    )
    trace = tuple(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.is_file() else ()
    return WorkerSliceResult(
        handoff=handoff,
        package=package,
        runtime_binding=runtime_binding,
        canonical_result=canonical_result,
        governance_projection=governance,
        worker_result_card=card,
        tool_trace=trace,
    )


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--mcp-server":
        asyncio.run(_serve_mcp_child())
        return
    raise SystemExit(f"usage: {sys.argv[0]} --mcp-server")


if __name__ == "__main__":
    main()


__all__ = [
    "MCP_PROFILE_NAME",
    "MCP_SERVER_MODULE",
    "REAL_HERMES_VERSION",
    "WorkerSliceResult",
    "build_worker_binding",
    "run_one_shot_worker",
]
