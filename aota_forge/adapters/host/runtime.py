"""Host runtime identity adapter (M1-F, read-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.core.runtime.inspect import version_identity


def host_runtime_identity(project_root: Path) -> dict[str, Any]:
    return version_identity(project_root).to_dict()
