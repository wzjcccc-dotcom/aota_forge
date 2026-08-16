#!/usr/bin/env python3
"""M3-A4/A5 serial authority-contract reconciliation guard.

This is a design-only guard.  It reconciles the accepted A4 bootstrap/cutover
contract with the accepted A5 binding/recovery contract without implementing a
graph, binding, recovery, projection, migration, or cutover engine.

The guard checks the pre/post-cutover authority boundary, preserves distinct
migration and runtime ambiguity evidence under the shared
``NEEDS_SEMANTIC_CHOICE`` class, and proves that projection repair is mechanical
and graph-preserving.  ``--self-test`` performs seven in-memory negative
mutations; no repository file is changed by the guard.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path


BASE_SHA = "2485a8d51b6fb5722a0da02e9dc93e5615401277"
A0_COMMIT = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A1_COMMIT = "7dbc2225e0b66f6bf9e6d0970581d96f428d4362"
A2_COMMIT = "b29fe5f6fa47035cdeb4137cd62aafc585257b40"
A12_COMMIT = "8dad2d3ceeaaf093f35e39daa5c12049c0505527"
A3_COMMIT = BASE_SHA
A4_COMMIT = "6552b9a2b4d38de2c947e22ea4c30972266bb54a"
A5_COMMIT = "4573712a1f89f5610635c9a77231be7509fb4957"
A0_COMMON_BASE = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

A45_REL = Path("deploy/evidence/issues/9/m3-a/a45-contract-reconciliation.json")
GUARD_REL = Path("scripts/m3_a45_contract_reconciliation_guard.py")
A0_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A1_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
A2_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
A12_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
A3_REL = Path("deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json")
A4_REL = Path("deploy/evidence/issues/9/m3-a/a4-bootstrap-cutover.json")
A5_REL = Path("deploy/evidence/issues/9/m3-a/a5-binding-recovery.json")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

OWN_CHANGED_PATHS = frozenset({str(A45_REL), str(GUARD_REL)})
INTEGRATED_CHANGED_PATHS = frozenset(
    {
        str(A4_REL),
        "scripts/m3_a4_bootstrap_cutover_guard.py",
        str(A5_REL),
        "scripts/m3_a5_binding_recovery_guard.py",
        str(A45_REL),
        str(GUARD_REL),
    }
)
FORBIDDEN_SOURCE_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

INPUTS = {
    "a0": A0_REL,
    "a1": A1_REL,
    "a2": A2_REL,
    "a12": A12_REL,
    "a3": A3_REL,
    "a4": A4_REL,
    "a5": A5_REL,
    "matrix": MATRIX_REL,
}
INPUT_COMMITS = {
    "a0": A0_COMMIT,
    "a1": A1_COMMIT,
    "a2": A2_COMMIT,
    "a12": A12_COMMIT,
    "a3": A3_COMMIT,
    "a4": A4_COMMIT,
    "a5": A5_COMMIT,
}

SCENARIO_IDS = tuple(f"A45-S{i}" for i in range(1, 10))
PHASES = ("PRE_CUTOVER", "CUTOVER", "POST_CUTOVER")
RESULT_CODES = {
    "SUBJECT_NOT_FOUND",
    "MIGRATION_REQUIRES_SEMANTIC_CHOICE",
    "PROJECTION_STALE_RECONCILABLE",
    "MATERIALIZED_DECISION_MISSING",
    "NEEDS_SEMANTIC_CHOICE",
}
SIBLING_PATCHES = (
    (A4_COMMIT, A4_REL),
    (A4_COMMIT, Path("scripts/m3_a4_bootstrap_cutover_guard.py")),
    (A5_COMMIT, A5_REL),
    (A5_COMMIT, Path("scripts/m3_a5_binding_recovery_guard.py")),
)


def _check(condition: bool, message: str) -> tuple[bool, str]:
    return condition, message


def _plain(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _get(item: object, dotted: str, default: object = None) -> object:
    current = item
    for key in dotted.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _git(repo_root: Path, args: list[str], *, binary: bool = False):
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=not binary,
        timeout=60,
    )


def _changed_paths(repo_root: Path) -> tuple[int, set[str]]:
    tracked = _git(repo_root, ["diff", "--name-only", BASE_SHA])
    untracked = _git(repo_root, ["ls-files", "--others", "--exclude-standard"])
    if tracked.returncode != 0 or untracked.returncode != 0:
        return 1, set()
    changed = set(filter(None, tracked.stdout.splitlines()))
    changed.update(
        path
        for path in untracked.stdout.splitlines()
        if path and "__pycache__" not in path and not path.endswith(".pyc")
    )
    return 0, changed


def _source_blob(repo_root: Path, commit: str, path: Path) -> bytes | None:
    proc = _git(repo_root, ["show", f"{commit}:{path}"], binary=True)
    return proc.stdout if proc.returncode == 0 else None


def _contains_all(text: object, phrases: tuple[str, ...]) -> bool:
    return isinstance(text, str) and all(phrase in text for phrase in phrases)


def _check_identity(results: list[tuple[bool, str]], artifact: dict) -> None:
    required = (
        "schema_version",
        "project_id",
        "milestone",
        "phase",
        "gate",
        "common_base",
        "inputs",
        "input_commits",
        "design_proof_only",
        "accepted_lane_truth",
        "source_error_codes_added",
        "integration_sequence",
        "pre_cutover_binding_model",
        "post_cutover_binding_model",
        "candidate_validity_contract",
        "ambiguity_reconciliation",
        "bounded_result_taxonomy",
        "projection_recovery_contract",
        "cutover_recovery_boundary",
        "pointer_phase_model",
        "pointer_global_invariants",
        "readonly_boundary",
        "idempotency_cross_contract",
        "cross_scenarios",
        "a6_handoff",
        "invariants",
        "implementation_boundary",
        "report_metrics",
    )
    for key in required:
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    for key, expected in (
        ("schema_version", 1),
        ("project_id", "aota_forge"),
        ("milestone", "M3"),
        ("phase", "M3-A"),
        ("gate", "M3-A4-A5-CONTRACT-RECONCILIATION"),
        ("common_base", BASE_SHA),
        ("governing_plan_issue", 9),
    ):
        results.append(_check(artifact.get(key) == expected, f"{key} must be {expected!r}"))

    inputs = artifact.get("inputs", {})
    for key, path in INPUTS.items():
        results.append(_check(inputs.get(key) == str(path), f"inputs.{key} must be {path}"))
    commits = artifact.get("input_commits", {})
    for key, commit in INPUT_COMMITS.items():
        results.append(_check(commits.get(key) == commit, f"input_commits.{key} must be {commit}"))

    sequence = artifact.get("integration_sequence", {})
    results.append(_check(sequence.get("start_commit") == BASE_SHA,
                          "integration_sequence.start_commit must be A3"))
    ordered = sequence.get("ordered_lanes", [])
    results.append(_check(
        [row.get("lane") for row in ordered if isinstance(row, dict)] == ["M3-A4", "M3-A5"],
        "integration_sequence must be ordered A4 then A5",
    ))
    results.append(_check(
        [row.get("source_commit") for row in ordered if isinstance(row, dict)] == [A4_COMMIT, A5_COMMIT],
        "integration_sequence must use the supplied A4 and A5 commits",
    ))
    results.append(_check(
        all(row.get("contents_verified_exactly") == "yes" for row in ordered if isinstance(row, dict)),
        "both sibling patches must be marked exact",
    ))
    results.append(_check("no source-code merge" in sequence.get("merge_scope", ""),
                          "integration merge scope must exclude source-code merge"))

    design = artifact.get("design_proof_only", {})
    for key in (
        "no_graph_storage_engine",
        "no_graph_mutation_implementation",
        "no_authoritative_graph_writes",
        "no_binding_engine",
        "no_recovery_engine",
        "no_projection_engine",
        "no_authority_engine",
        "no_cutover_runtime",
        "no_runtime_behavior",
    ):
        results.append(_check(design.get(key) == "yes", f"design_proof_only.{key} must be yes"))
    results.append(_check(design.get("source_mutation_scope") == "design_artifacts_only",
                          "design_proof_only.source_mutation_scope must be design_artifacts_only"))
    results.append(_check(artifact.get("source_error_codes_added") == [],
                          "source_error_codes_added must remain empty"))


def _check_accepted_lane_truth(results: list[tuple[bool, str]], artifact: dict) -> None:
    a4 = artifact.get("accepted_lane_truth", {}).get("a4", {})
    a4_expected = {
        "GRAPH_BOOTSTRAP_STRATEGY": "PASS",
        "MIGRATION_MAPPING": "PASS",
        "GRAPH_CANDIDATE_VALIDATION": "PASS",
        "AMBIGUOUS_MIGRATION_IDENTITY": "NEEDS_SEMANTIC_CHOICE",
        "HEURISTIC_IDENTITY_MERGE_ALLOWED": "no",
        "SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE": "no",
        "GRAPH_CUTOVER_RULE": "PASS",
        "ONE_CUTOVER_AUTHORITY": "yes",
        "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY": "no",
        "GRAPH_WRITE_BEFORE_CUTOVER": "denied",
        "CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY": "yes",
        "PROJECTION_REBUILD_RULE": "PASS",
        "PROJECTION_REBUILD_MUTATES_GRAPH": "no",
        "STALE_PROJECTION_MAY_BE_REBUILT": "yes",
        "STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT": "yes",
        "CURRENT_POINTERS_ARE_AUTHORITY": "no",
        "GIT_HISTORY_IS_SUBJECT_AUTHORITY": "no",
        "EVENT_LOG_IS_SUBJECT_AUTHORITY": "no",
        "CONTROL_COMMENT_IS_SUBJECT_AUTHORITY": "no",
    }
    for key, expected in a4_expected.items():
        results.append(_check(a4.get(key) == expected, f"accepted A4 truth {key} must be {expected}"))

    a5 = artifact.get("accepted_lane_truth", {}).get("a5", {})
    a5_expected = {
        "SUBJECT_BINDING_RULE": "PASS",
        "ZERO_CANDIDATE_RULE": "bounded_not_found_or_reconciliation",
        "ONE_CANDIDATE_RULE": "materialized_decision + authority + revision + predecessor_validity",
        "MANY_CANDIDATE_RULE": "NEEDS_SEMANTIC_CHOICE",
        "CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER": "yes",
        "HEURISTIC_PRE_FILTER_ALLOWED": "no",
        "HEURISTIC_SUBJECT_SELECTION_ALLOWED": "no",
        "MATERIALIZED_DECISION_REQUIRED_FOR_SEMANTIC_FOLLOWUP": "yes",
        "UNIQUE_CANDIDATE_BYPASSES_AUTHORITY": "no",
        "UNIQUE_CANDIDATE_BYPASSES_CAS": "no",
        "RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING": "no",
        "STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT": "yes",
        "SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING": "no",
        "RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT": "no",
        "AMBIGUITY_RESULT": "NEEDS_SEMANTIC_CHOICE",
    }
    for key, expected in a5_expected.items():
        results.append(_check(a5.get(key) == expected, f"accepted A5 truth {key} must be {expected}"))


def _check_lane_inputs(
    results: list[tuple[bool, str]],
    a1: dict,
    a2: dict,
    a3: dict,
    a4: dict,
    a5: dict,
    a12: dict,
    matrix: dict,
) -> None:
    results.append(_check(a4.get("lane") == "M3-A4", "A4 lane must be M3-A4"))
    results.append(_check(a5.get("lane") == "M3-A5", "A5 lane must be M3-A5"))
    results.append(_check(a4.get("base_sha") == BASE_SHA, "A4 must be based on A3"))
    results.append(_check(a5.get("base_sha") == BASE_SHA, "A5 must be based on A3"))
    results.append(_check(a3.get("base_sha") == A12_COMMIT, "A3 must preserve the A12 base"))
    results.append(_check(a3.get("m3_a_common_base") == A0_COMMON_BASE,
                          "A3 must preserve the historical M3-A common base"))
    results.append(_check(a12.get("invariants", {}).get("BOOTSTRAP_CUTOVER_OWNER") == "M3-A4",
                          "A12 bootstrap ownership must remain M3-A4"))
    results.append(_check(a12.get("invariants", {}).get("BINDING_AMBIGUITY_OWNER") == "M3-A5",
                          "A12 binding ambiguity ownership must remain M3-A5"))
    results.append(_check(a12.get("invariants", {}).get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
                          "A12 must deny authoritative graph writes"))
    results.append(_check(
        len(matrix.get("failure_classes", [])) == 16,
        "M2 regression matrix must retain 16 failure classes",
    ))

    a4_identity = a4.get("migration_identity_rule", {})
    for key, expected in (
        ("AMBIGUOUS_MIGRATION_IDENTITY", "NEEDS_SEMANTIC_CHOICE"),
        ("HEURISTIC_IDENTITY_MERGE_ALLOWED", "no"),
    ):
        results.append(_check(a4_identity.get(key) == expected, f"A4 {key} must be {expected}"))
    a4_shadow = a4.get("shadow_read_model", {})
    results.append(_check(a4_shadow.get("SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE") == "no",
                          "A4 shadow graph writes must be denied"))
    a4_cutover = a4.get("cutover_rule", {})
    for key, expected in (
        ("ONE_CUTOVER_AUTHORITY", "yes"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
    ):
        results.append(_check(a4_cutover.get(key) == expected, f"A4 cutover {key} must be {expected}"))
    a4_concurrency = a4.get("cutover_concurrency", {})
    results.append(_check(a4_concurrency.get("GRAPH_WRITE_BEFORE_CUTOVER") == "denied",
                          "A4 graph writes before cutover must be denied"))
    a4_projection = a4.get("projection_rebuild_rule", {})
    for key, expected in (
        ("PROJECTION_REBUILD_MUTATES_GRAPH", "no"),
        ("STALE_PROJECTION_MAY_BE_REBUILT", "yes"),
        ("STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT", "yes"),
    ):
        results.append(_check(a4_projection.get(key) == expected, f"A4 projection {key} must be {expected}"))
    a4_flags = a4.get("invariants", {}).get("frozen_authority_flags", {})
    for key, expected in (
        ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
        ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
        ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
        ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
    ):
        results.append(_check(a4_flags.get(key) == expected, f"A4 invariant {key} must be {expected}"))

    a5_validity = a5.get("candidate_validity_model", {})
    for key, expected in (
        ("CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER", "yes"),
        ("HEURISTIC_PRE_FILTER_ALLOWED", "no"),
        ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no"),
    ):
        results.append(_check(a5_validity.get(key) == expected, f"A5 validity {key} must be {expected}"))
    results.append(_check(a5.get("zero_one_many_rule", {}).get("MANY", {}).get("result") == "NEEDS_SEMANTIC_CHOICE",
                          "A5 MANY result must be NEEDS_SEMANTIC_CHOICE"))
    a5_semantic = a5.get("semantic_decision_materialization", {})
    results.append(_check(a5_semantic.get("UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION") == "no",
                          "A5 unique candidate cannot bypass Decision materialization"))
    a5_recovery = a5.get("recovery_boundary", {})
    results.append(_check(a5_recovery.get("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING") == "no",
                          "A5 recovery engine must not perform semantic reasoning"))
    a5_projection = a5.get("projection_recovery", {})
    results.append(_check(a5_projection.get("STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT") == "yes",
                          "A5 stale pointer must not hide a durable Subject"))
    a5_readonly = a5.get("readonly_diagnostic_exemption", {})
    results.append(_check(a5_readonly.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING") == "no",
                          "A5 safe diagnosis must not require Subject binding"))
    a5_idem = a5.get("idempotent_recovery", {})
    results.append(_check(a5_idem.get("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT") == "no",
                          "A5 recovery replay must not duplicate semantic effect"))
    a5_flags = a5.get("invariants", {}).get("binding_flags", {})
    results.append(_check(a5_flags.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "A5 current pointers must not be authority"))

    a1_flags = a1.get("invariants", {}).get("identity_flags", {})
    results.append(_check(a1_flags.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "A1 current pointers must not be authority"))
    a2_flags = a2.get("invariants", {}).get("frozen_authority_flags", {})
    results.append(_check(a2_flags.get("READONLY_DIAGNOSTIC_PLANE_PRESERVED") == "yes",
                          "A2 read-only diagnostic plane must be preserved"))


def _check_pre_post_models(results: list[tuple[bool, str]], artifact: dict) -> None:
    pre = artifact.get("pre_cutover_binding_model", {})
    for key, expected in (
        ("PRE_CUTOVER_SHADOW_GRAPH_CAN_SUPPLY_DIAGNOSTIC_CANDIDATES", "yes"),
        ("PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "no"),
        ("SHADOW_CANDIDATE_VALIDITY_DOES_NOT_EQUAL_AUTHORITY", "yes"),
    ):
        results.append(_check(pre.get(key) == expected, f"pre_cutover_binding_model.{key} must be {expected}"))
    results.append(_check(_contains_all(
        pre.get("PRE_CUTOVER_BINDING_AUTHORITY_SOURCE"),
        ("legacy", "shadow", "diagnostic"),
    ), "pre-cutover binding authority source must be explicit legacy authority with diagnostic shadow evidence"))
    results.append(_check(set(pre.get("shadow_candidate_uses", [])) >= {
        "comparison against legacy evidence",
        "migration candidate validation",
        "safe read-only diagnostics",
    }, "shadow candidate uses must remain diagnostic/validation uses"))
    results.append(_check(_contains_all(
        pre.get("candidate_count_rule"),
        ("after deterministic validity filtering", "never transfers binding authority"),
    ), "pre-cutover candidate count must not transfer authority"))

    post = artifact.get("post_cutover_binding_model", {})
    results.append(_check(post.get("POST_CUTOVER_BINDING_CANDIDATE_SOURCE") == "canonical_durable_subject_graph",
                          "post-cutover binding source must be canonical_durable_subject_graph"))
    results.append(_check(post.get("POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH") == "no",
                          "legacy pointers must not override the post-cutover graph"))
    results.append(_check(post.get("GRAPH_IS_LIFECYCLE_AUTHORITY") == "yes",
                          "post-cutover graph must be lifecycle authority"))
    results.append(_check(_contains_all(post.get("legacy_pointer_role"),
                                        ("compatibility", "projection", "cannot override")),
                          "post-cutover legacy pointer role must be compatibility/projection only"))


def _check_candidate_and_ambiguity(results: list[tuple[bool, str]], artifact: dict) -> None:
    validity = artifact.get("candidate_validity_contract", {})
    for key, expected in (
        ("CANDIDATE_VALIDITY_EVALUATED_BEFORE_COUNT", "yes"),
        ("CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER", "yes"),
        ("HEURISTIC_PRE_FILTER_ALLOWED", "no"),
        ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no"),
        ("SHADOW_CANDIDATE_VALIDITY_DOES_NOT_EQUAL_AUTHORITY", "yes"),
        ("ZERO_CANDIDATE_RULE", "bounded_not_found_or_reconciliation"),
        ("ONE_CANDIDATE_RULE", "materialized_decision + authority + revision + predecessor_validity"),
        ("MANY_CANDIDATE_RULE", "NEEDS_SEMANTIC_CHOICE"),
        ("MATERIALIZED_DECISION_REQUIRED_FOR_SEMANTIC_FOLLOWUP", "yes"),
        ("UNIQUE_CANDIDATE_BYPASSES_AUTHORITY", "no"),
        ("UNIQUE_CANDIDATE_BYPASSES_CAS", "no"),
    ):
        results.append(_check(validity.get(key) == expected, f"candidate validity {key} must be {expected}"))
    filters = set(validity.get("allowed_validity_filters", []))
    results.append(_check({
        "correct project and workflow scope",
        "canonical record type",
        "valid lineage",
        "valid predecessor",
        "explicit materialized Decision",
        "authority eligibility",
        "revision compatibility",
    } <= filters, "candidate validity filters must be deterministic and complete"))
    results.append(_check(set(validity.get("forbidden_selection_signals", [])) == {
        "recency or newest",
        "title or text similarity",
        "nearest timestamp",
        "path proximity",
        "first match",
        "LLM similarity",
    }, "candidate validity must forbid all heuristic selection signals"))
    results.append(_check(set(validity.get("one_candidate_requirements", [])) == {
        "materialized semantic Decision basis",
        "valid typed authority",
        "valid current and expected revision",
        "valid predecessor state",
    }, "one candidate must retain all semantic and concurrency prerequisites"))
    results.append(_check(validity.get("many_candidate_result") == "NEEDS_SEMANTIC_CHOICE",
                          "many valid candidates must return NEEDS_SEMANTIC_CHOICE"))

    ambiguity = artifact.get("ambiguity_reconciliation", {})
    for key, expected in (
        ("AMBIGUOUS_MIGRATION_IDENTITY", "NEEDS_SEMANTIC_CHOICE"),
        ("RUNTIME_BINDING_AMBIGUITY_RESULT", "NEEDS_SEMANTIC_CHOICE"),
        ("SHARED_SEMANTIC_CLASS", "NEEDS_SEMANTIC_CHOICE"),
        ("MIGRATION_AMBIGUITY_AND_RUNTIME_MANY_SHARE_SEMANTIC_CLASS", "yes"),
        ("evidence_context_is_distinct", "yes"),
        ("MIGRATION_AMBIGUITY_AUTOMATICALLY_RESOLVED_BY_RUNTIME_BINDING", "no"),
        ("RUNTIME_BINDING_AUTOMATICALLY_RESOLVES_MIGRATION_AMBIGUITY", "no"),
    ):
        results.append(_check(ambiguity.get(key) == expected, f"ambiguity reconciliation {key} must be {expected}"))
    results.append(_check(ambiguity.get("MIGRATION_AMBIGUITY_RESULT") == "MIGRATION_REQUIRES_SEMANTIC_CHOICE",
                          "migration ambiguity must retain its bounded result"))
    results.append(_check(_contains_all(ambiguity.get("followup_rule"),
                                        ("cannot silently repair", "semantic ambiguity")),
                          "runtime mechanics must not resolve migration ambiguity"))

    taxonomy = artifact.get("bounded_result_taxonomy", [])
    codes = {row.get("RESULT") for row in taxonomy if isinstance(row, dict)}
    results.append(_check(codes == RESULT_CODES, "bounded result taxonomy must define exactly five distinct results"))
    for row in taxonomy:
        if not isinstance(row, dict):
            results.append(_check(False, "bounded result taxonomy entries must be objects"))
            continue
        for field in ("RESULT", "WHEN", "SEMANTIC_CLASS", "RECOVERY"):
            results.append(_check(_plain(row.get(field)), f"bounded result {row.get('RESULT')} must define {field}"))
    results.append(_check(len(taxonomy) == len(codes), "bounded result taxonomy codes must be unique"))


def _check_projection_cutover(results: list[tuple[bool, str]], artifact: dict) -> None:
    projection = artifact.get("projection_recovery_contract", {})
    for key, expected in (
        ("PROJECTION_REBUILD_IS_MECHANICAL_RECOVERY", "yes"),
        ("PROJECTION_REBUILD_REQUIRES_SEMANTIC_CHOICE", "no"),
        ("PROJECTION_REBUILD_CAN_CHANGE_GRAPH", "no"),
        ("PROJECTION_REBUILD_CAN_SELECT_BETWEEN_MULTIPLE_SEMANTIC_SUBJECTS", "no"),
        ("STALE_POINTER_BLOCKS_CANONICAL_SUBJECT_DISCOVERY", "no"),
        ("STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT", "yes"),
        ("STALE_POINTER_REPAIR_REQUIRES_PROFILE_TASK", "no"),
        ("STALE_POINTER_REPAIR_REQUIRES_NEW_SEMANTIC_DECISION", "no"),
    ):
        results.append(_check(projection.get(key) == expected, f"projection recovery {key} must be {expected}"))
    results.append(_check(_contains_all(projection.get("canonical_source"), ("canonical", "Subject Graph")),
                          "projection rebuild source must be the canonical graph"))
    results.append(_check(_contains_all(projection.get("rebuild_effect"),
                                        ("mechanical", "no new Subject", "no", "lifecycle transition")),
                          "projection rebuild effect must be graph-preserving"))
    results.append(_check(_contains_all(projection.get("ambiguous_graph_behavior"),
                                        ("ambiguous", "not repairable", "guess")),
                          "ambiguous graph state must not be repaired by guessing"))

    boundary = artifact.get("cutover_recovery_boundary", {})
    for key, expected in (
        ("RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "no"),
        ("CUTOVER_REQUIRES_EXPLICIT_AUTHORIZED_TRANSITION", "yes"),
        ("RECOVERY_ENGINE_MAY_REPAIR_POST_CUTOVER_PROJECTION", "yes"),
        ("RECOVERY_ENGINE_MAY_REPAIR_PRE_CUTOVER_SHADOW_EVIDENCE", "diagnostic_only"),
        ("RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE", "no"),
        ("recovery_never_flips_lifecycle_authority", "yes"),
    ):
        results.append(_check(boundary.get(key) == expected, f"cutover recovery {key} must be {expected}"))
    pre_failure = boundary.get("failed_pre_flip_cutover", {})
    for key, expected in (("FAILED_PRE_FLIP_CUTOVER_CHANGES_BINDING_AUTHORITY", "no"),):
        results.append(_check(pre_failure.get(key) == expected, f"failed pre-flip {key} must be {expected}"))
    results.append(_check(_contains_all(pre_failure.get("legacy_authority"), ("sole", "authority")),
                          "failed pre-flip cutover must retain legacy sole authority"))
    results.append(_check(_contains_all(pre_failure.get("shadow_graph"), ("non-authoritative", "diagnostic")),
                          "failed pre-flip shadow graph must remain diagnostic"))
    post_failure = boundary.get("post_flip_projection_failure", {})
    results.append(_check(post_failure.get("POST_FLIP_PROJECTION_FAILURE_CHANGES_BINDING_AUTHORITY") == "no",
                          "post-flip projection failure must not change binding authority"))
    results.append(_check(_contains_all(post_failure.get("graph_authority"), ("sole", "authority")),
                          "post-flip projection failure must retain graph authority"))
    results.append(_check(_contains_all(boundary.get("cutover_replay"),
                                        ("already_cut_over", "no second authority transition")),
                          "cutover replay must be bounded and idempotent"))


def _check_pointers_readonly_idempotency(results: list[tuple[bool, str]], artifact: dict) -> None:
    phases = artifact.get("pointer_phase_model", [])
    results.append(_check([row.get("PHASE") for row in phases if isinstance(row, dict)] == list(PHASES),
                          "pointer phase model must be PRE_CUTOVER, CUTOVER, POST_CUTOVER"))
    for row in phases:
        if not isinstance(row, dict):
            results.append(_check(False, "pointer phase entries must be objects"))
            continue
        for field in ("PHASE", "POINTER_READ_ROLE", "POINTER_WRITE_ROLE", "GRAPH_AUTHORITY", "BINDING_AUTHORITY"):
            results.append(_check(_plain(row.get(field)), f"pointer phase {row.get('PHASE')} must define {field}"))
    by_phase = {row.get("PHASE"): row for row in phases if isinstance(row, dict)}
    results.append(_check(_contains_all(by_phase.get("PRE_CUTOVER", {}).get("POINTER_READ_ROLE"), ("legacy", "not canonical")),
                          "pre-cutover pointer reads must be legacy operational only"))
    results.append(_check(_contains_all(by_phase.get("PRE_CUTOVER", {}).get("BINDING_AUTHORITY"), ("legacy", "cannot")),
                          "pre-cutover binding must remain legacy"))
    results.append(_check(_contains_all(by_phase.get("POST_CUTOVER", {}).get("POINTER_READ_ROLE"), ("projection", "compatibility")),
                          "post-cutover pointer reads must be projection/compatibility only"))
    results.append(_check(_contains_all(by_phase.get("POST_CUTOVER", {}).get("BINDING_AUTHORITY"), ("canonical", "cannot")),
                          "post-cutover binding must be canonical graph only"))

    global_invariants = artifact.get("pointer_global_invariants", {})
    for key, expected in (
        ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
        ("CURRENT_POINTERS_ARE_AUTHORITY_AFTER_CUTOVER", "no"),
        ("CURRENT_POINTERS_ARE_PROJECTIONS", "yes"),
        ("POINTERS_ARE_CANONICAL_SUBJECT_IDENTITY", "no"),
    ):
        results.append(_check(global_invariants.get(key) == expected, f"pointer invariant {key} must be {expected}"))
    results.append(_check(_contains_all(global_invariants.get("pre_cutover_qualification"),
                                        ("operationally authoritative", "canonical Subject authority")),
                          "pre-cutover pointer qualification must distinguish operational and Subject authority"))

    readonly = artifact.get("readonly_boundary", {})
    for key, expected in (
        ("MIGRATION_AMBIGUITY_BLOCKS_SAFE_READONLY_DIAGNOSIS", "no"),
        ("CUTOVER_STATE_BLOCKS_SAFE_INDEPENDENT_DIAGNOSIS", "no_where_technically_safe"),
        ("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING", "no"),
        ("SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE", "no"),
    ):
        results.append(_check(readonly.get(key) == expected, f"readonly boundary {key} must be {expected}"))
    results.append(_check(set(readonly.get("available_phases", [])) == {
        "before cutover",
        "during cutover where technically safe",
        "after cutover",
        "with stale projections",
        "with migration ambiguity",
    }, "read-only diagnosis must remain available in all required phases"))

    idem = artifact.get("idempotency_cross_contract", {})
    for key, expected in (
        ("CUTOVER_REPLAY_AFTER_SUCCESS", "returns_already_cut_over_or_equivalent_bounded_result"),
        ("CUTOVER_REPLAY_CREATES_SECOND_AUTHORITY_TRANSITION", "no"),
        ("PROJECTION_REBUILD_REPLAY_DUPLICATES_SEMANTIC_EFFECT", "no"),
        ("BINDING_RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT", "no"),
        ("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT", "no"),
    ):
        results.append(_check(idem.get(key) == expected, f"idempotency {key} must be {expected}"))
    results.append(_check(_contains_all(idem.get("replay_rule"), ("never", "second authority", "semantic")),
                          "cross-contract replay rule must prohibit duplicate semantic effects"))


def _check_scenarios_handoff(results: list[tuple[bool, str]], artifact: dict) -> None:
    scenarios = artifact.get("cross_scenarios", [])
    ids = [row.get("SCENARIO_ID") for row in scenarios if isinstance(row, dict)]
    results.append(_check(ids == list(SCENARIO_IDS), "cross_scenarios must be exactly A45-S1 through A45-S9"))
    required_phrases = {
        "A45-S1": ("cannot authoritatively bind", "legacy"),
        "A45-S2": ("MIGRATION_REQUIRES_SEMANTIC_CHOICE", "cannot collapse"),
        "A45-S3": ("discoverable", "rebuilt deterministically"),
        "A45-S4": ("NEEDS_SEMANTIC_CHOICE", "cannot select"),
        "A45-S5": ("legacy remains sole", "diagnostic"),
        "A45-S6": ("graph remains binding authority", "recovery"),
        "A45-S7": ("may not invent", "heuristic"),
        "A45-S8": ("safe independent diagnosis remains available", "no Subject binding"),
        "A45-S9": ("already_cut_over", "second authority transition"),
    }
    for sid in SCENARIO_IDS:
        row = next((item for item in scenarios if isinstance(item, dict) and item.get("SCENARIO_ID") == sid), {})
        for field in ("SCENARIO_ID", "NAME", "given", "when", "expected", "result", "authority", "semantic_effect"):
            results.append(_check(_plain(row.get(field)), f"scenario {sid} must define {field}"))
        results.append(_check(_contains_all(row.get("expected"), required_phrases[sid]),
                              f"scenario {sid} must preserve its cross-contract behavior"))

    handoff = artifact.get("a6_handoff", {})
    for key, expected in (
        ("A4_CONTRACT_ACCEPTED", "yes"),
        ("A5_CONTRACT_ACCEPTED", "yes"),
        ("PRE_CUTOVER_BINDING_AUTHORITY", "PASS"),
        ("POST_CUTOVER_BINDING_AUTHORITY", "PASS"),
        ("MIGRATION_RUNTIME_AMBIGUITY_CONTRACT", "PASS"),
        ("PROJECTION_RECOVERY_CONTRACT", "PASS"),
        ("CUTOVER_RECOVERY_AUTHORITY_BOUNDARY", "PASS"),
        ("UNMIGRATABLE_RECOVERY_BOUNDARY", "PASS"),
        ("READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
        ("NO_A4_A5_CONTRADICTION", "yes"),
        ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
        ("M3_A6_READY", "yes"),
    ):
        results.append(_check(handoff.get(key) == expected, f"a6_handoff.{key} must be {expected}"))


def _check_implementation(results: list[tuple[bool, str]], artifact: dict, repo_root: Path) -> None:
    invariants = artifact.get("invariants", {})
    for key, expected in (
        ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
        ("CURRENT_POINTERS_ARE_AUTHORITY_POST_CUTOVER", "no"),
        ("CURRENT_POINTERS_ARE_PROJECTIONS", "yes"),
        ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
        ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
        ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
        ("SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE", "no"),
        ("ONE_CUTOVER_AUTHORITY", "yes"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
        ("GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
        ("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY", "yes"),
        ("PROJECTION_REBUILD_MUTATES_GRAPH", "no"),
        ("STALE_PROJECTION_MAY_BE_REBUILT", "yes"),
        ("STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT", "yes"),
        ("HEURISTIC_IDENTITY_MERGE_ALLOWED", "no"),
        ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no"),
        ("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING", "no"),
        ("RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "no"),
        ("RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE", "no"),
        ("FAILED_PRE_FLIP_CUTOVER_CHANGES_BINDING_AUTHORITY", "no"),
        ("POST_FLIP_PROJECTION_FAILURE_CHANGES_BINDING_AUTHORITY", "no"),
        ("MIGRATION_AMBIGUITY_AUTOMATICALLY_RESOLVED_BY_RUNTIME_BINDING", "no"),
        ("RUNTIME_BINDING_AUTOMATICALLY_RESOLVES_MIGRATION_AMBIGUITY", "no"),
        ("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT", "no"),
        ("CUTOVER_REPLAY_CREATES_SECOND_AUTHORITY_TRANSITION", "no"),
        ("MIGRATION_AMBIGUITY_BLOCKS_SAFE_READONLY_DIAGNOSIS", "no"),
        ("CUTOVER_STATE_BLOCKS_SAFE_INDEPENDENT_DIAGNOSIS", "no_where_technically_safe"),
        ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
        ("SOURCE_CODE_CHANGED", "no"),
    ):
        results.append(_check(invariants.get(key) == expected, f"invariants.{key} must be {expected}"))

    boundary = artifact.get("implementation_boundary", {})
    results.append(_check(boundary.get("source_mutation_scope") == "design_artifacts_only",
                          "implementation boundary must be design_artifacts_only"))
    results.append(_check(set(boundary.get("changed_paths", [])) == OWN_CHANGED_PATHS,
                          "implementation boundary changed_paths must be exactly the two A45 files"))
    results.append(_check(set(boundary.get("forbidden_paths", [])) == {
        "aota_forge/core/**",
        "aota_forge/adapters/**",
    }, "implementation boundary must forbid core and adapters"))
    for key in (
        "SOURCE_CODE_CHANGED",
        "GRAPH_IMPLEMENTATION_STARTED",
        "CUTOVER_IMPLEMENTATION_STARTED",
        "BINDING_IMPLEMENTATION_STARTED",
        "RECOVERY_IMPLEMENTATION_STARTED",
        "AUTHORITATIVE_GRAPH_WRITES_PERFORMED",
    ):
        results.append(_check(boundary.get(key) == "no", f"implementation boundary {key} must be no"))
    results.append(_check(len(boundary.get("implemented_nothing", [])) >= 8,
                          "implementation boundary must enumerate unimplemented runtime surfaces"))

    rc, changed = _changed_paths(repo_root)
    results.append(_check(rc == 0, "git changed-path inspection failed"))
    results.append(_check(changed == INTEGRATED_CHANGED_PATHS,
                          f"integrated changed paths must be exactly {sorted(INTEGRATED_CHANGED_PATHS)}; got {sorted(changed)}"))
    forbidden = sorted(path for path in changed if path.startswith(FORBIDDEN_SOURCE_PREFIXES))
    results.append(_check(not forbidden, f"source-code paths changed: {forbidden}"))

    for commit, path in SIBLING_PATCHES:
        current_path = repo_root / path
        expected_blob = _source_blob(repo_root, commit, path)
        results.append(_check(expected_blob is not None, f"source commit {commit} must contain {path}"))
        results.append(_check(expected_blob is not None and current_path.read_bytes() == expected_blob,
                              f"integrated sibling patch must match {commit}:{path} exactly"))
    ancestry = _git(repo_root, ["merge-base", "--is-ancestor", BASE_SHA, "HEAD"])
    results.append(_check(ancestry.returncode == 0, "integration HEAD must descend from A3"))


def _check_metrics(results: list[tuple[bool, str]], artifact: dict) -> None:
    metrics = artifact.get("report_metrics", {})
    for key, expected in (
        ("cross_scenario_count", 9),
        ("pointer_phase_count", 3),
        ("bounded_result_count", 5),
        ("source_patch_file_count", 4),
        ("negative_mutation_count", 7),
        ("source_error_code_count", 0),
        ("unsupported_success_claims", 0),
    ):
        results.append(_check(metrics.get(key) == expected, f"report_metrics.{key} must be {expected}"))


def run_checks(
    artifact: dict,
    repo_root: Path,
    a1: dict,
    a2: dict,
    a3: dict,
    a4: dict,
    a5: dict,
    a12: dict,
    matrix: dict,
) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    _check_identity(results, artifact)
    _check_accepted_lane_truth(results, artifact)
    _check_lane_inputs(results, a1, a2, a3, a4, a5, a12, matrix)
    _check_pre_post_models(results, artifact)
    _check_candidate_and_ambiguity(results, artifact)
    _check_projection_cutover(results, artifact)
    _check_pointers_readonly_idempotency(results, artifact)
    _check_scenarios_handoff(results, artifact)
    _check_implementation(results, artifact, repo_root)
    _check_metrics(results, artifact)
    return results


def _negative_mutations() -> list[tuple[str, str, callable]]:
    return [
        (
            "shadow graph authoritative binding before cutover",
            "PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND",
            lambda item: item["pre_cutover_binding_model"].__setitem__(
                "PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "yes"
            ),
        ),
        (
            "legacy pointer overrides graph after cutover",
            "POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH",
            lambda item: item["post_cutover_binding_model"].__setitem__(
                "POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH", "yes"
            ),
        ),
        (
            "runtime binding resolves migration ambiguity heuristically",
            "MIGRATION_AMBIGUITY_AUTOMATICALLY_RESOLVED_BY_RUNTIME_BINDING",
            lambda item: item["ambiguity_reconciliation"].__setitem__(
                "MIGRATION_AMBIGUITY_AUTOMATICALLY_RESOLVED_BY_RUNTIME_BINDING", "yes"
            ),
        ),
        (
            "projection rebuild selects a semantic candidate",
            "PROJECTION_REBUILD_CAN_SELECT_BETWEEN_MULTIPLE_SEMANTIC_SUBJECTS",
            lambda item: item["projection_recovery_contract"].__setitem__(
                "PROJECTION_REBUILD_CAN_SELECT_BETWEEN_MULTIPLE_SEMANTIC_SUBJECTS", "yes"
            ),
        ),
        (
            "recovery triggers authority cutover",
            "RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER",
            lambda item: item["cutover_recovery_boundary"].__setitem__(
                "RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "yes"
            ),
        ),
        (
            "unmigratable evidence auto-recovered",
            "RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE",
            lambda item: item["cutover_recovery_boundary"].__setitem__(
                "RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE", "yes"
            ),
        ),
        (
            "cutover replay performs a second authority transition",
            "CUTOVER_REPLAY_CREATES_SECOND_AUTHORITY_TRANSITION",
            lambda item: item["idempotency_cross_contract"].__setitem__(
                "CUTOVER_REPLAY_CREATES_SECOND_AUTHORITY_TRANSITION", "yes"
            ),
        ),
    ]


def run_self_test(
    artifact: dict,
    repo_root: Path,
    a1: dict,
    a2: dict,
    a3: dict,
    a4: dict,
    a5: dict,
    a12: dict,
    matrix: dict,
) -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for name, marker, mutate in _negative_mutations():
        candidate = copy.deepcopy(artifact)
        mutate(candidate)
        failures = [message for ok, message in run_checks(
            candidate, repo_root, a1, a2, a3, a4, a5, a12, matrix
        ) if not ok]
        outcomes.append({
            "case": name,
            "marker": marker,
            "rejected": bool(failures),
            "failure_count": len(failures),
        })
    return outcomes


def _summary(artifact: dict, results: list[tuple[bool, str]], negative: list[dict[str, object]] | None) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    negative_pass = negative is None or all(bool(case.get("rejected")) for case in negative)
    return {
        "artifact": "m3-a45-contract-reconciliation",
        "gate": artifact.get("gate"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "negative_guard_proof": {
            "case_count": 0 if negative is None else len(negative),
            "cases": negative,
            "verdict": "PASS" if negative_pass else "FAIL",
        } if negative is not None else None,
        "M3_A45_NEGATIVE_GUARD_PROOF": "PASS" if negative is not None and negative_pass else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A4/A5 serial contract reconciliation guard")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    parser.add_argument("--self-test", action="store_true", help="run seven negative guard mutations")
    parser.add_argument("--artifact", type=Path, help="path to the reconciliation artifact")
    parser.add_argument("--repo-root", type=Path, help="repository root; defaults to script parent.parent")
    args = parser.parse_args()

    repo_root = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    artifact_path = (args.artifact or repo_root / A45_REL).resolve()
    try:
        artifact = _read_json(artifact_path)
        loaded = {key: _read_json(repo_root / path) for key, path in INPUTS.items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"GUARD FAIL: unreadable input: {exc}")
        return 1

    results = run_checks(
        artifact,
        repo_root,
        loaded["a1"],
        loaded["a2"],
        loaded["a3"],
        loaded["a4"],
        loaded["a5"],
        loaded["a12"],
        loaded["matrix"],
    )
    negative = run_self_test(
        artifact,
        repo_root,
        loaded["a1"],
        loaded["a2"],
        loaded["a3"],
        loaded["a4"],
        loaded["a5"],
        loaded["a12"],
        loaded["matrix"],
    ) if args.self_test else None
    summary = _summary(artifact, results, negative)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        ok = summary["verdict"] == "PASS"
        if args.self_test:
            ok = ok and summary["M3_A45_NEGATIVE_GUARD_PROOF"] == "PASS"
        return 0 if ok else 1

    try:
        display_path = artifact_path.relative_to(repo_root)
    except ValueError:
        display_path = artifact_path
    print(f"artifact: {display_path}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if negative is not None:
        print(f"M3_A45_NEGATIVE_GUARD_PROOF: {summary['M3_A45_NEGATIVE_GUARD_PROOF']}")
        for case in negative:
            print(f"  {'REJECTED' if case['rejected'] else 'MISSED '}  {case['case']}")
    print(f"VERDICT: {summary['verdict']}")
    if args.self_test and summary["M3_A45_NEGATIVE_GUARD_PROOF"] != "PASS":
        return 1
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
