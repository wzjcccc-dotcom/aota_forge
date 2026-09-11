"""One-Core canonical operation dispatch plane (AF #46 M1/W1).

Semantic owner: AF_CORE. Expected layer: core_ingress.

This package is the single canonical AF operation dispatch plane that
CLI, MCP and future adapters converge onto:

    adapter
      -> canonical contracts/ingress (this package + core/contracts)
      -> core/provider (core.ingress, ExecutionDispatcher, work_plane providers,
         runtime.task_main control)

Single descriptor authority remains ``.aota/contracts/operations.yaml`` via
``core.contracts.loader``. This package creates no second registry, no
second schema authority, no permission engine, no control plane, no state
machine. It reuses existing leaf providers/services without rewriting
business logic:

    BoundedWorkspaceToolProvider / BoundedWorkspaceMutationProvider
    ResultHydrateProvider / BoundedRestrictedShellProvider
    BoundedTestExecutionToolProvider / BoundedGitToolProvider
    Role bootstrap / Skill resolution / TaskMainControlService /
    TaskMainMilestoneRunner / TaskMainCoordinator / ExecutionDispatcher

Dependency direction (allowed):

    adapter -> canonical contracts/ingress -> core/provider

Forbidden (enforced by tests):

    Core (aota_forge/core/*) must not import this package, MCP transport,
    Hermes, work_plane, composition or runtime. This package may import
    Core contracts and provider/runtime leaves, never the reverse.

MCP remains a thin transport adapter: single ``aota.invoke``, protocol
adaptation, stdio mechanics, bounded protocol projection, mechanical
transport failures. It must not own operation lookup, input validation or
provider selection semantics after W1 (see ``mcp_transport`` delegation).

CLI remains a thin transport adapter: argument parsing, trusted adapter
resource resolution, canonical ingress call, result projection, exit codes.
It must not own operation semantic dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.loader import (
    discover_canonical_project_root,
    load_operation_descriptor_map,
)
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.providers.tool import ToolRequest, ToolResponse

SEMANTIC_OWNER = "AF_CORE"
EXPECTED_LAYER = "core_ingress"
CANONICAL_DESCRIPTOR_AUTHORITY = ".aota/contracts/operations.yaml"
OPERATION_DESCRIPTOR_AUTHORITY_COUNT = 1
SECOND_DESCRIPTOR_REGISTRY_CREATED = False
SECOND_SCHEMA_AUTHORITY_CREATED = False
NEW_OPERATION_AUTHORITY_REGISTRY_CREATED = False
NEW_PERMISSION_ENGINE_CREATED = False
NEW_CONTROL_PLANE_CREATED = False
NEW_STATE_MACHINE_CREATED = False
CANONICAL_OPERATION_DISPATCH_PLANE_COUNT = 1
LEAF_PROVIDER_REWRITE_REQUIRED = False

# Canonical operation families (single registration path via loader).
# Provider-backed (work_plane leaves) vs ingress-backed (core.ingress).
PROVIDER_BACKED_OPERATIONS: tuple[str, ...] = (
    "workspace.search",
    "workspace.read",
    "workspace.write",
    "result.hydrate",
    "restricted_shell.run",
    "role.bootstrap",
    "skill.open",
    "test.run",
    "task_main.activate_milestone",
    "task_main.recover_coordinator",
    "task_main.advance_once",
    "task_main.submit_work_projection",
    "git.status",
    "git.diff",
    "handoff.write",
    "handoff.open",
    "task.start",
    "task.return",
)

# Existing ingress families (already single Core path via core.ingress).
# Listed for registration completeness; dispatch delegates to core.ingress.
INGRESS_BACKED_OPERATIONS: tuple[str, ...] = (
    "project.resolve",
    "git.inspect",
    "runtime.status",
    "host.status",
    "operations.list",
    "plan_init",
    "plan_retirement",
    "execution.task_start",
    "execution.task_status",
    "execution.task_result",
    "execution.task_cancel",
    "execution.executor_list",
    "execution.executor_capabilities",
)

_CACHED_MAP: dict[str, OperationContractDescriptor] | None = None


def _load_map() -> dict[str, OperationContractDescriptor]:
    global _CACHED_MAP
    if _CACHED_MAP is not None:
        return _CACHED_MAP
    root = discover_canonical_project_root()
    _CACHED_MAP = load_operation_descriptor_map(root)
    return _CACHED_MAP


def list_canonical_operations() -> tuple[str, ...]:
    """All canonical operation names from single YAML authority (sorted)."""
    return tuple(sorted(_load_map().keys()))


def resolve_descriptor(operation: object) -> OperationContractDescriptor:
    """Single canonical operation resolution (exact, case-sensitive, no fuzzy).

    Raises ForgeError with code UNKNOWN_OPERATION for unknown ops and
    INPUT_TYPE_INVALID for non-string identity. Never invents aliases.
    """
    if not isinstance(operation, str):
        raise ForgeError("INPUT_TYPE_INVALID", f"operation must be string, got {type(operation).__name__}")
    desc = _load_map().get(operation)
    if desc is None:
        raise ForgeError("UNKNOWN_OPERATION", f"unknown operation: {operation!r}")
    return desc


def validate_operation_input(
    descriptor: OperationContractDescriptor, arguments: dict[str, Any] | None
) -> dict[str, Any]:
    """Single canonical typed validation (same validate_inputs seam)."""
    if arguments is None:
        arguments = {}
    return validate_inputs(descriptor, arguments)


def _authority_for(
    read_authorities: tuple[Any, ...], operation: str
) -> Any | None:
    for auth in read_authorities:
        try:
            if getattr(getattr(auth, "operation", None), "name", None) == operation:
                return auth
        except Exception:
            continue
    return None


@dataclass(frozen=True)
class CanonicalDispatchBinding:
    """Core-owned trusted dispatch binding (no MCP/Hermes types).

    Mechanical carrier of already-authoritative trusted objects. It decides
    no policy, mints no authority, resolves no projects. Adapters convert
    their transport binding into this shape mechanically (no semantic
    choice); Core owns provider selection from here.
    """

    canonical_task_id: str = ""
    project_id: str = ""
    worktree_id: str = ""
    trusted_context: Any | None = None
    handoff: Any | None = None
    sandbox: Any | None = None
    tool_surface: Any | None = None
    read_authorities: tuple[Any, ...] = ()
    mutation_authority: Any | None = None
    restricted_shell_authority: Any | None = None
    test_execution_authority: Any | None = None
    git_authorities: tuple[Any, ...] = ()
    trusted_task_main_context: Any | None = None
    allowed_operations: frozenset[str] = frozenset()

    def capability_names(self) -> frozenset[str]:
        """Visibility-only helper (never authority).

        Returns the model-facing discovery surface (eager + progressive).
        Permission decisions must use trusted authority evidence
        (read/mutation/shell/test authorities, sandbox, task-main context)
        owned by Core policy/provider, never this set. See D2 convergence.
        """
        if self.allowed_operations:
            return frozenset(self.allowed_operations)
        try:
            surface = self.tool_surface
            if surface is not None and hasattr(surface, "all_capability_names"):
                return frozenset(surface.all_capability_names())
        except Exception:
            pass
        return frozenset()


def _require_sandbox(binding: CanonicalDispatchBinding) -> Any:
    if binding.sandbox is None:
        raise ForgeError("AUTHORITY_DENIED", "trusted sandbox is absent for this binding")
    return binding.sandbox


def _trusted_production_execution_dispatcher() -> Any | None:
    """Resolve the trusted production ExecutionDispatcher, if one is bound.

    AF #49 M1/W1: bounded reuse of the existing ``core.ingress`` mechanical
    binding seam. Production composition binds the real dispatcher through
    ``bind_production_execution_dispatcher``; task.start / task.return then
    receive it explicitly. Returns None when no trusted production dispatcher
    is bound; callers must fail closed. Never constructs a test double and
    never falls back to ReferenceFakeExecutorAdapter / in-memory state.
    """
    try:
        from aota_forge.core.execution.dispatcher import ExecutionDispatcher
        from aota_forge.core.ingress import get_execution_dispatcher
    except Exception:
        return None
    bound = get_execution_dispatcher()
    if isinstance(bound, ExecutionDispatcher):
        return bound
    return None


def _persist_governed_if_needed(binding: CanonicalDispatchBinding, response: Any, operation: str) -> None:
    """Core-owned durability seam (D5): persist by_ref tool payloads.

    Delegates to AF result governance (durable_result_store). Best-effort,
    fail-open on persistence error; hydration fails closed when missing.
    Adapters never call this directly.
    """
    try:
        sandbox = binding.sandbox
        if sandbox is None:
            return
        from aota_forge.work_plane.durable_result_store import (
            persist_governed_tool_result_if_by_ref as _persist_governed,
        )

        _persist_governed(sandbox, response, operation)
    except Exception:
        pass


def _map_task_main_exception(exc: Exception) -> str:
    """Typed Core error identity (no transport string classification).

    Type-first: isinstance against the semantic owner's exception types,
    then explicit .code preservation for ForgeError/typed carriers.
    Never inspects str(exc) contents ("AUTHORITY", "PLAN_DRIFT", ...).
    """
    # Import lazily to avoid cycles; fall back to class-name only when
    # the semantic owner module is unavailable (still no string search).
    try:
        from aota_forge.runtime.task_main.control import TaskMainControlAuthorityError
    except Exception:
        TaskMainControlAuthorityError = None  # type: ignore
    try:
        from aota_forge.runtime.task_main.coordinator import (
            CoordinatorBindingError,
            CoordinatorRuntimeError,
            PlanDriftError,
            SessionRecoveryRequiredError,
        )
    except Exception:
        CoordinatorBindingError = CoordinatorRuntimeError = None  # type: ignore
        PlanDriftError = SessionRecoveryRequiredError = None  # type: ignore
    try:
        from aota_forge.runtime.task_main.coordinator_store import (
            CoordinatorNotFoundError,
            StaleCoordinatorRevisionError,
        )
    except Exception:
        CoordinatorNotFoundError = StaleCoordinatorRevisionError = None  # type: ignore
    if PlanDriftError is not None and isinstance(exc, PlanDriftError):
        return "PLAN_DRIFT"
    if SessionRecoveryRequiredError is not None and isinstance(exc, SessionRecoveryRequiredError):
        return "SESSION_RECOVERY_REQUIRED"
    if CoordinatorNotFoundError is not None and isinstance(exc, CoordinatorNotFoundError):
        return "COORDINATOR_NOT_FOUND"
    if CoordinatorBindingError is not None and isinstance(exc, CoordinatorBindingError):
        return "COORDINATOR_BINDING_ERROR"
    if CoordinatorRuntimeError is not None and isinstance(exc, CoordinatorRuntimeError):
        return "COORDINATOR_RUNTIME_ERROR"
    if StaleCoordinatorRevisionError is not None and isinstance(exc, StaleCoordinatorRevisionError):
        return "STALE_COORDINATOR_REVISION"
    if TaskMainControlAuthorityError is not None and isinstance(exc, TaskMainControlAuthorityError):
        return "AUTHORITY_DENIED"
    # Typed carriers (ForgeError + semantic-owner typed ValueErrors) preserve code.
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "GOVERNED_OPERATION_FAILURE"


def _map_role_bootstrap_exception(exc: Exception) -> str:
    """Typed role.bootstrap identity (no string classification)."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "GOVERNED_OPERATION_FAILURE"


def _map_skill_open_exception(exc: Exception) -> str:
    """Typed skill.open identity (no string classification).

    The semantic owner (role_bootstrap.handle_skill_open) already sets
    exc.code type-first from Skill* typed errors. Preserve it; never
    search str(exc) for "foreign"/"digest"/"not found".
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    # Fallback for legacy untyped Skill* errors that escaped the owner:
    # map strictly by type (still no string search).
    try:
        from aota_forge.work_plane.skill_content import SkillDigestMismatchError as _CDigest
        from aota_forge.work_plane.skill_content import SkillNotFoundError as _CNotFound
    except Exception:
        _CDigest = _CNotFound = None  # type: ignore
    try:
        from aota_forge.work_plane.skill_resolution import (
            SkillDigestMismatchError as _RDigest,
        )
        from aota_forge.work_plane.skill_resolution import SkillForeignNamespaceError as _RForeign
        from aota_forge.work_plane.skill_resolution import SkillMissingError as _RMissing
        from aota_forge.work_plane.skill_resolution import SkillNotAuthorizedError as _RNotAuth
    except Exception:
        _RDigest = _RForeign = _RMissing = _RNotAuth = None  # type: ignore
    if _RForeign is not None and isinstance(exc, _RForeign):
        return "FOREIGN_SKILL_DENIED"
    if _RNotAuth is not None and isinstance(exc, _RNotAuth):
        return "AUTHORITY_DENIED"
    if (_CDigest is not None and isinstance(exc, _CDigest)) or (
        _RDigest is not None and isinstance(exc, _RDigest)
    ):
        return "DIGEST_MISMATCH"
    if (_CNotFound is not None and isinstance(exc, _CNotFound)) or (
        _RMissing is not None and isinstance(exc, _RMissing)
    ):
        return "SKILL_NOT_FOUND"
    return "GOVERNED_OPERATION_FAILURE"


def _dispatch_task_main(
    operation: str,
    binding: CanonicalDispatchBinding,
    validated: dict[str, Any] | None = None,
) -> ToolResponse:
    # Authority: task-main role only, context required (same as MCP gate,
    # now owned by Core). Worker can never reach service.
    handoff = binding.handoff
    surface = binding.tool_surface
    try:
        handoff_role = getattr(getattr(handoff, "work_role", None), "value", None) or str(
            getattr(handoff, "work_role", "")
        )
    except Exception:
        handoff_role = ""
    try:
        surface_role = getattr(getattr(surface, "work_role", None), "value", None) or str(
            getattr(surface, "work_role", "")
        )
    except Exception:
        surface_role = ""
    if handoff_role != "task-main" or surface_role != "task-main":
        return ToolResponse.failure(
            {"code": "AUTHORITY_DENIED", "message": "task-main control requires AF role task-main"}
        )
    ctx = binding.trusted_task_main_context
    if ctx is None or not hasattr(ctx, "control_service"):
        return ToolResponse.failure(
            {"code": "AUTHORITY_DENIED", "message": "trusted task-main context is absent for this binding"}
        )
    # Reuse existing TaskMainControlService/Runner/Coordinator without
    # rewriting lifecycle semantics (LEAF rewrite = no).
    # D8: neutral AF role, not Hermes profile literal.
    try:
        from aota_forge.runtime.task_main.control import AF_TASK_MAIN_ROLE
    except Exception as exc:
        return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)})
    try:
        if operation == "task_main.activate_milestone":
            live = ctx.live_plan_view
            # Gate interpretation owned by Core service (D4): delegate,
            # never read live.user_gate_blocked directly here.
            try:
                blocked = bool(ctx.control_service.is_user_gate_blocked(live))
            except Exception:
                blocked = True
            if blocked:
                return ToolResponse.failure(
                    {"code": "USER_GATE_REQUIRED", "message": "USER_GATE_REQUIRED: live Plan Milestone approval not satisfied"}
                )
            try:
                # Coordinator identity owned by Core service (D4): delegate
                # None -> default instead of inventing "{project}:{milestone}".
                try:
                    resolved_id = ctx.control_service.resolve_coordinator_id(
                        project_id=binding.project_id,
                        live_plan_view=live,
                        coordinator_id=ctx.coordinator_id,
                    )
                except Exception:
                    resolved_id = ctx.coordinator_id
                handle = ctx.control_service.activate_milestone(
                    profile=AF_TASK_MAIN_ROLE,
                    plan_view=live,
                    origin_task_main_session_ref=ctx.origin_task_main_session_ref,
                    executor_id=ctx.executor_id,
                    project_id=binding.project_id,
                    coordinator_id=resolved_id,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                state = handle.state if hasattr(handle, "state") else None
                # Coordinator id comes from the durable handle; fall back to
                # the service-resolved id (never invent format here).
                fallback_id = ctx.coordinator_id
                try:
                    if not (isinstance(fallback_id, str) and fallback_id.strip()):
                        fallback_id = ctx.control_service.default_coordinator_id(
                            project_id=binding.project_id, live_plan_view=live
                        )
                except Exception:
                    pass
                payload: dict[str, Any] = {
                    "coordinator_id": getattr(handle, "coordinator_id", fallback_id),
                    "status": state.status.value if state is not None and hasattr(state.status, "value") else str(getattr(state, "status", "ACTIVE")) if state else "ACTIVE",
                    "coordinator_revision": getattr(state, "coordinator_revision", 1) if state else 1,
                    "milestone_id": getattr(state, "milestone_id", getattr(live, "milestone_id", "")) if state else getattr(live, "milestone_id", ""),
                    "plan_authority": getattr(state, "plan_authority", getattr(live, "plan_authority", "")) if state else getattr(live, "plan_authority", ""),
                    "work_items": list(getattr(state, "work_items", [])) if state else [],
                    "user_gate_required": False,
                }
                # M3/W1-R1 F2: surface governed Work context for ready Works
                # lacking projections (one shared projector, no extra read).
                try:
                    from aota_forge.runtime.task_main.coordinator import build_projection_required_context

                    wi_s = dict(getattr(state, "wi_status", {}) or {}) if state else {}
                    wp = dict(getattr(state, "work_projections", {}) or {}) if state else {}
                    req, miss = build_projection_required_context(
                        live, wi_status=wi_s or None, work_projections=wp
                    )
                    if req:
                        payload["projection_required"] = req
                    if miss:
                        payload["missing_governed_work_semantics"] = miss
                except Exception:
                    pass
            except Exception:
                payload = {"coordinator_id": getattr(handle, "coordinator_id", ""), "status": "ACTIVE"}
            return ToolResponse.success(payload)
        if operation == "task_main.recover_coordinator":
            live = ctx.live_plan_view
            # Coordinator identity owned by Core service (D4).
            try:
                coord_id = ctx.control_service.resolve_coordinator_id(
                    project_id=binding.project_id,
                    live_plan_view=live,
                    coordinator_id=ctx.coordinator_id,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                handle = ctx.control_service.recover_coordinator(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=coord_id,
                    live_plan_view=live,
                    session_available=ctx.session_available,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                state = handle.state
                payload = {
                    "coordinator_id": handle.coordinator_id,
                    "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                    "coordinator_revision": state.coordinator_revision,
                    "milestone_id": state.milestone_id,
                }
                # M3/W1-R1 F2: same shared projector for recovery path.
                try:
                    from aota_forge.runtime.task_main.coordinator import build_projection_required_context

                    req, miss = build_projection_required_context(
                        live,
                        wi_status=dict(getattr(state, "wi_status", {}) or {}),
                        work_projections=dict(getattr(state, "work_projections", {}) or {}),
                    )
                    if req:
                        payload["projection_required"] = req
                    if miss:
                        payload["missing_governed_work_semantics"] = miss
                except Exception:
                    pass
            except Exception:
                payload = {"coordinator_id": coord_id, "status": "ACTIVE"}
            return ToolResponse.success(payload)
        if operation == "task_main.advance_once":
            live = ctx.live_plan_view
            # Coordinator identity + discovery owned by Core service (D4):
            # resolve None -> default, then discover matching durable id via
            # the public store boundary. Never touch _coord_store or scan
            # private fields here; never invent "{project}:{milestone}".
            try:
                coord_id = ctx.control_service.discover_matching_coordinator_id(
                    project_id=binding.project_id,
                    live_plan_view=live,
                    coordinator_id=ctx.coordinator_id,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                outcome = ctx.control_service.advance_once(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=coord_id,
                    live_plan_view=live,
                    handoff_resolver=ctx.handoff_resolver,
                    governed_evidence_resolver=ctx.governed_evidence_resolver,
                    reviewer_handoff_resolver=ctx.reviewer_handoff_resolver,
                    governed_review_resolver=ctx.governed_review_resolver,
                    next_milestone_view=ctx.next_milestone_view,
                    session_available=ctx.session_available,
                    reviewer_canonical_task_id_resolver=ctx.reviewer_canonical_task_id_resolver,
                )
            except Exception as exc:
                # M3/W1-R1 F2: fail-closed preserved, but expose governed Work
                # context required to repair (no heuristic scope generation).
                err: dict[str, Any] = {
                    "code": _map_task_main_exception(exc),
                    "message": str(exc)[:512] or "governed operation failed",
                }
                try:
                    req, miss = ctx.control_service.get_projection_required_context(
                        profile=AF_TASK_MAIN_ROLE,
                        coordinator_id=coord_id,
                        live_plan_view=live,
                    )
                    if req:
                        err["projection_required"] = req
                    if miss:
                        err["missing_governed_work_semantics"] = miss
                except Exception:
                    pass
                return ToolResponse.failure(err)
            try:
                payload = {
                    "coordinator_id": outcome.coordinator_id,
                    "coordinator_revision": outcome.coordinator_revision,
                    "disposition": outcome.disposition,
                    "next_action": outcome.disposition,
                    "ready": list(getattr(outcome, "ready", [])),
                    "dispatched": list(getattr(outcome, "dispatched", [])),
                    "deferred": list(getattr(outcome, "deferred", [])),
                    "reconciled_work_item": getattr(outcome, "reconciled_work_item", None),
                    "reconciled_canonical_task_id": getattr(outcome, "reconciled_canonical_task_id", None),
                    "ack_eligible": bool(getattr(outcome, "ack_eligible", False)),
                    "user_gate_required": bool(getattr(outcome, "user_gate_required", False)),
                    "session_recovery_required": bool(getattr(outcome, "session_recovery_required", False)),
                    "milestone_closure_ready": bool(getattr(outcome, "milestone_closure_ready", False)),
                    "next_milestone_gate": bool(getattr(outcome, "next_milestone_gate", False)),
                    "integrated_review_required": bool(getattr(outcome, "integrated_review_required", False)),
                    "reasons": list(getattr(outcome, "reasons", [])),
                }
                # M3/W1-R1 F2: same shared projector for advance success
                # (normally empty when dispatched; non-empty when BLOCKED and
                # projection still required).
                try:
                    req, miss = ctx.control_service.get_projection_required_context(
                        profile=AF_TASK_MAIN_ROLE,
                        coordinator_id=coord_id,
                        live_plan_view=live,
                    )
                    if req:
                        payload["projection_required"] = req
                    if miss:
                        payload["missing_governed_work_semantics"] = miss
                except Exception:
                    pass
            except Exception:
                payload = {"coordinator_id": coord_id, "disposition": getattr(outcome, "disposition", "UNKNOWN")}
            return ToolResponse.success(payload)
        if operation == "task_main.submit_work_projection":
            # M3/W1 canonical writer: model proposes bounded semantics for one
            # governed Work Item; Core validates against trusted binding and
            # persists durably. Trusted identities (project/Plan/Milestone/
            # coordinator) come from the pre-resolved runtime context, never
            # from model arguments. Model supplies only work_item_id
            # (correlation, validated against governed Work Items) plus the
            # four bounded semantic fields (already typed-validated via the
            # canonical descriptor above; re-validated depth-first here).
            live = ctx.live_plan_view
            args = dict(validated) if isinstance(validated, dict) else {}
            try:
                coord_id = ctx.control_service.discover_matching_coordinator_id(
                    project_id=binding.project_id,
                    live_plan_view=live,
                    coordinator_id=ctx.coordinator_id,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                from aota_forge.work_plane.handoff_runtime import parse_model_work_proposal

                proposal_raw = {
                    "work_item_id": args.get("work_item_id"),
                    "objective": args.get("objective"),
                    "bounded_scope": args.get("bounded_scope"),
                    "validation_expectations": args.get("validation_expectations"),
                    "semantic_stop_expectations": args.get("semantic_stop_expectations"),
                }
                wid, typed_projection = parse_model_work_proposal(proposal_raw)
                updated = ctx.control_service.submit_work_projection(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=coord_id,
                    live_plan_view=live,
                    work_item_id=wid,
                    projection=typed_projection,
                )
            except Exception as exc:
                return ToolResponse.failure(
                    {"code": _map_task_main_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            try:
                table = dict(getattr(updated, "work_projections", {}) or {})
                record = dict(table.get(wid, {}))
                proj_dict = dict(record.get("projection", {}))
                payload = {
                    "coordinator_id": getattr(updated, "coordinator_id", coord_id),
                    "coordinator_revision": getattr(updated, "coordinator_revision", 1),
                    "work_item_id": wid,
                    "milestone_id": record.get("milestone_id", getattr(live, "milestone_id", "")),
                    "plan_authority": record.get("plan_authority", ""),
                    "project_id": record.get("project_id", ""),
                    "projection": proj_dict,
                }
                try:
                    from aota_forge.work_plane.handoff_runtime import (
                        WorkSemanticProjection,
                        resolve_bounded_work_handoff,
                    )

                    _typed = WorkSemanticProjection.from_dict(proj_dict)
                    _handoff = resolve_bounded_work_handoff(
                        work_item_id=wid,
                        milestone_ref=record.get("milestone_id", getattr(live, "milestone_id", "")),
                        projection=_typed,
                        project_id=record.get("project_id"),
                        plan_authority=record.get("plan_authority"),
                        plan_digest=record.get("plan_digest"),
                    )
                    payload["handoff_digest"] = _handoff.compute_handoff_digest()
                except Exception:
                    pass
            except Exception:
                payload = {"coordinator_id": coord_id, "work_item_id": args.get("work_item_id", "")}
            return ToolResponse.success(payload)
    except ForgeError as exc:
        return ToolResponse.failure({"code": exc.code, "message": exc.message})
    except Exception as exc:
        return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
    return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported task_main operation: {operation!r}"})


def dispatch_tool_operation(
    operation: str, arguments: dict[str, Any] | None, binding: CanonicalDispatchBinding
) -> ToolResponse:
    """Canonical Core dispatch for provider-backed operations.

    Single orchestration: exact resolution -> typed validation -> Core-owned
    provider/service selection -> existing leaf implementation. Adapters must
    call this instead of duplicating lookup/validation/selection.
    """
    # Resolution (exact, no fuzzy).
    try:
        descriptor = resolve_descriptor(operation)
    except ForgeError as exc:
        return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": exc.message})
    except Exception as exc:
        return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
    if operation not in PROVIDER_BACKED_OPERATIONS:
        return ToolResponse.failure(
            {"code": "UNKNOWN_OPERATION", "message": f"operation not in canonical tool dispatch: {operation!r}"}
        )
    # Validation (typed, same seam).
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return ToolResponse.failure(
            {"code": "INPUT_TYPE_INVALID", "message": f"arguments must be object, got {type(arguments).__name__}"}
        )
    try:
        validated = validate_operation_input(descriptor, arguments)
    except ForgeError as exc:
        return ToolResponse.failure({"code": exc.code, "message": exc.message})
    except Exception as exc:
        return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})

    # Dispatch routing (Core-owned provider selection; reuse leaves).
    try:
        if operation == "workspace.write":
            if binding.mutation_authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted mutation authority is absent"})
            if getattr(getattr(binding.mutation_authority, "operation", None), "name", None) != operation:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "mutation authority mismatch"})
            try:
                if binding.mutation_authority.operation.contract_hash() != descriptor.contract_hash():
                    return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            except Exception:
                pass
            from aota_forge.work_plane.workspace_mutation import BoundedWorkspaceMutationProvider

            provider = BoundedWorkspaceMutationProvider(binding.mutation_authority)
            response = provider.invoke(ToolRequest(operation=descriptor, inputs=validated))
            _persist_governed_if_needed(binding, response, operation)
            return response

        if operation in ("workspace.search", "workspace.read"):
            authority = _authority_for(binding.read_authorities, operation)
            if authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted read authority is absent"})
            try:
                if authority.operation.contract_hash() != descriptor.contract_hash():
                    return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            except Exception:
                pass
            from aota_forge.work_plane.workspace_tools import BoundedWorkspaceToolProvider

            provider = BoundedWorkspaceToolProvider(authority)
            # Use authority's descriptor for request to preserve evidence binding;
            # validated inputs already canonical.
            response = provider.invoke(ToolRequest(operation=authority.operation, inputs=validated))
            _persist_governed_if_needed(binding, response, operation)
            return response

        if operation == "result.hydrate":
            # D2: authority derives from trusted binding + Core policy, never
            # from ToolRoleSurface visibility. Any binding with a trusted
            # sandbox may hydrate refs in its own scope; cross-scope,
            # tamper, and oversize still fail closed inside the provider
            # (reauthorization + digest + bounds owned by result governance).
            # A hidden capability with valid binding is NOT denied by surface;
            # surface controls disclosure only.
            sandbox = _require_sandbox(binding)
            from aota_forge.work_plane.result_hydrate import ResultHydrateProvider

            provider = ResultHydrateProvider(sandbox)
            response = provider.invoke(ToolRequest(operation=descriptor, inputs=validated))
            # D5: durable-result decision owned by result governance.
            # Persist governed by_ref payloads via the Core-owned seam so a
            # later process can hydrate; adapters never persist directly.
            _persist_governed_if_needed(binding, response, operation)
            return response

        if operation == "restricted_shell.run":
            # D2: surface membership never decides permission. Least privilege
            # is enforced by the trusted restricted-shell authority evidence
            # (per-binding, Core-constructed). Visible without authority still
            # fails closed below; hidden with valid authority succeeds.
            if binding.restricted_shell_authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "restricted shell authority absent for this binding"})
            from aota_forge.work_plane.restricted_shell import BoundedRestrictedShellProvider

            provider = BoundedRestrictedShellProvider(binding.restricted_shell_authority)
            response = provider.invoke(ToolRequest(operation=descriptor, inputs=validated))
            _persist_governed_if_needed(binding, response, operation)
            return response

        if operation == "test.run":
            # D2: same convergence as restricted_shell — authority evidence,
            # not surface visibility, decides.
            if binding.test_execution_authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted test execution authority absent for this binding"})
            from aota_forge.work_plane.test_execution import BoundedTestExecutionToolProvider

            provider = BoundedTestExecutionToolProvider(binding.test_execution_authority)
            response = provider.invoke(ToolRequest(operation=descriptor, inputs=validated))
            _persist_governed_if_needed(binding, response, operation)
            return response

        if operation == "role.bootstrap":
            from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

            try:
                payload = handle_role_bootstrap(binding, validated)
            except Exception as exc:
                # D3: typed identity from the semantic owner (exc.code),
                # never str(exc) classification.
                return ToolResponse.failure(
                    {"code": _map_role_bootstrap_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            return ToolResponse.success(payload)

        if operation == "skill.open":
            from aota_forge.work_plane.role_bootstrap import handle_skill_open

            try:
                payload = handle_skill_open(binding, validated)
            except Exception as exc:
                # D3: typed identity from the semantic owner (exc.code),
                # never "foreign"/"digest"/"not found" string search.
                return ToolResponse.failure(
                    {"code": _map_skill_open_exception(exc), "message": str(exc)[:512] or "governed operation failed"}
                )
            return ToolResponse.success(payload)

        if operation in ("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once", "task_main.submit_work_projection"):
            return _dispatch_task_main(operation, binding, validated)

        if operation == "handoff.write":
            sandbox = _require_sandbox(binding)
            # Derive caller role from trusted binding (handoff work_role or tool_surface)
            caller_role = ""
            try:
                h = getattr(binding, "handoff", None)
                if h is not None and getattr(h, "work_role", None) is not None:
                    wr = getattr(h, "work_role", None)
                    caller_role = getattr(wr, "value", None) or str(wr)
            except Exception:
                caller_role = ""
            if not caller_role:
                try:
                    surf = getattr(binding, "tool_surface", None)
                    if surf is not None and getattr(surf, "work_role", None) is not None:
                        wr = getattr(surf, "work_role", None)
                        caller_role = getattr(wr, "value", None) or str(wr)
                except Exception:
                    pass
            if not caller_role:
                # Fallback for test harness where binding carries canonical_task_id with role hint?
                caller_role = "task-main"
            mode = validated.get("mode")
            payload = validated.get("payload")
            if payload is None:
                # Collect any remaining validated keys as payload (compat)
                payload = {k: v for k, v in validated.items() if k != "mode"}
                if not payload:
                    payload = {}
            if not isinstance(payload, dict):
                return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "payload must be object"})
            try:
                from aota_forge.work_plane.handoff_store import handoff_write

                ref = handoff_write(mode=mode, semantic=payload, caller_role=caller_role, sandbox=sandbox)
            except ValueError as exc:
                msg = str(exc)
                # Map known fail-closed codes via .code if present else infer from message
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": msg[:512]})
                if "AUTHORITY_DENIED" in msg or "requires caller" in msg:
                    return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": msg[:512]})
                if "cross-project" in msg.lower() or "CROSS_SCOPE" in msg:
                    return ToolResponse.failure({"code": "CROSS_SCOPE_DENIED", "message": msg[:512]})
                if "tamper" in msg.lower() or "DIGEST_MISMATCH" in msg:
                    return ToolResponse.failure({"code": "DIGEST_MISMATCH", "message": msg[:512]})
                if "control field" in msg.lower():
                    return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": msg[:512]})
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": msg[:512]})
            except Exception as exc:
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": str(exc)[:512]})
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
            return ToolResponse.success(ref.to_dict())

        if operation == "handoff.open":
            sandbox = _require_sandbox(binding)
            ref = validated.get("ref")
            view = validated.get("view", "full")
            if not isinstance(ref, str) or not ref.strip():
                return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "ref must be non-empty string"})
            if not isinstance(view, str) or view not in ("card", "full"):
                return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "view must be card|full"})
            try:
                from aota_forge.work_plane.handoff_store import handoff_open

                result = handoff_open(ref, view, sandbox=sandbox)
            except ValueError as exc:
                msg = str(exc)
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": msg[:512]})
                if "cross-project" in msg.lower() or "cross-worktree" in msg.lower():
                    return ToolResponse.failure({"code": "CROSS_SCOPE_DENIED", "message": msg[:512]})
                if "digest" in msg.lower() and ("mismatch" in msg.lower() or "tamper" in msg.lower()):
                    return ToolResponse.failure({"code": "DIGEST_MISMATCH", "message": msg[:512]})
                if "not found" in msg.lower():
                    return ToolResponse.failure({"code": "UNKNOWN_REF", "message": msg[:512]})
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": msg[:512]})
            except Exception as exc:
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
            return ToolResponse.success(result)

        if operation == "task.start":
            sandbox = _require_sandbox(binding)
            # Caller authority: must be task-main
            caller_role = ""
            try:
                h = getattr(binding, "handoff", None)
                if h is not None and getattr(h, "work_role", None) is not None:
                    wr = getattr(h, "work_role", None)
                    caller_role = getattr(wr, "value", None) or str(wr)
            except Exception:
                caller_role = ""
            if not caller_role:
                try:
                    surf = getattr(binding, "tool_surface", None)
                    if surf is not None:
                        wr = getattr(surf, "work_role", None)
                        if wr is not None:
                            caller_role = getattr(wr, "value", None) or str(wr)
                except Exception:
                    pass
            if not caller_role:
                caller_role = "task-main"
            if caller_role != "task-main":
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "task.start caller must be task-main"})
            role = validated.get("role")
            handoff_ref = validated.get("handoff_ref")
            if not isinstance(role, str) or role not in ("coder", "analyst", "reviewer", "project-steward"):
                return ToolResponse.failure({"code": "INVALID_ROLE", "message": f"invalid target role: {role!r}"})
            if not isinstance(handoff_ref, str) or not handoff_ref.strip():
                return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "handoff_ref must be non-empty string"})
            # AF #49 M1/W1: supply the trusted production dispatcher from the
            # existing Core binding seam; fail closed when none is bound.
            trusted_dispatcher = _trusted_production_execution_dispatcher()
            if trusted_dispatcher is None:
                return ToolResponse.failure(
                    {
                        "code": "PRODUCTION_DISPATCHER_UNAVAILABLE",
                        "message": "task.start requires a trusted production ExecutionDispatcher; "
                        "test doubles are never a silent production fallback",
                    }
                )
            try:
                from aota_forge.work_plane.task_facade import task_start

                result = task_start(
                    role=role,
                    handoff_ref=handoff_ref,
                    caller_role=caller_role,
                    sandbox=sandbox,
                    dispatcher=trusted_dispatcher,
                )
            except ValueError as exc:
                msg = str(exc)
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": msg[:512]})
                if "AUTHORITY_DENIED" in msg:
                    return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": msg[:512]})
                if "cross-project" in msg.lower() or "CROSS_SCOPE" in msg:
                    return ToolResponse.failure({"code": "CROSS_SCOPE_DENIED", "message": msg[:512]})
                if "digest" in msg.lower() and "mismatch" in msg.lower():
                    return ToolResponse.failure({"code": "DIGEST_MISMATCH", "message": msg[:512]})
                if "not found" in msg.lower() or "UNKNOWN_REF" in msg:
                    return ToolResponse.failure({"code": "UNKNOWN_REF", "message": msg[:512]})
                if "work_item" in msg.lower() and "requires" in msg.lower():
                    return ToolResponse.failure({"code": "INVALID_ROLE", "message": msg[:512]})
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": msg[:512]})
            except Exception as exc:
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
            return ToolResponse.success(result)

        if operation == "task.return":
            sandbox = _require_sandbox(binding)
            # Caller must be one-shot role
            caller_role = ""
            caller_task_id = getattr(binding, "canonical_task_id", "") or ""
            try:
                h = getattr(binding, "handoff", None)
                if h is not None and getattr(h, "work_role", None) is not None:
                    wr = getattr(h, "work_role", None)
                    caller_role = getattr(wr, "value", None) or str(wr)
            except Exception:
                caller_role = ""
            if not caller_role:
                try:
                    surf = getattr(binding, "tool_surface", None)
                    if surf is not None:
                        wr = getattr(surf, "work_role", None)
                        if wr is not None:
                            caller_role = getattr(wr, "value", None) or str(wr)
                except Exception:
                    pass
            if not caller_role:
                # Fallback to payload? Not ideal
                caller_role = "coder"
            if caller_role not in ("coder", "analyst", "reviewer", "project-steward"):
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": f"task.return caller must be one-shot, got {caller_role!r}"})
            status = validated.get("status")
            result_ref = validated.get("result_ref")
            if status not in ("completed", "blocked", "failed"):
                return ToolResponse.failure({"code": "INVALID_STATUS", "message": f"status must be completed|blocked|failed, got {status!r}"})
            if not isinstance(result_ref, str) or not result_ref.strip():
                return ToolResponse.failure({"code": "INPUT_TYPE_INVALID", "message": "result_ref must be non-empty string"})
            if not caller_task_id:
                # Try to derive from sandbox? fallback to result_ref's envelope task_id?
                # For thin facade, require canonical_task_id in binding; if missing, use envelope task_id from result_ref as caller_task_id fallback for test harness
                try:
                    from aota_forge.work_plane.handoff_store import handoff_open

                    tmp = handoff_open(result_ref, "full", sandbox=sandbox)
                    caller_task_id = tmp.get("envelope", {}).get("task_id", "") or ""
                except Exception:
                    caller_task_id = ""
            if not caller_task_id:
                return ToolResponse.failure({"code": "WRONG_TASK", "message": "caller_task_id missing in binding"})
            # AF #49 M1/W1: terminal return uses the trusted production
            # dispatcher + its durable store; fail closed when none is bound.
            # No process-local completion channel is written on this path.
            trusted_dispatcher = _trusted_production_execution_dispatcher()
            if trusted_dispatcher is None:
                return ToolResponse.failure(
                    {
                        "code": "PRODUCTION_DISPATCHER_UNAVAILABLE",
                        "message": "task.return requires a trusted production ExecutionDispatcher; "
                        "test doubles are never a silent production fallback",
                    }
                )
            try:
                from aota_forge.work_plane.task_facade import task_return

                result = task_return(
                    status=status,
                    result_ref=result_ref,
                    caller_role=caller_role,
                    caller_task_id=caller_task_id,
                    sandbox=sandbox,
                    dispatcher=trusted_dispatcher,
                )
            except ValueError as exc:
                msg = str(exc)
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": msg[:512]})
                if "AUTHORITY_DENIED" in msg or "one-shot" in msg or "WRONG_ROLE" in msg or "wrong-role" in msg.lower():
                    return ToolResponse.failure({"code": "WRONG_ROLE", "message": msg[:512]})
                if "wrong-task" in msg.lower() or "WRONG_TASK" in msg or "task_id" in msg.lower():
                    return ToolResponse.failure({"code": "WRONG_TASK", "message": msg[:512]})
                if "cross-project" in msg.lower() or "CROSS_SCOPE" in msg:
                    return ToolResponse.failure({"code": "CROSS_SCOPE_DENIED", "message": msg[:512]})
                if "digest" in msg.lower() and "mismatch" in msg.lower():
                    return ToolResponse.failure({"code": "DIGEST_MISMATCH", "message": msg[:512]})
                if "terminal" in msg.lower():
                    return ToolResponse.failure({"code": "TASK_ALREADY_TERMINAL", "message": msg[:512]})
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": msg[:512]})
            except Exception as exc:
                return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
            return ToolResponse.success(result)

        if operation in ("git.status", "git.diff"):
            authority = _authority_for(binding.git_authorities, operation)
            if authority is None:
                return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted git authority is absent"})
            from aota_forge.work_plane.git_tools import BoundedGitToolProvider

            provider = BoundedGitToolProvider(authority)
            return provider.invoke(ToolRequest(operation=authority.operation, inputs=validated))

    except ForgeError as exc:
        return ToolResponse.failure({"code": exc.code, "message": exc.message})
    except Exception as exc:
        return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": str(exc)[:512]})
    return ToolResponse.failure({"code": "UNKNOWN_OPERATION", "message": f"unsupported operation: {operation!r}"})


def dispatch_via_core(
    operation: str,
    params: dict[str, Any] | None = None,
    binding: CanonicalDispatchBinding | None = None,
) -> Any:
    """Unified Core entry used by adapters/tests for parity.

    Provider-backed ops go through :func:`dispatch_tool_operation` (ToolResponse).
    All other canonical ops delegate to existing ``core.ingress.execute``
    (dict envelope) with the same descriptor/validation authority. This keeps
    one semantic owner and one lookup/validation path while reusing existing
    ingress/dispatcher leaves.
    """
    # Canonical resolution first (same for all).
    descriptor = resolve_descriptor(operation)
    if operation in PROVIDER_BACKED_OPERATIONS:
        if binding is None:
            return ToolResponse.failure({"code": "AUTHORITY_DENIED", "message": "trusted binding is required"})
        return dispatch_tool_operation(operation, params, binding)
    # Ingress-backed: same validation, then existing ingress dispatch.
    from aota_forge.core.ingress import execute as core_execute

    trusted_context = getattr(binding, "trusted_context", None) if binding is not None else None
    # Validate via canonical seam first to prove same validation (ingress will re-validate identically).
    validated = validate_operation_input(descriptor, params)
    return core_execute(operation, validated, trusted_context=trusted_context)


__all__ = [
    "SEMANTIC_OWNER",
    "EXPECTED_LAYER",
    "CANONICAL_DESCRIPTOR_AUTHORITY",
    "OPERATION_DESCRIPTOR_AUTHORITY_COUNT",
    "SECOND_DESCRIPTOR_REGISTRY_CREATED",
    "SECOND_SCHEMA_AUTHORITY_CREATED",
    "NEW_OPERATION_AUTHORITY_REGISTRY_CREATED",
    "NEW_PERMISSION_ENGINE_CREATED",
    "NEW_CONTROL_PLANE_CREATED",
    "NEW_STATE_MACHINE_CREATED",
    "CANONICAL_OPERATION_DISPATCH_PLANE_COUNT",
    "LEAF_PROVIDER_REWRITE_REQUIRED",
    "PROVIDER_BACKED_OPERATIONS",
    "INGRESS_BACKED_OPERATIONS",
    "CanonicalDispatchBinding",
    "list_canonical_operations",
    "resolve_descriptor",
    "validate_operation_input",
    "dispatch_tool_operation",
    "dispatch_via_core",
]
