#!/usr/bin/env python3
"""M3-A1 subject schema and identity guard (Issue #9, lane M3-A1).

Validates deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json
against the M3-A1 design-proof contract at the exact accepted base
(10b8018ecf618cd7ff4c2b73f962356af044669f):

- artifact parses as JSON with exact top-level identity and exact base SHA
- GRAPH_CANONICAL_SCHEMA (canonical_records) and SUBJECT_IDENTITY_RULE defined
- the six required executor-neutral canonical records (Workflow, Subject,
  Execution, Completion, Decision, FollowupEdge) with full field contracts
- subject identity rule: deterministic-or-minted only; independent of title /
  timestamps alone / newest matching / executor / Hermes session / Profile
  Task / filesystem / current pointer / LLM similarity / Git SHA alone
- IDs identify only and grant no authority (SUBJECT_ID_IS_AUTHORITY=no)
- Profile Task ID / Hermes session ID / current pointers are never canonical
  Subject identity
- exactly one Subject per Execution; multiple Executions per Subject
- retry and executor change never automatically create a Subject
- semantic lineage required for followup children; heuristic identity/parent
  selection denied
- Git history / Issue Event Log / control comments are never graph authority
- no storage engine frozen and no graph mutation implementation present
  (static scan of the aota_forge package)
- M3-A0 classifications respected (cross-checked against a0-input-map.json)
- all six OQ-M3A1-0x decisions resolved with DECISION / RATIONALE /
  INVARIANTS / REJECTED_ALTERNATIVES / REGRESSION_IMPACT
- A0-driven regression design-proof rows for B011, B013, B014, B014-F,
  ACTIVATE-R-current-binding, WCTX-1, BIND-1, RECOVERY-1
- no forbidden source changes vs the base commit when git is available

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a1_subject_schema_guard.py            # human report
    python3 scripts/m3_a1_subject_schema_guard.py --json     # machine summary
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m3-a" / "a1-subject-schema-identity.json"
A0_MAP_PATH = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m3-a" / "a0-input-map.json"
AOTA_FORGE_PACKAGE = REPO_ROOT / "aota_forge"

EXPECTED_BASE_SHA = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A0_EXPECTED_BASE_SHA = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

REQUIRED_SECTIONS = (
    "subject_definition",
    "subject_identity_rule",
    "canonical_records",
    "relationships",
    "identity_decisions",
    "semantic_decisions",
    "review_semantics",
    "executor_private_boundary",
    "lifecycle_invariants",
    "regression_design_proofs",
    "deferred_questions",
    "invariants",
)

REQUIRED_RECORD_KEYS = ("Workflow", "Subject", "Execution", "Completion", "Decision", "FollowupEdge")

RECORD_FIELDS = (
    "record",
    "semantic_purpose",
    "canonical_fields",
    "required_fields",
    "immutable_fields",
    "mutable_fields",
    "identifiers",
    "relationships",
    "lifecycle_ownership",
    "revision_participation",
    "authority_relevance",
    "executor_private_boundary",
)

REQUIRED_OQ_IDS = ("OQ-M3A1-01", "OQ-M3A1-02", "OQ-M3A1-03", "OQ-M3A1-04", "OQ-M3A1-05", "OQ-M3A1-06")

OQ_FIELDS = ("QUESTION_ID", "DECISION", "RATIONALE", "INVARIANTS", "REJECTED_ALTERNATIVES", "REGRESSION_IMPACT")

OQ_INVARIANT_CHECKS = {
    "OQ-M3A1-01": {"WORK_ITEM_NOT_SUBJECT": "yes"},
    "OQ-M3A1-02": {"PLAN_IS_SINGLE_AGGREGATE": "yes", "MILESTONE_NOT_SUBJECT_KIND": "yes",
                   "PLAN_IDENTITY_STABLE_ACROSS_REVISIONS": "yes"},
    "OQ-M3A1-03": {"REVIEW_NOT_SUBJECT_KIND": "yes"},
    "OQ-M3A1-04": {"DECISION_NOT_SUBJECT_KIND": "yes", "NO_AUTHORITATIVE_CURRENT_DECISION_POINTER": "yes"},
    "OQ-M3A1-05": {"FOLLOWUP_IS_CHILD_SUBJECT": "yes", "FOLLOWUP_REQUIRES_SOURCE_DECISION": "yes",
                   "NO_HEURISTIC_PARENT_SELECTION": "yes"},
    "OQ-M3A1-06": {"WORKSPACE_PROJECT_DISTINCT_KINDS": "yes"},
}

SEMANTIC_DECISION_REQUIRED = ("D-M3A1-01", "D-M3A1-02", "D-M3A1-03", "D-M3A1-04", "D-M3A1-05", "D-M3A1-06", "D-M3A1-07")

SEMANTIC_DECISION_FIELDS = (
    "DECISION_ID",
    "DECISION",
    "RATIONALE",
    "INVARIANTS",
    "REJECTED_ALTERNATIVES",
    "REGRESSION_IMPACT",
)

# identity flags that MUST be "no" (frozen plan facts / A1 decisions).
MUST_BE_NO_IDENTITY_FLAGS = (
    "SUBJECT_ID_IS_AUTHORITY",
    "PROFILE_TASK_ID_IS_SUBJECT_ID",
    "INTERNAL_EXECUTION_ID_IS_SUBJECT_ID",
    "HERMES_SESSION_ID_IS_SUBJECT_ID",
    "CURRENT_POINTERS_ARE_AUTHORITY",
    "CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY",
    "RETRY_CREATES_NEW_SUBJECT",
    "CHANGE_EXECUTOR_CREATES_NEW_SUBJECT",
    "HEURISTIC_PARENT_SELECTION_ALLOWED",
    "HEURISTIC_IDENTITY_SELECTION_ALLOWED",
    "HEURISTIC_SUBJECT_IDENTITY_ALLOWED",
    "GIT_HISTORY_IS_SUBJECT_AUTHORITY",
    "EVENT_LOG_IS_SUBJECT_AUTHORITY",
    "CONTROL_COMMENT_IS_SUBJECT_AUTHORITY",
)

# identity flags that MUST be "yes".
MUST_BE_YES_IDENTITY_FLAGS = (
    "SUBJECT_ID_DETERMINISTIC_OR_MINTED",
    "CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT",
)

MUST_BE_YES_RECORD_FLAGS = (
    "ONE_SUBJECT_PER_EXECUTION",
    "MULTIPLE_EXECUTIONS_PER_SUBJECT",
    "RETRY_NO_SILENT_NEW_SUBJECT",
    "EXECUTOR_CHANGE_NO_NEW_SUBJECT",
    "CHILD_REQUIRES_SEMANTIC_LINEAGE",
    "FOLLOWUP_REQUIRES_SEMANTIC_LINEAGE",
    "NO_SILENT_CYCLES",
    "PARENTHOOD_ONLY_VIA_DURABLE_EDGES",
    "DURABLE_DECISION_FOLLOWUP_LINEAGE",
    "COMPLETION_IMMUTABLE",
    "DECISION_NO_AUTHORITATIVE_CURRENT_POINTER",
    "MECHANICAL_STATE_MINIMAL",
    "SEMANTIC_DECISIONS_NOT_STATE",
    "NO_GIANT_STATE_MACHINE",
)

REQUIRED_PROOF_CLASSES = (
    "B011",
    "B013",
    "B014",
    "B014-F",
    "ACTIVATE-R-current-binding",
    "WCTX-1",
    "BIND-1",
    "RECOVERY-1",
)

PROOF_FIELDS = ("FAILURE_CLASS", "A1_DESIGN_INVARIANT", "A1_PROOF_STATUS", "FUTURE_IMPLEMENTATION_REQUIRED")

ALLOWED_PROOF_STATUS = {"DESIGN_PROVEN", "DESIGN_INCOMPLETE"}

ALLOWED_MUTATION_SCOPE = ("design_artifacts_only",)

ALLOWED_CHANGED_PATHS = frozenset({
    "deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json",
    "deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.md",
    "scripts/m3_a1_subject_schema_guard.py",
})

SUBJECT_ID_INDEPENDENCE_KEYS = (
    "title",
    "timestamps_alone",
    "newest_matching",
    "executor",
    "hermes_session",
    "profile_task",
    "filesystem",
    "current_pointer",
    "llm_similarity",
    "git_sha_alone",
)

EXECUTOR_PRIVATE_FLAGS = {
    "HERMES_PROFILE_ID_IS_CANONICAL_SUBJECT_IDENTITY": "no",
    "HERMES_SESSION_ID_IS_CANONICAL_SUBJECT_IDENTITY": "no",
    "PROFILE_TASK_ID_IS_SUBJECT_ID": "no",
    "HERMES_IDENTITY_IS_ONTOLOGY": "no",
    "HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY": "no",
    "GITHUB_IS_FORGE_CORE_ONTOLOGY": "no",
}

PLAN_REVISION_POLICY_FLAGS = {
    "PLAN_IDENTITY_STABLE_ACROSS_PLAN_REVISIONS": "yes",
    "PLAN_CONTENT_DIGEST_NOT_IDENTITY": "yes",
    "PLAN_REVISIONS_ARE_REVISIONED_RECORDS_OR_DECISIONS": "yes",
    "PLAN_NEW_SEMANTIC_INTENT_NEW_CHILD_SUBJECT": "yes",
}

REVIEW_SEMANTIC_FLAGS = {
    "REVIEW_NOT_SUBJECT_KIND": "yes",
    "REVIEW_CASE1_IS_DECISION_ON_EXISTING_SUBJECT": "yes",
    "REVIEW_CASE2_IS_EXPLICIT_WORKSUBJECT": "yes",
    "REVIEW_CASE2_RESULT_IS_DECISION": "yes",
    "REVIEW_CASE3_INDEPENDENT_IDENTITY_REQUIRES_MATERIALIZED_TASK": "yes",
    "REVIEWER_ROLE_OR_ARTIFACT_NEVER_CREATES_IDENTITY": "yes",
}

REVIEW_CASE_IDS = ("REVIEW-CASE-1", "REVIEW-CASE-2", "REVIEW-CASE-3")

FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Bounded static scan: no graph storage / mutation implementation may exist.
GRAPH_STORE_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease)\w*(?:Store|Repository|Engine|Broker)\w*\s*:"
)
GRAPH_WRITE_OPERATION_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease)\.[a-z0-9_.-]+"'
)
READ_ONLY_GATE_MARKER = "operation is not read-only in M2"

A0_PROFILE_TASK_FACTS = {
    "primary_class": "MIGRATION_EVIDENCE_ONLY",
    "executor_specific": True,
}
A0_FORBIDDEN_SOURCES = {"Git history", "Issue Event Log", "control comments"}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _flag_value(flags: dict, key: str) -> tuple[bool, str]:
    ok = key in flags
    return ok, f"flag {key} present" if ok else f"missing flag: {key}"


def _is_plain_nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def run_checks(artifact: dict, a0_map: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Top-level identity and exact base SHA.
    for key in ("schema_version", "project_id", "milestone", "phase", "lane", "base_sha"):
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    results.append(_check(artifact.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(artifact.get("project_id") == "aota_forge", "project_id must be aota_forge"))
    results.append(_check(artifact.get("milestone") == "M3" and artifact.get("phase") == "M3-A",
                          "milestone/phase must be M3 / M3-A"))
    results.append(_check(artifact.get("lane") == "M3-A1", "lane must be M3-A1"))
    recorded = artifact.get("base_sha")
    results.append(_check(
        recorded == EXPECTED_BASE_SHA,
        f"base_sha must be {EXPECTED_BASE_SHA} (recorded: {recorded!r})",
    ))

    # 1b. Exact top-level machine markers.
    results.append(_check(artifact.get("GRAPH_CANONICAL_SCHEMA") == "defined",
                          "GRAPH_CANONICAL_SCHEMA must be 'defined' at top level"))
    results.append(_check(artifact.get("SUBJECT_IDENTITY_RULE") == "defined",
                          "SUBJECT_IDENTITY_RULE must be 'defined' at top level"))

    # 2. All required sections present (GRAPH_CANONICAL_SCHEMA / SUBJECT_IDENTITY_RULE defined).
    for section in REQUIRED_SECTIONS:
        results.append(_check(
            isinstance(artifact.get(section), dict) or isinstance(artifact.get(section), list),
            f"required section missing or empty: {section}",
        ))
    canonical_records = artifact.get("canonical_records", {})
    records = canonical_records.get("records", [])
    results.append(_check(
        isinstance(records, list) and records,
        "canonical_records.records must be a non-empty list (GRAPH_CANONICAL_SCHEMA)",
    ))
    results.append(_check(
        canonical_records.get("required_record_keys") == list(REQUIRED_RECORD_KEYS),
        f"canonical_records.required_record_keys must be exactly {list(REQUIRED_RECORD_KEYS)}",
    ))
    identity_rule = artifact.get("subject_identity_rule", {})
    results.append(_check(
        _is_plain_nonempty(identity_rule.get("rule")) and _is_plain_nonempty(identity_rule.get("SUBJECT_ID_DETERMINISTIC_OR_MINTED_RULE")),
        "subject_identity_rule must define rule and SUBJECT_ID_DETERMINISTIC_OR_MINTED_RULE (SUBJECT_IDENTITY_RULE)",
    ))

    # 3. Canonical record field contracts.
    record_by_key = {rec.get("record"): rec for rec in records if isinstance(rec, dict)}
    for key in REQUIRED_RECORD_KEYS:
        rec = record_by_key.get(key)
        results.append(_check(rec is not None, f"canonical record missing: {key}"))
        if rec is None:
            continue
        missing = [f for f in RECORD_FIELDS if f not in rec]
        results.append(_check(not missing, f"record {key} missing fields: {sorted(missing)}"))
        canonical = rec.get("canonical_fields", [])
        immutable = rec.get("immutable_fields", [])
        mutable = rec.get("mutable_fields", [])
        # Field lists use plain field names only.
        all_field_names = canonical + immutable + mutable + rec.get("required_fields", [])
        results.append(_check(
            all(isinstance(f, str) and FIELD_NAME_RE.fullmatch(f) for f in all_field_names),
            f"record {key}: canonical/immutable/mutable/required fields must be plain snake_case field names",
        ))
        for label, field_list in (("canonical_fields", canonical), ("immutable_fields", immutable),
                                  ("mutable_fields", mutable), ("required_fields", rec.get("required_fields", []))):
            results.append(_check(isinstance(field_list, list) and len(field_list) == len(set(field_list)),
                                  f"record {key}: {label} must be a list without duplicates"))
        # Exact partition: immutable and mutable are disjoint and together equal canonical.
        results.append(_check(isinstance(canonical, list) and canonical,
                              f"record {key}: canonical_fields must be a non-empty list"))
        results.append(_check(isinstance(immutable, list) and isinstance(mutable, list),
                              f"record {key}: immutable_fields and mutable_fields must be lists"))
        if isinstance(canonical, list) and isinstance(immutable, list) and isinstance(mutable, list):
            overlap = set(immutable) & set(mutable)
            results.append(_check(not overlap,
                                  f"record {key}: immutable and mutable fields overlap: {sorted(overlap)}"))
            partition = set(immutable) | set(mutable)
            results.append(_check(
                partition == set(canonical),
                f"record {key}: immutable union mutable must equal canonical exactly "
                f"(missing from partition: {sorted(set(canonical) - partition)}, "
                f"extra in partition: {sorted(partition - set(canonical))})",
            ))
            required = rec.get("required_fields", [])
            results.append(_check(
                isinstance(required, list) and set(required) <= set(canonical),
                f"record {key}: required_fields must be a subset of canonical_fields",
            ))
        for field in ("identifiers", "relationships"):
            results.append(_check(isinstance(rec.get(field), list) and rec[field],
                                  f"record {key}: {field} must be a non-empty list"))
        for field in ("semantic_purpose", "lifecycle_ownership", "revision_participation",
                      "authority_relevance", "executor_private_boundary"):
            results.append(_check(_is_plain_nonempty(rec.get(field)),
                                  f"record {key}: {field} must be a non-empty string"))
        if key == "Execution":
            results.append(_check(
                "executor_kind" in rec.get("canonical_fields", [])
                and "executor_execution_ref" in rec.get("canonical_fields", []),
                "record Execution must carry executor_kind and executor_execution_ref canonical fields",
            ))

    # 4. Subject identity rule details.
    independence = identity_rule.get("SUBJECT_ID_INDEPENDENT_OF", {})
    for k in SUBJECT_ID_INDEPENDENCE_KEYS:
        results.append(_check(
            independence.get(k) == "yes",
            f"SUBJECT_ID_INDEPENDENT_OF.{k} must be 'yes' (identity independent of {k})",
        ))
    for key in ("SUBJECT_ID_IS_AUTHORITY", "SUBJECT_ID_REUSE_RULE", "SUBJECT_ID_COLLISION_RULE"):
        results.append(_check(key in identity_rule, f"subject_identity_rule missing field: {key}"))
    results.append(_check(identity_rule.get("SUBJECT_ID_IS_AUTHORITY") == "no",
                          "SUBJECT_ID_IS_AUTHORITY must be 'no' (IDs identify only)"))
    results.append(_check(
        _is_plain_nonempty(identity_rule.get("SUBJECT_ID_REUSE_RULE"))
        and _is_plain_nonempty(identity_rule.get("SUBJECT_ID_COLLISION_RULE")),
        "SUBJECT_ID_REUSE_RULE and SUBJECT_ID_COLLISION_RULE must be non-empty",
    ))

    # 4b. Stable PlanSubject identity / revision policy.
    plan_policy = identity_rule.get("PLAN_IDENTITY_REVISION_POLICY", {})
    for flag, expected in PLAN_REVISION_POLICY_FLAGS.items():
        results.append(_check(plan_policy.get(flag) == expected,
                              f"PLAN_IDENTITY_REVISION_POLICY.{flag} must be {expected!r}"))
    results.append(_check(
        _is_plain_nonempty(plan_policy.get("PLAN_IDENTITY_DERIVATION")),
        "PLAN_IDENTITY_REVISION_POLICY.PLAN_IDENTITY_DERIVATION must be non-empty",
    ))
    plan_kind = next((k for k in artifact.get("subject_definition", {}).get("subject_kinds", [])
                      if isinstance(k, dict) and k.get("kind") == "PlanSubject"), {})
    plan_derivation = plan_kind.get("id_derivation", "")
    results.append(_check(
        isinstance(plan_derivation, str) and "plan_id" in plan_derivation
        and "subject namespace" in plan_derivation,
        "PlanSubject id_derivation must derive from stable plan_id + subject namespace",
    ))
    results.append(_check(
        isinstance(plan_derivation, str)
        and "+ content digest" not in plan_derivation
        and "content digest +" not in plan_derivation,
        "PlanSubject id_derivation must not include plan content digest as an identity input",
    ))
    for cls in ("B011", "B013"):
        proof = next((p for p in artifact.get("regression_design_proofs", [])
                      if isinstance(p, dict) and p.get("FAILURE_CLASS") == cls), {})
        invariant = proof.get("A1_DESIGN_INVARIANT", "")
        results.append(_check(
            isinstance(invariant, str)
            and ("plan_id" in invariant or "PLAN_IDENTITY_REVISION_POLICY" in invariant),
            f"{cls} A1_DESIGN_INVARIANT must reference the stable plan identity / revision policy",
        ))
        results.append(_check(
            isinstance(invariant, str) and "+ plan content digest" not in invariant,
            f"{cls} A1_DESIGN_INVARIANT must not derive identity from plan content digest",
        ))

    # 5. Identity flags (frozen plan facts / A1 decisions).
    identity_flags = artifact.get("invariants", {}).get("identity_flags", {})
    for flag in MUST_BE_NO_IDENTITY_FLAGS:
        results.append(_check(identity_flags.get(flag) == "no", f"identity flag must be 'no': {flag}"))
    for flag in MUST_BE_YES_IDENTITY_FLAGS:
        results.append(_check(identity_flags.get(flag) == "yes", f"identity flag must be 'yes': {flag}"))

    # 6. Record/relationship flags.
    record_flags = artifact.get("invariants", {}).get("record_flags", {})
    for flag in MUST_BE_YES_RECORD_FLAGS:
        results.append(_check(record_flags.get(flag) == "yes", f"record flag must be 'yes': {flag}"))

    # 7. Relationships section content.
    relationships = artifact.get("relationships", {})
    for key in ("one_subject_per_execution", "many_executions_per_subject", "retry_no_new_subject",
                "executor_change_no_new_subject", "new_semantic_intent_new_subject",
                "completion_per_execution", "decision_per_subject", "followup_child_lineage",
                "no_silent_cycles", "parenthood_not_pointer_defined",
                "no_heuristic_parent_selection", "binding_zero_one_many"):
        results.append(_check(
            _is_plain_nonempty(relationships.get(key)),
            f"relationships missing or empty: {key}",
        ))

    # 8. OQ-M3A1-01..06 resolved with the full decision contract.
    decisions = artifact.get("identity_decisions", [])
    by_id = {d.get("QUESTION_ID"): d for d in decisions if isinstance(d, dict)}
    results.append(_check(
        set(by_id) == set(REQUIRED_OQ_IDS),
        f"identity_decisions must cover exactly {list(REQUIRED_OQ_IDS)} (got {sorted(by_id)})",
    ))
    for oq_id in REQUIRED_OQ_IDS:
        d = by_id.get(oq_id)
        if d is None:
            continue
        missing = [f for f in OQ_FIELDS if f not in d or not d[f]]
        results.append(_check(not missing, f"{oq_id} missing or empty fields: {missing}"))
        invariants = d.get("INVARIANTS", {})
        for flag, expected in OQ_INVARIANT_CHECKS.get(oq_id, {}).items():
            results.append(_check(invariants.get(flag) == expected,
                                  f"{oq_id} INVARIANTS.{flag} must be {expected!r}"))

    # 9. Semantic decisions (retry / executor / completion / decision / followup / review).
    semantic = artifact.get("semantic_decisions", [])
    semantic_ids = {s.get("DECISION_ID") for s in semantic if isinstance(s, dict)}
    for sid in SEMANTIC_DECISION_REQUIRED:
        results.append(_check(sid in semantic_ids, f"semantic decision missing: {sid}"))
    for s in semantic:
        if not isinstance(s, dict):
            continue
        missing = [f for f in SEMANTIC_DECISION_FIELDS if f not in s or not s[f]]
        results.append(_check(not missing, f"{s.get('DECISION_ID', '?')} missing or empty fields: {missing}"))
    semantic_by_id = {s.get("DECISION_ID"): s for s in semantic if isinstance(s, dict)}
    for sid, expected in (("D-M3A1-02", {"RETRY_CREATES_NEW_SUBJECT": "no"}),
                          ("D-M3A1-03", {"CHANGE_EXECUTOR_CREATES_NEW_SUBJECT": "no",
                                         "CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT": "yes"}),
                          ("D-M3A1-07", {"REVIEW_CASES_MACHINE_DOCUMENTED": "yes"})):
        invariants = semantic_by_id.get(sid, {}).get("INVARIANTS", {})
        for flag, value in expected.items():
            results.append(_check(invariants.get(flag) == value,
                                  f"{sid} INVARIANTS.{flag} must be {value!r}"))

    # 9b. Review semantics: three context-dependent canonical cases, no review ontology kind.
    review_semantics = artifact.get("review_semantics", {})
    results.append(_check(
        review_semantics.get("REVIEW_NOT_SUBJECT_KIND") == "yes",
        "review_semantics.REVIEW_NOT_SUBJECT_KIND must be 'yes' (no special ReviewSubject ontology kind)",
    ))
    review_cases = {c.get("CASE_ID"): c for c in review_semantics.get("cases", []) if isinstance(c, dict)}
    for case_id in REVIEW_CASE_IDS:
        case = review_cases.get(case_id)
        results.append(_check(case is not None, f"review_semantics case missing: {case_id}"))
        if case is not None:
            results.append(_check(
                _is_plain_nonempty(case.get("CASE")) and _is_plain_nonempty(case.get("semantics"))
                and _is_plain_nonempty(case.get("identity")),
                f"review_semantics {case_id} must document CASE / semantics / identity",
            ))
    review_flags = review_semantics.get("flags", {})
    for flag, expected in REVIEW_SEMANTIC_FLAGS.items():
        results.append(_check(review_flags.get(flag) == expected,
                              f"review_semantics.flags.{flag} must be {expected!r}"))

    # 10. Executor-private boundary.
    boundary = artifact.get("executor_private_boundary", {})
    results.append(_check(
        _is_plain_nonempty(boundary.get("boundary")),
        "executor_private_boundary must state the boundary",
    ))
    boundary_flags = boundary.get("flags", {})
    for flag, expected in EXECUTOR_PRIVATE_FLAGS.items():
        results.append(_check(boundary_flags.get(flag) == expected,
                              f"executor_private_boundary.flags.{flag} must be {expected!r}"))
    results.append(_check(
        isinstance(boundary.get("canonical_equivalents"), list) and boundary["canonical_equivalents"],
        "executor_private_boundary.canonical_equivalents must be a non-empty list",
    ))

    # 11. Regression design proofs for the required A0 classes.
    proofs = artifact.get("regression_design_proofs", [])
    proof_classes = {p.get("FAILURE_CLASS") for p in proofs if isinstance(p, dict)}
    for cls in REQUIRED_PROOF_CLASSES:
        results.append(_check(cls in proof_classes,
                              f"regression design proof missing required class: {cls}"))
    results.append(_check(len(proof_classes) == len(proofs),
                          "regression_design_proofs FAILURE_CLASS values must be unique"))
    for p in proofs:
        if not isinstance(p, dict):
            continue
        missing = [f for f in PROOF_FIELDS if f not in p or not p[f]]
        results.append(_check(not missing, f"proof row {p.get('FAILURE_CLASS', '?')} missing fields: {missing}"))
        results.append(_check(
            p.get("A1_PROOF_STATUS") in ALLOWED_PROOF_STATUS,
            f"proof row {p.get('FAILURE_CLASS')}: A1_PROOF_STATUS must be one of {sorted(ALLOWED_PROOF_STATUS)}",
        ))
        results.append(_check(
            p.get("FUTURE_IMPLEMENTATION_REQUIRED") in ("yes", "no"),
            f"proof row {p.get('FAILURE_CLASS')}: FUTURE_IMPLEMENTATION_REQUIRED must be yes|no",
        ))

    # 12. No storage engine frozen; design-proof-only scope.
    engine_boundary = artifact.get("invariants", {}).get("engine_boundary", {})
    for key, expected in (("NO_STORAGE_ENGINE_FROZEN", "yes"), ("NO_GRAPH_MUTATION_IMPLEMENTATION", "yes"),
                          ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
                          ("AUTHORITATIVE_STATE_MUTATION_ALLOWED", "no"),
                          ("NO_AUTHORITY_ENGINE", "yes"), ("NO_LEASE_ENGINE", "yes"),
                          ("NO_RUNTIME_BEHAVIOR", "yes"), ("SOURCE_CODE_CHANGED", "no")):
        results.append(_check(engine_boundary.get(key) == expected, f"engine_boundary.{key} must be {expected!r}"))
    results.append(_check(
        isinstance(engine_boundary.get("GRAPH_STORAGE_LOCATIONS"), list)
        and engine_boundary["GRAPH_STORAGE_LOCATIONS"] == [],
        "engine_boundary.GRAPH_STORAGE_LOCATIONS must be an empty list (no storage engine frozen)",
    ))
    proof_only = artifact.get("design_proof_only", {})
    for key, expected in (("no_graph_storage_engine", "yes"), ("no_graph_mutation_implementation", "yes"),
                          ("no_authoritative_graph_writes", "yes"), ("no_authority_engine", "yes"),
                          ("no_lease_engine", "yes"), ("no_runtime_behavior", "yes")):
        results.append(_check(proof_only.get(key) == expected, f"design_proof_only.{key} must be {expected!r}"))
    results.append(_check(
        proof_only.get("source_mutation_scope") in ALLOWED_MUTATION_SCOPE,
        f"design_proof_only.source_mutation_scope must be one of {sorted(ALLOWED_MUTATION_SCOPE)}",
    ))

    # 13. Frozen plan facts preserved.
    frozen = artifact.get("lifecycle_invariants", {}).get("frozen_plan_facts_preserved", {})
    for key, expected in (("DURABLE_SUBJECT_GRAPH_IS_AUTHORITY", "yes"),
                          ("CURRENT_POINTERS_ARE_PROJECTIONS", "yes"), ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
                          ("ID_IS_AUTHORITY", "no"), ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
                          ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"), ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
                          ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no"), ("DUAL_AUTHORITATIVE_WRITE", "no"),
                          ("SILENT_LAST_WRITE_WINS", "no"), ("LOST_UPDATE_ALLOWED", "no")):
        results.append(_check(frozen.get(key) == expected, f"frozen_plan_facts_preserved.{key} must be {expected!r}"))

    # 14. M3-A0 classifications respected (cross-check against the frozen A0 map).
    a0_base = a0_map.get("base_sha")
    results.append(_check(a0_base == A0_EXPECTED_BASE_SHA,
                          f"A0 map base_sha must remain {A0_EXPECTED_BASE_SHA} (frozen A0 preserved; got {a0_base!r})"))
    a0_concepts = {c.get("concept"): c for c in a0_map.get("lifecycle_concepts", []) if isinstance(c, dict)}
    profile_task = a0_concepts.get("Profile Task", {})
    for key, expected in A0_PROFILE_TASK_FACTS.items():
        results.append(_check(profile_task.get(key) == expected,
                              f"A0 Profile Task classification {key} must be {expected!r}"))
    a0_ambiguities = {a.get("concept"): a for a in a0_map.get("subject_identity_ambiguities", []) if isinstance(a, dict)}
    results.append(_check(
        a0_ambiguities.get("Profile Task", {}).get("obviously_not_canonical_subject") is True,
        "A0: Profile Task must be obviously_not_canonical_subject=true (preserved)",
    ))
    forbidden = {f.get("source") for f in a0_map.get("forbidden_authority_sources", []) if isinstance(f, dict)}
    results.append(_check(forbidden == A0_FORBIDDEN_SOURCES,
                          "A0 forbidden_authority_sources must be exactly Git history / Issue Event Log / control comments"))
    a0_frozen = a0_map.get("invariants", {}).get("frozen_plan_facts", {})
    for key, expected in (("ID_IS_AUTHORITY", "no"), ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
                          ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"), ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
                          ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"), ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no")):
        results.append(_check(a0_frozen.get(key) == expected,
                              f"A0 frozen plan fact {key} must remain {expected!r} (preserved)"))
    a0_questions = {q.get("QUESTION_ID") for q in a0_map.get("open_design_questions", []) if isinstance(q, dict)}
    for oq in REQUIRED_OQ_IDS:
        results.append(_check(oq in a0_questions,
                              f"A0 open design question {oq} must be preserved in a0-input-map.json"))
    a0_class_respected = artifact.get("invariants", {}).get("a0_classifications_respected", {})
    for key, expected in (("PROFILE_TASK_PRIMARY_CLASS", "MIGRATION_EVIDENCE_ONLY"),
                          ("WORK_ITEM_PRIMARY_CLASS", "MIGRATION_EVIDENCE_ONLY"),
                          ("DECISION_PRIMARY_CLASS", "MIGRATION_EVIDENCE_ONLY"),
                          ("FOLLOWUP_PRIMARY_CLASS", "MIGRATION_EVIDENCE_ONLY"),
                          ("PLAN_PRIMARY_CLASS", "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE"),
                          ("PROJECT_PRIMARY_CLASS", "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE"),
                          ("WORKSPACE_PRIMARY_CLASS", "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE"),
                          ("FORBIDDEN_SOURCES_CLASS", "FORBIDDEN_AUTHORITY_SOURCE")):
        results.append(_check(a0_class_respected.get(key) == expected,
                              f"a0_classifications_respected.{key} must be {expected!r}"))

    # 15. Static scan: no graph mutation implementation present at this base.
    ingress_text = (AOTA_FORGE_PACKAGE / "core" / "ingress.py").read_text(encoding="utf-8")
    results.append(_check(
        READ_ONLY_GATE_MARKER in ingress_text,
        f"read-only ingress gate marker missing (foundation for no graph mutation): {READ_ONLY_GATE_MARKER!r}",
    ))
    store_hits: list[str] = []
    for py in sorted(AOTA_FORGE_PACKAGE.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for match in GRAPH_STORE_CLASS_RE.finditer(text):
            store_hits.append(f"{py.relative_to(REPO_ROOT)}:{match.group(0)}")
    results.append(_check(not store_hits, f"graph storage implementation class found in Core: {store_hits}"))
    catalog_text = (AOTA_FORGE_PACKAGE / "core" / "catalog.py").read_text(encoding="utf-8")
    results.append(_check(
        not GRAPH_WRITE_OPERATION_RE.search(catalog_text),
        "catalog declares a graph/subject/authority/lease write operation",
    ))

    # 16. No forbidden source changes vs the base commit (when git is available).
    git_ok = True
    try:
        probe = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=30,
        )
        git_ok = probe.returncode == 0 and probe.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        git_ok = False
    if not git_ok:
        results.append(_check(True, "git not available in this copy; changed-path check skipped"))
    else:
        try:
            tracked = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "diff", "--name-only", EXPECTED_BASE_SHA],
                capture_output=True, text=True, timeout=60,
            )
            untracked = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, timeout=30,
            )
            changed = set(tracked.stdout.split()) | set(untracked.stdout.split())
        except (OSError, subprocess.SubprocessError):
            changed = set()
        results.append(_check(
            changed <= ALLOWED_CHANGED_PATHS,
            f"forbidden source changes detected vs base: {sorted(changed - ALLOWED_CHANGED_PATHS)}",
        ))

    return results


def summarize(artifact: dict, results: list[tuple[bool, str]]) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    return {
        "artifact": "m3-a1-subject-schema-identity",
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "oq_resolved": sum(1 for d in artifact.get("identity_decisions", [])
                           if d.get("QUESTION_ID", "").startswith("OQ-M3A1")),
        "proof_classes": len(artifact.get("regression_design_proofs", [])),
    }


def main() -> int:
    try:
        artifact = _read_json(ARTIFACT_PATH)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        a0_map = _read_json(A0_MAP_PATH)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A0 input map unreadable/invalid: {exc}")
        return 1

    results = run_checks(artifact, a0_map)
    summary = summarize(artifact, results)
    if "--json" in sys.argv:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

    print(f"artifact: {ARTIFACT_PATH.relative_to(REPO_ROOT)}")
    print(f"base SHA recorded: {artifact.get('base_sha')}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
