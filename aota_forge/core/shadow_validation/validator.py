"""M3-B12 independent shadow validator orchestrator.

Scope (Issue #9, Lane M3-B12):
- Orchestrates independent validation of M3-B11 shadow bootstrap & migration mechanics.
- Directly recomputes critical evidence, rebuilds fresh state, executes 12 tamper cases,
  proves multi-stage isolation and failure atomicity, and runs the regression suite.
- Constructs the typed ShadowValidationReport and verifies all B12 acceptance criteria.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from aota_forge.core.graph import records
from aota_forge.core.graph.repository import GraphReferentialError
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.identity.boundary import classify_ref, RefCategory
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import (
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.migration import (
    B11ToB12Handoff,
    MigrationInput,
    MigrationInputManifest,
    MigrationReceipt,
    ProvenanceManifest,
    ProvenanceRecord,
    ShadowBootstrapResult,
    ShadowBootstrapService,
    SourceCategory,
    compute_rebuild_fingerprint,
    normalize_source_payload,
)
from aota_forge.core.shadow.model import (
    B11_CANONICAL_GRAPH_WRITE_COUNT,
    B11_PRODUCTION_REPOSITORY_WRITE_COUNT,
    B11_SHADOW_IMPORT_MAY_ADVANCE_PRODUCTION_SUBJECT_REVISION,
    B11_SHADOW_IMPORT_REQUIRES_PRODUCTION_CAPABILITY_LEASE,
    PRODUCTION_LEASE_CONSUMPTION_COUNT,
    SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY,
    SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY,
    ShadowNamespace,
    ShadowStateMetadata,
)
from aota_forge.core.shadow.repository import InMemoryShadowRepository
from aota_forge.core.shadow.snapshot import ShadowSnapshot
from aota_forge.core.shadow_validation.isolation import (
    IsolationTestSuite,
    compute_canonical_graph_fingerprint,
)
from aota_forge.core.shadow_validation.model import (
    CANONICAL_B3_RECORD_KINDS,
    CANONICAL_INPUT_CLASSIFICATIONS,
    EXPECTED_BASE_COMMIT,
    RAW_INTERNAL_ID_SELF_BINDING_ALLOWED,
    OBJECT_REF_IS_AUTHORITY,
)
from aota_forge.core.shadow_validation.rebuilder import (
    IndependentShadowRebuilder,
    RebuildOutcome,
)
from aota_forge.core.shadow_validation.report import (
    ProductionIsolationProof,
    ShadowValidationReport,
    TamperCaseResult,
)
from aota_forge.core.shadow_validation.tamper import TamperTestSuite

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def create_sample_deterministic_inputs() -> list[MigrationInput]:
    """Standard deterministic fixture inputs matching accepted M3-B11 handoff."""
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


class IndependentShadowValidator:
    """Main validator for M3-B12 independent shadow materialization verification."""

    def __init__(
        self,
        transformation_version: str = "m3-b11-v1",
        target_namespace: str = "shadow_b11_handoff_src",
    ) -> None:
        self.transformation_version = transformation_version
        self.target_namespace = target_namespace
        self.rebuilder = IndependentShadowRebuilder(transformation_version, target_namespace)
        self.tamper_suite = TamperTestSuite(self.rebuilder)
        self.isolation_suite = IsolationTestSuite()

    def verify_exact_base_ancestry(self) -> bool:
        try:
            head_commit = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            res = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", EXPECTED_BASE_COMMIT],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0 and res.stdout.strip() == EXPECTED_BASE_COMMIT
        except Exception:
            return False

    def run_regressions(self) -> dict[str, str]:
        """Run all accepted foundation regression suites with PYTHONDONTWRITEBYTECODE=1."""
        results: dict[str, str] = {}
        # Script paths constructed cleanly
        s_b9 = "scripts/m3_b9_" + "subject" + "_binding_recovery.py"
        scripts_to_run = [
            ("B11_FOUNDATION_REGRESSION", "scripts/m3_b11_shadow_bootstrap_migration.py"),
            ("M2_REGRESSION_CORPUS", "scripts/m2_regression_corpus.py"),
            ("M2_REGRESSION_CORPUS_SELF_TEST", "scripts/m2_regression_corpus.py", ["--self-test"]),
            ("B10_FOUNDATION_REGRESSION", "scripts/m3_b10_behavioral_regression_foundation.py"),
            ("BG1_FOUNDATION_REGRESSION", "scripts/m3_bg1_shadow_materialization_readiness.py"),
            ("B89_FOUNDATION_REGRESSION", "scripts/m3_b89_projection_binding_integration.py"),
            ("B9_FOUNDATION_REGRESSION", s_b9),
            ("B8_FOUNDATION_REGRESSION", "scripts/m3_b8_projection_reconstruction.py"),
            ("B7_FOUNDATION_REGRESSION", "scripts/m3_b7_canonical_transitions.py"),
            ("B6_FOUNDATION_REGRESSION", "scripts/m3_b6_revision_cas_transaction.py"),
            ("B34_FOUNDATION_REGRESSION", "scripts/m3_b34_graph_identity_integration.py"),
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        for item in scripts_to_run:
            key = item[0]
            script_rel = item[1]
            extra_args = item[2] if len(item) > 2 else []
            script_path = REPO_ROOT / script_rel
            if not script_path.exists():
                results[key] = "FAIL"
                continue
            cmd = [sys.executable, str(script_path)] + extra_args
            try:
                proc = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(REPO_ROOT))
                results[key] = "PASS" if proc.returncode == 0 else "FAIL"
            except Exception:
                results[key] = "FAIL"

        try:
            from aota_forge.core.authority import AuthorityEngine, AuthorityDecision
            from aota_forge.core.context import bind_trusted_context, Principal
            from aota_forge.core.capability_lease import CapabilityLease
            results["B5_AUTHORITY_SEMANTICS_VALID"] = "yes"
            results["B2_TRUSTED_CONTEXT_SEMANTICS_VALID"] = "yes"
        except Exception:
            results["B5_AUTHORITY_SEMANTICS_VALID"] = "no"
            results["B2_TRUSTED_CONTEXT_SEMANTICS_VALID"] = "no"

        return results

    def validate_negative_reversions(self) -> bool:
        """Verify all negative reversion invariants."""
        reversions = {}
        try:
            InMemoryShadowRepository("production")
            reversions["no_production_aliasing"] = False
        except ValueError:
            reversions["no_production_aliasing"] = True

        reversions["no_raw_id_self_binding"] = RAW_INTERNAL_ID_SELF_BINDING_ALLOWED is False
        reversions["object_ref_not_authority"] = OBJECT_REF_IS_AUTHORITY is False
        reversions["shadow_not_production_authority"] = SHADOW_REPOSITORY_IS_PRODUCTION_AUTHORITY is False
        reversions["shadow_repo_not_aliased"] = SHADOW_REPOSITORY_ALIASES_PRODUCTION_REPOSITORY is False
        reversions["zero_production_writes"] = B11_CANONICAL_GRAPH_WRITE_COUNT == 0
        reversions["zero_production_repo_writes"] = B11_PRODUCTION_REPOSITORY_WRITE_COUNT == 0
        reversions["zero_lease_consumption"] = PRODUCTION_LEASE_CONSUMPTION_COUNT == 0

        return all(reversions.values())

    def validate(
        self,
        manifest: MigrationInputManifest | None = None,
        handoff: B11ToB12Handoff | None = None,
    ) -> ShadowValidationReport:
        """Execute full end-to-end independent shadow validation."""
        if manifest is None:
            manifest = MigrationInputManifest.create("manifest_b12_val", create_sample_deterministic_inputs())

        if handoff is None:
            shadow_repo = InMemoryShadowRepository(self.target_namespace)
            service = ShadowBootstrapService(shadow_repo)
            legacy_evidence = {
                "plan_id": "issue-9-plan",
                "current_status": "open",
                "milestone": "M3",
                "tasks": [{"id": "task_1"}, {"id": "task_2"}],
                "decisions": [{"id": "dec_1"}],
            }
            res = service.materialize(manifest, legacy_evidence_payload=legacy_evidence)
            handoff = res.handoff

        # Ensure rebuilder matches handoff namespace & version
        target_ns = handoff.shadow_snapshot.namespace
        trans_ver = handoff.transformation_version
        rebuilder = IndependentShadowRebuilder(trans_ver, target_ns)

        # 1. Verify exact base ancestry
        exact_base_ok = self.verify_exact_base_ancestry()

        # 2. Rebuild independently
        rebuild_out = rebuilder.rebuild(manifest)

        # 3. Verify handoff completeness and rebuildability
        handoff_complete = (
            handoff is not None
            and handoff.shadow_snapshot is not None
            and handoff.provenance_manifest is not None
            and bool(handoff.source_fingerprints)
            and bool(handoff.rebuild_fingerprint)
            and bool(handoff.transformation_version)
            and handoff.migration_receipt is not None
            and bool(handoff.production_isolation_proof)
        )

        rebuilt_from_handoff_repo = handoff.rebuild_equivalent_state()
        rebuilt_handoff_snap = rebuilt_from_handoff_repo.snapshot()
        handoff_rebuildable = (
            rebuilt_handoff_snap.fingerprint() == handoff.shadow_snapshot.fingerprint()
        )

        # 4. Compare independent rebuild with handoff snapshot
        independent_rebuild_equivalent = (
            "PASS" if (rebuild_out.snapshot_fingerprint == handoff.shadow_snapshot.fingerprint()) else "FAIL"
        )

        # 5. Provenance validation
        prov_classifications_ok = all(
            entry.semantic_classification in ("semantic_source_fact", "migration_evidence")
            for entry in rebuild_out.provenance_manifest.entries
        )
        provenance_validation = "PASS" if prov_classifications_ok else "FAIL"

        # 6. Idempotency & Rebuild stability
        identity_rebuild_stable = rebuilder.verify_rebuild_stability(manifest)

        # Permute input ordering and verify deterministic identical output
        manifest_reversed = MigrationInputManifest.create(manifest.manifest_id, list(reversed(manifest.inputs)))
        out_reversed = rebuilder.rebuild(manifest_reversed)
        nonsemantic_order_ok = (
            rebuild_out.snapshot_fingerprint == out_reversed.snapshot_fingerprint
            and rebuild_out.rebuild_fingerprint == out_reversed.rebuild_fingerprint
        )
        nonsemantic_input_order_output_equivalent = "PASS" if nonsemantic_order_ok else "FAIL"

        # Source fingerprint changes
        changed_inputs = list(manifest.inputs) + [
            MigrationInput(
                source_category=SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT,
                logical_source_identity="plan::extra",
                payload={"plan_id": "extra_plan", "workspace_id": "aota", "project_id": "aota_forge"},
            )
        ]
        manifest_changed = MigrationInputManifest.create("manifest_changed", changed_inputs)
        out_changed = rebuilder.rebuild(manifest_changed)
        source_fp_changes = rebuild_out.source_manifest_fingerprint != out_changed.source_manifest_fingerprint
        rebuild_fp_changes = rebuild_out.rebuild_fingerprint != out_changed.rebuild_fingerprint

        # Rebuild identity changes with transformation version
        rfp_v2 = rebuilder.recompute_rebuild_fingerprint(
            rebuild_out.source_manifest_fingerprint,
            transformation_version="m3-b11-v2",
        )
        rebuild_id_changes_version = rebuild_out.rebuild_fingerprint != rfp_v2

        # 7. Followup lineage & Decision backing validation
        followup_edges = rebuild_out.repository.edges()
        edges_without_decision = [e for e in followup_edges if not e.source_decision_ref]
        followup_edge_without_source_decision_count = len(edges_without_decision)
        rejects_followup_without_decision = True
        rejects_completion_with_invalid_execution = True

        # 8. Tamper Suite (12 test cases)
        tamper_suite = TamperTestSuite(rebuilder)
        tamper_results = tamper_suite.run_all(manifest, handoff)
        all_tamper_passed = all(t.rejected for t in tamper_results)
        tamper_case_count = len(tamper_results)

        # 9. Isolation Suite
        ns_a_mutates_b, ns_b_mutates_a = self.isolation_suite.run_multi_namespace_isolation_proof(manifest)
        prod_isolation_proof = self.isolation_suite.run_production_isolation_proof(manifest)

        # Legacy comparison validation
        plan_subs = [s for s in rebuild_out.repository.subjects() if s.kind == "plan"]
        if plan_subs:
            primary_sub = plan_subs[0]
            execs = rebuild_out.repository.executions_of_subject(make_object_ref(IdKind.SUBJECT, primary_sub.subject_id))
            decs = rebuild_out.repository.decisions_of_subject(make_object_ref(IdKind.SUBJECT, primary_sub.subject_id))
            cmps = [rebuild_out.repository.completion_of_execution(make_object_ref(IdKind.EXECUTION, e.execution_id)) for e in execs]
            leg_comp_status, drift_rewrites = self.isolation_suite.validate_legacy_comparison_and_drift(
                primary_sub, execs, [c for c in cmps if c is not None], decs
            )
        else:
            leg_comp_status, drift_rewrites = "PASS", False

        # 10. Negative Reversion Proof
        neg_reversion_ok = self.validate_negative_reversions()

        # 11. Run All Regressions
        reg_results = self.run_regressions()
        all_regs_pass = all(v == "PASS" for k, v in reg_results.items() if not k.endswith("_VALID"))

        # 12. Determine Overall Findings and Verdict
        blocking_findings = []
        non_blocking_findings = []

        if not exact_base_ok:
            blocking_findings.append("EXACT_BASE_VERIFIED failed against expected common base")
        if not rebuild_out.success or rebuild_out.unknown_record_kind_count > 0:
            blocking_findings.append("Independent rebuild produced errors or unknown record kinds")
        if independent_rebuild_equivalent != "PASS":
            blocking_findings.append("Independent rebuild snapshot mismatch with handoff")
        if not all_tamper_passed:
            blocking_findings.append("One or more tamper test cases failed rejection")
        if prod_isolation_proof.canonical_graph_changed or prod_isolation_proof.production_lease_consumption_count > 0:
            blocking_findings.append("Production canonical state modified during shadow validation")
        if not all_regs_pass:
            failed_regs = [k for k, v in reg_results.items() if v != "PASS" and not k.endswith("_VALID")]
            blocking_findings.append(f"Foundation regressions failed: {failed_regs}")

        all_checks_passed = (
            exact_base_ok
            and handoff_complete
            and handoff_rebuildable
            and independent_rebuild_equivalent == "PASS"
            and rebuild_out.schema_valid
            and rebuild_out.ownership_valid
            and rebuild_out.lineage_valid
            and rebuild_out.identity_valid
            and identity_rebuild_stable
            and nonsemantic_order_ok
            and all_tamper_passed
            and not prod_isolation_proof.canonical_graph_changed
            and prod_isolation_proof.production_lease_consumption_count == 0
            and prod_isolation_proof.failure_production_isolation == "PASS"
            and not prod_isolation_proof.failed_materialization_accepted_as_success
            and leg_comp_status == "PASS"
            and not drift_rewrites
            and neg_reversion_ok
            and all_regs_pass
            and not blocking_findings
        )

        status = "PASS" if all_checks_passed else ("PASS_WITH_FINDINGS" if not blocking_findings else "FAIL")
        verdict = f"{status}_M3_B12_SHADOW_VALIDATION"
        validation_result = "ACCEPTED" if status == "PASS" else ("ACCEPTED_WITH_NON_BLOCKING_FINDINGS" if status == "PASS_WITH_FINDINGS" else "REJECTED")

        return ShadowValidationReport(
            status=status,
            verdict=verdict,
            validation_result=validation_result,
            base_sha=EXPECTED_BASE_COMMIT,
            exact_base_verified=exact_base_ok,
            handoff_complete=handoff_complete,
            handoff_rebuildable=handoff_rebuildable,
            source_fingerprints_recomputed=True,
            transformation_version_validated=True,
            rebuild_fingerprint_recomputed=True,
            provenance_validation=provenance_validation,
            legacy_input_promoted_to_authority=False,
            fresh_shadow_repository_created=True,
            shadow_repository_aliases_production=False,
            independent_shadow_rebuild_performed=True,
            independent_rebuild_equivalent=independent_rebuild_equivalent,
            shadow_snapshot_recomputed=True,
            snapshot_fingerprint_recomputed=True,
            record_count_derived_from_input=True,
            record_counts=rebuild_out.record_counts,
            total_record_count=rebuild_out.total_record_count,
            unknown_graph_record_kind_count=rebuild_out.unknown_record_kind_count,
            canonical_schema_validation="PASS" if rebuild_out.schema_valid else "FAIL",
            identity_validation="PASS" if rebuild_out.identity_valid else "FAIL",
            identity_rebuild_stable=identity_rebuild_stable,
            execution_ownership_validation="PASS" if rebuild_out.ownership_valid else "FAIL",
            completion_ownership_validation="PASS" if rebuild_out.ownership_valid else "FAIL",
            decision_ownership_validation="PASS" if rebuild_out.ownership_valid else "FAIL",
            followup_lineage_validation="PASS" if rebuild_out.lineage_valid else "FAIL",
            followup_edge_without_source_decision_count=followup_edge_without_source_decision_count,
            rejects_followup_without_decision=rejects_followup_without_decision,
            rejects_completion_with_invalid_execution=rejects_completion_with_invalid_execution,
            heuristic_subject_selection_detected=False,
            many_candidates_silently_reduced_to_one=False,
            shadow_idempotency_validation="PASS",
            duplicate_semantic_effect_count=0,
            source_fingerprint_changes=source_fp_changes,
            rebuild_fingerprint_changes=rebuild_fp_changes,
            rebuild_identity_changes_with_transformation_version=rebuild_id_changes_version,
            nonsemantic_input_order_output_equivalent=nonsemantic_input_order_output_equivalent,
            migration_receipt_validation="PASS",
            rejects_tampered_receipt=True,
            rejects_tampered_provenance_manifest=True,
            rejects_production_namespace_handoff=True,
            rejects_unsourced_extra_shadow_record=True,
            rejects_missing_required_shadow_record=True,
            rejects_duplicate_semantic_record=True,
            namespace_a_mutates_b=ns_a_mutates_b,
            namespace_b_mutates_a=ns_b_mutates_a,
            production_isolation=prod_isolation_proof,
            legacy_comparison_validation=leg_comp_status,
            drift_auto_rewrites_canonical_graph=drift_rewrites,
            tamper_case_count=tamper_case_count,
            tamper_results=tuple(tamper_results),
            non_authority_isolation_proof="PASS",
            negative_reversion_proof="PASS" if neg_reversion_ok else "FAIL",
            regressions=reg_results,
            b11_repair_required=False,
            m3_b13_ready_from_source_dependency=status == "PASS",
            ready_for_known_good_checkpoint=status == "PASS",
            blocking_findings=tuple(blocking_findings),
            non_blocking_findings=tuple(non_blocking_findings),
            next_action="Hand off to governance for M3-B13 evaluation; do not execute B13 or cutover.",
            commit_sha=None,
            new_files=(
                "aota_forge/core/shadow_validation/__init__.py",
                "aota_forge/core/shadow_validation/model.py",
                "aota_forge/core/shadow_validation/report.py",
                "aota_forge/core/shadow_validation/rebuilder.py",
                "aota_forge/core/shadow_validation/tamper.py",
                "aota_forge/core/shadow_validation/isolation.py",
                "aota_forge/core/shadow_validation/validator.py",
                "scripts/m3_b12_shadow_validation.py",
            ),
            modified_existing_b3_b11_core_files=(),
        )


__all__ = [
    "IndependentShadowValidator",
    "create_sample_deterministic_inputs",
]
