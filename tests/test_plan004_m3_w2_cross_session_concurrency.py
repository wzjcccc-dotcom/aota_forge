"""Bounded concurrency proofs for one shared headed AOTA MCP child."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest

from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.mcp_transport import (
    MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS,
    SessionScopedContextRouter,
    UnboundHostContext,
    _UnboundAotaAdapter,
    _build_shared_mcp_server,
)
from aota_forge.runtime.task_main.context_binding import (
    FileBackedTaskMainContextBindingProjection,
    context_binding_projection_path,
)
from aota_forge.session_bridge import (
    BRIDGE_CARRIER_KEY,
    BRIDGE_CARRIER_SCHEMA,
    BridgeSocketClient,
    BridgeVerificationError,
)

from test_af55_m1_w2_registration_lifecycle import _create_checkout
from test_af57_m3_w2_governance_intake import (
    PLAN_BODY,
    PROJECT_ID,
    _context as _governance_context_fixture,
)
from test_m4_7_durable_journal import _make_record
from test_plan003_m2_w2_same_session_bind import _add_plan, _inspect, _payload, _world
from test_plan004_m2_bridge import _bridge_process


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context(world, socket_path: str) -> UnboundHostContext:
    return UnboundHostContext(
        repo_root=str(Path(__file__).resolve().parents[1]),
        registry_path=str(world["registry"]),
        runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
        governance_base=str(world["governance_base"]),
        governance_store_path=str(world["store"]),
        bridge_socket_path=socket_path,
        bind_global_dispatcher=False,
    )


def _mcp_arguments(
    client: BridgeSocketClient,
    session_id: str,
    call_id: str,
    operation: str,
    arguments: dict,
) -> dict:
    token = client.issue(session_id, call_id, operation, arguments)
    return {
        "operation": operation,
        "arguments": {
            **arguments,
            BRIDGE_CARRIER_KEY: {"schema": BRIDGE_CARRIER_SCHEMA, "token": token},
        },
    }


async def _wait_for(event: threading.Event, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.002)
    assert event.is_set(), "timed out waiting for deterministic worker observation"


def test_fastmcp_handler_is_async_bounded_and_preserves_request_context() -> None:
    assert MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS == 2
    request_context = contextvars.ContextVar("aota_test_request_context", default="missing")
    guard = threading.Lock()
    release = threading.Event()
    two_started = threading.Event()
    active = 0
    peak = 0
    observed_context: list[str] = []

    def sync_operation(operation: str, arguments: dict | None):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            if active == MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS:
                two_started.set()
            observed_context.append(request_context.get())
        assert release.wait(timeout=3)
        with guard:
            active -= 1
        return {
            "ok": True,
            "operation": operation,
            "is_success": True,
            "output_mode": "inline",
            "inline_output": "{}",
        }

    server = _build_shared_mcp_server(sync_operation)
    tool = server._tool_manager.get_tool("aota.invoke")
    assert tool is not None and tool.is_async is True
    assert [item.name for item in server._tool_manager.list_tools()] == ["aota.invoke"]

    async def scenario() -> None:
        async def invoke(index: int):
            request_context.set(f"request-{index}")
            return await server._tool_manager.call_tool(
                "aota.invoke",
                {"operation": "probe", "arguments": {"index": index}},
            )

        tasks = [asyncio.create_task(invoke(index)) for index in range(5)]
        try:
            await _wait_for(two_started)
            ticks = 0
            for _ in range(10):
                ticks += 1
                await asyncio.sleep(0.002)
            assert ticks == 10, "the MCP event loop stopped while sync operations were running"
            with guard:
                assert active == 2
                assert peak == 2
        finally:
            release.set()
        results = await asyncio.gather(*tasks)
        assert all(result.isError is False for result in results)

    try:
        anyio.run(scenario)
    finally:
        release.set()
    assert sorted(observed_context) == [f"request-{index}" for index in range(5)]


def test_cross_session_overlap_and_queued_same_session_call_do_not_hold_router_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    session_a = "ses_concurrency_A"
    session_b = "ses_concurrency_B"
    call_a1 = "call-concurrency-A1"
    call_a2 = "call-concurrency-A2"
    call_b = "call-concurrency-B"
    a1_release = threading.Event()
    a1_entered = threading.Event()
    a2_verified = threading.Event()
    b_entered = threading.Event()
    records: dict[str, dict] = {}
    records_lock = threading.Lock()
    original_verify = BridgeSocketClient.verify
    original_execute = SessionScopedContextRouter._execute_in_slot

    def verify(client, token, operation, arguments):
        identity = original_verify(client, token, operation, arguments)
        if identity.call_id == call_a2:
            a2_verified.set()
        return identity

    def instrumented_execute(router, slot, identity, operation, arguments):
        if operation != "task_main.context":
            return original_execute(router, slot, identity, operation, arguments)
        call_hash = _hash(identity.call_id)
        record = {
            "session_hash": _hash(identity.session_id),
            "call_hash": call_hash,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "start_ns": time.monotonic_ns(),
        }
        with records_lock:
            records[call_hash] = record
        if identity.call_id == call_a1:
            a1_entered.set()
            assert a1_release.wait(timeout=4)
        elif identity.call_id == call_b:
            b_entered.set()
            time.sleep(0.06)
        result = original_execute(router, slot, identity, operation, arguments)
        record["end_ns"] = time.monotonic_ns()
        return result

    monkeypatch.setattr(BridgeSocketClient, "verify", verify)
    monkeypatch.setattr(SessionScopedContextRouter, "_execute_in_slot", instrumented_execute)

    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(_context(world, client.socket_path))
        server = _build_shared_mcp_server(router.invoke)

        async def scenario() -> None:
            tasks = []
            try:
                inspect_args = {"mode": "inspect"}
                first = _mcp_arguments(
                    client, session_a, call_a1, "task_main.context", inspect_args
                )
                second = _mcp_arguments(
                    client, session_a, call_a2, "task_main.context", inspect_args
                )
                peer = _mcp_arguments(
                    client, session_b, call_b, "task_main.context", inspect_args
                )
                tasks.append(asyncio.create_task(server._tool_manager.call_tool("aota.invoke", first)))
                await _wait_for(a1_entered)
                tasks.append(asyncio.create_task(server._tool_manager.call_tool("aota.invoke", second)))
                await _wait_for(a2_verified)
                tasks.append(asyncio.create_task(server._tool_manager.call_tool("aota.invoke", peer)))
                await _wait_for(b_entered)
                assert _hash(session_a) != _hash(session_b)
                assert _hash(call_a1) != _hash(call_b)
                assert _hash(call_a2) != _hash(call_a1)
                assert _hash(call_a2) not in records
            finally:
                a1_release.set()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run(scenario())
        finally:
            a1_release.set()
            router.close()

    a1 = records[_hash(call_a1)]
    a2 = records[_hash(call_a2)]
    b = records[_hash(call_b)]
    assert a1["end_ns"] <= a2["start_ns"]
    same_session_overlap_ns = max(
        0,
        min(a1["end_ns"], a2["end_ns"])
        - max(a1["start_ns"], a2["start_ns"]),
    )
    cross_session_overlap_ns = max(
        0,
        min(a1["end_ns"], b["end_ns"])
        - max(a1["start_ns"], b["start_ns"]),
    )
    assert same_session_overlap_ns == 0
    assert cross_session_overlap_ns > 0
    assert b["pid"] == a1["pid"] == a2["pid"]


def test_concurrent_project_bind_and_plan_promotion_keep_exact_session_contexts(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    _add_plan(world, "plan_beta")
    session_a = "ses_concurrent_bind_A"
    session_b = "ses_concurrent_bind_B"
    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(_context(world, client.socket_path))
        server = _build_shared_mcp_server(router.invoke)

        async def call(session: str, call_id: str, arguments: dict):
            request = _mcp_arguments(
                client, session, call_id, "task_main.context", arguments
            )
            return await server._tool_manager.call_tool("aota.invoke", request)

        async def scenario() -> None:
            bound = await asyncio.gather(
                call(session_a, "bind-project-A", {"mode": "bind", "project_selector": "project-a"}),
                call(session_b, "bind-project-B", {"mode": "bind", "project_selector": "project-a"}),
            )
            assert all(result.structuredContent["ok"] for result in bound)

            promoted = await asyncio.gather(
                call(
                    session_a,
                    "promote-plan-A",
                    {"mode": "bind", "project_selector": "project-a", "plan_selector": "plan_alpha"},
                ),
                call(
                    session_b,
                    "promote-plan-B",
                    {"mode": "bind", "project_selector": "project-a", "plan_selector": "plan_beta"},
                ),
            )
            assert all(result.structuredContent["ok"] for result in promoted)
            adapter_a = router._slots[session_a].adapter
            adapter_b = router._slots[session_b].adapter
            assert adapter_a is not None and adapter_b is not None
            assert _payload(adapter_a, _inspect(adapter_a))["context"]["plan_id"] == "plan_alpha"
            assert _payload(adapter_b, _inspect(adapter_b))["context"]["plan_id"] == "plan_beta"

        try:
            asyncio.run(scenario())
        finally:
            router.close()

    projection = FileBackedTaskMainContextBindingProjection(
        context_binding_projection_path(Path(world["project_root"]))
    )
    durable_a = projection.get_for_session(session_a)
    durable_b = projection.get_for_session(session_b)
    assert durable_a is not None and durable_b is not None
    assert durable_a.binding_kind == "PLAN_BOUND"
    assert durable_b.binding_kind == "PLAN_BOUND"
    assert durable_a.plan_binding.plan_id == "plan_alpha"
    assert durable_b.plan_binding.plan_id == "plan_beta"
    assert durable_a.compute_digest() == durable_a.binding_digest
    assert durable_b.compute_digest() == durable_b.binding_digest


def test_same_session_bind_gate_prevents_partial_delegate_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    session_a = "ses_bind_gate_A"
    bind_call_id = "call-bind-gate-A"
    inspect_call_id = "call-inspect-gate-A"
    bind_entered = threading.Event()
    release_bind = threading.Event()
    inspect_verified = threading.Event()
    original_bind = _UnboundAotaAdapter._bind_unbound_context
    original_verify = BridgeSocketClient.verify

    def delayed_bind(adapter, arguments, *, promotion=False):
        bind_entered.set()
        assert release_bind.wait(timeout=4)
        return original_bind(adapter, arguments, promotion=promotion)

    def verify(client, token, operation, arguments):
        identity = original_verify(client, token, operation, arguments)
        if identity.call_id == inspect_call_id:
            inspect_verified.set()
        return identity

    monkeypatch.setattr(_UnboundAotaAdapter, "_bind_unbound_context", delayed_bind)
    monkeypatch.setattr(BridgeSocketClient, "verify", verify)

    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(_context(world, client.socket_path))
        server = _build_shared_mcp_server(router.invoke)

        async def scenario() -> None:
            bind_request = _mcp_arguments(
                client,
                session_a,
                bind_call_id,
                "task_main.context",
                {"mode": "bind", "project_selector": "project-a"},
            )
            inspect_request = _mcp_arguments(
                client,
                session_a,
                inspect_call_id,
                "task_main.context",
                {"mode": "inspect"},
            )
            bind_task = asyncio.create_task(
                server._tool_manager.call_tool("aota.invoke", bind_request)
            )
            try:
                await _wait_for(bind_entered)
                inspect_task = asyncio.create_task(
                    server._tool_manager.call_tool("aota.invoke", inspect_request)
                )
                await _wait_for(inspect_verified)
                await asyncio.sleep(0.02)
                assert not inspect_task.done()
                release_bind.set()
                bind_result, inspect_result = await asyncio.gather(bind_task, inspect_task)
                assert bind_result.structuredContent["ok"]
                assert inspect_result.structuredContent["ok"]
            finally:
                release_bind.set()
                if not bind_task.done():
                    await asyncio.gather(bind_task, return_exceptions=True)

        try:
            asyncio.run(scenario())
            adapter = router._slots[session_a].adapter
            assert adapter is not None
            assert _payload(adapter, _inspect(adapter))["state"] == "project_bound"
        finally:
            release_bind.set()
            router.close()


def test_bridge_accepts_two_concurrent_tokens_once_and_rejects_replay_substitution(
    tmp_path: Path,
) -> None:
    operation = "task_main.context"
    args_a = {"mode": "inspect"}
    args_b = {"mode": "bind", "project_selector": "project-a"}
    with _bridge_process(tmp_path) as client:
        client.rotate()
        token_a = client.issue("ses_bridge_A", "call-bridge-A", operation, args_a)
        token_b = client.issue("ses_bridge_B", "call-bridge-B", operation, args_b)
        barrier = threading.Barrier(2)

        def verify(token: str, args: dict):
            barrier.wait(timeout=3)
            return client.verify(token, operation, args)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(verify, token_a, args_a)
            future_b = executor.submit(verify, token_b, args_b)
            identity_a = future_a.result(timeout=5)
            identity_b = future_b.result(timeout=5)
        assert identity_a.session_id == "ses_bridge_A"
        assert identity_b.session_id == "ses_bridge_B"
        with pytest.raises(BridgeVerificationError, match="already consumed"):
            client.verify(token_a, operation, args_a)
        with pytest.raises(BridgeVerificationError, match="arguments differ"):
            client.verify(token_a, operation, args_b)


def test_cancelled_mcp_request_waits_for_worker_completion() -> None:
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def operation(name: str, arguments: dict | None):
        entered.set()
        assert release.wait(timeout=3)
        completed.set()
        return {
            "ok": True,
            "operation": name,
            "is_success": True,
            "output_mode": "inline",
            "inline_output": "{}",
        }

    server = _build_shared_mcp_server(operation)

    async def scenario() -> None:
        async def cancel_after_entry(scope: anyio.CancelScope) -> None:
            await _wait_for(entered)
            scope.cancel()

        async def release_after_entry() -> None:
            await _wait_for(entered)
            await asyncio.sleep(0.08)
            release.set()

        with anyio.CancelScope() as scope:
            asyncio.create_task(cancel_after_entry(scope))
            asyncio.create_task(release_after_entry())
            result = await server._tool_manager.call_tool(
                "aota.invoke", {"operation": "probe", "arguments": {}}
            )
        assert scope.cancel_called
        assert completed.is_set()
        assert result.structuredContent["ok"] is True

    try:
        anyio.run(scenario)
    finally:
        release.set()


def test_timed_out_mcp_request_does_not_abandon_sync_mutation() -> None:
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def operation(name: str, arguments: dict | None):
        entered.set()
        assert release.wait(timeout=3)
        completed.set()
        return {
            "ok": True,
            "operation": name,
            "is_success": True,
            "output_mode": "inline",
            "inline_output": "{}",
        }

    server = _build_shared_mcp_server(operation)

    async def scenario() -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        async def release_after_worker_started() -> None:
            await _wait_for(entered)
            await asyncio.sleep(0.08)
            release.set()

        release_task = asyncio.create_task(release_after_worker_started())
        async with create_connected_server_and_client_session(server) as client:
            with anyio.move_on_after(0.02) as timeout_scope:
                await client.call_tool(
                    "aota.invoke", {"operation": "probe", "arguments": {}}
                )
        assert timeout_scope.cancel_called
        await release_task
        assert entered.is_set()
        assert completed.is_set()

    try:
        anyio.run(scenario)
    finally:
        release.set()


def test_process_shutdown_drains_a_non_abandoned_mcp_worker() -> None:
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def operation(name: str, arguments: dict | None):
        entered.set()
        assert release.wait(timeout=3)
        completed.set()
        return {
            "ok": True,
            "operation": name,
            "is_success": True,
            "output_mode": "inline",
            "inline_output": "{}",
        }

    server = _build_shared_mcp_server(operation)

    async def scenario() -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        async def release_after_worker_started() -> None:
            await _wait_for(entered)
            await asyncio.sleep(0.08)
            release.set()

        release_task = asyncio.create_task(release_after_worker_started())
        call_task = None
        try:
            async with create_connected_server_and_client_session(server) as client:
                call_task = asyncio.create_task(
                    client.call_tool(
                        "aota.invoke", {"operation": "probe", "arguments": {}}
                    )
                )
                await _wait_for(entered)
            # Closing the MCP server cancels its request task group. The
            # server's worker join must complete before lifespan teardown.
            assert completed.is_set()
        finally:
            release.set()
            if call_task is not None:
                if not call_task.done():
                    call_task.cancel()
                await asyncio.gather(call_task, return_exceptions=True)
            await release_task

    try:
        anyio.run(scenario)
    finally:
        release.set()
    assert completed.is_set()


def test_mcp_server_shutdown_drains_verified_session_slot_before_close(
    tmp_path: Path,
) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    world = _world(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    session_id = "ses_shutdown_gate"
    call_id = "call-shutdown-gate"

    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(_context(world, client.socket_path))
        original_execute = router._execute_in_slot

        def delayed_execute(slot, identity, operation, arguments):
            entered.set()
            assert release.wait(timeout=3)
            result = original_execute(slot, identity, operation, arguments)
            completed.set()
            return result

        router._execute_in_slot = delayed_execute
        server = _build_shared_mcp_server(router.invoke)

        async def scenario() -> None:
            async def release_after_entry() -> None:
                await _wait_for(entered)
                await asyncio.sleep(0.08)
                release.set()

            release_task = asyncio.create_task(release_after_entry())
            call_task = None
            try:
                async with create_connected_server_and_client_session(server) as client_session:
                    await client_session.list_tools()
                    logical = {"mode": "inspect"}
                    token = client.issue(
                        session_id, call_id, "task_main.context", logical
                    )
                    request = {
                        "operation": "task_main.context",
                        "arguments": {
                            **logical,
                            BRIDGE_CARRIER_KEY: {
                                "schema": BRIDGE_CARRIER_SCHEMA,
                                "token": token,
                            },
                        },
                    }
                    call_task = asyncio.create_task(
                        client_session.call_tool("aota.invoke", request)
                    )
                    await _wait_for(entered)
                assert completed.is_set()
                assert router._closed is True
                assert router._slots == {}
            finally:
                release.set()
                if call_task is not None:
                    if not call_task.done():
                        call_task.cancel()
                    await asyncio.gather(call_task, return_exceptions=True)
                await release_task

        try:
            anyio.run(scenario)
        finally:
            release.set()
            router.close()
    assert completed.is_set()


def test_file_backed_governance_journal_keeps_concurrent_instance_updates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "governance-journal.json"
    stores = [FileBackedDurableJournalStore(path), FileBackedDurableJournalStore(path)]
    barrier = threading.Barrier(2)

    def create(index: int) -> None:
        barrier.wait(timeout=3)
        stores[index].create_prepared(
            _make_record(
                journal_id=f"journal-cross-session-{index}",
                correlation_id=f"corr-cross-session-{index}",
                attempt_id=f"attempt-cross-session-{index}",
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create, index) for index in range(2)]
            for future in futures:
                future.result(timeout=5)
        reopened = FileBackedDurableJournalStore(path)
        assert {entry.record.journal_id for entry in reopened.list_all()} == {
            "journal-cross-session-0",
            "journal-cross-session-1",
        }
    finally:
        for store in stores:
            store.close()


def test_workspace_registry_concurrent_registration_has_no_lost_entry(
    tmp_path: Path,
) -> None:
    from aota_forge.core.project.lifecycle import register_project
    from aota_forge.core.project.resolver import load_workspace_registry

    registry = tmp_path / "workspace-registry.json"
    registry.write_text("{}\n", encoding="utf-8")
    roots = []
    for index in range(2):
        workspace = tmp_path / f"workspace-{index}"
        workspace.mkdir()
        _create_checkout(workspace, f"folder-{index}", f"project-{index}")
        roots.append(workspace)
    barrier = threading.Barrier(2)

    def register(index: int) -> None:
        barrier.wait(timeout=3)
        register_project(
            workspace_id=f"workspace-{index}",
            registry_path=registry,
            project_id=f"project-{index}",
            workspace_root=roots[index],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(register, index) for index in range(2)]
        for future in futures:
            future.result(timeout=5)
    assert load_workspace_registry(registry) == {
        f"workspace-{index}": {"candidates": [str(roots[index])]} for index in range(2)
    }


def test_concurrent_plan_intakes_preserve_journal_and_project_projection(
    tmp_path: Path,
) -> None:
    import json

    from aota_forge.governance.intake import GovernanceIntakeService
    from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

    context = _governance_context_fixture(tmp_path)
    GovernanceIntakeService(context, principal="concurrency-setup").project(
        "init", project_id=PROJECT_ID
    )
    plans = ("plan_cross_session_a", "plan_cross_session_b")
    bodies = {
        plan_id: PLAN_BODY.replace("PLAN_ID=plan_af57_m3_w2", f"PLAN_ID={plan_id}")
        for plan_id in plans
    }
    barrier = threading.Barrier(2)

    def initialize(plan_id: str) -> dict:
        service = GovernanceIntakeService(context, principal=f"writer-{plan_id}")
        barrier.wait(timeout=3)
        return service.plan(
            "init",
            project_id=PROJECT_ID,
            plan_id=plan_id,
            candidate_body=bodies[plan_id],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(initialize, plan_id) for plan_id in plans]
        results = [future.result(timeout=10) for future in futures]
    assert all(result["status"] == "VERIFIED" for result in results)

    project_root = Path(context.project_root)
    store = SQLiteProjectGovernanceStore(project_root / ".aota" / "governance.sqlite3")
    try:
        records = store.list_plans(PROJECT_ID)
    finally:
        store.close()
    assert {record.plan_id for record in records} >= set(plans)

    map_text = (Path(context.governance_base) / PROJECT_ID / "MAP.md").read_text()
    assert all(plan_id in map_text for plan_id in plans)
    journal = json.loads((project_root / ".aota" / "governance-journal.json").read_text())
    assert len(journal) == 2
