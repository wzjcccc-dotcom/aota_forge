"""Common Result envelope + role-specific payload seam (M2/W1 foundation).

M1 accepted RESULT_ARCHITECTURE=common_envelope_plus_role_specific_payload.
This module evolves/reuses the existing WorkerResultCard infrastructure toward
a shared envelope without replacing Result classes unnecessarily.

Common envelope carries (all bounded, digest-bound, durable):
  role, handoff/task identity, outcome/status, bounded summary, digest,
  evidence refs, risk delta ref, needs_input/blocked metadata,
  role-specific payload kind/ref.

Role payload seam supports future W2 Role behaviors via kind/ref only
(full bodies stay as evidence/artifact refs, never raw transcripts):
  analyst_evidence, coder_implementation_evidence, reviewer_review_evidence,
  project_state_evidence, steward_closure_evidence.

Hard invariants:
  SECOND_UNRELATED_RESULT_TRANSPORT_CREATED=no (reuses WorkerResultCard)
  ROLE_RESULT_IS_DURABLE=yes (to_dict/from_dict round-trip, digest verified)
  ROLE_RESULT_IS_DIGEST_BOUND=yes (envelope digest covers card digest + payload)
  RAW_ROLE_TRANSCRIPT_REQUIRED=no
  TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT=no (card-first, bounded hydration)
  CARD_FIRST_RECONCILIATION=yes (envelope requires card truth)
  Legacy WorkerResultCard compatibility preserved (from/to card).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.result_governance import GovernedReference, ResultOutcome
from aota_forge.work_plane.handoff import SemanticReference
from aota_forge.work_plane.result_card import (
    MAX_DIGEST_LENGTH,
    MAX_REF_LENGTH,
    ResultHandoffRef,
    WorkerResultCard,
)
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# ---------------------------------------------------------------------------
# Authority / architecture markers
# ---------------------------------------------------------------------------

RESULT_ARCHITECTURE = "common_envelope_plus_role_specific_payload"
SECOND_UNRELATED_RESULT_TRANSPORT_CREATED = False
ROLE_RESULT_IS_DURABLE = True
ROLE_RESULT_IS_DIGEST_BOUND = True
RAW_ROLE_TRANSCRIPT_REQUIRED = False
TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT = False
CARD_FIRST_RECONCILIATION = True
LEGACY_WORKER_RESULT_COMPATIBILITY = True

# Role payload kinds (W1 seam only; W2 owns full payload behavior).
PAYLOAD_KIND_ANALYST_EVIDENCE = "analyst_evidence"
PAYLOAD_KIND_CODER_IMPLEMENTATION_EVIDENCE = "coder_implementation_evidence"
PAYLOAD_KIND_REVIEWER_REVIEW_EVIDENCE = "reviewer_review_evidence"
PAYLOAD_KIND_PROJECT_STATE_EVIDENCE = "project_state_evidence"
PAYLOAD_KIND_STEWARD_CLOSURE_EVIDENCE = "steward_closure_evidence"

ROLE_PAYLOAD_KINDS: frozenset[str] = frozenset(
    {
        PAYLOAD_KIND_ANALYST_EVIDENCE,
        PAYLOAD_KIND_CODER_IMPLEMENTATION_EVIDENCE,
        PAYLOAD_KIND_REVIEWER_REVIEW_EVIDENCE,
        PAYLOAD_KIND_PROJECT_STATE_EVIDENCE,
        PAYLOAD_KIND_STEWARD_CLOSURE_EVIDENCE,
    }
)

MAX_SUMMARY_LENGTH = 1024
MAX_PAYLOAD_KIND_LENGTH = 128
MAX_BLOCK_REASON_LENGTH = 512


def _require_bounded_str(value: object, label: str, max_len: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a str, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise ValueError(f"{label} must be non-empty")
    if len(s) > max_len:
        raise ValueError(f"{label} length ({len(s)}) exceeds maximum {max_len}")
    return s


def _require_strict_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class RolePayloadRef:
    """Bounded role-specific payload reference (kind + ref + digest).

    Carries only identity + integrity, never full transcript. Full payload
    bodies remain as evidence/artifact refs or via bounded hydration.
    """

    kind: str
    ref: str
    digest: str | None = None

    def __post_init__(self) -> None:
        kind = _require_bounded_str(self.kind, "kind", MAX_PAYLOAD_KIND_LENGTH)
        if kind not in ROLE_PAYLOAD_KINDS:
            raise ValueError(f"unknown role payload kind {kind!r}; expected one of {sorted(ROLE_PAYLOAD_KINDS)}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "ref", _require_bounded_str(self.ref, "ref", MAX_REF_LENGTH))
        if self.digest is not None:
            object.__setattr__(self, "digest", _require_bounded_str(self.digest, "digest", MAX_DIGEST_LENGTH))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "ref": self.ref}
        if self.digest is not None:
            d["digest"] = self.digest
        return canonicalize(d, path="RolePayloadRef")  # type: ignore[return-value]

    @classmethod
    def from_value(cls, val: Any) -> RolePayloadRef | None:
        if val is None:
            return None
        if isinstance(val, cls):
            return val
        if isinstance(val, Mapping):
            allowed = {"kind", "ref", "digest"}
            extra = set(val.keys()) - allowed
            if extra:
                raise ValueError(f"Unknown field(s) in RolePayloadRef: {sorted(extra)}")
            return cls(kind=val["kind"], ref=val["ref"], digest=val.get("digest"))
        raise TypeError(f"Cannot construct RolePayloadRef from {type(val).__name__}")


@dataclass(frozen=True)
class CommonResultEnvelope:
    """Shared envelope wrapping a WorkerResultCard plus role payload seam.

    The envelope does not replace WorkerResultCard; it reuses it as the
    durable card truth and adds the common coordination fields task-main needs
    card-first (identity/digest/binding validation + bounded hydration).
    """

    role: AgentWorkRole
    task_ref: str
    handoff_ref: ResultHandoffRef
    outcome: ResultOutcome
    summary: str
    card_digest: str
    evidence_refs: tuple[GovernedReference, ...] = ()
    artifact_refs: tuple[GovernedReference, ...] = ()
    work_item_ref: SemanticReference | None = None
    milestone_ref: SemanticReference | None = None
    risk_delta_ref: SemanticReference | None = None
    needs_input: bool = False
    blocked: bool = False
    block_reason: str | None = None
    role_payload: RolePayloadRef | None = None
    worker_result_card: WorkerResultCard | None = None

    def __post_init__(self) -> None:
        # Role (reuse AgentWorkRole, no second enum).
        role = self.role
        if isinstance(role, AgentWorkRole):
            pass
        elif isinstance(role, str) and type(role) is str:
            object.__setattr__(self, "role", parse_agent_work_role(role))
        else:
            from enum import Enum

            if isinstance(role, Enum):
                raise TypeError(f"role must be AgentWorkRole, got foreign Enum {type(role).__name__}")
            raise TypeError(f"role must be AgentWorkRole or str, got {type(role).__name__}")
        object.__setattr__(self, "task_ref", _require_bounded_str(self.task_ref, "task_ref", MAX_REF_LENGTH))
        if not isinstance(self.handoff_ref, ResultHandoffRef):
            raise TypeError(f"handoff_ref must be ResultHandoffRef, got {type(self.handoff_ref).__name__}")
        if not isinstance(self.outcome, ResultOutcome):
            raise TypeError(f"outcome must be ResultOutcome, got {type(self.outcome).__name__}")
        object.__setattr__(self, "summary", _require_bounded_str(self.summary, "summary", MAX_SUMMARY_LENGTH))
        object.__setattr__(self, "card_digest", _require_bounded_str(self.card_digest, "card_digest", MAX_DIGEST_LENGTH))
        # Evidence / artifact refs reuse card validation (kind-checked).
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be tuple")
        for r in self.evidence_refs:
            if not isinstance(r, GovernedReference):
                raise TypeError("evidence_refs must contain GovernedReference")
        if not isinstance(self.artifact_refs, tuple):
            raise TypeError("artifact_refs must be tuple")
        for r in self.artifact_refs:
            if not isinstance(r, GovernedReference):
                raise TypeError("artifact_refs must contain GovernedReference")
        for label in ("work_item_ref", "milestone_ref", "risk_delta_ref"):
            val = getattr(self, label)
            if val is not None and not isinstance(val, SemanticReference):
                raise TypeError(f"{label} must be SemanticReference or None")
        object.__setattr__(self, "needs_input", _require_strict_bool(self.needs_input, "needs_input"))
        object.__setattr__(self, "blocked", _require_strict_bool(self.blocked, "blocked"))
        if self.block_reason is not None:
            object.__setattr__(
                self, "block_reason", _require_bounded_str(self.block_reason, "block_reason", MAX_BLOCK_REASON_LENGTH)
            )
        if self.role_payload is not None and not isinstance(self.role_payload, RolePayloadRef):
            raise TypeError(f"role_payload must be RolePayloadRef or None, got {type(self.role_payload).__name__}")
        if self.worker_result_card is not None and not isinstance(self.worker_result_card, WorkerResultCard):
            raise TypeError("worker_result_card must be WorkerResultCard or None")
        # Identity coherence: envelope task identity must match handoff ref and,
        # when card is present, card task identity + digest.
        if self.handoff_ref.ref != self.task_ref:
            raise ValueError("handoff_ref.ref must equal task_ref for lineage traceability")
        if self.worker_result_card is not None:
            if self.worker_result_card.task_ref != self.task_ref:
                raise ValueError("worker card task_ref must equal envelope task_ref")
            if self.worker_result_card.compute_card_digest() != self.card_digest:
                raise ValueError("worker card digest must equal envelope card_digest")
            if self.worker_result_card.agent_work_role != self.role:
                raise ValueError("worker card role must equal envelope role")
            if self.worker_result_card.outcome != self.outcome:
                raise ValueError("worker card outcome must equal envelope outcome")

    def canonical_dict(self) -> dict[str, Any]:
        def _sorted_refs(refs: tuple[GovernedReference, ...]) -> list[dict[str, Any]]:
            return [r.to_dict() for r in sorted(refs, key=lambda x: (x.ref, x.digest or ""))]

        out: dict[str, Any] = {
            "artifact_refs": _sorted_refs(self.artifact_refs),
            "blocked": self.blocked,
            "card_digest": self.card_digest,
            "evidence_refs": _sorted_refs(self.evidence_refs),
            "handoff_ref": self.handoff_ref.to_dict(),
            "needs_input": self.needs_input,
            "outcome": self.outcome.value,
            "role": self.role.value,
            "summary": self.summary,
            "task_ref": self.task_ref,
        }
        if self.work_item_ref is not None:
            out["work_item_ref"] = self.work_item_ref.to_dict()
        if self.milestone_ref is not None:
            out["milestone_ref"] = self.milestone_ref.to_dict()
        if self.risk_delta_ref is not None:
            out["risk_delta_ref"] = self.risk_delta_ref.to_dict()
        if self.block_reason is not None:
            out["block_reason"] = self.block_reason
        if self.role_payload is not None:
            out["role_payload"] = self.role_payload.to_dict()
        return canonicalize(out, path="CommonResultEnvelope")  # type: ignore[return-value]

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def compute_envelope_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def envelope_digest(self) -> str:
        return self.compute_envelope_digest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role.value,
            "task_ref": self.task_ref,
            "handoff_ref": self.handoff_ref.to_dict(),
            "outcome": self.outcome.value,
            "summary": self.summary,
            "card_digest": self.card_digest,
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "artifact_refs": [r.to_dict() for r in self.artifact_refs],
            "needs_input": self.needs_input,
            "blocked": self.blocked,
        }
        if self.work_item_ref is not None:
            d["work_item_ref"] = self.work_item_ref.to_dict()
        if self.milestone_ref is not None:
            d["milestone_ref"] = self.milestone_ref.to_dict()
        if self.risk_delta_ref is not None:
            d["risk_delta_ref"] = self.risk_delta_ref.to_dict()
        if self.block_reason is not None:
            d["block_reason"] = self.block_reason
        if self.role_payload is not None:
            d["role_payload"] = self.role_payload.to_dict()
        if self.worker_result_card is not None:
            d["worker_result_card"] = self.worker_result_card.to_dict()
        d["envelope_digest"] = self.compute_envelope_digest()
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommonResultEnvelope:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        required = ("role", "task_ref", "handoff_ref", "outcome", "summary", "card_digest")
        for req in required:
            if req not in data:
                raise ValueError(f"Missing required field in CommonResultEnvelope: {req!r}")
        allowed = set(required) | {
            "evidence_refs",
            "artifact_refs",
            "work_item_ref",
            "milestone_ref",
            "risk_delta_ref",
            "needs_input",
            "blocked",
            "block_reason",
            "role_payload",
            "worker_result_card",
            "envelope_digest",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unknown field(s) in CommonResultEnvelope: {sorted(extra)}")
        role = parse_agent_work_role(data["role"])
        handoff_ref = ResultHandoffRef.from_value(data["handoff_ref"])
        outcome_raw = data["outcome"]
        if outcome_raw not in ("success", "failure", "unknown"):
            raise ValueError(f"outcome must be success/failure/unknown, got {outcome_raw!r}")
        outcome = ResultOutcome(outcome_raw)
        # Evidence / artifact refs reuse card validators (kind-checked).
        from aota_forge.work_plane.result_card import _validate_artifact_refs, _validate_evidence_refs

        ev_refs = _validate_evidence_refs(data.get("evidence_refs"))
        art_refs = _validate_artifact_refs(data.get("artifact_refs"))
        work_item_ref = SemanticReference.from_value(data["work_item_ref"]) if data.get("work_item_ref") is not None else None
        milestone_ref = SemanticReference.from_value(data["milestone_ref"]) if data.get("milestone_ref") is not None else None
        risk_delta_ref = SemanticReference.from_value(data["risk_delta_ref"]) if data.get("risk_delta_ref") is not None else None
        role_payload = RolePayloadRef.from_value(data.get("role_payload"))
        card = None
        if data.get("worker_result_card") is not None:
            card = WorkerResultCard.from_dict(data["worker_result_card"])  # type: ignore[arg-type]
        env = cls(
            role=role,
            task_ref=data["task_ref"],
            handoff_ref=handoff_ref,
            outcome=outcome,
            summary=data["summary"],
            card_digest=data["card_digest"],
            evidence_refs=ev_refs,
            artifact_refs=art_refs,
            work_item_ref=work_item_ref,
            milestone_ref=milestone_ref,
            risk_delta_ref=risk_delta_ref,
            needs_input=bool(data.get("needs_input", False)),
            blocked=bool(data.get("blocked", False)),
            block_reason=data.get("block_reason"),
            role_payload=role_payload,
            worker_result_card=card,
        )
        # Verify stored digest when present (digest-bound).
        stored = data.get("envelope_digest")
        if stored is not None and stored != env.compute_envelope_digest():
            raise ValueError("CommonResultEnvelope digest mismatch: stored evidence fails closed")
        return env

    @classmethod
    def from_worker_result_card(
        cls,
        card: WorkerResultCard,
        *,
        work_item_ref: SemanticReference | None = None,
        milestone_ref: SemanticReference | None = None,
        risk_delta_ref: SemanticReference | None = None,
        needs_input: bool = False,
        blocked: bool = False,
        block_reason: str | None = None,
        role_payload: RolePayloadRef | None = None,
    ) -> CommonResultEnvelope:
        """Project a shared envelope from an existing WorkerResultCard.

        Preserves card identity/digest/binding; adds coordination metadata and
        the role payload seam without duplicating transport.
        """
        if not isinstance(card, WorkerResultCard):
            raise TypeError(f"card must be WorkerResultCard, got {type(card).__name__}")
        return cls(
            role=card.agent_work_role,
            task_ref=card.task_ref,
            handoff_ref=card.result_handoff_ref,
            outcome=card.outcome,
            summary=card.summary,
            card_digest=card.compute_card_digest(),
            evidence_refs=card.primary_evidence_refs,
            artifact_refs=card.output_artifact_refs,
            work_item_ref=work_item_ref,
            milestone_ref=milestone_ref,
            risk_delta_ref=risk_delta_ref,
            needs_input=needs_input,
            blocked=blocked,
            block_reason=block_reason,
            role_payload=role_payload,
            worker_result_card=card,
        )


__all__ = [
    "RESULT_ARCHITECTURE",
    "SECOND_UNRELATED_RESULT_TRANSPORT_CREATED",
    "ROLE_RESULT_IS_DURABLE",
    "ROLE_RESULT_IS_DIGEST_BOUND",
    "RAW_ROLE_TRANSCRIPT_REQUIRED",
    "TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT",
    "CARD_FIRST_RECONCILIATION",
    "LEGACY_WORKER_RESULT_COMPATIBILITY",
    "PAYLOAD_KIND_ANALYST_EVIDENCE",
    "PAYLOAD_KIND_CODER_IMPLEMENTATION_EVIDENCE",
    "PAYLOAD_KIND_REVIEWER_REVIEW_EVIDENCE",
    "PAYLOAD_KIND_PROJECT_STATE_EVIDENCE",
    "PAYLOAD_KIND_STEWARD_CLOSURE_EVIDENCE",
    "ROLE_PAYLOAD_KINDS",
    "RolePayloadRef",
    "CommonResultEnvelope",
]
