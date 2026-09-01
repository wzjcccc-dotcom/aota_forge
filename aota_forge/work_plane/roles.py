"""Agent Work Role semantic contract (S1 M1-W1).

Defines stable semantic responsibility boundary — not an execution
authority. Distinct type/domain from ``core.execution.roles.CanonicalRole``.

Invariants
----------
* Exactly five canonical values:
  ``task-main``, ``analyst``, ``coder``, ``reviewer``, ``project-steward``
* ``AgentWorkRole`` is NOT a ``CanonicalRole`` — different type/domain
  even where serialized text coincides (``"coder"``).
* Fail-closed validation: unknown or non-string inputs are rejected.
* Deterministic serialization via ``.value`` (canonical string).
* No mapping to ``CanonicalRole`` — that is W2 responsibility.
* Work role carries no execution authority.
* No runtime-private identity.
"""

from __future__ import annotations

from enum import Enum, unique
from typing import Any

# Canonical work-role string values in declared order.
WORK_ROLES: tuple[str, ...] = (
    "task-main",
    "analyst",
    "coder",
    "reviewer",
    "project-steward",
)

WORK_ROLE_SET: frozenset[str] = frozenset(WORK_ROLES)

# Spec aliases — all denote the same five values.
VALID_WORK_ROLES: tuple[str, ...] = WORK_ROLES
AGENT_WORK_ROLES: tuple[str, ...] = WORK_ROLES
AGENT_WORK_ROLE_VALUES: tuple[str, ...] = WORK_ROLES


@unique
class AgentWorkRole(Enum):
    """Stable semantic responsibility — not an execution role.

    ``AgentWorkRole`` deliberately does NOT subclass ``str`` so that
    ``AgentWorkRole.CODER`` remains type-distinct from
    ``CanonicalRole.CODER`` and from plain ``"coder"`` strings.
    Canonical serialization is ``member.value``.
    """

    TASK_MAIN = "task-main"
    ANALYST = "analyst"
    CODER = "coder"
    REVIEWER = "reviewer"
    PROJECT_STEWARD = "project-steward"

    @classmethod
    def is_valid(cls, role: object) -> bool:
        """Return True iff *role* is a valid work role.

        Accepts plain strings and ``AgentWorkRole`` members, rejects
        all other shapes fail-closed. Enum members from other domains
        (e.g. CanonicalRole) are rejected even when their string value
        coincides.
        """
        if isinstance(role, cls):
            return True
        # Reject foreign Enum members that subclass str (e.g. CanonicalRole)
        if isinstance(role, Enum):
            return False
        if isinstance(role, str) and type(role) is str:
            return role in WORK_ROLE_SET
        return False

    def __str__(self) -> str:  # pragma: no cover — trivial
        return self.value


def is_agent_work_role(role: object) -> bool:
    """Fail-closed predicate for Agent Work Role.

    Returns True for exactly the five canonical strings or for
    ``AgentWorkRole`` members. All other inputs (including ``None``,
    integers, unknown strings, ``CanonicalRole`` members) return False
    — never falling back to a default.
    """
    if isinstance(role, AgentWorkRole):
        return True
    if isinstance(role, Enum):
        return False
    if isinstance(role, str) and type(role) is str:
        return role in WORK_ROLE_SET
    return False


def validate_agent_work_role(role: object) -> str:
    """Validate *role* and return its canonical string value.

    * Accepts ``str`` equal to one of the five canonical values.
    * Accepts ``AgentWorkRole`` members (returns ``.value``).
    * Rejects non-string/non-enum shapes with ``TypeError``.
    * Rejects unknown strings with ``ValueError``.
    * Rejects foreign Enum members (e.g. CanonicalRole) with ``TypeError``.
    * Fail-closed — never returns a fallback/default role.
    """
    if isinstance(role, AgentWorkRole):
        return role.value
    if isinstance(role, Enum):
        raise TypeError(f"work_role must be a string or AgentWorkRole, got {type(role).__name__}")
    if not isinstance(role, str) or type(role) is not str:
        raise TypeError(f"work_role must be a string or AgentWorkRole, got {type(role).__name__}")
    if role not in WORK_ROLE_SET:
        raise ValueError(
            f"Invalid work role: {role!r}. Must be one of {sorted(WORK_ROLE_SET)}"
        )
    return role


def parse_agent_work_role(value: object) -> AgentWorkRole:
    """Parse *value* into an ``AgentWorkRole`` member, fail-closed.

    Deterministic: same canonical string always maps to the same member.
    """
    if isinstance(value, AgentWorkRole):
        return value
    if isinstance(value, Enum):
        raise TypeError(f"work_role must be a string or AgentWorkRole, got {type(value).__name__}")
    canonical = validate_agent_work_role(value)
    # Direct value lookup is deterministic because Enum values are unique.
    return AgentWorkRole(canonical)
