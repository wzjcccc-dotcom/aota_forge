"""S1 M1-W1 Agent Work Role contract and invariants — bounded validation."""

from __future__ import annotations

import pathlib

import pytest

from aota_forge.core.execution.roles import (
    CANONICAL_ROLE_SET,
    CANONICAL_ROLES,
    CanonicalRole,
    validate_canonical_role,
)
from aota_forge.work_plane.roles import (
    AGENT_WORK_ROLES,
    VALID_WORK_ROLES,
    WORK_ROLES,
    WORK_ROLE_SET,
    AgentWorkRole,
    is_agent_work_role,
    parse_agent_work_role,
    validate_agent_work_role,
)


# ---------------------------------------------------------------------------
# T01 — exactly five Work Roles
# ---------------------------------------------------------------------------
def test_t01_exactly_five_work_roles() -> None:
    assert len(WORK_ROLES) == 5
    assert len(WORK_ROLE_SET) == 5
    assert len(AGENT_WORK_ROLES) == 5
    assert len(VALID_WORK_ROLES) == 5
    assert len(list(AgentWorkRole)) == 5
    # Enum names are distinct
    assert len({m.name for m in AgentWorkRole}) == 5
    assert len({m.value for m in AgentWorkRole}) == 5


# ---------------------------------------------------------------------------
# T02 — serialized values exactly
# ---------------------------------------------------------------------------
def test_t02_serialized_values_exactly() -> None:
    expected = {"task-main", "analyst", "coder", "reviewer", "project-steward"}
    assert set(WORK_ROLES) == expected
    assert WORK_ROLE_SET == expected
    # Enum values match exactly
    enum_values = {m.value for m in AgentWorkRole}
    assert enum_values == expected
    # Individual members
    assert AgentWorkRole.TASK_MAIN.value == "task-main"
    assert AgentWorkRole.ANALYST.value == "analyst"
    assert AgentWorkRole.CODER.value == "coder"
    assert AgentWorkRole.REVIEWER.value == "reviewer"
    assert AgentWorkRole.PROJECT_STEWARD.value == "project-steward"
    # Order is declared deterministic (WORK_ROLES tuple order)
    assert WORK_ROLES == ("task-main", "analyst", "coder", "reviewer", "project-steward")


# ---------------------------------------------------------------------------
# T03 — valid WorkRoles accepted
# ---------------------------------------------------------------------------
def test_t03_valid_work_roles_accepted() -> None:
    for v in WORK_ROLES:
        assert is_agent_work_role(v) is True
        assert validate_agent_work_role(v) == v
        assert AgentWorkRole.is_valid(v) is True

    # Enum members also accepted
    for member in AgentWorkRole:
        assert is_agent_work_role(member) is True
        assert validate_agent_work_role(member) == member.value
        assert AgentWorkRole.is_valid(member) is True
        assert parse_agent_work_role(member) is member
        assert parse_agent_work_role(member.value) is member


# ---------------------------------------------------------------------------
# T04 — invalid/unknown rejected fail-closed
# ---------------------------------------------------------------------------
def test_t04_invalid_unknown_rejected_fail_closed() -> None:
    # Unknown string
    for bad in ["", "Task-Main", "TASK-MAIN", "planner", "executor", "unknown", "coder "]:
        assert is_agent_work_role(bad) is False
        with pytest.raises(ValueError):
            validate_agent_work_role(bad)
        with pytest.raises((ValueError, TypeError)):
            parse_agent_work_role(bad)

    # Non-string shapes
    for bad in [None, 123, 3.14, [], {}, b"coder", object(), CanonicalRole.CODER]:
        assert is_agent_work_role(bad) is False
        with pytest.raises(TypeError):
            validate_agent_work_role(bad)

    # Unknown must not fallback to a default
    with pytest.raises(ValueError):
        validate_agent_work_role("not-a-role")
    with pytest.raises(TypeError):
        validate_agent_work_role(None)


# ---------------------------------------------------------------------------
# T05 — WorkRole type/domain distinct from CanonicalRole
# ---------------------------------------------------------------------------
def test_t05_work_role_type_domain_distinct_from_canonical_role() -> None:
    # Types are distinct
    assert AgentWorkRole is not CanonicalRole
    assert type(AgentWorkRole.CODER) is not type(CanonicalRole.CODER)
    assert not isinstance(AgentWorkRole.CODER, CanonicalRole)
    assert not isinstance(CanonicalRole.CODER, AgentWorkRole)
    # Identity distinct
    assert AgentWorkRole.CODER is not CanonicalRole.CODER  # type: ignore[comparison-overlap]
    # Enum members are not interchangeable via isinstance
    assert isinstance(AgentWorkRole.CODER, AgentWorkRole)
    assert isinstance(CanonicalRole.CODER, CanonicalRole)


# ---------------------------------------------------------------------------
# T06 — same textual value does not collapse type domains
# ---------------------------------------------------------------------------
def test_t06_same_textual_value_does_not_collapse_type_domains() -> None:
    # Serialized strings coincide but domains remain separate
    assert AgentWorkRole.CODER.value == "coder"
    assert CanonicalRole.CODER.value == "coder"
    assert AgentWorkRole.CODER.value == CanonicalRole.CODER.value
    # But typed members are not equal (AgentWorkRole is plain Enum, not str)
    assert AgentWorkRole.CODER != CanonicalRole.CODER  # type: ignore[comparison-overlap]
    assert AgentWorkRole.CODER is not CanonicalRole.CODER  # type: ignore[comparison-overlap]
    # Plain string "coder" is accepted by both validators as string domain,
    # but typed AgentWorkRole must not be usable as CanonicalRole authority
    # without explicit conversion — validate_canonical_role must reject the
    # WorkRole enum instance.
    with pytest.raises(TypeError):
        validate_canonical_role(AgentWorkRole.CODER)  # type: ignore[arg-type]
    # Conversely work-role validator rejects CanonicalRole member
    with pytest.raises(TypeError):
        validate_agent_work_role(CanonicalRole.CODER)  # type: ignore[arg-type]
    # String equality alone does not imply domain equivalence
    work_role_str = "coder"
    assert is_agent_work_role(work_role_str) is True
    assert validate_canonical_role(work_role_str) == "coder"
    # The string being valid in both string-sets does not mean implicit mapping exists
    assert work_role_str in WORK_ROLE_SET
    assert work_role_str in CANONICAL_ROLE_SET
    # Yet no API in work_plane provides mapping
    import aota_forge.work_plane.roles as wp_roles

    assert not hasattr(wp_roles, "WorkRoleMapping")
    assert not hasattr(wp_roles, "resolve_work_role_to_canonical_role")
    assert not hasattr(wp_roles, "to_canonical_role")


# ---------------------------------------------------------------------------
# T07 — task-main is valid WorkRole
# ---------------------------------------------------------------------------
def test_t07_task_main_is_valid_work_role() -> None:
    assert is_agent_work_role("task-main") is True
    assert validate_agent_work_role("task-main") == "task-main"
    assert AgentWorkRole.TASK_MAIN.value == "task-main"
    assert parse_agent_work_role("task-main") is AgentWorkRole.TASK_MAIN
    assert is_agent_work_role(AgentWorkRole.TASK_MAIN) is True


# ---------------------------------------------------------------------------
# T08 — task-main has no automatic CanonicalRole behavior
# ---------------------------------------------------------------------------
def test_t08_task_main_has_no_automatic_canonical_role_behavior() -> None:
    # task-main is not a canonical execution role
    assert "task-main" not in CANONICAL_ROLE_SET
    assert not CanonicalRole.is_valid("task-main")
    with pytest.raises(ValueError):
        validate_canonical_role("task-main")
    # No mapping attribute/function on WorkRole
    import aota_forge.work_plane.roles as wp_roles

    for attr in [
        "TASK_MAIN_CANONICAL",
        "DEFAULT_CANONICAL_ROLE",
        "WORK_ROLE_TO_CANONICAL",
        "WorkRoleMapping",
        "resolve_work_role_to_canonical_role",
        "to_canonical",
        "as_canonical_role",
    ]:
        assert not hasattr(wp_roles, attr)
    # AgentWorkRole.TASK_MAIN must not expose canonical conversion
    assert not hasattr(AgentWorkRole.TASK_MAIN, "to_canonical_role")
    assert not hasattr(AgentWorkRole.TASK_MAIN, "canonical_role")


# ---------------------------------------------------------------------------
# T09 — no sixth executor WorkRole
# ---------------------------------------------------------------------------
def test_t09_no_sixth_executor_work_role() -> None:
    assert "executor" not in WORK_ROLE_SET
    assert is_agent_work_role("executor") is False
    with pytest.raises(ValueError):
        validate_agent_work_role("executor")
    # Enum has no EXECUTOR member
    assert not hasattr(AgentWorkRole, "EXECUTOR")
    # Ensure exactly five, no extra names containing executor
    names = {m.name for m in AgentWorkRole}
    assert "EXECUTOR" not in names
    values = {m.value for m in AgentWorkRole}
    assert "executor" not in values


# ---------------------------------------------------------------------------
# T10 — existing CanonicalRole set remains
# ---------------------------------------------------------------------------
def test_t10_existing_canonical_role_set_remains() -> None:
    assert CANONICAL_ROLES == ("planner", "coder", "reviewer", "steward", "executor")
    assert CANONICAL_ROLE_SET == {"planner", "coder", "reviewer", "steward", "executor"}
    assert {m.value for m in CanonicalRole} == {
        "planner",
        "coder",
        "reviewer",
        "steward",
        "executor",
    }
    # Canonical validators still behave correctly
    for v in CANONICAL_ROLES:
        assert validate_canonical_role(v) == v
    with pytest.raises(ValueError):
        validate_canonical_role("task-main")
    with pytest.raises(ValueError):
        validate_canonical_role("analyst")


# ---------------------------------------------------------------------------
# T11 — no Hermes dependency
# ---------------------------------------------------------------------------
def test_t11_no_hermes_dependency() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/roles.py").read_text(encoding="utf-8")
    lower = src.lower()
    # No hermes imports — check import lines
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    # No hermes-private identifiers in code (outside docstring explanation allowed, but we check code)
    assert "hermes_profile" not in lower
    assert "processregistry" not in lower
    assert "hermeshostclient" not in lower
    assert "hermesadapter" not in lower
    # Overall file should not import hermes runtime
    assert "from aota_forge.adapters.hermes" not in lower
    assert "import hermes" not in lower

    # Also assert core.execution.roles does not import work_plane (dependency direction)
    core_src = (repo_root / "aota_forge/core/execution/roles.py").read_text(encoding="utf-8")
    assert "work_plane" not in core_src
    assert "work_role" not in core_src.lower()
    assert "AgentWorkRole" not in core_src


# ---------------------------------------------------------------------------
# T12 — deterministic serialization / roundtrip
# ---------------------------------------------------------------------------
def test_t12_deterministic_serialization_roundtrip() -> None:
    # value is canonical serialized form
    for member in AgentWorkRole:
        serialized = member.value
        assert isinstance(serialized, str)
        # Roundtrip via parse
        assert parse_agent_work_role(serialized) is member
        assert validate_agent_work_role(serialized) == serialized
        # Direct Enum construction via value is deterministic
        assert AgentWorkRole(serialized) is member

    # Sorted canonical set is deterministic
    assert sorted(WORK_ROLE_SET) == sorted(["task-main", "analyst", "coder", "reviewer", "project-steward"])
    # WORK_ROLES tuple order is deterministic and stable across imports
    from aota_forge.work_plane.roles import WORK_ROLES as WR2

    assert WR2 == WORK_ROLES
    # Duplicate impossible — Enum @unique and set size guarantee
    assert validate_agent_work_role("coder") == "coder"
    # Duplicates in Enum definition would have raised at import time; verify uniqueness
    assert len(set(WORK_ROLES)) == 5


# ---------------------------------------------------------------------------
# Additional: no W2/W3/W4 leakage
# ---------------------------------------------------------------------------
def test_no_w2_mapping_leakage() -> None:
    import aota_forge.work_plane.roles as wp_roles
    import aota_forge.work_plane as wp

    forbidden_roles = [
        "WorkRoleMapping",
        "resolve_work_role_to_canonical_role",
        "resolve_work_role",
        "to_canonical_role",
        "to_execution_role",
        "default_canonical_role",
        "role_ranking",
        "heuristic",
        "task_kind",
        "executor_selection",
    ]
    for name in forbidden_roles:
        assert not hasattr(wp_roles, name), f"forbidden W2 leakage: {name}"
    # Package-level export of W2 mapping is intentionally allowed after W4 convergence
    # per S1 plan §5: W4 may perform minimal public export wiring for accepted
    # WorkRole mapping, TaskHandoff, and compiler.
    package_strict_forbidden = [
        "role_ranking",
        "heuristic",
        "executor_selection",
    ]
    for name in package_strict_forbidden:
        assert not hasattr(wp, name), f"forbidden W2 leakage at package: {name}"

    # file content should not contain mapping semantics
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/roles.py").read_text(encoding="utf-8").lower()
    assert "workrolemapping" not in src
    assert "resolve_work_role" not in src
    # canonicalrole may appear in docstring but must not be part of mapping implementation
    assert "workrolemapping" not in src
    # Ensure no TaskHandoff / ExecutionPackage leakage
    assert "taskhandoff" not in src
    assert "executionpackage" not in src
    assert "intent_fingerprint" not in src


def test_work_role_is_not_authority() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/roles.py").read_text(encoding="utf-8")
    lower = src.lower()
    # must not implement authority semantics — no code granting filesystem/tool authority
    # Check that no Python code outside comments/docstrings introduces authority variables
    code_lines = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # toggle docstring state (simple)
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # skip comment lines
        if stripped.startswith("#"):
            continue
        code_lines.append(stripped.lower())
    code = "\n".join(code_lines)
    assert "is_authority" not in code
    # Ensure no authority-defining assignment like "= True" for authority
    assert "authority" not in code or "no authority" in lower
