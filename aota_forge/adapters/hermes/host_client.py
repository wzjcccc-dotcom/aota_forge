"""Production Hermes host bridge (M2/W2 durable runtime boundary).

This module owns Hermes one-shot launches.  Since M2/W2 the authoritative
execution evidence is the executor-private durable locator (see
:mod:`aota_forge.adapters.hermes.locator`) plus the atomic terminal receipt
written by the detached supervisor in :mod:`aota_forge.adapters.hermes.launcher`.
In-process state is a non-authoritative accelerator only: after an AF restart
a fresh :class:`HermesHostClient` can resolve the same adapter handles purely
from mechanical evidence — no old Popen object, no reader thread, no
process-local record store.

It still exposes only the small envelope expected by :class:`HermesAdapter`;
PIDs, paths, profiles, and retained output never become canonical identities.
Mechanical terminal evidence (an exit code) is never an AF semantic
acceptance: ``HERMES_PROCESS_EXIT_ZERO_IS_AF_ACCEPTANCE=no``.  When evidence
cannot establish a state, the boundary returns typed UNKNOWN — never a
fabricated COMPLETED/FAILED and never a silent re-dispatch.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import locator
from .executor import HermesHostUnavailableError
from .locator import HermesLocatorError, HermesRunPaths

MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RECORDS = 128
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600
CANCEL_RECEIPT_WAIT_SECONDS = 1.0
RECEIPT_POLL_SECONDS = 0.02
_TRUNCATION_MARKER = "\n...[output truncated]"
_USAGE_FILE_MAX_BYTES = 64 * 1024
_WORKING_DIRECTORY_KEYS = ("cwd", "working_directory", "working_dir", "repo_path", "repo_root", "dir")
_SEMANTIC_CONTEXT_KEYS = frozenset({"bounded_scope", "handoff_digest", "task_kind", "work_role", "refs"})
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

# Bootstrap used to launch the durable supervisor detached from this process.
# The importable package location is a machine-level fact computed from this
# module's own file, never from the payload, model text, or shell CWD.
# host_client.py lives at <root>/aota_forge/adapters/hermes/, so the parent
# directory that makes ``import aota_forge`` resolve is parents[3].
_PACKAGE_PARENT = str(Path(__file__).resolve().parents[3])
_SUPERVISOR_BOOTSTRAP = (
    f"import sys; sys.path.insert(0, {_PACKAGE_PARENT!r}); "
    "from aota_forge.adapters.hermes.launcher import main; "
    "raise SystemExit(main(sys.argv[1:]))"
)


class HermesHostClientError(Exception):
    """Bounded host-client error with a canonical adapter error code."""

    def __init__(self, message: str, code: str = "ADAPTER_PROTOCOL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class HermesHostClient:
    """Run bounded Hermes one-shot workers behind the durable Hermes protocol.

    Durable-boundary invariants (W2):

    - ``ADAPTER_HANDLE_RECOVERABLE_AFTER_RESTART=yes``: the public handle maps
      deterministically to ``<runtime-root>/runs/<run-id>`` mechanical evidence.
    - ``PROCESS_LOCAL_RECORD_REQUIRED_FOR_RECOVERY=no`` /
      ``OLD_POPEN_REQUIRED_FOR_RECOVERY=no``: recovery consults durable records
      plus OS process identity, never this instance's Python objects.
    - ``close()`` detaches local observation; it must NOT terminate durable
      workers (workers outliving the dispatching process is the point of M2).
    """

    def __init__(
        self,
        launcher_path: str | os.PathLike[str],
        *,
        default_cwd: str | os.PathLike[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = MAX_OUTPUT_BYTES,
        max_records: int = DEFAULT_MAX_RECORDS,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        runtime_root: str | os.PathLike[str] | None = None,
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
        if type(retention_seconds) not in (int, float) or isinstance(retention_seconds, bool) or retention_seconds < 0:
            raise ValueError("retention_seconds must be a non-negative number")

        self.launcher_path = str(launcher_path)
        self.default_cwd = self._validate_cwd(default_cwd) if default_cwd is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self.output_limit_bytes = output_limit_bytes
        # Capacity bound now counts ACTIVE durable runs (no terminal receipt
        # yet); terminal runs are kept until the bounded retention window.
        self.max_records = max_records
        self.retention_seconds = float(retention_seconds)
        self.runtime_root = Path(runtime_root) if runtime_root is not None else locator.default_runtime_root()
        self._popen_factory = popen_factory or subprocess.Popen
        if validate_launcher:
            self._validate_launcher()
        # Non-authoritative accelerator: supervisor Popen objects observed by
        # THIS instance.  Resolution never requires it.
        self._local_supervisors: dict[str, Any] = {}
        self._state_lock = threading.Lock()

    # -- construction-time validation ---------------------------------------

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

    def _resolve_cwd(self, payload: Mapping[str, Any]) -> str:
        context = payload.get("context")
        if not isinstance(context, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: host payload context must be a mapping", "PACKAGE_INVALID")
        working_context = context.get("working_context", {})
        if not isinstance(working_context, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: working_context must be a mapping", "PACKAGE_INVALID")
        unsupported_context = sorted(
            set(working_context) - set(_WORKING_DIRECTORY_KEYS) - _SEMANTIC_CONTEXT_KEYS
        )
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

    def _validate_payload(
        self, payload: Mapping[str, Any]
    ) -> tuple[str, str, str, float, str | None, str | None, tuple[str, ...] | None]:
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
        # Optional provider/model fields for W1 runtime binding (operator-owned, not semantic)
        provider = payload.get("provider")
        model = payload.get("model")
        if provider is not None:
            if not isinstance(provider, str) or not provider.strip() or any(ch.isspace() for ch in provider):
                raise HermesHostClientError("PACKAGE_INVALID: provider is invalid", "PACKAGE_INVALID")
            provider = provider.strip()
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise HermesHostClientError("PACKAGE_INVALID: model is invalid", "PACKAGE_INVALID")
            model = model.strip()
        # Optional toolsets field for the W4 mechanical Worker restriction:
        # operator-pinned Hermes toolset allowlist (shared MCP server names),
        # translated to a single bounded `-t <a,b>` argument. Deployment policy,
        # never semantic payload.
        toolsets = payload.get("toolsets")
        if toolsets is not None:
            if isinstance(toolsets, str) or not isinstance(toolsets, (list, tuple)) or len(toolsets) == 0:
                raise HermesHostClientError("PACKAGE_INVALID: toolsets must be a non-empty list", "PACKAGE_INVALID")
            for toolset in toolsets:
                if (
                    not isinstance(toolset, str)
                    or not toolset.strip()
                    or any(ch.isspace() for ch in toolset)
                    or "," in toolset
                ):
                    raise HermesHostClientError("PACKAGE_INVALID: toolset names are invalid", "PACKAGE_INVALID")
            if len({str(toolset) for toolset in toolsets}) != len(toolsets):
                raise HermesHostClientError("PACKAGE_INVALID: duplicate toolset names", "PACKAGE_INVALID")
            toolsets = tuple(str(toolset).strip() for toolset in toolsets)
        if payload["operation"] != "task_dispatch":
            raise HermesHostClientError("DISPATCH_REJECTED: only task_dispatch is supported", "DISPATCH_REJECTED")
        if not isinstance(payload["artifacts"], (list, tuple)):
            raise HermesHostClientError("PACKAGE_INVALID: artifacts must be a list", "PACKAGE_INVALID")
        metadata_artifacts = all(
            isinstance(item, Mapping) and item.get("handoff_kind") == "task_handoff"
            for item in payload["artifacts"]
        )
        if payload["artifacts"] and not metadata_artifacts:
            raise HermesHostClientError(
                "CAPABILITY_MISMATCH: artifact transport is not supported by the production Hermes slice",
                "CAPABILITY_MISMATCH",
            )
        if not isinstance(payload["result_expectations"], Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: result_expectations must be a mapping", "PACKAGE_INVALID")
        semantic_expectations = payload["result_expectations"]
        metadata_expectations = (
            set(semantic_expectations) <= {"validation_expectations", "semantic_stop_expectations"}
            and all(isinstance(value, list) and all(isinstance(item, str) for item in value) for value in semantic_expectations.values())
        )
        if semantic_expectations and not metadata_expectations:
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
        return profile, instruction, cwd, float(timeout), provider, model, toolsets

    # -- durable dispatch -----------------------------------------------------

    def _count_active_runs(self) -> int:
        active = 0
        for run_id in locator.list_run_dirs(self.runtime_root):
            try:
                if locator.read_receipt(HermesRunPaths(self.runtime_root, run_id)) is None:
                    active += 1
            except HermesLocatorError:
                # A run with unreadable evidence still occupies capacity: the
                # locator fails closed and an operator must resolve it.
                active += 1
        return active

    @staticmethod
    def _context_task_id(payload: Mapping[str, Any]) -> str:
        context = payload.get("context")
        if isinstance(context, Mapping):
            value = context.get("canonical_task_id")
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _hermes_argv(
        self,
        paths: HermesRunPaths,
        profile: str,
        instruction: str,
        provider: str | None,
        model: str | None,
        toolsets: tuple[str, ...] | None,
    ) -> list[str]:
        # Direct Hermes invocation per W1 launcher resolution Path A:
        # the executable comes from the operator RuntimeConfig; runtime binding is
        # translated -> hermes -p <profile> [-t <allowlist>] [--provider X] [-m Y]
        # --usage-file <durable> -z <instruction>.
        # Flags verified against Hermes v0.21 `hermes --help` / hermes_cli.oneshot:
        # -p, -t (toolset allowlist incl. MCP server names), --provider, -m,
        # --usage-file (durable machine-readable session identity), -z.
        args = [self.launcher_path, "-p", profile]
        if toolsets:
            args.extend(["-t", ",".join(str(toolset) for toolset in toolsets)])
        if provider:
            args.extend(["--provider", provider])
        if model:
            args.extend(["-m", model])
        # Hermes' own durable one-shot report: the only supported machine-readable
        # session identity of the `-z` path (hermes_cli/oneshot.py --usage-file).
        args.extend(["--usage-file", str(paths.usage)])
        args.extend(["-z", instruction])
        return args

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise HermesHostClientError("PACKAGE_INVALID: host payload must be a mapping", "PACKAGE_INVALID")
        profile, instruction, cwd, timeout, provider, model, toolsets = self._validate_payload(payload)
        try:
            locator.prune_finished_runs(self.runtime_root, retention_seconds=self.retention_seconds)
        except HermesLocatorError as exc:
            raise HermesHostUnavailableError(f"EXECUTOR_UNAVAILABLE: Hermes locator root is unusable: {exc.message}") from exc
        with self._state_lock:
            if self._count_active_runs() >= self.max_records:
                raise HermesHostUnavailableError(
                    "EXECUTOR_UNAVAILABLE: host execution record bound exhausted"
                )
            run_id = uuid.uuid4().hex
            try:
                paths = locator.prepare_run(self.runtime_root, run_id)
            except OSError as exc:
                raise HermesHostUnavailableError(
                    f"EXECUTOR_UNAVAILABLE: Hermes locator storage is unavailable: {type(exc).__name__}"
                ) from exc
            adapter_handle = paths.adapter_handle()
            now_wall = time.time()
            hermes_argv = self._hermes_argv(paths, profile, instruction, provider, model, toolsets)
            try:
                locator.write_spec(
                    paths,
                    hermes_argv=hermes_argv,
                    cwd=cwd,
                    timeout_seconds=timeout,
                    started_at_wall=now_wall,
                    deadline_wall=now_wall + timeout,
                    output_limit_bytes=self.output_limit_bytes,
                )
                locator.write_marker_reserved(
                    paths,
                    adapter_handle=adapter_handle,
                    canonical_task_id=self._context_task_id(payload),
                    profile=profile,
                    cwd=cwd,
                    launcher=self.launcher_path,
                    dispatched_at_wall=now_wall,
                    deadline_wall=now_wall + timeout,
                )
            except HermesLocatorError as exc:
                _cleanup_run(paths)
                raise HermesHostUnavailableError(
                    f"EXECUTOR_UNAVAILABLE: Hermes locator storage is unavailable: {exc.message}"
                ) from exc

            supervisor_argv = [sys.executable, "-c", _SUPERVISOR_BOOTSTRAP, str(paths.spec)]
            try:
                process = self._popen_factory(
                    supervisor_argv,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                _cleanup_run(paths)
                raise HermesHostUnavailableError(
                    f"EXECUTOR_UNAVAILABLE: Hermes worker launch failed: {type(exc).__name__}"
                ) from exc

            try:
                locator.mark_marker_spawned(paths, supervisor_pid=int(process.pid))
            except (HermesLocatorError, OSError, TypeError, ValueError):
                # The durable evidence must exist before the handle is handed
                # out; without it the run could never be recovered by a later process.
                _terminate_supervisor(process)
                _cleanup_run(paths)
                raise HermesHostUnavailableError(
                    "EXECUTOR_UNAVAILABLE: Hermes locator could not record the durable launch identity"
                )
            self._local_supervisors[adapter_handle] = process

        return {
            "adapter_handle": adapter_handle,
            "status": "pending",
            "dispatch_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_wall)),
        }

    # -- durable resolution ----------------------------------------------------

    def _not_found_envelope(self) -> dict[str, Any]:
        return {"status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "unknown adapter handle"}}

    def _corrupt_envelope(self, message: str) -> dict[str, Any]:
        return {"status": "unreachable", "error": {"code": "HERMES_LOCATOR_CORRUPT", "message": message}}

    def _unknown_envelope(self) -> dict[str, Any]:
        return {
            "status": "unknown",
            "error": {
                "code": "HERMES_EXECUTION_UNKNOWN",
                "message": "Hermes execution evidence cannot establish the state; no terminal was fabricated",
                "retryable": True,
            },
        }

    def _resolve(self, adapter_handle: str) -> dict[str, Any]:
        """Resolve mechanical execution state from durable evidence.

        Returns an observation dict:
          {"status": pending|running|done|failed|timeout|cancelled|unknown|unreachable,
           "paths"?, "marker"?, "receipt"?}
        The result NEVER depends on a Python object created by an earlier
        process, and never fabricates a terminal state from absence of data.
        """
        run_id = locator.run_id_from_adapter_handle(adapter_handle)
        if run_id is None:
            return self._not_found_envelope()
        paths = HermesRunPaths(self.runtime_root, run_id)
        if not paths.run_dir.is_dir():
            return self._not_found_envelope()
        try:
            marker = locator.validate_marker(locator.read_json_object(paths.marker) or {})
            receipt = locator.read_receipt(paths)
        except HermesLocatorError as exc:
            return self._corrupt_envelope(exc.message)

        if receipt is not None:
            published = locator.published_receipt_status(receipt)
            return {"status": published, "paths": paths, "marker": marker, "receipt": receipt}

        supervisor_state = self._supervisor_liveness(adapter_handle, paths, marker)
        if supervisor_state is None:
            return dict(self._unknown_envelope(), paths=paths, marker=marker)
        if not supervisor_state:
            # The supervisor is gone without a receipt: the supervised child
            # may have outlived it, and neither can be trusted for a terminal.
            child_state = self._child_liveness(paths, marker)
            if child_state is True:
                return {"status": "running", "paths": paths, "marker": marker}
            if child_state is None:
                return dict(self._unknown_envelope(), paths=paths, marker=marker)
            return dict(self._unknown_envelope(), paths=paths, marker=marker)

        child_present = paths.child.is_file()
        status = "running" if child_present else "pending"
        return {"status": status, "paths": paths, "marker": marker}

    def _supervisor_liveness(self, adapter_handle: str, paths: HermesRunPaths, marker: Mapping[str, Any]) -> bool | None:
        process = self._local_supervisors.get(adapter_handle)
        if process is not None:
            try:
                return process.poll() is None
            except Exception:
                pass
        return locator.process_matches_identity(
            marker.get("supervisor_pid"),
            expected_start_ticks=marker.get("supervisor_start_ticks"),
            cmdline_token=paths.run_id,
        )

    def _child_liveness(self, paths: HermesRunPaths, marker: Mapping[str, Any]) -> bool | None:
        try:
            record = locator.read_json_object(paths.child)
        except HermesLocatorError:
            return None
        if record is None:
            # The supervisor was proven dead before it recorded a child: the
            # run never started a Hermes process (or evidence was lost).
            return False
        try:
            record = locator.validate_child_record(record)
        except HermesLocatorError:
            return None
        return locator.process_matches_identity(
            record.get("child_pid"),
            expected_start_ticks=record.get("child_start_ticks"),
            cmdline_token=str(marker.get("launcher", "")),
        )

    # -- protocol surface -------------------------------------------------------

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        observed = self._resolve(adapter_handle)
        status = observed["status"]
        response: dict[str, Any] = {"status": status}
        if "error" in observed:
            response["error"] = observed["error"]
        if status == "failed" and observed.get("receipt") is not None:
            details, _ = self._read_output(observed["paths"].stderr)
            response["details"] = details
        else:
            response["details"] = ""
        return response

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        observed = self._resolve(adapter_handle)
        status = observed["status"]
        if status in {"running", "pending"}:
            return {"status": status}
        if status not in {"done", "failed", "timeout", "cancelled"}:
            response: dict[str, Any] = {"status": status}
            if "error" in observed:
                response["error"] = observed["error"]
            return response

        receipt = observed["receipt"]
        paths = observed["paths"]
        stdout_text, stdout_truncated = self._read_output(paths.stdout)
        stderr_text, stderr_truncated = self._read_output(paths.stderr)
        if receipt["stdout_truncated"] or stdout_truncated:
            stdout_text = _clip_with_marker(stdout_text, self.output_limit_bytes)
        if receipt["stderr_truncated"] or stderr_truncated:
            stderr_text = _clip_with_marker(stderr_text, self.output_limit_bytes)
        duration_ms = max(
            0,
            int((float(receipt["completed_at_wall"]) - float(receipt["started_at_wall"])) * 1000),
        )
        response = {
            "status": status,
            "exit_code": receipt["exit_code"],
            "stdout": stdout_text,
            "stderr": stderr_text,
            "execution_stats": {"duration_ms": duration_ms},
        }
        if status == "timeout":
            response["error"] = {"code": "EXECUTION_TIMEOUT", "message": "Hermes worker exceeded its bounded timeout"}
        elif status == "cancelled":
            response["error"] = {"code": "EXECUTION_CANCELLED", "message": "Hermes worker cancelled"}
        elif status == "failed":
            if receipt["read_failure"] and receipt["exit_code"] == 0:
                response["error"] = {
                    "code": "RESULT_UNAVAILABLE",
                    "message": "Hermes worker output capture failed before the result stream stabilized",
                }
            else:
                response["error"] = {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"}
        return response

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        observed = self._resolve(adapter_handle)
        status = observed["status"]
        if status not in {"pending", "running"}:
            response: dict[str, Any] = {"cancelled": False, "status": status}
            if "error" in observed:
                response["error"] = observed["error"]
            return response
        paths = observed["paths"]
        if not self._request_supervisor_cancel(adapter_handle, paths, observed["marker"]):
            return {"cancelled": False, "status": status}
        receipt = self._await_receipt(paths, CANCEL_RECEIPT_WAIT_SECONDS)
        if receipt is None:
            refreshed = self._resolve(adapter_handle)
            return {"cancelled": False, "status": refreshed["status"]}
        return {"cancelled": True, "status": locator.published_receipt_status(receipt)}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED", "message": "bounded Hermes oneshot has no resume session"}}

    # -- durable W2 seams consumed by the adapter layer / W3 -------------------

    def resolve_handle(self, adapter_handle: str) -> str | None:
        """Canonical task binding recovered from durable mechanical evidence.

        Lets a fresh adapter instance re-establish the handle<->task binding
        after a process restart WITHOUT inventing authority: the marker only
        echoes what the dispatching caller supplied at launch.
        """
        run_id = locator.run_id_from_adapter_handle(adapter_handle)
        if run_id is None:
            return None
        paths = HermesRunPaths(self.runtime_root, run_id)
        if not paths.run_dir.is_dir():
            return None
        marker = locator.validate_marker(locator.read_json_object(paths.marker) or {})
        task_id = marker.get("canonical_task_id")
        return task_id if isinstance(task_id, str) and task_id.strip() else None

    def execution_evidence(self, adapter_handle: str) -> Mapping[str, Any] | None:
        """Bounded executor-private evidence for W1/W3 recovery seams.

        Mechanical identity only — never AF canonical truth.  The Hermes
        session id, when Hermes itself persisted it (``--usage-file``), is
        recovered here so a later process can exact-session-reenter or audit
        lineage without the original Python objects.
        """
        run_id = locator.run_id_from_adapter_handle(adapter_handle)
        if run_id is None:
            return None
        paths = HermesRunPaths(self.runtime_root, run_id)
        if not paths.run_dir.is_dir():
            return None
        try:
            marker = locator.validate_marker(locator.read_json_object(paths.marker) or {})
            receipt = locator.read_receipt(paths)
            observed = self._resolve(adapter_handle)
        except HermesLocatorError as exc:
            return {"state": "corrupt", "error": {"code": exc.code, "message": exc.message}}
        evidence: dict[str, Any] = {
            "run_id": run_id,
            "adapter_handle": adapter_handle,
            "canonical_task_id": marker.get("canonical_task_id"),
            "state": observed["status"],
            "terminal": observed["status"] in {"done", "failed", "timeout", "cancelled"},
            "started_at_wall": marker.get("dispatched_at_wall"),
            "deadline_wall": marker.get("deadline_wall"),
            "session_id": _read_usage_session_id(paths),
        }
        if receipt is not None:
            evidence["exit_code"] = receipt["exit_code"]
            evidence["completed_at_wall"] = receipt["completed_at_wall"]
            evidence["read_failure"] = receipt["read_failure"]
        return evidence

    def query_execution_state(self, adapter_handle: str) -> str:
        """Durable status projection without process-local accelerators."""
        return str(self._resolve(adapter_handle)["status"])

    # -- helpers ----------------------------------------------------------------

    def _read_output(self, path: Path) -> tuple[str, bool]:
        try:
            return locator.read_bounded_text(path, self.output_limit_bytes)
        except HermesLocatorError:
            return "", True

    def _request_supervisor_cancel(self, adapter_handle: str, paths: HermesRunPaths, marker: Mapping[str, Any]) -> bool:
        process = self._local_supervisors.get(adapter_handle)
        if process is not None:
            try:
                process.terminate()
                return True
            except Exception:
                pass
        alive = locator.process_matches_identity(
            marker.get("supervisor_pid"),
            expected_start_ticks=marker.get("supervisor_start_ticks"),
            cmdline_token=paths.run_id,
        )
        if alive is not True:
            return False
        try:
            os.kill(int(marker["supervisor_pid"]), signal.SIGTERM)
            return True
        except OSError:
            return False

    @staticmethod
    def _await_receipt(paths: HermesRunPaths, budget: float) -> Mapping[str, Any] | None:
        deadline = time.monotonic() + budget
        while True:
            try:
                receipt = locator.read_receipt(paths)
            except HermesLocatorError:
                return None
            if receipt is not None:
                return receipt
            if time.monotonic() >= deadline:
                return None
            time.sleep(RECEIPT_POLL_SECONDS)

    def close(self) -> None:
        """Detach local observation only.

        Deliberate W2 boundary change: close() must NOT kill durable Workers.
        An AF process disappearing while a supervised Hermes Worker continues
        running is the supported crash case (R8), not a cancellation request.
        """
        with self._state_lock:
            self._local_supervisors.clear()


def _clip_with_marker(text: str, limit: int) -> str:
    if limit >= len(_TRUNCATION_MARKER.encode("utf-8", errors="replace")):
        budget = max(0, limit - len(_TRUNCATION_MARKER))
        clipped = text.encode("utf-8", errors="replace")[:budget].decode("utf-8", errors="replace")
        return clipped + _TRUNCATION_MARKER
    return text[:limit]


def _read_usage_session_id(paths: HermesRunPaths) -> str | None:
    """Recover the Hermes-minted session id from Hermes' own durable report.

    Read-only bounded JSON; malformed or oversized evidence yields None
    (unknown), never a guess.
    """
    try:
        size = paths.usage.stat().st_size
    except OSError:
        return None
    if size <= 0 or size > _USAGE_FILE_MAX_BYTES:
        return None
    try:
        report = locator.read_json_object(paths.usage)
    except HermesLocatorError:
        return None
    if not report:
        return None
    value = report.get("session_id")
    if isinstance(value, str) and value.strip() and len(value) <= 128:
        return value
    return None


def _terminate_supervisor(process: Any) -> None:
    try:
        process.terminate()
    except Exception:
        pass


def _cleanup_run(paths: HermesRunPaths) -> None:
    shutil.rmtree(paths.run_dir, ignore_errors=True)


__all__ = [
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_RETENTION_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "HermesHostClient",
    "HermesHostClientError",
]
