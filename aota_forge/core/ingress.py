"""Canonical read-only Unified Ingress (M1-A / M2-B).

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
from typing import Any

from aota_forge.core.context import ContextResolver
from aota_forge.core.contracts.descriptor import READ_ONLY
from aota_forge.core.contracts.errors import ForgeError, UnsupportedOperationError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import failure, failure_from_error, success
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.bootstrap import ensure_handlers_bound

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_RESOLVER = ContextResolver()


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
) -> dict[str, Any]:
    return {
        "operation": operation,
        "protocol_version": protocol_version or PROTOCOL_VERSION,
        "contract_hash": contract_hash,
        "correlation_id": correlation_id,
        "validation": validation,
        "context": context,
        "handler": handler,
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


def execute(
    operation: str,
    params: dict[str, Any] | None = None,
    principal: str = "library",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Execute one canonical read-only Core operation.

    Never raises ForgeError; canonical errors are returned in the envelope.
    """
    ensure_handlers_bound()
    cid = resolve_correlation_id(correlation_id)

    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        err = UnsupportedOperationError(f"unsupported operation: {operation}")
        return failure_from_error(
            operation,
            err,
            correlation_id=cid,
            audit=_audit(operation, None, None, cid, "ok", "ok", "no_descriptor"),
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
        context = _RESOLVER.resolve(descriptor, validated, principal, cid)
    except ForgeError as exc:
        audit["context"] = exc.code
        audit["handler"] = "not_executed"
        return failure_from_error(operation, exc, correlation_id=cid, audit=audit)
    audit["context"] = "ok"

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


__all__ = ["execute", "resolve_correlation_id"]
