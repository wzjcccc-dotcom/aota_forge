"""M3-B11 deterministic provenance model and manifest.

Scope (Issue #9, Lane M3-B11):
- Captures full traceability for every materialized shadow record:
    source_type, logical_source_identity, content_fingerprint,
    capture_version_id, semantic_classification, transformation_version,
    target_kind, target_identity.
- B11_PROVENANCE_MODEL_IMPLEMENTED = True
- PROVENANCE_MANIFEST_IMPLEMENTED = True
- B11_HIDDEN_AMBIENT_SOURCE_ALLOWED = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from aota_forge.core.idempotency import canonical_fingerprint

B11_PROVENANCE_MODEL_IMPLEMENTED = True
PROVENANCE_MANIFEST_IMPLEMENTED = True
B11_HIDDEN_AMBIENT_SOURCE_ALLOWED = False


@dataclass(frozen=True)
class ProvenanceRecord:
    """Provenance tracking entry for a single materialized shadow element."""

    source_type: str
    logical_source_identity: str
    content_fingerprint: str
    capture_version_id: str
    semantic_classification: str
    transformation_version: str
    target_kind: str
    target_identity: str

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "logical_source_identity": self.logical_source_identity,
            "content_fingerprint": self.content_fingerprint,
            "capture_version_id": self.capture_version_id,
            "semantic_classification": self.semantic_classification,
            "transformation_version": self.transformation_version,
            "target_kind": self.target_kind,
            "target_identity": self.target_identity,
        }


@dataclass(frozen=True)
class ProvenanceManifest:
    """Deterministic manifest of all provenance records for a shadow materialization."""

    entries: tuple[ProvenanceRecord, ...]
    transformation_version: str
    rebuild_fingerprint: str
    target_namespace: str
    source_manifest_fingerprint: str

    def manifest_fingerprint(self) -> str:
        payload = {
            "transformation_version": self.transformation_version,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "target_namespace": self.target_namespace,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "entries": [e.to_dict() for e in self.entries],
        }
        return canonical_fingerprint(payload)

    def to_dict(self) -> dict:
        return {
            "manifest_fingerprint": self.manifest_fingerprint(),
            "transformation_version": self.transformation_version,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "target_namespace": self.target_namespace,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "entry_count": len(self.entries),
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


__all__ = [
    "B11_PROVENANCE_MODEL_IMPLEMENTED",
    "PROVENANCE_MANIFEST_IMPLEMENTED",
    "B11_HIDDEN_AMBIENT_SOURCE_ALLOWED",
    "ProvenanceRecord",
    "ProvenanceManifest",
]
