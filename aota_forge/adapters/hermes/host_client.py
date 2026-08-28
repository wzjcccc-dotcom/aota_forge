"""Production Hermes host bridge.

This module owns the subprocess and all runtime state.  It deliberately exposes
only the small envelope expected by :class:`HermesAdapter`; process IDs,
profiles, and retained output never become canonical identities.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .executor import HermesHostUnavailableError

MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RECORDS = 128
READER_DRAIN_TIMEOUT_SECONDS = 1.0
_WORKING_DIRECTORY_KEYS = ("cwd", "working_directory", "working_dir", "repo_path", "repo_root", "dir")
_SUPPORTED_CONSTRAINTS = frozenset({"timeout_seconds", "execution_mode", "isolation", "isolation_mode"})
_SUPPORTED_REQUIREMENTS = frozenset(
    {
        "execution_mode",
        "isolation",
        "isolation_mode",
        "timeout_seconds",
        "requires_working_directory",
    }
)


class HermesHostClientError(Exception):
    """Bounded host-client error with a canonical adapter error code."""

    def __init__(self, message: str, code: str = "ADAPTER_PROTOCOL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self.truncated = False
        self._lock = threading.Lock()

    def append(self, value: bytes) -> None:
        with self._lock:
            remaining = self._limit - len(self._data)
            if remaining > 0:
                self._data.extend(value[:remaining])
            if len(value) > remaining:
                self.truncated = True

    def text(self) -> str:
        marker = b"\n...[output truncated]"
        with self._lock:
            data = bytes(self._data)
            truncated = self.truncated
        if truncated:
            if self._limit >= len(marker):
                data = data[: self._limit - len(marker)] + marker
            else:
                data = data[: self._limit]
        return data.decode("utf-8", errors="replace")


@dataclass
class _ExecutionRecord:
    process: Any
    profile: str
    cwd: str
    started_at: float
    deadline: float
    stdout: _BoundedOutput
    stderr: _BoundedOutput
    readers: list[threading.Thread] = field(default_factory=list)
    status: str = "pending"
    exit_code: int | None = None
    terminal_at: float | None = None
    timed_out: bool = False
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class HermesHostClient:
    """Run bounded Hermes one-shot workers behind the Hermes host protocol."""

    def __init__(
        self,
        launcher_path: str | os.PathLike[str],
        *,
        default_cwd: str | os.PathLike[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = MAX_OUTPUT_BYTES,
        max_records: int = DEFAULT_MAX_RECORDS,
        popen_factory: Callable[..., Any] | None = None,
        validate_launcher: bool = True,
    ) -> None:
        if not isinstance(launcher_path, (str, os.PathLike)) or not str(launcher_path).strip():
            raise ValueError("launcher_path must be a non-empty path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(output_limit_bytes) is not int or output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be a positive integer")
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("max_records must be a positive integer")

        self.launcher_path = str(launcher_path)
        self.default_cwd = self._validate_cwd(default_cwd) if default_cwd is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self.output_limit_bytes = output_limit_bytes
        self.max_records = max_records
        self._popen_factory = popen_factory or subprocess.Popen
        if validate_launcher:
            self._validate_launcher()
        self._records: OrderedDict[str, _ExecutionRecord] = OrderedDict()
        self._records_lock = threading.Lock()

    def _validate_launcher(self) -> None:
        path = Path(self.launcher_path)
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise HermesHostUnavailableError(
                f"EXECUTOR_UNAVAILABLE: Hermes launcher is missing or not executable: {self.launcher_path}"
            )

    @staticmethod
    def _validate_cwd(value: str | os.PathLike[str] | None) -> str:
        if value is None:
            raise HermesHostClientError(
                "PACKAGE_INVALID: no canonical or trusted default working directory", "PACKAGE_INVALID"
            )
        path = Path(value)
        if path.is_symlink() or not path.is_dir():
            raise HermesHostClientError(
                f"PACKAGE_INVALID: working directory is missing or unsafe: {value}",
                code="PACKAGE_INVALID",
            )
        return str(path.resolve())

    @staticmethod
    def _read_stream(stream: Any, output: _BoundedOutput) -> None:
        if stream is None:
            return
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            output.append(bytes(chunk))

    def _resolve_cwd(self, payload: Mapping[str, Any]) -> str:
        context = payload.get("context")
        if not isinstance(context, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: host payload context must be a mapping", "PACKAGE_INVALID")
        working_context = context.get("working_context", {})
        if not isinstance(working_context, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: working_context must be a mapping", "PACKAGE_INVALID")
        unsupported_context = sorted(set(working_context) - set(_WORKING_DIRECTORY_KEYS))
        if unsupported_context:
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: unsupported working_context values cannot be dropped",
                "CAPABILITY_MISMATCH",
            )

        requested: list[tuple[str, Any]] = [
            (key, working_context[key]) for key in _WORKING_DIRECTORY_KEYS if key in working_context
        ]
        if len({str(value) for _, value in requested}) > 1:
            raise HermesHostClientError(
                "PACKAGE_INVALID: working_context contains conflicting directories", "PACKAGE_INVALID"
            )
        if requested:
            value = requested[0][1]
            if not isinstance(value, str) or not value.strip():
                raise HermesHostClientError("PACKAGE_INVALID: working directory must be a non-empty string", "PACKAGE_INVALID")
            return self._validate_cwd(value)
        return self.default_cwd or self._validate_cwd(None)

    def _validate_payload(self, payload: Mapping[str, Any]) -> tuple[str, str, str, float]:
        required = {
            "profile",
            "instruction",
            "context",
            "artifacts",
            "constraints",
            "capability_requirements",
            "result_expectations",
            "operation",
            "package_id",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise HermesHostClientError(f"PACKAGE_INVALID: host payload missing fields: {missing}", "PACKAGE_INVALID")
        profile = payload["profile"]
        instruction = payload["instruction"]
        if not isinstance(profile, str) or not profile.strip() or any(ch.isspace() for ch in profile):
            raise HermesHostClientError("PACKAGE_INVALID: Hermes profile is invalid", "PACKAGE_INVALID")
        if not isinstance(instruction, str) or not instruction.strip():
            raise HermesHostClientError("PACKAGE_INVALID: instruction is invalid", "PACKAGE_INVALID")
        if payload["operation"] != "task_dispatch":
            raise HermesHostClientError("DISPATCH_REJECTED: only task_dispatch is supported", "DISPATCH_REJECTED")
        if not isinstance(payload["artifacts"], (list, tuple)):
            raise HermesHostClientError("PACKAGE_INVALID: artifacts must be a list", "PACKAGE_INVALID")
        if payload["artifacts"]:
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: artifact transport is not supported by the production Hermes slice",
                "CAPABILITY_MISMATCH",
            )
        if not isinstance(payload["result_expectations"], Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: result_expectations must be a mapping", "PACKAGE_INVALID")
        if payload["result_expectations"]:
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: result expectations are not supported by the production Hermes slice",
                "CAPABILITY_MISMATCH",
            )

        constraints = payload["constraints"]
        requirements = payload["capability_requirements"]
        if not isinstance(constraints, Mapping) or not isinstance(requirements, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: constraints and capability_requirements must be mappings", "PACKAGE_INVALID")
        unsupported_constraints = sorted(set(constraints) - _SUPPORTED_CONSTRAINTS)
        unsupported_requirements = sorted(set(requirements) - _SUPPORTED_REQUIREMENTS)
        if unsupported_constraints or unsupported_requirements:
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: unsupported canonical requirements cannot be dropped",
                "CAPABILITY_MISMATCH",
            )

        requirement_mode = requirements.get("execution_mode")
        constraint_mode = constraints.get("execution_mode")
        if (
            "execution_mode" in requirements
            and "execution_mode" in constraints
            and requirement_mode != constraint_mode
        ):
            raise HermesHostClientError(
                "PACKAGE_INVALID: conflicting execution_mode requirements between "
                "capability_requirements and constraints",
                "PACKAGE_INVALID",
            )
        mode_values = []
        if "execution_mode" in requirements:
            mode_values.append(requirement_mode)
        if "execution_mode" in constraints:
            mode_values.append(constraint_mode)
        mode = mode_values[0] if mode_values else "async"
        isolation = requirements.get(
            "isolation_mode",
            requirements.get("isolation", constraints.get("isolation_mode", constraints.get("isolation", "process"))),
        )
        if mode != "async" or isolation != "process":
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: production Hermes supports only async process execution",
                "CAPABILITY_MISMATCH",
            )
        if requirements.get("requires_working_directory") not in (None, False, True):
            raise HermesHostClientError("PACKAGE_INVALID: requires_working_directory must be boolean", "PACKAGE_INVALID")
        for key, value in requirements.items():
            if key in {"execution_mode", "isolation", "isolation_mode"}:
                continue
            if key == "timeout_seconds" and (type(value) not in (int, float) or isinstance(value, bool) or value <= 0):
                raise HermesHostClientError("PACKAGE_INVALID: timeout_seconds must be positive", "PACKAGE_INVALID")
        timeout = requirements.get("timeout_seconds", constraints.get("timeout_seconds", self.timeout_seconds))
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or timeout <= 0:
            raise HermesHostClientError("PACKAGE_INVALID: timeout_seconds must be positive", "PACKAGE_INVALID")
        if timeout > self.timeout_seconds:
            raise HermesHostClientError("CAPABILITY_MISMATCH: requested timeout exceeds host bound", "CAPABILITY_MISMATCH")
        cwd = self._resolve_cwd(payload)
        return profile, instruction, cwd, float(timeout)

    def _evict_records(self) -> None:
        with self._records_lock:
            while len(self._records) >= self.max_records:
                removable = next(
                    ((handle, record) for handle, record in self._records.items() if record.status in {"done", "failed", "cancelled", "timeout"}),
                    None,
                )
                if removable is None:
                    raise HermesHostClientError("EXECUTOR_UNAVAILABLE: host execution record bound exhausted", "EXECUTOR_UNAVAILABLE")
                del self._records[removable[0]]

    def _record(self, adapter_handle: str) -> _ExecutionRecord:
        with self._records_lock:
            record = self._records.get(adapter_handle)
        if record is None:
            raise KeyError(adapter_handle)
        return record

    def _finalize(self, record: _ExecutionRecord, status: str, exit_code: int | None) -> bool:
        # W2 ordering invariant: a terminal success/failure observation is
        # published only after the stdout/stderr reader threads have finished
        # draining, so fetch_result can never freeze a partially collected
        # stream into a final result envelope.  Drain waits are bounded and
        # Hermes-private; if the streams are not stable within the bound, the
        # terminal observation is deferred and retried on the next status or
        # result poll.  Timeout/cancellation are terminal by local decision
        # and publish regardless, but they can never project a success.
        forced = status in {"timeout", "cancelled"}
        drain_deadline = time.monotonic() + READER_DRAIN_TIMEOUT_SECONDS
        drained = True
        for reader in record.readers:
            remaining = drain_deadline - time.monotonic()
            if remaining <= 0:
                drained = False
                break
            reader.join(timeout=remaining)
            if reader.is_alive():
                drained = False
                break
        if not drained and not forced:
            return False
        with record.lock:
            if record.terminal_at is None:
                record.status = status
                record.exit_code = exit_code
                record.terminal_at = time.monotonic()
        return True

    def _refresh(self, record: _ExecutionRecord) -> str:
        with record.lock:
            if record.terminal_at is not None:
                return record.status
            if time.monotonic() >= record.deadline:
                timed_out = True
            else:
                timed_out = False
        if timed_out:
            self._terminate(record, timed_out=True)
            return "timeout"
        returncode = record.process.poll()
        if returncode is None:
            with record.lock:
                record.status = "running"
            return "running"
        status = "done" if returncode == 0 else "failed"
        if not self._finalize(record, status, int(returncode)):
            return "running"
        return status

    def _terminate(self, record: _ExecutionRecord, *, timed_out: bool = False) -> None:
        with record.lock:
            if record.terminal_at is not None:
                return
        try:
            record.process.terminate()
            record.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                record.process.kill()
                record.process.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            pass
        returncode = record.process.poll()
        if timed_out:
            self._finalize(record, "timeout", returncode if isinstance(returncode, int) else None)
        else:
            self._finalize(record, "cancelled", returncode if isinstance(returncode, int) else None)

    def _watch(self, record: _ExecutionRecord) -> None:
        try:
            record.process.wait(timeout=max(0.0, record.deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            self._terminate(record, timed_out=True)
        except Exception:
            return
        if record.terminal_at is None:
            self._refresh(record)

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: host payload must be a mapping", "PACKAGE_INVALID")
        profile, instruction, cwd, timeout = self._validate_payload(payload)
        self._evict_records()
        adapter_handle = f"hermes-host-{uuid.uuid4().hex}"
        args = [self.launcher_path, "-p", profile, "-z", instruction]
        try:
            process = self._popen_factory(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise HermesHostUnavailableError(
                f"EXECUTOR_UNAVAILABLE: Hermes worker launch failed: {type(exc).__name__}"
            ) from exc

        now = time.monotonic()
        record = _ExecutionRecord(
            process=process,
            profile=profile,
            cwd=cwd,
            started_at=now,
            deadline=now + timeout,
            stdout=_BoundedOutput(self.output_limit_bytes),
            stderr=_BoundedOutput(self.output_limit_bytes),
        )
        for stream, output in ((getattr(process, "stdout", None), record.stdout), (getattr(process, "stderr", None), record.stderr)):
            if stream is not None:
                reader = threading.Thread(target=self._read_stream, args=(stream, output), daemon=True)
                reader.start()
                record.readers.append(reader)
        with self._records_lock:
            self._records[adapter_handle] = record
        threading.Thread(target=self._watch, args=(record,), daemon=True).start()
        return {"adapter_handle": adapter_handle, "status": "pending", "dispatch_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        try:
            record = self._record(adapter_handle)
        except KeyError:
            return {"status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "unknown adapter handle"}}
        status = self._refresh(record)
        return {"status": status, "details": record.stderr.text() if status == "failed" else ""}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        try:
            record = self._record(adapter_handle)
        except KeyError:
            return {"status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "unknown adapter handle"}}
        status = self._refresh(record)
        if status in {"running", "pending"}:
            return {"status": status}
        duration_ms = int(((record.terminal_at or time.monotonic()) - record.started_at) * 1000)
        response: dict[str, Any] = {
            "status": status,
            "exit_code": record.exit_code,
            "stdout": record.stdout.text(),
            "stderr": record.stderr.text(),
            "execution_stats": {"duration_ms": duration_ms},
        }
        if status == "timeout":
            response["error"] = {"code": "EXECUTION_TIMEOUT", "message": "Hermes worker exceeded its bounded timeout"}
        elif status == "cancelled":
            response["error"] = {"code": "EXECUTION_CANCELLED", "message": "Hermes worker cancelled"}
        elif status == "failed":
            response["error"] = {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"}
        return response

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        try:
            record = self._record(adapter_handle)
        except KeyError:
            return {"cancelled": False, "status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "unknown adapter handle"}}
        status = self._refresh(record)
        if status != "running":
            return {"cancelled": False, "status": status}
        self._terminate(record)
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "bounded Hermes oneshot has no resume session"}}

    def close(self) -> None:
        with self._records_lock:
            records = list(self._records.values())
        for record in records:
            if self._refresh(record) == "running":
                self._terminate(record)
