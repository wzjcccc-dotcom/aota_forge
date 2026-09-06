"""Focused W3 tests for the real Hermes host bridge and composition graph."""

from __future__ import annotations

import io
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient, HermesHostClientError
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage
from aota_forge.core.ingress import (
    bind_execution_dispatcher,
    execute,
    get_execution_dispatcher,
    reset_execution_dispatcher,
)


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.stdout = io.BytesIO(b"fake stdout")
        self.stderr = io.BytesIO(b"fake stderr")
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None and timeout is not None and timeout < 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeRunner:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls: list[dict[str, Any]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append({"args": args, **kwargs})
        return self.process


class FakeHost:
    def __init__(self) -> None:
        self.payloads: list[Mapping[str, Any]] = []
        self._handle = "fake-w3-handle"

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.payloads.append(payload)
        return {"adapter_handle": self._handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "done"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "done", "exit_code": 0, "stdout": "AOTA_FORGE_W3_OK"}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error"}


def package(**kwargs: Any) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=kwargs.pop("canonical_task_id", "w3-task"),
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Return exactly: AOTA_FORGE_W3_OK",
        **kwargs,
    )


def host_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "profile": "coder",
        "instruction": "Return exactly: AOTA_FORGE_W3_OK",
        "context": {"working_context": kwargs.pop("working_context", {})},
        "artifacts": kwargs.pop("artifacts", []),
        "constraints": kwargs.pop("constraints", {}),
        "capability_requirements": kwargs.pop("capability_requirements", {}),
        "result_expectations": kwargs.pop("result_expectations", {}),
        "operation": "task_dispatch",
        "package_id": "w3-package",
    }


def test_launcher_invocation_profile_instruction_and_cwd(tmp_path: Path) -> None:
    process = FakeProcess(returncode=0)
    runner = FakeRunner(process)
    client = HermesHostClient(
        "/fake/hermes-host",
        popen_factory=runner,
        validate_launcher=False,
    )

    response = client.dispatch(host_payload(working_context={"cwd": str(tmp_path)}))

    assert response["status"] == "pending"
    assert runner.calls[0]["args"] == [
        "/fake/hermes-host",
        "-p",
        "coder",
        "-z",
        "Return exactly: AOTA_FORGE_W3_OK",
    ]
    assert runner.calls[0]["cwd"] == str(tmp_path.resolve())
    assert response["adapter_handle"] != str(process.returncode)


def test_status_result_failure_unknown_and_bounded_output(tmp_path: Path) -> None:
    process = FakeProcess(returncode=1)
    process.stdout = io.BytesIO(b"x" * 1024)
    process.stderr = io.BytesIO(b"failure")
    client = HermesHostClient(
        "/fake/hermes-host",
        output_limit_bytes=32,
        popen_factory=FakeRunner(process),
        validate_launcher=False,
    )
    handle = client.dispatch(host_payload(working_context={"cwd": str(tmp_path)}))["adapter_handle"]

    status = client.query_status(handle)
    result = client.fetch_result(handle)

    assert status["status"] == "failed"
    assert result["status"] == "failed"
    assert result["exit_code"] == 1
    assert len(result["stdout"].encode()) <= 32
    assert result["error"]["code"] == "EXECUTION_FAILED"
    assert client.query_status("unknown") ["status"] == "unreachable"


def test_missing_launcher_and_unsupported_semantics_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="EXECUTOR_UNAVAILABLE"):
        HermesHostClient("/does/not/exist")

    client = HermesHostClient("/fake/hermes-host", popen_factory=FakeRunner(FakeProcess()), validate_launcher=False)
    with pytest.raises(HermesHostClientError, match="CAPABILITY_MISMATCH"):
        client.dispatch(host_payload(artifacts=[{"path": "not-forwarded"}]))
    with pytest.raises(HermesHostClientError, match="CAPABILITY_MISMATCH"):
        client.dispatch(host_payload(working_context={"unsupported": "not-forwarded"}))
    with pytest.raises(HermesHostClientError, match="PACKAGE_INVALID"):
        client.dispatch(host_payload(working_context={"cwd": str(tmp_path / "missing")}))


def test_timeout_and_owned_process_cancellation(tmp_path: Path) -> None:
    process = FakeProcess()
    client = HermesHostClient(
        "/fake/hermes-host",
        timeout_seconds=0.01,
        popen_factory=FakeRunner(process),
        validate_launcher=False,
    )
    handle = client.dispatch(host_payload(working_context={"cwd": str(tmp_path)}))["adapter_handle"]
    time.sleep(0.03)
    assert client.query_status(handle)["status"] == "timeout"
    assert process.terminated is True

    process2 = FakeProcess()
    client2 = HermesHostClient(
        "/fake/hermes-host",
        popen_factory=FakeRunner(process2),
        validate_launcher=False,
    )
    handle2 = client2.dispatch(host_payload(working_context={"cwd": str(tmp_path)}))["adapter_handle"]
    assert client2.cancel_task(handle2) == {"cancelled": True, "status": "cancelled"}
    assert process2.terminated is True
    assert client2.resume_task(handle2, {})["error"]["code"] == "RESUME_UNSUPPORTED"


def test_production_capabilities_and_explicit_profile_mapping() -> None:
    adapter = HermesAdapter(host_client=FakeHost())
    production = create_production_execution_dispatcher(host_client=FakeHost())
    registered = production.registry.get("hermes")

    assert adapter is not registered
    caps = registered.capabilities()
    # M1 accepted architecture: the four Worker canonical roles share the
    # aota-worker profile via the operator-owned runtime config binding.
    assert caps.supported_canonical_roles == ("coder", "planner", "reviewer", "steward")
    assert caps.supported_execution_modes == ("async",)
    assert caps.supports_task_cancellation is True
    assert caps.supports_task_resume is False
    assert caps.supports_structured_result is False
    assert caps.supports_artifact_transport is False
    assert caps.supports_working_directory is True


def test_canonical_ingress_reaches_production_hermes_graph() -> None:
    reset_execution_dispatcher()
    try:
        host = FakeHost()
        dispatcher = create_production_execution_dispatcher(host_client=host)
        bind_execution_dispatcher(dispatcher)

        listed = execute("execution.executor_list", {})
        started = execute(
            "execution.task_start",
            {
                "executor": "hermes",
                "role": "coder",
                "instruction": "Return exactly: AOTA_FORGE_W3_OK",
                "project_id": "aota_forge",
                "canonical_task_id": "w3-ingress-task",
            },
        )
        status = execute(
            "execution.task_status",
            {"executor": "hermes", "task_id": "w3-ingress-task"},
        )
        result = execute(
            "execution.task_result",
            {"executor": "hermes", "task_id": "w3-ingress-task"},
        )

        assert get_execution_dispatcher() is dispatcher
        assert listed["ok"] is True
        assert [item["executor_id"] for item in listed["data"]["executors"]] == ["hermes"]
        assert started["ok"] is True
        assert status["data"]["state"] == CanonicalTaskState.COMPLETED.value
        assert result["ok"] is True
        assert result["data"]["stdout_summary"] == "AOTA_FORGE_W3_OK"
        assert host.payloads[0]["profile"] == "aota-worker"
        assert host.payloads[0]["toolsets"] == ["aota"]
    finally:
        reset_execution_dispatcher()
