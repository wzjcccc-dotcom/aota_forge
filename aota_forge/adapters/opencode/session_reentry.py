"""OpenCode EXACT-original-session completion re-entry (AF #56 M2/W3).

Mechanical re-entry into the exact original task-main OpenCode session with the
bounded AF completion envelope, followed by a bounded wait for the exact
session's identity-bound reconciliation evidence.

Hard boundaries (AF #56 M2):

- EXACT SESSION ONLY: the target is the durable ``origin_session_ref`` session
  id. There is no create-on-miss, no latest/newest/replacement/same-title/
  same-directory session selection anywhere (CREATE_PARENT_ON_MISS=no).
- DIRECTORY SCOPE: the exact session row's persisted directory (M1-proven
  directory(instance)-scoped observation) is used for every directory-scoped
  call. The directory is mechanical routing context derived from the exact
  session itself; it is never semantic identity and never model supplied.
- prompt_async is the primary mechanical delivery path (M1 D12: accepted
  non-blocking; a busy parent absorbs the prompt into the in-flight run).
  Blocking ``/message`` is NOT used just to wait for completion.
- 204 ACCEPTED IS NOT AN AF ACK: this module only reports that the host
  accepted the delivery and what bounded reconciliation evidence the exact
  session produced afterwards. The identity-bound ACK decision belongs to the
  existing AF completion coordinator.
- Missing exact session -> ``not_found`` truthfully; no replacement session is
  ever fabricated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


from aota_forge.adapters.opencode.host_client import (
    OpenCodeDispatchRejectedError,
    OpenCodeHostClient,
    OpenCodeHostError,
    OpenCodeHostUnavailableError,
    OpenCodeMalformedResponseError,
    OpenCodeProtocolViolationError,
    OpenCodeRetriableHostError,
    OpenCodeSessionNotFoundError,
    OpenCodeTimeoutError,
    assistant_texts_since,
    contains_ack_token,
    message_role,
)
from aota_forge.adapters.opencode.profiles import (
    require_exact_profile,
    task_main_profile_default,
)

# Bounded outcome vocabulary (structurally aligned with the executor-neutral
# delivery outcome classes used by the AF completion coordinator: no new
# transport ontology is created).
OUTCOME_COMPLETED = "completed"
OUTCOME_RETRYABLE = "retryable"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"

DEFAULT_REENTRY_TIMEOUT_SECONDS = 120.0
DEFAULT_REENTRY_POLL_SECONDS = 0.5
MAX_RESPONSE_EXCERPT_CHARS = 16 * 1024

# Truthful markers.
EXACT_SESSION_REENTRY_ONLY = True
CREATE_PARENT_ON_MISS = False
FABRICATED_REPLACEMENT_PARENT = False
BLOCKING_MESSAGE_USED_TO_WAIT = False
HTTP_ACCEPTED_IS_ACK = False


class OpenCodeSessionReentryError(Exception):
    """Bounded mechanical re-entry failure (never a fabricated ACK)."""

    def __init__(self, message: str, code: str = "SESSION_REENTRY_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OpenCodeReentryResult:
    """Mechanical outcome of one bounded exact-session re-entry attempt."""

    outcome: str
    session_id: str
    directory: str | None
    accepted: bool
    ack_observed: bool
    response_excerpt: str | None
    error_code: str | None = None
    error_message: str | None = None
    observed_messages: int = 0


class OpenCodeExactSessionReentry:
    """One bounded exact-original-session re-entry per ``reenter()`` call."""

    def __init__(
        self,
        host_client: OpenCodeHostClient,
        *,
        timeout_seconds: float = DEFAULT_REENTRY_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_REENTRY_POLL_SECONDS,
        default_model: dict[str, str] | None = None,
        agent: str | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if host_client is None or not hasattr(host_client, "get_session"):
            raise TypeError("OpenCodeExactSessionReentry requires an OpenCodeHostClient")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be numeric")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(poll_interval_seconds, (int, float)) or isinstance(
            poll_interval_seconds, bool
        ):
            raise TypeError("poll_interval_seconds must be numeric")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if default_model is not None:
            if (
                not isinstance(default_model, dict)
                or not isinstance(default_model.get("providerID"), str)
                or not isinstance(default_model.get("modelID"), str)
            ):
                raise ValueError("default_model must be {providerID, modelID} or None")
        # AF #58 M1: completion re-entry targets the exact task-main parent
        # session, so the re-entry prompt must carry the exact task-main host
        # profile (message-time agent is authoritative; a profile-less prompt
        # would otherwise be handled by the host default agent).
        self._agent = (
            task_main_profile_default()
            if agent is None
            else require_exact_profile(agent, label="task-main profile")
        )
        self._host = host_client
        self._timeout_seconds = float(timeout_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._default_model = dict(default_model) if default_model else None
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn

    def reenter(self, session_id: str, envelope: str) -> OpenCodeReentryResult:
        """Deliver the bounded envelope to the EXACT session and observe reconciliation.

        Returns mechanical evidence only; ``completed`` means the exact
        session produced bounded reconciliation text (the coordinator still
        decides whether it is an identity-bound ACK).
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty exact session id")
        if not isinstance(envelope, str) or not envelope.strip():
            raise ValueError("envelope must be a non-empty bounded string")

        # 1. Exact session existence + exact row directory (no heuristics).
        try:
            session = self._host.get_session(session_id)
        except OpenCodeSessionNotFoundError:
            return OpenCodeReentryResult(
                outcome=OUTCOME_NOT_FOUND,
                session_id=session_id,
                directory=None,
                accepted=False,
                ack_observed=False,
                response_excerpt=None,
                error_code="SESSION_NOT_FOUND",
                error_message="exact original session is missing; no replacement created",
            )
        except OpenCodeTimeoutError as exc:
            return self._failure(OUTCOME_RETRYABLE, session_id, "HOST_TIMEOUT", str(exc))
        except OpenCodeHostUnavailableError as exc:
            return self._failure(OUTCOME_RETRYABLE, session_id, "EXECUTOR_UNAVAILABLE", str(exc))
        except OpenCodeRetriableHostError as exc:
            return self._failure(OUTCOME_RETRYABLE, session_id, "RETRIABLE_HOST_FAILURE", str(exc))
        except (OpenCodeMalformedResponseError, OpenCodeProtocolViolationError) as exc:
            return self._failure(OUTCOME_UNKNOWN, session_id, exc.code, str(exc))
        except OpenCodeHostError as exc:
            return self._failure(OUTCOME_UNKNOWN, session_id, exc.code, str(exc))

        directory = session.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            return self._failure(
                OUTCOME_UNKNOWN,
                session_id,
                "MALFORMED_RESPONSE",
                "exact session row is missing its persisted directory scope",
            )

        # 2. Baseline exact-session messages before delivery.
        try:
            baseline = self._host.fetch_session_messages(session_id, directory=directory)
        except OpenCodeSessionNotFoundError:
            return self._not_found(session_id)
        except OpenCodeHostError as exc:
            return self._failure(OUTCOME_RETRYABLE, session_id, exc.code, str(exc))
        baseline_count = len(baseline)

        # 3. Non-blocking delivery into the exact session (busy parent joins
        #    its in-flight run; pinned M1 behavior).
        try:
            self._host.submit_prompt_async(
                session_id,
                directory=directory,
                parts=[{"type": "text", "text": envelope}],
                model=self._default_model,
                agent=self._agent,
            )
        except OpenCodeSessionNotFoundError:
            return self._not_found(session_id)
        except OpenCodeDispatchRejectedError as exc:
            return self._failure(
                OUTCOME_RETRYABLE,
                session_id,
                "DISPATCH_REJECTED",
                f"exact session rejected the completion delivery: {exc}",
                directory=directory,
            )
        except (OpenCodeTimeoutError, OpenCodeHostUnavailableError) as exc:
            return self._failure(
                OUTCOME_RETRYABLE, session_id, "TRANSPORT_UNCERTAIN", str(exc), directory=directory
            )
        except OpenCodeRetriableHostError as exc:
            return self._failure(
                OUTCOME_RETRYABLE, session_id, "RETRIABLE_HOST_FAILURE", str(exc), directory=directory
            )
        except OpenCodeHostError as exc:
            return self._failure(OUTCOME_FAILED, session_id, exc.code, str(exc), directory=directory)

        # 4. Bounded wait for the EXACT session's reconciliation evidence.
        deadline = self._now_fn() + self._timeout_seconds
        collected: list[str] = []
        observed_messages = baseline_count
        last_transport_error: str | None = None
        while True:
            self._sleep_fn(self._poll_interval_seconds)
            try:
                messages = self._host.fetch_session_messages(session_id, directory=directory)
            except OpenCodeSessionNotFoundError:
                return self._not_found(session_id)
            except OpenCodeHostError as exc:
                last_transport_error = f"{getattr(exc, 'code', 'HOST_ERROR')}: {exc}"
                if self._now_fn() >= deadline:
                    return self._failure(
                        OUTCOME_RETRYABLE,
                        session_id,
                        "TRANSPORT_UNCERTAIN",
                        last_transport_error,
                        directory=directory,
                    )
                continue
            observed_messages = max(observed_messages, len(messages))
            collected = assistant_texts_since(messages, baseline_count)
            if contains_ack_token(collected):
                return OpenCodeReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    session_id=session_id,
                    directory=directory,
                    accepted=True,
                    ack_observed=True,
                    response_excerpt=self._excerpt(collected),
                    observed_messages=observed_messages,
                )
            # Early exit when the exact session finished the accepted delivery
            # opportunity without emitting reconciliation evidence: keep the
            # outcome truthful (accepted, not acked) instead of burning the
            # full bounded timeout.
            status_confirmed_idle = False
            try:
                entry = self._host.session_status(session_id, directory=directory)
                status_confirmed_idle = entry is None
            except OpenCodeHostError:
                status_confirmed_idle = False
            new_assistant_seen = any(
                message_role(message) == "assistant" for message in messages[baseline_count:]
            )
            if status_confirmed_idle and new_assistant_seen:
                return OpenCodeReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    session_id=session_id,
                    directory=directory,
                    accepted=True,
                    ack_observed=False,
                    response_excerpt=self._excerpt(collected) if collected else None,
                    error_code="ACK_NOT_OBSERVED",
                    error_message="exact session finished without identity-bound reconciliation text",
                    observed_messages=observed_messages,
                )
            if self._now_fn() >= deadline:
                return OpenCodeReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    session_id=session_id,
                    directory=directory,
                    accepted=True,
                    ack_observed=False,
                    response_excerpt=self._excerpt(collected) if collected else None,
                    error_code="ACK_NOT_OBSERVED",
                    error_message="bounded delivery window expired without reconciliation evidence",
                    observed_messages=observed_messages,
                )

    # -- bounded helpers -------------------------------------------------------

    @staticmethod
    def _excerpt(texts: list[str]) -> str | None:
        if not texts:
            return None
        joined = "\n".join(texts)
        if len(joined) > MAX_RESPONSE_EXCERPT_CHARS:
            joined = joined[:MAX_RESPONSE_EXCERPT_CHARS]
        return joined

    def _not_found(self, session_id: str) -> OpenCodeReentryResult:
        return OpenCodeReentryResult(
            outcome=OUTCOME_NOT_FOUND,
            session_id=session_id,
            directory=None,
            accepted=False,
            ack_observed=False,
            response_excerpt=None,
            error_code="SESSION_NOT_FOUND",
            error_message="exact original session is missing; no replacement created",
        )

    def _failure(
        self,
        outcome: str,
        session_id: str,
        code: str,
        message: str,
        *,
        directory: str | None = None,
    ) -> OpenCodeReentryResult:
        return OpenCodeReentryResult(
            outcome=outcome,
            session_id=session_id,
            directory=directory,
            accepted=False,
            ack_observed=False,
            response_excerpt=None,
            error_code=code,
            error_message=message[:512],
        )


__all__ = [
    "BLOCKING_MESSAGE_USED_TO_WAIT",
    "CREATE_PARENT_ON_MISS",
    "DEFAULT_REENTRY_POLL_SECONDS",
    "DEFAULT_REENTRY_TIMEOUT_SECONDS",
    "EXACT_SESSION_REENTRY_ONLY",
    "FABRICATED_REPLACEMENT_PARENT",
    "HTTP_ACCEPTED_IS_ACK",
    "MAX_RESPONSE_EXCERPT_CHARS",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED",
    "OUTCOME_NOT_FOUND",
    "OUTCOME_RETRYABLE",
    "OUTCOME_UNKNOWN",
    "OpenCodeExactSessionReentry",
    "OpenCodeReentryResult",
    "OpenCodeSessionReentryError",
]
