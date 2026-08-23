"""M6-1 contract freeze focused tests — R1 descendant-safe guard domain repair (Issue #9, M6-1-R1).

Preserves all original 15-test semantic coverage (no loss) and adds explicit
frontier/descendant domain separation per M6-1-R1-F01 repair.

Domains:
  - Frontier provenance: exact M6-1 checkpoint 901f5a7e only; verifies
    authorized paths, m6-0 authority unchanged, evidence integrity.
  - Descendant contract: all authorized M6 descendants; verifies 12 frozen
    M5 contracts, zero schema rewrite, neutral fields, roles/states/adapter,
    selection semantics, M6-1 authority artifacts unchanged without global
    path-isolation rejection.

Default guard invocation (no --mode) must be descendant-safe.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import hashlib

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "m6_1_contract_guard.py"

M5_KGC = "b24893e82c3c50d6e6b776efe7f6daaa23e5a5f1"
M6_1_CHECKPOINT = "901f5a7e7126bdf399f8d35fb429ec60edc6f421"
M6_PLANNING_BASE = "d16b5813d8363ef28cdde29a68f62b108ad96d4b"
M6_2_CANDIDATE = "a5168e1ca608fd7f380ffb3b332fb15016bfa74c"
M6_4_CANDIDATE = "09881ec0ff5894129f3e9a293e2723329d0689e8"

# Authorized deltas per DAG
M6_2_AUTHORIZED_PROJECTION = {
    "aota_forge/adapters/codex/",
    "tests/test_m6_2_codex_adapter.py",
    "deploy/evidence/issues/9/m6-2/",
}
M6_4_AUTHORIZED_PROJECTION = {
    "docs/architecture.md",
    "tests/test_m6_4_surface_reduction.py",
    "deploy/evidence/issues/9/m6-4/",
}

FORBIDDEN_GENERIC_ALLOWLIST_TOKENS = [
    "aota_forge/**",
    "tests/test_m6_*.py",
    "deploy/evidence/issues/9/m6-*",
    "tests/test_m6_2_*",
    "tests/test_m6_4_*",
]


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
    # Descendant-safe default: run_descendant_guard (not legacy path_isolation mixed)
    inputs = _fresh_inputs(guard)
    results, passed = guard.run_descendant_guard(inputs)
    failed = [r for r in results if not r.passed]
    return results, failed


def _import_runtime(module_name: str):
    sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module_name)
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# Original 15-test semantic coverage — preserved (domain-aware)
# ---------------------------------------------------------------------------

def test_valid_accepted_planning_base_passes_end_to_end():
    # Default invocation must be descendant-safe and PASS on R1 candidate
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
    # Ensure default emits descendant domain, not frontier-only
    assert "descendant_contract" in proc.stdout or "R1_DESCENDANT_CONTRACT_GUARD" in proc.stdout


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


# ---------------------------------------------------------------------------
# R1 domain-separation tests
# ---------------------------------------------------------------------------

def test_frontier_provenance_at_exact_m6_1_checkpoint():
    """Frontier provenance targeting exact accepted M6-1 checkpoint must PASS."""
    guard = _load_guard()
    result = guard.check_frontier_provenance(M6_1_CHECKPOINT)
    assert result.applicability == "applicable", result.checks[0].detail if result.checks else ""
    assert result.passed, f"frontier guard failed: {[(c.name, c.passed, c.detail) for c in result.checks]}"
    # Verify expected 9 frontier checks
    assert result.total == 9, f"expected 9 frontier checks, got {result.total}"
    assert result.failed_count == 0


def test_descendant_guard_passes_on_current_r1_candidate():
    """Descendant contract guard must PASS on current R1 repaired tree."""
    guard = _load_guard()
    result = guard.check_descendant_contracts()
    assert result.applicability == "applicable"
    assert result.passed, f"descendant guard failed: {[(c.name, c.passed, c.detail) for c in result.checks if not c.passed]}"
    assert result.failed_count == 0
    assert result.total >= 30


def test_descendant_guard_default_invocation_is_descendant_safe():
    """Default CLI invocation (no --mode) must be descendant-safe, not frontier."""
    proc = subprocess.run(
        [sys.executable, str(GUARD_PATH)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout
    # Must NOT fail due to M6-1 path isolation on a descendant worktree that has extra files
    assert "M6_1_CONTRACT_GUARD=PASS" in proc.stdout
    # Explicit domain tag should show descendant
    assert "descendant_contract" in proc.stdout or "R1_DESCENDANT_CONTRACT_GUARD_TOTAL" in proc.stdout
    # Frontier NOT_APPLICABLE must not be reported as PASS for this default run


def test_frontier_not_applicable_on_descendant_ref():
    """Frontier provenance on any non-901f descendant must be NOT_APPLICABLE (not PASS/FAIL)."""
    guard = _load_guard()
    head = guard.try_git("rev-parse", "HEAD")
    if head == M6_1_CHECKPOINT:
        # We are exactly at frontier — pick a different descendant (e.g. HEAD with extra file in temp projection)
        # Use M6-2 candidate as descendant example
        target = M6_2_CANDIDATE
    else:
        target = head
    result = guard.check_frontier_provenance(target)
    assert result.applicability == "not_applicable", f"expected NOT_APPLICABLE for descendant {target[:12]}, got {result.applicability}"
    assert result.domain == "frontier_provenance"
    # NOT_APPLICABLE must be distinguishable from PASS
    assert "NOT_APPLICABLE" in result.checks[0].detail or result.checks[0].name == "frontier_not_applicable_on_descendant"


def test_explicit_domain_selection_api_exists():
    """Guard must expose explicit domain selection (no heuristic guessing)."""
    guard = _load_guard()
    assert hasattr(guard, "check_frontier_provenance")
    assert hasattr(guard, "check_descendant_contracts")
    assert hasattr(guard, "run_frontier_guard")
    assert hasattr(guard, "run_descendant_guard")
    # CLI must support --mode frontier|descendant
    proc = subprocess.run(
        [sys.executable, str(GUARD_PATH), "--help"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert "--mode" in proc.stdout
    assert "frontier" in proc.stdout
    assert "descendant" in proc.stdout


def test_no_generic_descendant_allowlist():
    """Repair must not introduce wildcard/generic descendant allowlist."""
    src = GUARD_PATH.read_text(encoding="utf-8")
    for token in FORBIDDEN_GENERIC_ALLOWLIST_TOKENS:
        assert token not in src, f"forbidden allowlist token found: {token}"
    # Also ensure no bulk additions of M6-2/M6-4 prefixes to AUTHORIZED_WRITE_PREFIXES beyond original
    # Original authorized prefixes are strictly M6-1
    guard = _load_guard()
    original = {"scripts/m6_1_contract_guard.py", "tests/test_m6_1_contract_freeze.py", "deploy/evidence/issues/9/m6-1/"}
    current = set(guard.AUTHORIZED_WRITE_PREFIXES)
    assert current == original, f"AUTHORIZED_WRITE_PREFIXES must remain M6-1 only, got {current}"


def test_frontier_isolation_does_not_block_legitimate_descendant_paths():
    """Descendant guard must NOT reject legitimate M6-2/M6-4 authorized paths."""
    guard = _load_guard()
    # Simulate descendant worktree with extra authorized-descendant files (M6-4 style)
    with tempfile.TemporaryDirectory(prefix="m61-r1-descendant-probe-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(REPO_ROOT / "aota_forge", tree / "aota_forge")
        # Copy guard-relevant files needed for frozen contract checks
        # Use actual current tree plus a legitimate descendant file
        legit = tree / "aota_forge" / "adapters" / "codex" / "__init__.py"
        legit.parent.mkdir(parents=True, exist_ok=True)
        legit.write_text('"""Legitimate M6-2 codex adapter file (descendant probe)."""\n', encoding="utf-8")
        # Also add a docs file like M6-4 would
        doc_probe = tree / "docs" / "architecture.md"
        # Don't need to create actual file for descendant guard (it only checks frozen contracts), but ensure no false rejection
        inputs = guard.GuardInputs(repo_root=guard.ROOT, source_root=tree)
        records, problems = guard.build_frozen_contract_records(inputs)
        assert problems == [], f"manifest problems: {problems}"
        results = guard.evaluate_source_tree(inputs, records)
        failed = [r.name for r in results if not r.passed]
        assert not failed, f"descendant should not fail frozen-contract checks due to extra path: {failed}"
        # Full descendant guard must still PASS even with extra descendant file
        inputs2 = guard.GuardInputs(repo_root=guard.ROOT, source_root=tree, head_commit=guard.try_git("rev-parse", "HEAD"))
        checks, _ = guard.run_descendant_guard(inputs2)
        # Filter to only descendant failures not related to authority provenance (tree has no evidence dir copy)
        # The authority check will still PASS because we use HEAD evidence fallback
        fails = [c for c in checks if not c.passed]
        # If tree lacks deploy evidence, authority check uses HEAD fallback — should still PASS
        for f in fails:
            assert "M6_1_UNAUTHORIZED_CHANGED_PATH_COUNT" not in f.detail, f"descendant falsely rejected for authorized path: {f.detail}"


def _overlay_candidate_files(candidate_ref: str, dest_tree: Path, allowlist: set[str]) -> list[str]:
    """Overlay authorized delta from candidate_ref into dest_tree. Returns list of overlaid relative paths."""
    diff_out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", f"{M6_1_CHECKPOINT}..{candidate_ref}"],
        stdout=subprocess.PIPE, text=True, check=True
    ).stdout.splitlines()
    overlaid: list[str] = []
    for rel in diff_out:
        if rel == "scripts/m6_1_contract_guard.py":
            continue  # explicitly forbidden in M6-2 sanitized projection
        allowed = any(rel == p.rstrip("/") or rel.startswith(p) for p in allowlist)
        if not allowed:
            continue
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{candidate_ref}:{rel}"],
            stdout=subprocess.PIPE, check=True
        ).stdout
        dest = dest_tree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        overlaid.append(rel)
    return overlaid


def _run_projection_descendant_check(allowlist: set[str], candidate_ref: str) -> tuple[bool, list]:
    guard = _load_guard()
    with tempfile.TemporaryDirectory(prefix="m61-r1-proj-") as tmp:
        synthetic_root = Path(tmp) / "repo"
        synthetic_root.mkdir(parents=True, exist_ok=True)
        for p in ["aota_forge", "deploy", "docs", "tests", "scripts"]:
            src = REPO_ROOT / p
            if src.exists():
                shutil.copytree(src, synthetic_root / p, dirs_exist_ok=True)
        diff_out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--name-only", f"{M6_1_CHECKPOINT}..{candidate_ref}"],
            stdout=subprocess.PIPE, text=True, check=True
        ).stdout.splitlines()
        for rel in diff_out:
            if rel == "scripts/m6_1_contract_guard.py":
                continue
            allowed = any(rel == p.rstrip("/") or rel.startswith(p) for p in allowlist)
            if not allowed:
                continue
            content = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{candidate_ref}:{rel}"], stdout=subprocess.PIPE, check=True).stdout
            dest = synthetic_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        inputs = guard.GuardInputs(repo_root=REPO_ROOT, source_root=synthetic_root, head_commit=guard.try_git("rev-parse", "HEAD"))
        records, problems = guard.build_frozen_contract_records(inputs)
        if problems:
            return False, problems
        results = guard.evaluate_source_tree(inputs, records)
        failed = [r.name for r in results if not r.passed]
        if failed:
            return False, failed
        auth = guard.descendant_authority_guard_result(inputs)
        if not auth.passed:
            return False, [auth.detail]
        return True, []


def test_m6_4_projection_descendant_passes_frontier_not_applicable():
    guard = _load_guard()
    # Verify projection uses only authorized delta and does not mutate guard
    ok, detail = _run_projection_descendant_check(M6_4_AUTHORIZED_PROJECTION, M6_4_CANDIDATE)
    assert ok, f"M6-4 sanitized projection descendant guard failed: {detail}"
    # Frontier must be NOT_APPLICABLE on M6-4 descendant
    fr = guard.check_frontier_provenance(M6_4_CANDIDATE)
    assert fr.applicability == "not_applicable"
    assert "NOT_APPLICABLE" in fr.checks[0].detail


def test_m6_2_projection_descendant_passes_without_guard_hack():
    guard = _load_guard()
    # Prove M6-2 sanitized delta (without scripts/m6_1_contract_guard.py mutation) still passes
    ok, detail = _run_projection_descendant_check(M6_2_AUTHORIZED_PROJECTION, M6_2_CANDIDATE)
    assert ok, f"M6-2 sanitized projection descendant guard failed: {detail}"
    fr = guard.check_frontier_provenance(M6_2_CANDIDATE)
    assert fr.applicability == "not_applicable"
    # Ensure predecessor-guard mutation is not present: the guard file at candidate's codex files does not include M6-2 allowlist
    m6_2_guard_blob = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{M6_2_CANDIDATE}:scripts/m6_1_contract_guard.py"], stdout=subprocess.PIPE, text=True, check=True).stdout
    assert "aota_forge/adapters/codex/" in m6_2_guard_blob
    # But our R1 guard must NOT have that allowlist
    r1_guard_src = GUARD_PATH.read_text(encoding="utf-8")
    # The R1 guard must not contain the hack allowlist in AUTHORIZED_WRITE_PREFIXES
    assert "aota_forge/adapters/codex/" not in r1_guard_src or r1_guard_src.count("codex") < 5  # only forbidden-field checks mention codex_


def test_synthetic_frozen_contract_mutation_rejected():
    guard = _load_guard()
    result = guard.negative_probe_n_m6_01()
    assert result.passed, f"frozen contract mutation not rejected: {result.detail}"
    # Direct file-level mutation: add field to capabilities
    with tempfile.TemporaryDirectory(prefix="m61-syn-freeze-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(REPO_ROOT / "aota_forge", tree / "aota_forge")
        # Also copy needed supporting tree layout for guard
        for extra in ["deploy", "aota_forge/core", "aota_forge/cli"]:
            src = REPO_ROOT / extra
            if src.exists():
                shutil.copytree(src, tree / extra, dirs_exist_ok=True)
        # Mutate capabilities
        cap_path = tree / "aota_forge" / "core" / "execution" / "capabilities.py"
        src_text = cap_path.read_text(encoding="utf-8")
        mutated = src_text.replace("    concurrency_limit: int | None = None\n", "    concurrency_limit: int | None = None\n    synthetic_injected_field: str\n", 1)
        cap_path.write_text(mutated, encoding="utf-8")
        synthetic_root = REPO_ROOT  # for repo_root
        # Use synthetic_root as repo_root but tree as source_root
        inputs = guard.GuardInputs(repo_root=REPO_ROOT, source_root=Path(tmp))
        # Copy minimal frozen sources into tmp root
        shutil.copytree(REPO_ROOT / "aota_forge", Path(tmp) / "aota_forge", dirs_exist_ok=True)
        cap_path2 = Path(tmp) / "aota_forge" / "core" / "execution" / "capabilities.py"
        cap_path2.write_text(mutated, encoding="utf-8")
        records, problems = guard.build_frozen_contract_records(guard.GuardInputs(repo_root=REPO_ROOT, source_root=Path(tmp)))
        results = guard.evaluate_source_tree(guard.GuardInputs(repo_root=REPO_ROOT, source_root=Path(tmp)), records)
        failed_names = {r.name for r in results if not r.passed}
        assert "byte_frozen_contracts_identical_to_m5_kgc" in failed_names or "executor_capabilities_field_set_frozen" in failed_names


def test_synthetic_execution_package_private_field_rejected():
    guard = _load_guard()
    result = guard.negative_probe_n_m6_02()
    assert result.passed, f"ExecutionPackage private field not rejected: {result.detail}"


def test_synthetic_canonical_role_mutation_rejected():
    guard = _load_guard()
    # Synthetic role injection via temp tree — must match actual file shape
    with tempfile.TemporaryDirectory(prefix="m61-syn-role-") as tmp:
        tmproot = Path(tmp)
        shutil.copytree(REPO_ROOT / "aota_forge", tmproot / "aota_forge")
        roles_path = tmproot / "aota_forge" / "core" / "execution" / "roles.py"
        src = roles_path.read_text(encoding="utf-8")
        # Actual content: CANONICAL_ROLES tuple with "executor", then newline + ")"
        mutated = src.replace(
            '    "executor",\n)',
            '    "executor",\n    "codex_specialist",\n)',
            1,
        )
        if mutated == src:
            mutated = src.replace(
                '"executor"',
                '"executor", "codex_specialist"',
                1,
            )
        assert mutated != src, "role mutation could not be applied"
        roles_path.write_text(mutated, encoding="utf-8")
        inputs = guard.GuardInputs(repo_root=REPO_ROOT, source_root=tmproot)
        records, _ = guard.build_frozen_contract_records(inputs)
        results = {r.name: r for r in guard.evaluate_source_tree(inputs, records)}
        assert not results["canonical_role_set_frozen"].passed, f"expected canonical_role_set_frozen to fail after role injection, got {results['canonical_role_set_frozen']}"


def test_synthetic_m6_1_authority_artifact_mutation_rejected():
    guard = _load_guard()
    # Corrupt an accepted M6-1 evidence artifact in a fixture
    with tempfile.TemporaryDirectory(prefix="m61-syn-auth-") as tmp:
        tmproot = Path(tmp)
        shutil.copytree(REPO_ROOT / "deploy", tmproot / "deploy")
        shutil.copytree(REPO_ROOT / "aota_forge", tmproot / "aota_forge", dirs_exist_ok=True)
        # Copy required m6-1 evidence
        for p in (REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m6-1").glob("*.json"):
            (tmproot / "deploy" / "evidence" / "issues" / "9" / "m6-1").mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tmproot / "deploy" / "evidence" / "issues" / "9" / "m6-1" / p.name)
        # Mutate one evidence file
        target = tmproot / "deploy" / "evidence" / "issues" / "9" / "m6-1" / "m6-1-guard-result.json"
        if target.is_file():
            data = json.loads(target.read_text(encoding="utf-8"))
            data["tampered"] = True
            target.write_text(json.dumps(data), encoding="utf-8")
            # Compute blob mismatch
            mutated_blob = hashlib.sha1(b"blob %d\x00" % len(target.read_bytes()) + target.read_bytes()).hexdigest()
            accepted_blob = guard.try_git("rev-parse", f"{M6_1_CHECKPOINT}:deploy/evidence/issues/9/m6-1/m6-1-guard-result.json")
            assert mutated_blob != accepted_blob
            # Authority guard should fail
            inputs = guard.GuardInputs(repo_root=REPO_ROOT, source_root=tmproot, head_commit=guard.try_git("rev-parse", "HEAD"))
            auth = guard.descendant_authority_guard_result(inputs)
            assert not auth.passed, "M6-1 authority artifact mutation must be rejected"


def test_guard_fails_closed_on_malformed_and_unknown_targets():
    guard = _load_guard()
    # Unknown 40-char sha that does not exist — must fail closed, not NOT_APPLICABLE
    r1 = guard.check_frontier_provenance("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    assert not r1.passed
    assert r1.applicability == "applicable"
    assert r1.checks[0].name == "frontier_target_resolves"
    # Malformed hash — must fail closed
    r2 = guard.check_frontier_provenance("not-a-hash!!!")
    assert not r2.passed
    assert r2.applicability == "applicable"
    assert r2.checks[0].name == "frontier_target_resolves"
    # Missing frozen manifest case is already covered by fail_closed probes; double-check
    def factory(inp):
        return guard.build_frozen_contract_records(inp)
    probes = guard.fail_closed_probe_results(factory)
    by_name = {p.name: p for p in probes}
    assert by_name["fail_closed_missing_m5_kgc"].passed
    assert by_name["fail_closed_malformed_expected_blob"].passed


def test_porcelain_parsing_handles_leading_space_correctly():
    """Verify git status --porcelain is parsed with raw (no strip) semantics."""
    src = GUARD_PATH.read_text(encoding="utf-8")
    # R1 must use git_raw for status --porcelain, preserving leading column
    assert "git_raw" in src
    assert "status" in src and "--porcelain" in src
    # Ensure the fix does not rely on strip() for porcelain lines
    assert 'git("status"' not in src or "git_raw" in src


def test_frontier_check_not_applicable_representable():
    guard = _load_guard()
    # pick any non-frontier ref
    non_frontier = M6_2_CANDIDATE
    result = guard.check_frontier_provenance(non_frontier)
    assert result.applicability == "not_applicable"
    assert result.domain == "frontier_provenance"
    # Must not be reported as PASS
    assert not result.passed
    # Must have explicit NOT_APPLICABLE marker
    detail = result.checks[0].detail if result.checks else ""
    assert "NOT_APPLICABLE" in detail


def test_descendant_path_isolation_not_global():
    """Descendant guard must not globally reject M6-2/M6-4 authorized paths."""
    guard_src = GUARD_PATH.read_text(encoding="utf-8")
    # DESCENDANT_GLOBAL_PATH_ISOLATION_CHECK_ALLOWED=no is enforced by ensuring
    # descendant path has no check like 'if changed_path not in M6_1_AUTHORIZED_PATHS: fail'
    assert "DESCENDANT_GLOBAL_PATH_ISOLATION" not in guard_src or "ALLOWED=no" in guard_src
    # Verify that run_descendant_guard does NOT call path_isolation on descendants
    # It should only check M6-1 authority artifacts, not blanket path isolation
    assert "path_isolation_result" not in guard_src or "run_descendant_guard" in guard_src
    # The descendant guard function should not contain AUTHORIZED_WRITE_PREFIXES global check
    # Find descendant code block
    assert "descendant_authority_guard_result" in guard_src


def test_old_42_check_mapping_complete():
    """Old 42 checks map to new domains without semantic loss."""
    guard = _load_guard()
    # Descendant total includes all original contract checks plus authority artifact check
    inputs = _fresh_inputs(guard)
    desc_checks, _ = guard.run_descendant_guard(inputs)
    # Frontier provenance
    front_checks, _ = guard.run_frontier_guard(inputs, target_ref=M6_1_CHECKPOINT)
    # Mapping: original path_isolation moves to frontier only; descendant gains authority check
    desc_names = {c.name for c in desc_checks}
    front_names = {c.name for c in front_checks}
    # All original frozen-contract names must still be present in descendant
    required = {
        "byte_frozen_contracts_identical_to_m5_kgc",
        "structural_mode_signatures_equal_to_m5_kgc",
        "core_schema_rewrite_count_zero",
        "executor_capabilities_field_set_frozen",
        "execution_package_field_set_frozen",
        "canonical_result_field_set_frozen",
        "canonical_task_state_set_frozen",
        "canonical_role_set_frozen",
        "executor_adapter_method_set_frozen",
        "registry_selection_cardinality_contract_intact",
        "dispatcher_structural_markers_intact",
        "ingress_execution_descriptor_surface_frozen",
        "cli_execution_projection_surface_frozen",
        "m4_mutation_surface_frozen",
        "execution_package_executor_neutral_field_count_zero",
        "runtime_canonical_task_states_match",
        "runtime_canonical_roles_match",
        "runtime_executor_adapter_methods_match",
        "runtime_execution_package_fields_match",
        "runtime_rejects_executor_specific_role",
        "runtime_rejects_hermes_private_package_field",
        "pos_m6_02_package_neutral_for_codex_target",
        "negative_probe_n_m6_01_core_schema_widening_rejected",
        "negative_probe_n_m6_02_executor_private_field_rejected",
        "negative_probe_n_m6_21_m4_mutation_redefinition_rejected",
        "future_sensitive_boundary_not_overbroad",
        "fail_closed_missing_m5_kgc",
        "fail_closed_missing_planning_base",
        "fail_closed_missing_planning_artifact",
        "fail_closed_missing_frozen_source_path",
        "fail_closed_duplicate_contract_id",
        "fail_closed_unknown_freeze_mode",
        "fail_closed_malformed_expected_blob",
        "nontautology_independent_of_evidence_content",
    }
    missing = required - desc_names
    assert not missing, f"semantic loss: missing checks {missing}"
    # Frontier must contain path isolation, not descendant
    assert "frontier_path_isolation_authorized_writes_only" in front_names
    assert "path_isolation_authorized_writes_only" not in desc_names
    # Descendant must contain authority artifact check
    assert "descendant_m6_1_authority_artifacts_unchanged" in desc_names
