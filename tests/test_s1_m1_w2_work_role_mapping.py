"""S1 M1-W2 Work Role to Canonical Role mapping contract — bounded validation."""

from __future__ import annotations

import pathlib
import pytest

from aota_forge.core.execution.roles import (
    CANONICAL_ROLE_SET,
    CANONICAL_ROLES,
    CanonicalRole,
    RoleMapping,
    RoleMappingNotFoundError,
    validate_canonical_role,
)
from aota_forge.work_plane.mapping import (
    WORK_ROLE_TO_CANONICAL_ROLE,
    WorkRoleMappingError,
    resolve_work_role_to_canonical_role,
)
from aota_forge.work_plane.roles import (
    WORK_ROLES,
    WORK_ROLE_SET,
    AgentWorkRole,
    is_agent_work_role,
    parse_agent_work_role,
    validate_agent_work_role,
)


# ---------------------------------------------------------------------------
# T01 — analyst resolves exactly to CanonicalRole.PLANNER
# ---------------------------------------------------------------------------
def test_t01_analyst_resolves_to_planner() -> None:
    # Enum member
    res_enum = resolve_work_role_to_canonical_role(AgentWorkRole.ANALYST)
    assert res_enum is CanonicalRole.PLANNER
    assert isinstance(res_enum, CanonicalRole)
    assert res_enum == "planner"

    # String value
    res_str = resolve_work_role_to_canonical_role("analyst")
    assert res_str is CanonicalRole.PLANNER
    assert isinstance(res_str, CanonicalRole)
    assert res_str == "planner"


# ---------------------------------------------------------------------------
# T02 — coder resolves exactly to CanonicalRole.CODER
# ---------------------------------------------------------------------------
def test_t02_coder_resolves_to_coder() -> None:
    # Enum member
    res_enum = resolve_work_role_to_canonical_role(AgentWorkRole.CODER)
    assert res_enum is CanonicalRole.CODER
    assert isinstance(res_enum, CanonicalRole)
    assert res_enum == "coder"

    # String value
    res_str = resolve_work_role_to_canonical_role("coder")
    assert res_str is CanonicalRole.CODER
    assert isinstance(res_str, CanonicalRole)
    assert res_str == "coder"


# ---------------------------------------------------------------------------
# T03 — reviewer resolves exactly to CanonicalRole.REVIEWER
# ---------------------------------------------------------------------------
def test_t03_reviewer_resolves_to_reviewer() -> None:
    # Enum member
    res_enum = resolve_work_role_to_canonical_role(AgentWorkRole.REVIEWER)
    assert res_enum is CanonicalRole.REVIEWER
    assert isinstance(res_enum, CanonicalRole)
    assert res_enum == "reviewer"

    # String value
    res_str = resolve_work_role_to_canonical_role("reviewer")
    assert res_str is CanonicalRole.REVIEWER
    assert isinstance(res_str, CanonicalRole)
    assert res_str == "reviewer"


# ---------------------------------------------------------------------------
# T04 — project-steward resolves exactly to CanonicalRole.STEWARD
# ---------------------------------------------------------------------------
def test_t04_project_steward_resolves_to_steward() -> None:
    # Enum member
    res_enum = resolve_work_role_to_canonical_role(AgentWorkRole.PROJECT_STEWARD)
    assert res_enum is CanonicalRole.STEWARD
    assert isinstance(res_enum, CanonicalRole)
    assert res_enum == "steward"

    # String value
    res_str = resolve_work_role_to_canonical_role("project-steward")
    assert res_str is CanonicalRole.STEWARD
    assert isinstance(res_str, CanonicalRole)
    assert res_str == "steward"


# ---------------------------------------------------------------------------
# T05 — task-main has no default CanonicalRole mapping (raises)
# ---------------------------------------------------------------------------
def test_t05_task_main_has_no_default_canonical_role_mapping() -> None:
    # Enum member raises WorkRoleMappingError / ValueError
    with pytest.raises(WorkRoleMappingError) as exc_info_enum:
        resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)
    assert "task-main" in str(exc_info_enum.value)

    # String value raises WorkRoleMappingError / ValueError
    with pytest.raises(WorkRoleMappingError) as exc_info_str:
        resolve_work_role_to_canonical_role("task-main")
    assert "task-main" in str(exc_info_str.value)

    # Fail closed: must not return None, planner, or executor silently
    try:
        res = resolve_work_role_to_canonical_role("task-main")
        pytest.fail(f"Expected WorkRoleMappingError, got {res!r}")
    except WorkRoleMappingError:
        pass


# ---------------------------------------------------------------------------
# T06 — unsupported/invalid WorkRole fails closed
# ---------------------------------------------------------------------------
def test_t06_unsupported_invalid_work_role_fails_closed() -> None:
    # Unknown string
    for bad_str in ["", "unknown", "TASK-MAIN", "Planner", "CODER", "coder ", "steward"]:
        with pytest.raises(ValueError):
            resolve_work_role_to_canonical_role(bad_str)

    # Non-string shapes
    for bad_shape in [None, 123, 4.56, [], {}, b"coder", object()]:
        with pytest.raises(TypeError):
            resolve_work_role_to_canonical_role(bad_shape)

    # Foreign enum members (CanonicalRole) fail closed via TypeError
    for canonical in CanonicalRole:
        with pytest.raises(TypeError):
            resolve_work_role_to_canonical_role(canonical)


# ---------------------------------------------------------------------------
# T07 — CanonicalRole.EXECUTOR is never produced by v0 mapping
# ---------------------------------------------------------------------------
def test_t07_canonical_role_executor_never_produced() -> None:
    # Exhaustively check all valid WorkRoles
    for role in WORK_ROLES:
        if role == "task-main":
            with pytest.raises(WorkRoleMappingError):
                resolve_work_role_to_canonical_role(role)
        else:
            mapped = resolve_work_role_to_canonical_role(role)
            assert mapped is not CanonicalRole.EXECUTOR
            assert mapped != "executor"

    for member in AgentWorkRole:
        if member is AgentWorkRole.TASK_MAIN:
            with pytest.raises(WorkRoleMappingError):
                resolve_work_role_to_canonical_role(member)
        else:
            mapped = resolve_work_role_to_canonical_role(member)
            assert mapped is not CanonicalRole.EXECUTOR
            assert mapped != "executor"

    # Also verify mapping table values do not contain EXECUTOR
    assert CanonicalRole.EXECUTOR not in WORK_ROLE_TO_CANONICAL_ROLE.values()
    assert "executor" not in [r.value for r in WORK_ROLE_TO_CANONICAL_ROLE.values()]


# ---------------------------------------------------------------------------
# T08 — no heuristic fallback
# ---------------------------------------------------------------------------
def test_t08_no_heuristic_fallback() -> None:
    # Close strings must NOT fallback or fuzzy match
    near_matches = [
        "analyse",
        "analysis",
        "analystic",
        "code",
        "coding",
        "review",
        "reviewing",
        "steward",
        "stewardship",
        "task",
        "main",
        "task_main",
        "project_steward",
    ]
    for bad in near_matches:
        with pytest.raises(ValueError):
            resolve_work_role_to_canonical_role(bad)


# ---------------------------------------------------------------------------
# T09 — deterministic repeated resolution
# ---------------------------------------------------------------------------
def test_t09_deterministic_repeated_resolution() -> None:
    for _ in range(10):
        assert resolve_work_role_to_canonical_role(AgentWorkRole.ANALYST) is CanonicalRole.PLANNER
        assert resolve_work_role_to_canonical_role("analyst") is CanonicalRole.PLANNER
        assert resolve_work_role_to_canonical_role(AgentWorkRole.CODER) is CanonicalRole.CODER
        assert resolve_work_role_to_canonical_role("coder") is CanonicalRole.CODER
        assert resolve_work_role_to_canonical_role(AgentWorkRole.REVIEWER) is CanonicalRole.REVIEWER
        assert resolve_work_role_to_canonical_role("reviewer") is CanonicalRole.REVIEWER
        assert resolve_work_role_to_canonical_role(AgentWorkRole.PROJECT_STEWARD) is CanonicalRole.STEWARD
        assert resolve_work_role_to_canonical_role("project-steward") is CanonicalRole.STEWARD

        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)
        with pytest.raises(WorkRoleMappingError):
            resolve_work_role_to_canonical_role("task-main")


# ---------------------------------------------------------------------------
# T10 — WorkRole type remains distinct from CanonicalRole
# ---------------------------------------------------------------------------
def test_t10_work_role_type_remains_distinct_from_canonical_role() -> None:
    assert AgentWorkRole is not CanonicalRole
    assert type(AgentWorkRole.CODER) is not type(CanonicalRole.CODER)
    assert not isinstance(AgentWorkRole.CODER, CanonicalRole)
    assert not isinstance(CanonicalRole.CODER, AgentWorkRole)

    # Resolution takes AgentWorkRole (or str) and returns CanonicalRole
    mapped = resolve_work_role_to_canonical_role(AgentWorkRole.CODER)
    assert isinstance(mapped, CanonicalRole)
    assert not isinstance(mapped, AgentWorkRole)
    assert mapped is CanonicalRole.CODER
    assert mapped is not AgentWorkRole.CODER  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# T11 — existing CanonicalRole set unchanged
# ---------------------------------------------------------------------------
def test_t11_existing_canonical_role_set_unchanged() -> None:
    assert CANONICAL_ROLES == ("planner", "coder", "reviewer", "steward", "executor")
    assert CANONICAL_ROLE_SET == {"planner", "coder", "reviewer", "steward", "executor"}
    assert {m.value for m in CanonicalRole} == {
        "planner",
        "coder",
        "reviewer",
        "steward",
        "executor",
    }


# ---------------------------------------------------------------------------
# T12 — existing RoleMapping contract unchanged
# ---------------------------------------------------------------------------
def test_t12_existing_role_mapping_contract_unchanged() -> None:
    # RoleMapping works with CanonicalRole
    rm = RoleMapping.create(
        "test_executor",
        {
            "planner": "hermes-planner-profile",
            "coder": "hermes-coder-profile",
            "reviewer": "hermes-reviewer-profile",
            "steward": "hermes-steward-profile",
            "executor": "hermes-default-profile",
        },
    )
    assert rm.get_target_role(CanonicalRole.PLANNER) == "hermes-planner-profile"
    assert rm.get_target_role(CanonicalRole.CODER) == "hermes-coder-profile"
    assert rm.get_target_role(CanonicalRole.REVIEWER) == "hermes-reviewer-profile"
    assert rm.get_target_role(CanonicalRole.STEWARD) == "hermes-steward-profile"
    assert rm.get_target_role(CanonicalRole.EXECUTOR) == "hermes-default-profile"

    # Composition: AgentWorkRole -> CanonicalRole -> RoleMapping.get_target_role
    canonical = resolve_work_role_to_canonical_role(AgentWorkRole.CODER)
    assert rm.get_target_role(canonical) == "hermes-coder-profile"

    canonical_analyst = resolve_work_role_to_canonical_role(AgentWorkRole.ANALYST)
    assert canonical_analyst is CanonicalRole.PLANNER
    assert rm.get_target_role(canonical_analyst) == "hermes-planner-profile"

    # But RoleMapping must NOT directly accept AgentWorkRole
    with pytest.raises(TypeError):
        rm.get_target_role(AgentWorkRole.CODER)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        rm.get_target_role(AgentWorkRole.ANALYST)  # type: ignore[arg-type]

    # RoleMapping does not accept "task-main" or "analyst" directly as canonical role strings
    with pytest.raises(ValueError):
        rm.get_target_role("task-main")
    with pytest.raises(ValueError):
        rm.get_target_role("analyst")
    with pytest.raises(ValueError):
        rm.get_target_role("project-steward")


# ---------------------------------------------------------------------------
# T13 — zero Hermes imports
# ---------------------------------------------------------------------------
def test_t13_zero_hermes_imports() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    mapping_src = (repo_root / "aota_forge/work_plane/mapping.py").read_text(encoding="utf-8")

    # Check import lines
    for line in mapping_src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped, f"Found hermes import: {line}"

    # Check body for hermes references
    lower = mapping_src.lower()
    assert "hermes_profile" not in lower
    assert "processregistry" not in lower
    assert "hermeshostclient" not in lower
    assert "hermesadapter" not in lower


# ---------------------------------------------------------------------------
# T14 — core execution does not import Work Plane mapping
# ---------------------------------------------------------------------------
def test_t14_core_execution_does_not_import_work_plane() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    core_exec_dir = repo_root / "aota_forge/core/execution"
    for py_file in core_exec_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "work_plane" not in content, f"{py_file.name} imports work_plane"
        assert "resolve_work_role" not in content, f"{py_file.name} references resolve_work_role"
        assert "AgentWorkRole" not in content, f"{py_file.name} references AgentWorkRole"
