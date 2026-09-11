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
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM = True
EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED = False
TASK_RETURN_IS_NOT_HANDOFF_WRITE = True
TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT = True
TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS = True
PARENT_WAKEUP_REENTRY_REUSED = True

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

CompletionSink = MutableMapping[str, dict[str, Any]]


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
) -> TaskHandoff:
    trusted_plan_ref, trusted_plan_digest, trusted_mid, trusted_wid = _trusted_envelope_identity(envelope)

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
        WorkSemanticProjection,
        resolve_bounded_work_handoff,
    )

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
    # Resolve to TaskHandoff — trusted envelope identity first, then semantic
    # refs, then legacy defaults (never model authority when grounded).
    wid = trusted_wid or "W1"
    mid = trusted_mid or "M1"
    if sandbox is not None:
        proj_id = sandbox.project_id
        plan_auth = f"plan-{sandbox.project_id}"
    else:
        proj_id = "proj-test"
        plan_auth = "plan-test"
    if trusted_plan_ref:
        plan_auth = trusted_plan_ref
    if not trusted_wid:
        for k in ("work_item_id", "work_item_ref"):
            v = _semantic_ref_value(semantic.get(k))
            if v:
                wid = v
                break
    if not trusted_mid:
        for k in ("milestone_id", "milestone_ref"):
            v = _semantic_ref_value(semantic.get(k))
            if v:
                mid = v
                break
    # Derive work_role from semantic's work_role or default coder
    wk_role = semantic.get("work_role")
    if not isinstance(wk_role, str) or not wk_role.strip():
        wk_role = "coder"
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
        except Exception:
            pass
    return _ground(handoff)


def task_start(
    *,
    role: str,
    handoff_ref: Any,
    caller_role: str,
    sandbox: WorktreeSandboxBoundary,
    dispatcher: ExecutionDispatcher | None = None,
) -> dict[str, Any]:
    """Agent-facing task.start — validates and reuses execution.task_start seam.

    caller_role must be task-main (fail-closed).
    role must be one of analyst|coder|reviewer|project-steward.
    handoff_ref must be durable work_item handoff, digest-bound, project/worktree bound.
    dispatcher must be the trusted production ExecutionDispatcher (or an explicit
    test/component double injected by the caller); absent -> typed fail-closed.

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
        task_handoff = _load_work_item_task_handoff(semantic, sandbox, envelope)  # type: ignore
    except Exception as exc:
        raise ValueError(f"handoff semantic cannot be resolved to TaskHandoff: {exc}") from exc
    # Compile/reuse existing execution-start inputs
    from aota_forge.work_plane.compiler import TrustedExecutionBinding, compile_handoff_to_execution_package

    # Generate canonical_task_id deterministically from envelope artifact + target role
    artifact_id = envelope.get("artifact_id", uuid.uuid4().hex)
    milestone_id = envelope.get("milestone_id") or "M1"
    work_item_id = envelope.get("work_item_id") or "W1"
    # task_id for execution is distinct from handoff artifact_id
    canonical_task_id = f"{sandbox.project_id}:{milestone_id}:{work_item_id}:{artifact_id[:8]}:{uuid.uuid4().hex[:8]}"
    # Use sandbox project_id as binding project
    binding = TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=sandbox.project_id)
    package = compile_handoff_to_execution_package(task_handoff, binding)
    # Dispatch — this is the existing execution.task_start seam reused
    try:
        result = disp.dispatch(package)
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
    "CompletionSink",
    "ProductionDispatcherUnavailableError",
    "ProductionExecutionStoreUnavailableError",
    "task_start",
    "task_return",
    "get_completion",
    "clear_completions",
]
