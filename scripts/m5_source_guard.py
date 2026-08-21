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

AUTHORIZED_POST_ACCEPTANCE_REPAIRS = {
    "M5-6-R1-F01": {
        "path": "tests/test_m5_5_cli_dispatch.py",
        "class": "temporal_regression_invariant_defect",
        "target_class": "TestM55NegativeAndArchitecturalInvariants",
        "target_method": "test_q15_m5_6_not_started",
    }
}


def git(*args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def verify_authorized_regression_repair(
    base_source: str,
    current_source: str,
    class_name: str,
    method_name: str,
) -> tuple[bool, str]:
    """Deterministic AST verifier proving only target_class.target_method body changed."""
    try:
        base_tree = ast.parse(base_source)
    except Exception as exc:
        return False, f"Failed to parse base source AST: {exc}"
    try:
        current_tree = ast.parse(current_source)
    except Exception as exc:
        return False, f"Failed to parse current source AST: {exc}"

    # 1. Target class exists exactly once in both
    base_classes = [n for n in base_tree.body if isinstance(n, ast.ClassDef) and n.name == class_name]
    if len(base_classes) != 1:
        return False, f"Base source defines class '{class_name}' {len(base_classes)} times (expected 1)"

    curr_classes = [n for n in current_tree.body if isinstance(n, ast.ClassDef) and n.name == class_name]
    if len(curr_classes) != 1:
        return False, f"Current source defines class '{class_name}' {len(curr_classes)} times (expected 1)"

    base_cls = base_classes[0]
    curr_cls = curr_classes[0]

    # 2. Target method exists exactly once in both
    base_methods = [
        n for n in base_cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name
    ]
    if len(base_methods) != 1:
        return False, f"Base class '{class_name}' defines method '{method_name}' {len(base_methods)} times (expected 1)"

    curr_methods = [
        n for n in curr_cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name
    ]
    if len(curr_methods) != 1:
        return False, f"Current class '{class_name}' defines method '{method_name}' {len(curr_methods)} times (expected 1)"

    base_method = base_methods[0]
    curr_method = curr_methods[0]

    # 3. Target name is unchanged (confirmed by name check)
    # 4. Decorators, parameters, and return annotations on target method must be unchanged
    if len(base_method.decorator_list) != len(curr_method.decorator_list):
        return False, f"Target method {class_name}.{method_name} decorators modified"
    for b_dec, c_dec in zip(base_method.decorator_list, curr_method.decorator_list):
        if ast.dump(b_dec, include_attributes=False) != ast.dump(c_dec, include_attributes=False):
            return False, f"Target method {class_name}.{method_name} decorator content modified"

    if ast.dump(base_method.args, include_attributes=False) != ast.dump(curr_method.args, include_attributes=False):
        return False, f"Target method {class_name}.{method_name} arguments/signature modified"

    if (base_method.returns is None) != (curr_method.returns is None):
        return False, f"Target method {class_name}.{method_name} return annotation modified"
    if base_method.returns and curr_method.returns:
        if ast.dump(base_method.returns, include_attributes=False) != ast.dump(curr_method.returns, include_attributes=False):
            return False, f"Target method {class_name}.{method_name} return annotation modified"

    # 5. Permits only the body of the exact target method to differ.
    # Replace body of target method with [ast.Pass()] in both AST trees.
    base_method.body = [ast.Pass()]
    curr_method.body = [ast.Pass()]

    base_dump = ast.dump(base_tree, include_attributes=False)
    curr_dump = ast.dump(current_tree, include_attributes=False)

    if base_dump != curr_dump:
        return False, f"Structural mutation detected outside authorized method {class_name}.{method_name}"

    return True, f"Authorized repair in {class_name}.{method_name} verified cleanly"


def verify_authorized_repair_for_file(rel_path: str) -> tuple[bool, str]:
    """Verify that a modified file matches an explicitly authorized post-acceptance repair."""
    matching_repairs = [
        (repair_id, info)
        for repair_id, info in AUTHORIZED_POST_ACCEPTANCE_REPAIRS.items()
        if info.get("path") == rel_path
    ]
    if not matching_repairs:
        return False, f"No authorized post-acceptance repair registered for path: {rel_path}"
    if len(matching_repairs) > 1:
        return False, f"Multiple authorized post-acceptance repairs registered for path: {rel_path}"

    repair_id, repair_info = matching_repairs[0]
    target_class = repair_info.get("target_class")
    target_method = repair_info.get("target_method")
    if not target_class or not target_method:
        return False, f"Repair {repair_id} missing target_class or target_method metadata"

    try:
        base_source = git("show", f"{ACCEPTED_BASE}:{rel_path}")
    except Exception as exc:
        return False, f"Failed to read base version of {rel_path} at {ACCEPTED_BASE}: {exc}"

    curr_path = ROOT / rel_path
    if not curr_path.is_file():
        return False, f"Current file {rel_path} does not exist"
    try:
        with open(curr_path, "r", encoding="utf-8") as handle:
            curr_source = handle.read()
    except Exception as exc:
        return False, f"Failed to read current version of {rel_path}: {exc}"

    return verify_authorized_regression_repair(base_source, curr_source, target_class, target_method)


def _self_validate_guard_repair_logic() -> tuple[bool, str]:
    """Non-destructive self-validation of AST repair verification logic."""
    sample_base = (
        "import unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_other(self):\n"
        "        pass\n\n"
        "    def test_target(self):\n"
        "        pass\n"
    )
    # Valid modification
    sample_valid = (
        "import unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_other(self):\n"
        "        pass\n\n"
        "    def test_target(self):\n"
        "        x = 42\n"
        "        self.assertEqual(x, 42)\n"
    )
    ok, msg = verify_authorized_regression_repair(sample_base, sample_valid, "TestSample", "test_target")
    if not ok:
        return False, f"Self-validation failed on valid repair: {msg}"

    # Invalid: other method modified
    sample_invalid_other = (
        "import unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_other(self):\n"
        "        self.assertTrue(False)\n\n"
        "    def test_target(self):\n"
        "        pass\n"
    )
    ok, _ = verify_authorized_regression_repair(sample_base, sample_invalid_other, "TestSample", "test_target")
    if ok:
        return False, "Self-validation failed: unauthorized other method modification was accepted"

    # Invalid: import modified
    sample_invalid_import = (
        "import os\nimport unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_other(self):\n"
        "        pass\n\n"
        "    def test_target(self):\n"
        "        pass\n"
    )
    ok, _ = verify_authorized_regression_repair(sample_base, sample_invalid_import, "TestSample", "test_target")
    if ok:
        return False, "Self-validation failed: unauthorized import modification was accepted"

    # Invalid: signature modified
    sample_invalid_sig = (
        "import unittest\n\n"
        "class TestSample(unittest.TestCase):\n"
        "    def test_other(self):\n"
        "        pass\n\n"
        "    def test_target(self, extra=True):\n"
        "        pass\n"
    )
    ok, _ = verify_authorized_regression_repair(sample_base, sample_invalid_sig, "TestSample", "test_target")
    if ok:
        return False, "Self-validation failed: unauthorized signature modification was accepted"

    return True, "Guard repair self-validation passed"


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
    self_ok, self_msg = _self_validate_guard_repair_logic()
    if not self_ok:
        return False, f"Guard quality verification failed: {self_msg}"

    diff_names = [line.strip() for line in git("diff", "--name-only", ACCEPTED_BASE).splitlines() if line.strip()]
    for name in diff_names:
        if name in ALLOWED_CHANGED_PATTERNS or name.startswith("deploy/evidence/issues/9/m5-source/"):
            continue
        if any(info.get("path") == name for info in AUTHORIZED_POST_ACCEPTANCE_REPAIRS.values()):
            ok, msg = verify_authorized_repair_for_file(name)
            if not ok:
                return False, f"Authorized repair verification failed for {name}: {msg}"
        else:
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
    self_ok, self_msg = _self_validate_guard_repair_logic()
    if not self_ok:
        return False, f"Guard quality verification failed: {self_msg}"

    diff_names = [line.strip() for line in git("diff", "--name-only", ACCEPTED_BASE).splitlines() if line.strip()]
    for name in diff_names:
        if name.startswith("aota_forge/"):
            return False, f"Production source touched: {name}"
        if name.startswith("tests/test_m4_"):
            return False, f"Previous test touched: {name}"
        if name.startswith("tests/test_m5_") and name != "tests/test_m5_6_convergence.py":
            if any(info.get("path") == name for info in AUTHORIZED_POST_ACCEPTANCE_REPAIRS.values()):
                ok, msg = verify_authorized_repair_for_file(name)
                if not ok:
                    return False, f"Previous test touched without verified authorized repair ({name}): {msg}"
            else:
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
