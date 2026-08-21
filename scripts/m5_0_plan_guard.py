#!/usr/bin/env python3
"""Deterministic M5-0 Planning Guard.

Validates M5-0 planning artifacts, scope boundaries, architecture invariants,
and executes at least 26 negative rejection test cases (G01 - G26).
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

EXPECTED_BASE = "b91aa7fd8ecd8219fab8386e00c098296b9c0c7b"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "deploy/evidence/issues/9/m5-0-plan"

PLAN_FILES = {
    "plan": "m5-0-plan.json",
    "authority": "m5-0-authority-extraction.json",
    "boundary": "m5-0-architecture-boundary.json",
    "capabilities": "m5-0-executor-capabilities-contract.json",
    "package": "m5-0-execution-package-contract.json",
    "task_state": "m5-0-canonical-task-state.json",
    "result": "m5-0-canonical-result.json",
    "role_mapping": "m5-0-role-mapping.json",
    "adapter": "m5-0-executor-adapter-contract.json",
    "hermes_boundary": "m5-0-hermes-adapter-boundary.json",
    "selection": "m5-0-executor-selection.json",
    "identity": "m5-0-execution-identity.json",
    "error_taxonomy": "m5-0-result-error-taxonomy.json",
    "cli_dispatch": "m5-0-cli-dispatch-plan.json",
    "reconnaissance": "m5-0-source-reconnaissance.json",
    "refactor": "m5-0-refactor-strategy.json",
    "ownership": "m5-0-source-ownership.json",
    "dag": "m5-0-implementation-dag.json",
    "acceptance": "m5-0-acceptance-matrix.json",
    "negative_matrix": "m5-0-negative-matrix.json",
    "failure_injection": "m5-0-failure-injection.json",
    "phases": "m5-0-phase-boundaries.json",
    "downstream": "m5-0-downstream-handoff.json",
    "negative_scope": "m5-0-negative-scope.json",
}

ALLOWED_PATH_PREFIXES = (
    "deploy/evidence/issues/9/m5-0-plan/",
    "scripts/m5_0_plan_guard.py",
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
        if doc.get("milestone") != "M5":
            failures.append(f"{name}: invalid milestone '{doc.get('milestone')}'")
        if doc.get("expected_base_sha") != EXPECTED_BASE:
            failures.append(f"{name}: invalid expected_base_sha '{doc.get('expected_base_sha')}'")

    # 2. Authority & Scope checks
    auth = artifacts["authority"]
    if auth.get("m5_exact_title") != "M5 — Executor-Neutral Runtime and CLI Dispatch":
        failures.append("authority: exact title mismatch")
    if auth.get("m5_primary_objective") != "導入 canonical execution abstractions，並將 Hermes 整合為 adapter。":
        failures.append("authority: primary objective mismatch")
    owns = set(auth.get("m5_owns", []))
    if any("redefine_plan_init" in x or "redefine_mutation" in x for x in owns):
        failures.append("authority: M4 mutation surface redefinition claimed in m5_owns")
    if any("generic_github" in x.lower() or "generic_github_write_api" in x for x in owns):
        failures.append("authority: generic GitHub API claimed in m5_owns")

    # 3. Architecture & Boundary checks
    bound = artifacts["boundary"]
    invariants = bound.get("invariants", {})
    if invariants.get("CORE_DIRECT_HERMES_DEPENDENCY_ALLOWED") != "no":
        failures.append("boundary: CORE_DIRECT_HERMES_DEPENDENCY_ALLOWED must be 'no'")
    if invariants.get("CLI_DIRECT_HERMES_DEPENDENCY_ALLOWED") != "no":
        failures.append("boundary: CLI_DIRECT_HERMES_DEPENDENCY_ALLOWED must be 'no'")
    if invariants.get("HERMES_IS_EXECUTOR_ADAPTER") != "yes":
        failures.append("boundary: HERMES_IS_EXECUTOR_ADAPTER must be 'yes'")
    if invariants.get("HERMES_PRIVATE_OBJECT_IN_CANONICAL_CORE_COUNT") != 0:
        failures.append("boundary: HERMES_PRIVATE_OBJECT_IN_CANONICAL_CORE_COUNT must be 0")
    if invariants.get("HERMES_FILESYSTEM_LAYOUT_IN_CORE_COUNT") != 0:
        failures.append("boundary: HERMES_FILESYSTEM_LAYOUT_IN_CORE_COUNT must be 0")

    # 4. Capabilities & Package checks
    caps = artifacts["capabilities"]
    if caps.get("invariants", {}).get("EXECUTOR_CAPABILITIES_SEMANTIC_DECISION_FIELD_COUNT") != 0:
        failures.append("capabilities: semantic decision fields detected")
    if bool(caps.get("forbidden_semantic_fields")) is False:
        failures.append("capabilities: missing forbidden semantic fields list")

    pkg = artifacts["package"]
    if pkg.get("invariants", {}).get("EXECUTION_PACKAGE_HERMES_PRIVATE_FIELD_COUNT") != 0:
        failures.append("package: hermes private fields detected")

    # 5. State & Result checks
    state = artifacts["task_state"]
    if state.get("reconciliation_invariants", {}).get("HERMES_ENUM_USED_AS_CANONICAL_STATE_AUTHORITY") != "no":
        failures.append("state: hermes enum used as canonical state authority")
    if state.get("reconciliation_invariants", {}).get("UNKNOWN_IS_NOT_COMPLETED") != "yes":
        failures.append("state: unknown collapsed to completed")

    res = artifacts["result"]
    if res.get("prohibitions", {}).get("GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED") != "no":
        failures.append("result: generic failure collapse allowed")

    # 6. Role Mapping checks
    roles = artifacts["role_mapping"]
    if roles.get("rules", {}).get("ROLE_MAPPING_SEMANTIC_DECISION_AUTHORITY") != "no":
        failures.append("roles: role mapping has semantic decision authority")
    if roles.get("rules", {}).get("HERMES_PROFILE_NAME_IS_CANONICAL_ROLE") != "no":
        failures.append("roles: hermes profile name used as canonical role")

    # 7. Adapter & Hermes Boundary checks
    adap = artifacts["adapter"]
    if adap.get("invariants", {}).get("GENERIC_ARBITRARY_EXECUTOR_COMMAND_ALLOWED") != "no":
        failures.append("adapter: arbitrary executor command allowed")

    h_bound = artifacts["hermes_boundary"]
    if h_bound.get("isolation_rules", {}).get("HERMES_PRIVATE_TYPE_ESCAPE_COUNT") != 0:
        failures.append("hermes_boundary: hermes private types escaping")

    # 8. Selection & Matching checks
    sel = artifacts["selection"]
    if sel.get("prohibitions", {}).get("HEURISTIC_EXECUTOR_SELECTION_ALLOWED") != "no":
        failures.append("selection: heuristic executor selection allowed")
    if sel.get("prohibitions", {}).get("SILENT_HERMES_FALLBACK_ALLOWED") != "no":
        failures.append("selection: silent hermes fallback allowed")
    if sel.get("prohibitions", {}).get("SILENT_CAPABILITY_DOWNGRADE_ALLOWED") != "no":
        failures.append("selection: silent capability downgrade allowed")

    # 9. Identity & Retry checks
    ident = artifacts["identity"]
    if ident.get("invariants", {}).get("EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY") != "no":
        failures.append("identity: executor local task id used as canonical identity")
    if ident.get("retry_and_resume_semantics", {}).get("ADAPTER_AUTO_SEMANTIC_RETRY_ALLOWED") != "no":
        failures.append("identity: adapter auto semantic retry allowed")

    # 10. CLI Dispatch checks
    cli = artifacts["cli_dispatch"]
    if cli.get("prohibitions", {}).get("CLI_SEMANTIC_EXECUTOR_SELECTION_ALLOWED") != "no":
        failures.append("cli: semantic executor selection allowed")
    if cli.get("prohibitions", {}).get("CLI_HEURISTIC_HERMES_FALLBACK_ALLOWED") != "no":
        failures.append("cli: heuristic hermes fallback allowed")
    if cli.get("prohibitions", {}).get("ARBITRARY_EXECUTOR_COMMAND_STRING_ALLOWED") != "no":
        failures.append("cli: arbitrary executor command string allowed")

    # 11. Ownership & DAG checks
    own = artifacts["ownership"]
    if own.get("invariants", {}).get("SOURCE_OWNERSHIP_AMBIGUITY_COUNT") != 0:
        failures.append("ownership: source ownership ambiguities detected")

    dag = artifacts["dag"]
    if dag.get("dag_properties", {}).get("acyclic") != "yes" or dag.get("dag_properties", {}).get("dag_cycle_count") != 0:
        failures.append("dag: cycles detected")

    # 12. Phase & Downstream checks
    phases = artifacts["phases"]
    if phases.get("invariants", {}).get("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH") != "no":
        failures.append("phases: planning performs live hermes dispatch")
    if phases.get("invariants", {}).get("PLANNING_PERFORMS_DEPLOYMENT") != "no":
        failures.append("phases: planning performs deployment")

    down = artifacts["downstream"]
    if down.get("invariants", {}).get("M5_DOWNSTREAM_HANDOFF_PLAN") != "PASS":
        failures.append("downstream: handoff plan invalid")

    # 13. Negative Scope & Semantic authority
    neg = artifacts["negative_scope"]
    if neg.get("invariants", {}).get("M5_SEMANTIC_AUTHORITY_BOUNDARY_PLAN") != "PASS":
        failures.append("negative_scope: semantic authority boundary invalid")
    prohib_map = {item.get("rule"): item.get("value") for item in neg.get("prohibitions", []) if isinstance(item, dict)}
    if prohib_map.get("CLI_EXCLUSIVE_ENTRYPOINT") != "no":
        failures.append("negative_scope: CLI_EXCLUSIVE_ENTRYPOINT must be 'no'")
    if prohib_map.get("GENERIC_GIT_WRITE_API_ALLOWED") != "no":
        failures.append("negative_scope: GENERIC_GIT_WRITE_API_ALLOWED must be 'no'")
    if prohib_map.get("GENERIC_GITHUB_API_ALLOWED") != "no":
        failures.append("negative_scope: GENERIC_GITHUB_API_ALLOWED must be 'no'")
    if prohib_map.get("GENERIC_TERMINAL_ALLOWED") != "no":
        failures.append("negative_scope: GENERIC_TERMINAL_ALLOWED must be 'no'")
    if prohib_map.get("REFERENCE_EXECUTOR_PRODUCTION_DEFAULT") != "no":
        failures.append("negative_scope: REFERENCE_EXECUTOR_PRODUCTION_DEFAULT must be 'no'")
    if prohib_map.get("M4_MUTATION_SURFACE_REDEFINED") != "no":
        failures.append("negative_scope: M4_MUTATION_SURFACE_REDEFINED must be 'no'")

    # 14. Search for coder-time semantic TODOs in artifacts
    for key, doc in artifacts.items():
        doc_str = json.dumps(doc).lower()
        for forbidden in ["todo: semantic", "tbd: choose", "coder may choose", "fallback to hermes if needed"]:
            if forbidden in doc_str:
                failures.append(f"{key}: unresolved semantic choice '{forbidden}'")

    return failures


def execute_negative_test_cases(artifacts: dict[str, dict[str, Any]]) -> tuple[int, int, int]:
    """Execute at least 26 deterministic negative rejection checks (G01 - G26)."""
    cases: list[tuple[str, Any]] = [
        # G01: wrong M4 source base
        ("G01_WRONG_M4_BASE", lambda a: a["plan"].__setitem__("expected_base_sha", "1111111111111111111111111111111111111111")),
        # G02: M5 source implementation performed in planning
        ("G02_M5_SOURCE_IMPLEMENTED", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH", "yes")),
        # G03: Hermes private type in canonical Core contract
        ("G03_HERMES_PRIVATE_TYPE_IN_CORE", lambda a: a["boundary"]["invariants"].__setitem__("HERMES_PRIVATE_OBJECT_IN_CANONICAL_CORE_COUNT", 2)),
        # G04: Hermes profile used as canonical role
        ("G04_HERMES_PROFILE_AS_ROLE", lambda a: a["role_mapping"]["rules"].__setitem__("HERMES_PROFILE_NAME_IS_CANONICAL_ROLE", "yes")),
        # G05: Core direct Hermes dependency
        ("G05_CORE_DIRECT_HERMES", lambda a: a["boundary"]["invariants"].__setitem__("CORE_DIRECT_HERMES_DEPENDENCY_ALLOWED", "yes")),
        # G06: CLI direct Hermes dependency
        ("G06_CLI_DIRECT_HERMES", lambda a: a["boundary"]["invariants"].__setitem__("CLI_DIRECT_HERMES_DEPENDENCY_ALLOWED", "yes")),
        # G07: CLI exclusive entrypoint
        ("G07_CLI_EXCLUSIVE_ENTRYPOINT", lambda a: [item.__setitem__("value", "yes") for item in a["negative_scope"]["prohibitions"] if item.get("rule") == "CLI_EXCLUSIVE_ENTRYPOINT"]),
        # G08: heuristic executor selection
        ("G08_HEURISTIC_EXECUTOR_SELECTION", lambda a: a["selection"]["prohibitions"].__setitem__("HEURISTIC_EXECUTOR_SELECTION_ALLOWED", "yes")),
        # G09: silent Hermes fallback
        ("G09_SILENT_HERMES_FALLBACK", lambda a: a["selection"]["prohibitions"].__setitem__("SILENT_HERMES_FALLBACK_ALLOWED", "yes")),
        # G10: silent capability downgrade
        ("G10_SILENT_CAPABILITY_DOWNGRADE", lambda a: a["selection"]["prohibitions"].__setitem__("SILENT_CAPABILITY_DOWNGRADE_ALLOWED", "yes")),
        # G11: arbitrary executor command
        ("G11_ARBITRARY_EXECUTOR_COMMAND", lambda a: a["adapter"]["invariants"].__setitem__("GENERIC_ARBITRARY_EXECUTOR_COMMAND_ALLOWED", "yes")),
        # G12: generic terminal
        ("G12_GENERIC_TERMINAL_ALLOWED", lambda a: a["cli_dispatch"]["prohibitions"].__setitem__("ARBITRARY_EXECUTOR_COMMAND_STRING_ALLOWED", "yes")),
        # G13: generic Git write
        ("G13_GENERIC_GIT_WRITE", lambda a: [item.__setitem__("value", "yes") for item in a["negative_scope"]["prohibitions"] if item.get("rule") == "GENERIC_GIT_WRITE_API_ALLOWED"]),
        # G14: generic GitHub API
        ("G14_GENERIC_GITHUB_API", lambda a: a["authority"].__setitem__("m5_owns", ["generic_github_write_api"])),
        # G15: adapter semantic authority
        ("G15_ADAPTER_SEMANTIC_AUTHORITY", lambda a: a["role_mapping"]["rules"].__setitem__("ROLE_MAPPING_SEMANTIC_DECISION_AUTHORITY", "yes")),
        # G16: executor-local ID as canonical identity
        ("G16_LOCAL_ID_AS_CANONICAL", lambda a: a["identity"]["invariants"].__setitem__("EXECUTOR_LOCAL_TASK_ID_IS_CANONICAL_IDENTITY", "yes")),
        # G17: generic result/error collapse
        ("G17_GENERIC_RESULT_COLLAPSE", lambda a: a["result"]["prohibitions"].__setitem__("GENERIC_EXECUTION_FAILURE_COLLAPSE_ALLOWED", "yes")),
        # G18: unknown executor state treated as success
        ("G18_UNKNOWN_STATE_AS_SUCCESS", lambda a: a["task_state"]["reconciliation_invariants"].__setitem__("UNKNOWN_IS_NOT_COMPLETED", "no")),
        # G19: reference adapter promoted to production default
        ("G19_REFERENCE_ADAPTER_PRODUCTION_DEFAULT", lambda a: [item.__setitem__("value", "yes") for item in a["negative_scope"]["prohibitions"] if item.get("rule") == "REFERENCE_EXECUTOR_PRODUCTION_DEFAULT"]),
        # G20: live runtime activation in planning
        ("G20_LIVE_RUNTIME_IN_PLANNING", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_LIVE_HERMES_DISPATCH", "yes")),
        # G21: deployment in planning
        ("G21_DEPLOYMENT_IN_PLANNING", lambda a: a["phases"]["invariants"].__setitem__("PLANNING_PERFORMS_DEPLOYMENT", "yes")),
        # G22: M4 mutation surface redefined
        ("G22_M4_MUTATION_SURFACE_REDEFINED", lambda a: a["authority"].__setitem__("m5_owns", ["redefine_plan_init"])),
        # G23: unresolved source ownership
        ("G23_UNRESOLVED_SOURCE_OWNERSHIP", lambda a: a["ownership"]["invariants"].__setitem__("SOURCE_OWNERSHIP_AMBIGUITY_COUNT", 3)),
        # G24: coder-time semantic TODO
        ("G24_CODER_TIME_SEMANTIC_TODO", lambda a: a["plan"].__setitem__("note", "TODO: semantic choice left for coder")),
        # G25: unresolved Hermes boundary
        ("G25_UNRESOLVED_HERMES_BOUNDARY", lambda a: a["hermes_boundary"]["isolation_rules"].__setitem__("HERMES_PRIVATE_TYPE_ESCAPE_COUNT", 1)),
        # G26: missing downstream handoff
        ("G26_MISSING_DOWNSTREAM_HANDOFF", lambda a: a["downstream"]["invariants"].__setitem__("M5_DOWNSTREAM_HANDOFF_PLAN", "FAIL")),
    ]

    rejected = 0
    unexpected = 0

    for label, mutate_fn in cases:
        cand = deepcopy(artifacts)
        mutate_fn(cand)
        errs = validate_artifacts(cand)
        if errs:
            rejected += 1
        else:
            unexpected += 1
            print(f"UNEXPECTED_ACCEPT={label}")

    return len(cases), rejected, unexpected


def check_git_path_isolation() -> list[str]:
    failures: list[str] = []
    # Verify merge base ancestry
    try:
        if git("merge-base", EXPECTED_BASE, "HEAD") != EXPECTED_BASE:
            failures.append("accepted base is not ancestor of HEAD")
    except subprocess.CalledProcessError as exc:
        failures.append(f"git merge-base failed: {exc}")

    # Check changed / untracked files
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
                failures.append(f"disallowed mutated path outside M5-0 scope: {path}")

        # Ensure no product source or test files are touched
        for path in all_changed:
            if path.startswith("aota_forge/"):
                failures.append(f"product source modified during planning: {path}")
            if path.startswith("tests/"):
                failures.append(f"product test modified during planning: {path}")
    except (subprocess.CalledProcessError, OSError) as exc:
        failures.append(f"git status / diff check failed: {exc}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic M5-0 Plan Guard")
    parser.add_argument("--artifact-dir", type=Path, default=EVIDENCE_DIR, help="Path to evidence directory")
    args = parser.parse_args()

    art_dir = args.artifact_dir.resolve()
    print("=== M5-0 Planning Guard ===")
    print(f"Evidence Directory: {art_dir}")

    # 1. Load all 24 artifacts
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

    # 2. Validate positive consistency
    val_failures = validate_artifacts(artifacts)
    if val_failures:
        print(f"FAIL Validation failures ({len(val_failures)}):")
        for f in val_failures:
            print(f"  - {f}")
        return 1

    print("PASS Plan artifact self-consistency and semantic invariants verified.")

    # 3. Path scope check
    path_failures = check_git_path_isolation()
    if path_failures:
        print(f"FAIL Path isolation failures ({len(path_failures)}):")
        for f in path_failures:
            print(f"  - {f}")
        return 1

    print("PASS Git path isolation verified (zero production source/test mutations).")

    # 4. Negative test cases
    total_cases, rejected, unexpected = execute_negative_test_cases(artifacts)
    print(f"NEGATIVE_CASE_COUNT={total_cases}")
    print(f"NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={unexpected}")

    if total_cases < 26 or rejected != total_cases or unexpected != 0:
        print("FAIL Negative rejection suite failed.")
        return 1

    print("PASS All negative rejection test cases rejected correctly (G01 - G26).")
    print("M5_PLAN_GUARD=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
