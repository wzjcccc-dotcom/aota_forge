"""Model-facing trusted host resource adapter (M2-E).

The model-facing contract is a logical reference (resource kind + id),
never a filesystem path.  References are resolved through the trusted
resource boundary into bounded filesystem resources under
operator-configured trusted roots; reads keep the existing bounded
mechanics (size limits, symlink rejection, JSON validation).

Trusted roots come from operator configuration only (env allowlist or an
explicit TrustedResourceConfig); the model cannot configure roots and
cannot supply a path.

Wiring into host.status handlers is M2-I; this module provides the
primitives only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aota_forge.adapters.host.process import (
    backup_rollback_readiness,
    read_pidfile,
    read_receipt,
)
from aota_forge.core.project.resolver import load_workspace_registry
from aota_forge.core.resources.resolver import (
    TrustedResourceConfig,
    TrustedResourceResolver,
)
from aota_forge.core.runtime.inspect import process_status, version_identity

HOST_RESOURCE_ROOT_ENV = {
    "runtime_pidfile": "AOTA_FORGE_HOST_ROOT_PIDFILE",
    "managed_deployment_receipt": "AOTA_FORGE_HOST_ROOT_RECEIPTS",
    "runtime_identity": "AOTA_FORGE_HOST_ROOT_RUNTIME",
    "project_registry": "AOTA_FORGE_HOST_ROOT_REGISTRY",
    "backup_receipt": "AOTA_FORGE_HOST_ROOT_BACKUP",
}


def host_resource_config_from_env() -> TrustedResourceConfig:
    """Build trusted roots from the operator env allowlist only.

    Only the exact allowlisted variable names are honored; any other
    variable (including a model-visible arbitrary path) is ignored.
    Kinds without a configured root fail closed at resolution time.
    """
    roots: dict[str, Path] = {}
    for kind, env_name in HOST_RESOURCE_ROOT_ENV.items():
        value = os.environ.get(env_name)
        if value:
            roots[kind] = Path(value)
    return TrustedResourceConfig(roots)


def resolve_host_resource(kind: str, resource_id: str, config: TrustedResourceConfig) -> dict[str, Any]:
    """Resolve a logical host resource reference to its bounded path.

    Read-only; does not open or read the resource.
    """
    path = TrustedResourceResolver(config).resolve(kind, resource_id)
    return {"kind": kind, "resource_id": resource_id, "path": str(path)}


def host_process_status_ref(resource_id: str, config: TrustedResourceConfig) -> dict[str, Any]:
    """Bounded host process observation by logical runtime pidfile id."""
    path = TrustedResourceResolver(config).resolve("runtime_pidfile", resource_id)
    pid = read_pidfile(path)
    return process_status(pid).to_dict()


def read_deployment_receipt_ref(resource_id: str, config: TrustedResourceConfig) -> dict[str, Any]:
    """Bounded managed deployment receipt inspection by logical receipt id."""
    path = TrustedResourceResolver(config).resolve("managed_deployment_receipt", resource_id)
    return read_receipt(path)


def host_runtime_identity_ref(resource_id: str, config: TrustedResourceConfig) -> dict[str, Any]:
    """Bounded runtime identity by logical runtime root id."""
    path = TrustedResourceResolver(config).resolve("runtime_identity", resource_id)
    return version_identity(path.parent).to_dict()


def read_project_registry_ref(resource_id: str, config: TrustedResourceConfig) -> dict[str, Any]:
    """Bounded project registry inspection by logical registry id."""
    path = TrustedResourceResolver(config).resolve("project_registry", resource_id)
    return load_workspace_registry(path)


def inspect_backup_readiness_ref(
    backup_id: str, receipt_ids: list[str], config: TrustedResourceConfig
) -> dict[str, Any]:
    """Bounded managed backup / rollback readiness by logical ids."""
    resolver = TrustedResourceResolver(config)
    backup_dir = resolver.resolve("backup_receipt", backup_id).parent
    receipts = [resolver.resolve("managed_deployment_receipt", rid) for rid in receipt_ids]
    return backup_rollback_readiness(backup_dir, receipts)
