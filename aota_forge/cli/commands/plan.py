"""aota plan mutation command family — adapter only, routes via Core ingress (M4-8).

Parameter builders are pure transport projection: CLI flags -> semantic
input dict -> canonical ingress.  No semantic decisions, no authority
fabrication, no contract validation, no lifecycle logic happens here.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from aota_forge.cli.config import AdapterTrustedConfig


def plan_init_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Project CLI arguments for plan_init into canonical ingress parameters."""
    if getattr(args, "request", None) is not None and getattr(args, "store", None) is not None:
        return {
            "request": args.request,
            "store": args.store,
        }
    project_binding = getattr(args, "project_binding", None)
    if isinstance(project_binding, str):
        try:
            project_binding = json.loads(project_binding)
        except Exception:
            pass
    semantic_inputs = getattr(args, "semantic_inputs", None)
    if isinstance(semantic_inputs, str):
        try:
            semantic_inputs = json.loads(semantic_inputs)
        except Exception:
            pass
    params: dict[str, Any] = {
        "plan_ref": getattr(args, "plan_ref", None),
        "project_binding": project_binding,
        "semantic_inputs": semantic_inputs,
        "subject_expected_revision": getattr(args, "subject_expected_revision", None),
        "authority_source_revision": getattr(args, "authority_source_revision", None),
        "authority_observed_raw_digest": getattr(args, "authority_observed_raw_digest", None),
    }
    if getattr(args, "request", None) is not None:
        params["request"] = args.request
    if getattr(args, "store", None) is not None:
        params["store"] = args.store
    if getattr(args, "lease", None) is not None:
        params["lease"] = args.lease
    return params


def plan_retire_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Project CLI arguments for plan_retirement into canonical ingress parameters."""
    if getattr(args, "request", None) is not None and getattr(args, "store", None) is not None:
        return {
            "request": args.request,
            "store": args.store,
        }
    params: dict[str, Any] = {
        "plan_ref": getattr(args, "plan_ref", None),
        "retirement_kind": getattr(args, "retirement_kind", None),
        "snapshot_identity": getattr(args, "snapshot_identity", None),
        "subject_expected_revision": getattr(args, "subject_expected_revision", None),
        "authority_source_revision": getattr(args, "authority_source_revision", None),
        "authority_observed_raw_digest": getattr(args, "authority_observed_raw_digest", None),
        "successor_ref": getattr(args, "successor_ref", None),
    }
    if getattr(args, "request", None) is not None:
        params["request"] = args.request
    if getattr(args, "store", None) is not None:
        params["store"] = args.store
    if getattr(args, "lease", None) is not None:
        params["lease"] = args.lease
    return params
