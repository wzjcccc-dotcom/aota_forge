"""AF #49 M1/W10 — Completion Reentry Tool-Surface Continuity (V1/V2).

Proves:
1. FACTORY/POLICY: launcher phase2 and completion delivery resolve the SAME
   accepted production exact-session transport policy; no hidden default
   divergence.
2. EXACT SESSION: completion delivery constructs reentry with the target
   session id unchanged; no replacement-session fallback.
3. PAYLOAD/ACK PRESERVATION: the transport change does not alter completion
   envelope payload, Card digest, origin parent session id, delivery identity,
   or ACK identity.
4. SUCCESS + FAILURE CARD: both a success Card and a truthful failure Card use
   the identical production reentry policy (no outcome-dependent transport).
5. NEGATIVE TRANSPORT probes: missing session, foreign session, invalid
   session, reentry command failure, tool-surface precheck failure, ACK
   identity mismatch -> all fail closed; never silently fall back to chat_quiet
   or a replacement session.
6. TARGETED V2: real production composition seam without a real model:
   inspect/execute exact-session reentry construction -> verify the accepted
   W7 transport mode and exact id.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import aota_forge.composition.execution as composition_execution
from aota_forge.adapters.hermes.delivery import HermesCompletionDeliveryTransport
from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_NOT_FOUND,
    PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
    TRANSPORT_CHAT_QUIET,
    TRANSPORT_ONESHOT_RESUME,
    HermesExactSessionReentry,
    HermesReentryResult,
    HermesSessionReentryError,
    observe_persisted_session_tool_surface,
    production_aota_tool_surface_present,
)
from aota_forge.composition.execution import (
    create_durable_completion_coordinator,
    create_hermes_completion_delivery_transport,
)
from aota_forge.composition.task_main_daily_launcher import (
    PRODUCTION_EXACT_SESSION_TRANSPORT,
    DailyTaskMainLauncher,
)
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    DurableExecutionRecord,
    ExecutionPhase,
    FileBackedExecutionStateStore,
    OriginSessionRef,
    card_digest_for,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultOutcome
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    DELIVER_DROPPED_SESSION_MISSING,
    DELIVER_RELEASED_ACK_NOT_PROVEN,
    DELIVER_RELEASED_TRANSPORT_ERROR,
    DeliveryTransportOutcome,
    DurableCompletionCoordinator,
    build_completion_envelope,
    parse_completion_ack,
)
from aota_forge.runtime.config import load_runtime_config
from aota_forge.work_plane.result_card import WorkerResultCard, project_worker_result_card
from m2_w3_support import (
    ORIGIN_SESSION,
    TEST_SCOPE,
    DurableWorldAdapter,
    build_coordinator,
    build_dispatcher,
    make_package,
    open_store,
)

DISPATCHED = "af49-w10-task"
TEST_ORIGIN_SESSION = "20260912_180000_origin10"


def _runtime_config_path(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "w10_runtime.json"
    cfg_path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/false",
                "concurrency": 2,
                "provider": "opencode-go",
                "model": "m",
                "bindings": {
                    "task-main": {"profile": "aota-task-main"},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg_path


def _make_hermes_state_db(
    db_path: Path,
    sessions: list[tuple[str, str | None, str | None]],
) -> None:
    """Create a minimal Hermes state.db fixture.

    sessions: list of (session_id, parent_session_id, tool_names_json)
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            parent_session_id TEXT,
            end_reason TEXT,
            tool_names TEXT
        );
        CREATE TABLE session_turn_leases (
            conversation_id TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            acquired_at REAL,
            expires_at REAL
        );
        """
    )
    for sid, parent, tool_names in sessions:
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, NULL, ?)",
            (sid, parent, tool_names),
        )
    conn.commit()
    conn.close()


def _seed_terminal_record(
    store: FileBackedExecutionStateStore,
    task_id: str,
    *,
    origin_session: str = TEST_ORIGIN_SESSION,
    governance: ResultGovernanceProjection | None = None,
) -> WorkerResultCard:
    """Seed a durable execution record with deterministic CARD truth."""
    record = DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="hermes",
        package_id=f"{task_id}:pkg",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"attempt-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint="f" * 32,
        execution_phase=ExecutionPhase.PREPARED,
        canonical_task_state=CanonicalTaskState.CREATED,
        origin_session_ref=OriginSessionRef(value=origin_session),
        admission_scope=TEST_SCOPE,
    )
    store.create(record)
    record = store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "execution_phase": ExecutionPhase.DISPATCHED,
            "adapter_handle": "seed-handle",
            "initial_state": CanonicalTaskState.RUNNING,
            "dispatched_at": "2026-09-12T00:00:00+00:00",
            "canonical_task_state": CanonicalTaskState.RUNNING,
        },
    )
    gov = governance if governance is not None else ResultGovernanceProjection.success()
    if gov.outcome != ResultOutcome.SUCCESS:
        result = CanonicalResult.failure(
            canonical_task_id=task_id,
            executor_id="hermes",
            error_code="EXECUTION_FAILED",
            error_message="governed failure",
            correlation_id=f"corr-{task_id}",
        )
        task_state = CanonicalTaskState.FAILED
    else:
        result = CanonicalResult.success(
            canonical_task_id=task_id,
            executor_id="hermes",
            result_data={"proof": "w10-seed"},
            correlation_id=f"corr-{task_id}",
        )
        task_state = CanonicalTaskState.COMPLETED
    card = project_worker_result_card(
        result,
        gov,
        "coder",
        summary="w10 seeded terminal",
    )
    store.compare_and_swap(
        task_id,
        record.record_revision,
        {
            "canonical_task_state": task_state,
            "terminal_result": result.to_dict(),
            "worker_result_card": card.canonical_dict(),
            "worker_result_card_digest": card.compute_card_digest(),
            "delivery_state": DeliveryState.PENDING,
        },
    )
    return card


@pytest.fixture(autouse=True)
def _clean_world():
    DurableWorldAdapter.reset_world()
    yield
    DurableWorldAdapter.reset_world()
    reset_execution_dispatcher()


# ===========================================================================
# 1. FACTORY/POLICY: unified production exact-session transport policy
# ===========================================================================


class TestFactoryAndPolicyUnification:
    def test_shared_canonical_policy_constants(self) -> None:
        """The shared canonical policy constant resolves to TRANSPORT_ONESHOT_RESUME."""
        assert PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT == TRANSPORT_ONESHOT_RESUME
        assert PRODUCTION_EXACT_SESSION_TRANSPORT == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert PRODUCTION_EXACT_SESSION_TRANSPORT == TRANSPORT_ONESHOT_RESUME

    def test_launcher_phase2_builder_uses_shared_policy(self) -> None:
        """DailyTaskMainLauncher builder resolves the shared canonical policy."""
        launcher = DailyTaskMainLauncher(plan_adapter=None, hermes_bin="/bin/false")
        ctx = SimpleNamespace(hermes_bin="/bin/false")
        adapter = launcher._build_exact_session_reentry(ctx=ctx, timeout_seconds=5)
        assert isinstance(adapter, HermesExactSessionReentry)
        assert adapter._transport == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert adapter._transport == TRANSPORT_ONESHOT_RESUME

    def test_completion_delivery_transport_factory_uses_shared_policy(
        self, tmp_path: Path
    ) -> None:
        """create_hermes_completion_delivery_transport resolves the shared canonical policy."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        transport = create_hermes_completion_delivery_transport(runtime_config=cfg)
        assert isinstance(transport, HermesCompletionDeliveryTransport)
        assert transport._reentry._transport == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert transport._reentry._transport == TRANSPORT_ONESHOT_RESUME

    def test_no_hidden_policy_divergence(self, tmp_path: Path) -> None:
        """Launcher phase2 and completion delivery reentry share the identical transport mode."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        launcher = DailyTaskMainLauncher(plan_adapter=None, hermes_bin="/bin/false")
        ctx = SimpleNamespace(hermes_bin="/bin/false")
        launcher_adapter = launcher._build_exact_session_reentry(ctx=ctx, timeout_seconds=5)
        delivery_transport = create_hermes_completion_delivery_transport(runtime_config=cfg)

        assert launcher_adapter._transport == delivery_transport._reentry._transport
        assert launcher_adapter._transport == "oneshot_resume"
        assert delivery_transport._reentry._transport != TRANSPORT_CHAT_QUIET


# ===========================================================================
# 2. EXACT SESSION: target session id unchanged; no replacement fallback
# ===========================================================================


class TestExactSessionIdentityPreservation:
    def test_delivery_targets_exact_session_id_unchanged(self, tmp_path: Path) -> None:
        """Completion delivery passes the exact target session id directly to reentry."""
        target_session = "20260912_200000_exact_session_id"
        calls: list[tuple[str, str]] = []

        class RecordingReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                calls.append((session_id, payload_text))
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt="AOTA_COMPLETION_ACK_V1 canonical_task_id=t1 card_digest=d1",
                    stderr_excerpt=None,
                )

        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        reentry = RecordingReentry(
            cfg.executable,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        evidence = transport.deliver(session_ref=target_session, envelope="test-envelope")

        assert evidence.outcome == DeliveryTransportOutcome.COMPLETED
        assert len(calls) == 1
        assert calls[0][0] == target_session
        assert calls[0][0] != "latest"
        assert calls[0][1] == "test-envelope"

    def test_no_replacement_session_fallback_on_missing(self, tmp_path: Path) -> None:
        """When the target session is absent, delivery fails closed without fallback."""
        home = tmp_path / "hermes_home"
        _make_hermes_state_db(home / "state.db", [])
        reentry = HermesExactSessionReentry(
            "/bin/false",
            hermes_home=home,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        evidence = transport.deliver(
            session_ref="20260912_000000_nonexistent",
            envelope="test-envelope",
        )
        assert evidence.outcome == DeliveryTransportOutcome.NOT_FOUND
        assert evidence.detail == "HERMES_EXACT_SESSION_NOT_FOUND"


# ===========================================================================
# 3. PAYLOAD/ACK PRESERVATION: envelope payload, Card digest, ACK identity
# ===========================================================================


class TestPayloadAndAckPreservation:
    def test_completion_envelope_and_ack_identity_preservation(self, tmp_path: Path) -> None:
        """The transport change does not alter completion envelope or ACK contract."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")
        card = _seed_terminal_record(store, DISPATCHED, origin_session=TEST_ORIGIN_SESSION)
        record = store.get(DISPATCHED)

        # 1. Build envelope through production builder
        envelope = build_completion_envelope(record)
        digest = card.compute_card_digest()
        assert f"canonical_task_id={DISPATCHED}" in envelope
        assert f"card_digest={digest}" in envelope
        assert f"result_handoff_ref={DISPATCHED}" in envelope
        expected_ack = f"AOTA_COMPLETION_ACK_V1 canonical_task_id={DISPATCHED} card_digest={digest}"
        assert expected_ack in envelope

        # 2. Recording reentry receives the exact envelope verbatim
        recorded_payloads: list[str] = []

        class RecordingReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                recorded_payloads.append(payload_text)
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt=expected_ack,
                    stderr_excerpt=None,
                )

        reentry = RecordingReentry(
            cfg.executable,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )

        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert len(recorded_payloads) == 1
        assert recorded_payloads[0] == envelope
        assert store.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED
        assert parse_completion_ack(expected_ack, canonical_task_id=DISPATCHED, card_digest=digest) is True


# ===========================================================================
# 4. SUCCESS + FAILURE CARD: both use identical production reentry policy
# ===========================================================================


class TestSuccessAndFailureCardDelivery:
    def test_both_success_and_failure_cards_use_production_transport(
        self, tmp_path: Path
    ) -> None:
        """Both a success Card and a truthful failure Card use the same transport policy."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")

        success_task = "task-success-w10"
        failure_task = "task-failure-w10"

        card_success = _seed_terminal_record(
            store,
            success_task,
            origin_session=TEST_ORIGIN_SESSION,
            governance=ResultGovernanceProjection.success(),
        )
        assert card_success.outcome == ResultOutcome.SUCCESS

        card_failure = _seed_terminal_record(
            store,
            failure_task,
            origin_session=TEST_ORIGIN_SESSION,
            governance=ResultGovernanceProjection.failure(
                {"code": "EXECUTION_FAILED", "message": "governed semantic failure proof", "retryable": False},
            ),
        )
        assert card_failure.outcome == ResultOutcome.FAILURE

        delivered_sessions: list[tuple[str, str, str]] = []

        class RecordingReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                task_id = ""
                digest = ""
                for line in payload_text.splitlines():
                    if line.startswith("canonical_task_id="):
                        task_id = line.split("=", 1)[1].strip()
                    elif line.startswith("card_digest="):
                        digest = line.split("=", 1)[1].strip()
                delivered_sessions.append((session_id, task_id, self._transport))
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt=f"AOTA_COMPLETION_ACK_V1 canonical_task_id={task_id} card_digest={digest}",
                    stderr_excerpt=None,
                )

        reentry = RecordingReentry(
            cfg.executable,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )

        report = coordinator.deliver_pending_once()
        assert report.outcomes[success_task] == DELIVER_ACKNOWLEDGED
        assert report.outcomes[failure_task] == DELIVER_ACKNOWLEDGED

        assert len(delivered_sessions) == 2
        # Both deliveries used the exact same oneshot_resume production transport
        assert delivered_sessions[0][2] == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert delivered_sessions[1][2] == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert delivered_sessions[0][2] == "oneshot_resume"
        assert delivered_sessions[1][2] == "oneshot_resume"


# ===========================================================================
# 5. NEGATIVE TRANSPORT PROBES: all fail closed; never silent fallback
# ===========================================================================


class TestNegativeTransportProbes:
    def test_missing_session_fails_closed(self, tmp_path: Path) -> None:
        """Missing session in state.db returns NOT_FOUND and drops delivery."""
        home = tmp_path / "hermes_home"
        _make_hermes_state_db(home / "state.db", [])
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")
        _seed_terminal_record(store, DISPATCHED, origin_session="nonexistent_session")

        transport = create_hermes_completion_delivery_transport(
            runtime_config=cfg, hermes_home=home
        )
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )

        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_DROPPED_SESSION_MISSING
        assert store.get(DISPATCHED).delivery_state == DeliveryState.DROPPED

    def test_invalid_session_id_fails_closed(self, tmp_path: Path) -> None:
        """Forbidden or invalid session id fails closed without subprocess execution."""
        reentry = HermesExactSessionReentry(
            "/bin/false",
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)

        for invalid_sid in ("latest", "-bad", "--all", "", "@claude"):
            evidence = transport.deliver(session_ref=invalid_sid, envelope="envelope")
            assert evidence.outcome == DeliveryTransportOutcome.FAILED
            assert evidence.detail is not None

    def test_reentry_command_failure_fails_closed(self, tmp_path: Path) -> None:
        """Reentry command execution failure maps to FAILED and retains delivery pending."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")
        _seed_terminal_record(store, DISPATCHED, origin_session=TEST_ORIGIN_SESSION)

        class FailingReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                return HermesReentryResult(
                    outcome=OUTCOME_FAILED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=False,
                    busy=False,
                    exit_code=1,
                    session_id=session_id,
                    resolved_session_id=None,
                    error_code="HERMES_REENTRY_FAILED",
                    error_message="process exited 1",
                    response_excerpt=None,
                    stderr_excerpt="fatal error",
                )

        reentry = FailingReentry(
            cfg.executable,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )

        # Direct transport delivery returns FAILED
        direct_evidence = transport.deliver(session_ref=TEST_ORIGIN_SESSION, envelope="test")
        assert direct_evidence.outcome == DeliveryTransportOutcome.FAILED

        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] in (DELIVER_RELEASED_TRANSPORT_ERROR, "released_retryable")
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        assert store.get(DISPATCHED).delivery_attempt == 1

    def test_tool_surface_precheck_failure_fails_closed(self, tmp_path: Path) -> None:
        """Tool surface precheck failure fails closed before calling reentry."""
        reentry_called = False

        class SpiedReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                nonlocal reentry_called
                reentry_called = True
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt="AOTA_COMPLETION_ACK_V1 canonical_task_id=t card_digest=d",
                    stderr_excerpt=None,
                )

        reentry = SpiedReentry(
            "/bin/false",
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        # 1. Explicit surface checker returns False
        transport = HermesCompletionDeliveryTransport(
            reentry,
            surface_checker=lambda _sid: False,
        )
        evidence = transport.deliver(session_ref=TEST_ORIGIN_SESSION, envelope="test")
        assert evidence.outcome == DeliveryTransportOutcome.FAILED
        assert evidence.detail == "TOOL_SURFACE_PRECHECK_FAILED"
        assert reentry_called is False

        # 2. Persisted tool surface lacking AOTA invoke capability in state.db
        home = tmp_path / "hermes_home"
        # Session has persisted empty tool_names
        _make_hermes_state_db(
            home / "state.db",
            [(TEST_ORIGIN_SESSION, None, "[]")],
        )
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        prod_transport = create_hermes_completion_delivery_transport(
            runtime_config=cfg,
            hermes_home=home,
        )
        evidence2 = prod_transport.deliver(session_ref=TEST_ORIGIN_SESSION, envelope="test")
        assert evidence2.outcome == DeliveryTransportOutcome.FAILED
        assert evidence2.detail == "TOOL_SURFACE_PRECHECK_FAILED"

    def test_ack_identity_mismatch_fails_closed(self, tmp_path: Path) -> None:
        """An ACK returned with wrong task_id or digest releases claim unacknowledged."""
        cfg = load_runtime_config(config_path=str(_runtime_config_path(tmp_path)))
        store = open_store(tmp_path / "state.json")
        _seed_terminal_record(store, DISPATCHED, origin_session=TEST_ORIGIN_SESSION)

        class MismatchedAckReentry(HermesExactSessionReentry):
            def reenter(self, session_id, payload_text, *, timeout_seconds=None):
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt=(
                        "AOTA_COMPLETION_ACK_V1 canonical_task_id=WRONG_TASK card_digest=WRONG_DIGEST"
                    ),
                    stderr_excerpt=None,
                )

        reentry = MismatchedAckReentry(
            cfg.executable,
            profile="aota-task-main",
            transport=PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT,
        )
        transport = HermesCompletionDeliveryTransport(reentry)
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = create_durable_completion_coordinator(
            dispatcher=dispatcher,
            state_store=store,
            runtime_config=cfg,
            transport=transport,
        )

        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING


# ===========================================================================
# 6. TARGETED V2: real production composition seam without real model
# ===========================================================================


class TestTargetedV2ProductionSeam:
    def test_targeted_v2_real_production_composition_seam(self, tmp_path: Path) -> None:
        """Targeted V2: inspect and execute exact-session reentry via production factory."""
        home = tmp_path / "hermes_home"
        target_session = "20260912_120000_deadbeef"
        # Seed session with valid AOTA tool pin
        _make_hermes_state_db(
            home / "state.db",
            [(target_session, None, json.dumps(["mcp__aota__invoke", "tool_search"]))],
        )

        cfg_file = tmp_path / "v2_runtime.json"
        fake_hermes = tmp_path / "hermes_fake_bin"
        fake_hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_hermes.chmod(0o755)

        cfg_file.write_text(
            json.dumps(
                {
                    "executor": "hermes",
                    "executable": str(fake_hermes),
                    "concurrency": 1,
                    "provider": "opencode-go",
                    "model": "m",
                    "bindings": {
                        "task-main": {"profile": "aota-task-main"},
                        "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                        "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    },
                }
            ),
            encoding="utf-8",
        )
        cfg = load_runtime_config(config_path=str(cfg_file))

        transport = create_hermes_completion_delivery_transport(
            runtime_config=cfg,
            hermes_home=home,
            spool_root=tmp_path / "spool",
        )

        # 1. Assert W7 transport mode on the constructed reentry object
        assert transport._reentry._transport == "oneshot_resume"
        assert transport._reentry._transport == PRODUCTION_EXACT_SESSION_REENTRY_TRANSPORT
        assert transport._reentry._profile == "aota-task-main"

        # 2. Capture argv produced by the exact-session reentry execution
        executed_argv: list[list[str]] = []

        class FakeProcess:
            def __init__(self, argv, **kwargs):
                executed_argv.append(argv)
                # If usage-file is passed, write mechanical usage JSON
                if "--usage-file" in argv:
                    usage_idx = argv.index("--usage-file") + 1
                    usage_path = Path(argv[usage_idx])
                    usage_path.parent.mkdir(parents=True, exist_ok=True)
                    usage_path.write_text(
                        json.dumps({"session_id": target_session, "completed": True}),
                        encoding="utf-8",
                    )
                self.stdout = io.BytesIO(b"AOTA_COMPLETION_ACK_V1 canonical_task_id=task1 card_digest=dig1\n")
                self.stderr = io.BytesIO(b"")
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

        transport._reentry._popen_factory = FakeProcess

        evidence = transport.deliver(
            session_ref=target_session,
            envelope="COMPLETION_ENVELOPE_CONTENT",
        )

        assert evidence.outcome == DeliveryTransportOutcome.COMPLETED
        assert len(executed_argv) == 1
        argv = executed_argv[0]

        # Verify command line arguments match the accepted W7 oneshot-resume contract:
        assert argv[0] == str(fake_hermes)
        assert "-p" in argv and argv[argv.index("-p") + 1] == "aota-task-main"
        assert "-z" in argv and argv[argv.index("-z") + 1] == "COMPLETION_ENVELOPE_CONTENT"
        assert "--resume" in argv and argv[argv.index("--resume") + 1] == target_session
        assert "--usage-file" in argv

        # Verify prohibited tokens are NEVER in argv:
        assert "chat" not in argv
        assert "-Q" not in argv
        assert "--create-if-missing" not in argv
        assert "-c" not in argv
        assert "--continue" not in argv
        assert "latest" not in argv
