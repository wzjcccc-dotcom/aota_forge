"""AF #001 focused thin RV1 lifecycle proof."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.runtime.task_main.review_adoption import (
    AcceptedFrontierAuthorization,
    trusted_plan_authority_from_binding,
)
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.composition.thin_review_lifecycle import ThinReviewLifecycle
from tests.test_m3_w3_real_governed_autonomous_slice import World
from tests.test_r3b_review_only_adoption import (
    FRONTIER,
    PROJECT_ID,
    REVIEWER_TASK_ID,
    _authorization,
    _reviewer_handoff,
    _reviewer_sandbox,
    _view,
    _patch_git_observation,
    PLAN_AUTHORITY,
    PLAN_DIGEST,
    PLAN_ID,
    PLAN_SOURCE_REVISION,
    _AuthorityReader,
    FakeGit,
)


class _ThinHost:
    def __init__(self, world: World, sandbox) -> None:
        self.execution_store = world.exec_store
        self.sandbox = sandbox
        self.trusted_binding = SimpleNamespace(trusted_task_main_context=None)
        self._binding = CanonicalDispatchBinding(
            canonical_task_id=f"{PROJECT_ID}:M1:task-main:test",
            project_id=PROJECT_ID,
            worktree_id=sandbox.worktree_id,
            handoff=TaskHandoff(
                work_role="task-main",
                task_kind="thin-review-test",
                objective="trusted test host",
                bounded_scope="trusted test host",
                validation_expectations=("focused",),
                semantic_stop_expectations=("stop",),
            ),
            sandbox=sandbox,
            tool_surface=get_tool_surface_for_role("task-main"),
            trusted_task_main_context=None,
        )

    def invoke(self, operation: str, arguments: dict[str, str]) -> dict:
        response = dispatch_via_core(operation, arguments, self._binding)
        return {
            "ok": response.ok,
            "payload": dict(response.payload or {}) if response.ok else None,
            "error": dict(response.error or {}) if not response.ok else None,
        }


@pytest.fixture(autouse=True)
def _reset_dispatcher() -> None:
    yield
    reset_execution_dispatcher()


def _lifecycle(world: World, view, auth: AcceptedFrontierAuthorization, root: Path) -> ThinReviewLifecycle:
    sandbox = _reviewer_sandbox(root)
    bind_execution_dispatcher(world.dispatcher)
    authority_binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=PLAN_AUTHORITY,
        source_revision=PLAN_SOURCE_REVISION,
        source_digest=PLAN_DIGEST,
    )
    return ThinReviewLifecycle(
        host=_ThinHost(world, sandbox),
        coordinator_store=world.coord_store,
        coordinator_id=world.activate(view).coordinator_id,
        live_plan_view=view,
        authorization=auth,
        trusted_plan_authority=trusted_plan_authority_from_binding(
            project_id=PROJECT_ID,
            binding=authority_binding,
            authoritative_reader=_AuthorityReader(authority_binding),
        ),
        reviewer_handoff_resolver=_reviewer_handoff,
        review_adoption_git=FakeGit(),
    )


def test_thin_review_dispatch_reuses_exact_durable_reviewer_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="af001-thin-dispatch")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        lifecycle = _lifecycle(world, view, auth, tmp_path)

        result = lifecycle.dispatch_reviewer()

        assert result["task_start"]["task_id"] == REVIEWER_TASK_ID
        record = world.exec_store.get(REVIEWER_TASK_ID)
        assert record is not None
        assert record.canonical_task_id == REVIEWER_TASK_ID
        assert result["frontier_ref"] == FRONTIER
        reused = lifecycle.dispatch_reviewer()
        assert reused["task_start"] == {
            "task_id": REVIEWER_TASK_ID,
            "status": record.canonical_task_state.value,
            "reused": True,
        }
    finally:
        world.close()


def test_thin_review_reconciliation_uses_adopted_frontier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="af001-thin-reconcile")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        lifecycle.dispatch_reviewer()

        canonical = world.dispatcher.result(REVIEWER_TASK_ID)
        card = project_worker_result_card(
            canonical,
            ResultGovernanceProjection.success(),
            "reviewer",
            summary="thin RV1 review",
        )
        world.dispatcher.attach_worker_result_card(REVIEWER_TASK_ID, card)

        outcome = lifecycle.reconcile_reviewer(card.compute_card_digest())

        assert outcome.milestone_closure_ready is True
        assert outcome.receipt.completion_kind == "MILESTONE_REVIEW"
    finally:
        world.close()
