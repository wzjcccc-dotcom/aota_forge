"""M2-W1 agent-neutral durable execution runtime state (AF Hermes Runtime Activation).

Executor-neutral durability seam for asynchronous Worker execution:

- ``DurableExecutionRecord``: the minimum durable execution truth needed to
  survive process restart — canonical identity (canonical_task_id, executor_id,
  adapter_handle, package_id, correlation_id, dispatch_attempt_id),
  idempotency truth (idempotency_key + intent_fingerprint), execution state
  (CanonicalTaskState), trusted-runtime origin session binding, terminal
  CanonicalResult, Worker Result CARD payload + digest, and bounded structural
  delivery fields with CAS metadata.
- ``ExecutionStateStore``: storage-neutral port (create/get/get_by_idempotency/
  compare_and_swap/scan_requiring_recovery/close) with two reference
  implementations aligned to the DurableJournalStore storage pattern
  (atomic persistence, per-record CAS, revision token, close/reopen
  durability, fail-closed load, explicit schema version).

Governance boundaries (M2 plan authority: wzjcccc-dotcom/aota-hermes-tools#36):

- JOURNAL_RECORD_ONTOLOGY_UNCHANGED=yes: JournalRecord/JournalState and the
  mutation ontology (plan_init/plan_retirement, authorization/lease fields)
  are NOT reused or broadened here; only the storage pattern semantics are
  aligned (I8).
- Terminal truth is structurally distinct from delivery truth (I13):
  canonical_task_state and terminal_result may never be inferred from
  delivery_state; no delivery state can fabricate task completion.
- Durable fields are runtime evidence/state, never semantic authority:
  possession of an origin_session_ref, an adapter_handle, or a persisted CARD
  grants no workspace mutation, role, provider, or model authority (I1/I10).
- W1 owns durability mechanics only. The delivery claim coordinator, retry
  loop, ACK orchestration, exact-session re-entry, and attempt-cap policy are
  W3. This module implements none of them.
- No distributed exactly-once dispatch is claimed: the crash window between
  physical dispatch success and DISPATCHED persistence is documented truthfully
  in ``ExecutionDispatcher.dispatch`` (recovery keeps the record PREPARED with
  sticky UNKNOWN; it never blindly redispatches).
- PRODUCTION_STORAGE_ENGINE_FROZEN=no; FILE_BACKED_IS_PRODUCTION_DEFAULT=no;
  the file-backed store is a reference/restart-test adapter only, mirroring
  the M4-7 journal precedent.
- Zero Hermes imports: executor recovery resolves adapters by durable
  executor_id through the ExecutorRegistry; adapter Python objects, Popen
  handles, reader threads, and open file descriptors are runtime-private and
  never serialized.
"""

from __future__ import annotations

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState, parse_state

# ---------------------------------------------------------------------------
# Governance markers
# ---------------------------------------------------------------------------

DURABLE_EXECUTION_RECORD_IMPLEMENTED = True
EXECUTION_STATE_STORE_PORT_IMPLEMENTED = True
PRODUCTION_STORAGE_ENGINE_FROZEN = False
FILE_BACKED_EXECUTION_STORE_ROLE = "reference_adapter"
FILE_BACKED_IS_PRODUCTION_DEFAULT = False
EXECUTION_DURABLE_SCHEMA_VERSION = 1
JOURNAL_RECORD_ONTOLOGY_UNCHANGED = True
JOURNAL_RECORD_SEMANTICS_REUSED = False
DURABLE_IDEMPOTENCY_IMPLEMENTED = True
CROSS_PROCESS_ATOMIC_TRANSACTION_GUARANTEED = False
EXACTLY_ONCE_DISPATCH_CLAIMED = False
DISPATCH_CRASH_WINDOW_DOCUMENTED = True
TERMINAL_TRUTH_SEPARATE_FROM_DELIVERY = True
ADAPTER_OBJECT_PERSISTED = False
ORIGIN_SESSION_REF_IS_AUTHORITY = False
CARD_POSSESSION_IS_AUTHORITY = False
THIRD_CARD_ONTOLOGY_CREATED = False
DELIVERY_COORDINATOR_IMPLEMENTED_IN_W1 = False

# ---------------------------------------------------------------------------
# Bounded vocabularies
# ---------------------------------------------------------------------------


class ExecutionPhase(str, Enum):
    """Narrow execution-specific dispatch phase (crash-window ordering).

    PREPARED  — durable intent identity exists; physical dispatch outcome is
                not yet confirmed (never blindly redispatched by W1).
    DISPATCHED — physical dispatch acknowledged by the adapter and the
                adapter_handle/initial state are durable.
    """

    PREPARED = "PREPARED"
    DISPATCHED = "DISPATCHED"


class DeliveryState(str, Enum):
    """Bounded structural delivery vocabulary (state persistence only).

    W1 persists these states CAS-safely; W3 owns claim/retry/ACK behavior.
    Delivery state can never set or infer canonical_task_state (I13).
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    ACKNOWLEDGED = "acknowledged"
    DROPPED = "dropped"


_MAX_ID_LENGTH = 256
_MAX_HANDLE_LENGTH = 512
_MAX_TOKEN_LENGTH = 1024


def _bounded_nonempty(value: object, label: str, max_len: int = _MAX_TOKEN_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"{label} length ({len(value)}) exceeds maximum {max_len}")
    return value


def parse_delivery_state(value: DeliveryState | str) -> DeliveryState:
    if isinstance(value, DeliveryState):
        return value
    if not isinstance(value, str):
        raise TypeError(f"delivery state must be DeliveryState or str, got {type(value).__name__}")
    try:
        return DeliveryState(value.strip().lower())
    except ValueError:
        raise ValueError(
            f"Unknown delivery_state {value!r}; bounded vocabulary: "
            f"{sorted(s.value for s in DeliveryState)}"
        ) from None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_execution_revision_token(
    canonical_task_id: str,
    revision: int,
    phase: ExecutionPhase,
    state: CanonicalTaskState,
) -> str:
    payload = f"{canonical_task_id}:{revision}:{phase.value}:{state.value}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def card_digest_for(card_dict: Mapping[str, Any]) -> str:
    """Recompute a Worker Result CARD digest over its canonical payload.

    Matches the existing CARD ontology's own compute_card_digest() exactly for
    canonical dicts (sha256 over canonical JSON); reuses it without importing
    the upper result-card layer into core (THIRD_CARD_ONTOLOGY_CREATED=no).
    """
    encoded = canonical_json(canonicalize(card_dict, path="worker_result_card"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Origin session binding (runtime-side, opaque, non-authoritative)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OriginSessionRef:
    """Trusted-runtime-supplied origin session binding.

    Opaque to the semantic model: W1 stores and restores the exact value and
    never parses or interprets its internal structure. It grants no authority
    by possession and is never model-self-asserted; it is supplied only by the
    trusted runtime side of the seam (dispatcher construction), and it does
    not touch TaskHandoff, the upper-layer work-role ontology, or Core work
    instructions.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or type(self.value) is not str:
            raise TypeError(f"OriginSessionRef.value must be a str, got {type(self.value).__name__}")
        if not self.value.strip():
            raise ValueError("OriginSessionRef.value must be a non-empty string")
        if len(self.value) > _MAX_HANDLE_LENGTH:
            raise ValueError(
                f"OriginSessionRef.value length ({len(self.value)}) exceeds maximum {_MAX_HANDLE_LENGTH}"
            )

    @classmethod
    def from_value(cls, val: "OriginSessionRef | str | None") -> "OriginSessionRef | None":
        if val is None:
            return None
        if isinstance(val, cls):
            return val
        if isinstance(val, str) and type(val) is str:
            return cls(value=val)
        raise TypeError(f"Cannot construct OriginSessionRef from {type(val).__name__}")


# ---------------------------------------------------------------------------
# Durable execution record
# ---------------------------------------------------------------------------

# Closed vocabulary of CAS-mutable fields. Everything not listed here is
# immutable durable identity; identity fields (canonical_task_id,
# executor_id, package_id, correlation_id, dispatch_attempt_id,
# idempotency_key, intent_fingerprint) may never be rewritten.
CAS_MUTABLE_EXECUTION_FIELDS: frozenset[str] = frozenset(
    {
        "execution_phase",
        "adapter_handle",
        "initial_state",
        "dispatched_at",
        "canonical_task_state",
        "origin_session_ref",
        "terminal_result",
        "worker_result_card",
        "worker_result_card_digest",
        "delivery_state",
        "delivery_attempt",
        "delivery_claim_owner",
        "delivery_claim_until",
    }
)

_DURABLE_FIELD_NAMES: tuple[str, ...] = (
    "schema_version",
    "canonical_task_id",
    "executor_id",
    "adapter_handle",
    "package_id",
    "correlation_id",
    "dispatch_attempt_id",
    "idempotency_key",
    "intent_fingerprint",
    "execution_phase",
    "canonical_task_state",
    "initial_state",
    "dispatched_at",
    "origin_session_ref",
    "terminal_result",
    "worker_result_card",
    "worker_result_card_digest",
    "delivery_state",
    "delivery_attempt",
    "delivery_claim_owner",
    "delivery_claim_until",
    "created_at",
    "updated_at",
    "record_revision",
    "revision_token",
)


@dataclass
class DurableExecutionRecord:
    """Executor-neutral durable execution truth for one canonical task.

    Structural ownership (M2/W1):
    - Identity: canonical_task_id, executor_id, adapter_handle, package_id,
      correlation_id, dispatch_attempt_id (immutable once durable).
    - Idempotency: idempotency_key + intent_fingerprint (immutable).
    - Execution truth: execution_phase, canonical_task_state, initial_state,
      dispatched_at.
    - Terminal truth: terminal_result (CanonicalResult) and worker_result_card
      payload + digest. Terminal truth is structurally distinct from delivery
      truth and never inferred from it.
    - Delivery structure (persistence only, no coordinator behavior):
      delivery_state, delivery_attempt, delivery_claim_owner,
      delivery_claim_until.
    - Runtime-side origin binding: origin_session_ref.
    - CAS metadata: record_revision, revision_token, schema_version,
      created_at, updated_at.

    Deliberately absent (runtime-private, never persisted): RouteRecord._adapter
    objects, Popen handles, reader threads, open file descriptors, in-memory
    stdout/stderr buffers, adapter Python instances.
    """

    canonical_task_id: str
    executor_id: str
    package_id: str
    correlation_id: str
    dispatch_attempt_id: str
    idempotency_key: str
    intent_fingerprint: str
    execution_phase: ExecutionPhase = ExecutionPhase.PREPARED
    canonical_task_state: CanonicalTaskState = CanonicalTaskState.CREATED
    adapter_handle: str | None = None
    initial_state: CanonicalTaskState | None = None
    dispatched_at: str | None = None
    origin_session_ref: OriginSessionRef | None = None
    terminal_result: CanonicalResult | None = None
    worker_result_card: Mapping[str, Any] | None = None
    worker_result_card_digest: str | None = None
    delivery_state: DeliveryState = DeliveryState.PENDING
    delivery_attempt: int = 0
    delivery_claim_owner: str | None = None
    delivery_claim_until: str | None = None
    record_revision: int = 1
    revision_token: str = ""
    schema_version: int = EXECUTION_DURABLE_SCHEMA_VERSION
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        self.canonical_task_id = _bounded_nonempty(self.canonical_task_id, "canonical_task_id", _MAX_ID_LENGTH)
        self.executor_id = _bounded_nonempty(self.executor_id, "executor_id", _MAX_ID_LENGTH)
        self.package_id = _bounded_nonempty(self.package_id, "package_id", _MAX_ID_LENGTH)
        self.correlation_id = _bounded_nonempty(self.correlation_id, "correlation_id", _MAX_ID_LENGTH)
        self.dispatch_attempt_id = _bounded_nonempty(
            self.dispatch_attempt_id, "dispatch_attempt_id", _MAX_ID_LENGTH
        )
        self.idempotency_key = _bounded_nonempty(self.idempotency_key, "idempotency_key", _MAX_ID_LENGTH)
        self.intent_fingerprint = _bounded_nonempty(self.intent_fingerprint, "intent_fingerprint", 128)

        if not isinstance(self.execution_phase, ExecutionPhase):
            self.execution_phase = ExecutionPhase(str(self.execution_phase))
        if not isinstance(self.canonical_task_state, CanonicalTaskState):
            self.canonical_task_state = parse_state(self.canonical_task_state)
        if self.initial_state is not None and not isinstance(self.initial_state, CanonicalTaskState):
            self.initial_state = parse_state(self.initial_state)
        if not isinstance(self.delivery_state, DeliveryState):
            self.delivery_state = parse_delivery_state(self.delivery_state)
        if type(self.record_revision) is not int or self.record_revision < 1:
            raise ValueError("record_revision must be an int >= 1")
        if self.schema_version != EXECUTION_DURABLE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported execution record schema_version {self.schema_version!r}; "
                f"W1 accepts exactly {EXECUTION_DURABLE_SCHEMA_VERSION} (fail closed)"
            )

        if self.adapter_handle is not None:
            _bounded_nonempty(self.adapter_handle, "adapter_handle", _MAX_HANDLE_LENGTH)
        if self.dispatched_at is not None:
            _bounded_nonempty(self.dispatched_at, "dispatched_at")
        if self.origin_session_ref is not None and not isinstance(self.origin_session_ref, OriginSessionRef):
            raise TypeError(
                f"origin_session_ref must be OriginSessionRef or None, "
                f"got {type(self.origin_session_ref).__name__}"
            )

        # Phase/state coherence
        if self.execution_phase == ExecutionPhase.DISPATCHED:
            if self.adapter_handle is None:
                raise ValueError("DISPATCHED requires a durable adapter_handle")
            if self.initial_state is None or self.dispatched_at is None:
                raise ValueError("DISPATCHED requires initial_state and dispatched_at")
        else:
            if self.adapter_handle is not None:
                raise ValueError("adapter_handle may only be durable once the record is DISPATCHED")

        # Terminal truth vs delivery truth (I13)
        if self.terminal_result is not None:
            if not isinstance(self.terminal_result, CanonicalResult):
                raise TypeError("terminal_result must be CanonicalResult")
            if not self.canonical_task_state.is_terminal:
                raise ValueError(
                    "terminal_result requires a terminal canonical_task_state; delivery "
                    "state may never fabricate task completion"
                )
            if self.terminal_result.canonical_task_id != self.canonical_task_id:
                raise ValueError("terminal_result canonical_task_id must match the record")
            if self.terminal_result.canonical_task_state != self.canonical_task_state.value:
                raise ValueError(
                    "terminal_result canonical_task_state must match the record's "
                    "canonical_task_state"
                )
        if self.execution_phase == ExecutionPhase.PREPARED:
            if self.canonical_task_state != CanonicalTaskState.CREATED:
                raise ValueError(
                    "PREPARED records carry intent identity only; canonical_task_state "
                    "must remain CREATED until dispatch is confirmed"
                )
        if self.worker_result_card is not None:
            if not isinstance(self.worker_result_card, Mapping):
                raise TypeError("worker_result_card must be the canonical CARD mapping")
            if not isinstance(self.worker_result_card_digest, str) or not self.worker_result_card_digest:
                raise ValueError("worker_result_card requires its digest")
            if len(self.worker_result_card_digest) > 128:
                raise ValueError("worker_result_card_digest exceeds maximum length")
            if card_digest_for(self.worker_result_card) != self.worker_result_card_digest:
                raise ValueError("worker_result_card_digest does not match the CARD canonical payload")
        if self.worker_result_card is None and self.worker_result_card_digest is not None:
            raise ValueError("worker_result_card_digest requires the CARD payload")

        if type(self.delivery_attempt) is not int or self.delivery_attempt < 0:
            raise ValueError("delivery_attempt must be an int >= 0")
        if self.delivery_state in (DeliveryState.ACKNOWLEDGED, DeliveryState.DROPPED):
            if self.terminal_result is None:
                raise ValueError(
                    f"delivery_state={self.delivery_state.value!r} requires a durable terminal_result; "
                    "no delivery state may fabricate task completion"
                )
        if self.delivery_state == DeliveryState.CLAIMED:
            if self.delivery_claim_owner is None:
                raise ValueError("claimed delivery state requires delivery_claim_owner")
        if self.delivery_claim_owner is not None:
            _bounded_nonempty(self.delivery_claim_owner, "delivery_claim_owner", _MAX_ID_LENGTH)
        if self.delivery_claim_until is not None:
            _bounded_nonempty(self.delivery_claim_until, "delivery_claim_until")

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return canonicalize(
            {
                "schema_version": self.schema_version,
                "canonical_task_id": self.canonical_task_id,
                "executor_id": self.executor_id,
                "adapter_handle": self.adapter_handle,
                "package_id": self.package_id,
                "correlation_id": self.correlation_id,
                "dispatch_attempt_id": self.dispatch_attempt_id,
                "idempotency_key": self.idempotency_key,
                "intent_fingerprint": self.intent_fingerprint,
                "execution_phase": self.execution_phase.value,
                "canonical_task_state": self.canonical_task_state.value,
                "initial_state": self.initial_state.value if self.initial_state else None,
                "dispatched_at": self.dispatched_at,
                "origin_session_ref": self.origin_session_ref.value if self.origin_session_ref else None,
                "terminal_result": self.terminal_result.to_dict() if self.terminal_result else None,
                "worker_result_card": (
                    canonicalize(self.worker_result_card, path="worker_result_card")
                    if self.worker_result_card is not None
                    else None
                ),
                "worker_result_card_digest": self.worker_result_card_digest,
                "delivery_state": self.delivery_state.value,
                "delivery_attempt": self.delivery_attempt,
                "delivery_claim_owner": self.delivery_claim_owner,
                "delivery_claim_until": self.delivery_claim_until,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "record_revision": self.record_revision,
                "revision_token": self.revision_token,
            },
            path="DurableExecutionRecord",
        )  # type: ignore[return-value]

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DurableExecutionRecord":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        # Strict v1 contract: unknown and newer schemas fail closed.
        raw_version = data.get("schema_version")
        if raw_version is None:
            raise ValueError("Missing required field in DurableExecutionRecord: 'schema_version'")
        if type(raw_version) is not int:
            raise ValueError(f"schema_version must be an int, got {type(raw_version).__name__}")
        if raw_version > EXECUTION_DURABLE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {raw_version} (newer than v{EXECUTION_DURABLE_SCHEMA_VERSION}); fail closed"
            )
        if raw_version < EXECUTION_DURABLE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {raw_version}; no migration framework in W1 (fail closed)"
            )
        extra = set(data.keys()) - set(_DURABLE_FIELD_NAMES)
        if extra:
            raise ValueError(f"Unknown field(s) in DurableExecutionRecord: {sorted(extra)}")
        required = (
            "canonical_task_id",
            "executor_id",
            "package_id",
            "correlation_id",
            "dispatch_attempt_id",
            "idempotency_key",
            "intent_fingerprint",
            "execution_phase",
            "canonical_task_state",
            "record_revision",
            "revision_token",
            "created_at",
            "updated_at",
        )
        for req in required:
            if req not in data:
                raise ValueError(f"Missing required field in DurableExecutionRecord: {req!r}")
        raw_result = data.get("terminal_result")
        return cls(
            schema_version=raw_version,
            canonical_task_id=data["canonical_task_id"],
            executor_id=data["executor_id"],
            adapter_handle=data.get("adapter_handle"),
            package_id=data["package_id"],
            correlation_id=data["correlation_id"],
            dispatch_attempt_id=data["dispatch_attempt_id"],
            idempotency_key=data["idempotency_key"],
            intent_fingerprint=data["intent_fingerprint"],
            execution_phase=ExecutionPhase(data["execution_phase"]),
            canonical_task_state=parse_state(data["canonical_task_state"]),
            initial_state=parse_state(data["initial_state"]) if data.get("initial_state") else None,
            dispatched_at=data.get("dispatched_at"),
            origin_session_ref=OriginSessionRef.from_value(data.get("origin_session_ref")),
            terminal_result=CanonicalResult.from_dict(raw_result) if raw_result is not None else None,
            worker_result_card=data.get("worker_result_card"),
            worker_result_card_digest=data.get("worker_result_card_digest"),
            delivery_state=parse_delivery_state(data.get("delivery_state", DeliveryState.PENDING.value)),
            delivery_attempt=data.get("delivery_attempt", 0),
            delivery_claim_owner=data.get("delivery_claim_owner"),
            delivery_claim_until=data.get("delivery_claim_until"),
            record_revision=data["record_revision"],
            revision_token=data["revision_token"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    # -- CAS update semantics -----------------------------------------------

    def with_cas_updates(self, updates: Mapping[str, Any]) -> "DurableExecutionRecord":
        """Return a new revision with validated updates; deterministic rejections.

        Structural invariants enforced here (storage semantics, not W3 policy):
        - identity fields are immutable;
        - adapter_handle/initial_state/dispatched_at bind exactly once at
          PREPARED -> DISPATCHED;
        - terminal canonical_task_state is sticky (no un-terminaling);
        - terminal_result attaches exactly once and must agree with the state;
        - CARD payload + digest attach together and the digest is verified;
        - origin_session_ref binds at most once;
        - acknowledged/dropped require terminal truth and are themselves
          immutable; claimed requires an owner reference.
        """
        if not isinstance(updates, Mapping):
            raise TypeError(f"updates must be a mapping, got {type(updates).__name__}")
        unknown = set(updates) - CAS_MUTABLE_EXECUTION_FIELDS
        if unknown:
            raise ValueError(f"Non-mutable or unknown CAS field(s): {sorted(unknown)}")
        if not updates:
            raise ValueError("compare_and_swap requires at least one field update")

        merged: dict[str, Any] = {
            "execution_phase": self.execution_phase,
            "adapter_handle": self.adapter_handle,
            "initial_state": self.initial_state,
            "dispatched_at": self.dispatched_at,
            "canonical_task_state": self.canonical_task_state,
            "origin_session_ref": self.origin_session_ref,
            "terminal_result": self.terminal_result,
            "worker_result_card": self.worker_result_card,
            "worker_result_card_digest": self.worker_result_card_digest,
            "delivery_state": self.delivery_state,
            "delivery_attempt": self.delivery_attempt,
            "delivery_claim_owner": self.delivery_claim_owner,
            "delivery_claim_until": self.delivery_claim_until,
        }

        # Phase transition: only PREPARED -> DISPATCHED.
        if "execution_phase" in updates:
            new_phase = updates["execution_phase"]
            new_phase = new_phase if isinstance(new_phase, ExecutionPhase) else ExecutionPhase(str(new_phase))
            if self.execution_phase == new_phase:
                raise ValueError(f"execution_phase already {new_phase.value}; no-op phase update rejected")
            if not (
                self.execution_phase == ExecutionPhase.PREPARED
                and new_phase == ExecutionPhase.DISPATCHED
            ):
                raise ValueError(
                    f"Illegal execution_phase transition {self.execution_phase.value} -> {new_phase.value}"
                )
            merged["execution_phase"] = new_phase

        # One-time dispatch bindings.
        for bind in ("adapter_handle", "initial_state", "dispatched_at"):
            if bind in updates:
                if self.__dict__[bind] is not None:
                    raise ValueError(f"{bind} is already durably bound; rewrites rejected")
                merged[bind] = updates[bind]

        # State update with terminal stickiness.
        if "canonical_task_state" in updates:
            new_state = parse_state(updates["canonical_task_state"])
            if self.canonical_task_state.is_terminal and new_state != self.canonical_task_state:
                raise ValueError(
                    f"canonical_task_state {self.canonical_task_state.value} is terminal and sticky; "
                    f"rejected transition to {new_state.value}"
                )
            if "terminal_result" not in updates and new_state.is_terminal and self.terminal_result is None:
                # Terminal task truth without a result yet is legitimate
                # (result attachment is a separate CAS step); no delivery inference.
                pass
            merged["canonical_task_state"] = new_state

        # Terminal result attaches exactly once and agrees with state.
        if "terminal_result" in updates:
            raw = updates["terminal_result"]
            result = raw if isinstance(raw, CanonicalResult) else CanonicalResult.from_dict(raw)  # type: ignore[arg-type]
            if self.terminal_result is not None:
                if self.terminal_result.to_json() != result.to_json():
                    raise ValueError("terminal_result is already durable and contradicts the proposed result")
                raise ValueError("terminal_result attaches exactly once; identical re-attach rejected")
            if not _attachable_result_is_terminal(result):
                raise ValueError("terminal_result requires a terminal canonical task state")
            if merged["canonical_task_state"] != parse_state(result.canonical_task_state):
                raise ValueError(
                    "terminal_result requires canonical_task_state to equal the result state in the same CAS"
                )
            merged["terminal_result"] = result
            merged["canonical_task_state"] = parse_state(result.canonical_task_state)

        # Origin session binds at most once (runtime-supplied; non-authoritative).
        if "origin_session_ref" in updates:
            ref = OriginSessionRef.from_value(updates["origin_session_ref"])
            if self.origin_session_ref is not None:
                if ref is not None and self.origin_session_ref == ref:
                    raise ValueError("origin_session_ref identical rebind rejected")
                raise ValueError("origin_session_ref is already durably bound; rewrites rejected")
            if ref is None:
                raise ValueError("origin_session_ref update must be a non-empty opaque value")
            merged["origin_session_ref"] = ref

        # CARD payload + digest pair.
        has_card_update = "worker_result_card" in updates or "worker_result_card_digest" in updates
        if has_card_update:
            if "worker_result_card" not in updates or "worker_result_card_digest" not in updates:
                raise ValueError("worker_result_card and its digest must be CAS-updated together")
            if self.worker_result_card is not None:
                raise ValueError("worker_result_card is already durable; rewrites rejected")
            card = updates["worker_result_card"]
            digest = updates["worker_result_card_digest"]
            if not isinstance(card, Mapping):
                raise TypeError("worker_result_card must be the canonical CARD mapping")
            canonical_card = canonicalize(card, path="worker_result_card")
            if not isinstance(digest, str) or card_digest_for(canonical_card) != digest:
                raise ValueError("worker_result_card_digest does not verify against the CARD payload")
            merged["worker_result_card"] = canonical_card
            merged["worker_result_card_digest"] = digest

        # Delivery structural state (bounded vocabulary; policy belongs to W3).
        if "delivery_attempt" in updates:
            merged["delivery_attempt"] = updates["delivery_attempt"]
        if "delivery_state" in updates:
            new_delivery = parse_delivery_state(updates["delivery_state"])
            if self.delivery_state in (DeliveryState.ACKNOWLEDGED, DeliveryState.DROPPED):
                if new_delivery != self.delivery_state:
                    raise ValueError(
                        f"delivery_state {self.delivery_state.value!r} is immutable; "
                        "claim/ACK lifecycle policy is W3, not a store rewrite"
                    )
                raise ValueError("identical delivery_state re-update rejected")
            if new_delivery in (DeliveryState.ACKNOWLEDGED, DeliveryState.DROPPED):
                if merged["terminal_result"] is None:
                    raise ValueError(
                        f"delivery_state={new_delivery.value!r} requires a durable terminal_result"
                    )
                if "delivery_claim_owner" in updates or "delivery_claim_until" in updates:
                    raise ValueError(
                        f"delivery_state={new_delivery.value!r} clears claim references; "
                        "owner/until updates in the same CAS rejected"
                    )
                merged["delivery_claim_owner"] = None
                merged["delivery_claim_until"] = None
            elif new_delivery == DeliveryState.CLAIMED:
                owner = updates.get("delivery_claim_owner", self.delivery_claim_owner)
                if owner is None:
                    raise ValueError("claimed delivery_state requires delivery_claim_owner in the same CAS")
                merged["delivery_claim_owner"] = owner
                if "delivery_claim_until" in updates:
                    merged["delivery_claim_until"] = updates["delivery_claim_until"]
            else:  # pending release
                if "delivery_claim_owner" in updates:
                    raise ValueError("pending delivery_state must not carry a claim owner update")
                merged["delivery_claim_owner"] = None
                merged["delivery_claim_until"] = None
            merged["delivery_state"] = new_delivery
        else:
            for key in ("delivery_claim_owner", "delivery_claim_until"):
                if key in updates:
                    merged[key] = updates[key]

        new_revision = self.record_revision + 1
        new_token = _compute_execution_revision_token(
            self.canonical_task_id, new_revision, merged["execution_phase"], merged["canonical_task_state"]
        )
        candidate = replace(
            self,
            **merged,
            record_revision=new_revision,
            revision_token=new_token,
            created_at=self.created_at,
            updated_at=_now_iso(),
        )
        return candidate


# CanonicalResult stores its state as a validated string; small helper keeps
# the update path readable without broadening either ontology.
def _attachable_result_is_terminal(result: CanonicalResult) -> bool:
    return parse_state(result.canonical_task_state).is_terminal


# ---------------------------------------------------------------------------
# Typed error model (bounded; reuse of the existing .code conventions)
# ---------------------------------------------------------------------------

EXECUTION_RECORD_NOT_FOUND = "EXECUTION_RECORD_NOT_FOUND"
STALE_EXECUTION_REVISION = "STALE_EXECUTION_REVISION"
EXECUTION_PERSISTENCE_FAILURE = "EXECUTION_PERSISTENCE_FAILURE"
EXECUTION_IDEMPOTENCY_CONFLICT = "EXECUTION_IDEMPOTENCY_CONFLICT"


class ExecutionStateError(Exception):
    """Base error for the durable execution state seam."""

    code: str = "EXECUTION_STATE_ERROR"


class ExecutionRecordNotFoundError(ExecutionStateError):
    code = EXECUTION_RECORD_NOT_FOUND


class StaleExecutionRevisionError(ExecutionStateError):
    code = STALE_EXECUTION_REVISION


class ExecutionPersistenceFailureError(ExecutionStateError):
    code = EXECUTION_PERSISTENCE_FAILURE


class ExecutionIdempotencyConflictError(ExecutionStateError):
    code = EXECUTION_IDEMPOTENCY_CONFLICT


# ---------------------------------------------------------------------------
# Storage-neutral port
# ---------------------------------------------------------------------------


class ExecutionStateStore(ABC):
    """Abstract storage-neutral execution-state port (M2/W1).

    Only operations genuinely needed for M2. No arbitrary query DSL.
    No cross-process distributed transaction guarantees are claimed beyond
    the per-record CAS that these adapters actually implement.
    """

    @abstractmethod
    def create(self, record: DurableExecutionRecord) -> "DurableExecutionRecord":
        raise NotImplementedError

    @abstractmethod
    def get(self, canonical_task_id: str) -> "DurableExecutionRecord | None":
        raise NotImplementedError

    @abstractmethod
    def get_by_idempotency(self, idempotency_key: str) -> "DurableExecutionRecord | None":
        raise NotImplementedError

    @abstractmethod
    def compare_and_swap(
        self,
        canonical_task_id: str,
        expected_revision: int,
        updates: Mapping[str, Any],
        expected_revision_token: str | None = None,
    ) -> "DurableExecutionRecord":
        raise NotImplementedError

    @abstractmethod
    def scan_requiring_recovery(self) -> "list[DurableExecutionRecord]":
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> "list[DurableExecutionRecord]":
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


# Recovery eligibility (bounded, for W3 startup reconciliation):
#   - nonterminal task state (includes sticky UNKNOWN and PREPARED crash tails)
#   - terminal truth not yet delivery-acknowledged
def _requires_recovery(record: DurableExecutionRecord) -> bool:
    if not record.canonical_task_state.is_terminal:
        return True
    if record.delivery_state not in (DeliveryState.ACKNOWLEDGED, DeliveryState.DROPPED):
        return True
    return False


class InMemoryExecutionStateStore(ExecutionStateStore):
    """Cheap per-record-locked in-memory store for unit tests (no reopen proof)."""

    def __init__(self) -> None:
        self._store: dict[str, DurableExecutionRecord] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()
        self._closed = False
        self._fail_next_create = False
        self._fail_next_cas = False

    def _lock_for(self, canonical_task_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(canonical_task_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[canonical_task_id] = lock
            return lock

    def inject_fail_next_create(self) -> None:
        self._fail_next_create = True

    def inject_fail_next_cas(self) -> None:
        self._fail_next_cas = True

    def create(self, record: DurableExecutionRecord) -> DurableExecutionRecord:
        if self._closed:
            raise ExecutionPersistenceFailureError("store closed")
        if self._fail_next_create:
            self._fail_next_create = False
            raise ExecutionPersistenceFailureError("injected create failure")
        if not isinstance(record, DurableExecutionRecord):
            raise TypeError(f"record must be DurableExecutionRecord, got {type(record).__name__}")
        if record.execution_phase != ExecutionPhase.PREPARED:
            raise ExecutionPersistenceFailureError(
                "create requires a fresh PREPARED record; durable execution identity starts at intent"
            )
        with self._lock_for(record.canonical_task_id):
            if record.canonical_task_id in self._store:
                raise ExecutionPersistenceFailureError(
                    f"canonical_task_id already durable: {record.canonical_task_id}"
                )
            for existing in self._store.values():
                if existing.idempotency_key == record.idempotency_key:
                    raise ExecutionIdempotencyConflictError(
                        f"idempotency_key already owned by {existing.canonical_task_id}"
                    )
            fresh = replace(
                record,
                record_revision=1,
                revision_token=_compute_execution_revision_token(
                    record.canonical_task_id, 1, ExecutionPhase.PREPARED, CanonicalTaskState.CREATED
                ),
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
            self._store[record.canonical_task_id] = fresh
            return fresh

    def get(self, canonical_task_id: str) -> DurableExecutionRecord | None:
        return self._store.get(canonical_task_id)

    def get_by_idempotency(self, idempotency_key: str) -> DurableExecutionRecord | None:
        for record in self._store.values():
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def compare_and_swap(
        self,
        canonical_task_id: str,
        expected_revision: int,
        updates: Mapping[str, Any],
        expected_revision_token: str | None = None,
    ) -> DurableExecutionRecord:
        if self._closed:
            raise ExecutionPersistenceFailureError("store closed")
        if self._fail_next_cas:
            self._fail_next_cas = False
            raise ExecutionPersistenceFailureError("injected CAS failure")
        with self._lock_for(canonical_task_id):
            current = self._store.get(canonical_task_id)
            if current is None:
                raise ExecutionRecordNotFoundError(f"execution record not found: {canonical_task_id}")
            _check_expected_revision(current, expected_revision, expected_revision_token)
            try:
                candidate = current.with_cas_updates(updates)
            except (ValueError, TypeError) as exc:
                raise ExecutionPersistenceFailureError(f"execution record update invalid: {exc}") from exc
            self._store[canonical_task_id] = candidate
            return candidate

    def scan_requiring_recovery(self) -> list[DurableExecutionRecord]:
        result = [r for r in self._store.values() if _requires_recovery(r)]
        result.sort(key=lambda r: (r.canonical_task_id, r.created_at))
        return result

    def list_all(self) -> list[DurableExecutionRecord]:
        result = list(self._store.values())
        result.sort(key=lambda r: (r.canonical_task_id, r.created_at))
        return result

    def close(self) -> None:
        self._closed = True


class FileBackedExecutionStateStore(ExecutionStateStore):
    """File-backed reference adapter proving close/reopen durability.

    Mirrors the M4-7 DurableJournalStore storage pattern (atomic temp+rename
    persist, per-record RLock, revision tokens, fail-closed strict load with
    rollback on persist failure). Reference/test adapter only:
    FILE_BACKED_IS_PRODUCTION_DEFAULT=no. Single-file JSON map keyed by
    canonical_task_id.
    """

    def __init__(self, path: str | Path, *, ensure_dir: bool = True) -> None:
        self._path = Path(path)
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()
        self._closed = False
        self._store: dict[str, DurableExecutionRecord] = {}
        self._fail_next_create = False
        self._fail_next_cas = False
        self._fail_next_persist = False
        if ensure_dir:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    # -- failure injection seams (mirrors journal precedent) ----------------

    def inject_fail_next_create(self) -> None:
        self._fail_next_create = True

    def inject_fail_next_cas(self) -> None:
        self._fail_next_cas = True

    def inject_fail_next_persist(self) -> None:
        self._fail_next_persist = True

    # -- durability core -----------------------------------------------------

    def _lock_for(self, canonical_task_id: str) -> threading.RLock:
        with self._global_lock:
            lock = self._locks.get(canonical_task_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[canonical_task_id] = lock
            return lock

    def _load(self) -> None:
        if not self._path.exists():
            self._store = {}
            return
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExecutionPersistenceFailureError(f"persistence read failed: {exc}") from exc
        if not text.strip():
            self._store = {}
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExecutionPersistenceFailureError(f"persistence load failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ExecutionPersistenceFailureError("persistence root must be a JSON object")
        store: dict[str, DurableExecutionRecord] = {}
        for task_id, entry in data.items():
            try:
                record = DurableExecutionRecord.from_dict(entry)
            except (ValueError, TypeError) as exc:
                raise ExecutionPersistenceFailureError(
                    f"corrupted or unsupported execution record {task_id}: {exc}"
                ) from exc
            if record.canonical_task_id != task_id:
                raise ExecutionPersistenceFailureError(
                    f"execution record key {task_id!r} contradicts canonical_task_id "
                    f"{record.canonical_task_id!r}"
                )
            store[task_id] = record
        self._store = store

    def _persist(self) -> None:
        if self._fail_next_persist:
            self._fail_next_persist = False
            raise ExecutionPersistenceFailureError("injected persist failure")
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = {task_id: record.to_dict() for task_id, record in self._store.items()}
        text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True)
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            raise ExecutionPersistenceFailureError(f"persist failed: {exc}") from exc

    # -- port ----------------------------------------------------------------

    def create(self, record: DurableExecutionRecord) -> DurableExecutionRecord:
        if self._closed:
            raise ExecutionPersistenceFailureError("store closed")
        if self._fail_next_create:
            self._fail_next_create = False
            raise ExecutionPersistenceFailureError("injected create failure")
        if not isinstance(record, DurableExecutionRecord):
            raise TypeError(f"record must be DurableExecutionRecord, got {type(record).__name__}")
        if record.execution_phase != ExecutionPhase.PREPARED:
            raise ExecutionPersistenceFailureError(
                "create requires a fresh PREPARED record; durable execution identity starts at intent"
            )
        with self._lock_for(record.canonical_task_id):
            self._load()
            if record.canonical_task_id in self._store:
                raise ExecutionPersistenceFailureError(
                    f"canonical_task_id already durable: {record.canonical_task_id}"
                )
            for existing in self._store.values():
                if existing.idempotency_key == record.idempotency_key:
                    raise ExecutionIdempotencyConflictError(
                        f"idempotency_key already owned by {existing.canonical_task_id}"
                    )
            fresh = replace(
                record,
                record_revision=1,
                revision_token=_compute_execution_revision_token(
                    record.canonical_task_id, 1, ExecutionPhase.PREPARED, CanonicalTaskState.CREATED
                ),
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
            self._store[record.canonical_task_id] = fresh
            try:
                self._persist()
            except ExecutionPersistenceFailureError:
                self._store.pop(record.canonical_task_id, None)
                raise
            except Exception as exc:
                self._store.pop(record.canonical_task_id, None)
                raise ExecutionPersistenceFailureError(f"persist failed: {exc}") from exc
            return fresh

    def get(self, canonical_task_id: str) -> DurableExecutionRecord | None:
        with self._global_lock:
            self._load()
            return self._store.get(canonical_task_id)

    def get_by_idempotency(self, idempotency_key: str) -> DurableExecutionRecord | None:
        with self._global_lock:
            self._load()
            for record in self._store.values():
                if record.idempotency_key == idempotency_key:
                    return record
            return None

    def compare_and_swap(
        self,
        canonical_task_id: str,
        expected_revision: int,
        updates: Mapping[str, Any],
        expected_revision_token: str | None = None,
    ) -> DurableExecutionRecord:
        if self._closed:
            raise ExecutionPersistenceFailureError("store closed")
        if self._fail_next_cas:
            self._fail_next_cas = False
            raise ExecutionPersistenceFailureError("injected CAS failure")
        with self._lock_for(canonical_task_id):
            self._load()
            current = self._store.get(canonical_task_id)
            if current is None:
                raise ExecutionRecordNotFoundError(f"execution record not found: {canonical_task_id}")
            _check_expected_revision(current, expected_revision, expected_revision_token)
            try:
                candidate = current.with_cas_updates(updates)
            except (ValueError, TypeError) as exc:
                raise ExecutionPersistenceFailureError(f"execution record update invalid: {exc}") from exc
            old_entry = current
            self._store[canonical_task_id] = candidate
            try:
                self._persist()
            except ExecutionPersistenceFailureError:
                self._store[canonical_task_id] = old_entry
                raise
            except Exception as exc:
                self._store[canonical_task_id] = old_entry
                raise ExecutionPersistenceFailureError(f"persist failed: {exc}") from exc
            return candidate

    def scan_requiring_recovery(self) -> list[DurableExecutionRecord]:
        with self._global_lock:
            self._load()
            result = [r for r in self._store.values() if _requires_recovery(r)]
            result.sort(key=lambda r: (r.canonical_task_id, r.created_at))
            return result

    def list_all(self) -> list[DurableExecutionRecord]:
        with self._global_lock:
            self._load()
            result = list(self._store.values())
            result.sort(key=lambda r: (r.canonical_task_id, r.created_at))
            return result

    def close(self) -> None:
        with self._global_lock:
            if not self._closed:
                try:
                    self._persist()
                except Exception:
                    pass
                self._closed = True


def _check_expected_revision(
    current: DurableExecutionRecord,
    expected_revision: int,
    expected_revision_token: str | None,
) -> None:
    if type(expected_revision) is not int or expected_revision < 1:
        raise StaleExecutionRevisionError(f"invalid expected_revision {expected_revision!r}")
    if current.record_revision != expected_revision:
        raise StaleExecutionRevisionError(
            f"stale revision: expected {expected_revision}, current {current.record_revision} "
            f"for {current.canonical_task_id}"
        )
    if expected_revision_token is not None and expected_revision_token != current.revision_token:
        raise StaleExecutionRevisionError(
            f"stale revision token for {current.canonical_task_id}"
        )


__all__ = [
    # Governance markers
    "DURABLE_EXECUTION_RECORD_IMPLEMENTED",
    "EXECUTION_STATE_STORE_PORT_IMPLEMENTED",
    "PRODUCTION_STORAGE_ENGINE_FROZEN",
    "FILE_BACKED_EXECUTION_STORE_ROLE",
    "FILE_BACKED_IS_PRODUCTION_DEFAULT",
    "EXECUTION_DURABLE_SCHEMA_VERSION",
    "JOURNAL_RECORD_ONTOLOGY_UNCHANGED",
    "DURABLE_IDEMPOTENCY_IMPLEMENTED",
    "CROSS_PROCESS_ATOMIC_TRANSACTION_GUARANTEED",
    "EXACTLY_ONCE_DISPATCH_CLAIMED",
    "DISPATCH_CRASH_WINDOW_DOCUMENTED",
    "TERMINAL_TRUTH_SEPARATE_FROM_DELIVERY",
    "ADAPTER_OBJECT_PERSISTED",
    "ORIGIN_SESSION_REF_IS_AUTHORITY",
    "CARD_POSSESSION_IS_AUTHORITY",
    "THIRD_CARD_ONTOLOGY_CREATED",
    "DELIVERY_COORDINATOR_IMPLEMENTED_IN_W1",
    # Vocabularies
    "ExecutionPhase",
    "DeliveryState",
    "parse_delivery_state",
    "OriginSessionRef",
    "card_digest_for",
    # Record
    "DurableExecutionRecord",
    "CAS_MUTABLE_EXECUTION_FIELDS",
    # Errors
    "EXECUTION_RECORD_NOT_FOUND",
    "STALE_EXECUTION_REVISION",
    "EXECUTION_PERSISTENCE_FAILURE",
    "EXECUTION_IDEMPOTENCY_CONFLICT",
    "ExecutionStateError",
    "ExecutionRecordNotFoundError",
    "StaleExecutionRevisionError",
    "ExecutionPersistenceFailureError",
    "ExecutionIdempotencyConflictError",
    # Store
    "ExecutionStateStore",
    "InMemoryExecutionStateStore",
    "FileBackedExecutionStateStore",
]
