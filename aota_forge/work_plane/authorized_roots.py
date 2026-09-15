"""AF #55 M2/W1 — Authorized Root Binding: root_ref capability model.

Converges the accepted single-worktree workspace authority into an explicit
*authorized root set*: task-main receives ``project-main`` + ``active-worktree``
read roots; workers receive only the assigned ``active-worktree``. Every root
is mechanically derived from the already-trusted ``WorktreeSandboxBoundary``
(``project_root`` / ``worktree_root``); the model selects a granted
``root_ref`` by name and can never supply a path.

This is a bounded capability carrier, not a new sandbox subsystem, resource
registry or authority engine. Physical containment, symlink rejection and
TOCTOU revalidation stay in the existing
``resolve_worktree_resource`` / ``WorktreeSandboxBoundary`` seams.

Invariants
----------
* AUTHORIZED_MULTI_ROOT_READ_MODEL_MATERIALIZED=yes
* ROOT_REF_CAPABILITY_MECHANICALLY_ENFORCED=yes
* ROOT_REF_IS_BOUNDED_NAME_NOT_PATH=yes
* ROOTS_DERIVED_FROM_TRUSTED_SANDBOX_ONLY=yes
* MODEL_NOMINATES_ROOT_PATH=no
* MODEL_CAN_MINT_ROOT_SET=no
* WORKSPACE_ROOTS_ARE_AF_AUTHORIZED_SET=yes
* WORKSPACE_ROOTS_ARE_CWD=no
* ARBITRARY_HOST_PATH_REACHABLE=no
* UNRELATED_SIBLING_PROJECT_REACHABLE=no
* CROSS_ROOT_ACCESS_REQUIRES_EXPLICIT_GRANT=yes
* WORKER_INHERITS_TASK_MAIN_ROOTS=no
* WORKER_EXTRA_ROOT_REQUIRES_EXPLICIT_GRANT=yes
* NARROW_WRITE=yes (write capability only on active-worktree)
* GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED=no
* LOCAL_GOVERNANCE_ROOT_SUPPORTED=no (prepared abstraction only)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Canonical root refs
# ---------------------------------------------------------------------------
# Instantiable in this Plan (mechanically derived from the trusted sandbox):
ROOT_REF_PROJECT_MAIN = "project-main"
ROOT_REF_ACTIVE_WORKTREE = "active-worktree"

# Prepared abstraction only (Governance 2.0 / future evidence roots). These
# never instantiate in this Plan and are never grantable through a model
# argument; the constants exist so the abstraction shape is honest.
ROOT_REF_AUTHORIZED_EVIDENCE = "authorized-evidence"
ROOT_REF_LOCAL_GOVERNANCE = "local-governance"

INSTANTIABLE_ROOT_REFS: tuple[str, ...] = (
    ROOT_REF_PROJECT_MAIN,
    ROOT_REF_ACTIVE_WORKTREE,
)
PREPARED_ROOT_REFS: tuple[str, ...] = (
    ROOT_REF_AUTHORIZED_EVIDENCE,
    ROOT_REF_LOCAL_GOVERNANCE,
)

CAPABILITY_SEARCH = "search"
CAPABILITY_READ = "read"
CAPABILITY_WRITE = "write"
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset(
    {CAPABILITY_SEARCH, CAPABILITY_READ, CAPABILITY_WRITE}
)

SOURCE_TRUSTED_SANDBOX = "trusted_sandbox"
SOURCE_EXPLICIT_GRANT = "explicit_grant"

ROOT_SET_SESSION_TASK_MAIN = "task-main"
ROOT_SET_SESSION_WORKER = "worker"
ROOT_SET_SESSION_SINGLE = "single"
SUPPORTED_ROOT_SET_SESSIONS: frozenset[str] = frozenset(
    {ROOT_SET_SESSION_TASK_MAIN, ROOT_SET_SESSION_WORKER, ROOT_SET_SESSION_SINGLE}
)

_ROOT_REF_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_PATH_LENGTH = 4096

# Deterministic multi-root traversal order for search: the active worktree is
# traversed first so the accepted single-root behavior is preserved when the
# project root and worktree coincide (deduplicated by canonical path).
_SEARCH_PRIORITY: dict[str, int] = {
    ROOT_REF_ACTIVE_WORKTREE: 0,
    ROOT_REF_PROJECT_MAIN: 1,
}


# ---------------------------------------------------------------------------
# Errors — typed codes consumed by the workspace providers (fail closed)
# ---------------------------------------------------------------------------


class AuthorizedRootError(ValueError):
    """Base fail-closed error for the authorized root model."""

    code = "AUTHORIZED_ROOT_ERROR"


class AuthorizedRootReferenceError(AuthorizedRootError):
    """Malformed root_ref input (must be a bounded name, never a path)."""

    code = "INVALID_ROOT_REF"


class AuthorizedRootUnknownError(AuthorizedRootError):
    """root_ref is not granted in this session's authorized root set."""

    code = "AUTHORIZED_ROOT_UNKNOWN"


class AuthorizedRootCapabilityError(AuthorizedRootError):
    """The granted root does not carry the requested capability."""

    code = "AUTHORIZED_ROOT_CAPABILITY_DENIED"


class AuthorizedRootSetError(AuthorizedRootError):
    """Invalid authorized root set (bad shape or not sandbox-derived)."""

    code = "AUTHORIZED_ROOT_SET_INVALID"


def _validate_bounded_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise AuthorizedRootSetError(f"{label} must be str, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate or not _BOUNDED_ID_RE.fullmatch(candidate):
        raise AuthorizedRootSetError(f"{label} must be a bounded trusted identifier")
    return candidate


def _canonical_directory(raw_path: object, *, label: str) -> str:
    if not isinstance(raw_path, str) or type(raw_path) is not str:
        raise AuthorizedRootSetError(f"{label} must be a filesystem path string")
    if not raw_path or "\x00" in raw_path or len(raw_path) > _MAX_PATH_LENGTH:
        raise AuthorizedRootSetError(f"{label} must be a bounded non-empty path")
    path = Path(raw_path)
    if not path.is_absolute():
        raise AuthorizedRootSetError(f"{label} must be an absolute path")
    try:
        if path.is_symlink():
            raise AuthorizedRootSetError(f"{label} must not be a symlink")
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise AuthorizedRootSetError(f"{label} unreadable: {exc}") from exc
    if canonical != path:
        raise AuthorizedRootSetError(f"{label} must already be canonical: {raw_path!r}")
    if not canonical.is_dir():
        raise AuthorizedRootSetError(f"{label} must be a directory: {canonical!r}")
    return str(canonical)


# ---------------------------------------------------------------------------
# Authorized root
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizedRoot:
    """One mechanically-granted filesystem root for this session.

    A root carries a bounded ``root_ref`` name (never a model-supplied path),
    a canonical physical ``root_path`` derived from the trusted sandbox, and
    the capability set mechanically enforced by the workspace providers.
    """

    root_ref: str
    root_kind: str
    root_path: str
    capabilities: frozenset[str]
    project_id: str
    worktree_id: str = ""
    source: str = SOURCE_TRUSTED_SANDBOX

    def __post_init__(self) -> None:
        if not isinstance(self.root_ref, str) or not _ROOT_REF_RE.fullmatch(self.root_ref):
            raise AuthorizedRootSetError(f"root_ref must be a bounded lowercase name, got {self.root_ref!r}")
        if self.root_ref not in INSTANTIABLE_ROOT_REFS:
            raise AuthorizedRootSetError(
                f"root_ref {self.root_ref!r} is not instantiable in this Plan "
                f"(supported: {list(INSTANTIABLE_ROOT_REFS)}, prepared-not-implemented: {list(PREPARED_ROOT_REFS)})"
            )
        if self.root_kind != self.root_ref:
            raise AuthorizedRootSetError(
                f"root_kind must equal the canonical root_ref, got {self.root_kind!r} != {self.root_ref!r}"
            )
        if not isinstance(self.capabilities, frozenset):
            raise AuthorizedRootSetError("capabilities must be a frozenset")
        unknown = set(self.capabilities) - SUPPORTED_CAPABILITIES
        if unknown:
            raise AuthorizedRootSetError(f"unknown capability names: {sorted(unknown)}")
        if not self.capabilities:
            raise AuthorizedRootSetError("a root must carry at least one capability")
        if CAPABILITY_WRITE in self.capabilities and self.root_kind != ROOT_REF_ACTIVE_WORKTREE:
            raise AuthorizedRootSetError(
                "write capability is only grantable on the active-worktree root (narrow write)"
            )
        if self.source not in (SOURCE_TRUSTED_SANDBOX, SOURCE_EXPLICIT_GRANT):
            raise AuthorizedRootSetError(f"invalid root source {self.source!r}")
        if self.source == SOURCE_EXPLICIT_GRANT and self.root_kind != ROOT_REF_ACTIVE_WORKTREE:
            # Explicitly granted supporting roots are a future extension; in
            # this Plan only the canonical sandbox-derived roots are trusted.
            raise AuthorizedRootSetError(
                "explicit grant roots are not instantiable in this Plan"
            )
        self_project = _validate_bounded_identifier(self.project_id, label="project_id")
        object.__setattr__(self, "project_id", self_project)
        if self.worktree_id:
            object.__setattr__(
                self, "worktree_id", _validate_bounded_identifier(self.worktree_id, label="worktree_id")
            )

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def public_dict(self) -> dict[str, Any]:
        """Model-visible projection: bounded name + capabilities, never a path."""
        return {
            "root_ref": self.root_ref,
            "kind": self.root_kind,
            "capabilities": sorted(self.capabilities),
        }

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "root_ref": self.root_ref,
            "kind": self.root_kind,
            "root_path": self.root_path,
            "capabilities": sorted(self.capabilities),
            "project_id": self.project_id,
            "worktree_id": self.worktree_id,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Authorized root set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizedRootSet:
    """The bounded AF-authorized filesystem roots for one session.

    Minted only by trusted composition (thin task-main host / worker slice)
    from an already-trusted ``WorktreeSandboxBoundary``; the model can never
    construct or widen it. The set is the single mechanical answer to
    "which roots may workspace.* reach" — never cwd, never the host tree.
    """

    session_kind: str
    roots: tuple[AuthorizedRoot, ...]

    def __post_init__(self) -> None:
        if self.session_kind not in SUPPORTED_ROOT_SET_SESSIONS:
            raise AuthorizedRootSetError(
                f"session_kind must be one of {sorted(SUPPORTED_ROOT_SET_SESSIONS)}, got {self.session_kind!r}"
            )
        if not isinstance(self.roots, tuple) or not self.roots:
            raise AuthorizedRootSetError("roots must be a non-empty tuple")
        seen: set[str] = set()
        project_ids: set[str] = set()
        for idx, root in enumerate(self.roots):
            if not isinstance(root, AuthorizedRoot):
                raise AuthorizedRootSetError(f"roots[{idx}] must be AuthorizedRoot, got {type(root).__name__}")
            if root.root_ref in seen:
                raise AuthorizedRootSetError(f"duplicate root_ref {root.root_ref!r}")
            seen.add(root.root_ref)
            project_ids.add(root.project_id)
        if len(project_ids) != 1:
            raise AuthorizedRootSetError("all roots in a set must share one project_id")
        if self.session_kind == ROOT_SET_SESSION_WORKER and ROOT_REF_PROJECT_MAIN in seen:
            raise AuthorizedRootSetError(
                "worker root sets must not include project-main (worker does not inherit task-main roots)"
            )

    def names(self) -> tuple[str, ...]:
        return tuple(root.root_ref for root in self.roots)

    def get(self, root_ref: str) -> AuthorizedRoot | None:
        for root in self.roots:
            if root.root_ref == root_ref:
                return root
        return None

    def read_roots(self) -> tuple[AuthorizedRoot, ...]:
        return tuple(root for root in self.roots if root.has_capability(CAPABILITY_READ))

    def search_roots(self) -> tuple[AuthorizedRoot, ...]:
        """Search-capable roots in deterministic traversal order.

        active-worktree is traversed first (the accepted single-root default
        behavior is preserved when roots coincide), then project-main.
        """
        candidates = [root for root in self.roots if root.has_capability(CAPABILITY_SEARCH)]
        return tuple(
            sorted(candidates, key=lambda root: (_SEARCH_PRIORITY.get(root.root_kind, 9), root.root_ref))
        )

    def write_roots(self) -> tuple[AuthorizedRoot, ...]:
        return tuple(root for root in self.roots if root.has_capability(CAPABILITY_WRITE))

    def default_read_root(self) -> AuthorizedRoot:
        """Deterministic trusted default read root: the bound active-worktree.

        There is no heuristic fallback (no first/nearest/newest pick): when the
        session carries no read-capable active-worktree, omission is ambiguous
        and fails closed with a typed error — the caller must pass an explicit
        ``root_ref``.
        """
        for root in self.roots:
            if root.root_kind == ROOT_REF_ACTIVE_WORKTREE and root.has_capability(CAPABILITY_READ):
                return root
        raise AuthorizedRootCapabilityError(
            "no read-capable active-worktree default is granted in this session; "
            "an explicit root_ref is required"
        )

    def default_write_root(self) -> AuthorizedRoot:
        for root in self.roots:
            if root.root_kind == ROOT_REF_ACTIVE_WORKTREE and root.has_capability(CAPABILITY_WRITE):
                return root
        raise AuthorizedRootCapabilityError("no write-capable root is granted in this session")

    def public_roots(self) -> list[dict[str, Any]]:
        return [root.public_dict() for root in self.roots]

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "session_kind": self.session_kind,
            "roots": [root.canonical_dict() for root in self.roots],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Resolution — mechanical capability enforcement
# ---------------------------------------------------------------------------


def validate_root_ref_syntax(root_ref: object) -> str:
    if not isinstance(root_ref, str) or type(root_ref) is not str:
        raise AuthorizedRootReferenceError(
            f"root_ref must be a bounded name string, got {type(root_ref).__name__}"
        )
    candidate = root_ref.strip()
    if candidate != root_ref or not _ROOT_REF_RE.fullmatch(candidate):
        raise AuthorizedRootReferenceError(f"root_ref must be a bounded name, got {root_ref!r}")
    return candidate


def resolve_authorized_root(
    roots: AuthorizedRootSet,
    root_ref: str,
    *,
    capability: str | None = None,
) -> AuthorizedRoot:
    """Resolve one granted root_ref and enforce the requested capability.

    Mechanically fail closed: unknown name, path-shaped input and missing
    capability all raise typed errors before any filesystem access.
    """
    if not isinstance(roots, AuthorizedRootSet):
        raise AuthorizedRootSetError(f"roots must be AuthorizedRootSet, got {type(roots).__name__}")
    name = validate_root_ref_syntax(root_ref)
    root = roots.get(name)
    if root is None:
        raise AuthorizedRootUnknownError(
            f"root_ref {name!r} is not granted in this session (granted: {list(roots.names())})"
        )
    if capability is not None and not root.has_capability(capability):
        raise AuthorizedRootCapabilityError(
            f"root_ref {name!r} does not carry capability {capability!r} (has: {sorted(root.capabilities)})"
        )
    return root


# ---------------------------------------------------------------------------
# Trusted factories — derived exclusively from the trusted sandbox
# ---------------------------------------------------------------------------


def _sandbox_root(sandbox: WorktreeSandboxBoundary, *, label: str) -> str:
    return _canonical_directory(getattr(sandbox, label, None), label=f"sandbox.{label}")


def validate_root_set_against_sandbox(
    roots: AuthorizedRootSet, sandbox: WorktreeSandboxBoundary
) -> None:
    """Mechanically prove a root set is the trusted sandbox's own roots.

    project-main must be the canonical ``sandbox.project_root``;
    active-worktree must be the canonical ``sandbox.worktree_root``. Nothing
    else (no sibling project, no host path, no cwd) is admissible.
    """
    if not isinstance(roots, AuthorizedRootSet):
        raise AuthorizedRootSetError(f"roots must be AuthorizedRootSet, got {type(roots).__name__}")
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    project_root = _sandbox_root(sandbox, label="project_root")
    worktree_root = _sandbox_root(sandbox, label="worktree_root")
    for root in roots.roots:
        if root.project_id != sandbox.project_id:
            raise AuthorizedRootSetError(
                f"root {root.root_ref!r} project_id does not match sandbox project"
            )
        if root.root_kind == ROOT_REF_PROJECT_MAIN:
            if root.root_path != project_root:
                raise AuthorizedRootSetError(
                    "project-main root must be the canonical sandbox project_root (no arbitrary root)"
                )
        elif root.root_kind == ROOT_REF_ACTIVE_WORKTREE:
            if root.root_path != worktree_root:
                raise AuthorizedRootSetError(
                    "active-worktree root must be the canonical sandbox worktree_root"
                )
            if root.worktree_id != sandbox.worktree_id:
                raise AuthorizedRootSetError(
                    "active-worktree root worktree_id does not match sandbox worktree"
                )
        else:
            raise AuthorizedRootSetError(f"root kind {root.root_kind!r} is not sandbox-derivable")


def authorized_roots_from_sandbox(
    sandbox: WorktreeSandboxBoundary,
) -> tuple[str, str]:
    project_root = _sandbox_root(sandbox, label="project_root")
    worktree_root = _sandbox_root(sandbox, label="worktree_root")
    return project_root, worktree_root


def authorized_roots_for_task_main(sandbox: WorktreeSandboxBoundary) -> AuthorizedRootSet:
    """Task-main default roots: project-main (read/search) + active-worktree
    (read/search/write). Derived exclusively from the trusted sandbox."""
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    project_root, worktree_root = authorized_roots_from_sandbox(sandbox)
    roots = (
        AuthorizedRoot(
            root_ref=ROOT_REF_PROJECT_MAIN,
            root_kind=ROOT_REF_PROJECT_MAIN,
            root_path=project_root,
            capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ}),
            project_id=sandbox.project_id,
        ),
        AuthorizedRoot(
            root_ref=ROOT_REF_ACTIVE_WORKTREE,
            root_kind=ROOT_REF_ACTIVE_WORKTREE,
            root_path=worktree_root,
            capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ, CAPABILITY_WRITE}),
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
        ),
    )
    root_set = AuthorizedRootSet(session_kind=ROOT_SET_SESSION_TASK_MAIN, roots=roots)
    validate_root_set_against_sandbox(root_set, sandbox)
    return root_set


def authorized_roots_for_worker(sandbox: WorktreeSandboxBoundary) -> AuthorizedRootSet:
    """Worker default roots: the assigned active-worktree only.

    Workers do not inherit task-main roots; supporting roots require an
    explicit future grant channel and are not instantiable in this Plan.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    _, worktree_root = authorized_roots_from_sandbox(sandbox)
    roots = (
        AuthorizedRoot(
            root_ref=ROOT_REF_ACTIVE_WORKTREE,
            root_kind=ROOT_REF_ACTIVE_WORKTREE,
            root_path=worktree_root,
            capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ, CAPABILITY_WRITE}),
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
        ),
    )
    root_set = AuthorizedRootSet(session_kind=ROOT_SET_SESSION_WORKER, roots=roots)
    validate_root_set_against_sandbox(root_set, sandbox)
    return root_set


def authorized_roots_single_root(sandbox: WorktreeSandboxBoundary) -> AuthorizedRootSet:
    """Legacy-compatible single-root set (active-worktree only).

    Used when a pre-existing authority did not declare an authorized root
    set; the session keeps exactly the accepted single-worktree behavior.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    _, worktree_root = authorized_roots_from_sandbox(sandbox)
    roots = (
        AuthorizedRoot(
            root_ref=ROOT_REF_ACTIVE_WORKTREE,
            root_kind=ROOT_REF_ACTIVE_WORKTREE,
            root_path=worktree_root,
            capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ, CAPABILITY_WRITE}),
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
        ),
    )
    root_set = AuthorizedRootSet(session_kind=ROOT_SET_SESSION_SINGLE, roots=roots)
    validate_root_set_against_sandbox(root_set, sandbox)
    return root_set


# ---------------------------------------------------------------------------
# Trusted project context projection (§8)
# ---------------------------------------------------------------------------


def build_trusted_project_context_projection(
    *,
    project_id: str,
    worktree_id: str,
    roots: AuthorizedRootSet,
    plan_ref: str = "",
    governing_repository: str = "",
    source_repository: str = "",
) -> dict[str, Any]:
    """Build the §8 trusted task-main/worker context projection.

    Clearly separates PLAN_AUTHORITY (plan_ref + governing repository),
    IMPLEMENTATION_PROJECT (project_id + source_repository + project root)
    and ACTIVE_WORKTREE (worktree_id + worktree root), so the model never has
    to infer the relationship. The projection carries bounded root refs and
    capabilities only — never physical paths.
    """
    if not isinstance(roots, AuthorizedRootSet):
        raise AuthorizedRootSetError(f"roots must be AuthorizedRootSet, got {type(roots).__name__}")
    pid = _validate_bounded_identifier(project_id, label="project_id")
    wid = _validate_bounded_identifier(worktree_id, label="worktree_id")
    if roots.roots[0].project_id != pid:
        raise AuthorizedRootSetError("projection project_id does not match the authorized root set")
    plan = str(plan_ref).strip()
    governing = str(governing_repository).strip()
    source = str(source_repository).strip()
    if bool(plan) != bool(governing):
        raise AuthorizedRootSetError(
            "trusted projection requires plan_ref and governing_repository together (no partial Plan identity)"
        )
    project_read_root = (
        ROOT_REF_PROJECT_MAIN if roots.get(ROOT_REF_PROJECT_MAIN) is not None else ROOT_REF_ACTIVE_WORKTREE
    )
    project: dict[str, Any] = {"project_id": pid, "root_ref": project_read_root}
    if source:
        project["source_repository"] = source
    return {
        "plan": (
            {"plan_ref": plan, "governing_repository": governing} if plan else None
        ),
        "project": project,
        "worktree": {
            "worktree_id": wid,
            "root_ref": ROOT_REF_ACTIVE_WORKTREE if roots.get(ROOT_REF_ACTIVE_WORKTREE) is not None else "",
        },
        # Bounded root_ref -> capability projection; never physical paths.
        "roots": {root.root_ref: sorted(root.capabilities) for root in roots.roots},
    }


# ---------------------------------------------------------------------------
# Public invariant flags (tests / downstream seam)
# ---------------------------------------------------------------------------

AUTHORIZED_MULTI_ROOT_READ_MODEL_MATERIALIZED: bool = True
ROOT_REF_CAPABILITY_MECHANICALLY_ENFORCED: bool = True
ROOT_REF_IS_BOUNDED_NAME_NOT_PATH: bool = True
ROOTS_DERIVED_FROM_TRUSTED_SANDBOX_ONLY: bool = True
MODEL_NOMINATES_ROOT_PATH: bool = False
MODEL_CAN_MINT_ROOT_SET: bool = False
WORKSPACE_ROOTS_ARE_AF_AUTHORIZED_SET: bool = True
WORKSPACE_ROOTS_ARE_CWD: bool = False
ARBITRARY_HOST_PATH_REACHABLE: bool = False
UNRELATED_SIBLING_PROJECT_REACHABLE: bool = False
CROSS_ROOT_ACCESS_REQUIRES_EXPLICIT_GRANT: bool = True
WORKER_INHERITS_TASK_MAIN_ROOTS: bool = False
WORKER_EXTRA_ROOT_REQUIRES_EXPLICIT_GRANT: bool = True
NARROW_WRITE: bool = True
SECOND_SANDBOX_SUBSYSTEM_CREATED: bool = False
SECOND_AUTHORITY_ENGINE_CREATED: bool = False
GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED: bool = False
LOCAL_GOVERNANCE_ROOT_SUPPORTED: bool = False
PREPARED_ROOT_REFS_INSTANTIATED: bool = False

ROOT_REF_CAPABILITY_SEAM: str = (
    "AuthorizedRootSet(WorktreeSandboxBoundary.project_root/worktree_root) + "
    "resolve_authorized_root + workspace provider capability enforcement"
)

__all__ = [
    "ROOT_REF_PROJECT_MAIN",
    "ROOT_REF_ACTIVE_WORKTREE",
    "ROOT_REF_AUTHORIZED_EVIDENCE",
    "ROOT_REF_LOCAL_GOVERNANCE",
    "INSTANTIABLE_ROOT_REFS",
    "PREPARED_ROOT_REFS",
    "CAPABILITY_SEARCH",
    "CAPABILITY_READ",
    "CAPABILITY_WRITE",
    "SUPPORTED_CAPABILITIES",
    "SOURCE_TRUSTED_SANDBOX",
    "SOURCE_EXPLICIT_GRANT",
    "ROOT_SET_SESSION_TASK_MAIN",
    "ROOT_SET_SESSION_WORKER",
    "ROOT_SET_SESSION_SINGLE",
    "SUPPORTED_ROOT_SET_SESSIONS",
    "AuthorizedRootError",
    "AuthorizedRootReferenceError",
    "AuthorizedRootUnknownError",
    "AuthorizedRootCapabilityError",
    "AuthorizedRootSetError",
    "AuthorizedRoot",
    "AuthorizedRootSet",
    "validate_root_ref_syntax",
    "resolve_authorized_root",
    "validate_root_set_against_sandbox",
    "authorized_roots_from_sandbox",
    "authorized_roots_for_task_main",
    "authorized_roots_for_worker",
    "authorized_roots_single_root",
    "build_trusted_project_context_projection",
    "AUTHORIZED_MULTI_ROOT_READ_MODEL_MATERIALIZED",
    "ROOT_REF_CAPABILITY_MECHANICALLY_ENFORCED",
    "ROOT_REF_IS_BOUNDED_NAME_NOT_PATH",
    "ROOTS_DERIVED_FROM_TRUSTED_SANDBOX_ONLY",
    "MODEL_NOMINATES_ROOT_PATH",
    "MODEL_CAN_MINT_ROOT_SET",
    "WORKSPACE_ROOTS_ARE_AF_AUTHORIZED_SET",
    "WORKSPACE_ROOTS_ARE_CWD",
    "ARBITRARY_HOST_PATH_REACHABLE",
    "UNRELATED_SIBLING_PROJECT_REACHABLE",
    "CROSS_ROOT_ACCESS_REQUIRES_EXPLICIT_GRANT",
    "WORKER_INHERITS_TASK_MAIN_ROOTS",
    "WORKER_EXTRA_ROOT_REQUIRES_EXPLICIT_GRANT",
    "NARROW_WRITE",
    "SECOND_SANDBOX_SUBSYSTEM_CREATED",
    "SECOND_AUTHORITY_ENGINE_CREATED",
    "GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED",
    "LOCAL_GOVERNANCE_ROOT_SUPPORTED",
    "PREPARED_ROOT_REFS_INSTANTIATED",
    "ROOT_REF_CAPABILITY_SEAM",
]
