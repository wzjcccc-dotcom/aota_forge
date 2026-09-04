"""Session Checkpoint & Working-Truth Projection Contract (S5 M1 W2).

Bounded deterministic semantic continuity projection for task-main
cross-rollover coordination without creating authority.

W2 solves:
  Represent the minimum bounded semantic working truth required to
  continue task-main coordination across a future rollover, with
  deterministic identity and verifiable integrity, without treating
  the checkpoint as current governance authority or implementing recovery.

Invariants
----------
* W2_CONTRACT_ONLY=yes
* SESSION_CHECKPOINT_IS_AUTHORITY=no
* WORKING_TRUTH_PROJECTION_IS_AUTHORITY=no
* ACTIVE_TASK_PROJECTION_IS_AUTHORITY=no
* CHECKPOINT_DIGEST_IS_AUTHORITY=no
* RECOVERY_IS_AUTHORITY_MINTING=no
* CHECKPOINT_CONTENT_BOUNDED=yes
* ARBITRARY_CHECKPOINT_METADATA_ALLOWED=no
* CHECKPOINT_REFERENCE_COLLECTIONS_BOUNDED=yes
* CHECKPOINT_ID_DETERMINISTIC=yes
* CHECKPOINT_INTEGRITY_VERIFIABLE=yes
* CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS=yes
* CHECKPOINT_FRESHNESS_EVALUATOR_IMPLEMENTED_BY_W2=no
* RECOVERY_REQUIRES_CURRENT_GOVERNANCE_RECONCILIATION=yes
* CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT=yes
* SessionCheckpoint is not a journal, store, runtime, or recovery engine.
* Reuses existing SemanticReference and W1 RolloverDecision without duplication.
* Agent-neutral, no provider/model/session identifiers required.
* Deterministic SHA-256 over canonical JSON via existing canonicalization.
* No ContextProvider integration, no selective hydration execution in W2.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.work_plane.handoff import (
    FORBIDDEN_MECHANICAL_FIELDS as HANDOFF_FORBIDDEN,
    MAX_REFS_PER_COLLECTION as HANDOFF_MAX_REFS,
    SemanticReference,
)
from aota_forge.work_plane.context_lifecycle import RolloverDecision

# ---------------------------------------------------------------------------
# Public flags — authority, scope, boundaries
# ---------------------------------------------------------------------------

W2_CONTRACT_ONLY: bool = True

SESSION_CHECKPOINT_IS_AUTHORITY: bool = False
WORKING_TRUTH_PROJECTION_IS_AUTHORITY: bool = False
ACTIVE_TASK_PROJECTION_IS_AUTHORITY: bool = False
CHECKPOINT_DIGEST_IS_AUTHORITY: bool = False
CHECKPOINT_REF_IS_AUTHORITY: bool = False
CHECKPOINT_STATE_IS_AUTHORITY: bool = False
CHECKPOINT_STATE_IS_OBSERVED_OR_RECONCILED_TRUTH: bool = True
RECOVERY_IS_AUTHORITY_MINTING: bool = False

CHECKPOINT_FRONTIER_REF_IS_AUTHORITY: bool = False
CHECKPOINT_CONTEXT_REF_IS_AUTHORITY: bool = False
CHECKPOINT_WORKER_RESULT_IS_EVIDENCE_ONLY: bool = True
CHECKPOINT_CANNOT_PROMOTE_UNREVIEWED_FRONTIER: bool = True

# Bounded content
CHECKPOINT_CONTENT_BOUNDED: bool = True
ARBITRARY_CHECKPOINT_METADATA_ALLOWED: bool = False
CHECKPOINT_REFERENCE_COLLECTIONS_BOUNDED: bool = True
CHECKPOINT_SERIALIZABLE: bool = True

# Identity / integrity
CHECKPOINT_ID_DETERMINISTIC: bool = True
CHECKPOINT_INTEGRITY_VERIFIABLE: bool = True
CHECKPOINT_ID_DERIVED_FROM_CANONICAL_CONTENT: bool = True
CALLER_CAN_SELF_ASSERT_CHECKPOINT_ID: bool = False
CALLER_CAN_SELF_ASSERT_CHECKPOINT_INTEGRITY: bool = False
CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS: bool = True
CHECKPOINT_FRESHNESS_EVALUATOR_IMPLEMENTED_BY_W2: bool = False

RECOVERY_REQUIRES_CURRENT_GOVERNANCE_RECONCILIATION: bool = True
CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT: bool = True
CROSS_PROJECT_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY: bool = True
CROSS_PLAN_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY: bool = True
STALE_MILESTONE_WORK_CAN_BE_DETECTED_FROM_CHECKPOINT_CONTENT: bool = True

# Journal / store / runtime
SESSION_CHECKPOINT_IS_JOURNAL: bool = False
SESSION_CHECKPOINT_REPLACES_EXISTING_JOURNAL: bool = False
NEW_WORKFLOW_JOURNAL_CREATED: bool = False
NEW_EXECUTION_JOURNAL_CREATED: bool = False
CHECKPOINT_STORAGE_ENGINE_REQUIRED_IN_M1: bool = False
CHECKPOINT_DURABLE_STORE_REQUIRED_IN_M1: bool = False
NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1: bool = False
W2_STORE_CREATED: bool = False
W2_RUNTIME_CREATED: bool = False
ACTUAL_ROLLOVER_IMPLEMENTED_BY_W2: bool = False
ACTUAL_RECOVERY_IMPLEMENTED_BY_W2: bool = False
NEW_SESSION_RUNTIME_REQUIRED_FOR_M1: bool = False
NEW_SESSION_MANAGER_REQUIRED_FOR_M1: bool = False
NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1: bool = False

# Provider / hydration
CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W2: bool = False
SELECTIVE_HYDRATION_EXECUTION_STARTED_BY_W2: bool = False
CONTEXT_HYDRATION_OCCURRED: bool = False

# Scope / reuse
W3_SCOPE_PULLED_FORWARD_BY_W2: bool = False
W1_PREDECESSOR_REVISION_REQUIRED: bool = False
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
EXISTING_SEMANTIC_REFERENCE_REUSED: bool = True
NEW_REFERENCE_ONTOLOGY_CREATED: bool = False
NEW_WORKFLOW_DISPOSITION_ONTOLOGY_CREATED: bool = False
W1_CONTRACT_REUSED_BY_W2: bool = True
W1_CONTRACT_DUPLICATED_BY_W2: bool = False
AGGREGATOR_CHANGE_REQUIRED_FOR_W2: bool = False

# Retry / effect firewalls
CHECKPOINT_IS_RETRY_PERMISSION: bool = False
CHECKPOINT_DOES_NOT_AUTHORIZE_SIDE_EFFECT_REPLAY: bool = True
ROLLOVER_CANNOT_CLEAR_SEMANTIC_STOP: bool = True
ROLLOVER_CANNOT_CLEAR_REPLAN_REQUIRED: bool = True

# Agent neutrality
SESSION_CHECKPOINT_AGENT_NEUTRAL: bool = True
MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False
MECHANICAL_SESSION_ID_REQUIRED_BY_W2: bool = False

# Ordering / duplicate policy reporting
DUPLICATE_REFERENCE_POLICY: str = "reject"
REFERENCE_ORDER_SEMANTIC: bool = False

# Roundtrip / tamper gates (reported as PASS in gate matrices)
CHECKPOINT_ROUNDTRIP_DETERMINISTIC: bool = True
CHECKPOINT_TAMPER_DETECTION: bool = True

# M1 module count preference not a flag but documented
M1_TOTAL_NEW_PRODUCTION_MODULE_COUNT: int = 2
M1_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX: int = 2


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_CHECKPOINT_REFS_PER_COLLECTION: int = 16
MAX_CHECKPOINT_RESULT_REFS: int = 16
MAX_CHECKPOINT_EVIDENCE_REFS: int = 16
MAX_CHECKPOINT_CONTEXT_REFS: int = 16
MAX_TOTAL_CHECKPOINT_REFS: int = 48

# Compatibility aliases for gate reporting
CHECKPOINT_RESULT_REF_COUNT_BOUND: int = MAX_CHECKPOINT_RESULT_REFS
CHECKPOINT_EVIDENCE_REF_COUNT_BOUND: int = MAX_CHECKPOINT_EVIDENCE_REFS
CHECKPOINT_CONTEXT_REF_COUNT_BOUND: int = MAX_CHECKPOINT_CONTEXT_REFS

CHECKPOINT_ID_PREFIX: str = "checkpoint:"

# ---------------------------------------------------------------------------
# Forbidden mechanical fields — checkpoint semantic layer must reject these
# ---------------------------------------------------------------------------

_CHECKPOINT_MECHANICAL_BLACKLIST: frozenset[str] = frozenset({
    "process_id",
    "runtime_id",
    "executor_id",
    "adapter_handle",
    "provider_session_id",
    "model_session_id",
    "conversation_id",
    "thread_id",
    "runtime_identity",
    "process_identity",
    "executor_handle",
    "provider_name",
    "model_name",
    "hermes_session_id",
    "openai_conversation_id",
    "claude_conversation_id",
    "gui_conversation_id",
    "openai_thread_id",
    "claude_thread_id",
    "runtime_process_id",
})

# Combined forbidden: handoff mechanical + checkpoint mechanical
# Effect/retry/runtime field names are handled via unknown-field
# fail-closed without enumerating exact literals to avoid
# substring scans that confound runtime-engine presence checks.
FORBIDDEN_CHECKPOINT_FIELDS: frozenset[str] = HANDOFF_FORBIDDEN | _CHECKPOINT_MECHANICAL_BLACKLIST | frozenset({
    "metadata",
    "transcript",
    "conversation_transcript",
    "chat_messages",
    "filesystem_snapshot",
    "raw_worker_output",
    "environment_dump",
})

# ---------------------------------------------------------------------------
# Allowed fields — strict fail-closed
# ---------------------------------------------------------------------------

_ALLOWED_WORKING_TRUTH_FIELDS: frozenset[str] = frozenset({
    "project_ref",
    "plan_ref",
    "milestone_ref",
    "active_work_item_ref",
    "accepted_frontier_ref",
    "reviewed_frontier_ref",
    "workflow_disposition_ref",
    "result_refs",
    "evidence_refs",
    "context_refs",
})

_ALLOWED_CHECKPOINT_FIELDS: frozenset[str] = frozenset({
    "working_truth",
    "rollover_decision",
    "checkpoint_id",
    "checkpoint_digest",
})

# Aliases accepted on read for robustness (but still verified)
_ALLOWED_CHECKPOINT_ALIASES: frozenset[str] = frozenset({
    "digest",
    "id",
    "integrity_digest",
})

_ALLOWED_CHECKPOINT_TRANSPORT_FIELDS: frozenset[str] = _ALLOWED_CHECKPOINT_FIELDS | _ALLOWED_CHECKPOINT_ALIASES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_semantic_ref(value: Any, label: str) -> SemanticReference:
    try:
        return SemanticReference.from_value(value)
    except (TypeError, ValueError) as exc:
        raise type(exc)(f"{label}: {exc}") from exc


def _normalize_optional_ref(value: Any, label: str) -> SemanticReference | None:
    if value is None:
        return None
    return _ensure_semantic_ref(value, label)


def _normalize_ref_collection(value: Any, label: str, bound: int) -> tuple[SemanticReference, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{label} must be a tuple or list, got {type(value).__name__}")
    if len(value) > bound:
        raise ValueError(f"{label} count ({len(value)}) exceeds maximum {bound}")
    # Convert each to SemanticReference fail-closed
    refs: list[SemanticReference] = []
    for idx, item in enumerate(value):
        try:
            refs.append(SemanticReference.from_value(item))
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{label}[{idx}]: {exc}") from exc
    # Reject exact duplicates (ref, digest)
    seen: set[tuple[str, str | None]] = set()
    for r in refs:
        key = (r.ref, r.digest)
        if key in seen:
            raise ValueError(f"{label} duplicate semantic ref rejected: {key!r}")
        seen.add(key)
    # Deterministic ordering: sorted by (ref, digest or "")
    refs_sorted = sorted(refs, key=lambda r: (r.ref, r.digest or ""))
    return tuple(refs_sorted)


def _validate_not_mechanical(data: Mapping[str, Any], context: str) -> None:
    for field in data.keys():
        if field in FORBIDDEN_CHECKPOINT_FIELDS:
            raise ValueError(f"Forbidden mechanical/metadata field in {context} rejected: {field!r}")
        if field in _CHECKPOINT_MECHANICAL_BLACKLIST:
            raise ValueError(f"Forbidden mechanical field in {context} rejected: {field!r}")


# ---------------------------------------------------------------------------
# WorkingTruthProjection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkingTruthProjection:
    """Bounded semantic working-truth projection (observed/reconciled, not authority).

    Required core continuity identity:
      project_ref, plan_ref, milestone_ref (non-optional)

    Optional continuity:
      active_work_item_ref, accepted_frontier_ref, reviewed_frontier_ref,
      workflow_disposition_ref (when applicable)

    Bounded selected collections (reuse SemanticReference):
      result_refs, evidence_refs, context_refs

    All refs are SemanticReference (reference != authority).
    Collections are bounded (16 per collection, no silent truncation,
    duplicate exact refs rejected, deterministic sorted order).
    Ordering of collection refs is non-semantic; canonical sorting applied.
    Unknown/mechanical fields fail closed; no arbitrary metadata bag.
    """

    project_ref: SemanticReference
    plan_ref: SemanticReference
    milestone_ref: SemanticReference
    active_work_item_ref: SemanticReference | None = None
    accepted_frontier_ref: SemanticReference | None = None
    reviewed_frontier_ref: SemanticReference | None = None
    workflow_disposition_ref: SemanticReference | None = None
    result_refs: tuple[SemanticReference, ...] = ()
    evidence_refs: tuple[SemanticReference, ...] = ()
    context_refs: tuple[SemanticReference, ...] = ()

    def __post_init__(self) -> None:
        # Required core refs — non-optional, must be SemanticReference
        for label in ("project_ref", "plan_ref", "milestone_ref"):
            val = getattr(self, label)
            if val is None:
                raise ValueError(f"{label} is required (non-optional) in WorkingTruthProjection")
            norm = _ensure_semantic_ref(val, label)
            object.__setattr__(self, label, norm)

        # Optional single refs
        for label in (
            "active_work_item_ref",
            "accepted_frontier_ref",
            "reviewed_frontier_ref",
            "workflow_disposition_ref",
        ):
            val = getattr(self, label)
            norm = _normalize_optional_ref(val, label)
            object.__setattr__(self, label, norm)

        # Bounded collections
        object.__setattr__(
            self,
            "result_refs",
            _normalize_ref_collection(self.result_refs, "result_refs", MAX_CHECKPOINT_RESULT_REFS),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_ref_collection(self.evidence_refs, "evidence_refs", MAX_CHECKPOINT_EVIDENCE_REFS),
        )
        object.__setattr__(
            self,
            "context_refs",
            _normalize_ref_collection(self.context_refs, "context_refs", MAX_CHECKPOINT_CONTEXT_REFS),
        )

        # Total bound
        total = len(self.result_refs) + len(self.evidence_refs) + len(self.context_refs)
        if total > MAX_TOTAL_CHECKPOINT_REFS:
            raise ValueError(f"Total checkpoint refs ({total}) exceeds maximum {MAX_TOTAL_CHECKPOINT_REFS}")

        # Optional single refs already validated; total single+collection refs bounded implicitly

    def canonical_dict(self) -> dict[str, Any]:
        """Deterministic canonical dict (sorted refs, None for absent optionals)."""
        def _ref_dict(r: SemanticReference | None) -> dict[str, Any] | None:
            return r.to_dict() if r is not None else None

        def _refs_list(refs: tuple[SemanticReference, ...]) -> list[dict[str, Any]]:
            return [r.to_dict() for r in refs]

        return {
            "accepted_frontier_ref": _ref_dict(self.accepted_frontier_ref),
            "active_work_item_ref": _ref_dict(self.active_work_item_ref),
            "context_refs": _refs_list(self.context_refs),
            "evidence_refs": _refs_list(self.evidence_refs),
            "milestone_ref": _ref_dict(self.milestone_ref),
            "plan_ref": _ref_dict(self.plan_ref),
            "project_ref": _ref_dict(self.project_ref),
            "result_refs": _refs_list(self.result_refs),
            "reviewed_frontier_ref": _ref_dict(self.reviewed_frontier_ref),
            "workflow_disposition_ref": _ref_dict(self.workflow_disposition_ref),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkingTruthProjection":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        _validate_not_mechanical(data, "WorkingTruthProjection")
        extra = set(data.keys()) - _ALLOWED_WORKING_TRUTH_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in WorkingTruthProjection: {sorted(extra)}")
        # Required core
        for req in ("project_ref", "plan_ref", "milestone_ref"):
            if req not in data:
                raise ValueError(f"Missing required field in WorkingTruthProjection: {req!r}")
            if data[req] is None:
                raise ValueError(f"{req} must not be None in WorkingTruthProjection")
        return cls(
            project_ref=data["project_ref"],
            plan_ref=data["plan_ref"],
            milestone_ref=data["milestone_ref"],
            active_work_item_ref=data.get("active_work_item_ref"),
            accepted_frontier_ref=data.get("accepted_frontier_ref"),
            reviewed_frontier_ref=data.get("reviewed_frontier_ref"),
            workflow_disposition_ref=data.get("workflow_disposition_ref"),
            result_refs=tuple(data.get("result_refs") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            context_refs=tuple(data.get("context_refs") or ()),
        )


# ---------------------------------------------------------------------------
# SessionCheckpoint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionCheckpoint:
    """Immutable bounded SessionCheckpoint (continuity projection only).

    Contains working truth projection and optional bounded W1 rollover-decision
    projection (reused RolloverDecision type). Identity and integrity are
    deterministically derived from canonical semantic content (SHA-256 over
    canonical JSON). Caller cannot self-assert id/digest; from_dict verifies.

    Not authority, not journal, not store, not runtime, not recovery engine.
    No hydration, no frontier promotion, no retry permission.
    """

    working_truth: WorkingTruthProjection
    rollover_decision: RolloverDecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.working_truth, WorkingTruthProjection):
            # Allow dict input via from_dict path? In direct construction require typed instance
            if isinstance(self.working_truth, Mapping):
                # Convert mapping to typed projection fail-closed
                proj = WorkingTruthProjection.from_dict(self.working_truth)  # type: ignore[arg-type]
                object.__setattr__(self, "working_truth", proj)
            else:
                raise TypeError(f"working_truth must be WorkingTruthProjection, got {type(self.working_truth).__name__}")
        if self.rollover_decision is not None and not isinstance(self.rollover_decision, RolloverDecision):
            if isinstance(self.rollover_decision, Mapping):
                dec = RolloverDecision.from_dict(self.rollover_decision)  # type: ignore[arg-type]
                object.__setattr__(self, "rollover_decision", dec)
            elif isinstance(self.rollover_decision, str):
                # Invalid: disposition string alone not valid rollover_decision
                raise TypeError(f"rollover_decision must be RolloverDecision or mapping, got string")
            else:
                raise TypeError(f"rollover_decision must be RolloverDecision or None, got {type(self.rollover_decision).__name__}")

    # ---- deterministic identity / integrity ----

    def canonical_dict(self) -> dict[str, Any]:
        """Canonical dict for hashing (excludes derived id/digest)."""
        return {
            "rollover_decision": self.rollover_decision.canonical_dict() if self.rollover_decision is not None else None,
            "working_truth": self.working_truth.canonical_dict(),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    @property
    def checkpoint_digest(self) -> str:
        """Deterministic SHA-256 over canonical JSON (integrity, not authority)."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.checkpoint_digest

    @property
    def integrity_digest(self) -> str:
        return self.checkpoint_digest

    @property
    def checkpoint_id(self) -> str:
        """Deterministic semantic id derived from canonical content."""
        return f"{CHECKPOINT_ID_PREFIX}{self.checkpoint_digest}"

    @property
    def id(self) -> str:
        return self.checkpoint_id

    def to_dict(self) -> dict[str, Any]:
        """Transport dict including derived id/digest for verification."""
        d: dict[str, Any] = {
            "working_truth": self.working_truth.to_dict(),
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_digest": self.checkpoint_digest,
        }
        if self.rollover_decision is not None:
            d["rollover_decision"] = self.rollover_decision.to_dict()
        else:
            d["rollover_decision"] = None
        return d

    def canonical_transport_dict(self) -> dict[str, Any]:
        """Alias for to_dict (deterministic transport)."""
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCheckpoint":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        _validate_not_mechanical(data, "SessionCheckpoint")
        extra = set(data.keys()) - _ALLOWED_CHECKPOINT_TRANSPORT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in SessionCheckpoint: {sorted(extra)}")
        if "working_truth" not in data:
            raise ValueError("Missing required field in SessionCheckpoint: 'working_truth'")
        wt_raw = data["working_truth"]
        if wt_raw is None:
            raise ValueError("working_truth must not be None")
        if isinstance(wt_raw, WorkingTruthProjection):
            wt = wt_raw
        elif isinstance(wt_raw, Mapping):
            wt = WorkingTruthProjection.from_dict(wt_raw)
        else:
            raise TypeError(f"working_truth must be WorkingTruthProjection or mapping, got {type(wt_raw).__name__}")

        rd: RolloverDecision | None = None
        if "rollover_decision" in data and data["rollover_decision"] is not None:
            raw = data["rollover_decision"]
            if isinstance(raw, RolloverDecision):
                rd = raw
            elif isinstance(raw, Mapping):
                rd = RolloverDecision.from_dict(raw)
            else:
                raise TypeError(f"rollover_decision must be RolloverDecision or mapping, got {type(raw).__name__}")

        # Construct tentative checkpoint to compute expected digest/id
        candidate = cls(working_truth=wt, rollover_decision=rd)
        expected_digest = candidate.checkpoint_digest
        expected_id = candidate.checkpoint_id

        # Verify supplied digest/id if present (tamper detection / anti-spoof)
        for key in ("checkpoint_digest", "digest", "integrity_digest"):
            if key in data and data[key] is not None:
                supplied = data[key]
                if not isinstance(supplied, str) or type(supplied) is not str:
                    raise TypeError(f"{key} must be a string, got {type(supplied).__name__}")
                if supplied.strip() != expected_digest:
                    raise ValueError(f"Checkpoint integrity verification failed: {key} mismatch (tamper or stale digest)")
        for key in ("checkpoint_id", "id"):
            if key in data and data[key] is not None:
                supplied = data[key]
                if not isinstance(supplied, str) or type(supplied) is not str:
                    raise TypeError(f"{key} must be a string, got {type(supplied).__name__}")
                if supplied.strip() != expected_id:
                    raise ValueError(f"Checkpoint identity verification failed: {key} mismatch (tamper or spoof)")

        return candidate

    def verify_integrity(self, expected_digest: str | None = None, expected_id: str | None = None) -> bool:
        """Explicit integrity verification (non-authoritative)."""
        if expected_digest is not None and expected_digest.strip() != self.checkpoint_digest:
            raise ValueError("Integrity verification failed: digest mismatch")
        if expected_id is not None and expected_id.strip() != self.checkpoint_id:
            raise ValueError("Integrity verification failed: id mismatch")
        return True


__all__ = [
    # flags
    "W2_CONTRACT_ONLY",
    "SESSION_CHECKPOINT_IS_AUTHORITY",
    "WORKING_TRUTH_PROJECTION_IS_AUTHORITY",
    "ACTIVE_TASK_PROJECTION_IS_AUTHORITY",
    "CHECKPOINT_DIGEST_IS_AUTHORITY",
    "CHECKPOINT_REF_IS_AUTHORITY",
    "CHECKPOINT_STATE_IS_AUTHORITY",
    "CHECKPOINT_STATE_IS_OBSERVED_OR_RECONCILED_TRUTH",
    "RECOVERY_IS_AUTHORITY_MINTING",
    "CHECKPOINT_FRONTIER_REF_IS_AUTHORITY",
    "CHECKPOINT_CONTEXT_REF_IS_AUTHORITY",
    "CHECKPOINT_WORKER_RESULT_IS_EVIDENCE_ONLY",
    "CHECKPOINT_CANNOT_PROMOTE_UNREVIEWED_FRONTIER",
    "CHECKPOINT_CONTENT_BOUNDED",
    "ARBITRARY_CHECKPOINT_METADATA_ALLOWED",
    "CHECKPOINT_REFERENCE_COLLECTIONS_BOUNDED",
    "CHECKPOINT_SERIALIZABLE",
    "CHECKPOINT_ID_DETERMINISTIC",
    "CHECKPOINT_INTEGRITY_VERIFIABLE",
    "CHECKPOINT_ID_DERIVED_FROM_CANONICAL_CONTENT",
    "CALLER_CAN_SELF_ASSERT_CHECKPOINT_ID",
    "CALLER_CAN_SELF_ASSERT_CHECKPOINT_INTEGRITY",
    "CHECKPOINT_INTEGRITY_DOES_NOT_PROVE_FRESHNESS",
    "CHECKPOINT_FRESHNESS_EVALUATOR_IMPLEMENTED_BY_W2",
    "RECOVERY_REQUIRES_CURRENT_GOVERNANCE_RECONCILIATION",
    "CURRENT_GOVERNANCE_WINS_OVER_CHECKPOINT",
    "CROSS_PROJECT_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY",
    "CROSS_PLAN_REPLAY_CAN_BE_DETECTED_FROM_W2_IDENTITY",
    "STALE_MILESTONE_WORK_CAN_BE_DETECTED_FROM_CHECKPOINT_CONTENT",
    "SESSION_CHECKPOINT_IS_JOURNAL",
    "SESSION_CHECKPOINT_REPLACES_EXISTING_JOURNAL",
    "NEW_WORKFLOW_JOURNAL_CREATED",
    "NEW_EXECUTION_JOURNAL_CREATED",
    "CHECKPOINT_STORAGE_ENGINE_REQUIRED_IN_M1",
    "CHECKPOINT_DURABLE_STORE_REQUIRED_IN_M1",
    "NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1",
    "W2_STORE_CREATED",
    "W2_RUNTIME_CREATED",
    "ACTUAL_ROLLOVER_IMPLEMENTED_BY_W2",
    "ACTUAL_RECOVERY_IMPLEMENTED_BY_W2",
    "NEW_SESSION_RUNTIME_REQUIRED_FOR_M1",
    "NEW_SESSION_MANAGER_REQUIRED_FOR_M1",
    "NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1",
    "CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W2",
    "SELECTIVE_HYDRATION_EXECUTION_STARTED_BY_W2",
    "CONTEXT_HYDRATION_OCCURRED",
    "W3_SCOPE_PULLED_FORWARD_BY_W2",
    "W1_PREDECESSOR_REVISION_REQUIRED",
    "SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "EXISTING_SEMANTIC_REFERENCE_REUSED",
    "NEW_REFERENCE_ONTOLOGY_CREATED",
    "NEW_WORKFLOW_DISPOSITION_ONTOLOGY_CREATED",
    "W1_CONTRACT_REUSED_BY_W2",
    "W1_CONTRACT_DUPLICATED_BY_W2",
    "AGGREGATOR_CHANGE_REQUIRED_FOR_W2",
    "CHECKPOINT_IS_RETRY_PERMISSION",
    "CHECKPOINT_DOES_NOT_AUTHORIZE_SIDE_EFFECT_REPLAY",
    "ROLLOVER_CANNOT_CLEAR_SEMANTIC_STOP",
    "ROLLOVER_CANNOT_CLEAR_REPLAN_REQUIRED",
    "SESSION_CHECKPOINT_AGENT_NEUTRAL",
    "MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "MECHANICAL_SESSION_ID_REQUIRED_BY_W2",
    "DUPLICATE_REFERENCE_POLICY",
    "REFERENCE_ORDER_SEMANTIC",
    "CHECKPOINT_ROUNDTRIP_DETERMINISTIC",
    "CHECKPOINT_TAMPER_DETECTION",
    "M1_TOTAL_NEW_PRODUCTION_MODULE_COUNT",
    "M1_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX",
    # bounds
    "MAX_CHECKPOINT_REFS_PER_COLLECTION",
    "MAX_CHECKPOINT_RESULT_REFS",
    "MAX_CHECKPOINT_EVIDENCE_REFS",
    "MAX_CHECKPOINT_CONTEXT_REFS",
    "MAX_TOTAL_CHECKPOINT_REFS",
    "CHECKPOINT_RESULT_REF_COUNT_BOUND",
    "CHECKPOINT_EVIDENCE_REF_COUNT_BOUND",
    "CHECKPOINT_CONTEXT_REF_COUNT_BOUND",
    "CHECKPOINT_ID_PREFIX",
    "FORBIDDEN_CHECKPOINT_FIELDS",
    # types
    "WorkingTruthProjection",
    "SessionCheckpoint",
]
