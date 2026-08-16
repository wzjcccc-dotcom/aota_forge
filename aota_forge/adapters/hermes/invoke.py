"""Thin canonical ingress projection for Hermes-style adapter requests (M2-F).

Contains NO business logic: no project selection, no context recovery, no
error taxonomy, no ID choreography, no authority logic, no handler
duplication.  The only adapter-side check is explicit contract drift
detection; everything else is owned by the canonical Unified Ingress.
"""

from __future__ import annotations

from typing import Any

from aota_forge.core.contracts.errors import ContractVersionMismatchError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import failure_from_error
from aota_forge.core.ingress import execute, resolve_correlation_id

from .schema import AdapterRequest


def detect_contract_drift(
    operation: str,
    expected_protocol_version: str | None = None,
    expected_contract_hash: str | None = None,
) -> tuple[bool, str | None]:
    """Compare expected canonical contract identity against the current one.

    Returns (drifted, field) where field names the first mismatched
    contract field; unknown operations yield (False, None) because the
    canonical ingress owns the unsupported-operation outcome.
    """
    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor is None:
        return False, None
    if expected_protocol_version is not None and expected_protocol_version != descriptor.protocol_version:
        return True, "protocol_version"
    if expected_contract_hash is not None and expected_contract_hash != descriptor.contract_hash():
        return True, "contract_hash"
    return False, None


def execute_request(request: AdapterRequest) -> dict[str, Any]:
    """Execute one Hermes-style adapter request through the canonical ingress.

    Adapter/Core contract drift yields a deterministic bounded
    ``CONTRACT_VERSION_MISMATCH`` envelope; a mismatched schema is never
    silently executed.  Otherwise the request is projected onto the same
    ingress used by direct library invocation and the CLI.
    """
    cid = resolve_correlation_id(request.correlation_id)
    drifted, field = detect_contract_drift(
        request.operation,
        request.expected_protocol_version,
        request.expected_contract_hash,
    )
    if drifted:
        err = ContractVersionMismatchError(
            f"adapter/Core contract drift: expected {field} does not match the "
            f"current canonical contract for '{request.operation}'"
        )
        return failure_from_error(
            request.operation,
            err,
            correlation_id=cid,
            next_action=(
                "refresh expected protocol_version/contract_hash from the "
                "canonical OperationContractDescriptor; refusing to execute "
                "mismatched schema"
            ),
        )
    return execute(
        request.operation,
        request.params,
        correlation_id=request.correlation_id,
        trusted_context=request.trusted_context,
    )
