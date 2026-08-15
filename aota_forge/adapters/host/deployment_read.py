"""Managed deployment receipt read-only adapter (M1-F).

Read-only deployment evidence inspection: receipt validation, backup /
rollback readiness.  No deployment mutation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.adapters.host.process import backup_rollback_readiness, read_receipt
from aota_forge.core.contracts.errors import ReceiptInvalidError


def inspect_deployment_receipt(receipt: Path) -> dict[str, Any]:
    return read_receipt(receipt)


def inspect_backup_readiness(backup_dir: Path, receipt: Path) -> dict[str, Any]:
    return backup_rollback_readiness(backup_dir, [receipt])


def validate_receipt_shape(data: dict[str, Any], required_fields: set[str]) -> dict[str, Any]:
    missing = sorted(required_fields - set(data))
    if missing:
        raise ReceiptInvalidError(f"receipt missing fields: {missing[0]}")
    return data
