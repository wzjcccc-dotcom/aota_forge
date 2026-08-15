#!/usr/bin/env python3
"""M1 static dependency guard — proves the Core import prohibition.

Verifies:

    NEW_CORE_IMPORTS_LEGACY_CONTROL_PLANE=no

by scanning the aota_forge package source for forbidden imports and by
importing Core in a Hermes-free environment.

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

FORBIDDEN_IMPORT_PATTERNS = (
    "plugin.aota_tools",
    "plugin.aota-tools",
    "hermes",
    "webui",
    "docker",
    "outbox",
    "parent_wake",
    "profile_task",
    "process_registry",
    "delivery_outbox",
    "spec_lifecycle",
    "task_main",
    "current_pointer",
)

FORBIDDEN_TOP_LEVEL_MODULES = {
    "plugin",
    "hermes",
    "docker",
    "requests",
}


def scan_forbidden_imports() -> list[str]:
    violations: list[str] = []
    for path in sorted(AOTA_FORGE_ROOT.rglob("*.py")):
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
                if any(pattern in folded for pattern in FORBIDDEN_IMPORT_PATTERNS):
                    violations.append(f"{path}:{node.lineno}: forbidden import: {name}")
                top = name.split(".")[0].replace("-", "_").casefold()
                if top in FORBIDDEN_TOP_LEVEL_MODULES:
                    violations.append(f"{path}:{node.lineno}: forbidden top-level import: {name}")
    return sorted(set(violations))


def assert_core_imports_without_hermes() -> bool:
    for module in list(sys.modules):
        if "hermes" in module.casefold():
            del sys.modules[module]
    importlib.import_module("aota_forge.core")
    return True


def main() -> int:
    violations = scan_forbidden_imports()
    results = {
        "guard": "m1_dependency_guard",
        "target": str(AOTA_FORGE_ROOT),
        "NEW_CORE_IMPORTS_LEGACY_CONTROL_PLANE": "no" if not violations else "yes",
        "forbidden_import_count": len(violations),
        "forbidden_imports": violations,
        "core_imports_without_hermes": assert_core_imports_without_hermes(),
        "verdict": "PASS" if not violations else "FAIL",
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
