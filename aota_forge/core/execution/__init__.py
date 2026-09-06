"""Canonical Execution Contracts Foundation (M5-1) with M2-W1 durable execution state.

Executor-neutral canonical execution foundation for ExecutorCapabilities,
ExecutionPackage, CanonicalTaskState, CanonicalResult, RoleMapping, and
ExecutorAdapter, plus the M2/W1 agent-neutral durable execution seam
(DurableExecutionRecord, ExecutionStateStore).
"""

from __future__ import annotations

from enum import Enum

from aota_forge.core.execution.adapter import (
    CancelResult,
    DispatchResult,
    ExecutorAdapter,
    ResumeResult,
    TaskStatusResult,
    ValidationResult,
)
from aota_forge.core.execution.capabilities import (
    ALLOWED_EXECUTION_MODES,
    ALLOWED_ISOLATION_MODES,
    FORBIDDEN_SEMANTIC_FIELDS,
    ExecutorCapabilities,
)
from aota_forge.core.execution.durable_state import (
    CAS_MUTABLE_EXECUTION_FIELDS,
    EXECUTION_DURABLE_SCHEMA_VERSION,
    EXECUTION_IDEMPOTENCY_CONFLICT,
    EXECUTION_PERSISTENCE_FAILURE,
    EXECUTION_RECORD_NOT_FOUND,
    STALE_EXECUTION_REVISION,
    DeliveryState,
    DurableExecutionRecord,
    ExecutionIdempotencyConflictError,
    ExecutionPersistenceFailureError,
    ExecutionPhase,
    ExecutionRecordNotFoundError,
    ExecutionStateError,
    ExecutionStateStore,
    FileBackedExecutionStateStore,
    InMemoryExecutionStateStore,
    OriginSessionRef,
    StaleExecutionRevisionError,
    card_digest_for,
    parse_delivery_state,
)
from aota_forge.core.execution.package import (
    ALLOWED_OPERATIONS,
    EXECUTION_CONTRACT_HASH,
    FORBIDDEN_HERMES_FIELDS,
    PROTOCOL_VERSION,
    ExecutionPackage,
    compute_intent_fingerprint,
    compute_package_fingerprint,
)
from aota_forge.core.execution.results import (
    ALLOWED_RESULT_STATUSES,
    CanonicalResult,
)
from aota_forge.core.execution.roles import (
    CANONICAL_ROLE_SET,
    CANONICAL_ROLES,
    CanonicalRole,
    RoleMapping,
    RoleMappingNotFoundError,
    is_canonical_role,
    validate_canonical_role,
)
from aota_forge.core.execution.state import (
    ALLOWED_STATE_TRANSITIONS,
    CANONICAL_TASK_STATE_SET,
    CANONICAL_TASK_STATES,
    TERMINAL_STATE_STRINGS,
    TERMINAL_STATES,
    CanonicalTaskState,
    InvalidStateTransitionError,
    allowed_transitions,
    can_transition,
    is_terminal,
    parse_state,
    validate_transition,
)

# Canonical Machine Error Taxonomy Codes
EXECUTOR_NOT_FOUND: str = "EXECUTOR_NOT_FOUND"
EXECUTOR_UNAVAILABLE: str = "EXECUTOR_UNAVAILABLE"
CAPABILITY_MISMATCH: str = "CAPABILITY_MISMATCH"
PACKAGE_INVALID: str = "PACKAGE_INVALID"
ROLE_MAPPING_NOT_FOUND: str = "ROLE_MAPPING_NOT_FOUND"
DISPATCH_REJECTED: str = "DISPATCH_REJECTED"
DISPATCH_TIMEOUT: str = "DISPATCH_TIMEOUT"
TASK_NOT_FOUND: str = "TASK_NOT_FOUND"
TASK_STATE_UNKNOWN: str = "TASK_STATE_UNKNOWN"
EXECUTION_FAILED: str = "EXECUTION_FAILED"
EXECUTION_TIMEOUT: str = "EXECUTION_TIMEOUT"
EXECUTION_CANCELLED: str = "EXECUTION_CANCELLED"
CANCEL_UNSUPPORTED: str = "CANCEL_UNSUPPORTED"
RESUME_UNSUPPORTED: str = "RESUME_UNSUPPORTED"
ADAPTER_PROTOCOL_ERROR: str = "ADAPTER_PROTOCOL_ERROR"
RESULT_MALFORMED: str = "RESULT_MALFORMED"
INTERNAL_MECHANICAL_ERROR: str = "INTERNAL_MECHANICAL_ERROR"


class ExecutionErrorCode(str, Enum):
    EXECUTOR_NOT_FOUND = "EXECUTOR_NOT_FOUND"
    EXECUTOR_UNAVAILABLE = "EXECUTOR_UNAVAILABLE"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    PACKAGE_INVALID = "PACKAGE_INVALID"
    ROLE_MAPPING_NOT_FOUND = "ROLE_MAPPING_NOT_FOUND"
    DISPATCH_REJECTED = "DISPATCH_REJECTED"
    DISPATCH_TIMEOUT = "DISPATCH_TIMEOUT"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_STATE_UNKNOWN = "TASK_STATE_UNKNOWN"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"
    CANCEL_UNSUPPORTED = "CANCEL_UNSUPPORTED"
    RESUME_UNSUPPORTED = "RESUME_UNSUPPORTED"
    ADAPTER_PROTOCOL_ERROR = "ADAPTER_PROTOCOL_ERROR"
    RESULT_MALFORMED = "RESULT_MALFORMED"
    INTERNAL_MECHANICAL_ERROR = "INTERNAL_MECHANICAL_ERROR"


__all__ = [
    # Capabilities
    "ALLOWED_EXECUTION_MODES",
    "ALLOWED_ISOLATION_MODES",
    "FORBIDDEN_SEMANTIC_FIELDS",
    "ExecutorCapabilities",
    # Package
    "ALLOWED_OPERATIONS",
    "EXECUTION_CONTRACT_HASH",
    "FORBIDDEN_HERMES_FIELDS",
    "PROTOCOL_VERSION",
    "ExecutionPackage",
    "compute_intent_fingerprint",
    "compute_package_fingerprint",
    # State
    "ALLOWED_STATE_TRANSITIONS",
    "CANONICAL_TASK_STATE_SET",
    "CANONICAL_TASK_STATES",
    "TERMINAL_STATE_STRINGS",
    "TERMINAL_STATES",
    "CanonicalTaskState",
    "InvalidStateTransitionError",
    "allowed_transitions",
    "can_transition",
    "is_terminal",
    "parse_state",
    "validate_transition",
    # Results
    "ALLOWED_RESULT_STATUSES",
    "CanonicalResult",
    # Roles
    "CANONICAL_ROLE_SET",
    "CANONICAL_ROLES",
    "CanonicalRole",
    "RoleMapping",
    "RoleMappingNotFoundError",
    "is_canonical_role",
    "validate_canonical_role",
    # Adapter
    "CancelResult",
    "DispatchResult",
    "ExecutorAdapter",
    "ResumeResult",
    "TaskStatusResult",
    "ValidationResult",
    # M2/W1 durable execution state
    "DurableExecutionRecord",
    "ExecutionStateStore",
    "InMemoryExecutionStateStore",
    "FileBackedExecutionStateStore",
    "ExecutionPhase",
    "DeliveryState",
    "OriginSessionRef",
    "parse_delivery_state",
    "card_digest_for",
    "CAS_MUTABLE_EXECUTION_FIELDS",
    "EXECUTION_DURABLE_SCHEMA_VERSION",
    "EXECUTION_RECORD_NOT_FOUND",
    "STALE_EXECUTION_REVISION",
    "EXECUTION_PERSISTENCE_FAILURE",
    "EXECUTION_IDEMPOTENCY_CONFLICT",
    "ExecutionStateError",
    "ExecutionRecordNotFoundError",
    "StaleExecutionRevisionError",
    "ExecutionPersistenceFailureError",
    "ExecutionIdempotencyConflictError",
    # Error taxonomy
    "ADAPTER_PROTOCOL_ERROR",
    "CANCEL_UNSUPPORTED",
    "CAPABILITY_MISMATCH",
    "DISPATCH_REJECTED",
    "DISPATCH_TIMEOUT",
    "EXECUTION_CANCELLED",
    "EXECUTION_FAILED",
    "EXECUTION_TIMEOUT",
    "EXECUTOR_NOT_FOUND",
    "EXECUTOR_UNAVAILABLE",
    "ExecutionErrorCode",
    "INTERNAL_MECHANICAL_ERROR",
    "PACKAGE_INVALID",
    "RESULT_MALFORMED",
    "RESUME_UNSUPPORTED",
    "ROLE_MAPPING_NOT_FOUND",
    "TASK_NOT_FOUND",
    "TASK_STATE_UNKNOWN",
]
