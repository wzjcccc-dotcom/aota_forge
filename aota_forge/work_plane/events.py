"""Minimal Execution Event contract (S1 M3-W3).

Bounded immutable observability identity + hook payload, not event storage.
Reuses existing mechanical lineage, semantic identity, W2 stop classification.

Invariants
----------
* EVENT_ID_IS_AUTHORITY=no — trace only
* EVENT_DIGEST_IS_AUTHORITY=no — if present, not authority
* WORKER_SELF_REPORT_IS_AUTHORITY=no — observation not governance
* No persistent telemetry store, no analytics, no exporter
* Bounded string/ref fields, deterministic canonical serialization
* Missing future mechanical identity must not be fabricated
* Reuses AgentWorkRole, TaskHandoff digest/ref, W2 stop types
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.stop import MechanicalFailure, SemanticStop

# ---------------------------------------------------------------------------
# Bounded capacity
# ---------------------------------------------------------------------------
MAX_EVENT_ID_LENGTH: int = 128
MAX_TASK_KIND_LENGTH: int = 128
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_CORRELATION_LENGTH: int = 512
MAX_PACKAGE_ID_LENGTH: int = 512
MAX_DISPATCH_ATTEMPT_LENGTH: int = 512
MAX_EXECUTOR_ID_LENGTH: int = 512
MAX_ADAPTER_HANDLE_LENGTH: int = 512

# ---------------------------------------------------------------------------
# Event type — minimal bounded set justified by S1 seam
# ---------------------------------------------------------------------------

@unique
class ExecutionEventType(str, Enum):
    HANDOFF_PREPARED = "handoff_prepared"
    EXECUTION_MATERIALIZED = "execution_materialized"
    WORKER_RESULT = "worker_result"
    SEMANTIC_STOP = "semantic_stop"
    MECHANICAL_FAILURE = "mechanical_failure"


EXECUTION_EVENT_TYPES: frozenset[str] = frozenset(e.value for e in ExecutionEventType)
UNKNOWN_EVENT_TYPE_FAIL_CLOSED: bool = True


def parse_execution_event_type(value: object) -> ExecutionEventType:
    if isinstance(value, ExecutionEventType):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return ExecutionEventType(value)
        except ValueError:
            raise ValueError(
                f"Unknown execution event type: {value!r}. Must be one of {sorted(EXECUTION_EVENT_TYPES)}"
            )
    raise TypeError(f"event_type must be a string or ExecutionEventType, got {type(value).__name__}")


def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_len:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_len}")
    return stripped


def _validate_optional_bounded_str(value: object, label: str, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string or None, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} when provided must be a non-empty string")
    if len(stripped) > max_len:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_len}")
    return stripped


_ALLOWED_EVENT_FIELDS: frozenset[str] = frozenset({
    "event_id",
    "event_type",
    "work_role",
    "task_kind",
    "handoff_ref",
    "handoff_digest",
    "canonical_task_id",
    "package_id",
    "correlation_id",
    "dispatch_attempt_id",
    "executor_id",
    "adapter_handle",
    "result_ref",
    "semantic_stop",
    "mechanical_failure",
})

# ---------------------------------------------------------------------------
# ExecutionEvent — immutable bounded observability identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionEvent:
    """Bounded immutable execution observation event.

    Purpose: trace/hook payload, not storage or authority.
    All string/ref fields bounded, deterministic serialization.
    Missing future mechanical identities must not be fabricated — remain None.

    Fields
    ------
    event_id: bounded required trace identity, NOT authority
    event_type: one of 5 bounded types
    work_role: optional reused AgentWorkRole
    task_kind: optional bounded task kind string
    handoff_ref / handoff_digest: optional bounded handoff traceability (NOT authority)
    canonical_task_id, package_id, correlation_id: optional mechanical lineage
    dispatch_attempt_id, executor_id, adapter_handle: optional post-dispatch lineage
    result_ref: optional bounded result/card reference (not full result)
    semantic_stop / mechanical_failure: optional reused W2 classification (exclusive, not both)
    """

    event_id: str
    event_type: ExecutionEventType
    work_role: AgentWorkRole | None = None
    task_kind: str | None = None
    handoff_ref: str | None = None
    handoff_digest: str | None = None
    canonical_task_id: str | None = None
    package_id: str | None = None
    correlation_id: str | None = None
    dispatch_attempt_id: str | None = None
    executor_id: str | None = None
    adapter_handle: str | None = None
    result_ref: str | None = None
    semantic_stop: SemanticStop | None = None
    mechanical_failure: MechanicalFailure | None = None

    def __post_init__(self) -> None:
        # event_id bounded required, not authority
        object.__setattr__(self, "event_id", _validate_bounded_str(self.event_id, "event_id", MAX_EVENT_ID_LENGTH))

        # event_type bounded, fail-closed on unknown
        if isinstance(self.event_type, ExecutionEventType):
            pass
        elif isinstance(self.event_type, str) and type(self.event_type) is str:
            object.__setattr__(self, "event_type", parse_execution_event_type(self.event_type))
        elif isinstance(self.event_type, Enum):
            raise TypeError(f"event_type must be ExecutionEventType or string, got foreign Enum {type(self.event_type).__name__}")
        else:
            raise TypeError(f"event_type must be ExecutionEventType or string, got {type(self.event_type).__name__}")

        # work_role optional reused AgentWorkRole, no duplicate taxonomy
        if self.work_role is not None:
            if isinstance(self.work_role, AgentWorkRole):
                pass
            elif isinstance(self.work_role, Enum):
                raise TypeError(f"work_role must be AgentWorkRole or None, got foreign Enum {type(self.work_role).__name__}")
            elif isinstance(self.work_role, str) and type(self.work_role) is str:
                object.__setattr__(self, "work_role", parse_agent_work_role(self.work_role))
            else:
                raise TypeError(f"work_role must be AgentWorkRole, string, or None, got {type(self.work_role).__name__}")

        # task_kind optional bounded
        if self.task_kind is not None:
            object.__setattr__(self, "task_kind", _validate_bounded_str(self.task_kind, "task_kind", MAX_TASK_KIND_LENGTH))

        # handoff_ref / digest optional bounded, not authority
        if self.handoff_ref is not None:
            object.__setattr__(self, "handoff_ref", _validate_bounded_str(self.handoff_ref, "handoff_ref", MAX_REF_LENGTH))
        if self.handoff_digest is not None:
            object.__setattr__(self, "handoff_digest", _validate_bounded_str(self.handoff_digest, "handoff_digest", MAX_DIGEST_LENGTH))

        # mechanical lineage optional — missing future identity must remain None, not fabricated
        if self.canonical_task_id is not None:
            object.__setattr__(self, "canonical_task_id", _validate_bounded_str(self.canonical_task_id, "canonical_task_id", MAX_REF_LENGTH))
        if self.package_id is not None:
            object.__setattr__(self, "package_id", _validate_bounded_str(self.package_id, "package_id", MAX_PACKAGE_ID_LENGTH))
        if self.correlation_id is not None:
            object.__setattr__(self, "correlation_id", _validate_bounded_str(self.correlation_id, "correlation_id", MAX_CORRELATION_LENGTH))
        if self.dispatch_attempt_id is not None:
            object.__setattr__(self, "dispatch_attempt_id", _validate_bounded_str(self.dispatch_attempt_id, "dispatch_attempt_id", MAX_DISPATCH_ATTEMPT_LENGTH))
        if self.executor_id is not None:
            object.__setattr__(self, "executor_id", _validate_bounded_str(self.executor_id, "executor_id", MAX_EXECUTOR_ID_LENGTH))
        if self.adapter_handle is not None:
            object.__setattr__(self, "adapter_handle", _validate_bounded_str(self.adapter_handle, "adapter_handle", MAX_ADAPTER_HANDLE_LENGTH))

        # result_ref optional bounded — pointer, not full result dump
        if self.result_ref is not None:
            object.__setattr__(self, "result_ref", _validate_bounded_str(self.result_ref, "result_ref", MAX_REF_LENGTH))

        # stop classification reuse W2 — no duplicate taxonomy, at most one
        if self.semantic_stop is not None and self.mechanical_failure is not None:
            raise ValueError("event may carry at most one of semantic_stop / mechanical_failure")
        if self.semantic_stop is not None:
            if not isinstance(self.semantic_stop, SemanticStop):
                raise TypeError(f"semantic_stop must be SemanticStop or None, got {type(self.semantic_stop).__name__}")
        if self.mechanical_failure is not None:
            if not isinstance(self.mechanical_failure, MechanicalFailure):
                raise TypeError(f"mechanical_failure must be MechanicalFailure or None, got {type(self.mechanical_failure).__name__}")

        # boundedness: ensure event does not carry raw dumps (enforced via ref-only fields)
        # event_type lineage consistency: adapter_handle should not be fabricated pre-dispatch
        # but we allow caller to set it when available; we cannot enforce temporal ordering strictly
        # except that pre-dispatch events (handoff_prepared) ideally omit dispatch fields — not hard fail, but document.

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
        }
        if self.work_role is not None:
            d["work_role"] = self.work_role.value
        if self.task_kind is not None:
            d["task_kind"] = self.task_kind
        if self.handoff_ref is not None:
            d["handoff_ref"] = self.handoff_ref
        if self.handoff_digest is not None:
            d["handoff_digest"] = self.handoff_digest
        if self.canonical_task_id is not None:
            d["canonical_task_id"] = self.canonical_task_id
        if self.package_id is not None:
            d["package_id"] = self.package_id
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.dispatch_attempt_id is not None:
            d["dispatch_attempt_id"] = self.dispatch_attempt_id
        if self.executor_id is not None:
            d["executor_id"] = self.executor_id
        if self.adapter_handle is not None:
            d["adapter_handle"] = self.adapter_handle
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.semantic_stop is not None:
            d["semantic_stop"] = self.semantic_stop.canonical_dict()
        if self.mechanical_failure is not None:
            d["mechanical_failure"] = self.mechanical_failure.canonical_dict()
        return canonicalize(d, path="ExecutionEvent")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
        }
        if self.work_role is not None:
            d["work_role"] = self.work_role.value
        if self.task_kind is not None:
            d["task_kind"] = self.task_kind
        if self.handoff_ref is not None:
            d["handoff_ref"] = self.handoff_ref
        if self.handoff_digest is not None:
            d["handoff_digest"] = self.handoff_digest
        if self.canonical_task_id is not None:
            d["canonical_task_id"] = self.canonical_task_id
        if self.package_id is not None:
            d["package_id"] = self.package_id
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.dispatch_attempt_id is not None:
            d["dispatch_attempt_id"] = self.dispatch_attempt_id
        if self.executor_id is not None:
            d["executor_id"] = self.executor_id
        if self.adapter_handle is not None:
            d["adapter_handle"] = self.adapter_handle
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.semantic_stop is not None:
            d["semantic_stop"] = self.semantic_stop.to_dict()
        if self.mechanical_failure is not None:
            d["mechanical_failure"] = self.mechanical_failure.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionEvent":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_EVENT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ExecutionEvent: {sorted(extra)}")
        if "event_id" not in data or "event_type" not in data:
            raise ValueError("Missing required field in ExecutionEvent: 'event_id' and 'event_type' required")
        # work_role parse
        wr = data.get("work_role")
        if wr is not None:
            wr = parse_agent_work_role(wr)
        # stop classifications
        sem = None
        mech = None
        if "semantic_stop" in data and data["semantic_stop"] is not None:
            raw = data["semantic_stop"]
            if isinstance(raw, SemanticStop):
                sem = raw
            elif isinstance(raw, Mapping):
                sem = SemanticStop.from_dict(raw)  # type: ignore
            else:
                raise TypeError(f"semantic_stop must be mapping or SemanticStop, got {type(raw).__name__}")
        if "mechanical_failure" in data and data["mechanical_failure"] is not None:
            raw = data["mechanical_failure"]
            if isinstance(raw, MechanicalFailure):
                mech = raw
            elif isinstance(raw, Mapping):
                mech = MechanicalFailure.from_dict(raw)  # type: ignore
            else:
                raise TypeError(f"mechanical_failure must be mapping or MechanicalFailure, got {type(raw).__name__}")
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            work_role=wr,
            task_kind=data.get("task_kind"),
            handoff_ref=data.get("handoff_ref"),
            handoff_digest=data.get("handoff_digest"),
            canonical_task_id=data.get("canonical_task_id"),
            package_id=data.get("package_id"),
            correlation_id=data.get("correlation_id"),
            dispatch_attempt_id=data.get("dispatch_attempt_id"),
            executor_id=data.get("executor_id"),
            adapter_handle=data.get("adapter_handle"),
            result_ref=data.get("result_ref"),
            semantic_stop=sem,
            mechanical_failure=mech,
        )


# ---------------------------------------------------------------------------
# Hook / Emission seam — injected, no persistent singleton storage
# ---------------------------------------------------------------------------

@runtime_checkable
class EventHook(Protocol):
    """Injected hook — caller-owned, no hidden singleton."""

    def __call__(self, event: ExecutionEvent) -> None: ...


# Typed hook failure — propagates without mutating execution state
class EventHookError(RuntimeError):
    """Typed wrapper for hook failures that propagate to caller."""

    def __init__(self, event_id: str, cause: BaseException) -> None:
        super().__init__(f"event hook failed for {event_id}: {cause}")
        self.event_id = event_id
        self.cause = cause


def emit_event(event: ExecutionEvent, hook: EventHook | Callable[[ExecutionEvent], None] | None = None) -> None:
    """Emit event via injected hook.

    Semantics (chosen for S1):
    * NO_HOOK → valid no-op, deterministic
    * hook present → call hook(event) exactly once
    * hook failure → propagate typed EventHookError to caller WITHOUT mutating
      Journal, Result Governance, execution result, or triggering automatic retry.
    * No persistent storage, no global singleton, no analytics.

    Parameters
    ----------
    event: ExecutionEvent — bounded immutable event
    hook: optional injected callable — caller-owned
    """
    if not isinstance(event, ExecutionEvent):
        raise TypeError(f"event must be ExecutionEvent, got {type(event).__name__}")
    if hook is None:
        return
    if not callable(hook):
        raise TypeError(f"hook must be callable or None, got {type(hook).__name__}")
    try:
        hook(event)
    except EventHookError:
        raise
    except BaseException as exc:  # pragma: no cover — wrap for typed propagation
        raise EventHookError(event.event_id, exc) from exc


__all__ = [
    "ExecutionEventType",
    "EXECUTION_EVENT_TYPES",
    "UNKNOWN_EVENT_TYPE_FAIL_CLOSED",
    "parse_execution_event_type",
    "ExecutionEvent",
    "EventHook",
    "EventHookError",
    "emit_event",
    "MAX_EVENT_ID_LENGTH",
    "MAX_TASK_KIND_LENGTH",
    "MAX_REF_LENGTH",
    "MAX_DIGEST_LENGTH",
]

