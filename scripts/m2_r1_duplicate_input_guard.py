#!/usr/bin/env python3
"""M2-R1 focused deterministic validation — I9-B007 duplicate input guard.

Proves the canonical invariant:

    DUPLICATE_DESCRIPTOR_INPUT_NAMES_ALLOWED=no

Required focused cases (Issue #9 M2-R1):

1. duplicate different types (x:string, x:integer)  -> descriptor invalid
2. duplicate identical spec  (x:string, x:string)   -> descriptor invalid
3. HandlerRegistry.bind(ambiguous descriptor)       -> fail closed, not bound
4. from_dict(serialized duplicate payload)          -> deterministic rejection
5. validate_inputs direct defense                   -> no silent last-wins
6. valid distinct inputs (x:string, y:integer)      -> valid, hash stable,
   binding succeeds, input validation correct

Also proves machine semantics of DUPLICATE_OPERATION_INPUT (operation name +
duplicate input name in bounded details) and that valid-descriptor hashing is
unchanged (no regression).

Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    from aota_forge.core.contracts.descriptor import (
        READ_ONLY,
        InputSpec,
        OperationContractDescriptor,
    )
    from aota_forge.core.contracts.errors import (
        ERROR_CLASSES,
        DuplicateOperationInputError,
        error_from_dict,
    )
    from aota_forge.core.contracts.registry import HandlerRegistry
    from aota_forge.core.contracts.validation import validate_inputs

    # --- Case 1: duplicate different types ------------------------------------------
    conflicting = OperationContractDescriptor(
        name="op.dup.conflicting",
        description="duplicate input probe",
        inputs=(InputSpec("x", "str"), InputSpec("x", "int")),
        read_write=READ_ONLY,
    )
    try:
        conflicting.validate()
        check("case1_conflicting_duplicates_rejected", False, "no exception raised")
    except DuplicateOperationInputError as exc:
        ok = (
            exc.code == "DUPLICATE_OPERATION_INPUT"
            and exc.details == {"operation": "op.dup.conflicting", "input": "x"}
            and "op.dup.conflicting" in exc.message
            and "x" in exc.message
        )
        check("case1_conflicting_duplicates_rejected", ok, f"{exc.code} {exc.message}")
    except Exception as exc:  # noqa: BLE001
        check("case1_conflicting_duplicates_rejected", False, f"{type(exc).__name__}: {exc}")

    # --- Case 2: duplicate identical spec --------------------------------------------
    identical = OperationContractDescriptor(
        name="op.dup.identical",
        description="duplicate identical probe",
        inputs=(InputSpec("x", "str"), InputSpec("x", "str")),
        read_write=READ_ONLY,
    )
    try:
        identical.validate()
        check("case2_identical_duplicates_rejected", False, "no exception raised")
    except DuplicateOperationInputError:
        check("case2_identical_duplicates_rejected", True)
    except Exception as exc:  # noqa: BLE001
        check("case2_identical_duplicates_rejected", False, f"{type(exc).__name__}: {exc}")

    # --- Case 3: bind invalid descriptor fails closed ---------------------------------
    registry = HandlerRegistry()
    try:
        registry.bind(conflicting, handler=lambda ctx: {"data": {}})
        check("case3_bind_ambiguous_fails_closed", False, "no exception raised")
    except DuplicateOperationInputError:
        check(
            "case3_bind_ambiguous_fails_closed",
            registry.get("op.dup.conflicting") is None
            and registry.handler("op.dup.conflicting") is None
            and not registry.has("op.dup.conflicting"),
        )
    except Exception as exc:  # noqa: BLE001
        check("case3_bind_ambiguous_fails_closed", False, f"{type(exc).__name__}: {exc}")

    # --- Case 4: from_dict rejects serialized duplicates -------------------------------
    payload = {
        "name": "op.dup.serialized",
        "description": "serialized duplicate probe",
        "inputs": [{"name": "x", "type": "str"}, {"name": "x", "type": "int"}],
        "required_context": [],
        "optional_context": [],
        "internal_ids_required": [],
        "internal_ids_created": [],
        "read_write": "read",
        "mutation_scope": None,
        "required_authority": None,
        "approval_required": False,
        "valid_predecessor_state": None,
        "valid_successor_state": None,
        "idempotency": None,
        "errors": [],
        "protocol_version": "1.0",
    }
    try:
        OperationContractDescriptor.from_dict(payload)
        check("case4_from_dict_rejects_duplicates", False, "no exception raised")
    except DuplicateOperationInputError as exc:
        check("case4_from_dict_rejects_duplicates", exc.code == "DUPLICATE_OPERATION_INPUT", exc.message)
    except Exception as exc:  # noqa: BLE001
        check("case4_from_dict_rejects_duplicates", False, f"{type(exc).__name__}: {exc}")

    # --- Case 5: validate_inputs direct defense (no silent last-wins) -------------------
    try:
        validate_inputs(conflicting, {"x": "value"})
        check("case5_validate_inputs_no_last_wins", False, "no exception raised")
    except DuplicateOperationInputError:
        check("case5_validate_inputs_no_last_wins", True)
    except Exception as exc:  # noqa: BLE001
        check("case5_validate_inputs_no_last_wins", False, f"{type(exc).__name__}: {exc}")

    # --- Case 6: valid distinct inputs ---------------------------------------------------
    valid = OperationContractDescriptor(
        name="op.valid",
        description="valid distinct inputs",
        inputs=(InputSpec("x", "str"), InputSpec("y", "int")),
        read_write=READ_ONLY,
    )
    valid.validate()
    twin = OperationContractDescriptor(
        name="op.valid",
        description="valid distinct inputs",
        inputs=(InputSpec("y", "int"), InputSpec("x", "str")),
        read_write=READ_ONLY,
    )
    check("case6_valid_distinct_inputs", valid.contract_hash() == twin.contract_hash())
    bound = HandlerRegistry()
    bound.bind(valid, handler=lambda ctx: {"data": {}})
    check(
        "case6_valid_binding_succeeds",
        bound.get("op.valid") is valid and callable(bound.handler("op.valid")),
    )
    validated = validate_inputs(valid, {"x": "hello", "y": 42})
    check(
        "case6_valid_input_validation",
        validated == {"x": "hello", "y": 42},
        str(validated),
    )
    try:
        validate_inputs(valid, {"x": "hello", "y": "not-an-int"})
        check("case6_valid_type_validation", False, "no exception raised")
    except Exception as exc:  # noqa: BLE001
        check(
            "case6_valid_type_validation",
            getattr(exc, "code", None) == "INPUT_TYPE_INVALID",
            str(getattr(exc, "code", type(exc).__name__)),
        )

    # --- Machine semantics: registered, round-trips, bounded detail ----------------------
    registered = "DUPLICATE_OPERATION_INPUT" in ERROR_CLASSES
    instance = DuplicateOperationInputError(
        "duplicate operation input declaration in contract 'op': x",
        details={"operation": "op", "input": "x"},
    )
    restored = error_from_dict(instance.to_dict())
    check(
        "machine_semantics_duplicate_input_error",
        registered
        and isinstance(restored, DuplicateOperationInputError)
        and restored.code == "DUPLICATE_OPERATION_INPUT"
        and restored.details == {"operation": "op", "input": "x"},
    )

    # --- Hash regression: valid descriptor hash stays deterministic, canonical -----------
    check("case6_hash_stable", valid.contract_hash() == valid.contract_hash())
    parsed = json.loads(valid.to_canonical_json())
    check(
        "case6_canonical_json_inputs",
        parsed["inputs"] == [{"name": "x", "type": "str"}, {"name": "y", "type": "int"}],
        str(parsed["inputs"]),
    )

    passed = all(item["pass"] for item in results)
    print(f"\nM2-R1 DUPLICATE INPUT GUARD: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
