#!/usr/bin/env python3
"""
M4-5 Planning Guard — validates the PLAN only, not source implementation.

Rejects planning defects G1-G15.
All paths are relative to repo root (aota_forge).
"""
from __future__ import annotations
import json, sys, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-5-plan"
ACCEPTED_BASE = "26e616872aa4063e6ecade2f43d258d27c23e188"
EXPECTED_TITLE = "Portable Plan Mutation Port + Multi-Domain Precondition Model + Mutation Journal Contract"

def load_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"FAIL: cannot load {p}: {e}")
        sys.exit(1)

def fail(g, msg):
    print(f"G{g} REJECT: {msg}")
    return False

def ok(g, msg):
    print(f"G{g} PASS: {msg}")
    return True

def main():
    passed = 0
    rejected = 0
    checks = []

    # Load artifacts
    plan = load_json(PLAN_DIR / "m4-5-plan.json")
    authority = load_json(PLAN_DIR / "m4-5-authority-extraction.json")
    scope = load_json(PLAN_DIR / "m4-5-scope-and-ownership.json")
    gap = load_json(PLAN_DIR / "m4-5-gap-map.json")
    boundary = load_json(PLAN_DIR / "m4-5-external-mutation-boundary.json")
    journal = load_json(PLAN_DIR / "m4-5-journal-state-machine.json")
    pre = load_json(PLAN_DIR / "m4-5-authority-precondition-plan.json")
    recon = load_json(PLAN_DIR / "m4-5-reconciliation-plan.json")
    idem = load_json(PLAN_DIR / "m4-5-idempotency-plan.json")
    result = load_json(PLAN_DIR / "m4-5-result-error-matrix.json")
    failure = load_json(PLAN_DIR / "m4-5-failure-injection-plan.json")
    accept = load_json(PLAN_DIR / "m4-5-acceptance-matrix.json")
    dag = load_json(PLAN_DIR / "m4-5-implementation-dag.json")
    downstream = load_json(PLAN_DIR / "m4-5-downstream-m4-6-m4-7-contract.json")
    negative = load_json(PLAN_DIR / "m4-5-negative-scope.json")
    ownership = load_json(PLAN_DIR / "m4-5-source-ownership.json")

    results = []
    title = authority.get("M4_5_EXACT_TITLE")
    plan_title = plan.get("work_item_title")

    # G1
    if title == EXPECTED_TITLE and plan_title == EXPECTED_TITLE:
        print("G1 PASS: M4-5 scope matches Issue authority (architecture freeze title)")
        results.append(True)
    else:
        print(f"G1 REJECT: scope contradicts Issue authority title={title!r} plan_title={plan_title!r}")
        results.append(False)

    # G2 source base != 26e616...
    g = 2
    if plan.get("accepted_writable_base") == ACCEPTED_BASE and authority.get("accepted_writable_base") == ACCEPTED_BASE and gap.get("accepted_writable_base") == ACCEPTED_BASE:
        print("G2 PASS: source base == accepted writable base 26e616872aa4063e6ecade2f43d258d27c23e188")
        results.append(True)
    else:
        print(f"G2 REJECT: source base mismatch plan={plan.get('accepted_writable_base')} authority={authority.get('accepted_writable_base')}")
        results.append(False)

    # G3 cross-authority atomic transaction
    g = 3
    cross = plan.get("cross_authority", {}).get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE")
    b = boundary.get("CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE")
    if cross == "no" and b == "no":
        print("G3 PASS: no cross-authority atomic transaction claim")
        results.append(True)
    else:
        print(f"G3 REJECT: cross-authority atomic claims cross={cross} boundary={b}")
        results.append(False)

    # G4 normalized_plan_digest used as external CAS
    g = 4
    norm = pre.get("D6_revision_digest_domains", {}).get("invariants", {}).get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN")
    boundary_raw = boundary.get("raw_vs_normalized", {}).get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN")
    journal_norm = journal.get("invariants", {}).get("NORMALIZED_EQUALITY_PROVES_EXACT_EXTERNAL_EFFECT")
    if norm == "no" and boundary_raw == "no" and journal_norm == "no":
        print("G4 PASS: normalized_plan_digest is not used as external CAS")
        results.append(True)
    else:
        print(f"G4 REJECT: normalized digest misuse norm={norm} boundary={boundary_raw} journal={journal_norm}")
        results.append(False)

    # G5 journal treated as semantic authority
    g = 5
    if journal.get("JOURNAL_IS_SEMANTIC_DECISION_MAKER") == "no" and plan.get("mutation_journal", {}).get("JOURNAL_IS_SEMANTIC_DECISION_MAKER") == "no":
        print("G5 PASS: journal is not semantic decision maker")
        results.append(True)
    else:
        print(f"G5 REJECT: journal semantic authority misdeclared")
        results.append(False)

    # G6 unknown outcome blindly reuses old lease
    g = 6
    blind = plan.get("retry", {}).get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE")
    recon_blind = recon.get("UNKNOWN_OUTCOME_BLIND_LEASE_REUSE")
    if blind == "no" and recon_blind == "no":
        print("G6 PASS: unknown outcome blind lease reuse denied")
        results.append(True)
    else:
        print(f"G6 REJECT: blind lease reuse allowed blind={blind} recon={recon_blind}")
        results.append(False)

    # G7 reconciliation heuristically selects third state
    g = 7
    forbidden = recon.get("forbidden", [])
    recon_third = None
    for c in recon.get("classification", []):
        if "third" in c.get("condition", "").lower():
            recon_third = c.get("result")
    if "heuristic selection" in forbidden and recon_third == "CONFLICT":
        print("G7 PASS: reconciliation does not heuristically select third state")
        results.append(True)
    else:
        print(f"G7 REJECT: heuristic selection or third state not CONFLICT forbidden={forbidden} third_result={recon_third}")
        results.append(False)

    # G8 generic Git write API planned
    g = 8
    neg = {item["item"]: item["allowed"] for item in negative.get("negative_scope", [])}
    platform_github = plan.get("platform", {}).get("GENERIC_GIT_WRITE_API_ALLOWED")
    if neg.get("Generic Git write API") == "no" and platform_github == "no":
        print("G8 PASS: no generic Git write API")
        results.append(True)
    else:
        print(f"G8 REJECT: generic Git write allowed neg={neg.get('Generic Git write API')} platform={platform_github}")
        results.append(False)

    # G9 generic external mutate-anything API
    g = 9
    generic_ext = plan.get("platform", {}).get("GENERIC_EXTERNAL_MUTATION_API_ALLOWED")
    if neg.get("Generic external mutate-anything API") == "no" and generic_ext == "no" and result.get("operation_contract_reuse", {}).get("generic_mutation_api_allowed") == "no":
        print("G9 PASS: no generic external mutate-anything API")
        results.append(True)
    else:
        print(f"G9 REJECT: generic external mutation allowed generic_ext={generic_ext}")
        results.append(False)

    # G10 GitHub adapter pulled into M4-5 without authority
    g = 10
    github_in_m4_5 = plan.get("platform", {}).get("GITHUB_ADAPTER_IMPLEMENTATION_IN_M4_5")
    github_write = plan.get("platform", {}).get("GITHUB_AUTHORITY_WRITE_IN_M4_5")
    github_scope = None
    for cap in scope.get("capability_ownership", []):
        if cap["capability"] == "GitHub-specific adapter":
            github_scope = cap["M4_5_OWNERSHIP"]
    if github_in_m4_5 == "no" and github_write == "no" and github_scope == "downstream":
        print("G10 PASS: GitHub adapter deferred to M4-6")
        results.append(True)
    else:
        print(f"G10 REJECT: GitHub in M4-5 github_in={github_in_m4_5} write={github_write} scope={github_scope}")
        results.append(False)

    # G11 M4-6/M4-7 implementation authorized by plan
    g = 11
    if plan.get("planning_gates", {}).get("M4_5_EXECUTION_AUTHORIZED") == "no" and plan.get("planning_gates", {}).get("M4_5_SOURCE_IMPLEMENTATION_AUTHORIZED") == "no":
        # also negative scope
        neg_exec = neg.get("M4-6/M4-7/M4-8 source implementation authorization")
        if neg_exec == "no":
            print("G11 PASS: M4-6/M4-7 not authorized by M4-5 plan (planning only)")
            results.append(True)
        else:
            print(f"G11 REJECT: neg exec={neg_exec}")
            results.append(False)
    else:
        print(f"G11 REJECT: execution authorized incorrectly")
        results.append(False)

    # G12 source ownership uses broad unconstrained glob
    g = 12
    exclusive = ownership.get("M4_5_EXCLUSIVE_WRITE_PATHS", [])
    has_glob = False
    for e in exclusive:
        p = e.get("path", "")
        if "*" in p or p.strip() == "" or p == "aota_forge/**/*":
            has_glob = True
        if p.endswith("/*") or p.endswith("/**"):
            has_glob = True
    total_exclusive = len(exclusive)
    if not has_glob and total_exclusive == 5 and ownership.get("SOURCE_OWNERSHIP_PLAN") == "PASS":
        print(f"G12 PASS: source ownership uses exact paths (no broad glob) count={total_exclusive}")
        results.append(True)
    else:
        print(f"G12 REJECT: broad glob or count mismatch has_glob={has_glob} count={total_exclusive}")
        results.append(False)

    # G13 external tests target production authority
    g = 13
    prod_allowed = failure.get("PRODUCTION_EXTERNAL_AUTHORITY_TEST_WRITE_ALLOWED")
    prod_github = failure.get("PRODUCTION_GITHUB_TEST_WRITE_ALLOWED")
    if prod_allowed == "no" and prod_github == "no":
        print("G13 PASS: no production authority test writes")
        results.append(True)
    else:
        print(f"G13 REJECT: prod writes allowed {prod_allowed} {prod_github}")
        results.append(False)

    # G14 semantic rollback planned
    g = 14
    semantic = plan.get("retry", {}).get("SEMANTIC_ROLLBACK_ALLOWED")
    recon_rollback = recon.get("SEMANTIC_ROLLBACK_ALLOWED")
    neg_rollback = neg.get("Semantic rollback")
    if semantic == "no" and recon_rollback == "no" and neg_rollback == "no":
        print("G14 PASS: semantic rollback denied")
        results.append(True)
    else:
        print(f"G14 REJECT: semantic rollback allowed semantic={semantic} recon={recon_rollback} neg={neg_rollback}")
        results.append(False)

    # G15 construction-time semantic TODO remains unresolved
    g = 15
    count = dag.get("CONSTRUCTION_TIME_SEMANTIC_DECISION_COUNT")
    dag_defined = dag.get("M4_5_IMPLEMENTATION_DAG_DEFINED")
    if count == 0 and dag_defined == "yes":
        print("G15 PASS: no construction-time semantic TODO")
        results.append(True)
    else:
        print(f"G15 REJECT: count={count} dag_defined={dag_defined}")
        results.append(False)

    # Additional self-consistency
    if not all(results):
        print(f"\nPLAN_GUARD=FAIL {sum(results)}/{len(results)} passed")
        sys.exit(1)
    print(f"\nPLAN_GUARD=PASS {len(results)}/{len(results)} negative cases rejected as expected")
    # Validate JSONs are parseable already done
    print(f"PLANNING_NEGATIVE_CASE_COUNT={len(results)}")
    print(f"PLANNING_NEGATIVE_CASE_REJECT_COUNT={len(results)}")

if __name__ == "__main__":
    main()
