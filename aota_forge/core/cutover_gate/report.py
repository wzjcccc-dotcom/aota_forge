"""M3-BG2 Typed Cutover Gate Report Model & Renderer.

Scope (Issue #9, Gate M3-BG2):
- Structured typed report representing complete authority cutover readiness gate evaluation.
- Renders the exact mandatory result block format with verified field names and values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from aota_forge.core.cutover_gate.candidate import (
    CANDIDATE_DEPENDS_ON_CURRENT_POINTER,
    CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER,
    CANDIDATE_IS_CUTOVER_AUTHORIZATION,
    FUTURE_PACKAGE_IS_AUTHORIZATION,
    ExactCutoverCandidate,
    FutureAuthorizationPackage,
)
from aota_forge.core.cutover_gate.model import (
    AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
    BG2_GATE_DECISION_MODEL_IMPLEMENTED,
    BG2_GATE_REPORT_IMPLEMENTED,
    BG2_IS_CUTOVER_EXECUTION,
    BG2_IS_GATE,
    BG2_PRODUCTION_BINDING_WRITE_COUNT,
    BG2_PRODUCTION_GRAPH_WRITE_COUNT,
    BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT,
    BG2_PRODUCTION_REVISION_WRITE_COUNT,
    CUTOVER_AUTHORIZED,
    CUTOVER_COMPLETED,
    CUTOVER_PERFORMED,
    DEPLOY_PERFORMED,
    GITHUB_MUTATION_PERFORMED,
    LIVE_PRODUCTION_CUTOVER_PERFORMED,
    M3_B14_EXECUTION_AUTHORIZED,
    PRODUCTION_GRAPH_AUTHORITY_ACTIVE,
    PUSH_PERFORMED,
    CutoverEligibilityProof,
    EligibilityInputMode,
    GateDecision,
)
from aota_forge.core.cutover_gate.review import (
    FailureCaseResult,
    ReviewCheckResult,
)


@dataclass
class CutoverGateReport:
    """Comprehensive typed report for M3-BG2 Authority-Cutover Readiness Gate."""

    status: str
    base_sha: str
    exact_base_verified: bool
    commit_sha: str
    new_files: tuple[str, ...]
    modified_existing_b3_b13_core_files: tuple[str, ...]
    b13_to_bg2_handoff_complete: bool
    b13_mechanics_revalidation: str
    request_model_review: str
    exact_source_authority_required: bool
    source_authority_inferred_from_current_pointer: bool
    exact_target_authority_required: bool
    target_selected_by_heuristic: bool
    b12_validation_reference_verified: bool
    unvalidated_target_accepted: bool
    target_fingerprint_revalidated: bool
    rejects_target_drift_after_validation: bool
    expected_source_revision_present: bool
    revision_cas_review: str
    stale_revision_accepted: bool
    lost_update_protection: str
    unresolved_semantic_ambiguity_present: bool
    can_override_needs_semantic_choice: bool
    authorization_separation_review: str
    unauthorized_activation_rejected: bool
    invalid_authorization_rejected: bool
    expired_or_revoked_authorization_rejected: bool
    authority_switch_atomicity_review: str
    precommit_failure_preserves_old_authority: bool
    staged_failure_partial_switch: bool
    postcommit_failure_model_review: str
    silent_postcommit_rollback_accepted: bool
    abort_compensation_review: str
    identical_replay_duplicate_effect: bool
    replay_conflict_review: str
    receipt_schema_review: str
    accepts_receipt_as_authorization: bool
    failure_matrix_case_count: int
    negative_reversion_proof: str
    eligibility_input_mode: str
    live_production_candidate_verified: bool
    exact_cutover_candidate_model_implemented: bool
    cutover_candidate_complete: bool
    candidate_fingerprint_deterministic: bool
    candidate_is_cutover_authorization: bool
    candidate_depends_on_current_pointer: bool
    candidate_depends_on_filesystem_order: bool
    cutover_eligibility_proof: str
    additional_live_binding_required_before_actual_cutover_authorization: bool
    future_authorization_package_implemented: bool
    future_authorization_package_is_authorization: bool
    production_graph_write_count: int
    production_binding_write_count: int
    production_revision_write_count: int
    production_lease_consumption_count: int
    regressions: dict[str, str]
    b13_repair_required: bool
    gate_decision: str
    ready_for_separate_cutover_authorization_governance: bool
    ready_for_known_good_checkpoint: bool
    blocking_findings: tuple[str, ...] = ()
    non_blocking_findings: tuple[str, ...] = ()
    verdict: str = ""
    next_action: str = ""
    candidate_details: dict[str, Any] = field(default_factory=dict)
    future_package_details: dict[str, Any] = field(default_factory=dict)
    review_results: list[ReviewCheckResult] = field(default_factory=list)
    failure_results: list[FailureCaseResult] = field(default_factory=list)

    def render_result_block(self) -> str:
        """Render the exact mandatory M3-BG2 result block."""
        lines = [
            "ISSUE_9_M3_BG2_AUTHORITY_CUTOVER_READINESS_GATE_RESULT",
            f"STATUS={self.status}",
            f"BASE_SHA={self.base_sha}",
            f"EXACT_BASE_VERIFIED={'yes' if self.exact_base_verified else 'no'}",
            f"M3_BG2_COMMIT={self.commit_sha if self.commit_sha else 'none'}",
            "NEW_FILES=",
        ]
        for f in self.new_files:
            lines.append(f"- {f}")

        lines.append("MODIFIED_EXISTING_B3_B13_CORE_FILES=")
        if self.modified_existing_b3_b13_core_files:
            for f in self.modified_existing_b3_b13_core_files:
                lines.append(f"- {f}")
        else:
            lines.append("- none")

        lines.extend([
            f"BG2_IS_GATE={'yes' if BG2_IS_GATE else 'no'}",
            f"BG2_IS_CUTOVER_EXECUTION={'yes' if BG2_IS_CUTOVER_EXECUTION else 'no'}",
            f"BG2_GATE_REPORT_IMPLEMENTED={'yes' if BG2_GATE_REPORT_IMPLEMENTED else 'no'}",
            f"BG2_GATE_DECISION_MODEL_IMPLEMENTED={'yes' if BG2_GATE_DECISION_MODEL_IMPLEMENTED else 'no'}",
            f"B13_TO_BG2_HANDOFF_COMPLETE={'yes' if self.b13_to_bg2_handoff_complete else 'no'}",
            f"BG2_B13_MECHANICS_REVALIDATION={self.b13_mechanics_revalidation}",
            f"BG2_REQUEST_MODEL_REVIEW={self.request_model_review}",
            f"BG2_EXACT_SOURCE_AUTHORITY_REQUIRED={'yes' if self.exact_source_authority_required else 'no'}",
            f"BG2_SOURCE_AUTHORITY_INFERRED_FROM_CURRENT_POINTER={'yes' if self.source_authority_inferred_from_current_pointer else 'no'}",
            f"BG2_EXACT_TARGET_AUTHORITY_REQUIRED={'yes' if self.exact_target_authority_required else 'no'}",
            f"BG2_TARGET_SELECTED_BY_HEURISTIC={'yes' if self.target_selected_by_heuristic else 'no'}",
            f"BG2_B12_VALIDATION_REFERENCE_VERIFIED={'yes' if self.b12_validation_reference_verified else 'no'}",
            f"BG2_UNVALIDATED_TARGET_ACCEPTED={'yes' if self.unvalidated_target_accepted else 'no'}",
            f"BG2_TARGET_FINGERPRINT_REVALIDATED={'yes' if self.target_fingerprint_revalidated else 'no'}",
            f"BG2_REJECTS_TARGET_DRIFT_AFTER_VALIDATION={'yes' if self.rejects_target_drift_after_validation else 'no'}",
            f"BG2_EXPECTED_SOURCE_REVISION_PRESENT={'yes' if self.expected_source_revision_present else 'no'}",
            f"BG2_REVISION_CAS_REVIEW={self.revision_cas_review}",
            f"BG2_STALE_REVISION_ACCEPTED={'yes' if self.stale_revision_accepted else 'no'}",
            f"BG2_LOST_UPDATE_PROTECTION={self.lost_update_protection}",
            f"BG2_UNRESOLVED_SEMANTIC_AMBIGUITY_PRESENT={'yes' if self.unresolved_semantic_ambiguity_present else 'no'}",
            f"BG2_CAN_OVERRIDE_NEEDS_SEMANTIC_CHOICE={'yes' if self.can_override_needs_semantic_choice else 'no'}",
            f"BG2_AUTHORIZATION_SEPARATION_REVIEW={self.authorization_separation_review}",
            f"BG2_UNAUTHORIZED_ACTIVATION_REJECTED={'yes' if self.unauthorized_activation_rejected else 'no'}",
            f"BG2_INVALID_AUTHORIZATION_REJECTED={'yes' if self.invalid_authorization_rejected else 'no'}",
            f"BG2_EXPIRED_OR_REVOKED_AUTHORIZATION_REJECTED={'yes' if self.expired_or_revoked_authorization_rejected else 'no'}",
            f"BG2_AUTHORITY_SWITCH_ATOMICITY_REVIEW={self.authority_switch_atomicity_review}",
            f"BG2_PRECOMMIT_FAILURE_PRESERVES_OLD_AUTHORITY={'yes' if self.precommit_failure_preserves_old_authority else 'no'}",
            f"BG2_STAGED_FAILURE_PARTIAL_SWITCH={'yes' if self.staged_failure_partial_switch else 'no'}",
            f"BG2_POSTCOMMIT_FAILURE_MODEL_REVIEW={self.postcommit_failure_model_review}",
            f"BG2_SILENT_POSTCOMMIT_ROLLBACK_ACCEPTED={'yes' if self.silent_postcommit_rollback_accepted else 'no'}",
            f"BG2_ABORT_COMPENSATION_REVIEW={self.abort_compensation_review}",
            f"BG2_IDENTICAL_REPLAY_DUPLICATE_EFFECT={'yes' if self.identical_replay_duplicate_effect else 'no'}",
            f"BG2_REPLAY_CONFLICT_REVIEW={self.replay_conflict_review}",
            f"BG2_RECEIPT_SCHEMA_REVIEW={self.receipt_schema_review}",
            f"BG2_ACCEPTS_RECEIPT_AS_AUTHORIZATION={'yes' if self.accepts_receipt_as_authorization else 'no'}",
            f"BG2_FAILURE_MATRIX_CASE_COUNT={self.failure_matrix_case_count}",
            f"BG2_NEGATIVE_REVERSION_PROOF={self.negative_reversion_proof}",
            f"BG2_ELIGIBILITY_INPUT_MODE={self.eligibility_input_mode}",
            f"BG2_LIVE_PRODUCTION_CANDIDATE_VERIFIED={'yes' if self.live_production_candidate_verified else 'no'}",
            f"BG2_EXACT_CUTOVER_CANDIDATE_MODEL_IMPLEMENTED={'yes' if self.exact_cutover_candidate_model_implemented else 'no'}",
            f"BG2_CUTOVER_CANDIDATE_COMPLETE={'yes' if self.cutover_candidate_complete else 'no'}",
            f"BG2_CANDIDATE_FINGERPRINT_DETERMINISTIC={'yes' if self.candidate_fingerprint_deterministic else 'no'}",
            f"BG2_CANDIDATE_IS_CUTOVER_AUTHORIZATION={'yes' if self.candidate_is_cutover_authorization else 'no'}",
            f"BG2_CANDIDATE_DEPENDS_ON_CURRENT_POINTER={'yes' if self.candidate_depends_on_current_pointer else 'no'}",
            f"BG2_CANDIDATE_DEPENDS_ON_FILESYSTEM_ORDER={'yes' if self.candidate_depends_on_filesystem_order else 'no'}",
            f"BG2_CUTOVER_ELIGIBILITY_PROOF={self.cutover_eligibility_proof}",
            f"BG2_ADDITIONAL_LIVE_BINDING_REQUIRED_BEFORE_ACTUAL_CUTOVER_AUTHORIZATION={'yes' if self.additional_live_binding_required_before_actual_cutover_authorization else 'no'}",
            f"BG2_FUTURE_AUTHORIZATION_PACKAGE_IMPLEMENTED={'yes' if self.future_authorization_package_implemented else 'no'}",
            f"BG2_FUTURE_AUTHORIZATION_PACKAGE_IS_AUTHORIZATION={'yes' if self.future_authorization_package_is_authorization else 'no'}",
            f"BG2_PRODUCTION_GRAPH_WRITE_COUNT={self.production_graph_write_count}",
            f"BG2_PRODUCTION_BINDING_WRITE_COUNT={self.production_binding_write_count}",
            f"BG2_PRODUCTION_REVISION_WRITE_COUNT={self.production_revision_write_count}",
            f"BG2_PRODUCTION_LEASE_CONSUMPTION_COUNT={self.production_lease_consumption_count}",
            f"B13_FOUNDATION_REGRESSION={self.regressions.get('B13_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B12_FOUNDATION_REGRESSION={self.regressions.get('B12_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B11_FOUNDATION_REGRESSION={self.regressions.get('B11_FOUNDATION_REGRESSION', 'FAIL')}",
            f"M2_REGRESSION_CORPUS={self.regressions.get('M2_REGRESSION_CORPUS', 'FAIL')}",
            f"M2_REGRESSION_CORPUS_SELF_TEST={self.regressions.get('M2_REGRESSION_CORPUS_SELF_TEST', 'FAIL')}",
            f"B10_FOUNDATION_REGRESSION={self.regressions.get('B10_FOUNDATION_REGRESSION', 'FAIL')}",
            f"BG1_FOUNDATION_REGRESSION={self.regressions.get('BG1_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B89_FOUNDATION_REGRESSION={self.regressions.get('B89_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B9_FOUNDATION_REGRESSION={self.regressions.get('B9_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B8_FOUNDATION_REGRESSION={self.regressions.get('B8_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B7_FOUNDATION_REGRESSION={self.regressions.get('B7_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B6_FOUNDATION_REGRESSION={self.regressions.get('B6_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B34_FOUNDATION_REGRESSION={self.regressions.get('B34_FOUNDATION_REGRESSION', 'FAIL')}",
            f"B5_AUTHORITY_SEMANTICS_VALID={self.regressions.get('B5_AUTHORITY_SEMANTICS_VALID', 'no')}",
            f"B2_TRUSTED_CONTEXT_SEMANTICS_VALID={self.regressions.get('B2_TRUSTED_CONTEXT_SEMANTICS_VALID', 'no')}",
            f"B13_REPAIR_REQUIRED={'yes' if self.b13_repair_required else 'no'}",
            f"BG2_GATE_DECISION={self.gate_decision}",
            f"BG2_READY_FOR_SEPARATE_CUTOVER_AUTHORIZATION_GOVERNANCE={'yes' if self.ready_for_separate_cutover_authorization_governance else 'no'}",
            f"BG2_READY_FOR_KNOWN_GOOD_CHECKPOINT={'yes' if self.ready_for_known_good_checkpoint else 'no'}",
            f"CUTOVER_AUTHORIZED={'yes' if CUTOVER_AUTHORIZED else 'no'}",
            f"CUTOVER_PERFORMED={'yes' if CUTOVER_PERFORMED else 'no'}",
            f"CUTOVER_COMPLETED={'yes' if CUTOVER_COMPLETED else 'no'}",
            f"PRODUCTION_GRAPH_AUTHORITY_ACTIVE={'yes' if PRODUCTION_GRAPH_AUTHORITY_ACTIVE else 'no'}",
            f"AUTHORITATIVE_GRAPH_WRITES_ALLOWED={'yes' if AUTHORITATIVE_GRAPH_WRITES_ALLOWED else 'no'}",
            f"M3_B14_EXECUTION_AUTHORIZED={'yes' if M3_B14_EXECUTION_AUTHORIZED else 'no'}",
            f"LIVE_PRODUCTION_CUTOVER_PERFORMED={'yes' if LIVE_PRODUCTION_CUTOVER_PERFORMED else 'no'}",
            f"PUSH_PERFORMED={'yes' if PUSH_PERFORMED else 'no'}",
            f"GITHUB_MUTATION_PERFORMED={'yes' if GITHUB_MUTATION_PERFORMED else 'no'}",
            f"DEPLOY_PERFORMED={'yes' if DEPLOY_PERFORMED else 'no'}",
            "BLOCKING_FINDINGS=",
        ])
        if self.blocking_findings:
            for b in self.blocking_findings:
                lines.append(f"- {b}")
        else:
            lines.append("- none")

        lines.append("NON_BLOCKING_FINDINGS=")
        if self.non_blocking_findings:
            for nb in self.non_blocking_findings:
                lines.append(f"- {nb}")
        else:
            lines.append("- none")

        lines.extend([
            f"VERDICT={self.verdict}",
            f"NEXT_ACTION={self.next_action}",
        ])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "gate_decision": self.gate_decision,
            "base_sha": self.base_sha,
            "exact_base_verified": self.exact_base_verified,
            "commit_sha": self.commit_sha,
            "new_files": list(self.new_files),
            "modified_existing_b3_b13_core_files": list(self.modified_existing_b3_b13_core_files),
            "b13_to_bg2_handoff_complete": self.b13_to_bg2_handoff_complete,
            "b13_mechanics_revalidation": self.b13_mechanics_revalidation,
            "request_model_review": self.request_model_review,
            "exact_source_authority_required": self.exact_source_authority_required,
            "exact_target_authority_required": self.exact_target_authority_required,
            "b12_validation_reference_verified": self.b12_validation_reference_verified,
            "target_fingerprint_revalidated": self.target_fingerprint_revalidated,
            "rejects_target_drift_after_validation": self.rejects_target_drift_after_validation,
            "expected_source_revision_present": self.expected_source_revision_present,
            "revision_cas_review": self.revision_cas_review,
            "lost_update_protection": self.lost_update_protection,
            "unresolved_semantic_ambiguity_present": self.unresolved_semantic_ambiguity_present,
            "authorization_separation_review": self.authorization_separation_review,
            "unauthorized_activation_rejected": self.unauthorized_activation_rejected,
            "invalid_authorization_rejected": self.invalid_authorization_rejected,
            "expired_or_revoked_authorization_rejected": self.expired_or_revoked_authorization_rejected,
            "authority_switch_atomicity_review": self.authority_switch_atomicity_review,
            "precommit_failure_preserves_old_authority": self.precommit_failure_preserves_old_authority,
            "staged_failure_partial_switch": self.staged_failure_partial_switch,
            "postcommit_failure_model_review": self.postcommit_failure_model_review,
            "silent_postcommit_rollback_accepted": self.silent_postcommit_rollback_accepted,
            "abort_compensation_review": self.abort_compensation_review,
            "identical_replay_duplicate_effect": self.identical_replay_duplicate_effect,
            "replay_conflict_review": self.replay_conflict_review,
            "receipt_schema_review": self.receipt_schema_review,
            "accepts_receipt_as_authorization": self.accepts_receipt_as_authorization,
            "failure_matrix_case_count": self.failure_matrix_case_count,
            "negative_reversion_proof": self.negative_reversion_proof,
            "eligibility_input_mode": self.eligibility_input_mode,
            "live_production_candidate_verified": self.live_production_candidate_verified,
            "exact_cutover_candidate_model_implemented": self.exact_cutover_candidate_model_implemented,
            "cutover_candidate_complete": self.cutover_candidate_complete,
            "candidate_fingerprint_deterministic": self.candidate_fingerprint_deterministic,
            "candidate_is_cutover_authorization": self.candidate_is_cutover_authorization,
            "candidate_depends_on_current_pointer": self.candidate_depends_on_current_pointer,
            "candidate_depends_on_filesystem_order": self.candidate_depends_on_filesystem_order,
            "cutover_eligibility_proof": self.cutover_eligibility_proof,
            "additional_live_binding_required_before_actual_cutover_authorization": self.additional_live_binding_required_before_actual_cutover_authorization,
            "future_authorization_package_implemented": self.future_authorization_package_implemented,
            "future_authorization_package_is_authorization": self.future_authorization_package_is_authorization,
            "production_graph_write_count": self.production_graph_write_count,
            "production_binding_write_count": self.production_binding_write_count,
            "production_revision_write_count": self.production_revision_write_count,
            "production_lease_consumption_count": self.production_lease_consumption_count,
            "regressions": dict(self.regressions),
            "b13_repair_required": self.b13_repair_required,
            "ready_for_separate_cutover_authorization_governance": self.ready_for_separate_cutover_authorization_governance,
            "ready_for_known_good_checkpoint": self.ready_for_known_good_checkpoint,
            "blocking_findings": list(self.blocking_findings),
            "non_blocking_findings": list(self.non_blocking_findings),
            "next_action": self.next_action,
            "candidate": self.candidate_details,
            "future_package": self.future_package_details,
            "reviews": [r.to_dict() for r in self.review_results],
            "failures": [f.to_dict() for f in self.failure_results],
        }


__all__ = [
    "CutoverGateReport",
]
