"""Canonical M2 operation descriptor catalog (M2-I).

Declarative registration of the canonical M2 operation contract surface.
Importing this module registers ONLY descriptors (serializable contract
semantics) into the canonical ``HandlerRegistry`` through the legacy M1
compatibility shim; it NEVER binds executable handlers.

Handler binding is a runtime-local concern owned by ``core.handlers`` and is
attached lazily, deterministically and idempotently at the first ingress
execution through ``core.bootstrap.ensure_handlers_bound``.  Importing
declarative contract/read-model modules therefore never executes handler
registration as a package-import side effect
(DECLARATIVE_IMPORT_TRIGGERS_HANDLER_BINDING=no).
"""

from __future__ import annotations

from aota_forge.core.contracts.operations import OperationContract, register

_CANONICAL_OPERATIONS: tuple[OperationContract, ...] = (
    OperationContract(
        name="project.resolve",
        description="resolve exactly one registered project deterministically (0/1/many fail-closed)",
        read_only=True,
        inputs={"workspace_id": "str", "project_id": "str", "registry_path": "str"},
    ),
    OperationContract(
        name="git.inspect",
        description="read-only git state inspection within the resolved project boundary (full SHA)",
        read_only=True,
        inputs={"workspace_id": "str", "project_id": "str", "registry_path": "str"},
    ),
    OperationContract(
        name="runtime.status",
        description="executor-neutral runtime process status by pid",
        read_only=True,
        inputs={"pid": "int"},
    ),
    OperationContract(
        name="host.status",
        description="host mechanical read-only inspection; requires no Plan/SPEC/Profile Task",
        read_only=True,
        inputs={
            "pidfile": "str?",
            "receipt": "str?",
            "workspace_id": "str?",
            "project_id": "str?",
            "registry_path": "str?",
        },
    ),
    OperationContract(
        name="operations.list",
        description="list canonical operations registered in the ingress contract registry",
        read_only=True,
        inputs={},
    ),
)

_REGISTERED = False


def register_canonical_descriptors() -> None:
    """Register the canonical M2 operation descriptors exactly once."""
    global _REGISTERED
    if _REGISTERED:
        return
    for contract in _CANONICAL_OPERATIONS:
        register(contract)
    _REGISTERED = True


register_canonical_descriptors()
