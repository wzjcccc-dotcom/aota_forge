"""Shared deterministic harness for M2/W3 tests (delivery/recovery/concurrency).

Reuses the W1 durable-fake pattern: an executor-neutral adapter whose
adapter_handle mechanically encodes the scripted outcome, so a FRESH adapter
object in a FRESH dispatcher behaves identically after "restart" without any
process-local memory. A controlled transport scripts the W2 exact-session
outcomes deterministically.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.roles import CANONICAL_ROLES
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.runtime.completion import (
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
)

FAKE_EXECUTOR_ID = "durable-fake"
DISPATCH_STAMP = "2026-09-06T00:00:00+00:00"
TEST_SCOPE = "durable-fake:coder"
ORIGIN_SESSION = "20260906_taskmain_disposable"


class DurableWorldAdapter(ExecutorAdapter):
    """Stable-handle durable fake; the handle itself carries the scripted truth.

    ``total_dispatches`` is a CLASS counter so restart-style tests can prove
    that a fresh runtime performed zero additional physical dispatches.
    """

    total_dispatches = 0

    def __init__(self, *, force_status_error: Exception | None = None) -> None:
        self.physical_dispatch_count = 0
        self._force_status_error = force_status_error

    @classmethod
    def reset_world(cls) -> None:
        cls.total_dispatches = 0

    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id=FAKE_EXECUTOR_ID,
            adapter_kind="durable_fake_w3_test_double",
            supported_execution_modes=("async",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=CANONICAL_ROLES,
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        return ValidationResult(valid=True)

    @staticmethod
    def handle_for(package: ExecutionPackage) -> str:
        outcome = package.constraints.get("fake_outcome", "success")
        return f"durable-fake::{package.canonical_task_id}::{package.correlation_id}::{outcome}"

    @staticmethod
    def _parse(handle: str) -> tuple[str, str, str]:
        _, task_id, correlation_id, outcome = handle.split("::")
        return task_id, correlation_id, outcome

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        self.physical_dispatch_count += 1
        type(self).total_dispatches += 1
        return DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=self.handle_for(package),
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time=DISPATCH_STAMP,
        )

    def _state(self, outcome: str) -> CanonicalTaskState:
        if outcome in {"running", "slow"}:
            return CanonicalTaskState.RUNNING
        if outcome == "failed":
            return CanonicalTaskState.FAILED
        if outcome == "cancelled":
            return CanonicalTaskState.CANCELLED
        return CanonicalTaskState.COMPLETED

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        if self._force_status_error is not None:
            raise self._force_status_error
        task_id, _, outcome = self._parse(adapter_handle)
        return TaskStatusResult(canonical_task_id=task_id, state=self._state(outcome))

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        if self._force_status_error is not None:
            raise self._force_status_error
        task_id, correlation_id, outcome = self._parse(adapter_handle)
        state = self._state(outcome)
        if outcome == "failed":
            return CanonicalResult.failure(
                canonical_task_id=task_id,
                executor_id=FAKE_EXECUTOR_ID,
                error_code="EXECUTION_FAILED",
                error_message="w3 durable fake scripted failure",
                correlation_id=correlation_id,
                canonical_task_state=state.value,
            )
        if outcome == "cancelled":
            return CanonicalResult.cancelled(
                canonical_task_id=task_id,
                executor_id=FAKE_EXECUTOR_ID,
                correlation_id=correlation_id,
            )
        return CanonicalResult.success(
            canonical_task_id=task_id,
            executor_id=FAKE_EXECUTOR_ID,
            result_data={"proof": "m2-w3-durable"},
            stdout_summary="raw-worker-stdout-should-not-travel-to-taskmain",
            stderr_summary="raw-worker-stderr-should-not-travel-to-taskmain",
            execution_stats={"duration_ms": 5},
            correlation_id=correlation_id,
        )

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        return CancelResult(
            canonical_task_id=canonical_task_id, cancelled=True, state=CanonicalTaskState.CANCELLED
        )

    def resume(self, canonical_task_id, adapter_handle, resume_package) -> ResumeResult:
        return ResumeResult(canonical_task_id=canonical_task_id, state=CanonicalTaskState.RUNNING)


@dataclass
class ControlledTransport:
    """Scripts exact-session delivery outcomes; records every attempt."""

    script: list[DeliveryAttemptEvidence] = field(default_factory=list)
    default: DeliveryAttemptEvidence | None = None
    raise_at: set[int] = field(default_factory=set)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence:
        index = len(self.attempts)
        self.attempts.append({"session_ref": session_ref, "envelope": envelope, "index": index})
        if index in self.raise_at:
            raise RuntimeError("injected transport crash")
        if index < len(self.script):
            return self.script[index]
        if self.default is not None:
            return self.default
        raise AssertionError("ControlledTransport exhausted without a scripted outcome")

    # convenience ack helper bound to a specific completion identity
    @staticmethod
    def ack(canonical_task_id: str, card_digest: str) -> DeliveryAttemptEvidence:
        return DeliveryAttemptEvidence(
            outcome=DeliveryTransportOutcome.COMPLETED,
            response_text=(
                "reconciled worker completion\n"
                f"AOTA_COMPLETION_ACK_V1 canonical_task_id={canonical_task_id} card_digest={card_digest}"
            ),
        )


def ack_completed(card_task_id: str, card_digest: str) -> DeliveryAttemptEvidence:
    return ControlledTransport.ack(card_task_id, card_digest)


def completed_without_ack() -> DeliveryAttemptEvidence:
    return DeliveryAttemptEvidence(
        outcome=DeliveryTransportOutcome.COMPLETED,
        response_text="task-main replied something else entirely",
    )


def retryable_busy() -> DeliveryAttemptEvidence:
    return DeliveryAttemptEvidence(
        outcome=DeliveryTransportOutcome.RETRYABLE, detail="HERMES_REENTRY_RETRYABLE"
    )


def not_found_missing_session() -> DeliveryAttemptEvidence:
    return DeliveryAttemptEvidence(
        outcome=DeliveryTransportOutcome.NOT_FOUND, detail="HERMES_EXACT_SESSION_NOT_FOUND"
    )


def make_package(
    task_id: str = "task-w3-1",
    *,
    idem_key: str | None = None,
    outcome: str = "success",
    canonical_role: str = "coder",
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role=canonical_role,
        instruction=f"m2-w3 durable proof {task_id}",
        capability_requirements={"execution_mode": "async", "isolation_mode": "process"},
        constraints={"fake_outcome": outcome},
        idempotency_key=idem_key or f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


def open_store(path: pathlib.Path) -> FileBackedExecutionStateStore:
    return FileBackedExecutionStateStore(path)


def build_dispatcher(
    store: FileBackedExecutionStateStore,
    adapter: ExecutorAdapter | None = None,
    *,
    origin_session_ref: str | None = ORIGIN_SESSION,
    scope_resolver: bool = True,
) -> ExecutionDispatcher:
    registry = ExecutorRegistry()
    registry.register(adapter or DurableWorldAdapter())
    return ExecutionDispatcher(
        registry,
        state_store=store,
        origin_session_ref=OriginSessionRef(value=origin_session_ref) if origin_session_ref else None,
        admission_scope_resolver=(lambda package: TEST_SCOPE) if scope_resolver else None,
    )


def build_coordinator(
    dispatcher: ExecutionDispatcher,
    store: FileBackedExecutionStateStore,
    transport: ControlledTransport | None = None,
    *,
    limits: dict[str, int] | None = None,
    now_fn: Any = None,
    **kwargs: Any,
) -> DurableCompletionCoordinator:
    ctor: dict[str, Any] = {
        "dispatcher": dispatcher,
        "store": store,
        "transport": transport,
        "admission_limits": limits if limits is not None else {TEST_SCOPE: 1},
        **kwargs,
    }
    if now_fn is not None:
        ctor["now_fn"] = now_fn
    return DurableCompletionCoordinator(**ctor)


def claim_delivery(store: FileBackedExecutionStateStore, task_id: str, *, owner: str, until: str, attempt: int = 1):
    record = store.get(task_id)
    return store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "delivery_state": DeliveryState.CLAIMED.value,
            "delivery_claim_owner": owner,
            "delivery_claim_until": until,
            "delivery_attempt": attempt,
        },
    )
