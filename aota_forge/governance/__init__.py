"""AF Project Governance Subsystem — W3 Project Governance Store (AF #57 M1/W3).

Bounded package surface: the storage-neutral port + record model
(``project_store``) and the one local SQLite adapter (``sqlite_store``).

This package intentionally contains no execution-state, coordinator-state,
work-progression, or Human Brake domain.  Cross-cutting runtime wiring is W4;
this package is consumed through ``aota_forge.composition.project_governance``.
"""

from aota_forge.governance.project_store import (
    GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE,
    GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE,
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    PLAN_LIFECYCLE_STATES,
    PROJECT_GOVERNANCE_CORRUPT_STATE,
    PROJECT_GOVERNANCE_PERSISTENCE_FAILURE,
    PROJECT_GOVERNANCE_PLAN_EXISTS,
    PROJECT_GOVERNANCE_PLAN_NOT_FOUND,
    PROJECT_GOVERNANCE_RECORD_INVALID,
    PROJECT_GOVERNANCE_SCHEMA_VERSION,
    PROJECT_GOVERNANCE_STALE_REVISION,
    PROJECT_GOVERNANCE_STORE_CLOSED,
    PROJECT_GOVERNANCE_STORE_ERROR,
    PROJECT_GOVERNANCE_STORE_IMPLEMENTED,
    PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE,
    PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS,
    PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE,
    PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION,
    PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS,
    PROJECT_GOVERNANCE_STORE_SCOPE,
    PlanRecordAlreadyExistsError,
    PlanRecordNotFoundError,
    ProjectGovernanceCorruptStateError,
    ProjectGovernancePersistenceError,
    ProjectGovernanceRecordError,
    ProjectGovernanceStore,
    ProjectGovernanceStoreClosedError,
    ProjectGovernanceStoreError,
    ProjectPlanRecord,
    StalePlanRevisionError,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

__all__ = [
    "GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE",
    "GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE",
    "PLAN_LIFECYCLE_ACTIVE",
    "PLAN_LIFECYCLE_RETIRED",
    "PLAN_LIFECYCLE_STATES",
    "PROJECT_GOVERNANCE_CORRUPT_STATE",
    "PROJECT_GOVERNANCE_PERSISTENCE_FAILURE",
    "PROJECT_GOVERNANCE_PLAN_EXISTS",
    "PROJECT_GOVERNANCE_PLAN_NOT_FOUND",
    "PROJECT_GOVERNANCE_RECORD_INVALID",
    "PROJECT_GOVERNANCE_SCHEMA_VERSION",
    "PROJECT_GOVERNANCE_STALE_REVISION",
    "PROJECT_GOVERNANCE_STORE_CLOSED",
    "PROJECT_GOVERNANCE_STORE_ERROR",
    "PROJECT_GOVERNANCE_STORE_IMPLEMENTED",
    "PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE",
    "PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS",
    "PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE",
    "PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION",
    "PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS",
    "PROJECT_GOVERNANCE_STORE_SCOPE",
    "PlanRecordAlreadyExistsError",
    "PlanRecordNotFoundError",
    "ProjectGovernanceCorruptStateError",
    "ProjectGovernancePersistenceError",
    "ProjectGovernanceRecordError",
    "ProjectGovernanceStore",
    "ProjectGovernanceStoreClosedError",
    "ProjectGovernanceStoreError",
    "ProjectPlanRecord",
    "SQLiteProjectGovernanceStore",
    "StalePlanRevisionError",
]
