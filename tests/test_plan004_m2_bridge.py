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

from aota_forge.session_bridge import (
    BRIDGE_CARRIER_KEY,
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
