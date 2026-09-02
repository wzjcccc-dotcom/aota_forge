"""Project / Worktree Identity & Sandbox Boundary (S2 M1-W1).

Trusted boundary that later W2/W3 consume. Establishes:

    logical project/worktree identity
            ↓
    trusted physical binding
            ↓
    sandbox / containment authority boundary

Invariants
----------
* PROJECT_RESOLUTION_EVIDENCE_IS_OPERATION_AUTHORITY=no
* SANDBOX_BOUNDARY_IS_OPERATION_AUTHORITY=no
* PATH_CONTAINMENT_IS_OPERATION_AUTHORITY=no
* AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY=no
* WORK_ROLE_IS_TOOL_PERMISSION=no
* BOUNDED_SCOPE_IS_FILESYSTEM_ACL=no
* RESOLVER_SUCCESS_IS_MUTATION_AUTHORITY=no
* PHYSICAL_ROOT_BOUND_BEFORE_FILE_DISCOVERY=yes
* WORKTREE_ROOT_IS_CONTAINMENT_ANCHOR=yes
* CONTAINMENT_PROOF_DOES_NOT_AUTHORIZE_OPERATION=yes
* PROJECT_IDENTITY_DISTINCT_FROM_WORKTREE_INSTANCE=yes
* DIGEST_IS_AUTHORITY=no
* PHYSICAL_LAYER_MAY_HOLD_PATH=yes
* UNTRUSTED_PATH_SELF_AUTHORIZES=no
* No AGENTS discovery, no Tool system, no Skill system, no lifecycle manager.

The trusted binding originates from Forge/task-main/runtime lifecycle inputs
via ProjectResolutionEvidence (existing resolver) plus an explicit trusted
worktree identity and physical root. Raw untrusted paths, TaskHandoff text,
Skill content, AGENTS content never self-authorize.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.project.resolver import ProjectResolutionEvidence

# ---------------------------------------------------------------------------
# Bounds & patterns
# ---------------------------------------------------------------------------

MAX_WORKTREE_ID_LENGTH: int = 128
MAX_WORKSPACE_ID_LENGTH: int = 128
MAX_PROJECT_ID_LENGTH: int = 96

# worktree_id charset: start alnum, then alnum . _ -
_WORKTREE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Digest is 64 lower hex
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class WorktreeSandboxError(ValueError):
    """Base fail-closed error for worktree sandbox binding."""


class WorktreeIdentityError(WorktreeSandboxError):
    """Malformed worktree identity."""


class WorktreeRootError(WorktreeSandboxError):
    """Unsafe or missing physical worktree root."""


class ProjectEvidenceError(WorktreeSandboxError):
    """Invalid or ambiguous project resolution evidence."""


class ProjectWorktreeMismatchError(WorktreeSandboxError):
    """Project/worktree mismatch fail-closed."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_worktree_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorktreeIdentityError(f"worktree_id must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise WorktreeIdentityError("worktree_id must be non-empty")
    if len(v) > MAX_WORKTREE_ID_LENGTH:
        raise WorktreeIdentityError(f"worktree_id length {len(v)} exceeds max {MAX_WORKTREE_ID_LENGTH}")
    if "/" in v or "\\" in v:
        raise WorktreeIdentityError(f"worktree_id must not contain path separators: {value!r}")
    if not _WORKTREE_ID_RE.fullmatch(v):
        raise WorktreeIdentityError(f"worktree_id has invalid charset: {value!r}")
    # reject raw path markers
    if v.startswith("/") or ".." in v.split("/"):
        raise WorktreeIdentityError(f"worktree_id must not be path-like: {value!r}")
    return v


def _validate_id(value: str, field_name: str, max_len: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorktreeSandboxError(f"{field_name} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise WorktreeSandboxError(f"{field_name} must be non-empty")
    if len(v) > max_len:
        raise WorktreeSandboxError(f"{field_name} length {len(v)} exceeds {max_len}")
    if pattern is not None and not pattern.fullmatch(v):
        raise WorktreeSandboxError(f"{field_name} has invalid charset: {value!r}")
    return v


def _canonicalize_root(root: Path) -> str:
    """Validate and canonicalize physical worktree root deterministically.

    Checks:
        - exists
        - is directory
        - itself is not an unsafe symlink
        - canonicalized physical root is deterministic (resolve)

    Does NOT implement full descendant symlink/TOCTOU matrix (W4).
    """
    if not isinstance(root, Path):
        raise WorktreeRootError(f"worktree_root must be a Path, got {type(root).__name__}")
    # Reject empty path explicitly (Path("") -> '.' which would otherwise pass)
    if str(root).strip() == "" or str(root) == "." and root == Path(""):
        raise WorktreeRootError(f"worktree_root must not be empty: {root!r}")
    # Baseline: exists and is directory
    try:
        # lstat to detect symlink without following
        is_symlink = root.is_symlink()
    except OSError as exc:
        raise WorktreeRootError(f"worktree_root inaccessible: {exc}") from exc
    if is_symlink:
        raise WorktreeRootError(f"worktree_root itself must not be a symlink: {root}")
    if not root.exists():
        raise WorktreeRootError(f"worktree_root does not exist: {root}")
    if not root.is_dir():
        raise WorktreeRootError(f"worktree_root is not a directory: {root}")
    # Deterministic canonical: resolve without strict (but we already checked exists)
    try:
        canonical = root.resolve(strict=True)
    except OSError as exc:
        raise WorktreeRootError(f"worktree_root resolve failed: {exc}") from exc
    # Double-check resolved is still not symlink target confusion
    # resolve() follows, but we already rejected symlink at input level baseline
    return str(canonical)


def _validate_evidence(evidence: object) -> ProjectResolutionEvidence:
    if not isinstance(evidence, ProjectResolutionEvidence):
        raise ProjectEvidenceError(f"evidence must be ProjectResolutionEvidence, got {type(evidence).__name__}")
    # Must be singular/trusted
    if evidence.status != "RESOLVED":
        if evidence.status == "PROJECT_NOT_FOUND":
            raise ProjectEvidenceError("project evidence not found: missing evidence fails closed")
        if evidence.status == "NEEDS_SEMANTIC_CHOICE":
            raise ProjectEvidenceError("project evidence ambiguous: NEEDS_SEMANTIC_CHOICE fails closed")
        raise ProjectEvidenceError(f"evidence status not RESOLVED: {evidence.status!r}")
    if len(evidence.candidates) != 1:
        if len(evidence.candidates) == 0:
            raise ProjectEvidenceError("evidence has no candidates: missing")
        raise ProjectEvidenceError(f"evidence ambiguous: {len(evidence.candidates)} candidates")
    # workspace_id / project_id present in candidate
    candidate = evidence.candidates[0]
    if not candidate.workspace_id or not candidate.project_id:
        raise ProjectEvidenceError("evidence candidate missing workspace_id or project_id")
    return evidence


# ---------------------------------------------------------------------------
# Sandbox boundary — trusted binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeSandboxBoundary:
    """Fail-closed trusted worktree sandbox boundary.

    Holds:
        - one logical workspace identity
        - one logical project identity
        - one worktree identity (distinct physical checkout instance)
        - one canonical physical worktree root
        - relationship to project resolution evidence (fingerprints)
        - deterministic digest

    This is a containment anchor, NOT an operation/mutation authority.
    """

    workspace_id: str
    workspace_root: str
    project_id: str
    project_root: str
    worktree_id: str
    worktree_root: str
    registry_fingerprint: str
    candidate_fingerprint: str

    def __post_init__(self) -> None:
        # Validate all fields are bounded deterministic strings already
        # (values come from factory, but we re-validate for frozen invariants)
        object.__setattr__(self, "workspace_id", _validate_id(self.workspace_id, "workspace_id", MAX_WORKSPACE_ID_LENGTH))
        object.__setattr__(self, "project_id", _validate_id(self.project_id, "project_id", MAX_PROJECT_ID_LENGTH))
        object.__setattr__(self, "worktree_id", _validate_worktree_id(self.worktree_id))
        # roots are canonical strings already validated
        if not isinstance(self.workspace_root, str) or not self.workspace_root:
            raise WorktreeSandboxError("workspace_root must be non-empty string")
        if not isinstance(self.project_root, str) or not self.project_root:
            raise WorktreeSandboxError("project_root must be non-empty string")
        if not isinstance(self.worktree_root, str) or not self.worktree_root:
            raise WorktreeSandboxError("worktree_root must be non-empty string")
        # fingerprints: 64 hex
        for label in ("registry_fingerprint", "candidate_fingerprint"):
            val = getattr(self, label)
            if not isinstance(val, str) or not _DIGEST_RE.fullmatch(val):
                raise WorktreeSandboxError(f"{label} must be 64 lower hex digest")

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical representation."""
        return {
            "candidate_fingerprint": self.candidate_fingerprint,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "registry_fingerprint": self.registry_fingerprint,
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "worktree_id": self.worktree_id,
            "worktree_root": self.worktree_root,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def canonical_bytes(self) -> bytes:
        return self.canonical_json().encode("utf-8")

    def compute_digest(self) -> str:
        """Deterministic identity digest (NOT authority)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    @property
    def fingerprint(self) -> str:
        return self.compute_digest()

    @property
    def sandbox_fingerprint(self) -> str:
        return self.compute_digest()

    # Containment anchor — does NOT authorize operation
    def is_contained(self, path: str | Path) -> bool:
        """Return True iff *path* is contained under the bound worktree root.

        This is a containment proof, NOT an operation/mutation authorization.
        """
        if isinstance(path, str):
            cand = Path(path)
        elif isinstance(path, Path):
            cand = path
        else:
            raise TypeError(f"path must be str or Path, got {type(path).__name__}")
        try:
            # Deterministic lexical containment via resolved paths
            root = Path(self.worktree_root).resolve(strict=False)
            # Resolve candidate non-strict to avoid requiring existence
            resolved = cand.resolve(strict=False) if cand.is_absolute() else (root / cand).resolve(strict=False)
            # Use relative_to for containment check
            resolved.relative_to(root)
            # Also reject if candidate path components would escape via .. without resolve
            # relative_to already handles after resolve
            return True
        except ValueError:
            return False
        except OSError:
            return False

    def contains(self, path: str | Path) -> bool:
        """Alias for is_contained (containment anchor)."""
        return self.is_contained(path)

    # Expose physical layer path holder (not authority)
    @property
    def worktree_root_path(self) -> Path:
        return Path(self.worktree_root)

    @property
    def project_root_path(self) -> Path:
        return Path(self.project_root)

    @property
    def workspace_root_path(self) -> Path:
        return Path(self.workspace_root)

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()


# ---------------------------------------------------------------------------
# Factory — trusted lifecycle binding only
# ---------------------------------------------------------------------------


def bind_worktree_sandbox(
    evidence: ProjectResolutionEvidence,
    worktree_id: str,
    worktree_root: Path | str,
    *,
    expected_workspace_id: str | None = None,
    expected_project_id: str | None = None,
) -> WorktreeSandboxBoundary:
    """Bind a trusted worktree sandbox from evidence + trusted lifecycle inputs.

    Parameters
    ----------
    evidence: ProjectResolutionEvidence
        Must be singular RESOLVED evidence from the existing project resolver
        (reuse). Ambiguous or missing evidence fails closed.
    worktree_id: str
        Trusted worktree identity (distinct from project_id), supplied via
        Forge/task-main/runtime lifecycle — never from raw TaskHandoff text.
    worktree_root: Path | str
        Trusted physical worktree root, supplied via lifecycle. Must exist,
        be a directory, not be a symlink, and be deterministically canonical.
    expected_workspace_id / expected_project_id:
        Optional cross-check for mismatch fail-closed. If provided, must match
        evidence candidate.

    Returns
    -------
    WorktreeSandboxBoundary
        Deterministic bound sandbox (containment anchor, not authority).

    Fail-closed conditions cover T15-T20 and T17 mismatch cases.
    """
    # 1. Validate evidence is singular trusted (reuse existing resolver)
    ev = _validate_evidence(evidence)
    candidate = ev.candidates[0]

    # 2. Validate worktree identity (distinct concept from project identity)
    wid = _validate_worktree_id(worktree_id)

    # 3. Validate physical root baseline
    root_path = Path(worktree_root) if isinstance(worktree_root, str) else worktree_root
    canonical_root = _canonicalize_root(root_path)

    # 4. Workspace / project mismatch fail-closed if expected provided
    if expected_workspace_id is not None:
        exp_ws = expected_workspace_id.strip()
        if exp_ws != candidate.workspace_id:
            raise ProjectWorktreeMismatchError(
                f"workspace mismatch: expected {exp_ws!r} != evidence {candidate.workspace_id!r}"
            )
    if expected_project_id is not None:
        exp_pid = expected_project_id.strip()
        if exp_pid != candidate.project_id:
            raise ProjectWorktreeMismatchError(
                f"project mismatch: expected {exp_pid!r} != evidence {candidate.project_id!r}"
            )

    # 5. Worktree vs project identity distinctness: they are separate fields
    # We do not collapse them; even if strings equal by coincidence, types are distinct slots
    # To emphasize distinction, we keep both; no heuristic picks one over the other

    # 6. Canonical workspace/project roots from evidence (deterministic)
    # candidate already carries canonical workspace_root/project_root
    ws_root = candidate.workspace_root
    proj_root = candidate.project_root
    # Ensure ws_root/proj_root are canonical strings (evidence already provides)
    # Re-canonicalize project_root via Path if needed for determinism: use stored string

    # 7. Construct boundary — deterministic
    boundary = WorktreeSandboxBoundary(
        workspace_id=candidate.workspace_id,
        workspace_root=ws_root,
        project_id=candidate.project_id,
        project_root=proj_root,
        worktree_id=wid,
        worktree_root=canonical_root,
        registry_fingerprint=candidate.registry_fingerprint,
        candidate_fingerprint=candidate.candidate_fingerprint,
    )
    return boundary


# Alias for alternative naming (downstream compatibility)
establish_worktree_sandbox = bind_worktree_sandbox
create_sandbox_boundary = bind_worktree_sandbox
bind_sandbox_boundary = bind_worktree_sandbox

# Marker flags for tests / downstream seam checks
SANDBOX_BOUNDARY_IS_OPERATION_AUTHORITY: bool = False
PATH_CONTAINMENT_IS_OPERATION_AUTHORITY: bool = False
AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY: bool = False
WORK_ROLE_IS_TOOL_PERMISSION: bool = False
BOUNDED_SCOPE_IS_FILESYSTEM_ACL: bool = False
RESOLVER_SUCCESS_IS_MUTATION_AUTHORITY: bool = False
PROJECT_RESOLUTION_EVIDENCE_IS_OPERATION_AUTHORITY: bool = False
HANDOFF_RAW_PATH_IS_WORKTREE_AUTHORITY: bool = False
SKILL_RAW_PATH_IS_WORKTREE_AUTHORITY: bool = False
AGENTS_CONTENT_IS_WORKTREE_AUTHORITY: bool = False
SANDBOX_BEFORE_AGENTS_READ: bool = True
PHYSICAL_ROOT_BOUND_BEFORE_FILE_DISCOVERY: bool = True
WORKTREE_ROOT_IS_CONTAINMENT_ANCHOR: bool = True
PROJECT_IDENTITY_DISTINCT_FROM_WORKTREE_INSTANCE: bool = True
EXISTING_PROJECT_RESOLVER_REUSED: bool = True
NEW_PROJECT_DISCOVERY_SYSTEM_CREATED: bool = False

__all__ = [
    "WorktreeSandboxBoundary",
    "bind_worktree_sandbox",
    "establish_worktree_sandbox",
    "create_sandbox_boundary",
    "bind_sandbox_boundary",
    "WorktreeSandboxError",
    "WorktreeIdentityError",
    "WorktreeRootError",
    "ProjectEvidenceError",
    "ProjectWorktreeMismatchError",
    "MAX_WORKTREE_ID_LENGTH",
    "SANDBOX_BOUNDARY_IS_OPERATION_AUTHORITY",
    "PATH_CONTAINMENT_IS_OPERATION_AUTHORITY",
    "AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY",
    "WORK_ROLE_IS_TOOL_PERMISSION",
    "BOUNDED_SCOPE_IS_FILESYSTEM_ACL",
    "RESOLVER_SUCCESS_IS_MUTATION_AUTHORITY",
    "PROJECT_RESOLUTION_EVIDENCE_IS_OPERATION_AUTHORITY",
    "HANDOFF_RAW_PATH_IS_WORKTREE_AUTHORITY",
    "SKILL_RAW_PATH_IS_WORKTREE_AUTHORITY",
    "AGENTS_CONTENT_IS_WORKTREE_AUTHORITY",
    "SANDBOX_BEFORE_AGENTS_READ",
    "PHYSICAL_ROOT_BOUND_BEFORE_FILE_DISCOVERY",
    "WORKTREE_ROOT_IS_CONTAINMENT_ANCHOR",
    "PROJECT_IDENTITY_DISTINCT_FROM_WORKTREE_INSTANCE",
    "EXISTING_PROJECT_RESOLVER_REUSED",
    "NEW_PROJECT_DISCOVERY_SYSTEM_CREATED",
]
