"""CLI trusted adapter configuration channel (M2-F).

Model-facing filesystem paths are denied at the CLI boundary.  Where the
CLI needs a registry / pidfile / receipt resource it consumes the M2-E
logical resource semantics: a logical resource id resolved through an
operator-configured trusted root.

The trusted channel is the environment variable ``AOTA_FORGE_ADAPTER_CONFIG``
pointing to an operator-maintained JSON file:

    {
      "registry_id": "canonical",
      "trusted_roots": {
        "project_registry": "/operator/path",
        "runtime_pidfile": "/operator/path",
        "managed_deployment_receipt": "/operator/path"
      }
    }

Fail-closed: unreadable, oversized, malformed or unknown-keyed config
files raise ``AdapterConfigInvalidError``; unsafe roots are rejected by
the canonical M2-E ``TrustedResourceResolver``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.resources.resolver import (
    RESOURCE_KINDS,
    HostResourceDeniedError,
    TrustedResourceConfig,
    TrustedResourceResolver,
)

ADAPTER_CONFIG_ENV = "AOTA_FORGE_ADAPTER_CONFIG"
MAX_ADAPTER_CONFIG_BYTES = 64 * 1024


class AdapterConfigInvalidError(ForgeError):
    """Operator adapter configuration is invalid, unsafe or unreadable."""

    def __init__(self, message: str = "adapter configuration is invalid", retryable: bool = False) -> None:
        super().__init__("ADAPTER_CONFIG_INVALID", message, retryable)


@dataclass(frozen=True)
class AdapterTrustedConfig:
    """Operator-trusted CLI adapter configuration (bounded, fail-closed)."""

    registry_id: str | None = None
    roots: TrustedResourceConfig = field(default_factory=TrustedResourceConfig)


def load_adapter_trusted_config(environ: Mapping[str, str] | None = None) -> AdapterTrustedConfig:
    """Load the operator trusted configuration from the environment channel."""
    env = environ if environ is not None else os.environ
    raw = env.get(ADAPTER_CONFIG_ENV)
    if not raw:
        return AdapterTrustedConfig()
    path = Path(raw)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ADAPTER_CONFIG_BYTES:
            raise AdapterConfigInvalidError("adapter config file is missing, unsafe or too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except AdapterConfigInvalidError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterConfigInvalidError(f"adapter config unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise AdapterConfigInvalidError("adapter config must be an object")
    unknown = set(data) - {"registry_id", "trusted_roots"}
    if unknown:
        raise AdapterConfigInvalidError(f"unknown adapter config keys: {sorted(unknown)}")
    registry_id = data.get("registry_id")
    if registry_id is not None and (not isinstance(registry_id, str) or not registry_id):
        raise AdapterConfigInvalidError("registry_id must be a non-empty string")
    roots_raw = data.get("trusted_roots") or {}
    if not isinstance(roots_raw, dict):
        raise AdapterConfigInvalidError("trusted_roots must be an object")
    roots: dict[str, Path] = {}
    for kind, root in roots_raw.items():
        if kind not in RESOURCE_KINDS:
            raise AdapterConfigInvalidError(f"unknown trusted resource kind: {kind}")
        if not isinstance(root, str) or not root:
            raise AdapterConfigInvalidError(f"trusted root must be a non-empty string: {kind}")
        roots[kind] = Path(root)
    return AdapterTrustedConfig(registry_id=registry_id, roots=TrustedResourceConfig(roots))


def resolve_trusted_resource(kind: str, resource_id: str, config: AdapterTrustedConfig) -> Path:
    """Resolve a logical resource reference through the M2-E trusted boundary."""
    return TrustedResourceResolver(config.roots).resolve(kind, resource_id)


def resolve_trusted_registry(registry_id: str | None, config: AdapterTrustedConfig) -> Path:
    """Resolve the project registry through the trusted boundary."""
    if not registry_id:
        raise HostResourceDeniedError("project_registry: no registry id configured")
    return resolve_trusted_resource("project_registry", registry_id, config)


def adapter_config_documentation() -> dict[str, Any]:
    """Bounded machine description of the operator trusted channel."""
    return {
        "env": ADAPTER_CONFIG_ENV,
        "registry_id": "logical project_registry resource id (defaults applied when a flag is absent)",
        "trusted_roots": "operator-configured trusted roots per logical resource kind",
    }
