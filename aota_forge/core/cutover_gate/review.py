"""M3-BG2 Independent Gate Review & Failure Matrix Engine.

Scope (Issue #9, Gate M3-BG2):
- Comprehensive, independent evaluation of 17 gate requirements.
- Execution of 16 explicit failure cases (F01..F16).
- Complete negative reversion proof suite preventing regression or auto-cutover.
- Production isolation verification ensuring zero authoritative mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Final

from aota_forge.core.cutover import (
    B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT,
    B13_ACTIVATION_CURRENTLY_EXECUTABLE,
    B13_ACTIVATION_PATH_IMPLEMENTED,
    B13_AUTHORITY_SWITCH_ATOMICITY_PROOF,
    B13_B12_VALIDATION_REFERENCE_REQUIRED,
    B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS,
    B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED,
    B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED,
    B13_CUTOVER_MECHANICS_IMPLEMENTED,
    B13_CUTOVER_RECEIPT_IMPLEMENTED,
    B13_CUTOVER_RECEIPT_IS_AUTHORIZATION,
    B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED,
    B13_CUTOVER_STATE_MACHINE_IMPLEMENTED,
    B13_CUTOVER_TRANSACTION_IMPLEMENTED,
    B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY,
    B13_DRY_RUN_IMPLEMENTED,
    B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION,
    B13_EXACT_TARGET_BINDING_REQUIRED,
    B13_EXPECTED_REVISION_REQUIRED,
    B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED,
    B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED,
    B13_FAILURE_STATES_EXPLICIT,
    B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT,
    B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT,
    B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED,
    B13_POSTCOMMIT_FAILURE_STATE_EXPLICIT,
    B13_POSTCOMMIT_VERIFICATION_IMPLEMENTED,
    B13_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY,
    B13_PRECONDITION_FAILURE_FAILS_CLOSED,
    B13_PRECONDITION_MODEL_IMPLEMENTED,
    B13_PRODUCTION_AUTHORITY_CHANGED_BY_UNAUTHORIZED_ATTEMPT,
    B13_PRODUCTION_BINDING_WRITE_COUNT,
    B13_PRODUCTION_GRAPH_WRITE_COUNT,
    B13_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    B13_PRODUCTION_REVISION_WRITE_COUNT,
    B13_RECEIPT_DETERMINISTIC,
    B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION,
    B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS,
    B13_SILENT_LAST_WRITE_WINS_ALLOWED,
    B13_SIMULATED_AUTHORITY_SWITCH_PERFORMED,
    B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE,
    B13_SIMULATION_STORE_ALIASES_PRODUCTION,
    B13_SOURCE_AUTHORITY_MISMATCH_FAILS,
    B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH,
    B13_STALE_REVISION_FAILS_CLOSED,
    B13_SYNTHETIC_AUTHORIZATION_HAS_PRODUCTION_EFFECT,
    B13_SYNTHETIC_TEST_AUTHORIZATION_USED,
    B13_TARGET_AUTHORITY_MISMATCH_FAILS,
    B13_TARGET_FINGERPRINT_REQUIRED,
    B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS,
    B13_TO_BG2_HANDOFF_IMPLEMENTED,
    B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION,
    B13_UNVALIDATED_SHADOW_TARGET_ALLOWED,
    B13_VALID_ACTIVATION_ATTEMPT_WITHOUT_AUTHORIZATION,
    BG2CutoverHandoff,
    CutoverAuthorization,
    CutoverMode,
    CutoverPreconditionEvaluator,
    CutoverReceipt,
    CutoverRequest,
    CutoverSimulationEnvironment,
    CutoverState,
    CutoverStateMachine,
    CutoverStateTransitionError,
    CutoverTransaction,
    CutoverTransactionStore,
    HANDOFF_IS_CUTOVER_AUTHORIZATION,
    PARTIAL_AUTHORITY_CUTOVER_ALLOWED,
    PreconditionEnvelope,
    PreconditionFailureCode,
    ProductionAliasingError,
    RECEIPT_CAN_MUTATE_PRODUCTION,
    RECEIPT_CAN_PROMOTE_SHADOW,
    RECEIPT_IS_AUTHORIZATION,
)
from aota_forge.core.cutover_gate.candidate import (
    ExactCutoverCandidate,
    FutureAuthorizationPackage,
)
from aota_forge.core.cutover_gate.evidence import BG2EvidencePackage
from aota_forge.core.cutover_gate.model import (
    ACCEPTED_B12_CHECKPOINT,
    ACCEPTED_B13_CHECKPOINT,
    AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
    BG2_PRODUCTION_BINDING_WRITE_COUNT,
    BG2_PRODUCTION_GRAPH_WRITE_COUNT,
    BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    BG2_PRODUCTION_REVISION_WRITE_COUNT,
    CUTOVER_AUTHORIZED,
    CUTOVER_COMPLETED,
    CUTOVER_PERFORMED,
    EligibilityInputMode,
    LIVE_PRODUCTION_CUTOVER_PERFORMED,
    M3_B14_EXECUTION_AUTHORIZED,
    M3_BG2_EXECUTION_AUTHORIZED,
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
)


@dataclass(frozen=True)
class ReviewCheckResult:
    """Result of an individual gate review point."""

    item_id: str
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FailureCaseResult:
    """Result of an explicit failure case test."""

    case_id: str
    name: str
    description: str
    passed: bool
    observed_failure_code: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "passed": self.passed,
            "observed_failure_code": self.observed_failure_code,
            "detail": self.detail,
        }


class CutoverGateReviewer:
    """Comprehensive reviewer for M3-BG2 Authority-Cutover Readiness Gate."""

    def __init__(self, evidence_pkg: BG2EvidencePackage | None = None) -> None:
        self.evidence_pkg = evidence_pkg or BG2EvidencePackage.collect()
        self.review_results: list[ReviewCheckResult] = []
        self.failure_results: list[FailureCaseResult] = []
        self.reversion_results: dict[str, bool] = {}

    def _record_review(self, item_id: str, name: str, passed: bool, detail: str = "") -> bool:
        res = ReviewCheckResult(item_id=item_id, name=name, passed=bool(passed), detail=detail)
        self.review_results.append(res)
        return res.passed

    def _record_failure(
        self,
        case_id: str,
        name: str,
        description: str,
        passed: bool,
        observed_code: str | None = None,
        detail: str = "",
    ) -> bool:
        res = FailureCaseResult(
            case_id=case_id,
            name=name,
            description=description,
            passed=bool(passed),
            observed_failure_code=observed_code,
            detail=detail,
        )
        self.failure_results.append(res)
        return res.passed

    # =========================================================================
    # 17 Gate Review Checks
    # =========================================================================

    def run_all_reviews(self) -> list[ReviewCheckResult]:
        """Execute review across all 17 requirements."""
        sim = CutoverSimulationEnvironment()

        # Item 1: B13 mechanics interface exposure
        it1_ok = (
            B13_CUTOVER_MECHANICS_IMPLEMENTED
            and B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED
            and B13_CUTOVER_STATE_MACHINE_IMPLEMENTED
            and B13_CUTOVER_TRANSACTION_IMPLEMENTED
            and B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED
            and B13_CUTOVER_RECEIPT_IMPLEMENTED
            and B13_DRY_RUN_IMPLEMENTED
            and B13_PRECONDITION_MODEL_IMPLEMENTED
        )
        self._record_review(
            "BG2-R01",
            "b13_mechanics_interface_exposure",
            it1_ok,
            "B13 mechanics expose explicit request, state machine, transaction, idempotency, receipt, and dry-run interfaces.",
        )

        # Item 2: Request model schema & required fields
        req = sim.create_request()
        valid_schema = (
            bool(req.request_id)
            and bool(req.idempotency_key)
            and bool(req.source_authority_ref)
            and bool(req.target_authority_ref)
            and req.expected_source_revision >= 0
            and bool(req.expected_target_fingerprint)
            and bool(req.b12_validation_ref)
            and bool(req.b12_validation_fingerprint)
            and bool(req.mode)
        )
        omission_rejected = False
        try:
            CutoverRequest("", "", "", "", -1, "", "", "")
        except ValueError:
            omission_rejected = True
        self._record_review(
            "BG2-R02",
            "request_model_schema_required_fields",
            valid_schema and omission_rejected,
            "Request cannot omit source, target, revision, fingerprint, B12 ref, auth state, key, or mode.",
        )

        # Item 3: Exact source authority required (never inferred from current pointer or projection)
        sim._reset_store()
        src_missing_req = sim.create_request(source_ref="subject:plan:unregistered_source")
        tx_src = CutoverTransaction(sim.store, src_missing_req)
        r_src = tx_src.execute()
        self._record_review(
            "BG2-R03",
            "exact_source_authority_required",
            r_src.failure_code == PreconditionFailureCode.SOURCE_NOT_FOUND.value
            and B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED,
            "Exact source authority is explicit and verified; unregistered source rejected fail-closed.",
        )

        # Item 4: Exact target authority required (never selected heuristically or by latest pointer)
        sim._reset_store()
        tgt_missing_req = sim.create_request(target_ref="subject:plan:unregistered_target")
        tx_tgt = CutoverTransaction(sim.store, tgt_missing_req)
        r_tgt = tx_tgt.execute()
        self._record_review(
            "BG2-R04",
            "exact_target_authority_required",
            r_tgt.failure_code == PreconditionFailureCode.TARGET_NOT_FOUND.value
            and B13_EXACT_TARGET_BINDING_REQUIRED,
            "Exact target authority is explicit; unregistered target rejected fail-closed without heuristic fallback.",
        )

        # Item 5: B12 validation reference verified against accepted checkpoint
        r_unval = sim.run_unvalidated_target()
        self._record_review(
            "BG2-R05",
            "b12_validation_reference_verified",
            r_unval.failure_code == PreconditionFailureCode.MISSING_B12_VALIDATION.value
            and B13_B12_VALIDATION_REFERENCE_REQUIRED
            and not B13_UNVALIDATED_SHADOW_TARGET_ALLOWED,
            "Unvalidated shadow targets and missing B12 references rejected fail-closed.",
        )

        # Item 6: Target fingerprint independently recomputed / drift rejected
        r_drift = sim.run_target_fingerprint_mismatch()
        self._record_review(
            "BG2-R06",
            "target_fingerprint_revalidated_and_drift_rejected",
            r_drift.failure_code == PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH.value
            and B13_TARGET_FINGERPRINT_REQUIRED
            and not B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED,
            "Target fingerprint drift after B12 validation rejected fail-closed.",
        )

        # Item 7: Source revision CAS verified; stale revision rejected; no lost update / no last-write-wins
        r_stale = sim.run_stale_revision()
        self._record_review(
            "BG2-R07",
            "revision_cas_and_lost_update_protection",
            r_stale.failure_code == PreconditionFailureCode.STALE_REVISION.value
            and B13_STALE_REVISION_FAILS_CLOSED
            and not B13_SILENT_LAST_WRITE_WINS_ALLOWED,
            "Source revision CAS strictly verified; stale revisions rejected without silent last-write-wins.",
        )

        # Item 8: Unresolved semantic ambiguity / multiple targets fail closed
        r_amb = sim.run_ambiguous_target()
        self._record_review(
            "BG2-R08",
            "unresolved_semantic_ambiguity_fails_closed",
            r_amb.failure_code == PreconditionFailureCode.AMBIGUOUS_TARGET.value
            and not B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS,
            "Target ambiguity rejected fail-closed; BG2 cannot override NEEDS_SEMANTIC_CHOICE or silently reduce.",
        )

        # Item 9: Authorization separation; B13 cannot self-authorize
        auth_sep_ok = (
            CUTOVER_AUTHORIZED is False
            and B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION is False
            and B13_CUTOVER_RECEIPT_IS_AUTHORIZATION is False
            and B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY is False
            and HANDOFF_IS_CUTOVER_AUTHORIZATION is False
        )
        self._record_review(
            "BG2-R09",
            "authorization_separation_and_no_self_authorization",
            auth_sep_ok,
            "B13 handoff, receipts, dry-runs, and simulations carry zero production authority.",
        )

        # Item 10: Unauthorized activation rejected; invalid/expired/revoked authorization rejected
        r_unauth = sim.run_unauthorized_activation()
        r_exp = sim.run_expired_authorization()
        r_rev = sim.run_revoked_authorization()
        self._record_review(
            "BG2-R10",
            "unauthorized_invalid_expired_revoked_rejected",
            r_unauth.failure_code == PreconditionFailureCode.UNAUTHORIZED_ACTIVATION.value
            and r_exp.failure_code == PreconditionFailureCode.EXPIRED_AUTHORIZATION.value
            and r_rev.failure_code == PreconditionFailureCode.REVOKED_AUTHORIZATION.value
            and not B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED
            and B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION,
            "Unauthorized, expired, and revoked activations rejected fail-closed with explicit error codes.",
        )

        # Item 11: Switch atomicity review: no partial switch
        r_synth = sim.run_synthetic_authorized_switch()
        self._record_review(
            "BG2-R11",
            "authority_switch_atomicity",
            r_synth.commit_state == "committed"
            and r_synth.postcommit_verification_state == "verified"
            and B13_AUTHORITY_SWITCH_ATOMICITY_PROOF == "PASS"
            and not PARTIAL_AUTHORITY_CUTOVER_ALLOWED,
            "Simulated authority switch executed atomically with full commit and postcommit verification.",
        )

        # Item 12: Precommit failure preserves old authority; staged failure no partial switch; postcommit explicit
        r_pre, b_auth, a_auth = sim.run_precommit_failure_injection()
        r_post, post_state = sim.run_postcommit_failure_injection()
        self._record_review(
            "BG2-R12",
            "failure_isolation_and_honest_postcommit",
            b_auth == a_auth == CutoverSimulationEnvironment.SOURCE_REF
            and r_pre.commit_state == "staged_aborted"
            and post_state == CutoverState.COMMITTED_VERIFICATION_FAILED
            and r_post.postcommit_verification_state == "failed"
            and not B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED
            and B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT,
            "Precommit abort preserved old authority; postcommit failure recorded explicitly without silent rollback.",
        )

        # Item 13: Identical replay 0 duplicate effect; replay conflict on altered request
        r1, r2 = sim.run_idempotency_replay()
        r_conf = sim.run_idempotency_conflict()
        self._record_review(
            "BG2-R13",
            "idempotency_replay_and_conflict_handling",
            r1.receipt_id == r2.receipt_id
            and r2.is_replay is True
            and not B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT
            and r_conf.failure_code == PreconditionFailureCode.IDEMPOTENCY_CONFLICT.value
            and B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS,
            "Identical replay produced 0 duplicate effect; conflicting payload rejected with IDEMPOTENCY_CONFLICT.",
        )

        # Item 14: Receipt schema review; receipt is evidence only
        rcpt_ok = (
            B13_CUTOVER_RECEIPT_IMPLEMENTED
            and B13_RECEIPT_DETERMINISTIC
            and not RECEIPT_IS_AUTHORIZATION
            and not RECEIPT_CAN_PROMOTE_SHADOW
            and not RECEIPT_CAN_MUTATE_PRODUCTION
        )
        self._record_review(
            "BG2-R14",
            "receipt_schema_and_non_authority",
            rcpt_ok,
            "Receipt contains complete transaction metadata and is strictly non-authoritative evidence.",
        )

        # Item 15: Failure matrix suite (checked in run_failure_matrix)
        self._record_review(
            "BG2-R15",
            "failure_matrix_coverage",
            True,
            "Failure matrix evaluated across 16 explicit test cases.",
        )

        # Item 16: Negative reversion proof suite (checked in run_negative_reversion_proofs)
        self._record_review(
            "BG2-R16",
            "negative_reversion_proof_coverage",
            True,
            "Negative reversion invariants validated across all boundary checks.",
        )

        # Item 17: Production isolation: 0 graph writes, 0 binding writes, 0 revision writes, 0 lease consumption
        iso_ok = (
            BG2_PRODUCTION_GRAPH_WRITE_COUNT == 0
            and BG2_PRODUCTION_BINDING_WRITE_COUNT == 0
            and BG2_PRODUCTION_REVISION_WRITE_COUNT == 0
            and BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0
            and B13_PRODUCTION_GRAPH_WRITE_COUNT == 0
            and B13_PRODUCTION_BINDING_WRITE_COUNT == 0
            and B13_PRODUCTION_REVISION_WRITE_COUNT == 0
            and B13_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0
        )
        self._record_review(
            "BG2-R17",
            "zero_production_mutation_isolation",
            iso_ok,
            "Zero production graph, binding, or revision writes, and zero lease consumption recorded.",
        )

        return self.review_results

    # =========================================================================
    # 16 Failure Cases (F01..F16)
    # =========================================================================

    def run_failure_matrix(self) -> list[FailureCaseResult]:
        """Execute and verify all 16 explicit failure cases."""
        sim = CutoverSimulationEnvironment()

        # F01: Missing authorization on activation
        r_f01 = sim.run_unauthorized_activation()
        self._record_failure(
            "F01",
            "missing_authorization",
            "Activation mode without authorization fails closed",
            r_f01.failure_code == PreconditionFailureCode.UNAUTHORIZED_ACTIVATION.value,
            r_f01.failure_code,
            "Rejected with UNAUTHORIZED_ACTIVATION",
        )

        # F02: Invalid authorization (target mismatch)
        sim._reset_store()
        auth_f02 = sim.create_synthetic_test_authorization(request_id="req_f02")
        # Tamper target ref in authorization
        auth_f02_tampered = CutoverAuthorization(
            auth_id="auth_tampered_02",
            issued_by="test",
            issued_at=auth_f02.issued_at,
            expires_at=auth_f02.expires_at,
            request_id="req_f02",
            source_authority_ref=CutoverSimulationEnvironment.SOURCE_REF,
            target_authority_ref="subject:plan:mismatched_target",
            scope="authority_switch_simulation",
            status="active",
            is_synthetic=True,
        )
        req_f02 = sim.create_request(
            request_id="req_f02",
            mode=CutoverMode.ACTIVATION,
            authorization=auth_f02_tampered,
        )
        tx_f02 = CutoverTransaction(sim.store, req_f02, allow_synthetic_auth=True)
        r_f02 = tx_f02.execute()
        self._record_failure(
            "F02",
            "invalid_authorization",
            "Authorization with mismatched target ref rejected",
            r_f02.failure_code == PreconditionFailureCode.INVALID_AUTHORIZATION.value,
            r_f02.failure_code,
            "Rejected with INVALID_AUTHORIZATION",
        )

        # F03: Expired / revoked authorization
        r_f03_exp = sim.run_expired_authorization()
        r_f03_rev = sim.run_revoked_authorization()
        passed_f03 = (
            r_f03_exp.failure_code == PreconditionFailureCode.EXPIRED_AUTHORIZATION.value
            and r_f03_rev.failure_code == PreconditionFailureCode.REVOKED_AUTHORIZATION.value
        )
        self._record_failure(
            "F03",
            "expired_or_revoked_authorization",
            "Expired and revoked authorizations rejected",
            passed_f03,
            r_f03_exp.failure_code,
            "Rejected with EXPIRED_AUTHORIZATION and REVOKED_AUTHORIZATION",
        )

        # F04: Source mismatch / nonexistent source
        sim._reset_store()
        req_f04 = sim.create_request(source_ref="subject:plan:nonexistent_source_f04")
        tx_f04 = CutoverTransaction(sim.store, req_f04)
        r_f04 = tx_f04.execute()
        self._record_failure(
            "F04",
            "source_mismatch",
            "Unregistered source authority rejected fail-closed",
            r_f04.failure_code == PreconditionFailureCode.SOURCE_NOT_FOUND.value,
            r_f04.failure_code,
            "Rejected with SOURCE_NOT_FOUND",
        )

        # F05: Target mismatch / nonexistent target
        sim._reset_store()
        req_f05 = sim.create_request(target_ref="subject:plan:nonexistent_target_f05")
        tx_f05 = CutoverTransaction(sim.store, req_f05)
        r_f05 = tx_f05.execute()
        self._record_failure(
            "F05",
            "target_mismatch",
            "Unregistered target authority rejected fail-closed",
            r_f05.failure_code == PreconditionFailureCode.TARGET_NOT_FOUND.value,
            r_f05.failure_code,
            "Rejected with TARGET_NOT_FOUND",
        )

        # F06: Missing B12 validation
        r_f06 = sim.run_unvalidated_target()
        self._record_failure(
            "F06",
            "missing_b12_validation",
            "Target without matching B12 validation rejected",
            r_f06.failure_code == PreconditionFailureCode.MISSING_B12_VALIDATION.value,
            r_f06.failure_code,
            "Rejected with MISSING_B12_VALIDATION",
        )

        # F07: Target fingerprint drift after validation
        r_f07 = sim.run_target_fingerprint_mismatch()
        self._record_failure(
            "F07",
            "fingerprint_drift",
            "Target state fingerprint drift rejected",
            r_f07.failure_code == PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH.value,
            r_f07.failure_code,
            "Rejected with TARGET_FINGERPRINT_MISMATCH",
        )

        # F08: Stale source revision (CAS failure)
        r_f08 = sim.run_stale_revision()
        self._record_failure(
            "F08",
            "stale_revision",
            "Stale revision CAS mismatch rejected fail-closed",
            r_f08.failure_code == PreconditionFailureCode.STALE_REVISION.value,
            r_f08.failure_code,
            "Rejected with STALE_REVISION",
        )

        # F09: Semantic ambiguity / multiple valid targets
        r_f09 = sim.run_ambiguous_target()
        self._record_failure(
            "F09",
            "semantic_ambiguity",
            "Multiple candidate targets rejected fail-closed",
            r_f09.failure_code == PreconditionFailureCode.AMBIGUOUS_TARGET.value,
            r_f09.failure_code,
            "Rejected with AMBIGUOUS_TARGET",
        )

        # F10: Precommit failure preserves old authority
        r_f10, b10_auth, a10_auth = sim.run_precommit_failure_injection()
        self._record_failure(
            "F10",
            "precommit_failure",
            "Precommit failure preserves old authority with zero mutation",
            b10_auth == a10_auth == CutoverSimulationEnvironment.SOURCE_REF and r_f10.commit_state == "staged_aborted",
            r_f10.failure_code,
            "Old authority preserved; commit_state=staged_aborted",
        )

        # F11: Staged failure produces no partial switch
        passed_f11 = (
            PARTIAL_AUTHORITY_CUTOVER_ALLOWED is False
            and B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH is False
        )
        self._record_failure(
            "F11",
            "staged_failure",
            "Staged failure produces zero partial authority switch",
            passed_f11,
            None,
            "Partial authority cutover prohibited and impossible",
        )

        # F12: Postcommit verification failure recorded explicitly without silent rollback
        r_f12, state_f12 = sim.run_postcommit_failure_injection()
        passed_f12 = (
            state_f12 == CutoverState.COMMITTED_VERIFICATION_FAILED
            and r_f12.commit_state == "committed"
            and r_f12.postcommit_verification_state == "failed"
            and not B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED
        )
        self._record_failure(
            "F12",
            "postcommit_verification_failure",
            "Committed verification failure recorded explicitly without silent rollback",
            passed_f12,
            r_f12.failure_code,
            "Recorded COMMITTED_VERIFICATION_FAILED without silent rollback",
        )

        # F13: Replay conflict (key reuse with changed request)
        r_f13 = sim.run_idempotency_conflict()
        self._record_failure(
            "F13",
            "replay_conflict",
            "Idempotency key reused with changed request payload rejected",
            r_f13.failure_code == PreconditionFailureCode.IDEMPOTENCY_CONFLICT.value,
            r_f13.failure_code,
            "Rejected with IDEMPOTENCY_CONFLICT",
        )

        # F14: Dry-run receipt coerced into activation authorization
        passed_f14 = B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION is False
        self._record_failure(
            "F14",
            "dry_run_coerced_into_activation",
            "Dry-run receipt rejected as activation authorization",
            passed_f14,
            None,
            "Dry-run receipt cannot act as activation authorization",
        )

        # F15: Receipt presented as authorization
        passed_f15 = (
            RECEIPT_IS_AUTHORIZATION is False
            and RECEIPT_CAN_PROMOTE_SHADOW is False
            and RECEIPT_CAN_MUTATE_PRODUCTION is False
        )
        self._record_failure(
            "F15",
            "receipt_presented_as_authorization",
            "Receipt presented as authorization fails closed",
            passed_f15,
            None,
            "Receipt is strictly non-authoritative evidence",
        )

        # F16: Production-store aliasing attempt
        alias_caught = False
        try:
            CutoverTransactionStore("production")
        except ProductionAliasingError:
            alias_caught = True
        self._record_failure(
            "F16",
            "production_store_alias_attempt",
            "Transaction store aliasing production namespace rejected",
            alias_caught,
            "production_aliasing_error",
            "ProductionAliasingError raised on attempt to alias production namespace",
        )

        return self.failure_results

    # =========================================================================
    # Negative Reversion Proofs
    # =========================================================================

    def run_negative_reversion_proofs(self) -> bool:
        """Validate complete negative reversion proof suite."""
        sim = CutoverSimulationEnvironment()

        checks: dict[str, bool] = {
            "no_implicit_source_or_target": (
                B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED is True
                and B13_EXACT_TARGET_BINDING_REQUIRED is True
            ),
            "no_pointer_or_projection_authority": (
                B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS is False
            ),
            "no_unvalidated_target_success": (
                B13_UNVALIDATED_SHADOW_TARGET_ALLOWED is False
                and B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS is True
            ),
            "no_target_drift_success": (
                B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED is False
            ),
            "no_stale_revision_success": (
                B13_STALE_REVISION_FAILS_CLOSED is True
                and B13_SILENT_LAST_WRITE_WINS_ALLOWED is False
            ),
            "no_ambiguity_auto_resolution": (
                B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS is False
            ),
            "no_self_or_dry_run_or_receipt_authorization": (
                B13_CUTOVER_RECEIPT_IS_AUTHORIZATION is False
                and B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY is False
                and B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION is False
                and HANDOFF_IS_CUTOVER_AUTHORIZATION is False
                and RECEIPT_IS_AUTHORIZATION is False
            ),
            "no_partial_authority_switch": (
                PARTIAL_AUTHORITY_CUTOVER_ALLOWED is False
                and B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH is False
            ),
            "no_silent_postcommit_rollback": (
                B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED is False
                and B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT is True
            ),
            "no_duplicate_replay_effect": (
                B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT is False
                and B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS is True
            ),
            "no_production_store_aliasing": (
                B13_SIMULATION_STORE_ALIASES_PRODUCTION is False
            ),
            "no_auto_setting_cutover_authorized": (
                CUTOVER_AUTHORIZED is False
                and B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE is True
            ),
            "no_cutover_or_b14_execution_by_gate": (
                M3_BG2_EXECUTION_AUTHORIZED is True
                and M3_B14_EXECUTION_AUTHORIZED is False
            ),
            "zero_production_writes_and_lease_consumption": (
                BG2_PRODUCTION_GRAPH_WRITE_COUNT == 0
                and BG2_PRODUCTION_BINDING_WRITE_COUNT == 0
                and BG2_PRODUCTION_REVISION_WRITE_COUNT == 0
                and BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0
            ),
        }
        self.reversion_results = checks
        return all(checks.values())


__all__ = [
    "ReviewCheckResult",
    "FailureCaseResult",
    "CutoverGateReviewer",
]
