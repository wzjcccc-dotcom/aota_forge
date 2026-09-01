"""Stop / Escalation / Bounded Retry Contract (S1 M3-W2).

Defines Work Plane semantic contract distinguishing mechanical failure
from semantic stop, with bounded escalation / retry-request semantics.
Does NOT execute retry, does NOT mutate Journal, does NOT own error ontology.

Invariants
----------
* MECHANICAL_FAILURE != AUTOMATIC_RETRY_PERMISSION
* ForgeError.retryable != authorization to retry
* SemanticStop requires task-main reconciliation, never grants retry
* RETRYABLE_NO_EFFECT requires fresh authorization + preconditions + lease
* UNKNOWN outcome never grants blind retry
* WORKER_SELF_REPLAN_AUTHORITY=no, UNBOUNDED_SELF_RETRY=no
* Contracts are immutable, bounded, deterministically serialized,
  fail-closed on unknown fields, evidence by ref not dump
* Separate domains: MechanicalFailure vs SemanticStop
* No JournalState mutation, no CanonicalResult rewrite, no new error ontology
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize

# ---------------------------------------------------------------------------
# Bounded capacity
# ---------------------------------------------------------------------------
MAX_TASK_REF_LENGTH: int = 512
MAX_RESULT_REF_LENGTH: int = 512
MAX_RATIONALE_LENGTH: int = 1024
MAX_EVIDENCE_REFS: int = 16
MAX_EVIDENCE_REF_LENGTH: int = 512
MAX_REQUESTED_BY_LENGTH: int = 128

# ---------------------------------------------------------------------------
# Semantic stop taxonomy — exactly 7 bounded reasons per S1 Plan
# ---------------------------------------------------------------------------

@unique
class SemanticStopReason(str, Enum):
    SCOPE_AMBIGUOUS = "SCOPE_AMBIGUOUS"
    REQUIREMENT_AMBIGUOUS = "REQUIREMENT_AMBIGUOUS"
    AUTHORITY_CONFLICT = "AUTHORITY_CONFLICT"
    PROJECT_POLICY_CONFLICT = "PROJECT_POLICY_CONFLICT"
    UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED = "UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED"
    HANDOFF_INSUFFICIENT = "HANDOFF_INSUFFICIENT"
    REPEATED_FAILURE_INDICATING_PLAN_DEFECT = "REPEATED_FAILURE_INDICATING_PLAN_DEFECT"


SEMANTIC_STOP_REASONS: frozenset[str] = frozenset(r.value for r in SemanticStopReason)
SEMANTIC_STOP_REASON_SET: frozenset[str] = SEMANTIC_STOP_REASONS

# For test / contract visibility
SEMANTIC_STOP_REASON_COUNT: int = len(SemanticStopReason)  # 7
SEMANTIC_STOP_REASON_SET_BOUNDED: bool = True
UNKNOWN_SEMANTIC_STOP_REASON_FAIL_CLOSED: bool = True

# Semantic stop meaning invariants
SEMANTIC_STOP_IS_RETRY_PERMISSION: bool = False
SEMANTIC_STOP_IS_PLAN_AUTHORITY: bool = False
SEMANTIC_STOP_ESCALATES_TO_TASK_MAIN: bool = True

# Mechanical boundary invariants
RETRYABLE_IS_RETRY_AUTHORITY: bool = False
MECHANICAL_FAILURE_IS_RETRY_PERMISSION: bool = False
FORGE_ERROR_RETAIN: bool = True
NEW_MECHANICAL_ERROR_ONTOLOGY: bool = False

# Unknown outcome invariants
UNKNOWN_OUTCOME_AUTO_RETRY: bool = False
UNKNOWN_BLIND_RETRY: bool = False
UNKNOWN_IS_AUTO_RETRY_PERMISSION: bool = False

# Worker authority invariants
WORKER_SELF_REPLAN_AUTHORITY: bool = False
UNBOUNDED_SELF_RETRY: bool = False

# Retry request invariant
RETRY_REQUEST_IS_AUTHORIZATION: bool = False

# Journal safety reuse (mirrors core/journal/retry.py, not duplicate)
RETRYABLE_NO_EFFECT_FRESH_AUTHORIZATION_REQUIRED: bool = True
JOURNAL_RETRY_SAFETY_RETAIN: bool = True

# Domain separation
MECHANICAL_FAILURE_DISTINCT: bool = True
SEMANTIC_STOP_DISTINCT: bool = True

# ---------------------------------------------------------------------------
# Helpers for semantic stop reason parsing (fail-closed)
# ---------------------------------------------------------------------------

def is_valid_semantic_stop_reason(value: object) -> bool:
    if isinstance(value, SemanticStopReason):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in SEMANTIC_STOP_REASONS
    return False


def parse_semantic_stop_reason(value: object) -> SemanticStopReason:
    if isinstance(value, SemanticStopReason):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return SemanticStopReason(value)
        except ValueError:
            raise ValueError(
                f"Unknown semantic stop reason: {value!r}. Must be one of {sorted(SEMANTIC_STOP_REASONS)}"
            )
    raise TypeError(f"semantic stop reason must be a string or SemanticStopReason, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# Stop kind discriminator (keeps domains distinct)
# ---------------------------------------------------------------------------

@unique
class StopKind(str, Enum):
    SEMANTIC_STOP = "SEMANTIC_STOP"
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE"


# ---------------------------------------------------------------------------
# Bounded validation helpers
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not allow_empty and not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_len:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_len}")
    return stripped


def _validate_evidence_refs(refs: object) -> tuple[str, ...]:
    if refs is None:
        return ()
    if not isinstance(refs, (tuple, list)):
        raise TypeError(f"evidence_refs must be tuple/list, got {type(refs).__name__}")
    if len(refs) > MAX_EVIDENCE_REFS:
        raise ValueError(f"evidence_refs count ({len(refs)}) exceeds maximum {MAX_EVIDENCE_REFS}")
    out: list[str] = []
    for idx, r in enumerate(refs):
        if not isinstance(r, str) or type(r) is not str:
            raise TypeError(f"evidence_refs[{idx}] must be string, got {type(r).__name__}")
        s = r.strip()
        if not s:
            raise ValueError(f"evidence_refs[{idx}] must be non-empty")
        if len(s) > MAX_EVIDENCE_REF_LENGTH:
            raise ValueError(f"evidence_refs[{idx}] length ({len(s)}) exceeds maximum {MAX_EVIDENCE_REF_LENGTH}")
        out.append(s)
    return tuple(out)


_ALLOWED_SEMANTIC_STOP_FIELDS: frozenset[str] = frozenset({
    "reason", "task_ref", "result_ref", "rationale", "evidence_refs",
})
_ALLOWED_MECHANICAL_FAILURE_FIELDS: frozenset[str] = frozenset({
    "task_ref", "result_ref", "error_code", "retryable", "evidence_refs", "rationale",
})
_ALLOWED_RETRY_REQUEST_FIELDS: frozenset[str] = frozenset({
    "task_ref", "classification", "rationale", "prior_result_ref", "requested_by", "evidence_refs",
})
_ALLOWED_ESCALATION_FIELDS: frozenset[str] = frozenset({
    "kind", "task_ref", "result_ref", "classification", "evidence_refs",
    "rationale", "retry_requires_fresh_authority", "escalation_target",
})


# ---------------------------------------------------------------------------
# SemanticStop — immutable bounded semantic stop
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticStop:
    """Worker cannot safely continue without task-main semantic reconciliation.

    Does NOT mean task failed mechanically, retry allowed, or Plan changed.
    Always requires escalation to task-main.
    """

    reason: SemanticStopReason
    task_ref: str
    result_ref: str | None = None
    rationale: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # reason — bounded enum, fail-closed
        if isinstance(self.reason, SemanticStopReason):
            pass
        elif isinstance(self.reason, str) and type(self.reason) is str:
            object.__setattr__(self, "reason", parse_semantic_stop_reason(self.reason))
        elif isinstance(self.reason, Enum):
            raise TypeError(f"reason must be SemanticStopReason or string, got foreign Enum {type(self.reason).__name__}")
        else:
            raise TypeError(f"reason must be SemanticStopReason or string, got {type(self.reason).__name__}")

        # task_ref — bounded
        object.__setattr__(self, "task_ref", _validate_bounded_str(self.task_ref, "task_ref", MAX_TASK_REF_LENGTH))

        # result_ref — optional bounded
        if self.result_ref is not None:
            object.__setattr__(self, "result_ref", _validate_bounded_str(self.result_ref, "result_ref", MAX_RESULT_REF_LENGTH))

        # rationale — optional bounded
        if self.rationale is not None:
            object.__setattr__(self, "rationale", _validate_bounded_str(self.rationale, "rationale", MAX_RATIONALE_LENGTH))

        # evidence_refs — bounded collection of refs (not dumps)
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs))

    # Semantic meaning helpers (always escalate, never retry)
    @property
    def requires_escalation(self) -> bool:
        return True

    @property
    def grants_retry(self) -> bool:
        return False

    @property
    def is_plan_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "reason": self.reason.value,
            "task_ref": self.task_ref,
            "evidence_refs": sorted(self.evidence_refs),
        }
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.rationale is not None:
            d["rationale"] = self.rationale
        return canonicalize(d, path="SemanticStop")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "task_ref": self.task_ref,
            "result_ref": self.result_ref,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticStop":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_SEMANTIC_STOP_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in SemanticStop: {sorted(extra)}")
        if "reason" not in data or "task_ref" not in data:
            raise ValueError("Missing required field in SemanticStop: 'reason' and 'task_ref' required")
        return cls(
            reason=data["reason"],
            task_ref=data["task_ref"],
            result_ref=data.get("result_ref"),
            rationale=data.get("rationale"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )


# ---------------------------------------------------------------------------
# MechanicalFailure — projection/classification of execution observation
# Reuses existing ForgeError / CanonicalResult evidence, owns no new ontology
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MechanicalFailure:
    """Projection of execution/runtime/tool observation as mechanical failure.

    Reuses existing evidence (ForgeError code, retryable flag, CanonicalResult
    state) without owning original error authority. Does NOT grant retry.
    """

    task_ref: str
    error_code: str
    retryable: bool
    result_ref: str | None = None
    rationale: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ref", _validate_bounded_str(self.task_ref, "task_ref", MAX_TASK_REF_LENGTH))
        object.__setattr__(self, "error_code", _validate_bounded_str(self.error_code, "error_code", 128))
        if type(self.retryable) is not bool:
            raise TypeError(f"retryable must be bool, got {type(self.retryable).__name__}")
        if self.result_ref is not None:
            object.__setattr__(self, "result_ref", _validate_bounded_str(self.result_ref, "result_ref", MAX_RESULT_REF_LENGTH))
        if self.rationale is not None:
            object.__setattr__(self, "rationale", _validate_bounded_str(self.rationale, "rationale", MAX_RATIONALE_LENGTH))
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs))

    @property
    def grants_retry(self) -> bool:
        return False

    @property
    def requires_escalation(self) -> bool:
        # Mechanical failure may be retried only with fresh authority,
        # but does not automatically escalate as semantic stop.
        # For bounded contract we treat retryable awareness, not auto-retry.
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "error_code": self.error_code,
            "evidence_refs": sorted(self.evidence_refs),
            "retryable": self.retryable,
            "task_ref": self.task_ref,
        }
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.rationale is not None:
            d["rationale"] = self.rationale
        return canonicalize(d, path="MechanicalFailure")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "result_ref": self.result_ref,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MechanicalFailure":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_MECHANICAL_FAILURE_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in MechanicalFailure: {sorted(extra)}")
        for req in ("task_ref", "error_code", "retryable"):
            if req not in data:
                raise ValueError(f"Missing required field in MechanicalFailure: {req!r}")
        return cls(
            task_ref=data["task_ref"],
            result_ref=data.get("result_ref"),
            error_code=data["error_code"],
            retryable=bool(data["retryable"]) if not isinstance(data["retryable"], bool) else data["retryable"],
            rationale=data.get("rationale"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )

    @classmethod
    def from_forge_error(cls, error: Any, *, task_ref: str, result_ref: str | None = None, rationale: str | None = None, evidence_refs: tuple[str, ...] = ()) -> "MechanicalFailure":
        """Project existing ForgeError evidence into mechanical failure without cloning ontology."""
        # Reuse ForgeError.code and retryable; do not create new error taxonomy.
        code = getattr(error, "code", None)
        retryable = getattr(error, "retryable", None)
        if not isinstance(code, str):
            code = str(code) if code is not None else "FORGE_ERROR"
        if not isinstance(retryable, bool):
            retryable = bool(retryable)
        return cls(
            task_ref=task_ref,
            error_code=code,
            retryable=retryable,
            result_ref=result_ref,
            rationale=rationale,
            evidence_refs=evidence_refs,
        )

    @classmethod
    def from_canonical_result(cls, result: Any, *, task_ref: str | None = None, rationale: str | None = None, evidence_refs: tuple[str, ...] = ()) -> "MechanicalFailure":
        """Project CanonicalResult evidence into mechanical failure."""
        # Use result.error code if present, otherwise status-derived.
        ref = task_ref or getattr(result, "canonical_task_id", "unknown-task")
        error = getattr(result, "error", None)
        if isinstance(error, Mapping) and "code" in error:
            code = str(error["code"])
            retryable = bool(error.get("retryable", False))
        else:
            code = "EXECUTION_FAILED"
            retryable = False
        result_ref = getattr(result, "correlation_id", None)
        return cls(
            task_ref=ref,
            error_code=code,
            retryable=retryable,
            result_ref=result_ref,
            rationale=rationale,
            evidence_refs=evidence_refs,
        )


# ---------------------------------------------------------------------------
# RetryRequest — bounded semantic request for consideration, NOT authorization
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryRequest:
    """Task-main requests consideration/new attempt — not Journal authorization."""

    task_ref: str
    classification: str  # bounded reason string or SemanticStopReason value
    rationale: str
    prior_result_ref: str | None = None
    requested_by: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ref", _validate_bounded_str(self.task_ref, "task_ref", MAX_TASK_REF_LENGTH))
        object.__setattr__(self, "classification", _validate_bounded_str(self.classification, "classification", 128))
        object.__setattr__(self, "rationale", _validate_bounded_str(self.rationale, "rationale", MAX_RATIONALE_LENGTH))
        if self.prior_result_ref is not None:
            object.__setattr__(self, "prior_result_ref", _validate_bounded_str(self.prior_result_ref, "prior_result_ref", MAX_RESULT_REF_LENGTH))
        if self.requested_by is not None:
            object.__setattr__(self, "requested_by", _validate_bounded_str(self.requested_by, "requested_by", MAX_REQUESTED_BY_LENGTH))
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs))

    @property
    def is_authorization(self) -> bool:
        return False

    @property
    def grants_retry(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "classification": self.classification,
            "evidence_refs": sorted(self.evidence_refs),
            "rationale": self.rationale,
            "task_ref": self.task_ref,
        }
        if self.prior_result_ref is not None:
            d["prior_result_ref"] = self.prior_result_ref
        if self.requested_by is not None:
            d["requested_by"] = self.requested_by
        return canonicalize(d, path="RetryRequest")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "classification": self.classification,
            "rationale": self.rationale,
            "prior_result_ref": self.prior_result_ref,
            "requested_by": self.requested_by,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RetryRequest":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_RETRY_REQUEST_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in RetryRequest: {sorted(extra)}")
        for req in ("task_ref", "classification", "rationale"):
            if req not in data:
                raise ValueError(f"Missing required field in RetryRequest: {req!r}")
        return cls(
            task_ref=data["task_ref"],
            classification=data["classification"],
            rationale=data["rationale"],
            prior_result_ref=data.get("prior_result_ref"),
            requested_by=data.get("requested_by"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )


# ---------------------------------------------------------------------------
# Escalation — bounded semantic escalation projection to task-main
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Escalation:
    """Bounded semantic escalation projection sufficient to tell task-main
    what stopped, why, refs, classification, mechanical vs semantic,
    and whether retry may require fresh authority.
    """

    kind: StopKind
    task_ref: str
    classification: str  # SemanticStopReason value or error_code
    result_ref: str | None = None
    rationale: str | None = None
    evidence_refs: tuple[str, ...] = ()
    retry_requires_fresh_authority: bool = True
    escalation_target: str = "task-main"

    def __post_init__(self) -> None:
        if isinstance(self.kind, StopKind):
            pass
        elif isinstance(self.kind, str) and type(self.kind) is str:
            try:
                object.__setattr__(self, "kind", StopKind(self.kind))
            except ValueError:
                raise ValueError(f"Unknown escalation kind: {self.kind!r}")
        elif isinstance(self.kind, Enum):
            raise TypeError(f"kind must be StopKind or string, got foreign Enum {type(self.kind).__name__}")
        else:
            raise TypeError(f"kind must be StopKind or string, got {type(self.kind).__name__}")

        object.__setattr__(self, "task_ref", _validate_bounded_str(self.task_ref, "task_ref", MAX_TASK_REF_LENGTH))
        object.__setattr__(self, "classification", _validate_bounded_str(self.classification, "classification", 128))
        if self.result_ref is not None:
            object.__setattr__(self, "result_ref", _validate_bounded_str(self.result_ref, "result_ref", MAX_RESULT_REF_LENGTH))
        if self.rationale is not None:
            object.__setattr__(self, "rationale", _validate_bounded_str(self.rationale, "rationale", MAX_RATIONALE_LENGTH))
        object.__setattr__(self, "evidence_refs", _validate_evidence_refs(self.evidence_refs))
        if type(self.retry_requires_fresh_authority) is not bool:
            raise TypeError(f"retry_requires_fresh_authority must be bool, got {type(self.retry_requires_fresh_authority).__name__}")
        object.__setattr__(self, "escalation_target", _validate_bounded_str(self.escalation_target, "escalation_target", 64))

    @classmethod
    def from_semantic_stop(cls, stop: SemanticStop) -> "Escalation":
        return cls(
            kind=StopKind.SEMANTIC_STOP,
            task_ref=stop.task_ref,
            result_ref=stop.result_ref,
            classification=stop.reason.value,
            rationale=stop.rationale,
            evidence_refs=stop.evidence_refs,
            retry_requires_fresh_authority=True,
            escalation_target="task-main",
        )

    @classmethod
    def from_mechanical_failure(cls, failure: MechanicalFailure) -> "Escalation":
        # Mechanical retry still requires fresh authority per Journal contract
        return cls(
            kind=StopKind.MECHANICAL_FAILURE,
            task_ref=failure.task_ref,
            result_ref=failure.result_ref,
            classification=failure.error_code,
            rationale=failure.rationale,
            evidence_refs=failure.evidence_refs,
            retry_requires_fresh_authority=True,
            escalation_target="task-main",
        )

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "classification": self.classification,
            "escalation_target": self.escalation_target,
            "evidence_refs": sorted(self.evidence_refs),
            "kind": self.kind.value,
            "retry_requires_fresh_authority": self.retry_requires_fresh_authority,
            "task_ref": self.task_ref,
        }
        if self.result_ref is not None:
            d["result_ref"] = self.result_ref
        if self.rationale is not None:
            d["rationale"] = self.rationale
        return canonicalize(d, path="Escalation")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "task_ref": self.task_ref,
            "result_ref": self.result_ref,
            "classification": self.classification,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "retry_requires_fresh_authority": self.retry_requires_fresh_authority,
            "escalation_target": self.escalation_target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Escalation":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_ESCALATION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in Escalation: {sorted(extra)}")
        for req in ("kind", "task_ref", "classification"):
            if req not in data:
                raise ValueError(f"Missing required field in Escalation: {req!r}")
        return cls(
            kind=data["kind"],
            task_ref=data["task_ref"],
            result_ref=data.get("result_ref"),
            classification=data["classification"],
            rationale=data.get("rationale"),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            retry_requires_fresh_authority=bool(data.get("retry_requires_fresh_authority", True)),
            escalation_target=data.get("escalation_target", "task-main"),
        )


# ---------------------------------------------------------------------------
# Decision helpers / boundaries (conceptual, no Journal mutation)
# ---------------------------------------------------------------------------

def requires_retry_authorization(*args: Any, **kwargs: Any) -> bool:
    """Conceptual decision helper: retry always requires separate authorization.

    Does NOT grant retry, does NOT call Journal mutation, does NOT issue lease.
    """
    return True


def retry_request_is_authorization(request: RetryRequest) -> bool:
    return False


def semantic_stop_requires_escalation(stop: SemanticStop) -> bool:
    return True


def mechanical_failure_requires_fresh_authority(failure: MechanicalFailure) -> bool:
    return True


def grants_retry_authority(*, retryable: bool) -> bool:
    """Adversarial boundary: ForgeError.retryable never grants retry authority."""
    return False


def is_retry_authorized(*, retryable: bool) -> bool:
    """Whether a mechanical retryable flag constitutes authorization — always False."""
    return False


# Matrix helpers (pure, no loops)

def decide_for_semantic_stop(stop: SemanticStop) -> dict[str, Any]:
    return {
        "action": "escalate",
        "to": "task-main",
        "grant_retry": False,
        "requires_fresh_authority": True,
    }


def decide_for_mechanical_failure(failure: MechanicalFailure) -> dict[str, Any]:
    # Both retryable True and False -> no automatic retry
    return {
        "action": "no_automatic_retry",
        "retryable": failure.retryable,
        "grant_retry": False,
        "requires_fresh_authority": True,
    }


def decide_for_unknown_outcome() -> dict[str, Any]:
    return {
        "action": "reconcile_or_escalate",
        "grant_retry": False,
        "blind_retry": False,
    }


def classify_authority_conflict(task_ref: str, *, rationale: str | None = None) -> SemanticStop:
    return SemanticStop(reason=SemanticStopReason.AUTHORITY_CONFLICT, task_ref=task_ref, rationale=rationale)


def classify_policy_conflict(task_ref: str, *, rationale: str | None = None) -> SemanticStop:
    return SemanticStop(reason=SemanticStopReason.PROJECT_POLICY_CONFLICT, task_ref=task_ref, rationale=rationale)


def classify_unexpected_architecture(task_ref: str, *, rationale: str | None = None) -> SemanticStop:
    return SemanticStop(reason=SemanticStopReason.UNEXPECTED_ARCHITECTURE_CHANGE_REQUIRED, task_ref=task_ref, rationale=rationale)


def classify_handoff_insufficient(task_ref: str, *, rationale: str | None = None) -> SemanticStop:
    return SemanticStop(reason=SemanticStopReason.HANDOFF_INSUFFICIENT, task_ref=task_ref, rationale=rationale)


__all__ = [
    "SemanticStopReason",
    "SEMANTIC_STOP_REASONS",
    "SEMANTIC_STOP_REASON_SET",
    "SEMANTIC_STOP_REASON_COUNT",
    "SEMANTIC_STOP_REASON_SET_BOUNDED",
    "UNKNOWN_SEMANTIC_STOP_REASON_FAIL_CLOSED",
    "SEMANTIC_STOP_IS_RETRY_PERMISSION",
    "SEMANTIC_STOP_IS_PLAN_AUTHORITY",
    "SEMANTIC_STOP_ESCALATES_TO_TASK_MAIN",
    "RETRYABLE_IS_RETRY_AUTHORITY",
    "MECHANICAL_FAILURE_IS_RETRY_PERMISSION",
    "FORGE_ERROR_RETAIN",
    "NEW_MECHANICAL_ERROR_ONTOLOGY",
    "UNKNOWN_OUTCOME_AUTO_RETRY",
    "UNKNOWN_BLIND_RETRY",
    "UNKNOWN_IS_AUTO_RETRY_PERMISSION",
    "WORKER_SELF_REPLAN_AUTHORITY",
    "UNBOUNDED_SELF_RETRY",
    "RETRY_REQUEST_IS_AUTHORIZATION",
    "RETRYABLE_NO_EFFECT_FRESH_AUTHORIZATION_REQUIRED",
    "JOURNAL_RETRY_SAFETY_RETAIN",
    "MECHANICAL_FAILURE_DISTINCT",
    "SEMANTIC_STOP_DISTINCT",
    "is_valid_semantic_stop_reason",
    "parse_semantic_stop_reason",
    "StopKind",
    "SemanticStop",
    "MechanicalFailure",
    "RetryRequest",
    "Escalation",
    "requires_retry_authorization",
    "retry_request_is_authorization",
    "semantic_stop_requires_escalation",
    "mechanical_failure_requires_fresh_authority",
    "grants_retry_authority",
    "is_retry_authorized",
    "decide_for_semantic_stop",
    "decide_for_mechanical_failure",
    "decide_for_unknown_outcome",
    "classify_authority_conflict",
    "classify_policy_conflict",
    "classify_unexpected_architecture",
    "classify_handoff_insufficient",
    "MAX_TASK_REF_LENGTH",
    "MAX_RESULT_REF_LENGTH",
    "MAX_RATIONALE_LENGTH",
    "MAX_EVIDENCE_REFS",
]
