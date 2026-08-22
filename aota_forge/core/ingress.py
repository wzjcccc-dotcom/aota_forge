"""Canonical Unified Ingress (M1-A / M2-B / M4-3).

All canonical operations from all external adapters enter through
``execute``.  The canonical sequence is:

    lookup descriptor
    -> M2 read-only gate
    -> shared input validation (contract boundary)
    -> read-only ContextResolver
    -> create OperationContext
    -> invoke registered handler
    -> normalize result/error

Adapters MUST NOT reimplement operation routing, contract lookup or input
validation; the ingress is the single internal entry point.

The ingress lazily binds the default canonical handlers exactly once before
routing (``core.bootstrap.ensure_handlers_bound``); handler binding is
deterministic and idempotent and never triggered by package imports alone.

Every canonical execution carries a bounded ``audit`` metadata record and a
``correlation_id`` (trusted caller-supplied when safely bounded, otherwise
Core-generated).  Invalid input or context failure never executes the
handler.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aota_forge.core.authorization import AuthorizationFailure, CapabilityLeaseIssuer
from aota_forge.core.context import ContextResolver, TrustedContext, resolve_principal_binding
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
    READ_ONLY,
    WRITE_ONLY,
    InputSpec,
    OperationContractDescriptor,
)
from aota_forge.core.contracts.errors import ForgeError, UnsupportedOperationError
from aota_forge.core.contracts.mutation import MutationIntent, MutationPreconditions
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import LifecycleResult, failure, failure_from_error, lifecycle_envelope, success
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.bootstrap import ensure_handlers_bound
from aota_forge.core.execution.dispatcher import (
    DispatcherError,
    ExecutionDispatcher,
    IdempotencyConflictError,
    NeedsSemanticChoiceError,
    PackageInvalidError,
    TaskNotFoundError,
)
from aota_forge.core.execution import ExecutionErrorCode
from aota_forge.core.execution.adapter import CancelResult
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import (
    AmbiguousExecutorError,
    CapabilityMismatchError,
    ExecutorNotFoundError,
    ExecutorRegistryError,
)
from aota_forge.core.execution.roles import validate_canonical_role
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.identity.refs import ObjectRef
from aota_forge.core.transaction import TransactionStore
from aota_forge.core.transitions import PlanInitRequest, PlanRetirementRequest

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_RESOLVER = ContextResolver()


# ---------------------------------------------------------------------------
# M5 Canonical Execution Descriptors & Registration
# ---------------------------------------------------------------------------

TASK_START_DESCRIPTOR = OperationContractDescriptor(
    name="execution.task_start",
    description="Mechanically dispatches a canonical execution task to the specified executor adapter",
    inputs=(
        InputSpec("executor", "str"),
        InputSpec("role", "str"),
        InputSpec("instruction", "str"),
        InputSpec("project_id", "str"),
        InputSpec("subject_ref", "str?"),
        InputSpec("timeout", "int?"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=WRITE_ONLY,
    mutation_scope="execution_task",
    required_authority="caller_execution_intent",
    approval_required=False,
    decision_required=False,
    valid_predecessor_state="uninitialized",
    valid_successor_state="dispatched",
    subject_revision_precondition=False,
    external_authority_precondition=False,
    idempotency="idempotent under (idempotency_key, intent_fingerprint)",
    result_contract="canonical_dispatch_result.v1",
    errors=(
        "EXECUTOR_NOT_FOUND",
        "EXECUTOR_UNAVAILABLE",
        "CAPABILITY_MISMATCH",
        "PACKAGE_INVALID",
        "ROLE_MAPPING_NOT_FOUND",
        "DISPATCH_REJECTED",
        "DISPATCH_TIMEOUT",
        "NEEDS_SEMANTIC_CHOICE",
        "IDEMPOTENCY_CONFLICT",
        "INTERNAL_MECHANICAL_ERROR",
    ),
    protocol_version=PROTOCOL_VERSION,
)

TASK_STATUS_DESCRIPTOR = OperationContractDescriptor(
    name="execution.task_status",
    description="Queries the execution status of a dispatched task",
    inputs=(
        InputSpec("task_id", "str"),
        InputSpec("executor", "str"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=READ_ONLY,
    errors=(
        "TASK_NOT_FOUND",
        "EXECUTOR_UNAVAILABLE",
        "TASK_STATE_UNKNOWN",
        "ROUTE_EXECUTOR_MISMATCH",
        "INTERNAL_MECHANICAL_ERROR",
    ),
    protocol_version=PROTOCOL_VERSION,
)

TASK_RESULT_DESCRIPTOR = OperationContractDescriptor(
    name="execution.task_result",
    description="Fetches the CanonicalResult of a finished task",
    inputs=(
        InputSpec("task_id", "str"),
        InputSpec("executor", "str"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=READ_ONLY,
    errors=(
        "TASK_NOT_FOUND",
        "RESULT_INVALID",
        "TASK_STILL_RUNNING",
        "EXECUTOR_UNAVAILABLE",
        "ROUTE_EXECUTOR_MISMATCH",
        "INTERNAL_MECHANICAL_ERROR",
    ),
    protocol_version=PROTOCOL_VERSION,
)

TASK_CANCEL_DESCRIPTOR = OperationContractDescriptor(
    name="execution.task_cancel",
    description="Requests cancellation of an active task",
    inputs=(
        InputSpec("task_id", "str"),
        InputSpec("executor", "str"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=WRITE_ONLY,
    mutation_scope="execution_task",
    required_authority="caller_execution_intent",
    approval_required=False,
    decision_required=False,
    valid_predecessor_state="active",
    valid_successor_state="cancelled",
    subject_revision_precondition=False,
    external_authority_precondition=False,
    idempotency="idempotent",
    result_contract="canonical_cancel_result.v1",
    errors=(
        "TASK_NOT_FOUND",
        "CANCEL_UNSUPPORTED",
        "TASK_ALREADY_TERMINAL",
        "ROUTE_EXECUTOR_MISMATCH",
        "INTERNAL_MECHANICAL_ERROR",
    ),
    protocol_version=PROTOCOL_VERSION,
)

EXECUTOR_LIST_DESCRIPTOR = OperationContractDescriptor(
    name="execution.executor_list",
    description="Lists all registered executor adapters and their descriptors",
    inputs=(),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=READ_ONLY,
    errors=("INTERNAL_MECHANICAL_ERROR",),
    protocol_version=PROTOCOL_VERSION,
)

EXECUTOR_CAPABILITIES_DESCRIPTOR = OperationContractDescriptor(
    name="execution.executor_capabilities",
    description="Displays advertised capabilities of the specified executor adapter",
    inputs=(
        InputSpec("executor", "str"),
    ),
    required_context=(),
    optional_context=(),
    internal_ids_required=(),
    internal_ids_created=(),
    read_write=READ_ONLY,
    errors=(
        "EXECUTOR_NOT_FOUND",
        "INTERNAL_MECHANICAL_ERROR",
    ),
    protocol_version=PROTOCOL_VERSION,
)

CANONICAL_EXECUTION_OPERATIONS: tuple[str, ...] = (
    "execution.task_start",
    "execution.task_status",
    "execution.task_result",
    "execution.task_cancel",
    "execution.executor_list",
    "execution.executor_capabilities",
)

EXECUTION_DESCRIPTORS: dict[str, OperationContractDescriptor] = {
    desc.name: desc
    for desc in (
        TASK_START_DESCRIPTOR,
        TASK_STATUS_DESCRIPTOR,
        TASK_RESULT_DESCRIPTOR,
        TASK_CANCEL_DESCRIPTOR,
        EXECUTOR_LIST_DESCRIPTOR,
        EXECUTOR_CAPABILITIES_DESCRIPTOR,
    )
}
EXECUTION_OPERATIONS: frozenset[str] = frozenset(CANONICAL_EXECUTION_OPERATIONS)

_CANONICAL_EXECUTION_ERROR_CODES = frozenset(
    {code.value for code in ExecutionErrorCode}
).union(
    code
    for descriptor in EXECUTION_DESCRIPTORS.values()
    for code in descriptor.errors
)


def _typed_execution_error(exc: Exception) -> tuple[str, str, bool] | None:
    """Extract a bounded canonical error from an adapter exception."""
    code: str | None = None
    for attribute in ("code", "error_code"):
        try:
            candidate = getattr(exc, attribute, None)
        except Exception:
            continue
        if isinstance(candidate, str) and candidate.strip() in _CANONICAL_EXECUTION_ERROR_CODES:
            code = candidate.strip()
            break

    message: str | None = None
    try:
        candidate_message = getattr(exc, "message", None)
    except Exception:
        candidate_message = None
    if isinstance(candidate_message, str) and candidate_message:
        message = candidate_message

    if code is None:
        try:
            args = exc.args
        except Exception:
            args = ()
        if isinstance(args, (tuple, list)) and args and isinstance(args[0], str):
            raw_message = args[0]
            prefix = raw_message.partition(":")[0].strip()
            if prefix in _CANONICAL_EXECUTION_ERROR_CODES:
                code = prefix
                message = message or raw_message

    if code is None:
        return None

    if message is None:
        message = code.replace("_", " ").lower()
    try:
        retryable = getattr(exc, "retryable", False)
    except Exception:
        retryable = False
    return code, message, retryable if isinstance(retryable, bool) else False


def get_execution_descriptor(operation: str) -> OperationContractDescriptor | None:
    """Retrieve the canonical Execution descriptor for an operation."""
    return EXECUTION_DESCRIPTORS.get(operation)


def register_execution_descriptors(registry: Any | None = None) -> None:
    """Register canonical execution descriptors into the provided or default registry."""
    target = DEFAULT_REGISTRY if registry is None else registry
    for desc in EXECUTION_DESCRIPTORS.values():
        if not target.has(desc.name):
            target.bind(desc)


# ---------------------------------------------------------------------------
# ExecutionDispatcher Mechanical Injection Seam
# ---------------------------------------------------------------------------

_BOUND_EXECUTION_DISPATCHER: ExecutionDispatcher | None = None


def bind_execution_dispatcher(dispatcher: ExecutionDispatcher | None) -> None:
    """Process-local mechanical injection seam for ExecutionDispatcher."""
    if dispatcher is not None and not isinstance(dispatcher, ExecutionDispatcher):
        raise TypeError(f"dispatcher must be ExecutionDispatcher, got {type(dispatcher).__name__}")
    global _BOUND_EXECUTION_DISPATCHER
    _BOUND_EXECUTION_DISPATCHER = dispatcher


def get_execution_dispatcher() -> ExecutionDispatcher | None:
    """Return the currently bound ExecutionDispatcher, or None."""
    return _BOUND_EXECUTION_DISPATCHER


def reset_execution_dispatcher() -> None:
    """Reset the bound ExecutionDispatcher."""
    global _BOUND_EXECUTION_DISPATCHER
    _BOUND_EXECUTION_DISPATCHER = None


def _require_bound_dispatcher() -> ExecutionDispatcher:
    if _BOUND_EXECUTION_DISPATCHER is None:
        raise ForgeError(
            "INTERNAL_MECHANICAL_ERROR",
            "Execution dispatcher is not bound in ingress",
            retryable=False,
        )
    return _BOUND_EXECUTION_DISPATCHER


@dataclass(frozen=True)
class MutationIngressRequest:
    """Trusted internal envelope for the closed M4-3 mutation set.

    Adapters may carry semantic values into the existing M4-4 request models,
    but the store, trusted context, lease, and mechanical preconditions remain
    typed runtime values supplied by the trusted boundary.
    """

    operation: str
    store: TransactionStore
    request: PlanInitRequest | PlanRetirementRequest


_MUTATION_REQUEST_TYPES: dict[str, type] = {
    PLAN_INIT_OPERATION: PlanInitRequest,
    PLAN_RETIREMENT_OPERATION: PlanRetirementRequest,
}


def resolve_correlation_id(correlation_id: object) -> str:
    """Return a bounded machine-readable correlation id.

    A trusted caller-supplied id is honored only when it is a bounded safe
    string; anything else is replaced by a Core-generated one.
    """
    if isinstance(correlation_id, str) and _CORRELATION_ID_PATTERN.match(correlation_id):
        return correlation_id
    return uuid.uuid4().hex


def _audit(
    operation: str,
    protocol_version: str | None,
    contract_hash: str | None,
    correlation_id: str,
    validation: str,
    context: str,
    handler: str,
    principal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation": operation,
        "protocol_version": protocol_version or PROTOCOL_VERSION,
        "contract_hash": contract_hash,
        "correlation_id": correlation_id,
        "validation": validation,
        "context": context,
        "handler": handler,
        "principal": principal,
    }


def _handler_result_failure(
    operation: str,
    correlation_id: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    audit["handler"] = "handler_result_invalid"
    err = ForgeError("FORGE_ERROR", "invalid handler result", retryable=False)
    return failure_from_error(operation, err, correlation_id=correlation_id, audit=audit)


def _json_safe(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return False
    return True


def _normalize_handler_result(
    operation: str,
    payload: object,
    correlation_id: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the existing internal handler mapping contract at ingress."""
    if not isinstance(payload, Mapping):
        return _handler_result_failure(operation, correlation_id, audit)
    try:
        payload = dict(payload)
    except Exception:
        return _handler_result_failure(operation, correlation_id, audit)

    try:
        ok = payload.get("ok", True)
        if not isinstance(ok, bool):
            return _handler_result_failure(operation, correlation_id, audit)

        if ok is False:
            error = payload.get("error")
            if not isinstance(error, Mapping):
                return _handler_result_failure(operation, correlation_id, audit)
            error = dict(error)
            code = error.get("code")
            message = error.get("message")
            retryable = error.get("retryable")
            if (
                not isinstance(code, str)
                or not code
                or not isinstance(message, str)
                or not isinstance(retryable, bool)
            ):
                return _handler_result_failure(operation, correlation_id, audit)

            blockers = payload.get("blockers")
            if blockers is None:
                blockers = []
            if not isinstance(blockers, list) or not all(
                isinstance(item, str) for item in blockers
            ):
                return _handler_result_failure(operation, correlation_id, audit)
            next_action = payload.get("next_action")
            if next_action is not None and not isinstance(next_action, str):
                return _handler_result_failure(operation, correlation_id, audit)

            audit["handler"] = "handler_failure"
            return failure(
                operation=operation,
                code=code,
                message=message,
                retryable=retryable,
                correlation_id=correlation_id,
                blockers=blockers,
                next_action=next_action,
                audit=audit,
            )

        data = payload.get("data")
        if data is None:
            data = {}
        elif isinstance(data, Mapping):
            data = dict(data)
        else:
            return _handler_result_failure(operation, correlation_id, audit)

        evidence = payload.get("evidence")
        if evidence is None:
            evidence = {}
        elif isinstance(evidence, Mapping):
            evidence = dict(evidence)
        else:
            return _handler_result_failure(operation, correlation_id, audit)

        warnings = payload.get("warnings")
        if warnings is None:
            warnings = []
        if not isinstance(warnings, list) or not all(
            isinstance(item, str) for item in warnings
        ):
            return _handler_result_failure(operation, correlation_id, audit)

        status = payload.get("status", "ok")
        result = payload.get("result", "ok")
        if not isinstance(status, str) or not isinstance(result, str):
            return _handler_result_failure(operation, correlation_id, audit)

        next_action = payload.get("next_action")
        if next_action is not None and not isinstance(next_action, str):
            return _handler_result_failure(operation, correlation_id, audit)

        semantic_choices = payload.get("semantic_choices")
        if semantic_choices is None:
            semantic_choices = []
        elif isinstance(semantic_choices, list):
            if not all(isinstance(choice, Mapping) for choice in semantic_choices):
                return _handler_result_failure(operation, correlation_id, audit)
            semantic_choices = [dict(choice) for choice in semantic_choices]
        else:
            return _handler_result_failure(operation, correlation_id, audit)

        if not all(_json_safe(value) for value in (data, evidence, semantic_choices)):
            return _handler_result_failure(operation, correlation_id, audit)

        audit["handler"] = "success"
        return success(
            operation=operation,
            data=data,
            evidence=evidence,
            warnings=warnings,
            status=status,
            result=result,
            next_action=next_action,
            semantic_choices=semantic_choices,
            correlation_id=correlation_id,
            audit=audit,
        )
    except Exception as exc:  # bounded normalization guard
        audit["handler"] = "internal_error"
        err = ForgeError("FORGE_ERROR", f"internal error: {type(exc).__name__}", retryable=False)
        return failure_from_error(operation, err, correlation_id=correlation_id, audit=audit)


def _mutation_target(request: PlanInitRequest | PlanRetirementRequest) -> ObjectRef:
    return request.plan_ref


def _intent_target_matches(intent: MutationIntent, target: ObjectRef) -> bool:
    logical_target = intent.logical_target
    if isinstance(logical_target, ObjectRef):
        return logical_target == target
    if isinstance(logical_target, str):
        return logical_target == target.to_canonical()
    if isinstance(logical_target, Mapping) and "typed_target" in logical_target:
        typed_target = logical_target["typed_target"]
        if isinstance(typed_target, ObjectRef):
            return typed_target == target
        return typed_target == target.to_canonical()
    return False


def _validate_mutation_request(
    envelope: MutationIngressRequest,
    descriptor,
) -> dict[str, object]:
    """Derive lease bindings from the actual typed mutation request."""
    expected_type = _MUTATION_REQUEST_TYPES.get(envelope.operation)
    if expected_type is None or not isinstance(envelope.request, expected_type):
        raise ForgeError("INPUT_TYPE_INVALID", "operation payload type is invalid")
    if not isinstance(envelope.store, TransactionStore):
        raise ForgeError("INPUT_TYPE_INVALID", "mutation store must be the isolated TransactionStore")

    request = envelope.request
    if not isinstance(request.trusted_context, TrustedContext) or not request.trusted_context.is_bound:
        raise AuthorizationFailure("AUTHORIZATION_MISSING", "runtime-bound trusted context is required")
    if request.trusted_context.principal is None:
        raise AuthorizationFailure("AUTHORIZATION_MISSING", "a trusted principal is required")
    if not isinstance(request.intent, MutationIntent):
        raise ForgeError("INPUT_TYPE_INVALID", "mutation intent must be typed")
    if request.intent.operation != envelope.operation:
        raise AuthorizationFailure("AUTHORIZATION_OPERATION_MISMATCH", "intent operation differs from request")
    target = _mutation_target(request)
    if not isinstance(target, ObjectRef):
        raise ForgeError("INPUT_TYPE_INVALID", "mutation target must be a typed ObjectRef")
    if not _intent_target_matches(request.intent, target):
        raise AuthorizationFailure("AUTHORIZATION_TARGET_MISMATCH", "intent target differs from request target")
    if not isinstance(request.preconditions, MutationPreconditions):
        raise ForgeError("INPUT_TYPE_INVALID", "mutation preconditions must be typed")
    if not isinstance(request.intent.mutation_scope, Mapping):
        raise AuthorizationFailure("AUTHORIZATION_SCOPE_MISMATCH", "mutation scope must be a mapping")

    return {
        "trusted_context": request.trusted_context,
        "operation": envelope.operation,
        "target": target,
        "mutation_scope": dict(request.intent.mutation_scope),
        "contract_hash": descriptor.contract_hash(),
        "intent_fingerprint": request.intent.intent_fingerprint(),
        "subject_expected_revision": request.preconditions.subject_expected_revision,
        "external_authority_precondition": request.external_authority_precondition,
        "authority_source_revision": request.preconditions.authority_source_revision,
        "authority_observed_raw_digest": request.preconditions.authority_observed_raw_digest,
        "candidate_raw_digest": request.preconditions.candidate_raw_digest,
        "normalized_plan_digest": request.normalized_plan_digest,
    }


def _mutation_failure(
    operation: str,
    code: str,
    message: str,
    correlation_id: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    audit["validation"] = code
    audit["context"] = "not_reached"
    audit["handler"] = "not_executed"
    return failure(
        operation=operation,
        code=code,
        message=message,
        correlation_id=correlation_id,
        audit=audit,
    )


def execute_mutation(envelope: MutationIngressRequest) -> dict[str, Any]:
    """Execute one exact, received-lease-only lifecycle mutation."""
    ensure_handlers_bound()
    operation = envelope.operation if isinstance(envelope, MutationIngressRequest) and isinstance(envelope.operation, str) else ""
    request = envelope.request if isinstance(envelope, MutationIngressRequest) else None
    trusted_context = getattr(request, "trusted_context", None)
    principal_binding = resolve_principal_binding(None, trusted_context)
    principal_audit = principal_binding.to_audit()
    intent = getattr(request, "intent", None)
    cid = resolve_correlation_id(getattr(intent, "correlation_id", None))

    if not isinstance(envelope, MutationIngressRequest):
        return _mutation_failure(operation, "INPUT_TYPE_INVALID", "typed mutation ingress request is required", cid, _audit(operation, None, None, cid, "invalid", "not_reached", "not_executed", principal_audit))

    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        err = UnsupportedOperationError(f"unsupported operation: {operation}")
        return failure_from_error(
            operation,
            err,
            correlation_id=cid,
            audit=_audit(operation, None, None, cid, "ok", "ok", "no_descriptor", principal_audit),
        )
    audit = _audit(
        operation,
        descriptor.protocol_version,
        descriptor.contract_hash(),
        cid,
        "pending",
        "pending",
        "pending",
        principal_audit,
    )
    if descriptor.read_write != WRITE_ONLY:
        return _mutation_failure(
            operation,
            "UNSUPPORTED_OPERATION",
            f"operation is not writable: {operation}",
            cid,
            audit,
        )

    try:
        bindings = _validate_mutation_request(envelope, descriptor)
    except AuthorizationFailure as exc:
        return _mutation_failure(operation, exc.code, exc.message, cid, audit)
    except ForgeError as exc:
        return _mutation_failure(operation, exc.code, exc.message, cid, audit)
    except (TypeError, ValueError) as exc:
        return _mutation_failure(operation, "INPUT_TYPE_INVALID", str(exc), cid, audit)
    audit["validation"] = "ok"

    try:
        CapabilityLeaseIssuer().validate_lease(
            envelope.request.lease,
            trusted_context=bindings["trusted_context"],
            operation=bindings["operation"],
            target=bindings["target"],
            mutation_scope=bindings["mutation_scope"],
            contract_hash=bindings["contract_hash"],
            intent_fingerprint=bindings["intent_fingerprint"],
            subject_expected_revision=bindings["subject_expected_revision"],
            external_authority_precondition=bindings["external_authority_precondition"],
            authority_source_revision=bindings["authority_source_revision"],
            authority_observed_raw_digest=bindings["authority_observed_raw_digest"],
            candidate_raw_digest=bindings["candidate_raw_digest"],
            normalized_plan_digest=bindings["normalized_plan_digest"],
            now=envelope.request.trusted_time,
        )
    except AuthorizationFailure as exc:
        return _mutation_failure(operation, exc.code, exc.message, cid, audit)
    except (TypeError, ValueError) as exc:
        return _mutation_failure(operation, "INPUT_TYPE_INVALID", str(exc), cid, audit)
    audit["context"] = "ok"

    handler = DEFAULT_REGISTRY.handler(operation)
    if handler is None:
        return _mutation_failure(operation, "UNSUPPORTED_OPERATION", f"operation has no handler: {operation}", cid, audit)

    try:
        result = handler(envelope)
    except ForgeError as exc:
        audit["handler"] = "forge_error"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    except Exception as exc:  # bounded internal guard
        audit["handler"] = "internal_error"
        err = ForgeError("FORGE_ERROR", f"internal error: {type(exc).__name__}", retryable=False)
        return failure_from_error(operation, err, correlation_id=cid, audit=audit)

    if not isinstance(result, LifecycleResult):
        return _mutation_failure(operation, "FORGE_ERROR", "invalid lifecycle handler result", cid, audit)
    audit["handler"] = "lifecycle"
    projected = lifecycle_envelope(result)
    projected["audit"] = audit
    return projected


def execute_execution(
    operation: str,
    params: dict[str, Any] | None = None,
    principal: object = None,
    correlation_id: str | None = None,
    trusted_context: TrustedContext | None = None,
) -> dict[str, Any]:
    """Execute one canonical execution operation via the bound ExecutionDispatcher."""
    cid = resolve_correlation_id(correlation_id)
    principal_binding = resolve_principal_binding(principal, trusted_context)
    principal_audit = principal_binding.to_audit()

    descriptor = get_execution_descriptor(operation) or DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        err = UnsupportedOperationError(f"unsupported operation: {operation}")
        return failure_from_error(
            operation,
            err,
            correlation_id=cid,
            audit=_audit(operation, None, None, cid, "ok", "ok", "no_descriptor", principal_audit),
        )

    audit = _audit(
        operation,
        descriptor.protocol_version,
        descriptor.contract_hash(),
        cid,
        "pending",
        "pending",
        "pending",
        principal_audit,
    )

    raw_params = dict(params) if isinstance(params, Mapping) else {}
    canonical_task_id = raw_params.pop("canonical_task_id", None)
    idempotency_key = raw_params.pop("idempotency_key", None)
    input_artifacts = raw_params.pop("input_artifacts", None)
    working_context = raw_params.pop("working_context", None)
    capability_requirements = raw_params.pop("capability_requirements", None)
    constraints = raw_params.pop("constraints", None)
    result_expectations = raw_params.pop("result_expectations", None)

    try:
        validated = validate_inputs(descriptor, raw_params)
    except ForgeError as exc:
        audit["validation"] = exc.code
        audit["context"] = "not_reached"
        audit["handler"] = "not_executed"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    except Exception as exc:
        audit["validation"] = "INPUT_TYPE_INVALID"
        audit["context"] = "not_reached"
        audit["handler"] = "not_executed"
        return failure(operation, "INPUT_TYPE_INVALID", str(exc), False, correlation_id=cid, audit=audit)

    audit["validation"] = "ok"
    audit["context"] = "ok"
    audit["principal"] = {
        **principal_audit,
        "trust": "operator" if trusted_context else "untrusted",
    }

    try:
        dispatcher = _require_bound_dispatcher()
    except ForgeError as exc:
        audit["handler"] = "dispatcher_unbound"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)

    try:
        if operation == "execution.task_start":
            executor = validated.get("executor")
            if not isinstance(executor, str) or not executor.strip():
                raise ForgeError("REQUIRED_INPUT_MISSING", "executor is required", retryable=False)

            role = validated.get("role")
            if not isinstance(role, str) or not role.strip():
                raise ForgeError("REQUIRED_INPUT_MISSING", "role is required", retryable=False)
            try:
                validate_canonical_role(role)
            except ValueError as e:
                raise ForgeError("PACKAGE_INVALID", str(e), retryable=False)

            instruction = validated.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                raise ForgeError("REQUIRED_INPUT_MISSING", "instruction is required", retryable=False)

            project_id = validated.get("project_id")
            if not isinstance(project_id, str) or not project_id.strip():
                raise ForgeError("REQUIRED_INPUT_MISSING", "project_id is required", retryable=False)

            subject_ref = validated.get("subject_ref")
            timeout = validated.get("timeout")

            constraints_dict = dict(constraints or {})
            if timeout is not None:
                constraints_dict["timeout_seconds"] = int(timeout)

            task_id = canonical_task_id or str(uuid.uuid4())
            idem_key = idempotency_key or str(uuid.uuid4())
            norm_input_artifacts = input_artifacts or ()
            norm_working_context = dict(working_context or {})
            norm_capability_requirements = dict(capability_requirements or {})
            norm_result_expectations = dict(result_expectations or {})

            package = ExecutionPackage.create(
                canonical_task_id=task_id,
                project_id=project_id,
                canonical_role=role,
                instruction=instruction,
                subject_ref=subject_ref,
                input_artifacts=norm_input_artifacts,
                working_context=norm_working_context,
                capability_requirements=norm_capability_requirements,
                constraints=constraints_dict,
                idempotency_key=idem_key,
                correlation_id=cid,
                result_expectations=norm_result_expectations,
            )

            dispatch_result = dispatcher.dispatch(package, target_executor_id=executor)
            audit["handler"] = "success"
            return success(
                operation=operation,
                data=dispatch_result.to_dict(),
                correlation_id=cid,
                audit=audit,
            )

        elif operation == "execution.task_status":
            task_id = validated.get("task_id")
            executor = validated.get("executor")
            if not task_id or not executor:
                raise ForgeError("REQUIRED_INPUT_MISSING", "task_id and executor are required", retryable=False)

            route = dispatcher.get_route(task_id)
            if route.executor_id != executor:
                audit["handler"] = "route_executor_mismatch"
                return failure(
                    operation,
                    "ROUTE_EXECUTOR_MISMATCH",
                    f"Task {task_id!r} is routed to executor {route.executor_id!r}, not requested {executor!r}",
                    False,
                    correlation_id=cid,
                    audit=audit,
                )

            status_res = dispatcher.status(task_id)
            audit["handler"] = "success"
            return success(
                operation=operation,
                data=status_res.to_dict(),
                correlation_id=cid,
                audit=audit,
            )

        elif operation == "execution.task_result":
            task_id = validated.get("task_id")
            executor = validated.get("executor")
            if not task_id or not executor:
                raise ForgeError("REQUIRED_INPUT_MISSING", "task_id and executor are required", retryable=False)

            route = dispatcher.get_route(task_id)
            if route.executor_id != executor:
                audit["handler"] = "route_executor_mismatch"
                return failure(
                    operation,
                    "ROUTE_EXECUTOR_MISMATCH",
                    f"Task {task_id!r} is routed to executor {route.executor_id!r}, not requested {executor!r}",
                    False,
                    correlation_id=cid,
                    audit=audit,
                )

            res = dispatcher.result(task_id)
            audit["handler"] = "success"
            return success(
                operation=operation,
                data=res.to_dict(),
                correlation_id=cid,
                audit=audit,
            )

        elif operation == "execution.task_cancel":
            task_id = validated.get("task_id")
            executor = validated.get("executor")
            if not task_id or not executor:
                raise ForgeError("REQUIRED_INPUT_MISSING", "task_id and executor are required", retryable=False)

            route = dispatcher.get_route(task_id)
            if route.executor_id != executor:
                audit["handler"] = "route_executor_mismatch"
                return failure(
                    operation,
                    "ROUTE_EXECUTOR_MISMATCH",
                    f"Task {task_id!r} is routed to executor {route.executor_id!r}, not requested {executor!r}",
                    False,
                    correlation_id=cid,
                    audit=audit,
                )

            replay_result = getattr(route, "_successful_cancel_result", None)
            if isinstance(replay_result, CancelResult):
                audit["handler"] = "cancel_replay"
                return success(
                    operation=operation,
                    data=replay_result.to_dict(),
                    correlation_id=cid,
                    audit=audit,
                )

            if route.last_known_state.is_terminal:
                audit["handler"] = "terminal_state"
                return failure(
                    operation,
                    "TASK_ALREADY_TERMINAL",
                    f"Task {task_id!r} is already in terminal state {route.last_known_state.value}",
                    False,
                    correlation_id=cid,
                    audit=audit,
                )

            try:
                cancel_res = dispatcher.cancel(task_id)
            except ValueError as exc:
                message = str(exc)
                known_code = next(
                    (
                        code
                        for code in (
                            "CANCEL_UNSUPPORTED",
                            "TASK_ALREADY_TERMINAL",
                            "TASK_STATE_UNKNOWN",
                            "TASK_NOT_FOUND",
                        )
                        if message.startswith(f"{code}:")
                    ),
                    "INTERNAL_MECHANICAL_ERROR",
                )
                audit["handler"] = known_code.lower()
                return failure(
                    operation,
                    known_code,
                    message,
                    False,
                    correlation_id=cid,
                    audit=audit,
                )

            if cancel_res.cancelled and cancel_res.state == CanonicalTaskState.CANCELLED:
                # Keep replay evidence on the existing per-task route, not in a
                # process-global semantic cache.
                setattr(route, "_successful_cancel_result", cancel_res)
            audit["handler"] = "success"
            return success(
                operation=operation,
                data=cancel_res.to_dict(),
                correlation_id=cid,
                audit=audit,
            )

        elif operation == "execution.executor_list":
            descriptors = [desc.to_dict() for desc in dispatcher.registry.list_descriptors()]
            audit["handler"] = "success"
            return success(
                operation=operation,
                data={"executors": descriptors},
                correlation_id=cid,
                audit=audit,
            )

        elif operation == "execution.executor_capabilities":
            executor = validated.get("executor")
            if not executor:
                raise ForgeError("REQUIRED_INPUT_MISSING", "executor is required", retryable=False)

            desc = dispatcher.registry.get_descriptor(executor)
            audit["handler"] = "success"
            return success(
                operation=operation,
                data=desc.to_dict(),
                correlation_id=cid,
                audit=audit,
            )

        else:
            raise UnsupportedOperationError(f"unsupported execution operation: {operation}")

    except ExecutorNotFoundError as exc:
        audit["handler"] = "executor_not_found"
        return failure(operation, "EXECUTOR_NOT_FOUND", str(exc), False, correlation_id=cid, audit=audit)
    except CapabilityMismatchError as exc:
        audit["handler"] = "capability_mismatch"
        return failure(operation, "CAPABILITY_MISMATCH", str(exc), False, correlation_id=cid, audit=audit)
    except NeedsSemanticChoiceError as exc:
        audit["handler"] = "needs_semantic_choice"
        choices = [{"executor_id": c} for c in exc.candidates]
        ret = failure(
            operation,
            "NEEDS_SEMANTIC_CHOICE",
            str(exc),
            False,
            correlation_id=cid,
            audit=audit,
            next_action="Specify target executor explicitly",
        )
        ret["semantic_choices"] = choices
        return ret
    except PackageInvalidError as exc:
        audit["handler"] = "package_invalid"
        return failure(operation, "PACKAGE_INVALID", str(exc), False, correlation_id=cid, audit=audit)
    except TaskNotFoundError as exc:
        audit["handler"] = "task_not_found"
        return failure(operation, "TASK_NOT_FOUND", str(exc), False, correlation_id=cid, audit=audit)
    except IdempotencyConflictError as exc:
        audit["handler"] = "idempotency_conflict"
        return failure(operation, "IDEMPOTENCY_CONFLICT", str(exc), False, correlation_id=cid, audit=audit)
    except ForgeError as exc:
        audit["handler"] = "forge_error"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    except Exception as exc:
        typed_error = _typed_execution_error(exc)
        if typed_error is not None:
            code, message, retryable = typed_error
            audit["handler"] = code.lower()
            return failure(
                operation,
                code,
                message,
                retryable,
                correlation_id=cid,
                audit=audit,
            )
        audit["handler"] = "internal_error"
        err = ForgeError(
            "INTERNAL_MECHANICAL_ERROR",
            "internal mechanical execution error",
            retryable=False,
        )
        return failure_from_error(operation, err, correlation_id=cid, audit=audit)


def execute(
    operation: str,
    params: dict[str, Any] | None = None,
    principal: object = None,
    correlation_id: str | None = None,
    trusted_context: TrustedContext | None = None,
) -> dict[str, Any]:
    """Execute one canonical Core operation.

    Never raises ForgeError; canonical errors are returned in the envelope.
    """
    if operation in CANONICAL_EXECUTION_OPERATIONS:
        return execute_execution(
            operation,
            params,
            principal=principal,
            correlation_id=correlation_id,
            trusted_context=trusted_context,
        )

    ensure_handlers_bound()
    cid = resolve_correlation_id(correlation_id)
    principal_binding = resolve_principal_binding(principal, trusted_context)
    principal_audit = principal_binding.to_audit()

    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        err = UnsupportedOperationError(f"unsupported operation: {operation}")
        return failure_from_error(
            operation,
            err,
            correlation_id=cid,
            audit=_audit(operation, None, None, cid, "ok", "ok", "no_descriptor", principal_audit),
        )

    handler = DEFAULT_REGISTRY.handler(operation)
    audit = _audit(
        operation,
        descriptor.protocol_version,
        descriptor.contract_hash(),
        cid,
        "pending",
        "pending",
        "pending",
        principal_audit,
    )

    if descriptor.read_write != READ_ONLY:
        audit["validation"] = "ok"
        audit["context"] = "ok"
        audit["handler"] = "not_executed"
        err = UnsupportedOperationError(f"operation is not read-only in M2: {operation}")
        return failure_from_error(operation, err, correlation_id=cid, audit=audit)

    try:
        validated = validate_inputs(descriptor, params)
    except ForgeError as exc:
        audit["validation"] = exc.code
        audit["context"] = "not_reached"
        audit["handler"] = "not_executed"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    audit["validation"] = "ok"

    try:
        context = _RESOLVER.resolve(
            descriptor,
            validated,
            principal,
            cid,
            trusted_context=trusted_context,
        )
    except ForgeError as exc:
        audit["context"] = exc.code
        audit["handler"] = "not_executed"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    audit["context"] = "ok"
    audit["principal"] = {
        **context.principal.to_audit(),
        "trust": context.principal_trust,
    }

    if handler is None:
        audit["handler"] = "no_handler"
        err = UnsupportedOperationError(f"operation has no handler: {operation}")
        return failure_from_error(operation, err, correlation_id=cid, audit=audit)

    try:
        payload = handler(context)
    except ForgeError as exc:
        audit["handler"] = "forge_error"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    except Exception as exc:  # bounded internal guard
        audit["handler"] = "internal_error"
        err = ForgeError("FORGE_ERROR", f"internal error: {type(exc).__name__}", retryable=False)
        return failure_from_error(operation, err, correlation_id=cid, audit=audit)

    return _normalize_handler_result(operation, payload, cid, audit)


__all__ = [
    "CANONICAL_EXECUTION_OPERATIONS",
    "EXECUTION_DESCRIPTORS",
    "EXECUTION_OPERATIONS",
    "EXECUTOR_CAPABILITIES_DESCRIPTOR",
    "EXECUTOR_LIST_DESCRIPTOR",
    "MutationIngressRequest",
    "TASK_CANCEL_DESCRIPTOR",
    "TASK_RESULT_DESCRIPTOR",
    "TASK_START_DESCRIPTOR",
    "TASK_STATUS_DESCRIPTOR",
    "bind_execution_dispatcher",
    "execute",
    "execute_execution",
    "execute_mutation",
    "get_execution_descriptor",
    "get_execution_dispatcher",
    "register_execution_descriptors",
    "reset_execution_dispatcher",
    "resolve_correlation_id",
]
