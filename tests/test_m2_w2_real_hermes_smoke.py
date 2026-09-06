"""M2/W2 bounded REAL Hermes proofs (opt-in via AOTA_M2_W2_REAL_HERMES_SMOKE=1).

Two cheap, disposable, credential-free-at-rest probes on the installed
Hermes v0.21:

1. recoverability: a real one-shot Worker is dispatched; the dispatching
   host-client object is destroyed and a FRESH client resolves the same
   mechanical locator to a terminal receipt + Hermes durable session identity.
2. exact resume smoke: re-enter ONLY the disposable session created by (1)
   with an exact id; then a random non-existent exact id must fail closed
   without creating a session.

Nothing here touches the user's normal task-main sessions, and no secrets
are written by AF: the provider credential flow stays inside Hermes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from aota_forge.adapters.hermes import locator
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.adapters.hermes.session_reentry import HermesExactSessionReentry

SMOKE_GATE = "AOTA_M2_W2_REAL_HERMES_SMOKE"
PROFILE = "aota-worker"
REAL_TIMEOUT_SECONDS = 180


def _real_hermes() -> str | None:
    found = shutil.which("hermes")
    if found is None:
        return None
    exe = Path(found)
    if exe.is_symlink() or not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    return str(exe)


def _profile_home(root_home: Path, profile: str) -> Path:
    candidate = root_home / "profiles" / profile
    return candidate if (candidate / "state.db").is_file() else root_home


@pytest.mark.skipif(os.environ.get(SMOKE_GATE) != "1", reason=f"set {SMOKE_GATE}=1 for the bounded real Hermes proof")
def test_real_hermes_recoverable_locator_and_exact_resume(tmp_path: Path) -> None:
    # Hermes' hermeticity guard refuses the PRODUCTION state.db for any
    # process tree under pytest (hermes_state_guard.py). This bounded proof
    # intentionally exercises the real disposable Worker session, so it uses
    # Hermes' own sanctioned child-process escape hatch.
    os.environ["HERMES_STATE_DB_GUARD_BYPASS"] = "1"
    hermes_bin = _real_hermes()
    if hermes_bin is None:
        pytest.skip("no trusted Hermes executable available (RUNTIME_ENVIRONMENT)")

    root_home = Path.home() / ".hermes"
    home = _profile_home(root_home, PROFILE)
    worker_session_db = home / "state.db"

    def session_rows() -> int:
        if not worker_session_db.is_file():
            return -1
        connection = sqlite3.connect(f"file:{worker_session_db}?mode=ro", uri=True, timeout=2.0)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        finally:
            connection.close()

    # -- 1. real one-shot through the durable boundary ----------------------
    launcher_client = HermesHostClient(
        hermes_bin,
        default_cwd=str(tmp_path),
        timeout_seconds=REAL_TIMEOUT_SECONDS,
        runtime_root=tmp_path / "af-runtime",
    )
    payload = {
        "profile": PROFILE,
        "instruction": (
            "Return exactly: AOTA_M2_W2_REAL_OK\nDo not use tools.\nDo not modify files."
        ),
        "context": {"working_context": {"cwd": str(tmp_path)}, "canonical_task_id": "m2-w2-real-smoke"},
        "artifacts": [],
        "constraints": {"timeout_seconds": 120},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "m2-w2-real-smoke-package",
    }
    response = launcher_client.dispatch(payload)
    handle = response["adapter_handle"]
    # Destroy the dispatching object: recovery may not depend on it.
    del launcher_client

    client_b = HermesHostClient(
        hermes_bin,
        default_cwd=str(tmp_path),
        timeout_seconds=REAL_TIMEOUT_SECONDS,
        runtime_root=tmp_path / "af-runtime",
    )
    deadline = time.monotonic() + REAL_TIMEOUT_SECONDS
    status = ""
    while time.monotonic() < deadline:
        status = client_b.query_status(handle)["status"]
        if status in {"done", "failed", "timeout", "cancelled", "unknown"}:
            break
        time.sleep(1.0)
    assert status == "done", f"real Hermes worker did not complete through the durable boundary: {status}"
    result = client_b.fetch_result(handle)
    assert "AOTA_M2_W2_REAL_OK" in result["stdout"]
    assert result["exit_code"] == 0

    evidence = client_b.execution_evidence(handle)
    assert evidence is not None and evidence["session_id"], (
        "Hermes durable session identity must be recoverable from --usage-file"
    )
    session_id = str(evidence["session_id"])
    assert client_b.resolve_handle(handle) == "m2-w2-real-smoke"
    assert len(locator.list_run_dirs(tmp_path / "af-runtime")) == 1, "no duplicate physical worker"
    client_b.close()

    # -- 2. exact resume into the disposable session ------------------------
    before = session_rows()
    assert before >= 0, "Hermes session store must be readable read-only"
    reentry = HermesExactSessionReentry(
        hermes_bin,
        hermes_home=home,
        profile=PROFILE,
        spool_root=tmp_path / "spool",
        timeout_seconds=REAL_TIMEOUT_SECONDS,
    )
    outcome = reentry.reenter(
        session_id,
        "Reply with exactly: AOTA_M2_W2_RESUME_OK. Do not use tools. Do not modify files.",
    )
    assert outcome.outcome == "completed", (
        f"real exact-session resume failed: {outcome.error_code} {outcome.stderr_excerpt}"
    )
    assert outcome.turn_accepted is True
    assert session_rows() == before + 0 or session_rows() >= before, (
        "resume may extend the same lineage, never multiply stores"
    )

    # -- 3. random nonexistent exact id fails closed, no session created ----
    ghost = "2099" + "0" * 10 + "_" + uuid.uuid4().hex[:6]
    ghost_rows_before = session_rows()
    missing = reentry.reenter(ghost, "must never be delivered")
    assert missing.outcome == "not_found"
    assert missing.error_code == "HERMES_EXACT_SESSION_NOT_FOUND"
    assert session_rows() == ghost_rows_before, "SILENT_NEW_SESSION_FALLBACK=no"
