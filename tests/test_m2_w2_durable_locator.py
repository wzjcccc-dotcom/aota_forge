"""M2/W2 durable locator tests: mechanical identity, schema fail-closed,
process-identity safety, bounded retention (executor-private evidence).

Gates exercised here:

    RECOVERABLE_ADAPTER_HANDLE=yes
    ADAPTER_HANDLE_RECOVERABLE_AFTER_RESTART=yes (mapping side)
    TERMINAL_MECHANICAL_EVIDENCE_RECOVERABLE=yes (schema side)
    HERMES_LOCATOR_CORRUPT fail-closed behavior
    Process-identity safety (PID reuse cannot fake RUNNING)
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aota_forge.adapters.hermes import locator
from aota_forge.adapters.hermes.locator import (
    HermesLocatorError,
    HermesRunPaths,
    adapter_handle_for_run,
    default_runtime_root,
    new_run_id,
    prepare_run,
    process_matches_identity,
    prune_finished_runs,
    run_id_from_adapter_handle,
    validate_marker,
    validate_receipt,
    validate_spec,
    write_marker_reserved,
    write_receipt,
    write_spec,
)


def _marker(paths: HermesRunPaths, state: str = "reserved", **overrides) -> dict:
    record = {
        "schema": 1,
        "state": state,
        "run_id": paths.run_id,
        "adapter_handle": paths.adapter_handle(),
        "canonical_task_id": "task-1",
        "profile": "aota-worker",
        "cwd": "/tmp/x",
        "launcher": "/bin/hermes",
        "dispatched_at_wall": time.time(),
        "deadline_wall": time.time() + 30,
        "supervisor_pid": None if state == "reserved" else 4242,
        "supervisor_start_ticks": None if state == "reserved" else 12345,
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# handle <-> durable mapping
# ---------------------------------------------------------------------------


def test_adapter_handle_maps_deterministically_to_run_directory() -> None:
    run_id = new_run_id()
    handle = adapter_handle_for_run(run_id)
    assert handle.startswith("hermes-host-")
    assert run_id_from_adapter_handle(handle) == run_id
    # RECOVERABLE_ADAPTER_HANDLE: the mapping is pure derivation; a fresh
    # process reconstructs the SAME run dir with no lookup table.
    root = Path("/runtime-root")
    a = HermesRunPaths(root, str(run_id_from_adapter_handle(handle)))
    assert a.run_dir == root / "runs" / run_id


@pytest.mark.parametrize("foreign", ["", "hermes-host-", "hermes-host-zzz", "task-123", None, 42, "HERMES-HOST-" + "a" * 32])
def test_foreign_handles_never_resolve(foreign: object) -> None:
    assert run_id_from_adapter_handle(foreign) is None


def test_run_directory_layout_is_private(tmp_path: Path) -> None:
    root = tmp_path / "af-w2-private" / new_run_id()
    paths = prepare_run(root, new_run_id())
    marker_mode = stat.S_IMODE(root.stat().st_mode)
    assert marker_mode & 0o077 == 0
    write_marker_reserved(
        paths,
        adapter_handle=paths.adapter_handle(),
        canonical_task_id="t",
        profile="p",
        cwd="/tmp",
        launcher="/bin/h",
        dispatched_at_wall=time.time(),
        deadline_wall=time.time() + 1,
    )
    mode = stat.S_IMODE(paths.marker.stat().st_mode)
    assert mode & 0o077 == 0, "mechanical evidence files must be private"
    # no arbitrary path escape: run dirs are strictly <root>/runs/<32hex>
    assert paths.run_dir.parent == root / "runs"


def test_prepare_run_rejects_foreign_run_id() -> None:
    with pytest.raises(HermesLocatorError):
        prepare_run(Path("/tmp/af-w2-escape"), "../escape")


# ---------------------------------------------------------------------------
# schema fail-closed (HERMES_LOCATOR_CORRUPT)
# ---------------------------------------------------------------------------


def test_malformed_records_fail_closed_without_guessing(tmp_path: Path) -> None:
    paths = prepare_run(tmp_path / "root", new_run_id())
    write_marker_reserved(
        paths,
        adapter_handle=paths.adapter_handle(),
        canonical_task_id="t",
        profile="p",
        cwd="/tmp",
        launcher="/bin/h",
        dispatched_at_wall=time.time(),
        deadline_wall=time.time() + 1,
    )
    good = locator.read_json_object(paths.marker)
    assert good is not None
    validate_marker(good)

    # corrupted JSON fails closed, never repairs
    paths.marker.write_bytes(b"{not json")
    with pytest.raises(HermesLocatorError) as excinfo:
        locator.read_json_object(paths.marker)
    assert excinfo.value.code == "HERMES_LOCATOR_CORRUPT"

    # unknown schema version fails closed
    paths.marker.write_bytes(json.dumps({**good, "schema": 999}).encode())
    with pytest.raises(HermesLocatorError):
        validate_marker(locator.read_json_object(paths.marker))

    # spawned marker without a supervisor identity fails closed
    corrupt = _marker(paths, state="spawned", supervisor_pid=None)
    with pytest.raises(HermesLocatorError):
        validate_marker(corrupt)

    # an empty canonical task id is tolerated (raw host seam), non-string is not
    assert validate_marker({**_marker(paths), "canonical_task_id": ""})
    with pytest.raises(HermesLocatorError):
        validate_marker({**_marker(paths), "canonical_task_id": 42})


def test_receipt_status_vocabulary_is_mechanical_only(tmp_path: Path) -> None:
    paths = prepare_run(tmp_path / "root", new_run_id())
    for valid in ("done", "failed", "timeout", "cancelled"):
        write_receipt(
            paths,
            status=valid,
            exit_code=0,
            started_at_wall=time.time() - 1,
            completed_at_wall=time.time(),
            stdout_bytes=1,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            read_failure=False,
        )
        assert validate_receipt(locator.read_receipt(paths))["status"] == valid
    # the locator can never record an AF semantic state
    with pytest.raises(HermesLocatorError):
        write_receipt(
            paths,
            status="accepted",  # not a mechanical status
            exit_code=0,
            started_at_wall=time.time(),
            completed_at_wall=time.time(),
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            read_failure=False,
        )


def test_published_status_downgrades_unstable_capture_but_never_upgrades() -> None:
    done_with_failure = {"status": "done", "read_failure": True}
    assert locator.published_receipt_status(done_with_failure) == "failed"
    assert locator.published_receipt_status({"status": "done", "read_failure": False}) == "done"
    assert locator.published_receipt_status({"status": "failed", "read_failure": True}) == "failed"


def test_spec_validation_fails_closed(tmp_path: Path) -> None:
    paths = prepare_run(tmp_path / "root", new_run_id())
    with pytest.raises(HermesLocatorError):
        write_spec(
            paths,
            hermes_argv=[],
            cwd=str(tmp_path),
            timeout_seconds=1,
            started_at_wall=time.time(),
            deadline_wall=time.time() + 1,
            output_limit_bytes=10,
        )
    write_spec(
        paths,
        hermes_argv=["/bin/h", "-z", "x"],
        cwd=str(tmp_path),
        timeout_seconds=1,
        started_at_wall=time.time(),
        deadline_wall=time.time() + 1,
        output_limit_bytes=10,
    )
    spec = validate_spec(locator.read_json_object(paths.spec) or {})
    assert spec["hermes_argv"] == ["/bin/h", "-z", "x"]
    with pytest.raises(HermesLocatorError):
        validate_spec({**spec, "output_limit_bytes": 0})


# ---------------------------------------------------------------------------
# runtime root resolution (operator/runtime supplied; never CWD, never model)
# ---------------------------------------------------------------------------


def test_runtime_root_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AOTA_HERMES_RUNTIME_ROOT", raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    resolved = default_runtime_root()
    assert resolved == fake_home / ".aota-forge" / "hermes-runtime"
    assert Path.cwd() not in resolved.parents and resolved != Path.cwd()

    monkeypatch.setenv("AOTA_HERMES_RUNTIME_ROOT", str(tmp_path / "operator-root"))
    assert default_runtime_root() == tmp_path / "operator-root"


# ---------------------------------------------------------------------------
# process identity safety: PID alone can never fake RUNNING
# ---------------------------------------------------------------------------


def test_process_identity_requires_birth_and_command_proof(tmp_path: Path) -> None:
    token = "af-w2-identity-" + new_run_id()
    process = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(30) # {token}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ticks = locator.proc_start_ticks(process.pid)
        assert ticks is not None
        # real birth identity + command fingerprint -> proven alive
        assert process_matches_identity(process.pid, expected_start_ticks=ticks, cmdline_token=token) is True
        # right pid, WRONG command fingerprint (recycled-pid scenario) -> not ours
        assert process_matches_identity(process.pid, expected_start_ticks=ticks, cmdline_token="not-our-token") is False
        # right command, WRONG birth window -> not ours
        assert process_matches_identity(process.pid, expected_start_ticks=ticks + 1, cmdline_token=token) is False
    finally:
        process.kill()
        process.wait(timeout=5)
    # reaped: definitively gone
    assert process_matches_identity(process.pid, expected_start_ticks=ticks, cmdline_token=token) is False


def test_dead_or_nonsense_pids_are_never_running() -> None:
    assert process_matches_identity(None, expected_start_ticks=5, cmdline_token="x") is False
    assert process_matches_identity(0, expected_start_ticks=5, cmdline_token="x") is False


# ---------------------------------------------------------------------------
# bounded retention
# ---------------------------------------------------------------------------


def test_prune_only_removes_terminal_evidence_past_retention(tmp_path: Path) -> None:
    root = tmp_path / "root"
    terminal_paths = prepare_run(root, new_run_id())
    write_receipt(
        terminal_paths,
        status="done",
        exit_code=0,
        started_at_wall=time.time() - 100,
        completed_at_wall=time.time() - 90,
        stdout_bytes=0,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        read_failure=False,
    )
    fresh_paths = prepare_run(root, new_run_id())
    write_receipt(
        fresh_paths,
        status="done",
        exit_code=0,
        started_at_wall=time.time(),
        completed_at_wall=time.time(),
        stdout_bytes=0,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        read_failure=False,
    )
    active_paths = prepare_run(root, new_run_id())  # no receipt: never pruned
    corrupt_paths = prepare_run(root, new_run_id())
    corrupt_paths.receipt.write_bytes(b"{{{corrupt")  # corrupt: operator decision, not GC

    removed = prune_finished_runs(root, retention_seconds=60, now_wall=time.time())
    assert removed == 1
    assert not terminal_paths.run_dir.exists()
    assert fresh_paths.run_dir.exists()
    assert active_paths.run_dir.exists(), "UNKNOWN_FROM_PREMATURE_ACTIVE_EVICTION=no"
    assert corrupt_paths.run_dir.exists(), "malformed evidence fails closed; it is never guessed away"
