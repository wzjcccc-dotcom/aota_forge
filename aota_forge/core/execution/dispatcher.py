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
from copy import copy
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


class AdapterProtocolError(DispatcherError, ValueError):
    """Raised when an adapter contradicts a Core-owned identity or state."""

    code = "ADAPTER_PROTOCOL_ERROR"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


# Executor-neutral protocol-integrity filter (S2/M3/R1 F02 repair):
# exceptions carrying one of these ACCEPTED canonical codes are protocol /
# binding integrity violations, not ordinary runtime uncertainty. Reuses the
# same `.code` / `.error_code` attribute pattern as the canonical ingress
# projection; never inspects adapter class identity.
_CANONICAL_PROTOCOL_INTEGRITY_CODES = frozenset({AdapterProtocolError.code})


def _carries_canonical_protocol_violation(exc: BaseException) -> bool:
    for attribute in ("code", "error_code"):
        try:
            candidate = getattr(exc, attribute, None)
        except Exception:
            continue
        if isinstance(candidate, str) and candidate.strip() in _CANONICAL_PROTOCOL_INTEGRITY_CODES:
            return True
    return False


class DuplicateCanonicalTaskIdError(DispatcherError):
    """Raised when a fresh dispatch reuses an already-routed canonical_task_id.

    S2/M3/R1 F01 repair: the rejection carries the ACCEPTED canonical ingress
    code DISPATCH_REJECTED so the canonical ingress projects a typed
    dispatch rejection instead of INTERNAL_MECHANICAL_ERROR. The
    DUPLICATE_CANONICAL_TASK_ID detail stays in the message; it is not a new
    canonical error code, and a duplicate task id with a fresh idempotency
    key remains a rejection, never a REPLAY.
    """

    code = "DISPATCH_REJECTED"

    def __init__(self, canonical_task_id: str) -> None:
        self.canonical_task_id = canonical_task_id
        self.message = (
            f"DUPLICATE_CANONICAL_TASK_ID: Task {canonical_task_id!r} "
            f"has already been dispatched"
        )
        super().__init__(self.message)


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

    def _get_internal_route(self, canonical_task_id: str) -> RouteRecord:
        """Lookup the mutable route used only by Core lifecycle operations."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if canonical_task_id not in self._routes:
            raise TaskNotFoundError(canonical_task_id)
        return self._routes[canonical_task_id]

    @staticmethod
    def _validate_response_task_id(
        response: object, expected_task_id: str, operation: str
    ) -> None:
        actual_task_id = getattr(response, "canonical_task_id", None)
        if actual_task_id != expected_task_id:
            raise AdapterProtocolError(
                f"{operation} response canonical_task_id {actual_task_id!r} "
                f"does not match Core task {expected_task_id!r}"
            )

    @staticmethod
    def _validated_response_state(
        route: RouteRecord,
        response_state: object,
        operation: str,
        *,
        enforce_terminal: bool = True,
    ) -> CanonicalTaskState:
        try:
            state = parse_state(response_state)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise AdapterProtocolError(
                f"{operation} response has invalid canonical task state: {response_state!r}"
            ) from exc

        if (
            enforce_terminal
            and route.last_known_state.is_terminal
            and state != route.last_known_state
        ):
            raise AdapterProtocolError(
                f"{operation} response state {state.value!r} contradicts "
                f"known terminal state {route.last_known_state.value!r}"
            )
        return state

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

        # Duplicate canonical_task_id check (typed dispatch rejection, F01)
        if package.canonical_task_id in self._routes:
            raise DuplicateCanonicalTaskIdError(package.canonical_task_id)

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
        self._validate_response_task_id(
            dispatch_result, package.canonical_task_id, "dispatch"
        )

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
        return copy(self._get_internal_route(canonical_task_id))

    def has_route(self, canonical_task_id: str) -> bool:
        """Check whether a route exists for canonical_task_id."""
        if not isinstance(canonical_task_id, str):
            return False
        return canonical_task_id in self._routes

    def list_routes(self) -> list[RouteRecord]:
        """List all registered routes sorted deterministically by canonical_task_id."""
        sorted_ids = sorted(self._routes.keys())
        return [copy(self._routes[cid]) for cid in sorted_ids]

    def status(self, canonical_task_id: str) -> TaskStatusResult:
        """Query current execution state of a dispatched task using exact stored route."""
        route = self._get_internal_route(canonical_task_id)
        adapter = route._adapter
        status_res = adapter.status(canonical_task_id, route.adapter_handle)
        self._validate_response_task_id(status_res, canonical_task_id, "status")
        route.last_known_state = self._validated_response_state(
            route, status_res.state, "status"
        )
        return status_res

    def result(self, canonical_task_id: str) -> CanonicalResult:
        """Fetch terminal CanonicalResult for a dispatched task using exact stored route."""
        route = self._get_internal_route(canonical_task_id)
        adapter = route._adapter
        canonical_res = adapter.result(canonical_task_id, route.adapter_handle)
        self._validate_response_task_id(canonical_res, canonical_task_id, "result")
        if canonical_res.executor_id != route.executor_id:
            raise AdapterProtocolError(
                f"result response executor_id {canonical_res.executor_id!r} does not "
                f"match route executor {route.executor_id!r}"
            )
        route.last_known_state = self._validated_response_state(
            route, canonical_res.canonical_task_state, "result"
        )
        return canonical_res

    def cancel(self, canonical_task_id: str, executor: str | None = None) -> CancelResult:
        """Request cancellation using the exact stored route and optional executor identity."""
        route = self._get_internal_route(canonical_task_id)

        if executor is not None and route.executor_id != executor:
            raise ValueError(
                f"ROUTE_EXECUTOR_MISMATCH: Task {canonical_task_id!r} is routed to "
                f"executor {route.executor_id!r}, not requested {executor!r}"
            )

        if route.last_known_state == CanonicalTaskState.CANCELLED:
            replay_result = getattr(route, "_successful_cancel_result", None)
            if isinstance(replay_result, CancelResult):
                return replay_result

        if route.last_known_state.is_terminal:
            raise ValueError(
                f"TASK_ALREADY_TERMINAL: Task {canonical_task_id!r} is already in terminal state "
                f"{route.last_known_state.value}"
            )

        adapter = route._adapter
        cancel_res = adapter.cancel(canonical_task_id, route.adapter_handle)
        self._validate_response_task_id(cancel_res, canonical_task_id, "cancel")
        route.last_known_state = self._validated_response_state(
            route, cancel_res.state, "cancel"
        )
        if cancel_res.cancelled and cancel_res.state == CanonicalTaskState.CANCELLED:
            setattr(route, "_successful_cancel_result", cancel_res)
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
        route = self._get_internal_route(canonical_task_id)
        if resume_package.canonical_task_id != canonical_task_id:
            raise PackageInvalidError(
                route.executor_id,
                (
                    "resume package canonical_task_id "
                    f"{resume_package.canonical_task_id!r} does not match target "
                    f"{canonical_task_id!r}",
                ),
            )
        if resume_package.operation != "task_resume":
            raise PackageInvalidError(
                route.executor_id,
                (
                    "resume package operation must be 'task_resume', got "
                    f"{resume_package.operation!r}",
                ),
            )
        if route.last_known_state.is_terminal:
            raise DispatcherError(
                f"TASK_ALREADY_TERMINAL: Task {canonical_task_id!r} is already in terminal state "
                f"{route.last_known_state.value}"
            )
        adapter = route._adapter
        resume_res = adapter.resume(canonical_task_id, route.adapter_handle, resume_package)
        self._validate_response_task_id(resume_res, canonical_task_id, "resume")
        route.last_known_state = self._validated_response_state(
            route, resume_res.state, "resume", enforce_terminal=False
        )
        return resume_res

    def reconcile_status(self, canonical_task_id: str) -> CanonicalTaskState:
        """Reconcile task state from adapter status query.

        - Definitive adapter states map to canonical state.
        - Adapter unknown, disconnect, or uncertain outcome remains UNKNOWN.
        - UNKNOWN is NEVER inferred as completed or successful.
        - Typed protocol/binding integrity violations carrying an accepted
          canonical protocol code (e.g. ADAPTER_PROTOCOL_ERROR) fail closed
          and propagate; the route is left unmutated (S2/M3/R1 F02).
        - Returns reconciled CanonicalTaskState.
        """
        route = self._get_internal_route(canonical_task_id)
        adapter = route._adapter
        try:
            status_res = adapter.status(canonical_task_id, route.adapter_handle)
            self._validate_response_task_id(status_res, canonical_task_id, "reconcile_status")
            state = self._validated_response_state(
                route, status_res.state, "reconcile_status"
            )
        except AdapterProtocolError:
            raise
        except Exception as exc:
            if _carries_canonical_protocol_violation(exc):
                raise
            if route.last_known_state.is_terminal:
                return route.last_known_state
            state = CanonicalTaskState.UNKNOWN

        route.last_known_state = state
        return state
