"""Cross-W proof for Plan-aware routing and trusted projection wiring."""

from __future__ import annotations

import os
from pathlib import Path

from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.runtime.task_main.control import AF_TASK_MAIN_ROLE
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    default_coordinator_id,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from tests.test_af57_m3_ac10_semantic_steward_bootstrap import (
    _EnvGuard,
    _runtime_config_json,
)
from tests.test_plan005_m1_w3_terminal_projection_convergence import (
    MILESTONE,
    PLAN_ID,
    PROJECT_ID,
    _world,
)
from tests.test_plan005_m1_w1_coordinator_routing import ENTRY_BASE


def test_trusted_bootstrap_routes_and_projects_from_one_authority(tmp_path: Path) -> None:
    world = _world(tmp_path)
    store = world["store"]
    root = world["governance_root"]
    assert store is not None and root is not None
    record = store.get_plan(PROJECT_ID, PLAN_ID)
    assert record is not None
    authority = record.authority
    session_ref = "20261008_plan005_m1_w1_w3_integration"
    view = MilestonePlanView(
        plan_authority=authority.authority_ref,
        plan_digest=authority.source_digest,
        plan_source_revision=str(authority.source_revision),
        milestone_id=MILESTONE,
        entry_base=ENTRY_BASE,
        graph=MilestoneWorkItemGraph(
            milestone_ref=MILESTONE,
            work_items=["W1"],
            dependencies=[],
        ),
        milestone_user_approval_satisfied=True,
    )
    governance_base = tmp_path / "plans"
    governance_store_path = tmp_path / ".aota" / "governance.sqlite3"
    project_root = Path(world["project_root"])
    coordinator_path = project_root / ".aota" / "coordinator.json"
    execution_path = project_root / ".aota" / "execution.json"
    runtime_config_path = tmp_path / "runtime.json"
    _runtime_config_json(runtime_config_path)
    bootstrap_path = write_bootstrap_file(
        worktree_root=project_root,
        project_id=PROJECT_ID,
        worktree_id="plan005-w1-w3-integration",
        coordinator_store_path=coordinator_path,
        execution_store_path=execution_path,
        runtime_config_path=runtime_config_path,
        origin_task_main_session_ref=session_ref,
        live_plan_view=view,
        next_milestone_view=None,
        plan_id=PLAN_ID,
        plan_authority_binding=authority,
        governance_base=governance_base,
        governance_store_path=governance_store_path,
    )
    expected_id = default_coordinator_id(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        milestone_id=MILESTONE,
    )
    binding = None
    try:
        with _EnvGuard():
            os.environ[BOOTSTRAP_ENV_ROOT] = str(project_root)
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bootstrap_path)
            binding = try_build_task_main_binding()

        assert binding is not None
        assert binding.plan_authority_binding == authority
        context = binding.trusted_task_main_context
        assert context is not None
        assert context.coordinator_id == expected_id
        service = context.control_service
        assert service.resolve_coordinator_id(
            project_id=PROJECT_ID,
            live_plan_view=view,
            coordinator_id=None,
        ) == expected_id

        handle = service.activate_milestone(
            profile=AF_TASK_MAIN_ROLE,
            plan_view=view,
            origin_task_main_session_ref=session_ref,
            executor_id="integration-test-executor",
            project_id=PROJECT_ID,
        )

        assert handle.coordinator_id == expected_id
        assert service._coord_store.get(expected_id).context_binding.plan_binding == authority
        governance_root = Path(root.root_path)
        assert (governance_root / "MAP.md").is_file()
        status = (governance_root / "STATUS.md").read_text(encoding="utf-8")
        assert PLAN_ID in status
        assert "active_coordinator_count: 1" in status
        assert project_root.joinpath(".aota", "project.yaml").is_file()
    finally:
        if binding is not None:
            binding.trusted_task_main_context.control_service._coord_store.close()
            binding.trusted_task_main_context.control_service._exec_store.close()
        store.close()
