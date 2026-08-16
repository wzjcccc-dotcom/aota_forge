"""Executor-neutral operation contracts.

Canonical contract foundation (M2-A):

* ``OperationContractDescriptor`` — portable declarative machine contract
* ``HandlerRegistry`` / ``DEFAULT_REGISTRY`` — runtime-local handler binding
* ``PROTOCOL_VERSION`` — explicit operation contract protocol version

The legacy coupled ``OperationContract`` shim lives in
``aota_forge.core.contracts.operations`` and remains importable unchanged
for M1 compatibility; it is NOT contract authority anymore.
"""

from aota_forge.core.contracts.descriptor import (
    OperationContractDescriptor,
    InputSpec,
    READ_ONLY,
    READ_WRITE,
    WRITE_ONLY,
)
from aota_forge.core.contracts.registry import (
    DuplicateOperationRegistrationError,
    HandlerRegistry,
    UnknownOperationError,
    DEFAULT_REGISTRY,
)
from aota_forge.core.contracts.version import (
    OPERATION_CONTRACT_PROTOCOL,
    PROTOCOL_VERSION,
    protocol_version,
)

__all__ = [
    "OperationContractDescriptor",
    "InputSpec",
    "READ_ONLY",
    "READ_WRITE",
    "WRITE_ONLY",
    "HandlerRegistry",
    "DEFAULT_REGISTRY",
    "DuplicateOperationRegistrationError",
    "UnknownOperationError",
    "OPERATION_CONTRACT_PROTOCOL",
    "PROTOCOL_VERSION",
    "protocol_version",
]
