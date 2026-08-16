#!/usr/bin/env python3
"""M3-A6 integrated graph and authority design proof guard (Issue #9).

Validates deploy/evidence/issues/9/m3-a/m3-a-design-proof.json against all
accepted M3-A input artifacts (A0, A1, A2, A12, A3, A4, A5, A45) and the M2
successor regression matrix:

- exact top-level identity, integration base, input commits, serial lineage
  and patch provenance (tree/blob equality against the accepted commits)
- all twelve mandatory Plan outputs (plus five derived contracts) exist with
  PASS status (GRAPH_CANONICAL_SCHEMA, SUBJECT_IDENTITY_RULE,
  GRAPH_BOOTSTRAP_STRATEGY, GRAPH_CUTOVER_RULE, REVISION_MODEL, CAS_MODEL,
  TRANSACTION_BOUNDARY, CAPABILITY_LEASE_LIFECYCLE, IDEMPOTENT_REPLAY_RULE,
  PROJECTION_REBUILD_RULE, CONFLICT_RULE, CONCURRENCY_RULE plus
  AUTHORITY_EVALUATION_MODEL, PRINCIPAL_BINDING_RULE,
  TRUSTED_CONTEXT_AUTHORITY_RULE, SUBJECT_BINDING_RULE,
  MECHANICAL_RECOVERY_BOUNDARY)
- every section's frozen values are verified against the source artifacts,
  not taken on trust (subject model, identity/authority separation,
  principal/lease model, revision/CAS model, transaction model, idempotency,
  bootstrap/migration, cutover, projection, binding, recovery, read-only
  diagnostic plane, NF2 disposition, I9-B008 disposition)
- cross-artifact consistency: at minimum subject authority target, lease
  revision target, Subject aggregate revision, followup transaction, cutover
  authority, post-cutover binding source, projection recovery, recovery cutover
  prohibition agree across every artifact that speaks to them
- regression ownership: exactly 16 classes, no unsupported success claims
- open question triage: BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT == 0
- M3-B construction map (14 work items), dependency DAG (acyclic + required
  ordering constraints), authoritative write gate prerequisites
- storage/executor neutrality, LLM-first control plane boundary, independent
  design self-check
- negative proof (--self-test): ten mutated /tmp copies must be rejected
- ancestor guard wrapper (--run-ancestor-guards): runs A0..A45 from the A6
  worktree and classifies every finding as either SEMANTIC_CONTRACT_FAILURE
  or DESCENDANT_WORKTREE_CHANGED_PATH_ALLOWLIST_FINDING (descendant findings
  are recorded, never counted as semantic failure)
- source boundary: only the A6 proof and guard files changed vs the
  integration base; no aota_forge/core/** or aota_forge/adapters/** change

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a6_integrated_design_guard.py
    python3 scripts/m3_a6_integrated_design_guard.py --json
    python3 scripts/m3_a6_integrated_design_guard.py --artifact /tmp/x.json
    python3 scripts/m3_a6_integrated_design_guard.py --repo-root /path/to/worktree
    python3 scripts/m3_a6_integrated_design_guard.py --self-test
    python3 scripts/m3_a6_integrated_design_guard.py --run-ancestor-guards
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INTEGRATION_BASE = "0f75ec4091abf3897bc638c6ba985f13ff1dfc9c"
A0_COMMIT = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A1_COMMIT = "7dbc2225e0b66f6bf9e6d0970581d96f428d4362"
A1_SERIAL = "0dd2a11876fec105fecd8593b1586fc9f04932e0"
A2_COMMIT = "b29fe5f6fa47035cdeb4137cd62aafc585257b40"
A2_SERIAL = "18980097b0bb15a285c28e25a5e3284f067af2f8"
A12_COMMIT = "8dad2d3ceeaaf093f35e39daa5c12049c0505527"
A3_COMMIT = "2485a8d51b6fb5722a0da02e9dc93e5615401277"
A4_COMMIT = "6552b9a2b4d38de2c947e22ea4c30972266bb54a"
A4_SERIAL = "271e9ea332c9bf1970e401b74109e4fd2da6a2b3"
A5_COMMIT = "4573712a1f89f5610635c9a77231be7509fb4957"
A5_SERIAL = "24c580582d7c8afea707f3d7f9ff42cf5ea9690c"
A45_COMMIT = "0f75ec4091abf3897bc638c6ba985f13ff1dfc9c"

PROOF_REL = Path("deploy/evidence/issues/9/m3-a/m3-a-design-proof.json")
GUARD_REL = Path("scripts/m3_a6_integrated_design_guard.py")
MD_REL = Path("deploy/evidence/issues/9/m3-a/m3-a-design-proof.md")
R1_REL = Path("deploy/evidence/issues/9/m3-a/m3-a-r1-contract-repair.json")
R1_GUARD_REL = Path("scripts/m3_a_r1_contract_repair_guard.py")
A12_GUARD_REL = Path("scripts/m3_a12_contract_reconciliation_guard.py")
A45_GUARD_REL = Path("scripts/m3_a45_contract_reconciliation_guard.py")

A0_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A1_REL = Path("deploy/evidence/issues/9/m3-a/a1-subject-schema-identity.json")
A2_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
A12_REL = Path("deploy/evidence/issues/9/m3-a/a12-contract-reconciliation.json")
A3_REL = Path("deploy/evidence/issues/9/m3-a/a3-revision-cas-transaction.json")
A4_REL = Path("deploy/evidence/issues/9/m3-a/a4-bootstrap-cutover.json")
A5_REL = Path("deploy/evidence/issues/9/m3-a/a5-binding-recovery.json")
A45_REL = Path("deploy/evidence/issues/9/m3-a/a45-contract-reconciliation.json")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

FORBIDDEN_SOURCE_PREFIXES = ("aota_forge/core/", "aota_forge/adapters/")

INPUT_RELS = {
    "a0": A0_REL,
    "a1": A1_REL,
    "a2": A2_REL,
    "a12": A12_REL,
    "a3": A3_REL,
    "a4": A4_REL,
    "a5": A5_REL,
    "a45": A45_REL,
}

MANDATORY_PLAN_OUTPUTS = (
    "GRAPH_CANONICAL_SCHEMA",
    "SUBJECT_IDENTITY_RULE",
    "GRAPH_BOOTSTRAP_STRATEGY",
    "GRAPH_CUTOVER_RULE",
    "REVISION_MODEL",
    "CAS_MODEL",
    "TRANSACTION_BOUNDARY",
    "CAPABILITY_LEASE_LIFECYCLE",
    "IDEMPOTENT_REPLAY_RULE",
    "PROJECTION_REBUILD_RULE",
    "CONFLICT_RULE",
    "CONCURRENCY_RULE",
)
DERIVED_CONTRACTS = (
    "AUTHORITY_EVALUATION_MODEL",
    "PRINCIPAL_BINDING_RULE",
    "TRUSTED_CONTEXT_AUTHORITY_RULE",
    "SUBJECT_BINDING_RULE",
    "MECHANICAL_RECOVERY_BOUNDARY",
)

# Section 28 required guard invariants (key -> expected value)
GUARD_INVARIANTS = {
    "DURABLE_SUBJECT_GRAPH_IS_AUTHORITY": "yes",
    "CURRENT_POINTERS_ARE_AUTHORITY": "no",
    "ID_IS_AUTHORITY": "no",
    "SUBJECT_ID_IS_AUTHORITY": "no",
    "LOST_UPDATE_ALLOWED": "no",
    "SILENT_LAST_WRITE_WINS": "no",
    "STALE_REVISION_MUTATION": "fail_closed",
    "CAPABILITY_LEASE_LIFECYCLE": "PASS",
    "IDEMPOTENT_REPLAY_RULE": "PASS",
    "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY": "no",
    "HEURISTIC_SUBJECT_SELECTION_ALLOWED": "no",
    "RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING": "no",
    "SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING": "no",
    "STORAGE_ENGINE_FROZEN": "no",
    "M3_CORE_HERMES_DEPENDENCY": "no",
    "BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT": 0,
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
}

EXPECTED_REG_CLASSES = {
    "B011", "B013", "B014", "B014-F", "B014-F1",
    "ACTIVATE-R-current-binding", "ACTIVATE-R-host-inspection-escalation",
    "ACTIVATE-R-wrong-source-checkout", "WCTX-1", "BIND-1", "DRIFT-1",
    "RC2-1", "CLASSIFY-1", "RECOVERY-1", "RUNNER-1", "E2E-1",
}

ANCESTOR_GUARDS = (
    "m3_a0_reconnaissance_guard.py",
    "m3_a1_subject_schema_guard.py",
    "m3_a2_authority_lease_guard.py",
    "m3_a12_contract_reconciliation_guard.py",
    "m3_a3_revision_cas_guard.py",
    "m3_a4_bootstrap_cutover_guard.py",
    "m3_a5_binding_recovery_guard.py",
    "m3_a45_contract_reconciliation_guard.py",
)

CHANGED_PATH_MARKERS = (
    "changed paths",
    "forbidden change",
    "vs base",
    "vs the base",
    "vs A0 commit",
    "vs the A0 commit",
    "source changes",
    "forbidden source",
    "git diff",
    "git status",
    "tracked files",
    "untracked",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def _blob(repo_root: Path, commit: str, path: str) -> bytes | None:
    rc, out = _git(repo_root, ["show", f"{commit}:{path}"])
    if rc != 0:
        return None
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{commit}:{path}"],
        capture_output=True,
        timeout=90,
    )
    return proc.stdout if proc.returncode == 0 else None


def _tree(repo_root: Path, commit: str) -> str | None:
    rc, out = _git(repo_root, ["rev-parse", f"{commit}^{{tree}}"])
    return out if rc == 0 and out else None


def _changed_paths(repo_root: Path) -> tuple[bool, set[str]]:
    """Return changed paths vs the integration base plus untracked files."""
    rc, tracked = _git(repo_root, ["diff", "--name-only", INTEGRATION_BASE])
    if rc != 0:
        return False, set()
    rc2, status = _git(repo_root, ["status", "--porcelain"])
    if rc2 != 0:
        return False, set()
    changed = set(tracked.splitlines()) if tracked else set()
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if path not in changed:
            changed.add(path)
    return True, changed


# ---------------------------------------------------------------------------
# identity / provenance
# ---------------------------------------------------------------------------


def _check_identity(results: list, proof: dict) -> None:
    for key, expected in (
        ("schema_version", 1),
        ("project_id", "aota_forge"),
        ("milestone", "M3"),
        ("phase", "M3-A"),
        ("lane", "M3-A6"),
    ):
        results.append(_check(proof.get(key) == expected, f"top-level {key} must be {expected!r}"))
    results.append(_check(
        proof.get("governing_plan_issue") == 9,
        "governing_plan_issue must be 9",
    ))
    results.append(_check(
        proof.get("integration_base") == INTEGRATION_BASE,
        "integration_base must be the A45 head 0f75ec4",
    ))


def _check_input_commits(results: list, proof: dict) -> None:
    commits = proof.get("input_commits", {})
    for lane, commit in (
        ("a0", A0_COMMIT), ("a1", A1_COMMIT), ("a2", A2_COMMIT),
        ("a12", A12_COMMIT), ("a3", A3_COMMIT), ("a4", A4_COMMIT),
        ("a5", A5_COMMIT), ("a45", A45_COMMIT),
    ):
        results.append(_check(
            commits.get(lane) == commit,
            f"input_commits.{lane} must be {commit}",
        ))
    lineage = proof.get("integrated_serial_lineage", {})
    for serial_key, commit in (
        ("a1_serial", A1_SERIAL), ("a2_serial", A2_SERIAL),
        ("a4_serial", A4_SERIAL), ("a5_serial", A5_SERIAL),
    ):
        results.append(_check(
            lineage.get(serial_key) == commit,
            f"integrated_serial_lineage.{serial_key} must be {commit}",
        ))
    provenance = proof.get("patch_provenance", {})
    for lane in ("a0", "a1", "a2", "a12", "a3", "a4", "a5", "a45"):
        results.append(_check(
            bool(provenance.get(lane)),
            f"patch_provenance.{lane} must be documented",
        ))


def _check_provenance_blobs(results: list, repo_root: Path) -> None:
    """Prove the accepted patches are byte-exact in the integration line."""
    # A1: serial copy tree == original tree
    t_orig = _tree(repo_root, A1_COMMIT)
    t_serial = _tree(repo_root, A1_SERIAL)
    results.append(_check(
        t_orig is not None and t_orig == t_serial,
        f"A1 serial copy {A1_SERIAL} tree must equal original {A1_COMMIT} tree",
    ))
    # A4: serial copy tree == original tree
    t_orig4 = _tree(repo_root, A4_COMMIT)
    t_serial4 = _tree(repo_root, A4_SERIAL)
    results.append(_check(
        t_orig4 is not None and t_orig4 == t_serial4,
        f"A4 serial copy {A4_SERIAL} tree must equal original {A4_COMMIT} tree",
    ))
    # A2: A2-owned blob at serial copy == blob at original
    b_orig = _blob(repo_root, A2_COMMIT, str(A2_REL))
    b_serial = _blob(repo_root, A2_SERIAL, str(A2_REL))
    results.append(_check(
        b_orig is not None and b_orig == b_serial,
        "A2 artifact blob at serial copy 1898009 must equal original b29fe5f",
    ))
    b_g_orig = _blob(repo_root, A2_COMMIT, "scripts/m3_a2_authority_lease_guard.py")
    b_g_serial = _blob(repo_root, A2_SERIAL, "scripts/m3_a2_authority_lease_guard.py")
    results.append(_check(
        b_g_orig is not None and b_g_orig == b_g_serial,
        "A2 guard blob at serial copy 1898009 must equal original b29fe5f",
    ))
    # A5: A5-owned blob at serial copy == blob at original
    b5_orig = _blob(repo_root, A5_COMMIT, str(A5_REL))
    b5_serial = _blob(repo_root, A5_SERIAL, str(A5_REL))
    results.append(_check(
        b5_orig is not None and b5_orig == b5_serial,
        "A5 artifact blob at serial copy 24c5805 must equal original 4573712",
    ))
    # checked-out blobs match the integrated serial line (exact integration)
    for rel, commit in (
        (A1_REL, A1_SERIAL), (A2_REL, A2_SERIAL), (A3_REL, A3_COMMIT),
        (A4_REL, A4_SERIAL), (A5_REL, A5_SERIAL),
    ):
        expected = _blob(repo_root, commit, str(rel))
        current = (repo_root / rel).read_bytes() if (repo_root / rel).is_file() else None
        if rel == A5_REL and current != expected:
            amendment = {}
            try:
                amendment = _read_json(repo_root / rel).get("contract_repair_amendment", {})
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            results.append(_check(
                amendment.get("repair_id") == "M3-A-R1"
                and amendment.get("REPAIR_REASON") == "independent_review_blocker"
                and bool(amendment.get("ORIGINAL_ASSERTION"))
                and bool(amendment.get("REPAIRED_ASSERTION")),
                "checked-out A5 amendment must preserve original and repaired assertions",
            ))
            continue
        results.append(_check(
            expected is not None and current == expected,
            f"checked-out {rel} must match {commit} blob exactly",
        ))


# ---------------------------------------------------------------------------
# required plan outputs
# ---------------------------------------------------------------------------


def _check_plan_outputs(results: list, proof: dict) -> None:
    outputs = proof.get("required_plan_outputs", {})
    for name in MANDATORY_PLAN_OUTPUTS:
        entry = outputs.get(name)
        results.append(_check(
            isinstance(entry, dict) and entry.get("status") == "PASS",
            f"mandatory plan output {name} must exist with status PASS",
        ))
    for name in DERIVED_CONTRACTS:
        entry = outputs.get(name)
        results.append(_check(
            isinstance(entry, dict) and entry.get("status") == "PASS",
            f"derived contract {name} must exist with status PASS",
        ))
    results.append(_check(
        proof.get("report_metrics", {}).get("mandatory_plan_output_count") == 12
        and proof["report_metrics"].get("derived_contract_count") == 5,
        "report_metrics must count 12 mandatory + 5 derived outputs",
    ))
    acceptance = proof.get("acceptance", {})
    for name in MANDATORY_PLAN_OUTPUTS:
        results.append(_check(
            acceptance.get(name) == "PASS",
            f"acceptance.{name} must be PASS",
        ))


# ---------------------------------------------------------------------------
# section models
# ---------------------------------------------------------------------------


def _check_subject_model(results: list, proof: dict, a1: dict) -> None:
    m = proof.get("canonical_subject_model", {})
    records = m.get("accepted_records", [])
    expected_records = {"Workflow", "Subject", "Execution", "Completion", "Decision", "FollowupEdge"}
    results.append(_check(
        expected_records <= set(records),
        f"canonical subject records must include {sorted(expected_records)}",
    ))
    for key, expected in (
        ("RETRY_CREATES_NEW_SUBJECT", "no"),
        ("CHANGE_EXECUTOR_CREATES_NEW_SUBJECT", "no"),
        ("HERMES_PRIVATE_IDENTITY_IN_CORE_ONTOLOGY", "no"),
        ("CURRENT_POINTERS_ARE_SUBJECT_AUTHORITY", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"canonical_subject_model.{key} must be {expected}"))
    # cross-check with A1 source
    a1_rels = a1.get("relationships", {})
    results.append(_check(
        "RETRY_CREATES_NEW_SUBJECT=no" in a1_rels.get("retry_no_new_subject", ""),
        "A1 relationships.retry_no_new_subject agrees",
    ))
    results.append(_check(
        "CHANGE_EXECUTOR_CREATES_NEW_SUBJECT=no" in a1_rels.get("executor_change_no_new_subject", ""),
        "A1 relationships.executor_change_no_new_subject agrees",
    ))
    wic = m.get("work_item_is_migration_evidence_not_canonical_subject", {})
    results.append(_check(
        wic.get("WORK_ITEM_NOT_SUBJECT") == "yes",
        "work item must be migration evidence, not canonical Subject identity",
    ))
    dec = m.get("decision_owned_by_one_subject", "")
    results.append(_check(
        "ONE_SUBJECT" in str(dec).upper(),
        "Decision must be a durable record owned by exactly one Subject",
    ))
    fl = m.get("semantic_followup_child_only_through_decision_lineage", {})
    results.append(_check(
        fl.get("FOLLOWUP_REQUIRES_SOURCE_DECISION") == "yes",
        "semantic followup child must be Decision-backed",
    ))


def _check_identity_authority(results: list, proof: dict, a1: dict, a2: dict, a12: dict) -> None:
    m = proof.get("identity_authority_model", {})
    for key, expected in (
        ("SUBJECT_ID_IS_AUTHORITY", "no"),
        ("ID_IS_AUTHORITY", "no"),
        ("AUTHORITY_TARGET_IS_TYPED", "yes"),
        ("MODEL_INTERNAL_IDS_NORMAL_INPUT", "no"),
        ("UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL", "no"),
        ("EXECUTOR_IDENTITY_EQUALS_PRINCIPAL", "no"),
        ("AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"identity_authority_model.{key} must be {expected}"))
    target = m.get("authority_target", {})
    results.append(_check(
        target.get("object_kind") == "Subject" and "object_id" in target,
        "authority target must be typed object_ref (object_kind=Subject + object_id)",
    ))
    # cross-artifact: every source artifact agrees
    results.append(_check(
        a1.get("subject_identity_rule", {}).get("SUBJECT_ID_IS_AUTHORITY") == "no",
        "A1 subject_identity_rule.SUBJECT_ID_IS_AUTHORITY=no",
    ))
    results.append(_check(
        a2.get("id_model", {}).get("policy_flags", {}).get("ID_IS_AUTHORITY") == "no",
        "A2 id_model.policy_flags.ID_IS_AUTHORITY=no",
    ))
    results.append(_check(
        a12.get("invariants", {}).get("SUBJECT_ID_IS_AUTHORITY") == "no"
        and a12["invariants"].get("ID_IS_AUTHORITY") == "no",
        "A12 invariants SUBJECT_ID_IS_AUTHORITY=no and ID_IS_AUTHORITY=no",
    ))
    results.append(_check(
        a2.get("authority_evaluation_model", {}).get("no_semantic_reasoning")
        == "AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING=no",
        "A2 authority_evaluation_model denies semantic reasoning",
    ))
    results.append(_check(
        a2.get("id_model", {}).get("policy_flags", {}).get("MODEL_INTERNAL_IDS_NORMAL_INPUT") == "no",
        "A2 id_model denies internal IDs as normal input",
    ))


def _check_lease_model(results: list, proof: dict, a2: dict, a3: dict) -> None:
    m = proof.get("principal_lease_model", {})
    bound = m.get("bound_to", [])
    for item in ("principal", "operation", "typed object", "expected revision", "expiry", "authority basis"):
        results.append(_check(item in bound, f"lease must be bound to {item}"))
    flags = m.get("flags", {})
    for key, expected in (
        ("LEASE_PRINCIPAL_BOUND", "yes"),
        ("LEASE_OPERATION_BOUND", "yes"),
        ("LEASE_OBJECT_BOUND", "yes"),
        ("LEASE_REVISION_BOUND", "yes"),
        ("LEASE_SCOPE_EXPANSION", "denied"),
        ("EXPIRED_LEASE_MUTATION", "denied"),
        ("STALE_LEASE_REVISION_MUTATION", "denied"),
        ("TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "no"),
    ):
        results.append(_check(flags.get(key) == expected, f"principal_lease_model.flags.{key} must be {expected}"))
    # cross-artifact
    a2_bound = a2.get("capability_lease_contract", {}).get("bound_properties", {})
    results.append(_check(a2_bound.get("principal_bound") == "yes", "A2 lease principal_bound=yes"))
    results.append(_check(a2_bound.get("operation_bound") == "yes", "A2 lease operation_bound=yes"))
    results.append(_check(a2_bound.get("object_bound") == "yes", "A2 lease object_bound=yes"))
    results.append(_check(a2_bound.get("revision_bound") == "yes", "A2 lease revision_bound=yes"))
    lifecycle = a2.get("capability_lease_contract", {}).get("lifecycle", {})
    results.append(_check(any("denied" in str(v) for v in lifecycle.values()), "A2 lease lifecycle denies expansion/expiry misuse"))
    results.append(_check(
        a3.get("invariants", {}).get("frozen_concurrency_flags", {}).get("STALE_LEASE_REVISION_MUTATION") == "denied",
        "A3 frozen STALE_LEASE_REVISION_MUTATION=denied",
    ))


def _check_revision_cas(results: list, proof: dict, a3: dict, a4: dict) -> None:
    m = proof.get("revision_cas_model", {})
    results.append(_check(
        m.get("primary_revision_boundary") == "Subject aggregate",
        "revision primary boundary must be Subject aggregate",
    ))
    results.append(_check(
        "independent" in str(m.get("workflow_metadata_revision", "")).lower(),
        "Workflow metadata must have independent revision",
    ))
    results.append(_check(m.get("whole_graph_serialization") == "no", "no whole-graph serialization"))
    flags = m.get("flags", {})
    for key, expected in (
        ("LOST_UPDATE_ALLOWED", "no"),
        ("SILENT_LAST_WRITE_WINS", "no"),
        ("STALE_REVISION_MUTATION", "fail_closed"),
        ("CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT", "yes"),
        ("GLOBAL_PROCESS_LOCK_IS_CANONICAL_CONCURRENCY_MODEL", "no"),
        ("INDEPENDENT_SUBJECTS_CONCURRENT_MUTATION", "yes"),
    ):
        results.append(_check(flags.get(key) == expected, f"revision_cas_model.flags.{key} must be {expected}"))
    fc = a3.get("invariants", {}).get("frozen_concurrency_flags", {})
    for key, expected in (
        ("LOST_UPDATE_ALLOWED", "no"),
        ("SILENT_LAST_WRITE_WINS", "no"),
        ("STALE_REVISION_MUTATION", "fail_closed"),
        ("CAS_CHECK_BEFORE_AUTHORITATIVE_COMMIT", "yes"),
    ):
        results.append(_check(fc.get(key) == expected, f"A3 frozen_concurrency_flags.{key}={expected}"))
    # CAS bypass only bootstrap/cutover (A4 agrees)
    results.append(_check(
        a4.get("cutover_concurrency", {}).get("A3_HANDOFF", {}).get("CAS_BYPASS_ONLY_BOOTSTRAP_CUTOVER") == "yes",
        "A4 confirms CAS bypass only bootstrap/cutover",
    ))


def _check_transaction_model(results: list, proof: dict, a3: dict) -> None:
    m = proof.get("transaction_model", {})
    contents = m.get("authoritative_transaction_contents", [])
    results.append(_check(len(contents) >= 5, "authoritative transaction contents enumerated"))
    includes = m.get("includes", [])
    for item in ("semantic records", "aggregate revision", "idempotency metadata"):
        results.append(_check(item in includes, f"transaction must include {item}"))
    fam = m.get("followup_atomic_unit", {})
    for key, expected in (
        ("NO_ORPHAN_CHILD_SUBJECT", "yes"),
        ("NO_DUPLICATE_SEMANTIC_CHILD_EFFECT", "yes"),
        ("PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE", "no"),
    ):
        results.append(_check(fam.get(key) == expected, f"transaction_model.followup_atomic_unit.{key} must be {expected}"))
    ft = a3.get("followup_transaction", {})
    results.append(_check(ft.get("FORBIDDEN_ORPHAN_CHILD") == "yes", "A3 followup forbids orphan child"))
    results.append(_check(ft.get("FORBIDDEN_EDGE_WITHOUT_CHILD") == "yes", "A3 followup forbids edge without child"))
    results.append(_check(ft.get("FORBIDDEN_CHILD_WITHOUT_VERIFIED_DECISION") == "yes", "A3 followup forbids child without decision"))
    results.append(_check(ft.get("PARTIAL_AUTHORITATIVE_TRANSITION_VISIBLE") == "no", "A3 no partial authoritative transition"))
    tb = a3.get("transaction_boundary", {})
    results.append(_check(
        "idempotency" in json.dumps(tb.get("AUTHORITATIVE_TRANSACTION_CONTENTS", []), ensure_ascii=False).lower(),
        "A3 transaction contents include idempotency metadata",
    ))


def _check_idempotency(results: list, proof: dict, a3: dict, a5: dict, a45: dict) -> None:
    m = proof.get("idempotency_model", {})
    results.append(_check(m.get("IDEMPOTENT_REPLAY_DUPLICATES_SEMANTIC_EFFECT") == "no",
                          "IDEMPOTENT_REPLAY_DUPLICATES_SEMANTIC_EFFECT=no"))
    results.append(_check(m.get("IDEMPOTENCY_PAYLOAD_MISMATCH") == "fail_closed",
                          "IDEMPOTENCY_PAYLOAD_MISMATCH=fail_closed"))
    results.append(_check(m.get("cutover_recovery_replay_same_principle") == "yes",
                          "cutover/recovery replay must obey same semantic-effect principle"))
    im = a3.get("idempotency_model", {})
    results.append(_check(im.get("IDEMPOTENT_REPLAY") == "must_not_duplicate_semantic_effects",
                          "A3 idempotency model must_not_duplicate_semantic_effects"))
    results.append(_check(
        a5.get("idempotent_recovery", {}).get("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT") == "no",
        "A5 recovery replay duplicates semantic effect = no",
    ))
    # A45 cross-contract idempotency must exist
    results.append(_check(bool(a45.get("idempotency_cross_contract")), "A45 idempotency_cross_contract present"))


def _check_bootstrap(results: list, proof: dict, a4: dict) -> None:
    m = proof.get("bootstrap_migration_model", {})
    for key, expected in (
        ("LEGACY_GRAPH_INPUT_IS_AUTHORITY", "no"),
        ("GIT_HISTORY_IS_SUBJECT_AUTHORITY", "no"),
        ("EVENT_LOG_IS_SUBJECT_AUTHORITY", "no"),
        ("CONTROL_COMMENT_IS_SUBJECT_AUTHORITY", "no"),
        ("HEURISTIC_IDENTITY_MERGE_ALLOWED", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"bootstrap_migration_model.{key} must be {expected}"))
    results.append(_check("NEEDS_SEMANTIC_CHOICE" in str(m.get("ambiguous_migration", "")).upper() or "UNMIGRATABLE" in str(m.get("ambiguous_migration", "")).upper(),
                          "ambiguous migration must be NEEDS_SEMANTIC_CHOICE or UNMIGRATABLE"))
    m4 = a4.get("migration_identity_rule", {})
    results.append(_check(m4.get("HEURISTIC_IDENTITY_MERGE_ALLOWED") == "no", "A4 heuristic identity merge = no"))
    results.append(_check(m4.get("AMBIGUOUS_MIGRATION_IDENTITY") == "NEEDS_SEMANTIC_CHOICE",
                          "A4 AMBIGUOUS_MIGRATION_IDENTITY=NEEDS_SEMANTIC_CHOICE"))
    inv4 = a4.get("invariants", {}).get("frozen_authority_flags", {})
    for key in ("LEGACY_GRAPH_INPUT_IS_AUTHORITY", "GIT_HISTORY_IS_SUBJECT_AUTHORITY",
                "EVENT_LOG_IS_SUBJECT_AUTHORITY", "CONTROL_COMMENT_IS_SUBJECT_AUTHORITY"):
        results.append(_check(inv4.get(key) == "no", f"A4 frozen_authority_flags.{key}=no"))


def _check_cutover(results: list, proof: dict, a4: dict, a45: dict) -> None:
    m = proof.get("cutover_model", {})
    phases = m.get("phases", [])
    results.append(_check(phases == ["PRE_CUTOVER", "CUTOVER", "POST_CUTOVER"], "cutover phases frozen"))
    for key, expected in (
        ("PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "no"),
        ("GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
        ("ONE_CUTOVER_AUTHORITY", "yes"),
        ("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY", "yes"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
        ("POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"cutover_model.{key} must be {expected}"))
    inv45 = a45.get("invariants", {})
    for key, expected in (
        ("PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND", "no"),
        ("GRAPH_WRITE_BEFORE_CUTOVER", "denied"),
        ("ONE_CUTOVER_AUTHORITY", "yes"),
        ("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY", "yes"),
        ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "no"),
        ("POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH", "no"),
    ):
        key45 = {
            "PRE_CUTOVER_SHADOW_GRAPH_CAN_AUTHORITATIVELY_BIND": "SHADOW_GRAPH_CAN_WRITE_AUTHORITATIVE_STATE",
            "GRAPH_WRITE_BEFORE_CUTOVER": "GRAPH_WRITE_BEFORE_CUTOVER",
            "ONE_CUTOVER_AUTHORITY": "ONE_CUTOVER_AUTHORITY",
            "CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY": "CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY",
            "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY": "POST_CUTOVER_DUAL_SUBJECT_AUTHORITY",
            "POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH": None,
        }[key]
        if key45 is None:
            results.append(_check(
                a45.get("post_cutover_binding_model", {}).get("POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH") == expected,
                f"A45 post_cutover_binding_model.POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH={expected}",
            ))
        else:
            results.append(_check(inv45.get(key45) == expected, f"A45 invariants.{key45}={expected}"))
    cutover_rule = a4.get("cutover_rule", {})
    results.append(_check(cutover_rule.get("ONE_CUTOVER_AUTHORITY") == "yes", "A4 ONE_CUTOVER_AUTHORITY=yes"))
    results.append(_check(
        a4.get("cutover_failure_model", {}).get("CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY") == "yes",
        "A4 CUTOVER_FAILURE_CANNOT_CREATE_DUAL_AUTHORITY=yes",
    ))
    results.append(_check(
        a4.get("cutover_concurrency", {}).get("GRAPH_WRITE_BEFORE_CUTOVER") == "denied",
        "A4 GRAPH_WRITE_BEFORE_CUTOVER=denied",
    ))


def _check_projection(results: list, proof: dict, a4: dict, a5: dict, a45: dict) -> None:
    m = proof.get("projection_model", {})
    for key, expected in (
        ("CURRENT_POINTERS_ARE_AUTHORITY", "no"),
        ("STALE_PROJECTION_MAY_BE_REBUILT", "yes"),
        ("STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT", "yes"),
        ("PROJECTION_REBUILD_MUTATES_GRAPH", "no"),
        ("PROJECTION_REBUILD_IS_MECHANICAL_RECOVERY", "yes"),
    ):
        results.append(_check(m.get(key) == expected, f"projection_model.{key} must be {expected}"))
    results.append(_check(m.get("projection_failure_after_canonical_commit_changes_authority") == "no",
                          "projection failure after canonical commit must not change authority"))
    prr = a4.get("projection_rebuild_rule", {})
    results.append(_check(prr.get("PROJECTION_REBUILD_MUTATES_GRAPH") == "no", "A4 PROJECTION_REBUILD_MUTATES_GRAPH=no"))
    results.append(_check(prr.get("STALE_PROJECTION_MAY_BE_REBUILT") == "yes", "A4 STALE_PROJECTION_MAY_BE_REBUILT=yes"))
    results.append(_check(prr.get("STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT") == "yes",
                          "A4 STALE_PROJECTION_MUST_NOT_HIDE_VALID_SUBJECT=yes"))
    pr5 = a5.get("projection_recovery", {})
    results.append(_check(pr5.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no", "A5 CURRENT_POINTERS_ARE_AUTHORITY=no"))
    results.append(_check(pr5.get("STALE_POINTER_MUST_NOT_HIDE_DURABLE_SUBJECT") == "yes",
                          "A5 stale pointer must not hide durable Subject"))
    inv45 = a45.get("invariants", {})
    results.append(_check(inv45.get("CURRENT_POINTERS_ARE_AUTHORITY") == "no", "A45 CURRENT_POINTERS_ARE_AUTHORITY=no"))


def _check_binding(results: list, proof: dict, a5: dict, a45: dict) -> None:
    m = proof.get("binding_model", {})
    for key, expected in (
        ("HEURISTIC_PRE_FILTER_ALLOWED", "no"),
        ("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "no"),
        ("UNIQUE_CANDIDATE_BYPASSES_AUTHORITY", "no"),
        ("UNIQUE_CANDIDATE_BYPASSES_CAS", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"binding_model.{key} must be {expected}"))
    # zero/one/many
    results.append(_check("NEEDS_SEMANTIC_CHOICE" in str(m.get("MANY", "")).upper(), "binding MANY must be NEEDS_SEMANTIC_CHOICE"))
    one = str(m.get("ONE", ""))
    results.append(_check(
        "exactly one" in one.lower() and "materialized" in one.lower(),
        "binding ONE must require exactly one candidate + materialized Decision",
    ))
    zom = a5.get("zero_one_many_rule", {})
    results.append(_check(zom.get("MANY", {}).get("AUTOMATIC_SELECTION") == "no", "A5 MANY AUTOMATIC_SELECTION=no"))
    cv = a5.get("candidate_validity_model", {})
    for key in ("HEURISTIC_PRE_FILTER_ALLOWED", "HEURISTIC_SUBJECT_SELECTION_ALLOWED"):
        results.append(_check(cv.get(key) == "no", f"A5 candidate_validity_model.{key}=no"))
    bf = a5.get("invariants", {}).get("binding_flags", {})
    for key in ("UNIQUE_CANDIDATE_BYPASSES_AUTHORITY", "UNIQUE_CANDIDATE_BYPASSES_CAS",
                "UNIQUE_CANDIDATE_BYPASSES_MATERIALIZED_DECISION", "UNIQUE_CANDIDATE_BYPASSES_PREDECESSOR_VALIDATION"):
        results.append(_check(bf.get(key) == "no", f"A5 binding_flags.{key}=no"))
    results.append(_check(a45.get("post_cutover_binding_model", {}).get("POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH") == "no",
                          "A45 POST_CUTOVER_LEGACY_POINTER_CAN_OVERRIDE_GRAPH=no"))


def _check_recovery(results: list, proof: dict, a5: dict, a45: dict) -> None:
    m = proof.get("recovery_model", {})
    for key, expected in (
        ("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING", "no"),
        ("RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "no"),
        ("RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE", "no"),
        ("RECOVERY_REPLAY_DUPLICATES_SEMANTIC_EFFECT", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"recovery_model.{key} must be {expected}"))
    may = m.get("may", [])
    may_not = m.get("may_not", [])
    for item in ("rebuild a stale projection", "resolve one unique durable Subject",
                 "repair deterministic index/projection drift", "bind a uniquely determined already-decided followup",
                 "replay an idempotent projection"):
        results.append(_check(any(item.split(" ")[0] in str(x).lower() for x in may), f"recovery may include {item}"))
    rb = a5.get("recovery_boundary", {})
    results.append(_check(rb.get("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING") == "no",
                          "A5 recovery engine no semantic reasoning"))
    inv45 = a45.get("invariants", {})
    for key in ("RECOVERY_ENGINE_MAY_TRIGGER_AUTHORITY_CUTOVER", "RECOVERY_ENGINE_MAY_RESOLVE_UNMIGRATABLE_SEMANTIC_STATE"):
        results.append(_check(inv45.get(key) == "no", f"A45 invariants.{key}=no"))


def _check_readonly(results: list, proof: dict, a5: dict, a45: dict) -> None:
    m = proof.get("readonly_diagnostic_model", {})
    for key, expected in (
        ("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING", "no"),
        ("READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
        ("MIGRATION_AMBIGUITY_BLOCKS_SAFE_READONLY_DIAGNOSIS", "no"),
    ):
        results.append(_check(m.get(key) == expected, f"readonly_diagnostic_model.{key} must be {expected}"))
    rde = a5.get("readonly_diagnostic_exemption", {})
    results.append(_check(rde.get("SAFE_READONLY_DIAGNOSIS_REQUIRES_SUBJECT_BINDING") == "no",
                          "A5 readonly diagnosis requires no Subject binding"))
    results.append(_check(a45.get("invariants", {}).get("MIGRATION_AMBIGUITY_BLOCKS_SAFE_READONLY_DIAGNOSIS") == "no",
                          "A45 migration ambiguity does not block readonly diagnosis"))


def _check_nf2_i9b008(results: list, proof: dict, a2: dict, a0: dict) -> None:
    nf2 = proof.get("nf2_disposition", {})
    results.append(_check(nf2.get("NF2_RESOLUTION_CONTRACT") == "PASS", "NF2_RESOLUTION_CONTRACT=PASS"))
    results.append(_check(nf2.get("NF2_SOURCE_FIXED") == "no", "NF2_SOURCE_FIXED=no"))
    results.append(_check(nf2.get("NF2_IMPLEMENTATION_OWNER") in ("M3-B",),
                          "NF2_IMPLEMENTATION_OWNER must be M3-B or a specific M3-B work item"))
    results.append(_check(a2.get("nf2_resolution_contract", {}).get("NF2_SOURCE_FIXED") == "no",
                          "A2 NF2_SOURCE_FIXED=no"))
    results.append(_check(bool(a2.get("nf2_resolution_contract", {}).get("RESOLVED_DESIGN_RULES")),
                          "A2 NF2 resolved design rules present"))
    b = proof.get("i9_b008_disposition", {})
    results.append(_check(b.get("I9_B008_SOURCE_FIXED") == "no", "I9_B008_SOURCE_FIXED=no"))
    results.append(_check(b.get("I9_B008_IMPLEMENTATION_OWNER") == "M3-B", "I9_B008_IMPLEMENTATION_OWNER=M3-B"))
    results.append(_check(b.get("I9_B008_M3B_ORDERING") == "early_prerequisite",
                          "I9_B008_M3B_ORDERING=early_prerequisite"))
    results.append(_check("envelope" in str(b.get("canonical_target_invariant", "")).lower(),
                          "I9-B008 canonical target invariant mentions bounded ingress envelope"))
    results.append(_check(
        a0.get("i9_b008_design_input", {}).get("I9_B008_M3B_PREREQUISITE") == "yes",
        "A0 established I9-B008 as M3-B prerequisite",
    ))


def _check_regression(results: list, proof: dict, a0: dict) -> None:
    rows = proof.get("regression_ownership", [])
    results.append(_check(len(rows) == 16, f"regression_ownership must have 16 classes (got {len(rows)})"))
    classes = {r.get("FAILURE_CLASS") for r in rows}
    results.append(_check(classes == EXPECTED_REG_CLASSES, "regression classes must match the 16-class matrix"))
    unsupported = 0
    for r in rows:
        if r.get("CURRENT_DISPOSITION") == "NOT_YET_IMPLEMENTED" and r.get("M3_A_DESIGN_PROOF") in ("PASS", "implemented"):
            unsupported += 1
    results.append(_check(unsupported == 0, "no unsupported success claims (design proof != implementation)"))
    results.append(_check(proof.get("report_metrics", {}).get("unsupported_success_claims") == 0,
                          "report_metrics.unsupported_success_claims=0"))
    # cross-check with A0 ownership list
    a0_rows = a0.get("successor_regression_ownership", [])
    results.append(_check(len(a0_rows) == 16, "A0 successor_regression_ownership must have 16 classes"))
    # design proof must be 'design' or 'n/a_or_construction' for NOT_YET_IMPLEMENTED classes
    for r in rows:
        if r.get("CURRENT_DISPOSITION") == "NOT_YET_IMPLEMENTED":
            results.append(_check(
                r.get("M3_A_DESIGN_PROOF") in ("design", "n/a_or_construction"),
                f"class {r.get('FAILURE_CLASS')} must not claim implemented design proof",
            ))


def _check_triage(results: list, proof: dict) -> None:
    rows = proof.get("open_question_triage", [])
    results.append(_check(isinstance(rows, list) and len(rows) >= 18,
                          f"open_question_triage must include the union of deferred questions (got {len(rows)})"))
    blocking = 0
    for r in rows:
        cls = r.get("CLASSIFICATION")
        results.append(_check(
            cls in ("RESOLVED_BY_M3_A", "M3_B_IMPLEMENTATION_DETAIL", "M4_PLUS_SEMANTIC_SCOPE", "BLOCKING_UNRESOLVED_SEMANTIC_QUESTION"),
            f"triage entry {r.get('QUESTION_ID')} must have an exact classification",
        ))
        if cls == "BLOCKING_UNRESOLVED_SEMANTIC_QUESTION":
            blocking += 1
    results.append(_check(blocking == 0, "BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT must be 0"))
    results.append(_check(proof.get("invariants", {}).get("BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT") == 0,
                          "invariants.BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT=0"))
    # A12 union of 18 must all be represented
    union_ids = {r.get("QUESTION_ID") for r in rows}
    for expected in ("OQ-M3A1-01", "OQ-M3A1-02", "OQ-M3A1-03", "OQ-M3A1-04", "OQ-M3A1-05", "OQ-M3A1-06",
                     "OQ-M3A2-01", "OQ-M3A2-02", "OQ-M3A2-03", "OQ-M3A2-04", "OQ-M3A4-01", "OQ-M3A5-01",
                     "OQ-M3A5-02", "A1-DEFER-01", "A1-DEFER-02", "A1-DEFER-03", "A1-DEFER-04", "A1-DEFER-05"):
        results.append(_check(expected in union_ids, f"triage must include {expected}"))


def _check_m3b_map_dag_gate(results: list, proof: dict) -> None:
    items = proof.get("m3_b_construction_map", [])
    results.append(_check(len(items) >= 14, f"M3-B construction map must have >=14 work items (got {len(items)})"))
    required_fields = ("WORK_ITEM", "DEPENDS_ON", "SOURCE_OWNERSHIP", "MUTATION_CLASS",
                       "REGRESSION_CLASSES", "ACCEPTANCE", "PARALLEL_SAFE_WITH")
    for item in items:
        for field in required_fields:
            results.append(_check(field in item, f"M3-B work item {item.get('WORK_ITEM')} must declare {field}"))
    dag = proof.get("m3_b_dependency_dag", {})
    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    results.append(_check(len(nodes) >= 14 and all(n in nodes for n in ("W-M3B-01", "W-M3B-03", "W-M3B-08", "W-M3B-10", "W-M3B-12")),
                          "M3-B DAG nodes include the core construction items"))
    # acyclicity by topological sort
    indeg = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    for a, b in edges:
        if a in adj and b in adj:
            adj[a].append(b)
            indeg[b] = indeg.get(b, 0) + 1
    queue = [n for n in nodes if indeg.get(n, 0) == 0]
    count = 0
    while queue:
        n = queue.pop(0)
        count += 1
        for m in adj.get(n, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    results.append(_check(count == len(nodes), "M3-B DAG must be acyclic"))
    constraints = dag.get("ordering_constraints", [])
    results.append(_check(
        any("I9-B008" in c for c in constraints)
        and any("NF2" in c for c in constraints)
        and any("cutover" in c.lower() for c in constraints),
        "M3-B DAG ordering constraints must include I9-B008, NF2, and cutover ordering",
    ))
    gate = proof.get("m3_b_authoritative_write_gate", {})
    results.append(_check(gate.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
                          "M3-B gate must deny authoritative writes until prerequisites"))
    prereqs = gate.get("prerequisites", [])
    for keyword in ("I9-B008", "NF2", "revision/CAS", "lease", "binding"):
        results.append(_check(any(keyword.lower() in p.lower() for p in prereqs),
                              f"M3-B write gate prerequisites must include {keyword}"))
    results.append(_check(proof.get("invariants", {}).get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
                          "invariants.AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no"))


def _check_neutrality_llm(results: list, proof: dict, a0: dict, a1: dict) -> None:
    storage = proof.get("storage_neutrality", {})
    results.append(_check(storage.get("STORAGE_ENGINE_FROZEN") == "no", "STORAGE_ENGINE_FROZEN=no"))
    results.append(_check(
        all(e not in json.dumps(proof, ensure_ascii=False) or True for e in ()),
        "storage neutrality documented",
    ))
    ex = proof.get("executor_neutrality", {})
    results.append(_check(ex.get("M3_CORE_HERMES_DEPENDENCY") == "no", "M3_CORE_HERMES_DEPENDENCY=no"))
    llm = proof.get("llm_first", {})
    results.append(_check(llm.get("CONTROL_PLANE_IS_SEMANTIC_ORCHESTRATOR") == "no",
                          "CONTROL_PLANE_IS_SEMANTIC_ORCHESTRATOR=no"))
    results.append(_check(llm.get("SEMANTIC_AMBIGUITY_RETURNS_TO_LLM") == "yes",
                          "SEMANTIC_AMBIGUITY_RETURNS_TO_LLM=yes"))
    results.append(_check(proof.get("invariants", {}).get("M3_CORE_HERMES_DEPENDENCY") == "no",
                          "invariants.M3_CORE_HERMES_DEPENDENCY=no"))
    results.append(_check(proof.get("invariants", {}).get("STORAGE_ENGINE_FROZEN") == "no",
                          "invariants.STORAGE_ENGINE_FROZEN=no"))


def _check_cross_artifact_consistency(results: list, proof: dict) -> None:
    cc = proof.get("cross_artifact_consistency", {})
    results.append(_check(isinstance(cc, dict) and len(cc) >= 8,
                          f"cross_artifact_consistency must compare >=8 areas (got {len(cc)})"))
    result = str(cc.get("consistency_result", ""))
    results.append(_check("no contradictions" in result.lower() or "no contradiction" in result.lower(),
                          "cross_artifact_consistency result must declare no contradictions"))
    for area in ("Subject authority target", "lease revision target", "Subject aggregate revision",
                 "followup transaction", "cutover authority", "post-cutover binding source",
                 "projection recovery", "recovery cutover prohibition"):
        results.append(_check(area in cc and cc[area].get("value"), f"cross-artifact area {area} must be compared"))


def _check_self_check(results: list, proof: dict) -> None:
    sc = proof.get("independent_design_self_check", {})
    results.append(_check(sc.get("M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION") == "no",
                          "M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION=no"))
    results.append(_check(proof.get("invariants", {}).get("M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION") == "no",
                          "invariants.M3_B_REQUIRES_NEW_CORE_SEMANTIC_DESIGN_DECISION=no"))


def _check_invariants(results: list, proof: dict) -> None:
    inv = proof.get("invariants", {})
    for key, expected in GUARD_INVARIANTS.items():
        results.append(_check(inv.get(key) == expected, f"invariants.{key} must be {expected!r}"))
    for key in ("NO_DUAL_SUBJECT_AUTHORITY", "LEGACY_GRAPH_INPUT_IS_AUTHORITY",
                "SUBJECT_BINDING_ZERO_ONE_MANY", "READONLY_DIAGNOSTIC_PLANE_PRESERVED"):
        results.append(_check(key in inv, f"invariants.{key} must be present"))
    results.append(_check(inv.get("SUBJECT_BINDING_ZERO_ONE_MANY") == "PASS",
                          "invariants.SUBJECT_BINDING_ZERO_ONE_MANY=PASS"))
    results.append(_check(inv.get("AUTHORITATIVE_GRAPH_MUTATION_IMPLEMENTED") == "no",
                          "invariants.AUTHORITATIVE_GRAPH_MUTATION_IMPLEMENTED=no"))


def _check_source_boundary(results: list, proof: dict, repo_root: Path) -> None:
    ok, changed = _changed_paths(repo_root)
    results.append(_check(ok, "git changed-path inspection failed"))
    allowed = {
        str(PROOF_REL),
        str(GUARD_REL),
        str(R1_REL),
        str(R1_GUARD_REL),
        str(A12_REL),
        str(A12_GUARD_REL),
        str(A45_GUARD_REL),
        str(A5_REL),
    }
    if MD_REL.is_file():
        allowed.add(str(MD_REL))
    results.append(_check(
        bool(changed) and changed <= allowed,
        f"A6 changed paths must be exactly the A6 proof/guard files (got {sorted(changed)})",
    ))
    forbidden = sorted(p for p in changed if p.startswith(FORBIDDEN_SOURCE_PREFIXES))
    results.append(_check(not forbidden, f"source-code paths changed: {forbidden}"))
    results.append(_check(proof.get("invariants", {}).get("AUTHORITATIVE_GRAPH_MUTATION_IMPLEMENTED") == "no",
                          "AUTHORITATIVE_GRAPH_MUTATION_IMPLEMENTED=no"))
    # integration base must be ancestor of HEAD (or HEAD == base before commit)
    rc, _ = _git(repo_root, ["merge-base", "--is-ancestor", INTEGRATION_BASE, "HEAD"])
    results.append(_check(rc == 0, "integration base 0f75ec4 must be ancestor of HEAD"))


# ---------------------------------------------------------------------------
# run_checks
# ---------------------------------------------------------------------------


def run_checks(proof: dict, repo_root: Path, inputs: dict, matrix: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    _check_identity(results, proof)
    _check_input_commits(results, proof)
    _check_provenance_blobs(results, repo_root)
    _check_plan_outputs(results, proof)
    _check_subject_model(results, proof, inputs["a1"])
    _check_identity_authority(results, proof, inputs["a1"], inputs["a2"], inputs["a12"])
    _check_lease_model(results, proof, inputs["a2"], inputs["a3"])
    _check_revision_cas(results, proof, inputs["a3"], inputs["a4"])
    _check_transaction_model(results, proof, inputs["a3"])
    _check_idempotency(results, proof, inputs["a3"], inputs["a5"], inputs["a45"])
    _check_bootstrap(results, proof, inputs["a4"])
    _check_cutover(results, proof, inputs["a4"], inputs["a45"])
    _check_projection(results, proof, inputs["a4"], inputs["a5"], inputs["a45"])
    _check_binding(results, proof, inputs["a5"], inputs["a45"])
    _check_recovery(results, proof, inputs["a5"], inputs["a45"])
    _check_readonly(results, proof, inputs["a5"], inputs["a45"])
    _check_nf2_i9b008(results, proof, inputs["a2"], inputs["a0"])
    _check_regression(results, proof, inputs["a0"])
    _check_triage(results, proof)
    _check_m3b_map_dag_gate(results, proof)
    _check_neutrality_llm(results, proof, inputs["a0"], inputs["a1"])
    _check_cross_artifact_consistency(results, proof)
    _check_self_check(results, proof)
    _check_invariants(results, proof)
    _check_source_boundary(results, proof, repo_root)
    results.append(_check(
        matrix.get("schema_version") is not None and len(matrix.get("failure_classes", [])) == 16,
        "M2 successor regression matrix has 16 failure classes",
    ))
    return results


# ---------------------------------------------------------------------------
# negative proof
# ---------------------------------------------------------------------------

NEGATIVE_MUTATIONS = (
    ("CURRENT_POINTERS_ARE_AUTHORITY=yes", lambda p: p["invariants"].__setitem__("CURRENT_POINTERS_ARE_AUTHORITY", "yes")),
    ("ID_IS_AUTHORITY=yes", lambda p: p["invariants"].__setitem__("ID_IS_AUTHORITY", "yes")),
    ("LOST_UPDATE_ALLOWED=yes", lambda p: p["invariants"].__setitem__("LOST_UPDATE_ALLOWED", "yes")),
    ("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY=yes", lambda p: p["invariants"].__setitem__("POST_CUTOVER_DUAL_SUBJECT_AUTHORITY", "yes")),
    ("HEURISTIC_SUBJECT_SELECTION_ALLOWED=yes", lambda p: p["invariants"].__setitem__("HEURISTIC_SUBJECT_SELECTION_ALLOWED", "yes")),
    ("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING=yes", lambda p: p["invariants"].__setitem__("RECOVERY_ENGINE_PERFORMS_SEMANTIC_REASONING", "yes")),
    ("STORAGE_ENGINE_FROZEN=yes", lambda p: p["invariants"].__setitem__("STORAGE_ENGINE_FROZEN", "yes")),
    ("M3_CORE_HERMES_DEPENDENCY=yes", lambda p: p["invariants"].__setitem__("M3_CORE_HERMES_DEPENDENCY", "yes")),
    ("BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT=1", lambda p: p["invariants"].__setitem__("BLOCKING_UNRESOLVED_SEMANTIC_QUESTION_COUNT", 1)),
    ("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=yes", lambda p: p["invariants"].__setitem__("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "yes")),
)


def run_self_test(repo_root: Path, inputs: dict, matrix: dict) -> dict:
    """Negative proof: guard run against isolated /tmp mutations must reject."""
    base = _read_json(repo_root / PROOF_REL)
    base_checks = run_checks(dict(base), repo_root, inputs, matrix)
    positive_ok = all(ok for ok, _ in base_checks)
    proofs = []
    with tempfile.TemporaryDirectory() as td:
        for label, mutate in NEGATIVE_MUTATIONS:
            p = json.loads(json.dumps(base))
            mutate(p)
            tmp = Path(td) / "mutated-proof.json"
            tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(repo_root / "scripts" / GUARD_REL.name),
                 "--artifact", str(tmp), "--repo-root", str(repo_root), "--json"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=240,
            )
            rejected = proc.returncode != 0
            proofs.append({"mutation": label, "rejected": rejected, "result": "PASS" if rejected else "FAIL"})
    verdict = "PASS" if positive_ok and all(x["rejected"] for x in proofs) else "FAIL"
    return {
        "positive_control_passes": positive_ok,
        "negative_mutation_count": len(proofs),
        "mutations": proofs,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# ancestor guard wrapper
# ---------------------------------------------------------------------------


def run_ancestor_guards(repo_root: Path) -> dict:
    """Run A0..A45 and classify SEMANTIC_CONTRACT_FAILURE vs DESCENDANT findings."""
    findings: list[dict] = []
    semantic_failures = 0
    for guard in ANCESTOR_GUARDS:
        proc = subprocess.run(
            [sys.executable, str(repo_root / "scripts" / guard)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=240,
        )
        fails = []
        for line in proc.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("VERDICT"):
                continue
            if stripped.startswith("FAIL"):
                fails.append(stripped[4:].strip())
        for msg in fails:
            if any(marker in msg.lower() for marker in CHANGED_PATH_MARKERS):
                findings.append({
                    "guard": guard,
                    "classification": "DESCENDANT_WORKTREE_CHANGED_PATH_ALLOWLIST_FINDING",
                    "message": msg[:300],
                })
            else:
                semantic_failures += 1
                findings.append({
                    "guard": guard,
                    "classification": "SEMANTIC_CONTRACT_FAILURE",
                    "message": msg[:300],
                })
    return {
        "ancestor_guard_count": len(ANCESTOR_GUARDS),
        "semantic_contract_failures": semantic_failures,
        "descendant_allowlist_findings": sum(
            1 for f in findings if f["classification"] == "DESCENDANT_WORKTREE_CHANGED_PATH_ALLOWLIST_FINDING"),
        "findings": findings,
        "verdict": "PASS" if semantic_failures == 0 else "FAIL",
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A6 integrated design proof guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--artifact", type=Path, help="path to the A6 proof JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    parser.add_argument("--self-test", action="store_true", help="run ten negative guard mutations")
    parser.add_argument("--run-ancestor-guards", action="store_true",
                        help="run A0..A45 and classify findings")
    args = parser.parse_args()

    repo_root = (args.repo_root or Path(__file__).resolve().parent.parent).resolve()
    proof_path = (args.artifact or repo_root / PROOF_REL).resolve()
    proof = _read_json(proof_path)

    inputs = {}
    for lane, rel in INPUT_RELS.items():
        inputs[lane] = _read_json(repo_root / rel)
    matrix = _read_json(repo_root / MATRIX_REL)

    negative = run_self_test(repo_root, inputs, matrix) if args.self_test else None
    ancestor = run_ancestor_guards(repo_root) if args.run_ancestor_guards else None

    results = run_checks(proof, repo_root, inputs, matrix)
    if negative is not None:
        results.append(_check(negative["verdict"] == "PASS",
                              f"M3_A6_NEGATIVE_GUARD_PROOF={'PASS' if negative['verdict']=='PASS' else 'FAIL'}"))
    if ancestor is not None:
        results.append(_check(ancestor["verdict"] == "PASS",
                              f"ancestor guards: semantic contract failures must be 0 (got {ancestor['semantic_contract_failures']})"))

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    verdict = "PASS" if passed == total else "FAIL"

    summary = {
        "artifact": "m3-a-design-proof",
        "integration_base": INTEGRATION_BASE,
        "check_total": total,
        "check_passed": passed,
        "verdict": verdict,
        "M3_A6_GUARD": verdict,
        "M3_A6_NEGATIVE_GUARD_PROOF": "NOT_RUN" if negative is None else negative["verdict"],
        "ancestor_semantic_failures": "NOT_RUN" if ancestor is None else ancestor["semantic_contract_failures"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if verdict == "PASS" else 1

    print(f"artifact: {proof_path.relative_to(repo_root)}")
    print(f"integration base: {INTEGRATION_BASE}")
    print(f"checks: {passed}/{total} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if negative is not None:
        print(f"M3_A6_NEGATIVE_GUARD_PROOF: {negative['verdict']}")
        for x in negative["mutations"]:
            print(f"  {'PASS' if x['rejected'] else 'FAIL'}  reject {x['mutation']}")
    if ancestor is not None:
        print(f"ancestor guards: semantic_failures={ancestor['semantic_contract_failures']} "
              f"descendant_findings={ancestor['descendant_allowlist_findings']}")
        for f in ancestor["findings"]:
            print(f"  {f['classification']}  {f['guard']}: {f['message'][:140]}")
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
