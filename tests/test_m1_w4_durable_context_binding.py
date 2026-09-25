"""M1/W4 durable context evidence and fail-closed recovery proof."""

from __future__ import annotations

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    DurableTaskMainContextBinding,
)
from test_m3_w1_task_main_coordinator import ORIGIN_SESSION, World, _view


PLAN_ID = "plan_m1_w4_context"
PROJECT_FINGERPRINT = "d" * 64


def _context_binding(view, *, session: str = ORIGIN_SESSION, project_id: str = "aota_forge"):
    return DurableTaskMainContextBinding(
        project_id=project_id,
        project_fingerprint=PROJECT_FINGERPRINT,
        plan_binding=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind="github_issue",
            authority_ref=view.plan_authority,
            source_revision=view.plan_source_revision,
            source_digest=view.plan_digest,
        ),
        origin_task_main_session_ref=session,
    )


def test_context_binding_roundtrips_through_existing_coordinator_store(tmp_path) -> None:
    from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
    from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore

    view = _view(["W1"])
    binding = _context_binding(view)
    state = TaskMainCoordinatorState(
        coordinator_id="aota_forge:M3",
        plan_authority=view.plan_authority,
        plan_digest=view.plan_digest,
        plan_source_revision=view.plan_source_revision,
        milestone_id=view.milestone_id,
        entry_base=view.entry_base,
        origin_task_main_session_ref=ORIGIN_SESSION,
        project_id="aota_forge",
        executor_id="m1-w4-test",
        work_items=("W1",),
        wi_status={"W1": "PENDING"},
        context_binding=binding,
    )
    path = tmp_path / "coordinator.json"
    store = FileBackedTaskMainCoordinatorStore(path)
    created = store.create(state)
    store.close()

    reopened = FileBackedTaskMainCoordinatorStore(path)
    loaded = reopened.get(created.coordinator_id)
    assert loaded is not None
    assert loaded.context_binding == binding
    assert loaded.context_binding.to_dict()["IS_AUTHORITY"] is False
    reopened.close()


def test_context_binding_recovery_survives_restart_and_rejects_foreign_session(tmp_path) -> None:
    view = _view(["W1"])
    binding = _context_binding(view)
    first = World(tmp_path)
    try:
        handle = first.activate(view, context_binding=binding)
        coordinator_id = handle.coordinator_id
        second = first.reopen()
        try:
            recovered = second.recover(view, coordinator_id, context_binding=binding)
            assert recovered.refresh().context_binding == binding

            foreign_session = _context_binding(view, session="different-exact-session")
            with pytest.raises(ContextBindingError) as exc_info:
                second.recover(view, coordinator_id, context_binding=foreign_session)
            assert exc_info.value.code == "SESSION_RECOVERY_REQUIRED"
        finally:
            second.close()
    finally:
        first.close()


def test_context_binding_rejects_stale_plan_evidence(tmp_path) -> None:
    original = _view(["W1"])
    binding = _context_binding(original)
    first = World(tmp_path)
    try:
        handle = first.activate(original, context_binding=binding)
        second = first.reopen()
        try:
            drifted = _view(["W1"], marker="drifted")
            drifted_binding = _context_binding(drifted)
            with pytest.raises(ContextBindingError) as exc_info:
                second.recover(
                    drifted,
                    handle.coordinator_id,
                    context_binding=drifted_binding,
                )
            assert exc_info.value.code == "STALE_CONTEXT"
        finally:
            second.close()
    finally:
        first.close()


def test_context_binding_digest_tamper_fails_closed() -> None:
    view = _view(["W1"])
    data = _context_binding(view).to_dict()
    data["binding_digest"] = "e" * 64

    with pytest.raises(ContextBindingError) as exc_info:
        DurableTaskMainContextBinding.from_dict(data)
    assert exc_info.value.code == "CONTEXT_BINDING_CORRUPT"
