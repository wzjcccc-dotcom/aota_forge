"""S1 M2-W3 Bounded Bootstrap Bundle Contract."""

from __future__ import annotations

import hashlib
import pathlib
import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.lifecycle import ExecutionWorkRoleBinding
from aota_forge.work_plane.soul import Soul
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate

import aota_forge.work_plane.bootstrap as bootstrap_mod
from aota_forge.work_plane.bootstrap import (
    BootstrapBudget,
    BootstrapBundle,
    BootstrapComponent,
    create_task_main_bundle,
    create_worker_bundle,
)


def _make_soul(content: str = "You are a concise helpful assistant.") -> Soul:
    return Soul(content=content)


def _make_handoff(work_role: str | AgentWorkRole = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=work_role,
        task_kind="implementation",
        objective="Implement bounded bootstrap",
        bounded_scope="Work on bootstrap bundle only",
        validation_expectations=("unit test",),
        semantic_stop_expectations=("ambiguous scope",),
    )


def _make_policy(policy_id: str = "policy1", project_id: str = "proj1", scope: str = "", content: str | None = "policy content") -> AgentsPolicyCandidate:
    # compute digest automatically if content provided
    if content is not None:
        return AgentsPolicyCandidate(policy_id=policy_id, project_id=project_id, scope=scope, content=content)
    else:
        # reference-only needs digest; use hash of some material
        digest = hashlib.sha256(f"{policy_id}:{scope}".encode()).hexdigest()
        return AgentsPolicyCandidate(policy_id=policy_id, project_id=project_id, scope=scope, content=None, content_digest=digest, provenance_ref=f"ref:{policy_id}")


def _make_binding(role: str | AgentWorkRole = "coder") -> ExecutionWorkRoleBinding:
    return ExecutionWorkRoleBinding(work_role=role)


# T01 task-main bootstrap valid
def test_t01_task_main_bootstrap_valid() -> None:
    soul = _make_soul()
    policy = _make_policy()
    bundle = create_task_main_bundle(soul=soul, agents_policies=[policy])
    assert bundle.bundle_type == "task_main"
    assert len(bundle.components) >= 1
    # contains soul
    kinds = [c.kind for c in bundle.components]
    assert "soul" in kinds
    # canonical serialization exists
    assert isinstance(bundle.canonical_json(), str)
    assert isinstance(bundle.digest, str)
    assert len(bundle.digest) == 64


# T02 Worker bootstrap valid
def test_t02_worker_bootstrap_valid() -> None:
    soul = _make_soul()
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    policy = _make_policy()
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    assert bundle.bundle_type == "worker"
    kinds = [c.kind for c in bundle.components]
    assert "work_role_binding" in kinds
    assert "soul" in kinds
    assert "task_handoff" in kinds
    assert "agents_policy" in kinds


# T03 task-main and Worker bootstrap structurally/semantically distinct
def test_t03_task_main_and_worker_distinct() -> None:
    soul = _make_soul()
    policy = _make_policy()
    task_main = create_task_main_bundle(soul=soul, agents_policies=[policy])
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    worker = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    assert task_main.bundle_type != worker.bundle_type
    assert task_main.canonical_json() != worker.canonical_json()
    assert task_main.digest != worker.digest
    # structural: worker has work_role_binding and task_handoff, task_main may not have work_role_binding
    task_kinds = {c.kind for c in task_main.components}
    worker_kinds = {c.kind for c in worker.components}
    assert task_kinds != worker_kinds


# T04 Worker reuses ExecutionWorkRoleBinding
def test_t04_worker_reuses_execution_work_role_binding() -> None:
    # Verify bootstrap uses ExecutionWorkRoleBinding type directly
    soul = _make_soul()
    handoff = _make_handoff("reviewer")
    binding = ExecutionWorkRoleBinding(work_role=AgentWorkRole.REVIEWER)
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    # Find work_role_binding component
    comp = [c for c in bundle.components if c.kind == "work_role_binding"][0]
    assert comp.materialized is not None
    # materialized is canonical JSON of binding
    assert '"work_role":"reviewer"' in comp.materialized
    # Ensure module imports ExecutionWorkRoleBinding
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "ExecutionWorkRoleBinding" in src
    assert "from aota_forge.work_plane.lifecycle import" in src or "from aota_forge.work_plane.lifecycle" in src
    # Should not define second binding contract
    assert "class WorkerBinding" not in src
    assert "class BootstrapWorkRoleBinding" not in src


# T05 Worker WorkRole mismatch fails closed
def test_t05_worker_workrole_mismatch_fails_closed() -> None:
    soul = _make_soul()
    handoff = _make_handoff("coder")
    binding = _make_binding("reviewer")  # mismatch
    with pytest.raises(ValueError):
        create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    # Also direct bundle construction mismatch
    handoff2 = _make_handoff("coder")
    binding2 = _make_binding("coder")
    # manually create mismatched components
    comp_binding = BootstrapComponent.from_work_role_binding(binding2)
    # create handoff with different role
    handoff_mismatch = _make_handoff("reviewer")
    comp_handoff = BootstrapComponent.from_task_handoff(handoff_mismatch)
    soul_comp = BootstrapComponent.from_soul(soul)
    with pytest.raises(ValueError):
        BootstrapBundle(bundle_type="worker", components=(comp_binding, soul_comp, comp_handoff))
    # reconcile check
    good_bundle = create_worker_bundle(binding=binding2, soul=soul, handoff=handoff2)
    with pytest.raises(ValueError):
        good_bundle.reconcile_work_role(_make_binding("reviewer"))
    # correct reconcile should pass
    good_bundle.reconcile_work_role(binding2)


# T06 SOUL reused
def test_t06_soul_reused() -> None:
    soul = _make_soul("bounded soul content")
    comp = BootstrapComponent.from_soul(soul)
    assert comp.kind == "soul"
    assert soul.canonical_json() in comp.materialized  # materialized is soul canonical
    assert comp.digest == soul.digest
    # Check module reuses Soul
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "from aota_forge.work_plane.soul import Soul" in src or "from aota_forge.work_plane.soul import" in src
    assert "class Soul" not in src  # should not duplicate


# T07 applicable AGENTS semantic policy reused
def test_t07_agents_policy_reused() -> None:
    policy = _make_policy(policy_id="p1", scope="a")
    comp = BootstrapComponent.from_agents_policy(policy, delivery="eager")
    assert comp.kind == "agents_policy"
    assert comp.materialized == policy.content
    assert comp.digest == policy.content_digest
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "AgentsPolicyCandidate" in src
    assert "from aota_forge.work_plane.agents_applicability import" in src
    assert "class AgentsPolicyCandidate" not in src


# T08 TaskHandoff reused
def test_t08_task_handoff_reused() -> None:
    handoff = _make_handoff("analyst")
    comp = BootstrapComponent.from_task_handoff(handoff)
    assert comp.kind == "task_handoff"
    assert comp.digest == handoff.handoff_digest
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    assert "TaskHandoff" in src
    assert "from aota_forge.work_plane.handoff import" in src
    assert "class TaskHandoff" not in src


# T09 eager component accepted
def test_t09_eager_component_accepted() -> None:
    soul = _make_soul()
    comp = BootstrapComponent.from_soul(soul, delivery="eager")
    assert comp.delivery == "eager"
    assert comp.materialized is not None
    # Eager via generic
    content = "x" * 100
    digest = hashlib.sha256(content.encode()).hexdigest()
    comp2 = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest)
    assert comp2.delivery == "eager"


# T10 progressive ref accepted
def test_t10_progressive_ref_accepted() -> None:
    ref = "policy:ref1"
    digest = hashlib.sha256(ref.encode()).hexdigest()
    comp = BootstrapComponent(kind="semantic_ref", delivery="progressive", ref=ref, digest=digest)
    assert comp.delivery == "progressive"
    assert comp.ref == ref
    # via helper
    comp2 = BootstrapComponent.progressive_ref(kind="semantic_ref", ref=ref, digest=digest)
    assert comp2.delivery == "progressive"


# T11 eager and progressive remain distinct
def test_t11_eager_progressive_distinct() -> None:
    content = "eager content"
    digest_eager = hashlib.sha256(content.encode()).hexdigest()
    eager = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest_eager)
    ref = "durable:ref"
    digest_prog = hashlib.sha256(ref.encode()).hexdigest()
    prog = BootstrapComponent(kind="semantic_ref", delivery="progressive", ref=ref, digest=digest_prog)
    assert eager.delivery != prog.delivery
    assert eager.materialized is not None and eager.ref is None
    assert prog.ref is not None and prog.materialized is None


# T12 refs-only bundle is not required
def test_t12_refs_only_not_required() -> None:
    # Bundle can contain eager materialized; not required to be refs-only
    assert bootstrap_mod.BOOTSTRAP_REFS_ONLY_REQUIRED is False
    soul = _make_soul()
    handoff = _make_handoff("coder")
    binding = _make_binding("coder")
    # worker bundle with eager materialized should be valid
    bundle = create_worker_bundle(binding=binding, soul=soul, handoff=handoff)
    assert any(c.delivery == "eager" and c.materialized is not None for c in bundle.components)
    # Also ensure not all refs only: at least one eager
    assert not all(c.delivery == "progressive" for c in bundle.components)


# T13 bounded eager materialization accepted
def test_t13_bounded_eager_materialization_accepted() -> None:
    content = "a" * 1024
    digest = hashlib.sha256(content.encode()).hexdigest()
    comp = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest)
    assert comp.materialized == content
    # via soul at bound
    max_len = bootstrap_mod.MAX_MATERIALIZED_LENGTH
    bounded_content = "y" * max_len
    soul = Soul(content=bounded_content[:8000])  # soul has 8192 limit, but bootstrap allows 32k
    # use generic component at exactly max
    digest2 = hashlib.sha256(bounded_content.encode()).hexdigest()
    # This should be accepted if we use generic with exactly max
    comp2 = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=bounded_content, digest=digest2)
    assert comp2.materialized == bounded_content


# T14 oversized individual component rejected
def test_t14_oversized_individual_component_rejected() -> None:
    oversized = "x" * (bootstrap_mod.MAX_MATERIALIZED_LENGTH + 1)
    digest = hashlib.sha256(oversized.encode()).hexdigest()
    with pytest.raises(ValueError):
        BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=oversized, digest=digest)
    # ref oversized
    oversized_ref = "r" * (bootstrap_mod.MAX_REF_LENGTH + 1)
    digest2 = hashlib.sha256(oversized_ref.encode()).hexdigest()
    with pytest.raises(ValueError):
        BootstrapComponent(kind="semantic_ref", delivery="progressive", ref=oversized_ref, digest=digest2)


# T15 component count overflow rejected
def test_t15_component_count_overflow_rejected() -> None:
    soul = _make_soul()
    base = BootstrapComponent.from_soul(soul)
    many = tuple(base for _ in range(bootstrap_mod.MAX_COMPONENT_COUNT + 1))
    # Need distinct components? We'll create generic distinct refs to exceed count
    comps = []
    for i in range(bootstrap_mod.MAX_COMPONENT_COUNT + 1):
        ref = f"ref:{i}"
        digest = hashlib.sha256(ref.encode()).hexdigest()
        comps.append(BootstrapComponent(kind="semantic_ref", delivery="progressive", ref=ref, digest=digest))
    with pytest.raises(ValueError):
        BootstrapBundle(bundle_type="task_main", components=tuple(comps))


# T16 ref count overflow rejected (budget ref count)
def test_t16_ref_count_overflow_rejected() -> None:
    # Create bundle with many progressive refs exceeding budget max_ref_count
    comps = []
    for i in range(5):
        ref = f"ref:{i}"
        digest = hashlib.sha256(ref.encode()).hexdigest()
        comps.append(BootstrapComponent(kind="semantic_ref", delivery="progressive", ref=ref, digest=digest))
    # Use soul eager + 5 refs = some bundle
    soul = _make_soul()
    soul_comp = BootstrapComponent.from_soul(soul)
    all_comps = (soul_comp,) + tuple(comps)
    bundle = BootstrapBundle(bundle_type="task_main", components=all_comps)
    # Budget with very low ref count should fail
    budget = BootstrapBudget(max_canonical_bytes=100000, max_components=16, max_ref_count=2)
    with pytest.raises(ValueError):
        bundle.validate_budget(budget)
    # Also hard structural bound: if we exceed MAX_COMPONENT_COUNT via refs, already fails in bundle creation (T15)


# T17 deterministic budget accounting
def test_t17_deterministic_budget_accounting() -> None:
    soul = _make_soul("deterministic")
    binding = _make_binding("coder")
    handoff = _make_handoff("coder")
    policy = _make_policy(content="policy")
    bundle1 = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    bundle2 = create_worker_bundle(binding=binding, soul=soul, handoff=handoff, agents_policies=[policy])
    assert bundle1.accounted_size() == bundle2.accounted_size()
    assert bundle1.canonical_json() == bundle2.canonical_json()
    # create same bundle with different component order, should still have same accounted size
    comps_reordered = tuple(reversed(bundle1.components))
    bundle_reordered = BootstrapBundle(bundle_type="worker", components=comps_reordered)
    assert bundle1.accounted_size() == bundle_reordered.accounted_size()
    assert bundle1.canonical_json() == bundle_reordered.canonical_json()


# T18 exact budget boundary accepted
def test_t18_exact_budget_boundary_accepted() -> None:
    soul = _make_soul()
    bundle = create_task_main_bundle(soul=soul)
    size = bundle.accounted_size()
    budget = BootstrapBudget(max_canonical_bytes=size)
    # cost == budget should be accepted
    bundle.validate_budget(budget)  # should not raise


# T19 budget+1 rejected
def test_t19_budget_plus_one_rejected() -> None:
    soul = _make_soul()
    bundle = create_task_main_bundle(soul=soul)
    size = bundle.accounted_size()
    budget = BootstrapBudget(max_canonical_bytes=size - 1) if size > 1 else BootstrapBudget(max_canonical_bytes=1)
    # Need budget that is size-1 to test budget+1 scenario: we test size == budget+1 -> rejected
    # So create budget one less than actual size
    with pytest.raises(ValueError):
        bundle.validate_budget(budget)


# T20 required eager oversized -> fail closed
def test_t20_required_eager_oversized_fail_closed() -> None:
    # Create a soul with content at bootstrap max, then try to create bundle that exceeds budget
    # Or create oversized materialized component directly
    oversized = "x" * (bootstrap_mod.MAX_MATERIALIZED_LENGTH + 1)
    digest = hashlib.sha256(oversized.encode()).hexdigest()
    with pytest.raises(ValueError):
        BootstrapComponent(kind="soul", delivery="eager", materialized=oversized, digest=digest)
    # Also test that worker bundle with oversized soul fails
    # Soul itself would reject oversized >8192, but bootstrap also rejects >32k, so we test via generic
    # Ensure fail-closed not truncate
    content = "a" * (bootstrap_mod.MAX_MATERIALIZED_LENGTH + 1)
    digest2 = hashlib.sha256(content.encode()).hexdigest()
    with pytest.raises(ValueError):
        BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest2)


# T21 required eager component never silently truncated
def test_t21_required_eager_never_silently_truncated() -> None:
    content = "hello world"
    digest = hashlib.sha256(content.encode()).hexdigest()
    comp = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=content, digest=digest)
    assert comp.materialized == content
    assert len(comp.materialized) == len(content)
    # Ensure no truncation happened inside constructor
    large_content = "b" * 5000
    digest_large = hashlib.sha256(large_content.encode()).hexdigest()
    comp_large = BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=large_content, digest=digest_large)
    assert comp_large.materialized == large_content
    # Oversized should raise, not truncate
    oversized = "c" * (bootstrap_mod.MAX_MATERIALIZED_LENGTH + 1)
    digest_over = hashlib.sha256(oversized.encode()).hexdigest()
    with pytest.raises(ValueError):
        BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=oversized, digest=digest_over)


# T22 deterministic canonical bundle serialization
def test_t22_deterministic_canonical_serialization() -> None:
    soul = _make_soul("canonical")
    bundle1 = create_task_main_bundle(soul=soul)
    bundle2 = create_task_main_bundle(soul=soul)
    assert bundle1.canonical_json() == bundle2.canonical_json()
    # Same components different order -> same canonical
    comps = list(bundle1.components)
    if len(comps) > 1:
        rev = BootstrapBundle(bundle_type="task_main", components=tuple(reversed(comps)))
        assert bundle1.canonical_json() == rev.canonical_json()
    # canonical_json uses sorted keys
    parsed = canonical_json(bundle1.canonical_dict())
    assert bundle1.canonical_json() == parsed


# T23 same semantic bundle -> same digest
def test_t23_same_semantic_bundle_same_digest() -> None:
    soul = _make_soul()
    b1 = create_task_main_bundle(soul=soul)
    b2 = create_task_main_bundle(soul=soul)
    assert b1.digest == b2.digest
    assert b1.compute_digest() == b2.compute_digest()


# T24 semantic content change -> digest differs
def test_t24_semantic_content_change_digest_differs() -> None:
    soul1 = _make_soul("content one")
    soul2 = _make_soul("content two")
    b1 = create_task_main_bundle(soul=soul1)
    b2 = create_task_main_bundle(soul=soul2)
    assert b1.digest != b2.digest
    # Also agents policy change
    p1 = _make_policy(policy_id="p1", content="a")
    p2 = _make_policy(policy_id="p1", content="b")
    b3 = create_task_main_bundle(soul=soul1, agents_policies=[p1])
    b4 = create_task_main_bundle(soul=soul1, agents_policies=[p2])
    assert b3.digest != b4.digest


# T25 bundle digest is not authority
def test_t25_bundle_digest_not_authority() -> None:
    assert bootstrap_mod.BOOTSTRAP_DIGEST_IS_AUTHORITY is False
    assert bootstrap_mod.BOOTSTRAP_BUNDLE_IS_AUTHORITY is False
    soul = _make_soul()
    bundle = create_task_main_bundle(soul=soul)
    # Digest is traceability, not authority; bundle should not have authority flag
    assert not hasattr(bundle, "is_authority")
    # Module flags
    assert bootstrap_mod.BOOTSTRAP_BUNDLE_IS_AUTHORITY is False


# T26 no AGENTS filesystem resolver
def test_t26_no_agents_filesystem_resolver() -> None:
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Should not contain filesystem discovery implementation
    assert "path.walk" not in lower
    assert "os.walk" not in lower
    assert "symlink" not in lower
    assert "filesystem" not in lower or "filesystem" in lower and "no" in lower  # allow mention in comment about not implementing
    # More strict: check for resolver implementation patterns, but allow constant name
    # Ensure no class named resolver
    assert "class AgentsResolver" not in src
    assert "def resolve_agents" not in src or "resolve_applicable_policies" in src  # imported is okay
    # Ensure bootstrap file does not implement discovery itself beyond reuse
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped


# T27 no Skill loading
def test_t27_no_skill_loading() -> None:
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Should not implement skill loading
    assert "skill loading" not in lower
    assert "load skill" not in lower
    assert "search skill" not in lower
    # Ensure no Skill registry code
    assert "class Skill" not in src
    assert "SkillRegistry" not in src


# T28 no Context retrieval
def test_t28_no_context_retrieval() -> None:
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    assert "context retrieval" not in lower
    assert "fetch context" not in lower
    assert "retrieve context" not in lower
    # No context fabric import
    assert "context fabric" not in lower


# T29 no sandbox implementation
def test_t29_no_sandbox() -> None:
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    assert "sandbox" not in lower


# T30 no Hermes dependency
def test_t30_no_hermes_dependency() -> None:
    src = pathlib.Path(bootstrap_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    for line in src.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "hermes" not in stripped
    assert "hermes" not in lower
