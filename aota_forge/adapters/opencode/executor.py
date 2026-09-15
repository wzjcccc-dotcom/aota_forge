"""OpenCode ExecutorAdapter (AF #56 M2/W1).

Thin mechanical translation layer between the EXISTING accepted canonical
execution contracts (ExecutionPackage / ExecutorAdapter / CanonicalResult /
ExecutorCapabilities) and the pinned OpenCode legacy-root host contract. This
is the SAME adapter protocol as the Hermes adapter; no second execution engine,
no second result model, and no OpenCode-private type escape exists here.

Hard boundaries (AF #56 M2):

- ``adapter_handle`` IS the exact OpenCode Worker session id (ADAPTER_HANDLE_IS_SESSION_ID=yes).
  One AF Worker task owns one dedicated Worker session.
- Worker session directory comes ONLY from the trusted AF server-side binding
  seam (``worker_directory_resolver`` derived from the governed Worker env
  resolver, or an explicit trusted construction input). It is NEVER taken from
  TaskHandoff free text, model arguments, OpenCode model output, ambient AF
  process CWD, or folder-name inference.
- ``parentID`` is set from the trusted runtime origin session when available:
  host lineage ONLY (PARENT_ID_USED_FOR_HOST_LINEAGE=yes /
  PARENT_ID_USED_FOR_AF_AUTHORITY=no). Dispatch correctness never depends on it.
- Host status is a mechanical observation. ``busy`` -> RUNNING, ``retry`` ->
  WAITING; ``idle``/absent NEVER means AF semantic completion. Mechanical run
  termination may be inferred only from exact-session evidence (the dispatched
  assistant turn terminated). MECHANICAL_TERMINAL_SUCCESS != AF_SEMANTIC_SUCCESS:
  the existing durable semantic-return gate still decides governed success.
- Unknown/ambiguous host evidence stays UNKNOWN; nothing is fabricated.
- The Worker still finishes through the governed AF path
  (``aota.invoke`` -> handoff.write(result) -> task.return); the OpenCode final
  assistant text is NEVER a task return (OPENCODE_FINAL_TEXT_IS_TASK_RETURN=no).
- Resume is unsupported and fails closed truthfully: sending another prompt to
  an existing session is NOT equivalent to AF task resume semantics.
- Raw assistant text never becomes the governed result authority; the adapter
  projects bounded mechanical execution facts only.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from aota_forge.adapters.opencode.host_client import (
    OpenCodeHostClient,
    OpenCodeHostError,
    OpenCodeSessionNotFoundError,
    TurnEvidence,
    session_status_entry,
    turn_evidence,
)
from aota_forge.core.contracts.canonical import canonicalize
from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.durable_state import is_bound_origin_session_ref
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.roles import (
    RoleMapping,
    RoleMappingNotFoundError,
    validate_canonical_role,
)
from aota_forge.core.execution.state import CanonicalTaskState

OPENCODE_EXECUTOR_ID: str = "opencode"
OPENCODE_ADAPTER_KIND: str = "opencode_host_adapter"

# Truthful capability / behavior markers.
ADAPTER_HANDLE_IS_SESSION_ID = True
PARENT_ID_USED_FOR_HOST_LINEAGE = True
PARENT_ID_USED_FOR_AF_AUTHORITY = False
HOST_SUCCESS_IS_SEMANTIC_SUCCESS = False
OPENCODE_FINAL_TEXT_IS_TASK_RETURN = False
SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN = True
MODEL_SUPPLIED_CWD_AUTHORITY = False
AMBIENT_CWD_IS_AUTHORITY = False
SECOND_EXECUTION_ENGINE = False
SECOND_RESULT_MODEL = False

# Frozen host status -> CanonicalTaskState mapping (conservative; idle/absent is
# deliberately NOT mapped to any terminal state).
OPENCODE_STATUS_MAP: dict[str, CanonicalTaskState] = {
    "busy": CanonicalTaskState.RUNNING,
    "retry": CanonicalTaskState.WAITING,
}

OPENCODE_HOST_METADATA_TASK_KEY = "aota_canonical_task_id"
OPENCODE_HOST_METADATA_SCHEMA_KEY = "aota_schema"
OPENCODE_HOST_METADATA_SCHEMA = "af56-m2-worker-v1"

# One bounded mechanical host note: the pinned host exposes the single AOTA
# MCP entry under the sanitized tool name. This is host translation only and
# carries no AF role semantics (AF startup guidance remains the single semantic
# bootstrap).
HOST_TOOL_BINDING_NOTE = (
    "Host tool binding (mechanical): invoke governed AF operations through the single "
    "MCP tool named aota_aota_invoke with arguments {operation, arguments}."
)

_CAPABILITY_REQUIREMENT_KEYS: frozenset[str] = frozenset(
    {
        "execution_mode",
        "isolation",
        "isolation_mode",
        "timeout_seconds",
        "requires_cancellation",
        "requires_resume",
        "requires_structured_result",
        "requires_streaming_events",
        "requires_working_directory",
        "requires_artifact_transport",
    }
)

_BOOLEAN_CAPABILITY_REQUIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("requires_cancellation", "supports_task_cancellation", "task cancellation"),
    ("requires_resume", "supports_task_resume", "task resume"),
    ("requires_structured_result", "supports_structured_result", "structured result"),
    ("requires_streaming_events", "supports_streaming_events", "streaming events"),
    ("requires_working_directory", "supports_working_directory", "working directory"),
    ("requires_artifact_transport", "supports_artifact_transport", "artifact transport"),
)


class OpenCodeAdapterError(Exception):
    """Base exception for OpenCode adapter mechanical errors."""

    def __init__(self, message: str, code: str = "ADAPTER_PROTOCOL_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OpenCodeHostUnavailableAdapterError(OpenCodeAdapterError):
    def __init__(self, message: str = "OpenCode host client is unavailable") -> None:
        super().__init__(message, code="EXECUTOR_UNAVAILABLE")


class OpenCodeAdapterProtocolError(OpenCodeAdapterError):
    def __init__(self, message: str = "OpenCode host protocol violation") -> None:
        super().__init__(message, code="ADAPTER_PROTOCOL_ERROR")


class OpenCodeDispatchFailureError(OpenCodeAdapterError):
    def __init__(self, message: str = "OpenCode dispatch rejected") -> None:
        super().__init__(message, code="DISPATCH_REJECTED")


class OpenCodeWorkerDirectoryUnavailableError(OpenCodeAdapterError):
    """Trusted AF worker-directory binding could not be resolved (fail closed)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="WORKER_DIRECTORY_UNAVAILABLE")


class OpenCodeResumeUnsupportedError(OpenCodeAdapterError):
    """AF task-resume semantics are not safely implementable on this host."""

    def __init__(self, message: str = "OpenCode adapter does not support AF task resume") -> None:
        super().__init__(message, code="RESUME_UNSUPPORTED")


def default_opencode_capabilities(
    supported_canonical_roles: tuple[str, ...] = ("coder", "planner", "reviewer", "steward"),
    *,
    concurrency_limit: int | None = None,
    max_timeout_seconds: int | None = None,
) -> ExecutorCapabilities:
    """Advertise ONLY capabilities actually implemented and proven.

    resume / streaming / structured result / artifact transport are NOT
    claimed (RESUME_SUPPORT=unsupported_fail_closed).
    """
    return ExecutorCapabilities(
        executor_id=OPENCODE_EXECUTOR_ID,
        adapter_kind=OPENCODE_ADAPTER_KIND,
        supported_execution_modes=("async",),
        supports_streaming_events=False,
        supports_task_cancellation=True,
        supports_task_resume=False,
        supports_structured_result=False,
        supported_canonical_roles=tuple(supported_canonical_roles),
        supported_isolation_modes=("worktree",),
        supports_working_directory=True,
        supports_artifact_transport=False,
        max_timeout_seconds=max_timeout_seconds,
        concurrency_limit=concurrency_limit,
    )


def build_opencode_host_payload(package: ExecutionPackage) -> dict[str, Any]:
    """Deterministic canonical projection consumed by the trusted binding seam.

    Carries the package's trusted ``working_context`` verbatim inside a bounded
    context block (the same shape the governed Worker env resolver consumes);
    it adds no authority and rewrites nothing.
    """
    if not isinstance(package, ExecutionPackage):
        raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")
    return canonicalize(
        {
            "context": {
                "canonical_task_id": package.canonical_task_id,
                "project_id": package.project_id,
                "subject_ref": package.subject_ref,
                "correlation_id": package.correlation_id,
                "idempotency_key": package.idempotency_key,
                "working_context": dict(package.working_context),
            },
            "canonical_role": package.canonical_role,
            "instruction": package.instruction,
            "package_id": package.package_id,
            "operation": package.operation,
        },
        path="opencode_host_payload",
    )


def opencode_status_to_canonical_state(status: Mapping[str, Any] | None) -> CanonicalTaskState:
    """Map one exact-session status entry to a NON-TERMINAL observation.

    ``None`` (idle/omitted) maps to UNKNOWN deliberately: the caller must
    consult exact-session turn evidence before any terminal projection. This
    function never fabricates COMPLETED/FAILED.
    """
    if status is None:
        return CanonicalTaskState.UNKNOWN
    status_type = status.get("type")
    if not isinstance(status_type, str):
        return CanonicalTaskState.UNKNOWN
    return OPENCODE_STATUS_MAP.get(status_type.strip().lower(), CanonicalTaskState.UNKNOWN)


def _result_observation_uncertainty(
    canonical_task_id: str,
    error_code: str,
    error_message: str,
    correlation_id: str,
    *,
    state: CanonicalTaskState = CanonicalTaskState.UNKNOWN,
) -> CanonicalResult:
    """Untrustworthy observation -> recoverable uncertainty (never terminal)."""
    return CanonicalResult.failure(
        canonical_task_id=canonical_task_id,
        executor_id=OPENCODE_EXECUTOR_ID,
        error_code=error_code,
        error_message=error_message,
        retryable=True,
        exit_code=None,
        status="unknown",
        canonical_task_state=state.value,
        correlation_id=correlation_id,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_title(canonical_task_id: str) -> str:
    return f"af-worker:{canonical_task_id}"[:120]


class OpenCodeAdapter(ExecutorAdapter):
    """OpenCode execution host adapter (AF #56 M2).

    Requires either an injected host client (tests / bounded harness) or the
    production-constructed ``OpenCodeHostClient`` over the operator-owned
    loopback endpoint. The trusted worker directory is resolved per dispatch
    through the AF-owned seam; the adapter itself decides no directory.
    """

    def __init__(
        self,
        host_client: OpenCodeHostClient | None = None,
        capabilities: ExecutorCapabilities | None = None,
        role_mapping: RoleMapping | None = None,
        runtime_config: Any | None = None,
        model_prompt_composer: Callable[[ExecutionPackage], str] | None = None,
        worker_directory_resolver: Callable[[ExecutionPackage], str | None] | None = None,
        trusted_worker_directory: str | None = None,
        origin_session_ref: str | None = None,
    ) -> None:
        if host_client is not None and not (
            isinstance(host_client, OpenCodeHostClient) or hasattr(host_client, "create_session")
        ):
            raise TypeError(
                "host_client must implement the OpenCode host operations (create_session/"
                f"prompt/status/messages/abort) or be an OpenCodeHostClient, got {type(host_client).__name__}"
            )
        self._host_client = host_client
        self._capabilities = capabilities or default_opencode_capabilities()
        if self._capabilities.executor_id != OPENCODE_EXECUTOR_ID:
            raise ValueError(
                f"capabilities executor_id must be {OPENCODE_EXECUTOR_ID!r}, "
                f"got {self._capabilities.executor_id!r}"
            )
        self._role_mapping = role_mapping
        self._runtime_config = runtime_config
        if model_prompt_composer is not None and not callable(model_prompt_composer):
            raise TypeError("model_prompt_composer must be callable or None")
        self._model_prompt_composer = model_prompt_composer
        if worker_directory_resolver is not None and not callable(worker_directory_resolver):
            raise TypeError("worker_directory_resolver must be callable or None")
        self._worker_directory_resolver = worker_directory_resolver
        if trusted_worker_directory is not None:
            if not isinstance(trusted_worker_directory, str) or not trusted_worker_directory.strip():
                raise ValueError("trusted_worker_directory must be a non-empty string or None")
            if not trusted_worker_directory.strip().startswith("/"):
                raise ValueError("trusted_worker_directory must be an absolute path")
            trusted_worker_directory = trusted_worker_directory.strip()
        self._trusted_worker_directory = trusted_worker_directory
        if origin_session_ref is not None:
            if not isinstance(origin_session_ref, str) or not origin_session_ref.strip():
                raise ValueError("origin_session_ref must be a non-empty string or None")
            origin_session_ref = origin_session_ref.strip()
        self._origin_session_ref = origin_session_ref

        self._dispatch_replays: dict[str, tuple[str, DispatchResult]] = {}
        self._cancel_replays: dict[tuple[str, str], CancelResult] = {}
        self._handle_tasks: dict[str, str] = {}
        self._task_handles: dict[str, str] = {}
        self._session_directories: dict[str, str] = {}

    # -- capabilities / validation -------------------------------------------

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def _verify_task_handle(self, canonical_task_id: str, adapter_handle: str) -> None:
        """Require an exact, restart-safe task<->session binding.

        Process-local binding first; otherwise the exact host session row must
        itself carry this canonical task identity in its trusted-runtime
        metadata. No heuristic session search exists.
        """
        if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
            raise ValueError("canonical_task_id must be a non-empty string")
        if not isinstance(adapter_handle, str) or not adapter_handle.strip():
            raise ValueError("adapter_handle must be a non-empty string")
        handle_is_known = adapter_handle in self._handle_tasks
        task_is_known = canonical_task_id in self._task_handles
        if (
            handle_is_known
            and task_is_known
            and self._handle_tasks.get(adapter_handle) == canonical_task_id
            and self._task_handles.get(canonical_task_id) == adapter_handle
        ):
            return
        if not handle_is_known and not task_is_known and self._recover_durable_binding(
            canonical_task_id, adapter_handle
        ):
            return
        if handle_is_known and self._handle_tasks.get(adapter_handle) != canonical_task_id:
            raise OpenCodeAdapterError(
                f"TASK_ID_MISMATCH: adapter handle {adapter_handle!r} belongs to canonical "
                f"task {self._handle_tasks.get(adapter_handle)!r}, not {canonical_task_id!r}",
                code="TASK_ID_MISMATCH",
            )
        if task_is_known and self._task_handles.get(canonical_task_id) != adapter_handle:
            raise OpenCodeAdapterError(
                f"TASK_ID_MISMATCH: canonical task {canonical_task_id!r} is bound to adapter "
                f"handle {self._task_handles.get(canonical_task_id)!r}, not {adapter_handle!r}",
                code="TASK_ID_MISMATCH",
            )
        raise OpenCodeAdapterError(
            f"TASK_HANDLE_NOT_FOUND: no adapter binding exists for canonical task "
            f"{canonical_task_id!r} and session {adapter_handle!r}",
            code="TASK_HANDLE_NOT_FOUND",
        )

    def _recover_durable_binding(self, canonical_task_id: str, adapter_handle: str) -> bool:
        """Restart recovery: exact host session metadata must agree exactly.

        The host session row persists the trusted-runtime task identity written
        at dispatch; disagreement or absence fails closed (never adopted).
        """
        if self._host_client is None:
            return False
        try:
            session = self._host_client.get_session(
                adapter_handle, directory=self._session_directories.get(adapter_handle)
            )
        except Exception:
            return False
        metadata = session.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get(OPENCODE_HOST_METADATA_TASK_KEY) != canonical_task_id:
            return False
        directory = session.get("directory")
        if isinstance(directory, str) and directory.strip():
            self._session_directories[adapter_handle] = directory
        self._handle_tasks[adapter_handle] = canonical_task_id
        self._task_handles[canonical_task_id] = adapter_handle
        return True

    def _resolve_host_profile(self, canonical_role: str) -> str:
        validate_canonical_role(canonical_role)
        mapping = self._role_mapping
        if mapping is None:
            raise RoleMappingNotFoundError(OPENCODE_EXECUTOR_ID, canonical_role)
        return mapping.get_target_role(canonical_role)

    def validate_package(self, package: ExecutionPackage) -> ValidationResult:
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")
        errors: list[str] = []

        # 1. canonical role must be mechanically mapped
        try:
            self._resolve_host_profile(package.canonical_role)
        except RoleMappingNotFoundError:
            errors.append(
                f"ROLE_MAPPING_NOT_FOUND: canonical role {package.canonical_role!r} "
                f"has no OpenCode host profile mapping"
            )

        # 2. capability requirement vocabulary must be known
        requirements = package.capability_requirements
        for key in sorted(requirements, key=str):
            if key not in _CAPABILITY_REQUIREMENT_KEYS:
                errors.append(f"PACKAGE_INVALID: unknown capability requirement key {key!r}")

        # 3. execution mode
        requirement_mode = requirements.get("execution_mode")
        constraint_mode = package.constraints.get("execution_mode")
        if (
            "execution_mode" in requirements
            and "execution_mode" in package.constraints
            and requirement_mode != constraint_mode
        ):
            errors.append(
                "PACKAGE_INVALID: conflicting execution_mode requirements between "
                "capability_requirements and constraints"
            )
        for req_mode in (requirement_mode, constraint_mode):
            if req_mode is None:
                continue
            if not isinstance(req_mode, str) or not self._capabilities.supports_mode(req_mode):
                errors.append(
                    f"CAPABILITY_MISMATCH: execution mode {req_mode!r} not supported by "
                    f"the OpenCode adapter"
                )

        # 4. isolation mode
        isolation_requirements = [
            requirements[key] for key in ("isolation", "isolation_mode") if key in requirements
        ]
        if "isolation" in package.constraints:
            isolation_requirements.append(package.constraints["isolation"])
        if "isolation_mode" in package.constraints:
            isolation_requirements.append(package.constraints["isolation_mode"])
        for req_iso in isolation_requirements:
            if not isinstance(req_iso, str) or not self._capabilities.supports_isolation(req_iso):
                errors.append(
                    f"CAPABILITY_MISMATCH: isolation mode {req_iso!r} not supported by "
                    f"the OpenCode adapter"
                )

        # 5. timeout
        req_timeout = (
            requirements["timeout_seconds"]
            if "timeout_seconds" in requirements
            else package.constraints.get("timeout_seconds")
        )
        if req_timeout is not None:
            if not isinstance(req_timeout, (int, float)) or isinstance(req_timeout, bool):
                errors.append(f"PACKAGE_INVALID: timeout_seconds must be numeric, got {req_timeout!r}")
            elif (
                self._capabilities.max_timeout_seconds is not None
                and req_timeout > self._capabilities.max_timeout_seconds
            ):
                errors.append(
                    f"CAPABILITY_MISMATCH: requested timeout {req_timeout}s exceeds "
                    f"OpenCode max timeout {self._capabilities.max_timeout_seconds}s"
                )

        # 6. typed feature requirements (resume/streaming/structured/artifacts denied)
        for req_key, capability_name, feature_name in _BOOLEAN_CAPABILITY_REQUIREMENTS:
            if req_key not in requirements:
                continue
            requested = requirements[req_key]
            if type(requested) is not bool:
                errors.append(f"PACKAGE_INVALID: capability requirement {req_key!r} must be boolean")
            elif requested and not getattr(self._capabilities, capability_name):
                errors.append(
                    f"CAPABILITY_MISMATCH: task requires {feature_name} but the OpenCode "
                    f"adapter does not support it"
                )

        # 7. resume operation is unsupported (truthful fail closed)
        if package.operation == "task_resume" and not self._capabilities.supports_task_resume:
            errors.append(
                "RESUME_UNSUPPORTED: task_resume requested but the OpenCode adapter does not "
                "implement AF task resume semantics"
            )

        if errors:
            return ValidationResult(valid=False, errors=tuple(errors))
        return ValidationResult(valid=True, errors=())

    # -- dispatch -------------------------------------------------------------

    def _resolve_worker_directory(self, package: ExecutionPackage) -> str:
        """Trusted AF binding -> exact Worker session directory (fail closed)."""
        directory: str | None = None
        if self._worker_directory_resolver is not None:
            try:
                resolved = self._worker_directory_resolver(package)
            except Exception as exc:  # noqa: BLE001 - trusted seam failure is fail-closed
                raise OpenCodeWorkerDirectoryUnavailableError(
                    "trusted Worker directory resolver failed: "
                    f"{type(exc).__name__}: {getattr(exc, 'code', None) or exc}"
                ) from exc
            if resolved is not None:
                if not isinstance(resolved, str) or not resolved.strip():
                    raise OpenCodeWorkerDirectoryUnavailableError(
                        "trusted Worker directory resolver returned a non-string/empty value"
                    )
                directory = resolved.strip()
        if directory is None:
            directory = self._trusted_worker_directory
        if directory is None:
            raise OpenCodeWorkerDirectoryUnavailableError(
                "no trusted AF Worker directory binding is available for this dispatch "
                "(the model, TaskHandoff and ambient CWD are never directory authority)"
            )
        if not directory.startswith("/"):
            raise OpenCodeWorkerDirectoryUnavailableError(
                f"trusted Worker directory must be an absolute path, got {directory!r}"
            )
        return directory

    def _resolve_parent_id(self) -> str | None:
        origin = self._origin_session_ref
        if origin is None:
            return None
        if not is_bound_origin_session_ref(origin):
            return None
        return origin

    def _compose_model_facing_instruction(self, package: ExecutionPackage) -> str:
        composer = self._model_prompt_composer
        instruction = package.instruction
        if composer is not None:
            try:
                composed = composer(package)
            except Exception as exc:
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or not code:
                    code = "WORKER_STARTUP_GUIDANCE_UNAVAILABLE"
                raise OpenCodeAdapterError(
                    f"{code}: model-facing prompt composition failed: {type(exc).__name__}",
                    code=code,
                ) from exc
            if not isinstance(composed, str) or not composed.strip():
                raise OpenCodeAdapterError(
                    "WORKER_STARTUP_GUIDANCE_UNAVAILABLE: composer produced an empty "
                    "model-facing instruction",
                    code="WORKER_STARTUP_GUIDANCE_UNAVAILABLE",
                )
            instruction = composed
        return f"{instruction}\n\n{HOST_TOOL_BINDING_NOTE}"

    def _resolve_prompt_model(self, canonical_role: str) -> dict[str, str] | None:
        """Operator-owned provider/model binding -> pinned prompt model fields."""
        config = self._runtime_config
        if config is None:
            return None
        from aota_forge.runtime.config import resolve_binding_for_canonical_role

        binding = resolve_binding_for_canonical_role(canonical_role, config)
        provider = binding.provider if binding.provider is not None else getattr(config, "provider", None)
        model = binding.model if binding.model is not None else getattr(config, "model", None)
        if provider is None and model is None:
            return None
        if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
            raise OpenCodeAdapterError(
                "provider and model must both be operator-configured for an OpenCode worker "
                "dispatch (no partial model binding is accepted)",
                code="MODEL_BINDING_INCOMPLETE",
            )
        return {"providerID": provider.strip(), "modelID": model.strip()}

    def dispatch(self, package: ExecutionPackage) -> DispatchResult:
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be ExecutionPackage, got {type(package).__name__}")
        validation = self.validate_package(package)
        if not validation.valid:
            raise OpenCodeDispatchFailureError(
                f"DISPATCH_REJECTED: package validation failed: {list(validation.errors)}"
            )

        previous = self._dispatch_replays.get(package.idempotency_key)
        if previous is not None:
            previous_fingerprint, previous_result = previous
            if previous_fingerprint != package.intent_fingerprint:
                raise OpenCodeAdapterError(
                    f"IDEMPOTENCY_CONFLICT: idempotency key {package.idempotency_key!r} "
                    "already used with a different intent fingerprint",
                    code="IDEMPOTENCY_CONFLICT",
                )
            self._verify_task_handle(package.canonical_task_id, previous_result.adapter_handle)
            return previous_result

        if self._host_client is None:
            raise OpenCodeHostUnavailableAdapterError(
                "EXECUTOR_UNAVAILABLE: OpenCode host client is not configured"
            )

        directory = self._resolve_worker_directory(package)
        self._resolve_host_profile(package.canonical_role)  # fail closed before any host call
        instruction = self._compose_model_facing_instruction(package)
        prompt_model = self._resolve_prompt_model(package.canonical_role)
        parent_id = self._resolve_parent_id()

        try:
            session = self._host_client.create_session(
                directory=directory,
                parent_id=parent_id,
                title=_bounded_title(package.canonical_task_id),
                model=prompt_model,
                metadata={
                    OPENCODE_HOST_METADATA_TASK_KEY: package.canonical_task_id,
                    OPENCODE_HOST_METADATA_SCHEMA_KEY: OPENCODE_HOST_METADATA_SCHEMA,
                    "aota_package_id": package.package_id,
                    "aota_correlation_id": package.correlation_id,
                },
            )
        except OpenCodeHostError as exc:
            raise OpenCodeDispatchFailureError(
                f"{exc.code}: OpenCode session create failed: {exc.message}"
            ) from exc

        session_id = session["id"]
        session_directory = session.get("directory")
        trusted_directory = (
            session_directory if isinstance(session_directory, str) and session_directory else directory
        )
        self._session_directories[session_id] = trusted_directory

        try:
            self._host_client.submit_prompt_async(
                session_id,
                directory=trusted_directory,
                parts=[{"type": "text", "text": instruction}],
                model=prompt_model,
            )
        except OpenCodeHostError as exc:
            raise OpenCodeDispatchFailureError(
                f"{exc.code}: OpenCode prompt submission failed: {exc.message}"
            ) from exc

        dispatch_result = DispatchResult(
            canonical_task_id=package.canonical_task_id,
            adapter_handle=session_id,
            initial_state=CanonicalTaskState.RUNNING,
            dispatch_time=_now_iso(),
        )
        self._dispatch_replays[package.idempotency_key] = (
            package.intent_fingerprint,
            dispatch_result,
        )
        self._handle_tasks[session_id] = package.canonical_task_id
        self._task_handles[package.canonical_task_id] = session_id
        return dispatch_result

    # -- mechanical observation ----------------------------------------------

    def _session_directory(self, adapter_handle: str) -> str:
        directory = self._session_directories.get(adapter_handle)
        if isinstance(directory, str) and directory:
            return directory
        if self._host_client is None:
            raise OpenCodeHostUnavailableAdapterError(
                "EXECUTOR_UNAVAILABLE: OpenCode host client is not configured"
            )
        session = self._host_client.get_session(adapter_handle)
        row_directory = session.get("directory")
        if not isinstance(row_directory, str) or not row_directory.strip():
            raise OpenCodeAdapterProtocolError(
                "exact session row is missing its persisted directory scope"
            )
        self._session_directories[adapter_handle] = row_directory
        return row_directory

    def _observe(
        self, canonical_task_id: str, adapter_handle: str
    ) -> tuple[CanonicalTaskState, TurnEvidence | None, str | None]:
        """One mechanical observation: (state, turn evidence, detail).

        No terminal state is inferred from idle/absent status alone; terminal
        evidence requires the exact dispatched assistant turn to have ended.
        """
        directory = self._session_directory(adapter_handle)
        status_map = self._host_client.query_status(directory=directory)
        entry = session_status_entry(status_map, adapter_handle)
        if entry is not None:
            status_type = entry.get("type")
            if status_type in ("busy", "retry"):
                return opencode_status_to_canonical_state(entry), None, f"host status {status_type!r}"
            if status_type != "idle":
                # Unknown/ambiguous host status vocabulary: stay UNKNOWN (fail
                # closed); never fabricate a terminal state from drift.
                return (
                    CanonicalTaskState.UNKNOWN,
                    None,
                    f"unknown host status {status_type!r}",
                )
        # idle/absent: consult EXACT session turn evidence before anything terminal
        messages = self._host_client.fetch_session_messages(
            adapter_handle, directory=directory
        )
        evidence = turn_evidence(messages)
        if evidence.terminal_error:
            if evidence.aborted:
                return CanonicalTaskState.CANCELLED, evidence, evidence.last_assistant_error
            return CanonicalTaskState.FAILED, evidence, evidence.last_assistant_error
        if evidence.terminal_success:
            return CanonicalTaskState.COMPLETED, evidence, "dispatched turn terminated"
        if evidence.has_assistant:
            return (
                CanonicalTaskState.UNKNOWN,
                evidence,
                f"assistant turn not proven terminal (finish={evidence.last_assistant_finish!r})",
            )
        return CanonicalTaskState.UNKNOWN, evidence, "no assistant turn evidence yet"

    def status(self, canonical_task_id: str, adapter_handle: str) -> TaskStatusResult:
        self._verify_task_handle(canonical_task_id, adapter_handle)
        if self._host_client is None:
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details="EXECUTOR_UNAVAILABLE: OpenCode host client not configured",
            )
        try:
            state, evidence, detail = self._observe(canonical_task_id, adapter_handle)
        except OpenCodeSessionNotFoundError:
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details="SESSION_NOT_FOUND: exact OpenCode session is missing",
            )
        except (OpenCodeHostError, OpenCodeAdapterError) as exc:
            return TaskStatusResult(
                canonical_task_id=canonical_task_id,
                state=CanonicalTaskState.UNKNOWN,
                details=f"{getattr(exc, 'code', 'ADAPTER_PROTOCOL_ERROR')}: {exc}",
            )
        progress: dict[str, Any] = {}
        if evidence is not None:
            progress = {
                "message_count": evidence.message_count,
                "last_assistant_finish": evidence.last_assistant_finish,
                "session_id": adapter_handle,
            }
        return TaskStatusResult(
            canonical_task_id=canonical_task_id,
            state=state,
            progress=progress,
            details=str(detail or ""),
        )

    def result(self, canonical_task_id: str, adapter_handle: str) -> CanonicalResult:
        self._verify_task_handle(canonical_task_id, adapter_handle)
        correlation_id = f"corr-{canonical_task_id}"
        if self._host_client is None:
            return _result_observation_uncertainty(
                canonical_task_id=canonical_task_id,
                error_code="EXECUTOR_UNAVAILABLE",
                error_message="OpenCode host client is not configured",
                correlation_id=correlation_id,
            )
        try:
            state, evidence, detail = self._observe(canonical_task_id, adapter_handle)
        except OpenCodeSessionNotFoundError:
            return _result_observation_uncertainty(
                canonical_task_id=canonical_task_id,
                error_code="SESSION_NOT_FOUND",
                error_message="exact OpenCode session is missing",
                correlation_id=correlation_id,
            )
        except (OpenCodeHostError, OpenCodeAdapterError) as exc:
            return _result_observation_uncertainty(
                canonical_task_id=canonical_task_id,
                error_code=getattr(exc, "code", "ADAPTER_PROTOCOL_ERROR"),
                error_message=str(exc),
                correlation_id=correlation_id,
            )

        if state == CanonicalTaskState.COMPLETED:
            assert evidence is not None
            result_data: dict[str, Any] = {
                "mechanical_execution": {
                    "session_id": adapter_handle,
                    "turn_terminated": True,
                    "message_count": evidence.message_count,
                    "last_assistant_finish": evidence.last_assistant_finish,
                }
            }
            if evidence.assistant_text:
                # Evidence linkage only; raw assistant text is never the
                # governed result authority and never a task return.
                result_data["mechanical_execution"]["assistant_text_sha256"] = hashlib.sha256(
                    evidence.assistant_text.encode("utf-8")
                ).hexdigest()
            return CanonicalResult.success(
                canonical_task_id=canonical_task_id,
                executor_id=OPENCODE_EXECUTOR_ID,
                result_data=result_data,
                exit_code=None,
                correlation_id=correlation_id,
                execution_stats={"session_id": adapter_handle},
            )
        if state == CanonicalTaskState.CANCELLED:
            return CanonicalResult.cancelled(
                canonical_task_id=canonical_task_id,
                executor_id=OPENCODE_EXECUTOR_ID,
                error_message=f"OpenCode session aborted ({detail})",
                correlation_id=correlation_id,
            )
        if state == CanonicalTaskState.FAILED:
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=OPENCODE_EXECUTOR_ID,
                error_code="OPENCODE_SESSION_ERROR",
                error_message=f"OpenCode exact session reported an assistant error ({detail})",
                retryable=False,
                exit_code=None,
                correlation_id=correlation_id,
            )
        if state in (CanonicalTaskState.RUNNING, CanonicalTaskState.WAITING, CanonicalTaskState.QUEUED):
            return CanonicalResult.failure(
                canonical_task_id=canonical_task_id,
                executor_id=OPENCODE_EXECUTOR_ID,
                error_code="TASK_STILL_RUNNING",
                error_message=f"OpenCode task remains in non-terminal state {state.value}",
                retryable=True,
                exit_code=None,
                correlation_id=correlation_id,
                status="unknown",
                canonical_task_state=state.value,
            )
        return _result_observation_uncertainty(
            canonical_task_id=canonical_task_id,
            error_code="TASK_STATE_UNKNOWN",
            error_message=f"OpenCode exact-session evidence is ambiguous ({detail})",
            correlation_id=correlation_id,
        )

    # -- cancel / resume -------------------------------------------------------

    def cancel(self, canonical_task_id: str, adapter_handle: str) -> CancelResult:
        self._verify_task_handle(canonical_task_id, adapter_handle)
        replay_key = (canonical_task_id, adapter_handle)
        replay_result = self._cancel_replays.get(replay_key)
        if replay_result is not None:
            return replay_result
        if not self._capabilities.supports_task_cancellation or self._host_client is None:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )
        try:
            directory = self._session_directory(adapter_handle)
            self._host_client.get_session(adapter_handle, directory=directory)
        except OpenCodeSessionNotFoundError:
            result = CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )
            self._cancel_replays[replay_key] = result
            return result
        except (OpenCodeHostError, OpenCodeAdapterError):
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )
        try:
            aborted = self._host_client.abort_session(adapter_handle, directory=directory)
        except OpenCodeSessionNotFoundError:
            result = CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )
            self._cancel_replays[replay_key] = result
            return result
        except OpenCodeHostError:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.UNKNOWN,
            )
        if not aborted:
            return CancelResult(
                canonical_task_id=canonical_task_id,
                cancelled=False,
                state=CanonicalTaskState.RUNNING,
            )
        # Authoritative abort: verify the exact session actually reached idle
        # before claiming CANCELLED (the pinned host returns true for unknown ids).
        deadline = time.monotonic() + 30.0
        last_state: CanonicalTaskState = CanonicalTaskState.UNKNOWN
        while True:
            try:
                entry = self._host_client.session_status(adapter_handle, directory=directory)
            except OpenCodeHostError:
                return CancelResult(
                    canonical_task_id=canonical_task_id,
                    cancelled=False,
                    state=CanonicalTaskState.UNKNOWN,
                )
            if entry is None:
                result = CancelResult(
                    canonical_task_id=canonical_task_id,
                    cancelled=True,
                    state=CanonicalTaskState.CANCELLED,
                )
                self._cancel_replays[replay_key] = result
                return result
            last_state = opencode_status_to_canonical_state(entry)
            if time.monotonic() >= deadline:
                return CancelResult(
                    canonical_task_id=canonical_task_id,
                    cancelled=False,
                    state=last_state,
                )
            time.sleep(0.2)

    def resume(
        self,
        canonical_task_id: str,
        adapter_handle: str,
        resume_package: ExecutionPackage,
    ) -> ResumeResult:
        if not isinstance(resume_package, ExecutionPackage):
            raise TypeError(
                f"resume_package must be ExecutionPackage, got {type(resume_package).__name__}"
            )
        if resume_package.canonical_task_id != canonical_task_id:
            raise OpenCodeAdapterError(
                f"TASK_ID_MISMATCH: resume package canonical_task_id "
                f"{resume_package.canonical_task_id!r} != {canonical_task_id!r}",
                code="TASK_ID_MISMATCH",
            )
        self._verify_task_handle(canonical_task_id, adapter_handle)
        raise OpenCodeResumeUnsupportedError()


__all__ = [
    "ADAPTER_HANDLE_IS_SESSION_ID",
    "AMBIENT_CWD_IS_AUTHORITY",
    "HOST_SUCCESS_IS_SEMANTIC_SUCCESS",
    "HOST_TOOL_BINDING_NOTE",
    "MODEL_SUPPLIED_CWD_AUTHORITY",
    "OPENCODE_ADAPTER_KIND",
    "OPENCODE_EXECUTOR_ID",
    "OPENCODE_FINAL_TEXT_IS_TASK_RETURN",
    "OPENCODE_STATUS_MAP",
    "OpenCodeAdapter",
    "OpenCodeAdapterError",
    "OpenCodeAdapterProtocolError",
    "OpenCodeDispatchFailureError",
    "OpenCodeHostUnavailableAdapterError",
    "OpenCodeResumeUnsupportedError",
    "OpenCodeWorkerDirectoryUnavailableError",
    "build_opencode_host_payload",
    "default_opencode_capabilities",
    "opencode_status_to_canonical_state",
]
