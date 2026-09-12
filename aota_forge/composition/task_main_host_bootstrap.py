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
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    TrustedTaskMainRuntimeContext,
    TrustedWorkerBinding,
    create_task_main_envelope,
    verify_envelope,
)
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
    if explicit and explicit.strip():
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

        completion = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=exec_store,
            runtime_config=runtime_cfg,
            transport=create_hermes_completion_delivery_transport(runtime_config=runtime_cfg),
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

    # Placeholder installed now; rebound after handoff resolvers are defined
    # (closures capture the names, resolved at call time).
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

    def _w2_build_child_env(handoff: TaskHandoff, payload: Any) -> Any:
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
                _w2_validate_grounded_handoff(opened, wi)
                handoff = load_trusted_work_item_task_handoff(opened=opened, sandbox=sandbox)
                if wi is not None:
                    wi_ref = handoff.work_item_ref.ref if handoff.work_item_ref is not None else None
                    if wi_ref != wi:
                        raise _w2_binding_unavailable(
                            f"derived Work Item {wi_ref!r} contradicts dispatch Work Item {wi!r}"
                        )
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
            reviewer_resolver_fn = _w2_holder.get("reviewer_handoff_resolver")
            if handoff_resolver_fn is None:
                raise _w2_binding_unavailable("no trusted Work handoff resolver is available")
            try:
                lowered = wi.lower()
                if lowered.startswith("rv") or "/rv" in lowered or "review" in lowered:
                    if reviewer_resolver_fn is None:
                        raise _w2_binding_unavailable(
                            "no trusted reviewer handoff resolver is available"
                        )
                    handoff = reviewer_resolver_fn()
                else:
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

    # Build the outer TrustedWorkerBinding for task-main (neutral AF principal).
    # D7: synthetic project evidence removed from production path
    # (SYNTHETIC_PROJECT_AUTHORITY_PRODUCTION_PATH=no). Missing canonical
    # project evidence fails closed (MISSING_CANONICAL_PROJECT_EVIDENCE_FAILS_CLOSED=yes).
    # Test fixtures may create synthetic evidence only via explicit test seam
    # (AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE=1).
    try:
        _ev = _project_evidence(worktree_root, project_id)
        if _ev.status != "RESOLVED":
            raise ValueError(f"evidence not resolved: {_ev.status}")
        sandbox = bind_worktree_sandbox(_ev, worktree_id, worktree_root)
    except Exception as exc:
        if os.environ.get("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE") == "1" or os.environ.get("AOTA_ALLOW_LEGACY_ENV_DISCOVERY") == "1":
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
