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

# M2/W1 durable orchestration foundation (minimum truth, Human Brake,
# attempt, ProjectState). All new top-level fields are optional with safe
# defaults so v2 records without them still load (backward compatible).
# No second coordinator model is created.
DURABLE_COORDINATOR_STATE_IS_PROGRESS_TRUTH = True
TASK_MAIN_CHAT_CONTEXT_IS_PROGRESS_TRUTH = False
NEW_SECOND_COORDINATOR_STATE_MODEL = False
DURABLE_STATE_REQUIRES_RAW_WORKER_TRANSCRIPT = False
RAW_FAILED_WORKER_TRANSCRIPTS_RETAINED_IN_TASK_MAIN = False
HUMAN_BRAKE_STATE_DURABLE = True
TASK_MAIN_RESTART_CANNOT_FORGET_USER_GATE = True
TASK_MAIN_RESTART_CANNOT_AUTO_RESOLVE_HUMAN_CHECKPOINT = True

# Human Brake durable vocabulary (state + scope). W1 stores the brake truth;
# W2 owns progression/decision policy (no policy here).
HUMAN_BRAKE_STATES: frozenset[str] = frozenset(
    {
        "NONE",
        "NEEDS_INPUT",
        "HUMAN_CHECKPOINT_REQUIRED",
        "USER_DECISION_REQUIRED",
        "USER_GATE_REQUIRED",
        "BLOCKED",
    }
)
HUMAN_BRAKE_SCOPES: frozenset[str] = frozenset(
    {
        "NONE",
        "AFFECTED_WORK",
        "DEPENDENT_SUBGRAPH",
        "WHOLE_MILESTONE",
    }
)

# Durable attempt vocabulary (compact, no raw transcript).
ATTEMPT_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "NONE",
        "TIMEOUT",
        "VALIDATION_FAILURE",
        "SEMANTIC_STOP",
        "MECHANICAL_FAILURE",
        "PLAN_DRIFT",
        "SESSION_RECOVERY",
        "UNKNOWN",
    }
)
ATTEMPT_NEXT_DISPOSITIONS: frozenset[str] = frozenset(
    {
        "NONE",
        "RETRY",
        "ESCALATE",
        "BLOCKED",
        "NEEDS_INPUT",
        "REVIEW_REQUIRED",
    }
)

MAX_HUMAN_BRAKE_REASON_LENGTH = 1024
MAX_NEXT_ACTION_LENGTH = 512
MAX_BLOCKERS = 16
MAX_BLOCKER_REF_LENGTH = 512
MAX_ATTEMPT_STATES = 256

# M1/W1-R1 task-main-owned bounded Work projection durability (AF #45 repair).
# The coordinator durably owns task-main-produced WorkSemanticProjection bound
# records (existing durable task-main/coordinator state reused; no new workflow
# DB, no Plan database, no queue, no event bus, no second task-main store).
# Each record binds one governed Work Item to trusted Plan/Milestone/Project
# identities at commit time; the handoff resolver binds refs from the same
# trusted identities at dispatch time. Raw LLM transcript is never stored.
MAX_WORK_PROJECTIONS: int = 64
WORK_PROJECTION_DURABLE: bool = True
TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION: bool = True

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
        "human_brake",
        "attempt_states",
        "project_state",
        "risk_projection",
        "review_gates",
        "integrated_review",
        "frontier_ref",
        "open_blockers",
        "next_action",
        "work_projections",
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
        "human_brake",
        "attempt_states",
        "project_state",
        "risk_projection",
        "review_gates",
        "integrated_review",
        "frontier_ref",
        "open_blockers",
        "next_action",
        "work_projections",
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
        "handoff_ref",
        "handoff_digest",
        "result_ref",
        "result_digest",
        "review_state",
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
    # M2/W1 per-Work durable refs (optional, fail-closed when present):
    # handoff_ref/handoff_digest bind the TaskHandoff identity used for dispatch;
    # result_ref/result_digest bind the durable result/card identity;
    # review_state tracks per-Work review progression (W2 owns policy).
    for opt_key in ("handoff_ref", "handoff_digest", "result_ref", "result_digest", "review_state"):
        val = entry.get(opt_key)
        if val is not None:
            normalized[opt_key] = _require_non_empty_str(val, f"{label}.{opt_key}", max_length=512)
        else:
            normalized[opt_key] = None
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


_ALLOWED_HUMAN_BRAKE_FIELDS: frozenset[str] = frozenset(
    {"state", "scope", "affected_work", "reason", "reported_at"}
)


def _normalize_human_brake(raw: Any) -> dict[str, Any] | None:
    """Durable Human Brake / user-gate state (W1 foundation, W2 owns policy).

    Supports NEEDS_INPUT, HUMAN_CHECKPOINT_REQUIRED / USER_DECISION_REQUIRED,
    USER_GATE_REQUIRED, BLOCKED plus scope affected_work / dependent_subgraph /
    whole_milestone. Survives restart; restart cannot auto-resolve checkpoints.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError(f"human_brake must be mapping or None, got {type(raw).__name__}")
    extra = set(raw.keys()) - _ALLOWED_HUMAN_BRAKE_FIELDS
    if extra:
        raise ValueError(f"Unknown field(s) in human_brake: {sorted(extra)}")
    state = raw.get("state", "NONE")
    if not isinstance(state, str) or state not in HUMAN_BRAKE_STATES:
        raise ValueError(f"human_brake.state must be one of {sorted(HUMAN_BRAKE_STATES)}, got {state!r}")
    scope = raw.get("scope", "NONE")
    if not isinstance(scope, str) or scope not in HUMAN_BRAKE_SCOPES:
        raise ValueError(f"human_brake.scope must be one of {sorted(HUMAN_BRAKE_SCOPES)}, got {scope!r}")
    affected = raw.get("affected_work", ())
    if affected is None:
        affected = ()
    if not isinstance(affected, (tuple, list)):
        raise TypeError("human_brake.affected_work must be tuple/list")
    norm_affected = tuple(_require_non_empty_str(x, "human_brake.affected_work[]", max_length=128) for x in affected)
    reason = raw.get("reason")
    if reason is not None:
        reason = _require_non_empty_str(reason, "human_brake.reason", max_length=MAX_HUMAN_BRAKE_REASON_LENGTH)
    reported_at = raw.get("reported_at", "")
    if not isinstance(reported_at, str):
        raise TypeError("human_brake.reported_at must be str")
    # Coherence: NONE state requires NONE scope and empty affected.
    if state == "NONE":
        if scope != "NONE" or norm_affected:
            raise ValueError("human_brake NONE requires NONE scope and empty affected_work")
    if scope == "NONE" and norm_affected:
        raise ValueError("human_brake NONE scope cannot carry affected_work")
    if scope == "AFFECTED_WORK" and not norm_affected:
        raise ValueError("human_brake AFFECTED_WORK scope requires non-empty affected_work")
    return {
        "state": state,
        "scope": scope,
        "affected_work": list(norm_affected),
        "reason": reason,
        "reported_at": reported_at,
    }


def _normalize_attempt_states(raw: Any, work_items: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Durable compact attempt truth (no raw transcript, no giant logs).

    Per-Work: attempt count/state, failure class, changed hypothesis/ref,
    risk delta ref, blocking evidence ref, next disposition. Large logs remain
    referenced evidence/artifacts, never stored here.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"attempt_states must be mapping, got {type(raw).__name__}")
    if len(raw) > MAX_ATTEMPT_STATES:
        raise ValueError(f"attempt_states exceeds maximum {MAX_ATTEMPT_STATES}")
    items = set(work_items)
    norm: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or key not in items:
            raise ValueError(f"attempt_states references unknown Work Item: {key!r}")
        if not isinstance(val, Mapping):
            raise TypeError(f"attempt_states[{key!r}] must be mapping")
        allowed = {"attempt", "failure_class", "hypothesis_ref", "risk_delta_ref", "blocking_evidence_ref", "next_disposition"}
        extra = set(val.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in attempt_states[{key!r}]: {sorted(extra)}")
        attempt = val.get("attempt", 1)
        if type(attempt) is not int or attempt < 1:
            raise ValueError(f"attempt_states[{key!r}].attempt must be int >=1")
        failure_class = val.get("failure_class", "NONE")
        if failure_class not in ATTEMPT_FAILURE_CLASSES:
            raise ValueError(f"attempt_states[{key!r}].failure_class must be one of {sorted(ATTEMPT_FAILURE_CLASSES)}")
        next_disp = val.get("next_disposition", "NONE")
        if next_disp not in ATTEMPT_NEXT_DISPOSITIONS:
            raise ValueError(f"attempt_states[{key!r}].next_disposition must be one of {sorted(ATTEMPT_NEXT_DISPOSITIONS)}")
        entry: dict[str, Any] = {"attempt": attempt, "failure_class": failure_class, "next_disposition": next_disp}
        for opt in ("hypothesis_ref", "risk_delta_ref", "blocking_evidence_ref"):
            v = val.get(opt)
            if v is not None:
                entry[opt] = _require_non_empty_str(v, f"attempt_states[{key!r}].{opt}", max_length=512)
            else:
                entry[opt] = None
        norm[key] = entry
    return norm


def _normalize_optional_ref_dict(raw: Any, label: str) -> dict[str, Any] | None:
    """Generic optional ref dict {ref, digest?, freshness?, status?} (bounded)."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError(f"{label} must be mapping or None, got {type(raw).__name__}")
    if "ref" not in raw:
        raise ValueError(f"{label} requires 'ref'")
    ref = _require_non_empty_str(raw["ref"], f"{label}.ref", max_length=512)
    out: dict[str, Any] = {"ref": ref}
    for opt in ("digest", "freshness", "status", "reason"):
        v = raw.get(opt)
        if v is not None:
            out[opt] = _require_non_empty_str(v, f"{label}.{opt}", max_length=512)
    # Preserve any additional bounded str fields? Fail closed on unknown to keep seam tight.
    allowed = {"ref", "digest", "freshness", "status", "reason", "required", "integrated_state"}
    extra = set(raw.keys()) - allowed
    if extra:
        raise ValueError(f"Unknown field(s) in {label}: {sorted(extra)}")
    for k in ("required",):
        if k in raw:
            v = raw[k]
            if type(v) is not bool:
                raise TypeError(f"{label}.{k} must be bool")
            out[k] = v
    for k in ("integrated_state",):
        if k in raw:
            out[k] = _require_non_empty_str(raw[k], f"{label}.{k}", max_length=512)
    return out


def _normalize_open_blockers(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise TypeError("open_blockers must be tuple/list")
    if len(raw) > MAX_BLOCKERS:
        raise ValueError(f"open_blockers exceeds maximum {MAX_BLOCKERS}")
    return tuple(_require_non_empty_str(x, "open_blockers[]", max_length=MAX_BLOCKER_REF_LENGTH) for x in raw)


def _normalize_next_action(raw: Any) -> str | None:
    if raw is None:
        return None
    return _require_non_empty_str(raw, "next_action", max_length=MAX_NEXT_ACTION_LENGTH)


_ALLOWED_WORK_PROJECTION_FIELDS: frozenset[str] = frozenset(
    {
        "projection",
        "plan_authority",
        "plan_digest",
        "milestone_id",
        "project_id",
        "work_item_id",
        # AF #49 M1/W4: optional mechanical Work-source digest binding
        # (absent on legacy records; never model-supplied authority).
        "work_source_digest",
    }
)


def _normalize_work_projections(raw: Any, work_items: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Durable task-main-owned bounded Work projections (M1/W1-R1, AF #45).

    Each entry binds one governed Work Item to trusted Plan/Milestone/Project
    identities at commit time plus the typed bounded semantic projection.
    No raw LLM transcript is stored; only the bounded typed projection dict
    plus identity strings. Fail-closed on unknown Work, oversized table,
    malformed projection, or identity mismatch.

    The projection payload itself is validated through the single bounded
    WorkSemanticProjection contract (lazy import to keep this low-level
    module free of work_plane import cycles at module load).
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"work_projections must be a mapping, got {type(raw).__name__}")
    if len(raw) > MAX_WORK_PROJECTIONS:
        raise ValueError(
            f"work_projections count ({len(raw)}) exceeds maximum {MAX_WORK_PROJECTIONS}"
        )
    items = set(work_items)
    normalized: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or type(key) is not str or not key.strip():
            raise TypeError("work_projections key must be a non-empty string")
        wid = key.strip()
        if len(wid) > 128:
            raise ValueError(f"work_projections key too long: {wid!r}")
        if wid not in items:
            raise ValueError(f"work_projections references unknown Work Item: {wid!r}")
        if not isinstance(val, Mapping):
            raise TypeError(f"work_projections[{wid!r}] must be a mapping")
        extra = set(val.keys()) - _ALLOWED_WORK_PROJECTION_FIELDS
        if extra:
            raise ValueError(
                f"Unknown field(s) in work_projections[{wid!r}]: {sorted(extra)}"
            )
        for req in (
            "projection",
            "plan_authority",
            "plan_digest",
            "milestone_id",
            "project_id",
            "work_item_id",
        ):
            if req not in val:
                raise ValueError(
                    f"Missing required field in work_projections[{wid!r}]: {req!r}"
                )
        stored_wid = _require_non_empty_str(
            val["work_item_id"], f"work_projections[{wid!r}].work_item_id", max_length=128
        )
        if stored_wid != wid:
            raise ValueError(
                f"work_projections[{wid!r}].work_item_id {stored_wid!r} contradicts key"
            )
        _require_non_empty_str(
            val["plan_authority"],
            f"work_projections[{wid!r}].plan_authority",
            max_length=512,
        )
        _require_non_empty_str(
            val["plan_digest"],
            f"work_projections[{wid!r}].plan_digest",
            max_length=512,
        )
        _require_non_empty_str(
            val["milestone_id"],
            f"work_projections[{wid!r}].milestone_id",
            max_length=128,
        )
        _require_non_empty_str(
            val["project_id"],
            f"work_projections[{wid!r}].project_id",
            max_length=512,
        )
        proj_raw = val["projection"]
        if not isinstance(proj_raw, Mapping):
            raise TypeError(f"work_projections[{wid!r}].projection must be a mapping")
        # Single bounded contract validation (no duplicated bounds here).
        try:
            from aota_forge.work_plane.handoff_runtime import WorkSemanticProjection
        except Exception as exc:
            raise ValueError(
                f"work_projections[{wid!r}].projection cannot be validated: {exc}"
            ) from exc
        try:
            validated = WorkSemanticProjection.from_dict(proj_raw)
        except Exception as exc:
            raise ValueError(
                f"work_projections[{wid!r}].projection invalid: {exc}"
            ) from exc
        entry: dict[str, Any] = {
            "projection": validated.to_dict(),
            "plan_authority": str(val["plan_authority"]).strip(),
            "plan_digest": str(val["plan_digest"]).strip(),
            "milestone_id": str(val["milestone_id"]).strip(),
            "project_id": str(val["project_id"]).strip(),
            "work_item_id": stored_wid,
        }
        # AF #49 M1/W4: optional mechanical Work-source digest binding (W4
        # records only; legacy records simply omit it and stay reloadable).
        stored_source_digest = val.get("work_source_digest")
        if stored_source_digest is not None:
            entry["work_source_digest"] = _require_non_empty_str(
                stored_source_digest,
                f"work_projections[{wid!r}].work_source_digest",
                max_length=128,
            )
        normalized[wid] = entry
    return normalized


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
    # M2/W1 minimum durable orchestration truth (all optional, backward compatible):
    # human_brake (NEEDS_INPUT / CHECKPOINT / GATE / BLOCKED + scope),
    # attempt_states (compact failure truth, no transcript),
    # project_state (ref + freshness/status, trusted binding via project_id),
    # risk_projection, review_gates, integrated_review, frontier_ref,
    # open_blockers, next_action. Ready_set stays derived from DAG + wi_status
    # (reconstructible without raw history); stored fields above are the
    # progress truth, chat context is never truth.
    human_brake: dict[str, Any] | None = None
    attempt_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    project_state: dict[str, Any] | None = None
    risk_projection: dict[str, Any] | None = None
    review_gates: dict[str, Any] | None = None
    integrated_review: dict[str, Any] | None = None
    frontier_ref: dict[str, Any] | None = None
    open_blockers: tuple[str, ...] = ()
    next_action: str | None = None
    # M1/W1-R1 task-main-owned bounded Work projections (durable, bound to
    # trusted Plan/Milestone/Project/Work identities; no raw transcript).
    work_projections: dict[str, dict[str, Any]] = field(default_factory=dict)
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
        # M2/W1 minimum durable truth (all optional, backward compatible).
        object.__setattr__(self, "human_brake", _normalize_human_brake(self.human_brake))
        # open_blockers may be stored as list (JSON) or tuple (in-memory).
        _ob = self.open_blockers
        if isinstance(_ob, list):
            _ob = tuple(_ob)
        object.__setattr__(self, "open_blockers", _normalize_open_blockers(_ob))
        object.__setattr__(
            self, "attempt_states", _normalize_attempt_states(self.attempt_states, canonical_items)
        )
        object.__setattr__(self, "project_state", _normalize_optional_ref_dict(self.project_state, "project_state"))
        object.__setattr__(
            self, "risk_projection", _normalize_optional_ref_dict(self.risk_projection, "risk_projection")
        )
        object.__setattr__(self, "review_gates", _normalize_optional_ref_dict(self.review_gates, "review_gates"))
        object.__setattr__(
            self, "integrated_review", _normalize_optional_ref_dict(self.integrated_review, "integrated_review")
        )
        object.__setattr__(self, "frontier_ref", _normalize_optional_ref_dict(self.frontier_ref, "frontier_ref"))
        object.__setattr__(self, "next_action", _normalize_next_action(self.next_action))
        object.__setattr__(
            self,
            "work_projections",
            _normalize_work_projections(self.work_projections, canonical_items),
        )

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
        if "human_brake" in updates:
            kwargs["human_brake"] = _normalize_human_brake(updates["human_brake"])
        if "attempt_states" in updates:
            kwargs["attempt_states"] = _normalize_attempt_states(updates["attempt_states"], self.work_items)
        if "project_state" in updates:
            kwargs["project_state"] = _normalize_optional_ref_dict(updates["project_state"], "project_state")
        if "risk_projection" in updates:
            kwargs["risk_projection"] = _normalize_optional_ref_dict(updates["risk_projection"], "risk_projection")
        if "review_gates" in updates:
            kwargs["review_gates"] = _normalize_optional_ref_dict(updates["review_gates"], "review_gates")
        if "integrated_review" in updates:
            kwargs["integrated_review"] = _normalize_optional_ref_dict(
                updates["integrated_review"], "integrated_review"
            )
        if "frontier_ref" in updates:
            kwargs["frontier_ref"] = _normalize_optional_ref_dict(updates["frontier_ref"], "frontier_ref")
        if "open_blockers" in updates:
            _ob_u = updates["open_blockers"]
            if isinstance(_ob_u, list):
                _ob_u = tuple(_ob_u)
            kwargs["open_blockers"] = _normalize_open_blockers(_ob_u)
        if "next_action" in updates:
            kwargs["next_action"] = _normalize_next_action(updates["next_action"])
        if "work_projections" in updates:
            kwargs["work_projections"] = _normalize_work_projections(
                updates["work_projections"], self.work_items
            )
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
            "human_brake": dict(self.human_brake) if self.human_brake is not None else None,
            "attempt_states": {k: dict(v) for k, v in self.attempt_states.items()},
            "project_state": dict(self.project_state) if self.project_state is not None else None,
            "risk_projection": dict(self.risk_projection) if self.risk_projection is not None else None,
            "review_gates": dict(self.review_gates) if self.review_gates is not None else None,
            "integrated_review": dict(self.integrated_review) if self.integrated_review is not None else None,
            "frontier_ref": dict(self.frontier_ref) if self.frontier_ref is not None else None,
            "open_blockers": list(self.open_blockers),
            "next_action": self.next_action,
            "work_projections": {k: dict(v) for k, v in self.work_projections.items()},
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
            human_brake=data.get("human_brake"),
            attempt_states=dict(data.get("attempt_states", {})),
            project_state=data.get("project_state"),
            risk_projection=data.get("risk_projection"),
            review_gates=data.get("review_gates"),
            integrated_review=data.get("integrated_review"),
            frontier_ref=data.get("frontier_ref"),
            open_blockers=tuple(data.get("open_blockers", ())),
            next_action=data.get("next_action"),
            work_projections=dict(data.get("work_projections", {})),
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
    "ATTEMPT_FAILURE_CLASSES",
    "ATTEMPT_NEXT_DISPOSITIONS",
    "COORDINATOR_STATE_IS_PLAN_AUTHORITY",
    "COORDINATOR_STATE_SCHEMA_VERSION",
    "COORDINATOR_STATUSES",
    "DURABLE_COORDINATOR_STATE_IS_PROGRESS_TRUTH",
    "DURABLE_STATE_REQUIRES_RAW_WORKER_TRANSCRIPT",
    "EXECUTION_STATE_STORE_USED_AS_COORDINATOR_STATE",
    "HERMES_SESSION_DB_IS_COORDINATOR_AUTHORITY",
    "HUMAN_BRAKE_SCOPES",
    "HUMAN_BRAKE_STATES",
    "HUMAN_BRAKE_STATE_DURABLE",
    "MAX_RECONCILED_COMPLETIONS",
    "MAX_WORK_PROJECTIONS",
    "TASK_MAIN_OWNS_WORK_SEMANTIC_PROJECTION",
    "WORK_PROJECTION_DURABLE",
    "MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED",
    "NEW_SECOND_COORDINATOR_STATE_MODEL",
    "RAW_FAILED_WORKER_TRANSCRIPTS_RETAINED_IN_TASK_MAIN",
    "REPAIR_AUTOMATION_IMPLEMENTED",
    "REVIEW_AUTOMATION_IMPLEMENTED",
    "TASK_MAIN_CHAT_CONTEXT_IS_PROGRESS_TRUTH",
    "TASK_MAIN_RESTART_CANNOT_AUTO_RESOLVE_HUMAN_CHECKPOINT",
    "TASK_MAIN_RESTART_CANNOT_FORGET_USER_GATE",
    "W1_CARD_SEMANTIC_APPLICATION_IMPLEMENTED",
    "WI_SEMANTIC_RECONCILED",
    "WI_SEMANTIC_STATUSES",
    "WORK_ITEM_STATUSES",
    "CoordinatorStatus",
    "TaskMainCoordinatorState",
    "WorkItemCoordinatorStatus",
]
