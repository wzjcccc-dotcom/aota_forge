"""S5/M1/W3 — Common Result Governance Core.

Minimal execution-neutral, transport-neutral, domain-neutral projection.

Covers:
  - governance version identity (1.0)
  - outcome (success / failure / unknown)
  - existing typed error projection (ForgeError)
  - provenance (opaque logical refs)
  - completeness (optional)
  - governed references (artifact / evidence) — W3
  - verification projection — W3
  - side-effect outcome projection — W3

Does not cover artifact/evidence store, evidence graph, verification engine,
authority model, or domain extensions.
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
    {
        "governance_version",
        "outcome",
        "error",
        "provenance",
        "completeness",
        "artifact_refs",
        "evidence_refs",
        "verification",
        "side_effect_outcome",
    }
)

_ALLOWED_REFERENCE_KINDS: frozenset[str] = frozenset({"artifact", "evidence"})
_ALLOWED_GOVERNED_REF_KEYS: frozenset[str] = frozenset({"kind", "ref", "digest"})
_ALLOWED_VERIFICATION_VALUES: frozenset[str] = frozenset(
    {"not_applicable", "unverified", "verified", "failed", "unknown"}
)
_ALLOWED_SIDE_EFFECT_VALUES: frozenset[str] = frozenset(
    {"none", "success", "failure", "partial", "unknown"}
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


class GovernedReferenceKind(str, Enum):
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"


class VerificationStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SideEffectOutcome(str, Enum):
    NONE = "none"
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


def _parse_governed_reference_kind(value: object) -> GovernedReferenceKind:
    if not isinstance(value, str):
        raise TypeError("kind must be a string")
    if value not in _ALLOWED_REFERENCE_KINDS:
        raise ValueError(f"kind must be one of {sorted(_ALLOWED_REFERENCE_KINDS)}, got {value!r}")
    return GovernedReferenceKind(value)


def _parse_verification(value: object) -> VerificationStatus:
    if not isinstance(value, str):
        raise TypeError("verification must be a string")
    if value not in _ALLOWED_VERIFICATION_VALUES:
        raise ValueError(f"verification must be one of {sorted(_ALLOWED_VERIFICATION_VALUES)}, got {value!r}")
    return VerificationStatus(value)


def _parse_side_effect(value: object) -> SideEffectOutcome:
    if not isinstance(value, str):
        raise TypeError("side_effect_outcome must be a string")
    if value not in _ALLOWED_SIDE_EFFECT_VALUES:
        raise ValueError(f"side_effect_outcome must be one of {sorted(_ALLOWED_SIDE_EFFECT_VALUES)}, got {value!r}")
    return SideEffectOutcome(value)


@dataclass(frozen=True)
class GovernedReference:
    kind: GovernedReferenceKind
    ref: str
    digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GovernedReferenceKind):
            raise TypeError(f"kind must be GovernedReferenceKind, got {type(self.kind).__name__}")
        _require_bounded_str("ref", self.ref)
        if self.digest is not None:
            _require_optional_bounded_str("digest", self.digest)
            # digest already validated non-empty when supplied; enforce strict
            if self.digest is not None and not self.digest.strip():
                raise ValueError("digest must be a non-empty string when supplied")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind.value,
            "ref": self.ref,
        }
        if self.digest is not None:
            out["digest"] = self.digest
        return canonicalize(out, path="GovernedReference")  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GovernedReference":
        if not isinstance(data, Mapping):
            raise TypeError("GovernedReference must be a mapping")
        for k in data:
            if k not in _ALLOWED_GOVERNED_REF_KEYS:
                raise ValueError(f"GovernedReference contains unknown field {k!r}")
        if "kind" not in data:
            raise ValueError("Missing required field: kind")
        if "ref" not in data:
            raise ValueError("Missing required field: ref")
        kind = _parse_governed_reference_kind(data["kind"])
        ref = _require_bounded_str("ref", data["ref"])
        digest = data.get("digest")
        if digest is not None:
            digest = _require_optional_bounded_str("digest", digest)
        return cls(kind=kind, ref=ref, digest=digest)


def _validate_reference_tuple(
    refs: object,
    *,
    expected_kind: GovernedReferenceKind,
    field_name: str,
) -> tuple["GovernedReference", ...]:
    if refs is None:
        raise TypeError(f"{field_name} must be a tuple/list when supplied")
    if not isinstance(refs, (tuple, list)):
        raise TypeError(f"{field_name} must be a tuple or list")
    result: list[GovernedReference] = []
    for idx, item in enumerate(refs):
        if isinstance(item, GovernedReference):
            ref_obj = item
        elif isinstance(item, Mapping):
            ref_obj = GovernedReference.from_dict(item)  # type: ignore[arg-type]
        else:
            raise TypeError(f"{field_name}[{idx}] must be GovernedReference or dict")
        if ref_obj.kind != expected_kind:
            raise ValueError(
                f"{field_name}[{idx}] kind mismatch: expected {expected_kind.value!r}, got {ref_obj.kind.value!r}"
            )
        result.append(ref_obj)
    return tuple(result)


def _parse_artifact_refs(value: object) -> tuple[GovernedReference, ...]:
    if value is None:
        return ()
    return _validate_reference_tuple(value, expected_kind=GovernedReferenceKind.ARTIFACT, field_name="artifact_refs")


def _parse_evidence_refs(value: object) -> tuple[GovernedReference, ...]:
    if value is None:
        return ()
    return _validate_reference_tuple(value, expected_kind=GovernedReferenceKind.EVIDENCE, field_name="evidence_refs")


@dataclass(frozen=True)
class ResultGovernanceProjection:
    governance_version: str
    outcome: ResultOutcome
    error: dict[str, Any] | None = None
    provenance: ResultProvenance | None = None
    completeness: ResultCompleteness | None = None
    artifact_refs: tuple[GovernedReference, ...] = ()
    evidence_refs: tuple[GovernedReference, ...] = ()
    verification: VerificationStatus | None = None
    side_effect_outcome: SideEffectOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.governance_version, str) or self.governance_version not in _ALLOWED_VERSIONS:
            raise ValueError(f"governance_version must be one of {sorted(_ALLOWED_VERSIONS)}, got {self.governance_version!r}")
        if not isinstance(self.outcome, ResultOutcome):
            raise TypeError(f"outcome must be a ResultOutcome, got {type(self.outcome).__name__}")
        if self.provenance is not None and not isinstance(self.provenance, ResultProvenance):
            raise TypeError("provenance must be ResultProvenance when supplied")
        if self.completeness is not None and not isinstance(self.completeness, ResultCompleteness):
            raise TypeError("completeness must be ResultCompleteness when supplied")
        if self.error is not None:
            _validate_error_projection(self.error, path="error")
        if self.outcome == ResultOutcome.SUCCESS and self.error is not None:
            raise ValueError("success outcome must have error=None")
        if self.outcome == ResultOutcome.FAILURE and self.error is None:
            raise ValueError("failure outcome requires a structured error projection")
        # artifact_refs / evidence_refs validation — tuple immutable, kind-separated
        if not isinstance(self.artifact_refs, tuple):
            raise TypeError("artifact_refs must be a tuple")
        for idx, r in enumerate(self.artifact_refs):
            if not isinstance(r, GovernedReference):
                raise TypeError(f"artifact_refs[{idx}] must be GovernedReference")
            if r.kind != GovernedReferenceKind.ARTIFACT:
                raise ValueError(f"artifact_refs[{idx}] kind mismatch: expected 'artifact', got {r.kind.value!r}")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        for idx, r in enumerate(self.evidence_refs):
            if not isinstance(r, GovernedReference):
                raise TypeError(f"evidence_refs[{idx}] must be GovernedReference")
            if r.kind != GovernedReferenceKind.EVIDENCE:
                raise ValueError(f"evidence_refs[{idx}] kind mismatch: expected 'evidence', got {r.kind.value!r}")
        if self.verification is not None and not isinstance(self.verification, VerificationStatus):
            raise TypeError(f"verification must be VerificationStatus, got {type(self.verification).__name__}")
        if self.side_effect_outcome is not None and not isinstance(self.side_effect_outcome, SideEffectOutcome):
            raise TypeError(f"side_effect_outcome must be SideEffectOutcome, got {type(self.side_effect_outcome).__name__}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "governance_version": self.governance_version,
            "outcome": self.outcome.value,
        }
        if self.error is not None:
            out["error"] = canonicalize(self.error, path="error")
        if self.provenance is not None:
            prov_dict = self.provenance.to_dict()
            if prov_dict:
                out["provenance"] = prov_dict
        if self.completeness is not None:
            comp_dict = self.completeness.to_dict()
            if comp_dict or self.completeness.complete is not None:
                out["completeness"] = comp_dict
            else:
                if any(v is not None for v in (self.completeness.complete, self.completeness.reason, self.completeness.scope)):
                    out["completeness"] = comp_dict
        if self.artifact_refs:
            out["artifact_refs"] = [r.to_dict() for r in self.artifact_refs]
        if self.evidence_refs:
            out["evidence_refs"] = [r.to_dict() for r in self.evidence_refs]
        if self.verification is not None:
            out["verification"] = self.verification.value
        if self.side_effect_outcome is not None:
            out["side_effect_outcome"] = self.side_effect_outcome.value
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
        artifact_refs: tuple[GovernedReference, ...] = ()
        if "artifact_refs" in data and data["artifact_refs"] is not None:
            raw = data["artifact_refs"]
            if not isinstance(raw, (list, tuple)):
                raise TypeError("artifact_refs must be a list or tuple")
            artifact_refs = _parse_artifact_refs(raw)
        evidence_refs: tuple[GovernedReference, ...] = ()
        if "evidence_refs" in data and data["evidence_refs"] is not None:
            raw = data["evidence_refs"]
            if not isinstance(raw, (list, tuple)):
                raise TypeError("evidence_refs must be a list or tuple")
            evidence_refs = _parse_evidence_refs(raw)
        verification: VerificationStatus | None = None
        if "verification" in data and data["verification"] is not None:
            verification = _parse_verification(data["verification"])
        side_effect_outcome: SideEffectOutcome | None = None
        if "side_effect_outcome" in data and data["side_effect_outcome"] is not None:
            side_effect_outcome = _parse_side_effect(data["side_effect_outcome"])
        return cls(
            governance_version=gv,
            outcome=outcome,
            error=error,
            provenance=provenance,
            completeness=completeness,
            artifact_refs=artifact_refs,
            evidence_refs=evidence_refs,
            verification=verification,
            side_effect_outcome=side_effect_outcome,
        )

    @classmethod
    def success(
        cls,
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
        artifact_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        evidence_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        verification: VerificationStatus | None = None,
        side_effect_outcome: SideEffectOutcome | None = None,
    ) -> "ResultGovernanceProjection":
        art = tuple(artifact_refs) if artifact_refs is not None else ()
        evi = tuple(evidence_refs) if evidence_refs is not None else ()
        # validate kinds early
        if art:
            _validate_reference_tuple(art, expected_kind=GovernedReferenceKind.ARTIFACT, field_name="artifact_refs")
        if evi:
            _validate_reference_tuple(evi, expected_kind=GovernedReferenceKind.EVIDENCE, field_name="evidence_refs")
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.SUCCESS,
            error=None,
            provenance=provenance,
            completeness=completeness,
            artifact_refs=art,
            evidence_refs=evi,
            verification=verification,
            side_effect_outcome=side_effect_outcome,
        )

    @classmethod
    def failure(
        cls,
        error: ForgeError | Mapping[str, Any],
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
        artifact_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        evidence_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        verification: VerificationStatus | None = None,
        side_effect_outcome: SideEffectOutcome | None = None,
    ) -> "ResultGovernanceProjection":
        if isinstance(error, ForgeError):
            err_dict: dict[str, Any] = error.to_dict()  # type: ignore[assignment]
        elif isinstance(error, Mapping):
            err_dict = dict(error)  # type: ignore[assignment]
        else:
            raise TypeError("error must be ForgeError or dict")
        validated = _validate_error_projection(err_dict, path="error")
        art = tuple(artifact_refs) if artifact_refs is not None else ()
        evi = tuple(evidence_refs) if evidence_refs is not None else ()
        if art:
            _validate_reference_tuple(art, expected_kind=GovernedReferenceKind.ARTIFACT, field_name="artifact_refs")
        if evi:
            _validate_reference_tuple(evi, expected_kind=GovernedReferenceKind.EVIDENCE, field_name="evidence_refs")
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.FAILURE,
            error=validated,
            provenance=provenance,
            completeness=completeness,
            artifact_refs=art,
            evidence_refs=evi,
            verification=verification,
            side_effect_outcome=side_effect_outcome,
        )

    @classmethod
    def unknown(
        cls,
        *,
        provenance: ResultProvenance | None = None,
        completeness: ResultCompleteness | None = None,
        error: ForgeError | Mapping[str, Any] | None = None,
        artifact_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        evidence_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
        verification: VerificationStatus | None = None,
        side_effect_outcome: SideEffectOutcome | None = None,
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
        art = tuple(artifact_refs) if artifact_refs is not None else ()
        evi = tuple(evidence_refs) if evidence_refs is not None else ()
        if art:
            _validate_reference_tuple(art, expected_kind=GovernedReferenceKind.ARTIFACT, field_name="artifact_refs")
        if evi:
            _validate_reference_tuple(evi, expected_kind=GovernedReferenceKind.EVIDENCE, field_name="evidence_refs")
        return cls(
            governance_version=RESULT_GOVERNANCE_VERSION,
            outcome=ResultOutcome.UNKNOWN,
            error=err_dict,
            provenance=provenance,
            completeness=completeness,
            artifact_refs=art,
            evidence_refs=evi,
            verification=verification,
            side_effect_outcome=side_effect_outcome,
        )
