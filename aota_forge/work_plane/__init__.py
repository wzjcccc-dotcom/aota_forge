"""Work Plane semantic layer (S1).

Application/work-plane layer owning Agent Work Role and subsequent
handoff semantics. Distinct from core execution ontology.
"""

from aota_forge.work_plane.roles import (  # noqa: F401
    AGENT_WORK_ROLES,
    VALID_WORK_ROLES,
    WORK_ROLES,
    WORK_ROLE_SET,
    AgentWorkRole,
    is_agent_work_role,
    validate_agent_work_role,
)

__all__ = [
    "AgentWorkRole",
    "WORK_ROLES",
    "WORK_ROLE_SET",
    "VALID_WORK_ROLES",
    "AGENT_WORK_ROLES",
    "is_agent_work_role",
    "validate_agent_work_role",
]
