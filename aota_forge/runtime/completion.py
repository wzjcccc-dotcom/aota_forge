"""M2/W3 durable completion coordination: recovery, pending delivery, ACK, bounded concurrency.

One narrow, bounded runtime coordinator over the M2/W1 durable execution seam
(``ExecutionStateStore`` + ``ExecutionDispatcher`` durable mode) and the M2/W2
recoverable Hermes runtime boundary. It implements runtime mechanics only:

- ``recover_once()``  — bounded startup reconciliation pass over
  ``scan_requiring_recovery()`` records (nonterminal executions, expired
  delivery claims, terminal-but-unacknowledged records).
- ``deliver_pending_once()`` — bounded CARD-first delivery pass with
  single-flight CAS claims, stale-claim reclaim, retryable release, and
  reconciliation-bound ACK.
- ``admit_dispatch()`` — bounded concurrency admission enforced against the
  durable active-set (survives restart), with UNKNOWN and unresolved PREPARED
  counted as active conservatively.

Hard ordering (M2/W3 §19): a delivery attempt may only happen after the
terminal CanonicalResult AND the Worker Result CARD are durable on the same
record. This module never delivers from in-memory truth.

Authority boundaries (wzjcccc-dotcom/aota-hermes-tools#36):

- The AF ``DurableExecutionRecord`` is canonical runtime truth. Hermes'
  locator and ``async_delegations`` ledger are executor-private mechanical
  evidence only (HERMES_LOCATOR_IS_AF_AUTHORITY=no,
  HERMES_LEDGER_IS_AF_AUTHORITY=no).
- Hermes process exit 0 is NEVER an ACK (§29): ACK requires the exact origin
  task-main session to reconcile this completion identity and return the
  bounded ``AOTA_COMPLETION_ACK_V1`` binding of ``canonical_task_id`` +
  Worker Result CARD digest (§29/§30).
- Delivery guarantee is at-least-once (§31): a crash between task-main
  reconciliation and the ACK CAS may cause a truthful redelivery; the
  reconciliation identity (canonical_task_id + card_digest) is idempotent.
- No exactly-once dispatch claim survives here either (§18): the W1 PREPARED
  crash window remains documented. Without a deterministic W2 prepared-run
  recovery, W3 policy is PREPARED_WITHOUT_LOCATOR -> durable UNKNOWN ->
  never blindly redispatched (§17). No heuristic "newest Hermes run/session"
  scan exists in this module or may be added.
- Terminal/nonterminal/delivery state vocabularies and CAS rules are W1's;
  this module performs no store-side invariant work of its own beyond issuing
  bounded CAS updates the W1 contract already allows.
- No WorkflowEngine, no CompletionInbox framework, no event bus, no
  scheduler platform, and NO permanent background daemon: every operation is
  an explicit bounded ``*_once()`` pass (M3 autonomy is out of scope).

The delivery transport is an injected executor-neutral seam
(:class:`CompletionDeliveryTransport`); this module has zero Hermes imports.
The Hermes implementation lives in ``aota_forge.adapters.hermes.delivery``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

from aota_forge.core.execution.dispatcher import (
    AdapterProtocolError,
    DispatchOutcomeUnresolvedError,
    ExecutionDispatcher,
)
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionStateStore,
    StaleExecutionRevisionError,
    card_digest_for,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.work_plane.result_card import project_worker_result_card
from aota_forge.work_plane.roles import parse_agent_work_role

# ---------------------------------------------------------------------------
# Governance markers
# ---------------------------------------------------------------------------

RECOVERY_PASS_IMPLEMENTED = True
DELIVERY_COORDINATOR_IMPLEMENTED = True
ACK_SEMANTICS_IMPLEMENTED = True
BOUNDED_CONCURRENCY_IMPLEMENTED = True
BACKGROUND_AUTONOMOUS_LOOP_IMPLEMENTED = False
WORKFLOW_ENGINE_CREATED = False
COMPLETION_INBOX_FRAMEWORK_CREATED = False
PERSISTENT_EVENT_BUS_CREATED = False
SCHEDULER_PLATFORM_CREATED = False
TERMINAL_RESULT_PERSISTED_BEFORE_DELIVERY = True
CARD_PERSISTED_BEFORE_DELIVERY = True
ACK_AFTER_RECONCILIATION_ONLY = True
ACK_IDENTITY_BOUND = True
DELIVERY_GUARANTEE = "at-least-once"
EXACTLY_ONCE_DELIVERY_CLAIMED = False
EXACTLY_ONCE_DISPATCH_CLAIMED = False
POST_ACK_REDELIVERY = False
BLIND_REDISPATCH_ON_UNRESOLVED = False
FABRICATED_COMPLETION_ON_UNRESOLVED = False
UNKNOWN_COUNTS_ACTIVE = True
PREPARED_UNRESOLVED_COUNTS_ACTIVE = True
HERMES_LOCATOR_IS_AF_AUTHORITY = False
HERMES_LEDGER_IS_AF_AUTHORITY = False
AF_DURABLE_RECORD_IS_CANONICAL_RUNTIME_TRUTH = True

# Bounded mechanical defaults (M2/W3 §24). Source review found no better AF
# policy seam; these mirror the Hermes runtime's bounded lease posture (claim
# TTL ~300s) and a bounded delivery attempt cap (~8). They are delivery
# mechanics ONLY and do not make the Hermes ledger authoritative.
DELIVERY_CLAIM_TTL_SECONDS = 300.0
DELIVERY_ATTEMPT_CAP = 8
MAX_DELIVERIES_PER_PASS = 8
MAX_RECOVERY_OBSERVATIONS = 256

# ---------------------------------------------------------------------------
# Typed bounded outcome vocabularies (delivery mechanics only)
# ---------------------------------------------------------------------------


class DeliveryTransportOutcome(str, Enum):
    """Typed mechanical outcome of one delivery attempt from the transport.

    Mirrors the W2 exact-session re-entry outcome classes without importing
    the Hermes layer: ``completed`` means the bounded turn ran (NOT an ACK),
    ``retryable`` keeps delivery pending, ``not_found`` is the hard-missing
    exact session, ``failed``/``unknown`` stay conservative.
    """

    COMPLETED = "completed"
    RETRYABLE = "retryable"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeliveryAttemptEvidence:
    """Mechanical evidence returned by a transport delivery attempt."""

    outcome: DeliveryTransportOutcome
    response_text: str | None = None
    detail: str | None = None


class CompletionDeliveryTransport(Protocol):
    """Executor-neutral completion-delivery seam implemented by W3 adapters.

    ``deliver`` performs ONE bounded re-entry attempt into the exact trusted
    session identified by ``session_ref`` with the bounded ``envelope`` text
    and returns mechanical evidence only. It must never fabricate an ACK.
    """

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence: ...


# ---------------------------------------------------------------------------
# Errors (bounded; reuse the existing .code conventions)
# ---------------------------------------------------------------------------


class CompletionCoordinatorError(Exception):
    """Base error for the W3 completion coordinator."""

    code = "COMPLETION_COORDINATOR_ERROR"


class CompletionRecoveryRequiredError(CompletionCoordinatorError):
    """New dispatch admission is gated until the bounded recovery pass ran."""

    code = "RECOVERY_REQUIRED_BEFORE_ADMISSION"


class CompletionAdmissionScopeError(CompletionCoordinatorError):
    """Concurrency accounting cannot resolve an unambiguous scope (fail closed)."""

    code = "ADMISSION_SCOPE_UNRESOLVED"


# ---------------------------------------------------------------------------
# CARD-first delivery envelope + reconciliation ACK contract (§27-§30)
# ---------------------------------------------------------------------------

COMPLETION_ENVELOPE_HEADER = "AOTA_WORKER_COMPLETION_V1"
COMPLETION_ACK_TOKEN = "AOTA_COMPLETION_ACK_V1"
_MAX_ENVELOPE_BYTES = 64 * 1024

_ACK_LINE_RE = re.compile(
    r"^[ \t]*AOTA_COMPLETION_ACK_V1[ \t]+canonical_task_id=(\S+)[ \t]+card_digest=(\S+)[ \t]*$",
    re.MULTILINE,
)


def build_completion_envelope(record: DurableExecutionRecord) -> str:
    """Deterministic bounded CARD-first transport envelope (delivery mechanics only).

    Contains ONLY: canonical_task_id, the canonical WorkerResultCard payload,
    its digest, and the CARD's result_handoff_ref — plus the ACK instruction.
    Raw Worker stdout/stderr are deliberately NOT injected (task-main hydrates
    rich detail via the governed result handoff reference instead).
    """
    if record.terminal_result is None or record.worker_result_card is None:
        raise CompletionCoordinatorError(
            "a completion envelope requires durable terminal result + CARD truth (§19)"
        )
    if record.worker_result_card_digest is None:
        raise CompletionCoordinatorError("durable CARD requires its digest")
    digest = record.worker_result_card_digest
    if card_digest_for(dict(record.worker_result_card)) != digest:
        raise CompletionCoordinatorError(
            "durable CARD digest fails verification; refusing to deliver corrupted truth"
        )
    handoff = record.worker_result_card.get("result_handoff_ref")
    if not isinstance(handoff, Mapping) or not isinstance(handoff.get("ref"), str):
        raise CompletionCoordinatorError("durable CARD is missing its result_handoff_ref")
    card_json = json.dumps(dict(record.worker_result_card), sort_keys=True, separators=(",", ":"))
    lines = [
        COMPLETION_ENVELOPE_HEADER,
        f"canonical_task_id={record.canonical_task_id}",
        f"card_digest={digest}",
        f"result_handoff_ref={handoff['ref']}",
        (
            "AOTA reconciliation contract: read this Worker completion CARD, reconcile it "
            "against the governed result for this task identity, and reply with EXACTLY "
            "this single line and nothing else:"
        ),
        f"{COMPLETION_ACK_TOKEN} canonical_task_id={record.canonical_task_id} card_digest={digest}",
        "",
        card_json,
    ]
    envelope = "\n".join(lines)
    if len(envelope.encode("utf-8")) > _MAX_ENVELOPE_BYTES:
        raise CompletionCoordinatorError("completion envelope exceeds the bounded transport seam")
    return envelope


def parse_completion_ack(
    response_text: str | None,
    *,
    canonical_task_id: str,
    card_digest: str,
) -> bool:
    """True only when the response reconciles THIS exact completion identity.

    A missing, malformed, partial, or foreign (different task/digest) ack is
    False. Multiple contradictory ack lines are False. Hermes reentry exit 0
    alone is never an ACK (§29).
    """
    if not response_text:
        return False
    matches = _ACK_LINE_RE.findall(response_text)
    if not matches:
        return False
    return all(
        ack_task == canonical_task_id and ack_digest == card_digest for ack_task, ack_digest in matches
    )


# ---------------------------------------------------------------------------
# Bounded reports
# ---------------------------------------------------------------------------

RECOVER_RUNNING = "running"
RECOVER_TERMINAL_PERSISTED = "terminal_result_persisted"
RECOVER_TERMINAL_ALREADY_DURABLE = "terminal_already_durable"
RECOVER_UNKNOWN_PERSISTED = "unknown_persisted"
RECOVER_PREPARED_UNRESOLVED_UNKNOWN = "prepared_unresolved_unknown"
RECOVER_PROTOCOL_ERROR_PRESERVED = "protocol_error_preserved"
RECOVER_UNRECOVERABLE = "unrecoverable"

DELIVER_ACKNOWLEDGED = "acknowledged"
DELIVER_RELEASED_RETRYABLE = "released_retryable"
DELIVER_RELEASED_ACK_NOT_PROVEN = "released_ack_not_proven"
DELIVER_RELEASED_TRANSPORT_ERROR = "released_transport_error"
DELIVER_DROPPED_SESSION_MISSING = "dropped_session_missing"
DELIVER_DROPPED_ORIGIN_MISSING = "dropped_origin_missing"
DELIVER_DROPPED_ATTEMPT_CAP = "dropped_attempt_cap_exhausted"
DELIVER_CLAIM_RACE_LOST = "claim_race_lost"
DELIVER_TRUTH_NOT_DURABLE = "skipped_truth_not_durable"
DELIVER_SKIPPED_NO_TRANSPORT = "skipped_no_transport"


@dataclass
class RecoveryReport:
    observations: dict[str, str] = field(default_factory=dict)
    reclaimed_expired_claims: list[str] = field(default_factory=list)
    terminal_persisted: list[str] = field(default_factory=list)
    unknown_persisted: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for kind in self.observations.values():
            counts[kind] = counts.get(kind, 0) + 1
        counts["reclaimed_expired_claims"] = len(self.reclaimed_expired_claims)
        return counts


@dataclass
class DeliveryReport:
    outcomes: dict[str, str] = field(default_factory=dict)
    reclaimed_expired_claims: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AdmissionDecision:
    """Typed bounded refusal/retryable result for dispatch admission (§38)."""

    admitted: bool
    reason: str
    admission_scope: str | None = None
    active_count: int = 0
    limit: int | None = None

    @property
    def retryable(self) -> bool:
        return not self.admitted


# ---------------------------------------------------------------------------
# Time helpers (bounded ISO handling aligned with the W1 store vocabulary)
# ---------------------------------------------------------------------------


def _now_iso(at_wall: float) -> str:
    return datetime.fromtimestamp(at_wall, tz=timezone.utc).isoformat()


def _parse_iso(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


# ---------------------------------------------------------------------------
# Result governance for a durable terminal CanonicalResult (§16, no new ontology)
# ---------------------------------------------------------------------------


def governance_projection_for_result(result: CanonicalResult) -> ResultGovernanceProjection:
    """Project the EXISTING governance ontology over a CanonicalResult.

    Hermes exit code is not consulted as semantic authority: the mapping
    follows the canonical result fields exactly as
    ``work_plane.result_card._expected_outcome_from_canonical`` requires
    (success / failure / unknown), so the CARD agreement check holds.
    """
    if result.ok is True and result.status == "completed":
        return ResultGovernanceProjection.success()
    if result.status == "unknown" or result.canonical_task_state == CanonicalTaskState.UNKNOWN.value:
        error = dict(result.error) if isinstance(result.error, Mapping) else None
        return ResultGovernanceProjection.unknown(error=error)
    if isinstance(result.error, Mapping) and "code" in result.error and "message" in result.error:
        error = dict(result.error)
        error.setdefault("retryable", False)
        return ResultGovernanceProjection.failure(error)
    return ResultGovernanceProjection.failure(
        {
            "code": "EXECUTION_FAILED",
            "message": f"worker execution ended with canonical status {result.status!r}",
            "retryable": False,
        }
    )


def _work_role_from_admission_scope(scope: str | None) -> str | None:
    """Deterministic recovery of the trusted work-role part from "executor:role"."""
    if not scope or ":" not in scope:
        return None
    role = scope.rsplit(":", 1)[1].strip()
    if not role:
        return None
    try:
        parse_agent_work_role(role)
    except Exception:
        return None
    return role


def _terminal_summary(result: CanonicalResult) -> str:
    return f"durable terminal worker result: status={result.status} state={result.canonical_task_state}"


# ---------------------------------------------------------------------------
# The narrow coordinator
# ---------------------------------------------------------------------------


class DurableCompletionCoordinator:
    """Bounded recovery / delivery / ACK / admission mechanics over W1 state.

    The coordinator owns no durable state itself: every transition is a CAS
    against the shared ``ExecutionStateStore`` and every read comes from the
    store, so a fresh coordinator object in a fresh process behaves identically
    after restart. No background loop, no queue framework.
    """

    def __init__(
        self,
        *,
        dispatcher: ExecutionDispatcher,
        store: ExecutionStateStore,
        transport: CompletionDeliveryTransport | None = None,
        admission_limits: Mapping[str, int] | None = None,
        claim_owner_prefix: str = "af-runtime",
        claim_ttl_seconds: float = DELIVERY_CLAIM_TTL_SECONDS,
        delivery_attempt_cap: int = DELIVERY_ATTEMPT_CAP,
        max_deliveries_per_pass: int = MAX_DELIVERIES_PER_PASS,
        now_fn: Callable[[], float] = time.time,
        require_recovery_before_admission: bool = True,
    ) -> None:
        if not isinstance(dispatcher, ExecutionDispatcher):
            raise TypeError("dispatcher must be an ExecutionDispatcher")
        if not isinstance(store, ExecutionStateStore):
            raise TypeError("store must be an ExecutionStateStore")
        if dispatcher.state_store is not store:
            raise CompletionCoordinatorError(
                "the coordinator and the dispatcher must share the same ExecutionStateStore"
            )
        if transport is not None and not hasattr(transport, "deliver"):
            raise TypeError("transport must implement deliver(session_ref=, envelope=)")
        if type(delivery_attempt_cap) is not int or delivery_attempt_cap < 1:
            raise ValueError("delivery_attempt_cap must be an int >= 1")
        if claim_ttl_seconds <= 0:
            raise ValueError("claim_ttl_seconds must be positive")
        if max_deliveries_per_pass < 1:
            raise ValueError("max_deliveries_per_pass must be >= 1")
        for scope, limit in (admission_limits or {}).items():
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError("admission_limits keys must be non-empty strings")
            if type(limit) is not int or limit < 1:
                raise ValueError(f"admission limit for {scope!r} must be an int >= 1")
        self._dispatcher = dispatcher
        self._store = store
        self._transport = transport
        self._admission_limits = dict(admission_limits or {})
        self._claim_owner_prefix = claim_owner_prefix
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._delivery_attempt_cap = delivery_attempt_cap
        self._max_deliveries_per_pass = max_deliveries_per_pass
        self._now_fn = now_fn
        self._require_recovery_before_admission = require_recovery_before_admission
        self._recovered = False

    # -- properties -----------------------------------------------------------

    @property
    def store(self) -> ExecutionStateStore:
        return self._store

    @property
    def recovered(self) -> bool:
        return self._recovered

    # -- bounded startup recovery (§42/§43) ------------------------------------

    def recover_once(self) -> RecoveryReport:
        """One bounded recovery pass; never a loop, never a daemon.

        Ordering per §43: reconstruct nonterminal execution truth first
        (persisting newly observed terminal result/CARD), reclaim expired
        delivery claims, and only then is new-dispatch admission unlocked.
        """
        report = RecoveryReport()
        records = self._store.scan_requiring_recovery()[:MAX_RECOVERY_OBSERVATIONS]
        for record in records:
            if not record.canonical_task_state.is_terminal:
                report.observations[record.canonical_task_id] = self._recover_nonterminal(record)
                if report.observations[record.canonical_task_id] == RECOVER_UNKNOWN_PERSISTED:
                    report.unknown_persisted.append(record.canonical_task_id)
            else:
                # Terminal-but-unacknowledged: ensure CARD truth accompanies the
                # result before any later delivery (idempotent if already there).
                kind = self._ensure_terminal_card(record)
                report.observations[record.canonical_task_id] = kind
                if kind == RECOVER_TERMINAL_PERSISTED:
                    report.terminal_persisted.append(record.canonical_task_id)
        self._reclaim_expired_claims(report.reclaimed_expired_claims)
        self._recovered = True
        return report

    def _recover_nonterminal(self, record: DurableExecutionRecord) -> str:
        task_id = record.canonical_task_id
        if record.execution_phase.value == "PREPARED":
            # §17 PREPARED crash window: no trusted deterministic run locator
            # is exposed for a PREPARED-only identity, so W3 policy is
            # durable UNKNOWN + never blindly redispatch. No newest-run scan.
            self._cas_persist_unknown(task_id)
            return RECOVER_PREPARED_UNRESOLVED_UNKNOWN
        try:
            state = self._dispatcher.reconcile_status(task_id)
        except DispatchOutcomeUnresolvedError:
            self._cas_persist_unknown(task_id)
            return RECOVER_PREPARED_UNRESOLVED_UNKNOWN
        except Exception as exc:  # noqa: BLE001 - protocol integrity is preserved below
            if isinstance(exc, AdapterProtocolError):
                # Fail closed WITHOUT fabricating a state change: the durable
                # record keeps its conservative nonterminal truth.
                return RECOVER_PROTOCOL_ERROR_PRESERVED
            self._cas_persist_unknown(task_id)
            return RECOVER_UNKNOWN_PERSISTED
        if state == CanonicalTaskState.UNKNOWN:
            return RECOVER_UNKNOWN_PERSISTED
        if not state.is_terminal:
            # reconcile_status has already persisted the current nonterminal
            # observation durably (RUNNING/QUEUED/...).
            return RECOVER_RUNNING
        if state.is_terminal:
            return self._persist_terminal_truth(task_id)
        return RECOVER_UNRECOVERABLE

    def _persist_terminal_truth(self, task_id: str) -> str:
        """Adapter reported terminal: CanonicalResult -> governance -> CARD -> store.

        The existing dispatcher result seam performs the CanonicalResult
        validation and persists terminal truth (W1 CAS). No delivery attempt
        may precede this point (§19); delivery only happens in a later pass.
        """
        try:
            result = self._dispatcher.result(task_id)
        except Exception:  # noqa: BLE001 - honest uncertainty, never a fabrication
            self._cas_persist_unknown(task_id)
            return RECOVER_UNKNOWN_PERSISTED
        if not result.canonical_task_state or not CanonicalTaskState(result.canonical_task_state).is_terminal:
            self._cas_persist_unknown(task_id)
            return RECOVER_UNKNOWN_PERSISTED
        self._attach_card(result, task_id)
        return RECOVER_TERMINAL_PERSISTED

    def _attach_card(self, result: CanonicalResult, task_id: str) -> bool:
        current = self._store.get(task_id)
        if current is None:
            return False
        if current.worker_result_card is not None:
            return True
        role = _work_role_from_admission_scope(current.admission_scope)
        if role is None:
            # Without the trusted accounting scope there is no honest way to
            # recover the work role; the terminal result stays durable and
            # delivery simply never becomes eligible (truthful, bounded).
            return False
        try:
            governance = governance_projection_for_result(result)
            card = project_worker_result_card(
                result,
                governance,
                role,
                summary=_terminal_summary(result),
            )
            self._dispatcher.attach_worker_result_card(task_id, card)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ensure_terminal_card(self, record: DurableExecutionRecord) -> str:
        if record.terminal_result is None:
            # Terminal state durable without an attached result yet: attempt the
            # adapter result seam once (bounded), then stay recovery-eligible.
            kind = self._persist_terminal_truth(record.canonical_task_id)
            return kind
        if record.worker_result_card is None:
            self._attach_card(record.terminal_result, record.canonical_task_id)
        return RECOVER_TERMINAL_ALREADY_DURABLE

    def _cas_persist_unknown(self, task_id: str) -> None:
        """Persist a conservative UNKNOWN; terminal truth is never un-terminated."""
        current = self._store.get(task_id)
        if current is None or current.canonical_task_state.is_terminal:
            return
        if current.canonical_task_state == CanonicalTaskState.UNKNOWN:
            return
        try:
            self._store.compare_and_swap(
                task_id,
                current.record_revision,
                {"canonical_task_state": CanonicalTaskState.UNKNOWN.value},
            )
        except Exception:  # noqa: BLE001
            # A concurrent reconciler already advanced the record; the durable
            # store's CAS rules keep the truth safe either way.
            pass

    # -- delivery pass ----------------------------------------------------------

    def deliver_pending_once(self, *, max_deliveries: int | None = None) -> DeliveryReport:
        """One bounded delivery pass over terminal, CARD-durable pending records."""
        report = DeliveryReport()
        self._reclaim_expired_claims(report.reclaimed_expired_claims)
        if self._transport is None:
            return report
        budget = self._max_deliveries_per_pass if max_deliveries is None else max_deliveries
        pending = [
            record
            for record in self._store.scan_requiring_recovery()
            if record.canonical_task_state.is_terminal and record.delivery_state == DeliveryState.PENDING
        ]
        pending.sort(key=lambda r: (r.created_at, r.canonical_task_id))
        for record in pending[: max(0, budget)]:
            report.outcomes[record.canonical_task_id] = self._attempt_delivery(record)
        return report

    def _reclaim_expired_claims(self, sink: list[str]) -> None:
        for record in self._store.list_all():
            if record.delivery_state != DeliveryState.CLAIMED:
                continue
            if not self._claim_expired(record):
                continue
            try:
                self._store.compare_and_swap(
                    record.canonical_task_id,
                    record.record_revision,
                    {"delivery_state": DeliveryState.PENDING.value},
                )
                sink.append(record.canonical_task_id)
            except StaleExecutionRevisionError:
                continue
            except Exception:  # noqa: BLE001 - another pass owner advanced it
                continue

    def _claim_expired(self, record: DurableExecutionRecord) -> bool:
        if record.delivery_claim_until is None:
            return True
        stamp = _parse_iso(record.delivery_claim_until)
        if stamp is None:
            return True
        return self._now_fn() >= stamp

    def _attempt_delivery(self, record: DurableExecutionRecord) -> str:
        task_id = record.canonical_task_id
        # §19 hard gate: no delivery attempt may precede durable terminal truth.
        if record.terminal_result is None or record.worker_result_card is None:
            return DELIVER_TRUTH_NOT_DURABLE
        digest = record.worker_result_card_digest or ""
        # Bounded attempt policy BEFORE claiming (no transport burn on exhaustion).
        if record.delivery_attempt >= self._delivery_attempt_cap:
            try:
                self._store.compare_and_swap(
                    task_id, record.record_revision, {"delivery_state": DeliveryState.DROPPED.value}
                )
            except StaleExecutionRevisionError:
                return DELIVER_CLAIM_RACE_LOST
            return DELIVER_DROPPED_ATTEMPT_CAP
        # Single-flight claim: CAS with owner + TTL + attempt increment (§23).
        owner = f"{self._claim_owner_prefix}-{uuid.uuid4().hex}"
        until = _now_iso(self._now_fn() + timedelta(seconds=self._claim_ttl_seconds).total_seconds())
        try:
            claimed = self._store.compare_and_swap(
                task_id,
                record.record_revision,
                {
                    "delivery_state": DeliveryState.CLAIMED.value,
                    "delivery_claim_owner": owner,
                    "delivery_claim_until": until,
                    "delivery_attempt": record.delivery_attempt + 1,
                },
            )
        except StaleExecutionRevisionError:
            return DELIVER_CLAIM_RACE_LOST
        origin = claimed.origin_session_ref
        if origin is None:
            # Hard-missing trusted origin binding: bounded policy drops the
            # DELIVERY only; terminal result + CARD stay durable and ACK stays
            # false (§26); the execution record is never deleted.
            return self._release_or_drop(claimed, DeliveryState.DROPPED, DELIVER_DROPPED_ORIGIN_MISSING)
        assert self._transport is not None
        try:
            envelope = build_completion_envelope(claimed)
        except CompletionCoordinatorError:
            return DELIVER_TRUTH_NOT_DURABLE
        try:
            evidence = self._transport.deliver(session_ref=origin.value, envelope=envelope)
        except Exception:  # noqa: BLE001 - transport uncertainty keeps truth pending
            return self._release_or_drop(claimed, DeliveryState.PENDING, DELIVER_RELEASED_TRANSPORT_ERROR)
        outcome = evidence.outcome
        if isinstance(outcome, str):
            outcome = DeliveryTransportOutcome(outcome)
        if outcome == DeliveryTransportOutcome.COMPLETED:
            if parse_completion_ack(
                evidence.response_text, canonical_task_id=task_id, card_digest=digest
            ):
                try:
                    self._store.compare_and_swap(
                        task_id,
                        claimed.record_revision,
                        {"delivery_state": DeliveryState.ACKNOWLEDGED.value},
                    )
                    return DELIVER_ACKNOWLEDGED
                except StaleExecutionRevisionError:
                    return DELIVER_CLAIM_RACE_LOST
            # Exit-0/reentry-completed WITHOUT a bound ack is NOT delivery (§29).
            return self._release_or_drop(claimed, DeliveryState.PENDING, DELIVER_RELEASED_ACK_NOT_PROVEN)
        if outcome == DeliveryTransportOutcome.NOT_FOUND:
            # Exact session hard-missing: no new session is ever created;
            # delivery drops under the bounded policy with truth retained (§26).
            return self._release_or_drop(claimed, DeliveryState.DROPPED, DELIVER_DROPPED_SESSION_MISSING)
        # retryable / failed / unknown: keep pending, later pass retries the SAME
        # exact session; no forced interrupt of the origin session (§25/§35).
        return self._release_or_drop(claimed, DeliveryState.PENDING, DELIVER_RELEASED_RETRYABLE)

    def _release_or_drop(
        self,
        claimed: DurableExecutionRecord,
        target: DeliveryState,
        label: str,
    ) -> str:
        try:
            self._store.compare_and_swap(
                claimed.canonical_task_id,
                claimed.record_revision,
                {"delivery_state": target.value},
            )
        except StaleExecutionRevisionError:
            return DELIVER_CLAIM_RACE_LOST
        return label

    # -- bounded concurrency admission (§36-§41) --------------------------------

    def admit_dispatch(
        self,
        package: ExecutionPackage,
        target_executor_id: str | None = None,
    ) -> Any:
        """Admission-gated dispatch. Returns the DispatchResult on entry, or a
        typed bounded refusal (:class:`AdmissionDecision`) without any physical
        dispatch.

        Idempotent replay of an already-durable intent bypasses admission
        (it starts no new physical Worker); a conflicting intent keeps the
        W1 CONFLICT behavior. New physical dispatch is refused when the
        durable active nonterminal count for the same admission scope has
        reached the configured bound — including UNKNOWN and unresolved
        PREPARED records, which count conservatively.
        """
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")
        if self._require_recovery_before_admission and not self._recovered:
            raise CompletionRecoveryRequiredError(
                "run recover_once() before admitting new dispatch: the durable "
                "active-set must be reconstructed first (§43)"
            )
        existing = self._store.get_by_idempotency(package.idempotency_key)
        if existing is not None:
            # Replay/CONFLICT/Unresolved policy stays entirely with W1.
            return self._dispatcher.dispatch(package, target_executor_id=target_executor_id)
        if not self._admission_limits:
            # Mechanically wired but no configured bounds: nothing to enforce.
            # (Production composition always passes the RuntimeConfig-derived
            # mapping; None here is explicit, never an implicit global default.)
            return self._dispatcher.dispatch(package, target_executor_id=target_executor_id)
        scope = self._dispatcher.resolve_admission_scope(package)
        if scope is None or scope not in self._admission_limits:
            raise CompletionAdmissionScopeError(
                f"configured admission bounds exist but the scope for {package.canonical_task_id!r} "
                "is unresolvable; refusing to guess an accounting bucket (fail closed)"
            )
        limit = self._admission_limits[scope]
        active = self._active_count(scope)
        if active >= limit:
            return AdmissionDecision(
                admitted=False,
                reason="ADMISSION_CAPACITY_EXCEEDED",
                admission_scope=scope,
                active_count=active,
                limit=limit,
            )
        return self._dispatcher.dispatch(package, target_executor_id=target_executor_id)

    def _active_count(self, scope: str) -> int:
        """Durable active-set reconstruction (restart-safe, conservative).

        Active = same admission scope and nonterminal canonical state.
        UNKNOWN counts (oversubscription guard), unresolved PREPARED counts
        (CREATED-phase crash tails are nonterminal by W1 construction), and
        terminal records free capacity even while delivery is still pending
        (the physical Worker is gone).
        """
        count = 0
        for record in self._store.list_all():
            if record.admission_scope != scope:
                continue
            if not record.canonical_task_state.is_terminal:
                count += 1
        return count


__all__ = [
    "ACK_AFTER_RECONCILIATION_ONLY",
    "ACK_IDENTITY_BOUND",
    "AF_DURABLE_RECORD_IS_CANONICAL_RUNTIME_TRUTH",
    "AdmissionDecision",
    "BOUNDED_CONCURRENCY_IMPLEMENTED",
    "COMPLETION_ACK_TOKEN",
    "COMPLETION_ENVELOPE_HEADER",
    "CARD_PERSISTED_BEFORE_DELIVERY",
    "CompletionAdmissionScopeError",
    "CompletionCoordinatorError",
    "CompletionDeliveryTransport",
    "CompletionRecoveryRequiredError",
    "DELIVERY_ATTEMPT_CAP",
    "DELIVERY_CLAIM_TTL_SECONDS",
    "DELIVER_ACKNOWLEDGED",
    "DELIVER_CLAIM_RACE_LOST",
    "DELIVER_DROPPED_ATTEMPT_CAP",
    "DELIVER_DROPPED_ORIGIN_MISSING",
    "DELIVER_DROPPED_SESSION_MISSING",
    "DELIVER_RELEASED_ACK_NOT_PROVEN",
    "DELIVER_RELEASED_RETRYABLE",
    "DELIVER_RELEASED_TRANSPORT_ERROR",
    "DELIVER_SKIPPED_NO_TRANSPORT",
    "DELIVER_TRUTH_NOT_DURABLE",
    "DELIVERY_GUARANTEE",
    "DeliveryAttemptEvidence",
    "DeliveryReport",
    "DeliveryTransportOutcome",
    "DurableCompletionCoordinator",
    "FABRICATED_COMPLETION_ON_UNRESOLVED",
    "HERMES_LEDGER_IS_AF_AUTHORITY",
    "HERMES_LOCATOR_IS_AF_AUTHORITY",
    "MAX_DELIVERIES_PER_PASS",
    "POST_ACK_REDELIVERY",
    "PREPARED_UNRESOLVED_COUNTS_ACTIVE",
    "RECOVER_PREPARED_UNRESOLVED_UNKNOWN",
    "RECOVER_PROTOCOL_ERROR_PRESERVED",
    "RECOVER_RUNNING",
    "RECOVER_TERMINAL_ALREADY_DURABLE",
    "RECOVER_TERMINAL_PERSISTED",
    "RECOVER_UNKNOWN_PERSISTED",
    "RECOVER_UNRECOVERABLE",
    "RECOVERY_PASS_IMPLEMENTED",
    "RecoveryReport",
    "TERMINAL_RESULT_PERSISTED_BEFORE_DELIVERY",
    "UNKNOWN_COUNTS_ACTIVE",
    "build_completion_envelope",
    "governance_projection_for_result",
    "parse_completion_ack",
]
