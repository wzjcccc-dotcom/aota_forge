"""M2/W4 RV1 F04 hardening: WorkerResultCard / durable execution identity.

`ExecutionDispatcher.attach_worker_result_card` verified CARD digest and
attachment semantics but did not independently ensure the CARD belongs to the
SAME canonical task as the durable execution record. This repair closes that
seam with the smallest invariant: the existing CARD identity field (task_ref;
no new field, no third CARD ontology) must equal the target record's
canonical_task_id BEFORE any mutation or digest attachment.

Gates proven here:

    CROSS_TASK_CARD_ATTACH_REJECTED=yes
    THIRD_CARD_ONTOLOGY_CREATED=no
"""

from __future__ import annotations

import pytest

from aota_forge.core.execution.dispatcher import (
    AdapterProtocolError,
    ExecutionDispatcher,
)
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    InMemoryExecutionStateStore,
    card_digest_for,
)
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.work_plane.result_card import project_worker_result_card


def _record(task_id: str) -> DurableExecutionRecord:
    return DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="durable-fake",
        package_id=f"pkg-{task_id}",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"att-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint=f"fp-{task_id}",
        execution_phase=ExecutionPhase.PREPARED,
    )


def _card_for(task_id: str):
    result = CanonicalResult.success(
        canonical_task_id=task_id,
        executor_id="durable-fake",
        result_data={"proof": task_id},
        correlation_id=f"corr-{task_id}",
    )
    return project_worker_result_card(
        result,
        ResultGovernanceProjection.success(),
        "coder",
        summary=f"durable terminal worker result for {task_id}",
    )


def _world() -> tuple[ExecutionDispatcher, InMemoryExecutionStateStore]:
    store = InMemoryExecutionStateStore()
    store.create(_record("task-A"))
    store.create(_record("task-B"))
    dispatcher = ExecutionDispatcher(ExecutorRegistry(), state_store=store)
    return dispatcher, store


def test_cross_task_card_attach_is_rejected_without_mutation() -> None:
    """Valid CARD for task-A must NEVER attach to task-B's durable record."""
    dispatcher, store = _world()
    card_a = _card_for("task-A")
    assert card_a.canonical_dict()["task_ref"] == "task-A"

    before = store.get("task-B")
    with pytest.raises(AdapterProtocolError):
        dispatcher.attach_worker_result_card("task-B", card_a)
    after = store.get("task-B")

    assert after is not None
    assert after.record_revision == before.record_revision, "rejection must not mutate the record"
    assert after.worker_result_card is None and after.worker_result_card_digest is None, (
        "no digest attachment may survive an identity mismatch"
    )

    # The matching attach still succeeds (bounded repair, not a lockdown).
    attached = dispatcher.attach_worker_result_card("task-A", card_a)
    assert attached.worker_result_card is not None
    assert card_digest_for(dict(attached.worker_result_card)) == attached.worker_result_card_digest
    assert attached.worker_result_card["task_ref"] == "task-A"


def test_identity_check_is_independent_of_digest_validity() -> None:
    """A well-formed CARD for task-A (valid digest over ITS OWN payload) is
    still rejected for task-B purely on identity: digest correctness never
    substitutes for canonical task identity."""
    dispatcher, store = _world()

    class _ForeignCardSurface:
        """Not a real WorkerResultCard: a foreign object presenting the two
        required seam methods with task-A identity inside."""

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def canonical_dict(self) -> dict:
            return dict(self._payload)

        def compute_card_digest(self) -> str:
            return card_digest_for(self._payload)

    foreign = _ForeignCardSurface(_card_for("task-A").canonical_dict())
    with pytest.raises(AdapterProtocolError):
        dispatcher.attach_worker_result_card("task-B", foreign)
    assert store.get("task-B").worker_result_card is None


def test_mismatch_surfaces_even_after_the_real_card_is_durable() -> None:
    """Once task-A legitimately holds its CARD, a mismatched attempt (A's CARD
    onto B) still fails closed, and A's durable truth is untouched."""
    dispatcher, store = _world()
    card_a = _card_for("task-A")
    durable_a = dispatcher.attach_worker_result_card("task-A", card_a)
    with pytest.raises(AdapterProtocolError):
        dispatcher.attach_worker_result_card("task-B", card_a)
    again = store.get("task-A")
    assert again.worker_result_card_digest == durable_a.worker_result_card_digest
    assert store.get("task-B").worker_result_card is None


def test_same_card_replay_is_idempotent() -> None:
    dispatcher, _store = _world()
    card_a = _card_for("task-A")
    first = dispatcher.attach_worker_result_card("task-A", card_a)
    second = dispatcher.attach_worker_result_card("task-A", card_a)
    assert second.record_revision == first.record_revision, "identical matching replay must not re-CAS"
