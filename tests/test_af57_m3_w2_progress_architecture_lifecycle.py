"""AF #57 M3/W2 projection and Architecture lifecycle proof."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.composition.task_main import create_task_main_control_service
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    FileBackedExecutionStateStore,
)
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.governance import (
    ArchitectureMetadataRecord,
    ArchitecturePromotionRequest,
    ArchitectureStateInput,
    GovernanceProjectionInput,
    GovernanceProjectionLifecycle,
    ProjectionRefreshRequest,
    ProjectionLifecycleError,
    REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
    REFRESH_TRIGGER_WORK_RECONCILIATION,
    StaleGovernanceMetadataRevisionError,
)
from aota_forge.governance.context_route import ContextRouteInput
from aota_forge.governance.project_store import ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.runtime.task_main.control import AF_TASK_MAIN_ROLE
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.handoff_runtime import WorkSemanticProjection
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


PROJECT_ID = "aota_forge"
PLAN_ID = "plan_architecture_w2"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record() -> ProjectPlanRecord:
    return ProjectPlanRecord(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        lifecycle_state="active",
        authority=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind="local_governance",
            authority_ref=f"local-governance:{PROJECT_ID}/{PLAN_ID}",
        ),
    )


def _source() -> GovernanceProjectionInput:
    return GovernanceProjectionInput(
        project_id=PROJECT_ID,
        project_ref=f"project:{PROJECT_ID}",
        project_status="active",
        plan_records=(_record(),),
        plan_documents=(),
        architecture=ArchitectureStateInput(
            project_id=PROJECT_ID,
            baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
            baseline_status="approved",
            accepted_ref=f"local-governance/{PROJECT_ID}/ARCHITECTURE.md",
        ),
        coordinator_states=(),
        execution_records=(),
    )


def _coordinator() -> TaskMainCoordinatorState:
    return TaskMainCoordinatorState(
        coordinator_id="coord-w2",
        plan_authority=f"local-governance:{PROJECT_ID}/{PLAN_ID}",
        plan_digest="a" * 64,
        milestone_id="M3",
        entry_base="b" * 40,
        origin_task_main_session_ref="session-w2",
        project_id=PROJECT_ID,
        executor_id="executor-w2",
        user_approval_satisfied=True,
        work_items=("W1", "W2"),
        wi_status={"W1": "ACTIVE", "W2": "PENDING"},
        open_blockers=("blocker:w2",),
        next_action="continue W1",
    )


def _execution_record(task_id: str) -> DurableExecutionRecord:
    return DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="executor-w2",
        package_id=f"{task_id}:package",
        correlation_id=f"correlation:{task_id}",
        dispatch_attempt_id=f"attempt:{task_id}",
        idempotency_key=f"idempotency:{task_id}",
        intent_fingerprint="c" * 64,
    )


def _promotion_fixture(tmp_path: Path):
    governance_base = tmp_path / "plans"
    project_root = governance_base / PROJECT_ID
    project_root.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(governance_base, project_id=PROJECT_ID)
    baseline = "# Architecture v1\n"
    target = "# Architecture v2\n"
    architecture_path = project_root / "ARCHITECTURE.md"
    architecture_path.write_text(baseline, encoding="utf-8")
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    store.put_plan(_record())
    store.put_architecture_metadata(
        ArchitectureMetadataRecord(
            project_id=PROJECT_ID,
            current_version="v1",
            current_digest=_digest(baseline),
            promotion_receipt_ref="receipt:initial",
        )
    )
    lifecycle = GovernanceProjectionLifecycle(governance_store=store, governance_root=binding)
    request = ArchitecturePromotionRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_revision=1,
        expected_current_version="v1",
        expected_current_digest=_digest(baseline),
        target_version="v2",
        content=target,
        accepted_delta_ref="delta:architecture-w2",
        accepted_delta_digest="c" * 64,
        promotion_receipt_ref="receipt:architecture-w2",
        baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
        baseline_status="approved",
    )
    return lifecycle, store, project_root, architecture_path, baseline, request


def test_architecture_metadata_cas_checks_revision_version_and_digest(tmp_path: Path):
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        store.put_architecture_metadata(
            ArchitectureMetadataRecord(
                project_id=PROJECT_ID,
                current_version="v1",
                current_digest="a" * 64,
                promotion_receipt_ref="receipt:initial",
            )
        )
        with pytest.raises(StaleGovernanceMetadataRevisionError):
            store.compare_and_swap_architecture_metadata(
                PROJECT_ID,
                1,
                expected_current_version="v0",
                expected_current_digest="a" * 64,
                current_version="v2",
                current_digest="b" * 64,
            )
        promoted = store.compare_and_swap_architecture_metadata(
            PROJECT_ID,
            1,
            expected_current_version="v1",
            expected_current_digest="a" * 64,
            current_version="v2",
            current_digest="b" * 64,
            accepted_delta_ref="delta:w2",
            accepted_delta_digest="c" * 64,
            promotion_receipt_ref="receipt:w2",
            promoted_by_plan_id=PLAN_ID,
        )
        assert promoted.revision == 2
        assert promoted.promoted_by_plan_id == PLAN_ID
    finally:
        store.close()

    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    try:
        assert reopened.get_architecture_metadata(PROJECT_ID).current_digest == "b" * 64
        assert reopened.get_architecture_metadata(PROJECT_ID).promoted_by_plan_id == PLAN_ID
    finally:
        reopened.close()


def test_existing_w1_architecture_table_gets_only_the_bounded_w2_column(tmp_path: Path):
    database = tmp_path / "governance.sqlite3"
    initial = SQLiteProjectGovernanceStore(database)
    initial.close()
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE project_architecture_metadata (
                project_id TEXT NOT NULL PRIMARY KEY,
                current_version TEXT NOT NULL,
                current_digest TEXT NOT NULL,
                accepted_delta_ref TEXT,
                accepted_delta_digest TEXT,
                promotion_receipt_ref TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    reopened = SQLiteProjectGovernanceStore(database)
    try:
        columns = {
            row[1]
            for row in reopened._connection.execute("PRAGMA table_info(project_architecture_metadata)")
        }
        assert columns == {
            "project_id",
            "current_version",
            "current_digest",
            "accepted_delta_ref",
            "accepted_delta_digest",
            "promotion_receipt_ref",
            "revision",
            "promoted_by_plan_id",
        }
    finally:
        reopened.close()


def test_explicit_refresh_materializes_derived_views_and_receipt(tmp_path: Path):
    lifecycle = GovernanceProjectionLifecycle()
    result = lifecycle.refresh(
        ProjectionRefreshRequest(
            trigger=REFRESH_TRIGGER_WORK_RECONCILIATION,
            source=replace(_source(), architecture=None),
        ),
        project_directory=tmp_path / "aota_forge",
    )
    assert result.bundle.progress.complete is True
    assert result.receipt.trigger == REFRESH_TRIGGER_WORK_RECONCILIATION
    assert result.map_path.read_text(encoding="utf-8") == result.views.map_markdown
    assert result.status_path.read_text(encoding="utf-8") == result.views.status_markdown
    assert "AUTHORITY=no" in result.map_path.read_text(encoding="utf-8")
    assert "AUTHORITY=no" in result.status_path.read_text(encoding="utf-8")


def test_architecture_promotion_is_cas_bound_read_back_verified_and_recoverable(tmp_path: Path):
    governance_base = tmp_path / "plans"
    project_root = governance_base / PROJECT_ID
    project_root.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(governance_base, project_id=PROJECT_ID)
    baseline = "# Architecture v1\n\nAccepted baseline.\n"
    target = "# Architecture v2\n\nAccepted delta.\n"
    (project_root / "ARCHITECTURE.md").write_text(baseline, encoding="utf-8")

    database = tmp_path / "governance.sqlite3"
    store = SQLiteProjectGovernanceStore(database)
    store.put_architecture_metadata(
        ArchitectureMetadataRecord(
            project_id=PROJECT_ID,
            current_version="v1",
            current_digest=_digest(baseline),
            promotion_receipt_ref="receipt:initial",
        )
    )
    lifecycle = GovernanceProjectionLifecycle(governance_store=store, governance_root=binding)
    request = ArchitecturePromotionRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_revision=1,
        expected_current_version="v1",
        expected_current_digest=_digest(baseline),
        target_version="v2",
        content=target,
        accepted_delta_ref="delta:architecture-w2",
        accepted_delta_digest="c" * 64,
        promotion_receipt_ref="receipt:architecture-w2",
        baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
        baseline_status="approved",
    )
    promoted = lifecycle.promote_architecture(request, projection_source=_source())
    assert promoted.status == "promoted"
    assert promoted.metadata.current_version == "v2"
    assert promoted.metadata.promoted_by_plan_id == PLAN_ID
    assert promoted.architecture_path.read_text(encoding="utf-8") == target
    assert promoted.projection is not None
    assert promoted.projection.bundle.architecture.current_version == "v2"
    assert (project_root / ".architecture-promotion-receipt.json").is_file()
    assert "current_version: v2" in (project_root / "MAP.md").read_text(encoding="utf-8")
    assert (project_root / "STATUS.md").read_text(encoding="utf-8") == promoted.projection.views.status_markdown
    store.close()

    reopened_store = SQLiteProjectGovernanceStore(database)
    try:
        recovered = GovernanceProjectionLifecycle(
            governance_store=reopened_store,
            governance_root=binding,
        ).recover_architecture_promotion(projection_source=_source())
        assert recovered is not None
        assert recovered.status == "recovered"
        assert recovered.receipt.phase == "committed"
        assert recovered.metadata.current_digest == _digest(target)
        assert recovered.projection is not None
        assert recovered.projection.request.trigger == REFRESH_TRIGGER_ARCHITECTURE_PROMOTION
        assert recovered.projection.bundle.architecture.current_digest == _digest(target)
        architecture_entries = recovered.projection.context_route.entries_for_kind("architecture_card")
        assert architecture_entries
        assert architecture_entries[0].projection_id == recovered.projection.bundle.architecture.projection_id()
    finally:
        reopened_store.close()


def test_refresh_reads_existing_durable_owners_and_rebuilds_after_restart(tmp_path: Path):
    governance_path = tmp_path / "governance.sqlite3"
    coordinator_path = tmp_path / "coordinators.json"
    execution_path = tmp_path / "executions.json"

    governance = SQLiteProjectGovernanceStore(governance_path)
    governance.put_plan(_record())
    coordinator = FileBackedTaskMainCoordinatorStore(coordinator_path)
    coordinator.create(_coordinator())
    execution = FileBackedExecutionStateStore(execution_path)
    execution.create(_execution_record("task-w2"))

    lifecycle = GovernanceProjectionLifecycle(
        governance_store=governance,
        coordinator_store=coordinator,
        execution_store=execution,
    )
    first = lifecycle.refresh_from_durable_owners(
        project_id=PROJECT_ID,
        project_directory=tmp_path / "project",
    )
    assert first.bundle.progress.complete is True
    assert first.bundle.progress.active_coordinator_count == 1
    assert first.bundle.progress.execution_aggregate.total == 1
    assert first.bundle.progress.open_blockers == ("blocker:w2",)
    assert first.context_route.entries_for_kind("progress_card")
    first_bundle = first.bundle.to_dict()
    first_status = first.status_path.read_text(encoding="utf-8")

    governance.close()
    coordinator.close()
    execution.close()
    reopened_governance = SQLiteProjectGovernanceStore(governance_path)
    reopened_coordinator = FileBackedTaskMainCoordinatorStore(coordinator_path)
    reopened_execution = FileBackedExecutionStateStore(execution_path)
    try:
        second = GovernanceProjectionLifecycle(
            governance_store=reopened_governance,
            coordinator_store=reopened_coordinator,
            execution_store=reopened_execution,
        ).refresh_from_durable_owners(
            project_id=PROJECT_ID,
            project_directory=tmp_path / "project",
        )
        assert second.bundle.to_dict() == first_bundle
        assert second.status_path.read_text(encoding="utf-8") == first_status
        assert second.receipt.projection_digest == first.receipt.projection_digest
    finally:
        reopened_governance.close()
        reopened_coordinator.close()
        reopened_execution.close()


def test_architecture_stale_digest_and_unexpected_current_fail_without_authority_mutation(tmp_path: Path):
    governance_base = tmp_path / "plans"
    project_root = governance_base / PROJECT_ID
    project_root.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(governance_base, project_id=PROJECT_ID)
    baseline = "# Architecture v1\n"
    target = "# Architecture v2\n"
    architecture_path = project_root / "ARCHITECTURE.md"
    architecture_path.write_text(baseline, encoding="utf-8")
    database = tmp_path / "governance.sqlite3"
    store = SQLiteProjectGovernanceStore(database)
    store.put_architecture_metadata(
        ArchitectureMetadataRecord(
            project_id=PROJECT_ID,
            current_version="v1",
            current_digest=_digest(baseline),
            promotion_receipt_ref="receipt:initial",
        )
    )
    lifecycle = GovernanceProjectionLifecycle(governance_store=store, governance_root=binding)

    stale = ArchitecturePromotionRequest(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_revision=1,
        expected_current_version="v1",
        expected_current_digest="d" * 64,
        target_version="v2",
        content=target,
        accepted_delta_ref="delta:stale",
        accepted_delta_digest="e" * 64,
        promotion_receipt_ref="receipt:stale",
    )
    with pytest.raises(ProjectionLifecycleError) as stale_error:
        lifecycle.promote_architecture(stale)
    assert stale_error.value.code == "ARCHITECTURE_DIGEST_MISMATCH"
    assert architecture_path.read_text(encoding="utf-8") == baseline
    assert store.get_architecture_metadata(PROJECT_ID).revision == 1

    architecture_path.write_text("unexpected current\n", encoding="utf-8")
    with pytest.raises(ProjectionLifecycleError) as unexpected_error:
        lifecycle.promote_architecture(
            replace_request(stale, expected_current_digest=_digest(baseline))
        )
    assert unexpected_error.value.code == "ARCHITECTURE_DIGEST_MISMATCH"
    assert store.get_architecture_metadata(PROJECT_ID).revision == 1

    architecture_path.write_text(baseline, encoding="utf-8")
    cas_stale = replace_request(
        stale,
        expected_revision=99,
        expected_current_digest=_digest(baseline),
    )
    with pytest.raises(ProjectionLifecycleError) as cas_error:
        lifecycle.promote_architecture(cas_stale)
    assert cas_error.value.code == "PROMOTION_CAS_FAILED"
    assert store.get_architecture_metadata(PROJECT_ID).revision == 1
    assert not (project_root / ".architecture-promotion-stage.md").exists()
    store.close()


def test_architecture_projection_requires_real_authority_file(tmp_path: Path):
    governance_base = tmp_path / "plans"
    project_root = governance_base / PROJECT_ID
    project_root.mkdir(parents=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(governance_base, project_id=PROJECT_ID)
    store = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    store.put_plan(_record())
    store.put_architecture_metadata(
        ArchitectureMetadataRecord(
            project_id=PROJECT_ID,
            current_version="v1",
            current_digest="a" * 64,
            promotion_receipt_ref="receipt:initial",
        )
    )
    coordinator = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinators.json")
    coordinator.create(_coordinator())
    execution = FileBackedExecutionStateStore(tmp_path / "executions.json")
    lifecycle = GovernanceProjectionLifecycle(
        governance_store=store,
        governance_root=binding,
        coordinator_store=coordinator,
        execution_store=execution,
    )
    try:
        with pytest.raises(ProjectionLifecycleError) as direct_error:
            lifecycle.refresh(
                ProjectionRefreshRequest(
                    trigger=REFRESH_TRIGGER_WORK_RECONCILIATION,
                    source=_source(),
                ),
                project_directory=project_root,
            )
        assert direct_error.value.code == "FILE_MISSING"
        with pytest.raises(ProjectionLifecycleError) as error:
            lifecycle.refresh_from_durable_owners(
                project_id=PROJECT_ID,
                architecture=ArchitectureStateInput(
                    project_id=PROJECT_ID,
                    baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
                    baseline_status="approved",
                    accepted_ref=f"local-governance/{PROJECT_ID}/ARCHITECTURE.md",
                ),
                project_directory=project_root,
            )
        assert error.value.code == "FILE_MISSING"
        assert not (project_root / "MAP.md").exists()
        assert not (project_root / "STATUS.md").exists()
    finally:
        store.close()
        coordinator.close()
        execution.close()


@pytest.mark.parametrize(
    ("architecture", "request_updates", "error_code"),
    [
        (
            ArchitectureStateInput(
                project_id=PROJECT_ID,
                baseline_status="approved",
                accepted_ref=f"local-governance/{PROJECT_ID}/ARCHITECTURE.md",
            ),
            {},
            "ARCHITECTURE_BASELINE_BINDING_MISSING",
        ),
        (
            ArchitectureStateInput(
                project_id=PROJECT_ID,
                baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
                accepted_ref=f"local-governance/{PROJECT_ID}/ARCHITECTURE.md",
            ),
            {},
            "ARCHITECTURE_BASELINE_BINDING_MISSING",
        ),
        (
            ArchitectureStateInput(
                project_id=PROJECT_ID,
                baseline_id="AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1",
                baseline_status="pending",
                accepted_ref=f"local-governance/{PROJECT_ID}/ARCHITECTURE.md",
            ),
            {"baseline_status": "pending"},
            "ARCHITECTURE_BASELINE_NOT_ACCEPTED",
        ),
    ],
)
def test_invalid_accepted_baseline_fails_before_any_promotion_mutation(
    tmp_path: Path,
    architecture: ArchitectureStateInput,
    request_updates: dict[str, object],
    error_code: str,
):
    lifecycle, store, project_root, architecture_path, baseline, request = _promotion_fixture(tmp_path)
    try:
        with pytest.raises(ProjectionLifecycleError) as error:
            lifecycle.promote_architecture(
                replace_request(request, **request_updates),
                projection_source=replace_source_architecture(architecture),
            )
        assert error.value.code == error_code
        assert architecture_path.read_text(encoding="utf-8") == baseline
        assert store.get_architecture_metadata(PROJECT_ID).revision == 1
        assert not (project_root / ".architecture-promotion-receipt.json").exists()
        assert not (project_root / ".architecture-promotion-stage.md").exists()
    finally:
        store.close()


def test_promotion_without_projection_source_is_denied_before_authority_write(tmp_path: Path):
    lifecycle, store, project_root, architecture_path, baseline, request = _promotion_fixture(tmp_path)
    try:
        with pytest.raises(ProjectionLifecycleError) as error:
            lifecycle.promote_architecture(request)
        assert error.value.code == "PROMOTION_PROJECTION_SOURCE_REQUIRED"
        assert architecture_path.read_text(encoding="utf-8") == baseline
        assert store.get_architecture_metadata(PROJECT_ID).revision == 1
        assert not (project_root / ".architecture-promotion-receipt.json").exists()
        assert not (project_root / ".architecture-promotion-stage.md").exists()
    finally:
        store.close()


def test_promotion_context_route_is_preflighted_before_authority_write(tmp_path: Path):
    lifecycle, store, project_root, architecture_path, baseline, request = _promotion_fixture(tmp_path)
    try:
        with pytest.raises(ProjectionLifecycleError) as error:
            lifecycle.promote_architecture(
                request,
                projection_source=_source(),
                context_route_input=ContextRouteInput(project_id="foreign_project"),
            )
        assert error.value.code == "PROJECT_MISMATCH"
        assert architecture_path.read_text(encoding="utf-8") == baseline
        assert store.get_architecture_metadata(PROJECT_ID).revision == 1
        assert not (project_root / ".architecture-promotion-receipt.json").exists()
        assert not (project_root / ".architecture-promotion-stage.md").exists()
    finally:
        store.close()


def test_promotion_rejects_invalid_generated_view_destination_before_authority_write(tmp_path: Path):
    lifecycle, store, project_root, architecture_path, baseline, request = _promotion_fixture(tmp_path)
    (project_root / "MAP.md").mkdir()
    try:
        with pytest.raises(ProjectionLifecycleError) as error:
            lifecycle.promote_architecture(request, projection_source=_source())
        assert error.value.code == "GENERATED_VIEW_DESTINATION_INVALID"
        assert architecture_path.read_text(encoding="utf-8") == baseline
        assert store.get_architecture_metadata(PROJECT_ID).revision == 1
        assert not (project_root / ".architecture-promotion-receipt.json").exists()
        assert not (project_root / ".architecture-promotion-stage.md").exists()
    finally:
        store.close()


def test_production_task_main_transition_refreshes_progress_without_direct_helper_call(tmp_path: Path):
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    governance.put_plan(_record())
    coordinator = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinators.json")
    coordinator.create(_coordinator())
    execution = FileBackedExecutionStateStore(tmp_path / "executions.json")
    dispatcher = ExecutionDispatcher(
        ExecutorRegistry(),
        state_store=execution,
        origin_session_ref="session:w2",
    )
    (tmp_path / "STATUS.md").write_text("stale projection\n", encoding="utf-8")
    (tmp_path / "MAP.md").write_text("stale projection\n", encoding="utf-8")
    plan_view = MilestonePlanView(
        plan_authority=f"local-governance:{PROJECT_ID}/{PLAN_ID}",
        plan_digest="a" * 64,
        milestone_id="M3",
        entry_base="b" * 40,
        graph=MilestoneWorkItemGraph(milestone_ref="M3", work_items=["W1", "W2"], dependencies=[]),
        milestone_user_approval_satisfied=True,
    )
    try:
        service = create_task_main_control_service(
            coordinator_store=coordinator,
            execution_store=execution,
            execution_dispatcher=dispatcher,
            governance_store=governance,
        )
        service.submit_work_projection(
            profile=AF_TASK_MAIN_ROLE,
            coordinator_id="coord-w2",
            live_plan_view=plan_view,
            work_item_id="W2",
            projection=WorkSemanticProjection(
                objective="bounded W2 objective",
                bounded_scope="bounded W2 scope",
                validation_expectations=("run focused validation",),
                semantic_stop_expectations=("stop on semantic uncertainty",),
            ),
        )
        status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
        map_view = (tmp_path / "MAP.md").read_text(encoding="utf-8")
        assert "stale projection" not in status
        assert "active_coordinator_count: 1" in status
        assert "stale projection" not in map_view
        assert "AUTHORITY=no" in status
        assert "AUTHORITY=no" in map_view
    finally:
        governance.close()
        coordinator.close()
        execution.close()


def replace_source_architecture(architecture: ArchitectureStateInput) -> GovernanceProjectionInput:
    source = _source()
    return GovernanceProjectionInput(
        project_id=source.project_id,
        project_ref=source.project_ref,
        project_status=source.project_status,
        plan_records=source.plan_records,
        plan_documents=source.plan_documents,
        architecture=architecture,
        coordinator_states=source.coordinator_states,
        execution_records=source.execution_records,
    )


def replace_request(request: ArchitecturePromotionRequest, **updates: object) -> ArchitecturePromotionRequest:
    values = {
        field: getattr(request, field)
        for field in request.__dataclass_fields__
    }
    values.update(updates)
    return ArchitecturePromotionRequest(**values)
