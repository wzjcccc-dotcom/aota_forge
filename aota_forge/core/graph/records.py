"""M3-B3 durable subject graph — canonical record models.

Executor-neutral durable graph records (issue #9, lane M3-B3) implementing the
accepted M3-A1 canonical schema:

    Workflow / Subject / Execution / Completion / Decision / FollowupEdge

This lane (M3-B3) implements the durable record foundation ONLY.  It does not
implement identity policy (M3-B4), the Authority Engine (M3-B5), capability
lease, CAS/transaction choreography (M3-A3), migration/cutover (M3-A4), or any
authoritative runtime graph write.

Identity model boundary (M3-B3 vs M3-B4):

* M3-B4 owns label ``SUBJECT_ID_PRIMITIVE`` / ``RECORD_ID_PRIMITIVES`` and the
  concrete durable ID implementation.  M3-B3 must NOT invent a conflicting
  durable ID implementation.
* M3-B3 therefore defines records against a minimal, validated, OPAQUE value
  boundary (strategy B): a ``CanonicalId`` carrying a bounded opaque token and
  a ``kind``.  It constructs records and enforces structural referential
  consistency, but performs NO minting, collision, reuse, derivation, or
  authority semantics, and never infers authority from an ID.
* ``SUBJECT_ID_IS_AUTHORITY=no``; IDs identify records only.

Canonical ownership invariants (structurally enforced here and by the
repository / resolver / fixtures):

* Subject owns 0..n Execution (Execution.subject_ref -> exactly one Subject)
* Subject owns 0..n Decision (Decision.subject_ref -> exactly one Subject)
* Execution owns 0..1 Completion (Completion.execution_ref -> exactly one
  Execution); Completion has NO direct subject_ref field
* FollowupEdge requires a source Decision (FOLLOWUP_EDGE_REQUIRES_DECISION_REF)
* Completion is immutable; Decision is immutable; FollowupEdge is immutable
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing import Union

from aota_forge.core.graph.ids import CanonicalId, IdKind, ref_of

# Union of all canonical graph record types, used by the repository/store API.
# Declared via forward references to the classes defined below.
_AnyRecord = Union["Workflow", "Subject", "Execution", "Completion", "Decision", "FollowupEdge"]


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@dataclass
class Workflow:
    """Durable record of the semantic intent of one unit of work (issue #9)."""

    workflow_id: CanonicalId
    semantic_intent: str
    creation_context: dict
    goal: str = ""
    scope: dict = field(default_factory=dict)
    context_references: list = field(default_factory=list)
    scope_annotations: list = field(default_factory=list)
    non_semantic_metadata: dict = field(default_factory=dict)

    def canonical_fields(self) -> dict:
        return {
            "workflow_id": self.workflow_id.value,
            "semantic_intent": self.semantic_intent,
            "goal": self.goal,
            "scope": self.scope,
            "context_references": self.context_references,
            "creation_context": self.creation_context,
            "scope_annotations": self.scope_annotations,
            "non_semantic_metadata": self.non_semantic_metadata,
        }


# ---------------------------------------------------------------------------
# Subject aggregate
# ---------------------------------------------------------------------------


@dataclass
class Subject:
    """Durable identity anchor and mechanical lifecycle carrier (aggregate root).

    ``SUBJECT_IS_CANONICAL_LIFECYCLE_AGGREGATE=yes``; ``SUBJECT_ID_IS_AUTHORITY=no``.
    The canonical ``kind`` constrains identity derivation only; executor-private
    identity never becomes Subject identity.
    """

    subject_id: CanonicalId
    kind: str
    mechanical_state: dict
    id_derivation: str
    workflow_ref: CanonicalId | None = None
    creation_context: dict = field(default_factory=dict)

    def canonical_fields(self) -> dict:
        return {
            "subject_id": self.subject_id.value,
            "kind": self.kind,
            "workflow_ref": self.workflow_ref.value if self.workflow_ref else None,
            "mechanical_state": dict(self.mechanical_state),
            "creation_context": dict(self.creation_context),
            "id_derivation": self.id_derivation,
        }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class Execution:
    """One executor-neutral attempt advancing exactly one Subject.

    Retry and executor change both produce a NEW Execution under the SAME
    Subject (never a new Subject).  ``EXECUTION_OWNING_SUBJECT_REQUIRED=yes``.
    """

    execution_id: CanonicalId
    subject_ref: CanonicalId
    executor_kind: str
    mechanical_status: str
    executor_execution_ref: str | None = None
    correlation_id: str | None = None
    started_at: str | None = None
    requested_principal: str | None = None

    def canonical_fields(self) -> dict:
        return {
            "execution_id": self.execution_id.value,
            "subject_ref": self.subject_ref.value,
            "executor_kind": self.executor_kind,
            "executor_execution_ref": self.executor_execution_ref,
            "correlation_id": self.correlation_id,
            "mechanical_status": self.mechanical_status,
            "started_at": self.started_at,
            "requested_principal": self.requested_principal,
        }


# ---------------------------------------------------------------------------
# Completion (immutable; no direct subject_ref)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Completion:
    """Immutable terminal outcome record of exactly one Execution.

    Explicitly carries NO direct ``subject_ref`` field
    (``COMPLETION_SUBJECT_REF_FIELD_CREATED=no``); the owning Subject is reached
    transitively via ``execution_ref`` -> Execution -> owning Subject.
    """

    completion_id: CanonicalId
    execution_ref: CanonicalId
    outcome: str
    evidence_refs: list = field(default_factory=list)
    recorded_at: str | None = None

    def canonical_fields(self) -> dict:
        return {
            "completion_id": self.completion_id.value,
            "execution_ref": self.execution_ref.value,
            "outcome": self.outcome,
            "evidence_refs": self.evidence_refs,
            "recorded_at": self.recorded_at,
        }


# ---------------------------------------------------------------------------
# Decision (durable, immutable, owned by one Subject)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Durable semantic decision owned by exactly one Subject.

    Decision is a canonical child record, not an independent mutation/revision
    root.  It may later serve as the concrete semantic foundation for followup
    edge creation (evaluated by the M3-B5 Authority Engine, not here).
    """

    decision_id: CanonicalId
    subject_ref: CanonicalId
    decision_kind: str
    statement: str
    target_refs: list = field(default_factory=list)
    decision_time: str | None = None
    evidence_refs: list = field(default_factory=list)

    def canonical_fields(self) -> dict:
        return {
            "decision_id": self.decision_id.value,
            "subject_ref": self.subject_ref.value,
            "decision_kind": self.decision_kind,
            "statement": self.statement,
            "target_refs": self.target_refs,
            "decision_time": self.decision_time,
            "evidence_refs": self.evidence_refs,
        }


# ---------------------------------------------------------------------------
# Followup edge (immutable, Decision-backed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowupEdge:
    """Immutable Decision-backed parent/child lineage edge.

    Requires a source ``source_decision_ref`` (``FOLLOWUP_EDGE_REQUIRES_DECISION_REF=yes``,
    ``HEURISTIC_PARENT_SELECTION_ALLOWED=no``).  The atomic child-creation
    transaction is owned by M3-B6/B7, not here; B3 validates structural
    referential consistency only.
    """

    edge_id: CanonicalId
    parent_subject_ref: CanonicalId
    child_subject_ref: CanonicalId
    source_decision_ref: CanonicalId
    rationale: str | None = None
    created_at: str | None = None

    def canonical_fields(self) -> dict:
        return {
            "edge_id": self.edge_id.value,
            "parent_subject_ref": self.parent_subject_ref.value,
            "child_subject_ref": self.child_subject_ref.value,
            "source_decision_ref": self.source_decision_ref.value,
            "rationale": self.rationale,
            "created_at": self.created_at,
        }


def workflow(workflow_id: str, **kwargs) -> Workflow:
    return Workflow(workflow_id=ref_of(IdKind.WORKFLOW, workflow_id), **kwargs)


def subject(subject_id: str, kind: str, *, workflow_ref=None, **kwargs) -> Subject:
    if workflow_ref is not None and not isinstance(workflow_ref, CanonicalId):
        workflow_ref = ref_of(IdKind.WORKFLOW, workflow_ref)
    return Subject(
        subject_id=ref_of(IdKind.SUBJECT, subject_id),
        kind=kind,
        workflow_ref=workflow_ref,
        **kwargs,
    )


def execution(execution_id: str, subject_ref, executor_kind: str, **kwargs) -> Execution:
    return Execution(
        execution_id=ref_of(IdKind.EXECUTION, execution_id),
        subject_ref=ref_of(IdKind.SUBJECT, subject_ref),
        executor_kind=executor_kind,
        **kwargs,
    )


def completion(completion_id: str, execution_ref, outcome: str, **kwargs) -> Completion:
    return Completion(
        completion_id=ref_of(IdKind.COMPLETION, completion_id),
        execution_ref=ref_of(IdKind.EXECUTION, execution_ref),
        outcome=outcome,
        **kwargs,
    )


def decision(decision_id: str, subject_ref, decision_kind: str, statement: str, **kwargs) -> Decision:
    return Decision(
        decision_id=ref_of(IdKind.DECISION, decision_id),
        subject_ref=ref_of(IdKind.SUBJECT, subject_ref),
        decision_kind=decision_kind,
        statement=statement,
        **kwargs,
    )


def followup_edge(edge_id: str, parent_subject_ref, child_subject_ref, source_decision_ref, **kwargs) -> FollowupEdge:
    return FollowupEdge(
        edge_id=ref_of(IdKind.EDGE, edge_id),
        parent_subject_ref=ref_of(IdKind.SUBJECT, parent_subject_ref),
        child_subject_ref=ref_of(IdKind.SUBJECT, child_subject_ref),
        source_decision_ref=ref_of(IdKind.DECISION, source_decision_ref),
        **kwargs,
    )