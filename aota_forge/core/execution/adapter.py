"""Abstract ExecutorAdapter interface and mechanical support models (M5-1).

Defines the abstract typed boundary that all executor adapters (ReferenceFake, Hermes, etc.)
must implement. Pure mechanical boundary: no arbitrary shell/command execution, no semantic
retry, no heuristic ranking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState, parse_state


@dataclass(frozen=True)
class ValidationResult:
    """Result of package validation by an adapter."""

    valid: bool
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError(f"valid must be a bool, got {type(self.valid).__name__}")
        if not isinstance(self.errors, (tuple, list)):
            raise TypeError(f"errors must be a tuple or list of strings, got {type(self.errors).__name__}")
        for err in self.errors:
            if not isinstance(err, str):
                raise TypeError(f"errors members must be strings, got {type(err).__name__}")
        object.__setattr__(self, "errors", tuple(self.errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ValidationResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            valid=bool(data["valid"]),
            errors=tuple(data.get("errors", ())),
        )


@dataclass(frozen=True)
class DispatchResult:
    """Result of dispatching an execution package to an executor adapter."""

    canonical_task_id: str
    adapter_handle: str
    initial_state: CanonicalTaskState
    dispatch_time: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(self.adapter_handle, str) or not self.adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")
        state_val = parse_state(self.initial_state)
        object.__setattr__(self, "initial_state", state_val)
        if not isinstance(self.dispatch_time, str) or not self.dispatch_time.strip():
            raise ValueError("dispatch_time must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_task_id": self.canonical_task_id,
            "adapter_handle": self.adapter_handle,
            "initial_state": self.initial_state.value,
            "dispatch_time": self.dispatch_time,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DispatchResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            canonical_task_id=str(data["canonical_task_id"]),
            adapter_handle=str(data["adapter_handle"]),
            initial_state=parse_state(data["initial_state"]),
            dispatch_time=str(data["dispatch_time"]),
        )


@dataclass(frozen=True)
class TaskStatusResult:
    """Current status of an active or terminated task."""

    canonical_task_id: str
    state: CanonicalTaskState
    progress: dict = field(default_factory=dict)
    details: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        state_val = parse_state(self.state)
        object.__setattr__(self, "state", state_val)
        if not isinstance(self.progress, Mapping):
            raise TypeError(f"progress must be a dict/mapping, got {type(self.progress).__name__}")
        object.__setattr__(self, "progress", canonicalize(self.progress, path="progress"))
        if not isinstance(self.details, str):
            raise TypeError(f"details must be a string, got {type(self.details).__name__}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_task_id": self.canonical_task_id,
            "state": self.state.value,
            "progress": self.progress,
            "details": self.details,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskStatusResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            canonical_task_id=str(data["canonical_task_id"]),
            state=parse_state(data["state"]),
            progress=dict(data.get("progress", {})),
            details=str(data.get("details", "")),
        )


@dataclass(frozen=True)
class CancelResult:
    """Result of requesting task cancellation."""

    canonical_task_id: str
    cancelled: bool
    state: CanonicalTaskState

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if type(self.cancelled) is not bool:
            raise TypeError(f"cancelled must be a bool, got {type(self.cancelled).__name__}")
        state_val = parse_state(self.state)
        object.__setattr__(self, "state", state_val)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_task_id": self.canonical_task_id,
            "cancelled": self.cancelled,
            "state": self.state.value,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CancelResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            canonical_task_id=str(data["canonical_task_id"]),
            cancelled=bool(data["cancelled"]),
            state=parse_state(data["state"]),
        )


@dataclass(frozen=True)
class ResumeResult:
    """Result of resuming a waiting task with new input."""

    canonical_task_id: str
    state: CanonicalTaskState

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        state_val = parse_state(self.state)
        object.__setattr__(self, "state", state_val)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_task_id": self.canonical_task_id,
            "state": self.state.value,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResumeResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            canonical_task_id=str(data["canonical_task_id"]),
            state=parse_state(data["state"]),
        )


class ExecutorAdapter(ABC):
    """Abstract base class for all executor adapters."""

    @abstractmethod
    def capabilities(self) -> ExecutorCapabilities:
        """Return static advertised capabilities of this executor adapter."""
        raise NotImplementedError

    @abstractmethod
    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        """Pure read check whether package can be executed by this adapter."""
        raise NotImplementedError

    @abstractmethod
    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        """Dispatch execution package to executor host."""
        raise NotImplementedError

    @abstractmethod
    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        """Query execution state of dispatched task."""
        raise NotImplementedError

    @abstractmethod
    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        """Fetch terminal CanonicalResult for task."""
        raise NotImplementedError

    @abstractmethod
    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        """Request cancellation of active task."""
        raise NotImplementedError

    @abstractmethod
    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        """Resume waiting or suspended task with new input."""
        raise NotImplementedError
