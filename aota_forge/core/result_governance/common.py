"""S5/M1/W2 — Common Result Governance Core.

Minimal execution-neutral, transport-neutral, domain-neutral projection.

Covers only:
  - governance version identity
  - outcome (success / failure / unknown)
  - existing typed error projection (ForgeError)
  - provenance (opaque logical refs)
  - completeness (optional)

Does not cover artifact/evidence, side-effect result, or domain extensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.errors import ForgeError, error_from_dict

RESULT_GOVERNANCE_VERSION: str = "1.0"

_ALLOWED_VERSIONS: frozenset[str] = frozenset({RESULT_GOVERNANCE_VERSION})

_MAX_STR_LEN: int = 4096

_ALLOWED_OUTCOME_VALUES: frozenset[str] = frozenset({"success", "failure", "unknown"})

_ALLOWED_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {"source_ref", "operation_ref", "content_digest", "observed_at"}
)

_ALLOWED_COMPLETENESS_KEYS: frozenset[str] = frozenset({"complete", "reason", "scope"})

_ALLOWED_PROJECTION_KEYS: frozenset[str] = frozenset(
    {"governance_version", "outcome", "error", "provenance", "completeness"}
)

_ALLOWED_ERROR_KEYS: frozenset[str] = frozenset(
    {"code", "message", "retryable", "details", "original_code"}
)


def _require_bounded_str(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > _MAX_STR_LEN:
        raise ValueError(f"{name} exceeds max length")
    return value


def _require_optional_bounded_str(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string when supplied")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string when supplied")
    if len(value) > _MAX_STR_LEN:
        raise ValueError(f"{name} exceeds max length")
    return value


def _validate_error_projection(value: object, *, path: str = "error") -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a dict when supplied")
    # check unknown keys
    for k in value:
        if k not in _ALLOWED_ERROR_KEYS:
            raise ValueError(f"{path} contains unknown field {k!r}")
    if "code" not in value or not isinstance(value["code"], str) or not value["code"].strip():
        raise ValueError(f"{path} must contain non-empty string code")
    if "message" not in value or not isinstance(value["message"], str) or not value["message"].strip():
        raise ValueError(f"{path} must contain non-empty string message")
    if "retryable" not in value:
        raise ValueError(f"{path} must contain retryable")
    if type(value["retryable"]) is not bool:
        raise TypeError(f"{path}.retryable must be a bool")
    # reuse existing model for round-trip check
    reconstructed = error_from_dict(dict(value))  # type: ignore[arg-type]
    if reconstructed is None:
        raise ValueError(f"{path} is not a valid ForgeError projection")
    # canonicalize ensures json-native
    canonical = canonicalize(dict(value), path=path)
    return canonical  # type: ignore[return-value]


class ResultOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


def _parse_outcome(value: object) -> ResultOutcome:
    if not isinstance(value, str):
        raise TypeError("outcome must be a string")
    if value not in _ALLOWED_OUTCOME_VALUES:
        raise ValueError(f"outcome must be one of {sorted(_ALLOWED_OUTCOME_VALUES)}, got {value!r}")
    return ResultOutcome(value)


@dataclass(frozen=True)
class ResultProvenance:
    source_ref: str | None = None
    operation_ref: str | None = None
    content_digest: str | None = None
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if self.source_ref is not None:
            _require_optional_bounded_str("source_ref", self.source_ref)
        if self.operation_ref is not None:
            _require_optional_bounded_str("operation_ref", self.operation_ref)
        if self.content_digest is not None:
            _require_optional_bounded_str("content_digest", self.content_digest)
        if self.observed_at is not None:
            _require_optional_bounded_str("observed_at", self.observed_at)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.source_ref is not None:
            out["source_ref"] = self.source_ref
        if self.operation_ref is not None:
            out["operation_ref"] = self.operation_ref
        if self.content_digest is not None:
            out["content_digest"] = self.content_digest
        if self.observed_at is not None:
            out["observed_at"] = self.observed_at
        return canonicalize(out, path="provenance")  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ResultProvenance" | None:
        if data is None:
            return None
        if not isinstance(data, Mapping):
            raise TypeError("provenance must be a mapping when supplied")
        for k in data:
            if k not in _ALLOWED_PROVENANCE_KEYS:
                raise ValueError(f"provenance contains unknown field {k!r}")
        return cls(
            source_ref=data.get("source_ref"),
            operation_ref=data.get("operation_ref"),
            content_digest=data.get("content_digest"),
            observed_at=data.get("observed_at"),
        )


@dataclass(frozen=True)
class ResultCompleteness:
    complete: bool | None = None
    reason: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        if self.complete is not None and type(self.complete) is not bool:
            raise TypeError("complete must be a bool when supplied")
        if self.reason is not None:
            _require_optional_bounded_str("reason", self.reason)
        if self.scope is not None:
            _require_optional_bounded_str("scope", self.scope)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.complete is not None:
            out["complete"] = self.complete
        if self.reason is not None:
            out["reason"] = self.reason
        if self.scope is not None:
            out["scope"] = self.scope
        return canonicalize(out, path="completeness")  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ResultCompleteness" | None:
        if data is None:
            return None
        if not isinstance(data, Mapping):
            raise TypeError("completeness must be a mapping when supplied")
        for k in data:
            if k not in _ALLOWED_COMPLETENESS_KEYS:
                raise ValueError(f"completeness contains unknown field {k!r}")
        complete = data.get("complete")
        if complete is not None and type(complete) is not bool:
            raise TypeError("complete must be a bool when supplied")
        return cls(
            complete=complete,
            reason=data.get("reason"),
            scope=data.get("scope"),
        )


@dataclass(frozen=True)
class ResultGovernanceProjection:
    governance_version: str
    outcome: ResultOutcome
    error: dict[str, Any] | None = None
    provenance: ResultProvenance | None = None
    completeness: ResultCompleteness | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.governance_version, str) or self.governance_version not in _ALLOWED_VERSIONS:
            raise ValueError(f"governance_version must be one of {sorted(_ALLOWED_VERSIONS)}, got {self.governance_version!r}")
        if not isinstance(self.outcome, ResultOutcome):
            # allow string coercion check
            raise TypeError(f"outcome must be a ResultOutcome, got {type(self.outcome).__name__}")
        # completeness and provenance type checks
        if self.provenance is not None and not isinstance(self.provenance, ResultProvenance):
            raise TypeError("provenance must be ResultProvenance when supplied")
        if self.completeness is not None and not isinstance(self.completeness, ResultCompleteness):
            raise TypeError("completeness must be ResultCompleteness when supplied")
        # error shape already canonicalized in construction paths, but validate again
        if self.error is not None:
            _validate_error_projection(self.error, path="error")
        # outcome / error consistency (fail-closed)
        if self.outcome == ResultOutcome.SUCCESS and self.error is not None:
            raise ValueError("success outcome must have error=None")
        if self.outcome == ResultOutcome.FAILURE and self.error is None:
            raise ValueError("failure outcome requires a structured error projection")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "governance_version": self.governance_version,
            "outcome": self.outcome.value,
        }
        if self.error is not None:
            out["error"] = canonicalize(self.error, path="error")
        if self.provenance is not None:
            # only include if non-empty? include even if empty? we include if any field present
            prov_dict = self.provenance.to_dict()
            if prov_dict:
                out["provenance"] = prov_dict
        if self.completeness is not None:
            comp_dict = self.completeness.to_dict()
            # include even if empty? but empty completeness means not applicable — we can omit empty
            # to keep projection minimal, omit if no keys
            if comp_dict or self.completeness.complete is not None:
                # if complete is explicitly False, comp_dict will contain it
                out["completeness"] = comp_dict
            else:
                # if completeness object was created but all None, treat as absent
                # only include if any field set
                if any(v is not None for v in (self.completeness.complete, self.completeness.reason, self.completeness.scope)):
                    out["completeness"] = comp_dict
        return canonicalize(out, path="ResultGovernanceProjection")  # type: ignore[return-value]

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResultGovernanceProjection":
        if not isinstance(data, Mapping):
            raise TypeError("data must be a mapping")
        for k in data:
            if k not in _ALLOWED_PROJECTION_KEYS:
                raise ValueError(f"ResultGovernanceProjection contains unknown field {k!r}")
        if "governance_version" not in data:
            raise ValueError("Missing required field: governance_version")
        if "outcome" not in data:
            raise ValueError("Missing required field: outcome")
        gv = data["governance_version"]
        if not isinstance(gv, str) or gv not in _ALLOWED_VERSIONS:
            raise ValueError(f"governance_version must be one of {sorted(_ALLOWED_VERSIONS)}, got {gv!r}")
        outcome = _parse_outcome(data["outcome"])
        error_raw = data.get("error")
        error: dict[str, Any] | None = None
        if error_raw is not None:
            error = _validate_error_projection(error_raw, path="error")
        provenance: ResultProvenance | None = None
        if "provenance" in data and data["provenance"] is not None:
            provenance = ResultProvenance.from_dict(data["provenance"])  # type: ignore[arg-type]
        completeness: ResultCompleteness | None = None
        if "completeness" in data and data["completeness"] is not None:
            completeness = ResultCompleteness.from_dict(data["completeness"])  # type: ignore[arg-type]
        return cls(
            governance_version=gv,
            outcome=outcome,
            error=error,
            provenance=provenance,
            completeness=completeness,
        )

    @classmethod
    def success(
        cls,
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
    ) -> "ResultGovernanceProjection":
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.SUCCESS,
            error=None,
            provenance=provenance,
            completeness=completeness,
        )

    @classmethod
    def failure(
        cls,
        error: ForgeError | Mapping[str, Any],
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
    ) -> "ResultGovernanceProjection":
        if isinstance(error, ForgeError):
            err_dict: dict[str, Any] = error.to_dict()  # type: ignore[assignment]
        elif isinstance(error, Mapping):
            err_dict = dict(error)  # type: ignore[assignment]
        else:
            raise TypeError("error must be ForgeError or dict")
        validated = _validate_error_projection(err_dict, path="error")
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.FAILURE,
            error=validated,
            provenance=provenance,
            completeness=completeness,
        )

    @classmethod
    def unknown(
        cls,
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
        error: ForgeError | Mapping[str, Any] | None = None,
    ) -> "ResultGovernanceProjection":
        err_dict: dict[str, Any] | None = None
        if error is not None:
            if isinstance(error, ForgeError):
                err_dict = error.to_dict()  # type: ignore[assignment]
            elif isinstance(error, Mapping):
                err_dict = dict(error)  # type: ignore[assignment]
            else:
                raise TypeError("error must be ForgeError or dict when supplied")
            err_dict = _validate_error_projection(err_dict, path="error")
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.UNKNOWN,
            error=err_dict,
            provenance=provenance,
            completeness=completeness,
        )
