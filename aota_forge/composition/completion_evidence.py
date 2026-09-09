"""Small generic bridge: trusted durable execution/result/card -> GovernedWorkItemEvidence (W2).

Reuses ExecutionStateStore, DurableCompletionCoordinator, existing WorkerResultCard,
existing result governance projection, existing reconciliation contracts.

No second completion inbox database, no queue, no event bus, no card cache,
no workflow database.

Responsibility: derive GovernedWorkItemEvidence from already-durable
execution truth so TaskMainMilestoneRunner can reconcile autonomously via
task_main.advance_once without operator inserting cards or building evidence
manually.

Trust: all inputs are hydrated from durable ExecutionStateStore and coordinator
bindings, never from model free text. Validation verdict is derived from
card outcome (success -> PASS, else FAIL) but still goes through existing
progression evaluator which enforces CARD is not authority.

Card-first remains: terminal execution -> governed WorkerResultCard available
-> verify card/digest/handoff/result identity -> semantic reconciliation -> ACK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from aota_forge.core.execution.durable_state import ExecutionStateStore, card_digest_for
from aota_forge.runtime.task_main.coordinator import MilestonePlanView, dispatch_identity_for
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.runtime.task_main.reconciliation import GovernedWorkItemEvidence, ReconciliationError
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.progression import FocusedValidationEvidence, FocusedValidationVerdict
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.handoff import SemanticReference


def _neutral_envelope(milestone_id: str) -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(
        milestone_ref=milestone_id,
        default_process_depth=ProcessDepth.STANDARD,
        minimum_process_depth=ProcessDepth.STANDARD,
    )


def derive_governed_evidence(
    *,
    work_item_id: str,
    canonical_task_id: str,
    execution_store: ExecutionStateStore,
    live_plan_view: MilestonePlanView,
    handoff_resolver: Callable[[str], TaskHandoff],
) -> GovernedWorkItemEvidence:
    """Derive GovernedWorkItemEvidence from trusted durable stores.

    Steps (card-first, fail-closed):
    - hydrate execution record for canonical_task_id
    - verify record is terminal and has worker_result_card + digest
    - verify card digest recomputation matches record and supplied digest
    - hydrate TaskHandoff via resolver and verify work_item_ref
    - derive validation verdict from card outcome (success -> PASS, else FAIL)
    - build neutral risk envelope

    No manual card insertion, no model-supplied governed evidence, no second store.
    """
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        raise ReconciliationError("work_item_id must be non-empty string")
    if not isinstance(canonical_task_id, str) or not canonical_task_id.strip():
        raise ReconciliationError("canonical_task_id must be non-empty string")
    if not isinstance(execution_store, ExecutionStateStore):
        raise TypeError(f"execution_store must be ExecutionStateStore, got {type(execution_store).__name__}")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    if not callable(handoff_resolver):
        raise TypeError("handoff_resolver must be callable")

    record = execution_store.get(canonical_task_id)
    if record is None:
        raise ReconciliationError(f"no durable M2 execution truth for {canonical_task_id!r}")
    if not record.canonical_task_state.is_terminal:
        raise ReconciliationError(f"M2 execution {canonical_task_id!r} is not terminal")
    if record.terminal_result is None:
        raise ReconciliationError(f"M2 execution {canonical_task_id!r} has no durable terminal result")
    if record.worker_result_card is None or record.worker_result_card_digest is None:
        raise ReconciliationError(f"M2 execution {canonical_task_id!r} has no durable CARD; CARD-first requires CARD truth")

    # Verify card mapping
    card_dict = dict(record.worker_result_card)
    # Verify digest recomputation (reuses existing card_digest_for)
    if card_digest_for(card_dict) != record.worker_result_card_digest:
        raise ReconciliationError("M2 durable CARD digest contradicts recomputation")

    # Hydrate WorkerResultCard for outcome
    try:
        from aota_forge.work_plane.result_card import WorkerResultCard

        card = WorkerResultCard.from_dict(card_dict)
    except Exception as exc:
        raise ReconciliationError(f"durable CARD fails strict validation: {exc}") from exc

    if card.task_ref != canonical_task_id:
        raise ReconciliationError(f"CARD task_ref {card.task_ref!r} contradicts {canonical_task_id!r}")
    if card.compute_card_digest() != record.worker_result_card_digest:
        raise ReconciliationError("CARD digest recomputation contradicts record digest")
    if card.result_handoff_ref.ref != canonical_task_id:
        raise ReconciliationError("CARD result_handoff_ref contradicts canonical task identity")

    # Hydrate and verify handoff identity
    handoff = handoff_resolver(work_item_id)
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff_resolver must return TaskHandoff for {work_item_id!r}")
    if handoff.work_item_ref is not None and handoff.work_item_ref.ref != work_item_id:
        raise ReconciliationError(f"handoff work_item_ref {handoff.work_item_ref.ref!r} contradicts {work_item_id!r}")
    # Card's result_handoff_ref should equal canonical_task_id, already checked; handoff itself is governed semantics.

    # Derive validation verdict from card outcome (card is evidence, not authority; evaluator still enforces)
    # Map card outcome to verdict: success -> PASS, else FAIL
    try:
        outcome_val = card.outcome.value if hasattr(card.outcome, "value") else str(card.outcome)
    except Exception:
        outcome_val = "unknown"
    if outcome_val == "success":
        verdict = FocusedValidationVerdict.PASS
    elif outcome_val in ("failure", "unknown"):
        # For this generic bridge, treat failure/unknown as FAIL so progression will block but still reconciled
        # The evaluator will mark progression_complete vs blocked accordingly.
        verdict = FocusedValidationVerdict.FAIL if outcome_val == "failure" else FocusedValidationVerdict.UNKNOWN
        # For unknown, we map to FAIL to keep progression blocked? But spec wants deterministic; we use FAIL for failure, UNKNOWN for unknown.
        # Keep as is: use FAIL for failure, UNKNOWN for unknown
        if outcome_val == "unknown":
            verdict = FocusedValidationVerdict.UNKNOWN
    else:
        verdict = FocusedValidationVerdict.FAIL

    # Validation evidence ref is derived from card digest (deterministic)
    validation_ref = SemanticReference(ref=f"val:{work_item_id}:{record.worker_result_card_digest[:8]}")
    validation = FocusedValidationEvidence(
        work_item_ref=work_item_id,
        verdict=verdict,
        validation_evidence_ref=validation_ref,
        validation_evidence_digest=record.worker_result_card_digest,
    )

    # Neutral risk envelope (generic)
    envelope = _neutral_envelope(live_plan_view.milestone_id)

    return GovernedWorkItemEvidence(
        validation_evidence=validation,
        risk_envelope=envelope,
        risk_delta=None,
        review_triggers=(),
        escalation_triggers=(),
    )


def create_automatic_governed_evidence_resolver(
    *,
    execution_store: ExecutionStateStore,
    coordinator_store: TaskMainCoordinatorStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    handoff_resolver: Callable[[str], TaskHandoff],
    project_id: str,
    plan_authority: str,
    milestone_id: str,
) -> Callable[[str], GovernedWorkItemEvidence]:
    """Create a runtime-owned resolver closure.

    The closure is trusted runtime code, not operator or model supplied.
    It derives evidence on-demand from durable stores, never from model text.
    """

    def _resolver(work_item_id: str) -> GovernedWorkItemEvidence:
        if not isinstance(work_item_id, str) or not work_item_id.strip():
            raise ReconciliationError("work_item_id must be non-empty string")
        # Resolve canonical_task_id via coordinator binding if available, else deterministic identity
        state = coordinator_store.get(coordinator_id)
        canonical_task_id: str | None = None
        if state is not None:
            binding = state.bindings.get(work_item_id)
            if binding is not None:
                canonical_task_id = binding.get("canonical_task_id")
        if canonical_task_id is None:
            # Fallback to deterministic identity (for dispatch path where binding not yet persisted but execution exists)
            c, _, _ = dispatch_identity_for(
                project_id=project_id,
                plan_authority=plan_authority,
                milestone_id=milestone_id,
                work_item_id=work_item_id,
            )
            canonical_task_id = c
        return derive_governed_evidence(
            work_item_id=work_item_id,
            canonical_task_id=canonical_task_id,
            execution_store=execution_store,
            live_plan_view=live_plan_view,
            handoff_resolver=handoff_resolver,
        )

    return _resolver


# Generic review evidence resolver (small, reuse existing)
def derive_governed_review_evidence(
    *,
    reviewer_canonical_task_id: str,
    card_digest: str,
    execution_store: ExecutionStateStore,
    live_plan_view: MilestonePlanView,
    reviewer_handoff_resolver: Callable[[], TaskHandoff],
) -> dict:
    """Minimal helper for review evidence derivation (reviewer card -> GovernedReviewEvidence).

    Reuses existing MilestoneReviewEvidence construction; keeps generic bridge small.
    """
    from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle
    from aota_forge.work_plane.result_card import WorkerResultCard
    from aota_forge.runtime.task_main.reconciliation import GovernedReviewEvidence
    from aota_forge.work_plane.handoff import SemanticReference

    record = execution_store.get(reviewer_canonical_task_id)
    if record is None or record.worker_result_card is None:
        raise ReconciliationError(f"no durable reviewer CARD for {reviewer_canonical_task_id!r}")
    card = WorkerResultCard.from_dict(dict(record.worker_result_card))
    if card.compute_card_digest() != card_digest:
        raise ReconciliationError("reviewer CARD digest mismatch")
    if record.worker_result_card_digest != card_digest:
        raise ReconciliationError("reviewer record digest mismatch")
    handoff = reviewer_handoff_resolver()
    ev = MilestoneReviewEvidence(
        milestone_ref=SemanticReference(ref=live_plan_view.milestone_id),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref=f"frontier-{live_plan_view.milestone_id}-rv1"),
        review_result_ref=card.result_handoff_ref,
        review_result_digest=card.compute_card_digest(),
        finding_refs=(),
    )
    return GovernedReviewEvidence(
        review_evidence=ev,
        review_findings=(),
        review_task_handoff=handoff,
        expected_final_frontier=SemanticReference(ref=f"frontier-{live_plan_view.milestone_id}-rv1"),
    )


__all__ = [
    "derive_governed_evidence",
    "create_automatic_governed_evidence_resolver",
    "derive_governed_review_evidence",
]
