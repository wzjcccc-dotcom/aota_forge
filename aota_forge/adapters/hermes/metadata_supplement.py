"""AF #54 M3/W4 — Bounded read-only Hermes metadata supplement.

Optional, non-authoritative metadata supplement over the stable Hermes
session state schema. It exists ONLY because AF canonical seams cannot
supply model identity / provider / turn counts. It never reads message
content, tool-call payloads, prompts, reasoning or transcripts.

    Hermes state.db (read-only)
        ↓
    allowlisted column projection (COUNT aggregates only for messages)
        ↓
    bounded HermesSessionMetadata (supplemental provenance)

Invariants
----------
* HERMES_DB_PRIMARY_TELEMETRY_SOURCE=no
* HERMES_METADATA_IS_AUTHORITY=no
* RAW_HERMES_TRANSCRIPT_REQUIRED=no, RAW_MESSAGE_CONTENT_CAPTURED=no,
  CHAIN_OF_THOUGHT_CAPTURED=no, RAW_PROMPT_CAPTURED=no
* READ_ONLY_ACCESS=yes (sqlite URI mode=ro), no mutation, no schema change
* SCHEMA_DRIFT_FAILS_CLOSED=yes (non-authoritative None, never guessed)
* TTFT_NOT_FABRICATED=yes, LLM_TIMING_NOT_FABRICATED=yes
* RUNTIME_FAILURE_ISOLATED=yes — absence degrades gracefully
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Contract identity / flags
# ---------------------------------------------------------------------------

HERMES_METADATA_SUPPLEMENT_VERSION: str = "s6-af54-m3-v1"
HERMES_DB_PRIMARY_TELEMETRY_SOURCE: bool = False
HERMES_METADATA_IS_AUTHORITY: bool = False
RAW_HERMES_TRANSCRIPT_REQUIRED: bool = False
RAW_MESSAGE_CONTENT_CAPTURED: bool = False
CHAIN_OF_THOUGHT_CAPTURED: bool = False
RAW_PROMPT_CAPTURED: bool = False
READ_ONLY_ACCESS: bool = True
SCHEMA_DRIFT_FAILS_CLOSED: bool = True
TTFT_STATUS: str = "not_reliably_measurable"
LLM_TIMING_STATUS: str = "unavailable"

MAX_SESSION_ID_LENGTH: int = 512
MAX_METADATA_VALUE_LENGTH: int = 256

# Allowlisted session columns — no content, no prompt, no transcript.
SESSION_ALLOWLISTED_COLUMNS: tuple[str, ...] = (
    "id",
    "model",
    "billing_provider",
    "parent_session_id",
    "started_at",
    "ended_at",
    "message_count",
    "tool_call_count",
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
)

# Forbidden column identities (defense-in-depth; never selected).
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "content",
        "tool_calls",
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "system_prompt",
        "api_content",
        "display_metadata",
    }
)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$")


@dataclass(frozen=True)
class HermesSessionMetadata:
    """Bounded non-authoritative Hermes session metadata supplement."""

    session_id: str
    model: str | None = None
    billing_provider: str | None = None
    parent_session_id: str | None = None
    started_at_utc: str | None = None
    ended_at_utc: str | None = None
    message_count: int | None = None
    tool_call_count: int | None = None
    api_call_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    messages_row_count: int | None = None
    ttft_status: str = TTFT_STATUS
    llm_timing_status: str = LLM_TIMING_STATUS
    unavailable_reason: str | None = None

    @property
    def is_authority(self) -> bool:
        return False

    @property
    def is_supplemental(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "ttft_status": self.ttft_status,
            "llm_timing_status": self.llm_timing_status,
            "is_authority": False,
            "is_supplemental": True,
        }
        for key in (
            "model",
            "billing_provider",
            "parent_session_id",
            "started_at_utc",
            "ended_at_utc",
            "message_count",
            "tool_call_count",
            "api_call_count",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "estimated_cost_usd",
            "actual_cost_usd",
            "messages_row_count",
            "unavailable_reason",
        ):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d


def _epoch_to_utc_iso(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _bounded_str(value: object, max_len: int = MAX_METADATA_VALUE_LENGTH) -> str | None:
    if not isinstance(value, str) or type(value) is not str:
        return None
    v = value.strip()
    if not v or len(v) > max_len or "\x00" in v:
        return None
    return v


def _bounded_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 2**53:
        return None
    return value


def _bounded_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        return None
    return f


def _verified_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return set()
    return {str(row[1]) for row in rows if len(row) > 1}


def read_hermes_session_metadata(
    db_path: str | Path,
    session_id: str,
    *,
    timeout_seconds: float = 3.0,
) -> HermesSessionMetadata | None:
    """Read bounded session metadata, read-only, fail-closed on drift.

    Returns ``None`` for: missing/unreadable DB, unknown session, schema
    drift (missing allowlisted columns), or any read failure. Absence is a
    completeness fact; it is never an error in execution truth.
    """
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        return None
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=float(timeout_seconds)
        )
    except Exception:
        return None
    try:
        connection.text_factory = lambda b: b.decode("utf-8", "replace")  # type: ignore[assignment]
        session_columns = _verified_columns(connection, "sessions")
        if not session_columns:
            return None
        if not set(SESSION_ALLOWLISTED_COLUMNS).issubset(session_columns):
            # schema drift: fail closed, non-authoritative
            return None
        select_columns = ", ".join(SESSION_ALLOWLISTED_COLUMNS)
        try:
            row = connection.execute(
                f"SELECT {select_columns} FROM sessions WHERE id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        record = dict(zip(SESSION_ALLOWLISTED_COLUMNS, row))
        messages_row_count: int | None = None
        message_columns = _verified_columns(connection, "messages")
        if "session_id" in message_columns:
            try:
                count_row = connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if count_row is not None:
                    messages_row_count = _bounded_int(count_row[0])
            except Exception:
                messages_row_count = None
        unavailable_reason = None
        if not record.get("model") or not record.get("billing_provider"):
            unavailable_reason = "provider_metadata_partially_unavailable"
        return HermesSessionMetadata(
            session_id=session_id,
            model=_bounded_str(record.get("model")),
            billing_provider=_bounded_str(record.get("billing_provider")),
            parent_session_id=_bounded_str(record.get("parent_session_id")),
            started_at_utc=_epoch_to_utc_iso(record.get("started_at")),
            ended_at_utc=_epoch_to_utc_iso(record.get("ended_at")),
            message_count=_bounded_int(record.get("message_count")),
            tool_call_count=_bounded_int(record.get("tool_call_count")),
            api_call_count=_bounded_int(record.get("api_call_count")),
            input_tokens=_bounded_int(record.get("input_tokens")),
            output_tokens=_bounded_int(record.get("output_tokens")),
            cache_read_tokens=_bounded_int(record.get("cache_read_tokens")),
            cache_write_tokens=_bounded_int(record.get("cache_write_tokens")),
            estimated_cost_usd=_bounded_float(record.get("estimated_cost_usd")),
            actual_cost_usd=_bounded_float(record.get("actual_cost_usd")),
            messages_row_count=messages_row_count,
            unavailable_reason=unavailable_reason,
        )
    except Exception:
        return None
    finally:
        try:
            connection.close()
        except Exception:
            pass


__all__ = [
    "HERMES_METADATA_SUPPLEMENT_VERSION",
    "HERMES_DB_PRIMARY_TELEMETRY_SOURCE",
    "HERMES_METADATA_IS_AUTHORITY",
    "RAW_HERMES_TRANSCRIPT_REQUIRED",
    "RAW_MESSAGE_CONTENT_CAPTURED",
    "CHAIN_OF_THOUGHT_CAPTURED",
    "RAW_PROMPT_CAPTURED",
    "READ_ONLY_ACCESS",
    "SCHEMA_DRIFT_FAILS_CLOSED",
    "TTFT_STATUS",
    "LLM_TIMING_STATUS",
    "SESSION_ALLOWLISTED_COLUMNS",
    "FORBIDDEN_COLUMNS",
    "HermesSessionMetadata",
    "read_hermes_session_metadata",
]
