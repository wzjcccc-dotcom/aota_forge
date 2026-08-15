"""aota project command family — adapter only, routes via Core ingress."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.core import execute as forge_execute


def resolve(workspace_id: str, project_id: str, registry: Path) -> dict[str, Any]:
    return forge_execute(
        "project.resolve",
        {"workspace_id": workspace_id, "project_id": project_id, "registry_path": str(registry)},
        principal="cli",
    )
