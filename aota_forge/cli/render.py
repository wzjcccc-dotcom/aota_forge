"""Human-readable rendering of canonical envelopes."""

from __future__ import annotations

import json
from typing import Any


def render_human(payload: dict[str, Any]) -> str:
    if payload.get("ok"):
        data = payload.get("data", {})
        lines = [f"OK  {payload.get('operation', 'operation')}"]
        if data:
            lines.append(json.dumps(data, ensure_ascii=False, indent=2))
        warnings = payload.get("warnings", [])
        if warnings:
            lines.append("warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        return "\n".join(lines)
    error = payload.get("error") or (payload.get("errors") or [{}])[0]
    lines = [
        f"ERROR  {payload.get('operation', 'operation')}",
        f"code: {error.get('code', 'FORGE_ERROR')}",
        f"message: {error.get('message', 'unknown error')}",
    ]
    blockers = payload.get("blockers") or []
    if blockers:
        lines.append("blockers: " + ", ".join(blockers))
    next_action = payload.get("next_action")
    if next_action:
        lines.append(f"next_action: {next_action}")
    return "\n".join(lines)
