"""Minimal Skill identity contract (S3 M1-W1).

Freeze exactly four descriptive/integrity fields:

    skill_id, version, digest, provenance

Invariants
----------
* SKILL_IS_AUTHORITY=no
* SKILL_GRANTS_TOOL_AUTHORITY=no
* SKILL_GRANTS_FILESYSTEM_AUTHORITY=no
* SKILL_GRANTS_EXECUTION_AUTHORITY=no
* PROVENANCE_IS_AUTHORITY=no
* DIGEST_IS_AUTHORITY=no
* Identity key is deterministic (skill_id, version)
* digest is SHA-256 64 lowercase hex
* version is explicit opaque bounded string (no SemVer requirement)
* All fields bounded, explicit, non-empty, deterministic
* Immutable value object, no registry/marketplace/database
* No namespace/work_role/title/description/tool fields

Reuse
-----
* Reuses canonical_json helper for deterministic serialization
* Does NOT modify SemanticReference, TaskHandoff, BootstrapBundle
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json

# ---------------------------------------------------------------------------
# Bounds and patterns
# ---------------------------------------------------------------------------

MAX_SKILL_ID_LENGTH: int = 128
MAX_VERSION_LENGTH: int = 64
MAX_PROVENANCE_LENGTH: int = 128

# SHA-256 64 lowercase hex characters
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Allowed fields for serialization (exactly 4)
_SKILL_IDENTITY_FIELDS: frozenset[str] = frozenset({
    "skill_id",
    "version",
    "digest",
    "provenance",
})

# Forbidden extensions — must never appear as required identity fields
_FORBIDDEN_IDENTITY_FIELDS: frozenset[str] = frozenset({
    "work_role",
    "namespace",
    "title",
    "description",
    "tools",
    "tool_requirements",
    "permissions",
    "filesystem_path",
    "path",
    "runtime_profile",
    "execution_role",
    "authority",
    "bootstrap_mode",
    "recommended",
    "required",
    "status",
})


# ---------------------------------------------------------------------------
# Validation helpers — bounded, explicit, fail-closed
# ---------------------------------------------------------------------------

def _validate_skill_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"skill_id must be a string, got {type(value).__name__}")
    # Reject empty/whitespace-only; avoid aggressive normalization that aliases
    # distinct inputs. Require caller to provide canonical form without
    # surrounding whitespace; we enforce deterministically by requiring stripped
    # equality or by storing stripped value deterministically.
    # To avoid silent aliasing we reject values with leading/trailing whitespace
    # rather than silently stripping.
    if not value:
        raise ValueError("skill_id must be non-empty")
    if not value.strip():
        raise ValueError("skill_id must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("skill_id must not have leading/trailing whitespace")
    if len(value) > MAX_SKILL_ID_LENGTH:
        raise ValueError(f"skill_id length ({len(value)}) exceeds maximum {MAX_SKILL_ID_LENGTH}")
    return value


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"version must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("version must be non-empty")
    if not value.strip():
        raise ValueError("version must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("version must not have leading/trailing whitespace")
    if len(value) > MAX_VERSION_LENGTH:
        raise ValueError(f"version length ({len(value)}) exceeds maximum {MAX_VERSION_LENGTH}")
    return value


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"digest must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("digest must be non-empty")
    stripped = value.strip()
    if not stripped:
        raise ValueError("digest must be non-empty (whitespace-only rejected)")
    # Canonical form is lowercase hex; require exact 64 lower hex chars.
    # Reject non-canonical forms (upper, wrong length, non-hex).
    canonical = stripped.lower()
    if canonical != stripped:
        raise ValueError(f"digest must be 64 lowercase hex chars (non-canonical uppercase): {value!r}")
    if len(canonical) != 64:
        raise ValueError(f"digest must be 64 hex chars, got length {len(canonical)}: {value!r}")
    if not _DIGEST_RE.fullmatch(canonical):
        raise ValueError(f"digest must be 64 lowercase hex chars: {value!r}")
    return canonical


def _validate_provenance(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"provenance must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("provenance must be non-empty")
    if not value.strip():
        raise ValueError("provenance must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("provenance must not have leading/trailing whitespace")
    if len(value) > MAX_PROVENANCE_LENGTH:
        raise ValueError(f"provenance length ({len(value)}) exceeds maximum {MAX_PROVENANCE_LENGTH}")
    return value


# ---------------------------------------------------------------------------
# Digest helper — deterministic SHA-256, UTF-8 for str, raw bytes as-is
# ---------------------------------------------------------------------------

def compute_skill_digest(content: str | bytes) -> str:
    """Compute deterministic SHA-256 digest for Skill content.

    * If *content* is str, encode as UTF-8 deterministically without hidden
      newline/content rewriting.
    * If *content* is bytes, hash bytes as-is.
    * Returns 64 lowercase hex characters.
    * Same exact content -> same digest; different content -> different digest.
    """
    if isinstance(content, str):
        if type(content) is not str:
            raise TypeError(f"content must be str or bytes, got {type(content).__name__}")
        data = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        # Accept exact bytes without copying semantics altering
        if isinstance(content, bytearray):
            data = bytes(content)
        else:
            # Ensure exact type is bytes (subclass str not possible here)
            if type(content) is not bytes:
                # Rejected subclass to keep determinism
                raise TypeError(f"content must be str or bytes, got {type(content).__name__}")
            data = content
    else:
        raise TypeError(f"content must be str or bytes, got {type(content).__name__}")
    return hashlib.sha256(data).hexdigest()


def compute_skill_digest_for_text(text: str) -> str:
    """Convenience helper for text content (UTF-8)."""
    if not isinstance(text, str) or type(text) is not str:
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SkillIdentity — immutable 4-field value contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillIdentity:
    """Minimal immutable Skill identity value contract.

    Fields (exactly 4, all required):
        skill_id   — explicit bounded non-empty identifier
        version    — explicit opaque bounded version identifier
        digest     — SHA-256 64 lowercase hex content integrity
        provenance — opaque bounded provenance metadata

    Invariants:
        * No authority granted (tool/execution/filesystem/AGENTS/TaskHandoff)
        * No work_role/namespace/title/description etc.
        * Identity key is (skill_id, version)
        * Digest and provenance are descriptive only
    """

    skill_id: str
    version: str
    digest: str
    provenance: str

    def __post_init__(self) -> None:
        sid = _validate_skill_id(self.skill_id)
        object.__setattr__(self, "skill_id", sid)

        ver = _validate_version(self.version)
        object.__setattr__(self, "version", ver)

        dg = _validate_digest(self.digest)
        object.__setattr__(self, "digest", dg)

        prov = _validate_provenance(self.provenance)
        object.__setattr__(self, "provenance", prov)

    # Identity key — deterministic semantic key (skill_id, version)
    @property
    def identity_key(self) -> tuple[str, str]:
        """Deterministic semantic key: (skill_id, version)."""
        return (self.skill_id, self.version)

    @property
    def key(self) -> tuple[str, str]:
        """Alias for identity_key."""
        return self.identity_key

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical dict with exactly four fields."""
        return {
            "digest": self.digest,
            "provenance": self.provenance,
            "skill_id": self.skill_id,
            "version": self.version,
        }

    def canonical_json(self) -> str:
        """Deterministic canonical JSON serialization."""
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict with exactly four fields (deterministic)."""
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillIdentity":
        """Construct SkillIdentity fail-closed from mapping.

        * Missing fields fail closed
        * Unknown fields fail closed
        * Non-mapping fails closed
        """
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        # Fail closed on unknown fields
        extra = set(data.keys()) - _SKILL_IDENTITY_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in SkillIdentity: {sorted(extra)}")
        # Fail closed on forbidden fifth fields explicitly
        forbidden_present = set(data.keys()) & _FORBIDDEN_IDENTITY_FIELDS
        if forbidden_present:
            raise ValueError(f"Forbidden field(s) in SkillIdentity: {sorted(forbidden_present)}")
        # Fail closed on missing required fields
        for req in ("skill_id", "version", "digest", "provenance"):
            if req not in data:
                raise ValueError(f"Missing required field in SkillIdentity: {req!r}")
        return cls(
            skill_id=data["skill_id"],
            version=data["version"],
            digest=data["digest"],
            provenance=data["provenance"],
        )

    # Explicit immutability proof helper
    def __repr__(self) -> str:
        return (
            f"SkillIdentity(skill_id={self.skill_id!r}, version={self.version!r}, "
            f"digest={self.digest!r}, provenance={self.provenance!r})"
        )


# ---------------------------------------------------------------------------
# Authority separation markers (descriptive, not authority)
# ---------------------------------------------------------------------------

SKILL_IS_AUTHORITY: bool = False
SKILL_GRANTS_TOOL_AUTHORITY: bool = False
SKILL_GRANTS_FILESYSTEM_AUTHORITY: bool = False
SKILL_GRANTS_EXECUTION_AUTHORITY: bool = False
PROVENANCE_IS_AUTHORITY: bool = False
DIGEST_IS_AUTHORITY: bool = False

# Field inventory for verification
SKILL_IDENTITY_FIELDS: tuple[str, ...] = ("skill_id", "version", "digest", "provenance")
REQUIRED_SKILL_IDENTITY_FIELD_COUNT: int = 4
ADDITIONAL_REQUIRED_IDENTITY_FIELD_COUNT: int = 0

__all__ = [
    "SkillIdentity",
    "compute_skill_digest",
    "compute_skill_digest_for_text",
    "MAX_SKILL_ID_LENGTH",
    "MAX_VERSION_LENGTH",
    "MAX_PROVENANCE_LENGTH",
    "SKILL_IDENTITY_FIELDS",
    "REQUIRED_SKILL_IDENTITY_FIELD_COUNT",
    "ADDITIONAL_REQUIRED_IDENTITY_FIELD_COUNT",
    "SKILL_IS_AUTHORITY",
    "SKILL_GRANTS_TOOL_AUTHORITY",
    "SKILL_GRANTS_FILESYSTEM_AUTHORITY",
    "SKILL_GRANTS_EXECUTION_AUTHORITY",
    "PROVENANCE_IS_AUTHORITY",
    "DIGEST_IS_AUTHORITY",
]
