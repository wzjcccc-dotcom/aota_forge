"""M3-B3 storage-neutral graph repository abstraction and owning-subject resolver.

Scope (issue #9, lane M3-B3):

* a storage-neutral repository / read abstraction over the canonical records
* an isolated, NON-authoritative in-memory implementation used only for
  fixtures and structural referential consistency checks
* a mechanical owning-Subject resolver with fail-closed semantics

This foundation performs NO authority decision and NO semantic selection.  It
is not bound to Git, current pointers, control comments, the Event Log, or any
production lifecycle graph path.  ``LEGACY_GRAPH_INPUT_IS_AUTHORITY=no``,
``GIT_HISTORY_IS_SUBJECT_AUTHORITY=no``, ``EVENT_LOG_IS_SUBJECT_AUTHORITY=no``,
``CURRENT_POINTER_AUTHORITY=no``.

No production graph write path is activated here; the concrete implementation
is a fixture-only, isolated, non-authoritative store
(``ISOLATED_GRAPH_STATE_IS_AUTHORITY=no``).
"""

from __future__ import annotations

import abc
from typing import Iterator

from aota_forge.core.graph.ids import CanonicalId, IdKind
from aota_forge.core.graph import records


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


class GraphRepository(abc.ABC):
    """Storage-neutral read/repository abstraction for canonical graph records.

    Concrete subclasses decide the storage medium.  This form of repository is
    an abstract interface only; it activates no production write path.
    """

    @abc.abstractmethod
    def store(self, record: "records._AnyRecord") -> None:
        """Materialize (or replace) one canonical record in the store."""

    @abc.abstractmethod
    def workflow(self, ref: CanonicalId) -> records.Workflow:
        ...

    @abc.abstractmethod
    def subject(self, ref: CanonicalId) -> records.Subject:
        ...

    @abc.abstractmethod
    def execution(self, ref: CanonicalId) -> records.Execution:
        ...

    @abc.abstractmethod
    def completion(self, ref: CanonicalId) -> records.Completion:
        ...

    @abc.abstractmethod
    def decision(self, ref: CanonicalId) -> records.Decision:
        ...

    @abc.abstractmethod
    def edge(self, ref: CanonicalId) -> records.FollowupEdge:
        ...

    # -- deterministic related-record listing ---------------------------------

    @abc.abstractmethod
    def executions_of_subject(self, subject_ref: CanonicalId) -> list[records.Execution]:
        ...

    @abc.abstractmethod
    def decisions_of_subject(self, subject_ref: CanonicalId) -> list[records.Decision]:
        ...

    @abc.abstractmethod
    def completion_of_execution(self, execution_ref: CanonicalId) -> records.Completion | None:
        ...

    @abc.abstractmethod
    def edges(self) -> list[records.FollowupEdge]:
        """All FollowupEdges in deterministic order (parent, child, edge ref)."""

    @abc.abstractmethod
    def subjects(self) -> list[records.Subject]:
        ...


class OwningSubjectResolver:
    """Mechanical graph/repository-side resolver (M3-B3).

    Resolution map
    Inventory:
        Subject ref        -> Subject
        Execution ref      -> owning Subject
        Completion ref     -> owning Subject (via its Execution)
        Decision ref       -> owning Subject

    ``OWNING_SUBJECT_RESOLVER_PERFORMS_AUTHORITY_DECISION=no`` and
    ``OWNING_SUBJECT_RESOLVER_PERFORMS_SEMANTIC_SELECTION=no``.  Unknown /
    not-found yields a deterministic ``GraphNotFoundError``; ambiguous internal
    canonical identity fails closed rather than heuristically selecting.
    """

    def __init__(self, repo: GraphRepository) -> None:
        self._repo = repo

    # --- lookup helpers -----------------------------------------------------

    def _subject(self, ref: CanonicalId) -> records.Subject:
        assert_ref_kind(ref, IdKind.SUBJECT, "subject")
        return self._repo.subject(ref)

    # --- resolution API -----------------------------------------------------

    def resolve_subject(self, ref: CanonicalId) -> records.Subject:
        return self._subject(assert_ref_kind(ref, IdKind.SUBJECT, "subject"))

    def owning_subject_of_execution(self, execution_ref: CanonicalId) -> records.Subject:
        assert_ref_kind(execution_ref, IdKind.EXECUTION, "execution")
        execution = self._repo.execution(execution_ref)
        return self._subject(execution.subject_ref)

    def owning_subject_of_completion(self, completion_ref: CanonicalId) -> records.Subject:
        assert_ref_kind(completion_ref, IdKind.COMPLETION, "completion")
        completion = self._repo.completion(completion_ref)
        # Completion has NO direct subject_ref; the owning Subject is reached
        # transitively via the Completion -> Execution -> Subject chain.
        return self.owning_subject_of_execution(completion.execution_ref)

    def owning_subject_of_decision(self, decision_ref: CanonicalId) -> records.Subject:
        assert_ref_kind(decision_ref, IdKind.DECISION, "decision")
        decision = self._repo.decision(decision_ref)
        return self._subject(decision.subject_ref)

    # --- derived relations --------------------------------------------------

    def executions_of(self, subject_ref: CanonicalId) -> list[records.Execution]:
        assert_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        return self._repo.executions_of_subject(subject_ref)

    def decisions_of(self, subject_ref: CanonicalId) -> list[records.Decision]:
        assert_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        return self._repo.decisions_of_subject(subject_ref)


def assert_ref_kind(ref: CanonicalId, expected_kind: str, label: str) -> CanonicalId:
    """Fail closed if a canonical reference carries the wrong kind role."""
    if ref.kind != expected_kind:
        raise GraphReferentialError(f"{label} reference has kind {ref.kind!r}, expected {expected_kind!r}")
    return ref


class InMemoryGraphRepository(GraphRepository):
    """ISOLATED, NON-AUTHORITATIVE, deterministic in-memory fixture store.

    Used only by M3-B3 fixtures to exercise the record foundation.  It is in no
    way production lifecycle graph state (``ISOLATED_GRAPH_STATE_IS_AUTHORITY=no``)
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

    # -- reads ----------------------------------------------------------------

    def _get(self, store: dict, ref: CanonicalId, kind: str):
        item = store.get(ref.value)
        if item is None:
            raise GraphNotFoundError(kind, ref.value)
        return item

    def workflow(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.WORKFLOW, "workflow")
        return self._get(self._workflows, ref, "workflow")

    def subject(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.SUBJECT, "subject")
        return self._get(self._subjects, ref, "subject")

    def execution(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.EXECUTION, "execution")
        return self._get(self._executions, ref, "execution")

    def completion(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.COMPLETION, "completion")
        return self._get(self._completions, ref, "completion")

    def decision(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.DECISION, "decision")
        return self._get(self._decisions, ref, "decision")

    def edge(self, ref: CanonicalId):
        assert_ref_kind(ref, IdKind.EDGE, "edge")
        return self._get(self._edges, ref, "edge")

    def executions_of_subject(self, subject_ref: CanonicalId) -> list[records.Execution]:
        assert_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        results = [e for e in self._executions.values() if e.subject_ref.value == subject_ref.value]
        return sorted(results, key=lambda e: e.execution_id.value)

    def decisions_of_subject(self, subject_ref: CanonicalId) -> list[records.Decision]:
        assert_ref_kind(subject_ref, IdKind.SUBJECT, "subject")
        results = [d for d in self._decisions.values() if d.subject_ref.value == subject_ref.value]
        return sorted(results, key=lambda d: d.decision_id.value)

    def completion_of_execution(self, execution_ref: CanonicalId) -> records.Completion | None:
        assert_ref_kind(execution_ref, IdKind.EXECUTION, "execution")
        matches = [c for c in self._completions.values() if c.execution_ref.value == execution_ref.value]
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

    def __iter__(self) -> Iterator["records._AnyRecord"]:
        yield from sorted(self._workflows.values(), key=lambda r: r.workflow_id.value)
        yield from sorted(self._subjects.values(), key=lambda r: r.subject_id.value)
        yield from sorted(self._executions.values(), key=lambda r: r.execution_id.value)
        yield from sorted(self._completions.values(), key=lambda r: r.completion_id.value)
        yield from sorted(self._decisions.values(), key=lambda r: r.decision_id.value)
        yield from self.edges()