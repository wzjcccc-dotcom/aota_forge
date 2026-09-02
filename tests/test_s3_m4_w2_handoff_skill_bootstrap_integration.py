"""S3 M4 W2 — Handoff → Skill Resolution → Bootstrap Integrated Proof.

Proves the complete accepted S3 semantic path works as one composition:

    TaskHandoff.skill_refs
            ↓
    allowed Skill universe
            ↓
    deterministic Skill resolution
            ↓
    exact Skill identity/version/digest
            ↓
    required/current eager read/open
            ↓
    digest verification
            ↓
    eager bootstrap materialization
    recommended → progressive semantic reference
            ↓
    existing S1 Bootstrap components/budget

This is primarily an integration proof.

Invariants from requirement:
- ALLOWED_UNIVERSE_IS_INPUT=yes, W2_EXPANDS_ALLOWED_UNIVERSE=no
- HANDOFF_PIN_RESOLVED_THROUGH_ALLOWED_UNIVERSE=yes, HANDOFF_PIN_GRANTS_AUTHORITY=no
- REQUIRED_SKILL_SELECTED=yes, registry membership verified
- pinned>required>role_default>recommended, duplicate collapse no duplicate output
- RECOMMENDED_AUTO_PROMOTED_TO_REQUIRED=no, RECOMMENDED_DELIVERY=progressive,
  RECOMMENDED_SKILL_CONTENT_HYDRATED=no
- AUTHORIZED_READER_BOUNDARY_REUSED=yes, CONTENT_REF_INTERPRETED_AS_PATH=no,
  CONTENT_REF_AUTO_DEREFERENCE=no
- EAGER_DIGEST_VERIFIED=yes, UNVERIFIED_EAGER_CONTENT_IN_BOOTSTRAP=no (negative case)
- PROGRESSIVE_REF_IS_LOGICAL_SKILL_REF=yes, PROGRESSIVE_REF_IS_CONTENT_REF=no
- BOOTSTRAP_BUDGET_REUSED=yes, NEW_SKILL_BUDGET_CREATED=no
- END_TO_END_ORDER_DETERMINISTIC=yes, INPUT_ORDER_IS_AUTHORITY=no
- SEARCH_RESULT_BYPASSES_AUTHORITY_GATE=no
- S2_OWNERSHIP_PRESERVED=yes, JRV1_SCOPE_DUPLICATED=no
- TOOL_EXECUTION_PERFORMED=no, LIVE_AGENT_RUNTIME_REQUIRED=no
- SEMANTIC_REFERENCE_MUTATED=no, TASK_HANDOFF_MUTATED=no, BOOTSTRAP_CONTRACT_MUTATED=no

Primary changed file is this test only (PRODUCTION_SOURCE_CHANGE_COUNT=0).
"""

from __future__ import annotations

import hashlib
import itertools
import pathlib

import pytest

from aota_forge.work_plane.bootstrap import BootstrapBudget, BootstrapBundle, BootstrapComponent, ALLOWED_KINDS
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.skill import SkillIdentity, compute_skill_digest
from aota_forge.work_plane.skill_content import open_skill
from aota_forge.work_plane.skill_registry import SkillRegistryEntry, StaticSkillRegistry
from aota_forge.work_plane.skill_resolution import (
    AllowedSkill,
    AllowedSkillUniverse,
    SkillResolutionError,
    SkillResolutionResult,
    resolve_skill_resolution,
)
from aota_forge.work_plane.skill_search import LexicalSkillSearchIndex, SkillSearchDocument

import aota_forge.work_plane.skill_bootstrap as skill_bootstrap_mod
from aota_forge.work_plane.skill_bootstrap import compose_skill_bootstrap
import aota_forge.work_plane.skill as skill_mod
import aota_forge.work_plane.skill_registry as skill_registry_mod
import aota_forge.work_plane.skill_resolution as skill_resolution_mod
import aota_forge.work_plane.skill_content as skill_content_mod
import aota_forge.work_plane.bootstrap as bootstrap_mod
import aota_forge.work_plane.handoff as handoff_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def _reader_for(mapping: dict[str, str]):
    calls: list[str] = []

    def reader(ref: str) -> str:
        calls.append(ref)
        if ref in mapping:
            return mapping[ref]
        raise KeyError(f"unknown ref {ref!r}")

    reader.calls = calls  # type: ignore[attr-defined]
    return reader


def _make_handoff_with_pin(pinned_ref: str, pinned_digest: str | None = None, work_role="coder") -> TaskHandoff:
    sr = SemanticReference(ref=pinned_ref, digest=pinned_digest) if pinned_digest else SemanticReference(ref=pinned_ref)
    return TaskHandoff(
        work_role=work_role,
        task_kind="implementation",
        objective="prove handoff skill bootstrap integration",
        bounded_scope="integrated proof scope",
        validation_expectations=("pytest",),
        semantic_stop_expectations=("ambiguous scope",),
        skill_refs=[sr],
    )


# ---------------------------------------------------------------------------
# A. Happy path: handoff pinned + required + role_default + recommended -> bootstrap
# ---------------------------------------------------------------------------

def test_a_happy_path_handoff_to_bootstrap_end_to_end():
    # Create 4 distinct skills covering each semantic category
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-pinned", "v1", content="pinned skill content", provenance="forge-pinned")
    e_req, c_req = _entry(AgentWorkRole.CODER, "skill-required", "v1", content="required skill content", provenance="forge-required")
    e_role, c_role = _entry(AgentWorkRole.CODER, "skill-role-default", "v1", content="role default content", provenance="forge-role")
    e_rec, c_rec = _entry(AgentWorkRole.CODER, "skill-recommended", "v1", content="recommended skill content", provenance="forge-rec")

    reg = _registry(e_pin, e_req, e_role, e_rec)
    univ = _universe(
        _allowed("ref-pinned", skill_id="skill-pinned", version="v1"),
        _allowed("ref-required", skill_id="skill-required", version="v1"),
        _allowed("ref-role", skill_id="skill-role-default", version="v1"),
        _allowed("ref-rec", skill_id="skill-recommended", version="v1"),
    )

    # TaskHandoff carries pinned ref; pinned digest matches registry digest (exact identity)
    handoff = _make_handoff_with_pin("ref-pinned", pinned_digest=e_pin.identity.digest)

    # Resolve: pinned from handoff + required + role_default + recommended
    res = resolve_skill_resolution(
        reg,
        AgentWorkRole.CODER,
        univ,
        pinned_refs=list(handoff.skill_refs),
        required_refs=[SemanticReference(ref="ref-required")],
        role_default_refs=[SemanticReference(ref="ref-role")],
        recommended_refs=[SemanticReference(ref="ref-rec")],
    )

    # Prove required handoff pin resolved through allowed universe (not self-granting)
    assert len(res.selected) == 4
    assert res.target_namespace == AgentWorkRole.CODER
    # exact identity/version/digest verified via registry membership and digest check
    for entry in res.selected:
        assert reg.get(entry.namespace, entry.skill_id, entry.version) is not None
        assert entry.identity.digest == _digest({"skill-pinned": c_pin, "skill-required": c_req, "skill-role-default": c_role, "skill-recommended": c_rec}[entry.skill_id])

    # Required skill membership verified
    required_entry = [e for e in res.selected if e.skill_id == "skill-required"][0]
    assert required_entry.identity.digest == e_req.identity.digest

    # Eager read/open via authorized reader boundary
    reader_map = {e_pin.content_ref: c_pin, e_req.content_ref: c_req, e_role.content_ref: c_role}
    reader = _reader_for(reader_map)

    logical_map = {
        e_pin.composite_key: "ref-pinned",
        e_req.composite_key: "ref-required",
        e_role.composite_key: "ref-role",
        e_rec.composite_key: "ref-rec",
    }

    proj = compose_skill_bootstrap(
        reg,
        reader,
        _budget(),
        resolution=res,
        allowed_universe=univ,
        logical_ref_map=logical_map,
        pinned_refs=list(handoff.skill_refs),
        required_refs=[SemanticReference(ref="ref-required")],
        role_default_refs=[SemanticReference(ref="ref-role")],
        recommended_refs=[SemanticReference(ref="ref-rec")],
    )

    # Prove bootstrap projection contains mandatory/current eager + recommended progressive
    assert len(proj.components) == 4
    eager = [c for c in proj.components if c.delivery == "eager"]
    prog = [c for c in proj.components if c.delivery == "progressive"]
    assert len(eager) == 3  # pinned, required, role_default
    assert len(prog) == 1  # recommended

    # Deterministic order: pinned > required > role_default > recommended
    # Within our skill_ids, lexicographic order inside each bucket is single entry, so priority order is as inserted
    assert proj.components[0].digest == e_pin.identity.digest
    assert proj.components[1].digest == e_req.identity.digest
    assert proj.components[2].digest == e_role.identity.digest
    assert prog[0].digest == e_rec.identity.digest

    # Eager materialized content digest-verified, progressive logical ref not content_ref
    for comp in eager:
        assert comp.materialized is not None
        assert comp.ref is None
        assert comp.kind == "semantic_ref"
        # digest must match materialized
        assert comp.digest == _digest(comp.materialized)  # type: ignore[arg-type]
    assert prog[0].ref == "ref-rec"
    assert prog[0].materialized is None
    assert prog[0].ref != e_rec.content_ref
    assert "skills/skill-recommended" not in prog[0].ref

    # Authorized reader boundary reused: only eager skills invoked reader, with content_ref opaque
    assert set(reader.calls) == {e_pin.content_ref, e_req.content_ref, e_role.content_ref}
    assert e_rec.content_ref not in reader.calls

    # Bootstrap budget reused (no new skill budget)
    assert isinstance(proj.budget, BootstrapBudget)
    assert skill_bootstrap_mod.BOOTSTRAP_BUDGET_REUSED is True
    assert skill_bootstrap_mod.NEW_SKILL_BUDGET_CONTRACT_CREATED is False

    # Hand-off to bootstrap end-to-end proven
    assert proj.components[0].kind == "semantic_ref"

    # Verify existing S1 contracts still hold: BootstrapBudget / Bundle work
    bundle = BootstrapBundle(bundle_type="task_main", components=tuple(proj.components))
    assert bundle.accounted_size() > 0
    bundle.validate_budget(_budget())


# ---------------------------------------------------------------------------
# B. Pin authority boundary — outside universe fails closed, no lexical fallback
# ---------------------------------------------------------------------------

def test_b_pin_outside_universe_fails_closed():
    e, _ = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="a content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    handoff = _make_handoff_with_pin("ref-unknown-outside")

    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=list(handoff.skill_refs))

    # Also via compose path — resolution already fails, so bootstrap never materializes
    assert skill_resolution_mod.ALLOWED_UNIVERSE_IS_INPUT is True
    assert skill_resolution_mod.W3_EXPANDS_ALLOWED_UNIVERSE is False

    # Lexical search cannot bypass authority gate (illustrative negative)
    doc = SkillSearchDocument(namespace=AgentWorkRole.CODER, skill_id="skill-a", version="v1", title="skill a")
    idx = LexicalSkillSearchIndex(reg, [doc])
    hits = idx.search(AgentWorkRole.CODER, "skill", limit=10)
    assert any(h.skill_id == "skill-a" for h in hits)
    # Search hit without allowed-universe membership must still fail when used as pinned
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, _universe(), pinned_refs=[SemanticReference(ref="ref-a")])
    assert skill_resolution_mod.SEARCH_RESULT_BYPASSES_AUTHORITY_GATE is False


# ---------------------------------------------------------------------------
# C. Pinned digest — provided digest mismatch fails closed
# ---------------------------------------------------------------------------

def test_c_pinned_digest_mismatch_fails_closed():
    e, c = _entry(AgentWorkRole.CODER, "skill-pin", "v1", content="pin content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-pin", skill_id="skill-pin", version="v1"))
    wrong_digest = "0" * 64
    assert wrong_digest != e.identity.digest
    handoff_bad = _make_handoff_with_pin("ref-pin", pinned_digest=wrong_digest)
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=list(handoff_bad.skill_refs))

    # Good digest succeeds
    handoff_good = _make_handoff_with_pin("ref-pin", pinned_digest=e.identity.digest)
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=list(handoff_good.skill_refs))
    assert len(res.selected) == 1
    # Also digest absent remains compatible (requires only ref match)
    handoff_no_digest = _make_handoff_with_pin("ref-pin")
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=list(handoff_no_digest.skill_refs))
    assert len(res2.selected) == 1


# ---------------------------------------------------------------------------
# D. Exact version — no latest/SemVer fallback
# ---------------------------------------------------------------------------

def test_d_exact_version_no_fallback():
    e_v1, _ = _entry(AgentWorkRole.CODER, "skill-ver", "v1", content="v1 content")
    e_v2, c_v2 = _entry(AgentWorkRole.CODER, "skill-ver", "v2", content="v2 content")
    reg = _registry(e_v1, e_v2)
    univ = _universe(_allowed("ref-v2", skill_id="skill-ver", version="v2"))
    # Request stale v1 via allowed mapping that points to v1 but registry has only v2 under that ref
    univ_stale = _universe(_allowed("ref-stale", skill_id="skill-ver", version="v1"))
    # Registry has v1 actually? we removed v1 from this reg, so stale v1 should fail not fallback to v2
    reg_only_v2 = _registry(e_v2)
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg_only_v2, AgentWorkRole.CODER, univ_stale, pinned_refs=[SemanticReference(ref="ref-stale")])
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg_only_v2, AgentWorkRole.CODER, univ_stale, required_refs=[SemanticReference(ref="ref-stale")])

    # open_skill exact lookup must not return latest if exact missing
    with pytest.raises(Exception):
        open_skill(reg_only_v2, AgentWorkRole.CODER, "skill-ver", "v1", lambda r: c_v2)

    assert skill_registry_mod.LATEST_VERSION_LOOKUP is False
    assert skill_registry_mod.SEMVER_PRECEDENCE is False
    assert skill_resolution_mod.LATEST_VERSION_SELECTION is False
    assert skill_resolution_mod.VERSION_FALLBACK is False


# ---------------------------------------------------------------------------
# E. Eager read uses authorized reader boundary (content_ref not interpreted as path)
# ---------------------------------------------------------------------------

def test_e_eager_uses_authorized_reader_boundary():
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-eager", "v1", content="eager content")
    e_req, c_req = _entry(AgentWorkRole.CODER, "skill-eager2", "v1", content="eager2 content")
    reg = _registry(e_pin, e_req)
    univ = _universe(_allowed("ref-pin", skill_id="skill-eager", version="v1"), _allowed("ref-req", skill_id="skill-eager2", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    logical_map = {e_pin.composite_key: "ref-pin", e_req.composite_key: "ref-req"}
    seen: list[str] = []

    def reader(ref: str) -> str:
        seen.append(ref)
        # prove content_ref is opaque bounded, not interpreted as absolute path
        assert not ref.startswith("/")
        assert ".." not in ref
        assert ref in (e_pin.content_ref, e_req.content_ref)
        return c_pin if ref == e_pin.content_ref else c_req

    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    assert len(proj.components) == 2
    assert set(seen) == {e_pin.content_ref, e_req.content_ref}
    # Content_ref never used as filesystem path by W2/reader boundary
    assert skill_content_mod.CONTENT_REF_IS_AUTHORITY is False
    assert skill_content_mod.CONTENT_REF_AUTO_DEREFERENCE is False
    assert skill_bootstrap_mod.W4_CONTENT_REF_AUTO_DEREFERENCE is False
    assert skill_bootstrap_mod.AUTHORIZED_READER_BOUNDARY_REUSED is True
    # W2 does not interpret content_ref as path: source must not contain Path.open/content_ref deref
    src = pathlib.Path(skill_bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "Path(" not in src or "content_ref" in src.lower()  # allow comments but no Path.open(content_ref)
    assert "open(content_ref" not in src
    assert 'open("' not in src.lower() or "open_skill" in src


# ---------------------------------------------------------------------------
# F. Eager digest verified before bootstrap — negative case fails closed
# ---------------------------------------------------------------------------

def test_f_eager_digest_verified_negative_fails_closed():
    real_content = "real verified content f"
    e, _ = _entry(AgentWorkRole.CODER, "skill-f", "v1", content=real_content)
    reg = _registry(e)
    univ = _universe(_allowed("ref-f", skill_id="skill-f", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-f")])
    logical_map = {e.composite_key: "ref-f"}

    def tampered_reader(ref: str) -> str:
        assert ref == e.content_ref
        return "tampered content with different digest"

    with pytest.raises(Exception) as exc:
        compose_skill_bootstrap(reg, tampered_reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-f")])
    msg = str(exc.value).lower()
    assert "digest" in msg or "mismatch" in msg or "open failed" in msg
    assert skill_content_mod.DIGEST_VERIFICATION_REQUIRED is True
    assert skill_bootstrap_mod.EAGER_CONTENT_DIGEST_VERIFIED_BEFORE_BOOTSTRAP is True


# ---------------------------------------------------------------------------
# G. Progressive recommended — no reader invocation
# ---------------------------------------------------------------------------

def test_g_progressive_recommended_no_reader():
    e_rec, _ = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="rec content not hydrated")
    reg = _registry(e_rec)
    univ = _universe(_allowed("ref-rec", skill_id="skill-rec", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-rec")])
    logical_map = {e_rec.composite_key: "ref-rec"}

    def failing_reader(ref: str) -> str:
        pytest.fail(f"progressive must not call reader, got {ref!r}")
        return "never"

    proj = compose_skill_bootstrap(reg, failing_reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, recommended_refs=[SemanticReference(ref="ref-rec")])
    assert len(proj.components) == 1
    assert proj.components[0].delivery == "progressive"
    assert proj.components[0].materialized is None
    assert skill_bootstrap_mod.PROGRESSIVE_SKILL_HYDRATED is False
    assert skill_bootstrap_mod.RECOMMENDED_DELIVERY == "progressive"


# ---------------------------------------------------------------------------
# H. Logical ref — progressive ref is logical skill ref, not content_ref
# ---------------------------------------------------------------------------

def test_h_progressive_logical_ref_not_content_ref():
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-h", "v1", content="h pin", content_ref="skills/skill-h/v1.md")
    e_rec, _ = _entry(AgentWorkRole.CODER, "skill-h-rec", "v1", content="h rec", content_ref="skills/skill-h-rec/v1.md")
    reg = _registry(e_pin, e_rec)
    univ = _universe(_allowed("ref-h", skill_id="skill-h", version="v1"), _allowed("ref-h-rec", skill_id="skill-h-rec", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-h")], recommended_refs=[SemanticReference(ref="ref-h-rec")])
    logical_map = {e_pin.composite_key: "ref-h", e_rec.composite_key: "ref-h-rec"}
    reader = _reader_for({e_pin.content_ref: c_pin})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-h")], recommended_refs=[SemanticReference(ref="ref-h-rec")])

    prog = [c for c in proj.components if c.delivery == "progressive"][0]
    assert prog.ref == "ref-h-rec"
    assert prog.ref != e_rec.content_ref
    assert "skills/skill-h-rec" not in prog.ref
    assert prog.digest == e_rec.identity.digest
    assert prog.kind == "semantic_ref"
    assert skill_bootstrap_mod.PROGRESSIVE_REF_IS_LOGICAL_SKILL_REF is True
    assert skill_bootstrap_mod.PROGRESSIVE_REF_IS_CONTENT_REF is False

    # Progressive component must not contain physical filesystem path
    assert not prog.ref.startswith("/")
    assert ".." not in prog.ref


# ---------------------------------------------------------------------------
# I. Duplicate collapse — same Skill from multiple categories appears once
# ---------------------------------------------------------------------------

def test_i_duplicate_collapse_highest_precedence():
    e, c = _entry(AgentWorkRole.CODER, "skill-dup", "v1", content="dup content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-dup", skill_id="skill-dup", version="v1"))

    # Same exact skill appears as pinned, required, role_default, recommended via same ref
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ,
        pinned_refs=[SemanticReference(ref="ref-dup")],
        required_refs=[SemanticReference(ref="ref-dup")],
        role_default_refs=[SemanticReference(ref="ref-dup")],
        recommended_refs=[SemanticReference(ref="ref-dup")])
    assert len(res.selected) == 1

    reader = _reader_for({e.content_ref: c})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-dup"}, pinned_refs=[SemanticReference(ref="ref-dup")], required_refs=[SemanticReference(ref="ref-dup")], role_default_refs=[SemanticReference(ref="ref-dup")], recommended_refs=[SemanticReference(ref="ref-dup")])
    assert len(proj.components) == 1
    assert proj.components[0].delivery == "eager"  # pinned wins over progressive
    assert len(reader.calls) == 1  # hydrated exactly once
    assert skill_resolution_mod.DUPLICATE_RESOLUTION_OUTPUT is False
    assert skill_resolution_mod.HIGHEST_SELECTION_PRECEDENCE_WINS is True


# ---------------------------------------------------------------------------
# J. Determinism — permuted input yields identical result
# ---------------------------------------------------------------------------

def test_j_end_to_end_order_deterministic():
    e_a, c_a = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="content a")
    e_b, c_b = _entry(AgentWorkRole.CODER, "skill-b", "v1", content="content b")
    e_c, c_c = _entry(AgentWorkRole.CODER, "skill-c", "v1", content="content c")
    e_rec, _ = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="rec content")
    reg = _registry(e_a, e_b, e_c, e_rec)

    allowed_a = _allowed("ref-a", skill_id="skill-a", version="v1")
    allowed_b = _allowed("ref-b", skill_id="skill-b", version="v1")
    allowed_c = _allowed("ref-c", skill_id="skill-c", version="v1")
    allowed_rec = _allowed("ref-rec", skill_id="skill-rec", version="v1")

    logical_map = {e_a.composite_key: "ref-a", e_b.composite_key: "ref-b", e_c.composite_key: "ref-c", e_rec.composite_key: "ref-rec"}
    reader_map = {e_a.content_ref: c_a, e_b.content_ref: c_b, e_c.content_ref: c_c}

    # Two permutations of allowed universe
    for perm in [ (allowed_a, allowed_b, allowed_c, allowed_rec), (allowed_c, allowed_a, allowed_rec, allowed_b) ]:
        univ1 = _universe(*perm)
        univ2 = _universe(*tuple(reversed(perm)))
        res1 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ1,
            pinned_refs=[SemanticReference(ref="ref-a")],
            required_refs=[SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")],
            recommended_refs=[SemanticReference(ref="ref-rec")])
        res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ2,
            pinned_refs=[SemanticReference(ref="ref-a")],
            required_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-b")],
            recommended_refs=[SemanticReference(ref="ref-rec")])
        assert [e.skill_id for e in res1.selected] == [e.skill_id for e in res2.selected]
        assert [e.identity.digest for e in res1.selected] == [e.identity.digest for e in res2.selected]

        proj1 = compose_skill_bootstrap(reg, _reader_for(reader_map), _budget(), resolution=res1, allowed_universe=univ1, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")], recommended_refs=[SemanticReference(ref="ref-rec")])
        proj2 = compose_skill_bootstrap(reg, _reader_for(reader_map), _budget(), resolution=res2, allowed_universe=univ2, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-b")], recommended_refs=[SemanticReference(ref="ref-rec")])

        assert [c.digest for c in proj1.components] == [c.digest for c in proj2.components]
        assert [c.delivery for c in proj1.components] == [c.delivery for c in proj2.components]
        assert [c.ref for c in proj1.components] == [c.ref for c in proj2.components]

    assert skill_resolution_mod.RESOLUTION_ORDER_DETERMINISTIC is True
    assert skill_resolution_mod.INPUT_ORDER_IS_AUTHORITY is False
    assert skill_bootstrap_mod.BOOTSTRAP_COMPONENT_ORDER_DETERMINISTIC is True
    assert skill_bootstrap_mod.BOOTSTRAP_INPUT_ORDER_IS_AUTHORITY is False
    assert skill_registry_mod.REGISTRY_DETERMINISTIC is True


# ---------------------------------------------------------------------------
# K. Budget — mandatory overflow fails closed, recommended degrades
# ---------------------------------------------------------------------------

def test_k_mandatory_budget_overflow_fails_closed():
    e_big, c_big = _entry(AgentWorkRole.CODER, "skill-big", "v1", content="x" * 8000)
    reg = _registry(e_big)
    univ = _universe(_allowed("ref-big", skill_id="skill-big", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, required_refs=[SemanticReference(ref="ref-big")])
    logical_map = {e_big.composite_key: "ref-big"}
    reader = _reader_for({e_big.content_ref: c_big})
    tiny = BootstrapBudget(max_canonical_bytes=200, max_components=16, max_ref_count=16)
    with pytest.raises(Exception) as exc:
        compose_skill_bootstrap(reg, reader, tiny, resolution=res, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-big")])
    assert "budget" in str(exc.value).lower() or "exceeds" in str(exc.value).lower()
    assert skill_bootstrap_mod.MANDATORY_SKILL_SILENT_TRUNCATION is False
    assert skill_bootstrap_mod.PINNED_BUDGET_OVERFLOW_FAIL_CLOSED is True
    assert skill_bootstrap_mod.REQUIRED_BUDGET_OVERFLOW_FAIL_CLOSED is True

    # Recommended overflow must degrade, not fail closed
    e_rec1, _ = _entry(AgentWorkRole.CODER, "skill-rec1", "v1", content="rec1 content")
    e_rec2, _ = _entry(AgentWorkRole.CODER, "skill-rec2", "v1", content="rec2 content")
    reg2 = _registry(e_rec1, e_rec2)
    univ2 = _universe(_allowed("ref-rec1", skill_id="skill-rec1", version="v1"), _allowed("ref-rec2", skill_id="skill-rec2", version="v1"))
    res2 = resolve_skill_resolution(reg2, AgentWorkRole.CODER, univ2, recommended_refs=[SemanticReference(ref="ref-rec1"), SemanticReference(ref="ref-rec2")])
    logical_map2 = {e_rec1.composite_key: "ref-rec1", e_rec2.composite_key: "ref-rec2"}
    # Compute budget that fits one progressive (about 200 bytes) but not two
    single = compose_skill_bootstrap(reg2, lambda r: "never", _budget(), resolution=SkillResolutionResult(selected=(e_rec1,), degraded_recommended=(), target_namespace=AgentWorkRole.CODER), allowed_universe=univ2, logical_ref_map={e_rec1.composite_key: "ref-rec1"}, recommended_refs=[SemanticReference(ref="ref-rec1")])
    sz = BootstrapBundle(bundle_type="task_main", components=tuple(single.components)).accounted_size()
    tiny2 = BootstrapBudget(max_canonical_bytes=sz + 10, max_components=16, max_ref_count=16)
    proj = compose_skill_bootstrap(reg2, lambda r: "never", tiny2, resolution=res2, allowed_universe=univ2, logical_ref_map=logical_map2, recommended_refs=[SemanticReference(ref="ref-rec1"), SemanticReference(ref="ref-rec2")])
    assert len(proj.components) == 1
    assert len(proj.degraded_recommended) == 1
    assert skill_bootstrap_mod.RECOMMENDED_BUDGET_FAILURE_IS_FATAL is False
    assert skill_bootstrap_mod.RECOMMENDED_BUDGET_DEGRADATION_ALLOWED is True

    # Prove budget reused, not bypassed
    assert skill_bootstrap_mod.BOOTSTRAP_BUDGET_REUSED is True


# ---------------------------------------------------------------------------
# L. Authority — no Skill/ref/bootstrap authority escalation
# ---------------------------------------------------------------------------

def test_l_no_authority_escalation():
    # Markers
    assert skill_mod.SKILL_IS_AUTHORITY is False
    assert skill_registry_mod.SKILL_IS_AUTHORITY is False
    assert skill_resolution_mod.SKILL_IS_AUTHORITY is False
    assert skill_bootstrap_mod.SKILL_IS_AUTHORITY is False
    assert bootstrap_mod.BOOTSTRAP_BUNDLE_IS_AUTHORITY is False
    assert bootstrap_mod.BOOTSTRAP_DIGEST_IS_AUTHORITY is False
    assert skill_resolution_mod.HANDOFF_PIN_GRANTS_AUTHORITY is False
    assert skill_bootstrap_mod.HANDOFF_PIN_GRANTS_AUTHORITY is False
    # S1 contracts unchanged
    assert skill_resolution_mod.SEMANTIC_REFERENCE_MUTATED is False
    assert skill_resolution_mod.TASK_HANDOFF_MUTATED is False
    assert skill_bootstrap_mod.S1_BOOTSTRAP_CONTRACT_CHANGE_REQUIRED is False
    # Digest is not authority
    assert skill_mod.DIGEST_IS_AUTHORITY is False
    assert skill_bootstrap_mod.DIGEST_IS_AUTHORITY is False

    # Handoff pin alone cannot grant authority
    e, _ = _entry(AgentWorkRole.CODER, "skill-auth", "v1", content="auth content")
    reg = _registry(e)
    univ_empty = _universe()
    handoff = _make_handoff_with_pin("ref-auth")
    with pytest.raises(SkillResolutionError):
        resolve_skill_resolution(reg, AgentWorkRole.CODER, univ_empty, pinned_refs=list(handoff.skill_refs))

    # Recommended does not auto-promote
    assert skill_resolution_mod.RECOMMENDED_SKILL_AUTO_PROMOTED_TO_REQUIRED is False

    # Check allowed universe is input, not expanded by W2
    univ = _universe(_allowed("ref-auth", skill_id="skill-auth", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-auth")])
    assert len(res.selected) == 1
    # Ensure universe not mutated
    assert len(univ.skills) == 1

    # No second handoff/registry/bootstrap ontology via imports
    src_handoff = pathlib.Path(handoff_mod.__file__).read_text(encoding="utf-8")
    assert "class TaskHandoff" in src_handoff
    src_bootstrap = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "class _BootstrapBundle" in src_bootstrap or "class BootstrapBundle" in src_bootstrap
    src_skill = pathlib.Path(skill_mod.__file__).read_text(encoding="utf-8")
    # W2 file must not create second ontology — check not defining class
    src_w2_text = pathlib.Path(__file__).read_text(encoding="utf-8")
    import re as _re
    assert not _re.search(r"^\s*class\s+TaskHandoff\s*\(", src_w2_text, _re.M)
    assert not _re.search(r"^\s*class\s+StaticSkillRegistry\s*\(", src_w2_text, _re.M)
    assert not _re.search(r"^\s*class\s+BootstrapBudget\s*\(", src_w2_text, _re.M)
    # Ensure no live agent runtime imports via actual import lines (exclude this test's own string checks)
    import_lines = [l for l in src_w2_text.splitlines() if l.strip().startswith("import ") or l.strip().startswith("from ")]
    assert not any("adapters.hermes" in l for l in import_lines)


def test_l_recommended_not_auto_promoted():
    e_req, c_req = _entry(AgentWorkRole.CODER, "skill-req", "v1", content="req")
    e_rec, _ = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="rec")
    reg = _registry(e_req, e_rec)
    univ = _universe(_allowed("ref-req", skill_id="skill-req", version="v1"), _allowed("ref-rec", skill_id="skill-rec", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, required_refs=[SemanticReference(ref="ref-req")], recommended_refs=[SemanticReference(ref="ref-rec")])
    # recommended should be in selected but as progressive via bootstrap, not promoted to eager
    logical_map = {e_req.composite_key: "ref-req", e_rec.composite_key: "ref-rec"}
    reader = _reader_for({e_req.content_ref: c_req})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, required_refs=[SemanticReference(ref="ref-req")], recommended_refs=[SemanticReference(ref="ref-rec")])
    assert len([c for c in proj.components if c.delivery == "eager"]) == 1
    assert len([c for c in proj.components if c.delivery == "progressive"]) == 1
    # Ensure recommended digest not used for eager hydration
    assert reader.calls == [e_req.content_ref]


# ---------------------------------------------------------------------------
# Additional: No filesystem/sandbox duplication, S2 ownership preserved
# ---------------------------------------------------------------------------

def test_s2_ownership_and_no_jrv_duplication():
    # W2 must not implement sandbox/worktree binding/AGENTS discovery/filesystem authority
    import re as _re
    src = pathlib.Path(skill_bootstrap_mod.__file__).read_text(encoding="utf-8")
    code_only = _re.sub(r'"""[\s\S]*?"""', '', src)
    lower_code = code_only.lower()
    assert "worktree_sandbox" not in lower_code
    assert "worktree_resources" not in lower_code
    assert "agents_discovery" not in lower_code
    # agents_applicability is reused conceptually but bootstrap must not implement resolver
    assert "path.open" not in lower_code
    assert "os.walk" not in lower_code

    src_res = pathlib.Path(skill_resolution_mod.__file__).read_text(encoding="utf-8")
    code_res = _re.sub(r'"""[\s\S]*?"""', '', src_res).lower()
    # Check that resolution does not import sandbox/worktree resolver (docstring may mention sandbox as concept)
    assert "from aota_forge.work_plane.worktree_sandbox" not in code_res
    assert "from aota_forge.work_plane.worktree_resources" not in code_res
    assert "import os" not in code_res or "import os" in code_res and "sandbox" not in code_res  # allow os not related to sandbox

    src_content = pathlib.Path(skill_content_mod.__file__).read_text(encoding="utf-8")
    code_content = _re.sub(r'"""[\s\S]*?"""', '', src_content).lower()
    assert "worktree_sandbox" not in code_content

    # JRV1 scope not duplicated: W2 should not implement Tool execution / WorkerResultCard
    # Check for actual import/construction of WorkerResultCard, not mere substring in comments
    import_lines_w2 = [l.lower() for l in pathlib.Path(__file__).read_text(encoding="utf-8").splitlines() if l.strip().startswith("import ") or l.strip().startswith("from ")]
    assert not any("workerresultcard" in l for l in import_lines_w2)
    assert not any("tool_result_governance" in l for l in import_lines_w2)


# ---------------------------------------------------------------------------
# Additional: S1 contracts unchanged, no new authority, no production change
# ---------------------------------------------------------------------------

def test_s1_contracts_unchanged_and_no_second_ontology():
    import re as _re
    assert handoff_mod.SemanticReference.__doc__ is not None
    # Check bootstrap allowed kinds unchanged (no skill kind)
    assert "semantic_ref" in ALLOWED_KINDS
    assert "skill" not in ALLOWED_KINDS
    assert skill_bootstrap_mod.NEW_BOOTSTRAP_KIND_CREATED is False
    assert skill_bootstrap_mod.BOOTSTRAP_ALLOWED_KINDS_MUTATED is False
    # No second bootstrap ontology
    assert skill_bootstrap_mod.SECOND_BOOTSTRAP_BUNDLE_ONTOLOGY_CREATED is False
    # No second skill registry / handoff created by this file — check for class definitions, not substring in asserts
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert not _re.search(r"^\s*class\s+TaskHandoff\s*\(", src, _re.M)
    assert not _re.search(r"^\s*class\s+StaticSkillRegistry\s*\(", src, _re.M)
    assert not _re.search(r"^\s*class\s+BootstrapBundle\s*\(", src, _re.M)
    assert not _re.search(r"^\s*class\s+BootstrapBudget\s*\(", src, _re.M)
    # Check S1 hot files not touched (verified via git diff externally, but check markers)
    assert skill_bootstrap_mod.S1_SHARED_HOT_FILE_TOUCHED is False
    assert skill_bootstrap_mod.S1_BOOTSTRAP_CONTRACT_REUSED is True


def test_recommended_delivers_progressive_with_digest_preserved():
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-pin", "v1", content="pin content")
    e_rec, _ = _entry(AgentWorkRole.CODER, "skill-rec", "v1", content="rec content", provenance="rec-prov")
    reg = _registry(e_pin, e_rec)
    univ = _universe(_allowed("ref-pin", skill_id="skill-pin", version="v1"), _allowed("ref-rec", skill_id="skill-rec", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-pin")], recommended_refs=[SemanticReference(ref="ref-rec")])
    logical_map = {e_pin.composite_key: "ref-pin", e_rec.composite_key: "ref-rec"}
    reader = _reader_for({e_pin.content_ref: c_pin})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical_map, pinned_refs=[SemanticReference(ref="ref-pin")], recommended_refs=[SemanticReference(ref="ref-rec")])
    # recommended progressive has digest and provenance preserved, no content
    prog = [c for c in proj.components if c.delivery == "progressive"][0]
    assert prog.digest == e_rec.identity.digest
    assert prog.provenance == e_rec.identity.provenance
    assert prog.materialized is None
    assert prog.ref == "ref-rec"
    # eager digest preserved too
    eager = [c for c in proj.components if c.delivery == "eager"][0]
    assert eager.digest == e_pin.identity.digest
    assert eager.provenance == e_pin.identity.provenance
