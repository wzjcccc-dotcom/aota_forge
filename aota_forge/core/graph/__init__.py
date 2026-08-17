"""M3-B3 durable subject graph — canonical record foundation.

Executor-neutral durable graph records, a storage-neutral repository
abstraction, and a mechanical owning-subject resolver (issue #9, lane M3-B3).

The canonical subject graph records here implement the accepted M3-A1 schema
and foundational relationships.  This lane is deliberately isolated and does
NOT implement identity policy (M3-B4), authority evaluation (M3-B5), lease,
CAS/transaction, migration/cutover, or any authoritative runtime graph write.

Tests/fixtures must NOT import this package simply to obtain graph side
effects; it defines data models and a read abstraction only.
"""

from aota_forge.core.graph.ids import CanonicalId, IdKind, ref_of, subject_id
from aota_forge.core.graph.records import (
    Workflow,
    Subject,
    Execution,
    Completion,
    Decision,
    FollowupEdge,
    workflow,
    subject,
    execution,
    completion,
    decision,
    followup_edge,
)
from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    GraphReferentialError,
    GraphRepository,
    OwningSubjectResolver,
    InMemoryGraphRepository,
    assert_ref_kind,
)
from aota_forge.core.graph.serialization import (
    to_dict,
    to_bytes,
    serialize,
    round_trip,
)

RECORD_TYPES = (
    Workflow,
    Subject,
    Execution,
    Completion,
    Decision,
    FollowupEdge,
)

__all__ = [
    "CanonicalId",
    "IdKind",
    "ref_of",
    "subject_id",
    "Workflow",
    "Subject",
    "Execution",
    "Completion",
    "Decision",
    "FollowupEdge",
    "workflow",
    "subject",
    "execution",
    "completion",
    "decision",
    "followup_edge",
    "GraphNotFoundError",
    "GraphReferentialError",
    "GraphRepository",
    "OwningSubjectResolver",
    "InMemoryGraphRepository",
    "assert_ref_kind",
    "to_dict",
    "to_bytes",
    "serialize",
    "round_trip",
    "RECORD_TYPES",
]