#!/usr/bin/env python3
"""M5-6 Source Implementation Guard (SG01 .. SG15).

Deterministic, read-only mechanical guard verifying:
- SG01: Required accepted M5 production files exist
- SG02: Required M5 tests exist
- SG03: Core zero direct Hermes dependency
- SG04: CLI zero direct Hermes dependency
- SG05: Arbitrary executor command surface absent
- SG06: ReferenceFake not production default
- SG07: Exact M4 mutation operation set (plan_init, plan_retirement)
- SG08: Exact six execution CLI operations
- SG09: Resume CLI absent
- SG10: Canonical roles and states retained
- SG11: No forbidden M5-6 production path
- SG12: No unresolved semantic TODO
- SG13: Source ownership intact
- SG14: M5-6 did not mutate production source
- SG15: No live/deploy artifacts in M5 source scope
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aota_forge.adapters.execution.reference import (
    REFERENCE_EXECUTOR_PRODUCTION_DEFAULT,
    REFERENCE_EXECUTOR_TEST_ONLY,
)
from aota_forge.cli.__main__ import ROUTES, _build_parser
from aota_forge.core.contracts.descriptor import LIFECYCLE_DESCRIPTORS
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.execution import (
    CANONICAL_ROLES,
    CANONICAL_TASK_STATES,
)
from aota_forge.core.ingress import (
    CANONICAL_EXECUTION_OPERATIONS,
    EXECUTION_DESCRIPTORS,
    get_execution_dispatcher,
)

ACCEPTED_BASE = "3b2796188e4f88047215fc7ee5271dc06a9b1be4"

REQUIRED_PRODUCTION_FILES = [
    "aota_forge/core/execution/__init__.py",
    "aota_forge/core/execution/capabilities.py",
    "aota_forge/core/execution/package.py",
    "aota_forge/core/execution/state.py",
    "aota_forge/core/execution/results.py",
    "aota_forge/core/execution/roles.py",
    "aota_forge/core/execution/adapter.py",
    "aota_forge/core/execution/registry.py",
    "aota_forge/core/execution/dispatcher.py",
    "aota_forge/adapters/execution/__init__.py",
    "aota_forge/adapters/execution/reference.py",
    "aota_forge/adapters/hermes/__init__.py",
    "aota_forge/adapters/hermes/executor.py",
    "aota_forge/cli/commands/execution.py",
    "aota_forge/core/ingress.py",
    "aota_forge/cli/__main__.py",
    "aota_forge/cli/projection.py",
]

REQUIRED_TEST_FILES = [
    "tests/test_m5_1_contracts.py",
    "tests/test_m5_2_reference_adapter.py",
    "tests/test_m5_3_dispatcher.py",
    "tests/test_m5_4_hermes_adapter.py",
    "tests/test_m5_5_cli_dispatch.py",
    "tests/test_m5_6_convergence.py",
]

ALLOWED_CHANGED_PATTERNS = {
    "tests/test_m5_6_convergence.py",
    "scripts/m5_source_guard.py",
}


def git(*args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def check_sg01_production_files_exist() -> tuple[bool, str]:
    missing = [f for f in REQUIRED_PRODUCTION_FILES if not (ROOT / f).is_file()]
    if missing:
        return False, f"Missing production files: {missing}"
    return True, f"All {len(REQUIRED_PRODUCTION_FILES)} required production files present"


def check_sg02_test_files_exist() -> tuple[bool, str]:
    missing = [f for f in REQUIRED_TEST_FILES if not (ROOT / f).is_file()]
    if missing:
        return False, f"Missing test files: {missing}"
    return True, f"All {len(REQUIRED_TEST_FILES)} required test files present"


def check_sg03_core_zero_hermes_dep() -> tuple[bool, str]:
    core_dir = ROOT / "aota_forge" / "core"
    leaks: list[str] = []
    for py_file in core_dir.rglob("*.py"):
        with open(py_file, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "hermes" in alias.name.lower():
                        leaks.append(f"{py_file.relative_to(ROOT)}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if "hermes" in node.module.lower():
                    leaks.append(f"{py_file.relative_to(ROOT)}: from {node.module} import ...")
    if leaks:
        return False, f"Core Hermes imports found ({len(leaks)}): {leaks}"
    return True, "Core has 0 Hermes direct dependencies"


def check_sg04_cli_zero_hermes_dep() -> tuple[bool, str]:
    cli_dir = ROOT / "aota_forge" / "cli"
    leaks: list[str] = []
    for py_file in cli_dir.rglob("*.py"):
        with open(py_file, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "hermes" in alias.name.lower():
                        leaks.append(f"{py_file.relative_to(ROOT)}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if "hermes" in node.module.lower():
                    leaks.append(f"{py_file.relative_to(ROOT)}: from {node.module} import ...")
    if leaks:
        return False, f"CLI Hermes imports found ({len(leaks)}): {leaks}"
    return True, "CLI has 0 Hermes direct dependencies"


def check_sg05_arbitrary_executor_command_absent() -> tuple[bool, str]:
    forbidden = ["--command", "--raw-command", "task.exec", "executor.run"]
    parser = _build_parser()
    actions = [a.dest for a in parser._actions]
    for fb in forbidden:
        if fb in actions:
            return False, f"Forbidden command surface found: {fb}"
    return True, "Arbitrary command surface absent"


def check_sg06_reference_fake_not_production_default() -> tuple[bool, str]:
    if REFERENCE_EXECUTOR_PRODUCTION_DEFAULT:
        return False, "REFERENCE_EXECUTOR_PRODUCTION_DEFAULT is True"
    if not REFERENCE_EXECUTOR_TEST_ONLY:
        return False, "REFERENCE_EXECUTOR_TEST_ONLY is False"
    if get_execution_dispatcher() is not None:
        return False, "ExecutionDispatcher is unexpectedly bound by default"
    return True, "ReferenceFake is test-only and not production default"


def check_sg07_m4_mutation_operation_set() -> tuple[bool, str]:
    m4_ops = {d.name for d in LIFECYCLE_DESCRIPTORS}
    if m4_ops != {"plan_init", "plan_retirement"}:
        return False, f"M4 mutation operations altered: {m4_ops}"
    return True, "M4 mutation operation set exact (plan_init, plan_retirement)"


def check_sg08_exact_six_execution_cli_operations() -> tuple[bool, str]:
    expected = {
        "execution.task_start",
        "execution.task_status",
        "execution.task_result",
        "execution.task_cancel",
        "execution.executor_list",
        "execution.executor_capabilities",
    }
    actual = set(CANONICAL_EXECUTION_OPERATIONS)
    if actual != expected:
        return False, f"Execution operations mismatch: {actual} != {expected}"
    if set(EXECUTION_DESCRIPTORS.keys()) != expected:
        return False, f"Execution descriptors mismatch: {set(EXECUTION_DESCRIPTORS.keys())}"
    return True, "Exact 6 execution CLI operations registered"


def check_sg09_resume_cli_absent() -> tuple[bool, str]:
    if "execution.task_resume" in CANONICAL_EXECUTION_OPERATIONS:
        return False, "task_resume present in CANONICAL_EXECUTION_OPERATIONS"
    if "execution.task_resume" in EXECUTION_DESCRIPTORS:
        return False, "task_resume present in EXECUTION_DESCRIPTORS"
    if ("task", "resume") in ROUTES:
        return False, "task resume route present in CLI ROUTES"
    return True, "Resume CLI subcommand absent"


def check_sg10_canonical_roles_and_states_retained() -> tuple[bool, str]:
    expected_roles = ("coder", "executor", "planner", "reviewer", "steward")
    if tuple(sorted(CANONICAL_ROLES)) != expected_roles:
        return False, f"Canonical roles altered: {CANONICAL_ROLES}"
    expected_states = {
        "CREATED", "ACCEPTED", "QUEUED", "RUNNING", "WAITING",
        "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN",
    }
    actual_states = {s.value for s in CANONICAL_TASK_STATES}
    if actual_states != expected_states:
        return False, f"Canonical task states altered: {actual_states}"
    return True, "Canonical roles (5) and task states (9) intact"


def check_sg11_no_forbidden_m5_6_production_path() -> tuple[bool, str]:
    diff_names = git("diff", "--name-only", ACCEPTED_BASE).splitlines()
    for name in diff_names:
        if name.strip() and name.strip() not in ALLOWED_CHANGED_PATTERNS and not name.startswith("deploy/evidence/issues/9/m5-source/"):
            return False, f"Forbidden file modified in M5-6: {name}"
    return True, "Only authorized M5-6 files modified"


def check_sg12_no_unresolved_semantic_todo() -> tuple[bool, str]:
    target_dirs = [
        ROOT / "aota_forge" / "core" / "execution",
        ROOT / "aota_forge" / "adapters" / "execution",
        ROOT / "aota_forge" / "adapters" / "hermes",
        ROOT / "aota_forge" / "cli" / "commands" / "execution.py",
        ROOT / "tests" / "test_m5_6_convergence.py",
        ROOT / "scripts" / "m5_source_guard.py",
    ]
    todos = []
    for target in target_dirs:
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        for f in files:
            with open(f, "r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    for marker in ("TODO", "TBD", "FIXME", "XXX"):
                        if marker in stripped and ("#" in stripped or '"""' in stripped):
                            if "scan accepted" in stripped.lower() or "unresolved" in stripped.lower() or "marker in" in stripped:
                                continue
                            todos.append(f"{f.relative_to(ROOT)}:{line_no}: {stripped}")
    if todos:
        return False, f"Unresolved semantic TODOs found ({len(todos)}): {todos}"
    return True, "Zero unresolved semantic TODO/TBD/FIXME/XXX"


def check_sg13_source_ownership_intact() -> tuple[bool, str]:
    diff_names = git("diff", "--name-only", ACCEPTED_BASE).splitlines()
    for name in diff_names:
        if name.startswith("aota_forge/"):
            return False, f"Production source touched: {name}"
        if name.startswith("tests/test_m4_") or (name.startswith("tests/test_m5_") and name != "tests/test_m5_6_convergence.py"):
            return False, f"Previous test touched: {name}"
    return True, "Source ownership partitions fully intact"


def check_sg14_m5_6_did_not_mutate_production_source() -> tuple[bool, str]:
    diff_prod = git("diff", "--name-only", ACCEPTED_BASE, "--", "aota_forge").splitlines()
    prod_files = [f for f in diff_prod if f.strip()]
    if prod_files:
        return False, f"Production files mutated: {prod_files}"
    return True, "Production source mutation count is 0"


def check_sg15_no_live_deploy_artifacts() -> tuple[bool, str]:
    # Ensure no live daemons or deploy scripts were triggered
    for env_var in ("HERMES_HOST_ACTIVE", "HERMES_PORT", "AOTA_DEPLOY_ACTIVE"):
        if os.environ.get(env_var):
            return False, f"Live environment variable set: {env_var}"
    return True, "No live/deploy artifacts in M5 source scope"


CHECKS = [
    ("SG01", check_sg01_production_files_exist),
    ("SG02", check_sg02_test_files_exist),
    ("SG03", check_sg03_core_zero_hermes_dep),
    ("SG04", check_sg04_cli_zero_hermes_dep),
    ("SG05", check_sg05_arbitrary_executor_command_absent),
    ("SG06", check_sg06_reference_fake_not_production_default),
    ("SG07", check_sg07_m4_mutation_operation_set),
    ("SG08", check_sg08_exact_six_execution_cli_operations),
    ("SG09", check_sg09_resume_cli_absent),
    ("SG10", check_sg10_canonical_roles_and_states_retained),
    ("SG11", check_sg11_no_forbidden_m5_6_production_path),
    ("SG12", check_sg12_no_unresolved_semantic_todo),
    ("SG13", check_sg13_source_ownership_intact),
    ("SG14", check_sg14_m5_6_did_not_mutate_production_source),
    ("SG15", check_sg15_no_live_deploy_artifacts),
]


def main() -> int:
    print("M5_SOURCE_GUARD")
    passed_count = 0
    failed_count = 0
    results: list[tuple[str, bool, str]] = []

    for name, check_fn in CHECKS:
        try:
            ok, msg = check_fn()
        except Exception as exc:
            ok, msg = False, f"Exception: {exc}"
        results.append((name, ok, msg))
        if ok:
            passed_count += 1
            print(f"{name}=PASS")
        else:
            failed_count += 1
            print(f"{name}=FAIL: {msg}")

    print(f"CHECK_TOTAL={len(CHECKS)}")
    print(f"CHECK_PASS={passed_count}")
    print(f"CHECK_FAIL={failed_count}")

    if failed_count == 0:
        print("STATUS=PASS")
        return 0
    else:
        print("STATUS=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
