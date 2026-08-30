"""Tool Provider contract — S4/M1/W3.

Executor-neutral seam:

    OperationContractDescriptor + validated inputs
        → ToolProvider.invoke  →  ToolResponse

Reuses canonical operation authority, validation, and error semantics
without creating new registries, YAML, or universal envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError, InputSizeError, InputTypeError
from aota_forge.core.contracts.validation import validate_inputs

_MAX_STRING_LEN = 4096


def _require_optional_bounded_str(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputTypeError(f"{name} must be a string when supplied")
    if len(value) > _MAX_STRING_LEN:
        raise InputSizeError(f"{name} exceeds bounded length")
    if value != "" and not value.strip():
        raise InputTypeError(f"{name} must not be blank when supplied")
    return value


@dataclass(frozen=True)
class ToolRequest:
    """Immutable tool invocation request.

    Carries the canonical operation descriptor (authority, read/write,
    mutation scope live on the descriptor) plus validated inputs and
    optional correlation.  No executor lifecycle identities are present.
    """

    operation: OperationContractDescriptor
    inputs: dict[str, Any]
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationContractDescriptor):
            raise InputTypeError("operation must be an OperationContractDescriptor")
        # validate that the operation descriptor itself is well-formed
        self.operation.validate()
        if not isinstance(self.inputs, dict):
            raise InputTypeError("inputs must be a dict")
        # reuse canonical validation seam
        validated = validate_inputs(self.operation, self.inputs)
        # store canonicalized validated dict (immutability via object.__setattr__)
        object.__setattr__(self, "inputs", dict(validated))
        _require_optional_bounded_str("correlation_id", self.correlation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.name,
            "inputs": dict(self.inputs),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class ToolResponse:
    """Local interface carrier for tool invocation outcome.

    Composes generic concepts: ok, payload, typed error with
    retryability preserved through ForgeError projection.  Local carrier
    only, not a universal envelope.
    """

    ok: bool
    payload: dict[str, Any] | None
    error: dict[str, Any] | None

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:  # noqa: E721
            raise TypeError("ok must be a bool")
        if self.ok:
            if self.error is not None:
                raise ValueError("ok=True must have error=None")
            if self.payload is not None and not isinstance(self.payload, dict):
                raise TypeError("payload must be a dict when ok")
        else:
            if self.error is None or not isinstance(self.error, dict):
                raise ValueError("ok=False requires a structured error dict")
            if "code" not in self.error or "message" not in self.error:
                raise ValueError("error dict must contain code and message")

    @classmethod
    def success(cls, payload: dict[str, Any] | None = None) -> "ToolResponse":
        return cls(ok=True, payload=dict(payload) if payload is not None else {}, error=None)

    @classmethod
    def failure(cls, error: ForgeError | dict[str, Any]) -> "ToolResponse":
        if isinstance(error, ForgeError):
            err_dict = error.to_dict()
        elif isinstance(error, dict):
            if "code" not in error or "message" not in error:
                raise ValueError("error dict must contain code and message")
            err_dict = dict(error)
        else:
            raise TypeError("error must be ForgeError or dict")
        return cls(ok=False, payload=None, error=err_dict)


@runtime_checkable
class ToolProvider(Protocol):
    """Distinct tool provider seam; invocation transport remains private.

    Not a HandlerRegistry and not a single tool call string.  The provider
    maps canonical operation semantics to its private invocation mechanism.
    """

    def invoke(self, request: ToolRequest) -> ToolResponse:
        ...
