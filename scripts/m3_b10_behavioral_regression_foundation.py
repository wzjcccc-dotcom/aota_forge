#!/usr/bin/env python3
"""M3-B10 Behavioral Regression Foundation Test Harness (Issue #9, lane M3-B10).

Executes end-to-end behavioral proofs across the frozen 16-class regression
matrix over the accepted integrated M3 foundation (B3-B9).

Usage:
    python3 scripts/m3_b10_behavioral_regression_foundation.py
    python3 scripts/m3_b10_behavioral_regression_foundation.py --json
    python3 scripts/m3_b10_behavioral_regression_foundation.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aota_forge.core.regression.fixtures import (
    FIXTURES_REQUIRE_DEPLOYMENT,
    FIXTURES_REQUIRE_HERMES_RUNTIME,
    FIXTURES_REQUIRE_LIVE_GITHUB,
    FIXTURES_REQUIRE_NETWORK,
    FIXTURES_REQUIRE_SHADOW_GRAPH,
    FIXTURES_USE_PRODUCTION_AUTHORITY_STATE,
)
from aota_forge.core.regression.matrix import (
    ALL_16_CLASSES_MAPPED,
    B10_REGRESSION_MATRIX_IS_FROZEN_INPUT,
    FROZEN_INVENTORY,
    REGRESSION_MATRIX_CLASS_COUNT,
    REGRESSION_MATRIX_MUTATED,
    REQUIRED_FAILURE_CLASSES,
)
from aota_forge.core.regression.runner import (
    BehavioralRegressionRunner,
    RegressionReport,
)

EXPECTED_BASE_COMMIT = "8ecf117b7c75b1833c1c0d2f607de528cb717fe6"


def verify_base_commit() -> tuple[bool, str]:
    """Check git base commit provenance."""
    try:
        merge_base = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "HEAD", EXPECTED_BASE_COMMIT],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if merge_base == EXPECTED_BASE_COMMIT:
            return True, f"Base matches {EXPECTED_BASE_COMMIT}"
        return False, f"Merge base {merge_base} does not match expected {EXPECTED_BASE_COMMIT}"
    except Exception as exc:
        return False, f"Git base verification error: {exc}"


def run_self_tests() -> tuple[bool, list[str]]:
    """Assert matrix invariants, rejection rules, and negative guard behaviors."""
    findings: list[str] = []

    # 1. Matrix inventory count and frozenness
    if len(FROZEN_INVENTORY) != 16:
        findings.append(f"Expected 16 inventory entries, got {len(FROZEN_INVENTORY)}")
    if REGRESSION_MATRIX_CLASS_COUNT != 16:
        findings.append(f"Expected REGRESSION_MATRIX_CLASS_COUNT=16, got {REGRESSION_MATRIX_CLASS_COUNT}")
    if REGRESSION_MATRIX_MUTATED:
        findings.append("REGRESSION_MATRIX_MUTATED must be False")
    if not B10_REGRESSION_MATRIX_IS_FROZEN_INPUT:
        findings.append("B10_REGRESSION_MATRIX_IS_FROZEN_INPUT must be True")
    if not ALL_16_CLASSES_MAPPED:
        findings.append("ALL_16_CLASSES_MAPPED must be True")

    # 2. Strict isolation assertions
    if FIXTURES_USE_PRODUCTION_AUTHORITY_STATE:
        findings.append("FIXTURES_USE_PRODUCTION_AUTHORITY_STATE must be False")
    if FIXTURES_REQUIRE_DEPLOYMENT:
        findings.append("FIXTURES_REQUIRE_DEPLOYMENT must be False")
    if FIXTURES_REQUIRE_SHADOW_GRAPH:
        findings.append("FIXTURES_REQUIRE_SHADOW_GRAPH must be False")
    if FIXTURES_REQUIRE_LIVE_GITHUB:
        findings.append("FIXTURES_REQUIRE_LIVE_GITHUB must be False")
    if FIXTURES_REQUIRE_HERMES_RUNTIME:
        findings.append("FIXTURES_REQUIRE_HERMES_RUNTIME must be False")
    if FIXTURES_REQUIRE_NETWORK:
        findings.append("FIXTURES_REQUIRE_NETWORK must be False")

    # 3. Class dispatch coverage
    runner = BehavioralRegressionRunner()
    for c in REQUIRED_FAILURE_CLASSES:
        if c not in runner.class_dispatch:
            findings.append(f"Class dispatch missing {c}")

    passed = len(findings) == 0
    return passed, findings


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-B10 Behavioral Regression Foundation Test Harness")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    parser.add_argument("--self-test", action="store_true", help="Run harness self-tests and invariant guards")
    args = parser.parse_args()

    if args.self_test:
        passed, findings = run_self_tests()
        if args.json:
            print(json.dumps({"self_test_passed": passed, "findings": findings}, indent=2))
        else:
            print("================================================================")
            print("M3-B10 Behavioral Regression Foundation Harness Self-Test")
            print("================================================================")
            if passed:
                print("SELF-TEST: PASS (all matrix invariants and isolation assertions hold)")
            else:
                print(f"SELF-TEST: FAIL ({len(findings)} findings)")
                for f in findings:
                    print(f"  - {f}")
        return 0 if passed else 1

    runner = BehavioralRegressionRunner()
    report = runner.run_all()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.verdict == "PASS" else 1

    print("================================================================")
    print("M3-B10 Behavioral Regression Foundation Suite")
    print("================================================================")
    print(f"Verdict:                        {report.verdict}")
    print(f"Matrix Class Count:             {report.matrix_class_count}")
    print(f"Matrix Mutated:                 {'yes' if report.matrix_mutated else 'no'}")
    print(f"All 16 Classes MAPPED:          {'yes' if report.all_16_classes_mapped else 'no'}")
    print(f"Class Result Count:             {report.class_result_count}/16")
    print(f"Behavioral Class Pass Count:    {report.behavioral_class_pass_count}/16")
    print(f"Negative Guard Pass Count:      {report.negative_guard_pass_count}/16")
    print("----------------------------------------------------------------")
    print("16-Class Regression Proof Outcomes:")
    for name, res in report.class_results.items():
        status = "PASS" if res.passed else "FAIL"
        print(f"  [{status}] {name:38} : {res.detail}")

    print("----------------------------------------------------------------")
    print("19 Required Behavioral Proof Outcomes:")
    for name, res in report.required_proof_results.items():
        status = "PASS" if res.passed else "FAIL"
        print(f"  [{status}] {name:42} : {res.detail}")

    if report.findings:
        print("----------------------------------------------------------------")
        print("Findings / Errors:")
        for finding in report.findings:
            print(f"  - {finding}")

    print("================================================================")
    return 0 if report.verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
