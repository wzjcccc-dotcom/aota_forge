"""Result governance package — S5 common core.

W2 owns outcome/provenance/completeness; W3 owns governed references,
verification, and side-effect outcome projections.
"""

from .common import (
    RESULT_GOVERNANCE_VERSION,
    GovernedReference,
    GovernedReferenceKind,
    ResultCompleteness,
    ResultGovernanceProjection,
    ResultOutcome,
    ResultProvenance,
    SideEffectOutcome,
    VerificationStatus,
)

__all__ = [
    "RESULT_GOVERNANCE_VERSION",
    "ResultOutcome",
    "ResultProvenance",
    "ResultCompleteness",
    "ResultGovernanceProjection",
    "GovernedReference",
    "GovernedReferenceKind",
    "VerificationStatus",
    "SideEffectOutcome",
]
