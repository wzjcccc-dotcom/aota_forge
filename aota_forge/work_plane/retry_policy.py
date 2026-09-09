"""Retry / no-progress runtime policy (M2/W2 runtime).

Uses the durable attempt state from W1 (coordinator record_attempt). A
retry requires new evidence, a changed repair hypothesis, a bounded
changed approach, or a reasonable progress rationale:

    RETRY_REQUIRES_PROGRESS_RATIONALE=yes
    NO_PROGRESS_RETRY_ALLOWED=no
    REPEATED_NO_PROGRESS_REQUIRES_ESCALATION=yes

Escalation vocabulary (task-main owns the disposition):

    review / replan / needs_input / human checkpoint / blocked.

No universal numeric retry count is frozen (no existing authoritative
contract requires one); escalation is evidence-driven.

Hard: see markers below. Reuses CoderRepairAttempt as the compact attempt
evidence shape alongside the coordinator durable attempt truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

RETRY_REQUIRES_PROGRESS_RATIONALE = True
NO_PROGRESS_RETRY_ALLOWED = False
REPEATED_NO_PROGRESS_REQUIRES_ESCALATION = True
UNIVERSAL_NUMERIC_RETRY_COUNT_FROZEN = False


@unique
class RetryEscalation(str, Enum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    NEEDS_INPUT = "NEEDS_INPUT"
    HUMAN_CHECKPOINT_REQUIRED = "HUMAN_CHECKPOINT_REQUIRED"
    BLOCKED = "BLOCKED"


@unique
class RetryDecisionKind(str, Enum):
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class AttemptEvidence:
    """Compact attempt truth (mirrors coordinator attempt_states shape)."""

    attempt: int
    failure_class: str
    new_evidence: bool = False
    changed_hypothesis: bool = False
    changed_approach: bool = False
    progress_rationale: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, int) or type(self.attempt) is not int or self.attempt < 1:
            raise ValueError(f"attempt must be an int >= 1, got {self.attempt!r}")
        if not isinstance(self.failure_class, str) or not self.failure_class.strip():
            raise ValueError("failure_class must be a non-empty string")
        for flag in ("new_evidence", "changed_hypothesis", "changed_approach"):
            if type(getattr(self, flag)) is not bool:
                raise TypeError(f"{flag} must be bool")
        if self.progress_rationale is not None:
            if not isinstance(self.progress_rationale, str) or not self.progress_rationale.strip():
                raise ValueError("progress_rationale must be a non-empty string when provided")

    @property
    def carries_progress(self) -> bool:
        return (
            self.new_evidence
            or self.changed_hypothesis
            or self.changed_approach
            or self.progress_rationale is not None
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AttemptEvidence:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        return cls(
            attempt=int(data.get("attempt", 1)),
            failure_class=str(data.get("failure_class", "UNKNOWN")),
            new_evidence=bool(data.get("new_evidence", False)),
            changed_hypothesis=bool(data.get("changed_hypothesis", False) or data.get("hypothesis_ref")),
            changed_approach=bool(data.get("changed_approach", False)),
            progress_rationale=data.get("progress_rationale"),
        )


@dataclass(frozen=True)
class RetryDecision:
    kind: RetryDecisionKind
    escalation: RetryEscalation | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RetryDecisionKind):
            raise TypeError(f"kind must be RetryDecisionKind, got {type(self.kind).__name__}")
        if self.kind is RetryDecisionKind.RETRY and self.escalation is not None:
            raise ValueError("RETRY must not carry an escalation")
        if self.kind is RetryDecisionKind.ESCALATE and self.escalation is None:
            raise ValueError("ESCALATE requires an escalation target")


def evaluate_retry(
    history: Sequence[AttemptEvidence],
    proposed: AttemptEvidence,
    *,
    default_escalation: RetryEscalation = RetryEscalation.REVIEW_REQUIRED,
) -> RetryDecision:
    """Pure retry/no-progress policy.

    - The proposed retry must carry progress (new evidence, changed
      hypothesis, bounded changed approach, or progress rationale);
      otherwise ESCALATE (no-progress retry denied).
    - A repeated same-class failure with no progress across the trailing
      history escalates even when the proposal claims progress wording
      without substance (proposal must still carry progress flags).
    """
    past = tuple(history)
    for entry in past:
        if not isinstance(entry, AttemptEvidence):
            raise TypeError(f"history must contain AttemptEvidence, got {type(entry).__name__}")
    if not isinstance(proposed, AttemptEvidence):
        raise TypeError(f"proposed must be AttemptEvidence, got {type(proposed).__name__}")
    if not isinstance(default_escalation, RetryEscalation):
        raise TypeError(f"default_escalation must be RetryEscalation, got {type(default_escalation).__name__}")

    if not proposed.carries_progress:
        # Repeated no-progress requires escalation (first no-progress too:
        # NO_PROGRESS_RETRY_ALLOWED=no).
        return RetryDecision(
            RetryDecisionKind.ESCALATE,
            default_escalation,
            "retry without new evidence, changed hypothesis, changed approach, or progress rationale is denied",
        )
    # Repeated same-class failure: require genuinely new substance on the
    # proposal (new evidence or changed hypothesis/approach), rationale
    # text alone does not justify a third same-class attempt.
    trailing_same = [e for e in past[-2:] if e.failure_class == proposed.failure_class]
    if len(trailing_same) >= 2 and not (
        proposed.new_evidence or proposed.changed_hypothesis or proposed.changed_approach
    ):
        return RetryDecision(
            RetryDecisionKind.ESCALATE,
            default_escalation,
            f"repeated {proposed.failure_class} failure: rationale alone cannot justify another same-class retry",
        )
    return RetryDecision(RetryDecisionKind.RETRY)


__all__ = [
    "RETRY_REQUIRES_PROGRESS_RATIONALE",
    "NO_PROGRESS_RETRY_ALLOWED",
    "REPEATED_NO_PROGRESS_REQUIRES_ESCALATION",
    "UNIVERSAL_NUMERIC_RETRY_COUNT_FROZEN",
    "RetryEscalation",
    "RetryDecisionKind",
    "AttemptEvidence",
    "RetryDecision",
    "evaluate_retry",
]
