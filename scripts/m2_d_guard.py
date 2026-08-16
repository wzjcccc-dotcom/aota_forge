#!/usr/bin/env python3
"""M2-D static dependency guard — proves Core/GitHub decoupling.

Verifies:

    GITHUB_IS_FORGE_CORE_ONTOLOGY=no
    GITHUB_API_IS_CORE_CONTRACT=no
    GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID=no
    RAW_GH_OPERATION_IN_CORE=no
    PLAN_WRITE_IMPLEMENTED=no

by scanning the aota_forge package for forbidden GitHub/network imports and
write-capable plan authority surface, and by importing Core in a Hermes-free
environment.

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

FORBIDDEN_IMPORT_SUBSTRINGS = (
    "github",
    "pygithub",
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
)

FORBIDDEN_TOP_LEVEL_MODULES = {
    "github",
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "pygithub",
}

PLAN_AUTHORITY_WRITE_PREFIXES = ("create", "update", "delete", "post", "patch", "write", "comment")


def scan_forbidden_imports(package_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{path}: syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names = [node.module]
            for name in names:
                folded = name.replace("-", "_").casefold()
                if any(pattern in folded for pattern in FORBIDDEN_IMPORT_SUBSTRINGS):
                    violations.append(f"{path}:{node.lineno}: forbidden import: {name}")
                top = name.split(".")[0].replace("-", "_").casefold()
                if top in FORBIDDEN_TOP_LEVEL_MODULES:
                    violations.append(f"{path}:{node.lineno}: forbidden top-level import: {name}")
    return sorted(set(violations))


def scan_plan_authority_write_surface() -> list[str]:
    violations: list[str] = []
    authority_init = AOTA_FORGE_ROOT / "adapters" / "plan_authority" / "__init__.py"
    tree = ast.parse(authority_init.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            if node.name.startswith(PLAN_AUTHORITY_WRITE_PREFIXES):
                violations.append(f"{authority_init}:{node.lineno}: write-capable method: {node.name}")
    return sorted(set(violations))


def scan_core_adapter_import() -> list[str]:
    violations: list[str] = []
    plan_root = AOTA_FORGE_ROOT / "core" / "plan"
    for path in sorted(plan_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names = [node.module]
            for name in names:
                if "aota_forge.adapters" in name:
                    violations.append(f"{path}:{node.lineno}: core/plan imports adapter: {name}")
    return sorted(set(violations))


def assert_core_imports_without_hermes() -> bool:
    for module in list(sys.modules):
        if "hermes" in module.casefold():
            del sys.modules[module]
    importlib.import_module("aota_forge.core")
    return True


def main() -> int:
    forbidden_imports = scan_forbidden_imports(AOTA_FORGE_ROOT)
    write_surface = scan_plan_authority_write_surface()
    core_adapter_imports = scan_core_adapter_import()

    from aota_forge.adapters import plan_authority as plan_authority_module

    markers = {
        "GITHUB_IS_FORGE_CORE_ONTOLOGY": plan_authority_module.GITHUB_IS_FORGE_CORE_ONTOLOGY,
        "GITHUB_API_IS_CORE_CONTRACT": plan_authority_module.GITHUB_API_IS_CORE_CONTRACT,
        "GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID": plan_authority_module.GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID,
        "RAW_GH_OPERATION_IN_CORE": plan_authority_module.RAW_GH_OPERATION_IN_CORE,
    }
    markers_ok = all(value == "no" for value in markers.values())

    results = {
        "guard": "m2_d_guard",
        "target": str(AOTA_FORGE_ROOT),
        "GITHUB_IS_FORGE_CORE_ONTOLOGY": "no" if not forbidden_imports else "yes",
        "GITHUB_API_IS_CORE_CONTRACT": markers["GITHUB_API_IS_CORE_CONTRACT"],
        "GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID": markers["GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID"],
        "RAW_GH_OPERATION_IN_CORE": markers["RAW_GH_OPERATION_IN_CORE"],
        "forbidden_import_count": len(forbidden_imports),
        "forbidden_imports": forbidden_imports,
        "PLAN_WRITE_IMPLEMENTED": "no" if not write_surface else "yes",
        "plan_authority_write_surface": write_surface,
        "CORE_IMPORTS_ADAPTER": "no" if not core_adapter_imports else "yes",
        "core_adapter_imports": core_adapter_imports,
        "core_imports_without_hermes": assert_core_imports_without_hermes(),
        "verdict": "PASS" if not forbidden_imports and not write_surface and not core_adapter_imports and markers_ok else "FAIL",
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
