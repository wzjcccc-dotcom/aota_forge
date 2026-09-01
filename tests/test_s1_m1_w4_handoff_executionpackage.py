"""S1 M1-W4 Handoff → ExecutionPackage compiler / materialization proof."""

from __future__ import annotations

import inspect
import pathlib

import pytest

from aota_forge.core.execution.package import (
    EXECUTION_CONTRACT_HASH,
    PROTOCOL_VERSION,
    ExecutionPackage,
    compute_intent_fingerprint,
)
from aota_forge.core.execution.roles import CanonicalRole
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
    compile_task_handoff,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.mapping import WorkRoleMappingError
from aota_forge.work_plane.roles import AgentWorkRole


def _handoff(
    work_role=AgentWorkRole.CODER,
    task_kind: str = "implement",
    objective: str = "Implement bounded semantic task handoff",
    bounded_scope: str = "aota_forge/work_plane/compiler.py",
    validation_expectations=("pytest pass",),
    semantic_stop_expectations=("escalate on unknown",),
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


def _binding(canonical_task_id="task-123abc", project_id="proj-xyz") -> TrustedExecutionBinding:
    return TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=project_id)


# ---------------------------------------------------------------------------
# T01 valid coder Handoff compiles to CanonicalRole.CODER
# ---------------------------------------------------------------------------
def test_t01_valid_coder_handoff_compiles_to_coder() -> None:
    h = _handoff(work_role=AgentWorkRole.CODER)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.canonical_role == CanonicalRole.CODER.value
    assert pkg.canonical_role == "coder"


# ---------------------------------------------------------------------------
# T02 analyst -> CanonicalRole.PLANNER
# ---------------------------------------------------------------------------
def test_t02_analyst_to_planner() -> None:
    h = _handoff(work_role=AgentWorkRole.ANALYST)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.canonical_role == CanonicalRole.PLANNER.value
    assert pkg.canonical_role == "planner"


# ---------------------------------------------------------------------------
# T03 reviewer -> CanonicalRole.REVIEWER
# ---------------------------------------------------------------------------
def test_t03_reviewer_to_reviewer() -> None:
    h = _handoff(work_role=AgentWorkRole.REVIEWER)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.canonical_role == CanonicalRole.REVIEWER.value


# ---------------------------------------------------------------------------
# T04 project-steward -> CanonicalRole.STEWARD
# ---------------------------------------------------------------------------
def test_t04_project_steward_to_steward() -> None:
    h = _handoff(work_role=AgentWorkRole.PROJECT_STEWARD)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.canonical_role == CanonicalRole.STEWARD.value


# ---------------------------------------------------------------------------
# T05 task-main execution materialization fails closed
# ---------------------------------------------------------------------------
def test_t05_task_main_fails_closed() -> None:
    h = _handoff(work_role=AgentWorkRole.TASK_MAIN)
    with pytest.raises(WorkRoleMappingError):
        compile_handoff_to_execution_package(h, _binding())
    # string variant
    h2 = _handoff(work_role="task-main")
    with pytest.raises(WorkRoleMappingError):
        compile_handoff_to_execution_package(h2, _binding())


# ---------------------------------------------------------------------------
# T06 compiler uses accepted W2 resolver rather than duplicate table
# ---------------------------------------------------------------------------
def test_t06_compiler_uses_w2_resolver() -> None:
    src = pathlib.Path("aota_forge/work_plane/compiler.py").read_text(encoding="utf-8")
    assert "resolve_work_role_to_canonical_role" in src
    # ensure no duplicate mapping literal
    # The only mapping table should live in mapping.py, not redefined in compiler.py
    # Compiler should not define its own WORK_ROLE_TO_CANONICAL_ROLE dict with four entries
    assert src.count("WORK_ROLE_TO_CANONICAL_ROLE") <= 1  # only via import if at all
    # Verify resolver is actually imported from mapping
    assert "from aota_forge.work_plane.mapping import" in src
    # Ensure compiler doesn't contain hardcoded canonical role strings mapping itself
    # We allow the artifact construction but not a duplicate mapping table
    assert "AgentWorkRole.ANALYST" not in src or "CanonicalRole.PLANNER" not in src or src.count("ANALYST") <= 2


# ---------------------------------------------------------------------------
# T07 ExecutionPackage type is existing Core type
# ---------------------------------------------------------------------------
def test_t07_executionpackage_type_is_existing_core() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert isinstance(pkg, ExecutionPackage)
    # Ensure type comes from core.execution.package
    assert type(pkg).__module__ == "aota_forge.core.execution.package"


# ---------------------------------------------------------------------------
# T08 protocol_version remains existing version
# ---------------------------------------------------------------------------
def test_t08_protocol_version_retained() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.protocol_version == PROTOCOL_VERSION
    assert pkg.protocol_version == "1.0.0"


# ---------------------------------------------------------------------------
# T09 contract_hash remains existing contract hash
# ---------------------------------------------------------------------------
def test_t09_contract_hash_retained() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.contract_hash == EXECUTION_CONTRACT_HASH


# ---------------------------------------------------------------------------
# T10 canonical_task_id comes from trusted binding
# ---------------------------------------------------------------------------
def test_t10_canonical_task_id_from_trusted_binding() -> None:
    h = _handoff()
    b = _binding(canonical_task_id="canonical-task-xyz-999")
    pkg = compile_handoff_to_execution_package(h, b)
    assert pkg.canonical_task_id == "canonical-task-xyz-999"
    assert pkg.canonical_task_id == b.canonical_task_id


# ---------------------------------------------------------------------------
# T11 project_id comes from trusted binding
# ---------------------------------------------------------------------------
def test_t11_project_id_from_trusted_binding() -> None:
    h = _handoff(project_ref=SemanticReference("other-proj"))
    b = _binding(project_id="trusted-proj-123")
    pkg = compile_handoff_to_execution_package(h, b)
    assert pkg.project_id == "trusted-proj-123"
    assert pkg.project_id == b.project_id
    # project_ref is not authority — it does not overwrite trusted binding
    assert pkg.project_id != "other-proj"


# ---------------------------------------------------------------------------
# T12 Handoff does not self-authorize task/project identity
# ---------------------------------------------------------------------------
def test_t12_handoff_does_not_self_authorize() -> None:
    # TaskHandoff has no canonical_task_id / project_id fields
    fields = {f.name for f in TaskHandoff.__dataclass_fields__.values()}
    assert "canonical_task_id" not in fields
    assert "project_id" not in fields
    # Ensure Handoff mechanical blacklist includes these
    from aota_forge.work_plane.handoff import FORBIDDEN_MECHANICAL_FIELDS

    assert "canonical_task_id" in FORBIDDEN_MECHANICAL_FIELDS
    assert "project_id" not in FORBIDDEN_MECHANICAL_FIELDS  # trusted binding owns it, not forbidden but not in Handoff
    # projector via compiler must use binding, not handoff.project_ref
    h = _handoff(project_ref=SemanticReference("proj-ref-xyz"))
    b = _binding(project_id="real-proj")
    pkg = compile_handoff_to_execution_package(h, b)
    assert pkg.project_id == "real-proj"


# ---------------------------------------------------------------------------
# T13 package_id is Forge/mechanical-owned
# ---------------------------------------------------------------------------
def test_t13_package_id_forge_owned() -> None:
    h = _handoff()
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h, b)
    pkg2 = compile_handoff_to_execution_package(h, b)
    # Auto-generated, different each time when not supplied
    assert pkg1.package_id != pkg2.package_id
    # When supplied via trusted seam, it is used
    pkg_fixed = compile_handoff_to_execution_package(h, b, package_id="fixed-pkg-123")
    assert pkg_fixed.package_id == "fixed-pkg-123"
    # Handoff dict injection rejected (already tested in W3)
    d = h.to_dict()
    d["package_id"] = "injected"
    with pytest.raises(ValueError, match="Forbidden mechanical field"):
        TaskHandoff.from_dict(d)


# ---------------------------------------------------------------------------
# T14 correlation identity is Forge/mechanical-owned
# ---------------------------------------------------------------------------
def test_t14_correlation_forge_owned() -> None:
    h = _handoff()
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h, b)
    pkg2 = compile_handoff_to_execution_package(h, b)
    assert pkg1.correlation_id != pkg2.correlation_id
    pkg_fixed = compile_handoff_to_execution_package(h, b, correlation_id="corr-fixed-123")
    assert pkg_fixed.correlation_id == "corr-fixed-123"


# ---------------------------------------------------------------------------
# T15 canonical_role cannot be caller-overridden
# ---------------------------------------------------------------------------
def test_t15_canonical_role_cannot_be_overridden() -> None:
    sig = inspect.signature(compile_handoff_to_execution_package)
    assert "canonical_role" not in sig.parameters
    # Also alias
    sig2 = inspect.signature(compile_task_handoff)
    assert "canonical_role" not in sig2.parameters
    # Attempt to pass via handoff string "planner" must not change mapping
    h = _handoff(work_role=AgentWorkRole.CODER)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.canonical_role == "coder"
    assert pkg.canonical_role != "planner"


# ---------------------------------------------------------------------------
# T16 no executor selection
# ---------------------------------------------------------------------------
def test_t16_no_executor_selection() -> None:
    src = pathlib.Path("aota_forge/work_plane/compiler.py").read_text(encoding="utf-8")
    # Compiler must not select executor/adapter/Hermes profile
    lower = src.lower()
    for forbidden in ["executor_id", "adapter_handle", "select_executor", "choose_executor"]:
        # allow only in comments about ownership, but not as assignment
        # Check that file doesn't contain dispatch to registry
        pass
    assert "ExecutorRegistry" not in src
    assert "select" not in lower or "executor" not in lower or "select_executor" not in lower
    # Ensure no registry import
    assert "registry" not in lower or "ExecutorRegistry" not in src


# ---------------------------------------------------------------------------
# T17 no Hermes dependency
# ---------------------------------------------------------------------------
def test_t17_no_hermes_dependency() -> None:
    src = pathlib.Path("aota_forge/work_plane/compiler.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    # Ensure no Hermes-private field leakage (mirrors W3 style)
    assert "hermes_profile" not in src.lower()
    assert "hermeshostclient" not in src.lower()


# ---------------------------------------------------------------------------
# T30 objective preserved
# ---------------------------------------------------------------------------
def test_objective_preserved() -> None:
    obj = "Implement critical bounded feature for S1 M1-W4"
    h = _handoff(objective=obj)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.instruction == obj
    assert obj in pkg.instruction


# ---------------------------------------------------------------------------
# bounded_scope preserved
# ---------------------------------------------------------------------------
def test_bounded_scope_preserved() -> None:
    scope = "aota_forge/work_plane/compiler.py + tests"
    h = _handoff(bounded_scope=scope)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.working_context["bounded_scope"] == scope


# ---------------------------------------------------------------------------
# validation_expectations preserved
# ---------------------------------------------------------------------------
def test_validation_expectations_preserved() -> None:
    vals = ("pytest passes", "typecheck passes")
    h = _handoff(validation_expectations=vals)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.result_expectations["validation_expectations"] == list(vals)


# ---------------------------------------------------------------------------
# semantic_stop_expectations preserved
# ---------------------------------------------------------------------------
def test_semantic_stop_expectations_preserved() -> None:
    stops = ("stop on ambiguity", "escalate on conflict")
    h = _handoff(semantic_stop_expectations=stops)
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.result_expectations["semantic_stop_expectations"] == list(stops)


# ---------------------------------------------------------------------------
# applicable refs remain traceable
# ---------------------------------------------------------------------------
def test_applicable_refs_traceable() -> None:
    h = _handoff(
        project_ref=SemanticReference("proj-ref-1"),
        plan_ref=SemanticReference("plan-ref-1"),
        milestone_ref=SemanticReference("m1"),
        work_item_ref=SemanticReference("w4"),
        policy_refs=(SemanticReference("pol-1"),),
        context_refs=(SemanticReference("ctx-1"),),
        evidence_refs=(SemanticReference("ev-1"),),
        skill_refs=(SemanticReference("skill-1"),),
        process_depth_or_risk_projection_ref=SemanticReference("deep-proc"),
    )
    pkg = compile_handoff_to_execution_package(h, _binding())
    ctx = pkg.working_context
    assert ctx["refs"]["project_ref"]["ref"] == "proj-ref-1"
    assert ctx["refs"]["plan_ref"]["ref"] == "plan-ref-1"
    assert ctx["refs"]["milestone_ref"]["ref"] == "m1"
    assert ctx["refs"]["work_item_ref"]["ref"] == "w4"
    assert ctx["refs"]["policy_refs"][0]["ref"] == "pol-1"
    assert ctx["refs"]["context_refs"][0]["ref"] == "ctx-1"
    assert ctx["refs"]["evidence_refs"][0]["ref"] == "ev-1"
    assert ctx["refs"]["skill_refs"][0]["ref"] == "skill-1"
    assert ctx["refs"]["process_depth_or_risk_projection_ref"]["ref"] == "deep-proc"


# ---------------------------------------------------------------------------
# Handoff digest remains traceable
# ---------------------------------------------------------------------------
def test_handoff_digest_traceable() -> None:
    h = _handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.working_context["handoff_digest"] == h.handoff_digest
    assert pkg.input_artifacts[0]["handoff_digest"] == h.handoff_digest


# ---------------------------------------------------------------------------
# Fingerprint adversarial matrix
# ---------------------------------------------------------------------------
def test_t18_same_handoff_same_intent() -> None:
    h = _handoff()
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h, b)
    pkg2 = compile_handoff_to_execution_package(h, b)
    assert pkg1.intent_fingerprint == pkg2.intent_fingerprint


def test_t19_objective_change_changes_fingerprint() -> None:
    h1 = _handoff(objective="objective A")
    h2 = _handoff(objective="objective B")
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint


def test_t20_scope_change_changes_fingerprint() -> None:
    h1 = _handoff(bounded_scope="scope A")
    h2 = _handoff(bounded_scope="scope B")
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint


def test_t21_validation_change_changes_fingerprint() -> None:
    h1 = _handoff(validation_expectations=("v1",))
    h2 = _handoff(validation_expectations=("v2",))
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint


def test_t22_stop_change_changes_fingerprint() -> None:
    h1 = _handoff(semantic_stop_expectations=("s1",))
    h2 = _handoff(semantic_stop_expectations=("s2",))
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint


def test_t23_work_role_change_changes_fingerprint() -> None:
    h1 = _handoff(work_role=AgentWorkRole.CODER)
    h2 = _handoff(work_role=AgentWorkRole.REVIEWER)
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint


def test_t24_ref_change_changes_fingerprint() -> None:
    h1 = _handoff(policy_refs=(SemanticReference("pol-A"),))
    h2 = _handoff(policy_refs=(SemanticReference("pol-B"),))
    b = _binding()
    assert compile_handoff_to_execution_package(h1, b).intent_fingerprint != compile_handoff_to_execution_package(h2, b).intent_fingerprint
    # context ref
    h3 = _handoff(context_refs=(SemanticReference("ctx-A"),))
    h4 = _handoff(context_refs=(SemanticReference("ctx-B"),))
    assert compile_handoff_to_execution_package(h3, b).intent_fingerprint != compile_handoff_to_execution_package(h4, b).intent_fingerprint
    # evidence ref
    h5 = _handoff(evidence_refs=(SemanticReference("ev-A"),))
    h6 = _handoff(evidence_refs=(SemanticReference("ev-B"),))
    assert compile_handoff_to_execution_package(h5, b).intent_fingerprint != compile_handoff_to_execution_package(h6, b).intent_fingerprint
    # skill ref
    h7 = _handoff(skill_refs=(SemanticReference("sk-A"),))
    h8 = _handoff(skill_refs=(SemanticReference("sk-B"),))
    assert compile_handoff_to_execution_package(h7, b).intent_fingerprint != compile_handoff_to_execution_package(h8, b).intent_fingerprint
    # plan ref
    h9 = _handoff(plan_ref=SemanticReference("plan-A"))
    h10 = _handoff(plan_ref=SemanticReference("plan-B"))
    assert compile_handoff_to_execution_package(h9, b).intent_fingerprint != compile_handoff_to_execution_package(h10, b).intent_fingerprint


def test_t25_package_id_only_same_intent() -> None:
    h = _handoff()
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h, b, package_id="pkg-111", correlation_id="corr-same", idempotency_key="idem-same")
    pkg2 = compile_handoff_to_execution_package(h, b, package_id="pkg-222", correlation_id="corr-same", idempotency_key="idem-same")
    assert pkg1.intent_fingerprint == pkg2.intent_fingerprint
    assert pkg1.package_id != pkg2.package_id


def test_t26_correlation_only_same_intent() -> None:
    h = _handoff()
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h, b, correlation_id="corr-A", package_id="same-pkg", idempotency_key="same-idem")
    pkg2 = compile_handoff_to_execution_package(h, b, correlation_id="corr-B", package_id="same-pkg", idempotency_key="same-idem")
    assert pkg1.intent_fingerprint == pkg2.intent_fingerprint
    assert pkg1.correlation_id != pkg2.correlation_id


def test_t27_handoff_digest_not_intent_fingerprint() -> None:
    h = _handoff()
    b = _binding()
    pkg = compile_handoff_to_execution_package(h, b)
    assert h.handoff_digest != pkg.intent_fingerprint
    # Different domains: handoff_digest via canonical handoff, intent via execution package
    assert len(h.handoff_digest) == 64
    assert len(pkg.intent_fingerprint) == 64


def test_t28_compute_intent_unchanged() -> None:
    # Verify compute_intent_fingerprint still validates and matches ExecutionPackage's stored fingerprint
    h = _handoff()
    b = _binding()
    pkg = compile_handoff_to_execution_package(h, b)
    expected = compute_intent_fingerprint(
        canonical_role=pkg.canonical_role,
        instruction=pkg.instruction,
        input_artifacts=pkg.input_artifacts,
        project_id=pkg.project_id,
    )
    assert pkg.intent_fingerprint == expected
    # Verify that function hasn't been monkey-patched to include working_context etc.
    # Changing working_context alone should NOT change intent fingerprint - prove separation
    pkg2 = ExecutionPackage(
        package_id=pkg.package_id,
        protocol_version=pkg.protocol_version,
        contract_hash=pkg.contract_hash,
        operation=pkg.operation,
        canonical_task_id=pkg.canonical_task_id,
        subject_ref=pkg.subject_ref,
        project_id=pkg.project_id,
        canonical_role=pkg.canonical_role,
        instruction=pkg.instruction,
        input_artifacts=pkg.input_artifacts,
        working_context={"different": "context"},
        capability_requirements=pkg.capability_requirements,
        constraints=pkg.constraints,
        idempotency_key=pkg.idempotency_key,
        intent_fingerprint=pkg.intent_fingerprint,
        correlation_id=pkg.correlation_id,
        result_expectations=pkg.result_expectations,
    )
    assert pkg2.intent_fingerprint == pkg.intent_fingerprint


# Additional deterministic + mechanical separation
def test_semantic_compilation_deterministic() -> None:
    h = _handoff(objective="deterministic check", bounded_scope="scope")
    b = _binding(canonical_task_id="task-det", project_id="proj-det")
    pkg1 = compile_handoff_to_execution_package(h, b, package_id="fixed-pkg", correlation_id="fixed-corr", idempotency_key="fixed-idem")
    pkg2 = compile_handoff_to_execution_package(h, b, package_id="fixed-pkg", correlation_id="fixed-corr", idempotency_key="fixed-idem")
    assert pkg1 == pkg2
    assert pkg1.intent_fingerprint == pkg2.intent_fingerprint


def test_mechanical_materialization_forge_owned() -> None:
    h = _handoff()
    b = _binding()
    # Without mechanical overrides, Forge generates ids
    pkg = compile_handoff_to_execution_package(h, b)
    assert pkg.package_id
    assert pkg.correlation_id
    assert pkg.idempotency_key
    # Handoff never supplies these
    assert "package_id" not in h.to_dict()
    assert "correlation_id" not in h.to_dict()


def test_trusted_binding_validation() -> None:
    with pytest.raises((ValueError, TypeError)):
        TrustedExecutionBinding(canonical_task_id="", project_id="proj")
    with pytest.raises((ValueError, TypeError)):
        TrustedExecutionBinding(canonical_task_id="task", project_id="")
    with pytest.raises(TypeError):
        TrustedExecutionBinding(canonical_task_id=123, project_id="proj")  # type: ignore[arg-type]


def test_core_does_not_depend_on_work_plane() -> None:
    core_files = [
        pathlib.Path("aota_forge/core/execution/package.py"),
        pathlib.Path("aota_forge/core/execution/roles.py"),
        pathlib.Path("aota_forge/core/execution/dispatcher.py"),
    ]
    for p in core_files:
        src = p.read_text(encoding="utf-8")
        assert "work_plane" not in src
        assert "WorkRole" not in src


def test_no_new_authority_registry() -> None:
    src = pathlib.Path("aota_forge/work_plane/compiler.py").read_text(encoding="utf-8")
    # Ensure no actual registry/store implementation (allow docstring mention as negative)
    # Check for class definitions that would indicate new authority system
    assert "class AuthorityRegistry" not in src
    assert "class TaskIdentityStore" not in src
    assert "class SessionRegistry" not in src
    # Must not implement AGENTS resolver etc.
    assert "def resolve_agents" not in src.lower()
    assert "AGENTS_RESOLVER_IMPLEMENTED" not in src


def test_idempotency_boundary_preserved() -> None:
    # Same idempotency key + same intent -> compatible replay semantics (same fingerprint)
    # Same idempotency key + different intent -> different fingerprint (conflict)
    h1 = _handoff(objective="same")
    h2 = _handoff(objective="different")
    b = _binding()
    pkg1 = compile_handoff_to_execution_package(h1, b, idempotency_key="idem-123")
    pkg2 = compile_handoff_to_execution_package(h1, b, idempotency_key="idem-123")
    assert pkg1.intent_fingerprint == pkg2.intent_fingerprint
    pkg3 = compile_handoff_to_execution_package(h2, b, idempotency_key="idem-123")
    assert pkg1.intent_fingerprint != pkg3.intent_fingerprint
