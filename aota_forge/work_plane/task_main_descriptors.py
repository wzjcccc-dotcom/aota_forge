"""Task-main control descriptors — canonical YAML projection (M3/W1).

Single semantic authority is .aota/contracts/operations.yaml via loader.
This module is a thin compatibility projection that deterministically derives
from the canonical source; it does not constitute an independently maintained
hard-coded authority.
TOOL_SCHEMA_SECOND_AUTHORITY=no
"""

from __future__ import annotations

from aota_forge.core.contracts.descriptor import OperationContractDescriptor


def _load_canonical_descriptor(name: str) -> OperationContractDescriptor:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map

    root = discover_canonical_project_root()
    return load_operation_descriptor_map(root)[name]


TASK_MAIN_ACTIVATE_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task_main.activate_milestone")
TASK_MAIN_RECOVER_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task_main.recover_coordinator")
TASK_MAIN_ADVANCE_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task_main.advance_once")

TASK_MAIN_ACTIVATE_DESCRIPTOR.validate()
TASK_MAIN_RECOVER_DESCRIPTOR.validate()
TASK_MAIN_ADVANCE_DESCRIPTOR.validate()

TASK_MAIN_DESCRIPTORS: tuple[OperationContractDescriptor, ...] = (
    TASK_MAIN_ACTIVATE_DESCRIPTOR,
    TASK_MAIN_RECOVER_DESCRIPTOR,
    TASK_MAIN_ADVANCE_DESCRIPTOR,
)
