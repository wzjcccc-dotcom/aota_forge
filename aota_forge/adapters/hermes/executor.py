"""Hermes Executor Adapter Translation Layer (M5-4).

Implements the mechanical translation layer between canonical execution contracts
(ExecutionPackage, CanonicalTaskState, CanonicalResult, ExecutorCapabilities) and
the Hermes agent host protocol.

Pure mechanical translation:
- canonical role -> Hermes profile deterministic mapping
- ExecutionPackage -> Hermes host dispatch envelope
- Hermes status -> CanonicalTaskState mapping
- Hermes output -> CanonicalResult mapping
- bounded mechanical host protocol interface (HermesHostClient)

Core Invariants:
- AOTA Forge supports Hermes; AOTA Forge Core does not require Hermes
- Canonical role != Hermes profile
- Hermes local task ID != canonical_task_id
- Unknown Hermes state maps to UNKNOWN (never false completion)
- Zero Hermes-private type escape into canonical objects
- No live Hermes daemon activation; 100% offline injectable host protocol
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import (
    FORBIDDEN_HERMES_RESULT_KEYS,
    CanonicalResult,
)
from aota_forge.core.execution.roles import (
    RoleMapping,
    RoleMappingNotFoundError,
    validate_canonical_role,
)
from aota_forge.core.execution.state import (
    CanonicalTaskState,
    parse_state,
)

HERMES_EXECUTOR_ID: str = "hermes"
HERMES_ADAPTER_KIND: str = "hermes_host_adapter"

# Deterministic canonical role -> Hermes profile mapping
HERMES_ROLE_MAPPING: dict[str, str] = {
    "coder": "coder_profile",
    "executor": "executor_profile",
    "planner": "planner_profile",
    "reviewer": "reviewer_profile",
    "steward": "steward_profile",
}

HERMES_ROLE_MAPPING_CONTRACT: RoleMapping = RoleMapping.create(
    HERMES_EXECUTOR_ID, HERMES_ROLE_MAPPING
)

# Frozen Hermes host status -> CanonicalTaskState mapping
HERMES_STATUS_MAP: dict[str, CanonicalTaskState] = {
    "init": CanonicalTaskState.CREATED,
    "pending": CanonicalTaskState.QUEUED,
    "running": CanonicalTaskState.RUNNING,
    "waiting_for_input": CanonicalTaskState.WAITING,
    "waiting_for_subagent": CanonicalTaskState.WAITING,
    "done": CanonicalTaskState.COMPLETED,
    "success": CanonicalTaskState.COMPLETED,
    "error": CanonicalTaskState.FAILED,
    "failed": CanonicalTaskState.FAILED,
    "aborted": CanonicalTaskState.CANCELLED,
    "cancelled": CanonicalTaskState.CANCELLED,
    "unreachable": CanonicalTaskState.UNKNOWN,
    "timeout": CanonicalTaskState.UNKNOWN,
}


class HermesAdapterError(Exception):
    """Base exception for Hermes adapter mechanical errors."""

    def __init__(self, message: str, code: str = "ADAPTER_PROTOCOL_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class HermesHostUnavailableError(HermesAdapterError):
    """Raised when the Hermes host client is not available or reachable."""

    def __init__(self, message: str = "Hermes host client is unavailable") -> None:
        super().__init__(message, code="EXECUTOR_UNAVAILABLE")


class HermesProtocolError(HermesAdapterError):
    """Raised on Hermes host protocol violations or malformed responses."""

    def __init__(self, message: str = "Hermes protocol violation") -> None:
        super().__init__(message, code="ADAPTER_PROTOCOL_ERROR")


class HermesDispatchRejectedError(HermesAdapterError):
    """Raised when task dispatch is rejected by Hermes host."""

    def __init__(self, message: str = "Task dispatch rejected") -> None:
        super().__init__(message, code="DISPATCH_REJECTED")


@runtime_checkable
class HermesHostClient(Protocol):
    """Bounded mechanical protocol for communication with the Hermes host.

    Describes ONLY the operations HermesAdapter requires. Tests inject a
    fake implementation conforming to this protocol.
    """

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Dispatch task envelope to Hermes host."""
        ...

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        """Query task execution status from Hermes host."""
        ...

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        """Fetch execution result envelope from Hermes host."""
        ...

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        """Request task cancellation on Hermes host."""
        ...

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Resume waiting task on Hermes host with input envelope."""
        ...


def canonical_role_to_hermes_profile(canonical_role: str) -> str:
    """Deterministic canonical role to Hermes profile translation.

    Fails closed with RoleMappingNotFoundError if the role is not mapped.
    No nearest-profile fallback, no semantic role choice.
    """
    validate_canonical_role(canonical_role)
    profile = HERMES_ROLE_MAPPING.get(canonical_role)
    if profile is None:
        raise RoleMappingNotFoundError(HERMES_EXECUTOR_ID, canonical_role)
    return profile


def canonical_to_hermes_payload(package: ExecutionPackage) -> dict[str, Any]:
    """Translate canonical ExecutionPackage into Hermes dispatch envelope.

    Deterministic projection:
    - profile: mapped Hermes profile
    - instruction: verbatim package instruction
    - context: deterministic dictionary (task id, project id, correlation id, working context)
    - artifacts: list of dicts
    - constraints: package.constraints
    - capability_requirements: package.capability_requirements

    Fails closed if role mapping is missing.
    Does NOT rewrite instructions, weaken constraints, or drop requirements.
    """
    if not isinstance(package, ExecutionPackage):
        raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")

    profile = canonical_role_to_hermes_profile(package.canonical_role)

    payload = {
        "profile": profile,
        "instruction": package.instruction,
        "context": {
            "canonical_task_id": package.canonical_task_id,
            "project_id": package.project_id,
            "subject_ref": package.subject_ref,
            "correlation_id": package.correlation_id,
            "idempotency_key": package.idempotency_key,
            "intent_fingerprint": package.intent_fingerprint,
            "working_context": dict(package.working_context),
        },
        "artifacts": [dict(a) for a in package.input_artifacts],
        "constraints": dict(package.constraints),
        "capability_requirements": dict(package.capability_requirements),
        "result_expectations": dict(package.result_expectations),
        "operation": package.operation,
        "package_id": package.package_id,
    }
    return canonicalize(payload, path="hermes_payload")


def hermes_status_to_canonical_state(status: str | None) -> CanonicalTaskState:
    """Map Hermes host status string to CanonicalTaskState.

    Unknown status always maps to CanonicalTaskState.UNKNOWN.
    Never produces false completion.
    """
    if not isinstance(status, str):
        return CanonicalTaskState.UNKNOWN
    normalized = status.strip().lower()
    return HERMES_STATUS_MAP.get(normalized, CanonicalTaskState.UNKNOWN)


def _clean_hermes_dict(d: Any) -> Any:
    """Recursively strip Hermes-private keys/objects to prevent type escape."""
    if isinstance(d, Mapping):
        cleaned: dict[str, Any] = {}
        for k, v in d.items():
            key_str = str(k).lower()
            if key_str in FORBIDDEN_HERMES_RESULT_KEYS or key_str.startswith("hermes_"):
                continue
            cleaned[str(k)] = _clean_hermes_dict(v)
        return cleaned
    elif isinstance(d, (list, tuple)):
        return [_clean_hermes_dict(x) for x in d]
    elif isinstance(d, (str, int, float, bool)) or d is None:
        return d
    else:
        return str(d)


def _clean_hermes_artifacts(artifacts: Any) -> list[dict[str, Any]]:
    """Clean artifacts list ensuring all items are dicts without Hermes private keys."""
    if not isinstance(artifacts, (list, tuple)):
        return []
    cleaned_list = []
    for item in artifacts:
        if isinstance(item, Mapping):
            cleaned_list.append(_clean_hermes_dict(item))
    return cleaned_list


def hermes_output_to_canonical_result(
    output: Mapping[str, Any],
    canonical_task_id: str,
    correlation_id: str = "",
) -> CanonicalResult:
    """Translate Hermes host output envelope into CanonicalResult.

    Deterministic extraction:
    - canonical_task_id
    - executor_id="hermes"
    - canonical_task_state
    - exit_code
    - result_data
    - output_artifacts
    - stdout_summary
    - stderr_summary
    - error (structured envelope)
    - execution_stats
    - correlation_id

    Strictly prevents Hermes private types/objects from escaping into the result.
    Rejects malformed output with RESULT_MALFORMED failure envelope.
    """
    cid = (
        str(output.get("correlation_id") if isinstance(output, Mapping) else None)
        or correlation_id
        or f"corr-{canonical_task_id}"
    )
    if not cid.strip():
        cid = f"corr-{canonical_task_id}"

    if not isinstance(output, Mapping):
        return CanonicalResult.failure(
            canonical_task_id=canonical_task_id,
            executor_id=HERMES_EXECUTOR_ID,
            error_code="RESULT_MALFORMED",
            error_message=f"Hermes output must be a mapping, got {type(output).__name__}",
            retryable=False,
            correlation_id=cid,
        )

    raw_status = output.get("status")
    state = hermes_status_to_canonical_state(raw_status)

    stats = _clean_hermes_dict(output.get("execution_stats") or output.get("stats", {}))
    if not isinstance(stats, dict):
        stats = {}

    stdout_val = output.get("stdout_summary") or output.get("stdout")
    stdout_summary = str(stdout_val) if stdout_val is not None else None

    stderr_val = output.get("stderr_summary") or output.get("stderr")
    stderr_summary = str(stderr_val) if stderr_val is not None else None

    # Handle completion (status = done/success)
    if state == CanonicalTaskState.COMPLETED:
        raw_exit = output.get("exit_code", 0)
        exit_code = raw_exit if isinstance(raw_exit, int) else 0
        raw_data = output.get("result_data", {})
        result_data = _clean_hermes_dict(raw_data) if isinstance(raw_data, Mapping) else {}
        raw_artifacts = output.get("output_artifacts") or output.get("artifacts", ())
        artifacts = _clean_hermes_artifacts(raw_artifacts)

        return CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id=HERMES_EXECUTOR_ID,
            result_data=result_data,
            output_artifacts=artifacts,
            exit_code=exit_code,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            execution_stats=stats,
            correlation_id=cid,
        )

    # Handle cancellation (status = aborted/cancelled)
    if state == CanonicalTaskState.CANCELLED:
        err_raw = output.get("error")
        err_msg = (
            err_raw.get("message")
            if isinstance(err_raw, Mapping)
            else (str(err_raw) if err_raw is not None else "Execution cancelled")
        )
        return CanonicalResult.cancelled(
            canonical_task_id=canonical_task_id,
            executor_id=HERMES_EXECUTOR_ID,
            error_message=str(err_msg),
            correlation_id=cid,
        )

    # Handle timeout
    if raw_status == "timeout" or (
        isinstance(output.get("error"), Mapping)
        and output.get("error", {}).get("code") == "EXECUTION_TIMEOUT"
    ):
        err_raw = output.get("error")
        err_msg = (
            err_raw.get("message")
            if isinstance(err_raw, Mapping)
            else (str(err_raw) if err_raw is not None else "Execution timed out")
        )
        return CanonicalResult.timeout(
            canonical_task_id=canonical_task_id,
            executor_id=HERMES_EXECUTOR_ID,
            error_message=str(err_msg),
            correlation_id=cid,
            execution_stats=stats,
        )

    # Handle unknown / unreachable
    if state == CanonicalTaskState.UNKNOWN:
        err_raw = output.get("error")
        err_msg = (
            err_raw.get("message")
            if isinstance(err_raw, Mapping)
            else (
                str(err_raw)
                if err_raw is not None
                else f"Hermes task state unknown (raw status: {raw_status!r})"
            )
        )
        return CanonicalResult.unknown(
            canonical_task_id=canonical_task_id,
            executor_id=HERMES_EXECUTOR_ID,
            error_message=str(err_msg),
            correlation_id=cid,
        )

    # Handle failure (status = error/failed or other non-terminal states treated as failure)
    err_raw = output.get("error")
    if isinstance(err_raw, Mapping):
        err_code = str(err_raw.get("code", "EXECUTION_FAILED"))
        err_msg = str(err_raw.get("message", "Execution failed"))
        retryable = bool(err_raw.get("retryable", False))
        details = _clean_hermes_dict(err_raw.get("details")) if err_raw.get("details") is not None else None
    elif isinstance(err_raw, str) and err_raw.strip():
        err_code = "EXECUTION_FAILED"
        err_msg = err_raw.strip()
        retryable = False
        details = None
    else:
        err_code = "EXECUTION_FAILED"
        err_msg = "Execution failed on Hermes host"
        retryable = False
        details = None

    raw_exit = output.get("exit_code", 1)
    exit_code = raw_exit if isinstance(raw_exit, int) else 1
    raw_data = output.get("result_data", {})
    result_data = _clean_hermes_dict(raw_data) if isinstance(raw_data, Mapping) else {}
    raw_artifacts = output.get("output_artifacts") or output.get("artifacts", ())
    artifacts = _clean_hermes_artifacts(raw_artifacts)

    return CanonicalResult.failure(
        canonical_task_id=canonical_task_id,
        executor_id=HERMES_EXECUTOR_ID,
        error_code=err_code,
        error_message=err_msg,
        retryable=retryable,
        details=details,
        exit_code=exit_code,
        result_data=result_data,
        output_artifacts=artifacts,
        stdout_summary=stdout_summary,
        stderr_summary=stderr_summary,
        execution_stats=stats,
        correlation_id=cid,
        status="failed",
        canonical_task_state=CanonicalTaskState.FAILED.value,
    )


def default_hermes_capabilities() -> ExecutorCapabilities:
    """Construct static, deterministic advertised capabilities for Hermes adapter."""
    return ExecutorCapabilities(
        executor_id=HERMES_EXECUTOR_ID,
        adapter_kind=HERMES_ADAPTER_KIND,
        supported_execution_modes=("async", "sync"),
        supports_streaming_events=False,
        supports_task_cancellation=True,
        supports_task_resume=True,
        supports_structured_result=True,
        supported_canonical_roles=("coder", "executor", "planner", "reviewer", "steward"),
        supported_isolation_modes=("process", "worktree"),
        supports_working_directory=True,
        supports_artifact_transport=True,
        max_timeout_seconds=86400,
        concurrency_limit=16,
    )


class HermesAdapter(ExecutorAdapter):
    """Hermes execution host adapter (M5-4).

    Implements the ExecutorAdapter interface for the Hermes agent host protocol.
    Provides deterministic projection between canonical ExecutionPackage/CanonicalResult
    and Hermes protocol envelopes.
    Requires an injected host protocol client for offline execution and testing.
    """

    def __init__(
        self,
        host_client: HermesHostClient | None = None,
        capabilities: ExecutorCapabilities | None = None,
        role_mapping: RoleMapping | None = None,
    ) -> None:
        self._host_client = host_client
        self._capabilities = capabilities or default_hermes_capabilities()
        self._role_mapping = role_mapping or HERMES_ROLE_MAPPING_CONTRACT

    def capabilities(self) -> ExecutorCapabilities:
        """Return static advertised capabilities of the Hermes adapter."""
        return self._capabilities

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        """Pure read check whether package can be executed by Hermes adapter."""
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")

        errors: list[str] = []

        # 1. Check canonical role mapping
        if not self._role_mapping.has_role(package.canonical_role):
            errors.append(
                f"ROLE_MAPPING_NOT_FOUND: canonical role {package.canonical_role!r} "
                f"has no Hermes profile mapping"
            )

        # 2. Check execution mode
        req_mode = (
            package.capability_requirements.get("execution_mode")
            or package.constraints.get("execution_mode")
        )
        if req_mode is not None and not self._capabilities.supports_mode(str(req_mode)):
            errors.append(
                f"CAPABILITY_MISMATCH: execution mode {req_mode!r} not supported by Hermes adapter"
            )

        # 3. Check isolation mode
        req_iso = (
            package.capability_requirements.get("isolation")
            or package.capability_requirements.get("isolation_mode")
            or package.constraints.get("isolation")
        )
        if req_iso is not None and not self._capabilities.supports_isolation(str(req_iso)):
            errors.append(
                f"CAPABILITY_MISMATCH: isolation mode {req_iso!r} not supported by Hermes adapter"
            )

        # 4. Check timeout requirement
        req_timeout = (
            package.constraints.get("timeout_seconds")
            or package.capability_requirements.get("timeout_seconds")
        )
        if req_timeout is not None and self._capabilities.max_timeout_seconds is not None:
            if isinstance(req_timeout, (int, float)) and req_timeout > self._capabilities.max_timeout_seconds:
                errors.append(
                    f"CAPABILITY_MISMATCH: requested timeout {req_timeout}s exceeds "
                    f"Hermes max timeout {self._capabilities.max_timeout_seconds}s"
                )

        # 5. Check cancellation requirement
        if package.capability_requirements.get("requires_cancellation", False) and not self._capabilities.supports_task_cancellation:
            errors.append("CAPABILITY_MISMATCH: task requires cancellation but Hermes adapter does not support it")

        # 6. Check resume requirement / operation
        if package.operation == "task_resume" and not self._capabilities.supports_task_resume:
            errors.append("RESUME_UNSUPPORTED: task_resume requested but Hermes adapter does not support resume")
        if package.capability_requirements.get("requires_resume", False) and not self._capabilities.supports_task_resume:
            errors.append("CAPABILITY_MISMATCH: task requires resume but Hermes adapter does not support it")

        # 7. Check working directory requirement
        if package.capability_requirements.get("requires_working_directory", False) and not self._capabilities.supports_working_directory:
            errors.append("CAPABILITY_MISMATCH: task requires working directory support")

        # 8. Check artifact transport requirement
        if package.capability_requirements.get("requires_artifact_transport", False) and not self._capabilities.supports_artifact_transport:
            errors.append("CAPABILITY_MISMATCH: task requires artifact transport support")

        if len(errors) > 0:
            return ValidationResult(valid=False, errors=tuple(errors))
        return ValidationResult(valid=True, errors=())

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        """Dispatch execution package to Hermes host via injected host protocol."""
        val = self.validate_package(package)
        if not val.valid:
            raise HermesDispatchRejectedError(
                f"DISPATCH_REJECTED: package validation failed: {list(val.errors)}"
            )

        if self._host_client is None:
            raise HermesHostUnavailableError(
                "EXECUTOR_UNAVAILABLE: Hermes host client is not configured (offline/test injection required)"
            )

        payload = canonical_to_hermes_payload(package)

        try:
            host_resp = self._host_client.dispatch(payload)
        except HermesAdapterError:
            raise
        except Exception as exc:
            raise HermesAdapterError(f"ADAPTER_PROTOCOL_ERROR: host dispatch failed: {exc}") from exc

        if not isinstance(host_resp, Mapping):
            raise HermesProtocolError(
                f"ADAPTER_PROTOCOL_ERROR: host dispatch response must be mapping, got {type(host_resp).__name__}"
            )

        adapter_handle = host_resp.get("adapter_handle")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise HermesProtocolError(
                "ADAPTER_PROTOCOL_ERROR: host dispatch response missing valid 'adapter_handle'"
            )

        raw_status = host_resp.get("status", "pending")
        initial_state = hermes_status_to_canonical_state(raw_status)
        dispatch_time = str(host_resp.get("dispatch_time", "2026-08-21T00:00:00Z"))

        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=str(adapter_handle),
            initial_state=initial_state,
            dispatch_time=dispatch_time,
        )

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        """Query execution state of dispatched task on Hermes host."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")

        if self._host_client is None:
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details="EXECUTOR_UNAVAILABLE: Hermes host client not configured",
            )

        try:
            host_resp = self._host_client.query_status(adapter_handle)
        except Exception as exc:
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details=f"ADAPTER_PROTOCOL_ERROR: query_status failed: {exc}",
            )

        if not isinstance(host_resp, Mapping):
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details=f"RESULT_MALFORMED: status response not mapping: {type(host_resp).__name__}",
            )

        raw_status = host_resp.get("status")
        state = hermes_status_to_canonical_state(raw_status)
        raw_progress = host_resp.get("progress", {})
        progress = _clean_hermes_dict(raw_progress) if isinstance(raw_progress, Mapping) else {}
        details = str(host_resp.get("details", ""))

        return TaskStatusResult(
            canonical_task_id=canonical_task_id,
            state=state,
            progress=progress,
            details=details,
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        """Fetch terminal CanonicalResult for task from Hermes host."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")

        if self._host_client is None:
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=HERMES_EXECUTOR_ID,
                error_code="EXECUTOR_UNAVAILABLE",
                error_message="Hermes host client is not configured",
                retryable=True,
                correlation_id=f"corr-{canonical_task_id}",
            )

        try:
            host_resp = self._host_client.fetch_result(adapter_handle)
        except Exception as exc:
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=HERMES_EXECUTOR_ID,
                error_code="ADAPTER_PROTOCOL_ERROR",
                error_message=f"fetch_result failed: {exc}",
                retryable=False,
                correlation_id=f"corr-{canonical_task_id}",
            )

        if not isinstance(host_resp, Mapping):
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=HERMES_EXECUTOR_ID,
                error_code="RESULT_MALFORMED",
                error_message=f"Hermes result must be a mapping, got {type(host_resp).__name__}",
                retryable=False,
                correlation_id=f"corr-{canonical_task_id}",
            )

        return hermes_output_to_canonical_result(
            output=host_resp,
            canonical_task_id=canonical_task_id,
            correlation_id=str(host_resp.get("correlation_id") or f"corr-{canonical_task_id}"),
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        """Request cancellation of active task on Hermes host."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")

        if not self._capabilities.supports_task_cancellation:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )

        if self._host_client is None:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )

        try:
            host_resp = self._host_client.cancel_task(adapter_handle)
        except Exception:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )

        if not isinstance(host_resp, Mapping):
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )

        cancelled = bool(host_resp.get("cancelled", False))
        raw_status = host_resp.get("status", "cancelled" if cancelled else "running")
        state = hermes_status_to_canonical_state(raw_status)

        return CancelResult(
            canonical_task_id=canonical_task_id,
            cancelled=cancelled,
            state=state,
        )

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        """Resume waiting task with new input on Hermes host."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")

        if not self._capabilities.supports_task_resume:
            raise HermesAdapterError("RESUME_UNSUPPORTED: Hermes adapter does not support task resume", code="RESUME_UNSUPPORTED")

        val = self.validate_package(resume_package)
        if not val.valid:
            raise HermesDispatchRejectedError(
                f"DISPATCH_REJECTED: resume package validation failed: {list(val.errors)}"
            )

        if self._host_client is None:
            raise HermesHostUnavailableError("EXECUTOR_UNAVAILABLE: Hermes host client is not configured")

        payload = canonical_to_hermes_payload(resume_package)

        try:
            host_resp = self._host_client.resume_task(adapter_handle, payload)
        except HermesAdapterError:
            raise
        except Exception as exc:
            raise HermesAdapterError(f"ADAPTER_PROTOCOL_ERROR: resume_task failed: {exc}") from exc

        if not isinstance(host_resp, Mapping):
            raise HermesProtocolError("ADAPTER_PROTOCOL_ERROR: invalid host resume response")

        raw_status = host_resp.get("status", "running")
        state = hermes_status_to_canonical_state(raw_status)

        return ResumeResult(
            canonical_task_id=canonical_task_id,
            state=state,
        )
