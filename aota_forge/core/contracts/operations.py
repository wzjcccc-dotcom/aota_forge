"""Legacy M1 operation contract surface — compatibility shim (M2-A).

Canonical contract authority now lives in:

* ``core/contracts/descriptor.py`` — ``OperationContractDescriptor``
* ``core/contracts/registry.py`` — ``HandlerRegistry``
* ``core/contracts/version.py`` — ``PROTOCOL_VERSION``

The legacy coupled ``OperationContract`` shape, module-level ``REGISTRY``
dict, and ``register`` / ``get_contract`` / ``available_operations`` remain
importable unchanged so existing M1 handlers and adapters keep working
without modification.  Every legacy registration is converted into a
canonical descriptor and bound into the canonical runtime-local handler
registry; the legacy coupled shape is NOT contract authority anymore.

Handler binding stays separated from the descriptor: the canonical
descriptor never carries a callable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from aota_forge.core.contracts.descriptor import READ_ONLY, InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.version import PROTOCOL_VERSION

OperationHandler = Callable[["OperationContext"], dict[str, Any]]


@dataclass(frozen=True)
class OperationContract:
    name: str
    description: str
    read_only: bool
    inputs: dict[str, Any] = field(default_factory=dict)
    handler: Optional[OperationHandler] = None


REGISTRY: dict[str, OperationContract] = {}


def _descriptor_from_legacy(contract: OperationContract) -> OperationContractDescriptor:
    inputs = tuple(
        InputSpec(name=name, type=type_text)
        for name, type_text in sorted(contract.inputs.items(), key=lambda item: item[0])
    )
    return OperationContractDescriptor(
        name=contract.name,
        description=contract.description,
        inputs=inputs,
        read_write=READ_ONLY if contract.read_only else "write",
        idempotency="read" if contract.read_only else None,
    )


def register(contract: OperationContract) -> None:
    if contract.name in REGISTRY:
        raise ValueError(f"duplicate operation registration: {contract.name}")
    descriptor = _descriptor_from_legacy(contract)
    DEFAULT_REGISTRY.bind(descriptor, handler=contract.handler)
    REGISTRY[contract.name] = contract


def get_contract(name: str) -> OperationContract | None:
    descriptor = DEFAULT_REGISTRY.get(name)
    if descriptor is None:
        return None
    return OperationContract(
        name=descriptor.name,
        description=descriptor.description,
        read_only=descriptor.read_write == READ_ONLY,
        inputs={spec.name: spec.type for spec in descriptor.inputs},
        handler=DEFAULT_REGISTRY.handler(name),
    )


def available_operations() -> list[dict[str, Any]]:
    listing: list[dict[str, Any]] = []
    for name in DEFAULT_REGISTRY.names():
        descriptor = DEFAULT_REGISTRY.get(name)
        listing.append(
            {
                "operation": descriptor.name,
                "description": descriptor.description,
                "read_only": descriptor.read_write == READ_ONLY,
            }
        )
    return listing
