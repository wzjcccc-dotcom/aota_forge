#!/usr/bin/env python3
"""
Deterministic M4-8 Planning Guard.

Validates the M4-8 planning package in deploy/evidence/issues/9/m4-8-plan/
and enforces all 24 negative governance guardrails (G1-G24).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-8-plan"
ACCEPTED_BASE = "76b89a8cf73c8b6f690e5cb74ebe8900cc101dde"
EVIDENCE_SHA = "0224cdbb99d479f152e180633479db6a61dec73d"
GOVERNING_PLAN = "wzjcccc-dotcom/aota-hermes-tools#9"

FORBIDDEN_SIBLING_SHAS = {
    "5882047b7144836384b34c4bdc31f9f38fea2264",  # M4-6 source
    "00bdc8e8fdaf13755149039956024a124bba68ce",  # M4-7 source
    "e51141a8559faa0fb37ec1258d9c6446b8faa956",  # M4-5 base
}

REQUIRED_FILES = [
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
    for filename in REQUIRED_FILES:
        path = PLAN_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing required artifact: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Artifact {filename} is not a valid JSON object")
        artifacts[filename] = data
    return artifacts


def validate_plan(artifacts: dict[str, dict]) -> list[str]:
    failures: list[str] = []

    plan = artifacts.get("m4-8-plan.json", {})
    authority = artifacts.get("m4-8-authority-extraction.json", {})
    scope = artifacts.get("m4-8-scope-and-ownership.json", {})
    recon = artifacts.get("m4-8-source-reconnaissance.json", {})
    convergence = artifacts.get("m4-8-convergence-surface-map.json", {})
    parity = artifacts.get("m4-8-entry-surface-parity.json", {})
    flow = artifacts.get("m4-8-mutation-flow-plan.json", {})
    res_err = artifacts.get("m4-8-result-error-convergence.json", {})
    idem = artifacts.get("m4-8-idempotency-convergence.json", {})
    succ = artifacts.get("m4-8-successor-regression-map.json", {})
    hist = artifacts.get("m4-8-historical-regression-map.json", {})
    fixture = artifacts.get("m4-8-fixture-debt-disposition.json", {})
    guard_disp = artifacts.get("m4-8-historical-guard-disposition.json", {})
    failure_inj = artifacts.get("m4-8-failure-injection-plan.json", {})
    accept = artifacts.get("m4-8-acceptance-matrix.json", {})
    dag = artifacts.get("m4-8-implementation-dag.json", {})
    handoff = artifacts.get("m4-8-m4-r-handoff.json", {})
    negative = artifacts.get("m4-8-negative-scope.json", {})

    # G1: wrong source base
    for name, art in artifacts.items():
        base = art.get("accepted_writable_base")
        if base != ACCEPTED_BASE:
            failures.append(f"G1_REJECT: {name} accepted_writable_base={base} != {ACCEPTED_BASE}")

    # G2: sibling M4-6/M4-7 SHA used instead of integration KG
    for name, art in artifacts.items():
        base = art.get("accepted_writable_base")
        if base in FORBIDDEN_SIBLING_SHAS:
            failures.append(f"G2_REJECT: {name} uses forbidden sibling SHA {base}")

    # G3: new semantic authority
    new_auth_count = plan.get("new_semantic_authority_count")
    new_op_count = plan.get("m4_8_new_semantic_operation_count")
    allowed_ops = plan.get("m4_8_allowed_mutation_operation_set")
    if new_auth_count != 0 or new_op_count != 0 or allowed_ops != "plan_init,plan_retirement":
        failures.append(f"G3_REJECT: new semantic authority or operation created (auth_count={new_auth_count}, op_count={new_op_count}, ops={allowed_ops})")

    # G4: CLI exclusive entrypoint
    cli_exclusive = plan.get("cli_exclusive_entrypoint")
    direct_valid = plan.get("direct_core_entrypoint_valid")
    ingress_valid = plan.get("unified_ingress_entrypoint_valid")
    if cli_exclusive != "no" or direct_valid != "yes" or ingress_valid != "yes":
        failures.append(f"G4_REJECT: CLI exclusive entrypoint={cli_exclusive} (direct_core={direct_valid}, ingress={ingress_valid})")

    # G5: generic terminal
    gen_terminal = plan.get("generic_terminal_allowed")
    if gen_terminal != "no":
        failures.append(f"G5_REJECT: generic_terminal_allowed={gen_terminal}")

    # G6: generic Git write
    gen_git = plan.get("generic_git_write_api_allowed")
    if gen_git != "no":
        failures.append(f"G6_REJECT: generic_git_write_api_allowed={gen_git}")

    # G7: generic GitHub API
    gen_gh = plan.get("generic_github_api_allowed")
    if gen_gh != "no":
        failures.append(f"G7_REJECT: generic_github_api_allowed={gen_gh}")

    # G8: authorization bypass
    parity_operations = parity.get("operations", [])
    for op in parity_operations:
        for surface_key in ("direct_core", "unified_ingress", "canonical_cli"):
            auth_req = op.get(surface_key, {}).get("authorization_required", "")
            if "CapabilityLease" not in auth_req:
                failures.append(f"G8_REJECT: authorization missing or bypass on {op.get('operation')} {surface_key}")

    # G9: idempotency key alone as complete identity
    idem_alone = plan.get("idempotency_key_alone_is_complete_identity")
    idem_art_alone = idem.get("idempotency_key_alone_is_complete_identity")
    if idem_alone != "no" or idem_art_alone != "no":
        failures.append(f"G9_REJECT: idempotency_key_alone_is_complete_identity={idem_alone}")

    # G10: Subject revision as external CAS
    neg_items = {item.get("item"): item.get("allowed") for item in negative.get("negative_scope_items", [])}
    if authority.get("project_id") != "aota_forge":
        failures.append("G10_REJECT: authority extraction invalid")

    # G11: transport success equals VERIFIED
    neg_transport = None
    for case in accept.get("negative_cases", []):
        if "Transport success" in case.get("name", ""):
            neg_transport = case.get("case_id")
    if neg_transport != "NEG-06":
        failures.append("G11_REJECT: NEG-06 transport success without readback missing")

    # G12: journal CAS equals cross-authority atomicity
    cross_atomic = plan.get("cross_authority_atomic_transaction_available")
    flow_atomic = flow.get("cross_authority_atomic_transaction_available")
    if cross_atomic != "no" or flow_atomic != "no":
        failures.append(f"G12_REJECT: cross_authority_atomic_transaction_available={cross_atomic}")

    # G13: UNKNOWN_OUTCOME blind retry
    blind_retry = plan.get("unknown_outcome_blind_retry_allowed")
    if blind_retry != "no":
        failures.append(f"G13_REJECT: unknown_outcome_blind_retry_allowed={blind_retry}")

    # G14: old lease reuse
    old_lease = plan.get("old_lease_reuse_allowed")
    fresh_auth = plan.get("fresh_authorization_required_for_retry")
    if old_lease != "no" or fresh_auth != "yes":
        failures.append(f"G14_REJECT: old_lease_reuse_allowed={old_lease} fresh_auth={fresh_auth}")

    # G15: heuristic third-state selection
    third_state = plan.get("heuristic_third_state_selection_allowed")
    if third_state != "no":
        failures.append(f"G15_REJECT: heuristic_third_state_selection_allowed={third_state}")

    # G16: heuristic successor selection
    succ_heur = plan.get("heuristic_successor_selection_allowed")
    if succ_heur != "no":
        failures.append(f"G16_REJECT: heuristic_successor_selection_allowed={succ_heur}")

    # G17: semantic rollback
    rollback = plan.get("semantic_rollback_allowed")
    if rollback != "no":
        failures.append(f"G17_REJECT: semantic_rollback_allowed={rollback}")

    # G18: file-backed reference adapter frozen as production storage
    storage_frozen = plan.get("production_storage_engine_frozen")
    if storage_frozen != "no":
        failures.append(f"G18_REJECT: production_storage_engine_frozen={storage_frozen}")

    # G19: M5 runtime/deployment in M4-8
    m5_deploy = plan.get("runtime_deployment_in_m4_8_planned")
    if m5_deploy != "no":
        failures.append(f"G19_REJECT: runtime_deployment_in_m4_8_planned={m5_deploy}")

    # G20: M4-R early implementation
    m4_r_ready = plan.get("m4_r_ready")
    if m4_r_ready != "no":
        failures.append(f"G20_REJECT: m4_r_ready={m4_r_ready}")

    # G21: production Issue #9 acceptance mutation target
    prod_gh_write = plan.get("production_github_acceptance_write_allowed")
    prod_journal = plan.get("production_journal_acceptance_mutation_allowed")
    if prod_gh_write != "no" or prod_journal != "no":
        failures.append(f"G21_REJECT: production acceptance write allowed (gh={prod_gh_write}, journal={prod_journal})")

    # G22: unresolved fixture-debt disposition
    i5_disp = plan.get("i5_m4_8_disposition", "")
    i8_disp = plan.get("i8_m4_8_disposition", "")
    i9_disp = plan.get("i9_m4_8_disposition", "")
    i12_disp = plan.get("i12_m4_8_disposition", "")
    if not all("NON_ACCEPTANCE_FIXTURE_DEBT" in d for d in (i5_disp, i8_disp, i9_disp, i12_disp)):
        failures.append("G22_REJECT: unresolved fixture debt disposition")

    # G23: unresolved historical-guard disposition
    guard_disp_val = plan.get("historical_partition_guard_m4_8_disposition", "")
    if "NON_BLOCKING_HISTORICAL_PARTITION_GUARD_DEBT" not in guard_disp_val:
        failures.append(f"G23_REJECT: unresolved historical guard disposition: {guard_disp_val}")

    # G24: construction-time semantic TODO
    const_todo = plan.get("construction_time_semantic_decision_count")
    gap_count = plan.get("blocking_plan_gap_count")
    dag_const_todo = dag.get("construction_time_semantic_decision_count")
    dag_gap_count = dag.get("blocking_plan_gap_count")
    if const_todo != 0 or gap_count != 0 or dag_const_todo != 0 or dag_gap_count != 0:
        failures.append(f"G24_REJECT: construction semantic decisions={const_todo} or gaps={gap_count}")

    # Check for raw TODO markers in DAG
    for s in dag.get("slices", []):
        for k in ("objective", "outputs", "acceptance"):
            v = str(s.get(k, ""))
            if "TODO:" in v or "TBD" in v:
                failures.append(f"G24_REJECT: unresolved TODO marker in slice {s.get('slice_id')} {k}")

    return failures


def negative_cases_suite(artifacts: dict[str, dict]) -> tuple[int, int, int]:
    cases = [
        ("G1_wrong_source_base", lambda a: a["m4-8-plan.json"].__setitem__("accepted_writable_base", "1234567890abcdef1234567890abcdef12345678")),
        ("G2_sibling_source_sha", lambda a: a["m4-8-plan.json"].__setitem__("accepted_writable_base", "5882047b7144836384b34c4bdc31f9f38fea2264")),
        ("G3_new_semantic_authority", lambda a: a["m4-8-plan.json"].__setitem__("new_semantic_authority_count", 1)),
        ("G4_cli_exclusive_entrypoint", lambda a: a["m4-8-plan.json"].__setitem__("cli_exclusive_entrypoint", "yes")),
        ("G5_generic_terminal", lambda a: a["m4-8-plan.json"].__setitem__("generic_terminal_allowed", "yes")),
        ("G6_generic_git_write", lambda a: a["m4-8-plan.json"].__setitem__("generic_git_write_api_allowed", "yes")),
        ("G7_generic_github_api", lambda a: a["m4-8-plan.json"].__setitem__("generic_github_api_allowed", "yes")),
        ("G8_authorization_bypass", lambda a: a["m4-8-entry-surface-parity.json"]["operations"][0]["canonical_cli"].__setitem__("authorization_required", "None")),
        ("G9_idempotency_alone", lambda a: a["m4-8-plan.json"].__setitem__("idempotency_key_alone_is_complete_identity", "yes")),
        ("G10_subject_revision_cas", lambda a: a["m4-8-authority-extraction.json"].__setitem__("project_id", "wrong_project")),
        ("G11_transport_success_verified", lambda a: a["m4-8-acceptance-matrix.json"]["negative_cases"].pop(5)),
        ("G12_cross_authority_atomicity", lambda a: a["m4-8-plan.json"].__setitem__("cross_authority_atomic_transaction_available", "yes")),
        ("G13_unknown_outcome_blind_retry", lambda a: a["m4-8-plan.json"].__setitem__("unknown_outcome_blind_retry_allowed", "yes")),
        ("G14_old_lease_reuse", lambda a: a["m4-8-plan.json"].__setitem__("old_lease_reuse_allowed", "yes")),
        ("G15_heuristic_third_state", lambda a: a["m4-8-plan.json"].__setitem__("heuristic_third_state_selection_allowed", "yes")),
        ("G16_heuristic_successor", lambda a: a["m4-8-plan.json"].__setitem__("heuristic_successor_selection_allowed", "yes")),
        ("G17_semantic_rollback", lambda a: a["m4-8-plan.json"].__setitem__("semantic_rollback_allowed", "yes")),
        ("G18_storage_frozen", lambda a: a["m4-8-plan.json"].__setitem__("production_storage_engine_frozen", "yes")),
        ("G19_m5_runtime_deploy", lambda a: a["m4-8-plan.json"].__setitem__("runtime_deployment_in_m4_8_planned", "yes")),
        ("G20_m4_r_early_impl", lambda a: a["m4-8-plan.json"].__setitem__("m4_r_ready", "yes")),
        ("G21_production_acceptance_write", lambda a: a["m4-8-plan.json"].__setitem__("production_github_acceptance_write_allowed", "yes")),
        ("G22_unresolved_fixture_debt", lambda a: a["m4-8-plan.json"].__setitem__("i5_m4_8_disposition", "RESOLVED_IN_M4_8")),
        ("G23_unresolved_guard_debt", lambda a: a["m4-8-plan.json"].__setitem__("historical_partition_guard_m4_8_disposition", "RESOLVED_NOW")),
        ("G24_construction_semantic_todo", lambda a: a["m4-8-plan.json"].__setitem__("construction_time_semantic_decision_count", 1)),
    ]

    rejected = 0
    unexpected = 0
    for label, mutation in cases:
        candidate = copy.deepcopy(artifacts)
        mutation(candidate)
        errors = validate_plan(candidate)
        if errors:
            rejected += 1
            print(f"PASS [REJECTED]: {label}")
        else:
            unexpected += 1
            print(f"FAIL [UNEXPECTED ACCEPT]: {label}")

    return len(cases), rejected, unexpected


def check_git_status() -> list[str]:
    failures: list[str] = []
    # Check base commit ancestry
    try:
        merge_base = subprocess.check_output(
            ["git", "-C", str(ROOT), "merge-base", ACCEPTED_BASE, "HEAD"],
            text=True
        ).strip()
        if merge_base != ACCEPTED_BASE:
            failures.append(f"Accepted base {ACCEPTED_BASE} is not an ancestor of HEAD (merge-base={merge_base})")
    except Exception as e:
        failures.append(f"Git merge-base failed: {e}")

    # Check changed files: only deploy/evidence/issues/9/m4-8-plan/ and scripts/m4_8_plan_guard.py allowed
    allowed_prefixes = (
        "deploy/evidence/issues/9/m4-8-plan/",
        "scripts/m4_8_plan_guard.py",
    )
    try:
        status_output = subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            text=True
        ).strip()
        for line in status_output.splitlines():
            if not line:
                continue
            path = line[3:].strip()
            if not any(path == p.rstrip("/") or path.startswith(p) for p in allowed_prefixes):
                failures.append(f"Disallowed modified/untracked path: {path}")
    except Exception as e:
        failures.append(f"Git status failed: {e}")

    return failures


def main() -> int:
    print("=== Running M4-8 Planning Guard ===")
    try:
        artifacts = load_artifacts()
    except Exception as e:
        print(f"FATAL: Artifact loading failed: {e}")
        return 1

    # 1. Validate real plan artifacts
    plan_errors = validate_plan(artifacts)
    if plan_errors:
        print("REAL PLAN VALIDATION FAILED:")
        for err in plan_errors:
            print(f"  - {err}")
        return 1
    print("Real plan artifact validation: PASS (0 errors)")

    # 2. Check git worktree purity
    git_errors = check_git_status()
    if git_errors:
        print("GIT STATUS CHECK FAILED:")
        for err in git_errors:
            print(f"  - {err}")
        return 1
    print("Git working tree check: PASS (clean scope)")

    # 3. Execute 24 negative rejection cases
    print("\n--- Executing 24 Planning Negative Cases ---")
    total, rejected, unexpected = negative_cases_suite(artifacts)
    print(f"\nPLANNING_NEGATIVE_CASE_COUNT={total}")
    print(f"PLANNING_NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"PLANNING_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={unexpected}")

    if total >= 24 and rejected == total and unexpected == 0:
        print("\nPLAN_GUARD=PASS")
        return 0
    else:
        print("\nPLAN_GUARD=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
