"""Project Steward Mode A / Mode B dispatch runtime (M2/W2 runtime).

M1 frozen (see #44 M1/W3, M1/W4):

    STEWARD_MUTATION_ARCHITECTURE=OPTION_B
    NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=no
    MILESTONE_GOVERNANCE_SYNC_OWNER=project-steward
    PROJECT_STEWARD_IS_ACCEPTANCE_AUTHORITY=no
    PROJECT_STEWARD_CAN_SET_USER_APPROVAL=no
    PROJECT_STEWARD_CAN_INVENT_ACCEPTED_FRONTIER=no

Mode A (ProjectState):

    task-main -> bounded ProjectState request -> project-steward one-shot
    -> ProjectState payload/result -> durable result
    -> exact/logical task-main re-entry.

    ProjectState is compact: trusted project identity/binding, project
    resolution/existence, accepted frontier, Plan/Milestone state, open
    defect state/ref, repo/worktree continuity, relevant artifact refs,
    freshness/ambiguity status. No full Git/Issue history. Mode A never
    plans Work.

Mode B (closure dispatch), only after:

    all source-ready -> integrated Reviewer -> task-main reconciliation
    -> MilestoneClosureReadiness(ready_for_project_steward=yes)

    task-main -> project-steward one-shot closure request -> StewardResult
    -> task-main/future finalizer seam.

W2 implements dispatch + StewardResult seam only (OPTION_B): the steward
returns a typed StewardResult consumed by a server-side trusted finalizer
(M3 owns the production finalizer). The steward itself performs no
generic Git/GitHub mutation and holds no unrestricted shell.

Hard:

    PROJECT_STEWARD_MODE_A_DISPATCH_WIRED=yes
    PROJECT_STEWARD_MODE_B_DISPATCH_WIRED=yes
    PROJECT_STEWARD_PROJECT_STATE_MODE_PLANS_WORK=no
    PROJECT_STATE_RESULT_IS_COMPACT=yes
    PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION=no
    PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION=no
    PROJECT_STEWARD_UNRESTRICTED_SHELL=no
    FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE=no
    STEWARD_RESULT_SEAM_WIRED=yes

Reuses existing contracts only: TaskHandoff, MilestoneClosureReadiness,
RolePayloadRef kind seam (common envelope, no second transport).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Sequence

from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.roles import AgentWorkRole

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

PROJECT_STEWARD_MODE_A_DISPATCH_WIRED = True
PROJECT_STEWARD_MODE_B_DISPATCH_WIRED = True
PROJECT_STEWARD_PROJECT_STATE_MODE_PLANS_WORK = False
PROJECT_STATE_RESULT_IS_COMPACT = True
PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION = False
PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION = False
PROJECT_STEWARD_UNRESTRICTED_SHELL = False
PROJECT_STEWARD_CAN_SET_USER_APPROVAL = False
PROJECT_STEWARD_CAN_INVENT_ACCEPTED_FRONTIER = False
PROJECT_STEWARD_IS_ACCEPTANCE_AUTHORITY = False
FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE = False
STEWARD_RESULT_SEAM_WIRED = True
NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT = False
STEWARD_MUTATION_ARCHITECTURE = "OPTION_B"
SECOND_RESULT_TRANSPORT_CREATED = False


@unique
class StewardDispatchMode(str, Enum):
    MODE_A_PROJECT_STATE = "MODE_A_PROJECT_STATE"
    MODE_B_CLOSURE = "MODE_B_CLOSURE"


STEWARD_TASK_KIND_MODE_A = "project-state-request"
STEWARD_TASK_KIND_MODE_B = "milestone-closure-request"


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 1024) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    return stripped


def _optional_ref(value: Any, label: str) -> SemanticReference | None:
    if value is None:
        return None
    if isinstance(value, SemanticReference):
        return value
    if isinstance(value, str) and type(value) is str:
        return SemanticReference(ref=value.strip())
    raise TypeError(f"{label} must be SemanticReference/str/None, got {type(value).__name__}")


def build_mode_a_handoff(
    *,
    objective: str,
    bounded_scope: str,
    project_ref: SemanticReference | str | None = None,
    plan_ref: SemanticReference | str | None = None,
    milestone_ref: SemanticReference | str | None = None,
) -> TaskHandoff:
    """Build a bounded Mode A ProjectState TaskHandoff for project-steward.

    The steward inspects project identity/binding/continuity only; the
    handoff forbids planning Work (semantic_stop expectations deny it).
    """
    return TaskHandoff(
        work_role=AgentWorkRole.PROJECT_STEWARD,
        task_kind=STEWARD_TASK_KIND_MODE_A,
        objective=_require_non_empty_str(objective, "objective", max_length=4096),
        bounded_scope=_require_non_empty_str(bounded_scope, "bounded_scope", max_length=4096),
        validation_expectations=(
            "ProjectState payload covers identity/binding, existence, frontier, Plan/Milestone state, defects, continuity, artifacts, freshness",
        ),
        semantic_stop_expectations=(
            "stop after compact ProjectState payload; do not plan Work Items; do not mutate product source",
        ),
        project_ref=_optional_ref(project_ref, "project_ref"),
        plan_ref=_optional_ref(plan_ref, "plan_ref"),
        milestone_ref=_optional_ref(milestone_ref, "milestone_ref"),
    )


def build_mode_b_handoff(
    *,
    readiness: MilestoneClosureReadiness,
    objective: str,
    bounded_scope: str,
    project_ref: SemanticReference | str | None = None,
    plan_ref: SemanticReference | str | None = None,
) -> TaskHandoff:
    """Build a bounded Mode B closure TaskHandoff for project-steward.

    Gated: readiness.ready_for_project_steward must be True, i.e. all
    source-ready -> integrated Reviewer -> task-main reconciliation ->
    MilestoneClosureReadiness already held. Otherwise fail closed (Mode B
    never dispatches early).
    """
    if not isinstance(readiness, MilestoneClosureReadiness):
        raise TypeError(f"readiness must be MilestoneClosureReadiness, got {type(readiness).__name__}")
    if readiness.ready_for_project_steward is not True:
        raise ValueError(
            "Mode B closure dispatch denied: MilestoneClosureReadiness.ready_for_project_steward is not yes "
            f"(blocking: {list(readiness.blocking_reasons)})"
        )
    return TaskHandoff(
        work_role=AgentWorkRole.PROJECT_STEWARD,
        task_kind=STEWARD_TASK_KIND_MODE_B,
        objective=_require_non_empty_str(objective, "objective", max_length=4096),
        bounded_scope=_require_non_empty_str(bounded_scope, "bounded_scope", max_length=4096),
        validation_expectations=(
            f"StewardResult reconciles milestone {readiness.milestone_ref.ref} governance sync for frontier {readiness.reviewed_frontier_ref.ref}",
        ),
        semantic_stop_expectations=(
            "stop after StewardResult; do not invent accepted frontier; do not set user approval; no generic Git/GitHub mutation",
        ),
        project_ref=_optional_ref(project_ref, "project_ref"),
        plan_ref=_optional_ref(plan_ref, "plan_ref"),
        milestone_ref=readiness.milestone_ref,
    )


@dataclass(frozen=True)
class ProjectStatePayload:
    """Compact ProjectState body (Mode A result, bounded evidence).

    Covers exactly the frozen minimum: trusted identity/binding,
    resolution/existence, accepted frontier, Plan/Milestone state, open
    defect summary/ref, worktree continuity, artifact refs,
    freshness/ambiguity status. No full Git/Issue history, no source dumps.
    Integrates into the common envelope via kind project_state_evidence.
    """

    PAYLOAD_KIND = "project_state_evidence"
    MAX_ARTIFACT_REFS = 16
    MAX_DEFECT_REFS = 16

    project_id: str
    binding_ref: str
    existence: str
    accepted_frontier_ref: str | None = None
    plan_state: str | None = None
    milestone_state: str | None = None
    defect_refs: tuple[str, ...] = ()
    continuity: str | None = None
    artifact_refs: tuple[str, ...] = ()
    freshness: str | None = None
    ambiguity: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_non_empty_str(self.project_id, "project_id", max_length=96))
        object.__setattr__(self, "binding_ref", _require_non_empty_str(self.binding_ref, "binding_ref"))
        object.__setattr__(self, "existence", _require_non_empty_str(self.existence, "existence"))
        for label in ("accepted_frontier_ref", "plan_state", "milestone_state", "continuity", "freshness", "ambiguity"):
            val = getattr(self, label)
            if val is not None:
                object.__setattr__(self, label, _require_non_empty_str(val, label))
        defects = tuple(self.defect_refs)
        if len(defects) > self.MAX_DEFECT_REFS:
            raise ValueError(f"defect_refs count ({len(defects)}) exceeds maximum {self.MAX_DEFECT_REFS}")
        for ref in defects:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("defect_refs must contain non-empty strings")
        object.__setattr__(self, "defect_refs", defects)
        artifacts = tuple(self.artifact_refs)
        if len(artifacts) > self.MAX_ARTIFACT_REFS:
            raise ValueError(f"artifact_refs count ({len(artifacts)}) exceeds maximum {self.MAX_ARTIFACT_REFS}")
        for ref in artifacts:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("artifact_refs must contain non-empty strings")
        object.__setattr__(self, "artifact_refs", artifacts)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.PAYLOAD_KIND,
            "project_id": self.project_id,
            "binding_ref": self.binding_ref,
            "existence": self.existence,
            "defect_refs": list(self.defect_refs),
            "artifact_refs": list(self.artifact_refs),
        }
        for label in ("accepted_frontier_ref", "plan_state", "milestone_state", "continuity", "freshness", "ambiguity"):
            val = getattr(self, label)
            if val is not None:
                d[label] = val
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProjectStatePayload:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {
            "kind",
            "project_id",
            "binding_ref",
            "existence",
            "accepted_frontier_ref",
            "plan_state",
            "milestone_state",
            "defect_refs",
            "continuity",
            "artifact_refs",
            "freshness",
            "ambiguity",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in ProjectStatePayload: {sorted(extra)}")
        for req in ("project_id", "binding_ref", "existence"):
            if req not in data:
                raise ValueError(f"Missing required field in ProjectStatePayload: {req!r}")
        return cls(
            project_id=data["project_id"],
            binding_ref=data["binding_ref"],
            existence=data["existence"],
            accepted_frontier_ref=data.get("accepted_frontier_ref"),
            plan_state=data.get("plan_state"),
            milestone_state=data.get("milestone_state"),
            defect_refs=tuple(data.get("defect_refs") or ()),
            continuity=data.get("continuity"),
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            freshness=data.get("freshness"),
            ambiguity=data.get("ambiguity"),
        )


@unique
class StewardClosureVerdict(str, Enum):
    GOVERNANCE_SYNCED = "GOVERNANCE_SYNCED"
    GOVERNANCE_BLOCKED = "GOVERNANCE_BLOCKED"


@dataclass(frozen=True)
class StewardResult:
    """Typed trusted-finalizer seam (Mode B result, OPTION_B).

    The steward returns governance-sync judgment only: which Milestone,
    which reviewed frontier, whether governance sync holds, and bounded
    refs. The server-side trusted finalizer (M3) consumes this seam; the
    finalizer can never exceed the StewardResult scope, and the steward
    can never set user approval or invent an accepted frontier.
    """

    PAYLOAD_KIND = "steward_closure_evidence"
    MAX_EVIDENCE_REFS = 16

    milestone_ref: str
    reviewed_frontier_ref: str
    verdict: StewardClosureVerdict
    accepted_frontier_ref: str | None = None
    governance_evidence_refs: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    user_approval_set: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "milestone_ref", _require_non_empty_str(self.milestone_ref, "milestone_ref"))
        object.__setattr__(
            self, "reviewed_frontier_ref", _require_non_empty_str(self.reviewed_frontier_ref, "reviewed_frontier_ref")
        )
        if isinstance(self.verdict, StewardClosureVerdict):
            pass
        elif isinstance(self.verdict, str) and type(self.verdict) is str:
            try:
                object.__setattr__(self, "verdict", StewardClosureVerdict(self.verdict))
            except ValueError as exc:
                raise ValueError(f"Unknown StewardClosureVerdict: {self.verdict!r}") from exc
        else:
            raise TypeError(f"verdict must be StewardClosureVerdict or str, got {type(self.verdict).__name__}")
        if self.accepted_frontier_ref is not None:
            object.__setattr__(
                self, "accepted_frontier_ref", _require_non_empty_str(self.accepted_frontier_ref, "accepted_frontier_ref")
            )
            # PROJECT_STEWARD_CAN_INVENT_ACCEPTED_FRONTIER=no: the steward
            # may only carry forward the already-reviewed frontier; claiming
            # any other "accepted" frontier is inventing authority.
            if self.accepted_frontier_ref != self.reviewed_frontier_ref:
                raise ValueError(
                    "steward cannot invent an accepted frontier: accepted_frontier_ref must equal "
                    "the reviewed frontier established by closure readiness"
                )
        refs = tuple(self.governance_evidence_refs)
        if len(refs) > self.MAX_EVIDENCE_REFS:
            raise ValueError(f"governance_evidence_refs count ({len(refs)}) exceeds maximum {self.MAX_EVIDENCE_REFS}")
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("governance_evidence_refs must contain non-empty strings")
        object.__setattr__(self, "governance_evidence_refs", refs)
        reasons = tuple(self.blocking_reasons)
        for reason in reasons:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("blocking_reasons must contain non-empty strings")
        object.__setattr__(self, "blocking_reasons", reasons)
        if type(self.user_approval_set) is not bool:
            raise TypeError(f"user_approval_set must be bool, got {type(self.user_approval_set).__name__}")
        if self.user_approval_set:
            raise ValueError("PROJECT_STEWARD_CAN_SET_USER_APPROVAL=no: steward cannot set user approval")
        if self.verdict is StewardClosureVerdict.GOVERNANCE_BLOCKED and not self.blocking_reasons:
            raise ValueError("GOVERNANCE_BLOCKED requires blocking reasons")

    def assert_finalizer_scope(self, *, operation_refs: Sequence[str]) -> None:
        """The trusted finalizer can never exceed the StewardResult scope:
        every mechanical operation must be covered by the steward's
        governance evidence refs or the reviewed frontier itself."""
        allowed = set(self.governance_evidence_refs) | {self.reviewed_frontier_ref}
        for op in operation_refs:
            if op not in allowed:
                raise ValueError(f"finalizer operation {op!r} exceeds StewardResult scope (fail closed)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.PAYLOAD_KIND,
            "milestone_ref": self.milestone_ref,
            "reviewed_frontier_ref": self.reviewed_frontier_ref,
            "verdict": self.verdict.value,
            "accepted_frontier_ref": self.accepted_frontier_ref,
            "governance_evidence_refs": list(self.governance_evidence_refs),
            "blocking_reasons": list(self.blocking_reasons),
            "user_approval_set": self.user_approval_set,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StewardResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {
            "kind",
            "milestone_ref",
            "reviewed_frontier_ref",
            "verdict",
            "accepted_frontier_ref",
            "governance_evidence_refs",
            "blocking_reasons",
            "user_approval_set",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in StewardResult: {sorted(extra)}")
        for req in ("milestone_ref", "reviewed_frontier_ref", "verdict"):
            if req not in data:
                raise ValueError(f"Missing required field in StewardResult: {req!r}")
        return cls(
            milestone_ref=data["milestone_ref"],
            reviewed_frontier_ref=data["reviewed_frontier_ref"],
            verdict=data["verdict"],
            accepted_frontier_ref=data.get("accepted_frontier_ref"),
            governance_evidence_refs=tuple(data.get("governance_evidence_refs") or ()),
            blocking_reasons=tuple(data.get("blocking_reasons") or ()),
            user_approval_set=bool(data.get("user_approval_set", False)),
        )


@dataclass(frozen=True)
class AnalystEvidencePayload:
    """Typed analyst evidence body (bounded, card-first)."""

    PAYLOAD_KIND = "analyst_evidence"
    MAX_FINDINGS = 16

    summary: str
    finding_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _require_non_empty_str(self.summary, "summary"))
        for label in ("finding_refs", "evidence_refs"):
            refs = tuple(getattr(self, label))
            if len(refs) > self.MAX_FINDINGS:
                raise ValueError(f"{label} count ({len(refs)}) exceeds maximum {self.MAX_FINDINGS}")
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    raise ValueError(f"{label} must contain non-empty strings")
            object.__setattr__(self, label, refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.PAYLOAD_KIND,
            "summary": self.summary,
            "finding_refs": list(self.finding_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AnalystEvidencePayload:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {"kind", "summary", "finding_refs", "evidence_refs"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in AnalystEvidencePayload: {sorted(extra)}")
        if "summary" not in data:
            raise ValueError("Missing required field in AnalystEvidencePayload: 'summary'")
        return cls(
            summary=data["summary"],
            finding_refs=tuple(data.get("finding_refs") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
        )


@dataclass(frozen=True)
class CoderImplementationPayload:
    """Typed coder implementation body (bounded, card-first).

    Carries what was changed, what validation ran, and any justified test
    modifications. Acceptance expectations stay frozen in the TaskHandoff;
    the payload references (never redefines) them.
    """

    PAYLOAD_KIND = "coder_implementation_evidence"
    MAX_REFS = 32

    summary: str
    artifact_refs: tuple[str, ...] = ()
    validation_performed: tuple[str, ...] = ()
    test_summary: str | None = None
    test_modifications: tuple[Mapping[str, Any], ...] = ()
    remaining_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _require_non_empty_str(self.summary, "summary"))
        for label in ("artifact_refs", "validation_performed", "remaining_limitations"):
            refs = tuple(getattr(self, label))
            if len(refs) > self.MAX_REFS:
                raise ValueError(f"{label} count ({len(refs)}) exceeds maximum {self.MAX_REFS}")
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    raise ValueError(f"{label} must contain non-empty strings")
            object.__setattr__(self, label, refs)
        if self.test_summary is not None:
            object.__setattr__(self, "test_summary", _require_non_empty_str(self.test_summary, "test_summary"))
        mods = tuple(self.test_modifications)
        for mod in mods:
            if not isinstance(mod, Mapping):
                raise TypeError("test_modifications must contain mappings")
        object.__setattr__(self, "test_modifications", mods)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.PAYLOAD_KIND,
            "summary": self.summary,
            "artifact_refs": list(self.artifact_refs),
            "validation_performed": list(self.validation_performed),
            "test_modifications": [dict(m) for m in self.test_modifications],
            "remaining_limitations": list(self.remaining_limitations),
        }
        if self.test_summary is not None:
            d["test_summary"] = self.test_summary
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CoderImplementationPayload:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        allowed = {
            "kind",
            "summary",
            "artifact_refs",
            "validation_performed",
            "test_summary",
            "test_modifications",
            "remaining_limitations",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in CoderImplementationPayload: {sorted(extra)}")
        if "summary" not in data:
            raise ValueError("Missing required field in CoderImplementationPayload: 'summary'")
        return cls(
            summary=data["summary"],
            artifact_refs=tuple(data.get("artifact_refs") or ()),
            validation_performed=tuple(data.get("validation_performed") or ()),
            test_summary=data.get("test_summary"),
            test_modifications=tuple(data.get("test_modifications") or ()),
            remaining_limitations=tuple(data.get("remaining_limitations") or ()),
        )


__all__ = [
    "PROJECT_STEWARD_MODE_A_DISPATCH_WIRED",
    "PROJECT_STEWARD_MODE_B_DISPATCH_WIRED",
    "PROJECT_STEWARD_PROJECT_STATE_MODE_PLANS_WORK",
    "PROJECT_STATE_RESULT_IS_COMPACT",
    "PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION",
    "PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION",
    "PROJECT_STEWARD_UNRESTRICTED_SHELL",
    "PROJECT_STEWARD_CAN_SET_USER_APPROVAL",
    "PROJECT_STEWARD_CAN_INVENT_ACCEPTED_FRONTIER",
    "PROJECT_STEWARD_IS_ACCEPTANCE_AUTHORITY",
    "FINALIZER_CAN_EXCEED_STEWARD_RESULT_SCOPE",
    "STEWARD_RESULT_SEAM_WIRED",
    "NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT",
    "STEWARD_MUTATION_ARCHITECTURE",
    "SECOND_RESULT_TRANSPORT_CREATED",
    "StewardDispatchMode",
    "STEWARD_TASK_KIND_MODE_A",
    "STEWARD_TASK_KIND_MODE_B",
    "StewardClosureVerdict",
    "ProjectStatePayload",
    "StewardResult",
    "AnalystEvidencePayload",
    "CoderImplementationPayload",
    "build_mode_a_handoff",
    "build_mode_b_handoff",
]
