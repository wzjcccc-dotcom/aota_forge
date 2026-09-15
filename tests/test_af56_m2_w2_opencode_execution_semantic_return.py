"""AF #56 M2/W2 — real dispatch path, status/result mapping, semantic return gate.

Exercises the EXISTING production composition + ExecutionDispatcher + durable
store + DurableCompletionCoordinator with the real OpenCode adapter over a
scripted fake host transport. Proves:

- operator RuntimeConfig selects OpenCode with no fallback to Hermes;
- dispatch -> durable record (executor_id=opencode, adapter_handle=session id);
- initial state is a truthful nonterminal state (RUNNING), never COMPLETED;
- busy -> RUNNING, retry -> WAITING, idle-without-terminal-turn -> UNKNOWN;
- matching terminal host turn -> mechanical terminal result only;
- host result without task.return -> semantic success REJECTED;
- host result + valid handoff + valid task.return -> semantic success;
- exact-session cancel with observed idle -> CANCELLED;
- unsupported resume fails truthfully through the dispatcher.
"""

from __future__ import annotations

import json
import pathlib
import stat
from typing import Any

import pytest

from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    InMemoryExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.runtime.config import (
    EXECUTOR_OPENCODE,
    RuntimeBinding,
    RuntimeConfig,
    RuntimeConfigError,
)
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.task_return_receipt import (
    WorktreeSemanticReturnEvidenceProvider,
    write_task_return_receipt,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

DIR = "/tmp/af56-m2-w2/worker"
PARENT = "ses_cccccccccccccccccccccccccc"
SES = "ses_dddddddddddddddddddddddddd"

WORKER_ROLES = ("analyst", "coder", "reviewer", "project-steward")


class FakeTransport:
    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.requests: list[dict[str, Any]] = []

    def push(self, item: Any) -> None:
        self.queue.append(item)

    def _next(self) -> Any:
        if not self.queue:
            raise AssertionError("FakeTransport has no queued response")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, method: str, url: str, *, body: bytes | None = None, timeout: float) -> Any:
        from aota_forge.adapters.opencode.host_client import OpenCodeHttpResponse

        self.requests.append(
            {"method": method, "url": url, "body": json.loads(body) if body else None}
        )
        item = self._next()
        if isinstance(item, OpenCodeHttpResponse):
            return item
        return OpenCodeHttpResponse(status=200, body=json.dumps(item).encode("utf-8"))

    def stream_events(self, url: str, *, timeout: float, max_events: int) -> list[dict[str, Any]]:
        return []


def _resp(status: int, payload: Any = None) -> Any:
    from aota_forge.adapters.opencode.host_client import OpenCodeHttpResponse

    return OpenCodeHttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def _stub_executable(tmp_path: pathlib.Path) -> str:
    exe = tmp_path / "opencode-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _opencode_config(tmp_path: pathlib.Path, **overrides: Any) -> RuntimeConfig:
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor=EXECUTOR_OPENCODE,
            profile=f"{role}-host-profile",
            provider="afstub",
            model="stub-model",
            concurrency=1,
            executable=_stub_executable(tmp_path),
        )
        for role in WORKER_ROLES
    ) + (
        RuntimeBinding(
            work_role="task-main",
            executor=EXECUTOR_OPENCODE,
            profile="task-main-host-profile",
            provider="afstub",
            model="stub-model",
            concurrency=1,
            executable=_stub_executable(tmp_path),
        ),
    )
    return RuntimeConfig(
        executor=EXECUTOR_OPENCODE,
        executable=_stub_executable(tmp_path),
        concurrency=1,
        provider="afstub",
        model="stub-model",
        bindings=bindings,
        host_endpoint="http://127.0.0.1:4096",
        **overrides,
    )


def _package(task_id: str = "aota_forge:m2:wi-a:01234567:abcdef12") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="bounded M2 worker instruction",
        working_context={"work_role": "coder"},
    )


def _session_payload(
    task_id: str, parent: str | None = PARENT, agent: str | None = "coder-host-profile"
) -> dict[str, Any]:
    # AF #58 M1: the pinned host echoes the persisted agent on the created
    # session row; every dispatch in this module is a coder-role dispatch.
    payload: dict[str, Any] = {
        "id": SES,
        "directory": DIR,
        "metadata": {"aota_canonical_task_id": task_id},
    }
    if parent is not None:
        payload["parentID"] = parent
    if agent is not None:
        payload["agent"] = agent
    return payload


def _terminal_messages(text: str = "worker final answer") -> list[dict[str, Any]]:
    return [
        {"info": {"role": "user", "id": "u1"}, "parts": [{"type": "text", "text": "do it"}]},
        {
            "info": {"role": "assistant", "id": "a1", "finish": "stop"},
            "parts": [{"type": "text", "text": text}],
        },
    ]


def _synthetic_sandbox(root: pathlib.Path, project_id: str = "aota_forge") -> Any:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-m2",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path=".aota/project.yaml",
        name=project_id,
        kind="test-synthetic",
        status="active",
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-m2",
        workspace_root=str(root),
        registry_fingerprint="0" * 64,
        listing_fingerprint="0" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, "wt-m2", root)


def _dispatcher_with_fake(
    tmp_path: pathlib.Path,
    transport: FakeTransport,
    *,
    store: InMemoryExecutionStateStore | None = None,
    config: RuntimeConfig | None = None,
) -> tuple[ExecutionDispatcher, Any]:
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient

    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    effective_config = config or _opencode_config(tmp_path)
    dispatcher = create_production_execution_dispatcher(
        runtime_config=effective_config,
        host_client=client,
        state_store=store,
        origin_session_ref=OriginSessionRef(PARENT),
        worker_env_resolver=None,
    )
    # Bounded test seam: explicit trusted worker directory for fake-host unit
    # tests (production uses the governed resolver). Never model supplied.
    adapter = dispatcher.registry.get("opencode")
    adapter._trusted_worker_directory = DIR
    return dispatcher, effective_config


# ---------------------------------------------------------------------------
# Composition selection / no fallback
# ---------------------------------------------------------------------------


def test_operator_config_selects_opencode_without_fallback(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport)
    caps = dispatcher.registry.get("opencode").capabilities()
    assert caps.executor_id == "opencode"
    assert caps.adapter_kind == "opencode_host_adapter"
    assert not dispatcher.registry.has("hermes")


def test_hermes_config_still_builds_hermes_adapter(tmp_path: pathlib.Path) -> None:
    from aota_forge.runtime.config import (
        EXECUTOR_HERMES,
        SHARED_WORKER_PROFILE,
        TASK_MAIN_PROFILE,
    )

    exe = _stub_executable(tmp_path)
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor=EXECUTOR_HERMES,
            profile=SHARED_WORKER_PROFILE,
            provider=None,
            model=None,
            concurrency=1,
            executable=exe,
            toolsets=("aota",),
        )
        for role in WORKER_ROLES
    ) + (
        RuntimeBinding(
            work_role="task-main",
            executor=EXECUTOR_HERMES,
            profile=TASK_MAIN_PROFILE,
            provider=None,
            model=None,
            concurrency=1,
            executable=exe,
        ),
    )
    config = RuntimeConfig(
        executor=EXECUTOR_HERMES,
        executable=exe,
        concurrency=1,
        provider=None,
        model=None,
        bindings=bindings,
    )
    dispatcher = create_production_execution_dispatcher(runtime_config=config)
    assert dispatcher.registry.has("hermes")
    assert not dispatcher.registry.has("opencode")


def test_hermes_completion_transport_rejects_opencode_config(tmp_path: pathlib.Path) -> None:
    with pytest.raises(RuntimeConfigError):
        create_hermes_completion_delivery_transport(runtime_config=_opencode_config(tmp_path))


def test_opencode_dispatch_without_trusted_directory_fails_closed(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, config = _dispatcher_with_fake(tmp_path, transport)
    adapter = dispatcher.registry.get("opencode")
    adapter._trusted_worker_directory = None
    with pytest.raises(Exception) as info:
        dispatcher.dispatch(_package())
    assert getattr(info.value, "code", None) == "WORKER_DIRECTORY_UNAVAILABLE"
    assert transport.requests == []  # no host call without directory authority


# ---------------------------------------------------------------------------
# Dispatch / status / result through the production dispatcher
# ---------------------------------------------------------------------------


def test_dispatch_records_durable_route_with_session_handle(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    store = InMemoryExecutionStateStore()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport, store=store)
    transport.push(_session_payload(_package().canonical_task_id))
    transport.push(_resp(204))
    result = dispatcher.dispatch(_package())
    assert result.adapter_handle == SES
    assert result.initial_state is CanonicalTaskState.RUNNING
    record = store.get(_package().canonical_task_id)
    assert record is not None
    assert record.executor_id == "opencode"
    assert record.adapter_handle == SES
    assert record.origin_session_ref is not None and record.origin_session_ref.value == PARENT
    assert record.canonical_task_state is CanonicalTaskState.RUNNING


def test_status_mapping_through_dispatcher(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    transport.push(_resp(200, {SES: {"type": "busy"}}))
    assert dispatcher.status(task_id).state is CanonicalTaskState.RUNNING
    transport.push(_resp(200, {SES: {"type": "retry"}}))
    assert dispatcher.status(task_id).state is CanonicalTaskState.WAITING
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _terminal_messages()))
    assert dispatcher.status(task_id).state is CanonicalTaskState.COMPLETED
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _terminal_messages()))
    result = dispatcher.result(task_id)
    assert result.ok is True
    assert result.canonical_task_state == CanonicalTaskState.COMPLETED.value


def test_idle_without_terminal_turn_stays_unknown(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    transport.push(_resp(200, {}))
    transport.push(
        _resp(
            200,
            [
                {"info": {"role": "assistant", "finish": None}, "parts": [{"type": "text", "text": "..."}]},
            ],
        )
    )
    assert dispatcher.status(task_id).state is CanonicalTaskState.UNKNOWN
    transport.push(_resp(200, {}))
    transport.push(_resp(200, [{"info": {"role": "assistant", "finish": None}, "parts": []}]))
    result = dispatcher.result(task_id)
    assert result.ok is False
    assert result.status == "unknown"


def test_cancel_exact_session_with_observed_idle(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    transport.push(_resp(200, _session_payload(task_id)))  # get_session existence check
    transport.push(_resp(200, True))  # abort
    transport.push(_resp(200, {}))  # status -> idle observed
    cancel_result = dispatcher.cancel(task_id, executor="opencode")
    assert cancel_result.cancelled is True
    assert cancel_result.state is CanonicalTaskState.CANCELLED


def test_resume_unsupported_through_dispatcher(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    dispatcher, _ = _dispatcher_with_fake(tmp_path, transport)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    resume_package = ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="continue",
        operation="task_resume",
    )
    with pytest.raises(Exception) as info:
        dispatcher.resume(task_id, resume_package)
    assert getattr(info.value, "code", None) == "RESUME_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Semantic-return gate (existing DurableCompletionCoordinator, reused)
# ---------------------------------------------------------------------------


def _governed_result(tmp_path: pathlib.Path, task_id: str) -> None:
    sandbox = _synthetic_sandbox(tmp_path)
    ref = handoff_write(
        mode="result",
        semantic={
            "summary": "bounded M2 worker completed the governed target",
            "work_done": "M2 proof",
        },
        caller_role="coder",
        sandbox=sandbox,
        task_id=task_id,
    )
    write_task_return_receipt(
        sandbox,
        canonical_task_id=task_id,
        result_ref=ref.ref,
        result_digest=ref.digest,
        status="completed",
    )


def test_host_result_without_task_return_rejected(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    store = InMemoryExecutionStateStore()
    dispatcher, config = _dispatcher_with_fake(tmp_path, transport, store=store)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    sandbox = _synthetic_sandbox(tmp_path)
    provider = WorktreeSemanticReturnEvidenceProvider(sandbox)
    coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=config,
        transport=None,
        semantic_return_provider=provider,
    )
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _terminal_messages()))
    report = coordinator.recover_once()
    assert report.observations[task_id] == "semantic_result_not_proven"
    record = store.get(task_id)
    assert record.canonical_task_state is CanonicalTaskState.FAILED
    assert record.terminal_result is not None and record.terminal_result.ok is False
    assert record.worker_result_card is not None  # truthful failure card is delivery-eligible


def test_host_result_with_valid_task_return_accepted(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    store = InMemoryExecutionStateStore()
    dispatcher, config = _dispatcher_with_fake(tmp_path, transport, store=store)
    task_id = _package().canonical_task_id
    transport.push(_session_payload(task_id))
    transport.push(_resp(204))
    dispatcher.dispatch(_package())
    _governed_result(tmp_path, task_id)
    sandbox = _synthetic_sandbox(tmp_path)
    provider = WorktreeSemanticReturnEvidenceProvider(sandbox)
    coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=config,
        transport=None,
        semantic_return_provider=provider,
    )
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _terminal_messages()))
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _terminal_messages()))
    report = coordinator.recover_once()
    assert report.observations[task_id] == "terminal_result_persisted"
    record = store.get(task_id)
    assert record.canonical_task_state is CanonicalTaskState.COMPLETED
    assert record.terminal_result is not None and record.terminal_result.ok is True
    assert record.worker_result_card is not None
    assert record.worker_result_card_digest is not None
