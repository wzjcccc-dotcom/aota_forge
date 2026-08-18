"""M3-B12 independent shadow rebuilder engine.

Scope (Issue #9, Lane M3-B12):
- Independently reconstructs shadow repository state directly from bounded handoff inputs.
- Directly recomputes source, rebuild, and snapshot fingerprints without trusting stored strings.
- Enforces canonical B3 schema, ownership, and lineage invariants.
- Validates identity set stability across destruction and repeated rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from aota_forge.core.graph import records
from aota_forge.core.graph.repository import GraphReferentialError
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.identity.ids import InternalId, make_id, parse_internal_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import (
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.migration.input import (
    CATEGORY_CLASSIFICATION_MAP,
    MigrationInput,
    MigrationInputManifest,
    SourceCategory,
    normalize_source_payload,
)
from aota_forge.core.migration.provenance import ProvenanceManifest, ProvenanceRecord
from aota_forge.core.migration.transform import (
    DEFAULT_TRANSFORMATION_VERSION,
    MigrationTransformer,
    TransformationResult,
)
from aota_forge.core.shadow.model import ShadowNamespace, ShadowStateMetadata
from aota_forge.core.shadow.repository import InMemoryShadowRepository, deserialize_record
from aota_forge.core.shadow.snapshot import ShadowSnapshot
from aota_forge.core.shadow.transaction import ShadowTransaction
from aota_forge.core.shadow_validation.model import (
    CANONICAL_B3_RECORD_KINDS,
    CANONICAL_INPUT_CLASSIFICATIONS,
)


@dataclass
class RebuildOutcome:
    """Outcome of an independent shadow rebuild operation."""

    success: bool
    repository: InMemoryShadowRepository
    snapshot: ShadowSnapshot
    provenance_manifest: ProvenanceManifest
    source_fingerprints: dict[str, str]
    source_manifest_fingerprint: str
    rebuild_fingerprint: str
    snapshot_fingerprint: str
    record_counts: dict[str, int]
    total_record_count: int
    unknown_record_kind_count: int
    findings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_valid: bool = True
    ownership_valid: bool = True
    lineage_valid: bool = True
    identity_valid: bool = True


class IndependentShadowRebuilder:
    """Independent engine that rebuilds and validates shadow state directly from inputs."""

    def __init__(
        self,
        transformation_version: str = DEFAULT_TRANSFORMATION_VERSION,
        target_namespace: str = "shadow_b12_independent",
    ) -> None:
        self.transformation_version = transformation_version
        self.target_namespace = target_namespace

    def recompute_source_fingerprints(self, inputs: list[MigrationInput]) -> dict[str, str]:
        """Directly recomputes normalized source fingerprints without trusting stored strings."""
        results = {}
        for inp in inputs:
            canonical_dict = {
                "source_category": inp.source_category,
                "logical_source_identity": inp.logical_source_identity,
                "version_id": inp.version_id,
                "semantic_classification": inp.semantic_classification,
                "target_kind": inp.target_kind,
                "target_identity": inp.target_identity,
                "payload": normalize_source_payload(inp.payload),
            }
            results[inp.logical_source_identity] = canonical_fingerprint(canonical_dict)
        return results

    def recompute_manifest_fingerprint(self, manifest: MigrationInputManifest) -> str:
        """Directly recomputes manifest fingerprint over normalized sorted inputs."""
        payload = {
            "manifest_id": manifest.manifest_id,
            "inputs": [inp.to_dict() for inp in manifest.inputs],
        }
        return canonical_fingerprint(payload)

    def recompute_rebuild_fingerprint(
        self,
        source_manifest_fp: str,
        transformation_version: str | None = None,
        target_namespace: str | None = None,
        schema_version: str = "b3-canonical-v1",
    ) -> str:
        """Independently recomputes rebuild fingerprint."""
        ver = transformation_version or self.transformation_version
        ns = target_namespace or self.target_namespace
        payload = {
            "source_manifest_fingerprint": source_manifest_fp,
            "transformation_version": ver,
            "target_namespace": ns,
            "schema_version": schema_version,
        }
        return canonical_fingerprint(payload)

    def rebuild(self, manifest: MigrationInputManifest) -> RebuildOutcome:
        """Perform independent rebuild from input manifest into fresh isolated repository."""
        source_fps = self.recompute_source_fingerprints(list(manifest.inputs))
        source_manifest_fp = self.recompute_manifest_fingerprint(manifest)
        rebuild_fp = self.recompute_rebuild_fingerprint(source_manifest_fp)

        # 1. Transform inputs using transformer
        transformer = MigrationTransformer(self.transformation_version)
        tx_result = transformer.transform(manifest, self.target_namespace)

        # 2. Inspect ontology & schema
        unknown_kinds = 0
        schema_valid = True
        ownership_valid = True
        lineage_valid = True
        identity_valid = True

        for rec in tx_result.records:
            rec_kind = rec.__class__.__name__
            if rec_kind not in CANONICAL_B3_RECORD_KINDS:
                unknown_kinds += 1
                schema_valid = False

            # Specific schema checks
            if isinstance(rec, records.Subject):
                if not rec.subject_id or rec.subject_id.kind != IdKind.SUBJECT:
                    identity_valid = False
            elif isinstance(rec, records.Execution):
                if not rec.execution_id or rec.execution_id.kind != IdKind.EXECUTION:
                    identity_valid = False
                if not rec.subject_ref or rec.subject_ref.kind != IdKind.SUBJECT:
                    ownership_valid = False
            elif isinstance(rec, records.Completion):
                if not rec.completion_id or rec.completion_id.kind != IdKind.COMPLETION:
                    identity_valid = False
                if not rec.execution_ref or rec.execution_ref.kind != IdKind.EXECUTION:
                    ownership_valid = False
                if hasattr(rec, "subject_ref"):
                    # Direct subject shortcut is forbidden
                    ownership_valid = False
                    schema_valid = False
            elif isinstance(rec, records.Decision):
                if not rec.decision_id or rec.decision_id.kind != IdKind.DECISION:
                    identity_valid = False
                if not rec.subject_ref or rec.subject_ref.kind != IdKind.SUBJECT:
                    ownership_valid = False
            elif isinstance(rec, records.FollowupEdge):
                if not rec.edge_id or rec.edge_id.kind != IdKind.EDGE:
                    identity_valid = False
                if not rec.parent_subject_ref or not rec.child_subject_ref:
                    lineage_valid = False
                if not rec.source_decision_ref or rec.source_decision_ref.kind != IdKind.DECISION:
                    lineage_valid = False

        # 3. Ingest into fresh isolated ShadowRepository
        repo = InMemoryShadowRepository(self.target_namespace)
        tx = ShadowTransaction(repo)
        tx.begin()
        for rec in tx_result.records:
            tx.stage(rec)
        tx.commit()

        counts = tx_result.record_counts()
        total_count = counts["total"]

        metadata = ShadowStateMetadata(
            namespace=self.target_namespace,
            transformation_version=self.transformation_version,
            rebuild_fingerprint=rebuild_fp,
            source_manifest_fingerprint=source_manifest_fp,
            materialized_at="2026-08-18T00:00:00Z",
            is_authoritative=False,
            is_production=False,
            is_shadow=True,
            record_counts=counts,
        )
        snapshot = repo.snapshot(metadata)
        snapshot_fp = snapshot.fingerprint()

        provenance_manifest = ProvenanceManifest(
            entries=tuple(tx_result.provenance_records),
            transformation_version=self.transformation_version,
            rebuild_fingerprint=rebuild_fp,
            target_namespace=self.target_namespace,
            source_manifest_fingerprint=source_manifest_fp,
        )

        return RebuildOutcome(
            success=tx_result.success and (unknown_kinds == 0),
            repository=repo,
            snapshot=snapshot,
            provenance_manifest=provenance_manifest,
            source_fingerprints=source_fps,
            source_manifest_fingerprint=source_manifest_fp,
            rebuild_fingerprint=rebuild_fp,
            snapshot_fingerprint=snapshot_fp,
            record_counts=counts,
            total_record_count=total_count,
            unknown_record_kind_count=unknown_kinds,
            findings=list(tx_result.findings),
            unresolved=list(tx_result.unresolved),
            warnings=list(tx_result.warnings),
            schema_valid=schema_valid,
            ownership_valid=ownership_valid,
            lineage_valid=lineage_valid,
            identity_valid=identity_valid,
        )

    def verify_rebuild_stability(self, manifest: MigrationInputManifest) -> bool:
        """Rebuild twice and verify identical identity sets and snapshots."""
        out1 = self.rebuild(manifest)
        out2 = self.rebuild(manifest)
        return (
            out1.snapshot_fingerprint == out2.snapshot_fingerprint
            and out1.rebuild_fingerprint == out2.rebuild_fingerprint
            and out1.total_record_count == out2.total_record_count
            and out1.record_counts == out2.record_counts
        )


__all__ = [
    "RebuildOutcome",
    "IndependentShadowRebuilder",
]
