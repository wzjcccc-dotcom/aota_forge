"""Internal bounded operation identity: OperationContext.

Executor-neutral context resolved by the ingress.  In M1 it carries the
operation name, correlation id, principal adapter identity, bounded input
params, and any resolved project context.  It must NOT carry Hermes
Profile/session/Profile Task/ProcessRegistry identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OperationContext:
    operation: str
    correlation_id: str
    principal: str
    params: dict[str, Any] = field(default_factory=dict)
    project: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "principal": self.principal,
            "project": self.project,
        }
