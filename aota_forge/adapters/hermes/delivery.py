"""Hermes completion-delivery transport (M2/W3) over the W2 exact-session seam.

Structural implementation of the executor-neutral
``aota_forge.runtime.completion.CompletionDeliveryTransport`` protocol, backed
ONLY by the accepted M2/W2 :class:`HermesExactSessionReentry`. It adds no
Hermes CLI construction of its own (§33): W3 delivery mechanics route through
the W2 adapter exactly as accepted.

Mechanical outcome mapping (transport evidence, never AF truth):

- ``completed``      W2 turn ran and returned bounded response text; whether
  this is an ACK is decided by the W3 coordinator from the response identity
  (§29) — never here.
- ``retryable``      busy / lease contention / bounded-wait timeout /
  temporary session coordination (W2 typed outcomes): completion stays
  PENDING durably; the origin session is never interrupted (§35).
- ``not_found``      the exact origin session is hard-missing: fail closed,
  no new session, no soft resume — W3 applies its bounded drop policy with
  terminal truth retained (§26/§34).
- ``failed``/``unknown`` conservative: keep pending, bounded attempt cap.
"""

from __future__ import annotations

from collections.abc import Callable

from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_NOT_FOUND,
    OUTCOME_RETRYABLE,
    OUTCOME_UNKNOWN,
    HermesExactSessionReentry,
    HermesSessionReentryError,
)
from aota_forge.runtime.completion import (
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
)

_HERMES_OUTCOME_MAP: dict[str, DeliveryTransportOutcome] = {
    OUTCOME_COMPLETED: DeliveryTransportOutcome.COMPLETED,
    OUTCOME_RETRYABLE: DeliveryTransportOutcome.RETRYABLE,
    OUTCOME_NOT_FOUND: DeliveryTransportOutcome.NOT_FOUND,
    OUTCOME_FAILED: DeliveryTransportOutcome.FAILED,
    OUTCOME_UNKNOWN: DeliveryTransportOutcome.UNKNOWN,
}


class HermesCompletionDeliveryTransport:
    """One bounded exact-session delivery attempt per ``deliver()`` call."""

    def __init__(
        self,
        reentry: HermesExactSessionReentry,
        *,
        surface_checker: Callable[[str], bool] | None = None,
    ) -> None:
        if not isinstance(reentry, HermesExactSessionReentry):
            raise TypeError(
                "HermesCompletionDeliveryTransport requires the accepted W2 "
                f"HermesExactSessionReentry adapter, got {type(reentry).__name__}"
            )
        self._reentry = reentry
        self._surface_checker = surface_checker

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence:
        """Attempt one exact-session re-entry with the bounded completion envelope.

        NO fallback of any kind is reachable from here: the W2 adapter can
        only target the exact id, never ``latest``/named/create-if-missing,
        and never launches an alternative session on a miss (fail closed).
        """
        if self._surface_checker is not None:
            try:
                surface_ok = self._surface_checker(session_ref)
            except Exception as exc:
                return DeliveryAttemptEvidence(
                    outcome=DeliveryTransportOutcome.FAILED,
                    detail=f"TOOL_SURFACE_PRECHECK_FAILED: {type(exc).__name__}: {exc}",
                )
            if not surface_ok:
                return DeliveryAttemptEvidence(
                    outcome=DeliveryTransportOutcome.FAILED,
                    detail="TOOL_SURFACE_PRECHECK_FAILED",
                )
        try:
            result = self._reentry.reenter(session_ref, envelope)
        except HermesSessionReentryError as exc:
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.FAILED,
                detail=str(exc),
            )
        except Exception as exc:
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )
        outcome = _HERMES_OUTCOME_MAP.get(result.outcome, DeliveryTransportOutcome.UNKNOWN)
        return DeliveryAttemptEvidence(
            outcome=outcome,
            response_text=result.response_excerpt,
            detail=result.error_code or result.error_message,
        )


__all__ = ["HermesCompletionDeliveryTransport"]
