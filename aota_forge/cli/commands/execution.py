"""aota task / executor command families — transport parameter extraction only (M5-5).

Parameter builders are pure transport projection: CLI flags -> semantic
input dict -> canonical ingress. No semantic validation, executor selection,
package construction, or Hermes translation happens here.
"""

from __future__ import annotations

import argparse
from typing import Any

from aota_forge.cli.config import AdapterTrustedConfig


def task_start_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.task_start."""
    params: dict[str, Any] = {
        "executor": getattr(args, "executor", None),
        "role": getattr(args, "role", None),
        "instruction": getattr(args, "instruction", None),
        "project_id": getattr(args, "project_id", None),
    }
    if getattr(args, "subject_ref", None) is not None:
        params["subject_ref"] = args.subject_ref
    if getattr(args, "timeout", None) is not None:
        params["timeout"] = args.timeout
    return params


def task_status_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.task_status."""
    return {
        "task_id": getattr(args, "task_id", None),
        "executor": getattr(args, "executor", None),
    }


def task_result_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.task_result."""
    return {
        "task_id": getattr(args, "task_id", None),
        "executor": getattr(args, "executor", None),
    }


def task_cancel_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.task_cancel."""
    return {
        "task_id": getattr(args, "task_id", None),
        "executor": getattr(args, "executor", None),
    }


def executor_list_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.executor_list."""
    return {}


def executor_capabilities_params(args: argparse.Namespace, config: AdapterTrustedConfig) -> dict[str, Any]:
    """Extract transport arguments for execution.executor_capabilities."""
    return {
        "executor": getattr(args, "executor", None),
    }
