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
* GOVERNANCE_2_0_LOCAL_PLAN_STORE_IMPLEMENTED=no (the Project Governance
  Store is AF #57 M1/W3 scope; W2 materializes only the trusted root +
  Plan authority adapter behind the existing PlanAuthorityMutationPort)
* LOCAL_GOVERNANCE_ROOT_SUPPORTED=yes (AF #57 M1/W2: project-scoped trusted
  binding only; the whole plans base is never grantable)
* LOCAL_GOVERNANCE_ROOT_PROJECT_SCOPED=yes
* LOCAL_GOVERNANCE_ROOT_REQUIRES_TRUSTED_BINDING=yes
* LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY=no
* LOCAL_GOVERNANCE_MODEL_NOMINATED_PATH=no

AF #57 M2/W4 governed-read extensions:

* AUTHORIZED_EVIDENCE_ROOT_SUPPORTED=yes (project-scoped trusted evidence
  base + canonical project identity binding; read/search only, task-main
  only; never model path, never the whole global evidence base)
* AUTHORIZED_EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING=yes
* AUTHORIZED_EVIDENCE_AGENT_WRITE_CAPABILITY=no
* AUTHORIZED_EVIDENCE_MODEL_NOMINATED_PATH=no
* CROSS_PROJECT_GRANT_ROOT_SUPPORTED=yes (a foreign project-main root appears
  only from a materialized trusted CrossProjectGrant binding; read/search
  only; grant-scoped, never a global weakening)
* CROSS_PROJECT_GRANT_ROOT_REQUIRES_EXPLICIT_GRANT=yes
* CROSS_PROJECT_GRANT_DEFAULT_DENIED=yes
* CROSS_PROJECT_GRANT_WRITE_CAPABILITY=no
* DEFAULT_SIBLING_ISOLATION_WEAKENED=no (normal roots keep the same
  sandbox/project checks; the foreign exception is grant-specific)
* MODEL_CAN_MINT_GRANT_ROOT=no
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Canonical root refs
# ---------------------------------------------------------------------------
# Instantiable in this Plan (mechanically derived from the trusted sandbox):
ROOT_REF_PROJECT_MAIN = "project-main"
ROOT_REF_ACTIVE_WORKTREE = "active-worktree"

# AF #57 M1/W2: the project-scoped trusted governance root.  It is never
# derived from the sandbox and never instantiable from a model-supplied path;
# it requires an explicit LocalGovernanceRootBinding built from a trusted
# operator governance base + the canonical project identity.
ROOT_REF_LOCAL_GOVERNANCE = "local-governance"

# AF #57 M2/W4: the project-scoped trusted evidence root.  Like the
# governance root it is never derived from the sandbox and never instantiable
# from a model-supplied path; it requires an explicit
# AuthorizedEvidenceRootBinding built from a trusted evidence base + the
# canonical project identity.  The whole global evidence base and sibling
# project evidence are never grantable.
ROOT_REF_AUTHORIZED_EVIDENCE = "authorized-evidence"

INSTANTIABLE_ROOT_REFS: tuple[str, ...] = (
    ROOT_REF_PROJECT_MAIN,
    ROOT_REF_ACTIVE_WORKTREE,
    ROOT_REF_LOCAL_GOVERNANCE,
    ROOT_REF_AUTHORIZED_EVIDENCE,
)
# AF #57 M2/W4: every previously prepared ref now has a trusted instantiation
# seam, so the prepared set is empty and the historical invariant flips
# truthfully.
PREPARED_ROOT_REFS: tuple[str, ...] = ()

# Grant-specific foreign root kinds.  Only kinds with a real W4 consumer are
# admitted; root_kind remains the semantic kind of the granted root, while
# root_ref is the grant-bound bounded name.
GRANTABLE_ROOT_KINDS: tuple[str, ...] = (ROOT_REF_PROJECT_MAIN,)

CAPABILITY_SEARCH = "search"
CAPABILITY_READ = "read"
CAPABILITY_WRITE = "write"
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset(
    {CAPABILITY_SEARCH, CAPABILITY_READ, CAPABILITY_WRITE}
)
GRANT_SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({CAPABILITY_SEARCH, CAPABILITY_READ})

SOURCE_TRUSTED_SANDBOX = "trusted_sandbox"
SOURCE_EXPLICIT_GRANT = "explicit_grant"
SOURCE_TRUSTED_GOVERNANCE = "trusted_governance"
SOURCE_TRUSTED_EVIDENCE = "trusted_evidence"

ROOT_SET_SESSION_TASK_MAIN = "task-main"
ROOT_SET_SESSION_WORKER = "worker"
ROOT_SET_SESSION_SINGLE = "single"
SUPPORTED_ROOT_SET_SESSIONS: frozenset[str] = frozenset(
    {ROOT_SET_SESSION_TASK_MAIN, ROOT_SET_SESSION_WORKER, ROOT_SET_SESSION_SINGLE}
)

_GRANT_ID_RE = re.compile(r"^grant-[a-z0-9][a-z0-9-]{3,58}$")
_SCOPE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SCOPE_LENGTH = 512
_MAX_SCOPE_SEGMENTS = 64

_ROOT_REF_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_BOUNDED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_PATH_LENGTH = 4096
_MAX_PROJECT_ID_LENGTH = 96

# Deterministic multi-root traversal order for search: the active worktree is
# traversed first so the accepted single-root behavior is preserved when the
# project root and worktree coincide (deduplicated by canonical path).
_SEARCH_PRIORITY: dict[str, int] = {
    ROOT_REF_ACTIVE_WORKTREE: 0,
    ROOT_REF_PROJECT_MAIN: 1,
    ROOT_REF_LOCAL_GOVERNANCE: 2,
    ROOT_REF_AUTHORIZED_EVIDENCE: 3,
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
# Trusted local governance binding (AF #57 M1/W2 §3)
# ---------------------------------------------------------------------------


class LocalGovernanceRootError(AuthorizedRootError):
    """The trusted governance binding or the local-governance root is invalid."""

    code = "LOCAL_GOVERNANCE_ROOT_INVALID"


class AuthorizedEvidenceRootError(AuthorizedRootError):
    """The trusted evidence binding or the authorized-evidence root is invalid."""

    code = "AUTHORIZED_EVIDENCE_ROOT_INVALID"


class CrossProjectGrantRootError(AuthorizedRootError):
    """The cross-project grant binding or the granted foreign root is invalid."""

    code = "CROSS_PROJECT_GRANT_ROOT_INVALID"


def _require_project_identity(
    value: object, *, label: str, error_cls: type[AuthorizedRootError]
) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise error_cls(f"{label} must be str, got {type(value).__name__}")
    if value != value.strip() or not value or len(value) > _MAX_PROJECT_ID_LENGTH:
        raise error_cls(f"{label} must be the canonical bounded project identity")
    if not PROJECT_ID_RE.fullmatch(value):
        raise error_cls(
            f"{label} must use the canonical project identity grammar (manifest project id)"
        )
    return value


def _canonical_project_id(value: object) -> str:
    return _require_project_identity(
        value, label="project_id", error_cls=LocalGovernanceRootError
    )


def validate_bounded_grant_scope(value: object) -> str:
    """Validate a mechanically enforceable relative grant scope.

    Empty scope means the whole granted root.  No glob/DSL system is
    introduced; traversal, absolute and symlink-shaped input fail closed.
    """
    if not isinstance(value, str) or type(value) is not str:
        raise CrossProjectGrantRootError("bounded_scope must be str")
    if value != value.strip() or "\x00" in value or "\\" in value:
        raise CrossProjectGrantRootError("bounded_scope must be a canonical relative subtree")
    if len(value) > _MAX_SCOPE_LENGTH or value.startswith("/"):
        raise CrossProjectGrantRootError("bounded_scope must be a bounded relative subtree")
    if value == "":
        return ""
    segments = value.split("/")
    if len(segments) > _MAX_SCOPE_SEGMENTS:
        raise CrossProjectGrantRootError("bounded_scope exceeds the bounded segment count")
    for segment in segments:
        if not segment or segment in (".", "..") or not _SCOPE_SEGMENT_RE.fullmatch(segment):
            raise CrossProjectGrantRootError(
                "bounded_scope must not contain empty/traversal/oversized segments"
            )
    return value


def resolve_cross_project_grant_root(
    *, target_project_root: object, bounded_scope: object = ""
) -> str:
    """Resolve the bounded granted root for one trusted target project root.

    The target root is always a canonical, non-symlink, existing directory
    supplied by trusted resolution; the scope must stay inside it.  The
    returned physical root is the mechanically enforced scope boundary.
    """
    try:
        base = _canonical_directory(target_project_root, label="target_project_root")
    except AuthorizedRootSetError as exc:
        raise CrossProjectGrantRootError(str(exc)) from exc
    scope = validate_bounded_grant_scope(bounded_scope)
    if not scope:
        return base
    try:
        scoped = _canonical_directory(str(Path(base) / scope), label="grant scope root")
    except AuthorizedRootSetError as exc:
        raise CrossProjectGrantRootError(str(exc)) from exc
    if not Path(scoped).is_relative_to(Path(base)):
        raise CrossProjectGrantRootError("grant scope root must stay inside the target project root")
    return scoped


@dataclass(frozen=True)
class LocalGovernanceRootBinding:
    """Trusted operator governance base + canonical project identity.

    The operator/control plane configures one trusted governance base
    directory (for example ``<workspace>/plans``).  The binding resolves it
    only to this project's scope::

        <governance-base>/<canonical-project-id>/

    The folder name alone is never authority: the root must be an existing,
    canonical, non-symlink directory strictly inside the trusted base and
    exactly the project-scoped child of it.  The whole governance base and
    any sibling project scope are never the bound root, and no model-supplied
    input participates in the resolution.
    """

    governance_base: str
    project_id: str
    root_path: str = ""

    def __post_init__(self) -> None:
        try:
            base = _canonical_directory(self.governance_base, label="governance_base")
        except AuthorizedRootSetError as exc:
            raise LocalGovernanceRootError(str(exc)) from exc
        pid = _canonical_project_id(self.project_id)
        candidate = Path(base) / pid
        if candidate.parent != Path(base) or candidate.name != pid:
            raise LocalGovernanceRootError(
                "project scope must be exactly one project-id child of the governance base"
            )
        try:
            if candidate.is_symlink():
                raise LocalGovernanceRootError("project-scoped governance root must not be a symlink")
            resolved = candidate.resolve(strict=True)
        except LocalGovernanceRootError:
            raise
        except OSError as exc:
            raise LocalGovernanceRootError(f"project-scoped governance root must exist: {exc}") from exc
        if resolved != candidate:
            raise LocalGovernanceRootError("project-scoped governance root must already be canonical")
        if not resolved.is_dir():
            raise LocalGovernanceRootError("project-scoped governance root must be a directory")
        base_path = Path(base)
        if resolved.parent != base_path or not resolved.is_relative_to(base_path):
            raise LocalGovernanceRootError(
                "project-scoped governance root must not escape the trusted governance base"
            )
        derived = str(resolved)
        if self.root_path and self.root_path != derived:
            raise LocalGovernanceRootError("binding root_path must be the derived project scope")
        object.__setattr__(self, "governance_base", base)
        object.__setattr__(self, "project_id", pid)
        object.__setattr__(self, "root_path", derived)

    @classmethod
    def from_trusted_base(
        cls, governance_base: object, *, project_id: object
    ) -> "LocalGovernanceRootBinding":
        """Trusted construction seam: operator governance base + canonical id."""
        raw = os.fspath(governance_base) if isinstance(governance_base, (str, os.PathLike)) else governance_base
        if isinstance(raw, bytes):
            raise LocalGovernanceRootError("governance_base must be a text path")
        return cls(governance_base=raw, project_id=project_id)

    def revalidate(self) -> "LocalGovernanceRootBinding":
        """Re-run fail-closed validation against current filesystem state."""
        self.__post_init__()
        return self


def validate_local_governance_root(
    root: AuthorizedRoot,
    *,
    project_id: object,
    binding: LocalGovernanceRootBinding | None,
) -> None:
    """Separate bounded validation seam for the local-governance root.

    Deliberately separate from the trusted-sandbox validator: the sandbox
    knows nothing about operator governance storage.  Fail closed on a missing
    binding, non-canonical or symlinked root, project identity mismatch,
    sibling escape, worktree identity, or any capability beyond read/search.
    """
    if not isinstance(root, AuthorizedRoot):
        raise LocalGovernanceRootError(
            f"root must be AuthorizedRoot, got {type(root).__name__}"
        )
    if root.root_kind != ROOT_REF_LOCAL_GOVERNANCE:
        raise LocalGovernanceRootError("validator only accepts the local-governance root kind")
    if not isinstance(binding, LocalGovernanceRootBinding):
        raise LocalGovernanceRootError(
            "local-governance root requires the trusted project-scoped governance binding"
        )
    pid = _canonical_project_id(project_id)
    if binding.project_id != pid:
        raise LocalGovernanceRootError(
            "governance binding project identity does not match the trusted project"
        )
    if root.source != SOURCE_TRUSTED_GOVERNANCE:
        raise LocalGovernanceRootError(
            "local-governance root must come from the trusted governance binding"
        )
    if root.worktree_id:
        raise LocalGovernanceRootError("local-governance root must not carry worktree identity")
    forbidden = set(root.capabilities) - {CAPABILITY_SEARCH, CAPABILITY_READ}
    if forbidden:
        raise LocalGovernanceRootError(
            f"local-governance root is read/search only; forbidden capabilities: {sorted(forbidden)}"
        )
    if root.project_id != binding.project_id:
        raise LocalGovernanceRootError(
            "local-governance root project_id must equal the bound project identity"
        )
    binding.revalidate()
    if root.root_path != binding.root_path:
        raise LocalGovernanceRootError(
            "local-governance root path must be the bound project-scoped governance root"
        )


# ---------------------------------------------------------------------------
# Trusted authorized-evidence binding (AF #57 M2/W4 §15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizedEvidenceRootBinding:
    """Trusted evidence base + canonical project identity.

    The operator/control plane configures one trusted evidence base directory
    (the existing ``.aota-evidence`` tree).  The binding resolves it only to
    this project's scope::

        <evidence-base>/<canonical-project-id>/

    The whole global evidence base and any sibling project evidence are never
    the bound root, and no model-supplied input participates in the
    resolution.  The binding creates no store and duplicates no bytes: it is
    a read-only projection over already-existing evidence.
    """

    evidence_base: str
    project_id: str
    root_path: str = ""

    def __post_init__(self) -> None:
        try:
            base = _canonical_directory(self.evidence_base, label="evidence_base")
        except AuthorizedRootSetError as exc:
            raise AuthorizedEvidenceRootError(str(exc)) from exc
        pid = _require_project_identity(
            self.project_id, label="project_id", error_cls=AuthorizedEvidenceRootError
        )
        candidate = Path(base) / pid
        if candidate.parent != Path(base) or candidate.name != pid:
            raise AuthorizedEvidenceRootError(
                "project scope must be exactly one project-id child of the evidence base"
            )
        try:
            if candidate.is_symlink():
                raise AuthorizedEvidenceRootError(
                    "project-scoped evidence root must not be a symlink"
                )
            resolved = candidate.resolve(strict=True)
        except AuthorizedEvidenceRootError:
            raise
        except OSError as exc:
            raise AuthorizedEvidenceRootError(
                f"project-scoped evidence root must exist: {exc}"
            ) from exc
        if resolved != candidate:
            raise AuthorizedEvidenceRootError(
                "project-scoped evidence root must already be canonical"
            )
        if not resolved.is_dir():
            raise AuthorizedEvidenceRootError("project-scoped evidence root must be a directory")
        base_path = Path(base)
        if resolved.parent != base_path or not resolved.is_relative_to(base_path):
            raise AuthorizedEvidenceRootError(
                "project-scoped evidence root must not escape the trusted evidence base"
            )
        derived = str(resolved)
        if self.root_path and self.root_path != derived:
            raise AuthorizedEvidenceRootError("binding root_path must be the derived project scope")
        object.__setattr__(self, "evidence_base", base)
        object.__setattr__(self, "project_id", pid)
        object.__setattr__(self, "root_path", derived)

    @classmethod
    def from_trusted_base(
        cls, evidence_base: object, *, project_id: object
    ) -> "AuthorizedEvidenceRootBinding":
        """Trusted construction seam: operator evidence base + canonical id."""
        raw = (
            os.fspath(evidence_base)
            if isinstance(evidence_base, (str, os.PathLike))
            else evidence_base
        )
        if isinstance(raw, bytes):
            raise AuthorizedEvidenceRootError("evidence_base must be a text path")
        return cls(evidence_base=raw, project_id=project_id)

    def revalidate(self) -> "AuthorizedEvidenceRootBinding":
        """Re-run fail-closed validation against current filesystem state."""
        self.__post_init__()
        return self


def validate_authorized_evidence_root(
    root: AuthorizedRoot,
    *,
    project_id: object,
    binding: AuthorizedEvidenceRootBinding | None,
) -> None:
    """Separate bounded validation seam for the authorized-evidence root.

    Fail closed on a missing binding, non-canonical or symlinked root,
    project identity mismatch, sibling/global escape, worktree identity, or
    any capability beyond read/search.  Never inherits the governance or
    grant validation and never weakens the sandbox checks.
    """
    if not isinstance(root, AuthorizedRoot):
        raise AuthorizedEvidenceRootError(
            f"root must be AuthorizedRoot, got {type(root).__name__}"
        )
    if root.root_kind != ROOT_REF_AUTHORIZED_EVIDENCE:
        raise AuthorizedEvidenceRootError(
            "validator only accepts the authorized-evidence root kind"
        )
    if not isinstance(binding, AuthorizedEvidenceRootBinding):
        raise AuthorizedEvidenceRootError(
            "authorized-evidence root requires the trusted project-scoped evidence binding"
        )
    pid = _require_project_identity(
        project_id, label="project_id", error_cls=AuthorizedEvidenceRootError
    )
    if binding.project_id != pid:
        raise AuthorizedEvidenceRootError(
            "evidence binding project identity does not match the trusted project"
        )
    if root.source != SOURCE_TRUSTED_EVIDENCE:
        raise AuthorizedEvidenceRootError(
            "authorized-evidence root must come from the trusted evidence binding"
        )
    if root.worktree_id:
        raise AuthorizedEvidenceRootError(
            "authorized-evidence root must not carry worktree identity"
        )
    forbidden = set(root.capabilities) - GRANT_SUPPORTED_CAPABILITIES
    if forbidden:
        raise AuthorizedEvidenceRootError(
            f"authorized-evidence root is read/search only; forbidden capabilities: {sorted(forbidden)}"
        )
    if root.project_id != binding.project_id:
        raise AuthorizedEvidenceRootError(
            "authorized-evidence root project_id must equal the bound project identity"
        )
    binding.revalidate()
    if root.root_path != binding.root_path:
        raise AuthorizedEvidenceRootError(
            "authorized-evidence root path must be the bound project-scoped evidence root"
        )


# ---------------------------------------------------------------------------
# Trusted cross-project grant root binding (AF #57 M2/W4 §6-§11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossProjectGrantRootBinding:
    """Materialized instance of one validated, live cross-project grant.

    This is the trusted physical binding produced by the W4 composition seam
    from an explicit ``CrossProjectGrant`` + a fresh trusted target Project
    resolution: the grant stores logical identity only, while this binding
    carries the resolved bounded root.  It is read-only and never the whole
    target checkout escape; ``bounded_scope`` is enforced by making the
    resolved scoped subtree the root path.
    """

    grant_id: str
    requesting_project: str
    target_project: str
    root_kind: str
    granted_capabilities: frozenset[str]
    bounded_scope: str
    root_path: str
    authority_digest: str
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.grant_id, str) or not _GRANT_ID_RE.fullmatch(self.grant_id):
            raise CrossProjectGrantRootError(
                "grant_id must be a bounded trusted grant identity, never model supplied"
            )
        requesting = _require_project_identity(
            self.requesting_project,
            label="requesting_project",
            error_cls=CrossProjectGrantRootError,
        )
        target = _require_project_identity(
            self.target_project, label="target_project", error_cls=CrossProjectGrantRootError
        )
        object.__setattr__(self, "requesting_project", requesting)
        object.__setattr__(self, "target_project", target)
        if requesting == target:
            raise CrossProjectGrantRootError(
                "a cross-project grant root can never target the requesting project"
            )
        if self.root_kind not in GRANTABLE_ROOT_KINDS:
            raise CrossProjectGrantRootError(
                f"root_kind must be one of {list(GRANTABLE_ROOT_KINDS)}"
            )
        if not isinstance(self.granted_capabilities, frozenset) or not self.granted_capabilities:
            raise CrossProjectGrantRootError(
                "granted_capabilities must be a non-empty frozenset"
            )
        forbidden = set(self.granted_capabilities) - GRANT_SUPPORTED_CAPABILITIES
        if forbidden:
            raise CrossProjectGrantRootError(
                f"cross-project grant roots are read/search only; forbidden: {sorted(forbidden)}"
            )
        object.__setattr__(
            self, "bounded_scope", validate_bounded_grant_scope(self.bounded_scope)
        )
        try:
            canonical = _canonical_directory(self.root_path, label="grant root_path")
        except AuthorizedRootSetError as exc:
            raise CrossProjectGrantRootError(str(exc)) from exc
        object.__setattr__(self, "root_path", canonical)
        if not isinstance(self.authority_digest, str) or not _DIGEST_RE.fullmatch(
            self.authority_digest
        ):
            raise CrossProjectGrantRootError("authority_digest must be a SHA-256 hex digest")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise CrossProjectGrantRootError("revision must be a positive integer")

    def revalidate(self) -> "CrossProjectGrantRootBinding":
        self.__post_init__()
        return self


def validate_cross_project_grant_root(
    root: AuthorizedRoot,
    *,
    native_project_id: object,
    binding: CrossProjectGrantRootBinding | None,
) -> None:
    """Separate bounded validation seam for a grant-specific foreign root.

    This is the *only* place a foreign project root is admissible, and it is
    validated strictly against its materialized grant binding.  The default
    same-project and sandbox checks for normal roots are never weakened.
    """
    if not isinstance(root, AuthorizedRoot):
        raise CrossProjectGrantRootError(
            f"root must be AuthorizedRoot, got {type(root).__name__}"
        )
    if root.source != SOURCE_EXPLICIT_GRANT:
        raise CrossProjectGrantRootError(
            "grant roots must come from an explicit trusted cross-project grant"
        )
    if root.root_kind not in GRANTABLE_ROOT_KINDS:
        raise CrossProjectGrantRootError(
            f"granted root kind must be one of {list(GRANTABLE_ROOT_KINDS)}"
        )
    if not isinstance(binding, CrossProjectGrantRootBinding):
        raise CrossProjectGrantRootError(
            "grant-specific foreign roots require the materialized grant binding"
        )
    native = _require_project_identity(
        native_project_id, label="native_project_id", error_cls=CrossProjectGrantRootError
    )
    if binding.requesting_project != native:
        raise CrossProjectGrantRootError(
            "grant requesting project must be the session's project identity"
        )
    if root.grant_id != binding.grant_id:
        raise CrossProjectGrantRootError("grant root identity must match the materialized binding")
    if root.root_ref != binding.grant_id:
        raise CrossProjectGrantRootError(
            "granted root_ref must be the bounded grant-bound name"
        )
    if root.project_id != binding.target_project:
        raise CrossProjectGrantRootError(
            "granted root project identity must be the trusted target project"
        )
    if root.project_id == native:
        raise CrossProjectGrantRootError("self-grant roots are not admissible")
    if root.worktree_id:
        raise CrossProjectGrantRootError("granted foreign roots carry no worktree identity")
    if root.capabilities != binding.granted_capabilities:
        raise CrossProjectGrantRootError("granted capabilities must match the binding exactly")
    if set(root.capabilities) - GRANT_SUPPORTED_CAPABILITIES:
        raise CrossProjectGrantRootError("granted foreign roots are read/search only")
    if root.root_kind != binding.root_kind:
        raise CrossProjectGrantRootError("granted root kind must match the binding")
    binding.revalidate()
    if root.root_path != binding.root_path:
        raise CrossProjectGrantRootError(
            "granted foreign root path must be the resolved bounded target root"
        )


def authorize_cross_project_read_root(
    sandbox: WorktreeSandboxBoundary,
    roots: AuthorizedRootSet,
    binding: CrossProjectGrantRootBinding,
) -> AuthorizedRootSet:
    """Materialize one validated grant binding into a new root set.

    The returned set is a new immutable set: the caller's set is untouched
    and remains the default (isolated) root set.  This is the only seam that
    can add a foreign-project read root, and it requires the trusted grant
    binding.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise CrossProjectGrantRootError(
            f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}"
        )
    if not isinstance(roots, AuthorizedRootSet):
        raise CrossProjectGrantRootError("roots must be an AuthorizedRootSet")
    if not isinstance(binding, CrossProjectGrantRootBinding):
        raise CrossProjectGrantRootError(
            "materialization requires the trusted cross-project grant binding"
        )
    if roots.session_kind != ROOT_SET_SESSION_TASK_MAIN:
        raise CrossProjectGrantRootError(
            "cross-project grant roots are task-main/project-scope only"
        )
    if binding.requesting_project != sandbox.project_id:
        raise CrossProjectGrantRootError(
            "grant requesting project must match the trusted session project"
        )
    if binding.target_project == sandbox.project_id:
        raise CrossProjectGrantRootError("self-grant roots are not admissible")
    if roots.get(binding.grant_id) is not None or any(
        existing.grant_id == binding.grant_id for existing in roots.roots
    ):
        raise CrossProjectGrantRootError("grant root is already materialized in this root set")
    root = AuthorizedRoot(
        root_ref=binding.grant_id,
        root_kind=binding.root_kind,
        root_path=binding.root_path,
        capabilities=binding.granted_capabilities,
        project_id=binding.target_project,
        source=SOURCE_EXPLICIT_GRANT,
        grant_id=binding.grant_id,
    )
    root_set = AuthorizedRootSet(
        session_kind=roots.session_kind,
        roots=roots.roots + (root,),
        governance_binding=roots.governance_binding,
        evidence_binding=roots.evidence_binding,
        cross_project_bindings=roots.cross_project_bindings + (binding,),
    )
    validate_root_set_against_sandbox(root_set, sandbox)
    return root_set


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
    # AF #57 M2/W4: grant-bound identity for explicit-grant foreign roots.
    # Empty for every sandbox/governance/evidence-derived root.
    grant_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.root_ref, str) or not _ROOT_REF_RE.fullmatch(self.root_ref):
            raise AuthorizedRootSetError(f"root_ref must be a bounded lowercase name, got {self.root_ref!r}")
        if self.source == SOURCE_EXPLICIT_GRANT:
            # AF #57 M2/W4: the only explicit-grant instantiation is a
            # grant-bound foreign read root.  The root_ref is the bounded
            # grant name; the semantic kind stays the granted root kind.
            if self.root_kind not in GRANTABLE_ROOT_KINDS:
                raise AuthorizedRootSetError(
                    f"explicit grant root kind must be one of {list(GRANTABLE_ROOT_KINDS)}"
                )
            if not isinstance(self.grant_id, str) or not _GRANT_ID_RE.fullmatch(self.grant_id):
                raise AuthorizedRootSetError(
                    "explicit grant roots require a bounded trusted grant identity"
                )
            if self.root_ref != self.grant_id:
                raise AuthorizedRootSetError(
                    "explicit grant root_ref must be the bounded grant-bound name"
                )
        else:
            if self.grant_id:
                raise AuthorizedRootSetError(
                    "grant identity is only admissible on explicit grant roots"
                )
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
        if self.source not in (
            SOURCE_TRUSTED_SANDBOX,
            SOURCE_EXPLICIT_GRANT,
            SOURCE_TRUSTED_GOVERNANCE,
            SOURCE_TRUSTED_EVIDENCE,
        ):
            raise AuthorizedRootSetError(f"invalid root source {self.source!r}")
        if self.root_kind == ROOT_REF_LOCAL_GOVERNANCE:
            # The trusted governance source is the only admissible origin for
            # the local-governance root; the binding path validation itself
            # lives in the separate governance validation seam.
            if self.source != SOURCE_TRUSTED_GOVERNANCE:
                raise AuthorizedRootSetError(
                    "local-governance roots must be constructed from a trusted governance binding"
                )
            if self.worktree_id:
                raise AuthorizedRootSetError(
                    "local-governance roots do not carry worktree identity"
                )
            forbidden = set(self.capabilities) - {CAPABILITY_SEARCH, CAPABILITY_READ}
            if forbidden:
                raise AuthorizedRootSetError(
                    f"local-governance roots are read/search only; got {sorted(forbidden)}"
                )
        elif self.root_kind == ROOT_REF_AUTHORIZED_EVIDENCE:
            # AF #57 M2/W4: the trusted evidence binding is the only
            # admissible origin; a bare sandbox-path construction fails closed.
            if self.source != SOURCE_TRUSTED_EVIDENCE:
                raise AuthorizedRootSetError(
                    "authorized-evidence roots must be constructed from a trusted evidence binding"
                )
            if self.worktree_id:
                raise AuthorizedRootSetError(
                    "authorized-evidence roots do not carry worktree identity"
                )
            forbidden = set(self.capabilities) - GRANT_SUPPORTED_CAPABILITIES
            if forbidden:
                raise AuthorizedRootSetError(
                    f"authorized-evidence roots are read/search only; got {sorted(forbidden)}"
                )
        elif self.source == SOURCE_TRUSTED_GOVERNANCE:
            raise AuthorizedRootSetError(
                "the trusted governance source is only valid for the local-governance root"
            )
        elif self.source == SOURCE_TRUSTED_EVIDENCE:
            raise AuthorizedRootSetError(
                "the trusted evidence source is only valid for the authorized-evidence root"
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
        payload = {
            "root_ref": self.root_ref,
            "kind": self.root_kind,
            "root_path": self.root_path,
            "capabilities": sorted(self.capabilities),
            "project_id": self.project_id,
            "worktree_id": self.worktree_id,
            "source": self.source,
        }
        if self.grant_id:
            payload["grant_id"] = self.grant_id
        return payload


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
    # AF #57 M1/W2: trusted operator governance binding for this session. It
    # is trusted construction data (never model-visible, never a path in any
    # projection) and is required exactly when a local-governance root is
    # granted. The whole governance base is never itself a root.
    governance_binding: LocalGovernanceRootBinding | None = None
    # AF #57 M2/W4: trusted project-scoped evidence binding, required exactly
    # when an authorized-evidence root is granted (task-main only; workers get
    # no default evidence grant).
    evidence_binding: AuthorizedEvidenceRootBinding | None = None
    # AF #57 M2/W4: materialized cross-project grant bindings.  Required
    # exactly when grant-specific foreign roots are present; the bindings are
    # trusted construction data, never model-visible.
    cross_project_bindings: tuple[CrossProjectGrantRootBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.session_kind not in SUPPORTED_ROOT_SET_SESSIONS:
            raise AuthorizedRootSetError(
                f"session_kind must be one of {sorted(SUPPORTED_ROOT_SET_SESSIONS)}, got {self.session_kind!r}"
            )
        if not isinstance(self.roots, tuple) or not self.roots:
            raise AuthorizedRootSetError("roots must be a non-empty tuple")
        if not isinstance(self.cross_project_bindings, tuple):
            raise AuthorizedRootSetError("cross_project_bindings must be a tuple")
        for idx, binding in enumerate(self.cross_project_bindings):
            if not isinstance(binding, CrossProjectGrantRootBinding):
                raise AuthorizedRootSetError(
                    f"cross_project_bindings[{idx}] must be a CrossProjectGrantRootBinding"
                )
        seen: set[str] = set()
        native_project_ids: set[str] = set()
        grant_roots: list[AuthorizedRoot] = []
        for idx, root in enumerate(self.roots):
            if not isinstance(root, AuthorizedRoot):
                raise AuthorizedRootSetError(f"roots[{idx}] must be AuthorizedRoot, got {type(root).__name__}")
            if root.root_ref in seen:
                raise AuthorizedRootSetError(f"duplicate root_ref {root.root_ref!r}")
            seen.add(root.root_ref)
            if root.source == SOURCE_EXPLICIT_GRANT:
                grant_roots.append(root)
            else:
                native_project_ids.add(root.project_id)
        if len(native_project_ids) != 1:
            raise AuthorizedRootSetError(
                "all native roots in a set must share one project_id"
            )
        native_project_id = next(iter(native_project_ids))
        for grant_root in grant_roots:
            if grant_root.project_id == native_project_id:
                raise AuthorizedRootSetError(
                    "cross-project grant roots must target a foreign project (no self-grant)"
                )
        binding_ids = [binding.grant_id for binding in self.cross_project_bindings]
        if len(set(binding_ids)) != len(binding_ids):
            raise AuthorizedRootSetError("duplicate cross-project grant binding identity")
        if grant_roots:
            if self.session_kind != ROOT_SET_SESSION_TASK_MAIN:
                raise AuthorizedRootSetError(
                    "cross-project grant roots are task-main/project-scope only"
                )
            root_grant_ids = sorted(grant_root.grant_id for grant_root in grant_roots)
            if root_grant_ids != sorted(binding_ids):
                raise AuthorizedRootSetError(
                    "every grant-specific foreign root requires exactly one materialized grant binding"
                )
            for binding in self.cross_project_bindings:
                if binding.requesting_project != native_project_id:
                    raise AuthorizedRootSetError(
                        "grant binding requesting project must be the session project"
                    )
        elif self.cross_project_bindings:
            raise AuthorizedRootSetError(
                "cross-project grant bindings require their grant-specific foreign roots"
            )
        if ROOT_REF_LOCAL_GOVERNANCE in seen:
            if not isinstance(self.governance_binding, LocalGovernanceRootBinding):
                raise AuthorizedRootSetError(
                    "a local-governance root requires the trusted project-scoped governance binding"
                )
            if self.session_kind in (ROOT_SET_SESSION_WORKER, ROOT_SET_SESSION_SINGLE):
                raise AuthorizedRootSetError(
                    "worker/single root sets must not include local-governance "
                    "(governance roots are task-main/project-scope only)"
                )
        elif self.governance_binding is not None and not isinstance(
            self.governance_binding, LocalGovernanceRootBinding
        ):
            raise AuthorizedRootSetError("governance_binding must be a LocalGovernanceRootBinding or None")
        if ROOT_REF_AUTHORIZED_EVIDENCE in seen:
            if not isinstance(self.evidence_binding, AuthorizedEvidenceRootBinding):
                raise AuthorizedRootSetError(
                    "an authorized-evidence root requires the trusted project-scoped evidence binding"
                )
            if self.session_kind in (ROOT_SET_SESSION_WORKER, ROOT_SET_SESSION_SINGLE):
                raise AuthorizedRootSetError(
                    "worker/single root sets must not include authorized-evidence "
                    "(evidence roots are task-main/project-scope only)"
                )
        elif self.evidence_binding is not None and not isinstance(
            self.evidence_binding, AuthorizedEvidenceRootBinding
        ):
            raise AuthorizedRootSetError(
                "evidence_binding must be an AuthorizedEvidenceRootBinding or None"
            )
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

    AF #57 M1/W2: a local-governance root is additionally admitted, but only
    through the separate trusted governance-binding validation seam carried by
    the set itself.  The sandbox checks above are unchanged and are never
    relaxed by the presence of a governance binding.
    """
    if not isinstance(roots, AuthorizedRootSet):
        raise AuthorizedRootSetError(f"roots must be AuthorizedRootSet, got {type(roots).__name__}")
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    project_root = _sandbox_root(sandbox, label="project_root")
    worktree_root = _sandbox_root(sandbox, label="worktree_root")
    bindings_by_grant = {binding.grant_id: binding for binding in roots.cross_project_bindings}
    for root in roots.roots:
        if root.source == SOURCE_EXPLICIT_GRANT:
            # AF #57 M2/W4: the single grant-specific foreign exception.  The
            # normal same-project/sandbox checks below never apply to (and are
            # never relaxed for) grant roots; they go through their own
            # materialized-binding seam.
            validate_cross_project_grant_root(
                root,
                native_project_id=sandbox.project_id,
                binding=bindings_by_grant.get(root.grant_id),
            )
            continue
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
        elif root.root_kind == ROOT_REF_LOCAL_GOVERNANCE:
            validate_local_governance_root(
                root,
                project_id=sandbox.project_id,
                binding=roots.governance_binding,
            )
        elif root.root_kind == ROOT_REF_AUTHORIZED_EVIDENCE:
            validate_authorized_evidence_root(
                root,
                project_id=sandbox.project_id,
                binding=roots.evidence_binding,
            )
        else:
            raise AuthorizedRootSetError(f"root kind {root.root_kind!r} is not sandbox-derivable")


def authorized_roots_from_sandbox(
    sandbox: WorktreeSandboxBoundary,
) -> tuple[str, str]:
    project_root = _sandbox_root(sandbox, label="project_root")
    worktree_root = _sandbox_root(sandbox, label="worktree_root")
    return project_root, worktree_root


def authorized_roots_for_task_main(
    sandbox: WorktreeSandboxBoundary,
    *,
    governance_binding: LocalGovernanceRootBinding | None = None,
    evidence_binding: AuthorizedEvidenceRootBinding | None = None,
) -> AuthorizedRootSet:
    """Task-main default roots: project-main (read/search) + active-worktree
    (read/search/write), plus the project-scoped local-governance root
    (read/search only) when the trusted operator governance binding is
    supplied, plus the project-scoped authorized-evidence root (read/search
    only) when the trusted evidence binding is supplied.  Derived exclusively
    from the trusted sandbox and the trusted bindings; no model input
    participates.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AuthorizedRootSetError(f"sandbox must be WorktreeSandboxBoundary, got {type(sandbox).__name__}")
    project_root, worktree_root = authorized_roots_from_sandbox(sandbox)
    roots: tuple[AuthorizedRoot, ...] = (
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
    if governance_binding is not None:
        if not isinstance(governance_binding, LocalGovernanceRootBinding):
            raise AuthorizedRootSetError(
                f"governance_binding must be LocalGovernanceRootBinding, got {type(governance_binding).__name__}"
            )
        if governance_binding.project_id != sandbox.project_id:
            raise AuthorizedRootSetError(
                "governance binding project identity does not match the trusted sandbox project"
            )
        roots = roots + (
            AuthorizedRoot(
                root_ref=ROOT_REF_LOCAL_GOVERNANCE,
                root_kind=ROOT_REF_LOCAL_GOVERNANCE,
                root_path=governance_binding.root_path,
                capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ}),
                project_id=sandbox.project_id,
                source=SOURCE_TRUSTED_GOVERNANCE,
            ),
        )
    if evidence_binding is not None:
        if not isinstance(evidence_binding, AuthorizedEvidenceRootBinding):
            raise AuthorizedRootSetError(
                f"evidence_binding must be AuthorizedEvidenceRootBinding, got {type(evidence_binding).__name__}"
            )
        if evidence_binding.project_id != sandbox.project_id:
            raise AuthorizedRootSetError(
                "evidence binding project identity does not match the trusted sandbox project"
            )
        roots = roots + (
            AuthorizedRoot(
                root_ref=ROOT_REF_AUTHORIZED_EVIDENCE,
                root_kind=ROOT_REF_AUTHORIZED_EVIDENCE,
                root_path=evidence_binding.root_path,
                capabilities=frozenset({CAPABILITY_SEARCH, CAPABILITY_READ}),
                project_id=sandbox.project_id,
                source=SOURCE_TRUSTED_EVIDENCE,
            ),
        )
    root_set = AuthorizedRootSet(
        session_kind=ROOT_SET_SESSION_TASK_MAIN,
        roots=roots,
        governance_binding=governance_binding,
        evidence_binding=evidence_binding,
    )
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
LOCAL_GOVERNANCE_ROOT_SUPPORTED: bool = True
LOCAL_GOVERNANCE_ROOT_PROJECT_SCOPED: bool = True
LOCAL_GOVERNANCE_ROOT_REQUIRES_TRUSTED_BINDING: bool = True
LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY: bool = False
LOCAL_GOVERNANCE_MODEL_NOMINATED_PATH: bool = False
# AF #57 M2/W4: authorized-evidence is now instantiable, but only from the
# trusted project-scoped evidence binding; a bare sandbox-path construction
# still fails closed and neither default root set grants it.
PREPARED_ROOT_REFS_INSTANTIATED: bool = True
AUTHORIZED_EVIDENCE_ROOT_SUPPORTED: bool = True
AUTHORIZED_EVIDENCE_ROOT_PROJECT_SCOPED: bool = True
AUTHORIZED_EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING: bool = True
AUTHORIZED_EVIDENCE_AGENT_WRITE_CAPABILITY: bool = False
AUTHORIZED_EVIDENCE_MODEL_NOMINATED_PATH: bool = False
CROSS_PROJECT_GRANT_ROOT_SUPPORTED: bool = True
CROSS_PROJECT_GRANT_ROOT_REQUIRES_EXPLICIT_GRANT: bool = True
CROSS_PROJECT_GRANT_DEFAULT_DENIED: bool = True
CROSS_PROJECT_GRANT_WRITE_CAPABILITY: bool = False
DEFAULT_SIBLING_ISOLATION_WEAKENED: bool = False
MODEL_CAN_MINT_GRANT_ROOT: bool = False

ROOT_REF_CAPABILITY_SEAM: str = (
    "AuthorizedRootSet(WorktreeSandboxBoundary.project_root/worktree_root) + "
    "resolve_authorized_root + workspace provider capability enforcement"
)

LOCAL_GOVERNANCE_SEAM: str = (
    "LocalGovernanceRootBinding(trusted operator governance base + canonical "
    "project_id) -> project-scoped local-governance root -> validate_local_governance_root"
)

AUTHORIZED_EVIDENCE_SEAM: str = (
    "AuthorizedEvidenceRootBinding(trusted evidence base + canonical project_id) "
    "-> project-scoped authorized-evidence root -> validate_authorized_evidence_root"
)

CROSS_PROJECT_GRANT_SEAM: str = (
    "live CrossProjectGrant + fresh trusted target Project resolution "
    "-> CrossProjectGrantRootBinding -> authorize_cross_project_read_root "
    "-> validate_cross_project_grant_root"
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
    "SOURCE_TRUSTED_GOVERNANCE",
    "SOURCE_TRUSTED_EVIDENCE",
    "GRANTABLE_ROOT_KINDS",
    "GRANT_SUPPORTED_CAPABILITIES",
    "ROOT_SET_SESSION_TASK_MAIN",
    "ROOT_SET_SESSION_WORKER",
    "ROOT_SET_SESSION_SINGLE",
    "SUPPORTED_ROOT_SET_SESSIONS",
    "AuthorizedRootError",
    "AuthorizedRootReferenceError",
    "AuthorizedRootUnknownError",
    "AuthorizedRootCapabilityError",
    "AuthorizedRootSetError",
    "LocalGovernanceRootError",
    "LocalGovernanceRootBinding",
    "validate_local_governance_root",
    "AuthorizedEvidenceRootError",
    "AuthorizedEvidenceRootBinding",
    "validate_authorized_evidence_root",
    "CrossProjectGrantRootError",
    "CrossProjectGrantRootBinding",
    "validate_cross_project_grant_root",
    "authorize_cross_project_read_root",
    "validate_bounded_grant_scope",
    "resolve_cross_project_grant_root",
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
    "LOCAL_GOVERNANCE_ROOT_PROJECT_SCOPED",
    "LOCAL_GOVERNANCE_ROOT_REQUIRES_TRUSTED_BINDING",
    "LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY",
    "LOCAL_GOVERNANCE_MODEL_NOMINATED_PATH",
    "PREPARED_ROOT_REFS_INSTANTIATED",
    "AUTHORIZED_EVIDENCE_ROOT_SUPPORTED",
    "AUTHORIZED_EVIDENCE_ROOT_PROJECT_SCOPED",
    "AUTHORIZED_EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING",
    "AUTHORIZED_EVIDENCE_AGENT_WRITE_CAPABILITY",
    "AUTHORIZED_EVIDENCE_MODEL_NOMINATED_PATH",
    "CROSS_PROJECT_GRANT_ROOT_SUPPORTED",
    "CROSS_PROJECT_GRANT_ROOT_REQUIRES_EXPLICIT_GRANT",
    "CROSS_PROJECT_GRANT_DEFAULT_DENIED",
    "CROSS_PROJECT_GRANT_WRITE_CAPABILITY",
    "DEFAULT_SIBLING_ISOLATION_WEAKENED",
    "MODEL_CAN_MINT_GRANT_ROOT",
    "ROOT_REF_CAPABILITY_SEAM",
    "LOCAL_GOVERNANCE_SEAM",
    "AUTHORIZED_EVIDENCE_SEAM",
    "CROSS_PROJECT_GRANT_SEAM",
]
