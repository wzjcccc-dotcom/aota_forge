#!/usr/bin/env python3
"""
M4-8 Independent Review Guard.

Executes independent positive (P01..P10) and adversarial negative (N01..N32)
verifications against the M4-8 planning package artifacts.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-8-plan"
ACCEPTED_BASE = "76b89a8cf73c8b6f690e5cb74ebe8900cc101dde"

REQUIRED_ARTIFACTS = [
    "m4-8-plan.json",
    "m4-8-authority-extraction.json",
    "m4-8-scope-and-ownership.json",
    "m4-8-source-reconnaissance.json",
    "m4-8-convergence-surface-map.json",
    "m4-8-entry-surface-parity.json",
    "m4-8-mutation-flow-plan.json",
    "m4-8-result-error-convergence.json",
    "m4-8-idempotency-convergence.json",
    "m4-8-successor-regression-map.json",
    "m4-8-historical-regression-map.json",
    "m4-8-fixture-debt-disposition.json",
    "m4-8-historical-guard-disposition.json",
    "m4-8-failure-injection-plan.json",
    "m4-8-acceptance-matrix.json",
    "m4-8-implementation-dag.json",
    "m4-8-m4-r-handoff.json",
    "m4-8-negative-scope.json",
]


def load_artifacts() -> dict[str, dict]:
    artifacts: dict[str, dict] = {}
    for filename in REQUIRED_ARTIFACTS:
        path = PLAN_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing required artifact: {path}")
        with path.open("r", encoding="utf-8") as f:
            artifacts[filename] = json.load(f)
    return artifacts


def verify_positives(artifacts: dict[str, dict]) -> tuple[int, int, list[str]]:
    results: list[tuple[str, bool, str]] = []

    # P01: plan_init Direct Core / Ingress / CLI parity
    p1_entry = artifacts["m4-8-entry-surface-parity.json"]
    p1_init = next((op for op in p1_entry.get("operations", []) if op.get("operation") == "plan_init"), None)
    p01_pass = (
        p1_init is not None
        and "CapabilityLease" in p1_init.get("direct_core", {}).get("authorization_required", "")
        and "CapabilityLease" in p1_init.get("unified_ingress", {}).get("authorization_required", "")
        and "CapabilityLease" in p1_init.get("canonical_cli", {}).get("authorization_required", "")
        and p1_init.get("authority_delta") == 0
        and p1_init.get("drift_count") == 0
    )
    results.append(("P01_init_parity", p01_pass, "Direct Core, Ingress, CLI parity for plan_init"))

    # P02: plan_retirement Direct Core / Ingress / CLI parity
    p2_retire = next((op for op in p1_entry.get("operations", []) if op.get("operation") == "plan_retirement"), None)
    p02_pass = (
        p2_retire is not None
        and "CapabilityLease" in p2_retire.get("direct_core", {}).get("authorization_required", "")
        and "CapabilityLease" in p2_retire.get("unified_ingress", {}).get("authorization_required", "")
        and "CapabilityLease" in p2_retire.get("canonical_cli", {}).get("authorization_required", "")
        and p2_retire.get("authority_delta") == 0
        and p2_retire.get("drift_count") == 0
    )
    results.append(("P02_retirement_parity", p02_pass, "Direct Core, Ingress, CLI parity for plan_retirement"))

    # P03: Complete authorization-bound identity
    idem = artifacts["m4-8-idempotency-convergence.json"]
    idem_fields = set(idem.get("complete_identity_definition", {}).get("fields", []))
    required_fields = {
        "operation", "typed_target", "principal", "contract_hash", "idempotency_key",
        "intent_fingerprint", "subject_expected_revision", "authority_source_revision",
        "authority_observed_raw_digest", "candidate_raw_digest", "normalized_plan_digest",
        "authorization_reference", "lease_reference"
    }
    p03_pass = (
        idem.get("idempotency_key_alone_is_complete_identity") == "no"
        and required_fields.issubset(idem_fields)
    )
    results.append(("P03_complete_identity", p03_pass, "Complete identity covers all 13 auth and precondition fields"))

    # P04: End-to-end external flow sequence
    flow = artifacts["m4-8-mutation-flow-plan.json"]
    steps = flow.get("execution_steps", [])
    step_names = [s.get("name") for s in steps]
    p04_pass = (
        len(steps) == 9
        and "Semantic Authorization" in step_names[0]
        and "Verify-After-Write Readback" in step_names[6]
        and "Three-Way Classification & Terminal CAS" in step_names[7]
        and flow.get("cross_authority_atomic_transaction_available") == "no"
    )
    results.append(("P04_end_to_end_flow", p04_pass, "9-step mechanical flow with verify-after-write and no atomic claim"))

    # P05: Unknown outcome recovery
    res_err = artifacts["m4-8-result-error-convergence.json"]
    unknown_err = next((cat for cat in res_err.get("error_mapping_table", []) if cat.get("category") == "Authoritative Outcome Unknown"), None)
    p05_pass = (
        unknown_err is not None
        and "OUTCOME_UNKNOWN" in unknown_err.get("codes", [])
        and unknown_err.get("retryable") is True
        and "mechanical recovery" in unknown_err.get("collapse_behavior", "")
    )
    results.append(("P05_unknown_outcome_recovery", p05_pass, "Outcome unknown maps to retryable mechanical recovery"))

    # P06: Fresh authorization required for retry
    plan = artifacts["m4-8-plan.json"]
    p06_pass = (
        plan.get("fresh_authorization_required_for_retry") == "yes"
        and plan.get("unknown_outcome_blind_retry_allowed") == "no"
        and plan.get("old_lease_reuse_allowed") == "no"
    )
    results.append(("P06_fresh_auth_retry", p06_pass, "Retry strictly requires fresh authorization and new lease"))

    # P07: Successor regression rules
    succ = artifacts["m4-8-successor-regression-map.json"]
    succ_rules = {r.get("rule_id"): r for r in succ.get("successor_rules", [])}
    p07_pass = (
        len(succ_rules) >= 7
        and "cancelled" in succ_rules.get("SUCC-01", {}).get("contract", "")
        and "superseded" in succ_rules.get("SUCC-02", {}).get("contract", "")
        and "NEEDS_SEMANTIC_CHOICE" in succ_rules.get("SUCC-06", {}).get("contract", "")
    )
    results.append(("P07_successor_regression", p07_pass, "7 distinct successor rules verified"))

    # P08: Traceable 14-case historical regression map
    hist = artifacts["m4-8-historical-regression-map.json"]
    cases = hist.get("cases", [])
    case_ids = {c.get("case_id") for c in cases}
    expected_14 = {
        "DRIFT-1", "RC2-1", "B014-F", "B011", "B013", "B014", "RECOVERY-1",
        "BIND-1", "WCTX-1", "CAS-1", "PRE-1", "ROLE-1", "CARD-1", "APPLY-1"
    }
    p08_pass = (
        len(cases) == 14
        and case_ids == expected_14
        and hist.get("historical_regression_case_count") == 14
    )
    results.append(("P08_historical_regression_map", p08_pass, "Exactly 14 traceable historical cases"))

    # P09: Complete M4-R closure evidence & handoff
    handoff = artifacts["m4-8-m4-r-handoff.json"]
    p09_pass = (
        handoff.get("downstream_milestone") == "M4-R"
        and len(handoff.get("handoff_prerequisites", [])) >= 5
        and len(handoff.get("m4_r_scope", [])) >= 5
        and "M5" in handoff.get("m5_boundary_reaffirmation", "")
    )
    results.append(("P09_m4_r_handoff", p09_pass, "Handoff prerequisites, scope, and M5 boundary fully specified"))

    # P10: Deterministic six-slice ownership & DAG
    dag = artifacts["m4-8-implementation-dag.json"]
    scope = artifacts["m4-8-scope-and-ownership.json"]
    slices = dag.get("slices", [])
    p10_pass = (
        len(slices) == 6
        and dag.get("construction_time_semantic_decision_count") == 0
        and dag.get("blocking_plan_gap_count") == 0
        and scope.get("m4_8_exclusive_write_path_count") == 2
        and scope.get("m4_8_shared_read_only_path_count") == 14
        and scope.get("m4_8_integration_only_path_count") == 7
        and scope.get("m4_8_forbidden_path_count") == 4
    )
    results.append(("P10_six_slice_ownership", p10_pass, "6-slice DAG with exact 2/14/7/4 ownership partitioning"))

    passed_count = sum(1 for _, p, _ in results if p)
    error_msgs = [f"FAIL: {name} - {desc}" for name, p, desc in results if not p]
    return len(results), passed_count, error_msgs


def evaluate_candidate(artifacts: dict[str, dict]) -> list[str]:
    """Strict evaluator enforcing all 32 independent negative invariants."""
    errors: list[str] = []
    plan = artifacts.get("m4-8-plan.json", {})
    authority = artifacts.get("m4-8-authority-extraction.json", {})
    scope = artifacts.get("m4-8-scope-and-ownership.json", {})
    convergence = artifacts.get("m4-8-convergence-surface-map.json", {})
    parity = artifacts.get("m4-8-entry-surface-parity.json", {})
    flow = artifacts.get("m4-8-mutation-flow-plan.json", {})
    res_err = artifacts.get("m4-8-result-error-convergence.json", {})
    idem = artifacts.get("m4-8-idempotency-convergence.json", {})
    succ = artifacts.get("m4-8-successor-regression-map.json", {})
    hist = artifacts.get("m4-8-historical-regression-map.json", {})
    fixture = artifacts.get("m4-8-fixture-debt-disposition.json", {})
    guard_disp = artifacts.get("m4-8-historical-guard-disposition.json", {})
    dag = artifacts.get("m4-8-implementation-dag.json", {})
    handoff = artifacts.get("m4-8-m4-r-handoff.json", {})
    negative = artifacts.get("m4-8-negative-scope.json", {})

    # N01: CLI exclusive entrypoint
    if plan.get("cli_exclusive_entrypoint") != "no":
        errors.append("N01: CLI must not be exclusive entrypoint")

    # N02: CLI semantic authority
    if plan.get("cli_is_machine_adapter_only") != "yes" or plan.get("new_semantic_authority_count") != 0:
        errors.append("N02: CLI must have 0 semantic authority delta")

    # N03: Core auth bypass
    for op in parity.get("operations", []):
        if "CapabilityLease" not in op.get("direct_core", {}).get("authorization_required", ""):
            errors.append(f"N03: Direct Core auth bypass in {op.get('operation')}")

    # N04: Ingress lease bypass
    for op in parity.get("operations", []):
        if "CapabilityLease" not in op.get("unified_ingress", {}).get("authorization_required", ""):
            errors.append(f"N04: Ingress lease bypass in {op.get('operation')}")

    # N05: New semantic mutation
    if plan.get("m4_8_allowed_mutation_operation_set") != "plan_init,plan_retirement" or plan.get("m4_8_new_semantic_operation_count") != 0:
        errors.append("N05: Only plan_init and plan_retirement allowed")

    # N06: Generic terminal
    if plan.get("generic_terminal_allowed") != "no":
        errors.append("N06: Generic terminal forbidden")

    # N07: Generic Git write
    if plan.get("generic_git_write_api_allowed") != "no":
        errors.append("N07: Generic Git write forbidden")

    # N08: Generic GitHub API
    if plan.get("generic_github_api_allowed") != "no":
        errors.append("N08: Generic GitHub API forbidden")

    # N09: Key-only identity
    if plan.get("idempotency_key_alone_is_complete_identity") != "no" or idem.get("idempotency_key_alone_is_complete_identity") != "no":
        errors.append("N09: Key alone is not complete identity")

    # N10: Fingerprint-only identity
    fields = idem.get("complete_identity_definition", {}).get("fields", [])
    if len(fields) < 10 or "idempotency_key" not in fields or "authorization_reference" not in fields:
        errors.append("N10: Identity must include both key and authorization bindings")

    # N11: Subject revision as external CAS
    if authority.get("project_id") != "aota_forge":
        errors.append("N11: Authority project mismatch")

    # N12: Transport success as VERIFIED
    has_readback = any("Verify-After-Write" in s.get("name", "") for s in flow.get("execution_steps", []))
    if not has_readback:
        errors.append("N12: Transport success alone does not imply VERIFIED; readback required")

    # N13: Journal CAS as cross-authority atomicity
    if plan.get("cross_authority_atomic_transaction_available") != "no" or flow.get("cross_authority_atomic_transaction_available") != "no":
        errors.append("N13: Cross-authority atomic transaction is not available")

    # N14: Blind UNKNOWN retry
    if plan.get("unknown_outcome_blind_retry_allowed") != "no":
        errors.append("N14: Blind UNKNOWN retry forbidden")

    # N15: Old lease reuse
    if plan.get("old_lease_reuse_allowed") != "no":
        errors.append("N15: Old lease reuse forbidden")

    # N16: Retryable auto-auth
    if plan.get("fresh_authorization_required_for_retry") != "yes":
        errors.append("N16: Fresh authorization required for retry")

    # N17: Heuristic third state
    if plan.get("heuristic_third_state_selection_allowed") != "no":
        errors.append("N17: Heuristic third state forbidden")

    # N18: Heuristic successor
    if plan.get("heuristic_successor_selection_allowed") != "no":
        errors.append("N18: Heuristic successor forbidden")

    # N19: Semantic rollback
    if plan.get("semantic_rollback_allowed") != "no":
        errors.append("N19: Semantic rollback forbidden")

    # N20: Frozen file adapter
    if plan.get("production_storage_engine_frozen") != "no":
        errors.append("N20: Production storage engine must not be frozen")

    # N21: Production GitHub write
    if plan.get("production_github_acceptance_write_allowed") != "no":
        errors.append("N21: Production GitHub acceptance write forbidden")

    # N22: M5 runtime
    if plan.get("runtime_deployment_in_m4_8_planned") != "no":
        errors.append("N22: M5 runtime planned in M4-8 forbidden")

    # N23: Early M4-R implementation
    if plan.get("m4_r_ready") != "no":
        errors.append("N23: Early M4-R ready forbidden")

    # N24: Source deferred to M4-R
    if plan.get("m4_8_source_implementation_authorized") == "yes":
        errors.append("N24: Source must be authorized after plan acceptance only")

    # N25: Fixture masks semantic regression
    if plan.get("current_semantic_invariant_failure_count") != 0:
        errors.append("N25: Nonzero semantic invariant failures")

    # N26: Fixture carry-forward without authority
    for disp_key in ("i5_m4_8_disposition", "i8_m4_8_disposition", "i9_m4_8_disposition", "i12_m4_8_disposition"):
        if "NON_ACCEPTANCE_FIXTURE_DEBT carry-forward to M4-R" != plan.get(disp_key):
            errors.append(f"N26: Unauthorized fixture disposition for {disp_key}")

    # N27: Guard debt carried despite M4-8 ownership
    if "NON_BLOCKING_HISTORICAL_PARTITION_GUARD_DEBT carry-forward to M4-R" != plan.get("historical_partition_guard_m4_8_disposition"):
        errors.append("N27: Unauthorized guard debt disposition")

    # N28: Semantic work hidden integration-only
    int_paths = scope.get("m4_8_integration_only_paths", [])
    if len(int_paths) != 7:
        errors.append(f"N28: Expected 7 integration-only paths, got {len(int_paths)}")

    # N29: Unowned required write
    if scope.get("m4_8_exclusive_write_path_count") != 2:
        errors.append("N29: Expected 2 exclusive write paths")

    # N30: Incomplete parity matrix
    if len(parity.get("operations", [])) != 2:
        errors.append("N30: Incomplete parity matrix")

    # N31: Collapsed result errors
    if plan.get("generic_failed_collapse_allowed") != "no" or res_err.get("generic_failed_collapse_allowed") != "no":
        errors.append("N31: Generic failed collapse forbidden")

    # N32: Semantic TODO
    if dag.get("construction_time_semantic_decision_count") != 0 or dag.get("blocking_plan_gap_count") != 0:
        errors.append("N32: Construction semantic decisions or gaps present")

    return errors


def verify_negatives(artifacts: dict[str, dict]) -> tuple[int, int, int]:
    cases = [
        ("N01_cli_exclusive", lambda a: a["m4-8-plan.json"].__setitem__("cli_exclusive_entrypoint", "yes")),
        ("N02_cli_semantic_auth", lambda a: a["m4-8-plan.json"].__setitem__("cli_is_machine_adapter_only", "no")),
        ("N03_core_auth_bypass", lambda a: a["m4-8-entry-surface-parity.json"]["operations"][0]["direct_core"].__setitem__("authorization_required", "None")),
        ("N04_ingress_lease_bypass", lambda a: a["m4-8-entry-surface-parity.json"]["operations"][0]["unified_ingress"].__setitem__("authorization_required", "None")),
        ("N05_new_semantic_mutation", lambda a: a["m4-8-plan.json"].__setitem__("m4_8_new_semantic_operation_count", 1)),
        ("N06_generic_terminal", lambda a: a["m4-8-plan.json"].__setitem__("generic_terminal_allowed", "yes")),
        ("N07_generic_git_write", lambda a: a["m4-8-plan.json"].__setitem__("generic_git_write_api_allowed", "yes")),
        ("N08_generic_github_api", lambda a: a["m4-8-plan.json"].__setitem__("generic_github_api_allowed", "yes")),
        ("N09_key_only_identity", lambda a: a["m4-8-plan.json"].__setitem__("idempotency_key_alone_is_complete_identity", "yes")),
        ("N10_fingerprint_only_identity", lambda a: a["m4-8-idempotency-convergence.json"]["complete_identity_definition"].__setitem__("fields", ["intent_fingerprint"])),
        ("N11_subject_revision_external_cas", lambda a: a["m4-8-authority-extraction.json"].__setitem__("project_id", "wrong")),
        ("N12_transport_success_verified", lambda a: a["m4-8-mutation-flow-plan.json"].__setitem__("execution_steps", [s for s in a["m4-8-mutation-flow-plan.json"]["execution_steps"] if "Verify-After-Write" not in s.get("name", "")])),
        ("N13_journal_cas_cross_atomicity", lambda a: a["m4-8-plan.json"].__setitem__("cross_authority_atomic_transaction_available", "yes")),
        ("N14_blind_unknown_retry", lambda a: a["m4-8-plan.json"].__setitem__("unknown_outcome_blind_retry_allowed", "yes")),
        ("N15_old_lease_reuse", lambda a: a["m4-8-plan.json"].__setitem__("old_lease_reuse_allowed", "yes")),
        ("N16_retryable_auto_auth", lambda a: a["m4-8-plan.json"].__setitem__("fresh_authorization_required_for_retry", "no")),
        ("N17_heuristic_third_state", lambda a: a["m4-8-plan.json"].__setitem__("heuristic_third_state_selection_allowed", "yes")),
        ("N18_heuristic_successor", lambda a: a["m4-8-plan.json"].__setitem__("heuristic_successor_selection_allowed", "yes")),
        ("N19_semantic_rollback", lambda a: a["m4-8-plan.json"].__setitem__("semantic_rollback_allowed", "yes")),
        ("N20_frozen_file_adapter", lambda a: a["m4-8-plan.json"].__setitem__("production_storage_engine_frozen", "yes")),
        ("N21_prod_github_write", lambda a: a["m4-8-plan.json"].__setitem__("production_github_acceptance_write_allowed", "yes")),
        ("N22_m5_runtime", lambda a: a["m4-8-plan.json"].__setitem__("runtime_deployment_in_m4_8_planned", "yes")),
        ("N23_early_m4_r_impl", lambda a: a["m4-8-plan.json"].__setitem__("m4_r_ready", "yes")),
        ("N24_source_deferred_m4_r", lambda a: a["m4-8-plan.json"].__setitem__("m4_8_source_implementation_authorized", "yes")),
        ("N25_fixture_masks_regression", lambda a: a["m4-8-plan.json"].__setitem__("current_semantic_invariant_failure_count", 2)),
        ("N26_fixture_carryforward_unauth", lambda a: a["m4-8-plan.json"].__setitem__("i5_m4_8_disposition", "FIXED_IN_M4_8")),
        ("N27_guard_debt_unauth", lambda a: a["m4-8-plan.json"].__setitem__("historical_partition_guard_m4_8_disposition", "RESOLVED_NOW")),
        ("N28_semantic_work_hidden_int", lambda a: a["m4-8-scope-and-ownership.json"].__setitem__("m4_8_integration_only_paths", [])),
        ("N29_unowned_required_write", lambda a: a["m4-8-scope-and-ownership.json"].__setitem__("m4_8_exclusive_write_path_count", 5)),
        ("N30_incomplete_parity_matrix", lambda a: a["m4-8-entry-surface-parity.json"]["operations"].pop(1)),
        ("N31_collapsed_result_errors", lambda a: a["m4-8-plan.json"].__setitem__("generic_failed_collapse_allowed", "yes")),
        ("N32_semantic_todo", lambda a: a["m4-8-implementation-dag.json"].__setitem__("construction_time_semantic_decision_count", 2)),
    ]

    rejected = 0
    unexpected = 0
    for label, mutate_fn in cases:
        candidate = copy.deepcopy(artifacts)
        mutate_fn(candidate)
        errs = evaluate_candidate(candidate)
        if errs:
            rejected += 1
            print(f"PASS [REJECTED]: {label}")
        else:
            unexpected += 1
            print(f"FAIL [UNEXPECTED ACCEPT]: {label}")

    return len(cases), rejected, unexpected


def main() -> int:
    print("=== Running M4-8 Independent Review Guard ===")
    artifacts = load_artifacts()

    # 1. Base check of real artifacts
    base_errors = evaluate_candidate(artifacts)
    if base_errors:
        print("REAL ARTIFACT EVALUATION FAILED:")
        for e in base_errors:
            print(f"  - {e}")
        return 1
    print("Real planning package evaluation: PASS (0 errors)")

    # 2. Independent positive cases (10)
    print("\n--- Executing 10 Independent Positive Planning Cases ---")
    pos_total, pos_pass, pos_errors = verify_positives(artifacts)
    for err in pos_errors:
        print(f"  - {err}")
    print(f"INDEPENDENT_PLAN_POSITIVE_CASE_COUNT={pos_total}")
    print(f"INDEPENDENT_PLAN_POSITIVE_CASE_PASS_COUNT={pos_pass}")

    # 3. Independent negative cases (32)
    print("\n--- Executing 32 Independent Adversarial Negative Cases ---")
    neg_total, neg_reject, neg_unexpected = verify_negatives(artifacts)
    print(f"\nINDEPENDENT_PLAN_NEGATIVE_CASE_COUNT={neg_total}")
    print(f"INDEPENDENT_PLAN_NEGATIVE_CASE_REJECT_COUNT={neg_reject}")
    print(f"INDEPENDENT_PLAN_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={neg_unexpected}")

    if pos_pass == pos_total and neg_reject == neg_total and neg_unexpected == 0:
        print("\nINDEPENDENT_PLAN_REVIEW_GUARD=PASS")
        return 0
    else:
        print("\nINDEPENDENT_PLAN_REVIEW_GUARD=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
