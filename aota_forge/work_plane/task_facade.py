"""Task lifecycle thin façade — AF #48 M1/W2; AF #49 M1/W1 production boundary.

Implements Agent-facing thin façade:

  task.start(role=target, handoff_ref=work_item handoff)
  task.return(status, result_ref)

Thin façade over existing execution machinery, no new engine/coordinator/ontology.

AF #49 M1/W1 production/test-double boundary (frozen):

* the façade resolves no execution dependency itself: ``task.start`` and
  ``task.return`` require an explicitly supplied ``ExecutionDispatcher`` and
  fail closed (typed error) when it is absent;
* reference fake executor adapters / in-memory execution state stores are never
  silently constructed or reachable from the normal production path; explicit
  test/component injection is the only way a test double reaches the façade;
* process-local completion observation is explicit test-only injection through
  ``completion_sink``; it is never a production durability channel.
  Production terminal truth belongs to parent-side execution reconciliation
  (AF #49 M1/W3), which this module does not implement.

Reuses:
* TaskHandoff + handoff_store (digest-bound durability)
* handoff_runtime WorkSemanticProjection + is_worker_usable
* compiler.compile_handoff_to_execution_package
* core.execution ExecutionDispatcher + ExecutionStateStore + DurableExecutionRecord
* core.execution.results.CanonicalResult + ResultGovernance + WorkerResultCard
* runtime.completion governance_projection + card digest

Thin: validates caller dispatch authority, handoff existence/digest/binding,
then delegates to existing seams. The trusted production dispatcher is resolved
and supplied by the canonical ingress (``core_ingress``) through the existing
``core.ingress`` binding seam.

"""
from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Mapping, MutableMapping

from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    ExecutionStateStore,
    UnboundOriginSessionError,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.work_plane.execution_identity import (
    MalformedTaskIdentityError,
    format_plan_aware_task_id,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM = True
EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED = False
TASK_RETURN_IS_NOT_HANDOFF_WRITE = True
TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT = True
TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS = True
PARENT_WAKEUP_REENTRY_REUSED = True

# AF #49 M1/W8 (I49-B006): trusted internal dispatch metadata key.
# task.start resolution (canonical durable grounded work_item handoff) is
# propagated to the Worker env resolver through this bounded internal
# working_context record. It is trusted server-side metadata produced by the
# grounded handoff open, never model-authored binding identity, and it does
# not expand the public agent-facing task.start contract.
TRUSTED_WORK_HANDOFF_CONTEXT_KEY = "trusted_work_handoff"

WORKER_RESULT_FULL_WRITE_COUNT_NORMAL = 1
WORKER_AUTHORS_RESULT_CARD = False
RESULT_CARD_DETERMINISTIC = True
NORMAL_TASK_MAIN_CARD_EXTRA_READ_CALL = 0

# AF #49 M1/W1 boundary contract markers (frozen; see module docstring).
PRODUCTION_REFERENCE_FAKE_EXECUTOR_FALLBACK = False
PRODUCTION_IN_MEMORY_EXECUTION_STORE_FALLBACK = False
PRODUCTION_PROCESS_LOCAL_COMPLETION_CHANNEL = False
TEST_DOUBLE_EXPLICIT_INJECTION_ALLOWED = True
TEST_DOUBLE_SILENT_PRODUCTION_FALLBACK_ALLOWED = False
MISSING_PRODUCTION_DISPATCHER_FAIL_CLOSED = True
TEST_DOUBLE_PRODUCTION_REACHABLE = False
PARENT_SIDE_DURABLE_RECONCILIATION_OWNS_TERMINAL_TRUTH = True
TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE = False
# AF #49 M1/W3 terminal semantic return boundary (frozen):
TASK_RETURN_REQUIRES_PARENT_STORE = False
TASK_RETURN_PARENT_STORE_MUTATION = False
TASK_RETURN_SEMANTIC_RETURN_WITHOUT_PARENT_STORE = True
WORKER_PARENT_STORE_PATH_EXPOSED = False
PARENT_TERMINAL_TRUTH_OWNER = "parent_side_execution_reconciliation"
# AF #49 M1/W9 (I49-B007) semantic terminal truth boundary (frozen):
# the trusted task.return path writes ONE bounded durable mechanical receipt
# (exact canonical task + exact result handoff ref/digest) so the parent-side
# reconciliation can distinguish a proven governed semantic return from a
# result handoff write alone. Process exit success is NOT semantic success.
TASK_RETURN_WRITES_BOUNDED_DURABLE_RECEIPT = True
TASK_RETURN_RECEIPT_IS_AUTHORITY = False
PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS = False
SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN = True
TASK_RETURN_REQUIRES_VALID_RESULT_HANDOFF = True

CompletionSink = MutableMapping[str, dict[str, Any]]

# AF #53 M2/W1 F2 (accepted M1/RV1 carry-forward): generic requested-role /
# grounded-handoff consistency. Mechanical integrity validation only - no
# workflow position, review strategy, Milestone state or reviewer-specific
# branch. The comparison happens after the grounded durable handoff is
# resolved and before execution dispatch / durable execution record creation.
REQUESTED_ROLE_MUST_EQUAL_GROUNDED_HANDOFF_ROLE = True
ROLE_HANDOFF_MISMATCH_CODE = "ROLE_HANDOFF_MISMATCH"
ROLE_HANDOFF_MISMATCH_FAILS_BEFORE_DISPATCH = True
# Generic thin task lifecycle (canonical task.start/task.return) requires no
# legacy workflow state; this module stays a legacy-free thin seam.
THIN_TASK_LIFECYCLE_REQUIRES_LEGACY_WORKFLOW_STATE = False

# AF #53 M3/W2-R2 (I53-B002) work-item role grounding contract:
# * thin path: the durable work_item handoff MUST carry an explicit semantic
#   work_role; a missing/invalid role fails closed (typed, before any
#   dispatch). The Control Plane never selects the child role
#   (CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH=no) because the task-main
#   LLM owns the semantic choice.
# * legacy compatibility path: the historical missing-work_role -> coder
#   default is preserved unchanged for existing non-thin runtime bindings.
THIN_WORK_ITEM_ROLE_EXPLICIT = True
THIN_PATH_MISSING_WORK_ROLE_FAILS_CLOSED = True
CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH = False
LEGACY_PATH_MISSING_WORK_ROLE_DEFAULT = "coder"

# AF #54 M2/W1-W2 (G54-01) work-item semantic identity grounding contract:
# a ``work_item`` handoff normatively denotes Plan-bound Work, so on the thin
# runtime path the durable semantic payload MUST carry an explicit
# ``work_item_ref`` and ``milestone_ref`` (the Plan/Work semantic identity is
# owned by the Plan/task-main, never invented by the Control Plane). Missing
# refs fail closed (typed WORK_SCOPE_INSUFFICIENT, zero dispatch) instead of
# being silently grounded to the historical "W1"/"M1" defaults. The legacy
# compatibility path keeps its historical envelope-or-default behavior
# unchanged (same bounded-conditional pattern accepted for the missing
# work_role -> coder default in #53).
THIN_WORK_ITEM_SEMANTIC_IDENTITY_EXPLICIT = True
THIN_PATH_MISSING_WORK_IDENTITY_FAILS_CLOSED = True
CONTROL_PLANE_DEFAULTS_WORK_ITEM_TO_W1_ON_THIN_PATH = False
CONTROL_PLANE_DEFAULTS_MILESTONE_TO_M1_ON_THIN_PATH = False
CONTROL_PLANE_INVENTS_SEMANTIC_WORK_IDENTITY = False
LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM = "W1"
LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE = "M1"
# Canonical semantic Work-identity payload fields (semantic payload only;
# the legacy aliases below are accepted as the same semantic intent).
WORK_ITEM_HANDOFF_WORK_ITEM_REF_FIELD = "work_item_ref"
WORK_ITEM_HANDOFF_MILESTONE_REF_FIELD = "milestone_ref"
_WORK_ITEM_REF_SEMANTIC_KEYS = ("work_item_ref", "work_item_id")
_MILESTONE_REF_SEMANTIC_KEYS = ("milestone_ref", "milestone_id")

# AF #57 M1/W4 canonical Plan-aware runtime identity contract:
# * when the trusted runtime supplies an explicit internal Plan identity
#   (the source-neutral PlanAuthorityBinding minted by composition), the
#   canonical task identity embeds it at the explicit Plan position;
# * when it does not (existing Governance 1.x launch), the bounded legacy
#   plan-less identity form is preserved byte-identically - no fake Plan ID
#   is injected and no legacy identity is silently upgraded.
CANONICAL_TASK_ID_PLAN_AWARE_WHEN_BOUND = True
LEGACY_PLAN_LESS_TASK_IDENTITY_COMPATIBILITY = True
PLAN_ID_SILENT_INFERENCE = False


class RoleHandoffMismatchError(ValueError):
    """Typed fail-closed error: requested role != grounded handoff work_role.

    AF #53 M2/W1 F2: the model-requested child role may not silently
    substitute a different grounded durable handoff work_role (nor may the
    handoff role substitute the requested role). This is mechanical integrity
    validation, not workflow reasoning. Raised after the grounded durable
    handoff is resolved and before any execution dispatch or durable execution
    record creation, so a mismatch creates zero child execution.
    """

    code = ROLE_HANDOFF_MISMATCH_CODE

    def __init__(self, requested_role: str, grounded_role: str) -> None:
        self.requested_role = requested_role
        self.grounded_role = grounded_role
        super().__init__(
            f"{self.code}: requested role {requested_role!r} != grounded durable "
            f"handoff work_role {grounded_role!r}; refusing role substitution "
            f"before execution dispatch"
        )


class ProductionDispatcherUnavailableError(ValueError):
    """Typed fail-closed error: no trusted production ExecutionDispatcher."""

    code = "PRODUCTION_DISPATCHER_UNAVAILABLE"

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"{self.code}: {operation} requires a trusted production ExecutionDispatcher "
            f"(explicit injection or canonical ingress resolution); test doubles are never "
            f"a silent production fallback"
        )


class ProductionExecutionStoreUnavailableError(ValueError):
    """Typed fail-closed error: dispatcher carries no execution state store."""

    code = "PRODUCTION_EXECUTION_STORE_UNAVAILABLE"

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"{self.code}: {operation} requires an ExecutionDispatcher wired to an "
            f"ExecutionStateStore; process-local state is not production truth"
        )


def _require_explicit_dispatcher(
    dispatcher: ExecutionDispatcher | None, operation: str
) -> ExecutionDispatcher:
    if dispatcher is None:
        raise ProductionDispatcherUnavailableError(operation)
    if not isinstance(dispatcher, ExecutionDispatcher):
        raise TypeError(
            f"dispatcher must be an ExecutionDispatcher, got {type(dispatcher).__name__}"
        )
    return dispatcher


def _require_execution_store(
    dispatcher: ExecutionDispatcher, operation: str
) -> ExecutionStateStore:
    store = dispatcher.state_store
    if store is None:
        raise ProductionExecutionStoreUnavailableError(operation)
    return store


def _handoff_store_handoff_open(ref: Any, sandbox: WorktreeSandboxBoundary, view: str = "full") -> dict[str, Any]:
    from aota_forge.work_plane.handoff_store import handoff_open

    return handoff_open(ref, view, sandbox=sandbox)


def _validate_task_start_caller(caller_role: str) -> None:
    if caller_role != "task-main":
        raise ValueError(f"task.start caller must be task-main, got {caller_role!r}")


def _validate_task_return_caller(caller_role: str) -> None:
    allowed = {"coder", "analyst", "reviewer", "project-steward"}
    if caller_role not in allowed:
        raise ValueError(f"task.return caller must be one of {sorted(allowed)}, got {caller_role!r}")


def _grounded_handoff_work_role(task_handoff: TaskHandoff) -> str:
    """Grounded durable handoff work_role as a plain string (mechanical only)."""
    role = getattr(task_handoff, "work_role", None)
    value = getattr(role, "value", None)
    return value if isinstance(value, str) else str(role)


def _validate_role_handoff_consistency(requested_role: str, grounded_role: str) -> None:
    """F2: requested role must equal the grounded durable handoff work_role.

    Generic for every child role (coder / analyst / reviewer /
    project-steward). Failure is typed and happens before compile/dispatch.
    """
    if requested_role != grounded_role:
        raise RoleHandoffMismatchError(requested_role, grounded_role)


def _semantic_ref_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        ref = value.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    return None


def _trusted_envelope_identity(
    envelope: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract trusted grounding identity from a durable handoff envelope.

    AF #49 M1/W4: the control envelope fields (plan_ref/milestone_id/
    work_item_id + provenance plan_digest) were filled mechanically by the
    Control Plane at ``handoff.write``. They are authority for the Worker
    compilation; semantic payload never supplies them.
    """
    if not isinstance(envelope, Mapping):
        return None, None, None, None
    plan_ref = _semantic_ref_value(envelope.get("plan_ref"))
    milestone_id = _semantic_ref_value(envelope.get("milestone_id"))
    work_item_id = _semantic_ref_value(envelope.get("work_item_id"))
    plan_digest: str | None = None
    provenance = envelope.get("provenance")
    if isinstance(provenance, Mapping):
        plan_digest = _semantic_ref_value(provenance.get("plan_digest"))
    return plan_ref, plan_digest, milestone_id, work_item_id


def _load_work_item_task_handoff(
    semantic: Mapping[str, Any],
    sandbox: WorktreeSandboxBoundary | None = None,
    envelope: Mapping[str, Any] | None = None,
    *,
    require_explicit_work_role: bool = False,
    require_explicit_work_identity: bool = False,
) -> TaskHandoff:
    trusted_plan_ref, trusted_plan_digest, trusted_mid, trusted_wid = _trusted_envelope_identity(envelope)

    # AF #54 M2/W2 (G54-01): entry-level thin semantic-identity gate so BOTH
    # derivation shapes (direct TaskHandoff payload and generic semantic
    # projection) fail closed identically before any dispatch.
    if require_explicit_work_identity:
        from aota_forge.work_plane.handoff_runtime import WorkScopeInsufficientError

        if not trusted_wid and not any(
            _semantic_ref_value(semantic.get(k)) for k in _WORK_ITEM_REF_SEMANTIC_KEYS
        ):
            raise WorkScopeInsufficientError(
                f"thin work_item handoff omits an explicit semantic "
                f"{WORK_ITEM_HANDOFF_WORK_ITEM_REF_FIELD!r}; Work identity "
                f"belongs to the Plan and task-main reasoning, and the "
                f"Control Plane does not invent a default; re-issue "
                f"handoff.write with the authoritative work_item_ref of the "
                f"Plan Work this child serves (do not retry unchanged)"
            )
        if not trusted_mid and not any(
            _semantic_ref_value(semantic.get(k)) for k in _MILESTONE_REF_SEMANTIC_KEYS
        ):
            raise WorkScopeInsufficientError(
                f"thin work_item handoff omits an explicit semantic "
                f"{WORK_ITEM_HANDOFF_MILESTONE_REF_FIELD!r}; Milestone "
                f"identity belongs to the Plan and task-main reasoning, and "
                f"the Control Plane does not invent a default; re-issue "
                f"handoff.write with the authoritative milestone_ref of the "
                f"current Plan Milestone (do not retry unchanged)"
            )

    def _ground(handoff: TaskHandoff) -> TaskHandoff:
        updates: dict[str, Any] = {}
        if handoff.project_ref is None and sandbox is not None:
            updates["project_ref"] = SemanticReference(ref=sandbox.project_id)
        if trusted_plan_ref:
            updates["plan_ref"] = SemanticReference(ref=trusted_plan_ref, digest=trusted_plan_digest)
        if trusted_mid:
            updates["milestone_ref"] = SemanticReference(ref=trusted_mid)
        if trusted_wid:
            updates["work_item_ref"] = SemanticReference(ref=trusted_wid)
        if not updates:
            return handoff
        try:
            return dataclasses.replace(handoff, **updates)
        except Exception:
            return handoff

    # Try direct TaskHandoff first
    try:
        # If semantic already matches TaskHandoff fields, use it
        if "work_role" in semantic and "task_kind" in semantic:
            return _ground(TaskHandoff.from_dict(semantic))  # type: ignore[arg-type]
    except Exception:
        pass
    # Fallback: synthesize from generic semantic (objective/scope etc.)
    # Use WorkSemanticProjection path: build TaskHandoff via handoff_runtime resolver
    from aota_forge.work_plane.handoff_runtime import (
        WorkScopeInsufficientError,
        WorkSemanticProjection,
        resolve_bounded_work_handoff,
    )
    from aota_forge.work_plane.handoff_store import WORK_ITEM_HANDOFF_ROLE_FIELD

    # Extract required semantic fields with fallbacks
    objective = semantic.get("objective") or semantic.get("summary") or semantic.get("work_done") or "task objective"
    bounded_scope = semantic.get("bounded_scope") or semantic.get("scope") or semantic.get("context") or "bounded scope"
    validation_expectations = semantic.get("validation_expectations") or semantic.get("validation") or ["validate"]
    semantic_stop_expectations = semantic.get("semantic_stop_expectations") or semantic.get("stop_conditions") or ["stop"]
    # Normalize to list
    if isinstance(validation_expectations, str):
        validation_expectations = [validation_expectations]
    if isinstance(semantic_stop_expectations, str):
        semantic_stop_expectations = [semantic_stop_expectations]
    # Build projection
    proj = WorkSemanticProjection(
        objective=str(objective),
        bounded_scope=str(bounded_scope),
        validation_expectations=tuple(validation_expectations),  # type: ignore
        semantic_stop_expectations=tuple(semantic_stop_expectations),  # type: ignore
    )
    # Resolve to TaskHandoff — trusted envelope identity first, then explicit
    # semantic refs (the entry-level thin gate above already fail-closed any
    # thin omission). AF #54 M2/W2 (G54-01): the historical "W1"/"M1" tail is
    # bounded to the legacy compatibility path only — the Control Plane never
    # invents semantic Work identity for thin Plan-bound Work.
    wid: str | None = trusted_wid
    mid: str | None = trusted_mid
    if not wid:
        for k in _WORK_ITEM_REF_SEMANTIC_KEYS:
            v = _semantic_ref_value(semantic.get(k))
            if v:
                wid = v
                break
    if not mid:
        for k in _MILESTONE_REF_SEMANTIC_KEYS:
            v = _semantic_ref_value(semantic.get(k))
            if v:
                mid = v
                break
    if not wid:
        wid = LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM
    if not mid:
        mid = LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE
    if sandbox is not None:
        proj_id = sandbox.project_id
        plan_auth = f"plan-{sandbox.project_id}"
    else:
        proj_id = "proj-test"
        plan_auth = "plan-test"
    if trusted_plan_ref:
        plan_auth = trusted_plan_ref
    # AF #53 M3/W2-R2 (I53-B002): thin work-item grounding requires the LLM's
    # explicit semantic work_role. The thin runtime never silently chooses a
    # child role (missing role -> typed fail-closed, zero dispatch); only the
    # legacy compatibility path keeps the historical coder default.
    wk_role = semantic.get(WORK_ITEM_HANDOFF_ROLE_FIELD)
    if not isinstance(wk_role, str) or not wk_role.strip():
        if require_explicit_work_role:
            raise WorkScopeInsufficientError(
                f"thin work_item handoff requires an explicit semantic "
                f"{WORK_ITEM_HANDOFF_ROLE_FIELD!r}; the Control Plane does not "
                f"choose the child role (missing role fails closed before dispatch)"
            )
        wk_role = LEGACY_PATH_MISSING_WORK_ROLE_DEFAULT
    # Build handoff via resolver (reuse)
    handoff = resolve_bounded_work_handoff(
        work_item_id=wid,
        milestone_ref=mid,
        projection=proj,
        project_id=proj_id,
        plan_authority=plan_auth,
        plan_digest=trusted_plan_digest,
    )
    # Override work_role if semantic specified
    if wk_role.strip() != handoff.work_role.value:
        try:
            from aota_forge.work_plane.roles import parse_agent_work_role

            parsed = parse_agent_work_role(wk_role)
            handoff = dataclasses.replace(handoff, work_role=parsed)
        except Exception as exc:
            if require_explicit_work_role:
                raise WorkScopeInsufficientError(
                    f"thin work_item handoff carries an invalid semantic "
                    f"{WORK_ITEM_HANDOFF_ROLE_FIELD} {wk_role!r}: {exc}"
                ) from exc
    return _ground(handoff)


def load_trusted_work_item_task_handoff(
    *,
    opened: Mapping[str, Any],
    sandbox: WorktreeSandboxBoundary,
    require_explicit_work_role: bool = False,
    require_explicit_work_identity: bool = False,
) -> TaskHandoff:
    """Derive the trusted TaskHandoff from an already-opened durable work_item handoff.

    AF #49 M1/W8 (I49-B006): single shared derivation used by both ``task.start``
    and the governed Worker env resolver, so the Worker binding is derived from
    the exact same canonical durable grounded handoff identity that task.start
    resolved (never a second semantics source).

    AF #53 M3/W2-R2 (I53-B002): ``require_explicit_work_role=yes`` is the thin
    runtime path requirement (typed fail-closed when the durable work_item
    handoff omits/invalidates the semantic ``work_role``). The default keeps
    the legacy compatibility derivation unchanged.

    AF #54 M2/W2 (G54-01): ``require_explicit_work_identity=yes`` is the thin
    runtime path requirement for explicit semantic ``work_item_ref`` +
    ``milestone_ref`` (typed fail-closed before dispatch when omitted; no
    invented W1/M1 grounding). The default keeps the legacy compatibility
    derivation unchanged.
    """
    if not isinstance(opened, Mapping):
        raise ValueError("opened durable handoff must be a mapping")
    if opened.get("mode") != "work_item":
        raise ValueError(f"trusted Work handoff mode must be work_item, got {opened.get('mode')!r}")
    envelope = opened.get("envelope")
    semantic = opened.get("semantic")
    if not isinstance(envelope, Mapping) or not isinstance(semantic, Mapping):
        raise ValueError("opened durable handoff is missing envelope/semantic")
    load_kwargs: dict[str, Any] = {}
    if require_explicit_work_role:
        # Thin runtime path only; the legacy call shape stays byte-identical.
        load_kwargs["require_explicit_work_role"] = True
    if require_explicit_work_identity:
        # Thin runtime path only; the legacy call shape stays byte-identical.
        load_kwargs["require_explicit_work_identity"] = True
    return _load_work_item_task_handoff(semantic, sandbox, envelope, **load_kwargs)


def _propagate_trusted_work_handoff(
    package: Any,
    *,
    opened: Mapping[str, Any],
) -> Any:
    """Propagate the trusted durable handoff identity into internal dispatch metadata.

    AF #49 M1/W8 (I49-B006): the reference/digest are authoritative output of
    the grounded ``handoff_open`` above (never model input). They travel to the
    governed Worker env resolver, which re-opens and re-validates the same
    durable handoff before any physical Worker launch.
    """
    ref = opened.get("ref")
    digest = opened.get("digest")
    if not isinstance(digest, str) or not digest.strip():
        raise ValueError("task.start durable handoff digest missing after trusted open")
    digest = digest.strip().lower()
    if not isinstance(ref, str) or not ref.strip():
        # Deterministic canonical ref form from the trusted open identity
        # (handoff:<mode>:<digest> is understood by the durable store).
        mode = opened.get("mode")
        if not isinstance(mode, str) or not mode.strip():
            raise ValueError("task.start durable handoff ref missing after trusted open")
        ref = f"handoff:{mode.strip()}:{digest}"
    working_context = dict(package.working_context)
    working_context[TRUSTED_WORK_HANDOFF_CONTEXT_KEY] = {
        "ref": ref.strip(),
        "digest": digest,
        "mode": "work_item",
    }
    return dataclasses.replace(package, working_context=working_context)


def task_start(
    *,
    role: str,
    handoff_ref: Any,
    caller_role: str,
    sandbox: WorktreeSandboxBoundary,
    dispatcher: ExecutionDispatcher | None = None,
    thin_task_lifecycle: bool = False,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Agent-facing task.start — validates and reuses execution.task_start seam.

    caller_role must be task-main (fail-closed).
    role must be one of analyst|coder|reviewer|project-steward.
    handoff_ref must be durable work_item handoff, digest-bound, project/worktree bound.
    dispatcher must be the trusted production ExecutionDispatcher (or an explicit
    test/component double injected by the caller); absent -> typed fail-closed.
    thin_task_lifecycle is the trusted mechanical runtime-path classification
    supplied by the canonical ingress (``is_thin_task_lifecycle_binding``); on
    the thin path the durable work_item handoff must carry an explicit semantic
    work_role AND explicit Plan/Work semantic identity (work_item_ref +
    milestone_ref); a missing/invalid role or identity fails closed before
    dispatch (the Control Plane invents neither the child role nor the Work
    identity). It never changes the agent-facing operation contract
    (role, handoff_ref).

    plan_id is the trusted internal Plan identity supplied by the runtime
    (source-neutral ``PlanAuthorityBinding`` minted by composition), never a
    model argument. When present, the canonical task identity embeds the Plan
    at its explicit Plan position; when absent, the bounded legacy
    Governance 1.x plan-less identity is preserved. It is never inferred.

    Returns {task_id, status, handoff_digest}
    """
    # Validate caller
    _validate_task_start_caller(caller_role)
    # Validate target role
    from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLE_SET

    if not isinstance(role, str) or role not in {"coder", "analyst", "reviewer", "project-steward"}:
        raise ValueError(f"task.start target role must be one of coder/analyst/reviewer/project-steward, got {role!r}")
    # AF #49 M1/W1: require the explicitly supplied trusted dispatcher before
    # any handoff IO; never resolve or construct an execution dependency here.
    disp = _require_explicit_dispatcher(dispatcher, "task.start")
    # Validate handoff_ref existence, digest, binding
    opened = _handoff_store_handoff_open(handoff_ref, sandbox, view="full")
    if opened.get("mode") != "work_item":
        raise ValueError(f"task.start requires work_item handoff, got {opened.get('mode')!r}")
    envelope = opened.get("envelope", {})
    semantic = opened.get("semantic", {})
    # Validate digest already done in open (tamper fail-closed)
    # Validate project/worktree binding already done
    # Validate handoff_digest binding? The envelope digest must match opened digest
    # Additional: wrong task/attempt fail-closed — ensure envelope task binding not foreign?
    # For now, ensure at least envelope project matches sandbox (already)
    # Resolve semantic handoff to TaskHandoff. AF #49 M1/W4: trusted control
    # envelope identity (from handoff.write grounding) is authority for the
    # Worker compilation refs; semantic payload stays LLM-owned.
    try:
        load_kwargs: dict[str, Any] = {"opened": opened, "sandbox": sandbox}
        if thin_task_lifecycle:
            # Only the thin runtime path supplies the explicit semantic
            # requirements (child role + Plan/Work identity); the legacy
            # compatibility call shape stays byte-identical for existing
            # legacy doubles/clients.
            load_kwargs["require_explicit_work_role"] = True
            load_kwargs["require_explicit_work_identity"] = True
        task_handoff = load_trusted_work_item_task_handoff(**load_kwargs)
    except Exception as exc:
        # Preserve typed fail-closed identity (e.g. WORK_SCOPE_INSUFFICIENT for
        # a thin work_item handoff missing/invalid work_role) so the canonical
        # ingress projects the typed code instead of a generic failure.
        if isinstance(getattr(exc, "code", None), str) and exc.code:
            raise
        raise ValueError(f"handoff semantic cannot be resolved to TaskHandoff: {exc}") from exc
    # AF #53 M2/W1 F2: requested role must equal the grounded durable handoff
    # work_role. Mechanical integrity validation ONLY (no workflow position,
    # review strategy or Milestone state) and fail closed before compile or
    # dispatch, so a mismatch can create zero durable child execution.
    _validate_role_handoff_consistency(role, _grounded_handoff_work_role(task_handoff))
    # Compile/reuse existing execution-start inputs
    from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package

    # Generate canonical_task_id deterministically from the grounded handoff
    # identity. AF #54 M2/W2 (G54-01): prefer the CONTROL-GROUNDED envelope
    # identity, then the grounded durable handoff's own Milestone/Work
    # semantic identity (which the thin path now requires to be explicit).
    # The historical M1/W1 literal remains a legacy-only tail and is never a
    # thin-path fabrication; the Worker binding resolver's consistency gate
    # requires canonical_task_id to carry the grounded Milestone/Work identity.
    artifact_id = envelope.get("artifact_id", uuid.uuid4().hex)
    grounded_milestone = task_handoff.milestone_ref.ref if task_handoff.milestone_ref is not None else None
    grounded_work_item = task_handoff.work_item_ref.ref if task_handoff.work_item_ref is not None else None
    milestone_id = envelope.get("milestone_id") or grounded_milestone or LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE
    work_item_id = envelope.get("work_item_id") or grounded_work_item or LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM
    # task_id for execution is distinct from handoff artifact_id.
    # AF #57 M1/W4: the trusted runtime may supply the explicit internal Plan
    # identity (source-neutral PlanAuthorityBinding). When present, the
    # canonical identity carries it at the explicit Plan position. When absent
    # (existing Governance 1.x launch) the bounded legacy plan-less form is
    # preserved byte-identically; no Plan identity is inferred or fabricated.
    if plan_id is not None:
        if not is_plan_id(plan_id):
            raise MalformedTaskIdentityError(
                "plan_id must be one canonical internal Plan ID when supplied "
                "by the trusted runtime; the Control Plane never infers it from "
                "an Issue number, worktree, branch, repository or title"
            )
        canonical_task_id = format_plan_aware_task_id(
            project_id=sandbox.project_id,
            plan_id=plan_id,
            milestone_id=milestone_id,
            work_item_id=work_item_id,
            tail=(artifact_id[:8], uuid.uuid4().hex[:8]),
        )
    else:
        canonical_task_id = f"{sandbox.project_id}:{milestone_id}:{work_item_id}:{artifact_id[:8]}:{uuid.uuid4().hex[:8]}"
    # Use sandbox project_id as binding project
    binding = TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=sandbox.project_id)
    package = compile_handoff_to_execution_package(task_handoff, binding)
    # AF #49 M1/W8 (I49-B006): propagate the trusted durable handoff identity
    # produced by this grounded resolution into the internal dispatch metadata;
    # the governed Worker env resolver resolves the same canonical durable
    # handoff instead of the legacy coordinator/operator projection artifacts.
    package = _propagate_trusted_work_handoff(package, opened=opened)
    # Dispatch — this is the existing execution.task_start seam reused
    try:
        result = disp.dispatch(package)
    except UnboundOriginSessionError:
        # AF #49 M1/W6: typed fail-closed transport/lifecycle refusal is
        # preserved so the canonical ingress projects UNBOUND_ORIGIN_SESSION
        # instead of a generic dispatch rejection. No record was created.
        raise
    except Exception as exc:
        # Wrap with typed code preservation
        code = getattr(exc, "code", "DISPATCH_REJECTED")
        raise ValueError(f"{code}: task.start dispatch failed: {exc}") from exc
    # Return task identity/status — thin fa\u00e7ade, not full execution package
    return {
        "task_id": result.canonical_task_id,
        "status": result.initial_state.value if hasattr(result.initial_state, "value") else str(result.initial_state),
        "handoff_digest": opened.get("digest"),
        "executor_id": getattr(result, "adapter_handle", ""),
    }


def task_return(
    *,
    status: str,
    result_ref: Any,
    caller_role: str,
    caller_task_id: str,
    sandbox: WorktreeSandboxBoundary,
    dispatcher: ExecutionDispatcher | None = None,
    completion_sink: CompletionSink | None = None,
) -> dict[str, Any]:
    """Agent-facing task.return — child -> parent terminal semantic return.

    Validates the trusted child identity, the result_ref ownership/digest/binding,
    and — when the caller supplies a trusted parent-boundary dispatcher — the
    active task/attempt truth. It derives the deterministic Worker Result Card
    from the durable full Result handoff and returns the terminal semantic
    return envelope.

    AF #49 M1/W3 boundary: this NEVER mutates the parent's durable
    ExecutionStateStore. Durable terminal execution truth is owned by
    parent-side ExecutionDispatcher reconciliation of the Hermes supervisor
    mechanical evidence; completion delivery / parent re-entry is owned by the
    production task-main progression pass over the wired DurableCompletionCoordinator.
    ``dispatcher`` is optional: the production Worker has no parent store
    authority and completes its semantic role without it. Process-local
    completion observation exists only through an explicitly injected
    ``completion_sink`` (test/component only; never a production channel).

    Distinct from handoff.write and execution.task_result.
    """
    _validate_task_return_caller(caller_role)
    if status not in ("completed", "blocked", "failed"):
        raise ValueError(f"task.return status must be completed|blocked|failed, got {status!r}")
    # Validate result_ref — must be durable full result handoff
    opened = _handoff_store_handoff_open(result_ref, sandbox, view="full")
    if opened.get("mode") != "result":
        raise ValueError(f"task.return requires result handoff, got {opened.get('mode')!r}")
    envelope = opened.get("envelope", {})
    semantic = opened.get("semantic", {})
    stored_digest = opened.get("digest")
    # Validate ownership/digest/binding
    # Cross-project already checked in open
    # Validate source_role matches caller_role
    src = envelope.get("source_role")
    if src != caller_role:
        raise ValueError(f"result handoff source_role {src!r} != caller {caller_role!r} — wrong-role fail-closed")
    # Validate task_id binding — result must belong to caller's task
    env_task = envelope.get("task_id")
    if env_task != caller_task_id:
        raise ValueError(f"result handoff task_id {env_task!r} != caller task {caller_task_id!r} — wrong-task fail-closed")
    # Validate status coherence: semantic payload should not contradict status? We allow.
    # Build CanonicalResult from status + semantic
    # For W2, we reuse CanonicalResult creation via factory methods
    #
    # AF #49 M1/W3 terminal semantic return boundary:
    # - when a trusted parent-boundary dispatcher is explicitly supplied, it is
    #   validated and (when it carries a durable store) used for the truthful
    #   active task/attempt check — it is NEVER mutated from here;
    # - the production Worker path has no parent ExecutionStateStore authority
    #   in its environment: it validates the trusted Worker identity + result
    #   binding only and exits normally. Durable terminal execution truth is
    #   established later by the parent-side ExecutionDispatcher reconciliation
    #   of the Hermes supervisor mechanical evidence (PARENT_TERMINAL_TRUTH).
    record = None
    if dispatcher is not None:
        disp = _require_explicit_dispatcher(dispatcher, "task.return")
        store = _require_execution_store(disp, "task.return")
        # Retrieve existing execution record for this task (must be active)
        record = store.get(caller_task_id)  # type: ignore[arg-type]
        if record is None:
            raise ValueError(f"task.return: no active execution record for {caller_task_id!r} — wrong-task fail-closed")
        if record.canonical_task_state.is_terminal:
            raise ValueError(f"task.return: task {caller_task_id!r} already terminal {record.canonical_task_state.value} — wrong-task fail-closed")
    # Derive CanonicalResult
    # result_data carries the full semantic result payload (bounded)
    if record is not None:
        executor_id = str(record.executor_id)
        correlation_id = getattr(record, "correlation_id", stored_digest) or stored_digest
    else:
        # Production Worker semantic return: no parent executor identity is
        # available or claimed; this transient observation envelope is never
        # persisted as parent truth.
        executor_id = "worker-return-pending-parent-reconciliation"
        correlation_id = stored_digest or f"pending-parent-reconciliation:{caller_task_id}".replace(" ", "")
    if status == "completed":
        canonical_result = CanonicalResult.success(
            canonical_task_id=caller_task_id,
            executor_id=executor_id,
            result_data=dict(semantic),
            correlation_id=correlation_id,
        )
    elif status == "failed":
        # Use semantic summary as error message
        msg = semantic.get("summary") or semantic.get("work_done") or "worker failed"
        if not isinstance(msg, str):
            msg = str(msg)[:512]
        canonical_result = CanonicalResult.failure(
            canonical_task_id=caller_task_id,
            executor_id=executor_id,
            error_code="EXECUTION_FAILED",
            error_message=msg[:512],
            result_data=dict(semantic),
            correlation_id=correlation_id,
        )
    else:  # blocked
        msg = semantic.get("summary") or semantic.get("blockers") or "blocked"
        if isinstance(msg, list):
            msg = "; ".join(msg[:3])[:512]
        if not isinstance(msg, str):
            msg = str(msg)[:512]
        canonical_result = CanonicalResult.failure(
            canonical_task_id=caller_task_id,
            executor_id=executor_id,
            error_code="SEMANTIC_STOP",
            error_message=msg[:512],
            result_data=dict(semantic),
            correlation_id=correlation_id,
            status="failed",
            canonical_task_state=CanonicalTaskState.FAILED.value,
        )
    # Derive governance projection (reuse existing seam)
    from aota_forge.runtime.completion import governance_projection_for_result

    governance = governance_projection_for_result(canonical_result)
    # Derive deterministic WorkerResultCard (reuse)
    from aota_forge.work_plane.result_card import project_worker_result_card

    # Determine work_role for card — from caller_role
    card = project_worker_result_card(
        canonical_result,
        governance,
        caller_role,
        summary=(semantic.get("summary") or semantic.get("work_done") or f"task {status}")[:1024],
        blocking_finding_count=int(semantic.get("findings", 0)) if isinstance(semantic.get("findings"), int) else 0,
        next_hint=semantic.get("recommendation") if isinstance(semantic.get("recommendation"), str) else None,
    )
    # AF #49 M1/W9 (I49-B007): the trusted task.return path durably records a
    # bounded mechanical receipt for the EXACT canonical task + result handoff
    # ref/digest. This is governance evidence only (no new result ontology, no
    # new task state, no semantic content duplicate): the parent-side
    # coordinator re-opens and digest-verifies the referenced result handoff
    # before any semantic-return claim. A failed receipt persist fails the
    # return closed; a process exit alone can never become semantic success.
    from aota_forge.work_plane.task_return_receipt import write_task_return_receipt

    receipt = write_task_return_receipt(
        sandbox,
        canonical_task_id=caller_task_id,
        result_ref=opened.get("ref") or str(result_ref),
        result_digest=stored_digest,
        status=status,
    )
    # AF #49 M1/W3: task.return performs NO direct mutation of the parent's
    # durable ExecutionStateStore (TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE=no).
    # The Worker process has no parent store authority path
    # (WORKER_PARENT_STORE_PATH_EXPOSED=no); the parent-side
    # ExecutionDispatcher reconciliation of the durable Hermes supervisor
    # mechanical evidence owns terminal execution truth and derives/attaches
    # the deterministic WorkerResultCard there.
    completion = {
        "task_id": caller_task_id,
        "status": status,
        "card": card.to_dict(),
        "card_digest": card.card_digest,
        "full_result_ref": opened.get("ref") or str(result_ref),
        "governance_outcome": governance.outcome.value if hasattr(governance.outcome, "value") else str(governance.outcome),
        "semantic_return_receipt_digest": receipt.receipt_digest,
        "durable_completion": "parent_side_reconciliation_pending",
        "parent_durable_truth_owner": "parent_side_execution_reconciliation",
        "parent_store_mutated": False,
        "process_local_completion_recorded": completion_sink is not None,
    }
    # Explicit test/component-only local observation; never silent production state.
    if completion_sink is not None:
        if not isinstance(completion_sink, MutableMapping):
            raise TypeError("completion_sink must be an explicit bounded mutable mapping")
        completion_sink[caller_task_id] = completion
    return completion


def get_completion(task_id: str, *, completion_sink: CompletionSink) -> dict[str, Any] | None:
    """Read a completion from an explicitly supplied bounded test/component sink.

    There is no process-local production completion channel; production completion
    durability is parent-side execution reconciliation (AF #49 M1/W3).
    """
    if not isinstance(completion_sink, MutableMapping):
        raise TypeError("completion_sink must be an explicit bounded mutable mapping")
    return completion_sink.get(task_id)


def clear_completions(completion_sink: CompletionSink) -> None:
    if not isinstance(completion_sink, MutableMapping):
        raise TypeError("completion_sink must be an explicit bounded mutable mapping")
    completion_sink.clear()


__all__ = [
    "TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM",
    "EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED",
    "TASK_RETURN_IS_NOT_HANDOFF_WRITE",
    "TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT",
    "TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS",
    "PARENT_WAKEUP_REENTRY_REUSED",
    "REQUESTED_ROLE_MUST_EQUAL_GROUNDED_HANDOFF_ROLE",
    "ROLE_HANDOFF_MISMATCH_CODE",
    "ROLE_HANDOFF_MISMATCH_FAILS_BEFORE_DISPATCH",
    "THIN_TASK_LIFECYCLE_REQUIRES_LEGACY_WORKFLOW_STATE",
    "THIN_WORK_ITEM_ROLE_EXPLICIT",
    "THIN_PATH_MISSING_WORK_ROLE_FAILS_CLOSED",
    "CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH",
    "LEGACY_PATH_MISSING_WORK_ROLE_DEFAULT",
    "THIN_WORK_ITEM_SEMANTIC_IDENTITY_EXPLICIT",
    "THIN_PATH_MISSING_WORK_IDENTITY_FAILS_CLOSED",
    "CONTROL_PLANE_DEFAULTS_WORK_ITEM_TO_W1_ON_THIN_PATH",
    "CONTROL_PLANE_DEFAULTS_MILESTONE_TO_M1_ON_THIN_PATH",
    "CONTROL_PLANE_INVENTS_SEMANTIC_WORK_IDENTITY",
    "LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM",
    "LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE",
    "WORK_ITEM_HANDOFF_WORK_ITEM_REF_FIELD",
    "WORK_ITEM_HANDOFF_MILESTONE_REF_FIELD",
    "CANONICAL_TASK_ID_PLAN_AWARE_WHEN_BOUND",
    "LEGACY_PLAN_LESS_TASK_IDENTITY_COMPATIBILITY",
    "PLAN_ID_SILENT_INFERENCE",
    "WORKER_RESULT_FULL_WRITE_COUNT_NORMAL",
    "WORKER_AUTHORS_RESULT_CARD",
    "RESULT_CARD_DETERMINISTIC",
    "NORMAL_TASK_MAIN_CARD_EXTRA_READ_CALL",
    "PRODUCTION_REFERENCE_FAKE_EXECUTOR_FALLBACK",
    "PRODUCTION_IN_MEMORY_EXECUTION_STORE_FALLBACK",
    "PRODUCTION_PROCESS_LOCAL_COMPLETION_CHANNEL",
    "TEST_DOUBLE_EXPLICIT_INJECTION_ALLOWED",
    "TEST_DOUBLE_SILENT_PRODUCTION_FALLBACK_ALLOWED",
    "MISSING_PRODUCTION_DISPATCHER_FAIL_CLOSED",
    "TEST_DOUBLE_PRODUCTION_REACHABLE",
    "PARENT_SIDE_DURABLE_RECONCILIATION_OWNS_TERMINAL_TRUTH",
    "TASK_RETURN_DIRECTLY_OWNS_DURABLE_PARENT_STATE",
    "TASK_RETURN_PARENT_STORE_MUTATION",
    "TASK_RETURN_REQUIRES_PARENT_STORE",
    "TASK_RETURN_SEMANTIC_RETURN_WITHOUT_PARENT_STORE",
    "WORKER_PARENT_STORE_PATH_EXPOSED",
    "PARENT_TERMINAL_TRUTH_OWNER",
    "TASK_RETURN_WRITES_BOUNDED_DURABLE_RECEIPT",
    "TASK_RETURN_RECEIPT_IS_AUTHORITY",
    "PROCESS_EXIT_SUCCESS_IS_SEMANTIC_SUCCESS",
    "SEMANTIC_SUCCESS_REQUIRES_VALID_TASK_RETURN",
    "TASK_RETURN_REQUIRES_VALID_RESULT_HANDOFF",
    "TRUSTED_WORK_HANDOFF_CONTEXT_KEY",
    "CompletionSink",
    "RoleHandoffMismatchError",
    "ProductionDispatcherUnavailableError",
    "ProductionExecutionStoreUnavailableError",
    "load_trusted_work_item_task_handoff",
    "task_start",
    "task_return",
    "get_completion",
    "clear_completions",
]
