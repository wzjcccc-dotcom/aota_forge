"""ExecutorCapabilities contract definition (M5-1).

Executor-neutral capability descriptor. Strictly rejects semantic decision fields
(preferred_executor, best_role, priority, routing scores).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.roles import validate_canonical_role

FORBIDDEN_SEMANTIC_FIELDS: frozenset[str] = frozenset({
    "preferred_executor",
    "best_role",
    "task_priority_recommendation",
    "semantic_routing_score",
    "heuristic_quality_rating",
})

ALLOWED_EXECUTION_MODES: frozenset[str] = frozenset({"sync", "async", "batch"})
ALLOWED_ISOLATION_MODES: frozenset[str] = frozenset({"worktree", "process", "container", "none"})


@dataclass(frozen=True)
class ExecutorCapabilities:
    """Frozen, deterministic descriptor of an executor's advertised capabilities.

    Contains no runtime callbacks, no heuristic ranking, and no semantic authority.
    """

    executor_id: str
    adapter_kind: str
    supported_execution_modes: tuple[str, ...]
    supports_streaming_events: bool
    supports_task_cancellation: bool
    supports_task_resume: bool
    supports_structured_result: bool
    supported_canonical_roles: tuple[str, ...]
    supported_isolation_modes: tuple[str, ...]
    supports_working_directory: bool
    supports_artifact_transport: bool
    max_timeout_seconds: int | None = None
    concurrency_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")
        if not isinstance(self.adapter_kind, str) or not self.adapter_kind.strip():
            raise ValueError("adapter_kind must be a non-empty string")

        # Validate bool fields strictly (must be bool, not just truthy/int)
        bool_fields = (
            ("supports_streaming_events", self.supports_streaming_events),
            ("supports_task_cancellation", self.supports_task_cancellation),
            ("supports_task_resume", self.supports_task_resume),
            ("supports_structured_result", self.supports_structured_result),
            ("supports_working_directory", self.supports_working_directory),
            ("supports_artifact_transport", self.supports_artifact_transport),
        )
        for name, val in bool_fields:
            if type(val) is not bool:
                raise TypeError(f"{name} must be a bool, got {type(val).__name__}")

        # Validate supported_execution_modes
        if not isinstance(self.supported_execution_modes, (tuple, list)) or len(self.supported_execution_modes) == 0:
            raise ValueError("supported_execution_modes must be a non-empty sequence of strings")
        for mode in self.supported_execution_modes:
            if not isinstance(mode, str):
                raise TypeError(
                    "supported_execution_modes members must be strings, "
                    f"got {type(mode).__name__}"
                )
            if mode not in ALLOWED_EXECUTION_MODES:
                raise ValueError(
                    f"supported_execution_modes contains non-canonical value {mode!r}; "
                    f"must be one of {sorted(ALLOWED_EXECUTION_MODES)}"
                )
        object.__setattr__(self, "supported_execution_modes", tuple(sorted(set(self.supported_execution_modes))))

        # Validate supported_canonical_roles
        if not isinstance(self.supported_canonical_roles, (tuple, list)) or len(self.supported_canonical_roles) == 0:
            raise ValueError("supported_canonical_roles must be a non-empty sequence of valid canonical roles")
        for role in self.supported_canonical_roles:
            validate_canonical_role(role)
        object.__setattr__(self, "supported_canonical_roles", tuple(sorted(set(self.supported_canonical_roles))))

        # Validate supported_isolation_modes
        if not isinstance(self.supported_isolation_modes, (tuple, list)) or len(self.supported_isolation_modes) == 0:
            raise ValueError("supported_isolation_modes must be a non-empty sequence of strings")
        for mode in self.supported_isolation_modes:
            if not isinstance(mode, str):
                raise TypeError(
                    "supported_isolation_modes members must be strings, "
                    f"got {type(mode).__name__}"
                )
            if mode not in ALLOWED_ISOLATION_MODES:
                raise ValueError(
                    f"supported_isolation_modes contains non-canonical value {mode!r}; "
                    f"must be one of {sorted(ALLOWED_ISOLATION_MODES)}"
                )
        object.__setattr__(self, "supported_isolation_modes", tuple(sorted(set(self.supported_isolation_modes))))

        # Validate optional ints
        if self.max_timeout_seconds is not None:
            if type(self.max_timeout_seconds) is not int or self.max_timeout_seconds <= 0:
                raise ValueError("max_timeout_seconds must be a positive integer if specified")

        if self.concurrency_limit is not None:
            if type(self.concurrency_limit) is not int or self.concurrency_limit <= 0:
                raise ValueError("concurrency_limit must be a positive integer if specified")

    def supports_mode(self, mode: str) -> bool:
        return mode in self.supported_execution_modes

    def supports_role(self, role: str) -> bool:
        return role in self.supported_canonical_roles

    def supports_isolation(self, isolation: str) -> bool:
        return isolation in self.supported_isolation_modes

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "adapter_kind": self.adapter_kind,
            "supported_execution_modes": list(self.supported_execution_modes),
            "supports_streaming_events": self.supports_streaming_events,
            "supports_task_cancellation": self.supports_task_cancellation,
            "supports_task_resume": self.supports_task_resume,
            "supports_structured_result": self.supports_structured_result,
            "supported_canonical_roles": list(self.supported_canonical_roles),
            "supported_isolation_modes": list(self.supported_isolation_modes),
            "supports_working_directory": self.supports_working_directory,
            "supports_artifact_transport": self.supports_artifact_transport,
            "max_timeout_seconds": self.max_timeout_seconds,
            "concurrency_limit": self.concurrency_limit,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutorCapabilities:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")

        # Fail closed on any forbidden semantic fields
        for field in FORBIDDEN_SEMANTIC_FIELDS:
            if field in data:
                raise ValueError(f"Forbidden semantic decision field rejected: {field!r}")

        # Check required fields
        required = (
            "executor_id",
            "adapter_kind",
            "supported_execution_modes",
            "supports_streaming_events",
            "supports_task_cancellation",
            "supports_task_resume",
            "supports_structured_result",
            "supported_canonical_roles",
            "supported_isolation_modes",
            "supports_working_directory",
            "supports_artifact_transport",
        )
        for req in required:
            if req not in data:
                raise ValueError(f"Missing required field in ExecutorCapabilities: {req!r}")

        return cls(
            executor_id=str(data["executor_id"]),
            adapter_kind=str(data["adapter_kind"]),
            supported_execution_modes=tuple(data["supported_execution_modes"]),
            supports_streaming_events=bool(data["supports_streaming_events"]),
            supports_task_cancellation=bool(data["supports_task_cancellation"]),
            supports_task_resume=bool(data["supports_task_resume"]),
            supports_structured_result=bool(data["supports_structured_result"]),
            supported_canonical_roles=tuple(data["supported_canonical_roles"]),
            supported_isolation_modes=tuple(data["supported_isolation_modes"]),
            supports_working_directory=bool(data["supports_working_directory"]),
            supports_artifact_transport=bool(data["supports_artifact_transport"]),
            max_timeout_seconds=data.get("max_timeout_seconds"),
            concurrency_limit=data.get("concurrency_limit"),
        )
