#!/usr/bin/env python3
"""M3-A-R1 integrated architecture contract repair guard.

This guard validates the narrow repair of the four blocking M3-A review
findings.  It checks the standalone repair artifact, the integrated M3-A
proof, and the minimally amended A5/A12 contract records.  It performs no
implementation, graph materialization, cutover, or repository mutation.

Usage:
    python3 scripts/m3_a_r1_contract_repair_guard.py
    python3 scripts/m3_a_r1_contract_repair_guard.py --json
    python3 scripts/m3_a_r1_contract_repair_guard.py --self-test
    python3 scripts/m3_a_r1_contract_repair_guard.py --artifact /tmp/x.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPAIR_BASE = "a58d0ca885610f6f9db7798c955e7afd0479688b"
PROOF_REL = Path("deploy/evidence/issues/9/m3-a/m3-a-design-proof.json")
REPAIR_REL = Path("deploy/evidence/issues/9/m3-a/m3-a-r1-contract-repair.json")
GUARD_REL = Path("scripts/m3_a_r1_contract_repair_guard.py")
A6_GUARD_REL = Path("scripts/m3_a6_integrated_design_guard.py")
A12_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
A12_GUARD_REL = Path("scripts/m3_a12_contract_reconciliation_guard.py")
A45_GUARD_REL = Path("scripts/m3_a45_contract_reconciliation_guard.py")
A5_REL = Path("deploy/evidence/issues/9/m3-a/a5-binding-recovery.json")
A1_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
FORBIDDEN_SOURCE_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")
ALLOWED_CHANGED_PATHS = {
    str(PROOF_REL),
    str(REPAIR_REL),
    str(GUARD_REL),
    str(A6_GUARD_REL),
    str(A12_REL),
    str(A12_GUARD_REL),
    str(A45_GUARD_REL),
    str(A5_REL),
}

EXPECTED_STEPS = [
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

EXPECTED_BLOCKERS = {
    "B-R1-01": "lease_target_revision_contradiction",
    "B-R1-02": "followup_two_root_lease_scope",
    "B-R1-03": "completion_subject_ref_schema_mismatch",
    "B-R1-04": "authoritative_write_gate_missing_cutover",
}

REQUIRED_GATE_CONDITIONS = {
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
    "CUTOVER_AUTHORIZED": "yes",
    "CUTOVER_COMPLETED": "yes",
    "DURABLE_SUBJECT_GRAPH_IS_CURRENT_LIFECYCLE_AUTHORITY": "yes",
    "LEGACY_LIFECYCLE_AUTHORITY_RETIRED_OR_SHADOW_ONLY": "yes",
    "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY": "no",
}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _check(results: list[tuple[bool, str]], condition: bool, message: str) -> None:
    results.append((condition, message))


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def _changed_paths(repo_root: Path) -> tuple[bool, set[str]]:
    rc, tracked = _git(repo_root, ["diff", "--name-only", REPAIR_BASE])
    if rc != 0:
        return False, set()
    rc, status = _git(repo_root, ["status", "--porcelain"])
    if rc != 0:
        return False, set()
    changed = set(tracked.splitlines()) if tracked else set()
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        changed.add(path)
    return True, changed


def _work_item_id(value: str) -> str:
    return value.split(" ", 1)[0]


def _check_authority(results: list[tuple[bool, str]], repair: dict, proof: dict) -> None:
    decision = repair.get("repair_decisions", {}).get("authority_target", {})
    identity = proof.get("identity_authority_model", {})
    revision = proof.get("revision_cas_model", {}).get("record_revision_authority", {})
    expected = {
        "CANONICAL_MUTATION_AUTHORITY_TARGET": "Subject_aggregate",
        "EXECUTION_INDEPENDENT_MUTATION_REVISION_ROOT": "no",
        "COMPLETION_INDEPENDENT_MUTATION_REVISION_ROOT": "no",
        "DECISION_INDEPENDENT_MUTATION_REVISION_ROOT": "no",
        "RECORD_OPERATION_AUTHORITY_RESOLVES_TO_OWNING_SUBJECT": "yes",
        "TYPED_SEMANTIC_OBJECT_REF != INDEPENDENT_AUTHORITY_ROOT": "yes",
        "BARE_RECORD_ID_GRANTS_AUTHORITY": "no",
    }
    for key, value in expected.items():
        _check(results, decision.get(key) == value, f"repair authority_target.{key}={value}")
    _check(
        results,
        identity.get("normal_m3_mutation_authority_root") == "Subject aggregate",
        "integrated normal M3 mutation authority root is Subject aggregate",
    )
    for key in (
        "EXECUTION_INDEPENDENT_MUTATION_REVISION_ROOT",
        "COMPLETION_INDEPENDENT_MUTATION_REVISION_ROOT",
        "DECISION_INDEPENDENT_MUTATION_REVISION_ROOT",
    ):
        _check(results, identity.get("independent_mutation_revision_roots", {}).get(key) == "no",
               f"integrated identity authority {key}=no")
        _check(results, identity.get(key) == "no", f"integrated identity direct flag {key}=no")
        _check(results, revision.get(key) == "no", f"integrated revision authority {key}=no")
        _check(results, proof.get("revision_cas_model", {}).get(key) == "no",
               f"integrated revision direct flag {key}=no")
    _check(
        results,
        identity.get("RECORD_OPERATION_AUTHORITY_RESOLVES_TO_OWNING_SUBJECT") == "yes",
        "integrated record operation authority resolves to owning Subject",
    )
    _check(
        results,
        identity.get("record_operation_resolution") == [
            "record_ref -> resolve owning Subject",
            "authority evaluation against owning Subject",
            "lease expected_revision = owning Subject revision",
            "Subject aggregate CAS",
        ],
        "record operation resolution is Subject-owned and CAS-bound",
    )


def _check_followup(results: list[tuple[bool, str]], repair: dict, proof: dict) -> None:
    lease = repair.get("repair_decisions", {}).get("followup_creation_lease", {})
    for key, expected in (
        ("transaction_shape", "PARENT_AUTHORIZED_NEW_CHILD_TRANSACTION"),
        ("operation", "create_followup_subject"),
        ("object_ref", "parent Subject"),
        ("expected_revision", "parent Subject revision"),
        ("FOLLOWUP_CREATION_LEASE_TARGET", "parent_subject"),
        ("FOLLOWUP_CREATION_REQUIRES_PREEXISTING_CHILD_LEASE", "no"),
        ("PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY", "no"),
    ):
        _check(results, lease.get(key) == expected, f"followup lease {key}={expected}")
    _check(results, lease.get("authority_basis") == "exact durable materialized Decision",
           "followup lease is based on the exact materialized Decision")
    _check(results, lease.get("bounded_mutation_scope", "").startswith("exact atomic creation"),
           "followup lease scope is the exact bounded creation operation")

    transaction = repair.get("followup_atomic_transaction", {})
    _check(results, transaction.get("transaction_steps") == EXPECTED_STEPS,
           "followup transaction has the frozen eleven-step order")
    for key, expected in (
        ("CHILD_ID_MINTED_INSIDE_TRANSACTION", "yes"),
        ("CHILD_INITIAL_REVISION", "1"),
        ("NO_ORPHAN_CHILD_SUBJECT", "yes"),
        ("NO_FOLLOWUP_EDGE_WITHOUT_CHILD", "yes"),
        ("NO_CHILD_WITHOUT_DECISION_BASIS", "yes"),
        ("NO_DUPLICATE_SEMANTIC_CHILD_EFFECT", "yes"),
        ("NO_NEW_SEMANTIC_DECISION_IN_TRANSACTION", "yes"),
        ("child_preexists", "no"),
    ):
        _check(results, transaction.get(key) == expected, f"followup transaction {key}={expected}")

    model = proof.get("transaction_model", {}).get("followup_atomic_unit", {})
    _check(results, model.get("authoritative_transaction_order") == EXPECTED_STEPS,
           "integrated proof has the same followup transaction order")
    for key, expected in (
        ("CHILD_ID_MINTED_INSIDE_TRANSACTION", "yes"),
        ("CHILD_INITIAL_REVISION", "1"),
        ("NO_FOLLOWUP_EDGE_WITHOUT_CHILD", "yes"),
        ("NO_NEW_SEMANTIC_DECISION_IN_TRANSACTION", "yes"),
    ):
        _check(results, model.get(key) == expected, f"integrated followup {key}={expected}")
    flags = proof.get("principal_lease_model", {}).get("flags", {})
    for key, expected in (
        ("FOLLOWUP_CREATION_LEASE_TARGET", "parent_subject"),
        ("FOLLOWUP_CREATION_REQUIRES_PREEXISTING_CHILD_LEASE", "no"),
        ("PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY", "no"),
    ):
        _check(results, flags.get(key) == expected, f"integrated lease flag {key}={expected}")


def _check_completion(results: list[tuple[bool, str]], repair: dict, proof: dict, a1: dict, a5: dict) -> None:
    binding = repair.get("repair_decisions", {}).get("completion_binding", {})
    for key, expected in (
        ("A1_CANONICAL_SCHEMA_CHANGED", "no"),
        ("COMPLETION_DIRECT_SUBJECT_REF_REQUIRED", "no"),
        ("COMPLETION_SUBJECT_RESOLUTION", "via_execution_owner"),
        ("canonical_path", "Completion -> execution_ref -> Execution -> owning Subject"),
    ):
        _check(results, binding.get(key) == expected, f"completion binding {key}={expected}")
    integrated = proof.get("binding_model", {}).get("completion_binding", {})
    _check(results, integrated.get("COMPLETION_DIRECT_SUBJECT_REF_REQUIRED") == "no",
           "integrated Completion direct subject_ref is not required")
    _check(results, integrated.get("COMPLETION_SUBJECT_RESOLUTION") == "via_execution_owner",
           "integrated Completion resolves through Execution owner")

    completion = next(
        (record for record in a1.get("canonical_records", {}).get("records", [])
         if record.get("record") == "Completion"),
        {},
    )
    _check(results, "execution_ref" in completion.get("canonical_fields", []),
           "A1 Completion canonical schema has execution_ref")
    _check(results, "subject_ref" not in completion.get("required_fields", []),
           "A1 Completion required fields do not require subject_ref")
    _check(results, "subject_ref" not in completion.get("canonical_fields", []),
           "A1 Completion canonical fields do not contain direct subject_ref")
    completion_kind = next(
        (kind for kind in a5.get("binding_kinds", {}).get("kinds", [])
         if kind.get("BINDING_KIND") == "completion_subject"),
        {},
    )
    _check(results, "execution_ref" in completion_kind.get("CANDIDATE_SOURCE", ""),
           "A5 Completion binding source uses execution_ref")
    _check(results, "explicit subject_ref" not in completion_kind.get("CANDIDATE_SOURCE", ""),
           "A5 Completion binding source does not require direct subject_ref")
    _check(results, "execution_ref" in completion_kind.get("VALIDITY_RULE", ""),
           "A5 Completion validity resolves through Execution")


def _check_cutover_and_gate(results: list[tuple[bool, str]], repair: dict, proof: dict) -> None:
    cutover = repair.get("repair_decisions", {}).get("write_gate_cutover", {})
    for key, expected in (
        ("SHADOW_GRAPH_MATERIALIZATION_BEFORE_CUTOVER", "allowed_non_authoritative"),
        ("AUTHORITATIVE_LIFECYCLE_GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
        ("SHADOW_GRAPH_IS_LIFECYCLE_AUTHORITY", "no"),
        ("SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "no"),
        ("SHADOW_GRAPH_CAN_ACCEPT_RUNTIME_LIFECYCLE_MUTATION", "no"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
        ("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY", "yes"),
        ("RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "no"),
    ):
        _check(results, cutover.get(key) == expected, f"cutover contract {key}={expected}")
    _check(results, cutover.get("GRAPH_WRITE_BEFORE_CUTOVER") == "denied",
           "legacy graph-write invariant remains denied")

    proof_cutover = proof.get("cutover_model", {})
    for key, expected in (
        ("SHADOW_GRAPH_MATERIALIZATION_BEFORE_CUTOVER", "allowed_non_authoritative"),
        ("AUTHORITATIVE_LIFECYCLE_GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
        ("SHADOW_GRAPH_IS_LIFECYCLE_AUTHORITY", "no"),
        ("SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "no"),
        ("SHADOW_GRAPH_CAN_ACCEPT_RUNTIME_LIFECYCLE_MUTATION", "no"),
    ):
        _check(results, proof_cutover.get(key) == expected, f"integrated cutover {key}={expected}")

    gate = repair.get("m3_b_gate_model", {})
    source_gate = gate.get("M3_B_SOURCE_CONSTRUCTION_GATE", {})
    shadow_gate = gate.get("M3_B_SHADOW_MATERIALIZATION_GATE", {})
    authoritative_gate = gate.get("M3_B_AUTHORITATIVE_WRITE_GATE", {})
    _check(results, source_gate.get("M3_B_SOURCE_CONSTRUCTION_REQUIRES_CUTOVER") == "no",
           "source construction does not require cutover")
    _check(results, source_gate.get("M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES") == "no",
           "M3-A PASS does not authorize graph writes")
    _check(results, shadow_gate.get("M3_B_SHADOW_MATERIALIZATION_IS_AUTHORITY") == "no",
           "shadow materialization is not authority")
    _check(results, authoritative_gate.get("M3_B_AUTHORITATIVE_WRITE_REQUIRES_CUTOVER") == "yes",
           "authoritative write requires cutover")
    _check(results, gate.get("M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES") == "no",
           "top-level M3-A PASS gate remains no")
    _check(results, gate.get("M3_B_AUTHORITATIVE_WRITE_REQUIRES_CUTOVER") == "yes",
           "top-level authoritative write cutover requirement is yes")

    integrated_gate = proof.get("m3_b_authoritative_write_gate", {})
    _check(results, integrated_gate.get("gate_scope") == "AUTHORITATIVE_RUNTIME_LIFECYCLE_GRAPH_MUTATION",
           "integrated gate scope is authoritative runtime lifecycle graph mutation")
    for key, expected in REQUIRED_GATE_CONDITIONS.items():
        _check(results, integrated_gate.get(key) == expected,
               f"integrated gate direct condition {key}={expected}")
        _check(results, integrated_gate.get("required_conditions", {}).get(key) == expected,
               f"integrated gate required condition {key}={expected}")
        _check(results, authoritative_gate.get("required_conditions", {}).get(key) == expected,
               f"repair gate required condition {key}={expected}")
    _check(results, integrated_gate.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
           "integrated authoritative graph writes remain denied")
    _check(results, integrated_gate.get("AUTHORITATIVE_RUNTIME_LIFECYCLE_GRAPH_WRITES_ALLOWED") == "no",
           "integrated runtime lifecycle graph writes remain denied")

    nf2 = proof.get("nf2_disposition", {})
    i9 = proof.get("i9_b008_disposition", {})
    _check(results, nf2.get("NF2_SOURCE_FIXED") == "no", "NF2 implementation remains a prerequisite")
    _check(results, i9.get("I9_B008_SOURCE_FIXED") == "no", "I9-B008 implementation remains a prerequisite")
    _check(results, i9.get("I9_B008_M3B_ORDERING") == "early_prerequisite",
           "I9-B008 early ordering is preserved")
    _check(results, gate.get("M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES") == "no",
           "M3-A pass cannot authorize graph writes")


def _check_dag(results: list[tuple[bool, str]], repair: dict, proof: dict) -> None:
    items = proof.get("m3_b_construction_map", [])
    by_id = {_work_item_id(item.get("WORK_ITEM", "")): item for item in items}
    required_ids = {f"W-M3B-{number:02d}" for number in range(1, 16)}
    _check(results, required_ids <= set(by_id), "M3-B construction map has W-M3B-01 through W-M3B-15")
    for work_id in required_ids:
        item = by_id.get(work_id, {})
        for field in ("WORK_ITEM", "DEPENDS_ON", "SOURCE_OWNERSHIP", "MUTATION_CLASS",
                      "GATE_STAGE", "AUTHORITATIVE_WRITES_ALLOWED", "REGRESSION_CLASSES",
                      "ACCEPTANCE", "PARALLEL_SAFE_WITH"):
            _check(results, field in item, f"{work_id} declares {field}")

    expected_edges = set()
    for work_id, item in by_id.items():
        for dependency in item.get("DEPENDS_ON", []):
            expected_edges.add((dependency.split(" ", 1)[0], work_id))
    dag = proof.get("m3_b_dependency_dag", {})
    edges = {tuple(edge) for edge in dag.get("edges", []) if isinstance(edge, list) and len(edge) == 2}
    _check(results, set(by_id) <= set(dag.get("nodes", [])), "DAG nodes cover every construction-map work item")
    _check(results, expected_edges == edges, "DAG edges exactly materialize construction-map dependencies")
    _check(results, ("W-M3B-13", "W-M3B-14") in edges,
           "W-M3B-13 -> W-M3B-14 dependency is materialized")
    for edge in (
        ("W-M3B-14", "W-M3B-15"),
        ("W-M3B-15", "W-M3B-09"),
        ("W-M3B-15", "W-M3B-10"),
        ("W-M3B-07", "W-M3B-13"),
        ("W-M3B-13", "W-M3B-08"),
    ):
        _check(results, edge in edges, f"DAG contains staged edge {edge[0]} -> {edge[1]}")

    indegree = {node: 0 for node in dag.get("nodes", [])}
    adjacency = {node: [] for node in dag.get("nodes", [])}
    for source, target in edges:
        if source in adjacency and target in indegree:
            adjacency[source].append(target)
            indegree[target] += 1
    queue = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for child in adjacency.get(node, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    _check(results, visited == len(indegree), "M3-B dependency DAG is acyclic")

    _check(results, by_id.get("W-M3B-07", {}).get("GATE_STAGE") == "M3_B_SHADOW_MATERIALIZATION_GATE",
           "bootstrap/shadow work is behind the shadow materialization gate")
    _check(results, by_id.get("W-M3B-13", {}).get("GATE_STAGE") == "SHADOW_VALIDATION",
           "behavioral work is shadow validation")
    _check(results, by_id.get("W-M3B-08", {}).get("GATE_STAGE") == "CUTOVER_GATE",
           "cutover has an explicit gate stage")
    _check(results, by_id.get("W-M3B-15", {}).get("GATE_STAGE") == "POST_CUTOVER_RUNTIME",
           "authoritative runtime mutation is post-cutover")
    _check(results, "no authoritative" in by_id.get("W-M3B-07", {}).get("MUTATION_CLASS", ""),
           "shadow materialization is explicitly non-authoritative")

    repairs = repair.get("m3_b_dag_repairs", [])
    _check(results, any(entry.get("required_dependency_edge") == ["W-M3B-13", "W-M3B-14"]
                        and entry.get("materialized") == "yes" for entry in repairs),
           "repair artifact records W-M3B-13 -> W-M3B-14 materialization")


def _check_amendments(results: list[tuple[bool, str]], a12: dict, a5: dict) -> None:
    for artifact, name in ((a12, "A12"), (a5, "A5")):
        amendment = artifact.get("contract_repair_amendment", {})
        _check(results, amendment.get("repair_id") == "M3-A-R1", f"{name} amendment identifies M3-A-R1")
        _check(results, amendment.get("REPAIR_REASON") == "independent_review_blocker",
               f"{name} amendment records independent_review_blocker")
        _check(results, bool(amendment.get("ORIGINAL_ASSERTION")), f"{name} preserves ORIGINAL_ASSERTION")
        _check(results, bool(amendment.get("REPAIRED_ASSERTION")), f"{name} records REPAIRED_ASSERTION")
    target_map = a12.get("cross_contract_rules", {}).get("C_EXECUTION_TO_AUTHORITY_TARGET", {}).get("typed_target_map", {})
    for key in ("Subject", "Workflow", "Execution", "Completion", "Decision"):
        _check(results, key in target_map, f"A12 typed target map includes {key}")
    for key in ("Execution", "Completion", "Decision"):
        _check(results, "not an independent mutation revision root" in target_map.get(key, ""),
               f"A12 {key} is not an independent mutation revision root")


def _check_acceptance(results: list[tuple[bool, str]], repair: dict, proof: dict) -> None:
    for artifact, name in ((repair, "repair"), (proof, "integrated proof")):
        acceptance = artifact.get("acceptance", {})
        _check(results, acceptance.get("BLOCKING_REVIEW_FINDING_COUNT_BEFORE") == 4,
               f"{name} records four findings before repair")
        _check(results, acceptance.get("BLOCKING_REVIEW_FINDING_COUNT_AFTER") == 0,
               f"{name} records zero findings after repair")
        for key, expected in (
            ("AUTHORITY_REVISION_TARGET_CONSISTENT", "yes"),
            ("FOLLOWUP_LEASE_SCOPE_FROZEN", "yes"),
            ("COMPLETION_BINDING_SCHEMA_CONSISTENT", "yes"),
            ("AUTHORITATIVE_WRITE_GATE_CUTOVER_SAFE", "yes"),
            ("NO_DUAL_SUBJECT_AUTHORITY", "yes"),
            ("M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION", "no"),
            ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
        ):
            _check(results, acceptance.get(key) == expected, f"{name} acceptance {key}={expected}")
    _check(results, proof.get("invariants", {}).get("BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT") == 0,
           "integrated proof has zero blocking unresolved semantic questions")
    triage = proof.get("open_question_triage", [])
    for finding_id in ("M3-A-R1-B-R1-01", "M3-A-R1-B-R1-02", "M3-A-R1-B-R1-03", "M3-A-R1-B-R1-04"):
        _check(results, any(row.get("QUESTION_ID") == finding_id
                            and row.get("CLASSIFICATION") == "RESOLVED_BY_M3_A" for row in triage),
               f"triage closes {finding_id}")
    _check(results, proof.get("report_metrics", {}).get("m3_b_work_item_count") == 15,
           "integrated proof reports 15 M3-B work items")


def _check_closure_and_manual_challenge(results: list[tuple[bool, str]], repair: dict) -> None:
    rows = repair.get("blocking_findings", [])
    observed = {row.get("finding_id"): row.get("blocker") for row in rows}
    _check(results, observed == EXPECTED_BLOCKERS, "blocking findings map exactly to the four repair blockers")
    _check(results, all(row.get("disposition") == "resolved" for row in rows),
           "all four blocking findings are resolved")
    matrix = repair.get("blocker_closure_matrix", [])
    _check(results, len(matrix) == 4 and all(row.get("DISPOSITION") == "resolved" for row in matrix),
           "blocker closure matrix has four resolved entries")
    challenge = repair.get("manual_four_blocker_challenge", {})
    for key in (
        "Q1_execution_id_bypass_owning_subject_revision_cas",
        "Q2_parent_followup_lease_reused_for_post_commit_child_mutation",
        "Q3_completion_requires_absent_direct_subject_ref",
        "Q4_authoritative_runtime_graph_write_before_cutover",
    ):
        _check(results, challenge.get(key) == "no", f"manual challenge {key}=no")
    _check(results, repair.get("invariants", {}).get("SOURCE_CODE_CHANGED") == "no",
           "repair invariant SOURCE_CODE_CHANGED=no")
    _check(results, repair.get("invariants", {}).get("M3_B_IMPLEMENTATION_STARTED") == "no",
           "repair invariant M3_B_IMPLEMENTATION_STARTED=no")
    _check(results, repair.get("invariants", {}).get("AUTHORITATIVE_GRAPH_WRITES_PERFORMED") == "no",
           "repair invariant AUTHORITATIVE_GRAPH_WRITES_PERFORMED=no")


def _check_source_boundary(results: list[tuple[bool, str]], repo_root: Path) -> None:
    ok, changed = _changed_paths(repo_root)
    _check(results, ok, "git changed-path inspection succeeds")
    if not ok:
        return
    _check(results, bool(changed), "repair worktree has changed evidence/guard paths")
    _check(results, changed <= ALLOWED_CHANGED_PATHS,
           f"repair changed paths are bounded to contract/evidence guards: {sorted(changed)}")
    forbidden = sorted(path for path in changed if path.startswith(FORBIDDEN_SOURCE_PREFIXES))
    _check(results, not forbidden, f"no core/adapters source paths changed: {forbidden}")


def run_checks(repair: dict, proof: dict, repo_root: Path, a1: dict, a5: dict, a12: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for key, expected in (
        ("schema_version", 1),
        ("project_id", "aota_forge"),
        ("milestone", "M3"),
        ("phase", "M3-A"),
        ("repair_id", "M3-A-R1"),
        ("review_subject", REPAIR_BASE),
        ("repair_base", REPAIR_BASE),
    ):
        _check(results, repair.get(key) == expected, f"repair top-level {key}={expected!r}")
    _check(results, proof.get("invariants", {}).get("BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT") == 0,
           "integrated proof blocking unresolved semantic question count is zero")
    _check_authority(results, repair, proof)
    _check_followup(results, repair, proof)
    _check_completion(results, repair, proof, a1, a5)
    _check_cutover_and_gate(results, repair, proof)
    _check_dag(results, repair, proof)
    _check_amendments(results, a12, a5)
    _check_acceptance(results, repair, proof)
    _check_closure_and_manual_challenge(results, repair)
    _check_source_boundary(results, repo_root)
    return results


NEGATIVE_MUTATIONS = (
    (
        "EXECUTION_INDEPENDENT_MUTATION_REVISION_ROOT=yes",
        lambda p: p["repair_decisions"]["authority_target"].__setitem__(
            "EXECUTION_INDEPENDENT_MUTATION_REVISION_ROOT", "yes"
        ),
    ),
    (
        "FOLLOWUP_CREATION_REQUIRES_PREEXISTING_CHILD_LEASE=yes_without_two_root_contract",
        lambda p: (
            p["repair_decisions"]["followup_creation_lease"].__setitem__(
                "FOLLOWUP_CREATION_REQUIRES_PREEXISTING_CHILD_LEASE", "yes"
            ),
            p["repair_decisions"]["followup_creation_lease"].pop("transaction_shape", None),
        ),
    ),
    (
        "PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY=yes",
        lambda p: p["repair_decisions"]["followup_creation_lease"].__setitem__(
            "PARENT_LEASE_GRANTS_POST_COMMIT_CHILD_AUTHORITY", "yes"
        ),
    ),
    (
        "COMPLETION_DIRECT_SUBJECT_REF_REQUIRED=yes",
        lambda p: p["repair_decisions"]["completion_binding"].__setitem__(
            "COMPLETION_DIRECT_SUBJECT_REF_REQUIRED", "yes"
        ),
    ),
    (
        "SHADOW_GRAPH_IS_LIFECYCLE_AUTHORITY=yes",
        lambda p: p["repair_decisions"]["write_gate_cutover"].__setitem__(
            "SHADOW_GRAPH_IS_LIFECYCLE_AUTHORITY", "yes"
        ),
    ),
    (
        "AUTHORITATIVE_LIFECYCLE_GRAPH_WRITE_BEFORE_CUTOVER=allowed",
        lambda p: p["repair_decisions"]["write_gate_cutover"].__setitem__(
            "AUTHORITATIVE_LIFECYCLE_GRAPH_WRITE_BEFORE_CUTOVER", "allowed"
        ),
    ),
    (
        "M3_B_AUTHORITATIVE_WRITE_REQUIRES_CUTOVER=no",
        lambda p: p["m3_b_gate_model"]["M3_B_AUTHORITATIVE_WRITE_GATE"].__setitem__(
            "M3_B_AUTHORITATIVE_WRITE_REQUIRES_CUTOVER", "no"
        ),
    ),
    (
        "M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES=yes",
        lambda p: p["m3_b_gate_model"].__setitem__(
            "M3_A_PASS_AUTOMATICALLY_AUTHORIZES_GRAPH_WRITES", "yes"
        ),
    ),
)


def run_self_test(repo_root: Path, proof: dict, a1: dict, a5: dict, a12: dict) -> dict:
    base = _read_json(repo_root / REPAIR_REL)
    positive = run_checks(base, proof, repo_root, a1, a5, a12)
    positive_passes = all(ok for ok, _ in positive)
    mutations = []
    with tempfile.TemporaryDirectory() as directory:
        for label, mutate in NEGATIVE_MUTATIONS:
            candidate = json.loads(json.dumps(base))
            mutate(candidate)
            path = Path(directory) / "mutated-repair.json"
            path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(repo_root / GUARD_REL), "--artifact", str(path), "--json"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=240,
            )
            rejected = proc.returncode != 0
            mutations.append({
                "mutation": label,
                "rejected": rejected,
                "result": "PASS" if rejected else "FAIL",
            })
    verdict = "PASS" if positive_passes and all(row["rejected"] for row in mutations) else "FAIL"
    return {
        "positive_control_passes": positive_passes,
        "negative_mutation_count": len(mutations),
        "mutations": mutations,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A-R1 contract repair guard")
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    parser.add_argument("--self-test", action="store_true", help="run eight isolated negative mutations")
    parser.add_argument("--artifact", type=Path, help="repair artifact path")
    parser.add_argument("--repo-root", type=Path, help="repository root")
    args = parser.parse_args()

    repo_root = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    repair_path = (args.artifact or repo_root / REPAIR_REL).resolve()
    try:
        repair = _read_json(repair_path)
        proof = _read_json(repo_root / PROOF_REL)
        a1 = _read_json(repo_root / A1_REL)
        a5 = _read_json(repo_root / A5_REL)
        a12 = _read_json(repo_root / A12_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact/input unreadable: {exc}")
        return 1

    negative = run_self_test(repo_root, proof, a1, a5, a12) if args.self_test else None
    results = run_checks(repair, proof, repo_root, a1, a5, a12)
    if negative is not None:
        _check(results, negative["verdict"] == "PASS",
               f"M3_A_R1_NEGATIVE_GUARD_PROOF={negative['verdict']}")
    passed = sum(1 for ok, _ in results if ok)
    verdict = "PASS" if passed == len(results) else "FAIL"
    summary = {
        "artifact": "m3-a-r1-contract-repair",
        "repair_base": REPAIR_BASE,
        "check_total": len(results),
        "check_passed": passed,
        "verdict": verdict,
        "M3_A_R1_GUARD": verdict,
        "M3_A_R1_NEGATIVE_GUARD_PROOF": "NOT_RUN" if negative is None else negative["verdict"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if verdict == "PASS" else 1

    print(f"artifact: {repair_path.relative_to(repo_root) if repair_path.is_relative_to(repo_root) else repair_path}")
    print(f"repair base: {REPAIR_BASE}")
    print(f"checks: {passed}/{len(results)} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if negative is not None:
        print(f"M3_A_R1_NEGATIVE_GUARD_PROOF: {negative['verdict']}")
        for mutation in negative["mutations"]:
            print(f"  {'PASS' if mutation['rejected'] else 'FAIL'}  reject {mutation['mutation']}")
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
