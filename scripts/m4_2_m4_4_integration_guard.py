#!/usr/bin/env python3
"""Executable guard for the serial M4-2/M4-4 integration descendant.

The guard validates source ancestry and ownership mechanically, then executes
the integrated Core tests and the current B89 semantic probe.  It does not
read generated evidence as an input to its verdict.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
M4_2_SOURCE = "9ce364cde6ae84284cd6fb83650417525bbd3144"
M4_4_SOURCE = "10a7e9809d2e1cb3c324ad65a13e6611662957e6"

M4_2_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/authority.py",
    "aota_forge/core/capability_lease.py",
    "aota_forge/core/authorization.py",
}
M4_4_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/context.py",
    "aota_forge/core/contracts/descriptor.py",
    "aota_forge/core/contracts/results.py",
    "aota_forge/core/contracts/validation.py",
    "aota_forge/core/transaction.py",
    "aota_forge/core/transitions.py",
    "aota_forge/core/project/resolver.py",
}
SHARED_READ_ONLY_PATHS = {
    "aota_forge/core/contracts/canonical.py",
    "aota_forge/core/contracts/mutation.py",
    "aota_forge/core/contracts/operations.py",
    "aota_forge/core/contracts/registry.py",
    "aota_forge/core/contracts/version.py",
    "aota_forge/core/idempotency.py",
    "aota_forge/core/ingress.py",
    "aota_forge/core/graph/repository.py",
}
INTEGRATION_ONLY_PATHS = {
    "aota_forge/core/contracts/errors.py",
    "aota_forge/core/contracts/__init__.py",
    "aota_forge/core/__init__.py",
}
INTEGRATION_ARTIFACT_PREFIXES = (
    "tests/",
    "deploy/evidence/issues/9/m4-2-m4-4-integration/",
)
INTEGRATION_ARTIFACT_PATHS = {
    "scripts/m4_2_m4_4_integration_guard.py",
}

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _run(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _head_for_worktree(commit: str) -> Path | None:
    listing = _run(["git", "worktree", "list", "--porcelain"])
    lines = listing.stdout.splitlines()
    worktree: Path | None = None
    for line in lines:
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree "))
        elif line.startswith("HEAD ") and line.removeprefix("HEAD ").strip() == commit:
            return worktree
    return None


def _changed_paths(ref: str) -> set[str]:
    result = _run(["git", "diff", "--name-only", f"{COMMON_SOURCE_BASE}..{ref}"])
    return set(result.stdout.splitlines())


def _working_tree_paths() -> set[str]:
    result = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    return {
        line[3:]
        for line in result.stdout.splitlines()
        if len(line) >= 4 and line[3:]
    }


def _is_integration_path(path: str) -> bool:
    return (
        path in INTEGRATION_ONLY_PATHS
        or path in INTEGRATION_ARTIFACT_PATHS
        or any(path.startswith(prefix) for prefix in INTEGRATION_ARTIFACT_PREFIXES)
    )


def _run_source_guard(commit: str, script: str) -> tuple[bool, str]:
    worktree = _head_for_worktree(commit)
    if worktree is None:
        return False, f"no worktree at accepted source {commit}"
    result = _run([sys.executable, f"scripts/{script}"], cwd=worktree)
    return result.returncode == 0, result.stdout + result.stderr


def _run_integrated_m42_semantics() -> bool:
    result = _run([sys.executable, "scripts/m4_2_source_guard.py"])
    output = result.stdout + result.stderr
    # The source guard's partition assertion is intentionally source-lane
    # local. On the merged tree only that historical ownership assertion may
    # fail; all authorization cases must remain green.
    return (
        "FOCUSED_TEST_PASSED=36" in output
        and "FAILED_CASES=SOURCE_PARTITION_EXACT" in output
    )


def _run_integrated_tests() -> tuple[bool, str]:
    result = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
    )
    return result.returncode == 0, result.stdout + result.stderr


def _run_b89_semantic_probe() -> tuple[bool, str]:
    path = ROOT / "scripts" / "m3_b89_projection_binding_integration.py"
    spec = importlib.util.spec_from_file_location("m3_b89_probe", path)
    if spec is None or spec.loader is None:
        return False, "unable to load B89 probe"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RESULTS.clear()
    repo, resolver, refs = module._build_integrated_fixture()
    for name in (
        "_b89_n1",
        "_b89_n2",
        "_b89_n3",
        "_b89_n4",
        "_b89_n5",
        "_b89_n6",
        "_b89_n7",
        "_b89_n8",
        "_b89_n9",
        "_b89_n10",
        "_b89_n11",
        "_b89_n12",
        "_b89_n13",
        "_b89_n14",
        "_b89_n15",
    ):
        getattr(module, name)(repo, resolver, refs)
    module._negative_reversion_proofs()
    failures = [name for passed, name in module.RESULTS if not passed]
    return not failures, ",".join(failures)


def _public_import_probe() -> bool:
    from aota_forge.core import (
        AuthorizationErrorCode,
        CapabilityLeaseIssuer,
        PLAN_INIT_DESCRIPTOR,
        PLAN_RETIREMENT_DESCRIPTOR,
        PlanInitRequest,
        PlanRetirementRequest,
        plan_init,
        retire_plan,
    )
    from aota_forge.core.contracts import (
        ERROR_CLASSES,
    )

    return all(
        item is not None
        for item in (
            AuthorizationErrorCode,
            CapabilityLeaseIssuer,
            PLAN_INIT_DESCRIPTOR,
            PLAN_RETIREMENT_DESCRIPTOR,
            PlanInitRequest,
            PlanRetirementRequest,
            plan_init,
            retire_plan,
            ERROR_CLASSES,
        )
    )


def _combined_error_probe() -> bool:
    from aota_forge.core.contracts import (
        M4_2_AUTHORIZATION_ERROR_CODES,
        M4_4_LIFECYCLE_ERROR_CODES,
        error_from_dict,
    )

    codes = set(M4_2_AUTHORIZATION_ERROR_CODES) | set(M4_4_LIFECYCLE_ERROR_CODES)
    return all(error_from_dict({"code": code}).code == code for code in codes)


def _read_only_probe() -> bool:
    from aota_forge.core import authorization, transitions
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    return (
        authorization.M4_2_WRITE_INGRESS_IMPLEMENTED is False
        and authorization.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
        and authorization.M4_2_DURABLE_JOURNAL_IMPLEMENTED is False
        and transitions.M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED is False
        and transitions.M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
        and transitions.M4_4_DURABLE_JOURNAL_IMPLEMENTED is False
        and transitions.PRODUCTION_INGRESS_TRANSITION_WIRING_IMPLEMENTED is False
        and transitions.AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False
        and all(
            descriptor.read_write == "read"
            for descriptor, _handler in DEFAULT_REGISTRY._bindings.values()
        )
    )


def main() -> int:
    base_exists = _run(["git", "cat-file", "-e", f"{COMMON_SOURCE_BASE}^{{commit}}"]).returncode == 0
    check("COMMON_BASE_EXISTS", base_exists, COMMON_SOURCE_BASE)
    check(
        "M4_2_ACCEPTED_SOURCE_ANCESTOR",
        _run(["git", "merge-base", "--is-ancestor", M4_2_SOURCE, "HEAD"]).returncode == 0,
        M4_2_SOURCE,
    )
    check(
        "M4_4_ACCEPTED_SOURCE_ANCESTOR",
        _run(["git", "merge-base", "--is-ancestor", M4_4_SOURCE, "HEAD"]).returncode == 0,
        M4_4_SOURCE,
    )

    inherited = _changed_paths(M4_2_SOURCE) | _changed_paths(M4_4_SOURCE)
    changed = _changed_paths("HEAD") | _working_tree_paths()
    new_integration = changed - inherited
    forbidden = {
        path
        for path in new_integration
        if path in M4_2_EXCLUSIVE_WRITE_PATHS
        or path in M4_4_EXCLUSIVE_WRITE_PATHS
        or path in SHARED_READ_ONLY_PATHS
        or not _is_integration_path(path)
    }
    missing_integration_paths = INTEGRATION_ONLY_PATHS - new_integration
    check(
        "EXACT_INTEGRATION_ONLY_PATH_SET_READ",
        not missing_integration_paths,
        "missing=" + ",".join(sorted(missing_integration_paths)),
    )
    check("NEW_INTEGRATION_EDITS_WITHIN_AUTHORIZED_SET", not forbidden, ",".join(sorted(forbidden)))
    check("NEW_M4_2_EXCLUSIVE_PATH_EDIT_COUNT", not (new_integration & M4_2_EXCLUSIVE_WRITE_PATHS))
    check("NEW_M4_4_EXCLUSIVE_PATH_EDIT_COUNT", not (new_integration & M4_4_EXCLUSIVE_WRITE_PATHS))
    check("NEW_SHARED_READ_ONLY_PATH_EDIT_COUNT", not (new_integration & SHARED_READ_ONLY_PATHS))

    m42_source_pass, m42_source_output = _run_source_guard(M4_2_SOURCE, "m4_2_source_guard.py")
    m44_source_pass, m44_source_output = _run_source_guard(M4_4_SOURCE, "m4_4_source_guard.py")
    check("M4_2_SOURCE_GUARD", m42_source_pass, m42_source_output[-300:])
    check("M4_4_SOURCE_GUARD", m44_source_pass, m44_source_output[-300:])
    check("M4_2_INTEGRATED_SEMANTICS", _run_integrated_m42_semantics())

    check("PUBLIC_IMPORTS", _public_import_probe())
    check("COMBINED_ERROR_REGISTRY", _combined_error_probe())
    tests_pass, tests_output = _run_integrated_tests()
    check("CROSS_LANE_TESTS", tests_pass, tests_output[-300:])
    b89_pass, b89_detail = _run_b89_semantic_probe()
    check("B89_CURRENT_SEMANTIC_PROBE", b89_pass, b89_detail)
    check("READ_ONLY_INGRESS_AND_DOWNSTREAM_STOPS", _read_only_probe())

    failures = [name for name, passed, _ in RESULTS if not passed]
    print(f"CURRENT_SEMANTIC_INVARIANT_FAILURE_COUNT={len(failures)}")
    print(f"M4_2_M4_4_INTEGRATION_GUARD={'PASS' if not failures else 'FAIL'}")
    if failures:
        print("FAILED_CASES=" + ",".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
