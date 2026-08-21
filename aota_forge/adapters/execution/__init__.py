"""Execution adapters package (M5).

Provides executor adapters implementing the canonical ExecutorAdapter interface.
Includes reference fake adapter for deterministic contract testing (M5-2).
"""

from __future__ import annotations

from aota_forge.adapters.execution.reference import (
    DEFAULT_CAPABILITIES,
    DEFAULT_ROLE_MAPPING,
    REFERENCE_ADAPTER_KIND,
    REFERENCE_EXECUTOR_ID,
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    REFERENCE_EXECUTOR_TEST_ONLY,
    ReferenceFakeExecutorAdapter,
)

__all__ = [
    "DEFAULT_CAPABILITIES",
    "DEFAULT_ROLE_MAPPING",
    "REFERENCE_ADAPTER_KIND",
    "REFERENCE_EXECUTOR_ID",
    "REFERENCE_EXECUTOR_PRODUCTION_DEFAULT",
    "REFERENCE_EXECUTOR_TEST_ONLY",
    "ReferenceFakeExecutorAdapter",
]
