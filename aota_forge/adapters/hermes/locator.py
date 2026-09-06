"""Hermes-adapter-private durable mechanical execution locator (M2/W2).

This module owns the executor-specific durability seam that lets a NEW AF
process reconcile a Hermes one-shot Worker after the dispatching process is
gone.  It is deliberately narrow:

    adapter handle  ->  <runtime-root>/runs/<run-id>/{marker,spec,receipt,...}

The locator is MECHANICAL RUNTIME EVIDENCE, not AF canonical truth.  It is
not the semantic Task authority, not a workspace authority, not a Plan
authority, and not a result-acceptance authority.  AF canonical durable
execution state is owned by M2/W1 (``ExecutionStateStore`` is W1 authority;
nothing here may grow into it).  Hermes' own ``async_delegations`` /
``session_turn_leases`` ledger stays runtime evidence as well:
``HERMES_LEDGER_IS_AF_AUTHORITY=no``.

Security/authority boundaries (W2):

- the runtime root is operator/runtime supplied (explicit argument,
  ``AOTA_HERMES_RUNTIME_ROOT`` environment, or a deterministic per-user
  fallback).  It is never CWD-derived authority and never model-supplied;
- records are schema-versioned, bounded, atomically written, and malformed
  records fail closed (``HERMES_LOCATOR_CORRUPT``);
- no arbitrary filesystem path is ever accepted from payload or model text;
- artifacts carry only mechanical fields (ids, paths under the root, exit
  evidence, bounded output references).  Credentials never belong here.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCATOR_SCHEMA_VERSION = 1
ADAPTER_HANDLE_PREFIX = "hermes-host-"
RUNTIME_ROOT_ENV_VAR = "AOTA_HERMES_RUNTIME_ROOT"
DEFAULT_RUNTIME_ROOT_NAME = ".aota-forge"
DEFAULT_RUNTIME_ROOT_LEAF = "hermes-runtime"

_MARKER_NAME = "marker.json"
_SPEC_NAME = "spec.json"
_RECEIPT_NAME = "receipt.json"
_CHILD_NAME = "child.json"
_USAGE_NAME = "usage.json"
_STDOUT_NAME = "stdout.log"
_STDERR_NAME = "stderr.log"

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_HANDLE_RE = re.compile(r"^hermes-host-([0-9a-f]{32})$")
_RUN_DIR_NAME_RE = re.compile(r"^[0-9a-f]{32}$")

_MARKER_STATES = frozenset({"reserved", "spawned"})
_RECEIPT_STATUSES = frozenset({"done", "failed", "timeout", "cancelled"})


class HermesLocatorError(Exception):
    """Fail-closed locator error with a bounded mechanical error code."""

    def __init__(self, message: str, code: str = "HERMES_LOCATOR_CORRUPT") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def new_run_id() -> str:
    return uuid.uuid4().hex


def adapter_handle_for_run(run_id: str) -> str:
    if not _RUN_ID_RE.match(str(run_id or "")):
        raise HermesLocatorError("run id is not a locator run id", "HERMES_LOCATOR_CORRUPT")
    return ADAPTER_HANDLE_PREFIX + run_id


def run_id_from_adapter_handle(adapter_handle: Any) -> str | None:
    """Parse the run id out of an adapter handle; None means foreign handle."""
    if not isinstance(adapter_handle, str):
        return None
    match = _HANDLE_RE.match(adapter_handle)
    if match is None:
        return None
    return match.group(1)


def default_runtime_root() -> Path:
    """Operator/runtime-supplied mechanical root.

    Resolution order: explicit ``AOTA_HERMES_RUNTIME_ROOT`` environment value,
    else a deterministic per-user directory.  Never the shell CWD, never a
    model-supplied path.
    """
    env = os.environ.get(RUNTIME_ROOT_ENV_VAR, "").strip()
    if env:
        return Path(env)
    return Path.home() / DEFAULT_RUNTIME_ROOT_NAME / DEFAULT_RUNTIME_ROOT_LEAF


@dataclass(frozen=True)
class HermesRunPaths:
    """Filesystem layout for exactly one Hermes execution's private evidence."""

    root: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / self.run_id

    @property
    def marker(self) -> Path:
        return self.run_dir / _MARKER_NAME

    @property
    def spec(self) -> Path:
        return self.run_dir / _SPEC_NAME

    @property
    def receipt(self) -> Path:
        return self.run_dir / _RECEIPT_NAME

    @property
    def child(self) -> Path:
        return self.run_dir / _CHILD_NAME

    @property
    def usage(self) -> Path:
        return self.run_dir / _USAGE_NAME

    @property
    def stdout(self) -> Path:
        return self.run_dir / _STDOUT_NAME

    @property
    def stderr(self) -> Path:
        return self.run_dir / _STDERR_NAME

    def adapter_handle(self) -> str:
        return adapter_handle_for_run(self.run_id)


def prepare_run(root: Path, run_id: str) -> HermesRunPaths:
    """Create the private bounded run directory (0700) for one execution."""
    if not _RUN_ID_RE.match(str(run_id or "")):
        raise HermesLocatorError("run id is not a locator run id", "HERMES_LOCATOR_CORRUPT")
    root = Path(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    paths = HermesRunPaths(root, run_id)
    paths.run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    return paths


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably write one bounded JSON record (tmp + fsync + atomic rename)."""
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_json_object(path: Path) -> dict[str, Any] | None:
    """Read one JSON object record; None when the file does not exist.

    Any parse/shape failure fails closed: mechanical evidence is never
    repaired by guessing.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise HermesLocatorError(f"locator record could not be read: {exc.strerror or type(exc).__name__}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HermesLocatorError("locator record is malformed JSON") from exc
    if not isinstance(value, dict):
        raise HermesLocatorError("locator record must be a JSON object")
    return value


def _require_schema(record: Mapping[str, Any]) -> None:
    if record.get("schema") != LOCATOR_SCHEMA_VERSION:
        raise HermesLocatorError("locator record schema is not supported")


# --------------------------------------------------------------------------
# marker: what the dispatching AF process durably records about the launch
# --------------------------------------------------------------------------


def write_marker_reserved(
    paths: HermesRunPaths,
    *,
    adapter_handle: str,
    canonical_task_id: str,
    profile: str,
    cwd: str,
    launcher: str,
    dispatched_at_wall: float,
    deadline_wall: float,
) -> None:
    marker = {
        "schema": LOCATOR_SCHEMA_VERSION,
        "state": "reserved",
        "run_id": paths.run_id,
        "adapter_handle": adapter_handle,
        "canonical_task_id": canonical_task_id,
        "profile": profile,
        "cwd": cwd,
        "launcher": launcher,
        "dispatched_at_wall": float(dispatched_at_wall),
        "deadline_wall": float(deadline_wall),
        "supervisor_pid": None,
        "supervisor_start_ticks": None,
    }
    atomic_write_json(paths.marker, marker)


def mark_marker_spawned(paths: HermesRunPaths, *, supervisor_pid: int) -> dict[str, Any]:
    marker = read_json_object(paths.marker)
    if marker is None:
        raise HermesLocatorError("marker disappeared before spawn was recorded")
    _require_schema(marker)
    if marker.get("state") != "reserved":
        raise HermesLocatorError("marker state cannot transition to spawned")
    marker["state"] = "spawned"
    marker["supervisor_pid"] = int(supervisor_pid)
    marker["supervisor_start_ticks"] = proc_start_ticks(supervisor_pid)
    atomic_write_json(paths.marker, marker)
    return marker


def validate_marker(marker: Mapping[str, Any]) -> dict[str, Any]:
    _require_schema(marker)
    for key in ("run_id", "adapter_handle", "canonical_task_id", "profile", "cwd", "launcher", "dispatched_at_wall", "deadline_wall", "state"):
        if key not in marker:
            raise HermesLocatorError(f"marker is missing field {key!r}")
    state = marker["state"]
    if state not in _MARKER_STATES:
        raise HermesLocatorError("marker state is invalid")
    if not isinstance(marker["run_id"], str) or not _RUN_ID_RE.match(marker["run_id"]):
        raise HermesLocatorError("marker run id is invalid")
    task_id = marker["canonical_task_id"]
    if not isinstance(task_id, str):
        # The raw host protocol can be dispatched without a canonical task
        # context; the marker tolerates an empty (but only an empty-or-real)
        # string so durable evidence never becomes self-corrupting.
        raise HermesLocatorError("marker canonical task id must be a string")
    for key in ("adapter_handle", "profile", "cwd", "launcher"):
        value = marker[key]
        if not isinstance(value, str) or not value.strip():
            raise HermesLocatorError(f"marker field {key!r} is invalid")
    if state == "spawned":
        pid = marker.get("supervisor_pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise HermesLocatorError("marker supervisor identity is invalid")
    return dict(marker)


def write_spec(
    paths: HermesRunPaths,
    *,
    hermes_argv: list[str],
    cwd: str,
    timeout_seconds: float,
    started_at_wall: float,
    deadline_wall: float,
    output_limit_bytes: int,
) -> None:
    spec = {
        "schema": LOCATOR_SCHEMA_VERSION,
        "run_id": paths.run_id,
        "hermes_argv": [str(arg) for arg in hermes_argv],
        "cwd": cwd,
        "timeout_seconds": float(timeout_seconds),
        "started_at_wall": float(started_at_wall),
        "deadline_wall": float(deadline_wall),
        "output_limit_bytes": int(output_limit_bytes),
    }
    if not spec["hermes_argv"] or not cwd.strip():
        raise HermesLocatorError("launch spec is incomplete")
    atomic_write_json(paths.spec, spec)


def validate_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    _require_schema(spec)
    argv = spec.get("hermes_argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise HermesLocatorError("launch spec argv is invalid")
    if not isinstance(spec.get("cwd"), str) or not spec["cwd"].strip():
        raise HermesLocatorError("launch spec cwd is invalid")
    for key in ("timeout_seconds", "started_at_wall", "deadline_wall"):
        value = spec.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise HermesLocatorError(f"launch spec field {key!r} is invalid")
    limit = spec.get("output_limit_bytes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise HermesLocatorError("launch spec output bound is invalid")
    return dict(spec)


# --------------------------------------------------------------------------
# child / receipt: written by the durable supervisor
# --------------------------------------------------------------------------


def write_child_record(paths: HermesRunPaths, *, child_pid: int, started_at_wall: float) -> None:
    record = {
        "schema": LOCATOR_SCHEMA_VERSION,
        "run_id": paths.run_id,
        "child_pid": int(child_pid),
        "child_start_ticks": proc_start_ticks(child_pid),
        "started_at_wall": float(started_at_wall),
    }
    atomic_write_json(paths.child, record)


def validate_child_record(record: Mapping[str, Any]) -> dict[str, Any]:
    _require_schema(record)
    pid = record.get("child_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise HermesLocatorError("child record pid is invalid")
    started = record.get("started_at_wall")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        raise HermesLocatorError("child record start time is invalid")
    return dict(record)


def write_receipt(
    paths: HermesRunPaths,
    *,
    status: str,
    exit_code: int | None,
    started_at_wall: float,
    completed_at_wall: float,
    stdout_bytes: int,
    stderr_bytes: int,
    stdout_truncated: bool,
    stderr_truncated: bool,
    read_failure: bool,
) -> None:
    if status not in _RECEIPT_STATUSES:
        raise HermesLocatorError("receipt status is invalid")
    receipt = {
        "schema": LOCATOR_SCHEMA_VERSION,
        "run_id": paths.run_id,
        "status": status,
        "exit_code": int(exit_code) if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
        "started_at_wall": float(started_at_wall),
        "completed_at_wall": float(completed_at_wall),
        "stdout_bytes": int(stdout_bytes),
        "stderr_bytes": int(stderr_bytes),
        "stdout_truncated": bool(stdout_truncated),
        "stderr_truncated": bool(stderr_truncated),
        "read_failure": bool(read_failure),
    }
    atomic_write_json(paths.receipt, receipt)


def validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    _require_schema(receipt)
    status = receipt.get("status")
    if status not in _RECEIPT_STATUSES:
        raise HermesLocatorError("receipt status is invalid")
    exit_code = receipt.get("exit_code")
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise HermesLocatorError("receipt exit code is invalid")
    for key in ("stdout_bytes", "stderr_bytes"):
        value = receipt.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HermesLocatorError(f"receipt field {key!r} is invalid")
    for key in ("stdout_truncated", "stderr_truncated", "read_failure"):
        if not isinstance(receipt.get(key), bool):
            raise HermesLocatorError(f"receipt flag {key!r} is invalid")
    for key in ("started_at_wall", "completed_at_wall"):
        value = receipt.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise HermesLocatorError(f"receipt time field {key!r} is invalid")
    return dict(receipt)


def read_receipt(paths: HermesRunPaths) -> dict[str, Any] | None:
    receipt = read_json_object(paths.receipt)
    if receipt is None:
        return None
    return validate_receipt(receipt)


def published_receipt_status(receipt: Mapping[str, Any]) -> str:
    """Mechanical published status, never semantic AF acceptance.

    Mirrors the accepted W3 fail-closed marker: a capture that died mid-stream
    can never project a success even when the process exit code was 0.
    """
    if receipt["status"] == "done" and receipt["read_failure"]:
        return "failed"
    return str(receipt["status"])


def read_bounded_text(path: Path, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` bytes of a mechanical output file (never raw-trust)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except FileNotFoundError:
        return "", False
    except OSError as exc:
        raise HermesLocatorError(f"locator output file could not be read: {exc.strerror or type(exc).__name__}") from exc
    return data.decode("utf-8", errors="replace"), size > limit


# --------------------------------------------------------------------------
# process identity: pid alone is insufficient (pid reuse), pair with start
# ticks (birth identity) and a command-line run token (deterministic match)
# --------------------------------------------------------------------------


def proc_start_ticks(pid: int) -> int | None:
    """First-field-after-comm (field 22) starttime from /proc/<pid>/stat."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    text = raw.decode("ascii", errors="replace")
    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def proc_cmdline_tokens(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    return [token.decode("utf-8", errors="replace") for token in raw.split(b"\x00") if token]


def process_matches_identity(
    pid: int | None,
    *,
    expected_start_ticks: int | None,
    cmdline_token: str,
) -> bool | None:
    """True/False when identity is PROVEN; None when the OS cannot prove it.

    Never claims RUNNING on a bare ``kill(pid, 0)`` hit: a recycled PID must
    fail the birth-time and/or command-line identity checks.
    """
    if not pid or int(pid) <= 0:
        return False
    ticks = proc_start_ticks(int(pid))
    if ticks is None:
        if expected_start_ticks is None:
            return None  # identity unprovable on this OS
        return False  # expected a birth identity but /proc vanished -> dead
    if expected_start_ticks is not None and ticks != int(expected_start_ticks):
        return False  # recycled pid
    tokens = proc_cmdline_tokens(int(pid))
    if tokens is None:
        if expected_start_ticks is None:
            return None
        return False
    if cmdline_token and not any(cmdline_token in token for token in tokens):
        return False  # same birth window but not our process text
    return True


# --------------------------------------------------------------------------
# bounded retention (executor-private cleanup; never touches live evidence,
# and NEVER deletes evidence that a trusted AF runtime layer has not
# explicitly declared canonically safe — M2/W4 RV1 F01 repair)
# --------------------------------------------------------------------------


def list_run_dirs(root: Path) -> list[str]:
    runs = root / "runs"
    try:
        names = sorted(os.listdir(runs))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise HermesLocatorError(f"locator root could not be listed: {exc.strerror or type(exc).__name__}") from exc
    return [name for name in names if _RUN_DIR_NAME_RE.match(name)]


def prune_finished_runs(
    root: Path,
    *,
    retention_seconds: float,
    eligible_handles: Collection[str],
    now_wall: float | None = None,
) -> int:
    """Delete only terminal receipts a trusted AF runtime layer declared safe.

    Cleanup principle (M2/W4, plan #36): a terminal Hermes receipt may become
    cleanup-eligible ONLY after a trusted AF runtime layer has established
    durable terminal ``CanonicalResult`` + ``WorkerResultCard`` truth with a
    verified CARD digest.  That determination is made OUTSIDE this module and
    arrives as the explicit ``eligible_handles`` set.  Mechanical age is an
    ADDITIONAL retention criterion here and never the sole criterion:

        canonical_safe_to_gc (eligible_handles) AND retention_satisfied AND
        mechanically-valid terminal receipt -> receipt may be pruned.

    This locator remains executor-private mechanical evidence: it does not
    import, consult, or grow into the AF canonical store.  An empty eligible
    set removes nothing.  Runs that are active, receipt-less (UNKNOWN),
    nonterminal, or whose receipt evidence is malformed are never removed.
    """
    if isinstance(eligible_handles, (str, bytes)) or not isinstance(eligible_handles, Iterable):
        raise TypeError("eligible_handles must be a collection of adapter handle strings")
    eligible_run_ids: set[str] = set()
    for handle in eligible_handles:
        if not isinstance(handle, str):
            raise TypeError("eligible_handles entries must be adapter handle strings")
        run_id = run_id_from_adapter_handle(handle)
        if run_id is not None:
            eligible_run_ids.add(run_id)
    if not eligible_run_ids:
        return 0
    cutoff = (now_wall if now_wall is not None else time.time()) - max(0.0, float(retention_seconds))
    removed = 0
    for name in list_run_dirs(Path(root)):
        if name not in eligible_run_ids:
            # Not canonically safe (or foreign evidence): never time-pruned.
            continue
        paths = HermesRunPaths(Path(root), name)
        try:
            receipt = read_receipt(paths)
        except HermesLocatorError:
            continue
        if receipt is None or float(receipt["completed_at_wall"]) > cutoff:
            continue
        shutil.rmtree(paths.run_dir, ignore_errors=True)
        removed += 1
    return removed


__all__ = [
    "ADAPTER_HANDLE_PREFIX",
    "LOCATOR_SCHEMA_VERSION",
    "RUNTIME_ROOT_ENV_VAR",
    "HermesLocatorError",
    "HermesRunPaths",
    "adapter_handle_for_run",
    "atomic_write_json",
    "default_runtime_root",
    "list_run_dirs",
    "mark_marker_spawned",
    "new_run_id",
    "prepare_run",
    "process_matches_identity",
    "prune_finished_runs",
    "published_receipt_status",
    "read_bounded_text",
    "read_json_object",
    "read_receipt",
    "run_id_from_adapter_handle",
    "validate_child_record",
    "validate_marker",
    "validate_receipt",
    "validate_spec",
    "write_child_record",
    "write_marker_reserved",
    "write_receipt",
    "write_spec",
]
