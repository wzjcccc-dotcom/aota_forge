"""Executor-neutral runtime read model (M1-E).

Concepts: RuntimeIdentity, RuntimeStatus, CapabilityStatus, RuntimeObservation.

The Core contract must NOT contain Hermes Profile ID, Hermes session ID,
Hermes Profile Task ID, or Hermes ProcessRegistry identity.  Hermes-specific
translation belongs to adapters later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeIdentity:
    name: str
    version: str | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "kind": self.kind}


@dataclass(frozen=True)
class RuntimeStatus:
    running: bool
    pid: int | None = None
    start_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"running": self.running, "pid": self.pid, "start_time": self.start_time}


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    available: bool
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available, "detail": self.detail}


@dataclass(frozen=True)
class RuntimeObservation:
    identity: RuntimeIdentity | None = None
    status: RuntimeStatus | None = None
    capabilities: list[CapabilityStatus] = field(default_factory=list)
    filesystem_hashes: dict[str, str] = field(default_factory=dict)
    parity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict() if self.identity else None,
            "status": self.status.to_dict() if self.status else None,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "filesystem_hashes": dict(self.filesystem_hashes),
            "parity": dict(self.parity),
        }
