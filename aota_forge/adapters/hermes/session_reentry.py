"""Hermes-specific exact session re-entry adapter (M2/W2).

Narrow mechanical seam: deliver a bounded text payload into ONE exactly
identified Hermes session via the source-confirmed v0.21 CLI contract:

    hermes [-p <profile>] chat -Q --resume <EXACT_SESSION_ID> --query-file <PATH>

Flags verified against installed Hermes v0.21.0 (commit 006b1be):
``chat`` subparser accepts ``-Q/--quiet``, ``--resume <id>`` (exact id or
title; ``latest`` supported by HERMES but structurally forbidden here), and
``--query-file`` (mutually exclusive with ``-q``).  A resumed ``-Q`` run
executes exactly one turn and exits (cli.py ``_run_quiet_single_query``):
exit 0 completed, exit 1 failed (session-not-found / lease timeout / init),
exit 130 interrupted.  A missing exact session fails closed in Hermes itself
(``Session not found`` + exit 1); this adapter additionally pre-checks the
durable session identity read-only and refuses to launch anything else.

AF #49 M1/W7 (I49-B005) — transport modes.  Two supported exact-session
continuation invocations target the SAME exact session identity:

- ``chat_quiet``      — the W6-accepted ``chat -Q --resume`` transport.
- ``oneshot_resume``  — ``hermes -z <PAYLOAD> --resume <EXACT_ID>
  [--usage-file <PATH>]``.  Verified against installed Hermes v0.21.1:
  ``run_oneshot`` loads the exact session's transcript, runs exactly one
  turn, and writes ``session_id``/``completed`` to the usage report.  The
  oneshot path resolves the model-visible tool surface from the production
  platform toolsets + MCP servers WITHOUT applying ``agent.disabled_toolsets``;
  the chat path applies that subtraction (the canonical task-main profile's
  list includes ``all``), which zeroes the AF MCP surface for the resumed
  turn.  ``oneshot_resume`` exists so the exact-session phase-2 continuation
  preserves the production AF tool surface.  Neither mode ever produces
  ``--resume latest``, ``-c <name>``, ``--create-if-missing``, or an implicit
  new conversation.

W7 also exposes a read-only durable session observation
(``observe_persisted_session_tool_surface``) so the production launcher can
fail closed BEFORE a productive phase-2 continuation when the phase-1 session
is known to have no usable production AF tool surface.  Observation only —
this module never writes session state.

Authority boundaries (W2, NOT W3):

- ``EXACT_SESSION_REENTRY=yes`` / ``SILENT_NEW_SESSION_FALLBACK=no``:
  the adapter can only target an exact id supplied by the trusted runtime
  seam (W1/W3 durable state), never by Worker/model output, and can never
  produce ``--resume latest``, ``-c <name>``, ``--create-if-missing``, or an
  implicit new conversation.
- Same-lineage redirect is honored: Hermes resolves a compressed session id
  forward to its descendant tip inside the SAME lineage
  (``resolve_resume_session_id`` / ``get_compression_tip``); the exact row
  must still exist.  Unrelated sessions are never substituted.
- Busy/lease semantics are Hermes', not invented: a live
  ``session_turn_leases`` row (TTL 300s) or the lease-timeout sentinel maps
  to a typed RETRYABLE outcome.  The origin session is never forcibly
  interrupted; only OUR injected-turn process may be timed out, and a
  timed-out attempt never claims delivery.
- No delivery/ACK state machine lives here (W3).  ``outcome`` is mechanical
  re-entry evidence; "completed" means the bounded turn ran, not that AF
  reconciled a CARD.  ``ACK_SEMANTICS_IMPLEMENTED=no``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_REENTRY_PAYLOAD_BYTES = 64 * 1024
MAX_SESSION_ID_CHARS = 128
MAX_REENTRY_TIMEOUT_SECONDS = 1800.0
DEFAULT_REENTRY_TIMEOUT_SECONDS = 300.0
REENTRY_OUTPUT_LIMIT_BYTES = 32 * 1024
LEASE_SCAN_LIMIT = 32

OUTCOME_COMPLETED = "completed"
OUTCOME_RETRYABLE = "retryable"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"

ERROR_EXACT_SESSION_NOT_FOUND = "HERMES_EXACT_SESSION_NOT_FOUND"
ERROR_REENTRY_RETRYABLE = "HERMES_REENTRY_RETRYABLE"
ERROR_REENTRY_FAILED = "HERMES_REENTRY_FAILED"
ERROR_EXECUTION_UNKNOWN = "HERMES_EXECUTION_UNKNOWN"

_SESSION_ID_RE = re.compile(r"^[0-9A-Za-z_.\-]{1,128}$")
_FORBIDDEN_SESSION_IDS = frozenset({"latest", "-", "--", "@claude", "@codex"})
# Structural never-produced guards for the exact re-entry seam.
_FORBIDDEN_ARGV_TOKENS = (
    "--create-if-missing",
    "-c",
    "--continue",
    "-z",
    "--oneshot",
    "--usage-file",
)
_ONESHOT_FORBIDDEN_ARGV_TOKENS = (
    "--create-if-missing",
    "-c",
    "--continue",
)

# AF #49 M1/W7 — exact-session continuation transports (same session identity).
TRANSPORT_CHAT_QUIET = "chat_quiet"
TRANSPORT_ONESHOT_RESUME = "oneshot_resume"
EXACT_SESSION_TRANSPORTS = (TRANSPORT_CHAT_QUIET, TRANSPORT_ONESHOT_RESUME)

# The oneshot payload is delivered as one argv element; Linux allows 128 KiB
# per element, so the seam's existing 64 KiB payload bound is portable here.
MAX_ONESHOT_ARGV_PAYLOAD_BYTES = 64 * 1024

# Production AF tool-surface markers visible in Hermes session metadata.
# Hermes may defer the raw MCP catalog behind the tool_search bridge, so the
# semantic invariant is "an AOTA invoke path is reachable", not one exact name.
AOTA_MCP_TOOL_NAME_PREFIX = "mcp__aota__"
AOTA_BRIDGE_TOOL_NAMES = frozenset({"tool_search", "tool_describe", "tool_call"})

_PID_HOLDER_RE = re.compile(r"^pid=(\d+):")

# Machine-readable sentinel from agent/turn_facade_lease.py surfaced through
# the -Q error line (cli.py): a bounded lease wait that expired.
_LEASE_TIMEOUT_TOKEN = "session_turn_lease_timeout"
_SESSION_NOT_FOUND_TOKENS = ("Session not found:", "No session found matching")
_SESSION_COORDINATION_TOKENS = ("SESSION_COORDINATION_UNAVAILABLE", "active-session registry", "Another Hermes instance")
_SESSION_ID_LINE_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.MULTILINE)


class HermesSessionReentryError(Exception):
    """Fail-closed configuration/validation error at the re-entry seam."""


@dataclass(frozen=True)
class HermesReentryResult:
    """Typed mechanical outcome of one exact-session re-entry attempt."""

    outcome: str
    retryable: bool
    exact_session_found: bool | None
    turn_accepted: bool
    busy: bool
    exit_code: int | None
    session_id: str
    resolved_session_id: str | None
    error_code: str | None
    error_message: str | None
    response_excerpt: str | None
    stderr_excerpt: str | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "retryable": self.retryable,
            "exact_session_found": self.exact_session_found,
            "turn_accepted": self.turn_accepted,
            "busy": self.busy,
            "exit_code": self.exit_code,
            "session_id": self.session_id,
            "resolved_session_id": self.resolved_session_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "response_excerpt": self.response_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
        }


def validate_exact_session_id(session_id: Any) -> str:
    """Accept only an exact, mechanical session id; refuse every soft mode."""
    if not isinstance(session_id, str) or not session_id:
        raise HermesSessionReentryError("session id must be a non-empty exact identifier")
    if len(session_id) > MAX_SESSION_ID_CHARS or not _SESSION_ID_RE.match(session_id):
        raise HermesSessionReentryError("session id contains characters outside the mechanical identity set")
    if session_id in _FORBIDDEN_SESSION_IDS or session_id.startswith("-"):
        raise HermesSessionReentryError("session id is a forbidden soft-resume mode")
    return session_id


def build_exact_reentry_argv(
    hermes_bin: str,
    session_id: str,
    query_file_path: str,
    *,
    profile: str | None = None,
) -> list[str]:
    """Compose the ONLY permitted argv shape, with a structural fail-closed guard.

    ``MISSING_SESSION_FAILS_CLOSED=yes`` relies on Hermes' own exact-id
    behavior (missing id -> nonzero + not-found error, never a new session);
    this builder makes it impossible for the adapter to emit any soft-resume,
    create-if-missing, or implicit-new-conversation path.
    """
    if not isinstance(hermes_bin, str) or not hermes_bin.strip() or any(ch.isspace() for ch in hermes_bin):
        raise HermesSessionReentryError("hermes binary reference is invalid")
    validate_exact_session_id(session_id)
    if not isinstance(query_file_path, str) or not query_file_path.strip() or any(ch.isspace() for ch in query_file_path):
        raise HermesSessionReentryError("re-entry query file path is invalid")
    if profile is not None and (not isinstance(profile, str) or not profile.strip() or any(ch.isspace() for ch in profile)):
        raise HermesSessionReentryError("profile reference is invalid")
    argv: list[str] = [hermes_bin]
    if profile is not None:
        argv.extend(["-p", profile])
    argv.extend(["chat", "-Q", "--resume", session_id, "--query-file", query_file_path])
    if any(token in argv for token in _FORBIDDEN_ARGV_TOKENS):
        raise HermesSessionReentryError("forbidden soft-resume token leaked into the exact re-entry argv")
    if "--resume" in argv and argv[argv.index("--resume") + 1] != session_id:
        raise HermesSessionReentryError("exact re-entry requires the exact session id after --resume")
    return argv


def build_exact_oneshot_resume_argv(
    hermes_bin: str,
    session_id: str,
    payload_text: str,
    *,
    profile: str | None = None,
    usage_file_path: str | None = None,
) -> list[str]:
    """Compose the W7 tool-surface-preserving exact-session continuation argv.

    Shape (verified against installed Hermes 0.21.1 ``run_oneshot`` +
    top-level ``-z/--oneshot PROMPT`` + ``--resume`` + ``--usage-file``):

        hermes [-p <profile>] -z <PAYLOAD> --resume <EXACT_SESSION_ID> [--usage-file <PATH>]

    The exact-session identity and the fail-closed shape guards are identical
    to :func:`build_exact_reentry_argv`; only the invocation mode differs.
    The payload is a bounded operator/runtime prompt (never model-supplied),
    delivered as one argv element so no shell interpretation is possible.
    """
    if not isinstance(hermes_bin, str) or not hermes_bin.strip() or any(ch.isspace() for ch in hermes_bin):
        raise HermesSessionReentryError("hermes binary reference is invalid")
    validate_exact_session_id(session_id)
    if not isinstance(payload_text, str) or not payload_text.strip():
        raise HermesSessionReentryError("oneshot continuation payload must be a non-empty string")
    if "\x00" in payload_text:
        raise HermesSessionReentryError("oneshot continuation payload must not contain NUL")
    if len(payload_text.encode("utf-8")) > MAX_ONESHOT_ARGV_PAYLOAD_BYTES:
        raise HermesSessionReentryError("oneshot continuation payload exceeds the bounded argv seam")
    if profile is not None and (not isinstance(profile, str) or not profile.strip() or any(ch.isspace() for ch in profile)):
        raise HermesSessionReentryError("profile reference is invalid")
    if usage_file_path is not None and (
        not isinstance(usage_file_path, str)
        or not usage_file_path.strip()
        or any(ch.isspace() for ch in usage_file_path)
    ):
        raise HermesSessionReentryError("oneshot usage file path is invalid")
    argv: list[str] = [hermes_bin]
    if profile is not None:
        argv.extend(["-p", profile])
    argv.extend(["-z", payload_text, "--resume", session_id])
    if usage_file_path is not None:
        argv.extend(["--usage-file", usage_file_path])
    # Only the flags the builder itself owns are scanned: the payload is one
    # opaque argv element (no shell), so it can never become a soft-resume flag.
    structural = list(argv[:3])
    if any(token in structural for token in _ONESHOT_FORBIDDEN_ARGV_TOKENS):
        raise HermesSessionReentryError("forbidden soft-resume token leaked into the oneshot continuation argv")
    if argv[argv.index("--resume") + 1] != session_id:
        raise HermesSessionReentryError("exact continuation requires the exact session id after --resume")
    return argv


def production_aota_tool_surface_present(tool_names: Any) -> bool:
    """Mechanical capability check: is an AOTA invoke path visible in a tool list?

    Accepts the raw MCP tool names (``mcp__aota__*``) and the Hermes
    tool_search bridge that fronts a deferred MCP catalog.  This is a
    presence check only; it never judges model semantics or Work validity.
    """
    if not isinstance(tool_names, (list, tuple, set, frozenset)):
        return False
    for name in tool_names:
        if not isinstance(name, str) or not name:
            continue
        if name.startswith(AOTA_MCP_TOOL_NAME_PREFIX) or name in AOTA_BRIDGE_TOOL_NAMES:
            return True
    return False


@dataclass(frozen=True)
class PersistedSessionToolSurface:
    """Read-only observation of a Hermes session's persisted tool-name pin."""

    observed: bool  # True only when the durable session row + pin were readable
    tool_names: tuple[str, ...] | None
    detail: str | None


def observe_persisted_session_tool_surface(
    session_id: str,
    *,
    hermes_home: str | os.PathLike[str] | None = None,
    state_db_path: str | os.PathLike[str] | None = None,
) -> PersistedSessionToolSurface:
    """Read-only observation of ``sessions.tool_names`` for one exact session.

    This is supported session-metadata observation, never repair: the durable
    store is opened ``mode=ro`` and no row/value is ever written.  A missing
    store/row/pin is reported as ``observed=False`` so the production caller
    can fail closed mechanically.
    """
    try:
        validate_exact_session_id(session_id)
    except HermesSessionReentryError:
        return PersistedSessionToolSurface(False, None, "session id is not an exact mechanical identity")
    if state_db_path is None:
        home: Path | None = Path(hermes_home) if hermes_home is not None else None
        if home is None:
            env_home = os.environ.get("HERMES_HOME", "").strip()
            if env_home:
                home = Path(env_home)
        if home is None:
            return PersistedSessionToolSurface(False, None, "Hermes home is not resolvable for tool-surface observation")
        state_db_path = home / "state.db"
    db_path = Path(state_db_path)
    if not db_path.is_file():
        return PersistedSessionToolSurface(False, None, "Hermes session store is absent")
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        return PersistedSessionToolSurface(False, None, f"session store unreadable: {type(exc).__name__}")
    try:
        connection.text_factory = lambda b: b.decode("utf-8", "replace")
        row = connection.execute("SELECT tool_names FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
        if row is None:
            return PersistedSessionToolSurface(False, None, "exact session id is not present in the durable session store")
        raw = row[0]
        if raw is None:
            return PersistedSessionToolSurface(False, None, "session tool pin is not persisted for the exact session")
        try:
            names = json.loads(raw)
        except (TypeError, ValueError):
            return PersistedSessionToolSurface(False, None, "session tool pin is not mechanical JSON")
        if not isinstance(names, list) or any(not isinstance(item, str) or not item.strip() for item in names):
            return PersistedSessionToolSurface(False, None, "session tool pin is not a mechanical name list")
        return PersistedSessionToolSurface(True, tuple(names), None)
    except sqlite3.Error as exc:
        return PersistedSessionToolSurface(False, None, f"session store query failed: {type(exc).__name__}")
    finally:
        connection.close()


@dataclass(frozen=True)
class _SessionPrecheck:
    exists: bool | None  # None = undecidable from durable evidence
    busy_holder_pid: int | None
    detail: str | None


class HermesExactSessionReentry:
    """One-shot exact-session re-entry attempt executor (no delivery state)."""

    def __init__(
        self,
        hermes_bin: str | os.PathLike[str],
        *,
        hermes_home: str | os.PathLike[str] | None = None,
        profile: str | None = None,
        state_db_path: str | os.PathLike[str] | None = None,
        spool_root: str | os.PathLike[str] | None = None,
        timeout_seconds: float = DEFAULT_REENTRY_TIMEOUT_SECONDS,
        max_payload_bytes: int = MAX_REENTRY_PAYLOAD_BYTES,
        output_limit_bytes: int = REENTRY_OUTPUT_LIMIT_BYTES,
        popen_factory: Callable[..., Any] | None = None,
        transport: str = TRANSPORT_CHAT_QUIET,
    ) -> None:
        if not str(hermes_bin).strip():
            raise HermesSessionReentryError("hermes binary reference is required")
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise HermesSessionReentryError("max_payload_bytes must be a positive integer")
        if type(output_limit_bytes) is not int or output_limit_bytes <= 0:
            raise HermesSessionReentryError("output_limit_bytes must be a positive integer")
        if timeout_seconds <= 0 or timeout_seconds > MAX_REENTRY_TIMEOUT_SECONDS:
            raise HermesSessionReentryError("re-entry timeout_seconds is outside the bounded range")
        if transport not in EXACT_SESSION_TRANSPORTS:
            raise HermesSessionReentryError(
                f"transport must be one of {EXACT_SESSION_TRANSPORTS!r}, got {transport!r}"
            )
        self._transport = transport
        self._hermes_bin = str(hermes_bin)
        self._profile = profile
        self._hermes_home = Path(hermes_home) if hermes_home is not None else None
        self._state_db_path = Path(state_db_path) if state_db_path is not None else None
        self._spool_root = Path(spool_root) if spool_root is not None else None
        self._timeout_seconds = float(timeout_seconds)
        self._max_payload_bytes = max_payload_bytes
        self._output_limit_bytes = output_limit_bytes
        self._popen_factory = popen_factory or subprocess.Popen

    # -- trusted paths ---------------------------------------------------------

    def _resolve_state_db(self) -> Path | None:
        """Durable Hermes session identity store (read-only pre-check only).

        Resolution is operator/runtime supplied or environment-derived — never
        CWD-derived and never taken from Worker/model output.
        """
        if self._state_db_path is not None:
            return self._state_db_path
        home = self._hermes_home
        if home is None:
            env_home = os.environ.get("HERMES_HOME", "").strip()
            if env_home:
                home = Path(env_home)
        if home is None:
            return None
        db = home / "state.db"
        if db.is_file() or not db.is_dir():
            return db
        return None

    def _resolve_spool_root(self) -> Path:
        if self._spool_root is not None:
            root = self._spool_root
        else:
            root = Path.home() / ".aota-forge" / "hermes-reentry"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        return root

    # -- durable evidence pre-check (read-only, mechanical) --------------------

    def _precheck_session(self, session_id: str) -> _SessionPrecheck:
        db_path = self._resolve_state_db()
        if db_path is None:
            return _SessionPrecheck(None, None, "no durable session identity store is configured for this runtime seam")
        if not db_path.is_file():
            # Hermes' own session store does not exist: the exact session
            # cannot exist in it.  Fail closed; never create, never search.
            return _SessionPrecheck(False, None, "Hermes session store is absent")
        try:
            uri = f"file:{db_path}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error as exc:
            return _SessionPrecheck(None, None, f"session store unreadable: {type(exc).__name__}")
        try:
            connection.text_factory = lambda b: b.decode("utf-8", "replace")
            row = connection.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
            if row is None:
                return _SessionPrecheck(False, None, "exact session id is not present in the durable session store")
            lease = self._precheck_lease(connection, session_id)
            return _SessionPrecheck(True, lease, None)
        except sqlite3.Error as exc:
            return _SessionPrecheck(None, None, f"session store query failed: {type(exc).__name__}")
        finally:
            connection.close()

    def _precheck_lease(self, connection: sqlite3.Connection, session_id: str) -> int | None:
        """Live foreign turn-lease evidence keyed on the session or its lineage root.

        Lease keys may sit at the compression-lineage ROOT
        (``_session_turn_lease_key_on_conn``), so walk ancestors with a hard
        bound and never beyond mechanical rows.
        """
        try:
            current: str | None = session_id
            for _ in range(LEASE_SCAN_LIMIT):
                if current is None:
                    break
                lease = connection.execute(
                    "SELECT holder, expires_at FROM session_turn_leases WHERE conversation_id = ? LIMIT 1",
                    (current,),
                ).fetchone()
                if lease is not None:
                    holder, expires_at = str(lease[0] or ""), float(lease[1] or 0.0)
                    if expires_at > time.time():
                        pid_match = _PID_HOLDER_RE.match(holder)
                        if pid_match is not None and _pid_alive(int(pid_match.group(1))) is False:
                            pass  # provably dead holder: Hermes reclaims it itself
                        elif pid_match is not None:
                            return int(pid_match.group(1))
                        else:
                            return -1  # busy, holder not pid-decomposable
                row = connection.execute(
                    "SELECT parent_session_id FROM sessions WHERE id = ? LIMIT 1",
                    (current,),
                ).fetchone()
                parent = row[0] if row is not None and row[0] else None
                current = str(parent) if parent else None
        except sqlite3.Error:
            return None
        return None

    # -- the one bounded re-entry attempt --------------------------------------

    def reenter(self, session_id: str, payload_text: str, *, timeout_seconds: float | None = None) -> HermesReentryResult:
        """Attempt one exact-session turn injection. Never an ACK.

        The payload is supplied by the trusted runtime seam (W3 composes the
        CARD); this seam only enforces the byte bound and the file transport.
        """
        session_id = validate_exact_session_id(session_id)
        if not isinstance(payload_text, str) or not payload_text.strip():
            raise HermesSessionReentryError("re-entry payload must be a non-empty string")
        encoded = payload_text.encode("utf-8")
        if len(encoded) > self._max_payload_bytes:
            raise HermesSessionReentryError(
                f"re-entry payload exceeds the bounded seam ({len(encoded)} > {self._max_payload_bytes} bytes)"
            )
        if "\x00" in payload_text:
            raise HermesSessionReentryError("re-entry payload must not contain NUL")

        timeout = self._timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0 or timeout > MAX_REENTRY_TIMEOUT_SECONDS:
            raise HermesSessionReentryError("re-entry timeout_seconds is outside the bounded range")

        precheck = self._precheck_session(session_id)
        if precheck.exists is False:
            return self._result(
                session_id,
                OUTCOME_NOT_FOUND,
                error_code=ERROR_EXACT_SESSION_NOT_FOUND,
                error_message=precheck.detail or "exact session not found",
                exact_session_found=False,
            )
        if precheck.exists is None:
            return self._result(
                session_id,
                OUTCOME_UNKNOWN,
                error_code=ERROR_EXECUTION_UNKNOWN,
                error_message=precheck.detail or "exact session identity cannot be established from durable evidence",
            )
        if precheck.busy_holder_pid is not None:
            return self._result(
                session_id,
                OUTCOME_RETRYABLE,
                error_code=ERROR_REENTRY_RETRYABLE,
                error_message=(
                    "origin session holds a live turn lease; completion delivery must stay pending (bounded wait/timeout semantics)"
                ),
                exact_session_found=True,
                busy=True,
            )

        spool = self._resolve_spool_root()
        query_path: Path | None = None
        usage_path: Path | None = None
        usage_session_id: str | None = None
        usage_completed: bool | None = None
        try:
            if self._transport == TRANSPORT_ONESHOT_RESUME:
                usage_path = spool / f"reentry-{uuid.uuid4().hex}.usage.json"
                argv = build_exact_oneshot_resume_argv(
                    self._hermes_bin,
                    session_id,
                    payload_text,
                    profile=self._profile,
                    usage_file_path=str(usage_path),
                )
            else:
                query_path = spool / f"reentry-{uuid.uuid4().hex}.query"
                fd = os.open(query_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                argv = build_exact_reentry_argv(
                    self._hermes_bin, session_id, str(query_path), profile=self._profile
                )
            stdout, stderr, exit_code, timed_out = self._run(argv, timeout)
            if self._transport == TRANSPORT_ONESHOT_RESUME and usage_path is not None and exit_code == 0:
                usage_session_id, usage_completed = self._read_oneshot_usage(usage_path)
        finally:
            for artifact in (query_path, usage_path):
                if artifact is None:
                    continue
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass

        if timed_out:
            # Our injected-turn process gave up waiting; the origin session
            # was never interrupted and the turn was never proven accepted.
            return self._result(
                session_id,
                OUTCOME_RETRYABLE,
                error_code=ERROR_REENTRY_RETRYABLE,
                error_message="bounded re-entry wait expired without a proven turn",
                exact_session_found=True,
                busy=True,
                stderr_excerpt=_clip(stderr, self._output_limit_bytes),
            )

        resolved = _SESSION_ID_LINE_RE.search(stderr or "")
        resolved_id = resolved.group(1) if resolved is not None else None
        if exit_code == 0:
            if self._transport == TRANSPORT_ONESHOT_RESUME:
                # The oneshot transport proves completion through the usage
                # report; a missing/incomplete report is a mechanical failure
                # and never claims a delivered turn.
                if not usage_session_id or usage_completed is not True:
                    return self._result(
                        session_id,
                        OUTCOME_FAILED,
                        error_code=ERROR_REENTRY_FAILED,
                        error_message=(
                            "oneshot exact-session continuation returned no mechanical completion evidence "
                            "(usage report missing or incomplete)"
                        ),
                        exact_session_found=True,
                        exit_code=exit_code,
                        stderr_excerpt=_clip(stderr, self._output_limit_bytes),
                    )
                resolved_id = usage_session_id
            return self._result(
                session_id,
                OUTCOME_COMPLETED,
                exact_session_found=True,
                turn_accepted=True,
                exit_code=0,
                resolved_session_id=resolved_id,
                response_excerpt=_clip(stdout, self._output_limit_bytes),
            )
        lowered = (stderr or "") + (stdout or "")
        if exit_code == 130:
            return self._result(
                session_id,
                OUTCOME_RETRYABLE,
                error_code=ERROR_REENTRY_RETRYABLE,
                error_message="re-entry turn was interrupted before completion",
                exact_session_found=True,
                busy=True,
                exit_code=130,
                stderr_excerpt=_clip(stderr, self._output_limit_bytes),
            )
        if any(token in lowered for token in _SESSION_NOT_FOUND_TOKENS) or "session not found" in lowered.lower():
            # Raced deletion between the durable pre-check and the CLI load.
            return self._result(
                session_id,
                OUTCOME_NOT_FOUND,
                error_code=ERROR_EXACT_SESSION_NOT_FOUND,
                error_message="exact session vanished before the CLI could load it",
                exact_session_found=False,
                exit_code=exit_code,
                stderr_excerpt=_clip(stderr, self._output_limit_bytes),
            )
        if exit_code == 1 and _LEASE_TIMEOUT_TOKEN in lowered:
            return self._result(
                session_id,
                OUTCOME_RETRYABLE,
                error_code=ERROR_REENTRY_RETRYABLE,
                error_message="session turn lease wait timed out; delivery stays pending",
                exact_session_found=True,
                busy=True,
                exit_code=exit_code,
                stderr_excerpt=_clip(stderr, self._output_limit_bytes),
            )
        if any(token in lowered for token in _SESSION_COORDINATION_TOKENS):
            return self._result(
                session_id,
                OUTCOME_RETRYABLE,
                error_code=ERROR_REENTRY_RETRYABLE,
                error_message="session coordination is temporarily unavailable for this exact session",
                exact_session_found=True,
                busy=True,
                exit_code=exit_code,
                stderr_excerpt=_clip(stderr, self._output_limit_bytes),
            )
        return self._result(
            session_id,
            OUTCOME_FAILED,
            error_code=ERROR_REENTRY_FAILED,
            error_message="exact-session re-entry failed mechanically",
            exact_session_found=True,
            exit_code=exit_code,
            stderr_excerpt=_clip(stderr, self._output_limit_bytes),
        )

    # -- bounded child process observation --------------------------------------

    def _run(self, argv: list[str], timeout: float) -> tuple[str, str, int, bool]:
        process = self._popen_factory(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        captured: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

        def _pump(stream: Any, key: str) -> None:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer = captured[key]
                room = self._output_limit_bytes - len(buffer)
                if room > 0:
                    buffer.extend(chunk[:room])

        readers = [
            threading.Thread(target=_pump, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=_pump, args=(process.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only OUR injected-turn process is terminated — never the origin
            # session or its active turn (FORCED_SESSION_INTERRUPT=no).
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            exit_code = -1
        for reader in readers:
            reader.join(timeout=1.0)
        stdout_text = captured["stdout"].decode("utf-8", errors="replace")
        stderr_text = captured["stderr"].decode("utf-8", errors="replace")
        return stdout_text, stderr_text, exit_code if isinstance(exit_code, int) else -1, timed_out

    def _read_oneshot_usage(self, usage_path: Path) -> tuple[str | None, bool | None]:
        """Read the bounded mechanical usage report of a ``-z`` continuation turn.

        Returns ``(session_id, completed)``; any unreadable/malformed report is
        ``(None, None)`` and the caller fails the attempt closed.
        """
        try:
            report = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        if not isinstance(report, dict):
            return None, None
        session_id = report.get("session_id")
        session_id = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
        completed = report.get("completed")
        return session_id, (completed if isinstance(completed, bool) else None)

    def _result(self, session_id: str, outcome: str, **kwargs: Any) -> HermesReentryResult:
        return HermesReentryResult(
            outcome=outcome,
            retryable=outcome == OUTCOME_RETRYABLE,
            exact_session_found=kwargs.pop("exact_session_found", None),
            turn_accepted=kwargs.pop("turn_accepted", False),
            busy=kwargs.pop("busy", False),
            exit_code=kwargs.pop("exit_code", None),
            session_id=session_id,
            resolved_session_id=kwargs.pop("resolved_session_id", None),
            error_code=kwargs.pop("error_code", None),
            error_message=kwargs.pop("error_message", None),
            response_excerpt=kwargs.pop("response_excerpt", None),
            stderr_excerpt=kwargs.pop("stderr_excerpt", None),
        )


def _pid_alive(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="replace") + "…"


__all__ = [
    "AOTA_BRIDGE_TOOL_NAMES",
    "AOTA_MCP_TOOL_NAME_PREFIX",
    "EXACT_SESSION_TRANSPORTS",
    "MAX_ONESHOT_ARGV_PAYLOAD_BYTES",
    "MAX_REENTRY_PAYLOAD_BYTES",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED",
    "OUTCOME_NOT_FOUND",
    "OUTCOME_RETRYABLE",
    "OUTCOME_UNKNOWN",
    "TRANSPORT_CHAT_QUIET",
    "TRANSPORT_ONESHOT_RESUME",
    "HermesExactSessionReentry",
    "HermesReentryResult",
    "HermesSessionReentryError",
    "PersistedSessionToolSurface",
    "build_exact_oneshot_resume_argv",
    "build_exact_reentry_argv",
    "observe_persisted_session_tool_surface",
    "production_aota_tool_surface_present",
    "validate_exact_session_id",
]
