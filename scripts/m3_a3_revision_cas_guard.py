#!/usr/bin/env python3
"""M3-A3 revision / CAS / transaction / concurrency guard (Issue #9, lane M3-A3).

Design-only freeze guard.  Validates
deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json at the exact
M3-A12 common base (8dad2d3ceeaaf093f35e39daa5c12049c0505527) against the
frozen A1/A2/A12 contracts:

- exact top-level identity, gate and base SHA; required design sections present
- revision model: Subject aggregate selected, whole-graph / workflow / record
  alternatives evaluated and rejected, storage-independent canonical revision
  representation, model does not choreograph revision, Workflow revision not
  needed, child initial revision independent, parent -> new-child atomic
- CAS model: expected/current semantics, CAS check before authoritative commit,
  fail-closed stale mutation, no bypass except M3-A4 bootstrap/cutover
- lease revision contract: typed target + expected revision bound; stale
  unexpired lease denied; expiry cannot silently expand authority
- transaction boundary: authoritative transaction separated from post-commit
  projection/audit; no dual authoritative write; audit failure never rolls back
- followup transaction: orphan child / edge without child / child without
  verified Decision basis forbidden; partial authoritative transition
  forbidden; duplicate child semantic effect forbidden
- multi-subject policy: default single-Subject transaction, cross-existing-
  Subject transactions minimized/deferred, global process mutex NOT canonical
- concurrency rules: LOST_UPDATE_ALLOWED=no, SILENT_LAST_WRITE_WINS=no,
  STALE_REVISION_MUTATION=fail_closed, CONCURRENT_VALID_MUTATION=
  CAS_or_explicit_serialization
- idempotency model: scope/content/retention/replay/mismatch; principal/authority
  context + operation + typed target + semantic fingerprint binding; request vs
  semantic effect dedupe distinguished; same key/different payload rejected;
  replay must not duplicate semantic effects
- conflict taxonomy: the five distinct codes REVISION_CONFLICT,
  IDEMPOTENCY_CONFLICT, PREDECESSOR_STATE_CONFLICT, AUTHORITY_STALE,
  TRANSACTION_CONFLICT
- crash atomicity: partial authoritative transition never visible
- projection/audit relation: projections and Event Log are not authority
- machine-readable C1-C7 concurrency scenarios with expected outcomes
- regression design proofs for B011/B013/B014/B014-F/ACTIVATE-R-current-binding/
  BIND-1/RC2-1/RECOVERY-1 with A3_INVARIANT/A3_DESIGN_PROOF/
  IMPLEMENTATION_REQUIRED/FUTURE_OWNER (corpus dispositions stay
  NOT_YET_IMPLEMENTED)
- deferred ownership normalization: transaction scope / CAS semantics / revision
  unit / idempotency transaction semantics resolved by M3-A3; bootstrap ordering
  and cutover -> M3-A4; binding 0/1/many -> M3-A5
- frozen A1 identity rules cross-checked against the checked-out A1/A2/A12
  artifacts: SUBJECT_ID_IS_AUTHORITY=no, ID_IS_AUTHORITY=no,
  CURRENT_POINTERS_ARE_AUTHORITY=no, EVENT_LOG_IS_SUBJECT_AUTHORITY=no,
  EXECUTOR_IDENTITY_EQUALS_PRINCIPAL=no, AUTHORITY_TARGET_IS_TYPED=yes,
  LEASE_REVISION_BOUND=yes, REVISION_SEMANTICS_OWNER=M3-A3,
  AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT=no,
  AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION=yes,
  CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE=LLM_or_user_materialized_decision
- engine boundary: no storage engine frozen, no graph mutation implementation
  (static scan of the aota_forge package), AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no
- changed-path check vs the exact base: only the two M3-A3 files

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a3_revision_cas_guard.py
    python3 scripts/m3_a3_revision_cas_guard.py --json
    python3 scripts/m3_a3_revision_cas_guard.py --artifact /tmp/x.json
    python3 scripts/m3_a3_revision_cas_guard.py --repo-root /path/to/worktree
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_BASE_SHA = "8dad2d3ceeaaf093f35e39daa5c12049c0505527"
M3_A_COMMON_BASE = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

A3_REL = Path("deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json")
GUARD_REL = Path("scripts/m3_a3_revision_cas_guard.py")
A0_MAP_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A1_ART_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
A2_ART_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
A12_ART_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

REQUIRED_SECTIONS = [
    "revision_model",
    "aggregate_boundary",
    "cas_model",
    "lease_revision_contract",
    "transaction_boundary",
    "followup_transaction",
    "multi_subject_policy",
    "concurrency_rules",
    "idempotency_model",
    "conflict_taxonomy",
    "crash_atomicity",
    "projection_relation",
    "audit_relation",
    "concurrency_scenarios",
    "regression_design_proofs",
    "deferred_questions",
    "invariants",
]

# Frozen A1 identity rules the A3 artifact must preserve (exact values).
PRESERVED_FLAGS_EXPECTED = {
    "SUBJECT_ID_IS_AUTHORITY": "no",
    "ID_IS_AUTHORITY": "no",
    "AUTHORITY_TARGET_IS_TYPED": "yes",
    "EXECUTOR_IDENTITY_EQUALS_PRINCIPAL": "no",
    "LEASE_REVISION_BOUND": "yes",
    "REVISION_SEMANTICS_OWNER": "M3-A3",
    "AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT": "no",
    "AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION": "yes",
    "CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE": "LLM_or_user_materialized_decision",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "CURRENT_POINTERS_ARE_PROJECTIONS": "yes",
    "DUAL_AUTHORITATIVE_WRITE": "no",
    "EVENT_LOG_IS_SUBJECT_AUTHORITY": "no",
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": "yes",
    "ERROR_SEMANTICS_DISTINCT": "yes",
}

FROZEN_CONCURRENCY_FLAGS_EXPECTED = {
    "LOST_UPDATE_ALLOWED": "no",
    "SILENT_LAST_WRITE_WINS": "no",
    "STALE_REVISION_MUTATION": "fail_closed",
    "CONCURRENT_VALID_MUTATION": "CAS_or_explicit_serialization",
    "CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT": "yes",
    "CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER": "yes",
    "NO_CAS_BYPASS_FOR_NORMAL_MUTATION": "yes",
    "IDEMPOTENT_REPLAY": "must_not_duplicate_semantic_effects",
    "PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE": "no",
    "STALE_UNEXPIRED_LEASE_DENIED": "yes",
    "STALE_LEASE_REVISION_MUTATION": "denied",
    "LEASE_REISSUE_REQUIRED_AFTER_REVISION_CHANGE": "yes",
    "GLOBAL_PROCESS_MUTEX_CANONICAL": "no",
    "GLOBAL_PROCESS_LOCK_IS_CANONICAL_CONCURRENCY_MODEL": "no",
    "DUPLICATE_CHILD_SEMANTIC_EFFECT_FORBIDDEN": "yes",
    "FORBIDDEN_ORPHAN_CHILD": "yes",
    "FORBIDDEN_EDGE_WITHOUT_CHILD": "yes",
    "FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION": "yes",
    "AUDIT_FAILURE_ROLLS_BACK": "no",
    "CANONICAL_COMMIT_SUCCEEDS_IF_PROJECTION_PUBLICATION_FAILS": "yes",
    "PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY": "yes",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    "NO_AUTHORITATIVE_GRAPH_WRITES": "yes",
}

REVISION_CONTRACT_FLAGS_EXPECTED = {
    "REVISION_UNIT": "subject_aggregate",
    "REVISION_REPRESENTATION_STORAGE_INDEPENDENT": "yes",
    "MODEL_DOES_NOT_CHOREOGRAPH_REVISION": "yes",
    "MODEL_MANAGES_REVISION_CHOREOGRAPHY": "no",
    "WORKFLOW_REVISION_IN_SUBJECT_AGGREGATE": "no",
    "WORKFLOW_RECORD_REVISION_SCOPE": "independent",
    "WORKFLOW_REVISION_SERIALIZES_WORKSUBJECTS": "no",
    "CHILD_INITIAL_REVISION_INDEPENDENT": "yes",
    "PARENT_CHILD_CREATION_ATOMIC": "yes",
}

# Per-scenario semantic requirements (NAME and expected-outcome phrases).
SCENARIO_REQUIREMENTS = {
    "C1": {
        "NAME": "stale writer rejected after concurrent commit",
        "expected_phrases": ["REVISION_CONFLICT", "revision 5", "no lost update"],
    },
    "C2": {
        "NAME": "independent Subjects succeed without global serialization",
        "expected_phrases": ["both commits succeed", "no global serialization", "no global lock"],
    },
    "C3": {
        "NAME": "two otherwise-valid writers on the same Subject/revision",
        "expected_phrases": ["exactly one CAS succeeds", "REVISION_CONFLICT", "no merge"],
    },
    "C4": {
        "NAME": "stale unexpired lease denied on revision mismatch",
        "expected_phrases": ["revision 7", "revision 8", "STALE_LEASE_REVISION_MUTATION=denied",
                             "LEASE_REISSUE_REQUIRED_AFTER_REVISION_CHANGE=yes"],
    },
    "C5": {
        "NAME": "lost response retry with same idempotency key",
        "expected_phrases": ["same idempotency key", "no semantic effect is duplicated",
                             "IDEMPOTENT_REPLAY=must_not_duplicate_semantic_effects"],
    },
    "C6": {
        "NAME": "same idempotency key with different semantic payload",
        "expected_phrases": ["IDEMPOTENCY_CONFLICT", "never silently overwritten"],
    },
    "C7": {
        "NAME": "concurrent materialization of the same decided followup",
        "expected_phrases": ["at most one child semantic effect", "no duplicate child",
                             "crash_atomicity", "different idempotency keys",
                             "REVISION_CONFLICT", "TRANSACTION_CONFLICT"],
    },
}

ENGINE_BOUNDARY_EXPECTED = {
    "NO_STORAGE_ENGINE_FROZEN": "yes",
    "NO_GRAPH_MUTATION_IMPLEMENTATION": "yes",
    "NO_AUTHORITY_ENGINE": "yes",
    "NO_LEASE_ENGINE": "yes",
    "NO_CAS_ENGINE": "yes",
    "NO_TRANSACTION_ENGINE": "yes",
    "NO_LOCK_LEASE_RECOVERY_ENGINE": "yes",
    "NO_RUNTIME_BEHAVIOR": "yes",
    "SOURCE_CODE_CHANGED": "no",
}

CONFLICT_CODES = (
    "REVISION_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "PREDECESSOR_STATE_CONFLICT",
    "AUTHORITY_STALE",
    "TRANSACTION_CONFLICT",
)

SCENARIO_IDS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")

REQUIRED_PROOF_CLASSES = (
    "B011",
    "B013",
    "B014",
    "B014-F",
    "ACTIVATE-R-current-binding",
    "BIND-1",
    "RC2-1",
    "RECOVERY-1",
)

PROOF_FIELDS = ("FAILURE_CLASS", "A3_INVARIANT", "A3_DESIGN_PROOF",
                "IMPLEMENTATION_REQUIRED", "FUTURE_OWNER")

A3_RESOLVED_IDS = ("OQ-M3A4-01", "CAS-SEMANTICS", "REVISION-UNIT", "IDEMPOTENCY-TRANSACTION")
A4_DEFERRED_IDS = ("OQ-M3A5-01", "OQ-M3A5-02")
A5_DEFERRED_IDS = ("SUBJECT-BINDING-ZERO-ONE-MANY",)

REJECTED_ALTERNATIVE_MODELS = ("whole_graph", "workflow_aggregate", "record_level")

ALLOWED_CHANGED_PATHS = frozenset({
    "deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json",
    "scripts/m3_a3_revision_cas_guard.py",
})
FORBIDDEN_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

# A1/A2/A12 accepted truth markers cross-checked against the checked-out artifacts.
A1_TRUTH_MARKERS = [
    ("subject_identity_rule.SUBJECT_ID_IS_AUTHORITY", "no"),
    ("invariants.identity_flags.SUBJECT_ID_IS_AUTHORITY", "no"),
    ("invariants.identity_flags.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
    ("invariants.identity_flags.EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
    ("invariants.identity_flags.RETRY_CREATES_NEW_SUBJECT", "no"),
    ("lifecycle_invariants.frozen_plan_facts_preserved.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
    ("lifecycle_invariants.frozen_plan_facts_preserved.DUAL_AUTHORITATIVE_WRITE", "no"),
    ("lifecycle_invariants.frozen_plan_facts_preserved.EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
    ("lifecycle_invariants.frozen_plan_facts_preserved.LOST_UPDATE_ALLOWED", "no"),
    ("lifecycle_invariants.frozen_plan_facts_preserved.SILENT_LAST_WRITE_WINS", "no"),
]
A2_TRUTH_MARKERS = [
    ("invariants.frozen_authority_flags.ID_IS_AUTHORITY", "no"),
    ("invariants.frozen_authority_flags.LEASE_REVISION_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_OBJECT_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_EXPIRY_ENFORCED", "yes"),
    ("invariants.frozen_authority_flags.LEASE_OBJECT_SCOPE_EXPANSION", "denied"),
    ("invariants.frozen_authority_flags.READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
    ("invariants.design_truths.EXECUTOR_IDENTITY_IS_PRINCIPAL", "no"),
    ("invariants.m3_a2_constraints.AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
]
A12_TRUTH_MARKERS = [
    ("invariants.AUTHORITY_TARGET_IS_TYPED", "yes"),
    ("invariants.LEASE_REVISION_BOUND", "yes"),
    ("invariants.REVISION_SEMANTICS_OWNER", "M3-A3"),
    ("invariants.TRANSACTION_CAS_OWNER", "M3-A3"),
    ("invariants.AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT", "no"),
    ("invariants.AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION", "yes"),
    ("invariants.CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE",
     "LLM_or_user_materialized_decision"),
    ("invariants.AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"),
]

# Conservative static scan: no graph/subject/authority/lease/mutation/transaction
# implementation may exist in aota_forge.
IMPL_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease|Mutation|Transaction|Revision|Cas|CAS)\w*"
    r"(?:Store|Repository|Engine|Broker|Manager|Service|Evaluator|Issuer|Ledger|Vault)\w*\s*[:(]"
)
IMPL_FUNC_RE = re.compile(
    r"def\s+(?:evaluate_authority|authorize_mutation|issue_lease|consume_lease|"
    r"revoke_lease|renew_lease|acquire_lease|validate_lease|grant_lease|"
    r"apply_mutation|perform_mutation|commit_mutation|execute_mutation|write_graph|"
    r"create_subject|transition_subject|cas_commit|compare_and_set|"
    r"begin_transaction|commit_transaction|rollback_transaction)\s*\("
)
IMPL_OP_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease|mutation|transaction|revision)\.[a-z0-9_.-]+"'
)
IMPL_IMPORT_RE = re.compile(
    r"(?:from|import)\s+aota_forge\.(?:core|adapters)\.(?:authority|lease|mutation|graph|transaction|revision|cas)\b"
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


def run_checks(artifact: dict, repo_root: Path, a1: dict, a2: dict, a12: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Top-level identity and exact base SHA.
    for key in ("schema_version", "project_id", "milestone", "phase", "lane", "base_sha"):
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    results.append(_check(artifact.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(artifact.get("project_id") == "aota_forge", "project_id must be aota_forge"))
    results.append(_check(artifact.get("milestone") == "M3" and artifact.get("phase") == "M3-A",
                          "milestone/phase must be M3 / M3-A"))
    results.append(_check(artifact.get("lane") == "M3-A3", "lane must be M3-A3"))
    results.append(_check(
        artifact.get("base_sha") == EXPECTED_BASE_SHA,
        f"base_sha must be {EXPECTED_BASE_SHA} (got {artifact.get('base_sha')!r})",
    ))

    # 2. Required design sections present.
    for section in REQUIRED_SECTIONS:
        results.append(_check(
            isinstance(artifact.get(section), dict) or isinstance(artifact.get(section), list),
            f"required section missing or empty: {section}",
        ))

    # 3. Revision model: subject aggregate selected, alternatives rejected,
    #    storage-independent canonical representation, no choreography.
    rev_model = artifact.get("revision_model", {})
    results.append(_check(rev_model.get("selected_model") == "subject_aggregate",
                          "revision_model.selected_model must be subject_aggregate"))
    alternatives = rev_model.get("evaluated_alternatives", [])
    alt_by_model = {a.get("model"): a for a in alternatives if isinstance(a, dict)}
    for model in REJECTED_ALTERNATIVE_MODELS:
        entry = alt_by_model.get(model)
        results.append(_check(entry is not None and entry.get("verdict") == "rejected",
                              f"revision_model must evaluate {model} as rejected"))
        results.append(_check(isinstance(entry.get("reasons"), list) and entry.get("reasons"),
                              f"revision_model {model} rejection must carry reasons"))
    results.append(_check(rev_model.get("revision_unit") == "subject_aggregate",
                          "revision_model.revision_unit must be subject_aggregate"))
    rev_ownership = rev_model.get("revision_ownership", {})
    results.append(_check(rev_ownership.get("MODEL_MANAGES_REVISION_CHOREOGRAPHY") == "no",
                          "revision_ownership.MODEL_MANAGES_REVISION_CHOREOGRAPHY must be no"))
    results.append(_check("mechanical control-plane" in rev_ownership.get("revision_choreography_rule", ""),
                          "revision choreography must be resolved by the mechanical control plane, never the model"))
    results.append(_check(_is_plain_nonempty(rev_ownership.get("expected_revision_resolution")),
                          "expected revision mechanical resolution must be defined"))
    rev_scope = rev_model.get("revision_scope", {})
    results.append(_check(rev_scope.get("PRIMARY_REVISION_SCOPE") == "subject_aggregate"
                          and rev_scope.get("REVISION_SCOPE_EXCEPTION") == "workflow_record",
                          "revision scope must be primary subject_aggregate with workflow_record exception"))
    results.append(_check("revision_participation" in rev_scope.get("exception_rationale", ""),
                          "workflow_record exception must cite A1 Workflow revision_participation=yes"))
    increment_rule = rev_model.get("revision_increment_rule", {})
    increments = increment_rule.get("increments_subject_aggregate", [])
    for phrase in ("create Execution", "record Completion", "add Decision",
                   "mechanical Subject state transition", "parent FollowupEdge / child creation"):
        results.append(_check(any(phrase in item for item in increments),
                              f"revision_increment_rule must cover {phrase!r}"))
    results.append(_check(_is_plain_nonempty(increment_rule.get("append_only_rule")),
                          "revision_increment_rule.append_only_rule must be defined"))
    results.append(_check(_is_plain_nonempty(increment_rule.get("child_initial_rule")),
                          "revision_increment_rule.child_initial_rule must be defined"))
    results.append(_check(_is_plain_nonempty(increment_rule.get("workflow_mutable_fields_rule")),
                          "revision_increment_rule.workflow_mutable_fields_rule must be defined"))
    representation = rev_model.get("revision_representation", {})
    for key in ("canonical_form", "revision_type", "aggregate_id", "revision_number", "revision_token",
                "storage_independent", "ordering_semantics", "no_choreography"):
        results.append(_check(_is_plain_nonempty(representation.get(key)),
                              f"revision_model.revision_representation.{key} must be defined"))
    results.append(_check("opaque content-derived" in representation.get("revision_type", "")
                          and "monotonic" in representation.get("revision_type", ""),
                          "revision_type must be opaque content-derived token + monotonic integer"))
    results.append(_check("no database" in representation.get("revision_type", "")
                          or "NO_STORAGE_ENGINE_FROZEN" in representation.get("revision_type", ""),
                          "revision_type must not select a database/storage engine"))
    wf_revision = rev_model.get("workflow_revision", {})
    results.append(_check(wf_revision.get("WORKFLOW_REVISION_IN_SUBJECT_AGGREGATE") == "no",
                          "workflow_revision.WORKFLOW_REVISION_IN_SUBJECT_AGGREGATE must be no"))
    results.append(_check(wf_revision.get("WORKFLOW_RECORD_REVISION_SCOPE") == "independent",
                          "workflow_revision.WORKFLOW_RECORD_REVISION_SCOPE must be independent"))
    results.append(_check(wf_revision.get("WORKFLOW_REVISION_SERIALIZES_WORKSUBJECTS") == "no",
                          "workflow_revision.WORKFLOW_REVISION_SERIALIZES_WORKSUBJECTS must be no"))
    results.append(_check("never serializes all WorkSubjects" in wf_revision.get("rule", ""),
                          "workflow_revision rule must state Workflow revision never serializes all WorkSubjects"))
    results.append(_check("revision_participation=yes" in wf_revision.get("rationale", ""),
                          "workflow_revision rationale must preserve A1 revision_participation=yes"))
    sharing = rev_model.get("records_sharing_revision", {})
    for key in ("Subject_root", "Execution", "Completion", "Decision", "FollowupEdge",
                "ParentChildEdge", "Workflow"):
        results.append(_check(key in sharing, f"records_sharing_revision must cover {key}"))
    results.append(_check("independent Workflow-record revision scope" in sharing.get("Workflow", ""),
                          "records_sharing_revision.Workflow must define the independent Workflow-record scope"))
    results.append(_check(rev_model.get("child_initial_revision", {}).get("independent") == "yes",
                          "child initial revision must be independent of the parent"))
    results.append(_check(rev_model.get("parent_child_creation_atomicity", {}).get("atomic") == "yes",
                          "parent -> new-child creation must be atomic"))

    # 4. Aggregate boundary.
    boundary = artifact.get("aggregate_boundary", {})
    results.append(_check(_is_plain_nonempty(boundary.get("aggregate_definition")),
                          "aggregate_boundary.aggregate_definition must be defined"))
    results.append(_check(_is_plain_nonempty(boundary.get("single_root_transactions")),
                          "aggregate_boundary.single_root_transactions must be defined"))
    results.append(_check(_is_plain_nonempty(boundary.get("two_root_transaction_shape")),
                          "aggregate_boundary.two_root_transaction_shape must be defined"))
    results.append(_check(_is_plain_nonempty(boundary.get("edge_attachment")),
                          "aggregate_boundary.edge_attachment must be defined"))

    # 5. CAS model: pre-commit check, fail-closed stale mutation, no bypass
    #    except A4 bootstrap/cutover, model does not choreograph revision.
    cas = artifact.get("cas_model", {})
    results.append(_check(cas.get("CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT") == "yes",
                          "cas_model.CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT must be yes"))
    for key in ("definition", "expected_revision", "current_revision", "pre_commit_check"):
        results.append(_check(_is_plain_nonempty(cas.get(key)),
                              f"cas_model.{key} must be defined"))
    results.append(_check(cas.get("stale_mutation") == "STALE_REVISION_MUTATION=fail_closed",
                          "cas_model.stale_mutation must be STALE_REVISION_MUTATION=fail_closed"))
    no_bypass = cas.get("no_bypass_except", {})
    results.append(_check(set(no_bypass.get("exceptions", [])) == {"M3-A4 bootstrap", "M3-A4 cutover"},
                          "cas_model no_bypass_except must be exactly {M3-A4 bootstrap, M3-A4 cutover}"))
    results.append(_check(no_bypass.get("CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER") == "yes",
                          "CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER must be yes"))
    results.append(_check(no_bypass.get("NO_CAS_BYPASS_FOR_NORMAL_MUTATION") == "yes",
                          "NO_CAS_BYPASS_FOR_NORMAL_MUTATION must be yes"))
    results.append(_check(cas.get("model_does_not_choreograph_revision") == "yes",
                          "cas_model.model_does_not_choreograph_revision must be yes"))
    results.append(_check(_is_plain_nonempty(cas.get("authoritative_read")),
                          "cas_model.authoritative_read must be defined (durable record set only)"))

    # 6. Lease revision contract: typed target + expected revision bound; stale
    #    unexpired lease denied; expiry cannot silently expand authority.
    lease = artifact.get("lease_revision_contract", {})
    results.append(_check(lease.get("LEASE_REVISION_BOUND") == "yes",
                          "lease_revision_contract.LEASE_REVISION_BOUND must be yes"))
    results.append(_check(lease.get("STALE_UNEXPIRED_LEASE_DENIED") == "yes",
                          "lease_revision_contract.STALE_UNEXPIRED_LEASE_DENIED must be yes"))
    results.append(_check(lease.get("STALE_LEASE_REVISION_MUTATION") == "denied",
                          "lease_revision_contract.STALE_LEASE_REVISION_MUTATION must be denied"))
    results.append(_check(lease.get("LEASE_REISSUE_REQUIRED_AFTER_REVISION_CHANGE") == "yes",
                          "lease_revision_contract.LEASE_REISSUE_REQUIRED_AFTER_REVISION_CHANGE must be yes"))
    typed = lease.get("typed_target_binding", "")
    results.append(_check("object_kind" in typed and "object_id" in typed,
                          "lease typed_target_binding must bind object_kind + object_id"))
    results.append(_check("bare Subject ID" in typed and "never authority" in typed,
                          "lease typed target must state bare Subject ID is never authority"))
    results.append(_check(_is_plain_nonempty(lease.get("expected_revision_bound")),
                          "lease expected_revision_bound must be defined"))
    results.append(_check(_is_plain_nonempty(lease.get("stale_unexpired_rule")),
                          "lease stale_unexpired_rule must be defined"))
    results.append(_check("hard denial" in lease.get("expiry_hard_denial", "")
                          or "hard denial" in lease.get("expiry_cannot_expand_authority", ""),
                          "lease expiry must be a hard denial"))
    results.append(_check("expand" in lease.get("expiry_cannot_expand_authority", ""),
                          "lease expiry must not be able to expand authority"))

    # 7. Transaction boundary: authoritative transaction separated from
    #    post-commit projection/audit; no dual authoritative write.
    tx = artifact.get("transaction_boundary", {})
    results.append(_check(_is_plain_nonempty(tx.get("authoritative_transaction")),
                          "transaction_boundary.authoritative_transaction must be defined"))
    results.append(_check(_is_plain_nonempty(tx.get("post_commit_projection")),
                          "transaction_boundary.post_commit_projection must be defined"))
    results.append(_check(_is_plain_nonempty(tx.get("post_commit_audit")),
                          "transaction_boundary.post_commit_audit must be defined"))
    results.append(_check(_is_plain_nonempty(tx.get("separation_rule")),
                          "transaction_boundary.separation_rule must be defined"))
    tx_contents = tx.get("AUTHORITATIVE_TRANSACTION_CONTENTS", [])
    for phrase in ("create Execution", "record Completion", "add Decision",
                   "followup verification + edge + child creation", "revision update",
                   "idempotency reservation and outcome metadata"):
        results.append(_check(any(phrase in item for item in tx_contents),
                              f"AUTHORITATIVE_TRANSACTION_CONTENTS must cover {phrase!r}"))
    proj_contents = tx.get("POST_COMMIT_PROJECTION_CONTENTS", [])
    for phrase in ("current decision projections", "current plan selection",
                   "current followup projection", "binding 0/1/many projection"):
        results.append(_check(any(phrase in item for item in proj_contents),
                              f"POST_COMMIT_PROJECTION_CONTENTS must cover {phrase!r}"))
    results.append(_check("NOT graph authority" in tx.get("idempotency_metadata_relation", "")
                          or "never authorizes" in tx.get("idempotency_metadata_relation", ""),
                          "idempotency metadata must be declared durable dedupe metadata, not graph authority"))
    results.append(_check(tx.get("CANONICAL_COMMIT_SUCCEEDS_IF_PROJECTION_PUBLICATION_FAILS") == "yes",
                          "CANONICAL_COMMIT_SUCCEEDS_IF_PROJECTION_PUBLICATION_FAILS must be yes"))
    results.append(_check("derived and rebuildable" in tx.get("projection_failure_rationale", ""),
                          "projection failure rationale must cite derived/rebuildable projections"))
    results.append(_check(tx.get("transaction_scope_default") == "one transaction = one Subject aggregate (single root)",
                          "transaction_scope_default must be single-Subject aggregate"))
    results.append(_check("no transaction spans the whole graph" in tx.get("no_whole_graph_transaction", ""),
                          "whole-graph transactions must be denied"))

    # 8. Followup transaction: no orphan child, no edge without child, no child
    #    without verified Decision basis, atomic, no duplicate child effect.
    followup = artifact.get("followup_transaction", {})
    results.append(_check(followup.get("FORBIDDEN_ORPHAN_CHILD") == "yes",
                          "FORBIDDEN_ORPHAN_CHILD must be yes"))
    results.append(_check(followup.get("FORBIDDEN_EDGE_WITHOUT_CHILD") == "yes",
                          "FORBIDDEN_EDGE_WITHOUT_CHILD must be yes"))
    results.append(_check(followup.get("FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION") == "yes",
                          "FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION must be yes"))
    results.append(_check(followup.get("PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE") == "no",
                          "followup PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE must be no"))
    results.append(_check(followup.get("DUPLICATE_CHILD_SEMANTIC_EFFECT_FORBIDDEN") == "yes",
                          "DUPLICATE_CHILD_SEMANTIC_EFFECT_FORBIDDEN must be yes"))
    results.append(_check(isinstance(followup.get("commit_unit"), list)
                          and len(followup.get("commit_unit", [])) >= 3,
                          "followup commit_unit must cover child, edge and verified Decision basis"))
    results.append(_check(followup.get("child_revision_independent", "").startswith("yes")
                          or "independent" in followup.get("child_revision_independent", ""),
                          "followup child revision must be independent"))
    results.append(_check(_is_plain_nonempty(followup.get("parent_cas_before_commit")),
                          "followup parent expected-revision CAS before commit must be defined"))
    results.append(_check(_is_plain_nonempty(followup.get("parent_revision_increment")),
                          "followup parent revision increment must be defined"))
    results.append(_check(_is_plain_nonempty(followup.get("verified_materialized_decision")),
                          "followup verified materialized Decision must be defined"))
    results.append(_check(_is_plain_nonempty(followup.get("edge_child_atomicity")),
                          "followup edge + child atomicity must be defined"))
    uniqueness = followup.get("semantic_uniqueness_concurrent", "")
    results.append(_check("at most one child semantic effect" in uniqueness
                          and "different idempotency keys" in uniqueness,
                          "followup must guarantee semantic uniqueness across concurrent attempts with different idempotency keys"))
    results.append(_check("IDEMPOTENCY_CONFLICT" not in uniqueness,
                          "followup distinct-key contention must not be collapsed into key-reuse conflict"))
    multi_parent = followup.get("multi_parent_followup_policy", {})
    results.append(_check(multi_parent.get("MULTI_PARENT_FOLLOWUP_DEFERRED") == "yes",
                          "MULTI_PARENT_FOLLOWUP_DEFERRED must be yes"))
    results.append(_check("exactly ONE existing parent" in multi_parent.get("canonical_shape", ""),
                          "multi-parent policy must state canonical shape is exactly one existing parent + new child"))
    results.append(_check("silently reduced to one parent" in multi_parent.get("no_silent_reduction", "")
                          or "silent parent loss" in multi_parent.get("no_silent_reduction", ""),
                          "multi-parent policy must forbid silent reduction to one parent"))
    results.append(_check("CAS ALL participating parent aggregates" in multi_parent.get("future_rule", ""),
                          "multi-parent future rule must CAS all participating parent aggregates"))
    edge_owner = followup.get("edge_ownership_boundary", {})
    results.append(_check("cross-root lineage record" in edge_owner.get("edge_is_cross_root_lineage_record", ""),
                          "FollowupEdge must be declared a cross-root lineage record, not dual authoritative ownership"))
    results.append(_check("parent CAS and parent revision own edge creation" in edge_owner.get("edge_creation_owner", ""),
                          "edge creation ownership must be the parent CAS / parent revision"))
    results.append(_check("committed lineage snapshot" in edge_owner.get("child_lineage_snapshot", ""),
                          "child initial revision must include the committed lineage snapshot"))
    results.append(_check("no independent edge revision" in edge_owner.get("no_independent_edge_revision", "")
                          or "never mutate later" in edge_owner.get("no_independent_edge_revision", ""),
                          "edges must have no independent revision"))
    results.append(_check("DUAL_AUTHORITATIVE_WRITE=no" in edge_owner.get("no_dual_authority", ""),
                          "edge participation in two roots must not create dual authoritative ownership"))
    results.append(_check(_is_plain_nonempty(followup.get("atomicity_rule")),
                          "followup atomicity_rule must be defined"))

    # 9. Multi-subject policy: default single-Subject; cross-existing-Subject
    #    minimized/deferred; global process mutex NOT canonical.
    multi = artifact.get("multi_subject_policy", {})
    results.append(_check("one authoritative transaction touches exactly one Subject aggregate" in multi.get("default", ""),
                          "multi_subject_policy default must be one transaction = one Subject aggregate"))
    results.append(_check(multi.get("GLOBAL_PROCESS_MUTEX_CANONICAL") == "no",
                          "GLOBAL_PROCESS_MUTEX_CANONICAL must be no (global mutex not canonical)"))
    results.append(_check(multi.get("GLOBAL_PROCESS_LOCK_IS_CANONICAL_CONCURRENCY_MODEL") == "no",
                          "GLOBAL_PROCESS_LOCK_IS_CANONICAL_CONCURRENCY_MODEL must be no"))
    results.append(_check(_is_plain_nonempty(multi.get("global_serialization_rule")),
                          "global serialization rule must be defined"))
    results.append(_check(_is_plain_nonempty(multi.get("explicit_serialization_allowed")),
                          "explicit serialization allowance must be defined"))
    results.append(_check(isinstance(multi.get("deferred_cross_subject"), list)
                          and multi.get("deferred_cross_subject"),
                          "deferred cross-existing-Subject transactions must be enumerated"))
    results.append(_check(any("MULTI_PARENT_FOLLOWUP_DEFERRED" in item
                              or "multi-parent followup" in item
                              for item in multi.get("deferred_cross_subject", [])),
                          "deferred cross-Subject list must include the multi-parent followup policy"))
    results.append(_check(_is_plain_nonempty(multi.get("justification_section")),
                          "multi_subject_policy must require justification for explicit serialization"))

    # 10. Concurrency rules: exact frozen values.
    conc = artifact.get("concurrency_rules", {})
    for key, expected in (
        ("LOST_UPDATE_ALLOWED", "no"),
        ("SILENT_LAST_WRITE_WINS", "no"),
        ("STALE_REVISION_MUTATION", "fail_closed"),
        ("CONCURRENT_VALID_MUTATION", "CAS_or_explicit_serialization"),
    ):
        results.append(_check(conc.get(key) == expected,
                              f"concurrency_rules.{key} must be {expected!r} (got {conc.get(key)!r})"))
    results.append(_check(_is_plain_nonempty(conc.get("independent_subjects")),
                          "concurrency_rules.independent_subjects must be defined"))
    results.append(_check(_is_plain_nonempty(conc.get("same_aggregate")),
                          "concurrency_rules.same_aggregate must be defined"))

    # 11. Idempotency model: scope/content/retention/replay/mismatch; request vs
    #     semantic effect dedupe; replay must not duplicate effects; same key /
    #     different payload rejected.
    idem = artifact.get("idempotency_model", {})
    results.append(_check(idem.get("IDEMPOTENT_REPLAY") == "must_not_duplicate_semantic_effects",
                          "idempotency_model.IDEMPOTENT_REPLAY must be must_not_duplicate_semantic_effects"))
    results.append(_check(_is_plain_nonempty(idem.get("scope")), "idempotency scope must be defined"))
    content = idem.get("content", {})
    key_binding = content.get("idempotency_key_binding", "")
    for phrase in ("principal/authority context", "operation", "typed target", "semantic fingerprint"):
        results.append(_check(phrase in key_binding,
                              f"idempotency key binding must include {phrase!r}"))
    results.append(_check(_is_plain_nonempty(content.get("semantic_fingerprint")),
                          "semantic fingerprint must be defined"))
    req_effect = idem.get("request_vs_semantic_effect", {})
    results.append(_check(_is_plain_nonempty(req_effect.get("request_dedupe")),
                          "request dedupe must be defined"))
    results.append(_check(_is_plain_nonempty(req_effect.get("semantic_effect_dedupe")),
                          "semantic effect dedupe must be defined"))
    results.append(_check(_is_plain_nonempty(idem.get("retention")), "idempotency retention must be defined"))
    results.append(_check(_is_plain_nonempty(idem.get("replay")), "idempotency replay must be defined"))
    mismatch = idem.get("mismatch", "")
    results.append(_check("IDEMPOTENCY_CONFLICT" in mismatch and "never silently overwritten" in mismatch,
                          "same idempotency key/different payload must be rejected with IDEMPOTENCY_CONFLICT"))
    results.append(_check(_is_plain_nonempty(idem.get("duplicate_effect_denied")),
                          "duplicate semantic effect denial must be defined"))

    # 12. Conflict taxonomy: the five distinct codes.
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

    # 13. Crash atomicity: partial authoritative transition never visible.
    crash = artifact.get("crash_atomicity", {})
    results.append(_check(crash.get("PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE") == "no",
                          "crash_atomicity.PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE must be no"))
    for key in ("commit_atomicity", "crash_before_commit", "crash_after_commit",
                "crash_during_commit", "recovery_relation", "no_dual_write"):
        results.append(_check(_is_plain_nonempty(crash.get(key)),
                              f"crash_atomicity.{key} must be defined"))

    # 14. Projection / audit relation: projections and Event Log not authority.
    proj = artifact.get("projection_relation", {})
    results.append(_check(proj.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no",
                          "projection_relation.CURRENT_POINTERS_ARE_AUTHORITY must be no"))
    results.append(_check(proj.get("CURRENT_POINTERS_ARE_PROJECTIONS") == "yes",
                          "projection_relation.CURRENT_POINTERS_ARE_PROJECTIONS must be yes"))
    results.append(_check(proj.get("PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY") == "yes",
                          "projection_relation.PROJECTION_FAILURE_DOES_NOT_CREATE_DUAL_AUTHORITY must be yes"))
    results.append(_check("never competing authority" in proj.get("projection_failure_dual_authority_rationale", "")
                          or "sole authority" in proj.get("projection_failure_dual_authority_rationale", ""),
                          "projection failure rationale must state projections are never competing authority"))
    results.append(_check(_is_plain_nonempty(proj.get("rebuildable")),
                          "projection rebuildability must be defined"))
    audit = artifact.get("audit_relation", {})
    results.append(_check(audit.get("EVENT_LOG_IS_SUBJECT_AUTHORITY") == "no",
                          "audit_relation.EVENT_LOG_IS_SUBJECT_AUTHORITY must be no"))
    results.append(_check("diagnostic/provenance trace" in audit.get("audit_character", ""),
                          "audit_relation.audit_character must label audit as diagnostic/provenance trace, never graph authority"))
    results.append(_check(audit.get("AUDIT_FAILURE_ROLLS_BACK") == "no",
                          "audit_relation.AUDIT_FAILURE_ROLLS_BACK must be no"))
    results.append(_check(_is_plain_nonempty(audit.get("after_commit")),
                          "audit after-commit relation must be defined"))
    results.append(_check(_is_plain_nonempty(audit.get("audit_repair")),
                          "audit repair relation must be defined"))

    # 15. Machine-readable C1-C7 concurrency scenarios with expected outcomes.
    scenarios = artifact.get("concurrency_scenarios", [])
    scenario_ids = [s.get("SCENARIO_ID") for s in scenarios if isinstance(s, dict)]
    results.append(_check(scenario_ids == list(SCENARIO_IDS),
                          f"concurrency_scenarios must be exactly {list(SCENARIO_IDS)}"))
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
    c7_entry = next((s for s in scenarios if s.get("SCENARIO_ID") == "C7"), {})
    results.append(_check(
        "crash_atomicity" in c7_entry.get("expected", ""),
        "scenario C7 must defer crash atomicity to crash_atomicity, not substitute for it"))
    c7_expected = c7_entry.get("expected", "")
    results.append(_check(
        "IDEMPOTENCY_CONFLICT is not the loser outcome" in c7_expected
        or "not key reuse" in c7_expected,
        "scenario C7 must not list IDEMPOTENCY_CONFLICT as a possible loser for distinct-key contention"))

    # 16. Regression design proofs: required classes, required fields,
    #     IMPLEMENTATION_REQUIRED yes/no, FUTURE_OWNER; corpus disposition not
    #     converted.
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

    # 17. Deferred ownership normalization.
    deferred = artifact.get("deferred_questions", {})
    resolved = [e.get("id") for e in deferred.get("resolved_by_a3", []) if isinstance(e, dict)]
    results.append(_check(resolved == list(A3_RESOLVED_IDS),
                          f"deferred_questions.resolved_by_a3 must be exactly {list(A3_RESOLVED_IDS)}"))
    a4_ids = [e.get("QUESTION_ID") for e in deferred.get("deferred_to_a4", []) if isinstance(e, dict)]
    results.append(_check(a4_ids == list(A4_DEFERRED_IDS),
                          f"deferred_to_a4 must be exactly {list(A4_DEFERRED_IDS)}"))
    a5_ids = [e.get("id") for e in deferred.get("deferred_to_a5", []) if isinstance(e, dict)]
    results.append(_check(a5_ids == list(A5_DEFERRED_IDS),
                          f"deferred_to_a5 must be exactly {list(A5_DEFERRED_IDS)}"))
    results.append(_check(isinstance(deferred.get("deferred_to_implementation"), list)
                          and deferred.get("deferred_to_implementation"),
                          "deferred_to_implementation must be a non-empty list"))

    # 18. Invariants: preserved / frozen / revision contract / engine boundary.
    invariants = artifact.get("invariants", {})
    preserved = invariants.get("preserved_contract_flags", {})
    for key, expected in PRESERVED_FLAGS_EXPECTED.items():
        results.append(_check(preserved.get(key) == expected,
                              f"invariants.preserved_contract_flags.{key} must be {expected!r}"))
    frozen = invariants.get("frozen_concurrency_flags", {})
    for key, expected in FROZEN_CONCURRENCY_FLAGS_EXPECTED.items():
        results.append(_check(frozen.get(key) == expected,
                              f"invariants.frozen_concurrency_flags.{key} must be {expected!r}"))
    revision_flags = invariants.get("revision_contract_flags", {})
    for key, expected in REVISION_CONTRACT_FLAGS_EXPECTED.items():
        results.append(_check(revision_flags.get(key) == expected,
                              f"invariants.revision_contract_flags.{key} must be {expected!r}"))
    engine = invariants.get("engine_boundary", {})
    for key, expected in ENGINE_BOUNDARY_EXPECTED.items():
        results.append(_check(engine.get(key) == expected,
                              f"invariants.engine_boundary.{key} must be {expected!r}"))
    results.append(_check(engine.get("GRAPH_STORAGE_LOCATIONS") == [],
                          "engine_boundary.GRAPH_STORAGE_LOCATIONS must be empty (no storage engine frozen)"))

    # 19. Frozen A1 identity rules cross-checked against A1/A2/A12 artifacts.
    for dotted, expected in A1_TRUTH_MARKERS:
        results.append(_check(_dict_get(a1, dotted) == expected,
                              f"A1 cross-check {dotted} must be {expected!r}"))
    # 19b. A1 Workflow canonical record revision_participation preserved
    #      (machine-verified against the checked-out A1 artifact).
    a1_records = a1.get("canonical_records", {}).get("records", [])
    workflow_record = next((r for r in a1_records if isinstance(r, dict) and r.get("record") == "Workflow"), {})
    results.append(_check(bool(workflow_record), "A1 canonical records must contain the Workflow record"))
    wf_participation = workflow_record.get("revision_participation", "")
    results.append(_check(wf_participation.startswith("yes"),
                          f"A1 Workflow revision_participation must start with 'yes' (got {wf_participation!r})"))
    for dotted, expected in A2_TRUTH_MARKERS:
        results.append(_check(_dict_get(a2, dotted) == expected,
                              f"A2 cross-check {dotted} must be {expected!r}"))
    for dotted, expected in A12_TRUTH_MARKERS:
        results.append(_check(_dict_get(a12, dotted) == expected,
                              f"A12 cross-check {dotted} must be {expected!r}"))

    # 20. Engine boundary: no graph mutation / CAS / transaction / lease
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
                          f"graph/CAS/transaction/lease/mutation implementation surface found: {impl_hits}"))
    ingress_text = (aota_pkg / "core" / "ingress.py").read_text(encoding="utf-8")
    results.append(_check(READ_ONLY_GATE_MARKER in ingress_text,
                          f"read-only ingress gate marker missing: {READ_ONLY_GATE_MARKER!r}"))

    # 21. Changed-path check vs the exact base: only the two M3-A3 files.
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
        "artifact": "m3-a3-revision-cas-transaction",
        "lane": artifact.get("lane"),
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A3 revision/CAS/transaction/concurrency guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--artifact", type=Path, help="path to the M3-A3 artifact JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    args = parser.parse_args()

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    repo_root = repo_root.resolve()
    artifact_path = args.artifact or repo_root / A3_REL
    artifact_path = artifact_path.resolve()

    try:
        artifact = _read_json(artifact_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        a1 = _read_json(repo_root / A1_ART_REL)
        a2 = _read_json(repo_root / A2_ART_REL)
        a12 = _read_json(repo_root / A12_ART_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A1/A2/A12 artifact unreadable/invalid: {exc}")
        return 1

    results = run_checks(artifact, repo_root, a1, a2, a12)
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
