"""AF #56 M2/W1 — OpenCode Host Client & ExecutorAdapter unit tests.

Covers the W1 contract without any live server: exact-session addressing,
explicit AF-bound directory scope on every directory-scoped call, typed
mechanical failures (unavailable/timeout/404/malformed/protocol/retriable/
rejected), role mapping, capability truthfulness, status mapping conservatism,
mechanical terminal projection, fail-closed resume, and hardened absence of
latest/newest/title/directory heuristics.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aota_forge.adapters.opencode.executor import (
    OPENCODE_EXECUTOR_ID,
    OpenCodeAdapter,
    OpenCodeAdapterError,
    OpenCodeDispatchFailureError,
    OpenCodeResumeUnsupportedError,
    OpenCodeWorkerDirectoryUnavailableError,
    build_opencode_host_payload,
    default_opencode_capabilities,
)
from aota_forge.adapters.opencode.host_client import (
    OpenCodeDispatchRejectedError,
    OpenCodeHostClient,
    OpenCodeHostUnavailableError,
    OpenCodeMalformedResponseError,
    OpenCodeProtocolViolationError,
    OpenCodeRetriableHostError,
    OpenCodeSessionNotFoundError,
    OpenCodeTimeoutError,
    OpenCodeHttpResponse,
    parse_session_info,
    turn_evidence,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.roles import RoleMapping
from aota_forge.core.execution.state import CanonicalTaskState

DIR = "/tmp/af56-m2-w1/test-root"
SES = "ses_aaaaaaaaaaaaaaaaaaaaaaaaaa"
PARENT = "ses_bbbbbbbbbbbbbbbbbbbbbbbbbb"


class FakeTransport:
    """Scripted OpenCodeHttpTransport double (records exact requests)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.queue: list[Any] = []
        self.stream_raises: Exception | None = None

    def push(self, response: Any) -> None:
        self.queue.append(response)

    def _next(self) -> OpenCodeHttpResponse:
        if not self.queue:
            raise AssertionError("FakeTransport has no queued response")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(
        self, method: str, url: str, *, body: bytes | None = None, timeout: float
    ) -> OpenCodeHttpResponse:
        parsed_body = json.loads(body.decode("utf-8")) if body else None
        self.requests.append(
            {"method": method, "url": url, "body": parsed_body, "timeout": timeout}
        )
        return self._next()

    def stream_events(self, url: str, *, timeout: float, max_events: int) -> list[dict[str, Any]]:
        self.requests.append(
            {"method": "SSE", "url": url, "body": None, "timeout": timeout, "max_events": max_events}
        )
        if self.stream_raises is not None:
            raise self.stream_raises
        return list(self.events)


def _json_response(status: int, payload: Any) -> OpenCodeHttpResponse:
    return OpenCodeHttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def _session_payload(
    session_id: str = SES,
    directory: str = DIR,
    parent_id: str | None = PARENT,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": session_id, "directory": directory}
    if parent_id is not None:
        payload["parentID"] = parent_id
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def _package(
    *,
    task_id: str = "aota_forge:m2:wi-a:01234567:abcdef12",
    role: str = "coder",
    instruction: str = "do the bounded thing",
    capability_requirements: dict[str, Any] | None = None,
    operation: str = "task_dispatch",
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role=role,
        instruction=instruction,
        operation=operation,
        capability_requirements=capability_requirements or {},
    )


def _adapter(
    transport: FakeTransport,
    *,
    directory: str | None = DIR,
    parent: str | None = PARENT,
    capabilities: Any | None = None,
    composer: Any | None = None,
    resolver: Any | None = None,
) -> OpenCodeAdapter:
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    mapping = RoleMapping.create(
        OPENCODE_EXECUTOR_ID,
        {"planner": "analyst-host", "coder": "coder-host", "reviewer": "reviewer-host", "steward": "steward-host"},
    )
    return OpenCodeAdapter(
        host_client=client,
        capabilities=capabilities,
        role_mapping=mapping,
        model_prompt_composer=composer,
        worker_directory_resolver=(lambda pkg: directory) if resolver is None and directory else resolver,
        trusted_worker_directory=directory if resolver is None else None,
        origin_session_ref=parent,
    )


# ---------------------------------------------------------------------------
# Host client mechanics
# ---------------------------------------------------------------------------


def test_create_session_exact_endpoint_and_directory_scope() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload()))
    client = OpenCodeHostClient("http://127.0.0.1:4096/", http_transport=transport)
    session = client.create_session(directory=DIR, parent_id=PARENT, title="af-worker:x")
    assert session["id"] == SES
    req = transport.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "http://127.0.0.1:4096/session?directory=%2Ftmp%2Faf56-m2-w1%2Ftest-root"
    assert req["body"]["parentID"] == PARENT
    assert req["body"]["title"] == "af-worker:x"


def test_directory_is_required_and_must_be_absolute() -> None:
    transport = FakeTransport()
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    with pytest.raises(ValueError):
        client.create_session(directory="relative/path")
    with pytest.raises(ValueError):
        client.query_status(directory="")


def test_prompt_async_dispatch_rejected_and_not_found() -> None:
    transport = FakeTransport()
    transport.push(OpenCodeHttpResponse(status=409, body=b"busy-rejected"))
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    with pytest.raises(OpenCodeDispatchRejectedError):
        client.submit_prompt_async(SES, directory=DIR, parts=[{"type": "text", "text": "x"}])

    transport.push(OpenCodeHttpResponse(status=404, body=b"not found"))
    with pytest.raises(OpenCodeSessionNotFoundError):
        client.get_session(SES)
    transport.push(OpenCodeHttpResponse(status=404, body=b"not found"))
    with pytest.raises(OpenCodeSessionNotFoundError):
        client.submit_prompt_async(SES, directory=DIR, parts=[{"type": "text", "text": "x"}])


def test_typed_host_unavailable_timeout_retriable_protocol() -> None:
    transport = FakeTransport()
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    transport.push(OpenCodeHostUnavailableError("refused"))
    with pytest.raises(OpenCodeHostUnavailableError):
        client.query_status(directory=DIR)
    transport.push(OpenCodeTimeoutError("slow"))
    with pytest.raises(OpenCodeTimeoutError):
        client.query_status(directory=DIR)
    transport.push(OpenCodeHttpResponse(status=503, body=b"later"))
    with pytest.raises(OpenCodeRetriableHostError):
        client.query_status(directory=DIR)
    transport.push(OpenCodeHttpResponse(status=418, body=b"teapot"))
    with pytest.raises(OpenCodeProtocolViolationError):
        client.query_status(directory=DIR)


def test_malformed_response_fails_closed() -> None:
    transport = FakeTransport()
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    transport.push(OpenCodeHttpResponse(status=200, body=b"not-json"))
    with pytest.raises(OpenCodeMalformedResponseError):
        client.create_session(directory=DIR)
    transport.push(_json_response(200, {"directory": DIR}))  # missing id
    with pytest.raises(OpenCodeMalformedResponseError):
        client.create_session(directory=DIR)
    transport.push(_json_response(200, {"id": "not-a-session", "directory": DIR}))
    with pytest.raises(OpenCodeMalformedResponseError):
        client.create_session(directory=DIR)
    transport.push(_json_response(200, {"id": SES}))  # missing directory
    with pytest.raises(OpenCodeMalformedResponseError):
        client.create_session(directory=DIR)
    transport.push(_json_response(200, {"status": {"x": "not-an-object"}}))  # malformed status map
    with pytest.raises((OpenCodeMalformedResponseError, OpenCodeProtocolViolationError)):
        client.query_status(directory=DIR)


def test_parse_session_info_rejects_drift() -> None:
    with pytest.raises(OpenCodeMalformedResponseError):
        parse_session_info(["not", "a", "mapping"])


def test_status_map_absent_means_idle_observation() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, {}))
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    assert client.session_status(SES, directory=DIR) is None


# ---------------------------------------------------------------------------
# ExecutorAdapter: dispatch
# ---------------------------------------------------------------------------


def test_dispatch_creates_session_and_handle_is_session_id() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload()))
    transport.push(OpenCodeHttpResponse(status=204, body=b""))
    adapter = _adapter(transport)
    result = adapter.dispatch(_package())
    assert result.adapter_handle == SES
    assert result.initial_state is CanonicalTaskState.RUNNING
    create_req, prompt_req = transport.requests
    assert create_req["url"].startswith("http://127.0.0.1:4096/session?directory=")
    assert create_req["body"]["parentID"] == PARENT
    assert create_req["body"]["metadata"]["aota_canonical_task_id"] == _package().canonical_task_id
    assert prompt_req["url"].startswith(
        f"http://127.0.0.1:4096/session/{SES}/prompt_async?directory="
    )
    assert prompt_req["body"]["parts"][0]["type"] == "text"
    assert "aota_aota_invoke" in prompt_req["body"]["parts"][0]["text"]


def test_dispatch_parent_id_absent_keeps_identity_valid() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload(parent_id=None)))
    transport.push(OpenCodeHttpResponse(status=204, body=b""))
    adapter = _adapter(transport, parent=None)
    result = adapter.dispatch(_package())
    assert result.adapter_handle == SES
    assert "parentID" not in transport.requests[0]["body"]


def test_dispatch_without_trusted_directory_fails_closed() -> None:
    transport = FakeTransport()
    adapter = OpenCodeAdapter(
        host_client=OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport),
        role_mapping=RoleMapping.create(OPENCODE_EXECUTOR_ID, {"coder": "coder-host"}),
        worker_directory_resolver=lambda pkg: None,
    )
    with pytest.raises(OpenCodeWorkerDirectoryUnavailableError):
        adapter.dispatch(_package())
    assert transport.requests == []


def test_dispatch_malformed_create_response_fails_closed() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, {"id": "ses_x"}))  # no directory
    adapter = _adapter(transport)
    with pytest.raises(OpenCodeDispatchFailureError):
        adapter.dispatch(_package())


def test_dispatch_prompt_rejection_fails_closed() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload()))
    transport.push(OpenCodeHttpResponse(status=409, body=b"rejected"))
    adapter = _adapter(transport)
    with pytest.raises(OpenCodeDispatchFailureError):
        adapter.dispatch(_package())


def test_dispatch_idempotent_replay_without_second_host_call() -> None:
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload()))
    transport.push(OpenCodeHttpResponse(status=204, body=b""))
    adapter = _adapter(transport)
    package = _package()
    first = adapter.dispatch(package)
    second = adapter.dispatch(package)
    assert first == second
    assert len(transport.requests) == 2  # no second physical dispatch
    conflicting = _package(instruction="different intent")
    conflicting = ExecutionPackage.create(
        canonical_task_id=conflicting.canonical_task_id,
        project_id=conflicting.project_id,
        canonical_role=conflicting.canonical_role,
        instruction="different intent",
        idempotency_key=package.idempotency_key,
    )
    with pytest.raises(OpenCodeAdapterError):
        adapter.dispatch(conflicting)


def test_role_mapping_fails_closed_for_unmapped_role() -> None:
    transport = FakeTransport()
    adapter = OpenCodeAdapter(
        host_client=OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport),
        role_mapping=RoleMapping.create(OPENCODE_EXECUTOR_ID, {"coder": "coder-host"}),
        trusted_worker_directory=DIR,
    )
    validation = adapter.validate_package(_package(role="reviewer"))
    assert not validation.valid
    assert any("ROLE_MAPPING_NOT_FOUND" in error for error in validation.errors)


def test_unsupported_capability_requirements_rejected() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    for key, value in (
        ("requires_resume", True),
        ("requires_structured_result", True),
        ("requires_streaming_events", True),
        ("requires_artifact_transport", True),
        ("execution_mode", "sync"),
        ("isolation", "container"),
    ):
        validation = adapter.validate_package(_package(capability_requirements={key: value}))
        assert not validation.valid, key
    assert adapter.validate_package(_package()).valid


def test_resume_operation_rejected_by_validation() -> None:
    adapter = _adapter(FakeTransport())
    validation = adapter.validate_package(_package(operation="task_resume"))
    assert not validation.valid
    assert any("RESUME_UNSUPPORTED" in error for error in validation.errors)


def test_capabilities_are_truthful() -> None:
    caps = default_opencode_capabilities()
    assert caps.executor_id == "opencode"
    assert caps.adapter_kind == "opencode_host_adapter"
    assert caps.supports_task_resume is False
    assert caps.supports_streaming_events is False
    assert caps.supports_structured_result is False
    assert caps.supports_artifact_transport is False
    assert caps.supports_task_cancellation is True
    assert caps.supports_working_directory is True


def test_resume_fails_closed_truthfully() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    resume_package = ExecutionPackage.create(
        canonical_task_id=_package().canonical_task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="continue",
        operation="task_resume",
    )
    with pytest.raises(OpenCodeResumeUnsupportedError) as info:
        adapter.resume(_package().canonical_task_id, SES, resume_package)
    assert info.value.code == "RESUME_UNSUPPORTED"


# ---------------------------------------------------------------------------
# ExecutorAdapter: status / result
# ---------------------------------------------------------------------------


def _dispatch_for_observation(transport: FakeTransport, parent: str | None = PARENT) -> OpenCodeAdapter:
    adapter = _adapter(transport, parent=parent)
    transport.push(_json_response(200, _session_payload()))
    transport.push(OpenCodeHttpResponse(status=204, body=b""))
    adapter.dispatch(_package())
    return adapter


def test_status_busy_maps_running() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {SES: {"type": "busy"}}))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.RUNNING


def test_status_retry_maps_waiting_nonterminal() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {SES: {"type": "retry", "attempt": 1}}))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.WAITING


def test_status_idle_without_terminal_turn_never_completes() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {}))
    transport.push(
        _json_response(
            200,
            [
                {"info": {"role": "user", "id": "msg_u1"}, "parts": [{"type": "text", "text": "do it"}]},
                {"info": {"role": "assistant", "id": "msg_a1", "finish": None}, "parts": [{"type": "text", "text": "working"}]},
            ],
        )
    )
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.UNKNOWN


def test_status_idle_without_any_assistant_stays_unknown() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {}))
    transport.push(_json_response(200, []))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.UNKNOWN


def test_status_unknown_host_state_maps_unknown() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {SES: {"type": "quantum-flux"}}))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.UNKNOWN


def test_status_host_unavailable_maps_unknown_not_failure() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(OpenCodeHostUnavailableError("refused"))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.UNKNOWN


def test_status_missing_exact_session_stays_unknown_fail_closed() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    adapter._session_directories.pop(SES, None)
    transport.push(OpenCodeHttpResponse(status=404, body=b"not found"))
    status = adapter.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.UNKNOWN
    assert "SESSION_NOT_FOUND" in status.details


def _terminal_messages() -> list[dict[str, Any]]:
    return [
        {"info": {"role": "user", "id": "msg_u1"}, "parts": [{"type": "text", "text": "do it"}]},
        {"info": {"role": "assistant", "id": "msg_a1", "finish": "stop"}, "parts": [{"type": "text", "text": "final answer"}]},
    ]


def test_result_terminal_turn_projects_mechanical_success_without_text_authority() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {}))
    transport.push(_json_response(200, _terminal_messages()))
    result = adapter.result(_package().canonical_task_id, SES)
    assert result.ok is True
    assert result.status == "completed"
    assert result.executor_id == "opencode"
    assert result.result_data["mechanical_execution"]["turn_terminated"] is True
    assert "final answer" not in json.dumps(result.to_dict())


def test_result_never_projects_success_from_idle_alone() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {}))
    transport.push(_json_response(200, [{"info": {"role": "user"}, "parts": []}]))
    result = adapter.result(_package().canonical_task_id, SES)
    assert result.ok is False
    assert result.status == "unknown"
    assert result.canonical_task_state == CanonicalTaskState.UNKNOWN.value


def test_result_busy_stays_nonterminal() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {SES: {"type": "busy"}}))
    result = adapter.result(_package().canonical_task_id, SES)
    assert result.ok is False
    assert result.error is not None and result.error["code"] == "TASK_STILL_RUNNING"


def test_result_aborted_maps_cancelled() -> None:
    transport = FakeTransport()
    adapter = _dispatch_for_observation(transport)
    transport.push(_json_response(200, {}))
    transport.push(
        _json_response(
            200,
            [
                {
                    "info": {
                        "role": "assistant",
                        "finish": None,
                        "error": {"name": "MessageAbortedError", "data": {}},
                    },
                    "parts": [],
                }
            ],
        )
    )
    result = adapter.result(_package().canonical_task_id, SES)
    assert result.status == "cancelled"
    assert result.canonical_task_state == CanonicalTaskState.CANCELLED.value


# ---------------------------------------------------------------------------
# Restart-safe binding / no heuristics
# ---------------------------------------------------------------------------


def test_binding_recovers_from_exact_host_session_metadata_after_restart() -> None:
    transport = FakeTransport()
    adapter = _adapter(transport)
    # Fresh adapter object (simulated process restart): no process-local maps.
    fresh = _adapter(transport)
    transport.push(
        _json_response(
            200,
            _session_payload(
                metadata={"aota_canonical_task_id": _package().canonical_task_id}
            ),
        )
    )
    transport.push(_json_response(200, {SES: {"type": "busy"}}))
    status = fresh.status(_package().canonical_task_id, SES)
    assert status.state is CanonicalTaskState.RUNNING  # recovered exact binding, observed


def test_binding_recovery_rejects_metadata_mismatch() -> None:
    transport = FakeTransport()
    fresh = _adapter(transport)
    transport.push(
        _json_response(200, _session_payload(metadata={"aota_canonical_task_id": "other-task"}))
    )
    with pytest.raises(OpenCodeAdapterError) as info:
        fresh.status("task-not-bound", SES)
    assert info.value.code == "TASK_HANDLE_NOT_FOUND"


def test_wrong_directory_never_selects_another_session_structurally() -> None:
    # The client has no session-listing/selection API at all: exact ids are the
    # only addressing mode, and a wrong directory query still returns the same
    # exact session row (row wins) rather than another session.
    transport = FakeTransport()
    transport.push(_json_response(200, _session_payload()))
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    session = client.get_session(SES, directory="/tmp/wrong-directory")
    assert session["id"] == SES
    assert session["directory"] == DIR
    assert not hasattr(client, "latest_session")
    assert not hasattr(client, "list_sessions")


def test_turn_evidence_helpers() -> None:
    evidence = turn_evidence(_terminal_messages())
    assert evidence.terminal_success
    assert evidence.last_assistant_finish == "stop"
    assert evidence.assistant_text == "final answer"


def test_host_payload_carries_trusted_working_context_verbatim() -> None:
    package = _package()
    payload = build_opencode_host_payload(package)
    assert payload["context"]["working_context"] == dict(package.working_context)
    assert payload["context"]["canonical_task_id"] == package.canonical_task_id
