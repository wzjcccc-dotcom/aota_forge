"""Reference Fake Executor Adapter implementation (M5-2).

In-memory, deterministic, zero-external-dependency test-only ExecutorAdapter.
Used strictly for validating M5-1 canonical execution contracts and test suites.
Not for production execution or registry default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import (
    ExecutorCapabilities,
)
from aota_forge.core.execution.package import (
    ExecutionPackage,
)
from aota_forge.core.execution.results import (
    CanonicalResult,
)
from aota_forge.core.execution.roles import (
    CANONICAL_ROLES,
    RoleMapping,
    RoleMappingNotFoundError,
    validate_canonical_role,
)
from aota_forge.core.execution.state import (
    CanonicalTaskState,
    parse_state,
    validate_transition,
)
from aota_forge.core.execution import (
    ADAPTER_PROTOCOL_ERROR,
    CANCEL_UNSUPPORTED,
    CAPABILITY_MISMATCH,
    DISPATCH_REJECTED,
    EXECUTION_FAILED,
    PACKAGE_INVALID,
    RESUME_UNSUPPORTED,
    ROLE_MAPPING_NOT_FOUND,
    TASK_NOT_FOUND,
    TASK_STATE_UNKNOWN,
)

REFERENCE_EXECUTOR_TEST_ONLY: bool = True
REFERENCE_EXECUTOR_PRODUCTION_DEFAULT: bool = False
REFERENCE_EXECUTOR_ID: str = "reference-fake"
REFERENCE_ADAPTER_KIND: str = "in_process_test_double"

DEFAULT_CAPABILITIES: ExecutorCapabilities = ExecutorCapabilities(
    executor_id=REFERENCE_EXECUTOR_ID,
    adapter_kind=REFERENCE_ADAPTER_KIND,
    supported_execution_modes=("async", "batch", "sync"),
    supports_streaming_events=True,
    supports_task_cancellation=True,
    supports_task_resume=True,
    supports_structured_result=True,
    supported_canonical_roles=CANONICAL_ROLES,
    supported_isolation_modes=("container", "none", "process", "worktree"),
    supports_working_directory=True,
    supports_artifact_transport=True,
    max_timeout_seconds=3600,
    concurrency_limit=16,
)

DEFAULT_ROLE_MAPPING: RoleMapping = RoleMapping.create(
    executor_id=REFERENCE_EXECUTOR_ID,
    mapping_dict={
        "planner": "fake_planner",
        "coder": "fake_coder",
        "reviewer": "fake_reviewer",
        "steward": "fake_steward",
        "executor": "fake_executor",
    },
)


@dataclass
class _InMemoryTaskRecord:
    """Adapter-private in-memory task state record.
    
    adapter_handle is adapter-local and never replaces canonical_task_id authority.
    """

    canonical_task_id: str
    adapter_handle: str
    package: ExecutionPackage
    state: CanonicalTaskState
    result: CanonicalResult | None = None
    idempotency_key: str = ""
    intent_fingerprint: str = ""
    dispatched_at: str = ""
    progress: dict[str, Any] = field(default_factory=dict)
    details: str = ""
    resume_history: list[ExecutionPackage] = field(default_factory=list)


class ReferenceFakeExecutorAdapter(ExecutorAdapter):
    """In-memory, deterministic, zero-external-dependency ExecutorAdapter test double.

    Provides mechanical validation of canonical execution contracts with no network,
    no disk I/O, no subprocess spawning, and no semantic decision heuristics.
    """

    def __init__(
        self,
        capabilities: ExecutorCapabilities | None = None,
        role_mapping: RoleMapping | None = None,
        default_initial_state: CanonicalTaskState | str = CanonicalTaskState.ACCEPTED,
        auto_complete: bool = False,
    ) -> None:
        self._capabilities = capabilities or DEFAULT_CAPABILITIES
        self._role_mapping = role_mapping or DEFAULT_ROLE_MAPPING
        self._default_initial_state = parse_state(default_initial_state)
        self._auto_complete = auto_complete

        # Private in-memory task state indexed by canonical_task_id
        self._tasks: dict[str, _InMemoryTaskRecord] = {}
        # Private index of adapter_handle -> canonical_task_id
        self._handles: dict[str, str] = {}
        # Idempotency index: idempotency_key -> (canonical_task_id, intent_fingerprint, adapter_handle, DispatchResult)
        self._idempotency_index: dict[str, tuple[str, str, str, DispatchResult]] = {}
        # Successful lifecycle operations are replayable only for their exact bound request.
        self._cancel_replays: dict[tuple[str, str], CancelResult] = {}
        self._resume_replays: dict[str, tuple[str, str, str, ResumeResult]] = {}

        # Mechanical effect counters for auditing and zero-IO verification
        self.validation_count: int = 0
        self.dispatch_count: int = 0
        self.status_count: int = 0
        self.result_count: int = 0
        self.cancel_count: int = 0
        self.resume_count: int = 0
        self.external_io_count: int = 0

    @property
    def executor_id(self) -> str:
        return self._capabilities.executor_id

    def capabilities(self) -> ExecutorCapabilities:
        """Return static advertised capabilities."""
        return self._capabilities

    def role_mapping(self) -> RoleMapping:
        """Return deterministic role mapping."""
        return self._role_mapping

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        """Pure read check whether package can be executed by this adapter."""
        self.validation_count += 1
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")

        errors: list[str] = []

        # 1. Canonical role supported and mapped
        if not self._capabilities.supports_role(package.canonical_role):
            errors.append(f"{CAPABILITY_MISMATCH}: Canonical role {package.canonical_role!r} not supported by executor")
        elif not self._role_mapping.has_role(package.canonical_role):
            errors.append(f"{ROLE_MAPPING_NOT_FOUND}: Canonical role {package.canonical_role!r} has no mapped target role")

        # 2. Execution mode check
        req_mode = package.capability_requirements.get("execution_mode")
        if req_mode is not None:
            if not isinstance(req_mode, str) or not self._capabilities.supports_mode(req_mode):
                errors.append(
                    f"{CAPABILITY_MISMATCH}: Execution mode {req_mode!r} not supported by executor (supported: {self._capabilities.supported_execution_modes})"
                )

        # 3. Isolation mode check
        req_iso = package.capability_requirements.get("isolation_mode") or package.capability_requirements.get("isolation")
        if req_iso is not None:
            if not isinstance(req_iso, str) or not self._capabilities.supports_isolation(req_iso):
                errors.append(
                    f"{CAPABILITY_MISMATCH}: Isolation mode {req_iso!r} not supported by executor (supported: {self._capabilities.supported_isolation_modes})"
                )

        # 4. Timeout check
        req_timeout = (
            package.capability_requirements.get("timeout_seconds")
            or package.constraints.get("timeout_seconds")
            or package.constraints.get("max_timeout_seconds")
        )
        if req_timeout is not None:
            if self._capabilities.max_timeout_seconds is not None:
                if not isinstance(req_timeout, (int, float)) or req_timeout > self._capabilities.max_timeout_seconds:
                    errors.append(
                        f"{CAPABILITY_MISMATCH}: Requested timeout {req_timeout}s exceeds executor maximum {self._capabilities.max_timeout_seconds}s"
                    )

        # 5. Feature requirement checks
        feature_checks = (
            ("requires_cancellation", self._capabilities.supports_task_cancellation, "task cancellation"),
            ("requires_resume", self._capabilities.supports_task_resume, "task resume"),
            ("requires_structured_result", self._capabilities.supports_structured_result, "structured result"),
            ("requires_streaming_events", self._capabilities.supports_streaming_events, "streaming events"),
            ("requires_working_directory", self._capabilities.supports_working_directory, "working directory"),
            ("requires_artifact_transport", self._capabilities.supports_artifact_transport, "artifact transport"),
        )
        for req_key, cap_supported, feature_name in feature_checks:
            if package.capability_requirements.get(req_key) is True and not cap_supported:
                errors.append(
                    f"{CAPABILITY_MISMATCH}: Requirement {req_key} ({feature_name}) is not supported by executor"
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        """Dispatch execution package to in-memory executor state."""
        self.dispatch_count += 1
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")

        # 1. Validate package; fail closed if invalid
        val = self.validate_package(package)
        if not val.valid:
            raise ValueError(f"{PACKAGE_INVALID}: Dispatch validation failed: {'; '.join(val.errors)}")

        # 2. Check idempotency: (idempotency_key, intent_fingerprint)
        idem_key = package.idempotency_key
        if idem_key in self._idempotency_index:
            prev_cid, prev_fp, prev_handle, prev_dispatch = self._idempotency_index[idem_key]
            if prev_fp == package.intent_fingerprint:
                # Idempotent resend: recover and return existing dispatch
                return prev_dispatch
            else:
                # Idempotency conflict: key reused with mutated intent
                raise ValueError(
                    f"IDEMPOTENCY_CONFLICT: Idempotency key {idem_key!r} already used with different intent fingerprint"
                )

        # 3. Canonical task id duplicate check
        if package.canonical_task_id in self._tasks:
            raise ValueError(
                f"DUPLICATE_CANONICAL_TASK_ID: Task {package.canonical_task_id!r} has already been dispatched"
            )

        # 4. Generate deterministic adapter-local handle
        adapter_handle = f"ref-handle-{package.canonical_task_id}"

        # 5. Determine initial state
        initial_state = CanonicalTaskState.COMPLETED if self._auto_complete else self._default_initial_state
        dispatch_time = "2026-08-21T00:00:00Z"

        dispatch_result = DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=adapter_handle,
            initial_state=initial_state,
            dispatch_time=dispatch_time,
        )

        record = _InMemoryTaskRecord(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=adapter_handle,
            package=package,
            state=initial_state,
            idempotency_key=package.idempotency_key,
            intent_fingerprint=package.intent_fingerprint,
            dispatched_at=dispatch_time,
            progress={"step": "dispatched", "total_steps": 1},
            details="Dispatched to reference fake executor",
        )

        if initial_state == CanonicalTaskState.COMPLETED:
            target_role = self._role_mapping.get_target_role(package.canonical_role)
            record.result = CanonicalResult.success(
                canonical_task_id=package.canonical_task_id,
                executor_id=self._capabilities.executor_id,
                result_data={
                    "output": f"Executed by {target_role}: {package.instruction}",
                    "canonical_role": package.canonical_role,
                    "executor_local_role": target_role,
                },
                correlation_id=package.correlation_id,
            )

        self._tasks[package.canonical_task_id] = record
        self._handles[adapter_handle] = package.canonical_task_id
        self._idempotency_index[package.idempotency_key] = (
            package.canonical_task_id,
            package.intent_fingerprint,
            adapter_handle,
            dispatch_result,
        )

        return dispatch_result

    def _get_verified_task(self, canonical_task_id: str, adapter_handle: str) -> _InMemoryTaskRecord:
        """Internal helper to look up task by canonical_task_id and verify adapter_handle."""
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found in adapter state")

        record = self._tasks[canonical_task_id]
        if record.adapter_handle != adapter_handle:
            raise ValueError(
                f"{ADAPTER_PROTOCOL_ERROR}: Adapter handle {adapter_handle!r} does not match record for {canonical_task_id!r}"
            )
        return record

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        """Query execution state of dispatched task."""
        self.status_count += 1
        record = self._get_verified_task(canonical_task_id, adapter_handle)
        return TaskStatusResult(
            canonical_task_id=canonical_task_id,
            state=record.state,
            progress=record.progress,
            details=record.details,
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        """Fetch terminal CanonicalResult for task."""
        self.result_count += 1
        record = self._get_verified_task(canonical_task_id, adapter_handle)

        if not record.state.is_terminal and record.state != CanonicalTaskState.UNKNOWN:
            raise ValueError(
                f"TASK_NOT_TERMINAL: Task {canonical_task_id!r} is in non-terminal state {record.state.value}"
            )

        if record.result is not None:
            return record.result

        if record.state == CanonicalTaskState.COMPLETED:
            target_role = self._role_mapping.get_target_role(record.package.canonical_role)
            return CanonicalResult.success(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                result_data={
                    "output": f"Executed by {target_role}: {record.package.instruction}",
                    "canonical_role": record.package.canonical_role,
                },
                correlation_id=record.package.correlation_id,
            )
        elif record.state == CanonicalTaskState.FAILED:
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_code=EXECUTION_FAILED,
                error_message=record.details or "Execution failed in reference adapter",
                correlation_id=record.package.correlation_id,
            )
        elif record.state == CanonicalTaskState.CANCELLED:
            return CanonicalResult.cancelled(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_message=record.details or "Execution cancelled",
                correlation_id=record.package.correlation_id,
            )
        elif record.state == CanonicalTaskState.UNKNOWN:
            return CanonicalResult.unknown(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_message=record.details or "Task execution state is unknown",
                correlation_id=record.package.correlation_id,
            )
        else:
            raise ValueError(f"{TASK_STATE_UNKNOWN}: State {record.state.value} is not recognized")

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        """Request cancellation of active task."""
        if not self._capabilities.supports_task_cancellation:
            raise ValueError(f"{CANCEL_UNSUPPORTED}: Executor does not support cancellation")

        record = self._get_verified_task(canonical_task_id, adapter_handle)
        replay_key = (canonical_task_id, adapter_handle)
        replay_result = self._cancel_replays.get(replay_key)
        if replay_result is not None:
            return replay_result

        if record.state.is_terminal:
            raise ValueError(
                f"TASK_ALREADY_TERMINAL: Task {canonical_task_id!r} is already in terminal state {record.state.value}"
            )
        if record.state == CanonicalTaskState.UNKNOWN:
            raise ValueError(f"{TASK_STATE_UNKNOWN}: Cannot cancel task in UNKNOWN state")

        validate_transition(record.state, CanonicalTaskState.CANCELLED)
        record.state = CanonicalTaskState.CANCELLED
        record.details = "Task cancelled by request"
        record.result = CanonicalResult.cancelled(
            canonical_task_id=canonical_task_id,
            executor_id=self._capabilities.executor_id,
            error_message="Task cancelled by request",
            correlation_id=record.package.correlation_id,
        )
        cancel_result = CancelResult(
            canonical_task_id=canonical_task_id,
            cancelled=True,
            state=CanonicalTaskState.CANCELLED,
        )
        self._cancel_replays[replay_key] = cancel_result
        self.cancel_count += 1
        return cancel_result

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        """Resume waiting task with new input."""
        if not self._capabilities.supports_task_resume:
            raise ValueError(f"{RESUME_UNSUPPORTED}: Executor does not support task resume")

        record = self._get_verified_task(canonical_task_id, adapter_handle)
        if not isinstance(resume_package, ExecutionPackage):
            raise TypeError(
                f"resume_package must be an ExecutionPackage, got {type(resume_package).__name__}"
            )

        if resume_package.canonical_task_id != canonical_task_id:
            raise ValueError(
                f"TASK_ID_MISMATCH: Resume package canonical_task_id {resume_package.canonical_task_id!r} != {canonical_task_id!r}"
            )

        val = self.validate_package(resume_package)
        if not val.valid:
            raise ValueError(f"{PACKAGE_INVALID}: Invalid resume package: {'; '.join(val.errors)}")

        previous = self._resume_replays.get(resume_package.idempotency_key)
        if previous is not None:
            previous_task_id, previous_handle, previous_fingerprint, previous_result = previous
            if (
                previous_task_id != canonical_task_id
                or previous_handle != adapter_handle
                or previous_fingerprint != resume_package.intent_fingerprint
            ):
                raise ValueError(
                    f"IDEMPOTENCY_CONFLICT: Resume idempotency key "
                    f"{resume_package.idempotency_key!r} is bound to a different task, handle, or intent"
                )
            return previous_result

        if record.state != CanonicalTaskState.WAITING:
            raise ValueError(
                f"TASK_NOT_WAITING: Task {canonical_task_id!r} is in state {record.state.value}, not WAITING"
            )

        validate_transition(record.state, CanonicalTaskState.RUNNING)
        record.state = CanonicalTaskState.RUNNING
        record.resume_history.append(resume_package)
        record.details = f"Task resumed with package {resume_package.package_id}"
        resume_result = ResumeResult(
            canonical_task_id=canonical_task_id,
            state=CanonicalTaskState.RUNNING,
        )
        self._resume_replays[resume_package.idempotency_key] = (
            canonical_task_id,
            adapter_handle,
            resume_package.intent_fingerprint,
            resume_result,
        )
        self.resume_count += 1
        return resume_result

    # --------------------------------------------------------------------------
    # Deterministic test fixture simulation helpers
    # --------------------------------------------------------------------------

    def simulate_transition(
        self,
        canonical_task_id: str,
        target_state: CanonicalTaskState | str,
        details: str = "",
        result: CanonicalResult | None = None,
    ) -> None:
        """Deterministically transition task state for fixture testing."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        target = parse_state(target_state)
        validate_transition(record.state, target)
        record.state = target
        if details:
            record.details = details
        if result is not None:
            record.result = result
        elif target == CanonicalTaskState.COMPLETED and record.result is None:
            record.result = CanonicalResult.success(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                result_data={"output": f"Simulated completion: {record.package.instruction}"},
                correlation_id=record.package.correlation_id,
            )
        elif target == CanonicalTaskState.FAILED and record.result is None:
            record.result = CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_code=EXECUTION_FAILED,
                error_message=details or "Simulated failure",
                correlation_id=record.package.correlation_id,
            )
        elif target == CanonicalTaskState.CANCELLED and record.result is None:
            record.result = CanonicalResult.cancelled(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_message=details or "Simulated cancellation",
                correlation_id=record.package.correlation_id,
            )
        elif target == CanonicalTaskState.UNKNOWN and record.result is None:
            record.result = CanonicalResult.unknown(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_message=details or "Simulated unknown state",
                correlation_id=record.package.correlation_id,
            )

    def simulate_progress(
        self,
        canonical_task_id: str,
        progress: dict[str, Any],
        details: str = "",
    ) -> None:
        """Update progress metrics deterministically."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        record.progress = dict(progress)
        if details:
            record.details = details

    def simulate_running(self, canonical_task_id: str, details: str = "Task is running") -> None:
        """Transition task from ACCEPTED/QUEUED/WAITING/UNKNOWN to RUNNING."""
        self.simulate_transition(canonical_task_id, CanonicalTaskState.RUNNING, details=details)

    def simulate_waiting(self, canonical_task_id: str, details: str = "Task is waiting for input") -> None:
        """Transition task to WAITING."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        if record.state != CanonicalTaskState.RUNNING:
            self.simulate_running(canonical_task_id)
        self.simulate_transition(canonical_task_id, CanonicalTaskState.WAITING, details=details)

    def simulate_completion(
        self,
        canonical_task_id: str,
        result_data: dict[str, Any] | None = None,
        output_artifacts: tuple[dict, ...] | list[dict] = (),
    ) -> None:
        """Transition task to COMPLETED."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        if record.state in (CanonicalTaskState.ACCEPTED, CanonicalTaskState.QUEUED):
            self.simulate_running(canonical_task_id)
        validate_transition(record.state, CanonicalTaskState.COMPLETED)
        record.state = CanonicalTaskState.COMPLETED
        record.details = "Execution completed successfully"
        record.result = CanonicalResult.success(
            canonical_task_id=canonical_task_id,
            executor_id=self._capabilities.executor_id,
            result_data=result_data if result_data is not None else {"output": f"Executed instruction: {record.package.instruction}"},
            output_artifacts=output_artifacts,
            correlation_id=record.package.correlation_id,
        )

    def simulate_failure(
        self,
        canonical_task_id: str,
        error_code: str = "EXECUTION_FAILED",
        error_message: str = "Execution failed",
        details: Any = None,
    ) -> None:
        """Transition task to FAILED."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        if record.state in (CanonicalTaskState.ACCEPTED, CanonicalTaskState.QUEUED):
            self.simulate_running(canonical_task_id)
        validate_transition(record.state, CanonicalTaskState.FAILED)
        record.state = CanonicalTaskState.FAILED
        record.details = error_message
        record.result = CanonicalResult.failure(
            canonical_task_id=canonical_task_id,
            executor_id=self._capabilities.executor_id,
            error_code=error_code,
            error_message=error_message,
            details=details,
            correlation_id=record.package.correlation_id,
        )

    def simulate_unknown(self, canonical_task_id: str, details: str = "Executor connection lost") -> None:
        """Transition task to UNKNOWN."""
        if canonical_task_id not in self._tasks:
            raise KeyError(f"{TASK_NOT_FOUND}: Task {canonical_task_id!r} not found")
        record = self._tasks[canonical_task_id]
        if record.state in (CanonicalTaskState.ACCEPTED, CanonicalTaskState.RUNNING):
            validate_transition(record.state, CanonicalTaskState.UNKNOWN)
            record.state = CanonicalTaskState.UNKNOWN
            record.details = details
            record.result = CanonicalResult.unknown(
                canonical_task_id=canonical_task_id,
                executor_id=self._capabilities.executor_id,
                error_message=details,
                correlation_id=record.package.correlation_id,
            )

    def get_task_record(self, canonical_task_id: str) -> _InMemoryTaskRecord | None:
        """Inspect internal record for test assertion."""
        return self._tasks.get(canonical_task_id)

    def clear(self) -> None:
        """Clear all in-memory task state."""
        self._tasks.clear()
        self._handles.clear()
        self._idempotency_index.clear()
        self._cancel_replays.clear()
        self._resume_replays.clear()
