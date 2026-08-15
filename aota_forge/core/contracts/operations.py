"""Canonical operation contract registry (M1).

Every canonical operation declares its name, executor-neutral description,
and read-only classification.  In M1 every operation is read-only; there is
no generic ``control --operation anything`` mega-operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

OperationHandler = Callable[["OperationContext"], dict[str, Any]]


@dataclass(frozen=True)
class OperationContract:
    name: str
    description: str
    read_only: bool
    inputs: dict[str, Any] = field(default_factory=dict)
    handler: Optional[OperationHandler] = None


REGISTRY: dict[str, OperationContract] = {}


def register(contract: OperationContract) -> None:
    if contract.name in REGISTRY:
        raise ValueError(f"duplicate operation registration: {contract.name}")
    REGISTRY[contract.name] = contract


def get_contract(name: str) -> OperationContract | None:
    return REGISTRY.get(name)


def available_operations() -> list[dict[str, Any]]:
    return [
        {
            "operation": contract.name,
            "description": contract.description,
            "read_only": contract.read_only,
        }
        for contract in sorted(REGISTRY.values(), key=lambda c: c.name)
    ]
