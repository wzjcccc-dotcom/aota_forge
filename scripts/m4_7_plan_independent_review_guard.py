#!/usr/bin/env python3
"""
M4-7 Independent Plan Review Guard — validates review evidence, not source implementation.

Ensures reviewer independently verified all 41 gates without repairing plan.
"""
from __future__ import annotations
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-7-plan"
REVIEW_PATH = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-7-plan-review" / "m4-7-plan-independent-review.json"

ACCEPTED_BASE = "e51141a8559faa0fb37ec1258d9c6446b8faa956"
CANDIDATE_SHA = "fc9b26f9c93768c8a430773668ae1ae61619af49"

def load_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"FAIL: cannot load {p}: {e}")
        sys.exit(1)

def main():
    print("=== M4-7 Independent Review Guard ===")
    if not REVIEW_PATH.exists():
        print(f"FAIL: review evidence missing at {REVIEW_PATH}")
        sys.exit(1)
    review = load_json(REVIEW_PATH)
    # Verify key fields
    checks = []
    def check(name, cond, detail=""):
        ok = bool(cond)
        print(f"{'PASS' if ok else 'REJECT'}: {name}" + (f" ({detail})" if detail else ""))
        checks.append(ok)
        return ok

    check("REVIEW_TARGET_SHA == fc9b26f", review.get("review_metadata", {}).get("review_base_sha") == CANDIDATE_SHA)
    check("ACCEPTED_WRITABLE_BASE == e51141a", review.get("review_metadata", {}).get("accepted_writable_base") == ACCEPTED_BASE)
    check("PLAN_TARGET_SHA_VERIFIED == yes", review.get("target_remote_ancestry", {}).get("PLAN_TARGET_SHA_VERIFIED") == "yes")
    check("PLAN_BASE_ANCESTRY_VERIFIED == yes", review.get("target_remote_ancestry", {}).get("PLAN_BASE_ANCESTRY_VERIFIED") == "yes")
    check("PRODUCTION_SOURCE_MUTATION_COUNT == 0", review.get("planning_only_diff", {}).get("PRODUCTION_SOURCE_MUTATION_COUNT") == 0)
    check("M4_7_SCOPE_AUTHORITY_REVIEW PASS", review.get("issue_authority_alignment", {}).get("M4_7_SCOPE_AUTHORITY_REVIEW") == "PASS")
    check("STORAGE_ENGINE_PRESCRIPTION_REVIEW PASS", review.get("storage_engine_prescription_review", {}).get("STORAGE_ENGINE_PRESCRIPTION_REVIEW") == "PASS")
    check("PRODUCTION_STORAGE_ENGINE_FROZEN no", review.get("storage_engine_prescription_review", {}).get("PRODUCTION_STORAGE_ENGINE_FROZEN_BY_M4_7_PLAN") == "no")
    check("DURABLE_STORE_PORT_REVIEW PASS", review.get("durable_store_port_review", {}).get("DURABLE_STORE_PORT_REVIEW") == "PASS")
    check("PROCESS_RESTART_DURABILITY_TEST_REVIEW PASS", review.get("real_durability_test_review", {}).get("PROCESS_RESTART_DURABILITY_TEST_REVIEW") == "PASS")
    check("M4_7_JOURNAL_STATE_REUSE_REVIEW PASS", review.get("m4_5_journal_state_reuse_review", {}).get("M4_7_JOURNAL_STATE_REUSE_REVIEW") == "PASS")
    check("DURABLE_JOURNAL_SCHEMA_REVIEW PASS", review.get("durable_journal_schema_review", {}).get("DURABLE_JOURNAL_SCHEMA_REVIEW") == "PASS")
    check("DURABLE_JOURNAL_TRANSITION_CAS_REVIEW PASS", review.get("local_journal_cas_review", {}).get("DURABLE_JOURNAL_TRANSITION_CAS_REVIEW") == "PASS")
    check("AT_MOST_ONE_ATTEMPT_DURABLE_REVIEW PASS", review.get("at_most_one_external_attempt_review", {}).get("AT_MOST_ONE_ATTEMPT_DURABLE_REVIEW") == "PASS")
    check("CRASH_WINDOW_COUNT 10", review.get("j1_j10_durable_matrix_review", {}).get("CRASH_WINDOW_COUNT") == 10)
    check("J10_DEDICATED_EXECUTION_ROW yes", review.get("j10_review", {}).get("J10_DEDICATED_EXECUTION_ROW") == "yes")
    check("RECOVERY_DISCOVERY_REVIEW PASS", review.get("recovery_discovery_review", {}).get("RECOVERY_DISCOVERY_REVIEW") == "PASS")
    check("RECOVERY_EXECUTOR not semantic decision maker", review.get("recovery_executor_review", {}).get("RECOVERY_EXECUTOR_IS_SEMANTIC_DECISION_MAKER") == "no")
    check("M4_7_DEPENDS_ON_M4_6_IMPLEMENTATION no", review.get("m4_6_independence_review", {}).get("M4_7_DEPENDS_ON_M4_6_IMPLEMENTATION") == "no")
    check("OUTCOME_UNKNOWN_DURABLE_REVIEW PASS", review.get("outcome_unknown_review", {}).get("OUTCOME_UNKNOWN_DURABLE_REVIEW") == "PASS")
    check("RECONCILIATION_EXECUTION_REVIEW PASS", review.get("three_way_reconciliation_execution_review", {}).get("RECONCILIATION_EXECUTION_REVIEW") == "PASS")
    check("FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY yes", review.get("retryable_no_effect_review", {}).get("FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY") == "yes")
    check("DURABLE_IDEMPOTENCY_REVIEW PASS", review.get("durable_idempotency_review", {}).get("DURABLE_IDEMPOTENCY_REVIEW") == "PASS")
    check("JOURNAL_WRITE_ORDER_REVIEW PASS", review.get("journal_write_order_review", {}).get("JOURNAL_WRITE_ORDER_REVIEW") == "PASS")
    check("VERIFY_AFTER_WRITE PASS", review.get("verify_after_write_review", {}).get("VERIFY_AFTER_WRITE_EXECUTION_REVIEW") == "PASS")
    check("DURABLE_ERROR_MODEL PASS", review.get("durable_error_model_review", {}).get("DURABLE_ERROR_MODEL_REVIEW") == "PASS")
    check("JOURNAL_PERSISTENCE_FAILURE PASS", review.get("persistence_failure_review", {}).get("JOURNAL_PERSISTENCE_FAILURE_REVIEW") == "PASS")
    check("SCHEMA_VERSIONING PASS", review.get("schema_versioning_review", {}).get("JOURNAL_SCHEMA_VERSIONING_REVIEW") in ("PASS", "not_required"))
    check("M4_7_SOURCE_OWNERSHIP_REVIEW PASS", review.get("m4_6_m4_7_parallel_ownership_review", {}).get("M4_7_SOURCE_OWNERSHIP_REVIEW") == "PASS")
    check("IMPLEMENTATION_DAG 7 slices", review.get("implementation_dag_review", {}).get("IMPLEMENTATION_SLICE_COUNT") == 7)
    check("M4_7_ACCEPTANCE_MATRIX PASS", review.get("acceptance_matrix_review", {}).get("M4_7_ACCEPTANCE_MATRIX_REVIEW") == "PASS")
    check("PRODUCTION_EXTERNAL_AUTHORITY_TEST_WRITE_ALLOWED no", review.get("production_safety_review", {}).get("PRODUCTION_EXTERNAL_AUTHORITY_TEST_WRITE_ALLOWED") == "no")
    check("PLAN_GUARD PASS", review.get("plan_guard_review", {}).get("PLAN_GUARD") == "PASS")
    # Independent negatives
    neg = review.get("independent_negative_matrix", {})
    n_count = neg.get("INDEPENDENT_PLAN_NEGATIVE_CASE_COUNT", 0)
    n_reject = neg.get("INDEPENDENT_PLAN_NEGATIVE_CASE_REJECT_COUNT", 0)
    check("INDEPENDENT_NEGATIVES >=22 all reject", n_count >= 22 and n_reject == n_count, f"count={n_count} reject={n_reject}")
    check("PLAN_IMPLEMENTABILITY PASS", review.get("plan_implementability_review", {}).get("PLAN_IMPLEMENTABILITY_REVIEW") == "PASS")
    check("BLOCKING_FINDING_COUNT 0", review.get("blocking_non_blocking_findings", {}).get("BLOCKING_FINDING_COUNT") == 0)

    # Also run the original plan guard to ensure plan still passes
    plan_guard = ROOT / "scripts" / "m4_7_plan_guard.py"
    if plan_guard.exists():
        import subprocess
        result = subprocess.run([sys.executable, str(plan_guard)], capture_output=True, text=True)
        print("\n--- upstream plan guard output ---")
        print(result.stdout)
        if result.returncode != 0:
            print("FAIL: upstream m4_7_plan_guard.py failed")
            print(result.stderr)
            checks.append(False)
        else:
            print("PASS: upstream plan guard still PASS")
            checks.append(True)
    else:
        print("WARN: plan guard not found")
        checks.append(False)

    passed = sum(1 for c in checks if c)
    total = len(checks)
    print(f"\nINDEPENDENT_REVIEW_GUARD: {passed}/{total} checks passed")
    if passed == total:
        print("INDEPENDENT_REVIEW_GUARD=PASS")
        sys.exit(0)
    else:
        print("INDEPENDENT_REVIEW_GUARD=FAIL")
        sys.exit(1)

if __name__ == "__main__":
    main()
