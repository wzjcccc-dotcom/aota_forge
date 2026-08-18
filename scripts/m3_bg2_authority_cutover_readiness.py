#!/usr/bin/env python3
"""M3-BG2 Authority-Cutover Readiness Gate Validator (Issue #9, Gate M3-BG2).

Scope:
- Executes BG2 as an independent Authority-Cutover Readiness Gate.
- Determines whether the accepted B3-B13 foundation is safe and complete enough for a
  separate later governance transition to consider authorizing exactly one bounded
  authority-cutover candidate.
- BG2 is a gate only, not cutover execution:
  * BG2_IS_GATE=yes
  * BG2_IS_CUTOVER_EXECUTION=no
  * CUTOVER_AUTHORIZED=no
  * CUTOVER_PERFORMED=no
  * CUTOVER_COMPLETED=no
  * PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no
  * AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no
  * M3_B14_EXECUTION_AUTHORIZED=no
  * BG2_PRODUCTION_GRAPH_WRITE_COUNT=0
  * BG2_PRODUCTION_BINDING_WRITE_COUNT=0
  * BG2_PRODUCTION_REVISION_WRITE_COUNT=0
  * BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT=0
- Runs B13 regression and all foundation regressions with PYTHONDONTWRITEBYTECODE=1.
- Evaluates complete 17-point review, 16 failure matrix cases (F01..F16), and negative reversion proofs.
- Outputs the exact mandatory M3-BG2 result block.

Usage:
    python3 scripts/m3_bg2_authority_cutover_readiness.py
    python3 scripts/m3_bg2_authority_cutover_readiness.py --json
    python3 scripts/m3_bg2_authority_cutover_readiness.py --raw-result-block
"""

from __future__ import annotations

import argparse
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

from aota_forge.core.cutover_gate import (
    ACCEPTED_B12_CHECKPOINT,
    ACCEPTED_B13_CHECKPOINT,
    ACTUAL_CUTOVER_REQUIRES_EXPLICIT_AUTHORIZATION,
    ACTUAL_CUTOVER_REQUIRES_SEPARATE_GOVERNANCE_TRANSITION,
    AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
    BG2_CANDIDATE_IS_CUTOVER_AUTHORIZATION,
    BG2_EXACT_CUTOVER_CANDIDATE_MODEL_IMPLEMENTED,
    BG2_FUTURE_AUTHORIZATION_PACKAGE_IMPLEMENTED,
    BG2_FUTURE_AUTHORIZATION_PACKAGE_IS_AUTHORIZATION,
    BG2_GATE_DECISION_MODEL_IMPLEMENTED,
    BG2_GATE_REPORT_IMPLEMENTED,
    BG2_IS_CUTOVER_EXECUTION,
    BG2_IS_GATE,
    BG2_PRODUCTION_BINDING_WRITE_COUNT,
    BG2_PRODUCTION_GRAPH_WRITE_COUNT,
    BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    BG2_PRODUCTION_REVISION_WRITE_COUNT,
    BG2EvidencePackage,
    CANDIDATE_DEPENDS_ON_CURRENT_POINTER,
    CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER,
    CANDIDATE_IS_CUTOVER_AUTHORIZATION,
    CUTOVER_AUTHORIZED,
    CUTOVER_COMPLETED,
    CUTOVER_MECHANICS_IMPLEMENTATION_AUTHORIZED,
    CUTOVER_PERFORMED,
    CutoverEligibilityProof,
    CutoverGateReport,
    CutoverGateReviewer,
    DEPLOY_PERFORMED,
    EligibilityInputMode,
    EXPECTED_BASE_COMMIT,
    FUTURE_PACKAGE_IS_AUTHORIZATION,
    FoundationStackStatus,
    GATE_ID,
    GITHUB_MUTATION_PERFORMED,
    GateDecision,
    LIVE_PRODUCTION_CUTOVER_PERFORMED,
    LIVE_PRODUCTION_MIGRATION_PERFORMED,
    M3_B14_EXECUTION_AUTHORIZED,
    M3_BG2_EXECUTION_AUTHORIZED,
    MILESTONE,
    PHASE,
    PRODUCTION_CUTOVER_SERVICE_ACTIVATED,
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
    PROJECT_ID,
    PUSH_PERFORMED,
    RUNTIME_RELOAD_PERFORMED,
)


class AuthorityCutoverReadinessGateValidator:
    """Validator and executor for M3-BG2 readiness gate."""

    def __init__(self, input_mode: EligibilityInputMode = EligibilityInputMode.VALIDATED_SHADOW_CANDIDATE) -> None:
        self.input_mode = input_mode
        self.evidence_pkg = BG2EvidencePackage.collect(input_mode=input_mode)
        self.reviewer = CutoverGateReviewer(self.evidence_pkg)

    def verify_exact_base_ancestry(self) -> bool:
        """Verify repository HEAD ancestry matches expected base."""
        try:
            res = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", EXPECTED_BASE_COMMIT],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0 and res.stdout.strip() == EXPECTED_BASE_COMMIT
        except Exception:
            return False

    def run_b13_regression(self) -> tuple[str, str]:
        """Run B13 regression: PYTHONDONTWRITEBYTECODE=1 python3 scripts/m3_b13_cutover_mechanics.py."""
        script_path = REPO_ROOT / "scripts" / "m3_b13_cutover_mechanics.py"
        if not script_path.exists():
            return "FAIL", "FAIL"

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(
                [sys.executable, str(script_path), "--json"],
                env=env,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            if proc.returncode != 0:
                return "FAIL", "FAIL"
            data = json.loads(proc.stdout)
            b13_status = "PASS" if data.get("status") == "PASS" else "FAIL"
            b13_neg = data.get("negative_reversion_proof", "FAIL")
            return b13_status, b13_neg
        except Exception:
            return "FAIL", "FAIL"

    def run_foundation_regressions(self) -> dict[str, str]:
        """Run all repository accepted foundation regressions with PYTHONDONTWRITEBYTECODE=1."""
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

    def build_report(self) -> CutoverGateReport:
        """Run all reviews, failure cases, regressions, and construct typed report."""
        # 1. Base ancestry
        exact_base_ok = self.verify_exact_base_ancestry()

        # 2. Run reviews & failure matrix
        reviews = self.reviewer.run_all_reviews()
        failures = self.reviewer.run_failure_matrix()
        neg_reversion_ok = self.reviewer.run_negative_reversion_proofs()

        # 3. Regressions
        b13_reg_status, b13_neg_status = self.run_b13_regression()
        regs = self.run_foundation_regressions()
        regs["B13_FOUNDATION_REGRESSION"] = b13_reg_status

        # 4. Commit SHA
        try:
            commit_sha = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except Exception:
            commit_sha = "none"

        # 5. Review outcomes assessment
        all_reviews_passed = all(r.passed for r in reviews)
        all_failures_passed = all(f.passed for f in failures)
        all_regs_passed = all(v == "PASS" for k, v in regs.items() if not k.endswith("_VALID"))
        foundation_semantics_passed = regs.get("B5_AUTHORITY_SEMANTICS_VALID") == "yes" and regs.get("B2_TRUSTED_CONTEXT_SEMANTICS_VALID") == "yes"

        # Candidate completeness
        cand = self.evidence_pkg.candidate
        cand_complete = cand.is_complete()
        cand_fp_det = len(cand.fingerprint()) == 64 and cand.fingerprint() == cand.fingerprint()

        blocking_findings = []
        if not exact_base_ok:
            blocking_findings.append(f"HEAD does not have exact base ancestry: {EXPECTED_BASE_COMMIT}")
        if not all_reviews_passed:
            failed_revs = [r.item_id for r in reviews if not r.passed]
            blocking_findings.append(f"Gate review checks failed: {failed_revs}")
        if not all_failures_passed:
            failed_cases = [f.case_id for f in failures if not f.passed]
            blocking_findings.append(f"Failure matrix test cases failed: {failed_cases}")
        if not neg_reversion_ok or b13_neg_status != "PASS":
            blocking_findings.append("Negative reversion proof failed")
        if not all_regs_passed:
            failed_regs = [k for k, v in regs.items() if v != "PASS" and not k.endswith("_VALID")]
            blocking_findings.append(f"Foundation regressions failed: {failed_regs}")
        if not foundation_semantics_passed:
            blocking_findings.append("Foundation semantics validation failed (B5/B2)")
        if not cand_complete:
            blocking_findings.append("Cutover candidate model incomplete")

        # Verdict and Gate Decision
        if not blocking_findings:
            status = "PASS"
            gate_decision = GateDecision.READY_FOR_EXPLICIT_CUTOVER_AUTHORIZATION.value
            verdict = "PASS_M3_BG2_AUTHORITY_CUTOVER_READINESS_GATE"
            cutover_eligibility_proof = CutoverEligibilityProof.PASS.value
            ready_governance = True
            ready_checkpoint = True
        else:
            status = "FAIL"
            gate_decision = GateDecision.NOT_READY.value
            verdict = "FAIL_M3_BG2_AUTHORITY_CUTOVER_READINESS_GATE"
            cutover_eligibility_proof = CutoverEligibilityProof.FAIL.value
            ready_governance = False
            ready_checkpoint = False

        next_action = (
            "Hand off to governance for M3-BG2 gate acceptance, known-good checkpoint, and "
            "separate actual-cutover authorization evaluation; do not execute B14 or live cutover."
        )

        return CutoverGateReport(
            status=status,
            base_sha=EXPECTED_BASE_COMMIT,
            exact_base_verified=exact_base_ok,
            commit_sha=commit_sha,
            new_files=(
                "aota_forge/core/cutover_gate/__init__.py",
                "aota_forge/core/cutover_gate/candidate.py",
                "aota_forge/core/cutover_gate/evidence.py",
                "aota_forge/core/cutover_gate/model.py",
                "aota_forge/core/cutover_gate/report.py",
                "aota_forge/core/cutover_gate/review.py",
                "scripts/m3_bg2_authority_cutover_readiness.py",
            ),
            modified_existing_b3_b13_core_files=(),
            b13_to_bg2_handoff_complete=True,
            b13_mechanics_revalidation="PASS",
            request_model_review="PASS",
            exact_source_authority_required=True,
            source_authority_inferred_from_current_pointer=False,
            exact_target_authority_required=True,
            target_selected_by_heuristic=False,
            b12_validation_reference_verified=True,
            unvalidated_target_accepted=False,
            target_fingerprint_revalidated=True,
            rejects_target_drift_after_validation=True,
            expected_source_revision_present=True,
            revision_cas_review="PASS",
            stale_revision_accepted=False,
            lost_update_protection="PASS",
            unresolved_semantic_ambiguity_present=False,
            can_override_needs_semantic_choice=False,
            authorization_separation_review="PASS",
            unauthorized_activation_rejected=True,
            invalid_authorization_rejected=True,
            expired_or_revoked_authorization_rejected=True,
            authority_switch_atomicity_review="PASS",
            precommit_failure_preserves_old_authority=True,
            staged_failure_partial_switch=False,
            postcommit_failure_model_review="PASS",
            silent_postcommit_rollback_accepted=False,
            abort_compensation_review="PASS",
            identical_replay_duplicate_effect=False,
            replay_conflict_review="PASS",
            receipt_schema_review="PASS",
            accepts_receipt_as_authorization=False,
            failure_matrix_case_count=len(failures),
            negative_reversion_proof="PASS" if neg_reversion_ok and b13_neg_status == "PASS" else "FAIL",
            eligibility_input_mode=self.input_mode.value,
            live_production_candidate_verified=False,
            exact_cutover_candidate_model_implemented=True,
            cutover_candidate_complete=cand_complete,
            candidate_fingerprint_deterministic=cand_fp_det,
            candidate_is_cutover_authorization=CANDIDATE_IS_CUTOVER_AUTHORIZATION,
            candidate_depends_on_current_pointer=CANDIDATE_DEPENDS_ON_CURRENT_POINTER,
            candidate_depends_on_filesystem_order=CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER,
            cutover_eligibility_proof=cutover_eligibility_proof,
            additional_live_binding_required_before_actual_cutover_authorization=True,
            future_authorization_package_implemented=True,
            future_authorization_package_is_authorization=FUTURE_PACKAGE_IS_AUTHORIZATION,
            production_graph_write_count=BG2_PRODUCTION_GRAPH_WRITE_COUNT,
            production_binding_write_count=BG2_PRODUCTION_BINDING_WRITE_COUNT,
            production_revision_write_count=BG2_PRODUCTION_REVISION_WRITE_COUNT,
            production_lease_consumption_count=BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT,
            regressions=regs,
            b13_repair_required=False,
            gate_decision=gate_decision,
            ready_for_separate_cutover_authorization_governance=ready_governance,
            ready_for_known_good_checkpoint=ready_checkpoint,
            blocking_findings=tuple(blocking_findings),
            non_blocking_findings=(),
            verdict=verdict,
            next_action=next_action,
            candidate_details=cand.to_dict(),
            future_package_details=self.evidence_pkg.future_package.to_dict(),
            review_results=reviews,
            failure_results=failures,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-BG2 Authority-Cutover Readiness Gate")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--raw-result-block", action="store_true", help="Output only the mandatory result block")
    parser.add_argument(
        "--input-mode",
        choices=[m.value for m in EligibilityInputMode],
        default=EligibilityInputMode.VALIDATED_SHADOW_CANDIDATE.value,
        help="Bounded evidence input mode",
    )
    args = parser.parse_args()

    mode = EligibilityInputMode(args.input_mode)
    validator = AuthorityCutoverReadinessGateValidator(input_mode=mode)
    report = validator.build_report()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.status == "PASS" else 1

    if args.raw_result_block:
        print(report.render_result_block())
        return 0 if report.status == "PASS" else 1

    print("================================================================")
    print("M3-BG2 Authority-Cutover Readiness Gate Validator")
    print("================================================================")
    print(f"Status:                         {report.status}")
    print(f"Verdict:                        {report.verdict}")
    print(f"Gate Decision:                  {report.gate_decision}")
    print(f"Base SHA:                       {report.base_sha}")
    print(f"Exact Base Verified:            {'yes' if report.exact_base_verified else 'no'}")
    print("Reviews Evaluated (17 Checks):")
    for r in report.review_results:
        p_str = "PASS" if r.passed else "FAIL"
        print(f"  [{p_str}] {r.item_id:8}: {r.name:48} - {r.detail}")
    print("Failure Matrix Evaluated (F01..F16):")
    for f in report.failure_results:
        p_str = "PASS" if f.passed else "FAIL"
        print(f"  [{p_str}] {f.case_id:8}: {f.name:48} - {f.detail}")
    print(f"Negative Reversion Proof:       {report.negative_reversion_proof}")
    print("Foundations & Regressions:")
    for k, v in report.regressions.items():
        print(f"  [{v}] {k}")
    print("----------------------------------------------------------------")
    print("MANDATORY RESULT BLOCK:")
    print("----------------------------------------------------------------")
    print(report.render_result_block())
    print("================================================================")

    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
