"""AF #56 M2/W3 — exact-session completion delivery & identity-bound ACK.

Covers: exact origin session delivery, exact directory scope, 204 != ACK,
wrong/missing session -> NOT_FOUND with no create-on-miss, temporary transport
errors conservative, the existing AF completion envelope unchanged, ACK
canonical_task_id/card_digest identity binding, and idempotent redelivery
reconciliation.
"""

from __future__ import annotations

import json
import pathlib
import stat
from typing import Any

import pytest

from aota_forge.adapters.opencode.delivery import OpenCodeCompletionDeliveryTransport
from aota_forge.adapters.opencode.host_client import OpenCodeHostClient, OpenCodeHttpResponse
from aota_forge.adapters.opencode.session_reentry import (
    OUTCOME_COMPLETED,
    OUTCOME_NOT_FOUND,
    OUTCOME_RETRYABLE,
    OpenCodeExactSessionReentry,
)
from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_opencode_completion_delivery_transport,
    create_production_execution_dispatcher,
)
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    InMemoryExecutionStateStore,
    OriginSessionRef,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.runtime.completion import (
    COMPLETION_ACK_TOKEN,
    COMPLETION_ENVELOPE_HEADER,
    DeliveryTransportOutcome,
    build_completion_envelope,
    parse_completion_ack,
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

PARENT = "ses_eeeeeeeeeeeeeeeeeeeeeeeeee"
WORKER = "ses_ffffffffffffffffffffffffff"
OTHER = "ses_99999999999999999999999999"
DIR = "/tmp/af56-m2-w3/parent-root"
WORKER_DIR = "/tmp/af56-m2-w3/worker-root"
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
        self.requests.append(
            {"method": method, "url": url, "body": json.loads(body) if body else None}
        )
        item = self._next()
        if isinstance(item, OpenCodeHttpResponse):
            return item
        return OpenCodeHttpResponse(status=200, body=json.dumps(item).encode("utf-8"))

    def stream_events(self, url: str, *, timeout: float, max_events: int) -> list[dict[str, Any]]:
        self.requests.append({"method": "SSE", "url": url, "body": None})
        return list(getattr(self, "events", []))


def _resp(status: int, payload: Any = None) -> OpenCodeHttpResponse:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return OpenCodeHttpResponse(status=status, body=body)


def _stub_executable(tmp_path: pathlib.Path) -> str:
    exe = tmp_path / "opencode-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _config(tmp_path: pathlib.Path) -> RuntimeConfig:
    exe = _stub_executable(tmp_path)
    bindings = tuple(
        RuntimeBinding(
            work_role=role,
            executor=EXECUTOR_OPENCODE,
            profile=f"{role}-host",
            provider="afstub",
            model="stub-model",
            concurrency=1,
            executable=exe,
        )
        for role in WORKER_ROLES
    ) + (
        RuntimeBinding(
            work_role="task-main",
            executor=EXECUTOR_OPENCODE,
            profile="task-main-host",
            provider="afstub",
            model="stub-model",
            concurrency=1,
            executable=exe,
        ),
    )
    return RuntimeConfig(
        executor=EXECUTOR_OPENCODE,
        executable=exe,
        concurrency=1,
        provider="afstub",
        model="stub-model",
        bindings=bindings,
        host_endpoint="http://127.0.0.1:4096",
    )


def _package(task_id: str = "aota_forge:m2:wi-a:01234567:abcdef12") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="bounded M2 W3 worker instruction",
    )


def _session_payload(session_id: str, directory: str, task_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": session_id, "directory": directory}
    if task_id is not None:
        payload["metadata"] = {"aota_canonical_task_id": task_id}
    return payload


def _worker_terminal_messages() -> list[dict[str, Any]]:
    return [
        {"info": {"role": "user", "id": "u1"}, "parts": [{"type": "text", "text": "work"}]},
        {"info": {"role": "assistant", "id": "a1", "finish": "stop"}, "parts": [{"type": "text", "text": "done"}]},
    ]


def _ack_text(task_id: str, digest: str) -> str:
    return f"{COMPLETION_ACK_TOKEN} canonical_task_id={task_id} card_digest={digest}"


def _assistant_message(text: str, message_id: str = "a2") -> dict[str, Any]:
    return {"info": {"role": "assistant", "id": message_id, "finish": "stop"}, "parts": [{"type": "text", "text": text}]}


def _synthetic_sandbox(root: pathlib.Path, project_id: str = "aota_forge") -> Any:
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-m2w3",
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
        workspace_id="ws-m2w3",
        workspace_root=str(root),
        registry_fingerprint="0" * 64,
        listing_fingerprint="0" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, "wt-m2w3", root)


def _reentry(transport: FakeTransport, **kwargs: Any) -> OpenCodeExactSessionReentry:
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    return OpenCodeExactSessionReentry(
        client, poll_interval_seconds=0.001, timeout_seconds=2.0, **kwargs
    )


# ---------------------------------------------------------------------------
# Reentry mechanics
# ---------------------------------------------------------------------------


def test_exact_origin_session_delivery_uses_row_directory() -> None:
    transport = FakeTransport()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, _worker_terminal_messages()))
    transport.push(_resp(204))
    transport.push(_resp(200, _worker_terminal_messages() + [_assistant_message("working")]))
    transport.push(_resp(200, {"type": "busy"}))
    transport.push(
        _resp(
            200,
            _worker_terminal_messages() + [_assistant_message("ack now\nAOTA_COMPLETION_ACK_V1 canonical_task_id=t card_digest=d")],
        )
    )
    result = _reentry(
        transport, default_model={"providerID": "afstub", "modelID": "stub-model"}
    ).reenter(PARENT, "AOTA_WORKER_COMPLETION_V1 ...")
    assert result.outcome == OUTCOME_COMPLETED
    assert result.accepted is True
    assert result.ack_observed is True
    assert PARENT in transport.requests[0]["url"]
    # Exact-session lookup is row-authoritative; every subsequent
    # directory-scoped call carries the exact session row directory scope.
    messages_req = next(r for r in transport.requests if "/message" in r["url"])
    assert "directory=%2Ftmp%2Faf56-m2-w3%2Fparent-root" in messages_req["url"]
    prompt_req = next(r for r in transport.requests if "prompt_async" in r["url"])
    assert f"/session/{PARENT}/prompt_async" in prompt_req["url"]
    assert "directory=" in prompt_req["url"]
    assert prompt_req["body"]["model"] == {"providerID": "afstub", "modelID": "stub-model"}


def test_missing_exact_session_is_not_found_and_creates_nothing() -> None:
    transport = FakeTransport()
    transport.push(_resp(404, {"name": "NotFound"}))
    result = _reentry(transport).reenter(PARENT, "envelope")
    assert result.outcome == OUTCOME_NOT_FOUND
    assert result.error_code == "SESSION_NOT_FOUND"
    # no create/session POST happened (only the exact GET)
    assert all(r["method"] != "POST" for r in transport.requests)
    assert transport.requests[0]["url"].endswith(f"/session/{PARENT}")


def test_wrong_session_never_selected_other_session() -> None:
    # Wrong session id -> exact 404; the transport NEVER falls back to another
    # session (no listing, no latest, no create-on-miss).
    transport = FakeTransport()
    transport.push(_resp(404))
    result = _reentry(transport).reenter(OTHER, "envelope")
    assert result.outcome == OUTCOME_NOT_FOUND
    assert transport.requests[0]["url"].endswith(f"/session/{OTHER}")
    assert not any("POST /session" in f"{r['method']} {r['url']}" for r in transport.requests)


def test_temporary_transport_error_is_retryable_conservative() -> None:
    transport = FakeTransport()
    from aota_forge.adapters.opencode.host_client import OpenCodeHostUnavailableError

    transport.push(OpenCodeHostUnavailableError("refused"))
    result = _reentry(transport).reenter(PARENT, "envelope")
    assert result.outcome == OUTCOME_RETRYABLE
    assert result.accepted is False


def test_prompt_rejection_is_retryable_conservative() -> None:
    transport = FakeTransport()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, _worker_terminal_messages()))
    transport.push(_resp(409, {"error": "conflict"}))
    result = _reentry(transport).reenter(PARENT, "envelope")
    assert result.outcome == OUTCOME_RETRYABLE
    assert result.accepted is False


def test_accepted_without_ack_is_not_completed_ack() -> None:
    transport = FakeTransport()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(_resp(200, [_assistant_message("no ack here")]))
    transport.push(_resp(200, {}))  # confirmed idle
    result = _reentry(transport).reenter(PARENT, "envelope")
    assert result.outcome == OUTCOME_COMPLETED
    assert result.accepted is True
    assert result.ack_observed is False
    assert result.error_code == "ACK_NOT_OBSERVED"


def test_transport_204_is_not_ack_structurally() -> None:
    transport = FakeTransport()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(_resp(200, [_assistant_message("no ack here")]))
    transport.push(_resp(200, {}))
    reentry = _reentry(transport)
    result = reentry.reenter(PARENT, "envelope")
    assert result.accepted is True
    assert result.ack_observed is False
    # A 204-only response can never satisfy the ACK parser.
    assert not parse_completion_ack(result.response_excerpt, canonical_task_id="t", card_digest="d")


def test_delivery_transport_maps_outcomes() -> None:
    transport = FakeTransport()
    transport.push(_resp(404))
    delivery = OpenCodeCompletionDeliveryTransport(_reentry(transport))
    evidence = delivery.deliver(session_ref=PARENT, envelope="envelope")
    assert evidence.outcome is DeliveryTransportOutcome.NOT_FOUND


def test_completion_envelope_unchanged_and_ack_identity_bound() -> None:
    task_id = _package().canonical_task_id
    card = {
        "schema_version": "1",
        "task_ref": task_id,
        "role": "coder",
        "outcome": "success",
        "result_handoff_ref": {"ref": "handoff://x", "digest": "a" * 64},
        "card_digest": "b" * 64,
    }
    # Build via the real durable record so the existing envelope builder runs.
    from aota_forge.core.execution.durable_state import (
        DurableExecutionRecord,
        ExecutionPhase,
        card_digest_for,
    )
    from aota_forge.core.execution.results import CanonicalResult

    result = CanonicalResult.success(
        canonical_task_id=task_id, executor_id="opencode", correlation_id="c1"
    )
    record = DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="opencode",
        package_id="p1",
        correlation_id="c1",
        dispatch_attempt_id="d1",
        idempotency_key="i1",
        intent_fingerprint="f" * 64,
        execution_phase=ExecutionPhase.DISPATCHED,
        canonical_task_state=CanonicalTaskState.COMPLETED,
        adapter_handle=WORKER,
        initial_state=CanonicalTaskState.RUNNING,
        dispatched_at="2026-09-15T00:00:00Z",
        terminal_result=result,
        worker_result_card=card,
        worker_result_card_digest=card_digest_for(card),
        delivery_state=DeliveryState.PENDING,
    )
    envelope = build_completion_envelope(record)
    assert envelope.startswith(COMPLETION_ENVELOPE_HEADER)
    digest = record.worker_result_card_digest
    assert parse_completion_ack(_ack_text(task_id, digest), canonical_task_id=task_id, card_digest=digest)
    # canonical_task_id mismatch rejected
    assert not parse_completion_ack(_ack_text("other-task", digest), canonical_task_id=task_id, card_digest=digest)
    # card_digest mismatch rejected
    assert not parse_completion_ack(_ack_text(task_id, "f" * 64), canonical_task_id=task_id, card_digest=digest)
    # 204-only (None) never reconciles
    assert not parse_completion_ack(None, canonical_task_id=task_id, card_digest=digest)


# ---------------------------------------------------------------------------
# Coordinator-level delivery / ACK integration
# ---------------------------------------------------------------------------


def _dispatch_and_complete(
    tmp_path: pathlib.Path,
    transport: FakeTransport,
    *,
    task_id: str,
    semantic_return: bool,
) -> tuple[Any, Any, Any]:
    store = InMemoryExecutionStateStore()
    config = _config(tmp_path)
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    dispatcher = create_production_execution_dispatcher(
        runtime_config=config,
        host_client=client,
        state_store=store,
        origin_session_ref=OriginSessionRef(PARENT),
    )
    adapter = dispatcher.registry.get("opencode")
    adapter._trusted_worker_directory = WORKER_DIR
    transport.push(_resp(200, _session_payload(WORKER, WORKER_DIR, task_id)))
    transport.push(_resp(204))
    package = _package(task_id)
    dispatcher.dispatch(package)
    if semantic_return:
        sandbox = _synthetic_sandbox(tmp_path)
        ref = handoff_write(
            mode="result",
            semantic={"summary": "M2 W3 worker done"},
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
    provider = WorktreeSemanticReturnEvidenceProvider(_synthetic_sandbox(tmp_path))
    completion_transport = create_opencode_completion_delivery_transport(
        runtime_config=config,
        host_client=client,
        timeout_seconds=2.0,
        poll_interval_seconds=0.001,
    )
    coordinator = create_durable_completion_coordinator(
        dispatcher=dispatcher,
        state_store=store,
        runtime_config=config,
        transport=completion_transport,
        semantic_return_provider=provider,
    )
    # terminal observations for recovery (observe_status + dispatcher.result)
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _worker_terminal_messages()))
    transport.push(_resp(200, {}))
    transport.push(_resp(200, _worker_terminal_messages()))
    return store, coordinator, config


def test_exact_same_session_ack_after_204(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=True
    )
    report = coordinator.recover_once()
    assert report.observations[task_id] == "terminal_result_persisted"
    record = store.get(task_id)
    assert record.canonical_task_state is CanonicalTaskState.COMPLETED
    digest = record.worker_result_card_digest
    assert digest

    # Delivery pass: exact PARENT session, its row directory, prompt_async
    # accepted (204), then identity-bound ACK text observed.
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))  # baseline messages
    transport.push(_resp(204))  # accepted; NOT an ACK
    transport.push(_resp(200, [_assistant_message(_ack_text(task_id, digest), "ack1")]))
    delivery = coordinator.deliver_pending_once()
    assert delivery.outcomes[task_id] == "acknowledged"
    record = store.get(task_id)
    assert record.delivery_state is DeliveryState.ACKNOWLEDGED
    # parent session id unchanged before/after; delivery created NO new session
    def _session_creates() -> list[str]:
        return [
            r["url"]
            for r in transport.requests
            if r["method"] == "POST"
            and (r["url"].rstrip("/").endswith("/session") or "/session?directory=" in r["url"])
        ]

    assert len(_session_creates()) == 1  # only the original worker dispatch create
    # no second delivery once ACKNOWLEDGED (no duplicate redelivery)
    second = coordinator.deliver_pending_once()
    assert task_id not in second.outcomes


def test_ack_identity_mismatch_releases_pending(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=True
    )
    coordinator.recover_once()
    digest = store.get(task_id).worker_result_card_digest
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    # ACK for a DIFFERENT task identity must not ACK this card.
    transport.push(_resp(200, [_assistant_message(_ack_text("other-task", digest), "ack1")]))
    transport.push(_resp(200, {}))  # idle early-exit for the reentry
    delivery = coordinator.deliver_pending_once()
    assert delivery.outcomes[task_id] == "released_ack_not_proven"
    assert store.get(task_id).delivery_state is DeliveryState.PENDING


def test_card_digest_mismatch_releases_pending(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=True
    )
    coordinator.recover_once()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(_resp(200, [_assistant_message(_ack_text(task_id, "f" * 64), "ack1")]))
    transport.push(_resp(200, {}))
    delivery = coordinator.deliver_pending_once()
    assert delivery.outcomes[task_id] == "released_ack_not_proven"
    assert store.get(task_id).delivery_state is DeliveryState.PENDING


def test_missing_parent_session_drops_truthfully_no_replacement(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=True
    )
    coordinator.recover_once()
    transport.push(_resp(404))
    delivery = coordinator.deliver_pending_once()
    assert delivery.outcomes[task_id] == "dropped_session_missing"
    record = store.get(task_id)
    assert record.delivery_state is DeliveryState.DROPPED
    assert record.terminal_result is not None  # terminal truth retained
    assert not any(
        r["method"] == "POST" and r["url"].rstrip("/").endswith("/session")
        for r in transport.requests
    )


def test_no_task_return_rejects_semantic_success_truthfully(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=False
    )
    report = coordinator.recover_once()
    assert report.observations[task_id] == "semantic_result_not_proven"
    record = store.get(task_id)
    assert record.canonical_task_state is CanonicalTaskState.FAILED
    assert record.terminal_result is not None and record.terminal_result.ok is False
    # truthful FAILURE card stays durable and delivery-eligible (the parent
    # must learn about the failure; no success card is ever derived)
    assert record.worker_result_card is not None
    assert record.delivery_state is DeliveryState.PENDING


def test_duplicate_redelivery_reconciles_idempotently(tmp_path: pathlib.Path) -> None:
    transport = FakeTransport()
    task_id = _package().canonical_task_id
    store, coordinator, _ = _dispatch_and_complete(
        tmp_path, transport, task_id=task_id, semantic_return=True
    )
    coordinator.recover_once()
    digest = store.get(task_id).worker_result_card_digest
    # First delivery: temporary transport failure -> stays pending (bounded retry).
    from aota_forge.adapters.opencode.host_client import OpenCodeHostUnavailableError

    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(OpenCodeHostUnavailableError("refused"))
    first = coordinator.deliver_pending_once()
    assert first.outcomes[task_id] == "released_retryable"
    assert store.get(task_id).delivery_state is DeliveryState.PENDING
    # Second delivery attempt of the SAME card: idempotent reconciliation ACK.
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    transport.push(_resp(200, []))
    transport.push(_resp(204))
    transport.push(_resp(200, [_assistant_message(_ack_text(task_id, digest), "ack2")]))
    second = coordinator.deliver_pending_once()
    assert second.outcomes[task_id] == "acknowledged"
    record = store.get(task_id)
    assert record.delivery_state is DeliveryState.ACKNOWLEDGED
    assert record.delivery_attempt == 2  # at-least-once, bounded attempts


def test_transport_factory_rejects_hermes_config(tmp_path: pathlib.Path) -> None:
    from aota_forge.runtime.config import EXECUTOR_HERMES, SHARED_WORKER_PROFILE, TASK_MAIN_PROFILE

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
    hermes = RuntimeConfig(
        executor=EXECUTOR_HERMES,
        executable=exe,
        concurrency=1,
        provider=None,
        model=None,
        bindings=bindings,
    )
    with pytest.raises(RuntimeConfigError):
        create_opencode_completion_delivery_transport(runtime_config=hermes)


def test_doorbell_events_carry_directory_scope_but_are_not_queue() -> None:
    transport = FakeTransport()
    transport.push(_resp(200, _session_payload(PARENT, DIR)))
    client = OpenCodeHostClient("http://127.0.0.1:4096", http_transport=transport)
    events = client.observe_events(directory=DIR, timeout_seconds=0.01, max_events=8)
    assert events == []
    assert "directory=" in transport.requests[-1]["url"]
