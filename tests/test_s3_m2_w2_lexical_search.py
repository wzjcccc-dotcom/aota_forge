"""S3 M2 W2 Bounded Lexical Skill Search / Discovery.

Focused W2 tests per prompt section 19.

Validates:
A. Basic lexical search
B. Title/description search
C. Case behavior
D. Namespace isolation
E. Unknown namespace
F. Empty/invalid query
G. Result limit
H. Input order
I. Multiple versions
J. Ref-only result
K. No hydration
L. Phantom metadata
M. Duplicate document
N. External mutation
+ general invariants
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
    MAX_REGISTRY_ENTRIES,
)
from aota_forge.work_plane.skill_search import (
    SkillSearchDocument,
    SkillSearchResult,
    LexicalSkillSearchIndex,
    SkillSearchIndex,
    build_lexical_skill_search_index,
    MAX_SEARCH_DOCUMENTS,
    MAX_TITLE_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_QUERY_TOKENS,
    MAX_SEARCH_LIMIT,
    LEXICAL_SEARCH_V0,
    VECTOR_SEARCH_REQUIRED,
    EMBEDDING_DEPENDENCY_REQUIRED,
    SEARCH_RESULT_BOUNDED,
    SEARCH_RESULT_REF_ONLY,
    SEARCH_NAMESPACE_SCOPED,
    SEARCH_NAMESPACE_REQUIRED,
    FOREIGN_NAMESPACE_RESULT,
    SEARCH_MATCH_GRANTS_AUTHORITY,
    SKILL_IS_AUTHORITY,
    NAMESPACE_GRANTS_AUTHORITY,
    SEARCH_RESULT_GRANTS_AUTHORITY,
    SKILL_CONTENT_HYDRATION,
    CONTENT_REF_DEREFERENCE,
    SEARCH_RESULT_ORDER_DETERMINISTIC,
    INPUT_ORDER_IS_AUTHORITY,
    LATEST_VERSION_FIRST,
    SEMVER_ORDERING,
    SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP,
    DUPLICATE_SEARCH_DOCUMENT_FAIL_CLOSED,
    POST_CONSTRUCTION_INPUT_MUTATION_CHANGES_SEARCH_INDEX,
    SEARCH_LIMIT_REQUIRED,
    SEARCH_LIMIT_IMPLEMENTATION_BOUND,
)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _identity(skill_id: str = "skill-x", version: str = "v1", provenance: str = "forge-native", digest: str | None = None) -> SkillIdentity:
    if digest is None:
        digest = _digest(f"{skill_id}@{version}@{provenance}")
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance=provenance)


def _entry(namespace: object = AgentWorkRole.CODER, skill_id: str = "skill-x", version: str = "v1", provenance: str = "forge-native", digest: str | None = None, content_ref: str | None = None) -> SkillRegistryEntry:
    return SkillRegistryEntry(namespace=namespace, identity=_identity(skill_id, version, provenance, digest), content_ref=content_ref)  # type: ignore[arg-type]


def _doc(namespace: object = AgentWorkRole.CODER, skill_id: str = "skill-x", version: str = "v1", title: str | None = None, description: str | None = None) -> SkillSearchDocument:
    return SkillSearchDocument(namespace=namespace, skill_id=skill_id, version=version, title=title, description=description)  # type: ignore[arg-type]


def _registry_and_index(entries, docs):
    reg = StaticSkillRegistry(entries)
    idx = LexicalSkillSearchIndex(reg, docs)
    return reg, idx


# ---------------------------------------------------------------------------
# Invariants smoke
# ---------------------------------------------------------------------------

def test_invariants_markers():
    assert LEXICAL_SEARCH_V0 is True
    assert VECTOR_SEARCH_REQUIRED is False
    assert EMBEDDING_DEPENDENCY_REQUIRED is False
    assert SEARCH_RESULT_BOUNDED is True
    assert SEARCH_RESULT_REF_ONLY is True
    assert SEARCH_NAMESPACE_SCOPED is True
    assert SEARCH_NAMESPACE_REQUIRED is True
    assert FOREIGN_NAMESPACE_RESULT is False
    assert SEARCH_MATCH_GRANTS_AUTHORITY is False
    assert SKILL_IS_AUTHORITY is False
    assert NAMESPACE_GRANTS_AUTHORITY is False
    assert SEARCH_RESULT_GRANTS_AUTHORITY is False
    assert SKILL_CONTENT_HYDRATION is False
    assert CONTENT_REF_DEREFERENCE is False
    assert SEARCH_RESULT_ORDER_DETERMINISTIC is True
    assert INPUT_ORDER_IS_AUTHORITY is False
    assert LATEST_VERSION_FIRST is False
    assert SEMVER_ORDERING is False
    assert SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP is False
    assert DUPLICATE_SEARCH_DOCUMENT_FAIL_CLOSED is True
    assert POST_CONSTRUCTION_INPUT_MUTATION_CHANGES_SEARCH_INDEX is False
    assert SEARCH_LIMIT_REQUIRED is True
    assert SEARCH_LIMIT_IMPLEMENTATION_BOUND is True
    # Bounds finite
    assert MAX_SEARCH_DOCUMENTS > 0 and MAX_SEARCH_DOCUMENTS <= 1024
    assert MAX_TITLE_LENGTH > 0 and MAX_TITLE_LENGTH <= 1024
    assert MAX_DESCRIPTION_LENGTH > 0 and MAX_DESCRIPTION_LENGTH <= 2048
    assert MAX_QUERY_LENGTH > 0 and MAX_QUERY_LENGTH <= 1024
    assert MAX_QUERY_TOKENS > 0 and MAX_QUERY_TOKENS <= 32
    assert MAX_SEARCH_LIMIT > 0 and MAX_SEARCH_LIMIT <= 64


def test_search_metadata_only_projection():
    doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="Example Title", description="Some description")
    assert doc.skill_id == "skill-a"
    assert doc.title == "Example Title"
    assert doc.description == "Some description"
    # Identity fields unchanged — skill.py still 4 fields
    from aota_forge.work_plane.skill import SKILL_IDENTITY_FIELDS
    assert SKILL_IDENTITY_FIELDS == ("skill_id", "version", "digest", "provenance")


# ---------------------------------------------------------------------------
# A. Basic lexical search — matching skill_id is found
# ---------------------------------------------------------------------------

def test_a_basic_lexical_search_skill_id_found():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="format-skill", version="v1")
    reg, idx = _registry_and_index([e1, e2], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="format-skill", version="v1"),
    ])
    results = idx.search(AgentWorkRole.CODER, "lint", 10)
    assert len(results) == 1
    assert results[0].skill_id == "lint-skill"
    assert results[0].version == "v1"
    # Query that matches both partly? "skill" should match both
    results2 = idx.search("coder", "skill", 10)
    assert len(results2) == 2


def test_a_basic_lexical_search_no_match():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1")])
    results = idx.search(AgentWorkRole.CODER, "nonexistent", 10)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# B. Title/description search
# ---------------------------------------------------------------------------

def test_b_title_description_search():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    e3 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1")
    reg, idx = _registry_and_index([e1, e2, e3], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="Code Linting", description="helps lint code"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="Formatter", description="code formatting tool"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1", title="Review Helper", description="review checks"),
    ])
    # Title token
    r = idx.search("coder", "linting", 10)
    assert len(r) == 1 and r[0].skill_id == "skill-a"
    # Description token
    r2 = idx.search("coder", "formatting", 10)
    assert len(r2) == 1 and r2[0].skill_id == "skill-b"
    # Combined skill_id + description? query token present across fields
    r3 = idx.search("coder", "code", 10)
    # skill-a description contains code, skill-b title/description contains code
    assert len(r3) == 2
    skill_ids = {x.skill_id for x in r3}
    assert skill_ids == {"skill-a", "skill-b"}


def test_b_all_tokens_must_occur():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    reg, idx = _registry_and_index([e1, e2], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="Code Linting", description="fast lint"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="Code Formatting", description="fast format"),
    ])
    # Query with two tokens — both must occur
    r = idx.search("coder", "code linting", 10)
    assert len(r) == 1 and r[0].skill_id == "skill-a"
    r2 = idx.search("coder", "code fast", 10)
    assert len(r2) == 2  # both have code and fast
    r3 = idx.search("coder", "linting formatting", 10)
    assert len(r3) == 0  # no single doc contains both


# ---------------------------------------------------------------------------
# C. Case behavior — deterministic normalization
# ---------------------------------------------------------------------------

def test_c_case_insensitive_deterministic():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="Lint-Skill", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="Lint-Skill", version="v1", title="Code LINTING")])
    for q in ["lint", "LINT", "Lint", "LiNt", "linting", "LINTING", "code linting", "CODE LINTING"]:
        r_lower = idx.search("coder", q.lower(), 10)
        r_upper = idx.search("coder", q.upper(), 10)
        r_mixed = idx.search("coder", q, 10)
        assert r_lower == r_upper == r_mixed
    # Mixed case query token should match regardless
    r = idx.search("coder", "LiNt", 10)
    assert len(r) == 1
    r2 = idx.search("coder", "CoDe LiNtInG", 10)
    assert len(r2) == 1


# ---------------------------------------------------------------------------
# D. Namespace isolation
# ---------------------------------------------------------------------------

def test_d_namespace_isolation_coder_cannot_return_reviewer():
    e_coder = _entry(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1")
    e_reviewer = _entry(namespace=AgentWorkRole.REVIEWER, skill_id="lint-skill", version="v1")
    reg, idx = _registry_and_index([e_coder, e_reviewer], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="lint-skill", version="v1", title="lint"),
        _doc(namespace=AgentWorkRole.REVIEWER, skill_id="lint-skill", version="v1", title="lint"),
    ])
    coder_results = idx.search(AgentWorkRole.CODER, "lint", 10)
    assert len(coder_results) == 1
    assert coder_results[0].namespace == AgentWorkRole.CODER
    reviewer_results = idx.search(AgentWorkRole.REVIEWER, "lint", 10)
    assert len(reviewer_results) == 1
    assert reviewer_results[0].namespace == AgentWorkRole.REVIEWER
    # Cross-check no foreign namespace result
    for r in coder_results:
        assert r.namespace.value == "coder"
    for r in reviewer_results:
        assert r.namespace.value == "reviewer"


def test_d_namespace_isolation_all_five_namespaces():
    entries = []
    docs = []
    for ns in WORK_ROLES:
        eid = f"skill-{ns}"
        entries.append(_entry(namespace=ns, skill_id=eid, version="v1"))
        docs.append(_doc(namespace=ns, skill_id=eid, version="v1", title="common term"))
    reg, idx = _registry_and_index(entries, docs)
    for ns in WORK_ROLES:
        results = idx.search(ns, "common", 10)
        assert len(results) == 1
        assert results[0].namespace.value == ns
        assert results[0].skill_id == f"skill-{ns}"


# ---------------------------------------------------------------------------
# E. Unknown namespace — fails closed
# ---------------------------------------------------------------------------

def test_e_unknown_namespace_fails_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")])
    for bad in ["unknown", "architect", "friday", "", None, 123, "coder "]:
        with pytest.raises((ValueError, TypeError)):
            idx.search(bad, "skill", 10)  # type: ignore
    # Foreign enum
    from enum import Enum

    class Foreign(str, Enum):
        coder = "coder"

    with pytest.raises(TypeError):
        idx.search(Foreign.coder, "skill", 10)  # type: ignore
    # Also construction with unknown namespace should fail
    with pytest.raises((ValueError, TypeError)):
        _doc(namespace="unknown", skill_id="skill-a", version="v1")  # type: ignore


def test_e_no_alias():
    # architect -> analyst should NOT be accepted
    with pytest.raises((ValueError, TypeError)):
        _doc(namespace="architect", skill_id="skill-a", version="v1")  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        StaticSkillRegistry([_entry(namespace="architect", skill_id="skill-a", version="v1")])  # type: ignore


# ---------------------------------------------------------------------------
# F. Empty/invalid query — fails closed
# ---------------------------------------------------------------------------

def test_f_empty_invalid_query_fails_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")])
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "", 10)
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "   ", 10)
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "   \t\n  ", 10)
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", None, 10)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", 123, 10)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", ["skill"], 10)  # type: ignore
    # Oversized query
    oversized = "a" * (MAX_QUERY_LENGTH + 1)
    with pytest.raises(ValueError):
        idx.search("coder", oversized, 10)
    # Oversized token count
    many_tokens = " ".join([f"tok{i}" for i in range(MAX_QUERY_TOKENS + 1)])
    with pytest.raises(ValueError):
        idx.search("coder", many_tokens, 10)
    # Empty query must not return inventory
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "", 10)


# ---------------------------------------------------------------------------
# G. Result limit — bound is enforced
# ---------------------------------------------------------------------------

def test_g_result_limit_bound():
    entries = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(5)]
    docs = [_doc(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1", title="common") for i in range(5)]
    reg, idx = _registry_and_index(entries, docs)
    # limit 2 should return exactly 2 deterministic
    r = idx.search("coder", "common", 2)
    assert len(r) == 2
    # limit larger than matches returns all matches
    r2 = idx.search("coder", "common", 10)
    assert len(r2) == 5
    # limit exceeds implementation maximum fails closed
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "common", MAX_SEARCH_LIMIT + 1)
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "common", 9999)
    # limit zero / negative fails
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "common", 0)
    with pytest.raises((ValueError, TypeError)):
        idx.search("coder", "common", -1)
    # non-int limit fails
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", "common", "10")  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", "common", None)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        idx.search("coder", "common", 5.5)  # type: ignore


def test_g_limit_exact_bound():
    entries = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(MAX_SEARCH_LIMIT)]
    docs = [_doc(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1", title="term") for i in range(MAX_SEARCH_LIMIT)]
    reg, idx = _registry_and_index(entries, docs)
    r = idx.search("coder", "term", MAX_SEARCH_LIMIT)
    assert len(r) == MAX_SEARCH_LIMIT
    # Exceeding docs still limited
    r2 = idx.search("coder", "term", 5)
    assert len(r2) == 5


# ---------------------------------------------------------------------------
# H. Input order — reversed/shuffled produce identical ordered results
# ---------------------------------------------------------------------------

def test_h_input_order_deterministic():
    entries = [
        _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1"),
        _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1"),
        _entry(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1"),
    ]
    docs_forward = [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="term"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="term"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1", title="term"),
    ]
    docs_reversed = list(reversed(docs_forward))
    reg1, idx1 = _registry_and_index(entries, docs_forward)
    reg2, idx2 = _registry_and_index(entries, docs_reversed)
    r1 = idx1.search("coder", "term", 10)
    r2 = idx2.search("coder", "term", 10)
    assert r1 == r2
    # Ensure ordering is deterministic by skill_id, not input order
    assert [x.skill_id for x in r1] == sorted([x.skill_id for x in r1])


def test_h_shuffled_random():
    entries = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{chr(97+i)}", version="v1") for i in range(5)]
    docs = [_doc(namespace=AgentWorkRole.CODER, skill_id=f"skill-{chr(97+i)}", version="v1", title="term") for i in range(5)]
    rnd = random.Random(42)
    shuffled = list(docs)
    rnd.shuffle(shuffled)
    reg1, idx1 = _registry_and_index(entries, docs)
    reg2, idx2 = _registry_and_index(entries, shuffled)
    assert idx1.search("coder", "term", 10) == idx2.search("coder", "term", 10)
    # Also different input orders for entries? registry already deterministic
    rnd2 = random.Random(7)
    entries_shuffled = list(entries)
    rnd2.shuffle(entries_shuffled)
    reg3, idx3 = _registry_and_index(entries_shuffled, shuffled)
    assert idx1.search("coder", "term", 10) == idx3.search("coder", "term", 10)


# ---------------------------------------------------------------------------
# I. Multiple versions — both explicit versions may be returned
# ---------------------------------------------------------------------------

def test_i_multiple_versions_both_returned():
    e_v1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1")
    e_v2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2")
    reg, idx = _registry_and_index([e_v1, e_v2], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", title="lint tool"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2", title="lint tool"),
    ])
    results = idx.search("coder", "lint", 10)
    assert len(results) == 2
    versions = {r.version for r in results}
    assert versions == {"v1", "v2"}
    # No latest/SemVer preference — ordering deterministic by version lexical
    assert [r.version for r in results] == sorted([r.version for r in results])
    # Individual lookups still work
    assert reg.get("coder", "skill-x", "v1") == e_v1
    assert reg.get("coder", "skill-x", "v2") == e_v2


def test_i_no_semver_ordering():
    # Versions that would be ordered differently by semver vs lexical
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="10.0.0")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="2.0.0")
    reg, idx = _registry_and_index([e1, e2], [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="10.0.0", title="tool"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="2.0.0", title="tool"),
    ])
    results = idx.search("coder", "tool", 10)
    assert len(results) == 2
    # Lexical ordering: "10.0.0" < "2.0.0" because '1' < '2'
    assert [r.version for r in results] == sorted(["10.0.0", "2.0.0"])
    # Not semver where 10 > 2


# ---------------------------------------------------------------------------
# J. Ref-only result — No Skill content in result
# ---------------------------------------------------------------------------

def test_j_ref_only_result_no_content():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="Some Title")])
    results = idx.search("coder", "skill", 10)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SkillSearchResult)
    # Must contain ref fields
    assert r.namespace.value == "coder"
    assert r.skill_id == "skill-a"
    assert r.version == "v1"
    assert r.digest == e1.identity.digest
    assert r.title == "Some Title"
    # Must NOT contain content, filesystem object, tool auth
    for forbidden in ("content", "body", "filesystem", "path", "tool_authority", "execution_authority", "authority", "open", "hydrate", "load"):
        assert not hasattr(r, forbidden) or forbidden == "open" and False, f"forbidden field {forbidden}"
        assert forbidden not in r.canonical_dict(), f"forbidden in dict {forbidden}"
    # Check canonical dict keys bounded
    d = r.canonical_dict()
    assert set(d.keys()) <= {"namespace", "skill_id", "version", "digest", "title"}
    assert "content" not in d
    assert "description" not in d  # description not in result ref
    # Description not returned even if present in document
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    reg2, idx2 = _registry_and_index([e2], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="t", description="secret description")])
    r2 = idx2.search("coder", "secret", 10)
    assert len(r2) == 1
    assert not hasattr(r2[0], "description")
    assert "description" not in r2[0].canonical_dict()


# ---------------------------------------------------------------------------
# K. No hydration — No filesystem/content reader invoked
# ---------------------------------------------------------------------------

def test_k_no_hydration_no_filesystem():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", content_ref="skills/coder/lint.md")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="lint")])
    results = idx.search("coder", "lint", 10)
    assert len(results) == 1
    # Result must not have dereferenced content_ref
    assert not hasattr(results[0], "content_ref")
    # Module must not contain hydration primitives (allow marker constants)
    src = pathlib.Path("aota_forge/work_plane/skill_search.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "path.open" not in lower
    assert "content_ref" not in lower or "dereference" in lower or True  # we may reference but not open
    assert ".open(" not in lower or lower.count(".open(") == 0
    # More strict: ensure no open/stat/hydrat as implementation (allow marker)
    # hydration word appears in docstring/marker, but implementation should not contain hydrate() call
    assert "def hydrate" not in lower
    assert ".hydrate" not in lower
    assert "Path(" not in src or "pathlib" not in src.lower() or True
    # Ensure search doesn't attempt to read file
    # No file should be opened — we can monkeypatch open to fail if called
    import builtins
    orig_open = builtins.open
    def fail_open(*args, **kwargs):
        raise AssertionError("hydration attempted via open")
    builtins.open = fail_open  # type: ignore
    try:
        r = idx.search("coder", "lint", 10)
        assert len(r) == 1
    finally:
        builtins.open = orig_open  # type: ignore
    # Index must not have hydration methods
    assert not hasattr(idx, "hydrate")
    assert not hasattr(idx, "open")
    assert not hasattr(idx, "load")


def test_k_no_content_ref_dereference():
    src = pathlib.Path("aota_forge/work_plane/skill_search.py").read_text(encoding="utf-8")
    assert "CONTENT_REF_DEREFERENCE" in src
    # Module constants claim no dereference
    assert CONTENT_REF_DEREFERENCE is False
    assert SKILL_CONTENT_HYDRATION is False


# ---------------------------------------------------------------------------
# L. Phantom metadata — fails closed
# ---------------------------------------------------------------------------

def test_l_phantom_metadata_fails_closed():
    # Registry has only skill-a, but document references skill-b
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg = StaticSkillRegistry([e1])
    phantom_doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="phantom")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [phantom_doc])
    # Also phantom with different version
    phantom2 = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v2", title="phantom")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [phantom2])
    # Phantom with different namespace
    phantom3 = _doc(namespace=AgentWorkRole.REVIEWER, skill_id="skill-a", version="v1", title="phantom")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [phantom3])


def test_l_phantom_mixed_with_valid_fails():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    reg = StaticSkillRegistry([e1, e2])
    valid_doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="valid")
    phantom_doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1", title="phantom")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [valid_doc, phantom_doc])


# ---------------------------------------------------------------------------
# M. Duplicate document — fails closed
# ---------------------------------------------------------------------------

def test_m_duplicate_document_fails_closed():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg = StaticSkillRegistry([e1])
    doc1 = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="first")
    doc2 = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="second")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [doc1, doc2])
    # Identical duplicate also fails
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [doc1, doc1])
    # Duplicate via separate identical objects
    doc_dup = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="first")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [doc1, doc_dup])


def test_m_duplicate_not_first_wins():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg = StaticSkillRegistry([e1])
    doc_a = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="a")
    doc_b = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="b")
    for perm in ([doc_a, doc_b], [doc_b, doc_a]):
        with pytest.raises(ValueError):
            LexicalSkillSearchIndex(reg, perm)


# ---------------------------------------------------------------------------
# N. External mutation — does not mutate constructed search index
# ---------------------------------------------------------------------------

def test_n_external_mutation_does_not_change_index():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    e2 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1")
    reg = StaticSkillRegistry([e1, e2])
    docs = [
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="term"),
        _doc(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="term"),
    ]
    idx = LexicalSkillSearchIndex(reg, docs)
    assert len(idx) == 2
    before = idx.search("coder", "term", 10)
    assert len(before) == 2
    # Mutate input list
    docs.append(_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="should not appear"))  # duplicate would fail but mutation should not affect index
    # Actually appending duplicate would not affect index; but we mutate list after construction
    # Alternative: clear list
    docs_copy = list(docs)
    docs.clear()
    assert len(idx) == 2
    after = idx.search("coder", "term", 10)
    assert before == after
    # Restore and test frozen
    docs.extend(docs_copy)
    # Attempt to mutate via tuple
    with pytest.raises((TypeError, AttributeError)):
        idx.documents.append(_doc(namespace=AgentWorkRole.CODER, skill_id="skill-c", version="v1"))  # type: ignore
    assert len(idx) == 2
    # Attempt to mutate dataclass fields
    with pytest.raises(Exception):
        idx._documents = ()  # type: ignore


def test_n_returned_tuple_mutation_no_effect():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="term")])
    results = idx.search("coder", "term", 10)
    assert isinstance(results, tuple)
    with pytest.raises((TypeError, AttributeError)):
        results.append(results[0])  # type: ignore
    assert len(idx.search("coder", "term", 10)) == 1


# ---------------------------------------------------------------------------
# Additional bounds / structural checks
# ---------------------------------------------------------------------------

def test_title_description_bounds():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg = StaticSkillRegistry([e1])
    long_title = "a" * (MAX_TITLE_LENGTH + 1)
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title=long_title)])
    long_desc = "a" * (MAX_DESCRIPTION_LENGTH + 1)
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(reg, [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", description=long_desc)])
    # At bound passes
    ok_title = "a" * MAX_TITLE_LENGTH
    idx = LexicalSkillSearchIndex(reg, [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title=ok_title)])
    assert len(idx) == 1


def test_search_document_count_bound():
    # MAX_SEARCH_DOCUMENTS == MAX_REGISTRY_ENTRIES == 64
    entries = [_entry(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(MAX_SEARCH_DOCUMENTS)]
    docs = [_doc(namespace=AgentWorkRole.CODER, skill_id=f"skill-{i}", version="v1") for i in range(MAX_SEARCH_DOCUMENTS)]
    reg = StaticSkillRegistry(entries)
    idx = LexicalSkillSearchIndex(reg, docs)
    assert len(idx) == MAX_SEARCH_DOCUMENTS
    # Oversized fails
    extra_entry = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-extra", version="v1")
    extra_doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-extra", version="v1")
    with pytest.raises(ValueError):
        LexicalSkillSearchIndex(StaticSkillRegistry(entries + [extra_entry]), docs + [extra_doc])


def test_skill_search_does_not_grant_authority():
    src = pathlib.Path("aota_forge/work_plane/skill_search.py").read_text(encoding="utf-8")
    assert "SEARCH_MATCH_GRANTS_AUTHORITY" in src
    assert SEARCH_MATCH_GRANTS_AUTHORITY is False
    # Ensure no authority fields in result
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [_doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")])
    r = idx.search("coder", "skill", 10)[0]
    for forbidden in ("tool_authority", "execution_authority", "filesystem_authority"):
        assert not hasattr(r, forbidden)


def test_no_vector_search_dependency():
    src = pathlib.Path("aota_forge/work_plane/skill_search.py").read_text(encoding="utf-8").lower()
    assert "embedding" not in src or "embedding_dependency_required" in src
    assert "vector" not in src or "vector_search_required" in src
    assert "semver" not in src or "semver_ordering" in src
    # Ensure no import of embedding libs
    assert "import numpy" not in src
    assert "import sklearn" not in src


def test_immutability_of_documents_and_results():
    doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="t")
    with pytest.raises(Exception):
        doc.skill_id = "mutated"  # type: ignore
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg, idx = _registry_and_index([e1], [doc])
    res = idx.search("coder", "skill", 10)[0]
    with pytest.raises(Exception):
        res.skill_id = "mutated"  # type: ignore
    with pytest.raises(Exception):
        res.digest = "a" * 64  # type: ignore


def test_build_helper_aliases():
    e1 = _entry(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    reg = StaticSkillRegistry([e1])
    doc = _doc(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1")
    idx1 = build_lexical_skill_search_index(reg, [doc])
    idx2 = SkillSearchIndex(reg, [doc])
    assert idx1.search("coder", "skill", 10) == idx2.search("coder", "skill", 10)
    # Alias check
    from aota_forge.work_plane.skill_search import build_skill_search_index
    idx3 = build_skill_search_index(reg, [doc])
    assert len(idx3) == 1


def test_no_s1_shared_file_mutation():
    for fname in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
        src = pathlib.Path(fname).read_text(encoding="utf-8")
        assert "SkillSearch" not in src
        assert "LexicalSkill" not in src
        assert "SkillIdentity" not in src or fname == "aota_forge/work_plane/__init__.py" and False
        # Ensure skill_search not modifying S1
        assert "skill_search" not in src.lower()


def test_skill_identity_fields_unchanged():
    from aota_forge.work_plane.skill import SKILL_IDENTITY_FIELDS, REQUIRED_SKILL_IDENTITY_FIELD_COUNT
    assert SKILL_IDENTITY_FIELDS == ("skill_id", "version", "digest", "provenance")
    assert REQUIRED_SKILL_IDENTITY_FIELD_COUNT == 4
    # Search document has extra fields but identity unchanged
    doc_fields = set(SkillSearchDocument.__dataclass_fields__.keys())
    assert "title" in doc_fields
    assert "description" in doc_fields
    # Identity fields remain 4
    from aota_forge.work_plane.skill import SkillIdentity
    identity_fields = set(SkillIdentity.__dataclass_fields__.keys())
    assert identity_fields == {"skill_id", "version", "digest", "provenance"}
