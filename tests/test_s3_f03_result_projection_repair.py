"""S3 coordinated repair for cross-lane finding W1-F03 (S2/M3/W1 -> S3 owner).

Adjudicated class: S3_ADAPTER_RUNTIME_DEFECT. No S1 canonical contract defect:
CanonicalResult already expresses non-terminal uncertainty
(status="unknown" / canonical_task_state=UNKNOWN / retryable error).

Repaired semantic requirement:

    a transient result-fetch or result-projection failure must not fabricate an
    unrecoverable terminal task failure unless terminal task failure itself is
    authoritative

    EXECUTION_FAILURE != RESULT_OBSERVATION_FAILURE

Coverage:
- transient fetch_result exception -> UNKNOWN / non-terminal / retryable
- malformed result observation (non-mapping, malformed terminal fields) ->
  recoverable uncertainty instead of sticky terminal FAILED
- genuine later terminal truth is still accepted after a transient projection
  failure (terminal truth is not poisoned)
- valid success stays COMPLETED, valid authoritative failure stays FAILED,
  RUNNING task_result keeps TASK_STILL_RUNNING
- accepted Core stickiness / terminal-contradiction guards remain in force
- canonical identity and Hermes-private opacity are preserved

Durability scope unchanged: everything here is in-process, D0/process-local.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from aota_forge.adapters.hermes.executor import (
    HERMES_EXECUTOR_ID,
    HermesAdapter,
    hermes_output_to_canonical_result,
)
from aota_forge.composition.execution import bind_production_execution_dispatcher
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage
from aota_forge.core.execution.dispatcher import AdapterProtocolError
from aota_forge.core.execution.results import FORBIDDEN_HERMES_RESULT_KEYS
from aota_forge.core.ingress import execute, reset_execution_dispatcher

PRIVATE_PROJECTION_TOKENS = FORBIDDEN_HERMES_RESULT_KEYS | frozenset(
    {"pid", "process", "profile", "cwd", "readers", "deadline", "terminal_at", "started_at", "adapter_handle"}
)


class SequencedHost:
    """Deterministic fake Hermes host client with per-call scripted outcomes."""

    def __init__(self) -> None:
        self.result_script: list[Any] = []
        self.status_by_handle: dict[str, Any] = {}
        self.handles: dict[str, str] = {}
        self.fetch_calls = 0

    def dispatch(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = payload["context"]["canonical_task_id"]
        handle = f"f03-opaque-{task_id}"
        self.handles[task_id] = handle
        return {"adapter_handle": handle, "status": "pending"}

    def query_status(self, adapter_handle: str) -> Mapping[str, Any]:
        return dict(self.status_by_handle.get(adapter_handle, {"status": "running"}))

    def fetch_result(self, adapter_handle: str) -> Any:
        self.fetch_calls += 1
        if self.result_script:
            outcome = self.result_script.pop(0)
        else:
            outcome = {"status": "unreachable"}
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome) if isinstance(outcome, Mapping) else outcome

    def cancel_task(self, adapter_handle: str) -> Mapping[str, Any]:
        return {"cancelled": False, "status": "running", "details": "out of F03 scope"}

    def resume_task(self, adapter_handle: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "error", "error": {"code": "RESUME_UNSUPPORTED"}}


def package(task_id: str, tmp_path: Path) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Return exactly: AOTA_FORGE_S3_F03_REPAIR",
        working_context={"cwd": str(tmp_path)},
    )


def bound_adapter(tmp_path: Path, host: SequencedHost, task_id: str) -> tuple[HermesAdapter, str, str]:
    adapter = HermesAdapter(host_client=host)
    dispatched = adapter.dispatch(package(task_id, tmp_path))
    return adapter, dispatched.canonical_task_id, dispatched.adapter_handle


def _iter_keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_keys(item)


def assert_recoverable_uncertainty(result: Any, *, error_code: str) -> None:
    """The narrowest existing representation of an untrustworthy observation."""
    assert result.ok is False
    assert result.status == "unknown"
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value
    assert CanonicalTaskState.UNKNOWN.is_terminal is False
    assert result.canonical_task_state != CanonicalTaskState.FAILED.value
    assert result.error is not None
    assert result.error["code"] == error_code, "typed error code must be preserved"
    assert result.error["retryable"] is True, "result-observation failure must stay recoverable"
    assert result.exit_code is None, "no fabricated exit code may be projected"
    assert result.result_data == {}, "no fabricated result payload may be projected"


def assert_no_private_leakage(result: Any) -> None:
    payload = result.to_dict()
    keys = {key.lower() for key in _iter_keys(payload)}
    offenders = sorted(keys & {token.lower() for token in PRIVATE_PROJECTION_TOKENS})
    assert offenders == [], f"private runtime keys escaped into projection: {offenders}"
    text = json.dumps(payload, default=str, sort_keys=True).lower()
    for marker in ("popen(", "threading.object", " -z ", "processregistry"):
        assert marker not in text, f"private runtime marker leaked into projection: {marker!r}"


@pytest.fixture(autouse=True)
def _clean_dispatcher_binding():
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# A. Transient result-fetch failure is recoverable uncertainty
# ---------------------------------------------------------------------------


def test_fetch_result_exception_projects_recoverable_uncertainty(tmp_path: Path) -> None:
    host = SequencedHost()
    host.result_script = [RuntimeError("transient transport noise")]
    adapter, task_id, handle = bound_adapter(tmp_path, host, "f03-fetch-exc")

    result = adapter.result(task_id, handle)

    assert_recoverable_uncertainty(result, error_code="ADAPTER_PROTOCOL_ERROR")
    assert result.canonical_task_id == task_id
    assert result.executor_id == HERMES_EXECUTOR_ID


def test_fetch_result_non_mapping_projects_recoverable_uncertainty(tmp_path: Path) -> None:
    host = SequencedHost()
    host.result_script = ["not-a-mapping"]
    adapter, task_id, handle = bound_adapter(tmp_path, host, "f03-fetch-nonmapping")

    result = adapter.result(task_id, handle)

    assert_recoverable_uncertainty(result, error_code="RESULT_MALFORMED")


def test_host_output_non_mapping_projects_recoverable_uncertainty() -> None:
    result = hermes_output_to_canonical_result("not-a-mapping", canonical_task_id="f03-output-nonmapping")

    assert_recoverable_uncertainty(result, error_code="RESULT_MALFORMED")


@pytest.mark.parametrize(
    "field,value",
    [
        ("exit_code", "0"),
        ("exit_code", None),
        ("exit_code", 1.5),
        ("exit_code", True),
        ("result_data", "scalar"),
        ("result_data", []),
        ("result_data", None),
        ("output_artifacts", "artifact.txt"),
        ("artifacts", [42]),
    ],
)
def test_malformed_terminal_fields_under_done_claim_are_recoverable(field: str, value: Any) -> None:
    output: dict[str, Any] = {"status": "done", "exit_code": 0}
    output[field] = value

    result = hermes_output_to_canonical_result(output, canonical_task_id="f03-malformed-done")

    assert_recoverable_uncertainty(result, error_code="RESULT_MALFORMED")


@pytest.mark.parametrize(
    "field,value",
    [("exit_code", "1"), ("result_data", "scalar"), ("output_artifacts", {"path": "a.txt"})],
)
def test_malformed_terminal_fields_under_failed_claim_are_recoverable(field: str, value: Any) -> None:
    """A malformed envelope may not be used as authority for terminal FAILED."""
    output: dict[str, Any] = {
        "status": "failed",
        "exit_code": 1,
        "error": {"code": "EXECUTION_FAILED", "message": "worker failed"},
    }
    output[field] = value

    result = hermes_output_to_canonical_result(output, canonical_task_id="f03-malformed-failed")

    assert_recoverable_uncertainty(result, error_code="RESULT_MALFORMED")


# ---------------------------------------------------------------------------
# B. Authoritative terminal semantics are preserved (no global FAILED->UNKNOWN)
# ---------------------------------------------------------------------------


def test_valid_success_result_remains_completed() -> None:
    result = hermes_output_to_canonical_result(
        {
            "status": "done",
            "exit_code": 0,
            "result_data": {"answer": "AOTA_FORGE_S3_F03_REPAIR"},
            "output_artifacts": [{"name": "diff.patch"}],
        },
        canonical_task_id="f03-valid-success",
    )

    assert result.ok is True
    assert result.status == "completed"
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    assert result.exit_code == 0
    assert result.error is None


def test_valid_authoritative_execution_failure_remains_failed() -> None:
    result = hermes_output_to_canonical_result(
        {
            "status": "failed",
            "exit_code": 7,
            "error": {"code": "EXECUTION_FAILED", "message": "Hermes worker exited unsuccessfully"},
        },
        canonical_task_id="f03-valid-failure",
    )

    assert result.ok is False
    assert result.status == "failed"
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.exit_code == 7
    assert result.error is not None and result.error["code"] == "EXECUTION_FAILED"
    assert CanonicalTaskState.FAILED.is_terminal is True


def test_host_reported_capture_failure_remains_authoritative_failed(tmp_path: Path) -> None:
    """Accepted M2/W3 semantic: the host itself reports a failed terminal
    envelope with valid fields -> terminal FAILED, not uncertainty."""
    host = SequencedHost()
    host.result_script = [
        {
            "status": "failed",
            "exit_code": 0,
            "error": {"code": "RESULT_UNAVAILABLE", "message": "output capture failed"},
        }
    ]
    adapter, task_id, handle = bound_adapter(tmp_path, host, "f03-capture-failure")

    result = adapter.result(task_id, handle)

    assert result.ok is False
    assert result.status == "failed"
    assert result.canonical_task_state == CanonicalTaskState.FAILED.value
    assert result.error is not None and result.error["code"] == "RESULT_UNAVAILABLE"


def test_active_running_result_semantics_preserved(tmp_path: Path) -> None:
    host = SequencedHost()
    host.status_by_handle["f03-opaque-f03-running"] = {"status": "running"}
    host.result_script = [{"status": "running"}]
    adapter, task_id, handle = bound_adapter(tmp_path, host, "f03-running")

    result = adapter.result(task_id, handle)

    assert result.ok is False
    assert result.error is not None and result.error["code"] == "TASK_STILL_RUNNING"
    assert result.error["retryable"] is True
    assert result.status == "unknown"
    assert result.canonical_task_state == CanonicalTaskState.RUNNING.value


# ---------------------------------------------------------------------------
# C. Recovery proof: transient projection failure cannot poison terminal truth
# ---------------------------------------------------------------------------


def _start_through_ingress(tmp_path: Path, task_id: str) -> dict[str, Any]:
    envelope = execute(
        "execution.task_start",
        {
            "canonical_task_id": task_id,
            "executor": HERMES_EXECUTOR_ID,
            "role": "coder",
            "instruction": "Return exactly: AOTA_FORGE_S3_F03_REPAIR",
            "project_id": "aota_forge",
            "working_context": {"cwd": str(tmp_path)},
        },
    )
    assert envelope["ok"], f"canonical task_start must succeed: {envelope}"
    return envelope


def _task_result(task_id: str) -> dict[str, Any]:
    return execute("execution.task_result", {"task_id": task_id, "executor": HERMES_EXECUTOR_ID})


def test_transient_fetch_failure_then_genuine_completion_is_accepted(tmp_path: Path) -> None:
    host = SequencedHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "f03-recovery-completion"
    _start_through_ingress(tmp_path, task_id)
    handle = dispatcher.get_route(task_id).adapter_handle
    host.status_by_handle[handle] = {"status": "running"}

    host.result_script = [RuntimeError("transient transport noise")]
    transient = _task_result(task_id)
    assert transient["ok"] is True, "transport envelope stays well-formed"
    assert transient["data"]["canonical_task_state"] == CanonicalTaskState.UNKNOWN.value
    assert transient["data"]["error"]["code"] == "ADAPTER_PROTOCOL_ERROR"
    assert transient["data"]["error"]["retryable"] is True
    route = dispatcher.get_route(task_id)
    assert not route.last_known_state.is_terminal, "transient result failure must not stick a terminal state"

    host.status_by_handle[handle] = {"status": "done"}
    host.result_script = [{"status": "done", "exit_code": 0, "result_data": {"answer": "genuine-terminal-truth"}}]
    genuine = _task_result(task_id)

    assert genuine["ok"] is True
    assert genuine["data"]["ok"] is True
    assert genuine["data"]["status"] == "completed"
    assert genuine["data"]["canonical_task_state"] == CanonicalTaskState.COMPLETED.value
    assert genuine["data"]["result_data"] == {"answer": "genuine-terminal-truth"}
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.COMPLETED


def test_transient_malformed_result_then_genuine_completion_is_accepted(tmp_path: Path) -> None:
    host = SequencedHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "f03-recovery-malformed"
    _start_through_ingress(tmp_path, task_id)
    handle = dispatcher.get_route(task_id).adapter_handle

    host.result_script = [{"status": "done", "exit_code": "0", "result_data": "not-a-mapping"}]
    transient = _task_result(task_id)
    assert transient["data"]["canonical_task_state"] == CanonicalTaskState.UNKNOWN.value
    assert transient["data"]["error"]["code"] == "RESULT_MALFORMED"
    assert not dispatcher.get_route(task_id).last_known_state.is_terminal

    host.result_script = [{"status": "done", "exit_code": 0, "result_data": {"answer": "recovered"}}]
    genuine = _task_result(task_id)
    assert genuine["data"]["ok"] is True
    assert genuine["data"]["canonical_task_state"] == CanonicalTaskState.COMPLETED.value


def test_transient_failure_then_genuine_authoritative_failure_is_accepted(tmp_path: Path) -> None:
    """Recoverable uncertainty must also let a genuine terminal FAILED through."""
    host = SequencedHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "f03-recovery-failure"
    _start_through_ingress(tmp_path, task_id)
    handle = dispatcher.get_route(task_id).adapter_handle

    host.result_script = ["not-a-mapping"]
    transient = _task_result(task_id)
    assert transient["data"]["canonical_task_state"] == CanonicalTaskState.UNKNOWN.value
    assert not dispatcher.get_route(task_id).last_known_state.is_terminal

    host.result_script = [
        {
            "status": "failed",
            "exit_code": 3,
            "error": {"code": "EXECUTION_FAILED", "message": "worker died"},
        }
    ]
    genuine = _task_result(task_id)
    assert genuine["data"]["ok"] is False
    assert genuine["data"]["canonical_task_state"] == CanonicalTaskState.FAILED.value
    assert genuine["data"]["error"]["code"] == "EXECUTION_FAILED"
    assert dispatcher.get_route(task_id).last_known_state is CanonicalTaskState.FAILED


# ---------------------------------------------------------------------------
# D. Accepted Core terminal stickiness remains in force (not weakened)
# ---------------------------------------------------------------------------


def test_known_terminal_state_is_still_sticky_after_valid_failure(tmp_path: Path) -> None:
    host = SequencedHost()
    dispatcher = bind_production_execution_dispatcher(host_client=host)
    task_id = "f03-sticky-terminal"
    _start_through_ingress(tmp_path, task_id)
    handle = dispatcher.get_route(task_id).adapter_handle

    host.result_script = [
        {
            "status": "failed",
            "exit_code": 1,
            "error": {"code": "EXECUTION_FAILED", "message": "authoritative terminal failure"},
        }
    ]
    first = _task_result(task_id)
    assert first["data"]["canonical_task_state"] == CanonicalTaskState.FAILED.value

    host.result_script = [{"status": "done", "exit_code": 0, "result_data": {"answer": "contradiction"}}]
    with pytest.raises(AdapterProtocolError, match="contradicts"):
        dispatcher.result(task_id)


# ---------------------------------------------------------------------------
# E. Identity boundary is preserved by the repaired projection
# ---------------------------------------------------------------------------


def test_repaired_projection_preserves_canonical_identity_and_privacy(tmp_path: Path) -> None:
    host = SequencedHost()
    adapter, task_id, handle = bound_adapter(tmp_path, host, "f03-identity")
    assert handle != task_id, "adapter_handle must stay distinct from canonical identity"

    host.result_script = [RuntimeError("transient transport noise")]
    result = adapter.result(task_id, handle)

    assert result.canonical_task_id == task_id
    assert result.executor_id == HERMES_EXECUTOR_ID
    assert result.correlation_id and result.correlation_id.strip()
    assert handle not in json.dumps(result.to_dict(), default=str), "opaque adapter_handle must not be projected"
    assert_no_private_leakage(result)

    host.result_script = ["not-a-mapping"]
    malformed = adapter.result(task_id, handle)
    assert malformed.canonical_task_id == task_id
    assert malformed.executor_id == HERMES_EXECUTOR_ID
    assert_no_private_leakage(malformed)
