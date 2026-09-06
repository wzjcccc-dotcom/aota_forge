"""M2/W2 crash/restart recoverability (acceptance scenario R8).

Proves the W2 durable runtime boundary with REAL supervised OS processes and
host-client recreation:

    client A launches Hermes Worker -> locator durable on disk
    -> A is destroyed (process-local state gone)
    -> client B resolves the SAME adapter handle from mechanical evidence

and the honest-state discipline:

    RUNNING   -> child provably supervised/alive
    TERMINAL  -> atomic supervisor receipt (+ Hermes usage evidence)
    UNKNOWN   -> evidence insufficient; NEVER a fabricated COMPLETED/FAILED,
                 NEVER an automatic re-dispatch
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from hermes_durable_support import DurableSupervisorPad

from aota_forge.adapters.hermes import locator
from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.core.execution import CanonicalTaskState

FAKE_HERMES = """#!/bin/sh
usage=""
instr=""
mode="${FAKE_HERMES_MODE:-ok}"
while [ $# -gt 0 ]; do
  case "$1" in
    --usage-file) usage="$2"; shift 2;;
    -z) instr="$2"; shift 2;;
    *) shift;;
  esac
done
if [ "$mode" = slow ]; then
  trap 'kill ${sleep_pid} 2>/dev/null; exit 143' TERM INT
  sleep "${FAKE_HERMES_SECONDS:-2}" &
  sleep_pid=$!
  wait "$sleep_pid" || true
fi
echo "worker output for: $instr"
if [ -n "$usage" ]; then
  printf '{"session_id":"20260906_120000_f00dca","failed":false,"completed":true}\\n' > "$usage"
fi
if [ "$mode" = fail ]; then exit 3; fi
exit 0
"""


def host_payload(cwd: Path, **overrides: object) -> dict:
    base: dict = {
        "profile": "aota-worker",
        "instruction": "Return exactly: AOTA_M2_W2_OK",
        "context": {"working_context": {"cwd": str(cwd)}, "canonical_task_id": overrides.pop("canonical_task_id", "m2-w2-task")},
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "m2-w2-package",
    }
    base.update(overrides)
    return base


def build_launcher(tmp_path: Path) -> str:
    launcher = tmp_path / "fake-hermes.sh"
    launcher.write_text(FAKE_HERMES)
    launcher.chmod(0o755)
    return str(launcher)


def make_client(tmp_path: Path, *, launcher: str | None = None, popen_factory=None, **kwargs) -> HermesHostClient:
    root = tmp_path / "af-runtime"
    return HermesHostClient(
        launcher or str(tmp_path / "unused-launcher"),
        default_cwd=str(tmp_path),
        runtime_root=root,
        popen_factory=popen_factory,
        validate_launcher=popen_factory is None and (launcher is not None),
        **kwargs,
    )


def wait_until(predicate, budget: float = 15.0) -> bool:
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.03)
    return False


def _pid_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


# ---------------------------------------------------------------------------
# R8 scenario: AF process A disappears while the child keeps running;
# a fresh client B resolves the same handle truthfully.
# ---------------------------------------------------------------------------


def test_running_child_after_af_restart_resolves_running_then_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_HERMES_MODE", "slow")
    monkeypatch.setenv("FAKE_HERMES_SECONDS", "2")
    launcher = build_launcher(tmp_path)
    client_a = make_client(tmp_path, launcher=launcher)
    response = client_a.dispatch(host_payload(tmp_path))
    handle = response["adapter_handle"]

    # Destroy A's process-local state entirely (controlled host-client
    # recreation: no shared Python objects carry into B).
    del client_a

    client_b = make_client(tmp_path, launcher=launcher)
    assert client_b._local_supervisors == {}, "B must start without any in-memory execution state"

    first = client_b.query_status(handle)
    assert first["status"] in {"pending", "running"}, (
        "RUNNING_CHILD_AFTER_AF_RESTART must observe RUNNING (or UNKNOWN), never a fabricated terminal"
    )
    assert wait_until(lambda: client_b.query_status(handle)["status"] == "running", budget=5), (
        "once the supervised child is proven alive the observation is RUNNING"
    )

    assert wait_until(lambda: client_b.query_status(handle)["status"] == "done"), (
        "the detached supervisor must publish the terminal receipt without AF"
    )
    result = client_b.fetch_result(handle)
    assert result["status"] == "done"
    assert result["exit_code"] == 0
    assert "worker output for" in result["stdout"], "bounded output evidence survives parent loss"

    evidence = client_b.execution_evidence(handle)
    assert evidence is not None
    assert evidence["session_id"] == "20260906_120000_f00dca", (
        "Hermes-minted durable session identity is recoverable via --usage-file"
    )
    assert evidence["canonical_task_id"] == "m2-w2-task"
    client_b.close()


def test_mechanical_terminal_evidence_recoverable_after_full_af_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AF never observes the completion at all: B still finds the receipt."""
    monkeypatch.setenv("FAKE_HERMES_MODE", "ok")
    launcher = build_launcher(tmp_path)
    client_a = make_client(tmp_path, launcher=launcher)
    handle = client_a.dispatch(host_payload(tmp_path))["adapter_handle"]
    del client_a  # A is gone immediately after submit

    client_b = make_client(tmp_path, launcher=launcher)
    assert wait_until(lambda: client_b.query_status(handle)["status"] == "done")
    receipt = locator.read_receipt(
        locator.HermesRunPaths(Path(client_b.runtime_root), str(locator.run_id_from_adapter_handle(handle)))
    )
    assert receipt is not None
    assert receipt["status"] == "done"
    assert isinstance(receipt["exit_code"], int)
    assert receipt["completed_at_wall"] > 0
    client_b.close()


def test_lost_supervisor_and_child_projects_unknown_never_terminal(tmp_path: Path) -> None:
    pad = DurableSupervisorPad()
    client = make_client(tmp_path, popen_factory=pad)
    handle = client.dispatch(host_payload(tmp_path))["adapter_handle"]
    assert client.query_status(handle)["status"] == "running"

    # total runtime loss: both processes gone, no receipt ever published
    pad.last.crash_without_receipt()

    observed = client.query_status(handle)["status"]
    assert observed == "unknown", "no-fabrication rule: lost evidence stays UNKNOWN"
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "unknown"
    assert envelope["error"]["code"] == "HERMES_EXECUTION_UNKNOWN"
    # UNKNOWN is retryable uncertainty, not an authority to re-dispatch:
    # the handle stays single-run and no new launch happened.
    assert len(pad.launches) == 1
    assert locator.list_run_dirs(Path(client.runtime_root)) == [
        str(locator.run_id_from_adapter_handle(handle))
    ]
    pad.close()


def test_blind_redispatch_on_lost_handle_is_impossible(tmp_path: Path) -> None:
    """Status/result probes on an unknown handle fail closed as TASK_NOT_FOUND.

    The recovery seam may NEVER spawn a second physical Worker as a side
    effect of observation (BLIND_REDISPATCH_ON_LOST_HANDLE=no).
    """
    pad = DurableSupervisorPad()
    client = make_client(tmp_path, popen_factory=pad)
    launch_before = len(pad.launches)

    probe = "hermes-host-" + "c" * 32
    for _ in range(3):
        assert client.query_status(probe)["error"]["code"] == "TASK_NOT_FOUND"
        assert client.fetch_result(probe)["error"]["code"] == "TASK_NOT_FOUND"
        assert client.cancel_task(probe)["cancelled"] is False
    assert len(pad.launches) == launch_before, "observation must never launch anything"


# ---------------------------------------------------------------------------
# adapter-level exact session re-entry of bindings after restart
# ---------------------------------------------------------------------------


def test_fresh_adapter_rebinds_handle_from_durable_marker_only_on_exact_match(tmp_path: Path) -> None:
    pad = DurableSupervisorPad(release_on_spawn=0)
    client = make_client(tmp_path, popen_factory=pad)
    handle = client.dispatch(host_payload(tmp_path, canonical_task_id="durable-task-1"))["adapter_handle"]

    # A brand-new adapter object with a brand-new client over the same
    # durable root must be able to serve the OLD handle without any
    # process-local memory (PROCESS_LOCAL_RECORD_REQUIRED_FOR_RECOVERY=no).
    client_b = make_client(tmp_path, popen_factory=DurableSupervisorPad())
    adapter_b = HermesAdapter(host_client=client_b)
    status = adapter_b.status("durable-task-1", handle)
    assert status.state is CanonicalTaskState.COMPLETED
    result = adapter_b.result("durable-task-1", handle)
    assert result.ok is True and result.canonical_task_state == CanonicalTaskState.COMPLETED.value

    # ... but the recovery never GUESSES the binding: wrong task fails closed.
    with pytest.raises(Exception) as mismatch:
        adapter_b.status("other-task", handle)
    assert getattr(mismatch.value, "code", None) == "TASK_ID_MISMATCH"
    pad.close()


def test_malformed_marker_surfaces_locator_corruption_fail_closed(tmp_path: Path) -> None:
    pad = DurableSupervisorPad(release_on_spawn=0)
    client = make_client(tmp_path, popen_factory=pad)
    handle = client.dispatch(host_payload(tmp_path))["adapter_handle"]
    paths = locator.HermesRunPaths(
        Path(client.runtime_root), str(locator.run_id_from_adapter_handle(handle))
    )
    paths.marker.write_bytes(b'{"schema": 1, "state": "spawned"')  # truncated JSON

    status = client.query_status(handle)
    assert status["status"] == "unreachable"
    assert status["error"]["code"] == "HERMES_LOCATOR_CORRUPT"
    with pytest.raises(locator.HermesLocatorError):
        client.resolve_handle(handle)
    pad.close()


# ---------------------------------------------------------------------------
# real supervisor lifecycle edges: timeout, cancellation via durable identity
# ---------------------------------------------------------------------------


def test_real_supervisor_timeout_publishes_terminal_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_HERMES_MODE", "slow")
    monkeypatch.setenv("FAKE_HERMES_SECONDS", "30")
    launcher = build_launcher(tmp_path)
    client = make_client(tmp_path, launcher=launcher, timeout_seconds=5)
    payload = host_payload(tmp_path)
    payload["constraints"] = {"timeout_seconds": 0.4}
    handle = client.dispatch(payload)["adapter_handle"]

    assert wait_until(lambda: client.query_status(handle)["status"] == "timeout", budget=15)
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "timeout"
    assert envelope["error"]["code"] == "EXECUTION_TIMEOUT"
    paths = locator.HermesRunPaths(Path(client.runtime_root), str(locator.run_id_from_adapter_handle(handle)))
    child = locator.read_json_object(paths.child)
    assert child is not None and _pid_dead(int(child["child_pid"])), "timed-out worker must be terminated"
    client.close()


def test_cancel_after_restart_uses_durable_process_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_HERMES_MODE", "slow")
    monkeypatch.setenv("FAKE_HERMES_SECONDS", "30")
    launcher = build_launcher(tmp_path)
    client_a = make_client(tmp_path, launcher=launcher)
    handle = client_a.dispatch(host_payload(tmp_path))["adapter_handle"]
    del client_a

    client_b = make_client(tmp_path, launcher=launcher)
    assert wait_until(lambda: client_b.query_status(handle)["status"] == "running", budget=10)
    cancelled = client_b.cancel_task(handle)
    assert cancelled == {"cancelled": True, "status": "cancelled"}

    paths = locator.HermesRunPaths(Path(client_b.runtime_root), str(locator.run_id_from_adapter_handle(handle)))
    assert wait_until(lambda: paths.child.is_file())
    child = locator.read_json_object(paths.child)
    assert child is not None and _pid_dead(int(child["child_pid"])), "worker terminated via durable identity"
    assert client_b.query_status(handle)["status"] == "cancelled"
    client_b.close()


# ---------------------------------------------------------------------------
# exit code is mechanical evidence, not AF acceptance
# ---------------------------------------------------------------------------


def test_exit_zero_envelope_still_requires_canonical_projection_pipeline(tmp_path: Path) -> None:
    """HERMES_PROCESS_EXIT_ZERO_IS_AF_ACCEPTANCE=no:

    The host seam emits the mechanical status only; semantic acceptance still
    flows exclusively through the canonical projection path, whose terminal
    validation (stable envelope, structured result rules) is owned elsewhere.
    """
    pad = DurableSupervisorPad(release_on_spawn=0)
    client = make_client(tmp_path, popen_factory=pad)
    handle = client.dispatch(host_payload(tmp_path))["adapter_handle"]
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "done"
    assert envelope["exit_code"] == 0
    # the envelope carries no authority vocabulary
    text = json.dumps(envelope).lower()
    for authority in ("ack", "accept", "reconcil", "delivered"):
        assert authority not in text
    pad.close()


def test_close_does_not_kill_durable_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W2 boundary: close() detaches local observation only."""
    monkeypatch.setenv("FAKE_HERMES_MODE", "slow")
    monkeypatch.setenv("FAKE_HERMES_SECONDS", "3")
    launcher = build_launcher(tmp_path)
    client = make_client(tmp_path, launcher=launcher)
    handle = client.dispatch(host_payload(tmp_path))["adapter_handle"]
    paths = locator.HermesRunPaths(Path(client.runtime_root), str(locator.run_id_from_adapter_handle(handle)))
    assert wait_until(lambda: paths.child.is_file())
    child = locator.read_json_object(paths.child)
    assert child is not None
    client.close()
    time.sleep(0.2)
    assert not _pid_dead(int(child["child_pid"])), (
        "close() must NOT terminate the durable worker; that is the crash-survival contract"
    )
    # it completes on its own and B can observe it
    client_b = make_client(tmp_path, launcher=launcher)
    assert wait_until(lambda: client_b.query_status(handle)["status"] == "done", budget=15)
    client_b.close()
