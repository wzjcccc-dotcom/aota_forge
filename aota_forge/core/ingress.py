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
)
from aota_forge.core.contracts.errors import ForgeError, UnsupportedOperationError
from aota_forge.core.contracts.mutation import MutationIntent, MutationPreconditions
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import LifecycleResult, failure, failure_from_error, lifecycle_envelope, success
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.bootstrap import ensure_handlers_bound
from aota_forge.core.identity.refs import ObjectRef
from aota_forge.core.transaction import TransactionStore
from aota_forge.core.transitions import PlanInitRequest, PlanRetirementRequest

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_RESOLVER = ContextResolver()


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


def execute(
    operation: str,
    params: dict[str, Any] | None = None,
    principal: object = None,
    correlation_id: str | None = None,
    trusted_context: TrustedContext | None = None,
) -> dict[str, Any]:
    """Execute one canonical read-only Core operation.

    Never raises ForgeError; canonical errors are returned in the envelope.
    """
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


__all__ = ["MutationIngressRequest", "execute", "execute_mutation", "resolve_correlation_id"]
