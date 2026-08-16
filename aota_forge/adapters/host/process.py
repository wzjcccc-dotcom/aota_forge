"""Host process mechanical read-only adapter (M1-F, M2-E).

Bounded reads only: process status, start time.  No arbitrary PID
traversal and no generic terminal.  Requires NO Plan / Milestone / Work
Item / SPEC / Profile Task / approval / completion / decision / followup.

M2-E boundary note: the model-facing entry points for host resources are
``adapters.host.resources``, which accept logical references resolved
through the trusted resource boundary only.  The Path-based functions in
this module are bounded mechanical readers for already-resolved
resources; their direct model-facing routing is retired by M2-I.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.errors import ReceiptInvalidError
from aota_forge.core.runtime.inspect import process_status

MAX_PIDFILE_BYTES = 4096
MAX_RECEIPT_BYTES = 64 * 1024


def read_pidfile(pidfile: Path) -> int:
    """Read a bounded managed pidfile (strict int only).

    Only to be reached with a path resolved by the trusted resource
    boundary (``adapters.host.resources``); enforces size and symlink
    bounds fail-closed regardless.
    """
    try:
        if pidfile.is_symlink() or not pidfile.is_file() or pidfile.stat().st_size > MAX_PIDFILE_BYTES:
            raise ReceiptInvalidError(f"pidfile invalid: {pidfile}")
        text = pidfile.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReceiptInvalidError(f"pidfile unreadable: {type(exc).__name__}") from exc
    if not text.isdigit() or len(text) > 12:
        raise ReceiptInvalidError("pidfile must contain a bounded integer pid")
    return int(text)


def host_process_status(pidfile: Path) -> dict[str, Any]:
    """Bounded host process observation from a managed pidfile."""
    pid = read_pidfile(pidfile)
    return process_status(pid).to_dict()


def read_receipt(receipt: Path) -> dict[str, Any]:
    """Bounded managed deployment receipt inspection.

    Only to be reached with a path resolved by the trusted resource
    boundary (``adapters.host.resources``); enforces size, symlink and
    JSON shape bounds fail-closed regardless.
    """
    try:
        if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
            raise ReceiptInvalidError(f"receipt invalid: {receipt}")
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except ReceiptInvalidError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptInvalidError(f"receipt unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ReceiptInvalidError("receipt must be an object")
    if "schema_version" not in data:
        raise ReceiptInvalidError("receipt missing schema_version")
    return data


def backup_rollback_readiness(backup_dir: Path, required_receipts: list[Path]) -> dict[str, Any]:
    """Inspect managed backup / rollback readiness (read-only)."""
    result: dict[str, Any] = {"backup_dir_present": backup_dir.is_dir()}
    result["backup_dir"] = str(backup_dir)
    receipts: list[dict[str, Any]] = []
    for path in required_receipts:
        if path.is_file():
            try:
                receipts.append({"path": str(path), "valid": True, "receipt": read_receipt(path)})
            except ReceiptInvalidError as exc:
                receipts.append({"path": str(path), "valid": False, "error_code": exc.code})
        else:
            receipts.append({"path": str(path), "valid": False, "error_code": "RECEIPT_INVALID"})
    result["receipts"] = receipts
    result["ready"] = result["backup_dir_present"] and all(item["valid"] for item in receipts)
    return result
