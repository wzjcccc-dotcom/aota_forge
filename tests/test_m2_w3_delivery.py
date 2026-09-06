"""M2/W3 — Pending delivery, single-flight claim, retry, ACK (deterministic).

Covers the delivery state machine over the W1 durable delivery structure:

- terminal result + CARD MUST be durable before any delivery attempt (§19)
- pending -> claimed CAS single-flight; stale claims reclaim; no regressions
- retryable transport outcomes release to pending with ACK=no (§25)
- exact-session hard-missing / trusted-origin-missing drop ONLY the delivery
  with durable terminal truth retained and ACK still false (§26)
- ACK requires reconciliation of THIS canonical_task_id + card_digest (§29/§30);
  transport completion without a bound ack is never delivery
- acknowledged records are never rescanned/redelivered (§32/R4)
- bounded attempt cap policy
- the CARD-first envelope carries bounded identity/CARD only — never raw
  Worker stdout/stderr (§27)
"""

from __future__ import annotations

import json
import threading

import pytest
from m2_w3_support import (
    ORIGIN_SESSION,
    TEST_SCOPE,
    ack_completed,
    build_coordinator,
    build_dispatcher,
    claim_delivery,
    completed_without_ack,
    make_package,
    not_found_missing_session,
    open_store,
    retryable_busy,
)
from m2_w3_support import ControlledTransport
from m2_w3_support import DurableWorldAdapter

from aota_forge.core.execution.dispatcher import AdapterProtocolError
from aota_forge.core.execution.durable_state import DeliveryState
from aota_forge.runtime.completion import (
    COMPLETION_ENVELOPE_HEADER,
    DELIVER_ACKNOWLEDGED,
    DELIVER_CLAIM_RACE_LOST,
    DELIVER_DROPPED_ATTEMPT_CAP,
    DELIVER_DROPPED_ORIGIN_MISSING,
    DELIVER_DROPPED_SESSION_MISSING,
    DELIVER_RELEASED_ACK_NOT_PROVEN,
    DELIVER_RELEASED_RETRYABLE,
    DELIVER_TRUTH_NOT_DURABLE,
    MAX_DELIVERIES_PER_PASS,
    build_completion_envelope,
    parse_completion_ack,
)

DISPATCHED = "task-w3-delivery"


@pytest.fixture(autouse=True)
def _fresh_world():
    DurableWorldAdapter.reset_world()
    yield
    DurableWorldAdapter.reset_world()


def dispatch_terminal(store, *, outcome="success", task_id=DISPATCHED):
    """Dispatch through W1 durable mode, reconcile to terminal, persist CARD."""
    adapter = DurableWorldAdapter()
    dispatcher = build_dispatcher(store, adapter)
    coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 4})
    package = make_package(task_id, outcome=outcome)
    dispatcher.dispatch(package, target_executor_id="durable-fake")
    # adapter reports terminal via the normal seam
    coordinator.recover_once()
    return dispatcher, coordinator


class TestTerminalBeforeDeliveryGate:
    def test_delivery_requires_durable_result_and_card(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        adapter = DurableWorldAdapter()
        dispatcher = build_dispatcher(store, adapter)
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        # Record is RUNNING: not even a delivery candidate.
        transport = ControlledTransport(default=ack_completed(DISPATCHED, "x"))
        coordinator = build_coordinator(dispatcher, store, transport)
        report = coordinator.deliver_pending_once()
        assert report.outcomes == {}
        assert transport.attempts == []

        # Force a terminal observation WITHOUT the CARD attached (bypass the
        # coordinator's own CARD persistence): the delivery gate must still
        # refuse to send anything.
        record = store.get(DISPATCHED)
        from aota_forge.core.execution.results import CanonicalResult

        result = CanonicalResult.success(
            canonical_task_id=DISPATCHED,
            executor_id="durable-fake",
            result_data={"proof": "gate"},
            correlation_id="corr-x",
        )
        store.compare_and_swap(
            DISPATCHED,
            record.record_revision,
            {"terminal_result": result.to_dict(), "canonical_task_state": result.canonical_task_state},
        )
        report2 = coordinator.deliver_pending_once()
        assert report2.outcomes[DISPATCHED] == DELIVER_TRUTH_NOT_DURABLE
        assert transport.attempts == []
        # No claim consumed; the record stays honestly pending.
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        assert store.get(DISPATCHED).delivery_attempt == 0

    def test_envelope_is_card_only_no_raw_worker_io(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        record = store.get(DISPATCHED)
        envelope = build_completion_envelope(record)
        assert COMPLETION_ENVELOPE_HEADER in envelope
        assert f"canonical_task_id={DISPATCHED}" in envelope
        assert f"card_digest={record.worker_result_card_digest}" in envelope
        assert "result_handoff_ref=" in envelope
        assert "AOTA_COMPLETION_ACK_V1" in envelope
        # §27: raw Worker stdout/stderr are never injected into the session.
        assert "raw-worker-stdout-should-not-travel-to-taskmain" not in envelope
        assert "raw-worker-stderr-should-not-travel-to-taskmain" not in envelope
        card_payload = record.worker_result_card
        assert isinstance(card_payload, dict)
        assert isinstance(json.dumps(dict(card_payload), sort_keys=True), str)


class TestDeliveryStateMachine:
    def test_pending_claim_ack_happy_path(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        record = store.get(DISPATCHED)
        digest = record.worker_result_card_digest
        transport = ControlledTransport(default=ack_completed(DISPATCHED, digest))
        coordinator._transport = transport
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.ACKNOWLEDGED
        assert final.delivery_claim_owner is None
        assert final.delivery_claim_until is None
        assert final.delivery_attempt == 1
        assert transport.attempts[0]["session_ref"] == ORIGIN_SESSION
        assert final.terminal_result is not None and final.worker_result_card is not None

    def test_transport_completion_without_ack_is_not_delivery(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        transport = ControlledTransport(default=completed_without_ack())
        coordinator._transport = transport
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        after = store.get(DISPATCHED)
        assert after.delivery_state == DeliveryState.PENDING
        assert after.delivery_attempt == 1
        assert after.terminal_result is not None  # truth retained, undelivered

    def test_ack_mismatched_identity_rejected_then_right_ack_accepted(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        wrong = ack_completed("someone-elses-task", digest)
        wrong_digest = ack_completed(DISPATCHED, "0" * 64)
        transport = ControlledTransport(script=[wrong, wrong_digest])
        coordinator._transport = transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_ACK_NOT_PROVEN
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING
        transport2 = ControlledTransport(default=ack_completed(DISPATCHED, digest))
        coordinator._transport = transport2
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert store.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED

    def test_retryable_busy_keeps_pending_and_retries_same_session(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        transport = ControlledTransport(script=[retryable_busy(), retryable_busy()])
        coordinator._transport = transport
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_RETRYABLE
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_RETRYABLE
        mid = store.get(DISPATCHED)
        assert mid.delivery_state == DeliveryState.PENDING
        assert mid.delivery_attempt == 2
        coordinator._transport = ControlledTransport(default=ack_completed(DISPATCHED, digest))
        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert all(a["session_ref"] == ORIGIN_SESSION for a in transport.attempts)

    def test_transport_crash_keeps_pending(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        transport = ControlledTransport(raise_at={0})
        coordinator._transport = transport
        from aota_forge.runtime.completion import DELIVER_RELEASED_TRANSPORT_ERROR

        assert coordinator.deliver_pending_once().outcomes[DISPATCHED] == DELIVER_RELEASED_TRANSPORT_ERROR
        assert store.get(DISPATCHED).delivery_state == DeliveryState.PENDING

    def test_hard_missing_session_drops_delivery_truth_retained(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        coordinator._transport = ControlledTransport(default=not_found_missing_session())
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_DROPPED_SESSION_MISSING
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.DROPPED
        assert final.terminal_result is not None and final.worker_result_card is not None
        # DROPPED is not an ACK of delivery: reconciliation never happened.
        assert final.delivery_state != DeliveryState.ACKNOWLEDGED
        # Record survives recovery scans unchanged (never deleted, never redelivered).
        assert all(r.canonical_task_id != DISPATCHED for r in store.scan_requiring_recovery())

    def test_missing_trusted_origin_binding_drops_delivery_only(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        adapter = DurableWorldAdapter()
        dispatcher = build_dispatcher(store, adapter, origin_session_ref=None)
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(default=completed_without_ack()))
        coordinator.recover_once()
        record = store.get(DISPATCHED)
        assert record.origin_session_ref is None
        report = coordinator.deliver_pending_once()
        assert report.outcomes[DISPATCHED] == DELIVER_DROPPED_ORIGIN_MISSING
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.DROPPED
        assert final.terminal_result is not None

    def test_attempt_cap_bounds_the_retry_loop(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        coordinator._delivery_attempt_cap = 3
        coordinator._transport = ControlledTransport(default=retryable_busy())
        outcomes = []
        for _ in range(4):
            outcomes.append(coordinator.deliver_pending_once().outcomes.get(DISPATCHED))
        assert outcomes[:3] == [DELIVER_RELEASED_RETRYABLE] * 3
        assert outcomes[3] == DELIVER_DROPPED_ATTEMPT_CAP
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.DROPPED
        assert final.terminal_result is not None  # terminal truth NEVER dropped


class TestClaimSingleFlight:
    def test_stale_revision_loses_claim(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        record = store.get(DISPATCHED)
        # Two snapshots of the same revision = two racing pass owners.
        transport = ControlledTransport(default=ack_completed(DISPATCHED, record.worker_result_card_digest))
        coordinator._transport = transport
        first = coordinator._attempt_delivery(record)
        second = coordinator._attempt_delivery(record)
        assert first == DELIVER_ACKNOWLEDGED
        assert second == DELIVER_CLAIM_RACE_LOST
        assert len(transport.attempts) == 1

    def test_concurrent_pass_second_waits_then_skips_claimed(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        gate = threading.Event()

        class BlockingTransport:
            def __init__(self):
                self.started = threading.Event()
                self.attempts = []

            def deliver(self, *, session_ref, envelope):
                self.started.set()
                assert gate.wait(timeout=5)
                self.attempts.append(session_ref)
                return ack_completed(DISPATCHED, digest)

        blocker = BlockingTransport()
        coordinator._transport = blocker
        results: dict[str, object] = {}

        def runner():
            results["inner"] = coordinator.deliver_pending_once()

        thread = threading.Thread(target=runner)
        thread.start()
        assert blocker.started.wait(timeout=5)
        # While A holds the claim, a second coordinator's pass must not claim.
        dispatcher_b = build_dispatcher(store, DurableWorldAdapter())
        coordinator_b = build_coordinator(dispatcher_b, store, ControlledTransport(default=completed_without_ack()))
        report_b = coordinator_b.deliver_pending_once()
        assert report_b.outcomes.get(DISPATCHED) is None  # claimed => out of pass
        gate.set()
        thread.join(timeout=10)
        assert results["inner"].outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        assert store.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED

    def test_expired_claim_reclaimed_then_delivered(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        stale = claim_delivery(store, DISPATCHED, owner="dead-process-x", until="2000-01-01T00:00:00+00:00", attempt=1)
        assert stale.delivery_state == DeliveryState.CLAIMED
        coordinator._transport = ControlledTransport(default=ack_completed(DISPATCHED, digest))
        report = coordinator.deliver_pending_once()
        assert report.reclaimed_expired_claims == [DISPATCHED]
        assert report.outcomes[DISPATCHED] == DELIVER_ACKNOWLEDGED
        final = store.get(DISPATCHED)
        assert final.delivery_state == DeliveryState.ACKNOWLEDGED
        assert final.delivery_attempt == 2

    def test_live_claim_is_not_stolen(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        claim_delivery(store, DISPATCHED, owner="active-owner", until="2999-01-01T00:00:00+00:00", attempt=1)
        coordinator._transport = ControlledTransport(default=completed_without_ack())
        report = coordinator.deliver_pending_once()
        assert report.reclaimed_expired_claims == []
        assert report.outcomes.get(DISPATCHED) is None
        assert store.get(DISPATCHED).delivery_claim_owner == "active-owner"

    def test_acknowledged_never_regresses(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        digest = store.get(DISPATCHED).worker_result_card_digest
        coordinator._transport = ControlledTransport(default=ack_completed(DISPATCHED, digest))
        coordinator.deliver_pending_once()
        record = store.get(DISPATCHED)
        assert record.delivery_state == DeliveryState.ACKNOWLEDGED
        with pytest.raises(Exception):
            store.compare_and_swap(
                DISPATCHED, record.record_revision, {"delivery_state": DeliveryState.PENDING.value}
            )
        # recovery scan skips acknowledged; second pass delivers nothing (§32/R4)
        assert all(r.canonical_task_id != DISPATCHED for r in store.scan_requiring_recovery())
        report = coordinator.deliver_pending_once()
        assert report.outcomes == {}
        assert store.get(DISPATCHED).delivery_state == DeliveryState.ACKNOWLEDGED


class TestEnvelopeAndAckContract:
    def test_envelope_binds_identity_digest_and_handoff(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        _, coordinator = dispatch_terminal(store)
        record = store.get(DISPATCHED)
        envelope = build_completion_envelope(record)
        head = envelope.splitlines()
        assert head[0] == COMPLETION_ENVELOPE_HEADER
        assert head[1] == f"canonical_task_id={DISPATCHED}"
        assert head[2] == f"card_digest={record.worker_result_card_digest}"
        assert head[3].startswith("result_handoff_ref=")
        card_json = head[-1]
        assert json.loads(card_json)["task_ref"] == DISPATCHED
        assert f"AOTA_COMPLETION_ACK_V1 canonical_task_id={DISPATCHED} card_digest={record.worker_result_card_digest}" in envelope

    def test_envelope_requires_durable_truth(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        adapter = DurableWorldAdapter()
        dispatcher = build_dispatcher(store, adapter)
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        with pytest.raises(Exception):
            build_completion_envelope(store.get(DISPATCHED))

    def test_ack_parsing_is_identity_bound(self):
        ok = "prefix\nAOTA_COMPLETION_ACK_V1 canonical_task_id=t1 card_digest=d1\nsuffix"
        assert parse_completion_ack(ok, canonical_task_id="t1", card_digest="d1") is True
        assert parse_completion_ack(ok, canonical_task_id="t1", card_digest="other") is False
        assert parse_completion_ack("no ack here", canonical_task_id="t1", card_digest="d1") is False
        assert parse_completion_ack(None, canonical_task_id="t1", card_digest="d1") is False
        split = (
            "AOTA_COMPLETION_ACK_V1 canonical_task_id=t1 card_digest=d1\n"
            "AOTA_COMPLETION_ACK_V1 canonical_task_id=t1 card_digest=CONTRADICTION"
        )
        assert parse_completion_ack(split, canonical_task_id="t1", card_digest="d1") is False


class TestBoundedPasses:
    def test_delivery_pass_is_bounded_per_run(self, tmp_path):
        store = open_store(tmp_path / "state.json")
        for i in range(MAX_DELIVERIES_PER_PASS + 2):
            task = f"bulk-{i}"
            dispatcher = build_dispatcher(store, DurableWorldAdapter())
            dispatcher.dispatch(make_package(task), target_executor_id="durable-fake")
            coordinator = build_coordinator(dispatcher, store, ControlledTransport(), limits={TEST_SCOPE: 16})
            coordinator.recover_once()
        dispatcher = build_dispatcher(store, DurableWorldAdapter())
        coordinator = build_coordinator(dispatcher, store, ControlledTransport(default=completed_without_ack()))
        report = coordinator.deliver_pending_once(max_deliveries=3)
        assert len(report.outcomes) == 3

    def test_protocol_error_never_fabricates_state(self, tmp_path):
        # recovery-side: protocol integrity failure keeps durable state intact
        store = open_store(tmp_path / "state.json")
        adapter = DurableWorldAdapter()
        dispatcher = build_dispatcher(store, adapter)
        dispatcher.dispatch(make_package(DISPATCHED), target_executor_id="durable-fake")
        protocol_error = AdapterProtocolError("adapter contradicted canonical identity")
        adapter_b = DurableWorldAdapter(force_status_error=protocol_error)
        dispatcher_b = build_dispatcher(store, adapter_b)
        coordinator_b = build_coordinator(dispatcher_b, store, ControlledTransport())
        report = coordinator_b.recover_once()
        assert report.observations[DISPATCHED] == "protocol_error_preserved"
        assert store.get(DISPATCHED).canonical_task_state.value == "RUNNING"  # unchanged truth
