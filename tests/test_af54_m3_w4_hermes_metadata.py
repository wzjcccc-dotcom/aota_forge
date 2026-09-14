"""AF #54 M3/W4 — Bounded Hermes metadata supplement tests.

Proves: read-only; allowlisted columns only; no message content; aggregate
counts only; missing DB / missing session / schema drift degrade gracefully;
TTFT and exact LLM timing are never fabricated.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from aota_forge.adapters.hermes import metadata_supplement as ms


def _create_db(path: Path, *, include_all: bool = True) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            model TEXT,
            billing_provider TEXT,
            parent_session_id TEXT,
            started_at REAL,
            ended_at REAL,
            message_count INTEGER,
            tool_call_count INTEGER,
            api_call_count INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            system_prompt TEXT,
            content TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            reasoning TEXT,
            timestamp REAL
        )
        """
    )
    con.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "20260914_131627_67dcb7",
            "deepseek-v4-flash",
            "opencode-go",
            None,
            1_758_000_000.0,
            1_758_000_120.0,
            42,
            17,
            40,
            1000,
            500,
            100,
            50,
            0.12,
            0.10,
            "SECRET SYSTEM PROMPT",
            "SECRET CONTENT",
        ),
    )
    for index in range(3):
        con.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, reasoning, timestamp) VALUES (?,?,?,?,?,?)",
            (
                "20260914_131627_67dcb7",
                "assistant",
                f"SECRET MESSAGE CONTENT {index}",
                '{"tool": "aota.invoke", "args": {"secret": true}}',
                "SECRET CHAIN OF THOUGHT",
                1_758_000_000.0 + index,
            ),
        )
    con.commit()
    con.close()


class TestHermesMetadataSupplement:
    def test_read_only_and_bounded(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        _create_db(db)
        before = db.read_bytes()
        metadata = ms.read_hermes_session_metadata(db, "20260914_131627_67dcb7")
        assert metadata is not None
        assert metadata.session_id == "20260914_131627_67dcb7"
        assert metadata.model == "deepseek-v4-flash"
        assert metadata.billing_provider == "opencode-go"
        assert metadata.message_count == 42
        assert metadata.tool_call_count == 17
        assert metadata.messages_row_count == 3
        assert metadata.started_at_utc is not None and metadata.started_at_utc.endswith("+00:00")
        assert metadata.is_authority is False
        assert metadata.is_supplemental is True
        assert db.read_bytes() == before  # read-only

    def test_no_content_or_prompt_captured(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        _create_db(db)
        metadata = ms.read_hermes_session_metadata(db, "20260914_131627_67dcb7")
        assert metadata is not None
        serialized = str(metadata.to_dict())
        assert "SECRET" not in serialized
        assert "tool_calls" not in serialized

    def test_allowlist_excludes_forbidden_columns(self) -> None:
        assert not (set(ms.SESSION_ALLOWLISTED_COLUMNS) & ms.FORBIDDEN_COLUMNS)
        source = inspect.getsource(ms.read_hermes_session_metadata)
        assert "'content'" not in source
        assert '"content"' not in source
        assert "tool_calls" not in source.replace("tool_call_count", "")
        assert "reasoning" not in source.replace("reasoning_status", "")

    def test_missing_db_degrades_gracefully(self, tmp_path: Path) -> None:
        assert ms.read_hermes_session_metadata(tmp_path / "nope.db", "sess-1") is None

    def test_missing_session_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        _create_db(db)
        assert ms.read_hermes_session_metadata(db, "unknown-session") is None

    def test_schema_drift_fails_closed(self, tmp_path: Path) -> None:
        db = tmp_path / "drift.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT)")
        con.execute("INSERT INTO sessions VALUES ('sess-drift', 'm')")
        con.commit()
        con.close()
        assert ms.read_hermes_session_metadata(db, "sess-drift") is None

    def test_invalid_session_id_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        _create_db(db)
        assert ms.read_hermes_session_metadata(db, "bad id with spaces") is None
        assert ms.read_hermes_session_metadata(db, "x" * 600) is None

    def test_partial_provider_metadata_graceful(self, tmp_path: Path) -> None:
        db = tmp_path / "partial.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT, parent_session_id TEXT, started_at REAL, ended_at REAL, message_count INTEGER, tool_call_count INTEGER, api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL)"
        )
        con.execute(
            "INSERT INTO sessions VALUES ('sess-partial', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
        )
        con.commit()
        con.close()
        metadata = ms.read_hermes_session_metadata(db, "sess-partial")
        assert metadata is not None
        assert metadata.model is None
        assert metadata.billing_provider is None
        assert metadata.unavailable_reason == "provider_metadata_partially_unavailable"

    def test_ttft_and_llm_timing_never_fabricated(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        _create_db(db)
        metadata = ms.read_hermes_session_metadata(db, "20260914_131627_67dcb7")
        assert metadata is not None
        assert metadata.ttft_status == "not_reliably_measurable"
        assert metadata.llm_timing_status == "unavailable"
        assert ms.TTFT_STATUS == "not_reliably_measurable"
        assert ms.RAW_HERMES_TRANSCRIPT_REQUIRED is False
        assert ms.HERMES_DB_PRIMARY_TELEMETRY_SOURCE is False
        assert ms.HERMES_METADATA_IS_AUTHORITY is False

    def test_supplement_optional_not_runtime_authority(self) -> None:
        # absent supplement must not affect Tool/Skill observation contracts
        from aota_forge.work_plane.tool_usage_observation import (
            TOOL_USAGE_OBSERVATION_CONTRACT_VERSION,
        )

        assert TOOL_USAGE_OBSERVATION_CONTRACT_VERSION  # observation contract independent
        assert ms.READ_ONLY_ACCESS is True
        assert ms.SCHEMA_DRIFT_FAILS_CLOSED is True
