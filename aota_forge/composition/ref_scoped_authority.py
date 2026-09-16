"""AF #59 M1/W2 — operation-time ref-scoped authority for unbound host sessions.

Ordinary OpenCode/OpenChamber sessions have no session/instance/Plan binding.
The always-available AOTA MCP therefore resolves operation authority from the
canonical refs supplied to the operation, server-side, at the operation
boundary:

    plan_ref     -> live Plan (accepted Plan-authority read path)
                 -> trusted project binding (operator workspace registry)
                 -> canonical thin task-main host composition (reused as-is)

The same locator scopes the task-main Git surface: ``git.status`` / ``git.diff``
resolve to the canonical project root and the existing bounded read authority
(usable without milestone approval), and ``git.checkpoint`` / ``git.integrate``
/ ``git.push`` additionally re-read the current milestone approval and use the
existing governed Git lifecycle providers with the operator-configured
integration branch/remote. No caller repo path, no session authority, no new
capability token.

Hard invariants
---------------
* SESSION_BINDING_REQUIRED=no — no session token, lease, preparation record or
  instance directory participates in authority.
* REF_IS_AUTHORITY_LOCATOR=yes, REF_IS_AUTHORITY_SOURCE=no — the ref locates a
  server-side trusted record; the record is the authority.
* NEW_AUTHORITY_ENGINE_CREATED=no — this module reuses, unchanged:
  - the accepted GitHub Plan-authority read adapter,
  - the canonical Portable Plan normalization,
  - the canonical trusted project binding resolver,
  - the canonical thin task-main host composition (``compose_thin_task_main_host``),
    which mints the same ``TrustedWorkerBinding`` the accepted headless/interactive
    paths use (sandbox + read/mutation/git/github authorities + dispatcher).
* APPROVAL_IS_CURRENT_SERVER_SIDE_FACT=yes, APPROVAL_IS_SESSION_STATE=no — the
  milestone approval gate re-reads the live Plan on every boundary crossing.
* WRONG_PROJECT_WORKTREE_TASK_REF_FAIL_CLOSED=yes — the durable handoff is
  opened under the resolved project sandbox; foreign worktrees fail closed.

No second MCP server, no second authority engine, no session system, no
generic ref registry, no capability token.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

REF_SCOPED_AUTHORITY = True
SESSION_BINDING_REQUIRED = False
REF_IS_AUTHORITY_LOCATOR = True
REF_IS_AUTHORITY_SOURCE = False
NEW_AUTHORITY_ENGINE_CREATED = False
APPROVAL_IS_CURRENT_SERVER_SIDE_FACT = True
APPROVAL_IS_SESSION_STATE = False
SECOND_SESSION_SYSTEM_CREATED = False
SECOND_MCP_SERVER_CREATED = False

# Operations whose unbound authority is located by the canonical ``plan_ref``
# input (the smallest existing-ref carrier; added to these canonical
# descriptors in .aota/contracts/operations.yaml).
# AF #59 M1 acceptance repair R4/R5: the task-main Git inspection reads and
# the governed Git lifecycle mutations are ref-scoped the same way; the Git
# mechanics themselves (trusted worktree, expected-head/CAS, FF-only
# integration, configured integration branch/remote) stay exactly the
# existing task-main providers.
PLAN_REF_SCOPED_OPERATIONS: frozenset[str] = frozenset(
    {
        "github.issue.read",
        "github.issue.comments.read",
        "github.issue.update",
        "github.issue.comment.update",
        "handoff.write",
        "handoff.open",
        "task.start",
        "workspace.read",
        "workspace.search",
        "git.status",
        "git.diff",
        "git.checkpoint",
        "git.integrate",
        "git.push",
    }
)

# Read-classified ref-scoped operations never require milestone approval
# (discussion/analysis stays free); every side effect does.
# Read-only Git must stay usable while the current Milestone approval is no.
READ_CLASSIFIED_OPERATIONS: frozenset[str] = frozenset(
    {
        "github.issue.read",
        "github.issue.comments.read",
        "handoff.open",
        "workspace.read",
        "workspace.search",
        "git.status",
        "git.diff",
    }
)

REGISTRY_ENV = "AOTA_FORGE_REGISTRY"
RUNTIME_CONFIG_ENV = "AOTA_FORGE_RUNTIME_CONFIG"
ORIGIN_SESSION_REF_ENV = "AOTA_MCP_ORIGIN_SESSION_REF"
DEFAULT_ORIGIN_SESSION_REF = "opencode-unbound-chat"
# AF #59 M1 acceptance repair R5: trusted operator configuration for the
# bounded Git lifecycle family (integration branch + remote). These are
# Control-Plane facts supplied by the operator process environment, exactly
# like the registry/runtime-config locators above; the model can never supply
# or widen them. Absent => the lifecycle mutations have no authority and fail
# closed at dispatch after the approval gate.
GIT_INTEGRATION_BRANCH_ENV = "AOTA_GIT_INTEGRATION_BRANCH"
GIT_REMOTE_ENV = "AOTA_GIT_REMOTE"

_PLAN_ID_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


class RefScopedAuthorityError(ValueError):
    """Typed fail-closed ref-scoped authority error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def derive_plan_id_from_ref(plan_ref: str) -> str:
    """Deterministic canonical internal Plan ID for a validated Plan ref.

    Reuses the accepted canonical Plan ID grammar (one owner:
    ``core.plan.validation.is_plan_id``). The bound GitHub Issue reference
    remains the authority source; this string is a mechanical identity.
    """
    from aota_forge.core.plan.validation import is_plan_id
    from aota_forge.work_plane.github_tools import parse_plan_ref

    try:
        repo, _owner, issue_number = parse_plan_ref(plan_ref)
    except Exception as exc:  # noqa: BLE001 - invalid references fail closed
        raise RefScopedAuthorityError(
            "PLAN_REF_INVALID", f"not a canonical Plan reference: {plan_ref!r}"
        ) from exc
    token = _PLAN_ID_SANITIZE_RE.sub("_", f"{repo}_{issue_number}".lower()).strip("_")
    plan_id = f"plan_{token}"
    if not is_plan_id(plan_id):
        raise RefScopedAuthorityError(
            "PLAN_REF_INVALID", f"cannot derive a canonical Plan ID from reference {plan_ref!r}"
        )
    return plan_id


def _plan_context_field(document: Any, *keys: str) -> str:
    context = getattr(document, "project_context", None) or {}
    current = getattr(document, "current_fields", None) or {}
    for key in keys:
        for source in (context, current):
            value = source.get(key) if isinstance(source, Mapping) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _default_plan_loader(plan_ref: str) -> Any:
    from aota_forge.adapters.plan_authority.github_read import (
        GitHubPlanAuthorityReadAdapter,
    )
    from aota_forge.work_plane.github_tools import parse_plan_ref

    repo, _owner, issue_number = parse_plan_ref(plan_ref)
    return GitHubPlanAuthorityReadAdapter(repo=repo, issue_number=issue_number).load()


def read_live_plan(
    plan_ref: str,
    *,
    plan_loader: Callable[[str], Any] | None = None,
) -> tuple[Any, str | None, str | None]:
    """Read + normalize the live Portable Plan behind a canonical Plan ref.

    Returns ``(document, source_revision, source_digest)``. The accepted
    Plan-authority read adapter is the default source; the loader is
    injectable for deterministic tests only (never model input).
    """
    from aota_forge.core.plan.normalize import normalize_portable_plan
    from aota_forge.work_plane.github_tools import parse_plan_ref

    try:
        parse_plan_ref(plan_ref)
    except Exception as exc:  # noqa: BLE001
        raise RefScopedAuthorityError(
            "PLAN_REF_INVALID", f"not a canonical Plan reference: {plan_ref!r}"
        ) from exc
    loader = plan_loader or _default_plan_loader
    try:
        snapshot = loader(plan_ref)
    except RefScopedAuthorityError:
        raise
    except Exception as exc:  # noqa: BLE001 - live Plan read fails closed
        raise RefScopedAuthorityError(
            "PLAN_UNREADABLE", f"live Plan {plan_ref} could not be read"
        ) from exc
    body = getattr(snapshot, "body", None)
    revision = getattr(snapshot, "revision", None)
    snapshot_digest = getattr(snapshot, "digest", None)
    if not isinstance(body, str) or not body.strip():
        raise RefScopedAuthorityError("PLAN_UNREADABLE", "live Plan body is empty")
    try:
        document = normalize_portable_plan(body, source_revision=revision)
    except Exception as exc:  # noqa: BLE001
        raise RefScopedAuthorityError(
            "PLAN_UNREADABLE", f"live Plan {plan_ref} is not an AF-supported Portable Plan"
        ) from exc
    return document, revision, snapshot_digest


def current_milestone_approval(document: Any) -> tuple[str, bool | None]:
    """Current milestone + its explicit user approval truth (fail closed)."""
    milestone = str(getattr(document, "current_milestone", "") or "").strip()
    approvals = getattr(document, "milestone_approvals", None) or {}
    if milestone and milestone in approvals:
        approval: bool | None = bool(approvals[milestone])
    else:
        approval = None
    return milestone, approval


def resolve_plan_project(
    document: Any,
    *,
    registry_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> tuple[str, str, str]:
    """Trusted project binding for a live Plan document.

    Returns ``(project_id, source_repository, canonical_project_root)``.
    Reuses the canonical resolver + operator registry; the Plan's declared
    CANONICAL_PROJECT_ROOT must agree with the trusted resolved root.
    """
    from aota_forge.composition.project_binding import resolve_trusted_project_binding

    project_id = _plan_context_field(document, "CANONICAL_PROJECT_ID", "PROJECT_ID")
    source_repository = _plan_context_field(
        document, "CANONICAL_SOURCE_REPOSITORY", "SOURCE_REPOSITORY"
    )
    declared_root = _plan_context_field(document, "CANONICAL_PROJECT_ROOT", "KNOWN_SOURCE_ROOT")
    if not project_id or not source_repository:
        raise RefScopedAuthorityError(
            "PLAN_PROJECT_IDENTITY_MISSING",
            "live Plan does not declare PROJECT_ID/SOURCE_REPOSITORY; refusing to guess",
        )
    try:
        binding_evidence = resolve_trusted_project_binding(
            project_id=project_id,
            source_repository=source_repository,
            registry_path=registry_path,
            workspace_root=workspace_root if registry_path is None else None,
        )
    except Exception as exc:  # noqa: BLE001 - trusted project resolution fails closed
        raise RefScopedAuthorityError(
            "PROJECT_RESOLUTION_FAILED",
            f"trusted project resolution failed for {project_id!r}",
        ) from exc
    candidates = getattr(getattr(binding_evidence, "resolution", None), "candidates", ()) or ()
    if getattr(binding_evidence, "status", "") != "RESOLVED" or len(candidates) != 1:
        raise RefScopedAuthorityError(
            "PROJECT_RESOLUTION_FAILED",
            f"trusted project resolution for {project_id!r} is not singular",
        )
    repository_identity = getattr(binding_evidence, "repository_identity", None) or {}
    if not repository_identity.get("verified"):
        raise RefScopedAuthorityError(
            "PROJECT_RESOLUTION_FAILED",
            "source repository identity was not mechanically verified",
        )
    canonical_project_root = str(candidates[0].project_root)
    if declared_root and Path(declared_root).resolve() != Path(canonical_project_root).resolve():
        raise RefScopedAuthorityError(
            "PROJECT_ROOT_MISMATCH",
            f"Plan root {declared_root} does not match the trusted resolved root",
        )
    return project_id, source_repository, canonical_project_root


def unbound_origin_session_ref() -> str:
    """Bounded origin transport destination for unbound host sessions.

    Transport only (completion delivery destination); never authority. The
    host may supply an exact session/instance ref through the operator env
    channel (``AOTA_MCP_ORIGIN_SESSION_REF``); otherwise a bounded deterministic
    unbound marker is used.
    """
    value = os.environ.get(ORIGIN_SESSION_REF_ENV, "").strip()
    if value and len(value) <= 256:
        return value
    return DEFAULT_ORIGIN_SESSION_REF


class RefScopedAuthorityResolver:
    """Process-local, per-ref cached authority resolution for unbound sessions.

    One resolver instance per MCP child process. It composes the canonical
    thin task-main binding lazily, once per Plan ref; the approval gate itself
    always re-reads the live Plan (never cached). The trusted Git lifecycle
    configuration (integration branch/remote) comes from the operator process
    environment (or explicit trusted construction input), never from the
    model.
    """

    def __init__(
        self,
        *,
        repo_root: str,
        registry_path: str | Path | None = None,
        runtime_config_path: str | Path | None = None,
        plan_loader: Callable[[str], Any] | None = None,
        git_integration_branch: str | None = None,
        git_remote: str | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._registry_path = (
            str(registry_path) if registry_path is not None else os.environ.get(REGISTRY_ENV, "")
        )
        self._runtime_config_path = (
            str(runtime_config_path)
            if runtime_config_path is not None
            else os.environ.get(RUNTIME_CONFIG_ENV, "")
        )
        self._plan_loader = plan_loader
        self._git_integration_branch = (
            git_integration_branch
            if git_integration_branch is not None
            else os.environ.get(GIT_INTEGRATION_BRANCH_ENV, "")
        )
        self._git_remote = (
            git_remote if git_remote is not None else os.environ.get(GIT_REMOTE_ENV, "")
        )
        self._bindings: dict[str, Any] = {}

    # -- live Plan facts (re-read every operation) ---------------------------

    def live_plan_facts(self, plan_ref: str) -> dict[str, Any]:
        document, _revision, _digest = read_live_plan(plan_ref, plan_loader=self._plan_loader)
        milestone, approval = current_milestone_approval(document)
        return {
            "plan_ref": plan_ref,
            "plan_id": derive_plan_id_from_ref(plan_ref),
            "current_milestone": milestone,
            "milestone_user_approval_satisfied": approval,
        }

    def require_plan_approval(self, plan_ref: str) -> dict[str, Any]:
        """Operation-time approval gate: current server-side Plan fact."""
        facts = self.live_plan_facts(plan_ref)
        if facts.get("milestone_user_approval_satisfied") is not True:
            milestone = str(facts.get("current_milestone") or "").strip() or "unknown"
            raise RefScopedAuthorityError(
                "MILESTONE_APPROVAL_REQUIRED",
                f"milestone {milestone} user approval is not satisfied in the current "
                "authoritative Plan; no construction, source or governance mutation "
                "is authorized at this operation boundary",
            )
        return facts

    # -- canonical thin task-main binding (cached per Plan ref) --------------

    def task_main_binding_for_plan_ref(self, plan_ref: str, *, refresh: bool = False) -> Any:
        cached = self._bindings.get(plan_ref)
        if cached is not None and not refresh:
            return cached
        document, _revision, _digest = read_live_plan(plan_ref, plan_loader=self._plan_loader)
        project_id, source_repository, project_root = resolve_plan_project(
            document,
            registry_path=self._registry_path or None,
            workspace_root=self._repo_root,
        )
        plan_id = derive_plan_id_from_ref(plan_ref)
        if not self._runtime_config_path or not Path(self._runtime_config_path).is_file():
            raise RefScopedAuthorityError(
                "RUNTIME_CONFIG_MISSING",
                "operator runtime config is required to mint ref-scoped task-main "
                "authority (AOTA_FORGE_RUNTIME_CONFIG)",
            )
        from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

        try:
            host = compose_thin_task_main_host(
                worktree_root=project_root,
                project_id=project_id,
                worktree_id=f"{project_id}-unbound",
                runtime_config_path=self._runtime_config_path,
                origin_task_main_session_ref=unbound_origin_session_ref(),
                plan_ref=plan_ref,
                source_repository=source_repository,
                registry_path=self._registry_path or None,
                plan_id=plan_id,
                # Trusted operator configuration (env or explicit construction
                # input): the bounded Git lifecycle family gains authority only
                # when the operator configured the integration branch (and
                # remote for push). Unconfigured => authorities absent => the
                # existing dispatch fails closed.
                git_integration_branch=self._git_integration_branch or None,
                git_remote=self._git_remote or None,
            )
        except Exception as exc:  # noqa: BLE001 - composition fails closed
            raise RefScopedAuthorityError(
                "AUTHORITY_RESOLUTION_FAILED",
                f"ref-scoped task-main authority could not be composed: {exc}",
            ) from exc
        self._bindings[plan_ref] = host.trusted_binding
        return host.trusted_binding


__all__ = [
    "PLAN_REF_SCOPED_OPERATIONS",
    "READ_CLASSIFIED_OPERATIONS",
    "REF_SCOPED_AUTHORITY",
    "SESSION_BINDING_REQUIRED",
    "REF_IS_AUTHORITY_LOCATOR",
    "REF_IS_AUTHORITY_SOURCE",
    "NEW_AUTHORITY_ENGINE_CREATED",
    "APPROVAL_IS_CURRENT_SERVER_SIDE_FACT",
    "APPROVAL_IS_SESSION_STATE",
    "SECOND_SESSION_SYSTEM_CREATED",
    "SECOND_MCP_SERVER_CREATED",
    "REGISTRY_ENV",
    "RUNTIME_CONFIG_ENV",
    "ORIGIN_SESSION_REF_ENV",
    "DEFAULT_ORIGIN_SESSION_REF",
    "GIT_INTEGRATION_BRANCH_ENV",
    "GIT_REMOTE_ENV",
    "RefScopedAuthorityError",
    "RefScopedAuthorityResolver",
    "derive_plan_id_from_ref",
    "read_live_plan",
    "current_milestone_approval",
    "resolve_plan_project",
    "unbound_origin_session_ref",
]
