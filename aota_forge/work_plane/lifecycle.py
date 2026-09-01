"""Worker Lifecycle and execution-time Work Role binding (S1 M2-W1).

Minimal semantic lifecycle contract distinguishing task-main (long-lived
coordination role, wake source user) from Worker (one-shot disposable,
wake source task-main/Forge) and defining execution-time WorkRole binding.

Invariants
----------
* TASK_MAIN_LONG_LIVED=yes, WAKE_SOURCE=user,
  PRIMARY_RESPONSIBILITY=coordination/planning/reconciliation
* WORKER_ONE_SHOT=yes, DISPOSABLE=yes, WAKE_SOURCE=task-main_or_forge
* WORK_ROLE_ASSIGNED_AT_EXECUTION_TIME=yes
* PREDEPLOYED_AGENT_PROFILE_REQUIRED=no
* WORK_ROLE_BINDING_IS_EXECUTION_SCOPED=yes,
  IS_PERSISTENT_AGENT_IDENTITY=no, IS_AUTHORITY=no
* LIFECYCLE_CONTRACT_IS_AUTHORITY=no
* TASK_MAIN_DEFAULT_EXECUTION_MAPPING=none (reuse M1 mapping, fail-closed)
* No runtime implementation: no daemon, session manager, rollover storage
* No persistent profile registry, no global agent identity, no multi-role

This module is a semantic contract only — not a runtime manager and
carries no authority. It reuses AgentWorkRole from M1 and does not
create a second role ontology.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Lifecycle invariants — task-main
# ---------------------------------------------------------------------------

TASK_MAIN_LONG_LIVED: bool = True
TASK_MAIN_WAKE_SOURCE: str = "user"
TASK_MAIN_PRIMARY_RESPONSIBILITY: str = "coordination/planning/reconciliation"

# Worker lifecycle invariants
WORKER_ONE_SHOT: bool = True
WORKER_DISPOSABLE: bool = True
WORKER_WAKE_SOURCE: str = "task-main_or_forge"

# Binding-time invariant
WORK_ROLE_ASSIGNED_AT_EXECUTION_TIME: bool = True
PREDEPLOYED_AGENT_PROFILE_REQUIRED: bool = False

# Authority separation
LIFECYCLE_CONTRACT_IS_AUTHORITY: bool = False
WORK_ROLE_BINDING_IS_AUTHORITY: bool = False
WORK_ROLE_BINDING_IS_EXECUTION_SCOPED: bool = True
WORK_ROLE_BINDING_IS_PERSISTENT_AGENT_IDENTITY: bool = False

# Task-main has no default execution mapping — reuse M1 fail-closed mapping
TASK_MAIN_DEFAULT_EXECUTION_MAPPING: None = None

# Allowed binding fields — fail-closed
_ALLOWED_BINDING_FIELDS: frozenset[str] = frozenset({"work_role"})


@dataclass(frozen=True)
class ExecutionWorkRoleBinding:
    """Execution-scoped binding of exactly one AgentWorkRole.

    Represents the lifecycle-level semantics:
      current execution -> exactly one AgentWorkRole binding

    Properties
    ----------
    * execution scoped — not a persistent agent identity
    * not an authority — carries no planning/filesystem/tool authority
    * exactly one role — multi-role or unknown role fails closed
    * reuses AgentWorkRole (no second ontology)
    * a Worker implementation may be bound to different roles across
      executions if Forge authority allows — binding itself does not
      persist the role
    """

    work_role: AgentWorkRole

    def __post_init__(self) -> None:
        # Validate work_role — reuse M1 AgentWorkRole semantics, fail-closed
        if isinstance(self.work_role, AgentWorkRole):
            pass
        elif isinstance(self.work_role, Enum):
            raise TypeError(
                f"work_role must be an AgentWorkRole or valid work role string, "
                f"got foreign Enum {type(self.work_role).__name__}"
            )
        elif isinstance(self.work_role, str) and type(self.work_role) is str:
            parsed = parse_agent_work_role(self.work_role)
            object.__setattr__(self, "work_role", parsed)
        else:
            raise TypeError(
                f"work_role must be an AgentWorkRole or valid string, "
                f"got {type(self.work_role).__name__}"
            )

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical representation."""
        return {"work_role": self.work_role.value}

    def canonical_json(self) -> str:
        """Deterministic canonical JSON."""
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {"work_role": self.work_role.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionWorkRoleBinding:
        """Construct from mapping, fail-closed on unknown/mechanical fields."""
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_BINDING_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ExecutionWorkRoleBinding: {sorted(extra)}")
        if "work_role" not in data:
            raise ValueError("Missing required field in ExecutionWorkRoleBinding: 'work_role'")
        return cls(work_role=data["work_role"])

    @classmethod
    def bind(cls, work_role: object) -> ExecutionWorkRoleBinding:
        """Convenience: bind a work role at execution time, fail-closed."""
        return cls(work_role=work_role)  # type: ignore[arg-type]
