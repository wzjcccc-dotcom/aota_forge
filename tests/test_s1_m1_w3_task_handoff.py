"""S1 M1-W3 Task Handoff contract and invariants — bounded validation."""

from __future__ import annotations

import dataclasses
import pathlib
import pytest

from aota_forge.core.execution.roles import CanonicalRole
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.handoff import (
    FORBIDDEN_MECHANICAL_FIELDS,
    MAX_DIGEST_LENGTH,
    MAX_EXPECTATION_LENGTH,
    MAX_EXPECTATIONS_COUNT,
    MAX_OBJECTIVE_LENGTH,
    MAX_REF_LENGTH,
    MAX_REFS_PER_COLLECTION,
    MAX_SCOPE_LENGTH,
    MAX_TASK_KIND_LENGTH,
    MAX_TOTAL_REFS,
    OPTIONAL_REFERENCE_FIELDS,
    REQUIRED_CORE_FIELDS,
    SemanticReference,
    TaskHandoff,
    compute_handoff_digest,
)


def _make_minimal_handoff(
    work_role: AgentWorkRole | str = AgentWorkRole.CODER,
    task_kind: str = "implementation",
    objective: str = "Implement bounded semantic task handoff",
    bounded_scope: str = "aota_forge/work_plane/handoff.py",
    validation_expectations: tuple[str, ...] = ("pytest pass",),
    semantic_stop_expectations: tuple[str, ...] = ("escalate on unknown error",),
    **kwargs,
) -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind=task_kind,
        objective=objective,
        bounded_scope=bounded_scope,
        validation_expectations=validation_expectations,
        semantic_stop_expectations=semantic_stop_expectations,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# T01 — minimal valid Handoff accepted
# ---------------------------------------------------------------------------
def test_t01_minimal_valid_handoff_accepted() -> None:
    handoff = _make_minimal_handoff()
    assert handoff.work_role == AgentWorkRole.CODER
    assert handoff.task_kind == "implementation"
    assert handoff.objective == "Implement bounded semantic task handoff"
    assert handoff.bounded_scope == "aota_forge/work_plane/handoff.py"
    assert handoff.validation_expectations == ("pytest pass",)
    assert handoff.semantic_stop_expectations == ("escalate on unknown error",)
    assert handoff.project_ref is None
    assert handoff.plan_ref is None
    assert handoff.milestone_ref is None
    assert handoff.work_item_ref is None
    assert handoff.policy_refs == ()
    assert handoff.context_refs == ()
    assert handoff.evidence_refs == ()
    assert handoff.skill_refs == ()
    assert handoff.process_depth_or_risk_projection_ref is None
    assert isinstance(handoff.handoff_digest, str)
    assert len(handoff.handoff_digest) == 64


# ---------------------------------------------------------------------------
# T02 — all six required semantic core fields represented
# ---------------------------------------------------------------------------
def test_t02_all_six_required_semantic_core_fields_represented() -> None:
    expected_core = (
        "work_role",
        "task_kind",
        "objective",
        "bounded_scope",
        "validation_expectations",
        "semantic_stop_expectations",
    )
    assert REQUIRED_CORE_FIELDS == expected_core
    field_names = {f.name for f in dataclasses.fields(TaskHandoff)}
    for req in expected_core:
        assert req in field_names


# ---------------------------------------------------------------------------
# T03 — WorkRole uses AgentWorkRole contract
# ---------------------------------------------------------------------------
def test_t03_work_role_uses_agent_work_role_contract() -> None:
    # Accept typed AgentWorkRole enum member
    h1 = _make_minimal_handoff(work_role=AgentWorkRole.CODER)
    assert h1.work_role is AgentWorkRole.CODER

    h2 = _make_minimal_handoff(work_role=AgentWorkRole.TASK_MAIN)
    assert h2.work_role is AgentWorkRole.TASK_MAIN

    # Accept valid string and parse to AgentWorkRole
    h3 = _make_minimal_handoff(work_role="coder")
    assert h3.work_role is AgentWorkRole.CODER

    h4 = _make_minimal_handoff(work_role="analyst")
    assert h4.work_role is AgentWorkRole.ANALYST


# ---------------------------------------------------------------------------
# T04 — CanonicalRole rejected as Handoff WorkRole
# ---------------------------------------------------------------------------
def test_t04_canonical_role_rejected_as_handoff_work_role() -> None:
    for canon in [CanonicalRole.CODER, CanonicalRole.PLANNER, CanonicalRole.REVIEWER]:
        with pytest.raises(TypeError):
            _make_minimal_handoff(work_role=canon)


# ---------------------------------------------------------------------------
# T05 — unknown WorkRole rejected
# ---------------------------------------------------------------------------
def test_t05_unknown_work_role_rejected() -> None:
    for bad in ["unknown-role", "executor", "planner", "", "CODER", 123, None]:
        with pytest.raises((ValueError, TypeError)):
            _make_minimal_handoff(work_role=bad)


# ---------------------------------------------------------------------------
# T06 — empty objective rejected
# ---------------------------------------------------------------------------
def test_t06_empty_objective_rejected() -> None:
    for bad in ["", "   ", "\t\n"]:
        with pytest.raises(ValueError):
            _make_minimal_handoff(objective=bad)
    with pytest.raises(TypeError):
        _make_minimal_handoff(objective=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T07 — malformed task kind rejected
# ---------------------------------------------------------------------------
def test_t07_malformed_task_kind_rejected() -> None:
    for bad in ["", "   ", "\n"]:
        with pytest.raises(ValueError):
            _make_minimal_handoff(task_kind=bad)
    with pytest.raises(TypeError):
        _make_minimal_handoff(task_kind=123)  # type: ignore[arg-type]
    # Mechanical field name as task kind rejected
    with pytest.raises(ValueError):
        _make_minimal_handoff(task_kind="package_id")


# ---------------------------------------------------------------------------
# T08 — deterministic canonical serialization
# ---------------------------------------------------------------------------
def test_t08_deterministic_canonical_serialization() -> None:
    handoff = _make_minimal_handoff(
        policy_refs=("policy-b", "policy-a"),
        context_refs=("ctx-1",),
    )
    json1 = handoff.canonical_json()
    json2 = handoff.canonical_json()
    assert json1 == json2
    assert isinstance(json1, str)
    # Check that keys are sorted in canonical output
    dict1 = handoff.canonical_dict()
    assert "bounded_scope" in dict1
    assert "work_role" in dict1


# ---------------------------------------------------------------------------
# T09 — identical semantic Handoff produces identical digest
# ---------------------------------------------------------------------------
def test_t09_identical_semantic_handoff_produces_identical_digest() -> None:
    h1 = _make_minimal_handoff(
        work_role="coder",
        task_kind="feature",
        objective="Do the thing",
        bounded_scope="path/to/file.py",
        validation_expectations=("t1", "t2"),
        semantic_stop_expectations=("s1",),
        project_ref="proj-1",
    )
    h2 = _make_minimal_handoff(
        work_role=AgentWorkRole.CODER,
        task_kind="feature",
        objective="Do the thing",
        bounded_scope="path/to/file.py",
        validation_expectations=["t1", "t2"],
        semantic_stop_expectations=["s1"],
        project_ref=SemanticReference("proj-1"),
    )
    assert h1.handoff_digest == h2.handoff_digest
    assert compute_handoff_digest(h1) == compute_handoff_digest(h2)
    assert compute_handoff_digest(h1.to_dict()) == h1.handoff_digest


# ---------------------------------------------------------------------------
# T10 — objective change changes digest
# ---------------------------------------------------------------------------
def test_t10_objective_change_changes_digest() -> None:
    base = _make_minimal_handoff()
    modified = _make_minimal_handoff(objective="Different objective")
    assert base.handoff_digest != modified.handoff_digest


# ---------------------------------------------------------------------------
# T11 — bounded scope change changes digest
# ---------------------------------------------------------------------------
def test_t11_bounded_scope_change_changes_digest() -> None:
    base = _make_minimal_handoff()
    modified = _make_minimal_handoff(bounded_scope="different/scope.py")
    assert base.handoff_digest != modified.handoff_digest


# ---------------------------------------------------------------------------
# T12 — validation expectation change changes digest
# ---------------------------------------------------------------------------
def test_t12_validation_expectation_change_changes_digest() -> None:
    base = _make_minimal_handoff(validation_expectations=("e1",))
    modified = _make_minimal_handoff(validation_expectations=("e1", "e2"))
    assert base.handoff_digest != modified.handoff_digest


# ---------------------------------------------------------------------------
# T13 — semantic stop expectation change changes digest
# ---------------------------------------------------------------------------
def test_t13_semantic_stop_expectation_change_changes_digest() -> None:
    base = _make_minimal_handoff(semantic_stop_expectations=("s1",))
    modified = _make_minimal_handoff(semantic_stop_expectations=("s2",))
    assert base.handoff_digest != modified.handoff_digest


# ---------------------------------------------------------------------------
# T14 — applicable semantic ref change changes digest
# ---------------------------------------------------------------------------
def test_t14_applicable_semantic_ref_change_changes_digest() -> None:
    base = _make_minimal_handoff(project_ref="proj-a")
    modified = _make_minimal_handoff(project_ref="proj-b")
    assert base.handoff_digest != modified.handoff_digest

    with_none = _make_minimal_handoff(project_ref=None)
    assert with_none.handoff_digest != base.handoff_digest


# ---------------------------------------------------------------------------
# T15 — mechanical package field injection rejected
# ---------------------------------------------------------------------------
def test_t15_mechanical_package_field_injection_rejected() -> None:
    valid_dict = _make_minimal_handoff().to_dict()
    bad_dict = dict(valid_dict)
    bad_dict["package_id"] = "pkg-12345"

    with pytest.raises(ValueError, match="Forbidden mechanical field"):
        TaskHandoff.from_dict(bad_dict)

    bad_dict2 = dict(valid_dict)
    bad_dict2["protocol_version"] = "1.0.0"
    with pytest.raises(ValueError, match="Forbidden mechanical field"):
        TaskHandoff.from_dict(bad_dict2)


# ---------------------------------------------------------------------------
# T16 — idempotency/correlation/executor field injection rejected
# ---------------------------------------------------------------------------
def test_t16_idempotency_correlation_executor_field_injection_rejected() -> None:
    valid_dict = _make_minimal_handoff().to_dict()
    for forbidden in FORBIDDEN_MECHANICAL_FIELDS:
        bad_dict = dict(valid_dict)
        bad_dict[forbidden] = "injected_val"
        with pytest.raises(ValueError, match="Forbidden mechanical field"):
            TaskHandoff.from_dict(bad_dict)


# ---------------------------------------------------------------------------
# T17 — references do not become authority
# ---------------------------------------------------------------------------
def test_t17_references_do_not_become_authority() -> None:
    """Verify semantic reference invariant: ID_IS_AUTHORITY=no, REFERENCE != BINDING."""
    ref = SemanticReference(ref="aota_forge/projects/demo", digest="sha256:abc")
    handoff = _make_minimal_handoff(project_ref=ref)

    # Reference is preserved as value object
    assert handoff.project_ref is not None
    assert handoff.project_ref.ref == "aota_forge/projects/demo"
    assert handoff.project_ref.digest == "sha256:abc"

    # Reference carries no authority execution attributes or methods
    assert not hasattr(handoff.project_ref, "is_authorized")
    assert not hasattr(handoff.project_ref, "bind_execution")
    assert not hasattr(handoff.project_ref, "authorize")
    assert not hasattr(handoff, "bind_runtime")
    assert not hasattr(handoff, "execute")


# ---------------------------------------------------------------------------
# T18 — optional refs remain bounded
# ---------------------------------------------------------------------------
def test_t18_optional_refs_remain_bounded() -> None:
    # Excessive single collection size rejected
    too_many_policies = tuple(f"policy-{i}" for i in range(MAX_REFS_PER_COLLECTION + 1))
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(policy_refs=too_many_policies)

    # Excessive ref string length rejected
    long_ref = "x" * (MAX_REF_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        SemanticReference(ref=long_ref)

    # Excessive digest string length rejected
    long_digest = "d" * (MAX_DIGEST_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds maximum"):
        SemanticReference(ref="valid-ref", digest=long_digest)


# ---------------------------------------------------------------------------
# T19 — no ExecutionPackage creation
# ---------------------------------------------------------------------------
def test_t19_no_execution_package_creation() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/handoff.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        assert "ExecutionPackage" not in stripped
        assert "create_execution_package" not in stripped
        assert "ExecutionDispatcher" not in stripped


# ---------------------------------------------------------------------------
# T20 — no WorkRole to CanonicalRole mapping implementation
# ---------------------------------------------------------------------------
def test_t20_no_work_role_to_canonical_role_mapping_implementation() -> None:
    import aota_forge.work_plane.handoff as wp_handoff

    forbidden = [
        "WorkRoleMapping",
        "resolve_work_role",
        "resolve_work_role_to_canonical_role",
        "to_canonical_role",
        "to_canonical",
        "mapping",
    ]
    for name in forbidden:
        assert not hasattr(wp_handoff, name), f"forbidden mapping symbol in handoff: {name}"

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/handoff.py").read_text(encoding="utf-8").lower()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        assert "resolve_work_role" not in stripped
        assert "workrolemapping" not in stripped


# ---------------------------------------------------------------------------
# T21 — no Hermes dependency
# ---------------------------------------------------------------------------
def test_t21_no_hermes_dependency() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    src = (repo_root / "aota_forge/work_plane/handoff.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    assert "hermes_profile" not in src.lower()
    assert "hermeshostclient" not in src.lower()


# ---------------------------------------------------------------------------
# T22 — digest covers all execution-relevant semantic handoff fields
# ---------------------------------------------------------------------------
def test_t22_digest_covers_all_execution_relevant_fields() -> None:
    base = TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="kind-1",
        objective="obj-1",
        bounded_scope="scope-1",
        validation_expectations=("v1",),
        semantic_stop_expectations=("s1",),
        project_ref=SemanticReference("proj-1"),
        plan_ref=SemanticReference("plan-1"),
        milestone_ref=SemanticReference("m-1"),
        work_item_ref=SemanticReference("w-1"),
        policy_refs=(SemanticReference("pol-1"),),
        context_refs=(SemanticReference("ctx-1"),),
        evidence_refs=(SemanticReference("ev-1"),),
        skill_refs=(SemanticReference("sk-1"),),
        process_depth_or_risk_projection_ref=SemanticReference("risk-1"),
    )
    base_digest = base.handoff_digest

    # 1. work_role change
    h_wr = dataclasses.replace(base, work_role=AgentWorkRole.ANALYST)
    assert h_wr.handoff_digest != base_digest

    # 2. task_kind change
    h_tk = dataclasses.replace(base, task_kind="kind-2")
    assert h_tk.handoff_digest != base_digest

    # 3. objective change
    h_obj = dataclasses.replace(base, objective="obj-2")
    assert h_obj.handoff_digest != base_digest

    # 4. bounded_scope change
    h_sc = dataclasses.replace(base, bounded_scope="scope-2")
    assert h_sc.handoff_digest != base_digest

    # 5. validation_expectations change
    h_val = dataclasses.replace(base, validation_expectations=("v2",))
    assert h_val.handoff_digest != base_digest

    # 6. semantic_stop_expectations change
    h_stp = dataclasses.replace(base, semantic_stop_expectations=("s2",))
    assert h_stp.handoff_digest != base_digest

    # 7. project_ref change
    h_proj = dataclasses.replace(base, project_ref=SemanticReference("proj-2"))
    assert h_proj.handoff_digest != base_digest

    # 8. plan_ref change
    h_plan = dataclasses.replace(base, plan_ref=SemanticReference("plan-2"))
    assert h_plan.handoff_digest != base_digest

    # 9. milestone_ref change
    h_ms = dataclasses.replace(base, milestone_ref=SemanticReference("m-2"))
    assert h_ms.handoff_digest != base_digest

    # 10. work_item_ref change
    h_wi = dataclasses.replace(base, work_item_ref=SemanticReference("w-2"))
    assert h_wi.handoff_digest != base_digest

    # 11. policy_refs change
    h_pol = dataclasses.replace(base, policy_refs=(SemanticReference("pol-2"),))
    assert h_pol.handoff_digest != base_digest

    # 12. context_refs change
    h_ctx = dataclasses.replace(base, context_refs=(SemanticReference("ctx-2"),))
    assert h_ctx.handoff_digest != base_digest

    # 13. evidence_refs change
    h_ev = dataclasses.replace(base, evidence_refs=(SemanticReference("ev-2"),))
    assert h_ev.handoff_digest != base_digest

    # 14. skill_refs change
    h_sk = dataclasses.replace(base, skill_refs=(SemanticReference("sk-2"),))
    assert h_sk.handoff_digest != base_digest

    # 15. process_depth_or_risk_projection_ref change
    h_risk = dataclasses.replace(
        base, process_depth_or_risk_projection_ref=SemanticReference("risk-2")
    )
    assert h_risk.handoff_digest != base_digest

    # 16. ref digest change
    h_digest_chg = dataclasses.replace(
        base, project_ref=SemanticReference("proj-1", digest="sha256:123")
    )
    assert h_digest_chg.handoff_digest != base_digest


# ---------------------------------------------------------------------------
# T23 — serialization roundtrip
# ---------------------------------------------------------------------------
def test_t23_serialization_roundtrip() -> None:
    original = TaskHandoff(
        work_role=AgentWorkRole.REVIEWER,
        task_kind="audit",
        objective="Verify security boundary",
        bounded_scope="aota_forge/security",
        validation_expectations=("pass static analysis", "pass unit tests"),
        semantic_stop_expectations=("stop on high severity defect",),
        project_ref=SemanticReference("proj-sec", digest="sha256:111"),
        plan_ref=SemanticReference("plan-s1"),
        milestone_ref=SemanticReference("m1"),
        work_item_ref=SemanticReference("w3"),
        policy_refs=(SemanticReference("sec-policy-1"), SemanticReference("sec-policy-2")),
        context_refs=(SemanticReference("ctx-sec"),),
        evidence_refs=(SemanticReference("ev-prior"),),
        skill_refs=(SemanticReference("skill-audit"),),
        process_depth_or_risk_projection_ref=SemanticReference("deep-audit"),
    )
    d = original.to_dict()
    reconstructed = TaskHandoff.from_dict(d)

    assert reconstructed == original
    assert reconstructed.handoff_digest == original.handoff_digest
    assert reconstructed.to_dict() == d


# ---------------------------------------------------------------------------
# T24 — unknown field fail-closed
# ---------------------------------------------------------------------------
def test_t24_unknown_field_fail_closed() -> None:
    valid_dict = _make_minimal_handoff().to_dict()
    valid_dict["unrecognized_field"] = "some_value"
    with pytest.raises(ValueError, match="Unknown field"):
        TaskHandoff.from_dict(valid_dict)


# ---------------------------------------------------------------------------
# T25 — immutability / frozen dataclass
# ---------------------------------------------------------------------------
def test_t25_immutability_frozen_dataclass() -> None:
    handoff = _make_minimal_handoff()
    with pytest.raises(dataclasses.FrozenInstanceError):
        handoff.objective = "mutate"  # type: ignore[misc]

    ref = SemanticReference(ref="foo")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.ref = "mutate"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T26 — collection reference ordering determinism
# ---------------------------------------------------------------------------
def test_t26_collection_reference_ordering_determinism() -> None:
    ref_a = SemanticReference("policy-a")
    ref_b = SemanticReference("policy-b")
    ref_c = SemanticReference("policy-c", digest="sha256:333")

    h1 = _make_minimal_handoff(policy_refs=(ref_a, ref_b, ref_c))
    h2 = _make_minimal_handoff(policy_refs=(ref_c, ref_a, ref_b))

    # Even though input sequence differed, canonical dict, canonical json, and digest are identical
    assert h1.canonical_dict() == h2.canonical_dict()
    assert h1.canonical_json() == h2.canonical_json()
    assert h1.handoff_digest == h2.handoff_digest


# ---------------------------------------------------------------------------
# T27 — SemanticReference edge cases
# ---------------------------------------------------------------------------
def test_t27_semantic_reference_edge_cases() -> None:
    # String construction
    r1 = SemanticReference.from_value("my-ref")
    assert r1.ref == "my-ref"
    assert r1.digest is None

    # Dict construction
    r2 = SemanticReference.from_value({"ref": "my-ref-2", "digest": "sha256:xyz"})
    assert r2.ref == "my-ref-2"
    assert r2.digest == "sha256:xyz"

    # Reject invalid types
    for bad in [123, [], object(), None]:
        with pytest.raises(TypeError):
            SemanticReference.from_value(bad)

    # Reject empty ref / digest
    with pytest.raises(ValueError):
        SemanticReference(ref="")
    with pytest.raises(ValueError):
        SemanticReference(ref="valid", digest="")

    # Reject mechanical field in mapping
    with pytest.raises(ValueError, match="Forbidden mechanical field"):
        SemanticReference.from_value({"ref": "valid", "package_id": "bad"})


# ---------------------------------------------------------------------------
# T28 — bounded strings validation
# ---------------------------------------------------------------------------
def test_t28_bounded_strings_validation() -> None:
    # Long objective
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(objective="a" * (MAX_OBJECTIVE_LENGTH + 1))

    # Long task_kind
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(task_kind="k" * (MAX_TASK_KIND_LENGTH + 1))

    # Long scope
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(bounded_scope="s" * (MAX_SCOPE_LENGTH + 1))

    # Long expectation
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(validation_expectations=("e" * (MAX_EXPECTATION_LENGTH + 1),))


# ---------------------------------------------------------------------------
# T29 — non-string and invalid expectation lists
# ---------------------------------------------------------------------------
def test_t29_invalid_expectation_lists() -> None:
    with pytest.raises(TypeError):
        _make_minimal_handoff(validation_expectations="not-a-tuple")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        _make_minimal_handoff(validation_expectations=(123,))  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        _make_minimal_handoff(validation_expectations=("",))

    too_many = tuple(f"exp-{i}" for i in range(MAX_EXPECTATIONS_COUNT + 1))
    with pytest.raises(ValueError, match="exceeds maximum"):
        _make_minimal_handoff(validation_expectations=too_many)


# ---------------------------------------------------------------------------
# T30 — handoff digest distinct domain from ExecutionPackage intent_fingerprint
# ---------------------------------------------------------------------------
def test_t30_handoff_digest_distinct_domain() -> None:
    handoff = _make_minimal_handoff()
    d = handoff.handoff_digest
    assert isinstance(d, str)
    assert len(d) == 64
    # Compute intent fingerprint from package module
    from aota_forge.core.execution.package import compute_intent_fingerprint

    intent_fp = compute_intent_fingerprint(
        canonical_role="coder",
        instruction="Implement bounded semantic task handoff",
        input_artifacts=(),
        project_id="aota_forge",
    )
    # Distinct payload structure and domain ensure different hashes
    assert d != intent_fp
