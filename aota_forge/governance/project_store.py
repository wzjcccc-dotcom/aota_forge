"""AF #57 M1/W3 — Project Governance Store contract (AF Project Governance Subsystem).

The smallest durable, storage-neutral contract for project / cross-Plan
governance state that is not already owned elsewhere:

    ExecutionStateStore        -> execution truth (unchanged)
    TaskMainCoordinatorState   -> task-main work progression truth (unchanged)
    Project Governance Store   -> project / Plan-level governance only

Minimum M1 domain (CONSUMER_REQUIRED_BEFORE_SCHEMA=yes):

    project reference / identity
    Plan record: plan_id + Plan-level lifecycle state
    authority binding: reused W1 ``PlanAuthorityBinding`` (identity != source)
    record revision for atomic CAS protection

Plan-level lifecycle only.  This store never owns Milestone execution state,
work progression, worker attempts, or any M2/M3 workflow state.  The reused
W1 binding remains the single authority-binding ontology: this module does
not redefine ``source_kind`` / ``authority_ref`` / ``source_revision`` /
``source_digest`` semantics.

Explicit negative ownership (guards below): the store never owns execution
attempts, worker results, work dispatch/acceptance progression, completion
queue, Human Brake, or task-main blocker/next-action working truth.  Those
remain in the existing stores; Project progress is an aggregate projection
over existing stores and requires no duplicate storage.

Storage-neutral core only:

    no ORM / PostgreSQL / event sourcing / CQRS / event bus
    no migration framework / repository-manager stack
    no second Plan mutation protocol

The one local SQLite adapter lives in
``aota_forge.governance.sqlite_store`` and is exposed through the bounded
composition seam ``aota_forge.composition.project_governance``.  Cross-cutting
runtime wiring is W4 (not W3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import (
    PlanAuthorityBinding,
    PlanAuthorityBindingError,
)
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE

PROJECT_GOVERNANCE_STORE_IMPLEMENTED = True
PROJECT_GOVERNANCE_STORE_SCOPE = "project_and_cross_plan_governance_only"

# Frozen Governance 2.0 negative ownership boundary.  These must remain
# ``False``: duplicating execution/coordinator working truth into this store
# is exactly what the three-store boundary forbids.
PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS = False
PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS = False
PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION = False
PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE = False
PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE = False
GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE = False
GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE = False

# Minimal reopen safety only; no migration framework and no speculative
# future schemas are implied by this number.
PROJECT_GOVERNANCE_SCHEMA_VERSION = 1

# Minimum Plan-level governance lifecycle for the M1 local Plan binding proof.
# This is not Milestone execution state, work progression, or worker attempt
# state.  Retirement-kind taxonomy and M2/M3 workflow states are deliberately
# not pre-implemented.
PLAN_LIFECYCLE_ACTIVE = "active"
PLAN_LIFECYCLE_RETIRED = "retired"
PLAN_LIFECYCLE_STATES = frozenset({PLAN_LIFECYCLE_ACTIVE, PLAN_LIFECYCLE_RETIRED})

MAX_PROJECT_REFERENCE_LENGTH = 96

PROJECT_GOVERNANCE_STORE_ERROR = "PROJECT_GOVERNANCE_STORE_ERROR"
PROJECT_GOVERNANCE_RECORD_INVALID = "PROJECT_GOVERNANCE_RECORD_INVALID"
PROJECT_GOVERNANCE_PLAN_NOT_FOUND = "PROJECT_GOVERNANCE_PLAN_NOT_FOUND"
PROJECT_GOVERNANCE_PLAN_EXISTS = "PROJECT_GOVERNANCE_PLAN_EXISTS"
PROJECT_GOVERNANCE_STALE_REVISION = "PROJECT_GOVERNANCE_STALE_REVISION"
PROJECT_GOVERNANCE_STORE_CLOSED = "PROJECT_GOVERNANCE_STORE_CLOSED"
PROJECT_GOVERNANCE_PERSISTENCE_FAILURE = "PROJECT_GOVERNANCE_PERSISTENCE_FAILURE"
PROJECT_GOVERNANCE_CORRUPT_STATE = "PROJECT_GOVERNANCE_CORRUPT_STATE"


class ProjectGovernanceStoreError(Exception):
    """Bounded deterministic base error for the Project Governance Store."""

    code = PROJECT_GOVERNANCE_STORE_ERROR

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


class ProjectGovernanceRecordError(ProjectGovernanceStoreError, ValueError):
    """A governance record or mutation request is invalid (fail closed)."""

    code = PROJECT_GOVERNANCE_RECORD_INVALID


class PlanRecordNotFoundError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_PLAN_NOT_FOUND


class PlanRecordAlreadyExistsError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_PLAN_EXISTS


class StalePlanRevisionError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_STALE_REVISION


class ProjectGovernanceStoreClosedError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_STORE_CLOSED


class ProjectGovernancePersistenceError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_PERSISTENCE_FAILURE


class ProjectGovernanceCorruptStateError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_CORRUPT_STATE


@dataclass(frozen=True)
class ProjectPlanRecord:
    """One Plan's project-level governance record.

    ``authority`` reuses the W1 ``PlanAuthorityBinding`` and must reference
    exactly this record's ``plan_id``: one current bound authority per Plan,
    with source revision/digest as observed facts only.
    """

    project_id: str
    plan_id: str
    lifecycle_state: str
    authority: PlanAuthorityBinding
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProjectGovernanceRecordError("project_id must be a non-empty project identity")
        project_id = self.project_id.strip()
        if len(project_id) > MAX_PROJECT_REFERENCE_LENGTH or not PROJECT_ID_RE.fullmatch(project_id):
            raise ProjectGovernanceRecordError(
                "project_id must match the canonical project identity grammar"
            )
        object.__setattr__(self, "project_id", project_id)

        if not is_plan_id(self.plan_id):
            raise ProjectGovernanceRecordError(
                "plan_id must be one canonical internal Plan ID (identity, never authority)"
            )
        if self.lifecycle_state not in PLAN_LIFECYCLE_STATES:
            raise ProjectGovernanceRecordError(
                f"lifecycle_state must be one of {sorted(PLAN_LIFECYCLE_STATES)}"
            )
        if not isinstance(self.authority, PlanAuthorityBinding):
            raise ProjectGovernanceRecordError(
                "authority must be a PlanAuthorityBinding (reuse the W1 contract)"
            )
        if self.authority.plan_id != self.plan_id:
            raise ProjectGovernanceRecordError(
                "authority binding plan_id must match the Plan record plan_id"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ProjectGovernanceRecordError("revision must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "lifecycle_state": self.lifecycle_state,
            "authority": self.authority.to_dict(),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectPlanRecord":
        if not isinstance(data, Mapping):
            raise ProjectGovernanceRecordError("record payload must be a mapping")
        allowed = {item.name for item in fields(cls)}
        extra = set(data.keys()) - allowed
        if extra:
            raise ProjectGovernanceRecordError(f"unknown record field(s): {sorted(extra)}")
        missing = {"project_id", "plan_id", "lifecycle_state", "authority"} - set(data.keys())
        if missing:
            raise ProjectGovernanceRecordError(f"missing record field(s): {sorted(missing)}")
        authority_data = data["authority"]
        if not isinstance(authority_data, Mapping):
            raise ProjectGovernanceRecordError("authority must be a binding mapping")
        try:
            authority = PlanAuthorityBinding.from_dict(authority_data)
        except PlanAuthorityBindingError as exc:
            raise ProjectGovernanceRecordError(f"invalid authority binding: {exc}") from exc
        return cls(
            project_id=data["project_id"],
            plan_id=data["plan_id"],
            lifecycle_state=data["lifecycle_state"],
            authority=authority,
            revision=data.get("revision", 1),
        )


class ProjectGovernanceStore(ABC):
    """Storage-neutral Project Governance Store port (AF #57 M1/W3).

    Only operations genuinely needed for M1.  No generic write API, no query
    DSL, and no second Plan mutation protocol: external Plan mutation remains
    ``PlanAuthorityMutationPort`` (W2 owns its local adapter).
    """

    @abstractmethod
    def put_plan(self, record: ProjectPlanRecord) -> ProjectPlanRecord:
        """Create one new Plan governance record at revision 1 (create-only)."""
        raise NotImplementedError

    @abstractmethod
    def get_plan(self, project_id: str, plan_id: str) -> ProjectPlanRecord | None:
        raise NotImplementedError

    @abstractmethod
    def list_plans(self, project_id: str) -> tuple[ProjectPlanRecord, ...]:
        raise NotImplementedError

    @abstractmethod
    def compare_and_swap_plan(
        self,
        project_id: str,
        plan_id: str,
        expected_revision: int,
        *,
        lifecycle_state: str | None = None,
        authority: PlanAuthorityBinding | None = None,
    ) -> ProjectPlanRecord:
        """Atomically mutate one Plan record if ``expected_revision`` still holds."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
