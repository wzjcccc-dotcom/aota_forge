"""Read-only runtime inspection primitives (M1-E).

Process state, start time, filesystem hashes, source/runtime parity.
Executor-neutral: no Hermes identity concepts.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from aota_forge.core.contracts.errors import (
    RuntimeIdentityUnavailableError,
    RuntimeNotRunningError,
    SourceParityMismatchError,
)
from aota_forge.core.runtime.model import RuntimeIdentity, RuntimeStatus


def process_status(pid: int) -> RuntimeStatus:
    """Bounded process status: running, pid, start time (read-only)."""
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeNotRunningError("invalid pid")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise RuntimeNotRunningError(f"process {pid} is not running", retryable=False)
    except PermissionError:
        # Exists but not ours; still report running.
        pass
    except OSError:
        raise RuntimeNotRunningError(f"process {pid} status unavailable", retryable=True)
    return RuntimeStatus(running=True, pid=pid, start_time=_process_start_time(pid))


def _process_start_time(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.match(r"^\d+\s+\(.+\)\s+\S\s+[\d\s]+?\s(\d+)", stat)
    if not match:
        return None
    clock_ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    boot = _boot_time_seconds()
    if boot is None:
        return None
    started = boot + int(match.group(1)) / clock_ticks
    return f"{started:.3f}"


def _boot_time_seconds() -> float | None:
    try:
        uptime = Path("/proc/uptime").read_text(encoding="utf-8").split()
        seconds = float(uptime[0])
        import time
        return time.time() - seconds
    except (OSError, ValueError, IndexError):
        return None


def file_sha256(path: Path, max_bytes: int = 64 * 1024 * 1024) -> str:
    """Bounded sha256 of a single file (read-only)."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeIdentityUnavailableError(f"file unavailable: {path}")
    if path.stat().st_size > max_bytes:
        raise RuntimeIdentityUnavailableError(f"file too large: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_identity(root: Path) -> RuntimeIdentity:
    """Read bounded VERSION file as executor-neutral runtime identity."""
    version_file = root / "VERSION"
    try:
        if version_file.is_symlink() or not version_file.is_file():
            raise RuntimeIdentityUnavailableError("VERSION file missing")
        text = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeIdentityUnavailableError(f"VERSION unreadable: {type(exc).__name__}") from exc
    return RuntimeIdentity(name="aota-forge", version=text[:64] or None)


def compare_parity(source_hash: str, runtime_hash: str, label: str = "parity") -> dict[str, object]:
    """Compare source vs runtime filesystem hash.

    Returns evidence dict; raises SourceParityMismatchError on mismatch.
    """
    equal = source_hash == runtime_hash
    result: dict[str, object] = {
        "label": label,
        "source_sha256": source_hash,
        "runtime_sha256": runtime_hash,
        "match": equal,
    }
    if not equal:
        raise SourceParityMismatchError(f"source/runtime parity mismatch: {label}")
    return result
