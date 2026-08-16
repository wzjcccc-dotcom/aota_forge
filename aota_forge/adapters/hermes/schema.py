"""Descriptor-driven Hermes adapter request schema (M2-F).

The canonical ``OperationContractDescriptor`` (M2-A) is the single source
for the adapter request schema: argument names, types, required-ness,
protocol_version and contract_hash are all derived — never
hand-maintained.

The request envelope has two namespaces:

* ``arguments`` — the model-facing semantic surface (declared descriptor
  inputs only).  Semantic validation deliberately stays at the canonical
  ingress; the adapter performs pure transport syntax checks only.
* ``trusted`` — the explicitly trusted adapter metadata channel, restricted
  to the canonical trusted-key enumeration (adapter-private resolved paths
  registry_path / pidfile / receipt, and logical host resource references
  registry_id / pidfile_id / receipt_id).  Model-facing filesystem paths
  are denied: trusted metadata cannot enter through ``arguments``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS
from aota_forge.core.context import TrustedContext

MAX_ARGUMENT_KEYS = 128
MAX_TRUSTED_KEYS = 16


class AdapterRequestInvalidError(ForgeError):
    """Pure transport syntax violation in the adapter request envelope."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__("ADAPTER_REQUEST_INVALID", message, retryable)


@dataclass(frozen=True)
class AdapterRequest:
    """One Hermes-style adapter request projected onto a canonical operation.

    ``expected_protocol_version`` / ``expected_contract_hash`` carry the
    caller's expected canonical contract identity; mismatch with the
    current canonical contract is detected before execution (never
    silently executed).
    """

    operation: str
    arguments: dict[str, Any] = field(default_factory=dict)
    trusted: dict[str, Any] = field(default_factory=dict)
    expected_protocol_version: str | None = None
    expected_contract_hash: str | None = None
    correlation_id: str | None = None
    trusted_context: TrustedContext | None = field(default=None, repr=False, compare=False)

    @property
    def params(self) -> dict[str, Any]:
        return {**self.arguments, **self.trusted}


def operation_request_schema(descriptor: OperationContractDescriptor) -> dict[str, Any]:
    """Project one canonical descriptor into the adapter request schema."""
    return {
        "operation": descriptor.name,
        "description": descriptor.description,
        "arguments": [
            {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
            for spec in descriptor.inputs
        ],
        "trusted_metadata": sorted(TRUSTED_ADAPTER_KEYS),
        "protocol_version": descriptor.protocol_version,
        "contract_hash": descriptor.contract_hash(),
    }


def build_request(
    operation: str,
    arguments: dict[str, Any] | None = None,
    *,
    trusted: dict[str, Any] | None = None,
    expected_protocol_version: str | None = None,
    expected_contract_hash: str | None = None,
    correlation_id: str | None = None,
    trusted_context: TrustedContext | None = None,
) -> AdapterRequest:
    """Build a bounded adapter request with pure transport syntax checks.

    Semantic validation (unknown inputs, declared types, size bounds) is
    deliberately NOT duplicated here — it happens at the canonical
    ingress.
    """
    if not isinstance(operation, str) or not operation.strip():
        raise AdapterRequestInvalidError("request operation must be a non-empty string")
    arguments = dict(arguments or {})
    trusted = dict(trusted or {})
    if len(arguments) > MAX_ARGUMENT_KEYS:
        raise AdapterRequestInvalidError(f"too many argument keys: {len(arguments)}")
    if len(trusted) > MAX_TRUSTED_KEYS:
        raise AdapterRequestInvalidError(f"too many trusted keys: {len(trusted)}")
    for key in arguments:
        if not isinstance(key, str) or not key:
            raise AdapterRequestInvalidError("argument keys must be non-empty strings")
        if key in TRUSTED_ADAPTER_KEYS:
            raise AdapterRequestInvalidError(
                f"trusted metadata '{key}' must be provided through the trusted channel"
            )
    for key in trusted:
        if key not in TRUSTED_ADAPTER_KEYS:
            raise AdapterRequestInvalidError(f"untrusted metadata key: {key}")
    if correlation_id is not None and not isinstance(correlation_id, str):
        raise AdapterRequestInvalidError("correlation_id must be a string or null")
    if expected_protocol_version is not None and not isinstance(expected_protocol_version, str):
        raise AdapterRequestInvalidError("expected_protocol_version must be a string or null")
    if expected_contract_hash is not None and not isinstance(expected_contract_hash, str):
        raise AdapterRequestInvalidError("expected_contract_hash must be a string or null")
    if trusted_context is not None and (
        not isinstance(trusted_context, TrustedContext) or not trusted_context.is_bound
    ):
        raise AdapterRequestInvalidError("trusted_context must come from the trusted runtime boundary")
    return AdapterRequest(
        operation=operation,
        arguments=arguments,
        trusted=trusted,
        expected_protocol_version=expected_protocol_version,
        expected_contract_hash=expected_contract_hash,
        correlation_id=correlation_id,
        trusted_context=trusted_context,
    )
