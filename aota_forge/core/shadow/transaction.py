"""M3-B11 isolated non-authoritative shadow transaction boundary.

Provides atomic staging, commit, and rollback semantics for shadow materialization:
- Staged shadow records commit atomically into the isolated ShadowRepository.
- Zero partial commit on failure (B11_PARTIAL_SHADOW_COMMIT_ALLOWED=no,
  FAILED_SHADOW_IMPORT_LEAVES_PARTIAL_COMMIT=no).
- Zero coupling with production Capability Leases (PRODUCTION_LEASE_CONSUMPTION_COUNT=0).
- Zero mutation of production Subject revisions.
- Completely discardable on error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aota_forge.core.graph import records
from aota_forge.core.shadow.model import (
    B11_CANONICAL_GRAPH_WRITE_COUNT,
    B11_PRODUCTION_REPOSITORY_WRITE_COUNT,
    PRODUCTION_LEASE_CONSUMPTION_COUNT,
    ShadowNamespace,
)
from aota_forge.core.shadow.snapshot import ShadowSnapshot

if TYPE_CHECKING:
    from aota_forge.core.shadow.repository import InMemoryShadowRepository

B11_PARTIAL_SHADOW_COMMIT_ALLOWED = False
FAILED_SHADOW_IMPORT_LEAVES_PARTIAL_COMMIT = False


class ShadowTransactionError(Exception):
    """Base exception for shadow transaction failures."""


@dataclass(frozen=True)
class ShadowTransactionResult:
    """Bounded outcome of a shadow transaction."""

    outcome: str
    committed_count: int
    namespace: str
    is_authoritative: bool = False


class ShadowTransaction:
    """Atomic staging and commit boundary for isolated shadow repositories."""

    B11_PARTIAL_SHADOW_COMMIT_ALLOWED = False

    def __init__(
        self,
        repo: "InMemoryShadowRepository",
        namespace: ShadowNamespace | str | None = None,
    ) -> None:
        self._repo = repo
        if namespace is None:
            self._namespace = repo.namespace
        elif isinstance(namespace, str):
            self._namespace = ShadowNamespace(namespace)
        else:
            self._namespace = namespace

        if str(self._namespace) != str(repo.namespace):
            raise ValueError(
                f"Transaction namespace {self._namespace} does not match repository namespace {repo.namespace}"
            )

        self._staged: list[records._AnyRecord] = []
        self._active = False
        self._snapshot_before: ShadowSnapshot | None = None
        self._committed = False

    def begin(self) -> ShadowTransaction:
        if self._active:
            raise ShadowTransactionError("ShadowTransaction is already active")
        self._snapshot_before = self._repo.snapshot()
        self._staged.clear()
        self._active = True
        self._committed = False
        return self

    def stage(self, record: records._AnyRecord) -> ShadowTransaction:
        if not self._active:
            raise ShadowTransactionError("Cannot stage records in inactive transaction")
        self._staged.append(record)
        return self

    def stage_many(self, records_list: list[records._AnyRecord]) -> ShadowTransaction:
        if not self._active:
            raise ShadowTransactionError("Cannot stage records in inactive transaction")
        self._staged.extend(records_list)
        return self

    def commit(self) -> ShadowTransactionResult:
        if not self._active:
            raise ShadowTransactionError("Cannot commit inactive transaction")

        try:
            # Stage all records into repository checking structural referential integrity
            for rec in self._staged:
                self._repo.store(rec)
            committed_count = len(self._staged)
            self._staged.clear()
            self._active = False
            self._committed = True
            return ShadowTransactionResult(
                outcome="COMMITTED",
                committed_count=committed_count,
                namespace=str(self._namespace),
                is_authoritative=False,
            )
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        if self._snapshot_before is not None:
            self._repo.restore_from_snapshot(self._snapshot_before)
        self._staged.clear()
        self._active = False
        self._committed = False

    def __enter__(self) -> ShadowTransaction:
        return self.begin()

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self.rollback()
            return False
        return False


__all__ = [
    "B11_PARTIAL_SHADOW_COMMIT_ALLOWED",
    "FAILED_SHADOW_IMPORT_LEAVES_PARTIAL_COMMIT",
    "ShadowTransactionError",
    "ShadowTransactionResult",
    "ShadowTransaction",
]
