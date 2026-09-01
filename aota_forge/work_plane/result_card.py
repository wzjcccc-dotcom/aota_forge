"""Worker Result CARD projection (S1 M3-W1).

Bounded immutable compact projection for task-main reconciliation.

Flow:
    CanonicalResult
    + ResultGovernanceProjection
    -> bounded Worker Result CARD
    -> task-main (hydrates richer details via RESULT_HANDOFF_REF)

Invariants
----------
* CANONICAL_RESULT_RETAIN=yes — CanonicalResult schema unchanged.
* RESULT_GOVERNANCE_RETAIN=yes — ResultGovernanceProjection schema unchanged.
* WORKER_RESULT_CARD_IS_RESULT_AUTHORITY=no — CARD is not authority.
* WORKER_RESULT_CARD_IS_COMPACT_PROJECTION=yes
* THIRD_RESULT_ONTOLOGY_CREATED=no
* CARD outcome is governance-backed (from ResultGovernanceProjection.outcome).
* No duplicate error/completeness/verification/side-effect ontology.
* No STOP_CLASSIFICATION taxonomy (M3-W2 lane).
* No retry logic, no telemetry, no Hermes dependency.
* Bounded summary, refs, next_hint, counts with fail-closed rejects.
* Deterministic canonical serialization and SHA-256 card digest.
* CanonicalResult + governance_projection must agree (outcome semantics) or fail closed.

Public function:
    project_worker_result_card(canonical_result, governance_projection, work_role, ...)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.result_governance import (
    GovernedReference,
    GovernedReferenceKind,
    ResultGovernanceProjection,
    ResultOutcome,
)
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Bounded capacity constraints
# ---------------------------------------------------------------------------

MAX_SUMMARY_LENGTH: int = 1024
MAX_NEXT_HINT_LENGTH: int = 512
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_EVIDENCE_REFS: int = 16
MAX_ARTIFACT_REFS: int = 16
MAX_FINDING_COUNT: int = 10000

# ---------------------------------------------------------------------------
# Result Handoff Reference — traceability/hydration reference, not authority
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultHandoffRef:
    """Bounded hydration reference linking CARD back to authoritative result.

    Fields
    ------
    ref: stable task/result identity (canonical_task_id)
    digest: optional integrity digest (e.g., correlation_id)

    Invariants:
    * RESULT_HANDOFF_REF_IS_AUTHORITY=no
    * bounded lengths, non-empty ref, optional digest
    """

    ref: str
    digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or type(self.ref) is not str:
            raise TypeError(f"ref must be a str, got {type(self.ref).__name__}")
        stripped = self.ref.strip()
        if not stripped:
            raise ValueError("ref must be a non-empty string")
        if len(stripped) > MAX_REF_LENGTH:
            raise ValueError(f"ref length ({len(stripped)}) exceeds maximum {MAX_REF_LENGTH}")
        object.__setattr__(self, "ref", stripped)
        if self.digest is not None:
            if not isinstance(self.digest, str) or type(self.digest) is not str:
                raise TypeError(f"digest must be a str or None, got {type(self.digest).__name__}")
            d = self.digest.strip()
            if not d:
                raise ValueError("digest when provided must be a non-empty string")
            if len(d) > MAX_DIGEST_LENGTH:
                raise ValueError(f"digest length ({len(d)}) exceeds maximum {MAX_DIGEST_LENGTH}")
            object.__setattr__(self, "digest", d)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ref": self.ref}
        if self.digest is not None:
            d["digest"] = self.digest
        return canonicalize(d, path="ResultHandoffRef")  # type: ignore[return-value]

    @classmethod
    def from_value(cls, val: Any) -> "ResultHandoffRef":
        if isinstance(val, cls):
            return val
        if isinstance(val, str) and type(val) is str:
            return cls(ref=val)
        if isinstance(val, Mapping):
            allowed = {"ref", "digest"}
            extra = set(val.keys()) - allowed
            if extra:
                raise ValueError(f"Unknown field(s) in ResultHandoffRef: {sorted(extra)}")
            if "ref" not in val:
                raise ValueError("Missing 'ref' in ResultHandoffRef mapping")
            return cls(ref=val["ref"], digest=val.get("digest"))
        raise TypeError(f"Cannot construct ResultHandoffRef from {type(val).__name__}")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_bounded_summary(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"summary must be a str, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("summary must be a non-empty string")
    if len(stripped) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"summary length ({len(stripped)}) exceeds maximum {MAX_SUMMARY_LENGTH}")
    return stripped


def _require_optional_bounded_next_hint(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"next_hint must be a str or None, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("next_hint when provided must be a non-empty string")
    if len(stripped) > MAX_NEXT_HINT_LENGTH:
        raise ValueError(f"next_hint length ({len(stripped)}) exceeds maximum {MAX_NEXT_HINT_LENGTH}")
    return stripped


def _require_finding_count(name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    if value > MAX_FINDING_COUNT:
        raise ValueError(f"{name} ({value}) exceeds maximum {MAX_FINDING_COUNT}")
    return value


def _validate_evidence_refs(
    refs: object,
) -> tuple[GovernedReference, ...]:
    if refs is None:
        return ()
    if not isinstance(refs, (tuple, list)):
        raise TypeError(f"primary_evidence_refs must be a tuple or list, got {type(refs).__name__}")
    if len(refs) > MAX_EVIDENCE_REFS:
        raise ValueError(f"primary_evidence_refs count ({len(refs)}) exceeds maximum {MAX_EVIDENCE_REFS}")
    result: list[GovernedReference] = []
    for idx, item in enumerate(refs):
        if isinstance(item, GovernedReference):
            ref_obj = item
        elif isinstance(item, Mapping):
            ref_obj = GovernedReference.from_dict(item)  # type: ignore[arg-type]
        else:
            raise TypeError(f"primary_evidence_refs[{idx}] must be GovernedReference or dict, got {type(item).__name__}")
        if ref_obj.kind != GovernedReferenceKind.EVIDENCE:
            raise ValueError(
                f"primary_evidence_refs[{idx}] kind mismatch: expected 'evidence', got {ref_obj.kind.value!r}"
            )
        result.append(ref_obj)
    return tuple(result)


def _validate_artifact_refs(
    refs: object,
) -> tuple[GovernedReference, ...]:
    if refs is None:
        return ()
    if not isinstance(refs, (tuple, list)):
        raise TypeError(f"output_artifact_refs must be a tuple or list, got {type(refs).__name__}")
    if len(refs) > MAX_ARTIFACT_REFS:
        raise ValueError(f"output_artifact_refs count ({len(refs)}) exceeds maximum {MAX_ARTIFACT_REFS}")
    result: list[GovernedReference] = []
    for idx, item in enumerate(refs):
        if isinstance(item, GovernedReference):
            ref_obj = item
        elif isinstance(item, Mapping):
            ref_obj = GovernedReference.from_dict(item)  # type: ignore[arg-type]
        else:
            raise TypeError(f"output_artifact_refs[{idx}] must be GovernedReference or dict, got {type(item).__name__}")
        if ref_obj.kind != GovernedReferenceKind.ARTIFACT:
            raise ValueError(
                f"output_artifact_refs[{idx}] kind mismatch: expected 'artifact', got {ref_obj.kind.value!r}"
            )
        result.append(ref_obj)
    return tuple(result)


def _expected_outcome_from_canonical(canonical_result: CanonicalResult) -> ResultOutcome:
    """Derive expected ResultOutcome from CanonicalResult for agreement check."""
    if canonical_result.ok is True and canonical_result.status == "completed":
        return ResultOutcome.SUCCESS
    if canonical_result.status == "unknown" or canonical_result.canonical_task_state == "UNKNOWN":
        return ResultOutcome.UNKNOWN
    # all other non-success including failed, rejected, cancelled map to failure
    return ResultOutcome.FAILURE


# ---------------------------------------------------------------------------
# Worker Result CARD
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerResultCard:
    """Bounded immutable Worker Result CARD projection.

    CARD is a compact projection, not an authority. Richer details hydrate
    via result_handoff_ref back to canonical/governed result.

    Fields map 1:1 to spec contract:
    * task_ref — traceable to CanonicalResult.canonical_task_id
    * agent_work_role — reused AgentWorkRole (no second enum)
    * summary — bounded human/task-main readable, non-authoritative
    * outcome — governance-backed (derived from ResultGovernanceProjection.outcome)
    * blocking_finding_count / non_blocking_finding_count — integer >=0 bounded
    * result_handoff_ref — bounded traceability ref (ref + optional digest)
    * primary_evidence_refs — bounded GovernedReference evidence refs
    * output_artifact_refs — bounded GovernedReference artifact refs
    * next_hint — optional bounded plain suggestion, non-authoritative
    """

    task_ref: str
    agent_work_role: AgentWorkRole
    summary: str
    outcome: ResultOutcome
    blocking_finding_count: int
    non_blocking_finding_count: int
    result_handoff_ref: ResultHandoffRef
    primary_evidence_refs: tuple[GovernedReference, ...]
    output_artifact_refs: tuple[GovernedReference, ...]
    next_hint: str | None = None

    def __post_init__(self) -> None:
        # task_ref bounded non-empty
        if not isinstance(self.task_ref, str) or type(self.task_ref) is not str:
            raise TypeError(f"task_ref must be a str, got {type(self.task_ref).__name__}")
        stripped_ref = self.task_ref.strip()
        if not stripped_ref:
            raise ValueError("task_ref must be a non-empty string")
        if len(stripped_ref) > MAX_REF_LENGTH:
            raise ValueError(f"task_ref length exceeds maximum {MAX_REF_LENGTH}")
        object.__setattr__(self, "task_ref", stripped_ref)

        # agent_work_role must be AgentWorkRole, reuse M1 contract
        if isinstance(self.agent_work_role, AgentWorkRole):
            pass
        elif isinstance(self.agent_work_role, Enum):
            raise TypeError(
                f"agent_work_role must be AgentWorkRole, got foreign Enum {type(self.agent_work_role).__name__}"
            )
        elif isinstance(self.agent_work_role, str) and type(self.agent_work_role) is str:
            parsed = parse_agent_work_role(self.agent_work_role)
            object.__setattr__(self, "agent_work_role", parsed)
        else:
            raise TypeError(
                f"agent_work_role must be AgentWorkRole or valid string, got {type(self.agent_work_role).__name__}"
            )

        # summary bounded (non-authoritative)
        validated_summary = _require_bounded_summary(self.summary)
        object.__setattr__(self, "summary", validated_summary)

        # outcome must be ResultOutcome, governance-backed
        if not isinstance(self.outcome, ResultOutcome):
            raise TypeError(f"outcome must be ResultOutcome, got {type(self.outcome).__name__}")

        # finding counts bounded >=0
        _require_finding_count("blocking_finding_count", self.blocking_finding_count)
        _require_finding_count("non_blocking_finding_count", self.non_blocking_finding_count)

        # result_handoff_ref bounded traceable
        if not isinstance(self.result_handoff_ref, ResultHandoffRef):
            raise TypeError(
                f"result_handoff_ref must be ResultHandoffRef, got {type(self.result_handoff_ref).__name__}"
            )

        # evidence / artifact refs bounded & kind-checked
        # validate tuple types
        if not isinstance(self.primary_evidence_refs, tuple):
            raise TypeError("primary_evidence_refs must be a tuple")
        if len(self.primary_evidence_refs) > MAX_EVIDENCE_REFS:
            raise ValueError(f"primary_evidence_refs exceeds maximum {MAX_EVIDENCE_REFS}")
        for idx, r in enumerate(self.primary_evidence_refs):
            if not isinstance(r, GovernedReference):
                raise TypeError(f"primary_evidence_refs[{idx}] must be GovernedReference")
            if r.kind != GovernedReferenceKind.EVIDENCE:
                raise ValueError(f"primary_evidence_refs[{idx}] kind must be 'evidence'")

        if not isinstance(self.output_artifact_refs, tuple):
            raise TypeError("output_artifact_refs must be a tuple")
        if len(self.output_artifact_refs) > MAX_ARTIFACT_REFS:
            raise ValueError(f"output_artifact_refs exceeds maximum {MAX_ARTIFACT_REFS}")
        for idx, r in enumerate(self.output_artifact_refs):
            if not isinstance(r, GovernedReference):
                raise TypeError(f"output_artifact_refs[{idx}] must be GovernedReference")
            if r.kind != GovernedReferenceKind.ARTIFACT:
                raise ValueError(f"output_artifact_refs[{idx}] kind must be 'artifact'")

        # next_hint optional bounded non-authoritative
        if self.next_hint is not None:
            validated_hint = _require_optional_bounded_next_hint(self.next_hint)
            object.__setattr__(self, "next_hint", validated_hint)

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical dict for digest/serialization."""
        # sort refs deterministically by (ref, digest or "")
        def _sorted_refs(refs: tuple[GovernedReference, ...]) -> list[dict[str, Any]]:
            sorted_refs = sorted(refs, key=lambda r: (r.ref, r.digest or ""))
            return [r.to_dict() for r in sorted_refs]

        out: dict[str, Any] = {
            "agent_work_role": self.agent_work_role.value,
            "blocking_finding_count": self.blocking_finding_count,
            "non_blocking_finding_count": self.non_blocking_finding_count,
            "outcome": self.outcome.value,
            "output_artifact_refs": _sorted_refs(self.output_artifact_refs),
            "primary_evidence_refs": _sorted_refs(self.primary_evidence_refs),
            "result_handoff_ref": self.result_handoff_ref.to_dict(),
            "summary": self.summary,
            "task_ref": self.task_ref,
        }
        if self.next_hint is not None:
            out["next_hint"] = self.next_hint
        return canonicalize(out, path="WorkerResultCard")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_card_digest(self) -> str:
        encoded = self.canonical_json().encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def card_digest(self) -> str:
        return self.compute_card_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_ref": self.task_ref,
            "agent_work_role": self.agent_work_role.value,
            "summary": self.summary,
            "outcome": self.outcome.value,
            "blocking_finding_count": self.blocking_finding_count,
            "non_blocking_finding_count": self.non_blocking_finding_count,
            "result_handoff_ref": self.result_handoff_ref.to_dict(),
            "primary_evidence_refs": [r.to_dict() for r in self.primary_evidence_refs],
            "output_artifact_refs": [r.to_dict() for r in self.output_artifact_refs],
        }
        if self.next_hint is not None:
            d["next_hint"] = self.next_hint
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerResultCard":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        required = (
            "task_ref",
            "agent_work_role",
            "summary",
            "outcome",
            "blocking_finding_count",
            "non_blocking_finding_count",
            "result_handoff_ref",
            "primary_evidence_refs",
            "output_artifact_refs",
        )
        for req in required:
            if req not in data:
                raise ValueError(f"Missing required field in WorkerResultCard: {req!r}")
        # unknown fields fail closed
        allowed = set(required) | {"next_hint"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in WorkerResultCard: {sorted(extra)}")
        task_ref = data["task_ref"]
        work_role = parse_agent_work_role(data["agent_work_role"])
        summary = data["summary"]
        outcome_raw = data["outcome"]
        if outcome_raw not in ("success", "failure", "unknown"):
            raise ValueError(f"outcome must be one of success/failure/unknown, got {outcome_raw!r}")
        outcome = ResultOutcome(outcome_raw)
        bcount = data["blocking_finding_count"]
        nbcount = data["non_blocking_finding_count"]
        rh_ref = ResultHandoffRef.from_value(data["result_handoff_ref"])
        ev_refs = _validate_evidence_refs(data["primary_evidence_refs"])
        art_refs = _validate_artifact_refs(data["output_artifact_refs"])
        next_hint = data.get("next_hint")
        return cls(
            task_ref=task_ref,
            agent_work_role=work_role,
            summary=summary,
            outcome=outcome,
            blocking_finding_count=bcount,
            non_blocking_finding_count=nbcount,
            result_handoff_ref=rh_ref,
            primary_evidence_refs=ev_refs,
            output_artifact_refs=art_refs,
            next_hint=next_hint,
        )


# ---------------------------------------------------------------------------
# Public projection function
# ---------------------------------------------------------------------------


def project_worker_result_card(
    canonical_result: CanonicalResult,
    governance_projection: ResultGovernanceProjection,
    work_role: AgentWorkRole | str,
    *,
    summary: str,
    blocking_finding_count: int = 0,
    non_blocking_finding_count: int = 0,
    primary_evidence_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
    output_artifact_refs: tuple[GovernedReference, ...] | list[GovernedReference] | None = None,
    next_hint: str | None = None,
    result_handoff_ref: ResultHandoffRef | Mapping[str, Any] | str | None = None,
) -> WorkerResultCard:
    """Project bounded Worker Result CARD from authoritative results.

    Derives outcome from ``governance_projection.outcome`` (governance-backed)
    and task identity from ``canonical_result.canonical_task_id``. Caller cannot
    override governance outcome.

    Fail-closed if canonical_result and governance_projection disagree on
    outcome semantics where comparison is possible.

    Parameters
    ----------
    canonical_result: CanonicalResult
        Frozen canonical execution result envelope (authority).
    governance_projection: ResultGovernanceProjection
        Governed projection (outcome, error, provenance, etc.) — authority for outcome.
    work_role: AgentWorkRole | str
        Worker role for this execution, reused AgentWorkRole (no second ontology).
    summary: str
        Bounded human/task-main readable summary (non-authoritative).
    blocking_finding_count / non_blocking_finding_count: int
        Finding counts >=0 bounded, NOT a result governance outcome.
    primary_evidence_refs / output_artifact_refs: bounded GovernedReference tuples
        Must use correct kinds (evidence / artifact). Bounded lengths.
    next_hint: str | None
        Optional bounded plain suggestion, non-authoritative.
    result_handoff_ref: optional bounded ref linking back to authority
        If None, synthesized as ref=canonical_task_id + digest=correlation_id.

    Returns
    -------
    WorkerResultCard
        Bounded immutable projection with deterministic digest.
    """
    if not isinstance(canonical_result, CanonicalResult):
        raise TypeError(f"canonical_result must be CanonicalResult, got {type(canonical_result).__name__}")
    if not isinstance(governance_projection, ResultGovernanceProjection):
        raise TypeError(
            f"governance_projection must be ResultGovernanceProjection, got {type(governance_projection).__name__}"
        )

    # Work role fail-closed parse (reuse AgentWorkRole, no new role)
    parsed_role = parse_agent_work_role(work_role)

    # Outcome is governance-backed, never caller-supplied
    governance_outcome = governance_projection.outcome
    if not isinstance(governance_outcome, ResultOutcome):
        raise TypeError(f"governance_projection.outcome must be ResultOutcome, got {type(governance_outcome).__name__}")

    # Identity/outcome agreement — fail closed where deterministic comparison possible
    expected = _expected_outcome_from_canonical(canonical_result)
    if expected != governance_outcome:
        raise ValueError(
            f"CanonicalResult/governance outcome mismatch: canonical maps to {expected.value!r}, "
            f"governance has {governance_outcome.value!r} — FAIL_CLOSED"
        )

    # Task identity traceable to canonical_task_id
    task_ref = canonical_result.canonical_task_id

    # Summary bounded
    bounded_summary = _require_bounded_summary(summary)

    # Finding counts
    bcount = _require_finding_count("blocking_finding_count", blocking_finding_count)
    nbcount = _require_finding_count("non_blocking_finding_count", non_blocking_finding_count)

    # Evidence / artifact refs bounded & kind-checked
    ev_refs = _validate_evidence_refs(primary_evidence_refs)
    art_refs = _validate_artifact_refs(output_artifact_refs)

    # Next hint optional bounded non-authoritative
    bounded_hint = _require_optional_bounded_next_hint(next_hint)

    # Result handoff ref bounded traceable — synthesize if not provided
    if result_handoff_ref is None:
        # Use canonical_task_id as ref and correlation_id as optional digest (bounded)
        digest_val = canonical_result.correlation_id.strip() if canonical_result.correlation_id else None
        if digest_val and len(digest_val) > MAX_DIGEST_LENGTH:
            # truncate digest cannot silently — fail closed? Use only bounded digest, reject oversized correlation
            raise ValueError(f"correlation_id digest length exceeds maximum {MAX_DIGEST_LENGTH}")
        handoff_ref = ResultHandoffRef(ref=task_ref, digest=digest_val if digest_val else None)
    else:
        handoff_ref = ResultHandoffRef.from_value(result_handoff_ref)
        # Ensure handoff ref traceable to canonical_task_id lineage
        # Require ref equals canonical_task_id or at least not contradicting lineage?
        # Enforce exact lineage: ref must equal canonical_task_id to be traceable
        if handoff_ref.ref != task_ref:
            raise ValueError(
                f"result_handoff_ref.ref {handoff_ref.ref!r} must equal canonical_task_id {task_ref!r} for lineage traceability"
            )

    return WorkerResultCard(
        task_ref=task_ref,
        agent_work_role=parsed_role,
        summary=bounded_summary,
        outcome=governance_outcome,
        blocking_finding_count=bcount,
        non_blocking_finding_count=nbcount,
        result_handoff_ref=handoff_ref,
        primary_evidence_refs=ev_refs,
        output_artifact_refs=art_refs,
        next_hint=bounded_hint,
    )
