"""Work Role to Canonical Execution Role mapping contract (S1 M1-W2).

Establishes a strict semantic translation layer between AgentWorkRole
and CanonicalRole.

Invariants
----------
* Exactly four supported 1:1 mappings:
    analyst         -> CanonicalRole.PLANNER
    coder           -> CanonicalRole.CODER
    reviewer        -> CanonicalRole.REVIEWER
    project-steward -> CanonicalRole.STEWARD
* task-main has no default execution mapping (raises WorkRoleMappingError / fails closed).
* CanonicalRole.EXECUTOR is NEVER produced by the mapping and cannot be a WorkRole.
* Fail-closed: invalid, unknown, or non-work-role inputs are rejected.
* Deterministic: identical inputs always produce identical CanonicalRole outputs.
* No heuristic, similarity, ranking, LLM selection, or one-to-many fallback.
* Mapping is semantic translation only — it grants no execution or dispatch authority.
* Zero Hermes imports.
* Dependency direction: work_plane -> core.execution (Core never imports work_plane).
"""

from __future__ import annotations

from typing import Mapping

from aota_forge.core.execution.roles import CanonicalRole
from aota_forge.work_plane.roles import (
    AgentWorkRole,
    parse_agent_work_role,
)


class WorkRoleMappingError(ValueError):
    """Raised when a work role cannot be mapped to a canonical execution role."""

    def __init__(self, work_role: object, message: str | None = None) -> None:
        self.work_role = work_role
        msg = message or f"Work role {work_role!r} has no default canonical execution role mapping"
        super().__init__(msg)


# Explicit, immutable mapping table for supported Worker roles
WORK_ROLE_TO_CANONICAL_ROLE: Mapping[AgentWorkRole, CanonicalRole] = {
    AgentWorkRole.ANALYST: CanonicalRole.PLANNER,
    AgentWorkRole.CODER: CanonicalRole.CODER,
    AgentWorkRole.REVIEWER: CanonicalRole.REVIEWER,
    AgentWorkRole.PROJECT_STEWARD: CanonicalRole.STEWARD,
}


def resolve_work_role_to_canonical_role(work_role: object) -> CanonicalRole:
    """Resolve an AgentWorkRole or work-role string to its CanonicalRole.

    Parameters
    ----------
    work_role : object
        An ``AgentWorkRole`` enum member or valid work-role string
        (e.g., "analyst", "coder", "reviewer", "project-steward").

    Returns
    -------
    CanonicalRole
        The corresponding ``CanonicalRole`` enum member.

    Raises
    ------
    TypeError
        If *work_role* is not a string or ``AgentWorkRole`` (including foreign Enums
        such as ``CanonicalRole``).
    ValueError
        If *work_role* is an unknown or invalid work-role string.
    WorkRoleMappingError
        If *work_role* is ``AgentWorkRole.TASK_MAIN`` or ``"task-main"``, which has
        no default execution role mapping.
    """
    # parse_agent_work_role raises TypeError for non-str/non-AgentWorkRole and foreign Enums,
    # and ValueError for unknown strings.
    parsed = parse_agent_work_role(work_role)

    if parsed is AgentWorkRole.TASK_MAIN:
        raise WorkRoleMappingError(
            work_role,
            f"Work role {parsed.value!r} has no default canonical execution role mapping (fail-closed)",
        )

    canonical = WORK_ROLE_TO_CANONICAL_ROLE.get(parsed)
    if canonical is None:
        raise WorkRoleMappingError(
            work_role,
            f"No canonical execution role mapping for work role {parsed.value!r}",
        )

    return canonical
