"""Bounded S3/M1/R1 regressions for the RV1 blocking findings."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping

import pytest

from aota_forge.adapters.hermes.executor import (
    HermesAdapter,
    HermesDispatchRejectedError,
    hermes_output_to_canonical_result,
)
from aota_forge.adapters.hermes.host_client import HermesHostClient, HermesHostClientError
from aota_forge.composition.execution import (
    PRODUCTION_HERMES_DEFAULT_CWD,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution import ExecutionPackage
from aota_forge.core.execution.dispatcher import PackageInvalidError


class FakeProcess:
    def __init__(self, returncode: int | None = 0) -> None:
        self.returncode = returncode
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class CountingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append({"args": args, **kwargs})
        return FakeProcess()


class CountingHost:
    def __init__(self) -> None:
        self.dispatch_calls = 0

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.dispatch_calls += 1
        return {"adapter_handle": "r1-handle", "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "done"}

    def fetch_result(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"status": "done", "exit_code": 0}

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": True, "status": "cancelled"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error"}


def host_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "profile": "coder",
        "instruction": "Return exactly: AOTA_FORGE_S3_M1_R1_OK",
        "context": {"working_context": kwargs.pop("working_context", {})},
        "artifacts": [],
        "constraints": kwargs.pop("constraints", {}),
        "capability_requirements": kwargs.pop("capability_requirements", {}),
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": "r1-package",
    }


def package(**kwargs: Any) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=kwargs.pop("canonical_task_id", "r1-task"),
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Return exactly: AOTA_FORGE_S3_M1_R1_OK",
        **kwargs,
    )


# F001: terminal envelopes must be structurally valid before completion projection.
@pytest.mark.parametrize(
    "field, value",
    [
        ("exit_code", "not-an-int"),
        ("exit_code", []),
        ("exit_code", None),
        ("result_data", []),
        ("result_data", "scalar"),
        ("output_artifacts", {"path": "artifact.txt"}),
        ("output_artifacts", "artifact.txt"),
        ("artifacts", {"path": "artifact.txt"}),
        ("artifacts", "artifact.txt"),
    ],
)
def test_malformed_terminal_fields_fail_closed(field: str, value: Any) -> None:
    output: dict[str, Any] = {"status": "done", field: value}

    result = hermes_output_to_canonical_result(output, canonical_task_id="r1-result")

    assert result.ok is False
    assert result.status != "completed"
    assert result.canonical_task_state != "COMPLETED"
    assert result.error["code"] == "RESULT_MALFORMED"


def test_valid_terminal_envelope_is_completed() -> None:
    result = hermes_output_to_canonical_result(
        {
            "status": "done",
            "exit_code": 0,
            "result_data": {"answer": "ok"},
            "output_artifacts": [],
        },
        canonical_task_id="r1-valid-result",
    )

    assert result.ok is True
    assert result.status == "completed"
    assert result.canonical_task_state == "COMPLETED"


def test_missing_optional_terminal_fields_retain_valid_behavior() -> None:
    result = hermes_output_to_canonical_result(
        {"status": "success"},
        canonical_task_id="r1-missing-optional",
    )

    assert result.ok is True
    assert result.exit_code == 0
    assert result.result_data == {}
    assert result.output_artifacts == ()


# F002: both execution-mode sources are validated and conflicts never reach a host.
@pytest.mark.parametrize(
    ("capability_mode", "constraint_mode"),
    [("async", "sync"), ("sync", "async")],
)
def test_conflicting_execution_modes_are_rejected_before_host_call(
    capability_mode: str,
    constraint_mode: str,
) -> None:
    host = CountingHost()
    adapter = HermesAdapter(host_client=host)

    with pytest.raises(HermesDispatchRejectedError, match="conflicting execution_mode"):
        adapter.dispatch(
            package(
                capability_requirements={"execution_mode": capability_mode},
                constraints={"execution_mode": constraint_mode},
            )
        )

    assert host.dispatch_calls == 0


def test_host_conflict_is_rejected_before_runner_call(tmp_path: Path) -> None:
    runner = CountingRunner()
    client = HermesHostClient(
        "/fake/hermes-host",
        default_cwd=tmp_path,
        popen_factory=runner,
        validate_launcher=False,
    )

    with pytest.raises(HermesHostClientError, match="conflicting execution_mode"):
        client.dispatch(
            host_payload(
                capability_requirements={"execution_mode": "async"},
                constraints={"execution_mode": "sync"},
            )
        )

    assert runner.calls == []


@pytest.mark.parametrize(
    "requirements, constraints",
    [
        ({"execution_mode": "async"}, {}),
        ({}, {"execution_mode": "async"}),
        ({"execution_mode": "async"}, {"execution_mode": "async"}),
        ({"execution_mode": "sync"}, {}),
        ({}, {"execution_mode": "sync"}),
        ({"execution_mode": "sync"}, {"execution_mode": "sync"}),
    ],
)
def test_non_conflicting_execution_modes_are_retained(
    requirements: dict[str, str],
    constraints: dict[str, str],
) -> None:
    host = CountingHost()
    adapter = HermesAdapter(host_client=host)

    result = adapter.validate_package(
        package(capability_requirements=requirements, constraints=constraints)
    )

    assert result.valid is True


def test_unsupported_execution_mode_is_rejected_without_host_call() -> None:
    host = CountingHost()
    adapter = HermesAdapter(host_client=host)

    with pytest.raises(HermesDispatchRejectedError, match="execution mode"):
        adapter.dispatch(package(capability_requirements={"execution_mode": "unsupported"}))

    assert host.dispatch_calls == 0


def test_production_dispatcher_rejects_conflict_before_host_call() -> None:
    host = CountingHost()
    dispatcher = create_production_execution_dispatcher(host_client=host)

    with pytest.raises(PackageInvalidError, match="conflicting execution_mode"):
        dispatcher.dispatch(
            package(
                capability_requirements={"execution_mode": "async"},
                constraints={"execution_mode": "sync"},
            ),
            target_executor_id="hermes",
        )

    assert host.dispatch_calls == 0


# F003: only canonical task context or an explicit trusted composition default may win.
def test_explicit_task_cwd_is_forwarded(tmp_path: Path) -> None:
    runner = CountingRunner()
    client = HermesHostClient(
        "/fake/hermes-host",
        popen_factory=runner,
        validate_launcher=False,
    )

    client.dispatch(host_payload(working_context={"cwd": str(tmp_path)}))

    assert len(runner.calls) == 1
    assert runner.calls[0]["cwd"] == str(tmp_path.resolve())


def test_invalid_explicit_task_cwd_fails_before_launch(tmp_path: Path) -> None:
    runner = CountingRunner()
    client = HermesHostClient(
        "/fake/hermes-host",
        popen_factory=runner,
        validate_launcher=False,
    )

    with pytest.raises(HermesHostClientError, match="working directory"):
        client.dispatch(host_payload(working_context={"cwd": str(tmp_path / "missing")}))

    assert runner.calls == []


def test_trusted_default_cwd_is_used_when_task_cwd_is_missing(tmp_path: Path) -> None:
    runner = CountingRunner()
    client = HermesHostClient(
        "/fake/hermes-host",
        default_cwd=tmp_path,
        popen_factory=runner,
        validate_launcher=False,
    )

    client.dispatch(host_payload())

    assert runner.calls[0]["cwd"] == str(tmp_path.resolve())


def test_missing_cwd_without_trusted_default_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CountingRunner()
    client = HermesHostClient(
        "/fake/hermes-host",
        popen_factory=runner,
        validate_launcher=False,
    )
    monkeypatch.setattr(Path, "cwd", lambda: (_ for _ in ()).throw(AssertionError("shell CWD used")))

    with pytest.raises(HermesHostClientError, match="canonical or trusted default"):
        client.dispatch(host_payload())

    assert runner.calls == []


def test_production_composition_injects_deterministic_trusted_cwd() -> None:
    captured: dict[str, Any] = {}

    def factory(launcher_path: str, **kwargs: Any) -> CountingHost:
        captured["launcher_path"] = launcher_path
        captured.update(kwargs)
        return CountingHost()

    create_production_execution_dispatcher(host_client_factory=factory)

    assert captured["default_cwd"] == PRODUCTION_HERMES_DEFAULT_CWD
    assert Path(captured["default_cwd"]).is_dir()
