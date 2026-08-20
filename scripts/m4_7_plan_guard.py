#!/usr/bin/env python3
"""
M4-7 Planning Guard — validates PLAN only, not source implementation.

Rejects at least G1-G18 per task section 39.
"""
from __future__ import annotations
import json
import sys
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-7-plan"
ACCEPTED_BASE = "e51141a8559faa0fb37ec1258d9c6446b8faa956"
EXPECTED_TITLE = "Durable External Mutation Journal + Reconciliation / Recovery"
EXPECTED_STATE_FAMILY = ["PREPARED","APPLYING","FAILED_NO_EFFECT","VERIFIED","OUTCOME_UNKNOWN","RECONCILING","VERIFIED_RECOVERED","RETRYABLE_NO_EFFECT","CONFLICT"]

def load_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"FAIL: cannot load {p}: {e}")
        sys.exit(1)

def main():
    results = []
    def check(g, name, condition, detail=""):
        passed = bool(condition)
        prefix = f"G{g}"
        msg = f"{prefix} {'PASS' if passed else 'REJECT'}: {name}" + (f"  ({detail})" if detail else "")
        print(msg)
        results.append(passed)
        return passed

    # Load artifacts
    try:
        plan = load_json(PLAN_DIR / "m4-7-plan.json")
        authority = load_json(PLAN_DIR / "m4-7-authority-extraction.json")
        scope = load_json(PLAN_DIR / "m4-7-scope-and-ownership.json")
        recon = load_json(PLAN_DIR / "m4-7-source-reconnaissance.json")
        backend = load_json(PLAN_DIR / "m4-7-durable-backend-decision.json")
        schema = load_json(PLAN_DIR / "m4-7-journal-schema.json")
        cas = load_json(PLAN_DIR / "m4-7-transition-cas-plan.json")
        attempt = load_json(PLAN_DIR / "m4-7-attempt-reservation.json")
        crash = load_json(PLAN_DIR / "m4-7-crash-window-execution-matrix.json")
        discovery = load_json(PLAN_DIR / "m4-7-recovery-discovery.json")
        reconc = load_json(PLAN_DIR / "m4-7-reconciliation-execution.json")
        retry = load_json(PLAN_DIR / "m4-7-retry-authorization-handoff.json")
        idem = load_json(PLAN_DIR / "m4-7-idempotency-plan.json")
        err = load_json(PLAN_DIR / "m4-7-error-model.json")
        failure = load_json(PLAN_DIR / "m4-7-failure-injection-plan.json")
        accept = load_json(PLAN_DIR / "m4-7-acceptance-matrix.json")
        dag = load_json(PLAN_DIR / "m4-7-implementation-dag.json")
        parallel = load_json(PLAN_DIR / "m4-7-m4-6-parallel-boundary.json")
        negative = load_json(PLAN_DIR / "m4-7-negative-scope.json")
    except SystemExit:
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: artifact load error {e}")
        sys.exit(1)

    # G1 wrong source base
    g=1
    if plan.get("accepted_writable_base")==ACCEPTED_BASE and authority.get("accepted_writable_base")==ACCEPTED_BASE and backend.get("accepted_writable_base")==ACCEPTED_BASE and crash.get("accepted_writable_base")==ACCEPTED_BASE:
        check(g,"source base == accepted writable base e51141a", True)
    else:
        check(g,"source base == accepted writable base e51141a", False, f"plan={plan.get('accepted_writable_base')}")

    # G2 M4-5 state redefinition
    g=2
    state_family = plan.get("JOURNAL_STATE_FAMILY") or schema.get("durable_schema",{}).get("fields",[])
    # plan state family should be exactly 9 expected
    if plan.get("JOURNAL_STATE_FAMILY")==EXPECTED_STATE_FAMILY and plan.get("JOURNAL_STATE_REDEFINITION_COUNT")==0 and plan.get("M4_7_JOURNAL_STATE_REUSE_PLAN")=="PASS":
        check(g,"M4-5 9-state reuse no redefinition", True)
    else:
        check(g,"M4-5 9-state reuse no redefinition", False, f"family={plan.get('JOURNAL_STATE_FAMILY')} redef={plan.get('JOURNAL_STATE_REDEFINITION_COUNT')}")

    # G3 GitHub adapter pulled into M4-7
    g=3
    if plan.get("GITHUB_API_PLANNED_CALL_COUNT")==0 and plan.get("GITHUB_ADAPTER_IMPLEMENTATION_IN_M4_7")=="no" and backend.get("M4_7_DURABLE_BACKEND") and "github" not in backend.get("M4_7_DURABLE_BACKEND","").lower() and parallel.get("M4_7_FORBIDDEN_PATHS"):
        # ensure no github in exclusive paths
        exclusive = parallel.get("M4_7_EXCLUSIVE_WRITE_PATHS",[])
        has_github = any("github" in p.lower() for p in exclusive)
        if not has_github and plan.get("GITHUB_ADAPTER_IMPLEMENTATION_IN_M4_7")=="no":
            check(g,"no GitHub adapter in M4-7", True)
        else:
            check(g,"no GitHub adapter in M4-7", False, f"exclusive={exclusive}")
    else:
        check(g,"no GitHub adapter in M4-7", False, f"GITHUB_API={plan.get('GITHUB_API_PLANNED_CALL_COUNT')}")

    # G4 backend unspecified
    g=4
    b = plan.get("M4_7_DURABLE_BACKEND") or backend.get("M4_7_DURABLE_BACKEND")
    if b and b != "unspecified" and len(str(b))>10 and backend.get("BACKEND_AUTHORITY_SOURCE"):
        check(g,"durable backend specified with authority", True)
    else:
        check(g,"durable backend specified with authority", False, f"backend={b}")

    # G5 in-memory lock as sole attempt protection
    g=5
    if attempt.get("AT_MOST_ONE_ATTEMPT_DURABLE_PLAN")=="PASS" and attempt.get("RESERVATION_SEMANTICS",{}).get("no_in_memory_only_lock_as_sole_authority")=="yes_rejected":
        check(g,"at-most-one not in-memory-only", True)
    else:
        check(g,"at-most-one not in-memory-only", False, f"AT_MOST_ONE={attempt.get('AT_MOST_ONE_ATTEMPT_DURABLE_PLAN')}")

    # G6 external apply before durable PREPARED
    g=6
    if plan.get("EXTERNAL_APPLY_BEFORE_DURABLE_PREPARED_ALLOWED")=="no" and cas.get("ordering_enforcement",{}).get("PREPARED_before_APPLYING_before_external"):
        check(g,"external apply before PREPARED denied", True)
    else:
        check(g,"external apply before PREPARED denied", False, f"value={plan.get('EXTERNAL_APPLY_BEFORE_DURABLE_PREPARED_ALLOWED')}")

    # G7 external apply without durable APPLYING
    g=7
    if plan.get("APPLY_WITHOUT_DURABLE_APPLYING_ALLOWED")=="no" and plan.get("AT_MOST_ONE_ATTEMPT_DURABLE_PLAN")=="PASS":
        check(g,"external apply without APPLYING denied", True)
    else:
        check(g,"external apply without APPLYING denied", False, f"value={plan.get('APPLY_WITHOUT_DURABLE_APPLYING_ALLOWED')}")

    # G8 APPLYING blind retry after restart
    g=8
    if plan.get("APPLYING_RESTART_BLIND_RETRY_ALLOWED")=="no" and crash.get("invariants",{}).get("APPLYING_RESTART_BLIND_RETRY_ALLOWED")=="no":
        check(g,"APPLYING blind retry after restart denied", True)
    else:
        check(g,"APPLYING blind retry after restart denied", False, f"plan={plan.get('APPLYING_RESTART_BLIND_RETRY_ALLOWED')} crash={crash.get('invariants',{}).get('APPLYING_RESTART_BLIND_RETRY_ALLOWED')}")

    # G9 UNKNOWN_OUTCOME blind retry
    g=9
    if plan.get("UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED")=="no" and err.get("ERROR_TAXONOMY"):
        check(g,"UNKNOWN_OUTCOME blind retry denied", True)
    else:
        check(g,"UNKNOWN_OUTCOME blind retry denied", False, f"plan={plan.get('UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED')}")

    # G10 old lease reuse
    g=10
    if plan.get("RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE")=="no" and retry.get("RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE")=="no" and retry.get("FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY")=="yes":
        check(g,"old lease reuse denied", True)
    else:
        check(g,"old lease reuse denied", False, f"plan={plan.get('RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE')} retry={retry.get('RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE')}")

    # G11 heuristic third-state resolution
    g=11
    if plan.get("HEURISTIC_RECONCILIATION_ALLOWED")=="no" and reconc.get("HEURISTIC_RECONCILIATION_ALLOWED")=="no":
        check(g,"heuristic third-state resolution denied", True)
    else:
        check(g,"heuristic third-state resolution denied", False, f"plan={plan.get('HEURISTIC_RECONCILIATION_ALLOWED')} reconc={reconc.get('HEURISTIC_RECONCILIATION_ALLOWED')}")

    # G12 semantic rollback
    g=12
    if plan.get("SEMANTIC_ROLLBACK_ALLOWED")=="no" and plan.get("RECOVERY_COMPENSATING_MUTATION_PLANNED")=="no" and err.get("TERMINAL_PERSISTENCE_FAILURE_BLIND_REAPPLY_ALLOWED")=="no":
        check(g,"semantic rollback denied", True)
    else:
        check(g,"semantic rollback denied", False, f"SEMANTIC_ROLLBACK={plan.get('SEMANTIC_ROLLBACK_ALLOWED')}")

    # G13 terminal state reprocessing
    g=13
    if plan.get("TERMINAL_STATE_REPROCESSING_ALLOWED")=="no" and discovery.get("TERMINAL_STATE_REPROCESSING_ALLOWED")=="no":
        check(g,"terminal state reprocessing denied", True)
    else:
        check(g,"terminal state reprocessing denied", False, f"plan={plan.get('TERMINAL_STATE_REPROCESSING_ALLOWED')}")

    # G14 same key + auth drift treated as replay
    g=14
    if plan.get("SAME_KEY_CHANGED_AUTHORIZATION_DURABLE_REPLAY_ALLOWED")=="no" and idem.get("SAME_KEY_CHANGED_AUTHORIZATION_DURABLE_REPLAY_ALLOWED")=="no":
        check(g,"same key+auth drift not replay", True)
    else:
        check(g,"same key+auth drift not replay", False, f"plan={plan.get('SAME_KEY_CHANGED_AUTHORIZATION_DURABLE_REPLAY_ALLOWED')} idem={idem.get('SAME_KEY_CHANGED_AUTHORIZATION_DURABLE_REPLAY_ALLOWED')}")

    # G15 production GitHub/external authority test write
    g=15
    if plan.get("PRODUCTION_GITHUB_TEST_WRITE_ALLOWED")=="no" and plan.get("PRODUCTION_EXTERNAL_AUTHORITY_TEST_WRITE_ALLOWED")=="no" and plan.get("PRODUCTION_JOURNAL_TEST_WRITE_ALLOWED")=="no" and failure.get("PRODUCTION_GITHUB_TEST_WRITE_ALLOWED")=="no":
        check(g,"no production GitHub/external/journal test write", True)
    else:
        check(g,"no production GitHub/external/journal test write", False, f"PRODUCTION_GITHUB={plan.get('PRODUCTION_GITHUB_TEST_WRITE_ALLOWED')}")

    # G16 M4-6 exclusive path claimed
    g=16
    exclusive = parallel.get("M4_7_EXCLUSIVE_WRITE_PATHS",[])
    has_m46 = any("github" in p.lower() for p in exclusive)
    forbidden = parallel.get("M4_7_FORBIDDEN_PATHS",[])
    has_forbidden_github = any("github" in p.lower() for p in forbidden)
    if not has_m46 and has_forbidden_github:
        check(g,"M4-6 exclusive not claimed by M4-7", True)
    else:
        check(g,"M4-6 exclusive not claimed by M4-7", False, f"exclusive={exclusive} forbidden_has_github={has_forbidden_github}")

    # G17 J10 missing execution row
    g=17
    if plan.get("J10_DEDICATED_EXECUTION_ROW")=="yes" and crash.get("J10_DEDICATED_EXECUTION_ROW")=="yes" and crash.get("CRASH_WINDOW_COUNT")==10:
        has_j10 = any(row.get("WINDOW_ID")=="J10" for row in crash.get("matrix",[]))
        if has_j10:
            check(g,"J10 dedicated execution row present", True)
        else:
            check(g,"J10 dedicated execution row present", False, "no J10 row in matrix")
    else:
        check(g,"J10 dedicated execution row present", False, f"plan J10={plan.get('J10_DEDICATED_EXECUTION_ROW')} crash J10={crash.get('J10_DEDICATED_EXECUTION_ROW')} count={crash.get('CRASH_WINDOW_COUNT')}")

    # G18 unresolved construction semantic TODO
    g=18
    if plan.get("CONSTRUCTION_TIME_SEMANTIC_DECISION_COUNT")==0 and dag.get("CONSTRUCTION_TIME_SEMANTIC_DECISION_COUNT")==0 and plan.get("BLOCKING_PLAN_GAP_COUNT")==0:
        check(g,"no unresolved construction TODO", True)
    else:
        check(g,"no unresolved construction TODO", False, f"CONSTRUCTION={plan.get('CONSTRUCTION_TIME_SEMANTIC_DECISION_COUNT')} DAG={dag.get('CONSTRUCTION_TIME_SEMANTIC_DECISION_COUNT')} GAP={plan.get('BLOCKING_PLAN_GAP_COUNT')}")

    # Summary
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\nPLAN_GUARD: {passed}/{total} checks passed")
    if passed==total:
        print("PLAN_GUARD=PASS")
        sys.exit(0)
    else:
        print("PLAN_GUARD=FAIL")
        sys.exit(1)

if __name__=="__main__":
    main()
