"""SOUL bounded behavioral bootstrap component (S1 M2-W1).

Minimal bounded SOUL semantic contract used during bootstrap.

Invariants
----------
* SOUL_IS_BOOTSTRAP_COMPONENT=yes
* SOUL_IS_AUTHORITY=no
* SOUL_IS_AGENT_PROFILE_ONTOLOGY=no
* SOUL_IS_PERSISTENT_STORE=no
* non-empty content
* bounded content size
* deterministic canonical representation
* deterministic digest (SHA-256 of canonical JSON)
* unknown fields fail closed
* exact byte/token budget not frozen in W1 (M2-W3 owns budget)
* no registry, database, marketplace, policy engine, or persistent profile

SOUL is concise stable behavioral principles, not an authority store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json

# Bounded capacity — minimal evidence-supported bound
MAX_SOUL_CONTENT_LENGTH: int = 8192
MAX_SOUL_VERSION_LENGTH: int = 128

# SOUL identity
SOUL_IS_BOOTSTRAP_COMPONENT: bool = True
SOUL_IS_AUTHORITY: bool = False
SOUL_IS_AGENT_PROFILE_ONTOLOGY: bool = False
SOUL_IS_PERSISTENT_STORE: bool = False

# Fail-closed allowed fields
_ALLOWED_SOUL_FIELDS: frozenset[str] = frozenset({"content", "version"})


@dataclass(frozen=True)
class Soul:
    """Bounded behavioral bootstrap component.

    Carries concise stable behavioral principles for bootstrap.
    Not an authority, not a persistent profile, not a store.
    """

    content: str
    version: str | None = None

    def __post_init__(self) -> None:
        # Validate content — non-empty, bounded
        if not isinstance(self.content, str) or type(self.content) is not str:
            raise TypeError(f"content must be a string, got {type(self.content).__name__}")
        stripped = self.content.strip()
        if not stripped:
            raise ValueError("content must be a non-empty string")
        if len(stripped) > MAX_SOUL_CONTENT_LENGTH:
            raise ValueError(
                f"content length ({len(stripped)}) exceeds maximum {MAX_SOUL_CONTENT_LENGTH} chars"
            )
        if stripped != self.content:
            object.__setattr__(self, "content", stripped)

        # Validate version — optional bounded non-empty
        if self.version is not None:
            if not isinstance(self.version, str) or type(self.version) is not str:
                raise TypeError(f"version must be a string or None, got {type(self.version).__name__}")
            v_stripped = self.version.strip()
            if not v_stripped:
                raise ValueError("version when provided must be a non-empty string")
            if len(v_stripped) > MAX_SOUL_VERSION_LENGTH:
                raise ValueError(
                    f"version length ({len(v_stripped)}) exceeds maximum {MAX_SOUL_VERSION_LENGTH} chars"
                )
            if v_stripped != self.version:
                object.__setattr__(self, "version", v_stripped)

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical dict."""
        d: dict[str, Any] = {"content": self.content}
        if self.version is not None:
            d["version"] = self.version
        return d

    def canonical_json(self) -> str:
        """Deterministic canonical JSON (sorted keys, no incidental ordering)."""
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        """Deterministic SHA-256 digest of canonical representation."""
        encoded = self.canonical_json().encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def digest(self) -> str:
        """Deterministic digest covering canonical content."""
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        d: dict[str, Any] = {"content": self.content}
        if self.version is not None:
            d["version"] = self.version
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Soul:
        """Construct from mapping, fail-closed on unknown fields."""
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_SOUL_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in Soul payload: {sorted(extra)}")
        if "content" not in data:
            raise ValueError("Missing required field in Soul: 'content'")
        return cls(content=data["content"], version=data.get("version"))
