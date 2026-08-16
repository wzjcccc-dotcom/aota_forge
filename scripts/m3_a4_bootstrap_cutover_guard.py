#!/usr/bin/env python3
"""M3-A4 bootstrap / migration / cutover / projection design guard.

This guard verifies the A4 design proof at the exact M3-A3 handoff commit.  It
does not materialize a graph, write authority, migrate data, publish a
projection, or perform a cutover.  It checks the A0 classifications and the
accepted A1/A2/A12/A3 contracts, then checks that A4 freezes:

* deterministic bootstrap candidates with no evidence-source majority vote;
* conservative legacy mappings and explicit identity ambiguity outcomes;
* complete candidate validation and bounded unmigratable state;
* read-only shadow comparison and a serialized, one-way authority flip;
* canonical-only projection rebuild after a successful graph commit; and
* design-only regression proofs and machine-readable A4-S1..A4-S7 scenarios.

The guard also rejects the six required negative mutations through
``--self-test``.  Exit status is 0 only when every contract check passes.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path


EXPECTED_BASE_SHA = "2485a8d51b6fb5722a0da02e9dc93e5615401277"
A0_COMMIT = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A1_COMMIT = "7dbc2225e0b66f6bf9e6d0970581d96f428d4362"
A2_COMMIT = "b29fe5f6fa47035cdeb4137cd62aafc585257b40"
A12_COMMIT = "8dad2d3ceeaaf093f35e39daa5c12049c0505527"
A3_COMMIT = EXPECTED_BASE_SHA
A0_COMMON_BASE = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

A4_REL = Path("deploy/evidence/issues/9/m3-a/a4-bootstrap-cutover.json")
GUARD_REL = Path("scripts/m3_a4_bootstrap_cutover_guard.py")
A0_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A1_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
A2_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
A12_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
A3_REL = Path("deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

ALLOWED_CHANGED_PATHS = frozenset({str(A4_REL), str(GUARD_REL)})
FORBIDDEN_SOURCE_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

REQUIRED_TOP_LEVEL = (
    "schema_version",
    "artifact",
    "project_id",
    "milestone",
    "phase",
    "lane",
    "governing_plan_issue",
    "canonical_source_repository",
    "base_sha",
    "common_base",
    "inputs",
    "input_commits",
    "design_proof_only",
    "bootstrap_input_contract",
    "migration_mapping",
    "migration_identity_rule",
    "candidate_validation",
    "unmigratable_state_rule",
    "shadow_read_model",
    "cutover_rule",
    "cutover_concurrency",
    "cutover_failure_model",
    "projection_rebuild_rule",
    "legacy_pointer_transition",
    "governance_evidence_boundary",
    "contract_handoff",
    "regression_design_proofs",
    "design_scenarios",
    "deferred_questions",
    "invariants",
    "implementation_boundary",
    "report_metrics",
)

REQUIRED_A0_CATEGORIES = frozenset(
    {
        "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE",
        "MIGRATION_EVIDENCE_ONLY",
        "PROJECTION_ONLY",
        "HISTORICAL_ONLY",
        "FORBIDDEN_AUTHORITY_SOURCE",
    }
)

SOURCE_CLASS_FIELDS = (
    "SOURCE_CLASS",
    "SOURCE_IDS",
    "PRIMARY_CLASSIFICATION",
    "CAN_SUPPLY_IDENTITY_FACTS",
    "CAN_SUPPLY_LINEAGE_FACTS",
    "CAN_SUPPLY_STATE_FACTS",
    "REQUIRES_CROSS_VALIDATION",
    "CAN_INDEPENDENTLY_ESTABLISH_GRAPH_TRUTH",
    "CONFLICT_BEHAVIOR",
)

MAPPING_OUTCOMES = {
    "CANONICAL_RECORD_CANDIDATE",
    "CANONICAL_METADATA_ONLY",
    "PROJECTION_ONLY",
    "MIGRATION_EVIDENCE_ONLY",
    "UNMIGRATABLE_WITHOUT_SEMANTIC_CHOICE",
    "IGNORED_FOR_CANONICAL_GRAPH",
}

REQUIRED_MAPPING_CONCEPTS = {
    "legacy Work Item",
    "Profile Task",
    "SPEC",
    "completion",
    "decision",
    "followup",
    "handoff",
    "project binding",
    "project decision",
    "legacy current_* pointers",
    "orchestration lineage",
}

EXPECTED_MAPPING_OUTCOMES = {
    "legacy Work Item": "MIGRATION_EVIDENCE_ONLY",
    "Profile Task": "MIGRATION_EVIDENCE_ONLY",
    "SPEC": "CANONICAL_METADATA_ONLY",
    "completion": "CANONICAL_RECORD_CANDIDATE",
    "decision": "CANONICAL_RECORD_CANDIDATE",
    "followup": "UNMIGRATABLE_WITHOUT_SEMANTIC_CHOICE",
    "handoff": "IGNORED_FOR_CANONICAL_GRAPH",
    "project binding": "CANONICAL_RECORD_CANDIDATE",
    "project decision": "CANONICAL_RECORD_CANDIDATE",
    "legacy current_* pointers": "PROJECTION_ONLY",
    "orchestration lineage": "MIGRATION_EVIDENCE_ONLY",
}

VALIDATION_GATES = (
    "schema_validity",
    "identity_uniqueness",
    "lineage_consistency",
    "no_forbidden_cycles",
    "revision_initialization",
    "decision_followup_basis_consistency",
    "no_orphan_records",
    "no_duplicate_semantic_effect",
    "provenance_completeness",
    "conflict_resolution_state",
)

POINTER_NAMES = (
    "plan.active_plan_id",
    "Portable Plan projection fields",
    "legacy session current_* pointers",
    "legacy active plan binding",
    "legacy active frozen spec binding",
    "legacy plan.json active pointers",
)

REGRESSION_PROOF_CLASSES = {
    "B011",
    "B013",
    "B014",
    "B014-F",
    "B014-F1",
    "ACTIVATE-R-current-binding",
    "WCTX-1",
    "BIND-1",
    "RECOVERY-1",
}

SCENARIO_NAMES = {
    "A4-S1": "clean deterministic migration",
    "A4-S2": "conflicting legacy evidence",
    "A4-S3": "stale current pointer with valid durable Subject evidence",
    "A4-S4": "projection rebuild after graph authoritative commit",
    "A4-S5": "cutover interrupted before authority flip",
    "A4-S6": "cutover succeeds but projection publish fails",
    "A4-S7": "forbidden authority evidence attempts to define graph truth",
}

FROZEN_AUTHORITY_FLAGS = {
    "DURABLE_SUBJECT_GRAPH_IS_AUTHORITY": "yes",
    "CURRENT_POINTERS_ARE_PROJECTIONS": "yes",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "LEGACY_GRAPH_INPUT_IS_AUTHORITY": "no",
    "GIT_HISTORY_IS_SUBJECT_AUTHORITY": "no",
    "EVENT_LOG_IS_SUBJECT_AUTHORITY": "no",
    "CONTROL_COMMENT_IS_SUBJECT_AUTHORITY": "no",
    "DUAL_AUTHORITATIVE_WRITE": "no",
    "GRAPH_BOOTSTRAP_STRATEGY_REQUIRED": "yes",
    "GRAPH_CUTOVER_RULE_REQUIRED": "yes",
    "SHADOW_READ_ALLOWED": "yes",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    "PROFILE_TASK_PRIVATE_ID_NOT_CANONICAL_SUBJECT_ID": "yes",
    "LEGACY_CURRENT_POINTER_NOT_CANONICAL_IDENTITY": "yes",
    "HEURISTIC_IDENTITY_MERGE_ALLOWED": "no",
    "ONE_CUTOVER_AUTHORITY": "yes",
    "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY": "no",
    "CUTOVER_SERIALIZATION_ALLOWED": "yes",
    "GLOBAL_PROCESS_LOCK_IS_NORMAL_MUTATION_MODEL": "no",
    "LEGACY_WRITE_DURING_CUTOVER": "blocked_or_serialized",
    "GRAPH_WRITE_BEFORE_CUTOVER": "denied",
    "CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY": "yes",
    "PROJECTION_REBUILD_SOURCE": "canonical_authority_only",
    "PROJECTION_REBUILD_MUTATES_GRAPH": "no",
    "STALE_PROJECTION_MAY_BE_REBUILT": "yes",
    "STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT": "yes",
    "PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY": "yes",
    "AMBIGUOUS_MIGRATION_IDENTITY": "NEEDS_SEMANTIC_CHOICE",
    "UNMIGRATABLE_STATE_DOES_NOT_BLOCK_SAFE_READONLY_DIAGNOSIS": "yes",
}

A3_HANDOFF_FLAGS = {
    "CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT": "yes",
    "CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER": "yes",
    "NO_CAS_BYPASS_FOR_NORMAL_MUTATION": "yes",
    "GLOBAL_PROCESS_MUTEX_CANONICAL": "no",
    "GLOBAL_PROCESS_LOCK_IS_CANONICAL_CONCURRENCY_MODEL": "no",
    "CANONICAL_COMMIT_SUCCEEDS_IF_PROJECTION_PUBLICATION_FAILS": "yes",
    "PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY": "yes",
}

ENGINE_FLAGS = {
    "NO_STORAGE_ENGINE_FROZEN": "yes",
    "NO_GRAPH_MUTATION_IMPLEMENTATION": "yes",
    "NO_AUTHORITY_ENGINE": "yes",
    "NO_LEASE_ENGINE": "yes",
    "NO_CAS_ENGINE": "yes",
    "NO_TRANSACTION_ENGINE": "yes",
    "NO_CUTOVER_RUNTIME": "yes",
    "NO_PROJECTION_REBUILD_RUNTIME": "yes",
    "NO_RUNTIME_BEHAVIOR": "yes",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    "SOURCE_CODE_CHANGED": "no",
}

DESIGN_ONLY_FLAGS = {
    "no_graph_storage_engine": "yes",
    "no_graph_mutation_implementation": "yes",
    "no_authoritative_graph_writes": "yes",
    "no_authority_engine": "yes",
    "no_lease_engine": "yes",
    "no_cas_engine": "yes",
    "no_transaction_engine": "yes",
    "no_cutover_runtime": "yes",
    "no_projection_rebuild_runtime": "yes",
    "no_runtime_behavior": "yes",
}

IMPL_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease|Mutation|Transaction|Revision|Cas|CAS)\w*"
    r"(?:Store|Repository|Engine|Broker|Manager|Service|Evaluator|Issuer|Ledger|Vault)\w*\s*[:(]"
)
IMPL_FUNC_RE = re.compile(
    r"def\s+(?:evaluate_authority|authorize_mutation|issue_lease|consume_lease|"
    r"revoke_lease|renew_lease|acquire_lease|validate_lease|grant_lease|"
    r"apply_mutation|perform_mutation|commit_mutation|execute_mutation|write_graph|"
    r"create_subject|transition_subject|cas_commit|compare_and_set|"
    r"begin_transaction|commit_transaction|rollback_transaction|cutover|"
    r"rebuild_projection)\s*\("
)
IMPL_OP_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease|mutation|transaction|revision|cutover)\.[a-z0-9_.-]+"'
)
IMPL_IMPORT_RE = re.compile(
    r"(?:from|import)\s+aota_forge\.(?:core|adapters)\."
    r"(?:authority|lease|mutation|graph|transaction|revision|cas|cutover|projection)\b"
)
READ_ONLY_GATE_MARKER = "operation is not read-only in M2"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _plain(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _dict_get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def _changed_paths(repo_root: Path) -> tuple[bool, set[str]]:
    rc_diff, diff = _git(repo_root, ["diff", "--name-only", EXPECTED_BASE_SHA])
    rc_status, status = _git(repo_root, ["status", "--porcelain"])
    if rc_diff != 0 or rc_status != 0:
        return False, set()
    changed = set(line for line in diff.splitlines() if line)
    for line in status.splitlines():
        if len(line) >= 4:
            path = line[3:].strip().strip('"')
            if path and "__pycache__" not in path and not path.endswith(".pyc"):
                changed.add(path)
    return True, changed


def _scan_source(repo_root: Path) -> list[str]:
    package = repo_root / "aota_forge"
    if not package.is_dir():
        return ["aota_forge package not found"]
    hits: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(repo_root))
        for label, matcher in (
            ("class", IMPL_CLASS_RE),
            ("function", IMPL_FUNC_RE),
            ("import", IMPL_IMPORT_RE),
        ):
            hits.extend(f"{rel}:{label} {match.group(0)}" for match in matcher.finditer(text))
    catalog = package / "core" / "catalog.py"
    if catalog.is_file():
        text = catalog.read_text(encoding="utf-8", errors="replace")
        hits.extend(f"aota_forge/core/catalog.py:operation {match.group(0)}"
                    for match in IMPL_OP_RE.finditer(text))
    return hits


def _a0_inputs(a0: dict) -> dict[str, dict]:
    return {item.get("id"): item for item in a0.get("migration_inputs", [])
            if isinstance(item, dict) and item.get("id")}


def _proof_by_class(artifact: dict) -> dict[str, dict]:
    return {item.get("FAILURE_CLASS"): item
            for item in artifact.get("regression_design_proofs", [])
            if isinstance(item, dict)}


def run_checks(
    artifact: dict,
    repo_root: Path,
    a0: dict,
    a1: dict,
    a2: dict,
    a12: dict,
    a3: dict,
    matrix: dict,
) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Exact artifact identity, source inputs and design-only boundary.
    for key in REQUIRED_TOP_LEVEL:
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    for key, expected in (
        ("schema_version", 1),
        ("project_id", "aota_forge"),
        ("milestone", "M3"),
        ("phase", "M3-A"),
        ("lane", "M3-A4"),
        ("governing_plan_issue", 9),
        ("canonical_source_repository", "wzjcccc-dotcom/aota_forge"),
        ("base_sha", EXPECTED_BASE_SHA),
        ("common_base", EXPECTED_BASE_SHA),
        ("m3_a_common_base", EXPECTED_BASE_SHA),
    ):
        results.append(_check(artifact.get(key) == expected,
                              f"{key} must be {expected!r} (got {artifact.get(key)!r})"))

    expected_inputs = {
        "a0": str(A0_REL), "a1": str(A1_REL), "a2": str(A2_REL),
        "a12": str(A12_REL), "a3": str(A3_REL), "matrix": str(MATRIX_REL),
    }
    for key, expected in expected_inputs.items():
        results.append(_check(artifact.get("inputs", {}).get(key) == expected,
                              f"inputs.{key} must be {expected!r}"))
    expected_commits = {
        "a0": A0_COMMIT, "a1": A1_COMMIT, "a2": A2_COMMIT,
        "a12": A12_COMMIT, "a3": A3_COMMIT,
    }
    for key, expected in expected_commits.items():
        results.append(_check(artifact.get("input_commits", {}).get(key) == expected,
                              f"input_commits.{key} must be {expected!r}"))

    design_only = artifact.get("design_proof_only", {})
    for key, expected in DESIGN_ONLY_FLAGS.items():
        results.append(_check(design_only.get(key) == expected,
                              f"design_proof_only.{key} must be {expected!r}"))
    results.append(_check(design_only.get("source_mutation_scope") == "design_artifacts_only",
                          "design_proof_only.source_mutation_scope must be design_artifacts_only"))

    # 2. A0 classification preservation and candidate source contract.
    categories = set(a0.get("classification_categories", []))
    results.append(_check(categories == REQUIRED_A0_CATEGORIES,
                          "A0 classification_categories must contain exactly all five frozen classes"))
    a0_inputs = _a0_inputs(a0)
    contract = artifact.get("bootstrap_input_contract", {})
    classifications = contract.get("a0_migration_input_classifications", [])
    classification_by_id = {
        item.get("source_id"): item for item in classifications
        if isinstance(item, dict) and item.get("source_id")
    }
    results.append(_check(len(classifications) == len(a0_inputs) == 25,
                          "A4 must classify exactly the 25 A0 migration inputs"))
    results.append(_check(set(classification_by_id) == set(a0_inputs),
                          "A4 migration input IDs must exactly match A0"))
    results.append(_check(len(classification_by_id) == len(classifications),
                          "A4 migration input classification IDs must be unique"))
    for source_id, a0_item in a0_inputs.items():
        row = classification_by_id.get(source_id, {})
        results.append(_check(row.get("PRIMARY_CLASSIFICATION") == a0_item.get("primary_class"),
                              f"A0 classification changed for {source_id}"))
        results.append(_check(_plain(row.get("SOURCE_CLASS")),
                              f"A4 SOURCE_CLASS missing for {source_id}"))

    source_classes = contract.get("source_classes", [])
    results.append(_check(isinstance(source_classes, list) and len(source_classes) == 25,
                          "bootstrap_input_contract.source_classes must contain 25 rows"))
    source_rows: dict[str, dict] = {}
    source_ids: list[str] = []
    for row in source_classes:
        if not isinstance(row, dict):
            results.append(_check(False, "source_classes entries must be objects"))
            continue
        missing = [field for field in SOURCE_CLASS_FIELDS if field not in row]
        results.append(_check(not missing,
                              f"source class {row.get('SOURCE_CLASS', '?')} missing {missing}"))
        source_class = row.get("SOURCE_CLASS")
        results.append(_check(_plain(source_class), "source class name must be non-empty"))
        ids = row.get("SOURCE_IDS", [])
        results.append(_check(isinstance(ids, list) and ids,
                              f"source class {source_class!r} must list SOURCE_IDS"))
        for source_id in ids if isinstance(ids, list) else []:
            source_ids.append(source_id)
            source_rows[source_id] = row
        results.append(_check(row.get("PRIMARY_CLASSIFICATION") in REQUIRED_A0_CATEGORIES,
                              f"source class {source_class!r} has invalid classification"))
        for field in ("CAN_SUPPLY_IDENTITY_FACTS", "CAN_SUPPLY_LINEAGE_FACTS",
                      "CAN_SUPPLY_STATE_FACTS", "REQUIRES_CROSS_VALIDATION",
                      "CAN_INDEPENDENTLY_ESTABLISH_GRAPH_TRUTH"):
            results.append(_check(row.get(field) in ("yes", "no"),
                                  f"source class {source_class!r}.{field} must be yes|no"))
        results.append(_check(_plain(row.get("CONFLICT_BEHAVIOR")),
                              f"source class {source_class!r} must define CONFLICT_BEHAVIOR"))
    results.append(_check(len(source_ids) == len(set(source_ids)) == 25,
                          "source class SOURCE_IDS must cover 25 unique A0 inputs"))
    results.append(_check(set(source_ids) == set(a0_inputs),
                          "source class SOURCE_IDS must exactly match A0 input IDs"))
    for source_id, a0_item in a0_inputs.items():
        row = source_rows.get(source_id, {})
        results.append(_check(row.get("PRIMARY_CLASSIFICATION") == a0_item.get("primary_class"),
                              f"source class classification changed for {source_id}"))
    candidate_ids = {source_id for source_id, item in a0_inputs.items()
                     if item.get("primary_class") == "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE"}
    results.append(_check(candidate_ids == {"MI-14", "MI-15", "MI-18"},
                          "A0 candidate source set must remain MI-14, MI-15, MI-18"))
    for source_id in candidate_ids:
        row = source_rows.get(source_id, {})
        results.append(_check(row.get("REQUIRES_CROSS_VALIDATION") == "yes",
                              f"candidate {source_id} must require cross-validation"))
        results.append(_check(row.get("CAN_INDEPENDENTLY_ESTABLISH_GRAPH_TRUTH") == "no",
                              f"candidate {source_id} cannot independently establish graph truth"))
    for source_id in ("MI-21", "MI-22", "MI-23"):
        row = source_rows.get(source_id, {})
        results.append(_check(row.get("PRIMARY_CLASSIFICATION") == "FORBIDDEN_AUTHORITY_SOURCE",
                              f"forbidden A0 source {source_id} classification changed"))
        results.append(_check("reject" in row.get("CONFLICT_BEHAVIOR", "").lower(),
                              f"forbidden A0 source {source_id} must be rejected as authority"))
    results.append(_check(contract.get("no_majority_vote") == "yes",
                          "bootstrap must forbid majority vote among evidence sources"))
    results.append(_check(contract.get("legacy_evidence_independently_establishes_graph_truth") == "no",
                          "legacy evidence must not independently establish graph truth"))
    results.append(_check(_plain(contract.get("GRAPH_BOOTSTRAP_STRATEGY")),
                          "GRAPH_BOOTSTRAP_STRATEGY must be defined"))
    results.append(_check(isinstance(contract.get("ordering_rule"), list)
                          and len(contract["ordering_rule"]) >= 5,
                          "bootstrap ordering_rule must define the complete dependency order"))

    # 3. Migration mapping and identity allocation.
    mapping = artifact.get("migration_mapping", {})
    results.append(_check(mapping.get("PROFILE_TASK_PRIVATE_ID_NOT_CANONICAL_SUBJECT_ID") == "yes",
                          "Profile Task private ID must not be canonical Subject ID"))
    results.append(_check(mapping.get("LEGACY_CURRENT_POINTER_NOT_CANONICAL_IDENTITY") == "yes",
                          "legacy current pointer must not be canonical identity"))
    results.append(_check(_plain(mapping.get("provenance_rule")),
                          "migration provenance rule must be defined"))
    mappings = mapping.get("mappings", [])
    by_concept = {row.get("LEGACY_CONCEPT"): row for row in mappings if isinstance(row, dict)}
    results.append(_check(set(by_concept) == REQUIRED_MAPPING_CONCEPTS,
                          "migration mapping must cover exactly the required legacy concepts"))
    for concept in REQUIRED_MAPPING_CONCEPTS:
        row = by_concept.get(concept, {})
        for field in ("SOURCE_CLASS", "PRIMARY_CLASSIFICATION", "MAPPING_OUTCOME",
                      "CANONICAL_TARGET", "VALIDATION_RULE", "CONFLICT_BEHAVIOR"):
            results.append(_check(_plain(row.get(field)),
                                  f"mapping {concept} missing {field}"))
        results.append(_check(row.get("MAPPING_OUTCOME") == EXPECTED_MAPPING_OUTCOMES[concept],
                              f"mapping outcome for {concept} is not conservative"))
    results.append(_check(len(mappings) == len(REQUIRED_MAPPING_CONCEPTS),
                          "migration mapping concepts must be unique"))
    identity = artifact.get("migration_identity_rule", {})
    results.append(_check(identity.get("AMBIGUOUS_MIGRATION_IDENTITY") in
                          ("NEEDS_SEMANTIC_CHOICE", "UNMIGRATABLE"),
                          "AMBIGUOUS_MIGRATION_IDENTITY must be NEEDS_SEMANTIC_CHOICE or UNMIGRATABLE"))
    results.append(_check(identity.get("HEURISTIC_IDENTITY_MERGE_ALLOWED") == "no",
                          "HEURISTIC_IDENTITY_MERGE_ALLOWED must be no"))
    deterministic = {row.get("kind") for row in identity.get("deterministic_subjects", [])
                     if isinstance(row, dict)}
    minted = {row.get("kind") for row in identity.get("minted_subjects", [])
              if isinstance(row, dict)}
    results.append(_check(deterministic == {"WorkspaceSubject", "ProjectSubject", "PlanSubject"},
                          "deterministic Subject kinds must match A1"))
    results.append(_check(minted == {"WorkSubject"},
                          "WorkSubject must be the only listed migrated minted kind"))
    identity_text = json.dumps(identity, ensure_ascii=False).lower()
    for forbidden in ("latest", "first", "timestamp proximity", "title similarity",
                      "current pointer", "executor"):
        results.append(_check(forbidden in identity_text,
                              f"identity rule must explicitly reject {forbidden}"))
    results.append(_check("explicit semantic choice" in identity_text,
                          "identity rule must require explicit semantic choice for ambiguous WorkSubject mapping"))
    results.append(_check("old ids remain opaque" in identity.get("old_id_provenance_rule", "").lower(),
                          "old ID provenance must remain opaque"))

    # 4. Candidate validation and unmigratable state.
    validation = artifact.get("candidate_validation", {})
    results.append(_check(_plain(validation.get("GRAPH_CANDIDATE_VALIDATION_RULE")),
                          "GRAPH_CANDIDATE_VALIDATION_RULE must be defined"))
    gates = validation.get("required_gates", [])
    gate_by_name = {gate.get("GATE"): gate for gate in gates if isinstance(gate, dict)}
    results.append(_check(tuple(gate_by_name) == VALIDATION_GATES,
                          "candidate validation gates must be complete and ordered"))
    for name in VALIDATION_GATES:
        gate = gate_by_name.get(name, {})
        for field in ("RULE", "FAILURE"):
            results.append(_check(_plain(gate.get(field)),
                                  f"validation gate {name} missing {field}"))
    results.append(_check("Only a complete validated candidate" in validation.get("cutover_eligibility", ""),
                          "cutover eligibility must require complete validation"))
    unmigratable = artifact.get("unmigratable_state_rule", {})
    results.append(_check(_plain(unmigratable.get("rule")),
                          "unmigratable state rule must be defined"))
    results.append(_check(unmigratable.get("UNMIGRATABLE_STATE_DOES_NOT_BLOCK_SAFE_READONLY_DIAGNOSIS") == "yes",
                          "unmigratable state must not block safe read-only diagnosis"))
    results.append(_check(isinstance(unmigratable.get("allowed_outcomes"), list)
                          and {"NEEDS_SEMANTIC_CHOICE", "UNMIGRATABLE", "QUARANTINED_EVIDENCE", "READONLY_DIAGNOSTIC_ONLY"}
                          <= set(unmigratable["allowed_outcomes"]),
                          "unmigratable outcomes must be explicitly bounded"))
    banned_text = " ".join(str(item) for item in unmigratable.get("forbidden_resolution", []))
    for phrase in ("latest wins", "first match", "timestamp proximity", "title similarity",
                   "blind current-pointer trust", "majority vote", "silent many-to-one reduction"):
        results.append(_check(phrase in banned_text,
                              f"unmigratable rule must forbid {phrase}"))
    results.append(_check("force semantic resolution" in banned_text,
                          "unmigratable rule must not force semantic resolution"))

    # 5. Shadow read, cutover and concurrency.
    shadow = artifact.get("shadow_read_model", {})
    for key, expected in (
        ("SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE", "no"),
        ("SHADOW_GRAPH_CAN_OVERRIDE_LEGACY_AUTHORITY", "no"),
    ):
        results.append(_check(shadow.get(key) == expected,
                              f"shadow_read_model.{key} must be {expected}"))
    results.append(_check(shadow.get("SHADOW_READ_DIVERGENCE") == "bounded evidence, not silent arbitration",
                          "SHADOW_READ_DIVERGENCE must be bounded evidence, not silent arbitration"))
    results.append(_check(_plain(shadow.get("phase")) and "legacy runtime remains" in shadow["phase"],
                          "shadow phase must preserve legacy operational role before flip"))
    dimensions = set(shadow.get("comparison_dimensions", []))
    results.append(_check({"identity", "lineage", "mechanical state", "current projection expectations"} <= dimensions,
                          "shadow reads must compare identity, lineage, state and projection expectations"))

    cutover = artifact.get("cutover_rule", {})
    rule_text = cutover.get("GRAPH_CUTOVER_RULE", "")
    for phrase in ("one-way", "atomic", "sole", "legacy", "Durable Subject Graph"):
        results.append(_check(phrase in rule_text,
                              f"GRAPH_CUTOVER_RULE must state {phrase!r}"))
    results.append(_check(cutover.get("ONE_CUTOVER_AUTHORITY") == "yes",
                          "ONE_CUTOVER_AUTHORITY must be yes"))
    preconditions = cutover.get("CUTOVER_PRECONDITIONS", [])
    results.append(_check(isinstance(preconditions, list) and len(preconditions) >= 9,
                          "CUTOVER_PRECONDITIONS must be explicit and complete"))
    precondition_text = " ".join(preconditions)
    for phrase in ("GRAPH_CANDIDATE_VALIDATION_RULE", "Legacy authoritative writes",
                   "No graph authoritative write", "shadow comparison", "projection rebuild preview"):
        results.append(_check(phrase in precondition_text,
                              f"CUTOVER_PRECONDITIONS must include {phrase!r}"))
    for key in ("CUTOVER_AUTHORITY", "CUTOVER_TRANSACTION_BOUNDARY",
                "CUTOVER_REVISION_INITIALIZATION", "POST_CUTOVER_LEGACY_ROLE",
                "one_way_rule"):
        results.append(_check(_plain(cutover.get(key)), f"cutover_rule.{key} must be defined"))
    results.append(_check(cutover.get("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY") == "no",
                          "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY must be no"))
    concurrency = artifact.get("cutover_concurrency", {})
    for key, expected in (
        ("CUTOVER_SERIALIZATION_ALLOWED", "yes"),
        ("GLOBAL_PROCESS_LOCK_IS_NORMAL_MUTATION_MODEL", "no"),
        ("LEGACY_WRITE_DURING_CUTOVER", "blocked_or_serialized"),
        ("GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
    ):
        results.append(_check(concurrency.get(key) == expected,
                              f"cutover_concurrency.{key} must be {expected!r}"))
    results.append(_check(_plain(concurrency.get("race_rule")),
                          "cutover concurrency race rule must be defined"))
    a3_handoff = concurrency.get("A3_HANDOFF", {})
    for key, expected in A3_HANDOFF_FLAGS.items():
        if key in a3_handoff:
            results.append(_check(a3_handoff.get(key) == expected,
                                  f"cutover_concurrency.A3_HANDOFF.{key} must be {expected!r}"))
    results.append(_check(a3_handoff.get("CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER") == "yes",
                          "A3 handoff must permit CAS bypass only for bootstrap/cutover"))
    results.append(_check(a3_handoff.get("GLOBAL_PROCESS_MUTEX_CANONICAL") == "no",
                          "A3 handoff must reject global process mutex as canonical"))

    failures = artifact.get("cutover_failure_model", {})
    results.append(_check(failures.get("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY") == "yes",
                          "CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY must be yes"))
    failure_stages = failures.get("failure_stages", [])
    stage_ids = [stage.get("STAGE") for stage in failure_stages if isinstance(stage, dict)]
    results.append(_check(stage_ids == ["before_graph_validation", "after_validation_before_authority_flip",
                                        "during_authority_flip", "after_authority_flip_before_projection_rebuild"],
                          "cutover failure stages must cover all four required boundaries"))
    for stage in failure_stages:
        results.append(_check(_plain(stage.get("FAILURE_BEHAVIOR")),
                              f"cutover failure stage {stage.get('STAGE')} lacks FAILURE_BEHAVIOR"))
        results.append(_check(_plain(stage.get("ROLLBACK")),
                              f"cutover failure stage {stage.get('STAGE')} lacks ROLLBACK semantics"))
    results.append(_check(failures.get("rollback_after_authority_flip") == "no",
                          "cutover must not roll back after graph authority flip"))
    results.append(_check(_plain(failures.get("repair_after_authority_flip")),
                          "post-flip repair rule must be defined"))

    # 6. Projection rebuild and legacy pointer retirement.
    projection = artifact.get("projection_rebuild_rule", {})
    for key, expected in (
        ("PROJECTION_REBUILD_SOURCE", "canonical_authority_only"),
        ("PROJECTION_REBUILD_MUTATES_GRAPH", "no"),
        ("STALE_PROJECTION_MAY_BE_REBUILT", "yes"),
        ("STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT", "yes"),
        ("PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY", "yes"),
    ):
        results.append(_check(projection.get(key) == expected,
                              f"projection_rebuild_rule.{key} must be {expected!r}"))
    for key in ("source_rule", "commit_relation", "stale_projection_behavior", "rebuild_idempotency"):
        results.append(_check(_plain(projection.get(key)),
                              f"projection_rebuild_rule.{key} must be defined"))
    projection_names = {item.get("PROJECTION") for item in projection.get("projections", [])
                        if isinstance(item, dict)}
    required_projection_names = {"current Plan pointer", "active Work Item pointer",
                                 "current task index", "current decision index",
                                 "current followup index", "project binding index"}
    results.append(_check(required_projection_names <= projection_names,
                          "projection rebuild must cover all required current_* projections"))
    pointer_transition = artifact.get("legacy_pointer_transition", {})
    results.append(_check(pointer_transition.get("CURRENT_POINTERS_ARE_PROJECTIONS") == "yes",
                          "legacy pointer transition must keep pointers as projections"))
    results.append(_check(pointer_transition.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "legacy pointer transition must deny pointer authority"))
    phases = pointer_transition.get("phase_rules", {})
    for phase in ("PRE_CUTOVER", "CUTOVER", "POST_CUTOVER"):
        row = phases.get(phase, {})
        results.append(_check(isinstance(row, dict), f"legacy pointer phase missing: {phase}"))
        for field in ("read_authority", "write_authority", "projection_status",
                      "rebuildability", "compatibility_adapter"):
            results.append(_check(_plain(row.get(field)),
                                  f"legacy pointer {phase}.{field} must be defined"))
    pointer_rows = pointer_transition.get("pointer_inventory_transition", [])
    results.append(_check([row.get("POINTER") for row in pointer_rows if isinstance(row, dict)]
                          == list(POINTER_NAMES),
                          "legacy pointer transition must cover the exact A0 pointer inventory"))
    for row in pointer_rows:
        for field in ("PRE_CUTOVER", "CUTOVER", "POST_CUTOVER"):
            results.append(_check(_plain(row.get(field)),
                                  f"pointer transition {row.get('POINTER')} missing {field}"))

    # 6b. Governing Plan / Issue / Git evidence remains evidence, not Subject
    #     Graph authority.
    governance = artifact.get("governance_evidence_boundary", {})
    for key, expected in (
        ("ISSUE_BODY_IS_PORTABLE_PLAN_AUTHORITY", "yes"),
        ("ISSUE_BODY_IS_SUBJECT_GRAPH_AUTHORITY", "no"),
        ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
        ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
        ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
    ):
        results.append(_check(governance.get(key) == expected,
                              f"governance_evidence_boundary.{key} must be {expected!r}"))
    for key in ("ISSUE_BODY_ROLE", "GIT_HISTORY_ROLE", "EVENT_LOG_ROLE",
                "CONTROL_COMMENT_ROLE", "PLAN_ISSUE_AND_GIT_EVIDENCE_RULE"):
        results.append(_check(_plain(governance.get(key)),
                              f"governance_evidence_boundary.{key} must be defined"))
    results.append(_check("not Durable Subject Graph authority" in governance.get("ISSUE_BODY_ROLE", ""),
                          "Issue body role must distinguish Plan authority from graph authority"))

    # 7. Regression design proofs and A4 scenarios.
    proofs = artifact.get("regression_design_proofs", [])
    proof_by_class = _proof_by_class(artifact)
    results.append(_check(set(proof_by_class) == REGRESSION_PROOF_CLASSES,
                          "regression_design_proofs must cover the required M3-relevant classes exactly"))
    results.append(_check(len(proofs) == len(proof_by_class),
                          "regression design proof classes must be unique"))
    for failure_class in REGRESSION_PROOF_CLASSES:
        proof = proof_by_class.get(failure_class, {})
        for field in ("FAILURE_CLASS", "A0_DISPOSITION",
                      "A4_BOOTSTRAP_OR_CUTOVER_INVARIANT", "DESIGN_PROOF",
                      "IMPLEMENTATION_REQUIRED", "FUTURE_OWNER",
                      "corpus_disposition_unchanged", "A4_PROOF_STATUS"):
            results.append(_check(_plain(proof.get(field)),
                                  f"proof {failure_class} missing {field}"))
        results.append(_check(proof.get("IMPLEMENTATION_REQUIRED") == "yes",
                              f"proof {failure_class} must not claim implementation"))
        results.append(_check(proof.get("A0_DISPOSITION") == "NOT_YET_IMPLEMENTED",
                              f"proof {failure_class} must preserve A0 disposition"))
        results.append(_check(proof.get("corpus_disposition_unchanged") == "NOT_YET_IMPLEMENTED",
                              f"proof {failure_class} must preserve matrix disposition"))
        results.append(_check(proof.get("A4_PROOF_STATUS") == "DESIGN_PROVEN_ONLY",
                              f"proof {failure_class} must be design-only"))
    matrix_classes = {entry.get("failure_class"): entry for entry in matrix.get("failure_classes", [])
                      if isinstance(entry, dict)}
    results.append(_check(len(matrix_classes) == 16, "M2 matrix must retain 16 failure classes"))
    for failure_class in REGRESSION_PROOF_CLASSES:
        results.append(_check(matrix_classes.get(failure_class, {}).get("expected_successor_disposition")
                              == "NOT_YET_IMPLEMENTED",
                              f"matrix disposition for {failure_class} must remain NOT_YET_IMPLEMENTED"))
    scenarios = artifact.get("design_scenarios", [])
    scenario_ids = [item.get("SCENARIO_ID") for item in scenarios if isinstance(item, dict)]
    results.append(_check(scenario_ids == list(SCENARIO_NAMES),
                          "design_scenarios must be exactly A4-S1 through A4-S7"))
    for scenario_id, expected_name in SCENARIO_NAMES.items():
        item = next((row for row in scenarios if isinstance(row, dict)
                     and row.get("SCENARIO_ID") == scenario_id), {})
        results.append(_check(item.get("NAME") == expected_name,
                              f"{scenario_id} NAME must be {expected_name!r}"))
        for field in ("given", "when", "expected", "INVARIANTS"):
            results.append(_check(bool(item.get(field)),
                                  f"scenario {scenario_id} must define {field}"))
    scenario_text = json.dumps(scenarios, ensure_ascii=False)
    for phrase in ("no heuristic merge", "semantic choice", "pointer does not hide",
                   "graph unchanged", "legacy remains the sole", "graph remains sole authority",
                   "forbidden authority"):
        results.append(_check(phrase.lower() in scenario_text.lower(),
                              f"design scenarios must cover {phrase!r}"))

    # 8. Frozen flags and implementation boundary.
    frozen = artifact.get("invariants", {}).get("frozen_authority_flags", {})
    for key, expected in FROZEN_AUTHORITY_FLAGS.items():
        results.append(_check(frozen.get(key) == expected,
                              f"invariants.frozen_authority_flags.{key} must be {expected!r}"))
    a3_flags = artifact.get("invariants", {}).get("a3_concurrency_flags", {})
    for key, expected in A3_HANDOFF_FLAGS.items():
        results.append(_check(a3_flags.get(key) == expected,
                              f"invariants.a3_concurrency_flags.{key} must be {expected!r}"))
    engine = artifact.get("invariants", {}).get("engine_boundary", {})
    for key, expected in ENGINE_FLAGS.items():
        results.append(_check(engine.get(key) == expected,
                              f"invariants.engine_boundary.{key} must be {expected!r}"))
    results.append(_check(engine.get("GRAPH_STORAGE_LOCATIONS") == [],
                          "invariants.engine_boundary.GRAPH_STORAGE_LOCATIONS must be empty"))
    impl = artifact.get("implementation_boundary", {})
    results.append(_check(impl.get("source_mutation_scope") == "design_artifacts_only",
                          "implementation_boundary.source_mutation_scope must be design_artifacts_only"))
    results.append(_check(set(impl.get("changed_paths", [])) == ALLOWED_CHANGED_PATHS,
                          "implementation_boundary.changed_paths must be exactly the two A4 files"))
    results.append(_check(set(impl.get("forbidden_paths", []))
                          == {"aota_forge/core/**", "aota_forge/adapters/**"},
                          "implementation_boundary.forbidden_paths must be core/adapters"))
    for key in ("SOURCE_CODE_CHANGED", "GRAPH_IMPLEMENTATION_STARTED",
                "CUTOVER_IMPLEMENTATION_STARTED", "AUTHORITATIVE_GRAPH_WRITES_PERFORMED"):
        results.append(_check(impl.get(key) == "no",
                              f"implementation_boundary.{key} must be no"))
    results.append(_check(isinstance(impl.get("implemented_nothing"), list)
                          and len(impl["implemented_nothing"]) >= 8,
                          "implementation_boundary must enumerate unimplemented runtime surfaces"))

    # 9. Cross-check A1/A2/A12/A3 accepted contracts and handoff.
    results.append(_check(a1.get("subject_identity_rule", {}).get("SUBJECT_ID_IS_AUTHORITY") == "no",
                          "A1 Subject ID authority rule changed"))
    a1_flags = a1.get("invariants", {}).get("identity_flags", {})
    for key, expected in (("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
                          ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
                          ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
                          ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
                          ("HEURISTIC_SUBJECT_IDENTITY_ALLOWED", "no")):
        results.append(_check(a1_flags.get(key) == expected, f"A1 identity flag {key} changed"))
    subject_kinds = {row.get("kind") for row in a1.get("subject_definition", {}).get("subject_kinds", [])
                     if isinstance(row, dict)}
    results.append(_check({"WorkspaceSubject", "ProjectSubject", "PlanSubject", "WorkSubject"}
                          <= subject_kinds, "A1 subject kinds must remain available"))
    work_kind = next((row for row in a1.get("subject_definition", {}).get("subject_kinds", [])
                      if isinstance(row, dict) and row.get("kind") == "WorkSubject"), {})
    results.append(_check("minted" in work_kind.get("id_derivation", "").lower(),
                          "A1 WorkSubject identity must remain minted"))
    a2_flags = a2.get("invariants", {}).get("frozen_authority_flags", {})
    for key, expected in (("ID_IS_AUTHORITY", "no"),
                          ("LEASE_REVISION_BOUND", "yes"),
                          ("LEASE_OBJECT_SCOPE_EXPANSION", "denied"),
                          ("LEASE_OPERATION_SCOPE_EXPANSION", "denied"),
                          ("READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes")):
        results.append(_check(a2_flags.get(key) == expected, f"A2 authority flag {key} changed"))
    results.append(_check(a2.get("invariants", {}).get("m3_a2_constraints", {}).get(
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
                          "A2 must keep authoritative graph writes denied"))
    a12_flags = a12.get("invariants", {})
    for key, expected in (("BOOTSTRAP_CUTOVER_OWNER", "M3-A4"),
                          ("BINDING_AMBIGUITY_OWNER", "M3-A5"),
                          ("TRANSACTION_CAS_OWNER", "M3-A3"),
                          ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no")):
        results.append(_check(a12_flags.get(key) == expected, f"A12 handoff flag {key} changed"))
    results.append(_check(a3.get("base_sha") == A12_COMMIT,
                          "A3 artifact must be based on the A12 commit"))
    results.append(_check(a3.get("m3_a_common_base") == A0_COMMON_BASE,
                          "A3 must preserve the M3-A common base"))
    a3_concurrency = a3.get("invariants", {}).get("frozen_concurrency_flags", {})
    for key, expected in A3_HANDOFF_FLAGS.items():
        results.append(_check(a3_concurrency.get(key) == expected,
                              f"A3 concurrency flag {key} changed"))
    results.append(_check(a3.get("projection_relation", {}).get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "A3 projection relation must deny pointer authority"))
    results.append(_check(a3.get("projection_relation", {}).get("CURRENT_POINTERS_ARE_PROJECTIONS") == "yes",
                          "A3 projection relation must preserve projection status"))
    a3_deferred = a3.get("deferred_questions", {}).get("deferred_to_a4", [])
    a3_deferred_ids = {item.get("QUESTION_ID") for item in a3_deferred if isinstance(item, dict)}
    results.append(_check({"OQ-M3A5-01", "OQ-M3A5-02"} <= a3_deferred_ids,
                          "A3 must hand bootstrap ordering and cutover to A4"))
    handoff = artifact.get("contract_handoff", {})
    results.append(_check(handoff.get("A3", {}).get("CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER") == "yes",
                          "A4 contract handoff must preserve A3 CAS bypass boundary"))
    results.append(_check(handoff.get("A5_boundary", "").find("M3-A5") >= 0,
                          "A4 must preserve A5 binding ambiguity ownership"))

    # 10. Exact changed paths and no implementation source changes.
    git_ok, changed = _changed_paths(repo_root)
    results.append(_check(git_ok, "git changed-path inspection failed"))
    results.append(_check(bool(changed), "no A4 changes detected against the exact base"))
    results.append(_check(changed <= ALLOWED_CHANGED_PATHS,
                          f"forbidden changed paths: {sorted(changed - ALLOWED_CHANGED_PATHS)}"))
    forbidden_changed = [path for path in sorted(changed)
                         if path.startswith(FORBIDDEN_SOURCE_PREFIXES)]
    results.append(_check(not forbidden_changed,
                          f"core/adapters source changes are forbidden: {forbidden_changed}"))
    results.append(_check(changed == set(ALLOWED_CHANGED_PATHS),
                          f"changed paths must be exactly {sorted(ALLOWED_CHANGED_PATHS)}"))
    source_hits = _scan_source(repo_root)
    results.append(_check(not source_hits,
                          f"graph/cutover/projection implementation surface found: {source_hits}"))
    ingress = repo_root / "aota_forge" / "core" / "ingress.py"
    results.append(_check(ingress.is_file() and READ_ONLY_GATE_MARKER in ingress.read_text(encoding="utf-8"),
                          "read-only ingress gate marker missing"))

    # 11. Metrics are intentionally checked last so a malformed artifact still
    # reports its substantive contract failures above.
    metrics = artifact.get("report_metrics", {})
    expected_metrics = {
        "a0_migration_input_count": 25,
        "bootstrap_source_class_count": 25,
        "bootstrap_candidate_source_class_count": 3,
        "migration_mapping_count": len(REQUIRED_MAPPING_CONCEPTS),
        "candidate_validation_gate_count": len(VALIDATION_GATES),
        "pointer_transition_count": len(POINTER_NAMES),
        "regression_proof_count": len(REGRESSION_PROOF_CLASSES),
        "design_scenario_count": len(SCENARIO_NAMES),
        "forbidden_authority_source_count": 3,
        "m3_a4_negative_mutation_count": 6,
        "unsupported_success_claims": 0,
    }
    for key, expected in expected_metrics.items():
        results.append(_check(metrics.get(key) == expected,
                              f"report_metrics.{key} must be {expected!r}"))
    return results


def _negative_mutations() -> list[tuple[str, str, object]]:
    return [
        (
            "git history promoted to Subject authority",
            "GIT_HISTORY_IS_SUBJECT_AUTHORITY",
            lambda item: item["invariants"]["frozen_authority_flags"].__setitem__(
                "GIT_HISTORY_IS_SUBJECT_AUTHORITY", "yes"),
        ),
        (
            "current pointer promoted to authority",
            "CURRENT_POINTERS_ARE_AUTHORITY",
            lambda item: item["invariants"]["frozen_authority_flags"].__setitem__(
                "CURRENT_POINTERS_ARE_AUTHORITY", "yes"),
        ),
        (
            "shadow graph allowed authoritative writes",
            "SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE",
            lambda item: item["shadow_read_model"].__setitem__(
                "SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE", "yes"),
        ),
        (
            "heuristic migration merge enabled",
            "HEURISTIC_IDENTITY_MERGE_ALLOWED",
            lambda item: item["migration_identity_rule"].__setitem__(
                "HEURISTIC_IDENTITY_MERGE_ALLOWED", "yes"),
        ),
        (
            "post-cutover dual authority enabled",
            "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY",
            lambda item: item["cutover_rule"].__setitem__(
                "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "yes"),
        ),
        (
            "projection rebuild mutates graph",
            "PROJECTION_REBUILD_MUTATES_GRAPH",
            lambda item: item["projection_rebuild_rule"].__setitem__(
                "PROJECTION_REBUILD_MUTATES_GRAPH", "yes"),
        ),
    ]


def run_self_test(
    artifact: dict,
    repo_root: Path,
    a0: dict,
    a1: dict,
    a2: dict,
    a12: dict,
    a3: dict,
    matrix: dict,
) -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for name, marker, mutate in _negative_mutations():
        candidate = copy.deepcopy(artifact)
        mutate(candidate)
        failures = [message for ok, message in run_checks(
            candidate, repo_root, a0, a1, a2, a12, a3, matrix
        ) if not ok]
        outcomes.append({
            "case": name,
            "marker": marker,
            "rejected": bool(failures),
            "failure_count": len(failures),
        })
    return outcomes


def summarize(artifact: dict, results: list[tuple[bool, str]], negative: list[dict[str, object]] | None) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    negative_pass = None if negative is None else all(bool(item.get("rejected")) for item in negative)
    return {
        "artifact": "m3-a4-bootstrap-cutover",
        "lane": artifact.get("lane"),
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "negative_guard_proof": None if negative is None else {
            "case_count": len(negative),
            "cases": negative,
            "verdict": "PASS" if negative_pass else "FAIL",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A4 bootstrap/cutover design guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--self-test", action="store_true", help="run six negative guard mutations")
    parser.add_argument("--artifact", type=Path, help="path to the A4 artifact JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    args = parser.parse_args()

    repo_root = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    artifact_path = (args.artifact or repo_root / A4_REL).resolve()
    try:
        artifact = _read_json(artifact_path)
        a0 = _read_json(repo_root / A0_REL)
        a1 = _read_json(repo_root / A1_REL)
        a2 = _read_json(repo_root / A2_REL)
        a12 = _read_json(repo_root / A12_REL)
        a3 = _read_json(repo_root / A3_REL)
        matrix = _read_json(repo_root / MATRIX_REL)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"GUARD FAIL: unreadable input: {exc}")
        return 1

    results = run_checks(artifact, repo_root, a0, a1, a2, a12, a3, matrix)
    negative = run_self_test(artifact, repo_root, a0, a1, a2, a12, a3, matrix) if args.self_test else None
    summary = summarize(artifact, results, negative)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        ok = summary["verdict"] == "PASS"
        if args.self_test:
            ok = ok and summary["negative_guard_proof"]["verdict"] == "PASS"
        return 0 if ok else 1

    try:
        display_path = artifact_path.relative_to(repo_root)
    except ValueError:
        display_path = artifact_path
    print(f"artifact: {display_path}")
    print(f"lane: {artifact.get('lane')}")
    print(f"base_sha: {artifact.get('base_sha')}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if negative is not None:
        print(f"M3_A4_NEGATIVE_GUARD_PROOF: {'PASS' if summary['negative_guard_proof']['verdict'] == 'PASS' else 'FAIL'}")
        for case in negative:
            print(f"  {'REJECTED' if case['rejected'] else 'MISSED '}  {case['case']}")
    print(f"VERDICT: {summary['verdict']}")
    if args.self_test and summary["negative_guard_proof"]["verdict"] != "PASS":
        return 1
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
