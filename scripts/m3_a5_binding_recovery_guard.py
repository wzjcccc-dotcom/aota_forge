#!/usr/bin/env python3
"""M3-A5 subject binding and recovery guard (Issue #9, lane M3-A5).

Design-only freeze guard.  Validates
deploy/evidence/issues/9/m3-a/a5-binding-recovery.json at the exact M3-A5
base 2485a8d51b6fb5722a0da02e9dc93e5615401277 (the M3-A3 commit) against the
frozen M3-A1/A2/A12/A3 contracts:

- exact top-level identity, base_sha, m3_a_common_base, input commits
  (A3 input commit must be 2485a8d51b6fb5722a0da02e9dc93e5615401277), and
  design-only provenance
- candidate validity model: deterministic validity evaluated BEFORE the count,
  allowed validity filters only, forbidden recency/newest / title-text
  similarity / nearest timestamp / path proximity / first match / LLM
  similarity signals, no hidden many->one reduction
- zero/one/many rule: ZERO / ONE / MANY semantics; unique candidate alone is
  insufficient (materialized Decision + valid authority + valid
  revision/predecessor required); MANY never auto-selects
- semantic decision materialization: canonical Decision, correct owning
  Subject, explicit followup category authorization, not superseded/invalid,
  deterministic lineage; Authority Engine verifies and never infers
- binding kinds: per-kind BINDING_KIND / CANDIDATE_SOURCE / VALIDITY_RULE /
  ZERO_RESULT / ONE_RESULT / MANY_RESULT / AUTOMATION_ALLOWED for the six
  required kinds; no single generic algorithm
- recovery boundary: may / may_not lists, recovery vs binding distinction,
  no semantic reasoning; projection recovery with stale-pointer visibility
  and no SPEC/Profile Task escalation; read-only diagnostic exemption without
  Subject binding or capability lease (except bounded privileged operations)
- conflict taxonomy: the eight canonical distinct codes
- revision/authority relation: unique candidate never bypasses CAS, authority,
  or predecessor validation; read-only resolution implies no mutation authority
- idempotent recovery: replay never duplicates semantic effects
- failure policy: fail closed, safe diagnosis, NEEDS_SEMANTIC_CHOICE for
  ambiguity, bounded explicit errors, no blind replay
- machine-readable A5-S1..A5-S10 design scenarios (S9 newer candidate still
  ambiguous, S10 replay no duplicate)
- regression design proofs for B011/B013/B014/ACTIVATE-R-current-binding/
  ACTIVATE-R-host-inspection-escalation/WCTX-1/BIND-1/RECOVERY-1 with
  A5_BINDING_OR_RECOVERY_INVARIANT / DESIGN_PROOF / IMPLEMENTATION_REQUIRED /
  FUTURE_OWNER and corpus dispositions NOT_YET_IMPLEMENTED (no implementation
  in this lane)
- invariants: preserved contract flags, all frozen binding flags, engine
  boundary (SOURCE_CODE_CHANGED=no, BINDING_IMPLEMENTATION_STARTED=no,
  RECOVERY_IMPLEMENTATION_STARTED=no, AUTHORITATIVE_GRAPH_WRITES_PERFORMED=no),
  A5 design-only boundary
- frozen M3-A3 truth markers cross-checked against the checked-out A3 artifact
- engine boundary: no binding/recovery/projection/authority/CAS implementation
  in the aota_forge package (static scan), read-only ingress gate preserved
- changed-path check vs the exact base: only the two M3-A5 files

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a5_binding_recovery_guard.py
    python3 scripts/m3_a5_binding_recovery_guard.py --json
    python3 scripts/m3_a5_binding_recovery_guard.py --artifact /tmp/x.json
    python3 scripts/m3_a5_binding_recovery_guard.py --repo-root /path/to/worktree
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_BASE_SHA = "2485a8d51b6fb5722a0da02e9dc93e5615401277"
M3_A_COMMON_BASE = "2485a8d51b6fb5722a0da02e9dc93e5615401277"
A3_INPUT_COMMIT = "2485a8d51b6fb5722a0da02e9dc93e5615401277"

A5_REL = Path("deploy/evidence/issues/9/m3-a/a5-binding-recovery.json")
GUARD_REL = Path("scripts/m3_a5_binding_recovery_guard.py")
A3_ART_REL = Path("deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json")
A12_ART_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

REQUIRED_SECTIONS = [
    "candidate_validity_model",
    "zero_one_many_rule",
    "semantic_decision_materialization",
    "binding_kinds",
    "recovery_boundary",
    "projection_recovery",
    "readonly_diagnostic_exemption",
    "conflict_taxonomy",
    "revision_authority_relation",
    "idempotent_recovery",
    "failure_policy",
    "design_scenarios",
    "regression_design_proofs",
    "deferred_questions",
    "invariants",
]

# Frozen flags the A5 artifact must expose (exact values).
BINDING_FLAGS_EXPECTED = {
    "HEURISTIC_SUBJECT_SELECTION_ALLOWED": "no",
    "HEURISTIC_PRE_FILTER_ALLOWED": "no",
    "CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER": "yes",
    "NO_HIDDEN_REDUCTION_MANY_TO_ONE": "yes",
    "UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION": "no",
    "UNIQUE_CANDIDATE_BYPASSES_AUTHORITY": "no",
    "UNIQUE_CANDIDATE_BYPASSES_CAS": "no",
    "UNIQUE_CANDIDATE_BYPASSES_PREDECESSOR_VALIDATION": "no",
    "STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT": "yes",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "CURRENT_POINTERS_ARE_PROJECTIONS": "yes",
    "RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING": "no",
    "SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING": "no",
    "SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE": "no",
    "RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT": "no",
    "IDEMPOTENT_REPLAY": "must_not_duplicate_semantic_effects",
}

PRESERVED_FLAGS_EXPECTED = {
    "SUBJECT_ID_IS_AUTHORITY": "no",
    "ID_IS_AUTHORITY": "no",
    "AUTHORITY_TARGET_IS_TYPED": "yes",
    "EXECUTOR_IDENTITY_EQUALS_PRINCIPAL": "no",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "CURRENT_POINTERS_ARE_PROJECTIONS": "yes",
    "DUAL_AUTHORITATIVE_WRITE": "no",
    "EVENT_LOG_IS_SUBJECT_AUTHORITY": "no",
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": "yes",
    "ERROR_SEMANTICS_DISTINCT": "yes",
    "AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION": "yes",
    "AUTHORITY_ENGINE_MAY_INFER_MATERIALIZED_DECISION": "no",
    "CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE": "LLM_or_user_materialized_decision",
    "BINDING_AMBIGUITY_OWNER": "M3-A5",
    "SUBJECT_BINDING_ZERO_ONE_MANY_OWNER": "M3-A5",
}

ENGINE_BOUNDARY_EXPECTED = {
    "NO_STORAGE_ENGINE_FROZEN": "yes",
    "NO_BINDING_ENGINE": "yes",
    "NO_RECOVERY_ENGINE": "yes",
    "NO_PROJECTION_ENGINE": "yes",
    "NO_AUTHORITY_ENGINE": "yes",
    "NO_CAS_ENGINE": "yes",
    "NO_GRAPH_MUTATION_IMPLEMENTATION": "yes",
    "NO_RUNTIME_BEHAVIOR": "yes",
    "BINDING_IMPLEMENTATION_STARTED": "no",
    "RECOVERY_IMPLEMENTATION_STARTED": "no",
    "SOURCE_CODE_CHANGED": "no",
    "AUTHORITATIVE_GRAPH_WRITES_PERFORMED": "no",
    "NO_AUTHORITATIVE_GRAPH_WRITES": "yes",
}

FORBIDDEN_SIGNALS_EXPECTED = {
    "recency_newest": "forbidden",
    "title_text_similarity": "forbidden",
    "nearest_timestamp": "forbidden",
    "path_proximity": "forbidden",
    "first_match": "forbidden",
    "llm_similarity": "forbidden",
}

CONFLICT_CODES = (
    "SUBJECT_NOT_FOUND",
    "SUBJECT_AMBIGUOUS",
    "NEEDS_SEMANTIC_CHOICE",
    "SUBJECT_BINDING_PREDECESSOR_INVALID",
    "SUBJECT_BINDING_REVISION_CONFLICT",
    "SUBJECT_BINDING_AUTHORITY_DENIED",
    "MATERIALIZED_DECISION_MISSING",
    "PROJECTION_STALE_RECONCILABLE",
)

SCENARIO_IDS = ("A5-S1", "A5-S2", "A5-S3", "A5-S4", "A5-S5", "A5-S6",
                "A5-S7", "A5-S8", "A5-S9", "A5-S10")

SCENARIO_REQUIREMENTS = {
    "A5-S1": {
        "NAME": "one valid already-decided candidate: deterministic binding",
        "expected_phrases": ["SUBJECT_BINDING_AUTOMATION_RULE", "candidate_count==1",
                             "materialized Decision", "valid authority",
                             "valid revision/predecessor", "deterministic binding"],
    },
    "A5-S2": {
        "NAME": "two valid candidates: ambiguity fails closed",
        "expected_phrases": ["NEEDS_SEMANTIC_CHOICE", "SUBJECT_AMBIGUOUS", "no automatic selection",
                             "NO_HIDDEN_REDUCTION_MANY_TO_ONE=yes", "no heuristic tie-break"],
    },
    "A5-S3": {
        "NAME": "zero candidates: bounded not-found, no fabricated Subject",
        "expected_phrases": ["SUBJECT_NOT_FOUND", "bounded zero-result",
                             "no fabricated Subject", "no fake Subject"],
    },
    "A5-S4": {
        "NAME": "one candidate without a materialized Decision",
        "expected_phrases": ["MATERIALIZED_DECISION_MISSING", "no automatic followup binding",
                             "UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION=no", "never inferred"],
    },
    "A5-S5": {
        "NAME": "one candidate with stale revision: fail closed",
        "expected_phrases": ["SUBJECT_BINDING_REVISION_CONFLICT", "expected revision",
                             "current revision", "fail closed"],
    },
    "A5-S6": {
        "NAME": "one candidate with invalid authority: denied",
        "expected_phrases": ["SUBJECT_BINDING_AUTHORITY_DENIED", "denied",
                             "UNIQUE_CANDIDATE_BYPASSES_AUTHORITY=no", "identity alone never authorizes"],
    },
    "A5-S7": {
        "NAME": "stale current pointer over a valid durable Subject",
        "expected_phrases": ["PROJECTION_STALE_RECONCILABLE", "discoverable",
                             "STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT=yes",
                             "no SPEC/Profile Task escalation"],
    },
    "A5-S8": {
        "NAME": "safe read-only host diagnosis needs no binding",
        "expected_phrases": ["SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING=no",
                             "SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE=no",
                             "no forced Plan / Work Item / SPEC / Profile Task / approval / followup"],
    },
    "A5-S9": {
        "NAME": "a newer candidate is still ambiguous",
        "expected_phrases": ["NEEDS_SEMANTIC_CHOICE", "recency/newest is a forbidden signal",
                             "never auto-selected", "never reduced to one"],
    },
    "A5-S10": {
        "NAME": "recovery replay does not duplicate the semantic effect",
        "expected_phrases": ["no second child Subject", "no second edge",
                             "RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT=no",
                             "IDEMPOTENT_REPLAY=must_not_duplicate_semantic_effects"],
    },
}

REQUIRED_PROOF_CLASSES = (
    "B011",
    "B013",
    "B014",
    "ACTIVATE-R-current-binding",
    "ACTIVATE-R-host-inspection-escalation",
    "WCTX-1",
    "BIND-1",
    "RECOVERY-1",
)

PROOF_FIELDS = ("FAILURE_CLASS", "A5_BINDING_OR_RECOVERY_INVARIANT", "DESIGN_PROOF",
                "IMPLEMENTATION_REQUIRED", "FUTURE_OWNER")

BINDING_KIND_FIELDS = ("BINDING_KIND", "CANDIDATE_SOURCE", "VALIDITY_RULE",
                       "ZERO_RESULT", "ONE_RESULT", "MANY_RESULT", "AUTOMATION_ALLOWED")

REQUIRED_BINDING_KINDS = (
    "current_subject_projection",
    "decision_followup_child",
    "execution_subject",
    "completion_subject",
    "review_result_subject",
    "plan_work_item_contextual_binding",
)

A5_RESOLVED_IDS = ("SUBJECT-BINDING-ZERO-ONE-MANY", "BINDING-AUTOMATION-RULE",
                   "RECOVERY-BOUNDARY", "READONLY-DIAGNOSTIC-EXEMPTION")

ALLOWED_CHANGED_PATHS = frozenset({
    "deploy/evidence/issues/9/m3-a/a5-binding-recovery.json",
    "scripts/m3_a5_binding_recovery_guard.py",
})
FORBIDDEN_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

# Frozen M3-A3 truth markers cross-checked against the checked-out A3 artifact.
A3_TRUTH_MARKERS = [
    ("cas_model.CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT", "yes"),
    ("projection_relation.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
    ("projection_relation.CURRENT_POINTERS_ARE_PROJECTIONS", "yes"),
    ("followup_transaction.FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION", "yes"),
    ("followup_transaction.DUPLICATE_CHILD_SEMANTIC_EFFECT_FORBIDDEN", "yes"),
    ("invariants.preserved_contract_flags.AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION", "yes"),
    ("invariants.preserved_contract_flags.AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT", "no"),
    ("invariants.preserved_contract_flags.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
]
A12_TRUTH_MARKERS = [
    ("invariants.BINDING_AMBIGUITY_OWNER", "M3-A5"),
    ("invariants.AUTHORITY_TARGET_IS_TYPED", "yes"),
    ("invariants.AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION", "yes"),
]

# Conservative static scan: no graph/subject/authority/binding/recovery/
# projection/mutation implementation may exist in aota_forge.
IMPL_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease|Mutation|Transaction|Revision|Cas|CAS|"
    r"Binding|Recovery|Projection)\w*"
    r"(?:Store|Repository|Engine|Broker|Manager|Service|Evaluator|Issuer|Ledger|Vault|Reconciler)\w*\s*[:(]"
)
IMPL_FUNC_RE = re.compile(
    r"def\s+(?:evaluate_authority|authorize_mutation|issue_lease|consume_lease|"
    r"revoke_lease|renew_lease|acquire_lease|validate_lease|grant_lease|"
    r"apply_mutation|perform_mutation|commit_mutation|execute_mutation|write_graph|"
    r"create_subject|transition_subject|cas_commit|compare_and_set|"
    r"begin_transaction|commit_transaction|rollback_transaction|"
    r"bind_subject|auto_bind|resolve_candidate|recover_binding|"
    r"rebuild_projection|repair_projection|replay_projection|resolve_binding)\s*\("
)
IMPL_OP_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease|mutation|transaction|revision|'
    r'binding|recovery|projection|decision)\.[a-z0-9_.-]+"'
)
IMPL_IMPORT_RE = re.compile(
    r"(?:from|import)\s+aota_forge\.(?:core|adapters)\.(?:authority|lease|mutation|graph|transaction|revision|cas|binding|recovery|projection)\b"
)
READ_ONLY_GATE_MARKER = "operation is not read-only in M2"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _dict_get(data: dict, dotted_path: str):
    node = data
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout.strip()


def _is_plain_nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def run_checks(artifact: dict, repo_root: Path, a3: dict, a12: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Top-level identity, exact base SHA, m3_a_common_base, input commits.
    for key in ("schema_version", "project_id", "milestone", "phase", "lane",
                "base_sha", "m3_a_common_base"):
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    results.append(_check(artifact.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(artifact.get("project_id") == "aota_forge",
                          "project_id must be aota_forge"))
    results.append(_check(artifact.get("milestone") == "M3" and artifact.get("phase") == "M3-A",
                          "milestone/phase must be M3 / M3-A"))
    results.append(_check(artifact.get("lane") == "M3-A5", "lane must be M3-A5"))
    results.append(_check(
        artifact.get("base_sha") == EXPECTED_BASE_SHA,
        f"base_sha must be {EXPECTED_BASE_SHA} (got {artifact.get('base_sha')!r})",
    ))
    results.append(_check(
        artifact.get("m3_a_common_base") == M3_A_COMMON_BASE,
        f"m3_a_common_base must be {M3_A_COMMON_BASE} (got {artifact.get('m3_a_common_base')!r})",
    ))
    input_commits = artifact.get("input_commits", {})
    results.append(_check(
        input_commits.get("a3") == A3_INPUT_COMMIT,
        f"input_commits.a3 must be {A3_INPUT_COMMIT} (got {input_commits.get('a3')!r})",
    ))
    results.append(_check(
        input_commits.get("a12") == "8dad2d3ceeaaf093f35e39daa5c12049c0505527",
        "input_commits.a12 must be 8dad2d3ceeaaf093f35e39daa5c12049c0505527",
    ))
    results.append(_check(
        input_commits.get("a2") == "b29fe5f6fa47035cdeb4137cd62aafc585257b40",
        "input_commits.a2 must be b29fe5f6fa47035cdeb4137cd62aafc585257b40",
    ))
    results.append(_check(
        input_commits.get("a1") == "7dbc2225e0b66f6bf9e6d0970581d96f428d4362",
        "input_commits.a1 must be 7dbc2225e0b66f6bf9e6d0970581d96f428d4362",
    ))
    results.append(_check(
        input_commits.get("a0") == "10b8018ecf618cd7ff4c2b73f962356af044669f",
        "input_commits.a0 must be 10b8018ecf618cd7ff4c2b73f962356af044669f",
    ))
    for key in ("a0", "a1", "a2", "a12", "a3", "matrix"):
        results.append(_check(
            _is_plain_nonempty(artifact.get("inputs", {}).get(key)),
            f"inputs.{key} must be defined",
        ))
    design_only = artifact.get("design_proof_only", {})
    for key in ("no_graph_storage_engine", "no_graph_mutation_implementation",
                "no_authoritative_graph_writes", "no_binding_engine", "no_recovery_engine",
                "no_projection_engine", "no_authority_engine", "no_cas_engine",
                "no_runtime_behavior"):
        results.append(_check(design_only.get(key) == "yes",
                              f"design_proof_only.{key} must be yes"))
    results.append(_check(design_only.get("source_mutation_scope") == "design_artifacts_only",
                          "design_proof_only.source_mutation_scope must be design_artifacts_only"))

    # 2. Required design sections present.
    for section in REQUIRED_SECTIONS:
        results.append(_check(
            isinstance(artifact.get(section), dict) or isinstance(artifact.get(section), list),
            f"required section missing or empty: {section}",
        ))

    # 3. Candidate validity model: deterministic validity first, allowed filters
    #    only, forbidden signals, no hidden many->one reduction.
    validity = artifact.get("candidate_validity_model", {})
    results.append(_check(validity.get("VALIDITY_EVALUATION_BEFORE_COUNT") == "yes",
                          "candidate_validity_model.VALIDITY_EVALUATION_BEFORE_COUNT must be yes"))
    results.append(_check(validity.get("CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER") == "yes",
                          "CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER must be yes"))
    results.append(_check(validity.get("HEURISTIC_PRE_FILTER_ALLOWED") == "no",
                          "HEURISTIC_PRE_FILTER_ALLOWED must be no (got %r)" % validity.get("HEURISTIC_PRE_FILTER_ALLOWED")))
    results.append(_check(validity.get("HEURISTIC_SUBJECT_SELECTION_ALLOWED") == "no",
                          "HEURISTIC_SUBJECT_SELECTION_ALLOWED must be no (got %r)" % validity.get("HEURISTIC_SUBJECT_SELECTION_ALLOWED")))
    allowed_filters = validity.get("allowed_validity_filters", [])
    for phrase in ("correct project/workflow scope", "canonical record type",
                   "valid lineage", "valid predecessor", "explicit materialized Decision",
                   "authority eligibility", "revision compatibility"):
        results.append(_check(any(phrase in item for item in allowed_filters),
                              f"allowed_validity_filters must include {phrase!r}"))
    signals = validity.get("forbidden_validity_signals", {})
    for signal, expected in FORBIDDEN_SIGNALS_EXPECTED.items():
        results.append(_check(signals.get(signal) == expected,
                              f"forbidden_validity_signals.{signal} must be {expected!r} (got {signals.get(signal)!r})"))
    results.append(_check(validity.get("NO_HIDDEN_REDUCTION_MANY_TO_ONE") == "yes",
                          "NO_HIDDEN_REDUCTION_MANY_TO_ONE must be yes"))
    results.append(_check("NEEDS_SEMANTIC_CHOICE" in validity.get("many_to_one_rule", ""),
                          "many_to_one_rule must fail many candidates to NEEDS_SEMANTIC_CHOICE"))
    results.append(_check(_is_plain_nonempty(validity.get("determinism")),
                          "candidate validity determinism must be defined"))

    # 4. Zero/one/many rule.
    zom = artifact.get("zero_one_many_rule", {})
    results.append(_check(zom.get("SUBJECT_BINDING_ZERO_ONE_MANY_OWNER") == "M3-A5",
                          "zero_one_many_rule.SUBJECT_BINDING_ZERO_ONE_MANY_OWNER must be M3-A5"))
    results.append(_check("deterministic validity filter" in zom.get("computation_order", "")
                          and "CANDIDATE_COUNT_COMPUTED_AFTER_DETERMINISTIC_VALIDITY_FILTER=yes" in zom.get("computation_order", ""),
                          "zero_one_many_rule.computation_order must state count is computed after the deterministic validity filter"))
    zero = zom.get("ZERO", {})
    zero_results = zero.get("results", [])
    for phrase in ("SUBJECT_NOT_FOUND", "PROJECTION_STALE_RECONCILABLE",
                   "missing prerequisite evidence", "NEEDS_SEMANTIC_CHOICE"):
        results.append(_check(any(phrase in item for item in zero_results),
                              f"zero_one_many_rule.ZERO.results must cover {phrase!r}"))
    results.append(_check("fake" in zero.get("never", "") and "never" in zero.get("never", ""),
                          "zero_one_many_rule.ZERO.never must forbid fabricating a Subject"))
    one = zom.get("ONE", {})
    results.append(_check(one.get("UNIQUE_CANDIDATE_ALONE_INSUFFICIENT") == "yes",
                          "zero_one_many_rule.ONE.UNIQUE_CANDIDATE_ALONE_INSUFFICIENT must be yes"))
    one_reqs = one.get("requirements", [])
    for phrase in ("materialized semantic basis", "required authority valid",
                   "required revision/predecessor valid"):
        results.append(_check(any(phrase in item for item in one_reqs),
                              f"zero_one_many_rule.ONE.requirements must cover {phrase!r}"))
    results.append(_check("candidate_count==1" in one.get("automation", ""),
                          "zero_one_many_rule.ONE.automation must require candidate_count==1"))
    many = zom.get("MANY", {})
    results.append(_check(many.get("AUTOMATIC_SELECTION") == "no",
                          "zero_one_many_rule.MANY.AUTOMATIC_SELECTION must be no (multiple candidates must never auto-select)"))
    results.append(_check(many.get("result") == "NEEDS_SEMANTIC_CHOICE",
                          "zero_one_many_rule.MANY.result must be NEEDS_SEMANTIC_CHOICE"))
    automation_rule = zom.get("SUBJECT_BINDING_AUTOMATION_RULE", "")
    for phrase in ("candidate_count==1", "durably materialized", "required authority valid",
                   "required revision/predecessor valid", "NEEDS_SEMANTIC_CHOICE",
                   "HEURISTIC_SUBJECT_SELECTION_ALLOWED=no"):
        results.append(_check(phrase in automation_rule,
                              f"SUBJECT_BINDING_AUTOMATION_RULE must state {phrase!r}"))

    # 5. Semantic decision materialization.
    sem = artifact.get("semantic_decision_materialization", {})
    results.append(_check(sem.get("requires_canonical_decision") == "yes",
                          "semantic_decision_materialization.requires_canonical_decision must be yes"))
    decision_reqs = sem.get("decision_requirements", [])
    for phrase in ("canonical Decision record exists", "correct owning Subject",
                   "explicitly authorizes the relevant followup category",
                   "not superseded or invalid", "deterministic lineage"):
        results.append(_check(any(phrase in item for item in decision_reqs),
                              f"semantic_decision_materialization.decision_requirements must cover {phrase!r}"))
    results.append(_check(sem.get("materialized_basis") == "LLM_or_user_materialized_decision",
                          "materialized_basis must be LLM_or_user_materialized_decision"))
    authority = sem.get("authority_engine_boundary", {})
    results.append(_check(authority.get("AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION") == "yes",
                          "authority engine must be allowed to verify a materialized Decision"))
    results.append(_check(authority.get("AUTHORITY_ENGINE_MAY_INFER_MATERIALIZED_DECISION") == "no",
                          "authority engine must never infer a materialized Decision"))
    results.append(_check("never infers" in authority.get("rule", ""),
                          "authority engine rule must state it never infers"))
    results.append(_check(sem.get("UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION") == "no",
                          "UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION must be no (got %r)" % sem.get("UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION")))

    # 6. Binding kinds: per-kind rules for all six kinds.
    kinds_wrapper = artifact.get("binding_kinds", {})
    results.append(_check("single generic algorithm" in kinds_wrapper.get("rule", "")
                          and "forbidden" in kinds_wrapper.get("rule", ""),
                          "binding_kinds.rule must forbid a single generic algorithm without per-kind rules"))
    kinds = kinds_wrapper.get("kinds", [])
    kind_names = [k.get("BINDING_KIND") for k in kinds if isinstance(k, dict)]
    results.append(_check(kind_names == list(REQUIRED_BINDING_KINDS),
                          f"binding_kinds.kinds must be exactly {list(REQUIRED_BINDING_KINDS)}"))
    for name in REQUIRED_BINDING_KINDS:
        kind = next((k for k in kinds if isinstance(k, dict) and k.get("BINDING_KIND") == name), {})
        for field in BINDING_KIND_FIELDS:
            results.append(_check(_is_plain_nonempty(kind.get(field)),
                                  f"binding kind {name} must define {field}"))
        auto = kind.get("AUTOMATION_ALLOWED", "")
        results.append(_check(auto.startswith("yes_only_when_one_valid_candidate_and_all_requirements_hold"),
                              f"binding kind {name} AUTOMATION_ALLOWED must be yes_only_when_one_valid_candidate_and_all_requirements_hold"))
        one_result = kind.get("ONE_RESULT", "")
        results.append(_check("insufficient" in one_result or "never" in one_result,
                              f"binding kind {name} ONE_RESULT must state the unique candidate is alone insufficient"))
        many_result = kind.get("MANY_RESULT", "")
        results.append(_check("NEEDS_SEMANTIC_CHOICE" in many_result,
                              f"binding kind {name} MANY_RESULT must be NEEDS_SEMANTIC_CHOICE"))
    results.append(_check(
        "review acceptance" in next(k for k in kinds if k.get("BINDING_KIND") == "review_result_subject").get("AUTOMATION_ALLOWED", ""),
        "review_result_subject must state review acceptance is never automated"))
    results.append(_check(
        "FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION" in next(k for k in kinds if k.get("BINDING_KIND") == "decision_followup_child").get("VALIDITY_RULE", ""),
        "decision_followup_child VALIDITY_RULE must preserve FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION"))

    # 7. Recovery boundary: may / may_not lists, no semantic reasoning.
    recovery = artifact.get("recovery_boundary", {})
    may = recovery.get("may", [])
    for phrase in ("rebuild a stale projection", "resolve one unique durable Subject",
                   "repair deterministic index/projection drift",
                   "bind a uniquely determined already-decided followup",
                   "replay an idempotent projection"):
        results.append(_check(any(phrase in item for item in may),
                              f"recovery_boundary.may must include {phrase!r}"))
    may_not = recovery.get("may_not", [])
    for phrase in ("choose a project under ambiguity", "choose a semantic followup",
                   "accept a review", "change requirements/scope/worker role",
                   "close a milestone", "invent a missing Decision"):
        results.append(_check(any(phrase in item for item in may_not),
                              f"recovery_boundary.may_not must include {phrase!r}"))
    results.append(_check(recovery.get("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING") == "no",
                          "RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING must be no (got %r)" % recovery.get("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING")))
    bvr = recovery.get("BINDING_VS_RECOVERY", {})
    results.append(_check("SUBJECT_BINDING_AUTOMATION_RULE" in bvr.get("binding", ""),
                          "BINDING_VS_RECOVERY.binding must cite the SUBJECT_BINDING_AUTOMATION_RULE"))
    results.append(_check("never chooses semantics" in bvr.get("recovery", ""),
                          "BINDING_VS_RECOVERY.recovery must state recovery never chooses semantics"))
    results.append(_check("bounded" in recovery.get("bounded", ""),
                          "recovery_boundary.bounded must be defined"))

    # 8. Projection recovery: stale pointer never hides the durable Subject.
    proj = artifact.get("projection_recovery", {})
    results.append(_check(proj.get("STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT") == "yes",
                          "STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT must be yes (got %r)" % proj.get("STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT")))
    results.append(_check(proj.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "projection_recovery.CURRENT_POINTERS_ARE_AUTHORITY must be no"))
    results.append(_check(proj.get("CURRENT_POINTERS_ARE_PROJECTIONS") == "yes",
                          "projection_recovery.CURRENT_POINTERS_ARE_PROJECTIONS must be yes"))
    results.append(_check("never hides" in proj.get("stale_subject_rule", ""),
                          "projection_recovery.stale_subject_rule must state the stale pointer never hides the durable Subject"))
    results.append(_check("SPEC" in proj.get("no_escalation_for_rebuild", "")
                          and "Profile Task" in proj.get("no_escalation_for_rebuild", ""),
                          "projection_recovery must forbid SPEC/Profile Task escalation merely to diagnose/rebuild"))
    results.append(_check("durable record set" in proj.get("rebuild_source", "")
                          and "never" in proj.get("rebuild_source", ""),
                          "projection_recovery.rebuild_source must be the durable record set only"))
    results.append(_check(_is_plain_nonempty(proj.get("drift_repair")),
                          "projection_recovery.drift_repair must be defined"))

    # 9. Read-only diagnostic exemption.
    ro = artifact.get("readonly_diagnostic_exemption", {})
    results.append(_check(ro.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING") == "no",
                          "SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING must be no (got %r)" % ro.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING")))
    results.append(_check(ro.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE") == "no",
                          "SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE must be no (got %r)" % ro.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_CAPABILITY_LEASE")))
    results.append(_check("unrelated to lifecycle mutation" in ro.get("capability_lease_exception", ""),
                          "capability_lease_exception must be limited to bounded privileged access unrelated to lifecycle mutation"))
    results.append(_check(_is_plain_nonempty(ro.get("independent_availability")),
                          "readonly_diagnostic_exemption.independent_availability must be defined"))
    results.append(_check("no forced Plan" in ro.get("no_forced_artifacts", "")
                          or "never forced" in ro.get("no_forced_artifacts", ""),
                          "readonly_diagnostic_exemption must forbid forced Plan/Work Item/SPEC/Profile Task/approval/followup"))

    # 10. Conflict taxonomy: the eight canonical distinct codes.
    taxonomy = artifact.get("conflict_taxonomy", {})
    results.append(_check(taxonomy.get("ERROR_SEMANTICS_DISTINCT") == "yes",
                          "conflict_taxonomy.ERROR_SEMANTICS_DISTINCT must be yes"))
    conflicts = taxonomy.get("conflicts", [])
    codes = {c.get("CODE") for c in conflicts if isinstance(c, dict)}
    results.append(_check(set(codes) == set(CONFLICT_CODES),
                          f"conflict_taxonomy must define exactly {sorted(CONFLICT_CODES)}"))
    for code in CONFLICT_CODES:
        entry = next((c for c in conflicts if isinstance(c, dict) and c.get("CODE") == code), {})
        results.append(_check(_is_plain_nonempty(entry.get("meaning")),
                              f"conflict {code} must define its meaning"))
        results.append(_check(_is_plain_nonempty(entry.get("retryable")),
                              f"conflict {code} must define retryable semantics"))
    results.append(_check("pairwise distinct" in taxonomy.get("canonical_distinct", "")
                          or "never collapse" in taxonomy.get("canonical_distinct", ""),
                          "conflict_taxonomy.canonical_distinct must state codes are canonical and distinct"))

    # 11. Revision / authority relation: no bypass for unique candidates.
    rel = artifact.get("revision_authority_relation", {})
    results.append(_check(rel.get("UNIQUE_CANDIDATE_BYPASSES_CAS") == "no",
                          "UNIQUE_CANDIDATE_BYPASSES_CAS must be no (got %r)" % rel.get("UNIQUE_CANDIDATE_BYPASSES_CAS")))
    results.append(_check(rel.get("UNIQUE_CANDIDATE_BYPASSES_AUTHORITY") == "no",
                          "UNIQUE_CANDIDATE_BYPASSES_AUTHORITY must be no (got %r)" % rel.get("UNIQUE_CANDIDATE_BYPASSES_AUTHORITY")))
    results.append(_check(rel.get("UNIQUE_CANDIDATE_BYPASSES_PREDECESSOR_VALIDATION") == "no",
                          "UNIQUE_CANDIDATE_BYPASSES_PREDECESSOR_VALIDATION must be no (got %r)" % rel.get("UNIQUE_CANDIDATE_BYPASSES_PREDECESSOR_VALIDATION")))
    results.append(_check("never bypasses" in rel.get("authoritative_mutation", ""),
                          "revision_authority_relation must state authoritative mutation never bypasses CAS/authority"))
    results.append(_check("never implies mutation authority" in rel.get("read_only_resolution", ""),
                          "read-only resolution must never imply mutation authority"))
    results.append(_check("BOTH" in rel.get("authority_and_revision_required", ""),
                          "automatic binding/recovery must require BOTH valid authority and valid revision/predecessor"))

    # 12. Idempotent recovery.
    idem = artifact.get("idempotent_recovery", {})
    results.append(_check(idem.get("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT") == "no",
                          "RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT must be no (got %r)" % idem.get("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT")))
    results.append(_check(idem.get("IDEMPOTENT_REPLAY") == "must_not_duplicate_semantic_effects",
                          "idempotent_recovery.IDEMPOTENT_REPLAY must be must_not_duplicate_semantic_effects"))
    results.append(_check("never duplicates" in idem.get("repeat_rule", ""),
                          "idempotent_recovery.repeat_rule must state replay never duplicates a semantic effect"))
    results.append(_check("no second Subject" in idem.get("existing_effect", "")
                          and "no second edge" in idem.get("existing_effect", ""),
                          "idempotent_recovery.existing_effect must create no second Subject/edge"))
    results.append(_check("no second Subject and no second edge" in idem.get("no_second_subject_or_edge", ""),
                          "idempotent_recovery.no_second_subject_or_edge must be defined"))
    results.append(_check(_is_plain_nonempty(idem.get("basis")),
                          "idempotent_recovery.basis must be defined (A3 idempotency model)"))

    # 13. Failure policy.
    fail = artifact.get("failure_policy", {})
    results.append(_check("fail closed" in fail.get("unsafe_mutation", ""),
                          "failure_policy.unsafe_mutation must fail closed"))
    results.append(_check("always available" in fail.get("safe_diagnosis", ""),
                          "failure_policy.safe_diagnosis must state safe diagnosis is always available"))
    results.append(_check("NEEDS_SEMANTIC_CHOICE" in fail.get("ambiguous_semantics", "")
                          and "never" in fail.get("ambiguous_semantics", ""),
                          "failure_policy.ambiguous_semantics must be NEEDS_SEMANTIC_CHOICE, never heuristic"))
    results.append(_check("bounded explicit error" in fail.get("missing_deterministic_prerequisite", ""),
                          "failure_policy.missing_deterministic_prerequisite must be a bounded explicit error"))
    results.append(_check("blind replay" in fail.get("no_blind_replay", "")
                          and "denied" in fail.get("no_blind_replay", ""),
                          "failure_policy.no_blind_replay must deny blind replay"))
    results.append(_check("never invents" in fail.get("never_invents", ""),
                          "failure_policy.never_invents must be defined"))

    # 14. Machine-readable A5-S1..A5-S10 design scenarios.
    scenarios = artifact.get("design_scenarios", [])
    scenario_ids = [s.get("SCENARIO_ID") for s in scenarios if isinstance(s, dict)]
    results.append(_check(scenario_ids == list(SCENARIO_IDS),
                          f"design_scenarios must be exactly {list(SCENARIO_IDS)}"))
    for sid in SCENARIO_IDS:
        entry = next((s for s in scenarios if isinstance(s, dict) and s.get("SCENARIO_ID") == sid), {})
        for key in ("NAME", "given", "when", "expected"):
            results.append(_check(_is_plain_nonempty(entry.get(key)),
                                  f"scenario {sid} must define {key}"))
        req = SCENARIO_REQUIREMENTS.get(sid, {})
        results.append(_check(entry.get("NAME") == req.get("NAME"),
                              f"scenario {sid} NAME must be {req.get('NAME')!r}"))
        for phrase in req.get("expected_phrases", []):
            results.append(_check(phrase in entry.get("expected", ""),
                                  f"scenario {sid} expected must state {phrase!r}"))

    # 15. Regression design proofs: required classes, fields, NYI dispositions.
    proofs = artifact.get("regression_design_proofs", [])
    proof_classes = [p.get("FAILURE_CLASS") for p in proofs if isinstance(p, dict)]
    results.append(_check(proof_classes == list(REQUIRED_PROOF_CLASSES),
                          f"regression_design_proofs must be exactly {list(REQUIRED_PROOF_CLASSES)}"))
    for cls in REQUIRED_PROOF_CLASSES:
        proof = next((p for p in proofs if isinstance(p, dict) and p.get("FAILURE_CLASS") == cls), {})
        for field in PROOF_FIELDS:
            results.append(_check(_is_plain_nonempty(proof.get(field)),
                                  f"proof {cls} must define {field}"))
        results.append(_check(proof.get("IMPLEMENTATION_REQUIRED") in ("yes", "no"),
                              f"proof {cls} IMPLEMENTATION_REQUIRED must be yes|no"))
        results.append(_check(_is_plain_nonempty(proof.get("FUTURE_OWNER")),
                              f"proof {cls} FUTURE_OWNER must be defined"))
        results.append(_check("NOT_YET_IMPLEMENTED" in proof.get("corpus_disposition_unchanged", ""),
                              f"proof {cls} must keep the corpus disposition NOT_YET_IMPLEMENTED"))
        invariant = proof.get("A5_BINDING_OR_RECOVERY_INVARIANT", "")
        results.append(_check("no implementation" in proof.get("DESIGN_PROOF", "")
                              or "no implementation" in proof.get("corpus_disposition_unchanged", "")
                              or "no implementation in this lane" in proof.get("DESIGN_PROOF", ""),
                              f"proof {cls} must state no implementation in this lane"))

    # 16. Deferred ownership normalization.
    deferred = artifact.get("deferred_questions", {})
    resolved = [e.get("id") for e in deferred.get("resolved_by_a5", []) if isinstance(e, dict)]
    results.append(_check(resolved == list(A5_RESOLVED_IDS),
                          f"deferred_questions.resolved_by_a5 must be exactly {list(A5_RESOLVED_IDS)}"))
    a4_ids = [e.get("QUESTION_ID") for e in deferred.get("deferred_to_a4", []) if isinstance(e, dict)]
    results.append(_check(a4_ids == ["OQ-M3A5-01", "OQ-M3A5-02"],
                          "deferred_to_a4 must be exactly [OQ-M3A5-01, OQ-M3A5-02]"))
    results.append(_check(isinstance(deferred.get("deferred_to_implementation"), list)
                          and deferred.get("deferred_to_implementation"),
                          "deferred_to_implementation must be a non-empty list"))

    # 17. Invariants: preserved flags, binding flags, engine boundary.
    invariants = artifact.get("invariants", {})
    preserved = invariants.get("preserved_contract_flags", {})
    for key, expected in PRESERVED_FLAGS_EXPECTED.items():
        results.append(_check(preserved.get(key) == expected,
                              f"invariants.preserved_contract_flags.{key} must be {expected!r}"))
    binding_flags = invariants.get("binding_flags", {})
    for key, expected in BINDING_FLAGS_EXPECTED.items():
        results.append(_check(binding_flags.get(key) == expected,
                              f"invariants.binding_flags.{key} must be {expected!r} (got {binding_flags.get(key)!r})"))
    automation = binding_flags.get("SUBJECT_BINDING_AUTOMATION_RULE", "")
    for phrase in ("candidate_count==1", "materialized Decision", "valid authority",
                   "valid revision/predecessor", "NEEDS_SEMANTIC_CHOICE"):
        results.append(_check(phrase in automation,
                              f"invariants.binding_flags.SUBJECT_BINDING_AUTOMATION_RULE must state {phrase!r}"))
    engine = invariants.get("engine_boundary", {})
    for key, expected in ENGINE_BOUNDARY_EXPECTED.items():
        results.append(_check(engine.get(key) == expected,
                              f"invariants.engine_boundary.{key} must be {expected!r}"))
    results.append(_check(engine.get("GRAPH_STORAGE_LOCATIONS") == [],
                          "engine_boundary.GRAPH_STORAGE_LOCATIONS must be empty (no storage engine frozen)"))
    a5_boundary = invariants.get("a5_design_only_boundary", {})
    results.append(_check(a5_boundary.get("A5_LANE_TYPE") == "design_only",
                          "a5_design_only_boundary.A5_LANE_TYPE must be design_only"))
    results.append(_check("design_only" in a5_boundary.get("A5_DESIGN_ONLY_BOUNDARY", ""),
                          "a5_design_only_boundary.A5_DESIGN_ONLY_BOUNDARY must declare the design-only boundary"))
    results.append(_check(a5_boundary.get("AUTHORITATIVE_GRAPH_WRITES_PERFORMED") == "no",
                          "a5_design_only_boundary.AUTHORITATIVE_GRAPH_WRITES_PERFORMED must be no"))

    # 18. Frozen M3-A3 / M3-A12 truth markers cross-checked.
    for dotted, expected in A3_TRUTH_MARKERS:
        results.append(_check(_dict_get(a3, dotted) == expected,
                              f"A3 cross-check {dotted} must be {expected!r}"))
    for dotted, expected in A12_TRUTH_MARKERS:
        results.append(_check(_dict_get(a12, dotted) == expected,
                              f"A12 cross-check {dotted} must be {expected!r}"))

    # 19. Engine boundary: no binding/recovery/projection/authority/CAS
    #     implementation in aota_forge; read-only ingress gate preserved.
    aota_pkg = repo_root / "aota_forge"
    if not aota_pkg.is_dir():
        results.append(_check(False, "aota_forge package not found under repo root"))
        return results
    impl_hits: list[str] = []
    for py in sorted(aota_pkg.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        rel = str(py.relative_to(repo_root))
        for pattern, matcher in (
            ("class", IMPL_CLASS_RE),
            ("function", IMPL_FUNC_RE),
            ("import", IMPL_IMPORT_RE),
        ):
            for match in matcher.finditer(text):
                impl_hits.append(f"{rel}:{pattern} {match.group(0)}")
    catalog_text = (aota_pkg / "core" / "catalog.py").read_text(encoding="utf-8")
    for match in IMPL_OP_RE.finditer(catalog_text):
        impl_hits.append(f"aota_forge/core/catalog.py:operation {match.group(0)}")
    results.append(_check(not impl_hits,
                          f"binding/recovery/projection/authority/CAS implementation surface found: {impl_hits}"))
    ingress_text = (aota_pkg / "core" / "ingress.py").read_text(encoding="utf-8")
    results.append(_check(READ_ONLY_GATE_MARKER in ingress_text,
                          f"read-only ingress gate marker missing: {READ_ONLY_GATE_MARKER!r}"))

    # 20. Changed-path check vs the exact base: only the two M3-A5 files.
    rc, tracked = _git(repo_root, ["diff", "--name-only", EXPECTED_BASE_SHA])
    results.append(_check(rc == 0, "git diff against the base commit failed"))
    rc2, untracked = _git(repo_root, ["ls-files", "--others", "--exclude-standard"])
    results.append(_check(rc2 == 0, "git ls-files --others failed"))
    changed = set(tracked.splitlines()) if tracked else set()
    for line in (untracked.splitlines() if untracked else []):
        if "__pycache__" in line or line.endswith(".pyc"):
            continue
        changed.add(line.strip())
    results.append(_check(bool(changed), "no changed paths detected against the base"))
    results.append(_check(changed <= ALLOWED_CHANGED_PATHS,
                          f"forbidden changes vs base: {sorted(changed - ALLOWED_CHANGED_PATHS)}"))
    forbidden = [p for p in sorted(changed) if p.startswith(FORBIDDEN_PREFIXES)]
    results.append(_check(not forbidden,
                          f"aota_forge/core or adapters changes are strictly forbidden: {forbidden}"))
    results.append(_check(sorted(changed) == sorted(ALLOWED_CHANGED_PATHS),
                          f"changed paths must be exactly {sorted(ALLOWED_CHANGED_PATHS)}"))

    return results


def summarize(artifact: dict, results: list[tuple[bool, str]]) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    return {
        "artifact": "m3-a5-binding-recovery",
        "lane": artifact.get("lane"),
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A5 subject binding and recovery guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--artifact", type=Path, help="path to the M3-A5 artifact JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    args = parser.parse_args()

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    repo_root = repo_root.resolve()
    artifact_path = args.artifact or repo_root / A5_REL
    artifact_path = artifact_path.resolve()

    try:
        artifact = _read_json(artifact_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        a3 = _read_json(repo_root / A3_ART_REL)
        a12 = _read_json(repo_root / A12_ART_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A3/A12 artifact unreadable/invalid: {exc}")
        return 1

    results = run_checks(artifact, repo_root, a3, a12)
    summary = summarize(artifact, results)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

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
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
