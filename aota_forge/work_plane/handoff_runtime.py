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

# M2/W2 validation integrity firewall (M1 frozen acceptance freeze).
# The TaskHandoff freezes acceptance + validation expectations; Coder
# cannot redefine or weaken them to pass, nor silently weaken existing
# tests (test modifications require typed justification).
TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS = True
TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS = True
CODER_CAN_REDEFINE_ACCEPTANCE = False
CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS = False
CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS = False
TEST_MODIFICATION_REQUIRES_JUSTIFICATION = True


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


def frozen_acceptance_expectations(handoff: TaskHandoff) -> tuple[str, ...]:
    """Return the frozen acceptance surface (task-main owned, Coder immutable).

    The frozen S1 TaskHandoff contract carries acceptance intent as the
    exact objective text plus the validation expectations tuple (there is
    no separate acceptance field by design — no Plan authority
    duplication). Both are frozen: the Coder Result must preserve them
    exactly.
    """
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    return (handoff.objective, *tuple(handoff.validation_expectations))


def frozen_validation_expectations(handoff: TaskHandoff) -> tuple[str, ...]:
    """Return the frozen validation expectations (task-main owned, Coder immutable)."""
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    return tuple(handoff.validation_expectations)


def assert_result_preserves_frozen_expectations(
    *,
    handoff: TaskHandoff,
    claimed_acceptance: tuple[str, ...] | list[str],
    claimed_validation: tuple[str, ...] | list[str],
) -> None:
    """Validation integrity firewall: fail closed when a Coder Result
    claims redefined or weakened acceptance/validation expectations.

    Delegates to the typed coder_lifecycle firewall (no string-policy
    heuristics); the narrowest reliable seam is exact expectation-set
    equality against the frozen TaskHandoff.
    """
    from aota_forge.work_plane.coder_lifecycle import assert_acceptance_expectations_frozen

    assert_acceptance_expectations_frozen(
        handoff_acceptance=frozen_acceptance_expectations(handoff),
        handoff_validation=frozen_validation_expectations(handoff),
        claimed_acceptance=tuple(claimed_acceptance),
        claimed_validation=tuple(claimed_validation),
    )


__all__ = [
    "TASK_HANDOFF_IS_BOUNDED",
    "TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE",
    "WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE",
    "TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY",
    "WORKER_STARTUP_PROMPT_IS_AUTHORITY",
    "TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS",
    "TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS",
    "CODER_CAN_REDEFINE_ACCEPTANCE",
    "CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS",
    "CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS",
    "TEST_MODIFICATION_REQUIRES_JUSTIFICATION",
    "validate_handoff_scope_containment",
    "assert_handoff_is_bounded_projection",
    "frozen_acceptance_expectations",
    "frozen_validation_expectations",
    "assert_result_preserves_frozen_expectations",
]
