"""Project-Bound Physical Resource Resolution (S2 M1-W2).

Physical resource resolution layer inside one already trusted W1 worktree
sandbox. Flow:

    W1 trusted Worktree/Sandbox binding
        ↓
    logical project-relative resource reference
        ↓
    bounded physical resolution
        ↓
    containment / integrity evidence

This module does NOT grant operation or mutation authority. Resolution
evidence is containment proof at resolution time only, not use-time
authority.

TOCTOU boundary
---------------
Resolution checks path containment and symlink safety at resolution time
and returns evidence with a canonical path. The filesystem may change
between check and use (TOCTOU). This implementation does NOT eliminate
TOCTOU. It does NOT provide a secure open/use seam. Downstream physical
open/mutation must revalidate containment at use time or use a dedicated
secure use-time seam. Resolution evidence is NOT use-time authority.

Evidence is deterministic for equivalent trusted binding + equivalent
logical reference when filesystem state is unchanged. No dependency on
cwd, dict order, enumeration order, mtime, or branch heuristics.

Authority for the physical root comes only from the trusted W1 binding
(WorktreeSandboxBoundary). Arbitrary absolute roots do not self-authorize.
No CWD authority.

Separation from host resolver: TrustedResourceResolver and RESOURCE_KINDS
retain host resources only. This module is a separate authority domain.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_LOGICAL_REF_LENGTH: int = 512
MAX_PART_LENGTH: int = 128
MAX_PARTS: int = 64

# part charset: start alnum, then alnum . _ -
_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Authority markers — explicit for tests and downstream seam
# ---------------------------------------------------------------------------

RESOURCE_RESOLUTION_IS_OPERATION_AUTHORITY: bool = False
RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY: bool = False
RESOURCE_TYPE_IS_OPERATION_AUTHORITY: bool = False
RESOLUTION_RESULT_IS_EVIDENCE: bool = True
RESOLUTION_RESULT_IS_AUTHORITY: bool = False
DIGEST_IS_AUTHORITY: bool = False
RESOLUTION_EVIDENCE_IS_USE_TIME_AUTHORITY: bool = False
TOCTOU_BOUNDARY_EXPLICIT: bool = True
TOCTOU_ELIMINATED: bool = False
ARBITRARY_ABSOLUTE_RESOURCE_INPUT: bool = False
UNTRUSTED_ROOT_SELF_AUTHORIZES: bool = False
RESOURCE_REFERENCE_RELATIVE: bool = True
RESOURCE_TRAVERSAL_FAIL_CLOSED: bool = True
CWD_IS_AUTHORITY: bool = False
RESOLVED_PATH_WITHIN_BOUND_ROOT: bool = True
PATH_ESCAPE_FAIL_CLOSED: bool = True
SYMLINK_ESCAPE_FAIL_CLOSED: bool = True
RESOURCE_RESOLUTION_DETERMINISTIC: bool = True
RESOURCE_RESOLUTION_WORKTREE_BOUND: bool = True
CROSS_WORKTREE_RESOURCE_REUSE_AUTHORITY: bool = False
HOST_TRUSTED_RESOURCE_RESOLVER_RETAIN: bool = True
RESOURCE_KINDS_PROJECT_EXPANSION: bool = False
PROJECT_BOUND_RESOLVER_SEPARATE_AUTHORITY_DOMAIN: bool = True

TOCTOU_EXPLANATION: str = (
    "Resolution evidence proves containment at resolution time only; "
    "downstream physical open/mutation must revalidate or use a secure "
    "use-time seam. TOCTOU not eliminated. Evidence is not use-time authority."
)

# ---------------------------------------------------------------------------
# Errors — fail-closed, prefer existing vocabulary where suitable but small
# domain-specific hierarchy is acceptable for deterministic containment.
# ---------------------------------------------------------------------------


class WorktreeResourceError(ValueError):
    """Base fail-closed error for project-bound resource resolution."""


class WorktreeResourceReferenceError(WorktreeResourceError):
    """Malformed logical resource reference."""


class WorktreeResourceBindingError(WorktreeResourceError):
    """Missing or invalid sandbox binding."""


class WorktreeResourceContainmentError(WorktreeResourceError):
    """Resource escapes bound root or violates containment."""


class WorktreeResourceSymlinkError(WorktreeResourceContainmentError):
    """Symlink-mediated escape or symlink at resource path rejected."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_binding(sandbox: object) -> WorktreeSandboxBoundary:
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise WorktreeResourceBindingError(
            f"binding must be WorktreeSandboxBoundary, got {type(sandbox).__name__}"
        )
    # Additional fail-closed: worktree_root must be non-empty canonical string
    if not isinstance(sandbox.worktree_root, str) or not sandbox.worktree_root:
        raise WorktreeResourceBindingError("sandbox worktree_root is invalid")
    # worktree_root must still exist and be directory at resolution time
    # (if missing, fail closed rather than silently resolving elsewhere)
    root_path = Path(sandbox.worktree_root)
    try:
        # Use lstat-style check: reject if root itself is symlink (W1 already does, but re-check)
        if root_path.is_symlink():
            raise WorktreeResourceBindingError(f"bound root is symlink: {root_path}")
        if not root_path.exists():
            raise WorktreeResourceBindingError(f"bound root does not exist: {root_path}")
        if not root_path.is_dir():
            raise WorktreeResourceBindingError(f"bound root is not a directory: {root_path}")
    except OSError as exc:
        raise WorktreeResourceBindingError(f"bound root inaccessible: {exc}") from exc
    return sandbox


def _validate_logical_ref(ref: object) -> str:
    if not isinstance(ref, str) or type(ref) is not str:
        raise WorktreeResourceReferenceError(f"resource ref must be a string, got {type(ref).__name__}")
    # NUL check explicit
    if "\x00" in ref:
        raise WorktreeResourceReferenceError("resource ref must not contain NUL")
    # backslash escape
    if "\\" in ref:
        raise WorktreeResourceReferenceError("resource ref must not contain backslash")
    # length bound
    if len(ref) == 0:
        raise WorktreeResourceReferenceError("resource ref must not be empty")
    if len(ref) > MAX_LOGICAL_REF_LENGTH:
        raise WorktreeResourceReferenceError(f"resource ref length {len(ref)} exceeds max {MAX_LOGICAL_REF_LENGTH}")
    # absolute path rejected (must be relative)
    if ref.startswith("/"):
        raise WorktreeResourceReferenceError(f"resource ref must be relative, got absolute: {ref!r}")
    if ref.startswith("\\"):
        raise WorktreeResourceReferenceError(f"resource ref must be relative: {ref!r}")
    # reject empty stripped ambiguity
    if ref.strip() == "":
        raise WorktreeResourceReferenceError("resource ref must not be empty or whitespace")
    if ref.strip() != ref:
        raise WorktreeResourceReferenceError("resource ref must not have leading/trailing whitespace")
    # "." ambiguity where inappropriate
    if ref == "." or ref == "./":
        raise WorktreeResourceReferenceError("resource ref '.' is ambiguous")
    # reject parts containing traversal or ambiguity
    parts = ref.split("/")
    if len(parts) > MAX_PARTS:
        raise WorktreeResourceReferenceError(f"resource ref depth {len(parts)} exceeds max {MAX_PARTS}")
    for part in parts:
        if part == "":
            raise WorktreeResourceReferenceError(f"resource ref contains empty segment: {ref!r}")
        if part == "." or part == "..":
            raise WorktreeResourceReferenceError(f"resource ref must not contain '.' or '..': {ref!r}")
        if len(part) > MAX_PART_LENGTH:
            raise WorktreeResourceReferenceError(f"resource ref segment too long: {part!r}")
        if not _PART_RE.fullmatch(part):
            raise WorktreeResourceReferenceError(f"resource ref segment has invalid charset: {part!r}")
    # Also use Path.is_absolute as secondary absolute check (e.g., Windows drive)
    # We already rejected "/" leading, but also check Path semantics without trusting CWD
    try:
        if Path(ref).is_absolute():
            raise WorktreeResourceReferenceError(f"resource ref must be relative: {ref!r}")
    except Exception as exc:
        if isinstance(exc, WorktreeResourceReferenceError):
            raise
        raise WorktreeResourceReferenceError(f"malformed resource ref: {ref!r}") from exc
    return ref


def _check_symlink_and_containment(root: Path, candidate: Path, root_canonical: Path) -> Path:
    """Fail-closed symlink walk + canonical containment verification.

    1. Walk each component from root to candidate, rejecting any symlink.
       This is the smallest safe v0 model: any symlink at or along the
       resource path is rejected, ensuring symlink-mediated escape fails
       closed without complex allowlists.

    2. Canonical containment via resolve(strict=False) relative_to.
       Do not use lexical prefix only.
    """
    # Component symlink walk
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise WorktreeResourceContainmentError("resource escapes bound root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise WorktreeResourceSymlinkError(f"symlink rejected at {current}")
            # Also reject if current is a symlink via lstat on missing broken link?
            # is_symlink already covers broken symlink. Keep check.
        except OSError as exc:
            raise WorktreeResourceContainmentError(f"symlink check failed at {current}: {exc}") from exc
        # Also check intermediate existing symlink that would be resolved by candidate.resolve():
        # The above is_symlink already catches it. No follow.

    # Final candidate symlink check (already done as last part, but be explicit)
    try:
        if candidate.is_symlink():
            raise WorktreeResourceSymlinkError(f"resource itself is symlink: {candidate}")
    except OSError as exc:
        raise WorktreeResourceContainmentError(f"symlink check failed: {exc}") from exc

    # Canonical containment verification (not lexical prefix only)
    try:
        cand_canonical = candidate.resolve(strict=False)
        # root_canonical already resolved strict
        cand_canonical.relative_to(root_canonical)
    except ValueError as exc:
        raise WorktreeResourceContainmentError("resource escapes bound root (canonical)") from exc
    except OSError as exc:
        raise WorktreeResourceContainmentError(f"canonical containment check failed: {exc}") from exc

    return cand_canonical


# ---------------------------------------------------------------------------
# Resolution evidence — bounded deterministic containment proof, not authority
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeResourceEvidence:
    """Bounded deterministic resolution evidence (not authority).

    Contains:
        - logical resource ref
        - project/worktree identity
        - canonical resolved path
        - containment anchor identity
        - resource type/existence observation
        - digest (integrity, not authority)
    """

    logical_ref: str
    workspace_id: str
    project_id: str
    worktree_id: str
    worktree_root: str
    canonical_path: str
    exists: bool
    kind: str

    def __post_init__(self) -> None:
        # Validate bounded fields
        if not isinstance(self.logical_ref, str) or not self.logical_ref:
            raise WorktreeResourceError("logical_ref must be non-empty string")
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise WorktreeResourceError("workspace_id must be non-empty")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise WorktreeResourceError("project_id must be non-empty")
        if not isinstance(self.worktree_id, str) or not self.worktree_id:
            raise WorktreeResourceError("worktree_id must be non-empty")
        if not isinstance(self.worktree_root, str) or not self.worktree_root:
            raise WorktreeResourceError("worktree_root must be non-empty")
        if not isinstance(self.canonical_path, str) or not self.canonical_path:
            raise WorktreeResourceError("canonical_path must be non-empty")
        if self.kind not in ("file", "directory", "missing", "other"):
            raise WorktreeResourceError(f"kind must be file/directory/missing/other, got {self.kind!r}")
        if not isinstance(self.exists, bool):
            raise WorktreeResourceError("exists must be bool")
        # exists / kind consistency (fail-closed consistency, not authority)
        if self.kind == "missing" and self.exists:
            raise WorktreeResourceError("missing kind cannot have exists=True")
        if self.kind in ("file", "directory", "other") and not self.exists:
            # allow? Actually file/directory must have exists True, missing must be False
            raise WorktreeResourceError(f"kind {self.kind!r} requires exists=True")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "exists": self.exists,
            "kind": self.kind,
            "logical_ref": self.logical_ref,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "worktree_id": self.worktree_id,
            "worktree_root": self.worktree_root,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def canonical_bytes(self) -> bytes:
        return self.canonical_json().encode("utf-8")

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    @property
    def fingerprint(self) -> str:
        return self.compute_digest()


# ---------------------------------------------------------------------------
# Resolver — separate authority domain, consumes W1 sandbox
# ---------------------------------------------------------------------------


class ProjectBoundResourceResolver:
    """Separate authority domain resolver for project-bound resources.

    Input contract: trusted W1 sandbox/binding + bounded logical relative ref.
    No arbitrary absolute root input. Authority for root comes only from
    trusted W1 binding. No CWD authority. Deterministic. Fail-closed.
    Symlink escape fail-closed. Evidence is not operation authority.
    """

    def resolve(
        self, sandbox: WorktreeSandboxBoundary, logical_ref: str
    ) -> WorktreeResourceEvidence:
        return resolve_worktree_resource(sandbox, logical_ref)


# Alias for alternative naming (implementation-specific, not frozen)
WorktreeResourceResolver = ProjectBoundResourceResolver
WorktreeBoundResourceResolver = ProjectBoundResourceResolver


def resolve_worktree_resource(
    sandbox: WorktreeSandboxBoundary, logical_ref: str
) -> WorktreeResourceEvidence:
    """Resolve a logical project-relative reference inside the bound sandbox.

    Steps:
        bound_root + logical_relative_ref → candidate → canonical containment
        verification → existence/type observation → bounded evidence.

    Fail-closed on absolute, traversal, malformed, escape, symlink.
    Deterministic. Worktree-bound. Evidence is not authority.
    """
    b = _validate_binding(sandbox)
    ref = _validate_logical_ref(logical_ref)

    root = Path(b.worktree_root)
    # root canonical (W1 already ensures canonical, but re-resolve for containment proof)
    try:
        root_canonical = root.resolve(strict=True)
    except OSError as exc:
        raise WorktreeResourceBindingError(f"bound root resolve failed: {exc}") from exc

    candidate = root / ref

    cand_canonical = _check_symlink_and_containment(root, candidate, root_canonical)

    # Resource existence / type observation (minimum generic semantics, not permission)
    # Note: candidate may not exist; kind is missing then. If exists, distinguish file/dir/other.
    # Use lstat already: symlink already rejected, so is_file/is_dir follow.
    exists = candidate.exists()
    if not exists:
        # Check if candidate is broken symlink already rejected, so missing is true missing
        # Also consider that candidate.exists() follows symlink; we already rejected symlink
        kind = "missing"
    else:
        # Need to be careful: candidate.exists() true, but is it file or dir?
        # Use is_file / is_dir after symlink rejection.
        # If neither, it's other (fifo, socket, etc.)
        try:
            if candidate.is_file():
                kind = "file"
            elif candidate.is_dir():
                kind = "directory"
            else:
                kind = "other"
        except OSError:
            kind = "other"

    evidence = WorktreeResourceEvidence(
        logical_ref=ref,
        workspace_id=b.workspace_id,
        project_id=b.project_id,
        worktree_id=b.worktree_id,
        worktree_root=b.worktree_root,
        canonical_path=str(cand_canonical),
        exists=exists,
        kind=kind,
    )
    return evidence


# Convenience single-call alias
resolve_project_bound_resource = resolve_worktree_resource
resolve_resource = resolve_worktree_resource

__all__ = [
    "WorktreeResourceEvidence",
    "ProjectBoundResourceResolver",
    "WorktreeResourceResolver",
    "WorktreeBoundResourceResolver",
    "resolve_worktree_resource",
    "resolve_project_bound_resource",
    "resolve_resource",
    "WorktreeResourceError",
    "WorktreeResourceReferenceError",
    "WorktreeResourceBindingError",
    "WorktreeResourceContainmentError",
    "WorktreeResourceSymlinkError",
    "MAX_LOGICAL_REF_LENGTH",
    "RESOURCE_RESOLUTION_IS_OPERATION_AUTHORITY",
    "RESOURCE_RESOLUTION_IS_MUTATION_AUTHORITY",
    "RESOURCE_TYPE_IS_OPERATION_AUTHORITY",
    "RESOLUTION_RESULT_IS_EVIDENCE",
    "RESOLUTION_RESULT_IS_AUTHORITY",
    "DIGEST_IS_AUTHORITY",
    "RESOLUTION_EVIDENCE_IS_USE_TIME_AUTHORITY",
    "TOCTOU_BOUNDARY_EXPLICIT",
    "TOCTOU_ELIMINATED",
    "TOCTOU_EXPLANATION",
    "ARBITRARY_ABSOLUTE_RESOURCE_INPUT",
    "UNTRUSTED_ROOT_SELF_AUTHORIZES",
    "RESOURCE_REFERENCE_RELATIVE",
    "RESOURCE_TRAVERSAL_FAIL_CLOSED",
    "CWD_IS_AUTHORITY",
    "RESOLVED_PATH_WITHIN_BOUND_ROOT",
    "PATH_ESCAPE_FAIL_CLOSED",
    "SYMLINK_ESCAPE_FAIL_CLOSED",
    "RESOURCE_RESOLUTION_DETERMINISTIC",
    "RESOURCE_RESOLUTION_WORKTREE_BOUND",
    "CROSS_WORKTREE_RESOURCE_REUSE_AUTHORITY",
    "HOST_TRUSTED_RESOURCE_RESOLVER_RETAIN",
    "RESOURCE_KINDS_PROJECT_EXPANSION",
    "PROJECT_BOUND_RESOLVER_SEPARATE_AUTHORITY_DOMAIN",
]
