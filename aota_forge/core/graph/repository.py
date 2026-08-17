"""M3-B3 storage-neutral graph repository abstraction and owning-subject resolver.

Scope (issue #9, lane M3-B3):

* a storage-neutral repository / read abstraction over the canonical records
* an isolated, NON-authoritative in-memory implementation used only for
  fixtures and structural referential consistency checks
* a mechanical owning-Subject resolver with fail-closed semantics
* canonical lookups consume B4 ``ObjectRef`` (``OBJECT_REF_GRAPH_LOOKUP_OWNER=M3-B3``)

This foundation performs NO authority decision and NO semantic selection.  It
is not bound to Git, current pointers, control comments, the Event Log, or any
production lifecycle graph path.  ``LEGACY_GRAPH_INPUT_IS_AUTHORITY=no``,
``GIT_HISTORY_IS_SUBJECT_AUTHORITY=no``, ``EVENT_LOG_IS_SUBJECT_AUTHORITY=no``,
``CURRENT_POINTER_AUTHORITY=no``.

No production graph write path is activated here; the concrete implementation
is a fixture-only, isolated, non-authoritative store
(``ISOLATED_GRAPH_STATE_IS_AUTHORITY=no``).

``OBJECT_REF_IS_AUTHORITY=no``: an ObjectRef identifies a record for lookup; it
never grants authority (``OBJECT_REF_AUTHORITY_SEMANTICS_OWNER=M3-B5``).

Candidate-query extensions (owned by M3-B9):

* ``subjects_by_scope``           — deterministic listing by workflow scope /
  subject-kind discriminator
* ``subjects_matching_semantic_ref`` — deterministic index listing over
  canonical identity values derived from a semantic ref
* ``followup_children_of``        — canonical Decision-backed child lineage

These are deterministic candidate-query primitives only.  The repository never
selects one candidate, never ranks candidates, never uses recency or current
pointers, and never makes a semantic or authority decision
(``GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE=no``;
``GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY=yes``).  Ordering uses only canonical
stable identity fields so candidate lists are reproducible.
"""

from __future__ import annotations

import abc
from typing import Iterator

from aota_forge.core.identity.ids import InternalId
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.errors import ObjectRefError, WrongKindIdError
from aota_forge.core.graph import records

# B9 candidate-query boundary signals.
GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE = False
GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY = True
CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH = True
CANDIDATE_LIST_ORDER_DETERMINISTIC = True


class GraphNotFoundError(LookupError):
    """Deterministic bounded result for an unknown canonical reference."""

    code = "GRAPH_REF_NOT_FOUND"

    def __init__(self, kind: str, value: str) -> None:
        self.kind = kind
        self.value = value
        super().__init__(f"{kind} reference not found: {value!r}")


class GraphReferentialError(ValueError):
    """A canonical referential constraint was violated (fail closed)."""

    code = "GRAPH_REFERENTIAL"


def assert_ref_kind(ref: InternalId, expected_kind: str, label: str) -> InternalId:
    """Fail closed if a canonical reference carries the wrong kind role."""
    if ref.kind != expected_kind:
        raise GraphReferentialError(f"{label} reference has kind {ref.kind!r}, expected {expected_kind!r}")
    return ref


def assert_object_ref_kind(ref: ObjectRef, expected_kind: str, label: str) -> ObjectRef:
    """Fail closed if a typed ObjectRef carries the wrong object kind."""
    if ref.object_kind != expected_kind:
        raise GraphReferentialError(
            f"{label} object ref has kind {ref.object_kind!r}, expected {expected_kind!r}"
        )
    return ref


class GraphRepository(abc.ABC):
    """Storage-neutral read/repository abstraction for canonical graph records.

    Concrete subclasses decide the storage medium.  This form of repository is
    an abstract interface only; it activates no production write path.

    Canonical lookups consume B4 ``ObjectRef`` so that wrong-kind and malformed
    refs are rejected before graph resolution
    (``OBJECT_REF_GRAPH_LOOKUP_INTEGRATED=yes``).
    """

    @abc.abstractmethod
    def store(self, record: "records._AnyRecord") -> None:
        """Materialize (or replace) one canonical record in the store."""

    @abc.abstractmethod
    def workflow(self, ref: ObjectRef) -> records.Workflow:
        ...

    @abc.abstractmethod
    def subject(self, ref: ObjectRef) -> records.Subject:
        ...

    @abc.abstractmethod
    def execution(self, ref: ObjectRef) -> records.Execution:
        ...

    @abc.abstractmethod
    def completion(self, ref: ObjectRef) -> records.Completion:
        ...

    @abc.abstractmethod
    def decision(self, ref: ObjectRef) -> records.Decision:
        ...

    @abc.abstractmethod
    def edge(self, ref: ObjectRef) -> records.FollowupEdge:
        ...

    @abc.abstractmethod
    def lookup(self, ref: ObjectRef) -> "records._AnyRecord":
        """Dispatch a typed ObjectRef to the matching record (fail closed on wrong kind)."""

    # -- deterministic related-record listing ---------------------------------

    @abc.abstractmethod
    def executions_of_subject(self, subject_ref: ObjectRef) -> list[records.Execution]:
        ...

    @abc.abstractmethod
    def decisions_of_subject(self, subject_ref: ObjectRef) -> list[records.Decision]:
        ...

    @abc.abstractmethod
    def completion_of_execution(self, execution_ref: ObjectRef) -> records.Completion | None:
        ...

    @abc.abstractmethod
    def edges(self) -> list[records.FollowupEdge]:
        """All FollowupEdges in deterministic order (parent, child, edge ref)."""

    @abc.abstractmethod
    def subjects(self) -> list[records.Subject]:
        ...

    # -- B9 deterministic candidate-query primitives -------------------------
    # Owned by M3-B9 (subject binding / recovery).  These return candidate
    # LISTS only; they never select, rank, or interpret a candidate.

    @abc.abstractmethod
    def subjects_by_scope(
        self,
        *,
        workflow_ref: InternalId | None = None,
        sub_kind: str | None = None,
    ) -> list[records.Subject]:
        """Deterministic Subject candidates constrained by scope facts.

        ``workflow_ref`` narrows to Subjects whose canonical ``workflow_ref``
        equals the supplied workflow identity (a canonical lineage/scope
        fact).  ``sub_kind`` narrows to a stable subject-kind discriminator.
        Ordering is by canonical stable identity field only.
        """

    @abc.abstractmethod
    def subjects_matching_semantic_ref(
        self,
        semantic_ref: str,
        *,
        sub_kind: str | None = None,
    ) -> list[records.Subject]:
        """Deterministic Subject candidates that canonically match a semantic ref.

        The semantic boundary rejects raw/legacy IDs before this primitive is
        ever consulted; only well-formed semantic public refs reach the graph.
        Matching uses canonical deterministic identity fields (the stable
        subject-kind / identity derivation), never title/digest/recency.
        """

    @abc.abstractmethod
    def followup_children_of(
        self,
        subject_ref: ObjectRef,
        *,
        decision_ref: ObjectRef | None = None,
        candidate_sub_kind: str | None = None,
    ) -> list[records.FollowupEdge]:
        """Deterministic Decision-backed followup child edges of one Subject.

        Returns FollowupEdge candidates grounded in canonical Decision-backed
        lineage (``FOLLOWUP_EDGE_REQUIRES_DECISION_REF=yes``).  Never defaults
        to guesswork or subtree heuristics; order is deterministic by canonical
        child Subject identity.
        """


class OwningSubjectResolver:
    """Mechanical graph/repository-side resolver (M3-B3).

    Resolution map (all via B4 typed ``ObjectRef``):

        Subject ref        -> Subject
        Execution ref      -> owning Subject
        Completion ref     -> owning Subject (via its Execution)
        Decision ref       -> owning Subject
        FollowupEdge ref   -> edge

    ``OWNING_SUBJECT_RESOLVER_USES_B4_OBJECT_REF=yes``,
    ``OWNING_SUBJECT_RESOLVER_PERFORMS_AUTHORITY_DECISION=no`` and
    ``OWNING_SUBJECT_RESOLVER_PERFORMS_SEMANTIC_SELECTION=no``.  Unknown /
    not-found yields a deterministic ``GraphNotFoundError``; wrong-kind and
    malformed ObjectRefs fail closed before graph resolution rather than
    heuristically selecting.
    """

    def __init__(self, repo: GraphRepository) -> None:
        self._repo = repo

    # --- lookup helpers -----------------------------------------------------

    def _subject_by_iid(self, iid: InternalId) -> records.Subject:
        assert_ref_kind(iid, IdKind.SUBJECT, "subject")
        return self._repo.subject(make_object_ref(IdKind.SUBJECT, iid))

    # --- resolution API (ObjectRef-based) -----------------------------------

    def resolve_subject(self, ref: ObjectRef) -> records.Subject:
        assert_object_ref_kind(ref, IdKind.SUBJECT, "subject")
        return self._repo.subject(ref)

    def resolve_execution(self, ref: ObjectRef) -> records.Execution:
        assert_object_ref_kind(ref, IdKind.EXECUTION, "execution")
        return self._repo.execution(ref)

    def resolve_completion(self, ref: ObjectRef) -> records.Completion:
        assert_object_ref_kind(ref, IdKind.COMPLETION, "completion")
        return self._repo.completion(ref)

    def resolve_decision(self, ref: ObjectRef) -> records.Decision:
        assert_object_ref_kind(ref, IdKind.DECISION, "decision")
        return self._repo.decision(ref)

    def resolve_edge(self, ref: ObjectRef) -> records.FollowupEdge:
        assert_object_ref_kind(ref, IdKind.EDGE, "edge")
        return self._repo.edge(ref)

    def owning_subject_of_execution(self, execution_ref: ObjectRef) -> records.Subject:
        assert_object_ref_kind(execution_ref, IdKind.EXECUTION, "execution")
        execution = self._repo.execution(execution_ref)
        return self._subject_by_iid(execution.subject_ref)

    def owning_subject_of_completion(self, completion_ref: ObjectRef) -> records.Subject:
        assert_object_ref_kind(completion_ref, IdKind.COMPLETION, "completion")
        completion = self._repo.completion(completion_ref)
        # Completion has NO direct subject_ref; the owning Subject is reached
        # transitively via the Completion -> Execution -> Subject chain.
        exec_ref = make_object_ref(IdKind.EXECUTION, completion.execution_ref)
        return self.owning_subject_of_execution(exec_ref)

    def owning_subject_of_decision(self, decision_ref: ObjectRef) -> records.Subject:
        assert_object_ref_kind(decision_ref, IdKind.DECISION, "decision")
        decision = self._repo.decision(decision_ref)
        return self._subject_by_iid(decision.subject_ref)

    # --- derived relations --------------------------------------------------

    def executions_of(self, subject_ref: ObjectRef) -> list[records.Execution]:
        assert_object_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        return self._repo.executions_of_subject(subject_ref)

    def decisions_of(self, subject_ref: ObjectRef) -> list[records.Decision]:
        assert_object_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        return self._repo.decisions_of_subject(subject_ref)


class InMemoryGraphRepository(GraphRepository):
    """ISOLATED, NON-AUTHORITATIVE, deterministic in-memory fixture store.

    Used only by M3-B3/B34 fixtures to exercise the record foundation.  It is in
    no way production lifecycle graph state (``ISOLATED_GRAPH_STATE_IS_AUTHORITY=no``)
    and activates no runtime mutation wiring.
    """

    def __init__(self, *, enforce_referential: bool = True) -> None:
        self._workflows: dict[str, records.Workflow] = {}
        self._subjects: dict[str, records.Subject] = {}
        self._executions: dict[str, records.Execution] = {}
        self._completions: dict[str, records.Completion] = {}
        self._decisions: dict[str, records.Decision] = {}
        self._edges: dict[str, records.FollowupEdge] = {}
        self._enforce_referential = enforce_referential
        self._writing = True
        self._stage: list["records._AnyRecord"] = []

    # -- write path (isolated, non-authoritative) ------------------------------

    def store(self, record: "records._AnyRecord") -> None:
        self._stage.append(record)
        if not self._enforce_referential:
            self._flush()
            return
        self._check_structural(record)
        self._flush()

    def _flush(self) -> None:
        while self._stage:
            record = self._stage.pop(0)
            self._put(record)

    def _put(self, record: "records._AnyRecord") -> None:
        if isinstance(record, records.Workflow):
            self._workflows[record.workflow_id.value] = record
        elif isinstance(record, records.Subject):
            self._subjects[record.subject_id.value] = record
        elif isinstance(record, records.Execution):
            self._executions[record.execution_id.value] = record
        elif isinstance(record, records.Completion):
            self._completions[record.completion_id.value] = record
        elif isinstance(record, records.Decision):
            self._decisions[record.decision_id.value] = record
        elif isinstance(record, records.FollowupEdge):
            self._edges[record.edge_id.value] = record
        else:
            raise TypeError(f"unknown canonical record type: {type(record).__name__}")

    # --- structural referential consistency (B3) ------------------------------

    def _check_structural(self, record: "records._AnyRecord") -> None:
        if isinstance(record, records.Subject):
            if record.workflow_ref is not None:
                if record.workflow_ref.value not in self._workflows:
                    raise GraphReferentialError(
                        f"Subject {record.subject_id.value!r} references unknown Workflow "
                        f"{record.workflow_ref.value!r}"
                    )
        elif isinstance(record, records.Execution):
            if record.subject_ref.value not in self._subjects:
                raise GraphReferentialError(
                    f"Execution {record.execution_id.value!r} references unknown Subject "
                    f"{record.subject_ref.value!r}; every Execution owns exactly one Subject"
                )
        elif isinstance(record, records.Completion):
            if record.execution_ref.value not in self._executions:
                raise GraphReferentialError(
                    f"Completion {record.completion_id.value!r} references unknown Execution "
                    f"{record.execution_ref.value!r} (no orphan Completion allowed)"
                )
        elif isinstance(record, records.Decision):
            if record.subject_ref.value not in self._subjects:
                raise GraphReferentialError(
                    f"Decision {record.decision_id.value!r} references unknown Subject "
                    f"{record.subject_ref.value!r}; every Decision is owned by exactly one Subject"
                )
        elif isinstance(record, records.FollowupEdge):
            if record.parent_subject_ref.value not in self._subjects:
                raise GraphReferentialError(
                    f"FollowupEdge {record.edge_id.value!r} references unknown parent Subject "
                    f"{record.parent_subject_ref.value!r}"
                )
            if record.child_subject_ref.value not in self._subjects:
                raise GraphReferentialError(
                    f"FollowupEdge {record.edge_id.value!r} references unknown child Subject "
                    f"{record.child_subject_ref.value!r}"
                )
            if record.source_decision_ref.value not in self._decisions:
                raise GraphReferentialError(
                    f"FollowupEdge {record.edge_id.value!r} references missing Decision "
                    f"{record.source_decision_ref.value!r}; FollowupEdge requires a source Decision"
                )

    # -- reads (ObjectRef-based canonical lookups) ----------------------------

    @staticmethod
    def _iid_of(ref: ObjectRef) -> InternalId:
        if not isinstance(ref, ObjectRef):
            raise GraphReferentialError(
                f"canonical lookup requires a B4 ObjectRef, got {type(ref).__name__}"
            )
        return ref.internal_id

    def _get(self, store: dict, ref: ObjectRef, kind: str):
        iid = self._iid_of(ref)
        item = store.get(iid.value)
        if item is None:
            raise GraphNotFoundError(kind, iid.value)
        return item

    def workflow(self, ref: ObjectRef) -> records.Workflow:
        assert_object_ref_kind(ref, IdKind.WORKFLOW, "workflow")
        return self._get(self._workflows, ref, "workflow")

    def subject(self, ref: ObjectRef) -> records.Subject:
        assert_object_ref_kind(ref, IdKind.SUBJECT, "subject")
        return self._get(self._subjects, ref, "subject")

    def execution(self, ref: ObjectRef) -> records.Execution:
        assert_object_ref_kind(ref, IdKind.EXECUTION, "execution")
        return self._get(self._executions, ref, "execution")

    def completion(self, ref: ObjectRef) -> records.Completion:
        assert_object_ref_kind(ref, IdKind.COMPLETION, "completion")
        return self._get(self._completions, ref, "completion")

    def decision(self, ref: ObjectRef) -> records.Decision:
        assert_object_ref_kind(ref, IdKind.DECISION, "decision")
        return self._get(self._decisions, ref, "decision")

    def edge(self, ref: ObjectRef) -> records.FollowupEdge:
        assert_object_ref_kind(ref, IdKind.EDGE, "edge")
        return self._get(self._edges, ref, "edge")

    def lookup(self, ref: ObjectRef) -> "records._AnyRecord":
        """Dispatch a typed ObjectRef to the matching record (fail closed)."""
        kind = ref.object_kind
        if kind == IdKind.WORKFLOW:
            return self.workflow(ref)
        if kind == IdKind.SUBJECT:
            return self.subject(ref)
        if kind == IdKind.EXECUTION:
            return self.execution(ref)
        if kind == IdKind.COMPLETION:
            return self.completion(ref)
        if kind == IdKind.DECISION:
            return self.decision(ref)
        if kind == IdKind.EDGE:
            return self.edge(ref)
        raise GraphReferentialError(f"unknown object kind for lookup: {kind!r}")

    def executions_of_subject(self, subject_ref: ObjectRef) -> list[records.Execution]:
        assert_object_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        target = subject_ref.internal_id.value
        results = [e for e in self._executions.values() if e.subject_ref.value == target]
        return sorted(results, key=lambda e: e.execution_id.value)

    def decisions_of_subject(self, subject_ref: ObjectRef) -> list[records.Decision]:
        assert_object_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        target = subject_ref.internal_id.value
        results = [d for d in self._decisions.values() if d.subject_ref.value == target]
        return sorted(results, key=lambda d: d.decision_id.value)

    def completion_of_execution(self, execution_ref: ObjectRef) -> records.Completion | None:
        assert_object_ref_kind(execution_ref, IdKind.EXECUTION, "execution")
        target = execution_ref.internal_id.value
        matches = [c for c in self._completions.values() if c.execution_ref.value == target]
        if not matches:
            return None
        if len(matches) > 1:
            raise GraphReferentialError("multiple Completions reference one Execution (fail closed)")
        return matches[0]

    def edges(self) -> list[records.FollowupEdge]:
        return sorted(
            self._edges.values(),
            key=lambda e: (e.parent_subject_ref.value, e.child_subject_ref.value, e.edge_id.value),
        )

    def subjects(self) -> list[records.Subject]:
        return sorted(self._subjects.values(), key=lambda s: s.subject_id.value)

    # -- B9 deterministic candidate-query primitives (owned by M3-B9) --------

    def subjects_by_scope(
        self,
        *,
        workflow_ref: InternalId | None = None,
        sub_kind: str | None = None,
    ) -> list[records.Subject]:
        """Deterministic candidate listing constrained by canonical scope facts."""
        results = []
        for subject in self._subjects.values():
            if workflow_ref is not None and subject.workflow_ref != workflow_ref:
                continue
            if sub_kind is not None and subject.subject_id.sub_kind != sub_kind:
                continue
            results.append(subject)
        return sorted(results, key=lambda s: s.subject_id.value)

    def subjects_matching_semantic_ref(
        self,
        semantic_ref: str,
        *,
        sub_kind: str | None = None,
        identity_values: tuple[str, ...] = (),
    ) -> list[records.Subject]:
        """Deterministic candidate listing over canonical stable identity facts.

        ``identity_values`` are canonical stable identity values derived from
        the semantic public ref by the binding layer (B9).  When provided, only
        Subjects whose canonical identity exactly equals one of those values
        are candidates.  If ``identity_values`` is empty, only an exact match
        between the semantic ref and a canonical identity value is considered;
        the repository never invents or heuristically interprets an identity
        for an unknown value.  Ordering is by canonical subject identity.
        """
        exact = tuple(identity_values) if identity_values else (semantic_ref,)
        results = []
        for subject in self._subjects.values():
            if sub_kind is not None and subject.subject_id.sub_kind != sub_kind:
                continue
            if subject.subject_id.value not in exact:
                continue
            results.append(subject)
        return sorted(results, key=lambda s: s.subject_id.value)

    def followup_children_of(
        self,
        subject_ref: ObjectRef,
        *,
        decision_ref: ObjectRef | None = None,
        candidate_sub_kind: str | None = None,
    ) -> list[records.FollowupEdge]:
        """Deterministic Decision-backed FollowupEdge candidates of one Subject."""
        assert_object_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        target = subject_ref.internal_id.value
        decision_value = decision_ref.internal_id.value if decision_ref is not None else None
        results = []
        for edge in self._edges.values():
            if edge.parent_subject_ref.value != target:
                continue
            if decision_value is not None and edge.source_decision_ref.value != decision_value:
                continue
            if candidate_sub_kind is not None:
                child = self._subjects.get(edge.child_subject_ref.value)
                if child is None or child.subject_id.sub_kind != candidate_sub_kind:
                    continue
            results.append(edge)
        return sorted(
            results,
            key=lambda e: (e.child_subject_ref.value, e.edge_id.value),
        )

    def __iter__(self) -> Iterator["records._AnyRecord"]:
        yield from sorted(self._workflows.values(), key=lambda r: r.workflow_id.value)
        yield from sorted(self._subjects.values(), key=lambda r: r.subject_id.value)
        yield from sorted(self._executions.values(), key=lambda r: r.execution_id.value)
        yield from sorted(self._completions.values(), key=lambda r: r.completion_id.value)
        yield from sorted(self._decisions.values(), key=lambda r: r.decision_id.value)
        yield from self.edges()
