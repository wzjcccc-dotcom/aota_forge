"""TaskHandoff runtime convergence helpers (M2/W1 foundation).

Reuses the frozen S1 TaskHandoff contract (handoff.py) without mutating the
S1 high-conflict file. Provides bounded Worker execution input validation:

  TASK_HANDOFF_IS_BOUNDED, scope containment, bounded-projection checks.

No Plan authority duplication: handoff carries refs, not Plan bodies.
"""

from __future__ import annotations

from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff

# M2/W1 runtime convergence markers (bounded execution projection only).
TASK_HANDOFF_IS_BOUNDED = True
TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE = False
WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE = False
TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY = False
WORKER_STARTUP_PROMPT_IS_AUTHORITY = False


def validate_handoff_scope_containment(*, handoff_scope: str, worker_scope: str) -> None:
    """Fail-closed scope containment: worker scope must equal handoff scope.

    W1 foundation: effective worker scope is derived from TaskHandoff
    bounded_scope (see worker_vertical_slice policy derivation). Any attempt
    to widen scope beyond the handoff fails closed; narrowing is also denied
    here to keep the seam exact (W2 may relax with explicit policy).
    """
    if not isinstance(handoff_scope, str) or not handoff_scope.strip():
        raise ValueError("handoff_scope must be non-empty")
    if not isinstance(worker_scope, str) or not worker_scope.strip():
        raise ValueError("worker_scope must be non-empty")
    if worker_scope.strip() != handoff_scope.strip():
        raise ValueError(
            f"worker scope {worker_scope!r} must equal handoff bounded_scope {handoff_scope!r}; "
            "scope expansion/narrowing denied"
        )


def assert_handoff_is_bounded_projection(handoff: TaskHandoff) -> None:
    """Validate that a handoff is a bounded projection (no Plan authority duplication)."""
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    for attr in ("project_ref", "plan_ref", "milestone_ref", "work_item_ref"):
        val = getattr(handoff, attr)
        if val is not None and not isinstance(val, SemanticReference):
            raise TypeError(f"{attr} must be SemanticReference or None")
    if not handoff.bounded_scope.strip():
        raise ValueError("bounded_scope must be non-empty")


__all__ = [
    "TASK_HANDOFF_IS_BOUNDED",
    "TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE",
    "WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE",
    "TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY",
    "WORKER_STARTUP_PROMPT_IS_AUTHORITY",
    "validate_handoff_scope_containment",
    "assert_handoff_is_bounded_projection",
]
