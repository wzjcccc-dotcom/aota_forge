"""Thin trusted host bootstrap for Hermes aota-task-main MCP (generic).

Generic derivation creates TrustedTaskMainRuntimeContext from trusted
bootstrap without injecting an in-process object. This module is the
thinnest operator-controlled seam that does so.

Trusted channel: the Hermes MCP child inherits a *single* host-controlled
filesystem reference (AOTA_W3_MCP_ROOT) that the operator set before the
Hermes session was launched.  The child resolves

    <root>/.aota/task-main-bootstrap.json

via that trusted root and deterministically rebuilds every typed object
from that file plus the durable file-backed stores it points to.  No
model-facing aota.invoke argument may supply project, worktree, store
location, plan authority, session identity, resolver, or approval truth.
The file is operator-owned; the MCP child never consults CWD or an
arbitrary model-supplied path.

Only operator/runtime-controlled configuration or durable state references
are consumed (store paths, plan authority/digest, session ref, runtime
config path, resolver factories that are code, not data).  No serialized
authority blob from the model is honored.

If the bootstrap file is absent the caller is not a task-main MCP child;
the normal worker binding path is used.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.composition.task_main import create_task_main_control_service
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    TrustedTaskMainRuntimeContext,
    TrustedWorkerBinding,
    create_task_main_envelope,
    verify_envelope,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_THIN
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.handoff_runtime import (
    WorkSemanticProjection,
    WorkScopeInsufficientError,
    generic_fallback_scope_template,
    generic_fallback_task_kind,
    parse_work_semantics_table,
    resolve_bounded_work_handoff,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.progression import FocusedValidationEvidence, FocusedValidationVerdict
from aota_forge.runtime.task_main.reconciliation import (
    COMPLETION_KIND_REVIEW,
    DISPOSITION_REVIEW_READY_FOR_STEWARD,
    CompletionReconciliationReceipt,
    GovernedReviewEvidence,
    GovernedWorkItemEvidence,
)
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    MaterializationRequest,
    SemanticFactSet,
    StewardshipCheckpoint,
    build_semantic_steward_handoff,
    evaluate_checkpoint,
)
from aota_forge.work_plane.steward_finalizer import (
    ClosurePhase,
    TrustedPlanIdentity,
    TrustedProjectBinding,
    TrustedUserGateState,
)
from aota_forge.work_plane.github_tools import parse_plan_ref
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.composition.project_binding import (
    resolve_trusted_project_binding,
    resolve_trusted_project_evidence,
)
from aota_forge.composition.completion_evidence import (
    create_automatic_governed_evidence_resolver,
    derive_governed_review_evidence,
)

BOOTSTRAP_ENV_ROOT = "AOTA_W3_MCP_ROOT"
BOOTSTRAP_RELPATH = ".aota/task-main-bootstrap.json"
# Also accept explicit path for testing harness
BOOTSTRAP_EXPLICIT_ENV = "AOTA_TASK_MAIN_BOOTSTRAP"

# M1/W1-R1 task-main-owned Work projection authority (AF #45 repair).
# Production normal path: task-main planning layer commits a bounded
# WorkSemanticProjection to the existing durable coordinator store; the
# production handoff_resolver below transports that durable projection into
# the existing TaskHandoff. The operator-owned bootstrap work_semantics table
# is retained ONLY as test/bootstrap compatibility (never production
# authority, never the normal path). Launcher prepare(work_semantics=...)
# is therefore not required for normal production operation, no operator
# refresh is required between Work Items, and restart never requires
# operator semantic reinjection.
TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION = True
TASK_MAIN_SEMANTIC_LAYER_PRODUCES_WORK_PROJECTION = True
WORK_PROJECTION_DURABLE = True
WORK_PROJECTION_BOUND_TO_TRUSTED_PLAN_IDENTITY = True
WORK_PROJECTION_BOUND_TO_WORK_ITEM = True
WORK_SEMANTIC_PROJECTION_IS_PLAN_AUTHORITY = False
PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED = False
OPERATOR_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
CODEX_WORK_SEMANTICS_REQUIRED_FOR_NORMAL_PATH = False
MANUAL_PER_WORK_SCOPE_INJECTION_REQUIRED = False
OPERATOR_REFRESH_REQUIRED_BETWEEN_WORK_ITEMS = False
TASK_MAIN_RESTART_REQUIRES_OPERATOR_WORK_SEMANTICS_REINJECTION = False
WORK_PROJECTION_SOURCE_AFTER = "task-main-owned-durable-coordinator"

# AF #49 M1/W6 parent-session identity boundary (I49-B002). The trusted
# task-main dispatcher is constructed from the bootstrap origin exactly as
# before; the mechanical unbound-origin gate lives in the ExecutionDispatcher
# and the task-main coordinator activation seam, so a pre-session placeholder
# can never create a durable child execution record or a placeholder-bound
# coordinator. The bootstrap origin itself remains bind-once and immutable.
ORIGIN_SESSION_BIND_ONCE = True
MUTABLE_ORIGIN_REWRITE = False
UNBOUND_ORIGIN_CAN_CREATE_CHILD_EXECUTION = False
PENDING_PLACEHOLDER_IS_DURABLE_COMPLETION_AUTHORITY = False
PENDING_PLACEHOLDER_MAY_ENTER_DURABLE_CHILD_RECORD = False
TASK_MAIN_SESSION_BINDING_IS_REAL_EXACT_IDENTITY = True


def _project_evidence(
    root: Path,
    project_id: str,
    *,
    source_repository: str | None = None,
    registry_path: Path | None = None,
) -> ProjectResolutionEvidence:
    """Generic trusted project evidence via canonical resolver.

    Derives ProjectResolutionEvidence from trusted workspace root (worktree)
    + canonical .aota/project.yaml discovery + exact trusted project_id.
    No synthetic fingerprints, no M3 fixture authority, no project_id if/elif
    branching, no dogfood literal.

    Reuses canonical scan_projects / fingerprint_registry via
    resolve_trusted_project_evidence (single shared helper).
    Fail-closed: unknown / ambiguous / invalid remains not RESOLVED.

    AF #55 M1/W4: when the operator-owned bootstrap declares a
    ``source_repository`` (Plan SOURCE_REPOSITORY) and/or a workspace
    ``registry_path``, resolution goes through the canonical registry-backed
    trusted binding with mechanical Git origin verification. No cwd/folder
    inference, no synthetic fallback on that path.
    """
    if (source_repository is not None and str(source_repository).strip()) or registry_path is not None:
        binding = resolve_trusted_project_binding(
            project_id=project_id,
            source_repository=source_repository,
            registry_path=registry_path,
        )
        return binding.resolution
    return resolve_trusted_project_evidence(
        worktree_root=root,
        project_id=project_id,
    )


def _neutral_envelope(milestone: str) -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(milestone_ref=milestone, default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.STANDARD)


def _view_from_dict(d: dict[str, Any]) -> MilestonePlanView:
    g = d["graph"]
    graph = MilestoneWorkItemGraph(
        milestone_ref=g["milestone_ref"],
        work_items=list(g["work_items"]),
        dependencies=[list(e) for e in g.get("dependencies", [])],
    )
    # M3/W1-R1 F2: restore bounded governed Work semantics (if present).
    # Legacy files without the key stay permissive (empty) for backward compat.
    ws_raw = d.get("work_semantics", ())
    ws_views: list[Any] = []
    if isinstance(ws_raw, (list, tuple)):
        try:
            from aota_forge.core.plan.read_model import GovernedWorkSemanticView
        except Exception:
            GovernedWorkSemanticView = None  # type: ignore
        for entry in ws_raw:
            try:
                if GovernedWorkSemanticView is not None and isinstance(entry, dict):
                    ws_views.append(GovernedWorkSemanticView.from_dict(entry))
            except Exception:
                raise TrustedBindingError(f"bootstrap work_semantics entry invalid: {entry!r}")
    # W4: restore bounded faithful Work source slices (structural).
    wss_raw = d.get("work_source_slices", ())
    wss_views: list[Any] = []
    if isinstance(wss_raw, (list, tuple)):
        try:
            from aota_forge.core.plan.read_model import WorkSourceSlice
        except Exception:
            WorkSourceSlice = None  # type: ignore
        for entry in wss_raw:
            try:
                if WorkSourceSlice is not None and isinstance(entry, dict):
                    wss_views.append(WorkSourceSlice.from_dict(entry))
            except Exception:
                raise TrustedBindingError(f"bootstrap work_source_slices entry invalid: {entry!r}")
    return MilestonePlanView(
        plan_authority=d["plan_authority"],
        plan_digest=d["plan_digest"],
        plan_source_revision=d.get("plan_source_revision"),
        milestone_id=d["milestone_id"],
        entry_base=d["entry_base"],
        graph=graph,
        milestone_user_approval_satisfied=bool(d["milestone_user_approval_satisfied"]),
        plan_amendment_required=bool(d.get("plan_amendment_required", False)),
        work_semantics=tuple(ws_views),
        work_source_slices=tuple(wss_views),
    )


def _semantic_facts_from_bootstrap(data: Mapping[str, Any]) -> SemanticFactSet:
    """Rebuild optional semantic residual facts from the operator bootstrap."""
    raw = data.get("stewardship_semantic_facts")
    if raw is None:
        return SemanticFactSet()
    if not isinstance(raw, Mapping):
        raise TrustedBindingError("bootstrap stewardship_semantic_facts must be a mapping")
    allowed = {
        "unresolved_defect_refs",
        "conflicting_artifact_refs",
        "ambiguous_governance_reason_refs",
        "architecture_question_refs",
        "plan_change_question_refs",
        "review_evidence_refs",
        "narrative_reconciliation_required",
        "recorded_semantic_question_refs",
        "declared_kind",
        "declared_reason",
    }
    extra = set(raw) - allowed
    if extra:
        raise TrustedBindingError(
            f"bootstrap stewardship_semantic_facts has unknown field(s): {sorted(extra)}"
        )
    kwargs: dict[str, Any] = {}
    for field in sorted(allowed - {"narrative_reconciliation_required", "declared_kind", "declared_reason"}):
        value = raw.get(field, ())
        if value is None:
            value = ()
        if not isinstance(value, (tuple, list)):
            raise TrustedBindingError(
                f"bootstrap stewardship_semantic_facts.{field} must be a tuple/list"
            )
        kwargs[field] = tuple(value)
    kwargs["narrative_reconciliation_required"] = raw.get(
        "narrative_reconciliation_required", False
    )
    kwargs["declared_kind"] = raw.get("declared_kind")
    kwargs["declared_reason"] = raw.get("declared_reason")
    try:
        return SemanticFactSet(**kwargs)
    except Exception as exc:
        raise TrustedBindingError(
            f"bootstrap stewardship_semantic_facts invalid: {exc}"
        ) from exc


def _trusted_plan_identity_from_bootstrap(
    live_view: MilestonePlanView,
    data: Mapping[str, Any],
) -> TrustedPlanIdentity:
    raw_binding = data.get("plan_authority_binding")
    plan_binding: PlanAuthorityBinding | None = None
    if raw_binding is not None:
        try:
            plan_binding = PlanAuthorityBinding.from_dict(raw_binding)
        except Exception as exc:
            raise TrustedBindingError(
                f"bootstrap Plan authority binding invalid: {exc}"
            ) from exc
        raw_plan_id = data.get("plan_id")
        if raw_plan_id is not None and raw_plan_id != plan_binding.plan_id:
            raise TrustedBindingError(
                "bootstrap Plan authority binding does not match plan_id"
            )
        if plan_binding.authority_ref != live_view.plan_authority:
            raise TrustedBindingError(
                "bootstrap Plan authority binding does not match the live Plan view"
            )
        if plan_binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            from aota_forge.adapters.plan_authority.local_governance import (
                local_plan_authority_reference,
            )

            expected_ref = local_plan_authority_reference(
                str(data.get("project_id") or ""),
                plan_binding.plan_id,
            )
            if plan_binding.authority_ref != expected_ref:
                raise TrustedBindingError(
                    "bootstrap local Plan authority does not match the trusted project/Plan identity"
                )
            governing_repo = None
            issue_number = None
        else:
            try:
                governing_repo, _owner, issue_number = parse_plan_ref(plan_binding.authority_ref)
            except Exception as exc:
                raise TrustedBindingError(
                    f"bootstrap GitHub Plan authority is not a canonical Plan reference: {exc}"
                ) from exc
    else:
        try:
            governing_repo, _owner, issue_number = parse_plan_ref(live_view.plan_authority)
        except Exception as exc:
            raise TrustedBindingError(
                f"bootstrap live Plan authority is not a canonical Plan reference: {exc}"
            ) from exc
        raw_plan_id = data.get("plan_id")
        if raw_plan_id is not None:
            try:
                plan_binding = PlanAuthorityBinding(
                    plan_id=raw_plan_id,
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref=live_view.plan_authority,
                )
            except Exception as exc:
                raise TrustedBindingError(
                    f"bootstrap GitHub Plan authority binding invalid: {exc}"
                ) from exc
    raw_comments = data.get("managed_comments", {})
    if raw_comments is None:
        raw_comments = {}
    if not isinstance(raw_comments, Mapping):
        raise TrustedBindingError("bootstrap managed_comments must be a mapping")
    try:
        return TrustedPlanIdentity(
            governing_repo=governing_repo,
            plan_issue_number=issue_number,
            milestone_ref=live_view.milestone_id,
            plan_ref=live_view.plan_authority,
            managed_comments=dict(raw_comments),
            authority_binding=plan_binding,
        )
    except Exception as exc:
        raise TrustedBindingError(
            f"bootstrap trusted Plan identity invalid: {exc}"
        ) from exc


def _readiness_from_durable_state(
    state: Any,
    *,
    coordinator_id: str,
    live_view: MilestonePlanView,
) -> MilestoneClosureReadiness:
    """Rebuild authority-negative closure readiness from durable review evidence."""
    if state is None:
        raise TrustedBindingError("stewardship checkpoint requires an existing coordinator state")
    if state.coordinator_id != coordinator_id:
        raise TrustedBindingError("coordinator state identity does not match the trusted runner")
    review_receipts: list[CompletionReconciliationReceipt] = []
    for key, raw in sorted(state.reconciled_completions.items()):
        try:
            receipt = CompletionReconciliationReceipt.from_dict(raw)
        except Exception as exc:
            raise TrustedBindingError(
                f"durable reconciliation receipt {key!r} is invalid: {exc}"
            ) from exc
        if receipt.coordinator_id != state.coordinator_id:
            raise TrustedBindingError("durable reconciliation receipt has a foreign coordinator")
        if receipt.plan_authority != live_view.plan_authority:
            raise TrustedBindingError("durable reconciliation receipt has a foreign Plan authority")
        if receipt.milestone_id != live_view.milestone_id:
            raise TrustedBindingError("durable reconciliation receipt has a foreign Milestone")
        if receipt.completion_kind == COMPLETION_KIND_REVIEW:
            review_receipts.append(receipt)
    if not review_receipts:
        raise TrustedBindingError(
            "closure-ready runner outcome has no durable milestone review receipt"
        )
    receipt = max(
        review_receipts,
        key=lambda item: (item.next_coordinator_revision, item.canonical_task_id),
    )
    if receipt.progression_disposition != DISPOSITION_REVIEW_READY_FOR_STEWARD:
        raise TrustedBindingError(
            "latest durable milestone review receipt is not ready for Project Steward"
        )
    if receipt.governed_evidence.get("closure_ready") is not True:
        raise TrustedBindingError(
            "latest durable milestone review receipt does not prove closure readiness"
        )
    raw_review = receipt.governed_evidence.get("review_evidence")
    try:
        review = MilestoneReviewEvidence.from_dict(raw_review)
    except Exception as exc:
        raise TrustedBindingError(
            f"durable milestone review evidence is invalid: {exc}"
        ) from exc
    if review.milestone_ref.ref != live_view.milestone_id:
        raise TrustedBindingError("durable review evidence has a foreign Milestone")
    if review.review_result_ref.ref != receipt.result_handoff_ref:
        raise TrustedBindingError("durable review result ref disagrees with reconciliation receipt")
    if review.review_result_digest != receipt.card_digest:
        raise TrustedBindingError("durable review result digest disagrees with reconciliation receipt")
    return MilestoneClosureReadiness(
        milestone_ref=review.milestone_ref,
        ready_for_project_steward=True,
        final_review_cycle=review.review_cycle,
        reviewed_frontier_ref=review.reviewed_frontier_ref,
        supporting_evidence_refs=(
            receipt.receipt_digest,
            receipt.result_handoff_ref,
            review.digest,
        ),
        blocking_reasons=(),
    )


def _build_stewardship_checkpoint(
    *,
    runner_outcome: Any,
    state: Any,
    live_view: MilestonePlanView,
    sandbox: Any,
    trusted_plan: TrustedPlanIdentity,
    semantic_facts: SemanticFactSet,
    plan_id: str | None,
    all_milestones_closed: bool | None = None,
) -> StewardshipCheckpoint:
    if getattr(runner_outcome, "milestone_closure_ready", False) is not True:
        raise TrustedBindingError(
            "stewardship checkpoint requested before a trusted closure-ready outcome"
        )
    if state.project_id != sandbox.project_id:
        raise TrustedBindingError("coordinator project does not match the trusted sandbox")
    if state.plan_authority != live_view.plan_authority or state.plan_digest != live_view.plan_digest:
        raise TrustedBindingError("coordinator Plan identity does not match the trusted Plan view")
    if state.milestone_id != live_view.milestone_id:
        raise TrustedBindingError("coordinator Milestone does not match the trusted Plan view")
    readiness = _readiness_from_durable_state(
        state,
        coordinator_id=state.coordinator_id,
        live_view=live_view,
    )
    identity_material = ":".join(
        (
            state.coordinator_id,
            str(state.coordinator_revision),
            state.revision_token,
            readiness.digest,
            repr(semantic_facts),
        )
    )
    checkpoint_id = f"stewardship:{hashlib.sha256(identity_material.encode('utf-8')).hexdigest()}"
    # The canonical Plan projection supplies the next trusted Milestone view;
    # absence plus the durable user gate means this is an accepted final Plan
    # closure. No model or runner text can assert the Plan-close fact.
    next_milestone_view_is_none = (
        all_milestones_closed is True
        and plan_id is not None
        and state.user_approval_satisfied is True
    )
    checkpoint_kind = (
        GovernanceCheckpointKind.PLAN_CLOSE
        if next_milestone_view_is_none
        else GovernanceCheckpointKind.MILESTONE_CLOSE
    )
    return StewardshipCheckpoint(
        checkpoint_id=checkpoint_id,
        kind=checkpoint_kind,
        project_id=state.project_id,
        trusted_binding=TrustedProjectBinding(
            project_id=sandbox.project_id,
            binding_ref=f"worktree:{sandbox.worktree_id}",
            binding_digest=sandbox.digest,
        ),
        trusted_plan=trusted_plan,
        plan_id=plan_id,
        milestone_ref=state.milestone_id,
        closure_phase=(
            ClosurePhase.ACCEPTED_CLOSURE
            if next_milestone_view_is_none
            else ClosurePhase.REVIEWED_CLOSURE
        ),
        readiness=readiness,
        user_gate=TrustedUserGateState(
            user_approval_satisfied=state.user_approval_satisfied,
        ),
        materialization=MaterializationRequest(),
        semantic_facts=semantic_facts,
        open_blocker_refs=state.open_blockers,
        plan_authority_ref=state.plan_authority,
        all_milestones_closed=True if next_milestone_view_is_none else None,
    )


def _canonical_steward_handoff(checkpoint: StewardshipCheckpoint) -> TaskHandoff:
    evaluation = evaluate_checkpoint(checkpoint)
    if evaluation.residual is None:
        raise RuntimeError("semantic Steward dispatch requires a typed semantic residual")
    return build_semantic_steward_handoff(checkpoint, evaluation.residual)


def _steward_task_id(checkpoint: StewardshipCheckpoint, handoff: TaskHandoff) -> str:
    if checkpoint.plan_id is None:
        raise RuntimeError("durable Steward task identity requires the trusted internal Plan ID")
    task_id_material = ":".join(
        (checkpoint.project_id, checkpoint.plan_id, checkpoint.checkpoint_id, handoff.handoff_digest)
    )
    return f"steward:{hashlib.sha256(task_id_material.encode('utf-8')).hexdigest()}"


def _production_steward_dispatch_factory(
    *,
    dispatcher: Any,
    sandbox: Any,
    plan_id: str | None,
):
    """Build the legacy semantic dispatch through the existing task-start seam."""
    def factory(checkpoint: StewardshipCheckpoint):
        from aota_forge.work_plane.task_facade import task_start
        from aota_forge.work_plane.handoff_store import HANDOFF_CONTROL_FIELDS, handoff_write

        if checkpoint.plan_id is None or checkpoint.plan_id != plan_id:
            raise RuntimeError("semantic Steward dispatch requires the trusted internal Plan ID")

        def semantic_payload(value: TaskHandoff) -> dict[str, Any]:
            # TaskHandoff is semantic, but the durable handoff store reserves
            # overlapping names such as plan_ref for its control envelope.
            return {
                key: item
                for key, item in value.to_dict().items()
                if key not in HANDOFF_CONTROL_FIELDS
            }

        def dispatch(received_handoff: TaskHandoff):
            # Deterministic closure never calls this seam. Defer residual-only
            # handoff construction until semantic dispatch is actually needed.
            handoff = _canonical_steward_handoff(checkpoint)
            task_id = _steward_task_id(checkpoint, handoff)
            if received_handoff.handoff_digest != handoff.handoff_digest:
                raise RuntimeError("semantic Steward handoff diverged from the trusted checkpoint")
            handoff_ref = handoff_write(
                mode="work_item",
                semantic=semantic_payload(received_handoff),
                caller_role="task-main",
                sandbox=sandbox,
                plan_ref=checkpoint.trusted_plan.plan_ref,
                milestone_id=checkpoint.milestone_ref,
                target_role="project-steward",
                task_id=task_id,
            )
            started = task_start(
                role="project-steward",
                handoff_ref=handoff_ref,
                caller_role="task-main",
                sandbox=sandbox,
                dispatcher=dispatcher,
                plan_id=checkpoint.plan_id,
            )
            started_task_id = started.get("task_id") if isinstance(started, Mapping) else None
            if not isinstance(started_task_id, str) or not started_task_id.strip():
                raise RuntimeError("task.start returned no canonical Steward task ID")
            if started_task_id.strip() != task_id:
                raise RuntimeError("task.start returned a different canonical Steward task ID")
            return None

        return dispatch

    return factory


def _production_steward_result_task_id_resolver_factory(
    execution_store: Any,
):
    def factory(checkpoint: StewardshipCheckpoint):
        def resolve(_record: Any) -> str:
            # Deterministic closure never resolves a semantic result. Defer
            # residual-only identity construction until replay actually needs it.
            handoff = _canonical_steward_handoff(checkpoint)
            task_id = _steward_task_id(checkpoint, handoff)
            durable = execution_store.get(task_id)
            if durable is None or getattr(durable, "canonical_task_id", None) != task_id:
                raise RuntimeError(
                    "durable Steward execution record is unavailable; refusing to guess its task ID"
                )
            return task_id

        return resolve

    return factory


def _load_bootstrap_dict() -> dict[str, Any] | None:
    # Explicit path takes precedence (harness-controlled). No ad-hoc /tmp
    # diagnostics: failures are fail-closed via None/raises and bounded
    # trace/observation mechanisms.
    # Host representation is semantically irrelevant: placeholder literals like
    # "${VAR}" are not specially recognized (HERMES_PLACEHOLDER_FILTER_SPECIAL_CASE=no).
    # An explicit path that is a literal placeholder fails closed as missing file,
    # not via special-case filtering.
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip():
        p = Path(explicit)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if not root_env:
        return None
    root = Path(root_env).resolve()
    bootstrap_path = root / BOOTSTRAP_RELPATH
    if not bootstrap_path.is_file():
        return None
    try:
        return json.loads(bootstrap_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _handoff_for(
    work_item_id: str,
    milestone_ref: str = "M1",
    *,
    project_id: str | None = None,
    plan_authority: str | None = None,
    plan_digest: str | None = None,
    work_semantics: Mapping[str, WorkSemanticProjection | Mapping[str, Any]] | None = None,
) -> TaskHandoff:
    """Generic TaskHandoff derivation from trusted Plan runtime.

    Derives handoff from live Plan view (milestone + work item) + canonical
    project context. No fixture authority, no dogfood special-case, no
    hard-coded milestone as production authority. Preserves the six
    core semantic fields and valid semantic refs, no mechanical fields.

    Trusted inputs: milestone_ref (from MilestonePlanView), work_item_id,
    project_id and plan_authority (from bootstrap). No model-supplied
    authority.

    M2/W2 Host Representation Neutralization (D7/D8):
    - Heuristic generic scope (GENERIC_SCOPE_FALLBACK_PRODUCTION_PATH=no) is
      removed from production. A Worker TaskHandoff must derive from a trusted
      WorkSemanticProjection (via durable coordinator or explicit bootstrap
      table). Generic "Execute Work Item W1..." without projection fails closed
      with WorkScopeInsufficientError (WORK_SCOPE_INSUFFICIENT_FAILS_CLOSED=yes).
    - The operator bootstrap table is retained ONLY as explicit test-only
      compatibility (never production authority). The durable coordinator path
      remains the production normal path.
    - Reviewer generic is preserved only for reviewer role (not Worker scope);
      Worker coder role never synthesizes scope.
    """
    wid = work_item_id.strip()
    mid = milestone_ref.strip() if milestone_ref else "M1"
    lower = wid.lower()
    is_review = lower.startswith("rv") or "/rv" in lower or "review" in lower

    if not is_review:
        # Heuristic fallback removed (D7). Production must supply trusted
        # WorkSemanticProjection; generic scope synthesis is not authority.
        if work_semantics is None or wid not in work_semantics:
            raise WorkScopeInsufficientError(
                f"no trusted Work semantics for Work Item {wid!r} "
                f"(Milestone {mid!r}); refusing scope-free derivation"
            )
        raw = work_semantics[wid]
        projection = raw if isinstance(raw, WorkSemanticProjection) else WorkSemanticProjection.from_dict(raw)
        return resolve_bounded_work_handoff(
            work_item_id=wid,
            milestone_ref=mid,
            projection=projection,
            project_id=project_id,
            plan_authority=plan_authority,
            plan_digest=plan_digest,
        )

    if is_review:
        work_role = "reviewer"
        # Generic review kind — reviewer lifecycle preserved (not Worker scope)
        safe_mid = "".join(c if c.isalnum() or c in "-_" else "-" for c in mid.lower())[:32] or "m1"
        safe_wi = "".join(c if c.isalnum() or c in "-_" else "-" for c in wid.lower().replace("/", "-"))[:48] or "rv1"
        task_kind = f"{safe_mid}-{safe_wi}-review"
        if len(task_kind) > 100:
            task_kind = task_kind[:100]
        objective = (
            f"Execute integrated review {wid} for Milestone {mid}. "
            f"Validate bounded workspace outputs via workspace.search/read and test.run. "
            f"Use only governed operations; no terminal/shell fallback."
        )
        bounded_scope = f"review/{safe_mid}/{safe_wi}/bounded-scope"
        validation_expectations = ("integrated review validation",)
        semantic_stop_expectations = ("stop if review scope unclear",)
    else:
        # Unreachable: non-review already returned or raised above.
        raise WorkScopeInsufficientError(
            f"no trusted Work semantics for Work Item {wid!r} "
            f"(Milestone {mid!r}); refusing scope-free derivation"
        )

    project_ref = SemanticReference(ref=project_id) if project_id else None
    plan_ref = SemanticReference(ref=plan_authority, digest=plan_digest) if plan_authority else None

    return TaskHandoff(
        work_role=work_role,
        task_kind=task_kind,
        objective=objective,
        bounded_scope=bounded_scope,
        validation_expectations=validation_expectations,
        semantic_stop_expectations=semantic_stop_expectations,
        work_item_ref=SemanticReference(ref=wid),
        milestone_ref=SemanticReference(ref=mid),
        project_ref=project_ref,
        plan_ref=plan_ref,
    )


def _reviewer_handoff(
    milestone_ref: str = "M1",
    project_id: str | None = None,
    plan_authority: str | None = None,
    plan_digest: str | None = None,
) -> TaskHandoff:
    return _handoff_for(
        "RV1",
        milestone_ref=milestone_ref,
        project_id=project_id,
        plan_authority=plan_authority,
        plan_digest=plan_digest,
    )


# AF #57 M3/RV1 lifecycle repair: the integrated-review dispatch must converge
# with the normal child lifecycle (W1/W2): materialize a durable work_item
# handoff, bind the exact canonical reviewer task, then dispatch with the real
# durable ref/digest. The dispatch mode name stays ``runtime_resolved`` but now
# means the runtime resolves and materializes the reviewer handoff BEFORE
# launch; it never means "inject an unresolvable synthetic digest".
REVIEW_DISPATCH_MODE_RUNTIME_RESOLVED = "runtime_resolved"
REVIEWER_DISPATCH_REFERENCES_DURABLE_HANDOFF = True
SECOND_HANDOFF_PROTOCOL_CREATED = False


def _reviewer_evidence_refs(
    *,
    sandbox: Any,
    live_view: MilestonePlanView,
    state: Any,
) -> tuple[str, ...]:
    """Real openable durable evidence refs for the integrated review inputs.

    Prefers the W-item task.return result handoff refs (the accepted worker
    outputs) and falls back to the durable work-item handoff refs recorded at
    dispatch. Every returned ref is actually opened before inclusion, so the
    reviewer never receives an unresolvable evidence ref. No evidence bodies
    are inlined.
    """
    from aota_forge.work_plane.handoff_store import handoff_open
    from aota_forge.work_plane.task_return_receipt import read_task_return_receipt

    def _openable(ref: str, digest: str) -> bool:
        try:
            opened = handoff_open(ref, "card", sandbox=sandbox)
        except Exception:
            return False
        opened_digest = opened.get("digest")
        return (
            isinstance(opened_digest, str)
            and opened_digest.strip().lower() == digest.strip().lower()
        )

    bindings = dict(getattr(state, "bindings", {}) or {}) if state is not None else {}
    evidence: list[str] = []
    seen: set[str] = set()
    for wi in tuple(getattr(live_view.graph, "work_items", ()) or ()):
        entry = bindings.get(wi)
        if not isinstance(entry, Mapping):
            continue
        candidates: list[tuple[str, str]] = []
        canonical_task_id = entry.get("canonical_task_id")
        if isinstance(canonical_task_id, str) and canonical_task_id.strip():
            try:
                receipt = read_task_return_receipt(sandbox, canonical_task_id.strip())
            except Exception:
                receipt = None
            if receipt is not None:
                candidates.append((receipt.result_ref, receipt.result_digest))
        handoff_ref = entry.get("handoff_ref")
        handoff_digest = entry.get("handoff_digest")
        if (
            isinstance(handoff_ref, str)
            and handoff_ref.startswith("handoff://")
            and isinstance(handoff_digest, str)
        ):
            candidates.append((handoff_ref, handoff_digest))
        for ref, digest in candidates:
            if not isinstance(ref, str) or ref in seen or not isinstance(digest, str):
                continue
            if len(digest) != 64:
                continue
            if _openable(ref, digest):
                seen.add(ref)
                evidence.append(ref)
    return tuple(evidence)


def _materialize_reviewer_work_handoff(
    *,
    base_handoff: TaskHandoff,
    sandbox: Any,
    live_view: MilestonePlanView,
    state: Any,
    project_id: str,
    reviewer_task_id: str,
    plan_id: str | None = None,
    parent_task_identity: str = "",
) -> dict[str, Any]:
    """Materialize the durable integrated-reviewer work_item handoff (AF #57 M3/RV1).

    Reuses the existing handoff.write durable store + canonical digest (no
    second reviewer handoff store, no synthetic digest): the exact canonical
    reviewer task id is bound in the control envelope, the bounded review
    context/refs ride the semantic payload, and the returned real ref/digest
    are what the dispatch and the reviewer bootstrap consume.
    """
    import dataclasses

    from aota_forge.runtime.task_main.coordinator import compute_work_source_digest
    from aota_forge.work_plane.handoff_store import HANDOFF_CONTROL_FIELDS, handoff_write

    evidence_refs = _reviewer_evidence_refs(
        sandbox=sandbox, live_view=live_view, state=state
    )[:32]
    context_refs: list[str] = [f"review-task://{reviewer_task_id}"]
    if parent_task_identity:
        context_refs.append(f"parent-task://{parent_task_identity}")
    if isinstance(live_view.entry_base, str) and live_view.entry_base.strip():
        context_refs.append(f"accepted-main://{live_view.entry_base.strip()}")
    if isinstance(live_view.plan_authority, str) and live_view.plan_authority.strip():
        context_refs.append(f"plan-authority://{live_view.plan_authority.strip()}")
    if plan_id:
        context_refs.append(f"plan-id://{plan_id}")
    for source_slice in tuple(getattr(live_view, "work_source_slices", ()) or ()):
        try:
            ref = (
                f"acceptance-criteria://{live_view.milestone_id}/"
                f"{source_slice.work_item_id}"
            )
            digest = compute_work_source_digest(str(source_slice.source_text))
        except Exception:
            continue
        context_refs.append(f"{ref}#{digest}")

    validation_expectations = tuple(base_handoff.validation_expectations) + (
        "emit verdict PASS|PASS_WITH_FINDINGS|NEEDS_FIX|BLOCKED|INCONCLUSIVE with findings",
    )
    enriched = dataclasses.replace(
        base_handoff,
        validation_expectations=validation_expectations,
        context_refs=tuple(SemanticReference(ref=r) for r in context_refs[:32]),
        evidence_refs=tuple(SemanticReference(ref=r) for r in evidence_refs),
        skill_refs=(SemanticReference(ref="aota-implementation-review@1.0.0"),),
    )
    # Durable semantic payload: bounded, store-compatible (lists of strings,
    # no control fields). Digests for evidence refs are resolvable from the
    # durable artifacts themselves.
    semantic: dict[str, Any] = {
        "work_role": enriched.work_role.value,
        "task_kind": enriched.task_kind,
        "objective": enriched.objective,
        "bounded_scope": enriched.bounded_scope,
        "validation_expectations": list(enriched.validation_expectations),
        "semantic_stop_expectations": list(enriched.semantic_stop_expectations),
        "context_refs": [r.ref for r in enriched.context_refs],
        "evidence_refs": [r.ref for r in enriched.evidence_refs],
        "skill_refs": [r.ref for r in enriched.skill_refs],
        "milestone_ref": live_view.milestone_id,
        "work_item_ref": "RV1",
        "project_ref": project_id,
    }
    for key in tuple(semantic):
        if key in HANDOFF_CONTROL_FIELDS:
            del semantic[key]
    ref = handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=sandbox,
        plan_ref=live_view.plan_authority,
        milestone_id=live_view.milestone_id,
        work_item_id="RV1",
        target_role="reviewer",
        task_id=reviewer_task_id,
        provenance={
            "plan_digest": live_view.plan_digest,
            "review_dispatch_mode": REVIEW_DISPATCH_MODE_RUNTIME_RESOLVED,
        },
    )
    return {
        "handoff": enriched,
        "handoff_ref": ref.ref,
        "handoff_digest": ref.digest,
        "canonical_task_id": reviewer_task_id,
        "dispatch_mode": REVIEW_DISPATCH_MODE_RUNTIME_RESOLVED,
    }


def _validate_bootstrap_trust_boundary(data: dict[str, Any], bootstrap_path: Path | None) -> None:
    """Fail-closed validation of bootstrap trust boundary (W3 operational acceptance).

    Ensures bootstrap file content is consistent with trusted operator context
    (env root, worktree scope, store locations). Prevents tamper that could
    cross project/worktree/session scope or inject foreign store paths.
    """
    from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError

    # Basic identifier shape
    import re

    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    for field in ("project_id", "worktree_id"):
        val = str(data.get(field, ""))
        if not val.strip() or len(val) > 512:
            raise TrustedBindingError(f"bootstrap {field} invalid length/empty")
        if not _SAFE_ID_RE.fullmatch(val):
            raise TrustedBindingError(f"bootstrap {field} invalid identifier")
    # origin session: allow broader but must be non-empty bounded
    origin_val = str(data.get("origin_task_main_session_ref", ""))
    if not origin_val.strip() or len(origin_val) > 512:
        raise TrustedBindingError("bootstrap origin_task_main_session_ref invalid")

    worktree_root = Path(str(data["worktree_root"])).resolve()
    coordinator_store_path = Path(str(data["coordinator_store_path"])).resolve()
    execution_store_path = Path(str(data["execution_store_path"])).resolve()
    runtime_config_path = Path(str(data["runtime_config_path"])).resolve()
    raw_governance_store = str(data.get("governance_store_path") or "").strip()
    governance_store_path = Path(raw_governance_store).resolve() if raw_governance_store else None

    # Bootstrap location must be derived from trusted env, not CWD or model path
    # Verify worktree_root matches the trusted env root (scope matching)
    trusted_root: Path | None = None
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip():
        try:
            trusted_root = Path(explicit).parent.parent.resolve() if explicit.endswith(BOOTSTRAP_RELPATH) else Path(explicit).parent.resolve()
            # If explicit is file path, its parent/.aota parent is worktree root
            if bootstrap_path is not None:
                expected_root = bootstrap_path.parent.parent.resolve()
                if worktree_root != expected_root:
                    raise TrustedBindingError("bootstrap worktree_root does not match explicit bootstrap location")
        except Exception as exc:
            raise TrustedBindingError(f"bootstrap explicit location mismatch: {exc}") from exc
    else:
        root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
        if root_env:
            try:
                trusted_root = Path(root_env).resolve()
            except Exception:
                raise TrustedBindingError("bootstrap trusted root invalid")
            if worktree_root != trusted_root:
                raise TrustedBindingError("bootstrap worktree_root does not match trusted AOTA_W3_MCP_ROOT")
        else:
            raise TrustedBindingError("bootstrap trusted root absent")

    # Store paths must be within worktree_root/.aota (bounded, no escape)
    store_paths = [
        (coordinator_store_path, "coordinator_store_path"),
        (execution_store_path, "execution_store_path"),
    ]
    if governance_store_path is not None:
        store_paths.append((governance_store_path, "governance_store_path"))
    for p, label in store_paths:
        try:
            p.relative_to(worktree_root)
        except ValueError:
            raise TrustedBindingError(f"bootstrap {label} outside worktree_root")
        # Must be under .aota
        if ".aota" not in p.parts:
            raise TrustedBindingError(f"bootstrap {label} not under .aota")
    # Runtime config must be absolute and within worktree or trusted location
    if not runtime_config_path.is_absolute():
        raise TrustedBindingError("bootstrap runtime_config_path must be absolute")
    # File permissions: bootstrap must not be world-writable or contain secrets
    if bootstrap_path is not None and bootstrap_path.exists():
        try:
            mode = bootstrap_path.stat().st_mode
            if mode & 0o002:
                raise TrustedBindingError("bootstrap file is world-writable")
        except TrustedBindingError:
            raise
        except Exception:
            pass
    # Plan digest shape validation (hex 64)
    live = data.get("live_plan_view") or {}
    digest = str(live.get("plan_digest", ""))
    if digest and (len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest.lower())):
        raise TrustedBindingError("bootstrap live_plan_view digest invalid")


def _bootstrap_path_for_validation() -> Path | None:
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip():
        p = Path(explicit)
        if p.is_file():
            return p.resolve()
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if root_env:
        return (Path(root_env).resolve() / BOOTSTRAP_RELPATH).resolve()
    return None


# ---------------------------------------------------------------------------
# AF #57 M3/AC10 — semantic Steward production dispatch grounding
# ---------------------------------------------------------------------------
#
# The semantic Steward is not a normal Plan Work Item: it is created from a
# trusted Governance checkpoint plus a typed semantic residual.  Its work
# source is the trusted checkpoint / semantic residual / logical replay
# identity already owned by StewardshipCheckpoint and the existing Steward
# replay seam -- there is no second work-source system and no fabricated Work
# Item.  A normal Worker binding requires governed Work Item source grounding;
# a semantic Steward binding requires the reconciliation below instead.  Role
# text alone is never sufficient authority.

SEMANTIC_STEWARD_DISPATCH_TARGET_ROLE = "project-steward"
SEMANTIC_STEWARD_CANONICAL_TASK_PREFIX = "steward:"


class _TrustedClosureReadyOutcome:
    """Minimal trusted closure-ready runner-outcome view.

    ``_build_stewardship_checkpoint`` re-validates every durable fact (state,
    readiness, plan/milestone/project identity); the runner outcome only
    supplies the trusted closure-ready fact.  This view lets the Worker
    binding resolver reconstruct the exact same checkpoint the production
    semantic Steward dispatch factory derived its handoff from, without
    re-running the runner.
    """

    milestone_closure_ready = True


def is_semantic_steward_dispatch_handoff(opened: Mapping[str, Any]) -> bool:
    """Structural declaration of the canonical semantic Steward dispatch.

    Positive trusted-envelope declaration only: the durable control envelope
    must name the Project Steward role and the canonical ``steward:`` task
    identity.  This is a routing hypothesis, never authority by itself -- the
    caller must reconcile the trusted checkpoint, semantic residual, logical
    replay identity and canonical task id before any Worker binding is built.
    """
    envelope = opened.get("envelope") if isinstance(opened, Mapping) else None
    if not isinstance(envelope, Mapping):
        return False
    if envelope.get("target_role") != SEMANTIC_STEWARD_DISPATCH_TARGET_ROLE:
        return False
    task_id = envelope.get("task_id")
    return isinstance(task_id, str) and task_id.strip().startswith(
        SEMANTIC_STEWARD_CANONICAL_TASK_PREFIX
    )


def validate_semantic_steward_dispatch_grounding(
    *,
    opened: Mapping[str, Any],
    payload: Any,
    state: Any,
    live_view: MilestonePlanView,
    sandbox: Any,
    trusted_plan: TrustedPlanIdentity,
    semantic_facts: SemanticFactSet,
    plan_id: str | None,
    all_milestones_closed: bool,
    project_id: str,
) -> TaskHandoff:
    """Reconcile a declared semantic Steward dispatch with trusted facts.

    The bounded alternate path is admitted only with positive proof of the
    canonical semantic Steward dispatch contract, reconciled against the same
    server-side facts the production dispatch factory used:

    * trusted Governance checkpoint reconstructed from durable coordinator
      state + the trusted Plan view + trusted semantic facts;
    * typed semantic residual (missing residual => fail closed; a checkpoint
      that is deterministically finalizable is never a Steward dispatch);
    * canonical Steward task identity (``steward:<digest>``) recomputed from
      project_id + plan_id + checkpoint_id + canonical handoff digest;
    * trusted control envelope plan/milestone identity agreement;
    * durable handoff semantic payload equality with the checkpoint-derived
      canonical TaskHandoff and canonical raw project binding.

    Any missing/mismatched/foreign dimension raises the typed fail-closed
    binding error before any physical Worker launch.
    """

    def _deny(detail: str) -> TrustedBindingError:
        error = TrustedBindingError(f"WORKER_BINDING_UNAVAILABLE: {detail}")
        error.code = "WORKER_BINDING_UNAVAILABLE"
        return error

    from aota_forge.governance.stewardship import StewardshipDisposition
    from aota_forge.work_plane.task_facade import load_trusted_work_item_task_handoff

    if not is_semantic_steward_dispatch_handoff(opened):
        raise _deny("semantic Steward grounding requires the canonical Steward dispatch declaration")
    envelope = opened.get("envelope")
    if not isinstance(envelope, Mapping):
        raise _deny("semantic Steward handoff carries no control envelope")
    declared_task_id = str(envelope.get("task_id")).strip()
    context = payload.get("context") if isinstance(payload, Mapping) else None
    payload_task_id = context.get("canonical_task_id") if isinstance(context, Mapping) else None
    if not isinstance(payload_task_id, str) or not payload_task_id.strip():
        raise _deny("semantic Steward dispatch payload carries no canonical task id")
    if payload_task_id.strip() != declared_task_id:
        raise _deny("canonical Steward task identity contradicts the trusted dispatch payload")

    checkpoint = _build_stewardship_checkpoint(
        runner_outcome=_TrustedClosureReadyOutcome(),
        state=state,
        live_view=live_view,
        sandbox=sandbox,
        trusted_plan=trusted_plan,
        semantic_facts=semantic_facts,
        plan_id=plan_id,
        all_milestones_closed=all_milestones_closed,
    )
    if checkpoint.project_id != project_id:
        raise _deny("semantic Steward checkpoint project does not match the trusted dispatch project")
    evaluation = evaluate_checkpoint(checkpoint)
    residual = evaluation.residual
    if evaluation.disposition is not StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED or residual is None:
        raise _deny("semantic Steward dispatch is not grounded by a trusted semantic residual")
    canonical_handoff = build_semantic_steward_handoff(checkpoint, residual)
    expected_task_id = _steward_task_id(checkpoint, canonical_handoff)
    if declared_task_id != expected_task_id:
        raise _deny(
            "canonical Steward task identity does not reconcile with the trusted logical checkpoint"
        )
    if envelope.get("plan_ref") != checkpoint.trusted_plan.plan_ref:
        raise _deny("semantic Steward handoff Plan identity contradicts the trusted Plan")
    expected_milestone = (
        checkpoint.milestone_ref if checkpoint.milestone_ref is not None else live_view.milestone_id
    )
    if envelope.get("milestone_id") != expected_milestone:
        raise _deny("semantic Steward handoff Milestone contradicts the trusted checkpoint")
    derived = load_trusted_work_item_task_handoff(opened=opened, sandbox=sandbox)
    if derived.handoff_digest != canonical_handoff.handoff_digest:
        raise _deny(
            "semantic Steward durable handoff does not match the trusted checkpoint handoff"
        )
    if derived.project_ref is None or derived.project_ref.ref != checkpoint.project_id:
        raise _deny("semantic Steward handoff project binding does not match the trusted project")
    if derived.work_item_ref is not None:
        raise _deny("semantic Steward dispatch must not claim a normal Work Item binding")
    return canonical_handoff


def try_build_task_main_binding() -> TrustedWorkerBinding | None:
    """Attempt to build a task-main TrustedWorkerBinding from host bootstrap.

    Returns None if no bootstrap file is present (caller is a normal worker).
    Raises TrustedBindingError if the bootstrap is present but malformed.
    This is the ONLY place where TrustedTaskMainRuntimeContext is minted for
    a real Hermes MCP child.
    """
    bootstrap_path = _bootstrap_path_for_validation()
    data = _load_bootstrap_dict()
    if data is None:
        return None
    # AF #53 M3/W1: an explicitly thin operator bootstrap is never a legacy
    # bootstrap. The trusted runtime path selector must route it to the thin
    # composition; this compatibility builder refuses it fail-closed.
    if str(data.get("runtime_path", "legacy") or "legacy") == TASK_MAIN_RUNTIME_PATH_THIN:
        raise TrustedBindingError(
            "thin task-main bootstrap cannot be built by the legacy host builder"
        )
    # Trust-boundary validation (W3) — fail closed on tamper / scope mismatch
    _validate_bootstrap_trust_boundary(data, bootstrap_path)
    # Validate required keys — all are operator-controlled, not model supplied
    project_id = str(data["project_id"])
    worktree_id = str(data["worktree_id"])
    worktree_root = Path(str(data["worktree_root"])).resolve()
    coordinator_store_path = Path(str(data["coordinator_store_path"])).resolve()
    execution_store_path = Path(str(data["execution_store_path"])).resolve()
    runtime_config_path = Path(str(data["runtime_config_path"])).resolve()
    raw_governance_store = str(data.get("governance_store_path") or "").strip()
    governance_store_path = Path(raw_governance_store).resolve() if raw_governance_store else None
    origin_session = str(data["origin_task_main_session_ref"])
    executor_id = str(data.get("executor_id", "hermes"))
    coordinator_id = data.get("coordinator_id")  # may be None
    live_view_dict = data["live_plan_view"]
    next_view_dict = data.get("next_milestone_view")
    raw_plan_id = data.get("plan_id")
    if raw_plan_id is not None and not is_plan_id(raw_plan_id):
        raise TrustedBindingError(
            "bootstrap plan_id must be a canonical internal Plan ID when supplied"
        )
    plan_id = raw_plan_id
    # AF #55 M1/W4: optional trusted Plan project identity from the
    # operator-owned bootstrap. Consumed by the canonical project binding;
    # never a model argument.
    bootstrap_source_repository = str(data.get("source_repository") or "").strip() or None
    raw_registry = str(data.get("registry_path") or "").strip()
    bootstrap_registry_path = Path(raw_registry).resolve() if raw_registry else None

    # Reconstruct typed views
    live_view = _view_from_dict(live_view_dict)
    next_view = _view_from_dict(next_view_dict) if next_view_dict else None
    trusted_plan = _trusted_plan_identity_from_bootstrap(live_view, data)
    semantic_facts = _semantic_facts_from_bootstrap(data)

    # Reconstruct stores (file-backed, durable)
    coord_store = FileBackedTaskMainCoordinatorStore(coordinator_store_path)
    exec_store = FileBackedExecutionStateStore(execution_store_path)

    # Build dispatcher via production composition (requires runtime config)
    # The dispatcher will be used by TaskMainControlService to dispatch workers
    # It needs the same hermes binary and runtime config that the operator
    # used to launch the task-main session.
    from aota_forge.core.execution.durable_state import OriginSessionRef
    from aota_forge.runtime.config import load_runtime_config

    # Ensure dispatcher sees same env as harness; AOTA_FORGE_RUNTIME_CONFIG
    # should already point to runtime_config_path, but we enforce fallback
    old_env = os.environ.get("AOTA_FORGE_RUNTIME_CONFIG")
    need_restore = False
    if old_env != str(runtime_config_path):
        os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = str(runtime_config_path)
        need_restore = True
    try:
        runtime_cfg = load_runtime_config(config_path=str(runtime_config_path))
        dispatcher = create_production_execution_dispatcher(
            default_cwd=worktree_root,
            runtime_config=runtime_cfg,
            state_store=exec_store,
            origin_session_ref=OriginSessionRef(value=origin_session),
        )
        # AF #49 M1/W1: bind this same trusted production dispatcher onto the
        # canonical ingress seam so Agent-facing task.start/task.return resolve
        # the real production graph; no test-double fallback is reachable.
        from aota_forge.core.ingress import bind_execution_dispatcher

        bind_execution_dispatcher(dispatcher)
        # AF #49 M1/W3: the production task-main completion coordinator now
        # carries the real Hermes completion delivery transport (exact-session
        # re-entry through the accepted W2 seam). The task-main profile comes
        # from the operator RuntimeConfig binding; the exact target session
        # arrives per delivery from the durable execution record's trusted
        # origin binding (origin_task_main_session_ref), never from model or
        # Worker input. No manual harness trigger is required: the bounded
        # task-main progression pass reconciles terminal evidence and delivers
        # pending completions through this transport.
        from aota_forge.composition.execution import (
            create_durable_completion_coordinator,
            create_hermes_completion_delivery_transport,
        )
        from aota_forge.work_plane.task_return_receipt import (
            WorktreeSemanticReturnEvidenceProvider,
        )

        # AF #49 M1/W9 (I49-B007): the parent-side terminal-truth boundary
        # carries the governed semantic-return evidence seam. The trusted
        # worktree sandbox is resolved lazily (it is built below, before this
        # binding is ever used); a missing sandbox resolves to no evidence and
        # therefore never permits an unproven success.
        semantic_return_provider = WorktreeSemanticReturnEvidenceProvider(
            sandbox_resolver=lambda: _w2_holder.get("sandbox"),
        )
        completion = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=exec_store,
            runtime_config=runtime_cfg,
            transport=create_hermes_completion_delivery_transport(runtime_config=runtime_cfg),
            semantic_return_provider=semantic_return_provider,
        )
    finally:
        if need_restore:
            if old_env is None:
                os.environ.pop("AOTA_FORGE_RUNTIME_CONFIG", None)
            else:
                os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = old_env

    # M1/W2 production Worker explicit env wiring (F2 repair).
    # The dispatcher/host_client created above inherits the task-main process
    # env by default; without this seam every Worker supervisor would inherit
    # task-main bootstrap authority. The resolver below derives the explicit
    # per-dispatch Worker child mapping from the existing trusted AF binding
    # (handoff resolvers + worktree base captured here), never from model
    # text. Host_client validates/filters it before Popen(env=...).
    _w2_repo_root = Path(__file__).resolve().parents[2]
    _w2_worktree_root = worktree_root
    _w2_worktree_id = worktree_id
    _w2_project_id = project_id
    _w2_runtime_config_path = runtime_config_path

    def _w2_extract_work_item(payload: Any) -> str | None:
        try:
            if not isinstance(payload, Mapping):
                return None
            context = payload.get("context")
            if not isinstance(context, Mapping):
                return None
            working = context.get("working_context")
            if not isinstance(working, Mapping):
                return None
            refs = working.get("refs")
            if not isinstance(refs, Mapping):
                return None
            wi_ref = refs.get("work_item_ref")
            if isinstance(wi_ref, Mapping):
                candidate = wi_ref.get("ref")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        except Exception:
            return None
        return None

    # AF #49 M1/W9: the trusted worktree sandbox holder is declared before the
    # completion coordinator (above) so the semantic-return provider can
    # resolve it lazily; it is populated later by the W8 binding section.
    # (Closures capture the name, resolved at call time.)
    _w2_holder: dict[str, Any] = {}

    def _w2_binding_unavailable(detail: str) -> TrustedBindingError:
        """Typed fail-closed Worker binding error (AF #49 M1/W8)."""
        error = TrustedBindingError(f"WORKER_BINDING_UNAVAILABLE: {detail}")
        error.code = "WORKER_BINDING_UNAVAILABLE"
        return error

    def _w2_typed_binding_error(exc: Exception) -> TrustedBindingError:
        """Normalize any resolver failure into the typed binding-unavailable error."""
        existing = getattr(exc, "code", None)
        if isinstance(existing, str) and existing:
            return exc  # type: ignore[return-value]
        detail = str(exc)
        prefix = "WORKER_BINDING_UNAVAILABLE: "
        if detail.startswith(prefix):
            detail = detail[len(prefix):]
        return _w2_binding_unavailable(detail)

    def _w2_extract_trusted_handoff(payload: Any) -> tuple[bool, dict[str, str] | None]:
        """Extract the trusted internal Work handoff record from a dispatch payload.

        AF #49 M1/W8 (I49-B006): the record is trusted server-side metadata
        produced by grounded ``task.start``. A declared-but-malformed record
        fails closed (never silently degrades to a bindingless launch);
        absence means the dispatch did not select the governed Worker binding
        contract.
        """
        if not isinstance(payload, Mapping):
            return False, None
        context = payload.get("context")
        if not isinstance(context, Mapping):
            return False, None
        working = context.get("working_context")
        if not isinstance(working, Mapping):
            return False, None
        if "trusted_work_handoff" not in working:
            return False, None
        record = working.get("trusted_work_handoff")
        if not isinstance(record, Mapping):
            raise _w2_binding_unavailable("trusted_work_handoff record must be a mapping")
        ref = record.get("ref")
        digest = record.get("digest")
        mode = record.get("mode")
        if not isinstance(ref, str) or not ref.strip():
            raise _w2_binding_unavailable("trusted Work handoff ref missing")
        if not isinstance(digest, str) or not digest.strip():
            raise _w2_binding_unavailable("trusted Work handoff digest missing")
        if not isinstance(mode, str) or mode.strip() != "work_item":
            raise _w2_binding_unavailable(f"trusted Work handoff mode invalid: {mode!r}")
        return True, {"ref": ref.strip(), "digest": digest.strip().lower(), "mode": "work_item"}

    def _w2_canonical_task_id(payload: Any) -> str:
        context = payload.get("context") if isinstance(payload, Mapping) else None
        if isinstance(context, Mapping):
            candidate = context.get("canonical_task_id")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise _w2_binding_unavailable("dispatch payload carries no canonical_task_id")

    def _w2_build_child_env(
        handoff: TaskHandoff,
        payload: Any,
        *,
        work_handoff_ref: str | None = None,
        work_handoff_digest: str | None = None,
    ) -> Any:
        from aota_forge.composition.worker_vertical_slice import build_worker_child_environment

        return build_worker_child_environment(
            root=_w2_worktree_root,
            project_id=_w2_project_id,
            worktree_id=_w2_worktree_id,
            canonical_task_id=_w2_canonical_task_id(payload),
            handoff=handoff,
            trace_path=None,
            repo_root=_w2_repo_root,
            runtime_config_path=_w2_runtime_config_path,
            work_handoff_ref=work_handoff_ref,
            work_handoff_digest=work_handoff_digest,
        )

    def _w2_validate_grounded_handoff(opened: Mapping[str, Any], wi: str | None) -> None:
        """Mechanical identity/digest validation of the re-opened durable handoff.

        AF #49 M1/W8 (I49-B006): exact equality / trusted lookup only, reusing
        the canonical W4 grounding identity resolver. The model may request a
        Work Item, but it never authors the trusted binding identity.
        """
        from aota_forge.runtime.task_main.coordinator import (
            MILESTONE_MISMATCH,
            PLAN_DIGEST_MISMATCH,
            PLAN_REF_MISMATCH,
            WORK_ITEM_MISMATCH,
            WORK_SOURCE_DIGEST_MISMATCH,
            WORK_SOURCE_GROUNDING_MISSING,
            WorkSourceGroundingError,
            resolve_trusted_work_grounding,
        )

        envelope = opened.get("envelope")
        if not isinstance(envelope, Mapping):
            raise WorkSourceGroundingError(
                WORK_SOURCE_GROUNDING_MISSING,
                "durable work_item handoff carries no control envelope",
            )
        provenance = envelope.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        plan_ref = envelope.get("plan_ref")
        plan_digest = provenance.get("plan_digest")
        milestone_id = envelope.get("milestone_id")
        work_item_id = envelope.get("work_item_id")
        source_digest = provenance.get("work_source_digest")
        missing = [
            name
            for name, value in (
                ("plan_ref", plan_ref),
                ("plan_digest", plan_digest),
                ("milestone_id", milestone_id),
                ("work_item_id", work_item_id),
                ("work_source_digest", source_digest),
            )
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise WorkSourceGroundingError(
                WORK_SOURCE_GROUNDING_MISSING,
                f"durable work_item handoff is not source-grounded (missing {missing})",
            )
        if plan_ref.strip() != live_view.plan_authority:
            raise WorkSourceGroundingError(
                PLAN_REF_MISMATCH,
                f"handoff plan_ref {plan_ref!r} != current trusted plan {live_view.plan_authority!r}",
            )
        if plan_digest.strip() != live_view.plan_digest:
            raise WorkSourceGroundingError(
                PLAN_DIGEST_MISMATCH,
                "handoff plan_digest does not match the current trusted Plan revision",
            )
        if milestone_id.strip() != live_view.milestone_id:
            raise WorkSourceGroundingError(
                MILESTONE_MISMATCH,
                f"handoff milestone_id {milestone_id!r} != current trusted Milestone {live_view.milestone_id!r}",
            )
        grounding = resolve_trusted_work_grounding(live_view, work_item_id=work_item_id)
        if source_digest.strip() != grounding["work_source_digest"]:
            raise WorkSourceGroundingError(
                WORK_SOURCE_DIGEST_MISMATCH,
                f"handoff Work source digest does not match the authoritative source of {work_item_id!r}",
            )
        if wi is not None and work_item_id.strip() != wi:
            raise WorkSourceGroundingError(
                WORK_ITEM_MISMATCH,
                f"handoff Work Item {work_item_id!r} contradicts dispatch Work Item {wi!r}",
            )

    def _w2_is_review_dispatch(wi: str | None) -> bool:
        if not isinstance(wi, str) or not wi.strip():
            return False
        lowered = wi.strip().lower()
        return lowered.startswith("rv") or "/rv" in lowered or "review" in lowered

    def _w2_validate_grounded_review_handoff(
        opened: Mapping[str, Any], payload: Any, wi: str | None
    ) -> None:
        """Mechanical identity validation of the integrated-reviewer handoff.

        AF #57 M3/RV1: the reviewer work-item handoff is materialized by the
        trusted runtime before dispatch, so it declares its exact canonical
        reviewer task binding. Validation is identity/digest only (plan /
        milestone / reviewer role / exact task binding); no Work source digest
        exists for the review seat (the review input is the evidence refs in
        the handoff itself).
        """
        from aota_forge.runtime.task_main.coordinator import (
            MILESTONE_MISMATCH,
            PLAN_DIGEST_MISMATCH,
            PLAN_REF_MISMATCH,
            WORK_ITEM_MISMATCH,
            WORK_SOURCE_GROUNDING_MISSING,
            WorkSourceGroundingError,
        )

        envelope = opened.get("envelope")
        if not isinstance(envelope, Mapping):
            raise WorkSourceGroundingError(
                WORK_SOURCE_GROUNDING_MISSING,
                "durable review work_item handoff carries no control envelope",
            )
        semantic = opened.get("semantic")
        semantic = semantic if isinstance(semantic, Mapping) else {}
        provenance = envelope.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        plan_ref = envelope.get("plan_ref")
        plan_digest = provenance.get("plan_digest")
        milestone_id = envelope.get("milestone_id")
        work_item_id = envelope.get("work_item_id")
        task_id = envelope.get("task_id")
        target_role = envelope.get("target_role")
        work_role = semantic.get("work_role")
        missing = [
            name
            for name, value in (
                ("plan_ref", plan_ref),
                ("plan_digest", plan_digest),
                ("milestone_id", milestone_id),
                ("work_item_id", work_item_id),
                ("task_id", task_id),
            )
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise WorkSourceGroundingError(
                WORK_SOURCE_GROUNDING_MISSING,
                f"durable review work_item handoff is not bound (missing {missing})",
            )
        if plan_ref.strip() != live_view.plan_authority:
            raise WorkSourceGroundingError(
                PLAN_REF_MISMATCH,
                f"review handoff plan_ref {plan_ref!r} != current trusted plan "
                f"{live_view.plan_authority!r}",
            )
        if plan_digest.strip() != live_view.plan_digest:
            raise WorkSourceGroundingError(
                PLAN_DIGEST_MISMATCH,
                "review handoff plan_digest does not match the current trusted Plan revision",
            )
        if milestone_id.strip() != live_view.milestone_id:
            raise WorkSourceGroundingError(
                MILESTONE_MISMATCH,
                f"review handoff milestone_id {milestone_id!r} != current trusted "
                f"Milestone {live_view.milestone_id!r}",
            )
        if wi is not None and work_item_id.strip() != wi:
            raise WorkSourceGroundingError(
                WORK_ITEM_MISMATCH,
                f"review handoff Work Item {work_item_id!r} contradicts dispatch Work Item {wi!r}",
            )
        if work_role != "reviewer" or target_role != "reviewer":
            raise WorkSourceGroundingError(
                WORK_ITEM_MISMATCH,
                "review handoff role is not the reviewer role",
            )
        canonical_task_id = _w2_canonical_task_id(payload)
        if task_id.strip() != canonical_task_id:
            raise _w2_binding_unavailable(
                f"review handoff task binding {task_id!r} != canonical reviewer task "
                f"{canonical_task_id!r}; refusing reviewer launch"
            )

    def _w2_worker_env_resolver(payload: Any) -> Any:
        """Governed Worker env resolver (AF #49 M1/W8, I49-B006).

        Normal path: resolve the same canonical durable grounded work_item
        handoff that ``task.start`` resolved, validate its trusted identity,
        derive the Worker binding and build the Worker child environment.

        Fail-closed: any missing/stale/foreign/mismatched/tampered/unresolvable
        trusted handoff raises a typed binding-unavailable error; the caller
        (host client) then performs ZERO physical Worker dispatch. A resolver
        ``None`` is returned only when the dispatch did not select the governed
        Worker binding contract and carries no Work Item identity.
        """
        declared, trusted = _w2_extract_trusted_handoff(payload)
        wi = _w2_extract_work_item(payload)
        is_review = _w2_is_review_dispatch(wi)
        if declared and trusted is not None:
            try:
                from aota_forge.work_plane.handoff_store import handoff_open
                from aota_forge.work_plane.task_facade import (
                    load_trusted_work_item_task_handoff,
                )

                sandbox = _w2_holder.get("sandbox")
                if sandbox is None:
                    raise _w2_binding_unavailable("trusted worktree sandbox is not available")
                opened = handoff_open(trusted["ref"], "full", sandbox=sandbox)
                if opened.get("mode") != "work_item":
                    raise _w2_binding_unavailable(
                        f"durable handoff mode is {opened.get('mode')!r}, not work_item"
                    )
                opened_digest = opened.get("digest")
                if not isinstance(opened_digest, str) or opened_digest.strip().lower() != trusted["digest"]:
                    raise _w2_binding_unavailable(
                        "durable handoff digest does not match the trusted dispatch reference"
                    )
                if is_semantic_steward_dispatch_handoff(opened):
                    # AF #57 M3/AC10: the semantic Steward is grounded by its
                    # trusted checkpoint + semantic residual + logical replay
                    # identity, not by a normal Work Item source.  This branch
                    # is admitted only after full reconciliation; any missing
                    # or foreign dimension raises before any physical dispatch.
                    handoff = validate_semantic_steward_dispatch_grounding(
                        opened=opened,
                        payload=payload,
                        state=coord_store.get(
                            coordinator_id or f"{project_id}:{live_view.milestone_id}"
                        ),
                        live_view=live_view,
                        sandbox=sandbox,
                        trusted_plan=trusted_plan,
                        semantic_facts=semantic_facts,
                        plan_id=plan_id,
                        all_milestones_closed=next_view is None,
                        project_id=project_id,
                    )
                else:
                    if is_review:
                        _w2_validate_grounded_review_handoff(opened, payload, wi)
                    else:
                        _w2_validate_grounded_handoff(opened, wi)
                    handoff = load_trusted_work_item_task_handoff(opened=opened, sandbox=sandbox)
                    if wi is not None:
                        wi_ref = handoff.work_item_ref.ref if handoff.work_item_ref is not None else None
                        if wi_ref != wi:
                            raise _w2_binding_unavailable(
                                f"derived Work Item {wi_ref!r} contradicts dispatch Work Item {wi!r}"
                            )
                return _w2_build_child_env(
                    handoff,
                    payload,
                    work_handoff_ref=trusted["ref"],
                    work_handoff_digest=trusted["digest"],
                )
            except TrustedBindingError as exc:
                raise _w2_typed_binding_error(exc) from exc
            except Exception as exc:
                inner = getattr(exc, "code", None)
                detail = (
                    f"{inner}: {exc}"
                    if isinstance(inner, str) and inner
                    else f"{type(exc).__name__}: {exc}"
                )
                raise _w2_binding_unavailable(detail) from exc
        if not declared:
            if wi is None:
                # Not a governed Work dispatch: preserves legitimate generic
                # host-client usage without an AF Worker binding contract.
                return None
            # Legacy/other trusted Work dispatch shape (e.g. runtime projection
            # dispatch): resolve through the trusted durable coordinator /
            # operator semantics; failure is typed fail-closed, never a
            # bindingless physical Worker launch.
            handoff_resolver_fn = _w2_holder.get("handoff_resolver")
            reviewer_dispatch_fn = _w2_holder.get("reviewer_dispatch_resolver")
            if is_review and reviewer_dispatch_fn is None:
                # AF #57 M3/RV1: a reviewer dispatch without the trusted
                # durable materializer can never launch with an unresolvable
                # handoff digest; fail closed before any physical spawn.
                raise _w2_binding_unavailable(
                    "reviewer dispatch requires the trusted durable reviewer "
                    "handoff materializer; refusing a reviewer launch with an "
                    "unresolvable handoff digest"
                )
            if handoff_resolver_fn is None and not is_review:
                raise _w2_binding_unavailable("no trusted Work handoff resolver is available")
            try:
                if is_review:
                    # Materialize the durable reviewer work-item handoff now
                    # (same canonical mechanism as the runtime review
                    # dispatch) and launch from the durable artifact.
                    from aota_forge.work_plane.handoff_store import handoff_open
                    from aota_forge.work_plane.task_facade import (
                        load_trusted_work_item_task_handoff,
                    )

                    record = reviewer_dispatch_fn()
                    if not isinstance(record, Mapping) or not isinstance(
                        record.get("handoff_ref"), str
                    ):
                        raise _w2_binding_unavailable(
                            "trusted reviewer handoff materializer returned no durable ref"
                        )
                    sandbox = _w2_holder.get("sandbox")
                    if sandbox is None:
                        raise _w2_binding_unavailable(
                            "trusted worktree sandbox is not available"
                        )
                    opened = handoff_open(record["handoff_ref"], "full", sandbox=sandbox)
                    _w2_validate_grounded_review_handoff(opened, payload, wi)
                    handoff = load_trusted_work_item_task_handoff(
                        opened=opened, sandbox=sandbox
                    )
                    return _w2_build_child_env(
                        handoff,
                        payload,
                        work_handoff_ref=record["handoff_ref"],
                        work_handoff_digest=str(record.get("handoff_digest") or ""),
                    )
                handoff = handoff_resolver_fn(wi)
                return _w2_build_child_env(handoff, payload)
            except TrustedBindingError as exc:
                raise _w2_typed_binding_error(exc) from exc
            except Exception as exc:
                inner = getattr(exc, "code", None)
                detail = (
                    f"{inner}: {exc}"
                    if isinstance(inner, str) and inner
                    else f"{type(exc).__name__}: {exc}"
                )
                raise _w2_binding_unavailable(detail) from exc
        raise _w2_binding_unavailable("trusted Work handoff record unusable")

    try:
        _registry = getattr(dispatcher, "registry", None)
        _adapters = getattr(_registry, "_adapters", {}) if _registry is not None else {}
        for _adapter in list(_adapters.values()):
            _host = getattr(_adapter, "_host_client", None)
            if _host is not None and hasattr(_host, "_worker_env_resolver"):
                try:
                    _host._worker_env_resolver = _w2_worker_env_resolver
                except Exception:
                    pass
    except Exception:
        pass

    # Trusted resolvers: generic derivation from Plan runtime + project context
    work_items = list(live_view.graph.work_items)

    # M1/W1: trusted bounded Work semantics from the operator-owned bootstrap
    # channel. Malformed tables or entries for ungoverned Work Items fail
    # closed here (no silent fallback, no invented requirements).
    try:
        semantics_table = parse_work_semantics_table(data.get("work_semantics"))
    except WorkScopeInsufficientError as exc:
        raise TrustedBindingError(f"bootstrap work_semantics invalid: {exc}") from exc
    for _key in semantics_table:
        if _key not in work_items:
            raise TrustedBindingError(
                f"bootstrap work_semantics entry {_key!r} is not a governed Work Item "
                f"of Milestone {live_view.milestone_id!r}"
            )

    def handoff_resolver(wi: str) -> TaskHandoff:
        # Production Work projection (M1/W1-R1): task-main-owned durable
        # projection first, operator bootstrap table only as test/bootstrap
        # compatibility. Absent or insufficient semantics fail closed with
        # WORK_SCOPE_INSUFFICIENT — a scope-free generic Worker is never
        # dispatched silently (I40-B003/F1: do not recreate the stall where a
        # scope-free Worker is launched and expected to guess).
        if wi not in work_items:
            raise WorkScopeInsufficientError(
                f"no trusted Work semantics for ungoverned Work Item {wi!r} "
                f"(Milestone {live_view.milestone_id!r}); refusing to invent scope"
            )
        # 1. Task-main-owned durable path (production normal path, no operator
        #    refresh, survives restart via the existing coordinator store).
        #    Fresh read each call so commit + restart are observed without
        #    rebuilding the binding.
        _durable_coordinator_id = coordinator_id or f"{project_id}:{live_view.milestone_id}"
        try:
            _durable_state = coord_store.get(_durable_coordinator_id)
        except Exception:
            _durable_state = None
        if _durable_state is not None:
            _durable_table = getattr(_durable_state, "work_projections", None) or {}
            if wi in _durable_table:
                from aota_forge.runtime.task_main.coordinator import (
                    resolve_task_main_work_handoff,
                )

                # Wrong/stale identity fails closed inside (no cross-Work reuse,
                # no stale Plan reuse, no generic fallback).
                return resolve_task_main_work_handoff(
                    state=_durable_state,
                    work_item_id=wi,
                    live_plan_view=live_view,
                )
        # 2. Operator bootstrap table (test/bootstrap compatibility only;
        #    never production authority, never the normal path).
        if wi not in semantics_table:
            raise WorkScopeInsufficientError(
                f"no trusted Work semantics for Work Item {wi!r} "
                f"(Milestone {live_view.milestone_id!r}); refusing scope-free dispatch"
            )
        return _handoff_for(
            wi,
            milestone_ref=live_view.milestone_id,
            project_id=project_id,
            plan_authority=live_view.plan_authority,
            plan_digest=live_view.plan_digest,
            work_semantics=semantics_table,
        )

    # Automatic governed evidence derivation via trusted durable stores (W2)
    # Small generic bridge: execution/result/card -> GovernedWorkItemEvidence
    # No operator insertion, no model construction, no second store.
    _auto_resolver = create_automatic_governed_evidence_resolver(
        execution_store=exec_store,
        coordinator_store=coord_store,
        coordinator_id=coordinator_id or f"{project_id}:{live_view.milestone_id}",
        live_plan_view=live_view,
        handoff_resolver=handoff_resolver,
        project_id=project_id,
        plan_authority=live_view.plan_authority,
        milestone_id=live_view.milestone_id,
    )

    def governed_evidence_resolver(wi: str):
        # Runtime-owned automatic derivation, card-first, fail-closed
        return _auto_resolver(wi)

    def reviewer_handoff_resolver():
        return _reviewer_handoff(
            milestone_ref=live_view.milestone_id,
            project_id=project_id,
            plan_authority=live_view.plan_authority,
            plan_digest=live_view.plan_digest,
        )

    # Bind resolvers for the W2 Worker explicit-env seam (closures resolve at call time).
    _w2_holder["handoff_resolver"] = handoff_resolver
    _w2_holder["reviewer_handoff_resolver"] = reviewer_handoff_resolver

    def governed_review_resolver(cid: str, digest: str):
        # D7: synthetic review acceptance removed (SYNTHETIC_REVIEW_ACCEPTANCE_PRODUCTION_PATH=no).
        # Review truth must come from governed review/reconciliation path
        # (AF_RECONCILIATION_OR_GOVERNED_REVIEW). If review evidence is
        # unavailable, fail closed (do not fabricate PASS).
        return derive_governed_review_evidence(
            reviewer_canonical_task_id=cid,
            card_digest=digest,
            execution_store=exec_store,
            live_plan_view=live_view,
            reviewer_handoff_resolver=reviewer_handoff_resolver,
        )

    def reviewer_canonical_task_id_resolver():
        # Deterministic reviewer task id for this slice
        return f"{project_id}:{live_view.milestone_id}:RV1:attempt-1"

    def reviewer_dispatch_resolver():
        # AF #57 M3/RV1: the trusted runtime review dispatch materializes the
        # durable reviewer work_item handoff (exact canonical reviewer task
        # binding) and returns its real ref/digest BEFORE the reviewer launch.
        sandbox = _w2_holder.get("sandbox")
        if sandbox is None:
            raise TrustedBindingError(
                "integrated-review dispatch requires the trusted worktree sandbox"
            )
        current_coordinator_id = coordinator_id or f"{project_id}:{live_view.milestone_id}"
        try:
            current_state = coord_store.get(current_coordinator_id)
        except Exception:
            current_state = None
        return _materialize_reviewer_work_handoff(
            base_handoff=reviewer_handoff_resolver(),
            sandbox=sandbox,
            live_view=live_view,
            state=current_state,
            project_id=project_id,
            reviewer_task_id=reviewer_canonical_task_id_resolver(),
            plan_id=plan_id,
            parent_task_identity=origin_session,
        )

    _w2_holder["reviewer_dispatch_resolver"] = reviewer_dispatch_resolver

    # Build the outer TrustedWorkerBinding for task-main (neutral AF principal).
    # D7: synthetic project evidence removed from production path
    # (SYNTHETIC_PROJECT_AUTHORITY_PRODUCTION_PATH=no). Missing canonical
    # project evidence fails closed (MISSING_CANONICAL_PROJECT_EVIDENCE_FAILS_CLOSED=yes).
    # Test fixtures may create synthetic evidence only via explicit test seam
    # (AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE=1).
    trusted_identity_declared = bool(bootstrap_source_repository or bootstrap_registry_path)
    try:
        _ev = _project_evidence(
            worktree_root,
            project_id,
            source_repository=bootstrap_source_repository,
            registry_path=bootstrap_registry_path,
        )
        if _ev.status != "RESOLVED":
            raise ValueError(f"evidence not resolved: {_ev.status}")
        sandbox = bind_worktree_sandbox(_ev, worktree_id, worktree_root)
    except Exception as exc:
        # A declared trusted Plan identity must never degrade to synthetic
        # evidence (AF #55 M1/W4): Git-grounded resolution fails closed.
        if not trusted_identity_declared and (
            os.environ.get("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE") == "1"
            or os.environ.get("AOTA_ALLOW_LEGACY_ENV_DISCOVERY") == "1"
        ):
            from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence

            synthetic = ProjectCandidateEvidence(
                workspace_id=f"test-{project_id}",
                workspace_root=str(worktree_root),
                project_id=project_id,
                project_root=str(worktree_root),
                manifest_path=".aota/project.yaml",
                name=project_id,
                kind="test-synthetic",
                status="active",
                registry_fingerprint="0" * 64,
                candidate_fingerprint="1" * 64,
            )
            synth_ev = ProjectResolutionEvidence(
                status="RESOLVED",
                workspace_id=f"test-{project_id}",
                workspace_root=str(worktree_root),
                registry_fingerprint="0" * 64,
                listing_fingerprint="0" * 64,
                candidates=(synthetic,),
            )
            sandbox = bind_worktree_sandbox(synth_ev, worktree_id, worktree_root)
        else:
            raise TrustedBindingError(f"missing canonical project evidence: {exc}") from exc
    # AF #49 M1/W8 (I49-B006): carry the already-built trusted worktree sandbox
    # into the governed Worker env resolver closure. Construction-time trusted
    # object only; durable recovery always re-reads the durable handoff store.
    _w2_holder["sandbox"] = sandbox

    # AF #57 M3/W3: the production legacy composition owns the trusted
    # checkpoint reconstruction and semantic dispatch factory. Deterministic
    # checkpoints never invoke the factory; semantic checkpoints use the
    # existing handoff.write -> task.start -> durable result-owner path.
    def stewardship_checkpoint_resolver(runner_outcome: Any) -> StewardshipCheckpoint:
        current_coordinator_id = getattr(runner_outcome, "coordinator_id", None)
        if not isinstance(current_coordinator_id, str) or not current_coordinator_id.strip():
            raise TrustedBindingError("runner outcome carries no trusted coordinator identity")
        current_state = coord_store.get(current_coordinator_id)
        return _build_stewardship_checkpoint(
            runner_outcome=runner_outcome,
            state=current_state,
            live_view=live_view,
            sandbox=sandbox,
            trusted_plan=trusted_plan,
            semantic_facts=semantic_facts,
            plan_id=plan_id,
            all_milestones_closed=next_view is None,
        )

    stewardship_dispatch_factory = _production_steward_dispatch_factory(
        dispatcher=dispatcher,
        sandbox=sandbox,
        plan_id=plan_id,
    )
    stewardship_result_task_id_resolver_factory = (
        _production_steward_result_task_id_resolver_factory(exec_store)
    )
    control_service = create_task_main_control_service(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
        completion_coordinator=completion,
        stewardship_checkpoint_resolver=stewardship_checkpoint_resolver,
        stewardship_dispatch_factory=stewardship_dispatch_factory,
        stewardship_result_task_id_resolver_factory=stewardship_result_task_id_resolver_factory,
        stewardship_sandbox=sandbox,
        stewardship_repo_path=worktree_root,
        stewardship_origin_session_ref=origin_session,
        governance_store=governance_store_path,
    )
    # Build the trusted task-main context (operator-owned)
    ctx = TrustedTaskMainRuntimeContext(
        control_service=control_service,
        live_plan_view=live_view,
        origin_task_main_session_ref=origin_session,
        executor_id=executor_id,
        handoff_resolver=handoff_resolver,
        governed_evidence_resolver=governed_evidence_resolver,
        reviewer_handoff_resolver=reviewer_handoff_resolver,
        governed_review_resolver=governed_review_resolver,
        reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
        reviewer_dispatch_resolver=reviewer_dispatch_resolver,
        next_milestone_view=next_view,
        session_available=True,
        coordinator_id=coordinator_id,
    )
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-control",
        objective="AOTA task-main milestone control via aota.invoke",
        bounded_scope="milestone coordination only",
        validation_expectations=("task-main control validation",),
        semantic_stop_expectations=("stop at user gate",),
        work_item_ref=SemanticReference(ref=f"{live_view.milestone_id}/task-main"),
        milestone_ref=SemanticReference(ref=live_view.milestone_id),
    )
    # Task-main eager is exactly the 4 controls; W3 adds broad read + inspection
    # W2-R1 bounded repair: task-main requires progressive result.hydrate to consume
    # production by_ref Skill content (7053-14911 bytes > 4096). Progressive only,
    # scoped to valid ToolOutputRef via existing result.hydrate canonical operation.
    # HYDRATE_AUTHORITY_SCOPED=yes, no new operation, no new MCP tool.
    # M3/W1 writer: task_main.submit_work_projection joins eager (canonical writer).
    # W3 rebalance: task-main gets broad authorized project search/read + bounded
    # restricted terminal (inspection + git_inspection) per role×family.
    # W5 (AF #49 M1/W5, I49-B001): the canonical normal path handoff.write,
    # handoff.open and task.start join the task-main model-visible surface.
    # Visibility is not authority: server-side role/grounding validation still
    # decides execution (Worker task.start remains denied).
    tool_surface = create_role_tool_surface(
        "task-main",
        eager=("workspace.search", "workspace.read", "task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once", "task_main.submit_work_projection", "handoff.write", "handoff.open", "task.start"),
        progressive=("result.hydrate", "restricted_shell.run"),
    )
    # W3 broad read: task-main may perform small targeted project search/read
    # without needing Analyst or Worker TaskHandoff. Provide read authorities
    # via broad seam (sandbox + operation, handoff as context not ACL).
    # Use the control handoff itself as context (not Worker TaskHandoff).
    from aota_forge.work_plane.workspace_tools import (
        create_broad_workspace_read_authority,
        WORKSPACE_READ_DESCRIPTOR as _W3_READ_DESC,
        WORKSPACE_SEARCH_DESCRIPTOR as _W3_SEARCH_DESC,
    )
    try:
        read_authorities = (
            create_broad_workspace_read_authority(sandbox, _W3_SEARCH_DESC, handoff=handoff, applicable_policies=()),
            create_broad_workspace_read_authority(sandbox, _W3_READ_DESC, handoff=handoff, applicable_policies=()),
        )
    except Exception:
        read_authorities = ()
    # W3 restricted shell for task-main: inspection + git_inspection only
    from aota_forge.work_plane.restricted_shell import create_restricted_shell_authority, RESTRICTED_SHELL_DESCRIPTOR as _W3_SHELL_DESC
    try:
        restricted_shell_authority = create_restricted_shell_authority(sandbox, handoff, (), _W3_SHELL_DESC)
    except Exception:
        restricted_shell_authority = None

    trusted_plan_binding = None
    if trusted_plan.authority_binding is not None:
        if trusted_plan.authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE:
            from aota_forge.work_plane.github_tools import TrustedPlanGitHubBinding

            trusted_plan_binding = TrustedPlanGitHubBinding.from_plan_ref(
                trusted_plan.authority_binding.authority_ref
            )
    binding = TrustedWorkerBinding(
        canonical_task_id=f"{project_id}:{live_view.milestone_id}:task-main:{origin_session[:8]}",
        project_id=project_id,
        worktree_id=worktree_id,
        trusted_context=bind_trusted_context(principal_id="task-main", principal_type="task-main", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=None,
        restricted_shell_authority=restricted_shell_authority,
        plan_binding=trusted_plan_binding,
        plan_authority_binding=trusted_plan.authority_binding,
        trusted_task_main_context=ctx,
    )
    return binding


def write_bootstrap_file(
    *,
    worktree_root: Path,
    project_id: str,
    worktree_id: str,
    coordinator_store_path: Path,
    execution_store_path: Path,
    runtime_config_path: Path,
    origin_task_main_session_ref: str,
    live_plan_view: MilestonePlanView,
    next_milestone_view: MilestonePlanView | None,
    executor_id: str = "hermes",
    coordinator_id: str | None = None,
    plan_id: str | None = None,
    plan_authority_binding: PlanAuthorityBinding | None = None,
    governance_store_path: Path | None = None,
    work_semantics: Mapping[str, WorkSemanticProjection | Mapping[str, Any]] | None = None,
    stewardship_semantic_facts: Mapping[str, Any] | SemanticFactSet | None = None,
) -> Path:
    """Operator helper to materialize the bootstrap JSON for the MCP child.

    M1/W1-R1: work_semantics is test/bootstrap compatibility ONLY, never
    production authority (PRODUCTION_PREPARE_WORK_SEMANTICS_REQUIRED=no).
    Production task-main commits its bounded projection to the existing
    durable coordinator store; the resolver prefers that durable path.
    """
    worktree_root = worktree_root.resolve()
    dest = worktree_root / BOOTSTRAP_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    def view_to_dict(v: MilestonePlanView) -> dict[str, Any]:
        d: dict[str, Any] = {
            "plan_authority": v.plan_authority,
            "plan_digest": v.plan_digest,
            "plan_source_revision": v.plan_source_revision,
            "milestone_id": v.milestone_id,
            "entry_base": v.entry_base,
            "graph": {
                "milestone_ref": v.graph.milestone_ref,
                "work_items": list(v.graph.work_items),
                "dependencies": [list(e) for e in v.graph.dependencies],
            },
            "milestone_user_approval_satisfied": v.milestone_user_approval_satisfied,
            "plan_amendment_required": v.plan_amendment_required,
        }
        # M3/W1-R1 F2: carry bounded governed Work semantics (Plan authority
        # projection, not full Plan body). Empty stays empty (legacy compat).
        try:
            ws = tuple(getattr(v, "work_semantics", ()) or ())
        except Exception:
            ws = ()
        if ws:
            items: list[dict[str, Any]] = []
            for w in ws:
                try:
                    items.append(w.to_dict() if hasattr(w, "to_dict") else dict(w))
                except Exception:
                    continue
            d["work_semantics"] = items
        # W4: carry bounded faithful Work source slices (structural, digest-bound).
        try:
            wss = tuple(getattr(v, "work_source_slices", ()) or ())
        except Exception:
            wss = ()
        if wss:
            items_s: list[dict[str, Any]] = []
            for w in wss:
                try:
                    items_s.append(w.to_dict() if hasattr(w, "to_dict") else dict(w))
                except Exception:
                    continue
            d["work_source_slices"] = items_s
        return d

    payload = {
        "project_id": project_id,
        "worktree_id": worktree_id,
        "worktree_root": str(worktree_root),
        "coordinator_store_path": str(coordinator_store_path.resolve()),
        "execution_store_path": str(execution_store_path.resolve()),
        "runtime_config_path": str(runtime_config_path.resolve()),
        "origin_task_main_session_ref": origin_task_main_session_ref,
        "executor_id": executor_id,
        "coordinator_id": coordinator_id or f"{project_id}:{live_plan_view.milestone_id}",
        "live_plan_view": view_to_dict(live_plan_view),
        "next_milestone_view": view_to_dict(next_milestone_view) if next_milestone_view else None,
    }
    if plan_id is not None:
        if not is_plan_id(plan_id):
            raise ValueError("plan_id must be a canonical internal Plan ID when supplied")
        payload["plan_id"] = plan_id
    if plan_authority_binding is not None:
        if not isinstance(plan_authority_binding, PlanAuthorityBinding):
            raise ValueError("plan_authority_binding must be a PlanAuthorityBinding")
        if plan_id != plan_authority_binding.plan_id:
            raise ValueError("plan_authority_binding must match plan_id")
        if plan_authority_binding.authority_ref != live_plan_view.plan_authority:
            raise ValueError(
                "plan_authority_binding must match the live Plan authority"
            )
        payload["plan_authority_binding"] = plan_authority_binding.to_dict()
    if governance_store_path is not None:
        payload["governance_store_path"] = str(governance_store_path.resolve())
    if stewardship_semantic_facts is not None:
        if isinstance(stewardship_semantic_facts, SemanticFactSet):
            facts = stewardship_semantic_facts
        else:
            facts = _semantic_facts_from_bootstrap(
                {"stewardship_semantic_facts": stewardship_semantic_facts}
            )
        payload["stewardship_semantic_facts"] = {
            "unresolved_defect_refs": list(facts.unresolved_defect_refs),
            "conflicting_artifact_refs": list(facts.conflicting_artifact_refs),
            "ambiguous_governance_reason_refs": list(facts.ambiguous_governance_reason_refs),
            "architecture_question_refs": list(facts.architecture_question_refs),
            "plan_change_question_refs": list(facts.plan_change_question_refs),
            "review_evidence_refs": list(facts.review_evidence_refs),
            "narrative_reconciliation_required": facts.narrative_reconciliation_required,
            "recorded_semantic_question_refs": list(facts.recorded_semantic_question_refs),
            "declared_kind": facts.declared_kind.value if facts.declared_kind is not None else None,
            "declared_reason": facts.declared_reason,
        }
    # M1/W1: trusted bounded Work semantics ride the existing operator-owned
    # bootstrap channel (no new store, no new authority). Keys must be
    # governed Work Items of the live Milestone graph; each projection is
    # validated here at materialization (fail-closed), so dispatch never
    # receives an unusable projection silently.
    if work_semantics is not None:
        table = parse_work_semantics_table(dict(work_semantics))
        governed = set(live_plan_view.graph.work_items)
        for key in table:
            if key not in governed:
                raise WorkScopeInsufficientError(
                    f"work_semantics entry {key!r} is not a governed Work Item of "
                    f"Milestone {live_plan_view.milestone_id!r}"
                )
        payload["work_semantics"] = {key: proj.to_dict() for key, proj in table.items()}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(dest)
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest
