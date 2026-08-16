"""aota runtime command family — adapter only, routes via Core ingress (M2-F)."""

from __future__ import annotations

import argparse
from typing import Any

from aota_forge.cli.config import AdapterTrustedConfig


def status_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    return {"pid": args.pid}
