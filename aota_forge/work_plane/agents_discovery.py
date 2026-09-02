"""AGENTS Physical Discovery → S1 AgentsPolicyCandidate Bridge (S2 M1-W3).

Trusted physical → semantic bridge:

    W1 trusted sandbox / bound worktree root
            ↓
    deterministic AGENTS physical discovery inside that root
            ↓
    bounded verified AGENTS content
            ↓
    existing S1 AgentsPolicyCandidate (pre-resolved trusted semantic candidate)

Invariants
----------
* WORKTREE_BINDING_BEFORE_PHYSICAL_DISCOVERY=yes
* SANDBOX_BEFORE_AGENTS_READ=yes
* AGENTS_DISCOVERY_BOUNDED=yes
* AGENTS_DISCOVERY_DETERMINISTIC=yes
* UNBOUNDED_RECURSIVE_SCAN=no
* AGENTS_SYMLINK_ESCAPE_FAIL_CLOSED=yes
* S1_POLICY_CONTENT_BOUND_REUSED=yes
* AGENTS_SILENT_TRUNCATION=no
* S1_POLICY_DIGEST_SEMANTICS_REUSED=yes
* AGENTS_POLICY_CANDIDATE_REUSED=yes
* AGENTS_POLICY_CANDIDATE_SCHEMA_CHANGED=no
* POLICY_ID_DETERMINISTIC=yes
* PROVENANCE_REF_IS_LOGICAL=yes
* PROVENANCE_REF_IS_AUTHORITY=no
* CANDIDATE_PROJECT_ID_FROM_TRUSTED_BINDING=yes
* PHYSICAL_DIRECTORY_TO_LOGICAL_SCOPE_DETERMINISTIC=yes
* FILESYSTEM_ENUMERATION_ORDER_IS_AUTHORITY=no
* PHYSICAL_DISCOVERY_IS_POLICY_AUTHORITY=no
* S1_APPLICABILITY_REUSED=yes
* S1_CONFLICT_FAIL_CLOSED_RETAINED=yes
* DEFAULT_AGENTS_POLICY_FABRICATED=no
* AGENTS_FILE_EXISTENCE_GRANTS_AUTHORITY=no
* AGENTS_CONTENT_GRANTS_FILESYSTEM_AUTHORITY=no
* AGENTS_CONTENT_GRANTS_TOOL_AUTHORITY=no
* SKILL_LOADING_IMPLEMENTED_IN_W3=no
* TOOL_SYSTEM_IMPLEMENTED_IN_W3=no
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from aota_forge.work_plane.agents_applicability import (
    AgentsPolicyCandidate,
    compute_policy_digest,
    MAX_CONTENT_LENGTH,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Public flags — invariants for tests / downstream seam
# ---------------------------------------------------------------------------

WORKTREE_BINDING_BEFORE_PHYSICAL_DISCOVERY: bool = True
SANDBOX_BEFORE_AGENTS_READ: bool = True

AGENTS_DISCOVERY_BOUNDED: bool = True
AGENTS_DISCOVERY_DETERMINISTIC: bool = True
UNBOUNDED_RECURSIVE_SCAN: bool = False

AGENTS_SYMLINK_ESCAPE_FAIL_CLOSED: bool = True

S1_POLICY_CONTENT_BOUND_REUSED: bool = True
AGENTS_SILENT_TRUNCATION: bool = False
S1_POLICY_DIGEST_SEMANTICS_REUSED: bool = True

AGENTS_POLICY_CANDIDATE_REUSED: bool = True
AGENTS_POLICY_CANDIDATE_SCHEMA_CHANGED: bool = False

POLICY_ID_DETERMINISTIC: bool = True
PROVENANCE_REF_IS_LOGICAL: bool = True
PROVENANCE_REF_IS_AUTHORITY: bool = False

CANDIDATE_PROJECT_ID_FROM_TRUSTED_BINDING: bool = True
CALLER_PROJECT_ID_SELF_AUTHORIZES: bool = False
PHYSICAL_DIRECTORY_TO_LOGICAL_SCOPE_DETERMINISTIC: bool = True
FILESYSTEM_ENUMERATION_ORDER_IS_AUTHORITY: bool = False

PHYSICAL_DISCOVERY_IS_POLICY_AUTHORITY: bool = False
S1_APPLICABILITY_REUSED: bool = True
S1_CONFLICT_FAIL_CLOSED_RETAINED: bool = True

DEFAULT_AGENTS_POLICY_FABRICATED: bool = False
AGENTS_FILE_EXISTENCE_GRANTS_AUTHORITY: bool = False
AGENTS_CONTENT_GRANTS_FILESYSTEM_AUTHORITY: bool = False
AGENTS_CONTENT_GRANTS_TOOL_AUTHORITY: bool = False
AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY: bool = False

SKILL_LOADING_IMPLEMENTED_IN_W3: bool = False
TOOL_SYSTEM_IMPLEMENTED_IN_W3: bool = False
BOOTSTRAP_CONTRACT_CHANGED: bool = False
S1_AGENTS_SEMANTIC_CONTRACT_REUSED: bool = True
NEW_AGENTS_POLICY_ONTOLOGY_CREATED: bool = False
GENERIC_PROJECT_RESOURCE_RESOLVER_CREATED_IN_W3: bool = False
W2_DEPENDENCY_INTRODUCED_IN_W3: bool = False
W1_SANDBOX_CONTRACT_REUSED: bool = True
ARBITRARY_AGENTS_ROOT_INPUT: bool = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENTS_FILENAME: str = "AGENTS.md"

# Scope validation — reuse S1 semantics (bounded, deterministic)
_MAX_SCOPE_LENGTH: int = 256
_MAX_SCOPE_COMPONENT_LENGTH: int = 64
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class AgentsDiscoveryError(ValueError):
    """Base fail-closed error for AGENTS physical discovery."""


class AgentsScopeError(AgentsDiscoveryError):
    """Malformed logical target scope."""


class AgentsSymlinkEscapeError(AgentsDiscoveryError):
    """AGENTS material escapes bound worktree via symlink."""


class AgentsContentError(AgentsDiscoveryError):
    """Malformed or unreadable AGENTS content (fail-closed)."""


class AgentsOversizedError(AgentsDiscoveryError):
    """AGENTS content exceeds bounded capacity (fail-closed)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_target_scope(value: object) -> str:
    """Validate logical target scope deterministically (S1 semantics).

    Returns normalized scope: "" for root, or "a/b/c" canonical.
    Rejects absolute, traversal, backslash, malformed.
    """
    if not isinstance(value, str) or type(value) is not str:
        raise AgentsScopeError(f"target_scope must be a string, got {type(value).__name__}")
    v = value.strip()
    if v == "" or v == "root":
        return ""
    if len(v) > _MAX_SCOPE_LENGTH:
        raise AgentsScopeError(f"scope length {len(v)} exceeds max {_MAX_SCOPE_LENGTH}")
    # Reject raw absolute / traversal / backslash before component checks
    if v.startswith("/"):
        raise AgentsScopeError(f"scope must not be absolute: {value!r}")
    if v.endswith("/"):
        raise AgentsScopeError(f"scope must not have trailing '/': {value!r}")
    if "//" in v:
        raise AgentsScopeError(f"scope must not contain '//': {value!r}")
    if "\\" in v:
        raise AgentsScopeError(f"scope must not contain backslash: {value!r}")
    parts = v.split("/")
    # also reject any ".." segment explicitly
    for p in parts:
        if p == "" or p == "." or p == "..":
            raise AgentsScopeError(f"scope contains invalid segment {p!r}: {value!r}")
        if len(p) > _MAX_SCOPE_COMPONENT_LENGTH:
            raise AgentsScopeError(f"scope component {p!r} exceeds max {_MAX_SCOPE_COMPONENT_LENGTH}")
        if not _COMPONENT_RE.fullmatch(p):
            raise AgentsScopeError(f"scope component {p!r} has invalid charset")
        if ".." in p or "\\" in p:
            raise AgentsScopeError(f"scope component {p!r} must not contain traversal")
    # Canonical form
    return "/".join(parts)


def _validate_sandbox(sandbox: object) -> WorktreeSandboxBoundary:
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise AgentsDiscoveryError(
            f"sandbox must be WorktreeSandboxBoundary (trusted W1 binding), got {type(sandbox).__name__}"
        )
    # Ensure bound root still exists and remains canonical (W1 already validated)
    # Fail-closed if worktree_root string empty
    if not isinstance(sandbox.worktree_root, str) or not sandbox.worktree_root:
        raise AgentsDiscoveryError("sandbox worktree_root must be non-empty")
    return sandbox


def _scope_to_provenance(scope: str) -> str:
    """Logical provenance ref for a discovered scope (not filesystem authority)."""
    if scope == "":
        return "agents:AGENTS.md"
    return f"agents:{scope}/AGENTS.md"


def _scope_to_policy_id(scope: str, content_digest: str) -> str:
    """Deterministic bounded policy_id from logical scope + digest.

    Must not use absolute path, mtime, inode, random, enumeration order.
    Derived from bounded logical identity/scope/digest.
    """
    # Use hash of scope for collision-free deterministic identity
    if scope == "":
        base = "root"
    else:
        # 12 hex chars from sha256(scope) — deterministic, bounded
        scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
        base = f"scope-{scope_hash}"
    # Include digest prefix for distinct identity per content (still deterministic)
    # Length: "agents-" (7) + base (up to 18) + "-" (1) + 8 = <=34 < 128
    return f"agents-{base}-{content_digest[:8]}"


def _check_dir_symlinks(root: Path, scope: str) -> None:
    """Fail-closed if any directory along scope escapes via symlink.

    Checks each ancestor directory prefix for symlink.
    """
    if scope == "":
        return
    parts = scope.split("/")
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        dir_path = root / Path(prefix)
        # Existence check without following? Use lstat via is_symlink
        try:
            if dir_path.is_symlink():
                raise AgentsSymlinkEscapeError(
                    f"nested directory symlink escape at scope {prefix!r}: {dir_path}"
                )
        except OSError as exc:
            raise AgentsSymlinkEscapeError(f"nested directory symlink check failed for {prefix!r}: {exc}") from exc
        # Also check that resolved dir (if exists) stays within root
        # If dir exists and is a directory, ensure its resolved path is contained
        try:
            if dir_path.exists():
                # Resolve strictly if possible, otherwise non-strict
                try:
                    resolved = dir_path.resolve(strict=True)
                except OSError:
                    resolved = dir_path.resolve(strict=False)
                try:
                    resolved.relative_to(root.resolve(strict=False))
                except ValueError:
                    raise AgentsSymlinkEscapeError(
                        f"nested directory escapes root at scope {prefix!r}: {resolved} not under {root}"
                    ) from None
                except OSError as exc:
                    raise AgentsSymlinkEscapeError(f"directory containment check failed for {prefix!r}: {exc}") from exc
        except AgentsSymlinkEscapeError:
            raise
        except OSError as exc:
            raise AgentsSymlinkEscapeError(f"directory check failed for {prefix!r}: {exc}") from exc


def _check_physical_containment(root: Path, candidate_path: Path) -> None:
    """Fail-closed if candidate physical path resolves outside root."""
    try:
        # Use strict=False to avoid requiring existence beyond our earlier check,
        # but for existing file we can strict
        if candidate_path.exists():
            resolved = candidate_path.resolve(strict=True)
        else:
            resolved = candidate_path.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise AgentsSymlinkEscapeError(
                f"candidate path escapes root: {resolved} not under {root_resolved}"
            ) from None
    except AgentsSymlinkEscapeError:
        raise
    except OSError as exc:
        raise AgentsSymlinkEscapeError(f"containment check failed for {candidate_path}: {exc}") from exc


def _read_agents_content(path: Path) -> str:
    """Read AGENTS.md deterministically as UTF-8, fail-closed.

    Checks:
    - regular file (not symlink, not dir, not fifo)
    - containment already checked
    - read bytes, decode utf-8 strict
    - oversized => fail-closed (no silent truncation)
    """
    # Symlink check first (fail-closed)
    try:
        if path.is_symlink():
            raise AgentsSymlinkEscapeError(f"AGENTS.md symlink rejected: {path}")
    except OSError as exc:
        raise AgentsSymlinkEscapeError(f"AGENTS.md symlink check failed: {exc}") from exc

    # Must be regular file
    try:
        if not path.exists():
            raise FileNotFoundError(str(path))
        if not path.is_file():
            raise AgentsContentError(f"AGENTS.md is not a regular file: {path}")
        # Additional safety: ensure not a directory
        if path.is_dir():
            raise AgentsContentError(f"AGENTS.md is a directory: {path}")
    except AgentsDiscoveryError:
        raise
    except OSError as exc:
        raise AgentsContentError(f"AGENTS.md file type check failed: {exc}") from exc

    # Size pre-check for bounding (avoid reading huge files)
    try:
        size = path.stat().st_size
        # Content bound is on character length (MAX_CONTENT_LENGTH) after utf-8 decode,
        # but file size in bytes exceeding bound is already suspicious;
        # use bytes size > MAX_CONTENT_LENGTH * 4 as early fail (utf-8 max 4 bytes per char)
        # However final decision is on character length strictly.
        # For deterministic fail-closed, we still read and check length, but pre-check avoids OOM.
        if size > MAX_CONTENT_LENGTH * 4 + 1024:  # allow some overhead for utf-8
            # Still need to confirm it's truly oversized after decode vs just large bytes
            # Fail based on byte size exceeding reasonable bound for 32KiB content
            # For strict S1 bound (32*1024 chars), max bytes is 32*1024*4 = 131072
            raise AgentsOversizedError(
                f"AGENTS.md size {size} exceeds bounded capacity (max {MAX_CONTENT_LENGTH} chars)"
            )
    except AgentsDiscoveryError:
        raise
    except OSError as exc:
        raise AgentsContentError(f"AGENTS.md stat failed: {exc}") from exc

    # Read with deterministic UTF-8
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgentsContentError(f"AGENTS.md unreadable: {exc}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentsContentError(f"AGENTS.md malformed encoding (must be utf-8): {exc}") from exc
    except Exception as exc:
        raise AgentsContentError(f"AGENTS.md decode failed: {exc}") from exc

    # Content bound — reuse S1 MAX_CONTENT_LENGTH
    if len(text) > MAX_CONTENT_LENGTH:
        raise AgentsOversizedError(
            f"AGENTS.md content length {len(text)} exceeds bounded max {MAX_CONTENT_LENGTH}"
        )

    # No silent truncation — we already fail-closed, never truncate
    return text


# ---------------------------------------------------------------------------
# Public discovery
# ---------------------------------------------------------------------------


def discover_agents(
    sandbox: WorktreeSandboxBoundary,
    target_scope: str = "",
) -> tuple[AgentsPolicyCandidate, ...]:
    """Deterministically discover AGENTS candidates inside trusted bound root.

    Requires W1 trusted sandbox binding before any AGENTS read.

    Parameters
    ----------
    sandbox: WorktreeSandboxBoundary
        Trusted W1 binding (worktree_root, project_id).
        Arbitrary filesystem root input is not accepted.
    target_scope: str
        Logical target scope for nested discovery.
        "" or "root" => root only.
        "src" => root + src
        "src/pkg" => root + src + src/pkg
        Rejected if absolute, traversal, backslash, malformed.

    Returns
    -------
    Tuple of AgentsPolicyCandidate ordered deterministically
    (specificity asc, scope lexical, policy_id).

    Discovery is bounded to ancestor chain only — no unbounded rglob.

    Fail-closed on symlink escape, oversized, malformed.
    Zero candidates is valid when no AGENTS.md exists along chain.
    Does NOT decide final semantic winner; use S1 resolve_applicable_policies.
    """
    sb = _validate_sandbox(sandbox)
    normalized = _validate_target_scope(target_scope)

    root = sb.worktree_root_path  # Path already canonical from W1
    project_id = sb.project_id

    # Build ancestor scope chain deterministically
    if normalized == "":
        scopes: list[str] = [""]
    else:
        parts = normalized.split("/")
        scopes = [""] + ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]

    candidates: list[AgentsPolicyCandidate] = []

    for scope in scopes:
        # Determine physical candidate path
        if scope == "":
            phys = root / AGENTS_FILENAME
        else:
            phys = root / Path(scope) / AGENTS_FILENAME

        # Nested directory symlink escape check (fail-closed even before file existence
        # if the directory itself is a symlink escaping root)
        _check_dir_symlinks(root, scope)

        # Physical containment check for candidate path (symlink target outside root)
        # This catches AGENTS.md symlink pointing outside as well as dir symlink escape
        # Do it irrespective of existence to catch escape attempts early
        # But only if the path or its parent exists as symlink/real path
        # We perform check optimistically via is_symlink + resolve containment
        try:
            if phys.is_symlink():
                raise AgentsSymlinkEscapeError(f"AGENTS.md symlink escape at scope {scope!r}: {phys}")
            # If parent dir is symlink, _check_dir_symlinks already raised
            # For candidate path itself, also ensure resolved containment if exists
            if phys.exists():
                _check_physical_containment(root, phys)
        except AgentsDiscoveryError:
            raise

        # If file does not exist, valid empty for this scope
        if not phys.exists():
            continue

        # At this point file exists and symlink checks passed; do containment again strict
        _check_physical_containment(root, phys)

        # Read bounded content (fail-closed on malformed/oversized)
        content = _read_agents_content(phys)

        # Digest reuse S1 semantics
        digest = compute_policy_digest(content)

        # Deterministic policy_id (no absolute path, no mtime, no random)
        policy_id = _scope_to_policy_id(scope, digest)

        # Logical provenance ref (not filesystem authority)
        provenance = _scope_to_provenance(scope)

        # Project must be from trusted binding, not caller
        candidate = AgentsPolicyCandidate(
            policy_id=policy_id,
            project_id=project_id,
            scope=scope,
            content=content,
            content_digest=digest,
            provenance_ref=provenance,
        )
        candidates.append(candidate)

    # Deterministic ordering — not filesystem enumeration order
    # S1 ordering: specificity asc, scope lexical, policy_id lexical
    def _specificity(s: str) -> int:
        if s == "":
            return 0
        return s.count("/") + 1

    ordered = sorted(candidates, key=lambda c: (_specificity(c.scope), c.scope, c.policy_id, c.content_digest or ""))
    return tuple(ordered)


# Alias for ergonomics / plan language
discover_agents_policies = discover_agents
discover_agents_candidates = discover_agents
discover_bound_agents = discover_agents

__all__ = [
    "discover_agents",
    "discover_agents_policies",
    "discover_agents_candidates",
    "discover_bound_agents",
    "AgentsDiscoveryError",
    "AgentsScopeError",
    "AgentsSymlinkEscapeError",
    "AgentsContentError",
    "AgentsOversizedError",
    "AGENTS_FILENAME",
    "WORKTREE_BINDING_BEFORE_PHYSICAL_DISCOVERY",
    "SANDBOX_BEFORE_AGENTS_READ",
    "AGENTS_DISCOVERY_BOUNDED",
    "AGENTS_DISCOVERY_DETERMINISTIC",
    "UNBOUNDED_RECURSIVE_SCAN",
    "AGENTS_SYMLINK_ESCAPE_FAIL_CLOSED",
    "S1_POLICY_CONTENT_BOUND_REUSED",
    "AGENTS_SILENT_TRUNCATION",
    "S1_POLICY_DIGEST_SEMANTICS_REUSED",
    "AGENTS_POLICY_CANDIDATE_REUSED",
    "AGENTS_POLICY_CANDIDATE_SCHEMA_CHANGED",
    "POLICY_ID_DETERMINISTIC",
    "PROVENANCE_REF_IS_LOGICAL",
    "PROVENANCE_REF_IS_AUTHORITY",
    "CANDIDATE_PROJECT_ID_FROM_TRUSTED_BINDING",
    "CALLER_PROJECT_ID_SELF_AUTHORIZES",
    "PHYSICAL_DIRECTORY_TO_LOGICAL_SCOPE_DETERMINISTIC",
    "FILESYSTEM_ENUMERATION_ORDER_IS_AUTHORITY",
    "PHYSICAL_DISCOVERY_IS_POLICY_AUTHORITY",
    "S1_APPLICABILITY_REUSED",
    "S1_CONFLICT_FAIL_CLOSED_RETAINED",
    "DEFAULT_AGENTS_POLICY_FABRICATED",
]
