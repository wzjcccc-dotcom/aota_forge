"""Milestone DAG & Progress Evidence Contract — S4 M2 W1.

Thin bounded immutable evidence contracts required for deterministic M2 progression,
without implementing progression itself.

Contracts:
    MilestoneWorkItemGraph
    WorkItemProgressEvidence
    FocusedValidationEvidence
    ReviewSatisfactionEvidence

Invariants
----------
* PURE_CONTRACT_LAYER=yes, STATELESS=yes, IMMUTABLE=yes, DETERMINISTIC=yes
* BOUNDED=yes, FAIL_CLOSED=yes
* Existing S1/S2/M1 contracts -> S4/M2 progression evidence contracts (no reverse import)
* Graph is projection only: ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
* No scheduler/workflow engine/execution state machine/result ontology/authority ontology/git lifecycle

Dependency direction: existing work_plane contracts -> progression evidence
Core never imports progression.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.result_governance import ResultOutcome
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.result_card import ResultHandoffRef, WorkerResultCard
from aota_forge.work_plane.risk_review import (
    ChallengeKind,
    ChallengeRole,
    ReviewEscalationDisposition,
    ReviewTrigger,
    parse_challenge_kind,
    parse_challenge_role,
    parse_review_trigger,
)
from aota_forge.work_plane.roles import AgentWorkRole

# ---------------------------------------------------------------------------
# Bounded capacities (structural safety limits — NOT risk calibration)
# ---------------------------------------------------------------------------

MAX_WORK_ITEMS_PER_MILESTONE: int = 64
MAX_DEPENDENCY_EDGES: int = 256
MAX_WORK_ITEM_REF_LENGTH: int = 512
MAX_MILESTONE_REF_LENGTH: int = 512
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128
MAX_EVIDENCE_REFS: int = 16
MAX_EVIDENCE_REF_LENGTH: int = 512
MAX_SUPPORTING_REFS: int = 16

# Authority / correctness flags
MILESTONE_WORK_ITEM_GRAPH_IS_PLAN_AUTHORITY: bool = False
MILESTONE_WORK_ITEM_GRAPH_IS_EXECUTION_AUTHORITY: bool = False
ISSUE_BODY_REMAINS_PLAN_AUTHORITY: bool = True

WORK_ITEM_PROGRESS_EVIDENCE_IS_AUTHORITY: bool = False
WORK_ITEM_PROGRESS_EVIDENCE_IS_RESULT_AUTHORITY: bool = False
WORK_ITEM_PROGRESS_EVIDENCE_IS_WORK_ITEM_ACCEPTANCE: bool = False

FOCUSED_VALIDATION_IS_OPERATION_AUTHORITY: bool = False
FOCUSED_VALIDATION_IS_WORK_ITEM_ACCEPTANCE_AUTHORITY: bool = False
VALIDATION_UNKNOWN_IS_NOT_PASS: bool = True

REVIEW_SATISFACTION_IS_EVIDENCE_ONLY: bool = True
REVIEW_SATISFACTION_IS_AUTHORITY: bool = False
NAKED_REVIEW_COMPLETED_BOOLEAN_ALLOWED: bool = False
CALLER_CAN_SELF_DECLARE_REVIEW_SATISFIED: bool = False
WORKER_CAN_SELF_DECLARE_REVIEW_SATISFIED: bool = False

PROGRESSION_EVALUATOR_RUNS_GIT: bool = False
NEW_GIT_LIFECYCLE_CREATED: bool = False
NEW_SCHEDULER_CREATED: bool = False
NEW_WORKFLOW_ENGINE_CREATED: bool = False
NEW_EXECUTION_STATE_MACHINE_CREATED: bool = False
NEW_RESULT_ONTOLOGY_CREATED: bool = False
NEW_AUTHORITY_ONTOLOGY_CREATED: bool = False
PERSISTENT_WORKFLOW_STATE_CREATED: bool = False

WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY: bool = False
WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY: bool = False

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
    # digest must be non-empty hex-like? Accept any bounded non-empty string but enforce hex length 64 typical?
    # Keep bounded check only; hex enforcement is not required but we ensure no whitespace/NUL already.
    if not s:
        raise ValueError(f"{label} must be non-empty")
    return s


# ---------------------------------------------------------------------------
# MilestoneWorkItemGraph — bounded immutable projection of approved Milestone DAG
# ---------------------------------------------------------------------------

_ALLOWED_GRAPH_FIELDS: frozenset[str] = frozenset({"milestone_ref", "work_items", "dependencies"})


@dataclass(frozen=True)
class MilestoneWorkItemGraph:
    """Bounded immutable projection of approved Milestone DAG.

    Identity carries milestone_ref + bounded work item identities + dependency edges.
    Projection only: not Plan authority, not execution authority.
    """

    milestone_ref: str
    work_items: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        # milestone_ref
        object.__setattr__(self, "milestone_ref", _validate_bounded_str(self.milestone_ref, "milestone_ref", MAX_MILESTONE_REF_LENGTH))

        # work_items — must be bounded list/tuple, non-empty
        if not isinstance(self.work_items, (tuple, list)):
            raise TypeError(f"work_items must be tuple/list, got {type(self.work_items).__name__}")
        raw_items = list(self.work_items)
        if len(raw_items) == 0:
            raise ValueError("work_items must not be empty (empty DAG)")
        if len(raw_items) > MAX_WORK_ITEMS_PER_MILESTONE:
            raise ValueError(f"work_items count ({len(raw_items)}) exceeds maximum {MAX_WORK_ITEMS_PER_MILESTONE}")

        # Validate each work_item identity — fail-closed before canonical dedup
        validated_items: list[str] = []
        seen_items: set[str] = set()
        for idx, wi in enumerate(raw_items):
            s = _validate_bounded_str(wi, f"work_items[{idx}]", MAX_WORK_ITEM_REF_LENGTH)
            if s in seen_items:
                raise ValueError(f"duplicate Work Item identity: {s!r}")
            seen_items.add(s)
            validated_items.append(s)

        # dependencies — list of [from,to] pairs
        if not isinstance(self.dependencies, (tuple, list)):
            raise TypeError(f"dependencies must be tuple/list, got {type(self.dependencies).__name__}")
        raw_deps = list(self.dependencies)
        if len(raw_deps) > MAX_DEPENDENCY_EDGES:
            raise ValueError(f"dependency edge count ({len(raw_deps)}) exceeds maximum {MAX_DEPENDENCY_EDGES}")

        validated_deps: list[tuple[str, str]] = []
        seen_edges: set[tuple[str, str]] = set()
        for idx, edge in enumerate(raw_deps):
            if not isinstance(edge, (tuple, list)):
                raise TypeError(f"dependencies[{idx}] must be tuple/list [from,to], got {type(edge).__name__}")
            if len(edge) != 2:
                raise ValueError(f"dependencies[{idx}] must have exactly 2 elements [from,to], got {len(edge)}")
            src_raw, dst_raw = edge[0], edge[1]
            src = _validate_bounded_str(src_raw, f"dependencies[{idx}].from", MAX_WORK_ITEM_REF_LENGTH)
            dst = _validate_bounded_str(dst_raw, f"dependencies[{idx}].to", MAX_WORK_ITEM_REF_LENGTH)
            # self dependency fail-closed
            if src == dst:
                raise ValueError(f"dependencies[{idx}] self dependency not allowed: {src!r}")
            # unknown dependency fail-closed
            if src not in seen_items:
                raise ValueError(f"dependencies[{idx}] unknown dependency source: {src!r}")
            if dst not in seen_items:
                raise ValueError(f"dependencies[{idx}] unknown dependency target: {dst!r}")
            pair = (src, dst)
            if pair in seen_edges:
                raise ValueError(f"dependencies[{idx}] duplicate dependency edge: {pair!r}")
            seen_edges.add(pair)
            validated_deps.append(pair)

        # cycle detection — fail-closed (DFS)
        # Build adjacency list from validated deps
        adj: dict[str, list[str]] = {wi: [] for wi in validated_items}
        for src, dst in validated_deps:
            adj[src].append(dst)
        # Kahn or DFS cycle detection
        visited: dict[str, int] = {wi: 0 for wi in validated_items}  # 0=unvisited,1=visiting,2=done

        def _dfs(node: str) -> bool:
            visited[node] = 1
            for nb in adj[node]:
                if visited[nb] == 1:
                    return True  # cycle
                if visited[nb] == 0 and _dfs(nb):
                    return True
            visited[node] = 2
            return False

        for n in validated_items:
            if visited[n] == 0 and _dfs(n):
                raise ValueError(f"cycle detected in dependency graph involving {n!r}")

        # Canonical ordering: sort work_items and dependencies deterministically
        canonical_items = tuple(sorted(validated_items))
        canonical_deps = tuple(sorted(validated_deps, key=lambda x: (x[0], x[1])))

        object.__setattr__(self, "work_items", canonical_items)
        object.__setattr__(self, "dependencies", canonical_deps)

    # Authority-negative
    @property
    def is_plan_authority(self) -> bool:
        return False

    @property
    def is_execution_authority(self) -> bool:
        return False

    @property
    def is_operation_authority(self) -> bool:
        return False

    # Topology helpers (optional, not evaluators)
    def predecessors_of(self, node: str) -> tuple[str, ...]:
        if node not in self.work_items:
            raise ValueError(f"unknown Work Item: {node!r}")
        preds = [src for src, dst in self.dependencies if dst == node]
        return tuple(sorted(preds))

    def successors_of(self, node: str) -> tuple[str, ...]:
        if node not in self.work_items:
            raise ValueError(f"unknown Work Item: {node!r}")
        succs = [dst for src, dst in self.dependencies if src == node]
        return tuple(sorted(succs))

    @property
    def roots(self) -> tuple[str, ...]:
        # nodes with no incoming edges
        targets = {dst for _, dst in self.dependencies}
        r = [wi for wi in self.work_items if wi not in targets]
        return tuple(sorted(r))

    @property
    def terminals(self) -> tuple[str, ...]:
        sources = {src for src, _ in self.dependencies}
        t = [wi for wi in self.work_items if wi not in sources]
        return tuple(sorted(t))

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "dependencies": [{"from": src, "to": dst} for src, dst in self.dependencies],
            "milestone_ref": self.milestone_ref,
            "work_items": list(self.work_items),
        }, path="MilestoneWorkItemGraph")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    @property
    def graph_digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_ref": self.milestone_ref,
            "work_items": list(self.work_items),
            "dependencies": [[src, dst] for src, dst in self.dependencies],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneWorkItemGraph":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_GRAPH_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in MilestoneWorkItemGraph: {sorted(extra)}")
        for req in ("milestone_ref", "work_items", "dependencies"):
            if req not in data:
                # dependencies may be omitted -> treat as empty?
                if req == "dependencies":
                    continue
                raise ValueError(f"Missing required field in MilestoneWorkItemGraph: {req!r}")
        work_items = data.get("work_items")
        if work_items is None:
            raise ValueError("Missing required field: work_items")
        deps = data.get("dependencies", ())
        # Normalize deps from list of dicts or list of pairs
        normalized_deps: list[tuple[str, str]] = []
        if isinstance(deps, (list, tuple)):
            for idx, edge in enumerate(deps):
                if isinstance(edge, Mapping):
                    if "from" not in edge or "to" not in edge:
                        raise ValueError(f"dependencies[{idx}] mapping must have 'from' and 'to'")
                    extra_edge = set(edge.keys()) - {"from", "to"}
                    if extra_edge:
                        raise ValueError(f"Unknown field(s) in dependencies[{idx}]: {sorted(extra_edge)}")
                    normalized_deps.append((edge["from"], edge["to"]))
                elif isinstance(edge, (list, tuple)):
                    normalized_deps.append((edge[0], edge[1]))  # validation will catch malformed
                else:
                    raise TypeError(f"dependencies[{idx}] must be mapping or pair, got {type(edge).__name__}")
        else:
            raise TypeError(f"dependencies must be list/tuple, got {type(deps).__name__}")
        return cls(
            milestone_ref=data["milestone_ref"],
            work_items=tuple(work_items),  # type: ignore[arg-type]
            dependencies=tuple(normalized_deps),
        )


# ---------------------------------------------------------------------------
# WorkItemProgressEvidence — thin evidence linking Work Item to governed result
# ---------------------------------------------------------------------------

_ALLOWED_PROGRESS_FIELDS: frozenset[str] = frozenset({
    "work_item_ref",
    "worker_result_ref",
    "worker_result_digest",
    "source_frontier_ref",
    "source_consistency_evidence_ref",
    "supporting_evidence_refs",
})


@dataclass(frozen=True)
class WorkItemProgressEvidence:
    """Immutable bounded evidence linking one Work Item to governed result evidence.

    Not authority, not result authority, not acceptance.
    """

    work_item_ref: str
    worker_result_ref: ResultHandoffRef
    worker_result_digest: str
    source_frontier_ref: SemanticReference | None = None
    source_consistency_evidence_ref: SemanticReference | None = None
    supporting_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_ref", _validate_bounded_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        # worker_result_ref must be ResultHandoffRef
        if not isinstance(self.worker_result_ref, ResultHandoffRef):
            raise TypeError(f"worker_result_ref must be ResultHandoffRef, got {type(self.worker_result_ref).__name__}")
        object.__setattr__(self, "worker_result_digest", _validate_digest(self.worker_result_digest, "worker_result_digest"))
        if self.source_frontier_ref is not None:
            if not isinstance(self.source_frontier_ref, SemanticReference):
                raise TypeError(f"source_frontier_ref must be SemanticReference or None, got {type(self.source_frontier_ref).__name__}")
        if self.source_consistency_evidence_ref is not None:
            if not isinstance(self.source_consistency_evidence_ref, SemanticReference):
                raise TypeError(f"source_consistency_evidence_ref must be SemanticReference or None, got {type(self.source_consistency_evidence_ref).__name__}")
        # supporting refs bounded
        if not isinstance(self.supporting_evidence_refs, (tuple, list)):
            raise TypeError(f"supporting_evidence_refs must be tuple/list, got {type(self.supporting_evidence_refs).__name__}")
        if len(self.supporting_evidence_refs) > MAX_SUPPORTING_REFS:
            raise ValueError(f"supporting_evidence_refs count ({len(self.supporting_evidence_refs)}) exceeds maximum {MAX_SUPPORTING_REFS}")
        out: list[str] = []
        seen: set[str] = set()
        for idx, r in enumerate(self.supporting_evidence_refs):
            s = _validate_bounded_str(r, f"supporting_evidence_refs[{idx}]", MAX_EVIDENCE_REF_LENGTH)
            if s in seen:
                raise ValueError(f"supporting_evidence_refs[{idx}] duplicate ref: {s!r}")
            seen.add(s)
            out.append(s)
        object.__setattr__(self, "supporting_evidence_refs", tuple(sorted(out)))

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_result_authority(self) -> bool:
        return False

    @property
    def is_acceptance(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "supporting_evidence_refs": sorted(self.supporting_evidence_refs),
            "work_item_ref": self.work_item_ref,
            "worker_result_digest": self.worker_result_digest,
            "worker_result_ref": self.worker_result_ref.to_dict(),
        }
        if self.source_frontier_ref is not None:
            d["source_frontier_ref"] = self.source_frontier_ref.to_dict()
        if self.source_consistency_evidence_ref is not None:
            d["source_consistency_evidence_ref"] = self.source_consistency_evidence_ref.to_dict()
        return canonicalize(d, path="WorkItemProgressEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_ref": self.work_item_ref,
            "worker_result_ref": self.worker_result_ref.to_dict(),
            "worker_result_digest": self.worker_result_digest,
            "source_frontier_ref": self.source_frontier_ref.to_dict() if self.source_frontier_ref else None,
            "source_consistency_evidence_ref": self.source_consistency_evidence_ref.to_dict() if self.source_consistency_evidence_ref else None,
            "supporting_evidence_refs": list(self.supporting_evidence_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkItemProgressEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_PROGRESS_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in WorkItemProgressEvidence: {sorted(extra)}")
        for req in ("work_item_ref", "worker_result_ref", "worker_result_digest"):
            if req not in data:
                raise ValueError(f"Missing required field in WorkItemProgressEvidence: {req!r}")
        # Reject duplicate result ontology fields if present (SUCCESS/FAILED etc)
        # If caller provides outcome fields that duplicate WorkerResultCard ontology, reject
        for dup in ("outcome", "result_outcome", "status"):
            if dup in data:
                raise ValueError(f"WorkItemProgressEvidence must not duplicate result outcome field: {dup!r}")
        wr_ref = ResultHandoffRef.from_value(data["worker_result_ref"])
        # source refs optional
        sf_ref = None
        if data.get("source_frontier_ref") is not None:
            raw = data["source_frontier_ref"]
            if isinstance(raw, SemanticReference):
                sf_ref = raw
            elif isinstance(raw, Mapping):
                sf_ref = SemanticReference.from_value(raw)
            elif isinstance(raw, str):
                sf_ref = SemanticReference(ref=raw)
            else:
                raise TypeError(f"source_frontier_ref must be SemanticReference/mapping/str or None, got {type(raw).__name__}")
        sc_ref = None
        if data.get("source_consistency_evidence_ref") is not None:
            raw = data["source_consistency_evidence_ref"]
            if isinstance(raw, SemanticReference):
                sc_ref = raw
            elif isinstance(raw, Mapping):
                sc_ref = SemanticReference.from_value(raw)
            elif isinstance(raw, str):
                sc_ref = SemanticReference(ref=raw)
            else:
                raise TypeError(f"source_consistency_evidence_ref must be SemanticReference/mapping/str or None, got {type(raw).__name__}")
        sup = tuple(data.get("supporting_evidence_refs") or ())
        return cls(
            work_item_ref=data["work_item_ref"],
            worker_result_ref=wr_ref,
            worker_result_digest=data["worker_result_digest"],
            source_frontier_ref=sf_ref,
            source_consistency_evidence_ref=sc_ref,
            supporting_evidence_refs=sup,
        )


# ---------------------------------------------------------------------------
# FocusedValidationEvidence
# ---------------------------------------------------------------------------

@unique
class FocusedValidationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


_FOCUSED_VERDICT_VALUES: frozenset[str] = frozenset(v.value for v in FocusedValidationVerdict)

_ALLOWED_VALIDATION_FIELDS: frozenset[str] = frozenset({
    "work_item_ref",
    "verdict",
    "validation_evidence_ref",
    "validation_evidence_digest",
})


def parse_focused_validation_verdict(value: object) -> FocusedValidationVerdict:
    if isinstance(value, FocusedValidationVerdict):
        return value
    if isinstance(value, str) and type(value) is str:
        if value in _FOCUSED_VERDICT_VALUES:
            return FocusedValidationVerdict(value)
        raise ValueError(f"Unknown FocusedValidationVerdict: {value!r}. Must be one of {sorted(_FOCUSED_VERDICT_VALUES)}")
    if isinstance(value, Enum):
        raise TypeError(f"FocusedValidationVerdict must be PASS/FAIL/UNKNOWN, got foreign Enum {type(value).__name__}")
    raise TypeError(f"FocusedValidationVerdict must be string or FocusedValidationVerdict, got {type(value).__name__}")


@dataclass(frozen=True)
class FocusedValidationEvidence:
    """Thin validation evidence — not operation authority, not acceptance."""

    work_item_ref: str
    verdict: FocusedValidationVerdict
    validation_evidence_ref: SemanticReference
    validation_evidence_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_ref", _validate_bounded_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        v = parse_focused_validation_verdict(self.verdict)
        object.__setattr__(self, "verdict", v)
        if not isinstance(self.validation_evidence_ref, SemanticReference):
            raise TypeError(f"validation_evidence_ref must be SemanticReference, got {type(self.validation_evidence_ref).__name__}")
        if self.validation_evidence_digest is not None:
            object.__setattr__(self, "validation_evidence_digest", _validate_digest(self.validation_evidence_digest, "validation_evidence_digest"))

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_acceptance(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "validation_evidence_ref": self.validation_evidence_ref.to_dict(),
            "verdict": self.verdict.value,
            "work_item_ref": self.work_item_ref,
        }
        if self.validation_evidence_digest is not None:
            d["validation_evidence_digest"] = self.validation_evidence_digest
        return canonicalize(d, path="FocusedValidationEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "work_item_ref": self.work_item_ref,
            "verdict": self.verdict.value,
            "validation_evidence_ref": self.validation_evidence_ref.to_dict(),
        }
        if self.validation_evidence_digest is not None:
            d["validation_evidence_digest"] = self.validation_evidence_digest
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FocusedValidationEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_VALIDATION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in FocusedValidationEvidence: {sorted(extra)}")
        for req in ("work_item_ref", "verdict", "validation_evidence_ref"):
            if req not in data:
                raise ValueError(f"Missing required field in FocusedValidationEvidence: {req!r}")
        # reject boolean variants
        if isinstance(data["verdict"], bool):
            raise TypeError("FocusedValidationVerdict must not be bool")
        verdict = parse_focused_validation_verdict(data["verdict"])
        raw_ref = data["validation_evidence_ref"]
        if isinstance(raw_ref, SemanticReference):
            ref = raw_ref
        elif isinstance(raw_ref, Mapping):
            ref = SemanticReference.from_value(raw_ref)
        elif isinstance(raw_ref, str) and type(raw_ref) is str:
            # need to validate leading/trailing whitespace — SemanticReference will fail-closed
            ref = SemanticReference(ref=raw_ref)
        else:
            raise TypeError(f"validation_evidence_ref must be SemanticReference/mapping/str, got {type(raw_ref).__name__}")
        digest = data.get("validation_evidence_digest")
        return cls(
            work_item_ref=data["work_item_ref"],
            verdict=verdict,
            validation_evidence_ref=ref,
            validation_evidence_digest=digest,
        )


# ---------------------------------------------------------------------------
# ReviewSatisfactionEvidence — binds W + disposition digest + role + trigger + governed result
# ---------------------------------------------------------------------------

_ALLOWED_REVIEW_SATISFACTION_FIELDS: frozenset[str] = frozenset({
    "work_item_ref",
    "review_disposition_digest",
    "required_challenge_role",
    "review_trigger",
    "review_result_ref",
    "review_result_digest",
})


@dataclass(frozen=True)
class ReviewSatisfactionEvidence:
    """Immutable evidence that formal review required by disposition was satisfied.

    Binds same Work Item + exact disposition digest + required ChallengeRole +
    exact ReviewTrigger + existing governed review-result reference + digest.
    Evidence only, not authority.
    """

    work_item_ref: str
    review_disposition_digest: str
    required_challenge_role: ChallengeRole
    review_trigger: ReviewTrigger
    review_result_ref: ResultHandoffRef
    review_result_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_ref", _validate_bounded_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        object.__setattr__(self, "review_disposition_digest", _validate_digest(self.review_disposition_digest, "review_disposition_digest"))
        cr = parse_challenge_role(self.required_challenge_role)
        object.__setattr__(self, "required_challenge_role", cr)
        rt = parse_review_trigger(self.review_trigger)
        object.__setattr__(self, "review_trigger", rt)
        if not isinstance(self.review_result_ref, ResultHandoffRef):
            raise TypeError(f"review_result_ref must be ResultHandoffRef, got {type(self.review_result_ref).__name__}")
        object.__setattr__(self, "review_result_digest", _validate_digest(self.review_result_digest, "review_result_digest"))

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_acceptance(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "required_challenge_role": self.required_challenge_role.value,
            "review_disposition_digest": self.review_disposition_digest,
            "review_result_digest": self.review_result_digest,
            "review_result_ref": self.review_result_ref.to_dict(),
            "review_trigger": self.review_trigger.value,
            "work_item_ref": self.work_item_ref,
        }, path="ReviewSatisfactionEvidence")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_ref": self.work_item_ref,
            "review_disposition_digest": self.review_disposition_digest,
            "required_challenge_role": self.required_challenge_role.value,
            "review_trigger": self.review_trigger.value,
            "review_result_ref": self.review_result_ref.to_dict(),
            "review_result_digest": self.review_result_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewSatisfactionEvidence":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_REVIEW_SATISFACTION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ReviewSatisfactionEvidence: {sorted(extra)}")
        # naked review_completed boolean not allowed
        if "review_completed" in data or "review_ok" in data or "trusted" in data:
            raise ValueError("naked review_completed/trusted/review_ok field not allowed")
        for req in ("work_item_ref", "review_disposition_digest", "required_challenge_role", "review_trigger", "review_result_ref", "review_result_digest"):
            if req not in data:
                raise ValueError(f"Missing required field in ReviewSatisfactionEvidence: {req!r}")
        # Validate no empty digest
        rrd = data["review_result_digest"]
        rdd = data["review_disposition_digest"]
        if isinstance(rrd, str) and not rrd.strip():
            raise ValueError("review_result_digest must be non-empty")
        if isinstance(rdd, str) and not rdd.strip():
            raise ValueError("review_disposition_digest must be non-empty")
        cr = parse_challenge_role(data["required_challenge_role"])
        rt = parse_review_trigger(data["review_trigger"])
        rr_ref = ResultHandoffRef.from_value(data["review_result_ref"])
        return cls(
            work_item_ref=data["work_item_ref"],
            review_disposition_digest=data["review_disposition_digest"],
            required_challenge_role=cr,
            review_trigger=rt,
            review_result_ref=rr_ref,
            review_result_digest=data["review_result_digest"],
        )


# ---------------------------------------------------------------------------
# ProgressionDisposition — bounded immutable deterministic output (W2)
# ---------------------------------------------------------------------------

# W2 authority-negative flags
PROGRESSION_DISPOSITION_IS_OPERATION_AUTHORITY: bool = False
PROGRESSION_DISPOSITION_IS_PLAN_AUTHORITY: bool = False
PROGRESSION_DISPOSITION_IS_WORK_ITEM_ACCEPTANCE: bool = False
PROGRESSION_DISPOSITION_IS_MILESTONE_ACCEPTANCE: bool = False
AUTO_PROGRESSION_ALLOWED_IS_OPERATION_AUTHORITY: bool = False
PROGRESSION_DERIVED_FROM_EVIDENCE: bool = True
PROGRESSION_EVALUATOR_STATELESS: bool = True

# Challenge routing caps
MAX_REASONS: int = 16
MAX_REASON_LENGTH: int = 512

# Result binding invariants
PROGRESSION_DISPOSITION_IS_RESULT_AUTHORITY: bool = False

# Milestone review readiness invariants
AUTOMATIC_MILESTONE_APPROVAL: bool = False
AUTOMATIC_MILESTONE_CLOSURE: bool = False

# No new engines
M2_AUTO_RETRY_ENGINE_CREATED: bool = False
M2_REPAIR_LOOP_CREATED: bool = False


@dataclass(frozen=True)
class PerWorkItemChallengeRouting:
    """Bounded per-W challenge routing (deterministic, not authority)."""

    work_item_ref: str
    challenge_role: ChallengeRole
    challenge_kind: ChallengeKind
    review_trigger: ReviewTrigger
    # optional: store disposition digest for traceability

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_ref", _validate_bounded_str(self.work_item_ref, "work_item_ref", MAX_WORK_ITEM_REF_LENGTH))
        cr = parse_challenge_role(self.challenge_role)
        object.__setattr__(self, "challenge_role", cr)
        ck = parse_challenge_kind(self.challenge_kind)
        object.__setattr__(self, "challenge_kind", ck)
        rt = parse_review_trigger(self.review_trigger)
        object.__setattr__(self, "review_trigger", rt)

    @property
    def is_operation_authority(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "challenge_kind": self.challenge_kind.value,
            "challenge_role": self.challenge_role.value,
            "review_trigger": self.review_trigger.value,
            "work_item_ref": self.work_item_ref,
        }, path="PerWorkItemChallengeRouting")  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_ref": self.work_item_ref,
            "challenge_role": self.challenge_role.value,
            "challenge_kind": self.challenge_kind.value,
            "review_trigger": self.review_trigger.value,
        }


@dataclass(frozen=True)
class ProgressionDisposition:
    """Bounded immutable deterministic progression decision (authority-negative)."""

    progression_complete_work_item_refs: tuple[str, ...]
    ready_work_item_refs: tuple[str, ...]
    blocked_work_item_refs: tuple[str, ...]
    # per-W challenge routing (bounded)
    challenge_routings: tuple[PerWorkItemChallengeRouting, ...] = ()
    # semantic escalation per-W
    semantic_escalation_required_work_item_refs: tuple[str, ...] = ()
    # reconciliation-required per-W
    reconciliation_required_work_item_refs: tuple[str, ...] = ()
    # global flags
    milestone_review_ready: bool = False
    auto_progression_allowed: bool = False
    # bounded reasons/evidence refs
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validate work_item refs are sorted and bounded
        for label, seq in [
            ("progression_complete_work_item_refs", self.progression_complete_work_item_refs),
            ("ready_work_item_refs", self.ready_work_item_refs),
            ("blocked_work_item_refs", self.blocked_work_item_refs),
            ("semantic_escalation_required_work_item_refs", self.semantic_escalation_required_work_item_refs),
            ("reconciliation_required_work_item_refs", self.reconciliation_required_work_item_refs),
        ]:
            if not isinstance(seq, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list, got {type(seq).__name__}")
            if len(seq) > MAX_WORK_ITEMS_PER_MILESTONE:
                raise ValueError(f"{label} exceeds max {MAX_WORK_ITEMS_PER_MILESTONE}")
            for idx, v in enumerate(seq):
                _validate_bounded_str(v, f"{label}[{idx}]", MAX_WORK_ITEM_REF_LENGTH)
            # ensure sorted deterministic (fail-closed if not sorted? allow but normalize)
            sorted_seq = tuple(sorted(seq))
            if tuple(seq) != sorted_seq:
                raise ValueError(f"{label} must be sorted deterministically")
            object.__setattr__(self, label, sorted_seq)
        # challenge_routings must be sorted by work_item_ref
        if not isinstance(self.challenge_routings, (tuple, list)):
            raise TypeError(f"challenge_routings must be tuple/list, got {type(self.challenge_routings).__name__}")
        for idx, cr in enumerate(self.challenge_routings):
            if not isinstance(cr, PerWorkItemChallengeRouting):
                raise TypeError(f"challenge_routings[{idx}] must be PerWorkItemChallengeRouting")
        # Ensure sorted by work_item_ref deterministically
        sorted_routings = tuple(sorted(self.challenge_routings, key=lambda x: x.work_item_ref))
        if tuple(self.challenge_routings) != sorted_routings:
            raise ValueError("challenge_routings must be sorted by work_item_ref")
        object.__setattr__(self, "challenge_routings", sorted_routings)
        # bools strict
        for flag in ("milestone_review_ready", "auto_progression_allowed"):
            val = getattr(self, flag)
            if type(val) is not bool:
                raise TypeError(f"{flag} must be bool, got {type(val).__name__}")
        # reasons bounded
        if not isinstance(self.reasons, (tuple, list)):
            raise TypeError(f"reasons must be tuple/list, got {type(self.reasons).__name__}")
        if len(self.reasons) > MAX_REASONS:
            raise ValueError(f"reasons exceeds max {MAX_REASONS}")
        for idx, r in enumerate(self.reasons):
            _validate_bounded_str(r, f"reasons[{idx}]", MAX_REASON_LENGTH)
        object.__setattr__(self, "reasons", tuple(sorted(self.reasons)))
        # evidence_refs bounded
        if not isinstance(self.evidence_refs, (tuple, list)):
            raise TypeError(f"evidence_refs must be tuple/list, got {type(self.evidence_refs).__name__}")
        if len(self.evidence_refs) > MAX_EVIDENCE_REFS:
            raise ValueError(f"evidence_refs exceeds max {MAX_EVIDENCE_REFS}")
        for idx, r in enumerate(self.evidence_refs):
            _validate_bounded_str(r, f"evidence_refs[{idx}]", MAX_EVIDENCE_REF_LENGTH)
        object.__setattr__(self, "evidence_refs", tuple(sorted(self.evidence_refs)))
        # Disjointness check: progression_complete vs ready vs blocked should be disjoint (except blocked may overlap with reconciliation etc)
        pc = set(self.progression_complete_work_item_refs)
        rd = set(self.ready_work_item_refs)
        bl = set(self.blocked_work_item_refs)
        if pc & rd:
            raise ValueError(f"progression_complete and ready overlap: {pc & rd}")
        if pc & bl:
            raise ValueError(f"progression_complete and blocked overlap: {pc & bl}")
        # ready and blocked also disjoint (a W cannot be both ready and blocked)
        if rd & bl:
            raise ValueError(f"ready and blocked overlap: {rd & bl}")

    @property
    def is_operation_authority(self) -> bool:
        return False

    @property
    def is_plan_authority(self) -> bool:
        return False

    @property
    def is_milestone_acceptance(self) -> bool:
        return False

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize({
            "auto_progression_allowed": self.auto_progression_allowed,
            "blocked_work_item_refs": sorted(self.blocked_work_item_refs),
            "challenge_routings": [cr.canonical_dict() for cr in sorted(self.challenge_routings, key=lambda x: x.work_item_ref)],
            "evidence_refs": sorted(self.evidence_refs),
            "milestone_review_ready": self.milestone_review_ready,
            "progression_complete_work_item_refs": sorted(self.progression_complete_work_item_refs),
            "ready_work_item_refs": sorted(self.ready_work_item_refs),
            "reasons": sorted(self.reasons),
            "reconciliation_required_work_item_refs": sorted(self.reconciliation_required_work_item_refs),
            "semantic_escalation_required_work_item_refs": sorted(self.semantic_escalation_required_work_item_refs),
        }, path="ProgressionDisposition")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "progression_complete_work_item_refs": list(self.progression_complete_work_item_refs),
            "ready_work_item_refs": list(self.ready_work_item_refs),
            "blocked_work_item_refs": list(self.blocked_work_item_refs),
            "challenge_routings": [cr.to_dict() for cr in self.challenge_routings],
            "semantic_escalation_required_work_item_refs": list(self.semantic_escalation_required_work_item_refs),
            "reconciliation_required_work_item_refs": list(self.reconciliation_required_work_item_refs),
            "milestone_review_ready": self.milestone_review_ready,
            "auto_progression_allowed": self.auto_progression_allowed,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
        }


# ---------------------------------------------------------------------------
# Deterministic evaluator — pure, stateless, no Git, no scheduler
# ---------------------------------------------------------------------------

def _handoff_ref_to_key(ref: ResultHandoffRef) -> str:
    # Deterministic key for lookup: ref + optional digest
    return f"{ref.ref}::{ref.digest or ''}"


def _normalize_frontier(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, SemanticReference):
        return value.ref
    if isinstance(value, str) and type(value) is str:
        # validate bounded
        if not value.strip():
            raise ValueError("expected frontier must be non-empty")
        if value != value.strip():
            raise ValueError("expected frontier must not contain whitespace")
        return value.strip()
    if isinstance(value, Mapping):
        # try SemanticReference mapping
        sr = SemanticReference.from_value(value)  # type: ignore[arg-type]
        return sr.ref
    raise TypeError(f"expected frontier must be str/SemanticReference/Mapping or None, got {type(value).__name__}")


def evaluate_milestone_progression(
    graph: MilestoneWorkItemGraph,
    progress_evidence: tuple[WorkItemProgressEvidence, ...] | list[WorkItemProgressEvidence],
    validation_evidence: tuple[FocusedValidationEvidence, ...] | list[FocusedValidationEvidence],
    review_dispositions: Mapping[str, ReviewEscalationDisposition],
    satisfaction_evidence: tuple[ReviewSatisfactionEvidence, ...] | list[ReviewSatisfactionEvidence],
    worker_cards: Mapping[str, WorkerResultCard],
    review_result_cards: Mapping[str, WorkerResultCard],
    expected_source_frontiers: Mapping[str, object] | None = None,
) -> ProgressionDisposition:
    """Pure deterministic progression evaluator.

    Inputs are bounded deterministic collections; caller ordering does not affect output.
    Unknown Work Item keys fail closed. No Git, no scheduler, no state machine.
    """
    # --- Input validation (bounded, fail-closed) ---
    if not isinstance(graph, MilestoneWorkItemGraph):
        raise TypeError(f"graph must be MilestoneWorkItemGraph, got {type(graph).__name__}")
    if not isinstance(progress_evidence, (tuple, list)):
        raise TypeError(f"progress_evidence must be tuple/list, got {type(progress_evidence).__name__}")
    if not isinstance(validation_evidence, (tuple, list)):
        raise TypeError(f"validation_evidence must be tuple/list, got {type(validation_evidence).__name__}")
    if not isinstance(satisfaction_evidence, (tuple, list)):
        raise TypeError(f"satisfaction_evidence must be tuple/list, got {type(satisfaction_evidence).__name__}")
    if not isinstance(review_dispositions, Mapping):
        raise TypeError(f"review_dispositions must be mapping, got {type(review_dispositions).__name__}")
    if not isinstance(worker_cards, Mapping):
        raise TypeError(f"worker_cards must be mapping, got {type(worker_cards).__name__}")
    if not isinstance(review_result_cards, Mapping):
        raise TypeError(f"review_result_cards must be mapping, got {type(review_result_cards).__name__}")
    if expected_source_frontiers is not None and not isinstance(expected_source_frontiers, Mapping):
        raise TypeError(f"expected_source_frontiers must be mapping or None, got {type(expected_source_frontiers).__name__}")

    # Bounded size checks
    if len(progress_evidence) > MAX_WORK_ITEMS_PER_MILESTONE * 2:
        raise ValueError(f"progress_evidence exceeds bounded size")
    if len(validation_evidence) > MAX_WORK_ITEMS_PER_MILESTONE * 2:
        raise ValueError(f"validation_evidence exceeds bounded size")
    if len(satisfaction_evidence) > MAX_WORK_ITEMS_PER_MILESTONE * 2:
        raise ValueError(f"satisfaction_evidence exceeds bounded size")
    if len(review_dispositions) > MAX_WORK_ITEMS_PER_MILESTONE:
        raise ValueError(f"review_dispositions exceeds bounded size")
    if len(worker_cards) > MAX_WORK_ITEMS_PER_MILESTONE * 2:
        raise ValueError(f"worker_cards exceeds bounded size")
    if len(review_result_cards) > MAX_WORK_ITEMS_PER_MILESTONE * 2:
        raise ValueError(f"review_result_cards exceeds bounded size")
    if expected_source_frontiers is not None and len(expected_source_frontiers) > MAX_WORK_ITEMS_PER_MILESTONE:
        raise ValueError(f"expected_source_frontiers exceeds bounded size")

    graph_work_items = set(graph.work_items)

    # Unknown Work Item keys fail closed
    for ev in progress_evidence:
        if not isinstance(ev, WorkItemProgressEvidence):
            raise TypeError(f"progress_evidence element must be WorkItemProgressEvidence, got {type(ev).__name__}")
        if ev.work_item_ref not in graph_work_items:
            raise ValueError(f"unknown Work Item in progress_evidence: {ev.work_item_ref!r}")
    for ev in validation_evidence:
        if not isinstance(ev, FocusedValidationEvidence):
            raise TypeError(f"validation_evidence element must be FocusedValidationEvidence, got {type(ev).__name__}")
        if ev.work_item_ref not in graph_work_items:
            raise ValueError(f"unknown Work Item in validation_evidence: {ev.work_item_ref!r}")
    for ev in satisfaction_evidence:
        if not isinstance(ev, ReviewSatisfactionEvidence):
            raise TypeError(f"satisfaction_evidence element must be ReviewSatisfactionEvidence, got {type(ev).__name__}")
        if ev.work_item_ref not in graph_work_items:
            raise ValueError(f"unknown Work Item in satisfaction_evidence: {ev.work_item_ref!r}")
    for k in review_dispositions.keys():
        if not isinstance(k, str) or type(k) is not str:
            raise TypeError(f"review_dispositions key must be str, got {type(k).__name__}")
        if k not in graph_work_items:
            raise ValueError(f"unknown Work Item in review_dispositions: {k!r}")
        v = review_dispositions[k]
        if not isinstance(v, ReviewEscalationDisposition):
            raise TypeError(f"review_dispositions[{k!r}] must be ReviewEscalationDisposition, got {type(v).__name__}")
    if expected_source_frontiers is not None:
        for k in expected_source_frontiers.keys():
            if not isinstance(k, str) or type(k) is not str:
                raise TypeError(f"expected_source_frontiers key must be str, got {type(k).__name__}")
            if k not in graph_work_items:
                raise ValueError(f"unknown Work Item in expected_source_frontiers: {k!r}")
    # worker_cards keys are result refs, not work item refs — allow any bounded string but validate not empty
    for k, v in worker_cards.items():
        if not isinstance(k, str) or type(k) is not str or not k.strip():
            raise TypeError(f"worker_cards key must be non-empty str, got {k!r}")
        if not isinstance(v, WorkerResultCard):
            raise TypeError(f"worker_cards[{k!r}] must be WorkerResultCard, got {type(v).__name__}")
    for k, v in review_result_cards.items():
        if not isinstance(k, str) or type(k) is not str or not k.strip():
            raise TypeError(f"review_result_cards key must be non-empty str, got {k!r}")
        if not isinstance(v, WorkerResultCard):
            raise TypeError(f"review_result_cards[{k!r}] must be WorkerResultCard, got {type(v).__name__}")

    # No metadata bags
    # (We already reject unknown fields in evidence constructors)

    # --- Deduplicate evidence by canonical digest (deterministic) ---
    def _group_by_work_item(ev_list):
        grouped: dict[str, dict[str, Any]] = {}  # work_item_ref -> {digest: evidence}
        for ev in ev_list:
            wi = ev.work_item_ref  # type: ignore
            d = ev.digest  # type: ignore
            if wi not in grouped:
                grouped[wi] = {}
            grouped[wi][d] = ev
        return grouped

    progress_groups = _group_by_work_item(progress_evidence)
    validation_groups = _group_by_work_item(validation_evidence)
    satisfaction_groups = _group_by_work_item(satisfaction_evidence)

    # Detect conflicting evidence (same W, different digests)
    conflicting_progress = {wi for wi, digests in progress_groups.items() if len(digests) > 1}
    conflicting_validation = {wi for wi, digests in validation_groups.items() if len(digests) > 1}
    conflicting_satisfaction = {wi for wi, digests in satisfaction_groups.items() if len(digests) > 1}

    # Build deterministic lookup for worker cards by result_handoff_ref.ref and also by full key
    # Create maps for fast deterministic lookup
    worker_by_ref: dict[str, WorkerResultCard] = {}
    for card in worker_cards.values():
        # Use result_handoff_ref.ref as key; if duplicate ref with different digest, last wins but we check digest separately
        # Store by ref; for determinism sort by ref
        worker_by_ref[card.result_handoff_ref.ref] = card
    # Also map by handoff ref string for direct lookup
    # For review cards similarly
    review_by_ref: dict[str, WorkerResultCard] = {}
    for card in review_result_cards.values():
        review_by_ref[card.result_handoff_ref.ref] = card

    # --- Evaluate per-W 13-condition gate ---
    progression_complete: set[str] = set()
    blocked: set[str] = set()
    reconciliation_required: set[str] = set()
    semantic_escalation: set[str] = set()
    challenge_routings: dict[str, PerWorkItemChallengeRouting] = {}
    reasons: set[str] = set()
    evidence_refs: set[str] = set()

    # Also track per-W reasons for later ready calculation
    per_w_block_reason: dict[str, str] = {}

    for wi in sorted(graph.work_items):
        # Collect evidence refs for this W for output
        # Add digests as evidence refs (bounded)
        has_progress = wi in progress_groups
        has_validation = wi in validation_groups
        has_satisfaction = wi in satisfaction_groups
        prog_conflict = wi in conflicting_progress
        val_conflict = wi in conflicting_validation
        sat_conflict = wi in conflicting_satisfaction

        if prog_conflict:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "conflicting progress evidence"
            reasons.add(f"{wi}: conflicting progress evidence")
            continue
        if val_conflict:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "conflicting validation evidence"
            reasons.add(f"{wi}: conflicting validation evidence")
            continue
        if sat_conflict:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "conflicting satisfaction evidence"
            reasons.add(f"{wi}: conflicting satisfaction evidence")
            continue

        # If no progress evidence -> not complete, will evaluate for ready later
        if not has_progress:
            # No progress: not complete. Will be ready if predecessors complete and no other block.
            # But also need to check if missing validation? That is incomplete but not yet conflicting.
            # Record no evidence
            continue

        # Single progress evidence (deduped)
        pe: WorkItemProgressEvidence = next(iter(progress_groups[wi].values()))  # type: ignore

        # Track evidence refs
        evidence_refs.add(pe.worker_result_digest[:16])

        # --- Condition 2: binding consistency ---
        # Lookup worker card by ref
        worker_card = worker_by_ref.get(pe.worker_result_ref.ref)
        if worker_card is None:
            # Try direct mapping key lookup
            worker_card = worker_cards.get(pe.worker_result_ref.ref)
        if worker_card is None:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "missing worker card"
            reasons.add(f"{wi}: missing worker card")
            continue
        # Verify worker_result_ref equality and digest
        if worker_card.result_handoff_ref != pe.worker_result_ref:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "worker_result_ref mismatch"
            reasons.add(f"{wi}: worker_result_ref mismatch")
            continue
        if worker_card.card_digest != pe.worker_result_digest:
            reconciliation_required.add(wi)
            blocked.add(wi)
            per_w_block_reason[wi] = "worker_result_digest mismatch"
            reasons.add(f"{wi}: worker_result_digest mismatch")
            continue

        # Condition 8: no unresolved SemanticStop / MechanicalFailure (check before outcome to capture escalation)
        if worker_card.semantic_stop is not None:
            blocked.add(wi)
            semantic_escalation.add(wi)
            per_w_block_reason[wi] = "semantic stop"
            reasons.add(f"{wi}: semantic stop")
            continue
        if worker_card.mechanical_failure is not None:
            blocked.add(wi)
            per_w_block_reason[wi] = "mechanical failure"
            reasons.add(f"{wi}: mechanical failure")
            continue
        # --- Condition 1,9,10,11: governed SUCCESS ---
        # Check outcome SUCCESS, not UNKNOWN/FAILURE/CANCELLED
        if worker_card.outcome != ResultOutcome.SUCCESS:
            blocked.add(wi)
            per_w_block_reason[wi] = f"outcome {worker_card.outcome.value} not success"
            reasons.add(f"{wi}: outcome not success")
            continue

        # --- Condition 3: validation PASS ---
        if not has_validation:
            blocked.add(wi)
            per_w_block_reason[wi] = "missing validation"
            reasons.add(f"{wi}: missing validation")
            continue
        ve: FocusedValidationEvidence = next(iter(validation_groups[wi].values()))  # type: ignore
        evidence_refs.add(ve.validation_evidence_digest or ve.validation_evidence_ref.ref[:16])
        if ve.verdict != FocusedValidationVerdict.PASS:
            blocked.add(wi)
            per_w_block_reason[wi] = f"validation {ve.verdict.value}"
            reasons.add(f"{wi}: validation {ve.verdict.value}")
            continue

        # --- Disposition checks (need disposition for this W) ---
        disp = review_dispositions.get(wi)
        if disp is None:
            # If no disposition, we treat as not eligible? For safety, block and require reconciliation?
            # But spec says evaluator needs disposition associated with relevant Work Item.
            # If missing, consider not complete.
            blocked.add(wi)
            per_w_block_reason[wi] = "missing disposition"
            reasons.add(f"{wi}: missing disposition")
            continue

        # Condition 5: semantic_escalation_required no (check before auto_continuation to preserve escalation semantics)
        if disp.semantic_escalation_required:
            blocked.add(wi)
            semantic_escalation.add(wi)
            per_w_block_reason[wi] = "semantic escalation required"
            reasons.add(f"{wi}: semantic escalation required")
            # preserve routing (also for UserEscalation triggers map to semantic reconciliation)
            if disp.challenge_role is not None:
                try:
                    rt = disp.identified_risk
                    if isinstance(rt, ReviewTrigger):
                        challenge_routings[wi] = PerWorkItemChallengeRouting(
                            work_item_ref=wi,
                            challenge_role=disp.challenge_role,
                            challenge_kind=disp.challenge_kind or ChallengeKind.SEMANTIC_RECONCILIATION,
                            review_trigger=rt,
                        )
                    elif rt is not None:
                        # UserEscalation -> still create routing with a placeholder trigger (use first ReviewTrigger mapping via challenge_role)
                        # For escalation, we map to a generic trigger but preserve role; use REVIEW trigger that maps to TASK_MAIN if needed
                        # Choose a trigger that matches challenge_role if possible, else fallback
                        # Here we attempt to map escalation to ACCEPTED_FRONTIER_ANOMALY style but keep deterministic
                        # Use the most semantic trigger that corresponds to task-main: use CROSS_SUBPLAN as fallback not ideal
                        # Instead, we will attempt to use the challenge's trigger via reverse mapping: if challenge_role is TASK_MAIN, use ACCEPTED_FRONTIER_ANOMALY as representative
                        fallback = ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY
                        challenge_routings[wi] = PerWorkItemChallengeRouting(
                            work_item_ref=wi,
                            challenge_role=disp.challenge_role,
                            challenge_kind=disp.challenge_kind or ChallengeKind.SEMANTIC_RECONCILIATION,
                            review_trigger=fallback,
                        )
                except Exception:
                    pass
            continue

        # Condition 4: auto_continuation_eligible
        if not disp.auto_continuation_eligible:
            blocked.add(wi)
            per_w_block_reason[wi] = "auto_continuation not eligible"
            reasons.add(f"{wi}: auto_continuation not eligible")
            # challenge routing for this W
            if disp.challenge_role is not None and disp.identified_risk is not None:
                try:
                    rt = disp.identified_risk
                    if isinstance(rt, ReviewTrigger):
                        challenge_routings[wi] = PerWorkItemChallengeRouting(
                            work_item_ref=wi,
                            challenge_role=disp.challenge_role,
                            challenge_kind=disp.challenge_kind or ChallengeKind.TECHNICAL_ACCEPTANCE,
                            review_trigger=rt,
                        )
                    else:
                        fallback = ReviewTrigger.ACCEPTED_FRONTIER_ANCESTRY_ANOMALY
                        challenge_routings[wi] = PerWorkItemChallengeRouting(
                            work_item_ref=wi,
                            challenge_role=disp.challenge_role,
                            challenge_kind=disp.challenge_kind or ChallengeKind.SEMANTIC_RECONCILIATION,
                            review_trigger=fallback,
                        )
                except Exception:
                    pass
            continue

        # Condition 13: no unresolved risk uncertainty
        # Check if disposition corresponds to UNRESOLVED_RISK_UNCERTAINTY
        if disp.identified_risk == ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY:
            blocked.add(wi)
            per_w_block_reason[wi] = "unresolved risk uncertainty"
            reasons.add(f"{wi}: unresolved risk uncertainty")
            # challenge routing
            if disp.challenge_role is not None:
                try:
                    challenge_routings[wi] = PerWorkItemChallengeRouting(
                        work_item_ref=wi,
                        challenge_role=disp.challenge_role,
                        challenge_kind=disp.challenge_kind or ChallengeKind.ARCHITECTURE_FEASIBILITY,
                        review_trigger=ReviewTrigger.UNRESOLVED_RISK_UNCERTAINTY,
                    )
                except Exception:
                    pass
            continue
        # Also check accepted_frontier_anomaly implies unresolved?
        if disp.accepted_frontier_anomaly:
            # This is a form of stale/conflicting frontier condition (12) but also uncertainty
            blocked.add(wi)
            reconciliation_required.add(wi)
            per_w_block_reason[wi] = "accepted frontier anomaly"
            reasons.add(f"{wi}: accepted frontier anomaly")
            continue

        # Condition 12: source frontier check
        if expected_source_frontiers is not None and wi in expected_source_frontiers:
            expected_raw = expected_source_frontiers[wi]
            expected_ref = _normalize_frontier(expected_raw)
            actual_ref = pe.source_frontier_ref.ref if pe.source_frontier_ref is not None else None
            if actual_ref is None:
                blocked.add(wi)
                per_w_block_reason[wi] = "required source frontier missing"
                reasons.add(f"{wi}: required source frontier missing")
                continue
            if actual_ref != expected_ref:
                blocked.add(wi)
                reconciliation_required.add(wi)
                per_w_block_reason[wi] = "source frontier mismatch"
                reasons.add(f"{wi}: source frontier mismatch")
                continue
        # If expected is None for this W, validation-only allowed (no check)

        # Condition 6/7: formal review
        if disp.formal_review_required:
            if not has_satisfaction:
                blocked.add(wi)
                per_w_block_reason[wi] = "missing review satisfaction"
                reasons.add(f"{wi}: missing review satisfaction")
                # challenge routing retained
                if disp.challenge_role is not None and disp.identified_risk is not None:
                    try:
                        if isinstance(disp.identified_risk, ReviewTrigger):
                            challenge_routings[wi] = PerWorkItemChallengeRouting(
                                work_item_ref=wi,
                                challenge_role=disp.challenge_role,
                                challenge_kind=disp.challenge_kind or ChallengeKind.TECHNICAL_ACCEPTANCE,
                                review_trigger=disp.identified_risk,
                            )
                    except Exception:
                        pass
                continue
            se: ReviewSatisfactionEvidence = next(iter(satisfaction_groups[wi].values()))  # type: ignore
            evidence_refs.add(se.review_result_digest[:16])
            # Verify satisfaction binds same W (already), disposition digest
            if se.review_disposition_digest != disp.digest:
                blocked.add(wi)
                reconciliation_required.add(wi)
                per_w_block_reason[wi] = "review disposition digest mismatch"
                reasons.add(f"{wi}: review disposition digest mismatch")
                continue
            if se.required_challenge_role != disp.challenge_role:
                blocked.add(wi)
                per_w_block_reason[wi] = "review challenge role mismatch"
                reasons.add(f"{wi}: review challenge role mismatch")
                continue
            # review trigger must match disposition trigger
            # disposition identified_risk should equal satisfaction review_trigger if identified_risk is ReviewTrigger
            if isinstance(disp.identified_risk, ReviewTrigger):
                if se.review_trigger != disp.identified_risk:
                    blocked.add(wi)
                    per_w_block_reason[wi] = "review trigger mismatch"
                    reasons.add(f"{wi}: review trigger mismatch")
                    continue
            # Verify review result card binding
            review_card = review_by_ref.get(se.review_result_ref.ref)
            if review_card is None:
                review_card = review_result_cards.get(se.review_result_ref.ref)
            if review_card is None:
                blocked.add(wi)
                reconciliation_required.add(wi)
                per_w_block_reason[wi] = "missing review result card"
                reasons.add(f"{wi}: missing review result card")
                continue
            if review_card.result_handoff_ref != se.review_result_ref:
                blocked.add(wi)
                reconciliation_required.add(wi)
                per_w_block_reason[wi] = "review result ref mismatch"
                reasons.add(f"{wi}: review result ref mismatch")
                continue
            if review_card.card_digest != se.review_result_digest:
                blocked.add(wi)
                reconciliation_required.add(wi)
                per_w_block_reason[wi] = "review result digest mismatch"
                reasons.add(f"{wi}: review result digest mismatch")
                continue
            # Verify review result outcome SUCCESS and role corresponds
            if review_card.outcome != ResultOutcome.SUCCESS:
                blocked.add(wi)
                per_w_block_reason[wi] = f"review result outcome {review_card.outcome.value}"
                reasons.add(f"{wi}: review result outcome {review_card.outcome.value}")
                continue
            # AgentWorkRole must correspond to required challenge role
            # ChallengeRole.ANALYST -> AgentWorkRole.ANALYST etc, TASK_MAIN -> task-main
            expected_role_map = {
                ChallengeRole.ANALYST: AgentWorkRole.ANALYST,
                ChallengeRole.REVIEWER: AgentWorkRole.REVIEWER,
                ChallengeRole.TASK_MAIN: AgentWorkRole.TASK_MAIN,
            }
            expected_agent_role = expected_role_map.get(se.required_challenge_role)
            if expected_agent_role is not None and review_card.agent_work_role != expected_agent_role:
                blocked.add(wi)
                per_w_block_reason[wi] = "review agent role mismatch"
                reasons.add(f"{wi}: review agent role mismatch")
                continue
            # All review checks pass -> continue to complete
        else:
            # formal_review_required == no => no satisfaction required; but if satisfaction exists for this W, ignore (evidence only)
            pass

        # All 13 conditions passed -> progression_complete
        progression_complete.add(wi)
        # evidence refs already added

    # --- Compute ready set (dependency-ready) ---
    ready: set[str] = set()
    # For each incomplete W not in progression_complete and not blocked due to attempt failure, check predecessors
    for wi in sorted(graph.work_items):
        if wi in progression_complete:
            continue
        # If already blocked due to conflicting/attempt failure, it should remain blocked, not ready
        if wi in blocked and per_w_block_reason.get(wi) is not None:
            # This W has attempt evidence that failed gate, so stays blocked
            continue
        # Check if W has any attempt evidence (progress) but we already handled blocked above? If it had progress but passed all checks, it would be in progression_complete. So if it has progress and not complete, it is already in blocked.
        # So for remaining W (no progress or not blocked for other reasons), check predecessors
        preds = graph.predecessors_of(wi)
        if all(p in progression_complete for p in preds):
            # No existing contradictory evidence preventing normal progression
            # Check if this W has any progress evidence at all (even if not conflicting) -> then it's an attempt that should not be ready
            if wi in progress_groups:
                # Has progress evidence but not complete -> already blocked above, so skip
                continue
            # Also if has validation evidence without progress? Treat as attempt? For safety, if has validation, consider attempt
            if wi in validation_groups:
                continue
            ready.add(wi)
        else:
            # Not all preds complete -> blocked_by_dependency, keep in blocked if not already
            blocked.add(wi)
            if wi not in per_w_block_reason:
                per_w_block_reason[wi] = "blocked by dependency"
                reasons.add(f"{wi}: blocked by dependency")

    # Final blocked set: all W not in progression_complete and not in ready (includes those with missing evidence but preds not ready)
    # Ensure blocked is sorted and includes all non-complete non-ready
    for wi in graph.work_items:
        if wi not in progression_complete and wi not in ready:
            blocked.add(wi)

    # Remove any W that might have been added to blocked but is actually progression_complete (should not happen)
    blocked = blocked - progression_complete
    ready = ready - progression_complete
    ready = ready - blocked  # ensure disjoint (should already)

    # --- Global flags ---
    # milestone_review_ready iff every graph Work Item is progression_complete and no global conditions
    milestone_review_ready = (
        len(progression_complete) == len(graph.work_items)
        and len(reconciliation_required) == 0
        and len(semantic_escalation) == 0
        and len(blocked) == 0
    )
    # If milestone_review_ready, ready must be empty per spec
    if milestone_review_ready:
        ready = set()

    auto_progression_allowed = (
        len(ready) > 0
        and len(reconciliation_required) == 0
        and len(semantic_escalation) == 0
    )

    # --- Challenge routings for remaining blocked that have disposition with challenge ---
    # Already added for formal review missing etc. Need to ensure for any remaining blocked due to validation etc, we don't add routing unless disposition indicates review
    # For completeness, add routing for any blocked where disposition has formal_review_required and we haven't yet
    for wi in blocked:
        if wi in challenge_routings:
            continue
        disp = review_dispositions.get(wi)
        if disp is not None and disp.formal_review_required and disp.challenge_role is not None and disp.identified_risk is not None:
            if isinstance(disp.identified_risk, ReviewTrigger):
                try:
                    challenge_routings[wi] = PerWorkItemChallengeRouting(
                        work_item_ref=wi,
                        challenge_role=disp.challenge_role,
                        challenge_kind=disp.challenge_kind or ChallengeKind.TECHNICAL_ACCEPTANCE,
                        review_trigger=disp.identified_risk,
                    )
                except Exception:
                    pass
        # Also semantic escalation already has routing via earlier

    # Sort and bound
    challenge_routings_tuple = tuple(sorted(challenge_routings.values(), key=lambda x: x.work_item_ref))
    # Ensure reasons bounded and sorted
    reasons_tuple = tuple(sorted(reasons)[:MAX_REASONS])
    evidence_refs_tuple = tuple(sorted(evidence_refs)[:MAX_EVIDENCE_REFS])

    # Build disposition
    # Ensure blocked and ready are sorted deterministically
    # The ProgressionDisposition constructor will enforce sorted
    disp_out = ProgressionDisposition(
        progression_complete_work_item_refs=tuple(sorted(progression_complete)),
        ready_work_item_refs=tuple(sorted(ready)),
        blocked_work_item_refs=tuple(sorted(blocked)),
        challenge_routings=challenge_routings_tuple,
        semantic_escalation_required_work_item_refs=tuple(sorted(semantic_escalation)),
        reconciliation_required_work_item_refs=tuple(sorted(reconciliation_required)),
        milestone_review_ready=milestone_review_ready,
        auto_progression_allowed=auto_progression_allowed,
        reasons=reasons_tuple,
        evidence_refs=evidence_refs_tuple,
    )
    return disp_out


__all__ = [
    "MAX_WORK_ITEMS_PER_MILESTONE",
    "MAX_DEPENDENCY_EDGES",
    "MAX_WORK_ITEM_REF_LENGTH",
    "MAX_MILESTONE_REF_LENGTH",
    "MAX_REF_LENGTH",
    "MAX_DIGEST_LENGTH",
    "MILESTONE_WORK_ITEM_GRAPH_IS_PLAN_AUTHORITY",
    "MILESTONE_WORK_ITEM_GRAPH_IS_EXECUTION_AUTHORITY",
    "ISSUE_BODY_REMAINS_PLAN_AUTHORITY",
    "WORK_ITEM_PROGRESS_EVIDENCE_IS_AUTHORITY",
    "WORK_ITEM_PROGRESS_EVIDENCE_IS_RESULT_AUTHORITY",
    "WORK_ITEM_PROGRESS_EVIDENCE_IS_WORK_ITEM_ACCEPTANCE",
    "FOCUSED_VALIDATION_IS_OPERATION_AUTHORITY",
    "FOCUSED_VALIDATION_IS_WORK_ITEM_ACCEPTANCE_AUTHORITY",
    "VALIDATION_UNKNOWN_IS_NOT_PASS",
    "REVIEW_SATISFACTION_IS_EVIDENCE_ONLY",
    "REVIEW_SATISFACTION_IS_AUTHORITY",
    "NAKED_REVIEW_COMPLETED_BOOLEAN_ALLOWED",
    "CALLER_CAN_SELF_DECLARE_REVIEW_SATISFIED",
    "WORKER_CAN_SELF_DECLARE_REVIEW_SATISFIED",
    "PROGRESSION_EVALUATOR_RUNS_GIT",
    "NEW_GIT_LIFECYCLE_CREATED",
    "NEW_SCHEDULER_CREATED",
    "NEW_WORKFLOW_ENGINE_CREATED",
    "NEW_EXECUTION_STATE_MACHINE_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_AUTHORITY_ONTOLOGY_CREATED",
    "PERSISTENT_WORKFLOW_STATE_CREATED",
    "WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY",
    "WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY",
    "PROGRESSION_DISPOSITION_IS_OPERATION_AUTHORITY",
    "PROGRESSION_DISPOSITION_IS_PLAN_AUTHORITY",
    "PROGRESSION_DISPOSITION_IS_WORK_ITEM_ACCEPTANCE",
    "PROGRESSION_DISPOSITION_IS_MILESTONE_ACCEPTANCE",
    "AUTO_PROGRESSION_ALLOWED_IS_OPERATION_AUTHORITY",
    "PROGRESSION_DERIVED_FROM_EVIDENCE",
    "PROGRESSION_EVALUATOR_STATELESS",
    "AUTOMATIC_MILESTONE_APPROVAL",
    "AUTOMATIC_MILESTONE_CLOSURE",
    "M2_AUTO_RETRY_ENGINE_CREATED",
    "M2_REPAIR_LOOP_CREATED",
    "MilestoneWorkItemGraph",
    "WorkItemProgressEvidence",
    "FocusedValidationVerdict",
    "FocusedValidationEvidence",
    "ReviewSatisfactionEvidence",
    "parse_focused_validation_verdict",
    "PerWorkItemChallengeRouting",
    "ProgressionDisposition",
    "evaluate_milestone_progression",
]
