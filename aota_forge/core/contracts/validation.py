"""Canonical shared input validation (M2-B).

One validation path at the canonical ingress / contract boundary; adapters
and handlers never re-implement validation semantics.

Behavior (Plan-frozen policy):

* unknown inputs are rejected (``UNKNOWN_INPUT``)
* required inputs are enforced (``REQUIRED_INPUT_MISSING``)
* declared input types are validated (``INPUT_TYPE_INVALID``)
* explicit deterministic size boundaries are enforced per field and in
  total (``INPUT_SIZE_EXCEEDED``) — never only on the total JSON blob
* undeclared inputs are never silently ignored

Trusted adapter/context metadata is explicitly enumerated and bounded
(``TRUSTED_ADAPTER_KEYS``).  Keys in this set are non-semantic (path /
artifact injection), never model-facing descriptor inputs; keys that are
NOT declared in the descriptor and NOT in this explicit enumeration are
rejected.

Explicit size boundaries (safe canonical conventions, deterministic and
testable):

* string length <= 4096 characters
* int magnitude < 2**63 (bool is not an int)
* list items <= 256, dict keys <= 64
* container nesting depth <= 8
* total params keys <= 128
* total serialized params <= 256 KiB
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from aota_forge.core.contracts.descriptor import OperationContractDescriptor, ensure_unique_input_names
from aota_forge.core.contracts.errors import (
    InputSizeError,
    InputTypeError,
    MissingRequiredInputError,
    UnknownInputError,
)

MAX_STRING_LENGTH = 4096
MAX_INT_BITS = 63
MAX_LIST_ITEMS = 256
MAX_DICT_KEYS = 64
MAX_INPUT_DEPTH = 8
MAX_PARAMS_KEYS = 128
MAX_TOTAL_INPUT_BYTES = 256 * 1024

LIFECYCLE_ERROR_CODES = frozenset(
    {
        "PROJECT_NOT_FOUND",
        "NEEDS_SEMANTIC_CHOICE",
        "PROJECT_BINDING_REQUIRED",
        "PLAN_INIT_INVALID_PREDECESSOR",
        "PLAN_INIT_ALREADY_INITIALIZED",
        "PLAN_INIT_STALE_SUBJECT_REVISION",
        "PLAN_INIT_STALE_AUTHORITY_PRECONDITION",
        "RETIREMENT_NO_CANDIDATE",
        "RETIREMENT_NEEDS_SEMANTIC_CHOICE",
        "RETIREMENT_STALE_SNAPSHOT",
        "RETIREMENT_TARGET_PROTECTED",
        "RETIREMENT_RUNNING_TASK_PROTECTED",
        "RETIREMENT_SUCCESSOR_REQUIRED",
        "RETIREMENT_SUCCESSOR_INVALID",
        "RETIREMENT_SELF_SUCCESSOR",
        "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
    }
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def validate_lifecycle_preconditions(
    preconditions: object,
    *,
    expected_revision: int | None = None,
    require_authority: bool = True,
) -> object:
    """Validate M4 lifecycle preconditions without changing intent identity."""
    from aota_forge.core.contracts.mutation import MutationPreconditions

    if not isinstance(preconditions, MutationPreconditions):
        raise TypeError("lifecycle preconditions must be MutationPreconditions")
    subject_revision = preconditions.subject_expected_revision
    if isinstance(subject_revision, bool) or not isinstance(subject_revision, int) or subject_revision < 1:
        raise ValueError("lifecycle Subject expected revision must be a positive integer")
    if expected_revision is not None and subject_revision != expected_revision:
        raise ValueError("lifecycle Subject expected revision does not match request")
    if require_authority:
        source_revision = preconditions.authority_source_revision
        if isinstance(source_revision, bool) or not isinstance(source_revision, (str, int)):
            raise ValueError("lifecycle authority source revision is required")
        if isinstance(source_revision, str) and (not source_revision or len(source_revision) > 256):
            raise ValueError("lifecycle authority source revision is invalid")
        if not isinstance(preconditions.authority_observed_raw_digest, str) or not _SHA256_HEX.fullmatch(
            preconditions.authority_observed_raw_digest
        ):
            raise ValueError("lifecycle authority raw digest must be a SHA-256 digest")
    candidate_digest = preconditions.candidate_raw_digest
    if candidate_digest is not None and (
        not isinstance(candidate_digest, str) or not _SHA256_HEX.fullmatch(candidate_digest)
    ):
        raise ValueError("lifecycle candidate raw digest must be a SHA-256 digest")
    return preconditions

# Explicitly enumerated trusted adapter/context metadata.  Non-semantic,
# bounded, and separate from model-facing descriptor inputs.  Keys not
# declared by the descriptor and not listed here are rejected.
#
# M2-I trusted-resource reconciliation:
#
#   registry_path / pidfile / receipt
#       adapter-private trusted RESOLVED filesystem paths produced by an
#       adapter through the M2-E TrustedResourceResolver; never model-facing
#       semantic input and never accepted from the model-facing argument
#       surface.
#
#   registry_id / pidfile_id / receipt_id
#       trusted logical host resource references; resolved by the operation
#       handler through the operator trusted adapter configuration
#       (host_resource_config_from_env) and the M2-E TrustedResourceResolver
#       into bounded host readers.
TRUSTED_ADAPTER_KEYS: frozenset[str] = frozenset(
    {
        "registry_path",
        "pidfile",
        "receipt",
        "registry_id",
        "pidfile_id",
        "receipt_id",
    }
)


def _base_type(spec_type: str) -> str:
    return spec_type[:-1] if spec_type.endswith("?") else spec_type


def _is_optional(spec_type: str) -> bool:
    return spec_type.endswith("?")


def _bounded_json_value(value: object, name: str, depth: int) -> None:
    """Bound a generic JSON value (size/depth/type) without type coercion."""
    if depth > MAX_INPUT_DEPTH:
        raise InputSizeError(f"input nesting too deep: {name}")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise InputSizeError(f"input string too large: {name}")
        return
    if isinstance(value, int):
        if not (-(2 ** (MAX_INT_BITS - 1)) <= value < 2 ** (MAX_INT_BITS - 1)):
            raise InputSizeError(f"input int out of bounded range: {name}")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputTypeError(f"input float must be finite: {name}")
        return
    if isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS:
            raise InputSizeError(f"input list too large: {name}")
        for item in value:
            _bounded_json_value(item, name, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_DICT_KEYS:
            raise InputSizeError(f"input dict too large: {name}")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_STRING_LENGTH:
                raise InputSizeError(f"input dict key is invalid: {name}")
            _bounded_json_value(item, name, depth + 1)
        return
    raise InputTypeError(f"input contains unsupported value type: {name}")


def _check_declared(type_text: str, value: object, name: str) -> None:
    """Validate one declared semantic input against its spec type."""
    base = _base_type(type_text)
    optional = _is_optional(type_text)
    if value is None:
        if optional:
            return
        raise InputTypeError(f"input must not be null: {name} (expected {base})")
    if base == "str":
        if not isinstance(value, str):
            raise InputTypeError(f"input must be str: {name}")
        if len(value) > MAX_STRING_LENGTH:
            raise InputSizeError(f"input string too large: {name}")
        return
    if base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InputTypeError(f"input must be int: {name}")
        if not (-(2 ** (MAX_INT_BITS - 1)) <= value < 2 ** (MAX_INT_BITS - 1)):
            raise InputSizeError(f"input int out of bounded range: {name}")
        return
    if base == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputTypeError(f"input must be float: {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise InputTypeError(f"input float must be finite: {name}")
        return
    if base == "bool":
        if not isinstance(value, bool):
            raise InputTypeError(f"input must be bool: {name}")
        return
    if base in ("list", "dict"):
        if (base == "list") != isinstance(value, list):
            raise InputTypeError(f"input must be {base}: {name}")
        _bounded_json_value(value, name, 0)
        return
    raise InputTypeError(f"unsupported declared input type: {type_text}")


def _check_trusted(key: str, value: object) -> None:
    """Bound a trusted adapter/context metadata value (non-semantic)."""
    if not isinstance(value, str):
        raise InputTypeError(f"trusted adapter value must be str: {key}")
    if len(value) > MAX_STRING_LENGTH:
        raise InputSizeError(f"trusted adapter value too large: {key}")


def validate_inputs(
    descriptor: OperationContractDescriptor,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate raw params against one descriptor; return the validated dict.

    Raises the canonical validation errors; never coerces types and never
    silently ignores undeclared inputs.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise InputTypeError("params must be an object")
    if len(params) > MAX_PARAMS_KEYS:
        raise InputSizeError(f"too many input keys: {len(params)}")

    ensure_unique_input_names(descriptor.name, descriptor.inputs)
    spec_by_name = {spec.name: spec for spec in descriptor.inputs}
    validated: dict[str, Any] = {}
    for key, value in params.items():
        spec = spec_by_name.get(key)
        if spec is not None:
            _check_declared(spec.type, value, key)
            validated[key] = value
        elif key in TRUSTED_ADAPTER_KEYS:
            _check_trusted(key, value)
            validated[key] = value
        else:
            raise UnknownInputError(f"unknown input for operation '{descriptor.name}': {key}")

    for spec in descriptor.inputs:
        if not _is_optional(spec.type) and (spec.name not in validated or validated[spec.name] is None):
            raise MissingRequiredInputError(f"required input missing for operation '{descriptor.name}': {spec.name}")

    try:
        total_bytes = len(json.dumps(validated, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise InputSizeError(f"input cannot be serialized: {type(exc).__name__}") from exc
    if total_bytes > MAX_TOTAL_INPUT_BYTES:
        raise InputSizeError(f"total input exceeds {MAX_TOTAL_INPUT_BYTES} bytes")
    return validated
