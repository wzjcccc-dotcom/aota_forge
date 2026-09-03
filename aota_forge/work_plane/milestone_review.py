"""Integrated Review & Finding Triage Contract — S4 M3 W1.

Thin deterministic evidence contracts for Milestone integrated review:

    ReviewCycle
    ReviewFindingClassification
    ReviewFindingEvidence
    MilestoneReviewEvidence

Invariants
----------
* PURE_CONTRACT_LAYER=yes, STATELESS=yes, IMMUTABLE=yes, DETERMINISTIC=yes
* BOUNDED=yes, FAIL_CLOSED=yes
* Existing S1/S2/M1/M2 contracts -> S4/M3 review evidence contracts (no reverse import)
* No evaluator / repair / replan / closure semantics in W1
* No Git / subprocess / filesystem / network / random / time
* Authority-negative: review evidence is not result/review/acceptance authority
* Reuses SemanticReference and ResultHandoffRef where semantically valid
* Finding identity is typed/bounded, not count/index/timestamp/summary
* MAX_FINDINGS_PER_REVIEW<=16, deterministic canonicalization, SHA256 digest
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.result_card import ResultHandoffRef

# ---------------------------------------------------------------------------
# Bounded capacities (structural safety limits — NOT policy thresholds)
# ---------------------------------------------------------------------------

MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_FINDING_REF_LENGTH: int = 512
MAX_MILESTONE_REF_LENGTH: int = 512
MAX_FRONTIER_REF_LENGTH: int = 512
MAX_EVIDENCE_REF_LENGTH: int = 512
MAX_FINDINGS_PER_REVIEW: int = 16

# Authority / correctness flags — all W1 contracts are authority-negative
REVIEW_CYCLE_IMPLEMENTED: bool = True
REVIEW_CYCLE_VALUES: tuple[str, ...] = ("RV1", "RV2")
RV3_ALLOWED: bool = False
RV4_ALLOWED: bool = False
GENERIC_RVN_ALLOWED: bool = False

REVIEW_FINDING_CLASSIFICATION_IMPLEMENTED: bool = True
REVIEW_FINDING_VALUES: tuple[str, ...] = ("BLOCKING", "NON_BLOCKING")
FINDING_CLASSIFICATION_IS_AUTHORITY: bool = False
FINDING_COUNT_IS_FINDING_IDENTITY: bool = False

REVIEW_FINDING_EVIDENCE_IMPLEMENTED: bool = True
REVIEW_FINDING_EVIDENCE_IS_AUTHORITY: bool = False
REVIEW_FINDING_EVIDENCE_IS_REPAIR_AUTHORITY: bool = False
REVIEW_FINDING_EVIDENCE_IS_PLAN_AUTHORITY: bool = False

MILESTONE_REVIEW_EVIDENCE_IMPLEMENTED: bool = True
MILESTONE_REVIEW_EVIDENCE_IS_PLAN_AUTHORITY: bool = False
MILESTONE_REVIEW_EVIDENCE_IS_RESULT_AUTHORITY: bool = False
MILESTONE_REVIEW_EVIDENCE_IS_REVIEW_AUTHORITY: bool = False
MILESTONE_REVIEW_EVIDENCE_IS_ACCEPTANCE_AUTHORITY: bool = False

# M3 global flags
M3_REVIEW_EVIDENCE_IS_RESULT_AUTHORITY: bool = False
M3_REVIEW_EVIDENCE_IS_REVIEW_AUTHORITY: bool = False
M3_REVIEW_EVIDENCE_IS_ACCEPTANCE_AUTHORITY: bool = False
M3_REVIEW_EVIDENCE_RUNS_GIT: bool = False
M3_REVIEW_EVIDENCE_IS_PLAN_AUTHORITY: bool = False

REVIEWED_FRONTIER_REF_IS_GIT_AUTHORITY: bool = False

M3_REVIEW_WORKFLOW_EVALUATOR_IMPLEMENTED: bool = False
REPAIR_EVIDENCE_IMPLEMENTED: bool = False
FAILURE_FINGERPRINT_IMPLEMENTED: bool = False
REPAIR_HISTORY_IMPLEMENTED: bool = False
MILESTONE_CLOSURE_READINESS_IMPLEMENTED: bool = False
MILESTONE_CLOSURE_READINESS_IS_ACCEPTANCE_AUTHORITY: bool = False
MILESTONE_CLOSURE_READINESS_IS_GIT_AUTHORITY: bool = False

W1_IS_ACCEPTANCE_AUTHORITY: bool = False
W1_IS_CLOSURE_AUTHORITY: bool = False

# Firewall / boundary flags
S2_AUTHORITY_BYPASS_CREATED: bool = False
WORKER_RESULT_FINDING_COUNT_IS_FINDING_AUTHORITY: bool = False

# No new engines / ontologies
NEW_GIT_LIFECYCLE_CREATED: bool = False
NEW_SCHEDULER_CREATED: bool = False
NEW_WORKFLOW_ENGINE_CREATED: bool = False
NEW_EXECUTION_STATE_MACHINE_CREATED: bool = False
NEW_RESULT_ONTOLOGY_CREATED: bool = False
NEW_AUTHORITY_ONTOLOGY_CREATED: bool = False
NEW_DIGEST_ONTOLOGY_CREATED: bool = False
PERSISTENT_WORKFLOW_STATE_CREATED: bool = False
M3_DOES_NOT_REPLACE_M4: bool = True
M3_CORRECTNESS_REQUIRES_S6_TELEMETRY: bool = False
S6_METRIC_IS_AUTHORITY: bool = False
S6_RECOMMENDATION_IS_AUTHORITY: bool = False
REVIEW_RESULT_BINDING_REPRESENTABLE: bool = True
MILESTONE_BINDING_REPRESENTABLE: bool = True
REVIEWED_FRONTIER_BINDING_REPRESENTABLE: bool = True
EXISTING_REVIEW_RESULT_EVIDENCE_REUSED: bool = True
EXISTING_TASK_HANDOFF_REUSED: bool = True
EXISTING_RESULT_HANDOFF_REF_REUSED: bool = True
EXACT_MILESTONE_REVIEW_BINDING_REPRESENTABLE: bool = True
ZERO_FINDING_REVIEW_REPRESENTABLE: bool = True
MIXED_FINDING_REVIEW_REPRESENTABLE: bool = True
CONDITIONAL_PREDECESSOR_REVISION_GATE_RETAINED: bool = True
CONDITIONAL_PREDECESSOR_REVISION_GATE_TRIGGERED: bool = False

# Boundary reuse
BOUNDED_S1_S2_S3_M1_M2_REVISION_GATE_REQUIRED: bool = False

# ---------------------------------------------------------------------------
# Helpers — bounded string validation (fail-closed)
# ---------------------------------------------------------------------------

def _validate_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace: {value!r}")
    if not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_len}")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _validate_digest(value: object, label: str) -> str:
    s = _validate_bounded_str(value, label, MAX_DIGEST_LENGTH)
    if not s:
        raise ValueError(f"{label} must be non-empty")
    return s


def _validate_finding_ref(value: object, label: str = "finding_ref") -> str:
    s = _validate_bounded_str(value, label, MAX_FINDING_REF_LENGTH)
    # whitespace-safe already checked; NUL already checked; non-empty; bounded
    # also reject if contains only whitespace? already covered
    return s


# ---------------------------------------------------------------------------
# ReviewCycle — RV1/RV2 only, strict parsing, fail-closed
# ---------------------------------------------------------------------------

@unique
class ReviewCycle(str, Enum):
    RV1 = "RV1"
    RV2 = "RV2"


_REVIEW_CYCLE_VALUES: frozenset[str] = frozenset(v.value for v in ReviewCycle)


def parse_review_cycle(value: object) -> ReviewCycle:
    if isinstance(value, ReviewCycle):
        return value
    if isinstance(value, bool):
        raise TypeError(f"ReviewCycle must be RV1/RV2 string or ReviewCycle, got bool")
    if isinstance(value, Enum):
        raise TypeError(f"ReviewCycle must be RV1/RV2 string or ReviewCycle, got foreign Enum {type(value).__name__}")
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"ReviewCycle must be a string or ReviewCycle, got {type(value).__name__}")
    # strict: no whitespace tolerance, exact match, no lowercase
    if value not in _REVIEW_CYCLE_VALUES:
        raise ValueError(f"Unknown ReviewCycle: {value!r}. Must be one of {sorted(_REVIEW_CYCLE_VALUES)}")
    return ReviewCycle(value)


def is_valid_review_cycle(value: object) -> bool:
    if isinstance(value, ReviewCycle):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in _REVIEW_CYCLE_VALUES
    return False


# ---------------------------------------------------------------------------
# ReviewFindingClassification — BLOCKING/NON_BLOCKING only
# ---------------------------------------------------------------------------

@unique
class ReviewFindingClassification(str, Enum):
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"


_CLASSIFICATION_VALUES: frozenset[str] = frozenset(v.value for v in ReviewFindingClassification)


def parse_review_finding_classification(value: object) -> ReviewFindingClassification:
    if isinstance(value, ReviewFindingClassification):
        return value
    if isinstance(value, bool):
        raise TypeError(f"ReviewFindingClassification must be BLOCKING/NON_BLOCKING string or enum, got bool")
    if isinstance(value, Enum):
        raise TypeError(f"ReviewFindingClassification must be BLOCKING/NON_BLOCKING, got foreign Enum {type(value).__name__}")
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"ReviewFindingClassification must be a string or ReviewFindingClassification, got {type(value).__name__}")
    if value not in _CLASSIFICATION_VALUES:
        raise ValueError(f"Unknown ReviewFindingClassification: {value!r}. Must be one of {sorted(_CLASSIFICATION_VALUES)}")
    return ReviewFindingClassification(value)


def is_valid_review_finding_classification(value: object) -> bool:
    if isinstance(value, ReviewFindingClassification):
        return True
    if isinstance(value, str) and type(value) is str:
        return value in _CLASSIFICATION_VALUES
    return False


# ---------------------------------------------------------------------------
# ReviewFindingEvidence — thin immutable bounded finding identity
# ---------------------------------------------------------------------------

_ALLOWED_FINDING_FIELDS: frozenset[str] = frozenset({
    "finding_ref",
    "classification",
    "supporting_evidence_ref",
    "supporting_evidence_digest",
})


@dataclass(frozen=True)
class ReviewFindingEvidence:
    """Thin immutable bounded finding identity.

    Not authority, not repair authority. Represents an identified review finding
    with bounded identity and supporting evidence binding.
    """

    finding_ref: str
    classification: ReviewFindingClassification
    supporting_evidence_ref: SemanticReference
    supporting_evidence_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_ref", _validate_finding_ref(self.finding_ref, "finding_ref"))
        cls = parse_review_finding_classification(self.classification)
        object.__setattr__(self, "classification", cls)
        if not isinstance(self.supporting_evidence_ref, SemanticReference):
            raise TypeError(f"supporting_evidence_ref must be SemanticReference, got {type(self.supporting_evidence_ref).__name__}")
        object.__setattr__(self, "supporting_evidence_digest", _validate_digest(self.supporting_evidence_digest, "supporting_evidence_digest"))

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_repair_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "classification": self.classification.value,
            "finding_ref": self.finding_ref,
            "supporting_evidence_digest": self.supporting_evidence_digest,
            "supporting_evidence_ref": self.supporting_evidence_ref.to_dict(),
        }, path="ReviewFindingEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_ref": self.finding_ref,
            "classification": self.classification.value,
            "supporting_evidence_ref": self.supporting_evidence_ref.to_dict(),
            "supporting_evidence_digest": self.supporting_evidence_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewFindingEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_FINDING_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ReviewFindingEvidence: {sorted(extra)}")
        for req in ("finding_ref", "classification", "supporting_evidence_ref", "supporting_evidence_digest"):
            if req not in data:
                raise ValueError(f"Missing required field in ReviewFindingEvidence: {req!r}")
        # reject bool for classification
        if isinstance(data["classification"], bool):
            raise TypeError("classification must not be bool")
        # reject bool for finding_ref?
        if isinstance(data["finding_ref"], bool):
            raise TypeError("finding_ref must not be bool")
        classification = parse_review_finding_classification(data["classification"])
        raw_ref = data["supporting_evidence_ref"]
        if isinstance(raw_ref, SemanticReference):
            ref = raw_ref
        elif isinstance(raw_ref, Mapping):
            ref = SemanticReference.from_value(raw_ref)
        elif isinstance(raw_ref, str) and type(raw_ref) is str:
            ref = SemanticReference(ref=raw_ref)
        else:
            raise TypeError(f"supporting_evidence_ref must be SemanticReference/mapping/str, got {type(raw_ref).__name__}")
        # finding_ref validation will happen in __post_init__; also check for whitespace/NUL via _validate
        # but from_dict should also reject foreign dict pretending to be typed evidence? That's handled via extra fields and type checks
        return cls(
            finding_ref=data["finding_ref"],
            classification=classification,
            supporting_evidence_ref=ref,
            supporting_evidence_digest=data["supporting_evidence_digest"],
        )


# ---------------------------------------------------------------------------
# MilestoneReviewEvidence — thin immutable bounded evidence contract
# ---------------------------------------------------------------------------

_ALLOWED_REVIEW_FIELDS: frozenset[str] = frozenset({
    "milestone_ref",
    "review_cycle",
    "reviewed_frontier_ref",
    "review_result_ref",
    "review_result_digest",
    "finding_refs",
})


@dataclass(frozen=True)
class MilestoneReviewEvidence:
    """Thin immutable bounded evidence contract for Milestone integrated review.

    Binds exact Milestone, review cycle (RV1/RV2), reviewed frontier identity,
    governed reviewer result (ResultHandoffRef + digest), and bounded finding identities.

    Evidence-only, not plan/result/acceptance authority, not Git authority.
    """

    milestone_ref: SemanticReference
    review_cycle: ReviewCycle
    reviewed_frontier_ref: SemanticReference
    review_result_ref: ResultHandoffRef
    review_result_digest: str
    finding_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.milestone_ref, SemanticReference):
            raise TypeError(f"milestone_ref must be SemanticReference, got {type(self.milestone_ref).__name__}")
        # milestone_ref already validated via SemanticReference but ensure no additional whitespace issues
        rc = parse_review_cycle(self.review_cycle)
        object.__setattr__(self, "review_cycle", rc)
        if not isinstance(self.reviewed_frontier_ref, SemanticReference):
            raise TypeError(f"reviewed_frontier_ref must be SemanticReference, got {type(self.reviewed_frontier_ref).__name__}")
        if not isinstance(self.review_result_ref, ResultHandoffRef):
            raise TypeError(f"review_result_ref must be ResultHandoffRef, got {type(self.review_result_ref).__name__}")
        object.__setattr__(self, "review_result_digest", _validate_digest(self.review_result_digest, "review_result_digest"))
        # finding_refs bounded, typed, duplicate fail-closed, canonical ordering
        if not isinstance(self.finding_refs, (tuple, list)):
            raise TypeError(f"finding_refs must be tuple/list, got {type(self.finding_refs).__name__}")
        if len(self.finding_refs) > MAX_FINDINGS_PER_REVIEW:
            raise ValueError(f"finding_refs count ({len(self.finding_refs)}) exceeds maximum {MAX_FINDINGS_PER_REVIEW}")
        out: list[str] = []
        seen: set[str] = set()
        for idx, r in enumerate(self.finding_refs):
            s = _validate_finding_ref(r, f"finding_refs[{idx}]")
            if s in seen:
                raise ValueError(f"finding_refs[{idx}] duplicate finding_ref: {s!r}")
            seen.add(s)
            out.append(s)
        # canonical deterministic ordering: sorted
        canonical = tuple(sorted(out))
        object.__setattr__(self, "finding_refs", canonical)

    @property
    def is_plan_authority(self) -> bool:
        return False

    @property
    def is_result_authority(self) -> bool:
        return False

    @property
    def is_acceptance_authority(self) -> bool:
        return False

    @property
    def is_review_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "finding_refs": sorted(self.finding_refs),
            "milestone_ref": self.milestone_ref.to_dict(),
            "review_cycle": self.review_cycle.value,
            "review_result_digest": self.review_result_digest,
            "review_result_ref": self.review_result_ref.to_dict(),
            "reviewed_frontier_ref": self.reviewed_frontier_ref.to_dict(),
        }, path="MilestoneReviewEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_ref": self.milestone_ref.to_dict(),
            "review_cycle": self.review_cycle.value,
            "reviewed_frontier_ref": self.reviewed_frontier_ref.to_dict(),
            "review_result_ref": self.review_result_ref.to_dict(),
            "review_result_digest": self.review_result_digest,
            "finding_refs": list(self.finding_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneReviewEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_REVIEW_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in MilestoneReviewEvidence: {sorted(extra)}")
        for req in ("milestone_ref", "review_cycle", "reviewed_frontier_ref", "review_result_ref", "review_result_digest"):
            if req not in data:
                raise ValueError(f"Missing required field in MilestoneReviewEvidence: {req!r}")
        # finding_refs optional, default empty
        if isinstance(data.get("review_cycle"), bool):
            raise TypeError("review_cycle must not be bool")
        rc = parse_review_cycle(data["review_cycle"])
        # milestone_ref
        raw_milestone = data["milestone_ref"]
        if isinstance(raw_milestone, SemanticReference):
            milestone_ref = raw_milestone
        elif isinstance(raw_milestone, Mapping):
            milestone_ref = SemanticReference.from_value(raw_milestone)
        elif isinstance(raw_milestone, str) and type(raw_milestone) is str:
            milestone_ref = SemanticReference(ref=raw_milestone)
        else:
            raise TypeError(f"milestone_ref must be SemanticReference/mapping/str, got {type(raw_milestone).__name__}")
        # frontier
        raw_frontier = data["reviewed_frontier_ref"]
        if isinstance(raw_frontier, SemanticReference):
            frontier_ref = raw_frontier
        elif isinstance(raw_frontier, Mapping):
            frontier_ref = SemanticReference.from_value(raw_frontier)
        elif isinstance(raw_frontier, str) and type(raw_frontier) is str:
            frontier_ref = SemanticReference(ref=raw_frontier)
        else:
            raise TypeError(f"reviewed_frontier_ref must be SemanticReference/mapping/str, got {type(raw_frontier).__name__}")
        # result ref
        raw_result = data["review_result_ref"]
        result_ref = ResultHandoffRef.from_value(raw_result)
        # finding_refs
        raw_findings = data.get("finding_refs", ())
        if raw_findings is None:
            raw_findings = ()
        if not isinstance(raw_findings, (tuple, list)):
            raise TypeError(f"finding_refs must be tuple/list, got {type(raw_findings).__name__}")
        # Validate each finding_ref for foreign dict pretending to be typed evidence: if someone passes dict with finding_ref key
        for idx, item in enumerate(raw_findings):
            if isinstance(item, Mapping):
                raise TypeError(f"finding_refs[{idx}] must be string, got mapping (foreign dict pretending to be typed evidence)")
            if isinstance(item, Enum):
                raise TypeError(f"finding_refs[{idx}] must be string, got foreign Enum")
            if isinstance(item, bool):
                raise TypeError(f"finding_refs[{idx}] must be string, got bool")
        # digest validation will happen in __post_init__
        return cls(
            milestone_ref=milestone_ref,
            review_cycle=rc,
            reviewed_frontier_ref=frontier_ref,
            review_result_ref=result_ref,
            review_result_digest=data["review_result_digest"],
            finding_refs=tuple(raw_findings),
        )


# ---------------------------------------------------------------------------
# Cross-representability helper (structural only, no evaluation)
# ---------------------------------------------------------------------------

def collect_finding_refs(findings: tuple[ReviewFindingEvidence, ...] | list[ReviewFindingEvidence]) -> tuple[str, ...]:
    """Collect canonical sorted finding_refs from a collection of ReviewFindingEvidence.

    Pure, deterministic, bounded. Does not evaluate coverage or mapping.
    """
    if not isinstance(findings, (tuple, list)):
        raise TypeError(f"findings must be tuple/list, got {type(findings).__name__}")
    if len(findings) > MAX_FINDINGS_PER_REVIEW:
        raise ValueError(f"findings count ({len(findings)}) exceeds maximum {MAX_FINDINGS_PER_REVIEW}")
    refs: list[str] = []
    seen: set[str] = set()
    for idx, f in enumerate(findings):
        if not isinstance(f, ReviewFindingEvidence):
            raise TypeError(f"findings[{idx}] must be ReviewFindingEvidence, got {type(f).__name__}")
        if f.finding_ref in seen:
            raise ValueError(f"findings[{idx}] duplicate finding_ref: {f.finding_ref!r}")
        seen.add(f.finding_ref)
        refs.append(f.finding_ref)
    return tuple(sorted(refs))


def milestone_review_finding_refs_match(
    review_evidence: MilestoneReviewEvidence,
    finding_evidences: tuple[ReviewFindingEvidence, ...] | list[ReviewFindingEvidence],
) -> bool:
    """Whether review evidence finding_refs exactly matches identities from finding evidences."""
    if not isinstance(review_evidence, MilestoneReviewEvidence):
        raise TypeError(f"review_evidence must be MilestoneReviewEvidence, got {type(review_evidence).__name__}")
    expected = collect_finding_refs(finding_evidences)
    return review_evidence.finding_refs == expected


__all__ = [
    "ReviewCycle",
    "parse_review_cycle",
    "is_valid_review_cycle",
    "ReviewFindingClassification",
    "parse_review_finding_classification",
    "is_valid_review_finding_classification",
    "ReviewFindingEvidence",
    "MilestoneReviewEvidence",
    "collect_finding_refs",
    "milestone_review_finding_refs_match",
    "MAX_FINDINGS_PER_REVIEW",
    "MAX_REF_LENGTH",
    "MAX_DIGEST_LENGTH",
    "REVIEW_CYCLE_IMPLEMENTED",
    "REVIEW_CYCLE_VALUES",
    "RV3_ALLOWED",
    "RV4_ALLOWED",
    "GENERIC_RVN_ALLOWED",
    "REVIEW_FINDING_CLASSIFICATION_IMPLEMENTED",
    "REVIEW_FINDING_VALUES",
    "FINDING_CLASSIFICATION_IS_AUTHORITY",
    "FINDING_COUNT_IS_FINDING_IDENTITY",
    "REVIEW_FINDING_EVIDENCE_IMPLEMENTED",
    "REVIEW_FINDING_EVIDENCE_IS_AUTHORITY",
    "REVIEW_FINDING_EVIDENCE_IS_REPAIR_AUTHORITY",
    "MILESTONE_REVIEW_EVIDENCE_IMPLEMENTED",
    "MILESTONE_REVIEW_EVIDENCE_IS_PLAN_AUTHORITY",
    "MILESTONE_REVIEW_EVIDENCE_IS_RESULT_AUTHORITY",
    "M3_REVIEW_EVIDENCE_IS_RESULT_AUTHORITY",
    "M3_REVIEW_EVIDENCE_IS_REVIEW_AUTHORITY",
    "M3_REVIEW_EVIDENCE_IS_ACCEPTANCE_AUTHORITY",
    "M3_REVIEW_EVIDENCE_RUNS_GIT",
    "REVIEWED_FRONTIER_REF_IS_GIT_AUTHORITY",
    "M3_REVIEW_WORKFLOW_EVALUATOR_IMPLEMENTED",
    "REPAIR_EVIDENCE_IMPLEMENTED",
    "FAILURE_FINGERPRINT_IMPLEMENTED",
    "REPAIR_HISTORY_IMPLEMENTED",
    "W1_IS_ACCEPTANCE_AUTHORITY",
    "W1_IS_CLOSURE_AUTHORITY",
    "S2_AUTHORITY_BYPASS_CREATED",
    "WORKER_RESULT_FINDING_COUNT_IS_FINDING_AUTHORITY",
    "NEW_GIT_LIFECYCLE_CREATED",
    "NEW_SCHEDULER_CREATED",
    "NEW_WORKFLOW_ENGINE_CREATED",
    "NEW_EXECUTION_STATE_MACHINE_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_AUTHORITY_ONTOLOGY_CREATED",
    "NEW_DIGEST_ONTOLOGY_CREATED",
    "PERSISTENT_WORKFLOW_STATE_CREATED",
    "M3_DOES_NOT_REPLACE_M4",
    "M3_CORRECTNESS_REQUIRES_S6_TELEMETRY",
    "S6_METRIC_IS_AUTHORITY",
]
