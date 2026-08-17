"""M3-B8 Projection Reconstruction — typed projection model (Issue #9, lane M3-B8).

This module defines the B8-local, executor-neutral, storage-neutral projection
read model and its bounded result semantics.  A projection is DERIVED state
only: it is rebuilt deterministically from canonical AOTA Forge graph state
and NEVER becomes Subject authority
(``PROJECTION_IS_SUBJECT_AUTHORITY=no``, ``GRAPH_IS_AUTHORITY_FOR_PROJECTION=yes``).

The projection model is deliberately isolated from shared contracts: it does
NOT import or edit ``core/contracts/errors.py`` or
``core/contracts/results.py`` (``B8_SHARED_ERRORS_WRITE_ALLOWED=no``,
``B8_SHARED_RESULTS_WRITE_ALLOWED=no``).  It carries no mutation capability
and exposes no graph write path (``PROJECTION_GRAPH_WRITE_COUNT=0``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any


class ProjectionResultCode(str, Enum):
    """Bounded B8-local projection result codes.

    ``PROJECTED`` — a deterministic projection was produced for the Subject.
    ``STALE``    — a projection was produced but the source Subject revision
                   has advanced since it was built.
    ``NOT_FOUND``          — the canonical Subject does not exist.
    ``REFERENTIAL_ERROR``  — canonical graph referential corruption was
                             detected; fail closed, never silently skip.
    ``AMBIGUOUS_DERIVED_STATE`` — the graph does not mechanically define a
                             unique derived value (e.g. current Execution);
                             bounded ambiguity is exposed, never a guess.
    ``INVALID_REFERENCE``  — the caller supplied a wrong-kind / malformed
                             ObjectRef; fail closed before graph resolution.
    """

    PROJECTED = "PROJECTED"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    REFERENTIAL_ERROR = "REFERENTIAL_ERROR"
    AMBIGUOUS_DERIVED_STATE = "AMBIGUOUS_DERIVED_STATE"
    INVALID_REFERENCE = "INVALID_REFERENCE"


@dataclass(frozen=True)
class CompletionProjection:
    """Read-model view of one immutable canonical Completion.

    Carries NO direct subject ref, mirroring the canonical record: the owning
    Subject is reached transitively via the owning Execution.
    """

    completion_ref: str
    execution_ref: str
    outcome: str
    evidence_refs: tuple = ()
    recorded_at: str | None = None

    def canonical(self) -> dict:
        return {
            "completion_ref": self.completion_ref,
            "execution_ref": self.execution_ref,
            "outcome": self.outcome,
            "evidence_refs": list(self.evidence_refs),
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class ExecutionProjection:
    """Read-model view of one canonical Execution under the owning Subject."""

    execution_ref: str
    subject_ref: str
    executor_kind: str
    mechanical_status: str
    started_at: str | None = None
    correlation_id: str | None = None
    completion: CompletionProjection | None = None

    def canonical(self) -> dict:
        return {
            "execution_ref": self.execution_ref,
            "subject_ref": self.subject_ref,
            "executor_kind": self.executor_kind,
            "mechanical_status": self.mechanical_status,
            "started_at": self.started_at,
            "correlation_id": self.correlation_id,
            "completion": self.completion.canonical() if self.completion else None,
        }


@dataclass(frozen=True)
class DecisionProjection:
    """Read-model view of one canonical Decision owned by the Subject."""

    decision_ref: str
    subject_ref: str
    decision_kind: str
    statement: str
    decision_time: str | None = None
    target_refs: tuple = ()
    evidence_refs: tuple = ()

    def canonical(self) -> dict:
        return {
            "decision_ref": self.decision_ref,
            "subject_ref": self.subject_ref,
            "decision_kind": self.decision_kind,
            "statement": self.statement,
            "decision_time": self.decision_time,
            "target_refs": list(self.target_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class FollowupProjection:
    """Read-model view of one immutable Decision-backed FollowupEdge.

    The parent / child / source Decision lineage is copied EXACTLY from the
    canonical edge; B8 never invents or reconstructs missing Decision lineage
    (``FOLLOWUP_PROJECTION_REQUIRES_CANONICAL_EDGE=yes``,
    ``FOLLOWUP_PROJECTION_INVENTS_DECISION=no``).
    """

    edge_ref: str
    parent_subject_ref: str
    child_subject_ref: str
    source_decision_ref: str
    rationale: str | None = None

    def canonical(self) -> dict:
        return {
            "edge_ref": self.edge_ref,
            "parent_subject_ref": self.parent_subject_ref,
            "child_subject_ref": self.child_subject_ref,
            "source_decision_ref": self.source_decision_ref,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class SubjectProjection:
    """Deterministic read-model projection of one canonical Subject.

    ``current_execution`` is a DERIVED read-model value selected only from
    canonical deterministic graph mechanics (frozen ``started_at`` canonical
    field, ties -> bounded ambiguity).  It is NOT canonical identity authority.

    ``legacy_compat`` is a comparison-only view of legacy ``current_*`` style
    values derived only where canonical graph semantics support a deterministic
    equivalent (``LEGACY_COMPATIBILITY_IS_COMPARISON_ONLY=yes``).
    """

    subject_ref: str
    subject_revision: int
    revision_token: str
    workflow_ref: str | None
    kind: str
    executions: tuple = ()
    current_execution: ExecutionProjection | None = None
    decisions: tuple = ()
    followups: tuple = ()
    derived_lifecycle_status: str = "open"
    legacy_compat: dict = field(default_factory=dict)

    def canonical(self) -> dict:
        """Deterministic field-equivalent canonical representation.

        Same canonical graph state always yields the same byte-equivalent
        projection (``PROJECTION_REBUILD_DETERMINISTIC=yes``,
        ``PROJECTION_FULL_REBUILD_IMPLEMENTED=yes``).
        """
        return {
            "subject_ref": self.subject_ref,
            "subject_revision": self.subject_revision,
            "revision_token": self.revision_token,
            "workflow_ref": self.workflow_ref,
            "kind": self.kind,
            "executions": [e.canonical() for e in sorted(self.executions, key=lambda e: e.execution_ref)],
            "current_execution": self.current_execution.canonical() if self.current_execution else None,
            "decisions": [d.canonical() for d in sorted(self.decisions, key=lambda d: d.decision_ref)],
            "followups": [f.canonical() for f in sorted(self.followups, key=lambda f: f.edge_ref)],
            "derived_lifecycle_status": self.derived_lifecycle_status,
            "legacy_compat": dict(self.legacy_compat),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def mutation_authority(self) -> bool:
        """A projection can never grant mutation authority.

        ``PROJECTION_IS_SUBJECT_AUTHORITY=no``;
        ``PROJECTION_GRANTS_MUTATION_AUTHORITY=no``.
        """
        return False


def _canonical_key(value: Any) -> Any:
    return value


__all__ = [
    "ProjectionResultCode",
    "CompletionProjection",
    "ExecutionProjection",
    "DecisionProjection",
    "FollowupProjection",
    "SubjectProjection",
]
