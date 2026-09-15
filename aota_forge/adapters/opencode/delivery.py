"""OpenCode completion-delivery transport (AF #56 M2/W3).

Structural implementation of the executor-neutral
``aota_forge.runtime.completion.CompletionDeliveryTransport`` protocol over the
M2/W3 :class:`OpenCodeExactSessionReentry`. It produces bounded mechanical
transport evidence only: the identity-bound ACK decision stays in the existing
AF durable completion coordinator (204 accepted != AF ACK).
"""

from __future__ import annotations

from aota_forge.adapters.opencode.session_reentry import (
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_NOT_FOUND,
    OUTCOME_RETRYABLE,
    OUTCOME_UNKNOWN,
    OpenCodeExactSessionReentry,
)
from aota_forge.runtime.completion import (
    DeliveryAttemptEvidence,
    DeliveryTransportOutcome,
)

_OPENCODE_OUTCOME_MAP: dict[str, DeliveryTransportOutcome] = {
    OUTCOME_COMPLETED: DeliveryTransportOutcome.COMPLETED,
    OUTCOME_RETRYABLE: DeliveryTransportOutcome.RETRYABLE,
    OUTCOME_NOT_FOUND: DeliveryTransportOutcome.NOT_FOUND,
    OUTCOME_FAILED: DeliveryTransportOutcome.FAILED,
    OUTCOME_UNKNOWN: DeliveryTransportOutcome.UNKNOWN,
}


class OpenCodeCompletionDeliveryTransport:
    """One bounded exact-original-session delivery attempt per ``deliver()`` call."""

    def __init__(self, reentry: OpenCodeExactSessionReentry) -> None:
        if not isinstance(reentry, OpenCodeExactSessionReentry) and not hasattr(reentry, "reenter"):
            raise TypeError(
                "OpenCodeCompletionDeliveryTransport requires the OpenCode exact-session "
                f"reentry adapter, got {type(reentry).__name__}"
            )
        self._reentry = reentry

    def deliver(self, *, session_ref: str, envelope: str) -> DeliveryAttemptEvidence:
        """Attempt one exact-session re-entry with the bounded completion envelope.

        No fallback exists here: the reentry can only target the exact durable
        ``origin_session_ref`` session id and never creates a replacement
        parent on a miss.
        """
        try:
            result = self._reentry.reenter(session_ref, envelope)
        except Exception as exc:  # noqa: BLE001 - uncertainty is never an ACK
            return DeliveryAttemptEvidence(
                outcome=DeliveryTransportOutcome.FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )
        outcome = _OPENCODE_OUTCOME_MAP.get(result.outcome, DeliveryTransportOutcome.UNKNOWN)
        return DeliveryAttemptEvidence(
            outcome=outcome,
            response_text=result.response_excerpt,
            detail=result.error_code or result.error_message,
        )


__all__ = ["OpenCodeCompletionDeliveryTransport"]
