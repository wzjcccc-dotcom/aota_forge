"""Trusted provenance and stage evidence carriers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .provenance_base import *
from .provenance_helpers import as_mapping, normalize_capabilities, normalize_fingerprint

@dataclass(frozen=True)
class TrustedRuntimeProvenance:
    source: str | None = None
    authority_ref: str | None = None
    evidence_ref: str | None = None
    trusted: bool | None = None
    active: bool | None = None
    current: bool | None = None
    historical: bool | None = None
    stale: bool | None = None
    status: str | None = None
    fingerprint: str | None = None
    generation: str | int | None = None
    observed_at: Any = None
    expires_at: Any = None
    conflict: bool | None = None
    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": PROVENANCE_CONTRACT_VERSION, "source": self.source, "authority_ref": self.authority_ref, "evidence_ref": self.evidence_ref, "trusted": self.trusted, "active": self.active, "current": self.current, "historical": self.historical, "stale": self.stale, "status": self.status, "fingerprint": self.fingerprint, "generation": self.generation, "observed_at": self.observed_at, "expires_at": self.expires_at, "conflict": self.conflict}

RuntimeProvenance = TrustedRuntimeProvenance

@dataclass(frozen=True)
class RuntimeIdentityEvidence:
    stage: str
    fingerprint: str | None = None
    provenance: Any = None
    capabilities: frozenset[str] | None = None
    active: bool | None = None
    current: bool | None = None
    historical: bool | None = None
    stale: bool | None = None
    conflict: bool | None = None
    status: str | None = None
    generation: str | int | None = None
    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise RuntimeContractError("evidence stage is unknown", (REASON_UNKNOWN_PROVENANCE,))
        if self.fingerprint is not None:
            object.__setattr__(self, "fingerprint", normalize_fingerprint(self.fingerprint, "evidence fingerprint"))
        if self.capabilities is not None:
            object.__setattr__(self, "capabilities", normalize_capabilities(self.capabilities, "evidence capabilities"))
    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "fingerprint": self.fingerprint, "provenance": as_mapping(self.provenance) or self.provenance, "capabilities": sorted(self.capabilities) if self.capabilities is not None else None, "active": self.active, "current": self.current, "historical": self.historical, "stale": self.stale, "conflict": self.conflict, "status": self.status, "generation": self.generation}
