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

# W5 (AF #49 M1/W5, I49-B001): canonical normal-path lifecycle descriptors,
# loaded from the same single authority (.aota/contracts/operations.yaml).
HANDOFF_WRITE_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("handoff.write")
HANDOFF_OPEN_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("handoff.open")
TASK_START_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task.start")
TASK_RETURN_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("task.return")

HANDOFF_WRITE_DESCRIPTOR.validate()
HANDOFF_OPEN_DESCRIPTOR.validate()
TASK_START_DESCRIPTOR.validate()
TASK_RETURN_DESCRIPTOR.validate()

TASK_MAIN_DESCRIPTORS: tuple[OperationContractDescriptor, ...] = (
    TASK_MAIN_ACTIVATE_DESCRIPTOR,
    TASK_MAIN_RECOVER_DESCRIPTOR,
    TASK_MAIN_ADVANCE_DESCRIPTOR,
    TASK_MAIN_SUBMIT_DESCRIPTOR,
)

# W5 guidance disposition freeze (submit_work_projection stays importable and
# visible for compatibility; it is not the Agent normal path or authority).
SUBMIT_WORK_PROJECTION_REMOVED = False
SUBMIT_WORK_PROJECTION_INTERNAL_COMPATIBILITY_ONLY = True
SUBMIT_WORK_PROJECTION_AGENT_NORMAL_PATH = False
TASK_MAIN_NORMAL_PATH = "authoritative_work_source>handoff.write(work_item)>task.start"

# ---------------------------------------------------------------------------
# M3/W1-R1 model-visible operation guidance (F1); W5 normal-path convergence.
#
# Single schema authority remains .aota/contracts/operations.yaml via loader
# (OPERATION_DESCRIPTOR_AUTHORITY_COUNT=1). This function deterministically
# derives a compact model-usable projection from the canonical descriptors;
# it never hardcodes a second independent schema. Required names/types come
# from descriptor.inputs; type+bound suffixes come from Core constants.
# Usage/example are curated guidance, not schema authority.
#
# W5 (AF #49 M1/W5): normal guidance is now
#   authoritative Work source -> LLM reasoning -> handoff.write(work_item)
#   -> task.start
# task_main.submit_work_projection is retained for compatibility only and is
# explicitly marked as not the Agent normal path (no authority change).
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


_SUBMIT_NOTE = "compatibility only; not agent normal path; cannot bypass Work-source grounding"
_SUBMIT_EXAMPLE = {
    "work_item_id": "W1",
    "objective": "Goal X",
    "bounded_scope": "Implement X in src/x.py",
    "validation_expectations": ["X ok"],
    "semantic_stop_expectations": ["stop if unclear"],
}

# W5 (AF #49 M1/W5): compact normal-path guidance. Names/types come from the
# canonical descriptors; usage and examples are curated guidance. The
# authoritative Work source arrives in activate/recover/advance work_context;
# the model reasons, writes a work_item handoff, then starts the target role.
# submit_work_projection stays compatibility-only and explicitly not the
# Agent normal path. Guidance stays compact to bound the payload; a bootstrap
# over the inline projection bound is delivered as a governed by_ref result.
_NORMAL_PATH_FLOW = "work_context -> reasoning -> handoff.write(mode=work_item) -> task.start"
_HANDOFF_WRITE_NOTE = "normal step 1; mode=work_item; trusted envelope filled by AF"
_HANDOFF_WRITE_EXAMPLE = {
    "mode": "work_item",
    "payload": {"objective": "Goal", "bounded_scope": "Scope"},
}
_TASK_START_NOTE = "normal step 2; AF verifies durable Work-source grounding"
_TASK_START_EXAMPLE = {"role": "coder", "handoff_ref": "<ref from handoff.write>"}


def _required_types(descriptor: OperationContractDescriptor) -> dict[str, str]:
    """Deterministic required-name -> type projection from the descriptor."""
    required: dict[str, str] = {}
    for spec in descriptor.inputs:
        if not spec.type.endswith("?"):
            required[spec.name] = spec.type
    return required


def build_task_main_operation_guidance() -> dict[str, dict[str, object]]:
    """Deterministic compact model-visible guidance (W5 normal path first).

    Required names/types are read from the canonical descriptors; usage and
    examples are curated guidance. No second schema authority is created.
    ``task_main.submit_work_projection`` is retained for compatibility and is
    explicitly marked as not the Agent normal path.
    """
    submit_required: dict[str, str] = {}
    for spec in TASK_MAIN_SUBMIT_DESCRIPTOR.inputs:
        submit_required[spec.name] = _submit_bound_suffix(spec.name, spec.type)
    return {
        "normal_path": {
            "flow": _NORMAL_PATH_FLOW,
            "steps": ["handoff.write", "task.start"],
        },
        "handoff.write": {
            "required": _required_types(HANDOFF_WRITE_DESCRIPTOR),
            "note": _HANDOFF_WRITE_NOTE,
            "example": dict(_HANDOFF_WRITE_EXAMPLE),
        },
        "task.start": {
            "required": _required_types(TASK_START_DESCRIPTOR),
            "note": _TASK_START_NOTE,
            "example": dict(_TASK_START_EXAMPLE),
        },
        "task_main.submit_work_projection": {
            "required": submit_required,
            "note": _SUBMIT_NOTE,
            "compatibility_only": True,
            "agent_normal_path": False,
            "example": dict(_SUBMIT_EXAMPLE),
        },
    }


def task_main_submit_required_fields() -> tuple[str, ...]:
    """Required field names for submit, derived from canonical descriptor."""
    return tuple(spec.name for spec in TASK_MAIN_SUBMIT_DESCRIPTOR.inputs)
