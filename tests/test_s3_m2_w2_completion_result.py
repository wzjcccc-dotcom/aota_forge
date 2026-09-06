"""Focused S3/M2/W2 tests: completion / result projection contract hardening.

Freezes the accepted W2 boundary:

    private Hermes process lifecycle
    -> runtime completion detection
    -> stable result availability
    -> result-envelope validation
    -> CanonicalResult projection

Core invariant under test: PROCESS_EXIT != CANONICAL_SUCCESS.  A terminal
process is only runtime evidence; a canonical success projection additionally
requires a stable, structurally valid result envelope.

Completion/result observation stops at the adapter projection seam.  No test
here asserts canonical TaskRecord reconciliation: that remains S2/Core
authority (wzjcccc-dotcom/aota-hermes-tools#19 shared boundary).
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from hermes_durable_support import DurableSupervisorPad

from aota_forge.adapters.hermes import locator
from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    HermesAdapterError,
    HermesHostUnavailableError,
    hermes_output_to_canonical_result,
)
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PROJECTION_KEYS = (
    "pid",
    "process",
    "profile",
    "hermes_session",
    "hermes_task",
    "hermes_trace",
    "hermes_worker",
    "hermes_profile",
    "deadline",
    "terminal_at",
    "started_at",
    "adapter_handle",
    "readers",
    "cwd",
)


class ControlledProcess:
    """Fake process whose exit is released explicitly by the test."""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: Any | None = None,
        stderr: Any | None = None,
    ) -> None:
        self._exit_code = exit_code
        self.returncode: int | None = None
        self.stdout = stdout if stdout is not None else io.BytesIO(b"controlled stdout")
        self.stderr = stderr if stderr is not None else io.BytesIO(b"controlled stderr")
        self.terminate_calls = 0
        self._lock = threading.Lock()

    def release(self, code: int | None = None) -> None:
        with self._lock:
            self.returncode = self._exit_code if code is None else code

    def poll(self) -> int | None:
        with self._lock:
            return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout if timeout is not None else 30.0)
        while True:
            code = self.poll()
            if code is not None:
                return code
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("controlled", timeout)
            time.sleep(0.002)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.release(-15)

    def kill(self) -> None:
        self.terminate_calls += 1
        self.release(-9)


class GatedStream:
    """Output stream that stays open after process exit until the gate opens.

    Models a pipe whose reader has not finished draining when the process
    becomes terminal: exit code is observable, output stability is not.
    """

    def __init__(self, chunks: list[bytes], gate: threading.Event) -> None:
        self._chunks = list(chunks)
        self.gate = gate

    def read(self, size: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        self.gate.wait(timeout=20)
        return b""


class RecordingRunner:
    """popen_factory handing out one controlled process per call."""

    def __init__(self, factory: Any | None = None) -> None:
        self.calls = 0
        self.processes: list[Any] = []
        self._factory = factory

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls += 1
        process = self._factory() if self._factory is not None else ControlledProcess()
        self.processes.append(process)
        return process


class ScriptedHost:
    """Fake host protocol with unique handles and fixed envelopes."""

    def __init__(
        self,
        status: str = "running",
        result_envelope: Mapping[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.status = status
        self.result_envelope = result_envelope if result_envelope is not None else {"status": "done", "exit_code": 0}
        self.raises = raises
        self._next = 1
        self.calls = {"dispatch": 0, "status": 0, "result": 0, "cancel": 0}

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls["dispatch"] += 1
        handle = f"w2-scripted-handle-{self._next}"
        self._next += 1
        return {"adapter_handle": handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["status"] += 1
        if self.raises is not None:
            raise self.raises
        return {"status": self.status}

    def fetch_result(self, adapter_handle: str) -> Any:
        self.calls["result"] += 1
        if self.raises is not None:
            raise self.raises
        return self.result_envelope

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["cancel"] += 1
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error"}


def host_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "profile": "coder",
        "instruction": "Return exactly: AOTA_FORGE_S3_M2_W2_OK",
        "context": {"working_context": {}},
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "s3-m2-w2-package",
    }
    base.update(overrides)
    return base


def payload_with_cwd(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    return host_payload(context={"working_context": {"cwd": str(tmp_path)}}, **overrides)


def package(task_id: str, **kwargs: Any) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=kwargs.pop("canonical_task_id", task_id),
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Return exactly: AOTA_FORGE_S3_M2_W2_OK",
        **kwargs,
    )


def concrete_client(tmp_path: Path, runner: Any, **kwargs: Any) -> HermesHostClient:
    kwargs.setdefault("validate_launcher", False)
    kwargs.setdefault("timeout_seconds", 30)
    kwargs.setdefault("runtime_root", tmp_path / "af-runtime")
    return HermesHostClient(str(tmp_path / "unused-launcher"), popen_factory=runner, **kwargs)


def run_paths(client: HermesHostClient, handle: str) -> locator.HermesRunPaths:
    run_id = locator.run_id_from_adapter_handle(handle)
    assert run_id is not None
    return locator.HermesRunPaths(Path(client.runtime_root), run_id)


def wait_for_status(client: HermesHostClient, handle: str, terminal: set[str], budget: float = 6.0) -> str:
    deadline = time.monotonic() + budget
    status = ""
    while time.monotonic() < deadline:
        status = client.query_status(handle)["status"]
        if status in terminal:
            return status
        time.sleep(0.005)
    return status


def _iter_keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_keys(item)


def assert_no_private_projection_leakage(obj: Any, *, launcher: str | None = None) -> None:
    """Projected keys must never contain Hermes-private runtime identifiers."""
    keys = {key.lower() for key in _iter_keys(obj)}
    offenders = sorted(keys & {token.lower() for token in FORBIDDEN_PROJECTION_KEYS})
    assert offenders == [], f"private runtime keys escaped into projection: {offenders}"
    text = json.dumps(obj, default=str, sort_keys=True).lower()
    for marker in ("popen(", "threading.object", "subprocess.Popen(".lower(), " -z "):
        assert marker not in text, f"private runtime object leaked into projection text: {marker!r}"
    if launcher is not None:
        assert str(launcher).lower() not in text, "launcher path leaked into projection"


def bound_adapter(tmp_path: Path, host: ScriptedHost, task_id: str = "w2-task") -> tuple[HermesAdapter, str, str]:
    adapter = HermesAdapter(host_client=host)
    result = adapter.dispatch(package(task_id, working_context={"cwd": str(tmp_path)}))
    return adapter, result.canonical_task_id, result.adapter_handle


# ---------------------------------------------------------------------------
# A. Completion detection ordering: terminal observation requires stable output
# ---------------------------------------------------------------------------


def test_terminal_not_observable_until_readers_finish_draining(tmp_path: Path) -> None:
    """PROCESS_EXIT alone must not publish a terminal result observation.

    W2 durable boundary: the supervisor publishes the atomic terminal receipt
    only after both bounded captures are complete, so no reconciling reader
    can ever freeze a partially drained stream.  This freezes
    TERMINAL_RESULT_AVAILABLE_ONLY_AFTER_OUTPUT_STABLE / RESULT_STREAM_DRAIN_RACE=no
    on the durable protocol.
    """
    gate = threading.Event()
    pad = DurableSupervisorPad(stdout=b"chunk-a-chunk-b-final", stderr=b"")
    client = concrete_client(tmp_path, pad)
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]

    pad.last.release(0, stream_gate=gate)
    assert pad.last.child_returncode() is not None, "the supervised worker process is already terminal"
    observed = {client.query_status(handle)["status"] for _ in range(3)}
    assert observed == {"running"}, (
        "terminal status must not be observable before the terminal receipt "
        f"publishes the stabilized captures; saw {sorted(observed)}"
    )
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "running", "result must not finalize from an unstable stream"
    assert "exit_code" not in envelope, "no final result fields while the stream is undrained"

    gate.set()
    assert wait_for_status(client, handle, {"done"}) == "done"
    result = client.fetch_result(handle)
    assert result["status"] == "done"
    assert result["exit_code"] == 0
    assert result["stdout"] == "chunk-a-chunk-b-final", "final result must carry the complete drained output"
    pad.close()


def test_rapid_real_completion_produces_complete_stable_result(tmp_path: Path) -> None:
    """Fast completion safety: a worker that starts, emits output and exits
    immediately still yields terminal status + complete output + stable result."""
    launcher = tmp_path / "fast-worker.sh"
    launcher.write_text("#!/bin/sh\nprintf 'AOTA_FORGE_S3_M2_W2_FAST\\n'\nexit 0\n")
    launcher.chmod(0o755)
    client = HermesHostClient(str(launcher), default_cwd=str(tmp_path))

    for _ in range(10):
        handle = client.dispatch(host_payload())["adapter_handle"]
        assert wait_for_status(client, handle, {"done", "failed"}) == "done"
        result = client.fetch_result(handle)
        assert result["status"] == "done"
        assert result["exit_code"] == 0
        assert result["stdout"] == "AOTA_FORGE_S3_M2_W2_FAST\n", "result must not be ordering-truncated"
        assert "duration_ms" in result["execution_stats"]
    client.close()


def test_concurrent_observers_never_see_terminal_during_drain(tmp_path: Path) -> None:
    gate = threading.Event()
    pad = DurableSupervisorPad(stdout=b"tail-bytes", stderr=b"")
    client = concrete_client(tmp_path, pad)
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    pad.last.release(0, stream_gate=gate)

    seen: list[str] = []
    seen_lock = threading.Lock()
    stop = threading.Event()

    def poller() -> None:
        while not stop.is_set():
            status = client.query_status(handle)["status"]
            with seen_lock:
                seen.append(status)
            time.sleep(0.005)

    threads = [threading.Thread(target=poller, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    stop.set()
    for thread in threads:
        thread.join(timeout=10)

    with seen_lock:
        assert "done" not in seen and "failed" not in seen, (
            "no observer may see terminal status while the stream is undrained"
        )
    gate.set()
    assert wait_for_status(client, handle, {"done"}) == "done"
    assert client.fetch_result(handle)["stdout"] == "tail-bytes"
    pad.close()


# ---------------------------------------------------------------------------
# B. Result access before completion (§8)
# ---------------------------------------------------------------------------


def test_fetch_result_before_terminal_is_non_terminal_and_safe(tmp_path: Path) -> None:
    pad = DurableSupervisorPad()
    client = concrete_client(tmp_path, pad)
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]

    first = client.fetch_result(handle)
    second = client.fetch_result(handle)
    assert first["status"] in {"running", "pending"}
    assert second == first, "pre-completion result access must be deterministic"

    paths = run_paths(client, handle)
    assert locator.read_receipt(paths) is None, "no durable terminal before the supervisor publishes it"
    assert pad.last.poll() is None, "result access must not disturb the live launch"

    pad.last.release(0)
    assert wait_for_status(client, handle, {"done"}) == "done"
    pad.close()


def test_adapter_result_before_completion_never_projects_success(tmp_path: Path) -> None:
    host = ScriptedHost(status="running", result_envelope={"status": "running"})
    adapter, task_id, handle = bound_adapter(tmp_path, host, "pre-completion")

    result = adapter.result(task_id, handle)

    assert result.ok is False
    assert result.status == "unknown"
    assert result.canonical_task_state == CanonicalTaskState.RUNNING.value
    assert result.error is not None and result.error["code"] == "TASK_STILL_RUNNING"
    assert result.error["retryable"] is True


# ---------------------------------------------------------------------------
# C. Successful completion projection (§11) and private metadata filtering
# ---------------------------------------------------------------------------


def test_valid_done_envelope_projects_canonical_success(tmp_path: Path) -> None:
    host = ScriptedHost(
        status="done",
        result_envelope={
            "status": "done",
            "exit_code": 0,
            "result_data": {"answer": "AOTA_FORGE_S3_M2_W2_OK"},
            "output_artifacts": [{"name": "notes", "kind": "text"}],
            "stdout": "AOTA_FORGE_S3_M2_W2_OK\n",
            "execution_stats": {"duration_ms": 42},
        },
    )
    adapter, task_id, handle = bound_adapter(tmp_path, host, "valid-success")

    result = adapter.result(task_id, handle)

    assert result.ok is True
    assert result.status == "completed"
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert result.exit_code == 0
    assert result.result_data == {"answer": "AOTA_FORGE_S3_M2_W2_OK"}
    assert result.stdout_summary == "AOTA_FORGE_S3_M2_W2_OK\n"
    assert result.canonical_task_id == task_id
    assert result.executor_id == "hermes"
    assert result.error is None
    assert_no_private_projection_leakage(result.to_dict())


def test_private_keys_are_filtered_out_of_projected_result(tmp_path: Path) -> None:
    host = ScriptedHost(
        status="done",
        result_envelope={
            "status": "done",
            "exit_code": 0,
            "result_data": {
                "answer": "ok",
                "hermes_session": "session-1",
                "hermes_profile": "coder_profile",
                "nested": {"hermes_worker": {"pid": 4242}, "keep": "value"},
            },
            "output_artifacts": [{"name": "a", "hermes_trace": "t"}],
            "execution_stats": {"duration_ms": 5, "hermes_task": "ht-1"},
        },
    )
    adapter, task_id, handle = bound_adapter(tmp_path, host, "private-filter")

    result = adapter.result(task_id, handle)
    payload = result.to_dict()

    assert result.ok is True
    assert payload["result_data"] == {"answer": "ok", "nested": {"keep": "value"}}
    assert payload["output_artifacts"][0] == {"name": "a"}
    assert payload["execution_stats"] == {"duration_ms": 5}
    assert_no_private_projection_leakage(payload)


def test_production_host_result_envelope_carries_no_private_identifiers(tmp_path: Path) -> None:
    launcher = str(tmp_path / "launcher-under-test")
    pad = DurableSupervisorPad()
    client = HermesHostClient(
        launcher,
        default_cwd=str(tmp_path),
        popen_factory=pad,
        validate_launcher=False,
        timeout_seconds=30,
        runtime_root=tmp_path / "af-runtime",
    )
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("envelope-privacy", working_context={"cwd": str(tmp_path)}))
    pad.last.release(0)
    assert wait_for_status(client, dispatch_result.adapter_handle, {"done"}) == "done"
    pad.close()

    envelope = client.fetch_result(dispatch_result.adapter_handle)
    assert set(envelope) == {"status", "exit_code", "stdout", "stderr", "execution_stats"}
    canonical = adapter.result("envelope-privacy", dispatch_result.adapter_handle)
    assert canonical.ok is True
    assert_no_private_projection_leakage(
        {"envelope": envelope, "canonical": canonical.to_dict()},
        launcher=launcher,
    )
    json_text = canonical.to_json().lower()
    assert "coder_profile" not in json_text
    assert "unused-launcher" not in json_text


# ---------------------------------------------------------------------------
# D. Known worker failure projection (§12) and failure result access (§16)
# ---------------------------------------------------------------------------


def test_nonzero_exit_is_retained_in_host_envelope(tmp_path: Path) -> None:
    pad = DurableSupervisorPad(stdout=b"", stderr=b"worker blew up")
    client = concrete_client(tmp_path, pad)
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    pad.last.release(7)

    assert wait_for_status(client, handle, {"failed"}) == "failed"
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "failed"
    assert envelope["exit_code"] == 7, "actual non-zero exit code must be retained, never coerced to 0"
    assert envelope["error"]["code"] == "EXECUTION_FAILED"
    assert envelope["stderr"] == "worker blew up"
    pad.close()


def test_known_worker_failure_projects_canonical_failure(tmp_path: Path) -> None:
    host = ScriptedHost(
        status="failed",
        result_envelope={
            "status": "failed",
            "exit_code": 7,
            "stdout": "partial work",
            "stderr": "worker blew up",
            "error": {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"},
        },
    )
    adapter, task_id, handle = bound_adapter(tmp_path, host, "worker-failure")

    result = adapter.result(task_id, handle)

    assert result.ok is False
    assert result.status == "failed"
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.exit_code == 7
    assert result.error is not None and result.error["code"] == "EXECUTION_FAILED"
    assert_no_private_projection_leakage(result.to_dict())


def test_failed_execution_result_access_is_repeatable_without_success_payload(tmp_path: Path) -> None:
    host = ScriptedHost(
        status="failed",
        result_envelope={
            "status": "failed",
            "exit_code": 2,
            "stderr": "boom",
            "error": {"code": "EXECUTION_FAILED", "message": "worker failed"},
        },
    )
    adapter, task_id, handle = bound_adapter(tmp_path, host, "failed-access")

    first = adapter.result(task_id, handle)
    second = adapter.result(task_id, handle)

    assert first.ok is False and first.status == "failed"
    assert first.exit_code == 2 and first.result_data == {}
    assert second.to_json() == first.to_json()
    assert second.canonical_task_state == CanonicalTaskState.FAILED.value


# ---------------------------------------------------------------------------
# E. Process completion without a valid result (§13) + malformed matrix (§14)
# ---------------------------------------------------------------------------

MALFORMED_TERMINAL_FIELDS: list[tuple[str, Any]] = [
    ("exit_code", "not-an-int"),
    ("exit_code", []),
    ("exit_code", None),
    ("exit_code", True),
    ("exit_code", 1.5),
    ("result_data", []),
    ("result_data", "text"),
    ("result_data", 123),
    ("result_data", None),
    ("output_artifacts", {"path": "artifact.txt"}),
    ("output_artifacts", "artifact.txt"),
    ("output_artifacts", [42]),
    ("artifacts", {"path": "artifact.txt"}),
    ("artifacts", ["text-item"]),
]


@pytest.mark.parametrize(("field", "value"), MALFORMED_TERMINAL_FIELDS)
def test_malformed_explicit_terminal_fields_fail_closed(field: str, value: Any) -> None:
    output: dict[str, Any] = {"status": "done", "exit_code": 0}
    output[field] = value

    result = hermes_output_to_canonical_result(output, canonical_task_id="w2-malformed")

    assert result.ok is False
    assert result.status != "completed"
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None and result.error["code"] == "RESULT_MALFORMED"


@pytest.mark.parametrize("envelope", ["not-a-mapping", ["status", "done"], 42, None, {}, {"status": "done-ish"}])
def test_malformed_result_envelope_type_never_completes(envelope: Any) -> None:
    result = hermes_output_to_canonical_result(envelope, canonical_task_id="w2-envelope")

    assert result.ok is False
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value


def test_exit_zero_completion_with_invalid_envelope_fails_closed(tmp_path: Path) -> None:
    """Full pipeline: host says done + exit 0, but the result envelope is
    structurally invalid.  Process exit 0 alone is NOT success authority."""
    host = ScriptedHost(
        status="done",
        result_envelope={"status": "done", "exit_code": 0, "result_data": "text-not-mapping"},
    )
    adapter, task_id, handle = bound_adapter(tmp_path, host, "exit0-invalid")

    result = adapter.result(task_id, handle)

    assert result.ok is False
    assert result.status != "completed"
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None and result.error["code"] == "RESULT_MALFORMED"
    assert result.result_data == {}  # not silently normalized into a payload


def test_absent_optional_fields_follow_contract_but_explicit_malformed_rejected() -> None:
    absent = hermes_output_to_canonical_result({"status": "done"}, canonical_task_id="absent-optional")
    assert absent.ok is True  # optional fields may be absent per accepted contract
    assert absent.exit_code == 0
    assert absent.result_data == {}

    for field, value in (("result_data", None), ("exit_code", "0"), ("artifacts", "text")):
        explicit = {"status": "done", field: value}
        rejected = hermes_output_to_canonical_result(explicit, canonical_task_id=f"explicit-{field}")
        assert rejected.ok is False
        assert rejected.error is not None and rejected.error["code"] == "RESULT_MALFORMED", (
            f"explicit malformed {field} must not collapse into a valid empty default"
        )


# ---------------------------------------------------------------------------
# F. Timeout and cancellation result semantics (§17-§18, regression only)
# ---------------------------------------------------------------------------


def test_real_timeout_result_never_projects_success(tmp_path: Path) -> None:
    launcher = tmp_path / "sleeper.sh"
    launcher.write_text("#!/bin/sh\nexec sleep 30\n")
    launcher.chmod(0o755)
    client = HermesHostClient(
        str(launcher),
        default_cwd=str(tmp_path),
        timeout_seconds=30,
        runtime_root=tmp_path / "af-runtime",
    )
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(
        package("timeout-result", working_context={"cwd": str(tmp_path)}, constraints={"timeout_seconds": 0.3})
    )

    deadline = time.monotonic() + 10.0
    status = None
    result = None
    while time.monotonic() < deadline:
        status = adapter.status(dispatch_result.canonical_task_id, dispatch_result.adapter_handle)
        if status.state in {CanonicalTaskState.UNKNOWN, CanonicalTaskState.COMPLETED}:
            result = adapter.result(dispatch_result.canonical_task_id, dispatch_result.adapter_handle)
            break
        time.sleep(0.05)

    assert status is not None and status.state is CanonicalTaskState.UNKNOWN, "W1 timeout mapping is frozen"
    assert result is not None
    assert result.ok is False
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None and result.error["code"] == "EXECUTION_TIMEOUT"
    client.close()


def test_cancelled_result_projection_stays_cancelled(tmp_path: Path) -> None:
    pad = DurableSupervisorPad()
    client = concrete_client(tmp_path, pad)
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("cancel-result", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle
    assert client.query_status(handle)["status"] == "running"

    cancel = adapter.cancel("cancel-result", handle)
    assert cancel.cancelled is True
    assert cancel.state is CanonicalTaskState.CANCELLED

    result = adapter.result("cancel-result", handle)
    assert result.ok is False
    assert result.status == "cancelled"
    assert result.canonical_task_state == CanonicalTaskState.CANCELLED.value
    assert result.error is not None and result.error["code"] == "EXECUTION_CANCELLED"
    repeated = adapter.result("cancel-result", handle)
    assert repeated.to_json() == result.to_json()


# ---------------------------------------------------------------------------
# G. Result identity guards (§19): rejected before any host fetch_result call
# ---------------------------------------------------------------------------


def _identity_adapter(tmp_path: Path) -> tuple[HermesAdapter, ScriptedHost, dict[str, str]]:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)
    handles: dict[str, str] = {}
    for task_id in ("task-one", "task-two"):
        dispatch_result = adapter.dispatch(package(task_id, working_context={"cwd": str(tmp_path)}))
        handles[task_id] = dispatch_result.adapter_handle
    return adapter, host, handles


@pytest.mark.parametrize(
    ("task_id", "handle_key", "expected_code"),
    [
        ("task-one", "never-issued", "TASK_ID_MISMATCH"),
        ("task-two", "task-one", "TASK_ID_MISMATCH"),
        ("never-dispatched", "task-one", "TASK_ID_MISMATCH"),
        ("never-dispatched", "never-issued", "TASK_HANDLE_NOT_FOUND"),
    ],
)
def test_result_identity_rejections_never_reach_host(
    tmp_path: Path, task_id: str, handle_key: str, expected_code: str
) -> None:
    adapter, host, handles = _identity_adapter(tmp_path)
    handle = handles.get(handle_key, handle_key)

    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.result(task_id, handle)

    assert excinfo.value.code == expected_code
    assert host.calls["result"] == 0, "rejected result access must not touch the host"


def test_inconsistent_result_binding_fails_closed_before_host(tmp_path: Path) -> None:
    adapter, host, handles = _identity_adapter(tmp_path)
    del adapter._task_handles["task-one"]

    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.result("task-one", handles["task-one"])

    assert excinfo.value.code == "ADAPTER_PROTOCOL_ERROR"
    assert host.calls["result"] == 0


def test_blank_result_identity_arguments_rejected_before_lookup(tmp_path: Path) -> None:
    adapter, host, handles = _identity_adapter(tmp_path)

    with pytest.raises(ValueError):
        adapter.result("", handles["task-one"])
    with pytest.raises(ValueError):
        adapter.result("task-one", "  ")
    assert host.calls["result"] == 0


# ---------------------------------------------------------------------------
# H. Status / result consistency matrix (§25)
# ---------------------------------------------------------------------------

CONSISTENCY_CASES = [
    ("running", {"status": "running"}, CanonicalTaskState.RUNNING),
    ("pending", {"status": "pending"}, CanonicalTaskState.QUEUED),
    ("waiting_for_input", {"status": "waiting_for_input"}, CanonicalTaskState.WAITING),
    ("unreachable", {"status": "unreachable"}, CanonicalTaskState.UNKNOWN),
    ("timeout", {"status": "timeout"}, CanonicalTaskState.UNKNOWN),
    (
        "failed",
        {"status": "failed", "exit_code": 1, "error": {"code": "EXECUTION_FAILED", "message": "x"}},
        CanonicalTaskState.FAILED,
    ),
    ("cancelled", {"status": "cancelled"}, CanonicalTaskState.CANCELLED),
]


@pytest.mark.parametrize(("host_status", "envelope", "expected_state"), CONSISTENCY_CASES)
def test_status_and_result_projections_never_contradict(
    tmp_path: Path, host_status: str, envelope: Mapping[str, Any], expected_state: CanonicalTaskState
) -> None:
    host = ScriptedHost(status=host_status, result_envelope=envelope)
    adapter, task_id, handle = bound_adapter(tmp_path, host, f"consistency-{host_status}")

    status = adapter.status(task_id, handle)
    result = adapter.result(task_id, handle)

    assert status.state is expected_state
    assert result.ok is False, "a non-COMPLETED runtime status may never project ok=True"
    assert result.status != "completed"
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value or expected_state is CanonicalTaskState.COMPLETED
    assert_no_private_projection_leakage(result.to_dict())


# ---------------------------------------------------------------------------
# I. Host / runtime failure after dispatch (§26)
# ---------------------------------------------------------------------------


def test_host_fetch_result_exception_never_projects_success(tmp_path: Path) -> None:
    host = ScriptedHost(raises=RuntimeError("runtime vanished"))
    adapter, task_id, handle = bound_adapter(tmp_path, host, "vanished")

    status = adapter.status(task_id, handle)
    result = adapter.result(task_id, handle)

    assert status.state is CanonicalTaskState.UNKNOWN
    assert result.ok is False
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None and result.error["code"] == "ADAPTER_PROTOCOL_ERROR"


def test_process_level_runtime_loss_projects_never_success(tmp_path: Path) -> None:
    """Total runtime loss (supervisor AND worker gone, no receipt) => UNKNOWN.

    W2 durable boundary: evidence absence is typed uncertainty, never a
    fabricated COMPLETED/FAILED and never an automatic re-dispatch.
    """
    pad = DurableSupervisorPad()
    client = concrete_client(tmp_path, pad)
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("runtime-loss", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle
    assert adapter.status("runtime-loss", handle).state is CanonicalTaskState.RUNNING

    pad.last.crash_without_receipt()

    status = adapter.status("runtime-loss", handle)
    result = adapter.result("runtime-loss", handle)

    assert status.state is CanonicalTaskState.UNKNOWN, "undeterminable runtime state must be UNKNOWN"
    assert result.ok is False
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    # Core owns the uncertainty projection spelling (TASK_STATE_UNKNOWN);
    # the adapter must surface UNKNOWN, never a fabricated terminal.
    assert result.error is not None and result.error["retryable"] is True
    pad.close()


def test_pruned_terminal_record_result_access_is_unreachable_unknown(tmp_path: Path) -> None:
    """After an EXPLICIT canonical-safe maintenance pass removes terminal
    evidence, access fails closed as TASK_NOT_FOUND (unknown handle), never a
    stale success.  M2/W4: pruning requires an AF-runtime-supplied eligible
    handle set; new dispatches alone never delete receipts."""
    pad = DurableSupervisorPad(release_on_spawn=0)
    client = HermesHostClient(
        str(tmp_path / "unused"),
        default_cwd=str(tmp_path),
        popen_factory=pad,
        validate_launcher=False,
        retention_seconds=0.0,
        timeout_seconds=30,
        runtime_root=tmp_path / "af-runtime",
    )
    first = client.dispatch(host_payload())["adapter_handle"]
    assert wait_for_status(client, first, {"done"}) == "done"
    second = client.dispatch(host_payload())["adapter_handle"]
    assert second != first
    # The new dispatch did NOT prune the aged terminal receipt (F01 repair).
    assert run_paths(client, first).run_dir.is_dir(), "dispatch must never prune uncanonicalized receipts"

    # Simulate the trusted AF runtime having durably canonicalized this
    # terminal truth, then run the explicit bounded maintenance pass.
    assert client.prune_canonicalized_receipts(eligible_handles=[first]) == 1

    assert not run_paths(client, first).run_dir.is_dir(), "canonically safe evidence past retention is pruned"
    envelope = client.fetch_result(first)
    assert envelope["status"] == "unreachable"
    assert envelope["error"]["code"] == "TASK_NOT_FOUND"
    # The still-referenced run remains fully resolvable.
    assert wait_for_status(client, second, {"done"}) == "done"
    pad.close()


# ---------------------------------------------------------------------------
# J. Bounded output retention (§22)
# ---------------------------------------------------------------------------


def test_oversized_output_is_bounded_and_deterministically_truncated(tmp_path: Path) -> None:
    launcher = tmp_path / "spew.sh"
    launcher.write_text("#!/bin/sh\nyes AOTA_FORGE_S3_M2_W2 | head -c 200000\nexit 0\n")
    launcher.chmod(0o755)
    client = HermesHostClient(
        str(launcher),
        default_cwd=str(tmp_path),
        output_limit_bytes=256,
        runtime_root=tmp_path / "af-runtime",
    )
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("bounded-output", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle

    assert wait_for_status(client, handle, {"done", "failed"}, budget=15) == "done"
    first = client.fetch_result(handle)
    assert len(first["stdout"].encode()) <= 256, "retained output must respect the configured bound"
    assert first["stdout"].endswith("...[output truncated]"), "truncation must be explicitly represented"
    second = client.fetch_result(handle)
    assert second["stdout"] == first["stdout"], "bounded output must be deterministic across fetches"

    canonical = adapter.result("bounded-output", handle)
    assert canonical.ok is True
    assert canonical.stdout_summary is not None
    assert canonical.stdout_summary.endswith("...[output truncated]")
    # Stream text is projection evidence only; truncation never parses into
    # or fabricates a structured payload.
    assert canonical.result_data == {}
    client.close()


# ---------------------------------------------------------------------------
# K. Record retention / eviction (§23)
# ---------------------------------------------------------------------------


def test_active_runs_are_never_evicted_under_capacity_pressure(tmp_path: Path) -> None:
    """W2 capacity bound counts durable ACTIVE runs; live evidence is never
    reclaimed to make room.  M2/W4: terminal evidence past retention is also
    kept until the EXPLICIT canonical-safe maintenance pass — a new dispatch
    under capacity pressure never deletes another execution's terminal
    evidence, aged or not."""
    pad = DurableSupervisorPad()
    client = HermesHostClient(
        str(tmp_path / "unused"),
        default_cwd=str(tmp_path),
        popen_factory=pad,
        validate_launcher=False,
        max_records=2,
        retention_seconds=0.0,
        timeout_seconds=30,
        runtime_root=tmp_path / "af-runtime",
    )
    first = client.dispatch(host_payload())["adapter_handle"]
    second = client.dispatch(host_payload())["adapter_handle"]

    with pytest.raises(Exception, match="EXECUTOR_UNAVAILABLE"):
        client.dispatch(host_payload())  # both active: bound exhausted, nothing prunable

    assert run_paths(client, first).run_dir.is_dir() and run_paths(client, second).run_dir.is_dir()

    pad.launches[0].release(0)
    assert wait_for_status(client, first, {"done"}) == "done"
    third = client.dispatch(host_payload())["adapter_handle"]
    assert run_paths(client, third).run_dir.is_dir()
    # The dispatch-side capacity pass freed the ACTIVE slot (first went
    # terminal), but the aged terminal receipt itself SURVIVES untouched:
    # NEW_DISPATCH_CAN_PRUNE_UNCANONICALIZED_RECEIPT=no.
    assert run_paths(client, first).run_dir.is_dir(), "dispatch must never prune uncanonicalized terminal receipts"
    assert run_paths(client, second).run_dir.is_dir(), "an active run must never be evicted"
    # Only the explicit canonical-safe maintenance pass reclaims it.
    assert client.prune_canonicalized_receipts(eligible_handles=[first]) == 1
    assert not run_paths(client, first).run_dir.is_dir()
    assert run_paths(client, second).run_dir.is_dir(), "active evidence stays protected after maintenance"
    pad.close()


def test_capacity_bound_is_enforced_from_durable_state_before_launch(tmp_path: Path) -> None:
    """The bound is authoritative even when THIS process never launched the
    runs: orphaned active durable evidence (e.g. left by a crashed AF process)
    consumes capacity and must fail dispatch closed before any spawn."""
    root = tmp_path / "af-runtime"
    runner = RecordingRunner()
    client = HermesHostClient(
        str(tmp_path / "unused"),
        default_cwd=str(tmp_path),
        popen_factory=runner,
        validate_launcher=False,
        max_records=2,
        timeout_seconds=30,
        runtime_root=root,
    )
    # Pre-seed two active (receipt-less) durable runs, as a crashed predecessor left them.
    import uuid as _uuid

    from aota_forge.adapters.hermes.locator import (
        write_marker_reserved,
        write_spec,
    )

    for _ in range(2):
        run_id = _uuid.uuid4().hex
        paths = locator.prepare_run(root, run_id)
        write_spec(
            paths,
            hermes_argv=[str(tmp_path / "unused"), "-p", "coder", "-z", "x"],
            cwd=str(tmp_path),
            timeout_seconds=30,
            started_at_wall=time.time(),
            deadline_wall=time.time() + 30,
            output_limit_bytes=64 * 1024,
        )
        write_marker_reserved(
            paths,
            adapter_handle=paths.adapter_handle(),
            canonical_task_id="orphan",
            profile="coder",
            cwd=str(tmp_path),
            launcher=str(tmp_path / "unused"),
            dispatched_at_wall=time.time(),
            deadline_wall=time.time() + 30,
        )

    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        client.dispatch(host_payload())
    assert runner.calls == 0, "capacity exhaustion must never start a process"


# ---------------------------------------------------------------------------
# L. Terminal result repeatability (§24)
# ---------------------------------------------------------------------------


def test_terminal_result_fetch_is_repeatable_and_non_consuming(tmp_path: Path) -> None:
    pad = DurableSupervisorPad(stdout=b"repeatable-output\n", release_on_spawn=0)
    client = concrete_client(tmp_path, pad)
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    assert wait_for_status(client, handle, {"done"}) == "done"

    envelopes = [client.fetch_result(handle) for _ in range(5)]
    assert all(envelope == envelopes[0] for envelope in envelopes)
    assert envelopes[0]["stdout"] == "repeatable-output\n"
    assert len(pad.launches) == 1 and all(len(c["args"]) == 4 for c in pad.calls), (
        "result fetch must never rerun the worker"
    )
    assert run_paths(client, handle).run_dir.is_dir(), "result evidence not consumed/deleted"
    pad.close()

    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("repeatable", working_context={"cwd": str(tmp_path)}))
    assert wait_for_status(client, dispatch_result.adapter_handle, {"done"}) == "done"
    results = [adapter.result("repeatable", dispatch_result.adapter_handle) for _ in range(3)]
    assert {result.to_json() for result in results} == {results[0].to_json()}
    assert results[0].canonical_task_id == "repeatable"
    assert results[0].ok is True


# ---------------------------------------------------------------------------
# M. W2 boundary freeze: projection is not reconciliation (§28 / #19 boundary)
# ---------------------------------------------------------------------------


def test_projection_stops_at_canonical_result_and_owns_no_task_state(tmp_path: Path) -> None:
    """CanonicalResult availability is INPUT to later S2 reconciliation.

    The Hermes adapter must not mutate any canonical task control state: it
    keeps only private handle bindings, and the Hermes runtime surfaces contain
    no reconciliation/TaskRecord authority.
    """
    host = ScriptedHost(status="done", result_envelope={"status": "done", "exit_code": 0})
    adapter, task_id, handle = bound_adapter(tmp_path, host, "boundary-freeze")
    before_handles = dict(adapter._handle_tasks)
    before_tasks = dict(adapter._task_handles)

    result = adapter.result(task_id, handle)

    assert result.ok is True
    assert adapter._handle_tasks == before_handles
    assert adapter._task_handles == before_tasks
    forbidden_attrs = [name for name in dir(adapter) if "reconcil" in name or "task_record" in name]
    assert forbidden_attrs == []
    for source in ("aota_forge/adapters/hermes/host_client.py", "aota_forge/adapters/hermes/executor.py"):
        assert re.search(r"TaskRecord|reconcil", (REPO_ROOT / source).read_text()) is None, (
            f"S2 reconciliation authority leaked into {source}"
        )
