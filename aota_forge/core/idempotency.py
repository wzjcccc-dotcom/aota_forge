"""M3-B6 canonical idempotency records and request fingerprints (Issue #9).

Implements the accepted M3-A3 idempotency contract:

* an idempotency record binds: idempotency_key, operation (contract identity),
  typed target, principal/authority context, canonical request fingerprint,
  committed result/effect identity, and the transaction outcome/state.
* the canonical request fingerprint is a deterministic SHA-256 digest of the
  canonical semantic payload (stable sorted JSON, minimal separators); it
  excludes unstable fields such as wall-clock request formatting, executor
  session IDs, and unordered mappings.
* same key + same fingerprint after a successful commit -> the committed result
  is reused, no duplicate semantic effect, no new lease consumption, no second
  revision advance (``SAME_KEY_SAME_REQUEST_DUPLICATE_EFFECT=no``,
  ``SAME_KEY_SAME_REQUEST_REUSES_COMMITTED_RESULT=yes``).
* same key + different fingerprint -> deterministic conflict, fail closed
  (``SAME_KEY_DIFFERENT_REQUEST=conflict``).
* a failed transaction is never recorded as a committed effect; its idempotency
  slot is either absent or explicitly marked failed so a retry may proceed
  (no key poisoning unless the accepted design requires a terminal state).

``IDEMPOTENCY_IMPLEMENTED=yes``.  This is durable dedupe metadata, never graph
authority: it never authorizes, selects, or participates in projections.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

IDEMPOTENCY_STATE_COMMITTED = "committed"
IDEMPOTENCY_STATE_FAILED = "failed"


def canonical_fingerprint(payload: object) -> str:
    """Deterministic canonical request fingerprint (SHA-256 hex).

    The payload is expected to be the canonical semantic request fields (typed
    target canonical refs, operation, expected revision, and the bounded
    semantic payload).  Unstable fields must be excluded by the caller.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotencyRecord:
    """Durable dedupe record binding a key to its committed request/effect.

    Fields:
      idempotency_key     — caller-supplied idempotency key (bounded token).
      operation           — contract identity bound to the key.
      target              — canonical typed target (ObjectRef serialized).
      principal           — bound principal identity (from trusted context).
      fingerprint         — canonical request fingerprint.
      state               — committed | failed.
      effect_refs         — canonical refs of the committed semantic effect
                            (e.g. the created child Subject) so a replay can
                            reconstruct the same result.
      revision_after      — owning Subject revision number after commit.
      revision_token      — owning Subject revision token after commit.
    """

    idempotency_key: str
    operation: str
    target: str
    principal: str
    fingerprint: str
    state: str
    effect_refs: tuple = ()
    revision_after: int | None = None
    revision_token: str | None = None

    def __post_init__(self) -> None:
        if self.state not in (IDEMPOTENCY_STATE_COMMITTED, IDEMPOTENCY_STATE_FAILED):
            raise ValueError("idempotency state must be committed|failed")


class IdempotencyStore:
    """Isolated, durable-for-tests idempotency registry (not production)."""

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}

    def get(self, key: str) -> IdempotencyRecord | None:
        return self._records.get(key)

    def put(self, record: IdempotencyRecord) -> None:
        self._records[record.idempotency_key] = record

    def committed(self, key: str) -> IdempotencyRecord | None:
        rec = self._records.get(key)
        if rec is not None and rec.state == IDEMPOTENCY_STATE_COMMITTED:
            return rec
        return None

    def __iter__(self):
        return iter(self._records.values())


__all__ = [
    "IDEMPOTENCY_STATE_COMMITTED",
    "IDEMPOTENCY_STATE_FAILED",
    "canonical_fingerprint",
    "IdempotencyRecord",
    "IdempotencyStore",
]
