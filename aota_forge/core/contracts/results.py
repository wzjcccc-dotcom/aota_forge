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
from dataclasses import dataclass, field
from typing import Any, Mapping

from aota_forge.core.contracts.mutation import MutationEffect, MutationResult


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

    Verified effects use the existing success shape.  A bounded no-op and a
    request for semantic choice are also non-error operation outcomes; their
    effect state and choice channel remain explicit instead of being converted
    into generic errors.  Other effects remain explicit failures, including an
    unknown authoritative outcome after local handler success.
    """
    if not isinstance(mutation, MutationResult):
        raise TypeError("mutation must be MutationResult")
    if mutation.usable_success or mutation.mutation_effect in {
        MutationEffect.NO_EFFECT,
        MutationEffect.NEEDS_SEMANTIC_CHOICE,
    }:
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


@dataclass(frozen=True)
class LifecycleResult:
    """Bounded M4 lifecycle result with a distinct mechanical result code."""

    operation: str
    code: str
    mutation: MutationResult
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation:
            raise ValueError("lifecycle operation is required")
        if self.mutation.operation != self.operation:
            raise ValueError("lifecycle result operation does not match mutation")
        object.__setattr__(self, "data", dict(self.data))

    @property
    def mutation_effect(self) -> MutationEffect:
        return self.mutation.mutation_effect

    @property
    def replayed(self) -> bool:
        return self.mutation.mutation_effect is MutationEffect.REPLAYED_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "code": self.code,
            "data": dict(self.data),
            "mutation": self.mutation.to_dict(),
        }

    def to_envelope(self) -> dict[str, Any]:
        return lifecycle_envelope(self)


def lifecycle_result(
    operation: str,
    code: str,
    effect: MutationEffect,
    *,
    message: str | None = None,
    data: Mapping[str, Any] | None = None,
    blockers: tuple[str, ...] = (),
    semantic_choices: tuple[Mapping[str, Any], ...] = (),
    next_action: str | None = None,
    correlation_id: str | None = None,
) -> LifecycleResult:
    """Build the canonical M4 effect/confirmation pairing."""
    confirmation = {
        MutationEffect.NO_EFFECT: "no",
        MutationEffect.APPLIED_VERIFIED: "yes",
        MutationEffect.REPLAYED_VERIFIED: "yes",
        MutationEffect.BLOCKED: "no",
        MutationEffect.CONFLICT: "no",
        MutationEffect.NEEDS_SEMANTIC_CHOICE: "no",
        MutationEffect.OUTCOME_UNKNOWN: "unknown",
        MutationEffect.FAILED_NO_EFFECT: "no",
    }[effect]
    errors = ()
    if message is not None and effect is not MutationEffect.NEEDS_SEMANTIC_CHOICE:
        errors = ({"code": code, "message": message, "retryable": False},)
    status = {
        MutationEffect.APPLIED_VERIFIED: "completed",
        MutationEffect.REPLAYED_VERIFIED: "replayed",
        MutationEffect.NEEDS_SEMANTIC_CHOICE: "needs_input",
        MutationEffect.BLOCKED: "blocked",
        MutationEffect.CONFLICT: "conflict",
    }.get(effect, "no_effect")
    mutation = MutationResult(
        operation=operation,
        status=status,
        result=code,
        mutation_effect=effect,
        authoritative_effect_confirmed=confirmation,
        errors=errors,
        blockers=blockers,
        semantic_choices=semantic_choices,
        next_action=next_action,
        correlation_id=correlation_id,
    )
    return LifecycleResult(operation=operation, code=code, mutation=mutation, data=data or {})


def lifecycle_envelope(result: LifecycleResult) -> dict[str, Any]:
    """Project lifecycle errors without collapsing no-effect into a generic error."""
    envelope = mutation_envelope(result.mutation, data=dict(result.data))
    envelope["lifecycle_code"] = result.code
    if result.mutation.mutation_effect is MutationEffect.NO_EFFECT and result.mutation.errors:
        envelope["errors"] = [dict(item) for item in result.mutation.errors]
    return envelope


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
