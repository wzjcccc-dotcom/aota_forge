"""Bounded local Plan lifecycle and Governance 1.x authority cutover.

This module is the production composition seam for the M3/W1 slice.  It does
not introduce a second transaction protocol or a second Plan model:

* ``ProjectGovernanceStore`` owns the durable Plan record and binding CAS;
* ``LocalPlanAuthorityAdapter`` remains the only local physical materializer;
* ``DurableJournalStore`` and ``RecoveryExecutor`` own attempt ordering and
  restart reconciliation.

The cutover is deliberately one-way and explicit.  A GitHub-bound Plan is
read, its exact raw bytes are materialized into the trusted local destination,
the local bytes are read back, and only then is the Project Governance binding
CAS'd to ``local_governance``.  The old source is never mutated by this path.

``plan_retirement`` on ``LocalPlanAuthorityAdapter`` remains a truthful
known-no-effect response.  ``retire_local_plan`` below is a different,
store-owned Plan lifecycle mutation; it does not pretend to be an authority
document mutation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from aota_forge.adapters.plan_authority import PlanAuthorityReadAdapter
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityAdapter,
    LocalPlanAuthorityDestination,
)
from aota_forge.adapters.plan_authority.port import (
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
)
from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR
from aota_forge.core.journal.executor import RecoveryExecutor
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import (
    DurableJournalEntry,
    DurableJournalStore,
    JournalError,
    JournalPersistenceFailureError,
    StaleJournalRevisionError,
)
from aota_forge.core.identity.refs import ObjectRef
from aota_forge.core.identity.kinds import SubjectKind
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import (
    MAX_PLAN_BYTES,
    PORTABLE_PLAN_SOURCE_LOCAL,
    PortablePlanDocument,
    portable_plan_digest,
)
from aota_forge.core.plan.sections import KIND_CURRENT, parse_body_sections
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    PlanRecordAlreadyExistsError,
    ProjectGovernancePersistenceError,
    ProjectGovernanceStore,
    ProjectPlanRecord,
    StalePlanRevisionError,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# Local Plan lifecycle implementation and negative-ownership markers.
LOCAL_PLAN_LIFECYCLE_IMPLEMENTED = True
LOCAL_PLAN_AUTHORITY_CUTOVER_IMPLEMENTED = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_PORT = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_JOURNAL = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_PROJECT_STORE_CAS = True
LOCAL_PLAN_AMENDMENT_IMPLEMENTED = True
LOCAL_PLAN_AMENDMENT_USES_EXISTING_PORT = True
LOCAL_PLAN_AMENDMENT_USES_EXISTING_JOURNAL = True
LOCAL_PLAN_AMENDMENT_USES_PROJECT_STORE_CAS = True
LOCAL_PLAN_CLOSE_MATERIALIZATION_IMPLEMENTED = True
LOCAL_PLAN_CLOSE_MATERIALIZATION_USES_EXISTING_AMENDMENT = True
LOCAL_PLAN_CLOSE_MATERIALIZATION_BEFORE_RETIREMENT = True
LOCAL_PLAN_CLOSE_APPLIED_IMPLIES_TERMINAL_BODY_CONVERGED = True
LOCAL_PLAN_RETIRED_FINAL_BODY_RECOVERY_IMPLEMENTED = True
PLAN_CANDIDATE_SEMANTIC_ADMISSION_IMPLEMENTED = True
PLAN_CANDIDATE_PREFLIGHT_READ_ONLY = True
PLAN_CANDIDATE_PREFLIGHT_REUSES_MUTATION_VALIDATOR = True
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND = "active_local_plan_orphan_body_recovery"
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_REASON = (
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_WITHOUT_DURABLE_AMENDMENT_LINEAGE"
)
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_IMPLEMENTED = True
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_JOURNAL = True
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_EXECUTOR = True
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_ADAPTER = True
ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_PROJECT_STORE_CAS = True
NORMAL_RETIRED_PLAN_MUTATION_DENIED = True
SECOND_PLAN_MUTATION_PROTOCOL_CREATED = False
SECOND_TRANSACTION_FRAMEWORK_CREATED = False
SECOND_GOVERNANCE_STORE_CREATED = False
NEW_AGENT_OPERATION_CREATED = False
NEW_GOVERNANCE_PLAN_MODE_CREATED = False
MODEL_FACING_RECOVERY_CAPABILITY_CREATED = False
NEW_JOURNAL_STATE_CREATED = False
SILENT_DUAL_AUTHORITY_ALLOWED = False
POST_CUTOVER_FALLBACK_TO_OLD_AUTHORITY_ALLOWED = False
CUTOVER_UNKNOWN_OUTCOME_FAILS_CLOSED = True
LOCAL_PLAN_RETIREMENT_USES_PROJECT_GOVERNANCE_CAS = True
LOCAL_PLAN_RETIREMENT_IS_AUTHORITY_DOCUMENT_MUTATION = False

_SUCCESS_STATES = frozenset({JournalState.VERIFIED, JournalState.VERIFIED_RECOVERED})
_NONTERMINAL_STATES = frozenset(
    {
        JournalState.PREPARED,
        JournalState.APPLYING,
        JournalState.OUTCOME_UNKNOWN,
        JournalState.RECONCILING,
    }
)
_FAILURE_STATES = frozenset(
    {
        JournalState.FAILED_NO_EFFECT,
        JournalState.RETRYABLE_NO_EFFECT,
        JournalState.CONFLICT,
    }
)


class LocalPlanLifecyclePhase(str, Enum):
    """Durable lifecycle view over the existing journal/store states."""

    BOUND = "bound"
    PREPARED = "prepared"
    COMMITTED = "committed"
    VERIFIED = "verified"
    FAILED_CLOSED = "failed_closed"
    UNKNOWN = "unknown"


class LocalPlanLifecycleError(ValueError):
    """Fail-closed request or authority composition error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LocalPlanLifecycleRequest:
    """Trusted, bounded linkage for one local Plan lifecycle attempt."""

    project_id: str
    plan_id: str
    destination: LocalPlanAuthorityDestination
    principal: str
    authorization_reference: str
    lease_reference: str
    idempotency_key: str
    correlation_id: str
    intent_fingerprint: str
    expected_plan_revision: int | None = None
    contract_hash: str = field(default_factory=lambda: PLAN_INIT_DESCRIPTOR.contract_hash())
    source_target: ObjectRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise LocalPlanLifecycleError("PROJECT_ID_INVALID", "project_id must be non-empty")
        if not is_plan_id(self.plan_id):
            raise LocalPlanLifecycleError(
                "PLAN_ID_INVALID", "plan_id must be one canonical internal Plan ID"
            )
        if not isinstance(self.destination, LocalPlanAuthorityDestination):
            raise LocalPlanLifecycleError(
                "DESTINATION_INVALID", "destination must be a trusted local Plan destination"
            )
        if self.destination.project_id != self.project_id or self.destination.plan_id != self.plan_id:
            raise LocalPlanLifecycleError(
                "DESTINATION_MISMATCH",
                "trusted destination must match the project_id and plan_id",
            )
        for value, label in (
            (self.principal, "principal"),
            (self.authorization_reference, "authorization_reference"),
            (self.lease_reference, "lease_reference"),
            (self.idempotency_key, "idempotency_key"),
            (self.correlation_id, "correlation_id"),
        ):
            if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
                raise LocalPlanLifecycleError(
                    "TRUSTED_LINKAGE_INVALID", f"{label} must be a bounded safe identifier"
                )
        if not isinstance(self.intent_fingerprint, str) or not _SHA256_RE.fullmatch(
            self.intent_fingerprint
        ):
            raise LocalPlanLifecycleError(
                "INTENT_FINGERPRINT_INVALID", "intent_fingerprint must be a SHA-256 digest"
            )
        if not isinstance(self.contract_hash, str) or not _SHA256_RE.fullmatch(self.contract_hash):
            raise LocalPlanLifecycleError(
                "CONTRACT_HASH_INVALID", "contract_hash must be a SHA-256 digest"
            )
        if self.expected_plan_revision is not None and (
            isinstance(self.expected_plan_revision, bool)
            or not isinstance(self.expected_plan_revision, int)
            or self.expected_plan_revision < 1
        ):
            raise LocalPlanLifecycleError(
                "PLAN_REVISION_INVALID", "expected_plan_revision must be a positive integer"
            )
        if self.source_target is not None:
            if not isinstance(self.source_target, ObjectRef):
                raise LocalPlanLifecycleError(
                    "SOURCE_TARGET_INVALID", "source_target must be an ObjectRef"
                )
            if self.source_target.internal_id.sub_kind != SubjectKind.PLAN:
                raise LocalPlanLifecycleError(
                    "SOURCE_TARGET_INVALID", "source_target must be a Plan subject ref"
                )
            if self.source_target.internal_id.value != self.plan_id:
                raise LocalPlanLifecycleError(
                    "SOURCE_TARGET_MISMATCH",
                    "source_target must identify the request plan_id",
                )


@dataclass(frozen=True)
class PlanCloseMaterializationEvidence:
    """Trusted accepted-close facts used by the body cutover seam.

    The carrier proves which already-accepted closure is being projected.  It
    does not create a checkpoint, receipt, user gate, or semantic decision.
    """

    project_id: str
    plan_id: str
    milestone_id: str
    accepted_frontier: str
    final_known_good: str
    milestone_checkpoint_id: str | None
    milestone_checkpoint_digest: str | None
    milestone_receipt_id: str | None
    milestone_receipt_digest: str | None
    milestone_steward_digest: str | None
    plan_close_checkpoint_id: str
    plan_close_checkpoint_digest: str | None
    plan_close_receipt_id: str
    plan_close_receipt_digest: str
    plan_close_steward_digest: str | None
    closure_phase: str = "ACCEPTED_CLOSURE"
    classification: str = "FINAL_PLAN_BODY_MATERIALIZATION"
    frontier_relation: str = "SAME_AS_ACCEPTED"

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", "project_id is required")
        if not is_plan_id(self.plan_id):
            raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", "plan_id is not canonical")
        if not isinstance(self.milestone_id, str) or not re.fullmatch(r"M[0-9]+", self.milestone_id):
            raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", "milestone_id is not canonical")
        if self.closure_phase != "ACCEPTED_CLOSURE":
            raise LocalPlanLifecycleError(
                "CLOSURE_EVIDENCE_NOT_ACCEPTED",
                "final Plan body materialization requires an accepted closure",
            )
        if self.frontier_relation not in {"SAME_AS_ACCEPTED", "GOVERNANCE_ONLY_DESCENDANT"}:
            raise LocalPlanLifecycleError(
                "CLOSURE_EVIDENCE_INVALID", "frontier_relation is not an accepted dual-frontier relation"
            )
        for value, label in (
            (self.accepted_frontier, "accepted_frontier"),
            (self.final_known_good, "final_known_good"),
            (self.plan_close_checkpoint_id, "plan_close_checkpoint_id"),
            (self.plan_close_receipt_id, "plan_close_receipt_id"),
            (self.plan_close_receipt_digest, "plan_close_receipt_digest"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", f"{label} is required")
        for value, label in (
            (self.accepted_frontier, "accepted_frontier"),
            (self.final_known_good, "final_known_good"),
            (self.plan_close_receipt_digest, "plan_close_receipt_digest"),
        ):
            if label in {"accepted_frontier", "final_known_good"}:
                valid = _COMMIT_RE.fullmatch(value) is not None or _SHA256_RE.fullmatch(value) is not None
            else:
                valid = _SHA256_RE.fullmatch(value) is not None
            if not valid:
                raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", f"{label} must be a SHA-256/commit digest")
        if self.final_known_good != self.accepted_frontier and self.frontier_relation != "GOVERNANCE_ONLY_DESCENDANT":
            raise LocalPlanLifecycleError(
                "CLOSURE_EVIDENCE_FRONTIER_MISMATCH",
                "a distinct final known-good frontier requires an explicit governance-only descendant relation",
            )
        for value, label in (
            (self.milestone_checkpoint_digest, "milestone_checkpoint_digest"),
            (self.milestone_receipt_digest, "milestone_receipt_digest"),
            (self.milestone_steward_digest, "milestone_steward_digest"),
            (self.plan_close_checkpoint_digest, "plan_close_checkpoint_digest"),
            (self.plan_close_steward_digest, "plan_close_steward_digest"),
        ):
            if value is not None and not _SHA256_RE.fullmatch(value):
                raise LocalPlanLifecycleError("CLOSURE_EVIDENCE_INVALID", f"{label} must be a SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "milestone_id": self.milestone_id,
            "accepted_frontier": self.accepted_frontier,
            "final_known_good": self.final_known_good,
            "milestone_checkpoint_id": self.milestone_checkpoint_id,
            "milestone_checkpoint_digest": self.milestone_checkpoint_digest,
            "milestone_receipt_id": self.milestone_receipt_id,
            "milestone_receipt_digest": self.milestone_receipt_digest,
            "milestone_steward_digest": self.milestone_steward_digest,
            "plan_close_checkpoint_id": self.plan_close_checkpoint_id,
            "plan_close_checkpoint_digest": self.plan_close_checkpoint_digest,
            "plan_close_receipt_id": self.plan_close_receipt_id,
            "plan_close_receipt_digest": self.plan_close_receipt_digest,
            "plan_close_steward_digest": self.plan_close_steward_digest,
            "closure_phase": self.closure_phase,
            "classification": self.classification,
            "frontier_relation": self.frontier_relation,
        }


@dataclass(frozen=True)
class LocalPlanLifecycleResult:
    """Bounded result separating lifecycle phase from physical journal state."""

    phase: LocalPlanLifecyclePhase
    project_id: str
    plan_id: str
    journal_id: str | None = None
    plan_record: ProjectPlanRecord | None = None
    journal_entry: DurableJournalEntry | None = None
    lifecycle_state: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.phase is LocalPlanLifecyclePhase.VERIFIED


class PlanCandidateKind(str, Enum):
    INIT = "init"
    AMEND = "amend"


@dataclass(frozen=True)
class LocalPlanCandidatePreflight:
    """Bounded, read-only semantic readiness result for one candidate body."""

    candidate_kind: PlanCandidateKind
    normalization_valid: bool
    semantic_readiness: str
    finding_codes: tuple[str, ...]
    normalized_current_state_usable: bool
    mutation_authorization: str = "NOT_EVALUATED"
    durable_materialization: str = "NOT_ATTEMPTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_kind": self.candidate_kind.value,
            "normalization_valid": self.normalization_valid,
            "semantic_readiness": self.semantic_readiness,
            "finding_codes": list(self.finding_codes[:8]),
            "normalized_current_state_usable": self.normalized_current_state_usable,
            "mutation_authorization": self.mutation_authorization,
            "durable_materialization": self.durable_materialization,
        }


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verify_plan_amendment_decision(
    event: Mapping[str, Any], request: "LocalPlanLifecycleRequest"
) -> tuple[bool, str]:
    receipt = event.get("plan_decision_receipt")
    amendment = event.get("amendment")
    if not isinstance(receipt, Mapping) or not isinstance(amendment, Mapping):
        return False, "structured amendment requires a Control Plane materialized Plan decision receipt"
    receipt_amendment = receipt.get("amendment")
    if not isinstance(receipt_amendment, Mapping):
        return False, "Plan decision receipt does not carry its semantic amendment"
    decision_source = receipt.get("decision_source")
    if not isinstance(decision_source, Mapping) or not all(
        isinstance(decision_source.get(key), str) and decision_source.get(key)
        for key in ("owner_type", "owner_id", "channel", "provenance")
    ):
        return False, "Plan decision receipt has no trusted decision source/owner"
    if receipt.get("request_provenance") != decision_source:
        return False, "Plan decision request provenance does not match its trusted owner"
    decision_kind = receipt.get("decision_kind")
    project_id = receipt.get("project_id")
    plan_id = receipt.get("plan_id")
    base_revision = receipt.get("base_plan_revision")
    amendment_digest = receipt.get("amendment_digest")
    created_at = receipt.get("created_at")
    decision_ref = receipt.get("decision_ref")
    if (
        decision_kind != "plan_amendment"
        or project_id != request.project_id
        or plan_id != request.plan_id
        or isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision != request.expected_plan_revision
        or not isinstance(amendment_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", amendment_digest)
        or event.get("amendment_digest") != amendment_digest
        or dict(receipt_amendment) != dict(amendment)
        or not isinstance(created_at, str)
        or len(created_at) > 64
        or not isinstance(decision_ref, str)
        or event.get("decision_ref") != decision_ref
    ):
        return False, "Plan decision receipt does not bind the exact amendment and current Plan revision"
    try:
        if _canonical_digest(dict(amendment)) != amendment_digest:
            return False, "Plan decision receipt amendment digest does not match its semantic amendment"
    except (TypeError, ValueError):
        return False, "Plan decision receipt amendment is not canonical JSON data"
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError:
        return False, "Plan decision receipt created_at is not canonical ISO timestamp text"
    if timestamp.tzinfo is None:
        return False, "Plan decision receipt created_at must include a timezone"
    identity = {
        "decision_kind": decision_kind,
        "project_id": project_id,
        "plan_id": plan_id,
        "base_plan_revision": base_revision,
        "amendment_digest": amendment_digest,
        "decision_source": dict(decision_source),
    }
    if decision_ref != f"plan-amendment:{_canonical_digest(identity)}":
        return False, "Plan decision ref was not minted from the exact materialized semantic decision"
    return True, ""


def _bounded_body(body: object) -> str:
    if not isinstance(body, str) or not body.strip():
        raise LocalPlanLifecycleError("PLAN_BODY_INVALID", "Plan body must be non-empty text")
    if len(body.encode("utf-8")) > MAX_PLAN_BYTES:
        raise LocalPlanLifecycleError("PLAN_BODY_TOO_LARGE", "Plan body exceeds the bounded Plan size")
    return body


def _normalized_digest(body: str) -> str:
    try:
        document = normalize_portable_plan(body, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    except Exception as exc:
        raise LocalPlanLifecycleError(
            "PLAN_BODY_INVALID", f"canonical Plan normalization failed: {exc}"
        ) from exc
    return portable_plan_digest(document)


def _candidate_identity_findings(
    body: str,
    *,
    project_id: str,
    plan_id: str,
) -> list[str]:
    """Check explicit candidate identity echoes using the canonical section parser."""
    values: dict[str, list[str]] = {
        "project_id": [],
        "plan_id": [],
    }
    identity_keys = {
        "PROJECT_ID": "project_id",
        "CANONICAL_PROJECT_ID": "project_id",
        "PLAN_ID": "plan_id",
    }
    for section in parse_body_sections(body):
        is_identity_section = section.title.strip().casefold() == "plan identity"
        if section.kind != KIND_CURRENT and not is_identity_section:
            continue
        for key, logical_key in identity_keys.items():
            if key in section.key_values:
                values[logical_key].append(section.key_values[key].strip())
            for duplicate_key, duplicate_values in section.duplicates:
                if duplicate_key == key:
                    values[logical_key].extend(value.strip() for value in duplicate_values)

    findings: list[str] = []
    expected = {"project_id": project_id, "plan_id": plan_id}
    for logical_key in ("project_id", "plan_id"):
        observed = values[logical_key]
        if not observed:
            continue
        if any(not value for value in observed):
            findings.append("CANDIDATE_IDENTITY_EMPTY")
        if len(set(observed)) > 1:
            findings.append("CANDIDATE_IDENTITY_CONTRADICTION")
        if any(value != expected[logical_key] for value in observed):
            findings.append(
                "CANDIDATE_PROJECT_ID_MISMATCH"
                if logical_key == "project_id"
                else "CANDIDATE_PLAN_ID_MISMATCH"
            )
    return list(dict.fromkeys(findings))


def _candidate_preflight(
    *,
    candidate_kind: PlanCandidateKind | str,
    project_id: str,
    plan_id: str,
    candidate_body: str,
) -> tuple[LocalPlanCandidatePreflight, PortablePlanDocument | None]:
    """Normalize and semantically inspect a candidate without durable effects.

    This is intentionally not an authorization or materialization operation.
    Mutation methods consume the same validator and add their ordinary trusted
    authorization, binding, journal and CAS checks afterwards.
    """
    try:
        kind = PlanCandidateKind(candidate_kind)
    except (TypeError, ValueError) as exc:
        raise LocalPlanLifecycleError(
            "CANDIDATE_KIND_INVALID", "candidate_kind must be init or amend"
        ) from exc

    try:
        body = _bounded_body(candidate_body)
    except LocalPlanLifecycleError as exc:
        return (
            LocalPlanCandidatePreflight(
                candidate_kind=kind,
                normalization_valid=False,
                semantic_readiness="BLOCKED",
                finding_codes=(exc.code,),
                normalized_current_state_usable=False,
            ),
            None,
        )

    try:
        document = normalize_portable_plan(
            body,
            source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
        )
    except Exception:
        return (
            LocalPlanCandidatePreflight(
                candidate_kind=kind,
                normalization_valid=False,
                semantic_readiness="BLOCKED",
                finding_codes=("PLAN_BODY_INVALID",),
                normalized_current_state_usable=False,
            ),
            None,
        )

    findings: list[str] = []
    state_findings: list[str] = []
    status = document.plan_status.strip() if isinstance(document.plan_status, str) else ""
    if not status:
        state_findings.append("PLAN_STATUS_MISSING")
    elif kind is PlanCandidateKind.INIT and status.casefold().replace("_", "-") in {
        "completed",
        "complete",
        "closed",
        "retired",
    }:
        state_findings.append("PLAN_STATUS_NOT_INITIALIZABLE")

    milestone_id = document.current_milestone
    if milestone_id is None:
        state_findings.append("CURRENT_MILESTONE_MISSING")
    else:
        milestone = document.milestone_specs.get(milestone_id)
        milestone_status = document.milestone_status.get(milestone_id)
        if isinstance(milestone, Mapping):
            if not str(milestone.get("title") or "").strip():
                state_findings.append("CURRENT_MILESTONE_NOT_DEFINED")
            if milestone_status is None:
                milestone_status = milestone.get("status")
        elif milestone_status is None:
            state_findings.append("CURRENT_MILESTONE_NOT_DEFINED")
        if not isinstance(milestone_status, str) or not milestone_status.strip():
            state_findings.append("CURRENT_MILESTONE_STATUS_MISSING")

    findings.extend(state_findings)
    findings.extend(
        _candidate_identity_findings(
            body,
            project_id=project_id,
            plan_id=plan_id,
        )
    )
    findings = list(dict.fromkeys(findings))[:8]
    return (
        LocalPlanCandidatePreflight(
            candidate_kind=kind,
            normalization_valid=True,
            semantic_readiness="READY" if not findings else "BLOCKED",
            finding_codes=tuple(findings),
            normalized_current_state_usable=not state_findings,
        ),
        document,
    )


def preflight_local_plan_candidate(
    *,
    candidate_kind: PlanCandidateKind | str,
    project_id: str,
    plan_id: str,
    candidate_body: str,
) -> LocalPlanCandidatePreflight:
    """Return the bounded read-only portion of the shared admission result."""
    result, _ = _candidate_preflight(
        candidate_kind=candidate_kind,
        project_id=project_id,
        plan_id=plan_id,
        candidate_body=candidate_body,
    )
    return result


def _require_candidate_admission(
    *,
    candidate_kind: PlanCandidateKind,
    project_id: str,
    plan_id: str,
    candidate_body: str,
) -> tuple[LocalPlanCandidatePreflight, PortablePlanDocument]:
    result, document = _candidate_preflight(
        candidate_kind=candidate_kind,
        project_id=project_id,
        plan_id=plan_id,
        candidate_body=candidate_body,
    )
    if result.semantic_readiness != "READY" or document is None:
        code = result.finding_codes[0] if result.finding_codes else "PLAN_CANDIDATE_NOT_READY"
        raise LocalPlanLifecycleError(
            code,
            "Plan candidate failed semantic admission"
            + (f": {', '.join(result.finding_codes)}" if result.finding_codes else ""),
        )
    return result, document


def next_plan_revision(body: str) -> str:
    """Derive the next canonical textual Plan revision from the body."""
    _bounded_body(body)
    values = []
    for section in parse_body_sections(body):
        if section.title != "Plan Identity" and section.kind != KIND_CURRENT:
            continue
        raw = section.key_values.get("PLAN_REVISION")
        match = re.fullmatch(r"R([0-9]+)", raw or "")
        if match is not None:
            values.append(int(match.group(1)))
    if not values:
        raise LocalPlanLifecycleError(
            "PLAN_REVISION_MISSING", "canonical Plan body has no PLAN_REVISION=R<n> field"
        )
    return f"R{max(values) + 1}"


def _terminal_plan_values(
    body: str,
    evidence: PlanCloseMaterializationEvidence,
    *,
    final_revision: str,
) -> dict[str, str]:
    sections = parse_body_sections(body)
    current_fields: dict[str, str] = {}
    for section in sections:
        if section.kind == KIND_CURRENT:
            current_fields.update(section.key_values)
    milestone_ids = sorted(
        {
            match.group(1)
            for key in current_fields
            if (match := re.fullmatch(r"(M[0-9]+)_STATUS", key)) is not None
        },
        key=lambda value: int(value[1:]),
    )
    values: dict[str, str] = {
        "PLAN_REVISION": final_revision,
        "PLAN_STATUS": "completed",
        "PLAN_ACCEPTED": "yes",
        "CURRENT_STATUS": "completed",
        "CURRENT_BLOCKER": "none",
        "PLAN_FINAL_KNOWN_GOOD": evidence.final_known_good,
    }
    for milestone_id in milestone_ids:
        values[f"{milestone_id}_STATUS"] = "completed"
        values[f"{milestone_id}_ACCEPTED"] = "yes"
    values.update(
        {
            f"{evidence.milestone_id}_CLOSURE_STATUS": "APPLIED",
            f"{evidence.milestone_id}_ACCEPTED_FRONTIER": evidence.accepted_frontier,
            f"{evidence.milestone_id}_CLOSURE_PHASE": evidence.closure_phase,
        }
    )
    for key, value in {
        f"{evidence.milestone_id}_CLOSURE_CHECKPOINT_ID": evidence.milestone_checkpoint_id,
        f"{evidence.milestone_id}_CLOSURE_RECEIPT_ID": evidence.milestone_receipt_id,
        f"{evidence.milestone_id}_CLOSURE_RECEIPT_DIGEST": evidence.milestone_receipt_digest,
    }.items():
        if value is not None:
            values[key] = value
    optional_values = {
        f"{evidence.milestone_id}_CLOSURE_CHECKPOINT_DIGEST": evidence.milestone_checkpoint_digest,
        f"{evidence.milestone_id}_CLOSURE_STEWARD_DIGEST": evidence.milestone_steward_digest,
    }
    values.update({key: value for key, value in optional_values.items() if value is not None})
    if "HANDOFF_STATE" in current_fields:
        values["HANDOFF_STATE"] = "plan_closed"
    if "CURRENT_NEXT_ACTION" in current_fields:
        values["CURRENT_NEXT_ACTION"] = "Stop; Plan is physically and durably closed"
    if "NEXT_CHECKPOINT" in current_fields:
        values["NEXT_CHECKPOINT"] = "none"
    return values


def project_terminal_plan_body(
    body: str,
    evidence: PlanCloseMaterializationEvidence,
    *,
    final_revision: str | None = None,
) -> str:
    """Project accepted closure facts into the existing local Plan body.

    Only canonical Plan Identity/current sections are changed. Historical and
    explanatory sections remain byte-for-byte untouched; physical persistence
    is delegated to ``LocalPlanAuthorityAdapter`` by the lifecycle coordinator.
    """
    body = _bounded_body(body)
    if evidence.project_id not in body or evidence.plan_id not in body:
        raise LocalPlanLifecycleError(
            "CLOSURE_EVIDENCE_IDENTITY_MISMATCH",
            "closure evidence does not identify the supplied Plan body",
        )
    final_revision = final_revision or next_plan_revision(body)
    if not re.fullmatch(r"R[0-9]+", final_revision):
        raise LocalPlanLifecycleError("PLAN_REVISION_INVALID", "final revision must be R<n>")
    values = _terminal_plan_values(body, evidence, final_revision=final_revision)
    sections = parse_body_sections(body)
    lines = body.splitlines(keepends=True)
    selected: set[int] = set()
    current_end: int | None = None
    for section in sections:
        if section.title == "Plan Identity" or section.kind == KIND_CURRENT:
            selected.update(range(section.start_line, section.end_line + 1))
        if section.kind == KIND_CURRENT:
            current_end = section.end_line + 1
    if current_end is None:
        raise LocalPlanLifecycleError(
            "CURRENT_PLAN_STATE_MISSING", "canonical Plan body has no current-state section"
        )

    found: set[str] = set()
    for index in selected:
        if index >= len(lines):
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*=", lines[index])
        if match is None or match.group(1) not in values:
            continue
        key = match.group(1)
        ending = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f"{key}={values[key]}{ending}"
        found.add(key)

    missing = [key for key in values if key not in found]
    if missing:
        insertion = [f"{key}={values[key]}\n" for key in missing]
        insertion_index = min(current_end, len(lines))
        if insertion_index > 0 and not lines[insertion_index - 1].endswith("\n"):
            lines[insertion_index - 1] += "\n"
        lines[insertion_index:insertion_index] = insertion
    projected = "".join(lines)
    try:
        document = normalize_portable_plan(projected, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    except Exception as exc:
        raise LocalPlanLifecycleError(
            "TERMINAL_PLAN_PROJECTION_INVALID", f"terminal Plan projection failed normalization: {exc}"
        ) from exc
    required = {
        "PLAN_STATUS": "completed",
        "PLAN_ACCEPTED": "yes",
        "CURRENT_STATUS": "completed",
        "CURRENT_BLOCKER": "none",
    }
    if any(document.current_fields.get(key) != value for key, value in required.items()):
        raise LocalPlanLifecycleError(
            "TERMINAL_PLAN_PROJECTION_INVALID",
            "terminal Plan projection did not verify required current-state fields",
        )
    return projected


def _validate_terminal_plan_body(
    body: str,
    evidence: PlanCloseMaterializationEvidence,
    *,
    previous_body: str,
) -> None:
    """Validate a trusted candidate before allowing PLAN_CLOSE mutation."""
    if body == previous_body:
        raise LocalPlanLifecycleError(
            "TERMINAL_PLAN_PROJECTION_INVALID",
            "terminal Plan candidate must advance the physical body",
        )
    if next_plan_revision(previous_body) != _plan_revision_from_body(body):
        raise LocalPlanLifecycleError(
            "PLAN_REVISION_INVALID",
            "terminal Plan candidate must use the next canonical Plan revision",
        )
    try:
        document = normalize_portable_plan(body, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    except Exception as exc:
        raise LocalPlanLifecycleError(
            "TERMINAL_PLAN_PROJECTION_INVALID", f"terminal Plan candidate failed normalization: {exc}"
        ) from exc
    fields = document.current_fields
    required = {
        "PLAN_STATUS": "completed",
        "PLAN_ACCEPTED": "yes",
        "CURRENT_STATUS": "completed",
        "CURRENT_BLOCKER": "none",
        "PLAN_FINAL_KNOWN_GOOD": evidence.final_known_good,
        f"{evidence.milestone_id}_STATUS": "completed",
        f"{evidence.milestone_id}_ACCEPTED": "yes",
        f"{evidence.milestone_id}_CLOSURE_STATUS": "APPLIED",
        f"{evidence.milestone_id}_ACCEPTED_FRONTIER": evidence.accepted_frontier,
        f"{evidence.milestone_id}_CLOSURE_PHASE": evidence.closure_phase,
    }
    for key, expected in required.items():
        if fields.get(key) != expected:
            raise LocalPlanLifecycleError(
                "TERMINAL_PLAN_PROJECTION_INVALID",
                f"terminal Plan field {key} must be {expected!r}",
            )
    for key in fields:
        if re.fullmatch(r"M[0-9]+_STATUS", key) and fields[key] != "completed":
            raise LocalPlanLifecycleError(
                "TERMINAL_PLAN_PROJECTION_INVALID",
                f"terminal Plan milestone field {key} is not completed",
            )
        if re.fullmatch(r"M[0-9]+_ACCEPTED", key) and fields[key] != "yes":
            raise LocalPlanLifecycleError(
                "TERMINAL_PLAN_PROJECTION_INVALID",
                f"terminal Plan milestone field {key} is not accepted",
            )


def _plan_revision_from_body(body: str) -> str:
    values: set[str] = set()
    for section in parse_body_sections(body):
        if section.title != "Plan Identity" and section.kind != KIND_CURRENT:
            continue
        raw = section.key_values.get("PLAN_REVISION")
        if raw is not None and re.fullmatch(r"R[0-9]+", raw):
            values.add(raw)
    if len(values) != 1:
        raise LocalPlanLifecycleError(
            "PLAN_REVISION_INVALID", "terminal Plan body must have one consistent PLAN_REVISION"
        )
    return values.pop()


def _read_source(
    adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort,
    target: object,
) -> tuple[str | int | None, str, str]:
    """Read one source through an existing read/port boundary, never mutate it."""
    if isinstance(adapter, PlanAuthorityMutationPort):
        revision, digest, body = adapter.read_raw_authority(target)  # type: ignore[arg-type]
    elif isinstance(adapter, PlanAuthorityReadAdapter):
        snapshot = adapter.load()
        revision, digest, body = snapshot.revision, snapshot.digest, snapshot.body
    else:
        raise LocalPlanLifecycleError(
            "SOURCE_ADAPTER_INVALID",
            "source must implement PlanAuthorityReadAdapter or PlanAuthorityMutationPort",
        )
    body = _bounded_body(body)
    computed = _digest(body)
    if digest is not None and digest != computed:
        raise LocalPlanLifecycleError(
            "SOURCE_DIGEST_MISMATCH", "source-provided digest does not match source bytes"
        )
    if isinstance(revision, bool) or (
        revision is not None and not isinstance(revision, (str, int))
    ):
        raise LocalPlanLifecycleError("SOURCE_REVISION_INVALID", "source revision must be str|int|None")
    return revision, computed, body


def _binding_identity(binding: PlanAuthorityBinding) -> tuple[str, str, str]:
    return (binding.plan_id, binding.source_kind, binding.authority_ref)


def _source_binding_matches(
    actual: PlanAuthorityBinding,
    expected: PlanAuthorityBinding,
) -> bool:
    if _binding_identity(actual) != _binding_identity(expected):
        return False
    if expected.source_revision is not None and actual.source_revision != expected.source_revision:
        return False
    if expected.source_digest is not None and actual.source_digest != expected.source_digest:
        return False
    return True


def _request_identity(
    kind: str,
    request: LocalPlanLifecycleRequest,
    candidate_digest: str,
    source_binding: PlanAuthorityBinding | None,
    source_revision: str | int | None,
    source_digest: str | None,
) -> str:
    return _canonical_digest(
        {
            "kind": kind,
            "project_id": request.project_id,
            "plan_id": request.plan_id,
            "target_authority_ref": request.destination.authority_ref,
            "source_target": _source_target(request).serialize(),
            "source_binding": source_binding.to_dict() if source_binding else None,
            "source_revision": source_revision,
            "source_digest": source_digest,
            "candidate_raw_digest": candidate_digest,
            "principal": request.principal,
            "authorization_reference": request.authorization_reference,
            "lease_reference": request.lease_reference,
            "idempotency_key": request.idempotency_key,
            "intent_fingerprint": request.intent_fingerprint,
            "contract_hash": request.contract_hash,
        }
    )


def _active_orphan_recovery_identity(
    request: LocalPlanLifecycleRequest,
    *,
    expected_authority: PlanAuthorityBinding,
    orphan_source_revision: str | int,
    orphan_digest: str,
    recovery_reason: str,
) -> str:
    """Bind the exact predecessor Store authority and observed orphan body."""
    return _canonical_digest(
        {
            "kind": ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
            "project_id": request.project_id,
            "plan_id": request.plan_id,
            "target_authority_ref": request.destination.authority_ref,
            "source_target": _source_target(request).serialize(),
            "predecessor_store_binding": expected_authority.to_dict(),
            "predecessor_store_revision": request.expected_plan_revision,
            "orphan_source_revision": orphan_source_revision,
            "orphan_raw_digest": orphan_digest,
            "recovery_reason": recovery_reason,
            "principal": request.principal,
            "authorization_reference": request.authorization_reference,
            "lease_reference": request.lease_reference,
            "idempotency_key": request.idempotency_key,
            "correlation_id": request.correlation_id,
            "intent_fingerprint": request.intent_fingerprint,
            "contract_hash": request.contract_hash,
        }
    )


def _journal_id(identity: str) -> str:
    return f"local-lifecycle-{identity[:48]}"


def _failure(
    request: LocalPlanLifecycleRequest,
    phase: LocalPlanLifecyclePhase,
    code: str,
    message: str,
    *,
    journal_entry: DurableJournalEntry | None = None,
    plan_record: ProjectPlanRecord | None = None,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleResult(
        phase=phase,
        project_id=request.project_id,
        plan_id=request.plan_id,
        journal_id=journal_entry.record.journal_id if journal_entry else None,
        plan_record=plan_record,
        journal_entry=journal_entry,
        lifecycle_state=plan_record.lifecycle_state if plan_record else None,
        error_code=code,
        error_message=message,
    )


def _success(
    request: LocalPlanLifecycleRequest,
    *,
    journal_entry: DurableJournalEntry | None,
    plan_record: ProjectPlanRecord,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleResult(
        phase=LocalPlanLifecyclePhase.VERIFIED,
        project_id=request.project_id,
        plan_id=request.plan_id,
        journal_id=journal_entry.record.journal_id if journal_entry else None,
        plan_record=plan_record,
        journal_entry=journal_entry,
        lifecycle_state=plan_record.lifecycle_state,
    )


def _retirement_result(
    project_id: str,
    plan_id: str,
    phase: LocalPlanLifecyclePhase,
    *,
    plan_record: ProjectPlanRecord | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleResult(
        phase=phase,
        project_id=project_id,
        plan_id=plan_id,
        plan_record=plan_record,
        lifecycle_state=plan_record.lifecycle_state if plan_record else None,
        error_code=error_code,
        error_message=error_message,
    )


def _phase_for_entry(entry: DurableJournalEntry) -> LocalPlanLifecyclePhase:
    state = entry.record.journal_state
    if state is JournalState.OUTCOME_UNKNOWN:
        return LocalPlanLifecyclePhase.UNKNOWN
    if state in _NONTERMINAL_STATES:
        return LocalPlanLifecyclePhase.PREPARED
    if state in _SUCCESS_STATES:
        return LocalPlanLifecyclePhase.COMMITTED
    return LocalPlanLifecyclePhase.FAILED_CLOSED


def _new_record(
    request: LocalPlanLifecycleRequest,
    *,
    kind: str,
    identity: str,
    target_revision: str | int | None,
    target_digest: str,
    candidate_digest: str,
    normalized_digest: str,
    subject_revision: int,
    source_binding: PlanAuthorityBinding | None,
    source_revision: str | int | None,
    source_digest: str | None,
    evidence_extra: Mapping[str, Any] | None = None,
) -> JournalRecord:
    target_binding = PlanAuthorityBinding(
        plan_id=request.plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=request.destination.authority_ref,
    )
    evidence: dict[str, Any] = {
        "local_plan_lifecycle": kind,
        "project_id": request.project_id,
        "plan_id": request.plan_id,
        "request_identity": identity,
        "target_binding": target_binding.to_dict(),
        "source_target": _source_target(request).serialize(),
        "target_original_raw_digest": target_digest,
        "source_binding": source_binding.to_dict() if source_binding else None,
        "source_observed_revision": source_revision,
        "source_observed_raw_digest": source_digest,
        "candidate_raw_digest": candidate_digest,
        "cutover_phase": LocalPlanLifecyclePhase.PREPARED.value,
        "authority_switch": "pending",
    }
    if evidence_extra:
        evidence.update(dict(evidence_extra))
    return JournalRecord(
        journal_id=_journal_id(identity),
        correlation_id=request.correlation_id,
        attempt_id=f"attempt-{identity[:40]}",
        operation="plan_init",
        typed_target=request.destination.expected_ref,
        principal=request.principal,
        contract_hash=request.contract_hash,
        idempotency_key=request.idempotency_key,
        intent_fingerprint=request.intent_fingerprint,
        subject_expected_revision=subject_revision,
        authority_source_revision=target_revision,
        authority_observed_raw_digest=target_digest,
        candidate_raw_digest=candidate_digest,
        normalized_plan_digest=normalized_digest,
        authorization_reference=request.authorization_reference,
        lease_reference=request.lease_reference,
        authorization_basis="trusted_local_lifecycle_linkage",
        journal_state=JournalState.PREPARED,
        original_raw_digest=target_digest,
        evidence=evidence,
    )


def _mutation_request(
    record: JournalRecord,
    candidate_body: str,
) -> PortablePlanMutationRequest:
    return PortablePlanMutationRequest(
        operation=record.operation,
        typed_target=record.typed_target,
        correlation_id=record.correlation_id,
        contract_hash=record.contract_hash,
        idempotency_key=record.idempotency_key,
        intent_fingerprint=record.intent_fingerprint,
        subject_expected_revision=record.subject_expected_revision,
        authority_source_revision=record.authority_source_revision,
        authority_observed_raw_digest=record.authority_observed_raw_digest,
        candidate_raw_digest=record.candidate_raw_digest,
        normalized_plan_digest=record.normalized_plan_digest,
        principal=record.principal,
        authorization_reference=record.authorization_reference,
        lease_reference=record.lease_reference,
        attempt_reference=record.attempt_id,
        candidate_raw_body=candidate_body,
    )


def _find_existing(
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    identity: str,
) -> DurableJournalEntry | None:
    matches = journal_store.find_by_idempotency(request.idempotency_key)
    if len(matches) > 1:
        raise LocalPlanLifecycleError(
            "IDEMPOTENCY_CONFLICT",
            "multiple durable lifecycle journals claim the same idempotency key",
        )
    for entry in matches:
        evidence = entry.record.evidence
        if evidence.get("local_plan_lifecycle") not in {
            "local_plan_init",
            "local_plan_amendment",
            "authority_cutover",
            "final_plan_body_materialization_recovery",
            ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
        }:
            raise LocalPlanLifecycleError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency key is already owned by another journal operation",
            )
        if evidence.get("project_id") != request.project_id or evidence.get("plan_id") != request.plan_id:
            raise LocalPlanLifecycleError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency key crosses project or Plan identity",
            )
        if evidence.get("request_identity") != identity:
            raise LocalPlanLifecycleError(
                "IDEMPOTENCY_CONFLICT",
                "same idempotency key has changed trusted request identity",
            )
        return entry
    return None


def _journal_entry_may_match_plan(
    entry: DurableJournalEntry,
    request: LocalPlanLifecycleRequest,
) -> bool:
    """Match explicit lifecycle identity, or conservatively inspect its target."""
    evidence = entry.record.evidence
    project_id = evidence.get("project_id")
    plan_id = evidence.get("plan_id")
    if project_id == request.project_id and plan_id == request.plan_id:
        return True
    if project_id not in (None, request.project_id) or plan_id not in (None, request.plan_id):
        return False
    try:
        return (
            entry.record.typed_target.to_canonical()
            == request.destination.expected_ref.to_canonical()
        )
    except Exception:
        return False


def _source_snapshot_matches(
    adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort,
    target: object,
    *,
    expected_revision: str | int | None,
    expected_digest: str,
    expected_body_digest: str,
) -> bool:
    try:
        revision, digest, body = _read_source(adapter, target)
    except Exception:
        return False
    if digest != expected_digest or _digest(body) != expected_body_digest:
        return False
    if expected_revision is not None and revision != expected_revision:
        return False
    return True


def _source_target(request: LocalPlanLifecycleRequest) -> ObjectRef:
    return request.source_target or request.destination.expected_ref


def _source_authority_ref(adapter: object) -> str | None:
    for name in ("plan_authority", "authority_ref"):
        value = getattr(adapter, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _require_source_adapter_identity(
    adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort,
    binding: PlanAuthorityBinding,
) -> None:
    actual = _source_authority_ref(adapter)
    if actual != binding.authority_ref:
        raise LocalPlanLifecycleError(
            "SOURCE_AUTHORITY_MISMATCH",
            "source adapter must expose the exact authority_ref from the trusted binding",
        )


class LocalPlanLifecycleCoordinator:
    """One explicit local Plan lifecycle/cutover owner.

    The coordinator is intentionally dependency-injected.  It creates no
    global runtime wiring and can therefore be rebuilt in a fresh process with
    the same SQLite governance store, durable journal, trusted destination and
    source read adapter.
    """

    def __init__(
        self,
        governance_store: ProjectGovernanceStore,
        journal_store: DurableJournalStore,
    ) -> None:
        if not isinstance(governance_store, ProjectGovernanceStore):
            raise TypeError("governance_store must implement ProjectGovernanceStore")
        if not isinstance(journal_store, DurableJournalStore):
            raise TypeError("journal_store must implement DurableJournalStore")
        self.governance_store = governance_store
        self.journal_store = journal_store

    def initialize(
        self,
        request: LocalPlanLifecycleRequest,
        candidate_body: str,
    ) -> LocalPlanLifecycleResult:
        """Materialize one new local Plan and durably register it as active."""
        body = _bounded_body(candidate_body)
        candidate_digest = _digest(body)
        _, document = _require_candidate_admission(
            candidate_kind=PlanCandidateKind.INIT,
            project_id=request.project_id,
            plan_id=request.plan_id,
            candidate_body=body,
        )
        normalized_digest = portable_plan_digest(document)
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        target_adapter = LocalPlanAuthorityAdapter(request.destination)
        identity = _request_identity(
            "local_plan_init",
            request,
            candidate_digest,
            None,
            None,
            None,
        )
        try:
            existing = _find_existing(self.journal_store, request, identity)
        except LocalPlanLifecycleError as exc:
            return _failure(request, LocalPlanLifecyclePhase.FAILED_CLOSED, exc.code, exc.message, plan_record=current)
        if existing is not None:
            return self._resume(
                request,
                existing,
                candidate_body=body,
                source_adapter=None,
                source_binding=None,
            )

        if current is not None:
            if request.expected_plan_revision is not None and current.revision != request.expected_plan_revision:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "STALE_PLAN_REVISION",
                    "local Plan record revision is stale",
                    plan_record=current,
                )
            if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "PLAN_NOT_ACTIVE",
                    "a retired Plan cannot be implicitly resurrected",
                    plan_record=current,
                )
            if current.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "AUTHORITY_ALREADY_BOUND",
                    "existing non-local authority requires explicit cutover",
                    plan_record=current,
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_ALREADY_INITIALIZED",
                "an active local Plan cannot be reinitialized; use the amendment lifecycle",
                plan_record=current,
            )

        try:
            target_revision, target_digest, _ = target_adapter.read_raw_authority(
                request.destination.expected_ref
            )
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "LOCAL_TARGET_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )

        record = _new_record(
            request,
            kind="local_plan_init",
            identity=identity,
            target_revision=target_revision,
            target_digest=target_digest or _digest(""),
            candidate_digest=candidate_digest,
            normalized_digest=normalized_digest,
            subject_revision=current.revision if current is not None else 0,
            source_binding=None,
            source_revision=None,
            source_digest=None,
        )
        return self._create_and_attempt(
            request,
            record,
            body,
            target_adapter,
            source_adapter=None,
            source_binding=None,
            source_revision=None,
            source_digest=None,
            source_body=None,
        )

    def amend(
        self,
        request: LocalPlanLifecycleRequest,
        candidate_body: str,
        *,
        expected_authority: PlanAuthorityBinding,
        amendment_event: Mapping[str, Any],
    ) -> LocalPlanLifecycleResult:
        """Replace one existing local Plan through the existing init pipeline.

        This is an internal local-governance seam, not a new Agent operation:
        the journal and mutation port still carry the canonical ``plan_init``
        contract. The replacement is admitted only with the caller's exact
        Project/Plan revision and raw authority precondition.
        """
        body = _bounded_body(candidate_body)
        candidate_digest = _digest(body)
        _, document = _require_candidate_admission(
            candidate_kind=PlanCandidateKind.AMEND,
            project_id=request.project_id,
            plan_id=request.plan_id,
            candidate_body=body,
        )
        normalized_digest = portable_plan_digest(document)
        current = self.governance_store.get_plan(request.project_id, request.plan_id)

        if not isinstance(expected_authority, PlanAuthorityBinding):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_BINDING_INVALID",
                "amendment requires the current local Plan authority binding",
                plan_record=current,
            )
        if (
            expected_authority.plan_id != request.plan_id
            or expected_authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
            or expected_authority.authority_ref != request.destination.authority_ref
            or expected_authority.source_revision is None
            or expected_authority.source_digest is None
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_PRECONDITION_INVALID",
                "amendment requires the complete current local authority binding",
                plan_record=current,
            )
        if request.expected_plan_revision is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_REVISION_REQUIRED",
                "local Plan amendment requires an explicit Project Governance CAS revision",
                plan_record=current,
            )
        try:
            event = dict(amendment_event)
        except (TypeError, ValueError) as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_EVENT_INVALID",
                f"amendment event must be a mapping: {exc}",
                plan_record=current,
            )
        if event.get("event") != "plan_amendment":
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_EVENT_INVALID",
                "amendment event must identify plan_amendment",
                plan_record=current,
            )
        decision_valid, decision_error = _verify_plan_amendment_decision(event, request)
        if not decision_valid:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "MATERIALIZED_DECISION_REQUIRED",
                decision_error,
                plan_record=current,
            )

        identity = _request_identity(
            "local_plan_amendment",
            request,
            candidate_digest,
            expected_authority,
            expected_authority.source_revision,
            expected_authority.source_digest,
        )
        try:
            existing = _find_existing(self.journal_store, request, identity)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        if existing is not None:
            return self._resume(
                request,
                existing,
                candidate_body=None,
                source_adapter=None,
                source_binding=None,
            )

        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.BOUND,
                "PLAN_RECORD_REQUIRED",
                "local Plan amendment requires an existing Project Governance Plan record",
            )
        if current.revision != request.expected_plan_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_PLAN_REVISION",
                "local Plan record revision is stale",
                plan_record=current,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "only an active local Plan can be amended",
                plan_record=current,
            )
        if not _source_binding_matches(current.authority, expected_authority):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_BINDING_STALE",
                "current local Plan authority does not match the amendment CAS precondition",
                plan_record=current,
            )

        target_adapter = LocalPlanAuthorityAdapter(
            request.destination,
            allow_existing_replacement=True,
        )
        try:
            target_revision, target_digest, target_body = target_adapter.read_raw_authority(
                request.destination.expected_ref
            )
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "LOCAL_TARGET_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        if not target_body:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "LOCAL_PLAN_MISSING",
                "local Plan amendment requires an existing non-empty Plan document",
                plan_record=current,
            )
        if (
            target_revision != expected_authority.source_revision
            or target_digest != expected_authority.source_digest
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_AUTHORITY",
                "local Plan document does not match the amendment raw CAS precondition",
                plan_record=current,
            )

        event.update(
            {
                "local_plan_lifecycle": "local_plan_amendment",
                "project_id": request.project_id,
                "plan_id": request.plan_id,
                "amendment_precondition_binding": expected_authority.to_dict(),
                "original_plan_raw_digest": target_digest,
                "candidate_plan_raw_digest": candidate_digest,
            }
        )
        record = _new_record(
            request,
            kind="local_plan_amendment",
            identity=identity,
            target_revision=target_revision,
            target_digest=target_digest,
            candidate_digest=candidate_digest,
            normalized_digest=normalized_digest,
            subject_revision=current.revision,
            source_binding=None,
            source_revision=target_revision,
            source_digest=target_digest,
            evidence_extra=event,
        )
        return self._create_and_attempt(
            request,
            record,
            body,
            target_adapter,
            source_adapter=None,
            source_binding=None,
            source_revision=target_revision,
            source_digest=target_digest,
            source_body=target_body,
        )

    def close_local_plan(
        self,
        request: LocalPlanLifecycleRequest,
        candidate_body: str,
        *,
        expected_authority: PlanAuthorityBinding,
        closure_evidence: PlanCloseMaterializationEvidence,
    ) -> LocalPlanLifecycleResult:
        """Materialize a terminal body, verify it, then retire the Plan.

        This is the only composite local PLAN_CLOSE seam.  The existing
        amendment journal/adapter/CAS path owns body mutation; retirement is
        attempted only after that path has durably verified the candidate.
        """
        body = _bounded_body(candidate_body)
        if closure_evidence.project_id != request.project_id or closure_evidence.plan_id != request.plan_id:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "CLOSURE_EVIDENCE_IDENTITY_MISMATCH",
                "accepted closure evidence does not match the requested Plan",
            )
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_REQUIRED",
                "local Plan close requires an existing Project Governance Plan record",
            )
        if current.lifecycle_state == PLAN_LIFECYCLE_RETIRED:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Plan requires the bounded stale-body recovery seam",
                plan_record=current,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "local Plan close requires an active Plan",
                plan_record=current,
            )
        if request.expected_plan_revision is None:
            request = replace(request, expected_plan_revision=current.revision)

        try:
            _, observed_digest, previous_body = LocalPlanAuthorityAdapter(
                request.destination,
                allow_existing_replacement=True,
            ).read_raw_authority(request.destination.expected_ref)
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "LOCAL_TARGET_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        if current.authority.source_digest != observed_digest:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_AUTHORITY",
                "local Plan body does not match the trusted close precondition",
                plan_record=current,
            )
        try:
            _validate_terminal_plan_body(body, closure_evidence, previous_body=previous_body)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )

        amended = self.amend(
            request,
            body,
            expected_authority=expected_authority,
            amendment_event={
                "event": "plan_amendment",
                "classification": closure_evidence.classification,
                "accepted_closure_decision_reused": True,
                "closure_semantic_decision_reexecuted": False,
                "closure_evidence": closure_evidence.to_dict(),
            },
        )
        if not amended.ok or amended.plan_record is None:
            return amended
        retired = self.retire(
            project_id=request.project_id,
            plan_id=request.plan_id,
            expected_revision=amended.plan_record.revision,
        )
        if not retired.ok or retired.plan_record is None:
            return _failure(
                request,
                retired.phase,
                retired.error_code or "PLAN_CLOSE_RETIREMENT_FAILED",
                retired.error_message or "Plan body materialized but retirement did not verify",
                journal_entry=amended.journal_entry,
                plan_record=retired.plan_record or amended.plan_record,
            )
        verified = self._verify_terminal_body(
            request,
            body,
            retired.plan_record,
            journal_entry=amended.journal_entry,
        )
        if not verified.ok:
            return verified
        return self._mark_close_terminal_evidence(
            request,
            verified,
            closure_evidence=closure_evidence,
            recovery=False,
        )

    def recover_retired_final_body(
        self,
        request: LocalPlanLifecycleRequest,
        candidate_body: str,
        *,
        expected_authority: PlanAuthorityBinding,
        closure_evidence: PlanCloseMaterializationEvidence,
    ) -> LocalPlanLifecycleResult:
        """Recover one retired Plan whose body is still the trusted pre-close body.

        This seam never reopens lifecycle state and never accepts a normal
        retired mutation.  It requires exact pre-close body/store CAS facts
        plus trusted accepted PLAN_CLOSE evidence.
        """
        body = _bounded_body(candidate_body)
        if closure_evidence.project_id != request.project_id or closure_evidence.plan_id != request.plan_id:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "foreign closure evidence is not admissible for retired-body recovery",
            )
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_FOUND",
                "retired Plan record is missing",
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_RETIRED:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired stale-body recovery requires lifecycle_state=retired",
                plan_record=current,
            )
        if request.expected_plan_revision is not None and current.revision != request.expected_plan_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Plan revision changed before recovery",
                plan_record=current,
            )
        if not isinstance(expected_authority, PlanAuthorityBinding) or not _source_binding_matches(
            current.authority, expected_authority
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Plan authority is not the trusted pre-close authority",
                plan_record=current,
            )
        target_adapter = LocalPlanAuthorityAdapter(
            request.destination,
            allow_existing_replacement=True,
        )
        try:
            target_revision, target_digest, target_body = target_adapter.read_raw_authority(
                request.destination.expected_ref
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                f"trusted pre-close Plan read failed: {exc}",
                plan_record=current,
            )
        if (
            target_digest != expected_authority.source_digest
            or target_revision != expected_authority.source_revision
            or _digest(target_body) != expected_authority.source_digest
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "physical Plan body is not the exact stored pre-close authority",
                plan_record=current,
            )
        try:
            _validate_terminal_plan_body(body, closure_evidence, previous_body=target_body)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        candidate_digest = _digest(body)
        if candidate_digest == target_digest:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "recovery candidate does not advance the stale Plan body",
                plan_record=current,
            )
        normalized_digest = _normalized_digest(body)
        identity = _request_identity(
            "final_plan_body_materialization_recovery",
            request,
            candidate_digest,
            expected_authority,
            expected_authority.source_revision,
            expected_authority.source_digest,
        )
        try:
            existing = _find_existing(self.journal_store, request, identity)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        if existing is not None:
            return self._resume(
                request,
                existing,
                candidate_body=None,
                source_adapter=None,
                source_binding=None,
            )
        record = _new_record(
            request,
            kind="final_plan_body_materialization_recovery",
            identity=identity,
            target_revision=target_revision,
            target_digest=target_digest,
            candidate_digest=candidate_digest,
            normalized_digest=normalized_digest,
            subject_revision=current.revision,
            source_binding=expected_authority,
            source_revision=expected_authority.source_revision,
            source_digest=expected_authority.source_digest,
            evidence_extra={
                "classification": closure_evidence.classification,
                "accepted_closure_decision_reused": True,
                "closure_semantic_decision_reexecuted": False,
                "recovery_precondition_binding": expected_authority.to_dict(),
                "closure_evidence": closure_evidence.to_dict(),
                "recovery_phase": LocalPlanLifecyclePhase.PREPARED.value,
            },
        )
        result = self._create_and_attempt(
            request,
            record,
            body,
            target_adapter,
            source_adapter=None,
            source_binding=None,
            source_revision=expected_authority.source_revision,
            source_digest=expected_authority.source_digest,
            source_body=target_body,
        )
        if not result.ok:
            return result
        return self._mark_close_terminal_evidence(
            request,
            result,
            closure_evidence=closure_evidence,
            recovery=True,
        )

    def recover_active_local_plan_orphan_body(
        self,
        request: LocalPlanLifecycleRequest,
        *,
        expected_authority: PlanAuthorityBinding,
        expected_orphan_digest: str,
        recovery_reason: str,
    ) -> LocalPlanLifecycleResult:
        """Reconcile one observed active local Plan body through the Gov2 lifecycle.

        The body is read only from the trusted destination. Recovery does not
        synthesize Plan content and reuses the existing journal, adapter,
        RecoveryExecutor and Project Governance Store CAS.
        """
        if not isinstance(request, LocalPlanLifecycleRequest):
            raise TypeError("request must be LocalPlanLifecycleRequest")
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_REQUIRED",
                "active orphan-body recovery requires an existing Project Governance Plan record",
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "active orphan-body recovery requires lifecycle_state=active",
                plan_record=current,
            )
        if not isinstance(expected_authority, PlanAuthorityBinding):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "RECOVERY_PRECONDITION_BINDING_INVALID",
                "recovery requires the exact predecessor Store authority binding",
                plan_record=current,
            )
        if (
            expected_authority.plan_id != request.plan_id
            or expected_authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
            or expected_authority.authority_ref != request.destination.authority_ref
            or expected_authority.source_revision is None
            or expected_authority.source_digest is None
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "RECOVERY_PRECONDITION_BINDING_INVALID",
                "recovery predecessor must be the complete local authority binding for this Plan",
                plan_record=current,
            )
        if request.expected_plan_revision is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_REVISION_REQUIRED",
                "active orphan-body recovery requires an explicit Store CAS revision",
                plan_record=current,
            )
        if not isinstance(expected_orphan_digest, str) or not _SHA256_RE.fullmatch(
            expected_orphan_digest
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ORPHAN_DIGEST_INVALID",
                "expected_orphan_digest must be a lowercase SHA-256 digest",
                plan_record=current,
            )
        if recovery_reason != ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_REASON:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "RECOVERY_REASON_INVALID",
                "recovery_reason must identify the bounded active orphan-body case",
                plan_record=current,
            )

        target_adapter = LocalPlanAuthorityAdapter(
            request.destination,
            allow_existing_replacement=True,
        )
        try:
            orphan_revision, orphan_digest, orphan_body = target_adapter.read_raw_authority(
                request.destination.expected_ref
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ORPHAN_PLAN_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        try:
            orphan_body = _bounded_body(orphan_body)
            _, document = _require_candidate_admission(
                candidate_kind=PlanCandidateKind.AMEND,
                project_id=request.project_id,
                plan_id=request.plan_id,
                candidate_body=orphan_body,
            )
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        normalized_digest = portable_plan_digest(document)
        if (
            orphan_digest != expected_orphan_digest
            or _digest(orphan_body) != expected_orphan_digest
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ORPHAN_BODY_DIGEST_MISMATCH",
                "trusted local Plan bytes do not match the authorized orphan digest",
                plan_record=current,
            )
        if orphan_revision != orphan_digest:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ORPHAN_SOURCE_REVISION_MISMATCH",
                "local Plan source revision does not match its observed raw digest",
                plan_record=current,
            )

        identity = _active_orphan_recovery_identity(
            request,
            expected_authority=expected_authority,
            orphan_source_revision=orphan_revision,
            orphan_digest=orphan_digest,
            recovery_reason=recovery_reason,
        )
        try:
            existing = _find_existing(self.journal_store, request, identity)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        if existing is not None:
            evidence = existing.record.evidence
            if (
                evidence.get("local_plan_lifecycle")
                != ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND
                or evidence.get("candidate_raw_digest") != orphan_digest
                or evidence.get("orphan_observed_raw_digest") != orphan_digest
                or evidence.get("orphan_observed_revision") != orphan_revision
                or evidence.get("predecessor_store_revision")
                != request.expected_plan_revision
                or evidence.get("recovery_precondition_binding")
                != expected_authority.to_dict()
            ):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "ORPHAN_RECOVERY_JOURNAL_IDENTITY_MISMATCH",
                    "existing recovery journal does not bind both exact sides of this authority split",
                    journal_entry=existing,
                    plan_record=current,
                )
            if existing.record.journal_state is JournalState.PREPARED:
                try:
                    entry = RecoveryExecutor(
                        self.journal_store, target_adapter
                    ).attempt_external_mutation(
                        existing.record.journal_id,
                        _mutation_request(existing.record, orphan_body),
                    )
                except Exception as exc:
                    return _failure(
                        request,
                        LocalPlanLifecyclePhase.UNKNOWN,
                        "ORPHAN_RECOVERY_ATTEMPT_UNKNOWN",
                        str(exc),
                        journal_entry=existing,
                        plan_record=current,
                    )
                return self._after_materialization(
                    request,
                    entry,
                    candidate_body=orphan_body,
                    target_adapter=target_adapter,
                    source_adapter=None,
                    source_binding=None,
                    source_revision=orphan_revision,
                    source_digest=orphan_digest,
                    source_body=orphan_body,
                )
            return self._resume(
                request,
                existing,
                candidate_body=None,
                source_adapter=None,
                source_binding=None,
            )

        try:
            all_entries = self.journal_store.list_all()
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ORPHAN_RECOVERY_JOURNAL_SCAN_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        for entry in all_entries:
            evidence = entry.record.evidence
            if (
                _journal_entry_may_match_plan(entry, request)
                and (
                    entry.record.candidate_raw_digest == orphan_digest
                    or evidence.get("candidate_plan_raw_digest") == orphan_digest
                )
            ):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "ORPHAN_RECOVERY_EXISTING_CANDIDATE_LINEAGE",
                    "another durable journal already owns the exact orphan candidate digest",
                    journal_entry=entry,
                    plan_record=current,
                )

        try:
            nonterminal = self.journal_store.scan_nonterminal()
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ORPHAN_RECOVERY_NONTERMINAL_SCAN_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        for entry in nonterminal:
            if _journal_entry_may_match_plan(entry, request):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "ACTIVE_PLAN_RECOVERY_CONCURRENT_MUTATION",
                    "another nonterminal local lifecycle mutation exists for this Plan",
                    journal_entry=entry,
                    plan_record=current,
                )

        desired = PlanAuthorityBinding(
            plan_id=request.plan_id,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=request.destination.authority_ref,
            source_revision=orphan_revision,
            source_digest=orphan_digest,
        )
        if current.authority == desired:
            return _success(request, journal_entry=None, plan_record=current)
        if expected_authority.source_digest == orphan_digest:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ORPHAN_RECOVERY_PREDECESSOR_NOT_DISTINCT",
                "the expected predecessor digest is not distinct from the observed orphan body",
                plan_record=current,
            )
        if current.revision != request.expected_plan_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_PLAN_REVISION",
                "Project Governance Store revision differs from the recovery CAS precondition",
                plan_record=current,
            )
        if not _source_binding_matches(current.authority, expected_authority):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "RECOVERY_PRECONDITION_BINDING_MISMATCH",
                "current Store authority does not exactly match the supplied predecessor binding",
                plan_record=current,
            )

        record = _new_record(
            request,
            kind=ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
            identity=identity,
            target_revision=orphan_revision,
            target_digest=orphan_digest,
            candidate_digest=orphan_digest,
            normalized_digest=normalized_digest,
            subject_revision=current.revision,
            source_binding=None,
            source_revision=orphan_revision,
            source_digest=orphan_digest,
            evidence_extra={
                "event": ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
                "recovery_reason": recovery_reason,
                "recovery_precondition_binding": expected_authority.to_dict(),
                "predecessor_store_revision": current.revision,
                "orphan_observed_revision": orphan_revision,
                "orphan_observed_raw_digest": orphan_digest,
                "recovery_phase": LocalPlanLifecyclePhase.PREPARED.value,
                "authority_identity_switch": "unchanged",
            },
        )
        return self._create_and_attempt(
            request,
            record,
            orphan_body,
            target_adapter,
            source_adapter=None,
            source_binding=None,
            source_revision=orphan_revision,
            source_digest=orphan_digest,
            source_body=orphan_body,
        )

    def cutover(
        self,
        request: LocalPlanLifecycleRequest,
        source_binding: PlanAuthorityBinding,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort,
    ) -> LocalPlanLifecycleResult:
        """Materialize a GitHub-bound Plan locally, then CAS the binding once."""
        if not isinstance(source_binding, PlanAuthorityBinding):
            raise LocalPlanLifecycleError("SOURCE_BINDING_INVALID", "source_binding is required")
        if source_binding.source_kind != PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE:
            raise LocalPlanLifecycleError(
                "SOURCE_KIND_INVALID", "only an existing github_issue authority can be cut over"
            )
        if source_binding.plan_id != request.plan_id:
            raise LocalPlanLifecycleError("SOURCE_PLAN_MISMATCH", "source binding Plan identity differs")
        try:
            _require_source_adapter_identity(source_adapter, source_binding)
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
            )

        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.BOUND,
                "PLAN_RECORD_REQUIRED",
                "authority cutover requires an existing Project Governance Plan record",
            )
        try:
            source_revision, source_digest, source_body = _read_source(
                source_adapter, _source_target(request)
            )
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "SOURCE_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        if source_binding.source_revision is not None and source_revision != source_binding.source_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "SOURCE_PRECONDITION_STALE",
                "source revision changed before local materialization",
                plan_record=current,
            )
        if source_binding.source_digest is not None and source_digest != source_binding.source_digest:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "SOURCE_PRECONDITION_STALE",
                "source digest changed before local materialization",
                plan_record=current,
            )
        candidate_digest = _digest(source_body)
        normalized_digest = _normalized_digest(source_body)
        identity = _request_identity(
            "authority_cutover",
            request,
            candidate_digest,
            source_binding,
            source_revision,
            source_digest,
        )
        try:
            existing = _find_existing(self.journal_store, request, identity)
        except LocalPlanLifecycleError as exc:
            return _failure(request, LocalPlanLifecyclePhase.FAILED_CLOSED, exc.code, exc.message, plan_record=current)
        if existing is not None:
            return self._resume(
                request,
                existing,
                candidate_body=source_body,
                source_adapter=source_adapter,
                source_binding=source_binding,
            )

        if request.expected_plan_revision is not None and current.revision != request.expected_plan_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_PLAN_REVISION",
                "local Plan record revision is stale",
                plan_record=current,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "only an active Plan authority can be cut over",
                plan_record=current,
            )

        if not _source_binding_matches(current.authority, source_binding):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "SOURCE_BINDING_MISMATCH",
                "stored current authority does not match the explicit cutover source",
                plan_record=current,
            )

        target_adapter = LocalPlanAuthorityAdapter(request.destination)
        try:
            target_revision, target_digest, _ = target_adapter.read_raw_authority(
                request.destination.expected_ref
            )
        except LocalPlanLifecycleError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                exc.code,
                exc.message,
                plan_record=current,
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "LOCAL_TARGET_READ_UNKNOWN",
                str(exc),
                plan_record=current,
            )
        record = _new_record(
            request,
            kind="authority_cutover",
            identity=identity,
            target_revision=target_revision,
            target_digest=target_digest or _digest(""),
            candidate_digest=candidate_digest,
            normalized_digest=normalized_digest,
            subject_revision=current.revision,
            source_binding=source_binding,
            source_revision=source_revision,
            source_digest=source_digest,
        )
        return self._create_and_attempt(
            request,
            record,
            source_body,
            target_adapter,
            source_adapter=source_adapter,
            source_binding=source_binding,
            source_revision=source_revision,
            source_digest=source_digest,
            source_body=source_body,
        )

    def recover(
        self,
        request: LocalPlanLifecycleRequest,
        *,
        source_binding: PlanAuthorityBinding | None = None,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None = None,
    ) -> LocalPlanLifecycleResult:
        """Recover one journal lineage in a fresh process without blind retry."""
        matches = self.journal_store.find_by_idempotency(request.idempotency_key)
        if not matches:
            return _failure(
                request,
                LocalPlanLifecyclePhase.BOUND,
                "JOURNAL_NOT_FOUND",
                "no local lifecycle journal exists for the idempotency key",
            )
        if len(matches) != 1:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "IDEMPOTENCY_CONFLICT",
                "multiple durable lifecycle journals claim the same idempotency key",
                journal_entry=matches[0],
            )
        entry = matches[0]
        evidence = entry.record.evidence
        if evidence.get("project_id") != request.project_id or evidence.get("plan_id") != request.plan_id:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "IDEMPOTENCY_CONFLICT",
                "journal lineage crosses project or Plan identity",
                journal_entry=entry,
            )
        if evidence.get("source_target") != _source_target(request).serialize():
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "SOURCE_TARGET_MISMATCH",
                "fresh-process recovery target differs from the durable lifecycle target",
                journal_entry=entry,
            )
        if evidence.get("local_plan_lifecycle") == ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND:
            try:
                expected_authority = PlanAuthorityBinding.from_dict(
                    evidence["recovery_precondition_binding"]
                )
                orphan_digest = str(evidence["orphan_observed_raw_digest"])
                recovery_reason = str(evidence["recovery_reason"])
            except (KeyError, TypeError, ValueError) as exc:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "JOURNAL_CORRUPT",
                    f"active orphan recovery journal evidence is invalid: {exc}",
                    journal_entry=entry,
                )
            return self.recover_active_local_plan_orphan_body(
                request,
                expected_authority=expected_authority,
                expected_orphan_digest=orphan_digest,
                recovery_reason=recovery_reason,
            )
        try:
            stored_source = evidence.get("source_binding")
            stored_binding = (
                PlanAuthorityBinding.from_dict(stored_source)
                if isinstance(stored_source, dict)
                else None
            )
            identity = _request_identity(
                str(evidence.get("local_plan_lifecycle")),
                request,
                str(evidence["candidate_raw_digest"]),
                stored_binding,
                evidence.get("source_observed_revision"),
                evidence.get("source_observed_raw_digest"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                f"local lifecycle journal evidence is invalid: {exc}",
                journal_entry=entry,
            )
        if identity != evidence.get("request_identity"):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "IDEMPOTENCY_CONFLICT",
                "fresh-process request does not match durable lifecycle identity",
                journal_entry=entry,
            )
        if stored_binding is not None:
            if source_binding is None or not _source_binding_matches(stored_binding, source_binding):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "SOURCE_BINDING_MISMATCH",
                    "fresh-process recovery requires the same explicit source binding",
                    journal_entry=entry,
                )
            if source_adapter is None:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "SOURCE_ADAPTER_REQUIRED",
                    "cutover recovery requires a fresh source read adapter",
                    journal_entry=entry,
                )
            try:
                _require_source_adapter_identity(source_adapter, stored_binding)
            except LocalPlanLifecycleError as exc:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    exc.code,
                    exc.message,
                    journal_entry=entry,
                )
        return self._resume(
            request,
            entry,
            candidate_body=None,
            source_adapter=source_adapter,
            source_binding=source_binding,
        )

    def retire(
        self,
        *,
        project_id: str,
        plan_id: str,
        expected_revision: int,
    ) -> LocalPlanLifecycleResult:
        """Retire a local Plan record through the existing Project Store CAS."""
        if not isinstance(project_id, str) or not project_id.strip() or not is_plan_id(plan_id):
            raise LocalPlanLifecycleError("PLAN_ID_INVALID", "canonical project and Plan identity required")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise LocalPlanLifecycleError("PLAN_REVISION_INVALID", "expected_revision must be positive")
        current = self.governance_store.get_plan(project_id, plan_id)
        if current is None:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                error_code="PLAN_NOT_FOUND",
                error_message="Plan record not found",
            )
        if current.revision != expected_revision:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                plan_record=current,
                error_code="STALE_PLAN_REVISION",
                error_message="retirement CAS expectation is stale",
            )
        if current.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                plan_record=current,
                error_code="LOCAL_AUTHORITY_REQUIRED",
                error_message="only a local-governance Plan record can use local lifecycle retirement",
            )
        if current.lifecycle_state == PLAN_LIFECYCLE_RETIRED:
            return _retirement_result(
                project_id, plan_id, LocalPlanLifecyclePhase.VERIFIED, plan_record=current
            )
        try:
            updated = self.governance_store.compare_and_swap_plan(
                project_id,
                plan_id,
                expected_revision,
                lifecycle_state=PLAN_LIFECYCLE_RETIRED,
            )
        except StalePlanRevisionError as exc:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                plan_record=self.governance_store.get_plan(project_id, plan_id),
                error_code="STALE_PLAN_REVISION",
                error_message=str(exc),
            )
        except ProjectGovernancePersistenceError as exc:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.UNKNOWN,
                plan_record=current,
                error_code="GOVERNANCE_PERSISTENCE_FAILURE",
                error_message=str(exc),
            )
        verified = self.governance_store.get_plan(project_id, plan_id)
        if verified is None or verified.lifecycle_state != PLAN_LIFECYCLE_RETIRED or verified.revision != updated.revision:
            return _retirement_result(
                project_id,
                plan_id,
                LocalPlanLifecyclePhase.UNKNOWN,
                plan_record=verified or updated,
                error_code="LIFECYCLE_READBACK_FAILED",
                error_message="retired Plan record did not verify after CAS",
            )
        return _retirement_result(
            project_id, plan_id, LocalPlanLifecyclePhase.VERIFIED, plan_record=verified
        )

    def resolve_active_local_plan(
        self,
        destination: LocalPlanAuthorityDestination,
    ) -> tuple[ProjectPlanRecord, Any]:
        """Resolve exactly one active local authority for a trusted destination."""
        record = self.governance_store.get_plan(destination.project_id, destination.plan_id)
        if record is None:
            raise LocalPlanLifecycleError("PLAN_NOT_FOUND", "active local Plan record not found")
        if record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            raise LocalPlanLifecycleError("PLAN_NOT_ACTIVE", "Plan record is not active")
        if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            raise LocalPlanLifecycleError("LOCAL_AUTHORITY_REQUIRED", "Plan is not locally bound")
        if record.authority.authority_ref != destination.authority_ref:
            raise LocalPlanLifecycleError("AUTHORITY_BINDING_MISMATCH", "trusted destination is not the active binding")
        from aota_forge.adapters.plan_authority.local_governance import LocalPlanAuthorityReadAdapter

        return record, LocalPlanAuthorityReadAdapter(destination, binding=record.authority)

    def _create_and_attempt(
        self,
        request: LocalPlanLifecycleRequest,
        record: JournalRecord,
        candidate_body: str,
        target_adapter: LocalPlanAuthorityAdapter,
        *,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None,
        source_binding: PlanAuthorityBinding | None,
        source_revision: str | int | None,
        source_digest: str | None,
        source_body: str | None,
    ) -> LocalPlanLifecycleResult:
        executor = RecoveryExecutor(self.journal_store, target_adapter)
        try:
            prepared = executor.create_prepared(record)
        except JournalError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                getattr(exc, "code", "JOURNAL_PERSISTENCE_FAILURE"),
                str(exc),
            )
        try:
            entry = executor.attempt_external_mutation(
                record.journal_id,
                _mutation_request(record, candidate_body),
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "JOURNAL_ATTEMPT_UNKNOWN",
                str(exc),
                journal_entry=prepared,
            )
        return self._after_materialization(
            request,
            entry,
            candidate_body=candidate_body,
            target_adapter=target_adapter,
            source_adapter=source_adapter,
            source_binding=source_binding,
            source_revision=source_revision,
            source_digest=source_digest,
            source_body=source_body,
        )

    def _mark_terminal_evidence(
        self,
        request: LocalPlanLifecycleRequest,
        result: LocalPlanLifecycleResult,
    ) -> LocalPlanLifecycleResult:
        """Persist the cross-store completion fact without reopening mutation."""
        entry = result.journal_entry
        if not result.ok or entry is None:
            return result
        lifecycle_kind = entry.record.evidence.get("local_plan_lifecycle")
        if lifecycle_kind == "local_plan_amendment":
            completion_evidence = {
                "event": "plan_amendment",
                "amendment_phase": LocalPlanLifecyclePhase.VERIFIED.value,
                "authority_switch": "unchanged",
                "binding_revision": result.plan_record.revision if result.plan_record else None,
            }
        elif lifecycle_kind == "final_plan_body_materialization_recovery":
            completion_evidence = {
                "event": "final_plan_body_materialization_recovery",
                "recovery_phase": LocalPlanLifecyclePhase.VERIFIED.value,
                "authority_switch": "unchanged",
                "binding_revision": result.plan_record.revision if result.plan_record else None,
            }
        elif lifecycle_kind == ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND:
            predecessor = entry.record.evidence.get("recovery_precondition_binding")
            predecessor_digest = (
                predecessor.get("source_digest") if isinstance(predecessor, Mapping) else None
            )
            recovered_digest = (
                result.plan_record.authority.source_digest if result.plan_record else None
            )
            completion_evidence = {
                "event": ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
                "recovery_phase": LocalPlanLifecyclePhase.VERIFIED.value,
                "authority_switch": "unchanged",
                "authority_identity_switch": "unchanged",
                "authority_binding_digest_converged": True,
                "binding_revision": result.plan_record.revision if result.plan_record else None,
                "predecessor_source_digest": predecessor_digest,
                "recovered_source_digest": recovered_digest,
            }
        else:
            completion_evidence = {
                "authority_switch": "complete",
                "cutover_phase": LocalPlanLifecyclePhase.VERIFIED.value,
                "binding_revision": result.plan_record.revision if result.plan_record else None,
            }
        if all(
            entry.record.evidence.get(key) == value
            for key, value in completion_evidence.items()
        ):
            return result
        try:
            _, updated = self.journal_store.cas_update_evidence(
                entry.record.journal_id,
                entry.journal_revision,
                evidence=completion_evidence,
            )
        except StaleJournalRevisionError:
            latest = self.journal_store.get(entry.record.journal_id)
            if latest is not None and all(
                latest.record.evidence.get(key) == value
                for key, value in completion_evidence.items()
            ):
                return replace(
                    result,
                    journal_entry=latest,
                    journal_id=latest.record.journal_id,
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "JOURNAL_TERMINAL_EVIDENCE_CONFLICT",
                "terminal lifecycle evidence changed before completion could be recorded",
                journal_entry=latest or entry,
                plan_record=result.plan_record,
            )
        except JournalPersistenceFailureError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "JOURNAL_TERMINAL_EVIDENCE_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=result.plan_record,
            )
        return replace(result, journal_entry=updated, journal_id=updated.record.journal_id)

    def _verify_terminal_body(
        self,
        request: LocalPlanLifecycleRequest,
        candidate_body: str,
        plan_record: ProjectPlanRecord,
        *,
        journal_entry: DurableJournalEntry | None,
    ) -> LocalPlanLifecycleResult:
        try:
            observed_revision, observed_digest, observed_body = LocalPlanAuthorityAdapter(
                request.destination,
                allow_existing_replacement=True,
            ).read_raw_authority(request.destination.expected_ref)
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "FINAL_BODY_READBACK_UNKNOWN",
                str(exc),
                journal_entry=journal_entry,
                plan_record=plan_record,
            )
        candidate_digest = _digest(candidate_body)
        if observed_digest != candidate_digest or observed_body != candidate_body:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "FINAL_BODY_READBACK_MISMATCH",
                "retired Plan body did not match the exact terminal candidate",
                journal_entry=journal_entry,
                plan_record=plan_record,
            )
        if (
            plan_record.lifecycle_state != PLAN_LIFECYCLE_RETIRED
            or plan_record.authority.source_digest != candidate_digest
            or plan_record.authority.source_revision != observed_revision
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "FINAL_BODY_STORE_CONVERGENCE_FAILED",
                "retired Store authority did not converge to the terminal body",
                journal_entry=journal_entry,
                plan_record=plan_record,
            )
        return _success(request, journal_entry=journal_entry, plan_record=plan_record)

    def _mark_close_terminal_evidence(
        self,
        request: LocalPlanLifecycleRequest,
        result: LocalPlanLifecycleResult,
        *,
        closure_evidence: PlanCloseMaterializationEvidence,
        recovery: bool,
    ) -> LocalPlanLifecycleResult:
        if not result.ok or result.journal_entry is None or result.plan_record is None:
            return result
        evidence = {
            "closure_materialization": closure_evidence.classification,
            "accepted_closure_decision_reused": True,
            "closure_semantic_decision_reexecuted": False,
            "closure_evidence": closure_evidence.to_dict(),
            "retirement_phase": "verified",
            "recovery_executed": recovery,
            "lifecycle_state": result.plan_record.lifecycle_state,
            "retired_record_revision": result.plan_record.revision,
            "terminal_body_digest": result.plan_record.authority.source_digest,
        }
        entry = result.journal_entry
        try:
            _, updated = self.journal_store.cas_update_evidence(
                entry.record.journal_id,
                entry.journal_revision,
                evidence=evidence,
            )
        except StaleJournalRevisionError:
            latest = self.journal_store.get(entry.record.journal_id)
            if latest is None or any(latest.record.evidence.get(key) != value for key, value in evidence.items()):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "JOURNAL_TERMINAL_EVIDENCE_CONFLICT",
                    "close terminal evidence changed before recovery could be recorded",
                    journal_entry=latest or entry,
                    plan_record=result.plan_record,
                )
            updated = latest
        except JournalPersistenceFailureError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "JOURNAL_TERMINAL_EVIDENCE_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=result.plan_record,
            )
        return replace(result, journal_entry=updated, journal_id=updated.record.journal_id)

    def _resume(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        *,
        candidate_body: str | None,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None,
        source_binding: PlanAuthorityBinding | None,
    ) -> LocalPlanLifecycleResult:
        target_adapter = LocalPlanAuthorityAdapter(
            request.destination,
            allow_existing_replacement=(
                entry.record.evidence.get("local_plan_lifecycle")
                in {
                    "local_plan_amendment",
                    "final_plan_body_materialization_recovery",
                    ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND,
                }
            ),
        )
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        state = entry.record.journal_state
        if state in _NONTERMINAL_STATES:
            recovered = RecoveryExecutor(self.journal_store, target_adapter).recover_one(
                entry.record.journal_id
            )
            if recovered is None:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "RECOVERY_NOT_APPLIED",
                    "nonterminal lifecycle entry was not recovered",
                    journal_entry=entry,
                    plan_record=current,
                )
            entry = recovered
            state = entry.record.journal_state
        stored_source = entry.record.evidence.get("source_binding")
        if isinstance(stored_source, dict):
            try:
                stored_binding = PlanAuthorityBinding.from_dict(stored_source)
            except Exception as exc:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "JOURNAL_CORRUPT",
                    str(exc),
                    journal_entry=entry,
                    plan_record=current,
                )
            if source_binding is None or not _source_binding_matches(stored_binding, source_binding):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "SOURCE_BINDING_MISMATCH",
                    "recovery source binding differs from the durable cutover binding",
                    journal_entry=entry,
                    plan_record=current,
                )
            if source_adapter is None:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "SOURCE_ADAPTER_REQUIRED",
                    "cutover recovery requires a fresh source read adapter",
                    journal_entry=entry,
                    plan_record=current,
                )
        if state in _FAILURE_STATES:
            code = str(entry.record.evidence.get("failure_error_code") or state.value)
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                code,
                "durable lifecycle attempt ended without a verified local effect",
                journal_entry=entry,
                plan_record=current,
            )
        if state not in _SUCCESS_STATES:
            return _failure(
                request,
                _phase_for_entry(entry),
                "RECOVERY_REQUIRES_INTERVENTION",
                "journal outcome remains unresolved; blind retry is denied",
                journal_entry=entry,
                plan_record=current,
            )
        candidate_digest = entry.record.candidate_raw_digest
        if candidate_digest is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                "verified lifecycle entry has no candidate digest",
                journal_entry=entry,
                plan_record=current,
            )
        return self._complete(
            request,
            entry,
            candidate_digest=candidate_digest,
            target_adapter=target_adapter,
            source_adapter=source_adapter,
            source_binding=source_binding,
        )

    def _after_materialization(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        *,
        candidate_body: str,
        target_adapter: LocalPlanAuthorityAdapter,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None,
        source_binding: PlanAuthorityBinding | None,
        source_revision: str | int | None,
        source_digest: str | None,
        source_body: str | None,
    ) -> LocalPlanLifecycleResult:
        state = entry.record.journal_state
        if state in _NONTERMINAL_STATES:
            return _failure(
                request,
                _phase_for_entry(entry),
                "RECOVERY_REQUIRED",
                "local authority outcome is not terminal; no blind retry is permitted",
                journal_entry=entry,
            )
        if state in _FAILURE_STATES:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                str(entry.record.evidence.get("failure_error_code") or state.value),
                "local authority materialization had no verified effect",
                journal_entry=entry,
            )
        if state not in _SUCCESS_STATES:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "JOURNAL_STATE_UNKNOWN",
                "unsupported durable lifecycle journal state",
                journal_entry=entry,
            )
        try:
            observed_revision, observed_digest, observed_body = target_adapter.verify(
                request.destination.expected_ref
            )
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "LOCAL_READBACK_UNKNOWN",
                str(exc),
                journal_entry=entry,
            )
        candidate_digest = _digest(candidate_body)
        if observed_digest != candidate_digest or observed_body != candidate_body:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "LOCAL_READBACK_MISMATCH",
                "local authority bytes did not match the exact candidate",
                journal_entry=entry,
            )
        if source_adapter is not None and source_binding is not None:
            if source_body is None or source_digest is None:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "SOURCE_SNAPSHOT_MISSING",
                    "cutover source snapshot is incomplete",
                    journal_entry=entry,
                )
            if not _source_snapshot_matches(
                source_adapter,
                _source_target(request),
                expected_revision=source_revision,
                expected_digest=source_digest,
                expected_body_digest=_digest(source_body),
            ):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "SOURCE_CHANGED_AFTER_MATERIALIZATION",
                    "old authority changed before the binding CAS; local authority remains unbound",
                    journal_entry=entry,
                )
        return self._complete(
            request,
            entry,
            candidate_digest=candidate_digest,
            target_adapter=target_adapter,
            source_adapter=source_adapter,
            source_binding=source_binding,
            observed_revision=observed_revision,
            observed_digest=observed_digest,
        )

    def _complete(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        *,
        candidate_digest: str,
        target_adapter: LocalPlanAuthorityAdapter,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None,
        source_binding: PlanAuthorityBinding | None,
        observed_revision: str | int | None = None,
        observed_digest: str | None = None,
    ) -> LocalPlanLifecycleResult:
        if observed_digest is None:
            try:
                observed_revision, observed_digest, observed_body = target_adapter.verify(
                    request.destination.expected_ref
                )
            except Exception as exc:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.UNKNOWN,
                    "LOCAL_READBACK_UNKNOWN",
                    str(exc),
                    journal_entry=entry,
                )
            if _digest(observed_body) != candidate_digest or observed_digest != candidate_digest:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "LOCAL_READBACK_MISMATCH",
                    "local authority did not verify against the durable candidate digest",
                    journal_entry=entry,
                )
        desired = PlanAuthorityBinding(
            plan_id=request.plan_id,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=request.destination.authority_ref,
            source_revision=observed_revision,
            source_digest=observed_digest,
        )
        kind = entry.record.evidence.get("local_plan_lifecycle")
        current = self.governance_store.get_plan(request.project_id, request.plan_id)
        if kind == "local_plan_init":
            return self._complete_local_init(request, entry, current, desired)
        if kind == "local_plan_amendment":
            return self._complete_local_amendment(request, entry, current, desired)
        if kind == "final_plan_body_materialization_recovery":
            return self._complete_local_body_recovery(request, entry, current, desired)
        if kind == ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND:
            return self._complete_active_orphan_body_recovery(
                request, entry, current, desired
            )
        if kind != "authority_cutover":
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                "unknown local lifecycle operation",
                journal_entry=entry,
                plan_record=current,
            )
        if source_binding is None or source_adapter is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "SOURCE_ADAPTER_REQUIRED",
                "authority cutover completion requires the old authority read boundary",
                journal_entry=entry,
                plan_record=current,
            )
        source_revision = entry.record.evidence.get("source_observed_revision")
        source_digest = entry.record.evidence.get("source_observed_raw_digest")
        if not _source_snapshot_matches(
            source_adapter,
            _source_target(request),
            expected_revision=source_revision,
            expected_digest=str(source_digest),
            expected_body_digest=candidate_digest,
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "SOURCE_CHANGED_BEFORE_BINDING_SWITCH",
                "old authority no longer matches the durable cutover candidate",
                journal_entry=entry,
                plan_record=current,
            )
        stored_source = entry.record.evidence.get("source_binding")
        if not isinstance(stored_source, dict):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                "cutover journal has no source binding",
                journal_entry=entry,
                plan_record=current,
            )
        expected_source = PlanAuthorityBinding.from_dict(stored_source)
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_MISSING",
                "cutover cannot switch a missing Project Governance Plan record",
                journal_entry=entry,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "cutover cannot switch a retired Plan",
                journal_entry=entry,
                plan_record=current,
            )
        if _binding_identity(current.authority) == _binding_identity(desired):
            if not _source_binding_matches(current.authority, desired):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "TARGET_BINDING_MISMATCH",
                    "Plan is locally bound to different local authority facts",
                    journal_entry=entry,
                    plan_record=current,
                )
            return self._mark_terminal_evidence(
                request, _success(request, journal_entry=entry, plan_record=current)
            )
        if not _source_binding_matches(current.authority, expected_source):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AUTHORITY_SWITCH_CONFLICT",
                "current Plan authority is neither the expected old source nor the exact local target",
                journal_entry=entry,
                plan_record=current,
            )
        if current.revision != entry.record.subject_expected_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_PLAN_REVISION",
                "Plan binding revision changed while local materialization was in flight",
                journal_entry=entry,
                plan_record=current,
            )
        try:
            switched = self.governance_store.compare_and_swap_plan(
                request.project_id,
                request.plan_id,
                current.revision,
                authority=desired,
            )
        except StalePlanRevisionError:
            latest = self.governance_store.get_plan(request.project_id, request.plan_id)
            if latest is not None and _binding_identity(latest.authority) == _binding_identity(desired) and _source_binding_matches(latest.authority, desired):
                return self._mark_terminal_evidence(
                    request, _success(request, journal_entry=entry, plan_record=latest)
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AUTHORITY_SWITCH_CONFLICT",
                "binding CAS lost and the observed binding is not the exact local target",
                journal_entry=entry,
                plan_record=latest,
            )
        except ProjectGovernancePersistenceError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "AUTHORITY_SWITCH_PERSISTENCE_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=current,
            )
        verified = self.governance_store.get_plan(request.project_id, request.plan_id)
        if verified is None or verified.revision != switched.revision or not _binding_identity(verified.authority) == _binding_identity(desired) or not _source_binding_matches(verified.authority, desired):
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "AUTHORITY_SWITCH_READBACK_FAILED",
                "binding CAS did not verify the exact local authority after write",
                journal_entry=entry,
                plan_record=verified or switched,
            )
        return self._mark_terminal_evidence(
            request, _success(request, journal_entry=entry, plan_record=verified)
        )

    def _complete_local_amendment(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        current: ProjectPlanRecord | None,
        desired: PlanAuthorityBinding,
    ) -> LocalPlanLifecycleResult:
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_MISSING",
                "local Plan amendment cannot update a missing Project Governance Plan record",
                journal_entry=entry,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "local Plan amendment cannot update a retired Plan",
                journal_entry=entry,
                plan_record=current,
            )
        try:
            expected_data = entry.record.evidence["amendment_precondition_binding"]
            expected = PlanAuthorityBinding.from_dict(expected_data)
        except (KeyError, TypeError, ValueError) as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                f"local amendment journal has an invalid authority precondition: {exc}",
                journal_entry=entry,
                plan_record=current,
            )
        if _source_binding_matches(current.authority, desired):
            return self._mark_terminal_evidence(
                request, _success(request, journal_entry=entry, plan_record=current)
            )
        if current.revision != entry.record.subject_expected_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_PLAN_REVISION",
                "Plan binding revision changed while local amendment was in flight",
                journal_entry=entry,
                plan_record=current,
            )
        if not _source_binding_matches(current.authority, expected):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_BINDING_CONFLICT",
                "current Plan authority is neither the amendment precondition nor the exact amended target",
                journal_entry=entry,
                plan_record=current,
            )
        if (
            current.authority.source_revision != entry.record.authority_source_revision
            or current.authority.source_digest != entry.record.authority_observed_raw_digest
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "STALE_AUTHORITY",
                "current Plan authority facts changed before amendment CAS",
                journal_entry=entry,
                plan_record=current,
            )
        try:
            amended = self.governance_store.compare_and_swap_plan(
                request.project_id,
                request.plan_id,
                current.revision,
                authority=desired,
            )
        except StalePlanRevisionError:
            latest = self.governance_store.get_plan(request.project_id, request.plan_id)
            if latest is not None and _source_binding_matches(latest.authority, desired):
                return self._mark_terminal_evidence(
                    request, _success(request, journal_entry=entry, plan_record=latest)
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AMENDMENT_BINDING_CONFLICT",
                "amendment CAS lost and the observed binding is not the exact amended target",
                journal_entry=entry,
                plan_record=latest,
            )
        except ProjectGovernancePersistenceError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "AMENDMENT_BINDING_PERSISTENCE_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=current,
            )
        verified = self.governance_store.get_plan(request.project_id, request.plan_id)
        if (
            verified is None
            or verified.revision != amended.revision
            or not _source_binding_matches(verified.authority, desired)
            or verified.lifecycle_state != PLAN_LIFECYCLE_ACTIVE
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "AMENDMENT_BINDING_READBACK_FAILED",
                "amended Plan binding did not verify after CAS",
                journal_entry=entry,
                plan_record=verified or amended,
            )
        return self._mark_terminal_evidence(
            request, _success(request, journal_entry=entry, plan_record=verified)
        )

    def _complete_local_body_recovery(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        current: ProjectPlanRecord | None,
        desired: PlanAuthorityBinding,
    ) -> LocalPlanLifecycleResult:
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_MISSING",
                "retired Plan recovery cannot update a missing Store record",
                journal_entry=entry,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_RETIRED:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Plan recovery cannot mutate an active or unknown lifecycle",
                journal_entry=entry,
                plan_record=current,
            )
        if _source_binding_matches(current.authority, desired):
            return self._mark_terminal_evidence(
                request, _success(request, journal_entry=entry, plan_record=current)
            )
        try:
            expected = PlanAuthorityBinding.from_dict(
                entry.record.evidence["recovery_precondition_binding"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                f"retired Plan recovery precondition is invalid: {exc}",
                journal_entry=entry,
                plan_record=current,
            )
        if not _source_binding_matches(current.authority, expected):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Store authority changed from the trusted pre-close binding",
                journal_entry=entry,
                plan_record=current,
            )
        if current.revision != entry.record.subject_expected_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Store revision changed before recovery CAS",
                journal_entry=entry,
                plan_record=current,
            )
        try:
            updated = self.governance_store.compare_and_swap_plan(
                request.project_id,
                request.plan_id,
                current.revision,
                authority=desired,
            )
        except StalePlanRevisionError:
            latest = self.governance_store.get_plan(request.project_id, request.plan_id)
            if latest is not None and latest.lifecycle_state == PLAN_LIFECYCLE_RETIRED and _source_binding_matches(latest.authority, desired):
                return self._mark_terminal_evidence(
                    request, _success(request, journal_entry=entry, plan_record=latest)
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "BLOCKED_FINAL_BODY_RECOVERY_CONFLICT",
                "retired Store authority CAS lost to a conflicting update",
                journal_entry=entry,
                plan_record=latest,
            )
        except ProjectGovernancePersistenceError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "GOVERNANCE_PERSISTENCE_FAILURE",
                str(exc),
                journal_entry=entry,
                plan_record=current,
            )
        verified = self.governance_store.get_plan(request.project_id, request.plan_id)
        if (
            verified is None
            or verified.lifecycle_state != PLAN_LIFECYCLE_RETIRED
            or verified.revision != updated.revision
            or not _source_binding_matches(verified.authority, desired)
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "FINAL_BODY_STORE_CONVERGENCE_FAILED",
                "retired Store authority did not verify after recovery CAS",
                journal_entry=entry,
                plan_record=verified or updated,
            )
        return self._mark_terminal_evidence(
            request, _success(request, journal_entry=entry, plan_record=verified)
        )

    def _complete_active_orphan_body_recovery(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        current: ProjectPlanRecord | None,
        desired: PlanAuthorityBinding,
    ) -> LocalPlanLifecycleResult:
        if current is None:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_RECORD_MISSING",
                "active orphan-body recovery cannot update a missing Store record",
                journal_entry=entry,
            )
        if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "PLAN_NOT_ACTIVE",
                "active orphan-body recovery cannot mutate a non-active Plan",
                journal_entry=entry,
                plan_record=current,
            )
        evidence = entry.record.evidence
        try:
            expected = PlanAuthorityBinding.from_dict(
                evidence["recovery_precondition_binding"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "JOURNAL_CORRUPT",
                f"active orphan-body recovery predecessor binding is invalid: {exc}",
                journal_entry=entry,
                plan_record=current,
            )
        orphan_digest = entry.record.candidate_raw_digest
        orphan_revision = entry.record.authority_source_revision
        if (
            evidence.get("local_plan_lifecycle")
            != ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND
            or evidence.get("event") != ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND
            or evidence.get("recovery_reason")
            != ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_REASON
            or evidence.get("project_id") != request.project_id
            or evidence.get("plan_id") != request.plan_id
            or evidence.get("predecessor_store_revision")
            != entry.record.subject_expected_revision
            or request.expected_plan_revision != entry.record.subject_expected_revision
            or evidence.get("orphan_observed_revision") != orphan_revision
            or evidence.get("orphan_observed_raw_digest") != orphan_digest
            or entry.record.authority_observed_raw_digest != orphan_digest
            or desired.source_revision != orphan_revision
            or desired.source_digest != orphan_digest
            or expected.source_digest == orphan_digest
            or _binding_identity(expected) != _binding_identity(desired)
            or _source_binding_matches(expected, desired)
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ORPHAN_RECOVERY_JOURNAL_IDENTITY_MISMATCH",
                "durable recovery journal does not bind the exact predecessor and orphan body",
                journal_entry=entry,
                plan_record=current,
            )
        if _source_binding_matches(current.authority, desired):
            return self._mark_terminal_evidence(
                request, _success(request, journal_entry=entry, plan_record=current)
            )
        if current.revision != entry.record.subject_expected_revision:
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ACTIVE_ORPHAN_STORE_REVISION_CONFLICT",
                "Project Governance Store revision changed before orphan recovery CAS",
                journal_entry=entry,
                plan_record=current,
            )
        if not _source_binding_matches(current.authority, expected):
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ACTIVE_ORPHAN_STORE_BINDING_CONFLICT",
                "Project Governance Store no longer matches the exact predecessor binding",
                journal_entry=entry,
                plan_record=current,
            )

        try:
            updated = self.governance_store.compare_and_swap_plan(
                request.project_id,
                request.plan_id,
                current.revision,
                authority=desired,
            )
        except StalePlanRevisionError:
            latest = self.governance_store.get_plan(request.project_id, request.plan_id)
            if (
                latest is not None
                and latest.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
                and _source_binding_matches(latest.authority, desired)
            ):
                return self._mark_terminal_evidence(
                    request, _success(request, journal_entry=entry, plan_record=latest)
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "ACTIVE_ORPHAN_STORE_CAS_CONFLICT",
                "Store authority CAS lost to a conflicting update",
                journal_entry=entry,
                plan_record=latest,
            )
        except ProjectGovernancePersistenceError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ACTIVE_ORPHAN_STORE_CAS_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=current,
            )

        try:
            verified = self.governance_store.get_plan(request.project_id, request.plan_id)
        except Exception as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ACTIVE_ORPHAN_STORE_READBACK_UNKNOWN",
                str(exc),
                journal_entry=entry,
                plan_record=updated,
            )
        if (
            verified is None
            or verified.lifecycle_state != PLAN_LIFECYCLE_ACTIVE
            or verified.revision != updated.revision
            or not _source_binding_matches(verified.authority, desired)
        ):
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "ACTIVE_ORPHAN_STORE_READBACK_FAILED",
                "Store authority did not verify the exact recovered local binding",
                journal_entry=entry,
                plan_record=verified or updated,
            )
        return self._mark_terminal_evidence(
            request, _success(request, journal_entry=entry, plan_record=verified)
        )

    def _complete_local_init(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        current: ProjectPlanRecord | None,
        desired: PlanAuthorityBinding,
    ) -> LocalPlanLifecycleResult:
        if current is not None:
            if current.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "PLAN_NOT_ACTIVE",
                    "a retired Plan cannot be implicitly resurrected",
                    journal_entry=entry,
                    plan_record=current,
                )
            if _binding_identity(current.authority) != _binding_identity(desired) or not _source_binding_matches(current.authority, desired):
                return _failure(
                    request,
                    LocalPlanLifecyclePhase.FAILED_CLOSED,
                    "AUTHORITY_ALREADY_BOUND",
                    "existing Plan authority is not the exact local target",
                    journal_entry=entry,
                    plan_record=current,
                )
            return self._mark_terminal_evidence(
                request, _success(request, journal_entry=entry, plan_record=current)
            )
        candidate = ProjectPlanRecord(
            project_id=request.project_id,
            plan_id=request.plan_id,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=desired,
        )
        try:
            stored = self.governance_store.put_plan(candidate)
        except PlanRecordAlreadyExistsError:
            raced = self.governance_store.get_plan(request.project_id, request.plan_id)
            if raced is not None and raced.lifecycle_state == PLAN_LIFECYCLE_ACTIVE and _binding_identity(raced.authority) == _binding_identity(desired) and _source_binding_matches(raced.authority, desired):
                return self._mark_terminal_evidence(
                    request, _success(request, journal_entry=entry, plan_record=raced)
                )
            return _failure(
                request,
                LocalPlanLifecyclePhase.FAILED_CLOSED,
                "AUTHORITY_SWITCH_CONFLICT",
                "another Plan record won creation with a different authority",
                journal_entry=entry,
                plan_record=raced,
            )
        except ProjectGovernancePersistenceError as exc:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "PLAN_RECORD_PERSISTENCE_UNKNOWN",
                str(exc),
                journal_entry=entry,
            )
        verified = self.governance_store.get_plan(request.project_id, request.plan_id)
        if verified is None or verified != stored:
            return _failure(
                request,
                LocalPlanLifecyclePhase.UNKNOWN,
                "PLAN_RECORD_READBACK_FAILED",
                "new local Plan record did not verify after durable write",
                journal_entry=entry,
                plan_record=verified or stored,
            )
        return self._mark_terminal_evidence(
            request, _success(request, journal_entry=entry, plan_record=verified)
        )


def initialize_local_plan(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    candidate_body: str,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).initialize(
        request, candidate_body
    )


def cutover_plan_authority(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    source_binding: PlanAuthorityBinding,
    source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).cutover(
        request, source_binding, source_adapter
    )


def recover_local_plan_lifecycle(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    source_binding: PlanAuthorityBinding | None = None,
    source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None = None,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).recover(
        request, source_binding=source_binding, source_adapter=source_adapter
    )


def retire_local_plan(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    project_id: str,
    plan_id: str,
    expected_revision: int,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).retire(
        project_id=project_id,
        plan_id=plan_id,
        expected_revision=expected_revision,
    )


def close_local_plan(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    candidate_body: str,
    expected_authority: PlanAuthorityBinding,
    closure_evidence: PlanCloseMaterializationEvidence,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).close_local_plan(
        request,
        candidate_body,
        expected_authority=expected_authority,
        closure_evidence=closure_evidence,
    )


def recover_retired_final_plan_body(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    candidate_body: str,
    expected_authority: PlanAuthorityBinding,
    closure_evidence: PlanCloseMaterializationEvidence,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(governance_store, journal_store).recover_retired_final_body(
        request,
        candidate_body,
        expected_authority=expected_authority,
        closure_evidence=closure_evidence,
    )


def recover_active_local_plan_orphan_body(
    *,
    governance_store: ProjectGovernanceStore,
    journal_store: DurableJournalStore,
    request: LocalPlanLifecycleRequest,
    expected_authority: PlanAuthorityBinding,
    expected_orphan_digest: str,
    recovery_reason: str,
) -> LocalPlanLifecycleResult:
    return LocalPlanLifecycleCoordinator(
        governance_store, journal_store
    ).recover_active_local_plan_orphan_body(
        request,
        expected_authority=expected_authority,
        expected_orphan_digest=expected_orphan_digest,
        recovery_reason=recovery_reason,
    )


__all__ = [
    "LOCAL_PLAN_LIFECYCLE_IMPLEMENTED",
    "LOCAL_PLAN_AUTHORITY_CUTOVER_IMPLEMENTED",
    "LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_PORT",
    "LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_JOURNAL",
    "LOCAL_PLAN_AUTHORITY_CUTOVER_USES_PROJECT_STORE_CAS",
    "SECOND_PLAN_MUTATION_PROTOCOL_CREATED",
    "SECOND_TRANSACTION_FRAMEWORK_CREATED",
    "SILENT_DUAL_AUTHORITY_ALLOWED",
    "POST_CUTOVER_FALLBACK_TO_OLD_AUTHORITY_ALLOWED",
    "CUTOVER_UNKNOWN_OUTCOME_FAILS_CLOSED",
    "LOCAL_PLAN_RETIREMENT_USES_PROJECT_GOVERNANCE_CAS",
    "LOCAL_PLAN_RETIREMENT_IS_AUTHORITY_DOCUMENT_MUTATION",
    "LOCAL_PLAN_CLOSE_MATERIALIZATION_IMPLEMENTED",
    "LOCAL_PLAN_CLOSE_MATERIALIZATION_USES_EXISTING_AMENDMENT",
    "LOCAL_PLAN_CLOSE_MATERIALIZATION_BEFORE_RETIREMENT",
    "LOCAL_PLAN_CLOSE_APPLIED_IMPLIES_TERMINAL_BODY_CONVERGED",
    "LOCAL_PLAN_RETIRED_FINAL_BODY_RECOVERY_IMPLEMENTED",
    "PLAN_CANDIDATE_SEMANTIC_ADMISSION_IMPLEMENTED",
    "PLAN_CANDIDATE_PREFLIGHT_READ_ONLY",
    "PLAN_CANDIDATE_PREFLIGHT_REUSES_MUTATION_VALIDATOR",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_KIND",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_REASON",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_IMPLEMENTED",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_JOURNAL",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_EXECUTOR",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_EXISTING_ADAPTER",
    "ACTIVE_LOCAL_PLAN_ORPHAN_BODY_RECOVERY_USES_PROJECT_STORE_CAS",
    "SECOND_GOVERNANCE_STORE_CREATED",
    "NEW_AGENT_OPERATION_CREATED",
    "NEW_GOVERNANCE_PLAN_MODE_CREATED",
    "MODEL_FACING_RECOVERY_CAPABILITY_CREATED",
    "NEW_JOURNAL_STATE_CREATED",
    "NORMAL_RETIRED_PLAN_MUTATION_DENIED",
    "LocalPlanLifecyclePhase",
    "LocalPlanLifecycleError",
    "PlanCandidateKind",
    "LocalPlanCandidatePreflight",
    "preflight_local_plan_candidate",
    "LocalPlanLifecycleRequest",
    "PlanCloseMaterializationEvidence",
    "LocalPlanLifecycleResult",
    "LocalPlanLifecycleCoordinator",
    "next_plan_revision",
    "project_terminal_plan_body",
    "initialize_local_plan",
    "cutover_plan_authority",
    "recover_local_plan_lifecycle",
    "retire_local_plan",
    "close_local_plan",
    "recover_retired_final_plan_body",
    "recover_active_local_plan_orphan_body",
]
