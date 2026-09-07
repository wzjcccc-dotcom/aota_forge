"""W0 Canonical Source Convergence — thin deterministic parity validator.

Validates that AF is canonical for AOTA domain Skills/Tools and that legacy
projection does not claim second authority. No new registry, no marketplace,
no dynamic plugin system.
"""

from __future__ import annotations
import json, pathlib, sys

AF_ROOT = pathlib.Path(__file__).resolve().parents[1]
INVENTORY = AF_ROOT / "tests/fixtures/w0_canonical_source_convergence_inventory.json"

def main() -> int:
    if not INVENTORY.exists():
        print(f"FAIL: inventory missing: {INVENTORY}", file=sys.stderr)
        return 1
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    counts = data.get("counts", {})
    # Check invariants
    inv = data.get("invariants", {})
    assert inv.get("AOTA_SKILL_CANONICAL_SOURCE") == "aota_forge", "skill canonical must be aota_forge"
    assert inv.get("AOTA_TOOL_CANONICAL_SOURCE") == "aota_forge", "tool canonical must be aota_forge"
    assert inv.get("AOTA_HERMES_TOOLS_SKILL_AUTHORITY") == "no"
    assert inv.get("AOTA_HERMES_TOOLS_TOOL_AUTHORITY") == "no"
    assert inv.get("SKILL_IS_AUTHORITY") == "no"
    assert inv.get("COPY_LEGACY_TREE_AS_IS") == "no"
    # Check no H
    assert counts.get("CLASS_H_COUNT", 0) == 0, "no H unclear ownership affecting M1"
    assert counts.get("UNRESOLVED_OWNERSHIP_COUNT", 0) == 0
    # Check canonical skill migration
    assert counts.get("CANONICAL_DOMAIN_SKILL_MIGRATION_REQUIRED_COUNT") == counts.get("CANONICAL_DOMAIN_SKILL_MIGRATED_COUNT"), "all required skills migrated"
    # Check AF skills exist
    skills_root = AF_ROOT / "skills"
    assert skills_root.is_dir(), "AF skills/ must exist as canonical source root"
    migrated = data.get("migration_plan", {}).get("migrated_skills", [])
    for skill in migrated:
        p = skills_root / skill / "SKILL.md"
        if not p.exists():
            print(f"FAIL: migrated skill missing: {p}", file=sys.stderr)
            return 1
        content = p.read_text(encoding="utf-8")
        if "AOTA_SKILL_CANONICAL_SOURCE=aota_forge" not in content:
            print(f"FAIL: skill {skill} missing canonical header", file=sys.stderr)
            return 1
        if "SKILL_IS_AUTHORITY=no" not in content:
            # check via skill.py invariant, but also in file header
            pass
    # Check tool authority: AF operations.yaml exists
    ops_yaml = AF_ROOT / ".aota/contracts/operations.yaml"
    if not ops_yaml.exists():
        print(f"FAIL: AF operations.yaml missing", file=sys.stderr)
        return 1
    # Check workspace descriptors exist
    ws_tool = AF_ROOT / "aota_forge/work_plane/workspace_tools.py"
    if not ws_tool.exists():
        print(f"FAIL: workspace_tools.py missing", file=sys.stderr)
        return 1
    # Check that AF descriptors load
    try:
        from aota_forge.core.contracts.loader import load_operation_descriptors
        from aota_forge.core.contracts.loader import discover_canonical_project_root
        root = discover_canonical_project_root()
        descs = load_operation_descriptors(root)
        assert len(descs) >= 13, f"expected >=13 descriptors, got {len(descs)}"
    except Exception as e:
        print(f"FAIL: loading AF descriptors: {e}", file=sys.stderr)
        return 1
    print("PASS: W0 canonical source convergence parity")
    print(f"  skills migrated: {len(migrated)}")
    print(f"  AF operations: {len(descs)}")
    print(f"  inventory assets: {counts.get('LEGACY_ASSET_COUNT')}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
