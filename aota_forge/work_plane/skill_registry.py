"""Deterministic static Skill registry (S3 M1-W2).

Static declarative index — no filesystem discovery, no plugin marketplace,
no persistent database, no hydration, no lexical search.

Invariants
----------
* STATIC_REGISTRY_V0=yes
* STATIC_DECLARATIVE_INDEX=yes
* RUNTIME_FILESYSTEM_DISCOVERY=no
* REGISTRY_BOUNDED=yes
* REGISTRY_DETERMINISTIC=yes
* REGISTRY_FAIL_CLOSED=yes
* REGISTRY_NAMESPACE_SCOPED=yes
* SKILL_IS_AUTHORITY=no
* NAMESPACE_GRANTS_AUTHORITY=no
* CONTENT_REF_IS_AUTHORITY=no (if present, opaque bounded, not dereferenced)
* Registry key is (namespace.value, skill_id, version)
* Exact version lookup only — no latest/SemVer
* Input order does not affect canonical projection
* Duplicate composite key fails closed including differing digest/provenance
* Cross-namespace isolation — same identity in different namespaces allowed
* No authority/tool/filesystem/hydration side effects
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Any

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill import SkillIdentity

# ---------------------------------------------------------------------------
# Invariant markers (descriptive, not authority)
# ---------------------------------------------------------------------------

STATIC_REGISTRY_V0: bool = True
STATIC_DECLARATIVE_INDEX: bool = True
RUNTIME_FILESYSTEM_DISCOVERY: bool = False

REGISTRY_BOUNDED: bool = True
REGISTRY_DETERMINISTIC: bool = True
REGISTRY_FAIL_CLOSED: bool = True
REGISTRY_NAMESPACE_SCOPED: bool = True

SKILL_IS_AUTHORITY: bool = False
SKILL_GRANTS_TOOL_AUTHORITY: bool = False
SKILL_GRANTS_FILESYSTEM_AUTHORITY: bool = False
SKILL_GRANTS_EXECUTION_AUTHORITY: bool = False
NAMESPACE_GRANTS_AUTHORITY: bool = False

# Content ref markers (opaque, not authority, not auto-opened)
CONTENT_REF_IS_SKILL_IDENTITY: bool = False
CONTENT_REF_IS_AUTHORITY: bool = False
CONTENT_REF_AUTO_OPENED: bool = False
CONTENT_REF_AUTO_HYDRATED: bool = False

# Registry key field inventory
REGISTRY_KEY_FIELDS: tuple[str, ...] = ("namespace", "skill_id", "version")

EXACT_VERSION_LOOKUP: bool = True
LATEST_VERSION_LOOKUP: bool = False
SEMVER_PRECEDENCE: bool = False

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

# Conservative implementation-local limit; not a Plan constant.
# Must be finite, fail-closed, no silent truncation.
MAX_REGISTRY_ENTRIES: int = 64

# Optional opaque content reference bound.
MAX_CONTENT_REF_LENGTH: int = 512

# ---------------------------------------------------------------------------
# Validation helpers — bounded, explicit, fail-closed
# ---------------------------------------------------------------------------

def _validate_namespace(value: object) -> AgentWorkRole:
    """Validate namespace as AgentWorkRole member, fail-closed.

    Accepts AgentWorkRole members or canonical string values.
    Rejects unknown role, foreign Enum, legacy aliases, None,
    non-string/non-AgentWorkRole, with TypeError/ValueError.
    No fallback/default.
    """
    # Use parse_agent_work_role which already fails closed for
    # foreign Enum, unknown string, non-string etc.
    # It raises TypeError for foreign Enum/non-string, ValueError for unknown.
    return parse_agent_work_role(value)


def _validate_content_ref(value: object | None) -> str | None:
    """Validate optional opaque content reference.

    Bounded opaque string, deterministic, no authority, not
    dereferenced. Reject absolute paths and parent traversal
    markers to avoid filesystem authority escalation.
    """
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"content_ref must be a string or None, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("content_ref when provided must be non-empty")
    if len(stripped) > MAX_CONTENT_REF_LENGTH:
        raise ValueError(f"content_ref length ({len(stripped)}) exceeds maximum {MAX_CONTENT_REF_LENGTH}")
    # Opaque but bounded — prevent arbitrary filesystem authority
    if stripped.startswith("/"):
        raise ValueError(f"content_ref must not be absolute path: {value!r}")
    if ".." in stripped.split("/"):
        raise ValueError(f"content_ref must not contain '..': {value!r}")
    # Must not have leading/trailing whitespace (canonical)
    if value != value.strip():
        raise ValueError("content_ref must not have leading/trailing whitespace")
    return stripped


def _validate_lookup_field(value: object, label: str, max_len: int = 512) -> str:
    """Validate lookup field (skill_id/version) for exact lookup.

    These reuse SkillIdentity bounds conceptually but are checked
    here to fail-closed on non-string/invalid shapes without
    creating a SkillIdentity.
    """
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{label} must be non-empty")
    if not value.strip():
        raise ValueError(f"{label} must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError(f"{label} must not have leading/trailing whitespace")
    if len(value) > max_len:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_len}")
    return value


# ---------------------------------------------------------------------------
# Registry entry — minimal immutable (namespace + SkillIdentity)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillRegistryEntry:
    """Minimal immutable registry entry associating namespace + SkillIdentity.

    Fields:
        namespace — AgentWorkRole member (canonical namespace)
        identity  — SkillIdentity value object (skill_id, version, digest, provenance)
        content_ref — optional opaque bounded resource reference (not authority,
                      not auto-opened, not dereferenced)

    Invariants:
        * namespace is AgentWorkRole member (via .value canonical)
        * identity is SkillIdentity (immutable, 4-field)
        * composite key is (namespace.value, skill_id, version)
        * No authority granted
        * Immutable value object
    """

    namespace: AgentWorkRole
    identity: SkillIdentity
    content_ref: str | None = None

    def __post_init__(self) -> None:
        # Validate namespace — must be AgentWorkRole, fail-closed
        ns = _validate_namespace(self.namespace)
        object.__setattr__(self, "namespace", ns)

        # Validate identity — must be SkillIdentity instance
        if not isinstance(self.identity, SkillIdentity):
            raise TypeError(f"identity must be SkillIdentity, got {type(self.identity).__name__}")

        # Validate optional content_ref — opaque bounded
        cr = _validate_content_ref(self.content_ref)
        object.__setattr__(self, "content_ref", cr)

    @property
    def namespace_value(self) -> str:
        """Canonical string namespace value."""
        return self.namespace.value

    @property
    def skill_id(self) -> str:
        return self.identity.skill_id

    @property
    def version(self) -> str:
        return self.identity.version

    @property
    def composite_key(self) -> tuple[str, str, str]:
        """Semantic registry key: (namespace.value, skill_id, version)."""
        return (self.namespace.value, self.identity.skill_id, self.identity.version)

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical dict projection."""
        d: dict[str, Any] = {
            "namespace": self.namespace.value,
            "identity": self.identity.canonical_dict(),
        }
        if self.content_ref is not None:
            d["content_ref"] = self.content_ref
        return d

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()


# ---------------------------------------------------------------------------
# Static registry — deterministic bounded declarative index
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StaticSkillRegistry:
    """Immutable deterministic static Skill registry.

    Constructed from an explicit bounded collection of entries.
    No filesystem discovery, no plugin scanning, no database.

    Ordering is canonical (namespace.value, skill_id, version)
    regardless of input order. Duplicate composite keys fail closed.
    """

    # Canonical sorted entries — immutable tuple
    _entries: tuple[SkillRegistryEntry, ...]
    # Internal index — composite key -> entry
    _index: Mapping[tuple[str, str, str], SkillRegistryEntry]

    def __init__(self, entries: Iterable[SkillRegistryEntry]) -> None:
        # Validate explicit iterable
        if entries is None:
            raise TypeError("entries must be iterable, got None")
        # Materialize to list for validation
        try:
            entry_list = list(entries)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TypeError(f"entries must be iterable, got {type(entries).__name__}") from exc

        # Bound check — fail-closed, no silent truncation
        if len(entry_list) > MAX_REGISTRY_ENTRIES:
            raise ValueError(
                f"registry size ({len(entry_list)}) exceeds maximum {MAX_REGISTRY_ENTRIES}"
            )

        # Validate each entry type
        for idx, e in enumerate(entry_list):
            if not isinstance(e, SkillRegistryEntry):
                raise TypeError(f"entries[{idx}] must be SkillRegistryEntry, got {type(e).__name__}")

        # Deterministic canonical ordering: (namespace.value, skill_id, version)
        # Additional tie-breaker: digest/provenance/content_ref for full determinism
        # but duplicate composite keys already rejected, so extra tie not needed
        # for uniqueness. We add deterministic tie for stability.
        def _sort_key(ent: SkillRegistryEntry) -> tuple[str, str, str, str, str, str]:
            return (
                ent.namespace.value,
                ent.identity.skill_id,
                ent.identity.version,
                ent.identity.digest,
                ent.identity.provenance,
                ent.content_ref or "",
            )

        sorted_entries = tuple(sorted(entry_list, key=_sort_key))

        # Duplicate detection — composite key unique within registry
        index: dict[tuple[str, str, str], SkillRegistryEntry] = {}
        for ent in sorted_entries:
            key = ent.composite_key
            if key in index:
                raise ValueError(f"duplicate composite key rejected: {key!r}")
            index[key] = ent

        # Store immutable structures — externally immutable
        object.__setattr__(self, "_entries", sorted_entries)
        # Store as frozen dict-like mapping (use dict but treat as immutable via frozen dataclass)
        # We keep a plain dict internally but expose only read access
        object.__setattr__(self, "_index", dict(index))

    # -----------------------------------------------------------------------
    # Accessors — deterministic, bounded, fail-closed, no authority
    # -----------------------------------------------------------------------

    @property
    def entries(self) -> tuple[SkillRegistryEntry, ...]:
        """Canonical ordered entries (immutable tuple)."""
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._entries)

    def __contains__(self, entry: object) -> bool:
        if isinstance(entry, SkillRegistryEntry):
            key = entry.composite_key
            found = self._index.get(key)
            return found == entry
        return False

    def get(
        self,
        namespace: object,
        skill_id: object,
        version: object,
    ) -> SkillRegistryEntry | None:
        """Exact lookup by (namespace, skill_id, version).

        Validates namespace fail-closed.
        Returns entry if present, else None (explicit absence).
        Does NOT substitute another version, does NOT return latest.
        """
        ns = _validate_namespace(namespace)
        sid = _validate_lookup_field(skill_id, "skill_id", max_len=128)
        ver = _validate_lookup_field(version, "version", max_len=64)
        key = (ns.value, sid, ver)
        return self._index.get(key)

    def entries_for_namespace(
        self, namespace: object
    ) -> tuple[SkillRegistryEntry, ...]:
        """Bounded deterministic enumeration for namespace.

        Validates namespace, returns bounded deterministic results,
        returns no Skill content (only entries). Not lexical search.
        """
        ns = _validate_namespace(namespace)
        # Filter canonical entries already sorted; result remains deterministic
        result = tuple(e for e in self._entries if e.namespace == ns)
        return result

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.canonical_dict() for e in self._entries],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()


# ---------------------------------------------------------------------------
# Convenience builder — explicit bounded declarative construction
# ---------------------------------------------------------------------------

def build_static_skill_registry(
    entries: Iterable[SkillRegistryEntry],
) -> StaticSkillRegistry:
    """Build deterministic static registry from explicit entries.

    No filesystem discovery. Input order does not affect result.
    Bounded, fail-closed.
    """
    return StaticSkillRegistry(entries)


__all__ = [
    "SkillRegistryEntry",
    "StaticSkillRegistry",
    "build_static_skill_registry",
    "MAX_REGISTRY_ENTRIES",
    "MAX_CONTENT_REF_LENGTH",
    "REGISTRY_KEY_FIELDS",
    "STATIC_REGISTRY_V0",
    "STATIC_DECLARATIVE_INDEX",
    "RUNTIME_FILESYSTEM_DISCOVERY",
    "REGISTRY_BOUNDED",
    "REGISTRY_DETERMINISTIC",
    "REGISTRY_FAIL_CLOSED",
    "REGISTRY_NAMESPACE_SCOPED",
    "SKILL_IS_AUTHORITY",
    "NAMESPACE_GRANTS_AUTHORITY",
    "CONTENT_REF_IS_AUTHORITY",
    "CONTENT_REF_IS_SKILL_IDENTITY",
    "CONTENT_REF_AUTO_OPENED",
    "CONTENT_REF_AUTO_HYDRATED",
    "EXACT_VERSION_LOOKUP",
    "LATEST_VERSION_LOOKUP",
    "SEMVER_PRECEDENCE",
]
