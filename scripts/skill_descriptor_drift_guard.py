#!/usr/bin/env python3
"""M1/W2 Skill drift guard"""
import json, pathlib, re, sys
from typing import Any
try:
    from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor
except Exception as exc:
    print(f"FAIL: cannot import {exc}", file=sys.stderr)
    sys.exit(1)
SKILL_PATH = pathlib.Path(__file__).resolve().parents[1] / "skills" / "aota-workspace-operations" / "SKILL.md"
EXPECTED_OPS = frozenset({"workspace.search", "workspace.read", "workspace.write"})
def _parse_skill_operations(skill_path: pathlib.Path) -> dict[str, dict[str, Any]]:
    text = skill_path.read_text(encoding="utf-8")
    m = re.search(r"```json\s*(\{.*?\"operations\".*?\})\s*```", text, re.DOTALL)
    if not m:
        raise ValueError(f"missing JSON block")
    j = json.loads(m.group(1))
    ops = j.get("operations")
    out: dict[str, dict[str, Any]] = {}
    for entry in ops:
        out[entry["name"]] = entry
    return out
def _descriptor_to_skill_projection(desc: OperationContractDescriptor) -> dict[str, Any]:
    inputs = []
    for spec in desc.inputs:
        inputs.append({"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")})
    return {"name": desc.name, "inputs": sorted(inputs, key=lambda x: x["name"])}
def _skill_inputs_to_map(skill_entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    m: dict[str, dict[str, Any]] = {}
    for inp in skill_entry.get("inputs", []):
        m[inp["name"]] = {"name": inp["name"], "type": inp["type"], "required": inp["required"]}
    return m
def run_guard(project_root: pathlib.Path | None = None) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    root = pathlib.Path(project_root) if project_root else discover_canonical_project_root()
    root = root.resolve()
    try:
        op_map = load_operation_descriptor_map(root)
    except Exception as exc:
        return {"status": "FAIL", "violations": [{"code": "LOAD_ERROR", "detail": str(exc)}]}
    canonical_subset = {n: op_map[n] for n in EXPECTED_OPS if n in op_map}
    for n in sorted(EXPECTED_OPS - set(canonical_subset.keys())):
        violations.append({"code": "MISSING_OPERATION_IN_CANONICAL", "operation": n, "detail": n})
    try:
        skill_ops = _parse_skill_operations(SKILL_PATH)
    except Exception as exc:
        violations.append({"code": "SKILL_PARSE_ERROR", "detail": str(exc)})
        return {"status": "FAIL", "violations": violations, "operation_count": len(canonical_subset), "skill_operation_count": 0}
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    if "SKILL_IS_AUTHORITY=no" not in skill_text:
        violations.append({"code": "SKILL_IS_AUTHORITY_VIOLATION", "detail": ""})
    if "TOOL_SCHEMA_SECOND_AUTHORITY=no" not in skill_text:
        violations.append({"code": "TOOL_SCHEMA_SECOND_AUTHORITY_VIOLATION", "detail": ""})
    canonical_names = set(canonical_subset.keys()); skill_names = set(skill_ops.keys())
    for n in sorted(canonical_names - skill_names):
        violations.append({"code": "MISSING_OPERATION", "operation": n, "detail": n})
    for n in sorted(skill_names - canonical_names):
        violations.append({"code": "EXTRA_OPERATION", "operation": n, "detail": n})
    for n in sorted(canonical_names & skill_names):
        desc = canonical_subset[n]
        skill_entry = skill_ops[n]
        canon = {i["name"]: i for i in _descriptor_to_skill_projection(desc)["inputs"]}
        try:
            skill_inputs = _skill_inputs_to_map(skill_entry)
        except Exception as exc:
            violations.append({"code": "SKILL_INPUT_PARSE_ERROR", "operation": n, "detail": str(exc)})
            continue
        for arg in sorted(set(canon.keys()) - set(skill_inputs.keys())):
            violations.append({"code": "MISSING_ARGUMENT", "operation": n, "argument": arg, "detail": arg})
        for arg in sorted(set(skill_inputs.keys()) - set(canon.keys())):
            violations.append({"code": "EXTRA_ARGUMENT", "operation": n, "argument": arg, "detail": arg})
        for arg in sorted(set(canon.keys()) & set(skill_inputs.keys())):
            if canon[arg]["type"] != skill_inputs[arg]["type"]:
                violations.append({"code": "ARGUMENT_TYPE_MISMATCH", "operation": n, "argument": arg, "detail": f"{canon[arg]['type']} vs {skill_inputs[arg]['type']}"})
            if canon[arg]["required"] != skill_inputs[arg]["required"]:
                violations.append({"code": "REQUIRED_MISMATCH", "operation": n, "argument": arg, "detail": arg})
    violations_sorted = sorted(violations, key=lambda v: (v.get("code",""), v.get("operation",""), v.get("argument","")))
    return {"status": "PASS" if not violations_sorted else "FAIL", "violations": violations_sorted, "operation_count": len(canonical_subset), "skill_operation_count": len(skill_ops) if 'skill_ops' in locals() else 0}
def main():
    import argparse, json
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=str, default=None)
    args = parser.parse_args()
    res = run_guard(pathlib.Path(args.project_root) if args.project_root else None)
    print(json.dumps(res, indent=2, sort_keys=True))
    sys.exit(0 if res["status"]=="PASS" else 1)
if __name__ == "__main__":
    main()
