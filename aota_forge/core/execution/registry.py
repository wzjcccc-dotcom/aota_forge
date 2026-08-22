"""ExecutorRegistry contract and deterministic mechanical capability matching (M5-3).

Registers executor adapters, produces deterministic sorted descriptors with zero handler leaks,
mechanically validates package capability requirements against advertised adapter capabilities,
and resolves executor selection under strict cardinality rules:
- Explicit executor_id: unknown -> EXECUTOR_NOT_FOUND; incompatible -> CAPABILITY_MISMATCH (no fallback); compatible -> RESOLVED.
- Capability-only matching: 0 -> EXECUTOR_NOT_FOUND; 1 -> RESOLVED; >1 -> NEEDS_SEMANTIC_CHOICE (no guessing, no heuristic ranking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.adapter import ExecutorAdapter
from aota_forge.core.execution.capabilities import (
    ALLOWED_EXECUTION_MODES,
    ALLOWED_ISOLATION_MODES,
    ExecutorCapabilities,
)
from aota_forge.core.execution.package import ExecutionPackage


KNOWN_CAPABILITY_REQUIREMENT_KEYS: frozenset[str] = frozenset(
    {
        "execution_mode",
        "isolation_mode",
        "isolation",
        "timeout_seconds",
        "requires_cancellation",
        "requires_resume",
        "requires_structured_result",
        "requires_streaming_events",
        "requires_working_directory",
        "requires_artifact_transport",
        "target_executor_id",
        "executor_id",
    }
)


class ResolutionOutcome(str, Enum):
    """Mechanical resolution outcome enum."""

    RESOLVED = "RESOLVED"
    EXECUTOR_NOT_FOUND = "EXECUTOR_NOT_FOUND"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    NEEDS_SEMANTIC_CHOICE = "NEEDS_SEMANTIC_CHOICE"


@dataclass(frozen=True)
class ExecutorDescriptor:
    """Frozen, serializable descriptor of a registered executor adapter.

    In-process memory keeps adapter objects; public descriptors contain only bounded mechanical
    metadata and capabilities. Strictly zero callable, bound method, memory address, or Hermes
    private object leaks.
    """

    executor_id: str
    adapter_kind: str
    capabilities: ExecutorCapabilities
    supported_canonical_roles: tuple[str, ...]
    supported_execution_modes: tuple[str, ...]
    supported_isolation_modes: tuple[str, ...]
    supports_streaming_events: bool
    supports_task_cancellation: bool
    supports_task_resume: bool
    supports_structured_result: bool
    supports_working_directory: bool
    supports_artifact_transport: bool
    max_timeout_seconds: int | None = None
    concurrency_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")
        if not isinstance(self.adapter_kind, str) or not self.adapter_kind.strip():
            raise ValueError("adapter_kind must be a non-empty string")
        if not isinstance(self.capabilities, ExecutorCapabilities):
            raise TypeError(
                f"capabilities must be ExecutorCapabilities, got {type(self.capabilities).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert descriptor to plain dict with zero handler leaks."""
        return {
            "executor_id": self.executor_id,
            "adapter_kind": self.adapter_kind,
            "capabilities": self.capabilities.to_dict(),
            "supported_canonical_roles": list(self.supported_canonical_roles),
            "supported_execution_modes": list(self.supported_execution_modes),
            "supported_isolation_modes": list(self.supported_isolation_modes),
            "supports_streaming_events": self.supports_streaming_events,
            "supports_task_cancellation": self.supports_task_cancellation,
            "supports_task_resume": self.supports_task_resume,
            "supports_structured_result": self.supports_structured_result,
            "supports_working_directory": self.supports_working_directory,
            "supports_artifact_transport": self.supports_artifact_transport,
            "max_timeout_seconds": self.max_timeout_seconds,
            "concurrency_limit": self.concurrency_limit,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_capabilities(cls, capabilities: ExecutorCapabilities) -> ExecutorDescriptor:
        if not isinstance(capabilities, ExecutorCapabilities):
            raise TypeError(
                f"capabilities must be ExecutorCapabilities, got {type(capabilities).__name__}"
            )
        return cls(
            executor_id=capabilities.executor_id,
            adapter_kind=capabilities.adapter_kind,
            capabilities=capabilities,
            supported_canonical_roles=capabilities.supported_canonical_roles,
            supported_execution_modes=capabilities.supported_execution_modes,
            supported_isolation_modes=capabilities.supported_isolation_modes,
            supports_streaming_events=capabilities.supports_streaming_events,
            supports_task_cancellation=capabilities.supports_task_cancellation,
            supports_task_resume=capabilities.supports_task_resume,
            supports_structured_result=capabilities.supports_structured_result,
            supports_working_directory=capabilities.supports_working_directory,
            supports_artifact_transport=capabilities.supports_artifact_transport,
            max_timeout_seconds=capabilities.max_timeout_seconds,
            concurrency_limit=capabilities.concurrency_limit,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutorDescriptor:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        caps = ExecutorCapabilities.from_dict(data["capabilities"])
        return cls(
            executor_id=str(data["executor_id"]),
            adapter_kind=str(data["adapter_kind"]),
            capabilities=caps,
            supported_canonical_roles=tuple(data.get("supported_canonical_roles", caps.supported_canonical_roles)),
            supported_execution_modes=tuple(data.get("supported_execution_modes", caps.supported_execution_modes)),
            supported_isolation_modes=tuple(data.get("supported_isolation_modes", caps.supported_isolation_modes)),
            supports_streaming_events=bool(data.get("supports_streaming_events", caps.supports_streaming_events)),
            supports_task_cancellation=bool(data.get("supports_task_cancellation", caps.supports_task_cancellation)),
            supports_task_resume=bool(data.get("supports_task_resume", caps.supports_task_resume)),
            supports_structured_result=bool(data.get("supports_structured_result", caps.supports_structured_result)),
            supports_working_directory=bool(data.get("supports_working_directory", caps.supports_working_directory)),
            supports_artifact_transport=bool(data.get("supports_artifact_transport", caps.supports_artifact_transport)),
            max_timeout_seconds=data.get("max_timeout_seconds", caps.max_timeout_seconds),
            concurrency_limit=data.get("concurrency_limit", caps.concurrency_limit),
        )


@dataclass(frozen=True)
class ExecutorResolution:
    """Typed resolution outcome representing deterministic matching result."""

    outcome: ResolutionOutcome
    selected_executor_id: str | None = None
    candidate_executor_ids: tuple[str, ...] = ()
    mismatch_reasons: tuple[str, ...] = ()
    details: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ResolutionOutcome):
            outcome_val = ResolutionOutcome(str(self.outcome))
            object.__setattr__(self, "outcome", outcome_val)
        if not isinstance(self.candidate_executor_ids, (tuple, list)):
            raise TypeError(
                f"candidate_executor_ids must be a sequence of strings, got {type(self.candidate_executor_ids).__name__}"
            )
        # Deterministically sort candidates
        sorted_candidates = tuple(sorted(set(str(c) for c in self.candidate_executor_ids)))
        object.__setattr__(self, "candidate_executor_ids", sorted_candidates)
        if not isinstance(self.mismatch_reasons, (tuple, list)):
            raise TypeError(
                f"mismatch_reasons must be a sequence of strings, got {type(self.mismatch_reasons).__name__}"
            )
        object.__setattr__(self, "mismatch_reasons", tuple(str(r) for r in self.mismatch_reasons))
        if not isinstance(self.details, str):
            raise TypeError(f"details must be a string, got {type(self.details).__name__}")

    @property
    def is_resolved(self) -> bool:
        return self.outcome == ResolutionOutcome.RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_executor_id": self.selected_executor_id,
            "candidate_executor_ids": list(self.candidate_executor_ids),
            "mismatch_reasons": list(self.mismatch_reasons),
            "details": self.details,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


class ExecutorRegistryError(Exception):
    """Base exception for executor registry errors."""


class DuplicateExecutorError(ExecutorRegistryError, ValueError):
    """Raised when registering an adapter whose executor_id is already registered."""

    def __init__(self, executor_id: str) -> None:
        self.executor_id = executor_id
        super().__init__(
            f"Duplicate executor registration rejected: executor_id {executor_id!r} is already registered"
        )


class ExecutorNotFoundError(ExecutorRegistryError, KeyError):
    """Raised when an exact executor lookup fails."""

    def __init__(self, executor_id: str) -> None:
        self.executor_id = executor_id
        super().__init__(f"Executor {executor_id!r} is not registered in ExecutorRegistry")


class CapabilityMismatchError(ExecutorRegistryError, ValueError):
    """Raised when an executor does not satisfy package capability requirements."""

    def __init__(self, executor_id: str, reasons: Sequence[str]) -> None:
        self.executor_id = executor_id
        self.reasons = tuple(reasons)
        super().__init__(
            f"Executor {executor_id!r} capability mismatch: {'; '.join(self.reasons)}"
        )


class AmbiguousExecutorError(ExecutorRegistryError, ValueError):
    """Raised when multiple executors match without explicit semantic choice."""

    def __init__(self, candidates: Sequence[str]) -> None:
        self.candidates = tuple(sorted(candidates))
        super().__init__(
            f"Ambiguous executor match: multiple matching executors {list(self.candidates)}; explicit semantic choice required"
        )


class ExecutorRegistry:
    """In-memory mechanical registry of ExecutorAdapter instances.

    Responsible only for adapter registration, lookup, capability validation, and deterministic
    cardinality resolution. Possesses no semantic choice authority, no dispatch logic, no retry,
    and no Hermes dependency.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, ExecutorAdapter] = {}

    def register(self, adapter: ExecutorAdapter) -> None:
        """Register an executor adapter by its unique capabilities().executor_id.

        Fails closed on duplicate executor_id (both same object and new object).
        """
        if not isinstance(adapter, ExecutorAdapter):
            raise TypeError(
                f"adapter must implement ExecutorAdapter, got {type(adapter).__name__}"
            )
        caps = adapter.capabilities()
        if not isinstance(caps, ExecutorCapabilities):
            raise TypeError(
                f"adapter.capabilities() must return ExecutorCapabilities, got {type(caps).__name__}"
            )
        executor_id = caps.executor_id
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("adapter.capabilities().executor_id must be a non-empty string")

        if executor_id in self._adapters:
            raise DuplicateExecutorError(executor_id)

        self._adapters[executor_id] = adapter

    def unregister(self, executor_id: str) -> None:
        """Unregister an adapter by executor_id."""
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")
        if executor_id not in self._adapters:
            raise ExecutorNotFoundError(executor_id)
        del self._adapters[executor_id]

    def get(self, executor_id: str) -> ExecutorAdapter:
        """Retrieve the adapter instance for an executor_id."""
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")
        if executor_id not in self._adapters:
            raise ExecutorNotFoundError(executor_id)
        return self._adapters[executor_id]

    def has(self, executor_id: str) -> bool:
        """Check whether an executor_id is registered."""
        if not isinstance(executor_id, str):
            return False
        return executor_id in self._adapters

    def get_capabilities(self, executor_id: str) -> ExecutorCapabilities:
        """Get advertised capabilities for a registered executor."""
        return self.get(executor_id).capabilities()

    def get_descriptor(self, executor_id: str) -> ExecutorDescriptor:
        """Get public serializable descriptor for a registered executor."""
        caps = self.get_capabilities(executor_id)
        return ExecutorDescriptor.from_capabilities(caps)

    def list_descriptors(self) -> list[ExecutorDescriptor]:
        """List descriptors sorted deterministically by executor_id."""
        sorted_ids = sorted(self._adapters.keys())
        return [self.get_descriptor(eid) for eid in sorted_ids]

    def list_executor_ids(self) -> list[str]:
        """List registered executor IDs sorted deterministically."""
        return sorted(self._adapters.keys())

    def count(self) -> int:
        """Return count of registered adapters."""
        return len(self._adapters)

    def clear(self) -> None:
        """Clear all registered adapters."""
        self._adapters.clear()

    @staticmethod
    def check_compatibility(
        capabilities: ExecutorCapabilities, package: ExecutionPackage
    ) -> tuple[bool, tuple[str, ...]]:
        """Mechanically check if capabilities satisfy package requirements.

        Returns (is_compatible, tuple_of_mismatch_reasons).
        Never silently downgrades capabilities.
        """
        if not isinstance(capabilities, ExecutorCapabilities):
            raise TypeError(
                f"capabilities must be ExecutorCapabilities, got {type(capabilities).__name__}"
            )
        if not isinstance(package, ExecutionPackage):
            raise TypeError(
                f"package must be ExecutionPackage, got {type(package).__name__}"
            )

        reasons: list[str] = []
        requirements = package.capability_requirements

        # Capability requirements are a closed vocabulary. Unknown keys must not
        # disappear from matching and accidentally produce a compatible result.
        for key in sorted(set(requirements) - KNOWN_CAPABILITY_REQUIREMENT_KEYS):
            reasons.append(f"Unknown capability requirement key {key!r}")

        # 1. Canonical role support
        if not capabilities.supports_role(package.canonical_role):
            reasons.append(
                f"Canonical role {package.canonical_role!r} not supported by executor (supported: {list(capabilities.supported_canonical_roles)})"
            )

        # 2. Execution mode check
        if "execution_mode" in requirements:
            req_mode = requirements["execution_mode"]
            if not isinstance(req_mode, str):
                reasons.append(
                    f"Execution mode {req_mode!r} must be a canonical string"
                )
            elif req_mode not in ALLOWED_EXECUTION_MODES:
                reasons.append(
                    f"Execution mode {req_mode!r} is not canonical "
                    f"(allowed: {sorted(ALLOWED_EXECUTION_MODES)})"
                )
            elif not capabilities.supports_mode(req_mode):
                reasons.append(
                    f"Execution mode {req_mode!r} not supported by executor (supported: {list(capabilities.supported_execution_modes)})"
                )

        # 3. Isolation mode check
        for isolation_key in ("isolation_mode", "isolation"):
            if isolation_key not in requirements:
                continue
            req_iso = requirements[isolation_key]
            if not isinstance(req_iso, str):
                reasons.append(
                    f"Isolation mode {req_iso!r} must be a canonical string"
                )
            elif req_iso not in ALLOWED_ISOLATION_MODES:
                reasons.append(
                    f"Isolation mode {req_iso!r} is not canonical "
                    f"(allowed: {sorted(ALLOWED_ISOLATION_MODES)})"
                )
            elif not capabilities.supports_isolation(req_iso):
                reasons.append(
                    f"Isolation mode {req_iso!r} not supported by executor (supported: {list(capabilities.supported_isolation_modes)})"
                )

        # 4. Timeout check
        timeout_source: tuple[str, Any] | None = None
        if "timeout_seconds" in requirements:
            timeout_source = ("capability requirement", requirements["timeout_seconds"])
        elif "timeout_seconds" in package.constraints:
            timeout_source = ("constraint", package.constraints["timeout_seconds"])
        elif "max_timeout_seconds" in package.constraints:
            timeout_source = ("constraint", package.constraints["max_timeout_seconds"])
        if timeout_source is not None:
            _, req_timeout = timeout_source
            if (
                type(req_timeout) not in (int, float)
                or isinstance(req_timeout, bool)
                or req_timeout <= 0
            ):
                reasons.append(
                    f"Requested timeout {req_timeout!r} must be a positive number of seconds"
                )
            elif (
                capabilities.max_timeout_seconds is not None
                and req_timeout > capabilities.max_timeout_seconds
            ):
                reasons.append(
                    f"Requested timeout {req_timeout}s exceeds executor maximum {capabilities.max_timeout_seconds}s"
                )

        # 5. Feature flags checks
        feature_checks = (
            (
                "requires_cancellation",
                capabilities.supports_task_cancellation,
                "task cancellation",
            ),
            (
                "requires_resume",
                capabilities.supports_task_resume,
                "task resume",
            ),
            (
                "requires_structured_result",
                capabilities.supports_structured_result,
                "structured result",
            ),
            (
                "requires_streaming_events",
                capabilities.supports_streaming_events,
                "streaming events",
            ),
            (
                "requires_working_directory",
                capabilities.supports_working_directory,
                "working directory",
            ),
            (
                "requires_artifact_transport",
                capabilities.supports_artifact_transport,
                "artifact transport",
            ),
        )
        for req_key, cap_supported, feature_name in feature_checks:
            if req_key not in requirements:
                continue
            requested = requirements[req_key]
            if type(requested) is not bool:
                reasons.append(f"Requirement {req_key!r} must be a bool")
            elif requested and not cap_supported:
                reasons.append(
                    f"Requirement {req_key!r} ({feature_name}) is not supported by executor"
                )

        # Explicit target IDs are routing constraints, not semantic preferences.
        for target_key in ("target_executor_id", "executor_id"):
            if target_key not in requirements:
                continue
            requested_executor_id = requirements[target_key]
            if not isinstance(requested_executor_id, str) or not requested_executor_id.strip():
                reasons.append(
                    f"Requirement {target_key!r} must be a non-empty executor ID string"
                )
            elif requested_executor_id != capabilities.executor_id:
                reasons.append(
                    f"Requirement {target_key!r} requests executor "
                    f"{requested_executor_id!r}, not {capabilities.executor_id!r}"
                )

        return (len(reasons) == 0, tuple(reasons))

    def resolve(
        self, package: ExecutionPackage, target_executor_id: str | None = None
    ) -> ExecutorResolution:
        """Mechanically resolve target executor for an execution package.

        Resolution cardinality:
        - Explicit executor_id:
          - Unknown in registry -> EXECUTOR_NOT_FOUND
          - Known but incompatible -> CAPABILITY_MISMATCH (never searches/falls back to other adapters)
          - Known and compatible -> RESOLVED
        - Capability-only matching (no explicit executor_id):
          - 0 matching executors -> EXECUTOR_NOT_FOUND (fail closed)
          - 1 matching executor -> RESOLVED
          - >1 matching executors -> NEEDS_SEMANTIC_CHOICE with stable sorted candidates list (never guess)
        """
        if not isinstance(package, ExecutionPackage):
            raise TypeError(
                f"package must be an ExecutionPackage, got {type(package).__name__}"
            )

        # Check explicit target executor from argument or package constraints
        explicit_id = target_executor_id
        if explicit_id is None:
            explicit_id = (
                package.constraints.get("target_executor_id")
                or package.constraints.get("executor_id")
                or package.capability_requirements.get("target_executor_id")
                or package.capability_requirements.get("executor_id")
            )

        if explicit_id is not None:
            explicit_id = str(explicit_id).strip()
            if explicit_id not in self._adapters:
                return ExecutorResolution(
                    outcome=ResolutionOutcome.EXECUTOR_NOT_FOUND,
                    selected_executor_id=None,
                    candidate_executor_ids=(),
                    details=f"Explicit target executor {explicit_id!r} is not registered in ExecutorRegistry",
                )

            adapter = self._adapters[explicit_id]
            is_compat, reasons = self.check_compatibility(adapter.capabilities(), package)
            if not is_compat:
                # Incompatible explicit executor: FAIL CLOSED, never fall back to another executor!
                return ExecutorResolution(
                    outcome=ResolutionOutcome.CAPABILITY_MISMATCH,
                    selected_executor_id=None,
                    candidate_executor_ids=(),
                    mismatch_reasons=reasons,
                    details=(
                        f"Explicit target executor {explicit_id!r} does not satisfy package capability "
                        f"requirements: {'; '.join(reasons)}"
                    ),
                )

            return ExecutorResolution(
                outcome=ResolutionOutcome.RESOLVED,
                selected_executor_id=explicit_id,
                candidate_executor_ids=(explicit_id,),
                details=f"Explicitly resolved to registered executor {explicit_id!r}",
            )

        # Capability-only mechanical resolution across all registered adapters
        matching_ids: list[str] = []
        for eid, adapter in self._adapters.items():
            is_compat, _ = self.check_compatibility(adapter.capabilities(), package)
            if is_compat:
                matching_ids.append(eid)

        # Stable deterministic sort
        matching_ids.sort()

        if len(matching_ids) == 0:
            return ExecutorResolution(
                outcome=ResolutionOutcome.EXECUTOR_NOT_FOUND,
                selected_executor_id=None,
                candidate_executor_ids=(),
                details="No registered executor satisfies package capability requirements",
            )
        elif len(matching_ids) == 1:
            winner = matching_ids[0]
            return ExecutorResolution(
                outcome=ResolutionOutcome.RESOLVED,
                selected_executor_id=winner,
                candidate_executor_ids=(winner,),
                details=f"Unambiguously resolved to single compatible executor {winner!r}",
            )
        else:
            candidates = tuple(matching_ids)
            return ExecutorResolution(
                outcome=ResolutionOutcome.NEEDS_SEMANTIC_CHOICE,
                selected_executor_id=None,
                candidate_executor_ids=candidates,
                details=(
                    f"Multiple executors ({len(candidates)}) satisfy capability requirements; "
                    f"explicit semantic choice required from candidates: {list(candidates)}"
                ),
            )
