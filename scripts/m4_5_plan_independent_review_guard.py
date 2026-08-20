#!/usr/bin/env python3
"""
M4-5 Independent Plan Review Guard — 18 adversarial planning probes.
Validates that the M4-5 planning package correctly REJECTS each forbidden pattern.
Tests PLAN only, not implementation.
"""
from __future__ import annotations
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-5-plan"
REVIEW_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-5-plan-review"

def load(p: pathlib.Path):
    return json.loads(p.read_text())

def main():
    plan = load(PLAN_DIR / "m4-5-plan.json")
    scope = load(PLAN_DIR / "m4-5-scope-and-ownership.json")
    ownership = load(PLAN_DIR / "m4-5-source-ownership.json")
    journal = load(PLAN_DIR / "m4-5-journal-state-machine.json")
    pre = load(PLAN_DIR / "m4-5-authority-precondition-plan.json")
    boundary = load(PLAN_DIR / "m4-5-external-mutation-boundary.json")
    recon = load(PLAN_DIR / "m4-5-reconciliation-plan.json")
    idem = load(PLAN_DIR / "m4-5-idempotency-plan.json")
    result = load(PLAN_DIR / "m4-5-result-error-matrix.json")
    failure = load(PLAN_DIR / "m4-5-failure-injection-plan.json")
    negative = load(PLAN_DIR / "m4-5-negative-scope.json")
    downstream = load(PLAN_DIR / "m4-5-downstream-m4-6-m4-7-contract.json")
    review = load(REVIEW_DIR / "m4-5-plan-independent-review.json") if (REVIEW_DIR / "m4-5-plan-independent-review.json").exists() else None

    neg = {item["item"]: item["allowed"] for item in negative.get("negative_scope", [])}

    cases = []

    # NEG-R1-01
    c01 = plan.get("platform", {}).get("GITHUB_ADAPTER_IMPLEMENTATION_IN_M4_5") == "no" and any("github.py (M4-6)" in s for s in ownership.get("M4_5_FORBIDDEN_PATHS", []))
    cases.append(("NEG-R1-01", "M4-5 implements GitHub adapter -> reject", c01))
    # NEG-R1-02
    c02 = neg.get("Durable journal persistence implementation") == "no, contract only" and "aota_forge/core/journal/store.py (M4-7 durable store)" in ownership.get("M4_5_FORBIDDEN_PATHS", [])
    cases.append(("NEG-R1-02", "M4-5 implements durable journal persistence -> reject", c02))
    # NEG-R1-03
    c03 = neg.get("Deterministic reconciliation execution engine") == "no, plan only"
    cases.append(("NEG-R1-03", "M4-5 implements reconciliation executor -> reject", c03))
    # NEG-R1-04
    c04 = plan.get("cross_authority", {}).get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE") == "no" and boundary.get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE") == "no"
    cases.append(("NEG-R1-04", "cross-authority atomic transaction claimed -> reject", c04))
    # NEG-R1-05
    c05 = pre.get("D6_revision_digest_domains", {}).get("invariants", {}).get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN") == "no"
    cases.append(("NEG-R1-05", "normalized digest used as external CAS -> reject", c05))
    # NEG-R1-06
    c06 = boundary.get("raw_vs_normalized", {}).get("SUBJECT_REVISION_DIFFERS_FROM_AUTHORITY_SOURCE_REVISION") == "yes"
    cases.append(("NEG-R1-06", "subject revision used as raw authority revision -> reject", c06))
    # NEG-R1-07
    c07 = journal.get("invariants", {}).get("EXTERNAL_WRITE_BEFORE_PREPARED_DURABLE") == "no" and journal.get("invariants", {}).get("EXTERNAL_WRITE_BEFORE_APPLYING_DURABLE") == "no"
    cases.append(("NEG-R1-07", "external apply before PREPARED allowed -> reject", c07))
    # NEG-R1-08
    c08 = plan.get("pre_write", {}).get("VERIFY_AFTER_EXTERNAL_WRITE_REQUIRED") == "yes" and boundary.get("invariants", {}).get("VERIFY_AFTER_EXTERNAL_WRITE_REQUIRED") == "yes"
    cases.append(("NEG-R1-08", "adapter success immediately means VERIFIED without readback -> reject", c08))
    # NEG-R1-09
    c09 = plan.get("retry", {}).get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE") == "no" and recon.get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE") == "no"
    cases.append(("NEG-R1-09", "OUTCOME_UNKNOWN blindly retries -> reject", c09))
    # NEG-R1-10
    c10 = recon.get("RETRY_AFTER_UNCERTAIN_OUTCOME_REQUIRES_FRESH_AUTHORIZATION") == "yes" and idem.get("external_operation_attempt_identity", {}).get("blind_reuse_denied") == "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE=no" or "fresh" in str(recon).lower()
    # more precise: check plan retry flag
    c10 = plan.get("retry", {}).get("RETRY_AFTER_UNCERTAIN_OUTCOME_REQUIRES_FRESH_AUTHORIZATION") == "yes"
    cases.append(("NEG-R1-10", "old lease reused after RETRYABLE_NO_EFFECT -> reject", c10))
    # NEG-R1-11
    c11 = recon.get("forbidden") and "heuristic selection" in recon.get("forbidden", [])
    cases.append(("NEG-R1-11", "third state heuristically merged -> reject", c11))
    # NEG-R1-12
    c12 = journal.get("JOURNAL_IS_SEMANTIC_DECISION_MAKER") == "no" and "Approval" in str(journal.get("journal_may_not_invent", []))
    cases.append(("NEG-R1-12", "journal/correlation ID becomes semantic authority -> reject", c12))
    # NEG-R1-13
    c13 = idem.get("external_journal_identity", {}).get("must_not_fall_back_to") == "MutationIntent.intent_fingerprint only"
    cases.append(("NEG-R1-13", "external identity falls back to intent_fingerprint -> reject", c13))
    # NEG-R1-14
    c14 = "always CONFLICT" in str(idem.get("linkage", {}).get("same_key_changed_authorization", ""))
    cases.append(("NEG-R1-14", "same key + changed authorization treated as replay -> reject", c14))
    # NEG-R1-15
    c15 = plan.get("platform", {}).get("GENERIC_EXTERNAL_MUTATION_API_ALLOWED") == "no" and result.get("operation_contract_reuse", {}).get("generic_mutation_api_allowed") == "no"
    cases.append(("NEG-R1-15", "generic external mutation API planned -> reject", c15))
    # NEG-R1-16
    c16 = plan.get("platform", {}).get("GENERIC_GIT_WRITE_API_ALLOWED") == "no"
    cases.append(("NEG-R1-16", "generic Git write planned -> reject", c16))
    # NEG-R1-17
    c17 = failure.get("PRODUCTION_GITHUB_TEST_WRITE_ALLOWED") == "no" and failure.get("PRODUCTION_EXTERNAL_AUTHORITY_TEST_WRITE_ALLOWED") == "no"
    cases.append(("NEG-R1-17", "production GitHub required for tests -> reject", c17))
    # NEG-R1-18
    c18 = downstream.get("parallel_safety_plan", {}).get("M4_6_M4_7_PARALLEL_SAFETY") == "conditional_parallel" and ownership.get("hot_file_analysis", {}).get("M4_6_M4_7_PARALLEL_SAFETY_PLAN") == "PASS"
    cases.append(("NEG-R1-18", "M4-6/M4-7 parallel write ownership ambiguous -> reject", c18))

    passed = sum(1 for _, _, ok in cases if ok)
    total = len(cases)
    for cid, desc, ok in cases:
        print(f"{cid} {'PASS' if ok else 'REJECT'}: {desc} -> {'rejected' if ok else 'NOT REJECTED (FAIL)'}")
    print(f"\nINDEPENDENT_PLAN_NEGATIVE_CASE_COUNT={total}")
    print(f"INDEPENDENT_PLAN_NEGATIVE_CASE_REJECT_COUNT={passed}")
    if passed == total:
        print(f"INDEPENDENT_PLAN_GUARD=PASS {passed}/{total} rejected as expected")
        sys.exit(0)
    else:
        print(f"INDEPENDENT_PLAN_GUARD=FAIL {passed}/{total}")
        sys.exit(1)

if __name__ == "__main__":
    main()
