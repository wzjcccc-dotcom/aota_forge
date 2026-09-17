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
from dataclasses import dataclass, field, replace
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
    portable_plan_digest,
)
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    PlanRecordAlreadyExistsError,
    PlanRecordNotFoundError,
    ProjectGovernancePersistenceError,
    ProjectGovernanceStore,
    ProjectPlanRecord,
    StalePlanRevisionError,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# W1 implementation and negative-ownership markers.
LOCAL_PLAN_LIFECYCLE_IMPLEMENTED = True
LOCAL_PLAN_AUTHORITY_CUTOVER_IMPLEMENTED = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_PORT = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_EXISTING_JOURNAL = True
LOCAL_PLAN_AUTHORITY_CUTOVER_USES_PROJECT_STORE_CAS = True
SECOND_PLAN_MUTATION_PROTOCOL_CREATED = False
SECOND_TRANSACTION_FRAMEWORK_CREATED = False
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


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        if evidence.get("local_plan_lifecycle") not in {"local_plan_init", "authority_cutover"}:
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
        normalized_digest = _normalized_digest(body)
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

    def _resume(
        self,
        request: LocalPlanLifecycleRequest,
        entry: DurableJournalEntry,
        *,
        candidate_body: str | None,
        source_adapter: PlanAuthorityReadAdapter | PlanAuthorityMutationPort | None,
        source_binding: PlanAuthorityBinding | None,
    ) -> LocalPlanLifecycleResult:
        target_adapter = LocalPlanAuthorityAdapter(request.destination)
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
    "LocalPlanLifecyclePhase",
    "LocalPlanLifecycleError",
    "LocalPlanLifecycleRequest",
    "LocalPlanLifecycleResult",
    "LocalPlanLifecycleCoordinator",
    "initialize_local_plan",
    "cutover_plan_authority",
    "recover_local_plan_lifecycle",
    "retire_local_plan",
]
