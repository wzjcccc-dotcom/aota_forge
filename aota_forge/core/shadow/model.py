"""M3-B11 isolated non-authoritative shadow state and namespace models.

Scope (Issue #9, Lane M3-B11):
- Explicit ShadowNamespace distinguishing shadow repository instances from production.
- Shadow state metadata capturing materialization provenance and non-authoritative status.
- Boundary invariants: shadow state is non-authoritative, never aliases production,
  and never defaults to production namespaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# Boundary invariants
SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY = False
SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY = False
SHADOW_NAMESPACE_DEFAULTS_TO_PRODUCTION = False
SHADOW_NAMESPACE_EXPLICIT = True
SHADOW_STATE_VISIBLE_TO_PRODUCTION_BINDING_BY_DEFAULT = False
SHADOW_REVISION_IS_CANONICAL_REVISION_AUTHORITY = False
CANONICAL_SUBJECT_REVISION_REMAINS_B6_AUTHORITY = True
B11_CANONICAL_GRAPH_WRITE_COUNT = 0
B11_PRODUCTION_REPOSITORY_WRITE_COUNT = 0
PRODUCTION_LEASE_CONSUMPTION_COUNT = 0
B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION = False
B11_SHADOW_IMPORT_REQUIRES_PRODUCTION_CAPABILITY_LEASE = False
B11_SHADOW_IMPORT_MAY_CONSUME_PRODUCTION_LEASE = False
B11_SHADOW_IMPORT_MAY_MUTATE_CURRENT_BINDING = False

FORBIDDEN_PRODUCTION_NAMESPACES = {
    "production",
    "canonical",
    "prod",
    "production_canonical",
    "default",
    "main",
    "master",
}


@dataclass(frozen=True)
class ShadowNamespace:
    """Explicit typed namespace for isolated shadow repository instances.

    Guarantees SHADOW_NAMESPACE_EXPLICIT=yes and prevents accidental
    or heuristic aliasing of production canonical namespaces.
    """

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("ShadowNamespace name must be a non-empty string")
        clean_name = self.name.strip()
        if clean_name.lower() in FORBIDDEN_PRODUCTION_NAMESPACES:
            raise ValueError(
                f"ShadowNamespace cannot alias production canonical namespace: {self.name!r}"
            )
        if not re.match(r"^[a-zA-Z0-9_-]+$", clean_name):
            raise ValueError(
                f"ShadowNamespace name contains invalid characters: {self.name!r}"
            )

    def is_production_aliased(self) -> bool:
        return False

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ShadowStateMetadata:
    """Audit and provenance metadata for shadow state materialization."""

    namespace: str
    transformation_version: str
    rebuild_fingerprint: str
    source_manifest_fingerprint: str
    materialized_at: str
    is_authoritative: bool = False
    is_production: bool = False
    is_shadow: bool = True
    record_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace,
            "transformation_version": self.transformation_version,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "materialized_at": self.materialized_at,
            "is_authoritative": self.is_authoritative,
            "is_production": self.is_production,
            "is_shadow": self.is_shadow,
            "record_counts": dict(self.record_counts),
        }


__all__ = [
    "SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY",
    "SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY",
    "SHADOW_NAMESPACE_DEFAULTS_TO_PRODUCTION",
    "SHADOW_NAMESPACE_EXPLICIT",
    "SHADOW_STATE_VISIBLE_TO_PRODUCTION_BINDING_BY_DEFAULT",
    "SHADOW_REVISION_IS_CANONICAL_REVISION_AUTHORITY",
    "CANONICAL_SUBJECT_REVISION_REMAINS_B6_AUTHORITY",
    "B11_CANONICAL_GRAPH_WRITE_COUNT",
    "B11_PRODUCTION_REPOSITORY_WRITE_COUNT",
    "PRODUCTION_LEASE_CONSUMPTION_COUNT",
    "B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION",
    "B11_SHADOW_IMPORT_REQUIRES_PRODUCTION_CAPABILITY_LEASE",
    "B11_SHADOW_IMPORT_MAY_CONSUME_PRODUCTION_LEASE",
    "B11_SHADOW_IMPORT_MAY_MUTATE_CURRENT_BINDING",
    "FORBIDDEN_PRODUCTION_NAMESPACES",
    "ShadowNamespace",
    "ShadowStateMetadata",
]
