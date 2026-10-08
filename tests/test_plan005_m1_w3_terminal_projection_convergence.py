"""Plan #005 M1/W3 terminal lifecycle and generated projection convergence."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
    local_plan_authority_reference,
)
from aota_forge.composition.stewardship import StewardshipExecutionState
from aota_forge.composition.task_main import (
    _default_projection_refresh_hook,
    create_task_main_control_service,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.governance.context_route import ROUTE_ENTRY_PLAN_AUTHORITY, ROUTE_ENTRY_PLAN_CARD
from aota_forge.governance.local_lifecycle import (
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecycleRequest,
)
from aota_forge.governance.projection_lifecycle import ProjectionLifecycleError
from aota_forge.governance.stewardship import GovernanceCheckpointKind
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_RETIRED,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.runtime.task_main.control import (
    AF_TASK_MAIN_ROLE,
    TaskMainControlService,
    TaskMainProjectionConvergenceError,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.review_adoption import trusted_plan_authority_from_binding
import aota_forge.runtime.task_main.runner as runner_module
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_BLOCKED,
    DISPOSITION_MILESTONE_CLOSURE_READY,
    RunnerOutcome,
)
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.steward_finalizer import (
    ClosurePhase,
    TrustedPlanIdentity,
    TrustedUserGateState,
)

from tests.test_af57_m3_w2_governance_intake import (
    MANIFEST as INTAKE_MANIFEST,
    PLAN_BODY as INTAKE_PLAN_BODY,
    PLAN_ID as INTAKE_PLAN_ID,
    PROJECT_ID as INTAKE_PROJECT_ID,
)
from tests.test_af57_m3_w3_steward_production_composition import (
    LOCAL_PLAN_BODY,
    MILESTONE,
    PLAN_ID,
    PROJECT_ID,
    _checkpoint,
    _executor,
    _sandbox,
)

PLAN002_ID = "plan_002_session_first_context_activation"
PLAN004_ID = "plan_004_trusted_opencode_session_bridge_context_isolation"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _terminal_journal_entry_count(project_root: Path) -> int:
    journal = FileBackedDurableJournalStore(project_root / "governance-journal.json")
    try:
        return len(journal.list_all())
    finally:
        journal.close()


def _request(
    root: LocalGovernanceRootBinding,
    plan_id: str,
    label: str,
    *,
    expected_revision: int | None = None,
) -> LocalPlanLifecycleRequest:
    destination = LocalPlanAuthorityDestination(
        root,
        plan_id,
        object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)),
    )
    return LocalPlanLifecycleRequest(
        project_id=PROJECT_ID,
        plan_id=plan_id,
        destination=destination,
        principal="plan005-w3-test",
        authorization_reference=f"{label}-authorization",
        lease_reference=f"{label}-lease",
        idempotency_key=f"{label}-idempotency",
        correlation_id=f"{label}-correlation",
        intent_fingerprint=_digest(label),
        expected_plan_revision=expected_revision,
    )


def _world(tmp_path: Path) -> dict[str, object]:
    (tmp_path / ".aota").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(
        INTAKE_MANIFEST.replace(INTAKE_PROJECT_ID, PROJECT_ID), encoding="utf-8"
    )
    governance_base = tmp_path / "plans"
    (governance_base / PROJECT_ID).mkdir(parents=True)
    governance_root = LocalGovernanceRootBinding.from_trusted_base(
        governance_base, project_id=PROJECT_ID
    )
    store = SQLiteProjectGovernanceStore(tmp_path / ".aota" / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / ".aota" / "governance-journal.json")
    lifecycle = LocalPlanLifecycleCoordinator(store, journal)
    for plan_id in (PLAN_ID, PLAN002_ID):
        body = LOCAL_PLAN_BODY.replace(PLAN_ID, plan_id)
        initialized = lifecycle.initialize(
            _request(governance_root, plan_id, f"{plan_id}-init"), body
        )
        assert initialized.ok

    historical_body = (
        LOCAL_PLAN_BODY.replace(PLAN_ID, PLAN004_ID)
        .replace("PLAN_REVISION=R1", "PLAN_REVISION=R2")
        .replace("PLAN_STATUS=active", "PLAN_STATUS=completed")
        .replace("CURRENT_STATUS=m3_user_acceptance_ready", "CURRENT_STATUS=completed")
        .replace("M3_STATUS=in_progress", "M3_STATUS=completed")
        .replace("M3_ACCEPTED=no", "M3_ACCEPTED=yes")
    )
    historical_directory = Path(governance_root.root_path) / PLAN004_ID
    historical_directory.mkdir()
    (historical_directory / "plan.md").write_text(historical_body, encoding="utf-8")
    historical_digest = _digest(historical_body)
    store.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN004_ID,
            lifecycle_state=PLAN_LIFECYCLE_RETIRED,
            authority=PlanAuthorityBinding(
                plan_id=PLAN004_ID,
                source_kind="local_governance",
                authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN004_ID),
                source_revision=historical_digest,
                source_digest=historical_digest,
            ),
            revision=1,
        )
    )
    journal.close()
    return {
        "project_root": tmp_path,
        "governance_root": governance_root,
        "governance_root_path": Path(governance_root.root_path),
        "store": store,
    }


def _checkpoint_for_local_plan(
    *, kind: str, accepted: bool, authority: PlanAuthorityBinding
) -> tuple[object, PlanAuthorityBinding]:
    trusted_plan = TrustedPlanIdentity(
        governing_repo=None,
        plan_issue_number=None,
        milestone_ref=MILESTONE,
        plan_ref=authority.authority_ref,
        managed_comments={},
        authority_binding=authority,
    )
    base = _checkpoint(semantic=False, checkpoint_id=f"plan005-w3-{kind.lower()}")
    checkpoint = replace(
        base,
        kind=type(base.kind)(kind),
        trusted_plan=trusted_plan,
        closure_phase=ClosurePhase.ACCEPTED_CLOSURE if accepted else ClosurePhase.REVIEWED_CLOSURE,
        user_gate=TrustedUserGateState(user_approval_satisfied=accepted),
        plan_id=PLAN_ID,
        all_milestones_closed=(True if kind == "PLAN_CLOSE" else None),
    )
    return checkpoint, authority


def _control_service(
    world: dict[str, object],
    *,
    checkpoint_kind: str = "PLAN_CLOSE",
    accepted: bool = True,
    projection_hook=None,
):
    root = world["governance_root"]
    store = world["store"]
    assert isinstance(root, LocalGovernanceRootBinding)
    assert isinstance(store, SQLiteProjectGovernanceStore)
    record = store.get_plan(PROJECT_ID, PLAN_ID)
    assert record is not None
    destination = LocalPlanAuthorityDestination(
        root,
        PLAN_ID,
        object_ref_subject(make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)),
    )
    reader = LocalPlanAuthorityReadAdapter(destination, binding=record.authority)
    trusted_review_authority = trusted_plan_authority_from_binding(
        project_id=PROJECT_ID,
        binding=record.authority,
        authoritative_reader=reader,
    )
    checkpoint, trusted_binding = _checkpoint_for_local_plan(
        kind=checkpoint_kind, accepted=accepted, authority=record.authority
    )

    coordinator_store = FileBackedTaskMainCoordinatorStore(
        Path(world["project_root"]) / "coordinator.json"
    )
    live_view = MilestonePlanView(
        plan_authority=trusted_binding.authority_ref,
        plan_digest="d" * 64,
        milestone_id=MILESTONE,
        entry_base="b" * 40,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE, work_items=["W1"], dependencies=[]
        ),
        milestone_user_approval_satisfied=True,
    )
    coordinator_store.create(
        TaskMainCoordinatorState(
            coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
            plan_authority=trusted_binding.authority_ref,
            plan_digest="d" * 64,
            milestone_id=MILESTONE,
            entry_base="b" * 40,
            origin_task_main_session_ref="session:plan005-w3",
            project_id=PROJECT_ID,
            executor_id="test-executor",
            user_approval_satisfied=True,
            work_items=("W1",),
            wi_status={"W1": "PENDING"},
            bindings={},
        )
    )
    execution_store = FileBackedExecutionStateStore(
        Path(world["project_root"]) / "executions.json"
    )
    dispatcher = ExecutionDispatcher(
        ExecutorRegistry(),
        state_store=execution_store,
        origin_session_ref="session:plan005-w3",
    )
    sandbox = _sandbox(Path(world["project_root"]))
    probe, _, _, _ = _executor(
        Path(world["project_root"]),
        lambda _handoff: None,
        governance_store=store,
        coordinator_store=coordinator_store,
        live_plan_view=live_view,
        result_sandbox=sandbox,
    )
    if projection_hook is None:
        projection_hook = _default_projection_refresh_hook(
            coordinator_store=coordinator_store,
            execution_store=execution_store,
            governance_store=store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=root,
            authority_binding=trusted_binding,
            required=True,
        )

    service = create_task_main_control_service(
        coordinator_store=coordinator_store,
        execution_store=execution_store,
        execution_dispatcher=dispatcher,
        stewardship_checkpoint=checkpoint,
        stewardship_dispatch=lambda _handoff: None,
        stewardship_sandbox=sandbox,
        stewardship_finalizer=probe.finalizer,
        governance_store=store,
        stewardship_local_plan_close_request_factory=lambda current_checkpoint, current: _request(
            root,
            current_checkpoint.plan_id,
            "plan005-w3-accepted-close",
            expected_revision=current.revision,
        ),
        projection_refresh_hook=projection_hook,
        projection_project_id=PROJECT_ID,
        projection_plan_id=PLAN_ID,
        projection_project_root=world["project_root"],
        projection_governance_root=root,
        projection_authority_binding=trusted_binding,
        trusted_plan_authority=trusted_review_authority,
    )
    return service, coordinator_store, execution_store, live_view


def _invoke(service, live_view: MilestonePlanView):
    return service.advance_once(
        profile=AF_TASK_MAIN_ROLE,
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        live_plan_view=live_view,
        handoff_resolver=lambda _ref: pytest.fail("terminal checkpoint must not dispatch a Worker"),
    )


def _closure_ready(self, *, session_available=True):
    return RunnerOutcome(
        disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
        coordinator_id=self.coordinator_id,
        coordinator_revision=2,
        milestone_closure_ready=True,
    )


def test_steward_plan_close_projects_terminal_truth_and_retired_reentry_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    root_path = world["governance_root_path"]
    assert isinstance(root_path, Path)
    (root_path / "MAP.md").write_text("stale projection\n", encoding="utf-8")
    (root_path / "STATUS.md").write_text("stale projection\n", encoding="utf-8")
    before_plan002 = world["store"].get_plan(PROJECT_ID, PLAN002_ID)
    before_plan004 = world["store"].get_plan(PROJECT_ID, PLAN004_ID)
    assert before_plan002 is not None and before_plan004 is not None
    refresh_results = []
    service_box: dict[str, object] = {}

    def refresh_projection():
        result = _default_projection_refresh_hook(
            coordinator_store=service_box["coordinator_store"],
            execution_store=service_box["execution_store"],
            governance_store=world["store"],
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=world["governance_root"],
            authority_binding=service_box["authority_binding"],
            required=True,
        )()
        refresh_results.append(result)
        return result

    monkeypatch.setattr(runner_module.TaskMainMilestoneRunner, "advance_once", _closure_ready)
    initial_service, coordinator, execution, live_view = _control_service(
        world, projection_hook=lambda: refresh_projection()
    )
    service_box["coordinator_store"] = coordinator
    service_box["execution_store"] = execution
    service_box["authority_binding"] = world["store"].get_plan(PROJECT_ID, PLAN_ID).authority
    first = _invoke(initial_service, live_view)
    assert first.milestone_closure_ready is True
    assert len(refresh_results) == 1
    result = refresh_results[-1]
    assert result.request.trigger == "plan_mutation"
    assert result.receipt.project_id == PROJECT_ID
    assert result.map_path.parent == root_path and result.status_path.parent == root_path
    assert result.map_path.read_text(encoding="utf-8") == result.views.map_markdown
    assert result.status_path.read_text(encoding="utf-8") == result.views.status_markdown
    assert "stale projection" not in result.views.map_markdown
    assert "stale projection" not in result.views.status_markdown

    current = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert current is not None and current.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    terminal_reader = LocalPlanAuthorityReadAdapter(
        LocalPlanAuthorityDestination(
            world["governance_root"],
            PLAN_ID,
            object_ref_subject(make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)),
        ),
        binding=current.authority,
    )
    terminal_snapshot = terminal_reader.load()
    terminal_document = load_local_portable_plan(terminal_reader)
    assert terminal_snapshot.digest == current.authority.source_digest
    assert terminal_snapshot.revision == current.authority.source_revision
    assert terminal_document.plan_status == "completed"
    plan_card = next(card for card in result.bundle.plans if card.plan_id == PLAN_ID)
    assert plan_card.lifecycle_state == "retired"
    assert plan_card.plan_status == "completed"
    assert plan_card.authority_digest == current.authority.source_digest
    assert plan_card.authority_revision == str(current.authority.source_revision)
    assert result.context_route.entries_for_kind(ROUTE_ENTRY_PLAN_CARD)
    assert result.context_route.entries_for_kind(ROUTE_ENTRY_PLAN_AUTHORITY)
    assert f"{PLAN_ID}" in result.map_path.read_text(encoding="utf-8")
    assert f"plan_lifecycle: {PLAN_ID}=retired" in result.status_path.read_text(encoding="utf-8")

    unchanged_plan002 = world["store"].get_plan(PROJECT_ID, PLAN002_ID)
    historical_plan004 = world["store"].get_plan(PROJECT_ID, PLAN004_ID)
    assert unchanged_plan002 == before_plan002
    assert historical_plan004 == before_plan004
    assert unchanged_plan002.lifecycle_state == "active"
    assert historical_plan004.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    historical_reader = LocalPlanAuthorityReadAdapter(
        LocalPlanAuthorityDestination(
            world["governance_root"],
            PLAN004_ID,
            object_ref_subject(make_id(IdKind.SUBJECT, PLAN004_ID, sub_kind=SubjectKind.PLAN)),
        ),
        binding=historical_plan004.authority,
    )
    assert load_local_portable_plan(historical_reader).plan_status == "completed"
    assert result.bundle.progress.active_coordinator_count == 1
    assert plan_card.lifecycle_state == "retired"

    completed_revision = current.revision
    completed_digest = current.authority.source_digest
    journal_entries = _terminal_journal_entry_count(tmp_path)
    (root_path / "MAP.md").write_text("stale after retirement\n", encoding="utf-8")
    (root_path / "STATUS.md").write_text("stale after retirement\n", encoding="utf-8")
    replay = _invoke(initial_service, live_view)
    assert replay.milestone_closure_ready is True
    recovered = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert recovered is not None
    assert recovered.revision == completed_revision
    assert recovered.authority.source_digest == completed_digest
    assert _terminal_journal_entry_count(tmp_path) == journal_entries
    assert len(refresh_results) == 2
    assert "stale after retirement" not in (root_path / "MAP.md").read_text(encoding="utf-8")
    assert "stale after retirement" not in (root_path / "STATUS.md").read_text(encoding="utf-8")


def test_projection_failure_is_visible_and_reentry_recovers_without_reclosing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    root_path = world["governance_root_path"]
    assert isinstance(root_path, Path)
    (root_path / "MAP.md").mkdir()
    (root_path / "STATUS.md").write_text("stale status\n", encoding="utf-8")
    refresh_results = []
    service_box: dict[str, object] = {}

    def refresh_projection():
        result = _default_projection_refresh_hook(
            coordinator_store=service_box["coordinator_store"],
            execution_store=service_box["execution_store"],
            governance_store=world["store"],
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=world["governance_root"],
            authority_binding=service_box["authority_binding"],
            required=True,
        )()
        refresh_results.append(result)
        return result

    monkeypatch.setattr(runner_module.TaskMainMilestoneRunner, "advance_once", _closure_ready)
    service, coordinator, execution, live_view = _control_service(
        world, projection_hook=lambda: refresh_projection()
    )
    service_box["coordinator_store"] = coordinator
    service_box["execution_store"] = execution
    service_box["authority_binding"] = world["store"].get_plan(PROJECT_ID, PLAN_ID).authority

    with pytest.raises(TaskMainProjectionConvergenceError) as error:
        _invoke(service, live_view)
    assert error.value.authority_mutation == "VERIFIED"
    assert error.value.projection_convergence == "BLOCKED"
    assert "AUTHORITY_MUTATION=VERIFIED" in str(error.value)
    assert "PROJECTION_CONVERGENCE=BLOCKED" in str(error.value)
    retired = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert retired is not None and retired.lifecycle_state == PLAN_LIFECYCLE_RETIRED
    terminal_revision = retired.revision
    terminal_digest = retired.authority.source_digest
    journal_entries = _terminal_journal_entry_count(tmp_path)
    assert refresh_results == []

    (root_path / "MAP.md").rmdir()
    recovered_outcome = _invoke(service, live_view)
    assert recovered_outcome.milestone_closure_ready is True
    recovered = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert recovered is not None
    assert recovered.revision == terminal_revision
    assert recovered.authority.source_digest == terminal_digest
    assert _terminal_journal_entry_count(tmp_path) == journal_entries
    assert len(refresh_results) == 1
    result = refresh_results[0]
    assert result.map_path.read_text(encoding="utf-8") == result.views.map_markdown
    assert result.status_path.read_text(encoding="utf-8") == result.views.status_markdown
    assert "plan_lifecycle: " + PLAN_ID + "=retired" in result.views.status_markdown


def test_unknown_local_plan_close_outcome_does_not_attempt_projection(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    service, _coordinator, _execution, live_view = _control_service(world)
    assert isinstance(service, TaskMainControlService)
    refresh_calls = []

    class UnknownPlanCloseRunner:
        stewardship_outcome = SimpleNamespace(
            checkpoint_outcome=SimpleNamespace(kind=GovernanceCheckpointKind.PLAN_CLOSE),
            execution_state=StewardshipExecutionState.FAILED_CLOSED,
            error_code="LOCAL_PLAN_CLOSE_OUTCOME_UNKNOWN",
        )
        coordinator_id = f"{PROJECT_ID}:{MILESTONE}"

        def advance_once(self, *, session_available=True):
            return RunnerOutcome(
                disposition=DISPOSITION_BLOCKED,
                coordinator_id=self.coordinator_id,
                coordinator_revision=2,
                milestone_closure_ready=False,
            )

    service._runner_factory = lambda **_kwargs: UnknownPlanCloseRunner()
    service._projection_refresh_hook = lambda: refresh_calls.append(True)
    with pytest.raises(TaskMainProjectionConvergenceError) as error:
        _invoke(service, live_view)
    assert error.value.authority_mutation == "UNKNOWN"
    assert error.value.projection_convergence == "NOT_ATTEMPTED"
    assert "AUTHORITY_MUTATION=UNKNOWN" in str(error.value)
    assert "PROJECTION_CONVERGENCE=NOT_ATTEMPTED" in str(error.value)
    assert refresh_calls == []


def test_milestone_acceptance_refreshes_working_progress_without_changing_plan_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    root_path = world["governance_root_path"]
    assert isinstance(root_path, Path)
    refresh_results = []
    service_box: dict[str, object] = {}

    def refresh_projection():
        result = _default_projection_refresh_hook(
            coordinator_store=service_box["coordinator_store"],
            execution_store=service_box["execution_store"],
            governance_store=world["store"],
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=world["governance_root"],
            authority_binding=service_box["authority_binding"],
            required=True,
        )()
        refresh_results.append(result)
        return result

    monkeypatch.setattr(runner_module.TaskMainMilestoneRunner, "advance_once", _closure_ready)
    service, coordinator, execution, live_view = _control_service(
        world,
        checkpoint_kind="MILESTONE_CLOSE",
        accepted=True,
        projection_hook=lambda: refresh_projection(),
    )
    service_box["coordinator_store"] = coordinator
    service_box["execution_store"] = execution
    service_box["authority_binding"] = world["store"].get_plan(PROJECT_ID, PLAN_ID).authority
    before_record = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert before_record is not None
    before_body = LocalPlanAuthorityReadAdapter(
        LocalPlanAuthorityDestination(
            world["governance_root"],
            PLAN_ID,
            object_ref_subject(make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)),
        ),
        binding=before_record.authority,
    ).load()

    outcome = _invoke(service, live_view)
    assert outcome.milestone_closure_ready is True
    assert len(refresh_results) == 1
    after_record = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert after_record == before_record
    after_snapshot = LocalPlanAuthorityReadAdapter(
        LocalPlanAuthorityDestination(
            world["governance_root"],
            PLAN_ID,
            object_ref_subject(make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)),
        ),
        binding=after_record.authority,
    ).load()
    assert after_snapshot == before_body
    assert refresh_results[0].request.trigger == "work_reconciliation"
    current_card = next(
        card for card in refresh_results[0].bundle.plans if card.plan_id == PLAN_ID
    )
    assert current_card.lifecycle_state == "active"
    assert current_card.plan_status == "active"
    coordinator_after = coordinator.get(f"{PROJECT_ID}:{MILESTONE}")
    assert coordinator_after.frontier_ref["status"] == "accepted"
    assert coordinator_after.open_blockers == ()
    assert "proceed to integrated review/next progression" in (
        root_path / "STATUS.md"
    ).read_text(encoding="utf-8")


def test_stale_foreign_or_missing_projection_authority_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    coordinator = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinator.json")
    execution = FileBackedExecutionStateStore(tmp_path / "executions.json")
    store = world["store"]
    root = world["governance_root"]
    record = store.get_plan(PROJECT_ID, PLAN_ID)
    assert record is not None
    hook = _default_projection_refresh_hook(
        coordinator_store=coordinator,
        execution_store=execution,
        governance_store=store,
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        project_root=world["project_root"],
        governance_root=root,
        authority_binding=record.authority,
        required=True,
    )
    plan_path = Path(root.root_path) / PLAN_ID / "plan.md"
    original = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(original + "\nexternal edit\n", encoding="utf-8")
    with pytest.raises(ProjectionLifecycleError) as stale_error:
        hook()
    assert stale_error.value.code == "STALE_AUTHORITY"
    assert not (Path(root.root_path) / "MAP.md").exists()
    assert not (Path(root.root_path) / "STATUS.md").exists()
    plan_path.write_text(original, encoding="utf-8")
    current = store.get_plan(PROJECT_ID, PLAN_ID)
    assert current is not None
    store.compare_and_swap_plan(
        PROJECT_ID,
        PLAN_ID,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    with pytest.raises(ProjectionLifecycleError) as terminal_error:
        hook()
    assert terminal_error.value.code == "TERMINAL_AUTHORITY_CONFLICT"

    foreign_base = tmp_path / "foreign-plans"
    (foreign_base / "foreign_project").mkdir(parents=True)
    foreign_root = LocalGovernanceRootBinding.from_trusted_base(
        foreign_base, project_id="foreign_project"
    )
    with pytest.raises(ProjectionLifecycleError) as foreign_error:
        _default_projection_refresh_hook(
            coordinator_store=coordinator,
            execution_store=execution,
            governance_store=store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=foreign_root,
            authority_binding=record.authority,
            required=True,
        )
    assert foreign_error.value.code == "PROJECT_MISMATCH"

    with pytest.raises(ProjectionLifecycleError) as missing_error:
        _default_projection_refresh_hook(
            coordinator_store=coordinator,
            execution_store=execution,
            governance_store=store,
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            required=True,
        )
    assert missing_error.value.code == "TRUSTED_PROJECTION_CONTEXT_REQUIRED"

    plan_path.unlink()
    with pytest.raises(ProjectionLifecycleError) as missing_source_error:
        hook()
    assert missing_source_error.value.code == "STALE_AUTHORITY"
    assert not (Path(root.root_path) / "MAP.md").exists()
    assert not (Path(root.root_path) / "STATUS.md").exists()
