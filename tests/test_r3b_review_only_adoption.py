"""R3B accepted-frontier review-only adoption seam."""

from __future__ import annotations

import base64
import dataclasses
import json
import zlib
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority import PlanAuthorityReadAdapter, PlanAuthoritySnapshot
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.composition import task_main_host_bootstrap
from aota_forge.runtime.task_main.coordinator import MilestonePlanView, PlanDriftError, TaskMainCoordinatorError
from aota_forge.runtime.task_main.review_adoption import (
    AcceptedFrontierAuthorization,
    ReviewAdoptionError,
    TrustedGitFrontier,
    mint_accepted_frontier_authorization,
    trusted_plan_authority_from_binding,
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
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
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


class _AuthorityReader(PlanAuthorityReadAdapter):
    def __init__(self, binding: PlanAuthorityBinding) -> None:
        self.binding = binding

    def load(self) -> PlanAuthoritySnapshot:
        return PlanAuthoritySnapshot(
            body="authoritative test Plan",
            revision=PLAN_SOURCE_REVISION,
            digest=PLAN_DIGEST,
        )


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
    authority_binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=PLAN_AUTHORITY,
        source_revision=PLAN_SOURCE_REVISION,
        source_digest=PLAN_DIGEST,
    )
    return mint_accepted_frontier_authorization(
        state=state,
        live_plan_view=view,
        plan_id=PLAN_ID,
        trusted_frontier=TrustedGitFrontier(
            worktree_root=str(root.resolve()),
            entry_base=ENTRY_BASE,
            frontier_ref=FRONTIER,
            tree_ref=TREE,
            clean=True,
        ),
        trusted_authority=trusted_plan_authority_from_binding(
            project_id=PROJECT_ID,
            binding=authority_binding,
            authoritative_reader=_AuthorityReader(authority_binding),
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
        project_ref=SemanticReference(ref=PROJECT_ID),
        plan_ref=SemanticReference(ref=PLAN_AUTHORITY, digest=PLAN_DIGEST),
        work_item_ref=SemanticReference(ref="RV1"),
        milestone_ref=SemanticReference(ref=MILESTONE_ID),
    )


def _reviewer_sandbox(root: Path) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="test-workspace",
        workspace_root=str(root.resolve()),
        project_id=PROJECT_ID,
        project_root=str(root.resolve()),
        worktree_id="r3b-test-worktree",
        worktree_root=str(root.resolve()),
        registry_fingerprint="1" * 64,
        candidate_fingerprint="2" * 64,
    )


def _durable_reviewer_handoff(root: Path) -> dict[str, object]:
    handoff = _reviewer_handoff()
    ref = handoff_write(
        mode="work_item",
        semantic={
            "work_role": handoff.work_role.value,
            "task_kind": handoff.task_kind,
            "objective": handoff.objective,
            "bounded_scope": handoff.bounded_scope,
            "validation_expectations": list(handoff.validation_expectations),
            "semantic_stop_expectations": list(handoff.semantic_stop_expectations),
            "milestone_ref": MILESTONE_ID,
            "work_item_ref": "RV1",
            "project_ref": PROJECT_ID,
        },
        caller_role="task-main",
        sandbox=_reviewer_sandbox(root),
        plan_ref=PLAN_AUTHORITY,
        milestone_id=MILESTONE_ID,
        work_item_id="RV1",
        target_role="reviewer",
        task_id=REVIEWER_TASK_ID,
        provenance={"plan_digest": PLAN_DIGEST, "frontier_ref": FRONTIER},
    )
    return {
        "handoff": handoff,
        "handoff_ref": ref.ref,
        "handoff_digest": ref.digest,
        "canonical_task_id": REVIEWER_TASK_ID,
        "frontier_ref": FRONTIER,
        "handoff_sandbox": _reviewer_sandbox(root),
    }


def _clean_observation(*, dirty: bool = False, head: str = FRONTIER) -> dict[str, object]:
    return {
        "available": True,
        "is_dirty": dirty,
        "head_sha": head,
        "ignored": {"paths": [], "truncated": False},
    }


def _legacy_provenance_reason(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    encoded = review_adoption_module.canonical_json(
        review_adoption_module.canonicalize(
            {
                "version": 1,
                "payload": review_adoption_module._authorization_provenance_payload(
                    authorization, frontier
                ),
            },
            path="AcceptedFrontierAuthorizationRecord",
        )
    ).encode("utf-8")
    return "authprov1:" + base64.urlsafe_b64encode(zlib.compress(encoded, level=9)).decode("ascii")


def _patch_git_observation(monkeypatch: pytest.MonkeyPatch, *, dirty: bool = False, head: str = FRONTIER) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None, **_kwargs: _clean_observation(dirty=dirty, head=head),
    )


def test_authprov2_real_production_shape_fits_the_512_byte_bound() -> None:
    authority = local_plan_authority_reference(
        "aota_forge", "plan_001_gov2_session_first_adoption"
    )
    authorization = AcceptedFrontierAuthorization(
        project_id="aota_forge",
        plan_id="plan_001_gov2_session_first_adoption",
        plan_authority=authority,
        authority_source_kind="local_governance",
        plan_digest="cabf11045bee778aac5fa7ff71138f1d2496ec8bff9c94f3c93190ea8ec3795d",
        plan_source_revision="cabf11045bee778aac5fa7ff71138f1d2496ec8bff9c94f3c93190ea8ec3795d",
        milestone_id="M1",
        entry_base="db7932a5445b0bc37d8fe7d1271ea3031ff4ef6e",
        candidate_frontier_ref="a3f5d68b07e0dece621a8cffd3de4619e09d1bf2",
        authorization_ref=(
            "review-authority://local_governance/aota_forge/"
            "plan_001_gov2_session_first_adoption/M1"
        ),
        reviewer_canonical_task_id="aota_forge:M1:RV1:attempt-1",
    )
    frontier = TrustedGitFrontier(
        worktree_root=str(Path(__file__).resolve().parents[1]),
        entry_base=authorization.entry_base,
        frontier_ref=authorization.candidate_frontier_ref,
        tree_ref="b" * 40,
        clean=True,
    )

    reason = review_adoption_module._authorization_provenance_reason(authorization, frontier)

    assert len(reason) <= 512
    assert reason.startswith("authprov2:")


def test_authprov2_is_compact_canonical_and_round_trips_from_durable_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-authprov2-shape")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW
        state = world.coord_store.get(runner.coordinator_id)
        assert state is not None and state.review_gates is not None

        reason = state.review_gates["reason"]
        assert reason.startswith("authprov2:")
        assert len(reason) <= 512
        residual = json.loads(reason.split(":", 1)[1])
        assert set(residual) == {"git", "version"}
        assert set(residual["git"]) == {"clean", "tree_ref", "worktree_root"}

        restored_auth, restored_frontier = review_adoption_module._authorization_provenance_from_reason(
            reason, state=state
        )
        assert restored_auth == auth
        assert restored_frontier.canonical_dict() == {
            "clean": True,
            "entry_base": ENTRY_BASE,
            "frontier_ref": FRONTIER,
            "tree_ref": TREE,
            "worktree_root": str(tmp_path.resolve()),
        }
    finally:
        world.close()


def test_authprov1_remains_strict_read_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-authprov1-read")
    try:
        auth = _authorization(world, tmp_path)
        frontier = TrustedGitFrontier(
            worktree_root=str(tmp_path.resolve()),
            entry_base=ENTRY_BASE,
            frontier_ref=FRONTIER,
            tree_ref=TREE,
            clean=True,
        )
        reason = _legacy_provenance_reason(auth, frontier)
        restored_auth, restored_frontier = review_adoption_module._authorization_provenance_from_reason(reason)
        assert restored_auth == auth
        assert restored_frontier == frontier
    finally:
        world.close()


def test_authprov2_oversized_residual_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-authprov2-oversized")
    try:
        auth = _authorization(world, tmp_path)
        frontier = TrustedGitFrontier(
            worktree_root="x" * 512,
            entry_base=ENTRY_BASE,
            frontier_ref=FRONTIER,
            tree_ref=TREE,
            clean=True,
        )
        with pytest.raises(ReviewAdoptionError, match="coordinator bound"):
            review_adoption_module._authorization_provenance_reason(auth, frontier)
    finally:
        world.close()


def test_authprov2_malformed_residual_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-authprov2-malformed")
    try:
        view = _view()
        auth = _authorization(world, tmp_path)
        runner = _runner(world, view, auth, tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW
        state = world.coord_store.get(runner.coordinator_id)
        assert state is not None and state.review_gates is not None
        with pytest.raises(ReviewAdoptionError, match="cannot be decoded"):
            review_adoption_module._authorization_provenance_from_reason(
                "authprov2:{not-json}", state=state
            )
    finally:
        world.close()


def _runner(
    world: World,
    view: MilestonePlanView,
    auth: AcceptedFrontierAuthorization | None,
    root: Path,
    *,
    reviewer_handoff_resolver=_reviewer_handoff,
    activate: bool = True,
) -> TaskMainMilestoneRunner:
    durable_resolver = (
        (lambda: _durable_reviewer_handoff(root))
        if reviewer_handoff_resolver is not None
        else None
    )
    coordinator_id = world.activate(view).coordinator_id if activate else f"{PROJECT_ID}:{MILESTONE_ID}"
    authority_binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind="local_governance",
        authority_ref=PLAN_AUTHORITY,
        source_revision=PLAN_SOURCE_REVISION,
        source_digest=PLAN_DIGEST,
    )
    return TaskMainMilestoneRunner(
        coordinator_store=world.coord_store,
        execution_store=world.exec_store,
        execution_dispatcher=world.dispatcher,
        live_plan_view=view,
        handoff_resolver=lambda work_item_id: (_ for _ in ()).throw(
            AssertionError(f"W handoff resolver must not run: {work_item_id}")
        ),
        reviewer_handoff_resolver=reviewer_handoff_resolver,
        reviewer_dispatch_resolver=durable_resolver,
        coordinator_id=coordinator_id,
        accepted_frontier_authorization=auth,
        review_adoption_worktree_root=_reviewer_sandbox(root),
        review_adoption_git=FakeGit(),
        trusted_plan_authority=trusted_plan_authority_from_binding(
            project_id=PROJECT_ID,
            binding=authority_binding,
            authoritative_reader=_AuthorityReader(authority_binding),
        ),
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


def test_tampered_authorization_rehashed_by_caller_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-rehashed-authorization")
    try:
        view = _view()
        valid = _authorization(world, tmp_path)
        frontier = TrustedGitFrontier(
            worktree_root=str(tmp_path.resolve()),
            entry_base=ENTRY_BASE,
            frontier_ref=FRONTIER,
            tree_ref=TREE,
            clean=True,
        )
        tampered = dataclasses.replace(
            valid,
            authorization_ref="attacker://self-issued",
            provenance_digest="",
        )
        tampered = dataclasses.replace(
            tampered,
            provenance_digest=review_adoption_module._authorization_provenance_digest(
                tampered, frontier
            ),
        )

        with pytest.raises(ReviewAdoptionError, match="derived from the trusted authority"):
            _runner(world, view, tampered, tmp_path).advance_once()
        assert world.exec_store.list_all() == []
    finally:
        world.close()


def test_durable_tamper_and_rehash_is_rejected_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-durable-rehashed-authorization")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW
        coordinator_id = runner.coordinator_id
        original_state = world.coord_store.get(coordinator_id)
        assert original_state is not None
        original_auth, original_frontier = review_adoption_module._authorization_provenance_from_reason(
            original_state.review_gates["reason"], state=original_state
        )
        tampered = dataclasses.replace(
            original_auth,
            authorization_ref="attacker://self-issued",
            provenance_digest="",
        )
        tampered = dataclasses.replace(
            tampered,
            provenance_digest=review_adoption_module._authorization_provenance_digest(
                tampered, original_frontier
            ),
        )
        persisted = json.loads(world.coord_path.read_text(encoding="utf-8"))
        record = persisted[coordinator_id]
        proof = review_adoption_module._proof_digest(tampered, original_frontier)
        record["review_gates"]["ref"] = tampered.authorization_ref
        record["review_gates"]["digest"] = proof
        record["review_gates"]["reason"] = review_adoption_module._authorization_provenance_reason(
            tampered, original_frontier
        )
        record["integrated_review"]["digest"] = proof
        record["frontier_ref"]["digest"] = proof
        persisted[coordinator_id] = record
        world.close()
        world.coord_path.write_text(json.dumps(persisted), encoding="utf-8")

        restarted = World(tmp_path, name="r3b-durable-rehashed-authorization")
        try:
            with pytest.raises(ReviewAdoptionError, match="derived from the trusted authority"):
                _runner(restarted, view, None, tmp_path, activate=False).advance_once()
            assert len(restarted.exec_store.list_all()) == 1
        finally:
            restarted.close()
    finally:
        try:
            world.close()
        except Exception:
            pass


def test_reviewer_handoff_materialization_reuses_identity_after_pre_binding_crash(
    tmp_path: Path,
) -> None:
    world = World(tmp_path, name="r3b-handoff-materialization-reuse")
    try:
        view = _view()
        handle = world.activate(view)
        state = world.coord_store.get(handle.coordinator_id)
        assert state is not None
        sandbox = _reviewer_sandbox(tmp_path)
        first = task_main_host_bootstrap._materialize_reviewer_work_handoff(
            base_handoff=_reviewer_handoff(),
            sandbox=sandbox,
            live_view=view,
            state=state,
            project_id=PROJECT_ID,
            reviewer_task_id=REVIEWER_TASK_ID,
            plan_id=PLAN_ID,
            parent_task_identity="session-1",
        )
        second = task_main_host_bootstrap._materialize_reviewer_work_handoff(
            base_handoff=_reviewer_handoff(),
            sandbox=sandbox,
            live_view=view,
            state=state,
            project_id=PROJECT_ID,
            reviewer_task_id=REVIEWER_TASK_ID,
            plan_id=PLAN_ID,
            parent_task_identity="session-1",
        )

        assert second["handoff_ref"] == first["handoff_ref"]
        assert second["handoff_digest"] == first["handoff_digest"]
        assert len(list((tmp_path / ".aota" / "handoffs").glob("*.json"))) == 1
    finally:
        world.close()


def test_untracked_runtime_database_is_excluded_but_source_changes_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None, **_kwargs: {
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
            "ignored": {"paths": [], "truncated": False},
        },
    )
    world = World(tmp_path, name="r3b-runtime-db")
    try:
        view = _view()
        outcome = _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        assert outcome.disposition == DISPOSITION_DISPATCHED_REVIEW
    finally:
        world.close()


@pytest.mark.parametrize(
    "runtime_path",
    (
        ".aota/governance-journal.json",
        ".aota/coordinator.json",
        ".aota/execution.json",
        ".aota/task-main-bootstrap.json",
        ".aota/task-main-thin-bootstrap.json",
        ".aota/observation-evidence.jsonl",
        ".aota/observation-evidence-w4A.jsonl",
        ".aota/handoffs/",
        ".aota/durable_payloads/",
        ".aota/pre-resolved-bindings/",
        ".aota/task_return_receipts/",
        ".aota/handoffs/" + "a" * 64 + ".json",
        ".aota/durable_payloads/" + "b" * 64 + ".bin",
        ".aota/pre-resolved-bindings/worker-r3b-review-deadbeef.json",
        ".aota/pre-resolved-bindings/task-main-12345678-abcdef12.json",
        ".aota/task_return_receipts/" + "c" * 64 + ".json",
    ),
)
def test_exact_runtime_owner_paths_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_path: str
) -> None:
    _patch_git_observation(monkeypatch)
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None, **_kwargs: {
            "available": True,
            "is_dirty": True,
            "head_sha": FRONTIER,
            "staged": {"paths": [], "truncated": False},
            "unstaged": {"paths": [], "truncated": False},
            "untracked": {"paths": [runtime_path], "truncated": False},
            "ignored": {"paths": [], "truncated": False},
        },
    )
    world = World(tmp_path, name=f"r3b-runtime-{runtime_path.rsplit('/', 1)[-1]}")
    try:
        view = _view()
        outcome = _runner(world, view, _authorization(world, tmp_path), tmp_path).advance_once()
        assert outcome.disposition == DISPOSITION_DISPATCHED_REVIEW
    finally:
        world.close()


def test_unknown_aota_path_is_source_dirty_and_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None, **_kwargs: {
            "available": True,
            "is_dirty": True,
            "head_sha": FRONTIER,
            "staged": {"paths": [], "truncated": False},
            "unstaged": {"paths": [], "truncated": False},
            "untracked": {"paths": [".aota/arbitrary.json"], "truncated": False},
            "ignored": {"paths": [], "truncated": False},
        },
    )
    world = World(tmp_path, name="r3b-unknown-runtime")
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


def test_truncated_git_status_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        review_adoption_module,
        "inspect_git",
        lambda _root, boundary=None, **_kwargs: {
            "available": True,
            "is_dirty": True,
            "head_sha": FRONTIER,
            "staged": {"paths": [], "truncated": False},
            "unstaged": {"paths": [], "truncated": False},
            "untracked": {
                "paths": [".aota/governance.sqlite3"],
                "truncated": True,
            },
            "ignored": {"paths": [], "truncated": False},
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
        recovered = _runner(restarted, view, None, tmp_path)
        outcome = recovered.advance_once()

        assert outcome.disposition == DISPOSITION_WAITING_FOR_WORKERS
        assert restarted.exec_store.get("aota_forge:M1:W1:attempt-1") is None
        assert len(restarted.exec_store.list_all()) == 1
    finally:
        if restarted is not None:
            restarted.close()
        else:
            world.close()


@pytest.mark.parametrize(
    "tampered_field",
    ("authorization_ref", "plan_digest", "proof_digest", "frontier_ref"),
)
def test_restart_rejects_tampered_durable_review_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name=f"r3b-tamper-{tampered_field}")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        assert runner.advance_once().disposition == DISPOSITION_DISPATCHED_REVIEW
        coordinator_id = runner.coordinator_id
        world.close()

        persisted = json.loads(world.coord_path.read_text(encoding="utf-8"))
        record = persisted[coordinator_id]
        if tampered_field == "authorization_ref":
            record["review_gates"]["ref"] = "tampered-authorization-ref"
        elif tampered_field == "plan_digest":
            record["plan_digest"] = "e" * 64
        elif tampered_field == "proof_digest":
            record["review_gates"]["digest"] = "0" * 64
        else:
            record["frontier_ref"]["ref"] = "f" * 40
        world.coord_path.write_text(json.dumps(persisted), encoding="utf-8")

        restarted = World(tmp_path, name=f"r3b-tamper-{tampered_field}")
        try:
            recovered = _runner(restarted, view, None, tmp_path, activate=False)
            with pytest.raises((ReviewAdoptionError, PlanDriftError)):
                recovered.advance_once()
            assert len(restarted.exec_store.list_all()) == 1
        finally:
            restarted.close()
    finally:
        if world.coord_store is not None:
            try:
                world.close()
            except Exception:
                pass


def test_reviewer_handoff_only_resolver_cannot_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-no-in-memory-review")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        runner._reviewer_dispatch_resolver = None
        with pytest.raises(TaskMainCoordinatorError, match="reviewer_handoff_resolver-only"):
            runner.advance_once()
        assert world.exec_store.list_all() == []
    finally:
        world.close()


@pytest.mark.parametrize("missing_field", ("handoff_ref", "handoff_digest"))
def test_reviewer_durable_binding_requires_ref_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_field: str
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name=f"r3b-missing-{missing_field}")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        record = _durable_reviewer_handoff(tmp_path)
        record.pop(missing_field)
        runner._reviewer_dispatch_resolver = lambda: record
        with pytest.raises(TaskMainCoordinatorError, match="durable handoff"):
            runner.advance_once()
        assert world.exec_store.list_all() == []
    finally:
        world.close()


def test_reviewer_durable_binding_requires_adopted_frontier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_git_observation(monkeypatch)
    world = World(tmp_path, name="r3b-wrong-review-frontier")
    try:
        view = _view()
        runner = _runner(world, view, _authorization(world, tmp_path), tmp_path)
        record = _durable_reviewer_handoff(tmp_path)
        record["frontier_ref"] = "f" * 40
        runner._reviewer_dispatch_resolver = lambda: record
        with pytest.raises(TaskMainCoordinatorError, match="frontier"):
            runner.advance_once()
        assert world.exec_store.list_all() == []
    finally:
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
