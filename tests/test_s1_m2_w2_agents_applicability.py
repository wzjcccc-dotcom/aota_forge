"""S1 M2-W2 AGENTS Applicability Semantic Contract — bounded validation."""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from aota_forge.work_plane.agents_applicability import (
    AGENTS_POLICY_APPLIES_TO_ALL_ROLES,
    AGENTS_POLICY_IS_PLAN_AUTHORITY,
    AgentsPolicyCandidate,
    AgentsPolicyError,
    AmbiguousPolicyError,
    CrossProjectPolicyError,
    UnboundedContentError,
    compute_policy_digest,
    resolve_applicable_policies,
)
from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES


def _cand(
    policy_id="pol-root-1",
    project_id="proj-alpha",
    scope="",
    content="root policy content",
    provenance_ref=None,
    content_digest=None,
):
    kwargs: dict = {
        "policy_id": policy_id,
        "project_id": project_id,
        "scope": scope,
    }
    if content is not None:
        kwargs["content"] = content
    if content_digest is not None:
        kwargs["content_digest"] = content_digest
    elif content is None:
        # reference-only needs digest; compute from logical placeholder if not given
        kwargs["content_digest"] = hashlib.sha256(b"ref-content").hexdigest()
    if provenance_ref is not None:
        kwargs["provenance_ref"] = provenance_ref
    return AgentsPolicyCandidate(**kwargs)


# T01 valid bounded root policy candidate accepted
def test_t01_valid_bounded_root_policy_candidate_accepted():
    c = _cand(scope="", content="root bounded material")
    assert c.scope == ""
    assert c.is_root is True
    assert c.specificity == 0
    assert c.project_id == "proj-alpha"
    assert len(c.content_digest) == 64


def test_t01_root_alias():
    c = _cand(scope="root", content="root alias")
    assert c.scope == ""
    assert c.is_root is True


# T02 valid nested scoped candidate accepted
def test_t02_valid_nested_scoped_candidate_accepted():
    c = _cand(policy_id="pol-nested-1", scope="services/api", content="nested content")
    assert c.scope == "services/api"
    assert c.specificity == 2
    assert c.is_root is False


def test_t02_single_level_nested():
    c = _cand(policy_id="pol-a", scope="a", content="a content")
    assert c.scope == "a"
    assert c.specificity == 1


# T03 deterministic applicable policy ordering
def test_t03_deterministic_applicable_policy_ordering():
    root = _cand(policy_id="pol-root", scope="", content="root")
    mid = _cand(policy_id="pol-mid", scope="a", content="mid")
    deep = _cand(policy_id="pol-deep", scope="a/b", content="deep")
    result = resolve_applicable_policies([deep, root, mid], "proj-alpha")
    assert [r.scope for r in result] == ["", "a", "a/b"]
    # tie-break stability within same specificity uses lexical scope
    c1 = _cand(policy_id="pol-x", scope="a/b", content="c1")
    c2 = _cand(policy_id="pol-y", scope="a/c", content="c2")
    result2 = resolve_applicable_policies([c2, c1], "proj-alpha")
    # both depth 2, lexical order decides
    assert result2[0].scope < result2[1].scope


# T04 same inputs different input order -> same effective semantic result
def test_t04_same_inputs_different_order_same_result():
    candidates = [
        _cand(policy_id="pol-1", scope="", content="r"),
        _cand(policy_id="pol-2", scope="a", content="a"),
        _cand(policy_id="pol-3", scope="a/b", content="ab"),
    ]
    result_a = resolve_applicable_policies(candidates, "proj-alpha")
    result_b = resolve_applicable_policies(list(reversed(candidates)), "proj-alpha")
    result_c = resolve_applicable_policies([candidates[1], candidates[2], candidates[0]], "proj-alpha")
    assert result_a == result_b == result_c
    # ensure not incidental order dependent
    assert [r.policy_id for r in result_a] == [r.policy_id for r in result_b]


# T05 duplicate ambiguous same-scope candidates -> fail closed
def test_t05_duplicate_ambiguous_same_scope_fail_closed():
    c1 = _cand(policy_id="pol-one", scope="a", content="content one")
    c2 = _cand(policy_id="pol-two", scope="a", content="content two")
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c1, c2], "proj-alpha")
    # also from AgentsPolicyError
    with pytest.raises(AgentsPolicyError):
        resolve_applicable_policies([c1, c2], "proj-alpha")


def test_t05_same_scope_same_identity_digest_dedup():
    # identical duplicate not ambiguous (deduplicated)
    c1 = _cand(policy_id="pol-dup", scope="a", content="same")
    c2 = _cand(policy_id="pol-dup", scope="a", content="same")
    result = resolve_applicable_policies([c1, c2], "proj-alpha")
    assert len(result) == 1
    assert result[0].policy_id == "pol-dup"


# T06 conflicting digest/identity ambiguity -> fail closed
def test_t06_conflicting_digest_ambiguity_fail_closed():
    # Same scope, same policy_id but different digest (different content) -> fail
    c1 = AgentsPolicyCandidate(
        policy_id="pol-conf",
        project_id="proj-alpha",
        scope="a",
        content="content A",
    )
    c2 = AgentsPolicyCandidate(
        policy_id="pol-conf",
        project_id="proj-alpha",
        scope="a",
        content="content B",
    )
    # digests differ
    assert c1.content_digest != c2.content_digest
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c1, c2], "proj-alpha")

    # Same scope different policy_id also ambiguous (already covered but assert)
    c3 = _cand(policy_id="pol-a", scope="x", content="aa")
    c4 = _cand(policy_id="pol-b", scope="x", content="bb")
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c3, c4], "proj-alpha")


# T07 unknown/malformed candidate fields rejected
def test_t07_unknown_malformed_candidate_fields_rejected():
    # unknown field via from_dict
    with pytest.raises(ValueError):
        AgentsPolicyCandidate.from_dict(
            {"policy_id": "pol-1", "project_id": "proj-alpha", "scope": "", "content": "hi", "unknown_field": "bad"}
        )
    # missing required
    with pytest.raises(ValueError):
        AgentsPolicyCandidate.from_dict({"policy_id": "pol-1", "project_id": "proj-alpha"})
    # malformed type: policy_id int
    with pytest.raises((TypeError, ValueError)):
        AgentsPolicyCandidate(policy_id=123, project_id="proj-alpha", scope="", content="hi")  # type: ignore[arg-type]
    # malformed scope type
    with pytest.raises((TypeError, ValueError)):
        AgentsPolicyCandidate(policy_id="pol-1", project_id="proj-alpha", scope=123, content="hi")  # type: ignore[arg-type]
    # malformed digest
    with pytest.raises(ValueError):
        AgentsPolicyCandidate(policy_id="pol-1", project_id="proj-alpha", scope="", content=None, content_digest="not-hex")
    # from_dict with wrong type
    with pytest.raises(TypeError):
        AgentsPolicyCandidate.from_dict("not-a-mapping")  # type: ignore[arg-type]
    # resolve with wrong candidate type
    with pytest.raises(TypeError):
        resolve_applicable_policies(["not-a-candidate"], "proj-alpha")  # type: ignore[arg-type]


# T08 unbounded policy material rejected
def test_t08_unbounded_policy_material_rejected():
    big = "x" * (32 * 1024 + 1)
    with pytest.raises((UnboundedContentError, ValueError)):
        AgentsPolicyCandidate(policy_id="pol-big", project_id="proj-alpha", scope="", content=big)
    with pytest.raises((UnboundedContentError, ValueError)):
        compute_policy_digest(big)
    # exactly at bound passes
    exact = "y" * (32 * 1024)
    c = AgentsPolicyCandidate(policy_id="pol-exact", project_id="proj-alpha", scope="", content=exact)
    assert c.content_digest is not None


# T09 deterministic policy digest/integrity behavior
def test_t09_deterministic_policy_digest():
    c1 = _cand(policy_id="pol-1", scope="", content="same content")
    c2 = _cand(policy_id="pol-1", scope="", content="same content")
    assert c1.content_digest == c2.content_digest == compute_policy_digest("same content")
    c3 = _cand(policy_id="pol-1", scope="", content="different content")
    assert c1.content_digest != c3.content_digest
    # digest matches hashlib sha256
    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert compute_policy_digest("hello world") == expected
    # tampered digest mismatch fails
    with pytest.raises(ValueError):
        AgentsPolicyCandidate(
            policy_id="pol-tamper",
            project_id="proj-alpha",
            scope="",
            content="hello",
            content_digest="0" * 64,
        )
    # reference-only digest stable
    ref = AgentsPolicyCandidate(
        policy_id="pol-ref",
        project_id="proj-alpha",
        scope="",
        content=None,
        content_digest=expected,
        provenance_ref="agents:ref:1",
    )
    assert ref.content_digest == expected
    ref2 = AgentsPolicyCandidate(
        policy_id="pol-ref2",
        project_id="proj-alpha",
        scope="",
        content=None,
        content_digest=expected,
        provenance_ref="agents:ref:1",
    )
    assert ref.content_digest == ref2.content_digest


# T10 raw arbitrary absolute path authority rejected
def test_t10_raw_arbitrary_absolute_path_rejected():
    # policy_id as path
    with pytest.raises(ValueError):
        _cand(policy_id="/etc/passwd", scope="", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="/tmp/evil", scope="a", content="hi")
    # provenance_ref absolute path
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="", content="hi", provenance_ref="/etc/passwd")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="", content="hi", provenance_ref="/home/user/.ssh/id_rsa")
    # scope absolute path
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="/etc/passwd", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="/a/b", content="hi")
    # project_id as path
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", project_id="/tmp/proj", scope="", content="hi")
    # from_dict with absolute path
    with pytest.raises(ValueError):
        AgentsPolicyCandidate.from_dict(
            {"policy_id": "/tmp/bad", "project_id": "proj-alpha", "scope": "", "content": "hi"}
        )
    # windows absolute
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="", content="hi", provenance_ref="C:/Windows/System32")


# T11 traversal-like path authority not accepted
def test_t11_traversal_like_path_rejected():
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="../", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="a/../b", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="a/../../b", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="a/b/../c", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="..", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="a//b", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="./a", content="hi")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="a/./b", content="hi")
    # provenance_ref traversal
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="", content="hi", provenance_ref="../escape")
    with pytest.raises(ValueError):
        _cand(policy_id="pol-1", scope="", content="hi", provenance_ref="a/../b")
    # policy_id traversal (ids don't allow / but check "..")
    with pytest.raises(ValueError):
        _cand(policy_id="..", scope="", content="hi")


# T12 cross-project semantic binding mismatch rejected
def test_t12_cross_project_binding_mismatch_rejected():
    c1 = _cand(policy_id="pol-1", project_id="proj-alpha", scope="", content="r")
    c2 = _cand(policy_id="pol-2", project_id="proj-beta", scope="a", content="a")
    with pytest.raises(CrossProjectPolicyError):
        resolve_applicable_policies([c1, c2], "proj-alpha")
    with pytest.raises(CrossProjectPolicyError):
        resolve_applicable_policies([c1, c2], "proj-beta")
    # single candidate with mismatched target
    with pytest.raises(CrossProjectPolicyError):
        resolve_applicable_policies([c1], "proj-beta")
    # ensure error is also AgentsPolicyError
    with pytest.raises(AgentsPolicyError):
        resolve_applicable_policies([c2], "proj-alpha")


# T13 candidate bound to intended worktree/project identity
def test_t13_candidate_bound_to_intended_worktree():
    c = _cand(policy_id="pol-1", project_id="proj-expected", scope="a", content="hi")
    assert c.project_id == "proj-expected"
    result = resolve_applicable_policies([c], "proj-expected")
    assert result[0].project_id == "proj-expected"
    # mismatch already tested in T12, ensure bound identity preserved via to_dict
    d = c.to_dict()
    assert d["project_id"] == "proj-expected"
    c2 = AgentsPolicyCandidate.from_dict(d)
    assert c2.project_id == "proj-expected"


# T14 no filesystem discovery
def test_t14_no_filesystem_discovery():
    src = pathlib.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    lower = src.lower()
    # ensure no discovery mechanics
    assert "path.walk" not in lower
    assert "os.walk" not in lower
    # glob as function call unlikely; ensure not present as discovery
    # allow word glob in comments? but we removed; check exact forbidden
    for pat in ["path.walk", "os.walk", "realpath", "discovery"]:
        assert pat not in lower
    # ensure no filesystem imports for traversal
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "pathlib" not in stripped or "path" not in stripped or "pathlib" in stripped and False, "pathlib import should not be used for discovery"
    # we don't import os at all
    assert "import os" not in lower


# T15 no TrustedResourceResolver modification
def test_t15_no_trusted_resource_resolver_modification():
    import pathlib as _p
    resolver_path = _p.Path("aota_forge/core/resources/resolver.py")
    src = resolver_path.read_text(encoding="utf-8")
    # Ensure AGENTS resource kind not added
    assert '"agents"' not in src.lower()
    assert "'agents'" not in src.lower()
    assert "agents_policy" not in src.lower()
    assert "agents_applicability" not in src.lower()
    # Work plane file must not modify resolver
    wp_src = _p.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    assert "TrustedResourceResolver" not in wp_src
    assert "RESOURCE_KINDS" not in wp_src
    # git diff check is done externally, but ensure file unchanged from base by checking expected kinds
    from aota_forge.core.resources.resolver import RESOURCE_KINDS

    assert set(RESOURCE_KINDS.keys()) == {
        "runtime_pidfile",
        "managed_deployment_receipt",
        "runtime_identity",
        "project_registry",
        "backup_receipt",
    }


# T16 no symlink walking
def test_t16_no_symlink_walking():
    src = pathlib.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "symlink" not in lower
    assert "readlink" not in lower
    assert "lstat" not in lower


# T17 no sandbox implementation
def test_t17_no_sandbox_implementation():
    src = pathlib.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "sandbox" not in lower
    assert "restricted_shell" not in lower
    assert "workspace mutation" not in lower


# T18 no bootstrap bundle implementation
def test_t18_no_bootstrap_bundle_implementation():
    src = pathlib.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "bootstrapbundle" not in lower
    assert "bootstrap_bundle" not in lower
    # also check no bundle allocator etc. we didn't implement
    assert "budget allocator" not in lower
    # ensure no Bootstrap class
    assert "class bootstrap" not in lower


# T19 applies to five Agent Work Roles semantically
def test_t19_applies_to_five_roles():
    assert AGENTS_POLICY_APPLIES_TO_ALL_ROLES is True
    assert AGENTS_POLICY_IS_PLAN_AUTHORITY is False
    from aota_forge.work_plane.agents_applicability import agents_policy_applies_to_role

    for role in WORK_ROLES:
        assert agents_policy_applies_to_role(role) is True
    for member in AgentWorkRole:
        assert agents_policy_applies_to_role(member) is True
        assert agents_policy_applies_to_role(member.value) is True
    # String check
    assert agents_policy_applies_to_role("task-main") is True
    assert agents_policy_applies_to_role("analyst") is True
    assert agents_policy_applies_to_role("coder") is True
    assert agents_policy_applies_to_role("reviewer") is True
    assert agents_policy_applies_to_role("project-steward") is True


# T20 zero Hermes dependency
def test_t20_zero_hermes_dependency():
    src = pathlib.Path("aota_forge/work_plane/agents_applicability.py").read_text(encoding="utf-8")
    lower = src.lower()
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    assert "hermes_profile" not in lower
    assert "hermeshostclient" not in lower
    assert "hermes_adapter" not in lower


# Additional: immutability and bounded content already covered


def test_additional_candidate_immutability():
    c = _cand(policy_id="pol-imm", scope="a", content="imm")
    with pytest.raises(Exception):
        c.policy_id = "changed"  # type: ignore[misc]


def test_additional_empty_list():
    assert resolve_applicable_policies([], "proj-alpha") == ()


def test_additional_opaque_logical_ref_preferred_over_path():
    # opaque logical ref like agents:// is allowed
    c = _cand(
        policy_id="pol-ref1",
        scope="",
        content="hi",
        provenance_ref="agents://proj-alpha/root:v1",
    )
    assert c.provenance_ref == "agents://proj-alpha/root:v1"
    # but absolute path not allowed
    with pytest.raises(ValueError):
        _cand(policy_id="pol-ref2", scope="", content="hi", provenance_ref="/tmp/agents.md")
