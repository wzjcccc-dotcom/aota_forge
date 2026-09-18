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

import importlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
    create_opencode_completion_delivery_transport,
    create_production_execution_dispatcher,
)
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.composition.plan_authority import (
    PlanAuthorityCompositionError,
    compose_plan_authority_binding,
    resolve_bound_plan_authority,
)
from aota_forge.composition.project_binding import (
    resolve_trusted_project_binding,
    resolve_trusted_project_evidence,
)
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
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.ingress import bind_execution_dispatcher
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.mcp_transport import create_aota_invoke_dispatch
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.config import (
    EXECUTOR_OPENCODE,
    TASK_MAIN_RUNTIME_PATH_THIN,
    RuntimeConfig,
    load_runtime_config,
)
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    TrustedWorkerBinding,
)
from aota_forge.work_plane.github_tools import (
    GITHUB_ISSUE_COMMENT_UPDATE_DESCRIPTOR,
    GITHUB_ISSUE_COMMENTS_READ_DESCRIPTOR,
    GITHUB_ISSUE_READ_DESCRIPTOR,
    GITHUB_ISSUE_UPDATE_DESCRIPTOR,
    TrustedPlanGitHubBinding,
    create_github_authority,
)
from aota_forge.work_plane.git_tools import (
    GIT_CHECKPOINT_DESCRIPTOR,
    GIT_DIFF_DESCRIPTOR,
    GIT_INTEGRATE_DESCRIPTOR,
    GIT_PUSH_DESCRIPTOR,
    GIT_STATUS_DESCRIPTOR,
    create_git_authority,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.authorized_roots import (
    AuthorizedRootSet,
    LocalGovernanceRootBinding,
    authorized_roots_for_task_main,
    build_trusted_project_context_projection,
)
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

# Task-main visible surface: the generic common operations + ``task.start``
# + the bounded Git reads (AF #54 M5/W1) + the bound-Plan GitHub reads
# (AF #54 M5/W2). No workflow-special tools are added or exposed on the thin
# normal path. Exposure is visibility only; mutation/lifecycle operations are
# granted only through trusted authority evidence minted at composition time.
THIN_TASK_MAIN_EAGER_OPERATIONS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "handoff.write",
    "handoff.open",
    "task.start",
    "git.status",
    "git.diff",
    "github.issue.read",
    "github.issue.comments.read",
)
THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS: tuple[str, ...] = (
    "result.hydrate",
    "restricted_shell.run",
    "git.checkpoint",
    "git.integrate",
    "git.push",
    "github.issue.update",
    "github.issue.comment.update",
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
    *,
    worktree_root: Path,
    project_id: str,
    worktree_id: str,
    source_repository: str | None = None,
    registry_path: str | PathLike[str] | None = None,
) -> tuple[Any, WorktreeSandboxBoundary, dict[str, Any]]:
    """Canonical project binding + trusted worktree sandbox (hard boundary).

    AF #55 M1/W4: when the trusted operator/runtime construction supplies a
    workspace registry and/or a Plan SOURCE_REPOSITORY, resolution goes
    through ``resolve_trusted_project_binding`` — registry-backed
    deterministic project_id resolution + mechanical Git origin grounding.
    No cwd inference, no folder-name inference, no model path authority.

    Without registry/SOURCE_REPOSITORY inputs the existing worktree-scan
    canonical path is preserved (legacy compatibility); it is still the same
    canonical resolver, never a second one.
    """
    trusted_context: dict[str, Any] = {
        "source_repository": "",
        "repository_identity_verified": False,
    }
    if source_repository is not None or registry_path is not None:
        binding = resolve_trusted_project_binding(
            project_id=project_id,
            source_repository=source_repository,
            registry_path=registry_path,
        )
        evidence = binding.resolution
        trusted_context = {
            "source_repository": binding.source_repository,
            "repository_identity_verified": binding.repository_identity is not None,
            "repository_identity": binding.repository_identity,
        }
    else:
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
    return evidence, sandbox, trusted_context


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
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    authorized_roots: AuthorizedRootSet,
) -> tuple[Any, ...]:
    return (
        create_broad_workspace_read_authority(
            sandbox, WORKSPACE_SEARCH_DESCRIPTOR, handoff=handoff, applicable_policies=(),
            authorized_roots=authorized_roots,
        ),
        create_broad_workspace_read_authority(
            sandbox, WORKSPACE_READ_DESCRIPTOR, handoff=handoff, applicable_policies=(),
            authorized_roots=authorized_roots,
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


def _build_git_authorities(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    *,
    git_integration_branch: str | None = None,
    git_remote: str | None = None,
) -> tuple[Any, ...]:
    """AF #54 M5/W1: mint the bounded trusted Git authorities for task-main.

    Reads (git.status/git.diff) are always minted. The lifecycle mutation
    family is minted only when the trusted operator runtime explicitly
    configured the integration branch (and remote for push); unconfigured
    operations have no authority and fail closed at dispatch. The model can
    never supply or widen these mechanical facts.
    """
    authorities: list[Any] = [
        create_git_authority(sandbox, handoff, (), GIT_STATUS_DESCRIPTOR),
        create_git_authority(sandbox, handoff, (), GIT_DIFF_DESCRIPTOR),
    ]
    branch = str(git_integration_branch).strip() if git_integration_branch else ""
    remote = str(git_remote).strip() if git_remote else ""
    if branch:
        authorities.append(
            create_git_authority(
                sandbox, handoff, (), GIT_CHECKPOINT_DESCRIPTOR, integration_branch=branch
            )
        )
        authorities.append(
            create_git_authority(
                sandbox, handoff, (), GIT_INTEGRATE_DESCRIPTOR, integration_branch=branch
            )
        )
        if remote:
            authorities.append(
                create_git_authority(
                    sandbox,
                    handoff,
                    (),
                    GIT_PUSH_DESCRIPTOR,
                    integration_branch=branch,
                    remote_name=remote,
                )
            )
    return tuple(authorities)


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
    # AF #54 M5/W2: the trusted bound-Plan reference this host was launched
    # with ("" when no Plan was bound at launch). Mechanical identity fact
    # for guidance/observation; never authority by itself.
    plan_ref: str = ""
    # AF #55 M1/W4: trusted Plan project identity grounding. Mechanical
    # identity facts only (never authority by themselves); M2 owns the full
    # multi-root bootstrap projection.
    source_repository: str = ""
    repository_identity_verified: bool = False
    repository_identity: dict[str, Any] | None = None
    # AF #55 M2: the task-main authorized root set (project-main read +
    # active-worktree read/write) and the §8 trusted context projection that
    # separates Plan authority, implementation project and active worktree.
    authorized_roots: AuthorizedRootSet | None = None
    context_projection: dict[str, Any] | None = None
    # AF #57 M1/W2: the trusted project-scoped local-governance binding this
    # host activated (None when the operator configured no governance base).
    # Mechanical carrier only: the binding grants the task-main read/search
    # root; it is never model-visible and never a write capability.
    local_governance_binding: LocalGovernanceRootBinding | None = None
    # AF #57 M3/W4: trusted project-scoped evidence binding activated for this
    # task-main launch. It is never model-visible and never a write capability.
    evidence_binding: Any | None = None
    # AF #57 M1/W4: the one source-neutral PlanAuthorityBinding this launch
    # materialized (None for a plan-less legacy launch). Mechanical carrier
    # only: it decides no policy and grants no authority by itself.
    plan_authority_binding: PlanAuthorityBinding | None = None
    # AF #57 M3/W1: trusted prebuilt Governance-context projection carried
    # into the existing role.bootstrap composition. It is derived context,
    # never authority and never model-supplied.
    governance_context: Mapping[str, Any] | None = None
    # AF #57 M3/W1: optional resolved authority read boundary. It is populated
    # only when trusted durable lookup inputs were explicitly supplied.
    plan_authority_reader: Any | None = None
    governance_store: Any | None = None

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

        return handle_role_bootstrap(
            self.trusted_binding,
            {},
            governance_context=self.trusted_binding.governance_context,
        )

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
    git_integration_branch: str | None = None,
    git_remote: str | None = None,
    plan_ref: str | None = None,
    source_repository: str | None = None,
    registry_path: str | PathLike[str] | None = None,
    governance_base: str | PathLike[str] | None = None,
    evidence_base: str | PathLike[str] | None = None,
    plan_id: str | None = None,
    governance_context: Mapping[str, Any] | None = None,
    governance_store_path: str | PathLike[str] | None = None,
    plan_authority_reader: Any | None = None,
) -> ThinTaskMainHost:
    """Compose the trusted thin task-main host (side-by-side, non-live ready).

    All identity inputs are trusted runtime/operator construction inputs; the
    model never supplies or overrides them. The composition never consults a
    Plan, never constructs the legacy workflow coordinator, and leaves the
    production default path untouched.

    AF #55 M1/W4: ``source_repository`` and ``registry_path`` are trusted
    operator/runtime inputs (Plan project identity + authorized workspace
    registry). When supplied, project resolution becomes registry-backed with
    mechanical Git origin verification; when absent the existing trusted
    worktree-scan resolution is preserved.

    AF #57 M1/W2: ``governance_base`` is the trusted operator governance base
    directory (for example ``<workspace>/plans``). When supplied, the host
    activates the project-scoped ``local-governance`` read/search root for
    exactly ``<governance-base>/<canonical project id>/``; the base itself and
    every sibling project scope stay unreachable, and no write capability is
    granted. When absent, the accepted #55 root set is unchanged.

    AF #57 M1/W4: ``plan_id`` is the trusted internal Plan identity (never a
    model argument, never inferred). When supplied, the host materializes
    exactly one source-neutral ``PlanAuthorityBinding`` for the launch: the
    existing GitHub ``plan_ref`` (``github_issue``) or an active trusted
    local-governance root (``local_governance``). Both at once fail closed
    (no silent dual authority); neither fails closed when an identity is
    declared. When absent, the launch stays plan-less/legacy and unchanged.

    AF #57 M3/W1: ``governance_context`` is an optional prebuilt trusted
    server-side projection. This composition only carries it through the
    existing binding and role.bootstrap path; it does not build Cards, read a
    Plan, or make projection data authoritative.

    AF #57 M3/W1: when ``governance_store_path`` is supplied for a local
    binding, the durable Plan record is resolved before exposing the local
    read adapter. A supplied GitHub source reader is accepted only when its
    operator-bound authority reference matches the launch binding. Omitting
    these optional inputs preserves the existing side-by-side launch shape.

    AF #57 M3/W4: ``evidence_base`` is a trusted operator evidence base. The
    host derives exactly the current project's ``authorized-evidence`` root;
    the global base and sibling project evidence remain unreachable.
    """
    pid = _validate_identifier(project_id, "project_id")
    wid = _validate_identifier(worktree_id, "worktree_id")
    root = _validate_worktree_root(worktree_root)
    origin = _validate_origin_session(origin_task_main_session_ref)
    normalized_plan_id: str | None = None
    if plan_id is not None and str(plan_id).strip():
        normalized_plan_id = str(plan_id).strip()
        if not is_plan_id(normalized_plan_id):
            raise TrustedBindingError(
                "thin host plan_id must be one canonical internal Plan ID "
                "(an Issue number, worktree, branch, repository or title is "
                "never a Plan identity)"
            )
    config_path = Path(runtime_config_path)
    if not config_path.is_file():
        raise TrustedBindingError(f"thin host runtime config missing: {config_path!r}")
    config_path = config_path.resolve()
    runtime_config = load_runtime_config(config_path=str(config_path))
    if not isinstance(runtime_config, RuntimeConfig):
        raise TrustedBindingError("thin host requires the canonical RuntimeConfig authority")

    project_evidence, sandbox, trusted_project_context = _resolve_sandbox(
        worktree_root=root,
        project_id=pid,
        worktree_id=wid,
        source_repository=source_repository,
        registry_path=registry_path,
    )

    # AF #55 M2: the task-main authorized root set is derived exclusively from
    # the trusted sandbox (project-main = canonical project root; active-worktree
    # = the bound construction worktree). No model input is involved.
    # AF #57 M1/W2: the trusted operator governance base additionally activates
    # the project-scoped local-governance read/search root.
    local_governance_binding: LocalGovernanceRootBinding | None = None
    if governance_base is not None and str(governance_base).strip():
        local_governance_binding = LocalGovernanceRootBinding.from_trusted_base(
            governance_base, project_id=pid
        )
    evidence_binding: Any | None = None
    if evidence_base is not None and str(evidence_base).strip():
        governed_read_composition = importlib.import_module(
            "aota_forge.composition.governed_read"
        )
        evidence_binding = governed_read_composition.bind_authorized_evidence_root(
            evidence_base=evidence_base,
            project_id=pid,
        )

    resolved_governance_store: Any | None = None
    if governance_store_path is not None and str(governance_store_path).strip():
        try:
            governance_composition = importlib.import_module(
                "aota_forge.composition.project_governance"
            )
            resolved_governance_store = governance_composition.open_project_governance_store(
                governance_store_path
            )
        except Exception as exc:
            raise TrustedBindingError(
                f"thin host governance store open failed: {exc}"
            ) from exc

    authorized_roots = authorized_roots_for_task_main(
        sandbox,
        governance_binding=local_governance_binding,
        evidence_binding=evidence_binding,
    )
    if resolved_governance_store is not None:
        try:
            governed_read_composition = importlib.import_module(
                "aota_forge.composition.governed_read"
            )
            authorized_roots = (
                governed_read_composition.materialize_live_cross_project_read_roots_from_registry(
                    sandbox=sandbox,
                    roots=authorized_roots,
                    store=resolved_governance_store,
                    registry_path=registry_path,
                )
            )
        except Exception as exc:
            resolved_governance_store.close()
            raise TrustedBindingError(
                f"thin host governed-read activation failed: {exc}"
            ) from exc

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

    # AF #56 M3/W1: mechanical host-completion-transport selection by the SAME
    # operator RuntimeConfig executor authority (shared trusted preparation,
    # bounded host-specific launch wiring; no cross-host fallback).
    if completion_transport is not None:
        transport = completion_transport
    elif runtime_config.executor == EXECUTOR_OPENCODE:
        transport = create_opencode_completion_delivery_transport(runtime_config=runtime_config)
    else:
        transport = create_hermes_completion_delivery_transport(runtime_config=runtime_config)
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
    read_authorities = _build_read_authorities(sandbox, handoff, authorized_roots)
    restricted_shell_authority = _build_restricted_shell_authority(sandbox, handoff)
    git_authorities = _build_git_authorities(
        sandbox,
        handoff,
        git_integration_branch=git_integration_branch,
        git_remote=git_remote,
    )
    # AF #54 M5/W2: mechanically ground the trusted bound-Plan GitHub
    # identity once at launch; GitHub authorities derive only from it. An
    # unbound launch carries no GitHub authority (fail closed at dispatch).
    plan_binding: TrustedPlanGitHubBinding | None = None
    github_authorities: tuple[Any, ...] = ()
    if plan_ref is not None and str(plan_ref).strip():
        plan_binding = TrustedPlanGitHubBinding.from_plan_ref(str(plan_ref).strip())
        github_authorities = (
            create_github_authority(sandbox, handoff, GITHUB_ISSUE_READ_DESCRIPTOR, plan_binding),
            create_github_authority(sandbox, handoff, GITHUB_ISSUE_COMMENTS_READ_DESCRIPTOR, plan_binding),
            create_github_authority(sandbox, handoff, GITHUB_ISSUE_UPDATE_DESCRIPTOR, plan_binding),
            create_github_authority(sandbox, handoff, GITHUB_ISSUE_COMMENT_UPDATE_DESCRIPTOR, plan_binding),
        )

    # AF #57 M1/W4: one source-neutral Plan Authority binding per launch.
    # The internal Plan identity is supplied explicitly by the trusted
    # runtime; the source kind is derived from exactly one available trusted
    # source (GitHub plan_ref or active local-governance root). Both or
    # neither fail closed; a plan-less legacy launch stays unchanged.
    plan_authority_binding: PlanAuthorityBinding | None = None
    if normalized_plan_id is not None:
        try:
            plan_authority_binding = compose_plan_authority_binding(
                project_id=pid,
                plan_id=normalized_plan_id,
                github_plan_ref=plan_binding.plan_ref if plan_binding is not None else None,
                local_governance_enabled=local_governance_binding is not None,
            )
        except PlanAuthorityCompositionError as exc:
            if resolved_governance_store is not None:
                resolved_governance_store.close()
            raise TrustedBindingError(str(exc)) from exc

    resolved_plan_authority: Any | None = None
    if plan_authority_binding is not None:
        try:
            if plan_authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                if plan_authority_reader is not None:
                    raise TrustedBindingError(
                        "local Plan authority cannot also receive a GitHub source reader"
                    )
                if governance_store_path is not None:
                    if local_governance_binding is None or normalized_plan_id is None:
                        raise TrustedBindingError(
                            "local Plan authority resolution requires a trusted governance root"
                        )
                    if resolved_governance_store is None:
                        raise TrustedBindingError(
                            "local Plan authority requires an opened governance store"
                        )
                    local_target = object_ref_subject(
                        make_id(
                            IdKind.SUBJECT,
                            normalized_plan_id,
                            sub_kind=SubjectKind.PLAN,
                        )
                    )
                    from aota_forge.adapters.plan_authority.local_governance import (
                        LocalPlanAuthorityDestination,
                    )

                    local_destination = LocalPlanAuthorityDestination(
                        governance_root=local_governance_binding,
                        plan_id=normalized_plan_id,
                        expected_ref=local_target,
                    )
                    resolved_plan_authority = resolve_bound_plan_authority(
                        plan_authority_binding,
                        local_destination=local_destination,
                        governance_store=resolved_governance_store,
                    )
            elif plan_authority_reader is not None:
                resolved_plan_authority = resolve_bound_plan_authority(
                    plan_authority_binding,
                    source_reader=plan_authority_reader,
                )
        except (PlanAuthorityCompositionError, TrustedBindingError) as exc:
            if resolved_governance_store is not None:
                resolved_governance_store.close()
            raise TrustedBindingError(str(exc)) from exc

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
        # AF #54 M5/W1: bounded trusted Git authorities (reads always;
        # lifecycle mutation family only when the operator configured it).
        git_authorities=git_authorities,
        # AF #54 M5/W2: trusted bound-Plan GitHub identity + the four
        # Plan-bound governance authorities (task-main composition only).
        github_authorities=github_authorities,
        plan_binding=plan_binding,
        # AF #57 M1/W4: the one source-neutral Plan Authority binding (None
        # for a plan-less legacy launch; never a second authority ontology).
        plan_authority_binding=plan_authority_binding,
        # The thin seam: no TrustedTaskMainRuntimeContext => M2/W1 generic thin
        # task lifecycle; no legacy workflow object is required or passed.
        trusted_task_main_context=None,
        # AF #53 M3/W1 explicit trusted classification (mechanical marker).
        task_main_runtime_path=TASK_MAIN_RUNTIME_PATH_THIN,
        # AF #54 M3/W1 passive observation session correlation: the exact
        # trusted origin session of this thin host. Mechanical carrier for
        # effectiveness evidence only; it decides no policy and grants no
        # authority.
        session_ref=origin,
        # AF #55 M2: trusted Plan SOURCE_REPOSITORY fact (mechanically grounded
        # above) and the task-main authorized root set. Mechanical carriers.
        source_repository=str(trusted_project_context.get("source_repository", "") or ""),
        authorized_roots=authorized_roots,
        governance_context=governance_context,
    )
    aota_invoke = create_aota_invoke_dispatch(binding)

    # AF #55 M2 §8: the trusted context projection separates PLAN_AUTHORITY
    # (plan_ref + governing repository), IMPLEMENTATION_PROJECT (project_id +
    # SOURCE_REPOSITORY + project-main root ref) and ACTIVE_WORKTREE, so the
    # model never infers the relationship. Bounded root refs only — no paths.
    source_repository_value = str(trusted_project_context.get("source_repository", "") or "")
    context_projection = build_trusted_project_context_projection(
        project_id=pid,
        worktree_id=wid,
        roots=authorized_roots,
        plan_ref=plan_binding.plan_ref if plan_binding is not None else "",
        governing_repository=plan_binding.repo if plan_binding is not None else "",
        source_repository=source_repository_value,
    )

    # Bind the existing canonical Core ingress dispatcher seam only after every
    # trusted authority lookup and host construction check has succeeded.
    bind_execution_dispatcher(dispatcher)

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
        plan_ref=plan_binding.plan_ref if plan_binding is not None else "",
        source_repository=str(trusted_project_context.get("source_repository", "") or ""),
        repository_identity_verified=bool(
            trusted_project_context.get("repository_identity_verified", False)
        ),
        repository_identity=trusted_project_context.get("repository_identity"),
        authorized_roots=authorized_roots,
        context_projection=context_projection,
        local_governance_binding=local_governance_binding,
        evidence_binding=evidence_binding,
        plan_authority_binding=plan_authority_binding,
        plan_authority_reader=resolved_plan_authority,
        governance_store=resolved_governance_store,
        governance_context=governance_context,
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
