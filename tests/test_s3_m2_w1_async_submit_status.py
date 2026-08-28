"""Focused S3/M2/W1 tests: async submit / status contract hardening.

Proves the accepted M1 runtime already satisfies the W1 contract:

    real async submit -> opaque adapter_handle -> private execution record
    -> status observation -> canonical status projection -> identity-safe access

No runtime source change is expected here; these tests freeze the contract.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from aota_forge.adapters.hermes.executor import (
    HERMES_STATUS_MAP,
    HermesAdapter,
    HermesAdapterError,
    HermesHostUnavailableError,
    hermes_status_to_canonical_state,
)
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage

HANDLE_SHAPE = re.compile(r"^hermes-host-[0-9a-f]{32}$")


class ControlledProcess:
    """Deterministic fake process that only exits when the test releases it."""

    def __init__(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.returncode: int | None = None
        self.stdout = io.BytesIO(b"controlled stdout")
        self.stderr = io.BytesIO(b"controlled stderr")
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


class RecordingRunner:
    """popen_factory stand-in that records launches and hands out one process."""

    def __init__(self, process: ControlledProcess | None = None, raises: Exception | None = None) -> None:
        self.process = process if process is not None else ControlledProcess()
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> ControlledProcess:
        if self.raises is not None:
            raise self.raises
        self.calls.append({"args": list(args), **kwargs})
        return self.process


class ScriptedHost:
    """Fake host protocol whose dispatch/status envelopes are test-controlled."""

    def __init__(self, dispatch_response: Any, status_response: Any = None) -> None:
        self.dispatch_response = dispatch_response
        self.status_response = status_response if status_response is not None else {"status": "running"}
        self.calls = {"dispatch": 0, "status": 0, "result": 0, "cancel": 0, "resume": 0}

    def dispatch(self, payload: Mapping[str, Any]) -> Any:
        self.calls["dispatch"] += 1
        return self.dispatch_response

    def query_status(self, adapter_handle: str) -> Any:
        self.calls["status"] += 1
        if isinstance(self.status_response, Exception):
            raise self.status_response
        return self.status_response

    def fetch_result(self, adapter_handle: str) -> Any:
        self.calls["result"] += 1
        return {"status": "done", "exit_code": 0}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        self.calls["cancel"] += 1
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls["resume"] += 1
        return {"status": "running"}


class StatusCountingHost:
    """Fake host that tracks every status call for identity-safety assertions."""

    def __init__(self) -> None:
        self.status_calls: list[str] = []
        self._next = 1
        self.statuses: dict[str, str] = {}

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        handle = f"s3-m2-w1-handle-{self._next}"
        self._next += 1
        self.statuses[handle] = "running"
        return {"adapter_handle": handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        self.status_calls.append(adapter_handle)
        return {"status": self.statuses.get(adapter_handle, "unreachable")}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "done", "exit_code": 0}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "running"}


def host_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "profile": "coder",
        "instruction": "Return exactly: AOTA_FORGE_S3_M2_W1_OK",
        "context": {"working_context": {}},
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "s3-m2-w1-package",
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
        instruction="Return exactly: AOTA_FORGE_S3_M2_W1_OK",
        **kwargs,
    )


def concrete_client(tmp_path: Path, runner: RecordingRunner, **kwargs: Any) -> HermesHostClient:
    kwargs.setdefault("validate_launcher", False)
    return HermesHostClient(str(tmp_path / "unused-launcher"), popen_factory=runner, **kwargs)


def wait_for_status(client: HermesHostClient, handle: str, terminal: set[str], budget: float = 5.0) -> str:
    deadline = time.monotonic() + budget
    status = ""
    while time.monotonic() < deadline:
        status = client.query_status(handle)["status"]
        if status in terminal:
            return status
        time.sleep(0.005)
    return status


# ---------------------------------------------------------------------------
# A. Async submit contract: real submit returns an opaque handle immediately
# ---------------------------------------------------------------------------


def test_dispatch_returns_handle_without_waiting_for_worker_completion(tmp_path: Path) -> None:
    process = ControlledProcess()
    runner = RecordingRunner(process)
    client = concrete_client(tmp_path, runner, timeout_seconds=30)

    started = time.monotonic()
    response = client.dispatch(payload_with_cwd(tmp_path))
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "dispatch must not wait for worker terminal completion"
    assert HANDLE_SHAPE.fullmatch(response["adapter_handle"])
    assert response["status"] == "pending"
    assert set(response) == {"adapter_handle", "status", "dispatch_time"}
    assert process.poll() is None, "worker must still be running when dispatch returns"

    assert client.query_status(response["adapter_handle"])["status"] == "running"
    process.release(0)
    assert wait_for_status(client, response["adapter_handle"], {"done"}) == "done"


def test_adapter_handle_is_opaque_and_carries_no_runtime_identity(tmp_path: Path) -> None:
    process = ControlledProcess()
    client = HermesHostClient(
        str(tmp_path / "unused"),
        default_cwd=str(tmp_path),
        popen_factory=RecordingRunner(process),
        validate_launcher=False,
    )
    response = client.dispatch(host_payload(profile="reviewer"))
    handle = response["adapter_handle"]

    assert HANDLE_SHAPE.fullmatch(handle)
    assert "reviewer" not in handle
    assert str(tmp_path) not in handle
    assert "s3-m2-w1" not in handle

    adapter = HermesAdapter(host_client=client)
    dispatch_result = adapter.dispatch(package("opaque-handle-task", working_context={"cwd": str(tmp_path)}))
    assert HANDLE_SHAPE.fullmatch(dispatch_result.adapter_handle)
    assert "opaque-handle-task" not in dispatch_result.adapter_handle
    assert "coder" not in dispatch_result.adapter_handle
    leaked = [key for key in dispatch_result.to_dict() if "pid" in key or "profile" in key or "session" in key]
    assert leaked == []
    process.release(0)


def test_execution_record_stays_private_and_status_is_derived_from_poll(tmp_path: Path) -> None:
    process = ControlledProcess()
    runner = RecordingRunner(process)
    client = concrete_client(tmp_path, runner, timeout_seconds=30)

    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    records = client._records  # private namespace, never surfaced through envelopes
    assert handle in records
    record = records[handle]
    assert record.process is process
    assert record.status == "pending"

    process.release(3)
    assert wait_for_status(client, handle, {"failed"}) == "failed"
    assert record.status == "failed"
    assert record.exit_code == 3


# ---------------------------------------------------------------------------
# B. Dispatch record ordering safety + rapid completion with real subprocesses
# ---------------------------------------------------------------------------


def test_returned_real_handle_is_immediately_queryable_no_registration_race(tmp_path: Path) -> None:
    launcher = tmp_path / "quick-exit.sh"
    launcher.write_text("#!/bin/sh\necho ok\nexit 0\n")
    launcher.chmod(0o755)
    client = HermesHostClient(str(launcher), default_cwd=str(tmp_path))

    for _ in range(25):
        response = client.dispatch(host_payload())
        handle = response["adapter_handle"]
        # Registration must precede handle return: the first status observation
        # for a returned handle can never be an unreachable/unknown artifact.
        first = client.query_status(handle)
        assert first["status"] != "unreachable"
        terminal = wait_for_status(client, handle, {"done", "failed"})
        assert terminal == "done"
    client.close()
    with client._records_lock:
        pids = [record.process.pid for record in client._records.values()]
    live = [pid for pid in pids if _pid_alive(pid)]
    assert live == []


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_fake_record_registration_ordering_returns_queryable_handle(tmp_path: Path) -> None:
    process = ControlledProcess(exit_code=0)
    process.release(0)  # worker already terminal at dispatch time
    runner = RecordingRunner(process)
    client = concrete_client(tmp_path, runner)

    handle = client.dispatch(payload_with_cwd(tmp_path))["adapter_handle"]
    assert client.query_status(handle)["status"] == "done"


# ---------------------------------------------------------------------------
# C. Deterministic lifecycle status matrix (Section 17)
# ---------------------------------------------------------------------------

STATUS_MATRIX = [
    ("init", CanonicalTaskState.CREATED),
    ("pending", CanonicalTaskState.QUEUED),
    ("running", CanonicalTaskState.RUNNING),
    ("waiting_for_input", CanonicalTaskState.WAITING),
    ("waiting_for_subagent", CanonicalTaskState.WAITING),
    ("done", CanonicalTaskState.COMPLETED),
    ("success", CanonicalTaskState.COMPLETED),
    ("error", CanonicalTaskState.FAILED),
    ("failed", CanonicalTaskState.FAILED),
    ("aborted", CanonicalTaskState.CANCELLED),
    ("cancelled", CanonicalTaskState.CANCELLED),
    ("unreachable", CanonicalTaskState.UNKNOWN),
    ("timeout", CanonicalTaskState.UNKNOWN),
]


@pytest.mark.parametrize(("hermes_status", "expected"), STATUS_MATRIX)
def test_pure_status_mapping_matrix(hermes_status: str, expected: CanonicalTaskState) -> None:
    assert hermes_status_to_canonical_state(hermes_status) is expected
    assert HERMES_STATUS_MAP[hermes_status] is expected


@pytest.mark.parametrize(("hermes_status", "expected"), STATUS_MATRIX)
def test_adapter_status_projection_matrix(
    tmp_path: Path, hermes_status: str, expected: CanonicalTaskState
) -> None:
    host = StatusCountingHost()
    adapter = HermesAdapter(host_client=host)
    dispatch_result = adapter.dispatch(package(f"matrix-{hermes_status}", working_context={"cwd": str(tmp_path)}))
    host.statuses[dispatch_result.adapter_handle] = hermes_status

    status_result = adapter.status(dispatch_result.canonical_task_id, dispatch_result.adapter_handle)

    assert status_result.state is expected
    assert status_result.canonical_task_id == dispatch_result.canonical_task_id
    assert host.status_calls == [dispatch_result.adapter_handle]


@pytest.mark.parametrize("raw", ["Totally-Unknown", "queued?", "", "   ", "null", "RUNNING_", "runnning"])
def test_unknown_variants_project_to_unknown_never_completion(raw: str) -> None:
    state = hermes_status_to_canonical_state(raw)
    assert state is CanonicalTaskState.UNKNOWN
    assert state is not CanonicalTaskState.COMPLETED


@pytest.mark.parametrize("raw", [None, 42, True, [], {}, 0])
def test_malformed_or_empty_status_never_projects_success(raw: Any) -> None:
    state = hermes_status_to_canonical_state(raw)
    assert state is CanonicalTaskState.UNKNOWN


@pytest.mark.parametrize("malformed", [{}, {"status": None}, {"status": ""}, "not-a-mapping", [7], 7])
def test_empty_and_malformed_status_envelopes_project_unknown(tmp_path: Path, malformed: Any) -> None:
    host = ScriptedHost({"adapter_handle": "h", "status": "pending"}, status_response=malformed)
    adapter = HermesAdapter(host_client=host)
    dispatch_result = adapter.dispatch(package("malformed-status", working_context={"cwd": str(tmp_path)}))

    result = adapter.status(dispatch_result.canonical_task_id, dispatch_result.adapter_handle)

    assert result.state is CanonicalTaskState.UNKNOWN
    assert result.state is not CanonicalTaskState.COMPLETED


def test_status_host_exception_projects_unknown(tmp_path: Path) -> None:
    host = ScriptedHost({"adapter_handle": "h", "status": "pending"}, status_response=RuntimeError("host exploded"))
    adapter = HermesAdapter(host_client=host)
    dispatch_result = adapter.dispatch(package("exploding-host", working_context={"cwd": str(tmp_path)}))

    result = adapter.status(dispatch_result.canonical_task_id, "h")

    assert result.state is CanonicalTaskState.UNKNOWN
    assert result.state is not CanonicalTaskState.COMPLETED


def test_waiting_subtypes_do_not_escape_into_core_schema() -> None:
    core_values = {state.value for state in CanonicalTaskState}
    assert "waiting_for_input" not in core_values
    assert "waiting_for_subagent" not in core_values
    for subtype in ("waiting_for_input", "waiting_for_subagent"):
        assert hermes_status_to_canonical_state(subtype) is CanonicalTaskState.WAITING


def test_real_timeout_projects_unknown_not_completion(tmp_path: Path) -> None:
    launcher = tmp_path / "sleeper.sh"
    launcher.write_text("#!/bin/sh\nexec sleep 30\n")
    launcher.chmod(0o755)
    client = HermesHostClient(str(launcher), default_cwd=str(tmp_path), timeout_seconds=30)
    adapter = HermesAdapter(host_client=client)

    dispatch_result = adapter.dispatch(
        package("timeout-task", working_context={"cwd": str(tmp_path)}, constraints={"timeout_seconds": 0.3})
    )
    deadline = time.monotonic() + 10.0
    state: CanonicalTaskState | None = None
    while time.monotonic() < deadline:
        state = adapter.status(
            dispatch_result.canonical_task_id, dispatch_result.adapter_handle
        ).state
        if state in {CanonicalTaskState.UNKNOWN, CanonicalTaskState.COMPLETED}:
            break
        time.sleep(0.05)

    assert state is CanonicalTaskState.UNKNOWN, "timeout must project to UNKNOWN, never COMPLETED/FAILED"
    host_status = client.query_status(dispatch_result.adapter_handle)["status"]
    assert host_status == "timeout"
    record = client._records[dispatch_result.adapter_handle]
    assert record.process.poll() is not None, "timed-out worker must be terminated"
    client.close()


# ---------------------------------------------------------------------------
# D. Status identity integrity (W2 guards, Section 13) and dispatch binding
#    establishment (Section 14)
# ---------------------------------------------------------------------------


def _bound_pair() -> tuple[HermesAdapter, StatusCountingHost, str, str]:
    host = StatusCountingHost()
    adapter = HermesAdapter(host_client=host)
    first = adapter.dispatch(package("task-one"))
    adapter.dispatch(package("task-two"))
    return adapter, host, first.canonical_task_id, first.adapter_handle


def test_known_task_with_correct_handle_allows_single_host_call() -> None:
    adapter, host, task_id, handle = _bound_pair()

    result = adapter.status(task_id, handle)

    assert result.state is CanonicalTaskState.RUNNING
    assert host.status_calls == [handle]


@pytest.mark.parametrize(
    ("pair_kind", "expected_code"),
    [
        ("known_task_wrong_handle", "TASK_ID_MISMATCH"),
        ("wrong_task_known_handle", "TASK_ID_MISMATCH"),
        ("known_task_unknown_handle", "TASK_ID_MISMATCH"),
        ("unknown_task_unknown_handle", "TASK_HANDLE_NOT_FOUND"),
    ],
)
def test_status_identity_rejections_never_reach_host(pair_kind: str, expected_code: str) -> None:
    adapter, host, task_a, handle_a = _bound_pair()
    adapter_other = adapter
    task_b = adapter_other._task_handles["task-two"]  # noqa: SLF001
    pairs = {
        "known_task_wrong_handle": (task_a, task_b),
        "wrong_task_known_handle": ("task-two", handle_a),
        "known_task_unknown_handle": (task_a, "never-issued-handle"),
        "unknown_task_unknown_handle": ("never-dispatched", "never-issued-handle"),
    }
    task_id, handle = pairs[pair_kind]

    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.status(task_id, handle)

    assert excinfo.value.code == expected_code
    assert host.status_calls == []


def test_inconsistent_binding_fails_closed_before_host_call() -> None:
    adapter, host, task_id, handle = _bound_pair()
    adapter._handle_tasks["orphan-forward-handle"] = task_id  # forward exists, reverse missing
    del adapter._task_handles[task_id]

    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.status(task_id, "orphan-forward-handle")
    assert excinfo.value.code == "ADAPTER_PROTOCOL_ERROR"
    assert host.status_calls == []

    adapter._task_handles[task_id] = "corrupt-reverse"
    with pytest.raises(HermesAdapterError):
        adapter.status(task_id, handle)
    assert host.status_calls == []


def test_blank_identity_arguments_rejected_before_any_lookup() -> None:
    adapter, host, *_ = _bound_pair()
    with pytest.raises(ValueError):
        adapter.status("", "some-handle")
    with pytest.raises(ValueError):
        adapter.status("task-one", "  ")
    assert host.status_calls == []


@pytest.mark.parametrize(
    ("malformed", "kind"),
    [
        ([["not-a-mapping"]], "non-mapping-envelope"),
        ({"status": "pending"}, "missing-adapter-handle"),
        ({"adapter_handle": "", "status": "pending"}, "empty-adapter-handle"),
        ({"adapter_handle": "   ", "status": "pending"}, "whitespace-adapter-handle"),
        ({"adapter_handle": 12345, "status": "pending"}, "non-string-adapter-handle"),
        ({"adapter_handle": None, "status": "pending"}, "null-adapter-handle"),
    ],
)
def test_malformed_dispatch_response_creates_no_binding(malformed: Any, kind: str) -> None:
    host = ScriptedHost(malformed)
    adapter = HermesAdapter(host_client=host)

    with pytest.raises(HermesAdapterError) as excinfo:
        adapter.dispatch(package(f"malformed-{kind}"))

    assert excinfo.value.code == "ADAPTER_PROTOCOL_ERROR"
    assert adapter._handle_tasks == {}  # noqa: SLF001
    assert adapter._task_handles == {}  # noqa: SLF001
    assert host.calls["status"] == 0
    with pytest.raises(HermesAdapterError) as lookup:
        adapter.status(f"malformed-{kind}", "any-handle")
    assert lookup.value.code == "TASK_HANDLE_NOT_FOUND"
    assert host.calls["status"] == 0


def test_valid_dispatch_establishes_bidirectional_binding(tmp_path: Path) -> None:
    host = StatusCountingHost()
    adapter = HermesAdapter(host_client=host)

    dispatch_result = adapter.dispatch(package("binding-ok", working_context={"cwd": str(tmp_path)}))

    assert adapter._handle_tasks[dispatch_result.adapter_handle] == "binding-ok"  # noqa: SLF001
    assert adapter._task_handles["binding-ok"] == dispatch_result.adapter_handle  # noqa: SLF001
    assert dispatch_result.initial_state is CanonicalTaskState.QUEUED


# ---------------------------------------------------------------------------
# E. Host unavailable / launch failure fail-closed (Section 15)
# ---------------------------------------------------------------------------


def test_missing_launcher_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient(str(tmp_path / "definitely-missing"))


def test_non_executable_launcher_fails_closed(tmp_path: Path) -> None:
    launcher = tmp_path / "not-executable"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o644)
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient(str(launcher))


def test_symlink_launcher_fails_closed(tmp_path: Path) -> None:
    real = tmp_path / "real-launcher"
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(0o755)
    link = tmp_path / "link-launcher"
    link.symlink_to(real)
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient(str(link))


def test_directory_launcher_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient(str(tmp_path))


@pytest.mark.parametrize("failure", [FileNotFoundError("No such file or directory"), PermissionError("denied"), ValueError("bad argv")])
def test_popen_failure_fails_closed_without_handle_or_record(tmp_path: Path, failure: Exception) -> None:
    runner = RecordingRunner(raises=failure)
    client = concrete_client(tmp_path, runner)

    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        client.dispatch(payload_with_cwd(tmp_path))

    assert client._records == {}  # no private record, no fake handle
    assert runner.calls == []


def test_production_composition_host_unavailable_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(HermesHostUnavailableError, match="EXECUTOR_UNAVAILABLE"):
        create_production_execution_dispatcher(launcher_path=str(tmp_path / "no-launcher-here"))


def test_production_composition_launch_failure_creates_no_identity_binding(tmp_path: Path) -> None:
    failing = RecordingRunner(raises=FileNotFoundError("launcher vanished"))

    def factory(launcher_path: str, default_cwd: Any = None) -> HermesHostClient:
        return HermesHostClient(
            launcher_path,
            default_cwd=default_cwd,
            popen_factory=failing,
            validate_launcher=False,
        )

    dispatcher = create_production_execution_dispatcher(host_client_factory=factory)
    adapter = dispatcher.registry.get("hermes")

    with pytest.raises(HermesAdapterError, match="EXECUTOR_UNAVAILABLE"):
        adapter.dispatch(package("launch-failed", working_context={"cwd": str(tmp_path)}))

    assert adapter._task_handles == {}  # noqa: SLF001
    assert adapter._handle_tasks == {}  # noqa: SLF001
    assert failing.calls == []
    with pytest.raises(HermesAdapterError) as lookup:
        adapter.status("launch-failed", "any-handle")  # noqa: SLF001
    assert lookup.value.code == "TASK_HANDLE_NOT_FOUND"


# ---------------------------------------------------------------------------
# F. M1/R1 regression anchors relevant to W1 (Sections 21-24)
# ---------------------------------------------------------------------------


def test_conflicting_execution_mode_rejected_before_process_start(tmp_path: Path) -> None:
    runner = RecordingRunner()
    client = concrete_client(tmp_path, runner, default_cwd=str(tmp_path))
    adapter = HermesAdapter(host_client=client)

    with pytest.raises(HermesAdapterError):
        adapter.dispatch(
            package(
                "mode-conflict",
                working_context={"cwd": str(tmp_path)},
                capability_requirements={"execution_mode": "sync"},
                constraints={"execution_mode": "async"},
            )
        )
    assert runner.calls == []

    with pytest.raises(Exception, match="CAPABILITY_MISMATCH|PACKAGE_INVALID"):
        client.dispatch(
            payload_with_cwd(
                tmp_path,
                capability_requirements={"execution_mode": "sync"},
                constraints={"execution_mode": "async"},
            )
        )
    assert runner.calls == []


def test_implicit_shell_cwd_fallback_is_not_used(tmp_path: Path) -> None:
    runner = RecordingRunner()
    client = HermesHostClient(str(tmp_path / "unused"), popen_factory=runner, validate_launcher=False)

    with pytest.raises(Exception, match="PACKAGE_INVALID"):
        client.dispatch(host_payload())
    assert runner.calls == []

    working = tmp_path / "work"
    working.mkdir()
    client_with_default = HermesHostClient(
        str(tmp_path / "unused"),
        default_cwd=str(tmp_path),
        popen_factory=runner,
        validate_launcher=False,
    )
    handle = client_with_default.dispatch(host_payload(context={"working_context": {"cwd": str(working)}}))[
        "adapter_handle"
    ]
    assert runner.calls[0]["cwd"] == str(working.resolve())
    client_with_default._records[handle].process.release(0)


def test_malformed_terminal_result_never_becomes_false_success(tmp_path: Path) -> None:
    host = ScriptedHost({"adapter_handle": "h", "status": "pending"})
    host.fetch_result = lambda handle: {"status": "done", "exit_code": 0, "result_data": "not-a-mapping"}  # type: ignore[method-assign]
    adapter = HermesAdapter(host_client=host)
    adapter.dispatch(package("bad-result", working_context={"cwd": str(tmp_path)}))

    result = adapter.result("bad-result", "h")

    assert result.ok is False
    assert result.canonical_task_state != CanonicalTaskState.COMPLETED.value
    assert result.error is not None and result.error["code"] == "RESULT_MALFORMED"


def test_production_capability_truth_unchanged() -> None:
    dispatcher = create_production_execution_dispatcher(
        host_client_factory=lambda launcher_path, default_cwd=None: HermesHostClient(
            launcher_path,
            default_cwd=default_cwd,
            popen_factory=RecordingRunner(),
            validate_launcher=False,
        )
    )
    caps = dispatcher.registry.get("hermes").capabilities()

    assert caps.supported_execution_modes == ("async",)
    assert caps.concurrency_limit == 1
    assert caps.supports_task_cancellation is True
    assert caps.supports_task_resume is False
