"""R3B accepted-frontier review-only adoption seam."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.review_adoption import (
    AcceptedFrontierAuthorization,
    ReviewAdoptionError,
    TrustedGitFrontier,
    mint_accepted_frontier_authorization,
)
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_DISPATCHED_REVIEW,
    DISPOSITION_INTEGRATED_REVIEW_REQUIRED,
    DISPOSITION_MILESTONE_CLOSURE_READY,
    DISPOSITION_WAITING_FOR_WORKERS,
    TaskMainMilestoneRunner,
)
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from aota_forge.work_plane.steward_finalizer import GitPort
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.runtime.task_main.reconciliation import GovernedReviewEvidence, ReconciliationError
from tests.test_m3_w3_real_governed_autonomous_slice import World
from aota_forge.runtime.task_main import review_adoption as review_adoption_module


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_001_gov2_session_first_adoption"
MILESTONE_ID = "M1"
PLAN_AUTHORITY = local_plan_authority_reference(PROJECT_ID, PLAN_ID)
PLAN_DIGEST = "d" * 64
PLAN_SOURCE_REVISION = "R4"
ENTRY_BASE = "c" * 40
FRONTIER = "a" * 40
TREE = "b" * 40
REVIEWER_TASK_ID = f"{PROJECT_ID}:{MILESTONE_ID}:RV1:attempt-1"


class FakeGit(GitPort):
    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        return ancestor == ENTRY_BASE

    def resolve_tree(self, repo: Path, commit: str) -> str:
        return TREE


def _view() -> MilestonePlanView:
    return MilestonePlanView(
        plan_authority=PLAN_AUTHORITY,
        plan_digest=PLAN_DIGEST,
        plan_source_revision=PLAN_SOURCE_REVISION,
        milestone_id=MILESTONE_ID,
        entry_base=ENTRY_BASE,
        graph=MilestoneWorkItemGraph(milestone_ref=MILESTONE_ID, work_items=["W1"]),
        milestone_user_approval_satisfied=True,
    )


def _authorization(world: World, root: Path) -> AcceptedFrontierAuthorization:
    view = _view()
    handle = world.activate(view)
    state = world.coord_store.get(handle.coordinator_id)
    assert state is not None
    return mint_accepted_frontier_authorization(
        state=state,
        live_plan_view=view,
        plan_id=PLAN_ID,
        authorization_ref="local-governance-adoption://aota_forge/M1/R3B",
        trusted_frontier=TrustedGitFrontier(
            worktree_root=str(root.resolve()),
            entry_base=ENTRY_BASE,
            frontier_ref=FRONTIER,
            tree_ref=TREE,
            clean=True,
        ),
    )


def _reviewer_handoff() -> TaskHandoff:
    return TaskHandoff(
        work_role="reviewer",
        task_kind="r3b-review",
        objective="review the adopted frontier",
        bounded_scope="review-only adopted frontier",
        validation_expectations=("validate adopted frontier",),
        semantic_stop_expectations=("stop on review ambiguity",),
        work_item_ref=SemanticReference(ref="M1/RV1"),
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
    )


def _clean_observation(*, dirty: bool = False, head: str = FRONTIER) -> dict[str, object]:
    return {
        "available": True,
        "is_dirty": dirty,
        "head_sha": head,
    }


def _patch_git_observation(monkeypatch: pytest.MonkeyPatch, *, dirty: bool = False, head: str = FRONTIER) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None: _clean_observation(dirty=dirty, head=head),
    )


def _runner(
    world: World,
    view: MilestonePlanView,
    auth: AcceptedFrontierAuthorization,
    root: Path,
    *,
    reviewer_handoff_resolver=_reviewer_handoff,
) -> TaskMainMilestoneRunner:
    return TaskMainMilestoneRunner(
        coordinator_store=world.coord_store,
        execution_store=world.exec_store,
        execution_dispatcher=world.dispatcher,
        live_plan_view=view,
        handoff_resolver=lambda work_item_id: (_ for _ in ()).throw(
            AssertionError(f"W handoff resolver must not run: {work_item_id}")
        ),
        reviewer_handoff_resolver=reviewer_handoff_resolver,
        coordinator_id=world.activate(view).coordinator_id,
        accepted_frontier_authorization=auth,
        review_adoption_worktree_root=root,
        review_adoption_git=FakeGit(),
    )


def test_adoption_routes_directly_to_reviewer_without_w_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-first-adoption")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)

        outcome = runner.advance_once()

        assert outcome.disposition == DISPOSITION_DISPATCHED_REVIEW
        assert outcome.dispatched == (REVIEWER_TASK_ID,)
        assert world.exec_store.get("aota_forge:M1:W1:attempt-1") is None
        assert world.exec_store.get(REVIEWER_TASK_ID) is not None
        state = world.coord_store.get(runner.coordinator_id)
        assert state is not None
        assert state.wi_status == {"W1": "PENDING"}
        assert state.bindings == {}
        assert state.wi_semantic_status == {}
        assert state.reconciled_completions == {}
        assert state.working_truth is None
        assert state.frontier_ref is not None
        assert state.frontier_ref["ref"] == FRONTIER
    finally:
        world.close()


def test_adoption_does_not_fall_back_to_work_when_reviewer_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-review-unavailable")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path, reviewer_handoff_resolver=None)

        outcome = runner.advance_once()

        assert outcome.disposition == DISPOSITION_INTEGRATED_REVIEW_REQUIRED
        assert outcome.integrated_review_required is True
        assert outcome.dispatched == ()
        assert world.exec_store.list_all() == []
        state = world.coord_store.get(runner.coordinator_id)
        assert state is not None
        assert state.wi_status == {"W1": "PENDING"}
    finally:
        world.close()


def test_caller_constructed_authorization_has_no_adoption_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-untrusted-authorization")
    try:
        view = _view()
        auth = dataclasses.replace(
            _authorization(world, tmp_path), provenance_digest=""
        )
        before = world.coord_store.get(world.activate(view).coordinator_id)
        assert before is not None
        with pytest.raises(ReviewAdoptionError, match="trusted runtime provenance"):
            _runner(world, view, auth, tmp_path).advance_once()
        assert world.coord_store.get(before.coordinator_id) == before
    finally:
        world.close()


def test_untracked_runtime_database_is_excluded_but_source_changes_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None: {
            "available": True,
            "is_dirty": True,
            "head_sha": FRONTIER,
            "staged": {"paths": []},
            "unstaged": {"paths": []},
            "untracked": {
                "paths": [
                    ".aota/governance.sqlite3",
                    ".aota/governance.sqlite3-wal",
                    ".aota/governance.sqlite3-shm",
                ]
            },
        },
    )
    world = World(tmp_path, name="r3b-runtime-db")
    try:
        view = _view()
        outcome = _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        assert outcome.disposition == DISPOSITION_DISPATCHED_REVIEW
    finally:
        world.close()


def test_truncated_git_status_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None: {
            "available": True,
            "is_dirty": True,
            "head_sha": FRONTIER,
            "staged": {"paths": [], "truncated": False},
            "unstaged": {"paths": [], "truncated": False},
            "untracked": {
                "paths": [".aota/governance.sqlite3"],
                "truncated": True,
            },
        },
    )
    world = World(tmp_path, name="r3b-truncated-status")
    try:
        view = _view()
        handle = world.activate(view)
        before = world.coord_store.get(handle.coordinator_id)
        assert before is not None
        with pytest.raises(ReviewAdoptionError, match="dirty"):
            _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        assert world.coord_store.get(handle.coordinator_id) == before
        assert world.exec_store.list_all() == []
    finally:
        world.close()


def test_adopted_frontier_is_revalidated_before_next_review_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-frontier-refresh")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW
        before = world.coord_store.get(runner.coordinator_id)
        assert before is not None
        _patch_git_observation(monkeypatch, head="e" * 40)

        with pytest.raises(ReviewAdoptionError, match="candidate frontier"):
            runner.advance_once()

        assert world.coord_store.get(runner.coordinator_id) == before
        assert len(world.exec_store.list_all()) == 1
    finally:
        world.close()


def test_adoption_is_idempotent_while_reviewer_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-idempotent")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)
        first = runner.advance_once()
        second = runner.advance_once()

        assert first.disposition == DISPOSITION_DISPATCHED_REVIEW
        assert second.disposition == DISPOSITION_WAITING_FOR_WORKERS
        assert world.exec_store.list_all() and len(world.exec_store.list_all()) == 1
        assert world.coord_store.get(runner.coordinator_id).coordinator_revision == 3
    finally:
        world.close()


def test_adoption_rehydrates_after_restart_without_w_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-restart")
    restarted = None
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW

        restarted = world.reopen()
        recovered = _runner(restarted, view, auth, tmp_path)
        outcome = recovered.advance_once()

        assert outcome.disposition == DISPOSITION_WAITING_FOR_WORKERS
        assert restarted.exec_store.get("aota_forge:M1:W1:attempt-1") is None
        assert len(restarted.exec_store.list_all()) == 1
    finally:
        if restarted is not None:
            restarted.close()
        else:
            world.close()


def test_dirty_frontier_fails_closed_without_state_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch, dirty=True)
    world = World(tmp_path, name="r3b-dirty")
    try:
        view = _view()
        handle = world.activate(view)
        before = world.coord_store.get(handle.coordinator_id)
        assert before is not None
        with pytest.raises(ReviewAdoptionError, match="dirty"):
            _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        after = world.coord_store.get(handle.coordinator_id)
        assert after == before
        assert world.exec_store.list_all() == []
    finally:
        world.close()


def test_authorized_frontier_mismatch_fails_closed_without_state_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch, head="e" * 40)
    world = World(tmp_path, name="r3b-frontier-mismatch")
    try:
        view = _view()
        handle = world.activate(view)
        before = world.coord_store.get(handle.coordinator_id)
        assert before is not None
        with pytest.raises(ReviewAdoptionError, match="candidate frontier"):
            _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        after = world.coord_store.get(handle.coordinator_id)
        assert after == before
        assert world.exec_store.list_all() == []
    finally:
        world.close()


def test_reviewer_reconciliation_uses_adopted_frontier_for_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-review-closure")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)
        dispatched = runner.advance_once()
        assert dispatched.disposition == DISPOSITION_DISPATCHED_REVIEW

        canonical = world.dispatcher.result(REVIEWER_TASK_ID)
        card = project_worker_result_card(
            canonical,
            ResultGovernanceProjection.success(),
            "reviewer",
            summary="R3B adopted-frontier review",
        )
        world.dispatcher.attach_worker_result_card(REVIEWER_TASK_ID, card)

        def review_resolver(canonical_task_id: str, card_digest: str) -> GovernedReviewEvidence:
            record = world.exec_store.get(canonical_task_id)
            assert record is not None and record.worker_result_card is not None
            reviewer_card = WorkerResultCard.from_dict(record.worker_result_card)
            evidence = MilestoneReviewEvidence(
                milestone_ref=SemanticReference(ref=MILESTONE_ID),
                review_cycle=ReviewCycle.RV1,
                reviewed_frontier_ref=SemanticReference(ref=FRONTIER),
                review_result_ref=reviewer_card.result_handoff_ref,
                review_result_digest=card_digest,
                finding_refs=(),
            )
            return GovernedReviewEvidence(
                review_evidence=evidence,
                review_task_handoff=_reviewer_handoff(),
                expected_final_frontier=SemanticReference(ref=FRONTIER),
            )

        runner._governed_review_resolver = review_resolver
        outcome = runner.advance_once()

        assert outcome.disposition == DISPOSITION_MILESTONE_CLOSURE_READY
        assert outcome.milestone_closure_ready is True
        state = world.coord_store.get(runner.coordinator_id)
        assert state is not None
        assert state.wi_status == {"W1": "PENDING"}
        assert state.bindings == {}
        assert len(state.reconciled_completions) == 1
        assert state.reconciled_completions[REVIEWER_TASK_ID]["completion_kind"] == "MILESTONE_REVIEW"
        revision = state.coordinator_revision
        replay = runner.advance_once()
        assert replay.disposition == DISPOSITION_MILESTONE_CLOSURE_READY
        assert world.coord_store.get(runner.coordinator_id).coordinator_revision == revision
    finally:
        world.close()


def test_reviewer_reconciliation_rejects_handoff_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-handoff-mismatch")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW

        canonical = world.dispatcher.result(REVIEWER_TASK_ID)
        card = project_worker_result_card(
            canonical,
            ResultGovernanceProjection.success(),
            "reviewer",
            summary="R3B adopted-frontier review",
        )
        world.dispatcher.attach_worker_result_card(REVIEWER_TASK_ID, card)
        tampered_handoff = dataclasses.replace(
            _reviewer_handoff(), objective="review a different frontier"
        )

        def review_resolver(canonical_task_id: str, card_digest: str) -> GovernedReviewEvidence:
            assert canonical_task_id == REVIEWER_TASK_ID
            reviewer_card = WorkerResultCard.from_dict(
                world.exec_store.get(canonical_task_id).worker_result_card  # type: ignore[union-attr]
            )
            evidence = MilestoneReviewEvidence(
                milestone_ref=SemanticReference(ref=MILESTONE_ID),
                review_cycle=ReviewCycle.RV1,
                reviewed_frontier_ref=SemanticReference(ref=FRONTIER),
                review_result_ref=reviewer_card.result_handoff_ref,
                review_result_digest=card_digest,
                finding_refs=(),
            )
            return GovernedReviewEvidence(
                review_evidence=evidence,
                review_task_handoff=tampered_handoff,
                expected_final_frontier=SemanticReference(ref=FRONTIER),
            )

        runner._governed_review_resolver = review_resolver
        before = world.coord_store.get(runner.coordinator_id)
        assert before is not None
        with pytest.raises(ReconciliationError, match="TaskHandoff digest"):
            runner.advance_once()
        assert world.coord_store.get(runner.coordinator_id) == before
    finally:
        world.close()
