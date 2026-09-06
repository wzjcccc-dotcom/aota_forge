"""Focused S3/M2/W3 tests: failure / unknown boundary construction.

Governing invariant for the whole matrix:

    ambiguous / unavailable / malformed / unknown / runtime failure /
    identity mismatch  ->  NEVER a false success

W3 adversarially exercises the accepted W1 submit/status and W2
completion/result frontier at the failure boundary:

    host unavailable       -> fail closed, no fallback launcher
    launch failure         -> no handle, no record, no binding
    dispatch rejection     -> zero process starts
    unknown handle         -> fail closed, no nearest-execution fallback
    cross-task / corrupt binding -> rejected before any host call
    unknown runtime state  -> UNKNOWN, never completion
    drain synchronization  -> terminal publication only after stable output,
                              bounded, never wedged, never deadlocked
    reader failure         -> capture never re-stabilizes, fail closed
    worker failure         -> FAILED retained, distinguished from launch failure
    timeout / cancellation -> UNKNOWN / CANCELLED, never success
    malformed / unavailable result -> never ok=True, never COMPLETED

One bounded real production smoke (opt-in via AOTA_S3_REAL_HERMES_SMOKE=1)
proves the real Hermes path survives the W3 hardening.

Completion/result observation stops at the adapter projection seam; nothing
here asserts canonical TaskRecord reconciliation (S2/Core authority,
wzjcccc-dotcom/aota-hermes-tools#19).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    HermesAdapterError,
    HermesHostUnavailableError,
    hermes_output_to_canonical_result,
)
from aota_forge.adapters.hermes.host_client import (
    READER_DRAIN_TIMEOUT_SECONDS,
    HermesHostClient,
    HermesHostClientError,
)
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.runtime.config import SHARED_MCP_TOOLSET, RuntimeBinding, RuntimeConfig


def _operator_real_config() -> RuntimeConfig | None:
    """Explicit operator-style config fixture for bounded real smokes.

    Production source carries no launcher authority; the real-runtime probes
    discover the Hermes binary and pin provider/model as test fixtures.
    """
    import shutil

    found = shutil.which("hermes")
    if found is None:
        return None
    exe = Path(found)
    if exe.is_symlink() or not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor="hermes",
            profile="aota-worker" if role != "task-main" else "aota-task-main",
            provider="opencode-go",
            model="deepseek-v4-flash",
            concurrency=1,
            executable=str(exe),
            toolsets=(SHARED_MCP_TOOLSET,) if role != "task-main" else None,
        )
        for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
    )
    return RuntimeConfig(
        executor="hermes",
        executable=str(exe),
        concurrency=1,
        provider="opencode-go",
        model="deepseek-v4-flash",
        bindings=bindings,
    )
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage

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
    """Emits queued chunks, then blocks until the test opens the gate."""

    def __init__(self, chunks: list[bytes], gate: threading.Event) -> None:
        self._chunks = list(chunks)
        self.gate = gate

    def read(self, size: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        self.gate.wait(timeout=20)
        return b""


class ExplodingStream:
    """Returns partial output once, then fails like a broken pipe mid-read."""

    def __init__(self, partial: bytes = b"partial", fault: str = "SECRET_STREAM_TOKEN=broken") -> None:
        self._partial: bytes | None = partial
        self._fault = fault

    def read(self, size: int = -1) -> bytes:
        if self._partial is not None:
            chunk = self._partial
            self._partial = None
            return chunk
        raise OSError(self._fault)


class ClosedFileStream:
    """Simulates a stream object closed underneath the reader mid-capture."""

    def __init__(self, partial: bytes = b"first-half") -> None:
        self._partial: bytes | None = partial

    def read(self, size: int = -1) -> bytes:
        if self._partial is not None:
            chunk = self._partial
            self._partial = None
            return chunk
        raise ValueError("read of closed file")


class RecordingRunner:
    """popen_factory handing out one controlled process per call, or failing."""

    def __init__(self, factory: Any | None = None, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.processes: list[Any] = []
        self._factory = factory
        self._raises = raises

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls.append({"args": list(args), **kwargs})
        if self._raises is not None:
            raise self._raises
        process = self._factory() if self._factory is not None else ControlledProcess()
        self.processes.append(process)
        return process


class ScriptedHost:
    """Fake host protocol with unique handles, fixed envelopes, call counters."""

    def __init__(
        self,
        status: str = "running",
        result_envelope: Any = None,
        raises: Exception | None = None,
    ) -> None:
        self.status = status
        self.result_envelope = result_envelope if result_envelope is not None else {"status": "done", "exit_code": 0}
        self.raises = raises
        self._next = 1
        self.calls = {"dispatch": 0, "status": 0, "result": 0, "cancel": 0, "resume": 0}

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls["dispatch"] += 1
        handle = f"w3-scripted-handle-{self._next}"
        self._next += 1
        return {"adapter_handle": handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Any:
        self.calls["status"] += 1
        if self.raises is not None:
            raise self.raises
        return {"status": self.status}

    def fetch_result(self, adapter_handle: str) -> Any:
        self.calls["result"] += 1
        if self.raises is not None:
            raise self.raises
        return self.result_envelope

    def cancel_task(self, adapter_handle: str) -> Any:
        self.calls["cancel"] += 1
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls["resume"] += 1
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


def host_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "profile": "coder",
        "instruction": "Return exactly: AOTA_FORGE_S3_M2_W3_OK",
        "context": {"working_context": {}},
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "s3-m2-w3-package",
    }
    base.update(overrides)
    return base


def payload_with_cwd(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    return host_payload(context={"working_context": {"cwd": str(tmp_path)}}, **overrides)


def package(task_id: str, **kwargs: Any) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=kwargs.pop("canonical_task_id", task_id),
        project_id="aota_forge",
        canonical_role=kwargs.pop("canonical_role", "coder"),
        instruction=kwargs.pop("instruction", "Return exactly: AOTA_FORGE_S3_M2_W3_OK"),
        **kwargs,
    )


def concrete_client(tmp_path: Path, runner: RecordingRunner, **kwargs: Any) -> HermesHostClient:
    kwargs.setdefault("validate_launcher", False)
    kwargs.setdefault("timeout_seconds", 30)
    return HermesHostClient(str(tmp_path / "w3-unused-launcher"), popen_factory=runner, **kwargs)


def wait_for_status(client: HermesHostClient, handle: str, wanted: set[str], budget: float = 8.0) -> str:
    deadline = time.monotonic() + budget
    status = ""
    while time.monotonic() < deadline:
        status = client.query_status(handle)["status"]
        if status in wanted:
            return status
        time.sleep(0.005)
    return status


def wait_for_adapter_state(
    adapter: HermesAdapter, task_id: str, handle: str, wanted: set[CanonicalTaskState], budget: float = 10.0
) -> CanonicalTaskState:
    deadline = time.monotonic() + budget
    state = CanonicalTaskState.UNKNOWN
    while time.monotonic() < deadline:
        state = adapter.status(task_id, handle).state
        if state in wanted:
            return state
        time.sleep(0.01)
    return state


def _iter_keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_keys(item)


def assert_no_private_projection_leakage(obj: Any, *, markers: tuple[str, ...] = ()) -> None:
    keys = {key.lower() for key in _iter_keys(obj)}
    offenders = sorted(keys & {token.lower() for token in FORBIDDEN_PROJECTION_KEYS})
    assert offenders == [], f"private runtime keys escaped into projection: {offenders}"
    text = json.dumps(obj, default=str, sort_keys=True).lower()
    for marker in ("popen(", "threading.object", "subprocess.Popen"):
        assert marker not in text, f"private runtime object leaked into projection text: {marker!r}"
    for marker in markers:
        assert marker.lower() not in text, f"private marker escaped into projection: {marker!r}"


def assert_never_false_success(result: Any) -> None:
    """Master W3 invariant: no projection pairs success with anything but COMPLETED."""
    if result.ok is True:
        assert result.status == "completed"
        assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    else:
        assert result.status != "completed"
        assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value


def _make_launcher(tmp_path: Path, name: str, body: str) -> str:
    launcher = tmp_path / name
    launcher.write_text(f"#!/bin/sh\n{body}\n")
    launcher.chmod(0o755)
    return str(launcher)


def bound_pair(adapter: HermesAdapter, host: ScriptedHost, tmp_path: Path, task_id: str) -> str:
    result = adapter.dispatch(package(task_id, working_context={"cwd": str(tmp_path)}))
    return result.adapter_handle


# ---------------------------------------------------------------------------
# A. Host unavailable (§10, §36): fail closed, never fall back, mutate nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["missing", "not-executable", "directory", "rejected-symlink"])
def test_host_unavailable_matrix_rejects_exact_configured_launcher(tmp_path: Path, kind: str) -> None:
    if kind == "missing":
        path = str(tmp_path / "definitely-missing-launcher")
    elif kind == "not-executable":
        path = str(tmp_path / "not-executable")
        Path(path).write_text("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o644)
    elif kind == "directory":
        path = str(tmp_path)
    else:
        real = tmp_path / "real-launcher"
        real.write_text("#!/bin/sh\nexit 0\n")
        real.chmod(0o755)
        link = tmp_path / "symlinked-launcher"
        link.symlink_to(real)
        path = str(link)

    with pytest.raises(HermesHostUnavailableError) as excinfo:
        HermesHostClient(path)

    assert excinfo.value.code == "EXECUTOR_UNAVAILABLE"
    assert path in str(excinfo.value), "failure must name the exact configured launcher"
    assert "/usr/local/bin" not in str(excinfo.value)


def test_unavailable_launcher_launch_never_falls_back_to_any_other_binary(tmp_path: Path) -> None:
    """With launcher validation bypassed, the launch attempt itself must fail
    closed on the exact configured path: no PATH search, no wrapper fallback."""
    configured = str(tmp_path / "configured-but-missing" / "hermes")
    attempted: list[Any] = []

    def spy_popen(args: Any, **kwargs: Any) -> Any:
        attempted.append(list(args))
        raise FileNotFoundError(2, "No such file or directory", str(args[0]))

    client = HermesHostClient(
        configured,
        default_cwd=str(tmp_path),
        popen_factory=spy_popen,
        validate_launcher=False,
    )
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        client.dispatch(host_payload())
    assert attempted == [[configured, "-p", "coder", "-z", "Return exactly: AOTA_FORGE_S3_M2_W3_OK"]]
    assert attempted[0][0] == configured, "no fallback launcher may be attempted"
    assert client._records == {}  # noqa: SLF001


def test_host_unavailable_probe_is_non_destructive_to_production_launcher(tmp_path: Path) -> None:
    def snapshot(path: str) -> Any:
        stat = os.stat(path)
        return (stat.st_mode, stat.st_mtime_ns, stat.st_size, hashlib.sha256(Path(path).read_bytes()).hexdigest())

    launcher = _operator_real_config()
    if launcher is None:
        pytest.skip("no trusted Hermes executable available (RUNTIME_ENVIRONMENT)")
    before = snapshot(launcher.executable)
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient(str(tmp_path / "pretend" / "hermes-host"))
    HermesHostClient(str(tmp_path / "x"), popen_factory=RecordingRunner(), validate_launcher=False).close()
    assert snapshot(launcher.executable) == before, "production launcher must remain untouched"


# ---------------------------------------------------------------------------
# B. Launch failure (§11, §31) and dispatch rejection (§12): zero ghost state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        OSError(5, "I/O error"),
        FileNotFoundError(2, "No such file or directory"),
        PermissionError(13, "Permission denied"),
        ValueError("embedded null byte"),
    ],
)
def test_launch_failure_matrix_creates_no_handle_record_or_binding(tmp_path: Path, failure: Exception) -> None:
    runner = RecordingRunner(raises=failure)
    client = concrete_client(tmp_path, runner)
    adapter = HermesAdapter(host_client=client)

    with pytest.raises(HermesHostUnavailableError) as excinfo:
        adapter.dispatch(package("ghost-launch", working_context={"cwd": str(tmp_path)}))

    assert excinfo.value.code == "EXECUTOR_UNAVAILABLE"
    assert client._records == {}  # noqa: SLF001
    assert adapter._task_handles == {} and adapter._handle_tasks == {}  # noqa: SLF001
    assert adapter._dispatch_replays == {}  # noqa: SLF001
    assert len(runner.calls) == 1, "exactly one launch attempt, failed, no ghost state"

    with pytest.raises(HermesAdapterError) as lookup:
        adapter.status("ghost-launch", "any-handle")
    assert lookup.value.code == "TASK_HANDLE_NOT_FOUND"

    # no ghost residue: a later valid dispatch through a fresh client still works
    ok_client = concrete_client(tmp_path, RecordingRunner(lambda: ControlledProcess(exit_code=0)))
    handle = ok_client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    assert handle.startswith("hermes-host-")
    ok_client.close()


def test_launch_failure_message_never_carries_exception_payload_credentials(tmp_path: Path) -> None:
    secret = "AOTAFAKECRED-9e7d1c0f"
    runner = RecordingRunner(raises=OSError(f"exec failed env dump: TOKEN={secret} HOME=/home/latios"))
    client = concrete_client(tmp_path, runner)
    adapter = HermesAdapter(host_client=client)

    with pytest.raises(HermesHostUnavailableError) as excinfo:
        adapter.dispatch(package("cred-launch", working_context={"cwd": str(tmp_path)}))

    text = str(excinfo.value)
    assert secret not in text, "launch failure must surface only bounded error text"
    assert "HOME=" not in text
    assert "OSError" in text, "only the exception class name may be surfaced"


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda: {"artifacts": [{"path": "not-forwarded"}]}, id="unsupported-artifacts"),
        pytest.param(
            lambda: {"capability_requirements": {"execution_mode": "sync"}, "constraints": {"execution_mode": "async"}},
            id="conflicting-execution-mode",
        ),
        pytest.param(lambda: {"context": {"working_context": {"cwd": "/never/resolved/w3"}}}, id="invalid-cwd"),
        pytest.param(lambda: {"operation": "task_resume_via_dispatch"}, id="unsupported-operation"),
        pytest.param(lambda: {"capability_requirements": {"isolation": "firewalled-vm"}}, id="unsupported-capability"),
    ],
)
def test_dispatch_rejections_start_zero_processes(tmp_path: Path, mutation: Any) -> None:
    runner = RecordingRunner()
    client = HermesHostClient(
        str(tmp_path / "w3-unused"), default_cwd=str(tmp_path), popen_factory=runner, validate_launcher=False
    )

    payload = payload_with_cwd(tmp_path)
    payload.update(mutation())
    with pytest.raises(HermesHostClientError) as excinfo:
        client.dispatch(payload)

    assert excinfo.value.code in {"CAPABILITY_MISMATCH", "PACKAGE_INVALID", "DISPATCH_REJECTED"}
    assert runner.calls == [], "a rejected dispatch may never start a process"
    assert client._records == {}  # noqa: SLF001


def test_rejected_dispatch_never_creates_adapter_binding(tmp_path: Path) -> None:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)

    with pytest.raises(HermesAdapterError):
        adapter.dispatch(
            package("reject", working_context={"cwd": str(tmp_path)}, capability_requirements={"execution_mode": "quantum"})
        )

    assert host.calls["dispatch"] == 0
    assert adapter._task_handles == {} and adapter._handle_tasks == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# C. Unknown adapter handles (§13): no lookup fallback, no nearest execution
# ---------------------------------------------------------------------------


def test_host_level_unknown_handle_status_result_cancel_fail_closed(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0)
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    live = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    records_before = dict.fromkeys(client._records)  # noqa: SLF001

    unknown = "hermes-host-" + "f" * 32
    for response in (client.query_status(unknown), client.fetch_result(unknown)):
        assert response["status"] == "unreachable"
        assert response["error"]["code"] == "TASK_NOT_FOUND"
    cancelled = client.cancel_task(unknown)
    assert cancelled["cancelled"] is False
    assert cancelled["status"] == "unreachable"
    assert cancelled["error"]["code"] == "TASK_NOT_FOUND"
    assert process.poll() is None, "an unknown handle may not resolve to (or disturb) the live execution"
    assert dict.fromkeys(client._records) == records_before, "unknown probes must not create or swap records"  # noqa: SLF001
    assert client.query_status(live)["status"] == "running"
    client.close()


def test_resume_on_live_or_unknown_handle_stays_unsupported(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0)
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]

    for probe in (handle, "hermes-host-" + "0" * 32):
        response = client.resume_task(probe, {})
        assert response["error"]["code"] == "RESUME_UNSUPPORTED"
    assert client.query_status(handle)["status"] == "running", "a resume probe must not disturb live state"
    client.close()


def test_adapter_level_unknown_handle_never_reaches_host(tmp_path: Path) -> None:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)
    handle = bound_pair(adapter, host, tmp_path, "known-task")

    cases = (
        ("status", ("never-dispatched", "never-issued")),
        ("result", ("never-dispatched", "never-issued")),
        ("cancel", ("never-dispatched", "never-issued")),
        ("resume", ("never-dispatched", "never-issued", package("never-dispatched", operation="task_resume"))),
    )
    for operation, args in cases:
        with pytest.raises(HermesAdapterError) as excinfo:
            getattr(adapter, operation)(*args)
        assert excinfo.value.code == "TASK_HANDLE_NOT_FOUND"
    for operation in ("status", "result", "cancel", "resume"):
        assert host.calls[operation] == 0, f"rejected {operation} identity probe touched the host"
    assert adapter._task_handles["known-task"] == handle  # noqa: SLF001


# ---------------------------------------------------------------------------
# D. Cross-task handle mismatch (§14) — canonical_task_A + adapter_handle_B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["status", "result", "cancel", "resume"])
def test_cross_task_access_fails_closed_before_any_host_call(tmp_path: Path, operation: str) -> None:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)
    bound_pair(adapter, host, tmp_path, "task-b")
    handle_b = adapter._task_handles["task-b"]  # noqa: SLF001
    handle_a = bound_pair(adapter, host, tmp_path, "task-a")

    args: tuple[Any, ...]
    if operation == "resume":
        args = ("task-a", handle_b, package("task-a", operation="task_resume"))
    else:
        args = ("task-a", handle_b)
    assert handle_a != handle_b
    with pytest.raises(HermesAdapterError) as excinfo:
        getattr(adapter, operation)(*args)
    assert excinfo.value.code == "TASK_ID_MISMATCH"
    assert host.calls[operation] == 0


def test_terminal_execution_keeps_identity_binding_strict(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0)
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    adapter = HermesAdapter(host_client=client)
    result_a = adapter.dispatch(package("term-a", working_context={"cwd": str(tmp_path)}))
    process.release(0)
    assert wait_for_status(client, result_a.adapter_handle, {"done"}) == "done"

    terminal_before = client._records[result_a.adapter_handle].terminal_at  # noqa: SLF001
    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.status("term-a", "hermes-host-" + "a" * 32)
    assert excinfo.value.code == "TASK_ID_MISMATCH", "terminal executions keep bidirectional guards"
    assert client._records[result_a.adapter_handle].terminal_at == terminal_before  # noqa: SLF001
    still = adapter.result("term-a", result_a.adapter_handle)
    assert_never_false_success(still)
    assert still.ok is True, "the known pair keeps working after a rejected cross probe"


# ---------------------------------------------------------------------------
# E. Inconsistent binding state (§15): no repair-by-guessing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("corruption", ["forward-only", "reverse-only", "cross-mapped"])
def test_inconsistent_binding_fails_closed_without_repair(tmp_path: Path, corruption: str) -> None:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)
    task_id = "victim"
    handle = bound_pair(adapter, host, tmp_path, task_id)
    if corruption == "forward-only":
        del adapter._task_handles[task_id]  # noqa: SLF001
    elif corruption == "reverse-only":
        del adapter._handle_tasks[handle]  # noqa: SLF001
    else:
        adapter._task_handles[task_id] = "phantom-handle"  # noqa: SLF001

    forward_before = dict(adapter._handle_tasks)  # noqa: SLF001
    reverse_before = dict(adapter._task_handles)  # noqa: SLF001

    with pytest.raises(HermesAdapterError):
        adapter.status(task_id, handle)
    with pytest.raises(HermesAdapterError):
        adapter.result(task_id, handle)

    assert host.calls["status"] == 0 and host.calls["result"] == 0
    assert adapter._handle_tasks == forward_before  # noqa: SLF001
    assert adapter._task_handles == reverse_before  # noqa: SLF001


# ---------------------------------------------------------------------------
# F. Unknown runtime status / malformed envelopes (§16, §27)
# ---------------------------------------------------------------------------


UNKNOWN_STATUS_VALUES: list[Any] = ["mystery", "unknown_new_state", "", "   ", None, 123, True, [], {}]


@pytest.mark.parametrize("raw", UNKNOWN_STATUS_VALUES)
def test_unknown_runtime_status_never_false_completion_status(tmp_path: Path, raw: Any) -> None:
    host = ScriptedHost(status=raw)
    adapter = HermesAdapter(host_client=host)
    task_id = "unknown-status-case"
    handle = bound_pair(adapter, host, tmp_path, task_id)

    first = adapter.status(task_id, handle)
    second = adapter.status(task_id, handle)

    assert first.state is CanonicalTaskState.UNKNOWN
    assert first.state is not CanonicalTaskState.COMPLETED
    assert second.state is first.state, "unknown projection must be deterministic"


@pytest.mark.parametrize("raw", UNKNOWN_STATUS_VALUES)
def test_unknown_runtime_status_never_false_completion_result(raw: Any) -> None:
    envelope: dict[str, Any] = {"status": raw, "exit_code": 0, "stdout": "ALL DONE SUCCESS"}
    result = hermes_output_to_canonical_result(envelope, canonical_task_id="w3-unknown-result")

    assert_never_false_success(result)
    assert result.ok is False
    assert result.result_data == {}


@pytest.mark.parametrize("envelope", ["nope", 42, ["status", "done"], None, {}, {"status": {"nested": 1}}])
def test_non_mapping_or_empty_status_envelopes_fail_closed(tmp_path: Path, envelope: Any) -> None:
    host = ScriptedHost(status="ignored", result_envelope=envelope)
    adapter = HermesAdapter(host_client=host)
    task_id = "envelope-malformed"
    handle = bound_pair(adapter, host, tmp_path, task_id)

    status = adapter.status(task_id, handle)
    result = adapter.result(task_id, handle)

    assert status.state is CanonicalTaskState.UNKNOWN
    assert_never_false_success(result)


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({"status": "done", "exit_code": 0, "result_data": "text"}, id="result-data-scalar"),
        pytest.param({"status": "done", "exit_code": "0"}, id="exit-code-string"),
        pytest.param({"status": "done", "exit_code": None}, id="exit-code-null"),
        pytest.param({"status": "done", "exit_code": 1.5}, id="exit-code-float"),
        pytest.param({"status": "done", "output_artifacts": "artifact.txt"}, id="artifacts-string"),
        pytest.param({"status": "done", "artifacts": [42]}, id="artifacts-items-scalar"),
        pytest.param({"status": "success", "exit_code": True}, id="exit-code-bool"),
        pytest.param({"status": "done", "result_data": None}, id="result-data-null"),
    ],
)
def test_malformed_result_envelopes_can_never_reach_completed(output: Mapping[str, Any]) -> None:
    result = hermes_output_to_canonical_result(output, canonical_task_id="w3-malformed")

    assert_never_false_success(result)
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None
    assert result.error["code"] == "RESULT_MALFORMED"


def test_truncated_structured_stdout_is_evidence_not_parsed_payload(tmp_path: Path) -> None:
    """A worker that emits truncated JSON but exits 0 yields the accepted W2
    contract: bounded stdout text is evidence only and is never parsed into a
    fabricated structured result."""
    launcher = _make_launcher(tmp_path, "trunc.sh", 'printf \'{"answer": "unfinishe\' ; exit 0')
    client = HermesHostClient(launcher, default_cwd=str(tmp_path))
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("truncated-json", working_context={"cwd": str(tmp_path)}))

    assert wait_for_status(client, dispatch_result.adapter_handle, {"done", "failed"}) == "done"
    result = adapter.result("truncated-json", dispatch_result.adapter_handle)

    assert result.ok is True  # structurally valid terminal envelope per accepted W2 contract
    assert result.result_data == {}, "stdout text must never be parsed into a payload"
    assert result.stdout_summary is not None and '{"answer": "unfinishe' in result.stdout_summary
    client.close()


# ---------------------------------------------------------------------------
# G. W2 drain synchronization, adversarially (§17, §18, §33)
# ---------------------------------------------------------------------------


def test_stderr_gated_completion_publishes_failed_only_after_drain(tmp_path: Path) -> None:
    """TERMINAL_PUBLICATION_WAITS_FOR_STABLE_OUTPUT also applies to FAILED."""
    gate = threading.Event()
    stderr = GatedStream([b"tail-"], gate)
    process = ControlledProcess(exit_code=9, stdout=io.BytesIO(b"out"), stderr=stderr)
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(9)

    assert process.poll() == 9
    seen = {client.query_status(handle)["status"] for _ in range(3)}
    assert seen == {"running"}, "a failed exit with an undrained stderr must not publish terminal yet"

    gate.set()
    assert wait_for_status(client, handle, {"failed"}) == "failed"
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "failed" and envelope["exit_code"] == 9
    assert envelope["stderr"] == "tail-"


def test_both_streams_delayed_publish_only_after_both_stabilize(tmp_path: Path) -> None:
    out_gate, err_gate = threading.Event(), threading.Event()
    process = ControlledProcess(
        exit_code=0,
        stdout=GatedStream([b"so-"], out_gate),
        stderr=GatedStream([b"se-"], err_gate),
    )
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(0)
    try:
        assert client.query_status(handle)["status"] == "running"
        out_gate.set()
        assert client.query_status(handle)["status"] == "running", "stdout alone must not release publication"
        err_gate.set()
        assert wait_for_status(client, handle, {"done"}) == "done"
        result = client.fetch_result(handle)
        assert result["stdout"] == "so-" and result["stderr"] == "se-"
    finally:
        out_gate.set()
        err_gate.set()


def test_reader_join_timeout_is_bounded_and_delayed_retry_terminates(tmp_path: Path) -> None:
    gate = threading.Event()
    process = ControlledProcess(exit_code=0, stdout=GatedStream([b"held"], gate), stderr=io.BytesIO(b""))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(0)

    started = time.monotonic()
    first = client.query_status(handle)["status"]
    elapsed = time.monotonic() - started
    assert first == "running"
    assert elapsed < READER_DRAIN_TIMEOUT_SECONDS + 2.0, (
        f"a blocked reader may bound the wait by at most the drain budget; took {elapsed:.2f}s"
    )

    def release_soon() -> None:
        time.sleep(0.15)
        gate.set()

    threading.Thread(target=release_soon, daemon=True).start()
    assert wait_for_status(client, handle, {"done"}, budget=8.0) == "done", (
        "DELAYED_DRAIN_EVENTUALLY_TERMINATES: no permanent RUNNING once streams drain"
    )
    assert client.fetch_result(handle)["stdout"] == "held"


def test_never_draining_stream_cannot_wedge_running_past_deadline(tmp_path: Path) -> None:
    """Even a stream that never stabilizes cannot produce permanent RUNNING:
    the bounded local deadline forces a terminal (timeout -> UNKNOWN)."""
    gate = threading.Event()
    process = ControlledProcess(exit_code=0, stdout=GatedStream([b"stuck-after-this"], gate), stderr=io.BytesIO(b""))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path, constraints={"timeout_seconds": 0.5}))["adapter_handle"]
    process.release(0)
    try:
        status = wait_for_status(client, handle, {"timeout"}, budget=8.0)
        assert status == "timeout", "OUTPUT_DRAIN_DEADLOCK: deadline must publish terminal anyway"
        envelope = client.fetch_result(handle)
        assert envelope["status"] == "timeout"
        assert envelope["error"]["code"] == "EXECUTION_TIMEOUT"
    finally:
        gate.set()


def test_late_arriving_output_after_exit_is_captured_in_full(tmp_path: Path) -> None:
    gate = threading.Event()

    class LateStream:
        def __init__(self) -> None:
            self._delivered = False

        def read(self, size: int = -1) -> bytes:
            if self._delivered:
                return b""
            gate.wait(timeout=20)
            self._delivered = True
            return b"complete-after-gate\n"

    process = ControlledProcess(exit_code=0, stdout=LateStream(), stderr=io.BytesIO(b""))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(0)

    assert client.query_status(handle)["status"] == "running"
    gate.set()
    assert wait_for_status(client, handle, {"done"}) == "done"
    assert client.fetch_result(handle)["stdout"] == "complete-after-gate\n"


# ---------------------------------------------------------------------------
# H. Reader abnormality (§19): a broken capture can never project success
# ---------------------------------------------------------------------------


def test_reader_exception_after_partial_output_fails_closed_exit_zero(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0, stdout=ExplodingStream(partial=b"half-a-result"))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("reader-crash", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle
    process.release(0)

    assert wait_for_status(client, handle, {"failed"}) == "failed", (
        "READER_FAILURE_FALSE_SUCCESS: a crashed reader must never publish done"
    )
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "failed"
    assert envelope["error"]["code"] == "RESULT_UNAVAILABLE"
    assert "SECRET_STREAM_TOKEN" not in str(envelope), "raw reader exception text must stay private"
    assert envelope["stdout"] == "half-a-result", "partial capture may be shown, never promoted"

    result = adapter.result("reader-crash", handle)
    assert_never_false_success(result)
    assert result.status == "failed"
    assert result.error is not None and result.error["code"] == "RESULT_UNAVAILABLE"
    assert result.result_data == {}


def test_reader_closed_file_failure_fails_closed(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0, stdout=ClosedFileStream())
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(0)

    assert wait_for_status(client, handle, {"failed"}) == "failed"
    assert client.fetch_result(handle)["error"]["code"] == "RESULT_UNAVAILABLE"


def test_stderr_reader_failure_fails_closed(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0, stdout=io.BytesIO(b"fine"), stderr=ExplodingStream())
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(0)

    assert wait_for_status(client, handle, {"failed"}) == "failed"
    assert client.fetch_result(handle)["error"]["code"] == "RESULT_UNAVAILABLE"


def test_nonzero_exit_with_reader_failure_stays_execution_failed(tmp_path: Path) -> None:
    """A known worker failure remains the dominant honest evidence even when
    the reader also died; the actual exit code is preserved."""
    process = ControlledProcess(exit_code=3, stdout=ExplodingStream(), stderr=io.BytesIO(b"boom"))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    process.release(3)

    assert wait_for_status(client, handle, {"failed"}) == "failed"
    envelope = client.fetch_result(handle)
    assert envelope["exit_code"] == 3
    assert envelope["error"]["code"] == "EXECUTION_FAILED"


def test_clean_process_with_good_readers_still_completes(tmp_path: Path) -> None:
    """The fail-closed downgrade must not poison the happy path."""
    launcher = _make_launcher(tmp_path, "clean.sh", "printf 'ALL-FINE\\n'; exit 0")
    client = HermesHostClient(launcher, default_cwd=str(tmp_path))
    handle = client.dispatch(host_payload())["adapter_handle"]
    assert wait_for_status(client, handle, {"done"}) == "done"
    envelope = client.fetch_result(handle)
    assert envelope["status"] == "done" and envelope["exit_code"] == 0 and envelope["stdout"] == "ALL-FINE\n"
    client.close()


# ---------------------------------------------------------------------------
# I. Worker / process failure via real subprocesses (§20, §21, §22, §30, §31)
# ---------------------------------------------------------------------------


def test_fast_failing_real_worker_keeps_handle_queryable_failed(tmp_path: Path) -> None:
    launcher = _make_launcher(tmp_path, "fail-fast.sh", "printf 'worker died\\n' >&2; exit 4")
    client = HermesHostClient(launcher, default_cwd=str(tmp_path))
    adapter = HermesAdapter(host_client=client)

    dispatch_result = adapter.dispatch(package("fast-fail", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle
    assert handle.startswith("hermes-host-")

    first = client.query_status(handle)
    assert first["status"] != "unreachable", "a launched-but-fast-failing worker is NOT a launch failure"
    assert wait_for_status(client, handle, {"failed"}, budget=10) == "failed"

    envelope = client.fetch_result(handle)
    assert envelope["exit_code"] == 4
    assert envelope["error"]["code"] == "EXECUTION_FAILED"
    assert "worker died" in envelope["stderr"]

    result = adapter.result("fast-fail", handle)
    assert_never_false_success(result)
    assert result.status == "failed" and result.exit_code == 4
    client.close()


def test_real_failure_with_garbage_output_can_never_override_into_success(tmp_path: Path) -> None:
    launcher = _make_launcher(tmp_path, "fail-garbage.sh", "printf '{\"ok\": tr' ; printf 'oops' >&2 ; exit 9")
    client = HermesHostClient(launcher, default_cwd=str(tmp_path))
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("fail-garbage", working_context={"cwd": str(tmp_path)}))

    assert wait_for_status(client, dispatch_result.adapter_handle, {"failed"}, budget=10) == "failed"
    result = adapter.result("fail-garbage", dispatch_result.adapter_handle)
    assert_never_false_success(result)
    assert result.exit_code == 9
    assert result.result_data == {}, "garbage stdout is never promoted into a payload"
    assert result.error is not None and result.error["code"] == "EXECUTION_FAILED"
    client.close()


def test_exit_zero_without_stable_result_fails_closed_end_to_end(tmp_path: Path) -> None:
    """§22 regression through the real pipeline shape: exit code 0 is runtime
    evidence only; without a stable valid capture the projection fails closed.
    (Structural envelope variants are frozen in F/W2.)"""
    process = ControlledProcess(exit_code=0, stdout=ExplodingStream(partial=b'{"answer":'))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("exit0-noresult", working_context={"cwd": str(tmp_path)}))
    process.release(0)

    state = wait_for_adapter_state(
        adapter,
        "exit0-noresult",
        dispatch_result.adapter_handle,
        {CanonicalTaskState.FAILED},
    )
    assert state is CanonicalTaskState.FAILED
    result = adapter.result("exit0-noresult", dispatch_result.adapter_handle)
    assert_never_false_success(result)
    assert result.ok is False


# ---------------------------------------------------------------------------
# J. Timeout boundary (§23, §24)
# ---------------------------------------------------------------------------


TIMEOUT_PROFILES = {
    "no-output": "exec sleep 30",
    "partial-stdout": "printf 'half-a-line\\n' ; exec sleep 30",
    "partial-stderr": "printf 'half-an-error\\n' >&2 ; exec sleep 30",
}


@pytest.mark.parametrize(("profile", "body"), TIMEOUT_PROFILES.items())
def test_real_timeout_never_false_completion(tmp_path: Path, profile: str, body: str) -> None:
    launcher = _make_launcher(tmp_path, f"sleeper-{profile}.sh", body)
    client = HermesHostClient(launcher, default_cwd=str(tmp_path), timeout_seconds=30)
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(
        package(f"timeout-{profile}", working_context={"cwd": str(tmp_path)}, constraints={"timeout_seconds": 0.4})
    )
    handle = dispatch_result.adapter_handle

    state = wait_for_adapter_state(adapter, dispatch_result.canonical_task_id, handle, {CanonicalTaskState.UNKNOWN})
    assert state is CanonicalTaskState.UNKNOWN, "timeout must project UNKNOWN, never completion"
    assert client.query_status(handle)["status"] == "timeout"

    result = adapter.result(dispatch_result.canonical_task_id, handle)
    assert_never_false_success(result)
    assert result.error is not None and result.error["code"] == "EXECUTION_TIMEOUT"
    assert result.result_data == {}, "retained partial output never fabricates a payload"

    process = client._records[handle].process  # noqa: SLF001
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.05)
    assert process.poll() is not None, "timed-out worker must be terminated, no lingering process"
    client.close()


def test_timeout_while_reader_drains_is_bounded_and_never_success(tmp_path: Path) -> None:
    gate = threading.Event()
    process = ControlledProcess(exit_code=0, stdout=GatedStream([b"draining..."], gate), stderr=io.BytesIO(b""))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    handle = client.dispatch(payload_with_cwd(tmp_path, constraints={"timeout_seconds": 0.4}))["adapter_handle"]

    started = time.monotonic()
    status = ""
    while time.monotonic() - started < 10:
        status = client.query_status(handle)["status"]
        if status == "timeout":
            break
    elapsed = time.monotonic() - started
    try:
        assert status == "timeout", "TIMEOUT_DRAIN_INTERACTION_DEADLOCK"
        assert elapsed < 8.0, "timeout during drain must remain bounded"
        assert client.query_status(handle)["status"] == "timeout", "terminal stays stable"
        envelope = client.fetch_result(handle)
        assert envelope["status"] == "timeout"
        assert envelope["error"]["code"] == "EXECUTION_TIMEOUT"
    finally:
        gate.set()


# ---------------------------------------------------------------------------
# K. Cancellation during drain regression (§25)
# ---------------------------------------------------------------------------


def test_cancel_while_reader_delayed_no_deadlock_no_false_success(tmp_path: Path) -> None:
    gate = threading.Event()
    process = ControlledProcess(exit_code=0, stdout=GatedStream([b"in-flight"], gate), stderr=io.BytesIO(b""))
    client = concrete_client(tmp_path, RecordingRunner(lambda: process))
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("cancel-drain", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle
    assert client.query_status(handle)["status"] == "running"

    started = time.monotonic()
    cancel = adapter.cancel("cancel-drain", handle)
    elapsed = time.monotonic() - started
    try:
        assert cancel.cancelled is True
        assert cancel.state is CanonicalTaskState.CANCELLED
        assert elapsed < 8.0, "CANCEL_DRAIN_INTERACTION_DEADLOCK: cancellation stayed bounded"

        result = adapter.result("cancel-drain", handle)
        assert_never_false_success(result)
        assert result.status == "cancelled"
        assert result.error is not None and result.error["code"] == "EXECUTION_CANCELLED"
        assert client.query_status(handle)["status"] == "cancelled", "terminal stays cancelled after drain settles"
    finally:
        gate.set()


# ---------------------------------------------------------------------------
# L. Resume unsupported truth (§26) — freeze only, no implementation change
# ---------------------------------------------------------------------------


def test_production_resume_is_not_advertised_and_guarded_before_host(tmp_path: Path) -> None:
    runner = RecordingRunner()
    host_client = HermesHostClient(str(tmp_path / "w3-unused"), popen_factory=runner, validate_launcher=False)
    dispatcher = create_production_execution_dispatcher(host_client=host_client)
    adapter = dispatcher.registry.get("hermes")

    assert adapter.capabilities().supports_task_resume is False, "REAL_RESUME_ADVERTISED=no"

    dispatch_result = adapter.dispatch(package("no-resume", working_context={"cwd": str(tmp_path)}))
    launches_before = len(runner.calls)
    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.resume(
            "no-resume",
            dispatch_result.adapter_handle,
            package("no-resume", operation="task_resume"),
        )
    assert excinfo.value.code == "RESUME_UNSUPPORTED"
    assert len(runner.calls) == launches_before, "a rejected resume may never launch or continue a worker"
    host_client.close()


# ---------------------------------------------------------------------------
# M. Unavailable result matrix (§28) and status/result consistency (§29)
# ---------------------------------------------------------------------------


def test_unavailable_result_paths_never_fabricate_success(tmp_path: Path) -> None:
    host = ScriptedHost()
    adapter = HermesAdapter(host_client=host)
    task_id = "unavailable"
    handle = bound_pair(adapter, host, tmp_path, task_id)

    cases: dict[str, Any] = {
        "running": {"status": "running"},
        "unknown-state": {"status": "mystery"},
        "terminal-without-valid-result": {"status": "done", "exit_code": "broken"},
        "timeout": {"status": "timeout", "error": {"code": "EXECUTION_TIMEOUT", "message": "x"}},
        "cancelled": {"status": "cancelled", "error": {"code": "EXECUTION_CANCELLED", "message": "x"}},
        "unreachable": {"status": "unreachable", "error": {"code": "TASK_NOT_FOUND", "message": "x"}},
    }
    for label, envelope in cases.items():
        host.result_envelope = envelope
        result = adapter.result(task_id, handle)
        assert_never_false_success(result), f"unavailable result path: {label}"
        assert result.result_data == {}, f"{label} must not fabricate a payload"


def test_status_and_result_agree_on_every_real_lifecycle_state(tmp_path: Path) -> None:
    """No adapter-visible (status, result) pair may contradict: ok=True iff
    COMPLETED; every other lifecycle outcome fails closed."""
    done_proc = ControlledProcess(exit_code=0, stdout=io.BytesIO(b"ok"))
    failed_proc = ControlledProcess(exit_code=8, stderr=io.BytesIO(b"bad"))
    cancelled_proc = ControlledProcess()
    timeout_proc = ControlledProcess()
    readerfail_proc = ControlledProcess(exit_code=0, stdout=ExplodingStream())

    processes = iter([done_proc, failed_proc, cancelled_proc, timeout_proc, readerfail_proc])
    client = concrete_client(tmp_path, RecordingRunner(lambda: next(processes)))
    adapter = HermesAdapter(host_client=client)

    handles = {}
    for task in ("done", "failed", "cancelled", "timeout", "readerfail"):
        constraints = {"timeout_seconds": 0.5} if task == "timeout" else {}
        handles[task] = adapter.dispatch(
            package(task, working_context={"cwd": str(tmp_path)}, constraints=constraints)
        ).adapter_handle

    done_proc.release(0)
    failed_proc.release(8)
    readerfail_proc.release(0)
    adapter.cancel("cancelled", handles["cancelled"])

    assert wait_for_status(client, handles["done"], {"done"}) == "done"
    assert wait_for_status(client, handles["failed"], {"failed"}) == "failed"
    assert wait_for_status(client, handles["cancelled"], {"cancelled"}) == "cancelled"
    assert wait_for_status(client, handles["readerfail"], {"failed"}) == "failed"
    assert wait_for_status(client, handles["timeout"], {"timeout"}) == "timeout"

    for task in ("failed", "cancelled", "timeout", "readerfail"):
        state = adapter.status(task, handles[task]).state
        result = adapter.result(task, handles[task])
        assert_never_false_success(result)
        assert result.ok is False, f"{task} must never project success"
        assert state is not CanonicalTaskState.COMPLETED
        if task == "timeout":
            # accepted contract: timeout maps status to UNKNOWN and the result
            # envelope to a fail-closed timeout projection, never completion
            assert state is CanonicalTaskState.UNKNOWN
            assert result.error is not None and result.error["code"] == "EXECUTION_TIMEOUT"
        else:
            assert result.canonical_task_state == state.value, f"{task} status/result contradiction"

    done_result = adapter.result("done", handles["done"])
    assert done_result.ok is True and done_result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert adapter.status("done", handles["done"]).state is CanonicalTaskState.COMPLETED


# ---------------------------------------------------------------------------
# N. Record retention under failure pressure (§32)
# ---------------------------------------------------------------------------


def test_active_records_survive_terminal_eviction_pressure(tmp_path: Path) -> None:
    active = ControlledProcess()
    doomed = [ControlledProcess(exit_code=0) for _ in range(4)]
    queue = iter([active] + doomed)
    client = HermesHostClient(
        str(tmp_path / "w3-unused"),
        default_cwd=str(tmp_path),
        popen_factory=lambda *a, **k: next(queue),
        validate_launcher=False,
        max_records=3,
        timeout_seconds=30,
    )
    active_handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    for _ in range(4):
        handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
        client._records[handle].process.release(0)  # noqa: SLF001
        assert wait_for_status(client, handle, {"done"}) == "done"

    assert active_handle in client._records, "ACTIVE_RECORD_NOT_EVICTED"  # noqa: SLF001
    assert client.query_status(active_handle)["status"] == "running", "UNKNOWN_FROM_PREMATURE_ACTIVE_EVICTION=no"
    assert len(client._records) <= 3  # noqa: SLF001
    client.close()


def test_record_bound_exhaustion_fails_before_process_start(tmp_path: Path) -> None:
    runner = RecordingRunner()
    client = HermesHostClient(
        str(tmp_path / "w3-unused"),
        default_cwd=str(tmp_path),
        popen_factory=runner,
        validate_launcher=False,
        max_records=2,
        timeout_seconds=30,
    )
    client.dispatch(payload_with_cwd(tmp_path))
    client.dispatch(payload_with_cwd(tmp_path))
    with pytest.raises(Exception, match="EXECUTOR_UNAVAILABLE"):
        client.dispatch(payload_with_cwd(tmp_path))
    assert len(runner.calls) == 2, "exhausted record bound may never start a third process"
    client.close()


# ---------------------------------------------------------------------------
# O. Bounded resources + private metadata on failure paths (§33, §34)
# ---------------------------------------------------------------------------


def test_failure_paths_do_not_grow_threads(tmp_path: Path) -> None:
    gates: list[threading.Event] = []
    processes: list[ControlledProcess] = []

    def spawn() -> ControlledProcess:
        gate = threading.Event()
        gates.append(gate)
        process = ControlledProcess(exit_code=0, stdout=GatedStream([b"x" * 10], gate))
        processes.append(process)
        return process

    client = HermesHostClient(
        str(tmp_path / "w3-unused"),
        default_cwd=str(tmp_path),
        popen_factory=RecordingRunner(spawn),
        validate_launcher=False,
        output_limit_bytes=64,
        timeout_seconds=30,
    )
    before_threads = {threading.current_thread().ident} | {
        t.ident for t in threading.enumerate() if t is not threading.current_thread()
    }
    try:
        handles = [client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"] for _ in range(4)]
        assert len(client._records) == 4  # noqa: SLF001
        for record in list(client._records.values()):  # noqa: SLF001
            assert len(record.readers) == 2, "exactly two bounded reader threads per execution"
        spawned = [t for t in threading.enumerate() if t.ident not in before_threads]
        assert len(spawned) <= 4 * 3, "at most bounded per-record reader/watcher threads"
        count_after_spawn = len([t for t in threading.enumerate() if t.ident not in before_threads])
        for _ in range(6):
            for handle in handles:
                client.query_status(handle)
        assert len([t for t in threading.enumerate() if t.ident not in before_threads]) <= count_after_spawn, (
            "status polling must never spawn more threads"
        )
    finally:
        for gate, process in zip(gates, processes):
            process.release(0)
            gate.set()
        client.close()

    deadline = time.monotonic() + 10
    for thread in spawned:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [thread.name for thread in spawned if thread.is_alive()]
    assert alive == [], f"LINGERING reader/watcher threads after close: {alive}"


def test_bounded_output_survives_failure_projection(tmp_path: Path) -> None:
    launcher = _make_launcher(tmp_path, "spew-fail.sh", "yes AOTA_FORGE_S3_M2_W3 | head -c 100000 ; exit 2")
    client = HermesHostClient(launcher, default_cwd=str(tmp_path), output_limit_bytes=256)
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("spew-fail", working_context={"cwd": str(tmp_path)}))
    handle = dispatch_result.adapter_handle

    assert wait_for_status(client, handle, {"failed"}, budget=15) == "failed"
    envelope = client.fetch_result(handle)
    assert len(envelope["stdout"].encode()) <= 256
    assert envelope["stdout"].endswith("...[output truncated]")
    result = adapter.result("spew-fail", handle)
    assert_never_false_success(result)
    client.close()


def test_failure_envelopes_carry_no_private_runtime_metadata(tmp_path: Path) -> None:
    launcher_marker = "w3LAUNCHmarkerPATH"
    cwd_marker = "w3cwdMARKdir"
    workspace = tmp_path / cwd_marker
    workspace.mkdir()
    process = ControlledProcess(exit_code=1, stderr=io.BytesIO(b"bounded failure text"))
    client = HermesHostClient(
        str(tmp_path / launcher_marker),
        popen_factory=RecordingRunner(lambda: process),
        validate_launcher=False,
        timeout_seconds=30,
    )
    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("privacy", working_context={"cwd": str(workspace)}))
    handle = dispatch_result.adapter_handle
    process.release(1)
    assert wait_for_status(client, handle, {"failed"}) == "failed"

    status = adapter.status("privacy", handle)
    envelope = client.fetch_result(handle)
    result = adapter.result("privacy", handle)
    assert envelope["status"] == "failed"
    assert set(envelope) == {"status", "exit_code", "stdout", "stderr", "execution_stats", "error"}
    combined = {"status": status.to_dict(), "envelope": envelope, "result": result.to_dict()}
    assert_no_private_projection_leakage(combined, markers=(launcher_marker, cwd_marker))
    text = json.dumps(combined, default=str).lower()
    for banned in ("thread", "popen", "traceback", "controlleprocess"):
        assert banned not in text


# ---------------------------------------------------------------------------
# P. Bounded real production Hermes regression smoke (§37, §38)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AOTA_S3_REAL_HERMES_SMOKE") != "1",
    reason="bounded real Hermes production regression smoke (requires the production launcher)",
)
def test_real_production_hermes_happy_path_after_w3_hardening(tmp_path: Path) -> None:
    real_config = _operator_real_config()
    if real_config is None:
        pytest.skip("production Hermes executable unavailable via environment discovery (RUNTIME_ENVIRONMENT)")

    def factory(launcher_path: str, default_cwd: Any = None) -> HermesHostClient:
        return HermesHostClient(launcher_path, default_cwd=default_cwd, timeout_seconds=180)

    dispatcher = create_production_execution_dispatcher(
        default_cwd=str(tmp_path), host_client_factory=factory, runtime_config=real_config
    )
    adapter = dispatcher.registry.get("hermes")
    dispatch_result = adapter.dispatch(
        package(
            "w3-real-smoke",
            working_context={"cwd": str(tmp_path)},
            constraints={"timeout_seconds": 150},
            instruction=(
                "Return exactly: AOTA_FORGE_S3_M2_W3_OK\nDo not use tools.\nDo not modify files."
            ),
        )
    )
    task_id = dispatch_result.canonical_task_id
    handle = dispatch_result.adapter_handle
    client = adapter._host_client  # noqa: SLF001
    process = client._records[handle].process  # noqa: SLF001
    pid = process.pid

    try:
        deadline = time.monotonic() + 240
        terminal: CanonicalTaskState | None = None
        observed: list[CanonicalTaskState] = []
        while time.monotonic() < deadline:
            state = adapter.status(task_id, handle).state
            observed.append(state)
            if state.is_terminal:
                terminal = state
                break
            time.sleep(2)
        assert terminal is CanonicalTaskState.COMPLETED, f"real worker did not complete: observed {observed}"

        result = adapter.result(task_id, handle)
        assert_never_false_success(result)
        assert result.ok is True
        evidence = json.dumps(result.to_dict(), default=str)
        assert "AOTA_FORGE_S3_M2_W3_OK" in evidence, "real result must project canonically"
        lower = evidence.lower()
        for banned in ("runtime.env", "/home/latios/.venvs", "hermes-host", "-z "):
            assert banned not in lower, f"private/credential marker in real projection: {banned}"
        assert_no_private_projection_leakage(result.to_dict())
        assert process.poll() is not None
    finally:
        client.close()

    alive = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
            break
        time.sleep(0.1)
    assert not alive, "LINGERING_W3_PROBE_PROCESSES: real worker must not linger after close"
