"""S1 M2-W1 Lifecycle / Execution-Time Work Role Binding / SOUL Boundary."""

from __future__ import annotations

import hashlib
import pathlib
import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.roles import CanonicalRole
from aota_forge.work_plane.roles import AgentWorkRole

# Direct concrete-module imports — avoid aggregator per parallel discipline
import aota_forge.work_plane.lifecycle as lifecycle_mod
import aota_forge.work_plane.soul as soul_mod
from aota_forge.work_plane.lifecycle import ExecutionWorkRoleBinding
from aota_forge.work_plane.soul import Soul
from aota_forge.work_plane.mapping import resolve_work_role_to_canonical_role, WorkRoleMappingError


# ---------------------------------------------------------------------------
# T01 task-main lifecycle is long-lived
# ---------------------------------------------------------------------------
def test_t01_task_main_lifecycle_is_long_lived() -> None:
    assert lifecycle_mod.TASK_MAIN_LONG_LIVED is True
    # Also via attribute presence
    assert getattr(lifecycle_mod, "TASK_MAIN_LONG_LIVED") is True
    # Semantic: not a one-shot
    assert lifecycle_mod.TASK_MAIN_LONG_LIVED != lifecycle_mod.WORKER_ONE_SHOT or lifecycle_mod.TASK_MAIN_LONG_LIVED is True


# ---------------------------------------------------------------------------
# T02 task-main wake source is user
# ---------------------------------------------------------------------------
def test_t02_task_main_wake_source_is_user() -> None:
    assert lifecycle_mod.TASK_MAIN_WAKE_SOURCE == "user"
    # Must not be forge or task-main
    assert lifecycle_mod.TASK_MAIN_WAKE_SOURCE != "task-main_or_forge"
    assert lifecycle_mod.TASK_MAIN_WAKE_SOURCE != "forge"


# ---------------------------------------------------------------------------
# T03 Worker lifecycle is one-shot
# ---------------------------------------------------------------------------
def test_t03_worker_lifecycle_is_one_shot() -> None:
    assert lifecycle_mod.WORKER_ONE_SHOT is True
    assert lifecycle_mod.WORKER_ONE_SHOT is not False


# ---------------------------------------------------------------------------
# T04 Worker is disposable
# ---------------------------------------------------------------------------
def test_t04_worker_is_disposable() -> None:
    assert lifecycle_mod.WORKER_DISPOSABLE is True
    # Disposable is semantic/product lifecycle, not subprocess identity
    assert lifecycle_mod.WORKER_DISPOSABLE is True


# ---------------------------------------------------------------------------
# T05 Worker wake source is task-main/Forge
# ---------------------------------------------------------------------------
def test_t05_worker_wake_source_is_task_main_or_forge() -> None:
    assert lifecycle_mod.WORKER_WAKE_SOURCE == "task-main_or_forge"
    assert "task-main" in lifecycle_mod.WORKER_WAKE_SOURCE
    assert "forge" in lifecycle_mod.WORKER_WAKE_SOURCE.lower()


# ---------------------------------------------------------------------------
# T06 execution-time WorkRole binding accepts AgentWorkRole
# ---------------------------------------------------------------------------
def test_t06_execution_time_workrole_binding_accepts_agent_work_role() -> None:
    # Accepts enum members
    for role in AgentWorkRole:
        b = ExecutionWorkRoleBinding(work_role=role)
        assert b.work_role is role
        assert b.work_role.value == role.value
        # Accepts string via parse
        b2 = ExecutionWorkRoleBinding(work_role=role.value)
        assert b2.work_role is role
        # Via bind helper
        b3 = ExecutionWorkRoleBinding.bind(role)
        assert b3.work_role is role
        b4 = ExecutionWorkRoleBinding.bind(role.value)
        assert b4.work_role is role

    # Canonical dict is deterministic
    b = ExecutionWorkRoleBinding(work_role=AgentWorkRole.CODER)
    assert b.canonical_dict() == {"work_role": "coder"}
    assert b.to_dict() == {"work_role": "coder"}
    # from_dict roundtrip
    restored = ExecutionWorkRoleBinding.from_dict({"work_role": "coder"})
    assert restored.work_role is AgentWorkRole.CODER
    assert restored.canonical_json() == b.canonical_json()


# ---------------------------------------------------------------------------
# T07 foreign CanonicalRole rejected as WorkRole binding
# ---------------------------------------------------------------------------
def test_t07_foreign_canonical_role_rejected_as_workrole_binding() -> None:
    for canonical in CanonicalRole:
        with pytest.raises(TypeError):
            ExecutionWorkRoleBinding(work_role=canonical)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ExecutionWorkRoleBinding.bind(canonical)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ExecutionWorkRoleBinding.from_dict({"work_role": canonical})  # type: ignore[dict-item]

    # Unknown string also fails closed
    with pytest.raises((ValueError, TypeError)):
        ExecutionWorkRoleBinding(work_role="executor")  # executor not a work role
    with pytest.raises((ValueError, TypeError)):
        ExecutionWorkRoleBinding(work_role="planner")
    with pytest.raises((ValueError, TypeError)):
        ExecutionWorkRoleBinding(work_role="unknown-role")

    # Non-string shapes
    for bad in [None, 123, [], {}, b"coder"]:
        with pytest.raises(TypeError):
            ExecutionWorkRoleBinding(work_role=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T08 binding does not persist/globalize WorkRole authority
# ---------------------------------------------------------------------------
def test_t08_binding_does_not_persist_globalize_workrole_authority() -> None:
    # Constants declare binding is execution scoped, not persistent, not authority
    assert lifecycle_mod.WORK_ROLE_BINDING_IS_EXECUTION_SCOPED is True
    assert lifecycle_mod.WORK_ROLE_BINDING_IS_PERSISTENT_AGENT_IDENTITY is False
    assert lifecycle_mod.WORK_ROLE_BINDING_IS_AUTHORITY is False
    assert lifecycle_mod.LIFECYCLE_CONTRACT_IS_AUTHORITY is False

    # Two bindings with different roles are independent — no global registry
    b1 = ExecutionWorkRoleBinding(work_role=AgentWorkRole.CODER)
    b2 = ExecutionWorkRoleBinding(work_role=AgentWorkRole.REVIEWER)
    assert b1.work_role is AgentWorkRole.CODER
    assert b2.work_role is AgentWorkRole.REVIEWER
    assert b1 != b2
    # Same Worker implementation can be rebound to different role in new execution
    # (Forge authority would decide, binding itself does not lock)
    b3 = ExecutionWorkRoleBinding(work_role=AgentWorkRole.ANALYST)
    assert b3.work_role is AgentWorkRole.ANALYST

    # Binding object itself carries no authority fields
    assert not hasattr(b1, "is_authority")
    assert not hasattr(b1, "authority")
    # Module must not expose global registry
    assert not hasattr(lifecycle_mod, "AGENT_PROFILE_REGISTRY")
    assert not hasattr(lifecycle_mod, "GLOBAL_WORK_ROLE_REGISTRY")
    assert not hasattr(lifecycle_mod, "PERSISTENT_ROLE_ASSIGNMENT")
    # from_dict unknown fields fail closed
    with pytest.raises(ValueError):
        ExecutionWorkRoleBinding.from_dict({"work_role": "coder", "authority": True})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        ExecutionWorkRoleBinding.from_dict({"work_role": "coder", "agent_id": "x"})  # type: ignore[dict-item]

    # No module-level persistent storage
    src = pathlib.Path(lifecycle_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    assert "global agent profile registry" not in lower
    assert "persistent role assignment database" not in lower
    # Should not define registry/database dict at module level
    assert "registry = {" not in lower
    assert "profile_registry" not in lower


# ---------------------------------------------------------------------------
# T09 task-main default CanonicalRole mapping still absent
# ---------------------------------------------------------------------------
def test_t09_task_main_default_canonical_role_mapping_still_absent() -> None:
    # lifecycle module declares none
    assert lifecycle_mod.TASK_MAIN_DEFAULT_EXECUTION_MAPPING is None
    # mapping resolver still fails closed for task-main
    with pytest.raises(WorkRoleMappingError):
        resolve_work_role_to_canonical_role(AgentWorkRole.TASK_MAIN)
    with pytest.raises(WorkRoleMappingError):
        resolve_work_role_to_canonical_role("task-main")
    # Binding for task-main is allowed as WorkRole binding (semantic role),
    # but mapping to CanonicalRole remains absent
    b = ExecutionWorkRoleBinding(work_role=AgentWorkRole.TASK_MAIN)
    assert b.work_role is AgentWorkRole.TASK_MAIN
    with pytest.raises(WorkRoleMappingError):
        resolve_work_role_to_canonical_role(b.work_role)


# ---------------------------------------------------------------------------
# T10 bounded SOUL accepted
# ---------------------------------------------------------------------------
def test_t10_bounded_soul_accepted() -> None:
    s = Soul(content="You are a concise helpful assistant. Follow project policy.")
    assert s.content == "You are a concise helpful assistant. Follow project policy."
    # With version
    s2 = Soul(content="Be precise. No authority.", version="1.0.0")
    assert s2.version == "1.0.0"
    # from_dict roundtrip
    s3 = Soul.from_dict({"content": "hello soul", "version": "v1"})
    assert s3.content == "hello soul"
    assert s3.version == "v1"
    s4 = Soul.from_dict({"content": "hello soul"})
    assert s4.version is None
    # canonical representation exists
    assert isinstance(s.canonical_json(), str)
    assert isinstance(s.canonical_dict(), dict)
    # digest present
    assert isinstance(s.digest, str)
    assert len(s.digest) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# T11 empty SOUL rejected
# ---------------------------------------------------------------------------
def test_t11_empty_soul_rejected() -> None:
    with pytest.raises(ValueError):
        Soul(content="")
    with pytest.raises(ValueError):
        Soul(content="   ")
    with pytest.raises(ValueError):
        Soul.from_dict({"content": ""})
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "   "})
    with pytest.raises(TypeError):
        Soul(content=None)  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError)):
        Soul.from_dict({})  # missing required


# ---------------------------------------------------------------------------
# T12 oversized SOUL rejected
# ---------------------------------------------------------------------------
def test_t12_oversized_soul_rejected() -> None:
    oversized = "x" * (soul_mod.MAX_SOUL_CONTENT_LENGTH + 1)
    with pytest.raises(ValueError):
        Soul(content=oversized)
    with pytest.raises(ValueError):
        Soul.from_dict({"content": oversized})
    # Exactly at bound should be accepted
    at_bound = "y" * soul_mod.MAX_SOUL_CONTENT_LENGTH
    s = Soul(content=at_bound)
    assert len(s.content) == soul_mod.MAX_SOUL_CONTENT_LENGTH
    # version oversized
    oversized_version = "v" * (soul_mod.MAX_SOUL_VERSION_LENGTH + 1)
    with pytest.raises(ValueError):
        Soul(content="valid", version=oversized_version)


# ---------------------------------------------------------------------------
# T13 SOUL digest deterministic
# ---------------------------------------------------------------------------
def test_t13_soul_digest_deterministic() -> None:
    s1 = Soul(content="deterministic content", version="1.0")
    s2 = Soul(content="deterministic content", version="1.0")
    assert s1.digest == s2.digest
    assert s1.compute_digest() == s2.compute_digest()
    assert s1.canonical_json() == s2.canonical_json()
    # from_dict via different insertion order should give same canonical/digest
    s3 = Soul.from_dict({"version": "1.0", "content": "deterministic content"})
    s4 = Soul.from_dict({"content": "deterministic content", "version": "1.0"})
    assert s3.digest == s4.digest
    assert s3.canonical_json() == s4.canonical_json()
    # canonical_json uses sorted keys
    parsed = canonical_json({"version": "1.0", "content": "deterministic content"})
    assert s1.canonical_json() == parsed


# ---------------------------------------------------------------------------
# T14 SOUL semantic change changes digest
# ---------------------------------------------------------------------------
def test_t14_soul_semantic_change_changes_digest() -> None:
    base = Soul(content="base content")
    changed = Soul(content="base content modified")
    assert base.digest != changed.digest
    assert base.canonical_json() != changed.canonical_json()
    # version change also changes digest
    v1 = Soul(content="same", version="1.0")
    v2 = Soul(content="same", version="2.0")
    assert v1.digest != v2.digest
    # whitespace normalization still deterministic but trimmed content defines identity
    # Ensure two souls with same stripped content have same digest
    s1 = Soul(content="  hello  ")
    s2 = Soul(content="hello")
    assert s1.digest == s2.digest


# ---------------------------------------------------------------------------
# T15 SOUL is not authority/profile/store
# ---------------------------------------------------------------------------
def test_t15_soul_is_not_authority_profile_store() -> None:
    assert soul_mod.SOUL_IS_BOOTSTRAP_COMPONENT is True
    assert soul_mod.SOUL_IS_AUTHORITY is False
    assert soul_mod.SOUL_IS_AGENT_PROFILE_ONTOLOGY is False
    assert soul_mod.SOUL_IS_PERSISTENT_STORE is False
    # Soul instance carries no authority fields
    s = Soul(content="test")
    assert not hasattr(s, "is_authority")
    assert not hasattr(s, "authority")
    # Module must not expose authority/store/registry
    assert not hasattr(soul_mod, "SOUL_REGISTRY")
    assert not hasattr(soul_mod, "SOUL_DATABASE")
    assert not hasattr(soul_mod, "SOUL_STORE")
    # Unknown fields fail closed
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "x", "registry": "bad"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "x", "authority": True})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "x", "prompt_marketplace": "bad"})  # type: ignore[dict-item]
    # Check file does not contain authority semantics in code
    src = pathlib.Path(soul_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Ensure no persistent store semantics
    assert "soul registry" not in lower
    assert "soul database" not in lower
    assert "prompt marketplace" not in lower
    assert "behavior policy engine" not in lower


# ---------------------------------------------------------------------------
# T16 no Hermes dependency
# ---------------------------------------------------------------------------
def test_t16_no_hermes_dependency() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ["aota_forge/work_plane/lifecycle.py", "aota_forge/work_plane/soul.py"]:
        src = (repo_root / rel).read_text(encoding="utf-8")
        lower = src.lower()
        # Check import lines
        for line in src.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "hermes" not in stripped, f"hermes import in {rel}: {line}"
        # No hermes private identifiers
        assert "hermesadapter" not in lower, f"found hermesadapter in {rel}"
        assert "hermeshostclient" not in lower, f"found hermeshostclient in {rel}"
        assert "processregistry" not in lower, f"found processregistry in {rel}"
        assert "from aota_forge.adapters.hermes" not in lower

    # Also ensure core does not depend on work_plane (dependency direction)
    core_exec = repo_root / "aota_forge/core/execution"
    for py_file in core_exec.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "work_plane" not in content, f"{py_file.name} imports work_plane"
        assert "lifecycle" not in content or "work_plane" not in content

    # lifecycle and soul files themselves must have zero hermes imports
    for mod in [lifecycle_mod, soul_mod]:
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "HermesAdapter" not in src
        assert "HermesHostClient" not in src
        assert "ProcessRegistry" not in src


# ---------------------------------------------------------------------------
# T17 no bootstrap bundle implementation
# ---------------------------------------------------------------------------
def test_t17_no_bootstrap_bundle_implementation() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ["aota_forge/work_plane/lifecycle.py", "aota_forge/work_plane/soul.py"]:
        src = (repo_root / rel).read_text(encoding="utf-8")
        lower = src.lower()
        # Must not implement bootstrap bundle artifacts
        assert "BootstrapBundle" not in src, f"BootstrapBundle leaked in {rel}"
        assert "bootstrap bundle" not in lower, f"bootstrap bundle string in {rel}"
        assert "bootstrapbundle" not in lower
        assert "bundle assembler" not in lower
        assert "eager_bootstrap" not in lower
        assert "progressive bundle" not in lower
        assert "bootstrap budget" not in lower
        assert "effective policy" not in lower or "soul" not in lower

    # Also ensure no bundle file exists in work_plane
    work_plane_dir = repo_root / "aota_forge/work_plane"
    for py_file in work_plane_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "class BootstrapBundle" not in content
        assert "def assemble_bootstrap" not in content


# ---------------------------------------------------------------------------
# T18 no AGENTS resolver implementation
# ---------------------------------------------------------------------------
def test_t18_no_agents_resolver_implementation() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ["aota_forge/work_plane/lifecycle.py", "aota_forge/work_plane/soul.py"]:
        src = (repo_root / rel).read_text(encoding="utf-8")
        lower = src.lower()
        # Must not implement AGENTS filesystem resolver, sandbox, restricted shell
        assert "agentsfilesystemresolver" not in lower.replace(" ", "").replace("_", "")
        assert "agents_resolver" not in lower
        # Check no resolver implementation keywords
        assert "filesystem resolver" not in lower
        assert "restricted shell" not in lower
        assert "sandbox" not in lower
        # Ensure no AGENTS resolver artifacts
        assert "class AgentsResolver" not in src
        assert "def resolve_agents" not in src
        assert "AGENTS effective policy" not in src
        assert "resolve_applicable_agents" not in lower

    # Ensure work_plane does not contain AGENTS resolver file
    work_plane_dir = repo_root / "aota_forge/work_plane"
    for py_file in work_plane_dir.glob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        assert "def resolve_applicable_agents" not in src
        assert "class AgentsResolver" not in src


# ---------------------------------------------------------------------------
# Additional: SOUL boundedness unknowns fail closed, canonical determinism
# ---------------------------------------------------------------------------
def test_soul_unknown_fields_fail_closed() -> None:
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "x", "unknown": "field"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        Soul.from_dict({"content": "x", "content_extra": "y"})  # type: ignore[dict-item]


def test_soul_canonical_deterministic() -> None:
    s = Soul(content="a", version="1")
    # canonical_json uses sorted keys, deterministic
    expected = canonical_json({"content": "a", "version": "1"})
    assert s.canonical_json() == expected
    # digest matches sha256 of canonical_json
    expected_digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert s.digest == expected_digest


def test_binding_unknown_fields_fail_closed() -> None:
    with pytest.raises(ValueError):
        ExecutionWorkRoleBinding.from_dict({"work_role": "coder", "extra": 1})  # type: ignore[dict-item]


def test_lifecycle_no_second_role_ontology() -> None:
    # Must reuse AgentWorkRole, not define second enum
    src = pathlib.Path(lifecycle_mod.__file__).read_text(encoding="utf-8")
    # Should not define enum class defining roles again
    assert "class AgentWorkRole" not in src
    assert "class WorkRole" not in src or "ExecutionWorkRoleBinding" in src
    # Should import AgentWorkRole from roles
    assert "from aota_forge.work_plane.roles import" in src
    assert "AgentWorkRole" in src


def test_task_main_not_mapped_to_planner_in_lifecycle() -> None:
    src = pathlib.Path(lifecycle_mod.__file__).read_text(encoding="utf-8")
    # Must not contain task-main -> planner default mapping
    assert "TASK_MAIN" not in src or "PLANNER" not in src or "task-main" not in src.lower()  # loose check
    # Direct check: lifecycle should not contain mapping to CanonicalRole.PLANNER for task-main
    # So ensure no literal "CanonicalRole.PLANNER" in lifecycle
    assert "CanonicalRole.PLANNER" not in src
    assert "CANONICAL_ROLE" not in src or "CanonicalRole" not in src
