"""Descriptor-driven CLI schema projection (M2-F).

The CLI argument schema derives from the canonical
``OperationContractDescriptor`` (M2-A) — argument names, types and
required-ness are never hand-maintained in the CLI.  Trusted adapter
metadata keys (``registry_path`` / ``pidfile`` / ``receipt``) are NOT
model-facing CLI flags; they are resolved through the operator trusted
configuration channel instead.

Only transport formatting happens here (e.g. int-typed inputs are parsed
as ints); no semantic validation, no type coercion beyond argparse's
transport parse.
"""

from __future__ import annotations

import argparse
from typing import Any

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS

# Transport parse for the small closed set of declared input types; anything
# unknown stays a string and is validated by the canonical ingress.
_TRANSPORT_TYPES = {"int": int}


def semantic_input_specs(descriptor: OperationContractDescriptor) -> tuple:
    """Declared descriptor inputs that are model-facing CLI arguments."""
    return tuple(spec for spec in descriptor.inputs if spec.name not in TRUSTED_ADAPTER_KEYS)


def operation_schema(operation: str) -> dict[str, Any] | None:
    """Project one canonical operation descriptor into the CLI schema."""
    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        return None
    return {
        "operation": descriptor.name,
        "arguments": [
            {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
            for spec in semantic_input_specs(descriptor)
        ],
        "protocol_version": descriptor.protocol_version,
        "contract_hash": descriptor.contract_hash(),
    }


def attach_semantic_arguments(parser: argparse.ArgumentParser, operation: str) -> None:
    """Attach descriptor-derived semantic flags to one CLI subparser."""
    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        return
    for spec in semantic_input_specs(descriptor):
        base_type = spec.type[:-1] if spec.type.endswith("?") else spec.type
        parser.add_argument(
            f"--{spec.name.replace('_', '-')}",
            type=_TRANSPORT_TYPES.get(base_type, str),
            required=not spec.type.endswith("?"),
            help=f"{base_type} semantic input (validated by canonical ingress)",
        )
