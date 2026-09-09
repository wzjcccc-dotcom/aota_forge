"""Reviewer independent validation runtime (M2/W2 runtime).

M1 frozen (see #44 M1/W3, #43 finding):

    REVIEWER_IS_INDEPENDENT_FROM_CODER=yes
    REVIEWER_MUST_VERIFY_PRODUCT_EFFECT=yes
    REVIEWER_MUST_HAVE_INDEPENDENT_VALIDATION_CAPABILITY=yes
    WORKER_CARD_ALONE_PROVES_PRODUCT_EFFECT=no
    REVIEW_PASS_AUTOMATICALLY_CLOSES_MILESTONE=no
    REVIEWER_IS_FINAL_ACCEPTANCE_AUTHORITY=no

Normal Reviewer tool/runtime capability:

    workspace.search = usable
    workspace.read = usable
    test.run = eager visible + conditionally server-authorized when review
    evidence requires it (review TaskHandoff carries validation
    expectations). No test.run on every review by default; never
    unrestricted shell; never product-source mutation.

Result taxonomy (maps onto existing canonical Review structures, never a
duplicate review subsystem):

    PASS               = acceptance evidence satisfied
    PASS_WITH_FINDINGS = acceptance satisfied with bounded non-blocking findings
    NEEDS_FIX          = acceptance not satisfied
    BLOCKED / INCONCLUSIVE = reliable review verdict unavailable

Result returns to task-main (reviewer is acceptance-evidence producer,
never final acceptance authority).

Hard:

    REVIEWER_INDEPENDENT_VALIDATION_RUNTIME_WIRED=yes
    REVIEWER_CAN_USE_VALIDATION_TOOL=yes
    REVIEWER_TEST_RUN_POLICY=eager_visible+conditionally_authorized
    REVIEWER_TEST_RUN_EVERY_REVIEW_REQUIRED=no
    REVIEWER_CAN_MUTATE_PRODUCT_SOURCE=no
    REVIEWER_WORKSPACE_WRITE_PRODUCT_SOURCE=no
    REVIEW_RESULT_TAXONOMY_WIRED=yes
    REVIEWER_MUST_VERIFY_PRODUCT_EFFECT=yes
    CODER_RESULT_PASS_IS_NOT_REVIEWER_PROOF=yes

Reuses existing contracts only: TaskHandoff, MilestoneReviewEvidence /
ReviewFindingClassification (finding classes), TestExecutionAuthorityEvidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

REVIEWER_INDEPENDENT_VALIDATION_RUNTIME_WIRED = True
REVIEWER_CAN_USE_VALIDATION_TOOL = True
REVIEWER_TEST_RUN_POLICY = "eager_visible+conditionally_authorized"
REVIEWER_TEST_RUN_EVERY_REVIEW_REQUIRED = False
REVIEWER_CAN_MUTATE_PRODUCT_SOURCE = False
REVIEWER_WORKSPACE_WRITE_PRODUCT_SOURCE = False
REVIEWER_UNRESTRICTED_SHELL = False
REVIEW_RESULT_TAXONOMY_WIRED = True
REVIEWER_MUST_VERIFY_PRODUCT_EFFECT = True
CODER_RESULT_PASS_IS_NOT_REVIEWER_PROOF = True
REVIEW_PASS_AUTOMATICALLY_CLOSES_MILESTONE = False
REVIEWER_IS_FINAL_ACCEPTANCE_AUTHORITY = False
REVIEWER_IS_PLAN_AUTHORITY = False


@unique
class ReviewVerdict(str, Enum):
    """Bounded reviewer outcome taxonomy (frozen M1 vocabulary)."""

    PASS = "PASS"
    PASS_WITH_FINDINGS = "PASS_WITH_FINDINGS"
    NEEDS_FIX = "NEEDS_FIX"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


REVIEW_VERDICTS: frozenset[str] = frozenset(v.value for v in ReviewVerdict)

# Clean mapping onto the existing canonical Review finding classes
# (milestone_review.ReviewFindingClassification): PASS and
# PASS_WITH_FINDINGS produce no BLOCKING findings; NEEDS_FIX produces at
# least one BLOCKING finding; BLOCKED/INCONCLUSIVE produce no verdict at
# all (reliable review unavailable), hence no finding classification.
VERDICT_TO_FINDING_CLASS: dict[str, str | None] = {
    ReviewVerdict.PASS.value: None,
    ReviewVerdict.PASS_WITH_FINDINGS.value: "NON_BLOCKING",
    ReviewVerdict.NEEDS_FIX.value: "BLOCKING",
    ReviewVerdict.BLOCKED.value: None,
    ReviewVerdict.INCONCLUSIVE.value: None,
}


def parse_review_verdict(value: Any) -> ReviewVerdict:
    if isinstance(value, ReviewVerdict):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return ReviewVerdict(value.strip())
        except ValueError as exc:
            raise ValueError(f"Unknown ReviewVerdict: {value!r}; expected one of {sorted(REVIEW_VERDICTS)}") from exc
    raise TypeError(f"verdict must be ReviewVerdict or str, got {type(value).__name__}")


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    return stripped


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


def reviewer_test_run_authorized(*, work_role: str, validation_expectations: Sequence[str]) -> bool:
    """Conditional server authorization rule for reviewer test.run.

    The narrowest reliable typed seam: a reviewer TaskHandoff that carries
    validation expectations requires review evidence, hence validation
    capability; a reviewer handoff without validation expectations (or any
    non-reviewer role through this seam) stays denied. Visibility is eager
    (surface), authorization is conditional (binding authority).
    """
    if not isinstance(work_role, str) or type(work_role) is not str:
        raise TypeError(f"work_role must be a string, got {type(work_role).__name__}")
    expectations = tuple(validation_expectations)
    for exp in expectations:
        if not isinstance(exp, str):
            raise TypeError("validation_expectations must contain strings")
    return work_role.strip() == "reviewer" and len(expectations) > 0


@dataclass(frozen=True)
class ProductEffectEvidence:
    """Typed inputs a Reviewer must compare before emitting a verdict.

    A Coder PASS alone never proves product effect: the verdict requires
    handoff expectations, the Coder Result/card identity, the actual
    repository delta/effect refs, test evidence refs, and (where relevant)
    negative/fail-closed behavior evidence.
    """

    handoff_acceptance_expectations: tuple[str, ...] = ()
    coder_result_ref: str | None = None
    coder_claimed_pass: bool = False
    repo_delta_refs: tuple[str, ...] = ()
    test_evidence_refs: tuple[str, ...] = ()
    negative_behavior_evidence_refs: tuple[str, ...] = ()
    missing_artifact: bool = False

    def __post_init__(self) -> None:
        acc = tuple(self.handoff_acceptance_expectations)
        for exp in acc:
            if not isinstance(exp, str) or not exp.strip():
                raise ValueError("handoff_acceptance_expectations must contain non-empty strings")
        object.__setattr__(self, "handoff_acceptance_expectations", acc)
        if self.coder_result_ref is not None:
            object.__setattr__(self, "coder_result_ref", _require_non_empty_str(self.coder_result_ref, "coder_result_ref"))
        _require_strict_bool(self.coder_claimed_pass, "coder_claimed_pass")
        for label in ("repo_delta_refs", "test_evidence_refs", "negative_behavior_evidence_refs"):
            refs = tuple(getattr(self, label))
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    raise ValueError(f"{label} must contain non-empty strings")
            object.__setattr__(self, label, refs)
        _require_strict_bool(self.missing_artifact, "missing_artifact")


@dataclass(frozen=True)
class ProductEffectVerdict:
    verdict: ReviewVerdict
    satisfied_expectations: tuple[str, ...] = ()
    unsatisfied_expectations: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, ReviewVerdict):
            raise TypeError(f"verdict must be ReviewVerdict, got {type(self.verdict).__name__}")


def verify_product_effect(
    evidence: ProductEffectEvidence,
    *,
    satisfied_expectations: Sequence[str] = (),
    unsatisfied_expectations: Sequence[str] = (),
    non_blocking_findings: Sequence[str] = (),
    review_inconclusive: bool = False,
    inconclusive_reason: str | None = None,
) -> ProductEffectVerdict:
    """Pure product-effect verification: derive the taxonomy verdict.

    Rules (fail-closed, Coder PASS never sufficient alone):

    - missing review artifact -> BLOCKED/INCONCLUSIVE (no reliable verdict).
    - explicitly inconclusive (insufficient evidence) -> INCONCLUSIVE.
    - no repo delta/effect refs while acceptance expectations exist ->
      NEEDS_FIX (nothing observable proves effect).
    - any unsatisfied acceptance expectation -> NEEDS_FIX.
    - all satisfied + bounded non-blocking findings -> PASS_WITH_FINDINGS.
    - all satisfied, no findings -> PASS.
    """
    if not isinstance(evidence, ProductEffectEvidence):
        raise TypeError(f"evidence must be ProductEffectEvidence, got {type(evidence).__name__}")
    satisfied = tuple(satisfied_expectations)
    unsatisfied = tuple(unsatisfied_expectations)
    findings = tuple(non_blocking_findings)
    _require_strict_bool(review_inconclusive, "review_inconclusive")

    if evidence.missing_artifact:
        return ProductEffectVerdict(ReviewVerdict.BLOCKED, detail="review artifact missing: no reliable verdict")
    if review_inconclusive:
        return ProductEffectVerdict(
            ReviewVerdict.INCONCLUSIVE,
            detail=inconclusive_reason or "insufficient evidence for a reliable verdict",
        )
    if evidence.handoff_acceptance_expectations and not evidence.repo_delta_refs:
        return ProductEffectVerdict(
            ReviewVerdict.NEEDS_FIX,
            unsatisfied_expectations=evidence.handoff_acceptance_expectations,
            detail="no repository delta/effect evidence: Coder PASS alone proves nothing",
        )
    if unsatisfied:
        return ProductEffectVerdict(
            ReviewVerdict.NEEDS_FIX,
            satisfied_expectations=satisfied,
            unsatisfied_expectations=unsatisfied,
            detail="acceptance expectations not satisfied",
        )
    if findings:
        return ProductEffectVerdict(
            ReviewVerdict.PASS_WITH_FINDINGS,
            satisfied_expectations=satisfied,
            detail=f"{len(findings)} bounded non-blocking finding(s)",
        )
    return ProductEffectVerdict(
        ReviewVerdict.PASS, satisfied_expectations=satisfied, detail="acceptance evidence satisfied"
    )


@dataclass(frozen=True)
class ReviewerReviewPayload:
    """Typed reviewer review payload body (role-specific, bounded).

    Integrates into the W1 common envelope via kind
    ``reviewer_review_evidence`` (RolePayloadRef kind/ref/digest); the full
    body stays bounded evidence, never a transcript. No second transport.
    """

    verdict: ReviewVerdict
    reviewed_handoff_digest: str
    coder_result_digest: str | None = None
    satisfied_expectations: tuple[str, ...] = ()
    unsatisfied_expectations: tuple[str, ...] = ()
    finding_refs: tuple[str, ...] = ()
    independent_validation_performed: bool = False

    PAYLOAD_KIND = "reviewer_review_evidence"
    MAX_FINDINGS = 16

    def __post_init__(self) -> None:
        if isinstance(self.verdict, ReviewVerdict):
            pass
        elif isinstance(self.verdict, str) and type(self.verdict) is str:
            object.__setattr__(self, "verdict", parse_review_verdict(self.verdict))
        else:
            raise TypeError(f"verdict must be ReviewVerdict or str, got {type(self.verdict).__name__}")
        object.__setattr__(
            self, "reviewed_handoff_digest", _require_non_empty_str(self.reviewed_handoff_digest, "reviewed_handoff_digest")
        )
        if self.coder_result_digest is not None:
            object.__setattr__(
                self, "coder_result_digest", _require_non_empty_str(self.coder_result_digest, "coder_result_digest")
            )
        for label in ("satisfied_expectations", "unsatisfied_expectations"):
            vals = tuple(getattr(self, label))
            for val in vals:
                if not isinstance(val, str) or not val.strip():
                    raise ValueError(f"{label} must contain non-empty strings")
            object.__setattr__(self, label, vals)
        findings = tuple(self.finding_refs)
        if len(findings) > self.MAX_FINDINGS:
            raise ValueError(f"finding_refs count ({len(findings)}) exceeds maximum {self.MAX_FINDINGS}")
        for ref in findings:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("finding_refs must contain non-empty strings")
        object.__setattr__(self, "finding_refs", findings)
        _require_strict_bool(self.independent_validation_performed, "independent_validation_performed")
        # Taxonomy coherence: NEEDS_FIX requires unsatisfied expectations;
        # PASS requires none unsatisfied; PASS_WITH_FINDINGS requires findings.
        if self.verdict is ReviewVerdict.NEEDS_FIX and not self.unsatisfied_expectations:
            raise ValueError("NEEDS_FIX requires at least one unsatisfied expectation")
        if self.verdict is ReviewVerdict.PASS and (self.unsatisfied_expectations or self.finding_refs):
            raise ValueError("PASS requires no unsatisfied expectations and no findings")
        if self.verdict is ReviewVerdict.PASS_WITH_FINDINGS and not self.finding_refs:
            raise ValueError("PASS_WITH_FINDINGS requires bounded non-blocking findings")

    def finding_class(self) -> str | None:
        return VERDICT_TO_FINDING_CLASS[self.verdict.value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.PAYLOAD_KIND,
            "verdict": self.verdict.value,
            "finding_class": self.finding_class(),
            "reviewed_handoff_digest": self.reviewed_handoff_digest,
            "coder_result_digest": self.coder_result_digest,
            "satisfied_expectations": list(self.satisfied_expectations),
            "unsatisfied_expectations": list(self.unsatisfied_expectations),
            "finding_refs": list(self.finding_refs),
            "independent_validation_performed": self.independent_validation_performed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReviewerReviewPayload:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {
            "kind",
            "verdict",
            "finding_class",
            "reviewed_handoff_digest",
            "coder_result_digest",
            "satisfied_expectations",
            "unsatisfied_expectations",
            "finding_refs",
            "independent_validation_performed",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in ReviewerReviewPayload: {sorted(extra)}")
        if "verdict" not in data or "reviewed_handoff_digest" not in data:
            raise ValueError("ReviewerReviewPayload requires verdict and reviewed_handoff_digest")
        verdict = parse_review_verdict(data["verdict"])
        expected_class = VERDICT_TO_FINDING_CLASS[verdict.value]
        if data.get("finding_class") is not None and data["finding_class"] != expected_class:
            raise ValueError("finding_class contradicts verdict taxonomy mapping")
        return cls(
            verdict=verdict,
            reviewed_handoff_digest=data["reviewed_handoff_digest"],
            coder_result_digest=data.get("coder_result_digest"),
            satisfied_expectations=tuple(data.get("satisfied_expectations") or ()),
            unsatisfied_expectations=tuple(data.get("unsatisfied_expectations") or ()),
            finding_refs=tuple(data.get("finding_refs") or ()),
            independent_validation_performed=bool(data.get("independent_validation_performed", False)),
        )


__all__ = [
    "REVIEWER_INDEPENDENT_VALIDATION_RUNTIME_WIRED",
    "REVIEWER_CAN_USE_VALIDATION_TOOL",
    "REVIEWER_TEST_RUN_POLICY",
    "REVIEWER_TEST_RUN_EVERY_REVIEW_REQUIRED",
    "REVIEWER_CAN_MUTATE_PRODUCT_SOURCE",
    "REVIEWER_WORKSPACE_WRITE_PRODUCT_SOURCE",
    "REVIEWER_UNRESTRICTED_SHELL",
    "REVIEW_RESULT_TAXONOMY_WIRED",
    "REVIEWER_MUST_VERIFY_PRODUCT_EFFECT",
    "CODER_RESULT_PASS_IS_NOT_REVIEWER_PROOF",
    "REVIEW_PASS_AUTOMATICALLY_CLOSES_MILESTONE",
    "REVIEWER_IS_FINAL_ACCEPTANCE_AUTHORITY",
    "REVIEWER_IS_PLAN_AUTHORITY",
    "ReviewVerdict",
    "REVIEW_VERDICTS",
    "VERDICT_TO_FINDING_CLASS",
    "parse_review_verdict",
    "reviewer_test_run_authorized",
    "ProductEffectEvidence",
    "ProductEffectVerdict",
    "verify_product_effect",
    "ReviewerReviewPayload",
]
