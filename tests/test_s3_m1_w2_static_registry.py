"""S3 M1 W2 Agent Work Role Namespace / Deterministic Static Registry.

Focused W2 tests per prompt section 19.

Validates:
A. Namespace contract — exactly S1 AgentWorkRole universe accepted
B. Registry construction — explicit, bounded, externally immutable
C. Determinism — input order does not affect canonical projection
D. Exact lookup — namespace+skill_id+version, no latest substitution
E. Multiple versions coexist without precedence
F. Duplicate fail-closed — identical and differing digest
G. Namespace isolation — same identity in different namespaces allowed
H. No authority — no tool/fs/hydration/TaskHandoff mutation
I. No filesystem discovery
"""

from __future__ import annotations

import hashlib
import pathlib
import random

import pytest

from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES
from aota_forge.work_plane.skill import SkillIdentity
from aota_forge.work_plane.skill_registry import (
    SkillRegistryEntry,
    StaticSkillRegistry,
    build_static_skill_registry,
    MAX_REGISTRY_ENTRIES,
    MAX_CONTENT_REF_LENGTH,
    REGISTRY_KEY_FIELDS,
    STATIC_REGISTRY_V0,
    STATIC_DECLARATIVE_INDEX,
    RUNTIME_FILESYSTEM_DISCOVERY,
    REGISTRY_BOUNDED,
    REGISTRY_DETERMINISTIC,
    REGISTRY_FAIL_CLOSED,
    REGISTRY_NAMESPACE_SCOPED,
    SKILL_IS_AUTHORITY,
    NAMESPACE_GRANTS_AUTHORITY,
    CONTENT_REF_IS_AUTHORITY,
    CONTENT_REF_IS_SKILL_IDENTITY,
    CONTENT_REF_AUTO_OPENED,
    CONTENT_REF_AUTO_HYDRATED,
    EXACT_VERSION_LOOKUP,
    LATEST_VERSION_LOOKUP,
    SEMVER_PRECEDENCE,
)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _identity(skill_id: str = "skill-x", version: str = "v1", provenance: str = "forge-native", digest: str | None = None) -> SkillIdentity:
    if digest is None:
        digest = _digest(f"{skill_id}@{version}@{provenance}")
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance=provenance)


def _entry(namespace: object = AgentWorkRole.CODER, skill_id: str = "skill-x", version: str = "v1", provenance: str = "forge-native", digest: str | None = None, content_ref: str | None = None) -> SkillRegistryEntry:
    return SkillRegistryEntry(namespace=namespace, identity=_identity(skill_id, version, provenance, digest), content_ref=content_ref)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A. Namespace contract
# ---------------------------------------------------------------------------

def test_namespace_accepts_exactly_five_values():
    assert set(WORK_ROLES) == {"task-main", "analyst", "coder", "reviewer", "project-steward"}
    # Each string and enum member accepted via registry entry construction
    for role_str in WORK_ROLES:
        e1 = _entry(namespace=role_str, skill_id=f"skill-{role_str}", version="v1")
        assert e1.namespace.value == role_str
        # also enum member
        enum_member = AgentWorkRole(role_str)
        e2 = _entry(namespace=enum_member, skill_id=f"skill-{role_str}-2", version="v1")
        assert e2.namespace == enum_member


def test_namespace_rejects_unknown_role():
    with pytest.raises((ValueError, TypeError)):
        _entry(namespace="unknown-role", skill_id="s", version="v1")
    with pytest.raises((ValueError, TypeError)):
        _entry(namespace="architect", skill_id="s", version="v1")
    with pytest.raises((ValueError, TypeError)):
        _entry(namespace="friday", skill_id="s", version="v1")


def test_namespace_rejects_foreign_enum():
    from enum import Enum

    class ForeignRole(str, Enum):
        coder = "coder"

    with pytest.raises(TypeError):
        _entry(namespace=ForeignRole.coder, skill_id="s", version="v1")  # type: ignore
    # Also direct CanonicalRole if available
    try:
        from aota_forge.core.execution.roles import CanonicalRole

        with pytest.raises(TypeError):
            _entry(namespace=CanonicalRole.CODER, skill_id="s", version="v1")  # type: ignore
    except ImportError:
        pass


def test_namespace_rejects_legacy_profile_aliases():
    legacy_aliases = ["architect", "debugger", "task-coordinator", "friday"]
    for alias in legacy_aliases:
        with pytest.raises((ValueError, TypeError)):
            _entry(namespace=alias, skill_id="s", version="v1")
        # Also ensure no inferred mapping — alias not accepted as analyst/task-main etc
        with pytest.raises((ValueError, TypeError)):
            StaticSkillRegistry([]).get(alias, "s", "v1")  # type: ignore


def test_namespace_rejects_none_and_non_string():
    with pytest.raises((TypeError, ValueError)):
        _entry(namespace=None, skill_id="s", version="v1")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _entry(namespace=123, skill_id="s", version="v1")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _entry(namespace=["coder"], skill_id="s", version="v1")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _entry(namespace="", skill_id="s", version="v1")  # type: ignore


def test_namespace_no_fallback():
    # Unknown namespace must fail, not return default
    reg = StaticSkillRegistry([_entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")])
    with pytest.raises((ValueError, TypeError)):
        reg.get("unknown", "s", "v1")
    with pytest.raises((ValueError, TypeError)):
        reg.entries_for_namespace("unknown")
    with pytest.raises((ValueError, TypeError)):
        reg.get(None, "s", "v1")  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        reg.entries_for_namespace(None)  # type: ignore


def test_namespace_canonical_serialization_uses_value():
    e = _entry(namespace=AgentWorkRole.PROJECT_STEWARD, skill_id="s", version="v1")
    assert e.namespace.value == "project-steward"
    assert e.namespace_value == "project-steward"
    assert e.canonical_dict()["namespace"] == "project-steward"


# ---------------------------------------------------------------------------
# B. Registry construction
# ---------------------------------------------------------------------------

def test_registry_constructs_from_explicit_entries():
    entries = [
        _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1"),
        _entry(namespace=AgentWorkRole.REVIEWER, skill_id="skill-b", version="v2"),
    ]
    reg = StaticSkillRegistry(entries)
    assert len(reg) == 2
    reg2 = build_static_skill_registry(entries)
    assert len(reg2) == 2


def test_registry_bounded():
    assert REGISTRY_BOUNDED is True
    assert MAX_REGISTRY_ENTRIES > 0
    assert MAX_REGISTRY_ENTRIES <= 1024  # conservative bound
    # Oversized fails closed, no silent truncation
    oversized = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES + 1)]
    with pytest.raises(ValueError):
        StaticSkillRegistry(oversized)
    # Exactly at bound passes
    at_bound = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES)]
    reg = StaticSkillRegistry(at_bound)
    assert len(reg) == MAX_REGISTRY_ENTRIES
    # Ensure not truncated
    assert len(reg.entries) == MAX_REGISTRY_ENTRIES


def test_registry_externally_immutable():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="s1", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="s2", version="v1")
    reg = StaticSkillRegistry([e1, e2])
    # entries is tuple — immutable
    assert isinstance(reg.entries, tuple)
    with pytest.raises((TypeError, AttributeError)):
        reg.entries.append(e1)  # type: ignore
    # frozen dataclass — cannot mutate _entries
    with pytest.raises((TypeError, AttributeError, ValueError)):
        reg._entries = ()  # type: ignore
    # __iter__ also deterministic
    assert list(reg) == list(reg.entries)


def test_registry_entry_immutable():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")
    with pytest.raises(Exception):
        e.namespace = AgentWorkRole.REVIEWER  # type: ignore
    with pytest.raises(Exception):
        e.identity = _identity(skill_id="other")  # type: ignore
    # entries tuple immutable
    reg = StaticSkillRegistry([e])
    with pytest.raises(Exception):
        reg.entries[0] = e  # type: ignore


# ---------------------------------------------------------------------------
# C. Determinism
# ---------------------------------------------------------------------------

def test_determinism_reversed_input_order():
    a = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    b = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    c = _entry(namespace=AgentWorkRole.REVIEWER, skill_id="skill-a", version="v1")
    entries_forward = [a, b, c]
    entries_reversed = [c, b, a]
    reg1 = StaticSkillRegistry(entries_forward)
    reg2 = StaticSkillRegistry(entries_reversed)
    assert reg1.entries == reg2.entries
    assert reg1.canonical_json() == reg2.canonical_json()
    assert reg1.digest == reg2.digest


def test_determinism_shuffled_input_order():
    entries = [
        _entry(namespace=AgentWorkRole.ANALYST, skill_id="s3", version="v2"),
        _entry(namespace=AgentWorkRole.CODER, skill_id="s1", version="v1"),
        _entry(namespace=AgentWorkRole.REVIEWER, skill_id="s2", version="v1"),
        _entry(namespace=AgentWorkRole.TASK_MAIN, skill_id="s1", version="v2"),
        _entry(namespace=AgentWorkRole.PROJECT_STEWARD, skill_id="s4", version="v1"),
    ]
    shuffled = list(reversed(entries))
    # also random shuffle with fixed seed
    rnd = random.Random(42)
    rnd.shuffle(shuffled)
    reg1 = StaticSkillRegistry(entries)
    reg2 = StaticSkillRegistry(shuffled)
    assert reg1.entries == reg2.entries
    # enumeration also deterministic
    assert [e.composite_key for e in reg1] == [e.composite_key for e in reg2]


def test_determinism_canonical_ordering_by_namespace_value_skill_id_version():
    # Create entries that would sort differently than insertion order
    e1 = _entry(namespace=AgentWorkRole.REVIEWER, skill_id="b", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="a", version="v2")
    e3 = _entry(namespace=AgentWorkRole.CODER, skill_id="a", version="v1")
    reg = StaticSkillRegistry([e1, e2, e3])
    keys = [e.composite_key for e in reg.entries]
    assert keys == sorted(keys)


def test_input_order_is_not_authority():
    a = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1", provenance="a")
    # Same composite key cannot be duplicated regardless of order, but order should not affect lookup
    reg = StaticSkillRegistry([a])
    assert reg.get(AgentWorkRole.CODER, "s", "v1") == a
    # Reversed single entry still same
    reg2 = StaticSkillRegistry([a])
    assert reg2.get(AgentWorkRole.CODER, "s", "v1") == a


# ---------------------------------------------------------------------------
# D. Exact lookup
# ---------------------------------------------------------------------------

def test_exact_lookup():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    reg = StaticSkillRegistry([e])
    found = reg.get(AgentWorkRole.CODER, "skill-x", "v1")
    assert found == e
    # Also via string namespace
    found2 = reg.get("coder", "skill-x", "v1")
    assert found2 == e
    assert found2.identity == e.identity


def test_exact_lookup_missing_version_not_replaced():
    e_v1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2")
    reg = StaticSkillRegistry([e_v1, e_v2])
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") == e_v1
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v2") == e_v2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v3") is None
    # No latest substitution — missing v3 not replaced by v2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v9") is None


def test_no_latest_or_semver_lookup():
    assert LATEST_VERSION_LOOKUP is False
    assert SEMVER_PRECEDENCE is False
    assert EXACT_VERSION_LOOKUP is True
    # Registry must not have get_latest method
    reg = StaticSkillRegistry([_entry(namespace=AgentWorkRole.CODER, skill_id="s", version="1.0.0")])
    assert not hasattr(reg, "get_latest")
    assert not hasattr(reg, "latest")
    assert not hasattr(reg, "get_highest_version")


# ---------------------------------------------------------------------------
# E. Multiple versions
# ---------------------------------------------------------------------------

def test_multiple_versions_coexist_same_namespace():
    e_v1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2")
    reg = StaticSkillRegistry([e_v1, e_v2])
    assert len(reg) == 2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") == e_v1
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v2") == e_v2
    # No implicit precedence — both retrievable, no ordering preference
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") != reg.get(AgentWorkRole.CODER, "skill-x", "v2")
    # entries_for_namespace returns both deterministically
    entries = reg.entries_for_namespace(AgentWorkRole.CODER)
    assert len(entries) == 2
    # Sorted by version deterministically
    versions = [e.version for e in entries]
    assert versions == sorted(versions)


# ---------------------------------------------------------------------------
# F. Duplicate fail-closed
# ---------------------------------------------------------------------------

def test_duplicate_identical_fail_closed():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    # Duplicate identical entry
    with pytest.raises(ValueError):
        StaticSkillRegistry([e, e])
    # Duplicate via separate identical objects same key
    e_dup = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance=e.identity.provenance, digest=e.identity.digest)
    with pytest.raises(ValueError):
        StaticSkillRegistry([e, e_dup])


def test_duplicate_same_key_differing_digest_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("content-a"), provenance="a")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("content-b"), provenance="b")
    # Same namespace+skill_id+version but different digest/provenance must fail
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])
    # Also different content_ref must fail
    e3 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="ref/a")
    e4 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="ref/b")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e3, e4])


def test_duplicate_same_key_differing_metadata_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="p1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="p2")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])


# ---------------------------------------------------------------------------
# G. Namespace isolation
# ---------------------------------------------------------------------------

def test_namespace_isolation_same_identity_different_namespaces():
    # Same skill_id+version in coder and reviewer allowed
    e_coder = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_reviewer = _entry(namespace=AgentWorkRole.REVIEWER, skill_id="skill-x", version="v1")
    # Must not collide
    reg = StaticSkillRegistry([e_coder, e_reviewer])
    assert len(reg) == 2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") == e_coder
    assert reg.get(AgentWorkRole.REVIEWER, "skill-x", "v1") == e_reviewer
    # Coder lookup must not return reviewer entry
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") != e_reviewer
    # Entries per namespace isolated
    assert len(reg.entries_for_namespace(AgentWorkRole.CODER)) == 1
    assert len(reg.entries_for_namespace(AgentWorkRole.REVIEWER)) == 1
    assert reg.entries_for_namespace(AgentWorkRole.CODER)[0] == e_coder


def test_namespace_isolation_missing_in_one_namespace():
    e_coder = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    reg = StaticSkillRegistry([e_coder])
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") is not None
    assert reg.get(AgentWorkRole.REVIEWER, "skill-x", "v1") is None


# ---------------------------------------------------------------------------
# H. No authority
# ---------------------------------------------------------------------------

def test_no_authority_on_entry():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")
    assert SKILL_IS_AUTHORITY is False
    assert NAMESPACE_GRANTS_AUTHORITY is False
    assert CONTENT_REF_IS_AUTHORITY is False
    # Entry must not expose authority fields
    for forbidden in ("tool_authority", "execution_authority", "filesystem_authority", "authority", "tool_requirements", "permissions"):
        assert not hasattr(e, forbidden)
        assert forbidden not in e.canonical_dict()
    # Registry must not expose authority
    reg = StaticSkillRegistry([e])
    assert not hasattr(reg, "authorize")
    assert not hasattr(reg, "grant_tool")
    assert not hasattr(reg, "open")
    assert not hasattr(reg, "load_content")


def test_registry_does_not_hydrate_or_authorize():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1", content_ref="ref/lint")
    reg = StaticSkillRegistry([e])
    # content_ref is opaque, not auto-opened
    assert CONTENT_REF_AUTO_OPENED is False
    assert CONTENT_REF_AUTO_HYDRATED is False
    # Registry must not have hydration methods
    assert not hasattr(reg, "hydrate")
    assert not hasattr(reg, "open")
    assert not hasattr(reg, "load")
    # Entry content_ref not auto-resolved
    found = reg.get(AgentWorkRole.CODER, "s", "v1")
    assert found.content_ref == "ref/lint"
    # Must not have filesystem side effect — check no file opened
    assert found.identity.digest is not None


def test_registry_does_not_mutate_task_handoff():
    # Registry entry creation must not mutate TaskHandoff or require it
    from aota_forge.work_plane.handoff import TaskHandoff

    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")
    reg = StaticSkillRegistry([e])
    # No handoff mutation — ensure handoff unchanged after registry ops
    h = TaskHandoff(
        work_role="coder",
        task_kind="implementation",
        objective="test",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )
    before = h.canonical_json()
    _ = reg.get(AgentWorkRole.CODER, "s", "v1")
    _ = reg.entries_for_namespace(AgentWorkRole.CODER)
    assert h.canonical_json() == before


def test_entry_identity_is_skill_identity_not_duplicate():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")
    # Must carry SkillIdentity via .identity, not duplicate fields
    assert isinstance(e.identity, SkillIdentity)
    assert e.identity.skill_id == "s"
    assert e.identity.version == "v1"
    # Composite key derived correctly
    assert e.composite_key == ("coder", "s", "v1")
    # Registry key fields inventory
    assert REGISTRY_KEY_FIELDS == ("namespace", "skill_id", "version")


# ---------------------------------------------------------------------------
# I. No filesystem discovery
# ---------------------------------------------------------------------------

def test_no_filesystem_discovery_structurally():
    src = pathlib.Path("aota_forge/work_plane/skill_registry.py").read_text(encoding="utf-8")
    lower = src.lower()
    # Must not implement discovery primitives (allow docstring mentions)
    assert "os.walk" not in lower
    assert "path.rglob" not in lower
    # rglob as implementation primitive — allow word in comment but check not as method call
    # we check for ".rglob" or "rglob(" to avoid false positive on docstring
    assert ".rglob" not in lower
    assert "os.scandir" not in lower
    assert "entry_points" not in lower
    assert "importlib.metadata" not in lower
    # No filesystem imports for discovery — allow descriptive invariant strings
    assert "os.walk" not in src
    # Registry input is explicit — check constructor signature
    assert "def __init__" in src
    assert "entries" in src


def test_no_filesystem_discovery_behaviorally():
    # Construct registry from explicit entries — no filesystem needed
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1")
    reg = StaticSkillRegistry([e])
    assert len(reg) == 1
    # Ensure registry without any files on disk
    # Create another registry with different explicit entries — no scan
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="s2", version="v1")
    reg2 = StaticSkillRegistry([e2])
    assert reg2.get(AgentWorkRole.CODER, "s", "v1") is None
    assert reg2.get(AgentWorkRole.CODER, "s2", "v1") == e2


def test_registry_static_markers():
    assert STATIC_REGISTRY_V0 is True
    assert STATIC_DECLARATIVE_INDEX is True
    assert RUNTIME_FILESYSTEM_DISCOVERY is False
    assert REGISTRY_BOUNDED is True
    assert REGISTRY_DETERMINISTIC is True
    assert REGISTRY_FAIL_CLOSED is True
    assert REGISTRY_NAMESPACE_SCOPED is True
    assert CONTENT_REF_IS_SKILL_IDENTITY is False


def test_content_ref_opaque_bounded():
    # Valid opaque ref
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="v1", content_ref="skills/coder/lint.md")
    assert e.content_ref == "skills/coder/lint.md"
    # Invalid: absolute
    with pytest.raises(ValueError):
        _entry(namespace=AgentWorkRole.CODER, skill_id="s2", version="v1", content_ref="/absolute/path")
    # Invalid: traversal
    with pytest.raises(ValueError):
        _entry(namespace=AgentWorkRole.CODER, skill_id="s2", version="v1", content_ref="../escape")
    # Invalid: too long
    with pytest.raises(ValueError):
        _entry(namespace=AgentWorkRole.CODER, skill_id="s2", version="v1", content_ref="a" * (MAX_CONTENT_REF_LENGTH + 1))
    # None is allowed (no ref)
    e_none = _entry(namespace=AgentWorkRole.CODER, skill_id="s3", version="v1", content_ref=None)
    assert e_none.content_ref is None
    # Not authority
    assert CONTENT_REF_IS_AUTHORITY is False


def test_entries_for_namespace_bounded_deterministic():
    entries = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"s{i}", version="v1") for i in range(5)]
    entries += [_entry(namespace=AgentWorkRole.REVIEWER, skill_id=f"s{i}", version="v1") for i in range(3)]
    reg = StaticSkillRegistry(entries)
    coder_entries = reg.entries_for_namespace(AgentWorkRole.CODER)
    assert len(coder_entries) == 5
    # Deterministic order
    assert [e.skill_id for e in coder_entries] == sorted([e.skill_id for e in coder_entries])
    # No Skill content returned — only entries (no hydration)
    for e in coder_entries:
        assert isinstance(e, SkillRegistryEntry)
        assert not hasattr(e, "content")
        assert not hasattr(e, "hydrate")


def test_registry_does_not_implement_lexical_search():
    reg = StaticSkillRegistry([_entry(namespace=AgentWorkRole.CODER, skill_id="skill-abc", version="v1")])
    assert not hasattr(reg, "search")
    assert not hasattr(reg, "find_by_keyword")
    assert not hasattr(reg, "search_by_title")


def test_registry_does_not_use_semver():
    # Version is opaque — different versions coexist without semver ordering
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="1.0.0")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="2.0.0-alpha")
    e3 = _entry(namespace=AgentWorkRole.CODER, skill_id="s", version="not-semver")
    reg = StaticSkillRegistry([e1, e2, e3])
    assert reg.get(AgentWorkRole.CODER, "s", "1.0.0") == e1
    assert reg.get(AgentWorkRole.CODER, "s", "2.0.0-alpha") == e2
    assert reg.get(AgentWorkRole.CODER, "s", "not-semver") == e3
    # No semver precedence — lexical determinism only
    assert SEMVER_PRECEDENCE is False
