"""AF Hermes runtime configuration, invocation binding (M1/W1), and the M2/W3
bounded durable completion coordinator (recovery / pending delivery / ACK /
admission mechanics over the W1 durable execution seam)."""

from .completion import (
    AdmissionDecision,
    CompletionCoordinatorError,
    CompletionDeliveryTransport,
    CompletionRecoveryRequiredError,
    DELIVERY_ATTEMPT_CAP,
    DELIVERY_CLAIM_TTL_SECONDS,
    DeliveryAttemptEvidence,
    DeliveryReport,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
    RecoveryReport,
    build_completion_envelope,
    parse_completion_ack,
)
from .config import (
    RUNTIME_CONFIG_ENV,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
    SHARED_MCP_TOOLSET,
    SHARED_WORKER_PROFILE,
    TASK_MAIN_PROFILE,
    load_runtime_config,
    resolve_binding_for_canonical_role,
    resolve_binding_for_work_role,
)

__all__ = [
    "RUNTIME_CONFIG_ENV",
    "RuntimeBinding",
    "RuntimeConfig",
    "RuntimeConfigError",
    "SHARED_MCP_TOOLSET",
    "SHARED_WORKER_PROFILE",
    "TASK_MAIN_PROFILE",
    "load_runtime_config",
    "resolve_binding_for_canonical_role",
    "resolve_binding_for_work_role",
    # M2/W3 durable completion coordination
    "AdmissionDecision",
    "CompletionCoordinatorError",
    "CompletionDeliveryTransport",
    "CompletionRecoveryRequiredError",
    "DELIVERY_ATTEMPT_CAP",
    "DELIVERY_CLAIM_TTL_SECONDS",
    "DeliveryAttemptEvidence",
    "DeliveryReport",
    "DeliveryTransportOutcome",
    "DurableCompletionCoordinator",
    "RecoveryReport",
    "build_completion_envelope",
    "parse_completion_ack",
]
