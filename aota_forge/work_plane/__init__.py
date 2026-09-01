"""Work Plane semantic layer (S1).

Application/work-plane layer owning Agent Work Role and subsequent
handoff semantics. Distinct from core execution ontology.
"""

from aota_forge.work_plane.compiler import (  # noqa: F401
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
    compile_task_handoff,
)
from aota_forge.work_plane.events import (  # noqa: F401
    EventHook,
    EventHookError,
    ExecutionEvent,
    ExecutionEventType,
    emit_event,
)
from aota_forge.work_plane.handoff import (  # noqa: F401
    FORBIDDEN_MECHANICAL_FIELDS as HANDOFF_FORBIDDEN_MECHANICAL_FIELDS,
    SemanticReference,
    TaskHandoff,
    compute_handoff_digest,
)
from aota_forge.work_plane.mapping import (  # noqa: F401
    WORK_ROLE_TO_CANONICAL_ROLE,
    WorkRoleMappingError,
    resolve_work_role_to_canonical_role,
)
from aota_forge.work_plane.result_card import (  # noqa: F401
    ResultHandoffRef,
    WorkerResultCard,
    project_worker_result_card,
)
from aota_forge.work_plane.roles import (  # noqa: F401
    AGENT_WORK_ROLES,
    VALID_WORK_ROLES,
    WORK_ROLES,
    WORK_ROLE_SET,
    AgentWorkRole,
    is_agent_work_role,
    parse_agent_work_role,
    validate_agent_work_role,
)
from aota_forge.work_plane.stop import (  # noqa: F401
    Escalation,
    MechanicalFailure,
    RetryRequest,
    SemanticStop,
    SemanticStopReason,
    StopKind,
)

__all__ = [
    "AgentWorkRole",
    "WORK_ROLES",
    "WORK_ROLE_SET",
    "VALID_WORK_ROLES",
    "AGENT_WORK_ROLES",
    "is_agent_work_role",
    "validate_agent_work_role",
    "parse_agent_work_role",
    "resolve_work_role_to_canonical_role",
    "WORK_ROLE_TO_CANONICAL_ROLE",
    "WorkRoleMappingError",
    "TaskHandoff",
    "SemanticReference",
    "compute_handoff_digest",
    "HANDOFF_FORBIDDEN_MECHANICAL_FIELDS",
    "TrustedExecutionBinding",
    "compile_handoff_to_execution_package",
    "compile_task_handoff",
    # M3-W1
    "WorkerResultCard",
    "ResultHandoffRef",
    "project_worker_result_card",
    # M3-W2
    "SemanticStop",
    "SemanticStopReason",
    "MechanicalFailure",
    "Escalation",
    "StopKind",
    "RetryRequest",
    # M3-W3
    "ExecutionEvent",
    "ExecutionEventType",
    "EventHook",
    "EventHookError",
    "emit_event",
]
