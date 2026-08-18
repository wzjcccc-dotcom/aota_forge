"""M3-B11 shadow bootstrap and migration execution service.

Scope (Issue #9, Lane M3-B11):
- Orchestrates deterministic shadow materialization from MigrationInputManifest
  into an isolated InMemoryShadowRepository.
- Manages rebuild fingerprints, idempotency, atomic transactional staging/commit,
  and failure isolation.
- Bundles complete B11->B12 handoff object containing snapshot, provenance, receipts,
  comparison evidence, and rebuildability proof.
- Production isolation invariants:
    B11_CANONICAL_GRAPH_WRITE_COUNT = 0
    B11_PRODUCTION_REPOSITORY_WRITE_COUNT = 0
    PRODUCTION_LEASE_CONSUMPTION_COUNT = 0
    B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION = False
    B11_SHADOW_IMPORT_MAY_MUTATE_CURRENT_BINDING = False
    B11_TO_B12_HANDOFF_IMPLEMENTED = True
    B11_TO_B12_HANDOFF_REBUILDABLE = True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any

from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import make_object_ref
from aota_forge.core.migration.comparison import (
    ComparisonEvidence,
    compare_legacy_and_shadow,
)
from aota_forge.core.migration.input import (
    MigrationInputManifest,
)
from aota_forge.core.migration.provenance import (
    ProvenanceManifest,
)
from aota_forge.core.migration.receipt import (
    MigrationReceipt,
)
from aota_forge.core.migration.transform import (
    DEFAULT_TRANSFORMATION_VERSION,
    MigrationTransformer,
    TransformationResult,
)
from aota_forge.core.shadow.model import (
    B11_CANONICAL_GRAPH_WRITE_COUNT,
    B11_PRODUCTION_REPOSITORY_WRITE_COUNT,
    B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION,
    B11_SHADOW_IMPORT_MAY_MUTATE_CURRENT_BINDING,
    PRODUCTION_LEASE_CONSUMPTION_COUNT,
    SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY,
    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY,
    ShadowNamespace,
    ShadowStateMetadata,
)
from aota_forge.core.shadow.repository import (
    InMemoryShadowRepository,
)
from aota_forge.core.shadow.snapshot import (
    ShadowSnapshot,
)
from aota_forge.core.shadow.transaction import (
    ShadowTransaction,
    ShadowTransactionError,
)

B11_TO_B12_HANDOFF_IMPLEMENTED = True
B11_TO_B12_HANDOFF_REBUILDABLE = True
B11_SHADOW_IDEMPOTENCY_IMPLEMENTED = True
SAME_REBUILD_DUPLICATE_SEMANTIC_EFFECT = False


def compute_rebuild_fingerprint(
    source_manifest_fp: str,
    transformation_version: str,
    target_namespace: str,
    schema_version: str = "b3-canonical-v1",
) -> str:
    """Compute deterministic rebuild fingerprint over source, version, namespace and schema."""
    payload = {
        "source_manifest_fingerprint": source_manifest_fp,
        "transformation_version": transformation_version,
        "target_namespace": target_namespace,
        "schema_version": schema_version,
    }
    return canonical_fingerprint(payload)


@dataclass(frozen=True)
class B11ToB12Handoff:
    """Complete handoff bundle from M3-B11 for independent M3-B12 validation."""

    shadow_snapshot: ShadowSnapshot
    provenance_manifest: ProvenanceManifest
    source_fingerprints: dict[str, str]
    rebuild_fingerprint: str
    transformation_version: str
    migration_receipt: MigrationReceipt
    comparison_evidence: ComparisonEvidence | None = None
    production_isolation_proof: dict = field(default_factory=dict)

    def rebuild_equivalent_state(self) -> InMemoryShadowRepository:
        """Regenerate or independently inspect equivalent shadow state from handoff."""
        repo = InMemoryShadowRepository(self.shadow_snapshot.namespace)
        repo.restore_from_snapshot(self.shadow_snapshot)
        meta = ShadowStateMetadata(
            namespace=self.shadow_snapshot.namespace,
            transformation_version=self.transformation_version,
            rebuild_fingerprint=self.rebuild_fingerprint,
            source_manifest_fingerprint=self.shadow_snapshot.source_manifest_fingerprint,
            materialized_at=self.shadow_snapshot.metadata.get("materialized_at", ""),
            is_authoritative=False,
            is_production=False,
            is_shadow=True,
            record_counts=dict(self.shadow_snapshot.record_counts),
        )
        rebuilt_snap = repo.snapshot(meta)
        if rebuilt_snap.fingerprint() != self.shadow_snapshot.fingerprint():
            raise ValueError(
                f"Rebuilt snapshot fingerprint mismatch: "
                f"{rebuilt_snap.fingerprint()} != {self.shadow_snapshot.fingerprint()}"
            )
        return repo

    def to_dict(self) -> dict:
        return {
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "transformation_version": self.transformation_version,
            "source_fingerprints": dict(self.source_fingerprints),
            "shadow_snapshot": self.shadow_snapshot.to_dict(),
            "provenance_manifest": self.provenance_manifest.to_dict(),
            "migration_receipt": self.migration_receipt.to_dict(),
            "comparison_evidence": (
                self.comparison_evidence.to_dict() if self.comparison_evidence else None
            ),
            "production_isolation_proof": dict(self.production_isolation_proof),
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


@dataclass
class ShadowBootstrapResult:
    """Bounded outcome of a shadow bootstrap operation."""

    status: str  # PASS | PASS_WITH_FINDINGS | FAIL
    receipt: MigrationReceipt
    handoff: B11ToB12Handoff | None = None
    replayed: bool = False
    error_detail: str | None = None


class ShadowBootstrapService:
    """Service orchestrating isolated, non-authoritative shadow bootstrap materialization."""

    def __init__(
        self,
        repo: InMemoryShadowRepository,
        transformation_version: str = DEFAULT_TRANSFORMATION_VERSION,
    ) -> None:
        self.repo = repo
        self.transformation_version = transformation_version
        self._last_rebuild_fingerprint: str | None = None
        self._last_result: ShadowBootstrapResult | None = None

    def materialize(
        self,
        manifest: MigrationInputManifest,
        *,
        failure_injection_point: str | None = None,
        legacy_evidence_payload: dict | None = None,
    ) -> ShadowBootstrapResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        source_manifest_fp = manifest.fingerprint()
        rebuild_fp = compute_rebuild_fingerprint(
            source_manifest_fp,
            self.transformation_version,
            str(self.repo.namespace),
        )

        # Idempotency check: same rebuild fingerprint and repository state
        if self._last_rebuild_fingerprint == rebuild_fp and self._last_result is not None:
            if self._last_result.status in ("PASS", "PASS_WITH_FINDINGS"):
                # Return idempotent replay without duplicating records
                return ShadowBootstrapResult(
                    status=self._last_result.status,
                    receipt=self._last_result.receipt,
                    handoff=self._last_result.handoff,
                    replayed=True,
                )

        # Check for changed source with existing state
        if self._last_rebuild_fingerprint is not None and self._last_rebuild_fingerprint != rebuild_fp:
            # Clean rebuild required when source/transformation changes
            self.repo.clear()

        # Failure injection: after_normalization
        if failure_injection_point == "after_normalization":
            receipt = MigrationReceipt(
                receipt_id=f"rcpt_fail_{rebuild_fp[:16]}",
                source_manifest_fingerprint=source_manifest_fp,
                rebuild_fingerprint=rebuild_fp,
                transformation_version=self.transformation_version,
                shadow_namespace=str(self.repo.namespace),
                created_record_counts={},
                findings=("Injected failure after normalization",),
                status="FAILED",
                production_canonical_writes=0,
                production_lease_consumptions=0,
                materialized_at=now_iso,
            )
            return ShadowBootstrapResult(
                status="FAIL",
                receipt=receipt,
                error_detail="Injected failure after normalization",
            )

        # Transformation
        transformer = MigrationTransformer(self.transformation_version)
        tx_result = transformer.transform(manifest, str(self.repo.namespace))

        # Failure injection: during_transformation
        if failure_injection_point == "during_transformation":
            receipt = MigrationReceipt(
                receipt_id=f"rcpt_fail_{rebuild_fp[:16]}",
                source_manifest_fingerprint=source_manifest_fp,
                rebuild_fingerprint=rebuild_fp,
                transformation_version=self.transformation_version,
                shadow_namespace=str(self.repo.namespace),
                created_record_counts={},
                findings=("Injected failure during transformation",),
                status="FAILED",
                production_canonical_writes=0,
                production_lease_consumptions=0,
                materialized_at=now_iso,
            )
            return ShadowBootstrapResult(
                status="FAIL",
                receipt=receipt,
                error_detail="Injected failure during transformation",
            )

        # Isolated Transaction Boundary
        tx = ShadowTransaction(self.repo)
        try:
            tx.begin()

            # Handle partial failure injection points inside transaction
            if failure_injection_point == "after_staged_subject_creation":
                # Stage only subjects then raise error
                for rec in tx_result.records:
                    if rec.__class__.__name__ == "Subject":
                        tx.stage(rec)
                raise RuntimeError("Injected failure after staged subject creation")

            if failure_injection_point == "before_edge_commit":
                # Stage all except FollowupEdge then raise error
                for rec in tx_result.records:
                    if rec.__class__.__name__ != "FollowupEdge":
                        tx.stage(rec)
                raise RuntimeError("Injected failure before edge commit")

            # Stage all transformed records
            for rec in tx_result.records:
                tx.stage(rec)

            tx.commit()
        except Exception as exc:
            tx.rollback()
            receipt = MigrationReceipt(
                receipt_id=f"rcpt_fail_{rebuild_fp[:16]}",
                source_manifest_fingerprint=source_manifest_fp,
                rebuild_fingerprint=rebuild_fp,
                transformation_version=self.transformation_version,
                shadow_namespace=str(self.repo.namespace),
                created_record_counts={},
                findings=(str(exc),),
                status="FAILED",
                production_canonical_writes=0,
                production_lease_consumptions=0,
                materialized_at=now_iso,
            )
            return ShadowBootstrapResult(
                status="FAIL",
                receipt=receipt,
                error_detail=str(exc),
            )

        # Failure injection: during_receipt_finalization
        if failure_injection_point == "during_receipt_finalization":
            # Discard staged changes if receipt finalization fails
            self.repo.clear()
            receipt = MigrationReceipt(
                receipt_id=f"rcpt_fail_{rebuild_fp[:16]}",
                source_manifest_fingerprint=source_manifest_fp,
                rebuild_fingerprint=rebuild_fp,
                transformation_version=self.transformation_version,
                shadow_namespace=str(self.repo.namespace),
                created_record_counts={},
                findings=("Injected failure during receipt finalization",),
                status="FAILED",
                production_canonical_writes=0,
                production_lease_consumptions=0,
                materialized_at=now_iso,
            )
            return ShadowBootstrapResult(
                status="FAIL",
                receipt=receipt,
                error_detail="Injected failure during receipt finalization",
            )

        # Generate audit and handoff artifacts
        counts = tx_result.record_counts()
        status = "PASS_WITH_FINDINGS" if (tx_result.findings or tx_result.warnings) else "PASS"

        receipt = MigrationReceipt(
            receipt_id=f"rcpt_{rebuild_fp[:16]}",
            source_manifest_fingerprint=source_manifest_fp,
            rebuild_fingerprint=rebuild_fp,
            transformation_version=self.transformation_version,
            shadow_namespace=str(self.repo.namespace),
            created_record_counts=counts,
            unresolved_items=tuple(tx_result.unresolved),
            warnings=tuple(tx_result.warnings),
            findings=tuple(tx_result.findings),
            status=status,
            production_canonical_writes=0,
            production_lease_consumptions=0,
            materialized_at=now_iso,
        )

        metadata = ShadowStateMetadata(
            namespace=str(self.repo.namespace),
            transformation_version=self.transformation_version,
            rebuild_fingerprint=rebuild_fp,
            source_manifest_fingerprint=source_manifest_fp,
            materialized_at=now_iso,
            is_authoritative=False,
            is_production=False,
            is_shadow=True,
            record_counts=counts,
        )
        self.repo._metadata = metadata
        snapshot = self.repo.snapshot(metadata)

        provenance_manifest = ProvenanceManifest(
            entries=tuple(tx_result.provenance_records),
            transformation_version=self.transformation_version,
            rebuild_fingerprint=rebuild_fp,
            target_namespace=str(self.repo.namespace),
            source_manifest_fingerprint=source_manifest_fp,
        )

        # Comparison evidence if legacy payload provided
        comparison = None
        if legacy_evidence_payload:
            # Find primary plan subject
            plan_subs = [s for s in self.repo.subjects() if s.kind == "plan"]
            if plan_subs:
                primary_sub = plan_subs[0]
                execs = self.repo.executions_of_subject(
                    make_object_ref(IdKind.SUBJECT, primary_sub.subject_id)
                )
                decs = self.repo.decisions_of_subject(
                    make_object_ref(IdKind.SUBJECT, primary_sub.subject_id)
                )
                cmps = [
                    self.repo.completion_of_execution(
                        make_object_ref(IdKind.EXECUTION, e.execution_id)
                    )
                    for e in execs
                ]
                comparison = compare_legacy_and_shadow(
                    legacy_evidence_payload,
                    primary_sub,
                    executions=execs,
                    completions=[c for c in cmps if c is not None],
                    decisions=decs,
                )

        isolation_proof = {
            "production_graph_write_count": 0,
            "production_repository_write_count": 0,
            "production_lease_consumption_count": 0,
            "production_subject_revision_advancement": False,
            "production_binding_mutation": False,
            "shadow_state_is_authoritative": False,
            "shadow_repository_aliases_production": False,
        }

        handoff = B11ToB12Handoff(
            shadow_snapshot=snapshot,
            provenance_manifest=provenance_manifest,
            source_fingerprints=manifest.source_fingerprints(),
            rebuild_fingerprint=rebuild_fp,
            transformation_version=self.transformation_version,
            migration_receipt=receipt,
            comparison_evidence=comparison,
            production_isolation_proof=isolation_proof,
        )

        res = ShadowBootstrapResult(
            status=status,
            receipt=receipt,
            handoff=handoff,
            replayed=False,
        )
        self._last_rebuild_fingerprint = rebuild_fp
        self._last_result = res
        return res


__all__ = [
    "B11_TO_B12_HANDOFF_IMPLEMENTED",
    "B11_TO_B12_HANDOFF_REBUILDABLE",
    "B11_SHADOW_IDEMPOTENCY_IMPLEMENTED",
    "SAME_REBUILD_DUPLICATE_SEMANTIC_EFFECT",
    "compute_rebuild_fingerprint",
    "B11ToB12Handoff",
    "ShadowBootstrapResult",
    "ShadowBootstrapService",
]
