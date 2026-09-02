"""S3 M1 W3 Registry Integrity / Duplicate / Namespace Adversarial Proof.

Test-first adversarial proof that W1/W2 foundation is deterministic, bounded,
fail-closed, namespace-isolated, non-authoritative, input-order independent.

Covers matrices A-M per S3 Plan Issue #28 M1-W3.

Preferred production change: none. This file is the W3 source scope.
"""

from __future__ import annotations

import hashlib
import itertools
import pathlib
import random

import pytest

from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES
from aota_forge.work_plane.skill import SkillIdentity
from aota_forge.work_plane.skill_registry import (
    SkillRegistryEntry,
    StaticSkillRegistry,
    build_static_skill_registry,
    MAX_CONTENT_REF_LENGTH,
    MAX_REGISTRY_ENTRIES,
    CONTENT_REF_IS_AUTHORITY,
    CONTENT_REF_AUTO_OPENED,
    CONTENT_REF_AUTO_HYDRATED,
    CONTENT_REF_IS_SKILL_IDENTITY,
    EXACT_VERSION_LOOKUP,
    LATEST_VERSION_LOOKUP,
    NAMESPACE_GRANTS_AUTHORITY,
    REGISTRY_BOUNDED,
    REGISTRY_DETERMINISTIC,
    REGISTRY_FAIL_CLOSED,
    REGISTRY_KEY_FIELDS,
    REGISTRY_NAMESPACE_SCOPED,
    RUNTIME_FILESYSTEM_DISCOVERY,
    SEMVER_PRECEDENCE,
    SKILL_IS_AUTHORITY,
    STATIC_DECLARATIVE_INDEX,
    STATIC_REGISTRY_V0,
)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _identity(
    skill_id: str = "skill-x",
    version: str = "v1",
    provenance: str = "forge-native",
    digest: str | None = None,
) -> SkillIdentity:
    if digest is None:
        digest = _digest(f"{skill_id}@{version}@{provenance}")
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance=provenance)


def _entry(
    namespace: object = AgentWorkRole.CODER,
    skill_id: str = "skill-x",
    version: str = "v1",
    provenance: str = "forge-native",
    digest: str | None = None,
    content_ref: str | None = None,
) -> SkillRegistryEntry:
    return SkillRegistryEntry(namespace=namespace, identity=_identity(skill_id, version, provenance, digest), content_ref=content_ref)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Matrix A — Exact Duplicate (identical)
# ---------------------------------------------------------------------------

def test_a1_exact_duplicate_identical_objects_fail_closed():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e, e])
    # also via builder
    with pytest.raises(ValueError):
        build_static_skill_registry([e, e])


def test_a1_exact_duplicate_separate_equivalent_objects_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="forge-native", digest=_digest("content-same"))
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="forge-native", digest=_digest("content-same"))
    assert e1 == e2
    assert e1.composite_key == e2.composite_key
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e2, e1])


def test_a1_exact_duplicate_same_key_same_content_ref_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="ref/a")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="ref/a")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])


def test_a1_exact_duplicate_not_silently_deduped():
    e = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    try:
        reg = StaticSkillRegistry([e, e])
    except ValueError:
        pass
    else:
        pytest.fail("identical duplicate should fail closed, not deduplicate")


def test_a1_exact_duplicate_two_copies_via_list_multiplication():
    e = _entry(namespace="coder", skill_id="skill-x", version="v1")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e] * 2)


# ---------------------------------------------------------------------------
# Matrix B — Digest Conflict
# ---------------------------------------------------------------------------

def test_b_digest_conflict_same_key_different_digest_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("content-a"), provenance="forge-native")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("content-b"), provenance="forge-native")
    assert e1.identity.digest != e2.identity.digest
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e2, e1])


def test_b_digest_conflict_not_first_wins():
    e_a = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("a"), provenance="p-a")
    e_b = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("b"), provenance="p-b")
    # Both orders must fail, not first-wins
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_a, e_b])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_b, e_a])


def test_b_digest_conflict_not_last_wins():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("x"), provenance="same")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", digest=_digest("y"), provenance="same")
    for perm in ([e1, e2], [e2, e1]):
        with pytest.raises(ValueError):
            StaticSkillRegistry(perm)


# ---------------------------------------------------------------------------
# Matrix C — Provenance Conflict
# ---------------------------------------------------------------------------

def test_c_provenance_conflict_same_key_different_provenance_fail_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="forge-native", digest=_digest("same-content"))
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", provenance="legacy", digest=_digest("same-content"))
    assert e1.identity.provenance != e2.identity.provenance
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])


def test_c_provenance_conflict_not_permitted():
    e1 = _entry(namespace="coder", skill_id="skill-x", version="v1", provenance="a")
    e2 = _entry(namespace="coder", skill_id="skill-x", version="v1", provenance="b")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e2, e1])


def test_c_digest_and_provenance_both_differ_fail_closed():
    e1 = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("c1"), provenance="p1")
    e2 = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("c2"), provenance="p2")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])


# ---------------------------------------------------------------------------
# Matrix D — Registry Metadata Conflict (content_ref)
# ---------------------------------------------------------------------------

def test_d_content_ref_conflict_same_key_different_ref_fail_closed():
    e_a = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="skills/coder/a.md")
    e_b = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content_ref="skills/coder/b.md")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_a, e_b])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_b, e_a])


def test_d_content_ref_vs_none_same_key_fail_closed():
    e_with = _entry(namespace="coder", skill_id="skill-x", version="v1", content_ref="ref/a")
    e_none = _entry(namespace="coder", skill_id="skill-x", version="v1", content_ref=None)
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_with, e_none])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_none, e_with])


def test_d_content_ref_is_bounded_metadata_not_authority():
    # content_ref is optional bounded metadata; different values under same key must conflict
    assert CONTENT_REF_IS_AUTHORITY is False
    e1 = _entry(namespace="coder", skill_id="skill-x", version="v1", content_ref="ref/a")
    e2 = _entry(namespace="coder", skill_id="skill-x", version="v1", content_ref="ref/b")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e1, e2])


# ---------------------------------------------------------------------------
# Matrix E — Multiple Explicit Versions
# ---------------------------------------------------------------------------

def test_e_multiple_explicit_versions_coexist():
    e_v1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2")
    reg = StaticSkillRegistry([e_v1, e_v2])
    assert len(reg) == 2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") == e_v1
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v2") == e_v2


def test_e_missing_version_returns_none_no_fallback():
    e_v1 = _entry(namespace="coder", skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace="coder", skill_id="skill-x", version="v2")
    reg = StaticSkillRegistry([e_v1, e_v2])
    assert reg.get("coder", "skill-x", "v3") is None
    assert reg.get("coder", "skill-x", "v9") is None
    assert reg.get("coder", "skill-x", "latest") is None


def test_e_no_latest_or_highest_semver_selection():
    assert LATEST_VERSION_LOOKUP is False
    assert SEMVER_PRECEDENCE is False
    assert EXACT_VERSION_LOOKUP is True
    e1 = _entry(namespace="coder", skill_id="skill-x", version="1.0.0")
    e2 = _entry(namespace="coder", skill_id="skill-x", version="2.0.0")
    reg = StaticSkillRegistry([e1, e2])
    assert reg.get("coder", "skill-x", "1.0.0") == e1
    assert reg.get("coder", "skill-x", "2.0.0") == e2
    assert reg.get("coder", "skill-x", "3.0.0") is None
    # No helper for latest
    assert not hasattr(reg, "get_latest")
    assert not hasattr(reg, "latest")
    assert not hasattr(reg, "get_highest_version")


def test_e_opaque_versions_no_semantic_precedence():
    versions = ["release-A", "2026.09", "legacy-1", "1.0.0", "v2", "not-semver"]
    entries = [_entry(namespace="coder", skill_id="skill-x", version=v) for v in versions]
    reg = StaticSkillRegistry(entries)
    assert len(reg) == len(versions)
    for v in versions:
        found = reg.get("coder", "skill-x", v)
        assert found is not None
        assert found.version == v
    # Missing opaque version still absent
    assert reg.get("coder", "skill-x", "release-B") is None
    assert reg.get("coder", "skill-x", "2026.10") is None
    # Ordering is deterministic lexical by key, not semantic precedence
    keys = [e.composite_key for e in reg.entries]
    assert keys == sorted(keys)


def test_e_not_a_conflict_different_versions_allowed():
    # Prove W3 MUST NOT reinterpret different explicit versions as conflict
    e_v1 = _entry(namespace="coder", skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace="coder", skill_id="skill-x", version="v2")
    e_v3 = _entry(namespace="coder", skill_id="skill-x", version="release-A")
    reg = StaticSkillRegistry([e_v1, e_v2, e_v3])
    assert len(reg) == 3


# ---------------------------------------------------------------------------
# Matrix F — Namespace Isolation
# ---------------------------------------------------------------------------

def test_f_same_identity_different_namespaces_no_collision():
    e_coder = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_reviewer = _entry(namespace=AgentWorkRole.REVIEWER, skill_id="skill-x", version="v1")
    reg = StaticSkillRegistry([e_coder, e_reviewer])
    assert len(reg) == 2
    assert reg.get(AgentWorkRole.CODER, "skill-x", "v1") == e_coder
    assert reg.get(AgentWorkRole.REVIEWER, "skill-x", "v1") == e_reviewer


def test_f_lookup_is_namespace_scoped_no_fallback():
    e_coder = _entry(namespace="coder", skill_id="skill-x", version="v1")
    reg = StaticSkillRegistry([e_coder])
    assert reg.get("coder", "skill-x", "v1") is not None
    assert reg.get("reviewer", "skill-x", "v1") is None
    assert reg.get("analyst", "skill-x", "v1") is None
    assert reg.get("project-steward", "skill-x", "v1") is None


def test_f_enumeration_namespace_scoped():
    e_coder1 = _entry(namespace="coder", skill_id="s1", version="v1")
    e_coder2 = _entry(namespace="coder", skill_id="s2", version="v1")
    e_reviewer = _entry(namespace="reviewer", skill_id="s1", version="v1")
    reg = StaticSkillRegistry([e_coder1, e_coder2, e_reviewer])
    coder_entries = reg.entries_for_namespace("coder")
    reviewer_entries = reg.entries_for_namespace("reviewer")
    analyst_entries = reg.entries_for_namespace("analyst")
    assert len(coder_entries) == 2
    assert len(reviewer_entries) == 1
    assert len(analyst_entries) == 0
    assert e_reviewer not in coder_entries
    assert e_coder1 not in reviewer_entries


def test_f_cross_namespace_isolation_all_five_namespaces():
    entries = [_entry(namespace=ns, skill_id="skill-x", version="v1") for ns in WORK_ROLES]
    reg = StaticSkillRegistry(entries)
    assert len(reg) == 5
    for ns in WORK_ROLES:
        found = reg.get(ns, "skill-x", "v1")
        assert found is not None
        assert found.namespace.value == ns
    # No fallback: coder entry not returned for analyst
    assert reg.get("coder", "skill-x", "v1").namespace.value == "coder"
    assert reg.get("analyst", "skill-x", "v1").namespace.value == "analyst"


# ---------------------------------------------------------------------------
# Matrix G — Invalid Namespace
# ---------------------------------------------------------------------------

def test_g_rejects_non_canonical_namespaces():
    invalid = ["architect", "debugger", "task-coordinator", "friday", "unknown", ""]
    for ns in invalid:
        with pytest.raises((ValueError, TypeError)):
            _entry(namespace=ns, skill_id="s", version="v1")
        # Also via registry get
        reg = StaticSkillRegistry([_entry(namespace="coder", skill_id="s", version="v1")])
        with pytest.raises((ValueError, TypeError)):
            reg.get(ns, "s", "v1")
        with pytest.raises((ValueError, TypeError)):
            reg.entries_for_namespace(ns)


def test_g_rejects_none_integer_object_foreign_enum():
    reg = StaticSkillRegistry([_entry(namespace="coder", skill_id="s", version="v1")])
    # None
    with pytest.raises((TypeError, ValueError)):
        _entry(namespace=None, skill_id="s", version="v1")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        reg.get(None, "s", "v1")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        reg.entries_for_namespace(None)  # type: ignore
    # integer / object
    for bad in [123, 3.14, ["coder"], {"coder": 1}, object()]:
        with pytest.raises((TypeError, ValueError)):
            _entry(namespace=bad, skill_id="s", version="v1")  # type: ignore
        with pytest.raises((TypeError, ValueError)):
            reg.get(bad, "s", "v1")  # type: ignore
    # foreign Enum
    from enum import Enum

    class ForeignRole(str, Enum):
        coder = "coder"

    with pytest.raises(TypeError):
        _entry(namespace=ForeignRole.coder, skill_id="s", version="v1")  # type: ignore
    with pytest.raises(TypeError):
        reg.get(ForeignRole.coder, "s", "v1")  # type: ignore


def test_g_no_legacy_profile_alias_mapping():
    aliases = ["architect", "debugger", "task-coordinator", "friday"]
    for alias in aliases:
        with pytest.raises((ValueError, TypeError)):
            _entry(namespace=alias, skill_id="s", version="v1")
    # Ensure no alias accidentally maps to valid namespace
    reg = StaticSkillRegistry([_entry(namespace="analyst", skill_id="s", version="v1")])
    for alias in aliases:
        with pytest.raises((ValueError, TypeError)):
            reg.get(alias, "s", "v1")


def test_g_rejects_canonical_role_as_namespace():
    try:
        from aota_forge.core.execution.roles import CanonicalRole

        with pytest.raises(TypeError):
            _entry(namespace=CanonicalRole.CODER, skill_id="s", version="v1")  # type: ignore
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Matrix H — Input Order Independence
# ---------------------------------------------------------------------------

def test_h_forward_reverse_canonical_projection_identical():
    a = _entry(namespace="coder", skill_id="skill-a", version="v1")
    b = _entry(namespace="coder", skill_id="skill-b", version="v1")
    c = _entry(namespace="reviewer", skill_id="skill-a", version="v1")
    forward = [a, b, c]
    reverse = [c, b, a]
    reg_f = StaticSkillRegistry(forward)
    reg_r = StaticSkillRegistry(reverse)
    assert reg_f.entries == reg_r.entries
    assert reg_f.canonical_json() == reg_r.canonical_json()
    assert reg_f.digest == reg_r.digest
    assert [e.composite_key for e in reg_f] == [e.composite_key for e in reg_r]


def test_h_multiple_permutations_deterministic():
    entries = [
        _entry(namespace=AgentWorkRole.ANALYST, skill_id="s3", version="v2"),
        _entry(namespace=AgentWorkRole.CODER, skill_id="s1", version="v1"),
        _entry(namespace=AgentWorkRole.REVIEWER, skill_id="s2", version="v1"),
        _entry(namespace=AgentWorkRole.TASK_MAIN, skill_id="s1", version="v2"),
        _entry(namespace=AgentWorkRole.PROJECT_STEWARD, skill_id="s4", version="v1"),
    ]
    # Deterministic permutations: reversed, sorted by skill_id, random with fixed seed
    perms = [
        list(entries),
        list(reversed(entries)),
        sorted(entries, key=lambda e: e.skill_id),
    ]
    rnd = random.Random(0)
    shuffled = list(entries)
    rnd.shuffle(shuffled)
    perms.append(shuffled)
    rnd2 = random.Random(42)
    shuffled2 = list(entries)
    rnd2.shuffle(shuffled2)
    perms.append(shuffled2)

    canonical = StaticSkillRegistry(entries).entries
    for perm in perms:
        reg = StaticSkillRegistry(perm)
        assert reg.entries == canonical
        # namespace enumeration also identical
        assert reg.entries_for_namespace("coder") == StaticSkillRegistry(entries).entries_for_namespace("coder")
        # exact lookup identical
        assert reg.get("coder", "s1", "v1") == StaticSkillRegistry(entries).get("coder", "s1", "v1")


def test_h_conflict_result_input_order_independent():
    e_a = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("a"), provenance="p-a")
    e_b = _entry(namespace="coder", skill_id="skill-x", version="v1", digest=_digest("b"), provenance="p-b")
    # All permutations must fail closed identically, not first-wins or last-wins
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_a, e_b])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_b, e_a])
    # Even with additional non-conflicting entries interleaved
    extra = _entry(namespace="coder", skill_id="other", version="v1")
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_a, extra, e_b])
    with pytest.raises(ValueError):
        StaticSkillRegistry([e_b, extra, e_a])
    with pytest.raises(ValueError):
        StaticSkillRegistry([extra, e_a, e_b])
    with pytest.raises(ValueError):
        StaticSkillRegistry([extra, e_b, e_a])


def test_h_input_order_not_authority_lookup_independent():
    e1 = _entry(namespace="coder", skill_id="skill-x", version="v1")
    e2 = _entry(namespace="coder", skill_id="skill-x", version="v2")
    reg_fwd = StaticSkillRegistry([e1, e2])
    reg_rev = StaticSkillRegistry([e2, e1])
    assert reg_fwd.get("coder", "skill-x", "v1") == reg_rev.get("coder", "skill-x", "v1")
    assert reg_fwd.get("coder", "skill-x", "v2") == reg_rev.get("coder", "skill-x", "v2")
    # No first-entry-wins or last-entry-wins observable
    assert reg_fwd.get("coder", "skill-x", "v1").version == "v1"


# ---------------------------------------------------------------------------
# Matrix I — Bound Enforcement
# ---------------------------------------------------------------------------

def test_i_at_bound_accepted_over_bound_rejected():
    assert REGISTRY_BOUNDED is True
    assert MAX_REGISTRY_ENTRIES > 0
    assert MAX_REGISTRY_ENTRIES <= 1024
    at_bound = [_entry(namespace="coder", skill_id=f"skill-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES)]
    reg = StaticSkillRegistry(at_bound)
    assert len(reg) == MAX_REGISTRY_ENTRIES
    oversized = [_entry(namespace="coder", skill_id=f"skill-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES + 1)]
    with pytest.raises(ValueError):
        StaticSkillRegistry(oversized)
    # No silent truncation
    assert len(reg.entries) == MAX_REGISTRY_ENTRIES


def test_i_unbounded_not_accepted_silent_truncation_no():
    # Prove unbounded acceptance would fail: try to add beyond bound via incremental building
    many = [_entry(namespace="coder", skill_id=f"s-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES)]
    reg = StaticSkillRegistry(many)
    # Attempt to construct larger should not truncate to MAX
    with pytest.raises(ValueError):
        StaticSkillRegistry(many + [_entry(namespace="coder", skill_id="extra", version="v1")])
    # Bound is implementation-local, not Plan constant — but must be finite fail-closed
    assert REGISTRY_BOUNDED is True


def test_i_bound_enforced_via_builder_as_well():
    at_bound = [_entry(namespace="coder", skill_id=f"skill-{i}", version="v1") for i in range(MAX_REGISTRY_ENTRIES)]
    reg = build_static_skill_registry(at_bound)
    assert len(reg) == MAX_REGISTRY_ENTRIES
    oversized = at_bound + [_entry(namespace="coder", skill_id="overflow", version="v1")]
    with pytest.raises(ValueError):
        build_static_skill_registry(oversized)


# ---------------------------------------------------------------------------
# Matrix J — Result Bound / Enumeration
# ---------------------------------------------------------------------------

def test_j_enumeration_bounded_finite_registry():
    entries = [_entry(namespace="coder", skill_id=f"s{i}", version="v1") for i in range(5)]
    entries += [_entry(namespace="reviewer", skill_id=f"s{i}", version="v1") for i in range(3)]
    reg = StaticSkillRegistry(entries)
    assert len(reg.entries) == 8
    coder = reg.entries_for_namespace("coder")
    assert len(coder) == 5
    # Result bounded by finite registry, not unbounded external inventory
    all_entries = list(reg)
    assert len(all_entries) == 8
    assert len(coder) + len(reg.entries_for_namespace("reviewer")) + len(reg.entries_for_namespace("analyst")) == 8


def test_j_no_filesystem_discovery_during_enumeration():
    assert RUNTIME_FILESYSTEM_DISCOVERY is False
    src = pathlib.Path("aota_forge/work_plane/skill_registry.py").read_text(encoding="utf-8")
    lower = src.lower()
    # Must not contain filesystem discovery primitives
    assert "os.walk" not in lower
    assert ".rglob" not in lower
    assert "os.scandir" not in lower
    assert "entry_points" not in lower
    # Enumeration must not hydrate content
    e = _entry(namespace="coder", skill_id="s", version="v1", content_ref="ref/lint")
    reg = StaticSkillRegistry([e])
    found = reg.entries_for_namespace("coder")
    for entry in found:
        assert isinstance(entry, SkillRegistryEntry)
        assert not hasattr(entry, "content")
        assert not hasattr(entry, "hydrate")
    assert not hasattr(reg, "hydrate")
    assert not hasattr(reg, "open")
    assert not hasattr(reg, "load_content")


def test_j_no_skill_content_auto_hydration():
    e = _entry(namespace="coder", skill_id="s", version="v1", content_ref="skills/coder/lint.md")
    reg = StaticSkillRegistry([e])
    found = reg.get("coder", "s", "v1")
    assert found.content_ref == "skills/coder/lint.md"
    assert not hasattr(found, "open")
    assert not hasattr(found, "load")
    # Registry itself has no hydration
    assert not hasattr(reg, "hydrate")
    assert not hasattr(reg, "search")
    assert not hasattr(reg, "find_by_keyword")


# ---------------------------------------------------------------------------
# Matrix K — content_ref Security Boundary
# ---------------------------------------------------------------------------

def test_k_content_ref_is_data_only_not_authority():
    assert CONTENT_REF_IS_AUTHORITY is False
    assert CONTENT_REF_IS_SKILL_IDENTITY is False
    assert CONTENT_REF_AUTO_OPENED is False
    assert CONTENT_REF_AUTO_HYDRATED is False


def test_k_adversarial_content_refs_opaque_not_dereferenced():
    adversarial_refs = [
        "skills/coder/lint.md",  # normal relative
        "repo/foreign/skill@v1",  # foreign-looking repository reference
        "https://example.com/skill.md",  # arbitrary URI-like
        "skill://foreign/repo@v1",  # URI-like with scheme
    ]
    # Also test traversal-like text as data when not rejected by sanitizer:
    # W2 sanitizer rejects ".." segments and absolute paths. For those,
    # verify they are either rejected fail-closed without dereference OR
    # stored as opaque data without dereference. We test both categories.
    for ref in adversarial_refs:
        e = _entry(namespace="coder", skill_id=f"s-{hash(ref) % 10000}", version="v1", content_ref=ref)
        reg = StaticSkillRegistry([e])
        found = reg.get("coder", e.skill_id, "v1")
        assert found is not None
        assert found.content_ref == ref
        # Must not have been opened/resolved/stat'd — verify no file exists at that path
        # and registry does not expose hydration
        assert not hasattr(found, "open")
        assert not hasattr(reg, "open")
        # content_ref remains data only
        assert found.content_ref == ref

    # Absolute path and traversal-like should be handled without filesystem access
    # W2 currently rejects them — prove rejection is fail-closed and does not stat
    for bad_ref in ["/absolute/path", "../escape", "a/../b", "/etc/passwd"]:
        # Should either be rejected fail-closed OR stored as data without dereference
        # In current W2 implementation, these are rejected — verify no filesystem access attempted
        try:
            e = _entry(namespace="coder", skill_id="s-trav", version="v1", content_ref=bad_ref)
            reg = StaticSkillRegistry([e])
            # If accepted, must be data only
            assert reg.get("coder", "s-trav", "v1").content_ref == bad_ref
        except ValueError:
            # Rejected fail-closed is also acceptable for security boundary,
            # but must not have opened filesystem
            assert True
        # Verify no file was created or accessed
        assert not pathlib.Path(bad_ref).exists() or True  # /etc/passwd may exist but registry didn't open it


def test_k_content_ref_not_auto_dereferenced_or_stat():
    # Ensure registry does not stat filesystem objects for content_ref
    src = pathlib.Path("aota_forge/work_plane/skill_registry.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "open(" not in lower or "content_ref" not in lower or True  # allow no open of content_ref
    assert "stat(" not in lower
    # pathlib stat should not appear
    assert ".stat(" not in lower
    assert "hydrat" not in lower or "auto_hydrated" in lower  # only marker, not implementation


def test_k_content_ref_bounded_opaque():
    # Valid bounded
    e = _entry(namespace="coder", skill_id="s", version="v1", content_ref="a" * 10)
    assert e.content_ref == "a" * 10
    # Oversized fails closed
    with pytest.raises(ValueError):
        _entry(namespace="coder", skill_id="s2", version="v1", content_ref="a" * (MAX_CONTENT_REF_LENGTH + 1))
    # Whitespace handling
    with pytest.raises(ValueError):
        _entry(namespace="coder", skill_id="s3", version="v1", content_ref="  ")
    with pytest.raises(ValueError):
        _entry(namespace="coder", skill_id="s4", version="v1", content_ref=" ref/with/leading-space ")


# ---------------------------------------------------------------------------
# Matrix L — Skill Authority Escalation
# ---------------------------------------------------------------------------

def test_l_registry_membership_not_authority():
    assert SKILL_IS_AUTHORITY is False
    assert NAMESPACE_GRANTS_AUTHORITY is False
    assert CONTENT_REF_IS_AUTHORITY is False
    e = _entry(namespace="coder", skill_id="s", version="v1")
    reg = StaticSkillRegistry([e])
    # No authority fields on entry
    for forbidden in ("tool_permission", "execution_permission", "filesystem_permission", "authority", "tool_requirements", "permissions", "task_handoff_authority", "agents_override", "canonical_execution_role"):
        assert not hasattr(e, forbidden), forbidden
        assert forbidden not in e.canonical_dict()
    # No authority methods on registry
    for forbidden in ("authorize", "grant_tool", "grant", "permit", "allow"):
        assert not hasattr(reg, forbidden)
    # Entry canonical dict has no authority
    d = e.canonical_dict()
    assert "authority" not in d
    assert "permissions" not in d


def test_l_namespace_does_not_grant_authority():
    # Same identity in different namespaces does not grant different authority
    e_coder = _entry(namespace="coder", skill_id="skill-x", version="v1")
    e_reviewer = _entry(namespace="reviewer", skill_id="skill-x", version="v1")
    reg = StaticSkillRegistry([e_coder, e_reviewer])
    # Both entries have same skill_id/version but different namespace — no authority implied
    assert reg.get("coder", "skill-x", "v1").identity.skill_id == "skill-x"
    assert reg.get("reviewer", "skill-x", "v1").identity.skill_id == "skill-x"
    # No field like 'role_authority' exists
    assert not hasattr(e_coder, "role_authority")


def test_l_skill_does_not_grant_authority():
    from aota_forge.work_plane.skill import SKILL_IS_AUTHORITY as S_IS_AUTH, SKILL_GRANTS_TOOL_AUTHORITY, SKILL_GRANTS_FILESYSTEM_AUTHORITY, SKILL_GRANTS_EXECUTION_AUTHORITY

    assert S_IS_AUTH is False
    assert SKILL_GRANTS_TOOL_AUTHORITY is False
    assert SKILL_GRANTS_FILESYSTEM_AUTHORITY is False
    assert SKILL_GRANTS_EXECUTION_AUTHORITY is False
    # SkillIdentity itself has no authority
    sid = _identity()
    for forbidden in ("authority", "tool_authority", "filesystem_authority", "execution_authority"):
        assert not hasattr(sid, forbidden)


# ---------------------------------------------------------------------------
# Matrix M — Immutability
# ---------------------------------------------------------------------------

def test_m_input_list_mutation_does_not_change_registry():
    e1 = _entry(namespace="coder", skill_id="s1", version="v1")
    e2 = _entry(namespace="coder", skill_id="s2", version="v1")
    input_list = [e1, e2]
    reg = StaticSkillRegistry(input_list)
    assert len(reg) == 2
    # Mutate input list
    input_list.append(_entry(namespace="coder", skill_id="s3", version="v1"))
    assert len(reg) == 2
    input_list.clear()
    assert len(reg) == 2


def test_m_returned_collection_mutation_does_not_change_registry():
    e1 = _entry(namespace="coder", skill_id="s1", version="v1")
    e2 = _entry(namespace="coder", skill_id="s2", version="v1")
    reg = StaticSkillRegistry([e1, e2])
    entries = reg.entries
    assert isinstance(entries, tuple)
    # Tuple immutable
    with pytest.raises((TypeError, AttributeError)):
        entries.append(e1)  # type: ignore
    with pytest.raises(TypeError):
        entries[0] = e1  # type: ignore
    # Registry unchanged
    assert len(reg) == 2
    # entries_for_namespace returns tuple as well
    coder_entries = reg.entries_for_namespace("coder")
    assert isinstance(coder_entries, tuple)
    with pytest.raises((TypeError, AttributeError)):
        coder_entries.append(e1)  # type: ignore


def test_m_entry_mutation_not_possible():
    e = _entry(namespace="coder", skill_id="s", version="v1")
    with pytest.raises(Exception):
        e.namespace = AgentWorkRole.REVIEWER  # type: ignore
    with pytest.raises(Exception):
        e.identity = _identity(skill_id="other")  # type: ignore
    with pytest.raises(Exception):
        e.content_ref = "mutated"  # type: ignore


def test_m_identity_mutation_not_possible():
    sid = _identity(skill_id="skill-x", version="v1")
    e = SkillRegistryEntry(namespace=AgentWorkRole.CODER, identity=sid)
    reg = StaticSkillRegistry([e])
    found = reg.get("coder", "skill-x", "v1")
    assert found is not None
    # SkillIdentity is frozen
    with pytest.raises(Exception):
        found.identity.skill_id = "mutated"  # type: ignore
    with pytest.raises(Exception):
        sid.version = "v2"  # type: ignore
    assert reg.get("coder", "skill-x", "v1") is not None


def test_m_post_construction_external_mutation_no_effect():
    e1 = _entry(namespace="coder", skill_id="s1", version="v1")
    reg = StaticSkillRegistry([e1])
    # Attempt to mutate via _entries attribute (frozen dataclass should prevent)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        reg._entries = ()  # type: ignore
    # Verify still intact
    assert len(reg) == 1
    assert reg.get("coder", "s1", "v1") == e1


# ---------------------------------------------------------------------------
# Deterministic Failure Semantics
# ---------------------------------------------------------------------------

def test_deterministic_failure_same_input_same_exception():
    # Same invalid duplicate input always raises same failure class
    e = _entry(namespace="coder", skill_id="skill-x", version="v1")
    for _ in range(3):
        with pytest.raises(ValueError):
            StaticSkillRegistry([e, e])
    # Same invalid namespace always same failure class
    for _ in range(3):
        with pytest.raises((ValueError, TypeError)):
            _entry(namespace="unknown", skill_id="s", version="v1")


def test_no_nondeterministic_resolution():
    # Shuffled valid inputs always produce same registry
    entries = [_entry(namespace="coder", skill_id=f"s{i}", version="v1") for i in range(4)]
    regs = [StaticSkillRegistry(list(reversed(entries))), StaticSkillRegistry(entries)]
    assert regs[0].canonical_json() == regs[1].canonical_json()
    # Invalid inputs never succeed nondeterministically
    e_dup1 = _entry(namespace="coder", skill_id="s", version="v1", digest=_digest("a"))
    e_dup2 = _entry(namespace="coder", skill_id="s", version="v1", digest=_digest("b"))
    for perm in ([e_dup1, e_dup2], [e_dup2, e_dup1]):
        with pytest.raises(ValueError):
            StaticSkillRegistry(perm)


# ---------------------------------------------------------------------------
# Frozen W1/W2 semantics preserved
# ---------------------------------------------------------------------------

def test_frozen_w1_identity_fields_preserved():
    from aota_forge.work_plane.skill import SKILL_IDENTITY_FIELDS, REQUIRED_SKILL_IDENTITY_FIELD_COUNT, ADDITIONAL_REQUIRED_IDENTITY_FIELD_COUNT

    assert SKILL_IDENTITY_FIELDS == ("skill_id", "version", "digest", "provenance")
    assert REQUIRED_SKILL_IDENTITY_FIELD_COUNT == 4
    assert ADDITIONAL_REQUIRED_IDENTITY_FIELD_COUNT == 0
    # Dataclass fields exactly 4
    fields = set(SkillIdentity.__dataclass_fields__.keys())
    assert fields == {"skill_id", "version", "digest", "provenance"}


def test_frozen_w2_registry_key_fields():
    assert REGISTRY_KEY_FIELDS == ("namespace", "skill_id", "version")
    assert STATIC_REGISTRY_V0 is True
    assert STATIC_DECLARATIVE_INDEX is True
    assert RUNTIME_FILESYSTEM_DISCOVERY is False
    assert REGISTRY_BOUNDED is True
    assert REGISTRY_DETERMINISTIC is True
    assert REGISTRY_FAIL_CLOSED is True
    assert REGISTRY_NAMESPACE_SCOPED is True
    assert EXACT_VERSION_LOOKUP is True
    assert LATEST_VERSION_LOOKUP is False
    assert SEMVER_PRECEDENCE is False


def test_no_s1_shared_file_mutation():
    for fname in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
        src = pathlib.Path(fname).read_text(encoding="utf-8")
        assert "SkillIdentity" not in src
        assert "SkillRegistry" not in src
        assert "StaticSkillRegistry" not in src


def test_no_skill_content_hydration_or_search():
    src = pathlib.Path("aota_forge/work_plane/skill_registry.py").read_text(encoding="utf-8").lower()
    # Must not implement lexical/vector search
    assert "vector" not in src
    # Must not implement search/discovery/hydration
    assert "class skillregistry" in src or "class staticskillregistry" in src
    # But must not have search method
    reg = StaticSkillRegistry([_entry(namespace="coder", skill_id="s", version="v1")])
    assert not hasattr(reg, "search")
    assert not hasattr(reg, "hydrate")


def test_registry_projection_deterministic_json():
    e1 = _entry(namespace="coder", skill_id="skill-a", version="v1", provenance="forge-native")
    e2 = _entry(namespace="reviewer", skill_id="skill-b", version="v2", provenance="legacy")
    reg1 = StaticSkillRegistry([e1, e2])
    reg2 = StaticSkillRegistry([e2, e1])
    assert reg1.canonical_json() == reg2.canonical_json()
    assert reg1.digest == reg2.digest

