"""CanonicalResult contract definition and structured outcome categories (M5-1).

Defines canonical execution outcome envelope with strict category consistency:
EXECUTION_SUCCESS, EXECUTION_FAILURE, DISPATCH_REJECTED, EXECUTION_TIMEOUT,
EXECUTION_CANCELLED, UNKNOWN_EXECUTOR_STATE.
Prohibits generic failure string collapse and Hermes-private objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.state import (
    CanonicalTaskState,
    parse_state,
)

ALLOWED_RESULT_STATUSES: frozenset[str] = frozenset({
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "unknown",
})

FORBIDDEN_HERMES_RESULT_KEYS: frozenset[str] = frozenset({
    "hermes_session",
    "hermes_task",
    "hermes_trace",
    "hermes_worker",
    "hermes_profile",
})


@dataclass(frozen=True)
class CanonicalResult:
    """Frozen canonical execution result envelope."""

    ok: bool
    status: str
    canonical_task_id: str
    executor_id: str
    canonical_task_state: str
    exit_code: int | None
    result_data: dict
    output_artifacts: tuple[dict, ...]
    stdout_summary: str | None
    stderr_summary: str | None
    error: dict | None
    execution_stats: dict
    correlation_id: str

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError(f"ok must be a bool, got {type(self.ok).__name__}")

        if not isinstance(self.status, str) or self.status not in ALLOWED_RESULT_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALLOWED_RESULT_STATUSES)}, got {self.status!r}")

        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")

        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")

        # Validate task state
        state_enum = parse_state(self.canonical_task_state)
        object.__setattr__(self, "canonical_task_state", state_enum.value)

        # Validate exit code if present
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise TypeError(f"exit_code must be int or None, got {type(self.exit_code).__name__}")

        # Validate result_data
        if not isinstance(self.result_data, Mapping):
            raise TypeError(f"result_data must be a dict/mapping, got {type(self.result_data).__name__}")
        object.__setattr__(self, "result_data", canonicalize(self.result_data, path="result_data"))

        # Validate output_artifacts
        if not isinstance(self.output_artifacts, (tuple, list)):
            raise TypeError(f"output_artifacts must be a tuple or list, got {type(self.output_artifacts).__name__}")
        for idx, item in enumerate(self.output_artifacts):
            if not isinstance(item, Mapping):
                raise TypeError(f"output_artifacts[{idx}] must be a dict, got {type(item).__name__}")
        normalized_artifacts = tuple(canonicalize(item, path=f"output_artifacts[{i}]") for i, item in enumerate(self.output_artifacts))
        object.__setattr__(self, "output_artifacts", normalized_artifacts)

        # Summaries
        if self.stdout_summary is not None and not isinstance(self.stdout_summary, str):
            raise TypeError(f"stdout_summary must be str or None, got {type(self.stdout_summary).__name__}")
        if self.stderr_summary is not None and not isinstance(self.stderr_summary, str):
            raise TypeError(f"stderr_summary must be str or None, got {type(self.stderr_summary).__name__}")

        # Validate execution_stats
        if not isinstance(self.execution_stats, Mapping):
            raise TypeError(f"execution_stats must be a dict/mapping, got {type(self.execution_stats).__name__}")
        object.__setattr__(self, "execution_stats", canonicalize(self.execution_stats, path="execution_stats"))

        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError("correlation_id must be a non-empty string")

        # Validate structured error envelope if present
        if self.error is not None:
            if not isinstance(self.error, Mapping):
                raise TypeError(f"error must be a dict envelope, got {type(self.error).__name__}")
            if "code" not in self.error or not isinstance(self.error["code"], str):
                raise ValueError("error envelope must contain a string code")
            if "message" not in self.error or not isinstance(self.error["message"], str):
                raise ValueError("error envelope must contain a string message")
            object.__setattr__(self, "error", canonicalize(self.error, path="error"))

        # Category and consistency invariants:
        if self.ok is True:
            if self.status != "completed":
                raise ValueError(f"ok=True requires status='completed', got {self.status!r}")
            if self.canonical_task_state != CanonicalTaskState.COMPLETED.value:
                raise ValueError(f"ok=True requires canonical_task_state='COMPLETED', got {self.canonical_task_state!r}")
            if self.error is not None:
                raise ValueError("ok=True must have error=None")
        else:
            # ok is False
            if self.status == "completed":
                raise ValueError("ok=False cannot have status='completed'")
            if self.error is None:
                raise ValueError("ok=False requires a structured error envelope")

        if self.status == "unknown" or self.canonical_task_state == CanonicalTaskState.UNKNOWN.value:
            if self.ok is True:
                raise ValueError("UNKNOWN task state cannot have ok=True")

        if self.status == "cancelled" or self.canonical_task_state == CanonicalTaskState.CANCELLED.value:
            if self.ok is True:
                raise ValueError("CANCELLED task state cannot have ok=True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "canonical_task_id": self.canonical_task_id,
            "executor_id": self.executor_id,
            "canonical_task_state": self.canonical_task_state,
            "exit_code": self.exit_code,
            "result_data": self.result_data,
            "output_artifacts": list(self.output_artifacts),
            "stdout_summary": self.stdout_summary,
            "stderr_summary": self.stderr_summary,
            "error": self.error,
            "execution_stats": self.execution_stats,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CanonicalResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")

        # Check for forbidden Hermes keys
        for key in FORBIDDEN_HERMES_RESULT_KEYS:
            if key in data:
                raise ValueError(f"Forbidden Hermes-private object in result: {key!r}")

        required = (
            "ok",
            "status",
            "canonical_task_id",
            "executor_id",
            "canonical_task_state",
            "result_data",
            "output_artifacts",
            "execution_stats",
            "correlation_id",
        )
        for req in required:
            if req not in data:
                raise ValueError(f"Missing required field in CanonicalResult: {req!r}")

        return cls(
            ok=bool(data["ok"]),
            status=str(data["status"]),
            canonical_task_id=str(data["canonical_task_id"]),
            executor_id=str(data["executor_id"]),
            canonical_task_state=str(data["canonical_task_state"]),
            exit_code=data.get("exit_code"),
            result_data=dict(data["result_data"]),
            output_artifacts=tuple(data["output_artifacts"]),
            stdout_summary=data.get("stdout_summary"),
            stderr_summary=data.get("stderr_summary"),
            error=dict(data["error"]) if data.get("error") is not None else None,
            execution_stats=dict(data["execution_stats"]),
            correlation_id=str(data["correlation_id"]),
        )

    @classmethod
    def success(
        cls,
        canonical_task_id: str,
        executor_id: str,
        result_data: dict[str, Any] | None = None,
        output_artifacts: tuple[dict, ...] | list[dict] = (),
        exit_code: int | None = 0,
        stdout_summary: str | None = None,
        stderr_summary: str | None = None,
        execution_stats: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> CanonicalResult:
        return cls(
            ok=True,
            status="completed",
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            canonical_task_state=CanonicalTaskState.COMPLETED.value,
            exit_code=exit_code,
            result_data=result_data if result_data is not None else {},
            output_artifacts=tuple(output_artifacts),
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            error=None,
            execution_stats=execution_stats if execution_stats is not None else {},
            correlation_id=correlation_id,
        )

    @classmethod
    def failure(
        cls,
        canonical_task_id: str,
        executor_id: str,
        error_code: str = "EXECUTION_FAILED",
        error_message: str = "Execution failed",
        retryable: bool = False,
        details: Any = None,
        exit_code: int | None = 1,
        result_data: dict[str, Any] | None = None,
        output_artifacts: tuple[dict, ...] | list[dict] = (),
        stdout_summary: str | None = None,
        stderr_summary: str | None = None,
        execution_stats: dict[str, Any] | None = None,
        correlation_id: str = "",
        status: str = "failed",
        canonical_task_state: str = CanonicalTaskState.FAILED.value,
    ) -> CanonicalResult:
        error_env: dict[str, Any] = {
            "code": error_code,
            "message": error_message,
            "retryable": retryable,
        }
        if details is not None:
            error_env["details"] = details
        return cls(
            ok=False,
            status=status,
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            canonical_task_state=canonical_task_state,
            exit_code=exit_code,
            result_data=result_data if result_data is not None else {},
            output_artifacts=tuple(output_artifacts),
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            error=error_env,
            execution_stats=execution_stats if execution_stats is not None else {},
            correlation_id=correlation_id,
        )

    @classmethod
    def rejected(
        cls,
        canonical_task_id: str,
        executor_id: str,
        error_code: str = "DISPATCH_REJECTED",
        error_message: str = "Dispatch rejected",
        details: Any = None,
        correlation_id: str = "",
    ) -> CanonicalResult:
        return cls.failure(
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            error_code=error_code,
            error_message=error_message,
            retryable=False,
            details=details,
            exit_code=None,
            status="rejected",
            canonical_task_state=CanonicalTaskState.FAILED.value,
            correlation_id=correlation_id,
        )

    @classmethod
    def timeout(
        cls,
        canonical_task_id: str,
        executor_id: str,
        error_message: str = "Execution timed out",
        correlation_id: str = "",
        execution_stats: dict[str, Any] | None = None,
    ) -> CanonicalResult:
        return cls.failure(
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            error_code="EXECUTION_TIMEOUT",
            error_message=error_message,
            retryable=False,
            exit_code=None,
            status="failed",
            canonical_task_state=CanonicalTaskState.FAILED.value,
            execution_stats=execution_stats,
            correlation_id=correlation_id,
        )

    @classmethod
    def cancelled(
        cls,
        canonical_task_id: str,
        executor_id: str,
        error_message: str = "Execution cancelled",
        correlation_id: str = "",
    ) -> CanonicalResult:
        return cls.failure(
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            error_code="EXECUTION_CANCELLED",
            error_message=error_message,
            retryable=False,
            exit_code=None,
            status="cancelled",
            canonical_task_state=CanonicalTaskState.CANCELLED.value,
            correlation_id=correlation_id,
        )

    @classmethod
    def unknown(
        cls,
        canonical_task_id: str,
        executor_id: str,
        error_message: str = "Executor task state unknown",
        correlation_id: str = "",
    ) -> CanonicalResult:
        return cls.failure(
            canonical_task_id=canonical_task_id,
            executor_id=executor_id,
            error_code="TASK_STATE_UNKNOWN",
            error_message=error_message,
            retryable=True,
            exit_code=None,
            status="unknown",
            canonical_task_state=CanonicalTaskState.UNKNOWN.value,
            correlation_id=correlation_id,
        )
