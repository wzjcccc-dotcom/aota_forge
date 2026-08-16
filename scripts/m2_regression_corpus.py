#!/usr/bin/env python3
"""M2-C successor regression corpus validator.

Validates deploy/evidence/issues/9/m2-successor-regression-matrix.json and
executes every recorded evidence entry against the successor Core at BASE.

Rejections enforced (schema contract):

- missing required field
- duplicate failure_class
- unknown disposition
- vague disposition (handled / fixed / covered / somehow / conceptually /
  "should not happen" / "pass conceptually")
- invalid owner milestone
- invalid prerequisite milestone
- empty Given / When / Then
- ELIMINATED_BY_CONSTRUCTION / DETERMINISTICALLY_RECONCILED /
  NEEDS_SEMANTIC_CHOICE without executable (fixture, proves=successor_pass)
  evidence
- M3+ owned capability falsely marked implemented during M2 (anything but
  NOT_YET_IMPLEMENTED on an M3/M4/M5/M6/M7 owner)
- NOT_YET_IMPLEMENTED without a frozen future_pass_condition
- NOT_YET_IMPLEMENTED carrying successor_pass evidence
- unknown fixture scenario / malformed evidence / duplicate evidence id
- metadata claiming legacy runtime dependency
- legacy control-plane imports in the successor Core package

Executable evidence runs in isolated temp directories using only the
successor source and stdlib + git binary; no legacy runtime, no network.

Usage:

    python3 scripts/m2_regression_corpus.py          # human report, exit 0/1
    python3 scripts/m2_regression_corpus.py --json   # machine summary
    python3 scripts/m2_regression_corpus.py --self-test   # negative rule checks

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AOTA_FORGE_PACKAGE = REPO_ROOT / "aota_forge"
MATRIX_PATH = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m2-successor-regression-matrix.json"
MAX_MATRIX_BYTES = 2 * 1024 * 1024

ALLOWED_DISPOSITIONS = {
    "ELIMINATED_BY_CONSTRUCTION",
    "DETERMINISTICALLY_RECONCILED",
    "NEEDS_SEMANTIC_CHOICE",
    "NOT_YET_IMPLEMENTED",
}
PASS_DISPOSITIONS = ALLOWED_DISPOSITIONS - {"NOT_YET_IMPLEMENTED"}
ALLOWED_OWNER_MILESTONES = {"M1", "M2", "M3", "M4", "M5", "M6", "M7"}
M2_OWNED_MILESTONES = {"M1", "M2"}

REQUIRED_FIELDS = (
    "failure_class",
    "legacy_evidence",
    "successor_owner_milestone",
    "given",
    "when",
    "then",
    "expected_successor_disposition",
    "executable_evidence",
)

REQUIRED_FAILURE_CLASSES = (
    "B011",
    "B013",
    "B014",
    "B014-F",
    "B014-F1",
    "ACTIVATE-R-current-binding",
    "ACTIVATE-R-host-inspection-escalation",
    "ACTIVATE-R-wrong-source-checkout",
    "WCTX-1",
    "BIND-1",
    "DRIFT-1",
    "RC2-1",
    "CLASSIFY-1",
    "RECOVERY-1",
    "RUNNER-1",
    "E2E-1",
)

VAGUE_WORDS = (
    "handled",
    "fixed",
    "covered",
    "somehow",
    "conceptually",
    "should not happen",
    "pass conceptually",
    "passes conceptually",
)

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


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class CorpusError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_future_pass_condition(entry: dict, errors: list[tuple[str, str, str]]) -> None:
    fpc = entry.get("future_pass_condition")
    if not isinstance(fpc, dict) or not all(
        _nonempty_str(fpc.get(k)) for k in ("given", "when", "then")
    ):
        errors.append((entry["failure_class"], "future_pass_condition invalid",
                       "future_pass_condition must contain non-empty given/when/then"))


def _validate_evidence(entry: dict, errors: list[tuple[str, str, str]], seen_ids: set[str]) -> None:
    failure_class = entry["failure_class"]
    disposition = entry["expected_successor_disposition"]
    evidence = entry["executable_evidence"]
    if not isinstance(evidence, list):
        errors.append((failure_class, "invalid evidence", "executable_evidence must be a list"))
        return
    for item in evidence:
        if not isinstance(item, dict):
            errors.append((failure_class, "invalid evidence", "evidence entries must be objects"))
            continue
        eid = item.get("id")
        if not _nonempty_str(eid):
            errors.append((failure_class, "invalid evidence", "evidence id is required"))
            continue
        if eid in seen_ids:
            errors.append((failure_class, "duplicate evidence id", eid))
        seen_ids.add(eid)
        kind = item.get("kind")
        if kind not in ("static", "fixture"):
            errors.append((failure_class, "invalid evidence", f"{eid}: kind must be static|fixture"))
            continue
        proves = item.get("proves")
        if proves not in ("successor_pass", "foundation"):
            errors.append((failure_class, "invalid evidence", f"{eid}: proves must be successor_pass|foundation"))
        if kind == "static":
            source = item.get("source")
            check = item.get("check")
            marker = item.get("marker")
            if not _nonempty_str(source):
                errors.append((failure_class, "invalid evidence", f"{eid}: static source required"))
            if check not in ("contains", "absent"):
                errors.append((failure_class, "invalid evidence", f"{eid}: static check must be contains|absent"))
            if not _nonempty_str(marker):
                errors.append((failure_class, "invalid evidence", f"{eid}: static marker required"))
        if kind == "fixture":
            scenario = item.get("scenario")
            if not _nonempty_str(scenario):
                errors.append((failure_class, "invalid evidence", f"{eid}: fixture scenario required"))
            elif scenario not in SCENARIOS:
                errors.append((failure_class, "unknown scenario", f"{eid}: {scenario}"))
    if disposition in PASS_DISPOSITIONS:
        has_pass_fixture = any(
            isinstance(item, dict)
            and item.get("kind") == "fixture"
            and item.get("proves") == "successor_pass"
            for item in evidence
        )
        if not has_pass_fixture:
            errors.append((failure_class, "evidence required for pass disposition",
                           f"{disposition} requires at least one fixture evidence with proves=successor_pass"))
    elif disposition == "NOT_YET_IMPLEMENTED":
        for item in evidence:
            if isinstance(item, dict) and item.get("proves") == "successor_pass":
                errors.append((failure_class, "successor_pass on NOT_YET_IMPLEMENTED",
                               f"{item.get('id')} claims successor pass on a NOT_YET_IMPLEMENTED entry"))


def validate_matrix(matrix: dict) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    if matrix.get("legacy_runtime_required_for_corpus") != "no":
        errors.append(("metadata", "metadata flag invalid",
                       "legacy_runtime_required_for_corpus must be 'no'"))
    if matrix.get("new_core_imports_legacy_control_plane") != "no":
        errors.append(("metadata", "metadata flag invalid",
                       "new_core_imports_legacy_control_plane must be 'no'"))
    classes = matrix.get("failure_classes")
    if not isinstance(classes, list):
        errors.append(("metadata", "invalid corpus", "failure_classes must be a list"))
        return errors
    seen_ids: set[str] = set()
    seen_classes: set[str] = set()
    for entry in classes:
        if not isinstance(entry, dict):
            errors.append(("metadata", "invalid corpus", "failure_class entries must be objects"))
            continue
        failure_class = entry.get("failure_class")
        if not _nonempty_str(failure_class):
            errors.append(("unknown", "missing required field", "failure_class required"))
            continue
        for field in REQUIRED_FIELDS:
            if field not in entry:
                errors.append((failure_class, "missing required field", field))
        if failure_class in seen_classes:
            errors.append((failure_class, "duplicate failure_class", failure_class))
        seen_classes.add(failure_class)
        if not _nonempty_str(entry.get("legacy_evidence")):
            errors.append((failure_class, "empty legacy_evidence", "legacy_evidence must be non-empty"))
        for field in ("given", "when", "then"):
            if not _nonempty_str(entry.get(field)):
                errors.append((failure_class, "empty given/when/then", field))
        owner = entry.get("successor_owner_milestone")
        if owner not in ALLOWED_OWNER_MILESTONES:
            errors.append((failure_class, "invalid owner milestone", str(owner)))
        disposition = entry.get("expected_successor_disposition")
        if disposition not in ALLOWED_DISPOSITIONS:
            folded = str(disposition).casefold()
            if any(word in folded for word in VAGUE_WORDS):
                errors.append((failure_class, "vague disposition", str(disposition)))
            else:
                errors.append((failure_class, "unknown disposition", str(disposition)))
        if owner in ALLOWED_OWNER_MILESTONES and disposition in PASS_DISPOSITIONS and owner not in M2_OWNED_MILESTONES:
            errors.append((failure_class, "false pass for future milestone",
                           f"owner {owner} capability does not exist during M2; only NOT_YET_IMPLEMENTED is allowed"))
        prereqs = entry.get("prerequisite_milestones", [])
        if prereqs:
            if not isinstance(prereqs, list) or not all(p in ALLOWED_OWNER_MILESTONES for p in prereqs):
                errors.append((failure_class, "invalid prerequisite milestone", str(prereqs)))
            elif len(set(prereqs)) != len(prereqs) or owner in prereqs:
                errors.append((failure_class, "invalid prerequisite milestone",
                               "prerequisites must be distinct and not include the owner"))
        if "executable_evidence" in entry:
            _validate_evidence(entry, errors, seen_ids)
        if disposition == "NOT_YET_IMPLEMENTED":
            if "future_pass_condition" not in entry:
                errors.append((failure_class, "future_pass_condition required",
                               "NOT_YET_IMPLEMENTED requires a frozen future_pass_condition"))
            else:
                _validate_future_pass_condition(entry, errors)
        elif entry.get("future_pass_condition") is not None:
            _validate_future_pass_condition(entry, errors)
        if entry.get("known_current_failure") is not None and not _nonempty_str(entry.get("known_current_failure")):
            errors.append((failure_class, "invalid evidence", "known_current_failure must be non-empty"))
    for required in REQUIRED_FAILURE_CLASSES:
        if required not in seen_classes:
            errors.append(("corpus", "missing required failure class", required))
    return errors


# ---------------------------------------------------------------------------
# Legacy import guard (NEW_CORE_IMPORTS_LEGACY_CONTROL_PLANE=no)
# ---------------------------------------------------------------------------


def scan_forbidden_imports(package_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            violations.append(f"{path}: parse error: {exc}")
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


# ---------------------------------------------------------------------------
# Executable fixtures (isolated, deterministic, no legacy runtime)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO_ROOT))


def _make_project(workspace: Path, project_id: str) -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
        "summary": "M2-C corpus fixture project",
        "capabilities": ["fixture"],
        "paths": {"source_root": ".", "source": ["."], "docs": ["docs"], "scripts": ["scripts"], "profiles": ["profiles"], "skills": ["skills"], "tests": ["tests"]},
        "commands": {"validate": ["validate"], "deploy": ["deploy"], "verify_deploy": ["verify"]},
        "runtime": {"deployment_type": "managed-files", "requires_human_checkpoint": False},
        "codegraph": {"enabled": False, "index_location": ".codegraph/"},
        "plan": {"active_plan_id": None},
        "constraints": [],
    }
    for sub in ("docs", "scripts", "profiles", "skills", "tests"):
        (project_dir / sub).mkdir(exist_ok=True)
    (project_dir / ".aota" / "project.yaml").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _write_registry(workdir: Path, workspace: Path) -> Path:
    registry = workdir / "workspaces.json"
    registry.write_text(
        json.dumps({"corpus-ws": {"candidates": [str(workspace)]}}), encoding="utf-8")
    return registry


def _git_commit(project_dir: Path) -> str:
    subprocess.run(["git", "init", "-q", str(project_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project_dir), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project_dir), "-c", "user.email=corpus@t", "-c", "user.name=corpus",
         "commit", "-q", "-m", "corpus-init"],
        check=True, capture_output=True,
    )
    full_sha = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    return full_sha


def scenario_host_inspection_no_plan_required() -> tuple[bool, str]:
    from aota_forge.core import execute
    from aota_forge.core.contracts.operations import get_contract

    result = execute("host.status", {}, principal="corpus")
    if result.get("ok") is not True or "identity" not in result.get("data", {}):
        return False, "host.status did not return ok with identity"
    contract = get_contract("host.status")
    if contract is None or not contract.read_only:
        return False, "host.status contract missing or not read-only"
    input_keys = {str(k).casefold() for k in contract.inputs}
    plan_related = input_keys & {"plan", "spec", "profile", "profile_task", "approval", "work_item", "milestone"}
    if plan_related:
        return False, f"host.status contract exposes plan-related inputs: {sorted(plan_related)}"
    return True, "host.status ok with no Plan/SPEC/Profile Task inputs"


def scenario_git_boundary_escape_fails_closed() -> tuple[bool, str]:
    from aota_forge.core.contracts.errors import GitBoundaryViolationError, GitNotFoundError
    from aota_forge.core.git.inspect import find_git_root, inspect_git

    workdir = Path(tempfile.mkdtemp(prefix="corpus-git-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        p1 = _make_project(workspace, "fixture-alpha")
        registry = _write_registry(workdir, workspace)

        from aota_forge.core import execute

        resolved = execute("project.resolve", {
            "workspace_id": "corpus-ws", "project_id": "fixture-alpha",
            "registry_path": str(registry),
        }, principal="corpus")
        if resolved.get("ok") is not True:
            return False, "project.resolve failed: " + str(resolved.get("errors"))
        project_root = Path(resolved["data"]["project_root"])

        full_sha = _git_commit(project_root)
        inspected = inspect_git(project_root, boundary=project_root)
        if inspected["available"] is not True:
            return False, "git inspection unavailable"
        if len(inspected["head_sha"]) != 40 or inspected["head_sha"] != full_sha:
            return False, "git head_sha is not the full 40-char SHA"

        nested = project_root / "nested" / "deep"
        nested.mkdir(parents=True)
        nested_result = inspect_git(nested, boundary=project_root)
        if nested_result["repo_root"] != ".":
            return False, "nested path did not resolve to the same repo root"

        outside = workdir / "outside"
        outside.mkdir()
        try:
            find_git_root(outside, boundary=project_root)
            return False, "boundary escape did not fail closed"
        except GitBoundaryViolationError:
            pass

        no_git = workdir / "no-git-inside"
        no_git.mkdir()
        try:
            find_git_root(no_git, boundary=no_git)
            return False, "repository above the boundary was followed"
        except GitNotFoundError:
            pass

        try:
            inspect_git(no_git, boundary=no_git)
            return False, "inspect_git above boundary did not fail"
        except GitNotFoundError:
            pass
        return True, "git bounded to project root; escapes fail closed"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def scenario_exact_registered_project_classification() -> tuple[bool, str]:
    from aota_forge.core import execute
    from aota_forge.core.contracts.operations import get_contract
    from aota_forge.core.project.discovery import scan_projects

    workdir = Path(tempfile.mkdtemp(prefix="corpus-classify-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        for index in range(60):
            _make_project(workspace, f"proj-{index:03d}")
        _make_project(workspace, "proj-054-allterm")
        _make_project(workspace, "unrelated-project")
        registry = _write_registry(workdir, workspace)

        complete = scan_projects(workspace, limit=None)
        ids = [item["project_id"] for item in complete["projects"]]
        if "proj-054" not in ids:
            return False, "exact registered project beyond the first-50 listing boundary disappeared from the complete candidate scan"
        if complete["project_count"] != 62:
            return False, f"deterministic scan did not enumerate all registered projects: {complete['project_count']}"

        resolved = execute("project.resolve", {
            "workspace_id": "corpus-ws", "project_id": "proj-054",
            "registry_path": str(registry),
        }, principal="corpus")
        if resolved.get("ok") is not True:
            return False, "exact resolution beyond the listing boundary failed: " + str(resolved.get("errors"))
        if resolved["data"]["project_id"] != "proj-054":
            return False, "resolution selected the wrong candidate"

        duplicate_dir = workspace / "proj-054-copy"
        (duplicate_dir / ".aota").mkdir(parents=True)
        (duplicate_dir / ".aota" / "project.yaml").write_text(
            (workspace / "proj-054" / ".aota" / "project.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        duplicate = execute("project.resolve", {
            "workspace_id": "corpus-ws", "project_id": "proj-054",
            "registry_path": str(registry),
        }, principal="corpus")
        if duplicate.get("ok") is not False:
            return False, "duplicate target beyond the first-50 boundary did not fail closed"
        if duplicate.get("errors", [{}])[0].get("code") != "PROJECT_AMBIGUOUS":
            return False, "duplicate target did not surface PROJECT_AMBIGUOUS"

        contract = get_contract("project.resolve")
        if contract is None or any("hint" in str(k).casefold() for k in contract.inputs):
            return False, "project.resolve contract exposes a hint input"
        return True, "exact registered project classified deterministically beyond the listing boundary; duplicates fail closed; no hint input"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def scenario_distinct_error_taxonomy_foundation() -> tuple[bool, str]:
    from aota_forge.core import execute

    codes: list[str] = []
    r1 = execute("project.resolve", {}, principal="corpus")
    codes.append(r1.get("errors", [{}])[0].get("code", ""))
    r2 = execute("runtime.status", {"pid": "not-an-int"}, principal="corpus")
    codes.append(r2.get("errors", [{}])[0].get("code", ""))
    r3 = execute("corpus.unknown-operation", {}, principal="corpus")
    codes.append(r3.get("errors", [{}])[0].get("code", ""))
    if any(not c for c in codes):
        return False, f"empty canonical error code: {codes}"
    if len(set(codes)) != 3:
        return False, f"distinct root causes collapsed: {codes}"
    return True, f"distinct canonical codes: {sorted(set(codes))}"


def scenario_single_declarative_contract_surface() -> tuple[bool, str]:
    from aota_forge.core import execute
    from aota_forge.core.contracts.operations import available_operations

    lib_a = execute("operations.list", {}, principal="corpus")
    lib_b = execute("operations.list", {}, principal="corpus")
    if lib_a.get("ok") is not True or lib_b.get("ok") is not True:
        return False, "operations.list failed"
    surface_a = lib_a["data"]["operations"]
    surface_b = lib_b["data"]["operations"]
    if surface_a != surface_b:
        return False, "operation surface is not deterministic across invocations"
    if surface_a != available_operations():
        return False, "ingress surface diverges from the contract registry"

    env = dict(os.environ)
    env.pop("HERMES_HOME", None)
    cli_proc = subprocess.run(
        [sys.executable, "-m", "aota_forge.cli", "operations", "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=60,
    )
    if cli_proc.returncode != 0:
        return False, "CLI operations failed"
    try:
        cli_payload = json.loads(cli_proc.stdout)
    except json.JSONDecodeError:
        return False, "CLI output is not JSON"
    if cli_payload.get("ok") is not True or cli_payload["data"]["operations"] != surface_a:
        return False, "CLI surface diverges from the single declarative registry"
    return True, "library and CLI project the same deterministic surface from one registry"


def scenario_shadow_pointer_not_authority() -> tuple[bool, str]:
    from aota_forge.core.plan.read_model import LegacyPlanStateReader, snapshot_sha256

    workdir = Path(tempfile.mkdtemp(prefix="corpus-shadow-"))
    try:
        p1 = _make_project(workdir, "fixture-alpha")
        plans_dir = p1 / ".aota" / "forge" / "plans" / "plan_20260816T000000_corpus01"
        plans_dir.mkdir(parents=True)
        (plans_dir / "plan.json").write_text(json.dumps({
            "plan_id": "plan_20260816T000000_corpus01", "revision": 3, "status": "planned",
            "current_milestone": "M2", "current_work_item": "pointer-not-authority",
            "milestones": {"M1": {"status": "completed", "work_items": [{"work_item_id": "M1-A", "status": "completed"}]}},
        }), encoding="utf-8")
        reader = LegacyPlanStateReader()
        snapshot = reader.load(p1, "plan_20260816T000000_corpus01")
        if snapshot is None or snapshot.source != "legacy_shadow":
            return False, "shadow read model did not classify as legacy_shadow"
        flat = snapshot.to_dict()
        if "current_work_item" in flat:
            return False, "current_* pointer leaked into the portable snapshot"
        if snapshot.current_milestone != "M2":
            return False, "observed milestone state was lost"
        if snapshot_sha256({"plan_id": snapshot.plan_id, "revision": snapshot.revision, "status": snapshot.status}) != snapshot.sha256:
            return False, "snapshot digest is not deterministic"
        return True, "current_* pointer is not Core authority; digest deterministic"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def scenario_contract_drift_detected_deterministically() -> tuple[bool, str]:
    from aota_forge.adapters.hermes import build_request, execute_request
    from aota_forge.adapters.hermes.invoke import detect_contract_drift
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    descriptor = DEFAULT_REGISTRY.get("operations.list")
    if descriptor is None:
        return False, "operations.list descriptor missing"
    current_hash = descriptor.contract_hash()
    current_protocol = descriptor.protocol_version

    hash_mismatch = execute_request(
        build_request("operations.list", {}, expected_contract_hash="0" * 64)
    )
    proto_mismatch = execute_request(
        build_request("operations.list", {}, expected_protocol_version="9.9")
    )
    for payload in (hash_mismatch, proto_mismatch):
        if payload.get("ok") is not False:
            return False, "drift request was executed instead of rejected"
        codes = [item.get("code") for item in payload.get("errors", [])]
        if "CONTRACT_VERSION_MISMATCH" not in codes:
            return False, f"drift mismatch error missing: {codes}"
        if payload.get("data") != {}:
            return False, "mismatched schema was executed despite drift"

    drifted_hash, field_hash = detect_contract_drift(
        "operations.list", current_protocol, "0" * 64
    )
    drifted_proto, field_proto = detect_contract_drift(
        "operations.list", "9.9", current_hash
    )
    if not drifted_hash or field_hash != "contract_hash":
        return False, f"contract_hash drift not detected deterministically: {field_hash}"
    if not drifted_proto or field_proto != "protocol_version":
        return False, f"protocol_version drift not detected deterministically: {field_proto}"
    drifted_none, _ = detect_contract_drift(
        "operations.list", current_protocol, current_hash
    )
    if drifted_none:
        return False, "matching contract identity reported as drifted"
    return True, "deterministic drift detection; mismatched schemas never silently run"


def scenario_plan_normalization_current_vs_historical() -> tuple[bool, str]:
    from aota_forge.core.plan.normalize import normalize_portable_plan

    body = (
        "# [PLAN] corpus fixture\n"
        "\n"
        "## 1. Current State\n"
        "\n"
        "```text\n"
        "PLAN_STATUS=in-progress\n"
        "CURRENT_MILESTONE=M2\n"
        "HANDOFF_STATE=m2_planning_ready\n"
        "```\n"
        "\n"
        "## M1 Historical Evidence (superseded)\n"
        "\n"
        "```text\n"
        "PLAN_STATUS=completed\n"
        "CURRENT_MILESTONE=M1\n"
        "HANDOFF_STATE=m1_closed\n"
        "```\n"
    )
    doc = normalize_portable_plan(body, source_revision="corpus-m2i")
    if doc.current_milestone != "M2" or doc.plan_status != "in-progress":
        return False, "historical values overrode current authoritative state"
    if doc.handoff_state != "m2_planning_ready":
        return False, "current handoff state was lost"
    provenance = doc.provenance_observations.get("M1 Historical Evidence (superseded)", {})
    if provenance.get("CURRENT_MILESTONE") != "M1" or provenance.get("PLAN_STATUS") != "completed":
        return False, "historical values were not preserved as provenance observations"
    if len(doc.source_digest) != 64:
        return False, "document digest is not deterministic"
    return True, "current state stays authoritative; historical state is provenance only"


def scenario_wctx1_lifecycle_producers_absent() -> tuple[bool, str]:
    from aota_forge.core import execute

    surface = execute("operations.list", {}, principal="corpus")
    if surface.get("ok") is not True:
        return False, "operations.list failed"
    names = [op["operation"] for op in surface["data"]["operations"]]
    absent_codes = ("PLAN_MISSING", "PLAN_WORKSPACE_CONTEXT_MISSING", "ACTIVE_WORK_ITEM_MISSING")
    for name in names:
        result = execute(
            name,
            {"workspace_id": "w", "project_id": "p", "registry_path": "/nonexistent/corpus-registry.json"},
            principal="corpus",
        )
        codes = [item.get("code") for item in result.get("errors", [])]
        for code in absent_codes:
            if code in codes:
                return False, f"{code} already produced by canonical operation {name}"
    binding = execute("host.status", {"workspace_id": "w", "project_id": "p"}, principal="corpus")
    if binding.get("errors", [{}])[0].get("code") != "PROJECT_BINDING_MISSING":
        return False, "PROJECT_BINDING_MISSING is not produced distinctly"
    return True, "no canonical operation produces the three later lifecycle errors yet"


def scenario_all_operations_read_only() -> tuple[bool, str]:
    from aota_forge.core import execute

    result = execute("operations.list", {}, principal="corpus")
    if result.get("ok") is not True:
        return False, "operations.list failed"
    operations = result["data"]["operations"]
    names = sorted(op["operation"] for op in operations)
    expected = {"git.inspect", "host.status", "operations.list", "project.resolve", "runtime.status"}
    if set(names) != expected:
        return False, f"unexpected operation surface: {names}"
    if not all(op["read_only"] is True for op in operations):
        return False, "a registered operation is not read-only at BASE"
    return True, f"all {len(names)} registered operations are read-only; no mutation surface"


SCENARIOS = {
    "host-inspection-no-plan-required": scenario_host_inspection_no_plan_required,
    "git-boundary-escape-fails-closed": scenario_git_boundary_escape_fails_closed,
    "exact-registered-project-classification": scenario_exact_registered_project_classification,
    "distinct-error-taxonomy-foundation": scenario_distinct_error_taxonomy_foundation,
    "single-declarative-contract-surface": scenario_single_declarative_contract_surface,
    "shadow-pointer-not-authority": scenario_shadow_pointer_not_authority,
    "all-operations-read-only": scenario_all_operations_read_only,
    "contract-drift-detected-deterministically": scenario_contract_drift_detected_deterministically,
    "plan-normalization-current-vs-historical": scenario_plan_normalization_current_vs_historical,
    "wctx1-lifecycle-producers-absent": scenario_wctx1_lifecycle_producers_absent,
}


def run_static_evidence(item: dict) -> tuple[bool, str]:
    source = str(item.get("source", ""))
    marker = str(item.get("marker", ""))
    check = item.get("check")
    target = REPO_ROOT / source if source != "aota_forge" else AOTA_FORGE_PACKAGE
    if check == "contains":
        if not target.is_file():
            return False, f"static source not found: {source}"
        text = target.read_text(encoding="utf-8", errors="replace")
        if marker not in text:
            return False, f"marker not found in {source}: {marker!r}"
        return True, f"marker found in {source}"
    if check == "absent":
        if not target.is_dir():
            return False, f"package directory not found: {source}"
        hits: list[str] = []
        for path in sorted(target.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if marker in text:
                hits.append(str(path.relative_to(REPO_ROOT)))
        if hits:
            return False, f"marker present (expected absent): {marker!r} in {hits[:5]}"
        return True, f"marker absent from package: {marker!r}"
    return False, f"unknown static check: {check}"


def execute_evidence(matrix: dict) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for entry in matrix.get("failure_classes", []):
        if not isinstance(entry, dict):
            continue
        for item in entry.get("executable_evidence", []):
            if not isinstance(item, dict):
                continue
            eid = item.get("id")
            if not eid:
                continue
            if item.get("kind") == "fixture":
                scenario = SCENARIOS.get(item.get("scenario"))
                if scenario is None:
                    results[eid] = {"pass": False, "detail": "unknown scenario"}
                    continue
                try:
                    ok, detail = scenario()
                except Exception as exc:  # bounded guard
                    ok, detail = False, f"scenario raised {type(exc).__name__}: {exc}"
                results[eid] = {"pass": ok, "detail": str(detail)[:400]}
            else:
                ok, detail = run_static_evidence(item)
                results[eid] = {"pass": ok, "detail": str(detail)[:400]}
    return dict(sorted(results.items()))


# ---------------------------------------------------------------------------
# Self-test: every rejection rule must fire
# ---------------------------------------------------------------------------


def _clone_matrix() -> dict:
    with open(MATRIX_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _by_class(matrix: dict, failure_class: str) -> dict:
    for entry in matrix["failure_classes"]:
        if entry["failure_class"] == failure_class:
            return entry
    raise KeyError(failure_class)


def _errors_for(matrix: dict) -> list[tuple[str, str, str]]:
    return validate_matrix(matrix)


def run_self_test() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = []

    def check_case(name: str, expected_code: str, mutate) -> None:
        matrix = _clone_matrix()
        try:
            mutate(matrix)
        except Exception as exc:  # bounded guard
            cases.append((name, expected_code, False))
            return
        codes = {code for _, code, _ in _errors_for(matrix)}
        cases.append((name, expected_code, expected_code in codes))

    def set_disposition(matrix: dict, failure_class: str, disposition: str) -> dict:
        entry = _by_class(matrix, failure_class)
        entry["expected_successor_disposition"] = disposition
        entry["executable_evidence"] = []
        return entry

    check_case("missing required field", "missing required field",
               lambda m: _by_class(m, "B011").pop("then"))
    check_case("duplicate failure_class", "duplicate failure_class",
               lambda m: m["failure_classes"].append(copy.deepcopy(_by_class(m, "B011"))))
    check_case("unknown disposition", "unknown disposition",
               lambda m: set_disposition(m, "B011", "SOMEDAY_SOLVED"))
    check_case("vague disposition", "vague disposition",
               lambda m: set_disposition(m, "B011", "ELIMINATED_BY_CONSTRUCTION should not happen"))
    check_case("invalid owner milestone", "invalid owner milestone",
               lambda m: _by_class(m, "B011").__setitem__("successor_owner_milestone", "M9"))
    check_case("empty when", "empty given/when/then",
               lambda m: _by_class(m, "B011").__setitem__("when", "   "))
    check_case("eliminated without evidence", "evidence required for pass disposition",
               lambda m: set_disposition(m, "ACTIVATE-R-host-inspection-escalation", "ELIMINATED_BY_CONSTRUCTION"))
    check_case("reconciled without evidence", "evidence required for pass disposition",
               lambda m: set_disposition(m, "B013", "DETERMINISTICALLY_RECONCILED"))
    check_case("semantic choice without evidence", "evidence required for pass disposition",
               lambda m: set_disposition(m, "DRIFT-1", "NEEDS_SEMANTIC_CHOICE"))
    check_case("false pass future milestone", "false pass for future milestone",
               lambda m: set_disposition(m, "B014", "ELIMINATED_BY_CONSTRUCTION"))
    check_case("NYI without future pass condition", "future_pass_condition required",
               lambda m: _by_class(m, "B014").pop("future_pass_condition"))
    check_case("unknown scenario", "unknown scenario",
               lambda m: _by_class(m, "CLASSIFY-1")["executable_evidence"][0].__setitem__("scenario", "no-such-scenario"))
    check_case("legacy runtime flag yes", "metadata flag invalid",
               lambda m: m.__setitem__("legacy_runtime_required_for_corpus", "yes"))
    check_case("successor_pass on NYI", "successor_pass on NOT_YET_IMPLEMENTED",
               lambda m: _by_class(m, "B011")["executable_evidence"].append(
                   {"id": "corpus-selftest-fake", "kind": "fixture",
                    "scenario": "host-inspection-no-plan-required", "proves": "successor_pass"}))
    check_case("invalid prerequisite milestone", "invalid prerequisite milestone",
               lambda m: _by_class(m, "BIND-1").__setitem__("prerequisite_milestones", ["M4"]))
    return cases


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def summarize(matrix: dict, schema_errors: list, evidence: dict, violations: list, self_test: list) -> dict:
    classes = [e for e in matrix.get("failure_classes", []) if isinstance(e, dict)]
    by_owner: dict[str, list[str]] = {}
    nyi: list[str] = []
    pass_count = 0
    for entry in classes:
        owner = entry.get("successor_owner_milestone", "?")
        by_owner.setdefault(owner, []).append(entry["failure_class"])
        if entry.get("expected_successor_disposition") == "NOT_YET_IMPLEMENTED":
            nyi.append(entry["failure_class"])
        else:
            pass_count += 1
    for owner in sorted(by_owner):
        by_owner[owner] = sorted(by_owner[owner])
    fixture_count = sum(1 for e in evidence.values() if e.get("pass"))
    evidence_fixture_items = sum(
        1 for entry in classes
        for item in entry.get("executable_evidence", [])
        if isinstance(item, dict) and item.get("kind") == "fixture"
    )
    evidence_static_items = sum(
        1 for entry in classes
        for item in entry.get("executable_evidence", [])
        if isinstance(item, dict) and item.get("kind") == "static"
    )
    present = all(required in {e.get("failure_class") for e in classes} for required in REQUIRED_FAILURE_CLASSES)
    all_evidence_pass = all(e.get("pass") for e in evidence.values())
    self_test_pass = all(ok for _, _, ok in self_test) if self_test is not None else None
    verdict = (
        "PASS"
        if not schema_errors and not violations and all_evidence_pass
        and (self_test_pass is None or self_test_pass)
        else "FAIL"
    )
    return {
        "artifact": "m2-successor-regression-matrix",
        "base_sha": matrix.get("base_sha", ""),
        "verdict": verdict,
        "failure_class_count": len(classes),
        "required_failure_classes_present": "yes" if present else "no",
        "not_yet_implemented_count": len(nyi),
        "pass_disposition_count": pass_count,
        "not_yet_implemented_classes": sorted(nyi),
        "classes_by_owner_milestone": by_owner,
        "evidence_fixture_items": evidence_fixture_items,
        "evidence_static_items": evidence_static_items,
        "evidence_executed": len(evidence),
        "evidence_fixtures_passed": fixture_count,
        "legacy_runtime_required_for_corpus": matrix.get("legacy_runtime_required_for_corpus", "?"),
        "new_core_imports_legacy_control_plane": matrix.get("new_core_imports_legacy_control_plane", "?"),
        "legacy_import_violations": violations,
        "schema_error_count": len(schema_errors),
        "schema_errors": [{"failure_class": f, "code": c, "message": m} for f, c, m in schema_errors],
        "evidence_results": evidence,
        "self_test": None if self_test is None else {
            "cases": [{"case": n, "expected_code": e, "rejected": ok} for n, e, ok in self_test],
            "pass": self_test_pass,
        },
    }


def main() -> int:
    as_json = "--json" in sys.argv
    self_test_mode = "--self-test" in sys.argv

    try:
        if MATRIX_PATH.stat().st_size > MAX_MATRIX_BYTES:
            raise CorpusError("invalid corpus", "matrix exceeds size bound")
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, CorpusError) as exc:
        if as_json:
            print(json.dumps({"verdict": "FAIL", "fatal": str(exc)}, ensure_ascii=False, sort_keys=True))
        else:
            print(f"FATAL  cannot load matrix: {exc}")
        return 1

    schema_errors = validate_matrix(matrix)
    violations = scan_forbidden_imports(AOTA_FORGE_PACKAGE)
    evidence = execute_evidence(matrix) if not schema_errors else {}
    self_test = run_self_test() if self_test_mode else None
    summary = summarize(matrix, schema_errors, evidence, violations, self_test)

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

    print(f"matrix: {MATRIX_PATH.relative_to(REPO_ROOT)}")
    print(f"schema validation: {'PASS' if not schema_errors else 'FAIL'} ({len(schema_errors)} errors)")
    for failure_class, code, message in schema_errors:
        print(f"  REJECT {failure_class}: {code}: {message}")
    print(f"legacy import scan: {'PASS' if not violations else 'FAIL'} ({len(violations)} violations)")
    for violation in violations:
        print(f"  REJECT {violation}")
    if not schema_errors:
        print(f"evidence execution: {sum(1 for e in evidence.values() if e.get('pass'))}/{len(evidence)} PASS")
        for eid, result in evidence.items():
            print(f"  {'PASS' if result['pass'] else 'FAIL'}  {eid}  ({result['detail']})")
    print(f"failure classes: {summary['failure_class_count']} "
          f"(NYI={summary['not_yet_implemented_count']}, pass-disposition={summary['pass_disposition_count']})")
    print("classes by owner milestone:")
    for owner in sorted(summary["classes_by_owner_milestone"]):
        print(f"  {owner}: {', '.join(summary['classes_by_owner_milestone'][owner])}")
    if self_test is not None:
        self_test_summary = summary["self_test"]
        print(f"self-test: {'PASS' if self_test_summary['pass'] else 'FAIL'}")
        for case in self_test_summary["cases"]:
            print(f"  {'REJECTED' if case['rejected'] else 'MISSED '} {case['case']} (expected code: {case['expected_code']})")
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
