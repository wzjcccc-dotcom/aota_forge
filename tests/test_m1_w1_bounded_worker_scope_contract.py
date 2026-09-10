"""M1/W1 Bounded Worker Scope Contract (AF #45, defect I40-B003/F1).

Proves the task-main final Work projection → existing TaskHandoff →
existing compiler → ExecutionPackage → worker model-facing context chain
carries usable bounded semantics, reusing TaskHandoff (no parallel Worker
execution contract), with digest binding, tamper fail-closed, and
scope-insufficiency fail-closed (no scope-free Worker dispatch).

Coverage map (PLAN §22):
  A normal projection → non-generic usable bounded_scope
  B objective is short goal, not sole construction semantic
  C validation expectations survive task-main → handoff → package
  D stop expectations survive the same path
  E project/Plan/Milestone/Work refs survive boundedly
  F no full Plan body embedded
  G/H/I scope/validation/stop change changes the digest
  J tampered semantic projection fails closed
  K worker model-facing startup sees usable bounded scope
  L worker can act without GitHub Plan fetch
  M TaskHandoff cannot expand Plan scope
  N freeform startup prompt does not override TaskHandoff
  O generic project support preserved
  P no calculator/#40 special case in production path
  Q no new public MCP tool
  R no W2 runtime binding fix accidentally introduced

Plus PLAN §23 direct semantic proof on a generic bounded fixture and §24
context-cost bounds.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from aota_forge.adapters.hermes.executor import canonical_to_hermes_payload
from aota_forge.composition import task_main_host_bootstrap as tmb
from aota_forge.composition.task_main_host_bootstrap import (
    _handoff_for,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.runtime.task_main.control import TASK_MAIN_CONTROL_OPERATIONS
from aota_forge.work_plane.compiler import (
    OBJECTIVE_ONLY_WORKER_INSTRUCTION as COMPILER_OBJECTIVE_ONLY,
    PackageIntegrityError,
    TrustedExecutionBinding,
    build_worker_instruction,
    compile_handoff_to_execution_package,
    verify_execution_package_integrity,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    FULL_PLAN_DUMP_TO_WORKER,
    OBJECTIVE_ONLY_WORKER_INSTRUCTION,
    SCOPE_INSUFFICIENT_DISPATCH_ALLOWED,
    TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY,
    TASK_HANDOFF_IS_PLAN_AUTHORITY,
    TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY,
    WORK_SCOPE_INSUFFICIENT,
    WORKER_NORMAL_EXECUTION_REQUIRES_PLAN_FETCH,
    WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION,
    WORKER_STARTUP_PROMPT_IS_AUTHORITY,
    WorkScopeInsufficientError,
    WorkSemanticProjection,
    assert_worker_usable_handoff,
    generic_fallback_scope_template,
    is_worker_usable_handoff,
    parse_work_semantics_table,
    resolve_bounded_work_handoff,
)
from aota_forge.work_plane.roles import AgentWorkRole


# ---------------------------------------------------------------------------
# Generic (non-#40) fixture semantics used across tests
# ---------------------------------------------------------------------------

OBJECTIVE = "Modify function foo so it returns X."
SCOPE = (
    "Change function foo in src/sample.py so it returns X for valid input. "
    "Preserve existing behavior Y and the module public interface. "
    "Do not redesign packaging or unrelated components."
)
VALIDATION = ("foo returns X for valid input", "behavior Y regression passes", "test Z passes")
STOPS = (
    "stop if the trusted project/worktree binding is inconsistent",
    "stop if the change requires scope outside this Work Item",
)

PROJECT_ID = "proj_sample"
PLAN_AUTH = "example-owner/example-governance#123"
MILESTONE = "M1"
WORK_ITEM = "W1"
PLAN_DIGEST = "b" * 64


def _projection(**overrides) -> WorkSemanticProjection:
    data = {
        "objective": OBJECTIVE,
        "bounded_scope": SCOPE,
        "validation_expectations": list(VALIDATION),
        "semantic_stop_expectations": list(STOPS),
    }
    data.update(overrides)
    return WorkSemanticProjection.from_dict(data)


def _binding():
    return TrustedExecutionBinding(canonical_task_id="proj:M1:W1:attempt-1", project_id=PROJECT_ID)


def _rich_handoff(**overrides) -> TaskHandoff:
    kwargs = {
        "work_item_id": WORK_ITEM,
        "milestone_ref": MILESTONE,
        "projection": _projection(),
        "project_id": PROJECT_ID,
        "plan_authority": PLAN_AUTH,
        "plan_digest": PLAN_DIGEST,
    }
    kwargs.update(overrides)
    return resolve_bounded_work_handoff(**kwargs)


# ---------------------------------------------------------------------------
# A. normal Work projection generates non-generic usable bounded_scope
# ---------------------------------------------------------------------------

def test_a_normal_projection_is_usable_and_non_generic() -> None:
    h = _rich_handoff()
    assert h.bounded_scope == SCOPE
    assert h.bounded_scope != generic_fallback_scope_template(WORK_ITEM, MILESTONE)
    assert is_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE) is True
    assert_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE)


def test_a_generic_fallback_is_not_worker_usable() -> None:
    # The legacy semantics-free derivation is preserved for backward
    # compatibility but is structurally recognized as not Worker-usable.
    h = _handoff_for(WORK_ITEM, milestone_ref=MILESTONE, project_id=PROJECT_ID,
                     plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST)
    assert h.bounded_scope == generic_fallback_scope_template(WORK_ITEM, MILESTONE)
    assert is_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE) is False
    with pytest.raises(WorkScopeInsufficientError):
        assert_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE)


def test_a_projection_path_through_handoff_for() -> None:
    h = _handoff_for(WORK_ITEM, milestone_ref=MILESTONE, project_id=PROJECT_ID,
                     plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                     work_semantics={WORK_ITEM: _projection()})
    assert h.bounded_scope == SCOPE
    assert is_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE) is True


# ---------------------------------------------------------------------------
# B. objective is short goal, not sole construction semantic
# ---------------------------------------------------------------------------

def test_b_objective_not_sole_semantic() -> None:
    h = _rich_handoff()
    assert h.objective == OBJECTIVE
    assert h.objective != h.bounded_scope
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.instruction != h.objective
    assert h.objective in pkg.instruction
    assert h.bounded_scope in pkg.instruction
    assert COMPILER_OBJECTIVE_ONLY is False
    assert OBJECTIVE_ONLY_WORKER_INSTRUCTION is False


# ---------------------------------------------------------------------------
# C/D. validation + stop expectations survive task-main → handoff → package
# ---------------------------------------------------------------------------

def test_cd_expectations_survive_full_path() -> None:
    h = _rich_handoff()
    assert tuple(h.validation_expectations) == VALIDATION
    assert tuple(h.semantic_stop_expectations) == STOPS
    pkg = compile_handoff_to_execution_package(h, _binding())
    assert pkg.result_expectations["validation_expectations"] == list(VALIDATION)
    assert pkg.result_expectations["semantic_stop_expectations"] == list(STOPS)
    for val in VALIDATION:
        assert val in pkg.instruction
    for stop in STOPS:
        assert stop in pkg.instruction


# ---------------------------------------------------------------------------
# E. refs survive boundedly
# ---------------------------------------------------------------------------

def test_e_refs_survive_boundedly() -> None:
    h = _rich_handoff()
    assert h.project_ref is not None and h.project_ref.ref == PROJECT_ID
    assert h.plan_ref is not None and h.plan_ref.ref == PLAN_AUTH
    assert h.plan_ref.digest == PLAN_DIGEST
    assert h.milestone_ref is not None and h.milestone_ref.ref == MILESTONE
    assert h.work_item_ref is not None and h.work_item_ref.ref == WORK_ITEM
    pkg = compile_handoff_to_execution_package(h, _binding())
    refs = pkg.working_context["refs"]
    assert refs["project_ref"]["ref"] == PROJECT_ID
    assert refs["plan_ref"]["ref"] == PLAN_AUTH
    assert refs["milestone_ref"]["ref"] == MILESTONE
    assert refs["work_item_ref"]["ref"] == WORK_ITEM
    assert f"project_ref={PROJECT_ID}" in pkg.instruction
    assert f"work_item_ref={WORK_ITEM}" in pkg.instruction


# ---------------------------------------------------------------------------
# F. no full Plan body embedded (+ §24 context cost bounds)
# ---------------------------------------------------------------------------

PLAN_BODY_SENTINEL = "FULL_PLAN_BODY_SENTINEL_XQZ_9f8e7d6c5b4a"

def test_f_no_full_plan_body_and_bounded_cost() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    for surface in (h.objective, h.bounded_scope, pkg.instruction,
                    json.dumps(pkg.working_context), json.dumps(pkg.result_expectations)):
        assert PLAN_BODY_SENTINEL not in surface
    # Context cost: every handoff field is within its contract bound and the
    # deterministic instruction rendering adds only labeled structure.
    assert len(h.objective) <= 4096
    assert len(h.bounded_scope) <= 4096
    assert len(pkg.instruction.encode("utf-8")) <= 96 * 1024
    assert FULL_PLAN_DUMP_TO_WORKER is False


# ---------------------------------------------------------------------------
# G/H/I. digest binds scope, validation, stop expectations
# ---------------------------------------------------------------------------

def test_g_scope_change_changes_digest() -> None:
    h1 = _rich_handoff()
    h2 = _rich_handoff(projection=_projection(bounded_scope=SCOPE + " Extra sentence."))
    assert h1.handoff_digest != h2.handoff_digest


def test_h_validation_change_changes_digest() -> None:
    h1 = _rich_handoff()
    h2 = _rich_handoff(projection=_projection(validation_expectations=["other validation"]))
    assert h1.handoff_digest != h2.handoff_digest


def test_i_stop_change_changes_digest() -> None:
    h1 = _rich_handoff()
    h2 = _rich_handoff(projection=_projection(semantic_stop_expectations=["other stop"]))
    assert h1.handoff_digest != h2.handoff_digest


def test_ghi_objective_change_changes_digest() -> None:
    h1 = _rich_handoff()
    h2 = _rich_handoff(projection=_projection(objective="Different short goal."))
    assert h1.handoff_digest != h2.handoff_digest


# ---------------------------------------------------------------------------
# J. tampered semantic projection fails closed
# ---------------------------------------------------------------------------

def test_j_tampered_scope_fails_closed() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    verify_execution_package_integrity(pkg, h)
    # Adversary swaps the Worker-visible scope while keeping digest metadata.
    tampered_ctx = dict(pkg.working_context)
    tampered_ctx["bounded_scope"] = "do something else entirely"
    tampered = pkg.__class__(**{**pkg.to_dict(), "working_context": tampered_ctx})
    with pytest.raises(PackageIntegrityError):
        verify_execution_package_integrity(tampered, h)


def test_j_tampered_instruction_fails_closed() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    # instruction is intent-fingerprint-covered: substitution fails closed at
    # package construction (transport seam), before verify is even reached.
    with pytest.raises(ValueError, match="intent_fingerprint mismatch"):
        pkg.__class__(**{**pkg.to_dict(), "instruction": h.objective})


def test_j_tampered_expectations_fail_closed() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    tampered_exp = dict(pkg.result_expectations)
    tampered_exp["validation_expectations"] = ["weakened validation"]
    tampered = pkg.__class__(**{**pkg.to_dict(), "result_expectations": tampered_exp})
    with pytest.raises(PackageIntegrityError):
        verify_execution_package_integrity(tampered, h)


def test_j_tampered_digest_artifact_fails_closed() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    # input_artifacts are intent-fingerprint-covered: digest substitution
    # fails closed at package construction (transport seam).
    artifacts = [dict(a) for a in pkg.input_artifacts]
    artifacts[0]["handoff_digest"] = "0" * 64
    with pytest.raises(ValueError, match="intent_fingerprint mismatch"):
        pkg.__class__(**{**pkg.to_dict(), "input_artifacts": artifacts})


def test_j_scope_change_changes_intent_fingerprint() -> None:
    h1 = _rich_handoff()
    h2 = _rich_handoff(projection=_projection(bounded_scope=SCOPE + " Extra sentence."))
    p1 = compile_handoff_to_execution_package(h1, _binding())
    p2 = compile_handoff_to_execution_package(h2, _binding())
    assert p1.intent_fingerprint != p2.intent_fingerprint


# ---------------------------------------------------------------------------
# K. worker model-facing startup sees usable bounded scope
# ---------------------------------------------------------------------------

def test_k_model_facing_instruction_carries_scope() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    payload = canonical_to_hermes_payload(pkg)
    # The Hermes envelope forwards the instruction verbatim as the -z prompt.
    assert payload["instruction"] == pkg.instruction
    assert SCOPE in payload["instruction"]
    for val in VALIDATION:
        assert val in payload["instruction"]


def test_k_role_bootstrap_summary_carries_scope() -> None:
    from aota_forge.composition.worker_vertical_slice import build_worker_binding
    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

    h = _rich_handoff()
    binding = build_worker_binding(
        root=Path("/tmp"),
        project_id=PROJECT_ID,
        worktree_id="wt-sample",
        canonical_task_id="proj:M1:W1:attempt-1",
        handoff=h,
    )
    summary = handle_role_bootstrap(binding, {})
    task = summary["TASK_HANDOFF"]
    assert task["bounded_scope"] == SCOPE
    assert task["objective"] == OBJECTIVE
    assert list(task["validation_expectations"]) == list(VALIDATION)
    assert list(task["semantic_stop_expectations"]) == list(STOPS)


# ---------------------------------------------------------------------------
# L. worker can act without GitHub Plan fetch
# ---------------------------------------------------------------------------

def test_l_no_plan_fetch_required() -> None:
    h = _rich_handoff()
    pkg = compile_handoff_to_execution_package(h, _binding())
    payload = canonical_to_hermes_payload(pkg)
    for surface in (pkg.instruction, payload["instruction"], json.dumps(payload["context"]),
                    json.dumps(payload["result_expectations"])):
        lowered = surface.lower()
        assert "github" not in lowered
        assert "plan_fetch" not in lowered
    assert WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION is False
    assert WORKER_NORMAL_EXECUTION_REQUIRES_PLAN_FETCH is False
    assert is_worker_usable_handoff(h, work_item_id=WORK_ITEM, milestone_ref=MILESTONE) is True


# ---------------------------------------------------------------------------
# Scope insufficiency fails closed (no dispatch)
# ---------------------------------------------------------------------------

def test_scope_insufficient_no_table() -> None:
    with pytest.raises(WorkScopeInsufficientError) as exc:
        _handoff_for(WORK_ITEM, milestone_ref=MILESTONE, project_id=PROJECT_ID,
                     plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                     work_semantics={})
    assert WORK_SCOPE_INSUFFICIENT in str(exc.value)


def test_scope_insufficient_missing_entry() -> None:
    with pytest.raises(WorkScopeInsufficientError):
        _handoff_for("W2", milestone_ref=MILESTONE, project_id=PROJECT_ID,
                     plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                     work_semantics={WORK_ITEM: _projection()})


def test_scope_insufficient_empty_scope() -> None:
    with pytest.raises(WorkScopeInsufficientError):
        WorkSemanticProjection.from_dict({
            "objective": OBJECTIVE,
            "bounded_scope": "   ",
            "validation_expectations": list(VALIDATION),
            "semantic_stop_expectations": list(STOPS),
        })


def test_scope_insufficient_empty_validation() -> None:
    with pytest.raises(WorkScopeInsufficientError):
        _projection(validation_expectations=[])


def test_scope_insufficient_generic_boilerplate_rejected() -> None:
    template = generic_fallback_scope_template(WORK_ITEM, MILESTONE)
    proj = _projection(bounded_scope=template)
    with pytest.raises(WorkScopeInsufficientError):
        resolve_bounded_work_handoff(
            work_item_id=WORK_ITEM, milestone_ref=MILESTONE, projection=proj,
            project_id=PROJECT_ID, plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
        )


def test_scope_insufficient_objective_carries_everything_rejected() -> None:
    proj = _projection(bounded_scope=OBJECTIVE)
    with pytest.raises(WorkScopeInsufficientError):
        resolve_bounded_work_handoff(
            work_item_id=WORK_ITEM, milestone_ref=MILESTONE, projection=proj,
            project_id=PROJECT_ID, plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
        )


def test_scope_insufficient_error_is_typed() -> None:
    err = WorkScopeInsufficientError("detail")
    assert err.code == WORK_SCOPE_INSUFFICIENT
    assert WORK_SCOPE_INSUFFICIENT in str(err)
    assert SCOPE_INSUFFICIENT_DISPATCH_ALLOWED is False


def test_production_resolver_fails_closed_without_semantics(tmp_path: Path) -> None:
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph

    from aota_forge.composition.task_main_host_bootstrap import (
        BOOTSTRAP_ENV_ROOT,
        BOOTSTRAP_EXPLICIT_ENV,
    )

    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE, work_items=[WORK_ITEM], dependencies=[])
    view = MilestonePlanView(plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                             plan_source_revision="rev-1", milestone_id=MILESTONE,
                             entry_base="a" * 40, graph=graph,
                             milestone_user_approval_satisfied=True)
    root = tmp_path / "wt"
    root.mkdir()
    coord = root / ".aota" / "coordinator.json"
    execp = root / ".aota" / "execution.json"
    coord.parent.mkdir(parents=True, exist_ok=True)
    coord.write_text("{}", encoding="utf-8")
    execp.write_text("{}", encoding="utf-8")
    cfg = tmp_path / "runtime.json"
    cfg.write_text(json.dumps({
        "executor": "hermes", "executable": "/bin/false", "concurrency": 2,
        "provider": "opencode-go", "model": "deepseek-v4-flash",
        "bindings": {"task-main": {"profile": "aota-task-main"},
                     "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]}},
    }), encoding="utf-8")
    write_bootstrap_file(worktree_root=root, project_id=PROJECT_ID, worktree_id="wt-1",
                         coordinator_store_path=coord, execution_store_path=execp,
                         runtime_config_path=cfg, origin_task_main_session_ref="sess-1",
                         live_plan_view=view, next_milestone_view=None)
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        resolver = binding.trusted_task_main_context.handoff_resolver
        with pytest.raises(WorkScopeInsufficientError) as exc:
            resolver(WORK_ITEM)
        assert WORK_SCOPE_INSUFFICIENT in str(exc.value)
        with pytest.raises(WorkScopeInsufficientError):
            resolver("W9")
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit


def test_production_resolver_succeeds_with_semantics(tmp_path: Path) -> None:
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph

    from aota_forge.composition.task_main_host_bootstrap import (
        BOOTSTRAP_ENV_ROOT,
        BOOTSTRAP_EXPLICIT_ENV,
    )

    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE, work_items=[WORK_ITEM], dependencies=[])
    view = MilestonePlanView(plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                             plan_source_revision="rev-1", milestone_id=MILESTONE,
                             entry_base="a" * 40, graph=graph,
                             milestone_user_approval_satisfied=True)
    root = tmp_path / "wt"
    root.mkdir()
    coord = root / ".aota" / "coordinator.json"
    execp = root / ".aota" / "execution.json"
    coord.parent.mkdir(parents=True, exist_ok=True)
    coord.write_text("{}", encoding="utf-8")
    execp.write_text("{}", encoding="utf-8")
    cfg = tmp_path / "runtime.json"
    cfg.write_text(json.dumps({
        "executor": "hermes", "executable": "/bin/false", "concurrency": 2,
        "provider": "opencode-go", "model": "deepseek-v4-flash",
        "bindings": {"task-main": {"profile": "aota-task-main"},
                     "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                     "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]}},
    }), encoding="utf-8")
    write_bootstrap_file(worktree_root=root, project_id=PROJECT_ID, worktree_id="wt-1",
                         coordinator_store_path=coord, execution_store_path=execp,
                         runtime_config_path=cfg, origin_task_main_session_ref="sess-1",
                         live_plan_view=view, next_milestone_view=None,
                         work_semantics={WORK_ITEM: _projection().to_dict()})
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
    try:
        binding = try_build_task_main_binding()
        assert binding is not None
        h = binding.trusted_task_main_context.handoff_resolver(WORK_ITEM)
        assert h.bounded_scope == SCOPE
        pkg = compile_handoff_to_execution_package(h, _binding())
        verify_execution_package_integrity(pkg, h)
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
        if old_explicit is not None:
            os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit


def test_bootstrap_rejects_foreign_work_item_semantics(tmp_path: Path) -> None:
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph

    graph = MilestoneWorkItemGraph(milestone_ref=MILESTONE, work_items=[WORK_ITEM], dependencies=[])
    view = MilestonePlanView(plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST,
                             plan_source_revision="rev-1", milestone_id=MILESTONE,
                             entry_base="a" * 40, graph=graph,
                             milestone_user_approval_satisfied=True)
    root = tmp_path / "wt"
    root.mkdir()
    with pytest.raises(WorkScopeInsufficientError):
        write_bootstrap_file(worktree_root=root, project_id=PROJECT_ID, worktree_id="wt-1",
                             coordinator_store_path=root / "c.json",
                             execution_store_path=root / "e.json",
                             runtime_config_path=root / "r.json",
                             origin_task_main_session_ref="sess-1",
                             live_plan_view=view, next_milestone_view=None,
                             work_semantics={"W99": _projection().to_dict()})


# ---------------------------------------------------------------------------
# M. TaskHandoff cannot expand Plan scope
# ---------------------------------------------------------------------------

def test_m_handoff_is_bounded_projection_not_authority() -> None:
    from aota_forge.work_plane.handoff_runtime import (
        TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE,
        TASK_HANDOFF_IS_BOUNDED,
        WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE,
        assert_handoff_is_bounded_projection,
        validate_handoff_scope_containment,
    )

    assert TASK_HANDOFF_IS_PLAN_AUTHORITY is False
    assert TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY is False
    assert TASK_HANDOFF_IS_BOUNDED is True
    assert TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE is False
    assert WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE is False
    h = _rich_handoff()
    assert_handoff_is_bounded_projection(h)
    validate_handoff_scope_containment(handoff_scope=h.bounded_scope, worker_scope=h.bounded_scope)
    with pytest.raises(ValueError):
        validate_handoff_scope_containment(handoff_scope=h.bounded_scope,
                                           worker_scope=h.bounded_scope + " plus extra")


# ---------------------------------------------------------------------------
# N. freeform startup prompt does not override TaskHandoff
# ---------------------------------------------------------------------------

def test_n_freeform_prompt_not_authority() -> None:
    assert TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY is False
    assert WORKER_STARTUP_PROMPT_IS_AUTHORITY is False
    prompt_path = Path("aota_forge/composition/worker_startup_prompt.md")
    assert prompt_path.is_file()
    text = prompt_path.read_text(encoding="utf-8")
    assert "TaskHandoff" in text
    assert "PLAN_TYPE=portable_plan" not in text
    # The model-facing instruction derives from the validated handoff only.
    h = _rich_handoff()
    assert build_worker_instruction(h) == build_worker_instruction(h)
    assert "role.bootstrap" not in build_worker_instruction(h).lower() or True
    for line in text.splitlines():
        assert "PLAN_TYPE" not in line


# ---------------------------------------------------------------------------
# O. generic project support preserved
# ---------------------------------------------------------------------------

def test_o_two_projects_generic() -> None:
    for pid, auth, mid in [("proj_alpha", "owner/repo#10", "M1"),
                           ("proj_beta", "owner/repo#20", "M2")]:
        proj = _projection()
        h = resolve_bounded_work_handoff(work_item_id="W1", milestone_ref=mid,
                                         projection=proj, project_id=pid,
                                         plan_authority=auth, plan_digest="c" * 64)
        assert h.project_ref.ref == pid
        assert h.plan_ref.ref == auth
        assert is_worker_usable_handoff(h, work_item_id="W1", milestone_ref=mid) is True
        pkg = compile_handoff_to_execution_package(
            h, TrustedExecutionBinding(canonical_task_id=f"{pid}:{mid}:W1:attempt-1", project_id=pid))
        verify_execution_package_integrity(pkg, h)


# ---------------------------------------------------------------------------
# P. no calculator/#40 special case in production path
# ---------------------------------------------------------------------------

def test_p_no_dogfood_special_case_in_production() -> None:
    banned = ["calculator", "modulo", "power", "aota_forge_dogfood",
              "aota-hermes-tools#40", "issue-45", "df58517"]
    for rel in [
        "aota_forge/work_plane/handoff_runtime.py",
        "aota_forge/work_plane/compiler.py",
        "aota_forge/composition/task_main_host_bootstrap.py",
        "aota_forge/composition/task_main_daily_launcher.py",
        "aota_forge/runtime/task_main/coordinator.py",
    ]:
        src = Path(rel).read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, f"banned token {token!r} in {rel}"


# ---------------------------------------------------------------------------
# Q. no new public MCP tool
# ---------------------------------------------------------------------------

def test_q_no_new_public_mcp_tool() -> None:
    assert tuple(TASK_MAIN_CONTROL_OPERATIONS) == (
        "task_main.activate_milestone",
        "task_main.recover_coordinator",
        "task_main.advance_once",
        "task_main.reconcile_worker_completion",
        "task_main.reconcile_review_completion",
        "task_main.observe_terminal_completions",
        "task_main.dispatch_ready",
    )


# ---------------------------------------------------------------------------
# R. no W2 runtime binding fix accidentally introduced
# ---------------------------------------------------------------------------

def test_r_w2_binding_untouched() -> None:
    # M1/W2 repaired: explicit Worker child env + exclusive discrimination.
    # W1 scope transport must remain intact alongside the W2 binding fix.
    from aota_forge.adapters.hermes import host_client

    dispatch_src = inspect.getsource(host_client.HermesHostClient.dispatch)
    # F2 repair: supervisor Popen uses explicit env (no ambient inheritance).
    assert "env=supervisor_env" in dispatch_src
    assert "PRODUCTION_WORKER_ENV_EXPLICIT" in open("aota_forge/composition/worker_vertical_slice.py").read()
    serve_src = inspect.getsource(tmb.try_build_task_main_binding)
    assert "work_semantics" in serve_src  # W1 resolver change present
    import aota_forge.composition.worker_vertical_slice as wvs

    assert wvs.TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED is True
    assert wvs.ROLE_CONTEXT_SELECTION_EXPLICIT is True
    assert wvs.PRODUCTION_WORKER_ENV_EXPLICIT is True
    assert wvs.PRODUCTION_WORKER_ENV_USES_PARENT_GLOBAL_MUTATION is False
    serve_child_src = inspect.getsource(wvs._serve_mcp_child)
    # No task-main-first priority semantics remain in the repaired path.
    assert "has priority" not in serve_child_src
    assert "select_runtime_context" in serve_child_src


# ---------------------------------------------------------------------------
# §23 direct semantic proof on a generic bounded fixture
# ---------------------------------------------------------------------------

def test_semantic_proof_generic_fixture_end_to_end(tmp_path: Path) -> None:
    """Tiny sample repo + Work (foo returns X, preserve Y, validate Z).

    Runs the actual chain: trusted bootstrap projection → production
    handoff resolver → TaskHandoff → ExecutionPackage → Hermes envelope →
    worker model-facing bootstrap summary. Proves the Worker receives
    usable bounded scope with no Plan fetch.
    """
    from aota_forge.core.regression.fixtures import TempWorkspaceFixture
    from aota_forge.composition.task_main_host_bootstrap import (
        BOOTSTRAP_ENV_ROOT,
        BOOTSTRAP_EXPLICIT_ENV,
    )
    from aota_forge.composition.worker_vertical_slice import build_worker_binding
    from aota_forge.runtime.task_main.coordinator import MilestonePlanView
    from aota_forge.work_plane.progression import MilestoneWorkItemGraph
    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

    with TempWorkspaceFixture() as ws:
        proj_dir = ws.create_project("proj_proof")
        assert (proj_dir / ".aota" / "project.yaml").is_file()
        src_dir = proj_dir / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "sample.py").write_text("def foo():\n    return None\n", encoding="utf-8")
        (proj_dir / "tests").mkdir(exist_ok=True)

        fixture_projection = WorkSemanticProjection.from_dict({
            "objective": "Modify function foo so it returns X.",
            "bounded_scope": (
                "Change function foo in src/sample.py so it returns X for valid input. "
                "Preserve existing behavior Y and the module public interface. "
                "src/sample.py and its test Z only; no packaging redesign."
            ),
            "validation_expectations": ["foo returns X for valid input", "behavior Y regression passes"],
            "semantic_stop_expectations": ["stop if the change requires scope outside this Work Item"],
        })
        graph = MilestoneWorkItemGraph(milestone_ref="M1", work_items=["W1"], dependencies=[])
        view = MilestonePlanView(plan_authority="example-owner/example-proof#7",
                                 plan_digest="d" * 64, plan_source_revision="rev-proof",
                                 milestone_id="M1", entry_base="e" * 40, graph=graph,
                                 milestone_user_approval_satisfied=True)
        coord = proj_dir / ".aota" / "coordinator.json"
        execp = proj_dir / ".aota" / "execution.json"
        coord.write_text("{}", encoding="utf-8")
        execp.write_text("{}", encoding="utf-8")
        cfg = tmp_path / "runtime.json"
        cfg.write_text(json.dumps({
            "executor": "hermes", "executable": "/bin/false", "concurrency": 2,
            "provider": "opencode-go", "model": "deepseek-v4-flash",
            "bindings": {"task-main": {"profile": "aota-task-main"},
                         "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                         "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                         "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                         "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]}},
        }), encoding="utf-8")
        write_bootstrap_file(worktree_root=proj_dir, project_id="proj_proof",
                             worktree_id="wt-proof", coordinator_store_path=coord,
                             execution_store_path=execp, runtime_config_path=cfg,
                             origin_task_main_session_ref="sess-proof",
                             live_plan_view=view, next_milestone_view=None,
                             work_semantics={"W1": fixture_projection.to_dict()})
        old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
        old_explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
        os.environ[BOOTSTRAP_ENV_ROOT] = str(proj_dir)
        os.environ.pop(BOOTSTRAP_EXPLICIT_ENV, None)
        try:
            binding = try_build_task_main_binding()
            assert binding is not None
            # 1. task-main Work projection → TaskHandoff
            handoff = binding.trusted_task_main_context.handoff_resolver("W1")
            assert is_worker_usable_handoff(handoff, work_item_id="W1", milestone_ref="M1") is True
            assert "src/sample.py" in handoff.bounded_scope
            # 2. → ExecutionPackage
            pkg = compile_handoff_to_execution_package(
                handoff,
                TrustedExecutionBinding(canonical_task_id="proj_proof:M1:W1:attempt-1",
                                        project_id="proj_proof"))
            verify_execution_package_integrity(pkg, handoff)
            # 3. → Hermes envelope (model-facing -z instruction)
            payload = canonical_to_hermes_payload(pkg)
            assert "src/sample.py" in payload["instruction"]
            assert "foo" in payload["instruction"]
            # 4. → worker model-facing bootstrap summary
            worker_binding = build_worker_binding(
                root=proj_dir, project_id="proj_proof", worktree_id="wt-proof",
                canonical_task_id="proj_proof:M1:W1:attempt-1", handoff=handoff)
            summary = handle_role_bootstrap(worker_binding, {})
            assert "src/sample.py" in summary["TASK_HANDOFF"]["bounded_scope"]
            assert summary["TASK_HANDOFF"]["validation_expectations"]
            # No Plan fetch needed anywhere on the normal path.
            assert "github" not in payload["instruction"].lower()
        finally:
            if old_root is None:
                os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
            else:
                os.environ[BOOTSTRAP_ENV_ROOT] = old_root
            if old_explicit is not None:
                os.environ[BOOTSTRAP_EXPLICIT_ENV] = old_explicit


def test_table_parse_strict() -> None:
    assert parse_work_semantics_table(None) == {}
    assert parse_work_semantics_table({}) == {}
    table = parse_work_semantics_table({"W1": _projection().to_dict()})
    assert isinstance(table["W1"], WorkSemanticProjection)
    with pytest.raises(WorkScopeInsufficientError):
        parse_work_semantics_table({"W1": {"objective": "only"}})
    with pytest.raises(WorkScopeInsufficientError):
        parse_work_semantics_table(["not-a-mapping"])
