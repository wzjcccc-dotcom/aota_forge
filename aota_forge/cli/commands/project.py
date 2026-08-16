"""aota project / git command families — adapter only, routes via Core ingress (M2-F).

Parameter builders are pure transport projection: CLI flags -> semantic
input dict -> canonical ingress.  No project resolution, no registry
loading, no error interpretation happens here.
"""

from __future__ import annotations

import argparse
from typing import Any

from aota_forge.cli.config import AdapterTrustedConfig, resolve_trusted_registry


def _registry_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    registry_path = resolve_trusted_registry(args.registry_id or config.registry_id, config)
    return {
        "workspace_id": args.workspace_id,
        "project_id": args.project_id,
        "registry_path": str(registry_path),
    }


def resolve_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    return _registry_params(args, config)


def inspect_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    return _registry_params(args, config)
