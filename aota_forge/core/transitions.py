"""M3-B7 executor-neutral canonical transition layer (Issue #9, lane M3-B7).

This module composes the accepted B3-B6 foundations into four canonical
lifecycle transitions:

    create_execution
    record_completion
    record_decision
    create_followup_subject

B7 is a MECHANICAL transition orchestrator.  It REUSES, never reinvents:

* B3 — Subject graph, record ownership, owning-Subject resolution
* B4 — ``InternalId``, ``ObjectRef``, ID Broker
* B5 — Authority Engine + Capability Lease
* B6 — Subject revision/CAS, transaction, idempotency, atomic lease
  consumption, and the parent + new-child transaction foundation

It is NOT a semantic reasoner and invents no scope, approval, decision,
identity policy, or Subject selection (``B7_IS_SEMANTIC_REASONER=no``,
``B7_SELECTS_SUBJECT_HEURISTICALLY=no``, ``B7_INVENTS_SCOPE=no``,
``B7_INVENTS_APPROVAL=no``, ``B7_INVENTS_DECISION=no``,
``B7_INVENTS_IDENTITY_POLICY=no``).

Canonical record authority root (preserved for every child-record mutation):

    record -> owning Subject -> authority -> Subject revision/CAS

``TRANSITION_SOURCE_IMPLEMENTED=yes``;
``TRANSITION_RUNTIME_AUTHORITY_ACTIVE=no``;
``PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no``;
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``.  These transitions run only against
the isolated, NON-AUTHORITATIVE ``TransactionStore`` (B6 fixture scope).  No
CLI / Hermes / production ingress / catalog wiring is activated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os

from aota_forge.core.authority import (
    MaterializedDecisionEvidence,
)
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import TrustedContext
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    OwningSubjectResolver,
    assert_object_ref_kind,
)
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import mint_subject_value
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.revision import MutationError
from aota_forge.core.transaction import (
    ParentChildTransaction,
    SubjectTransaction,
    TransactionStore,
)

# ---------------------------------------------------------------------------
# Module boundary flags (see plan sections 26, 30, 47).
# ---------------------------------------------------------------------------

FORGE_CORE_TRANSITIONS_CALLABLE_DIRECTLY = True
CLI_TRANSITION_WIRING_IMPLEMENTED = False
HERMES_TRANSITION_WIRING_IMPLEMENTED = False
PRODUCTION_INGRESS_TRANSITION_WIRING_IMPLEMENTED = False

TRANSITION_SOURCE_IMPLEMENTED = True
TRANSITION_RUNTIME_AUTHORITY_ACTIVE = False
PRODUCTION_GRAPH_AUTHORITY_ACTIVE = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
CUTOVER_PERFORMED = False
SHADOW_GRAPH_MATERIALIZATION_PERFORMED = False
PROJECTION_RECONSTRUCTION_IMPLEMENTED = False
BINDING_RECOVERY_IMPLEMENTED = False

# ---------------------------------------------------------------------------
# Reuse signals (B4/B5/B6).
# ---------------------------------------------------------------------------

B7_REUSES_B5_AUTHORITY_ENGINE = True
B7_DUPLICATE_AUTHORITY_ENGINE_CREATED = False
B7_REUSES_B6_CAS = True
B7_DUPLICATE_CAS_IMPLEMENTATION_CREATED = False
B7_REUSES_B6_TRANSACTION = True
B7_DUPLICATE_TRANSACTION_ENGINE_CREATED = False
B7_REUSES_B6_IDEMPOTENCY = True
B7_DUPLICATE_IDEMPOTENCY_SYSTEM_CREATED = False
B7_REUSES_B4_INTERNAL_ID = True
B7_REUSES_B4_OBJECT_REF = True
B7_REUSES_B4_ID_BROKER = True
B7_NEW_IDENTITY_POLICY_CREATED = False
B7_DUPLICATE_GRAPH_RECORD_MODEL_CREATED = False

B7_SILENT_REBASE_ALLOWED = False
B7_LAST_WRITE_WINS_ALLOWED = False

# ---------------------------------------------------------------------------
# Canonical transition role signals (plan sections 4, 24).
# ---------------------------------------------------------------------------

B7_IS_CANONICAL_TRANSITION_LAYER = True
B7_IS_SEMANTIC_REASONER = False
B7_SELECTS_SUBJECT_HEURISTICALLY = False
B7_INVENTS_SCOPE = False
B7_INVENTS_APPROVAL = False
B7_INVENTS_DECISION = False
B7_INVENTS_IDENTITY_POLICY = False

CREATE_EXECUTION_AUTHORITY_ROOT = "Subject"
RECORD_COMPLETION_AUTHORITY_ROOT = "owning_Subject"
RECORD_DECISION_AUTHORITY_ROOT = "owning_Subject"
EXECUTION_IS_INDEPENDENT_AUTHORITY_ROOT = False
COMPLETION_IS_INDEPENDENT_AUTHORITY_ROOT = False
DECISION_IS_INDEPENDENT_AUTHORITY_ROOT = False

COMPLETION_DIRECT_SUBJECT_REF_REQUIRED = False
COMPLETION_SUBJECT_REF_FIELD_CREATED = False

FOLLOWUP_REQUIRES_MATERIALIZED_DECISION = True
FOLLOWUP_CHILD_ID_MINTED_INSIDE_TRANSACTION = True
FOLLOWUP_CHILD_INITIAL_REVISION = 1
FOLLOWUP_EDGE_REQUIRES_DECISION_REF = True
FOLLOWUP_DECISION_MISMATCH_DENIED = True
FOLLOWUP_FOREIGN_DECISION_DENIED = True
FOLLOWUP_MISSING_DECISION_DENIED = True
FOLLOWUP_IDEMPOTENT_REPLAY_CREATES_SECOND_CHILD = False
FOLLOWUP_IDEMPOTENT_REPLAY_CREATES_SECOND_EDGE = False
FOLLOWUP_IDEMPOTENT_REPLAY_ADVANCES_PARENT_REVISION_AGAIN = False
PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY = False
CHILD_LEASE_REQUIRED_PRE_TRANSACTION = False

READONLY_DIAGNOSTIC_PLANE_PRESERVED = True

WRITE_SCOPE = {"mode": "write"}
_MINTED_ID_PREFIXES = {
    IdKind.EXECUTION: "exec",
    IdKind.COMPLETION: "comp",
    IdKind.DECISION: "dec",
    IdKind.EDGE: "edge",
}


# ---------------------------------------------------------------------------
# Deterministic bounded transition errors.
# ---------------------------------------------------------------------------


class TransitionError(MutationError):
    """Base class for B7 canonical transition failures (bounded code)."""

    code = "B7_TRANSITION_ERROR"
    default_message = "canonical transition error"


class TransitionInvalidError(TransitionError):
    """The canonical request is malformed or carries an unsupported shape."""

    code = "B7_INVALID_REQUEST"
    default_message = "invalid canonical transition request"


class TransitionDeniedError(TransitionError):
    """A canonical referential / decision guard denied the transition."""

    code = "B7_DENIED"
    default_message = "canonical transition denied"


class TransitionNotFoundError(TransitionError):
    """A required canonical reference could not be resolved."""

    code = "B7_REF_NOT_FOUND"
    default_message = "canonical reference not found"


# ---------------------------------------------------------------------------
# Canonical result model (executor-neutral; plan section 7).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    """Bounded executor-neutral result of one canonical transition.

    ``code`` is a deterministic bounded result code (``COMMITTED`` |
    ``REPLAYED``).  It never exposes credentials, private host paths, storage
    internals, or executor-private state.
    """

    transition: str
    owning_subject: str
    revision_after: int
    revision_token: str
    created_objects: tuple = ()
    replayed: bool = False
    code: str = "COMMITTED"


# ---------------------------------------------------------------------------
# Canonical request model (bounded typed input; plan section 6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateExecutionRequest:
    trusted_context: TrustedContext
    subject_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    executor_kind: str
    mechanical_status: str = "running"
    correlation_id: str | None = None
    executor_execution_ref: str | None = None


@dataclass(frozen=True)
class RecordCompletionRequest:
    trusted_context: TrustedContext
    execution_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    outcome: str
    evidence_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class RecordDecisionRequest:
    trusted_context: TrustedContext
    subject_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    decision_kind: str
    statement: str
    target_refs: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class CreateFollowupSubjectRequest:
    trusted_context: TrustedContext
    parent_ref: ObjectRef
    decision_ref: ObjectRef
    lease: CapabilityLease
    expected_revision: int
    idempotency_key: str
    trusted_time: datetime
    child_kind: str
    child_sub_kind: str
    rationale: str | None = None
    child_creation_context: dict = field(default_factory=dict)
    child_id_derivation: str = "minted"


# ---------------------------------------------------------------------------
# Validation helpers (fail closed, never heuristic).
# ---------------------------------------------------------------------------


def _require_store(store: object) -> TransactionStore:
    if not isinstance(store, TransactionStore):
        raise TransitionInvalidError(
            "canonical transitions require the isolated B6 TransactionStore"
        )
    return store


def _require_lease(lease: object) -> CapabilityLease:
    if not isinstance(lease, CapabilityLease):
        raise TransitionInvalidError("a Capability Lease is required")
    return lease


def _require_object_ref(ref: object, kind: str, label: str) -> ObjectRef:
    if not isinstance(ref, ObjectRef):
        raise TransitionInvalidError(f"{label} must be a B4 ObjectRef")
    assert_object_ref_kind(ref, kind, label)
    return ref


def _require_subject_ref(ref: object, label: str) -> ObjectRef:
    return _require_object_ref(ref, IdKind.SUBJECT, label)


def _bounded(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise TransitionInvalidError(f"{label} must be a bounded non-empty string")
    return value


def _bounded_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise TransitionInvalidError(f"{label} must be a bounded idempotency token")
    return value


def _bounded_scope(scope: object) -> dict:
    if not isinstance(scope, dict) or not scope:
        raise TransitionInvalidError("requested scope must be a non-empty mapping")
    return dict(scope)


def _iso(value: datetime) -> str:
    return value.astimezone().isoformat()


def _mint_id(kind: str, prefix: str) -> InternalId:
    return make_id(kind, f"{prefix}_{os.urandom(12).hex()}")


def _resolve_subject(store: TransactionStore, ref: ObjectRef) -> records.Subject:
    try:
        return store.read_subject(ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"subject not found: {ref.serialize()}") from exc


def _resolve_decision(store: TransactionStore, ref: ObjectRef) -> records.Decision:
    if not isinstance(ref, ObjectRef) or ref.object_kind != IdKind.DECISION:
        raise TransitionInvalidError("decision_ref must be a Decision ObjectRef")
    try:
        return store.decision(ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"decision not found: {ref.serialize()}") from exc


def _result(
    transition: str,
    subject_ref: ObjectRef,
    tx_result,
    created: tuple,
) -> TransitionResult:
    effect_refs = tuple(tx_result.effect_refs) if tx_result.effect_refs else tuple(created)
    return TransitionResult(
        transition=transition,
        owning_subject=subject_ref.serialize(),
        revision_after=tx_result.revision_after,
        revision_token=tx_result.revision_token,
        created_objects=effect_refs,
        replayed=bool(tx_result.replayed),
        code="REPLAYED" if tx_result.replayed else "COMMITTED",
    )


# ---------------------------------------------------------------------------
# create_execution
# ---------------------------------------------------------------------------


def create_execution(store: TransactionStore, req: CreateExecutionRequest) -> TransitionResult:
    """Create one Execution owned by an existing Subject.

    Authority root is the owning Subject; a retry or executor change produces a
    NEW Execution under the SAME Subject (``CREATE_EXECUTION_CREATES_NEW_SUBJECT=no``).
    """
    store = _require_store(store)
    subject_ref = _require_subject_ref(req.subject_ref, "subject_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    executor_kind = _bounded(req.executor_kind, "executor_kind")
    mechanical_status = _bounded(req.mechanical_status or "running", "mechanical_status")
    execution_id = _mint_id(IdKind.EXECUTION, _MINTED_ID_PREFIXES[IdKind.EXECUTION])

    execution = records.execution(
        execution_id,
        subject_ref.internal_id,
        executor_kind,
        mechanical_status=mechanical_status,
        correlation_id=req.correlation_id,
        executor_execution_ref=req.executor_execution_ref,
        started_at=_iso(req.trusted_time),
        requested_principal=(req.trusted_context.principal.id if req.trusted_context.principal else None),
    )
    execution_ref = make_object_ref(IdKind.EXECUTION, execution_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "create_execution",
            "target": subject_ref.serialize(),
            "executor_kind": executor_kind,
            "mechanical_status": mechanical_status,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=subject_ref,
        expected_revision=req.expected_revision,
        operation="create_execution",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(execution_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(execution)
        tx.add_staged_id(execution_id)
    result = tx.commit()
    return _result("create_execution", subject_ref, result, (execution_ref.serialize(),))


# ---------------------------------------------------------------------------
# record_completion
# ---------------------------------------------------------------------------


def record_completion(store: TransactionStore, req: RecordCompletionRequest) -> TransitionResult:
    """Record an immutable Completion for an Execution of an owning Subject.

    ``Completion`` carries NO direct ``subject_ref`` (``COMPLETION_SUBJECT_REF_FIELD_CREATED=no``);
    the owning Subject is reached transitively via Completion -> Execution ->
    Subject.  Authority root is the owning Subject.
    """
    store = _require_store(store)
    execution_ref = _require_object_ref(req.execution_ref, IdKind.EXECUTION, "execution_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    outcome = _bounded(req.outcome, "outcome")
    resolver = OwningSubjectResolver(store)
    try:
        owning = resolver.owning_subject_of_execution(execution_ref)
    except GraphNotFoundError as exc:
        raise TransitionNotFoundError(f"owning subject of execution not found: {execution_ref.serialize()}") from exc
    owning_ref = make_object_ref(IdKind.SUBJECT, owning.subject_id)
    completion_id = _mint_id(IdKind.COMPLETION, _MINTED_ID_PREFIXES[IdKind.COMPLETION])

    completion = records.completion(
        completion_id,
        execution_ref.internal_id,
        outcome,
        evidence_refs=list(req.evidence_refs or []),
        recorded_at=_iso(req.trusted_time),
    )
    completion_ref = make_object_ref(IdKind.COMPLETION, completion_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "record_completion",
            "execution": execution_ref.serialize(),
            "outcome": outcome,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=owning_ref,
        expected_revision=req.expected_revision,
        operation="record_completion",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(completion_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(completion)
        tx.add_staged_id(completion_id)
    result = tx.commit()
    return _result("record_completion", owning_ref, result, (completion_ref.serialize(),))


# ---------------------------------------------------------------------------
# record_decision
# ---------------------------------------------------------------------------


def record_decision(store: TransactionStore, req: RecordDecisionRequest) -> TransitionResult:
    """Create a durable Materialized Decision owned by exactly one Subject.

    The Decision is a canonical child record, not an independent mutation or
    revision root; it is committed inside the isolated transaction foundation.
    """
    store = _require_store(store)
    subject_ref = _require_subject_ref(req.subject_ref, "subject_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    decision_kind = _bounded(req.decision_kind, "decision_kind")
    statement = _bounded(req.statement, "statement")
    decision_id = _mint_id(IdKind.DECISION, _MINTED_ID_PREFIXES[IdKind.DECISION])

    decision = records.decision(
        decision_id,
        subject_ref.internal_id,
        decision_kind,
        statement,
        target_refs=list(req.target_refs or []),
        evidence_refs=list(req.evidence_refs or []),
        decision_time=_iso(req.trusted_time),
    )
    decision_ref = make_object_ref(IdKind.DECISION, decision_id)
    fingerprint = canonical_fingerprint(
        {
            "operation": "record_decision",
            "target": subject_ref.serialize(),
            "decision_kind": decision_kind,
            "statement": statement,
        }
    )
    tx = SubjectTransaction(
        store,
        subject_ref=subject_ref,
        expected_revision=req.expected_revision,
        operation="record_decision",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        new_state={"state": "advanced"},
        effect_refs=(decision_ref.serialize(),),
    ).begin()
    if not tx.replayed:
        tx.add_record(decision)
        tx.add_staged_id(decision_id)
    result = tx.commit()
    return _result("record_decision", subject_ref, result, (decision_ref.serialize(),))


# ---------------------------------------------------------------------------
# create_followup_subject
# ---------------------------------------------------------------------------


def create_followup_subject(store: TransactionStore, req: CreateFollowupSubjectRequest) -> TransitionResult:
    """Create a new child Subject backed by an exact Materialized Decision.

    Uses the B6 parent + new-child transaction foundation: the child Subject ID
    is minted inside the transaction, the child is created at revision 1, a
    Decision-backed FollowupEdge is created, the parent Subject revision
    advances once, the parent lease is consumed, and everything commits
    atomically.
    """
    store = _require_store(store)
    parent_ref = _require_subject_ref(req.parent_ref, "parent_ref")
    decision_ref = _require_object_ref(req.decision_ref, IdKind.DECISION, "decision_ref")
    lease = _require_lease(req.lease)
    _bounded_id(req.idempotency_key, "idempotency_key")
    child_kind = _bounded(req.child_kind, "child_kind")
    child_sub_kind = _bounded(req.child_sub_kind, "child_sub_kind")

    _resolve_subject(store, parent_ref)
    decision = _resolve_decision(store, decision_ref)

    if decision.subject_ref != parent_ref.internal_id:
        raise TransitionDeniedError(
            f"foreign decision {decision_ref.serialize()} does not belong to parent {parent_ref.serialize()}",
            details={"code": "FOLLOWUP_FOREIGN_DECISION_DENIED"},
        )

    evidence = MaterializedDecisionEvidence.from_decision(
        decision,
        operation="create_followup_subject",
        target=parent_ref,
        expected_revision=req.expected_revision,
        scope=dict(WRITE_SCOPE),
    )

    child_iid = make_id(
        IdKind.SUBJECT,
        mint_subject_value(child_sub_kind, "wid"),
        sub_kind=child_sub_kind,
    )
    edge_id = _mint_id(IdKind.EDGE, _MINTED_ID_PREFIXES[IdKind.EDGE])

    fingerprint = canonical_fingerprint(
        {
            "operation": "create_followup_subject",
            "parent": parent_ref.serialize(),
            "decision": decision_ref.serialize(),
            "expected_revision": req.expected_revision,
            "child_sub_kind": child_sub_kind,
            "rationale": req.rationale,
        }
    )
    tx = ParentChildTransaction(
        store,
        parent_ref=parent_ref,
        expected_parent_revision=req.expected_revision,
        operation="create_followup_subject",
        trusted_context=req.trusted_context,
        requested_scope=_bounded_scope(WRITE_SCOPE),
        lease=lease,
        idempotency_key=req.idempotency_key,
        fingerprint=fingerprint,
        trusted_time=req.trusted_time,
        child_kind=child_kind,
        child_iid=child_iid,
        source_decision=decision,
        decision_evidence=evidence,
        edge_id=edge_id,
        child_creation_context=dict(req.child_creation_context or {}),
        child_id_derivation=req.child_id_derivation or "minted",
        rationale=req.rationale,
    ).begin()
    tx.stage_child().stage_edge()
    result = tx.commit()
    return _result("create_followup_subject", parent_ref, result, (child_iid.to_canonical(),))


__all__ = [
    "CreateExecutionRequest",
    "RecordCompletionRequest",
    "RecordDecisionRequest",
    "CreateFollowupSubjectRequest",
    "TransitionResult",
    "TransitionError",
    "TransitionInvalidError",
    "TransitionDeniedError",
    "TransitionNotFoundError",
    "create_execution",
    "record_completion",
    "record_decision",
    "create_followup_subject",
]
