"""Canonical machine result contract (M1-G).

Success shape:

    {"ok": true, "operation": str, "data": {...}, "evidence": {...}, "warnings": []}

Error shape:

    {"ok": false, "operation": str,
     "error": {"code": str, "message": str, "retryable": bool}}

Machine fields required by the Plan (status/result/errors/blockers/
semantic_choices/next_action/correlation_id) are emitted when a command has
meaningful content; the canonical five-key envelope remains stable.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from aota_forge.core.contracts.mutation import MutationResult


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def success(
    operation: str,
    data: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    status: str = "ok",
    result: str = "ok",
    next_action: str | None = None,
    semantic_choices: list[dict[str, Any]] | None = None,
    correlation_id: str | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = {
        "ok": True,
        "operation": operation,
        "data": data if data is not None else {},
        "evidence": evidence if evidence is not None else {},
        "warnings": warnings if warnings is not None else [],
        "status": status,
        "result": result,
        "errors": [],
        "blockers": [],
        "semantic_choices": semantic_choices if semantic_choices is not None else [],
        "next_action": next_action,
        "correlation_id": correlation_id or new_correlation_id(),
    }
    if audit is not None:
        envelope["audit"] = audit
    return envelope


def failure(
    operation: str,
    code: str,
    message: str,
    retryable: bool = False,
    correlation_id: str | None = None,
    blockers: list[str] | None = None,
    next_action: str | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = {
        "ok": False,
        "operation": operation,
        "data": {},
        "evidence": {},
        "warnings": [],
        "error": {"code": code, "message": message, "retryable": retryable},
        "status": "error",
        "result": "error",
        "errors": [{"code": code, "message": message, "retryable": retryable}],
        "blockers": blockers if blockers is not None else [],
        "semantic_choices": [],
        "next_action": next_action,
        "correlation_id": correlation_id or new_correlation_id(),
    }
    if audit is not None:
        envelope["audit"] = audit
    return envelope


def failure_from_error(
    operation: str,
    exc: Exception,
    correlation_id: str | None = None,
    blockers: list[str] | None = None,
    next_action: str | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize any canonical ForgeError into the machine envelope."""
    from aota_forge.core.contracts.errors import ForgeError

    if isinstance(exc, ForgeError):
        return failure(
            operation,
            exc.code,
            exc.message,
            exc.retryable,
            correlation_id=correlation_id,
            blockers=blockers,
            next_action=next_action,
            audit=audit,
        )
    return failure(
        operation,
        "FORGE_ERROR",
        f"internal error: {type(exc).__name__}",
        False,
        correlation_id=correlation_id,
        blockers=blockers,
        next_action=next_action,
        audit=audit,
    )


def mutation_envelope(
    mutation: MutationResult,
    *,
    data: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Project a canonical mutation result without promoting local success.

    Only verified applied/replayed effects use the existing success shape.  All
    other effects remain explicit machine results, including an unknown
    authoritative outcome after local handler success.
    """
    if not isinstance(mutation, MutationResult):
        raise TypeError("mutation must be MutationResult")
    if mutation.usable_success:
        envelope = success(
            mutation.operation,
            data=data,
            evidence=evidence,
            warnings=warnings,
            status=mutation.status,
            result=mutation.result,
            next_action=mutation.next_action,
            semantic_choices=list(mutation.semantic_choices),
            correlation_id=mutation.correlation_id,
        )
    else:
        first_error = next(iter(mutation.errors), None)
        code = str(first_error.get("code")) if first_error and first_error.get("code") else mutation.mutation_effect.value
        message = str(first_error.get("message")) if first_error and first_error.get("message") else mutation.mutation_effect.value
        envelope = failure(
            mutation.operation,
            code,
            message,
            correlation_id=mutation.correlation_id,
            blockers=list(mutation.blockers),
            next_action=mutation.next_action,
        )
        envelope["status"] = mutation.status
        envelope["result"] = mutation.result
        envelope["semantic_choices"] = list(mutation.semantic_choices)
    envelope["mutation_effect"] = mutation.mutation_effect.value
    envelope["authoritative_effect_confirmed"] = mutation.authoritative_effect_confirmed.value
    envelope["effect_evidence"] = [dict(item) for item in mutation.effect_evidence]
    return envelope


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
