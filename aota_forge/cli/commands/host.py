"""aota host command family — adapter only, routes via Core ingress (M2-F).

pidfile / receipt enter only as logical resource ids resolved through the
M2-E trusted resource boundary; model-facing filesystem paths are denied.
"""

from __future__ import annotations

import argparse
from typing import Any

from aota_forge.cli.config import AdapterTrustedConfig, resolve_trusted_resource


def status_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if getattr(args, "pidfile_id", None):
        params["pidfile"] = str(resolve_trusted_resource("runtime_pidfile", args.pidfile_id, config))
    if getattr(args, "receipt_id", None):
        params["receipt"] = str(resolve_trusted_resource("managed_deployment_receipt", args.receipt_id, config))
    return params
