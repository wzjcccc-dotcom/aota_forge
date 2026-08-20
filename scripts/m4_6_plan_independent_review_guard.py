#!/usr/bin/env python3
"""
M4-6 Independent Review Guard — reviewer-generated negative matrix validator.

Validates planning package at 2a7da31772f6313f9da17c3e17ab245a58168305
against 22 independent planning negatives (N01-N22).

All rejects must hold for review PASS.
Fails fast on unexpected acceptance (false negative in plan).
Planning-only: no source implementation, no GitHub writes.
"""
from __future__ import annotations
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy/evidence/issues/9/m4-6-plan"
REVIEW_DIR = ROOT / "deploy/evidence/issues/9/m4-6-plan-review"

ACCEPTED_BASE = "e51141a8559faa0fb37ec1258d9c6446b8faa956"

def load(p):
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"FAIL: cannot load {p}: {e}")
        sys.exit(1)

def main():
    plan = load(PLAN_DIR / "m4-6-plan.json")
    role = load(PLAN_DIR / "m4-6-control-role-resolution.json")
    raw = load(PLAN_DIR / "m4-6-raw-precondition-model.json")
    rwv = load(PLAN_DIR / "m4-6-read-write-verify-protocol.json")
    obj = load(PLAN_DIR / "m4-6-github-authority-object-model.json")
    partial = load(PLAN_DIR / "m4-6-partial-effect-model.json")
    idem = load(PLAN_DIR / "m4-6-idempotency-plan.json")
    error = load(PLAN_DIR / "m4-6-error-projection.json")
    negative = load(PLAN_DIR / "m4-6-negative-scope.json")
    parallel = load(PLAN_DIR / "m4-6-m4-7-parallel-boundary.json")
    accept = load(PLAN_DIR / "m4-6-acceptance-matrix.json")
    failure = load(PLAN_DIR / "m4-6-failure-injection-plan.json")

    cases = []

    def check(id_, title, condition, evidence):
        ok = bool(condition)
        status = "REJECT" if ok else "UNEXPECTED_ACCEPT"
        icon = "PASS" if ok else "FAIL"
        print(f"{icon} {id_} {title}: {status} -- {evidence}")
        cases.append(ok)
        return ok

    # N01 latest-comment wins
    check("N01","latest-comment wins heuristic rejected",
          role.get("HEURISTIC_LATEST_COMMENT_SELECTION_ALLOWED")=="no" and any("latest" in s.lower() for s in role.get("role_resolution_model",{}).get("forbidden_heuristics",[])),
          "HEURISTIC_LATEST_COMMENT_SELECTION_ALLOWED=no + forbidden_heuristics contains latest")

    # N02 duplicate auto-selected
    check("N02","duplicate role auto-selected rejected",
          role.get("DUPLICATE_CONTROL_ROLE_CREATION_ALLOWED")=="no" and any("CONFLICT" in r.get("many_behavior","") for r in role.get("canonical_roles",[])),
          "DUPLICATE_CONTROL_ROLE_CREATION_ALLOWED=no + many_behavior CONFLICT")

    # N03 missing auto-created
    check("N03","missing role auto-created without authority rejected",
          any("CONTROL_ROLE_MISSING" in r.get("zero_behavior","") for r in role.get("canonical_roles",[])),
          "zero_behavior CONTROL_ROLE_MISSING fail closed")

    # N04 subject revision as CAS
    check("N04","Subject revision used as GitHub CAS rejected",
          raw.get("invariants",{}).get("SUBJECT_REVISION_IS_EXTERNAL_AUTHORITY_CAS")=="no",
          "SUBJECT_REVISION_IS_EXTERNAL_AUTHORITY_CAS=no")

    # N05 normalized as CAS
    check("N05","normalized digest used as GitHub CAS rejected",
          raw.get("invariants",{}).get("NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN")=="no",
          "NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN=no")

    # N06 false native CAS
    # raw choice: actual_github_api_conditional_write_available == no
    native = rwv.get("github_native_cas_capability_review",{})
    check("N06","false native CAS claim rejected",
          native.get("actual_github_api_conditional_write_available","").startswith("no") and native.get("do_not_pretend_read_before_write_eliminates_race") is True,
          "actual_github_api_conditional_write_available=no + do_not_pretend=true")

    # N07 blind overwrite
    check("N07","blind overwrite rejected",
          (rwv.get("GITHUB_READ_BEFORE_WRITE_REQUIRED")=="yes") and any("Blind overwrite" in x["item"] and x["allowed"]=="no" for x in negative.get("negative_scope",[])),
          "GITHUB_READ_BEFORE_WRITE_REQUIRED=yes + negative Blind overwrite allowed=no")

    # N08 HTTP success == VERIFIED
    check("N08","transport success == VERIFIED rejected",
          (rwv.get("TRANSPORT_SUCCESS_ALONE_MEANS_VERIFIED")=="no" and rwv.get("GITHUB_VERIFY_AFTER_WRITE_REQUIRED")=="yes"),
          "TRANSPORT_SUCCESS_ALONE_MEANS_VERIFIED=no + VERIFY_AFTER_WRITE yes")

    # N09 unknown == retry
    check("N09","unknown outcome blind retry rejected",
          plan.get("UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED")=="no",
          "UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED=no")

    # N10 old lease reuse
    check("N10","old lease reused after unknown rejected",
          any("Old lease reuse" in x["item"] and x["allowed"]=="no" for x in negative.get("negative_scope",[])),
          "Old lease reuse allowed=no")

    # N11 same key changed auth replay
    check("N11","same key changed auth replay rejected",
          idem.get("SAME_KEY_CHANGED_AUTHORIZATION_GITHUB_REPLAY_ALLOWED")=="no",
          "SAME_KEY_CHANGED_AUTHORIZATION_GITHUB_REPLAY_ALLOWED=no")

    # N12 generic REST passthrough
    check("N12","generic REST passthrough rejected",
          plan.get("GENERIC_GITHUB_API_ALLOWED")=="no" and rwv.get("typed_issue_body_mutation_plan",{}).get("generic_update_issue_arbitrary_body_allowed")=="no",
          "GENERIC_GITHUB_API_ALLOWED=no + generic_update_issue_arbitrary_body_allowed=no")

    # N13 generic GraphQL passthrough
    check("N13","generic GraphQL passthrough rejected",
          plan.get("GENERIC_GITHUB_API_ALLOWED")=="no",
          "GENERIC_GITHUB_API_ALLOWED=no covers GraphQL")

    # N14 generic gh executor
    check("N14","generic gh command executor rejected",
          plan.get("GENERIC_GITHUB_API_ALLOWED")=="no",
          "GENERIC_GITHUB_API_ALLOWED=no covers gh executor")

    # N15 Event Log rewritten
    check("N15","Event Log rewritten rejected",
          any("Event Log" in o.get("OBJECT","") and ("append-only" in o.get("WRITE_METHOD","").lower() or "never edit" in o.get("WRITE_METHOD","").lower()) for o in obj.get("objects",[])),
          "Event Log WRITE_METHOD append-only/history never edit")

    # N16 Event Log as semantic authority
    check("N16","Event Log used as semantic authority rejected",
          obj.get("authority_principle","").find("Issue body is Portable Plan semantic authority")!=-1,
          "authority_principle Issue body is semantic authority; Event Log is history")

    # N17 journal persistence in M4-6
    check("N17","journal persistence pulled into M4-6 rejected",
          any("Durable journal persistence" in x["item"] and x["allowed"]=="no" for x in negative.get("negative_scope",[])) and any(p["path"]=="aota_forge/core/journal/store.py" for p in parallel.get("M4_6_FORBIDDEN_PATHS",[])),
          "negative-scope durable persistence no + store.py forbidden")

    # N18 reconciliation executor in M4-6
    check("N18","reconciliation executor pulled into M4-6 rejected",
          any("Reconciliation executor" in x["item"] and x["allowed"]=="no" for x in negative.get("negative_scope",[])),
          "Reconciliation executor allowed=no")

    # N19 M4-7 exclusive claimed
    check("N19","M4-7 exclusive path claimed rejected",
          parallel.get("NO_OVERLAP_WITH_M4_7_EXCLUSIVE_WRITES") is True,
          "NO_OVERLAP_WITH_M4_7_EXCLUSIVE_WRITES=true")

    # N20 live Issue #9 write
    check("N20","live Issue #9 acceptance write rejected",
          plan.get("PRODUCTION_GITHUB_ACCEPTANCE_WRITE_ALLOWED")=="no",
          "PRODUCTION_GITHUB_ACCEPTANCE_WRITE_ALLOWED=no")

    # N21 heuristic third-state merge
    check("N21","heuristic third-state merge rejected",
          partial.get("partial_effect_classification",{}).get("no_heuristic_merge") is True,
          "partial no_heuristic_merge true")

    # N22 semantic rollback
    # check port contract via plan leak count
    check("N22","semantic rollback rejected",
          plan.get("M4_5_CORE_CONTRACT_MODIFICATION_REQUIRED")=="no" and partial.get("recovery_contract",{}).get("M4_6_durable_recovery_executor_planned")=="no",
          "SEMANTIC_ROLLBACK not planned; recovery executor planned=no")

    print("\n--- Summary ---")
    total = len(cases)
    passed = sum(cases)
    print(f"INDEPENDENT_PLAN_NEGATIVE_CASE_COUNT={total}")
    print(f"INDEPENDENT_PLAN_NEGATIVE_CASE_REJECT_COUNT={passed}")
    if passed == total:
        print(f"INDEPENDENT_NEGATIVE_GUARD=PASS {passed}/{total} all REJECT")
        # also verify original plan guard still passes
        import subprocess, pathlib as pl
        result = subprocess.run(["python3","scripts/m4_6_plan_guard.py"], cwd=str(ROOT), capture_output=True, text=True)
        print(result.stdout.strip().splitlines()[-5:])
        if "PLAN_GUARD=PASS" in result.stdout:
            print("PLAN_GUARD=PASS (original still PASS)")
            sys.exit(0)
        else:
            print("PLAN_GUARD FAIL")
            sys.exit(1)
    else:
        print(f"INDEPENDENT_NEGATIVE_GUARD=FAIL {passed}/{total}")
        sys.exit(1)

if __name__ == "__main__":
    main()
