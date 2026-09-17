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

AF #57 M2/W4 additive extension: the durable bounded cross-project grant
record (``governance.cross_project_grant``) is owned by this same store.  The
plan core schema version intentionally stays 1: the accepted W1/W3 contract
pins (``PROJECT_GOVERNANCE_SCHEMA_VERSION == 1`` and the fresh v1 table set)
must remain unchanged, and the grant table is a bounded additive table
materialized on first grant write rather than a versioned migration.  There
is no generic migration framework, no ORM and no second grant database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
import hashlib
import re
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
# future schemas are implied by this number.  AF #57 M2/W4 keeps the plan
# core at v1 and uses a bounded additive grant table in the same database
# (see the sqlite adapter); no version bump is required or made.
PROJECT_GOVERNANCE_SCHEMA_VERSION = 1

# Minimum Plan-level governance lifecycle for the M1 local Plan binding proof.
# This is not Milestone execution state, work progression, or worker attempt
# state.  Retirement-kind taxonomy and M2/M3 workflow states are deliberately
# not pre-implemented.
PLAN_LIFECYCLE_ACTIVE = "active"
PLAN_LIFECYCLE_RETIRED = "retired"
PLAN_LIFECYCLE_STATES = frozenset({PLAN_LIFECYCLE_ACTIVE, PLAN_LIFECYCLE_RETIRED})

MAX_PROJECT_REFERENCE_LENGTH = 96
MAX_GOVERNANCE_METADATA_LENGTH = 256
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_METADATA_REFERENCE_RE = re.compile(r"^[^\x00\r\n]+$")

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


class GovernanceMetadataAlreadyExistsError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_PLAN_EXISTS


class GovernanceMetadataNotFoundError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_PLAN_NOT_FOUND


class StaleGovernanceMetadataRevisionError(ProjectGovernanceStoreError):
    code = PROJECT_GOVERNANCE_STALE_REVISION


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


def _metadata_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or type(value) is not str
        or not value.strip()
        or value != value.strip()
        or len(value) > MAX_GOVERNANCE_METADATA_LENGTH
        or not _METADATA_REFERENCE_RE.fullmatch(value)
    ):
        raise ProjectGovernanceRecordError(
            f"{label} must be a bounded single-line governance reference"
        )
    return value


def _metadata_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectGovernanceRecordError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _metadata_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _metadata_text(value, label)


USER_GATE_STATE_REQUIRED = "required"
USER_GATE_STATE_SATISFIED = "satisfied"
USER_GATE_STATE_REVOKED = "revoked"
USER_GATE_STATES = frozenset(
    {USER_GATE_STATE_REQUIRED, USER_GATE_STATE_SATISFIED, USER_GATE_STATE_REVOKED}
)


@dataclass(frozen=True)
class ProjectPlanUserGateRecord:
    """Durable linkage to one already-issued operator user-gate fact.

    This record stores provenance and CAS metadata only. It does not mint an
    approval, evaluate policy, or contain workflow/dispatch state.
    """

    project_id: str
    plan_id: str
    gate_kind: str
    gate_scope: str
    approval_ref: str
    approval_digest: str
    state: str = USER_GATE_STATE_SATISFIED
    revision: int = 1

    @classmethod
    def from_approval(
        cls,
        project_id: str,
        plan_id: str,
        gate_kind: str,
        gate_scope: str,
        approval: Any,
        *,
        state: str = USER_GATE_STATE_SATISFIED,
    ) -> "ProjectPlanUserGateRecord":
        """Persist an existing typed ``UserGateApproval`` fact, never a raw ref."""
        from aota_forge.governance.cross_project_grant import UserGateApproval

        if not isinstance(approval, UserGateApproval):
            raise ProjectGovernanceRecordError(
                "user-gate linkage requires an existing UserGateApproval fact"
            )
        return cls(
            project_id=project_id,
            plan_id=plan_id,
            gate_kind=gate_kind,
            gate_scope=gate_scope,
            approval_ref=approval.approval_ref,
            approval_digest=approval.approval_digest,
            state=state,
        )

    @property
    def is_satisfied(self) -> bool:
        return self.state == USER_GATE_STATE_SATISFIED

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectGovernanceRecordError("user-gate project_id is not canonical")
        if not is_plan_id(self.plan_id):
            raise ProjectGovernanceRecordError("user-gate plan_id is not canonical")
        for value, label in (
            (self.gate_kind, "gate_kind"),
            (self.gate_scope, "gate_scope"),
            (self.approval_ref, "approval_ref"),
        ):
            _metadata_text(value, label)
        _metadata_digest(self.approval_digest, "approval_digest")
        if self.state not in USER_GATE_STATES:
            raise ProjectGovernanceRecordError(
                f"user-gate state must be one of {sorted(USER_GATE_STATES)}"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ProjectGovernanceRecordError("user-gate revision must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "gate_kind": self.gate_kind,
            "gate_scope": self.gate_scope,
            "approval_ref": self.approval_ref,
            "approval_digest": self.approval_digest,
            "state": self.state,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectPlanUserGateRecord":
        if not isinstance(data, Mapping):
            raise ProjectGovernanceRecordError("user-gate payload must be a mapping")
        required = {
            "project_id",
            "plan_id",
            "gate_kind",
            "gate_scope",
            "approval_ref",
            "approval_digest",
            "state",
        }
        extra = set(data) - required - {"revision"}
        if extra:
            raise ProjectGovernanceRecordError(f"unknown user-gate field(s): {sorted(extra)}")
        missing = required - set(data)
        if missing:
            raise ProjectGovernanceRecordError(f"missing user-gate field(s): {sorted(missing)}")
        return cls(**{key: data[key] for key in required}, revision=data.get("revision", 1))


def architecture_authority_reference(project_id: str) -> str:
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectGovernanceRecordError("architecture project_id is not canonical")
    return f"local-governance/{project_id}/ARCHITECTURE.md"


@dataclass(frozen=True)
class ArchitectureMetadataRecord:
    """Minimal durable metadata for the accepted local Architecture Card source."""

    project_id: str
    current_version: str
    current_digest: str
    accepted_delta_ref: str | None = None
    accepted_delta_digest: str | None = None
    promotion_receipt_ref: str = ""
    revision: int = 1

    @property
    def authority_ref(self) -> str:
        return architecture_authority_reference(self.project_id)

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectGovernanceRecordError("architecture project_id is not canonical")
        _metadata_text(self.current_version, "current_version")
        _metadata_digest(self.current_digest, "current_digest")
        if (self.accepted_delta_ref is None) != (self.accepted_delta_digest is None):
            raise ProjectGovernanceRecordError(
                "accepted architecture delta ref and digest must be supplied together"
            )
        _metadata_optional_text(self.accepted_delta_ref, "accepted_delta_ref")
        if self.accepted_delta_digest is not None:
            _metadata_digest(self.accepted_delta_digest, "accepted_delta_digest")
        _metadata_text(self.promotion_receipt_ref, "promotion_receipt_ref")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ProjectGovernanceRecordError("architecture revision must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "current_version": self.current_version,
            "current_digest": self.current_digest,
            "accepted_delta_ref": self.accepted_delta_ref,
            "accepted_delta_digest": self.accepted_delta_digest,
            "promotion_receipt_ref": self.promotion_receipt_ref,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitectureMetadataRecord":
        if not isinstance(data, Mapping):
            raise ProjectGovernanceRecordError("architecture metadata payload must be a mapping")
        required = {"project_id", "current_version", "current_digest", "promotion_receipt_ref"}
        allowed = required | {"accepted_delta_ref", "accepted_delta_digest", "revision"}
        extra = set(data) - allowed
        if extra:
            raise ProjectGovernanceRecordError(
                f"unknown architecture metadata field(s): {sorted(extra)}"
            )
        missing = required - set(data)
        if missing:
            raise ProjectGovernanceRecordError(
                f"missing architecture metadata field(s): {sorted(missing)}"
            )
        return cls(
            project_id=data["project_id"],
            current_version=data["current_version"],
            current_digest=data["current_digest"],
            accepted_delta_ref=data.get("accepted_delta_ref"),
            accepted_delta_digest=data.get("accepted_delta_digest"),
            promotion_receipt_ref=data["promotion_receipt_ref"],
            revision=data.get("revision", 1),
        )


STEWARD_REPLAY_STATE_ACTIVE = "active"
STEWARD_REPLAY_STATE_COMPLETED = "completed"
STEWARD_REPLAY_STATE_FAILED_CLOSED = "failed_closed"
STEWARD_REPLAY_STATE_CONFLICT = "conflict"
STEWARD_REPLAY_STATES = frozenset(
    {
        STEWARD_REPLAY_STATE_ACTIVE,
        STEWARD_REPLAY_STATE_COMPLETED,
        STEWARD_REPLAY_STATE_FAILED_CLOSED,
        STEWARD_REPLAY_STATE_CONFLICT,
    }
)


@dataclass(frozen=True)
class StewardLogicalReplayRecord:
    """Durable mechanical replay lineage; it makes no dispatch decision."""

    project_id: str
    plan_id: str
    lineage_id: str
    request_digest: str
    checkpoint_ref: str
    checkpoint_digest: str
    state: str = STEWARD_REPLAY_STATE_ACTIVE
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectGovernanceRecordError("replay project_id is not canonical")
        if not is_plan_id(self.plan_id):
            raise ProjectGovernanceRecordError("replay plan_id is not canonical")
        _metadata_text(self.lineage_id, "lineage_id")
        _metadata_digest(self.request_digest, "request_digest")
        _metadata_text(self.checkpoint_ref, "checkpoint_ref")
        _metadata_digest(self.checkpoint_digest, "checkpoint_digest")
        if self.state not in STEWARD_REPLAY_STATES:
            raise ProjectGovernanceRecordError(
                f"replay state must be one of {sorted(STEWARD_REPLAY_STATES)}"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ProjectGovernanceRecordError("replay revision must be positive")

    @property
    def replay_id(self) -> str:
        payload = f"{self.project_id}:{self.plan_id}:{self.lineage_id}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "lineage_id": self.lineage_id,
            "request_digest": self.request_digest,
            "checkpoint_ref": self.checkpoint_ref,
            "checkpoint_digest": self.checkpoint_digest,
            "state": self.state,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StewardLogicalReplayRecord":
        if not isinstance(data, Mapping):
            raise ProjectGovernanceRecordError("replay payload must be a mapping")
        required = {
            "project_id",
            "plan_id",
            "lineage_id",
            "request_digest",
            "checkpoint_ref",
            "checkpoint_digest",
            "state",
        }
        extra = set(data) - required - {"revision"}
        if extra:
            raise ProjectGovernanceRecordError(f"unknown replay field(s): {sorted(extra)}")
        missing = required - set(data)
        if missing:
            raise ProjectGovernanceRecordError(f"missing replay field(s): {sorted(missing)}")
        return cls(**{key: data[key] for key in required}, revision=data.get("revision", 1))


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

    # AF #57 M3/W1: bounded additive foundation records. These are explicit
    # methods rather than a generic metadata dictionary or workflow database.
    def put_user_gate(self, record: ProjectPlanUserGateRecord) -> ProjectPlanUserGateRecord:
        raise NotImplementedError

    def get_user_gate(self, project_id: str, plan_id: str) -> ProjectPlanUserGateRecord | None:
        raise NotImplementedError

    def compare_and_swap_user_gate(
        self,
        project_id: str,
        plan_id: str,
        expected_revision: int,
        *,
        state: str | None = None,
        approval_ref: str | None = None,
        approval_digest: str | None = None,
    ) -> ProjectPlanUserGateRecord:
        raise NotImplementedError

    def put_architecture_metadata(
        self, record: ArchitectureMetadataRecord
    ) -> ArchitectureMetadataRecord:
        raise NotImplementedError

    def get_architecture_metadata(self, project_id: str) -> ArchitectureMetadataRecord | None:
        raise NotImplementedError

    def compare_and_swap_architecture_metadata(
        self,
        project_id: str,
        expected_revision: int,
        *,
        current_version: str | None = None,
        current_digest: str | None = None,
        accepted_delta_ref: str | None = None,
        accepted_delta_digest: str | None = None,
        promotion_receipt_ref: str | None = None,
    ) -> ArchitectureMetadataRecord:
        raise NotImplementedError

    def put_steward_replay(
        self, record: StewardLogicalReplayRecord
    ) -> StewardLogicalReplayRecord:
        raise NotImplementedError

    def get_steward_replay(
        self, project_id: str, plan_id: str, lineage_id: str
    ) -> StewardLogicalReplayRecord | None:
        raise NotImplementedError

    def compare_and_swap_steward_replay(
        self,
        project_id: str,
        plan_id: str,
        lineage_id: str,
        expected_revision: int,
        *,
        state: str | None = None,
        checkpoint_ref: str | None = None,
        checkpoint_digest: str | None = None,
    ) -> StewardLogicalReplayRecord:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
