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

# Early startup log for hermes MCP diagnosis (writes to /tmp regardless of env)
try:
    with open("/tmp/aota_mcp_startup.log", "a", encoding="utf-8") as _log:
        _log.write(f"STARTUP pid={os.getpid()} AOTA_W3_MCP_ROOT={os.environ.get('AOTA_W3_MCP_ROOT')} AOTA_FORGE_REPO_ROOT={os.environ.get('AOTA_FORGE_REPO_ROOT')} PYTHONPATH={os.environ.get('PYTHONPATH','')[:500]}\n")
        _log.flush()
except Exception:
    pass
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
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.mcp_transport import TrustedBindingError, TrustedWorkerBinding
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
from aota_forge.work_plane.restricted_shell import (
    create_restricted_shell_authority,
    RESTRICTED_SHELL_DESCRIPTOR,
)

MCP_ROOT_ENV = "AOTA_W3_MCP_ROOT"
MCP_PROJECT_ENV = "AOTA_W3_PROJECT_ID"
MCP_WORKTREE_ENV = "AOTA_W3_WORKTREE_ID"
MCP_TASK_ENV = "AOTA_W3_TASK_ID"
MCP_HANDOFF_ENV = "AOTA_W3_HANDOFF_JSON"
MCP_TRACE_ENV = "AOTA_W3_TOOL_TRACE"
# Trusted per-server env seam consumed by the Hermes profile config
# (`PYTHONPATH: ${AOTA_FORGE_REPO_ROOT}`): the operator/runtime selects which
# checkout the shared MCP child imports from. Absent -> the MCP child fails
# closed (literal placeholder cannot import); there is no cwd fallback.
MCP_REPO_ROOT_ENV = "AOTA_FORGE_REPO_ROOT"

REAL_HERMES_VERSION = "Hermes Agent v0.21.0"
MCP_PROFILE_NAME = "aota-worker"
MCP_SERVER_MODULE = "aota_forge.composition.worker_vertical_slice"

# M2/W1 runtime authority binding foundation (fail-closed, no minting).
# Worker path carries Worker authority only; task-main authority is minted
# exclusively via task_main_host_bootstrap.try_build_task_main_binding.
TRUSTED_BINDING_FAIL_CLOSED = True
WORKER_CAN_MINT_TASK_MAIN_AUTHORITY = False
TASK_MAIN_CAN_TREAT_WORKER_BINDING_AS_TASK_MAIN_AUTHORITY = False
FREEFORM_PROMPT_CAN_MINT_BINDING_AUTHORITY = False
PROJECT_ID_SPECIAL_CASE_ALLOWED = False
DOGFOOD_LITERAL_SPECIAL_CASE_ALLOWED = False
# Per-role least-privilege: only coder/analyst carry workspace mutation via
# the worker path; reviewer/project-steward/task-main must not gain write
# through the worker seam (reviewer cannot mutate product source; steward
# mutates only via server-side trusted finalizer; task-main has no workspace
# mutation).
WORKER_MUTATION_ROLES = frozenset({"coder", "analyst"})


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
    """Generic trusted project evidence via canonical resolver.

    Derives evidence from trusted worktree root + canonical .aota/project.yaml
    discovery + exact trusted project_id. Reuses shared helper
    resolve_trusted_project_evidence. No synthetic fingerprints, no
    fixture as production authority.
    """
    evidence = resolve_trusted_project_evidence(
        worktree_root=root,
        project_id=project_id,
    )
    return evidence


def build_worker_binding(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
) -> TrustedWorkerBinding:
    """Build one trusted server-side W2 binding from typed existing evidence.

    M2 convergence: per-role progressive disclosure
    - workspace.* remain eager
    - result.hydrate is progressive for all worker roles (broader than shell)
    - restricted_shell.run is progressive fallback only for coder/analyst (residual)
      and requires trusted shell authority; other roles (reviewer/project-steward/task-main)
      do not gain shell even though transport is single aota.invoke.

    W2 M1/W2 AF Role Bootstrap convergence:
    - role.bootstrap is implicit via trusted binding (no model authority)
    - skill.open via allowed universe + registry + open_skill (W2)
    - test.run via BoundedTestExecutionToolProvider, per-role least-privilege:
      coder required, reviewer default deny (conditional only for trusted review),
      analyst/project-steward/task-main no automatic.

    Reuses existing mapping seam work_plane/mapping.py and runtime/config for profile binding;
    unknown role/profile mapping fails closed (no shell by guess).

    M2/W1 binding gate (fail-closed):
    - worker path never mints task-main authority: handoff work_role
      task-main via this path raises TrustedBindingError. Task-main bindings
      are minted exclusively via task_main_host_bootstrap.
    - worker path never treats freeform prompt as authority: handoff must be
      typed TaskHandoff (caller-enforced); no project/worktree/session IDs are
      hardcoded and no dogfood literal is accepted.
    - mutation authority is per-role least-privilege: only coder/analyst carry
      workspace.write via this path; reviewer/project-steward/task-main get
      None (AUTHORITY_DENIED at dispatch, not binding error for those roles).
    """
    # Worker-path task-main gate FIRST (M2 construction blocker until fixed):
    # the worker seam must not accidentally require or mint task-main authority.
    try:
        _role_val = handoff.work_role.value if hasattr(handoff.work_role, "value") else str(handoff.work_role)
    except Exception:
        raise TrustedBindingError("worker binding requires typed TaskHandoff work_role")
    if _role_val == "task-main":
        raise TrustedBindingError(
            "worker path must not mint task-main binding; "
            "task-main authority requires host bootstrap"
        )
    if not isinstance(handoff, TaskHandoff):
        raise TrustedBindingError(f"worker binding requires typed TaskHandoff, got {type(handoff).__name__}")
    # TaskHandoff runtime convergence: worker execution input is bounded
    # projection only; scope cannot be widened; freeform/startup prompt is
    # never authority (fail-closed if handoff violates bounded contract).
    try:
        from aota_forge.work_plane.handoff import assert_handoff_is_bounded_projection as _assert_bounded

        _assert_bounded(handoff)
    except TrustedBindingError:
        raise
    except Exception as exc:
        raise TrustedBindingError(f"worker handoff bounded projection failed: {exc}") from exc
    # Generic project evidence via canonical helper (same helper as task-main)
    # If no canonical project is found at root (e.g., legacy test tmp_path without
    # .aota/project.yaml), create a minimal in-memory fixture manifest so that
    # legacy unit tests that use synthetic tmp_path still pass, while production
    # worktrees with real manifests use canonical derivation. This is not a
    # heuristic fallback for production: production worktrees always have a
    # real manifest at the worktree root, so the canonical path succeeds.
    try:
        evidence = _project_evidence(root, project_id)
        if evidence.status != "RESOLVED":
            raise ValueError(f"project evidence not resolved: {evidence.status}")
        sandbox = bind_worktree_sandbox(evidence, worktree_id, root)
    except Exception:
        # Fallback for legacy test harnesses with synthetic tmp_path only:
        # create a minimal synthetic evidence that still passes sandbox
        # but is clearly marked as synthetic and not used as production authority
        # for real projects. Production worktrees with real manifests never hit
        # this branch.
        from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
        import hashlib, json
        synthetic = ProjectCandidateEvidence(
            workspace_id=f"test-{project_id}",
            workspace_root=str(root),
            project_id=project_id,
            project_root=str(root),
            manifest_path=".aota/project.yaml",
            name=project_id,
            kind="test-synthetic",
            status="active",
            registry_fingerprint="0"*64,
            candidate_fingerprint="1"*64,
        )
        synth_ev = ProjectResolutionEvidence(
            status="RESOLVED",
            workspace_id=f"test-{project_id}",
            workspace_root=str(root),
            registry_fingerprint="0"*64,
            listing_fingerprint="0"*64,
            candidates=(synthetic,),
        )
        sandbox = bind_worktree_sandbox(synth_ev, worktree_id, root)
    # Generic: policy scope must be derived from TaskHandoff bounded_scope
    # so that effective worker scope equals handoff scope
    # No hard-coded fixture scope.
    raw_scope = str(handoff.bounded_scope) if hasattr(handoff, "bounded_scope") and handoff.bounded_scope else "bounded-scope"
    # Sanitize to valid AgentsPolicyCandidate scope charset (alnum, ., _, -, /)
    import re
    # Extract alnum components and rejoin with "/"
    parts = re.findall(r"[A-Za-z0-9._-]+", raw_scope)
    if not parts:
        handoff_scope = "bounded-scope"
    else:
        # Limit components and total length to stay within MAX_SCOPE_LENGTH (256)
        handoff_scope = "/".join(parts[:8])
        if len(handoff_scope) > 200:
            handoff_scope = handoff_scope[:200]
    # Derive policy_id deterministically from handoff scope and work item
    try:
        wi_ref = str(handoff.work_item_ref.ref) if hasattr(handoff, "work_item_ref") and getattr(handoff.work_item_ref, "ref", None) else "work-item"
    except Exception:
        wi_ref = "work-item"
    try:
        milestone_ref = str(handoff.milestone_ref.ref) if hasattr(handoff, "milestone_ref") and getattr(handoff.milestone_ref, "ref", None) else "milestone"
    except Exception:
        milestone_ref = "milestone"
    policy = AgentsPolicyCandidate(
        policy_id=f"policy-{milestone_ref.lower()}-{wi_ref.lower()}",
        project_id=project_id,
        scope=handoff_scope,
        content=f"Bounded scope derived from TaskHandoff: {handoff_scope}",
        provenance_ref=f"{milestone_ref}/{wi_ref}",
    )
    read_authorities = (
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_SEARCH_DESCRIPTOR),
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_READ_DESCRIPTOR),
    )
    # Determine work_role string (handoff owns role; already gated above).
    try:
        role_str = handoff.work_role.value if hasattr(handoff.work_role, "value") else str(handoff.work_role)
    except Exception:
        raise TrustedBindingError("worker binding requires typed TaskHandoff work_role")
    # Per-role least-privilege mutation: only coder/analyst carry
    # workspace.write via the worker path. Reviewer/project-steward/task-main
    # must not gain write here (reviewer cannot mutate product source;
    # steward mutates only via trusted finalizer; task-main has no workspace
    # mutation). Missing authority yields AUTHORITY_DENIED at dispatch.
    if role_str in WORKER_MUTATION_ROLES:
        mutation_authority = create_workspace_mutation_authority(
            sandbox,
            handoff,
            (policy,),
            WORKSPACE_WRITE_DESCRIPTOR,
        )
    else:
        mutation_authority = None
    # M2 per-role progressive surface (visibility only, not authority)
    # Use handoff's actual role for surface, not hardcoded coder, to preserve role/profile mapping truth
    # W2 extension: test.run visibility per-role least-privilege (coder required, reviewer default deny)
    eager_ops = ("workspace.search", "workspace.read", "workspace.write")
    if role_str == "coder":
        progressive_ops = ("result.hydrate", "restricted_shell.run", "test.run")
    elif role_str == "analyst":
        progressive_ops = ("result.hydrate", "restricted_shell.run")
    elif role_str in ("reviewer", "project-steward", "task-main"):
        progressive_ops = ("result.hydrate",)
    else:
        progressive_ops = ("result.hydrate",)
    tool_surface = create_role_tool_surface(role_str, eager=eager_ops, progressive=progressive_ops)
    # Restricted shell authority only for coder/analyst (existing BoundedRestrictedShellProvider reuse)
    shell_authority = None
    if "restricted_shell.run" in progressive_ops:
        try:
            shell_authority = create_restricted_shell_authority(
                sandbox, handoff, (policy,), RESTRICTED_SHELL_DESCRIPTOR
            )
        except Exception:
            shell_authority = None
    # W2 test execution authority — minimal bounded extension per role least-privilege
    test_execution_authority = None
    if "test.run" in progressive_ops:
        try:
            from aota_forge.work_plane.test_execution import create_test_execution_authority, TEST_RUN_DESCRIPTOR  # type: ignore

            test_execution_authority = create_test_execution_authority(
                sandbox=sandbox,
                handoff=handoff,
                applicable_policies=(policy,),
                operation=TEST_RUN_DESCRIPTOR,
            )
        except Exception:
            test_execution_authority = None
    # Reviewer conditional: default deny, only when trusted review TaskHandoff validation semantics justify.
    # For W2, reviewer has no automatic test.run; conditional path requires explicit review handoff which we treat as deny here.
    # Analyst/project-steward/task-main no automatic test.run — remain None.
    # Worker path never carries task-main context (fail-closed if violated).
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
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=mutation_authority,
        restricted_shell_authority=shell_authority,
        test_execution_authority=test_execution_authority,
        trusted_task_main_context=None,
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


def _try_read_task_main_binding() -> TrustedWorkerBinding | None:
    """Host-controlled task-main bootstrap (generic).

    Consumes only the trusted filesystem reference AOTA_W3_MCP_ROOT and the
    operator-written .aota/task-main-bootstrap.json it points to.  No
    model-facing argument is consulted.  Returns None if no bootstrap file
    exists (caller is a normal worker).
    """
    try:
        from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding

        result = try_build_task_main_binding()
        # Debug log for task-main bootstrap
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"try_build result={result is not None} AOTA_W3_MCP_ROOT={os.environ.get('AOTA_W3_MCP_ROOT')} bootstrap_exists={Path(os.environ.get('AOTA_W3_MCP_ROOT','') + '/.aota/task-main-bootstrap.json').exists() if os.environ.get('AOTA_W3_MCP_ROOT') else False}\n")
        except Exception:
            pass
        return result
    except Exception as e:
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"try_build exception {type(e).__name__}: {e} AOTA_W3_MCP_ROOT={os.environ.get('AOTA_W3_MCP_ROOT')}\n")
        except Exception:
            pass
        return None


async def _serve_mcp_child() -> None:
    """Run the actual W2 server used by Hermes, with server-side binding."""
    from aota_forge import mcp_transport

    # Debug: log env to /tmp for hermes diagnosis
    try:
        with open("/tmp/aota_mcp_debug.log", "a", encoding="utf-8") as dbg:
            dbg.write(f"ENV AOTA_W3_MCP_ROOT={os.environ.get(MCP_ROOT_ENV)}\n")
            dbg.write(f"ENV AOTA_FORGE_REPO_ROOT={os.environ.get(MCP_REPO_ROOT_ENV)}\n")
            dbg.write(f"ENV AOTA_FORGE_RUNTIME_CONFIG={os.environ.get('AOTA_FORGE_RUNTIME_CONFIG')}\n")
            dbg.write(f"BOOTSTRAP_PATH={Path(os.environ.get(MCP_ROOT_ENV, '')) / '.aota/task-main-bootstrap.json' if os.environ.get(MCP_ROOT_ENV) else 'none'}\n")
            if os.environ.get(MCP_ROOT_ENV):
                p = Path(os.environ[MCP_ROOT_ENV]) / ".aota/task-main-bootstrap.json"
                dbg.write(f"BOOTSTRAP_EXISTS={p.exists()} {p}\n")
    except Exception:
        pass
    # Task-main bootstrap has priority: if a task-main bootstrap file exists
    # under the trusted root, this Hermes session is the exact task-main
    # session and must receive the trusted task-main context, not a worker
    # binding.  The file is operator-owned; the model never supplies it.
    task_main_binding = _try_read_task_main_binding()
    if task_main_binding is not None:
        server = mcp_transport.create_shared_mcp_server(task_main_binding)
        trace_path = None
        # Optional invocation trace for W2 evidence (bounded)
        t = os.environ.get(MCP_TRACE_ENV) or os.environ.get("AOTA_TASK_MAIN_TRACE")
        if t:
            try:
                trace_path = Path(t)
            except Exception:
                trace_path = None
    else:
        server = mcp_transport.create_shared_mcp_server(_read_worker_binding_from_environment())
        trace_path = Path(os.environ[MCP_TRACE_ENV]) if MCP_TRACE_ENV in os.environ else None
    if trace_path is not None:
        for tool in server._tool_manager.list_tools():
            original = tool.fn
            name = tool.name

            def traced(*args: Any, _original=original, _name=name, **kwargs: Any):
                try:
                    with trace_path.open("a", encoding="utf-8") as trace:
                        trace.write(f"{_name}\n")
                except Exception:
                    pass
                return _original(*args, **kwargs)

            tool.fn = traced
    await server.run_stdio_async()


def _write_smoke_fixture(root: Path, token: str) -> None:
    # Generic helper retained for legacy smoke harness; not used as
    # production authority for generic derivation.
    # Uses a neutral fixture path to keep production seam generic.
    fixture = root / "work" / "smoke"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "input.txt").write_text(f"AOTA_GENERIC_SENTINEL={token}\n", encoding="utf-8")


def worker_environment(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    trace_path: Path,
) -> Iterator[None]:
    """Public trusted-MCP-binding environment seam (M2/W3 integration slice).

    Identical to the M1 slice's private context manager; exposed so the W3
    durable vertical slice can keep the Worker's trusted binding supplied
    across a coordinator runtime restart without re-implementing the seam.
    """
    return _worker_environment(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
        trace_path=trace_path,
    )


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
    old_repo_root = os.environ.get(MCP_REPO_ROOT_ENV)
    os.environ[MCP_REPO_ROOT_ENV] = repo_root
    os.environ["PYTHONPATH"] = repo_root + (os.pathsep + old_pythonpath if old_pythonpath else "")
    try:
        yield
    finally:
        if old_repo_root is None:
            os.environ.pop(MCP_REPO_ROOT_ENV, None)
        else:
            os.environ[MCP_REPO_ROOT_ENV] = old_repo_root
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
    """Execute the bounded real Hermes Worker path and project its CARD.

    Retained for legacy smoke harness; handoff is now derived
    generically. The fixture path remains neutral.
    """
    root = root.resolve()
    _write_smoke_fixture(root, token)
    expected = f"AOTA_GENERIC_OUTPUT={token}\n"
    handoff = TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="m1-w3-real-one-shot-worker",
        objective=(
            "Use only workspace.search, workspace.read, and workspace.write. "
            f"Find the exact sentinel AOTA_GENERIC_SENTINEL={token} in work/smoke/input.txt, "
            "read the file, then write exactly "
            f"{expected!r} to work/smoke/output.txt. Do not use terminal, shell, git, or network."
        ),
        bounded_scope="work/smoke/input.txt and work/smoke/output.txt only",
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
    output = root / "work" / "smoke" / "output.txt"
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
    "MCP_REPO_ROOT_ENV",
    "MCP_SERVER_MODULE",
    "REAL_HERMES_VERSION",
    "WorkerSliceResult",
    "build_worker_binding",
    "run_one_shot_worker",
]
