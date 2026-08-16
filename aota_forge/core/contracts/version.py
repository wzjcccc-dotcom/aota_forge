"""Explicit operation contract protocol version (M2-A).

``PROTOCOL_VERSION`` is the canonical, machine-readable version of the
operation contract descriptor protocol.  It is explicit and stable: it is
NOT derived from the package version, and every consumer (Core, CLI adapter,
future Hermes adapter, future MCP/API adapter) MUST read this constant
instead of inventing its own version signal.
"""

from __future__ import annotations

OPERATION_CONTRACT_PROTOCOL = "aota-forge.operation-contract"
PROTOCOL_VERSION = "1.0"


def protocol_version() -> str:
    """Return the canonical operation contract protocol version."""
    return PROTOCOL_VERSION
