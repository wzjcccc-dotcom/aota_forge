"""M3-B8 Projection Reconstruction (Issue #9, lane M3-B8).

An executor-neutral, storage-neutral projection layer that derives
deterministic lifecycle / read-model state EXCLUSIVELY from canonical AOTA
Forge graph state.  The canonical Subject Graph is authoritative lifecycle
state; the projection is derived state only
(``GRAPH_IS_AUTHORITY_FOR_PROJECTION=yes``,
``PROJECTION_IS_SUBJECT_AUTHORITY=no``).

B8 is a read-only derived-state lane:

* deterministic full rebuild of a Subject projection from canonical graph
* projection revision / staleness metadata (storage-neutral, in-memory)
* bounded B8-local result/error codes
* legacy ``current_*`` comparison view (comparison-only, never authority)
* NO authoritative mutation capability and NO graph write
  (``PROJECTION_GRAPH_WRITE_COUNT=0``)

Runtime hard stop (plan section 31): projection SOURCE is implemented here, but
no production projection runtime / persistence / shadow graph sync / cutover is
active (``PROJECTION_RUNTIME_PRODUCTION_ACTIVE=no``,
``PROJECTION_PERSISTENCE_IMPLEMENTED=no``,
``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``).
"""

from __future__ import annotations

from aota_forge.core.projection.model import (
    CompletionProjection,
    DecisionProjection,
    ExecutionProjection,
    FollowupProjection,
    ProjectionResultCode,
    SubjectProjection,
)
from aota_forge.core.projection.rebuild import (
    ProjectionError,
    ProjectionRebuildService,
    ProjectionResult,
)
from aota_forge.core.projection.status import ProjectionRevision, compare_revision

# ---------------------------------------------------------------------------
# Module boundary / end-state flags (plan sections 4, 8, 18, 22, 31).
# ---------------------------------------------------------------------------

PROJECTION_RECONSTRUCTION_IMPLEMENTED = True
GRAPH_IS_AUTHORITY_FOR_PROJECTION = True
PROJECTION_IS_SUBJECT_AUTHORITY = False
CURRENT_POINTER_AUTHORITY = False
CURRENT_POINTER_SUBJECT_AUTHORITY = False
LEGACY_PROJECTION_IS_AUTHORITY = False
CONTROL_COMMENT_IS_SUBJECT_AUTHORITY = False
EVENT_LOG_IS_SUBJECT_AUTHORITY = False
GIT_HISTORY_IS_SUBJECT_AUTHORITY = False

PROJECTION_FULL_REBUILD_IMPLEMENTED = True
PROJECTION_REBUILD_DETERMINISTIC = True
PROJECTION_HEURISTIC_SELECTION_ALLOWED = False
B8_GRAPH_ONLY_DETERMINISTIC_REBUILD = True

PROJECTION_STALENESS_USES_SUBJECT_REVISION = True
PROJECTION_REVISION_SOURCE = "Subject_aggregate_revision"
B8_PERSISTED_PROJECTION_STORE_REQUIRED = False
B8_DURABLE_STALE_MARKER_STORAGE_REQUIRED = False
B8_STORAGE_NEUTRAL_STALE_STATE_ALLOWED = True
PROJECTION_PERSISTENCE_IMPLEMENTED = False

PROJECTION_FAILURE_CAN_CORRUPT_CANONICAL_GRAPH = False
PROJECTION_FAILURE_INVALIDATES_GRAPH_AUTHORITY = False
PROJECTION_FAILURE_MUTATES_SUBJECT_REVISION = False
PROJECTION_GRAPH_WRITE_COUNT = 0
PROJECTION_TRANSACTION_START_REQUIRED = False
PROJECTION_CAPABILITY_LEASE_REQUIRED = False
PROJECTION_AUTHORITY_ENGINE_REQUIRED = False
PROJECTION_SILENTLY_IGNORES_REFERENTIAL_ERROR = False

FOLLOWUP_PROJECTION_REQUIRES_CANONICAL_EDGE = True
FOLLOWUP_PROJECTION_INVENTS_DECISION = False

LEGACY_COMPATIBILITY_IS_COMPARISON_ONLY = True
LEGACY_CURRENT_POINTER_INPUT_TO_PROJECTION_AUTHORITY = False

B8_GRAPH_REPOSITORY_WRITE_ALLOWED = False
B8_NEEDS_GRAPH_SOURCE_MUTATION = False
B8_NEEDS_TRANSITION_SOURCE_MUTATION = False
B8_SHARED_ERRORS_WRITE_ALLOWED = False
B8_SHARED_RESULTS_WRITE_ALLOWED = False
B8_LEGACY_PLAN_FILES_WRITE_ALLOWED = False
B8_TRANSITIONS_WRITE_ALLOWED = False
B8_NEW_CANONICAL_TRANSITION_CREATED = False

BINDING_RECOVERY_IMPLEMENTED = False
SUBJECT_CANDIDATE_QUERY_IMPLEMENTED_BY_B8 = False

PROJECTION_RUNTIME_PRODUCTION_ACTIVE = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED = False
CUTOVER_AUTHORIZED = False

__all__ = [
    "CompletionProjection",
    "DecisionProjection",
    "ExecutionProjection",
    "FollowupProjection",
    "ProjectionResultCode",
    "SubjectProjection",
    "ProjectionError",
    "ProjectionRebuildService",
    "ProjectionResult",
    "ProjectionRevision",
    "compare_revision",
]
