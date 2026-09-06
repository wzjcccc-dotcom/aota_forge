"""S3/M3/W2: Hermes reference path validation on the CURRENT joint frontier.

Rendezvous: RENDEZVOUS_ID=S2S3-JRV1 (current-frontier revalidation).

Source frontier under test:
    ab5972e6203b278d3772e277e0366b594ce17dad
    = merge(S3/M2 accepted 6bbb247 + S2/M2 accepted 0fb5b24)

Unlike the prior S2/M2-W3 JRV1 (which converged S2/M1 + S3/M1 lineage and did
not import unaccepted S3/M2 lane source), every joint semantics under test here
includes the accepted S3/M2 runtime fixes:

    * W2 drain fix: terminal publication is gated on stdout/stderr stabilization
      (a Forge status observer may legitimately see RUNNING after OS process
      exit -- S3_DRAIN_DELAY_COMPATIBLE_WITH_S2_RECONCILIATION).
    * W3 reader-failure fix: a broken capture with exit code 0 fails closed as
      RESULT_UNAVAILABLE / canonical failure -- never a false success.

Primary canonical chain proven here (controlled + bounded real):

    execution.task_start (unified ingress)
    -> ExecutionPackage
    -> ExecutionDispatcher (accepted production composition)
    -> HermesAdapter
    -> HermesHostClient (concrete production class)
    -> bounded worker execution
    -> adapter_handle -> execution.task_status -> execution.task_result
    -> CanonicalTaskState / CanonicalResult
    -> Forge/Core route reconciliation

The bounded real Hermes probe is opt-in via AOTA_S3_REAL_HERMES_SMOKE=1 and
skips ONLY when the accepted production launcher or default working directory
is unavailable; a skip never counts as JRV1 acceptance.

Durability stays D0 / process-local: no restart recovery, no cross-process
route claims, no persistent task-main, no cancel/resume expansion.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from aota_forge.adapters.hermes.executor import HERMES_EXECUTOR_ID, HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.composition.execution import (
    PRODUCTION_HERMES_DEFAULT_CWD,
    bind_production_execution_dispatcher,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution.results import FORBIDDEN_HERMES_RESULT_KEYS
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import execute, reset_execution_dispatcher
from aota_forge.runtime.config import SHARED_MCP_TOOLSET, RuntimeBinding, RuntimeConfig

JOINT_MARKER = "AOTA_FORGE_S3_M3_W2_JOINT_DONE"
REAL_MARKER = "AOTA_FORGE_S3_M3_W2_OK"
REAL_WORKER_INSTRUCTION = (
    f"Return exactly: {REAL_MARKER}\nDo not use tools.\nDo not modify files."
)

REAL_WORKER_TIMEOUT_SECONDS = 120
REAL_POLL_INTERVAL_SECONDS = 2.0
REAL_POLL_COUNT_LIMIT = 75  # ~150s wall bound, larger than the host deadline

TERMINAL_STATE_VALUES = {s.value for s in CanonicalTaskState if s.is_terminal}

PRODUCTION_HOST_CLIENT_SOURCE = inspect.getsource(HermesHostClient)


def _discovered_hermes_executable() -> Path | None:
    """Opt-in real slice: locate the Hermes binary the operator would pin.

    The production composition no longer carries source-owned executable
    authority, so the bounded real probe discovers the executable explicitly.
    """
    import shutil

    found = shutil.which("hermes")
    if found is None:
        return None
    path = Path(found)
    if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def _real_slice_runtime_config() -> RuntimeConfig | None:
    executable = _discovered_hermes_executable()
    if executable is None:
        return None
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor="hermes",
            profile="aota-worker" if role != "task-main" else "aota-task-main",
            provider="opencode-go",
            model="deepseek-v4-flash",
            concurrency=1,
            executable=str(executable),
            toolsets=(SHARED_MCP_TOOLSET,) if role != "task-main" else None,
        )
        for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
    )
    return RuntimeConfig(
        executor="hermes",
        executable=str(executable),
        concurrency=1,
        provider="opencode-go",
        model="deepseek-v4-flash",
        bindings=bindings,
    )


# ---------------------------------------------------------------------------
# Deterministic offline seams (controlled processes; no real Hermes)
# ---------------------------------------------------------------------------


class ControlledProcess:
    """Fake process whose exit is released explicitly by the test."""

    def __init__(self, exit_code: int = 0, stdout: Any = None, stderr: Any = None) -> None:
        self._exit_code = exit_code
        self.returncode: int | None = None
        self.pid = 999_001
        self.stdout = stdout if stdout is not None else io.BytesIO(b"controlled stdout")
        self.stderr = stderr if stderr is not None else io.BytesIO(b"controlled stderr")
        self.terminate_calls = 0

    def release(self, code: int | None = None) -> None:
        self.returncode = self._exit_code if code is None else code

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout if timeout is not None else 30.0)
        while True:
            code = self.poll()
            if code is not None:
                return code
            if time.monotonic() >= deadline:
                raise TimeoutError("controlled process not released")
            time.sleep(0.002)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.release(-15)

    def kill(self) -> None:
        self.terminate_calls += 1
        self.release(-9)


class GatedStream:
    """Stream that keeps draining after process exit until the gate opens."""

    def __init__(self, chunks: list[bytes], gate: threading.Event) -> None:
        self._chunks = list(chunks)
        self.gate = gate

    def read(self, size: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        self.gate.wait(timeout=20)
        return b""


class ExplodingStream:
    """Returns partial output once, then fails mid-read (broken capture)."""

    def __init__(self, partial: bytes = b"half-a-result") -> None:
        self._partial: bytes | None = partial

    def read(self, size: int = -1) -> bytes:
        if self._partial is not None:
            chunk = self._partial
            self._partial = None
            return chunk
        raise OSError("SECRET_STREAM_TOKEN=broken")


class RecordingRunner:
    """popen_factory handing out one controlled process per call."""

    def __init__(self, factory: Any) -> None:
        self.calls = 0
        self.processes: list[Any] = []
        self._factory = factory

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls += 1
        process = self._factory()
        self.processes.append(process)
        return process


class ScriptedJointHost:
    """Deterministic fake host client; conforms to the frozen Hermes host protocol."""

    def __init__(self) -> None:
        self.dispatched_payloads: list[dict[str, Any]] = []
        self.status_by_handle: dict[str, Any] = {}
        self.result_by_handle: dict[str, Any] = {}
        self.dispatch_calls = 0

    @staticmethod
    def handle_for(canonical_task_id: str) -> str:
        return f"joint-w2-handle-{canonical_task_id}"

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatch_calls += 1
        self.dispatched_payloads.append(dict(payload))
        task_id = payload["context"]["canonical_task_id"]
        return {
            "adapter_handle": self.handle_for(task_id),
            "status": "pending",
            "dispatch_time": "2026-08-29T00:00:00Z",
        }

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        scripted = self.status_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return dict(scripted)

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        scripted = self.result_by_handle.get(adapter_handle, {"status": "unreachable"})
        if isinstance(scripted, Exception):
            raise scripted
        return dict(scripted)

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": False, "status": "running", "details": "out of W2 scope"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


def controlled_client(runner: RecordingRunner, tmp_path: Path) -> HermesHostClient:
    return HermesHostClient(
        str(tmp_path / "unused-joint-w2-launcher"),
        default_cwd=str(tmp_path),
        popen_factory=runner,
        validate_launcher=False,
        timeout_seconds=30,
    )


def _iter_keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_keys(item)


def assert_no_private_hermes_leakage(obj: Any, *, banned_text: tuple[str, ...] = ()) -> None:
    """No Hermes-private runtime semantics may escape into canonical projections."""
    keys = {key.lower() for key in _iter_keys(obj)}
    for token in FORBIDDEN_HERMES_RESULT_KEYS:
        assert token not in keys
    for token in ("pid", "process", "profile", "cwd", "readers", "deadline", "terminal_at", "started_at"):
        assert token not in keys, f"private runtime key escaped into projection: {token!r}"
    text = json.dumps(obj, default=str, sort_keys=True).lower()
    for marker in ("popen(", "threading.object", "subprocess.popen", " -z ", "processregistry"):
        assert marker not in text, f"private runtime marker leaked into projection: {marker!r}"
    for fragment in banned_text:
        assert fragment.lower() not in text, f"private value leaked into projection: {fragment!r}"


def assert_never_false_success(envelope: Mapping[str, Any]) -> None:
    data = envelope["data"]
    if data["ok"] is True:
        assert data["status"] == "completed"
        assert data["canonical_task_state"] == CanonicalTaskState.COMPLETED.value
    else:
        assert data["status"] != "completed"
        assert data["canonical_task_state"] != CanonicalTaskState.COMPLETED.value


@pytest.fixture()
def joint_ingress():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


def start_task(task_id: str, instruction: str, timeout: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "canonical_task_id": task_id,
        "executor": HERMES_EXECUTOR_ID,
        "role": "coder",
        "instruction": instruction,
        "project_id": "aota_forge",
    }
    if timeout is not None:
        params["timeout"] = timeout
    return execute("execution.task_start", params)


def query_status(task_id: str) -> dict[str, Any]:
    return execute("execution.task_status", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID})


def query_result(task_id: str) -> dict[str, Any]:
    return execute("execution.task_result", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID})


def wait_for_terminal(
    task_id: str, budget: float = 15.0, prior: list[str] | None = None
) -> tuple[str, list[str]]:
    observed: list[str] = list(prior or [])
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        envelope = query_status(task_id)
        assert envelope["ok"], f"canonical task_status failed: {envelope}"
        state = envelope["data"]["state"]
        if not observed or observed[-1] != state:
            observed.append(state)
        if state in TERMINAL_STATE_VALUES:
            return state, observed
        time.sleep(0.02)
    raise AssertionError(f"no terminal state observed within {budget}s; observed {observed}")


# ---------------------------------------------------------------------------
# A. Controlled full canonical ingress -> Forge reconciliation
# ---------------------------------------------------------------------------


def test_joint_canonical_ingress_completion_reconciles_forge(joint_ingress) -> None:
    host = ScriptedJointHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)

    task_id = "s3-m3-w2-joint-completed"
    start = start_task(task_id, "prove current-frontier canonical completion")
    assert start["ok"], f"task_start failed: {start}"
    assert start["audit"]["validation"] == "ok"
    handle = start["data"]["adapter_handle"]
    assert handle == ScriptedJointHost.handle_for(task_id)
    assert CanonicalTaskState(start["data"]["initial_state"]) is CanonicalTaskState.QUEUED

    # role -> profile translation happens only inside the adapter/host payload
    # (M1 sync: shared Worker profile + shared MCP toolset pin)
    assert host.dispatched_payloads[0]["profile"] == "aota-worker"
    assert host.dispatched_payloads[0]["toolsets"] == ["aota"]

    # QUEUED -> RUNNING -> COMPLETED, reconciled through canonical status
    host.status_by_handle[handle] = {"status": "running"}
    running = query_status(task_id)
    assert running["ok"] and running["data"]["state"] == CanonicalTaskState.RUNNING.value
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.RUNNING

    host.status_by_handle[handle] = {"status": "done"}
    completed = query_status(task_id)
    assert completed["ok"] and completed["data"]["state"] == CanonicalTaskState.COMPLETED.value

    host.result_by_handle[handle] = {
        "status": "done",
        "exit_code": 0,
        "stdout_summary": JOINT_MARKER,
    }
    result = query_result(task_id)
    assert result["ok"] and result["data"]["ok"] is True
    assert result["data"]["status"] == "completed"
    assert result["data"]["canonical_task_state"] == CanonicalTaskState.COMPLETED.value
    assert result["data"]["canonical_task_id"] == task_id
    assert result["data"]["executor_id"] == HERMES_EXECUTOR_ID
    assert result["data"]["stdout_summary"] == JOINT_MARKER

    # Forge reconciliation: Core route holds the canonical terminal truth
    route = dispatcher.get_route(task_id)
    assert route.last_known_state is CanonicalTaskState.COMPLETED
    assert route.canonical_task_id == task_id
    assert route.adapter_handle == handle

    # terminal state is sticky and consistent on replay
    again = query_status(task_id)
    assert again["data"]["state"] == CanonicalTaskState.COMPLETED.value
    replay = query_result(task_id)
    assert replay["data"]["ok"] is True
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.COMPLETED


def test_identity_domains_remain_distinct_through_canonical_path(joint_ingress) -> None:
    host = ScriptedJointHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "s3-m3-w2-identity"
    start = start_task(task_id, "identity boundary check")
    assert start["ok"], start

    route = dispatcher.get_route(task_id)
    handle = route.adapter_handle
    ids = {
        "canonical_task_id": route.canonical_task_id,
        "adapter_handle": handle,
        "package_id": route.package_id,
        "dispatch_attempt_id": route.dispatch_attempt_id,
    }
    assert len(set(ids.values())) == 4, f"identity domains collapsed: {ids}"
    assert route.canonical_task_id == task_id

    # Hermes-private host handle prefix must never become canonical authority
    assert handle.startswith("joint-w2-handle-")
    status = query_status(task_id)
    assert status["data"]["canonical_task_id"] == task_id
    assert "adapter_handle" not in status["data"], "private handle must stay out of canonical status"


# ---------------------------------------------------------------------------
# B. UNKNOWN safety and result-safety contracts consumed by S2 reconciliation
# ---------------------------------------------------------------------------


def test_unknown_runtime_state_never_false_completion_joint(joint_ingress) -> None:
    host = ScriptedJointHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "s3-m3-w2-unknown"
    start = start_task(task_id, "unknown safety")
    handle = start["data"]["adapter_handle"]

    # default scripted "unreachable"
    status = query_status(task_id)
    assert status["ok"] and status["data"]["state"] == CanonicalTaskState.UNKNOWN.value
    result = query_result(task_id)
    assert_never_false_success(result)
    assert result["data"]["error"]["code"] == "TASK_STATE_UNKNOWN"
    assert dispatcher.get_route(task_id).last_known_state is not CanonicalTaskState.COMPLETED

    # recovery: later definitive evidence still reconciles honestly
    host.status_by_handle[handle] = {"status": "done"}
    recovered = query_status(task_id)
    assert recovered["data"]["state"] == CanonicalTaskState.COMPLETED.value
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.COMPLETED


def test_malformed_terminal_result_fails_closed_joint(joint_ingress) -> None:
    host = ScriptedJointHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "s3-m3-w2-malformed"
    start = start_task(task_id, "malformed safety")
    handle = start["data"]["adapter_handle"]

    host.status_by_handle[handle] = {"status": "done"}
    for malformed in (
        {"status": "done", "exit_code": "0"},
        {"status": "done", "result_data": "not-a-mapping"},
        {"status": "done", "output_artifacts": "not-a-list"},
    ):
        host.result_by_handle[handle] = malformed
        envelope = query_result(task_id)
        assert envelope["ok"], f"transport envelope must remain well-formed: {envelope}"
        assert_never_false_success(envelope)
        assert envelope["data"]["error"]["code"] == "RESULT_MALFORMED"
        assert envelope["data"]["canonical_task_state"] != CanonicalTaskState.COMPLETED.value

    # a never-repaired malformed stream can never yield a successful terminal state
    route = dispatcher.get_route(task_id)
    assert route.last_known_state is not CanonicalTaskState.COMPLETED


def test_unavailable_active_result_matches_accepted_s2_contract(joint_ingress) -> None:
    host = ScriptedJointHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "s3-m3-w2-active-result"
    start = start_task(task_id, "active result behavior")
    handle = start["data"]["adapter_handle"]

    host.status_by_handle[handle] = {"status": "running"}
    host.result_by_handle[handle] = {"status": "running"}
    running = query_status(task_id)
    assert running["data"]["state"] == CanonicalTaskState.RUNNING.value

    # Accepted current first-slice behavior: TASK_STILL_RUNNING while RUNNING
    active = query_result(task_id)
    assert active["ok"], active
    assert_never_false_success(active)
    assert active["data"]["error"]["code"] == "TASK_STILL_RUNNING"
    assert active["data"]["canonical_task_state"] == CanonicalTaskState.RUNNING.value
    assert active["data"]["status"] == "unknown"
    assert not dispatcher.get_route(task_id).last_known_state.is_terminal

    host.result_by_handle[handle] = {"status": "done", "exit_code": 0, "stdout_summary": JOINT_MARKER}
    host.status_by_handle[handle] = {"status": "done"}
    final = query_result(task_id)
    assert final["data"]["ok"] is True
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.COMPLETED


# ---------------------------------------------------------------------------
# C. S3/M2 W2 drain-fix regression through the JOINT canonical path
# ---------------------------------------------------------------------------


def test_real_host_drain_delay_compatible_with_forge_reconciliation(joint_ingress, tmp_path) -> None:
    """Terminal publication waits for output stabilization; S2 must tolerate it."""
    gate = threading.Event()
    process = ControlledProcess(exit_code=0, stdout=GatedStream([b"head-", b"tail"], gate), stderr=io.BytesIO(b""))
    client = controlled_client(RecordingRunner(lambda: process), tmp_path)
    dispatcher = bind_production_execution_dispatcher(host_client=client)

    task_id = "s3-m3-w2-drain-delay"
    start = start_task(task_id, "drain delay joint regression")
    assert start["ok"], start
    assert CanonicalTaskState(start["data"]["initial_state"]) is CanonicalTaskState.QUEUED

    # OS process exits while the stdout reader has not stabilized
    process.release(0)
    assert process.poll() == 0, "the underlying runtime process is terminal"
    drain_observations: list[str] = [CanonicalTaskState.QUEUED.value]
    for _ in range(3):
        during = query_status(task_id)
        assert during["ok"], during
        assert during["data"]["state"] == CanonicalTaskState.RUNNING.value, (
            "terminal must not be published before the output stream stabilizes"
        )
        drain_observations.append(during["data"]["state"])
    assert not dispatcher.get_route(task_id).last_known_state.is_terminal

    early = query_result(task_id)
    assert_never_false_success(early)
    assert early["data"]["error"]["code"] == "TASK_STILL_RUNNING"

    # Output stabilizes -> terminal is now observable with the complete capture
    gate.set()
    terminal, observed = wait_for_terminal(task_id, prior=drain_observations)
    assert terminal == CanonicalTaskState.COMPLETED.value
    assert list(dict.fromkeys(observed)) == [
        CanonicalTaskState.QUEUED.value,
        CanonicalTaskState.RUNNING.value,
        CanonicalTaskState.COMPLETED.value,
    ], f"S2-visible lifecycle across the drain boundary: {observed}"
    assert observed[-1] == CanonicalTaskState.COMPLETED.value

    result = query_result(task_id)
    assert result["data"]["ok"] is True
    assert result["data"]["exit_code"] == 0
    assert result["data"]["stdout_summary"] == "head-tail", (
        "result must carry the fully drained output, never a frozen partial stream"
    )
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.COMPLETED
    client.close()


# ---------------------------------------------------------------------------
# D. S3/M2 W3 reader-failure regression through the JOINT canonical path
# ---------------------------------------------------------------------------


def test_reader_failure_with_exit_zero_fails_closed_joint(joint_ingress, tmp_path) -> None:
    process = ControlledProcess(exit_code=0, stdout=ExplodingStream(), stderr=io.BytesIO(b""))
    client = controlled_client(RecordingRunner(lambda: process), tmp_path)
    bind_production_execution_dispatcher(host_client=client)

    task_id = "s3-m3-w2-reader-failure"
    start = start_task(task_id, "reader failure joint regression")
    assert start["ok"], start
    process.release(0)

    terminal, observed = wait_for_terminal(task_id)
    assert terminal == CanonicalTaskState.FAILED.value, (
        f"READER_FAILURE_FALSE_SUCCESS: exit 0 with broken capture reached {terminal}; {observed}"
    )

    result = query_result(task_id)
    assert_never_false_success(result)
    assert result["data"]["canonical_task_state"] == CanonicalTaskState.FAILED.value
    assert result["data"]["error"]["code"] == "RESULT_UNAVAILABLE"
    assert "SECRET_STREAM_TOKEN" not in json.dumps(result, default=str), (
        "raw reader exception text must remain adapter-private"
    )
    assert_no_private_hermes_leakage(result)
    client.close()


# ---------------------------------------------------------------------------
# E. Security / invocation boundary regressions around the joint path
# ---------------------------------------------------------------------------


def test_argument_construction_avoids_shell_evaluation(joint_ingress, tmp_path) -> None:
    """Instructions travel as one argv element; nothing is shell-interpreted.

    W4 sync (§24): the real property is list-form argv with per-flag bounded
    values, not a fixed argument index. The probe launcher echoes every argv
    element on its own line, so the assertion below checks structure rather
    than position.
    """
    sentinel = tmp_path / "shell-pwned"
    injection = (
        f"Report: {JOINT_MARKER} $(touch {sentinel}) `touch {sentinel}` ; touch {sentinel}"
    )
    echo_launcher = tmp_path / "joint-w2-arg-echo.sh"
    echo_launcher.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$0"\nfor arg in "$@"; do printf \'%s\\n\' "$arg"; done\n'
    )
    echo_launcher.chmod(0o755)

    client = HermesHostClient(str(echo_launcher), default_cwd=str(tmp_path), timeout_seconds=30)
    bind_production_execution_dispatcher(host_client=client)

    task_id = "s3-m3-w2-argv-safety"
    start = start_task(task_id, injection)
    assert start["ok"], start
    terminal, _ = wait_for_terminal(task_id)
    assert terminal == CanonicalTaskState.COMPLETED.value

    result = query_result(task_id)
    assert result["data"]["ok"] is True
    stdout = result["data"]["stdout_summary"] or ""
    argv = stdout.splitlines()

    # the instruction arrives as exactly ONE verbatim argv element, attached to -z
    assert argv.count(injection) == 1, "instruction must arrive as a single argv element"
    assert argv[argv.index("-z") + 1] == injection, "instruction must follow -z as one element"

    # flags and values are separately bounded elements (operator config pins)
    assert argv[0] == str(echo_launcher)
    for flag, value in (
        ("-p", "aota-worker"),
        ("-t", "aota"),
        ("--provider", "aota-test-provider"),
        ("-m", "aota-test-model"),
    ):
        assert flag in argv, f"missing bounded flag element {flag}"
        assert argv[argv.index(flag) + 1] == value
    assert not any(
        line != injection and ("$(touch" in line or "`touch" in line or "; touch" in line)
        for line in argv
    ), "shell metacharacters must never be split/merged into other argv elements"
    assert not sentinel.exists(), "SHELL_INJECTION: shell metacharacters must never be evaluated"

    # production construction mechanics: list argv, no shell, inherited environment only
    dispatch_source = inspect.getsource(HermesHostClient.dispatch)
    assert "shell=True" not in PRODUCTION_HOST_CLIENT_SOURCE
    assert "shell=" not in dispatch_source, "no shell execution at the host seam"
    assert "env=" not in dispatch_source, "no explicit environment rewriting at the host seam"
    client.close()


def test_profile_and_working_directory_boundaries_fail_closed(joint_ingress, tmp_path) -> None:
    launches = RecordingRunner(lambda: ControlledProcess())
    client = controlled_client(launches, tmp_path)
    bind_production_execution_dispatcher(host_client=client)

    base_payload: dict[str, Any] = {
        "profile": "aota-worker",
        "instruction": "boundary probe",
        "context": {"working_context": {}},
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "w2-boundary",
    }
    bad_payloads = [
        {**base_payload, "profile": "bad profile"},
        {**base_payload, "profile": ""},
        {**base_payload, "toolsets": []},
        {**base_payload, "toolsets": ["aota terminal"]},
        {**base_payload, "toolsets": ["aota", "aota"]},
        {**base_payload, "toolsets": ["aota,terminal"]},
        {
            **base_payload,
            "context": {
                "working_context": {"cwd": str(tmp_path), "repo_root": "/etc/elsewhere"}
            },
        },
        {**base_payload, "context": {"working_context": {"cwd": str(tmp_path / "missing-dir")}}},
        {**base_payload, "context": {"working_context": {"arbitrary": "payload"}}},
    ]
    for payload in bad_payloads:
        with pytest.raises(Exception) as raised:
            client.dispatch(payload)
        assert type(raised.value).__name__ in {"HermesHostClientError", "HermesHostUnavailableError"}
    assert launches.calls == 0, "rejected payloads must never start a process"

    # canonical role boundary at the ingress itself: unknown role fails pre-dispatch
    bad_role = execute(
        "execution.task_start",
        {
            "canonical_task_id": "s3-m3-w2-bad-role-2",
            "executor": HERMES_EXECUTOR_ID,
            "role": "schemer",
            "instruction": "unknown canonical role",
            "project_id": "aota_forge",
        },
    )
    assert not bad_role["ok"]
    assert bad_role["error"]["code"] == "PACKAGE_INVALID"
    from aota_forge.core.ingress import get_execution_dispatcher

    assert not get_execution_dispatcher().has_route("s3-m3-w2-bad-role-2")

    # valid canonical role without a Worker binding fails closed before host
    # (M1 sync: all four Worker canonical roles are bound to the shared
    # profile; the legacy "executor" canonical role has no Worker binding)
    unmapped = execute(
        "execution.task_start",
        {
            "canonical_task_id": "s3-m3-w2-unmapped-role",
            "executor": HERMES_EXECUTOR_ID,
            "role": "executor",
            "instruction": "role has no worker binding",
            "project_id": "aota_forge",
        },
    )
    assert not unmapped["ok"]
    assert unmapped["error"]["code"] == "CAPABILITY_MISMATCH"
    assert unmapped["audit"]["handler"] == "capability_mismatch"
    assert launches.calls == 0, "role boundary failures must never launch a process"
    client.close()


# ---------------------------------------------------------------------------
# F. Private Hermes semantics containment on the joint path
# ---------------------------------------------------------------------------


def test_private_hermes_semantics_stay_inside_the_adapter(joint_ingress, tmp_path) -> None:
    process = ControlledProcess(exit_code=0, stdout=io.BytesIO(b"private-check"), stderr=io.BytesIO(b""))
    client = controlled_client(RecordingRunner(lambda: process), tmp_path)
    dispatcher = bind_production_execution_dispatcher(host_client=client)

    task_id = "s3-m3-w2-privacy"
    start = start_task(task_id, "privacy containment")
    handle = start["data"]["adapter_handle"]
    process.release(0)
    terminal, _ = wait_for_terminal(task_id)
    assert terminal == CanonicalTaskState.COMPLETED.value
    result = query_result(task_id)

    combined = {"start": start, "result": result}
    assert_no_private_hermes_leakage(combined, banned_text=(str(tmp_path), "999001"))

    # adapter-private bindings exist only inside the adapter, never in Core schema
    adapter = dispatcher.registry.get(HERMES_EXECUTOR_ID)
    assert isinstance(adapter, HermesAdapter)
    assert adapter._task_handles[task_id] == handle  # noqa: SLF001
    route_fields = set(dispatcher.get_route(task_id).to_dict())
    assert "profile" not in route_fields and "cwd" not in route_fields
    assert not any(key.startswith("hermes_") for key in route_fields)
    client.close()


# ---------------------------------------------------------------------------
# G. Bounded real Hermes vertical slice on the current frontier (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AOTA_S3_REAL_HERMES_SMOKE") != "1",
    reason="bounded real Hermes current-frontier slice (requires a trusted Hermes executable)",
)
def test_current_frontier_real_hermes_vertical_slice(joint_ingress) -> None:
    # M1/W4 sync: the production composition has no source-owned launcher
    # constant; the bounded real probe supplies an explicit operator-style
    # config fixture discovered from the environment.
    from aota_forge.composition import execution as composition_module

    composition_source = inspect.getsource(composition_module)
    assert "/usr/local/bin/hermes" not in composition_source, (
        "legacy container wrapper must not be part of the production composition"
    )
    assert "/home/latios" not in composition_source, (
        "source-owned host deployment path must not return to production composition"
    )
    real_config = _real_slice_runtime_config()
    if real_config is None:
        pytest.skip("production Hermes executable unavailable via environment discovery (RUNTIME_ENVIRONMENT)")
    if not Path(PRODUCTION_HERMES_DEFAULT_CWD).is_dir():
        pytest.skip(f"production default cwd unavailable at {PRODUCTION_HERMES_DEFAULT_CWD} (RUNTIME_ENVIRONMENT)")

    baseline_threads = threading.active_count()

    # No host_client / factory arguments: the accepted production composition
    # must build the real HermesHostClient against the operator config executable.
    dispatcher = bind_production_execution_dispatcher(runtime_config=real_config)
    adapter = dispatcher.registry.get(HERMES_EXECUTOR_ID)
    assert isinstance(adapter, HermesAdapter)
    client = adapter._host_client  # noqa: SLF001
    assert type(client) is HermesHostClient
    assert client.launcher_path == real_config.executable

    task_id = f"s3-m3-w2-real-{int(time.time())}"
    pid: int | None = None
    try:
        start = start_task(task_id, REAL_WORKER_INSTRUCTION, timeout=REAL_WORKER_TIMEOUT_SECONDS)
        assert start["ok"], f"real canonical task_start failed: {start}"
        data = start["data"]
        handle = data["adapter_handle"]
        assert isinstance(handle, str) and handle.strip()
        assert CanonicalTaskState(data["initial_state"]) in {
            CanonicalTaskState.QUEUED,
            CanonicalTaskState.RUNNING,
            CanonicalTaskState.WAITING,
        }

        route = dispatcher.get_route(task_id)
        assert route.executor_id == HERMES_EXECUTOR_ID
        assert route.adapter_handle == handle
        pairwise = {
            route.canonical_task_id,
            route.adapter_handle,
            route.package_id,
            route.dispatch_attempt_id,
        }
        assert len(pairwise) == 4, "current frontier collapsed canonical identity domains"

        # Hermes-private runtime identity is observable only through private seams
        pid = client._records[handle].process.pid  # noqa: SLF001

        observed: list[str] = [data["initial_state"]]
        terminal: str | None = None
        active_probe_seen = False
        for _ in range(REAL_POLL_COUNT_LIMIT):
            status = query_status(task_id)
            assert status["ok"], f"real status query failed: {status}"
            state = status["data"]["state"]
            assert state in {s.value for s in CanonicalTaskState}
            if state != observed[-1]:
                observed.append(state)
            if state in TERMINAL_STATE_VALUES:
                terminal = state
                break
            if not active_probe_seen:
                probe = query_result(task_id)
                active_probe_seen = True
                assert probe["ok"], f"active-result envelope malformed: {probe}"
                assert_never_false_success(probe)
                if not probe["data"]["ok"]:
                    # accepted S2 contract: TASK_STILL_RUNNING while non-terminal
                    assert probe["data"]["error"]["code"] in {"TASK_STILL_RUNNING", "TASK_STATE_UNKNOWN"}
            time.sleep(REAL_POLL_INTERVAL_SECONDS)

        assert terminal == CanonicalTaskState.COMPLETED.value, (
            f"real Hermes worker did not complete; observed {observed}"
        )

        result = query_result(task_id)
        assert result["ok"], f"canonical task_result failed: {result}"
        assert_never_false_success(result)
        rdata = result["data"]
        assert rdata["ok"] is True
        assert rdata["status"] == "completed"
        assert rdata["canonical_task_state"] == CanonicalTaskState.COMPLETED.value
        assert rdata["canonical_task_id"] == task_id
        assert rdata["executor_id"] == HERMES_EXECUTOR_ID
        assert rdata["exit_code"] == 0
        assert REAL_MARKER in (rdata["stdout_summary"] or ""), (
            "real worker output must be projected through the canonical result"
        )

        # canonical projections carry no Hermes-private semantics or credentials
        combined = {"status": status, "result": result}
        assert_no_private_hermes_leakage(combined)
        lowered = json.dumps(combined, default=str).lower()
        for banned in ("hermes-host", "/usr/local/bin/hermes", "runtime.env", str(pid), "-z "):
            assert banned not in lowered, f"private/legacy marker escaped into real projection: {banned}"

        # Forge reconciliation terminal stickiness on the real path
        reconciled = dispatcher.get_route(task_id)
        assert reconciled.last_known_state is CanonicalTaskState.COMPLETED
        post = query_status(task_id)
        assert post["data"]["state"] == CanonicalTaskState.COMPLETED.value
        replay = query_result(task_id)
        assert replay["data"]["ok"] is True
    finally:
        client.close()

    # cleanup: worker reaped, no lingering owned threads
    if pid is not None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("LINGERING_W2_PROBE_PROCESSES: real worker did not exit after close")
    settle = time.monotonic() + 10
    while threading.active_count() > baseline_threads and time.monotonic() < settle:
        time.sleep(0.1)
    assert threading.active_count() <= baseline_threads, "W2 probe leaked threads"
