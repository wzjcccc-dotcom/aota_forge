"""Trusted read-only resource resolver (M2-E).

Deterministic, fail-closed mapping of a logical resource reference
(kind + id) to exactly one bounded filesystem path under an
operator-configured trusted root.

Security properties:

    arbitrary absolute model path      → denied (no path field exists)
    ../ traversal                      → denied (id charset + separators)
    resource escaping trusted root     → denied (containment check)
    untrusted symlink escape           → denied (component walk)
    unknown resource kind              → denied
    known logical resource             → deterministic bounded resolution

The resolver never reads file content; reads happen in the existing
bounded readers only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from aota_forge.core.contracts.errors import ForgeError

MAX_RESOURCE_ID_LENGTH = 128

RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class HostResourceDeniedError(ForgeError):
    """A model-facing resource reference was denied by the trusted boundary."""

    def __init__(self, message: str = "host resource denied", retryable: bool = False) -> None:
        super().__init__("HOST_RESOURCE_DENIED", message, retryable)


@dataclass(frozen=True)
class ResourceKind:
    """One logical host resource family: kind → deterministic bounded layout."""

    name: str
    layout: str
    max_bytes: int


RESOURCE_KINDS: dict[str, ResourceKind] = {
    "runtime_pidfile": ResourceKind("runtime_pidfile", "{id}.pid", 4096),
    "managed_deployment_receipt": ResourceKind("managed_deployment_receipt", "{id}/deployment.json", 64 * 1024),
    "runtime_identity": ResourceKind("runtime_identity", "{id}/VERSION", 4096),
    "project_registry": ResourceKind("project_registry", "{id}.json", 512 * 1024),
    "backup_receipt": ResourceKind("backup_receipt", "{id}/backup.json", 64 * 1024),
}


@dataclass(frozen=True)
class TrustedResourceConfig:
    """Operator-configured trusted roots, one per resource kind."""

    roots: Mapping[str, Path] = field(default_factory=dict)

    def root_for(self, kind: str) -> Path | None:
        root = self.roots.get(kind)
        if root is None:
            return None
        return Path(root)

    def with_root(self, kind: str, root: Path) -> "TrustedResourceConfig":
        return TrustedResourceConfig(dict(self.roots, **{kind: Path(root)}))


class TrustedResourceResolver:
    """Fail-closed resolver from logical resource refs to bounded paths."""

    def __init__(self, config: TrustedResourceConfig | None = None) -> None:
        self._config = config if config is not None else TrustedResourceConfig()

    def resolve(self, kind: str, resource_id: str) -> Path:
        spec = RESOURCE_KINDS.get(kind)
        if spec is None:
            raise HostResourceDeniedError(f"unknown resource kind: {kind}")
        if not isinstance(resource_id, str) or not resource_id:
            raise HostResourceDeniedError(f"{kind}: resource id is required")
        if len(resource_id) > MAX_RESOURCE_ID_LENGTH or not RESOURCE_ID_RE.fullmatch(resource_id):
            raise HostResourceDeniedError(f"{kind}: unsafe resource id: {resource_id!r}")
        if resource_id in (".", ".."):
            raise HostResourceDeniedError(f"{kind}: unsafe resource id: {resource_id!r}")
        root = self._config.root_for(kind)
        if root is None:
            raise HostResourceDeniedError(f"{kind}: no trusted root configured")
        if root.is_symlink() or not root.is_dir():
            raise HostResourceDeniedError(f"{kind}: trusted root is unsafe or missing: {root}")
        candidate = root / spec.layout.format(id=resource_id)
        self._reject_symlink_escape(root, candidate)
        try:
            candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError as exc:
            raise HostResourceDeniedError(f"{kind}: resource escapes trusted root") from exc
        return candidate

    @staticmethod
    def _reject_symlink_escape(root: Path, candidate: Path) -> None:
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise HostResourceDeniedError("resource escapes trusted root") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise HostResourceDeniedError("symlink escape rejected")


def resolve_host_resource(kind: str, resource_id: str, config: TrustedResourceConfig) -> Path:
    """Convenience resolution through a fresh trusted resolver."""
    return TrustedResourceResolver(config).resolve(kind, resource_id)
