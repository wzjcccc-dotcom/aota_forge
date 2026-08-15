"""Forge Core — executor-neutral read-only domain.

Dependency invariant (M1): Core imports stdlib, PyYAML (repo-accepted strict
parser dependency), and new executor-neutral contracts only.  It MUST NOT
import plugin/aota-tools legacy orchestration, Hermes packages, WebUI,
Docker, outbox, or parent-wake modules.
"""

import aota_forge.core.handlers  # noqa: F401  (registers canonical operations)
from aota_forge.core.ingress import execute

__all__ = ["execute"]
