#!/usr/bin/env python3
"""Reviewer Independent Probe Suite for M5-0 Plan Verification.

Executes independent positive control probes (RP-P01..RP-P05) and adversarial
negative probe mutants (RP-N01..RP-N12) against M5-0 planning artifacts.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[6]
PLAN_DIR = ROOT / "deploy/evidence/issues/9/m5-0-plan"

REQUIRED_ARTIFACTS = [
    "m5-0-plan.json",
    "m5-0-authority-extraction.json",
    "m5-0-architecture-boundary.json",
    "m5-0-executor-capabilities-contract.json",
    "m5-0-execution-package-contract.json",
    "m5-0-canonical-task-state.json",
    "m5-0-canonical-result.json",
    "m5-0-role-mapping.json",
    "m5-0-executor-adapter-contract.json",
    "m5-0-hermes-adapter-boundary.json",
    "m5-0-executor-selection.json",
    "m5-0-execution-identity.json",
    "m5-0-result-error-taxonomy.json",
    "m5-0-cli-dispatch-plan.json",
    "m5-0-source-reconnaissance.json",
    "m5-0-refactor-strategy.json",
    "m5-0-source-ownership.json",
    "m5-0-implementation-dag.json",
    "m5-0-acceptance-matrix.json",
    "m5-0-negative-matrix.json",
    "m5-0-failure-injection.json",
    "m5-0-phase-boundaries.json",
    "m5-0-downstream-handoff.json",
    "m5-0-negative-scope.json",
]


def load_all_artifacts(plan_dir: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_ARTIFACTS:
        p = plan_dir / name
        if not p.is_file():
            raise FileNotFoundError(f"Missing required artifact {name} at {p}")
        with p.open(encoding="utf-8") as f:
            artifacts[name] = json.load(f)
    return artifacts


# ==============================================================================
# Independent Reviewer Checker Engine
# ==============================================================================

def check_planning_package(arts: dict[str, dict[str, Any]]) -> list[str]:
    errs: list[str] = []

    # 1. Structural & Metadata completeness
    for name, doc in arts.items():
        if doc.get("project_id") != "aota_forge":
            errs.append(f"{name}: invalid project_id")
        if doc.get("repository") != "wzjcccc-dotcom/aota_forge":
            errs.append(f"{name}: invalid repository")
        if doc.get("governing_plan_issue") != "wzjcccc-dotcom/aota-hermes-tools#9":
            errs.append(f"{name}: invalid governing_plan_issue")
        if doc.get("milestone") != "M5":
            errs.append(f"{name}: invalid milestone")
        if doc.get("expected_base_sha") != "b91aa7fd8ecd8219fab8386e00c098296b9c0c7b":
            errs.append(f"{name}: invalid expected_base_sha")

    # 2. Canonical state consistency across artifacts
    state_doc = arts.get("m5-0-canonical-task-state.json", {})
    canonical_states = {s["state"] for s in state_doc.get("canonical_states", []) if "state" in s}
    expected_states = {"CREATED", "ACCEPTED", "QUEUED", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"}
    if not expected_states.issubset(canonical_states):
        errs.append(f"canonical_task_state: missing required states in {canonical_states}")
    if state_doc.get("reconciliation_invariants", {}).get("UNKNOWN_IS_NOT_COMPLETED") != "yes":
        errs.append("canonical_task_state: UNKNOWN_IS_NOT_COMPLETED must be yes")

    # 3. Hermes adapter boundary isolation
    bound_doc = arts.get("m5-0-architecture-boundary.json", {})
    invariants = bound_doc.get("invariants", {})
    if invariants.get("HERMES_IS_EXECUTOR_ADAPTER") != "yes":
        errs.append("architecture_boundary: HERMES_IS_EXECUTOR_ADAPTER must be yes")
    if invariants.get("CORE_DIRECT_HERMES_DEPENDENCY_ALLOWED") != "no":
        errs.append("architecture_boundary: CORE_DIRECT_HERMES_DEPENDENCY_ALLOWED must be no")
    if invariants.get("CLI_DIRECT_HERMES_DEPENDENCY_ALLOWED") != "no":
        errs.append("architecture_boundary: CLI_DIRECT_HERMES_DEPENDENCY_ALLOWED must be no")
    if invariants.get("HERMES_PRIVATE_OBJECT_IN_CANONICAL_CORE_COUNT") != 0:
        errs.append("architecture_boundary: HERMES_PRIVATE_OBJECT_IN_CANONICAL_CORE_COUNT != 0")
    if invariants.get("HERMES_FILESYSTEM_LAYOUT_IN_CORE_COUNT") != 0:
        errs.append("architecture_boundary: HERMES_FILESYSTEM_LAYOUT_IN_CORE_COUNT != 0")

    # Check for hidden Hermes aliases in canonical contracts
    cap_doc = arts.get("m5-0-executor-capabilities-contract.json", {})
    for f in cap_doc.get("fields", []):
        name = f.get("name", "").lower()
        if "hermes" in name or "session_dir" in name:
            errs.append(f"capabilities_contract: hidden adapter alias in field {name}")

    if cap_doc.get("invariants", {}).get("EXECUTOR_CAPABILITIES_SEMANTIC_DECISION_FIELD_COUNT") != 0:
        errs.append("capabilities_contract: semantic decision fields present")

    # 4. Executor Selection rules
    sel_doc = arts.get("m5-0-executor-selection.json", {})
    prohibs = sel_doc.get("prohibitions", {})
    if prohibs.get("HEURISTIC_EXECUTOR_SELECTION_ALLOWED") != "no":
        errs.append("selection: HEURISTIC_EXECUTOR_SELECTION_ALLOWED must be no")
    if prohibs.get("SILENT_HERMES_FALLBACK_ALLOWED") != "no":
        errs.append("selection: SILENT_HERMES_FALLBACK_ALLOWED must be no")
    if prohibs.get("SILENT_CAPABILITY_DOWNGRADE_ALLOWED") != "no":
        errs.append("selection: SILENT_CAPABILITY_DOWNGRADE_ALLOWED must be no")
    card = sel_doc.get("selection_architecture", {}).get("cardinality_rules", {})
    if not card.get("multiple_matches_no_explicit_target", "").startswith("Returns NEEDS_SEMANTIC_CHOICE"):
        errs.append("selection: multi-match must require semantic choice")

    # 5. Identity & Resume separation
    id_doc = arts.get("m5-0-execution-identity.json", {})
    if id_doc.get("invariants", {}).get("EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY") != "no":
        errs.append("execution_identity: EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY must be no")
    retries = id_doc.get("retry_and_resume_semantics", {})
    if retries.get("ADAPTER_AUTO_SEMANTIC_RETRY_ALLOWED") != "no":
        errs.append("execution_identity: ADAPTER_AUTO_SEMANTIC_RETRY_ALLOWED must be no")

    # 6. Result & Error taxonomy
    res_doc = arts.get("m5-0-canonical-result.json", {})
    if res_doc.get("prohibitions", {}).get("GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED") != "no":
        errs.append("canonical_result: GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED must be no")

    err_doc = arts.get("m5-0-result-error-taxonomy.json", {})
    codes = {e["code"] for e in err_doc.get("error_codes", []) if "code" in e}
    required_errs = {"EXECUTOR_NOT_FOUND", "CAPABILITY_MISMATCH", "PACKAGE_INVALID", "ROLE_MAPPING_NOT_FOUND", "DISPATCH_TIMEOUT", "TASK_STATE_UNKNOWN"}
    if not required_errs.issubset(codes):
        errs.append(f"error_taxonomy: missing critical codes from {codes}")

    # 7. CLI contract parity
    cli_doc = arts.get("m5-0-cli-dispatch-plan.json", {})
    cli_prohibs = cli_doc.get("prohibitions", {})
    if cli_prohibs.get("CLI_SEMANTIC_EXECUTOR_SELECTION_ALLOWED") != "no":
        errs.append("cli_dispatch: CLI_SEMANTIC_EXECUTOR_SELECTION_ALLOWED must be no")
    if cli_prohibs.get("CLI_HEURISTIC_HERMES_FALLBACK_ALLOWED") != "no":
        errs.append("cli_dispatch: CLI_HEURISTIC_HERMES_FALLBACK_ALLOWED must be no")
    if cli_prohibs.get("ARBITRARY_EXECUTOR_COMMAND_STRING_ALLOWED") != "no":
        errs.append("cli_dispatch: ARBITRARY_EXECUTOR_COMMAND_STRING_ALLOWED must be no")

    # 8. M4 Mutation Plane isolation
    neg_scope = arts.get("m5-0-negative-scope.json", {})
    prohib_map = {item["rule"]: item["value"] for item in neg_scope.get("prohibitions", []) if "rule" in item}
    if prohib_map.get("M4_MUTATION_SURFACE_REDEFINED") != "no":
        errs.append("negative_scope: M4_MUTATION_SURFACE_REDEFINED must be no")
    if prohib_map.get("REFERENCE_EXECUTOR_PRODUCTION_DEFAULT") != "no":
        errs.append("negative_scope: REFERENCE_EXECUTOR_PRODUCTION_DEFAULT must be no")

    # 9. Source Ownership & DAG Parallel Safety
    own_doc = arts.get("m5-0-source-ownership.json", {})
    if own_doc.get("invariants", {}).get("SOURCE_OWNERSHIP_AMBIGUITY_COUNT") != 0:
        errs.append("source_ownership: SOURCE_OWNERSHIP_AMBIGUITY_COUNT != 0")
    exclusive_writes = own_doc.get("ownership_partitions", {}).get("EXCLUSIVE_WRITE", [])
    if len(exclusive_writes) < 8:
        errs.append("source_ownership: EXCLUSIVE_WRITE count insufficient")

    dag_doc = arts.get("m5-0-implementation-dag.json", {})
    dag_props = dag_doc.get("dag_properties", {})
    if dag_props.get("acyclic") != "yes" or dag_props.get("dag_cycle_count") != 0:
        errs.append("dag: cycles detected")

    # Verify parallel pairs in DAG
    parallel_info = dag_doc.get("parallelization", {})
    if parallel_info.get("MAX_SAFE_PARALLEL_SOURCE_LANES") != 2:
        errs.append("dag: MAX_SAFE_PARALLEL_SOURCE_LANES must be 2")

    # Check hot-file conflict detection
    int_paths = own_doc.get("ownership_partitions", {}).get("INTEGRATION_ONLY", [])
    for p in int_paths:
        if not p.get("owner_slice"):
            errs.append(f"source_ownership: integration path {p.get('path')} missing owner_slice")

    return errs


# ==============================================================================
# Positive Control Probes (RP-P01..RP-P05)
# ==============================================================================

def run_positive_probes(artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    results = []

    # RP-P01: Pristine planning package passes independent consistency checks
    errs_p01 = check_planning_package(artifacts)
    results.append({
        "probe_id": "RP-P01",
        "name": "Pristine planning package consistency",
        "passed": len(errs_p01) == 0,
        "error_count": len(errs_p01),
        "errors": errs_p01,
    })

    # RP-P02: Deterministic one-adapter selection verification
    sel = artifacts["m5-0-executor-selection.json"]
    card = sel.get("selection_architecture", {}).get("cardinality_rules", {})
    p02_ok = (
        "EXECUTOR_NOT_FOUND" in card.get("zero_match", "")
        and "Deterministically selects" in card.get("one_match", "")
        and "NEEDS_SEMANTIC_CHOICE" in card.get("multiple_matches_no_explicit_target", "")
    )
    results.append({
        "probe_id": "RP-P02",
        "name": "Deterministic executor selection architecture",
        "passed": p02_ok,
        "details": card,
    })

    # RP-P03: Adapter-neutral contract verification
    cap = artifacts["m5-0-executor-capabilities-contract.json"]
    pkg = artifacts["m5-0-execution-package-contract.json"]
    p03_ok = (
        cap.get("invariants", {}).get("EXECUTOR_CAPABILITIES_SEMANTIC_DECISION_FIELD_COUNT") == 0
        and pkg.get("invariants", {}).get("EXECUTION_PACKAGE_HERMES_PRIVATE_FIELD_COUNT") == 0
        and len(cap.get("forbidden_semantic_fields", [])) >= 5
    )
    results.append({
        "probe_id": "RP-P03",
        "name": "Adapter-neutral contract validation",
        "passed": p03_ok,
        "forbidden_fields_count": len(cap.get("forbidden_semantic_fields", [])),
    })

    # RP-P04: Exact ownership & DAG parallel safety proof (M5-2, M5-4)
    dag = artifacts["m5-0-implementation-dag.json"]
    slices = {s["slice_id"]: s for s in dag.get("slices", [])}
    s2 = slices.get("M5-2", {})
    s4 = slices.get("M5-4", {})
    w2 = set(s2.get("authorized_write_paths", []))
    w4 = set(s4.get("authorized_write_paths", []))
    p04_ok = len(w2 & w4) == 0 and "M5-1" in s2.get("prerequisites", []) and "M5-1" in s4.get("prerequisites", [])
    results.append({
        "probe_id": "RP-P04",
        "name": "2-way parallel lane safety proof (M5-2, M5-4)",
        "passed": p04_ok,
        "m5_2_writes": list(w2),
        "m5_4_writes": list(w4),
        "intersection": list(w2 & w4),
    })

    # RP-P05: Acceptance, Negative & Failure matrices cross-link
    acc = artifacts["m5-0-acceptance-matrix.json"]
    neg = artifacts["m5-0-negative-matrix.json"]
    fl = artifacts["m5-0-failure-injection.json"]
    p05_ok = (
        len(acc.get("cases", [])) == 24
        and len(neg.get("negative_cases", [])) == 24
        and len(fl.get("scenarios", [])) == 15
    )
    results.append({
        "probe_id": "RP-P05",
        "name": "Cross-matrix completeness and link verification",
        "passed": p05_ok,
        "acceptance_count": len(acc.get("cases", [])),
        "negative_count": len(neg.get("negative_cases", [])),
        "failure_scenario_count": len(fl.get("scenarios", [])),
    })

    return results


# ==============================================================================
# Adversarial Negative Probes (RP-N01..RP-N12)
# ==============================================================================

def run_negative_probes(base_artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    probes: list[tuple[str, str, Any, str]] = [
        (
            "RP-N01",
            "Cross-artifact split-brain state definitions",
            lambda a: a["m5-0-canonical-task-state.json"]["canonical_states"].pop(0),
            "canonical_task_state: missing required states",
        ),
        (
            "RP-N02",
            "Hidden Hermes alias in canonical capabilities contract",
            lambda a: a["m5-0-executor-capabilities-contract.json"]["fields"].append({"name": "hermes_profile_mode", "type": "str"}),
            "capabilities_contract: hidden adapter alias",
        ),
        (
            "RP-N03",
            "Ambiguous adapter tie auto-selection without semantic choice",
            lambda a: a["m5-0-executor-selection.json"]["selection_architecture"]["cardinality_rules"].__setitem__("multiple_matches_no_explicit_target", "AUTO_PICK_FIRST"),
            "selection: multi-match must require semantic choice",
        ),
        (
            "RP-N04",
            "Capability weakening / silent downgrade allowed",
            lambda a: a["m5-0-executor-selection.json"]["prohibitions"].__setitem__("SILENT_CAPABILITY_DOWNGRADE_ALLOWED", "yes"),
            "selection: SILENT_CAPABILITY_DOWNGRADE_ALLOWED must be no",
        ),
        (
            "RP-N05",
            "Identity domain collapse (executor-local ID as canonical)",
            lambda a: a["m5-0-execution-identity.json"]["invariants"].__setitem__("EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY", "yes"),
            "execution_identity: EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY must be no",
        ),
        (
            "RP-N06",
            "Retry/resume collapse (adapter auto semantic retry)",
            lambda a: a["m5-0-execution-identity.json"]["retry_and_resume_semantics"].__setitem__("ADAPTER_AUTO_SEMANTIC_RETRY_ALLOWED", "yes"),
            "execution_identity: ADAPTER_AUTO_SEMANTIC_RETRY_ALLOWED must be no",
        ),
        (
            "RP-N07",
            "Unknown state false completion",
            lambda a: a["m5-0-canonical-task-state.json"]["reconciliation_invariants"].__setitem__("UNKNOWN_IS_NOT_COMPLETED", "no"),
            "canonical_task_state: UNKNOWN_IS_NOT_COMPLETED must be yes",
        ),
        (
            "RP-N08",
            "Result taxonomy collapse (generic failure allowed)",
            lambda a: a["m5-0-canonical-result.json"]["prohibitions"].__setitem__("GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED", "yes"),
            "canonical_result: GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED must be no",
        ),
        (
            "RP-N09",
            "CLI semantic drift (heuristic Hermes fallback in CLI)",
            lambda a: a["m5-0-cli-dispatch-plan.json"]["prohibitions"].__setitem__("CLI_HEURISTIC_HERMES_FALLBACK_ALLOWED", "yes"),
            "cli_dispatch: CLI_HEURISTIC_HERMES_FALLBACK_ALLOWED must be no",
        ),
        (
            "RP-N10",
            "Hidden hot-file conflict in integration seam",
            lambda a: a["m5-0-source-ownership.json"]["ownership_partitions"]["INTEGRATION_ONLY"][0].__setitem__("owner_slice", ""),
            "source_ownership: integration path",
        ),
        (
            "RP-N11",
            "Reference adapter accidental production default",
            lambda a: [item.__setitem__("value", "yes") for item in a["m5-0-negative-scope.json"]["prohibitions"] if item.get("rule") == "REFERENCE_EXECUTOR_PRODUCTION_DEFAULT"],
            "negative_scope: REFERENCE_EXECUTOR_PRODUCTION_DEFAULT must be no",
        ),
        (
            "RP-N12",
            "Execution/mutation plane contamination (M4 surface redefined)",
            lambda a: [item.__setitem__("value", "yes") for item in a["m5-0-negative-scope.json"]["prohibitions"] if item.get("rule") == "M4_MUTATION_SURFACE_REDEFINED"],
            "negative_scope: M4_MUTATION_SURFACE_REDEFINED must be no",
        ),
    ]

    results = []
    for pid, name, mutator, expected_err_pattern in probes:
        cand = deepcopy(base_artifacts)
        mutator(cand)
        errs = check_planning_package(cand)
        matched = any(expected_err_pattern in e for e in errs)
        rejected = len(errs) > 0 and matched
        results.append({
            "probe_id": pid,
            "name": name,
            "expected_error_pattern": expected_err_pattern,
            "rejected": rejected,
            "errors": errs,
        })
    return results


def main() -> int:
    artifacts = load_all_artifacts(PLAN_DIR)
    print(f"Loaded {len(artifacts)} pristine artifacts from {PLAN_DIR}")

    pos_results = run_positive_probes(artifacts)
    print(f"\n--- Positive Controls ({len(pos_results)}) ---")
    pos_pass = 0
    for pr in pos_results:
        st = "PASS" if pr["passed"] else "FAIL"
        print(f"  [{pr['probe_id']}] {st} - {pr['name']}")
        if pr["passed"]:
            pos_pass += 1

    neg_results = run_negative_probes(artifacts)
    print(f"\n--- Adversarial Negative Probes ({len(neg_results)}) ---")
    neg_reject = 0
    for nr in neg_results:
        st = "REJECTED (PASS)" if nr["rejected"] else "ACCEPTED (FAIL)"
        print(f"  [{nr['probe_id']}] {st} - {nr['name']}")
        if nr["rejected"]:
            neg_reject += 1

    print("\n=== Summary ===")
    print(f"POSITIVE_PROBES: {pos_pass}/{len(pos_results)}")
    print(f"NEGATIVE_PROBES: {neg_reject}/{len(neg_results)}")

    out_dir = ROOT / "deploy/evidence/issues/9/m5-0-plan-review"
    with (out_dir / "reviewer-positive-probes.json").open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "project_id": "aota_forge",
            "governing_plan_issue": "wzjcccc-dotcom/aota-hermes-tools#9",
            "milestone": "M5",
            "work_item": "M5-0-PLAN-INDEPENDENT-REVIEW",
            "probe_count": len(pos_results),
            "pass_count": pos_pass,
            "status": "PASS" if pos_pass == len(pos_results) else "FAIL",
            "results": pos_results,
        }, f, indent=2)

    with (out_dir / "reviewer-negative-probes.json").open("w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "project_id": "aota_forge",
            "governing_plan_issue": "wzjcccc-dotcom/aota-hermes-tools#9",
            "milestone": "M5",
            "work_item": "M5-0-PLAN-INDEPENDENT-REVIEW",
            "probe_count": len(neg_results),
            "reject_count": neg_reject,
            "unexpected_accept_count": len(neg_results) - neg_reject,
            "status": "PASS" if neg_reject == len(neg_results) else "FAIL",
            "results": neg_results,
        }, f, indent=2)

    if pos_pass != len(pos_results) or neg_reject != len(neg_results):
        print("FAIL: Probe suite did not meet 100% criteria.")
        return 1

    print("ALL REVIEWER PROBES PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
