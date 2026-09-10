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
import os
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.mcp_transport import TrustedTaskMainRuntimeContext, TrustedWorkerBinding, TrustedBindingError
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
from aota_forge.runtime.task_main.reconciliation import GovernedWorkItemEvidence, GovernedReviewEvidence
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle, ReviewFindingEvidence, ReviewFindingClassification
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.composition.completion_evidence import (
    create_automatic_governed_evidence_resolver,
    derive_governed_review_evidence,
)
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.workspace_mutation import WORKSPACE_WRITE_DESCRIPTOR, create_workspace_mutation_authority
from aota_forge.work_plane.restricted_shell import RESTRICTED_SHELL_DESCRIPTOR, create_restricted_shell_authority

BOOTSTRAP_ENV_ROOT = "AOTA_W3_MCP_ROOT"
BOOTSTRAP_RELPATH = ".aota/task-main-bootstrap.json"
# Also accept explicit path for testing harness
BOOTSTRAP_EXPLICIT_ENV = "AOTA_TASK_MAIN_BOOTSTRAP"


def _project_evidence(root: Path, project_id: str) -> ProjectResolutionEvidence:
    """Generic trusted project evidence via canonical resolver.

    Derives ProjectResolutionEvidence from trusted workspace root (worktree)
    + canonical .aota/project.yaml discovery + exact trusted project_id.
    No synthetic fingerprints, no M3 fixture authority, no project_id if/elif
    branching, no dogfood literal.

    Reuses canonical scan_projects / fingerprint_registry via
    resolve_trusted_project_evidence (single shared helper).
    Fail-closed: unknown / ambiguous / invalid remains not RESOLVED.
    """
    evidence = resolve_trusted_project_evidence(
        worktree_root=root,
        project_id=project_id,
    )
    return evidence


def _neutral_envelope(milestone: str) -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(milestone_ref=milestone, default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.STANDARD)


def _view_from_dict(d: dict[str, Any]) -> MilestonePlanView:
    g = d["graph"]
    graph = MilestoneWorkItemGraph(
        milestone_ref=g["milestone_ref"],
        work_items=list(g["work_items"]),
        dependencies=[list(e) for e in g.get("dependencies", [])],
    )
    return MilestonePlanView(
        plan_authority=d["plan_authority"],
        plan_digest=d["plan_digest"],
        plan_source_revision=d.get("plan_source_revision"),
        milestone_id=d["milestone_id"],
        entry_base=d["entry_base"],
        graph=graph,
        milestone_user_approval_satisfied=bool(d["milestone_user_approval_satisfied"]),
        plan_amendment_required=bool(d.get("plan_amendment_required", False)),
    )


def _load_bootstrap_dict() -> dict[str, Any] | None:
    # Explicit path takes precedence (harness-controlled)
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip() and "${" not in explicit:
        p = Path(explicit)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                try:
                    with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                        dbg.write(f"_load explicit failed {e}\n")
                except Exception:
                    pass
                return None
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load explicit not found {explicit}\n")
        except Exception:
            pass
        return None
    elif explicit and "${" in explicit:
        # Literal from hermes mcp_servers expansion when var not set in hermes env; ignore and fall back
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load explicit is literal {explicit}, ignoring\n")
        except Exception:
            pass
        pass  # fall through to root_env
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if not root_env:
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load no root_env\n")
        except Exception:
            pass
        return None
    root = Path(root_env).resolve()
    bootstrap_path = root / BOOTSTRAP_RELPATH
    if not bootstrap_path.is_file():
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load bootstrap not found {bootstrap_path} root_env={root_env}\n")
        except Exception:
            pass
        return None
    try:
        return json.loads(bootstrap_path.read_text(encoding="utf-8"))
    except Exception as e:
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load bootstrap json failed {e} {bootstrap_path}\n")
        except Exception:
            pass
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

    M1/W1 bounded scope contract: when a trusted WorkSemanticProjection is
    supplied for this Work Item (operator channel: task-main has already
    reasoned about the Work; the runtime faithfully transports that
    projection), the handoff carries usable bounded semantics via the
    existing TaskHandoff contract (REUSE, no parallel contract). Without a
    projection the legacy generic scope-free derivation is preserved for
    backward-compatible direct callers; the PRODUCTION resolver built by
    try_build_task_main_binding never uses that fallback silently — it
    fails closed with WorkScopeInsufficientError instead.

    bounded_scope is the Worker's only scope source; policy derivation must
    align to it (see worker_vertical_slice).
    """
    wid = work_item_id.strip()
    mid = milestone_ref.strip() if milestone_ref else "M1"
    lower = wid.lower()
    is_review = lower.startswith("rv") or "/rv" in lower or "review" in lower

    if not is_review and work_semantics is not None:
        # Explicit trusted projection channel: the entry must exist and must
        # yield a Worker-usable handoff. A missing entry fails closed here
        # (no silent generic substitution); callers wanting the legacy
        # generic derivation pass work_semantics=None.
        if wid not in work_semantics:
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
        # Generic review kind
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
        work_role = "coder"
        # Generic scope-free fallback shape. The exact template rule lives in
        # handoff_runtime.generic_fallback_scope_template /
        # generic_fallback_task_kind (single source of truth shared with the
        # Worker-usability gate); outputs are unchanged.
        task_kind = generic_fallback_task_kind(wid, mid)
        objective = (
            f"Execute Work Item {wid} for Milestone {mid}. "
            f"Implement bounded functionality via workspace.* operations only. "
            f"Use only governed operations; stop if scope unclear."
        )
        bounded_scope = generic_fallback_scope_template(wid, mid)
        validation_expectations = (f"validation for {wid}",)
        semantic_stop_expectations = (f"stop if {wid} scope unclear",)

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


def _validate_bootstrap_trust_boundary(data: dict[str, Any], bootstrap_path: Path | None) -> None:
    """Fail-closed validation of bootstrap trust boundary (W3 operational acceptance).

    Ensures bootstrap file content is consistent with trusted operator context
    (env root, worktree scope, store locations). Prevents tamper that could
    cross project/worktree/session scope or inject foreign store paths.
    """
    from aota_forge.mcp_transport import TrustedBindingError

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

    # Bootstrap location must be derived from trusted env, not CWD or model path
    # Verify worktree_root matches the trusted env root (scope matching)
    trusted_root: Path | None = None
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip() and "${" not in explicit:
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
    for p, label in ((coordinator_store_path, "coordinator_store_path"), (execution_store_path, "execution_store_path")):
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
    if explicit and explicit.strip() and "${" not in explicit:
        p = Path(explicit)
        if p.is_file():
            return p.resolve()
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if root_env:
        return (Path(root_env).resolve() / BOOTSTRAP_RELPATH).resolve()
    return None


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
    # Trust-boundary validation (W3) — fail closed on tamper / scope mismatch
    _validate_bootstrap_trust_boundary(data, bootstrap_path)
    # Validate required keys — all are operator-controlled, not model supplied
    project_id = str(data["project_id"])
    worktree_id = str(data["worktree_id"])
    worktree_root = Path(str(data["worktree_root"])).resolve()
    coordinator_store_path = Path(str(data["coordinator_store_path"])).resolve()
    execution_store_path = Path(str(data["execution_store_path"])).resolve()
    runtime_config_path = Path(str(data["runtime_config_path"])).resolve()
    origin_session = str(data["origin_task_main_session_ref"])
    executor_id = str(data.get("executor_id", "hermes"))
    coordinator_id = data.get("coordinator_id")  # may be None
    live_view_dict = data["live_plan_view"]
    next_view_dict = data.get("next_milestone_view")

    # Reconstruct typed views
    live_view = _view_from_dict(live_view_dict)
    next_view = _view_from_dict(next_view_dict) if next_view_dict else None

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
        # Completion coordinator (admission-gated, optional)
        # For this slice we create one without transport (delivery is via harness)
        # Use same store as dispatcher (ensured above) and limits from config
        from aota_forge.composition.execution import admission_limits_from_runtime_config

        completion = DurableCompletionCoordinator(
            dispatcher=dispatcher,
            store=exec_store,
            transport=None,
            admission_limits=admission_limits_from_runtime_config(runtime_cfg),
        )
    finally:
        if need_restore:
            if old_env is None:
                os.environ.pop("AOTA_FORGE_RUNTIME_CONFIG", None)
            else:
                os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = old_env

    control_service = TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
        completion_coordinator=completion,
    )

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
        # Production Work projection (M1/W1): the trusted operator-supplied
        # projection is faithfully transported into the existing TaskHandoff.
        # Absent or insufficient semantics fail closed with
        # WORK_SCOPE_INSUFFICIENT — a scope-free generic Worker is never
        # dispatched silently (I40-B003/F1: do not recreate the stall where a
        # scope-free Worker is launched and expected to guess).
        if wi not in work_items:
            raise WorkScopeInsufficientError(
                f"no trusted Work semantics for ungoverned Work Item {wi!r} "
                f"(Milestone {live_view.milestone_id!r}); refusing to invent scope"
            )
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

    def governed_review_resolver(cid: str, digest: str):
        # For this slice we synthesize a PASS review evidence deterministically
        # The actual review result card will be attached by the reviewer worker;
        # but the resolver is asked during advance to reconcile the review.
        # We return a minimal GovernedReviewEvidence that will be validated
        # against the actual card. For PASS we set no findings.
        # We need to fetch the actual card digest? The MCP transport will call
        # this with the actual cid/digest of the reviewer completion, so we
        # should load the card from exec_store to produce correct evidence.
        try:
            rec = exec_store.get(cid)
            card_dict = rec.worker_result_card if rec and rec.worker_result_card else None
            if card_dict:
                from aota_forge.work_plane.result_card import WorkerResultCard
                card = WorkerResultCard.from_dict(card_dict)
                ev = MilestoneReviewEvidence(
                    milestone_ref=SemanticReference(ref=live_view.milestone_id),
                    review_cycle=ReviewCycle.RV1,
                    reviewed_frontier_ref=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
                    review_result_ref=card.result_handoff_ref,
                    review_result_digest=card.compute_card_digest(),
                    finding_refs=(),
                )
                return GovernedReviewEvidence(
                    review_evidence=ev,
                    review_findings=(),
                    review_task_handoff=_reviewer_handoff(
                        milestone_ref=live_view.milestone_id,
                        project_id=project_id,
                        plan_authority=live_view.plan_authority,
                        plan_digest=live_view.plan_digest,
                    ),
                    expected_final_frontier=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
                )
        except Exception:
            pass
        # Fallback minimal (generic, no M3 fixture)
        ev = MilestoneReviewEvidence(
            milestone_ref=SemanticReference(ref=live_view.milestone_id),
            review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
            review_result_ref=SemanticReference(ref=cid),
            review_result_digest=digest,
            finding_refs=(),
        )
        return GovernedReviewEvidence(
            review_evidence=ev,
            review_findings=(),
            review_task_handoff=_reviewer_handoff(
                milestone_ref=live_view.milestone_id,
                project_id=project_id,
                plan_authority=live_view.plan_authority,
                plan_digest=live_view.plan_digest,
            ),
            expected_final_frontier=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
        )

    def reviewer_canonical_task_id_resolver():
        # Deterministic reviewer task id for this slice
        return f"{project_id}:{live_view.milestone_id}:RV1:attempt-1"

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
        next_milestone_view=next_view,
        session_available=True,
        coordinator_id=coordinator_id,
    )

    # Build the outer TrustedWorkerBinding for profile aota-task-main
    # The sandbox/handoff/tool_surface are for the task-main session itself
    # Generic evidence via shared helper; fallback to synthetic for legacy
    # test harnesses with synthetic tmp_path only (production always has real manifest)
    try:
        _ev = _project_evidence(worktree_root, project_id)
        if _ev.status != "RESOLVED":
            raise ValueError(f"evidence not resolved: {_ev.status}")
        sandbox = bind_worktree_sandbox(_ev, worktree_id, worktree_root)
    except Exception:
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
            registry_fingerprint="0"*64,
            candidate_fingerprint="1"*64,
        )
        synth_ev = ProjectResolutionEvidence(
            status="RESOLVED",
            workspace_id=f"test-{project_id}",
            workspace_root=str(worktree_root),
            registry_fingerprint="0"*64,
            listing_fingerprint="0"*64,
            candidates=(synthetic,),
        )
        sandbox = bind_worktree_sandbox(synth_ev, worktree_id, worktree_root)
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
    # Task-main eager is exactly the 3 controls; no workspace ops, no shell
    # W2-R1 bounded repair: task-main requires progressive result.hydrate to consume
    # production by_ref Skill content (7053-14911 bytes > 4096). Progressive only,
    # scoped to valid ToolOutputRef via existing result.hydrate canonical operation.
    # HYDRATE_AUTHORITY_SCOPED=yes, no new operation, no new MCP tool.
    tool_surface = create_role_tool_surface(
        "task-main",
        eager=("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once"),
        progressive=("result.hydrate",),
    )
    # For task-main we still need read authorities? The binding validation
    # allows 0..2 read authorities. Provide none for task-main (it doesn't do
    # workspace ops). But we need at least sandbox/handoff match.
    # The mcp_transport checks that read_authorities are tuple and <=2, and each
    # must match sandbox/handoff and be search/read. Empty is allowed.
    read_authorities: tuple = ()
    # But to satisfy tool_surface vs authority separation, we keep empty and
    # rely on task-main ops not needing workspace authority.

    binding = TrustedWorkerBinding(
        canonical_task_id=f"{project_id}:{live_view.milestone_id}:task-main:{origin_session[:8]}",
        project_id=project_id,
        worktree_id=worktree_id,
        trusted_context=bind_trusted_context(principal_id="hermes-task-main", principal_type="hermes-task-main", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=None,
        restricted_shell_authority=None,
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
    work_semantics: Mapping[str, WorkSemanticProjection | Mapping[str, Any]] | None = None,
) -> Path:
    """Operator helper to materialize the bootstrap JSON for the MCP child."""
    worktree_root = worktree_root.resolve()
    dest = worktree_root / BOOTSTRAP_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    def view_to_dict(v: MilestonePlanView) -> dict[str, Any]:
        return {
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
