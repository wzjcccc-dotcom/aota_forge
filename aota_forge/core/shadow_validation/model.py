"""M3-B12 Independent Shadow Validation Model & Invariants.

Scope (Issue #9, Lane M3-B12):
- Identity constants and invariant declarations for independent shadow validation.
- Boundary signals preventing live production mutation, cutover, or producer repair.
- Canonical classification mappings and ontology constants.
"""

from __future__ import annotations

from typing import Final

# B12 Required Identity Invariants
B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED: Final[bool] = True
B12_IS_INDEPENDENT_SHADOW_VALIDATION: Final[bool] = True
B12_IS_BOOTSTRAP_IMPLEMENTATION: Final[bool] = False
B12_IS_CUTOVER: Final[bool] = False
B12_HAS_INDEPENDENT_ASSERTION_SET: Final[bool] = True
B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE: Final[bool] = True
B12_VALIDATION_REPORT_IMPLEMENTED: Final[bool] = True
RAW_INTERNAL_ID_SELF_BINDING_ALLOWED: Final[bool] = False
OBJECT_REF_IS_AUTHORITY: Final[bool] = False

# Hard prohibitions & governance guards
M3_B12_EXECUTION_AUTHORIZED: Final[bool] = True
M3_B13_EXECUTION_AUTHORIZED: Final[bool] = False
M3_BG2_EXECUTION_AUTHORIZED: Final[bool] = False
M3_B14_EXECUTION_AUTHORIZED: Final[bool] = False
M3_B_SHADOW_MATERIALIZATION_AUTHORIZED: Final[bool] = True
PRODUCTION_GRAPH_AUTHORITY_ACTIVE: Final[bool] = False
AUTHORITATIVE_GRAPH_WRITES_ALLOWED: Final[bool] = False
CUTOVER_AUTHORIZED: Final[bool] = False
LIVE_PRODUCTION_MIGRATION_PERFORMED: Final[bool] = False
DEPLOY_PERFORMED: Final[bool] = False
RUNTIME_RELOAD_PERFORMED: Final[bool] = False
PRODUCTION_SHADOW_SERVICE_ACTIVATED: Final[bool] = False
PUSH_PERFORMED: Final[bool] = False
GITHUB_MUTATION_PERFORMED: Final[bool] = False

# Base SHA
EXPECTED_BASE_COMMIT: Final[str] = "99b64d64bc194a8e1f58a626518c71c09ee04672"

# Canonical B3 Record Kinds (no new ontology allowed)
CANONICAL_B3_RECORD_KINDS: Final[frozenset[str]] = frozenset(
    {
        "Workflow",
        "Subject",
        "Execution",
        "Completion",
        "Decision",
        "FollowupEdge",
    }
)

# Canonical Input Classifications Mapping
CANONICAL_INPUT_CLASSIFICATIONS: Final[dict[str, str]] = {
    "legacy_portable_plan_state": "migration_evidence",
    "accepted_plan_authority_snapshot": "semantic_source_fact",
    "bounded_legacy_graph_evidence": "migration_evidence",
    "project_workspace_manifest_facts": "semantic_source_fact",
    "explicit_migration_evidence_artifacts": "migration_evidence",
    "legacy_current_pointers_and_control_comments": "forbidden_authority_source",
}

FORBIDDEN_AUTHORITY_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "legacy_current_pointers_and_control_comments",
        "current_active_plan",
        "current_task_id",
        "arbitrary_host_path",
    }
)

__all__ = [
    "B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED",
    "B12_IS_INDEPENDENT_SHADOW_VALIDATION",
    "B12_IS_BOOTSTRAP_IMPLEMENTATION",
    "B12_IS_CUTOVER",
    "B12_HAS_INDEPENDENT_ASSERTION_SET",
    "B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE",
    "B12_VALIDATION_REPORT_IMPLEMENTED",
    "RAW_INTERNAL_ID_SELF_BINDING_ALLOWED",
    "OBJECT_REF_IS_AUTHORITY",
    "M3_B12_EXECUTION_AUTHORIZED",
    "M3_B13_EXECUTION_AUTHORIZED",
    "M3_BG2_EXECUTION_AUTHORIZED",
    "M3_B14_EXECUTION_AUTHORIZED",
    "M3_B_SHADOW_MATERIALIZATION_AUTHORIZED",
    "PRODUCTION_GRAPH_AUTHORITY_ACTIVE",
    "AUTHORITATIVE_GRAPH_WRITES_ALLOWED",
    "CUTOVER_AUTHORIZED",
    "LIVE_PRODUCTION_MIGRATION_PERFORMED",
    "DEPLOY_PERFORMED",
    "RUNTIME_RELOAD_PERFORMED",
    "PRODUCTION_SHADOW_SERVICE_ACTIVATED",
    "PUSH_PERFORMED",
    "GITHUB_MUTATION_PERFORMED",
    "EXPECTED_BASE_COMMIT",
    "CANONICAL_B3_RECORD_KINDS",
    "CANONICAL_INPUT_CLASSIFICATIONS",
    "FORBIDDEN_AUTHORITY_SOURCES",
]
