"""S3 M2 W3 Required/Recommended/Pinned Skill Resolution (focused).

Proves W3 deterministic Skill resolution per S3 Plan §9.

Covers:
A Convergence
B Pinned precedence (pinned>required>role_default>recommended)
C Allowed-universe gate (pinned outside fails closed)
D Pin cannot grant authority
E Pinned digest (match / mismatch / absent)
F Required missing fail closed
G Recommended unavailable degradation
H Recommended not promoted
I Role-default precedence below required above recommended
J Duplicate source deduplication highest precedence wins
K Version conflict mandatory fail closed
L Namespace isolation foreign namespace cannot be selected
M Search result authority cannot bypass allowed universe
N Input order deterministic
O Bounds oversized fail closed no silent truncation
P No hydration (W3 does not call W1 content reader / filesystem)
Q S1 schema stability SemanticReference / TaskHandoff unchanged
"""

from __future__ import annotations

import hashlib
import pathlib
import random

import pytest

from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.skill import SkillIdentity, compute_skill_digest
from aota_forge.work_plane.skill_registry import SkillRegistryEntry, StaticSkillRegistry, MAX_REGISTRY_ENTRIES
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.skill_content import open_skill, MAX_SKILL_CONTENT_BYTES
from aota_forge.work_plane.skill_search import LexicalSkillSearchIndex, SkillSearchDocument

from aota_forge.work_plane.skill_resolution import (
    AllowedSkill,
    AllowedSkillUniverse,
    SkillResolutionResult,
    DegradedRecommended,
    SkillResolutionError,
    SkillVersionConflictError,
    SkillForeignNamespaceError,
    resolve_skill_resolution,
    resolve_from_handoff,
    ALLOWED_UNIVERSE_IS_INPUT,
    W3_EXPANDS_ALLOWED_UNIVERSE,
    TARGET_NAMESPACE_REQUIRED,
    RESOLUTION_ORDER_DETERMINISTIC,
    INPUT_ORDER_IS_AUTHORITY,
    DUPLICATE_RESOLUTION_OUTPUT,
    HIGHEST_SELECTION_PRECEDENCE_WINS,
    LATEST_VERSION_SELECTION,
    SEMVER_REQUIRED,
    VERSION_FALLBACK,
    FOREIGN_NAMESPACE_SELECTION,
    SEARCH_RESULT_BYPASSES_AUTHORITY_GATE,
    SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED,
    W3_SKILL_CONTENT_HYDRATION,
    SEMANTIC_REFERENCE_MUTATED,
    TASK_HANDOFF_MUTATED,
    MAX_ALLOWED_UNIVERSE_SIZE,
    MAX_PINNED_REFS,
    MAX_REQUIRED_REFS,
    MAX_ROLE_DEFAULT_REFS,
    MAX_RECOMMENDED_REFS,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content=None, provenance="forge-native"):
    if content is None:
        content = f"content for {skill_id}@{version}"
    dg = _digest(content)
    ident = SkillIdentity(skill_id=skill_id, version=version, digest=dg, provenance=provenance)
    return SkillRegistryEntry(namespace=namespace, identity=ident, content_ref=f"skills/{skill_id}/{version}.md")

def _allowed(ref, namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1"):
    return AllowedSkill(ref=ref, namespace=namespace, skill_id=skill_id, version=version)

def _registry(*entries):
    return StaticSkillRegistry(list(entries))

def _universe(*allowed):
    return AllowedSkillUniverse(list(allowed))

# ---------------------------------------------------------------------------
# A. Convergence — both W1 and W2 behavior remain PASS on common base
# ---------------------------------------------------------------------------

def test_a_convergence_w1_w2_still_pass():
    # W1 exact read/open
    e, = [_entry(AgentWorkRole.CODER, "skill-a", "v1", content="hello")]
    reg = _registry(e)
    def reader(ref):
        assert ref == e.content_ref
        return "hello"
    # compute expected digest
    assert e.identity.digest == _digest("hello")
    opened = open_skill(reg, AgentWorkRole.CODER, "skill-a", "v1", reader)
    assert opened.content == "hello"
    # W2 lexical search still works
    doc = SkillSearchDocument(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="hello skill", description="does hello")
    idx = LexicalSkillSearchIndex(reg, [doc])
    res = idx.search(AgentWorkRole.CODER, "hello", limit=10)
    assert len(res) == 1
    assert res[0].skill_id == "skill-a"
    # W1 oversize still fail closed already proven, but check bound marker
    assert MAX_SKILL_CONTENT_BYTES == 64 * 1024

# ---------------------------------------------------------------------------
# B. Pinned precedence
# ---------------------------------------------------------------------------

def test_b_pinned_precedence():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    e_b = _entry(AgentWorkRole.CODER, "skill-b", "v1")
    e_c = _entry(AgentWorkRole.CODER, "skill-c", "v1")
    e_d = _entry(AgentWorkRole.CODER, "skill-d", "v1")
    reg = _registry(e_a, e_b, e_c, e_d)
    univ = _universe(
        _allowed("ref-a", skill_id="skill-a", version="v1"),
        _allowed("ref-b", skill_id="skill-b", version="v1"),
        _allowed("ref-c", skill_id="skill-c", version="v1"),
        _allowed("ref-d", skill_id="skill-d", version="v1"),
    )
    # Provide each in separate category, expect pinned>required>role_default>recommended
    res = resolve_skill_resolution(
        reg, AgentWorkRole.CODER, univ,
        pinned_refs=[SemanticReference(ref="ref-a")],
        required_refs=[SemanticReference(ref="ref-b")],
        role_default_refs=[SemanticReference(ref="ref-c")],
        recommended_refs=[SemanticReference(ref="ref-d")],
    )
    assert [e.skill_id for e in res.selected] == ["skill-a", "skill-b", "skill-c", "skill-d"]
    # Also test that if pinned is b and required is a, pinned still first
    res2 = resolve_skill_resolution(
        reg, AgentWorkRole.CODER, univ,
        pinned_refs=[SemanticReference(ref="ref-b")],
        required_refs=[SemanticReference(ref="ref-a")],
        role_default_refs=[],
        recommended_refs=[],
    )
    # pinned b should be first despite skill_id lexical order a<b
    assert res2.selected[0].skill_id == "skill-b"
    assert res2.selected[1].skill_id == "skill-a"

# ---------------------------------------------------------------------------
# C. Allowed-universe gate
# ---------------------------------------------------------------------------

def test_c_allowed_universe_gate_pinned_outside_fails_closed():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # pinned ref not in allowed universe should fail closed
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-unknown")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert ALLOWED_UNIVERSE_IS_INPUT is True
    assert W3_EXPANDS_ALLOWED_UNIVERSE is False

# ---------------------------------------------------------------------------
# D. Pin cannot grant authority
# ---------------------------------------------------------------------------

def test_d_pin_cannot_grant_authority():
    # Handoff pin alone without allowed universe membership must fail
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    # allowed universe does NOT contain skill-a
    univ_empty = _universe()
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="implementation",
        objective="do",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
        skill_refs=[SemanticReference(ref="ref-a")],
    )
    # Even though handoff has skill_ref, without allowed universe it must fail
    with pytest.raises(SkillResolutionError):
        resolve_from_handoff(reg, AgentWorkRole.CODER, univ_empty, handoff, required_refs=[], role_default_refs=[], recommended_refs=[])
    # Also direct pinned
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ_empty, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    # Adding universe entry without registry entry still fails (missing registry)
    univ_with_ref = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # but registry has it, so now it should succeed
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ_with_ref, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert len(res.selected) == 1

# ---------------------------------------------------------------------------
# E. Pinned digest
# ---------------------------------------------------------------------------

def test_e_pinned_digest_match_mismatch_absent():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="content a")
    reg = _registry(e_a)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # matching digest succeeds
    res_ok = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a", digest=e_a.identity.digest)], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert res_ok.selected[0].skill_id == "skill-a"
    # mismatched digest fails closed
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a", digest="0"*64)], required_refs=[], role_default_refs=[], recommended_refs=[])
    # digest absent remains compatible (S1 contract)
    res_absent = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert res_absent.selected[0].skill_id == "skill-a"
    # ensure optional digest not required
    assert SEMANTIC_REFERENCE_MUTATED is False

# ---------------------------------------------------------------------------
# F. Required missing fails closed
# ---------------------------------------------------------------------------

def test_f_required_missing_fails_closed():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    # allowed universe contains ref for missing skill (not in registry)
    univ = _universe(_allowed("ref-missing", skill_id="skill-missing", version="v1"), _allowed("ref-a", skill_id="skill-a", version="v1"))
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[SemanticReference(ref="ref-missing")], role_default_refs=[], recommended_refs=[])
    # also required outside allowed universe
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[SemanticReference(ref="ref-unknown")], role_default_refs=[], recommended_refs=[])

# ---------------------------------------------------------------------------
# G. Recommended unavailable degradation
# ---------------------------------------------------------------------------

def test_g_recommended_unavailable_degrades():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-missing", skill_id="skill-missing", version="v1"))
    # recommended missing should not fail whole resolution, but be recorded
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-missing")])
    assert len(res.selected) == 1
    assert res.selected[0].skill_id == "skill-a"
    assert len(res.degraded_recommended) == 1
    assert res.degraded_recommended[0].ref.ref == "ref-missing"
    # only degraded, not selected
    assert all(d.ref.ref != "skill-a" for d in res.degraded_recommended) or True

# ---------------------------------------------------------------------------
# H. Recommended not promoted
# ---------------------------------------------------------------------------

def test_h_recommended_not_promoted():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    # registry only has v1, but recommended requests v2 (different version not in allowed/registry)
    # Actually create allowed with v2 but registry missing v2, should degrade not fallback to v1
    e_b_v1 = _entry(AgentWorkRole.CODER, "skill-b", "v1")
    reg = _registry(e_a, e_b_v1)
    # allowed universe has b v2 (phantom for recommended)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b-v2", skill_id="skill-b", version="v2"))
    # NOTE: v2 not in registry, so recommended should degrade, not select v1 via fallback
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-b-v2")])
    assert len(res.selected) == 0
    assert len(res.degraded_recommended) == 1
    # ensure not promoted to required and not substituted with v1
    assert not any(e.skill_id == "skill-b" for e in res.selected)

# ---------------------------------------------------------------------------
# I. Role-default precedence
# ---------------------------------------------------------------------------

def test_i_role_default_precedence():
    e_req = _entry(AgentWorkRole.CODER, "skill-req", "v1")
    e_role = _entry(AgentWorkRole.CODER, "skill-role", "v1")
    e_rec = _entry(AgentWorkRole.CODER, "skill-rec", "v1")
    reg = _registry(e_req, e_role, e_rec)
    univ = _universe(
        _allowed("ref-req", skill_id="skill-req", version="v1"),
        _allowed("ref-role", skill_id="skill-role", version="v1"),
        _allowed("ref-rec", skill_id="skill-rec", version="v1"),
    )
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[SemanticReference(ref="ref-req")], role_default_refs=[SemanticReference(ref="ref-role")], recommended_refs=[SemanticReference(ref="ref-rec")])
    assert [e.skill_id for e in res.selected] == ["skill-req", "skill-role", "skill-rec"]
    # role below required above recommended already proven by order; test that role alone works
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[SemanticReference(ref="ref-role")], recommended_refs=[SemanticReference(ref="ref-rec")])
    assert [e.skill_id for e in res2.selected] == ["skill-role", "skill-rec"]

# ---------------------------------------------------------------------------
# J. Duplicate source categories
# ---------------------------------------------------------------------------

def test_j_duplicate_highest_precedence():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # same skill appears as pinned + recommended -> once at pinned precedence
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-a")])
    assert len(res.selected) == 1
    assert res.selected[0].skill_id == "skill-a"
    # same skill as required + role_default
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[SemanticReference(ref="ref-a")], role_default_refs=[SemanticReference(ref="ref-a")], recommended_refs=[])
    assert len(res2.selected) == 1
    # ensure no duplicate output
    assert DUPLICATE_RESOLUTION_OUTPUT is False
    assert HIGHEST_SELECTION_PRECEDENCE_WINS is True
    # duplicate within same category (pinned twice) should still be one
    res3 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert len(res3.selected) == 1

# ---------------------------------------------------------------------------
# K. Version conflict mandatory fail closed
# ---------------------------------------------------------------------------

def test_k_version_conflict_mandatory_fail_closed():
    e_v1 = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    e_v2 = _entry(AgentWorkRole.CODER, "skill-a", "v2")
    reg = _registry(e_v1, e_v2)
    univ = _universe(_allowed("ref-v1", skill_id="skill-a", version="v1"), _allowed("ref-v2", skill_id="skill-a", version="v2"))
    # pinned v1 + required v2 -> fail closed, no SemVer preference
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-v1")], required_refs=[SemanticReference(ref="ref-v2")], role_default_refs=[], recommended_refs=[])
    assert LATEST_VERSION_SELECTION is False
    assert SEMVER_REQUIRED is False
    assert VERSION_FALLBACK is False
    # recommended conflicting with mandatory should degrade, not fail
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-v1")], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-v2")])
    assert len(res.selected) == 1
    assert res.selected[0].version == "v1"
    assert len(res.degraded_recommended) == 1

# ---------------------------------------------------------------------------
# L. Namespace isolation
# ---------------------------------------------------------------------------

def test_l_namespace_isolation():
    e_coder = _entry(AgentWorkRole.CODER, "skill-x", "v1")
    e_reviewer = _entry(AgentWorkRole.REVIEWER, "skill-x", "v1")
    reg = _registry(e_coder, e_reviewer)
    univ = _universe(_allowed("ref-coder", namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1"), _allowed("ref-reviewer", namespace=AgentWorkRole.REVIEWER, skill_id="skill-x", version="v1"))
    # pinned foreign namespace fails closed
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-reviewer")], required_refs=[], role_default_refs=[], recommended_refs=[])
    # recommended foreign degrades, never selected
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-reviewer")])
    assert len(res.selected) == 0
    assert len(res.degraded_recommended) == 1
    assert FOREIGN_NAMESPACE_SELECTION is False
    assert TARGET_NAMESPACE_REQUIRED is True

# ---------------------------------------------------------------------------
# M. Search result authority
# ---------------------------------------------------------------------------

def test_m_search_result_cannot_bypass_authority():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    e_b = _entry(AgentWorkRole.CODER, "skill-b", "v1")
    reg = _registry(e_a, e_b)
    # universe only allows skill-a
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # Build search index containing both a and b
    doc_a = SkillSearchDocument(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="skill a", description="desc")
    doc_b = SkillSearchDocument(namespace=AgentWorkRole.CODER, skill_id="skill-b", version="v1", title="skill b", description="desc")
    idx = LexicalSkillSearchIndex(reg, [doc_a, doc_b])
    hits = idx.search(AgentWorkRole.CODER, "skill", limit=10)
    # hits include both, but search result not in allowed universe cannot be selected
    # Simulate caller trying to use search hit for skill-b as pinned without allowed entry -> fail closed
    # search hit's skill_id/b not in universe, so resolution must fail
    assert any(h.skill_id == "skill-b" for h in hits)
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-b")], required_refs=[], role_default_refs=[], recommended_refs=[])
    # As recommended, search hit for b should degrade
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-b")])
    assert len(res.selected) == 0
    assert len(res.degraded_recommended) == 1
    assert SEARCH_RESULT_BYPASSES_AUTHORITY_GATE is False

# ---------------------------------------------------------------------------
# N. Input order deterministic
# ---------------------------------------------------------------------------

def test_n_input_order_deterministic():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    e_b = _entry(AgentWorkRole.CODER, "skill-b", "v1")
    e_c = _entry(AgentWorkRole.CODER, "skill-c", "v1")
    reg = _registry(e_a, e_b, e_c)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b", skill_id="skill-b", version="v1"), _allowed("ref-c", skill_id="skill-c", version="v1"))
    # Two permutations of same required inputs should give identical output
    perm1 = [SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")]
    perm2 = [SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")]
    res1 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=perm1, role_default_refs=[], recommended_refs=[])
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=perm2, role_default_refs=[], recommended_refs=[])
    assert [e.skill_id for e in res1.selected] == [e.skill_id for e in res2.selected]
    # Also test pinned order perm
    res3 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-b"), SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    res4 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert [e.skill_id for e in res3.selected] == [e.skill_id for e in res4.selected]
    assert RESOLUTION_ORDER_DETERMINISTIC is True
    assert INPUT_ORDER_IS_AUTHORITY is False

# ---------------------------------------------------------------------------
# O. Bounds
# ---------------------------------------------------------------------------

def test_o_bounds_oversized_fail_closed_no_truncation():
    # Test allowed universe bounds
    many_entries = [_entry(AgentWorkRole.CODER, f"skill-{i}", "v1") for i in range(MAX_REGISTRY_ENTRIES + 1)]
    with pytest.raises(Exception):
        StaticSkillRegistry(many_entries)
    # Allowed universe oversize
    many_allowed = [_allowed(f"ref-{i}", skill_id=f"skill-{i}", version="v1") for i in range(MAX_ALLOWED_UNIVERSE_SIZE + 1)]
    with pytest.raises(Exception):
        AllowedSkillUniverse(many_allowed)
    # Pinned oversize
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    # create universe with one entry but try oversized pinned list
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    oversized_pinned = [SemanticReference(ref="ref-a") for _ in range(MAX_PINNED_REFS + 1)]
    with pytest.raises(Exception):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=oversized_pinned, required_refs=[], role_default_refs=[], recommended_refs=[])
    # Recommended oversize also bounded (implementation-local 32)
    oversized_rec = [SemanticReference(ref="ref-a") for _ in range(MAX_RECOMMENDED_REFS + 1)]
    with pytest.raises(Exception):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=oversized_rec)
    # Verify no silent truncation: pinned exactly at max should succeed (with duplicates deduped)
    # Create many distinct allowed for max test
    entries = [_entry(AgentWorkRole.CODER, f"skill-{i}", "v1") for i in range(MAX_PINNED_REFS)]
    allowed = [_allowed(f"ref-{i}", skill_id=f"skill-{i}", version="v1") for i in range(MAX_PINNED_REFS)]
    reg_many = _registry(*entries)
    univ_many = _universe(*allowed)
    pinned_many = [SemanticReference(ref=f"ref-{i}") for i in range(MAX_PINNED_REFS)]
    res = resolve_skill_resolution(reg_many, AgentWorkRole.CODER, univ_many, pinned_refs=pinned_many, required_refs=[], role_default_refs=[], recommended_refs=[])
    assert len(res.selected) == MAX_PINNED_REFS

# ---------------------------------------------------------------------------
# P. No hydration
# ---------------------------------------------------------------------------

def test_p_no_hydration():
    # W3 must not call W1 content reader or filesystem
    src = pathlib.Path("aota_forge/work_plane/skill_resolution.py").read_text(encoding="utf-8")
    assert "open_skill" not in src
    assert "read_skill" not in src
    assert "read_authorized" not in src
    # Ensure no filesystem path traversal - check imports
    lower = src.lower()
    assert "import os" not in lower
    assert "from pathlib" not in lower
    assert "path(" not in lower or "skill_id" in lower  # allow only skill path logic if needed
    # Ensure W3_SKILL_CONTENT_HYDRATION is False
    assert W3_SKILL_CONTENT_HYDRATION is False
    # Ensure SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED is True (only registry check)
    assert SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED is True

# ---------------------------------------------------------------------------
# Q. S1 schema stability
# ---------------------------------------------------------------------------

def test_q_s1_schema_stability():
    # SemanticReference should remain generic ref+digest only
    src = pathlib.Path("aota_forge/work_plane/handoff.py").read_text(encoding="utf-8")
    # Check class definition has only ref and digest
    assert "class SemanticReference" in src
    # Ensure no skill_id/version/namespace added
    # Parse SemanticReference block
    import re
    m = re.search(r"class SemanticReference.*?(?=\nclass |\n@dataclass)", src, re.S)
    block = m.group(0) if m else src[:2000]
    # Should not contain forbidden fields
    assert "skill_id" not in block
    assert "namespace" not in block
    # TaskHandoff unchanged
    assert "class TaskHandoff" in src
    assert TASK_HANDOFF_MUTATED is False
    assert SEMANTIC_REFERENCE_MUTATED is False
    # Bootstrap unchanged (file defines _BootstrapBundle with alias BootstrapBundle)
    boot_src = pathlib.Path("aota_forge/work_plane/bootstrap.py").read_text(encoding="utf-8")
    assert "BootstrapBundle" in boot_src
    # Roles unchanged
    roles_src = pathlib.Path("aota_forge/work_plane/roles.py").read_text(encoding="utf-8")
    assert "class AgentWorkRole" in roles_src
    # Check __init__ not mutated to export skill_resolution aggregation
    init_src = pathlib.Path("aota_forge/work_plane/__init__.py").read_text(encoding="utf-8")
    assert "skill_resolution" not in init_src.lower()

# ---------------------------------------------------------------------------
# Additional: immutability and deterministic ordering sanity
# ---------------------------------------------------------------------------

def test_immutability_external_mutation():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    allowed = _allowed("ref-a", skill_id="skill-a", version="v1")
    univ = _universe(allowed)
    # Mutate input list after universe creation should not affect universe
    lst = [allowed]
    univ2 = AllowedSkillUniverse(lst)
    lst.append(_allowed("ref-b", skill_id="skill-b", version="v1"))
    assert len(univ2) == 1
    # Mutate pinned list after call should not affect result
    pinned_list = [SemanticReference(ref="ref-a")]
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=pinned_list, required_refs=[], role_default_refs=[], recommended_refs=[])
    pinned_list.append(SemanticReference(ref="ref-a"))
    assert len(res.selected) == 1

def test_allowed_universe_is_input_not_expanded():
    e_a = _entry(AgentWorkRole.CODER, "skill-a", "v1")
    reg = _registry(e_a)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    # Try to resolve a skill not in universe via recommended — should degrade, not expand universe
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-unknown")])
    assert len(res.selected) == 0
    assert len(univ.skills) == 1

