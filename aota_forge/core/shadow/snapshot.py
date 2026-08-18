"""M3-B11 isolated non-authoritative shadow snapshot model.

Provides deterministic serializable snapshots of shadow repository state
suitable for handoff to M3-B12 independent validation.

Snapshots are evidence only:
- SHADOW_SNAPSHOT_DETERMINISTIC = True
- SHADOW_SNAPSHOT_IS_PRODUCTION_AUTHORITY = False
- SHADOW_SNAPSHOT_IS_SUBJECT_AUTHORITY = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import TYPE_CHECKING, Any

from aota_forge.core.graph.serialization import serialize, to_dict
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.shadow.model import (
    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY,
    ShadowStateMetadata,
)

if TYPE_CHECKING:
    from aota_forge.core.shadow.repository import ShadowRepository

SHADOW_SNAPSHOT_DETERMINISTIC = True
SHADOW_SNAPSHOT_IS_PRODUCTION_AUTHORITY = False
SHADOW_SNAPSHOT_IS_SUBJECT_AUTHORITY = False


@dataclass(frozen=True)
class ShadowSnapshot:
    """Immutable deterministic serializable snapshot of a shadow repository."""

    namespace: str
    transformation_version: str
    rebuild_fingerprint: str
    source_manifest_fingerprint: str
    workflows: tuple[dict, ...] = ()
    subjects: tuple[dict, ...] = ()
    executions: tuple[dict, ...] = ()
    completions: tuple[dict, ...] = ()
    decisions: tuple[dict, ...] = ()
    edges: tuple[dict, ...] = ()
    record_counts: dict[str, int] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Deterministic SHA-256 digest of canonical state payload."""
        payload = {
            "namespace": self.namespace,
            "transformation_version": self.transformation_version,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "workflows": list(self.workflows),
            "subjects": list(self.subjects),
            "executions": list(self.executions),
            "completions": list(self.completions),
            "decisions": list(self.decisions),
            "edges": list(self.edges),
            "record_counts": dict(sorted(self.record_counts.items())),
        }
        return canonical_fingerprint(payload)

    def to_dict(self) -> dict:
        return {
            "snapshot_fingerprint": self.fingerprint(),
            "namespace": self.namespace,
            "transformation_version": self.transformation_version,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "workflows": list(self.workflows),
            "subjects": list(self.subjects),
            "executions": list(self.executions),
            "completions": list(self.completions),
            "decisions": list(self.decisions),
            "edges": list(self.edges),
            "record_counts": dict(self.record_counts),
            "metadata": dict(self.metadata),
            "is_production_authority": False,
            "is_subject_authority": False,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> ShadowSnapshot:
        return cls(
            namespace=data["namespace"],
            transformation_version=data["transformation_version"],
            rebuild_fingerprint=data["rebuild_fingerprint"],
            source_manifest_fingerprint=data["source_manifest_fingerprint"],
            workflows=tuple(data.get("workflows", ())),
            subjects=tuple(data.get("subjects", ())),
            executions=tuple(data.get("executions", ())),
            completions=tuple(data.get("completions", ())),
            decisions=tuple(data.get("decisions", ())),
            edges=tuple(data.get("edges", ())),
            record_counts=dict(data.get("record_counts", {})),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_repository(
        cls,
        repo: "ShadowRepository",
        metadata: ShadowStateMetadata | None = None,
    ) -> ShadowSnapshot:
        """Capture deterministic snapshot from an isolated ShadowRepository."""
        workflows = tuple(to_dict(w) for w in sorted(repo._workflows.values(), key=lambda r: r.workflow_id.value))
        subjects = tuple(to_dict(s) for s in sorted(repo._subjects.values(), key=lambda r: r.subject_id.value))
        executions = tuple(to_dict(e) for e in sorted(repo._executions.values(), key=lambda r: r.execution_id.value))
        completions = tuple(to_dict(c) for c in sorted(repo._completions.values(), key=lambda r: r.completion_id.value))
        decisions = tuple(to_dict(d) for d in sorted(repo._decisions.values(), key=lambda r: r.decision_id.value))
        edges = tuple(
            to_dict(e)
            for e in sorted(
                repo._edges.values(),
                key=lambda e: (e.parent_subject_ref.value, e.child_subject_ref.value, e.edge_id.value),
            )
        )

        counts = {
            "workflows": len(workflows),
            "subjects": len(subjects),
            "executions": len(executions),
            "completions": len(completions),
            "decisions": len(decisions),
            "edges": len(edges),
            "total": len(workflows) + len(subjects) + len(executions) + len(completions) + len(decisions) + len(edges),
        }

        trans_version = metadata.transformation_version if metadata else "m3-b11-v1"
        rebuild_fp = metadata.rebuild_fingerprint if metadata else ""
        source_fp = metadata.source_manifest_fingerprint if metadata else ""
        meta_dict = metadata.to_dict() if metadata else {}

        return cls(
            namespace=str(repo.namespace),
            transformation_version=trans_version,
            rebuild_fingerprint=rebuild_fp,
            source_manifest_fingerprint=source_fp,
            workflows=workflows,
            subjects=subjects,
            executions=executions,
            completions=completions,
            decisions=decisions,
            edges=edges,
            record_counts=counts,
            metadata=meta_dict,
        )


__all__ = [
    "SHADOW_SNAPSHOT_DETERMINISTIC",
    "SHADOW_SNAPSHOT_IS_PRODUCTION_AUTHORITY",
    "SHADOW_SNAPSHOT_IS_SUBJECT_AUTHORITY",
    "ShadowSnapshot",
]
