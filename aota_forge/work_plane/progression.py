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
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.result_card import ResultHandoffRef
from aota_forge.work_plane.risk_review import ChallengeRole, ReviewTrigger, parse_challenge_role, parse_review_trigger

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
    "MilestoneWorkItemGraph",
    "WorkItemProgressEvidence",
    "FocusedValidationVerdict",
    "FocusedValidationEvidence",
    "ReviewSatisfactionEvidence",
    "parse_focused_validation_verdict",
]
