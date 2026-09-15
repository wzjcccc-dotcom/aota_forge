"""Bounded OpenCode host client over the pinned legacy-root HTTP surface (AF #56 M2/W1).

Mechanical translation only: this module speaks the exact v1.18.30 legacy-root
HTTP contract (M1/W1 frozen evidence, M1 decision D09) and returns raw host
payloads to the adapter. It owns no AF semantics, no retry policy, no session
heuristics, and no OpenCode TypeScript internals.

Hard boundaries (AF #56 M2):

- EXACT SESSION IDS ONLY: every session-scoped operation addresses an explicit
  ``ses_*`` path parameter. There is no latest-session/newest-session/title
  match/directory-name guessing anywhere in this module.
- DIRECTORY SCOPE IS EXPLICIT: every directory(instance)-scoped operation
  requires an explicit absolute trusted directory argument and attaches it
  with the M1-proven ``?directory=`` mechanism. The ambient process CWD is
  never used deliberately (AMBIENT_CWD_IS_AUTHORITY=no). Session-scoped routes
  remain row-authoritative on the pinned server (M1 evidence: the session row
  directory wins), so the exact session identity can never be re-selected by a
  wrong directory.
- MECHANICAL FAILURES STAY TYPED/CONSERVATIVE: host unavailable, timeout,
  exact-session-not-found, malformed response, protocol violation, retriable
  temporary host failure, and dispatch rejection are distinct bounded error
  classes. Unknown shapes never become success.
- HTTP/JSON only: no OpenCode internals are imported; the injectable
  transport seam is the only I/O surface (tests inject a fake).

The default transport uses only the Python standard library (urllib) exactly
as the accepted M1 probe did, so the adapter has zero third-party runtime
dependencies.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Governance markers (truthful)
# ---------------------------------------------------------------------------

OPENCODE_HTTP_SURFACE = "legacy_root"
OPENCODE_SESSION_ID_PREFIX = "ses"
EXACT_SESSION_IDS_ONLY = True
AMBIENT_CWD_IS_AUTHORITY = False
DIRECTORY_SCOPE_EXPLICIT = True
LATEST_SESSION_HEURISTIC_PRESENT = False
NEWEST_SESSION_HEURISTIC_PRESENT = False
TITLE_MATCH_HEURISTIC_PRESENT = False
DIRECTORY_NAME_GUESSING_PRESENT = False
OPENCODE_INTERNALS_IMPORTED = False
RETRY_POLICY_OWNS_ADAPTER = False

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_EVENT_TIMEOUT_SECONDS = 5.0

# Status codes classified as bounded temporary host failure (conservative:
# retryable by the caller, never a fabricated terminal state).
_RETRIABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
# Request-level rejections (dispatch/session-create/prompt rejected by host).
_REJECTION_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 403, 405, 409, 422})


class OpenCodeHostError(Exception):
    """Base class for bounded OpenCode host client failures."""

    code = "OPENCODE_HOST_ERROR"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.message = message
        self.status = status
        super().__init__(f"{self.code}: {message}" if self.code else message)


class OpenCodeHostUnavailableError(OpenCodeHostError):
    """The host endpoint is unreachable (connection refused/DNS/TLS)."""

    code = "EXECUTOR_UNAVAILABLE"


class OpenCodeTimeoutError(OpenCodeHostError):
    """A bounded host call exceeded its timeout."""

    code = "HOST_TIMEOUT"


class OpenCodeSessionNotFoundError(OpenCodeHostError):
    """The exact requested session does not exist (404); never a heuristic miss."""

    code = "SESSION_NOT_FOUND"


class OpenCodeMalformedResponseError(OpenCodeHostError):
    """The host answered with a shape the pinned contract does not allow."""

    code = "MALFORMED_RESPONSE"


class OpenCodeProtocolViolationError(OpenCodeHostError):
    """The host answered with an unexpected status/protocol shape for the call."""

    code = "ADAPTER_PROTOCOL_ERROR"


class OpenCodeRetriableHostError(OpenCodeHostError):
    """A bounded temporary host failure (5xx/429/...): retryable, never terminal."""

    code = "RETRIABLE_HOST_FAILURE"


class OpenCodeDispatchRejectedError(OpenCodeHostError):
    """Session create / prompt submission was rejected by the host."""

    code = "DISPATCH_REJECTED"


# ---------------------------------------------------------------------------
# Injectable transport seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenCodeHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class OpenCodeHttpTransport(Protocol):
    """Minimal injectable HTTP/SSE transport (tests inject a fake)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        timeout: float,
    ) -> OpenCodeHttpResponse:
        """Perform ONE bounded HTTP request; raise typed transport errors."""
        ...

    def stream_events(
        self,
        url: str,
        *,
        timeout: float,
        max_events: int,
    ) -> list[dict[str, Any]]:
        """Read bounded SSE events (may return fewer on timeout); no replay."""
        ...


class UrllibOpenCodeHttpTransport:
    """Default stdlib transport (no third-party dependency)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        timeout: float,
    ) -> OpenCodeHttpResponse:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return OpenCodeHttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # noqa: BLE001 - body unavailable
                payload = b""
            return OpenCodeHttpResponse(
                status=int(exc.code),
                body=payload,
                headers=dict(exc.headers.items()) if exc.headers is not None else {},
            )
        except TimeoutError as exc:
            raise OpenCodeTimeoutError(f"request timed out: {url}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise OpenCodeTimeoutError(f"request timed out: {url}") from exc
            raise OpenCodeHostUnavailableError(f"host unreachable: {url}: {reason}") from exc
        except OSError as exc:
            raise OpenCodeHostUnavailableError(f"host unreachable: {url}: {exc}") from exc

    def stream_events(
        self,
        url: str,
        *,
        timeout: float,
        max_events: int,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        parsed = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        events.append(parsed)
                    if len(events) >= max_events:
                        break
        except TimeoutError:
            return events
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                return events
            raise OpenCodeHostUnavailableError(f"event stream unreachable: {url}: {reason}") from exc
        except OSError:
            return events
        return events


# ---------------------------------------------------------------------------
# Mechanical parsing helpers (pinned legacy-root shapes only)
# ---------------------------------------------------------------------------


def _require_session_id(value: object, *, label: str = "session_id") -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise OpenCodeMalformedResponseError(f"{label} must be a string, got {type(value).__name__}")
    sid = value.strip()
    if not sid:
        raise OpenCodeMalformedResponseError(f"{label} must be non-empty")
    if not sid.startswith(OPENCODE_SESSION_ID_PREFIX):
        raise OpenCodeMalformedResponseError(
            f"{label} {sid!r} does not carry the pinned 'ses' session id form"
        )
    return sid


def _require_directory(directory: object) -> str:
    """Explicit trusted directory scope; absolute, bounded, never model supplied."""
    if not isinstance(directory, str) or type(directory) is not str:
        raise ValueError(f"directory must be an explicit string, got {type(directory).__name__}")
    value = directory.strip()
    if not value:
        raise ValueError("directory must be a non-empty explicit path")
    if not value.startswith("/"):
        raise ValueError(f"directory must be an absolute path, got {value!r}")
    if any(ch == "\x00" for ch in value):
        raise ValueError("directory must not contain NUL")
    return value


def _directory_query(directory: str) -> str:
    return urllib.parse.urlencode({"directory": directory})


def parse_session_info(payload: object) -> dict[str, Any]:
    """Validate the frozen session-create/get shape; fail closed on drift."""
    if not isinstance(payload, Mapping):
        raise OpenCodeMalformedResponseError(
            f"session payload must be an object, got {type(payload).__name__}"
        )
    session_id = _require_session_id(payload.get("id"), label="session.id")
    directory = payload.get("directory")
    if not isinstance(directory, str) or not directory.strip():
        raise OpenCodeMalformedResponseError("session payload missing a non-empty 'directory'")
    parent_id = payload.get("parentID")
    if parent_id is not None:
        _require_session_id(parent_id, label="session.parentID")
    return dict(payload)


def parse_status_map(payload: object) -> dict[str, dict[str, Any]]:
    """Validate GET /session/status -> Record<sessionID, status>; fail closed."""
    if not isinstance(payload, Mapping):
        raise OpenCodeMalformedResponseError(
            f"status payload must be an object, got {type(payload).__name__}"
        )
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            raise OpenCodeMalformedResponseError("status payload contains a non-object entry")
        status_type = value.get("type")
        if not isinstance(status_type, str) or not status_type.strip():
            raise OpenCodeMalformedResponseError(
                f"status entry for {key!r} missing a string 'type'"
            )
        result[key] = dict(value)
    return result


def session_status_entry(
    status_map: Mapping[str, Mapping[str, Any]], session_id: str
) -> dict[str, Any] | None:
    """Exact session status entry (idle sessions are omitted by the pinned host).

    ``None`` means "not present in the active status map" == host-observed idle.
    This is a mechanical observation only; it is never semantic completion.
    """
    entry = status_map.get(session_id)
    if entry is None:
        return None
    return dict(entry)


def parse_message_list(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise OpenCodeMalformedResponseError(
            f"message list must be an array, got {type(payload).__name__}"
        )
    messages: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise OpenCodeMalformedResponseError("message list contains a non-object entry")
        messages.append(dict(item))
    return messages


def message_info(message: Mapping[str, Any]) -> dict[str, Any]:
    info = message.get("info")
    return dict(info) if isinstance(info, Mapping) else {}


def message_parts(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    parts = message.get("parts")
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)):
        return []
    return [dict(part) for part in parts if isinstance(part, Mapping)]


def message_role(message: Mapping[str, Any]) -> str | None:
    role = message_info(message).get("role")
    return role if isinstance(role, str) else None


def message_finish(message: Mapping[str, Any]) -> str | None:
    finish = message_info(message).get("finish")
    return finish if isinstance(finish, str) else None


def message_error_name(message: Mapping[str, Any]) -> str | None:
    error = message_info(message).get("error")
    if not isinstance(error, Mapping):
        return None
    name = error.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "UNKNOWN_MESSAGE_ERROR"


def message_text_parts(message: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for part in message_parts(message):
        if part.get("type") != "text":
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


@dataclass(frozen=True)
class TurnEvidence:
    """Bounded mechanical evidence about the last assistant turn of an exact session."""

    message_count: int
    last_assistant_index: int | None
    last_assistant_finish: str | None
    last_assistant_error: str | None
    assistant_text: str | None

    @property
    def has_assistant(self) -> bool:
        return self.last_assistant_index is not None

    @property
    def terminal_success(self) -> bool:
        """The dispatched turn terminated normally (finish=stop, no error).

        Mechanical host evidence ONLY; semantic success still requires the AF
        governed task.return gate downstream (MECHANICAL_TERMINAL_SUCCESS !=
        AF_SEMANTIC_SUCCESS).
        """
        return self.last_assistant_error is None and self.last_assistant_finish == "stop"

    @property
    def terminal_error(self) -> bool:
        return self.last_assistant_error is not None

    @property
    def aborted(self) -> bool:
        return bool(self.last_assistant_error and "abort" in self.last_assistant_error.lower())


def turn_evidence(messages: Sequence[Mapping[str, Any]]) -> TurnEvidence:
    """Extract bounded mechanical evidence from an exact-session message list."""
    last_assistant_index: int | None = None
    finish: str | None = None
    error_name: str | None = None
    text: str | None = None
    for index, message in enumerate(messages):
        if message_role(message) != "assistant":
            continue
        last_assistant_index = index
        finish = message_finish(message)
        error_name = message_error_name(message)
        collected = message_text_parts(message)
        text = "\n".join(collected) if collected else None
    return TurnEvidence(
        message_count=len(messages),
        last_assistant_index=last_assistant_index,
        last_assistant_finish=finish,
        last_assistant_error=error_name,
        assistant_text=text,
    )


_ACK_TOKEN = "AOTA_COMPLETION_ACK_V1"


def assistant_texts_since(
    messages: Sequence[Mapping[str, Any]], start_index: int
) -> list[str]:
    """Assistant text parts for messages after ``start_index`` (bounded)."""
    texts: list[str] = []
    for message in messages[start_index:]:
        if message_role(message) != "assistant":
            continue
        texts.extend(message_text_parts(message))
    return texts


def contains_ack_token(texts: Sequence[str]) -> bool:
    return any(_ACK_TOKEN in text for text in texts)


# ---------------------------------------------------------------------------
# Host client
# ---------------------------------------------------------------------------


class OpenCodeHostClient:
    """Mechanical legacy-root client for one pinned OpenCode reference host.

    ``base_url`` is operator-owned deployment truth (loopback base URL from the
    trusted RuntimeConfig ``host_endpoint``); nothing here accepts a model or
    TaskHandoff supplied endpoint.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_transport: OpenCodeHttpTransport | None = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty operator-owned base URL")
        self._base_url = base_url.strip().rstrip("/")
        if not self._base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an http(s) base URL, got {base_url!r}")
        if http_transport is not None and not hasattr(http_transport, "request"):
            raise TypeError("http_transport must implement request()/stream_events()")
        self._transport: OpenCodeHttpTransport = (
            http_transport if http_transport is not None else UrllibOpenCodeHttpTransport()
        )
        if not isinstance(request_timeout_seconds, (int, float)) or isinstance(
            request_timeout_seconds, bool
        ):
            raise TypeError("request_timeout_seconds must be numeric")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self._request_timeout_seconds = float(request_timeout_seconds)

    @property
    def base_url(self) -> str:
        return self._base_url

    # -- mechanical request core ---------------------------------------------

    def _url(self, path: str, directory: str | None) -> str:
        if directory is None:
            return f"{self._base_url}{path}"
        joiner = "&" if "?" in path else "?"
        return f"{self._base_url}{path}{joiner}{_directory_query(directory)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        directory: str | None,
        body: Mapping[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        session_scoped: bool = False,
        rejection_statuses: frozenset[int] | None = None,
        timeout: float | None = None,
    ) -> OpenCodeHttpResponse:
        encoded = (
            json.dumps(dict(body), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        url = self._url(path, directory)
        response = self._transport.request(
            method,
            url,
            body=encoded,
            timeout=self._request_timeout_seconds if timeout is None else float(timeout),
        )
        if response.status in expected:
            return response
        if response.status == 404 and session_scoped:
            raise OpenCodeSessionNotFoundError(
                f"exact session missing for {path}", status=404
            )
        rejections = _REJECTION_HTTP_STATUSES if rejection_statuses is None else rejection_statuses
        if response.status in rejections:
            raise OpenCodeDispatchRejectedError(
                f"host rejected {method} {path} with status {response.status}",
                status=response.status,
            )
        if response.status in _RETRIABLE_HTTP_STATUSES or response.status >= 500:
            raise OpenCodeRetriableHostError(
                f"temporary host failure for {method} {path}: status {response.status}",
                status=response.status,
            )
        raise OpenCodeProtocolViolationError(
            f"unexpected status {response.status} for {method} {path}",
            status=response.status,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        directory: str | None,
        body: Mapping[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        session_scoped: bool = False,
        rejection_statuses: frozenset[int] | None = None,
        allow_empty: bool = False,
    ) -> Any:
        response = self._request(
            method,
            path,
            directory=directory,
            body=body,
            expected=expected,
            session_scoped=session_scoped,
            rejection_statuses=rejection_statuses,
        )
        if not response.body:
            if allow_empty:
                return None
            raise OpenCodeMalformedResponseError(f"empty response body for {method} {path}")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCodeMalformedResponseError(
                f"response body is not JSON for {method} {path}: {type(exc).__name__}"
            ) from exc

    # -- bounded operations ---------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Global health endpoint (not directory-scoped)."""
        payload = self._request_json("GET", "/global/health", directory=None)
        if not isinstance(payload, Mapping):
            raise OpenCodeMalformedResponseError("health payload must be an object")
        return dict(payload)

    def create_session(
        self,
        *,
        directory: str,
        parent_id: str | None = None,
        title: str | None = None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        permission: Sequence[Mapping[str, Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /session?directory=... with the trusted AF-bound directory."""
        scope = _require_directory(directory)
        body: dict[str, Any] = {}
        if parent_id is not None:
            body["parentID"] = _require_session_id(parent_id, label="parent_id")
        if title is not None:
            if not isinstance(title, str) or not title.strip():
                raise ValueError("title must be a non-empty string when provided")
            body["title"] = title
        if agent is not None:
            if not isinstance(agent, str) or not agent.strip():
                raise ValueError("agent must be a non-empty string when provided")
            body["agent"] = agent
        if model is not None:
            if not isinstance(model, Mapping):
                raise TypeError("model must be a mapping when provided")
            body["model"] = dict(model)
        if permission is not None:
            body["permission"] = [dict(item) for item in permission]
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise TypeError("metadata must be a mapping when provided")
            body["metadata"] = dict(metadata)
        payload = self._request_json(
            "POST",
            "/session",
            directory=scope,
            body=body,
            expected=(200, 201),
            rejection_statuses=frozenset({400, 401, 403, 409, 422}),
        )
        return parse_session_info(payload)

    def get_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        scope_hint: str | None = None,
    ) -> dict[str, Any]:
        """GET /session/:id — exact session ONLY (404 => typed not-found).

        The route is session-scoped: the pinned server resolves the persisted
        session row and the row directory wins over any query scope (M1
        evidence). ``scope_hint`` is the trusted AF-bound directory when the
        caller already holds it; it routes the instance deterministically and
        is never used to select a session.
        """
        sid = _require_session_id(session_id)
        scope = _require_directory(directory) if directory is not None else None
        if scope is None and scope_hint is not None:
            scope = _require_directory(scope_hint)
        payload = self._request_json(
            "GET", f"/session/{urllib.parse.quote(sid)}", directory=scope, session_scoped=True
        )
        return parse_session_info(payload)

    def children(self, session_id: str, *, directory: str) -> list[dict[str, Any]]:
        sid = _require_session_id(session_id)
        scope = _require_directory(directory)
        payload = self._request_json(
            "GET",
            f"/session/{urllib.parse.quote(sid)}/children",
            directory=scope,
            session_scoped=True,
        )
        if not isinstance(payload, list):
            raise OpenCodeMalformedResponseError("children payload must be an array")
        return [parse_session_info(item) for item in payload]

    def submit_prompt_async(
        self,
        session_id: str,
        *,
        directory: str,
        parts: Sequence[Mapping[str, Any]],
        model: Mapping[str, Any] | None = None,
        agent: str | None = None,
        system: str | None = None,
    ) -> None:
        """POST /session/:id/prompt_async — non-blocking accepted delivery (204).

        A 204 proves only that the host accepted and persisted the prompt (M1
        D12: accepted non-blocking, joins the in-flight run). It is NEVER an AF
        ACK.
        """
        sid = _require_session_id(session_id)
        scope = _require_directory(directory)
        if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)) or not parts:
            raise ValueError("parts must be a non-empty sequence of part mappings")
        normalized = [dict(part) for part in parts if isinstance(part, Mapping)]
        if len(normalized) != len(list(parts)):
            raise TypeError("parts members must be mappings")
        body: dict[str, Any] = {"parts": normalized}
        if model is not None:
            if not isinstance(model, Mapping):
                raise TypeError("model must be a mapping when provided")
            body["model"] = dict(model)
        if agent is not None:
            if not isinstance(agent, str) or not agent.strip():
                raise ValueError("agent must be a non-empty string when provided")
            body["agent"] = agent
        if system is not None:
            if not isinstance(system, str) or not system.strip():
                raise ValueError("system must be a non-empty string when provided")
            body["system"] = system
        self._request(
            "POST",
            f"/session/{urllib.parse.quote(sid)}/prompt_async",
            directory=scope,
            body=body,
            expected=(204,),
            session_scoped=True,
            rejection_statuses=frozenset({400, 401, 403, 409, 422}),
        )

    def query_status(self, *, directory: str) -> dict[str, dict[str, Any]]:
        """GET /session/status?directory=... — instance(directory)-scoped map."""
        scope = _require_directory(directory)
        payload = self._request_json("GET", "/session/status", directory=scope)
        return parse_status_map(payload)

    def session_status(self, session_id: str, *, directory: str) -> dict[str, Any] | None:
        """Exact session status entry; ``None`` == host-observed idle (omitted)."""
        sid = _require_session_id(session_id)
        status_map = self.query_status(directory=directory)
        return session_status_entry(status_map, sid)

    def fetch_session_messages(self, session_id: str, *, directory: str) -> list[dict[str, Any]]:
        """GET /session/:id/message?directory=... — exact session messages only."""
        sid = _require_session_id(session_id)
        scope = _require_directory(directory)
        payload = self._request_json(
            "GET",
            f"/session/{urllib.parse.quote(sid)}/message",
            directory=scope,
            session_scoped=True,
        )
        return parse_message_list(payload)

    def abort_session(self, session_id: str, *, directory: str) -> bool:
        """POST /session/:id/abort — returns the host boolean.

        The pinned host answers ``true`` even for an unknown id, so callers must
        pair this with an exact-session existence check / observed idle state;
        this method itself is a mechanical call only.
        """
        sid = _require_session_id(session_id)
        scope = _require_directory(directory)
        payload = self._request_json(
            "POST",
            f"/session/{urllib.parse.quote(sid)}/abort",
            directory=scope,
            expected=(200,),
            session_scoped=True,
            rejection_statuses=frozenset({400, 401, 403, 405, 422}),
        )
        if type(payload) is not bool:
            raise OpenCodeMalformedResponseError("abort response must be a boolean")
        return payload

    def observe_events(
        self,
        *,
        directory: str,
        timeout_seconds: float = DEFAULT_EVENT_TIMEOUT_SECONDS,
        max_events: int = 64,
    ) -> list[dict[str, Any]]:
        """Bounded, live-only SSE observation with the AF-bound directory scope.

        Doorbell evidence only: no replay, no queue semantics (M1: GET /event is
        live-only; event stream is never durable AF truth).
        """
        scope = _require_directory(directory)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be numeric")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be an int >= 1")
        url = self._url("/event", scope)
        events = self._transport.stream_events(
            url, timeout=float(timeout_seconds), max_events=int(max_events)
        )
        if not isinstance(events, list):
            raise OpenCodeMalformedResponseError("event stream returned a non-list payload")
        bounded: list[dict[str, Any]] = []
        for event in events[:max_events]:
            if not isinstance(event, Mapping):
                raise OpenCodeMalformedResponseError("event stream contains a non-object frame")
            bounded.append(dict(event))
        return bounded

    def probe_versions(self) -> dict[str, Any]:
        """Bounded advisory host metadata for evidence (never dispatch input)."""
        payload = self.health()
        version = payload.get("version")
        return {"healthy": bool(payload.get("healthy")), "version": version if isinstance(version, str) else None}


__all__ = [
    "AMBIENT_CWD_IS_AUTHORITY",
    "DEFAULT_EVENT_TIMEOUT_SECONDS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DIRECTORY_SCOPE_EXPLICIT",
    "EXACT_SESSION_IDS_ONLY",
    "LATEST_SESSION_HEURISTIC_PRESENT",
    "NEWEST_SESSION_HEURISTIC_PRESENT",
    "OPENCODE_HTTP_SURFACE",
    "OPENCODE_INTERNALS_IMPORTED",
    "OPENCODE_SESSION_ID_PREFIX",
    "TITLE_MATCH_HEURISTIC_PRESENT",
    "TurnEvidence",
    "UrllibOpenCodeHttpTransport",
    "OpenCodeDispatchRejectedError",
    "OpenCodeHostClient",
    "OpenCodeHostError",
    "OpenCodeHostUnavailableError",
    "OpenCodeHttpResponse",
    "OpenCodeHttpTransport",
    "OpenCodeMalformedResponseError",
    "OpenCodeProtocolViolationError",
    "OpenCodeRetriableHostError",
    "OpenCodeSessionNotFoundError",
    "OpenCodeTimeoutError",
    "assistant_texts_since",
    "contains_ack_token",
    "message_error_name",
    "message_finish",
    "message_info",
    "message_parts",
    "message_role",
    "message_text_parts",
    "parse_message_list",
    "parse_session_info",
    "parse_status_map",
    "session_status_entry",
    "turn_evidence",
]
