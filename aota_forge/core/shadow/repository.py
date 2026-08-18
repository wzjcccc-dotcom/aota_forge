"""M3-B11 isolated non-authoritative shadow repository abstraction.

Provides storage-neutral and in-memory shadow repository implementations:
- Isolated from production canonical stores (B11_PRODUCTION_CANONICAL_STORE_REUSE_ALLOWED=no).
- Never aliases production repository instances (SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY=no).
- Explicit namespace marker required (SHADOW_NAMESPACE_EXPLICIT=yes).
- Non-authoritative: exposes zero production mutation or subject authority.
- Supports deterministic snapshotting, readback, reset, and rebuild.
"""

from __future__ import annotations

import abc
from typing import Iterator

from aota_forge.core.graph import records
from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    GraphReferentialError,
    GraphRepository,
    assert_object_ref_kind,
    assert_ref_kind,
)
from aota_forge.core.graph.serialization import round_trip, serialize, to_bytes, to_dict
from aota_forge.core.identity.ids import InternalId
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.shadow.model import (
    SHADOW_NAMESPACE_DEFAULTS_TO_PRODUCTION,
    SHADOW_NAMESPACE_EXPLICIT,
    SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY,
    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY,
    ShadowNamespace,
    ShadowStateMetadata,
)
from aota_forge.core.shadow.snapshot import ShadowSnapshot


class ShadowRepository(GraphRepository, abc.ABC):
    """Abstract storage-neutral interface for isolated shadow graph repositories."""

    @property
    @abc.abstractmethod
    def namespace(self) -> ShadowNamespace:
        """The explicit non-production namespace of this shadow store."""

    @abc.abstractmethod
    def snapshot(self, metadata: ShadowStateMetadata | None = None) -> ShadowSnapshot:
        """Capture an immutable deterministic snapshot of current shadow state."""

    @abc.abstractmethod
    def clear(self) -> None:
        """Discard all records in this shadow repository (reset)."""

    @abc.abstractmethod
    def record_count(self) -> int:
        """Total number of canonical records currently held."""

    @abc.abstractmethod
    def record_counts_by_kind(self) -> dict[str, int]:
        """Record count grouped by canonical kind."""

    @property
    @abc.abstractmethod
    def write_count(self) -> int:
        """Total number of write/store operations performed."""


def deserialize_record(data: dict, kind: str) -> records._AnyRecord:
    """Deserialize a canonical dict back into a typed graph record."""
    from aota_forge.core.identity.ids import parse_internal_id

    if kind == "Workflow":
        return records.workflow(
            workflow_id=parse_internal_id(data["workflow_id"]),
            semantic_intent=data.get("semantic_intent", ""),
            creation_context=data.get("creation_context", {}),
            goal=data.get("goal", ""),
            scope=data.get("scope", {}),
            context_references=data.get("context_references", []),
            scope_annotations=data.get("scope_annotations", []),
            non_semantic_metadata=data.get("non_semantic_metadata", {}),
        )
    elif kind == "Subject":
        wf_ref = parse_internal_id(data["workflow_ref"]) if data.get("workflow_ref") else None
        return records.subject(
            subject_id=parse_internal_id(data["subject_id"]),
            kind=data["kind"],
            workflow_ref=wf_ref,
            mechanical_state=data.get("mechanical_state", {}),
            id_derivation=data.get("id_derivation", "deterministic"),
            creation_context=data.get("creation_context", {}),
        )
    elif kind == "Execution":
        return records.execution(
            execution_id=parse_internal_id(data["execution_id"]),
            subject_ref=parse_internal_id(data["subject_ref"]),
            executor_kind=data.get("executor_kind", "unknown"),
            mechanical_status=data.get("mechanical_status", "running"),
            executor_execution_ref=data.get("executor_execution_ref"),
            correlation_id=data.get("correlation_id"),
            started_at=data.get("started_at"),
            requested_principal=data.get("requested_principal"),
        )
    elif kind == "Completion":
        return records.completion(
            completion_id=parse_internal_id(data["completion_id"]),
            execution_ref=parse_internal_id(data["execution_ref"]),
            outcome=data.get("outcome", "unknown"),
            evidence_refs=data.get("evidence_refs", []),
            recorded_at=data.get("recorded_at"),
        )
    elif kind == "Decision":
        return records.decision(
            decision_id=parse_internal_id(data["decision_id"]),
            subject_ref=parse_internal_id(data["subject_ref"]),
            decision_kind=data.get("decision_kind", "unknown"),
            statement=data.get("statement", ""),
            target_refs=data.get("target_refs", []),
            decision_time=data.get("decision_time"),
            evidence_refs=data.get("evidence_refs", []),
        )
    elif kind == "FollowupEdge":
        return records.followup_edge(
            edge_id=parse_internal_id(data["edge_id"]),
            parent_subject_ref=parse_internal_id(data["parent_subject_ref"]),
            child_subject_ref=parse_internal_id(data["child_subject_ref"]),
            source_decision_ref=parse_internal_id(data["source_decision_ref"]),
            rationale=data.get("rationale"),
            created_at=data.get("created_at"),
        )
    raise ValueError(f"Unknown record kind for deserialization: {kind!r}")


class InMemoryShadowRepository(ShadowRepository):
    """Isolated, non-authoritative in-memory shadow repository.

    Never aliases or shares mutable storage with production canonical stores.
    """

    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY = False
    SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY = False
    SHADOW_NAMESPACE_EXPLICIT = True

    def __init__(
        self,
        namespace: ShadowNamespace | str,
        *,
        enforce_referential: bool = True,
    ) -> None:
        if isinstance(namespace, str):
            self._namespace = ShadowNamespace(namespace)
        elif isinstance(namespace, ShadowNamespace):
            self._namespace = namespace
        else:
            raise TypeError(
                f"namespace must be a ShadowNamespace or str, got {type(namespace).__name__}"
            )

        self._workflows: dict[str, records.Workflow] = {}
        self._subjects: dict[str, records.Subject] = {}
        self._executions: dict[str, records.Execution] = {}
        self._completions: dict[str, records.Completion] = {}
        self._decisions: dict[str, records.Decision] = {}
        self._edges: dict[str, records.FollowupEdge] = {}
        self._enforce_referential = enforce_referential
        self._write_count = 0
        self._stage: list[records._AnyRecord] = []
        self._metadata: ShadowStateMetadata | None = None

    @property
    def namespace(self) -> ShadowNamespace:
        return self._namespace

    @property
    def write_count(self) -> int:
        return self._write_count

    def is_production_aliased(self) -> bool:
        return False

    def clear(self) -> None:
        self._workflows.clear()
        self._subjects.clear()
        self._executions.clear()
        self._completions.clear()
        self._decisions.clear()
        self._edges.clear()
        self._stage.clear()
        self._metadata = None

    def record_count(self) -> int:
        return (
            len(self._workflows)
            + len(self._subjects)
            + len(self._executions)
            + len(self._completions)
            + len(self._decisions)
            + len(self._edges)
        )

    def record_counts_by_kind(self) -> dict[str, int]:
        return {
            "workflows": len(self._workflows),
            "subjects": len(self._subjects),
            "executions": len(self._executions),
            "completions": len(self._completions),
            "decisions": len(self._decisions),
            "edges": len(self._edges),
            "total": self.record_count(),
        }

    # -- write path (isolated shadow storage) ---------------------------------

    def store(self, record: records._AnyRecord) -> None:
        self._stage.append(record)
        if self._enforce_referential:
            self._check_structural(record)
        self._flush()

    def _flush(self) -> None:
        while self._stage:
            record = self._stage.pop(0)
            self._put(record)
            self._write_count += 1

    def _put(self, record: records._AnyRecord) -> None:
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

    # -- structural referential consistency (B3 reused) ------------------------

    def _check_structural(self, record: records._AnyRecord) -> None:
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

    # -- reads (ObjectRef-based lookups) -------------------------------------

    @staticmethod
    def _iid_of(ref: ObjectRef) -> InternalId:
        if not isinstance(ref, ObjectRef):
            raise GraphReferentialError(
                f"canonical lookup requires a B4 ObjectRef, got {type(ref).__name__}"
            )
        return ref.internal_id

    def _get(self, store_dict: dict, ref: ObjectRef, kind: str):
        iid = self._iid_of(ref)
        item = store_dict.get(iid.value)
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

    def lookup(self, ref: ObjectRef) -> records._AnyRecord:
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

    # -- B9 deterministic candidate-query primitives -------------------------

    def subjects_by_scope(
        self,
        *,
        workflow_ref: InternalId | None = None,
        sub_kind: str | None = None,
    ) -> list[records.Subject]:
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

    def snapshot(self, metadata: ShadowStateMetadata | None = None) -> ShadowSnapshot:
        meta = metadata or self._metadata
        return ShadowSnapshot.from_repository(self, metadata=meta)

    def restore_from_snapshot(self, snap: ShadowSnapshot) -> None:
        self.clear()
        for w_dict in snap.workflows:
            self.store(deserialize_record(w_dict, "Workflow"))
        for s_dict in snap.subjects:
            self.store(deserialize_record(s_dict, "Subject"))
        for e_dict in snap.executions:
            self.store(deserialize_record(e_dict, "Execution"))
        for c_dict in snap.completions:
            self.store(deserialize_record(c_dict, "Completion"))
        for d_dict in snap.decisions:
            self.store(deserialize_record(d_dict, "Decision"))
        for ed_dict in snap.edges:
            self.store(deserialize_record(ed_dict, "FollowupEdge"))
        self._metadata = ShadowStateMetadata(
            namespace=snap.namespace,
            transformation_version=snap.transformation_version,
            rebuild_fingerprint=snap.rebuild_fingerprint,
            source_manifest_fingerprint=snap.source_manifest_fingerprint,
            materialized_at=snap.metadata.get("materialized_at", ""),
            is_authoritative=False,
            is_production=False,
            is_shadow=True,
            record_counts=dict(snap.record_counts),
        )

    def __iter__(self) -> Iterator[records._AnyRecord]:
        yield from sorted(self._workflows.values(), key=lambda r: r.workflow_id.value)
        yield from sorted(self._subjects.values(), key=lambda r: r.subject_id.value)
        yield from sorted(self._executions.values(), key=lambda r: r.execution_id.value)
        yield from sorted(self._completions.values(), key=lambda r: r.completion_id.value)
        yield from sorted(self._decisions.values(), key=lambda r: r.decision_id.value)
        yield from self.edges()


__all__ = [
    "ShadowRepository",
    "InMemoryShadowRepository",
]
