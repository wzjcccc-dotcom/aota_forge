"""AF #57 M2/W3 — Deterministic Project Stewardship Subsystem.

Convergence target (mechanism construction only; M3 owns real production
conditional-invocation proof):

    current:
        task-main
          -> project-steward LLM Role
          -> StewardResult
          -> TrustedStewardFinalizer

    target:
        Governance checkpoint            (typed in-process model)
          -> deterministic Stewardship Engine (mechanical checks first)
          -> semantic residual? no
                -> mechanically derived bounded StewardResult
                -> existing TrustedStewardFinalizer
                -> close / materialize with NO LLM invocation
          -> semantic residual? yes
                -> bounded SemanticResidual (kind + reason + bounded refs)
                -> existing bounded Project Steward dispatch seam
                -> existing StewardResult contract
                -> same TrustedStewardFinalizer

Hard architecture (all asserted as module markers):

    PROJECT_STEWARD_IS_NORMAL_WORKER=no
    PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN=yes

    DETERMINISTIC_FIRST_GOVERNANCE=yes
    CHECKPOINT_DOES_NOT_IMPLY_LLM_INVOCATION=yes

    STEWARDSHIP_REPORT_BY_EXCEPTION=yes
    NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN=no

    NO_FLAG_DAY_DELETION=yes
    CURRENT_PROJECT_STEWARD_ROLE_REMAINS_MIGRATION_COMPATIBILITY=yes

    STEWARDSHIP_CAN_INFER_USER_APPROVAL=no
    STEWARDSHIP_CAN_SET_USER_APPROVAL=no
    STEWARDSHIP_CAN_INVENT_ACCEPTED_FRONTIER=no

    TRUSTED_STEWARD_FINALIZER_REUSED=yes
    FINALIZER_INPUT_IS_STEWARD_RESULT=yes
    SECOND_FINALIZATION_PROTOCOL=no
    SECOND_MUTATION_PROTOCOL=no
    GENERIC_GOVERNANCE_WRITE_API=no

    FAKE_STEWARD_RESULT=no
    FABRICATED_SEMANTIC_JUDGMENT=no
    DETERMINISTIC_STEWARD_RESULT_IS_MECHANICALLY_DERIVED=yes

    NEW_EVENT_BUS_CREATED=no
    NEW_WORKFLOW_DATABASE_CREATED=no
    NEW_SCHEDULER_CREATED=no

The engine is semantic/mechanical evaluation only — never mutation
authority.  Mutation stays with the existing trusted seams
(``TrustedStewardFinalizer`` and, where applicable, the existing
``PlanAuthorityMutationPort`` / governed Git/GitHub authority owned
elsewhere).  No generic governance write API is introduced.

The deterministic producer only populates semantic fields that are
mechanically derivable from trusted checkpoint facts.  It never invents
architecture interpretation, unresolved defect disposition, Plan
semantics, review meaning, user approval or an accepted frontier.  Any
checkpoint that cannot be mechanically decided yields a bounded
``SemanticResidual`` routed through the existing Project Steward dispatch
seam — the current Role remains available (no flag day, no deletion).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Callable

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.cards import require_logical_ref
from aota_forge.governance.context_route import ContextRoute
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.materialization_receipt import MaterializationReceipt
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.steward_dispatch import (
    StewardClosureVerdict,
    StewardResult,
    build_mode_a_handoff,
    build_mode_b_handoff,
)
from aota_forge.work_plane.steward_finalizer import (
    MANAGED_ROLES,
    ClosurePhase,
    FinalizerInput,
    GitHubUpdateIntent,
    GitPromotionIntent,
    MaterializationIntent,
    TrustedPlanIdentity,
    TrustedProjectBinding,
    TrustedStewardFinalizer,
    TrustedUserGateState,
)

# ---------------------------------------------------------------------------
# Architecture markers (importable for tests / review)
# ---------------------------------------------------------------------------

PROJECT_STEWARD_IS_NORMAL_WORKER = False
PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN = True

DETERMINISTIC_FIRST_GOVERNANCE = True
CHECKPOINT_DOES_NOT_IMPLY_LLM_INVOCATION = True

STEWARDSHIP_REPORT_BY_EXCEPTION = True
NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN = False
NO_FLAG_DAY_DELETION = True
CURRENT_PROJECT_STEWARD_ROLE_REMAINS_MIGRATION_COMPATIBILITY = True

STEWARDSHIP_CAN_INFER_USER_APPROVAL = False
STEWARDSHIP_CAN_SET_USER_APPROVAL = False
STEWARDSHIP_CAN_INVENT_ACCEPTED_FRONTIER = False
STEWARDSHIP_IS_ACCEPTANCE_AUTHORITY = False
STEWARDSHIP_IS_MUTATION_AUTHORITY = False
STEWARDSHIP_CAN_EXCEED_STEWARD_RESULT_SCOPE = False

TRUSTED_STEWARD_FINALIZER_REUSED = True
FINALIZER_INPUT_IS_STEWARD_RESULT = True
SECOND_FINALIZATION_PROTOCOL = False
SECOND_MUTATION_PROTOCOL = False
SECOND_RESULT_TRANSPORT_CREATED = False
GENERIC_GOVERNANCE_WRITE_API = False
RAW_ARBITRARY_MUTATION_COMMAND_ACCEPTED = False

DETERMINISTIC_STEWARD_RESULT_PATH = True
DETERMINISTIC_STEWARD_RESULT_IS_MECHANICALLY_DERIVED = True
FAKE_STEWARD_RESULT = False
FABRICATED_SEMANTIC_JUDGMENT = False
DETERMINISTIC_PATH_INVOKES_LLM = False
NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH = True
LLM_PATH_REMAINS_AVAILABLE = True

NEW_EVENT_BUS_CREATED = False
NEW_WORKFLOW_DATABASE_CREATED = False
NEW_SCHEDULER_CREATED = False
NEW_PLUGIN_MARKETPLACE_CREATED = False
NEW_EXECUTION_STATE_MACHINE_CREATED = False

CHECKPOINT_MODEL_IMPLEMENTED = True
SEMANTIC_RESIDUAL_MODEL_IMPLEMENTED = True

# ---------------------------------------------------------------------------
# Bounded capacities
# ---------------------------------------------------------------------------

MAX_CHECKPOINT_ID_LENGTH = 128
MAX_IDENTITY_REF_LENGTH = 512
MAX_RESIDUAL_REASON_LENGTH = 512
MAX_REQUIRED_INPUT_REFS = 16
MAX_OPEN_BLOCKER_REFS = 16
MAX_SEMANTIC_FACT_REFS = 16
MAX_MANAGED_COMMENT_FACTS = 5
MAX_HANDOFF_OBJECTIVE_LENGTH = 4096
MAX_HANDOFF_SCOPE_LENGTH = 4096

# ---------------------------------------------------------------------------
# Checkpoint kinds (exactly three conceptual kinds)
# ---------------------------------------------------------------------------


@unique
class GovernanceCheckpointKind(str, Enum):
    PLAN_INIT = "PLAN_INIT"
    MILESTONE_CLOSE = "MILESTONE_CLOSE"
    PLAN_CLOSE = "PLAN_CLOSE"


CHECKPOINT_KINDS: frozenset[str] = frozenset(kind.value for kind in GovernanceCheckpointKind)

# ---------------------------------------------------------------------------
# Deterministic dispositions
# ---------------------------------------------------------------------------


@unique
class StewardshipDisposition(str, Enum):
    DETERMINISTIC_FINALIZABLE = "DETERMINISTIC_FINALIZABLE"
    DETERMINISTIC_SATISFIED = "DETERMINISTIC_SATISFIED"
    DETERMINISTIC_BLOCKED = "DETERMINISTIC_BLOCKED"
    SEMANTIC_RESIDUAL_REQUIRED = "SEMANTIC_RESIDUAL_REQUIRED"


# ---------------------------------------------------------------------------
# Semantic residual kinds
# ---------------------------------------------------------------------------


@unique
class SemanticResidualKind(str, Enum):
    AMBIGUOUS_GOVERNANCE_REASON = "AMBIGUOUS_GOVERNANCE_REASON"
    ARCHITECTURE_INTERPRETATION = "ARCHITECTURE_INTERPRETATION"
    UNRESOLVED_DEFECT_DISPOSITION = "UNRESOLVED_DEFECT_DISPOSITION"
    DECISION_PLAN_CHANGE_INTERPRETATION = "DECISION_PLAN_CHANGE_INTERPRETATION"
    NARRATIVE_RECONCILIATION = "NARRATIVE_RECONCILIATION"
    CONFLICTING_SEMANTIC_ARTIFACTS = "CONFLICTING_SEMANTIC_ARTIFACTS"
    REVIEW_EVIDENCE_INTERPRETATION = "REVIEW_EVIDENCE_INTERPRETATION"
    RECORDED_SEMANTIC_QUESTION = "RECORDED_SEMANTIC_QUESTION"


# ---------------------------------------------------------------------------
# Mechanical check ids (deterministic evidence of what ran)
# ---------------------------------------------------------------------------

CHECK_IDENTITY_BINDING = "IDENTITY_BINDING"
CHECK_PLAN_IDENTITY = "PLAN_IDENTITY"
CHECK_NO_OPEN_BLOCKERS = "NO_OPEN_BLOCKERS"
CHECK_SEMANTIC_FACTS_NONE = "SEMANTIC_FACTS_NONE"
CHECK_PLAN_AUTHORITY_BINDING = "PLAN_AUTHORITY_BINDING"
CHECK_READINESS_FACT = "READINESS_FACT"
CHECK_MILESTONE_IDENTITY = "MILESTONE_IDENTITY"
CHECK_READINESS_CLEAR = "READINESS_CLEAR"
CHECK_USER_GATE_FACT = "USER_GATE_FACT"
CHECK_MATERIALIZATION_SCOPE = "MATERIALIZATION_SCOPE"
CHECK_PLAN_CLOSE_ALL_MILESTONES = "PLAN_CLOSE_ALL_MILESTONES"

# ---------------------------------------------------------------------------
# Bounded deterministic block codes
# ---------------------------------------------------------------------------

BLOCK_BINDING_PROJECT_MISMATCH = "BINDING_PROJECT_MISMATCH"
BLOCK_INVALID_PLAN_ID = "INVALID_PLAN_ID"
BLOCK_OPEN_BLOCKERS = "OPEN_BLOCKERS"
BLOCK_MISSING_PLAN_AUTHORITY = "MISSING_PLAN_AUTHORITY_BINDING"
BLOCK_MATERIALIZATION_SCOPE_VIOLATION = "MATERIALIZATION_SCOPE_VIOLATION"
BLOCK_MISSING_REQUIRED_FACTS = "MISSING_REQUIRED_FACTS"
BLOCK_MILESTONE_IDENTITY_MISMATCH = "MILESTONE_IDENTITY_MISMATCH"
BLOCK_READINESS_NOT_READY = "READINESS_NOT_READY"
BLOCK_USER_GATE_REQUIRED = "USER_GATE_REQUIRED"
BLOCK_MISSING_EXPECTED_OLD_REF = "MISSING_EXPECTED_OLD_REF"
BLOCK_INVALID_FRONTIER_REF = "INVALID_FRONTIER_REF"
BLOCK_INVALID_EXPECTED_OLD_REF = "INVALID_EXPECTED_OLD_REF"
BLOCK_MISSING_MANAGED_COMMENT_FACT = "MISSING_MANAGED_COMMENT_FACT"
BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH = "MANAGED_COMMENT_IDENTITY_MISMATCH"
BLOCK_INVALID_MANAGED_COMMENT_DIGEST = "INVALID_MANAGED_COMMENT_DIGEST"
BLOCK_PLAN_MILESTONES_NOT_CLOSED = "PLAN_MILESTONES_NOT_CLOSED"


class StewardshipError(Exception):
    """Typed fail-closed stewardship refusal (no mutation, no LLM)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Bounded validation helpers
# ---------------------------------------------------------------------------


def _require_bounded_str(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError(f"{label} must not contain leading/trailing whitespace: {value!r}")
    if not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_length}")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


def _require_logical_ref(value: Any, label: str, *, max_length: int = MAX_IDENTITY_REF_LENGTH) -> str:
    return require_logical_ref(value, label, max_length=max_length)


def _bounded_logical_refs(value: Any, label: str, *, max_entries: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{label} must be a tuple/list of logical refs")
    refs: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        ref = _require_logical_ref(raw, f"{label}[{index}]")
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    if len(refs) > max_entries:
        raise ValueError(f"{label} exceeds maximum {max_entries} entries")
    return tuple(sorted(refs))


def _is_commit_sha(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)


def _is_64_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _coerce_checkpoint_kind(value: Any) -> GovernanceCheckpointKind:
    if isinstance(value, GovernanceCheckpointKind):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return GovernanceCheckpointKind(value)
        except ValueError as exc:
            raise ValueError(f"unknown GovernanceCheckpointKind: {value!r}") from exc
    raise TypeError(f"kind must be GovernanceCheckpointKind or str, got {type(value).__name__}")


def _coerce_closure_phase(value: Any) -> ClosurePhase | None:
    if value is None:
        return None
    if isinstance(value, ClosurePhase):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return ClosurePhase(value)
        except ValueError as exc:
            raise ValueError(f"unknown ClosurePhase: {value!r}") from exc
    raise TypeError(f"closure_phase must be ClosurePhase/str/None, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# Trusted checkpoint facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustedManagedCommentFact:
    """One trusted observed managed-comment fact (never model-supplied text)."""

    role: str
    comment_id: str
    digest: str
    revision: str | None = None

    def __post_init__(self) -> None:
        if self.role not in MANAGED_ROLES:
            raise ValueError(f"role must be one of {sorted(MANAGED_ROLES)}, got {self.role!r}")
        object.__setattr__(self, "comment_id", _require_bounded_str(self.comment_id, "comment_id", max_length=64))
        object.__setattr__(self, "digest", _require_bounded_str(self.digest, "digest", max_length=128))
        if self.revision is not None:
            object.__setattr__(self, "revision", _require_bounded_str(self.revision, "revision", max_length=128))


@dataclass(frozen=True)
class MaterializationRequest:
    """Bounded materialization intent declared by the trusted checkpoint.

    There is no arbitrary mutation command here: only the typed operations the
    existing ``TrustedStewardFinalizer`` already supports, with the promotion
    target always derived from the trusted reviewed/accepted frontier.
    """

    promote_to_main: bool = False
    expected_old_ref: str | None = None
    expected_tree: str | None = None
    branch: str = "main"
    remote: str | None = None
    managed_comment_roles: tuple[str, ...] = ()
    review_projection: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "promote_to_main", _require_strict_bool(self.promote_to_main, "promote_to_main"))
        object.__setattr__(self, "review_projection", _require_strict_bool(self.review_projection, "review_projection"))
        if self.expected_old_ref is not None:
            object.__setattr__(
                self, "expected_old_ref", _require_bounded_str(self.expected_old_ref, "expected_old_ref", max_length=128)
            )
        if self.expected_tree is not None:
            object.__setattr__(
                self, "expected_tree", _require_bounded_str(self.expected_tree, "expected_tree", max_length=128)
            )
        branch = _require_bounded_str(self.branch, "branch", max_length=128)
        if "/" in branch or "\\" in branch or branch.startswith("-") or branch == "HEAD":
            raise ValueError(f"branch must be a simple branch name, got {branch!r}")
        object.__setattr__(self, "branch", branch)
        if self.remote is not None:
            object.__setattr__(self, "remote", _require_bounded_str(self.remote, "remote", max_length=64))
        if not isinstance(self.managed_comment_roles, (tuple, list)):
            raise TypeError("managed_comment_roles must be a tuple/list")
        roles: list[str] = []
        seen: set[str] = set()
        for index, raw in enumerate(self.managed_comment_roles):
            if raw not in MANAGED_ROLES:
                raise ValueError(
                    f"managed_comment_roles[{index}] must be one of {sorted(MANAGED_ROLES)}, got {raw!r}"
                )
            if raw in seen:
                raise ValueError(f"duplicate managed comment role: {raw!r}")
            seen.add(raw)
            roles.append(raw)
        if len(roles) > MAX_MANAGED_COMMENT_FACTS:
            raise ValueError(f"managed_comment_roles exceeds maximum {MAX_MANAGED_COMMENT_FACTS}")
        object.__setattr__(self, "managed_comment_roles", tuple(sorted(roles)))

    def wants_materialization(self) -> bool:
        return self.promote_to_main or bool(self.managed_comment_roles)


@dataclass(frozen=True)
class SemanticFactSet:
    """Trusted facts that mechanically indicate semantic judgment is needed.

    These are trusted governance-state facts (refs, never model narrative).
    Any non-empty fact set makes the checkpoint non-mechanically decidable and
    produces a bounded ``SemanticResidual`` instead of a fabricated judgment.
    """

    unresolved_defect_refs: tuple[str, ...] = ()
    conflicting_artifact_refs: tuple[str, ...] = ()
    ambiguous_governance_reason_refs: tuple[str, ...] = ()
    architecture_question_refs: tuple[str, ...] = ()
    plan_change_question_refs: tuple[str, ...] = ()
    review_evidence_refs: tuple[str, ...] = ()
    narrative_reconciliation_required: bool = False
    recorded_semantic_question_refs: tuple[str, ...] = ()
    declared_kind: SemanticResidualKind | None = None
    declared_reason: str | None = None

    def __post_init__(self) -> None:
        for label in (
            "unresolved_defect_refs",
            "conflicting_artifact_refs",
            "ambiguous_governance_reason_refs",
            "architecture_question_refs",
            "plan_change_question_refs",
            "review_evidence_refs",
            "recorded_semantic_question_refs",
        ):
            object.__setattr__(
                self, label, _bounded_logical_refs(getattr(self, label), label, max_entries=MAX_SEMANTIC_FACT_REFS)
            )
        object.__setattr__(
            self,
            "narrative_reconciliation_required",
            _require_strict_bool(self.narrative_reconciliation_required, "narrative_reconciliation_required"),
        )
        if self.declared_kind is not None and not isinstance(self.declared_kind, SemanticResidualKind):
            if isinstance(self.declared_kind, str) and type(self.declared_kind) is str:
                try:
                    object.__setattr__(self, "declared_kind", SemanticResidualKind(self.declared_kind))
                except ValueError as exc:
                    raise ValueError(f"unknown SemanticResidualKind: {self.declared_kind!r}") from exc
            else:
                raise TypeError("declared_kind must be SemanticResidualKind/str/None")
        if self.declared_reason is not None:
            object.__setattr__(
                self,
                "declared_reason",
                _require_bounded_str(self.declared_reason, "declared_reason", max_length=MAX_RESIDUAL_REASON_LENGTH),
            )

    def is_empty(self) -> bool:
        return not (
            self.unresolved_defect_refs
            or self.conflicting_artifact_refs
            or self.ambiguous_governance_reason_refs
            or self.architecture_question_refs
            or self.plan_change_question_refs
            or self.review_evidence_refs
            or self.narrative_reconciliation_required
            or self.recorded_semantic_question_refs
            or self.declared_kind is not None
        )


# ---------------------------------------------------------------------------
# Typed Governance checkpoint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StewardshipCheckpoint:
    """One typed in-process Governance checkpoint (trusted facts only).

    Exactly three conceptual kinds are supported — ``PLAN_INIT``,
    ``MILESTONE_CLOSE`` and ``PLAN_CLOSE``.  This is deliberately a bounded
    Python typed input, not an event bus, message broker, scheduler framework
    or workflow database: no generic event stream, no arbitrary mutation
    command, no ambient subscription surface.
    """

    checkpoint_id: str
    kind: GovernanceCheckpointKind
    project_id: str
    trusted_binding: TrustedProjectBinding
    trusted_plan: TrustedPlanIdentity
    plan_id: str | None = None
    milestone_ref: str | None = None
    closure_phase: ClosurePhase | None = None
    readiness: MilestoneClosureReadiness | None = None
    user_gate: TrustedUserGateState = field(
        default_factory=lambda: TrustedUserGateState(user_approval_satisfied=False)
    )
    materialization: MaterializationRequest = field(default_factory=MaterializationRequest)
    semantic_facts: SemanticFactSet = field(default_factory=SemanticFactSet)
    open_blocker_refs: tuple[str, ...] = ()
    plan_authority_ref: str | None = None
    all_milestones_closed: bool | None = None
    managed_comment_facts: tuple[TrustedManagedCommentFact, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "checkpoint_id", _require_bounded_str(self.checkpoint_id, "checkpoint_id", max_length=MAX_CHECKPOINT_ID_LENGTH)
        )
        object.__setattr__(self, "kind", _coerce_checkpoint_kind(self.kind))
        project_id = _require_bounded_str(self.project_id, "project_id", max_length=96)
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project_id must match the canonical project identity grammar")
        object.__setattr__(self, "project_id", project_id)
        if not isinstance(self.trusted_binding, TrustedProjectBinding):
            raise TypeError("trusted_binding must be TrustedProjectBinding")
        if self.trusted_binding.project_id != project_id:
            raise ValueError(
                "BINDING_PROJECT_MISMATCH: trusted binding belongs to another project "
                f"({self.trusted_binding.project_id!r} != {project_id!r})"
            )
        if not isinstance(self.trusted_plan, TrustedPlanIdentity):
            raise TypeError("trusted_plan must be TrustedPlanIdentity")
        if self.plan_id is not None and not is_plan_id(self.plan_id):
            raise ValueError("INVALID_PLAN_ID: plan_id must be one canonical internal Plan ID")
        if self.milestone_ref is not None:
            object.__setattr__(
                self, "milestone_ref", _require_bounded_str(self.milestone_ref, "milestone_ref", max_length=MAX_IDENTITY_REF_LENGTH)
            )
        object.__setattr__(self, "closure_phase", _coerce_closure_phase(self.closure_phase))
        if self.readiness is not None and not isinstance(self.readiness, MilestoneClosureReadiness):
            raise TypeError("readiness must be MilestoneClosureReadiness or None")
        if not isinstance(self.user_gate, TrustedUserGateState):
            raise TypeError("user_gate must be TrustedUserGateState")
        if not isinstance(self.materialization, MaterializationRequest):
            raise TypeError("materialization must be MaterializationRequest")
        if not isinstance(self.semantic_facts, SemanticFactSet):
            raise TypeError("semantic_facts must be SemanticFactSet")
        object.__setattr__(
            self, "open_blocker_refs", _bounded_logical_refs(self.open_blocker_refs, "open_blocker_refs", max_entries=MAX_OPEN_BLOCKER_REFS)
        )
        if self.plan_authority_ref is not None:
            object.__setattr__(
                self, "plan_authority_ref", _require_logical_ref(self.plan_authority_ref, "plan_authority_ref")
            )
        if self.all_milestones_closed is not None:
            object.__setattr__(
                self, "all_milestones_closed", _require_strict_bool(self.all_milestones_closed, "all_milestones_closed")
            )
        if not isinstance(self.managed_comment_facts, (tuple, list)):
            raise TypeError("managed_comment_facts must be a tuple/list")
        facts: list[TrustedManagedCommentFact] = []
        seen: set[str] = set()
        for index, fact in enumerate(self.managed_comment_facts):
            if not isinstance(fact, TrustedManagedCommentFact):
                raise TypeError(f"managed_comment_facts[{index}] must be TrustedManagedCommentFact")
            if fact.role in seen:
                raise ValueError(f"duplicate managed comment fact role: {fact.role!r}")
            seen.add(fact.role)
            facts.append(fact)
        if len(facts) > MAX_MANAGED_COMMENT_FACTS:
            raise ValueError(f"managed_comment_facts exceeds maximum {MAX_MANAGED_COMMENT_FACTS}")
        object.__setattr__(self, "managed_comment_facts", tuple(sorted(facts, key=lambda f: f.role)))

        kind = self.kind
        if kind is GovernanceCheckpointKind.PLAN_INIT:
            if self.readiness is not None:
                raise ValueError("PLAN_INIT checkpoint must not carry closure readiness")
            if self.closure_phase is not None:
                raise ValueError("PLAN_INIT checkpoint must not carry a closure phase")
            if self.all_milestones_closed is not None:
                raise ValueError("PLAN_INIT checkpoint must not carry plan-close facts")
        elif kind is GovernanceCheckpointKind.MILESTONE_CLOSE:
            if self.readiness is None:
                raise ValueError("MILESTONE_CLOSE checkpoint requires closure readiness")
            if self.closure_phase is None:
                raise ValueError("MILESTONE_CLOSE checkpoint requires a closure phase")
            if self.milestone_ref is None:
                raise ValueError("MILESTONE_CLOSE checkpoint requires a milestone ref")
            if self.all_milestones_closed is not None:
                raise ValueError("MILESTONE_CLOSE checkpoint must not carry plan-close facts")
        else:
            if self.all_milestones_closed is None:
                raise ValueError("PLAN_CLOSE checkpoint requires the all-milestones-closed fact")

    def managed_comment_fact_for(self, role: str) -> TrustedManagedCommentFact | None:
        for fact in self.managed_comment_facts:
            if fact.role == role:
                return fact
        return None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "kind": self.kind.value,
            "project_id": self.project_id,
            "plan_ref": self.trusted_plan.plan_ref,
            "plan_id": self.plan_id,
            "milestone_ref": self.milestone_ref,
            "closure_phase": self.closure_phase.value if self.closure_phase is not None else None,
            "readiness_digest": self.readiness.digest if self.readiness is not None else None,
            "user_approval_satisfied": self.user_gate.user_approval_satisfied,
            "materialization": {
                "promote_to_main": self.materialization.promote_to_main,
                "expected_old_ref": self.materialization.expected_old_ref,
                "expected_tree": self.materialization.expected_tree,
                "branch": self.materialization.branch,
                "remote": self.materialization.remote,
                "managed_comment_roles": list(self.materialization.managed_comment_roles),
                "review_projection": self.materialization.review_projection,
            },
            "semantic_facts_empty": self.semantic_facts.is_empty(),
            "open_blocker_refs": list(self.open_blocker_refs),
            "plan_authority_ref": self.plan_authority_ref,
            "all_milestones_closed": self.all_milestones_closed,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Semantic residual (bounded, explicitly non-authoritative)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticResidual:
    """Bounded typed residual: why a semantic Steward is required.

    Carries a residual kind, a bounded mechanical reason and only the
    bounded trusted refs needed for progressive hydration.  It never
    eagerly ships full Plan/Architecture/defect/Decision/history context.
    """

    kind: SemanticResidualKind
    reason: str
    checkpoint_id: str
    required_input_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SemanticResidualKind):
            if isinstance(self.kind, str) and type(self.kind) is str:
                try:
                    object.__setattr__(self, "kind", SemanticResidualKind(self.kind))
                except ValueError as exc:
                    raise ValueError(f"unknown SemanticResidualKind: {self.kind!r}") from exc
            else:
                raise TypeError("kind must be SemanticResidualKind/str")
        object.__setattr__(
            self, "reason", _require_bounded_str(self.reason, "reason", max_length=MAX_RESIDUAL_REASON_LENGTH)
        )
        object.__setattr__(
            self,
            "checkpoint_id",
            _require_bounded_str(self.checkpoint_id, "checkpoint_id", max_length=MAX_CHECKPOINT_ID_LENGTH),
        )
        object.__setattr__(
            self,
            "required_input_refs",
            _bounded_logical_refs(self.required_input_refs, "required_input_refs", max_entries=MAX_REQUIRED_INPUT_REFS),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "checkpoint_id": self.checkpoint_id,
            "required_input_refs": list(self.required_input_refs),
            "IS_AUTHORITY": False,
            "SEMANTIC_RESIDUAL_IS_AUTHORITY": False,
        }


# ---------------------------------------------------------------------------
# Deterministic evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StewardshipEvaluation:
    """Deterministic evaluation result for one typed checkpoint."""

    checkpoint_id: str
    kind: GovernanceCheckpointKind
    disposition: StewardshipDisposition
    mechanical_checks: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    residual: SemanticResidual | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, StewardshipDisposition):
            if isinstance(self.disposition, str) and type(self.disposition) is str:
                object.__setattr__(self, "disposition", StewardshipDisposition(self.disposition))
            else:
                raise TypeError("disposition must be StewardshipDisposition")
        if self.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED and self.residual is None:
            raise ValueError("SEMANTIC_RESIDUAL_REQUIRED requires a SemanticResidual")
        if self.disposition is StewardshipDisposition.DETERMINISTIC_BLOCKED and not self.blocking_reasons:
            raise ValueError("DETERMINISTIC_BLOCKED requires blocking reasons")
        if self.residual is not None and not isinstance(self.residual, SemanticResidual):
            raise TypeError("residual must be SemanticResidual or None")
        object.__setattr__(self, "mechanical_checks", tuple(sorted(dict.fromkeys(self.mechanical_checks or ()))))
        object.__setattr__(self, "blocking_reasons", tuple(sorted(dict.fromkeys(self.blocking_reasons or ()))))

    @property
    def requires_semantic_steward(self) -> bool:
        return self.disposition is StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED

    @property
    def is_fully_deterministic(self) -> bool:
        return self.disposition in (
            StewardshipDisposition.DETERMINISTIC_FINALIZABLE,
            StewardshipDisposition.DETERMINISTIC_SATISFIED,
            StewardshipDisposition.DETERMINISTIC_BLOCKED,
        )

    @property
    def requires_task_main_report(self) -> bool:
        return self.disposition in (
            StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED,
            StewardshipDisposition.DETERMINISTIC_BLOCKED,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "kind": self.kind.value,
            "disposition": self.disposition.value,
            "mechanical_checks": list(self.mechanical_checks),
            "blocking_reasons": list(self.blocking_reasons),
            "residual": self.residual.to_dict() if self.residual is not None else None,
            "LLM_INVOKED": False,
        }

    def evaluation_id(self) -> str:
        digest = hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()
        return f"steweval-{digest[:32]}"


def _classify_semantic_residual(checkpoint: StewardshipCheckpoint) -> SemanticResidual | None:
    facts = checkpoint.semantic_facts
    if facts.is_empty():
        return None

    def pick(
        kind: SemanticResidualKind,
        refs: tuple[str, ...],
        description: str,
    ) -> SemanticResidual:
        if facts.declared_kind is not None:
            kind = facts.declared_kind
        return SemanticResidual(
            kind=kind,
            reason=facts.declared_reason or description,
            checkpoint_id=checkpoint.checkpoint_id,
            required_input_refs=refs,
        )

    if facts.declared_kind is not None:
        all_refs = tuple(
            sorted(
                set(
                    facts.unresolved_defect_refs
                    + facts.conflicting_artifact_refs
                    + facts.ambiguous_governance_reason_refs
                    + facts.architecture_question_refs
                    + facts.plan_change_question_refs
                    + facts.review_evidence_refs
                    + facts.recorded_semantic_question_refs
                )
            )
        )
        return pick(
            facts.declared_kind,
            all_refs,
            f"trusted governance state declared semantic residual kind {facts.declared_kind.value}",
        )
    if facts.conflicting_artifact_refs:
        return pick(
            SemanticResidualKind.CONFLICTING_SEMANTIC_ARTIFACTS,
            facts.conflicting_artifact_refs,
            f"conflicting semantic artifacts require reconciliation ({len(facts.conflicting_artifact_refs)} refs)",
        )
    if facts.unresolved_defect_refs:
        return pick(
            SemanticResidualKind.UNRESOLVED_DEFECT_DISPOSITION,
            facts.unresolved_defect_refs,
            f"unresolved defect disposition requires judgment ({len(facts.unresolved_defect_refs)} refs)",
        )
    if facts.architecture_question_refs:
        return pick(
            SemanticResidualKind.ARCHITECTURE_INTERPRETATION,
            facts.architecture_question_refs,
            f"architecture interpretation required ({len(facts.architecture_question_refs)} refs)",
        )
    if facts.plan_change_question_refs:
        return pick(
            SemanticResidualKind.DECISION_PLAN_CHANGE_INTERPRETATION,
            facts.plan_change_question_refs,
            f"Decision/Plan change interpretation required ({len(facts.plan_change_question_refs)} refs)",
        )
    if facts.review_evidence_refs:
        return pick(
            SemanticResidualKind.REVIEW_EVIDENCE_INTERPRETATION,
            facts.review_evidence_refs,
            f"review evidence requires non-mechanical interpretation ({len(facts.review_evidence_refs)} refs)",
        )
    if facts.narrative_reconciliation_required:
        return SemanticResidual(
            kind=SemanticResidualKind.NARRATIVE_RECONCILIATION,
            reason="narrative reconciliation requiring judgment is recorded",
            checkpoint_id=checkpoint.checkpoint_id,
            required_input_refs=(),
        )
    if facts.recorded_semantic_question_refs:
        return pick(
            SemanticResidualKind.RECORDED_SEMANTIC_QUESTION,
            facts.recorded_semantic_question_refs,
            f"recorded semantic question is unresolved ({len(facts.recorded_semantic_question_refs)} refs)",
        )
    if facts.ambiguous_governance_reason_refs:
        return pick(
            SemanticResidualKind.AMBIGUOUS_GOVERNANCE_REASON,
            facts.ambiguous_governance_reason_refs,
            f"ambiguous governance reason requires interpretation ({len(facts.ambiguous_governance_reason_refs)} refs)",
        )
    return None


def _materialization_violations(
    checkpoint: StewardshipCheckpoint,
    *,
    readiness: MilestoneClosureReadiness | None,
) -> list[str]:
    """Mechanical scope checks for the requested materialization.

    The promotion target is never caller-specified: it derives from the
    trusted reviewed/accepted frontier.  This routine only verifies that the
    trusted observations needed by the existing finalizer contract exist.
    """
    request = checkpoint.materialization
    if not request.wants_materialization():
        return []
    violations: list[str] = []
    if readiness is None:
        violations.append(
            f"{BLOCK_MISSING_REQUIRED_FACTS}: materialization requires trusted closure readiness/reviewed frontier"
        )
        return violations
    reviewed = readiness.reviewed_frontier_ref.ref
    if request.promote_to_main:
        if not _is_commit_sha(reviewed):
            violations.append(
                f"{BLOCK_INVALID_FRONTIER_REF}: reviewed frontier {reviewed!r} is not a 40-char commit SHA for promotion"
            )
        if request.expected_old_ref is None:
            violations.append(
                f"{BLOCK_MISSING_EXPECTED_OLD_REF}: promotion requires the trusted observed branch ref for CAS"
            )
        elif not _is_commit_sha(request.expected_old_ref):
            violations.append(
                f"{BLOCK_INVALID_EXPECTED_OLD_REF}: expected_old_ref must be a 40-char lower hex commit SHA"
            )
    for role in request.managed_comment_roles:
        fact = checkpoint.managed_comment_fact_for(role)
        if fact is None:
            violations.append(f"{BLOCK_MISSING_MANAGED_COMMENT_FACT}: no trusted observed fact for role {role!r}")
            continue
        trusted_comment_id = checkpoint.trusted_plan.comment_id_for(role)
        if trusted_comment_id is None:
            violations.append(
                f"{BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH}: role {role!r} has no trusted managed mapping"
            )
        elif fact.comment_id != trusted_comment_id:
            violations.append(
                f"{BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH}: observed comment {fact.comment_id!r} != trusted "
                f"{trusted_comment_id!r} for role {role!r}"
            )
        if not _is_64_hex(fact.digest):
            violations.append(
                f"{BLOCK_INVALID_MANAGED_COMMENT_DIGEST}: observed digest for role {role!r} must be 64 lowercase hex"
            )
    return violations


def evaluate_checkpoint(checkpoint: StewardshipCheckpoint) -> StewardshipEvaluation:
    """Run mechanical checks first; classify the checkpoint deterministically.

    Returns a bounded ``StewardshipEvaluation``.  No LLM is consulted, no
    mutation is attempted, no semantic field is fabricated.
    """
    if not isinstance(checkpoint, StewardshipCheckpoint):
        raise TypeError(f"checkpoint must be StewardshipCheckpoint, got {type(checkpoint).__name__}")

    checks: list[str] = []
    blocked: list[str] = []

    # -- 1. Identity / binding ------------------------------------------------
    if checkpoint.trusted_binding.project_id == checkpoint.project_id:
        checks.append(CHECK_IDENTITY_BINDING)
    else:
        blocked.append(
            f"{BLOCK_BINDING_PROJECT_MISMATCH}: binding project {checkpoint.trusted_binding.project_id!r} "
            f"!= checkpoint project {checkpoint.project_id!r}"
        )
    if checkpoint.plan_id is None or is_plan_id(checkpoint.plan_id):
        checks.append(CHECK_PLAN_IDENTITY)
    else:
        blocked.append(f"{BLOCK_INVALID_PLAN_ID}: {checkpoint.plan_id!r} is not a canonical internal Plan ID")

    # -- 2. Mechanically known blockers --------------------------------------
    if checkpoint.open_blocker_refs:
        blocked.append(f"{BLOCK_OPEN_BLOCKERS}: {list(checkpoint.open_blocker_refs)}")
    else:
        checks.append(CHECK_NO_OPEN_BLOCKERS)

    # -- 3. Semantic residual classification (trusted facts only) ------------
    residual = _classify_semantic_residual(checkpoint)
    if residual is None:
        checks.append(CHECK_SEMANTIC_FACTS_NONE)

    # Hard mechanical violations fail closed before any semantic dispatch.
    if blocked:
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=checkpoint.kind,
            disposition=StewardshipDisposition.DETERMINISTIC_BLOCKED,
            mechanical_checks=tuple(checks),
            blocking_reasons=tuple(sorted(set(blocked))),
        )

    # -- 4. Kind-specific mechanical gates -----------------------------------
    kind = checkpoint.kind
    readiness = checkpoint.readiness

    if kind is GovernanceCheckpointKind.PLAN_INIT:
        if checkpoint.plan_authority_ref is None:
            blocked.append(
                f"{BLOCK_MISSING_PLAN_AUTHORITY}: PLAN_INIT requires a bound Plan authority ref"
            )
        else:
            checks.append(CHECK_PLAN_AUTHORITY_BINDING)
        if checkpoint.materialization.wants_materialization():
            blocked.append(
                f"{BLOCK_MATERIALIZATION_SCOPE_VIOLATION}: PLAN_INIT declares no close/materialization scope"
            )
        if blocked:
            return StewardshipEvaluation(
                checkpoint_id=checkpoint.checkpoint_id,
                kind=kind,
                disposition=StewardshipDisposition.DETERMINISTIC_BLOCKED,
                mechanical_checks=tuple(checks),
                blocking_reasons=tuple(sorted(set(blocked))),
            )
        if residual is not None:
            return StewardshipEvaluation(
                checkpoint_id=checkpoint.checkpoint_id,
                kind=kind,
                disposition=StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED,
                mechanical_checks=tuple(checks),
                residual=residual,
            )
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=kind,
            disposition=StewardshipDisposition.DETERMINISTIC_SATISFIED,
            mechanical_checks=tuple(checks),
        )

    # MILESTONE_CLOSE / PLAN_CLOSE share the closure machinery.
    if kind is GovernanceCheckpointKind.PLAN_CLOSE:
        if checkpoint.all_milestones_closed is not True:
            blocked.append(
                f"{BLOCK_PLAN_MILESTONES_NOT_CLOSED}: plan close requires all Milestones closed"
            )
        else:
            checks.append(CHECK_PLAN_CLOSE_ALL_MILESTONES)

    if readiness is None:
        # Only a PLAN_CLOSE that declares no materialization is mechanically
        # satisfiable without trusted review/readiness evidence.
        if checkpoint.materialization.wants_materialization():
            blocked.append(
                f"{BLOCK_MISSING_REQUIRED_FACTS}: materialization requires trusted closure readiness"
            )
        if blocked:
            return StewardshipEvaluation(
                checkpoint_id=checkpoint.checkpoint_id,
                kind=kind,
                disposition=StewardshipDisposition.DETERMINISTIC_BLOCKED,
                mechanical_checks=tuple(checks),
                blocking_reasons=tuple(sorted(set(blocked))),
            )
        if residual is not None:
            return StewardshipEvaluation(
                checkpoint_id=checkpoint.checkpoint_id,
                kind=kind,
                disposition=StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED,
                mechanical_checks=tuple(checks),
                residual=residual,
            )
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=kind,
            disposition=StewardshipDisposition.DETERMINISTIC_SATISFIED,
            mechanical_checks=tuple(checks),
        )

    # Readiness present: verify milestone identity agreement across trusted facts.
    readiness_milestone = readiness.milestone_ref.ref
    checks.append(CHECK_READINESS_FACT)
    if readiness_milestone != checkpoint.trusted_plan.milestone_ref or (
        checkpoint.milestone_ref is not None and checkpoint.milestone_ref != readiness_milestone
    ):
        blocked.append(
            f"{BLOCK_MILESTONE_IDENTITY_MISMATCH}: readiness {readiness_milestone!r} vs checkpoint "
            f"{checkpoint.milestone_ref!r} vs trusted plan {checkpoint.trusted_plan.milestone_ref!r}"
        )
    else:
        checks.append(CHECK_MILESTONE_IDENTITY)

    if not readiness.ready_for_project_steward:
        blocked.append(
            f"{BLOCK_READINESS_NOT_READY}: closure readiness is not ready ({list(readiness.blocking_reasons)})"
        )
    else:
        checks.append(CHECK_READINESS_CLEAR)

    phase = checkpoint.closure_phase
    if phase is ClosurePhase.ACCEPTED_CLOSURE:
        # The deterministic evaluator may only observe a durable existing
        # approval fact.  It can never mint one.
        if checkpoint.user_gate.user_approval_satisfied is not True:
            blocked.append(
                f"{BLOCK_USER_GATE_REQUIRED}: accepted closure denied without durable user approval"
            )
        else:
            checks.append(CHECK_USER_GATE_FACT)

    violations = _materialization_violations(checkpoint, readiness=readiness)
    if violations:
        blocked.extend(violations)
    else:
        checks.append(CHECK_MATERIALIZATION_SCOPE)

    if blocked:
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=kind,
            disposition=StewardshipDisposition.DETERMINISTIC_BLOCKED,
            mechanical_checks=tuple(checks),
            blocking_reasons=tuple(sorted(set(blocked))),
        )
    if residual is not None:
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=kind,
            disposition=StewardshipDisposition.SEMANTIC_RESIDUAL_REQUIRED,
            mechanical_checks=tuple(checks),
            residual=residual,
        )
    if phase is None:
        # Readiness without a closure phase: deterministic bookkeeping only.
        return StewardshipEvaluation(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=kind,
            disposition=StewardshipDisposition.DETERMINISTIC_SATISFIED,
            mechanical_checks=tuple(checks),
        )
    return StewardshipEvaluation(
        checkpoint_id=checkpoint.checkpoint_id,
        kind=kind,
        disposition=StewardshipDisposition.DETERMINISTIC_FINALIZABLE,
        mechanical_checks=tuple(checks),
    )


# ---------------------------------------------------------------------------
# Mechanically derived StewardResult / FinalizerInput
# ---------------------------------------------------------------------------


def proof_marker_for(checkpoint: StewardshipCheckpoint, role: str) -> str:
    """Deterministic mechanical proof marker for one managed role."""
    if role not in MANAGED_ROLES:
        raise ValueError(f"role must be one of {sorted(MANAGED_ROLES)}, got {role!r}")
    return f"stewardship-proof:{checkpoint.checkpoint_id}:{role}"


def mechanical_evidence_refs(checkpoint: StewardshipCheckpoint) -> tuple[str, ...]:
    """Bounded mechanical evidence refs derived only from trusted facts."""
    refs: list[str] = [f"checkpoint:{checkpoint.checkpoint_id}"]
    readiness = checkpoint.readiness
    if readiness is not None:
        refs.append(f"readiness:{readiness.digest}")
    if checkpoint.plan_authority_ref is not None:
        refs.append(checkpoint.plan_authority_ref)
    for role in checkpoint.materialization.managed_comment_roles:
        refs.append(proof_marker_for(checkpoint, role))
    return tuple(sorted(dict.fromkeys(refs)))[:MAX_REQUIRED_INPUT_REFS]


def mechanical_proof_content(checkpoint: StewardshipCheckpoint) -> str:
    """Deterministic proof content composed only of trusted mechanical values."""
    readiness = checkpoint.readiness
    if readiness is None:
        raise StewardshipError("MISSING_REQUIRED_FACTS", "mechanical proof content requires closure readiness")
    reviewed = readiness.reviewed_frontier_ref.ref
    accepted = reviewed if checkpoint.closure_phase is ClosurePhase.ACCEPTED_CLOSURE else None
    payload = {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_kind": checkpoint.kind.value,
        "project_id": checkpoint.project_id,
        "plan_ref": checkpoint.trusted_plan.plan_ref,
        "milestone_ref": readiness.milestone_ref.ref,
        "verdict": StewardClosureVerdict.GOVERNANCE_SYNCED.value,
        "reviewed_frontier_ref": reviewed,
        "accepted_frontier_ref": accepted,
        "readiness_digest": readiness.digest,
        "mechanical": True,
    }
    return canonical_json(payload)


def produce_deterministic_steward_result(checkpoint: StewardshipCheckpoint) -> StewardResult:
    """Mechanically derive a valid bounded ``StewardResult`` from trusted facts.

    Allowed only when every populated semantic field is mechanically
    derivable.  The producer never invents architecture interpretation,
    unresolved defect disposition, Plan semantics, review meaning, user
    approval or an accepted frontier.
    """
    evaluation = evaluate_checkpoint(checkpoint)
    if evaluation.disposition is not StewardshipDisposition.DETERMINISTIC_FINALIZABLE:
        raise StewardshipError(
            "NOT_DETERMINISTICALLY_FINALIZABLE",
            f"checkpoint {checkpoint.checkpoint_id} is {evaluation.disposition.value}, "
            "no mechanical StewardResult may be fabricated",
        )
    readiness = checkpoint.readiness
    assert readiness is not None  # guaranteed by DETERMINISTIC_FINALIZABLE
    reviewed = readiness.reviewed_frontier_ref.ref
    accepted: str | None = None
    if checkpoint.closure_phase is ClosurePhase.ACCEPTED_CLOSURE:
        # Derived from the trusted reviewed frontier; user approval was only
        # observed as an existing durable fact by the evaluator.
        accepted = reviewed
    return StewardResult(
        milestone_ref=readiness.milestone_ref.ref,
        reviewed_frontier_ref=reviewed,
        verdict=StewardClosureVerdict.GOVERNANCE_SYNCED,
        accepted_frontier_ref=accepted,
        governance_evidence_refs=mechanical_evidence_refs(checkpoint),
        blocking_reasons=(),
        user_approval_set=False,
    )


def build_materialization_intent(checkpoint: StewardshipCheckpoint) -> MaterializationIntent:
    """Build the bounded typed materialization intent from trusted facts."""
    request = checkpoint.materialization
    readiness = checkpoint.readiness
    git_intent: GitPromotionIntent | None = None
    if request.promote_to_main:
        if readiness is None:
            raise StewardshipError("MISSING_REQUIRED_FACTS", "promotion requires trusted readiness")
        reviewed = readiness.reviewed_frontier_ref.ref
        target = reviewed  # accepted frontier (when present) equals reviewed
        git_intent = GitPromotionIntent(
            branch=request.branch,
            expected_old_ref=request.expected_old_ref,
            target_ref=target,
            remote=request.remote,
            expected_tree=request.expected_tree,
        )
    proof_content = mechanical_proof_content(checkpoint) if request.managed_comment_roles else ""
    updates: list[GitHubUpdateIntent] = []
    for role in request.managed_comment_roles:
        fact = checkpoint.managed_comment_fact_for(role)
        if fact is None:
            raise StewardshipError("MISSING_MANAGED_COMMENT_FACT", f"no trusted observed fact for role {role!r}")
        updates.append(
            GitHubUpdateIntent(
                role=role,
                comment_id=fact.comment_id,
                expected_digest=fact.digest,
                expected_revision=fact.revision,
                proof_marker=proof_marker_for(checkpoint, role),
                proof_content=proof_content,
            )
        )
    return MaterializationIntent(
        git_promotion=git_intent,
        github_updates=tuple(updates),
        review_projection=request.review_projection,
    )


def build_finalizer_input(checkpoint: StewardshipCheckpoint) -> FinalizerInput:
    """Compose the existing ``FinalizerInput`` (StewardResult + trusted facts)."""
    readiness = checkpoint.readiness
    if readiness is None or checkpoint.closure_phase is None:
        raise StewardshipError(
            "MISSING_REQUIRED_FACTS", "finalizer input requires trusted readiness and closure phase"
        )
    return FinalizerInput(
        steward_result=produce_deterministic_steward_result(checkpoint),
        readiness=readiness,
        trusted_binding=checkpoint.trusted_binding,
        trusted_plan=checkpoint.trusted_plan,
        user_gate=checkpoint.user_gate,
        intent=build_materialization_intent(checkpoint),
        closure_phase=checkpoint.closure_phase,
    )


# ---------------------------------------------------------------------------
# Semantic residual routing (existing Project Steward dispatch seam)
# ---------------------------------------------------------------------------


def build_semantic_steward_handoff(
    checkpoint: StewardshipCheckpoint,
    residual: SemanticResidual,
) -> TaskHandoff:
    """Route a semantic residual through the existing Project Steward seam.

    Reuses the existing bounded dispatch builders only (no second transport):
    Mode B closure dispatch when closure readiness is ready, otherwise the
    bounded Mode A ProjectState request.  Actual invocation remains owned by
    the existing coordinator/task-main path — this function performs no LLM
    dispatch itself.
    """
    if not isinstance(checkpoint, StewardshipCheckpoint):
        raise TypeError("checkpoint must be StewardshipCheckpoint")
    if not isinstance(residual, SemanticResidual):
        raise TypeError("residual must be SemanticResidual")
    if residual.checkpoint_id != checkpoint.checkpoint_id:
        raise ValueError("residual does not belong to this checkpoint")
    objective = (
        f"Resolve governance checkpoint {checkpoint.checkpoint_id} ({checkpoint.kind.value}) "
        f"semantic residual {residual.kind.value}"
    )[:MAX_HANDOFF_OBJECTIVE_LENGTH]
    scope_bits = [residual.reason]
    if residual.required_input_refs:
        scope_bits.append("bounded refs: " + ", ".join(residual.required_input_refs))
    bounded_scope = ("; ".join(scope_bits))[:MAX_HANDOFF_SCOPE_LENGTH]
    # Canonical TaskHandoff.project_ref is the raw canonical project_id (the
    # same representation the normal governed Worker handoffs use); the
    # projection-style ``project:<id>`` form belongs to Governance
    # semantic/projection refs, not to TaskHandoff transport identity.
    project_ref = SemanticReference(ref=checkpoint.project_id)
    plan_ref = SemanticReference(ref=checkpoint.trusted_plan.plan_ref)
    readiness = checkpoint.readiness
    if readiness is not None and readiness.ready_for_project_steward:
        return build_mode_b_handoff(
            readiness=readiness,
            objective=objective,
            bounded_scope=bounded_scope,
            project_ref=project_ref,
            plan_ref=plan_ref,
        )
    milestone_ref = (
        SemanticReference(ref=checkpoint.milestone_ref) if checkpoint.milestone_ref is not None else None
    )
    return build_mode_a_handoff(
        objective=objective,
        bounded_scope=bounded_scope,
        project_ref=project_ref,
        plan_ref=plan_ref,
        milestone_ref=milestone_ref,
    )


# ---------------------------------------------------------------------------
# Orchestration outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StewardshipOutcome:
    """Compact bounded outcome of one checkpoint run.

    Report-by-exception: normal deterministic success carries no task-main
    ceremony; only semantic residual, mechanical block, Human Gate, finalizer
    failure or partial failure surface to task-main (finalizer failures raise
    the existing typed ``FinalizerError``).
    """

    checkpoint_id: str
    kind: GovernanceCheckpointKind
    disposition: StewardshipDisposition
    evaluation: StewardshipEvaluation
    receipt: MaterializationReceipt | None = None
    steward_result: StewardResult | None = None
    materialization_intent: MaterializationIntent | None = None
    semantic_handoff: TaskHandoff | None = None
    context_route_projection_id: str | None = None
    semantic_dispatch_invoked: bool = False
    llm_invoked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, StewardshipEvaluation):
            raise TypeError("evaluation must be StewardshipEvaluation")
        if self.evaluation.disposition is not self.disposition:
            raise ValueError("outcome disposition must match evaluation disposition")
        if self.receipt is not None and not isinstance(self.receipt, MaterializationReceipt):
            raise TypeError("receipt must be MaterializationReceipt or None")
        if self.llm_invoked:
            raise ValueError("stewardship engine never invokes an LLM")

    @property
    def requires_task_main_report(self) -> bool:
        return self.evaluation.requires_task_main_report

    @property
    def is_normal_success(self) -> bool:
        return self.disposition in (
            StewardshipDisposition.DETERMINISTIC_FINALIZABLE,
            StewardshipDisposition.DETERMINISTIC_SATISFIED,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "checkpoint_id": self.checkpoint_id,
            "kind": self.kind.value,
            "disposition": self.disposition.value,
            "evaluation_id": self.evaluation.evaluation_id(),
            "blocking_reasons": list(self.evaluation.blocking_reasons),
            "residual": self.evaluation.residual.to_dict() if self.evaluation.residual is not None else None,
            "receipt_id": self.receipt.receipt_id if self.receipt is not None else None,
            "receipt_digest": self.receipt.receipt_digest if self.receipt is not None else None,
            "semantic_handoff_kind": (
                self.semantic_handoff.task_kind if self.semantic_handoff is not None else None
            ),
            "context_route_projection_id": self.context_route_projection_id,
            "semantic_dispatch_invoked": self.semantic_dispatch_invoked,
            "llm_invoked": self.llm_invoked,
            "requires_task_main_report": self.requires_task_main_report,
            "NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN": False,
        }
        return payload


def run_governance_checkpoint(
    checkpoint: StewardshipCheckpoint,
    *,
    finalizer: TrustedStewardFinalizer | None = None,
    semantic_dispatch: Callable[[TaskHandoff], Any] | None = None,
    context_route: ContextRoute | None = None,
) -> StewardshipOutcome:
    """Deterministic-first execution of one typed Governance checkpoint.

    * ``DETERMINISTIC_FINALIZABLE`` -> mechanically derived StewardResult via
      the existing ``TrustedStewardFinalizer`` (no LLM; finalizer failures
      raise the existing typed ``FinalizerError``).
    * ``DETERMINISTIC_SATISFIED`` -> trusted outcome with no materialization.
    * ``DETERMINISTIC_BLOCKED`` -> fail closed; surfaces to task-main by
      exception policy.
    * ``SEMANTIC_RESIDUAL_REQUIRED`` -> bounded residual routed through the
      existing Project Steward dispatch seam.  ``semantic_dispatch``, when
      supplied, is invoked with the existing ``TaskHandoff`` (the hook the
      existing coordinator/task-main path uses); no LLM is invoked here.

    ``context_route`` (W2) is an optional read-only progressive-hydration
    route; its projection id is recorded, never eagerly hydrated.
    """
    if not isinstance(checkpoint, StewardshipCheckpoint):
        raise TypeError(f"checkpoint must be StewardshipCheckpoint, got {type(checkpoint).__name__}")
    if context_route is not None and not isinstance(context_route, ContextRoute):
        raise TypeError("context_route must be a ContextRoute or None")

    evaluation = evaluate_checkpoint(checkpoint)
    route_id = context_route.projection_id() if context_route is not None else None

    if evaluation.disposition is StewardshipDisposition.DETERMINISTIC_FINALIZABLE:
        if finalizer is None:
            raise StewardshipError("FINALIZER_REQUIRED", "deterministic finalization requires the trusted finalizer")
        if not isinstance(finalizer, TrustedStewardFinalizer):
            raise TypeError("finalizer must be TrustedStewardFinalizer")
        finalizer_input = build_finalizer_input(checkpoint)
        receipt = finalizer.finalize(
            finalizer_input,
            caller_role="coordinator-internal",
            via_coordinator=True,
            has_closure_evidence=True,
        )
        return StewardshipOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=checkpoint.kind,
            disposition=evaluation.disposition,
            evaluation=evaluation,
            receipt=receipt,
            steward_result=finalizer_input.steward_result,
            materialization_intent=finalizer_input.intent,
            context_route_projection_id=route_id,
        )

    if evaluation.disposition in (
        StewardshipDisposition.DETERMINISTIC_SATISFIED,
        StewardshipDisposition.DETERMINISTIC_BLOCKED,
    ):
        return StewardshipOutcome(
            checkpoint_id=checkpoint.checkpoint_id,
            kind=checkpoint.kind,
            disposition=evaluation.disposition,
            evaluation=evaluation,
            context_route_projection_id=route_id,
        )

    # Semantic residual: bounded residual routed through the existing seam.
    residual = evaluation.residual
    assert residual is not None
    handoff = build_semantic_steward_handoff(checkpoint, residual)
    dispatch_invoked = False
    if semantic_dispatch is not None:
        if not callable(semantic_dispatch):
            raise TypeError("semantic_dispatch must be callable or None")
        semantic_dispatch(handoff)
        dispatch_invoked = True
    return StewardshipOutcome(
        checkpoint_id=checkpoint.checkpoint_id,
        kind=checkpoint.kind,
        disposition=evaluation.disposition,
        evaluation=evaluation,
        semantic_handoff=handoff,
        context_route_projection_id=route_id,
        semantic_dispatch_invoked=dispatch_invoked,
    )


__all__ = [
    "BLOCK_BINDING_PROJECT_MISMATCH",
    "BLOCK_INVALID_EXPECTED_OLD_REF",
    "BLOCK_INVALID_FRONTIER_REF",
    "BLOCK_INVALID_MANAGED_COMMENT_DIGEST",
    "BLOCK_INVALID_PLAN_ID",
    "BLOCK_MANAGED_COMMENT_IDENTITY_MISMATCH",
    "BLOCK_MATERIALIZATION_SCOPE_VIOLATION",
    "BLOCK_MILESTONE_IDENTITY_MISMATCH",
    "BLOCK_MISSING_EXPECTED_OLD_REF",
    "BLOCK_MISSING_MANAGED_COMMENT_FACT",
    "BLOCK_MISSING_PLAN_AUTHORITY",
    "BLOCK_MISSING_REQUIRED_FACTS",
    "BLOCK_OPEN_BLOCKERS",
    "BLOCK_PLAN_MILESTONES_NOT_CLOSED",
    "BLOCK_READINESS_NOT_READY",
    "BLOCK_USER_GATE_REQUIRED",
    "CHECKPOINT_DOES_NOT_IMPLY_LLM_INVOCATION",
    "CHECKPOINT_KINDS",
    "CHECKPOINT_MODEL_IMPLEMENTED",
    "CHECK_IDENTITY_BINDING",
    "CHECK_MATERIALIZATION_SCOPE",
    "CHECK_MILESTONE_IDENTITY",
    "CHECK_NO_OPEN_BLOCKERS",
    "CHECK_PLAN_AUTHORITY_BINDING",
    "CHECK_PLAN_CLOSE_ALL_MILESTONES",
    "CHECK_PLAN_IDENTITY",
    "CHECK_READINESS_CLEAR",
    "CHECK_READINESS_FACT",
    "CHECK_SEMANTIC_FACTS_NONE",
    "CHECK_USER_GATE_FACT",
    "CURRENT_PROJECT_STEWARD_ROLE_REMAINS_MIGRATION_COMPATIBILITY",
    "DETERMINISTIC_FIRST_GOVERNANCE",
    "DETERMINISTIC_PATH_INVOKES_LLM",
    "DETERMINISTIC_STEWARD_RESULT_IS_MECHANICALLY_DERIVED",
    "DETERMINISTIC_STEWARD_RESULT_PATH",
    "FABRICATED_SEMANTIC_JUDGMENT",
    "FAKE_STEWARD_RESULT",
    "FINALIZER_INPUT_IS_STEWARD_RESULT",
    "GENERIC_GOVERNANCE_WRITE_API",
    "GovernanceCheckpointKind",
    "LLM_PATH_REMAINS_AVAILABLE",
    "MAX_CHECKPOINT_ID_LENGTH",
    "MAX_MANAGED_COMMENT_FACTS",
    "MAX_REQUIRED_INPUT_REFS",
    "MAX_RESIDUAL_REASON_LENGTH",
    "MaterializationRequest",
    "NEW_EVENT_BUS_CREATED",
    "NEW_EXECUTION_STATE_MACHINE_CREATED",
    "NEW_PLUGIN_MARKETPLACE_CREATED",
    "NEW_SCHEDULER_CREATED",
    "NEW_WORKFLOW_DATABASE_CREATED",
    "NORMAL_STEWARDSHIP_SUCCESS_REPORT_TO_TASK_MAIN",
    "NO_FLAG_DAY_DELETION",
    "NO_LLM_DISPATCH_ON_DETERMINISTIC_PATH",
    "PROJECT_STEWARD_IS_NORMAL_WORKER",
    "PROJECT_STEWARDSHIP_IS_SUBSYSTEM_PLUGIN",
    "RAW_ARBITRARY_MUTATION_COMMAND_ACCEPTED",
    "SECOND_FINALIZATION_PROTOCOL",
    "SECOND_MUTATION_PROTOCOL",
    "SECOND_RESULT_TRANSPORT_CREATED",
    "SEMANTIC_RESIDUAL_MODEL_IMPLEMENTED",
    "STEWARDSHIP_CAN_EXCEED_STEWARD_RESULT_SCOPE",
    "STEWARDSHIP_CAN_INFER_USER_APPROVAL",
    "STEWARDSHIP_CAN_INVENT_ACCEPTED_FRONTIER",
    "STEWARDSHIP_CAN_SET_USER_APPROVAL",
    "STEWARDSHIP_IS_ACCEPTANCE_AUTHORITY",
    "STEWARDSHIP_IS_MUTATION_AUTHORITY",
    "STEWARDSHIP_REPORT_BY_EXCEPTION",
    "TRUSTED_STEWARD_FINALIZER_REUSED",
    "SemanticFactSet",
    "SemanticResidual",
    "SemanticResidualKind",
    "StewardshipCheckpoint",
    "StewardshipDisposition",
    "StewardshipError",
    "StewardshipEvaluation",
    "StewardshipOutcome",
    "TrustedManagedCommentFact",
    "build_finalizer_input",
    "build_materialization_intent",
    "build_semantic_steward_handoff",
    "evaluate_checkpoint",
    "mechanical_evidence_refs",
    "mechanical_proof_content",
    "produce_deterministic_steward_result",
    "proof_marker_for",
    "run_governance_checkpoint",
]
