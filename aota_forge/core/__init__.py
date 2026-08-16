"""Forge Core — executor-neutral read-only domain.

Dependency invariant (M1): Core imports stdlib, PyYAML (repo-accepted strict
parser dependency), and new executor-neutral contracts only.  It MUST NOT
import plugin/aota-tools legacy orchestration, Hermes packages, WebUI,
Docker, outbox, or parent-wake modules.

M2-I import isolation: importing this package registers the canonical
operation DESCRIPTORS only (declarative contract surface).  Executable
handler binding is attached lazily, deterministically and idempotently at
the first ingress execution (``core.bootstrap.ensure_handlers_bound``), so
importing declarative contract modules never executes handler registration
as a package-import side effect.
"""

from aota_forge.core import catalog  # noqa: F401  (registers canonical descriptors)
from aota_forge.core.ingress import execute

__all__ = ["execute"]
