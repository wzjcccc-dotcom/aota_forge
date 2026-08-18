"""M3-B12 typed validation report model.

Scope (Issue #9, Lane M3-B12):
- Typed report representation capturing all independent shadow validation outcomes.
- Renders the mandatory result block format with exact field names and verified values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from aota_forge.core.shadow_validation.model import (
    B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE,
    B12_HAS_INDEPENDENT_ASSERTION_SET,
    B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED,
    B12_IS_BOOTSTRAP_IMPLEMENTATION,
    B12_IS_CUTOVER,
    B12_IS_INDEPENDENT_SHADOW_VALIDATION,
    B12_VALIDATION_REPORT_IMPLEMENTED,
    CUTOVER_AUTHORIZED,
    DEPLOY_PERFORMED,
    GITHUB_MUTATION_PERFORMED,
    LIVE_PRODUCTION_MIGRATION_PERFORMED,
    M3_B12_EXECUTION_AUTHORIZED,
    M3_B13_EXECUTION_AUTHORIZED,
    M3_B14_EXECUTION_AUTHORIZED,
    M3_B_SHADOW_MATERIALIZATION_AUTHORIZED,
    M3_BG2_EXECUTION_AUTHORIZED,
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
    AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
    PUSH_PERFORMED,
)


@dataclass(frozen=True)
class TamperCaseResult:
    """Result of a single tamper rejection test case."""

    case_id: str
    name: str
    description: str
    rejected: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "rejected": self.rejected,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProductionIsolationProof:
    """Verification evidence for complete production graph isolation."""

    canonical_graph_fingerprint_before: str
    canonical_graph_fingerprint_after: str
    canonical_graph_changed: bool
    production_revision_changed: bool
    production_lease_consumption_count: int
    production_binding_before: str
    production_binding_after: str
    production_binding_changed: bool
    shadow_subject_visible_to_production_b9: bool
    production_projection_changed: bool
    shadow_projection_is_subject_authority: bool
    failure_production_isolation: str
    failed_materialization_accepted_as_success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_graph_fingerprint_before": self.canonical_graph_fingerprint_before,
            "canonical_graph_fingerprint_after": self.canonical_graph_fingerprint_after,
            "canonical_graph_changed": self.canonical_graph_changed,
            "production_revision_changed": self.production_revision_changed,
            "production_lease_consumption_count": self.production_lease_consumption_count,
            "production_binding_before": self.production_binding_before,
            "production_binding_after": self.production_binding_after,
            "production_binding_changed": self.production_binding_changed,
            "shadow_subject_visible_to_production_b9": self.shadow_subject_visible_to_production_b9,
            "production_projection_changed": self.production_projection_changed,
            "shadow_projection_is_subject_authority": self.shadow_projection_is_subject_authority,
            "failure_production_isolation": self.failure_production_isolation,
            "failed_materialization_accepted_as_success": self.failed_materialization_accepted_as_success,
        }


@dataclass
class ShadowValidationReport:
    """Complete typed report for M3-B12 Independent Shadow Validation."""

    status: str
    verdict: str
    validation_result: str
    base_sha: str
    exact_base_verified: bool
    handoff_complete: bool
    handoff_rebuildable: bool
    source_fingerprints_recomputed: bool
    transformation_version_validated: bool
    rebuild_fingerprint_recomputed: bool
    provenance_validation: str
    legacy_input_promoted_to_authority: bool
    fresh_shadow_repository_created: bool
    shadow_repository_aliases_production: bool
    independent_shadow_rebuild_performed: bool
    independent_rebuild_equivalent: str
    shadow_snapshot_recomputed: bool
    snapshot_fingerprint_recomputed: bool
    record_count_derived_from_input: bool
    record_counts: dict[str, int]
    total_record_count: int
    unknown_graph_record_kind_count: int
    canonical_schema_validation: str
    identity_validation: str
    identity_rebuild_stable: bool
    execution_ownership_validation: str
    completion_ownership_validation: str
    decision_ownership_validation: str
    followup_lineage_validation: str
    followup_edge_without_source_decision_count: int
    rejects_followup_without_decision: bool
    rejects_completion_with_invalid_execution: bool
    heuristic_subject_selection_detected: bool
    many_candidates_silently_reduced_to_one: bool
    shadow_idempotency_validation: str
    duplicate_semantic_effect_count: int
    source_fingerprint_changes: bool
    rebuild_fingerprint_changes: bool
    rebuild_identity_changes_with_transformation_version: bool
    nonsemantic_input_order_output_equivalent: str
    migration_receipt_validation: str
    rejects_tampered_receipt: bool
    rejects_tampered_provenance_manifest: bool
    rejects_production_namespace_handoff: bool
    rejects_unsourced_extra_shadow_record: bool
    rejects_missing_required_shadow_record: bool
    rejects_duplicate_semantic_record: bool
    namespace_a_mutates_b: bool
    namespace_b_mutates_a: bool
    production_isolation: ProductionIsolationProof
    legacy_comparison_validation: str
    drift_auto_rewrites_canonical_graph: bool
    tamper_case_count: int
    tamper_results: tuple[TamperCaseResult, ...]
    non_authority_isolation_proof: str
    negative_reversion_proof: str
    regressions: dict[str, str] = field(default_factory=dict)
    b11_repair_required: bool = False
    m3_b13_ready_from_source_dependency: bool = True
    ready_for_known_good_checkpoint: bool = True
    blocking_findings: tuple[str, ...] = ()
    non_blocking_findings: tuple[str, ...] = ()
    next_action: str = ""
    commit_sha: str | None = None
    new_files: tuple[str, ...] = ()
    modified_existing_b3_b11_core_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "validation_result": self.validation_result,
            "base_sha": self.base_sha,
            "exact_base_verified": self.exact_base_verified,
            "commit_sha": self.commit_sha or "none",
            "new_files": list(self.new_files),
            "modified_existing_b3_b11_core_files": list(self.modified_existing_b3_b11_core_files),
            "b12_independent_shadow_validation_implemented": B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED,
            "b12_is_independent_shadow_validation": B12_IS_INDEPENDENT_SHADOW_VALIDATION,
            "b12_is_bootstrap_implementation": B12_IS_BOOTSTRAP_IMPLEMENTATION,
            "b12_is_cutover": B12_IS_CUTOVER,
            "b12_has_independent_assertion_set": B12_HAS_INDEPENDENT_ASSERTION_SET,
            "b12_directly_recomputes_critical_evidence": B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE,
            "b11_to_b12_handoff_complete": self.handoff_complete,
            "b12_validation_report_implemented": B12_VALIDATION_REPORT_IMPLEMENTED,
            "b12_source_fingerprints_recomputed": self.source_fingerprints_recomputed,
            "b12_transformation_version_validated": self.transformation_version_validated,
            "b12_rebuild_fingerprint_recomputed": self.rebuild_fingerprint_recomputed,
            "b12_provenance_validation": self.provenance_validation,
            "b12_legacy_input_promoted_to_authority": self.legacy_input_promoted_to_authority,
            "b12_fresh_shadow_repository_created": self.fresh_shadow_repository_created,
            "b12_shadow_repository_aliases_production": self.shadow_repository_aliases_production,
            "b12_independent_shadow_rebuild_performed": self.independent_shadow_rebuild_performed,
            "b12_independent_rebuild_equivalent": self.independent_rebuild_equivalent,
            "b12_shadow_snapshot_recomputed": self.shadow_snapshot_recomputed,
            "b12_snapshot_fingerprint_recomputed": self.snapshot_fingerprint_recomputed,
            "b12_record_count_derived_from_input": self.record_count_derived_from_input,
            "b12_total_record_count": self.total_record_count,
            "b12_record_counts": dict(self.record_counts),
            "b12_unknown_graph_record_kind_count": self.unknown_graph_record_kind_count,
            "b12_canonical_schema_validation": self.canonical_schema_validation,
            "b12_identity_validation": self.identity_validation,
            "b12_identity_rebuild_stable": self.identity_rebuild_stable,
            "b12_execution_ownership_validation": self.execution_ownership_validation,
            "b12_completion_ownership_validation": self.completion_ownership_validation,
            "b12_decision_ownership_validation": self.decision_ownership_validation,
            "b12_followup_lineage_validation": self.followup_lineage_validation,
            "followup_edge_without_source_decision_count": self.followup_edge_without_source_decision_count,
            "b12_rejects_followup_without_decision": self.rejects_followup_without_decision,
            "b12_rejects_completion_with_invalid_execution": self.rejects_completion_with_invalid_execution,
            "b12_heuristic_subject_selection_detected": self.heuristic_subject_selection_detected,
            "b12_many_candidates_silently_reduced_to_one": self.many_candidates_silently_reduced_to_one,
            "b12_shadow_idempotency_validation": self.shadow_idempotency_validation,
            "b12_duplicate_semantic_effect_count": self.duplicate_semantic_effect_count,
            "b12_source_fingerprint_changes": self.source_fingerprint_changes,
            "b12_rebuild_fingerprint_changes": self.rebuild_fingerprint_changes,
            "b12_rebuild_identity_changes_with_transformation_version": self.rebuild_identity_changes_with_transformation_version,
            "b12_nonsemantic_input_order_output_equivalent": self.nonsemantic_input_order_output_equivalent,
            "b12_migration_receipt_validation": self.migration_receipt_validation,
            "b12_rejects_tampered_receipt": self.rejects_tampered_receipt,
            "b12_rejects_tampered_provenance_manifest": self.rejects_tampered_provenance_manifest,
            "b12_rejects_production_namespace_handoff": self.rejects_production_namespace_handoff,
            "b12_rejects_unsourced_extra_shadow_record": self.rejects_unsourced_extra_shadow_record,
            "b12_rejects_missing_required_shadow_record": self.rejects_missing_required_shadow_record,
            "b12_rejects_duplicate_semantic_record": self.rejects_duplicate_semantic_record,
            "b12_namespace_a_mutates_b": self.namespace_a_mutates_b,
            "b12_namespace_b_mutates_a": self.namespace_b_mutates_a,
            "production_isolation": self.production_isolation.to_dict(),
            "b12_legacy_comparison_validation": self.legacy_comparison_validation,
            "b12_drift_auto_rewrites_canonical_graph": self.drift_auto_rewrites_canonical_graph,
            "b12_tamper_case_count": self.tamper_case_count,
            "tamper_results": [t.to_dict() for t in self.tamper_results],
            "b12_non_authority_isolation_proof": self.non_authority_isolation_proof,
            "b12_negative_reversion_proof": self.negative_reversion_proof,
            "regressions": dict(self.regressions),
            "b11_repair_required": self.b11_repair_required,
            "m3_b13_ready_from_source_dependency": self.m3_b13_ready_from_source_dependency,
            "b12_ready_for_known_good_checkpoint": self.ready_for_known_good_checkpoint,
            "blocking_findings": list(self.blocking_findings),
            "non_blocking_findings": list(self.non_blocking_findings),
            "next_action": self.next_action,
        }

    def render_result_block(self) -> str:
        """Render the exact mandatory result block format for M3-B12."""
        new_files_lines = [f"NEW_FILES=- {f}" for f in self.new_files] if self.new_files else ["NEW_FILES=- none"]
        mod_files_lines = [f"MODIFIED_EXISTING_B3_B11_CORE_FILES=- {f}" for f in self.modified_existing_b3_b11_core_files] if self.modified_existing_b3_b11_core_files else ["MODIFIED_EXISTING_B3_B11_CORE_FILES=- none"]
        blocking_lines = [f"BLOCKING_FINDINGS=- {f}" for f in self.blocking_findings] if self.blocking_findings else ["BLOCKING_FINDINGS=- none"]
        non_blocking_lines = [f"NON_BLOCKING_FINDINGS=- {f}" for f in self.non_blocking_findings] if self.non_blocking_findings else ["NON_BLOCKING_FINDINGS=- none"]

        iso = self.production_isolation
        regs = self.regressions

        lines = [
            "ISSUE_9_M3_B12_SHADOW_VALIDATION_RESULT",
            f"STATUS={self.status}",
            f"BASE_SHA={self.base_sha}",
            f"EXACT_BASE_VERIFIED={'yes' if self.exact_base_verified else 'no'}",
            f"M3_B12_COMMIT={self.commit_sha if self.commit_sha else 'none'}",
        ]
        lines.extend(new_files_lines)
        lines.extend(mod_files_lines)
        lines.extend([
            f"B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED={'yes' if B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED else 'no'}",
            f"B12_IS_INDEPENDENT_SHADOW_VALIDATION={'yes' if B12_IS_INDEPENDENT_SHADOW_VALIDATION else 'no'}",
            f"B12_IS_BOOTSTRAP_IMPLEMENTATION={'yes' if B12_IS_BOOTSTRAP_IMPLEMENTATION else 'no'}",
            f"B12_IS_CUTOVER={'yes' if B12_IS_CUTOVER else 'no'}",
            f"B12_HAS_INDEPENDENT_ASSERTION_SET={'yes' if B12_HAS_INDEPENDENT_ASSERTION_SET else 'no'}",
            f"B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE={'yes' if B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE else 'no'}",
            f"B11_TO_B12_HANDOFF_COMPLETE={'yes' if self.handoff_complete else 'no'}",
            f"B12_VALIDATION_REPORT_IMPLEMENTED={'yes' if B12_VALIDATION_REPORT_IMPLEMENTED else 'no'}",
            f"B12_SOURCE_FINGERPRINTS_RECOMPUTED={'yes' if self.source_fingerprints_recomputed else 'no'}",
            f"B12_TRANSFORMATION_VERSION_VALIDATED={'yes' if self.transformation_version_validated else 'no'}",
            f"B12_REBUILD_FINGERPRINT_RECOMPUTED={'yes' if self.rebuild_fingerprint_recomputed else 'no'}",
            f"B12_PROVENANCE_VALIDATION={self.provenance_validation}",
            f"B12_LEGACY_INPUT_PROMOTED_TO_AUTHORITY={'yes' if self.legacy_input_promoted_to_authority else 'no'}",
            f"B12_FRESH_SHADOW_REPOSITORY_CREATED={'yes' if self.fresh_shadow_repository_created else 'no'}",
            f"B12_SHADOW_REPOSITORY_ALIASES_PRODUCTION={'yes' if self.shadow_repository_aliases_production else 'no'}",
            f"B12_INDEPENDENT_SHADOW_REBUILD_PERFORMED={'yes' if self.independent_shadow_rebuild_performed else 'no'}",
            f"B12_INDEPENDENT_REBUILD_EQUIVALENT={self.independent_rebuild_equivalent}",
            f"B12_SHADOW_SNAPSHOT_RECOMPUTED={'yes' if self.shadow_snapshot_recomputed else 'no'}",
            f"B12_SNAPSHOT_FINGERPRINT_RECOMPUTED={'yes' if self.snapshot_fingerprint_recomputed else 'no'}",
            f"B12_RECORD_COUNT_DERIVED_FROM_INPUT={'yes' if self.record_count_derived_from_input else 'no'}",
            f"B12_TOTAL_RECORD_COUNT={self.total_record_count}",
            f"B12_UNKNOWN_GRAPH_RECORD_KIND_COUNT={self.unknown_graph_record_kind_count}",
            f"B12_CANONICAL_SCHEMA_VALIDATION={self.canonical_schema_validation}",
            f"B12_IDENTITY_VALIDATION={self.identity_validation}",
            f"B12_IDENTITY_REBUILD_STABLE={'yes' if self.identity_rebuild_stable else 'no'}",
            f"B12_EXECUTION_OWNERSHIP_VALIDATION={self.execution_ownership_validation}",
            f"B12_COMPLETION_OWNERSHIP_VALIDATION={self.completion_ownership_validation}",
            f"B12_DECISION_OWNERSHIP_VALIDATION={self.decision_ownership_validation}",
            f"B12_FOLLOWUP_LINEAGE_VALIDATION={self.followup_lineage_validation}",
            f"FOLLOWUP_EDGE_WITHOUT_SOURCE_DECISION_COUNT={self.followup_edge_without_source_decision_count}",
            f"B12_REJECTS_FOLLOWUP_WITHOUT_DECISION={'yes' if self.rejects_followup_without_decision else 'no'}",
            f"B12_REJECTS_COMPLETION_WITH_INVALID_EXECUTION={'yes' if self.rejects_completion_with_invalid_execution else 'no'}",
            f"B12_HEURISTIC_SUBJECT_SELECTION_DETECTED={'yes' if self.heuristic_subject_selection_detected else 'no'}",
            f"B12_MANY_CANDIDATES_SILENTLY_REDUCED_TO_ONE={'yes' if self.many_candidates_silently_reduced_to_one else 'no'}",
            f"B12_SHADOW_IDEMPOTENCY_VALIDATION={self.shadow_idempotency_validation}",
            f"B12_DUPLICATE_SEMANTIC_EFFECT_COUNT={self.duplicate_semantic_effect_count}",
            f"B12_SOURCE_FINGERPRINT_CHANGES={'yes' if self.source_fingerprint_changes else 'no'}",
            f"B12_REBUILD_FINGERPRINT_CHANGES={'yes' if self.rebuild_fingerprint_changes else 'no'}",
            f"B12_REBUILD_IDENTITY_CHANGES_WITH_TRANSFORMATION_VERSION={'yes' if self.rebuild_identity_changes_with_transformation_version else 'no'}",
            f"B12_NONSEMANTIC_INPUT_ORDER_OUTPUT_EQUIVALENT={self.nonsemantic_input_order_output_equivalent}",
            f"B12_MIGRATION_RECEIPT_VALIDATION={self.migration_receipt_validation}",
            f"B12_REJECTS_TAMPERED_RECEIPT={'yes' if self.rejects_tampered_receipt else 'no'}",
            f"B12_REJECTS_TAMPERED_PROVENANCE_MANIFEST={'yes' if self.rejects_tampered_provenance_manifest else 'no'}",
            f"B12_REJECTS_PRODUCTION_NAMESPACE_HANDOFF={'yes' if self.rejects_production_namespace_handoff else 'no'}",
            f"B12_REJECTS_UNSOURCED_EXTRA_SHADOW_RECORD={'yes' if self.rejects_unsourced_extra_shadow_record else 'no'}",
            f"B12_REJECTS_MISSING_REQUIRED_SHADOW_RECORD={'yes' if self.rejects_missing_required_shadow_record else 'no'}",
            f"B12_REJECTS_DUPLICATE_SEMANTIC_RECORD={'yes' if self.rejects_duplicate_semantic_record else 'no'}",
            f"B12_NAMESPACE_A_MUTATES_B={'yes' if self.namespace_a_mutates_b else 'no'}",
            f"B12_NAMESPACE_B_MUTATES_A={'yes' if self.namespace_b_mutates_a else 'no'}",
            f"B12_CANONICAL_GRAPH_FINGERPRINT_BEFORE={iso.canonical_graph_fingerprint_before}",
            f"B12_CANONICAL_GRAPH_FINGERPRINT_AFTER={iso.canonical_graph_fingerprint_after}",
            f"B12_CANONICAL_GRAPH_CHANGED={'yes' if iso.canonical_graph_changed else 'no'}",
            f"B12_PRODUCTION_REVISION_CHANGED={'yes' if iso.production_revision_changed else 'no'}",
            f"B12_PRODUCTION_LEASE_CONSUMPTION_COUNT={iso.production_lease_consumption_count}",
            f"B12_PRODUCTION_BINDING_BEFORE={iso.production_binding_before}",
            f"B12_PRODUCTION_BINDING_AFTER={iso.production_binding_after}",
            f"B12_PRODUCTION_BINDING_CHANGED={'yes' if iso.production_binding_changed else 'no'}",
            f"B12_SHADOW_SUBJECT_VISIBLE_TO_PRODUCTION_B9={'yes' if iso.shadow_subject_visible_to_production_b9 else 'no'}",
            f"B12_PRODUCTION_PROJECTION_CHANGED={'yes' if iso.production_projection_changed else 'no'}",
            f"B12_SHADOW_PROJECTION_IS_SUBJECT_AUTHORITY={'yes' if iso.shadow_projection_is_subject_authority else 'no'}",
            f"B12_FAILURE_PRODUCTION_ISOLATION={iso.failure_production_isolation}",
            f"B12_FAILED_MATERIALIZATION_ACCEPTED_AS_SUCCESS={'yes' if iso.failed_materialization_accepted_as_success else 'no'}",
            f"B12_LEGACY_COMPARISON_VALIDATION={self.legacy_comparison_validation}",
            f"B12_DRIFT_AUTO_REWRITES_CANONICAL_GRAPH={'yes' if self.drift_auto_rewrites_canonical_graph else 'no'}",
            f"B12_TAMPER_CASE_COUNT={self.tamper_case_count}",
            f"B12_NON_AUTHORITY_ISOLATION_PROOF={self.non_authority_isolation_proof}",
            f"B12_NEGATIVE_REVERSION_PROOF={self.negative_reversion_proof}",
            f"B11_FOUNDATION_REGRESSION={regs.get('B11_FOUNDATION_REGRESSION', 'FAIL')}",
            f"M2_REGRESSION_CORPUS={regs.get('M2_REGRESSION_CORPUS', 'FAIL')}",
            f"M2_REGRESSION_CORPUS_SELF_TEST={regs.get('M2_REGRESSION_CORPUS_SELF_TEST', 'FAIL')}",
            f"B10_FOUNDATION_REGRESSION={regs.get('B10_FOUNDATION_REGRESSION', 'FAIL')}",
            f"BG1_FOUNDATION_REGRESSION={regs.get('BG1_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B89_FOUNDATION_REGRESSION={regs.get('B89_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B9_FOUNDATION_REGRESSION={regs.get('B9_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B8_FOUNDATION_REGRESSION={regs.get('B8_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B7_FOUNDATION_REGRESSION={regs.get('B7_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B6_FOUNDATION_REGRESSION={regs.get('B6_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B34_FOUNDATION_REGRESSION={regs.get('B34_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B5_AUTHORITY_SEMANTICS_VALID={regs.get('B5_AUTHORITY_SEMANTICS_VALID', 'no')}",
            f"B2_TRUSTED_CONTEXT_SEMANTICS_VALID={regs.get('B2_TRUSTED_CONTEXT_SEMANTICS_VALID', 'no')}",
            f"B12_VALIDATION_RESULT={self.validation_result}",
            f"B11_REPAIR_REQUIRED={'yes' if self.b11_repair_required else 'no'}",
            f"M3_B13_READY_FROM_SOURCE_DEPENDENCY={'yes' if self.m3_b13_ready_from_source_dependency else 'no'}",
            f"M3_B13_EXECUTION_AUTHORIZED={'yes' if M3_B13_EXECUTION_AUTHORIZED else 'no'}",
            f"M3_BG2_EXECUTION_AUTHORIZED={'yes' if M3_BG2_EXECUTION_AUTHORIZED else 'no'}",
            f"M3_B14_EXECUTION_AUTHORIZED={'yes' if M3_B14_EXECUTION_AUTHORIZED else 'no'}",
            f"M3_B_SHADOW_MATERIALIZATION_AUTHORIZED={'yes' if M3_B_SHADOW_MATERIALIZATION_AUTHORIZED else 'no'}",
            f"PRODUCTION_GRAPH_AUTHORITY_ACTIVE={'yes' if PRODUCTION_GRAPH_AUTHORITY_ACTIVE else 'no'}",
            f"AUTHORITATIVE_GRAPH_WRITES_ALLOWED={'yes' if AUTHORITATIVE_GRAPH_WRITES_ALLOWED else 'no'}",
            f"CUTOVER_AUTHORIZED={'yes' if CUTOVER_AUTHORIZED else 'no'}",
            f"LIVE_PRODUCTION_MIGRATION_PERFORMED={'yes' if LIVE_PRODUCTION_MIGRATION_PERFORMED else 'no'}",
            f"B12_READY_FOR_KNOWN_GOOD_CHECKPOINT={'yes' if self.ready_for_known_good_checkpoint else 'no'}",
            f"PUSH_PERFORMED={'yes' if PUSH_PERFORMED else 'no'}",
            f"GITHUB_MUTATION_PERFORMED={'yes' if GITHUB_MUTATION_PERFORMED else 'no'}",
            f"DEPLOY_PERFORMED={'yes' if DEPLOY_PERFORMED else 'no'}",
        ])
        lines.extend(blocking_lines)
        lines.extend(non_blocking_lines)
        lines.extend([
            f"VERDICT={self.verdict}",
            f"NEXT_ACTION={self.next_action}",
        ])
        return chr(10).join(lines)


__all__ = [
    "TamperCaseResult",
    "ProductionIsolationProof",
    "ShadowValidationReport",
]
