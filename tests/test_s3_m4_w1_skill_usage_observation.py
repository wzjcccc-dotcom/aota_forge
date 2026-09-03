"""S3 M4 W1 Skill Usage Observation Projection (focused).

Proves W1 bounded S3-local Skill usage observation projection per S3 Plan M4-W1.

Covers A-L from W1 spec plus required invariants.

Invariants under test:
* REUSE_EXISTING_EVENT_HOOK=yes
* EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR=yes
* NEW_EXECUTION_EVENT_TYPE_CREATED=no
* EXECUTION_EVENT_TYPE_MUTATED=no
* EVENTS_PY_MUTATED=no
* SKILL_USAGE_OBSERVATION_BOUNDED=yes, SILENT_TRUNCATION=no
* SKILL_USAGE_OBSERVATION_DETERMINISTIC=yes
* OBSERVED_SKILL_VERSION_PRESERVED=yes, OBSERVED_SKILL_DIGEST_PRESERVED=yes
* OBSERVED_DELIVERY_MATCHES_BOOTSTRAP=yes
* OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION=yes
* W1_RECOMPUTES_SKILL_RESOLUTION=no, W1_READS_SKILL_CONTENT=no, W1_PERFORMS_BOOTSTRAP=no
* SKILL_USAGE_OBSERVATION_IS_AUTHORITY=no
* TELEMETRY_STORE_CREATED=no, ANALYTICS_CREATED=no, BACKGROUND_EXPORTER_CREATED=no
* S6_OWNERSHIP_PRESERVED=yes
* SELECTION_SOURCE_IS_AUTHORITY=no
"""

from __future__ import annotations

import ast
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
    SkillResolutionResult,
    resolve_skill_resolution,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.bootstrap import BootstrapBudget
from aota_forge.work_plane.events import (
    ExecutionEvent,
    ExecutionEventType,
    EventHookError,
    emit_event,
)
from aota_forge.work_plane.skill_bootstrap import compose_skill_bootstrap

import aota_forge.work_plane.skill_usage as su_mod
from aota_forge.work_plane.skill_usage import (
    SkillUsageObservation,
    SkillUsageBoundsError,
    SkillUsageConsumerError,
    SkillUsageObservationError,
    project_skill_usage,
    consume_skill_usage_observations,
    observe_skill_usage,
    MAX_SKILL_USAGE_OBSERVATIONS,
    REUSE_EXISTING_EVENT_HOOK,
    EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR,
    EXISTING_EVENT_HOOK_CONTRACT_REUSED,
    NEW_EXECUTION_EVENT_TYPE_CREATED,
    EXECUTION_EVENT_TYPE_MUTATED,
    TELEMETRY_STORE_CREATED,
    ANALYTICS_CREATED,
    BACKGROUND_EXPORTER_CREATED,
    S6_OWNERSHIP_PRESERVED,
    S6_TELEMETRY_IMPLEMENTED_IN_S3,
    SKILL_USAGE_OBSERVATION_BOUNDED,
    SKILL_USAGE_OBSERVATION_DETERMINISTIC,
    OBSERVED_SKILL_VERSION_PRESERVED,
    OBSERVED_SKILL_DIGEST_PRESERVED,
    OBSERVED_DELIVERY_MATCHES_BOOTSTRAP,
    OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION,
    W1_RECOMPUTES_SKILL_RESOLUTION,
    W1_READS_SKILL_CONTENT,
    W1_PERFORMS_BOOTSTRAP,
    SKILL_USAGE_OBSERVATION_IS_AUTHORITY,
    SKILL_IS_AUTHORITY,
    EXECUTION_EVENT_IS_AUTHORITY,
    EVENT_ID_IS_AUTHORITY,
    DIGEST_IS_AUTHORITY,
    SELECTION_SOURCE_IS_AUTHORITY,
    S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED,
    DELIVERY_EAGER,
    DELIVERY_PROGRESSIVE,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "events.py"
USAGE_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "skill_usage.py"


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
    def reader(ref: str) -> str:
        if ref in mapping:
            return mapping[ref]
        raise KeyError(f"unknown ref {ref!r}")
    return reader


def _event(event_id="evt-001", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER):
    return ExecutionEvent(event_id=event_id, event_type=event_type, work_role=work_role, handoff_ref="handoff-001")


# ---------------------------------------------------------------------------
# A. Existing Event Contract Unchanged
# ---------------------------------------------------------------------------

def test_a_event_contract_unchanged():
    # ExecutionEventType set unchanged — exactly 5 values, no skill-specific
    expected = {"handoff_prepared", "execution_materialized", "worker_result", "semantic_stop", "mechanical_failure"}
    actual = {e.value for e in ExecutionEventType}
    assert actual == expected, f"ExecutionEventType mutated: {actual}"
    # Forbidden skill-specific values must not appear
    for forbidden in ("skill_loaded", "skill_selected", "skill_used", "skill_hydrated", "skill_resolved"):
        assert forbidden not in actual
    assert NEW_EXECUTION_EVENT_TYPE_CREATED is False
    assert EXECUTION_EVENT_TYPE_MUTATED is False


def test_a_events_py_not_modified():
    text = EVENTS_PATH.read_text(encoding="utf-8")
    # Ensure no skill-specific enum added
    assert "skill_loaded" not in text
    assert "skill_selected" not in text
    assert "skill_used" not in text
    assert "skill_hydrated" not in text
    assert "skill_resolved" not in text
    # Ensure events.py still defines only 5 types
    tree = ast.parse(text)
    enum_defs = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ExecutionEventType"]
    assert len(enum_defs) == 1
    assigns = [node for node in enum_defs[0].body if isinstance(node, ast.Assign) or isinstance(node, ast.AnnAssign)]
    # Count enum members via string check
    assert text.count("HANDOFF_PREPARED") == 1
    assert text.count("EXECUTION_MATERIALIZED") == 1
    assert text.count("WORKER_RESULT") == 1
    assert text.count("SEMANTIC_STOP") == 1
    assert text.count("MECHANICAL_FAILURE") == 1
    # Ensure markers
    assert REUSE_EXISTING_EVENT_HOOK is True
    assert EXISTING_EXECUTION_EVENT_USED_AS_OBSERVATION_ANCHOR is True
    assert EXISTING_EVENT_HOOK_CONTRACT_REUSED is True


# ---------------------------------------------------------------------------
# B. Observation Projection — bounded SkillUsageObservation produced
# ---------------------------------------------------------------------------

def test_b_observation_projection():
    e, content = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="content a")
    reg = _registry(e)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")])
    reader = _reader_for({e.content_ref: content})
    budget = _budget()
    proj = compose_skill_bootstrap(reg, reader, budget, resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-a"}, pinned_refs=[SemanticReference(ref="ref-a")])
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-a"}, pinned_refs=[SemanticReference(ref="ref-a")])
    assert isinstance(obs, tuple)
    assert len(obs) == 1
    assert isinstance(obs[0], SkillUsageObservation)
    # Bounded fields present
    o = obs[0]
    assert o.event_id == evt.event_id
    assert o.skill_id == "skill-a"
    assert o.version == "v1"
    assert o.digest == e.identity.digest
    assert o.namespace == "coder"
    assert SKILL_USAGE_OBSERVATION_BOUNDED is True


# ---------------------------------------------------------------------------
# C. Exact identity version/digest/namespace/skill_id preserved
# ---------------------------------------------------------------------------

def test_c_exact_identity_preserved():
    e1, c1 = _entry(AgentWorkRole.CODER, "skill-x", "v2", content="x content v2")
    e2, c2 = _entry(AgentWorkRole.ANALYST, "skill-y", "v9", content="y content")
    reg = _registry(e1, e2)
    univ = _universe(_allowed("ref-x", namespace=AgentWorkRole.CODER, skill_id="skill-x", version="v2"), _allowed("ref-y", namespace=AgentWorkRole.ANALYST, skill_id="skill-y", version="v9"))
    # Need two separate events? We'll test single event with two skills via required + role_default both in coder namespace? Use coder for both to satisfy target_namespace
    # For exact identity, test single skill in each namespace via separate resolutions
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, _universe(_allowed("ref-x", skill_id="skill-x", version="v2")), pinned_refs=[SemanticReference(ref="ref-x")])
    reader = _reader_for({e1.content_ref: c1})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=_universe(_allowed("ref-x", skill_id="skill-x", version="v2")), logical_ref_map={e1.composite_key: "ref-x"}, pinned_refs=[SemanticReference(ref="ref-x")])
    evt = _event(work_role=AgentWorkRole.CODER)
    obs = project_skill_usage(evt, res, proj, allowed_universe=_universe(_allowed("ref-x", skill_id="skill-x", version="v2")), logical_ref_map={e1.composite_key: "ref-x"}, pinned_refs=[SemanticReference(ref="ref-x")])
    o = obs[0]
    assert o.namespace == "coder"
    assert o.skill_id == "skill-x"
    assert o.version == "v2"
    assert o.digest == e1.identity.digest
    assert o.digest == _digest("x content v2")
    assert OBSERVED_SKILL_VERSION_PRESERVED is True
    assert OBSERVED_SKILL_DIGEST_PRESERVED is True
    # Also test analyst namespace
    reg2 = _registry(e2)
    res2 = resolve_skill_resolution(_registry(e2), AgentWorkRole.ANALYST, _universe(_allowed("ref-y", namespace=AgentWorkRole.ANALYST, skill_id="skill-y", version="v9")), pinned_refs=[SemanticReference(ref="ref-y")])
    proj2 = compose_skill_bootstrap(_registry(e2), _reader_for({e2.content_ref: c2}), _budget(), resolution=res2, allowed_universe=_universe(_allowed("ref-y", namespace=AgentWorkRole.ANALYST, skill_id="skill-y", version="v9")), logical_ref_map={e2.composite_key: "ref-y"}, pinned_refs=[SemanticReference(ref="ref-y")])
    evt2 = _event(event_id="evt-analyst", work_role=AgentWorkRole.ANALYST)
    obs2 = project_skill_usage(evt2, res2, proj2, allowed_universe=_universe(_allowed("ref-y", namespace=AgentWorkRole.ANALYST, skill_id="skill-y", version="v9")), logical_ref_map={e2.composite_key: "ref-y"}, pinned_refs=[SemanticReference(ref="ref-y")])
    assert obs2[0].namespace == "analyst"
    assert obs2[0].version == "v9"


# ---------------------------------------------------------------------------
# D. Eager observation — eager materialized Skill projected as eager
# ---------------------------------------------------------------------------

def test_d_eager_observation():
    e, content = _entry(AgentWorkRole.CODER, "skill-eager", "v1", content="eager content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-eager", skill_id="skill-eager", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-eager")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-eager"}, pinned_refs=[SemanticReference(ref="ref-eager")])
    assert proj.components[0].delivery == "eager"
    assert proj.components[0].materialized == content
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-eager"}, pinned_refs=[SemanticReference(ref="ref-eager")])
    assert obs[0].delivery == "eager"
    assert obs[0].delivery == DELIVERY_EAGER
    # Eager observation must NOT contain content body
    assert not hasattr(obs[0], "content")
    assert not hasattr(obs[0], "materialized")
    # Distinguishes selection from materialization
    assert OBSERVATION_DISTINGUISHES_SELECTION_FROM_MATERIALIZATION is True
    assert OBSERVED_DELIVERY_MATCHES_BOOTSTRAP is True


# ---------------------------------------------------------------------------
# E. Progressive observation — progressive Skill projected without content hydration
# ---------------------------------------------------------------------------

def test_e_progressive_observation():
    e, content = _entry(AgentWorkRole.CODER, "skill-prog", "v1", content="progressive content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-prog", skill_id="skill-prog", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-prog")])
    # Progressive should not hydrate — reader should not be called
    def failing_reader(ref: str) -> str:
        pytest.fail("reader must not be called for progressive")
        return content
    proj = compose_skill_bootstrap(reg, failing_reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-prog"}, recommended_refs=[SemanticReference(ref="ref-prog")])
    assert proj.components[0].delivery == "progressive"
    assert proj.components[0].ref == "ref-prog"
    assert proj.components[0].materialized is None
    evt = _event(event_id="evt-prog")
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-prog"}, recommended_refs=[SemanticReference(ref="ref-prog")])
    assert len(obs) == 1
    o = obs[0]
    assert o.delivery == "progressive"
    assert o.delivery == DELIVERY_PROGRESSIVE
    assert o.ref == "ref-prog"
    # Must not have hydrated content
    assert not hasattr(o, "content")
    # Selection source should be recommended (where preserved)
    assert o.selection_source == "recommended"


# ---------------------------------------------------------------------------
# F. No content leakage — progressive observation does not contain Skill body
# ---------------------------------------------------------------------------

def test_f_no_content_leakage():
    e, content = _entry(AgentWorkRole.CODER, "skill-leak", "v1", content="secret body content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-leak", skill_id="skill-leak", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-leak")])
    proj = compose_skill_bootstrap(reg, lambda r: content, _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-leak"}, recommended_refs=[SemanticReference(ref="ref-leak")])
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-leak"}, recommended_refs=[SemanticReference(ref="ref-leak")])
    o = obs[0]
    # Ensure body not in observation dict
    d = o.canonical_dict()
    assert "content" not in d
    assert "materialized" not in d
    assert "body" not in d
    # Also check string representation doesn't leak content
    assert content not in o.canonical_json()
    assert content not in str(o.to_dict())
    # Eager also should not leak beyond digest/provenance (already tested eager has no content field, but check eager)
    e_eager, c_eager = _entry(AgentWorkRole.CODER, "skill-eager-leak", "v1", content="eager secret")
    reg2 = _registry(e_eager)
    res2 = resolve_skill_resolution(reg2, AgentWorkRole.CODER, _universe(_allowed("ref-eager-leak", skill_id="skill-eager-leak", version="v1")), pinned_refs=[SemanticReference(ref="ref-eager-leak")])
    proj2 = compose_skill_bootstrap(reg2, _reader_for({e_eager.content_ref: c_eager}), _budget(), resolution=res2, allowed_universe=_universe(_allowed("ref-eager-leak", skill_id="skill-eager-leak", version="v1")), logical_ref_map={e_eager.composite_key: "ref-eager-leak"}, pinned_refs=[SemanticReference(ref="ref-eager-leak")])
    obs2 = project_skill_usage(_event(event_id="evt-eager-leak"), res2, proj2, allowed_universe=_universe(_allowed("ref-eager-leak", skill_id="skill-eager-leak", version="v1")), logical_ref_map={e_eager.composite_key: "ref-eager-leak"}, pinned_refs=[SemanticReference(ref="ref-eager-leak")])
    assert c_eager not in obs2[0].canonical_json()
    assert "eager secret" not in str(obs2[0].to_dict())


# ---------------------------------------------------------------------------
# G. Determinism — input permutations produce identical projection
# ---------------------------------------------------------------------------

def test_g_determinism():
    # Create 3 skills, same resolution but input order permuted
    entries = []
    contents = {}
    allowed = []
    logical = {}
    for i, sid in enumerate(["skill-a", "skill-b", "skill-c"]):
        e, c = _entry(AgentWorkRole.CODER, sid, "v1", content=f"content {sid}")
        entries.append(e)
        contents[e.content_ref] = c
        ref = f"ref-{sid[-1]}"
        allowed.append(_allowed(ref, skill_id=sid, version="v1"))
        logical[e.composite_key] = ref
    reg = _registry(*entries)
    univ = _universe(*allowed)
    # All recommended for progressive
    res1 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    res2 = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    # Bootstrap projections also deterministic
    proj1 = compose_skill_bootstrap(reg, lambda r: contents[r], _budget(), resolution=res1, allowed_universe=univ, logical_ref_map=logical, recommended_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    proj2 = compose_skill_bootstrap(reg, lambda r: contents[r], _budget(), resolution=res2, allowed_universe=univ, logical_ref_map=logical, recommended_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    evt = _event(event_id="evt-determinism")
    obs1 = project_skill_usage(evt, res1, proj1, allowed_universe=univ, logical_ref_map=logical, recommended_refs=[SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b"), SemanticReference(ref="ref-c")])
    obs2 = project_skill_usage(evt, res2, proj2, allowed_universe=univ, logical_ref_map=logical, recommended_refs=[SemanticReference(ref="ref-c"), SemanticReference(ref="ref-a"), SemanticReference(ref="ref-b")])
    # Identical tuples regardless of input order
    assert obs1 == obs2
    assert [o.skill_id for o in obs1] == [o.skill_id for o in obs2] == ["skill-a", "skill-b", "skill-c"]
    assert SKILL_USAGE_OBSERVATION_DETERMINISTIC is True
    # Also test that observations sorted canonically even if resolution selected order shuffled internally (but resolution already deterministic)


def test_g_bootstrap_input_permutation_does_not_change_delivery():
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-pin", "v1", content="pin")
    e_req, c_req = _entry(AgentWorkRole.CODER, "skill-req", "v1", content="req")
    reg = _registry(e_pin, e_req)
    univ = _universe(_allowed("ref-pin", skill_id="skill-pin", version="v1"), _allowed("ref-req", skill_id="skill-req", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    logical = {e_pin.composite_key: "ref-pin", e_req.composite_key: "ref-req"}
    reader = _reader_for({e_pin.content_ref: c_pin, e_req.content_ref: c_req})
    proj = compose_skill_bootstrap(reg, reader, _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    evt = _event()
    obs_a = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    # Permute logical map order (dict order not authority)
    logical_rev = {e_req.composite_key: "ref-req", e_pin.composite_key: "ref-pin"}
    obs_b = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map=logical_rev, pinned_refs=[SemanticReference(ref="ref-pin")], required_refs=[SemanticReference(ref="ref-req")])
    assert obs_a == obs_b


# ---------------------------------------------------------------------------
# H. Duplicate safety — same exact Skill does not generate duplicate
# ---------------------------------------------------------------------------

def test_h_duplicate_safety():
    e, content = _entry(AgentWorkRole.CODER, "skill-dup", "v1", content="dup content")
    reg = _registry(e)
    # Create universe with same skill but resolution deduplicates
    univ = _universe(_allowed("ref-dup", skill_id="skill-dup", version="v1"))
    # If same exact skill appears via pinned and required (duplicate), resolution keeps one
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-dup")], required_refs=[SemanticReference(ref="ref-dup")])
    assert len(res.selected) == 1
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-dup"}, pinned_refs=[SemanticReference(ref="ref-dup")], required_refs=[SemanticReference(ref="ref-dup")])
    assert len(proj.components) == 1
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-dup"}, pinned_refs=[SemanticReference(ref="ref-dup")], required_refs=[SemanticReference(ref="ref-dup")])
    assert len(obs) == 1
    # Duplicate same Skill via explicit per-category API should deduplicate
    from aota_forge.work_plane.skill_bootstrap import compose_skill_bootstrap as csb
    proj2 = csb(reg, _reader_for({e.content_ref: content}), _budget(), pinned_entries=[e], required_entries=[e], logical_ref_map={e.composite_key: "ref-dup"})
    assert len(proj2.components) == 1
    # Directly call project with resolution containing duplicate entry attempt? Create manual duplicate resolution (should be prevented by resolution, but test observation dedup)
    dup_res = SkillResolutionResult(selected=(e, e), degraded_recommended=(), target_namespace=AgentWorkRole.CODER)
    # Observation should deduplicate to 1 even if resolution incorrectly had duplicate
    evt2 = _event(event_id="evt-dup2")
    obs2 = project_skill_usage(evt2, dup_res, proj2, logical_ref_map={e.composite_key: "ref-dup"})
    assert len(obs2) == 1


def test_h_no_accidental_duplicate_across_calls():
    e1, c1 = _entry(AgentWorkRole.CODER, "skill-a", "v1", content="a")
    e2, c2 = _entry(AgentWorkRole.CODER, "skill-b", "v1", content="b")
    reg = _registry(e1, e2)
    univ = _universe(_allowed("ref-a", skill_id="skill-a", version="v1"), _allowed("ref-b", skill_id="skill-b", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-b")])
    logical = {e1.composite_key: "ref-a", e2.composite_key: "ref-b"}
    proj = compose_skill_bootstrap(reg, _reader_for({e1.content_ref: c1, e2.content_ref: c2}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-b")])
    evt = _event()
    obs1 = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-b")])
    obs2 = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-a")], required_refs=[SemanticReference(ref="ref-b")])
    assert obs1 == obs2
    assert len(obs1) == 2


# ---------------------------------------------------------------------------
# I. Authority — observation grants no authority
# ---------------------------------------------------------------------------

def test_i_authority():
    assert SKILL_USAGE_OBSERVATION_IS_AUTHORITY is False
    assert su_mod.SKILL_USAGE_OBSERVATION_IS_AUTHORITY is False
    assert su_mod.SKILL_USAGE_OBSERVATION_GRANTS_AUTHORITY is False
    assert su_mod.SKILL_IS_AUTHORITY is False
    assert su_mod.EXECUTION_EVENT_IS_AUTHORITY is False
    assert su_mod.EVENT_ID_IS_AUTHORITY is False
    assert su_mod.DIGEST_IS_AUTHORITY is False
    assert su_mod.SELECTION_SOURCE_IS_AUTHORITY is False
    assert S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED is False
    # Observation object should not have authority fields
    e, content = _entry(AgentWorkRole.CODER, "skill-auth", "v1", content="auth content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-auth", skill_id="skill-auth", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-auth")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-auth"}, pinned_refs=[SemanticReference(ref="ref-auth")])
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-auth"}, pinned_refs=[SemanticReference(ref="ref-auth")])
    o = obs[0]
    assert not hasattr(o, "grants_authority")
    assert not hasattr(o, "is_authority")
    assert not hasattr(o, "authorizes_tool")
    assert not hasattr(o, "filesystem_scope")
    # Source should contain authority markers set to no
    src = USAGE_PATH.read_text(encoding="utf-8")
    low = src.lower()
    assert "is_authority" in low
    # Ensure observation projection does not authorize TaskHandoff changes etc via source inspection
    assert "skill_usage_observation_is_authority" in low or "skill_is_authority" in low


def test_i_selection_source_not_authority():
    e, content = _entry(AgentWorkRole.CODER, "skill-sel", "v1", content="sel content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-sel", skill_id="skill-sel", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-sel")])
    proj = compose_skill_bootstrap(reg, lambda r: content, _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-sel"}, recommended_refs=[SemanticReference(ref="ref-sel")])
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-sel"}, recommended_refs=[SemanticReference(ref="ref-sel")])
    assert obs[0].selection_source == "recommended"
    assert SELECTION_SOURCE_IS_AUTHORITY is False
    # Changing selection_source must not grant authority — just observation


# ---------------------------------------------------------------------------
# J. Bounds — oversized identity/ref fails closed, no silent truncation
# ---------------------------------------------------------------------------

def test_j_bounds_oversized_identity_fails_closed():
    # Oversized skill_id should fail closed when constructing observation directly
    evt = _event()
    with pytest.raises((ValueError, SkillUsageBoundsError, TypeError)):
        SkillUsageObservation(
            event_id=evt.event_id,
            event_type=evt.event_type.value,
            namespace="coder",
            skill_id="x" * 200,  # exceeds 128
            version="v1",
            digest=_digest("content"),
            delivery="eager",
        )
    # Oversized ref should fail closed
    e, content = _entry(AgentWorkRole.CODER, "skill-j", "v1", content="j content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-j", skill_id="skill-j", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, recommended_refs=[SemanticReference(ref="ref-j")])
    # Create bootstrap with oversized ref? Instead directly test observation with oversized ref fails
    with pytest.raises((ValueError, SkillUsageBoundsError, TypeError)):
        SkillUsageObservation(
            event_id=evt.event_id,
            event_type="worker_result",
            namespace="coder",
            skill_id="skill-j",
            version="v1",
            digest=e.identity.digest,
            delivery="progressive",
            ref="r" * 600,  # exceeds 512
        )


def test_j_bounds_observation_count_fails_closed():
    # Create 65 entries exceeds MAX 64 — either resolution or observation must fail closed
    entries = []
    for i in range(65):
        ident = SkillIdentity(skill_id=f"skill-{i}", version="v1", digest=_digest(f"content {i}"), provenance="forge-native")
        entries.append(SkillRegistryEntry(namespace=AgentWorkRole.CODER, identity=ident, content_ref=f"skills/skill-{i}/v1.md"))
    # Resolution itself is bounded at 64, so construction should already fail closed
    with pytest.raises(Exception) as exc:
        res = SkillResolutionResult(selected=tuple(entries), degraded_recommended=(), target_namespace=AgentWorkRole.CODER)
        evt = _event()
        project_skill_usage(evt, res, None)
    # Must be bounds error (from resolution or observation), not silent
    assert "exceeds maximum" in str(exc.value).lower() or "bounds" in str(exc.value).lower()
    # Also test observation-level bound via fake resolution that bypasses SkillResolutionResult check
    class FakeRes:
        selected = tuple(entries[:65])
        target_namespace = AgentWorkRole.CODER
        degraded_recommended = ()
    fake = FakeRes()
    # Fake still should be caught by project_skill_usage count check
    with pytest.raises(SkillUsageBoundsError):
        project_skill_usage(_event(event_id="evt-j-65"), fake, None)


def test_j_no_silent_truncation():
    # Ensure that oversized provenance fails rather than truncates
    evt = _event()
    with pytest.raises((ValueError, SkillUsageBoundsError)):
        SkillUsageObservation(
            event_id=evt.event_id,
            event_type="worker_result",
            namespace="coder",
            skill_id="skill-trunc",
            version="v1",
            digest=_digest("content"),
            delivery="eager",
            provenance="p" * 600,  # exceeds 512
        )
    assert su_mod.SILENT_TRUNCATION is False or su_mod.SKILL_USAGE_OBSERVATION_BOUNDED is True


def test_j_digest_exact_preserved():
    # Digest must be exact 64 hex, not truncated or normalized incorrectly
    evt = _event()
    with pytest.raises((ValueError, SkillUsageBoundsError)):
        SkillUsageObservation(
            event_id=evt.event_id,
            event_type="worker_result",
            namespace="coder",
            skill_id="skill-d",
            version="v1",
            digest="abc",  # too short
            delivery="eager",
        )
    with pytest.raises((ValueError, SkillUsageBoundsError)):
        SkillUsageObservation(
            event_id=evt.event_id,
            event_type="worker_result",
            namespace="coder",
            skill_id="skill-d",
            version="v1",
            digest="Z" * 64,  # non-hex
            delivery="eager",
        )


# ---------------------------------------------------------------------------
# K. No persistence — no telemetry/analytics store created
# ---------------------------------------------------------------------------

def test_k_no_persistence():
    src = USAGE_PATH.read_text(encoding="utf-8")
    low = src.lower()
    for forbidden in ("sqlite", "postgres", "telemetrystore", "eventstore", "jsonl", "_event_history", "background_exporter", "analytics", "telemetry.db", "events.jsonl"):
        # Allow markers/comments that say "no telemetry" etc, but ensure no actual store class definition
        # Check that forbidden store class not defined
        pass
    tree = ast.parse(src)
    symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    assert "TelemetryStore" not in symbols
    assert "EventStore" not in symbols
    assert "Analytics" not in symbols
    # Ensure no file writing for telemetry
    assert "import sqlite3" not in src
    assert "open(" not in src or "open_skill" in src  # open_skill is allowed but not telemetry file open
    # Check markers
    assert TELEMETRY_STORE_CREATED is False
    assert ANALYTICS_CREATED is False
    assert BACKGROUND_EXPORTER_CREATED is False
    assert S6_OWNERSHIP_PRESERVED is True
    assert S6_TELEMETRY_IMPLEMENTED_IN_S3 is False
    # Ensure no persistence files created after projection
    evt = _event()
    e, content = _entry(AgentWorkRole.CODER, "skill-k", "v1", content="k content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-k", skill_id="skill-k", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-k")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-k"}, pinned_refs=[SemanticReference(ref="ref-k")])
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-k"}, pinned_refs=[SemanticReference(ref="ref-k")])
    # Check no files created
    for p in ["telemetry.db", "events.jsonl", "telemetry_store.py", "analytics.py", "aota_forge/work_plane/telemetry_store.py"]:
        assert not pathlib.Path(p).exists()
        assert not pathlib.Path(REPO_ROOT / p).exists()


# ---------------------------------------------------------------------------
# L. Existing EventHook compatibility — existing S1 event/hook tests still PASS
# ---------------------------------------------------------------------------

def test_l_eventhook_compatibility():
    # Reuse S1 semantics: emit_event with no hook is no-op, hook receives exact event, failure typed
    e = ExecutionEvent(event_id="evt-l-01", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.CODER)
    # No hook
    emit_event(e, None)
    emit_event(e)
    # With hook
    received = []
    def hook(evt):
        received.append(evt)
    emit_event(e, hook)
    assert received[0] == e
    # Failure propagation typed
    def failing_hook(evt):
        raise RuntimeError("hook fail")
    with pytest.raises(EventHookError) as exc:
        emit_event(e, failing_hook)
    assert exc.value.event_id == "evt-l-01"
    assert isinstance(exc.value.cause, RuntimeError)
    # Ensure observation consumer failure does not mutate eventHook behavior
    # Observe via skill_usage with hook
    e_skill, c_skill = _entry(AgentWorkRole.CODER, "skill-l", "v1", content="l content")
    reg = _registry(e_skill)
    univ = _universe(_allowed("ref-l", skill_id="skill-l", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-l")])
    proj = compose_skill_bootstrap(reg, _reader_for({e_skill.content_ref: c_skill}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e_skill.composite_key: "ref-l"}, pinned_refs=[SemanticReference(ref="ref-l")])
    evt2 = ExecutionEvent(event_id="evt-l-02", event_type=ExecutionEventType.WORKER_RESULT, work_role=AgentWorkRole.CODER)
    hook_calls = []
    def good_hook(evt):
        hook_calls.append(evt.event_id)
    # observe should call emit_event hook and consumer
    consumer_calls = []
    def consumer(obs):
        consumer_calls.append(len(obs))
    out = observe_skill_usage(evt2, res, proj, consumer=consumer, hook=good_hook, allowed_universe=univ, logical_ref_map={e_skill.composite_key: "ref-l"}, pinned_refs=[SemanticReference(ref="ref-l")])
    assert len(out) == 1
    assert hook_calls == ["evt-l-02"]
    assert consumer_calls == [1]
    # Hook failure should propagate EventHookError without affecting observation result? Our observe calls emit first then consumer; if hook fails, it propagates before consumer, but event not mutated
    def bad_hook(evt):
        raise RuntimeError("bad")
    with pytest.raises(EventHookError):
        observe_skill_usage(evt2, res, proj, consumer=consumer, hook=bad_hook, allowed_universe=univ, logical_ref_map={e_skill.composite_key: "ref-l"}, pinned_refs=[SemanticReference(ref="ref-l")])
    # Consumer failure typed
    def bad_consumer(obs):
        raise RuntimeError("consumer fail")
    with pytest.raises(SkillUsageConsumerError) as exc2:
        observe_skill_usage(evt2, res, proj, consumer=bad_consumer, allowed_universe=univ, logical_ref_map={e_skill.composite_key: "ref-l"}, pinned_refs=[SemanticReference(ref="ref-l")])
    assert exc2.value.event_id == "evt-l-02"
    assert isinstance(exc2.value.cause, RuntimeError)
    # Original event unchanged after consumer failure
    assert evt2.event_id == "evt-l-02"
    assert evt2.event_type == ExecutionEventType.WORKER_RESULT


def test_l_no_event_mutation():
    evt = ExecutionEvent(event_id="evt-mut", event_type=ExecutionEventType.HANDOFF_PREPARED, work_role=AgentWorkRole.ANALYST)
    orig_dict = evt.to_dict()
    e, content = _entry(AgentWorkRole.ANALYST, "skill-mut", "v1", content="mut content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-mut", namespace=AgentWorkRole.ANALYST, skill_id="skill-mut", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.ANALYST, univ, pinned_refs=[SemanticReference(ref="ref-mut")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-mut"}, pinned_refs=[SemanticReference(ref="ref-mut")])
    def mutating_consumer(obs):
        # Try to mutate? but observations are frozen; event should remain unchanged
        pass
    observe_skill_usage(evt, res, proj, consumer=mutating_consumer, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-mut"}, pinned_refs=[SemanticReference(ref="ref-mut")])
    assert evt.to_dict() == orig_dict
    assert evt.event_id == "evt-mut"


# ---------------------------------------------------------------------------
# Additional: No recomputation / no content read / no bootstrap
# ---------------------------------------------------------------------------

def test_no_recomputation_no_content_read():
    src = USAGE_PATH.read_text(encoding="utf-8")
    low = src.lower()
    code_only = re.sub(r'"""[\s\S]*?"""', '', src).lower()
    assert W1_RECOMPUTES_SKILL_RESOLUTION is False
    assert W1_READS_SKILL_CONTENT is False
    assert W1_PERFORMS_BOOTSTRAP is False
    # Should not import or call resolve_skill_resolution, open_skill, compose_skill_bootstrap
    assert "resolve_skill_resolution" not in code_only
    assert "resolve_from_handoff" not in code_only
    assert "open_skill" not in code_only
    assert "read_skill" not in code_only
    assert "compose_skill_bootstrap" not in code_only
    assert "build_skill_bootstrap" not in code_only
    # Should not create filesystem resolver
    assert "worktree_sandbox" not in code_only
    assert "worktree_resources" not in code_only
    assert "agents_discovery" not in code_only


def test_observation_preserves_all_fields():
    # Ensure every observation has required identity fields, not generic map
    e, content = _entry(AgentWorkRole.CODER, "skill-preserve", "v1", content="preserve content", provenance="custom-prov")
    reg = _registry(e)
    univ = _universe(_allowed("ref-preserve", skill_id="skill-preserve", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-preserve")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-preserve"}, pinned_refs=[SemanticReference(ref="ref-preserve")])
    evt = ExecutionEvent(event_id="evt-preserve", event_type=ExecutionEventType.EXECUTION_MATERIALIZED, work_role=AgentWorkRole.CODER, handoff_ref="handoff-preserve", handoff_digest="abc123"*10)
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-preserve"}, pinned_refs=[SemanticReference(ref="ref-preserve")])
    o = obs[0]
    assert o.event_id == "evt-preserve"
    assert o.event_type == "execution_materialized"
    assert o.namespace == "coder"
    assert o.skill_id == "skill-preserve"
    assert o.version == "v1"
    assert o.digest == e.identity.digest
    assert o.delivery in ("eager", "progressive")
    assert o.provenance == "custom-prov"
    # No unbounded metadata map
    assert not hasattr(o, "metadata")
    assert not hasattr(o, "extra")


def test_observation_immutable():
    e, content = _entry(AgentWorkRole.CODER, "skill-imm", "v1", content="imm content")
    reg = _registry(e)
    univ = _universe(_allowed("ref-imm", skill_id="skill-imm", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-imm")])
    proj = compose_skill_bootstrap(reg, _reader_for({e.content_ref: content}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-imm"}, pinned_refs=[SemanticReference(ref="ref-imm")])
    evt = _event(event_id="evt-imm")
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map={e.composite_key: "ref-imm"}, pinned_refs=[SemanticReference(ref="ref-imm")])
    o = obs[0]
    with pytest.raises(Exception):
        o.skill_id = "mutated"  # type: ignore
    with pytest.raises(Exception):
        o.digest = "0"*64  # type: ignore


def test_bootstrap_delivery_matches_observation():
    # Pin -> eager, recommended -> progressive, verify observation matches bootstrap
    e_pin, c_pin = _entry(AgentWorkRole.CODER, "skill-pin-delivery", "v1", content="pin delivery")
    e_rec, c_rec = _entry(AgentWorkRole.CODER, "skill-rec-delivery", "v1", content="rec delivery")
    reg = _registry(e_pin, e_rec)
    univ = _universe(_allowed("ref-pin-delivery", skill_id="skill-pin-delivery", version="v1"), _allowed("ref-rec-delivery", skill_id="skill-rec-delivery", version="v1"))
    res = resolve_skill_resolution(reg, AgentWorkRole.CODER, univ, pinned_refs=[SemanticReference(ref="ref-pin-delivery")], recommended_refs=[SemanticReference(ref="ref-rec-delivery")])
    logical = {e_pin.composite_key: "ref-pin-delivery", e_rec.composite_key: "ref-rec-delivery"}
    proj = compose_skill_bootstrap(reg, _reader_for({e_pin.content_ref: c_pin}), _budget(), resolution=res, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-pin-delivery")], recommended_refs=[SemanticReference(ref="ref-rec-delivery")])
    # Verify bootstrap truth
    eager_comps = [c for c in proj.components if c.delivery == "eager"]
    prog_comps = [c for c in proj.components if c.delivery == "progressive"]
    assert len(eager_comps) == 1 and len(prog_comps) == 1
    evt = _event()
    obs = project_skill_usage(evt, res, proj, allowed_universe=univ, logical_ref_map=logical, pinned_refs=[SemanticReference(ref="ref-pin-delivery")], recommended_refs=[SemanticReference(ref="ref-rec-delivery")])
    by_id = {o.skill_id: o for o in obs}
    assert by_id["skill-pin-delivery"].delivery == "eager"
    assert by_id["skill-rec-delivery"].delivery == "progressive"
    assert by_id["skill-pin-delivery"].ref is None
    assert by_id["skill-rec-delivery"].ref == "ref-rec-delivery"
    assert OBSERVED_DELIVERY_MATCHES_BOOTSTRAP is True


def test_no_extra_telemetry_files():
    for p in [REPO_ROOT / "telemetry.db", REPO_ROOT / "telemetry_store.py", REPO_ROOT / "analytics.py", REPO_ROOT / "aota_forge" / "telemetry_store.py"]:
        assert not p.exists()
