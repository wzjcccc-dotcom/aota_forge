from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.runtime.task_main.context_binding import (
    DurableTaskMainContextBinding,
)
from aota_forge.runtime.task_main.control import (
    AF_TASK_MAIN_ROLE,
    TaskMainControlService,
)
from aota_forge.runtime.task_main.coordinator import (
    CoordinatorBindingError,
    MilestonePlanView,
    default_coordinator_id,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
)
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
)
from aota_forge.composition.task_main_host_bootstrap import (
    _current_local_plan_route_eligible,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.progression import MilestoneWorkItemGraph


PROJECT_ID = "aota_forge"
MILESTONE_ID = "M1"
ORIGIN_SESSION = "20260907_taskmain_m3w1_disposable"
ENTRY_BASE = "6f6745dd0374c91cc40f9ab5a09b103da044091d"


def _plan_binding(
    plan_id: str,
    *,
    revision: str = "R2",
    digest: str | None = None,
) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(PROJECT_ID, plan_id),
        source_revision=revision,
        source_digest=digest or ("a" if plan_id.endswith("a") else "b") * 64,
    )


def _context(binding: PlanAuthorityBinding) -> DurableTaskMainContextBinding:
    return DurableTaskMainContextBinding(
        project_id=PROJECT_ID,
        project_fingerprint="c" * 64,
        plan_binding=binding,
        origin_task_main_session_ref=ORIGIN_SESSION,
    )


def _view(
    binding: PlanAuthorityBinding,
    *,
    revision: str | None = None,
    digest: str | None = None,
) -> MilestonePlanView:
    return MilestonePlanView(
        plan_authority=binding.authority_ref,
        plan_digest=digest or binding.source_digest or "a" * 64,
        plan_source_revision=(
            revision
            if revision is not None
            else str(binding.source_revision)
            if binding.source_revision is not None
            else None
        ),
        milestone_id=MILESTONE_ID,
        entry_base=ENTRY_BASE,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE_ID,
            work_items=["W1"],
            dependencies=[],
        ),
        milestone_user_approval_satisfied=True,
    )


def _service(
    tmp_path: Path,
    binding: PlanAuthorityBinding,
    *,
    eligible: bool = True,
) -> tuple[TaskMainControlService, FileBackedTaskMainCoordinatorStore, MilestonePlanView]:
    context = _context(binding)
    coordinator_store = FileBackedTaskMainCoordinatorStore(
        tmp_path / f"{binding.plan_id}.coordinator.json"
    )
    execution_store = FileBackedExecutionStateStore(
        tmp_path / f"{binding.plan_id}.execution.json"
    )
    service = TaskMainControlService(
        coordinator_store=coordinator_store,
        execution_store=execution_store,
        execution_dispatcher=ExecutionDispatcher(
            registry=ExecutorRegistry(),
            state_store=execution_store,
        ),
        context_binding=context,
        current_plan_eligibility_resolver=lambda _project, _view, _context: eligible,
    )
    return service, coordinator_store, _view(binding)


def _state(
    *,
    coordinator_id: str,
    binding: PlanAuthorityBinding | None,
    plan_authority: str,
    digest: str,
    revision: str,
    status: CoordinatorStatus = CoordinatorStatus.ACTIVE,
) -> TaskMainCoordinatorState:
    return TaskMainCoordinatorState(
        coordinator_id=coordinator_id,
        plan_authority=plan_authority,
        plan_digest=digest,
        plan_source_revision=revision,
        milestone_id=MILESTONE_ID,
        entry_base=ENTRY_BASE,
        origin_task_main_session_ref=ORIGIN_SESSION,
        project_id=PROJECT_ID,
        executor_id="test-executor",
        work_items=("W1",),
        status=status,
        wi_status={"W1": "PENDING"},
        context_binding=_context(binding) if binding is not None else None,
    )


def test_same_milestone_in_different_plans_creates_distinct_coordinator_ids(
    tmp_path: Path,
) -> None:
    binding_a = _plan_binding("plan_route_a")
    binding_b = _plan_binding("plan_route_b")
    service_a, _, view_a = _service(tmp_path, binding_a)
    service_b, _, view_b = _service(tmp_path, binding_b)

    handle_a = service_a.activate_milestone(
        profile=AF_TASK_MAIN_ROLE,
        plan_view=view_a,
        origin_task_main_session_ref=ORIGIN_SESSION,
        executor_id="test-executor",
        project_id=PROJECT_ID,
    )
    handle_b = service_b.activate_milestone(
        profile=AF_TASK_MAIN_ROLE,
        plan_view=view_b,
        origin_task_main_session_ref=ORIGIN_SESSION,
        executor_id="test-executor",
        project_id=PROJECT_ID,
    )
    id_a = handle_a.coordinator_id
    id_b = handle_b.coordinator_id

    assert id_a == default_coordinator_id(
        project_id=PROJECT_ID, plan_id="plan_route_a", milestone_id=MILESTONE_ID
    )
    assert id_a != id_b
    assert handle_a.state.context_binding.plan_binding.plan_id == "plan_route_a"
    assert handle_b.state.context_binding.plan_binding.plan_id == "plan_route_b"


def test_new_plan_does_not_select_prior_plan_legacy_coordinator(tmp_path: Path) -> None:
    binding = _plan_binding("plan_route_current")
    service, store, view = _service(tmp_path, binding)
    legacy_id = f"{PROJECT_ID}:{MILESTONE_ID}"
    legacy_binding = _plan_binding("plan_route_prior", digest="d" * 64)
    legacy = _state(
        coordinator_id=legacy_id,
        binding=None,
        plan_authority=legacy_binding.authority_ref,
        digest=legacy_binding.source_digest or "d" * 64,
        revision="R1",
    )
    store.create(legacy)

    selected = service.discover_matching_coordinator_id(
        project_id=PROJECT_ID,
        live_plan_view=view,
        coordinator_id=legacy_id,
    )

    assert selected == default_coordinator_id(
        project_id=PROJECT_ID,
        plan_id=binding.plan_id,
        milestone_id=MILESTONE_ID,
    )
    assert selected != legacy_id
    assert store.get(legacy_id) is not None


def test_retired_current_plan_is_not_auto_routable_even_with_active_record(
    tmp_path: Path,
) -> None:
    binding = _plan_binding("plan_route_retired")
    context = _context(binding)
    coordinator_id = default_coordinator_id(
        project_id=PROJECT_ID,
        plan_id=binding.plan_id,
        milestone_id=MILESTONE_ID,
    )
    store = FileBackedTaskMainCoordinatorStore(tmp_path / "retired.json")
    store.create(
        _state(
            coordinator_id=coordinator_id,
            binding=binding,
            plan_authority=binding.authority_ref,
            digest=binding.source_digest or "a" * 64,
            revision=str(binding.source_revision),
        )
    )
    execution_store = FileBackedExecutionStateStore(tmp_path / "retired.exec.json")
    service = TaskMainControlService(
        coordinator_store=store,
        execution_store=execution_store,
        execution_dispatcher=ExecutionDispatcher(
            registry=ExecutorRegistry(), state_store=execution_store
        ),
        context_binding=context,
        current_plan_eligibility_resolver=lambda _project, _view, _context: False,
    )

    with pytest.raises(CoordinatorBindingError, match="lifecycle/source"):
        service.discover_matching_coordinator_id(
            project_id=PROJECT_ID,
            live_plan_view=_view(binding),
            coordinator_id=coordinator_id,
        )
    assert store.get(coordinator_id).status == CoordinatorStatus.ACTIVE


def test_historical_coordinator_remains_readable(tmp_path: Path) -> None:
    binding = _plan_binding("plan_route_history")
    service, store, view = _service(tmp_path, binding)
    legacy_id = f"{PROJECT_ID}:{MILESTONE_ID}"
    state = _state(
        coordinator_id=legacy_id,
        binding=None,
        plan_authority=binding.authority_ref,
        digest=binding.source_digest or "a" * 64,
        revision=str(binding.source_revision),
    )
    stored = store.create(state)

    assert store.get(legacy_id) == stored
    assert [item.coordinator_id for item in store.list_all()] == [legacy_id]
    assert service.discover_matching_coordinator_id(
        project_id=PROJECT_ID, live_plan_view=view, coordinator_id=legacy_id
    ) != legacy_id


def test_wrong_live_plan_authority_binding_fails_closed(tmp_path: Path) -> None:
    current = _plan_binding("plan_route_expected")
    wrong = _plan_binding("plan_route_wrong")
    service, _, _ = _service(tmp_path, current)

    with pytest.raises(CoordinatorBindingError, match="PlanAuthorityBinding"):
        service.discover_matching_coordinator_id(
            project_id=PROJECT_ID,
            live_plan_view=_view(wrong),
            coordinator_id=None,
        )


@pytest.mark.parametrize(
    ("revision", "digest"),
    (("R3", None), (None, "e" * 64)),
)
def test_stale_live_source_revision_or_digest_fails_closed(
    tmp_path: Path,
    revision: str | None,
    digest: str | None,
) -> None:
    binding = _plan_binding("plan_route_stale")
    service, _, _ = _service(tmp_path, binding)

    with pytest.raises(CoordinatorBindingError, match="PlanAuthorityBinding"):
        service.discover_matching_coordinator_id(
            project_id=PROJECT_ID,
            live_plan_view=_view(binding, revision=revision, digest=digest),
            coordinator_id=None,
        )


def test_plan002_healthy_current_plan_routing_remains_compatible(
    tmp_path: Path,
) -> None:
    binding = _plan_binding("plan_002_session_first_context_activation")
    service, store, view = _service(tmp_path, binding)
    coordinator_id = default_coordinator_id(
        project_id=PROJECT_ID,
        plan_id=binding.plan_id,
        milestone_id=MILESTONE_ID,
    )
    store.create(
        _state(
            coordinator_id=coordinator_id,
            binding=binding,
            plan_authority=binding.authority_ref,
            digest=binding.source_digest or "a" * 64,
            revision=str(binding.source_revision),
        )
    )

    assert service.discover_matching_coordinator_id(
        project_id=PROJECT_ID,
        live_plan_view=view,
        coordinator_id=None,
    ) == coordinator_id


def test_current_local_lifecycle_and_source_are_rechecked_before_routing(
    tmp_path: Path,
) -> None:
    plan_id = "plan_route_local_source"
    body = "# Plan route source\n\nA bounded local plan source.\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    binding = _plan_binding(plan_id, revision=digest, digest=digest)
    governance_base = tmp_path / "governance"
    plan_directory = governance_base / PROJECT_ID / plan_id
    plan_directory.mkdir(parents=True)
    plan_document = plan_directory / "plan.md"
    plan_document.write_text(body, encoding="utf-8")
    store_path = tmp_path / "governance.sqlite3"
    governance = SQLiteProjectGovernanceStore(store_path)
    governance.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=plan_id,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=binding,
        )
    )
    governance.close()
    context = _context(binding)
    view = _view(binding)

    assert _current_local_plan_route_eligible(
        project_id=PROJECT_ID,
        live_plan_view=view,
        context_binding=context,
        trusted_plan_binding=binding,
        governance_base=governance_base,
        governance_store_path=store_path,
    )

    governance = SQLiteProjectGovernanceStore(store_path)
    current = governance.get_plan(PROJECT_ID, plan_id)
    governance.compare_and_swap_plan(
        PROJECT_ID,
        plan_id,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_RETIRED,
    )
    governance.close()
    assert not _current_local_plan_route_eligible(
        project_id=PROJECT_ID,
        live_plan_view=view,
        context_binding=context,
        trusted_plan_binding=binding,
        governance_base=governance_base,
        governance_store_path=store_path,
    )

    governance = SQLiteProjectGovernanceStore(store_path)
    current = governance.get_plan(PROJECT_ID, plan_id)
    governance.compare_and_swap_plan(
        PROJECT_ID,
        plan_id,
        current.revision,
        lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
    )
    governance.close()
    plan_document.write_text(body + "\nSource changed after binding.\n", encoding="utf-8")
    assert not _current_local_plan_route_eligible(
        project_id=PROJECT_ID,
        live_plan_view=view,
        context_binding=context,
        trusted_plan_binding=binding,
        governance_base=governance_base,
        governance_store_path=store_path,
    )


def test_planless_legacy_default_remains_compatible(tmp_path: Path) -> None:
    binding = _plan_binding("plan_route_legacy_compat")
    view = _view(binding)
    execution_store = FileBackedExecutionStateStore(tmp_path / "legacy.exec.json")
    store = FileBackedTaskMainCoordinatorStore(tmp_path / "legacy.compat.json")
    service = TaskMainControlService(
        coordinator_store=store,
        execution_store=execution_store,
        execution_dispatcher=ExecutionDispatcher(
            registry=ExecutorRegistry(), state_store=execution_store
        ),
    )

    assert service.default_coordinator_id(
        project_id=PROJECT_ID, live_plan_view=view
    ) == f"{PROJECT_ID}:{MILESTONE_ID}"
