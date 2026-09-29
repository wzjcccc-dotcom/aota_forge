"""CARD-first governed semantic reconciliation (M3/W2).

M2/F03 closure construction: the M2 ``exact origin session + canonical
task identity + CARD digest`` ACK is lifted to true task-main governed
semantic reconciliation:

.. code-block:: text

    Worker completion
      -> CARD-first exact completion validation
      -> CanonicalResult / CARD / evidence hydration (trusted M2 stores only)
      -> existing progression / review evaluators (authority-negative)
      -> durable working truth + reconciliation receipt (coordinator CAS)
      -> ONLY THEN ACK eligibility

Hard invariants
---------------
* ``ACK_BEFORE_SEMANTIC_RECONCILIATION=no``: ACK eligibility is produced
  only after exact completion identity validation, governed deterministic
  semantic reconciliation, and durable reconciliation evidence persistence.
* A model-produced ``AOTA_COMPLETION_ACK_V1`` string alone is never
  sufficient (``MODEL_ACK_STRING_ALONE_SUFFICIENT=no``): accepting or
  producing an ACK requires stored semantic reconciliation evidence
  (``ack_eligible_for`` / receipt-bound ``ack_token``).
* CARD is evidence, never self-certification:
  ``WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY=no`` and
  ``WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY=no`` are asserted
  against the existing work-plane flags. A CARD PASS alone changes nothing;
  only the existing deterministic progression evaluator advances semantics,
  and only over trusted hydrated evidence plus trusted governed channel
  inputs (validation verdicts, risk triggers, review evidence).
* Raw Worker transcripts are never semantic input:
  ``CARD_FIRST=yes``, ``RAW_WORKER_TRANSCRIPT_PRIMARY=no``. The terminal
  ``CanonicalResult`` is hydrated for terminality/identity only; stdout /
  stderr / result payloads never enter semantic evaluation.
* Authority separation is preserved: coordinator state is working truth,
  never Plan authority (``COORDINATOR_STATE_IS_PLAN_AUTHORITY=no``); the
  M2 ``ExecutionStateStore`` stays worker runtime truth and is never used
  as coordinator state; receipts are durable evidence only
  (``RECONCILIATION_RECEIPT_IS_PLAN_AUTHORITY=no``,
  ``RECONCILIATION_RECEIPT_IS_RESULT_AUTHORITY=no``).
* No new stores, no new delivery machine, no automation beyond derivation:
  ``NEW_GENERIC_WORKFLOW_STORE_CREATED=no`` (receipts live inside the
  coordinator v2 semantic fields),
  ``NEW_DELIVERY_STATE_MACHINE_CREATED=no`` (M2 pending/claimed/
  acknowledged/dropped reused; W2 only gates ACK eligibility),
  ``REPAIR_AUTOMATION_IMPLEMENTED=no``,
  ``MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED=no``,
  ``TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE=no``.
* No Worker privilege expansion and no task-main tool surface in W2:
  reconciliation is a narrow typed internal service
  (``WORKER_CAN_CALL_TASK_MAIN_CONTROL=no``,
  ``TASK_MAIN_RAW_SHELL_REQUIRED=no``).

Trusted input channels
----------------------
``reconcile_worker_completion`` takes ``(coordinator_id, canonical_task_id,
card_digest)`` as primary input identity plus a ``live_plan_view`` and a
``governed_evidence`` bundle. The bundle (validation verdicts, risk
envelope/delta/triggers, review evidence) arrives through the trusted
task-main governed channel — the same trust class as W1 ``MilestonePlanView``
and ``handoff_resolver`` — never from Worker CARD fields, never from model
free text. Every bundle value is fail-closed validated and its digest is
bound into the durable receipt for audit. The caller/model can NOT submit:
result outcome, Work Item status, approval state, next Work Item, ACK
eligibility, or review workflow disposition — all of those are derived by
existing deterministic evaluators or by this module.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.durable_state import ExecutionStateStore, card_digest_for
from aota_forge.runtime.completion import COMPLETION_ACK_TOKEN, parse_completion_ack
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    PlanDriftError,
    TaskMainCoordinatorError,
    evaluate_ready_work_items,
)
from aota_forge.runtime.task_main.coordinator_state import (
    COORDINATOR_STATE_IS_PLAN_AUTHORITY,
    WI_SEMANTIC_RECONCILED,
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    CoordinatorNotFoundError,
    CoordinatorPersistenceFailureError,
    StaleCoordinatorRevisionError,
    TaskMainCoordinatorStore,
)
from aota_forge.runtime.task_main.review_adoption import (
    CompletedTrustedReviewEvidence,
    ReviewAdoptionError,
    TrustedPlanAuthority,
    review_only_adoption_from_state,
    reviewer_attempt_from_canonical_task_id,
    review_cycle_from_canonical_task_id,
    review_only_progression_for,
    revalidate_adopted_frontier,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.milestone_closure import evaluate_milestone_closure_readiness
from aota_forge.work_plane.milestone_review import (
    MilestoneReviewEvidence,
    ReviewCycle,
    ReviewFindingEvidence,
    parse_review_cycle,
)
from aota_forge.work_plane.milestone_review_workflow import (
    FailureFingerprint,
    RepairEvidence,
    RepairHistory,
    WorkflowDisposition,
    evaluate_milestone_review_workflow,
)
from aota_forge.work_plane.progression import (
    WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY,
    WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY,
    FocusedValidationEvidence,
    MilestoneWorkItemGraph,
    ProgressionDisposition,
    WorkerResultCard,
    WorkItemProgressEvidence,
    evaluate_milestone_progression,
)
from aota_forge.work_plane.risk_review import (
    MilestoneRiskEnvelope,
    ReviewEscalationDisposition,
    ReviewTrigger,
    UserEscalationTrigger,
    WorkItemRiskDelta,
    evaluate_work_item_risk_policy,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.session_checkpoint import WorkingTruthProjection

CARD_FIRST = True
RAW_WORKER_TRANSCRIPT_PRIMARY = False
SEMANTIC_RECONCILIATION_IMPLEMENTED = True
SEMANTIC_RECONCILIATION_IDEMPOTENT = True
ACK_BEFORE_SEMANTIC_RECONCILIATION = False
ACK_REQUIRES_DURABLE_RECONCILIATION = True
MODEL_ACK_STRING_ALONE_SUFFICIENT = False
RECONCILIATION_RECEIPT_IS_PLAN_AUTHORITY = False
RECONCILIATION_RECEIPT_IS_RESULT_AUTHORITY = False
M2_DELIVERY_STATE_REUSED = True
NEW_DELIVERY_STATE_MACHINE_CREATED = False
NEW_GENERIC_WORKFLOW_STORE_CREATED = False
REPAIR_AUTOMATION_IMPLEMENTED = False
MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED = False
TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE = False
WORKER_CAN_CALL_TASK_MAIN_CONTROL = False
TASK_MAIN_RAW_SHELL_REQUIRED = False
TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED = False

assert COORDINATOR_STATE_IS_PLAN_AUTHORITY is False
assert WORKER_RESULT_CARD_IS_PROGRESSION_AUTHORITY is False
assert WORKER_RESULT_NEXT_HINT_IS_PROGRESSION_AUTHORITY is False

COMPLETION_KIND_WORK_ITEM = "WORK_ITEM"
COMPLETION_KIND_REVIEW = "MILESTONE_REVIEW"
COMPLETION_KIND_COMPLETED_REVIEW_ADOPTION = "COMPLETED_REVIEW_ADOPTION"

COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_PLAN_AUTHORITY = False
COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_ACCEPTANCE_AUTHORITY = False
COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_RESULT_AUTHORITY = False

# Bounded receipt disposition vocabulary (evidence labels only — the
# evaluators below own the actual semantics).
DISPOSITION_PROGRESSION_COMPLETE = "PROGRESSION_COMPLETE"
DISPOSITION_PROGRESSION_BLOCKED = "PROGRESSION_BLOCKED"
DISPOSITION_REVIEW_READY_FOR_STEWARD = "REVIEW_READY_FOR_STEWARD"
DISPOSITION_REVIEW_REPAIR_REQUIRED = "REVIEW_REPAIR_REQUIRED"
DISPOSITION_REVIEW_RV2_REQUIRED = "REVIEW_RV2_REQUIRED"
DISPOSITION_REVIEW_REPLAN_REQUIRED = "REVIEW_REPLAN_REQUIRED"
DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT = "REVIEW_BLOCKED_ENVIRONMENT"
DISPOSITION_REVIEW_BLOCKED = "REVIEW_BLOCKED"

RECEIPT_DISPOSITIONS: frozenset[str] = frozenset(
    {
        DISPOSITION_PROGRESSION_COMPLETE,
        DISPOSITION_PROGRESSION_BLOCKED,
        DISPOSITION_REVIEW_READY_FOR_STEWARD,
        DISPOSITION_REVIEW_REPAIR_REQUIRED,
        DISPOSITION_REVIEW_RV2_REQUIRED,
        DISPOSITION_REVIEW_REPLAN_REQUIRED,
        DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT,
        DISPOSITION_REVIEW_BLOCKED,
    }
)


class ReconciliationError(TaskMainCoordinatorError):
    """Fail-closed governed reconciliation refusal: no mutation, no ACK."""


class ContradictoryCompletionError(ReconciliationError):
    """Same canonical task reconciled before with a different CARD digest."""


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_of(payload: Mapping[str, Any], *, path: str) -> str:
    return hashlib.sha256(canonical_json(canonicalize(dict(payload), path=path)).encode("utf-8")).hexdigest()


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _require_commit_ref(value: Any, label: str) -> str:
    normalized = _require_non_empty_str(value, label, max_length=40).lower()
    if _COMMIT_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a full lowercase Git commit SHA")
    return normalized


def _frontier_text(value: Any, label: str) -> str:
    if isinstance(value, SemanticReference):
        return value.ref
    if isinstance(value, str) and type(value) is str:
        value = value.strip()
        if value:
            return value
    if isinstance(value, Mapping):
        ref = value.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    ref = getattr(value, "ref", None)
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    raise ReconciliationError(f"{label} must carry a non-empty frontier ref")


def build_ack_token(*, canonical_task_id: str, card_digest: str) -> str:
    """Construct the bounded ACK line for a reconciled completion.

    Same ``AOTA_COMPLETION_ACK_V1`` shape as M2 (no ACK_V2): the ability to
    produce it is what changed — callers must hold durable reconciliation
    evidence (see ``ack_eligible_for``). The token self-verifies through the
    existing ``parse_completion_ack`` gate.
    """
    canonical_task_id = _require_non_empty_str(canonical_task_id, "canonical_task_id")
    card_digest = _require_non_empty_str(card_digest, "card_digest", max_length=128)
    token = f"{COMPLETION_ACK_TOKEN} canonical_task_id={canonical_task_id} card_digest={card_digest}"
    if not parse_completion_ack(
        token, canonical_task_id=canonical_task_id, card_digest=card_digest
    ):
        raise ReconciliationError("constructed ACK token fails the existing ACK parse gate")
    return token


# ---------------------------------------------------------------------------
# CompletionReconciliationReceipt — durable server-produced evidence
# ---------------------------------------------------------------------------

_ALLOWED_RECEIPT_FIELDS: frozenset[str] = frozenset(
    {
        "coordinator_id",
        "plan_authority",
        "milestone_id",
        "work_item_id",
        "completion_kind",
        "canonical_task_id",
        "card_digest",
        "result_handoff_ref",
        "result_digest",
        "prev_coordinator_revision",
        "next_coordinator_revision",
        "progression_revision",
        "progression_disposition",
        "validation_evidence_digest",
        "risk_disposition_digest",
        "review_workflow_disposition",
        "working_truth_digest",
        "governed_evidence",
        "reconciled_at",
        "receipt_digest",
    }
)


@dataclass(frozen=True)
class CompletionReconciliationReceipt:
    """Durable evidence that task-main applied already-governed result evidence.

    Binds plan / milestone / Work Item / canonical task / CARD digest /
    canonical result identity / pre-post coordinator revisions / progression
    disposition / working-truth digest. It is NOT Plan authority and NOT
    result authority — only proof that deterministic reconciliation ran and
    persisted. No event ledger: one receipt per reconciled completion,
    keyed by canonical_task_id inside coordinator v2 state.
    """

    coordinator_id: str
    plan_authority: str
    milestone_id: str
    work_item_id: str | None
    completion_kind: str
    canonical_task_id: str
    card_digest: str
    result_handoff_ref: str
    result_digest: str | None
    prev_coordinator_revision: int
    next_coordinator_revision: int
    progression_revision: int
    progression_disposition: str
    validation_evidence_digest: str | None
    risk_disposition_digest: str | None
    review_workflow_disposition: str | None
    working_truth_digest: str
    governed_evidence: dict[str, Any]
    reconciled_at: str
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinator_id", _require_non_empty_str(self.coordinator_id, "coordinator_id"))
        object.__setattr__(self, "plan_authority", _require_non_empty_str(self.plan_authority, "plan_authority"))
        object.__setattr__(self, "milestone_id", _require_non_empty_str(self.milestone_id, "milestone_id", max_length=128))
        if self.work_item_id is not None:
            object.__setattr__(
                self, "work_item_id", _require_non_empty_str(self.work_item_id, "work_item_id", max_length=128)
            )
        if self.completion_kind not in (COMPLETION_KIND_WORK_ITEM, COMPLETION_KIND_REVIEW):
            raise ValueError(f"completion_kind must be WORK_ITEM or MILESTONE_REVIEW, got {self.completion_kind!r}")
        object.__setattr__(self, "canonical_task_id", _require_non_empty_str(self.canonical_task_id, "canonical_task_id"))
        object.__setattr__(self, "card_digest", _require_non_empty_str(self.card_digest, "card_digest", max_length=128))
        object.__setattr__(
            self, "result_handoff_ref", _require_non_empty_str(self.result_handoff_ref, "result_handoff_ref")
        )
        if self.result_digest is not None:
            object.__setattr__(
                self, "result_digest", _require_non_empty_str(self.result_digest, "result_digest", max_length=512)
            )
        for label in ("prev_coordinator_revision", "next_coordinator_revision", "progression_revision"):
            val = getattr(self, label)
            if type(val) is not int or val < 0:
                raise ValueError(f"{label} must be an int >= 0, got {val!r}")
        if self.next_coordinator_revision != self.prev_coordinator_revision + 1:
            raise ValueError("next_coordinator_revision must equal prev_coordinator_revision + 1")
        if self.progression_disposition not in RECEIPT_DISPOSITIONS:
            raise ValueError(f"progression_disposition must be one of {sorted(RECEIPT_DISPOSITIONS)}")
        for label in ("validation_evidence_digest", "risk_disposition_digest", "review_workflow_disposition"):
            val = getattr(self, label)
            if val is not None:
                object.__setattr__(self, label, _require_non_empty_str(val, label, max_length=128))
        object.__setattr__(
            self, "working_truth_digest", _require_non_empty_str(self.working_truth_digest, "working_truth_digest", max_length=128)
        )
        if not isinstance(self.governed_evidence, dict):
            raise TypeError(f"governed_evidence must be a dict, got {type(self.governed_evidence).__name__}")
        object.__setattr__(self, "reconciled_at", _require_non_empty_str(self.reconciled_at, "reconciled_at"))
        if not isinstance(self.receipt_digest, str) or type(self.receipt_digest) is not str:
            raise TypeError(f"receipt_digest must be a string, got {type(self.receipt_digest).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "plan_authority": self.plan_authority,
            "milestone_id": self.milestone_id,
            "work_item_id": self.work_item_id,
            "completion_kind": self.completion_kind,
            "canonical_task_id": self.canonical_task_id,
            "card_digest": self.card_digest,
            "result_handoff_ref": self.result_handoff_ref,
            "result_digest": self.result_digest,
            "prev_coordinator_revision": self.prev_coordinator_revision,
            "next_coordinator_revision": self.next_coordinator_revision,
            "progression_revision": self.progression_revision,
            "progression_disposition": self.progression_disposition,
            "validation_evidence_digest": self.validation_evidence_digest,
            "risk_disposition_digest": self.risk_disposition_digest,
            "review_workflow_disposition": self.review_workflow_disposition,
            "working_truth_digest": self.working_truth_digest,
            "governed_evidence": dict(self.governed_evidence),
            "reconciled_at": self.reconciled_at,
        }

    def compute_digest(self) -> str:
        return _digest_of(self.canonical_dict(), path="CompletionReconciliationReceipt")

    def with_digest(self) -> CompletionReconciliationReceipt:
        return replace(self, receipt_digest=self.compute_digest())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["receipt_digest"] = self.receipt_digest
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CompletionReconciliationReceipt:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_RECEIPT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in CompletionReconciliationReceipt: {sorted(extra)}")
        for req in (
            "coordinator_id",
            "plan_authority",
            "milestone_id",
            "completion_kind",
            "canonical_task_id",
            "card_digest",
            "result_handoff_ref",
            "prev_coordinator_revision",
            "next_coordinator_revision",
            "progression_revision",
            "progression_disposition",
            "working_truth_digest",
            "governed_evidence",
            "reconciled_at",
            "receipt_digest",
        ):
            if req not in data:
                raise ValueError(f"Missing required field in CompletionReconciliationReceipt: {req!r}")
        receipt = cls(
            coordinator_id=data["coordinator_id"],
            plan_authority=data["plan_authority"],
            milestone_id=data["milestone_id"],
            work_item_id=data.get("work_item_id"),
            completion_kind=data["completion_kind"],
            canonical_task_id=data["canonical_task_id"],
            card_digest=data["card_digest"],
            result_handoff_ref=data["result_handoff_ref"],
            result_digest=data.get("result_digest"),
            prev_coordinator_revision=data["prev_coordinator_revision"],
            next_coordinator_revision=data["next_coordinator_revision"],
            progression_revision=data["progression_revision"],
            progression_disposition=data["progression_disposition"],
            validation_evidence_digest=data.get("validation_evidence_digest"),
            risk_disposition_digest=data.get("risk_disposition_digest"),
            review_workflow_disposition=data.get("review_workflow_disposition"),
            working_truth_digest=data["working_truth_digest"],
            governed_evidence=dict(data["governed_evidence"]),
            reconciled_at=data["reconciled_at"],
            receipt_digest=data["receipt_digest"],
        )
        if receipt.receipt_digest != receipt.compute_digest():
            raise ValueError("CompletionReconciliationReceipt digest mismatch: stored evidence fails closed")
        return receipt


_ADOPTION_RECEIPT_FIELDS: frozenset[str] = frozenset(
    {
        "coordinator_id",
        "project_id",
        "plan_id",
        "plan_authority",
        "plan_digest",
        "plan_source_revision",
        "milestone_id",
        "review_cycle",
        "reviewed_frontier_ref",
        "closure_frontier_ref",
        "frontier_relation",
        "artifact_ref",
        "artifact_digest",
        "review_status",
        "blocking_findings",
        "non_blocking_findings",
        "production_validation_sufficient",
        "closure_ready",
        "frontier_proof_digest",
        "prev_coordinator_revision",
        "next_coordinator_revision",
        "adopted_at",
        "receipt_digest",
    }
)

_ADOPTION_FRONTIER_RELATIONS = frozenset(
    {"EXACT_REVIEWED_FRONTIER", "GOVERNANCE_ONLY_DESCENDANT"}
)


@dataclass(frozen=True)
class CompletedReviewAdoptionReceipt:
    """Durable evidence for adopting a trusted completed review artifact.

    Unlike ``CompletionReconciliationReceipt``, this receipt intentionally has
    no canonical task, CARD, result handoff, or ACK identity.  It records only
    the trusted external review artifact and the exact frontier used by the
    closure infrastructure.  It is evidence, never Plan, acceptance, result,
    or Git authority.
    """

    coordinator_id: str
    project_id: str
    plan_id: str
    plan_authority: str
    plan_digest: str
    plan_source_revision: str
    milestone_id: str
    review_cycle: ReviewCycle
    reviewed_frontier_ref: str
    closure_frontier_ref: str
    frontier_relation: str
    artifact_ref: str
    artifact_digest: str
    review_status: str
    blocking_findings: tuple[str, ...]
    non_blocking_findings: tuple[str, ...]
    production_validation_sufficient: bool
    closure_ready: bool
    frontier_proof_digest: str
    prev_coordinator_revision: int
    next_coordinator_revision: int
    adopted_at: str
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for label in (
            "coordinator_id",
            "project_id",
            "plan_id",
            "plan_authority",
            "plan_source_revision",
            "milestone_id",
            "artifact_ref",
            "review_status",
            "adopted_at",
        ):
            object.__setattr__(self, label, _require_non_empty_str(getattr(self, label), label))
        object.__setattr__(self, "plan_digest", _require_non_empty_str(self.plan_digest, "plan_digest", max_length=128))
        object.__setattr__(self, "review_cycle", parse_review_cycle(self.review_cycle))
        object.__setattr__(
            self,
            "reviewed_frontier_ref",
            _require_commit_ref(self.reviewed_frontier_ref, "reviewed_frontier_ref"),
        )
        object.__setattr__(
            self,
            "closure_frontier_ref",
            _require_commit_ref(self.closure_frontier_ref, "closure_frontier_ref"),
        )
        if self.frontier_relation not in _ADOPTION_FRONTIER_RELATIONS:
            raise ValueError(
                "frontier_relation must be one of "
                f"{sorted(_ADOPTION_FRONTIER_RELATIONS)}"
            )
        if self.frontier_relation == "EXACT_REVIEWED_FRONTIER" and (
            self.reviewed_frontier_ref != self.closure_frontier_ref
        ):
            raise ValueError("exact frontier adoption requires equal reviewed and closure frontiers")
        if self.frontier_relation == "GOVERNANCE_ONLY_DESCENDANT" and (
            self.reviewed_frontier_ref == self.closure_frontier_ref
        ):
            raise ValueError("governance-only descendant adoption requires a changed closure frontier")
        object.__setattr__(self, "artifact_digest", _require_non_empty_str(self.artifact_digest, "artifact_digest", max_length=128))
        object.__setattr__(self, "frontier_proof_digest", _require_non_empty_str(self.frontier_proof_digest, "frontier_proof_digest", max_length=128))
        for label in ("blocking_findings", "non_blocking_findings"):
            raw = getattr(self, label)
            if not isinstance(raw, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list")
            normalized = tuple(
                sorted(
                    {
                        _require_non_empty_str(item, f"{label}[]", max_length=1024)
                        for item in raw
                    }
                )
            )
            object.__setattr__(self, label, normalized)
        for label in ("production_validation_sufficient", "closure_ready"):
            if type(getattr(self, label)) is not bool:
                raise TypeError(f"{label} must be bool")
        for label in ("prev_coordinator_revision", "next_coordinator_revision"):
            value = getattr(self, label)
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} must be an int >= 0")
        if self.next_coordinator_revision != self.prev_coordinator_revision + 1:
            raise ValueError("next_coordinator_revision must equal prev_coordinator_revision + 1")
        if not isinstance(self.receipt_digest, str) or type(self.receipt_digest) is not str:
            raise TypeError("receipt_digest must be a string")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "adopted_at": self.adopted_at,
            "artifact_digest": self.artifact_digest,
            "artifact_ref": self.artifact_ref,
            "blocking_findings": list(self.blocking_findings),
            "closure_frontier_ref": self.closure_frontier_ref,
            "closure_ready": self.closure_ready,
            "coordinator_id": self.coordinator_id,
            "frontier_proof_digest": self.frontier_proof_digest,
            "frontier_relation": self.frontier_relation,
            "milestone_id": self.milestone_id,
            "next_coordinator_revision": self.next_coordinator_revision,
            "non_blocking_findings": list(self.non_blocking_findings),
            "plan_authority": self.plan_authority,
            "plan_digest": self.plan_digest,
            "plan_id": self.plan_id,
            "plan_source_revision": self.plan_source_revision,
            "prev_coordinator_revision": self.prev_coordinator_revision,
            "production_validation_sufficient": self.production_validation_sufficient,
            "project_id": self.project_id,
            "review_cycle": self.review_cycle.value,
            "review_status": self.review_status,
            "reviewed_frontier_ref": self.reviewed_frontier_ref,
        }

    def compute_digest(self) -> str:
        return _digest_of(self.canonical_dict(), path="CompletedReviewAdoptionReceipt")

    def with_digest(self) -> "CompletedReviewAdoptionReceipt":
        return replace(self, receipt_digest=self.compute_digest())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["receipt_digest"] = self.receipt_digest
        payload["completion_kind"] = COMPLETION_KIND_COMPLETED_REVIEW_ADOPTION
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompletedReviewAdoptionReceipt":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        if data.get("completion_kind") != COMPLETION_KIND_COMPLETED_REVIEW_ADOPTION:
            raise ValueError("mapping is not a completed-review adoption receipt")
        extra = set(data) - (_ADOPTION_RECEIPT_FIELDS | {"completion_kind"})
        if extra:
            raise ValueError(
                "Unknown field(s) in CompletedReviewAdoptionReceipt: "
                f"{sorted(extra)}"
            )
        required = _ADOPTION_RECEIPT_FIELDS
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(
                "Missing required field(s) in CompletedReviewAdoptionReceipt: "
                f"{missing}"
            )
        receipt = cls(
            coordinator_id=data["coordinator_id"],
            project_id=data["project_id"],
            plan_id=data["plan_id"],
            plan_authority=data["plan_authority"],
            plan_digest=data["plan_digest"],
            plan_source_revision=data["plan_source_revision"],
            milestone_id=data["milestone_id"],
            review_cycle=data["review_cycle"],
            reviewed_frontier_ref=data["reviewed_frontier_ref"],
            closure_frontier_ref=data["closure_frontier_ref"],
            frontier_relation=data["frontier_relation"],
            artifact_ref=data["artifact_ref"],
            artifact_digest=data["artifact_digest"],
            review_status=data["review_status"],
            blocking_findings=tuple(data["blocking_findings"]),
            non_blocking_findings=tuple(data["non_blocking_findings"]),
            production_validation_sufficient=data["production_validation_sufficient"],
            closure_ready=data["closure_ready"],
            frontier_proof_digest=data["frontier_proof_digest"],
            prev_coordinator_revision=data["prev_coordinator_revision"],
            next_coordinator_revision=data["next_coordinator_revision"],
            adopted_at=data["adopted_at"],
            receipt_digest=data["receipt_digest"],
        )
        if receipt.receipt_digest != receipt.compute_digest():
            raise ValueError(
                "CompletedReviewAdoptionReceipt digest mismatch: stored evidence fails closed"
            )
        return receipt


# ---------------------------------------------------------------------------
# Trusted governed evidence bundles (task-main channel, never Worker/model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernedWorkItemEvidence:
    """Trusted governed inputs for one Work Item completion.

    Same trust class as W1 ``MilestonePlanView`` / ``handoff_resolver``:
    supplied by the operator/Friday governed channel, never derived from
    Worker CARD fields and never from model free text. The coordinator
    validates the work-item binding and records digests in the receipt; the
    existing progression evaluator still enforces verdict/digest/role gates.
    """

    validation_evidence: FocusedValidationEvidence
    risk_envelope: MilestoneRiskEnvelope
    risk_delta: WorkItemRiskDelta | None = None
    review_triggers: tuple[ReviewTrigger | str, ...] = ()
    escalation_triggers: tuple[UserEscalationTrigger | str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.validation_evidence, FocusedValidationEvidence):
            raise TypeError(
                "validation_evidence must be FocusedValidationEvidence, "
                f"got {type(self.validation_evidence).__name__}"
            )
        if not isinstance(self.risk_envelope, MilestoneRiskEnvelope):
            raise TypeError(
                f"risk_envelope must be MilestoneRiskEnvelope, got {type(self.risk_envelope).__name__}"
            )
        if self.risk_delta is not None and not isinstance(self.risk_delta, WorkItemRiskDelta):
            raise TypeError(
                f"risk_delta must be WorkItemRiskDelta or None, got {type(self.risk_delta).__name__}"
            )
        for label in ("review_triggers", "escalation_triggers"):
            val = getattr(self, label)
            if not isinstance(val, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list, got {type(val).__name__}")
            object.__setattr__(self, label, tuple(val))


@dataclass(frozen=True)
class GovernedReviewEvidence:
    """Trusted governed inputs for one milestone-level reviewer completion.

    Reviewer output enters only through CanonicalResult + WorkerResultCard
    (hydrated from M2) + MilestoneReviewEvidence + ReviewFindingEvidence.
    Raw reviewer prose never becomes review authority: the existing review
    workflow evaluator enforces evidence↔card↔handoff binding.
    """

    review_evidence: MilestoneReviewEvidence
    review_findings: tuple[ReviewFindingEvidence, ...] = ()
    review_task_handoff: TaskHandoff | None = None
    expected_frontier: Any = None
    expected_final_frontier: Any = None
    repair_evidences: tuple[RepairEvidence, ...] = ()
    repair_history: RepairHistory | None = None
    failure_fingerprints: tuple[FailureFingerprint, ...] = ()
    pre_failure_fingerprints: tuple[FailureFingerprint, ...] = ()
    post_failure_fingerprints: tuple[FailureFingerprint, ...] = ()
    semantic_boundary_exceeded: bool = False
    review_handoff_ref: str | None = None
    review_handoff_digest: str | None = None
    rv1_evidence: MilestoneReviewEvidence | None = None
    rv1_findings: tuple[ReviewFindingEvidence, ...] = ()
    rv1_result_card: WorkerResultCard | None = None
    rv1_task_handoff: TaskHandoff | None = None
    repair_result_cards: Mapping[str, WorkerResultCard] | tuple[WorkerResultCard, ...] | None = None
    repair_task_handoffs: Mapping[str, TaskHandoff] | tuple[TaskHandoff, ...] | None = None
    validation_evidences: tuple[FocusedValidationEvidence, ...] = ()
    review_triggers: tuple[ReviewTrigger | str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.review_evidence, MilestoneReviewEvidence):
            raise TypeError(
                "review_evidence must be MilestoneReviewEvidence, "
                f"got {type(self.review_evidence).__name__}"
            )
        if not isinstance(self.review_findings, (tuple, list)):
            raise TypeError(f"review_findings must be tuple/list, got {type(self.review_findings).__name__}")
        for idx, finding in enumerate(self.review_findings):
            if not isinstance(finding, ReviewFindingEvidence):
                raise TypeError(f"review_findings[{idx}] must be ReviewFindingEvidence")
        object.__setattr__(self, "review_findings", tuple(self.review_findings))
        if self.review_task_handoff is not None and not isinstance(self.review_task_handoff, TaskHandoff):
            raise TypeError(
                "review_task_handoff must be TaskHandoff or None, "
                f"got {type(self.review_task_handoff).__name__}"
            )
        if not isinstance(self.repair_evidences, (tuple, list)):
            raise TypeError(f"repair_evidences must be tuple/list, got {type(self.repair_evidences).__name__}")
        for idx, repair in enumerate(self.repair_evidences):
            if not isinstance(repair, RepairEvidence):
                raise TypeError(f"repair_evidences[{idx}] must be RepairEvidence")
        object.__setattr__(self, "repair_evidences", tuple(self.repair_evidences))
        if self.repair_history is not None and not isinstance(self.repair_history, RepairHistory):
            raise TypeError(
                f"repair_history must be RepairHistory or None, got {type(self.repair_history).__name__}"
            )
        for label in ("failure_fingerprints", "pre_failure_fingerprints", "post_failure_fingerprints"):
            val = getattr(self, label)
            if not isinstance(val, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list, got {type(val).__name__}")
            for idx, fingerprint in enumerate(val):
                if not isinstance(fingerprint, FailureFingerprint):
                    raise TypeError(f"{label}[{idx}] must be FailureFingerprint")
            object.__setattr__(self, label, tuple(val))
        object.__setattr__(
            self,
            "semantic_boundary_exceeded",
            _require_strict_bool(self.semantic_boundary_exceeded, "semantic_boundary_exceeded"),
        )
        if (self.review_handoff_ref is None) != (self.review_handoff_digest is None):
            raise ValueError("review_handoff_ref and review_handoff_digest must be supplied together")
        if self.review_handoff_ref is not None:
            object.__setattr__(
                self,
                "review_handoff_ref",
                _require_non_empty_str(self.review_handoff_ref, "review_handoff_ref"),
            )
            object.__setattr__(
                self,
                "review_handoff_digest",
                _require_non_empty_str(self.review_handoff_digest, "review_handoff_digest", max_length=128),
            )
        if self.rv1_evidence is not None and not isinstance(self.rv1_evidence, MilestoneReviewEvidence):
            raise TypeError(
                "rv1_evidence must be MilestoneReviewEvidence or None, "
                f"got {type(self.rv1_evidence).__name__}"
            )
        for label in ("rv1_findings", "validation_evidences"):
            val = getattr(self, label)
            if not isinstance(val, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list, got {type(val).__name__}")
        for idx, finding in enumerate(self.rv1_findings):
            if not isinstance(finding, ReviewFindingEvidence):
                raise TypeError(f"rv1_findings[{idx}] must be ReviewFindingEvidence")
        for idx, validation in enumerate(self.validation_evidences):
            if not isinstance(validation, FocusedValidationEvidence):
                raise TypeError(f"validation_evidences[{idx}] must be FocusedValidationEvidence")
        object.__setattr__(self, "rv1_findings", tuple(self.rv1_findings))
        object.__setattr__(self, "validation_evidences", tuple(self.validation_evidences))
        if not isinstance(self.review_triggers, (tuple, list)):
            raise TypeError(f"review_triggers must be tuple/list, got {type(self.review_triggers).__name__}")
        for idx, trigger in enumerate(self.review_triggers):
            if not isinstance(trigger, (ReviewTrigger, str)):
                raise TypeError(f"review_triggers[{idx}] must be ReviewTrigger or str")
        object.__setattr__(self, "review_triggers", tuple(self.review_triggers))
        if self.rv1_result_card is not None and not isinstance(self.rv1_result_card, WorkerResultCard):
            raise TypeError(
                "rv1_result_card must be WorkerResultCard or None, "
                f"got {type(self.rv1_result_card).__name__}"
            )
        if self.rv1_task_handoff is not None and not isinstance(self.rv1_task_handoff, TaskHandoff):
            raise TypeError(
                "rv1_task_handoff must be TaskHandoff or None, "
                f"got {type(self.rv1_task_handoff).__name__}"
            )
        for label, expected_type in (
            ("repair_result_cards", WorkerResultCard),
            ("repair_task_handoffs", TaskHandoff),
        ):
            val = getattr(self, label)
            if val is None:
                continue
            if isinstance(val, Mapping):
                values = tuple(val.values())
                object.__setattr__(self, label, dict(val))
            elif isinstance(val, (tuple, list)):
                values = tuple(val)
                object.__setattr__(self, label, values)
            else:
                raise TypeError(f"{label} must be mapping/tuple/list or None")
            for idx, item in enumerate(values):
                if not isinstance(item, expected_type):
                    raise TypeError(f"{label}[{idx}] must be {expected_type.__name__}")


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Deterministic result of one reconcile call (fresh or replayed).

    ``physical_dispatch_attempts`` is always 0: reconciliation derives
    dispositions only and never dispatches Workers, reviewers, repairs, or
    closure side effects (W3 domain). ``ack_eligible`` is True only with a
    durably persisted receipt; ``ack_token`` is bound to it.
    """

    receipt: CompletionReconciliationReceipt
    ack_eligible: bool
    ack_token: str | None
    replayed: bool
    ready_work_items: tuple[str, ...]
    progression_complete: tuple[str, ...]
    blocked_work_items: tuple[str, ...]
    integrated_review_required: bool
    user_gate_required: bool
    milestone_closure_ready: bool
    rv2_required: bool
    physical_dispatch_attempts: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, CompletionReconciliationReceipt):
            raise TypeError(f"receipt must be CompletionReconciliationReceipt, got {type(self.receipt).__name__}")
        object.__setattr__(self, "ack_eligible", _require_strict_bool(self.ack_eligible, "ack_eligible"))
        if self.ack_token is not None:
            object.__setattr__(self, "ack_token", _require_non_empty_str(self.ack_token, "ack_token", max_length=512))
        object.__setattr__(self, "replayed", _require_strict_bool(self.replayed, "replayed"))
        for label in ("ready_work_items", "progression_complete", "blocked_work_items", "reasons"):
            val = getattr(self, label)
            if not isinstance(val, (tuple, list)):
                raise TypeError(f"{label} must be tuple/list, got {type(val).__name__}")
            object.__setattr__(self, label, tuple(val))
        for label in ("integrated_review_required", "user_gate_required", "milestone_closure_ready", "rv2_required"):
            object.__setattr__(self, label, _require_strict_bool(getattr(self, label), label))
        if type(self.physical_dispatch_attempts) is not int or self.physical_dispatch_attempts != 0:
            raise ValueError("reconciliation never dispatches: physical_dispatch_attempts must be 0")
        if self.ack_eligible and self.ack_token is None:
            raise ValueError("ack_eligible requires a bound ack_token")
        if not self.ack_eligible and self.ack_token is not None:
            raise ValueError("no ack_token without ack_eligible")


@dataclass(frozen=True)
class CompletedReviewAdoptionOutcome:
    """Result of the trusted completed-review adoption seam.

    This is intentionally not an ACK outcome and never claims reviewer
    execution.  ``milestone_closure_ready`` is an evidence projection only;
    the existing Steward/user closure lifecycle remains the authority boundary.
    """

    receipt: CompletedReviewAdoptionReceipt
    replayed: bool
    milestone_closure_ready: bool
    physical_dispatch_attempts: int = 0
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, CompletedReviewAdoptionReceipt):
            raise TypeError("receipt must be CompletedReviewAdoptionReceipt")
        if type(self.replayed) is not bool:
            raise TypeError("replayed must be bool")
        if type(self.milestone_closure_ready) is not bool:
            raise TypeError("milestone_closure_ready must be bool")
        if type(self.physical_dispatch_attempts) is not int or self.physical_dispatch_attempts != 0:
            raise ValueError("completed review adoption never dispatches")
        if not isinstance(self.reasons, (tuple, list)):
            raise TypeError("reasons must be tuple/list")
        object.__setattr__(self, "reasons", tuple(self.reasons))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_live_plan_binding(state: TaskMainCoordinatorState, view: MilestonePlanView) -> None:
    """Fail closed when live Plan authority diverges from durable binding.

    Same comparisons as W1 activation/recovery: authority, digest, milestone
    (never cross the next-Milestone gate), entry base, and governed DAG.
    """
    if not isinstance(view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(view).__name__}")
    if state.plan_authority != view.plan_authority:
        raise PlanDriftError(
            f"plan authority changed: durable {state.plan_authority!r} vs live {view.plan_authority!r}"
        )
    if state.plan_digest != view.plan_digest:
        raise PlanDriftError(
            f"plan digest changed for {state.plan_authority}: durable {state.plan_digest[:16]}... "
            "vs live projection; refusing to continue stale execution truth"
        )
    if state.milestone_id != view.milestone_id:
        raise PlanDriftError(
            f"milestone changed: durable {state.milestone_id!r} vs live {view.milestone_id!r}; "
            "NEXT_MILESTONE_REQUIRES_EXPLICIT_APPROVAL"
        )
    if state.entry_base != view.entry_base:
        raise PlanDriftError(
            f"entry base changed: durable {state.entry_base!r} vs live {view.entry_base!r}"
        )
    if state.plan_source_revision != view.plan_source_revision:
        raise PlanDriftError(
            f"Plan source revision changed: durable {state.plan_source_revision!r} "
            f"vs live {view.plan_source_revision!r}"
        )
    if state.work_items != tuple(sorted(view.graph.work_items)) or state.dependencies != tuple(
        sorted(view.graph.dependencies)
    ):
        raise PlanDriftError("governed Milestone DAG changed under a durable coordinator")


def _validate_adopted_review_binding(
    *,
    state: TaskMainCoordinatorState,
    adoption: Any,
    governed_review: GovernedReviewEvidence,
) -> None:
    """Validate every durable identity bound when the reviewer was dispatched."""

    marker = state.integrated_review
    if not isinstance(marker, Mapping):
        raise ReconciliationError("review-only adoption has no integrated-review marker")
    binding = marker.get("reviewer_binding")
    if not isinstance(binding, Mapping):
        raise ReconciliationError("review-only adoption has no durable reviewer binding")
    try:
        reviewer_attempt = reviewer_attempt_from_canonical_task_id(
            adoption.reviewer_canonical_task_id
        )
        review_cycle = review_cycle_from_canonical_task_id(adoption.reviewer_canonical_task_id)
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(str(exc)) from exc
    expected = (
        (binding.get("canonical_task_id"), adoption.reviewer_canonical_task_id, "reviewer task"),
        (binding.get("attempt"), reviewer_attempt, "reviewer attempt"),
        (binding.get("role"), AgentWorkRole.REVIEWER.value, "reviewer role"),
        (binding.get("project_id"), state.project_id, "reviewer project"),
        (binding.get("plan_authority"), state.plan_authority, "reviewer Plan authority"),
        (binding.get("plan_digest"), state.plan_digest, "reviewer Plan digest"),
        (binding.get("plan_source_revision"), state.plan_source_revision, "reviewer Plan source revision"),
        (binding.get("milestone_id"), state.milestone_id, "reviewer Milestone"),
        (binding.get("frontier_ref"), adoption.frontier_ref, "reviewer frontier"),
        (binding.get("review_cycle"), review_cycle.value, "review cycle"),
    )
    for supplied, expected_value, label in expected:
        if supplied != expected_value:
            raise ReconciliationError(f"{label} binding mismatch")

    evidence = governed_review.review_evidence
    if evidence.review_cycle != review_cycle:
        raise ReconciliationError("review-only adoption requires the bound review cycle")
    if evidence.reviewed_frontier_ref.ref != adoption.frontier_ref:
        raise ReconciliationError("review evidence frontier contradicts adopted frontier")
    handoff = governed_review.review_task_handoff
    if handoff is None:
        raise ReconciliationError("review-only adoption requires the bound reviewer TaskHandoff")
    if handoff.work_role != AgentWorkRole.REVIEWER:
        raise ReconciliationError("reviewer TaskHandoff role contradicts durable reviewer binding")
    if binding.get("task_handoff_digest") != handoff.compute_handoff_digest():
        raise ReconciliationError("reviewer TaskHandoff digest contradicts durable reviewer binding")

    bound_handoff_ref = binding.get("handoff_ref")
    bound_handoff_digest = binding.get("handoff_digest")
    if governed_review.review_handoff_ref != bound_handoff_ref:
        raise ReconciliationError("reviewer durable handoff ref contradicts durable reviewer binding")
    if governed_review.review_handoff_digest != bound_handoff_digest:
        raise ReconciliationError("reviewer durable handoff digest contradicts durable reviewer binding")
    if handoff.project_ref is not None and handoff.project_ref.ref != state.project_id:
        raise ReconciliationError("reviewer TaskHandoff project identity contradicts coordinator")
    if handoff.plan_ref is not None:
        if handoff.plan_ref.ref != state.plan_authority:
            raise ReconciliationError("reviewer TaskHandoff Plan authority contradicts coordinator")
        if handoff.plan_ref.digest is not None and handoff.plan_ref.digest != state.plan_digest:
            raise ReconciliationError("reviewer TaskHandoff Plan digest contradicts coordinator")
    if handoff.milestone_ref is not None and handoff.milestone_ref.ref != state.milestone_id:
        raise ReconciliationError("reviewer TaskHandoff Milestone contradicts coordinator")


def _require_active_for_reconciliation(
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
) -> TaskMainCoordinatorState:
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    state = store.get(coordinator_id)
    if state is None:
        raise CoordinatorNotFoundError(coordinator_id)
    if state.status != CoordinatorStatus.ACTIVE:
        raise ReconciliationError(
            f"coordinator {coordinator_id!r} status is {state.status.value}; "
            "semantic reconciliation requires ACTIVE"
        )
    _check_live_plan_binding(state, live_plan_view)
    if live_plan_view.user_gate_blocked:
        raise ReconciliationError(
            "live governed view is gate-blocked; refusing semantic apply under the user gate"
        )
    return state


def _hydrate_completion(
    execution_store: ExecutionStateStore,
    *,
    coordinator_id: str,
    canonical_task_id: str,
    card_digest: str,
) -> tuple[Any, WorkerResultCard]:
    """CARD-first hydration from trusted M2 durable truth.

    Hydrates the terminal record + CARD from the ExecutionStateStore and
    verifies every identity binding. Raw Worker transcripts (stdout/stderr/
    result payloads) are never read here and never enter semantics.
    """
    if not isinstance(execution_store, ExecutionStateStore):
        raise TypeError(
            f"execution_store must be ExecutionStateStore, got {type(execution_store).__name__}"
        )
    canonical_task_id = _require_non_empty_str(canonical_task_id, "canonical_task_id")
    card_digest = _require_non_empty_str(card_digest, "card_digest", max_length=128)
    record = execution_store.get(canonical_task_id)
    if record is None:
        raise ReconciliationError(
            f"no durable M2 execution truth for {canonical_task_id!r}; refusing to guess completion"
        )
    if record.canonical_task_id != canonical_task_id:
        raise ReconciliationError("M2 record identity contradicts completion identity")
    if not record.canonical_task_state.is_terminal:
        raise ReconciliationError(
            f"M2 execution {canonical_task_id!r} is not terminal; no semantic apply"
        )
    if record.terminal_result is None:
        raise ReconciliationError(
            f"M2 execution {canonical_task_id!r} has no durable terminal result"
        )
    card_dict = record.worker_result_card
    if card_dict is None:
        raise ReconciliationError(
            f"M2 execution {canonical_task_id!r} has no durable CARD; CARD-first requires CARD truth"
        )
    if not isinstance(card_dict, Mapping):
        raise ReconciliationError("durable CARD payload is not a mapping")
    try:
        card = WorkerResultCard.from_dict(card_dict)
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"durable CARD fails strict validation: {exc}") from exc
    # Exact identity binding: CARD task identity, recomputed digest, and the
    # durable record digest must all agree with the completion identity.
    if card.task_ref != canonical_task_id:
        raise ReconciliationError(
            f"CARD task_ref {card.task_ref!r} contradicts completion {canonical_task_id!r}"
        )
    if card.compute_card_digest() != card_digest:
        raise ReconciliationError("CARD digest recomputation contradicts completion card_digest")
    if record.worker_result_card_digest != card_digest:
        raise ReconciliationError("M2 durable CARD digest contradicts completion card_digest")
    if card_digest_for(dict(card_dict)) != card_digest:
        raise ReconciliationError("M2 CARD digest function contradicts completion card_digest")
    if card.result_handoff_ref.ref != canonical_task_id:
        raise ReconciliationError("CARD result_handoff_ref contradicts canonical task identity")
    return record, card


def _graph_for(state: TaskMainCoordinatorState) -> MilestoneWorkItemGraph:
    return MilestoneWorkItemGraph(
        milestone_ref=state.milestone_id,
        work_items=list(state.work_items),
        dependencies=[list(edge) for edge in state.dependencies],
    )


def _derive_risk_disposition(
    evidence: GovernedWorkItemEvidence, *, work_item_id: str, milestone_id: str
) -> tuple[ReviewEscalationDisposition, str]:
    """Derive (never accept) the per-Work-Item risk disposition deterministically."""
    envelope = evidence.risk_envelope
    if envelope.milestone_ref != milestone_id:
        raise ReconciliationError(
            f"risk envelope milestone {envelope.milestone_ref!r} contradicts {milestone_id!r}"
        )
    delta = evidence.risk_delta
    if delta is not None:
        if delta.work_item_ref != work_item_id:
            raise ReconciliationError("risk delta Work Item contradicts reconciled Work Item")
        if delta.milestone_ref != milestone_id:
            raise ReconciliationError("risk delta Milestone contradicts reconciled Milestone")
    try:
        disposition = evaluate_work_item_risk_policy(
            envelope,
            delta,
            review_triggers=list(evidence.review_triggers),
            escalation_triggers=list(evidence.escalation_triggers),
        )
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"risk policy inputs fail closed: {exc}") from exc
    return disposition, _digest_of(disposition.to_dict(), path="ReviewEscalationDisposition")


def _stored_governed_inputs(receipt_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    governed = receipt_payload.get("governed_evidence")
    if not isinstance(governed, Mapping):
        raise ReconciliationError("durable receipt carries no governed evidence inputs")
    return governed


def _hydrate_prior_rv1_review(
    *,
    state: TaskMainCoordinatorState,
    execution_store: ExecutionStateStore,
    worktree_root: WorktreeSandboxBoundary | None,
    provided_handoff: TaskHandoff | None = None,
) -> tuple[
    MilestoneReviewEvidence,
    tuple[ReviewFindingEvidence, ...],
    WorkerResultCard,
    TaskHandoff,
]:
    """Rebuild the durable RV1 bundle required before an RV2 evaluation."""
    candidates: list[CompletionReconciliationReceipt] = []
    for payload in state.reconciled_completions.values():
        try:
            receipt = CompletionReconciliationReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(f"durable receipt fails strict validation: {exc}") from exc
        if receipt.completion_kind != COMPLETION_KIND_REVIEW:
            continue
        raw_review = receipt.governed_evidence.get("review_evidence")
        if not isinstance(raw_review, Mapping):
            continue
        try:
            review = MilestoneReviewEvidence.from_dict(raw_review)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(f"durable RV1 evidence fails strict validation: {exc}") from exc
        if review.review_cycle is ReviewCycle.RV1:
            candidates.append(receipt)
    if not candidates:
        raise ReconciliationError("RV2 reconciliation requires one durable RV1 review receipt")
    candidates.sort(key=lambda item: (item.next_coordinator_revision, item.canonical_task_id))
    receipt = candidates[-1]
    try:
        evidence = MilestoneReviewEvidence.from_dict(
            receipt.governed_evidence["review_evidence"]
        )
        findings = tuple(
            ReviewFindingEvidence.from_dict(item)
            for item in receipt.governed_evidence.get("review_findings", ())
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ReconciliationError(f"durable RV1 review bundle is invalid: {exc}") from exc
    _, card = _hydrate_completion(
        execution_store,
        coordinator_id=state.coordinator_id,
        canonical_task_id=receipt.canonical_task_id,
        card_digest=receipt.card_digest,
    )
    handoff_ref = receipt.governed_evidence.get("review_handoff_ref")
    handoff_digest = receipt.governed_evidence.get("review_handoff_digest")
    if not isinstance(handoff_ref, str) or not isinstance(handoff_digest, str):
        raise ReconciliationError("durable RV1 review receipt has no handoff identity")
    if provided_handoff is not None:
        if provided_handoff.compute_handoff_digest() != handoff_digest:
            raise ReconciliationError("governed RV1 handoff contradicts durable RV1 handoff digest")
        handoff = provided_handoff
    else:
        if worktree_root is None:
            raise ReconciliationError("RV2 reconciliation requires the durable RV1 handoff sandbox")
        try:
            from aota_forge.work_plane.handoff_store import handoff_open
            from aota_forge.work_plane.task_facade import load_trusted_work_item_task_handoff

            opened = handoff_open(handoff_ref, "full", sandbox=worktree_root)
            if opened.get("digest") != handoff_digest:
                raise ValueError("durable RV1 handoff digest mismatch")
            handoff = load_trusted_work_item_task_handoff(
                opened=opened,
                sandbox=worktree_root,
                require_explicit_work_role=True,
                require_explicit_work_identity=True,
            )
        except Exception as exc:
            raise ReconciliationError(f"durable RV1 handoff reopen failed: {exc}") from exc
    return evidence, findings, card, handoff


def _rebuild_work_item_inputs(
    *,
    state: TaskMainCoordinatorState,
    execution_store: ExecutionStateStore,
    current_work_item: str | None = None,
    current_progress: WorkItemProgressEvidence | None = None,
    current_validation: FocusedValidationEvidence | None = None,
    current_disposition: ReviewEscalationDisposition | None = None,
) -> tuple[
    list[WorkItemProgressEvidence],
    list[FocusedValidationEvidence],
    dict[str, ReviewEscalationDisposition],
    dict[str, WorkerResultCard],
]:
    """Rebuild the full evaluator input set from durable truth.

    Previously reconciled completions rehydrate their CARDs from the still-
    durable M2 records (verified against receipt digests) and re-derive risk
    dispositions from stored governed inputs — nothing is trusted from memory.
    """
    progress: list[WorkItemProgressEvidence] = []
    validations: list[FocusedValidationEvidence] = []
    dispositions: dict[str, ReviewEscalationDisposition] = {}
    cards: dict[str, WorkerResultCard] = {}
    for reconciled_task_id in sorted(state.reconciled_completions.keys()):
        payload = state.reconciled_completions[reconciled_task_id]
        try:
            receipt = CompletionReconciliationReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(f"durable receipt fails strict validation: {exc}") from exc
        if receipt.completion_kind != COMPLETION_KIND_WORK_ITEM or receipt.work_item_id is None:
            continue
        if receipt.work_item_id == current_work_item:
            continue
        _, card = _hydrate_completion(
            execution_store,
            coordinator_id=state.coordinator_id,
            canonical_task_id=reconciled_task_id,
            card_digest=receipt.card_digest,
        )
        governed = _stored_governed_inputs(payload)
        try:
            progress_evidence = WorkItemProgressEvidence.from_dict(governed["progress_evidence"])
            validation_evidence = FocusedValidationEvidence.from_dict(governed["validation_evidence"])
            envelope = MilestoneRiskEnvelope.from_dict(governed["risk_envelope"])
            delta_raw = governed.get("risk_delta")
            delta = WorkItemRiskDelta.from_dict(delta_raw) if delta_raw is not None else None
            disposition = evaluate_work_item_risk_policy(
                envelope,
                delta,
                review_triggers=list(governed.get("review_triggers", ())),
                escalation_triggers=list(governed.get("escalation_triggers", ())),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ReconciliationError(
                f"durable governed inputs for {receipt.work_item_id!r} fail closed: {exc}"
            ) from exc
        if progress_evidence.worker_result_digest != receipt.card_digest:
            raise ReconciliationError("durable progress evidence contradicts receipt CARD digest")
        progress.append(progress_evidence)
        validations.append(validation_evidence)
        dispositions[receipt.work_item_id] = disposition
        cards[card.result_handoff_ref.ref] = card
    if current_work_item is not None:
        assert current_progress is not None
        assert current_validation is not None
        assert current_disposition is not None
        progress.append(current_progress)
        validations.append(current_validation)
        dispositions[current_work_item] = current_disposition
    return progress, validations, dispositions, cards


def _resolve_task_handoffs(
    *,
    state: TaskMainCoordinatorState,
    handoff_resolver: Callable[[str], TaskHandoff] | None,
    current_work_item: str | None,
    current_canonical_task_id: str | None = None,
) -> dict[str, TaskHandoff] | None:
    """Resolve governed handoffs keyed by canonical task identity.

    The progression evaluator looks handoffs up by task identity
    (``worker_card.task_ref`` / result refs), so the mapping is keyed by
    ``canonical_task_id`` — never by Work Item.
    """
    if handoff_resolver is None:
        return None
    if not callable(handoff_resolver):
        raise TypeError("handoff_resolver must be callable or None")
    handoffs: dict[str, TaskHandoff] = {}
    for payload in state.reconciled_completions.values():
        try:
            receipt = CompletionReconciliationReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(f"durable receipt fails strict validation: {exc}") from exc
        if receipt.completion_kind != COMPLETION_KIND_WORK_ITEM or receipt.work_item_id is None:
            continue
        if receipt.work_item_id == current_work_item:
            continue
        handoff = handoff_resolver(receipt.work_item_id)
        if not isinstance(handoff, TaskHandoff):
            raise TypeError(
                f"handoff_resolver must return TaskHandoff for {receipt.work_item_id!r}"
            )
        handoffs[receipt.canonical_task_id] = handoff
    if current_work_item is not None:
        if current_canonical_task_id is None:
            raise ReconciliationError("current handoff resolution requires the completion task identity")
        handoff = handoff_resolver(current_work_item)
        if not isinstance(handoff, TaskHandoff):
            raise TypeError(f"handoff_resolver must return TaskHandoff for {current_work_item!r}")
        if handoff.work_item_ref is not None and handoff.work_item_ref.ref != current_work_item:
            raise ReconciliationError("governed handoff Work Item contradicts reconciled Work Item")
        handoffs[current_canonical_task_id] = handoff
    return handoffs


def _build_working_truth(
    *,
    project_id: str,
    plan_authority: str,
    milestone_id: str,
    state: TaskMainCoordinatorState,
    execution_store: ExecutionStateStore,
    extra_result_refs: tuple[tuple[str, str], ...] = (),
    reviewed_frontier_ref: SemanticReference | None = None,
    workflow_disposition_ref: SemanticReference | None = None,
) -> tuple[dict[str, Any], str]:
    """Project durable working truth from reconciled completion identities.

    Reuses ``WorkingTruthProjection`` semantics: reconciled canonical result
    identities become ``result_refs`` and receipt digests become
    ``evidence_refs``. References only — never Plan authority.
    """
    result_keys: dict[str, str] = {}
    evidence_keys: dict[str, str] = {}
    for reconciled_task_id in sorted(state.reconciled_completions.keys()):
        payload = state.reconciled_completions[reconciled_task_id]
        receipt = CompletionReconciliationReceipt.from_dict(payload)
        result_keys[reconciled_task_id] = receipt.card_digest
        evidence_keys[receipt.receipt_digest] = receipt.receipt_digest
    for reconciled_task_id, digest in extra_result_refs:
        result_keys[reconciled_task_id] = digest
    try:
        projection = WorkingTruthProjection(
            project_ref=SemanticReference(ref=project_id),
            plan_ref=SemanticReference(ref=plan_authority),
            milestone_ref=SemanticReference(ref=milestone_id),
            reviewed_frontier_ref=reviewed_frontier_ref,
            workflow_disposition_ref=workflow_disposition_ref,
            result_refs=tuple(
                SemanticReference(ref=task_id, digest=digest)
                for task_id, digest in sorted(result_keys.items())
            ),
            evidence_refs=tuple(
                SemanticReference(ref=digest) for digest in sorted(evidence_keys)
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"working-truth projection fails closed: {exc}") from exc
    payload = projection.canonical_dict()
    digest = hashlib.sha256(
        canonical_json(canonicalize(payload, path="WorkingTruthProjection")).encode("utf-8")
    ).hexdigest()
    return {"projection": payload, "digest": digest}, digest


def _persist_reconciliation(
    *,
    store: TaskMainCoordinatorStore,
    state: TaskMainCoordinatorState,
    work_item_id: str | None,
    receipt: CompletionReconciliationReceipt,
    working_truth: Mapping[str, Any],
    progression_revision: int,
) -> tuple[TaskMainCoordinatorState, CompletionReconciliationReceipt, bool]:
    """CAS-persist one semantic reconciliation (exactly-once under contention).

    Returns ``(durable_state, effective_receipt, replayed)``. On a lost CAS
    race the durable truth is re-read: the same completion already reconciled
    replays the winner's stored receipt deterministically; a contradictory
    record fails closed. A persist failure propagates with no ACK eligibility.
    """
    reconciled = {key: dict(val) for key, val in state.reconciled_completions.items()}
    reconciled[receipt.canonical_task_id] = receipt.to_dict()
    wi_semantic = dict(state.wi_semantic_status)
    if work_item_id is not None:
        wi_semantic[work_item_id] = WI_SEMANTIC_RECONCILED
    updates = {
        "wi_semantic_status": wi_semantic,
        "reconciled_completions": reconciled,
        "working_truth": {"projection": dict(working_truth["projection"]), "digest": working_truth["digest"]},
        "progression_revision": progression_revision,
    }
    try:
        persisted = store.compare_and_swap(
            state.coordinator_id,
            state.coordinator_revision,
            updates,
            state.revision_token,
        )
    except StaleCoordinatorRevisionError:
        refreshed = store.get(state.coordinator_id)
        if refreshed is None:
            raise CoordinatorNotFoundError(state.coordinator_id)
        existing = refreshed.reconciled_completions.get(receipt.canonical_task_id)
        if existing is not None:
            stored = CompletionReconciliationReceipt.from_dict(existing)
            if stored.card_digest != receipt.card_digest:
                raise ContradictoryCompletionError(
                    f"canonical task {receipt.canonical_task_id!r} already reconciled "
                    "with a different CARD digest; refusing overwrite"
                )
            # Lost the race to the same completion: replay the winner's
            # stored receipt instead of the unpersisted candidate.
            return refreshed, stored, True
        raise ReconciliationError(
            "coordinator revision raced without reconciling this completion; retry safely"
        )
    except CoordinatorPersistenceFailureError:
        raise
    # Re-read and verify: the receipt must be durably present with its digest.
    verified = store.get(state.coordinator_id)
    if verified is None:
        raise CoordinatorNotFoundError(state.coordinator_id)
    stored_payload = verified.reconciled_completions.get(receipt.canonical_task_id)
    if stored_payload is None:
        raise ReconciliationError("reconciliation receipt missing after persist; ACK refused")
    stored = CompletionReconciliationReceipt.from_dict(stored_payload)
    if stored.receipt_digest != receipt.receipt_digest:
        raise ReconciliationError("reconciliation receipt digest mismatch after persist; ACK refused")
    return persisted, receipt, False


def reconcile_adopted_completed_review(
    *,
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    completed_review: CompletedTrustedReviewEvidence,
    trusted_plan_authority: TrustedPlanAuthority,
    review_adoption_worktree_root: WorktreeSandboxBoundary,
    review_adoption_git: Any | None = None,
) -> CompletedReviewAdoptionOutcome:
    """Persist one already-completed trusted review without reviewer execution.

    This sibling to ``reconcile_review_completion`` is deliberately not CARD
    reconciliation.  It accepts only the typed, independently validated
    completed-review carrier, records no Work Item progression, and uses the
    same coordinator CAS/readback boundary for exactly-once adoption.
    """

    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(completed_review, CompletedTrustedReviewEvidence):
        raise TypeError(
            "completed_review must be CompletedTrustedReviewEvidence, "
            f"got {type(completed_review).__name__}"
        )
    if not isinstance(trusted_plan_authority, TrustedPlanAuthority):
        raise TypeError("trusted_plan_authority must be TrustedPlanAuthority")
    if not isinstance(review_adoption_worktree_root, WorktreeSandboxBoundary):
        raise TypeError("review_adoption_worktree_root must be WorktreeSandboxBoundary")

    state = _require_active_for_reconciliation(store, coordinator_id, live_plan_view)
    if completed_review.project_id != state.project_id:
        raise ReconciliationError("completed review project contradicts coordinator")
    if completed_review.project_id != trusted_plan_authority.project_id:
        raise ReconciliationError("completed review project contradicts trusted Plan authority")
    if completed_review.plan_id != trusted_plan_authority.plan_id:
        raise ReconciliationError("completed review Plan identity contradicts trusted Plan authority")
    if completed_review.plan_authority != state.plan_authority:
        raise ReconciliationError("completed review Plan authority contradicts coordinator")
    if completed_review.plan_authority != trusted_plan_authority.authority_ref:
        raise ReconciliationError("completed review Plan authority contradicts trusted authority")
    if completed_review.plan_digest != state.plan_digest:
        raise PlanDriftError("completed review Plan digest contradicts the live coordinator binding")
    if completed_review.plan_digest != trusted_plan_authority.source_digest:
        raise ReconciliationError("completed review Plan digest contradicts trusted authority")
    if completed_review.plan_source_revision != state.plan_source_revision:
        raise PlanDriftError("completed review Plan source revision contradicts the live binding")
    if completed_review.plan_source_revision != trusted_plan_authority.source_revision:
        raise ReconciliationError("completed review Plan source revision contradicts trusted authority")
    if completed_review.milestone_id != state.milestone_id or completed_review.milestone_id != live_plan_view.milestone_id:
        raise ReconciliationError("completed review Milestone contradicts coordinator")
    if completed_review.review_status not in {"PASS", "PASS_WITH_FINDINGS"}:
        raise ReconciliationError("completed review status is not an accepted review status")
    if completed_review.blocking_findings:
        raise ReconciliationError("completed review contains blocking findings")
    if not completed_review.production_validation_sufficient:
        raise ReconciliationError("completed review lacks sufficient production validation")
    if not completed_review.source_runtime_aligned or not completed_review.source_chain_contiguous:
        raise ReconciliationError("completed review lacks source/runtime alignment proof")

    adoption = review_only_adoption_from_state(
        state, trusted_authority=trusted_plan_authority
    )
    if adoption is None:
        raise ReconciliationError("completed review adoption requires durable frontier adoption")
    current_frontier = revalidate_adopted_frontier(
        state=state,
        live_plan_view=live_plan_view,
        worktree_root=review_adoption_worktree_root,
        git=review_adoption_git,
        trusted_authority=trusted_plan_authority,
    )
    closure_frontier = current_frontier.frontier_ref
    if completed_review.reviewed_frontier_ref == closure_frontier:
        frontier_relation = "EXACT_REVIEWED_FRONTIER"
    else:
        if review_adoption_git is None or not callable(
            getattr(review_adoption_git, "is_ancestor", None)
        ):
            raise ReconciliationError(
                "a post-review closure frontier requires a trusted Git ancestor proof"
            )
        try:
            is_ancestor = review_adoption_git.is_ancestor(
                Path(review_adoption_worktree_root.worktree_root),
                completed_review.reviewed_frontier_ref,
                closure_frontier,
            )
        except Exception as exc:
            raise ReconciliationError(
                f"trusted Git ancestor proof failed: {exc}"
            ) from exc
        if is_ancestor is not True:
            raise ReconciliationError(
                "completed review frontier is not an ancestor of the adopted closure frontier"
            )
        frontier_relation = "GOVERNANCE_ONLY_DESCENDANT"

    adoption_key = f"completed-review-adoption:{completed_review.artifact_digest}"
    existing_payload = state.reconciled_completions.get(adoption_key)
    if existing_payload is not None:
        try:
            stored = CompletedReviewAdoptionReceipt.from_dict(existing_payload)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(
                f"durable completed-review adoption receipt is invalid: {exc}"
            ) from exc
        expected_frontier_proof = _digest_of(
            {
                "artifact_digest": completed_review.artifact_digest,
                "closure_frontier_ref": closure_frontier,
                "plan_authority": state.plan_authority,
                "plan_digest": state.plan_digest,
                "reviewed_frontier_ref": completed_review.reviewed_frontier_ref,
            },
            path="CompletedReviewAdoptionFrontierProof",
        )
        if (
            stored.artifact_digest != completed_review.artifact_digest
            or stored.closure_frontier_ref != closure_frontier
            or stored.reviewed_frontier_ref != completed_review.reviewed_frontier_ref
            or stored.frontier_proof_digest != expected_frontier_proof
        ):
            raise ContradictoryCompletionError(
                "completed review artifact/frontier contradicts the durable adoption receipt"
            )
        return CompletedReviewAdoptionOutcome(
            receipt=stored,
            replayed=True,
            milestone_closure_ready=stored.closure_ready,
            reasons=("identical completed-review adoption replayed from durable evidence",),
        )

    for key, payload in state.reconciled_completions.items():
        if not isinstance(payload, Mapping):
            raise ReconciliationError(f"durable reconciliation entry {key!r} is malformed")
        if payload.get("completion_kind") == COMPLETION_KIND_COMPLETED_REVIEW_ADOPTION:
            raise ReconciliationError(
                "a different completed-review adoption already owns this coordinator"
            )
        raise ReconciliationError(
            "existing reviewer or Work Item reconciliation truth blocks completed-review adoption"
        )

    frontier_proof_digest = _digest_of(
        {
            "artifact_digest": completed_review.artifact_digest,
            "closure_frontier_ref": closure_frontier,
            "plan_authority": state.plan_authority,
            "plan_digest": state.plan_digest,
            "reviewed_frontier_ref": completed_review.reviewed_frontier_ref,
        },
        path="CompletedReviewAdoptionFrontierProof",
    )
    receipt = CompletedReviewAdoptionReceipt(
        coordinator_id=state.coordinator_id,
        project_id=state.project_id,
        plan_id=completed_review.plan_id,
        plan_authority=state.plan_authority,
        plan_digest=state.plan_digest,
        plan_source_revision=state.plan_source_revision or "",
        milestone_id=state.milestone_id,
        review_cycle=completed_review.review_cycle,
        reviewed_frontier_ref=completed_review.reviewed_frontier_ref,
        closure_frontier_ref=closure_frontier,
        frontier_relation=frontier_relation,
        artifact_ref=completed_review.artifact_ref,
        artifact_digest=completed_review.artifact_digest,
        review_status=completed_review.review_status,
        blocking_findings=completed_review.blocking_findings,
        non_blocking_findings=completed_review.non_blocking_findings,
        production_validation_sufficient=completed_review.production_validation_sufficient,
        closure_ready=True,
        frontier_proof_digest=frontier_proof_digest,
        prev_coordinator_revision=state.coordinator_revision,
        next_coordinator_revision=state.coordinator_revision + 1,
        adopted_at=_now_iso(),
    ).with_digest()
    projection = WorkingTruthProjection(
        project_ref=SemanticReference(ref=state.project_id),
        plan_ref=SemanticReference(ref=state.plan_authority),
        milestone_ref=SemanticReference(ref=state.milestone_id),
        reviewed_frontier_ref=SemanticReference(ref=closure_frontier),
        result_refs=(),
        evidence_refs=(
            SemanticReference(ref=completed_review.artifact_digest),
            SemanticReference(ref=receipt.receipt_digest),
        ),
    )
    projection_payload = projection.canonical_dict()
    working_truth_digest = hashlib.sha256(
        canonical_json(
            canonicalize(projection_payload, path="WorkingTruthProjection")
        ).encode("utf-8")
    ).hexdigest()
    updates = {
        "reconciled_completions": {
            adoption_key: receipt.to_dict(),
        },
        "working_truth": {
            "projection": projection_payload,
            "digest": working_truth_digest,
        },
        "progression_revision": state.progression_revision + 1,
    }
    try:
        persisted = store.compare_and_swap(
            state.coordinator_id,
            state.coordinator_revision,
            updates,
            state.revision_token,
        )
    except StaleCoordinatorRevisionError:
        refreshed = store.get(state.coordinator_id)
        if refreshed is None:
            raise CoordinatorNotFoundError(state.coordinator_id)
        winner = refreshed.reconciled_completions.get(adoption_key)
        if winner is not None:
            stored = CompletedReviewAdoptionReceipt.from_dict(winner)
            if stored.receipt_digest != receipt.receipt_digest:
                raise ContradictoryCompletionError(
                    "completed-review adoption CAS race produced contradictory evidence"
                )
            return CompletedReviewAdoptionOutcome(
                receipt=stored,
                replayed=True,
                milestone_closure_ready=stored.closure_ready,
                reasons=("completed-review adoption CAS race replayed the durable winner",),
            )
        raise ReconciliationError(
            "completed-review adoption CAS raced without a durable winner"
        )
    except Exception as exc:
        raise ReconciliationError(f"completed-review adoption CAS failed: {exc}") from exc

    verified = store.get(state.coordinator_id)
    if verified is None:
        raise CoordinatorNotFoundError(state.coordinator_id)
    stored_payload = verified.reconciled_completions.get(adoption_key)
    if stored_payload is None:
        raise ReconciliationError(
            "completed-review adoption receipt missing after persist"
        )
    stored = CompletedReviewAdoptionReceipt.from_dict(stored_payload)
    if stored.receipt_digest != receipt.receipt_digest:
        raise ReconciliationError(
            "completed-review adoption receipt digest mismatch after persist"
        )
    if persisted.coordinator_revision != verified.coordinator_revision:
        raise ReconciliationError(
            "completed-review adoption coordinator readback revision mismatch"
        )
    return CompletedReviewAdoptionOutcome(
        receipt=stored,
        replayed=False,
        milestone_closure_ready=stored.closure_ready,
        reasons=(
            "trusted completed review adopted without reviewer execution",
            "no Work Item progression or result authority was synthesized",
        ),
    )


def _replay_outcome(
    *,
    state: TaskMainCoordinatorState,
    receipt: CompletionReconciliationReceipt,
    live_plan_view: MilestonePlanView,
) -> ReconciliationOutcome:
    """Deterministic replay for duplicate delivery: same receipt, no mutation."""
    ack_token = build_ack_token(
        canonical_task_id=receipt.canonical_task_id, card_digest=receipt.card_digest
    )
    ready = evaluate_ready_work_items(
        graph=_graph_for(state),
        wi_status=dict(state.wi_status),
        gate_blocked=live_plan_view.user_gate_blocked,
    )
    return ReconciliationOutcome(
        receipt=receipt,
        ack_eligible=True,
        ack_token=ack_token,
        replayed=True,
        ready_work_items=tuple(ready),
        progression_complete=(
            (receipt.work_item_id,)
            if receipt.progression_disposition == DISPOSITION_PROGRESSION_COMPLETE and receipt.work_item_id
            else ()
        ),
        blocked_work_items=(
            (receipt.work_item_id,)
            if receipt.progression_disposition == DISPOSITION_PROGRESSION_BLOCKED and receipt.work_item_id
            else ()
        ),
        integrated_review_required=False,
        user_gate_required=receipt.progression_disposition
        in (DISPOSITION_REVIEW_REPLAN_REQUIRED, DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT),
        milestone_closure_ready=False,
        rv2_required=receipt.progression_disposition == DISPOSITION_REVIEW_RV2_REQUIRED,
        physical_dispatch_attempts=0,
        reasons=("duplicate delivery replayed from durable receipt; no second semantic mutation",),
    )


def ack_eligible_for(
    *,
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    canonical_task_id: str,
    card_digest: str,
) -> bool:
    """True only with stored semantic reconciliation evidence for the identity.

    This is the causal ACK gate: a matching ``AOTA_COMPLETION_ACK_V1`` string
    alone (``parse_completion_ack``) is NOT sufficient — the coordinator must
    durably hold the exact reconciliation receipt.
    """
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    canonical_task_id = _require_non_empty_str(canonical_task_id, "canonical_task_id")
    card_digest = _require_non_empty_str(card_digest, "card_digest", max_length=128)
    state = store.get(coordinator_id)
    if state is None:
        return False
    payload = state.reconciled_completions.get(canonical_task_id)
    if payload is None:
        return False
    try:
        receipt = CompletionReconciliationReceipt.from_dict(payload)
    except (TypeError, ValueError):
        return False
    return receipt.card_digest == card_digest


# ---------------------------------------------------------------------------
# Primary operation: reconcile one Worker completion (CARD-first)
# ---------------------------------------------------------------------------


def reconcile_worker_completion(
    *,
    store: TaskMainCoordinatorStore,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    canonical_task_id: str,
    card_digest: str,
    live_plan_view: MilestonePlanView,
    governed_evidence: GovernedWorkItemEvidence,
    handoff_resolver: Callable[[str], TaskHandoff] | None = None,
) -> ReconciliationOutcome:
    """Reconcile one Worker completion CARD-first with governed semantics.

    Primary input identity is ``(canonical_task_id, card_digest)``; every
    other truth hydrates from trusted durable stores. Fails closed with no
    semantic mutation and no ACK on: unknown coordinator, non-ACTIVE or
    gate-blocked lifecycle, Plan drift, out-of-order/unexpected completion,
    contradictory replay, non-terminal or CARD-less M2 truth, any identity /
    digest mismatch, governed-evidence binding mismatch, or persist failure.
    Duplicate delivery of an already-reconciled completion replays the
    stored receipt deterministically with no second mutation.
    """
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(governed_evidence, GovernedWorkItemEvidence):
        raise TypeError(
            "governed_evidence must be GovernedWorkItemEvidence, "
            f"got {type(governed_evidence).__name__}"
        )
    canonical_task_id = _require_non_empty_str(canonical_task_id, "canonical_task_id")
    card_digest = _require_non_empty_str(card_digest, "card_digest", max_length=128)

    state = _require_active_for_reconciliation(store, coordinator_id, live_plan_view)

    # Expected-Work-Item binding: the completion must address a dispatched
    # Work Item that W1 preserved as pending reconciliation. Anything else —
    # out-of-order, cross-Work-Item, cross-Milestone/coordinator/authority —
    # fails closed here and never creates Work Item state.
    work_item_id: str | None = None
    for candidate, entry in state.bindings.items():
        if entry.get("canonical_task_id") == canonical_task_id:
            work_item_id = candidate
            break
    if work_item_id is None:
        raise ReconciliationError(
            f"completion {canonical_task_id!r} addresses no dispatched Work Item "
            f"of coordinator {state.coordinator_id!r}; no semantic apply, no ACK"
        )
    if state.wi_status.get(work_item_id) != WorkItemCoordinatorStatus.COMPLETION_PENDING_RECONCILIATION.value:
        raise ReconciliationError(
            f"Work Item {work_item_id!r} is not pending reconciliation "
            f"({state.wi_status.get(work_item_id)!r}); no semantic apply, no ACK"
        )

    # Idempotency: same identity already reconciled replays; a different CARD
    # digest for the same canonical task fails closed (never overwrites).
    existing_payload = state.reconciled_completions.get(canonical_task_id)
    if existing_payload is not None:
        stored = CompletionReconciliationReceipt.from_dict(existing_payload)
        if stored.card_digest != card_digest:
            raise ContradictoryCompletionError(
                f"canonical task {canonical_task_id!r} already reconciled with digest "
                f"{stored.card_digest!r}; contradictory replay refused"
            )
        return _replay_outcome(state=state, receipt=stored, live_plan_view=live_plan_view)

    # Trusted hydration + exact binding validation (CARD-first).
    _, card = _hydrate_completion(
        execution_store,
        coordinator_id=state.coordinator_id,
        canonical_task_id=canonical_task_id,
        card_digest=card_digest,
    )
    # Worker next-hints are evidence/advice only and never reach the
    # evaluator as authority (they are simply never read here).
    _ = card.next_hint

    # Governed evidence binding: validation verdicts and risk inputs must
    # address exactly this Work Item / Milestone.
    validation = governed_evidence.validation_evidence
    if validation.work_item_ref != work_item_id:
        raise ReconciliationError(
            f"validation evidence addresses {validation.work_item_ref!r}, "
            f"not reconciled Work Item {work_item_id!r}"
        )
    risk_disposition, risk_digest = _derive_risk_disposition(
        governed_evidence, work_item_id=work_item_id, milestone_id=state.milestone_id
    )
    validation_digest = _digest_of(validation.to_dict(), path="FocusedValidationEvidence")

    progress_evidence = WorkItemProgressEvidence(
        work_item_ref=work_item_id,
        worker_result_ref=card.result_handoff_ref,
        worker_result_digest=card_digest,
    )

    progress_list, validation_list, dispositions, cards = _rebuild_work_item_inputs(
        state=state,
        execution_store=execution_store,
        current_work_item=work_item_id,
        current_progress=progress_evidence,
        current_validation=validation,
        current_disposition=risk_disposition,
    )
    cards[card.result_handoff_ref.ref] = card
    handoffs = _resolve_task_handoffs(
        state=state,
        handoff_resolver=handoff_resolver,
        current_work_item=work_item_id,
        current_canonical_task_id=canonical_task_id,
    )

    # Governed semantic reconciliation through the existing deterministic
    # progression evaluator (all fail-closed gates preserved; a CARD PASS
    # alone is never sufficient).
    disposition: ProgressionDisposition = evaluate_milestone_progression(
        _graph_for(state),
        progress_list,
        validation_list,
        dispositions,
        (),
        cards,
        {},
        task_handoffs=handoffs,
    )
    complete_refs = tuple(disposition.progression_complete_work_item_refs)
    blocked_refs = tuple(disposition.blocked_work_item_refs)
    if work_item_id in complete_refs:
        receipt_disposition = DISPOSITION_PROGRESSION_COMPLETE
    else:
        receipt_disposition = DISPOSITION_PROGRESSION_BLOCKED
    integrated_review_required = bool(disposition.milestone_review_ready)

    progression_revision = state.progression_revision + 1
    working_truth, truth_digest = _build_working_truth(
        project_id=state.project_id,
        plan_authority=state.plan_authority,
        milestone_id=state.milestone_id,
        state=state,
        execution_store=execution_store,
        extra_result_refs=((canonical_task_id, card_digest),),
    )
    receipt = CompletionReconciliationReceipt(
        coordinator_id=state.coordinator_id,
        plan_authority=state.plan_authority,
        milestone_id=state.milestone_id,
        work_item_id=work_item_id,
        completion_kind=COMPLETION_KIND_WORK_ITEM,
        canonical_task_id=canonical_task_id,
        card_digest=card_digest,
        result_handoff_ref=card.result_handoff_ref.ref,
        result_digest=card.result_handoff_ref.digest,
        prev_coordinator_revision=state.coordinator_revision,
        next_coordinator_revision=state.coordinator_revision + 1,
        progression_revision=progression_revision,
        progression_disposition=receipt_disposition,
        validation_evidence_digest=validation_digest,
        risk_disposition_digest=risk_digest,
        review_workflow_disposition=None,
        working_truth_digest=truth_digest,
        governed_evidence={
            "progress_evidence": progress_evidence.to_dict(),
            "validation_evidence": validation.to_dict(),
            "risk_envelope": governed_evidence.risk_envelope.to_dict(),
            "risk_delta": (
                governed_evidence.risk_delta.to_dict() if governed_evidence.risk_delta is not None else None
            ),
            "review_triggers": sorted(
                t.value if isinstance(t, ReviewTrigger) else str(t)
                for t in governed_evidence.review_triggers
            ),
            "escalation_triggers": sorted(
                t.value if isinstance(t, UserEscalationTrigger) else str(t)
                for t in governed_evidence.escalation_triggers
            ),
        },
        reconciled_at=_now_iso(),
    ).with_digest()

    _, effective_receipt, replayed = _persist_reconciliation(
        store=store,
        state=state,
        work_item_id=work_item_id,
        receipt=receipt,
        working_truth=working_truth,
        progression_revision=progression_revision,
    )
    if replayed:
        # Lost a CAS race to the same completion: the winner's receipt is
        # durable; replay it deterministically with no second mutation.
        persisted_state = store.get(state.coordinator_id)
        if persisted_state is None:
            raise CoordinatorNotFoundError(state.coordinator_id)
        return _replay_outcome(
            state=persisted_state, receipt=effective_receipt, live_plan_view=live_plan_view
        )
    persisted_state = store.get(state.coordinator_id)
    if persisted_state is None:
        raise CoordinatorNotFoundError(state.coordinator_id)

    # ONLY now is ACK eligible: validation PASS + reconciliation PASS +
    # durable persistence PASS, all causally bound to the stored receipt.
    ack_token = build_ack_token(canonical_task_id=canonical_task_id, card_digest=card_digest)
    refreshed_wi_status = dict(persisted_state.wi_status)
    ready = evaluate_ready_work_items(
        graph=_graph_for(state),
        wi_status=refreshed_wi_status,
        gate_blocked=live_plan_view.user_gate_blocked,
    )
    reasons = tuple(disposition.reasons) + (
        (f"Work Item {work_item_id} progression-complete",)
        if receipt_disposition == DISPOSITION_PROGRESSION_COMPLETE
        else (f"Work Item {work_item_id} blocked: see progression reasons",)
    )
    if integrated_review_required:
        reasons = reasons + (
            "milestone integrated review is ready; call task_main.advance_once again "
            "to dispatch the governed integrated reviewer",
        )
    return ReconciliationOutcome(
        receipt=receipt,
        ack_eligible=True,
        ack_token=ack_token,
        replayed=False,
        ready_work_items=tuple(ready),
        progression_complete=complete_refs,
        blocked_work_items=blocked_refs,
        integrated_review_required=integrated_review_required,
        user_gate_required=False,
        milestone_closure_ready=False,
        rv2_required=False,
        physical_dispatch_attempts=0,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Reviewer completion path (finding-batch semantics, derivation only)
# ---------------------------------------------------------------------------


def _map_workflow_disposition(
    workflow_disposition: WorkflowDisposition,
    *,
    reviewer_card: WorkerResultCard,
) -> tuple[str, bool, bool, tuple[str, ...]]:
    """Map the existing review workflow disposition to task-main dispositions.

    Returns ``(receipt_disposition, user_gate_required, rv2_required, reasons)``.
    Runtime/environment failure (reviewer mechanical failure) is distinct
    from source defect: it yields revalidation, never source repair.
    Semantic stop escalates to the user gate. Derivation only — nothing is
    executed, dispatched, or mutated here.
    """
    if reviewer_card.mechanical_failure is not None:
        return (
            DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT,
            False,
            False,
            ("reviewer mechanical failure: environment revalidation required, not source repair",),
        )
    if reviewer_card.semantic_stop is not None:
        return (
            DISPOSITION_REVIEW_BLOCKED,
            True,
            False,
            ("reviewer semantic stop: user-gated escalation required",),
        )
    if workflow_disposition == WorkflowDisposition.READY_FOR_STEWARD:
        return (
            DISPOSITION_REVIEW_READY_FOR_STEWARD,
            True,
            False,
            ("integrated review technically accepted; Steward/user closure decision still required",),
        )
    if workflow_disposition == WorkflowDisposition.REPAIR_REQUIRED:
        return (
            DISPOSITION_REVIEW_REPAIR_REQUIRED,
            False,
            False,
            ("source repair derived; repair execution count is 0 (W3 domain)",),
        )
    if workflow_disposition == WorkflowDisposition.RV2_REQUIRED:
        return (
            DISPOSITION_REVIEW_RV2_REQUIRED,
            False,
            True,
            ("RV2 required after a trusted bounded review transition; RV2 is not auto-executed",),
        )
    if workflow_disposition == WorkflowDisposition.REPLAN_REQUIRED:
        return (
            DISPOSITION_REVIEW_REPLAN_REQUIRED,
            True,
            False,
            ("plan change required; stops at the user gate, no automatic Plan mutation",),
        )
    return (
        DISPOSITION_REVIEW_BLOCKED,
        False,
        False,
        ("review workflow blocked; revalidation required, no source repair inferred",),
    )


def reconcile_review_completion(
    *,
    store: TaskMainCoordinatorStore,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    reviewer_canonical_task_id: str,
    card_digest: str,
    live_plan_view: MilestonePlanView,
    governed_review: GovernedReviewEvidence,
    review_adoption_worktree_root: WorktreeSandboxBoundary | None = None,
    review_adoption_git: Any | None = None,
    trusted_plan_authority: TrustedPlanAuthority | None = None,
) -> ReconciliationOutcome:
    """Reconcile one milestone-level reviewer completion through review seams.

    Consumes the governed reviewer completion (CanonicalResult + CARD +
    MilestoneReviewEvidence + findings), runs the existing review workflow
    evaluator (and the closure readiness evaluator on technical acceptance),
    and persists the review reconciliation receipt. Derives dispositions
    only: no reviewer/repair/RV2 auto-dispatch, no closure automation, no
    Plan mutation, no next-Milestone activation.
    """
    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(governed_review, GovernedReviewEvidence):
        raise TypeError(
            "governed_review must be GovernedReviewEvidence, "
            f"got {type(governed_review).__name__}"
        )
    reviewer_canonical_task_id = _require_non_empty_str(
        reviewer_canonical_task_id, "reviewer_canonical_task_id"
    )
    card_digest = _require_non_empty_str(card_digest, "card_digest", max_length=128)

    state = _require_active_for_reconciliation(store, coordinator_id, live_plan_view)
    review_only_adoption = review_only_adoption_from_state(
        state, trusted_authority=trusted_plan_authority
    )
    if (
        review_only_adoption is not None
        and reviewer_canonical_task_id != review_only_adoption.reviewer_canonical_task_id
    ):
        raise ReconciliationError(
            "reviewer completion identity contradicts the adopted reviewer task"
        )
    if review_only_adoption is not None:
        try:
            revalidate_adopted_frontier(
                state=state,
                live_plan_view=live_plan_view,
                worktree_root=review_adoption_worktree_root,
                git=review_adoption_git,
                trusted_authority=trusted_plan_authority,
            )
        except PlanDriftError:
            raise
        except ReviewAdoptionError as exc:
            raise ReconciliationError(f"adopted review frontier is stale: {exc}") from exc
        _validate_adopted_review_binding(
            state=state,
            adoption=review_only_adoption,
            governed_review=governed_review,
        )

    existing_payload = state.reconciled_completions.get(reviewer_canonical_task_id)
    if existing_payload is not None:
        stored = CompletionReconciliationReceipt.from_dict(existing_payload)
        if stored.card_digest != card_digest:
            raise ContradictoryCompletionError(
                f"reviewer task {reviewer_canonical_task_id!r} already reconciled with a "
                "different CARD digest; contradictory replay refused"
            )
        return _replay_outcome(state=state, receipt=stored, live_plan_view=live_plan_view)

    _, reviewer_card = _hydrate_completion(
        execution_store,
        coordinator_id=state.coordinator_id,
        canonical_task_id=reviewer_canonical_task_id,
        card_digest=card_digest,
    )
    if reviewer_card.agent_work_role != AgentWorkRole.REVIEWER:
        raise ReconciliationError(
            f"reviewer completion role is {reviewer_card.agent_work_role.value!r}, not reviewer"
        )

    evidence = governed_review.review_evidence
    if evidence.milestone_ref.ref != state.milestone_id:
        raise ReconciliationError("review evidence Milestone contradicts coordinator Milestone")
    if evidence.review_result_ref != reviewer_card.result_handoff_ref:
        raise ReconciliationError("review evidence result ref contradicts reviewer CARD")
    if evidence.review_result_digest != card_digest:
        raise ReconciliationError("review evidence result digest contradicts reviewer CARD digest")

    # Current governed progression (recomputed from durable WI receipts) feeds
    # the closure readiness input when technical acceptance is derived.
    progress_list, validation_list, dispositions, cards = _rebuild_work_item_inputs(
        state=state, execution_store=execution_store
    )
    handoffs = _resolve_task_handoffs(
        state=state,
        handoff_resolver=None,
        current_work_item=None,
    )
    current_progression: ProgressionDisposition | None = None
    if review_only_adoption is not None:
        # This projection is intentionally in-memory.  The review receipt is
        # durable, but no Work Item completion/status/history is reconstructed.
        current_progression = review_only_progression_for(state, review_only_adoption)
    elif progress_list:
        current_progression = evaluate_milestone_progression(
            _graph_for(state),
            progress_list,
            validation_list,
            dispositions,
            (),
            cards,
            {},
            task_handoffs=handoffs,
        )

    expected_review_frontier = governed_review.expected_frontier
    expected_final_frontier = governed_review.expected_final_frontier
    if review_only_adoption is not None:
        adopted_frontier = review_only_adoption.frontier_ref
        if expected_review_frontier is None:
            expected_review_frontier = SemanticReference(ref=adopted_frontier)
        elif _frontier_text(expected_review_frontier, "expected review frontier") != adopted_frontier:
            raise ReconciliationError("governed review expected frontier contradicts adopted frontier")
        if expected_final_frontier is None:
            expected_final_frontier = SemanticReference(ref=adopted_frontier)
        elif _frontier_text(expected_final_frontier, "expected final frontier") != adopted_frontier:
            raise ReconciliationError("governed review expected final frontier contradicts adopted frontier")
        if evidence.reviewed_frontier_ref.ref != adopted_frontier:
            raise ReconciliationError("review evidence frontier contradicts adopted frontier")

    expected_review_frontier = expected_review_frontier or evidence.reviewed_frontier_ref
    review_triggers = list(governed_review.review_triggers)
    for payload in state.reconciled_completions.values():
        try:
            prior_receipt = CompletionReconciliationReceipt.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise ReconciliationError(f"durable receipt fails strict validation: {exc}") from exc
        if prior_receipt.completion_kind not in {
            COMPLETION_KIND_WORK_ITEM,
            COMPLETION_KIND_REVIEW,
        }:
            continue
        prior_governed = prior_receipt.governed_evidence
        raw_triggers = prior_governed.get("review_triggers", ())
        if not isinstance(raw_triggers, (tuple, list)):
            raise ReconciliationError("durable review trigger evidence must be tuple/list")
        review_triggers.extend(raw_triggers)
    review_triggers = list(
        dict.fromkeys(
            trigger.value if isinstance(trigger, ReviewTrigger) else str(trigger)
            for trigger in review_triggers
        )
    )
    rv1_evidence = governed_review.rv1_evidence
    rv1_findings = tuple(governed_review.rv1_findings)
    rv1_result_card = governed_review.rv1_result_card
    rv1_task_handoff = governed_review.rv1_task_handoff
    if evidence.review_cycle is ReviewCycle.RV2:
        durable_rv1 = _hydrate_prior_rv1_review(
            state=state,
            execution_store=execution_store,
            worktree_root=review_adoption_worktree_root,
            provided_handoff=governed_review.rv1_task_handoff,
        )
        durable_rv1_evidence, durable_rv1_findings, durable_rv1_card, durable_rv1_handoff = durable_rv1
        if rv1_evidence is not None and rv1_evidence != durable_rv1_evidence:
            raise ReconciliationError("governed RV1 evidence contradicts durable RV1 receipt")
        if rv1_evidence is not None and rv1_findings != durable_rv1_findings:
            raise ReconciliationError("governed RV1 findings contradict durable RV1 receipt")
        if rv1_result_card is not None and rv1_result_card != durable_rv1_card:
            raise ReconciliationError("governed RV1 CARD contradicts durable RV1 receipt")
        if rv1_task_handoff is not None and rv1_task_handoff != durable_rv1_handoff:
            raise ReconciliationError("governed RV1 handoff contradicts durable RV1 receipt")
        rv1_evidence = durable_rv1_evidence
        rv1_findings = durable_rv1_findings
        rv1_result_card = durable_rv1_card
        rv1_task_handoff = durable_rv1_handoff

    review_kwargs: dict[str, Any] = {
        "milestone_ref": SemanticReference(ref=state.milestone_id),
        "repair_evidences": list(governed_review.repair_evidences),
        "repair_result_cards": governed_review.repair_result_cards,
        "repair_task_handoffs": governed_review.repair_task_handoffs,
        "validation_evidences": list(governed_review.validation_evidences),
        "review_triggers": review_triggers,
        "repair_history": governed_review.repair_history,
        "failure_fingerprints": list(governed_review.failure_fingerprints),
        "pre_failure_fingerprints": list(governed_review.pre_failure_fingerprints),
        "post_failure_fingerprints": list(governed_review.post_failure_fingerprints),
        "semantic_boundary_exceeded": governed_review.semantic_boundary_exceeded,
    }
    if expected_final_frontier is not None:
        review_kwargs["expected_final_frontier"] = expected_final_frontier
    if evidence.review_cycle == ReviewCycle.RV2:
        if rv1_evidence is None or rv1_result_card is None or rv1_task_handoff is None:
            raise ReconciliationError("RV2 reconciliation requires a complete durable RV1 review bundle")
        review_kwargs.update(
            {
                "rv1_evidence": rv1_evidence,
                "rv1_findings": list(rv1_findings),
                "rv1_result_card": rv1_result_card,
                "rv1_task_handoff": rv1_task_handoff,
                "expected_rv1_frontier": rv1_evidence.reviewed_frontier_ref,
                "expected_rv2_frontier": expected_review_frontier,
            }
        )
        review_kwargs.update(
            {
                "rv2_evidence": evidence,
                "rv2_findings": list(governed_review.review_findings),
                "rv2_result_card": reviewer_card,
                "rv2_task_handoff": governed_review.review_task_handoff,
            }
        )
    else:
        review_kwargs["expected_rv1_frontier"] = expected_review_frontier
        review_kwargs.update(
            {
                "rv1_evidence": evidence,
                "rv1_findings": list(governed_review.review_findings),
                "rv1_result_card": reviewer_card,
                "rv1_task_handoff": governed_review.review_task_handoff,
            }
        )
    workflow = evaluate_milestone_review_workflow(**review_kwargs)

    receipt_disposition, user_gate_required, rv2_required, map_reasons = _map_workflow_disposition(
        workflow.disposition, reviewer_card=reviewer_card
    )

    milestone_closure_ready = False
    closure_reasons: tuple[str, ...] = ()
    if (
        workflow.disposition == WorkflowDisposition.READY_FOR_STEWARD
        and current_progression is not None
        and expected_final_frontier is not None
    ):
        closure = evaluate_milestone_closure_readiness(
            graph=_graph_for(state),
            progression_disposition=current_progression,
            workflow_disposition=workflow,
            final_review_evidence=evidence,
            expected_frontier_ref=expected_final_frontier,
            final_review_result_card=reviewer_card,
            final_review_task_handoff=governed_review.review_task_handoff,
            repair_evidences=list(governed_review.repair_evidences),
            repair_history=governed_review.repair_history,
            rv1_evidence=rv1_evidence,
            review_triggers=review_triggers,
            milestone_ref=SemanticReference(ref=state.milestone_id),
        )
        milestone_closure_ready = bool(closure.ready_for_project_steward)
        closure_reasons = tuple(closure.blocking_reasons)
        if milestone_closure_ready:
            # Closure readiness is derived only: Steward reconciliation and
            # user approval remain gates (no closure automation, no GitHub).
            user_gate_required = True

    progression_revision = state.progression_revision + 1
    workflow_ref = SemanticReference(ref=workflow.digest)
    working_truth, truth_digest = _build_working_truth(
        project_id=state.project_id,
        plan_authority=state.plan_authority,
        milestone_id=state.milestone_id,
        state=state,
        execution_store=execution_store,
        extra_result_refs=((reviewer_canonical_task_id, card_digest),),
        reviewed_frontier_ref=evidence.reviewed_frontier_ref,
        workflow_disposition_ref=workflow_ref,
    )
    receipt = CompletionReconciliationReceipt(
        coordinator_id=state.coordinator_id,
        plan_authority=state.plan_authority,
        milestone_id=state.milestone_id,
        work_item_id=None,
        completion_kind=COMPLETION_KIND_REVIEW,
        canonical_task_id=reviewer_canonical_task_id,
        card_digest=card_digest,
        result_handoff_ref=reviewer_card.result_handoff_ref.ref,
        result_digest=reviewer_card.result_handoff_ref.digest,
        prev_coordinator_revision=state.coordinator_revision,
        next_coordinator_revision=state.coordinator_revision + 1,
        progression_revision=progression_revision,
        progression_disposition=receipt_disposition,
        validation_evidence_digest=None,
        risk_disposition_digest=None,
        review_workflow_disposition=workflow.disposition.value,
        working_truth_digest=truth_digest,
        governed_evidence={
            "review_evidence": evidence.to_dict(),
            "review_findings": [finding.to_dict() for finding in governed_review.review_findings],
            "review_handoff_ref": governed_review.review_handoff_ref,
            "review_handoff_digest": governed_review.review_handoff_digest,
            "review_triggers": review_triggers,
            "repair_evidences": [repair.to_dict() for repair in governed_review.repair_evidences],
            "validation_evidences": [
                validation.to_dict() for validation in governed_review.validation_evidences
            ],
            "workflow_disposition": workflow.disposition.value,
            "workflow_digest": workflow.digest,
            "closure_ready": milestone_closure_ready,
        },
        reconciled_at=_now_iso(),
    ).with_digest()

    _, effective_review_receipt, review_replayed = _persist_reconciliation(
        store=store,
        state=state,
        work_item_id=None,
        receipt=receipt,
        working_truth=working_truth,
        progression_revision=progression_revision,
    )
    if review_replayed:
        persisted_review_state = store.get(state.coordinator_id)
        if persisted_review_state is None:
            raise CoordinatorNotFoundError(state.coordinator_id)
        return _replay_outcome(
            state=persisted_review_state,
            receipt=effective_review_receipt,
            live_plan_view=live_plan_view,
        )

    ack_token = build_ack_token(
        canonical_task_id=reviewer_canonical_task_id, card_digest=card_digest
    )
    reasons = map_reasons + tuple(workflow.reasons) + closure_reasons
    return ReconciliationOutcome(
        receipt=receipt,
        ack_eligible=True,
        ack_token=ack_token,
        replayed=False,
        ready_work_items=(),
        progression_complete=tuple(current_progression.progression_complete_work_item_refs)
        if current_progression is not None
        else (),
        blocked_work_items=tuple(current_progression.blocked_work_item_refs)
        if current_progression is not None
        else (),
        integrated_review_required=False,
        user_gate_required=user_gate_required,
        milestone_closure_ready=milestone_closure_ready,
        rv2_required=rv2_required,
        physical_dispatch_attempts=0,
        reasons=reasons,
    )


__all__ = [
    "ACK_BEFORE_SEMANTIC_RECONCILIATION",
    "ACK_REQUIRES_DURABLE_RECONCILIATION",
    "CARD_FIRST",
    "COMPLETION_KIND_REVIEW",
    "COMPLETION_KIND_COMPLETED_REVIEW_ADOPTION",
    "COMPLETION_KIND_WORK_ITEM",
    "COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_ACCEPTANCE_AUTHORITY",
    "COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_PLAN_AUTHORITY",
    "COMPLETED_REVIEW_ADOPTION_RECEIPT_IS_RESULT_AUTHORITY",
    "DISPOSITION_PROGRESSION_BLOCKED",
    "DISPOSITION_PROGRESSION_COMPLETE",
    "DISPOSITION_REVIEW_BLOCKED",
    "DISPOSITION_REVIEW_BLOCKED_ENVIRONMENT",
    "DISPOSITION_REVIEW_READY_FOR_STEWARD",
    "DISPOSITION_REVIEW_REPAIR_REQUIRED",
    "DISPOSITION_REVIEW_REPLAN_REQUIRED",
    "DISPOSITION_REVIEW_RV2_REQUIRED",
    "M2_DELIVERY_STATE_REUSED",
    "MILESTONE_CLOSURE_AUTOMATION_IMPLEMENTED",
    "MODEL_ACK_STRING_ALONE_SUFFICIENT",
    "NEW_DELIVERY_STATE_MACHINE_CREATED",
    "NEW_GENERIC_WORKFLOW_STORE_CREATED",
    "RAW_WORKER_TRANSCRIPT_PRIMARY",
    "RECEIPT_DISPOSITIONS",
    "RECONCILIATION_RECEIPT_IS_PLAN_AUTHORITY",
    "RECONCILIATION_RECEIPT_IS_RESULT_AUTHORITY",
    "REPAIR_AUTOMATION_IMPLEMENTED",
    "SEMANTIC_RECONCILIATION_IDEMPOTENT",
    "SEMANTIC_RECONCILIATION_IMPLEMENTED",
    "TASK_MAIN_CAN_CROSS_NEXT_MILESTONE_GATE",
    "TASK_MAIN_RAW_SHELL_REQUIRED",
    "TASK_MAIN_UNRESTRICTED_FILESYSTEM_REQUIRED",
    "WORKER_CAN_CALL_TASK_MAIN_CONTROL",
    "CompletionReconciliationReceipt",
    "CompletedReviewAdoptionReceipt",
    "CompletedReviewAdoptionOutcome",
    "ContradictoryCompletionError",
    "GovernedReviewEvidence",
    "GovernedWorkItemEvidence",
    "ReconciliationError",
    "ReconciliationOutcome",
    "ack_eligible_for",
    "build_ack_token",
    "reconcile_adopted_completed_review",
    "reconcile_review_completion",
    "reconcile_worker_completion",
]
