#!/usr/bin/env python3
"""M3-A1/A2 contract reconciliation guard (Issue #9, gate M3-A1-A2-CONTRACT-RECONCILIATION).

Design-only reconciliation guard for the serial A0 -> A1 -> A2 worktree.  Validates
deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json against the A0 input
map, the A1 subject/schema artifact and the A2 authority/lease artifact:

- exact top-level identity/gate and the three exact input paths and input commit IDs
- input commits exist in the object store; the checked-out tree carries the A1/A2
  artifact and guard paths; A0/A1/A2 artifact and guard blobs at the input commits
  are identical to the checked-out blobs (git blob provenance)
- accepted A1 truth: canonical schema/identity markers, Subject ID not authority,
  retry/executor no new Subject, Decision owned by one Subject, followup requires
  source Decision lineage
- accepted A2 truth: ID not authority, internal IDs not normal model input, no
  self-asserted privileged principal, executor identity not principal, no semantic
  reasoning, lease principal/operation/object/revision bound, scope expansion
  denied, trusted resource no mutation authority, read-only plane preserved, and
  the deferred list retains OQ-M3A1-03 (old guard allowlist omission tolerated)
- every cross-contract flag/rule (A-F), the typed authority target map, the A3
  handoff readiness criteria, the lane owner DAG and required ownership
  corrections, and both non-blocking findings
- no authoritative graph writes / no source implementation: changed-path check
  against the A0 commit permits only integration evidence/guards plus the two
  reconciliation files, rejects any aota_forge/core/** or aota_forge/adapters/**
  change, and tolerates the A1/A2 sibling evidence files in this serial tree
- static scan of aota_forge for graph/authority/lease/mutation implementation or
  write-operation markers (conservative checks compatible with the A1/A2 guards)
  and the read-only ingress marker

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a12_contract_reconciliation_guard.py
    python3 scripts/m3_a12_contract_reconciliation_guard.py --json
    python3 scripts/m3_a12_contract_reconciliation_guard.py --artifact /tmp/x.json
    python3 scripts/m3_a12_contract_reconciliation_guard.py --repo-root /path/to/worktree
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

A0_COMMIT = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A1_COMMIT = "7dbc2225e0b66f6bf9e6d0970581d96f428d4362"
A2_COMMIT = "b29fe5f6fa47035cdeb4137cd62aafc585257b40"
M3_A_COMMON_BASE = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

RECON_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
GUARD_REL = Path("scripts/m3_a12_contract_reconciliation_guard.py")
A0_MAP_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A1_ART_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
A1_MD_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.md")
A2_ART_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
A1_GUARD_REL = Path("scripts/m3_a1_subject_schema_guard.py")
A2_GUARD_REL = Path("scripts/m3_a2_authority_lease_guard.py")

EXPECTED_INPUTS = {
    "a0": str(A0_MAP_REL),
    "a1": str(A1_ART_REL),
    "a2": str(A2_ART_REL),
}
EXPECTED_INPUT_COMMITS = {
    "a0": A0_COMMIT,
    "a1": A1_COMMIT,
    "a2": A2_COMMIT,
}

SIBLING_PATHS = {
    str(A1_ART_REL),
    str(A1_MD_REL),
    str(A2_ART_REL),
    str(A1_GUARD_REL),
    str(A2_GUARD_REL),
}
RECON_PATHS = {str(RECON_REL), str(GUARD_REL)}
ALLOWED_CHANGED_PATHS = SIBLING_PATHS | RECON_PATHS
FORBIDDEN_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

# Blob provenance: input commit blob must equal the checked-out blob for the
# known A0/A1/A2 artifact and guard paths (sibling blobs are identical because
# the serial tree cherry-picks A0 -> A1 -> A2 without edits).
PROVENANCE_PATHS = {
    A0_COMMIT: [str(A0_MAP_REL)],
    A1_COMMIT: [str(A1_ART_REL), str(A1_GUARD_REL)],
    A2_COMMIT: [str(A2_ART_REL), str(A2_GUARD_REL)],
}

REQUIRED_TOP_LEVEL = [
    "schema_version",
    "artifact",
    "project_id",
    "milestone",
    "phase",
    "gate",
    "inputs",
    "input_commits",
    "cross_contract_rules",
    "lane_owner_normalization",
    "non_blocking_findings",
    "a3_handoff",
    "invariants",
]

# A. Subject identity <-> authority object_ref.
SUBJECT_AUTHORITY_OBJECT_REF_PHRASES = (
    "typed object reference",
    "object_kind",
    "object_id",
    "canonical Subject internal ID",
    "not sufficient to authorize",
    "trusted boundary",
)

# B. Principal <-> executor.
TRUSTED_MAPPING_PHRASES = ("trusted runtime", "injection boundary", "provenance", "freshness")

# C. Execution <-> authority target.
TYPED_TARGET_KEYS = {
    "Subject",
    "Workflow",
    "Execution",
    "Completion",
    "Decision",
    "FollowupEdge_and_other_canonical_objects",
    "Execution.subject_ref",
}

# D. Revision handoff.
# E. Decision-backed followup.
DECISION_ROLE_PHRASES = ("mechanically verifies", "never performs semantic reasoning")

# Lane DAG.
LANE_DAG = [
    ("M3-A1", "Subject Graph Schema & Identity"),
    ("M3-A2", "Internal ID / Principal / Authority / Capability Lease"),
    ("M3-A3", "Revision / CAS / Transaction / Concurrency"),
    ("M3-A4", "Bootstrap / Migration / Cutover / Projection Rebuild"),
    ("M3-A5", "Subject Binding / Ambiguity / Recovery Design Proof"),
    ("M3-A6", "Integrated Design Proof"),
]

# Full union of deferred QUESTION_IDs across the A1 and A2 artifacts.
EXPECTED_UNION_IDS = frozenset(
    [f"OQ-M3A1-{i:02d}" for i in range(1, 7)]
    + [f"OQ-M3A2-{i:02d}" for i in range(1, 5)]
    + ["OQ-M3A4-01", "OQ-M3A5-01", "OQ-M3A5-02"]
    + [f"A1-DEFER-{i:02d}" for i in range(1, 6)]
)

# Exact normalized owner per deferred question (dynamic union mapping).
UNION_OWNER_EXPECTED = {
    "OQ-M3A1-01": "M3-A1",
    "OQ-M3A1-02": "M3-A1",
    "OQ-M3A1-03": "M3-A1",
    "OQ-M3A1-04": "M3-A1",
    "OQ-M3A1-05": "M3-A1",
    "OQ-M3A1-06": "M3-A1",
    "OQ-M3A2-01": "M3-A2",
    "OQ-M3A2-02": "M3-A2",
    "OQ-M3A2-03": "M3-A2",
    "OQ-M3A2-04": "M3-A2",
    "OQ-M3A4-01": "M3-A3",
    "OQ-M3A5-01": "M3-A4",
    "OQ-M3A5-02": "M3-A4",
    "A1-DEFER-01": "M3-A4/M3-A5",
    "A1-DEFER-02": "M3-B",
    "A1-DEFER-03": "M3-B/M4",
    "A1-DEFER-04": "M5",
    "A1-DEFER-05": "M3-B",
}

# Out-of-M3-A DAG entries (implementation/out-of-DAG ownership retained).
OUTSIDE_DAG_OWNERS = frozenset({"A1-DEFER-02", "A1-DEFER-03", "A1-DEFER-04", "A1-DEFER-05"})

# Synthetic correction entries: not deferred questions, kept for normalization.
SYNTHETIC_CORRECTIONS = {
    "CAS-SEMANTICS": "M3-A3",
    "PROJECTION-REBUILD": "M3-A4",
    "SUBJECT-BINDING-ZERO-ONE-MANY": "M3-A5",
}

# A3 handoff readiness criteria: exact key -> expected value.
A3_READINESS = {
    "A1_CONTRACT_ACCEPTED": "yes",
    "A2_CONTRACT_ACCEPTED": "yes",
    "SUBJECT_AUTHORITY_INTERFACE": "PASS",
    "PRINCIPAL_EXECUTOR_INTERFACE": "PASS",
    "REVISION_HANDOFF_TO_A3": "PASS",
    "FOLLOWUP_AUTHORITY_INTERFACE": "PASS",
    "LANE_OWNER_NORMALIZATION": "PASS",
    "NO_A1_A2_CONTRADICTION": "yes",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    "M3_A3_READY": "yes",
}
A3_NEXT_ACTION = "return to ChatGPT for M3-A3 Revision/CAS/Transaction/Concurrency design lane"

# Machine-checked invariants: exact key -> expected value.
REQUIRED_INVARIANTS = {
    "A1_CONTRACT_ACCEPTED": "yes",
    "A2_CONTRACT_ACCEPTED": "yes",
    "SUBJECT_ID_IS_AUTHORITY": "no",
    "ID_IS_AUTHORITY": "no",
    "EXECUTOR_IDENTITY_DEFINES_SUBJECT_IDENTITY": "no",
    "EXECUTOR_IDENTITY_DEFINES_PRINCIPAL_AUTHORITY": "no",
    "EXECUTOR_IDENTITY_EQUALS_PRINCIPAL": "no",
    "CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT": "yes",
    "CHANGE_EXECUTOR_MUST_NOT_SILENTLY_CHANGE_AUTHORITY": "yes",
    "AUTHORITY_TARGET_IS_TYPED": "yes",
    "LEASE_REVISION_BOUND": "yes",
    "LEASE_SCOPE_EXPANSION": "denied",
    "REVISION_SEMANTICS_OWNER": "M3-A3",
    "AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT": "no",
    "AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION": "yes",
    "CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE": "LLM_or_user_materialized_decision",
    "TRANSACTION_CAS_OWNER": "M3-A3",
    "BOOTSTRAP_CUTOVER_OWNER": "M3-A4",
    "BINDING_AMBIGUITY_OWNER": "M3-A5",
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": "yes",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
    "NO_AUTHORITATIVE_GRAPH_WRITES": "yes",
    "NO_A1_A2_CONTRADICTION": "yes",
}

# Accepted contract status: exact machine-readable candidate accepted truth.
A1_ACCEPTED_EXPECTED = {
    "GRAPH_CANONICAL_SCHEMA": "PASS",
    "SUBJECT_IDENTITY_RULE": "PASS",
    "SUBJECT_ID_IS_AUTHORITY": "no",
    "RETRY_CREATES_NEW_SUBJECT": "no",
    "CHANGE_EXECUTOR_CREATES_NEW_SUBJECT": "no",
    "HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY": "no",
    "CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY": "no",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "HEURISTIC_SUBJECT_IDENTITY_ALLOWED": "no",
}
A1_ACCEPTED_CROSS = {
    "GRAPH_CANONICAL_SCHEMA": ("GRAPH_CANONICAL_SCHEMA", "defined"),
    "SUBJECT_IDENTITY_RULE": ("SUBJECT_IDENTITY_RULE", "defined"),
    "SUBJECT_ID_IS_AUTHORITY": ("subject_identity_rule.SUBJECT_ID_IS_AUTHORITY", "no"),
    "RETRY_CREATES_NEW_SUBJECT": ("invariants.identity_flags.RETRY_CREATES_NEW_SUBJECT", "no"),
    "CHANGE_EXECUTOR_CREATES_NEW_SUBJECT": ("invariants.identity_flags.CHANGE_EXECUTOR_CREATES_NEW_SUBJECT", "no"),
    "HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY": ("executor_private_boundary.flags.HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY", "no"),
    "CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY": ("invariants.identity_flags.CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY", "no"),
    "CURRENT_POINTERS_ARE_AUTHORITY": ("invariants.identity_flags.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
    "HEURISTIC_SUBJECT_IDENTITY_ALLOWED": ("invariants.identity_flags.HEURISTIC_SUBJECT_IDENTITY_ALLOWED", "no"),
}
A2_ACCEPTED_EXPECTED = {
    "INTERNAL_ID_MODEL": "PASS",
    "PRINCIPAL_MODEL": "PASS",
    "AUTHORITY_EVALUATION_MODEL": "PASS",
    "CAPABILITY_LEASE_LIFECYCLE": "PASS",
    "ID_IS_AUTHORITY": "no",
    "MODEL_INTERNAL_IDS_NORMAL_INPUT": "no",
    "UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL": "no",
    "AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING": "no",
    "LEASE_PRINCIPAL_BOUND": "yes",
    "LEASE_OPERATION_BOUND": "yes",
    "LEASE_OBJECT_BOUND": "yes",
    "LEASE_REVISION_BOUND": "yes",
    "LEASE_SCOPE_EXPANSION": "denied",
    "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY": "no",
    "NF2_RESOLUTION_CONTRACT": "PASS",
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": "yes",
}
A2_ACCEPTED_CROSS = {
    "ID_IS_AUTHORITY": ("invariants.frozen_authority_flags.ID_IS_AUTHORITY", "no"),
    "MODEL_INTERNAL_IDS_NORMAL_INPUT": ("invariants.frozen_authority_flags.MODEL_INTERNAL_IDS_NORMAL_INPUT", "no"),
    "UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL": ("invariants.frozen_authority_flags.UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL", "no"),
    "AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING": ("invariants.frozen_authority_flags.AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING", "no"),
    "LEASE_PRINCIPAL_BOUND": ("invariants.frozen_authority_flags.LEASE_PRINCIPAL_BOUND", "yes"),
    "LEASE_OPERATION_BOUND": ("invariants.frozen_authority_flags.LEASE_OPERATION_BOUND", "yes"),
    "LEASE_OBJECT_BOUND": ("invariants.frozen_authority_flags.LEASE_OBJECT_BOUND", "yes"),
    "LEASE_REVISION_BOUND": ("invariants.frozen_authority_flags.LEASE_REVISION_BOUND", "yes"),
    "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY": ("invariants.frozen_authority_flags.TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "no"),
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": ("invariants.frozen_authority_flags.READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
}
A2_MODEL_PASS_SUMMARIES = (
    "INTERNAL_ID_MODEL",
    "PRINCIPAL_MODEL",
    "AUTHORITY_EVALUATION_MODEL",
    "CAPABILITY_LEASE_LIFECYCLE",
    "NF2_RESOLUTION_CONTRACT",
)
ID_CATEGORY_IDS = {
    "semantic_public_reference",
    "internal_durable_object_id",
    "execution_private_id",
    "correlation_id",
    "adapter_private_executor_id",
    "lease_id",
}
PRINCIPAL_CLASS_IDS = {
    "human",
    "llm_agent_session",
    "executor_adapter",
    "system_mechanical",
    "operator_debug",
}
DECISION_CLASS_IDS = {"ALLOW", "DENY", "BLOCKED", "NEEDS_APPROVAL"}
LEASE_LIFECYCLE_STAGES = (
    "issuance", "validation", "expiry", "consumption", "replay",
    "revocation", "renewal", "expansion", "substitution", "delegation",
)

# Accepted A1 truth markers (paths into the A1 artifact).
A1_TRUTH_MARKERS = [
    ("GRAPH_CANONICAL_SCHEMA", "defined"),
    ("SUBJECT_IDENTITY_RULE", "defined"),
    ("subject_identity_rule.SUBJECT_ID_IS_AUTHORITY", "no"),
    ("invariants.identity_flags.SUBJECT_ID_IS_AUTHORITY", "no"),
    ("invariants.identity_flags.RETRY_CREATES_NEW_SUBJECT", "no"),
    ("invariants.identity_flags.CHANGE_EXECUTOR_CREATES_NEW_SUBJECT", "no"),
    ("invariants.identity_flags.CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT", "yes"),
    ("executor_private_boundary.flags.HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY", "no"),
    ("invariants.identity_flags.CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY", "no"),
    ("invariants.identity_flags.CURRENT_POINTERS_ARE_AUTHORITY", "no"),
    ("invariants.identity_flags.HEURISTIC_SUBJECT_IDENTITY_ALLOWED", "no"),
    ("invariants.record_flags.FOLLOWUP_REQUIRES_SEMANTIC_LINEAGE", "yes"),
]

# Accepted A2 truth markers (paths into the A2 artifact).
A2_TRUTH_MARKERS = [
    ("invariants.frozen_authority_flags.ID_IS_AUTHORITY", "no"),
    ("invariants.frozen_authority_flags.MODEL_INTERNAL_IDS_NORMAL_INPUT", "no"),
    ("invariants.frozen_authority_flags.RAW_INTERNAL_ID_MODEL_SURFACE", "denied_by_default"),
    ("invariants.frozen_authority_flags.UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL", "no"),
    ("invariants.frozen_authority_flags.AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING", "no"),
    ("invariants.frozen_authority_flags.LEASE_PRINCIPAL_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_OPERATION_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_OBJECT_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_REVISION_BOUND", "yes"),
    ("invariants.frozen_authority_flags.LEASE_OBJECT_SCOPE_EXPANSION", "denied"),
    ("invariants.frozen_authority_flags.LEASE_OPERATION_SCOPE_EXPANSION", "denied"),
    ("invariants.frozen_authority_flags.TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "no"),
    ("invariants.frozen_authority_flags.READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
    ("invariants.design_truths.EXECUTOR_IDENTITY_IS_PRINCIPAL", "no"),
]

# Conservative static scan: no graph/authority/lease/mutation implementation.
IMPL_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease|Mutation)\w*"
    r"(?:Store|Repository|Engine|Broker|Manager|Service|Evaluator|Issuer|Ledger|Vault)\w*\s*[:(]"
)
IMPL_FUNC_RE = re.compile(
    r"def\s+(?:evaluate_authority|authorize_mutation|issue_lease|consume_lease|"
    r"revoke_lease|renew_lease|acquire_lease|validate_lease|grant_lease|"
    r"apply_mutation|perform_mutation|commit_mutation|execute_mutation|write_graph|"
    r"create_subject|transition_subject)\s*\("
)
IMPL_OP_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease|mutation)\.[a-z0-9_.-]+"'
)
IMPL_IMPORT_RE = re.compile(
    r"(?:from|import)\s+aota_forge\.(?:core|adapters)\.(?:authority|lease|mutation|graph)\b"
)
READ_ONLY_GATE_MARKER = "operation is not read-only in M2"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout.strip()


def _dict_get(data: dict, dotted_path: str):
    node = data
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _a2_model_pass(a2: dict, model: str) -> bool:
    """Cross-check an A2 summary PASS marker against the A2 contract sections."""
    if model == "INTERNAL_ID_MODEL":
        id_model = a2.get("id_model", {})
        cats = {r.get("category") for r in id_model.get("id_categories", []) if isinstance(r, dict)}
        return cats == ID_CATEGORY_IDS and id_model.get("raw_internal_id_rule") == "deny_by_default"
    if model == "PRINCIPAL_MODEL":
        p_model = a2.get("principal_model", {})
        classes = {p.get("class") for p in p_model.get("principal_classes", []) if isinstance(p, dict)}
        return classes == PRINCIPAL_CLASS_IDS and "cannot self-assert" in p_model.get("self_assertion_rule", "")
    if model == "AUTHORITY_EVALUATION_MODEL":
        return set(a2.get("authority_evaluation_model", {}).get("decision_classes", {})) == DECISION_CLASS_IDS
    if model == "CAPABILITY_LEASE_LIFECYCLE":
        lifecycle = a2.get("capability_lease_contract", {}).get("lifecycle", {})
        return all(bool(lifecycle.get(stage)) for stage in LEASE_LIFECYCLE_STAGES)
    if model == "NF2_RESOLUTION_CONTRACT":
        nf2 = a2.get("nf2_resolution_contract", {})
        return (isinstance(nf2.get("RESOLVED_DESIGN_RULES"), list)
                and bool(nf2["RESOLVED_DESIGN_RULES"])
                and nf2.get("NF2_SOURCE_FIXED") == "no")
    return False


def run_checks(recon: dict, repo_root: Path, a0_map: dict, a1: dict, a2: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Top-level identity and gate.
    for key in REQUIRED_TOP_LEVEL:
        results.append(_check(key in recon, f"artifact missing top-level field: {key}"))
    results.append(_check(recon.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(recon.get("project_id") == "aota_forge", "project_id must be aota_forge"))
    results.append(_check(recon.get("milestone") == "M3" and recon.get("phase") == "M3-A",
                          "milestone/phase must be M3 / M3-A"))
    results.append(_check(
        recon.get("gate") == "M3-A1-A2-CONTRACT-RECONCILIATION",
        "gate must be M3-A1-A2-CONTRACT-RECONCILIATION",
    ))

    # 2. Exact input paths and input commit IDs.
    inputs = recon.get("inputs", {})
    for key, expected_path in EXPECTED_INPUTS.items():
        results.append(_check(inputs.get(key) == expected_path,
                              f"inputs.{key} must be {expected_path!r} (got {inputs.get(key)!r})"))
    input_commits = recon.get("input_commits", {})
    for key, expected_commit in EXPECTED_INPUT_COMMITS.items():
        results.append(_check(input_commits.get(key) == expected_commit,
                              f"input_commits.{key} must be {expected_commit!r} (got {input_commits.get(key)!r})"))

    # 2b. Accepted contract status (machine-readable candidate accepted truth).
    accepted = recon.get("accepted_contracts", {})
    results.append(_check(isinstance(accepted, dict) and "a1" in accepted and "a2" in accepted,
                          "accepted_contracts must define a1 and a2 sections"))
    a1_accepted = accepted.get("a1", {})
    a2_accepted = accepted.get("a2", {})
    results.append(_check(a1_accepted.get("accepted") == "yes",
                          "accepted_contracts.a1.accepted must be yes"))
    results.append(_check(a2_accepted.get("accepted") == "yes",
                          "accepted_contracts.a2.accepted must be yes"))
    results.append(_check(a1_accepted.get("source_artifact") == str(A1_ART_REL),
                          "accepted_contracts.a1.source_artifact must be the A1 artifact path"))
    results.append(_check(a2_accepted.get("source_artifact") == str(A2_ART_REL),
                          "accepted_contracts.a2.source_artifact must be the A2 artifact path"))
    a1_status = a1_accepted.get("status", {})
    results.append(_check(a1_status == dict(A1_ACCEPTED_EXPECTED),
                          f"accepted_contracts.a1.status must be exactly {sorted(A1_ACCEPTED_EXPECTED)}"))
    for status_key, expected in A1_ACCEPTED_EXPECTED.items():
        results.append(_check(a1_status.get(status_key) == expected,
                              f"accepted_contracts.a1.status.{status_key} must be {expected!r}"))
    for status_key, (dotted, expected) in A1_ACCEPTED_CROSS.items():
        results.append(_check(_dict_get(a1, dotted) == expected,
                              f"A1 accepted status {status_key} cross-check ({dotted}) must be {expected!r}"))
    a2_status = a2_accepted.get("status", {})
    results.append(_check(a2_status == dict(A2_ACCEPTED_EXPECTED),
                          f"accepted_contracts.a2.status must be exactly {sorted(A2_ACCEPTED_EXPECTED)}"))
    for status_key, expected in A2_ACCEPTED_EXPECTED.items():
        results.append(_check(a2_status.get(status_key) == expected,
                              f"accepted_contracts.a2.status.{status_key} must be {expected!r}"))
    for status_key, (dotted, expected) in A2_ACCEPTED_CROSS.items():
        results.append(_check(_dict_get(a2, dotted) == expected,
                              f"A2 accepted status {status_key} cross-check ({dotted}) must be {expected!r}"))
    results.append(_check(
        _dict_get(a2, "invariants.frozen_authority_flags.LEASE_OBJECT_SCOPE_EXPANSION") == "denied"
        and _dict_get(a2, "invariants.frozen_authority_flags.LEASE_OPERATION_SCOPE_EXPANSION") == "denied",
        "A2 accepted status LEASE_SCOPE_EXPANSION=denied must hold for object and operation scope expansion",
    ))
    for model in A2_MODEL_PASS_SUMMARIES:
        results.append(_check(_a2_model_pass(a2, model),
                              f"A2 accepted status {model}=PASS cross-check failed against the A2 artifact"))

    # 3. Cross-contract rules: section A (subject identity <-> object_ref).
    rules = recon.get("cross_contract_rules", {})
    rule_a = rules.get("A_SUBJECT_IDENTITY_TO_OBJECT_REF", {})
    ref_rule = rule_a.get("SUBJECT_AUTHORITY_OBJECT_REFERENCE_RULE", "")
    for phrase in SUBJECT_AUTHORITY_OBJECT_REF_PHRASES:
        results.append(_check(phrase in ref_rule,
                              f"SUBJECT_AUTHORITY_OBJECT_REFERENCE_RULE must state: {phrase!r}"))
    results.append(_check(rule_a.get("SUBJECT_ID_IS_AUTHORITY") == "no",
                          "rule A SUBJECT_ID_IS_AUTHORITY must be no"))
    results.append(_check(bool(rule_a.get("source")), "rule A must cite its A1/A2 source"))

    # 4. Rule B (principal <-> executor).
    rule_b = rules.get("B_PRINCIPAL_TO_EXECUTOR", {})
    results.append(_check(rule_b.get("EXECUTOR_IDENTITY_EQUALS_PRINCIPAL") == "no",
                          "rule B EXECUTOR_IDENTITY_EQUALS_PRINCIPAL must be no (A2 exact truth)"))
    results.append(_check(rule_b.get("CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT") == "yes",
                          "rule B CHANGE_EXECUTOR_NOT_CHANGE_SUBJECT must be yes"))
    results.append(_check(rule_b.get("CHANGE_EXECUTOR_MUST_NOT_SILENTLY_CHANGE_AUTHORITY") == "yes",
                          "rule B CHANGE_EXECUTOR_MUST_NOT_SILENTLY_CHANGE_AUTHORITY must be yes"))
    mapping = rule_b.get("trusted_mapping_boundary", "")
    for phrase in TRUSTED_MAPPING_PHRASES:
        results.append(_check(phrase in mapping,
                              f"rule B trusted_mapping_boundary must state: {phrase!r}"))
    results.append(_check(
        "new Execution under the same Subject" in mapping,
        "rule B must state executor change creates a new Execution under the same Subject",
    ))

    # 5. Rule C (execution <-> authority target).
    rule_c = rules.get("C_EXECUTION_TO_AUTHORITY_TARGET", {})
    results.append(_check(rule_c.get("AUTHORITY_TARGET_IS_TYPED") == "yes",
                          "rule C AUTHORITY_TARGET_IS_TYPED must be yes"))
    results.append(_check(rule_c.get("HEURISTIC_TARGET_INFERENCE_ALLOWED") == "no",
                          "rule C HEURISTIC_TARGET_INFERENCE_ALLOWED must be no"))
    target_map = rule_c.get("typed_target_map", {})
    results.append(_check(isinstance(target_map, dict) and set(target_map) == TYPED_TARGET_KEYS,
                          f"rule C typed_target_map must be exactly {sorted(TYPED_TARGET_KEYS)}"))
    for key in ("Subject", "Workflow", "Execution", "Completion", "Decision", "FollowupEdge_and_other_canonical_objects"):
        results.append(_check(isinstance(target_map.get(key), str) and target_map[key].strip(),
                              f"rule C typed_target_map.{key} must be a non-empty explanation"))
    results.append(_check(
        "does not silently become an authority" in target_map.get("Execution.subject_ref", ""),
        "rule C Execution.subject_ref must not silently become authority",
    ))
    results.append(_check(
        "no universal" in rule_c.get("universal_target_rule", "").lower()
        or "no single universal" in rule_c.get("universal_target_rule", "").lower(),
        "rule C must reject a single universal authority target",
    ))

    # 6. Rule D (revision handoff to M3-A3).
    rule_d = rules.get("D_REVISION_HANDOFF", {})
    results.append(_check(rule_d.get("LEASE_REVISION_BOUND") == "yes",
                          "rule D LEASE_REVISION_BOUND must be yes"))
    results.append(_check(rule_d.get("REVISION_SEMANTICS_OWNER") == "M3-A3",
                          "rule D REVISION_SEMANTICS_OWNER must be M3-A3"))
    results.append(_check(rule_d.get("TRANSACTION_CAS_OWNER") == "M3-A3",
                          "rule D TRANSACTION_CAS_OWNER must be M3-A3"))
    rev_semantics = rule_d.get("expected_revision_semantics", "")
    results.append(_check("M3-A3" in rev_semantics and "defines no revision numbering" in rev_semantics,
                          "rule D must defer revision semantics to M3-A3 without defining them"))

    # 7. Rule E (decision-backed followup).
    rule_e = rules.get("E_DECISION_BACKED_FOLLOWUP", {})
    results.append(_check(rule_e.get("AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT") == "no",
                          "rule E AUTHORITY_ENGINE_MAY_INFER_FOLLOWUP_INTENT must be no"))
    results.append(_check(rule_e.get("AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION") == "yes",
                          "rule E AUTHORITY_ENGINE_MAY_VERIFY_MATERIALIZED_DECISION must be yes"))
    results.append(_check(
        "Decision-backed child Subject" in rule_e.get("semantic_followup_shape", ""),
        "rule E must define semantic followup as an A1 Decision-backed child Subject",
    ))
    decision_role = rule_e.get("decision_role", "")
    for phrase in DECISION_ROLE_PHRASES:
        results.append(_check(phrase in decision_role,
                              f"rule E decision_role must state: {phrase!r}"))
    results.append(_check("valid lease" in decision_role and "revision" in decision_role,
                          "rule E must keep valid lease + exact target revision required"))

    # 8. Rule F (child Subject creation).
    rule_f = rules.get("F_CHILD_SUBJECT_CREATION", {})
    results.append(_check(
        rule_f.get("CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE")
        == "LLM_or_user_materialized_decision",
        "rule F CHILD_SUBJECT_CREATION_SEMANTIC_DECISION_SOURCE must be "
        "LLM_or_user_materialized_decision (exact machine value)",
    ))
    creation_rule = rule_f.get("child_creation_rule", "")
    for phrase in ("LLM/user authority", "materialized as a Decision", "valid lease", "exact target revision"):
        results.append(_check(phrase in creation_rule,
                              f"rule F child_creation_rule must state: {phrase!r}"))

    # 9. Lane owner DAG.
    dag = recon.get("lane_owner_dag", [])
    results.append(_check(isinstance(dag, list) and len(dag) == len(LANE_DAG),
                          f"lane_owner_dag must contain exactly {len(LANE_DAG)} lanes"))
    if isinstance(dag, list):
        dag_entries = [(entry.get("lane"), entry.get("scope")) for entry in dag if isinstance(entry, dict)]
        results.append(_check(dag_entries == LANE_DAG,
                              f"lane_owner_dag must be exactly {[e[0] for e in LANE_DAG]} with their scopes"))

    # 10. Lane owner normalization: full union of A1+A2 deferred questions,
    #     required ownership corrections and out-of-DAG retention.
    a1_deferred_ids = [q.get("QUESTION_ID") for q in a1.get("deferred_questions", []) if isinstance(q, dict)]
    a2_deferred_ids = [q.get("QUESTION_ID") for q in a2.get("deferred_questions", []) if isinstance(q, dict)]
    results.append(_check(len(a1_deferred_ids) == len(set(a1_deferred_ids)),
                          "A1 deferred_questions must not contain duplicate QUESTION_IDs"))
    results.append(_check(len(a2_deferred_ids) == len(set(a2_deferred_ids)),
                          "A2 deferred_questions must not contain duplicate QUESTION_IDs"))
    union_ids = sorted(set(a1_deferred_ids) | set(a2_deferred_ids))
    results.append(_check(set(union_ids) == EXPECTED_UNION_IDS,
                          f"deferred-question union must be exactly {sorted(EXPECTED_UNION_IDS)} (got {union_ids})"))

    normalization = recon.get("lane_owner_normalization", [])
    results.append(_check(isinstance(normalization, list) and normalization,
                          "lane_owner_normalization must be a non-empty list"))
    norm_ids = [e.get("id") for e in normalization if isinstance(e, dict) and e.get("id")]
    duplicates = sorted({nid for nid in norm_ids if norm_ids.count(nid) > 1})
    results.append(_check(not duplicates,
                          f"lane_owner_normalization must not contain duplicate ids: {duplicates}"))
    norm_by_id = {}
    if isinstance(normalization, list):
        for entry in normalization:
            if isinstance(entry, dict) and entry.get("id"):
                norm_by_id[entry["id"]] = entry
    results.append(_check(
        all(qid in norm_by_id for qid in union_ids),
        f"lane_owner_normalization must cover every deferred question; missing: "
        f"{sorted(set(union_ids) - set(norm_by_id))}",
    ))

    def _expected_sources(qid: str) -> set:
        sources = set()
        if qid in a1_deferred_ids:
            sources.add("M3-A1")
        if qid in a2_deferred_ids:
            sources.add("M3-A2")
        return sources

    for qid in union_ids:
        entry = norm_by_id.get(qid)
        if entry is None:
            continue
        expected_owner = UNION_OWNER_EXPECTED[qid]
        results.append(_check(entry.get("normalized_owner") == expected_owner,
                              f"normalized owner for {qid} must be {expected_owner!r} "
                              f"(got {entry.get('normalized_owner')!r})"))
        expected_sources = _expected_sources(qid)
        results.append(_check(set(entry.get("input_sources", [])) == expected_sources,
                              f"{qid} input_sources must be {sorted(expected_sources)} "
                              f"(got {sorted(entry.get('input_sources', []))})"))
        if qid in OUTSIDE_DAG_OWNERS:
            results.append(_check(entry.get("outside_m3_a_dag") is True,
                                  f"{qid} must be recorded as outside the M3-A DAG"))
        else:
            results.append(_check(entry.get("outside_m3_a_dag") in (None, False),
                                  f"{qid} is an M3-A DAG entry and must not be marked outside_m3_a_dag"))

    synthetic = set(norm_ids) - set(union_ids)
    results.append(_check(synthetic == set(SYNTHETIC_CORRECTIONS),
                          f"normalization extra ids must be exactly the synthetic corrections "
                          f"{sorted(SYNTHETIC_CORRECTIONS)} (got {sorted(synthetic)})"))
    for correction_id, owner in SYNTHETIC_CORRECTIONS.items():
        entry = norm_by_id.get(correction_id, {})
        results.append(_check(entry.get("normalized_owner") == owner,
                              f"synthetic correction {correction_id} must have owner {owner}"))

    # 10b. deferred_question_union summary must match the computed union exactly.
    union_summary = recon.get("deferred_question_union", {})
    summary_entries = union_summary.get("entries", []) if isinstance(union_summary, dict) else []
    summary_ids = [e.get("QUESTION_ID") for e in summary_entries if isinstance(e, dict)]
    results.append(_check(bool(summary_ids),
                          "deferred_question_union summary must be present with entries"))
    results.append(_check(union_summary.get("entry_count") == len(union_ids),
                          f"deferred_question_union.entry_count must be {len(union_ids)}"))
    results.append(_check(set(summary_ids) == set(union_ids),
                          "deferred_question_union entries must match the computed union exactly"))
    for qid in union_ids:
        entry = next((e for e in summary_entries if isinstance(e, dict) and e.get("QUESTION_ID") == qid), {})
        results.append(_check(entry.get("normalized_owner") == UNION_OWNER_EXPECTED[qid],
                              f"deferred_question_union {qid} owner must be {UNION_OWNER_EXPECTED[qid]!r}"))
        results.append(_check(set(entry.get("input_sources", [])) == _expected_sources(qid),
                              f"deferred_question_union {qid} input_sources must be "
                              f"{sorted(_expected_sources(qid))}"))

    # 11. Non-blocking findings.
    findings = recon.get("non_blocking_findings", [])
    findings_by_id = {}
    if isinstance(findings, list):
        findings_by_id = {f.get("finding_id"): f for f in findings if isinstance(f, dict)}
    allowlist_finding = findings_by_id.get("A2_DEFERRED_ID_ALLOWLIST_FINDING", {})
    results.append(_check(allowlist_finding.get("classification") == "non_blocking",
                          "A2_DEFERRED_ID_ALLOWLIST_FINDING must be non_blocking"))
    results.append(_check(allowlist_finding.get("DESIGN_CONTRACT_IMPACT") == "none",
                          "A2_DEFERRED_ID_ALLOWLIST_FINDING DESIGN_CONTRACT_IMPACT must be none"))
    results.append(_check(
        "OQ-M3A1-03" in allowlist_finding.get("evidence", "")
        and "deferred_questions" in allowlist_finding.get("evidence", ""),
        "allowlist finding evidence must cite OQ-M3A1-03 retained in A2 deferred_questions",
    ))
    results.append(_check(
        "guard cleanup" in allowlist_finding.get("action", "").lower(),
        "allowlist finding must record that no guard cleanup was performed",
    ))
    diag_finding = findings_by_id.get("A2_NON_JSON_ARTIFACT_DIAGNOSTIC_PATH", {})
    results.append(_check(diag_finding.get("classification") == "non_blocking",
                          "A2_NON_JSON_ARTIFACT_DIAGNOSTIC_PATH must be non_blocking"))
    results.append(_check(diag_finding.get("A2_CONTRACT_BLOCKING") == "no",
                          "A2_NON_JSON_ARTIFACT_DIAGNOSTIC_PATH A2_CONTRACT_BLOCKING must be no"))
    results.append(_check(diag_finding.get("M3_A3_BLOCKING") == "no",
                          "A2_NON_JSON_ARTIFACT_DIAGNOSTIC_PATH M3_A3_BLOCKING must be no"))

    # 12. A3 handoff readiness criteria.
    handoff = recon.get("a3_handoff", {})
    readiness = handoff.get("readiness_criteria", {})
    for key, expected in A3_READINESS.items():
        results.append(_check(readiness.get(key) == expected,
                              f"a3_handoff.readiness_criteria.{key} must be {expected!r}"))
    results.append(_check(
        readiness.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
        "A3 handoff must keep AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no",
    ))
    results.append(_check(handoff.get("next_action") == A3_NEXT_ACTION,
                          "a3_handoff.next_action must be the exact M3-A3 handoff action"))
    results.append(_check(handoff.get("autostart_a3") is False,
                          "a3_handoff must not autostart M3-A3"))

    # 13. Machine-checked invariants (exact set and values).
    invariants = recon.get("invariants", {})
    results.append(_check(isinstance(invariants, dict) and set(invariants) == set(REQUIRED_INVARIANTS),
                          f"invariants must be exactly {sorted(REQUIRED_INVARIANTS)}"))
    for key, expected in REQUIRED_INVARIANTS.items():
        results.append(_check(invariants.get(key) == expected,
                              f"invariants.{key} must be {expected!r} (got {invariants.get(key)!r})"))

    # 14. Source boundary: design artifacts only, exact changed paths.
    boundary = recon.get("source_boundary", {})
    results.append(_check(boundary.get("source_mutation_scope") == "design_artifacts_only",
                          "source_boundary.source_mutation_scope must be design_artifacts_only"))
    results.append(_check(set(boundary.get("changed_paths", [])) == RECON_PATHS,
                          f"source_boundary.changed_paths must be exactly {sorted(RECON_PATHS)}"))
    results.append(_check(set(boundary.get("forbidden_paths", [])) == {
                              "aota_forge/core/**", "aota_forge/adapters/**"},
                          "source_boundary.forbidden_paths must be core/adapters trees"))
    for key in ("a1_a2_artifacts_preserved", "a1_a2_guards_preserved",
                "a0_artifact_preserved", "m2_corpus_and_matrix_preserved"):
        results.append(_check(boundary.get(key) == "yes",
                              f"source_boundary.{key} must be yes"))

    # 15. Accepted A1 truth (as read from the checked-out A1 artifact).
    for dotted, expected in A1_TRUTH_MARKERS:
        results.append(_check(_dict_get(a1, dotted) == expected,
                              f"A1 accepted truth {dotted} must be {expected!r}"))
    a1_decision_04 = next((d for d in a1.get("identity_decisions", [])
                           if isinstance(d, dict) and d.get("QUESTION_ID") == "OQ-M3A1-04"), {})
    results.append(_check(a1_decision_04.get("INVARIANTS", {}).get("DECISION_OWNED_BY_ONE_SUBJECT") == "yes",
                          "A1 OQ-M3A1-04 must freeze DECISION_OWNED_BY_ONE_SUBJECT=yes"))
    a1_decision_05 = next((d for d in a1.get("identity_decisions", [])
                           if isinstance(d, dict) and d.get("QUESTION_ID") == "OQ-M3A1-05"), {})
    results.append(_check(a1_decision_05.get("INVARIANTS", {}).get("FOLLOWUP_REQUIRES_SOURCE_DECISION") == "yes",
                          "A1 OQ-M3A1-05 must freeze FOLLOWUP_REQUIRES_SOURCE_DECISION=yes"))

    # 16. Accepted A2 truth (as read from the checked-out A2 artifact).
    for dotted, expected in A2_TRUTH_MARKERS:
        results.append(_check(_dict_get(a2, dotted) == expected,
                              f"A2 accepted truth {dotted} must be {expected!r}"))
    a2_deferred = [q.get("QUESTION_ID") for q in a2.get("deferred_questions", [])]
    results.append(_check("OQ-M3A1-03" in a2_deferred,
                          "A2 deferred_questions must retain OQ-M3A1-03 (allowlist omission is tolerated)"))
    oq_m3a1_03 = next((q for q in a2.get("deferred_questions", [])
                       if isinstance(q, dict) and q.get("QUESTION_ID") == "OQ-M3A1-03"), {})
    results.append(_check(oq_m3a1_03.get("not_answered_by_a2") is True
                          and oq_m3a1_03.get("DEFERRED_TO") == "M3-A1",
                          "A2 deferred OQ-M3A1-03 must be unanswered and deferred to M3-A1"))
    results.append(_check(a2.get("invariants", {}).get("m3_a2_constraints", {}).get(
                              "AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
                          "A2 must keep AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no"))
    a2_frozen = a2.get("invariants", {}).get("frozen_authority_flags", {})
    results.append(_check(a2_frozen.get("LEASE_OBJECT_SCOPE_EXPANSION") == "denied"
                          and a2_frozen.get("LEASE_OPERATION_SCOPE_EXPANSION") == "denied",
                          "A2 must deny lease scope expansion for object and operation "
                          "(LEASE_SCOPE_EXPANSION=denied)"))
    a2_nf2 = a2.get("nf2_resolution_contract", {})
    results.append(_check(
        isinstance(a2_nf2.get("RESOLVED_DESIGN_RULES"), list) and a2_nf2["RESOLVED_DESIGN_RULES"]
        and a2_nf2.get("NF2_SOURCE_FIXED") == "no",
        "A2 NF2_RESOLUTION_CONTRACT must be PASS (resolved design rules; NF2_SOURCE_FIXED=no)",
    ))

    # 17. A0 map preservation (common base and frozen facts).
    results.append(_check(a0_map.get("base_sha") == M3_A_COMMON_BASE,
                          f"A0 map base_sha must remain {M3_A_COMMON_BASE}"))
    results.append(_check(a0_map.get("invariants", {}).get("frozen_plan_facts", {}).get("ID_IS_AUTHORITY") == "no",
                          "A0 map must still freeze ID_IS_AUTHORITY=no"))
    results.append(_check(
        "SUBJECT_BINDING_ZERO_ONE_MANY" in json.dumps(a0_map, ensure_ascii=False),
        "A0 map must preserve SUBJECT_BINDING_ZERO_ONE_MANY",
    ))

    # 18. Git: input commits exist; HEAD tree carries A1/A2 artifact/guard paths.
    for label, commit in (("a0", A0_COMMIT), ("a1", A1_COMMIT), ("a2", A2_COMMIT)):
        rc, _ = _git(repo_root, ["cat-file", "-e", f"{commit}^{{commit}}"])
        results.append(_check(rc == 0, f"input commit {label} ({commit}) must exist in the object store"))
    for rel in (A1_ART_REL, A1_GUARD_REL, A2_ART_REL, A2_GUARD_REL):
        rc, _ = _git(repo_root, ["cat-file", "-e", f"HEAD:{rel}"])
        results.append(_check(rc == 0, f"checked-out tree must carry {rel} (HEAD:{rel})"))
        results.append(_check((repo_root / rel).is_file(), f"checked-out file missing on disk: {rel}"))

    # 19. Git blob provenance: input commit blob == checked-out blob for A0/A1/A2
    #     artifact and guard paths (identical because of the serial cherry-pick).
    for commit, rels in PROVENANCE_PATHS.items():
        for rel in rels:
            rc, commit_blob = _git(repo_root, ["rev-parse", f"{commit}:{rel}"])
            rc2, head_blob = _git(repo_root, ["rev-parse", f"HEAD:{rel}"])
            results.append(_check(
                rc == 0 and rc2 == 0 and commit_blob and commit_blob == head_blob,
                f"blob provenance: {rel} at {commit} must equal the checked-out blob",
            ))

    # 20. Changed-path check vs the A0 commit: only integration evidence/guards
    #     plus the two reconciliation files; core/adapters strictly forbidden.
    rc, tracked = _git(repo_root, ["diff", "--name-only", A0_COMMIT])
    results.append(_check(rc == 0, "git diff against the A0 commit failed"))
    rc2, untracked = _git(repo_root, ["ls-files", "--others", "--exclude-standard"])
    results.append(_check(rc2 == 0, "git ls-files --others failed"))
    changed = set(tracked.splitlines()) if tracked else set()
    for line in (untracked.splitlines() if untracked else []):
        if "__pycache__" in line or line.endswith(".pyc"):
            continue
        changed.add(line.strip())
    results.append(_check(bool(changed), "no changed paths detected against the A0 commit"))
    results.append(_check(changed <= ALLOWED_CHANGED_PATHS,
                          f"forbidden changes vs A0 commit: {sorted(changed - ALLOWED_CHANGED_PATHS)}"))
    forbidden = [p for p in sorted(changed) if p.startswith(FORBIDDEN_PREFIXES)]
    results.append(_check(not forbidden,
                          f"aota_forge/core or adapters changes are strictly forbidden: {forbidden}"))
    a1_a2_present = sorted(SIBLING_PATHS & changed)
    results.append(_check(bool(a1_a2_present),
                          "A1/A2 sibling evidence paths must be present in the serial tree"))

    # 21. Static scan of aota_forge: no graph/authority/lease/mutation
    #     implementation, no write-operation declarations, read-only ingress kept.
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
                          f"graph/authority/lease/mutation implementation surface found: {impl_hits}"))
    ingress_text = (aota_pkg / "core" / "ingress.py").read_text(encoding="utf-8")
    results.append(_check(READ_ONLY_GATE_MARKER in ingress_text,
                          f"read-only ingress gate marker missing: {READ_ONLY_GATE_MARKER!r}"))

    return results


def summarize(recon: dict, results: list[tuple[bool, str]]) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    return {
        "artifact": "m3-a12-contract-reconciliation",
        "gate": recon.get("gate"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "a1_commit": recon.get("input_commits", {}).get("a1"),
        "a2_commit": recon.get("input_commits", {}).get("a2"),
        "m3_a3_ready": recon.get("a3_handoff", {}).get("readiness_criteria", {}).get("M3_A3_READY"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A1/A2 contract reconciliation guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--artifact", type=Path, help="path to the reconciliation artifact JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    args = parser.parse_args()

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    repo_root = repo_root.resolve()
    artifact_path = args.artifact or repo_root / RECON_REL
    artifact_path = artifact_path.resolve()

    try:
        recon = _read_json(artifact_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        a0_map = _read_json(repo_root / A0_MAP_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A0 input map unreadable/invalid: {exc}")
        return 1
    try:
        a1 = _read_json(repo_root / A1_ART_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A1 artifact unreadable/invalid: {exc}")
        return 1
    try:
        a2 = _read_json(repo_root / A2_ART_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A2 artifact unreadable/invalid: {exc}")
        return 1

    results = run_checks(recon, repo_root, a0_map, a1, a2)
    summary = summarize(recon, results)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

    try:
        display_path = artifact_path.relative_to(repo_root)
    except ValueError:
        display_path = artifact_path
    print(f"artifact: {display_path}")
    print(f"gate: {recon.get('gate')}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
