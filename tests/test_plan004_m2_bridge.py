"""Plan #004 M2 trusted bridge and one-time credential proofs."""

from __future__ import annotations

import os
import json
import multiprocessing
import subprocess
import sys
import threading
import socket
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import InputSizeError
from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map
from aota_forge.core.contracts.validation import (
    LARGE_TEXT_INPUT_BOUNDS,
    MAX_TOTAL_INPUT_BYTES,
    validate_inputs,
)
from aota_forge.core.plan.read_model import MAX_PLAN_BYTES
from aota_forge.session_bridge import (
    BRIDGE_CANONICAL_INPUT_MAX_BYTES,
    BRIDGE_CARRIER_KEY,
    BRIDGE_ENVELOPE_FIXED_MAX_BYTES,
    BRIDGE_IDENTITY_MAX_CHARS,
    BRIDGE_JSON_STRING_MAX_BYTES_PER_CHAR,
    BRIDGE_OPERATION_NAME_MAX_BYTES,
    BRIDGE_SOCKET_MAX_BYTES,
    BRIDGE_TOKEN_MAX_BYTES,
    BridgeSocketClient,
    BridgeVerificationError,
    InvocationBridge,
    extract_carrier,
)


LOGICAL = {"operation": "workspace.search", "arguments": {"query": "needle"}}


def _authority(now: list[float] | None = None) -> InvocationBridge:
    clock = (lambda: now[0]) if now is not None else (lambda: 1_000.0)
    return InvocationBridge(secret=b"plan004-test-secret" * 2, bridge_id="bridge-test-1", clock=clock)


def _persist_projection_in_process(path: str, session: str, barrier) -> None:
    from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
    from aota_forge.runtime.task_main.context_binding import (
        DurableTaskMainContextBinding,
        FileBackedTaskMainContextBindingProjection,
    )

    binding = DurableTaskMainContextBinding(
        project_id="project-a",
        project_fingerprint="b" * 64,
        plan_binding=PlanAuthorityBinding(
            plan_id="plan_alpha",
            source_kind="local_governance",
            authority_ref="local-governance:project-a/plan_alpha",
            source_revision="r1",
            source_digest="a" * 64,
        ),
        origin_task_main_session_ref=session,
        authorized_roots_digest="c" * 64,
    )
    barrier.wait()
    FileBackedTaskMainContextBindingProjection(path).persist(binding)


@contextmanager
def _bridge_process(tmp_path: Path):
    socket_path = tmp_path / "bridge.sock"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1]), "/usr/lib/python3/dist-packages"]
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "aota_forge.session_bridge", "--socket", str(socket_path)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            threading.Event().wait(0.01)
        yield BridgeSocketClient(socket_path)
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_token_binds_exact_invocation_and_denies_tamper_call_session_and_arguments() -> None:
    authority = _authority()
    token = authority.issue("ses-A", "call-A", LOGICAL["operation"], LOGICAL["arguments"])

    assert authority.verify(token, LOGICAL["operation"], LOGICAL["arguments"]).session_id == "ses-A"
    with pytest.raises(BridgeVerificationError, match="already consumed"):
        authority.verify(token, LOGICAL["operation"], LOGICAL["arguments"])

    token = authority.issue("ses-A", "call-B", LOGICAL["operation"], LOGICAL["arguments"])
    with pytest.raises(BridgeVerificationError, match="call differs"):
        authority.verify(
            token,
            LOGICAL["operation"],
            LOGICAL["arguments"],
            expected_session_id="ses-A",
            expected_call_id="call-A",
        )
    with pytest.raises(BridgeVerificationError, match="session differs"):
        authority.verify(
            token,
            LOGICAL["operation"],
            LOGICAL["arguments"],
            expected_session_id="ses-B",
        )
    with pytest.raises(BridgeVerificationError, match="arguments differ"):
        authority.verify(token, LOGICAL["operation"], {"query": "tampered"})

    prefix, encoded_payload, encoded_mac = token.split(".")
    changed_payload = encoded_payload[:4] + ("A" if encoded_payload[4] != "A" else "B") + encoded_payload[5:]
    forged = ".".join((prefix, changed_payload, encoded_mac))
    with pytest.raises(BridgeVerificationError, match="MAC is invalid"):
        authority.verify(forged, LOGICAL["operation"], LOGICAL["arguments"])


def test_token_expiry_epoch_rotation_and_carrier_extraction_fail_closed() -> None:
    now = [1_000.0]
    authority = _authority(now)
    token = authority.issue("ses-A", "call-A", LOGICAL["operation"], LOGICAL["arguments"])
    now[0] = 1_013.0
    with pytest.raises(BridgeVerificationError, match="expired"):
        authority.verify(token, LOGICAL["operation"], LOGICAL["arguments"])

    now[0] = 1_000.0
    old = authority.issue("ses-A", "call-old", LOGICAL["operation"], LOGICAL["arguments"])
    authority.rotate()
    with pytest.raises(BridgeVerificationError, match="MAC is invalid|stale bridge epoch"):
        authority.verify(old, LOGICAL["operation"], LOGICAL["arguments"])

    fresh = authority.issue("ses-A", "call-new", LOGICAL["operation"], LOGICAL["arguments"])
    logical, extracted = extract_carrier(
        {"query": "needle", BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": fresh}}
    )
    assert logical == {"query": "needle"}
    assert extracted == fresh
    with pytest.raises(BridgeVerificationError, match="required"):
        extract_carrier({"query": "needle"})
    with pytest.raises(BridgeVerificationError, match="fields"):
        extract_carrier({BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": fresh, "extra": 1}})


def test_atomic_nonce_consumption_has_one_winner_under_replay_race() -> None:
    authority = _authority()
    token = authority.issue("ses-A", "call-race", LOGICAL["operation"], LOGICAL["arguments"])
    barrier = threading.Barrier(16)

    def attempt() -> bool:
        barrier.wait()
        try:
            authority.verify(token, LOGICAL["operation"], LOGICAL["arguments"])
            return True
        except BridgeVerificationError:
            return False

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _: attempt(), range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15


def test_unix_socket_bridge_roundtrip_and_restart_boundary(tmp_path: Path) -> None:
    with _bridge_process(tmp_path) as client:
        client.rotate()
        token = client.issue("ses-A", "call-A", LOGICAL["operation"], LOGICAL["arguments"])
        assert client.verify(token, LOGICAL["operation"], LOGICAL["arguments"]).session_id == "ses-A"
        with pytest.raises(BridgeVerificationError, match="already consumed"):
            client.verify(token, LOGICAL["operation"], LOGICAL["arguments"])
        old = client.issue("ses-A", "call-old", LOGICAL["operation"], LOGICAL["arguments"])
        client.rotate()
        with pytest.raises(BridgeVerificationError, match="MAC is invalid|stale bridge epoch"):
            client.verify(old, LOGICAL["operation"], LOGICAL["arguments"])


def test_session_router_interleaves_two_bound_sessions_without_global_dispatcher(tmp_path: Path) -> None:
    from aota_forge.core.ingress import get_execution_dispatcher, reset_execution_dispatcher
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext
    from test_plan003_m2_w2_same_session_bind import _add_plan, _world

    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    repo_root = Path(__file__).resolve().parents[1]
    from aota_forge.core.ingress import reset_execution_dispatcher

    reset_execution_dispatcher()

    with _bridge_process(tmp_path) as client:
        host_context = UnboundHostContext(
            repo_root=str(repo_root),
            registry_path=str(world["registry"]),
            runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
            governance_base=str(world["governance_base"]),
            governance_store_path=str(world["store"]),
            bridge_socket_path=client.socket_path,
            bind_global_dispatcher=False,
        )
        router = SessionScopedContextRouter(host_context)

        def call(
            session: str,
            call_id: str,
            operation: str,
            arguments: dict,
            target_router=None,
        ):
            token = client.issue(session, call_id, operation, arguments)
            return (target_router or router).invoke(
                operation,
                {**arguments, BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": token}},
            )

        assert call("ses-A", "inspect-A", "task_main.context", {"mode": "inspect"})["ok"] is True
        assert call("ses-B", "inspect-B", "task_main.context", {"mode": "inspect"})["ok"] is True

        def bind(session: str, call_id: str):
            return call(
                session,
                call_id,
                "task_main.context",
                {
                    "mode": "bind",
                    "project_selector": "project-a",
                    "plan_selector": "plan_alpha",
                },
            )

        def payload(result, session: str, call_id: str, target_router=None):
            if isinstance(result.get("payload"), dict):
                return result["payload"]
            hydration = result.get("hydration") or {}
            hydrated = call(
                session,
                call_id,
                "result.hydrate",
                hydration.get("arguments") or {},
                target_router=target_router,
            )
            content = (hydrated.get("payload") or {}).get("content")
            return json.loads(content) if isinstance(content, str) else hydrated.get("payload")

        with ThreadPoolExecutor(max_workers=2) as executor:
            bound_a, bound_b = executor.map(
                bind,
                ("ses-A", "ses-B"),
                ("bind-A", "bind-B"),
            )
        assert bound_a["ok"] is True, bound_a
        assert bound_b["ok"] is True, bound_b
        slot_a = router._slots["ses-A"]
        slot_b = router._slots["ses-B"]
        assert slot_a.execution_dispatcher is not None
        assert slot_b.execution_dispatcher is not None
        assert slot_a.execution_dispatcher is not slot_b.execution_dispatcher
        assert slot_a.execution_store is not None
        assert slot_b.execution_store is not None
        assert slot_a.execution_store._path != slot_b.execution_store._path
        assert slot_a.execution_store._path.parent.name == "execution-sessions"

        with ThreadPoolExecutor(max_workers=2) as executor:
            inspect_a, inspect_b = executor.map(
                lambda item: call(item[0], item[1], "task_main.context", {"mode": "inspect"}),
                (("ses-A", "inspect-A2"), ("ses-B", "inspect-B2")),
            )
        assert inspect_a["ok"] is True, inspect_a
        assert inspect_b["ok"] is True, inspect_b
        assert payload(inspect_a, "ses-A", "hydrate-A")["origin_session_ref"] == "ses-A"
        assert payload(inspect_b, "ses-B", "hydrate-B")["origin_session_ref"] == "ses-B"
        assert get_execution_dispatcher() is None
        records = json.loads(
            (Path(world["project_root"]) / ".aota" / "task-main-context-bindings.json").read_text(
                encoding="utf-8"
            )
        )["records"]
        assert {record["origin_task_main_session_ref"] for record in records} == {"ses-A", "ses-B"}
        router.close()
        resumed_router = SessionScopedContextRouter(host_context)
        resumed_token = client.issue("ses-A", "restart-A", "task_main.context", {"mode": "inspect"})
        resumed = resumed_router.invoke(
            "task_main.context",
            {"mode": "inspect", BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": resumed_token}},
        )
        assert resumed["ok"] is True
        assert payload(
            resumed,
            "ses-A",
            "restart-hydrate-A",
            target_router=resumed_router,
        )["origin_session_ref"] == "ses-A"
        resumed_router.close()
    reset_execution_dispatcher()


def test_session_router_rejects_untrusted_model_identity_without_a_carrier(tmp_path: Path) -> None:
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext

    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(
            UnboundHostContext(
                repo_root=str(Path(__file__).resolve().parents[1]),
                bridge_socket_path=client.socket_path,
                bind_global_dispatcher=False,
            )
        )
        denied = router.invoke("role.bootstrap", {})
        assert denied["ok"] is False
        assert denied["error"]["code"] == "TOKEN_MISSING"


def test_projection_lock_preserves_two_process_creates(tmp_path: Path) -> None:
    projection_path = tmp_path / ".aota" / "task-main-context-bindings.json"
    projection_path.parent.mkdir()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_persist_projection_in_process, args=(str(projection_path), session, barrier))
        for session in ("ses_proc_A", "ses_proc_B")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    assert all(process.exitcode == 0 for process in processes)

    from aota_forge.runtime.task_main.context_binding import FileBackedTaskMainContextBindingProjection

    projection = FileBackedTaskMainContextBindingProjection(projection_path)
    assert projection.get_for_session("ses_proc_A") is not None
    assert projection.get_for_session("ses_proc_B") is not None


def test_peer_credential_unavailability_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from aota_forge import session_bridge

    left, right = socket.socketpair()
    try:
        monkeypatch.delattr(session_bridge.socket, "SO_PEERCRED", raising=False)
        assert session_bridge._same_user_peer(left) is False
    finally:
        left.close()
        right.close()


def test_projection_lock_unavailability_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aota_forge.runtime.task_main import context_binding

    projection_path = tmp_path / ".aota" / "task-main-context-bindings.json"
    projection_path.parent.mkdir()
    projection = context_binding.FileBackedTaskMainContextBindingProjection(projection_path)
    monkeypatch.setattr(context_binding, "fcntl", None)
    with pytest.raises(context_binding.ContextBindingError, match="inter-process") as error:
        projection.get_for_session("ses_lock_probe")
    assert error.value.code == "CONTEXT_BINDING_PERSISTENCE_FAILED"


def _governance_plan_arguments(candidate_body: str) -> dict[str, object]:
    return {
        "mode": "amend",
        "project_id": "aota_forge",
        "plan_id": "plan_004_trusted_opencode_session_bridge_context_isolation",
        "candidate_body": candidate_body,
        "expected_plan_revision": 43,
        "semantic_decision_ref": "bridge-request-size-test",
        "idempotency_key": "bridge-request-size-test-idempotency",
        "correlation_id": "bridge-request-size-test-correlation",
    }


def _wire(payload: dict[str, object]) -> bytes:
    return (canonical_json(payload) + "\n").encode("utf-8")


def _issue_payload(
    arguments: dict[str, object],
    *,
    session_id: str = "ses-size-test",
    call_id: str = "call-size-test",
) -> dict[str, object]:
    return {
        "op": "issue",
        "session_id": session_id,
        "call_id": call_id,
        "operation": "governance.plan",
        "arguments": arguments,
    }


def _send_raw_frame(socket_path: str | Path, frame: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2.0)
        connection.connect(str(socket_path))
        try:
            connection.sendall(frame)
        except OSError:
            # The bounded daemon may close an oversized request while sendall
            # is still draining the test's deliberately gross frame.
            pass
        response = bytearray()
        while b"\n" not in response:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response)


def _read_error_response(raw: bytes) -> dict[str, object]:
    parsed = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
    assert parsed["ok"] is False
    return parsed["error"]


def test_bridge_request_bound_is_derived_from_canonical_input_and_protocol_envelopes() -> None:
    descriptors = load_operation_descriptor_map(discover_canonical_project_root())
    operation_names = tuple(descriptors)
    assert len(operation_names) == 43
    assert max(len(name.encode("utf-8")) for name in operation_names) == BRIDGE_OPERATION_NAME_MAX_BYTES

    assert MAX_TOTAL_INPUT_BYTES == MAX_PLAN_BYTES == 256 * 1024
    assert LARGE_TEXT_INPUT_BOUNDS[("governance.plan", "candidate_body")] == 256 * 1024
    assert BRIDGE_CANONICAL_INPUT_MAX_BYTES == MAX_TOTAL_INPUT_BYTES
    escaped_identity_bytes = len(canonical_json("\x01" * BRIDGE_IDENTITY_MAX_CHARS).encode("utf-8")) - len(b'""')
    assert escaped_identity_bytes == BRIDGE_IDENTITY_MAX_CHARS * BRIDGE_JSON_STRING_MAX_BYTES_PER_CHAR

    issue_skeleton = len(
        _wire({"op": "issue", "session_id": "", "call_id": "", "operation": "", "arguments": {}})
    )
    verify_skeleton = len(_wire({"op": "verify", "token": "", "operation": "", "arguments": {}}))
    issue_fixed = (
        issue_skeleton
        + 2 * BRIDGE_IDENTITY_MAX_CHARS * BRIDGE_JSON_STRING_MAX_BYTES_PER_CHAR
        + BRIDGE_OPERATION_NAME_MAX_BYTES
        - len(b"{}")
    )
    verify_fixed = (
        verify_skeleton
        + BRIDGE_TOKEN_MAX_BYTES
        + BRIDGE_OPERATION_NAME_MAX_BYTES
        - len(b"{}")
    )

    assert (issue_skeleton, verify_skeleton) == (74, 57)
    assert BRIDGE_ENVELOPE_FIXED_MAX_BYTES == max(issue_fixed, verify_fixed) == 6_248
    assert BRIDGE_SOCKET_MAX_BYTES == MAX_TOTAL_INPUT_BYTES + BRIDGE_ENVELOPE_FIXED_MAX_BYTES == 268_392


@pytest.fixture(scope="module")
def governance_plan_descriptor():
    return load_operation_descriptor_map(discover_canonical_project_root())["governance.plan"]


def _validate_governance_plan_args(descriptor, candidate_body: str) -> dict[str, object]:
    return validate_inputs(descriptor, _governance_plan_arguments(candidate_body))


def _governance_plan_args_for_frame_size(descriptor, target_size: int, label: str) -> dict[str, object]:
    empty_args = _validate_governance_plan_args(descriptor, "")
    session_id = f"ses-{label}"
    call_id = f"call-{label}"
    empty_frame_size = len(
        _wire(_issue_payload(empty_args, session_id=session_id, call_id=call_id))
    )
    return _validate_governance_plan_args(descriptor, "x" * (target_size - empty_frame_size))


def _roundtrip_large_bridge_request(client: BridgeSocketClient, arguments: dict[str, object], label: str) -> int:
    session_id = f"ses-{label}"
    call_id = f"call-{label}"
    request_size = len(_wire(_issue_payload(arguments, session_id=session_id, call_id=call_id)))
    token = client.issue(session_id, call_id, "governance.plan", arguments)
    verified = client.verify(token, "governance.plan", arguments)
    assert verified.session_id == session_id
    assert verified.call_id == call_id
    return request_size


def test_bridge_carries_requests_below_and_above_old_16k_boundary_and_37124_body(
    tmp_path: Path, governance_plan_descriptor
) -> None:
    old_bound = 16 * 1024
    below_args = _governance_plan_args_for_frame_size(governance_plan_descriptor, old_bound - 1, "below-old-bound")
    above_args = _governance_plan_args_for_frame_size(governance_plan_descriptor, old_bound + 1, "above-old-bound")
    large_args = _validate_governance_plan_args(governance_plan_descriptor, "x" * 37_124)

    below_size = len(
        _wire(_issue_payload(below_args, session_id="ses-below-old-bound", call_id="call-below-old-bound"))
    )
    above_size = len(
        _wire(_issue_payload(above_args, session_id="ses-above-old-bound", call_id="call-above-old-bound"))
    )
    large_size = len(_wire(_issue_payload(large_args, session_id="ses-37124-body", call_id="call-37124-body")))
    assert below_size == old_bound - 1
    assert above_size == old_bound + 1
    assert old_bound < large_size < BRIDGE_SOCKET_MAX_BYTES
    assert len(large_args["candidate_body"].encode("utf-8")) == 37_124

    with _bridge_process(tmp_path) as client:
        assert _roundtrip_large_bridge_request(client, below_args, "below-old-bound") == below_size
        assert _roundtrip_large_bridge_request(client, above_args, "above-old-bound") == above_size
        assert _roundtrip_large_bridge_request(client, large_args, "37124-body") == large_size


@pytest.mark.parametrize(
    "body",
    (
        "ASCII governance candidate " * 700,
        "繁體中文候選本文🙂" * 300,
        'quotes """ and backslashes \\\\ ' * 300,
        "line one\nline two\r\nline three\t" * 300,
        ("\x01\x02\x1f\\\"\n" * 700),
    ),
    ids=("ascii", "utf8-multibyte", "quotes-backslashes", "newlines", "json-escape-heavy"),
)
def test_bridge_encoding_adversarial_inputs_pass_real_aota_validation_and_roundtrip(
    tmp_path: Path,
    governance_plan_descriptor,
    body: str,
) -> None:
    arguments = _validate_governance_plan_args(governance_plan_descriptor, body)
    canonical_input_bytes = len(json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    bridge_request_bytes = len(_wire(_issue_payload(arguments)))
    assert canonical_input_bytes <= MAX_TOTAL_INPUT_BYTES
    assert bridge_request_bytes <= BRIDGE_SOCKET_MAX_BYTES

    with _bridge_process(tmp_path) as client:
        _roundtrip_large_bridge_request(client, arguments, "encoding")


def test_near_canonical_maximum_request_is_valid_and_bridge_roundtrips(
    tmp_path: Path, governance_plan_descriptor
) -> None:
    empty_args = _governance_plan_arguments("")
    empty_size = len(json.dumps(empty_args, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    candidate_body = "x" * (MAX_TOTAL_INPUT_BYTES - empty_size)
    arguments = _validate_governance_plan_args(governance_plan_descriptor, candidate_body)
    assert len(json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")) == MAX_TOTAL_INPUT_BYTES
    request_size = len(
        _wire(
            _issue_payload(
                arguments,
                session_id="ses-near-canonical-max",
                call_id="call-near-canonical-max",
            )
        )
    )
    assert request_size <= BRIDGE_SOCKET_MAX_BYTES

    with _bridge_process(tmp_path) as client:
        assert _roundtrip_large_bridge_request(client, arguments, "near-canonical-max") == request_size

    over_limit = _governance_plan_arguments(candidate_body + "x")
    with pytest.raises(InputSizeError, match="total input exceeds"):
        validate_inputs(governance_plan_descriptor, over_limit)


def test_exact_bridge_hard_bound_passes_and_hard_bound_plus_one_is_denied(tmp_path: Path) -> None:
    def payload_for_size(size: int) -> dict[str, object]:
        payload: dict[str, object] = {
            "op": "issue",
            "session_id": "ses-frame-boundary",
            "call_id": "call-frame-boundary",
            "operation": "workspace.search",
            "arguments": {"padding": ""},
        }
        base_size = len(_wire(payload))
        payload["arguments"]["padding"] = "x" * (size - base_size)  # type: ignore[index]
        assert len(_wire(payload)) == size
        return payload

    exact_payload = payload_for_size(BRIDGE_SOCKET_MAX_BYTES)
    over_payload = payload_for_size(BRIDGE_SOCKET_MAX_BYTES + 1)
    with _bridge_process(tmp_path) as client:
        exact_response = client._request(exact_payload)
        assert exact_response["ok"] is True
        over_error = _read_error_response(_send_raw_frame(client.socket_path, _wire(over_payload)))
        assert over_error["code"] == "BRIDGE_REQUEST_TOO_LARGE"


def test_grossly_oversized_malformed_and_truncated_frames_fail_closed(tmp_path: Path) -> None:
    with _bridge_process(tmp_path) as client:
        malformed = _read_error_response(_send_raw_frame(client.socket_path, b"{not-json}\n"))
        assert malformed["code"] == "BRIDGE_REQUEST_INVALID"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2.0)
            connection.connect(str(client.socket_path))
            connection.sendall(b'{"op":"issue"')
            connection.shutdown(socket.SHUT_WR)
            truncated_raw = bytearray()
            while b"\n" not in truncated_raw:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                truncated_raw.extend(chunk)
        truncated = _read_error_response(bytes(truncated_raw))
        assert truncated["code"] == "BRIDGE_REQUEST_INVALID"

        gross_payload: dict[str, object] = {
            "op": "issue",
            "session_id": "ses-gross-size",
            "call_id": "call-gross-size",
            "operation": "workspace.search",
            "arguments": {"padding": "x" * (BRIDGE_SOCKET_MAX_BYTES + 64 * 1024)},
        }
        gross_error = _read_error_response(_send_raw_frame(client.socket_path, _wire(gross_payload)))
        assert gross_error["code"] == "BRIDGE_REQUEST_TOO_LARGE"


def test_multiple_frames_on_one_connection_process_only_the_first_frame(tmp_path: Path) -> None:
    arguments = {"query": "one-frame"}
    first = {
        "op": "issue",
        "session_id": "ses-single-frame",
        "call_id": "call-single-frame",
        "operation": "workspace.search",
        "arguments": arguments,
    }
    second = {"op": "rotate"}
    with _bridge_process(tmp_path) as client:
        response = _send_raw_frame(client.socket_path, _wire(first) + _wire(second))
        assert response.count(b"\n") == 1
        token = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))["token"]
        identity = client.verify(token, "workspace.search", arguments)
        assert identity.session_id == "ses-single-frame"
        with pytest.raises(BridgeVerificationError, match="already consumed"):
            client.verify(token, "workspace.search", arguments)


def test_bridge_concurrent_sessions_keep_frames_calls_tokens_and_bodies_isolated(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    cases = (
        ("ses-concurrent-a", "call-concurrent-a", {"query": "A" * 20_000}),
        ("ses-concurrent-b", "call-concurrent-b", {"query": "B" * 20_000}),
    )
    with _bridge_process(tmp_path) as client:
        def issue_and_verify(case):
            session_id, call_id, arguments = case
            barrier.wait()
            token = client.issue(session_id, call_id, "workspace.search", arguments)
            return client.verify(token, "workspace.search", arguments)

        with ThreadPoolExecutor(max_workers=2) as executor:
            identities = list(executor.map(issue_and_verify, cases))

    assert [(item.session_id, item.call_id) for item in identities] == [
        ("ses-concurrent-a", "call-concurrent-a"),
        ("ses-concurrent-b", "call-concurrent-b"),
    ]
    assert len({item.nonce for item in identities}) == 2
    assert len({item.args_sha256 for item in identities}) == 2


def test_bridge_denies_unknown_tokens_hmac_tampering_substitution_and_replay(tmp_path: Path) -> None:
    arguments = {"query": "bridge security"}
    with _bridge_process(tmp_path) as client:
        token = client.issue("ses-security-a", "call-security-a", "workspace.search", arguments)
        prefix, payload, mac = token.split(".")
        changed_mac = ("A" if mac[0] != "A" else "B") + mac[1:]
        with pytest.raises(BridgeVerificationError, match="MAC is invalid"):
            client.verify(".".join((prefix, payload, changed_mac)), "workspace.search", arguments)

        with pytest.raises(BridgeVerificationError, match="MAC is invalid"):
            client.verify("aota-bridge-v1.unknown.AA", "workspace.search", arguments)

        with pytest.raises(BridgeVerificationError, match="arguments differ"):
            client.verify(token, "workspace.search", {"query": "substituted"})
        verified = client.verify(token, "workspace.search", arguments)
        assert verified.session_id == "ses-security-a"
        with pytest.raises(BridgeVerificationError, match="already consumed"):
            client.verify(token, "workspace.search", arguments)


def test_session_router_allows_unbound_role_bootstrap_without_granting_authority(tmp_path: Path) -> None:
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext

    session_id = "ses-w4-unbound-bootstrap"
    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(
            UnboundHostContext(
                repo_root=str(Path(__file__).resolve().parents[1]),
                bridge_socket_path=client.socket_path,
                bind_global_dispatcher=False,
            )
        )

        def call(operation: str, arguments: dict, call_id: str) -> dict:
            token = client.issue(session_id, call_id, operation, arguments)
            return router.invoke(
                operation,
                {
                    **arguments,
                    BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": token},
                },
            )

        def payload(result: dict, call_id: str) -> dict:
            if isinstance(result.get("payload"), dict):
                return result["payload"]
            hydration = result.get("hydration") or {}
            hydrated = call("result.hydrate", hydration.get("arguments") or {}, call_id)
            assert hydrated["ok"] is True, hydrated
            content = (hydrated.get("payload") or {}).get("content")
            return json.loads(content) if isinstance(content, str) else hydrated.get("payload")

        bootstrap = call("role.bootstrap", {}, "call-w4-bootstrap")
        assert bootstrap["ok"] is True, bootstrap
        bootstrap_payload = payload(bootstrap, "call-w4-bootstrap-hydrate")
        assert bootstrap_payload["ROLE"] == "task-main"
        assert bootstrap_payload["HOST_SESSION"] == "unbound"
        assert bootstrap_payload["ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT"] is False
        assert bootstrap_payload["ROLE_BOOTSTRAP_MECHANICAL_GATE"] is False
        assert bootstrap_payload["IS_AUTHORITY"] is False
        assert bootstrap_payload["SESSION_BINDING_PRESENT"] is False
        assert bootstrap_payload["SESSION_BINDING_REQUIRED"] is False
        assert bootstrap_payload["RUNTIME_CONTEXT"]["plan_binding"] == "none"
        assert bootstrap_payload["RUNTIME_CONTEXT"]["session_binding"] == "none"

        inspected = call("task_main.context", {"mode": "inspect"}, "call-w4-inspect")
        assert inspected["ok"] is True, inspected
        inspect_payload = payload(inspected, "call-w4-inspect-hydrate")
        assert inspect_payload["state"] == "unbound"
        assert inspect_payload["context"] is None
        assert inspect_payload["session_binding_is_authority"] is False
        assert inspect_payload["identity_is_authority"] is False
        assert inspect_payload["locator_is_authority"] is False

        help_result = call("help", {"operation": "role.bootstrap"}, "call-w4-help")
        operations = call("operations.list", {}, "call-w4-operations-list")
        skill = call("skill.open", {"ref": "aota-task-main-control@1.0.0"}, "call-w4-skill-open")
        assert help_result["ok"] is True, help_result
        assert operations["ok"] is True, operations
        assert skill["ok"] is True, skill

        unbound_project = call(
            "governance.project",
            {"mode": "inspect", "project_id": "project-a"},
            "call-w4-project-denied",
        )
        assert unbound_project["ok"] is False
        assert unbound_project["error"]["code"] == "AUTHORITY_DENIED"

        unbound_governance = call(
            "governance.plan",
            {
                "mode": "inspect",
                "project_id": "project-a",
                "plan_id": "plan_alpha",
            },
            "call-w4-governance-denied",
        )
        assert unbound_governance["ok"] is False
        assert unbound_governance["error"]["code"] == "AUTHORITY_DENIED"

        slot = router._slots[session_id]
        assert slot.adapter is not None
        assert slot.adapter._bound_delegate is None
        assert slot.adapter._bound_runtime is None
        router.close()
