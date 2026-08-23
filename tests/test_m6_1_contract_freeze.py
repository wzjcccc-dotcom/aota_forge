"""M6-1 contract freeze focused tests (Issue #9, M6-1).

Exercises scripts/m6_1_contract_guard.py against the accepted planning base:
positive freeze proof, structural canonical-shape proofs, negative rejection
probes (N-M6-01/N-M6-02/N-M6-21), fail-closed authority handling, and
non-tautological evidence independence.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "m6_1_contract_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("m6_1_contract_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["m6_1_contract_guard"] = module
    spec.loader.exec_module(module)
    return module


def _fresh_inputs(guard):
    head = guard.try_git("rev-parse", "HEAD")
    return guard.GuardInputs(repo_root=guard.ROOT, source_root=guard.ROOT, head_commit=head)


def _run_all_checks(guard):
    results, passed = guard.run_full_guard(_fresh_inputs(guard))
    failed = [r for r in results if not r.passed]
    return results, failed


def test_valid_accepted_planning_base_passes_end_to_end():
    proc = subprocess.run(
        [sys.executable, str(GUARD_PATH)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout
    assert "M6_1_CONTRACT_GUARD=PASS" in proc.stdout


def test_all_twelve_frozen_contracts_recognized():
    guard = _load_guard()
    records, problems = guard.build_frozen_contract_records(_fresh_inputs(guard))
    assert problems == []
    assert len(records) == 12
    ids = {r.contract_id for r in records}
    expected = {
        "ExecutorCapabilities",
        "ExecutionPackage",
        "CanonicalTaskState",
        "CanonicalResult",
        "RoleMapping",
        "ExecutorAdapter",
        "ExecutorRegistry",
        "ExecutionDispatcher",
        "Unified Ingress execution descriptors",
        "CLI execution projection",
        "Hermes adapter",
        "Reference adapter",
    }
    assert ids == expected


def test_zero_core_schema_rewrite_and_byte_identity():
    guard = _load_guard()
    inputs = _fresh_inputs(guard)
    records, problems = guard.build_frozen_contract_records(inputs)
    assert problems == []
    results = {r.name: r for r in guard.evaluate_source_tree(inputs, records)}
    assert results["core_schema_rewrite_count_zero"].passed
    assert "CORE_SCHEMA_REWRITE_COUNT=0" in results["core_schema_rewrite_count_zero"].detail
    assert results["byte_frozen_contracts_identical_to_m5_kgc"].passed
    assert results["structural_mode_signatures_equal_to_m5_kgc"].passed


def test_execution_package_private_field_count_zero():
    guard = _load_guard()
    inputs = _fresh_inputs(guard)
    records, problems = guard.build_frozen_contract_records(inputs)
    assert problems == []
    results = {r.name: r for r in guard.evaluate_source_tree(inputs, records)}
    check = results["execution_package_executor_neutral_field_count_zero"]
    assert check.passed
    assert "EXECUTION_PACKAGE_EXECUTOR_PRIVATE_FIELD_COUNT=0" in check.detail


def test_canonical_role_set_unchanged():
    guard = _load_guard()
    roles_mod = _import_runtime("aota_forge.core.execution.roles")
    assert tuple(roles_mod.CANONICAL_ROLES) == ("planner", "coder", "reviewer", "steward", "executor")
    inputs = _fresh_inputs(guard)
    records, _ = guard.build_frozen_contract_records(inputs)
    results = {r.name: r for r in guard.evaluate_source_tree(inputs, records)}
    assert results["canonical_role_set_frozen"].passed
    assert "EXECUTOR_SPECIFIC_CANONICAL_ROLE_COUNT=0" in results["canonical_role_set_frozen"].detail


def test_canonical_task_state_set_unchanged():
    state_mod = _import_runtime("aota_forge.core.execution.state")
    states = tuple(s.value for s in state_mod.CANONICAL_TASK_STATES)
    assert len(states) == 9
    assert set(states) == {
        "CREATED",
        "ACCEPTED",
        "QUEUED",
        "RUNNING",
        "WAITING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "UNKNOWN",
    }
    assert state_mod.CanonicalTaskState.UNKNOWN.is_terminal is False
    for terminal in ("COMPLETED", "FAILED", "CANCELLED"):
        assert state_mod.parse_state(terminal).is_terminal is True


def test_executor_adapter_method_set_unchanged():
    adapter_mod = _import_runtime("aota_forge.core.execution.adapter")
    abstract_methods = {
        name
        for name in vars(adapter_mod.ExecutorAdapter)
        if getattr(getattr(adapter_mod.ExecutorAdapter, name), "__isabstractmethod__", False)
    }
    assert abstract_methods == {
        "capabilities",
        "validate_package",
        "dispatch",
        "status",
        "result",
        "cancel",
        "resume",
    }
    assert not any(name.startswith(("codex_", "hermes_")) for name in abstract_methods)


def test_pos_m6_02_execution_package_remains_executor_neutral_for_codex():
    package_mod = _import_runtime("aota_forge.core.execution.package")
    package = package_mod.ExecutionPackage.create(
        canonical_task_id="task-pos-m6-02-test",
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Execute coding step via codex-targeting constraints",
        capability_requirements={"supported_execution_modes": ["sync"]},
        constraints={"execution_target": "codex"},
    )
    dumped = package.to_dict()
    forbidden_prefixes = ("hermes_", "codex_", "profile_", "command_", "shell_")
    leaked = [
        k
        for k in dumped
        if k.startswith(forbidden_prefixes) or k in {"preferred_executor", "fallback_executor"}
    ]
    assert leaked == []
    recomputed = package_mod.compute_intent_fingerprint(
        canonical_role=package.canonical_role,
        instruction=package.instruction,
        input_artifacts=(),
        project_id=package.project_id,
    )
    assert package.intent_fingerprint == recomputed


def test_n_m6_01_rejected():
    guard = _load_guard()
    result = guard.negative_probe_n_m6_01()
    assert result.passed, result.detail


def test_n_m6_02_rejected():
    guard = _load_guard()
    result = guard.negative_probe_n_m6_02()
    assert result.passed, result.detail


def test_n_m6_21_rejected():
    guard = _load_guard()
    result = guard.negative_probe_n_m6_21()
    assert result.passed, result.detail


def test_unknown_untracked_drift_fails_closed():
    guard = _load_guard()

    def factory(inp):
        return guard.build_frozen_contract_records(inp)

    probes = guard.fail_closed_probe_results(factory)
    by_name = {p.name: p for p in probes}
    expected_probes = {
        "fail_closed_missing_m5_kgc",
        "fail_closed_missing_planning_base",
        "fail_closed_missing_planning_artifact",
        "fail_closed_missing_frozen_source_path",
        "fail_closed_duplicate_contract_id",
        "fail_closed_unknown_freeze_mode",
        "fail_closed_malformed_expected_blob",
    }
    assert expected_probes <= set(by_name)
    for name in expected_probes:
        assert by_name[name].passed, f"{name} did not fail closed: {by_name[name].detail}"


def test_nontautology_independent_of_evidence_content():
    guard = _load_guard()
    result = guard.non_tautology_result()
    assert result.passed, result.detail


def test_future_boundary_not_overbroad():
    guard = _load_guard()
    result = guard.future_sensitivity_result()
    assert result.passed, result.detail


def test_full_guard_suite_reports_zero_failures():
    guard = _load_guard()
    results, failed = _run_all_checks(guard)
    names = [f.name for f in failed]
    assert failed == [], f"failing checks: {names}"
    assert len(results) >= 30


def _import_runtime(module_name: str):
    sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module_name)
    finally:
        sys.path.pop(0)
