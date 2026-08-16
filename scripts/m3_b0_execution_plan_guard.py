#!/usr/bin/env python3
"""Guard the Issue #9 M3-B0 execution plan and materialized DAG.

This guard validates planning evidence only.  It deliberately does not create
worktrees, authorize implementation, materialize a graph, flip authority, or
write Issue #9.  A plan PASS is not an implementation or cutover PASS.

Usage:
    python3 scripts/m3_b0_execution_plan_guard.py
    python3 scripts/m3_b0_execution_plan_guard.py --json
    python3 scripts/m3_b0_execution_plan_guard.py --self-test
    python3 scripts/m3_b0_execution_plan_guard.py --artifact /tmp/plan.json
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_SHA = "6945110206993c753fb332d1298c34687c24900c"
PLAN_REL = Path("deploy/evidence/issues/9/m3-b/m3-b-execution-plan.json")
GUARD_REL = Path("scripts/m3_b0_execution_plan_guard.py")
OPTIONAL_DIGEST_REL = Path("deploy/evidence/issues/9/m3-b/m3-b-execution-plan.md")
FORBIDDEN_SOURCE_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

CANONICAL_WORK_ITEMS = tuple(f"M3-B{number}" for number in range(1, 16))
GATES = ("M3-BG1", "M3-BG2")
ALL_NODES = ("M3-B0", *CANONICAL_WORK_ITEMS[:10], "M3-BG1", *CANONICAL_WORK_ITEMS[10:12], "M3-B13", "M3-BG2", "M3-B14", "M3-B15")
AUTHORITY_CLASSES = {
    "SOURCE_ONLY",
    "ISOLATED_NON_AUTHORITATIVE_STATE",
    "SHADOW_NON_AUTHORITATIVE",
    "CUTOVER_MECHANISM_ONLY",
    "POST_CUTOVER_AUTHORITATIVE_VALIDATION",
    "GOVERNANCE_ONLY",
}
EXPECTED_REGRESSION_CLASSES = {
    "B011",
    "B013",
    "B014",
    "B014-F",
    "B014-F1",
    "ACTIVATE-R-current-binding",
    "ACTIVATE-R-host-inspection-escalation",
    "ACTIVATE-R-wrong-source-checkout",
    "WCTX-1",
    "BIND-1",
    "DRIFT-1",
    "RC2-1",
    "CLASSIFY-1",
    "RECOVERY-1",
    "RUNNER-1",
    "E2E-1",
}
EXPECTED_SOURCE_FIELDS = (
    "WORK_ITEM_ID",
    "PURPOSE",
    "DEPENDS_ON",
    "SOURCE_OWNERSHIP",
    "EXPECTED_FILES_OR_MODULES",
    "SHARED_HOT_FILES",
    "PARALLEL_SAFE_WITH",
    "MUTATION_CLASS",
    "AUTHORITY_CLASS",
    "REGRESSION_CLASSES",
    "ACCEPTANCE",
    "CHECKPOINT_OR_REVIEW_REQUIREMENT",
)
EXPECTED_ACCEPTANCE_FIELDS = (
    "SOURCE_ACCEPTANCE",
    "STATIC_OR_DETERMINISTIC_FIXTURE_ACCEPTANCE",
    "REGRESSION_ACCEPTANCE",
    "INDEPENDENT_REVIEW_REQUIRED",
    "STEWARD_RECONCILIATION_REQUIRED",
    "RUNTIME_OR_LIVE_REQUIRED",
)
REQUIRED_EDGES = {
    ("M3-B0", "M3-B1"),
    ("M3-B0", "M3-B2"),
    ("M3-B1", "M3-B3"),
    ("M3-B2", "M3-B3"),
    ("M3-B1", "M3-B4"),
    ("M3-B2", "M3-B4"),
    ("M3-B1", "M3-B5"),
    ("M3-B2", "M3-B5"),
    ("M3-B3", "M3-B6"),
    ("M3-B4", "M3-B6"),
    ("M3-B5", "M3-B6"),
    ("M3-B3", "M3-B7"),
    ("M3-B4", "M3-B7"),
    ("M3-B5", "M3-B7"),
    ("M3-B6", "M3-B7"),
    ("M3-B7", "M3-B8"),
    ("M3-B7", "M3-B9"),
    ("M3-B8", "M3-B10"),
    ("M3-B9", "M3-B10"),
    ("M3-B10", "M3-BG1"),
    ("M3-BG1", "M3-B11"),
    ("M3-B11", "M3-B12"),
    ("M3-B12", "M3-B13"),
    ("M3-B13", "M3-BG2"),
    ("M3-BG2", "M3-B14"),
    ("M3-B10", "M3-B15"),
    ("M3-B14", "M3-B15"),
}
FOLLOWUP_TRANSACTION_ORDER = [
    "resolve parent Subject",
    "verify materialized Decision basis",
    "verify lease",
    "verify expected parent revision",
    "CAS parent",
    "mint canonical child Subject ID",
    "create child Subject at initial revision",
    "create Decision-backed FollowupEdge",
    "advance parent revision",
    "persist idempotency metadata",
    "atomically commit authoritative unit",
]


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _check(results: list[tuple[bool, str]], condition: bool, message: str) -> None:
    results.append((bool(condition), message))


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def _changed_paths(repo_root: Path) -> tuple[bool, set[str]]:
    rc, tracked = _git(repo_root, ["diff", "--name-only", BASE_SHA])
    if rc != 0:
        return False, set()
    rc, status = _git(repo_root, ["status", "--porcelain", "--untracked-files=all"])
    if rc != 0:
        return False, set()
    changed = set(line for line in tracked.splitlines() if line)
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    return True, changed


def _source_boundary(results: list[tuple[bool, str]], repo_root: Path) -> None:
    ok, changed = _changed_paths(repo_root)
    _check(results, ok, "git changed-path inspection succeeds")
    allowed = {str(PLAN_REL), str(GUARD_REL)}
    if (repo_root / OPTIONAL_DIGEST_REL).is_file():
        allowed.add(str(OPTIONAL_DIGEST_REL))
    _check(results, bool(changed), "B0 has planning evidence or guard changes")
    _check(results, changed <= allowed, f"B0 changed paths are limited to plan evidence/guard: {sorted(changed)}")
    forbidden = sorted(path for path in changed if path.startswith(FORBIDDEN_SOURCE_PREFIXES))
    _check(results, not forbidden, f"no aota_forge/core or aota_forge/adapters source changed: {forbidden}")
    rc, _ = _git(repo_root, ["merge-base", "--is-ancestor", BASE_SHA, "HEAD"])
    _check(results, rc == 0, "accepted writable base is an ancestor of HEAD")


def _check_identity(results: list[tuple[bool, str]], plan: dict) -> None:
    for key, expected in (
        ("schema_version", 1),
        ("project_id", "aota_forge"),
        ("milestone", "M3"),
        ("phase", "M3-B"),
        ("lane", "M3-B0"),
        ("governing_plan_issue", "wzjcccc-dotcom/aota-hermes-tools#9"),
        ("canonical_source_repository", "wzjcccc-dotcom/aota_forge"),
        ("base_sha", BASE_SHA),
        ("accepted_writable_base", BASE_SHA),
    ):
        _check(results, plan.get(key) == expected, f"top-level {key}={expected!r}")
    governance = plan.get("governance_preconditions", {})
    for key, expected in (
        ("M3_A_STATUS", "completed"),
        ("M3_PHASE", "M3-B"),
        ("M3_B_STATUS", "planned"),
        ("M3_B_EXECUTION_PLANNING_READY", "yes"),
        ("M3_B_EXECUTION_AUTHORIZED", "no"),
        ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
        ("M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION", "no"),
        ("ISSUE_9_MUTATED", "no"),
        ("CONTROL_COMMENTS_MUTATED", "no"),
        ("SOURCE_CODE_CHANGED", "no"),
        ("M3_B_IMPLEMENTATION_STARTED", "no"),
    ):
        _check(results, governance.get(key) == expected, f"governance_preconditions.{key}={expected!r}")
    issue = governance.get("observed_governing_issue_9", {})
    _check(results, issue.get("state") == "open", "governing Issue #9 remains open")
    _check(results, issue.get("mutation_performed") == "no", "Issue #9 mutation is explicitly absent")


def _work_item_map(plan: dict, results: list[tuple[bool, str]]) -> dict[str, dict]:
    rows = plan.get("work_items", [])
    by_id = {row.get("work_item_id"): row for row in rows if isinstance(row, dict)}
    _check(results, len(by_id) == len(rows), "work item IDs are unique")
    _check(results, set(CANONICAL_WORK_ITEMS) <= set(by_id), "all canonical M3-B1..M3-B15 work items exist")
    _check(results, set(GATES) <= set(by_id), "BG1 and BG2 work items exist")
    _check(results, "M3-B0" in by_id, "B0 planning lane exists in work items")
    _check(results, plan.get("work_item_set", {}).get("canonical_implementation_work_item_count") == 15,
           "canonical implementation work item count is 15")
    for work_id, row in by_id.items():
        _check(results, row.get("implementation_status") == "not_started",
               f"{work_id} does not claim implementation started")
        _check(results, row.get("authority_class") in AUTHORITY_CLASSES,
               f"{work_id} has an allowed authority class")
    return by_id


def _check_dag(results: list[tuple[bool, str]], plan: dict, by_id: dict[str, dict]) -> None:
    dag = plan.get("dependency_dag", {})
    nodes = dag.get("nodes", [])
    edges_raw = dag.get("edges", [])
    _check(results, set(nodes) == set(ALL_NODES), "DAG nodes exactly cover B0, B1-B15, BG1, and BG2")
    _check(results, len(nodes) == len(set(nodes)), "DAG nodes are unique")
    edges: set[tuple[str, str]] = set()
    valid_edge_shape = True
    for edge in edges_raw:
        if not isinstance(edge, list) or len(edge) != 2 or not all(isinstance(part, str) for part in edge):
            valid_edge_shape = False
            continue
        pair = (edge[0], edge[1])
        edges.add(pair)
        _check(results, pair[0] in set(nodes) and pair[1] in set(nodes), f"DAG edge endpoints exist: {pair}")
    _check(results, valid_edge_shape, "DAG edges have two string endpoints")
    _check(results, len(edges) == len(edges_raw), "DAG edges are unique")
    declared_edges = {
        (dependency, work_id)
        for work_id, row in by_id.items()
        for dependency in row.get("depends_on", [])
        if isinstance(dependency, str)
    }
    _check(results, edges == declared_edges, "DAG edges exactly materialize work-item depends_on declarations")
    _check(results, REQUIRED_EDGES <= edges, "DAG contains all required dependency edges")
    _check(results, ("M3-B10", "M3-B15") in edges, "DAG contains the accepted W-M3B-13 -> W-M3B-14 equivalent edge")

    indegree = {node: 0 for node in nodes}
    adjacency = {node: [] for node in nodes}
    for source, target in edges:
        if source in adjacency and target in indegree:
            adjacency[source].append(target)
            indegree[target] += 1
    queue = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for child in adjacency[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    _check(results, visited == len(nodes), "DAG is acyclic")

    for target in ("M3-B1", "M3-B2"):
        _check(results, set(by_id.get(target, {}).get("depends_on", [])) == {"M3-B0"},
               f"{target} is an initial B0 child")
    _check(results, ("M3-B1", "M3-B2") not in edges and ("M3-B2", "M3-B1") not in edges,
           "B1 and B2 are sibling lanes, not serial dependencies")
    for source, target in (
        ("M3-B10", "M3-BG1"),
        ("M3-BG1", "M3-B11"),
        ("M3-B11", "M3-B12"),
        ("M3-B12", "M3-B13"),
        ("M3-B13", "M3-BG2"),
        ("M3-BG2", "M3-B14"),
        ("M3-B14", "M3-B15"),
    ):
        _check(results, (source, target) in edges, f"DAG contains {source} -> {target}")


def _check_ready_parallel_and_ownership(
    results: list[tuple[bool, str]], plan: dict, by_id: dict[str, dict]
) -> None:
    ready = plan.get("ready_sets", {})
    _check(results, ready.get("CURRENT_READY_SET") == ["M3-B0"], "CURRENT_READY_SET is M3-B0")
    _check(results, ready.get("CURRENT_AUTHORIZED_LANES") == ["M3-B0"], "only B0 is currently authorized")
    _check(results, ready.get("NEXT_IMPLEMENTATION_READY_SET") == ["M3-B1", "M3-B2"],
           "next implementation ready set is B1,B2")
    for key in ("B0_DOES_NOT_AUTHORIZE_B1_B2", "B0_DOES_NOT_START_B1_B2", "B0_DOES_NOT_CREATE_B1_B2_WORKTREES"):
        _check(results, ready.get(key) == "yes", f"ready_sets.{key}=yes")
    parallel = plan.get("parallel_safety", {})
    _check(results, parallel.get("PARALLEL_WRITERS_USE_SEPARATE_WORKTREES") == "yes",
           "parallel writers use separate worktrees")
    _check(results, parallel.get("SHARED_HOT_FILE_CONCURRENT_WRITE_ALLOWED") == "no",
           "shared hot-file concurrent writes are denied")
    _check(results, parallel.get("B1_B2_PARALLEL_SAFE") == "conditional", "B1/B2 parallel safety is conditional")
    _check(results, "serial" in str(parallel.get("B6_PLUS_SERIALIZATION_POLICY", "")).lower(),
           "B6 onward defaults to serial execution")
    _check(results, parallel.get("NO_AUTOMATIC_BRANCH_MERGES_IN_B0") == "yes",
           "B0 does not merge implementation branches")

    source_map = plan.get("source_ownership", {})
    _check(results, set(source_map) == set(CANONICAL_WORK_ITEMS), "source ownership map covers exactly B1-B15")
    for work_id in CANONICAL_WORK_ITEMS:
        entry = source_map.get(work_id, {})
        for field in EXPECTED_SOURCE_FIELDS:
            _check(results, field in entry, f"{work_id} source ownership declares {field}")
        acceptance = entry.get("ACCEPTANCE", {})
        for field in EXPECTED_ACCEPTANCE_FIELDS:
            _check(results, field in acceptance, f"{work_id} acceptance declares {field}")
        _check(results, entry.get("AUTHORITY_CLASS") in AUTHORITY_CLASSES,
               f"{work_id} source map authority class is allowed")
        _check(results, entry.get("AUTHORITY_CLASS") == by_id.get(work_id, {}).get("authority_class"),
               f"{work_id} source map and work item authority classes agree")
        _check(results, entry.get("DEPENDS_ON") == by_id.get(work_id, {}).get("depends_on"),
               f"{work_id} source map and work item dependencies agree")
    _check(results, source_map.get("M3-B7", {}).get("FROZEN_FOLLOWUP_TRANSACTION_ORDER") == FOLLOWUP_TRANSACTION_ORDER,
           "B7 preserves the exact R1 followup transaction order")

    _check(results, plan.get("defect_carry_forward", {}).get("I9-B008", {}).get("owner") == "M3-B1",
           "I9-B008 owner is M3-B1")
    _check(results, plan.get("defect_carry_forward", {}).get("I9-B008", {}).get("ordering") == "early_prerequisite",
           "I9-B008 is an early prerequisite")
    _check(results, plan.get("defect_carry_forward", {}).get("NF2", {}).get("owner") == "M3-B2",
           "NF2 owner is M3-B2")
    _check(results, plan.get("defect_carry_forward", {}).get("NF2", {}).get("ordering") == "early_prerequisite",
           "NF2 is an early prerequisite")


def _check_gates(results: list[tuple[bool, str]], plan: dict, by_id: dict[str, dict]) -> None:
    shadow = plan.get("shadow_materialization_gate", {})
    _check(results, shadow.get("gate_id") == "M3-BG1", "BG1 gate exists")
    _check(results, shadow.get("PLAN_GATE_DEFINITION_STATUS") == "PASS", "BG1 plan gate definition is PASS")
    _check(results, shadow.get("B11_READY") == "only_if_PASS", "B11 is ready only after BG1 PASS")
    _check(results, shadow.get("SHADOW_GRAPH_IS_AUTHORITY") == "no", "shadow graph is non-authoritative")
    _check(results, shadow.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no", "BG1 denies authoritative writes")
    required_bg1 = {
        "I9_B008_SOURCE_FIXED": "yes",
        "NF2_SOURCE_FIXED": "yes",
        "CANONICAL_GRAPH_FOUNDATION": "PASS",
        "SUBJECT_IDENTITY_MECHANICS": "PASS",
        "AUTHORITY_LEASE_FOUNDATION": "PASS",
        "REVISION_CAS_TRANSACTION": "PASS",
        "CANONICAL_TRANSITION_LAYER": "PASS",
        "PROJECTION_FOUNDATION": "PASS where required",
        "BINDING_RECOVERY_FOUNDATION": "PASS where required",
        "BEHAVIORAL_FOUNDATION_REGRESSIONS": "PASS",
        "SHADOW_GRAPH_IS_AUTHORITY": "no",
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    }
    _check(results, shadow.get("required_prerequisites") == required_bg1, "BG1 prerequisites are frozen exactly")
    _check(results, "M3-BG1" in by_id.get("M3-B11", {}).get("depends_on", []), "BG1 precedes B11")

    cutover = plan.get("authority_cutover_gate", {})
    _check(results, cutover.get("gate_id") == "M3-BG2", "BG2 gate exists")
    _check(results, cutover.get("PLAN_GATE_DEFINITION_STATUS") == "PASS", "BG2 plan gate definition is PASS")
    _check(results, cutover.get("B14_READY") == "only_if_PASS", "B14 is ready only after BG2 PASS")
    required_bg2 = {
        "I9_B008_SOURCE_FIXED": "yes",
        "NF2_PRINCIPAL_ENFORCEMENT_IMPLEMENTED": "yes",
        "CANONICAL_GRAPH_FOUNDATION_IMPLEMENTED": "yes",
        "SUBJECT_IDENTITY_IMPLEMENTED": "yes",
        "REVISION_CAS_TRANSACTION_IMPLEMENTED": "yes",
        "AUTHORITY_ENGINE_IMPLEMENTED": "yes",
        "CAPABILITY_LEASE_IMPLEMENTED": "yes",
        "IDEMPOTENCY_IMPLEMENTED": "yes",
        "REQUIRED_M3_BEHAVIORAL_REGRESSION_PROOFS": "PASS",
        "BOOTSTRAP_CANDIDATE_VALIDATED": "yes",
        "CUTOVER_MECHANICS_IMPLEMENTED": "yes",
        "CUTOVER_AUTHORIZED": "yes",
    }
    _check(results, cutover.get("required_prerequisites") == required_bg2, "BG2 prerequisites are frozen exactly")
    activation = cutover.get("activation_outputs", {})
    for key, expected in (
        ("CUTOVER_COMPLETED", "yes"),
        ("DURABLE_SUBJECT_GRAPH_IS_CURRENT_LIFECYCLE_AUTHORITY", "yes"),
        ("LEGACY_LIFECYCLE_AUTHORITY_RETIRED_OR_SHADOW_ONLY", "yes"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
        ("AUTHORITATIVE_RUNTIME_LIFECYCLE_GRAPH_WRITES_ALLOWED", "yes"),
    ):
        _check(results, activation.get(key) == expected, f"BG2 activation output {key}={expected}")
    _check(results, cutover.get("CURRENT_EXECUTION_EVALUATION") == "not_passed_in_B0",
           "BG2 is not passed by B0")
    _check(results, cutover.get("CURRENT_B0_ACTIVATION_OUTPUTS", {}).get("AUTHORITATIVE_RUNTIME_LIFECYCLE_GRAPH_WRITES_ALLOWED") == "no",
           "B0 runtime authoritative writes remain denied")
    _check(results, cutover.get("M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES") == "no",
           "M3-A PASS does not authorize graph writes")
    _check(results, cutover.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
           "BG2 plan artifact keeps writes denied")
    _check(results, "M3-BG2" in by_id.get("M3-B14", {}).get("depends_on", []), "BG2 precedes B14")

    boundary = plan.get("cutover_authorization_boundary", {})
    for key, expected in (
        ("CUTOVER_IMPLEMENTATION_EQUALS_CUTOVER_COMPLETION", "no"),
        ("CUTOVER_REQUIRES_EXPLICIT_AUTHORIZATION", "yes"),
        ("M3_B_EXECUTION_PLAN_DOES_NOT_AUTHORIZE_CUTOVER", "yes"),
        ("B13_PASS_IMPLIES_CUTOVER_COMPLETED", "no"),
        ("recovery_engine_may_trigger_cutover", "no"),
    ):
        _check(results, boundary.get(key) == expected, f"cutover boundary {key}={expected}")
    _check(results, boundary.get("B13") == "cutover mechanics implementation", "B13 is mechanics only")
    _check(results, boundary.get("BG2") == "cutover authority/activation gate", "BG2 is authority activation only")

    _check(results, by_id.get("M3-B13", {}).get("authority_class") == "CUTOVER_MECHANISM_ONLY",
           "B13 cannot claim post-cutover authority")
    _check(results, by_id.get("M3-B11", {}).get("authority_class") == "SHADOW_NON_AUTHORITATIVE",
           "B11 is classified shadow non-authoritative")
    _check(results, by_id.get("M3-B14", {}).get("authority_class") == "POST_CUTOVER_AUTHORITATIVE_VALIDATION",
           "B14 is post-cutover validation")

    governance = plan.get("governance_preconditions", {})
    _check(results, governance.get("M3_B_SOURCE_CONSTRUCTION_REQUIRES_CUTOVER") == "no",
           "source construction does not require cutover")
    _check(results, governance.get("M3_B_AUTHORITATIVE_WRITE_REQUIRES_CUTOVER") == "yes",
           "authoritative graph writes require cutover")


def _check_regressions(results: list[tuple[bool, str]], plan: dict, repo_root: Path) -> None:
    rows = plan.get("regression_ownership", [])
    classes = {row.get("FAILURE_CLASS") for row in rows if isinstance(row, dict)}
    _check(results, len(rows) == 16, "regression ownership has 16 rows")
    _check(results, classes == EXPECTED_REGRESSION_CLASSES, "regression ownership accounts for exactly the 16 known classes")
    for row in rows:
        for field in (
            "FAILURE_CLASS",
            "CURRENT_DISPOSITION",
            "DESIGN_OWNER",
            "IMPLEMENTATION_OWNER",
            "BEHAVIORAL_PROOF_OWNER",
            "GATE_DEPENDENCY",
            "MAY_TRANSITION_FROM_NYI_AT",
            "FINAL_REQUIRED_STATE_FOR_M3_CLOSE",
        ):
            _check(results, field in row, f"regression row {row.get('FAILURE_CLASS')} declares {field}")
    matrix_path = repo_root / "deploy/evidence/issues/9/m2-successor-regression-matrix.json"
    try:
        matrix = _read_json(matrix_path)
        matrix_rows = {row.get("failure_class"): row for row in matrix.get("failure_classes", [])}
    except (FileNotFoundError, json.JSONDecodeError):
        matrix_rows = {}
    _check(results, set(matrix_rows) == EXPECTED_REGRESSION_CLASSES, "source regression matrix contains the same 16 classes")
    for row in rows:
        failure_class = row.get("FAILURE_CLASS")
        _check(results, row.get("CURRENT_DISPOSITION") == matrix_rows.get(failure_class, {}).get("expected_successor_disposition"),
               f"{failure_class} disposition is unchanged from source matrix")
    _check(results, plan.get("acceptance", {}).get("REGRESSION_MATRIX_MUTATED") == "no",
           "B0 does not mutate regression dispositions")
    _check(results, "only" in plan.get("acceptance", {}).get("claim_status", "") and "proof" in plan.get("acceptance", {}).get("claim_status", ""),
           "claim boundary denies unsupported implementation success")


def _check_neutrality_and_semantics(results: list[tuple[bool, str]], plan: dict) -> None:
    policy = plan.get("validation_policy", {})
    runtime = plan.get("runtime_boundary", {})
    _check(results, policy.get("M3_CORE_HERMES_DEPENDENCY") == "no", "M3 core has no Hermes dependency")
    _check(results, policy.get("HERMES_TASK_MAIN_REQUIRED_FOR_M3_B_EXECUTION") == "no", "Hermes task-main is not required")
    _check(results, runtime.get("M3_CORE_HERMES_DEPENDENCY") == "no", "runtime boundary preserves Hermes neutrality")
    boundary = plan.get("semantic_boundary_check", {})
    _check(results, boundary.get("M3_B_EXECUTION_PLAN_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION") == "no",
           "B0 requires no new semantic design decision")
    _check(results, boundary.get("unresolved_semantic_design_questions") == [],
           "no unresolved semantic design question is hidden in B0")
    _check(results, plan.get("cutover_authorization_boundary", {}).get("M3_B_EXECUTION_PLAN_DOES_NOT_AUTHORIZE_CUTOVER") == "yes",
           "executor plan does not authorize cutover")


def _check_claims_and_source_boundary(results: list[tuple[bool, str]], plan: dict, repo_root: Path) -> None:
    acceptance = plan.get("acceptance", {})
    for key, expected in (
        ("M3_B_EXECUTION_AUTHORIZED", "no"),
        ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
        ("ISSUE_9_MUTATED", "no"),
        ("CONTROL_COMMENTS_MUTATED", "no"),
        ("M3_B_IMPLEMENTATION_STARTED", "no"),
        ("SOURCE_CODE_CHANGED", "no"),
    ):
        _check(results, acceptance.get(key) == expected, f"acceptance.{key}={expected!r}")
    _source_boundary(results, repo_root)


def run_checks(plan: dict, repo_root: Path) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    _check_identity(results, plan)
    by_id = _work_item_map(plan, results)
    _check_dag(results, plan, by_id)
    _check_ready_parallel_and_ownership(results, plan, by_id)
    _check_gates(results, plan, by_id)
    _check_regressions(results, plan, repo_root)
    _check_neutrality_and_semantics(results, plan)
    _check_claims_and_source_boundary(results, plan, repo_root)
    return results


def _edge(plan: dict, source: str, target: str) -> list:
    return [source, target]


def run_self_test(repo_root: Path, plan: dict) -> dict:
    mutations = [
        (
            "B13 -> B14 directly with no BG2",
            lambda p: (
                p["dependency_dag"]["edges"].remove(_edge(p, "M3-BG2", "M3-B14")),
                p["dependency_dag"]["edges"].append(_edge(p, "M3-B13", "M3-B14")),
            ),
        ),
        (
            "BG2 missing completed cutover prerequisite",
            lambda p: p["authority_cutover_gate"]["activation_outputs"].__setitem__("CUTOVER_COMPLETED", "no"),
        ),
        (
            "B11 classified authoritative",
            lambda p: p["work_items"][next(i for i, row in enumerate(p["work_items"]) if row["work_item_id"] == "M3-B11")].__setitem__(
                "authority_class", "POST_CUTOVER_AUTHORITATIVE_VALIDATION"
            ),
        ),
        (
            "I9-B008 assigned after mutation-capable foundation",
            lambda p: p["defect_carry_forward"]["I9-B008"].__setitem__("owner", "M3-B6"),
        ),
        (
            "NF2 assigned after authority activation",
            lambda p: p["defect_carry_forward"]["NF2"].__setitem__("owner", "M3-B13"),
        ),
        (
            "B1/B2 auto-started by B0",
            lambda p: p["ready_sets"].__setitem__("CURRENT_READY_SET", ["M3-B0", "M3-B1"]),
        ),
        (
            "M3_B_EXECUTION_AUTHORIZED=yes",
            lambda p: p["governance_preconditions"].__setitem__("M3_B_EXECUTION_AUTHORIZED", "yes"),
        ),
        (
            "AUTHORITATIVE_GRAPH_WRITES_ALLOWED=yes",
            lambda p: p["governance_preconditions"].__setitem__("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "yes"),
        ),
        (
            "DAG cycle introduced",
            lambda p: p["dependency_dag"]["edges"].append(_edge(p, "M3-B15", "M3-B0")),
        ),
        (
            "one regression ownership row removed",
            lambda p: p["regression_ownership"].pop(),
        ),
    ]
    positive = run_checks(plan, repo_root)
    results = []
    with tempfile.TemporaryDirectory(prefix="m3-b0-guard-") as directory:
        for label, mutate in mutations:
            candidate = copy.deepcopy(plan)
            try:
                mutate(candidate)
                path = Path(directory) / ("candidate-" + str(len(results)) + ".json")
                path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, str(repo_root / GUARD_REL), "--artifact", str(path), "--json"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
                rejected = proc.returncode != 0
            except (OSError, StopIteration, ValueError):
                rejected = True
            results.append({"mutation": label, "rejected": rejected, "result": "PASS" if rejected else "FAIL"})
    return {
        "positive_control_passes": all(ok for ok, _ in positive),
        "negative_mutation_count": len(results),
        "mutations": results,
        "verdict": "PASS" if all(ok for ok, _ in positive) and all(row["rejected"] for row in results) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-B0 execution plan guard")
    parser.add_argument("--artifact", type=Path, help="plan artifact path")
    parser.add_argument("--repo-root", type=Path, help="repository root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    parser.add_argument("--self-test", action="store_true", help="run ten isolated negative mutations")
    args = parser.parse_args()

    repo_root = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    artifact = (args.artifact or repo_root / PLAN_REL).resolve()
    try:
        plan = _read_json(artifact)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: plan artifact unreadable: {exc}")
        return 1

    negative = run_self_test(repo_root, plan) if args.self_test else None
    checks = run_checks(plan, repo_root)
    if negative is not None:
        _check(checks, negative["verdict"] == "PASS", f"M3_B0_NEGATIVE_GUARD_PROOF={negative['verdict']}")
    passed = sum(1 for ok, _ in checks if ok)
    verdict = "PASS" if passed == len(checks) else "FAIL"
    dag = plan.get("dependency_dag", {})
    summary = {
        "artifact": str(artifact.relative_to(repo_root)) if artifact.is_relative_to(repo_root) else str(artifact),
        "base_sha": BASE_SHA,
        "work_item_count": len(CANONICAL_WORK_ITEMS),
        "dag_node_count": len(dag.get("nodes", [])),
        "dag_edge_count": len(dag.get("edges", [])),
        "regression_class_count": len(plan.get("regression_ownership", [])),
        "checks": len(checks),
        "checks_passed": passed,
        "verdict": verdict,
        "M3_B0_GUARD": verdict,
        "M3_B0_NEGATIVE_GUARD_PROOF": "NOT_RUN" if negative is None else negative["verdict"],
        "negative_mutations": None if negative is None else negative["mutations"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if verdict == "PASS" else 1

    print(f"artifact: {summary['artifact']}")
    print(f"base: {BASE_SHA}")
    print(f"checks: {passed}/{len(checks)} PASS")
    for ok, message in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if negative is not None:
        print(f"M3_B0_NEGATIVE_GUARD_PROOF: {negative['verdict']}")
        for mutation in negative["mutations"]:
            print(f"  {'PASS' if mutation['rejected'] else 'FAIL'}  reject {mutation['mutation']}")
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
