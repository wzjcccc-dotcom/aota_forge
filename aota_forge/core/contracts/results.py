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
) -> dict[str, Any]:
    return {
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


def failure(
    operation: str,
    code: str,
    message: str,
    retryable: bool = False,
    correlation_id: str | None = None,
    blockers: list[str] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    return {
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


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
