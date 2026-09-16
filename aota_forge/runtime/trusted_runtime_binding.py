"""AF #46 M2/W1 — Pre-resolved Trusted Runtime Binding (runtime_composition ownership).

This module is the canonical owner of trusted runtime binding types.
Semantic owner: AF_CORE_OR_RUNTIME_COMPOSITION. Expected layer: runtime_composition.

It reuses existing trusted types (TrustedContext, TaskHandoff, WorktreeSandboxBoundary,
ToolRoleSurface, authority evidences) and moves the construction ownership
out of the MCP transport adapter. MCP adapter imports from here; the reverse
direction is forbidden.

Dependency direction (allowed):
    Host adapter -> AF runtime composition -> trusted binding -> MCP adapter -> canonical Core
Forbidden:
    AF_CORE imports MCP semantic type — no
    AF_RUNTIME imports MCP authority type — no

The pre-resolved binding must be constructed BEFORE MCP semantic dispatch.
Env/hermes/profile are not authority sources; a serialized envelope is not
authority source. Authority comes from AF runtime construction and verified
envelope.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.context import TrustedContext
from aota_forge.runtime.config import (
    SUPPORTED_TASK_MAIN_RUNTIME_PATHS,
    TASK_MAIN_RUNTIME_PATH_LEGACY,
    TASK_MAIN_RUNTIME_PATH_THIN,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.authorized_roots import (
    AuthorizedRootSet,
    authorized_roots_single_root,
    validate_root_set_against_sandbox,
)
from aota_forge.work_plane.tool_surface import ToolRoleSurface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.work_plane.workspace_tools import WorkspaceAuthorityEvidence  # type: ignore
from aota_forge.work_plane.workspace_mutation import WorkspaceMutationAuthority  # type: ignore

# ---------------------------------------------------------------------------
# Ownership markers (single source of truth)
# ---------------------------------------------------------------------------
SEMANTIC_OWNER = "AF_CORE_OR_RUNTIME_COMPOSITION"
EXPECTED_LAYER = "runtime_composition"
TRUSTED_RUNTIME_BINDING_OWNER = "AF_CORE_OR_RUNTIME_COMPOSITION"
TRUSTED_BINDING_TYPE_OWNER_IS_MCP = False
# Must be verifiable without MCP:
AF_RUNTIME_CAN_CONSTRUCT_TASK_MAIN_BINDING_WITHOUT_MCP = True
AF_RUNTIME_CAN_CONSTRUCT_WORKER_BINDING_WITHOUT_MCP = True
CORE_AUTHORITY_CAN_CONSUME_BINDING_WITHOUT_MCP = True

# Process-boundary transport: envelope is representation, not source
SERIALIZED_BINDING_IS_AUTHORITY_SOURCE = False
HOST_ENV_IS_AUTHORITY_SOURCE = False
HERMES_PROFILE_IS_AUTHORITY_SOURCE = False

# Role discrimination moved to runtime
ROLE_DISCRIMINATION_OWNER = "AF_RUNTIME_COMPOSITION"

# W5 (AF #49 M1/W5): role tool surfaces remain a subset of the canonical
# Agent-visible operation catalog. Exposure is not authority; unknown or
# unregistered operations still fail closed here.
ROLE_SURFACE_SUBSET_CANONICAL_AGENT_VISIBLE_OPERATION_CATALOG = True
UNKNOWN_OPERATION_FAIL_CLOSED = True

# Envelope transport (mechanical)
PRE_RESOLVED_BINDING_ENV = "AOTA_PRE_RESOLVED_BINDING"
PRE_RESOLVED_BINDING_VERSION = 1
PRE_RESOLVED_BINDING_DIGEST_ALGO = "sha256"

# ---------------------------------------------------------------------------
# Trusted binding error (single typed error, no string classification)
# ---------------------------------------------------------------------------
class TrustedBindingError(ValueError):
    """Malformed or incomplete server-side binding."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_ID_LEN = 512


# ---------------------------------------------------------------------------
# Trusted task-main runtime context (narrow carrier)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrustedTaskMainRuntimeContext:
    """Narrow trusted carrier for task-main control. Already-authoritative only."""

    control_service: Any
    live_plan_view: Any
    origin_task_main_session_ref: str
    executor_id: str
    handoff_resolver: Any
    governed_evidence_resolver: Any | None = None
    reviewer_handoff_resolver: Any | None = None
    governed_review_resolver: Any | None = None
    reviewer_canonical_task_id_resolver: Any | None = None
    next_milestone_view: Any | None = None
    session_available: bool = True
    coordinator_id: str | None = None

    def __post_init__(self) -> None:
        try:
            from aota_forge.runtime.task_main.control import TaskMainControlService  # type: ignore
            from aota_forge.runtime.task_main.coordinator import MilestonePlanView  # type: ignore
        except Exception:
            TaskMainControlService = object  # type: ignore
            MilestonePlanView = object  # type: ignore
        if TaskMainControlService is not object and not isinstance(self.control_service, TaskMainControlService):  # type: ignore
            if not hasattr(self.control_service, "activate_milestone"):
                raise TrustedBindingError(f"control_service must be TaskMainControlService, got {type(self.control_service).__name__}")
        if MilestonePlanView is not object and not isinstance(self.live_plan_view, MilestonePlanView):  # type: ignore
            raise TrustedBindingError(f"live_plan_view must be MilestonePlanView, got {type(self.live_plan_view).__name__}")
        if self.next_milestone_view is not None and MilestonePlanView is not object and not isinstance(self.next_milestone_view, MilestonePlanView):  # type: ignore
            raise TrustedBindingError(f"next_milestone_view must be MilestonePlanView or None, got {type(self.next_milestone_view).__name__}")
        if not isinstance(self.origin_task_main_session_ref, str) or not self.origin_task_main_session_ref.strip():
            raise TrustedBindingError("origin_task_main_session_ref must be non-empty string")
        if len(self.origin_task_main_session_ref) > _MAX_ID_LEN:
            raise TrustedBindingError("origin_task_main_session_ref exceeds bound")
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise TrustedBindingError("executor_id must be non-empty string")
        if not callable(self.handoff_resolver):
            raise TrustedBindingError("handoff_resolver must be callable")
        if self.governed_evidence_resolver is not None and not callable(self.governed_evidence_resolver):
            raise TrustedBindingError("governed_evidence_resolver must be callable or None")
        if self.reviewer_handoff_resolver is not None and not callable(self.reviewer_handoff_resolver):
            raise TrustedBindingError("reviewer_handoff_resolver must be callable or None")
        if self.governed_review_resolver is not None and not callable(self.governed_review_resolver):
            raise TrustedBindingError("governed_review_resolver must be callable or None")
        if self.reviewer_canonical_task_id_resolver is not None and not callable(self.reviewer_canonical_task_id_resolver):
            raise TrustedBindingError("reviewer_canonical_task_id_resolver must be callable or None")
        if type(self.session_available) is not bool:
            raise TrustedBindingError("session_available must be bool")
        if self.coordinator_id is not None:
            if not isinstance(self.coordinator_id, str) or not self.coordinator_id.strip():
                raise TrustedBindingError("coordinator_id must be non-empty string when supplied")
            if len(self.coordinator_id) > _MAX_ID_LEN:
                raise TrustedBindingError("coordinator_id exceeds bound")


# ---------------------------------------------------------------------------
# Trusted worker binding (thin carrier of already-authoritative objects)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrustedWorkerBinding:
    """Operator/runtime-owned context for one restricted MCP server. Thin carrier."""

    canonical_task_id: str
    project_id: str
    worktree_id: str
    trusted_context: TrustedContext
    handoff: TaskHandoff
    sandbox: WorktreeSandboxBoundary
    tool_surface: ToolRoleSurface
    read_authorities: tuple[WorkspaceAuthorityEvidence, ...]
    mutation_authority: WorkspaceMutationAuthority | None = None
    restricted_shell_authority: Any | None = None
    test_execution_authority: Any | None = None
    # AF #54 M5/W1: bounded trusted Git operation-authority evidences
    # (git.status/git.diff reads + the task-main lifecycle mutation family).
    # Like every other authority here this is a thin carrier of
    # already-authoritative runtime-minted evidence: it decides no policy and
    # is never minted from model input. Worker compositions mint none.
    git_authorities: tuple[Any, ...] = ()
    # AF #54 M5/W2: bounded trusted GitHub governance operation authorities
    # (Plan-bound reads + task-main mutations). Thin carriers of
    # runtime-minted evidence, like git_authorities; workers mint none.
    github_authorities: tuple[Any, ...] = ()
    # AF #54 M5/W2: trusted bound-Plan GitHub identity grounded at launch by
    # the operator/runtime (plan_ref "owner/repo#number"). Mechanical carrier
    # only: it decides no policy; GitHub authorities minted from it are the
    # authority. Workers keep None.
    plan_binding: Any | None = None
    # AF #57 M1/W4: the source-neutral W1 PlanAuthorityBinding for this launch
    # (internal plan_id + one bound authority source). Mechanical carrier
    # only: it decides no policy and grants no authority. Workers keep None;
    # a plan-less legacy launch keeps None.
    plan_authority_binding: Any | None = None
    trusted_task_main_context: Any | None = None
    # AF #53 M3/W1 trusted classification of the task-main production
    # composition path this binding was minted for. Mechanical carrier
    # metadata only (it decides no policy); it lets the MCP child classify an
    # explicitly thin trusted task-main binding (no legacy context required)
    # without guessing. Worker bindings keep the legacy default.
    task_main_runtime_path: str = TASK_MAIN_RUNTIME_PATH_LEGACY
    # AF #54 M3/W1 optional bounded observation correlation carriers. They are
    # mechanical runtime identity facts for passive effectiveness observation
    # only: never authority, never execution input, never policy. Empty means
    # unavailable (never invented).
    session_ref: str = ""
    parent_session_ref: str = ""
    run_ref: str = ""
    # AF #55 M2: trusted Plan project identity (declared SOURCE_REPOSITORY,
    # mechanically grounded through the canonical resolver + Git origin).
    # Mechanical carrier only; task-main composition only.
    source_repository: str = ""
    # AF #55 M2: the session's authorized root set (root_ref capability model).
    # None preserves the accepted single-worktree behavior for legacy bindings;
    # when present it must be the trusted sandbox's own roots and must match
    # every read/mutation authority's root set.
    authorized_roots: AuthorizedRootSet | None = None

    def __post_init__(self) -> None:
        # Import here to avoid circular at import time for optional authorities
        try:
            from aota_forge.work_plane.restricted_shell import RestrictedShellAuthorityEvidence as _ShellEv  # type: ignore
        except Exception:
            _ShellEv = None  # type: ignore
        try:
            from aota_forge.work_plane.test_execution import TestExecutionAuthorityEvidence as _TestEv  # type: ignore
        except Exception:
            _TestEv = None  # type: ignore
        try:
            from aota_forge.work_plane.git_tools import GitOperationAuthorityEvidence as _GitEv  # type: ignore
            from aota_forge.work_plane.git_tools import ALL_EXPOSED_GIT_OPERATIONS as _GIT_OPS  # type: ignore
        except Exception:
            _GitEv = None  # type: ignore
            _GIT_OPS = ("git.status", "git.diff", "git.checkpoint", "git.integrate", "git.push")  # type: ignore
        try:
            from aota_forge.work_plane.github_tools import GitHubOperationAuthorityEvidence as _GitHubEv  # type: ignore
            from aota_forge.work_plane.github_tools import ALL_GITHUB_OPERATIONS as _GITHUB_OPS  # type: ignore
        except Exception:
            _GitHubEv = None  # type: ignore
            _GITHUB_OPS = (
                "github.issue.read",
                "github.issue.comments.read",
                "github.issue.update",
                "github.issue.comment.update",
            )  # type: ignore

        if not isinstance(self.canonical_task_id, str) or not _SAFE_ID.fullmatch(self.canonical_task_id):
            raise TrustedBindingError("canonical_task_id must be a bounded trusted identifier")
        if not isinstance(self.project_id, str) or not _SAFE_ID.fullmatch(self.project_id):
            raise TrustedBindingError("project_id must be a bounded trusted identifier")
        if not isinstance(self.worktree_id, str) or not _SAFE_ID.fullmatch(self.worktree_id):
            raise TrustedBindingError("worktree_id must be a bounded trusted identifier")
        if not isinstance(self.trusted_context, TrustedContext) or not self.trusted_context.is_bound:
            raise TrustedBindingError("trusted runtime context is required")
        if self.trusted_context.principal is None:
            raise TrustedBindingError("trusted principal is required")
        if not isinstance(self.handoff, TaskHandoff):
            raise TrustedBindingError("typed TaskHandoff is required")
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise TrustedBindingError("trusted WorktreeSandboxBoundary is required")
        if self.sandbox.project_id != self.project_id or self.sandbox.worktree_id != self.worktree_id:
            raise TrustedBindingError("binding project/worktree does not match sandbox")
        if not isinstance(self.tool_surface, ToolRoleSurface):
            raise TrustedBindingError("typed ToolRoleSurface is required")
        if self.tool_surface.work_role != self.handoff.work_role:
            raise TrustedBindingError("tool surface role does not match TaskHandoff role")
        # Surface must be subset of the canonical Agent-visible operation
        # catalog (MCP transport exposure boundary or Core set).
        # W5 (AF #49 M1/W5): the catalog now includes the canonical normal-path
        # handoff/task lifecycle operations; subset validation is preserved and
        # unknown/unregistered operations still fail closed.
        try:
            from aota_forge.mcp_transport import SUPPORTED_OPERATIONS as _MCP_OPS  # type: ignore

            allowed = set(_MCP_OPS)
        except Exception:
            # fallback to minimal set if MCP not present (circular import guard)
            allowed = {
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
                "task_main.submit_work_projection",
                "handoff.write",
                "handoff.open",
                "task.start",
                "task.return",
                "git.status",
                "git.diff",
                "git.checkpoint",
                "git.integrate",
                "git.push",
                "github.issue.read",
                "github.issue.comments.read",
                "github.issue.update",
                "github.issue.comment.update",
            }
        surface_names = set(self.tool_surface.all_capability_names())
        if not surface_names.issubset(allowed):
            unknown = sorted(surface_names - allowed)
            raise TrustedBindingError(
                f"tool surface contains unknown logical operation(s) (allowed {sorted(allowed)}, unknown {unknown})"
            )
        if "aota.invoke" in surface_names:
            raise TrustedBindingError("tool surface must not contain transport name aota.invoke")
        if not isinstance(self.read_authorities, tuple):
            raise TrustedBindingError("read_authorities must be tuple")
        if len(self.read_authorities) > 2:
            raise TrustedBindingError("read authorities at most 2")
        read_names = set()
        for authority in self.read_authorities:
            if not isinstance(authority, WorkspaceAuthorityEvidence):
                raise TrustedBindingError(f"read authority must be WorkspaceAuthorityEvidence, got {type(authority).__name__}")
            if authority.sandbox != self.sandbox or authority.handoff != self.handoff:
                raise TrustedBindingError("read authority does not match trusted binding")
            if authority.operation.name not in ("workspace.search", "workspace.read"):
                raise TrustedBindingError(f"read authority operation must be workspace.search/read, got {authority.operation.name!r}")
            if authority.operation.name in read_names:
                raise TrustedBindingError(f"duplicate read authority for {authority.operation.name!r}")
            read_names.add(authority.operation.name)
        if self.mutation_authority is not None:
            if not isinstance(self.mutation_authority, WorkspaceMutationAuthority):
                raise TrustedBindingError("mutation authority must be typed")
            if self.mutation_authority.sandbox != self.sandbox or self.mutation_authority.handoff != self.handoff:
                raise TrustedBindingError("mutation authority does not match trusted binding")
            if self.mutation_authority.operation.name != "workspace.write":
                raise TrustedBindingError(f"mutation authority operation must be workspace.write, got {self.mutation_authority.operation.name!r}")
        if self.restricted_shell_authority is not None:
            if _ShellEv is not None and not isinstance(self.restricted_shell_authority, _ShellEv):
                raise TrustedBindingError(f"restricted_shell_authority must be RestrictedShellAuthorityEvidence, got {type(self.restricted_shell_authority).__name__}")
            if self.restricted_shell_authority.sandbox != self.sandbox or self.restricted_shell_authority.handoff != self.handoff:
                raise TrustedBindingError("restricted shell authority does not match trusted binding")
            if self.restricted_shell_authority.operation.name != "restricted_shell.run":
                raise TrustedBindingError(f"restricted shell authority operation must be restricted_shell.run, got {self.restricted_shell_authority.operation.name!r}")
        if self.test_execution_authority is not None:
            if _TestEv is not None and not isinstance(self.test_execution_authority, _TestEv):
                raise TrustedBindingError(f"test_execution_authority must be TestExecutionAuthorityEvidence, got {type(self.test_execution_authority).__name__}")
            ev = self.test_execution_authority
            if not hasattr(ev, "sandbox") or not hasattr(ev, "handoff") or not hasattr(ev, "operation"):
                raise TrustedBindingError("test_execution_authority missing required fields")
            if ev.sandbox != self.sandbox or ev.handoff != self.handoff:
                raise TrustedBindingError("test execution authority does not match trusted binding")
            if ev.operation.name != "test.run":  # type: ignore[union-attr]
                raise TrustedBindingError(f"test execution authority operation must be test.run, got {ev.operation.name!r}")  # type: ignore[union-attr]
        # AF #54 M5/W1: bounded Git operation-authority validation (mechanical
        # match against the already-trusted sandbox/handoff; lifecycle
        # mutation evidence additionally requires the trusted task-main role —
        # evidence construction itself enforces the rest).
        if not isinstance(self.git_authorities, tuple):
            raise TrustedBindingError("git_authorities must be tuple")
        if len(self.git_authorities) > 5:
            raise TrustedBindingError("git authorities at most 5")
        git_names: set[str] = set()
        for authority in self.git_authorities:
            if _GitEv is not None and not isinstance(authority, _GitEv):
                raise TrustedBindingError(f"git authority must be GitOperationAuthorityEvidence, got {type(authority).__name__}")
            if authority.sandbox != self.sandbox or authority.handoff != self.handoff:
                raise TrustedBindingError("git authority does not match trusted binding")
            op_name = authority.operation.name
            if op_name not in _GIT_OPS:
                raise TrustedBindingError(f"git authority operation must be a canonical git operation, got {op_name!r}")
            if op_name in git_names:
                raise TrustedBindingError(f"duplicate git authority for {op_name!r}")
            git_names.add(op_name)
            if op_name in ("git.checkpoint", "git.integrate", "git.push"):
                if self.handoff.work_role.value != "task-main" or self.tool_surface.work_role.value != "task-main":
                    raise TrustedBindingError(f"git lifecycle mutation authority {op_name!r} requires task-main role binding")
        # AF #54 M5/W2: bounded GitHub governance authority validation
        # (mechanical match; role/target gates live in the evidence itself).
        if not isinstance(self.github_authorities, tuple):
            raise TrustedBindingError("github_authorities must be tuple")
        if len(self.github_authorities) > 4:
            raise TrustedBindingError("github authorities at most 4")
        github_names: set[str] = set()
        for authority in self.github_authorities:
            if _GitHubEv is not None and not isinstance(authority, _GitHubEv):
                raise TrustedBindingError(f"github authority must be GitHubOperationAuthorityEvidence, got {type(authority).__name__}")
            if authority.sandbox != self.sandbox or authority.handoff != self.handoff:
                raise TrustedBindingError("github authority does not match trusted binding")
            gh_name = authority.operation.name
            if gh_name not in _GITHUB_OPS:
                raise TrustedBindingError(f"github authority operation must be a canonical github operation, got {gh_name!r}")
            if gh_name in github_names:
                raise TrustedBindingError(f"duplicate github authority for {gh_name!r}")
            github_names.add(gh_name)
            if self.handoff.work_role.value != "task-main" or self.tool_surface.work_role.value != "task-main":
                raise TrustedBindingError(f"github governance authority {gh_name!r} requires task-main role binding")
        # AF #54 M5/W2: trusted bound-Plan GitHub identity (mechanical only).
        if self.plan_binding is not None:
            try:
                from aota_forge.work_plane.github_tools import TrustedPlanGitHubBinding  # type: ignore
            except Exception:
                TrustedPlanGitHubBinding = None  # type: ignore
            if TrustedPlanGitHubBinding is not None and not isinstance(self.plan_binding, TrustedPlanGitHubBinding):
                raise TrustedBindingError(f"plan_binding must be TrustedPlanGitHubBinding, got {type(self.plan_binding).__name__}")
            plan_ref = getattr(self.plan_binding, "plan_ref", None)
            if not isinstance(plan_ref, str) or not plan_ref.strip():
                raise TrustedBindingError("plan_binding carries no plan_ref")
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError("plan_binding may only be carried by a task-main binding")
        # AF #57 M1/W4: source-neutral Plan Authority binding (mechanical
        # carrier only). It must be a real binding, task-main composition
        # only, and it must not create a second simultaneous authority for
        # one Plan: a github_issue binding must agree with the bound GitHub
        # plan_binding, and a local_governance binding requires the GitHub
        # plan authority to be absent.
        if self.plan_authority_binding is not None:
            try:
                from aota_forge.adapters.plan_authority.binding import (
                    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    PlanAuthorityBinding,
                )  # type: ignore
            except Exception:
                PlanAuthorityBinding = None  # type: ignore
                PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE = "github_issue"  # type: ignore
            if PlanAuthorityBinding is not None and not isinstance(
                self.plan_authority_binding, PlanAuthorityBinding
            ):
                raise TrustedBindingError(
                    "plan_authority_binding must be a source-neutral PlanAuthorityBinding, "
                    f"got {type(self.plan_authority_binding).__name__}"
                )
            bound_kind = getattr(self.plan_authority_binding, "source_kind", None)
            bound_plan_id = getattr(self.plan_authority_binding, "plan_id", None)
            bound_ref = getattr(self.plan_authority_binding, "authority_ref", None)
            if not isinstance(bound_plan_id, str) or not bound_plan_id.strip():
                raise TrustedBindingError("plan_authority_binding carries no plan_id")
            if not isinstance(bound_ref, str) or not bound_ref.strip():
                raise TrustedBindingError("plan_authority_binding carries no authority_ref")
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError(
                    "plan_authority_binding may only be carried by a task-main binding"
                )
            if bound_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE:
                plan_ref = getattr(self.plan_binding, "plan_ref", None)
                if plan_ref is None or str(plan_ref).strip() != bound_ref:
                    raise TrustedBindingError(
                        "github_issue plan_authority_binding must agree with the bound "
                        "Plan GitHub reference (no second authority)"
                    )
            elif self.plan_binding is not None:
                raise TrustedBindingError(
                    f"{bound_kind!r} plan_authority_binding must not carry a simultaneous "
                    "Plan GitHub authority (no silent dual authority)"
                )
        if self.trusted_task_main_context is not None:
            if not isinstance(self.trusted_task_main_context, TrustedTaskMainRuntimeContext):
                raise TrustedBindingError(f"trusted_task_main_context must be TrustedTaskMainRuntimeContext, got {type(self.trusted_task_main_context).__name__}")
            ctx = self.trusted_task_main_context
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError("task-main context requires handoff work_role task-main")
            if self.tool_surface.work_role.value != "task-main":
                raise TrustedBindingError("task-main context requires tool_surface work_role task-main")
            if not hasattr(ctx.control_service, "activate_milestone"):
                raise TrustedBindingError("task-main context control_service missing activate_milestone")
        # AF #53 M3/W1: explicit trusted task-main runtime path classification.
        if self.task_main_runtime_path not in SUPPORTED_TASK_MAIN_RUNTIME_PATHS:
            raise TrustedBindingError(
                "task_main_runtime_path must be one of "
                f"{list(SUPPORTED_TASK_MAIN_RUNTIME_PATHS)}, got {self.task_main_runtime_path!r}"
            )
        if self.task_main_runtime_path == TASK_MAIN_RUNTIME_PATH_THIN:
            if self.trusted_task_main_context is not None:
                raise TrustedBindingError(
                    "thin task-main runtime path must not carry a legacy task-main context"
                )
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError(
                    "thin task-main runtime path requires handoff work_role task-main"
                )
            if self.tool_surface.work_role.value != "task-main":
                raise TrustedBindingError(
                    "thin task-main runtime path requires tool_surface work_role task-main"
                )
        # AF #54 M3/W1 optional observation correlation carriers: bounded,
        # mechanical, non-authoritative. Empty is the unavailable default.
        for _label, _value in (
            ("session_ref", self.session_ref),
            ("parent_session_ref", self.parent_session_ref),
            ("run_ref", self.run_ref),
        ):
            if not isinstance(_value, str):
                raise TrustedBindingError(f"{_label} must be a string")
            if _value and (len(_value) > 512 or "\x00" in _value or not _SAFE_ID.fullmatch(_value)):
                raise TrustedBindingError(f"{_label} must be a bounded trusted identifier")
        # AF #55 M2: trusted Plan project identity is a task-main-only
        # mechanical fact and must be a normalizable repository identity.
        if self.source_repository:
            if not isinstance(self.source_repository, str) or len(self.source_repository) > 512:
                raise TrustedBindingError("source_repository must be a bounded repository identity string")
            from aota_forge.core.project.repository_identity import normalize_repository_identity  # type: ignore

            try:
                normalize_repository_identity(self.source_repository)
            except ValueError as exc:
                raise TrustedBindingError(f"source_repository is not a repository identity: {exc}") from exc
            if self.handoff.work_role.value != "task-main":
                raise TrustedBindingError("source_repository may only be carried by a task-main binding")
        # AF #55 M2: an explicitly carried authorized root set must be the
        # trusted sandbox's own roots and must agree with every authority's
        # effective root set (no authority may widen the binding's roots).
        if self.authorized_roots is not None:
            if not isinstance(self.authorized_roots, AuthorizedRootSet):
                raise TrustedBindingError(
                    f"authorized_roots must be AuthorizedRootSet or None, got {type(self.authorized_roots).__name__}"
                )
            try:
                validate_root_set_against_sandbox(self.authorized_roots, self.sandbox)
            except Exception as exc:
                raise TrustedBindingError(f"authorized root set is not sandbox-derived: {exc}") from exc
            expected_digest = self.authorized_roots.digest()
            for authority in self.read_authorities:
                try:
                    actual_digest = authority.authorized_root_set.digest()
                except Exception as exc:
                    raise TrustedBindingError(f"read authority authorized root set invalid: {exc}") from exc
                if actual_digest != expected_digest:
                    raise TrustedBindingError(
                        "read authority authorized root set does not match the binding's authorized roots"
                    )
            if self.mutation_authority is not None:
                try:
                    mutation_digest = self.mutation_authority.authorized_root_set.digest()
                except Exception as exc:
                    raise TrustedBindingError(f"mutation authority authorized root set invalid: {exc}") from exc
                if mutation_digest != expected_digest:
                    raise TrustedBindingError(
                        "mutation authority authorized root set does not match the binding's authorized roots"
                    )

        # AF #59 M1: the #58 interactive Plan-state carrier is removed; the
        # binding never carries session-scoped Plan approval state.

    @property
    def effective_authorized_roots(self) -> AuthorizedRootSet:
        """Effective authorized root set (legacy bindings derive the accepted
        single-worktree set from the trusted sandbox)."""
        if self.authorized_roots is not None:
            return self.authorized_roots
        return authorized_roots_single_root(self.sandbox)


# ---------------------------------------------------------------------------
# Process-boundary envelope (representation, not authority source)
# ---------------------------------------------------------------------------
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_digest(canonical_str: str) -> str:
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def create_worker_envelope(
    *,
    worktree_root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    envelope_dir: Path | None = None,
    provenence: Mapping[str, Any] | None = None,
) -> Path:
    """AF runtime composition creates worker envelope BEFORE MCP transport.

    Returns path to envelope file (0600). The envelope digest binds project,
    worktree, task and handoff. Tamper fails closed on load.
    """
    worktree_root = Path(worktree_root).resolve()
    if envelope_dir is None:
        envelope_dir = worktree_root / ".aota" / "pre-resolved-bindings"
    envelope_dir = Path(envelope_dir).resolve()
    envelope_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "kind": "worker",
        "version": PRE_RESOLVED_BINDING_VERSION,
        "project_id": project_id,
        "worktree_id": worktree_id,
        "canonical_task_id": canonical_task_id,
        "worktree_root": str(worktree_root),
        "handoff": handoff.to_dict(),
        "handoff_digest": handoff.handoff_digest,
        "provenance": dict(provenence or {}),
    }
    canonical = _canonical_json(payload)
    digest = _compute_digest(canonical)
    envelope = {
        "version": PRE_RESOLVED_BINDING_VERSION,
        "kind": "worker",
        "digest": digest,
        "payload": payload,
    }
    # Deterministic filename per task
    safe_task = re.sub(r"[^A-Za-z0-9._-]", "_", canonical_task_id)[:64]
    path = envelope_dir / f"worker-{safe_task}-{digest[:8]}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_canonical_json(envelope), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


def create_task_main_envelope(
    *,
    worktree_root: Path,
    bootstrap_path: Path,
    provenence: Mapping[str, Any] | None = None,
) -> Path:
    """Wrap existing task-main bootstrap into verified envelope.

    The bootstrap file itself remains operator-owned (0600). We create a
    sibling envelope that digest-binds its content plus runtime provenance.
    The envelope path is the locator for MCP; the bootstrap remains the
    durable store locator inside.
    """
    worktree_root = Path(worktree_root).resolve()
    bootstrap_path = Path(bootstrap_path).resolve()
    if not bootstrap_path.is_file():
        raise TrustedBindingError(f"bootstrap file missing: {bootstrap_path}")
    try:
        bootstrap_content = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TrustedBindingError(f"bootstrap unreadable: {exc}") from exc

    envelope_dir = worktree_root / ".aota" / "pre-resolved-bindings"
    envelope_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "kind": "task-main",
        "version": PRE_RESOLVED_BINDING_VERSION,
        "bootstrap_path": str(bootstrap_path),
        "bootstrap_digest": hashlib.sha256(json.dumps(bootstrap_content, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "bootstrap_content": bootstrap_content,
        "worktree_root": str(worktree_root),
        "provenance": dict(provenence or {}),
    }
    canonical = _canonical_json(payload)
    digest = _compute_digest(canonical)
    envelope = {
        "version": PRE_RESOLVED_BINDING_VERSION,
        "kind": "task-main",
        "digest": digest,
        "payload": payload,
    }
    # For task-main, envelope name binds to bootstrap digest to be stable per launch
    path = envelope_dir / f"task-main-{payload['bootstrap_digest'][:8]}-{digest[:8]}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_canonical_json(envelope), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


def verify_envelope(path: Path | str) -> tuple[str, dict[str, Any]]:
    """Verify envelope digest and version. Returns (kind, payload). Fail closed."""
    p = Path(path).resolve()
    if not p.is_file():
        raise TrustedBindingError(f"envelope file missing: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TrustedBindingError(f"envelope unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustedBindingError("envelope must be object")
    if data.get("version") != PRE_RESOLVED_BINDING_VERSION:
        raise TrustedBindingError(f"envelope version mismatch: {data.get('version')}")
    kind = data.get("kind")
    if kind not in ("worker", "task-main"):
        raise TrustedBindingError(f"envelope kind invalid: {kind!r}")
    digest = data.get("digest")
    payload = data.get("payload")
    if not isinstance(digest, str) or not isinstance(payload, dict):
        raise TrustedBindingError("envelope missing digest/payload")
    canonical = _canonical_json(payload)
    computed = _compute_digest(canonical)
    if computed != digest:
        raise TrustedBindingError(f"envelope digest mismatch: tampered (expected {digest}, computed {computed})")
    # Additional tamper checks: handoff_digest inside payload must match handoff content for worker
    if kind == "worker":
        handoff_dict = payload.get("handoff")
        expected_hd = payload.get("handoff_digest")
        if not isinstance(handoff_dict, dict) or not isinstance(expected_hd, str):
            raise TrustedBindingError("worker envelope missing handoff")
        try:
            h = TaskHandoff.from_dict(handoff_dict)
        except Exception as exc:
            raise TrustedBindingError(f"worker handoff invalid: {exc}") from exc
        if h.handoff_digest != expected_hd:
            raise TrustedBindingError(f"handoff digest mismatch: {h.handoff_digest} != {expected_hd}")
        # Project/worktree inside handoff refs when present must agree
        # (handoff.project_ref when present) — fail closed on mismatch is already in build path
    elif kind == "task-main":
        # Verify bootstrap_digest matches bootstrap_content
        bootstrap_content = payload.get("bootstrap_content")
        bootstrap_digest = payload.get("bootstrap_digest")
        if not isinstance(bootstrap_content, dict) or not isinstance(bootstrap_digest, str):
            raise TrustedBindingError("task-main envelope missing bootstrap")
        computed_bd = hashlib.sha256(json.dumps(bootstrap_content, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if computed_bd != bootstrap_digest:
            raise TrustedBindingError(f"bootstrap digest mismatch in envelope")
    return kind, payload


def load_binding_from_envelope(envelope_path: Path | str) -> TrustedWorkerBinding:
    """Verified reconstruction of trusted binding from envelope.

    This is the ONLY MCP-side reconstruction path after W1. Authority still
    derives from AF runtime construction; envelope is verified before use.
    """
    kind, payload = verify_envelope(envelope_path)
    if kind == "worker":
        # Reconstruct via the canonical builder (runtime-owned)
        # Lazy import to avoid circular at module import time
        from aota_forge.composition.worker_vertical_slice import build_worker_binding  # type: ignore

        worktree_root = Path(payload["worktree_root"])
        project_id = payload["project_id"]
        worktree_id = payload["worktree_id"]
        canonical_task_id = payload["canonical_task_id"]
        handoff = TaskHandoff.from_dict(payload["handoff"])
        binding = build_worker_binding(
            root=worktree_root,
            project_id=project_id,
            worktree_id=worktree_id,
            canonical_task_id=canonical_task_id,
            handoff=handoff,
        )
        # AF #54 M3/W2: install the operator-opt-in passive observation sink
        # carried by the verified envelope provenance (bounded, mechanical,
        # non-authoritative). Fail-isolated.
        try:
            provenance = payload.get("provenance")
            if isinstance(provenance, dict):
                from aota_forge.work_plane.runtime_observation import (
                    OBSERVATION_RUN_REF_ENV,
                    OBSERVATION_SESSION_REF_ENV,
                    OBSERVATION_SINK_ENV,
                    configure_runtime_observation,
                )

                configure_runtime_observation(
                    evidence_path=provenance.get(OBSERVATION_SINK_ENV),
                    run_ref=provenance.get(OBSERVATION_RUN_REF_ENV),
                    session_ref=provenance.get(OBSERVATION_SESSION_REF_ENV),
                )
        except BaseException:
            pass
        return binding
    else:  # task-main
        # Delegate to host bootstrap's builder via explicit path handling
        # The envelope payload contains bootstrap_content; we reconstruct via
        # the same deterministic path as try_build_task_main_binding but using
        # the verified bootstrap_content directly to avoid re-reading file that
        # could be tampered after envelope creation.
        # For simplicity, we write the verified bootstrap_content to a temp file
        # and invoke the builder with explicit env pointing there, then verify.
        # Instead, we directly call the builder logic: we replicate the
        # try_build_task_main_binding steps but with payload content.
        # Simpler: use the bootstrap_path inside payload and call the builder
        # after verifying that the file content matches the digest already.
        bootstrap_path = Path(payload["bootstrap_path"])
        # Ensure file content still matches digest (already verified inside envelope,
        # but also verify live file hasn't been swapped)
        try:
            live_content = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TrustedBindingError(f"live bootstrap unreadable: {exc}") from exc
        live_digest = hashlib.sha256(json.dumps(live_content, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if live_digest != payload["bootstrap_digest"]:
            raise TrustedBindingError("live bootstrap tampered after envelope creation")
        # AF #53 M3/W1: explicit trusted runtime-path marker routes to the
        # accepted M2 thin composition (legacy-free executed path). Unknown
        # values fail closed; absence keeps the existing legacy compatibility
        # behavior unchanged.
        runtime_path = str(live_content.get("runtime_path", TASK_MAIN_RUNTIME_PATH_LEGACY) or TASK_MAIN_RUNTIME_PATH_LEGACY)
        if runtime_path == TASK_MAIN_RUNTIME_PATH_THIN:
            from aota_forge.composition.task_main_runtime_selection import (  # type: ignore
                build_thin_task_main_binding_from_envelope_bootstrap,
            )

            return build_thin_task_main_binding_from_envelope_bootstrap(
                live_content,
                envelope_worktree_root=payload.get("worktree_root"),
            )
        if runtime_path != TASK_MAIN_RUNTIME_PATH_LEGACY:
            raise TrustedBindingError(
                f"task-main bootstrap runtime_path invalid: {runtime_path!r}"
            )
        # Now delegate to existing builder via env indirection (explicit path)
        import os

        old_explicit = os.environ.get("AOTA_TASK_MAIN_BOOTSTRAP")
        old_root = os.environ.get("AOTA_W3_MCP_ROOT")
        try:
            os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = str(bootstrap_path)
            # Ensure MCP root points to worktree so bootstrap resolution works
            os.environ["AOTA_W3_MCP_ROOT"] = str(Path(payload["worktree_root"]))
            from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding  # type: ignore

            binding = try_build_task_main_binding()
        finally:
            if old_explicit is None:
                os.environ.pop("AOTA_TASK_MAIN_BOOTSTRAP", None)
            else:
                os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = old_explicit
            if old_root is None:
                os.environ.pop("AOTA_W3_MCP_ROOT", None)
            else:
                os.environ["AOTA_W3_MCP_ROOT"] = old_root
        if binding is None:
            raise TrustedBindingError("task-main binding construction failed from envelope")
        return binding


# ---------------------------------------------------------------------------
# Helper for composition: build envelope and return env dict with locator
# ---------------------------------------------------------------------------
def build_worker_envelope_env(
    *,
    worktree_root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    envelope_dir: Path | None = None,
) -> dict[str, str]:
    """AF runtime composition builds envelope and returns mechanical env overlay.

    The overlay contains ONLY the opaque locator PRE_RESOLVED_BINDING_ENV
    plus mechanical PYTHONPATH/REPO_ROOT. No AOTA_W3_* authority keys.
    """
    path = create_worker_envelope(
        worktree_root=worktree_root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
        envelope_dir=envelope_dir,
    )
    return {PRE_RESOLVED_BINDING_ENV: str(path)}


def build_task_main_envelope_env(
    *,
    worktree_root: Path,
    bootstrap_path: Path,
) -> dict[str, str]:
    """AF runtime composition builds task-main envelope and returns locator env."""
    path = create_task_main_envelope(worktree_root=worktree_root, bootstrap_path=bootstrap_path)
    return {PRE_RESOLVED_BINDING_ENV: str(path)}


__all__ = [
    "SEMANTIC_OWNER",
    "EXPECTED_LAYER",
    "TRUSTED_RUNTIME_BINDING_OWNER",
    "TRUSTED_BINDING_TYPE_OWNER_IS_MCP",
    "AF_RUNTIME_CAN_CONSTRUCT_TASK_MAIN_BINDING_WITHOUT_MCP",
    "AF_RUNTIME_CAN_CONSTRUCT_WORKER_BINDING_WITHOUT_MCP",
    "CORE_AUTHORITY_CAN_CONSUME_BINDING_WITHOUT_MCP",
    "SERIALIZED_BINDING_IS_AUTHORITY_SOURCE",
    "HOST_ENV_IS_AUTHORITY_SOURCE",
    "HERMES_PROFILE_IS_AUTHORITY_SOURCE",
    "ROLE_DISCRIMINATION_OWNER",
    "PRE_RESOLVED_BINDING_ENV",
    "PRE_RESOLVED_BINDING_VERSION",
    "TrustedBindingError",
    "TrustedWorkerBinding",
    "TrustedTaskMainRuntimeContext",
    "create_worker_envelope",
    "create_task_main_envelope",
    "verify_envelope",
    "load_binding_from_envelope",
    "build_worker_envelope_env",
    "build_task_main_envelope_env",
]
