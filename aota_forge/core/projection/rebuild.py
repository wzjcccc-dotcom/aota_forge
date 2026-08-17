"""M3-B8 Projection Reconstruction — deterministic rebuild service.

Rebuilds a B8 projection model PURELY from canonical AOTA Forge graph state via
the accepted read-only interfaces (``GraphRepository`` + ``OwningSubjectResolver``
+ canonical records + B6 Subject aggregate revision).  It performs ZERO graph
writes (``PROJECTION_GRAPH_WRITE_COUNT=0``), never starts a transaction, never
takes a capability lease, and never consults an Authority Engine for a read.

Determinism contract: same canonical graph state always yields a
byte/field-equivalent canonical projection
(``PROJECTION_FULL_REBUILD_IMPLEMENTED=yes``,
``PROJECTION_REBUILD_DETERMINISTIC=yes``).  No external mutable state may alter
the result.

The derived ``current_execution`` is selected only from canonical deterministic
graph mechanics (the frozen canonical ``started_at`` field); ties expose bounded
``AMBIGUOUS_DERIVED_STATE`` instead of guessing
(``PROJECTION_HEURISTIC_SELECTION_ALLOWED=no``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    GraphReferentialError,
    GraphRepository,
    OwningSubjectResolver,
)
from aota_forge.core.identity.errors import ObjectRefError, WrongKindIdError
from aota_forge.core.identity.kinds import IdKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.revision import revision_number_of

from aota_forge.core.projection.model import (
    CompletionProjection,
    DecisionProjection,
    ExecutionProjection,
    FollowupProjection,
    ProjectionResultCode,
    SubjectProjection,
)
from aota_forge.core.projection.status import ProjectionRevision


class ProjectionError(Exception):
    """Base class for bounded B8-local projection failures.

    A projection failure never affects canonical graph truth: it is a local,
    bounded result only (``PROJECTION_FAILURE_CAN_CORRUPT_CANONICAL_GRAPH=no``,
    ``PROJECTION_FAILURE_MUTATES_SUBJECT_REVISION=no``).
    """

    code = ProjectionResultCode.REFERENTIAL_ERROR


@dataclass(frozen=True)
class ProjectionResult:
    """Bounded B8-local projection result (never writes the graph)."""

    code: ProjectionResultCode
    projection: SubjectProjection | None = None
    revision: ProjectionRevision | None = None
    error_detail: str | None = None


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_revision(repo: GraphRepository, subject_ref: ObjectRef, subject) -> tuple[int, str]:
    """Read the accepted B6 Subject aggregate revision (number + token).

    The number is read from the canonical Subject record's mechanical revision
    field via the accepted B6 ``revision_number_of`` interface; the token is
    read from the B6 store when available, else derived deterministically from
    the canonical Subject record (``PROJECTION_REVISION_SOURCE=Subject_aggregate_revision``).
    """
    number = revision_number_of(subject)
    token = ""
    current_revision = getattr(repo, "current_revision", None)
    if callable(current_revision):
        try:
            token = current_revision(subject_ref).revision_token
        except Exception:  # noqa: BLE001 - bounded fallback, never authority
            token = ""
    if not token:
        token = _digest({"subject_revision_token": subject.canonical_fields()})
    return number, token


def _parse_started_at(text: str | None) -> datetime | None:
    """Parse the frozen canonical ``started_at`` field, or None if absent/bad."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _derive_lifecycle_status(executions: tuple[ExecutionProjection, ...], subject) -> str:
    base = subject.mechanical_state.get("state") or "open"
    if any(e.completion is not None for e in executions):
        return "completed"
    if executions:
        return "running"
    return base


def _legacy_compat(subject, derived_status: str) -> dict:
    """Comparison-only legacy ``current_*`` view derived only where canonical
    graph semantics support a deterministic equivalent.

    ``LEGACY_COMPATIBILITY_IS_COMPARISON_ONLY=yes``; these values are NEVER
    consumed back into canonical graph truth
    (``LEGACY_CURRENT_POINTER_INPUT_TO_PROJECTION_AUTHORITY=no``).
    """
    state = subject.mechanical_state or {}
    compat = {
        "current_status": derived_status,
        "current_milestone": state.get("milestone"),
        "current_blocker": state.get("blocker"),
        "handoff_state": state.get("handoff_state"),
    }
    bounded_fields = {}
    for key, value in state.items():
        if isinstance(value, (str, int, float, bool)) and len(str(key)) <= 64 and len(str(value)) <= 256:
            bounded_fields[key] = value
    compat["current_fields"] = bounded_fields
    return compat


class ProjectionRebuildService:
    """Executor-neutral, storage-neutral deterministic projection rebuild.

    Read-only from the canonical graph's perspective: it consumes only
    ``GraphRepository`` reads and ``OwningSubjectResolver`` resolution.
    """

    def __init__(self, repo: GraphRepository) -> None:
        if not isinstance(repo, GraphRepository):
            raise ProjectionError("projection rebuild requires a GraphRepository")
        self._repo = repo
        self._resolver = OwningSubjectResolver(repo)

    # -- entry point ---------------------------------------------------------

    def rebuild(self, subject_ref: ObjectRef) -> ProjectionResult:
        """Rebuild a deterministic projection for one canonical Subject.

        Input is a B4 ``ObjectRef`` of SUBJECT kind.  Wrong-kind or malformed
        refs fail closed (``INVALID_REFERENCE``) before graph resolution.
        """
        if not isinstance(subject_ref, ObjectRef):
            return ProjectionResult(
                code=ProjectionResultCode.INVALID_REFERENCE,
                error_detail="projection rebuild requires a B4 ObjectRef",
            )
        if subject_ref.object_kind != IdKind.SUBJECT:
            return ProjectionResult(
                code=ProjectionResultCode.INVALID_REFERENCE,
                error_detail=f"projection requires a SUBJECT ObjectRef, got {subject_ref.object_kind!r}",
            )
        try:
            subject = self._repo.subject(subject_ref)
        except GraphNotFoundError:
            return ProjectionResult(
                code=ProjectionResultCode.NOT_FOUND,
                error_detail=f"subject not found: {subject_ref.serialize()}",
            )
        except (GraphReferentialError, ObjectRefError, WrongKindIdError):
            return ProjectionResult(
                code=ProjectionResultCode.INVALID_REFERENCE,
                error_detail="invalid subject reference",
            )

        return self._build(subject_ref, subject)

    # -- construction --------------------------------------------------------

    def _build(self, subject_ref: ObjectRef, subject) -> ProjectionResult:
        subject_iid = subject_ref.internal_id

        executions = self._project_executions(subject_ref, subject_iid)
        if isinstance(executions, ProjectionResult):
            return executions
        exec_projs, current = executions

        decisions = self._project_decisions(subject_ref, subject_iid)
        if isinstance(decisions, ProjectionResult):
            return decisions

        followups = self._project_followups(subject_iid)
        if isinstance(followups, ProjectionResult):
            return followups

        derived_status = _derive_lifecycle_status(exec_projs, subject)
        number, token = _read_revision(self._repo, subject_ref, subject)

        projection = SubjectProjection(
            subject_ref=subject_ref.serialize(),
            subject_revision=number,
            revision_token=token,
            workflow_ref=subject.workflow_ref.to_canonical() if subject.workflow_ref else None,
            kind=subject.kind,
            executions=tuple(exec_projs),
            current_execution=current,
            decisions=tuple(decisions),
            followups=tuple(followups),
            derived_lifecycle_status=derived_status,
            legacy_compat=_legacy_compat(subject, derived_status),
        )
        revision = ProjectionRevision(
            subject_ref=subject_ref.serialize(),
            built_from_revision=number,
            current_revision=number,
        )
        return ProjectionResult(
            code=ProjectionResultCode.PROJECTED,
            projection=projection,
            revision=revision,
        )

    # -- executions + current selection --------------------------------------

    def _project_executions(self, subject_ref: ObjectRef, subject_iid):
        exec_records = self._repo.executions_of_subject(subject_ref)
        projs: list[ExecutionProjection] = []
        for rec in exec_records:
            if rec.subject_ref != subject_iid:
                return ProjectionResult(
                    code=ProjectionResultCode.REFERENTIAL_ERROR,
                    error_detail=f"execution {rec.execution_id.to_canonical()} owned by a different subject",
                )
            exec_ref = make_object_ref(IdKind.EXECUTION, rec.execution_id)
            completion = None
            try:
                comp = self._repo.completion_of_execution(exec_ref)
            except GraphNotFoundError:
                comp = None
            except GraphReferentialError:
                return ProjectionResult(
                    code=ProjectionResultCode.REFERENTIAL_ERROR,
                    error_detail=f"execution {rec.execution_id.to_canonical()} has corrupt completion state",
                )
            if comp is not None:
                if comp.execution_ref != rec.execution_id:
                    return ProjectionResult(
                        code=ProjectionResultCode.REFERENTIAL_ERROR,
                        error_detail=f"completion {comp.completion_id.to_canonical()} references wrong execution",
                    )
                completion = CompletionProjection(
                    completion_ref=comp.completion_id.to_canonical(),
                    execution_ref=comp.execution_ref.to_canonical(),
                    outcome=comp.outcome,
                    evidence_refs=tuple(comp.evidence_refs or ()),
                    recorded_at=comp.recorded_at,
                )
            projs.append(
                ExecutionProjection(
                    execution_ref=rec.execution_id.to_canonical(),
                    subject_ref=rec.subject_ref.to_canonical(),
                    executor_kind=rec.executor_kind,
                    mechanical_status=rec.mechanical_status,
                    started_at=rec.started_at,
                    correlation_id=rec.correlation_id,
                    completion=completion,
                )
            )
        current = self._select_current(projs)
        if current is ProjectionResultCode.AMBIGUOUS_DERIVED_STATE:
            return ProjectionResult(
                code=ProjectionResultCode.AMBIGUOUS_DERIVED_STATE,
                error_detail="no mechanically unique current execution (tie or unorderable started_at)",
            )
        return tuple(projs), current

    def _select_current(self, projs):
        """Deterministic current Execution from frozen canonical started_at."""
        if not projs:
            return None
        keys: list[tuple[datetime, str]] = []
        for p in projs:
            parsed = _parse_started_at(p.started_at)
            if parsed is None:
                return ProjectionResultCode.AMBIGUOUS_DERIVED_STATE
            keys.append((parsed, p.execution_ref))
        keys.sort(key=lambda kv: (kv[0], kv[1]), reverse=True)
        top_time = keys[0][0]
        ties = [ref for (t, ref) in keys if t == top_time]
        if len(ties) > 1:
            return ProjectionResultCode.AMBIGUOUS_DERIVED_STATE
        target = next(p for p in projs if p.execution_ref == ties[0])
        return target

    # -- decisions -----------------------------------------------------------

    def _project_decisions(self, subject_ref: ObjectRef, subject_iid):
        dec_records = self._repo.decisions_of_subject(subject_ref)
        projs = []
        for rec in dec_records:
            if rec.subject_ref != subject_iid:
                return ProjectionResult(
                    code=ProjectionResultCode.REFERENTIAL_ERROR,
                    error_detail=f"decision {rec.decision_id.to_canonical()} owned by a different subject",
                )
            projs.append(
                DecisionProjection(
                    decision_ref=rec.decision_id.to_canonical(),
                    subject_ref=rec.subject_ref.to_canonical(),
                    decision_kind=rec.decision_kind,
                    statement=rec.statement,
                    decision_time=rec.decision_time,
                    target_refs=tuple(rec.target_refs or ()),
                    evidence_refs=tuple(rec.evidence_refs or ()),
                )
            )
        return projs

    # -- followups -----------------------------------------------------------

    def _project_followups(self, subject_iid):
        try:
            edges = self._repo.edges()
        except GraphNotFoundError:
            return ProjectionResult(
                code=ProjectionResultCode.REFERENTIAL_ERROR,
                error_detail="unable to read canonical followup edges",
            )
        projs = []
        for edge in edges:
            involved = (
                edge.parent_subject_ref == subject_iid or edge.child_subject_ref == subject_iid
            )
            if not involved:
                continue
            # Fail closed on any broken canonical lineage: parent, child and
            # source Decision must all resolve (PROJECTION_SILENTLY_IGNORES_REFERENTIAL_ERROR=no).
            try:
                self._repo.edge(make_object_ref(IdKind.EDGE, edge.edge_id))
                self._repo.subject(make_object_ref(IdKind.SUBJECT, edge.parent_subject_ref))
                self._repo.subject(make_object_ref(IdKind.SUBJECT, edge.child_subject_ref))
                self._repo.decision(make_object_ref(IdKind.DECISION, edge.source_decision_ref))
            except GraphNotFoundError:
                return ProjectionResult(
                    code=ProjectionResultCode.REFERENTIAL_ERROR,
                    error_detail=f"followup edge {edge.edge_id.to_canonical()} has broken lineage",
                )
            projs.append(
                FollowupProjection(
                    edge_ref=edge.edge_id.to_canonical(),
                    parent_subject_ref=edge.parent_subject_ref.to_canonical(),
                    child_subject_ref=edge.child_subject_ref.to_canonical(),
                    source_decision_ref=edge.source_decision_ref.to_canonical(),
                    rationale=edge.rationale,
                )
            )
        return projs


__all__ = [
    "ProjectionError",
    "ProjectionResult",
    "ProjectionRebuildService",
]
