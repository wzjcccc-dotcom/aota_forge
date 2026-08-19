"""Executor-neutral mutation intent and authoritative effect contracts.

These values describe a request and its classified result.  They do not grant
authority, choose a target, issue leases, write an authority store, or execute
handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.contracts.version import PROTOCOL_VERSION


_TRUSTED_INTENT_KEYS = frozenset(
    {
        "authority",
        "principal",
        "typed_target",
        "subject_ref",
        "subject_expected_revision",
        "authority_source_revision",
        "authority_observed_raw_digest",
        "candidate_raw_digest",
        "contract_hash",
        "authorization_basis",
        "capability_lease",
        "capability_lease_reference",
        "internal_id",
        "internal_ids",
    }
)


def _validate_semantic_inputs(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("semantic_inputs must be a mapping")
    for key in value:
        if key in _TRUSTED_INTENT_KEYS:
            raise ValueError(f"trusted field is not a semantic input: {key}")
    return canonicalize(dict(value), path="semantic_inputs")


@dataclass(frozen=True)
class MutationPreconditions:
    """Mechanical preconditions kept separate from semantic intent identity."""

    subject_expected_revision: str | int | None = None
    authority_source_revision: str | int | None = None
    authority_observed_raw_digest: str | None = None
    candidate_raw_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return canonicalize(
            {
                "subject_expected_revision": self.subject_expected_revision,
                "authority_source_revision": self.authority_source_revision,
                "authority_observed_raw_digest": self.authority_observed_raw_digest,
                "candidate_raw_digest": self.candidate_raw_digest,
            }
        )


@dataclass(frozen=True)
class MutationIntent:
    """An already-decided semantic mutation request, never an authority grant."""

    operation: str
    semantic_inputs: Mapping[str, Any]
    logical_target: Any
    mutation_scope: Any
    idempotency_key: str
    correlation_id: str | None = None
    protocol_version: str = PROTOCOL_VERSION
    _semantic_inputs_canonical: dict[str, Any] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be a non-empty string")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if not isinstance(self.protocol_version, str) or not self.protocol_version:
            raise ValueError("protocol_version must be a non-empty string")
        if self.logical_target is None:
            raise ValueError("logical_target is required")
        if self.mutation_scope is None:
            raise ValueError("mutation_scope is required")
        semantic_inputs = _validate_semantic_inputs(self.semantic_inputs)
        logical_target = canonicalize(self.logical_target, path="logical_target")
        mutation_scope = canonicalize(self.mutation_scope, path="mutation_scope")
        object.__setattr__(self, "semantic_inputs", semantic_inputs)
        object.__setattr__(self, "logical_target", logical_target)
        object.__setattr__(self, "mutation_scope", mutation_scope)
        object.__setattr__(self, "_semantic_inputs_canonical", semantic_inputs)

    def to_semantic_dict(self) -> dict[str, Any]:
        """Return the complete semantic representation, excluding trusted data."""
        return {
            "operation": self.operation,
            "protocol_version": self.protocol_version,
            "semantic_inputs": canonicalize(self._semantic_inputs_canonical),
            "logical_target": canonicalize(self.logical_target),
            "mutation_scope": canonicalize(self.mutation_scope),
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the model-facing representation without trusted resolution data."""
        return self.to_semantic_dict() | {"intent_fingerprint": self.intent_fingerprint()}

    def to_canonical_json(self) -> str:
        return canonical_json(self.to_semantic_dict())

    def intent_fingerprint(self) -> str:
        """Hash semantic identity only; runtime and mechanical values are absent."""
        fingerprint_input = self.to_semantic_dict()
        fingerprint_input.pop("correlation_id", None)
        return hashlib.sha256(canonical_json(fingerprint_input).encode("utf-8")).hexdigest()

    def with_preconditions(self, preconditions: MutationPreconditions) -> tuple["MutationIntent", MutationPreconditions]:
        """Keep trusted mechanical preconditions adjacent without hashing them."""
        if not isinstance(preconditions, MutationPreconditions):
            raise TypeError("preconditions must be MutationPreconditions")
        return self, preconditions


class MutationEffect(str, Enum):
    NO_EFFECT = "NO_EFFECT"
    APPLIED_VERIFIED = "APPLIED_VERIFIED"
    REPLAYED_VERIFIED = "REPLAYED_VERIFIED"
    BLOCKED = "BLOCKED"
    CONFLICT = "CONFLICT"
    NEEDS_SEMANTIC_CHOICE = "NEEDS_SEMANTIC_CHOICE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    FAILED_NO_EFFECT = "FAILED_NO_EFFECT"


class AuthoritativeEffectConfirmation(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


_EFFECT_CONFIRMATION = {
    MutationEffect.NO_EFFECT: AuthoritativeEffectConfirmation.NO,
    MutationEffect.APPLIED_VERIFIED: AuthoritativeEffectConfirmation.YES,
    MutationEffect.REPLAYED_VERIFIED: AuthoritativeEffectConfirmation.YES,
    MutationEffect.BLOCKED: AuthoritativeEffectConfirmation.NO,
    MutationEffect.CONFLICT: AuthoritativeEffectConfirmation.NO,
    MutationEffect.NEEDS_SEMANTIC_CHOICE: AuthoritativeEffectConfirmation.NO,
    MutationEffect.OUTCOME_UNKNOWN: AuthoritativeEffectConfirmation.UNKNOWN,
    MutationEffect.FAILED_NO_EFFECT: AuthoritativeEffectConfirmation.NO,
}


@dataclass(frozen=True)
class MutationResult:
    """Canonical operation result with independently classified mutation effect."""

    operation: str
    status: str
    result: str
    mutation_effect: MutationEffect | str
    authoritative_effect_confirmed: AuthoritativeEffectConfirmation | str
    errors: tuple[Mapping[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    semantic_choices: tuple[Mapping[str, Any], ...] = ()
    next_action: str | None = None
    correlation_id: str | None = None
    effect_evidence: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be a non-empty string")
        effect = MutationEffect(self.mutation_effect)
        confirmed = AuthoritativeEffectConfirmation(self.authoritative_effect_confirmed)
        expected = _EFFECT_CONFIRMATION[effect]
        if confirmed is not expected:
            raise ValueError(f"{effect.value} requires authoritative confirmation {expected.value}")
        object.__setattr__(self, "mutation_effect", effect)
        object.__setattr__(self, "authoritative_effect_confirmed", confirmed)

    @property
    def usable_success(self) -> bool:
        return self.authoritative_effect_confirmed is AuthoritativeEffectConfirmation.YES

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "result": self.result,
            "mutation_effect": self.mutation_effect.value,
            "authoritative_effect_confirmed": self.authoritative_effect_confirmed.value,
            "errors": [canonicalize(item) for item in self.errors],
            "blockers": list(self.blockers),
            "semantic_choices": [canonicalize(item) for item in self.semantic_choices],
            "next_action": self.next_action,
            "correlation_id": self.correlation_id,
            "effect_evidence": [canonicalize(item) for item in self.effect_evidence],
        }


CanonicalMutationResult = MutationResult
