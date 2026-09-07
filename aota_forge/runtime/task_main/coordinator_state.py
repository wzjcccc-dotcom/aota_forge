"""Durable task-main coordinator state (M3/W1).

The smallest missing durable task-main working state. This module owns ONLY
coordinator lifecycle state:

* activation / revision / session / runtime bindings
* Work Item dispatch progression (pending / active / completion-pending)
* dispatch attempt bindings (canonical_task_id / idempotency identity)

It is NOT Plan authority (``COORDINATOR_STATE_IS_PLAN_AUTHORITY=no``), NOT
the M2 execution store (``EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE=no``),
and NOT Hermes session truth (``HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY=no``).

Explicitly out of scope for W1 (owned by M3/W2 and M3/W3):

* CARD governed semantic apply / acceptance judgment
* ACK-after-semantic-reconciliation
* review finding batch application / RV1-RV2 control loop
* Milestone closure automation

WorkingTruthProjection / SessionCheckpoint / MilestoneWorkItemGraph /
progression-review-closure evaluators are reused where they already exist;
this state only references their identities and never duplicates their
semantics.

M3/W2 bounded extension (schema v1 -> v2): the coordinator durably retains
governed semantic reconciliation working state alongside the W1 dispatch
lifecycle — reconciled completion identities (receipt payloads keyed by
canonical_task_id), per-Work-Item semantic status, a WorkingTruthProjection
payload/ref/digest, and the current progression revision. No migration
framework: v1 records fail closed under v2 (no production W1 coordinator
data exists). The v2 semantic fields are working truth only, never Plan
authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any

COORDINATOR_STATE_SCHEMA_VERSION = 2

COORDINATOR_STATE_IS_PLAN_AUTHORITY = False
EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE = False
HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY = False

W1_CARD_SEMANTIC_APPLICATION_IMPLEMENTED = False
ACK_AFTER_SEMANTIC_RECONCILIATION_IMPLEMENTED = False
REVIEW_AUTOMATION_IMPLEMENTED = False
REPAIR_AUTOMATION_IMPLEMENTED = False
MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED = False


@unique
class CoordinatorStatus(str, Enum):
    ACTIVE = "ACTIVE"
    USER_GATE_REQUIRED = "USER_GATE_REQUIRED"
    SESSION_RECOVERY_REQUIRED = "SESSION_RECOVERY_REQUIRED"
    CLOSED = "CLOSED"


@unique
class WorkItemCoordinatorStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETION_PENDING_RECONCILIATION = "COMPLETION_PENDING_RECONCILIATION"


COORDINATOR_STATUSES: frozenset[str] = frozenset(v.value for v in CoordinatorStatus)
WORK_ITEM_STATUSES: frozenset[str] = frozenset(v.value for v in WorkItemCoordinatorStatus)

# M3/W2 post-reconciliation semantic status vocabulary (durable working
# truth only — never Plan authority, never Work Item acceptance by itself).
# The W1 dispatch lifecycle (wi_status) is untouched: a reconciled Work Item
# keeps wi_status == COMPLETION_PENDING_RECONCILIATION and records
# RECONCILED here. No second lifecycle state machine is created.
WI_SEMANTIC_RECONCILED = "RECONCILED"

WI_SEMANTIC_STATUSES: frozenset[str] = frozenset({WI_SEMANTIC_RECONCILED})

# Bounded durable semantic working state: receipts are keyed by
# canonical_task_id; the cap fails closed rather than truncating.
MAX_RECONCILED_COMPLETIONS = 256

_CAS_MUTABLE_COORDINATOR_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "user_approval_satisfied",
        "wi_status",
        "bindings",
        "wi_semantic_status",
        "reconciled_completions",
        "working_truth",
        "progression_revision",
    }
)

_ALLOWED_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "coordinator_id",
        "plan_authority",
        "plan_digest",
        "plan_source_revision",
        "milestone_id",
        "entry_base",
        "origin_task_main_session_ref",
        "project_id",
        "executor_id",
        "user_approval_satisfied",
        "status",
        "work_items",
        "dependencies",
        "wi_status",
        "bindings",
        "wi_semantic_status",
        "reconciled_completions",
        "working_truth",
        "progression_revision",
        "created_at",
        "updated_at",
        "coordinator_revision",
        "revision_token",
    }
)

_ALLOWED_BINDING_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_task_id",
        "attempt",
        "idempotency_key",
        "completion_ref",
        "completion_card_digest",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_coordinator_revision_token(
    coordinator_id: str,
    revision: int,
    status: CoordinatorStatus,
) -> str:
    payload = f"{coordinator_id}:{revision}:{status.value}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_non_empty_str(value: Any, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    return stripped


def _require_strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


def _parse_status(value: Any) -> CoordinatorStatus:
    if isinstance(value, CoordinatorStatus):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return CoordinatorStatus(value)
        except ValueError as exc:
            raise ValueError(f"Unknown CoordinatorStatus: {value!r}") from exc
    raise TypeError(f"status must be CoordinatorStatus or string, got {type(value).__name__}")


def _parse_wi_status(value: Any, label: str) -> WorkItemCoordinatorStatus:
    if isinstance(value, WorkItemCoordinatorStatus):
        return value
    if isinstance(value, str) and type(value) is str:
        try:
            return WorkItemCoordinatorStatus(value)
        except ValueError as exc:
            raise ValueError(f"Unknown WorkItemCoordinatorStatus for {label}: {value!r}") from exc
    raise TypeError(f"{label} must be WorkItemCoordinatorStatus or string, got {type(value).__name__}")


def _normalize_wi_status_map(
    raw: Mapping[str, Any],
    work_items: tuple[str, ...],
) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"wi_status must be a mapping, got {type(raw).__name__}")
    items = set(work_items)
    normalized: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or type(key) is not str:
            raise TypeError(f"wi_status key must be a string, got {type(key).__name__}")
        if key not in items:
            raise ValueError(f"wi_status references unknown Work Item: {key!r}")
        normalized[key] = _parse_wi_status(val, f"wi_status[{key!r}]").value
    missing = items - set(normalized.keys())
    if missing:
        raise ValueError(f"wi_status missing Work Item entries: {sorted(missing)}")
    return normalized


def _normalize_binding(entry: Any, label: str) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise TypeError(f"{label} must be a mapping, got {type(entry).__name__}")
    extra = set(entry.keys()) - _ALLOWED_BINDING_FIELDS
    if extra:
        raise ValueError(f"Unknown field(s) in {label}: {sorted(extra)}")
    for req in ("canonical_task_id", "attempt", "idempotency_key"):
        if req not in entry:
            raise ValueError(f"Missing required field in {label}: {req!r}")
    canonical_task_id = _require_non_empty_str(entry["canonical_task_id"], f"{label}.canonical_task_id")
    attempt = entry["attempt"]
    if not isinstance(attempt, int) or type(attempt) is not int or attempt < 1:
        raise ValueError(f"{label}.attempt must be an int >= 1, got {attempt!r}")
    idempotency_key = _require_non_empty_str(entry["idempotency_key"], f"{label}.idempotency_key")
    normalized: dict[str, Any] = {
        "canonical_task_id": canonical_task_id,
        "attempt": attempt,
        "idempotency_key": idempotency_key,
    }
    completion_ref = entry.get("completion_ref")
    if completion_ref is not None:
        normalized["completion_ref"] = _require_non_empty_str(completion_ref, f"{label}.completion_ref")
    else:
        normalized["completion_ref"] = None
    completion_card_digest = entry.get("completion_card_digest")
    if completion_card_digest is not None:
        normalized["completion_card_digest"] = _require_non_empty_str(
            completion_card_digest, f"{label}.completion_card_digest"
        )
    else:
        normalized["completion_card_digest"] = None
    return normalized


def _normalize_bindings_map(raw: Mapping[str, Any], work_items: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"bindings must be a mapping, got {type(raw).__name__}")
    items = set(work_items)
    normalized: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or type(key) is not str:
            raise TypeError(f"bindings key must be a string, got {type(key).__name__}")
        if key not in items:
            raise ValueError(f"bindings references unknown Work Item: {key!r}")
        normalized[key] = _normalize_binding(val, f"bindings[{key!r}]")
    return normalized


def _normalize_wi_semantic_status_map(
    raw: Mapping[str, Any],
    work_items: tuple[str, ...],
) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"wi_semantic_status must be a mapping, got {type(raw).__name__}")
    items = set(work_items)
    normalized: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or type(key) is not str:
            raise TypeError(f"wi_semantic_status key must be a string, got {type(key).__name__}")
        if key not in items:
            raise ValueError(f"wi_semantic_status references unknown Work Item: {key!r}")
        if not isinstance(val, str) or type(val) is not str or val not in WI_SEMANTIC_STATUSES:
            raise ValueError(
                f"wi_semantic_status[{key!r}] must be one of {sorted(WI_SEMANTIC_STATUSES)}, "
                f"got {val!r}"
            )
        normalized[key] = val
    return normalized


def _normalize_reconciled_completions_map(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"reconciled_completions must be a mapping, got {type(raw).__name__}")
    if len(raw) > MAX_RECONCILED_COMPLETIONS:
        raise ValueError(
            f"reconciled_completions count ({len(raw)}) exceeds maximum {MAX_RECONCILED_COMPLETIONS}"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or type(key) is not str or not key.strip():
            raise TypeError("reconciled_completions key must be a non-empty canonical_task_id string")
        if not isinstance(val, Mapping):
            raise TypeError(f"reconciled_completions[{key!r}] must be a receipt mapping")
        normalized[key] = dict(val)
    return normalized


_ALLOWED_WORKING_TRUTH_FIELDS: frozenset[str] = frozenset({"projection", "digest"})


def _normalize_working_truth(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError(f"working_truth must be a mapping or None, got {type(raw).__name__}")
    extra = set(raw.keys()) - _ALLOWED_WORKING_TRUTH_FIELDS
    if extra:
        raise ValueError(f"Unknown field(s) in working_truth: {sorted(extra)}")
    if "projection" not in raw or "digest" not in raw:
        raise ValueError("working_truth requires 'projection' and 'digest'")
    projection = raw["projection"]
    if not isinstance(projection, Mapping):
        raise TypeError("working_truth.projection must be a mapping")
    digest = _require_non_empty_str(raw["digest"], "working_truth.digest", max_length=128)
    return {"projection": dict(projection), "digest": digest}


@dataclass(frozen=True)
class TaskMainCoordinatorState:
    """Durable lifecycle + dispatch-progression state for one Milestone activation.

    Working truth for Milestone Work Item *semantic* progress stays with the
    existing WorkingTruthProjection / progression evaluators (W2 domain).
    This record tracks only which governed Work Items the coordinator has
    dispatched, their durable execution bindings, and whether terminal
    completion is still awaiting W2 semantic reconciliation.
    """

    coordinator_id: str
    plan_authority: str
    plan_digest: str
    milestone_id: str
    entry_base: str
    origin_task_main_session_ref: str
    project_id: str
    executor_id: str
    work_items: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...] = ()
    schema_version: int = COORDINATOR_STATE_SCHEMA_VERSION
    plan_source_revision: str | None = None
    user_approval_satisfied: bool = False
    status: CoordinatorStatus = CoordinatorStatus.ACTIVE
    wi_status: dict[str, str] = field(default_factory=dict)
    bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    # M3/W2 durable semantic working state (schema v2): reconciled completion
    # receipts keyed by canonical_task_id, per-Work-Item semantic status, a
    # WorkingTruthProjection payload + digest, and the progression revision.
    wi_semantic_status: dict[str, str] = field(default_factory=dict)
    reconciled_completions: dict[str, dict[str, Any]] = field(default_factory=dict)
    working_truth: dict[str, Any] | None = None
    progression_revision: int = 0
    created_at: str = ""
    updated_at: str = ""
    coordinator_revision: int = 1
    revision_token: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinator_id", _require_non_empty_str(self.coordinator_id, "coordinator_id"))
        object.__setattr__(self, "plan_authority", _require_non_empty_str(self.plan_authority, "plan_authority"))
        object.__setattr__(self, "plan_digest", _require_non_empty_str(self.plan_digest, "plan_digest"))
        object.__setattr__(
            self,
            "origin_task_main_session_ref",
            _require_non_empty_str(self.origin_task_main_session_ref, "origin_task_main_session_ref"),
        )
        object.__setattr__(self, "milestone_id", _require_non_empty_str(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "entry_base", _require_non_empty_str(self.entry_base, "entry_base"))
        object.__setattr__(self, "project_id", _require_non_empty_str(self.project_id, "project_id"))
        object.__setattr__(self, "executor_id", _require_non_empty_str(self.executor_id, "executor_id"))

        if type(self.schema_version) is not int or self.schema_version != COORDINATOR_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be exactly {COORDINATOR_STATE_SCHEMA_VERSION}, "
                f"got {self.schema_version!r}"
            )
        if self.plan_source_revision is not None:
            object.__setattr__(
                self,
                "plan_source_revision",
                _require_non_empty_str(self.plan_source_revision, "plan_source_revision"),
            )
        object.__setattr__(
            self, "user_approval_satisfied", _require_strict_bool(self.user_approval_satisfied, "user_approval_satisfied")
        )
        object.__setattr__(self, "status", _parse_status(self.status))

        if not isinstance(self.work_items, (tuple, list)) or not self.work_items:
            raise ValueError("work_items must be a non-empty tuple/list")
        items: list[str] = []
        seen: set[str] = set()
        for idx, raw in enumerate(self.work_items):
            item = _require_non_empty_str(raw, f"work_items[{idx}]", max_length=128)
            if item in seen:
                raise ValueError(f"duplicate Work Item identity: {item!r}")
            seen.add(item)
            items.append(item)
        canonical_items = tuple(sorted(items))
        object.__setattr__(self, "work_items", canonical_items)

        if not isinstance(self.dependencies, (tuple, list)):
            raise TypeError(f"dependencies must be tuple/list, got {type(self.dependencies).__name__}")
        edges: list[tuple[str, str]] = []
        seen_edges: set[tuple[str, str]] = set()
        for idx, edge in enumerate(self.dependencies):
            if not isinstance(edge, (tuple, list)) or len(edge) != 2:
                raise ValueError(f"dependencies[{idx}] must be a [from, to] pair")
            src = _require_non_empty_str(edge[0], f"dependencies[{idx}].from", max_length=128)
            dst = _require_non_empty_str(edge[1], f"dependencies[{idx}].to", max_length=128)
            if src == dst:
                raise ValueError(f"dependencies[{idx}] self dependency not allowed: {src!r}")
            if src not in seen or dst not in seen:
                raise ValueError(f"dependencies[{idx}] references unknown Work Item")
            pair = (src, dst)
            if pair in seen_edges:
                raise ValueError(f"dependencies[{idx}] duplicate dependency edge: {pair!r}")
            seen_edges.add(pair)
            edges.append(pair)
        object.__setattr__(self, "dependencies", tuple(sorted(edges)))

        object.__setattr__(self, "wi_status", _normalize_wi_status_map(self.wi_status, canonical_items))
        object.__setattr__(self, "bindings", _normalize_bindings_map(self.bindings, canonical_items))
        object.__setattr__(
            self,
            "wi_semantic_status",
            _normalize_wi_semantic_status_map(self.wi_semantic_status, canonical_items),
        )
        object.__setattr__(
            self, "reconciled_completions", _normalize_reconciled_completions_map(self.reconciled_completions)
        )
        object.__setattr__(self, "working_truth", _normalize_working_truth(self.working_truth))

        for wi in self.bindings:
            if self.wi_status.get(wi) == WorkItemCoordinatorStatus.PENDING.value:
                raise ValueError(f"bindings entry for PENDING Work Item {wi!r} contradicts progression state")
        for wi in self.wi_semantic_status:
            if self.wi_status.get(wi) == WorkItemCoordinatorStatus.PENDING.value:
                raise ValueError(f"semantic status for PENDING Work Item {wi!r} contradicts progression state")

        if type(self.progression_revision) is not int or self.progression_revision < 0:
            raise ValueError(
                f"progression_revision must be an int >= 0, got {self.progression_revision!r}"
            )

        if type(self.coordinator_revision) is not int or self.coordinator_revision < 1:
            raise ValueError(f"coordinator_revision must be an int >= 1, got {self.coordinator_revision!r}")
        if not isinstance(self.revision_token, str) or type(self.revision_token) is not str:
            raise TypeError(f"revision_token must be a string, got {type(self.revision_token).__name__}")
        for label in ("created_at", "updated_at"):
            val = getattr(self, label)
            if not isinstance(val, str) or type(val) is not str:
                raise TypeError(f"{label} must be a string, got {type(val).__name__}")

    def with_cas_updates(self, updates: Mapping[str, Any]) -> TaskMainCoordinatorState:
        """Return the successor state for a CAS update (revision + 1, new token)."""
        if not isinstance(updates, Mapping):
            raise TypeError(f"updates must be a mapping, got {type(updates).__name__}")
        extra = set(updates.keys()) - _CAS_MUTABLE_COORDINATOR_FIELDS
        if extra:
            raise ValueError(f"Unknown or immutable coordinator field(s) in CAS update: {sorted(extra)}")
        if not updates:
            raise ValueError("CAS update must change at least one field")
        kwargs: dict[str, Any] = {}
        if "status" in updates:
            kwargs["status"] = _parse_status(updates["status"])
        if "user_approval_satisfied" in updates:
            kwargs["user_approval_satisfied"] = _require_strict_bool(
                updates["user_approval_satisfied"], "user_approval_satisfied"
            )
        if "wi_status" in updates:
            kwargs["wi_status"] = _normalize_wi_status_map(updates["wi_status"], self.work_items)
        if "bindings" in updates:
            kwargs["bindings"] = _normalize_bindings_map(updates["bindings"], self.work_items)
        if "wi_semantic_status" in updates:
            kwargs["wi_semantic_status"] = _normalize_wi_semantic_status_map(
                updates["wi_semantic_status"], self.work_items
            )
        if "reconciled_completions" in updates:
            kwargs["reconciled_completions"] = _normalize_reconciled_completions_map(
                updates["reconciled_completions"]
            )
        if "working_truth" in updates:
            kwargs["working_truth"] = _normalize_working_truth(updates["working_truth"])
        if "progression_revision" in updates:
            revision = updates["progression_revision"]
            if type(revision) is not int or revision < 0:
                raise ValueError(f"progression_revision must be an int >= 0, got {revision!r}")
            kwargs["progression_revision"] = revision
        candidate = replace(
            self,
            coordinator_revision=self.coordinator_revision + 1,
            revision_token=_compute_coordinator_revision_token(
                self.coordinator_id,
                self.coordinator_revision + 1,
                kwargs.get("status", self.status),
            ),
            updated_at=_now_iso(),
            **kwargs,
        )
        # Re-validate cross-field coherence (e.g. no binding left on PENDING).
        candidate.__post_init__()
        return candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "coordinator_id": self.coordinator_id,
            "plan_authority": self.plan_authority,
            "plan_digest": self.plan_digest,
            "plan_source_revision": self.plan_source_revision,
            "milestone_id": self.milestone_id,
            "entry_base": self.entry_base,
            "origin_task_main_session_ref": self.origin_task_main_session_ref,
            "project_id": self.project_id,
            "executor_id": self.executor_id,
            "user_approval_satisfied": self.user_approval_satisfied,
            "status": self.status.value,
            "work_items": list(self.work_items),
            "dependencies": [list(edge) for edge in self.dependencies],
            "wi_status": dict(self.wi_status),
            "bindings": {wi: dict(entry) for wi, entry in self.bindings.items()},
            "wi_semantic_status": dict(self.wi_semantic_status),
            "reconciled_completions": {
                key: dict(receipt) for key, receipt in self.reconciled_completions.items()
            },
            "working_truth": (
                {
                    "projection": dict(self.working_truth["projection"]),
                    "digest": self.working_truth["digest"],
                }
                if self.working_truth is not None
                else None
            ),
            "progression_revision": self.progression_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "coordinator_revision": self.coordinator_revision,
            "revision_token": self.revision_token,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskMainCoordinatorState:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_STATE_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in TaskMainCoordinatorState: {sorted(extra)}")
        for req in (
            "coordinator_id",
            "plan_authority",
            "plan_digest",
            "milestone_id",
            "entry_base",
            "origin_task_main_session_ref",
            "project_id",
            "executor_id",
            "work_items",
            "wi_status",
        ):
            if req not in data:
                raise ValueError(f"Missing required field in TaskMainCoordinatorState: {req!r}")
        state = cls(
            schema_version=data.get("schema_version", COORDINATOR_STATE_SCHEMA_VERSION),
            coordinator_id=data["coordinator_id"],
            plan_authority=data["plan_authority"],
            plan_digest=data["plan_digest"],
            plan_source_revision=data.get("plan_source_revision"),
            milestone_id=data["milestone_id"],
            entry_base=data["entry_base"],
            origin_task_main_session_ref=data["origin_task_main_session_ref"],
            project_id=data["project_id"],
            executor_id=data["executor_id"],
            user_approval_satisfied=data.get("user_approval_satisfied", False),
            status=data.get("status", CoordinatorStatus.ACTIVE.value),
            work_items=tuple(data["work_items"]),
            dependencies=tuple(tuple(edge) for edge in data.get("dependencies", ())),
            wi_status=dict(data.get("wi_status", {})),
            bindings=dict(data.get("bindings", {})),
            wi_semantic_status=dict(data.get("wi_semantic_status", {})),
            reconciled_completions=dict(data.get("reconciled_completions", {})),
            working_truth=data.get("working_truth"),
            progression_revision=data.get("progression_revision", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            coordinator_revision=data.get("coordinator_revision", 1),
            revision_token=data.get("revision_token", ""),
        )
        expected_token = _compute_coordinator_revision_token(
            state.coordinator_id, state.coordinator_revision, state.status
        )
        if state.revision_token != expected_token:
            raise ValueError(
                f"TaskMainCoordinatorState revision_token mismatch for {state.coordinator_id!r}: "
                "stored coordinator truth fails closed on tamper"
            )
        return state


__all__ = [
    "ACK_AFTER_SEMANTIC_RECONCILIATION_IMPLEMENTED",
    "COORDINATOR_STATE_IS_PLAN_AUTHORITY",
    "COORDINATOR_STATE_SCHEMA_VERSION",
    "COORDINATOR_STATUSES",
    "EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE",
    "HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY",
    "MAX_RECONCILED_COMPLETIONS",
    "MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED",
    "REPAIR_AUTOMATION_IMPLEMENTED",
    "REVIEW_AUTOMATION_IMPLEMENTED",
    "W1_CARD_SEMANTIC_APPLICATION_IMPLEMENTED",
    "WI_SEMANTIC_RECONCILED",
    "WI_SEMANTIC_STATUSES",
    "WORK_ITEM_STATUSES",
    "CoordinatorStatus",
    "TaskMainCoordinatorState",
    "WorkItemCoordinatorStatus",
]
