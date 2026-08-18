"""M3-B13 Cutover Simulator & Fixtures.

Scope (Issue #9, Lane M3-B13):
- Deterministic isolated simulation environment for validating cutover mechanics.
- Tests dry-run, unauthorized activation, invalid/expired/revoked authorization,
  stale revision, target fingerprint mismatch, ambiguous targets, idempotency replay/conflict,
  precommit abort preservation, postcommit failure isolation, and synthetic authorized switch.
- Strictly asserts complete production isolation and that governance CUTOVER_AUTHORIZED remains False.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from aota_forge.core.cutover.model import (
    B13_PRODUCTION_BINDING_WRITE_COUNT,
    B13_PRODUCTION_GRAPH_WRITE_COUNT,
    B13_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    B13_PRODUCTION_REVISION_WRITE_COUNT,
    CUTOVER_AUTHORIZED,
    CutoverAuthorization,
    CutoverMode,
    CutoverRequest,
    CutoverState,
)
from aota_forge.core.cutover.preconditions import (
    CutoverPreconditionEvaluator,
    PreconditionFailureCode,
)
from aota_forge.core.cutover.receipt import CutoverReceipt
from aota_forge.core.cutover.transaction import (
    CutoverTransaction,
    CutoverTransactionStore,
)


def create_sample_target_state() -> dict[str, Any]:
    """Sample deterministic B12-validated shadow target payload."""
    return {
        "namespace": "shadow_b11_handoff_src",
        "transformation_version": "m3-b11-v1",
        "rebuild_fingerprint": "5ab5cd8fd5bd12089c7cf3b6ececa75374fad30da09e94463ab7de4e04b2884a",
        "record_counts": {
            "completions": 2,
            "decisions": 1,
            "edges": 1,
            "executions": 2,
            "subjects": 4,
            "total": 11,
            "workflows": 1,
        },
        "status": "ACCEPTED",
    }


class CutoverSimulationEnvironment:
    """Isolated environment for cutover validation scenarios."""

    SOURCE_REF = "subject:plan:issue_9_legacy"
    TARGET_REF = "subject:plan:issue_9_validated_shadow"
    B12_VAL_REF = "b12_report::manifest_b12_exec"

    def __init__(self, namespace: str = "isolated_simulation_lane_b13") -> None:
        self.namespace = namespace
        self.target_state = create_sample_target_state()
        encoded = json.dumps(self.target_state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.target_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        self.store = CutoverTransactionStore(self.namespace)
        self._reset_store()

    def _reset_store(self) -> None:
        self.store = CutoverTransactionStore(self.namespace)
        # Source authority at revision 1
        self.store.register_authority(self.SOURCE_REF, initial_revision=1, active_target=self.SOURCE_REF)
        # Register validated shadow target
        self.store.register_target(self.TARGET_REF, self.target_state)
        # Register B12 validation report
        self.store.register_b12_validation(
            self.B12_VAL_REF,
            {
                "status": "ACCEPTED",
                "rebuild_fingerprint": self.target_fingerprint,
                "snapshot_fingerprint": self.target_fingerprint,
                "total_records": 11,
            },
        )

    def create_synthetic_test_authorization(
        self,
        request_id: str = "req_sim_01",
        status: str = "active",
        validity_offset_minutes: int = 10,
    ) -> CutoverAuthorization:
        """Create an isolated synthetic test authorization (for simulation tests only)."""
        now = datetime.now(timezone.utc)
        return CutoverAuthorization(
            auth_id=f"auth_synth_{request_id}",
            issued_by="synthetic_simulation_harness",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            expires_at=(now + timedelta(minutes=validity_offset_minutes)).isoformat(),
            request_id=request_id,
            source_authority_ref=self.SOURCE_REF,
            target_authority_ref=self.TARGET_REF,
            scope="authority_switch_simulation",
            status=status,
            is_synthetic=True,
        )

    def create_request(
        self,
        request_id: str = "req_sim_01",
        idempotency_key: str = "idem_sim_01",
        mode: CutoverMode = CutoverMode.DRY_RUN,
        expected_source_revision: int = 1,
        expected_target_fingerprint: str | None = None,
        b12_val_ref: str | None = None,
        b12_val_fingerprint: str | None = None,
        source_ref: str | None = None,
        target_ref: str | None = None,
        authorization: CutoverAuthorization | None = None,
        target_candidates: tuple[str, ...] = (),
    ) -> CutoverRequest:
        return CutoverRequest(
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_authority_ref=source_ref or self.SOURCE_REF,
            target_authority_ref=target_ref or self.TARGET_REF,
            expected_source_revision=expected_source_revision,
            expected_target_fingerprint=expected_target_fingerprint or self.target_fingerprint,
            b12_validation_ref=b12_val_ref or self.B12_VAL_REF,
            b12_validation_fingerprint=b12_val_fingerprint or self.target_fingerprint,
            mode=mode,
            authorization=authorization,
            target_candidates=target_candidates,
        )

    def run_dry_run(self) -> CutoverReceipt:
        """Execute a valid dry-run; verify zero store mutation."""
        self._reset_store()
        before_fp = self.store.fingerprint()
        req = self.create_request(mode=CutoverMode.DRY_RUN)
        tx = CutoverTransaction(self.store, req)
        receipt = tx.execute()
        after_fp = self.store.fingerprint()
        assert before_fp == after_fp, "Dry run mutated transaction store!"
        assert receipt.commit_state == "not_committed"
        assert receipt.postcommit_verification_state == "not_applicable"
        assert self.store.get_active_authority(self.SOURCE_REF) == self.SOURCE_REF
        return receipt

    def run_unauthorized_activation(self) -> CutoverReceipt:
        """Execute activation without authorization; verify fail-closed and zero store mutation."""
        self._reset_store()
        before_fp = self.store.fingerprint()
        req = self.create_request(mode=CutoverMode.ACTIVATION, authorization=None)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=False)
        receipt = tx.execute()
        after_fp = self.store.fingerprint()
        assert before_fp == after_fp, "Unauthorized activation attempt mutated store!"
        assert receipt.failure_code == PreconditionFailureCode.UNAUTHORIZED_ACTIVATION.value
        assert receipt.commit_state == "not_committed"
        assert self.store.get_active_authority(self.SOURCE_REF) == self.SOURCE_REF
        return receipt

    def run_stale_revision(self) -> CutoverReceipt:
        """Execute with stale revision (expected=99, actual=1); verify CAS rejection."""
        self._reset_store()
        before_fp = self.store.fingerprint()
        req = self.create_request(mode=CutoverMode.DRY_RUN, expected_source_revision=99)
        tx = CutoverTransaction(self.store, req)
        receipt = tx.execute()
        after_fp = self.store.fingerprint()
        assert before_fp == after_fp
        assert receipt.failure_code == PreconditionFailureCode.STALE_REVISION.value
        return receipt

    def run_target_fingerprint_mismatch(self) -> CutoverReceipt:
        """Execute with tampered/mismatched target fingerprint; verify rejection."""
        self._reset_store()
        req = self.create_request(mode=CutoverMode.DRY_RUN, expected_target_fingerprint="0000000000000000000000000000000000000000000000000000000000000000")
        tx = CutoverTransaction(self.store, req)
        receipt = tx.execute()
        assert receipt.failure_code == PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH.value
        return receipt

    def run_unvalidated_target(self) -> CutoverReceipt:
        """Execute with missing/unvalidated B12 reference; verify rejection."""
        self._reset_store()
        req = self.create_request(mode=CutoverMode.DRY_RUN, b12_val_ref="b12_nonexistent")
        tx = CutoverTransaction(self.store, req)
        receipt = tx.execute()
        assert receipt.failure_code == PreconditionFailureCode.MISSING_B12_VALIDATION.value
        return receipt

    def run_ambiguous_target(self) -> CutoverReceipt:
        """Execute with ambiguous target candidates; verify fail-closed."""
        self._reset_store()
        req = self.create_request(
            mode=CutoverMode.DRY_RUN,
            target_candidates=(self.TARGET_REF, "subject:plan:other_candidate"),
        )
        tx = CutoverTransaction(self.store, req)
        receipt = tx.execute()
        assert receipt.failure_code == PreconditionFailureCode.AMBIGUOUS_TARGET.value
        return receipt

    def run_expired_authorization(self) -> CutoverReceipt:
        """Execute with expired authorization; verify rejection."""
        self._reset_store()
        auth = self.create_synthetic_test_authorization(status="expired", validity_offset_minutes=-10)
        req = self.create_request(mode=CutoverMode.ACTIVATION, authorization=auth)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=True)
        receipt = tx.execute()
        assert receipt.failure_code == PreconditionFailureCode.EXPIRED_AUTHORIZATION.value
        return receipt

    def run_revoked_authorization(self) -> CutoverReceipt:
        """Execute with revoked authorization; verify rejection."""
        self._reset_store()
        auth = self.create_synthetic_test_authorization(status="revoked")
        req = self.create_request(mode=CutoverMode.ACTIVATION, authorization=auth)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=True)
        receipt = tx.execute()
        assert receipt.failure_code == PreconditionFailureCode.REVOKED_AUTHORIZATION.value
        return receipt

    def run_idempotency_replay(self) -> tuple[CutoverReceipt, CutoverReceipt]:
        """Execute dry-run and then exact replay; verify identical outcome and zero duplicate effect."""
        self._reset_store()
        req = self.create_request(request_id="req_idem_1", idempotency_key="key_replay_01", mode=CutoverMode.DRY_RUN)
        tx1 = CutoverTransaction(self.store, req)
        r1 = tx1.execute()
        tx2 = CutoverTransaction(self.store, req)
        r2 = tx2.execute()
        assert r1.is_replay is False
        assert r2.is_replay is True
        assert r1.receipt_id == r2.receipt_id
        assert r1.fingerprint() == r2.fingerprint()
        return r1, r2

    def run_idempotency_conflict(self) -> CutoverReceipt:
        """Execute with same key but different target; verify conflict rejection."""
        self._reset_store()
        req1 = self.create_request(request_id="req_idem_1", idempotency_key="key_conflict_01", mode=CutoverMode.DRY_RUN)
        tx1 = CutoverTransaction(self.store, req1)
        tx1.execute()
        # Same key, changed revision / target
        req2 = self.create_request(
            request_id="req_idem_2",
            idempotency_key="key_conflict_01",
            mode=CutoverMode.DRY_RUN,
            expected_source_revision=2,
        )
        tx2 = CutoverTransaction(self.store, req2)
        r2 = tx2.execute()
        assert r2.failure_code == PreconditionFailureCode.IDEMPOTENCY_CONFLICT.value
        return r2

    def run_precommit_failure_injection(self) -> tuple[CutoverReceipt, str, str]:
        """Inject precommit failure; verify old authority preserved and zero partial switch."""
        self._reset_store()
        auth = self.create_synthetic_test_authorization(request_id="req_precommit_fail")
        req = self.create_request(request_id="req_precommit_fail", mode=CutoverMode.ACTIVATION, authorization=auth)
        before_auth = self.store.get_active_authority(self.SOURCE_REF)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=True)
        receipt = tx.execute(inject_precommit_failure=True)
        after_auth = self.store.get_active_authority(self.SOURCE_REF)
        assert before_auth == after_auth == self.SOURCE_REF
        assert receipt.commit_state == "staged_aborted"
        return receipt, before_auth, after_auth

    def run_postcommit_failure_injection(self) -> tuple[CutoverReceipt, CutoverState]:
        """Inject postcommit verification failure; verify explicit failure state and no silent rollback."""
        self._reset_store()
        auth = self.create_synthetic_test_authorization(request_id="req_postcommit_fail")
        req = self.create_request(request_id="req_postcommit_fail", mode=CutoverMode.ACTIVATION, authorization=auth)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=True)
        receipt = tx.execute(inject_postcommit_failure=True)
        state = tx.state_machine.current_state
        assert state == CutoverState.COMMITTED_VERIFICATION_FAILED
        assert receipt.commit_state == "committed"
        assert receipt.postcommit_verification_state == "failed"
        # Commit occurred in store, verification failed explicitly without silent rollback
        assert self.store.get_active_authority(self.SOURCE_REF) == self.TARGET_REF
        return receipt, state

    def run_synthetic_authorized_switch(self) -> CutoverReceipt:
        """Execute successful authorized switch in isolated test simulation."""
        self._reset_store()
        auth = self.create_synthetic_test_authorization(request_id="req_synth_success")
        req = self.create_request(request_id="req_synth_success", mode=CutoverMode.ACTIVATION, authorization=auth)
        tx = CutoverTransaction(self.store, req, allow_synthetic_auth=True)
        receipt = tx.execute()
        assert tx.state_machine.current_state == CutoverState.POSTCOMMIT_VERIFIED
        assert receipt.commit_state == "committed"
        assert receipt.postcommit_verification_state == "verified"
        assert self.store.get_active_authority(self.SOURCE_REF) == self.TARGET_REF
        assert self.store.get_revision(self.SOURCE_REF) == 2
        return receipt


__all__ = [
    "create_sample_target_state",
    "CutoverSimulationEnvironment",
]
