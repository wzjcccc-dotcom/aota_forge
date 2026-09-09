"""Coder normal lifecycle + self-validation runtime (M2/W2 runtime).

M1 frozen (see #44 M1/W3, M1/W4):

    Coder -> inspect -> implement/repair -> syntax/import sanity -> tests
    -> targeted validation -> bounded behavior validation
    -> bounded self-repair -> Result

    CODER_TEST_RUN_NORMAL_PATH=yes
    CODER_CAN_SELF_REPAIR=yes
    CODER_CAN_ACCEPT_OWN_WORK=no
    BOUNDED_SELF_REPAIR_ALLOWED=yes
    UNBOUNDED_REPAIR_LOOP_ALLOWED=no
    CODER_CAN_REDEFINE_ACCEPTANCE=no
    CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS=no
    CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS=no
    TEST_MODIFICATION_REQUIRES_JUSTIFICATION=yes
    TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS=yes
    TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS=yes

The runtime supports this without returning to task-main for every small
error: bounded self-repair continues inside the one-shot Coder session.
Coder stops/escalates when continuation would need scope expansion, an
architecture decision, authority expansion, acceptance redefinition,
material ambiguity, or repeated same-class failure with no progress
rationale.

Hard:

    CODER_SELF_VALIDATION_RUNTIME_WIRED=yes
    CODER_BOUNDED_SELF_REPAIR_WIRED=yes
    CODER_CAN_ACCEPT_OWN_WORK=no
    VALIDATION_INTEGRITY_FIREWALL_WIRED=yes

Reuses existing contracts only: TaskHandoff (frozen expectations),
TestExecutionAuthorityEvidence (test.run), CommonResultEnvelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

CODER_SELF_VALIDATION_RUNTIME_WIRED = True
CODER_BOUNDED_SELF_REPAIR_WIRED = True
CODER_CAN_SELF_REPAIR = True
CODER_CAN_ACCEPT_OWN_WORK = False
CODER_SELF_VALIDATION_IS_ACCEPTANCE_AUTHORITY = False
BOUNDED_SELF_REPAIR_ALLOWED = True
UNBOUNDED_REPAIR_LOOP_ALLOWED = False
VALIDATION_INTEGRITY_FIREWALL_WIRED = True
CODER_CAN_REDEFINE_ACCEPTANCE = False
CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS = False
CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS = False
TEST_MODIFICATION_REQUIRES_JUSTIFICATION = True


@unique
class CoderPhase(str, Enum):
    """Normal Coder lifecycle phases (frozen M1 order)."""

    INSPECT = "INSPECT"
    IMPLEMENT_OR_REPAIR = "IMPLEMENT_OR_REPAIR"
    SANITY = "SANITY"
    TESTS = "TESTS"
    TARGETED_VALIDATION = "TARGETED_VALIDATION"
    BEHAVIOR_VALIDATION = "BEHAVIOR_VALIDATION"
    SELF_REPAIR = "SELF_REPAIR"
    RESULT = "RESULT"


CODER_PHASE_ORDER: tuple[str, ...] = tuple(p.value for p in CoderPhase)


@unique
class CoderContinuation(str, Enum):
    CONTINUE_SELF_REPAIR = "CONTINUE_SELF_REPAIR"
    STOP_ESCALATE = "STOP_ESCALATE"


@unique
class CoderEscalation(str, Enum):
    """Bounded escalation vocabulary (task-main owns the disposition)."""

    NEEDS_SCOPE_DECISION = "NEEDS_SCOPE_DECISION"
    NEEDS_ARCHITECTURE_DECISION = "NEEDS_ARCHITECTURE_DECISION"
    NEEDS_AUTHORITY_EXPANSION = "NEEDS_AUTHORITY_EXPANSION"
    NEEDS_ACCEPTANCE_REDEFINITION = "NEEDS_ACCEPTANCE_REDEFINITION"
    NEEDS_INPUT = "NEEDS_INPUT"
    REPEATED_FAILURE_NO_PROGRESS = "REPEATED_FAILURE_NO_PROGRESS"


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


@dataclass(frozen=True)
class CoderRepairAttempt:
    """Compact evidence for one bounded self-repair attempt (no log dump)."""

    failure_class: str
    changed_hypothesis: bool = False
    changed_approach: bool = False
    new_evidence: bool = False
    progress_rationale: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_class", _require_non_empty_str(self.failure_class, "failure_class"))
        _require_strict_bool(self.changed_hypothesis, "changed_hypothesis")
        _require_strict_bool(self.changed_approach, "changed_approach")
        _require_strict_bool(self.new_evidence, "new_evidence")
        if self.progress_rationale is not None:
            object.__setattr__(
                self,
                "progress_rationale",
                _require_non_empty_str(self.progress_rationale, "progress_rationale", max_length=1024),
            )

    @property
    def carries_progress(self) -> bool:
        """A repair attempt carries progress only with new evidence, a
        changed hypothesis/approach, or an explicit progress rationale."""
        return (
            self.new_evidence
            or self.changed_hypothesis
            or self.changed_approach
            or self.progress_rationale is not None
        )


@dataclass(frozen=True)
class CoderContinuationDecision:
    continuation: CoderContinuation
    escalation: CoderEscalation | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.continuation, CoderContinuation):
            raise TypeError(f"continuation must be CoderContinuation, got {type(self.continuation).__name__}")
        if self.continuation is CoderContinuation.CONTINUE_SELF_REPAIR and self.escalation is not None:
            raise ValueError("CONTINUE_SELF_REPAIR must not carry an escalation")
        if self.continuation is CoderContinuation.STOP_ESCALATE and self.escalation is None:
            raise ValueError("STOP_ESCALATE requires an escalation")


def evaluate_coder_continuation(
    attempts: Sequence[CoderRepairAttempt],
    *,
    scope_expansion_needed: bool = False,
    architecture_decision_needed: bool = False,
    authority_expansion_needed: bool = False,
    acceptance_redefinition_needed: bool = False,
    material_ambiguity: bool = False,
) -> CoderContinuationDecision:
    """Pure bounded self-repair policy: continue in-session or stop/escalate.

    No generic uncontrolled retry loop: continuation requires the latest
    attempt to carry progress (new evidence / changed hypothesis / bounded
    changed approach / progress rationale). A repeated same-class failure
    with no progress rationale escalates. Any need for scope expansion,
    architecture/authority/acceptance decisions, or material ambiguity
    stops immediately.
    """
    _require_strict_bool(scope_expansion_needed, "scope_expansion_needed")
    _require_strict_bool(architecture_decision_needed, "architecture_decision_needed")
    _require_strict_bool(authority_expansion_needed, "authority_expansion_needed")
    _require_strict_bool(acceptance_redefinition_needed, "acceptance_redefinition_needed")
    _require_strict_bool(material_ambiguity, "material_ambiguity")

    if scope_expansion_needed:
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE, CoderEscalation.NEEDS_SCOPE_DECISION, "continuation needs scope expansion"
        )
    if architecture_decision_needed:
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE,
            CoderEscalation.NEEDS_ARCHITECTURE_DECISION,
            "continuation needs an architecture decision",
        )
    if authority_expansion_needed:
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE,
            CoderEscalation.NEEDS_AUTHORITY_EXPANSION,
            "continuation needs authority expansion",
        )
    if acceptance_redefinition_needed:
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE,
            CoderEscalation.NEEDS_ACCEPTANCE_REDEFINITION,
            "continuation would redefine acceptance (denied)",
        )
    if material_ambiguity:
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE, CoderEscalation.NEEDS_INPUT, "material ambiguity blocks self-repair"
        )

    attempts = tuple(attempts)
    for attempt in attempts:
        if not isinstance(attempt, CoderRepairAttempt):
            raise TypeError(f"attempts must contain CoderRepairAttempt, got {type(attempt).__name__}")
    if not attempts:
        return CoderContinuationDecision(CoderContinuation.CONTINUE_SELF_REPAIR)
    latest = attempts[-1]
    if not latest.carries_progress:
        # Repeated same-class failure with no progress rationale escalates.
        if len(attempts) >= 2 and all(a.failure_class == latest.failure_class for a in attempts[-2:]):
            return CoderContinuationDecision(
                CoderContinuation.STOP_ESCALATE,
                CoderEscalation.REPEATED_FAILURE_NO_PROGRESS,
                f"repeated {latest.failure_class} failure with no progress rationale",
            )
        return CoderContinuationDecision(
            CoderContinuation.STOP_ESCALATE,
            CoderEscalation.REPEATED_FAILURE_NO_PROGRESS,
            "repair attempt carries no new evidence, changed hypothesis, changed approach, or progress rationale",
        )
    return CoderContinuationDecision(CoderContinuation.CONTINUE_SELF_REPAIR)


@unique
class TestModificationKind(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class TestModificationRecord:
    """Typed justification for any Coder test modification.

    Silent weakening is denied: every added/updated/deleted test carries an
    explicit justification plus an evidence ref. Acceptance expectations
    remain frozen in the TaskHandoff; this record justifies the *change*,
    never a redefinition.
    """

    test_path: str
    kind: TestModificationKind
    justification: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "test_path", _require_non_empty_str(self.test_path, "test_path"))
        if isinstance(self.kind, TestModificationKind):
            pass
        elif isinstance(self.kind, str) and type(self.kind) is str:
            try:
                object.__setattr__(self, "kind", TestModificationKind(self.kind))
            except ValueError as exc:
                raise ValueError(f"Unknown TestModificationKind: {self.kind!r}") from exc
        else:
            raise TypeError(f"kind must be TestModificationKind or str, got {type(self.kind).__name__}")
        object.__setattr__(
            self, "justification", _require_non_empty_str(self.justification, "justification", max_length=1024)
        )
        object.__setattr__(self, "evidence_ref", _require_non_empty_str(self.evidence_ref, "evidence_ref"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_path": self.test_path,
            "kind": self.kind.value,
            "justification": self.justification,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TestModificationRecord:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {"test_path", "kind", "justification", "evidence_ref"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in TestModificationRecord: {sorted(extra)}")
        for req in allowed:
            if req not in data:
                raise ValueError(f"Missing required field in TestModificationRecord: {req!r}")
        return cls(
            test_path=data["test_path"],
            kind=data["kind"],
            justification=data["justification"],
            evidence_ref=data["evidence_ref"],
        )


def assert_test_modifications_justified(modifications: Sequence[TestModificationRecord | Mapping[str, Any]]) -> None:
    """Fail closed when any test modification lacks a typed justification."""
    mods = tuple(modifications)
    if not mods:
        return
    for idx, mod in enumerate(mods):
        if isinstance(mod, Mapping):
            mod = TestModificationRecord.from_dict(mod)
        if not isinstance(mod, TestModificationRecord):
            raise TypeError(f"modifications[{idx}] must be TestModificationRecord, got {type(mod).__name__}")
        # Construction already enforces justification + evidence_ref.


def assert_acceptance_expectations_frozen(
    *,
    handoff_acceptance: Sequence[str],
    handoff_validation: Sequence[str],
    claimed_acceptance: Sequence[str],
    claimed_validation: Sequence[str],
) -> None:
    """Validation integrity firewall: Coder cannot redefine or weaken
    frozen acceptance/validation expectations to pass.

    The TaskHandoff freezes both expectation sets; a Coder Result claiming
    a narrower (weakened) or different (redefined) set fails closed.
    Claiming the exact frozen sets passes.
    """
    frozen_acc = tuple(handoff_acceptance)
    frozen_val = tuple(handoff_validation)
    claimed_acc = tuple(claimed_acceptance)
    claimed_val = tuple(claimed_validation)
    if set(claimed_acc) != set(frozen_acc) or len(claimed_acc) != len(frozen_acc):
        raise ValueError(
            "Coder Result acceptance expectations differ from frozen TaskHandoff expectations "
            "(redefinition/weakening denied)"
        )
    if set(claimed_val) != set(frozen_val) or len(claimed_val) != len(frozen_val):
        raise ValueError(
            "Coder Result validation expectations differ from frozen TaskHandoff expectations "
            "(redefinition/weakening denied)"
        )


__all__ = [
    "CODER_SELF_VALIDATION_RUNTIME_WIRED",
    "CODER_BOUNDED_SELF_REPAIR_WIRED",
    "CODER_CAN_SELF_REPAIR",
    "CODER_CAN_ACCEPT_OWN_WORK",
    "CODER_SELF_VALIDATION_IS_ACCEPTANCE_AUTHORITY",
    "BOUNDED_SELF_REPAIR_ALLOWED",
    "UNBOUNDED_REPAIR_LOOP_ALLOWED",
    "VALIDATION_INTEGRITY_FIREWALL_WIRED",
    "CODER_CAN_REDEFINE_ACCEPTANCE",
    "CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS",
    "CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS",
    "TEST_MODIFICATION_REQUIRES_JUSTIFICATION",
    "CoderPhase",
    "CODER_PHASE_ORDER",
    "CoderContinuation",
    "CoderEscalation",
    "CoderRepairAttempt",
    "CoderContinuationDecision",
    "TestModificationKind",
    "TestModificationRecord",
    "evaluate_coder_continuation",
    "assert_test_modifications_justified",
    "assert_acceptance_expectations_frozen",
]
