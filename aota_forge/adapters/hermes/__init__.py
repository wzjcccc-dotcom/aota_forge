"""Executor-specific read-only semantic adapter projection (M2-F).

Proves the convergence invariant:

    Hermes-style adapter input
    -> canonical semantic operation
    -> the same Unified Ingress as direct library invocation and the CLI

The adapter is intentionally thin and source-only: it requires no live
Hermes runtime, no legacy Profile Task lifecycle, no current_* authority,
no Hermes WebUI, no Docker Hermes, no outbox, no parent-wake, no
ProcessRegistry identity, no live session and no Hermes-private task id.

Markers:

    HERMES_ADAPTER_LIVE_RUNTIME_REQUIRED=no
    HERMES_ADAPTER_LEGACY_CONTROL_PLANE_DEPENDENCY=no
"""

from __future__ import annotations

from .invoke import detect_contract_drift, execute_request
from .schema import (
    AdapterRequest,
    AdapterRequestInvalidError,
    build_request,
    operation_request_schema,
)

HERMES_ADAPTER_LIVE_RUNTIME_REQUIRED = "no"
HERMES_ADAPTER_LEGACY_CONTROL_PLANE_DEPENDENCY = "no"

__all__ = [
    "AdapterRequest",
    "AdapterRequestInvalidError",
    "build_request",
    "detect_contract_drift",
    "execute_request",
    "operation_request_schema",
    "HERMES_ADAPTER_LIVE_RUNTIME_REQUIRED",
    "HERMES_ADAPTER_LEGACY_CONTROL_PLANE_DEPENDENCY",
]
