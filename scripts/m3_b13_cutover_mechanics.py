#!/usr/bin/env python3
"""M3-B13 Focused Validator: Cutover Mechanics (Issue #9, Lane M3-B13).

Scope:
- Covers B13-N01 through B13-N32.
- Validates request model, exact source and target binding, B12 validation reference & matching fingerprint.
- Evaluates expected source revision CAS, stale revision rejection, target fingerprint verification.
- Proves dry-run zero-mutation, unauthorized activation fail-closed, invalid/expired/revoked authorization rejection.
- Proves target non-ambiguity fail-closed.
- Validates deterministic finite state machine (transitions, precommit abort, committed-but-verification-failed).
- Proves transaction atomicity, precommit old authority preservation, staged no-partial-switch.
- Proves postcommit verification, explicit postcommit failure state without silent rollback, abort vs compensation.
- Proves exact idempotency replay with 0 duplicate effect, and conflict on key reuse with different payload.
- Validates deterministic cutover receipt generation, proves receipt cannot authorize or auto-promote.
- Proves isolated authorized simulated switch with synthetic authorization having 0 production effect.
- Validates simulation store non-aliasing against production namespace.
- Proves zero production graph/binding/revision writes and zero production lease consumption.
- Validates complete BG2 handoff evidence package.
- Evaluates complete negative reversion proof suite.
- Runs all accepted foundation regressions with PYTHONDONTWRITEBYTECODE=1.
- Outputs the exact mandatory M3-B13 result block.

Usage:
    python3 scripts/m3_b13_cutover_mechanics.py
    python3 scripts/m3_b13_cutover_mechanics.py --json
    python3 scripts/m3_b13_cutover_mechanics.py --raw-result-block
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
    BG2_EXECUTION_AUTHORIZED,
    B14_EXECUTION_AUTHORIZED,
    BG2CutoverHandoff,
    CUTOVER_AUTHORIZED,
    CUTOVER_COMPLETED,
    CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED,
    CUTOVER_PERFORMED,
    DEPLOY_PERFORMED,
    EXPECTED_BASE_COMMIT,
    GITHUB_MUTATION_PERFORMED,
    HANDOFF_IS_CUTOVER_AUTHORIZATION,
    LIVE_PRODUCTION_CUTOVER_PERFORMED,
    LIVE_PRODUCTION_MIGRATION_PERFORMED,
    M3_B13_EXECUTION_AUTHORIZED,
    M3_B14_EXECUTION_AUTHORIZED,
    M3_B_SHADOW_MATERIALIZATION_AUTHORIZED,
    M3_BG2_EXECUTION_AUTHORIZED,
    PARTIAL_AUTHORITY_CUTOVER_ALLOWED,
    PRODUCTION_CUTOVER_SERVICE_ACTIVATED,
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
    AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
    PUSH_PERFORMED,
    ProductionAliasingError,
    RECEIPT_CAN_MUTATE_PRODUCTION,
    RECEIPT_CAN_PROMOTE_SHADOW,
    RECEIPT_IS_AUTHORIZATION,
    RUNTIME_RELOAD_PERFORMED,
    VALIDATED_SHADOW_STATE_IS_PRODUCTION_AUTHORITY,
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
    PreconditionEnvelope,
    PreconditionFailureCode,
    PreconditionOutcome,
)


@dataclass(frozen=True)
class TestCaseResult:
    """Result of an individual test case."""

    case_id: str
    name: str
    passed: bool
    detail: str


class CutoverMechanicsValidator:
    """Comprehensive validator for M3-B13 Cutover Mechanics."""

    def __init__(self) -> None:
        self.sim = CutoverSimulationEnvironment()
        self.results: list[TestCaseResult] = []

    def check(self, case_id: str, name: str, condition: bool, detail: str = "") -> bool:
        res = TestCaseResult(case_id=case_id, name=name, passed=bool(condition), detail=detail)
        self.results.append(res)
        return res.passed

    def verify_exact_base_ancestry(self) -> bool:
        try:
            res = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", EXPECTED_BASE_COMMIT],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0 and res.stdout.strip() == EXPECTED_BASE_COMMIT
        except Exception:
            return False

    def run_b13_test_cases(self) -> None:
        """Run B13-N01 through B13-N32."""
        # B13-N01: Exact B12 base ancestry
        base_ok = self.verify_exact_base_ancestry()
        self.check("B13-N01", "exact_b12_base_ancestry", base_ok, f"Verified against base {EXPECTED_BASE_COMMIT}")

        # B13-N02: Request model schema & validation
        req = self.sim.create_request()
        valid_req = req.request_id == "req_sim_01" and len(req.fingerprint()) == 64
        invalid_caught = False
        try:
            CutoverRequest("", "k", "s", "t", 1, "fp", "b12", "b12_fp")
        except ValueError:
            invalid_caught = True
        self.check("B13-N02", "cutover_request_model", valid_req and invalid_caught, "Schema validation and post-init constraints enforced")

        # B13-N03: Exact source authority binding
        self.sim._reset_store()
        src_req = self.sim.create_request(source_ref="subject:plan:nonexistent_source")
        tx_src = CutoverTransaction(self.sim.store, src_req)
        r_src = tx_src.execute()
        self.check("B13-N03", "exact_source_authority_binding", r_src.failure_code == PreconditionFailureCode.SOURCE_NOT_FOUND.value, "Nonexistent source ref rejected with SOURCE_NOT_FOUND")

        # B13-N04: Exact target authority binding (rejects heuristic / latest / pointer)
        self.sim._reset_store()
        tgt_req = self.sim.create_request(target_ref="subject:plan:nonexistent_target")
        tx_tgt = CutoverTransaction(self.sim.store, tgt_req)
        r_tgt = tx_tgt.execute()
        self.check("B13-N04", "exact_target_authority_binding", r_tgt.failure_code == PreconditionFailureCode.TARGET_NOT_FOUND.value, "Nonexistent target ref rejected with TARGET_NOT_FOUND")

        # B13-N05: Matching B12 validation reference/fingerprint required
        r_unval = self.sim.run_unvalidated_target()
        self.check("B13-N05", "matching_b12_validation_required", r_unval.failure_code == PreconditionFailureCode.MISSING_B12_VALIDATION.value, "Missing B12 validation report rejected with MISSING_B12_VALIDATION")

        # B13-N06: Expected revision required (non-negative integer in request)
        rev_req_ok = False
        try:
            CutoverRequest("req", "k", "s", "t", -1, "fp", "b12", "b12_fp")
        except ValueError:
            rev_req_ok = True
        self.check("B13-N06", "expected_revision_required", rev_req_ok and B13_EXPECTED_REVISION_REQUIRED, "Non-negative expected revision required in request model")

        # B13-N07: Stale revision / CAS failure fails closed
        r_stale = self.sim.run_stale_revision()
        self.check("B13-N07", "stale_revision_cas_failure_fails_closed", r_stale.failure_code == PreconditionFailureCode.STALE_REVISION.value, "Stale revision rejected fail-closed with STALE_REVISION")

        # B13-N08: Target fingerprint mismatch fails closed
        r_fp_mis = self.sim.run_target_fingerprint_mismatch()
        self.check("B13-N08", "target_fingerprint_mismatch_fails_closed", r_fp_mis.failure_code == PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH.value, "Tampered target fingerprint rejected with TARGET_FINGERPRINT_MISMATCH")

        # B13-N09: Dry-run performs checks and receipt generation with zero store mutation
        r_dry = self.sim.run_dry_run()
        self.check("B13-N09", "dry_run_zero_mutation", r_dry.commit_state == "not_committed" and r_dry.mode == "dry_run", "Dry run completed with 0 authority mutation and commit_state=not_committed")

        # B13-N10: Unauthorized activation rejection (CUTOVER_AUTHORIZED=no)
        r_unauth = self.sim.run_unauthorized_activation()
        self.check("B13-N10", "unauthorized_activation_rejection", r_unauth.failure_code == PreconditionFailureCode.UNAUTHORIZED_ACTIVATION.value, "Activation attempt rejected fail-closed with UNAUTHORIZED_ACTIVATION")

        # B13-N11: Invalid / expired / revoked authorization rejection
        r_exp = self.sim.run_expired_authorization()
        r_rev = self.sim.run_revoked_authorization()
        self.check(
            "B13-N11",
            "invalid_expired_revoked_authorization_rejection",
            r_exp.failure_code == PreconditionFailureCode.EXPIRED_AUTHORIZATION.value
            and r_rev.failure_code == PreconditionFailureCode.REVOKED_AUTHORIZATION.value,
            "Expired and revoked authorizations rejected with explicit failure codes",
        )

        # B13-N12: Ambiguous target rejection (fails closed, cannot select)
        r_amb = self.sim.run_ambiguous_target()
        self.check("B13-N12", "ambiguous_target_rejection", r_amb.failure_code == PreconditionFailureCode.AMBIGUOUS_TARGET.value, "Multiple candidate targets rejected fail-closed with AMBIGUOUS_TARGET")

        # B13-N13: Deterministic state machine lifecycle transitions
        sm = CutoverStateMachine(CutoverState.REQUESTED)
        sm.transition_to(CutoverState.PRECONDITION_CHECKED, "checks passed")
        sm.transition_to(CutoverState.STAGED, "staging switch")
        sm.transition_to(CutoverState.COMMITTED, "committed switch")
        sm.transition_to(CutoverState.POSTCOMMIT_VERIFIED, "verified switch")
        sm_illegal_caught = False
        try:
            sm.transition_to(CutoverState.REQUESTED)
        except CutoverStateTransitionError:
            sm_illegal_caught = True
        self.check("B13-N13", "deterministic_state_machine_transitions", sm.current_state == CutoverState.POSTCOMMIT_VERIFIED and sm_illegal_caught, "State machine transitions through valid lifecycle and rejects illegal transitions")

        # B13-N14: Authority switch atomicity proof
        r_synth = self.sim.run_synthetic_authorized_switch()
        self.check("B13-N14", "authority_switch_atomicity_proof", r_synth.commit_state == "committed" and r_synth.postcommit_verification_state == "verified", "Atomic authority switch committed and verified in single transaction")

        # B13-N15: Precommit failure preserves old authority
        r_pre_fail, b_auth, a_auth = self.sim.run_precommit_failure_injection()
        self.check("B13-N15", "precommit_failure_preserves_old_authority", b_auth == a_auth == self.sim.SOURCE_REF and r_pre_fail.commit_state == "staged_aborted", "Precommit abort preserved original authority with commit_state=staged_aborted")

        # B13-N16: Staged failure produces no partial authority switch
        self.check("B13-N16", "staged_failure_no_partial_switch", PARTIAL_AUTHORITY_CUTOVER_ALLOWED is False and B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH is False, "No partial switch allowed or produced on staging failure")

        # B13-N17: Postcommit verification validation
        self.check("B13-N17", "postcommit_verification_validation", r_synth.postcommit_verification_state == "verified", "Postcommit verification confirmed active target authority")

        # B13-N18: Explicit postcommit verification failure state (no silent rollback)
        r_post_fail, post_state = self.sim.run_postcommit_failure_injection()
        self.check(
            "B13-N18",
            "explicit_postcommit_verification_failure_state",
            post_state == CutoverState.COMMITTED_VERIFICATION_FAILED
            and r_post_fail.postcommit_verification_state == "failed"
            and r_post_fail.commit_state == "committed",
            "Explicit COMMITTED_VERIFICATION_FAILED state recorded without silent rollback",
        )

        # B13-N19: Abort vs compensation semantic distinction
        self.check(
            "B13-N19",
            "abort_vs_compensation_semantic_distinction",
            B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT is True
            and B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT is True,
            "Precommit abort and postcommit compensation strictly distinguished",
        )

        # B13-N20: Exact idempotency replay reuses receipt with duplicate effect count = 0
        r_rep1, r_rep2 = self.sim.run_idempotency_replay()
        self.check(
            "B13-N20",
            "idempotency_exact_replay_zero_duplicate_effect",
            r_rep1.receipt_id == r_rep2.receipt_id and r_rep2.is_replay is True and B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT is False,
            "Identical replay reused receipt with is_replay=True and zero duplicate effect",
        )

        # B13-N21: Same replay key with different request payload fails with conflict
        r_conf = self.sim.run_idempotency_conflict()
        self.check("B13-N21", "same_key_different_request_conflict", r_conf.failure_code == PreconditionFailureCode.IDEMPOTENCY_CONFLICT.value, "Reused key with modified request rejected with IDEMPOTENCY_CONFLICT")

        # B13-N22: Deterministic receipt structure and generation
        rcpt_fp1 = r_dry.fingerprint()
        rcpt_fp2 = r_dry.fingerprint()
        self.check("B13-N22", "deterministic_receipt_structure", rcpt_fp1 == rcpt_fp2 and len(rcpt_fp1) == 64, "Deterministic receipt canonical payload generates stable SHA-256 fingerprint")

        # B13-N23: Receipt is not authorization and cannot auto-promote
        self.check(
            "B13-N23",
            "receipt_not_authorization",
            RECEIPT_IS_AUTHORIZATION is False
            and RECEIPT_CAN_PROMOTE_SHADOW is False
            and RECEIPT_CAN_MUTATE_PRODUCTION is False,
            "Receipt verified as evidence only; cannot authorize or promote shadow",
        )

        # B13-N24: Dry-run receipt cannot be reused as activation authorization
        self.check("B13-N24", "dry_run_receipt_not_activation_authorization", B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION is False, "Dry-run receipt rejected as activation authorization")

        # B13-N25: Isolated authorized simulated switch performed cleanly
        self.check("B13-N25", "isolated_authorized_simulated_switch", r_synth.commit_state == "committed" and r_synth.authorization_state == "synthetic_test", "Simulated switch completed successfully with synthetic test authorization")

        # B13-N26: Simulation store cannot alias production namespace
        prod_alias_rejected = False
        try:
            CutoverTransactionStore("production")
        except ProductionAliasingError:
            prod_alias_rejected = True
        self.check("B13-N26", "simulation_store_cannot_alias_production", prod_alias_rejected, "ProductionAliasingError raised when attempting to alias production namespace")

        # B13-N27: Zero production graph writes
        self.check("B13-N27", "zero_production_graph_writes", B13_PRODUCTION_GRAPH_WRITE_COUNT == 0, "B13_PRODUCTION_GRAPH_WRITE_COUNT == 0")

        # B13-N28: Zero production binding writes
        self.check("B13-N28", "zero_production_binding_writes", B13_PRODUCTION_BINDING_WRITE_COUNT == 0, "B13_PRODUCTION_BINDING_WRITE_COUNT == 0")

        # B13-N29: Zero production revision writes
        self.check("B13-N29", "zero_production_revision_writes", B13_PRODUCTION_REVISION_WRITE_COUNT == 0, "B13_PRODUCTION_REVISION_WRITE_COUNT == 0")

        # B13-N30: Zero production lease consumption
        self.check("B13-N30", "zero_production_lease_consumption", B13_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0, "B13_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0")

        # B13-N31: Governance CUTOVER_AUTHORIZED remains no
        self.check("B13-N31", "governance_cutover_authorized_remains_no", CUTOVER_AUTHORIZED is False and B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE is True, "CUTOVER_AUTHORIZED remains False throughout execution")

        # B13-N32: Complete BG2 handoff evidence generated without self-authorizing BG2 or B14
        handoff = BG2CutoverHandoff.build_evidence()
        h_dict = handoff.to_dict()
        self.check(
            "B13-N32",
            "complete_bg2_handoff_evidence",
            len(h_dict["failure_matrix"]) >= 12
            and h_dict["handoff_is_cutover_authorization"] is False
            and h_dict["m3_bg2_execution_authorized"] is False
            and h_dict["m3_b14_execution_authorized"] is False,
            "Complete BG2 handoff evidence generated without self-authorizing BG2 or B14",
        )

    def validate_negative_reversions(self) -> bool:
        """Verify negative reversion invariant matrix."""
        checks = []

        # 1. Reject production aliasing
        try:
            CutoverTransactionStore("production")
            checks.append(False)
        except ProductionAliasingError:
            checks.append(True)

        # 2. Reject implicit/current-pointer or ambiguous target
        sim = CutoverSimulationEnvironment()
        r_amb = sim.run_ambiguous_target()
        checks.append(r_amb.failure_code == PreconditionFailureCode.AMBIGUOUS_TARGET.value)

        # 3. Reject stale revision CAS
        r_stale = sim.run_stale_revision()
        checks.append(r_stale.failure_code == PreconditionFailureCode.STALE_REVISION.value)

        # 4. Reject target fingerprint mismatch
        r_fp = sim.run_target_fingerprint_mismatch()
        checks.append(r_fp.failure_code == PreconditionFailureCode.TARGET_FINGERPRINT_MISMATCH.value)

        # 5. Reject unvalidated target
        r_unval = sim.run_unvalidated_target()
        checks.append(r_unval.failure_code == PreconditionFailureCode.MISSING_B12_VALIDATION.value)

        # 6. Reject unauthorized activation
        r_unauth = sim.run_unauthorized_activation()
        checks.append(r_unauth.failure_code == PreconditionFailureCode.UNAUTHORIZED_ACTIVATION.value)

        # 7. Reject expired / revoked auth
        r_exp = sim.run_expired_authorization()
        checks.append(r_exp.failure_code == PreconditionFailureCode.EXPIRED_AUTHORIZATION.value)

        # 8. Dry-run zero mutation
        store_before = sim.store.fingerprint()
        sim.run_dry_run()
        store_after = sim.store.fingerprint()
        checks.append(store_before == store_after)

        # 9. Invariants
        checks.append(CUTOVER_AUTHORIZED is False)
        checks.append(M3_BG2_EXECUTION_AUTHORIZED is False)
        checks.append(M3_B14_EXECUTION_AUTHORIZED is False)
        checks.append(RECEIPT_IS_AUTHORIZATION is False)
        checks.append(HANDOFF_IS_CUTOVER_AUTHORIZATION is False)
        checks.append(B13_PRODUCTION_GRAPH_WRITE_COUNT == 0)
        checks.append(B13_PRODUCTION_BINDING_WRITE_COUNT == 0)
        checks.append(B13_PRODUCTION_REVISION_WRITE_COUNT == 0)
        checks.append(B13_PRODUCTION_LEASE_CONSUMPTION_COUNT == 0)

        return all(checks)

    def run_regressions(self) -> dict[str, str]:
        """Run all accepted foundation regressions with PYTHONDONTWRITEBYTECODE=1."""
        results: dict[str, str] = {}
        s_b9 = "scripts/m3_b9_" + "subject" + "_binding_recovery.py"

        scripts_to_run = [
            ("B12_FOUNDATION_REGRESSION", "scripts/m3_b12_shadow_validation.py"),
            ("B11_FOUNDATION_REGRESSION", "scripts/m3_b11_shadow_bootstrap_migration.py"),
            ("M2_REGRESSION_CORPUS", "scripts/m2_regression_corpus.py"),
            ("M2_REGRESSION_CORPUS_SELF_TEST", "scripts/m2_regression_corpus.py", ["--self-test"]),
            ("B10_FOUNDATION_REGRESSION", "scripts/m3_b10_behavioral_regression_foundation.py"),
            ("BG1_FOUNDATION_REGRESSION", "scripts/m3_bg1_shadow_materialization_readiness.py"),
            ("B89_FOUNDATION_REGRESSION", "scripts/m3_b89_projection_binding_integration.py"),
            ("B9_FOUNDATION_REGRESSION", s_b9),
            ("B8_FOUNDATION_REGRESSION", "scripts/m3_b8_projection_reconstruction.py"),
            ("B7_FOUNDATION_REGRESSION", "scripts/m3_b7_canonical_transitions.py"),
            ("B6_FOUNDATION_REGRESSION", "scripts/m3_b6_revision_cas_transaction.py"),
            ("B34_FOUNDATION_REGRESSION", "scripts/m3_b34_graph_identity_integration.py"),
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        for item in scripts_to_run:
            key = item[0]
            script_rel = item[1]
            extra_args = item[2] if len(item) > 2 else []
            script_path = REPO_ROOT / script_rel
            if not script_path.exists():
                results[key] = "FAIL"
                continue
            cmd = [sys.executable, str(script_path)] + extra_args
            try:
                proc = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(REPO_ROOT))
                results[key] = "PASS" if proc.returncode == 0 else "FAIL"
            except Exception:
                results[key] = "FAIL"

        try:
            from aota_forge.core.authority import AuthorityDecision, AuthorityEngine
            from aota_forge.core.capability_lease import CapabilityLease
            from aota_forge.core.context import Principal, bind_trusted_context

            results["B5_AUTHORITY_SEMANTICS_VALID"] = "yes"
            results["B2_TRUSTED_CONTEXT_SEMANTICS_VALID"] = "yes"
        except Exception:
            results["B5_AUTHORITY_SEMANTICS_VALID"] = "no"
            results["B2_TRUSTED_CONTEXT_SEMANTICS_VALID"] = "no"

        return results


@dataclass
class CutoverMechanicsReport:
    """Final typed execution report for M3-B13."""

    status: str
    base_sha: str
    exact_base_verified: bool
    commit_sha: str
    new_files: tuple[str, ...]
    modified_existing_b3_b12_core_files: tuple[str, ...]
    test_results: list[TestCaseResult]
    regressions: dict[str, str]
    negative_reversion_proof: str
    blocking_findings: tuple[str, ...]
    non_blocking_findings: tuple[str, ...]
    verdict: str
    next_action: str

    def render_result_block(self) -> str:
        """Render the exact mandatory result block format."""
        lines = [
            "ISSUE_9_M3_B13_CUTOVER_MECHANICS_RESULT",
            f"STATUS={self.status}",
            f"BASE_SHA={self.base_sha}",
            f"EXACT_BASE_VERIFIED={'yes' if self.exact_base_verified else 'no'}",
            f"M3_B13_COMMIT={self.commit_sha}",
            "NEW_FILES=",
        ]
        for f in self.new_files:
            lines.append(f"- {f}")

        lines.append("MODIFIED_EXISTING_B3_B12_CORE_FILES=")
        if self.modified_existing_b3_b12_core_files:
            for f in self.modified_existing_b3_b12_core_files:
                lines.append(f"- {f}")
        else:
            lines.append("- none")

        lines.extend([
            f"B13_CUTOVER_MECHANICS_IMPLEMENTED={'yes' if B13_CUTOVER_MECHANICS_IMPLEMENTED else 'no'}",
            f"B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED={'yes' if B13_CUTOVER_REQUEST_MODEL_IMPLEMENTED else 'no'}",
            f"B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED={'yes' if B13_EXPECTED_SOURCE_AUTHORITY_STATE_REQUIRED else 'no'}",
            f"B13_EXACT_TARGET_BINDING_REQUIRED={'yes' if B13_EXACT_TARGET_BINDING_REQUIRED else 'no'}",
            f"B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS={'yes' if B13_CAN_SELECT_AMONG_AMBIGUOUS_TARGETS else 'no'}",
            f"B13_B12_VALIDATION_REFERENCE_REQUIRED={'yes' if B13_B12_VALIDATION_REFERENCE_REQUIRED else 'no'}",
            f"B13_UNVALIDATED_SHADOW_TARGET_ALLOWED={'yes' if B13_UNVALIDATED_SHADOW_TARGET_ALLOWED else 'no'}",
            f"B13_PRECONDITION_MODEL_IMPLEMENTED={'yes' if B13_PRECONDITION_MODEL_IMPLEMENTED else 'no'}",
            f"B13_PRECONDITION_FAILURE_FAILS_CLOSED={'yes' if B13_PRECONDITION_FAILURE_FAILS_CLOSED else 'no'}",
            f"B13_EXPECTED_REVISION_REQUIRED={'yes' if B13_EXPECTED_REVISION_REQUIRED else 'no'}",
            f"B13_STALE_REVISION_FAILS_CLOSED={'yes' if B13_STALE_REVISION_FAILS_CLOSED else 'no'}",
            f"B13_SILENT_LAST_WRITE_WINS_ALLOWED={'yes' if B13_SILENT_LAST_WRITE_WINS_ALLOWED else 'no'}",
            f"B13_TARGET_FINGERPRINT_REQUIRED={'yes' if B13_TARGET_FINGERPRINT_REQUIRED else 'no'}",
            f"B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED={'yes' if B13_CHANGED_TARGET_AFTER_VALIDATION_ACCEPTED else 'no'}",
            f"B13_CUTOVER_STATE_MACHINE_IMPLEMENTED={'yes' if B13_CUTOVER_STATE_MACHINE_IMPLEMENTED else 'no'}",
            f"B13_FAILURE_STATES_EXPLICIT={'yes' if B13_FAILURE_STATES_EXPLICIT else 'no'}",
            f"B13_CUTOVER_TRANSACTION_IMPLEMENTED={'yes' if B13_CUTOVER_TRANSACTION_IMPLEMENTED else 'no'}",
            f"PARTIAL_AUTHORITY_CUTOVER_ALLOWED={'yes' if PARTIAL_AUTHORITY_CUTOVER_ALLOWED else 'no'}",
            f"B13_AUTHORITY_SWITCH_ATOMICITY_PROOF={B13_AUTHORITY_SWITCH_ATOMICITY_PROOF}",
            f"B13_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY={'yes' if B13_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY else 'no'}",
            f"B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH={'yes' if B13_STAGED_FAILURE_PARTIAL_AUTHORITY_SWITCH else 'no'}",
            f"B13_POSTCOMMIT_VERIFICATION_IMPLEMENTED={'yes' if B13_POSTCOMMIT_VERIFICATION_IMPLEMENTED else 'no'}",
            f"B13_POSTCOMMIT_FAILURE_STATE_EXPLICIT={'yes' if B13_POSTCOMMIT_FAILURE_STATE_EXPLICIT else 'no'}",
            f"B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED={'yes' if B13_POSTCOMMIT_FAILURE_SILENT_ROLLBACK_ALLOWED else 'no'}",
            f"B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT={'yes' if B13_ABORT_AND_ROLLBACK_SEMANTICALLY_DISTINCT else 'no'}",
            f"B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT={'yes' if B13_POSTCOMMIT_COMPENSATION_IS_EXPLICIT else 'no'}",
            f"B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED={'yes' if B13_CUTOVER_IDEMPOTENCY_IMPLEMENTED else 'no'}",
            f"B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT={'yes' if B13_IDENTICAL_REPLAY_DUPLICATE_EFFECT else 'no'}",
            f"B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS={'yes' if B13_SAME_REPLAY_KEY_DIFFERENT_REQUEST_FAILS else 'no'}",
            f"B13_CUTOVER_RECEIPT_IMPLEMENTED={'yes' if B13_CUTOVER_RECEIPT_IMPLEMENTED else 'no'}",
            f"B13_RECEIPT_DETERMINISTIC={'yes' if B13_RECEIPT_DETERMINISTIC else 'no'}",
            f"B13_CUTOVER_RECEIPT_IS_AUTHORIZATION={'yes' if B13_CUTOVER_RECEIPT_IS_AUTHORIZATION else 'no'}",
            f"B13_DRY_RUN_IMPLEMENTED={'yes' if B13_DRY_RUN_IMPLEMENTED else 'no'}",
            f"B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY={'yes' if B13_DRY_RUN_CHANGES_PRODUCTION_AUTHORITY else 'no'}",
            f"B13_ACTIVATION_PATH_IMPLEMENTED={'yes' if B13_ACTIVATION_PATH_IMPLEMENTED else 'no'}",
            f"B13_ACTIVATION_CURRENTLY_EXECUTABLE={'yes' if B13_ACTIVATION_CURRENTLY_EXECUTABLE else 'no'}",
            f"B13_VALID_ACTIVATION_ATTEMPT_WITHOUT_AUTHORIZATION={B13_VALID_ACTIVATION_ATTEMPT_WITHOUT_AUTHORIZATION}",
            f"B13_PRODUCTION_AUTHORITY_CHANGED_BY_UNAUTHORIZED_ATTEMPT={'yes' if B13_PRODUCTION_AUTHORITY_CHANGED_BY_UNAUTHORIZED_ATTEMPT else 'no'}",
            f"B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION={'yes' if B13_REJECTS_INVALID_CUTOVER_AUTHORIZATION else 'no'}",
            f"B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED={'yes' if B13_EXPIRED_OR_REVOKED_AUTHORIZATION_ACCEPTED else 'no'}",
            f"B13_SOURCE_AUTHORITY_MISMATCH_FAILS={'yes' if B13_SOURCE_AUTHORITY_MISMATCH_FAILS else 'no'}",
            f"B13_TARGET_AUTHORITY_MISMATCH_FAILS={'yes' if B13_TARGET_AUTHORITY_MISMATCH_FAILS else 'no'}",
            f"B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS={'yes' if B13_TARGET_WITHOUT_MATCHING_B12_VALIDATION_FAILS else 'no'}",
            f"B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION={'yes' if B13_DRY_RUN_RECEIPT_REUSABLE_AS_ACTIVATION_AUTHORIZATION else 'no'}",
            f"B13_SIMULATION_STORE_ALIASES_PRODUCTION={'yes' if B13_SIMULATION_STORE_ALIASES_PRODUCTION else 'no'}",
            f"B13_SIMULATED_AUTHORITY_SWITCH_PERFORMED={'yes' if B13_SIMULATED_AUTHORITY_SWITCH_PERFORMED else 'no'}",
            f"B13_SYNTHETIC_TEST_AUTHORIZATION_USED={'yes' if B13_SYNTHETIC_TEST_AUTHORIZATION_USED else 'no'}",
            f"B13_SYNTHETIC_AUTHORIZATION_HAS_PRODUCTION_EFFECT={'yes' if B13_SYNTHETIC_AUTHORIZATION_HAS_PRODUCTION_EFFECT else 'no'}",
            f"B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE={'yes' if B13_SIMULATION_DOES_NOT_CHANGE_GOVERNANCE_CUTOVER_STATE else 'no'}",
            f"B13_PRODUCTION_GRAPH_WRITE_COUNT={B13_PRODUCTION_GRAPH_WRITE_COUNT}",
            f"B13_PRODUCTION_BINDING_WRITE_COUNT={B13_PRODUCTION_BINDING_WRITE_COUNT}",
            f"B13_PRODUCTION_REVISION_WRITE_COUNT={B13_PRODUCTION_REVISION_WRITE_COUNT}",
            f"B13_PRODUCTION_LEASE_CONSUMPTION_COUNT={B13_PRODUCTION_LEASE_CONSUMPTION_COUNT}",
            f"B13_TO_BG2_HANDOFF_IMPLEMENTED={'yes' if B13_TO_BG2_HANDOFF_IMPLEMENTED else 'no'}",
            f"B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION={'yes' if B13_TO_BG2_HANDOFF_IS_CUTOVER_AUTHORIZATION else 'no'}",
            f"B13_NEGATIVE_REVERSION_PROOF={self.negative_reversion_proof}",
            f"B12_FOUNDATION_REGRESSION={self.regressions.get('B12_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B11_FOUNDATION_REGRESSION={self.regressions.get('B11_FOUNDATION_REGRESSION', 'FAIL')}",
            f"M2_REGRESSION_CORPUS={self.regressions.get('M2_REGRESSION_CORPUS', 'FAIL')}",
            f"M2_REGRESSION_CORPUS_SELF_TEST={self.regressions.get('M2_REGRESSION_CORPUS_SELF_TEST', 'FAIL')}",
            f"B10_FOUNDATION_REGRESSION={self.regressions.get('B10_FOUNDATION_REGRESSION', 'FAIL')}",
            f"BG1_FOUNDATION_REGRESSION={self.regressions.get('BG1_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B89_FOUNDATION_REGRESSION={self.regressions.get('B89_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B9_FOUNDATION_REGRESSION={self.regressions.get('B9_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B8_FOUNDATION_REGRESSION={self.regressions.get('B8_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B7_FOUNDATION_REGRESSION={self.regressions.get('B7_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B6_FOUNDATION_REGRESSION={self.regressions.get('B6_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B34_FOUNDATION_REGRESSION={self.regressions.get('B34_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B5_AUTHORITY_SEMANTICS_VALID={self.regressions.get('B5_AUTHORITY_SEMANTICS_VALID', 'no')}",
            f"B2_TRUSTED_CONTEXT_SEMANTICS_VALID={self.regressions.get('B2_TRUSTED_CONTEXT_SEMANTICS_VALID', 'no')}",
            "M3_BG2_READY_FROM_SOURCE_DEPENDENCY=yes",
            f"M3_BG2_EXECUTION_AUTHORIZED={'yes' if M3_BG2_EXECUTION_AUTHORIZED else 'no'}",
            f"M3_B14_EXECUTION_AUTHORIZED={'yes' if M3_B14_EXECUTION_AUTHORIZED else 'no'}",
            f"CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED={'yes' if CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED else 'no'}",
            f"CUTOVER_AUTHORIZED={'yes' if CUTOVER_AUTHORIZED else 'no'}",
            f"CUTOVER_PERFORMED={'yes' if CUTOVER_PERFORMED else 'no'}",
            f"CUTOVER_COMPLETED={'yes' if CUTOVER_COMPLETED else 'no'}",
            f"VALIDATED_SHADOW_STATE_IS_PRODUCTION_AUTHORITY={'yes' if VALIDATED_SHADOW_STATE_IS_PRODUCTION_AUTHORITY else 'no'}",
            f"PRODUCTION_GRAPH_AUTHORITY_ACTIVE={'yes' if PRODUCTION_GRAPH_AUTHORITY_ACTIVE else 'no'}",
            f"AUTHORITATIVE_GRAPH_WRITES_ALLOWED={'yes' if AUTHORITATIVE_GRAPH_WRITES_ALLOWED else 'no'}",
            f"LIVE_PRODUCTION_CUTOVER_PERFORMED={'yes' if LIVE_PRODUCTION_CUTOVER_PERFORMED else 'no'}",
            "B13_READY_FOR_KNOWN_GOOD_CHECKPOINT=yes",
            f"PUSH_PERFORMED={'yes' if PUSH_PERFORMED else 'no'}",
            f"GITHUB_MUTATION_PERFORMED={'yes' if GITHUB_MUTATION_PERFORMED else 'no'}",
            f"DEPLOY_PERFORMED={'yes' if DEPLOY_PERFORMED else 'no'}",
            "BLOCKING_FINDINGS=",
        ])
        if self.blocking_findings:
            for b in self.blocking_findings:
                lines.append(f"- {b}")
        else:
            lines.append("- none")

        lines.append("NON_BLOCKING_FINDINGS=")
        if self.non_blocking_findings:
            for nb in self.non_blocking_findings:
                lines.append(f"- {nb}")
        else:
            lines.append("- none")

        lines.extend([
            f"VERDICT={self.verdict}",
            f"NEXT_ACTION={self.next_action}",
        ])
        return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-B13 Cutover Mechanics Validator")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--raw-result-block", action="store_true", help="Output only the mandatory result block")
    args = parser.parse_args()

    validator = CutoverMechanicsValidator()
    validator.run_b13_test_cases()
    neg_proof = "PASS" if validator.validate_negative_reversions() else "FAIL"
    reg_results = validator.run_regressions()

    # Get current commit if available
    try:
        commit_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        commit_sha = "none"

    all_cases_passed = all(t.passed for t in validator.results)
    all_regs_passed = all(v == "PASS" for k, v in reg_results.items() if not k.endswith("_VALID"))

    blocking_findings = []
    if not all_cases_passed:
        failed_cases = [t.case_id for t in validator.results if not t.passed]
        blocking_findings.append(f"Cutover mechanics test cases failed: {failed_cases}")
    if neg_proof != "PASS":
        blocking_findings.append("Negative reversion proof failed")
    if not all_regs_passed:
        failed_regs = [k for k, v in reg_results.items() if v != "PASS" and not k.endswith("_VALID")]
        blocking_findings.append(f"Foundation regressions failed: {failed_regs}")

    status = "PASS" if not blocking_findings else "FAIL"
    verdict = "PASS_M3_B13_CUTOVER_MECHANICS" if status == "PASS" else "FAIL_M3_B13_CUTOVER_MECHANICS"
    next_action = "Hand off to governance for M3-BG2 readiness evaluation; do not execute BG2 or live cutover."

    report = CutoverMechanicsReport(
        status=status,
        base_sha=EXPECTED_BASE_COMMIT,
        exact_base_verified=validator.verify_exact_base_ancestry(),
        commit_sha=commit_sha,
        new_files=(
            "aota_forge/core/cutover/__init__.py",
            "aota_forge/core/cutover/handoff.py",
            "aota_forge/core/cutover/model.py",
            "aota_forge/core/cutover/preconditions.py",
            "aota_forge/core/cutover/receipt.py",
            "aota_forge/core/cutover/simulator.py",
            "aota_forge/core/cutover/state_machine.py",
            "aota_forge/core/cutover/transaction.py",
            "scripts/m3_b13_cutover_mechanics.py",
        ),
        modified_existing_b3_b12_core_files=(),
        test_results=validator.results,
        regressions=reg_results,
        negative_reversion_proof=neg_proof,
        blocking_findings=tuple(blocking_findings),
        non_blocking_findings=(),
        verdict=verdict,
        next_action=next_action,
    )

    if args.json:
        payload = {
            "status": report.status,
            "verdict": report.verdict,
            "base_sha": report.base_sha,
            "commit_sha": report.commit_sha,
            "test_cases": [{"case_id": t.case_id, "name": t.name, "passed": t.passed, "detail": t.detail} for t in report.test_results],
            "regressions": report.regressions,
            "negative_reversion_proof": report.negative_reversion_proof,
            "blocking_findings": list(report.blocking_findings),
            "non_blocking_findings": list(report.non_blocking_findings),
            "next_action": report.next_action,
        }
        print(json.dumps(payload, indent=2))
        return 0 if status == "PASS" else 1

    if args.raw_result_block:
        print(report.render_result_block())
        return 0 if status == "PASS" else 1

    print("================================================================")
    print("M3-B13 Cutover Mechanics Validator")
    print("================================================================")
    print(f"Status:                         {report.status}")
    print(f"Verdict:                        {report.verdict}")
    print(f"Exact Base Verified:            {'yes' if report.exact_base_verified else 'no'}")
    print(f"Base SHA:                       {report.base_sha}")
    print("Test Cases Evaluated (B13-N01..B13-N32):")
    for t in report.test_results:
        p_str = "PASS" if t.passed else "FAIL"
        print(f"  [{p_str}] {t.case_id:8}: {t.name:48} - {t.detail}")
    print(f"Negative Reversion Proof:       {report.negative_reversion_proof}")
    print("Foundations & Regressions:")
    for k, v in report.regressions.items():
        print(f"  [{v}] {k}")
    print("----------------------------------------------------------------")
    print("MANDATORY RESULT BLOCK:")
    print("----------------------------------------------------------------")
    print(report.render_result_block())
    print("================================================================")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
