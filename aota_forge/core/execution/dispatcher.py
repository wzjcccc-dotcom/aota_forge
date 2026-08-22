"""ExecutionDispatcher and canonical task lifecycle reconciliation (M5-3).

Core deterministic execution dispatcher responsible for:
- Executing dispatch flow: ExecutionPackage -> registry.resolve -> unique adapter -> adapter.validate_package -> adapter.dispatch -> record route -> DispatchResult.
- Binding and isolating canonical routes without broadcasting across adapters.
- Preserving distinct identity domains: canonical_task_id, executor_id, adapter_handle, package_id, correlation_id, and dispatch_attempt_id.
- Enforcing dispatch idempotency (REPLAY vs CONFLICT).
- Mechanical lifecycle operations (status, result, cancel, resume) routed strictly via stored route.
- Deterministic task state reconciliation (UNKNOWN preserved, never false completion).
- Pure core execution: zero Hermes dependencies, zero heuristic ranking, zero automatic semantic retries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import (
    AmbiguousExecutorError,
    CapabilityMismatchError,
    ExecutorNotFoundError,
    ExecutorRegistry,
    ResolutionOutcome,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState, parse_state


@dataclass
class RouteRecord:
    """Bounded process-local mechanical routing record separating distinct identity domains.

    Explicitly separates:
    - canonical_task_id: Semantic task identity across life cycle.
    - executor_id: Registered adapter identifier.
    - adapter_handle: Adapter-local private task handle.
    - package_id: Immutable execution package identity.
    - correlation_id: End-to-end lineage correlation.
    - dispatch_attempt_id: Physical dispatch attempt identity.
    - idempotency_key: Dispatch idempotency pairing key.
    - intent_fingerprint: SHA-256 fingerprint of semantic intent.
    """

    canonical_task_id: str
    executor_id: str
    adapter_handle: str
    package_id: str
    correlation_id: str
    dispatch_attempt_id: str
    idempotency_key: str
    intent_fingerprint: str
    initial_state: CanonicalTaskState
    dispatched_at: str
    last_known_state: CanonicalTaskState
    # Process-local binding only; public route serializers intentionally omit it.
    _adapter: ExecutorAdapter = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_task_id, str) or not self.canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")
        if not isinstance(self.adapter_handle, str) or not self.adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")
        if not isinstance(self.package_id, str) or not self.package_id.strip():
            raise ValueError("package_id must be a non-empty string")
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ValueError("correlation_id must be a non-empty string")
        if not isinstance(self.dispatch_attempt_id, str) or not self.dispatch_attempt_id.strip():
            raise ValueError("dispatch_attempt_id must be a non-empty string")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if not isinstance(self.intent_fingerprint, str) or not self.intent_fingerprint.strip():
            raise ValueError("intent_fingerprint must be a non-empty string")
        if not isinstance(self._adapter, ExecutorAdapter):
            raise TypeError(
                f"_adapter must implement ExecutorAdapter, got {type(self._adapter).__name__}"
            )
        self.initial_state = parse_state(self.initial_state)
        self.last_known_state = parse_state(self.last_known_state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_task_id": self.canonical_task_id,
            "executor_id": self.executor_id,
            "adapter_handle": self.adapter_handle,
            "package_id": self.package_id,
            "correlation_id": self.correlation_id,
            "dispatch_attempt_id": self.dispatch_attempt_id,
            "idempotency_key": self.idempotency_key,
            "intent_fingerprint": self.intent_fingerprint,
            "initial_state": self.initial_state.value,
            "dispatched_at": self.dispatched_at,
            "last_known_state": self.last_known_state.value,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


class DispatcherError(Exception):
    """Base exception for ExecutionDispatcher errors."""


class TaskNotFoundError(DispatcherError, KeyError):
    """Raised when a requested canonical_task_id has no registered route."""

    def __init__(self, canonical_task_id: str) -> None:
        self.canonical_task_id = canonical_task_id
        super().__init__(
            f"Task {canonical_task_id!r} not found in dispatcher route table"
        )


class IdempotencyConflictError(DispatcherError, ValueError):
    """Raised when an idempotency key is reused with a different intent fingerprint."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency key {idempotency_key!r} reused with different intent fingerprint (conflict)"
        )


class PackageInvalidError(DispatcherError, ValueError):
    """Raised when execution package fails adapter validation before dispatch."""

    def __init__(self, executor_id: str, errors: tuple[str, ...]) -> None:
        self.executor_id = executor_id
        self.errors = errors
        super().__init__(
            f"Execution package validation failed for executor {executor_id!r}: {'; '.join(errors)}"
        )


class NeedsSemanticChoiceError(DispatcherError, ValueError):
    """Raised when multiple executors match without an explicit semantic target."""

    def __init__(self, candidates: tuple[str, ...], details: str = "") -> None:
        self.candidates = candidates
        super().__init__(
            details
            or f"Multiple compatible executors {list(candidates)}; explicit semantic choice required"
        )


class ExecutionDispatcher:
    """Deterministic core execution dispatcher.

    Provides dependency-injected execution routing, mechanical adapter resolution, idempotency
    preservation, and status reconciliation. Does not maintain persistent project state or
    perform automatic semantic retries.
    """

    def __init__(self, registry: ExecutorRegistry) -> None:
        if not isinstance(registry, ExecutorRegistry):
            raise TypeError(
                f"registry must be an ExecutorRegistry, got {type(registry).__name__}"
            )
        self.registry: ExecutorRegistry = registry
        # Process-local routing table: canonical_task_id -> RouteRecord
        self._routes: dict[str, RouteRecord] = {}
        # Idempotency index: idempotency_key -> (canonical_task_id, intent_fingerprint, DispatchResult)
        self._idempotency_index: dict[str, tuple[str, str, DispatchResult]] = {}
        # Cached dispatch results for replay
        self._dispatch_results: dict[str, DispatchResult] = {}

    def dispatch(
        self,
        package: ExecutionPackage,
        target_executor_id: str | None = None,
    ) -> DispatchResult:
        """Dispatch execution package through canonical validation and routing flow.

        Flow:
        1. Check idempotency:
           - same idempotency_key + same intent_fingerprint -> REPLAY (returns existing DispatchResult)
           - same idempotency_key + different intent_fingerprint -> CONFLICT (raises IdempotencyConflictError)
        2. Resolve executor from registry:
           - Explicit target: unknown -> ExecutorNotFoundError; incompatible -> CapabilityMismatchError; compatible -> Resolved
           - Capability matching: 0 -> ExecutorNotFoundError; 1 -> Resolved; >1 -> NeedsSemanticChoiceError
        3. Validate package against adapter: adapter.validate_package(package)
           - If invalid: FAIL CLOSED (raises PackageInvalidError); do not dispatch or repair
        4. Dispatch to adapter: adapter.dispatch(package)
        5. Record canonical route with separated identities
        6. Return canonical DispatchResult
        """
        if not isinstance(package, ExecutionPackage):
            raise TypeError(
                f"package must be an ExecutionPackage, got {type(package).__name__}"
            )

        idem_key = package.idempotency_key

        # 1. Idempotency Check
        if idem_key in self._idempotency_index:
            prev_task_id, prev_intent_fp, prev_dispatch = self._idempotency_index[idem_key]
            if prev_intent_fp == package.intent_fingerprint:
                # Idempotent replay: return previous dispatch result without second execution
                return prev_dispatch
            else:
                # Idempotency conflict: same key, altered intent fingerprint
                raise IdempotencyConflictError(idem_key)

        # Duplicate canonical_task_id check
        if package.canonical_task_id in self._routes:
            raise DispatcherError(
                f"DUPLICATE_CANONICAL_TASK_ID: Task {package.canonical_task_id!r} has already been dispatched"
            )

        # 2. Registry Mechanical Resolution
        resolution = self.registry.resolve(package, target_executor_id=target_executor_id)

        if resolution.outcome == ResolutionOutcome.EXECUTOR_NOT_FOUND:
            raise ExecutorNotFoundError(
                target_executor_id or "unspecified_matching_executor"
            )
        elif resolution.outcome == ResolutionOutcome.CAPABILITY_MISMATCH:
            raise CapabilityMismatchError(
                target_executor_id or "unspecified_executor",
                resolution.mismatch_reasons,
            )
        elif resolution.outcome == ResolutionOutcome.NEEDS_SEMANTIC_CHOICE:
            raise NeedsSemanticChoiceError(
                resolution.candidate_executor_ids,
                details=resolution.details,
            )
        elif resolution.outcome != ResolutionOutcome.RESOLVED or not resolution.selected_executor_id:
            raise DispatcherError(f"Unexpected resolution outcome: {resolution.outcome}")

        executor_id = resolution.selected_executor_id
        adapter = self.registry.get(executor_id)

        # 3. Validate package on target adapter
        validation = adapter.validate_package(package)
        if not validation.valid:
            raise PackageInvalidError(executor_id, validation.errors)

        # 4. Dispatch to adapter
        dispatch_attempt_id = str(uuid.uuid4())
        dispatch_result = adapter.dispatch(package)

        # 5. Record route with explicit separated identities
        route = RouteRecord(
            canonical_task_id=package.canonical_task_id,
            executor_id=executor_id,
            adapter_handle=dispatch_result.adapter_handle,
            package_id=package.package_id,
            correlation_id=package.correlation_id,
            dispatch_attempt_id=dispatch_attempt_id,
            idempotency_key=package.idempotency_key,
            intent_fingerprint=package.intent_fingerprint,
            initial_state=dispatch_result.initial_state,
            dispatched_at=dispatch_result.dispatch_time,
            last_known_state=dispatch_result.initial_state,
            _adapter=adapter,
        )

        self._routes[package.canonical_task_id] = route
        self._dispatch_results[package.canonical_task_id] = dispatch_result
        self._idempotency_index[idem_key] = (
            package.canonical_task_id,
            package.intent_fingerprint,
            dispatch_result,
        )

        return dispatch_result

    def get_route(self, canonical_task_id: str) -> RouteRecord:
        """Lookup stored mechanical route by canonical_task_id.

        Fails closed with TaskNotFoundError; never broadcasts or queries other adapters.
        """
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if canonical_task_id not in self._routes:
            raise TaskNotFoundError(canonical_task_id)
        return self._routes[canonical_task_id]

    def has_route(self, canonical_task_id: str) -> bool:
        """Check whether a route exists for canonical_task_id."""
        if not isinstance(canonical_task_id, str):
            return False
        return canonical_task_id in self._routes

    def list_routes(self) -> list[RouteRecord]:
        """List all registered routes sorted deterministically by canonical_task_id."""
        sorted_ids = sorted(self._routes.keys())
        return [self._routes[cid] for cid in sorted_ids]

    def status(self, canonical_task_id: str) -> TaskStatusResult:
        """Query current execution state of a dispatched task using exact stored route."""
        route = self.get_route(canonical_task_id)
        adapter = route._adapter
        status_res = adapter.status(canonical_task_id, route.adapter_handle)
        # Update last known state
        route.last_known_state = status_res.state
        return status_res

    def result(self, canonical_task_id: str) -> CanonicalResult:
        """Fetch terminal CanonicalResult for a dispatched task using exact stored route."""
        route = self.get_route(canonical_task_id)
        adapter = route._adapter
        canonical_res = adapter.result(canonical_task_id, route.adapter_handle)
        state_enum = parse_state(canonical_res.canonical_task_state)
        route.last_known_state = state_enum
        return canonical_res

    def cancel(self, canonical_task_id: str) -> CancelResult:
        """Request cancellation of an active task using exact stored route."""
        route = self.get_route(canonical_task_id)
        adapter = route._adapter
        cancel_res = adapter.cancel(canonical_task_id, route.adapter_handle)
        route.last_known_state = cancel_res.state
        return cancel_res

    def resume(
        self,
        canonical_task_id: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        """Resume a waiting task using exact stored route and new resume package."""
        if not isinstance(resume_package, ExecutionPackage):
            raise TypeError(
                f"resume_package must be an ExecutionPackage, got {type(resume_package).__name__}"
            )
        route = self.get_route(canonical_task_id)
        adapter = route._adapter
        resume_res = adapter.resume(canonical_task_id, route.adapter_handle, resume_package)
        route.last_known_state = resume_res.state
        return resume_res

    def reconcile_status(self, canonical_task_id: str) -> CanonicalTaskState:
        """Reconcile task state from adapter status query.

        - Definitive adapter states map to canonical state.
        - Adapter unknown, disconnect, or uncertain outcome remains UNKNOWN.
        - UNKNOWN is NEVER inferred as completed or successful.
        - Returns reconciled CanonicalTaskState.
        """
        route = self.get_route(canonical_task_id)
        adapter = route._adapter
        try:
            status_res = adapter.status(canonical_task_id, route.adapter_handle)
            state = status_res.state
        except Exception:
            state = CanonicalTaskState.UNKNOWN

        route.last_known_state = state
        return state
