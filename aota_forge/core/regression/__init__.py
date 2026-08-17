"""M3-B10 Behavioral Regression Foundation Package (Issue #9, lane M3-B10).

Provides deterministic behavioral proof infrastructure across the frozen
16-class regression matrix over the accepted integrated M3 foundation.
"""

from __future__ import annotations

from aota_forge.core.regression.fixtures import (
    FIXTURES_REQUIRE_DEPLOYMENT,
    FIXTURES_REQUIRE_HERMES_RUNTIME,
    FIXTURES_REQUIRE_LIVE_GITHUB,
    FIXTURES_REQUIRE_NETWORK,
    FIXTURES_REQUIRE_SHADOW_GRAPH,
    FIXTURES_USE_PRODUCTION_AUTHORITY_STATE,
    RegressionGraphFixture,
    TempWorkspaceFixture,
    fixture_time,
    make_test_context,
    make_test_lease,
    make_test_principal,
    make_test_store,
    setup_standard_regression_graph,
)
from aota_forge.core.regression.matrix import (
    ALL_16_CLASSES_MAPPED,
    B10_REGRESSION_MATRIX_IS_FROZEN_INPUT,
    FROZEN_INVENTORY,
    INVENTORY_BY_CLASS,
    REGRESSION_MATRIX_CLASS_COUNT,
    REGRESSION_MATRIX_MUTATED,
    REQUIRED_FAILURE_CLASSES,
    RegressionClassInventoryEntry,
    get_inventory_entry,
    list_inventory_classes,
)
from aota_forge.core.regression.proofs import (
    CLASS_PROOF_DISPATCH,
    ProofResult,
    REQUIRED_PROOF_DISPATCH,
)
from aota_forge.core.regression.runner import (
    BehavioralRegressionRunner,
    RegressionReport,
)

__all__ = [
    "ALL_16_CLASSES_MAPPED",
    "B10_REGRESSION_MATRIX_IS_FROZEN_INPUT",
    "BehavioralRegressionRunner",
    "CLASS_PROOF_DISPATCH",
    "FIXTURES_REQUIRE_DEPLOYMENT",
    "FIXTURES_REQUIRE_HERMES_RUNTIME",
    "FIXTURES_REQUIRE_LIVE_GITHUB",
    "FIXTURES_REQUIRE_NETWORK",
    "FIXTURES_REQUIRE_SHADOW_GRAPH",
    "FIXTURES_USE_PRODUCTION_AUTHORITY_STATE",
    "FROZEN_INVENTORY",
    "INVENTORY_BY_CLASS",
    "ProofResult",
    "REGRESSION_MATRIX_CLASS_COUNT",
    "REGRESSION_MATRIX_MUTATED",
    "REQUIRED_FAILURE_CLASSES",
    "REQUIRED_PROOF_DISPATCH",
    "RegressionClassInventoryEntry",
    "RegressionGraphFixture",
    "RegressionReport",
    "TempWorkspaceFixture",
    "fixture_time",
    "get_inventory_entry",
    "list_inventory_classes",
    "make_test_context",
    "make_test_lease",
    "make_test_principal",
    "make_test_store",
    "setup_standard_regression_graph",
]
