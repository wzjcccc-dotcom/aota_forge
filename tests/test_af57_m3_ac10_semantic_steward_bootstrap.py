"""AF #57 M3/AC10 — semantic Steward production bootstrap grounding repair.

Focused guard for the two real AC10 integration defects proven by the frozen
RV1 real proof:

* Defect A: the semantic Steward ``TaskHandoff.project_ref`` must use the
  canonical raw ``project_id`` (``aota_forge``), never the projection-style
  ``project:<id>`` form, so the governed Worker MCP child binding check
  (``binding.handoff.project_ref.ref == binding.project_id``) accepts it.
* Defect B: the production Worker env resolver must ground a semantic Steward
  dispatch from its trusted Governance checkpoint + typed semantic residual +
  logical replay identity/canonical Steward task id, not from a fabricated
  normal Plan Work Item (``work_item_id`` / ``work_source_digest``).

The normal Worker source-grounding path is preserved unchanged and remains
fail-closed; a project-steward role name alone can never bypass grounding.

No real model, no physical Worker dispatch: these are bootstrap/composition
guards.  The real AC10 proof runs separately against the frozen source.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from aota_forge.composition import task_main_host_bootstrap as host
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    is_semantic_steward_dispatch_handoff,
    validate_semantic_steward_dispatch_grounding,
    write_bootstrap_file,
)
from aota_forge.governance.stewardship import (
    build_semantic_steward_handoff,
    evaluate_checkpoint,
)
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import (
    FileBackedTaskMainCoordinatorStore,
)
from aota_forge.runtime.task_main.reconciliation import (
    COMPLETION_KIND_REVIEW,
    DISPOSITION_REVIEW_READY_FOR_STEWARD,
    CompletionReconciliationReceipt,
)
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    load_binding_from_envelope,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_store import HANDOFF_CONTROL_FIELDS, handoff_write
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.result_card import ResultHandoffRef

PROJECT_ID = "aota_forge"
PLAN_ID = "plan_wzjcccc_dotcom_aota_hermes_tools_57"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#57"
MILESTONE = "M3"
ENTRY_BASE = "a" * 40
PLAN_DIGEST = "d" * 64
REVIEWED_FRONTIER = "frontier:ac10-reviewed-rv1"
SEMANTIC_REF = "architecture:af57-ac10-semantic-residual"


class _EnvGuard:
    def __init__(self):
        self.saved = dict(os.environ)

    def __enter__(self):
        for key in list(os.environ):
            if key.startswith("AOTA_") or key == "PYTHONPATH":
                del os.environ[key]
        return self

    def __exit__(self, *args):
        os.environ.clear()
        os.environ.update(self.saved)


def _make_project(tmp_path: Path, project_id: str = PROJECT_ID) -> Path:
    root = tmp_path / f"wt-{project_id}"
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {project_id}\n"
        "  name: ac10 semantic steward proof\n"
        "  kind: test\n"
        "  status: active\n"
        "summary: AC10 bootstrap grounding guard project\n"
        "capabilities: []\n"
        "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    return root


def _runtime_config_json(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/false",
                "concurrency": 1,
                "provider": "opencode-go",
                "model": "m",
                "bindings": {
                    "task-main": {"profile": "aota-task-main"},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                },
            }
        ),
        encoding="utf-8",
    )


def _view() -> MilestonePlanView:
    graph = MilestoneWorkItemGraph(
        milestone_ref=MILESTONE, work_items=["W1", "W2"], dependencies=[("W1", "W2")]
    )
    return MilestonePlanView(
        plan_authority=PLAN_AUTH,
        plan_digest=PLAN_DIGEST,
        plan_source_revision="rev-ac10",
        milestone_id=MILESTONE,
        entry_base=ENTRY_BASE,
        graph=graph,
        milestone_user_approval_satisfied=False,
    )


def _seed_closure_state(coordinator_path: Path) -> None:
    review = MilestoneReviewEvidence(
        milestone_ref=SemanticReference(ref=MILESTONE),
        review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref=REVIEWED_FRONTIER),
        review_result_ref=ResultHandoffRef(
            ref="review-task-ac10", digest="review-card-digest"
        ),
        review_result_digest="review-card-digest",
    )
    receipt = CompletionReconciliationReceipt(
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        plan_authority=PLAN_AUTH,
        milestone_id=MILESTONE,
        work_item_id=None,
        completion_kind=COMPLETION_KIND_REVIEW,
        canonical_task_id="review-task-ac10",
        card_digest="review-card-digest",
        result_handoff_ref="review-task-ac10",
        result_digest="review-card-digest",
        prev_coordinator_revision=1,
        next_coordinator_revision=2,
        progression_revision=1,
        progression_disposition=DISPOSITION_REVIEW_READY_FOR_STEWARD,
        validation_evidence_digest=None,
        risk_disposition_digest=None,
        review_workflow_disposition="PASS",
        working_truth_digest="working-truth-digest",
        governed_evidence={
            "review_evidence": review.to_dict(),
            "closure_ready": True,
        },
        reconciled_at="2026-09-19T00:00:00+00:00",
    ).with_digest()
    state = TaskMainCoordinatorState(
        coordinator_id=f"{PROJECT_ID}:{MILESTONE}",
        plan_authority=PLAN_AUTH,
        plan_digest=PLAN_DIGEST,
        milestone_id=MILESTONE,
        entry_base=ENTRY_BASE,
        origin_task_main_session_ref="session:ac10",
        project_id=PROJECT_ID,
        executor_id="hermes",
        work_items=("W1", "W2"),
        wi_status={"W1": "PENDING", "W2": "PENDING"},
        reconciled_completions={receipt.canonical_task_id: receipt.to_dict()},
        user_approval_satisfied=False,
        coordinator_revision=2,
    )
    store = FileBackedTaskMainCoordinatorStore(coordinator_path)
    try:
        store.create(state)
    finally:
        store.close()


class _BootstrapWorld:
    """Trusted bootstrap fixture: production binding + canonical dispatch data."""

    def __init__(self, binding, root: Path, coordinator_path: Path, view, data, sandbox):
        self.binding = binding
        self.root = root
        self.coordinator_path = coordinator_path
        self.view = view
        self.data = data
        self.sandbox = sandbox

    @property
    def resolver(self):
        control = self.binding.trusted_task_main_context.control_service
        dispatcher = getattr(control, "_dispatcher")
        adapters = list(getattr(dispatcher.registry, "_adapters", {}).values())
        assert adapters, "production dispatcher carries no adapter"
        host_client = getattr(adapters[0], "_host_client", None)
        resolver = getattr(host_client, "_worker_env_resolver", None)
        assert callable(resolver), "production Worker env resolver is not wired"
        return resolver

    def trusted_state(self):
        store = FileBackedTaskMainCoordinatorStore(self.coordinator_path)
        try:
            return store.get(f"{PROJECT_ID}:{MILESTONE}")
        finally:
            store.close()

    def trusted_checkpoint(self):
        return host._build_stewardship_checkpoint(
            runner_outcome=host._TrustedClosureReadyOutcome(),
            state=self.trusted_state(),
            live_view=self.view,
            sandbox=self.sandbox,
            trusted_plan=host._trusted_plan_identity_from_bootstrap(self.view, self.data),
            semantic_facts=host._semantic_facts_from_bootstrap(self.data),
            plan_id=PLAN_ID,
            all_milestones_closed=True,
        )


def _bootstrap_world(
    tmp_path: Path, *, semantic_facts: dict | None = None
) -> _BootstrapWorld:
    root = _make_project(tmp_path)
    coordinator_path = root / ".aota" / "coordinator.json"
    execution_path = root / ".aota" / "execution.json"
    runtime_config = tmp_path / "runtime.json"
    _runtime_config_json(runtime_config)
    view = _view()
    _seed_closure_state(coordinator_path)
    facts = (
        semantic_facts
        if semantic_facts is not None
        else {"architecture_question_refs": [SEMANTIC_REF]}
    )
    bootstrap = write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-ac10",
        coordinator_store_path=coordinator_path,
        execution_store_path=execution_path,
        runtime_config_path=runtime_config,
        origin_task_main_session_ref="session:ac10",
        live_plan_view=view,
        next_milestone_view=None,
        plan_id=PLAN_ID,
        stewardship_semantic_facts=facts,
    )
    data = json.loads(bootstrap.read_text(encoding="utf-8"))
    with _EnvGuard():
        os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
        os.environ[BOOTSTRAP_EXPLICIT_ENV] = str(bootstrap)
        binding = host.try_build_task_main_binding()
    assert binding is not None
    return _BootstrapWorld(binding, root, coordinator_path, view, data, binding.sandbox)


def _canonical_dispatch(world: _BootstrapWorld):
    checkpoint = world.trusted_checkpoint()
    evaluation = evaluate_checkpoint(checkpoint)
    residual = evaluation.residual
    assert residual is not None
    handoff = build_semantic_steward_handoff(checkpoint, residual)
    task_id = host._steward_task_id(checkpoint, handoff)
    return checkpoint, handoff, task_id


def _write_durable_work_item(
    world: _BootstrapWorld,
    *,
    semantic: dict,
    task_id: str,
    target_role: str = "project-steward",
    plan_ref: str | None = None,
    milestone_id: str | None = None,
    work_item_id: str | None = None,
    provenance: dict | None = None,
):
    return handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=world.sandbox,
        plan_ref=plan_ref if plan_ref is not None else PLAN_AUTH,
        milestone_id=milestone_id if milestone_id is not None else MILESTONE,
        work_item_id=work_item_id,
        target_role=target_role,
        task_id=task_id,
        provenance=provenance,
    )


def _semantic_payload(handoff: TaskHandoff) -> dict:
    return {
        key: value
        for key, value in handoff.to_dict().items()
        if key not in HANDOFF_CONTROL_FIELDS
    }


def _payload(task_id: str, ref) -> dict:
    return {
        "profile": "aota-worker",
        "instruction": "bounded semantic steward bootstrap guard",
        "context": {
            "canonical_task_id": task_id,
            "project_id": PROJECT_ID,
            "working_context": {
                "trusted_work_handoff": {
                    "ref": ref.ref,
                    "digest": ref.digest,
                    "mode": "work_item",
                },
                "refs": {},
            },
        },
    }


# ---------------------------------------------------------------------------
# Defect A — producer uses the canonical raw project_id
# ---------------------------------------------------------------------------


def test_semantic_steward_handoff_project_ref_is_canonical_project_id(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    _, handoff, _ = _canonical_dispatch(world)
    assert handoff.project_ref is not None
    assert handoff.project_ref.ref == PROJECT_ID
    assert not handoff.project_ref.ref.startswith("project:")
    assert handoff.work_item_ref is None
    assert handoff.plan_ref is not None and handoff.plan_ref.ref == PLAN_AUTH
    assert handoff.milestone_ref is not None and handoff.milestone_ref.ref == MILESTONE


# ---------------------------------------------------------------------------
# Defect B — bootstrap grounds the semantic Steward from trusted checkpoint
# ---------------------------------------------------------------------------


def test_semantic_steward_bootstrap_grounding_accepts_canonical_dispatch(
    tmp_path: Path,
) -> None:
    world = _bootstrap_world(tmp_path)
    checkpoint, handoff, task_id = _canonical_dispatch(world)
    assert task_id.startswith("steward:")

    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(handoff), task_id=task_id
    )
    # The durable handoff carries NO normal Work Item fields and no provenance.
    artifact = json.loads(
        (world.root / ".aota" / "handoffs" / f"{ref.digest}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "work_item_id" not in artifact["envelope"]
    assert "provenance" not in artifact["envelope"]
    assert artifact["semantic"]["project_ref"]["ref"] == PROJECT_ID

    env = world.resolver(_payload(task_id, ref))
    # SEMANTIC_STEWARD_BOOTSTRAP_GROUNDING=PASS
    assert isinstance(env, dict)
    envelope_path = env[PRE_RESOLVED_BINDING_ENV]
    binding = load_binding_from_envelope(envelope_path)
    assert binding.canonical_task_id == task_id
    assert binding.project_id == PROJECT_ID
    assert binding.handoff.project_ref is not None
    assert binding.handoff.project_ref.ref == PROJECT_ID
    assert binding.handoff.work_item_ref is None
    assert binding.handoff.milestone_ref is not None
    assert binding.handoff.milestone_ref.ref == checkpoint.milestone_ref


def test_semantic_steward_grounding_is_project_bound(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    checkpoint, handoff, _ = _canonical_dispatch(world)
    forged_handoff = dataclasses.replace(
        handoff, project_ref=SemanticReference(ref="foreign_project")
    )
    # A task identity recomputed over the forged handoff still cannot
    # authorize a foreign project binding: the trusted checkpoint handoff is
    # the sole authority for the Worker project binding.
    forged_task = host._steward_task_id(checkpoint, forged_handoff)
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(forged_handoff), task_id=forged_task
    )
    artifact = json.loads(
        (world.root / ".aota" / "handoffs" / f"{ref.digest}.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["semantic"]["project_ref"]["ref"] == "foreign_project"
    with pytest.raises(TrustedBindingError):
        world.resolver(_payload(forged_task, ref))


# ---------------------------------------------------------------------------
# Negative semantic Steward grounding — all fail before model execution
# ---------------------------------------------------------------------------


def test_missing_semantic_residual_dispatch_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path, semantic_facts={})
    checkpoint = world.trusted_checkpoint()
    evaluation = evaluate_checkpoint(checkpoint)
    assert evaluation.residual is None
    forged_task = f"steward:{'0' * 64}"
    forged = TaskHandoff(
        work_role="project-steward",
        task_kind="milestone-closure-request",
        objective="forged steward dispatch without residual",
        bounded_scope="governed checkpoint scope",
        validation_expectations=("bounded",),
        semantic_stop_expectations=("stop",),
        project_ref=SemanticReference(ref=PROJECT_ID),
        milestone_ref=SemanticReference(ref=MILESTONE),
    )
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(forged), task_id=forged_task
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(forged_task, ref))
    assert "semantic residual" in str(excinfo.value)


def test_foreign_steward_task_identity_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    _, handoff, _ = _canonical_dispatch(world)
    foreign_task = f"steward:{'1' * 64}"
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(handoff), task_id=foreign_task
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(foreign_task, ref))
    assert "logical checkpoint" in str(excinfo.value)


def test_foreign_canonical_task_identity_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    _, handoff, task_id = _canonical_dispatch(world)
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(handoff), task_id=task_id
    )
    payload = _payload(task_id, ref)
    payload["context"]["canonical_task_id"] = f"steward:{'2' * 64}"
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(payload)
    assert "dispatch payload" in str(excinfo.value)


def test_mismatched_plan_identity_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    checkpoint, handoff, task_id = _canonical_dispatch(world)
    ref = _write_durable_work_item(
        world,
        semantic=_semantic_payload(handoff),
        task_id=task_id,
        plan_ref=f"local-governance:{PROJECT_ID}/plan_foreign",
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(task_id, ref))
    assert "Plan identity" in str(excinfo.value)


def test_mismatched_milestone_identity_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    _, handoff, task_id = _canonical_dispatch(world)
    ref = _write_durable_work_item(
        world,
        semantic=_semantic_payload(handoff),
        task_id=task_id,
        milestone_id="M4",
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(task_id, ref))
    assert "Milestone" in str(excinfo.value)


def test_role_alone_project_steward_dispatch_denied(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    # A role-only project-steward dispatch: no canonical steward task identity,
    # no Work Item grounding.  It must never bypass grounding.
    forged = TaskHandoff(
        work_role="project-steward",
        task_kind="milestone-closure-request",
        objective="role-only steward request",
        bounded_scope="role-only scope",
        validation_expectations=("bounded",),
        semantic_stop_expectations=("stop",),
        project_ref=SemanticReference(ref=PROJECT_ID),
        milestone_ref=SemanticReference(ref=MILESTONE),
    )
    task_id = f"{PROJECT_ID}:{MILESTONE}:W99:abcd1234:ef567890"
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(forged), task_id=task_id
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(task_id, ref))
    assert "WORK_SOURCE_GROUNDING_MISSING" in str(excinfo.value)


def test_steward_structural_declaration_is_not_role_text() -> None:
    opened = {
        "mode": "work_item",
        "envelope": {"target_role": "project-steward", "task_id": "M3:W1:abcd:ef", "work_item_id": "W1"},
    }
    assert is_semantic_steward_dispatch_handoff(opened) is False
    opened["envelope"]["task_id"] = f"steward:{'3' * 64}"
    assert is_semantic_steward_dispatch_handoff(opened) is True


# ---------------------------------------------------------------------------
# Normal Worker source grounding is preserved (fail closed)
# ---------------------------------------------------------------------------


def _normal_worker_semantic() -> dict:
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="bounded-work-item",
        objective="normal worker dispatch",
        bounded_scope="normal worker bounded scope",
        validation_expectations=("bounded",),
        semantic_stop_expectations=("stop",),
        project_ref=SemanticReference(ref=PROJECT_ID),
        milestone_ref=SemanticReference(ref=MILESTONE),
        work_item_ref=SemanticReference(ref="W1"),
    )
    return _semantic_payload(handoff)


def test_normal_worker_missing_work_item_id_fails_closed(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    task_id = f"{PROJECT_ID}:{MILESTONE}:W1:abcd1234:ef567890"
    semantic = _normal_worker_semantic()
    ref = _write_durable_work_item(
        world, semantic=semantic, task_id=task_id, target_role="coder"
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(task_id, ref))
    assert "WORK_SOURCE_GROUNDING_MISSING" in str(excinfo.value)
    assert "work_item_id" in str(excinfo.value)


def test_normal_worker_missing_work_source_digest_fails_closed(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    task_id = f"{PROJECT_ID}:{MILESTONE}:W1:abcd1234:ef567890"
    semantic = _normal_worker_semantic()
    ref = _write_durable_work_item(
        world,
        semantic=semantic,
        task_id=task_id,
        target_role="coder",
        work_item_id="W1",
        provenance={"plan_digest": PLAN_DIGEST},
    )
    with pytest.raises(TrustedBindingError) as excinfo:
        world.resolver(_payload(task_id, ref))
    assert "WORK_SOURCE_GROUNDING_MISSING" in str(excinfo.value)
    assert "work_source_digest" in str(excinfo.value)


def test_direct_validator_requires_full_trusted_dimensions(tmp_path: Path) -> None:
    world = _bootstrap_world(tmp_path)
    checkpoint, handoff, task_id = _canonical_dispatch(world)
    ref = _write_durable_work_item(
        world, semantic=_semantic_payload(handoff), task_id=task_id
    )
    from aota_forge.work_plane.handoff_store import handoff_open

    opened = handoff_open(ref.ref, "full", sandbox=world.sandbox)
    payload = _payload(task_id, ref)
    result = validate_semantic_steward_dispatch_grounding(
        opened=opened,
        payload=payload,
        state=world.trusted_state(),
        live_view=world.view,
        sandbox=world.sandbox,
        trusted_plan=host._trusted_plan_identity_from_bootstrap(world.view, world.data),
        semantic_facts=host._semantic_facts_from_bootstrap(world.data),
        plan_id=PLAN_ID,
        all_milestones_closed=True,
        project_id=PROJECT_ID,
    )
    assert result.handoff_digest == handoff.handoff_digest
    assert result.project_ref is not None and result.project_ref.ref == PROJECT_ID
    # Mismatched project dispatch context is denied even with a valid handoff.
    with pytest.raises(TrustedBindingError):
        validate_semantic_steward_dispatch_grounding(
            opened=opened,
            payload=payload,
            state=world.trusted_state(),
            live_view=world.view,
            sandbox=world.sandbox,
            trusted_plan=host._trusted_plan_identity_from_bootstrap(world.view, world.data),
            semantic_facts=host._semantic_facts_from_bootstrap(world.data),
            plan_id=PLAN_ID,
            all_milestones_closed=True,
            project_id="foreign_project",
        )
