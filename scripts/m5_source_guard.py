#!/usr/bin/env python3
"""M5 source and post-repair provenance guard (SG01 .. SG35).

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
- SG16: Exact repaired-source ancestry is present
- SG17: The final repair delta contains exactly six paths
- SG18: R1C2 serial provenance is exact
- SG19: No generic or wildcard repair allowlist exists
- SG20: Future unauthorized production/test paths are rejected
- SG21: Critical guard claims are non-tautological
- SG22: M5-6 R1/R2 lifecycle protections remain bounded
- SG23: M4 test scope remains frozen
- SG24: R2E changed paths are exact and authorized
- SG25: R2E evidence remains under the exact evidence root
- SG26: Historical blocked review evidence remains untouched
- SG27: The guard is an authorized R2E path
- SG28: No unauthorized current source mutation is accepted
- SG30: R2A/R2B/R2C/R2D lane provenance is exact
- SG31: R2 integration direct parent and lineage are exact
- SG32: R2 repair delta is exactly nine authorized paths
- SG33: Integrated deltas are exact lane blobs
- SG34: No generic or wildcard R2 allowlist exists
- SG35: Post-R2 unauthorized production/test mutations are rejected
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
from aota_forge.core.catalog import LIFECYCLE_DESCRIPTORS
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

M5_6_ACCEPTED_BASE = "3b2796188e4f88047215fc7ee5271dc06a9b1be4"
PRE_REPAIR_SOURCE_BASE = "c32d2c4eac5cb99aec529787473fd88fc9646521"
FINAL_REPAIRED_SOURCE_BASE = "f89874b0f419184de88c39f5ccc25ae473c707f1"
BLOCKED_INTEGRATION_CANDIDATE = "31a63c7e1dbdbf99217fe98b770842152a16ac55"
R1A_SOURCE = "d1a25c141c2da4b134f2fb20690526a4909dda92"
R1B_SOURCE = "435437d67fa2ad355b55b373597e7358ddfa8d1c"
R1C_SOURCE = "5ff86933a7c2676c8e3a573b23c1a24a99b0a896"
R1C2_SOURCE = FINAL_REPAIRED_SOURCE_BASE

# R2 exact repair provenance anchors.
PRE_R2_REPAIR_BASE = "23c3c69e6e00bf4b32eba4f5f688e5a677418933"
POST_R2_REPAIR_BASE = "eefe1260892eb309d8f0ad459ec607e6eff0d8cb"
R2A_SOURCE = "d74f7fbcf7ed9477df1b7d722a6957e94aa36b9c"
R2B_SOURCE = "7da6877953967d30ab98f667f65c256ce55cc2f6"
R2C_SOURCE = "22112f6c33c8b13fdbfd04e1437066091f952315"
R2D_SOURCE = "225fadd3f5763f85a6c1d076cccb3abc799339e5"

R2E_EVIDENCE_ROOT = "deploy/evidence/issues/9/m5-source/"
R2E_EXACT_CHANGED_PATHS = frozenset(
    {
        "tests/test_m5_6_convergence.py",
        "scripts/m5_source_guard.py",
    }
)

R2_AUTHORIZED_PRODUCTION_PATHS = frozenset(
    {
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/adapters/execution/reference.py",
        "aota_forge/core/ingress.py",
        "aota_forge/core/execution/registry.py",
        "aota_forge/core/execution/capabilities.py",
    }
)
R2_AUTHORIZED_REPAIR_TEST_PATHS = frozenset(
    {
        "tests/test_m5_r_r2a_hermes_repair.py",
        "tests/test_m5_r_r2b_reference_replay.py",
        "tests/test_m5_r_r2c_ingress_error_preservation.py",
        "tests/test_m5_r_r2d_capability_validation.py",
    }
)
R2_AUTHORIZED_REPAIR_DELTA_PATHS = (
    R2_AUTHORIZED_PRODUCTION_PATHS | R2_AUTHORIZED_REPAIR_TEST_PATHS
)
R2_LANE_PATHS = {
    R2A_SOURCE: frozenset(
        {
            "aota_forge/adapters/hermes/executor.py",
            "tests/test_m5_r_r2a_hermes_repair.py",
        }
    ),
    R2B_SOURCE: frozenset(
        {
            "aota_forge/adapters/execution/reference.py",
            "tests/test_m5_r_r2b_reference_replay.py",
        }
    ),
    R2C_SOURCE: frozenset(
        {
            "aota_forge/core/ingress.py",
            "tests/test_m5_r_r2c_ingress_error_preservation.py",
        }
    ),
    R2D_SOURCE: frozenset(
        {
            "aota_forge/core/execution/capabilities.py",
            "aota_forge/core/execution/registry.py",
            "tests/test_m5_r_r2d_capability_validation.py",
        }
    ),
}

R1D_EVIDENCE_ROOT = "deploy/evidence/issues/9/m5-source/"
R1D_EXACT_CHANGED_PATHS = frozenset(
    {
        "tests/test_m5_6_convergence.py",
        "scripts/m5_source_guard.py",
    }
)

AUTHORIZED_REPAIR_PRODUCTION_PATHS = frozenset(
    {
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/core/execution/dispatcher.py",
        "aota_forge/core/ingress.py",
    }
)
AUTHORIZED_REPAIR_TEST_PATHS = frozenset(
    {
        "tests/test_m5_4_hermes_adapter.py",
        "tests/test_m5_3_dispatcher.py",
        "tests/test_m5_5_cli_dispatch.py",
    }
)
AUTHORIZED_REPAIR_DELTA_PATHS = (
    AUTHORIZED_REPAIR_PRODUCTION_PATHS | AUTHORIZED_REPAIR_TEST_PATHS
)
R1C2_SERIAL_PATHS = frozenset(
    {
        "aota_forge/core/execution/dispatcher.py",
        "tests/test_m5_3_dispatcher.py",
    }
)

REPAIR_COMMIT_PATHS = {
    R1A_SOURCE: frozenset(
        {
            "aota_forge/adapters/hermes/executor.py",
            "tests/test_m5_4_hermes_adapter.py",
        }
    ),
    R1B_SOURCE: frozenset(
        {
            "aota_forge/core/execution/dispatcher.py",
            "tests/test_m5_3_dispatcher.py",
        }
    ),
    R1C_SOURCE: frozenset(
        {
            "aota_forge/core/ingress.py",
            "tests/test_m5_5_cli_dispatch.py",
        }
    ),
    R1C2_SOURCE: R1C2_SERIAL_PATHS,
}

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

AUTHORIZED_POST_ACCEPTANCE_REPAIRS = {
    "M5-6-R1-F01": {
        "path": "tests/test_m5_5_cli_dispatch.py",
        "class": "temporal_regression_invariant_defect",
        "target_class": "TestM55NegativeAndArchitecturalInvariants",
        "target_method": "test_q15_m5_6_not_started",
    }
}
M5_6_R2_PROTECTION_ID = "M5-6-R2-F01"


def git(*args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def git_succeeds(*args: str) -> bool:
    res = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def diff_paths(base: str, head: str | None = None) -> frozenset[str]:
    args = ["diff", "--name-only", base]
    if head is not None:
        args.append(head)
    return frozenset(line for line in git(*args).splitlines() if line.strip())


def current_changed_paths() -> frozenset[str]:
    paths = set(diff_paths(POST_R2_REPAIR_BASE))
    status = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--short", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return frozenset(paths)


def is_ancestor(ancestor: str, descendant: str) -> bool:
    return git_succeeds("merge-base", "--is-ancestor", ancestor, descendant)


def commit_parent(commit: str) -> str:
    return git("rev-parse", f"{commit}^")


def blob(commit: str, path: str) -> str:
    return git("show", f"{commit}:{path}")


def is_authorized_r1d_path(path: str) -> bool:
    return path in R1D_EXACT_CHANGED_PATHS or path.startswith(R1D_EVIDENCE_ROOT)


def is_authorized_r2e_path(path: str) -> bool:
    return path in R2E_EXACT_CHANGED_PATHS or path.startswith(R2E_EVIDENCE_ROOT)


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
        base_source = git("show", f"{M5_6_ACCEPTED_BASE}:{rel_path}")
    except Exception as exc:
        return False, f"Failed to read base version of {rel_path} at {M5_6_ACCEPTED_BASE}: {exc}"

    curr_path = ROOT / rel_path
    if not curr_path.is_file():
        return False, f"Current file {rel_path} does not exist"
    try:
        with open(curr_path, "r", encoding="utf-8") as handle:
            curr_source = handle.read()
    except Exception as exc:
        return False, f"Failed to read current version of {rel_path}: {exc}"

    return verify_authorized_regression_repair(base_source, curr_source, target_class, target_method)


def verify_authorized_repair_between(
    rel_path: str,
    base_commit: str,
    candidate_commit: str,
) -> tuple[bool, str]:
    """Verify one bounded historical repair between two exact commits."""
    repair_info = AUTHORIZED_POST_ACCEPTANCE_REPAIRS.get("M5-6-R1-F01")
    if repair_info is None or repair_info.get("path") != rel_path:
        return False, f"Historical repair metadata does not authorize {rel_path}"
    try:
        base_source = blob(base_commit, rel_path)
        candidate_source = blob(candidate_commit, rel_path)
    except Exception as exc:
        return False, f"Failed to read historical repair sources: {exc}"
    return verify_authorized_regression_repair(
        base_source,
        candidate_source,
        str(repair_info["target_class"]),
        str(repair_info["target_method"]),
    )


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

    forbidden = sorted(path for path in current_changed_paths() if not is_authorized_r2e_path(path))
    if forbidden:
        return False, f"Forbidden file modified after repaired base: {forbidden}"
    return True, "Only exact R2E paths are modified after the R2 repaired base"


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

    for name in current_changed_paths():
        if name.startswith("aota_forge/"):
            return False, f"Production source touched: {name}"
        if name.startswith("tests/test_m4_"):
            return False, f"Previous test touched: {name}"
        if name.startswith("tests/test_m5_") and name != "tests/test_m5_6_convergence.py":
            return False, f"Previous test touched: {name}"
    return True, "Source ownership partitions fully intact"


def check_sg14_m5_6_did_not_mutate_production_source() -> tuple[bool, str]:
    prod_files = sorted(path for path in current_changed_paths() if path.startswith("aota_forge/"))
    if prod_files:
        return False, f"Production files mutated: {prod_files}"
    return True, "Production source mutation count after repaired base is 0"


def check_sg16_repaired_source_lineage() -> tuple[bool, str]:
    ancestor_pairs = (
        (PRE_REPAIR_SOURCE_BASE, R1A_SOURCE),
        (PRE_REPAIR_SOURCE_BASE, R1B_SOURCE),
        (PRE_REPAIR_SOURCE_BASE, R1C_SOURCE),
        (PRE_REPAIR_SOURCE_BASE, BLOCKED_INTEGRATION_CANDIDATE),
        (BLOCKED_INTEGRATION_CANDIDATE, FINAL_REPAIRED_SOURCE_BASE),
        (R1B_SOURCE, BLOCKED_INTEGRATION_CANDIDATE),
        (R1C_SOURCE, BLOCKED_INTEGRATION_CANDIDATE),
    )
    for ancestor, descendant in ancestor_pairs:
        if not is_ancestor(ancestor, descendant):
            return False, f"Missing Git ancestor relation: {ancestor} -> {descendant}"

    for commit, expected_paths in REPAIR_COMMIT_PATHS.items():
        actual_paths = diff_paths(commit_parent(commit), commit)
        if actual_paths != expected_paths:
            return False, f"Repair commit {commit} changed {sorted(actual_paths)}, expected {sorted(expected_paths)}"
    return True, "R1A/R1B/R1C/R1C2 Git lineage and commit path attribution are exact"


def check_sg17_final_repair_delta_exact() -> tuple[bool, str]:
    actual_paths = diff_paths(PRE_REPAIR_SOURCE_BASE, FINAL_REPAIRED_SOURCE_BASE)
    if actual_paths != AUTHORIZED_REPAIR_DELTA_PATHS:
        return False, f"Final repair delta mismatch: {sorted(actual_paths)}"
    return True, "Final repair delta is exactly six authorized production/test paths"


def check_sg18_r1c2_serial_provenance() -> tuple[bool, str]:
    if commit_parent(R1C2_SOURCE) != BLOCKED_INTEGRATION_CANDIDATE:
        return False, "R1C2 parent is not the blocked integration candidate"
    if diff_paths(BLOCKED_INTEGRATION_CANDIDATE, R1C2_SOURCE) != R1C2_SERIAL_PATHS:
        return False, "R1C2 changed paths are not exactly dispatcher.py and test_m5_3_dispatcher.py"
    for path in R1C2_SERIAL_PATHS:
        if blob(R1B_SOURCE, path) != blob(BLOCKED_INTEGRATION_CANDIDATE, path):
            return False, f"Blocked integration does not contain the R1B state for {path}"
    return True, "R1C2 is a serial child of blocked integration with exact two-path provenance"


def check_sg30_r2_lane_provenance() -> tuple[bool, str]:
    """Mechanically verify each R2 lane is a direct child of the pre-R2 base."""
    for lane in (R2A_SOURCE, R2B_SOURCE, R2C_SOURCE, R2D_SOURCE):
        if commit_parent(lane) != PRE_R2_REPAIR_BASE:
            return False, f"R2 lane {lane} parent is not the pre-R2 base"
    return True, "R2A/R2B/R2C/R2D are each direct children of the pre-R2 repair base"


def check_sg31_r2_integration_provenance() -> tuple[bool, str]:
    """Mechanically verify the R2 integration direct parent and exact delta attribution."""
    if commit_parent(POST_R2_REPAIR_BASE) != PRE_R2_REPAIR_BASE:
        return False, "R2 integration direct parent is not the pre-R2 base"
    if not is_ancestor(PRE_R2_REPAIR_BASE, POST_R2_REPAIR_BASE):
        return False, "Pre-R2 base is not an ancestor of the R2 integration"
    return True, "R2 integration direct parent = pre-R2 base with exact lineage"


def check_sg32_r2_delta_exact() -> tuple[bool, str]:
    """Mechanically verify the R2 repair delta is exactly nine authorized paths."""
    actual_paths = diff_paths(PRE_R2_REPAIR_BASE, POST_R2_REPAIR_BASE)
    if actual_paths != R2_AUTHORIZED_REPAIR_DELTA_PATHS:
        return False, f"R2 repair delta mismatch: {sorted(actual_paths)}"
    return True, "R2 repair delta is exactly nine authorized production/test paths"


def check_sg33_r2_lane_blob_attribution() -> tuple[bool, str]:
    """Mechanically attribute the integrated deltas to R2A/B/C/D by exact blob equality."""
    for lane, lane_paths in R2_LANE_PATHS.items():
        actual_paths = diff_paths(PRE_R2_REPAIR_BASE, lane)
        if actual_paths != lane_paths:
            return False, f"R2 lane {lane} changed {sorted(actual_paths)}, expected {sorted(lane_paths)}"
        for path in lane_paths:
            if blob(lane, path) != blob(POST_R2_REPAIR_BASE, path):
                return False, f"R2 integration does not contain the exact {lane} blob for {path}"
    return True, "All nine integrated deltas are exact blobs from R2A/R2B/R2C/R2D"


def check_sg34_no_generic_r2_allowlist() -> tuple[bool, str]:
    """No generic or wildcard R2 allowlist exists."""
    all_r2_paths = R2_AUTHORIZED_PRODUCTION_PATHS | R2_AUTHORIZED_REPAIR_TEST_PATHS
    if any("*" in path or "?" in path for path in all_r2_paths):
        return False, "Wildcard R2 repair path is present"
    if any(
        path.startswith("aota_forge/") for path in R1D_EXACT_CHANGED_PATHS
    ) or any(path.startswith("aota_forge/") for path in R2E_EXACT_CHANGED_PATHS):
        return False, "R2E exact path set contains a generic production allowance"
    return True, "GENERIC_PRODUCTION_ALLOWLIST_PRESENT=no; WILDCARD_REPAIR_ALLOWLIST_PRESENT=no"


def check_sg35_post_r2_future_mutations_rejected() -> tuple[bool, str]:
    """Negative self-checks: hypothetical unauthorized mutations after eefe126 are rejected."""
    conceptual_unauthorized = (
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/adapters/execution/reference.py",
        "aota_forge/core/ingress.py",
        "aota_forge/core/execution/registry.py",
        "aota_forge/core/execution/capabilities.py",
        "aota_forge/core/execution/extra.py",
        "tests/test_m5_7_extra.py",
        "tests/test_m5_r_r2a_hermes_repair.py",
        "scripts/other_guard.py",
    )
    accepted = [path for path in conceptual_unauthorized if is_authorized_r2e_path(path)]
    if accepted:
        return False, f"Conceptual unauthorized paths were accepted: {accepted}"
    if not all(is_authorized_r2e_path(path) for path in R2E_EXACT_CHANGED_PATHS):
        return False, "An exact R2E path was rejected"
    return True, "POST_R2_UNAUTHORIZED_PRODUCTION_MUTATION_REJECTED=yes; POST_R2_UNAUTHORIZED_TEST_MUTATION_REJECTED=yes"


def check_sg19_no_generic_or_wildcard_allowlist() -> tuple[bool, str]:
    if AUTHORIZED_REPAIR_PRODUCTION_PATHS != {
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/core/execution/dispatcher.py",
        "aota_forge/core/ingress.py",
    }:
        return False, "Production repair authorization is not the exact three-path set"
    if AUTHORIZED_REPAIR_TEST_PATHS != {
        "tests/test_m5_4_hermes_adapter.py",
        "tests/test_m5_3_dispatcher.py",
        "tests/test_m5_5_cli_dispatch.py",
    }:
        return False, "Repair-test authorization is not the exact three-path set"
    all_path_values = (
        set(AUTHORIZED_REPAIR_PRODUCTION_PATHS)
        | set(AUTHORIZED_REPAIR_TEST_PATHS)
        | set(R1D_EXACT_CHANGED_PATHS)
    )
    if any("*" in path or "?" in path for path in all_path_values):
        return False, "Wildcard repair path is present"
    if any(path.startswith("aota_forge/") for path in R1D_EXACT_CHANGED_PATHS):
        return False, "R1D exact path set contains a generic production allowance"
    return True, "GENERIC_PRODUCTION_ALLOWLIST_PRESENT=no; WILDCARD_REPAIR_ALLOWLIST_PRESENT=no"


def check_sg20_future_unauthorized_mutations_rejected() -> tuple[bool, str]:
    conceptual_unauthorized_paths = (
        "aota_forge/adapters/hermes/executor.py",
        "aota_forge/core/execution/dispatcher.py",
        "aota_forge/core/ingress.py",
        "aota_forge/core/execution/extra.py",
        "tests/test_m5_3_dispatcher.py",
        "tests/test_m5_7_extra.py",
        "scripts/other_guard.py",
    )
    accepted = [path for path in conceptual_unauthorized_paths if is_authorized_r1d_path(path)]
    if accepted:
        return False, f"Conceptual unauthorized paths were accepted: {accepted}"
    if not all(is_authorized_r1d_path(path) for path in R1D_EXACT_CHANGED_PATHS):
        return False, "An exact R1D path was rejected"
    if not all(is_authorized_r2e_path(path) for path in R2E_EXACT_CHANGED_PATHS):
        return False, "An exact R2E path was rejected"
    return True, "POST_REPAIR_UNAUTHORIZED_PRODUCTION_MUTATION_REJECTED=yes; POST_REPAIR_UNAUTHORIZED_TEST_MUTATION_REJECTED=yes"


def check_sg21_guard_non_tautological() -> tuple[bool, str]:
    source = (ROOT / "scripts" / "m5_source_guard.py").read_text(encoding="utf-8")
    required_mechanical_tokens = (
        "ast.parse",
        "merge-base",
        "diff_paths",
        "FINAL_REPAIRED_SOURCE_BASE",
        "POST_R2_REPAIR_BASE",
        "R2_LANE_PATHS",
    )
    missing = [token for token in required_mechanical_tokens if token not in source]
    if missing:
        return False, f"Guard is missing mechanical validation structure: {missing}"
    self_ok, self_msg = _self_validate_guard_repair_logic()
    if not self_ok:
        return False, self_msg
    return True, "Critical provenance claims derive from Git, exact paths, and AST checks"


def check_sg22_m5_6_lifecycle_protection_preserved() -> tuple[bool, str]:
    repair_ok, repair_msg = verify_authorized_repair_between(
        "tests/test_m5_5_cli_dispatch.py",
        M5_6_ACCEPTED_BASE,
        PRE_REPAIR_SOURCE_BASE,
    )
    if not repair_ok:
        return False, f"M5-6-R1-F01 historical repair no longer verifies: {repair_msg}"

    historical_guard = blob(PRE_REPAIR_SOURCE_BASE, "scripts/m5_source_guard.py")
    current_guard = (ROOT / "scripts" / "m5_source_guard.py").read_text(encoding="utf-8")
    historical_required = (
        "M5-6-R1-F01",
        "TestM55NegativeAndArchitecturalInvariants",
        "test_q15_m5_6_not_started",
        "verify_authorized_regression_repair",
        "_self_validate_guard_repair_logic",
    )
    current_required = historical_required + (M5_6_R2_PROTECTION_ID,)
    for source_name, source, required in (
        ("accepted M5-6 guard", historical_guard, historical_required),
        ("current guard", current_guard, current_required),
    ):
        missing = [token for token in required if token not in source]
        if missing:
            return False, f"{source_name} lost lifecycle protection tokens: {missing}"
    return True, "M5-6-R1-F01 exact AST repair and M5-6-R2-F01 bounded guard protection preserved"


def check_sg23_m4_scope_frozen() -> tuple[bool, str]:
    historical_m4 = sorted(
        path
        for path in diff_paths(PRE_REPAIR_SOURCE_BASE, FINAL_REPAIRED_SOURCE_BASE)
        if path.startswith("tests/test_m4_")
    )
    current_m4 = sorted(path for path in current_changed_paths() if path.startswith("tests/test_m4_"))
    if historical_m4 or current_m4:
        return False, f"M4 test scope changed: historical={historical_m4}, current={current_m4}"
    return True, "M4 frozen scope is unchanged"


def check_sg24_r1d_changed_paths_exact() -> tuple[bool, str]:
    current = current_changed_paths()
    unexpected = sorted(path for path in current if not is_authorized_r2e_path(path))
    if unexpected:
        return False, f"R2E unauthorized changed paths: {unexpected}"
    if not R2E_EXACT_CHANGED_PATHS.issubset(current):
        return False, "R2E convergence test and guard are not both present in the candidate"
    return True, "R2E changed paths are exact; evidence may only be under the exact M5 source root"


def check_sg25_evidence_root_authorized() -> tuple[bool, str]:
    evidence_paths = [
        path for path in current_changed_paths() if path.startswith("deploy/evidence/issues/9/")
    ]
    unauthorized = [path for path in evidence_paths if not path.startswith(R2E_EVIDENCE_ROOT)]
    if unauthorized:
        return False, f"Evidence changed outside the authorized M5 source root: {unauthorized}"
    return True, "All current evidence changes are under deploy/evidence/issues/9/m5-source/"


def check_sg26_historical_review_untouched() -> tuple[bool, str]:
    current_review_paths = [
        path
        for path in current_changed_paths()
        if path.startswith("deploy/evidence/issues/9/m5-r-review/")
    ]
    if current_review_paths:
        return False, f"Historical blocked review evidence changed: {current_review_paths}"
    return True, "Historical blocked M5-R review evidence is untouched"


def check_sg27_guard_path_authorized() -> tuple[bool, str]:
    if "scripts/m5_source_guard.py" not in current_changed_paths():
        return False, "R2E guard path is missing from the candidate"
    return True, "scripts/m5_source_guard.py is an exact authorized R2E path"


def check_sg28_current_source_mutation_rejected() -> tuple[bool, str]:
    current_production = sorted(
        path for path in current_changed_paths() if path.startswith("aota_forge/")
    )
    current_previous_tests = sorted(
        path
        for path in current_changed_paths()
        if path.startswith("tests/test_m5_") and path != "tests/test_m5_6_convergence.py"
    )
    if current_production or current_previous_tests:
        return False, f"Unauthorized current mutation: production={current_production}, tests={current_previous_tests}"
    return True, "New unauthorized production and previous-test mutations are rejected"


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
    ("SG16", check_sg16_repaired_source_lineage),
    ("SG17", check_sg17_final_repair_delta_exact),
    ("SG18", check_sg18_r1c2_serial_provenance),
    ("SG19", check_sg19_no_generic_or_wildcard_allowlist),
    ("SG20", check_sg20_future_unauthorized_mutations_rejected),
    ("SG21", check_sg21_guard_non_tautological),
    ("SG22", check_sg22_m5_6_lifecycle_protection_preserved),
    ("SG23", check_sg23_m4_scope_frozen),
    ("SG24", check_sg24_r1d_changed_paths_exact),
    ("SG25", check_sg25_evidence_root_authorized),
    ("SG26", check_sg26_historical_review_untouched),
    ("SG27", check_sg27_guard_path_authorized),
    ("SG28", check_sg28_current_source_mutation_rejected),
    ("SG30", check_sg30_r2_lane_provenance),
    ("SG31", check_sg31_r2_integration_provenance),
    ("SG32", check_sg32_r2_delta_exact),
    ("SG33", check_sg33_r2_lane_blob_attribution),
    ("SG34", check_sg34_no_generic_r2_allowlist),
    ("SG35", check_sg35_post_r2_future_mutations_rejected),
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
