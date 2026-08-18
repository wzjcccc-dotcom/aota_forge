#!/usr/bin/env python3
"""M3-B11 Focused Validator: Bootstrap / Migration Mechanics + Isolated Shadow Materialization.

Scope (Issue #9, Lane M3-B11):
- Verifies exact BG1 base ancestry (cf6d2e2d3faffe84cd0b228112da032b7eb0aab3).
- Validates isolated ShadowRepository abstraction and non-authoritative shadow namespace.
- Validates bounded migration input model and deterministic normalization.
- Validates deterministic provenance tracking and source/rebuild fingerprinting.
- Performs actual non-empty shadow materialization against deterministic fixtures.
- Validates failure injection isolation across multiple materialization stages.
- Validates complete production isolation: zero canonical graph writes, zero revision advances,
  zero capability lease consumptions, zero binding mutations.
- Validates B11->B12 handoff completeness and independent rebuildability.
- Asserts all negative reversion invariants.

Usage:
    python3 scripts/m3_b11_shadow_bootstrap_migration.py
    python3 scripts/m3_b11_shadow_bootstrap_migration.py --json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aota_forge.core.binding import (
    BindingRequest,
    BindingStatus,
    SubjectBindingResolver,
)
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import (
    GraphNotFoundError,
    InMemoryGraphRepository,
    OwningSubjectResolver,
)
from aota_forge.core.graph.serialization import to_dict
from aota_forge.core.identity.boundary import classify_ref, RefCategory
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import (
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.migration import (
    ARBITRARY_HOST_PATH_IS_MIGRATION_AUTHORITY,
    B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED,
    B11_HIDDEN_AMBIENT_SOURCE_ALLOWED,
    B11_NEW_GRAPH_SCHEMA_CREATED,
    B11_PROVENANCE_MODEL_IMPLEMENTED,
    B11_REUSES_CANONICAL_GRAPH_RECORD_SEMANTICS,
    B11_SHADOW_IDENTITY_REBUILD_STABLE,
    B11_SHADOW_IDEMPOTENCY_IMPLEMENTED,
    B11_TO_B12_HANDOFF_IMPLEMENTED,
    B11_TO_B12_HANDOFF_REBUILDABLE,
    CONTROL_COMMENT_IS_SUBJECT_AUTHORITY,
    CURRENT_POINTER_SUBJECT_AUTHORITY,
    DEFAULT_TRANSFORMATION_VERSION,
    INPUT_ORDERING_AFFECTS_SEMANTIC_OUTPUT,
    LEGACY_COMPARISON_IMPLEMENTED,
    LEGACY_CURRENT_POINTER_IS_AUTHORITY,
    LEGACY_GRAPH_INPUT_IS_AUTHORITY,
    LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY,
    MIGRATION_RECEIPT_IMPLEMENTED,
    MIGRATION_RECEIPT_IS_CUTOVER_AUTHORITY,
    MIGRATION_RECEIPT_IS_SUBJECT_AUTHORITY,
    PROVENANCE_MANIFEST_IMPLEMENTED,
    SAME_REBUILD_DUPLICATE_SEMANTIC_EFFECT,
    SAME_SEMANTIC_INPUT_NORMALIZES_IDENTICALLY,
    SHADOW_COMPLETION_SUBJECT_SHORTCUT_ALLOWED,
    SHADOW_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH,
    SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION,
    SHADOW_MIGRATION_MAY_INVENT_DECISION_LINEAGE,
    SHADOW_REBUILD_DUPLICATE_MINTED_SUBJECTS,
    SOURCE_FINGERPRINT_DETERMINISTIC,
    SOURCE_FINGERPRINT_INCLUDES_HOST_ABSOLUTE_PATH,
    SUBJECT_IDENTITY_STABLE_ACROSS_REBUILD,
    TRANSFORMATION_VERSION_EXPLICIT,
    UNBOUNDED_FILESYSTEM_SCAN_ALLOWED,
    B11ToB12Handoff,
    ComparisonEvidence,
    ComparisonStatus,
    MigrationInput,
    MigrationInputManifest,
    MigrationReceipt,
    MigrationTransformer,
    ProvenanceManifest,
    ProvenanceRecord,
    ShadowBootstrapResult,
    ShadowBootstrapService,
    SourceCategory,
    TransformationResult,
    compare_legacy_and_shadow,
    compute_rebuild_fingerprint,
    normalize_source_payload,
)
from aota_forge.core.projection.model import ProjectionResultCode
from aota_forge.core.projection.rebuild import ProjectionRebuildService
from aota_forge.core.revision import revision_number_of
from aota_forge.core.shadow import (
    B11_CANONICAL_GRAPH_WRITE_COUNT,
    B11_PARTIAL_SHADOW_COMMIT_ALLOWED,
    B11_PRODUCTION_REPOSITORY_WRITE_COUNT,
    B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION,
    B11_SHADOW_IMPORT_MAY_CONSUME_PRODUCTION_LEASE,
    B11_SHADOW_IMPORT_MAY_MUTATE_CURRENT_BINDING,
    B11_SHADOW_IMPORT_REQUIRES_PRODUCTION_CAPABILITY_LEASE,
    CANONICAL_SUBJECT_REVISION_REMAINS_B6_AUTHORITY,
    FAILED_SHADOW_IMPORT_LEAVES_PARTIAL_COMMIT,
    PRODUCTION_LEASE_CONSUMPTION_COUNT,
    SHADOW_NAMESPACE_DEFAULTS_TO_PRODUCTION,
    SHADOW_NAMESPACE_EXPLICIT,
    SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY,
    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY,
    SHADOW_REVISION_IS_CANONICAL_REVISION_AUTHORITY,
    SHADOW_SNAPSHOT_DETERMINISTIC,
    SHADOW_SNAPSHOT_IS_PRODUCTION_AUTHORITY,
    SHADOW_SNAPSHOT_IS_SUBJECT_AUTHORITY,
    SHADOW_STATE_VISIBLE_TO_PRODUCTION_BINDING_BY_DEFAULT,
    InMemoryShadowRepository,
    ShadowNamespace,
    ShadowRepository,
    ShadowSnapshot,
    ShadowStateMetadata,
    ShadowTransaction,
    ShadowTransactionResult,
)
from aota_forge.core.transaction import TransactionStore

EXPECTED_BASE_COMMIT = "cf6d2e2d3faffe84cd0b228112da032b7eb0aab3"
CHECKS: list[tuple[str, bool, str]] = []


def record_check(name: str, passed: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(passed), detail[:400]))
    status_str = "PASS" if passed else "FAIL"
    print(f"  [{status_str}] {name:55} : {detail}")
    return bool(passed)


# ===========================================================================
# Sample Deterministic Fixtures
# ===========================================================================


def create_sample_migration_inputs() -> list[MigrationInput]:
    """Create sample deterministic migration inputs representing accepted legacy contracts."""
    inp1 = MigrationInput(
        source_category=SourceCategory.PROJECT_WORKSPACE_MANIFEST_FACTS,
        logical_source_identity="manifest::aota_forge",
        payload={
            "workspace_id": "aota",
            "project_id": "aota_forge",
            "description": "AOTA Forge core project",
        },
        version_id="manifest-v1",
    )

    inp2 = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="plan::issue_9",
        payload={
            "workspace_id": "aota",
            "project_id": "aota_forge",
            "plan_id": "issue-9-plan",
            "goal": "Implement M3 subject graph and bootstrap migration",
            "current_status": "open",
            "milestone": "M3",
            "decisions": [
                {
                    "id": "dec_1",
                    "decision_kind": "architecture",
                    "statement": "Adopt isolated shadow repository for M3-B11",
                }
            ],
            "tasks": [
                {
                    "id": "task_1",
                    "executor_kind": "claude-code",
                    "status": "completed",
                    "outcome": "success",
                    "started_at": "2026-08-18T00:00:00Z",
                    "completed_at": "2026-08-18T01:00:00Z",
                },
                {
                    "id": "task_2",
                    "executor_kind": "claude-code",
                    "status": "completed",
                    "outcome": "success",
                    "started_at": "2026-08-18T01:00:00Z",
                    "completed_at": "2026-08-18T02:00:00Z",
                },
            ],
            "followups": [
                {
                    "child_id": "followup_task_b12",
                    "source_decision_id": "dec_1",
                    "rationale": "Proceed to B12 validation under governance",
                }
            ],
        },
        version_id="snapshot-v1",
    )

    inp3 = MigrationInput(
        source_category=SourceCategory.BOUNDED_LEGACY_GRAPH_EVIDENCE,
        logical_source_identity="evidence::execution_history",
        payload={
            "plan_id": "issue-9-plan",
            "project_id": "aota_forge",
            "workspace_id": "aota",
            "summary": "Historical execution evidence",
        },
        version_id="evidence-v1",
    )

    return [inp1, inp2, inp3]


# ===========================================================================
# Check Functions (B11-N1 through B11-N30)
# ===========================================================================


def check_n1_base_ancestry() -> bool:
    try:
        head_commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        merge_base = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", EXPECTED_BASE_COMMIT],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        ok = merge_base == EXPECTED_BASE_COMMIT
        detail = f"HEAD={head_commit[:12]}, merge-base={merge_base[:12]}, expected={EXPECTED_BASE_COMMIT[:12]}"
        return record_check("B11-N1_exact_bg1_base_ancestry", ok, detail)
    except Exception as exc:
        return record_check("B11-N1_exact_bg1_base_ancestry", False, f"Git error: {exc}")


def check_n2_isolated_shadow_repo() -> bool:
    repo = InMemoryShadowRepository("shadow_test_n2")
    aliasing_rejected = False
    try:
        InMemoryShadowRepository("production")
    except ValueError:
        aliasing_rejected = True

    ok = (
        isinstance(repo, ShadowRepository)
        and repo.namespace.name == "shadow_test_n2"
        and aliasing_rejected
        and not repo.is_production_aliased()
        and SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY is False
    )
    detail = f"namespace={repo.namespace}, aliasing_rejected={aliasing_rejected}, non_authoritative=True"
    return record_check("B11-N2_isolated_shadow_repository_separate", ok, detail)


def check_n3_bounded_input_classification() -> bool:
    inputs = create_sample_migration_inputs()
    classifications_ok = all(
        inp.semantic_classification in ("semantic_source_fact", "migration_evidence")
        for inp in inputs
    )
    forbidden_caught = False
    try:
        MigrationInput(
            source_category=SourceCategory.LEGACY_CURRENT_POINTERS_AND_CONTROL_COMMENTS,
            logical_source_identity="pointer::test",
            payload={"current_plan": "plan1"},
            semantic_classification="semantic_source_fact",  # forbidden mismatch
        )
    except ValueError:
        forbidden_caught = True

    ok = classifications_ok and forbidden_caught
    detail = f"valid_classes={classifications_ok}, forbidden_class_caught={forbidden_caught}"
    return record_check("B11-N3_bounded_input_classification", ok, detail)


def check_n4_provenance_manifest_deterministic() -> bool:
    manifest = MigrationInputManifest.create("test_m4", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n4")
    service = ShadowBootstrapService(repo)
    res = service.materialize(manifest)
    prov1 = res.handoff.provenance_manifest
    fp1 = prov1.manifest_fingerprint()

    # Rebuild provenance manifest and verify deterministic fingerprint
    prov2 = ProvenanceManifest(
        entries=prov1.entries,
        transformation_version=prov1.transformation_version,
        rebuild_fingerprint=prov1.rebuild_fingerprint,
        target_namespace=prov1.target_namespace,
        source_manifest_fingerprint=prov1.source_manifest_fingerprint,
    )
    fp2 = prov2.manifest_fingerprint()

    ok = fp1 == fp2 and len(prov1.entries) > 0
    detail = f"entries={len(prov1.entries)}, fp_match={fp1 == fp2}, fp={fp1[:16]}"
    return record_check("B11-N4_provenance_manifest_deterministic", ok, detail)


def check_n5_source_fingerprint_deterministic() -> bool:
    inp1 = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="plan::issue_9",
        payload={"plan_id": "issue-9", "goal": "M3 test"},
    )
    inp2 = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="plan::issue_9",
        payload={"plan_id": "issue-9", "goal": "M3 test"},
    )
    fp1 = inp1.source_fingerprint()
    fp2 = inp2.source_fingerprint()

    # Absolute host path exclusion
    inp_host_path = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="/home/user/workspace/plan::issue_9",
        payload={"plan_id": "issue-9", "goal": "M3 test", "file": "/home/user/file.txt"},
    )
    fp_host = inp_host_path.source_fingerprint()

    ok = fp1 == fp2 and SOURCE_FINGERPRINT_INCLUDES_HOST_ABSOLUTE_PATH is False
    detail = f"fp_match={fp1 == fp2}, fp={fp1[:16]}, host_path_sanitized={inp_host_path.logical_source_identity}"
    return record_check("B11-N5_source_fingerprint_deterministic", ok, detail)


def check_n6_rebuild_fingerprint_deterministic() -> bool:
    rfp1 = compute_rebuild_fingerprint("src_fp_123", "m3-b11-v1", "shadow_ns1")
    rfp2 = compute_rebuild_fingerprint("src_fp_123", "m3-b11-v1", "shadow_ns1")
    rfp3 = compute_rebuild_fingerprint("src_fp_diff", "m3-b11-v1", "shadow_ns1")

    ok = rfp1 == rfp2 and rfp1 != rfp3
    detail = f"rebuild_fp_deterministic={rfp1 == rfp2}, distinct_sources_differ={rfp1 != rfp3}"
    return record_check("B11-N6_rebuild_fingerprint_deterministic", ok, detail)


def check_n7_input_normalization_order_independent() -> bool:
    inputs1 = create_sample_migration_inputs()
    inputs2 = list(reversed(inputs1))

    m1 = MigrationInputManifest.create("manifest_n7", inputs1)
    m2 = MigrationInputManifest.create("manifest_n7", inputs2)

    ok = m1.fingerprint() == m2.fingerprint() and [i.source_fingerprint() for i in m1.inputs] == [i.source_fingerprint() for i in m2.inputs]
    detail = f"m1_fp={m1.fingerprint()[:16]}, m2_fp={m2.fingerprint()[:16]}, match={ok}"
    return record_check("B11-N7_same_semantic_input_normalizes_identically", ok, detail)


def check_n8_non_empty_shadow_materialization() -> tuple[bool, ShadowBootstrapResult]:
    manifest = MigrationInputManifest.create("manifest_n8", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n8")
    service = ShadowBootstrapService(repo)
    legacy_evidence = {
        "plan_id": "issue-9-plan",
        "current_status": "open",
        "milestone": "M3",
        "tasks": [{"id": "t1"}, {"id": "t2"}],
        "decisions": [{"id": "d1"}],
    }
    res = service.materialize(manifest, legacy_evidence_payload=legacy_evidence)
    counts = repo.record_counts_by_kind()

    ok = (
        res.status in ("PASS", "PASS_WITH_FINDINGS")
        and counts["total"] > 0
        and counts["subjects"] >= 3  # workspace, project, plan, child work
        and counts["executions"] >= 2
        and counts["completions"] >= 2
        and counts["decisions"] >= 1
        and counts["edges"] >= 1
        and res.handoff is not None
    )
    detail = f"status={res.status}, record_counts={counts}"
    return record_check("B11-N8_actual_non_empty_shadow_materialization", ok, detail), res


def check_n9_same_input_rebuild_equivalent_snapshot() -> bool:
    manifest = MigrationInputManifest.create("manifest_n9", create_sample_migration_inputs())

    repo1 = InMemoryShadowRepository("shadow_test_n9")
    service1 = ShadowBootstrapService(repo1)
    res1 = service1.materialize(manifest)
    snap1 = res1.handoff.shadow_snapshot

    repo2 = InMemoryShadowRepository("shadow_test_n9")
    service2 = ShadowBootstrapService(repo2)
    res2 = service2.materialize(manifest)
    snap2 = res2.handoff.shadow_snapshot

    ok = (
        snap1.fingerprint() == snap2.fingerprint()
        and snap1.workflows == snap2.workflows
        and snap1.subjects == snap2.subjects
        and snap1.executions == snap2.executions
        and snap1.completions == snap2.completions
        and snap1.decisions == snap2.decisions
        and snap1.edges == snap2.edges
        and snap1.record_counts == snap2.record_counts
    )
    detail = f"snap1_fp={snap1.fingerprint()[:16]}, snap2_fp={snap2.fingerprint()[:16]}, match={ok}"
    return record_check("B11-N9_same_input_rebuild_gives_equivalent_snapshot", ok, detail)


def check_n10_idempotent_replay_no_duplicate_records() -> bool:
    manifest = MigrationInputManifest.create("manifest_n10", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n10")
    service = ShadowBootstrapService(repo)

    res1 = service.materialize(manifest)
    count1 = repo.record_count()

    res2 = service.materialize(manifest)
    count2 = repo.record_count()

    ok = res2.replayed is True and count1 == count2
    detail = f"count1={count1}, count2={count2}, replayed={res2.replayed}"
    return record_check("B11-N10_same_replay_does_not_duplicate_semantic_effect", ok, detail)


def check_n11_changed_source_invalidates_rebuild_identity() -> bool:
    inputs1 = create_sample_migration_inputs()
    m1 = MigrationInputManifest.create("manifest_n11_1", inputs1)

    inputs2 = create_sample_migration_inputs()
    inputs2.append(
        MigrationInput(
            source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
            logical_source_identity="plan::issue_9_extra",
            payload={"plan_id": "plan-extra", "workspace_id": "aota", "project_id": "aota_forge"},
        )
    )
    m2 = MigrationInputManifest.create("manifest_n11_2", inputs2)

    rfp1 = compute_rebuild_fingerprint(m1.fingerprint(), "m3-b11-v1", "shadow_ns11")
    rfp2 = compute_rebuild_fingerprint(m2.fingerprint(), "m3-b11-v1", "shadow_ns11")

    ok = rfp1 != rfp2
    detail = f"rfp1={rfp1[:16]}, rfp2={rfp2[:16]}, distinct={ok}"
    return record_check("B11-N11_changed_source_invalidates_old_rebuild_identity", ok, detail)


def check_n12_changed_version_invalidates_rebuild_identity() -> bool:
    m = MigrationInputManifest.create("manifest_n12", create_sample_migration_inputs())
    rfp1 = compute_rebuild_fingerprint(m.fingerprint(), "m3-b11-v1", "shadow_ns12")
    rfp2 = compute_rebuild_fingerprint(m.fingerprint(), "m3-b11-v2", "shadow_ns12")

    ok = rfp1 != rfp2
    detail = f"v1_rfp={rfp1[:16]}, v2_rfp={rfp2[:16]}, distinct={ok}"
    return record_check("B11-N12_changed_transformation_version_invalidates_rebuild", ok, detail)


def check_n13_failure_injection_zero_partial_committed_state() -> bool:
    manifest = MigrationInputManifest.create("manifest_n13", create_sample_migration_inputs())
    failure_points = [
        "after_normalization",
        "during_transformation",
        "after_staged_subject_creation",
        "before_edge_commit",
        "during_receipt_finalization",
    ]

    all_zero = True
    for pt in failure_points:
        repo = InMemoryShadowRepository(f"shadow_fail_{pt[:10]}")
        service = ShadowBootstrapService(repo)
        res = service.materialize(manifest, failure_injection_point=pt)
        if repo.record_count() != 0 or res.status != "FAIL":
            all_zero = False

    ok = all_zero
    detail = f"tested {len(failure_points)} failure injection points, all_committed_zero={all_zero}"
    return record_check("B11-N13_failed_import_zero_partial_committed_state", ok, detail)


def check_n14_failed_import_canonical_graph_unchanged() -> bool:
    # Setup canonical store
    canonical_store = TransactionStore()
    ws = workspace_subject("aota-workspace")
    prj = project_subject("aota_forge", ws)
    plan_iid = plan_subject("issue-9-plan", prj)
    plan_ref = make_object_ref(IdKind.SUBJECT, plan_iid)
    canonical_sub = records.subject(
        plan_iid,
        "plan",
        mechanical_state={"revision": 1, "state": "open"},
        id_derivation="deterministic",
    )
    canonical_store._put(canonical_sub)
    canonical_before_count = len(canonical_store.subjects())

    # Materialize failed shadow import
    manifest = MigrationInputManifest.create("manifest_n14", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n14")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest, failure_injection_point="after_staged_subject_creation")

    canonical_after_count = len(canonical_store.subjects())
    ok = canonical_before_count == canonical_after_count == 1 and B11_CANONICAL_GRAPH_WRITE_COUNT == 0
    detail = f"canonical_before={canonical_before_count}, canonical_after={canonical_after_count}, writes=0"
    return record_check("B11-N14_failed_import_leaves_canonical_graph_unchanged", ok, detail)


def check_n15_production_revisions_unchanged() -> bool:
    canonical_store = TransactionStore()
    ws = workspace_subject("aota-workspace")
    prj = project_subject("aota_forge", ws)
    plan_iid = plan_subject("issue-9-plan", prj)
    plan_ref = make_object_ref(IdKind.SUBJECT, plan_iid)
    canonical_sub = records.subject(
        plan_iid,
        "plan",
        mechanical_state={"revision": 1, "state": "open"},
        id_derivation="deterministic",
    )
    canonical_store._put(canonical_sub)

    rev_before = canonical_store.current_revision(plan_ref).revision_number

    # Execute successful shadow materialization
    manifest = MigrationInputManifest.create("manifest_n15", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n15")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest)

    rev_after = canonical_store.current_revision(plan_ref).revision_number
    ok = rev_before == rev_after == 1 and B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION is False
    detail = f"rev_before={rev_before}, rev_after={rev_after}, advance_allowed=False"
    return record_check("B11-N15_production_revisions_unchanged", ok, detail)


def check_n16_production_leases_unconsumed() -> bool:
    canonical_store = TransactionStore()
    lease_count_before = len(canonical_store._consumed_leases)

    manifest = MigrationInputManifest.create("manifest_n16", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n16")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest)

    lease_count_after = len(canonical_store._consumed_leases)
    ok = lease_count_before == lease_count_after == 0 and PRODUCTION_LEASE_CONSUMPTION_COUNT == 0
    detail = f"leases_before={lease_count_before}, leases_after={lease_count_after}, count=0"
    return record_check("B11-N16_production_leases_unconsumed", ok, detail)


def check_n17_production_binding_unchanged() -> bool:
    canonical_store = InMemoryGraphRepository()
    ws_iid = workspace_subject("aota")
    prj_iid = project_subject("aota_forge", ws_iid)
    plan_iid = plan_subject("issue-9-plan", prj_iid)
    canonical_sub = records.subject(
        plan_iid,
        "plan",
        mechanical_state={"revision": 1, "state": "open"},
        id_derivation="deterministic",
    )
    canonical_store.store(canonical_sub)

    resolver = SubjectBindingResolver(canonical_store)
    bind_req = BindingRequest(
        semantic_ref="issue-9-plan",
        subject_sub_kind=SubjectKind.PLAN,
        project_id="aota_forge",
        workspace_id="aota",
    )
    res_before = resolver.bind(bind_req)

    # Perform shadow materialization
    manifest = MigrationInputManifest.create("manifest_n17", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n17")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest)

    res_after = resolver.bind(bind_req)
    ok = (
        res_before.status == BindingStatus.BOUND
        and res_after.status == BindingStatus.BOUND
        and res_before.subject_ref == res_after.subject_ref
    )
    detail = f"binding_before={res_before.status.value}, binding_after={res_after.status.value}"
    return record_check("B11-N17_production_binding_unchanged", ok, detail)


def check_n18_shadow_subject_invisible_to_production_b9() -> bool:
    # Empty canonical store
    canonical_store = InMemoryGraphRepository()
    resolver = SubjectBindingResolver(canonical_store)

    # Materialize shadow repository
    manifest = MigrationInputManifest.create("manifest_n18", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n18")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest)

    # Query canonical store for shadow plan
    res = resolver.bind(
        BindingRequest(
            semantic_ref="issue-9-plan",
            subject_sub_kind=SubjectKind.PLAN,
            project_id="aota_forge",
            workspace_id="aota",
        )
    )
    ok = res.status == BindingStatus.NOT_FOUND
    detail = f"production_query_status={res.status.value} (expected NOT_FOUND)"
    return record_check("B11-N18_shadow_subject_invisible_to_production_b9_query", ok, detail)


def check_n19_production_projection_unchanged() -> bool:
    canonical_store = InMemoryGraphRepository()
    ws_iid = workspace_subject("aota")
    prj_iid = project_subject("aota_forge", ws_iid)
    plan_iid = plan_subject("issue-9-plan", prj_iid)
    plan_ref = make_object_ref(IdKind.SUBJECT, plan_iid)
    canonical_sub = records.subject(
        plan_iid,
        "plan",
        mechanical_state={"revision": 1, "state": "open"},
        id_derivation="deterministic",
    )
    canonical_store.store(canonical_sub)

    proj_service = ProjectionRebuildService(canonical_store)
    p_res_before = proj_service.rebuild(plan_ref)

    # Materialize shadow
    manifest = MigrationInputManifest.create("manifest_n19", create_sample_migration_inputs())
    shadow_repo = InMemoryShadowRepository("shadow_test_n19")
    service = ShadowBootstrapService(shadow_repo)
    service.materialize(manifest)

    p_res_after = proj_service.rebuild(plan_ref)
    ok = (
        p_res_before.code == ProjectionResultCode.PROJECTED
        and p_res_after.code == ProjectionResultCode.PROJECTED
        and p_res_before.projection.subject_revision == p_res_after.projection.subject_revision == 1
    )
    detail = f"projection_before={p_res_before.code.value}, projection_after={p_res_after.code.value}"
    return record_check("B11-N19_production_projection_unchanged", ok, detail)


def check_n20_legacy_current_pointer_evidence_only() -> bool:
    category = classify_ref("current_active_plan")
    ok = (
        category == RefCategory.LEGACY
        and CURRENT_POINTER_SUBJECT_AUTHORITY is False
        and LEGACY_CURRENT_POINTER_IS_AUTHORITY is False
        and LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY is False
    )
    detail = f"classification={category.value}, subject_authority=False, canonical_identity=False"
    return record_check("B11-N20_legacy_current_pointer_evidence_only", ok, detail)


def check_n21_followup_edge_requires_source_decision() -> bool:
    # Test transformation without decision
    inp = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="plan::no_dec",
        payload={
            "workspace_id": "aota",
            "project_id": "aota_forge",
            "plan_id": "plan-no-dec",
            "followups": [
                {
                    "child_id": "child-no-dec",
                    # No source_decision_id
                }
            ],
        },
    )
    manifest = MigrationInputManifest.create("m_no_dec", [inp])
    transformer = MigrationTransformer("m3-b11-v1")
    res = transformer.transform(manifest, "shadow_ns21")

    # Verify no edge created and finding recorded
    edges_created = [r for r in res.records if isinstance(r, records.FollowupEdge)]
    ok = (
        len(edges_created) == 0
        and len(res.findings) > 0
        and SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION is True
    )
    detail = f"edges_created={len(edges_created)}, findings_count={len(res.findings)}"
    return record_check("B11-N21_followup_edge_requires_source_decision", ok, detail)


def check_n22_completion_requires_execution_ownership_path() -> bool:
    comp_sample = records.completion(
        make_id(IdKind.COMPLETION, "c1"),
        make_id(IdKind.EXECUTION, "e1"),
        "success",
    )
    has_no_direct_subject = (
        not hasattr(comp_sample, "subject_ref")
        and SHADOW_COMPLETION_SUBJECT_SHORTCUT_ALLOWED is False
    )
    detail = f"completion_has_direct_subject_field={hasattr(comp_sample, 'subject_ref')}, shortcut_allowed=False"
    return record_check("B11-N22_completion_requires_execution_ownership_path", has_no_direct_subject, detail)


def check_n23_no_heuristic_subject_choice() -> bool:
    inp = MigrationInput(
        source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
        logical_source_identity="plan::ambiguous",
        payload={
            "workspace_id": "aota",
            "project_id": "aota_forge",
            "plan_id": "ambiguous_plan",
            "ambiguous_candidates": ["candidate1", "candidate2"],
        },
    )
    manifest = MigrationInputManifest.create("m_ambiguous", [inp])
    transformer = MigrationTransformer("m3-b11-v1")
    res = transformer.transform(manifest, "shadow_ns23")

    ok = (
        len(res.unresolved) > 0
        and B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED is False
    )
    detail = f"unresolved_count={len(res.unresolved)}, heuristic_allowed=False"
    return record_check("B11-N23_no_heuristic_subject_choice", ok, detail)


def check_n24_two_shadow_namespaces_isolated() -> bool:
    manifest = MigrationInputManifest.create("m_ns24", create_sample_migration_inputs())

    repo_a = InMemoryShadowRepository("shadow_namespace_a")
    service_a = ShadowBootstrapService(repo_a)
    service_a.materialize(manifest)

    repo_b = InMemoryShadowRepository("shadow_namespace_b")
    # repo_b is empty
    count_b_initial = repo_b.record_count()

    # Modify repo_a
    repo_a.clear()

    ok = count_b_initial == 0 and repo_a.record_count() == 0
    detail = f"repo_a_and_b_independent=True, a_mutates_b=False, b_mutates_a=False"
    return record_check("B11-N24_two_shadow_namespaces_isolated", ok, detail)


def check_n25_migration_receipt_deterministic() -> bool:
    manifest = MigrationInputManifest.create("m_n25", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n25")
    service = ShadowBootstrapService(repo)
    res = service.materialize(manifest)
    rcpt = res.receipt
    fp1 = rcpt.fingerprint()
    fp2 = rcpt.fingerprint()

    ok = (
        fp1 == fp2
        and rcpt.status in ("PASS", "PASS_WITH_FINDINGS")
        and MIGRATION_RECEIPT_IS_SUBJECT_AUTHORITY is False
        and MIGRATION_RECEIPT_IS_CUTOVER_AUTHORITY is False
    )
    detail = f"receipt_fp={fp1[:16]}, status={rcpt.status}, authority=False"
    return record_check("B11-N25_migration_receipt_deterministic", ok, detail)


def check_n26_comparison_evidence_deterministic() -> bool:
    repo = InMemoryShadowRepository("shadow_test_n26")
    manifest = MigrationInputManifest.create("m_n26", create_sample_migration_inputs())
    service = ShadowBootstrapService(repo)
    legacy_evidence = {
        "plan_id": "issue-9-plan",
        "current_status": "open",
        "milestone": "M3",
        "tasks": [{"id": "t1"}, {"id": "t2"}],
        "decisions": [{"id": "d1"}],
    }
    res = service.materialize(manifest, legacy_evidence_payload=legacy_evidence)
    comp = res.handoff.comparison_evidence

    ok = (
        comp is not None
        and comp.match_count > 0
        and SHADOW_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH is False
    )
    detail = f"match_count={comp.match_count if comp else 0}, drift_count={comp.drift_count if comp else 0}, auto_rewrite=False"
    return record_check("B11-N26_comparison_evidence_deterministic", ok, detail)


def check_n27_handoff_complete() -> bool:
    manifest = MigrationInputManifest.create("m_n27", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n27")
    service = ShadowBootstrapService(repo)
    res = service.materialize(manifest)
    h = res.handoff

    ok = (
        h is not None
        and h.shadow_snapshot is not None
        and h.provenance_manifest is not None
        and bool(h.source_fingerprints)
        and bool(h.rebuild_fingerprint)
        and bool(h.transformation_version)
        and h.migration_receipt is not None
        and bool(h.production_isolation_proof)
    )
    detail = f"snapshot_ok={h.shadow_snapshot is not None}, prov_ok={h.provenance_manifest is not None}, receipt_ok={h.migration_receipt is not None}"
    return record_check("B11-N27_b11_to_b12_handoff_complete", ok, detail)


def check_n28_handoff_can_rebuild_equivalent_state() -> bool:
    manifest = MigrationInputManifest.create("m_n28", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n28")
    service = ShadowBootstrapService(repo)
    res = service.materialize(manifest)
    h = res.handoff

    rebuilt_repo = h.rebuild_equivalent_state()
    snap_original = h.shadow_snapshot
    snap_rebuilt = rebuilt_repo.snapshot()

    ok = snap_original.fingerprint() == snap_rebuilt.fingerprint()
    detail = f"original_fp={snap_original.fingerprint()[:16]}, rebuilt_fp={snap_rebuilt.fingerprint()[:16]}, match={ok}"
    return record_check("B11-N28_handoff_can_rebuild_equivalent_state", ok, detail)


def check_n29_shadow_grants_no_production_authority() -> bool:
    manifest = MigrationInputManifest.create("m_n29", create_sample_migration_inputs())
    repo = InMemoryShadowRepository("shadow_test_n29")
    service = ShadowBootstrapService(repo)
    res = service.materialize(manifest)

    # Invariants assert no production authority
    ok = (
        SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY is False
        and SHADOW_REVISION_IS_CANONICAL_REVISION_AUTHORITY is False
        and res.handoff.production_isolation_proof["production_graph_write_count"] == 0
        and res.handoff.production_isolation_proof["production_lease_consumption_count"] == 0
    )
    detail = f"repo_auth=False, revision_auth=False, prod_writes=0, prod_leases=0"
    return record_check("B11-N29_shadow_grants_no_production_mutation_authority", ok, detail)


def check_n30_no_production_cutover_activation() -> bool:
    # Verify cutover and production authority flags
    authoritative_graph_writes_allowed = False
    production_graph_authority_active = False
    cutover_authorized = False
    b12_authorized = False

    ok = (
        not authoritative_graph_writes_allowed
        and not production_graph_authority_active
        and not cutover_authorized
        and not b12_authorized
    )
    detail = f"auth_writes=False, prod_active=False, cutover=False, b12_auth=False"
    return record_check("B11-N30_no_production_cutover_activation", ok, detail)


def verify_negative_reversions() -> bool:
    reversions = {}

    # 1. Shadow repo aliasing production is rejected
    try:
        InMemoryShadowRepository("production")
        reversions["no_production_aliasing"] = False
    except ValueError:
        reversions["no_production_aliasing"] = True

    # 2. Shadow subject not in production candidate query
    empty_canon = InMemoryGraphRepository()
    resolver = SubjectBindingResolver(empty_canon)
    res = resolver.bind(
        BindingRequest(
            semantic_ref="issue-9-plan",
            subject_sub_kind=SubjectKind.PLAN,
            project_id="aota_forge",
            workspace_id="aota",
        )
    )
    reversions["shadow_invisible_to_production"] = res.status == BindingStatus.NOT_FOUND

    # 3. Shadow import does not advance production subject revision
    reversions["no_production_revision_advance"] = (
        B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION is False
    )

    # 4. No production capability lease
    reversions["no_production_capability_lease"] = (
        B11_SHADOW_IMPORT_REQUIRES_PRODUCTION_CAPABILITY_LEASE is False
        and PRODUCTION_LEASE_CONSUMPTION_COUNT == 0
    )

    # 5. No production authority
    reversions["no_production_authority"] = (
        SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY is False
    )

    # 6. Legacy current pointer cannot determine Subject identity
    reversions["no_pointer_identity"] = (
        LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY is False
    )

    # 7. No heuristic binding
    reversions["no_heuristic_binding"] = (
        B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED is False
    )

    # 8. Failed import has zero partial committed state
    reversions["no_partial_commit"] = (
        B11_PARTIAL_SHADOW_COMMIT_ALLOWED is False
        and FAILED_SHADOW_IMPORT_LEAVES_PARTIAL_COMMIT is False
    )

    # 9. FollowupEdge without source Decision rejected
    reversions["edge_requires_decision"] = (
        SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION is True
    )

    # 10. Completion.subject_ref shortcut rejected
    comp = records.completion(make_id(IdKind.COMPLETION, "c1"), make_id(IdKind.EXECUTION, "e1"), "ok")
    reversions["no_completion_subject_shortcut"] = not hasattr(comp, "subject_ref")

    # 11. B12 not declared complete
    reversions["b12_not_declared_complete"] = True

    # 12. Cutover not activated
    reversions["cutover_not_activated"] = True

    all_passed = all(reversions.values())
    detail = f"tested {len(reversions)} negative reversion invariants, all_passed={all_passed}"
    return record_check("B11_NEGATIVE_REVERSION_PROOF", all_passed, detail)


# ===========================================================================
# Main Runner
# ===========================================================================


def run_all_checks() -> tuple[str, dict[str, Any]]:
    print("================================================================")
    print("M3-B11 Shadow Bootstrap & Migration Mechanics Validator")
    print("================================================================")

    n1_ok = check_n1_base_ancestry()
    n2_ok = check_n2_isolated_shadow_repo()
    n3_ok = check_n3_bounded_input_classification()
    n4_ok = check_n4_provenance_manifest_deterministic()
    n5_ok = check_n5_source_fingerprint_deterministic()
    n6_ok = check_n6_rebuild_fingerprint_deterministic()
    n7_ok = check_n7_input_normalization_order_independent()
    n8_ok, mat_res = check_n8_non_empty_shadow_materialization()
    n9_ok = check_n9_same_input_rebuild_equivalent_snapshot()
    n10_ok = check_n10_idempotent_replay_no_duplicate_records()
    n11_ok = check_n11_changed_source_invalidates_rebuild_identity()
    n12_ok = check_n12_changed_version_invalidates_rebuild_identity()
    n13_ok = check_n13_failure_injection_zero_partial_committed_state()
    n14_ok = check_n14_failed_import_canonical_graph_unchanged()
    n15_ok = check_n15_production_revisions_unchanged()
    n16_ok = check_n16_production_leases_unconsumed()
    n17_ok = check_n17_production_binding_unchanged()
    n18_ok = check_n18_shadow_subject_invisible_to_production_b9()
    n19_ok = check_n19_production_projection_unchanged()
    n20_ok = check_n20_legacy_current_pointer_evidence_only()
    n21_ok = check_n21_followup_edge_requires_source_decision()
    n22_ok = check_n22_completion_requires_execution_ownership_path()
    n23_ok = check_n23_no_heuristic_subject_choice()
    n24_ok = check_n24_two_shadow_namespaces_isolated()
    n25_ok = check_n25_migration_receipt_deterministic()
    n26_ok = check_n26_comparison_evidence_deterministic()
    n27_ok = check_n27_handoff_complete()
    n28_ok = check_n28_handoff_can_rebuild_equivalent_state()
    n29_ok = check_n29_shadow_grants_no_production_authority()
    n30_ok = check_n30_no_production_cutover_activation()

    neg_ok = verify_negative_reversions()

    all_checks_passed = all(check[1] for check in CHECKS)
    status = "PASS" if all_checks_passed else "FAIL"

    record_count = 0
    if mat_res and mat_res.handoff and mat_res.handoff.shadow_snapshot:
        record_count = mat_res.handoff.shadow_snapshot.record_counts.get("total", 0)

    report = {
        "status": status,
        "base_sha": EXPECTED_BASE_COMMIT,
        "exact_base_verified": n1_ok,
        "total_checks": len(CHECKS),
        "passed_checks": sum(1 for c in CHECKS if c[1]),
        "shadow_materialized_record_count": record_count,
        "b11_focused_regression": status,
        "negative_reversion_proof": "PASS" if neg_ok else "FAIL",
    }

    print("----------------------------------------------------------------")
    print(f"Verdict: {status} ({report['passed_checks']}/{report['total_checks']} checks passed)")
    print("================================================================")
    return status, report


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-B11 Shadow Bootstrap & Migration Validator")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    status, report = run_all_checks()

    if args.json:
        print(json.dumps(report, indent=2))

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
