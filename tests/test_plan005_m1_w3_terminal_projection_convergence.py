"""Plan #005 M1/W3 terminal lifecycle and generated projection convergence."""

from __future__ import annotations

import hashlib
import os
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
from aota_forge.composition.stewardship import (
    StewardshipExecutionState,
    create_legacy_stewardship_executor,
)
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
from aota_forge.governance.terminal_projection import (
    TerminalProjectionReconciliationError,
    create_terminal_projection_reconciler,
)
from aota_forge.mcp_transport import create_aota_invoke_dispatch
from aota_forge.runtime.task_main.control import (
    AF_TASK_MAIN_ROLE,
    TaskMainControlService,
    TaskMainProjectionConvergenceError,
)
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    default_coordinator_id,
)
from aota_forge.runtime.task_main.context_binding import DurableTaskMainContextBinding
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    _current_local_plan_route_eligible,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.runtime.task_main.review_adoption import trusted_plan_authority_from_binding
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY
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
    InMemoryGitHubPort,
    InMemoryGitPort,
    TrustedPlanIdentity,
    TrustedStewardFinalizer,
    TrustedUserGateState,
)
from tests.test_af57_m3_ac10_semantic_steward_bootstrap import (
    _EnvGuard,
    _runtime_config_json,
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
    projection_plan_id: str = PLAN_ID,
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
    if coordinator_store.get(f"{PROJECT_ID}:{MILESTONE}") is None:
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
        projection_plan_id=projection_plan_id,
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


def test_real_task_main_bootstrap_reproduces_close_projection_failure_and_w1_denial(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    project_root = Path(world["project_root"])
    governance_root = world["governance_root"]
    store = world["store"]
    active_record = store.get_plan(PROJECT_ID, PLAN_ID)
    assert active_record is not None
    origin_session = "session:plan005-m1-bootstrap-reentry"
    view = MilestonePlanView(
        plan_authority=active_record.authority.authority_ref,
        plan_digest=active_record.authority.source_digest,
        plan_source_revision=str(active_record.authority.source_revision),
        milestone_id=MILESTONE,
        entry_base="b" * 40,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE, work_items=["W1"], dependencies=[]
        ),
        milestone_user_approval_satisfied=True,
    )
    runtime_config = tmp_path / "runtime.json"
    _runtime_config_json(runtime_config)
    coordinator_path = project_root / ".aota" / "coordinator.json"
    execution_path = project_root / ".aota" / "execution.json"
    bootstrap_path = write_bootstrap_file(
        worktree_root=project_root,
        project_id=PROJECT_ID,
        worktree_id="plan005-m1-terminal-projection-test",
        coordinator_store_path=coordinator_path,
        execution_store_path=execution_path,
        runtime_config_path=runtime_config,
        origin_task_main_session_ref=origin_session,
        live_plan_view=view,
        next_milestone_view=None,
        plan_id=PLAN_ID,
        plan_authority_binding=active_record.authority,
        governance_base=Path(governance_root.root_path).parent,
        governance_store_path=project_root / ".aota" / "governance.sqlite3",
    )

    binding = None
    restarted_binding = None
    journal = None
    try:
        with _EnvGuard():
            os.environ[BOOTSTRAP_ENV_ROOT] = str(project_root)
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bootstrap_path)
            binding = try_build_task_main_binding()
        assert binding is not None
        context = binding.trusted_task_main_context
        assert context is not None
        service = context.control_service
        active_recovery = create_aota_invoke_dispatch(binding)(
            "governance.terminal_projection.reconcile",
            {},
        )
        assert active_recovery["ok"] is False
        assert active_recovery["error"]["code"] == "TERMINAL_PRECONDITION_FAILED"
        handle = service.activate_milestone(
            profile=AF_TASK_MAIN_ROLE,
            plan_view=view,
            origin_task_main_session_ref=origin_session,
            executor_id="plan005-m1-bootstrap-test",
            project_id=PROJECT_ID,
        )
        assert handle.coordinator_id == context.coordinator_id

        projection_root = Path(governance_root.root_path)
        (projection_root / "MAP.md").write_text("stale map\n", encoding="utf-8")
        (projection_root / "STATUS.md").unlink()
        (projection_root / "STATUS.md").mkdir()
        checkpoint, _ = _checkpoint_for_local_plan(
            kind="PLAN_CLOSE", accepted=True, authority=active_record.authority
        )
        journal = FileBackedDurableJournalStore(
            project_root / ".aota" / "governance-journal.json"
        )
        lifecycle = LocalPlanLifecycleCoordinator(store, journal)
        probe, _, _, _ = _executor(
            project_root,
            lambda _handoff: None,
            governance_store=store,
            coordinator_store=service._coord_store,
            live_plan_view=view,
            result_sandbox=binding.sandbox,
        )
        close_executor = create_legacy_stewardship_executor(
            runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
            governance_store=store,
            coordinator_store=service._coord_store,
            coordinator_id=context.coordinator_id,
            live_plan_view=view,
            finalizer=probe.finalizer,
            semantic_dispatch=lambda _handoff: None,
            result_sandbox=binding.sandbox,
            origin_session_ref=origin_session,
            local_plan_lifecycle_coordinator=lifecycle,
            local_plan_close_request_factory=lambda current_checkpoint, current: _request(
                governance_root,
                current_checkpoint.plan_id,
                "plan005-m1-bootstrap-close",
                expected_revision=current.revision,
            ),
        )

        class ClosureReadyRunner:
            coordinator_id = context.coordinator_id
            stewardship_outcome = None
            calls = 0

            def advance_once(self, *, session_available=True):
                self.calls += 1
                self.stewardship_outcome = close_executor.execute(checkpoint)
                assert self.stewardship_outcome.execution_state is StewardshipExecutionState.FINALIZED
                state = service._coord_store.get(self.coordinator_id)
                return RunnerOutcome(
                    disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
                    coordinator_id=self.coordinator_id,
                    coordinator_revision=state.coordinator_revision,
                    milestone_closure_ready=True,
                )

        closure_runner = ClosureReadyRunner()
        service._runner_factory = lambda **_kwargs: closure_runner
        with pytest.raises(TaskMainProjectionConvergenceError) as error:
            service.advance_once(
                profile=AF_TASK_MAIN_ROLE,
                coordinator_id=context.coordinator_id,
                live_plan_view=view,
                handoff_resolver=lambda _ref: pytest.fail(
                    "Plan_CLOSE fixture must not dispatch a Worker"
                ),
            )
        assert error.value.authority_mutation == "VERIFIED"
        assert error.value.projection_convergence == "BLOCKED"
        assert closure_runner.calls == 1
        retired = store.get_plan(PROJECT_ID, PLAN_ID)
        assert retired is not None and retired.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        terminal_reader = LocalPlanAuthorityReadAdapter(
            LocalPlanAuthorityDestination(
                governance_root,
                PLAN_ID,
                object_ref_subject(
                    make_id(IdKind.SUBJECT, PLAN_ID, sub_kind=SubjectKind.PLAN)
                ),
            ),
            binding=retired.authority,
        )
        assert load_local_portable_plan(terminal_reader).plan_status == "completed"

        with pytest.raises(CoordinatorBindingError, match="not eligible"):
            service.advance_once(
                profile=AF_TASK_MAIN_ROLE,
                coordinator_id=context.coordinator_id,
                live_plan_view=view,
                handoff_resolver=lambda _ref: pytest.fail(
                    "W1 must deny retired Plan routing before worker dispatch"
                ),
            )
        assert closure_runner.calls == 1
        assert (projection_root / "MAP.md").is_file()
        assert (projection_root / "STATUS.md").is_dir()

        invoke = create_aota_invoke_dispatch(binding)
        retired_normal_route = invoke("task_main.advance_once", {})
        assert retired_normal_route["ok"] is False
        assert retired_normal_route["error"]["code"] == "COORDINATOR_BINDING_ERROR"
        assert closure_runner.calls == 1

        before_recovery_record = store.get_plan(PROJECT_ID, PLAN_ID)
        assert before_recovery_record is not None
        before_recovery_journal = len(journal.list_all())
        before_recovery_coordinator = service._coord_store.list_all()
        before_recovery_execution = service._exec_store.list_all()
        before_recovery_receipt = closure_runner.stewardship_outcome.receipt
        (projection_root / "STATUS.md").rmdir()
        (projection_root / "STATUS.md").write_text("stale status\n", encoding="utf-8")

        def invoke_reconciliation():
            return invoke(
                "governance.terminal_projection.reconcile",
                {},
            )

        first_reconciliation = invoke_reconciliation()
        assert first_reconciliation["ok"] is True, first_reconciliation
        assert first_reconciliation["payload"]["status"] == "VERIFIED"
        assert first_reconciliation["payload"]["authority_mutation"] == "NONE"
        assert first_reconciliation["payload"]["terminal_authority"] == "VERIFIED"
        assert first_reconciliation["payload"]["projection_convergence"] == "VERIFIED"
        projection_result = first_reconciliation["payload"]
        assert projection_result["project_id"] == PROJECT_ID
        assert projection_result["plan_id"] == PLAN_ID
        assert PLAN_ID in (projection_root / "MAP.md").read_text(encoding="utf-8")
        assert f"plan_lifecycle: {PLAN_ID}=retired" in (
            projection_root / "STATUS.md"
        ).read_text(encoding="utf-8")
        assert "governance.terminal_projection.reconcile" in binding.tool_surface.all_capability_names()

        from aota_forge.core_ingress import dispatch_tool_operation
        from aota_forge.mcp_transport import _to_canonical_binding

        canonical_binding = _to_canonical_binding(binding)
        foreign_project_binding = replace(
            canonical_binding,
            project_id="foreign_project",
        )
        foreign_project_result = dispatch_tool_operation(
            "governance.terminal_projection.reconcile",
            {},
            foreign_project_binding,
        )
        assert foreign_project_result.ok is False
        assert foreign_project_result.error["code"] == "AUTHORITY_DENIED"
        mismatched_plan_authority = replace(
            canonical_binding.plan_authority_binding,
            plan_id=PLAN002_ID,
            authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN002_ID),
        )
        mismatched_plan_binding = replace(
            canonical_binding,
            plan_authority_binding=mismatched_plan_authority,
        )
        mismatched_plan_result = dispatch_tool_operation(
            "governance.terminal_projection.reconcile",
            {},
            mismatched_plan_binding,
        )
        assert mismatched_plan_result.ok is False
        assert mismatched_plan_result.error["code"] == "AUTHORITY_DENIED"

        unknown_input = invoke(
            "governance.terminal_projection.reconcile",
            {"plan_id": PLAN_ID},
        )
        assert unknown_input["ok"] is False
        assert unknown_input["error"]["code"] == "UNKNOWN_INPUT"

        (projection_root / "MAP.md").write_text("stale before idempotent replay\n", encoding="utf-8")
        second_reconciliation = invoke_reconciliation()
        assert second_reconciliation["ok"] is True
        assert second_reconciliation["payload"] == projection_result
        terminal_after_recovery = store.get_plan(PROJECT_ID, PLAN_ID)
        assert terminal_after_recovery == before_recovery_record
        assert len(journal.list_all()) == before_recovery_journal
        assert service._coord_store.list_all() == before_recovery_coordinator
        assert service._exec_store.list_all() == before_recovery_execution
        assert closure_runner.stewardship_outcome.receipt == before_recovery_receipt
        assert "stale before idempotent replay" not in (projection_root / "MAP.md").read_text(
            encoding="utf-8"
        )

        # Recovery is reconstructed from durable current truth after reopening
        # every owner. The bootstrap's pre-close revision/digest are stale and
        # serve only as the trusted Plan identity selector.
        service._coord_store.close()
        service._exec_store.close()
        journal.close()
        journal = None
        store.close()
        world["store"] = SQLiteProjectGovernanceStore(
            project_root / ".aota" / "governance.sqlite3"
        )
        with _EnvGuard():
            os.environ[BOOTSTRAP_ENV_ROOT] = str(project_root)
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bootstrap_path)
            restarted_binding = try_build_task_main_binding()
        assert restarted_binding is not None
        restarted_context = restarted_binding.trusted_task_main_context
        assert restarted_context is not None
        assert restarted_binding.plan_authority_binding is not None
        reopened_terminal_record = world["store"].get_plan(PROJECT_ID, PLAN_ID)
        assert reopened_terminal_record is not None
        assert restarted_binding.plan_authority_binding.source_digest == (
            active_record.authority.source_digest
        )
        assert restarted_binding.plan_authority_binding.source_digest != (
            reopened_terminal_record.authority.source_digest
        )
        (projection_root / "STATUS.md").write_text(
            "stale after restart\n", encoding="utf-8"
        )
        restarted_invoke = create_aota_invoke_dispatch(restarted_binding)
        restarted_recovery = restarted_invoke(
            "governance.terminal_projection.reconcile",
            {},
        )
        assert restarted_recovery["ok"] is True, restarted_recovery
        assert restarted_recovery["payload"] == projection_result
        assert world["store"].get_plan(PROJECT_ID, PLAN_ID) == before_recovery_record
        reopened_journal = FileBackedDurableJournalStore(
            project_root / ".aota" / "governance-journal.json"
        )
        assert len(reopened_journal.list_all()) == before_recovery_journal
        reopened_journal.close()
        assert restarted_context.control_service._coord_store.list_all() == before_recovery_coordinator
        assert restarted_context.control_service._exec_store.list_all() == before_recovery_execution
        assert closure_runner.calls == 1
        with pytest.raises(CoordinatorBindingError, match="not eligible"):
            restarted_context.control_service.advance_once(
                profile=AF_TASK_MAIN_ROLE,
                coordinator_id=restarted_context.coordinator_id,
                live_plan_view=restarted_context.live_plan_view,
                handoff_resolver=lambda _ref: pytest.fail(
                    "restarted retired Plan must remain ineligible for normal routing"
                ),
            )
        assert (projection_root / "STATUS.md").is_file()
    finally:
        if binding is not None:
            context = binding.trusted_task_main_context
            if context is not None:
                context.control_service._coord_store.close()
                context.control_service._exec_store.close()
        if restarted_binding is not None:
            restarted_context = restarted_binding.trusted_task_main_context
            if restarted_context is not None:
                restarted_context.control_service._coord_store.close()
                restarted_context.control_service._exec_store.close()
        if journal is not None:
            journal.close()
        store.close()
        if world.get("store") is not store:
            reopened_store = world.get("store")
            if isinstance(reopened_store, SQLiteProjectGovernanceStore):
                reopened_store.close()


def _closure_ready(self, *, session_available=True):
    return RunnerOutcome(
        disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
        coordinator_id=self.coordinator_id,
        coordinator_revision=2,
        milestone_closure_ready=True,
    )


def test_steward_plan_close_projects_terminal_truth(
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

    assert len(refresh_results) == 1


def test_projection_failure_is_visible_and_projection_only_recovery_is_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    root_path = world["governance_root_path"]
    assert isinstance(root_path, Path)
    (root_path / "MAP.md").write_text("stale map\n", encoding="utf-8")
    (root_path / "STATUS.md").mkdir()
    active_record = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert active_record is not None
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
    runner_instances = []
    original_factory = service._runner_factory

    def capture_runner(**kwargs):
        runner = original_factory(**kwargs)
        runner_instances.append(runner)
        return runner

    service._runner_factory = capture_runner
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
    assert (root_path / "MAP.md").is_file()
    assert (root_path / "STATUS.md").is_dir()
    assert len(runner_instances) == 1
    close_receipt = runner_instances[0].stewardship_outcome.receipt
    assert close_receipt is not None
    coordinator_before_recovery = coordinator.list_all()
    execution_before_recovery = execution.list_all()

    # Normal progression still fails closed through W1's trusted live authority
    # resolver once the current Store says this Plan is retired.
    bound_view = MilestonePlanView(
        plan_authority=active_record.authority.authority_ref,
        plan_digest=active_record.authority.source_digest,
        plan_source_revision=str(active_record.authority.source_revision),
        milestone_id=MILESTONE,
        entry_base="b" * 40,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE, work_items=["W1"], dependencies=[]
        ),
        milestone_user_approval_satisfied=True,
    )
    context_binding = DurableTaskMainContextBinding(
        project_id=PROJECT_ID,
        project_fingerprint="c" * 64,
        plan_binding=active_record.authority,
        origin_task_main_session_ref="session:plan005-w3",
    )
    route_service = TaskMainControlService(
        coordinator_store=coordinator,
        execution_store=execution,
        execution_dispatcher=ExecutionDispatcher(
            ExecutorRegistry(), state_store=execution
        ),
        context_binding=context_binding,
        current_plan_eligibility_resolver=lambda route_project, route_view, route_context: (
            _current_local_plan_route_eligible(
                project_id=route_project,
                live_plan_view=route_view,
                context_binding=route_context,
                trusted_plan_binding=active_record.authority,
                governance_base=Path(world["governance_root"].root_path).parent,
                governance_store_path=tmp_path / ".aota" / "governance.sqlite3",
            )
        ),
    )
    with pytest.raises(CoordinatorBindingError, match="not eligible"):
        route_service.advance_once(
            profile=AF_TASK_MAIN_ROLE,
            coordinator_id=default_coordinator_id(
                project_id=PROJECT_ID, plan_id=PLAN_ID, milestone_id=MILESTONE
            ),
            live_plan_view=bound_view,
            handoff_resolver=lambda _ref: pytest.fail(
                "retired Plan must not dispatch through normal routing"
            ),
        )

    # The Governance-domain capability does not call workflow progression.
    (root_path / "MAP.md").write_text("stale before recovery\n", encoding="utf-8")
    monkeypatch.setattr(
        runner_module.TaskMainMilestoneRunner,
        "advance_once",
        lambda *_args, **_kwargs: pytest.fail(
            "projection-only reconciliation must not re-enter coordinator progression"
        ),
    )
    (root_path / "STATUS.md").rmdir()
    (root_path / "STATUS.md").write_text("stale before recovery\n", encoding="utf-8")
    reconciler = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        project_root=world["project_root"],
        governance_root=world["governance_root"],
        governance_store=tmp_path / ".aota" / "governance.sqlite3",
        coordinator_store=coordinator,
        execution_store=execution,
    )
    recovery_result = reconciler()
    assert recovery_result.status == "VERIFIED"
    assert recovery_result.authority_mutation == "NONE"
    assert recovery_result.projection_convergence == "VERIFIED"
    assert "stale before recovery" not in (root_path / "MAP.md").read_text(encoding="utf-8")
    assert "stale before recovery" not in (root_path / "STATUS.md").read_text(encoding="utf-8")

    # Retry after another restart. The projection is regenerated from current
    # owners; no in-memory success marker or prior result is used.
    service._coord_store.close()
    service._exec_store.close()
    world["store"].close()
    (root_path / "STATUS.md").write_text("stale after retry restart\n", encoding="utf-8")
    world["store"] = SQLiteProjectGovernanceStore(
        tmp_path / ".aota" / "governance.sqlite3"
    )
    final_service, restarted_coordinator, restarted_execution, _ = _control_service(world)
    result = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        project_root=world["project_root"],
        governance_root=world["governance_root"],
        governance_store=tmp_path / ".aota" / "governance.sqlite3",
        coordinator_store=restarted_coordinator,
        execution_store=restarted_execution,
    )()
    assert result.authority_mutation == "NONE"
    assert result.projection_convergence == "VERIFIED"
    assert result.project_id == PROJECT_ID
    assert result.plan_id == PLAN_ID
    assert "plan_lifecycle: " + PLAN_ID + "=retired" in (
        root_path / "STATUS.md"
    ).read_text(encoding="utf-8")

    recovered = world["store"].get_plan(PROJECT_ID, PLAN_ID)
    assert recovered is not None
    assert recovered.revision == terminal_revision
    assert recovered.authority.source_digest == terminal_digest
    assert _terminal_journal_entry_count(tmp_path) == journal_entries
    assert restarted_coordinator.list_all() == coordinator_before_recovery
    assert restarted_execution.list_all() == execution_before_recovery
    assert runner_instances[0].stewardship_outcome.receipt == close_receipt
    final_service._coord_store.close()
    final_service._exec_store.close()
    world["store"].close()


def test_terminal_projection_reconciliation_rejects_nonterminal_plan(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _service, coordinator, execution, _live_view = _control_service(world)

    reconciler = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        project_root=world["project_root"],
        governance_root=world["governance_root"],
        governance_store=world["store"],
        coordinator_store=coordinator,
        execution_store=execution,
    )
    with pytest.raises(TerminalProjectionReconciliationError) as error:
        reconciler()
    assert error.value.code == "TERMINAL_PRECONDITION_FAILED"
    assert not (Path(world["governance_root_path"]) / "MAP.md").exists()
    assert not (Path(world["governance_root_path"]) / "STATUS.md").exists()

    foreign_project_root = Path(world["governance_root_path"]).parent / "foreign_project"
    foreign_project_root.mkdir()
    foreign_root = LocalGovernanceRootBinding.from_trusted_base(
        Path(world["governance_root_path"]).parent, project_id="foreign_project"
    )
    with pytest.raises(TerminalProjectionReconciliationError) as identity_error:
        create_terminal_projection_reconciler(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=foreign_root,
            governance_store=world["store"],
            coordinator_store=coordinator,
            execution_store=execution,
        )
    assert identity_error.value.code == "PROJECT_MISMATCH"

    with pytest.raises(TerminalProjectionReconciliationError) as missing_root:
        create_terminal_projection_reconciler(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world["project_root"],
            governance_root=None,
            governance_store=world["store"],
            coordinator_store=coordinator,
            execution_store=execution,
        )
    assert missing_root.value.code == "TRUSTED_PROJECTION_CONTEXT_REQUIRED"


def test_terminal_projection_reconciliation_rejects_current_authority_drift(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    service, coordinator, execution, _live_view = _control_service(world)
    store = world["store"]
    assert isinstance(store, SQLiteProjectGovernanceStore)
    record = store.get_plan(PROJECT_ID, PLAN004_ID)
    assert record is not None
    mismatched_authority = replace(
        record.authority,
        source_revision="f" * 64,
        source_digest="f" * 64,
    )
    store.compare_and_swap_plan(
        PROJECT_ID,
        PLAN004_ID,
        record.revision,
        authority=mismatched_authority,
    )
    reconciler = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN004_ID,
        project_root=world["project_root"],
        governance_root=world["governance_root"],
        governance_store=store,
        coordinator_store=coordinator,
        execution_store=execution,
    )
    with pytest.raises(TerminalProjectionReconciliationError) as error:
        reconciler()
    assert error.value.code == "CURRENT_AUTHORITY_MISMATCH"
    service._coord_store.close()
    service._exec_store.close()
    store.close()


def test_terminal_projection_reconciliation_rejects_foreign_source_and_incomplete_body(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    service, coordinator, execution, _live_view = _control_service(world)
    store = world["store"]
    assert isinstance(store, SQLiteProjectGovernanceStore)
    record = store.get_plan(PROJECT_ID, PLAN004_ID)
    assert record is not None
    foreign_authority = PlanAuthorityBinding(
        plan_id=PLAN004_ID,
        source_kind="github_issue",
        authority_ref="owner/repository#4",
    )
    store.compare_and_swap_plan(
        PROJECT_ID,
        PLAN004_ID,
        record.revision,
        authority=foreign_authority,
    )
    foreign_source_reconciler = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN004_ID,
        project_root=world["project_root"],
        governance_root=world["governance_root"],
        governance_store=store,
        coordinator_store=coordinator,
        execution_store=execution,
    )
    with pytest.raises(TerminalProjectionReconciliationError) as source_error:
        foreign_source_reconciler()
    assert source_error.value.code == "TERMINAL_PRECONDITION_FAILED"
    service._coord_store.close()
    service._exec_store.close()
    store.close()

    second_world = _world(tmp_path / "incomplete")
    second_service, second_coordinator, second_execution, _ = _control_service(second_world)
    second_store = second_world["store"]
    assert isinstance(second_store, SQLiteProjectGovernanceStore)
    terminal_record = second_store.get_plan(PROJECT_ID, PLAN004_ID)
    assert terminal_record is not None
    plan_path = Path(second_world["governance_root_path"]) / PLAN004_ID / "plan.md"
    incomplete_body = (
        plan_path.read_text(encoding="utf-8")
        .replace("PLAN_STATUS=completed", "PLAN_STATUS=active")
        .replace("CURRENT_STATUS=completed", "CURRENT_STATUS=m3_user_acceptance_ready")
        .replace("M3_STATUS=completed", "M3_STATUS=in_progress")
        .replace("M3_ACCEPTED=yes", "M3_ACCEPTED=no")
    )
    plan_path.write_text(incomplete_body, encoding="utf-8")
    incomplete_digest = _digest(incomplete_body)
    second_store.compare_and_swap_plan(
        PROJECT_ID,
        PLAN004_ID,
        terminal_record.revision,
        authority=replace(
            terminal_record.authority,
            source_revision=incomplete_digest,
            source_digest=incomplete_digest,
        ),
    )
    incomplete_reconciler = create_terminal_projection_reconciler(
        project_id=PROJECT_ID,
        plan_id=PLAN004_ID,
        project_root=second_world["project_root"],
        governance_root=second_world["governance_root"],
        governance_store=second_store,
        coordinator_store=second_coordinator,
        execution_store=second_execution,
    )
    with pytest.raises(TerminalProjectionReconciliationError) as body_error:
        incomplete_reconciler()
    assert body_error.value.code == "TERMINAL_PRECONDITION_FAILED"
    second_service._coord_store.close()
    second_service._exec_store.close()
    second_store.close()


def test_terminal_projection_operation_role_exposure_and_unbound_denial(
    tmp_path: Path,
) -> None:
    operation = "governance.terminal_projection.reconcile"
    from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_tool_operation
    from aota_forge.mcp_transport import _UnboundAotaAdapter, UnboundHostContext
    from aota_forge.work_plane.af_roles import get_tool_surface_for_role
    from aota_forge.work_plane.task_main_exposure import (
        TASK_MAIN_UNBOUND_OPERATIONS,
    )

    assert operation in TASK_MAIN_UNBOUND_OPERATIONS
    assert operation in get_tool_surface_for_role("task-main").all_capability_names()
    for role in ("coder", "analyst", "reviewer", "project-steward"):
        assert operation not in get_tool_surface_for_role(role).all_capability_names()
        binding = CanonicalDispatchBinding(
            project_id=PROJECT_ID,
            handoff=SimpleNamespace(work_role=role),
            tool_surface=SimpleNamespace(work_role=role),
            trusted_task_main_context=SimpleNamespace(
                terminal_projection_reconciler=lambda: pytest.fail(
                    "non-task-main role must be denied before invoking the reconciler"
                )
            ),
        )
        denied = dispatch_tool_operation(operation, {}, binding)
        assert denied.ok is False
        assert denied.error["code"] == "AUTHORITY_DENIED"

    unbound = _UnboundAotaAdapter(
        UnboundHostContext(repo_root=str(tmp_path), bind_global_dispatcher=False)
    )
    operation_list = unbound.invoke("operations.list", {})
    assert operation_list["ok"] is True
    assert operation in {
        item["operation"]
        for item in operation_list["payload"]["data"]["operations"]
    }
    discoverable_but_denied = unbound.invoke(operation, {})
    assert discoverable_but_denied["ok"] is False
    assert discoverable_but_denied["error"]["code"] == "AUTHORITY_DENIED"


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
