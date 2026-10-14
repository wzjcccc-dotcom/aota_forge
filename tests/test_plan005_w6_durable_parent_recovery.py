"""Focused W6 proof for recovery from durable terminal execution truth."""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.adapters.opencode.host_client import OpenCodeSessionNotFoundError
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPhase,
    InMemoryExecutionStateStore,
    OriginSessionRef,
    card_digest_for,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.runtime.completion import (
    CompletionCoordinatorError,
    DurableCompletionCoordinator,
)
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
    write_task_return_receipt,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


TASK_ID = (
    "aota_forge:plan_005_governance_self_hosting_routing_runtime_lifecycle:"
    "M2:W6:4443f277:989e23f9"
)
PROJECT_ID = "aota_forge"
PLAN_ID = "plan_005_governance_self_hosting_routing_runtime_lifecycle"
MILESTONE_ID = "M2"
WORK_ITEM_ID = "W6"
OLD_ORIGIN = "ses_deleted_historical_task_main"
CURRENT_TASK_MAIN = "ses_current_legitimate_task_main"
WORKER_SESSION = "ses_w6_worker"


def _sandbox(path: Path) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="ws-plan005-w6-recovery",
        workspace_root=str(path),
        project_id=PROJECT_ID,
        project_root=str(path),
        worktree_id="aota_forge-unbound",
        worktree_root=str(path),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _world(tmp_path: Path):
    sandbox = _sandbox(tmp_path)
    opened = handoff_write(
        mode="result",
        semantic={"summary": "completed W6 result", "work_done": "done"},
        caller_role="coder",
        sandbox=sandbox,
        task_id=TASK_ID,
    )
    write_task_return_receipt(
        sandbox,
        canonical_task_id=TASK_ID,
        result_ref=opened.ref,
        result_digest=opened.digest,
        status="completed",
    )

    store = InMemoryExecutionStateStore()
    record = store.create(
        DurableExecutionRecord(
            canonical_task_id=TASK_ID,
            executor_id="opencode",
            package_id="w6-package",
            correlation_id="w6-correlation",
            dispatch_attempt_id="w6-dispatch-attempt",
            idempotency_key="w6-idempotency",
            intent_fingerprint="f" * 64,
            origin_session_ref=OriginSessionRef(OLD_ORIGIN),
            admission_scope="opencode:coder",
        )
    )
    record = store.compare_and_swap(
        TASK_ID,
        record.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": WORKER_SESSION,
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-10-13T15:00:04+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING,
        },
    )
    result = CanonicalResult.success(
        canonical_task_id=TASK_ID,
        executor_id="opencode",
        result_data={"mechanical_execution": {"session_id": WORKER_SESSION}},
        execution_stats={"session_id": WORKER_SESSION},
        correlation_id="w6-result-correlation",
    )
    record = store.compare_and_swap(
        TASK_ID,
        record.record_revision,
        {
            "terminal_result": result.to_dict(),
            "canonical_task_state": CanonicalTaskState.COMPLETED,
        },
    )
    card = project_worker_result_card(
        result,
        ResultGovernanceProjection.success(),
        "coder",
        summary="completed W6 result",
        result_handoff_ref={"ref": TASK_ID, "digest": opened.digest},
    )
    card_digest = card.compute_card_digest()
    record = store.compare_and_swap(
        TASK_ID,
        record.record_revision,
        {
            "worker_result_card": card.canonical_dict(),
            "worker_result_card_digest": card_digest,
        },
    )
    record = store.compare_and_swap(
        TASK_ID,
        record.record_revision,
        {"delivery_state": DeliveryState.ACKNOWLEDGED},
    )
    assert card_digest_for(dict(record.worker_result_card)) == card_digest

    dispatcher = ExecutionDispatcher(
        ExecutorRegistry(),
        state_store=store,
        origin_session_ref=OriginSessionRef(CURRENT_TASK_MAIN),
    )
    completion = DurableCompletionCoordinator(
        dispatcher=dispatcher,
        store=store,
        semantic_return_provider=WorktreeSemanticReturnEvidenceProvider(sandbox),
    )
    return store, completion, opened.digest, card_digest


def test_recover_completed_result_without_live_historical_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_origin(*args, **kwargs):
        raise OpenCodeSessionNotFoundError("historical origin is unavailable", status=404)

    # Recovery must not query or re-enter the historical parent session.
    monkeypatch.setattr(
        "aota_forge.adapters.opencode.host_client.OpenCodeHostClient.get_session",
        missing_origin,
    )
    store, completion, result_digest, card_digest = _world(tmp_path)
    before = store.get(TASK_ID)

    recovered = completion.recover_terminal_result(
        TASK_ID,
        card_digest=card_digest,
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        milestone_id=MILESTONE_ID,
        work_item_id=WORK_ITEM_ID,
    )

    after = store.get(TASK_ID)
    assert recovered.canonical_task_id == TASK_ID
    assert recovered.canonical_task_state == CanonicalTaskState.COMPLETED
    assert recovered.worker_result_card_digest == card_digest
    assert recovered.worker_result_card["result_handoff_ref"]["digest"] == result_digest
    assert before.origin_session_ref == after.origin_session_ref == OriginSessionRef(OLD_ORIGIN)
    assert before.record_revision == after.record_revision


def test_recovery_fails_closed_for_wrong_digest_task_or_attempt(tmp_path: Path) -> None:
    _, completion, _, card_digest = _world(tmp_path)
    valid = {
        "card_digest": card_digest,
        "project_id": PROJECT_ID,
        "plan_id": PLAN_ID,
        "milestone_id": MILESTONE_ID,
        "work_item_id": WORK_ITEM_ID,
    }
    wrong_attempt = TASK_ID.rsplit(":", 1)[0] + ":00000000"
    wrong_task = TASK_ID.replace(":W6:", ":W5:")

    for task_id, arguments in (
        (TASK_ID, {**valid, "card_digest": "0" * 64}),
        (wrong_task, valid),
        (wrong_attempt, valid),
    ):
        with pytest.raises(CompletionCoordinatorError):
            completion.recover_terminal_result(task_id, **arguments)
