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
from collections.abc import Callable
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
)
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionIdempotencyConflictError,
    ExecutionPhase,
    ExecutionRecordNotFoundError,
    ExecutionStateStore,
    OriginSessionRef,
    UnboundOriginSessionError,
    card_digest_for,
    is_placeholder_origin_session_ref,
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


class DispatchOutcomeUnresolvedError(DispatcherError):
    """Raised when a durable record exists but physical dispatch outcome is unconfirmed.

    M2/W1 crash-window semantics: a PREPARED record means the durable intent
    identity exists while the adapter_handle/dispatch outcome was never
    persisted (crash between physical dispatch success and DISPATCHED CAS, or
    crash before physical dispatch). Recovery must reconcile externally; the
    dispatcher never assumes completion, never deletes the record, and never
    blindly redispatches. Reuses the accepted TASK_STATE_UNKNOWN code — not a
    new canonical error ontology.
    """

    code = "TASK_STATE_UNKNOWN"

    def __init__(self, canonical_task_id: str) -> None:
        self.canonical_task_id = canonical_task_id
        super().__init__(
            f"TASK_STATE_UNKNOWN: durable execution record {canonical_task_id!r} is PREPARED "
            f"with unconfirmed dispatch outcome; reconciliation required, blind redispatch prohibited"
        )


class ExecutionDispatcher:
    """Deterministic core execution dispatcher.

    Provides dependency-injected execution routing, mechanical adapter resolution, idempotency
    preservation, and status reconciliation. Does not maintain persistent project state or
    perform automatic semantic retries.

    M2/W1 durable mode (optional): when an ``ExecutionStateStore`` is supplied,
    dispatch identity, idempotency truth, route binding, task state, terminal
    CanonicalResult, Worker Result CARD payload, and the runtime-side origin
    session binding become durable and restart-safe. Routes lost with the
    process are reconstructed from the store, and the adapter object is
    re-resolved from the ExecutorRegistry by durable ``executor_id`` — the
    ``RouteRecord._adapter`` Python object itself is never persisted. With
    ``state_store=None`` the dispatcher keeps the M1 process-local behavior;
    production composition wiring is M2/W3 scope.

    Crash-window truth with a store (no exactly-once dispatch is claimed):
    - durable PREPARED identity is written before physical dispatch;
    - a crash after PREPARED and before ``adapter.dispatch`` returns leaves a
      PREPARED record; same key + intent afterwards fails closed with
      DispatchOutcomeUnresolvedError instead of redispatching;
    - a crash after physical dispatch succeeded but before the DISPATCHED CAS
      (or a DISPATCHED persist failure) leaves the durable record PREPARED
      while an execution may physically exist and the adapter_handle is lost
      to the durable store; recovery keeps sticky uncertainty and never
      assumes completion.
    """

    def __init__(
        self,
        registry: ExecutorRegistry,
        *,
        state_store: ExecutionStateStore | None = None,
        origin_session_ref: OriginSessionRef | str | None = None,
        admission_scope_resolver: Callable[[ExecutionPackage], str | None] | None = None,
    ) -> None:
        if not isinstance(registry, ExecutorRegistry):
            raise TypeError(
                f"registry must be an ExecutorRegistry, got {type(registry).__name__}"
            )
        if state_store is not None and not isinstance(state_store, ExecutionStateStore):
            raise TypeError(
                f"state_store must be an ExecutionStateStore or None, got {type(state_store).__name__}"
            )
        if admission_scope_resolver is not None and not callable(admission_scope_resolver):
            raise TypeError("admission_scope_resolver must be callable or None")
        self.registry: ExecutorRegistry = registry
        self.state_store: ExecutionStateStore | None = state_store
        # Trusted-runtime supplied; opaque; never model-self-asserted.
        self.origin_session_ref: OriginSessionRef | None = OriginSessionRef.from_value(origin_session_ref)
        # AF #49 M1/W6: mechanical classification of the runtime origin
        # binding state. A pre-session placeholder (``pending-*``) is UNBOUND:
        # durable child execution creation fails closed until the real exact
        # task-main session identity is bound (two-phase launch). No mutable
        # origin rewrite exists; the placeholder never becomes durable truth.
        self._origin_session_is_placeholder = is_placeholder_origin_session_ref(
            self.origin_session_ref
        )
        # M2/W3: server-side trusted resolver deriving the concurrency-accounting
        # scope from the runtime binding for each package. The package, model,
        # and TaskHandoff never carry it; the resolver is composition-owned.
        self._admission_scope_resolver = admission_scope_resolver
        # Process-local routing table: canonical_task_id -> RouteRecord
        self._routes: dict[str, RouteRecord] = {}
        # Idempotency index: idempotency_key -> (canonical_task_id, intent_fingerprint, DispatchResult)
        self._idempotency_index: dict[str, tuple[str, str, DispatchResult]] = {}
        # Cached dispatch results for replay
        self._dispatch_results: dict[str, DispatchResult] = {}

    @property
    def origin_session_is_placeholder(self) -> bool:
        """True when this runtime's origin binding is still the unbound placeholder.

        Mechanical, non-authoritative classification consumed by the canonical
        ingress and task-main progression to project a typed fail-closed
        refusal instead of attempting a durable child dispatch.
        """
        return self._origin_session_is_placeholder

    def _get_internal_route(self, canonical_task_id: str) -> RouteRecord:
        """Lookup the mutable route used only by Core lifecycle operations.

        Durable mode: on an in-memory miss, reconstruct the route from the
        durable record; the adapter object is re-resolved mechanically through
        the registry by durable executor_id.
        """
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if canonical_task_id not in self._routes:
            if self.state_store is not None:
                durable = self.state_store.get(canonical_task_id)
                if durable is not None:
                    return self._rehydrate_route(durable)
            raise TaskNotFoundError(canonical_task_id)
        return self._routes[canonical_task_id]

    def _rehydrate_route(self, record: DurableExecutionRecord) -> RouteRecord:
        """Reconstruct a process-local RouteRecord from a durable record."""
        if record.execution_phase != ExecutionPhase.DISPATCHED or record.adapter_handle is None:
            raise DispatchOutcomeUnresolvedError(record.canonical_task_id)
        adapter = self.registry.get(record.executor_id)
        route = RouteRecord(
            canonical_task_id=record.canonical_task_id,
            executor_id=record.executor_id,
            adapter_handle=record.adapter_handle,
            package_id=record.package_id,
            correlation_id=record.correlation_id,
            dispatch_attempt_id=record.dispatch_attempt_id,
            idempotency_key=record.idempotency_key,
            intent_fingerprint=record.intent_fingerprint,
            initial_state=record.initial_state or record.canonical_task_state,
            dispatched_at=record.dispatched_at or record.created_at,
            last_known_state=record.canonical_task_state,
            _adapter=adapter,
        )
        self._routes[record.canonical_task_id] = route
        return route

    def _persist_route_state(
        self, canonical_task_id: str, state: CanonicalTaskState
    ) -> None:
        """Durably persist an observed task state (no-op without a store)."""
        if self.state_store is None:
            return
        current = self.state_store.get(canonical_task_id)
        if current is None:
            raise ExecutionRecordNotFoundError(
                f"execution record not found for state persistence: {canonical_task_id}"
            )
        if current.canonical_task_state == state:
            return
        if current.canonical_task_state.is_terminal:
            # Durable terminal truth is sticky; nothing may un-terminate it.
            return
        self.state_store.compare_and_swap(
            canonical_task_id,
            current.record_revision,
            {"canonical_task_state": state.value},
        )

    def bind_origin_session_ref(
        self, canonical_task_id: str, origin_session_ref: OriginSessionRef | str
    ) -> DurableExecutionRecord:
        """Bind the trusted-runtime origin session onto a durable record once.

        Requires a store; the binding is runtime evidence, never authority.
        """
        if self.state_store is None:
            raise DispatcherError("bind_origin_session_ref requires an ExecutionStateStore")
        ref = OriginSessionRef.from_value(origin_session_ref)
        if ref is None:
            raise ValueError("origin_session_ref must be a non-empty opaque value")
        if is_placeholder_origin_session_ref(ref):
            # AF #49 M1/W6: the pre-session placeholder is never a bindable
            # durable origin identity; binding fails closed.
            raise UnboundOriginSessionError(
                "origin_session_ref must be a bound exact session identity; "
                "the pre-session placeholder is not durable origin authority"
            )
        record = self.state_store.get(canonical_task_id)
        if record is None:
            raise ExecutionRecordNotFoundError(f"execution record not found: {canonical_task_id}")
        if record.origin_session_ref is not None:
            if record.origin_session_ref == ref:
                return record
            raise DispatcherError(
                f"origin_session_ref already durably bound for {canonical_task_id}; rewrites rejected"
            )
        return self.state_store.compare_and_swap(
            canonical_task_id, record.record_revision, {"origin_session_ref": ref}
        )

    def attach_worker_result_card(
        self, canonical_task_id: str, card: object
    ) -> DurableExecutionRecord:
        """Durably attach an existing-ontology Worker Result CARD payload + digest.

        Reuses the CARD canonical projection (no third result ontology); the
        persisted CARD is evidence for later reconciliation and grants no
        authority by possession.
        """
        if self.state_store is None:
            raise DispatcherError("attach_worker_result_card requires an ExecutionStateStore")
        canonical_dict = getattr(card, "canonical_dict", None)
        compute_digest = getattr(card, "compute_card_digest", None)
        if not callable(canonical_dict) or not callable(compute_digest):
            raise TypeError(
                "card must expose canonical_dict() and compute_card_digest() "
                "(reuses the existing worker-result CARD ontology; no third result model)"
            )
        card_dict = canonical_dict()
        digest = compute_digest()
        record = self.state_store.get(canonical_task_id)
        if record is None:
            raise ExecutionRecordNotFoundError(f"execution record not found: {canonical_task_id}")
        # M2/W4 RV1 F04 hardening: the CARD must independently identify the SAME
        # canonical task as the durable execution record BEFORE any digest or
        # attachment semantics are considered. Reuses the existing CARD
        # identity field (task_ref; no third ontology, no new field). A
        # mismatch is a deterministic protocol-integrity rejection: the record
        # is not mutated and no digest is attached.
        card_task_ref = card_dict.get("task_ref") if isinstance(card_dict, Mapping) else None
        if card_task_ref != record.canonical_task_id:
            raise AdapterProtocolError(
                f"worker_result_card task_ref {card_task_ref!r} does not match the durable "
                f"execution canonical_task_id {record.canonical_task_id!r}; "
                f"CARD/execution identity mismatch rejected without mutation"
            )
        if record.worker_result_card is not None:
            if card_digest_for(dict(record.worker_result_card)) == record.worker_result_card_digest == digest:
                return record
            raise AdapterProtocolError(
                f"worker_result_card already durable for {canonical_task_id!r} contradicts the proposed CARD"
            )
        return self.state_store.compare_and_swap(
            canonical_task_id,
            record.record_revision,
            {"worker_result_card": card_dict, "worker_result_card_digest": digest},
        )

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

        # AF #49 M1/W6 (I49-B002): an unbound/bootstrap placeholder origin may
        # never create a durable child execution record. This is the mechanical
        # server/runtime fail-closed gate (no model cooperation required): the
        # refusal happens BEFORE any durable PREPARED identity or physical
        # dispatch, so no origin-less/placeholder-bound completion can exist.
        if self.state_store is not None and self._origin_session_is_placeholder:
            raise UnboundOriginSessionError(
                f"refusing child dispatch for {package.canonical_task_id!r}: the trusted "
                f"task-main origin session is still the pre-session placeholder; bind the "
                f"real exact session identity first (two-phase launch)"
            )

        idem_key = package.idempotency_key

        # 1. Idempotency Check (in-memory fast path, then durable truth)
        if idem_key in self._idempotency_index:
            prev_task_id, prev_intent_fp, prev_dispatch = self._idempotency_index[idem_key]
            if prev_intent_fp == package.intent_fingerprint:
                # Idempotent replay: return previous dispatch result without second execution
                return prev_dispatch
            else:
                # Idempotency conflict: same key, altered intent fingerprint
                raise IdempotencyConflictError(idem_key)

        if self.state_store is not None:
            durable = self.state_store.get_by_idempotency(idem_key)
            if durable is not None:
                if durable.intent_fingerprint == package.intent_fingerprint:
                    # Restart-safe REPLAY: no physical redispatch, ever.
                    return self._replay_from_durable(durable)
                raise IdempotencyConflictError(idem_key)

        # Duplicate canonical_task_id check (typed dispatch rejection, F01)
        if package.canonical_task_id in self._routes:
            raise DuplicateCanonicalTaskIdError(package.canonical_task_id)
        if self.state_store is not None and self.state_store.get(package.canonical_task_id) is not None:
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

        # 4. Dispatch: durable intent first, physical dispatch second
        #    (only when an ExecutionStateStore is wired; crash windows are
        #    documented in the class docstring — no exactly-once claim).
        dispatch_attempt_id = str(uuid.uuid4())
        prepared: DurableExecutionRecord | None = None
        if self.state_store is not None:
            created = self._create_prepared_record(package, executor_id, dispatch_attempt_id)
            if isinstance(created, DispatchResult):
                # Lost a create race against an identical-intent dispatch:
                # durable truth says REPLAY, so no physical dispatch happens.
                return created
            prepared = created
        try:
            dispatch_result = adapter.dispatch(package)
        except Exception:
            # Honest crash-window behavior: keep the durable PREPARED record
            # (physical outcome unknown), leave sticky uncertainty for
            # recovery, never delete, never redispatch blindly.
            raise
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

        # 5b. Persist the dispatch confirmation (adapter_handle/state) so the
        #     route survives restart. If this persistence fails after physical
        #     dispatch succeeded, the durable record stays PREPARED with the
        #     in-memory route still live in this process; recovery keeps
        #     sticky uncertainty instead of redispatching.
        if self.state_store is not None and prepared is not None:
            self.state_store.compare_and_swap(
                package.canonical_task_id,
                prepared.record_revision,
                {
                    "execution_phase": ExecutionPhase.DISPATCHED,
                    "adapter_handle": dispatch_result.adapter_handle,
                    "initial_state": dispatch_result.initial_state,
                    "dispatched_at": dispatch_result.dispatch_time,
                    "canonical_task_state": dispatch_result.initial_state.value,
                },
            )

        return dispatch_result

    def resolve_admission_scope(self, package: ExecutionPackage) -> str | None:
        """Public composition seam: the trusted concurrency-accounting scope.

        Deterministic for the same operator config + package; used by the M2/W3
        coordinator to evaluate admission BEFORE any physical dispatch.
        """
        return self._resolve_admission_scope(package)

    def _resolve_admission_scope(self, package: ExecutionPackage) -> str | None:
        """Derive the trusted concurrency-accounting scope for a new dispatch.

        Composition-owned server-side seam (M2/W3). The value is mechanical
        accounting identity only (ADMISSION_SCOPE_IS_AUTHORITY=no); it may
        never come from the package contents, the model, or TaskHandoff. An
        unresolvable scope stays None: W3 admission then fails closed in the
        coordinator rather than guessing a bucket.
        """
        if self._admission_scope_resolver is None:
            return None
        scope = self._admission_scope_resolver(package)
        if scope is None:
            return None
        if not isinstance(scope, str) or not scope.strip():
            raise DispatcherError("admission_scope_resolver must return None or a non-empty string")
        return scope

    def _create_prepared_record(
        self,
        package: ExecutionPackage,
        executor_id: str,
        dispatch_attempt_id: str,
    ) -> DurableExecutionRecord | DispatchResult:
        """Durably persist intent identity before physical dispatch.

        Returns the PREPARED record, or a replay DispatchResult if a
        concurrent dispatcher already persisted the identical intent.
        """
        assert self.state_store is not None
        record = DurableExecutionRecord(
            canonical_task_id=package.canonical_task_id,
            executor_id=executor_id,
            package_id=package.package_id,
            correlation_id=package.correlation_id,
            dispatch_attempt_id=dispatch_attempt_id,
            idempotency_key=package.idempotency_key,
            intent_fingerprint=package.intent_fingerprint,
            execution_phase=ExecutionPhase.PREPARED,
            canonical_task_state=CanonicalTaskState.CREATED,
            origin_session_ref=self.origin_session_ref,
            admission_scope=self._resolve_admission_scope(package),
        )
        try:
            return self.state_store.create(record)
        except ExecutionIdempotencyConflictError:
            competing = self.state_store.get_by_idempotency(package.idempotency_key)
            if competing is not None and competing.intent_fingerprint == package.intent_fingerprint:
                return self._replay_from_durable(competing)
            raise IdempotencyConflictError(package.idempotency_key) from None

    def _replay_from_durable(self, record: DurableExecutionRecord) -> DispatchResult:
        """Reconstruct the replay DispatchResult from durable truth."""
        if record.execution_phase != ExecutionPhase.DISPATCHED or record.adapter_handle is None:
            raise DispatchOutcomeUnresolvedError(record.canonical_task_id)
        replay = DispatchResult(
            canonical_task_id=record.canonical_task_id,
            adapter_handle=record.adapter_handle,
            initial_state=record.initial_state or record.canonical_task_state,
            dispatch_time=record.dispatched_at or record.created_at,
        )
        self._rehydrate_route(record)
        self._dispatch_results[record.canonical_task_id] = replay
        self._idempotency_index[record.idempotency_key] = (
            record.canonical_task_id,
            record.intent_fingerprint,
            replay,
        )
        return replay

    def get_route(self, canonical_task_id: str) -> RouteRecord:
        """Lookup stored mechanical route by canonical_task_id.

        Fails closed with TaskNotFoundError; never broadcasts or queries other adapters.
        """
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        return copy(self._get_internal_route(canonical_task_id))

    def has_route(self, canonical_task_id: str) -> bool:
        """Check whether a route exists for canonical_task_id (memory or durable)."""
        if not isinstance(canonical_task_id, str):
            return False
        if canonical_task_id in self._routes:
            return True
        if self.state_store is not None:
            return self.state_store.get(canonical_task_id) is not None
        return False

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
        self._persist_route_state(canonical_task_id, route.last_known_state)
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
        self._persist_terminal_result(canonical_task_id, canonical_res)
        return canonical_res

    def _persist_terminal_result(
        self, canonical_task_id: str, result: CanonicalResult
    ) -> None:
        """Attach the terminal CanonicalResult to the durable record (no-op without a store)."""
        if self.state_store is None:
            return
        current = self.state_store.get(canonical_task_id)
        if current is None:
            raise ExecutionRecordNotFoundError(
                f"execution record not found for result persistence: {canonical_task_id}"
            )
        state = parse_state(result.canonical_task_state)
        if current.terminal_result is not None:
            if current.terminal_result.to_json() != result.to_json():
                raise AdapterProtocolError(
                    f"durable terminal result for {canonical_task_id!r} contradicts the "
                    f"adapter-reported CanonicalResult"
                )
            if current.canonical_task_state != state:
                raise AdapterProtocolError(
                    f"durable terminal state {current.canonical_task_state.value!r} contradicts "
                    f"adapter-reported result state {state.value!r}"
                )
            return
        if not state.is_terminal:
            # Non-terminal observation (e.g., UNKNOWN); no terminal attachment.
            if current.canonical_task_state != state and not current.canonical_task_state.is_terminal:
                self.state_store.compare_and_swap(
                    canonical_task_id,
                    current.record_revision,
                    {"canonical_task_state": state.value},
                )
            return
        self.state_store.compare_and_swap(
            canonical_task_id,
            current.record_revision,
            {"terminal_result": result.to_dict(), "canonical_task_state": state.value},
        )

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
        self._persist_route_state(canonical_task_id, route.last_known_state)
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
        self._persist_route_state(canonical_task_id, route.last_known_state)
        return resume_res

    def _observe_adapter_state(self, canonical_task_id: str) -> CanonicalTaskState:
        """Mechanical adapter status observation WITHOUT any persistence.

        Shared by :meth:`reconcile_status` (observe + persist) and
        :meth:`observe_status` (pure observation). Preserves the accepted
        reconciliation semantics exactly:
        - definitive adapter states map to canonical state;
        - adapter unknown/disconnect/uncertain outcome observes UNKNOWN;
        - typed protocol/binding integrity violations fail closed and propagate
          (the route is left unmutated, S2/M3/R1 F02).
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
        return state

    def observe_status(self, canonical_task_id: str) -> CanonicalTaskState:
        """Mechanical adapter status observation that NEVER persists.

        AF #49 M1/W9: the parent-side completion coordinator must be able to
        gate a false adapter success BEFORE terminal truth becomes sticky
        durable state. This is a pure observation seam: it queries the exact
        stored route, applies the same canonical state mapping/validation as
        :meth:`reconcile_status`, and writes nothing. It carries no semantic
        policy and no AF work-plane ontology; the caller owns any gate.
        """
        return self._observe_adapter_state(canonical_task_id)

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
        state = self._observe_adapter_state(canonical_task_id)
        route = self._get_internal_route(canonical_task_id)
        route.last_known_state = state
        self._persist_route_state(canonical_task_id, state)
        return state

    def persist_reconciled_terminal_result(
        self, canonical_task_id: str, result: CanonicalResult
    ) -> None:
        """Mechanically persist a parent-side reconciled terminal result.

        AF #49 M1/W9: the parent-side completion coordinator may reconcile
        terminal truth that differs from the adapter's mechanical observation
        (for example: process exit 0 without a governed semantic ``task.return``
        is NOT semantic success). Core validates the result identity and state
        against the exact stored route, then attaches it exactly once through
        the existing terminal-result persistence; it never interprets AF
        work-plane semantics and remains executor-neutral.
        """
        if not isinstance(result, CanonicalResult):
            raise TypeError(f"result must be a CanonicalResult, got {type(result).__name__}")
        route = self._get_internal_route(canonical_task_id)
        if result.canonical_task_id != canonical_task_id:
            raise AdapterProtocolError(
                f"reconciled result canonical_task_id {result.canonical_task_id!r} "
                f"does not match Core task {canonical_task_id!r}"
            )
        if result.executor_id != route.executor_id:
            raise AdapterProtocolError(
                f"reconciled result executor_id {result.executor_id!r} does not "
                f"match route executor {route.executor_id!r}"
            )
        state = self._validated_response_state(
            route, result.canonical_task_state, "persist_reconciled_terminal_result"
        )
        if not state.is_terminal:
            raise AdapterProtocolError(
                "persist_reconciled_terminal_result requires a terminal result state"
            )
        route.last_known_state = state
        self._persist_terminal_result(canonical_task_id, result)
