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

import re
import uuid
from typing import Any

from aota_forge.core.context import ContextResolver
from aota_forge.core.contracts.descriptor import READ_ONLY
from aota_forge.core.contracts.errors import ForgeError, UnsupportedOperationError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import failure_from_error, success
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

    if isinstance(payload, dict) and payload.get("ok") is False:
        audit["handler"] = "handler_failure"
        merged = dict(payload)
        merged.setdefault("audit", audit)
        return merged

    audit["handler"] = "success"
    return success(
        operation=operation,
        data=payload.get("data", {}),
        evidence=payload.get("evidence", {}),
        warnings=payload.get("warnings", []),
        status=payload.get("status", "ok"),
        result=payload.get("result", "ok"),
        next_action=payload.get("next_action"),
        semantic_choices=payload.get("semantic_choices"),
        correlation_id=cid,
        audit=audit,
    )


__all__ = ["execute", "resolve_correlation_id"]
