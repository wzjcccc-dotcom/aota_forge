"""M3-B11 typed deterministic migration receipt.

Scope (Issue #9, Lane M3-B11):
- Immutable audit receipt recording the outcome of a shadow materialization.
- Receipts are audit evidence only:
    MIGRATION_RECEIPT_IMPLEMENTED = True
    MIGRATION_RECEIPT_IS_SUBJECT_AUTHORITY = False
    MIGRATION_RECEIPT_IS_CUTOVER_AUTHORITY = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from aota_forge.core.idempotency import canonical_fingerprint

MIGRATION_RECEIPT_IMPLEMENTED = True
MIGRATION_RECEIPT_IS_SUBJECT_AUTHORITY = False
MIGRATION_RECEIPT_IS_CUTOVER_AUTHORITY = False


@dataclass(frozen=True)
class MigrationReceipt:
    """Typed deterministic migration receipt capturing audit evidence."""

    receipt_id: str
    source_manifest_fingerprint: str
    rebuild_fingerprint: str
    transformation_version: str
    shadow_namespace: str
    created_record_counts: dict[str, int]
    unresolved_items: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    status: str = "SUCCESS"  # SUCCESS | SUCCESS_WITH_FINDINGS | FAILED
    production_canonical_writes: int = 0
    production_lease_consumptions: int = 0
    materialized_at: str = ""

    def fingerprint(self) -> str:
        payload = {
            "receipt_id": self.receipt_id,
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "transformation_version": self.transformation_version,
            "shadow_namespace": self.shadow_namespace,
            "created_record_counts": dict(sorted(self.created_record_counts.items())),
            "unresolved_items": list(self.unresolved_items),
            "warnings": list(self.warnings),
            "findings": list(self.findings),
            "status": self.status,
            "production_canonical_writes": self.production_canonical_writes,
            "production_lease_consumptions": self.production_lease_consumptions,
        }
        return canonical_fingerprint(payload)

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "receipt_fingerprint": self.fingerprint(),
            "source_manifest_fingerprint": self.source_manifest_fingerprint,
            "rebuild_fingerprint": self.rebuild_fingerprint,
            "transformation_version": self.transformation_version,
            "shadow_namespace": self.shadow_namespace,
            "created_record_counts": dict(self.created_record_counts),
            "unresolved_items": list(self.unresolved_items),
            "warnings": list(self.warnings),
            "findings": list(self.findings),
            "status": self.status,
            "production_canonical_writes": self.production_canonical_writes,
            "production_lease_consumptions": self.production_lease_consumptions,
            "materialized_at": self.materialized_at,
            "is_subject_authority": False,
            "is_cutover_authority": False,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


__all__ = [
    "MIGRATION_RECEIPT_IMPLEMENTED",
    "MIGRATION_RECEIPT_IS_SUBJECT_AUTHORITY",
    "MIGRATION_RECEIPT_IS_CUTOVER_AUTHORITY",
    "MigrationReceipt",
]
