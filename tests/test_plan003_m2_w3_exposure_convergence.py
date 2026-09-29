"""Plan #003 M2/W3 exposure and project-only convergence proofs."""

from __future__ import annotations

from pathlib import Path

from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
    SKILL_IS_AUTHORITY,
    TOOL_VISIBILITY_IS_AUTHORITY,
)
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.task_main_exposure import canonical_task_main_operations
from aota_forge.work_plane.tool_surface import (
    ROLE_DEFAULT_SURFACE_IS_SECURITY_ACL,
    ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY,
    ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY,
)

from test_plan003_m2_w2_same_session_bind import (
    _add_plan,
    _adapter,
    _inspect,
    _payload,
    _world,
)


def test_task_main_exposure_bootstrap_list_and_role_surface_converge(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    expected = set(canonical_task_main_operations())

    bootstrap = adapter.invoke("role.bootstrap", {})
    listed = adapter.invoke("operations.list", {})
    assert bootstrap["ok"] is True
    assert listed["ok"] is True

    bootstrap_ops = set(bootstrap["payload"]["AOTA_MCP"]["operations"])
    listed_ops = {entry["operation"] for entry in listed["payload"]["data"]["operations"]}
    surface_ops = set(get_tool_surface_for_role("task-main").all_capability_names())
    assert bootstrap_ops == expected == listed_ops == surface_ops
    assert {"task_main.context", "governance.project", "governance.plan"} <= expected

    for foreign_role in ("analyst", "coder", "reviewer", "project-steward"):
        foreign_ops = set(get_tool_surface_for_role(foreign_role).all_capability_names())
        assert "governance.project" not in foreign_ops
        assert "governance.plan" not in foreign_ops

    assert ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY is True
    assert ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY is True
    assert ROLE_DEFAULT_SURFACE_IS_SECURITY_ACL is False
    assert TOOL_VISIBILITY_IS_AUTHORITY is False
    assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
    assert MCP_PUBLIC_TOOL_COUNT == 1


def test_context_and_governance_help_describe_current_contract(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)

    context_help = adapter.invoke("help", {"operation": "task_main.context"})
    assert context_help["ok"] is True
    context = context_help["payload"]
    assert context["required_inputs"] == ["mode"]
    assert set(context["optional_inputs"]) == {
        "project_selector",
        "plan_selector",
        "project_ref",
        "plan_ref",
    }
    purpose = context["purpose"].lower()
    for marker in ("inspect", "bind", "switch", "same session", "trusted af", "locator"):
        assert marker in purpose
    assert context["relevant_skill_ref"] == "aota-task-main-control@1.0.0"

    for operation, skill_ref in (
        ("governance.project", "aota-project-governance@1.0.0"),
        ("governance.plan", "aota-plan-governance@1.0.0"),
    ):
        response = adapter.invoke("help", {"operation": operation})
        assert response["ok"] is True
        assert response["payload"]["operation"] == operation
        assert response["payload"]["relevant_skill_ref"] == skill_ref


def test_project_only_projection_is_truthful_and_plan_guard_does_not_infer(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    adapter = _adapter(world, origin="w3-project-only")

    bound = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert bound["ok"] is True
    assert _payload(adapter, bound)["state"] == "project_bound"

    inspected = _inspect(adapter)["payload"]
    assert inspected["state"] == "project_bound"
    assert inspected["context"]["project_id"] == "project-a"
    assert inspected["context"]["plan_id"] is None
    assert inspected["context"]["authority_binding"] is None

    governance = inspected["governance_context"]
    assert governance["CARDS"]["project"] is not None
    assert governance["CARDS"]["plans"] == []
    assert governance["CARDS"]["milestones"] == []
    route = governance["CONTEXT_ROUTE"]
    assert route["plan_id"] is None
    assert route["authority_source_kind"] is None
    assert not any(entry["kind"] == "plan_authority" for entry in route["entries"])

    project_result = adapter.invoke(
        "governance.project",
        {"mode": "inspect", "project_id": "project-a"},
    )
    assert project_result["ok"] is True, project_result.get("error")

    plan_result = adapter.invoke(
        "governance.plan",
        {"mode": "inspect", "project_id": "project-a", "plan_id": "plan_alpha"},
    )
    assert plan_result["ok"] is False
    assert plan_result["error"]["code"] == "AUTHORITY_DENIED"


def test_plan_bound_context_and_legacy_bind_aliases_remain_compatible(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    adapter = _adapter(world, origin="w3-legacy-alias")

    bound = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_ref": "project-a", "plan_ref": "plan_alpha"},
    )
    assert bound["ok"] is True
    assert _payload(adapter, bound)["state"] == "bound"

    inspected = _inspect(adapter)["payload"]
    assert inspected["context"]["project_id"] == "project-a"
    assert inspected["context"]["plan_id"] == "plan_alpha"
    route = inspected["governance_context"]["CONTEXT_ROUTE"]
    assert route["plan_id"] == "plan_alpha"
    assert sum(entry["kind"] == "plan_authority" for entry in route["entries"]) == 1


def test_base_and_progressive_skills_state_session_first_non_authority_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    base = (root / "skills" / "aota-task-main-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "ordinary unbound session may establish trusted context",
        "project_selector",
        "optional plan_selector",
        "same session/adapter alive",
        "selector or legacy ref never mints",
        "role.bootstrap",
        "task_main.context(mode=inspect)",
    ):
        assert marker in base
    assert "project_selector != authority" in base
    assert "plan_selector != authority" in base

    for skill_id in (
        "aota-task-main-governance",
        "aota-project-governance",
        "aota-plan-governance",
    ):
        text = (root / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert "pre-bound" in text
        assert "task_main.context" in text
        assert "guidance, not authority" in text or "never authority" in text

    workspace = (root / "skills" / "aota-workspace-operations" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "normally NOT required" in workspace
    assert SKILL_IS_AUTHORITY is False


def test_foreign_worker_skill_denial_is_unchanged(tmp_path: Path) -> None:
    from aota_forge.composition.worker_vertical_slice import build_worker_binding
    from aota_forge.mcp_transport import _SharedAotaMcpAdapter
    from aota_forge.work_plane.handoff import TaskHandoff

    handoff = TaskHandoff(
        work_role="analyst",
        task_kind="w3-skill-denial",
        objective="bounded analysis",
        bounded_scope="bounded scope",
        validation_expectations=("focused",),
        semantic_stop_expectations=("stop",),
    )
    binding = build_worker_binding(
        root=tmp_path,
        project_id="project-a",
        worktree_id="w3-worker",
        canonical_task_id="w3-worker-task",
        handoff=handoff,
    )
    response = _SharedAotaMcpAdapter(binding).invoke(
        "skill.open",
        {"ref": "aota-task-main-governance@1.0.0"},
    )
    assert response["ok"] is False
    assert response["error"]["code"] in {"AUTHORITY_DENIED", "FOREIGN_SKILL_DENIED"}
