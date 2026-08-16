"""Runtime-local executable handler registry (M2-A).

``HandlerRegistry`` binds canonical ``OperationContractDescriptor`` objects
to process-local callables.  The registry is the runtime authority for
"which executable implements this contract" and is intentionally separate
from the descriptor: handler identity is NOT contract semantics and never
participates in serialization or ``contract_hash``.

Registration is fail-closed and deterministic:

* duplicate canonical operation names raise
  ``DuplicateOperationRegistrationError`` (never last-registration-wins)
* lookups of missing operations are deterministic (``None`` or
  ``UnknownOperationError``)
* available-operation order is canonical (sorted by name)
* descriptor lookup never executes a handler
"""

from __future__ import annotations

from typing import Any, Callable

from aota_forge.core.contracts.descriptor import OperationContractDescriptor

Handler = Callable[[Any], Any]


class DuplicateOperationRegistrationError(ValueError):
    """Deterministic fail-closed outcome for duplicate canonical registration."""

    def __init__(self, name: str) -> None:
        super().__init__(f"duplicate operation registration: {name}")
        self.name = name


class UnknownOperationError(KeyError):
    """Deterministic outcome for lookups of unregistered operations."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
        self.message = f"unknown operation: {name}"


class HandlerRegistry:
    """Runtime-local registry mapping canonical descriptors to executable handlers."""

    def __init__(self) -> None:
        self._bindings: dict[str, tuple[OperationContractDescriptor, Handler | None]] = {}

    def bind(
        self,
        descriptor: OperationContractDescriptor,
        handler: Handler | None = None,
    ) -> None:
        """Bind one descriptor (and optionally its runtime handler).

        Fails closed with ``DuplicateOperationRegistrationError`` on any
        duplicate canonical operation name.
        """
        if descriptor.name in self._bindings:
            raise DuplicateOperationRegistrationError(descriptor.name)
        descriptor.validate()
        self._bindings[descriptor.name] = (descriptor, handler)

    def get(self, name: str) -> OperationContractDescriptor | None:
        """Look up a descriptor without executing any handler."""
        binding = self._bindings.get(name)
        return binding[0] if binding is not None else None

    def require(self, name: str) -> OperationContractDescriptor:
        """Look up a descriptor; raise ``UnknownOperationError`` when missing."""
        descriptor = self.get(name)
        if descriptor is None:
            raise UnknownOperationError(name)
        return descriptor

    def handler(self, name: str) -> Handler | None:
        """Resolve the runtime-local handler for an operation (None when missing)."""
        binding = self._bindings.get(name)
        return binding[1] if binding is not None else None

    def has(self, name: str) -> bool:
        return name in self._bindings

    def names(self) -> tuple[str, ...]:
        """Canonical deterministic order of registered operation names."""
        return tuple(sorted(self._bindings))

    def available(self) -> list[dict[str, Any]]:
        """Deterministic listing of bound operations (sorted by name)."""
        listing: list[dict[str, Any]] = []
        for name in self.names():
            descriptor = self._bindings[name][0]
            listing.append(
                {
                    "operation": descriptor.name,
                    "description": descriptor.description,
                    "read_only": descriptor.read_write == "read",
                    "protocol_version": descriptor.protocol_version,
                    "contract_hash": descriptor.contract_hash(),
                }
            )
        return listing

    def __len__(self) -> int:
        return len(self._bindings)


DEFAULT_REGISTRY = HandlerRegistry()
