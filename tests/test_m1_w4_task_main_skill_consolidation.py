"""M1/W4 task-main Skill and guidance consolidation contract."""

from __future__ import annotations

from pathlib import Path

from aota_forge.work_plane import af_roles


ROOT = Path(__file__).resolve().parents[1]


def _skill(skill_id: str) -> str:
    return (ROOT / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")


def test_task_main_has_one_eager_base_and_gov2_progressive_routes() -> None:
    eager, progressive = af_roles._ROLE_SKILL_DEFS["task-main"]

    assert eager == ["aota-task-main-control"]
    assert set(progressive) == {
        "aota-workspace-operations",
        "aota-result-hydration",
        "aota-task-main-governance",
        "aota-project-governance",
        "aota-plan-governance",
    }
    assert "aota-profile-task-orchestration" not in progressive
    assert "aota-task-lifecycle" not in progressive
    assert af_roles.TASK_MAIN_EAGER_BASE_SKILL_COUNT == 1
    assert af_roles.NORMAL_TASK_MAIN_REQUIRES_MULTI_SKILL_PRELOAD is False
    assert af_roles.COMPETING_TASK_MAIN_OPERATIONAL_CONTRACTS == 0
    assert af_roles.LEGACY_TASK_MAIN_SKILLS_IN_NORMAL_ROUTING == 0


def test_base_and_governance_skills_have_distinct_ownership() -> None:
    base = _skill("aota-task-main-control")
    governance = _skill("aota-task-main-governance")

    for marker in (
        "TASK_MAIN_BASE_SKILL_SUFFICIENT_FOR_NORMAL_HAPPY_PATH=yes",
        "BASE_SKILL_OWNS_EXACT_OPERATION_SCHEMA=no",
        "GOV2_IS_NORMAL=yes",
        "governance.project",
        "governance.plan",
        "handoff.write",
        "task.start",
        "submit_work_projection` is internal compatibility only",
    ):
        assert marker in base, marker
    for stale in ("task_main.activate_milestone", "task_main.advance_once", "task_main.context"):
        assert stale not in base, stale

    for marker in (
        "GOVERNANCE_SKILL_GOV2_NORMAL=yes",
        "GOVERNANCE_SKILL_GOV1_COMPATIBILITY=yes",
        "GOV1_IS_NORMAL_NEW_PLAN_PATH=no",
        "authority_kind=local_governance",
        "authority_kind=github_issue",
        "Never delegate governance to a project-steward child in the normal path.",
    ):
        assert marker in governance, marker
    assert "task_main.context" not in governance


def test_project_plan_and_historical_dispositions_are_explicit() -> None:
    project = _skill("aota-project-governance")
    plan = _skill("aota-plan-governance")
    profile = _skill("aota-profile-task-orchestration")
    lifecycle = _skill("aota-task-lifecycle")
    steward = _skill("aota-pcf-project-steward")
    soul = (ROOT / "aota_forge" / "roles" / "task-main.md").read_text(encoding="utf-8")
    steward_soul = (ROOT / "aota_forge" / "roles" / "project-steward.md").read_text(encoding="utf-8")

    for marker in (
        "PROJECT_GOVERNANCE_SKILL_PROGRESSIVE=yes",
        "PROJECT_MANIFEST_OWNS_PLAN_STATE=no",
        "MAP_STATUS_MANUAL_MAINTENANCE=no",
        "governance.project",
        "init",
        "inspect",
        "reconcile",
    ):
        assert marker in project, marker
    for marker in (
        "PLAN_GOVERNANCE_SKILL_PROGRESSIVE=yes",
        "GOV2_IS_NORMAL=yes",
        "NEW_PLAN_REQUIRES_GITHUB_ISSUE=no",
        "PLAN_MD_IS_AUTHORITY=yes",
        "MAP_STATUS_ARE_PROJECTIONS_ONLY=yes",
        "governance.plan",
        "amend",
        "close",
    ):
        assert marker in plan, marker
    assert "DISPOSITION=legacy/deprecated-reference" in profile
    assert "DISPOSITION=bounded-runtime-compatibility-reference" in lifecycle
    for marker in (
        "PROJECT_STEWARD_IS_NORMAL_WORKER=no",
        "PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN=yes",
        "DETERMINISTIC_FIRST_STEWARDSHIP=yes",
    ):
        assert marker in steward, marker
    assert "SOUL_OWNS_WORKFLOW=no" in soul
    assert "SOUL_OWNS_OPERATION_SCHEMA=no" in soul
    assert "PROJECT_STEWARD_IS_NORMAL_WORKER=no" in steward_soul
    assert "DETERMINISTIC_FIRST_STEWARDSHIP=yes" in steward_soul


def test_unavailable_context_operation_is_not_advertised() -> None:
    sources = [
        _skill("aota-task-main-control"),
        _skill("aota-task-main-governance"),
        _skill("aota-project-governance"),
        _skill("aota-plan-governance"),
        af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE,
    ]

    assert all("task_main.context" not in source for source in sources)
    assert all("governance.plan_migration" not in source for source in sources)
