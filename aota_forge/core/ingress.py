"""Read-only Unified Ingress skeleton (M1-A).

All canonical operations from all external adapters enter through
``execute``.  The ingress resolves the operation contract, builds an
executor-neutral OperationContext, routes to the Core handler, and wraps the
result into the canonical envelope.

Adapters MUST NOT reimplement operation routing or contract lookup; the
ingress is the single internal entry point.
"""

from __future__ import annotations

import uuid
from typing import Any

from aota_forge.core.context import OperationContext
from aota_forge.core.contracts import results
from aota_forge.core.contracts.errors import ForgeError, UnsupportedOperationError
from aota_forge.core.contracts.operations import get_contract


def execute(
    operation: str,
    params: dict[str, Any] | None = None,
    principal: str = "library",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Execute one canonical read-only Core operation.

    Never raises ForgeError; canonical errors are returned in the envelope.
    """
    contract = get_contract(operation)
    if contract is None:
        err = UnsupportedOperationError(f"unsupported operation: {operation}")
        return results.failure(operation, err.code, err.message, err.retryable)
    if not contract.read_only:
        err = UnsupportedOperationError(f"operation is not read-only in M1: {operation}")
        return results.failure(operation, err.code, err.message, err.retryable)
    if contract.handler is None:
        err = UnsupportedOperationError(f"operation has no handler: {operation}")
        return results.failure(operation, err.code, err.message, err.retryable)

    cid = correlation_id or uuid.uuid4().hex
    context = OperationContext(
        operation=operation,
        correlation_id=cid,
        principal=principal,
        params=params if params is not None else {},
    )
    try:
        payload = contract.handler(context)
        if isinstance(payload, dict) and payload.get("ok") is False:
            return payload
        return results.success(
            operation=operation,
            data=payload.get("data", {}),
            evidence=payload.get("evidence", {}),
            warnings=payload.get("warnings", []),
            status=payload.get("status", "ok"),
            result=payload.get("result", "ok"),
            next_action=payload.get("next_action"),
            semantic_choices=payload.get("semantic_choices"),
            correlation_id=cid,
        )
    except ForgeError as exc:
        return results.failure(
            operation=operation,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            correlation_id=cid,
        )
    except Exception as exc:  # bounded internal guard
        return results.failure(
            operation=operation,
            code="FORGE_ERROR",
            message=f"internal error: {type(exc).__name__}",
            retryable=False,
            correlation_id=cid,
        )
