"""Focused proof for trusted completed-review adoption (M3/CLOSE-R1)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from aota_forge.composition import task_main_host_bootstrap
from aota_forge.composition.thin_review_lifecycle import (
    ThinReviewLifecycleError,
    validate_completed_trusted_review_artifact,
)
from aota_forge.runtime.task_main.reconciliation import (
    CompletedReviewAdoptionReceipt,
    ReconciliationError,
)
from aota_forge.runtime.task_main.coordinator_store import StaleCoordinatorRevisionError
from aota_forge.runtime.task_main.review_adoption import (
    CompletedTrustedReviewEvidence,
    ReviewAdoptionError,
)
from aota_forge.work_plane.milestone_review import ReviewCycle
from tests.test_af001_thin_review_lifecycle import _lifecycle
from tests.test_r3b_review_only_adoption import (
    ENTRY_BASE,
    FRONTIER,
    PLAN_DIGEST,
    _authorization,
    _patch_git_observation,
    _view,
)
from tests.test_m3_w3_real_governed_autonomous_slice import World
from tests.test_m3_w3_real_governed_autonomous_slice import _evidence, _resolver
from aota_forge.runtime.task_main.runner import TaskMainMilestoneRunner


def _artifact(path: Path, *, frontier: str = ENTRY_BASE, blocking: list[str] | None = None) -> Path:
    payload = {
        "record": "AF_PLAN003_M3_RV1_INTEGRATED_PRODUCTION_REVIEW",
        "status": "PASS_WITH_FINDINGS",
        "project_id": "aota_forge",
        "plan_id": "plan_001_gov2_session_first_adoption",
        "milestone_id": "M1",
        "review_id": "RV1",
        "plan_revision_final": "R17",
        "plan_file_digest_final": PLAN_DIGEST,
        "store_digest_final": PLAN_DIGEST,
        "plan_file_store_digest_match_after": True,
        "journal_terminal_verified_after": True,
        "production_validation_sufficient": True,
        "source_frontier": {
            "candidate": frontier,
            "main_head": frontier,
            "runtime_source_commit": frontier,
            "production_runtime_source_aligned": True,
            "source_chain": [frontier],
            "source_chain_contiguous": True,
            "source_code_changed_during_review": False,
        },
        "blocking_findings": blocking or [],
        "non_blocking_findings": ["F-D by_ref hydration"],
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _validated(world: World, view, lifecycle, path: Path) -> CompletedTrustedReviewEvidence:
    return validate_completed_trusted_review_artifact(
        artifact_ref=path,
        live_plan_view=view,
        trusted_plan_authority=lifecycle.trusted_plan_authority,
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=ENTRY_BASE,
    )


def test_completed_review_artifact_validation_binds_digest_and_runtime_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-validator")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")

        evidence = _validated(world, view, lifecycle, artifact)

        assert len(evidence.artifact_digest) == 64
        assert evidence.reviewed_frontier_ref == ENTRY_BASE
        assert evidence.source_runtime_aligned is True
        assert evidence.source_chain_contiguous is True
        assert evidence.blocking_findings == ()
    finally:
        world.close()


def test_completed_review_adoption_is_cas_durable_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-adoption")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")
        evidence = _validated(world, view, lifecycle, artifact)

        first = lifecycle.adopt_completed_review(evidence)
        state = world.coord_store.get(lifecycle.coordinator_id)
        assert state is not None
        assert first.milestone_closure_ready is True
        assert first.replayed is False
        assert first.physical_dispatch_attempts == 0
        assert state.wi_status == {"W1": "PENDING"}
        assert state.bindings == {}
        assert state.wi_semantic_status == {}
        assert len(state.reconciled_completions) == 1
        assert state.frontier_ref["ref"] == FRONTIER
        assert state.frontier_ref["status"] == "REVIEW_ONLY_CANDIDATE"
        assert world.exec_store.list_all() == []

        second = lifecycle.adopt_completed_review(evidence)
        assert second.replayed is True
        assert second.receipt.receipt_digest == first.receipt.receipt_digest
        assert world.coord_store.get(lifecycle.coordinator_id).coordinator_revision == 3
    finally:
        world.close()


def test_completed_review_receipt_is_authority_negative_and_readiness_rehydrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-readiness")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")
        evidence = _validated(world, view, lifecycle, artifact)
        outcome = lifecycle.adopt_completed_review(evidence)
        payload = outcome.receipt.to_dict()

        assert payload["completion_kind"] == "COMPLETED_REVIEW_ADOPTION"
        assert "canonical_task_id" not in payload
        assert "card_digest" not in payload
        assert "result_handoff_ref" not in payload
        assert "accepted" not in payload
        assert outcome.receipt.closure_frontier_ref == FRONTIER
        assert outcome.receipt.reviewed_frontier_ref == ENTRY_BASE
        assert outcome.receipt.frontier_relation == "GOVERNANCE_ONLY_DESCENDANT"
        assert CompletedReviewAdoptionReceipt.from_dict(payload) == outcome.receipt

        state = world.coord_store.get(lifecycle.coordinator_id)
        readiness = task_main_host_bootstrap._readiness_from_durable_state(
            state,
            coordinator_id=lifecycle.coordinator_id,
            live_view=view,
        )
        assert readiness.ready_for_project_steward is True
        assert readiness.reviewed_frontier_ref.ref == FRONTIER
        assert ENTRY_BASE in readiness.supporting_evidence_refs
    finally:
        world.close()


@pytest.mark.parametrize(
    "field,value",
    (
        ("project_id", "foreign"),
        ("plan_id", "plan_foreign"),
        ("milestone_id", "M2"),
        ("plan_digest", "e" * 64),
        ("reviewed_frontier_ref", "f" * 40),
        ("artifact_digest", "0" * 64),
    ),
)
def test_completed_review_adoption_rejects_identity_or_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name=f"completed-review-negative-{field}")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")
        evidence = _validated(world, view, lifecycle, artifact)
        tampered = dataclasses.replace(evidence, **{field: value})
        with pytest.raises((ThinReviewLifecycleError, ReviewAdoptionError)):
            lifecycle.adopt_completed_review(tampered)
        state = world.coord_store.get(lifecycle.coordinator_id)
        assert state is not None
        assert state.reconciled_completions == {}
    finally:
        world.close()


def test_completed_review_artifact_rejects_blocking_findings_and_insufficient_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-findings")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        blocked = _artifact(tmp_path / "blocked.json", blocking=["blocking"])
        with pytest.raises(ThinReviewLifecycleError, match="blocking"):
            _validated(world, view, lifecycle, blocked)

        insufficient = _artifact(tmp_path / "insufficient.json")
        raw = json.loads(insufficient.read_text(encoding="utf-8"))
        raw["production_validation_sufficient"] = False
        insufficient.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        with pytest.raises(ThinReviewLifecycleError, match="sufficient"):
            _validated(world, view, lifecycle, insufficient)
    finally:
        world.close()


def test_completed_review_adoption_rejects_active_execution_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-active-execution")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")
        evidence = _validated(world, view, lifecycle, artifact)
        runner = TaskMainMilestoneRunner(
            coordinator_store=world.coord_store,
            execution_store=world.exec_store,
            execution_dispatcher=world.dispatcher,
            live_plan_view=view,
            handoff_resolver=_resolver(["W1"]),
            governed_evidence_resolver=lambda _work_item: _evidence("W1"),
            coordinator_id=lifecycle.coordinator_id,
        )
        runner.advance_once()
        with pytest.raises(ReviewAdoptionError):
            lifecycle.adopt_completed_review(evidence)
    finally:
        world.close()


def test_completed_review_adoption_cas_conflict_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="completed-review-cas-conflict")
    try:
        view = _view()
        auth = _authorization(world, tmp_path, frontier=FRONTIER)
        lifecycle = _lifecycle(world, view, auth, tmp_path)
        artifact = _artifact(tmp_path / "rv1.json")
        evidence = _validated(world, view, lifecycle, artifact)
        lifecycle.adopt_frontier()

        def stale(*_args, **_kwargs):
            raise StaleCoordinatorRevisionError(
                lifecycle.coordinator_id, 2, 3
            )

        monkeypatch.setattr(world.coord_store, "compare_and_swap", stale)
        with pytest.raises(ReconciliationError, match="CAS"):
            lifecycle.adopt_completed_review(evidence)
        state = world.coord_store.get(lifecycle.coordinator_id)
        assert state is not None
        assert state.reconciled_completions == {}
    finally:
        world.close()
