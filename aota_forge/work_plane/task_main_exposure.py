"""Canonical ordinary task-main operation exposure — AF #59 M2/W2-F.

The single role-filtered actual exposure of an ordinary unbound task-main
session. Everything the model can actually use through ``aota.invoke``
derives from this one set: the ``role.bootstrap`` ``AOTA_MCP`` operation
projection, the unbound ``operations.list`` projection, ``help`` lookup
eligibility and the task-main role tool surface. No second operation catalog
exists, and this module deliberately carries no runtime dependencies (it is
imported by the role catalog and by the ref-scoped authority locator alike).
"""

from __future__ import annotations

# Operations whose unbound authority is located by the canonical ``plan_ref``
# input (the smallest existing-ref carrier; declared on these canonical
# descriptors in .aota/contracts/operations.yaml).
PLAN_REF_SCOPED_OPERATIONS: frozenset[str] = frozenset(
    {
        "github.issue.read",
        "github.issue.comments.read",
        "github.issue.update",
        "github.issue.comment.update",
        "handoff.write",
        "handoff.open",
        "task.start",
        "workspace.read",
        "workspace.search",
        "git.status",
        "git.diff",
        "git.checkpoint",
        "git.integrate",
        "git.push",
    }
)

# Operations that are reachable in an ordinary host session without any
# Plan/task binding (guidance, hydration, introspection, the contract reader).
TASK_MAIN_UNBOUND_EXTRAS: frozenset[str] = frozenset(
    {
        "help",
        "host.status",
        "operations.list",
        "result.hydrate",
        "role.bootstrap",
        "runtime.status",
        "skill.open",
    }
)

TASK_MAIN_UNBOUND_OPERATIONS: frozenset[str] = PLAN_REF_SCOPED_OPERATIONS | TASK_MAIN_UNBOUND_EXTRAS


def canonical_task_main_operations() -> tuple[str, ...]:
    """Deterministic canonical ordering of the ordinary task-main exposure."""
    return tuple(sorted(TASK_MAIN_UNBOUND_OPERATIONS))


__all__ = [
    "PLAN_REF_SCOPED_OPERATIONS",
    "TASK_MAIN_UNBOUND_EXTRAS",
    "TASK_MAIN_UNBOUND_OPERATIONS",
    "canonical_task_main_operations",
]
