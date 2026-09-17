"""AF #57 M2/W3 — Deterministic Project Stewardship Subsystem.

V1:
* typed checkpoint validation for all three conceptual kinds
  (PLAN_INIT / MILESTONE_CLOSE / PLAN_CLOSE);
* deterministic no-residual classification;
* semantic-residual classification;
* user approval can never be inferred, accepted frontier can never be invented;
* deterministic result reproducible.

V2:
* checkpoint -> deterministic producer -> valid StewardResult ->
  existing TrustedStewardFinalizer;
* checkpoint -> semantic residual -> existing Project Steward dispatch seam ->
  existing StewardResult contract -> same TrustedStewardFinalizer.

Proof obligations:
    NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH=yes
    LLM_PATH_REMAINS_AVAILABLE=yes

No real LLM invocation.  No real Git/GitHub mutation: InMemory doubles only
(the Git boundary is exercised through the existing InMemoryGitPort).
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aota_forge.governance.stewardship import (
    BLOCK_INVALID_FRONTIER_REF,
    BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH,
    BLOCK_MILESTONE_IDENTITY_MISMATCH,
    BLOCK_MISSING_EXPECTED_OLD_REF,
    BLOCK_MISSING_MANAGED_COMMENT_FACT,
    BLOCK_MISSING_PLAN_AUTHORITY,
    BLOCK_MISSING_REQUIRED_FACTS,
    BLOCK_OPEN_BLOCKERS,
    BLOCK_PLAN_MILESTONES_NOT_CLOSED,
    BLOCK_READINESS_NOT_READY,
    BLOCK_USER_GATE_REQUIRED,
    CHECKPOINT_DOES_NOT_IMPLY_LLM_INVOCATION,
    CHECKPOINT_KINDS,
    CURRENT_PROJECT_STEWARD_ROLE_REMAINS_MIGRATION_COMPATIBILITY,
    DETERMINISTIC_FIRST_GOVERNANCE,
    DETERMINISTIC_PATH_INVOKES_LLM,
    DETERMINISTIC_STEWARD_RESULT_IS_MECHANICALLY_DERIVED,
    DETERMINISTIC_STEWARD_RESULT_PATH,
    FABRICATED_SEMANTIC_JUDGMENT,
    FAKE_STEWARD_RESULT,
    FINALIZER_INPUT_IS_STEWARD_RESULT,
    GENERIC_GOVERNANCE_WRITE_API,
    LLM_PATH_REMAINS_AVAILABLE,
    NEW_EVENT_BUS_CREATED,
    NEW_EXECUTION_STATE_MACHINE_CREATED,
    NEW_PLUGIN_MARKETPLACE_CREATED,
    NEW_SCHEDULER_CREATED,
    NEW_WORKFLOW_DATABASE_CREATED,
    NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN,
    NO_FLAG_DAY_DELETION,
    NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH,
    PROJECT_STEWARD_IS_NORMAL_WORKER,
    PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN,
    RAW_ARBITRARY_MUTATION_COMMAND_ACCEPTED,
    SECOND_FINALIZATION_PROTOCOL,
    SECOND_MUTATION_PROTOCOL,
    SECOND_RESULT_TRANSPORT_CREATED,
    SEMANTIC_RESIDUAL_MODEL_IMPLEMENTED,
    STEWARDSHIP_CAN_INFER_USER_APPROVAL,
    STEWARDSHIP_CAN_INVENT_ACCEPTED_FRONTIER,
    STEWARDSHIP_CAN_SET_USER_APPROVAL,
    STEWARDSHIP_IS_MUTATION_AUTHORITY,
    STEWARDSHIP_REPORT_BY_EXCEPTION,
    TRUSTED_STEWARD_FINALIZER_REUSED,
    GovernanceCheckpointKind,
    MaterializationRequest,
    SemanticFactSet,
    SemanticResidual,
    SemanticResidualKind,
    StewardshipCheckpoint,
    StewardshipDisposition,
    StewardshipError,
    TrustedManagedCommentFact,
    build_finalizer_input,
    build_materialization_intent,
    build_semantic_steward_handoff,
    evaluate_checkpoint,
    mechanical_proof_content,
    produce_deterministic_steward_result,
    run_governance_checkpoint,
)
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.materialization_receipt import MaterializationReceipt
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.milestone_review import ReviewCycle
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.steward_dispatch import (
    STEWARD_TASK_KIND_MODE_A,
    STEWARD_TASK_KIND_MODE_B,
    StewardClosureVerdict,
    StewardResult,
    build_mode_b_handoff,
)
from aota_forge.work_plane.steward_finalizer import (
    ClosurePhase,
    FinalizerInput,
    InMemoryGitHubPort,
    InMemoryGitPort,
    InMemoryReceiptStore,
    TrustedPlanIdentity,
    TrustedStewardFinalizer,
    TrustedUserGateState,
    make_trusted_binding_for_test,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STEWARDSHIP_MODULE = REPO_ROOT / "aota_forge" / "governance" / "stewardship.py"

PROJECT_ID = "aota_forge"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#57"
GOV_REPO = "wzjcccc-dotcom/aota-hermes-tools"
ISSUE = 57
MILESTONE = "M2"

W4_HOT_MODULES = (
    "aota_forge.work_plane.authorized_roots",
    "aota_forge.governance.project_store",
    "aota_forge.governance.sqlite_store",
)

BANNED_IMPORT_MODULES = {
    "sqlite3",
    "shelve",
    "dbm",
    "threading",
    "asyncio",
    "watchdog",
    "pickle",
    "celery",
    "kafka",
}

BANNED_IMPORT_PREFIXES = (
    "aota_reader",
    "chatgpt_hermes",
    "chatgpt-hermes",
    "aota_forge.adapters",
)


def _sha(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()  # 40 hex


def _tree(seed: str) -> str:
    return hashlib.sha256(f"tree:{seed}".encode("utf-8")).hexdigest()


def _digest_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


A_SHA = _sha("A-base")
B_SHA = _sha("B-reviewed")
A_TREE = _tree("A")
B_TREE = _tree("B")

COMMENT_ROLE = "development_notes"
COMMENT_ID = "102"
COMMENT_BODY = "governance body"


def _binding(project: str = PROJECT_ID):
    return make_trusted_binding_for_test(project)


def _plan(milestone: str = MILESTONE, mapping: dict[str, str] | None = None) -> TrustedPlanIdentity:
    if mapping is None:
        mapping = {COMMENT_ROLE: COMMENT_ID}
    return TrustedPlanIdentity(
        governing_repo=GOV_REPO,
        plan_issue_number=ISSUE,
        milestone_ref=milestone,
        plan_ref=PLAN_REF,
        managed_comments=dict(mapping),
    )


def _readiness(
    reviewed: str = B_SHA,
    *,
    ready: bool = True,
    milestone: str = MILESTONE,
    reasons: tuple[str, ...] = (),
) -> MilestoneClosureReadiness:
    return MilestoneClosureReadiness(
        milestone_ref=SemanticReference(ref=milestone),
        ready_for_project_steward=ready,
        final_review_cycle=ReviewCycle.RV1,
        reviewed_frontier_ref=SemanticReference(ref=reviewed),
        supporting_evidence_refs=("ev:review:RV1",),
        blocking_reasons=reasons,
    )


def _gate(satisfied: bool) -> TrustedUserGateState:
    return TrustedUserGateState(user_approval_satisfied=satisfied)


def _comment_fact(
    *,
    role: str = COMMENT_ROLE,
    comment_id: str = COMMENT_ID,
    digest: str | None = None,
    revision: str = "1",
) -> TrustedManagedCommentFact:
    return TrustedManagedCommentFact(
        role=role,
        comment_id=comment_id,
        digest=digest if digest is not None else _digest_body(COMMENT_BODY),
        revision=revision,
    )


def _checkpoint(
    *,
    kind: str = "MILESTONE_CLOSE",
    checkpoint_id: str = "cp-m2-close",
    milestone: str | None = MILESTONE,
    readiness: MilestoneClosureReadiness | None = None,
    phase: str | None = "REVIEWED_CLOSURE",
    gate: TrustedUserGateState | None = None,
    materialization: MaterializationRequest | None = None,
    semantic: SemanticFactSet | None = None,
    open_blockers: tuple[str, ...] = (),
    plan_authority_ref: str | None = None,
    all_milestones_closed: bool | None = None,
    comment_facts: tuple[TrustedManagedCommentFact, ...] = (),
) -> StewardshipCheckpoint:
    kwargs: dict = {
        "checkpoint_id": checkpoint_id,
        "kind": kind,
        "project_id": PROJECT_ID,
        "trusted_binding": _binding(),
        "trusted_plan": _plan(),
        "plan_id": "plan_wzjcccc_dotcom_aota_hermes_tools_57",
        "milestone_ref": milestone,
        "closure_phase": phase,
        "readiness": readiness,
        "user_gate": gate if gate is not None else _gate(False),
        "materialization": materialization or MaterializationRequest(),
        "semantic_facts": semantic or SemanticFactSet(),
        "open_blocker_refs": open_blockers,
        "plan_authority_ref": plan_authority_ref,
        "all_milestones_closed": all_milestones_closed,
        "managed_comment_facts": comment_facts,
    }
    return StewardshipCheckpoint(**kwargs)


def _plan_init(
    *,
    semantic: SemanticFactSet | None = None,
    plan_authority_ref: str | None = "plan-authority:wzjcccc-dotcom/aota-hermes-tools#57",
) -> StewardshipCheckpoint:
    return StewardshipCheckpoint(
        checkpoint_id="cp-plan-init",
        kind=GovernanceCheckpointKind.PLAN_INIT,
        project_id=PROJECT_ID,
        trusted_binding=_binding(),
        trusted_plan=_plan(),
        plan_id="plan_wzjcccc_dotcom_aota_hermes_tools_57",
        plan_authority_ref=plan_authority_ref,
        semantic_facts=semantic or SemanticFactSet(),
    )


def _plan_close(
    *,
    all_closed: bool | None = True,
    readiness: MilestoneClosureReadiness | None = None,
    phase: str | None = None,
    gate: TrustedUserGateState | None = None,
    materialization: MaterializationRequest | None = None,
    comment_facts: tuple[TrustedManagedCommentFact, ...] = (),
) -> StewardshipCheckpoint:
    return StewardshipCheckpoint(
        checkpoint_id="cp-plan-close",
        kind=GovernanceCheckpointKind.PLAN_CLOSE,
        project_id=PROJECT_ID,
        trusted_binding=_binding(),
        trusted_plan=_plan(),
        plan_id="plan_wzjcccc_dotcom_aota_hermes_tools_57",
        milestone_ref=MILESTONE,
        closure_phase=phase,
        readiness=readiness,
        user_gate=gate if gate is not None else _gate(False),
        materialization=materialization or MaterializationRequest(),
        all_milestones_closed=all_closed,
    )


def _github_port() -> InMemoryGitHubPort:
    gh = InMemoryGitHubPort()
    gh.seed(COMMENT_ID, COMMENT_BODY, revision="1")
    return gh


def _git_port() -> InMemoryGitPort:
    git = InMemoryGitPort()
    git.seed_repo(
        Path("/tmp/opencode/af57-w3-inmem-fixture"),
        branches={"main": A_SHA},
        commits={A_SHA: ([], A_TREE), B_SHA: ([A_SHA], B_TREE)},
        remotes={"origin": {"main": A_SHA}},
    )
    return git


def _finalizer(
    *,
    with_git: bool = False,
    with_github: bool = False,
    receipts: InMemoryReceiptStore | None = None,
) -> TrustedStewardFinalizer:
    return TrustedStewardFinalizer(
        git=_git_port() if with_git else InMemoryGitPort(),
        github=_github_port() if with_github else InMemoryGitHubPort(),
        repo_path=Path("/tmp/opencode/af57-w3-inmem-fixture"),
        receipt_store=receipts if receipts is not None else InMemoryReceiptStore(),
    )


class _DispatchSpy:
    """Records semantic dispatch calls (never an LLM invocation)."""

    def __init__(self) -> None:
        self.handoffs: list = []

    def __call__(self, handoff) -> None:
        self.handoffs.append(handoff)


# ---------------------------------------------------------------------------
# Architecture markers / surface guards
# ---------------------------------------------------------------------------


def test_architecture_markers() -> None:
    assert PROJECT_STEWARD_IS_NORMAL_WORKER is False
    assert PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN is True
    assert DETERMINISTIC_FIRST_GOVERNANCE is True
    assert CHECKPOINT_DOES_NOT_IMPLY_LLM_INVOCATION is True
    assert STEWARDSHIP_REPORT_BY_EXCEPTION is True
    assert NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN is False
    assert NO_FLAG_DAY_DELETION is True
    assert CURRENT_PROJECT_STEWARD_ROLE_REMAINS_MIGRATION_COMPATIBILITY is True
    assert STEWARDSHIP_CAN_INFER_USER_APPROVAL is False
    assert STEWARDSHIP_CAN_SET_USER_APPROVAL is False
    assert STEWARDSHIP_CAN_INVENT_ACCEPTED_FRONTIER is False
    assert STEWARDSHIP_IS_MUTATION_AUTHORITY is False
    assert TRUSTED_STEWARD_FINALIZER_REUSED is True
    assert FINALIZER_INPUT_IS_STEWARD_RESULT is True
    assert SECOND_FINALIZATION_PROTOCOL is False
    assert SECOND_MUTATION_PROTOCOL is False
    assert SECOND_RESULT_TRANSPORT_CREATED is False
    assert GENERIC_GOVERNANCE_WRITE_API is False
    assert RAW_ARBITRARY_MUTATION_COMMAND_ACCEPTED is False
    assert DETERMINISTIC_STEWARD_RESULT_PATH is True
    assert DETERMINISTIC_STEWARD_RESULT_IS_MECHANICALLY_DERIVED is True
    assert FAKE_STEWARD_RESULT is False
    assert FABRICATED_SEMANTIC_JUDGMENT is False
    assert DETERMINISTIC_PATH_INVOKES_LLM is False
    assert NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH is True
    assert LLM_PATH_REMAINS_AVAILABLE is True
    assert SEMANTIC_RESIDUAL_MODEL_IMPLEMENTED is True
    assert NEW_EVENT_BUS_CREATED is False
    assert NEW_WORKFLOW_DATABASE_CREATED is False
    assert NEW_SCHEDULER_CREATED is False
    assert NEW_PLUGIN_MARKETPLACE_CREATED is False
    assert NEW_EXECUTION_STATE_MACHINE_CREATED is False


def test_module_has_no_event_bus_or_database_and_no_w4_hot_dependency() -> None:
    source = STEWARDSHIP_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for imported in imports:
        assert imported.split(".")[0] not in BANNED_IMPORT_MODULES, imported
        assert not imported.startswith(BANNED_IMPORT_PREFIXES), imported
        assert not imported.startswith(W4_HOT_MODULES), imported
    # No second finalization protocol class in this module.
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert not any(name.endswith("Finalizer") for name in class_names)


def test_no_flag_day_legacy_project_steward_surface_preserved() -> None:
    assert AgentWorkRole.PROJECT_STEWARD.value == "project-steward"
    assert STEWARD_TASK_KIND_MODE_B == "milestone-closure-request"
    assert STEWARD_TASK_KIND_MODE_A == "project-state-request"
    assert StewardClosureVerdict.GOVERNANCE_SYNCED.value == "GOVERNANCE_SYNCED"
    # Existing Mode B gate still fails closed without readiness.
    readiness = _readiness(ready=False, reasons=("workflow not READY_FOR_STEWARD",))
    with pytest.raises(ValueError):
        build_mode_b_handoff(
            readiness=readiness,
            objective="x",
            bounded_scope="y",
        )


# ---------------------------------------------------------------------------
# V1 — typed checkpoint validation
# ---------------------------------------------------------------------------


def test_all_three_checkpoint_kinds_supported() -> None:
    assert CHECKPOINT_KINDS == {"PLAN_INIT", "MILESTONE_CLOSE", "PLAN_CLOSE"}
    assert _plan_init().kind is GovernanceCheckpointKind.PLAN_INIT
    assert _checkpoint(readiness=_readiness()).kind is GovernanceCheckpointKind.MILESTONE_CLOSE
    assert _plan_close().kind is GovernanceCheckpointKind.PLAN_CLOSE


def test_plan_init_rejects_readiness_and_closure_phase() -> None:
    with pytest.raises(ValueError):
        StewardshipCheckpoint(
            checkpoint_id="cp-bad",
            kind="PLAN_INIT",
            project_id=PROJECT_ID,
            trusted_binding=_binding(),
            trusted_plan=_plan(),
            readiness=_readiness(),
        )
    with pytest.raises(ValueError):
        StewardshipCheckpoint(
            checkpoint_id="cp-bad",
            kind="PLAN_INIT",
            project_id=PROJECT_ID,
            trusted_binding=_binding(),
            trusted_plan=_plan(),
            closure_phase="REVIEWED_CLOSURE",
        )


def test_milestone_close_requires_readiness_phase_and_milestone() -> None:
    base = {
        "checkpoint_id": "cp-bad",
        "kind": "MILESTONE_CLOSE",
        "project_id": PROJECT_ID,
        "trusted_binding": _binding(),
        "trusted_plan": _plan(),
    }
    with pytest.raises(ValueError):
        StewardshipCheckpoint(**base, closure_phase="REVIEWED_CLOSURE", milestone_ref=MILESTONE)
    with pytest.raises(ValueError):
        StewardshipCheckpoint(**base, readiness=_readiness(), milestone_ref=MILESTONE)
    with pytest.raises(ValueError):
        StewardshipCheckpoint(**base, readiness=_readiness(), closure_phase="REVIEWED_CLOSURE")


def test_plan_close_requires_all_milestones_closed_fact() -> None:
    with pytest.raises(ValueError):
        _plan_close(all_closed=None)


def test_binding_project_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        StewardshipCheckpoint(
            checkpoint_id="cp-bad",
            kind="PLAN_INIT",
            project_id=PROJECT_ID,
            trusted_binding=make_trusted_binding_for_test("other-project"),
            trusted_plan=_plan(),
        )


def test_materialization_request_is_bounded() -> None:
    with pytest.raises(ValueError):
        MaterializationRequest(managed_comment_roles=("not_a_managed_role",))
    with pytest.raises(ValueError):
        MaterializationRequest(managed_comment_roles=(COMMENT_ROLE, COMMENT_ROLE))
    with pytest.raises(ValueError):
        MaterializationRequest(branch="feature/x")
    req = MaterializationRequest(promote_to_main=True, expected_old_ref=A_SHA, remote="origin")
    assert req.wants_materialization() is True
    assert MaterializationRequest().wants_materialization() is False


def test_semantic_fact_refs_are_bounded_logical_refs() -> None:
    with pytest.raises(Exception):
        SemanticFactSet(unresolved_defect_refs=("/host/path/defect",))
    facts = SemanticFactSet(unresolved_defect_refs=("defect:AF57-D1", "defect:AF57-D2"))
    assert facts.unresolved_defect_refs == ("defect:AF57-D1", "defect:AF57-D2")
    assert facts.is_empty() is False


# ---------------------------------------------------------------------------
# V1 — deterministic no-residual classification
# ---------------------------------------------------------------------------


def test_plan_init_deterministic_satisfied_no_llm_no_report() -> None:
    cp = _plan_init()
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_SATISFIED
    assert ev.is_fully_deterministic is True
    assert ev.requires_semantic_steward is False
    assert ev.requires_task_main_report is False
    assert ev.residual is None
    spy = _DispatchSpy()
    outcome = run_governance_checkpoint(cp, semantic_dispatch=spy)
    assert outcome.disposition is StewardshipDisposition.DETERMINISTIC_SATISFIED
    assert outcome.receipt is None
    assert outcome.semantic_handoff is None
    assert outcome.requires_task_main_report is False
    assert outcome.is_normal_success is True
    assert outcome.llm_invoked is False
    assert spy.handoffs == []


def test_plan_init_missing_authority_blocked() -> None:
    cp = _plan_init(plan_authority_ref=None)
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_MISSING_PLAN_AUTHORITY in reason for reason in ev.blocking_reasons)
    assert ev.requires_task_main_report is True


def test_milestone_close_reviewed_reaches_trusted_finalizer_without_llm() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        materialization=MaterializationRequest(managed_comment_roles=(COMMENT_ROLE,)),
        comment_facts=(_comment_fact(),),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE
    spy = _DispatchSpy()
    receipts = InMemoryReceiptStore()
    finalizer = _finalizer(with_github=True, receipts=receipts)
    outcome = run_governance_checkpoint(cp, finalizer=finalizer, semantic_dispatch=spy)
    assert isinstance(outcome.receipt, MaterializationReceipt)
    assert outcome.receipt.final_status == "APPLIED"
    assert outcome.receipt.closure_phase == "REVIEWED_CLOSURE"
    assert outcome.requires_task_main_report is False
    assert outcome.is_normal_success is True
    assert outcome.llm_invoked is False
    assert outcome.semantic_handoff is None
    assert spy.handoffs == []
    assert isinstance(outcome.steward_result, StewardResult)
    assert outcome.steward_result.accepted_frontier_ref is None
    assert outcome.steward_result.milestone_ref == MILESTONE
    assert outcome.steward_result.reviewed_frontier_ref == B_SHA
    github_ops = [op for op in outcome.receipt.operations if op.kind == "github.update_managed_comment"]
    assert len(github_ops) == 1
    assert github_ops[0].status == "applied"


def test_milestone_close_accepted_gate_and_git_promotion() -> None:
    cp = _checkpoint(
        phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
        gate=_gate(True),
        materialization=MaterializationRequest(
            promote_to_main=True,
            expected_old_ref=A_SHA,
            expected_tree=B_TREE,
            remote="origin",
        ),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE
    finalizer = _finalizer(with_git=True)
    outcome = run_governance_checkpoint(cp, finalizer=finalizer)
    assert isinstance(outcome.receipt, MaterializationReceipt)
    assert outcome.receipt.final_status == "APPLIED"
    assert outcome.receipt.git_final_refs is not None
    assert outcome.receipt.git_final_refs["local"] == B_SHA
    assert outcome.receipt.git_final_refs["remote"] == B_SHA
    assert outcome.steward_result is not None
    assert outcome.steward_result.accepted_frontier_ref == B_SHA
    assert outcome.steward_result.user_approval_set is False
    assert outcome.requires_task_main_report is False
    assert outcome.llm_invoked is False


def test_plan_close_finalizable_with_readiness_and_receipt() -> None:
    cp = _plan_close(
        readiness=_readiness(),
        phase="ACCEPTED_CLOSURE",
        gate=_gate(True),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE
    outcome = run_governance_checkpoint(cp, finalizer=_finalizer())
    assert outcome.kind is GovernanceCheckpointKind.PLAN_CLOSE
    assert outcome.receipt is not None
    assert outcome.receipt.final_status == "APPLIED"
    assert outcome.steward_result is not None
    assert outcome.steward_result.accepted_frontier_ref == B_SHA


def test_plan_close_without_readiness_satisfied() -> None:
    cp = _plan_close()
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_SATISFIED
    assert ev.requires_task_main_report is False


def test_plan_close_without_readiness_but_materialization_blocked() -> None:
    cp = _plan_close(materialization=MaterializationRequest(promote_to_main=True, expected_old_ref=A_SHA))
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_MISSING_REQUIRED_FACTS in reason for reason in ev.blocking_reasons)


def test_plan_close_not_all_milestones_closed_blocked() -> None:
    cp = _plan_close(all_closed=False)
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_PLAN_MILESTONES_NOT_CLOSED in reason for reason in ev.blocking_reasons)


def test_readiness_not_ready_blocks_deterministically() -> None:
    cp = _checkpoint(
        readiness=_readiness(ready=False, reasons=("workflow not READY_FOR_STEWARD: is REPAIR_REQUIRED",)),
        phase="REVIEWED_CLOSURE",
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_READINESS_NOT_READY in reason for reason in ev.blocking_reasons)
    # No finalizer is required and none may be reached on a deterministic block.
    outcome = run_governance_checkpoint(cp)
    assert outcome.receipt is None
    assert outcome.requires_task_main_report is True


def test_milestone_identity_conflict_blocks() -> None:
    cp = _checkpoint(readiness=_readiness(milestone="M3"), phase="REVIEWED_CLOSURE")
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_MILESTONE_IDENTITY_MISMATCH in reason for reason in ev.blocking_reasons)


def test_open_blockers_block() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        open_blockers=("blocker:AF57-B1",),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_OPEN_BLOCKERS in reason for reason in ev.blocking_reasons)


def test_materialization_scope_requires_trusted_observations() -> None:
    # Promotion without the trusted observed branch ref for CAS.
    cp = _checkpoint(
        readiness=_readiness(),
        phase="ACCEPTED_CLOSURE",
        gate=_gate(True),
        materialization=MaterializationRequest(promote_to_main=True),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_MISSING_EXPECTED_OLD_REF in reason for reason in ev.blocking_reasons)

    # Comment role without trusted observed fact.
    cp2 = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        materialization=MaterializationRequest(managed_comment_roles=(COMMENT_ROLE,)),
    )
    ev2 = evaluate_checkpoint(cp2)
    assert any(BLOCK_MISSING_MANAGED_COMMENT_FACT in reason for reason in ev2.blocking_reasons)

    # Comment identity mismatch against the trusted Plan mapping.
    cp3 = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        materialization=MaterializationRequest(managed_comment_roles=(COMMENT_ROLE,)),
        comment_facts=(_comment_fact(comment_id="999"),),
    )
    ev3 = evaluate_checkpoint(cp3)
    assert any(BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH in reason for reason in ev3.blocking_reasons)


def test_non_commit_reviewed_frontier_cannot_be_promoted() -> None:
    cp = _checkpoint(
        readiness=_readiness(reviewed="reviewed-frontier-ref-not-a-sha"),
        phase="ACCEPTED_CLOSURE",
        gate=_gate(True),
        materialization=MaterializationRequest(promote_to_main=True, expected_old_ref=A_SHA),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_INVALID_FRONTIER_REF in reason for reason in ev.blocking_reasons)


def test_plan_init_materialization_scope_violation_blocked() -> None:
    cp = StewardshipCheckpoint(
        checkpoint_id="cp-plan-init",
        kind="PLAN_INIT",
        project_id=PROJECT_ID,
        trusted_binding=_binding(),
        trusted_plan=_plan(),
        plan_authority_ref="plan-authority:x",
        materialization=MaterializationRequest(promote_to_main=True, expected_old_ref=A_SHA),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED


def test_finalizer_required_on_finalizable_path() -> None:
    cp = _checkpoint(readiness=_readiness(), phase="REVIEWED_CLOSURE")
    with pytest.raises(StewardshipError) as excinfo:
        run_governance_checkpoint(cp)
    assert excinfo.value.code == "FINALIZER_REQUIRED"


# ---------------------------------------------------------------------------
# V1 — semantic residual classification and routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("facts", "expected_kind"),
    [
        (SemanticFactSet(ambiguous_governance_reason_refs=("reason:ambiguous-1",)), SemanticResidualKind.AMBIGUOUS_GOVERNANCE_REASON),
        (SemanticFactSet(architecture_question_refs=("architecture:q1",)), SemanticResidualKind.ARCHITECTURE_INTERPRETATION),
        (SemanticFactSet(unresolved_defect_refs=("defect:AF57-D1",)), SemanticResidualKind.UNRESOLVED_DEFECT_DISPOSITION),
        (SemanticFactSet(plan_change_question_refs=("decision:DC-1",)), SemanticResidualKind.DECISION_PLAN_CHANGE_INTERPRETATION),
        (SemanticFactSet(narrative_reconciliation_required=True), SemanticResidualKind.NARRATIVE_RECONCILIATION),
        (SemanticFactSet(conflicting_artifact_refs=("artifact:a", "artifact:b")), SemanticResidualKind.CONFLICTING_SEMANTIC_ARTIFACTS),
        (SemanticFactSet(review_evidence_refs=("review:RV1",)), SemanticResidualKind.REVIEW_EVIDENCE_INTERPRETATION),
        (SemanticFactSet(recorded_semantic_question_refs=("question:q1",)), SemanticResidualKind.RECORDED_SEMANTIC_QUESTION),
    ],
)
def test_semantic_residual_classification(facts: SemanticFactSet, expected_kind: SemanticResidualKind) -> None:
    cp = _checkpoint(readiness=_readiness(), phase="REVIEWED_CLOSURE", semantic=facts)
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED
    assert ev.residual is not None
    assert ev.residual.kind is expected_kind
    assert ev.requires_task_main_report is True
    assert ev.is_fully_deterministic is False
    assert len(ev.residual.reason) <= 512
    assert len(ev.residual.required_input_refs) <= 16


def test_semantic_residual_declared_kind_wins() -> None:
    facts = SemanticFactSet(
        unresolved_defect_refs=("defect:AF57-D1",),
        declared_kind=SemanticResidualKind.RECORDED_SEMANTIC_QUESTION,
        declared_reason="trusted governance store recorded an open semantic question",
    )
    cp = _checkpoint(readiness=_readiness(), phase="REVIEWED_CLOSURE", semantic=facts)
    ev = evaluate_checkpoint(cp)
    assert ev.residual is not None
    assert ev.residual.kind is SemanticResidualKind.RECORDED_SEMANTIC_QUESTION
    assert ev.residual.reason == "trusted governance store recorded an open semantic question"
    assert ev.residual.required_input_refs == ("defect:AF57-D1",)


def test_semantic_residual_routes_through_existing_mode_b_seam() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        semantic=SemanticFactSet(unresolved_defect_refs=("defect:AF57-D1",)),
    )
    spy = _DispatchSpy()
    outcome = run_governance_checkpoint(cp, semantic_dispatch=spy)
    assert outcome.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED
    assert outcome.receipt is None
    assert outcome.semantic_dispatch_invoked is True
    assert len(spy.handoffs) == 1
    handoff = spy.handoffs[0]
    assert handoff is outcome.semantic_handoff
    assert handoff.work_role is AgentWorkRole.PROJECT_STEWARD
    assert handoff.task_kind == STEWARD_TASK_KIND_MODE_B
    assert handoff.milestone_ref is not None and handoff.milestone_ref.ref == MILESTONE
    assert outcome.llm_invoked is False
    assert outcome.requires_task_main_report is True


def test_semantic_residual_without_ready_readiness_uses_mode_a() -> None:
    cp = _plan_init(semantic=SemanticFactSet(architecture_question_refs=("architecture:q1",)))
    outcome = run_governance_checkpoint(cp)
    assert outcome.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED
    assert outcome.semantic_handoff is not None
    assert outcome.semantic_handoff.task_kind == STEWARD_TASK_KIND_MODE_A
    assert outcome.semantic_handoff.work_role is AgentWorkRole.PROJECT_STEWARD


def test_build_semantic_steward_handoff_is_pure_existing_seam() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        semantic=SemanticFactSet(ambiguous_governance_reason_refs=("reason:ambiguous-1",)),
    )
    evaluation = evaluate_checkpoint(cp)
    residual = evaluation.residual
    assert residual is not None
    handoff = build_semantic_steward_handoff(cp, residual)
    assert handoff.work_role is AgentWorkRole.PROJECT_STEWARD
    assert handoff.task_kind == STEWARD_TASK_KIND_MODE_B
    assert handoff.project_ref is not None and handoff.project_ref.ref == f"project:{PROJECT_ID}"
    other_checkpoint_residual = SemanticResidual(
        kind=SemanticResidualKind.AMBIGUOUS_GOVERNANCE_REASON,
        reason="other checkpoint residual",
        checkpoint_id="other-checkpoint",
    )
    with pytest.raises(ValueError):
        build_semantic_steward_handoff(cp, other_checkpoint_residual)


def test_semantic_residual_bounded_context_uses_w2_route_refs() -> None:
    from aota_forge.governance.context_route import build_context_route, ContextRouteInput

    route = build_context_route(ContextRouteInput(project_id=PROJECT_ID))
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        semantic=SemanticFactSet(unresolved_defect_refs=("defect:AF57-D1", "defect:AF57-D2")),
    )
    outcome = run_governance_checkpoint(cp, context_route=route)
    assert outcome.context_route_projection_id == route.projection_id()
    assert outcome.evaluation.residual is not None
    assert outcome.evaluation.residual.required_input_refs == ("defect:AF57-D1", "defect:AF57-D2")


def test_deterministic_paths_never_dispatch_and_never_handoff() -> None:
    spy = _DispatchSpy()
    finalizable = _checkpoint(readiness=_readiness(), phase="REVIEWED_CLOSURE")
    outcome = run_governance_checkpoint(finalizable, finalizer=_finalizer(), semantic_dispatch=spy)
    assert outcome.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE
    assert outcome.semantic_handoff is None
    assert spy.handoffs == []

    satisfied_outcome = run_governance_checkpoint(_plan_init(), semantic_dispatch=spy)
    assert satisfied_outcome.disposition is StewardshipDisposition.DETERMINISTIC_SATISFIED
    assert spy.handoffs == []

    blocked = _checkpoint(
        readiness=_readiness(ready=False, reasons=("workflow not READY_FOR_STEWARD",)),
        phase="REVIEWED_CLOSURE",
    )
    blocked_outcome = run_governance_checkpoint(blocked, semantic_dispatch=spy)
    assert blocked_outcome.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert spy.handoffs == []


# ---------------------------------------------------------------------------
# V1 — user gate / frontier invariants
# ---------------------------------------------------------------------------


def test_user_approval_cannot_be_inferred_on_accepted_close() -> None:
    cp = _checkpoint(
        phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
        gate=_gate(False),
    )
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED
    assert any(BLOCK_USER_GATE_REQUIRED in reason for reason in ev.blocking_reasons)
    # No finalizer, no handoff, no fabricated approval; caller sees exception.
    outcome = run_governance_checkpoint(cp)
    assert outcome.receipt is None
    assert outcome.requires_task_main_report is True


def test_default_user_gate_is_fail_closed() -> None:
    cp = StewardshipCheckpoint(
        checkpoint_id="cp-default-gate",
        kind="MILESTONE_CLOSE",
        project_id=PROJECT_ID,
        trusted_binding=_binding(),
        trusted_plan=_plan(),
        milestone_ref=MILESTONE,
        closure_phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
    )
    assert cp.user_gate.user_approval_satisfied is False
    ev = evaluate_checkpoint(cp)
    assert ev.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED


def test_produced_result_never_sets_user_approval_and_passes_gate_fact_through() -> None:
    gate = _gate(True)
    cp = _checkpoint(
        phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
        gate=gate,
    )
    result = produce_deterministic_steward_result(cp)
    assert result.user_approval_set is False
    finalizer_input = build_finalizer_input(cp)
    assert finalizer_input.user_gate is gate  # observed fact passed through unchanged
    assert isinstance(finalizer_input, FinalizerInput)
    assert isinstance(finalizer_input.steward_result, StewardResult)


def test_accepted_frontier_cannot_be_invented() -> None:
    # No checkpoint field can carry an independently claimed accepted frontier:
    cp = _checkpoint(phase="ACCEPTED_CLOSURE", readiness=_readiness(), gate=_gate(True))
    result = produce_deterministic_steward_result(cp)
    assert result.accepted_frontier_ref == result.reviewed_frontier_ref == B_SHA
    reviewed_only = _checkpoint(phase="REVIEWED_CLOSURE", readiness=_readiness())
    reviewed_result = produce_deterministic_steward_result(reviewed_only)
    assert reviewed_result.accepted_frontier_ref is None


def test_produced_evidence_refs_are_mechanical_only() -> None:
    cp = _checkpoint(
        phase="REVIEWED_CLOSURE",
        readiness=_readiness(),
        materialization=MaterializationRequest(managed_comment_roles=(COMMENT_ROLE,)),
        comment_facts=(_comment_fact(),),
    )
    result = produce_deterministic_steward_result(cp)
    for ref in result.governance_evidence_refs:
        assert ref.startswith(("checkpoint:", "readiness:", "stewardship-proof:")) or ref == "plan-authority:x"
    content = mechanical_proof_content(cp)
    assert '"mechanical":true' in content
    assert '"verdict":"GOVERNANCE_SYNCED"' in content
    assert len(content) <= 8192


def test_fabricated_semantic_judgment_not_possible_on_residual_path() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        semantic=SemanticFactSet(plan_change_question_refs=("decision:DC-1",)),
    )
    with pytest.raises(StewardshipError) as excinfo:
        produce_deterministic_steward_result(cp)
    assert excinfo.value.code == "NOT_DETERMINISTICALLY_FINALIZABLE"


# ---------------------------------------------------------------------------
# V1 — reproducibility / idempotency
# ---------------------------------------------------------------------------


def test_deterministic_evaluation_and_production_reproducible() -> None:
    cp = _checkpoint(
        phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
        gate=_gate(True),
        materialization=MaterializationRequest(
            promote_to_main=True,
            expected_old_ref=A_SHA,
            remote="origin",
            managed_comment_roles=(COMMENT_ROLE,),
        ),
        comment_facts=(_comment_fact(),),
    )
    ev_a = evaluate_checkpoint(cp)
    ev_b = evaluate_checkpoint(cp)
    assert ev_a.canonical_payload() == ev_b.canonical_payload()
    assert ev_a.evaluation_id() == ev_b.evaluation_id()

    result_a = produce_deterministic_steward_result(cp)
    result_b = produce_deterministic_steward_result(cp)
    assert result_a.to_dict() == result_b.to_dict()

    intent_a = build_materialization_intent(cp)
    intent_b = build_materialization_intent(cp)
    assert intent_a.scope_dict() == intent_b.scope_dict()


def test_finalization_idempotent_replay_on_same_finalizer() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        materialization=MaterializationRequest(managed_comment_roles=(COMMENT_ROLE,)),
        comment_facts=(_comment_fact(),),
    )
    receipts = InMemoryReceiptStore()
    finalizer = _finalizer(with_github=True, receipts=receipts)
    first = run_governance_checkpoint(cp, finalizer=finalizer)
    second = run_governance_checkpoint(cp, finalizer=finalizer)
    assert first.receipt is not None and second.receipt is not None
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert first.receipt.receipt_digest == second.receipt.receipt_digest
    assert finalizer.github.update_call_count == 1


# ---------------------------------------------------------------------------
# V2 — deterministic public path to existing trusted finalization
# ---------------------------------------------------------------------------


def test_v2_deterministic_path_reaches_existing_finalizer() -> None:
    cp = _checkpoint(
        phase="ACCEPTED_CLOSURE",
        readiness=_readiness(),
        gate=_gate(True),
        materialization=MaterializationRequest(
            promote_to_main=True,
            expected_old_ref=A_SHA,
            expected_tree=B_TREE,
            remote="origin",
            managed_comment_roles=(COMMENT_ROLE,),
        ),
        comment_facts=(_comment_fact(),),
    )
    # 1. checkpoint -> deterministic producer -> valid StewardResult
    steward_result = produce_deterministic_steward_result(cp)
    assert isinstance(steward_result, StewardResult)
    assert steward_result.verdict is StewardClosureVerdict.GOVERNANCE_SYNCED
    # 2. existing FinalizerInput contract: input is the StewardResult
    finalizer_input = build_finalizer_input(cp)
    assert finalizer_input.steward_result is not None
    assert finalizer_input.steward_result.to_dict() == steward_result.to_dict()
    # 3. existing TrustedStewardFinalizer materializes (no LLM anywhere)
    finalizer = _finalizer(with_git=True, with_github=True)
    receipt = finalizer.finalize(
        finalizer_input,
        caller_role="coordinator-internal",
        via_coordinator=True,
        has_closure_evidence=True,
    )
    assert isinstance(receipt, MaterializationReceipt)
    assert receipt.final_status == "APPLIED"
    assert receipt.git_final_refs is not None
    assert receipt.git_final_refs["local"] == B_SHA
    assert receipt.git_final_refs["remote"] == B_SHA
    assert receipt.github_evidence is not None
    assert COMMENT_ROLE in receipt.github_evidence


def test_v2_semantic_residual_to_existing_steward_result_contract() -> None:
    cp = _checkpoint(
        readiness=_readiness(),
        phase="REVIEWED_CLOSURE",
        semantic=SemanticFactSet(review_evidence_refs=("review:RV1",)),
    )
    spy = _DispatchSpy()
    outcome = run_governance_checkpoint(cp, semantic_dispatch=spy)
    assert outcome.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED
    handoff = outcome.semantic_handoff
    assert handoff is not None and handoff.task_kind == STEWARD_TASK_KIND_MODE_B
    # Existing bounded Project Steward path returns the existing StewardResult
    # contract (simulated here; no real LLM invocation).
    semantic_result = StewardResult(
        milestone_ref=MILESTONE,
        reviewed_frontier_ref=B_SHA,
        verdict=StewardClosureVerdict.GOVERNANCE_SYNCED,
        governance_evidence_refs=("review:RV1",),
    )
    finalizer_input = FinalizerInput(
        steward_result=semantic_result,
        readiness=cp.readiness,  # type: ignore[arg-type]
        trusted_binding=cp.trusted_binding,
        trusted_plan=cp.trusted_plan,
        user_gate=cp.user_gate,
        intent=build_materialization_intent(cp),
        closure_phase=ClosurePhase.REVIEWED_CLOSURE,
    )
    receipt = _finalizer().finalize(
        finalizer_input,
        caller_role="coordinator-internal",
        via_coordinator=True,
        has_closure_evidence=True,
    )
    assert receipt.steward_digest != ""
    assert receipt.final_status == "APPLIED"
    assert outcome.llm_invoked is False


def test_v2_single_finalization_protocol_only() -> None:
    import aota_forge.governance.stewardship as stewardship_module
    import aota_forge.work_plane.steward_finalizer as finalizer_module

    assert stewardship_module.TrustedStewardFinalizer is finalizer_module.TrustedStewardFinalizer
    assert SECOND_FINALIZATION_PROTOCOL is False
    assert TRUSTED_STEWARD_FINALIZER_REUSED is True
    assert FINALIZER_INPUT_IS_STEWARD_RESULT is True
    assert NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH is True
    assert LLM_PATH_REMAINS_AVAILABLE is True
