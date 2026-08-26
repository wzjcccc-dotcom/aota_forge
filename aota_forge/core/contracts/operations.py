"""Legacy operation contract surface — projection-only compatibility shim (S1/M2/W2).

Canonical contract authority now lives in:

* ``.aota/contracts/operations.yaml`` — single canonical YAML instance authority
* ``core/contracts/loader.py`` — validated YAML → ``OperationContractDescriptor``
* ``core/catalog.py`` — YAML projection that registers general/lifecycle descriptors
* ``core/ingress.py`` — YAML projection for execution descriptors

The legacy coupled ``OperationContract`` shape, module-level ``REGISTRY``
dict, and ``register`` / ``get_contract`` / ``available_operations`` remain
importable for compatibility, but they are now **projection-only** and must
not create a new canonical contract outside YAML authority.

Behavior after W2 cutover:
* ``register(OperationContract(...))`` no longer defines contract semantics.
  It verifies the requested legacy contract matches the YAML-backed canonical
  descriptor and binds the runtime handler only if appropriate; unknown or
  mismatching canonical identity fails closed.
* ``REGISTRY`` and ``get_contract``/``available_operations`` are derived
  projections from the YAML-backed ``DEFAULT_REGISTRY``.

Handler binding stays separated from the descriptor: the canonical
descriptor never carries a callable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from aota_forge.core.contracts.descriptor import READ_ONLY, InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

OperationHandler = Callable[["OperationContext"], dict[str, Any]]


@dataclass(frozen=True)
class OperationContract:
    name: str
    description: str
    read_only: bool
    inputs: dict[str, Any] = field(default_factory=dict)
    handler: Optional[OperationHandler] = None


# Legacy REGISTRY is now projection-only cache, not authority.
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


def _canonical_descriptor(name: str) -> OperationContractDescriptor | None:
    # Prefer DEFAULT_REGISTRY (already YAML-backed via catalog) first;
    # fall back to direct YAML map for operations not in DEFAULT_REGISTRY
    # (e.g. execution.*).
    desc = DEFAULT_REGISTRY.get(name)
    if desc is not None:
        return desc
    try:
        from aota_forge.core.contracts.loader import (
            discover_canonical_project_root,
            load_operation_descriptor_map,
        )

        root = discover_canonical_project_root()
        m = load_operation_descriptor_map(root)
        return m.get(name)
    except Exception:
        return None


def register(contract: OperationContract) -> None:
    """Compatibility projection for legacy registration.

    Does NOT create canonical semantics. Verifies the requested legacy
    contract matches the YAML-backed canonical descriptor, then binds the
    handler only if appropriate. Unknown or mismatching identity fails closed.
    """
    if contract.name in REGISTRY:
        raise ValueError(f"duplicate operation registration: {contract.name}")
    # Also guard against duplicate in DEFAULT_REGISTRY handler already bound?
    # The legacy check above plus YAML authority is the gate.
    canonical = _canonical_descriptor(contract.name)
    if canonical is None:
        from aota_forge.core.contracts.loader import DeclarativeContractError

        raise DeclarativeContractError(
            "DECLARATIVE_CONTRACT_INVALID",
            f"unknown operation not in YAML authority: {contract.name}",
        )
    derived = _descriptor_from_legacy(contract)
    if derived.to_dict() != canonical.to_dict():
        from aota_forge.core.contracts.loader import DeclarativeContractError

        raise DeclarativeContractError(
            "DECLARATIVE_CONTRACT_INVALID",
            f"legacy contract does not match YAML canonical for {contract.name}",
        )
    # Handler binding: only if descriptor is in DEFAULT_REGISTRY topology
    existing = DEFAULT_REGISTRY.get(contract.name)
    if existing is not None:
        if contract.handler is not None:
            # bind_handler fails closed on duplicate, unknown, or already bound
            current_handler = DEFAULT_REGISTRY.handler(contract.name)
            if current_handler is not None:
                raise ValueError(f"duplicate operation handler registration: {contract.name}")
            DEFAULT_REGISTRY.bind_handler(contract.name, contract.handler)
    else:
        # For operations not in DEFAULT_REGISTRY (e.g. execution.*), we do
        # not add a new canonical to DEFAULT_REGISTRY merely for uniformity.
        # Keep as projection-only; handler binding for execution stays in
        # ExecutionDispatcher, not HandlerRegistry.
        # Still record legacy projection for compatibility visibility.
        pass
    # Store projection derived from canonical, not raw contract, to keep
    # REGISTRY as projection-only.
    REGISTRY[contract.name] = OperationContract(
        name=canonical.name,
        description=canonical.description,
        read_only=canonical.read_write == READ_ONLY,
        inputs={spec.name: spec.type for spec in canonical.inputs},
        handler=contract.handler if contract.handler is not None else DEFAULT_REGISTRY.handler(contract.name),
    )


def get_contract(name: str) -> OperationContract | None:
    # Projection from YAML-backed registry; handler identity from DEFAULT_REGISTRY
    canonical = _canonical_descriptor(name)
    if canonical is None:
        return None
    # Prefer projection that matches canonical semantics
    handler = DEFAULT_REGISTRY.handler(name)
    # If no handler yet, check legacy REGISTRY cache
    if handler is None and name in REGISTRY:
        handler = REGISTRY[name].handler
    return OperationContract(
        name=canonical.name,
        description=canonical.description,
        read_only=canonical.read_write == READ_ONLY,
        inputs={spec.name: spec.type for spec in canonical.inputs},
        handler=handler,
    )


def available_operations() -> list[dict[str, Any]]:
    # Retain deterministic sorted ordering and compatible shape over
    # DEFAULT_REGISTRY (topology preserved: only general/lifecycle).
    listing: list[dict[str, Any]] = []
    for name in DEFAULT_REGISTRY.names():
        descriptor = DEFAULT_REGISTRY.get(name)
        if descriptor is None:
            continue
        listing.append(
            {
                "operation": descriptor.name,
                "description": descriptor.description,
                "read_only": descriptor.read_write == READ_ONLY,
            }
        )
    return listing
