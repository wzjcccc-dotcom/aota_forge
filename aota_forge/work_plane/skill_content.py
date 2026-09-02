"""Bounded Skill read/open with digest verification (S3 M2-W1).

Implements read/open semantics after an authorized content-source boundary.

Conceptual flow::

    exact registry lookup
          ↓
    opaque content_ref
          ↓
    caller-supplied authorized content reader
          ↓
    bounded Skill content
          ↓
    SHA-256 digest verification
          ↓
    verified opened Skill

Invariants
----------
* SKILL_READ_OPEN_PRESENT=yes
* DIGEST_VERIFICATION_REQUIRED=yes
* CONTENT_BOUNDED=yes
* AUTHORIZED_READER_BOUNDARY_PRESENT=yes
* CONTENT_REF_IS_AUTHORITY=no
* CONTENT_REF_AUTO_DEREFERENCE=no
* SKILL_IS_AUTHORITY=no
* OPENED_SKILL_IS_AUTHORITY=no
* CONTENT_REF_IS_AUTHORITY=no
* No filesystem authority — no Path.open, open(content_ref), os.stat,
  Path.resolve, traversal, repository discovery, network fetch, URI fetch.
* Bounded invocation — shape/size/digest verification only.
* Reader receives only opaque content_ref already stored in registry entry.
* W1 does not reinterpret content_ref.
* Digest algorithm SHA-256 64 lowercase hex (reuse compute_skill_digest).
* Oversized content fails closed, no silent truncation.
* No lexical/vector search, no selection, no bootstrap integration.

Ownership
---------
* S2 owns physical project/worktree sandbox and resource authority.
* S3 W1 owns read/open semantics after authorized content-source boundary.
* This module does NOT create sandbox and does NOT import S2 resolver candidates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping, Any

from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.skill import SkillIdentity, compute_skill_digest
from aota_forge.work_plane.skill_registry import StaticSkillRegistry, SkillRegistryEntry

# ---------------------------------------------------------------------------
# Invariant markers (descriptive, not authority)
# ---------------------------------------------------------------------------

SKILL_READ_OPEN_PRESENT: bool = True
DIGEST_VERIFICATION_REQUIRED: bool = True
CONTENT_BOUNDED: bool = True
AUTHORIZED_READER_BOUNDARY_PRESENT: bool = True

# Re-affirm M1 invariants in this module scope
SKILL_IS_AUTHORITY: bool = False
SKILL_GRANTS_TOOL_AUTHORITY: bool = False
SKILL_GRANTS_FILESYSTEM_AUTHORITY: bool = False
SKILL_GRANTS_EXECUTION_AUTHORITY: bool = False
CONTENT_REF_IS_AUTHORITY: bool = False
CONTENT_REF_AUTO_DEREFERENCE: bool = False
CONTENT_REF_AUTO_OPENED: bool = False

# W1 bounded content markers
SKILL_CONTENT_BOUNDED: bool = True
OVERSIZED_CONTENT_FAIL_CLOSED: bool = True
SILENT_TRUNCATION: bool = False
DIGEST_MISMATCH_RETURNS_CONTENT: bool = False
OPENED_SKILL_IS_AUTHORITY: bool = False

# No extra search/resolution/bootstrap markers
LEXICAL_SEARCH_IMPLEMENTED: bool = False
SKILL_RESOLUTION_IMPLEMENTED: bool = False
BOOTSTRAP_INTEGRATION_IMPLEMENTED: bool = False
S3_W1_CREATES_SANDBOX: bool = False
S2_UNACCEPTED_CANDIDATE_DEPENDENCY: bool = False

# Alias required by acceptance boundary
SKILL_CONTENT_BOUNDED_MARKER: bool = True

# ---------------------------------------------------------------------------
# Content bound — implementation-local finite bound, not Plan constant
# ---------------------------------------------------------------------------

# Prefer checking UTF-8 encoded byte size where practical.
# Implementation-local: 64 KiB. Bounded, finite, fail-closed, no truncation.
MAX_SKILL_CONTENT_BYTES: int = 64 * 1024
MAX_SKILL_CONTENT_CHARS: int = MAX_SKILL_CONTENT_BYTES  # conservative

# For test introspection — keep byte bound authoritative
SKILL_CONTENT_MAX_BYTES: int = MAX_SKILL_CONTENT_BYTES

# ---------------------------------------------------------------------------
# Exceptions — small deterministic local exceptions, no new ontology
# ---------------------------------------------------------------------------

class SkillReadError(RuntimeError):
    """Local fail-closed Skill read/open error."""


class SkillNotFoundError(SkillReadError):
    """Registry entry not found for exact lookup."""


class SkillContentRefError(SkillReadError):
    """Missing or invalid content_ref."""


class SkillContentBoundError(SkillReadError):
    """Content exceeds bounded limit."""


class SkillDigestMismatchError(SkillReadError):
    """Digest verification failed."""


class SkillContentShapeError(SkillReadError):
    """Reader returned invalid non-text shape."""


# ---------------------------------------------------------------------------
# OpenedSkill — verified immutable projection (not authority)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenedSkill:
    """Verified opened Skill projection — not authority.

    Fields:
        namespace — canonical AgentWorkRole value (or AgentWorkRole member via .namespace)
        identity  — SkillIdentity (immutable 4-field)
        content   — verified bounded text content (UTF-8 for digest)

    Invariants:
        * OPENED_SKILL_IS_AUTHORITY=no
        * No Tool/filesystem/execution permission fields
        * No bootstrap mode or selection precedence
        * Immutable value object
    """

    namespace: AgentWorkRole
    identity: SkillIdentity
    content: str

    def __post_init__(self) -> None:
        # Validate namespace is AgentWorkRole member
        if not isinstance(self.namespace, AgentWorkRole):
            raise TypeError(f"namespace must be AgentWorkRole, got {type(self.namespace).__name__}")
        if not isinstance(self.identity, SkillIdentity):
            raise TypeError(f"identity must be SkillIdentity, got {type(self.identity).__name__}")
        if not isinstance(self.content, str) or type(self.content) is not str:
            raise TypeError(f"content must be str, got {type(self.content).__name__}")
        # Content already bounded and digest-verified by open path — re-check bounds
        data_len = len(self.content.encode("utf-8"))
        if data_len > MAX_SKILL_CONTENT_BYTES:
            raise ValueError(f"content byte size {data_len} exceeds bound {MAX_SKILL_CONTENT_BYTES}")
        # Re-verify digest to keep projection internally consistent
        computed = compute_skill_digest(self.content)
        if computed != self.identity.digest:
            raise ValueError(f"OpenedSkill digest mismatch: computed {computed!r} != identity {self.identity.digest!r}")

    @property
    def namespace_value(self) -> str:
        return self.namespace.value

    @property
    def skill_id(self) -> str:
        return self.identity.skill_id

    @property
    def version(self) -> str:
        return self.identity.version

    @property
    def digest(self) -> str:
        return self.identity.digest


# ---------------------------------------------------------------------------
# Core read/open implementation
# ---------------------------------------------------------------------------

def _validate_reader(reader: object) -> Callable[[str], str]:
    if reader is None:
        raise TypeError("read_authorized_content must be callable, got None")
    if not callable(reader):
        raise TypeError(f"read_authorized_content must be callable, got {type(reader).__name__}")
    return reader  # type: ignore[return-value]


def open_skill(
    registry: StaticSkillRegistry,
    namespace: object,
    skill_id: object,
    version: object,
    read_authorized_content: Callable[[str], str],
) -> OpenedSkill:
    """Open and verify Skill content via exact registry lookup + authorized reader.

    Args:
        registry: StaticSkillRegistry (exact lookup, no latest/fallback)
        namespace: AgentWorkRole value or member
        skill_id: explicit skill_id
        version: explicit version (no default)
        read_authorized_content: caller-supplied authorized content reader.
            Receives only the opaque content_ref stored in registry entry.
            Must return text (str). W1 never interprets content_ref as path/URI.

    Returns:
        Verified OpenedSkill projection (immutable, not authority).

    Fail-closed on:
        * unknown namespace
        * missing registry entry
        * version mismatch via exact lookup (no fallback)
        * missing content_ref
        * reader failure
        * reader returns invalid/non-text shape
        * oversized content
        * digest mismatch

    Security:
        * W1 never performs Path.open, open(content_ref), os.stat,
          Path.resolve, traversal, repository/URI/network fetch.
        * W1 does not reinterpret content_ref — passes opaque ref to reader.
    """
    # Validate reader boundary present
    reader = _validate_reader(read_authorized_content)

    # Validate registry type — keep minimal but fail-closed
    if not isinstance(registry, StaticSkillRegistry):
        raise TypeError(f"registry must be StaticSkillRegistry, got {type(registry).__name__}")

    # Exact registry lookup — no latest, no fallback, no cross-namespace
    # Registry's get() already validates namespace fail-closed
    entry: SkillRegistryEntry | None
    try:
        entry = registry.get(namespace, skill_id, version)
    except (ValueError, TypeError):
        # Propagate namespace/field validation fail-closed
        raise

    if entry is None:
        raise SkillNotFoundError(
            f"Skill not found for exact lookup: namespace={namespace!r} skill_id={skill_id!r} version={version!r}"
        )

    # Missing content_ref fails closed
    if entry.content_ref is None:
        raise SkillContentRefError(
            f"missing content_ref for {entry.composite_key!r}"
        )

    content_ref: str = entry.content_ref

    # Invoke authorized reader — boundary owned by caller / future S2 resolver
    try:
        content = reader(content_ref)  # type: ignore[call-arg]
    except SkillReadError:
        # Already fail-closed local error — propagate
        raise
    except Exception as exc:
        # Map reader failure deterministically to read/open failure
        raise SkillReadError(f"authorized reader failure for {content_ref!r}: {exc}") from exc

    # Shape validation — must be str (text oriented, UTF-8 for digest)
    if not isinstance(content, str) or type(content) is not str:
        raise SkillContentShapeError(
            f"reader returned invalid non-text shape: {type(content).__name__} for {content_ref!r}"
        )

    # Content bound — check UTF-8 byte size, fail closed, no truncation
    byte_size = len(content.encode("utf-8"))
    if byte_size > MAX_SKILL_CONTENT_BYTES:
        raise SkillContentBoundError(
            f"Skill content byte size {byte_size} exceeds bound {MAX_SKILL_CONTENT_BYTES}"
        )

    # Digest verification — exact content semantics established by M1
    # Do NOT normalize newlines/strip/rewrite before verification
    computed_digest = compute_skill_digest(content)
    if computed_digest != entry.identity.digest:
        raise SkillDigestMismatchError(
            f"digest mismatch for {entry.composite_key!r}: computed {computed_digest!r} != expected {entry.identity.digest!r}"
        )

    # Verified projection — small immutable, not authority
    # Normalize namespace to AgentWorkRole member for projection
    # entry.namespace already validated as AgentWorkRole
    return OpenedSkill(
        namespace=entry.namespace,
        identity=entry.identity,
        content=content,
    )


# Convenience alias — same semantics
read_skill = open_skill

# Also provide explicit name for bounded read variant
def read_authorized_skill_content(
    registry: StaticSkillRegistry,
    namespace: object,
    skill_id: object,
    version: object,
    read_authorized_content: Callable[[str], str],
) -> OpenedSkill:
    return open_skill(registry, namespace, skill_id, version, read_authorized_content)


__all__ = [
    "OpenedSkill",
    "open_skill",
    "read_skill",
    "read_authorized_skill_content",
    "MAX_SKILL_CONTENT_BYTES",
    "MAX_SKILL_CONTENT_CHARS",
    "SKILL_CONTENT_MAX_BYTES",
    "SkillReadError",
    "SkillNotFoundError",
    "SkillContentRefError",
    "SkillContentBoundError",
    "SkillDigestMismatchError",
    "SkillContentShapeError",
    "SKILL_READ_OPEN_PRESENT",
    "DIGEST_VERIFICATION_REQUIRED",
    "CONTENT_BOUNDED",
    "AUTHORIZED_READER_BOUNDARY_PRESENT",
    "SKILL_IS_AUTHORITY",
    "CONTENT_REF_IS_AUTHORITY",
    "CONTENT_REF_AUTO_DEREFERENCE",
    "CONTENT_REF_AUTO_OPENED",
    "SKILL_CONTENT_BOUNDED",
    "OVERSIZED_CONTENT_FAIL_CLOSED",
    "SILENT_TRUNCATION",
    "DIGEST_MISMATCH_RETURNS_CONTENT",
    "OPENED_SKILL_IS_AUTHORITY",
    "LEXICAL_SEARCH_IMPLEMENTED",
    "SKILL_RESOLUTION_IMPLEMENTED",
    "BOOTSTRAP_INTEGRATION_IMPLEMENTED",
    "S3_W1_CREATES_SANDBOX",
    "S2_UNACCEPTED_CANDIDATE_DEPENDENCY",
]
