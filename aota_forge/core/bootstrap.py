"""Deterministic lazy handler bootstrap (M2-I).

Canonical execution binds the default handlers exactly once per process,
lazily and idempotently, at the first ingress execution
(``ensure_handlers_bound``).  Importing declarative contract/read-model
modules never triggers handler registration as a package-import side effect
(DECLARATIVE_IMPORT_TRIGGERS_HANDLER_BINDING=no), while canonical execution
still reliably binds the default handlers before any operation is routed.

Properties:

    HANDLER_BOOTSTRAP_DETERMINISTIC=yes  (same handlers, same order, every time)
    HANDLER_BOOTSTRAP_IDEMPOTENT=yes     (repeated calls are no-ops)
"""

from __future__ import annotations

_HANDLERS_BOUND = False


def ensure_handlers_bound() -> None:
    """Bind the default canonical handlers exactly once (idempotent).

    Imports ``aota_forge.core.handlers``, whose import-time registration
    attaches each canonical handler onto its already-registered descriptor
    (owned by ``core.catalog``).
    """
    global _HANDLERS_BOUND
    if _HANDLERS_BOUND:
        return
    import aota_forge.core.handlers  # noqa: F401  (import-time registration)
    _HANDLERS_BOUND = True
