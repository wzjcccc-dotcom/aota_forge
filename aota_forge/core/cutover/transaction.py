"""M3-B13 Cutover Transaction and Store Abstraction.

Scope (Issue #9, Lane M3-B13):
- Isolated in-memory transaction store strictly preventing production aliasing.
- Atomic Compare-And-Swap (CAS) on source revision.
- Complete deterministic before/after authority fingerprints.
- Strict multi-stage transaction: staging, atomic switch, explicit postcommit verification.
- Explicit failure isolation: precommit abort preserves old authority; postcommit verification failure
  records explicit failed state with zero silent rollback.
- Idempotency registry preventing duplicate transition on replay and rejecting conflicting keys.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
import uuid

from aota_forge.core.cutover.model import (
    CutoverMode,
    CutoverRequest,
    CutoverState,
)
from aota_forge.core.cutover.preconditions import (
    CutoverPreconditionEvaluator,
    PreconditionEnvelope,
    PreconditionFailureCode,
)
from aota_forge.core.cutover.receipt import CutoverReceipt
from aota_forge.core.cutover.state_machine import CutoverStateMachine


class ProductionAliasingError(ValueError):
    """Raised if an attempt is made to alias the production namespace."""


class CutoverTransactionStore:
    """Isolated in-memory authority repository."""

    def __init__(self, namespace: str = "isolated_simulation") -> None:
        if namespace == "production":
            raise ProductionAliasingError("CutoverTransactionStore cannot alias 'production' namespace.")
        self.namespace = namespace
        self._authorities: dict[str, str] = {}  # role_or_ref -> active_target_ref
        self._revisions: dict[str, int] = {}    # ref -> revision
        self._targets: dict[str, dict[str, Any]] = {}  # ref -> state payload
        self._b12_validations: dict[str, dict[str, Any]] = {}  # val_ref -> val_data
        self._idempotency_records: dict[str, tuple[str, CutoverReceipt]] = {}  # key -> (req_fp, receipt)

    def register_authority(self, ref: str, initial_revision: int = 1, active_target: str | None = None) -> None:
        self._revisions[ref] = initial_revision
        self._authorities[ref] = active_target if active_target is not None else ref

    def register_target(self, ref: str, state_payload: dict[str, Any]) -> None:
        self._targets[ref] = copy.deepcopy(state_payload)
        if ref not in self._revisions:
            self._revisions[ref] = 1

    def register_b12_validation(self, validation_ref: str, validation_data: dict[str, Any]) -> None:
        self._b12_validations[validation_ref] = copy.deepcopy(validation_data)

    def has_authority_ref(self, ref: str) -> bool:
        return ref in self._revisions or ref in self._authorities

    def has_target_ref(self, ref: str) -> bool:
        return ref in self._targets

    def get_active_authority(self, role_or_ref: str) -> str | None:
        return self._authorities.get(role_or_ref)

    def get_revision(self, ref: str) -> int:
        return self._revisions.get(ref, 0)

    def get_target_state(self, ref: str) -> dict[str, Any] | None:
        return self._targets.get(ref)

    def get_target_fingerprint(self, ref: str) -> str:
        state = self._targets.get(ref)
        if state is None:
            return ""
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get_b12_validation(self, validation_ref: str) -> dict[str, Any] | None:
        return self._b12_validations.get(validation_ref)

    def fingerprint(self) -> str:
        """Deterministic fingerprint of total store authority configuration."""
        payload = {
            "namespace": self.namespace,
            "authorities": dict(sorted(self._authorities.items())),
            "revisions": dict(sorted(self._revisions.items())),
            "targets": {k: self._targets[k] for k in sorted(self._targets.keys())},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def clone(self) -> CutoverTransactionStore:
        cloned = CutoverTransactionStore(self.namespace)
        cloned._authorities = dict(self._authorities)
        cloned._revisions = dict(self._revisions)
        cloned._targets = copy.deepcopy(self._targets)
        cloned._b12_validations = copy.deepcopy(self._b12_validations)
        cloned._idempotency_records = dict(self._idempotency_records)
        return cloned


class CutoverTransaction:
    """Atomic transactional coordinator for cutover mechanics."""

    def __init__(
        self,
        store: CutoverTransactionStore,
        request: CutoverRequest,
        *,
        evaluator: CutoverPreconditionEvaluator | None = None,
        allow_synthetic_auth: bool = False,
    ) -> None:
        self.store = store
        self.request = request
        self.evaluator = evaluator or CutoverPreconditionEvaluator()
        self.allow_synthetic_auth = allow_synthetic_auth
        self.state_machine = CutoverStateMachine(CutoverState.REQUESTED)
        self.transaction_id = f"tx_cutover_{uuid.uuid4().hex[:12]}"

    def execute(
        self,
        *,
        dry_run: bool | None = None,
        inject_precommit_failure: bool = False,
        inject_postcommit_failure: bool = False,
    ) -> CutoverReceipt:
        """Execute the guarded cutover transaction lifecycle."""
        req_fp = self.request.fingerprint()
        now_iso = datetime.now(timezone.utc).isoformat()
        receipt_id = f"rcpt_{uuid.uuid4().hex[:12]}"

        # 1. Idempotency Key Replay & Conflict Check
        if self.request.idempotency_key in self.store._idempotency_records:
            prev_fp, prev_receipt = self.store._idempotency_records[self.request.idempotency_key]
            if prev_fp == req_fp:
                # Deterministic replay of identical request: return cached receipt with is_replay=True
                return CutoverReceipt(
                    receipt_id=prev_receipt.receipt_id,
                    request_id=prev_receipt.request_id,
                    idempotency_key=prev_receipt.idempotency_key,
                    mode=prev_receipt.mode,
                    source_authority_ref=prev_receipt.source_authority_ref,
                    target_authority_ref=prev_receipt.target_authority_ref,
                    expected_source_revision=prev_receipt.expected_source_revision,
                    observed_source_revision=prev_receipt.observed_source_revision,
                    expected_target_fingerprint=prev_receipt.expected_target_fingerprint,
                    observed_target_fingerprint=prev_receipt.observed_target_fingerprint,
                    authorization_state=prev_receipt.authorization_state,
                    authorization_ref=prev_receipt.authorization_ref,
                    precondition_outcomes=prev_receipt.precondition_outcomes,
                    transaction_id=prev_receipt.transaction_id,
                    before_fingerprint=prev_receipt.before_fingerprint,
                    after_fingerprint=prev_receipt.after_fingerprint,
                    commit_state=prev_receipt.commit_state,
                    postcommit_verification_state=prev_receipt.postcommit_verification_state,
                    failure_envelope=prev_receipt.failure_envelope,
                    failure_code=prev_receipt.failure_code,
                    is_replay=True,
                    created_at=prev_receipt.created_at,
                )
            else:
                # Same key, different request content -> Conflict!
                self.state_machine.transition_to(
                    CutoverState.REJECTED,
                    reason=f"Idempotency conflict: key '{self.request.idempotency_key}' reused with different request payload.",
                )
                fail_env = PreconditionEnvelope(
                    passed=False,
                    outcomes={},
                    primary_failure_code=PreconditionFailureCode.IDEMPOTENCY_CONFLICT,
                    error_message=f"Idempotency conflict: key '{self.request.idempotency_key}' already bound to different fingerprint.",
                )
                return CutoverReceipt(
                    receipt_id=receipt_id,
                    request_id=self.request.request_id,
                    idempotency_key=self.request.idempotency_key,
                    mode=self.request.mode.value,
                    source_authority_ref=self.request.source_authority_ref,
                    target_authority_ref=self.request.target_authority_ref,
                    expected_source_revision=self.request.expected_source_revision,
                    observed_source_revision=self.store.get_revision(self.request.source_authority_ref),
                    expected_target_fingerprint=self.request.expected_target_fingerprint,
                    observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                    authorization_state="invalid",
                    authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                    precondition_outcomes=fail_env.to_dict()["outcomes"],
                    transaction_id=self.transaction_id,
                    before_fingerprint=self.store.fingerprint(),
                    after_fingerprint=self.store.fingerprint(),
                    commit_state="not_committed",
                    postcommit_verification_state="not_applicable",
                    failure_envelope=fail_env.to_dict(),
                    failure_code=PreconditionFailureCode.IDEMPOTENCY_CONFLICT.value,
                    is_replay=False,
                    created_at=now_iso,
                )

        # 2. Precondition Evaluation
        envelope = self.evaluator.evaluate(
            self.request,
            self.store,
            allow_synthetic_auth=self.allow_synthetic_auth,
        )

        auth_state = "none"
        if self.request.authorization:
            if self.request.authorization.is_synthetic:
                auth_state = "synthetic_test"
            elif self.request.authorization.status == "active":
                auth_state = "authorized"
            else:
                auth_state = self.request.authorization.status

        before_fp = self.store.fingerprint()

        if not envelope.passed:
            self.state_machine.transition_to(
                CutoverState.PRECONDITION_CHECKED,
                reason="Preconditions evaluated with failures.",
            )
            self.state_machine.transition_to(
                CutoverState.REJECTED,
                reason=envelope.error_message or "Precondition check failed.",
            )
            return CutoverReceipt(
                receipt_id=receipt_id,
                request_id=self.request.request_id,
                idempotency_key=self.request.idempotency_key,
                mode=self.request.mode.value,
                source_authority_ref=self.request.source_authority_ref,
                target_authority_ref=self.request.target_authority_ref,
                expected_source_revision=self.request.expected_source_revision,
                observed_source_revision=self.store.get_revision(self.request.source_authority_ref),
                expected_target_fingerprint=self.request.expected_target_fingerprint,
                observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                authorization_state=auth_state if envelope.primary_failure_code != PreconditionFailureCode.UNAUTHORIZED_ACTIVATION else "unauthorized",
                authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                precondition_outcomes=envelope.to_dict()["outcomes"],
                transaction_id=self.transaction_id,
                before_fingerprint=before_fp,
                after_fingerprint=before_fp,
                commit_state="not_committed",
                postcommit_verification_state="not_applicable",
                failure_envelope=envelope.to_dict(),
                failure_code=envelope.primary_failure_code.value if envelope.primary_failure_code else None,
                is_replay=False,
                created_at=now_iso,
            )

        self.state_machine.transition_to(
            CutoverState.PRECONDITION_CHECKED,
            reason="All preconditions passed verification.",
        )

        is_dry_run = (dry_run is True) or (self.request.mode == CutoverMode.DRY_RUN)

        # 3. Dry-Run Execution
        if is_dry_run:
            self.state_machine.transition_to(
                CutoverState.STAGED,
                reason="Dry-run: constructed staging plan without mutating authority.",
            )
            # Predict simulated after fingerprint using a clone
            simulated_store = self.store.clone()
            simulated_store._authorities[self.request.source_authority_ref] = self.request.target_authority_ref
            cur_rev = simulated_store.get_revision(self.request.source_authority_ref)
            simulated_store._revisions[self.request.source_authority_ref] = cur_rev + 1
            predicted_after_fp = simulated_store.fingerprint()

            receipt = CutoverReceipt(
                receipt_id=receipt_id,
                request_id=self.request.request_id,
                idempotency_key=self.request.idempotency_key,
                mode=CutoverMode.DRY_RUN.value,
                source_authority_ref=self.request.source_authority_ref,
                target_authority_ref=self.request.target_authority_ref,
                expected_source_revision=self.request.expected_source_revision,
                observed_source_revision=self.store.get_revision(self.request.source_authority_ref),
                expected_target_fingerprint=self.request.expected_target_fingerprint,
                observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                authorization_state=auth_state,
                authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                precondition_outcomes=envelope.to_dict()["outcomes"],
                transaction_id=self.transaction_id,
                before_fingerprint=before_fp,
                after_fingerprint=predicted_after_fp,
                commit_state="not_committed",
                postcommit_verification_state="not_applicable",
                failure_envelope=None,
                failure_code=None,
                is_replay=False,
                created_at=now_iso,
            )
            # Dry-run records in idempotency store as well
            self.store._idempotency_records[self.request.idempotency_key] = (req_fp, receipt)
            return receipt

        # 4. Activation Execution Path
        # Check injected precommit failure
        if inject_precommit_failure:
            self.state_machine.transition_to(
                CutoverState.STAGED,
                reason="Staging authority switch before injected abort.",
            )
            self.state_machine.transition_to(
                CutoverState.ABORTED_PRECOMMIT,
                reason="Precommit failure injected. Preserving original authority state.",
            )
            return CutoverReceipt(
                receipt_id=receipt_id,
                request_id=self.request.request_id,
                idempotency_key=self.request.idempotency_key,
                mode=CutoverMode.ACTIVATION.value,
                source_authority_ref=self.request.source_authority_ref,
                target_authority_ref=self.request.target_authority_ref,
                expected_source_revision=self.request.expected_source_revision,
                observed_source_revision=self.store.get_revision(self.request.source_authority_ref),
                expected_target_fingerprint=self.request.expected_target_fingerprint,
                observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                authorization_state=auth_state,
                authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                precondition_outcomes=envelope.to_dict()["outcomes"],
                transaction_id=self.transaction_id,
                before_fingerprint=before_fp,
                after_fingerprint=before_fp,
                commit_state="staged_aborted",
                postcommit_verification_state="not_applicable",
                failure_envelope={"error": "Injected precommit failure"},
                failure_code="injected_precommit_failure",
                is_replay=False,
                created_at=now_iso,
            )

        # Stage
        self.state_machine.transition_to(
            CutoverState.STAGED,
            reason="Staging atomic authority switch.",
        )

        # Commit: Atomic Authority Pointer Flip & Revision CAS
        current_rev = self.store.get_revision(self.request.source_authority_ref)
        if current_rev != self.request.expected_source_revision:
            self.state_machine.transition_to(
                CutoverState.ABORTED_PRECOMMIT,
                reason="CAS failure during commit phase. Stale revision detected.",
            )
            return CutoverReceipt(
                receipt_id=receipt_id,
                request_id=self.request.request_id,
                idempotency_key=self.request.idempotency_key,
                mode=CutoverMode.ACTIVATION.value,
                source_authority_ref=self.request.source_authority_ref,
                target_authority_ref=self.request.target_authority_ref,
                expected_source_revision=self.request.expected_source_revision,
                observed_source_revision=current_rev,
                expected_target_fingerprint=self.request.expected_target_fingerprint,
                observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                authorization_state=auth_state,
                authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                precondition_outcomes=envelope.to_dict()["outcomes"],
                transaction_id=self.transaction_id,
                before_fingerprint=before_fp,
                after_fingerprint=before_fp,
                commit_state="staged_aborted",
                postcommit_verification_state="not_applicable",
                failure_envelope={"error": "CAS revision mismatch during commit"},
                failure_code=PreconditionFailureCode.STALE_REVISION.value,
                is_replay=False,
                created_at=now_iso,
            )

        # Atomic commit mutation
        self.store._authorities[self.request.source_authority_ref] = self.request.target_authority_ref
        self.store._revisions[self.request.source_authority_ref] = current_rev + 1
        after_fp = self.store.fingerprint()

        self.state_machine.transition_to(
            CutoverState.COMMITTED,
            reason="Atomic authority pointer switch and revision increment committed.",
        )

        # Postcommit Verification
        active_target = self.store.get_active_authority(self.request.source_authority_ref)
        new_rev = self.store.get_revision(self.request.source_authority_ref)
        verification_passed = (
            active_target == self.request.target_authority_ref
            and new_rev == self.request.expected_source_revision + 1
            and not inject_postcommit_failure
        )

        if not verification_passed:
            self.state_machine.transition_to(
                CutoverState.COMMITTED_VERIFICATION_FAILED,
                reason="Postcommit verification failed. No silent rollback; explicit failure recorded.",
            )
            return CutoverReceipt(
                receipt_id=receipt_id,
                request_id=self.request.request_id,
                idempotency_key=self.request.idempotency_key,
                mode=CutoverMode.ACTIVATION.value,
                source_authority_ref=self.request.source_authority_ref,
                target_authority_ref=self.request.target_authority_ref,
                expected_source_revision=self.request.expected_source_revision,
                observed_source_revision=new_rev,
                expected_target_fingerprint=self.request.expected_target_fingerprint,
                observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
                authorization_state=auth_state,
                authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
                precondition_outcomes=envelope.to_dict()["outcomes"],
                transaction_id=self.transaction_id,
                before_fingerprint=before_fp,
                after_fingerprint=after_fp,
                commit_state="committed",
                postcommit_verification_state="failed",
                failure_envelope={"error": "Postcommit verification failed on committed state."},
                failure_code="postcommit_verification_failed",
                is_replay=False,
                created_at=now_iso,
            )

        self.state_machine.transition_to(
            CutoverState.POSTCOMMIT_VERIFIED,
            reason="Postcommit verification passed: target authority verified active and revision consistent.",
        )

        receipt = CutoverReceipt(
            receipt_id=receipt_id,
            request_id=self.request.request_id,
            idempotency_key=self.request.idempotency_key,
            mode=CutoverMode.ACTIVATION.value,
            source_authority_ref=self.request.source_authority_ref,
            target_authority_ref=self.request.target_authority_ref,
            expected_source_revision=self.request.expected_source_revision,
            observed_source_revision=new_rev,
            expected_target_fingerprint=self.request.expected_target_fingerprint,
            observed_target_fingerprint=self.store.get_target_fingerprint(self.request.target_authority_ref),
            authorization_state=auth_state,
            authorization_ref=self.request.authorization.auth_id if self.request.authorization else None,
            precondition_outcomes=envelope.to_dict()["outcomes"],
            transaction_id=self.transaction_id,
            before_fingerprint=before_fp,
            after_fingerprint=after_fp,
            commit_state="committed",
            postcommit_verification_state="verified",
            failure_envelope=None,
            failure_code=None,
            is_replay=False,
            created_at=now_iso,
        )

        self.store._idempotency_records[self.request.idempotency_key] = (req_fp, receipt)
        return receipt


__all__ = [
    "ProductionAliasingError",
    "CutoverTransactionStore",
    "CutoverTransaction",
]
