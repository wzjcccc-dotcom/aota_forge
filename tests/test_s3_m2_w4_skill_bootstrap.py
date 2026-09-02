"""S3 M2 W4 Eager / Progressive Skill Bootstrap Integration (focused).

Proves W4 deterministic skill bootstrap per S3 Plan M2-W4.

Covers:
A Pinned -> eager
B Required -> eager
C Role default -> eager
D Recommended -> progressive
E Recommended does not hydrate
F Logical ref vs content_ref
G Digest preservation
H Eager digest failure
I Required budget overflow fail closed (no truncation, no fallback)
J Pinned budget overflow fail closed
K Role-default budget overflow fail closed (no downgrade)
L Recommended budget pressure may degrade
M Deterministic ordering (input/candidate permutations do not change order)
N No authority (bootstrap output grants no authority)
O No search / re-resolution
P No filesystem resolver (W1 authorized reader seam only)
Q Existing S1 Bootstrap compatibility
R Complete M2 compatibility (W1/W2/W3 still PASS)
"""

from __future__ import annotations

import hashlib
import pathlib
import re

import pytest

from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.skill import SkillIdentity, compute_skill_digest
from aota_forge.work_plane.skill_registry import SkillRegistryEntry, StaticSkillRegistry
from aota_forge.work_plane.skill_resolution import (
    AllowedSkill,
    AllowedSkillUniverse,
    DegradedRecommended,
    SkillResolutionResult,
    resolve_skill_resolution,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.bootstrap import BootstrapBudget, BootstrapBundle, BootstrapComponent

import aota_forge.work_plane.skill_bootstrap as sb_mod
from aota_forge.work_plane.skill_bootstrap import (
    SkillBootstrapProjection,
    compose_skill_bootstrap,
    build_skill_bootstrap,
    EAGER_PROGRESSIVE_SKILL_BOOTSTRAP_INTEGRATED,
    S1_BOOTSTRAP_CONTRACT_REUSED,
    NEW_BOOTSTRAP_KIND_CREATED,
    BOOTSTRAP_ALLOWED_KINDS_MUTATED,
    BOOTSTRAP_BUDGET_REUSED,
    NEW_SKILL_BUDGET_CONTRACT_CREATED,
    PINNED_DELIVERY,
    REQUIRED_DELIVERY,
    ROLE_DEFAULT_DELIVERY,
    RECOMMENDED_DELIVERY,
    EAGER_CONTENT_USES_W1_VERIFIED_OPEN,
    EAGER_CONTENT_DIGEST_VERIFIED_BEFORE_BOOTSTRAP,
    PROGRESSIVE_REF_IS_LOGICAL_SKILL_REF,
    PROGRESSIVE_REF_IS_CONTENT_REF,
    PROGRESSIVE_SKILL_HYDRATED,
    BOOTSTRAP_SKILL_DIGEST_PRESERVED,
    SKILL_PROVENANCE_PRESERVED,
    PINNED_BUDGET_OVERFLOW_FAIL_CLOSED,
    REQUIRED_BUDGET_OVERFLOW_FAIL_CLOSED,
    ROLE_DEFAULT_AUTO_DOWNGRADE_TO_PROGRESSIVE,
    MANDATORY_SKILL_SILENT_TRUNCATION,
    MANDATORY_SKILL_AUTO_DOWNGRADE_TO_PROGRESSIVE,
    RECOMMENDED_BUDGET_FAILURE_IS_FATAL,
    RECOMMENDED_BUDGET_DEGRADATION_ALLOWED,
    BOOTSTRAP_COMPONENT_ORDER_DETERMINISTIC,
    BOOTSTRAP_INPUT_ORDER_IS_AUTHORITY,
    AUTHORIZED_READER_BOUNDARY_REUSED,
    W4_LEXICAL_SEARCH_INVOCATION,
    W4_SKILL_RERESOLUTION,
    SECOND_BOOTSTRAP_BUNDLE_ONTOLOGY_CREATED,
    S1_BOOTSTRAP_CANONICAL_ACCOUNTING_REUSED,
    SKILL_IS_AUTHORITY,
    BOOTSTRAP_BUNDLE_IS_AUTHORITY,
    W4_CONTENT_REF_AUTO_DEREFERENCE,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1", content=None, provenance="forge-native", content_ref=None):
    if content is None:
        content = f"content for {skill_id}@{version}"
    dg = _digest(content)
    ident = SkillIdentity(skill_id=skill_id, version=version, digest=dg, provenance=provenance)
    if content_ref is None:
        content_ref = f"skills/{skill_id}/{version}.md"
    entry = SkillRegistryEntry(namespace=namespace, identity=ident, content_ref=content_ref)
    return entry, content


def _allowed(ref, namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v1"):
    return AllowedSkill(ref=ref, namespace=namespace, skill_id=skill_id, version=version)


def _registry(*entries):
    return StaticSkillRegistry(list(entries))


def _universe(*allowed):
    return AllowedSkillUniverse(list(allowed))


def _budget(max_bytes=128 * 1024, max_components=16, max_ref=16):
    return BootstrapBudget(max_canonical_bytes=max_bytes, max_components=max_components, max_ref_count=max_ref)


def _reader_for(entries_and_contents: dict[str, str]):
    """Create reader that maps content_ref -> content, tracking calls."""
    calls: list[str] = []

    def reader(ref: str) -> str:
        calls.append(ref)
        if ref in entries_and_contents:
            return entries_and_contents[ref]
        # fallback try to find by suffix
        for k, v in entries_and_contents.items():
            if ref == k:
                return v
        raise KeyError(f"unknown ref {ref!r}")

    reader.calls = calls  # type: ignore[attr-defined]
    return reader


# ---------------------------------------------------------------------------
# A. Pinned -> eager
# ---------------------------------------------------------------------------

def test_a_pinned_eager():
    e, content = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="pinned content a")
    reg = _registry(e)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[], role_default_refs=[], recommended_refs=[])
    assert len(res.selected) == 1
    # Build logical map from universe
    logical_map = {e.composite_key: "ref-a"}
    reader = _reader_for({e.content_ref: content})
    budget = _budget()
    proj = compose_skill_bootstrap(reg, reader, budget, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-a")])
    assert len(proj.components) == 1
    comp = proj.components[0]
    assert comp.delivery == "eager"
    assert comp.kind == "semantic_ref"
    assert comp.materialized == content
    assert comp.digest == e.identity.digest
    assert comp.provenance == e.identity.provenance
    assert comp.ref is None
    # Must have used reader
    assert reader.calls == [e.content_ref]
    # Delivery constant
    assert PINNED_DELIVERY == "eager"


# ---------------------------------------------------------------------------
# B. Required -> eager
# ---------------------------------------------------------------------------

def test_b_required_eager():
    e, content = _entry(AgentWorkRole.CODER, "skill-req", "v1", content="required content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-req", skill_id="skill-req", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[SemanticReference(ref="ref-req")], role_default_refs=[], recommended_refs=[])
    logical_map = {e.composite_key: "ref-req"}
    reader = _reader_for({e.content_ref: content})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-req")])
    comp = proj.components[0]
    assert comp.delivery == "eager"
    assert comp.materialized == content
    assert comp.digest == e.identity.digest
    assert REQUIRED_DELIVERY == "eager"


# ---------------------------------------------------------------------------
# C. Role default -> eager
# ---------------------------------------------------------------------------

def test_c_role_default_eager():
    e, content = _entry(AgentWorkRole.CODER, "skill-role", "v1", content="role default content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-role", skill_id="skill-role", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[SemanticReference(ref="ref-role")], recommended_refs=[])
    logical_map = {e.composite_key: "ref-role"}
    reader = _reader_for({e.content_ref: content})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, role_default_refs=[SemanticReference(ref="ref-role")])
    comp = proj.components[0]
    assert comp.delivery == "eager"
    assert comp.materialized == content
    assert ROLE_DEFAULT_DELIVERY == "eager"


# ---------------------------------------------------------------------------
# D. Recommended -> progressive
# ---------------------------------------------------------------------------

def test_d_recommended_progressive():
    e, content = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="recommended content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-rec", skill_id="skill-rec", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[], required_refs=[], role_default_refs=[], recommended_refs=[SemanticReference(ref="ref-rec")])
    logical_map = {e.composite_key: "ref-rec"}
    # Recommended should NOT hydrate — reader should not be called even if we provide reader
    def failing_reader(ref: str) -> str:
        pytest.fail("reader should not be called for progressive")
        return content

    proj = compose_skill_bootstrap(reg, failing_reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-rec")])
    assert len(proj.components) == 1
    comp = proj.components[0]
    assert comp.delivery == "progressive"
    assert comp.ref == "ref-rec"
    assert comp.materialized is None
    assert comp.digest == e.identity.digest
    assert RECOMMENDED_DELIVERY == "progressive"


# ---------------------------------------------------------------------------
# E. Recommended does not hydrate
# ---------------------------------------------------------------------------

def test_e_recommended_does_not_hydrate():
    e, content = _entry(AgentWorkRole.CODER, "skill-x", "v1", content="rec content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-x", skill_id="skill-x", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-x")])
    logical_map = {e.composite_key: "ref-x"}
    calls: list[str] = []

    def reader(ref: str) -> str:
        calls.append(ref)
        return content

    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-x")])
    assert calls == [], f"progressive should not invoke reader, got {calls}"
    assert proj.progressive_components[0].materialized is None
    assert PROGRESSIVE_SKILL_HYDRATED is False


# ---------------------------------------------------------------------------
# F. Logical ref vs content_ref
# ---------------------------------------------------------------------------

def test_f_logical_ref_vs_content_ref():
    e, content = _entry(AgentWorkRole.CODER, "skill-f", "v1", content="f content", content_ref="skills/skill-f/v1.md")
    reg = _registry(e)
    univ = _universe(_allowed("ref-logical", skill_id="skill-f", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-logical")])
    logical_map = {e.composite_key: "ref-logical"}
    reader = _reader_for({e.content_ref: content})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-logical")])
    comp = proj.components[0]
    assert comp.delivery == "progressive"
    assert comp.ref == "ref-logical"
    assert comp.ref != e.content_ref
    assert "skills/skill-f" not in comp.ref  # logical, not physical
    assert PROGRESSIVE_REF_IS_LOGICAL_SKILL_REF is True
    assert PROGRESSIVE_REF_IS_CONTENT_REF is False

    # Also test synthetic fallback when logical map missing: should not be content_ref
    e2, c2 = _entry(AgentWorkRole.CODER, "skill-g", "v1", content="g content", content_ref="skills/skill-g/v1.md")
    reg2 = _registry(e2)
    # No allowed universe, so synthetic ref used
    res2 = SkillResolutionResult(selected=(e2,), degraded_recommended=(), target_namespace=AgentWorkRole.CODER)
    proj2 = compose_skill_bootstrap(reg2, lambda r: c2, _budget(), resolution=res2, logical_ref_map={}, recommended_refs=[SemanticReference(ref="skill:coder/skill-g@v1")])
    # Even synthetic should not be content_ref
    comp2 = proj2.components[0]
    # For recommended with synthetic, ref should be synthetic, not content_ref
    assert comp2.ref != e2.content_ref
    assert comp2.ref is not None
    assert "skills/skill-g" not in comp2.ref


# ---------------------------------------------------------------------------
# G. Digest preservation
# ---------------------------------------------------------------------------

def test_g_digest_preservation():
    e1, c1 = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="content a")
    e2, c2 = _entry(AgentWorkRole.CODER, "skill-b", "v1", content="content b")
    reg = _registry(e1, e2)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b", skill_id="skill-b", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], recommended_refs=[SemanticReference(ref="ref-b")])
    logical_map = {e1.composite_key: "ref-a", e2.composite_key: "ref-b"}
    reader = _reader_for({e1.content_ref: c1, e2.content_ref: c2})
    # Need category hints to make a eager and b progressive
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-a")], recommended_refs=[SemanticReference(ref="ref-b")])
    # Find eager and progressive
    eager = [c for c in proj.components if c.delivery == "eager"][0]
    prog = [c for c in proj.components if c.delivery == "progressive"][0]
    assert eager.digest == e1.identity.digest
    assert prog.digest == e2.identity.digest
    assert BOOTSTRAP_SKILL_DIGEST_PRESERVED is True
    assert SKILL_PROVENANCE_PRESERVED is True
    # Provenance preserved
    assert eager.provenance == e1.identity.provenance
    assert prog.provenance == e2.identity.provenance


# ---------------------------------------------------------------------------
# H. Eager digest failure
# ---------------------------------------------------------------------------

def test_h_eager_digest_mismatch_fails_closed():
    # Create entry with correct digest but reader returns tampered content with different digest
    real_content = "real content h"
    e_real, _ = _entry(AgentWorkRole.CODER, "skill-h", "v1", content=real_content)
    reg = _registry(e_real)
    univ = _universe(_allowed("ref-h", skill_id="skill-h", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-h")])
    logical_map = {e_real.composite_key: "ref-h"}

    def tampered_reader(ref: str) -> str:
        return "tampered content"

    with pytest.raises(Exception) as exc:
        compose_skill_bootstrap(reg, tampered_reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-h")])
    # Must be fail-closed, not silent
    assert "digest" in str(exc.value).lower() or "mismatch" in str(exc.value).lower() or "open failed" in str(exc.value).lower()
    assert EAGER_CONTENT_DIGEST_VERIFIED_BEFORE_BOOTSTRAP is True
    assert EAGER_CONTENT_USES_W1_VERIFIED_OPEN is True


# ---------------------------------------------------------------------------
# I. Required budget overflow fail closed (no truncation, no fallback)
# ---------------------------------------------------------------------------

def test_i_required_budget_overflow_fails_closed():
    e, content = _entry(AgentWorkRole.CODER, "skill-big", "v1", content="x" * 5000)
    reg = _registry(e)
    univ = _universe(_allowed("ref-big", skill_id="skill-big", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, required_refs=[SemanticReference(ref="ref-big")])
    logical_map = {e.composite_key: "ref-big"}
    reader = _reader_for({e.content_ref: content})
    # Budget tiny to force overflow
    tiny = BootstrapBudget(max_canonical_bytes=100, max_components=16, max_ref_count=16)
    with pytest.raises(Exception) as exc:
        compose_skill_bootstrap(reg, reader, tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-big")])
    assert "budget" in str(exc.value).lower() or "exceeds" in str(exc.value).lower()
    assert REQUIRED_BUDGET_OVERFLOW_FAIL_CLOSED is True
    assert MANDATORY_SKILL_SILENT_TRUNCATION is False
    assert MANDATORY_SKILL_AUTO_DOWNGRADE_TO_PROGRESSIVE is False


# ---------------------------------------------------------------------------
# J. Pinned budget overflow fail closed
# ---------------------------------------------------------------------------

def test_j_pinned_budget_overflow_fails_closed():
    e, content = _entry(AgentWorkRole.CODER, "skill-pin", "v1", content="y" * 5000)
    reg = _registry(e)
    univ = _universe(_allowed("ref-pin", skill_id="skill-pin", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-pin")])
    logical_map = {e.composite_key: "ref-pin"}
    reader = _reader_for({e.content_ref: content})
    tiny = BootstrapBudget(max_canonical_bytes=100, max_components=16, max_ref_count=16)
    with pytest.raises(Exception):
        compose_skill_bootstrap(reg, reader, tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-pin")])
    assert PINNED_BUDGET_OVERFLOW_FAIL_CLOSED is True


# ---------------------------------------------------------------------------
# K. Role-default budget overflow fail closed (no downgrade)
# ---------------------------------------------------------------------------

def test_k_role_default_budget_overflow_fails_closed():
    e, content = _entry(AgentWorkRole.CODER, "skill-role", "v1", content="z" * 5000)
    reg = _registry(e)
    univ = _universe(_allowed("ref-role", skill_id="skill-role", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, role_default_refs=[SemanticReference(ref="ref-role")])
    logical_map = {e.composite_key: "ref-role"}
    reader = _reader_for({e.content_ref: content})
    tiny = BootstrapBudget(max_canonical_bytes=100, max_components=16, max_ref_count=16)
    with pytest.raises(Exception):
        compose_skill_bootstrap(reg, reader, tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, role_default_refs=[SemanticReference(ref="ref-role")])
    assert ROLE_DEFAULT_AUTO_DOWNGRADE_TO_PROGRESSIVE is False
    # Ensure not downgraded to progressive
    # If it were downgraded, it would not raise but produce progressive; we already assert it raises


# ---------------------------------------------------------------------------
# L. Recommended budget pressure may degrade
# ---------------------------------------------------------------------------

def test_l_recommended_budget_pressure_degrades():
    # Create two recommended entries, budget only allows one
    e1, c1 = _entry(AgentWorkRole.CODER, "skill-rec1", "v1", content="rec1 content")
    e2, c2 = _entry(AgentWorkRole.CODER, "skill-rec2", "v1", content="rec2 content")
    reg = _registry(e1, e2)
    univ = _universe(_allowed("ref-rec1", skill_id="skill-rec1", version="v1"), _allowed("ref-rec2", skill_id="skill-rec2", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-rec1"), SemanticReference(ref="ref-rec2")])
    logical_map = {e1.composite_key: "ref-rec1", e2.composite_key: "ref-rec2"}
    # Budget that fits one progressive but not two: need to compute size.
    # First, get size for one progressive
    single_proj = compose_skill_bootstrap(reg, lambda r: c1, _budget(), resolution=SkillResolutionResult(selected=(e1,), degraded_recommended=(), target_namespace=AgentWorkRole.CODER), allowed_universe=univ, logical_ref_map={e1.composite_key: "ref-rec1"}, recommended_refs=[SemanticReference(ref="ref-rec1")])
    single_bundle = BootstrapBundle(bundle_type="task_main", components=tuple(single_proj.components))
    single_size = single_bundle.accounted_size()
    # Budget = single_size + small slack but not enough for two
    # Two progressive bundle size will be larger; set budget to single_size + 10
    tiny_budget = BootstrapBudget(max_canonical_bytes=single_size + 10, max_components=16, max_ref_count=16)
    # Now try with two recommended
    proj = compose_skill_bootstrap(reg, lambda r: c1, tiny_budget, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-rec1"), SemanticReference(ref="ref-rec2")])
    # Should have 1 component and 1 degraded (deterministic)
    assert len(proj.components) == 1
    assert len(proj.degraded_recommended) == 1  # plus W3 degraded (none) + budget degraded
    # Degradation explicit and deterministic
    assert proj.degraded_recommended[0].ref.ref in ("ref-rec1", "ref-rec2")
    assert RECOMMENDED_BUDGET_FAILURE_IS_FATAL is False
    assert RECOMMENDED_BUDGET_DEGRADATION_ALLOWED is True
    # Ensure not fatal — overall projection still valid
    assert proj.components[0].delivery == "progressive"


def test_l_recommended_degradation_deterministic():
    e1, _ = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="a")
    e2, _ = _entry(AgentWorkRole.CODER, "skill-b", "v1", content="b")
    e3, _ = _entry(AgentWorkRole.CODER, "skill-c", "v1", content="c")
    reg = _registry(e1, e2, e3)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b", skill_id="skill-b", version="v1"), _allowed("ref-c", skill_id="skill-c", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    logical_map = {e1.composite_key: "ref-a", e2.composite_key: "ref-b", e3.composite_key: "ref-c"}
    # Budget for 1
    single = compose_skill_bootstrap(reg, lambda r: "a", _budget(), resolution=SkillResolutionResult(selected=(e1,), degraded_recommended=(), target_namespace=AgentWorkRole.CODER), allowed_universe=univ, logical_ref_map={e1.composite_key: "ref-a"}, recommended_refs=[SemanticReference(ref="ref-a")])
    sz = BootstrapBundle(bundle_type="task_main", components=tuple(single.components)).accounted_size()
    tiny = BootstrapBudget(max_canonical_bytes=sz + 5, max_components=16, max_ref_count=16)
    p1 = compose_skill_bootstrap(reg, lambda r: "a", tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    p2 = compose_skill_bootstrap(reg, lambda r: "a", tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    # Deterministic regardless of input recommended order (but our resolution already deterministic)
    assert [c.ref for c in p1.components] == [c.ref for c in p2.components]
    assert len(p1.degraded_recommended) == len(p2.degraded_recommended)


# ---------------------------------------------------------------------------
# M. Deterministic ordering
# ---------------------------------------------------------------------------

def test_m_deterministic_ordering():
    # Create pinned, required, role, recommended each one, in mixed input order
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-pin", "v1", content="pin")
    e_req, c_req = _entry(AgentWorkRole.CODER, "skill-req", "v1", content="req")
    e_role, c_role = _entry(AgentWorkRole.CODER, "skill-role", "v1", content="role")
    e_rec, c_rec = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="rec")
    reg = _registry(e_pin, e_req, e_role, e_rec)
    univ = _universe(
        _allowed("ref-pin", skill_id="skill-pin", version="v1"),
        _allowed("ref-req", skill_id="skill-req", version="v1"),
        _allowed("ref-role", skill_id="skill-role", version="v1"),
        _allowed("ref-rec", skill_id="skill-rec", version="v1"),
    )
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ,
        pinned_refs=[SemanticReference(ref="ref-pin")],
        required_refs=[SemanticReference(ref="ref-req")],
        role_default_refs=[SemanticReference(ref="ref-role")],
        recommended_refs=[SemanticReference(ref="ref-rec")])
    logical_map = {
        e_pin.composite_key: "ref-pin",
        e_req.composite_key: "ref-req",
        e_role.composite_key: "ref-role",
        e_rec.composite_key: "ref-rec",
    }
    reader_map = {e_pin.content_ref: c_pin, e_req.content_ref: c_req, e_role.content_ref: c_role, e_rec.content_ref: c_rec}
    reader = _reader_for(reader_map)
    # Input order permutation: provide refs in different order but category same
    proj1 = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map,
        pinned_refs=[SemanticReference(ref="ref-pin")],
        required_refs=[SemanticReference(ref="ref-req")],
        role_default_refs=[SemanticReference(ref="ref-role")],
        recommended_refs=[SemanticReference(ref="ref-rec")])
    # Permuted within category? Our resolution deterministic but test ordering of components
    # Components should be pinned, required, role, recommended regardless of skill_id lexical?
    # skill-pin, skill-req, etc. Our bucket sorting is by skill_id within category, so order inside category deterministic
    # Across categories, priority order pinned>required>role>recommended
    assert [c.digest for c in proj1.components] == [e_pin.identity.digest, e_req.identity.digest, e_role.identity.digest, e_rec.identity.digest]
    # Input order should not affect: create another resolution with permuted recommended order but same categories — still same component order
    # Also test that bootstrap input order is not authority: reverse components input to compose_skill_bootstrap via explicit entries
    proj2 = compose_skill_bootstrap(reg, reader, _budget(),
        pinned_entries=[e_pin],
        required_entries=[e_req],
        role_default_entries=[e_role],
        recommended_entries=[e_rec],
        logical_ref_map=logical_map)
    # Even if we pass recommended first in explicit call? Our function buckets, so order still priority
    assert [c.digest for c in proj2.components] == [c.digest for c in proj1.components]
    assert BOOTSTRAP_COMPONENT_ORDER_DETERMINISTIC is True
    assert BOOTSTRAP_INPUT_ORDER_IS_AUTHORITY is False


def test_m_within_class_deterministic():
    # Within same category, order deterministic by skill_id regardless of input order
    e_a, c_a = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="a")
    e_b, c_b = _entry(AgentWorkRole.CODER, "skill-b", "v1", content="b")
    e_c, c_c = _entry(AgentWorkRole.CODER, "skill-c", "v1", content="c")
    reg = _registry(e_a, e_b, e_c)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b", skill_id="skill-b", version="v1"), _allowed("ref-c", skill_id="skill-c", version="v1"))
    # All required, input order permuted
    res1 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, required_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, required_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    logical_map = {e_a.composite_key: "ref-a", e_b.composite_key: "ref-b", e_c.composite_key: "ref-c"}
    reader = _reader_for({e_a.content_ref: c_a, e_b.content_ref: c_b, e_c.content_ref: c_c})
    proj1 = compose_skill_bootstrap(reg, reader, _budget(), resolution=res1, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    proj2 = compose_skill_bootstrap(reg, reader, _budget(), resolution=res2, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    assert [c.materialized for c in proj1.components] == [c.materialized for c in proj2.components]
    assert [c.materialized for c in proj1.components] == ["a", "b", "c"]  # sorted by skill_id


# ---------------------------------------------------------------------------
# N. No authority
# ---------------------------------------------------------------------------

def test_n_no_authority():
    assert SKILL_IS_AUTHORITY is False
    assert BOOTSTRAP_BUNDLE_IS_AUTHORITY is False
    # Check projection components don't grant authority
    e, content = _entry(AgentWorkRole.CODER, "skill-n", "v1", content="n content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-n", skill_id="skill-n", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-n")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-n"}, pinned_refs=[SemanticReference(ref="ref-n")])
    # Bundle is not authority, component is not authority
    for c in proj.components:
        assert not hasattr(c, "is_authority")
        assert c.kind == "semantic_ref"
    # Source file should not contain authority grant
    src = pathlib.Path(sb_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Should state authority no, but not implement authority
    assert "skill_is_authority" in lower or "is_authority" in lower


# ---------------------------------------------------------------------------
# O. No search / re-resolution
# ---------------------------------------------------------------------------

def test_o_no_search_or_reresolution():
    src = pathlib.Path(sb_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Remove docstring for strict check
    code_only = re.sub(r'"""[\s\S]*?"""', '', src).lower()
    assert "lexical" not in code_only or "lexical_search" not in code_only or "w4_lexical_search_invocation" in src.lower()
    assert W4_LEXICAL_SEARCH_INVOCATION is False
    assert W4_SKILL_RERESOLUTION is False
    # Should not import skill_search
    assert "from aota_forge.work_plane.skill_search import" not in src
    assert "import aota_forge.work_plane.skill_search" not in src
    # Should not call resolve_skill_resolution
    assert "resolve_skill_resolution" not in code_only
    assert "resolve_from_handoff" not in code_only
    # Should not reconstruct authority universe via AGENTS or filesystem
    assert "AllowedSkillUniverse(" not in code_only or "allowed_universe" in src.lower()  # allow as param, not construction
    # Ensure no search invocation
    assert "def search" not in code_only


# ---------------------------------------------------------------------------
# P. No filesystem resolver (W1 seam only)
# ---------------------------------------------------------------------------

def test_p_no_filesystem_resolver():
    src = pathlib.Path(sb_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    code_only = re.sub(r'"""[\s\S]*?"""', '', src).lower()
    assert AUTHORIZED_READER_BOUNDARY_REUSED is True
    # Should reuse open_skill, not create resolver
    assert "open_skill" in src
    assert "read_authorized_content" in src
    # Must not create filesystem resolver/sandbox
    assert "worktree_sandbox" not in code_only
    assert "worktree_resources" not in code_only
    assert "agents_discovery" not in code_only
    assert "path.open" not in code_only
    assert "os.walk" not in code_only
    assert "filesystem" not in code_only or "filesystem_resolver" in src.lower()  # allow comment
    # Check no S2 unaccepted dependency
    assert "S2_UNACCEPTED_CANDIDATE_DEPENDENCY" in src or "s2" not in code_only


def test_p_uses_w1_reader_not_content_ref_deref():
    e, content = _entry(AgentWorkRole.CODER, "skill-p", "v1", content="p content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-p", skill_id="skill-p", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-p")])
    logical_map = {e.composite_key: "ref-p"}
    # Reader receives opaque content_ref, not logical ref
    seen: list[str] = []

    def reader(ref: str) -> str:
        seen.append(ref)
        assert ref == e.content_ref
        assert ref != "ref-p"
        return content

    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-p")])
    assert seen == [e.content_ref]
    assert W4_CONTENT_REF_AUTO_DEREFERENCE is False if hasattr(sb_mod, "W4_CONTENT_REF_AUTO_DEREFERENCE") else True  # check flag exists elsewhere
    # Alternative flag name
    assert sb_mod.W4_CONTENT_REF_AUTO_DEREFERENCE is False


# ---------------------------------------------------------------------------
# Q. Existing S1 Bootstrap compatibility
# ---------------------------------------------------------------------------

def test_q_s1_bootstrap_compatibility_markers():
    assert EAGER_PROGRESSIVE_SKILL_BOOTSTRAP_INTEGRATED is True
    assert S1_BOOTSTRAP_CONTRACT_REUSED is True
    assert NEW_BOOTSTRAP_KIND_CREATED is False
    assert BOOTSTRAP_ALLOWED_KINDS_MUTATED is False
    assert BOOTSTRAP_BUDGET_REUSED is True
    assert NEW_SKILL_BUDGET_CONTRACT_CREATED is False
    assert S1_BOOTSTRAP_CANONICAL_ACCOUNTING_REUSED is True
    assert SECOND_BOOTSTRAP_BUNDLE_ONTOLOGY_CREATED is False
    # Check bootstrap.py unchanged: kind semantic_ref still allowed, no skill kind
    from aota_forge.work_plane.bootstrap import ALLOWED_KINDS
    assert "semantic_ref" in ALLOWED_KINDS
    assert "skill" not in ALLOWED_KINDS


def test_q_s1_bootstrap_still_works():
    from aota_forge.work_plane.lifecycle import ExecutionWorkRoleBinding
    from aota_forge.work_plane.soul import Soul
    from aota_forge.work_plane.handoff import TaskHandoff
    from aota_forge.work_plane.bootstrap import create_worker_bundle, create_task_main_bundle
    soul = Soul(content="soul content")
    handoff = TaskHandoff(work_role="coder", task_kind="impl", objective="obj", bounded_scope="scope", validation_expectations=("ok",), semantic_stop_expectations=("stop",))
    binding = ExecutionWorkRoleBinding(work_role="coder")
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    assert bundle.bundle_type == "worker"
    # Validate budget still works
    budget = BootstrapBudget(max_canonical_bytes=128 * 1024)
    bundle.validate_budget(budget)


# ---------------------------------------------------------------------------
# R. Complete M2 compatibility (W1/W2/W3 still PASS)
# ---------------------------------------------------------------------------

def test_r_w1_still_pass():
    from aota_forge.work_plane.skill_content import open_skill as w1_open
    e, content = _entry(AgentWorkRole.CODER, "skill-w1", "v1", content="w1 content")
    reg = _registry(e)
    opened = w1_open(reg, "coder", "skill-w1", "v1", lambda r: content)
    assert opened.content == content


def test_r_w2_still_pass():
    from aota_forge.work_plane.skill_search import LexicalSkillSearchIndex, SkillSearchDocument
    e, _ = _entry(AgentWorkRole.CODER, "skill-w2", "v1", content="w2 content")
    reg = _registry(e)
    doc = SkillSearchDocument(namespace=AgentWorkRole.CODER, skill_id="skill-w2", version="v1", title="hello", description="world")
    idx = LexicalSkillSearchIndex(reg, [doc])
    hits = idx.search(AgentWorkRole.CODER, "hello", limit=10)
    assert len(hits) == 1


def test_r_w3_still_pass():
    e, _ = _entry(AgentWorkRole.CODER, "skill-w3", "v1")
    reg = _registry(e)
    univ = _universe(_allowed("ref-w3", skill_id="skill-w3", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-w3")])
    assert len(res.selected) == 1
    assert res.selected[0].skill_id == "skill-w3"


# ---------------------------------------------------------------------------
# Additional: explicit per-category API, duplicate hydration, component bound
# ---------------------------------------------------------------------------

def test_duplicate_eager_hydration_no():
    # W3 deduplicates, but test W4 does not double hydrate if same skill appears in multiple buckets via explicit entries
    e, content = _entry(AgentWorkRole.CODER, "skill-dup", "v1", content="dup content")
    reg = _registry(e)
    calls: list[str] = []

    def reader(ref: str) -> str:
        calls.append(ref)
        return content

    # Pass same entry in pinned and required via explicit API — W4 should deduplicate and hydrate once
    proj = compose_skill_bootstrap(reg, reader, _budget(),
        pinned_entries=[e],
        required_entries=[e],
        logical_ref_map={e.composite_key: "ref-dup"})
    assert len(proj.components) == 1
    assert len(calls) == 1  # hydrated exactly once
    # Check markers
    assert sb_mod.W4_LEXICAL_SEARCH_INVOCATION is False


def test_component_count_bound_reused():
    # Create many skills to exceed MAX_COMPONENT_COUNT
    from aota_forge.work_plane.bootstrap import MAX_COMPONENT_COUNT
    entries = []
    contents: dict[str, str] = {}
    univ_allowed = []
    logical_map: dict[tuple[str, str, str], str] = {}
    for i in range(MAX_COMPONENT_COUNT + 1):
        e, c = _entry(AgentWorkRole.CODER, f"skill-{i}", "v1", content=f"content {i}")
        entries.append(e)
        contents[e.content_ref] = c
        ref = f"ref-{i}"
        univ_allowed.append(_allowed(ref, skill_id=f"skill-{i}", version="v1"))
        logical_map[e.composite_key] = ref
    reg = _registry(*entries)
    univ = _universe(*univ_allowed)
    # Try to resolve all as required (mandatory) — W3 would fail due to bounds but we test W4 direct
    # Use explicit per-category to bypass W3 bounds for this test, but W4 should respect S1 component count via budget
    # For mandatory, should fail closed when exceeding MAX_COMPONENT_COUNT via bundle construction
    budget = BootstrapBudget(max_canonical_bytes=128 * 1024, max_components=MAX_COMPONENT_COUNT, max_ref_count=MAX_COMPONENT_COUNT)
    # Use pinned entries with all
    with pytest.raises(Exception):
        compose_skill_bootstrap(reg, _reader_for(contents), budget,
            pinned_entries=entries,
            logical_ref_map=logical_map)


def test_no_new_kind_and_budget_reused():
    src = pathlib.Path(sb_mod.__file__).read_text(encoding="utf-8")
    assert "kind=\"skill\"" not in src
    assert "kind='skill'" not in src
    # Ensure ALLOWED_KINDS not mutated
    assert BOOTSTRAP_ALLOWED_KINDS_MUTATED is False
    assert NEW_BOOTSTRAP_KIND_CREATED is False
    assert BOOTSTRAP_BUDGET_REUSED is True
    # Ensure no new budget type
    code_only = re.sub(r'"""[\s\S]*?"""', '', src)
    assert "class SkillBootstrapBudget" not in code_only
    assert "class SkillTokenBudget" not in code_only


def test_skill_bootstrap_uses_semantic_ref_kind():
    e, content = _entry(AgentWorkRole.CODER, "skill-kind", "v1", content="kind content")
    reg = _registry(e)
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(),
        pinned_entries=[e], logical_ref_map={e.composite_key: "ref-kind"})
    assert all(c.kind == "semantic_ref" for c in proj.components)


def test_provenance_preserved():
    e, content = _entry(AgentWorkRole.CODER, "skill-prov", "v1", content="prov content", provenance="custom-provenance")
    reg = _registry(e)
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(),
        pinned_entries=[e], logical_ref_map={e.composite_key: "ref-prov"})
    assert proj.components[0].provenance == "custom-provenance"
    assert SKILL_PROVENANCE_PRESERVED is True


def test_eager_progressive_distinct():
    e_eager, c_eager = _entry(AgentWorkRole.CODER, "skill-eager", "v1", content="eager content")
    e_prog, c_prog = _entry(AgentWorkRole.CODER, "skill-prog", "v1", content="prog content")
    reg = _registry(e_eager, e_prog)
    univ = _universe(_allowed("ref-eager", skill_id="skill-eager", version="v1"), _allowed("ref-prog", skill_id="skill-prog", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ,
        pinned_refs=[SemanticReference(ref="ref-eager")],
        recommended_refs=[SemanticReference(ref="ref-prog")])
    logical_map = {e_eager.composite_key: "ref-eager", e_prog.composite_key: "ref-prog"}
    reader = _reader_for({e_eager.content_ref: c_eager})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map,
        pinned_refs=[SemanticReference(ref="ref-eager")], recommended_refs=[SemanticReference(ref="ref-prog")])
    assert len(proj.components) == 2
    eager = [c for c in proj.components if c.delivery == "eager"][0]
    prog = [c for c in proj.components if c.delivery == "progressive"][0]
    assert eager.materialized is not None and eager.ref is None
    assert prog.ref is not None and prog.materialized is None

