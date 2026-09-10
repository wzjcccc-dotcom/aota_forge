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
TASK_MAIN_SUBMIT_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task_main.submit_work_projection")

TASK_MAIN_ACTIVATE_DESCRIPTOR.validate()
TASK_MAIN_RECOVER_DESCRIPTOR.validate()
TASK_MAIN_ADVANCE_DESCRIPTOR.validate()
TASK_MAIN_SUBMIT_DESCRIPTOR.validate()

TASK_MAIN_DESCRIPTORS: tuple[OperationContractDescriptor, ...] = (
    TASK_MAIN_ACTIVATE_DESCRIPTOR,
    TASK_MAIN_RECOVER_DESCRIPTOR,
    TASK_MAIN_ADVANCE_DESCRIPTOR,
    TASK_MAIN_SUBMIT_DESCRIPTOR,
)

# ---------------------------------------------------------------------------
# M3/W1-R1 model-visible operation guidance (F1).
#
# Single schema authority remains .aota/contracts/operations.yaml via loader
# (OPERATION_DESCRIPTOR_AUTHORITY_COUNT=1). This function deterministically
# derives a compact model-usable projection from the canonical descriptor;
# it never hardcodes a second independent schema. Required names/types come
# from descriptor.inputs; type+bound suffixes come from Core constants.
# Usage/example are curated guidance, not schema authority. Submit-only to
# stay within the 4096 inline bound; other controls take empty args (see
# eager Skill guidance which already covers activate/recover/advance).
# ---------------------------------------------------------------------------

def _submit_bound_suffix(field_name: str, primitive: str) -> str:
    from aota_forge.work_plane.handoff import (
        MAX_EXPECTATION_LENGTH,
        MAX_EXPECTATIONS_COUNT,
        MAX_OBJECTIVE_LENGTH,
        MAX_SCOPE_LENGTH,
    )

    if primitive == "str":
        if field_name == "work_item_id":
            return "str<=128,correlation not authority"
        if field_name == "objective":
            return f"str<={MAX_OBJECTIVE_LENGTH},executable goal"
        if field_name == "bounded_scope":
            return f"str<={MAX_SCOPE_LENGTH},punctuation ok,text"
        return "str<=4096"
    if primitive == "list":
        if field_name == "validation_expectations":
            return f"list<={MAX_EXPECTATIONS_COUNT}x{MAX_EXPECTATION_LENGTH},evidence"
        if field_name == "semantic_stop_expectations":
            return f"list<={MAX_EXPECTATIONS_COUNT}x{MAX_EXPECTATION_LENGTH},conditions"
        return "list<=256"
    return primitive


_SUBMIT_NOTE = "text!=authority;projection!=plan authority;no expansion"
_SUBMIT_EXAMPLE = {
    "work_item_id": "W1",
    "objective": "Goal X",
    "bounded_scope": "Implement X in src/x.py,validate.",
    "validation_expectations": ["X ok"],
    "semantic_stop_expectations": ["stop if unclear"],
}


def build_task_main_operation_guidance() -> dict[str, dict[str, object]]:
    """Deterministic compact model-visible guidance for submit (F1, eager).

    Required names/types are read from the canonical descriptor; bounds from
    Core constants. No second schema authority is created.
    """
    required: dict[str, str] = {}
    for spec in TASK_MAIN_SUBMIT_DESCRIPTOR.inputs:
        required[spec.name] = _submit_bound_suffix(spec.name, spec.type)
    return {
        "task_main.submit_work_projection": {
            "required": required,
            "note": _SUBMIT_NOTE,
            "example": dict(_SUBMIT_EXAMPLE),
        }
    }


def task_main_submit_required_fields() -> tuple[str, ...]:
    """Required field names for submit, derived from canonical descriptor."""
    return tuple(spec.name for spec in TASK_MAIN_SUBMIT_DESCRIPTOR.inputs)
