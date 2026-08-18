"""M3-B12 Independent Shadow Validation module (Issue #9, Lane M3-B12).

Provides:
- IndependentShadowValidator: orchestrates independent validation of shadow bootstrap and migration mechanics.
- IndependentShadowRebuilder: independent rebuilder for shadow state and fingerprint recomputation.
- TamperTestSuite: 12-case tamper rejection suite.
- IsolationTestSuite: multi-stage isolation and failure atomicity proofs.
- ShadowValidationReport: typed report with mandatory result block formatting.
"""

from __future__ import annotations

from aota_forge.core.shadow_validation.model import (
    B12_DIRECTLY_RECOMPUTES_CRITICAL_EVIDENCE,
    B12_HAS_INDEPENDENT_ASSERTION_SET,
    B12_INDEPENDENT_SHADOW_VALIDATION_IMPLEMENTED,
    B12_IS_BOOTSTRAP_IMPLEMENTATION,
    B12_IS_CUTOVER,
    B12_IS_INDEPENDENT_SHADOW_VALIDATION,
    B12_VALIDATION_REPORT_IMPLEMENTED,
    CANONICAL_B3_RECORD_KINDS,
    CANONICAL_INPUT_CLASSIFICATIONS,
    EXPECTED_BASE_COMMIT,
    FORBIDDEN_AUTHORITY_SOURCES,
    OBJECT_REF_IS_AUTHORITY,
    RAW_INTERNAL_ID_SELF_BINDING_ALLOWED,
)
from aota_forge.core.shadow_validation.rebuilder import (
    IndependentShadowRebuilder,
    RebuildOutcome,
)
from aota_forge.core.shadow_validation.tamper import (
    TamperTestSuite,
)
from aota_forge.core.shadow_validation.isolation import (
    IsolationTestSuite,
    compute_canonical_graph_fingerprint,
    compute_binding_fingerprint,
)
from aota_forge.core.shadow_validation.report import (
    ProductionIsolationProof,
    ShadowValidationReport,
    TamperCaseResult,
)
from aota_forge.core.shadow_validation.validator import (
    IndependentShadowValidator,
    create_sample_deterministic_inputs,
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
    "EXPECTED_BASE_COMMIT",
    "CANONICAL_B3_RECORD_KINDS",
    "CANONICAL_INPUT_CLASSIFICATIONS",
    "FORBIDDEN_AUTHORITY_SOURCES",
    "IndependentShadowValidator",
    "IndependentShadowRebuilder",
    "RebuildOutcome",
    "TamperTestSuite",
    "IsolationTestSuite",
    "compute_canonical_graph_fingerprint",
    "compute_binding_fingerprint",
    "TamperCaseResult",
    "ProductionIsolationProof",
    "ShadowValidationReport",
    "create_sample_deterministic_inputs",
]
