"""Context Provider contract — S4/M1/W2.

Executor-neutral, store-independent seam:

    ContextRequest  →  ContextProvider.fetch  →  ContextResponse

Reuses existing canonical error and authority semantics without creating
new provider registries, YAML, or universal result envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aota_forge.core.contracts.errors import ForgeError, InputSizeError, InputTypeError

_MAX_STRING_LEN = 4096
_MAX_LIMIT = 100
_MIN_LIMIT = 1


def _require_bounded_str(name: str, value: object, *, allow_none: bool = False, required: bool = True) -> str | None:
    if value is None:
        if allow_none:
            return None
        if required:
            raise InputTypeError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise InputTypeError(f"{name} must be a string")
    if not value.strip():
        raise InputTypeError(f"{name} must be a non-empty string")
    if len(value) > _MAX_STRING_LEN:
        raise InputSizeError(f"{name} exceeds bounded length")
    return value


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
class ContextRequest:
    """Immutable bounded retrieval request.

    Subject and scope identify the retrieval domain, query carries the
    semantic ask, and explicit bounds remain generic:

        limit  — bounded size/count constraint
        cursor — opaque pagination/reference
        capability_ref — optional capability requirement/reference
        correlation_id — optional caller correlation/reference
    """

    subject_ref: str
    scope: str
    query: str
    limit: int | None = None
    cursor: str | None = None
    capability_ref: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        _require_bounded_str("subject_ref", self.subject_ref)
        _require_bounded_str("scope", self.scope)
        _require_bounded_str("query", self.query)
        # bounded retrieval constraint
        if self.limit is not None:
            if isinstance(self.limit, bool) or not isinstance(self.limit, int):
                raise InputTypeError("limit must be an integer when supplied")
            if not (_MIN_LIMIT <= self.limit <= _MAX_LIMIT):
                raise InputSizeError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}")
        _require_optional_bounded_str("cursor", self.cursor)
        _require_optional_bounded_str("capability_ref", self.capability_ref)
        _require_optional_bounded_str("correlation_id", self.correlation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "scope": self.scope,
            "query": self.query,
            "limit": self.limit,
            "cursor": self.cursor,
            "capability_ref": self.capability_ref,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class ContextResponse:
    """Local interface carrier for context fetch outcome.

    Composes existing generic concepts: ok/status, payload, opaque
    reference, typed error with retryability preserved through ForgeError
    dict projection. It is a local carrier, not a universal envelope.
    """

    ok: bool
    payload: tuple[dict[str, Any], ...]
    reference: str | None
    error: dict[str, Any] | None

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:  # noqa: E721
            raise TypeError("ok must be a bool")
        if not isinstance(self.payload, (tuple, list)):
            raise TypeError("payload must be a tuple or list")
        for idx, item in enumerate(self.payload):
            if not isinstance(item, dict):
                raise TypeError(f"payload[{idx}] must be a dict")
        object.__setattr__(self, "payload", tuple(self.payload))
        if self.reference is not None:
            if not isinstance(self.reference, str):
                raise TypeError("reference must be a string when supplied")
            if len(self.reference) > _MAX_STRING_LEN:
                raise InputSizeError("reference exceeds bounded length")
        if self.ok:
            if self.error is not None:
                raise ValueError("ok=True must have error=None")
        else:
            if self.error is None or not isinstance(self.error, dict):
                raise ValueError("ok=False requires a structured error dict")
            if "code" not in self.error or "message" not in self.error:
                raise ValueError("error dict must contain code and message")

    @classmethod
    def success(
        cls,
        payload: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        reference: str | None = None,
    ) -> "ContextResponse":
        return cls(ok=True, payload=tuple(payload), reference=reference, error=None)

    @classmethod
    def failure(cls, error: ForgeError | dict[str, Any]) -> "ContextResponse":
        if isinstance(error, ForgeError):
            err_dict: dict[str, Any] = error.to_dict()
        elif isinstance(error, dict):
            if "code" not in error or "message" not in error:
                raise ValueError("error dict must contain code and message")
            err_dict = dict(error)
        else:
            raise TypeError("error must be ForgeError or dict")
        return cls(ok=False, payload=(), reference=None, error=err_dict)


@runtime_checkable
class ContextProvider(Protocol):
    """Distinct provider seam; intentionally tiny.

    Not an ExecutorAdapter and not a ContextStore.  Implementations keep
    retrieval mechanics private; only the bounded request/response crosses
    the seam.
    """

    def fetch(self, request: ContextRequest) -> ContextResponse:
        ...
