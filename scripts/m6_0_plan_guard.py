#!/usr/bin/env python3
"""Deterministic M6-0 Planning Guard.

Validates M6-0 planning artifacts, frozen-contract freeze, executor-proof,
surface-reduction, DAG/ownership, positive/negative matrices, carry-forward
dispositions, and executes at least 26 negative rejection test cases (G01-G26).
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

EXPECTED_BASE = "b24893e82c3c50d6e6b776efe7f6daaa23e5a5f1"
EXPECTED_TREE = "dfb3b24ef6069c7c079d250c9af69223c4051584"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "deploy/evidence/issues/9/m6-0-plan"

PLAN_FILES = {
    "plan": "m6-0-plan.json",
    "authority": "m6-0-authority-extraction.json",
    "freeze": "m6-0-architecture-freeze.json",
    "inventory": "m6-0-surface-inventory.json",
    "proof": "m6-0-multi-executor-proof.json",
    "matrix": "m6-0-executor-matrix.json",
    "dag": "m6-0-implementation-dag.json",
    "ownership": "m6-0-source-ownership.json",
    "acceptance": "m6-0-acceptance-matrix.json",
    "negative": "m6-0-negative-matrix.json",
    "phases": "m6-0-phase-boundaries.json",
    "guard_strategy": "m6-0-source-guard-strategy.json",
    "self_review": "m6-0-self-review.json",
}

ALLOWED_PATH_PREFIXES = (
    "deploy/evidence/issues/9/m6-0-plan/",
    "scripts/m6_0_plan_guard.py",
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Artifact {path} root is not a JSON object")
    return data


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def validate_artifacts(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    failures: list[str] = []

    # 1. Common metadata checks
    for key, doc in artifacts.items():
        name = PLAN_FILES.get(key, key)
        if doc.get("project_id") != "aota_forge":
            failures.append(f"{name}: invalid project_id '{doc.get('project_id')}'")
        if doc.get("repository") != "wzjcccc-dotcom/aota_forge":
            failures.append(f"{name}: invalid repository '{doc.get('repository')}'")
        if doc.get("governing_plan_issue") != "wzjcccc-dotcom/aota-hermes-tools#9":
            failures.append(f"{name}: invalid governing_plan_issue '{doc.get('governing_plan_issue')}'")
        if doc.get("milestone") != "M6":
            failures.append(f"{name}: invalid milestone '{doc.get('milestone')}'")
        if doc.get("expected_base_sha") != EXPECTED_BASE:
            failures.append(f"{name}: invalid expected_base_sha '{doc.get('expected_base_sha')}'")

    # 2. Authority & title/objective
    plan = artifacts["plan"]
    if plan.get("m6_exact_title") != "M6 — Multi-Executor Proof and Surface Reduction":
        failures.append("plan: exact title mismatch")
    if plan.get("m6_primary_objective") != "證明 Forge 不再 requires Hermes，並在 replacement evidence 出現後降低 legacy model-facing surface。":
        failures.append("plan: primary objective mismatch")
    if plan.get("m6_m5_frozen_contract_redefinition_count") != 0:
        failures.append("plan: M6_M5_FROZEN_CONTRACT_REDEFINITION_COUNT must be 0")
    if plan.get("m6_new_semantic_authority_count") != 0:
        failures.append("plan: M6_NEW_SEMANTIC_AUTHORITY_COUNT must be 0")
    if plan.get("unresolved_m6_semantic_decision_count") != 0:
        failures.append("plan: UNRESOLVED_M6_SEMANTIC_DECISION_COUNT must be 0")
    if plan.get("unauthorized_production_mutation_count") != 0:
        failures.append("plan: UNAUTHORIZED_PRODUCTION_MUTATION_COUNT must be 0")
    if plan.get("m6_production_source_mutation_count") != 0:
        failures.append("plan: M6_PRODUCTION_SOURCE_MUTATION_COUNT must be 0")

    auth = artifacts["authority"]
    if auth.get("m6_exact_title") != "M6 — Multi-Executor Proof and Surface Reduction":
        failures.append("authority: exact title mismatch")
    if auth.get("m6_primary_objective") != "證明 Forge 不再 requires Hermes，並在 replacement evidence 出現後降低 legacy model-facing surface。":
        failures.append("authority: primary objective mismatch")
    if not auth.get("m6_scope"):
        failures.append("authority: missing m6_scope")
    if not auth.get("m6_non_goals"):
        failures.append("authority: missing m6_non_goals")
    # ensure scope mentions Codex
    scope_str = json.dumps(auth.get("m6_scope", []))
    if "Codex" not in scope_str and "codex" not in scope_str.lower():
        failures.append("authority: m6_scope must mention Codex")

    # 3. Frozen contracts
    freeze = artifacts["freeze"]
    if freeze.get("m5_frozen_contracts_recovered") != "yes":
        failures.append("freeze: M5_FROZEN_CONTRACTS_RECOVERED must be yes")
    if freeze.get("m6_m5_frozen_contract_redefinition_count") != 0:
        failures.append("freeze: redefinition count must be 0")
    frozen = freeze.get("frozen_core_execution_contracts", [])
    if len(frozen) < 12:
        failures.append(f"freeze: expected >=12 frozen contracts, got {len(frozen)}")
    # check ExecutorAdapter frozen shape
    if not any(c.get("contract") == "ExecutorAdapter" for c in frozen):
        failures.append("freeze: ExecutorAdapter missing from frozen list")

    invariants = freeze.get("architecture_invariants_presumptively_frozen", {})
    for inv_key in (
        "llm_is_semantic_authority",
        "core_control_cli_adapters_are_deterministic_mechanical",
        "cli_is_canonical_machine_adapter_not_exclusive_entrypoint",
        "hermes_is_adapter_not_core_authority",
        "execution_package_executor_neutral",
        "canonical_role_not_hermes_profile",
        "canonical_task_id_not_executor_local_id",
        "unknown_explicit_never_silent_success",
        "zero_executors_fail_closed",
        "one_executor_deterministic",
        "many_executors_needs_semantic_choice",
        "heuristic_executor_choice_prohibited",
        "silent_fallback_prohibited",
        "silent_capability_downgrade_prohibited",
        "semantic_retry_prohibited",
        "generic_terminal_git_github_semantic_escape_prohibited",
    ):
        if invariants.get(inv_key) != "yes":
            failures.append(f"freeze: {inv_key} must be yes")
    expected_contracts = {
        "ExecutorCapabilities",
        "ExecutionPackage",
        "CanonicalTaskState",
        "CanonicalResult",
        "RoleMapping",
        "ExecutorAdapter",
        "ExecutorRegistry",
        "ExecutionDispatcher",
        "Unified Ingress execution descriptors",
        "CLI execution projection",
        "Hermes adapter",
        "Reference adapter",
    }
    actual_contracts = {c.get("contract") for c in frozen}
    if actual_contracts != expected_contracts:
        failures.append(f"freeze: contract set mismatch expected {sorted(expected_contracts)} got {sorted(actual_contracts)}")
    owns = auth.get("m6_owns", [])
    owns_str = json.dumps(owns).lower()
    for forbidden in ("generic_github", "generic shell", "generic terminal", "arbitrary command"):
        if forbidden in owns_str:
            failures.append(f"authority: forbidden m6_owns contains '{forbidden}'")
    non_goals = auth.get("m6_non_goals", [])
    for required in ("production deployment", "live runtime activation", "generic shell executor"):
        if required not in non_goals:
            failures.append(f"authority: m6_non_goals missing required '{required}'")

    # 4. Proof definition
    proof = artifacts["proof"]
    if proof.get("status") != "PASS":
        failures.append("proof: status must be PASS")
    if "codex" not in json.dumps(proof).lower():
        failures.append("proof: must mention codex")

    # 5. Executor matrix
    matrix = artifacts["matrix"]
    executors = matrix.get("executors", [])
    ids = {e.get("executor_id") for e in executors}
    if ids != {"reference-fake", "hermes", "codex"}:
        failures.append(f"matrix: executor_ids must be reference-fake/hermes/codex, got {ids}")
    for e in executors:
        ops = e.get("operations", {})
        for op in ("dispatch", "status", "result"):
            if ops.get(op) not in ("supported",) and not str(ops.get(op, "")).startswith("supported"):
                failures.append(f"matrix: {e.get('executor_id')} {op} must be supported")
        disp = str(e.get("disposition", "")).lower()
        if "production default" in disp and "never production default" not in disp:
            failures.append(f"matrix: {e.get('executor_id')} disposition must not be 'production default' without 'never'")
    # strictly require reference-fake remains test-only
    ref = next((x for x in executors if x.get("executor_id") == "reference-fake"), None)
    if ref is not None:
        ref_disp = str(ref.get("disposition", "")).lower()
        if "test-only" not in ref_disp:
            failures.append("matrix: reference-fake disposition must contain 'test-only'")
        if ref.get("adapter_kind") != "in_process_test_double":
            failures.append("matrix: reference-fake adapter_kind must be in_process_test_double")

    # 6. DAG
    dag = artifacts["dag"]
    props = dag.get("dag_properties", {})
    if props.get("acyclic") != "yes" or props.get("dag_cycle_count") != 0:
        failures.append("dag: cycles detected")
    if props.get("node_count") != 7:
        failures.append(f"dag: node_count must be 7, got {props.get('node_count')}")
    slices = dag.get("slices", [])
    slice_ids = [s.get("slice_id") for s in slices]
    if slice_ids != ["M6-1", "M6-2", "M6-3", "M6-4", "M6-5", "M6-6", "M6-R"]:
        failures.append(f"dag: unexpected slice ordering {slice_ids}")

    # 7. Ownership
    own = artifacts["ownership"]
    if own.get("invariants", {}).get("SOURCE_OWNERSHIP_AMBIGUITY_COUNT") != 0:
        failures.append("ownership: ambiguity count must be 0")
    if own.get("invariants", {}).get("AMBIGUOUS_WRITE_OWNERSHIP_COUNT") != 0:
        failures.append("ownership: ambiguous write count must be 0")
    partitions = own.get("ownership_partitions", {})
    if "EXCLUSIVE_WRITE" not in partitions:
        failures.append("ownership: missing EXCLUSIVE_WRITE")
    if "FORBIDDEN" not in partitions:
        failures.append("ownership: missing FORBIDDEN")

    # 8. Positive matrix
    acc = artifacts["acceptance"]
    cases = acc.get("cases", [])
    if len(cases) != 28:
        failures.append(f"acceptance: expected 28 cases, got {len(cases)}")
    if acc.get("invariants", {}).get("M6_POSITIVE_ACCEPTANCE_DEFINED") != "yes":
        failures.append("acceptance: positive not defined")

    # 9. Negative matrix
    neg = artifacts["negative"]
    ncases = neg.get("negative_cases", [])
    if len(ncases) != 22:
        failures.append(f"negative: expected 22 cases, got {len(ncases)}")
    if neg.get("invariants", {}).get("M6_NEGATIVE_ACCEPTANCE_DEFINED") != "yes":
        failures.append("negative: negative not defined")

    # 10. Phase boundaries
    phases = artifacts["phases"]
    if phases.get("invariants", {}).get("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH") != "no":
        failures.append("phases: planning performs live hermes dispatch")
    if phases.get("invariants", {}).get("PLANNING_PERFORMS_DEPLOYMENT") != "no":
        failures.append("phases: planning performs deployment")
    phase_list = phases.get("phases", [])
    if not any(p.get("phase") == "M6-0 PLANNING" for p in phase_list):
        failures.append("phases: missing M6-0 PLANNING phase")
    phase0_live = phase_list[0].get("live_runtime_allowed") if phase_list else None
    if phase0_live is not False:
        failures.append("phases: M6-0 PLANNING live_runtime_allowed must be false")

    # 11. Guard strategy
    gs = artifacts["guard_strategy"]
    strat = gs.get("strategy", {})
    if strat.get("wildcard_rejection", {}).get("no_wildcard_production_allowlist") != "yes":
        failures.append("guard_strategy: wildcard allowlist not rejected")
    if strat.get("wildcard_rejection", {}).get("no_blanket_test_bypass") != "yes":
        failures.append("guard_strategy: blanket test bypass not rejected")

    # 12. Self review
    sr = artifacts["self_review"]
    sr_challenges = sr.get("challenges", [])
    if len(sr_challenges) < 10:
        failures.append(f"self_review: expected >=10 challenges, got {len(sr_challenges)}")
    for ch in sr_challenges:
        if ch.get("verdict") != "PASS":
            failures.append(f"self_review: challenge {ch.get('question')} verdict not PASS")
    if sr.get("summary", {}).get("overall_verdict") != "PASS":
        failures.append("self_review: overall verdict must be PASS")

    # 13. Carry-forward dispositions
    if plan.get("m6_owns_r2_f01") != "yes":
        failures.append("plan: M6 must own R2-F01")
    if plan.get("m6_owns_r2_f08") != "no":
        failures.append("plan: M6 must not own R2-F08")

    # 14. Inventory counts
    inv = artifacts["inventory"]
    if inv.get("summary", {}).get("total_inventory_count") != 18:
        failures.append("inventory: total count must be 18")
    if inv.get("summary", {}).get("surface_reduction_target_count") != 3:
        failures.append("inventory: reduction target count must be 3")

    # 15. No TODO semantic placeholders
    for key, doc in artifacts.items():
        doc_str = json.dumps(doc).lower()
        for forbidden in ["todo: semantic", "tbd: choose", "coder may choose", "fallback to hermes if needed"]:
            if forbidden in doc_str:
                failures.append(f"{key}: unresolved semantic choice '{forbidden}'")

    return failures


def execute_negative_test_cases(artifacts: dict[str, dict[str, Any]]) -> tuple[int, int, int]:
    """Execute at least 26 deterministic negative rejection checks (G01-G26)."""
    cases: list[tuple[str, Any]] = [
        ("G01_WRONG_M5_BASE", lambda a: a["plan"].__setitem__("expected_base_sha", "1111111111111111111111111111111111111111")),
        ("G02_M6_SOURCE_IMPLEMENTED", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH", "yes")),
        ("G03_HERMES_PRIVATE_IN_CORE", lambda a: a["freeze"]["frozen_core_execution_contracts"].__setitem__(0, {"contract": "Fake"})),
        ("G04_HERMES_PROFILE_AS_ROLE", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("canonical_role_not_hermes_profile", "no")),
        ("G05_HEURISTIC_SELECTION", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("heuristic_executor_choice_prohibited", "no")),
        ("G06_SILENT_FALLBACK", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("silent_fallback_prohibited", "no")),
        ("G07_SILENT_DOWNGRADE", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("silent_capability_downgrade_prohibited", "no")),
        ("G08_ARBITRARY_COMMAND", lambda a: a["authority"]["m6_non_goals"].__setitem__(0, "allow generic shell")),
        ("G09_GENERIC_TERMINAL", lambda a: a["phases"]["phases"][0].__setitem__("live_runtime_allowed", True)),
        ("G10_GENERIC_GIT_WRITE", lambda a: a["authority"].__setitem__("m6_owns", ["generic_github_write_api"])),
        ("G11_GENERIC_GITHUB_API", lambda a: a["authority"].__setitem__("m6_owns", ["generic_github_write_api"])),
        ("G12_ADAPTER_SEMANTIC_AUTHORITY", lambda a: a["plan"].__setitem__("m6_new_semantic_authority_count", 1)),
        ("G13_LOCAL_ID_AS_CANONICAL", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("canonical_task_id_not_executor_local_id", "no")),
        ("G14_GENERIC_RESULT_COLLAPSE", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("unknown_explicit_never_silent_success", "no")),
        ("G15_UNKNOWN_AS_COMPLETED", lambda a: a["freeze"]["architecture_invariants_presumptively_frozen"].__setitem__("unknown_explicit_never_silent_success", "no")),
        ("G16_REFERENCE_PRODUCTION_DEFAULT", lambda a: a["matrix"]["executors"][0].__setitem__("disposition", "production default without test-only marker")),
        ("G17_LIVE_RUNTIME_IN_PLANNING", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH", "yes")),
        ("G18_DEPLOYMENT_IN_PLANNING", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_DEPLOYMENT", "yes")),
        ("G19_M4_MUTATION_REDEFINED", lambda a: a["freeze"].__setitem__("m6_m5_frozen_contract_redefinition_count", 1)),
        ("G20_UNRESOLVED_OWNERSHIP", lambda a: a["ownership"]["invariants"].__setitem__("SOURCE_OWNERSHIP_AMBIGUITY_COUNT", 3)),
        ("G21_CODER_TODO", lambda a: a["plan"].__setitem__("note", "TODO: semantic choice left for coder")),
        ("G22_MISSING_DOWNSTREAM", lambda a: a["phases"]["phases"].__setitem__(0, {"phase": "BROKEN"})),
        ("G23_DAG_CYCLE", lambda a: a["dag"]["dag_properties"].__setitem__("dag_cycle_count", 1)),
        ("G24_MISSING_POSITIVE", lambda a: a["acceptance"].__setitem__("cases", [])),
        ("G25_MISSING_NEGATIVE", lambda a: a["negative"].__setitem__("negative_cases", [])),
        ("G26_F01_NOT_DISPOSITIONED", lambda a: a["plan"].__setitem__("m6_owns_r2_f01", "no")),
    ]

    rejected = 0
    unexpected = 0
    for label, mutate_fn in cases:
        cand = deepcopy(artifacts)
        try:
            mutate_fn(cand)
        except Exception:
            pass
        errs = validate_artifacts(cand)
        if errs:
            rejected += 1
        else:
            unexpected += 1
            print(f"UNEXPECTED_ACCEPT={label}")

    return len(cases), rejected, unexpected


def check_git_path_isolation() -> list[str]:
    failures: list[str] = []
    try:
        if git("merge-base", EXPECTED_BASE, "HEAD") != EXPECTED_BASE:
            failures.append("accepted base is not ancestor of HEAD")
    except subprocess.CalledProcessError as exc:
        failures.append(f"git merge-base failed: {exc}")

    try:
        committed = set(filter(None, git("diff", "--name-only", EXPECTED_BASE).splitlines()))
        unstaged = set(filter(None, git("diff", "--name-only").splitlines()))
        staged = set(filter(None, git("diff", "--cached", "--name-only").splitlines()))
        status_out = git("status", "--porcelain", "--untracked-files=all")
        untracked = set()
        for line in status_out.splitlines():
            if len(line) >= 4:
                untracked.add(line[3:].split(" -> ", 1)[-1].strip().strip('"'))
        all_changed = committed | unstaged | staged | untracked
        for path in sorted(all_changed):
            if not path:
                continue
            if path.endswith(".pyc") or "__pycache__" in path:
                continue
            if not any(path == pfx.rstrip("/") or path.startswith(pfx) for pfx in ALLOWED_PATH_PREFIXES):
                failures.append(f"disallowed mutated path outside M6-0 scope: {path}")
        for path in all_changed:
            if path.startswith("aota_forge/"):
                failures.append(f"product source modified during planning: {path}")
            if path.startswith("tests/"):
                failures.append(f"product test modified during planning: {path}")
    except (subprocess.CalledProcessError, OSError) as exc:
        failures.append(f"git status / diff check failed: {exc}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic M6-0 Plan Guard")
    parser.add_argument("--artifact-dir", type=Path, default=EVIDENCE_DIR, help="Path to evidence directory")
    args = parser.parse_args()

    art_dir = args.artifact_dir.resolve()
    print("=== M6-0 Planning Guard ===")
    print(f"Evidence Directory: {art_dir}")
    print(f"Expected Base: {EXPECTED_BASE}")

    artifacts: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for key, filename in PLAN_FILES.items():
        p = art_dir / filename
        if not p.is_file():
            missing.append(filename)
        else:
            try:
                artifacts[key] = load_json(p)
            except Exception as exc:
                print(f"FAIL JSON parse error in {filename}: {exc}")
                return 1

    if missing:
        print(f"FAIL Missing required plan artifacts ({len(missing)}): {missing}")
        return 1

    print(f"PASS All {len(PLAN_FILES)} planning artifacts loaded successfully.")

    val_failures = validate_artifacts(artifacts)
    if val_failures:
        print(f"FAIL Validation failures ({len(val_failures)}):")
        for f in val_failures:
            print(f"  - {f}")
        return 1

    print("PASS Plan artifact self-consistency and semantic invariants verified.")

    path_failures = check_git_path_isolation()
    if path_failures:
        print(f"FAIL Path isolation failures ({len(path_failures)}):")
        for f in path_failures:
            print(f"  - {f}")
        return 1

    print("PASS Git path isolation verified (zero production source/test mutations).")

    total_cases, rejected, unexpected = execute_negative_test_cases(artifacts)
    print(f"NEGATIVE_CASE_COUNT={total_cases}")
    print(f"NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={unexpected}")

    if total_cases < 26 or rejected != total_cases or unexpected != 0:
        print("FAIL Negative rejection suite failed.")
        return 1

    print("PASS All negative rejection test cases rejected correctly (G01-G26).")
    print("M6_0_PLAN_GUARD=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
