"""aota host command family — adapter only, routes via Core ingress."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.core import execute as forge_execute


def status(pidfile: Path | None = None, receipt: Path | None = None) -> dict[str, Any]:
    return forge_execute(
        "host.status",
        {
            "pidfile": str(pidfile) if pidfile else None,
            "receipt": str(receipt) if receipt else None,
        },
        principal="cli",
    )
