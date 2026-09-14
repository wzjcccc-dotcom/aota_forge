"""AF #53 M2/W2 — Thin task-main host composition (side-by-side).

Composition-only seam that assembles the trusted mechanics the task-main host
needs on a thin trusted binding (``trusted_task_main_context=None``), so the
canonical generic ``task.start`` / ``task.return`` lifecycle (M2/W1) can run
without the legacy workflow coordinator as the normal-path brain:

    trusted operator/runtime inputs (worktree_root, project_id, worktree_id,
    runtime_config_path, exact origin session)
        -> canonical project binding + worktree sandbox (hard boundary)
        -> canonical operator RuntimeConfig (existing sole config authority)
        -> trusted production ExecutionDispatcher (durable store, exact origin)
        -> thin TrustedWorkerBinding (trusted_task_main_context=None)
        -> canonical single-entry ``aota.invoke`` dispatch (existing transport)
        -> task-main Role/Skill/tool guidance (existing role.bootstrap)
        -> completion delivery (existing DurableCompletionCoordinator over the
           existing Hermes exact-session transport)
        -> ThinTaskMainHost composition result (ready for non-live execution)

What this module deliberately does NOT do:

* it does not construct, require, import-through-execution or pass into the
  task-main binding: ``TaskMainControlService``, ``MilestonePlanView``,
  coordinator state, ``task_main.advance_once``, READY work calculation or
  review-transition state (``THIN_HOST_REQUIRES_*=no``);
* it does not read a Plan, decide the next Work Item, select a reviewer,
  decide repair or advance a Milestone — those are task-main LLM semantics;
* M3/W3 cutover: this thin host composition is now the production default
  path (``THIN_HOST_PRODUCTION_DEFAULT=yes``) because the canonical
  RuntimeConfig default resolves to ``thin``; the legacy host composition
  remains present and untouched as the explicit operator-selectable
  compatibility path (``LEGACY_HOST_PATH_PRESERVED=yes``,
  ``LEGACY_PATH_COMPATIBILITY_ONLY=yes``). Selection between legacy and thin
  is a trusted/internal composition choice owned by runtime construction,
  never a model-facing argument (``MODEL_AUTHORED_THIN_HOST_BINDING=no``);
* it creates no execution engine, authority engine, result ontology, session
  engine, workflow engine or generic plugin framework.

Completion delivery stays a factual signal
(``COMPLETION_DELIVERY_IS_FACTUAL_SIGNAL=yes``): terminal truth + exact
trusted parent session identity in, mechanical delivery out. It is never a
workflow decision (``COMPLETION_DELIVERY_IS_WORKFLOW_DECISION=no``).

Trust boundary: every identity field (project / worktree / runtime profile /
origin session / tool authority / completion route) is supplied by
server-side runtime construction and mechanically validated here. No
model-facing ``aota.invoke`` argument can provide or override them
(``HARD_PROJECT_BOUNDARY=yes``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
    create_production_execution_dispatcher,
)
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.composition.worker_vertical_slice import (
    create_governed_worker_env_resolver,
)
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    ExecutionStateStore,
    FileBackedExecutionStateStore,
    OriginSessionRef,
    is_bound_origin_session_ref,
)
from aota_forge.core.ingress import bind_execution_dispatcher
from aota_forge.mcp_transport import create_aota_invoke_dispatch
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.config import (
    TASK_MAIN_RUNTIME_PATH_THIN,
    RuntimeConfig,
    load_runtime_config,
)
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    TrustedWorkerBinding,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.restricted_shell import (
    RESTRICTED_SHELL_DESCRIPTOR,
    create_restricted_shell_authority,
)
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
)
from aota_forge.work_plane.tool_surface import ToolRoleSurface, create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_broad_workspace_read_authority,
)
from aota_forge.work_plane.worktree_sandbox import (
    WorktreeSandboxBoundary,
    bind_worktree_sandbox,
)

# ---------------------------------------------------------------------------
# Composition ownership and architecture markers
# ---------------------------------------------------------------------------

THIN_HOST_COMPOSITION_OWNER = "aota_forge/composition/thin_task_main_host.py"
THIN_HOST_COMPOSITION_KIND = "trusted_internal_composition"
THIN_HOST_ROLE = "task-main"

MODEL_AUTHORED_THIN_HOST_BINDING = False
THIN_HOST_PRODUCTION_DEFAULT = True
THIN_HOST_SIDE_BY_SIDE = True
LEGACY_HOST_PATH_PRESERVED = True
LEGACY_PATH_COMPATIBILITY_ONLY = True

# The legacy workflow brain is not constructed, required, imported-through-
# execution or passed into the thin task-main binding.
THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE = False
THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW = False
THIN_HOST_REQUIRES_COORDINATOR = False
THIN_HOST_REQUIRES_ADVANCE_ONCE = False
THIN_HOST_LEGACY_WORKFLOW_BRAIN_DEPENDENCY = False
CONTROL_PLANE_WORKFLOW_STRATEGY = "no"

# Completion delivery is a factual signal, not a workflow decision.
COMPLETION_DELIVERY_IS_FACTUAL_SIGNAL = True
COMPLETION_DELIVERY_IS_WORKFLOW_DECISION = False

# Origin/parent session identity is trusted runtime metadata.
THIN_HOST_ORIGIN_SESSION_FROM_TRUSTED_RUNTIME = True
MODEL_CAN_FORGE_PARENT_SESSION = False

# Progressive disclosure: a small bootstrap + on-demand reads, never a
# preloaded Plan/context manual.
PROGRESSIVE_DISCLOSURE_TASK_MAIN = True
TASK_MAIN_STARTUP_PRELOADS_PLAN = False
TASK_MAIN_CONTEXT_READ_ON_DEMAND = True

# Task-main visible surface: the generic common operations + ``task.start``.
# No workflow-special tools are added or exposed on the thin normal path.
THIN_TASK_MAIN_EAGER_OPERATIONS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "handoff.write",
    "handoff.open",
    "task.start",
)
THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS: tuple[str, ...] = (
    "result.hydrate",
    "restricted_shell.run",
)
WORKFLOW_SPECIAL_OPERATIONS: tuple[str, ...] = (
    "task.observe",
    "task.advance",
    "task.review",
    "task.repair",
    "workflow.next",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_ORIGIN_LEN = 512


def _validate_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustedBindingError(f"thin host {label} must be a non-empty string")
    candidate = value.strip()
    if not _SAFE_ID.fullmatch(candidate):
        raise TrustedBindingError(f"thin host {label} must be a bounded trusted identifier")
    return candidate


def _validate_origin_session(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustedBindingError("thin host origin task-main session ref must be non-empty")
    candidate = value.strip()
    if len(candidate) > _MAX_ORIGIN_LEN:
        raise TrustedBindingError("thin host origin task-main session ref exceeds bound")
    return candidate


def _validate_worktree_root(value: Any) -> Path:
    if not isinstance(value, (str, PathLike)):
        raise TrustedBindingError("thin host worktree_root must be a filesystem path")
    root = Path(value)
    try:
        if root.is_symlink():
            raise TrustedBindingError("thin host worktree_root must not be a symlink")
        root = root.resolve()
    except TrustedBindingError:
        raise
    except OSError as exc:
        raise TrustedBindingError(f"thin host worktree_root inaccessible: {exc}") from exc
    if not root.is_dir():
        raise TrustedBindingError(f"thin host worktree_root must be a directory: {root!r}")
    return root


def _resolve_sandbox(
    *, worktree_root: Path, project_id: str, worktree_id: str
) -> tuple[Any, WorktreeSandboxBoundary]:
    """Canonical project binding + trusted worktree sandbox (hard boundary)."""
    evidence = resolve_trusted_project_evidence(
        worktree_root=worktree_root,
        project_id=project_id,
    )
    candidates = tuple(getattr(evidence, "candidates", ()) or ())
    if getattr(evidence, "status", None) != "RESOLVED" or len(candidates) != 1:
        raise TrustedBindingError(
            f"thin host requires singular canonical project evidence for {project_id!r} "
            f"(status={getattr(evidence, 'status', None)!r}, candidates={len(candidates)})"
        )
    sandbox = bind_worktree_sandbox(
        evidence,
        worktree_id,
        worktree_root,
        expected_project_id=project_id,
    )
    return evidence, sandbox


def _task_main_control_handoff() -> TaskHandoff:
    """Trusted task-main identity handoff (no Plan/Milestone workflow fields)."""
    return TaskHandoff(
        work_role=THIN_HOST_ROLE,
        task_kind="task-main-host",
        objective="AOTA task-main thin host composition via aota.invoke",
        bounded_scope="task-main trusted host composition only",
        validation_expectations=("trusted host composition validation",),
        semantic_stop_expectations=("stop if trusted binding unavailable",),
    )


def _build_thin_tool_surface() -> ToolRoleSurface:
    return create_role_tool_surface(
        THIN_HOST_ROLE,
        eager=THIN_TASK_MAIN_EAGER_OPERATIONS,
        progressive=THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS,
    )


def _build_read_authorities(
    sandbox: WorktreeSandboxBoundary, handoff: TaskHandoff
) -> tuple[Any, ...]:
    return (
        create_broad_workspace_read_authority(
            sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=handoff, applicable_policies=()
        ),
        create_broad_workspace_read_authority(
            sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=()
        ),
    )


def _build_restricted_shell_authority(
    sandbox: WorktreeSandboxBoundary, handoff: TaskHandoff
) -> Any | None:
    try:
        return create_restricted_shell_authority(
            sandbox, handoff, (), RESTRICTED_SHELL_DESCRIPTOR
        )
    except Exception:
        # Residual terminal capability only; absence never blocks the host.
        return None


def _default_execution_store(worktree_root: Path) -> FileBackedExecutionStateStore:
    store_path = worktree_root / ".aota" / "execution.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if not store_path.exists():
        store_path.write_text("{}", encoding="utf-8")
        try:
            store_path.chmod(0o600)
        except Exception:
            pass
    return FileBackedExecutionStateStore(store_path)


@dataclass(frozen=True)
class ThinTaskMainHost:
    """Trusted thin task-main host composition result (non-live ready).

    Mechanical carrier of already-authoritative objects only. It decides no
    workflow strategy, holds no review/repair/next-milestone state and owns
    no execution/authority/result/session engine.
    """

    project_id: str
    worktree_id: str
    worktree_root: Path
    project_evidence: Any
    sandbox: WorktreeSandboxBoundary
    runtime_config: RuntimeConfig
    runtime_config_path: Path
    origin_task_main_session_ref: str
    canonical_task_id: str
    trusted_binding: TrustedWorkerBinding
    tool_surface: ToolRoleSurface
    execution_store: ExecutionStateStore
    execution_dispatcher: ExecutionDispatcher
    completion_coordinator: DurableCompletionCoordinator
    completion_transport: Any
    aota_invoke: Callable[..., Any]

    @property
    def origin_session_is_bound(self) -> bool:
        """True for a real exact origin session (never the pre-session placeholder)."""
        return is_bound_origin_session_ref(self.origin_task_main_session_ref)

    @property
    def session_profile(self) -> str:
        """Trusted task-main Hermes profile from the operator RuntimeConfig."""
        return self.runtime_config.get_binding(THIN_HOST_ROLE).profile

    @property
    def session_executable(self) -> str:
        """Trusted Hermes executable from the operator RuntimeConfig."""
        return self.runtime_config.executable

    def invoke(self, operation: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Canonical single-entry ``aota.invoke`` over the thin trusted binding."""
        return self.aota_invoke(operation, dict(arguments) if arguments is not None else None)

    def role_guidance(self) -> dict[str, Any]:
        """Task-main Role / Skill / tool guidance via existing ``role.bootstrap``.

        Advisory only (``SKILL_IS_AUTHORITY=no``): it describes capabilities and
        operating behavior; it encodes no review frequency/order, Work sequence
        or repair strategy.
        """
        from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

        return handle_role_bootstrap(self.trusted_binding, {})

    def create_mcp_server(self) -> Any:
        """Existing single-entry MCP server over this host's trusted binding."""
        from aota_forge.mcp_transport import create_shared_mcp_server

        return create_shared_mcp_server(self.trusted_binding)


def compose_thin_task_main_host(
    *,
    worktree_root: str | PathLike[str],
    project_id: str,
    worktree_id: str,
    runtime_config_path: str | PathLike[str],
    origin_task_main_session_ref: str,
    execution_store: ExecutionStateStore | None = None,
    host_client: Any | None = None,
    host_client_factory: Callable[..., Any] | None = None,
    completion_transport: Any | None = None,
) -> ThinTaskMainHost:
    """Compose the trusted thin task-main host (side-by-side, non-live ready).

    All identity inputs are trusted runtime/operator construction inputs; the
    model never supplies or overrides them. The composition never consults a
    Plan, never constructs the legacy workflow coordinator, and leaves the
    production default path untouched.
    """
    pid = _validate_identifier(project_id, "project_id")
    wid = _validate_identifier(worktree_id, "worktree_id")
    root = _validate_worktree_root(worktree_root)
    origin = _validate_origin_session(origin_task_main_session_ref)

    config_path = Path(runtime_config_path)
    if not config_path.is_file():
        raise TrustedBindingError(f"thin host runtime config missing: {config_path!r}")
    config_path = config_path.resolve()
    runtime_config = load_runtime_config(config_path=str(config_path))
    if not isinstance(runtime_config, RuntimeConfig):
        raise TrustedBindingError("thin host requires the canonical RuntimeConfig authority")

    project_evidence, sandbox = _resolve_sandbox(
        worktree_root=root, project_id=pid, worktree_id=wid
    )

    store: ExecutionStateStore = (
        execution_store if execution_store is not None else _default_execution_store(root)
    )

    # AF #53 M3/W2-R1 (I53-B001): the thin production dispatcher is composed
    # with the canonical governed Worker env resolver (trusted server-side
    # runtime source). The physically spawned Worker therefore receives the
    # canonical pre-resolved binding envelope built from the SAME grounded
    # durable handoff that ``task.start`` resolved. Without a valid resolver a
    # governed Worker dispatch fails closed before any physical spawn.
    worker_env_resolver = create_governed_worker_env_resolver(
        sandbox=sandbox,
        runtime_config_path=config_path,
    )

    dispatcher_kwargs: dict[str, Any] = {
        "default_cwd": root,
        "runtime_config": runtime_config,
        "state_store": store,
        "origin_session_ref": OriginSessionRef(value=origin),
        "worker_env_resolver": worker_env_resolver,
    }
    if host_client is not None:
        dispatcher_kwargs["host_client"] = host_client
    if host_client_factory is not None:
        dispatcher_kwargs["host_client_factory"] = host_client_factory
    dispatcher = create_production_execution_dispatcher(**dispatcher_kwargs)
    # Bind the existing canonical Core ingress dispatcher seam (same seam the
    # legacy host bootstrap uses) so the thin task.start/task.return lifecycle
    # resolves the real production dispatcher above.
    bind_execution_dispatcher(dispatcher)

    transport = (
        completion_transport
        if completion_transport is not None
        else create_hermes_completion_delivery_transport(runtime_config=runtime_config)
    )
    semantic_return_provider = WorktreeSemanticReturnEvidenceProvider(sandbox)
    completion_coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=runtime_config,
        transport=transport,
        semantic_return_provider=semantic_return_provider,
    )

    handoff = _task_main_control_handoff()
    tool_surface = _build_thin_tool_surface()
    read_authorities = _build_read_authorities(sandbox, handoff)
    restricted_shell_authority = _build_restricted_shell_authority(sandbox, handoff)

    binding = TrustedWorkerBinding(
        canonical_task_id=f"{pid}:task-main:{origin[:8]}",
        project_id=pid,
        worktree_id=wid,
        trusted_context=bind_trusted_context(
            principal_id=THIN_HOST_ROLE,
            principal_type=THIN_HOST_ROLE,
            channel="mcp",
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=None,
        restricted_shell_authority=restricted_shell_authority,
        # The thin seam: no TrustedTaskMainRuntimeContext => M2/W1 generic thin
        # task lifecycle; no legacy workflow object is required or passed.
        trusted_task_main_context=None,
        # AF #53 M3/W1 explicit trusted classification (mechanical marker).
        task_main_runtime_path=TASK_MAIN_RUNTIME_PATH_THIN,
    )
    aota_invoke = create_aota_invoke_dispatch(binding)

    return ThinTaskMainHost(
        project_id=pid,
        worktree_id=wid,
        worktree_root=root,
        project_evidence=project_evidence,
        sandbox=sandbox,
        runtime_config=runtime_config,
        runtime_config_path=config_path,
        origin_task_main_session_ref=origin,
        canonical_task_id=binding.canonical_task_id,
        trusted_binding=binding,
        tool_surface=tool_surface,
        execution_store=store,
        execution_dispatcher=dispatcher,
        completion_coordinator=completion_coordinator,
        completion_transport=transport,
        aota_invoke=aota_invoke,
    )


__all__ = [
    "THIN_HOST_COMPOSITION_OWNER",
    "THIN_HOST_COMPOSITION_KIND",
    "THIN_HOST_ROLE",
    "MODEL_AUTHORED_THIN_HOST_BINDING",
    "THIN_HOST_PRODUCTION_DEFAULT",
    "THIN_HOST_SIDE_BY_SIDE",
    "LEGACY_HOST_PATH_PRESERVED",
    "LEGACY_PATH_COMPATIBILITY_ONLY",
    "THIN_HOST_REQUIRES_TASK_MAIN_CONTROL_SERVICE",
    "THIN_HOST_REQUIRES_MILESTONE_PLAN_VIEW",
    "THIN_HOST_REQUIRES_COORDINATOR",
    "THIN_HOST_REQUIRES_ADVANCE_ONCE",
    "THIN_HOST_LEGACY_WORKFLOW_BRAIN_DEPENDENCY",
    "CONTROL_PLANE_WORKFLOW_STRATEGY",
    "COMPLETION_DELIVERY_IS_FACTUAL_SIGNAL",
    "COMPLETION_DELIVERY_IS_WORKFLOW_DECISION",
    "THIN_HOST_ORIGIN_SESSION_FROM_TRUSTED_RUNTIME",
    "MODEL_CAN_FORGE_PARENT_SESSION",
    "PROGRESSIVE_DISCLOSURE_TASK_MAIN",
    "TASK_MAIN_STARTUP_PRELOADS_PLAN",
    "TASK_MAIN_CONTEXT_READ_ON_DEMAND",
    "THIN_TASK_MAIN_EAGER_OPERATIONS",
    "THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS",
    "WORKFLOW_SPECIAL_OPERATIONS",
    "ThinTaskMainHost",
    "compose_thin_task_main_host",
]
