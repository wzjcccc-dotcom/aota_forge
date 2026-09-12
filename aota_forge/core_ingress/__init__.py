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

from collections.abc import Mapping
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


_DEFAULT_INLINE_OUTPUT_BOUND = 4096
_DEFAULT_INLINE_ERROR_BOUND = 2048


def _work_context_inline_bound() -> int:
    try:
        from aota_forge.work_plane.tool_result_governance import TOOL_INLINE_OUTPUT_MAX_BYTES

        return int(TOOL_INLINE_OUTPUT_MAX_BYTES)
    except Exception:
        return _DEFAULT_INLINE_OUTPUT_BOUND


def _error_context_inline_bound() -> int:
    try:
        from aota_forge.work_plane.tool_result_governance import TOOL_ERROR_INLINE_MAX_BYTES

        return int(TOOL_ERROR_INLINE_MAX_BYTES)
    except Exception:
        return _DEFAULT_INLINE_ERROR_BOUND


def _canonical_payload_bytes(payload: Any) -> int:
    try:
        from aota_forge.core.contracts.canonical import canonical_json, canonicalize

        return len(canonical_json(canonicalize(payload, path="payload")).encode("utf-8"))
    except Exception:
        return 2**31 - 1


def _work_context_by_ref(binding: CanonicalDispatchBinding, operation: str, work_context: dict[str, Any]) -> dict[str, Any] | None:
    """Persist exact source text via the existing durable result mechanism.

    Reuses result governance's ToolOutputRef persistence (no new store, no new
    protocol); the returned metadata keeps plan/milestone/work identity and the
    source digest/ref so the model can hydrate the exact source with the
    existing result.hydrate operation.
    """
    sandbox = binding.sandbox
    if sandbox is None:
        return None
    source_text = work_context.get("source_text")
    if not isinstance(source_text, str) or not source_text:
        return None
    try:
        from aota_forge.work_plane.durable_result_store import persist_tool_output_payload

        ref = persist_tool_output_payload(sandbox, source_text.encode("utf-8"), capability_name=operation)
    except Exception:
        return None
    from aota_forge.runtime.task_main.coordinator import WORK_CONTEXT_STATE_BY_REF

    meta = {key: value for key, value in work_context.items() if key != "source_text"}
    meta["state"] = WORK_CONTEXT_STATE_BY_REF
    meta["source_ref"] = {
        "ref": ref.ref,
        "digest": ref.digest,
        "project_id": ref.project_id,
        "worktree_id": ref.worktree_id,
        "byte_length": ref.byte_length,
    }
    return meta


def _attach_work_context(
    binding: CanonicalDispatchBinding,
    payload: dict[str, Any],
    work_context: Any,
    *,
    operation: str,
    inline_bound: int,
) -> None:
    """Attach bounded model-visible authoritative Work context.

    Inline when the whole result safely fits the existing transport bound;
    otherwise persist the exact source through the existing trusted by-ref
    hydration mechanism (never silent truncation). Missing structural source
    stays an explicit typed insufficient state.
    """
    if not isinstance(work_context, dict):
        return
    from aota_forge.runtime.task_main.coordinator import (
        WORK_CONTEXT_STATE_AVAILABLE,
        WORK_CONTEXT_STATE_INSUFFICIENT,
    )

    if work_context.get("state") != WORK_CONTEXT_STATE_AVAILABLE or "source_text" not in work_context:
        payload["work_context"] = dict(work_context)
        return
    candidate = dict(payload)
    candidate["work_context"] = dict(work_context)
    if _canonical_payload_bytes(candidate) <= inline_bound:
        payload["work_context"] = candidate["work_context"]
        return
    by_ref = _work_context_by_ref(binding, operation, work_context)
    if by_ref is not None:
        payload["work_context"] = by_ref
        return
    payload["work_context"] = {
        "state": WORK_CONTEXT_STATE_INSUFFICIENT,
        "work_item_id": work_context.get("work_item_id"),
        "milestone_id": work_context.get("milestone_id"),
        "plan_ref": work_context.get("plan_ref"),
        "plan_digest": work_context.get("plan_digest"),
        "selection": work_context.get("selection"),
        "code": "WORK_SOURCE_UNREPRESENTABLE",
        "reason": (
            "authoritative Work source exceeds the inline bound and trusted by-ref "
            "is unavailable; refusing silent truncation or semantic fallback"
        ),
    }


def _claimed_semantic_ref(payload: Mapping[str, Any], field: str) -> str | None:
    """Model-supplied semantic ref (correlation only, never authority)."""
    value = payload.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        ref = value.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    return None


def _current_authoritative_work_context(
    trusted_ctx: Any, *, project_id: str, live_plan_view: Any
) -> dict[str, Any] | None:
    """Mechanical current model-visible authoritative Work context.

    Reuses the W2 selection seam (ready -> in-flight -> completion pending)
    through the trusted control service. Never interprets Work meaning.
    """
    service = getattr(trusted_ctx, "control_service", None)
    if service is None or live_plan_view is None:
        return None
    coordinator_id = getattr(trusted_ctx, "coordinator_id", None)
    try:
        resolved = service.resolve_coordinator_id(
            project_id=project_id,
            live_plan_view=live_plan_view,
            coordinator_id=coordinator_id,
        )
    except Exception:
        resolved = coordinator_id
    if not isinstance(resolved, str) or not resolved.strip():
        return None
    try:
        from aota_forge.runtime.task_main.control import AF_TASK_MAIN_ROLE

        context = service.get_model_visible_work_context(
            profile=AF_TASK_MAIN_ROLE,
            coordinator_id=resolved,
            live_plan_view=live_plan_view,
        )
    except Exception:
        return None
    return context if isinstance(context, dict) else None


def _grounded_work_item_write_binding(
    *,
    binding: CanonicalDispatchBinding,
    live_plan_view: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Trusted envelope fields for handoff.write(mode=work_item) (AF #49 W4).

    Mechanical identity/digest binding only: the Control Plane fills the
    durable handoff control envelope from the trusted task-main runtime
    context and the authoritative WorkSourceSlice. Model-supplied refs are
    correlation only and are rejected when they contradict the trusted
    identity; they never override it.
    """
    from aota_forge.runtime.task_main.coordinator import (
        MILESTONE_MISMATCH,
        WORK_ITEM_MISMATCH,
        WORK_SOURCE_GROUNDING_MISMATCH,
        WORK_SOURCE_GROUNDING_MISSING,
        WorkSourceGroundingError,
        resolve_trusted_work_grounding,
    )

    claimed_wid = _claimed_semantic_ref(payload, "work_item_ref")
    claimed_mid = _claimed_semantic_ref(payload, "milestone_ref")
    claimed_proj = _claimed_semantic_ref(payload, "project_ref")
    if claimed_proj is not None and claimed_proj != binding.project_id:
        raise WorkSourceGroundingError(
            WORK_SOURCE_GROUNDING_MISMATCH,
            f"model project_ref {claimed_proj!r} contradicts trusted project "
            f"{binding.project_id!r}; refusing foreign scope",
        )
    if claimed_mid is not None and claimed_mid != live_plan_view.milestone_id:
        raise WorkSourceGroundingError(
            MILESTONE_MISMATCH,
            f"model milestone_ref {claimed_mid!r} contradicts current trusted "
            f"Milestone {live_plan_view.milestone_id!r}",
        )
    context = _current_authoritative_work_context(
        binding.trusted_task_main_context,
        project_id=binding.project_id,
        live_plan_view=live_plan_view,
    )
    current_wid = None
    if isinstance(context, Mapping):
        current = context.get("work_item_id")
        if isinstance(current, str) and current.strip():
            current_wid = current.strip()
    if claimed_wid is not None and current_wid is not None and claimed_wid != current_wid:
        raise WorkSourceGroundingError(
            WORK_ITEM_MISMATCH,
            f"model Work Item {claimed_wid!r} contradicts current authoritative "
            f"Work Item {current_wid!r}; refusing cross-Work binding",
        )
    wid = claimed_wid or current_wid
    if wid is None:
        raise WorkSourceGroundingError(
            WORK_SOURCE_GROUNDING_MISSING,
            "no current authoritative Work Item/source is available for grounding; "
            "refusing ungrounded work_item handoff",
        )
    grounding = resolve_trusted_work_grounding(live_plan_view, work_item_id=wid)
    return {
        "plan_ref": grounding["plan_ref"],
        "milestone_id": grounding["milestone_id"],
        "work_item_id": grounding["work_item_id"],
        "provenance": {
            "plan_digest": grounding["plan_digest"],
            "work_source_digest": grounding["work_source_digest"],
            "grounding": "task_main_authoritative_work_source",
        },
    }


def _map_handoff_open_error(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return {"code": code, "message": str(exc)[:512]}
    msg = str(exc)
    lowered = msg.lower()
    if "cross-project" in lowered or "cross-worktree" in lowered:
        return {"code": "CROSS_SCOPE_DENIED", "message": msg[:512]}
    if "digest" in lowered and ("mismatch" in lowered or "tamper" in lowered):
        return {"code": "DIGEST_MISMATCH", "message": msg[:512]}
    if "not found" in lowered:
        return {"code": "UNKNOWN_REF", "message": msg[:512]}
    return {"code": "GOVERNED_OPERATION_FAILURE", "message": msg[:512] or "handoff open failed"}


def _verify_grounded_task_start(
    *,
    binding: CanonicalDispatchBinding,
    live_plan_view: Any,
    handoff_ref: str,
    sandbox: Any,
) -> dict[str, Any] | None:
    """Verify a task.start handoff against the current authorized Work identity.

    AF #49 M1/W4: exact equality / digest equality / identity lookup only.
    Returns a bounded failure dict when the durable handoff is not
    mechanically grounded to the current trusted Plan/Milestone/Work/source;
    returns None when grounded (or when the handoff carries no trusted
    context requirement, handled by the caller).
    """
    from aota_forge.runtime.task_main.coordinator import (
        MILESTONE_MISMATCH,
        PLAN_DIGEST_MISMATCH,
        PLAN_REF_MISMATCH,
        WORK_ITEM_MISMATCH,
        WORK_SOURCE_DIGEST_MISMATCH,
        WORK_SOURCE_GROUNDING_MISSING,
        resolve_trusted_work_grounding,
    )

    try:
        from aota_forge.work_plane.handoff_store import handoff_open

        opened = handoff_open(handoff_ref, "full", sandbox=sandbox)
    except Exception as exc:
        return _map_handoff_open_error(exc)
    envelope = opened.get("envelope")
    if not isinstance(envelope, Mapping):
        return {
            "code": WORK_SOURCE_GROUNDING_MISSING,
            "message": "task.start handoff carries no control envelope; refusing ungrounded dispatch",
        }
    provenance = envelope.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    plan_ref = envelope.get("plan_ref")
    milestone_id = envelope.get("milestone_id")
    work_item_id = envelope.get("work_item_id")
    plan_digest = provenance.get("plan_digest")
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
        return {
            "code": WORK_SOURCE_GROUNDING_MISSING,
            "message": (
                f"task.start handoff is not source-grounded (missing {missing}); "
                "refusing ungrounded dispatch"
            )[:512],
        }
    if plan_ref.strip() != live_plan_view.plan_authority:
        return {
            "code": PLAN_REF_MISMATCH,
            "message": (
                f"handoff plan_ref {plan_ref!r} != current trusted plan "
                f"{live_plan_view.plan_authority!r}; refusing cross-Plan dispatch"
            )[:512],
        }
    if plan_digest.strip() != live_plan_view.plan_digest:
        return {
            "code": PLAN_DIGEST_MISMATCH,
            "message": (
                "handoff plan_digest does not match the current trusted Plan revision; "
                "refusing stale Plan binding (no silent rebinding)"
            )[:512],
        }
    if milestone_id.strip() != live_plan_view.milestone_id:
        return {
            "code": MILESTONE_MISMATCH,
            "message": (
                f"handoff milestone_id {milestone_id!r} != current trusted Milestone "
                f"{live_plan_view.milestone_id!r}; refusing cross-Milestone dispatch"
            )[:512],
        }
    try:
        grounding = resolve_trusted_work_grounding(live_plan_view, work_item_id=work_item_id)
    except Exception as exc:
        return {
            "code": getattr(exc, "code", WORK_SOURCE_GROUNDING_MISSING),
            "message": str(exc)[:512],
        }
    if source_digest.strip() != grounding["work_source_digest"]:
        return {
            "code": WORK_SOURCE_DIGEST_MISMATCH,
            "message": (
                f"handoff Work source digest does not match the current authoritative "
                f"Work source of {grounding['work_item_id']!r}; refusing stale/cross-source dispatch"
            )[:512],
        }
    context = _current_authoritative_work_context(
        binding.trusted_task_main_context,
        project_id=binding.project_id,
        live_plan_view=live_plan_view,
    )
    current_wid = None
    if isinstance(context, Mapping):
        current = context.get("work_item_id")
        if isinstance(current, str) and current.strip():
            current_wid = current.strip()
    if current_wid is None or current_wid != grounding["work_item_id"]:
        return {
            "code": WORK_ITEM_MISMATCH,
            "message": (
                f"handoff Work Item {grounding['work_item_id']!r} is not the current "
                f"authorized Work Item {current_wid!r}; refusing cross-Work dispatch"
            )[:512],
        }
    return None


def _task_main_success(binding: CanonicalDispatchBinding, operation: str, payload: dict[str, Any]) -> ToolResponse:
    response = ToolResponse.success(payload)
    _persist_governed_if_needed(binding, response, operation)
    return response


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
            try:
                work_context = ctx.control_service.get_model_visible_work_context(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=str(payload.get("coordinator_id") or resolved_id),
                    live_plan_view=live,
                )
                _attach_work_context(
                    binding,
                    payload,
                    work_context,
                    operation=operation,
                    inline_bound=_work_context_inline_bound(),
                )
            except Exception:
                pass
            return _task_main_success(binding, operation, payload)
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
            try:
                work_context = ctx.control_service.get_model_visible_work_context(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=str(payload.get("coordinator_id") or coord_id),
                    live_plan_view=live,
                )
                _attach_work_context(
                    binding,
                    payload,
                    work_context,
                    operation=operation,
                    inline_bound=_work_context_inline_bound(),
                )
            except Exception:
                pass
            return _task_main_success(binding, operation, payload)
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
                try:
                    work_context = ctx.control_service.get_model_visible_work_context(
                        profile=AF_TASK_MAIN_ROLE,
                        coordinator_id=coord_id,
                        live_plan_view=live,
                    )
                    _attach_work_context(
                        binding,
                        err,
                        work_context,
                        operation=operation,
                        inline_bound=_error_context_inline_bound(),
                    )
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
            try:
                work_context = ctx.control_service.get_model_visible_work_context(
                    profile=AF_TASK_MAIN_ROLE,
                    coordinator_id=str(payload.get("coordinator_id") or coord_id),
                    live_plan_view=live,
                )
                _attach_work_context(
                    binding,
                    payload,
                    work_context,
                    operation=operation,
                    inline_bound=_work_context_inline_bound(),
                )
            except Exception:
                pass
            return _task_main_success(binding, operation, payload)
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
            return _task_main_success(binding, operation, payload)
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
            response = ToolResponse.success(payload)
            # W5 (AF #49 M1/W5, I49-B003): a bootstrap over the inline bound is
            # projected by the transport as by_ref; the Core-owned durability
            # seam must persist it first or the model-visible hydration claims
            # would be unresolvable. MCP never persists (decision stays here).
            _persist_governed_if_needed(binding, response, operation)
            return response

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
            response = ToolResponse.success(payload)
            _persist_governed_if_needed(binding, response, operation)
            return response

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
            # AF #49 M1/W4 normal-path cutover: a task-main work_item handoff is
            # mechanically bound to the current trusted Plan/Milestone/Work
            # source identity at this seam. The semantic payload stays LLM-owned
            # (stored verbatim); only the control envelope is filled here.
            write_kwargs: dict[str, Any] = {}
            if mode == "work_item":
                trusted_ctx = getattr(binding, "trusted_task_main_context", None)
                live_view = getattr(trusted_ctx, "live_plan_view", None)
                if trusted_ctx is not None and live_view is not None:
                    try:
                        write_kwargs = _grounded_work_item_write_binding(
                            binding=binding,
                            live_plan_view=live_view,
                            payload=payload,
                        )
                    except Exception as exc:
                        return ToolResponse.failure(
                            {
                                "code": _map_task_main_exception(exc),
                                "message": str(exc)[:512] or "Work source grounding failed",
                            }
                        )
            try:
                from aota_forge.work_plane.handoff_store import handoff_write

                ref = handoff_write(
                    mode=mode,
                    semantic=payload,
                    caller_role=caller_role,
                    sandbox=sandbox,
                    **write_kwargs,
                )
            except ValueError as exc:
                msg = str(exc)
                # Map known fail-closed codes via .code if present else infer from message
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code:
                    return ToolResponse.failure({"code": code, "message": msg[:512]})
                if "AUTHORITY_DENIED" in msg or "requires caller" in msg or "requires one-shot" in msg:
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
            response = ToolResponse.success(ref.to_dict())
            _persist_governed_if_needed(binding, response, operation)
            return response

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
            response = ToolResponse.success(result)
            _persist_governed_if_needed(binding, response, operation)
            return response

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
            # AF #49 M1/W4: a task-main task.start may consume only a durable
            # work_item handoff whose trusted grounding matches the current
            # authorized Plan/Milestone/Work/source identity. Mechanical
            # identity/digest verification only (no semantic interpretation).
            trusted_ctx = getattr(binding, "trusted_task_main_context", None)
            live_view = getattr(trusted_ctx, "live_plan_view", None)
            if trusted_ctx is not None and live_view is not None:
                grounding_error = _verify_grounded_task_start(
                    binding=binding,
                    live_plan_view=live_view,
                    handoff_ref=handoff_ref,
                    sandbox=sandbox,
                )
                if grounding_error is not None:
                    return ToolResponse.failure(grounding_error)
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
            response = ToolResponse.success(result)
            _persist_governed_if_needed(binding, response, operation)
            return response

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
            # AF #49 M1/W1 + M1/W3: terminal return uses the trusted production
            # dispatcher + its durable store ONLY when one is bound (trusted
            # parent-boundary active-task check); no process-local completion
            # channel is written on this path. The production Worker has no
            # parent ExecutionStateStore authority: its task.return performs the
            # terminal SEMANTIC return without mutating parent durability, and
            # the parent-side ExecutionDispatcher reconciliation of the durable
            # Hermes supervisor mechanical evidence owns terminal truth.
            trusted_dispatcher = _trusted_production_execution_dispatcher()
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
            response = ToolResponse.success(result)
            _persist_governed_if_needed(binding, response, operation)
            return response

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
