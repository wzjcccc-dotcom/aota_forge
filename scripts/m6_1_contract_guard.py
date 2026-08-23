#!/usr/bin/env python3
"""Deterministic M6-1 Contract Freeze Guard.

Proves that the M6 accepted planning frontier introduced zero drift in the M5
frozen canonical execution contracts relative to the M5 known-good checkpoint,
and remains future-sensitive: any later unauthorized change to a frozen
canonical contract boundary is rejected while bounded M6 implementation lanes
(Codex adapter, dispatcher defense-in-depth, integration surfaces) stay open.

Authority model (non-tautological):
  - Expected byte identity derives from Git objects pinned at the M5 KGC
    commit, never from the current tree or from M6-1 evidence files.
  - Expected structural shapes derive from the accepted M6-0 planning
    artifacts and are cross-checked against the M5 KGC blob contents.
  - Nothing under deploy/evidence/issues/9/m6-1/ is ever read as input
    authority for a claim it is proving.
"""

from __future__ import annotations

import argparse
import ast
import copy
import dataclasses
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

M5_KGC = "b24893e82c3c50d6e6b776efe7f6daaa23e5a5f1"
M6_ACCEPTED_PLANNING_BASE = "d16b5813d8363ef28cdde29a68f62b108ad96d4b"

ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = Path("deploy/evidence/issues/9/m6-0-plan")
M6_1_EVIDENCE_DIR = Path("deploy/evidence/issues/9/m6-1")

FREEZE_ARTIFACT = "m6-0-architecture-freeze.json"
DAG_ARTIFACT = "m6-0-implementation-dag.json"
OWNERSHIP_ARTIFACT = "m6-0-source-ownership.json"
INVENTORY_ARTIFACT = "m6-0-surface-inventory.json"

AUTHORIZED_WRITE_PREFIXES = (
    "scripts/m6_1_contract_guard.py",
    "tests/test_m6_1_contract_freeze.py",
    "deploy/evidence/issues/9/m6-1/",
)

M6_1_DAG_AUTHORIZED_PATHS = (
    "scripts/m6_1_contract_guard.py",
    "tests/test_m6_1_contract_freeze.py",
    "deploy/evidence/issues/9/m6-1/*",
)

M6_1_OBJECTIVE = (
    "Prove M5 frozen contracts unchanged before any executor proof: "
    "inventory surfaces, assert zero Core schema rewrite, freeze boundary"
)

FREEZE_MODE_BYTE = "byte"
FREEZE_MODE_STRUCTURAL = "structural"

FROZEN_CONTRACT_PATHS: dict[str, tuple[str, ...]] = {
    "ExecutorCapabilities": ("aota_forge/core/execution/capabilities.py",),
    "ExecutionPackage": ("aota_forge/core/execution/package.py",),
    "CanonicalTaskState": ("aota_forge/core/execution/state.py",),
    "CanonicalResult": ("aota_forge/core/execution/results.py",),
    "RoleMapping": ("aota_forge/core/execution/roles.py",),
    "ExecutorAdapter": ("aota_forge/core/execution/adapter.py",),
    "ExecutorRegistry": ("aota_forge/core/execution/registry.py",),
    "ExecutionDispatcher": ("aota_forge/core/execution/dispatcher.py",),
    "Unified Ingress execution descriptors": ("aota_forge/core/ingress.py",),
    "CLI execution projection": (
        "aota_forge/cli/commands/execution.py",
        "aota_forge/cli/__main__.py",
    ),
    "Hermes adapter": ("aota_forge/adapters/hermes/executor.py",),
    "Reference adapter": ("aota_forge/adapters/execution/reference.py",),
}

PATH_FREEZE_MODE_OVERRIDE: dict[str, str] = {
    "aota_forge/core/execution/dispatcher.py": FREEZE_MODE_STRUCTURAL,
    "aota_forge/core/ingress.py": FREEZE_MODE_STRUCTURAL,
    "aota_forge/cli/commands/execution.py": FREEZE_MODE_STRUCTURAL,
    "aota_forge/cli/__main__.py": FREEZE_MODE_STRUCTURAL,
}

KNOWN_FREEZE_MODES = frozenset({FREEZE_MODE_BYTE, FREEZE_MODE_STRUCTURAL})

FUTURE_AUTHORIZED_WRITER_LANES: dict[str, str] = {
    "aota_forge/core/execution/dispatcher.py": "M6-3",
    "aota_forge/core/ingress.py": "M6-5",
    "aota_forge/cli/commands/execution.py": "M6-5",
    "aota_forge/cli/__main__.py": "M6-5",
}

EXECUTOR_CAPABILITIES_FIELDS = (
    "executor_id",
    "adapter_kind",
    "supported_execution_modes",
    "supports_streaming_events",
    "supports_task_cancellation",
    "supports_task_resume",
    "supports_structured_result",
    "supported_canonical_roles",
    "supported_isolation_modes",
    "supports_working_directory",
    "supports_artifact_transport",
    "max_timeout_seconds",
    "concurrency_limit",
)

EXECUTION_PACKAGE_FIELDS = (
    "package_id",
    "protocol_version",
    "contract_hash",
    "operation",
    "canonical_task_id",
    "subject_ref",
    "project_id",
    "canonical_role",
    "instruction",
    "input_artifacts",
    "working_context",
    "capability_requirements",
    "constraints",
    "idempotency_key",
    "intent_fingerprint",
    "correlation_id",
    "result_expectations",
)

CANONICAL_RESULT_FIELDS = (
    "ok",
    "status",
    "canonical_task_id",
    "executor_id",
    "canonical_task_state",
    "exit_code",
    "result_data",
    "output_artifacts",
    "stdout_summary",
    "stderr_summary",
    "error",
    "execution_stats",
    "correlation_id",
)

CANONICAL_TASK_STATES = (
    "CREATED",
    "ACCEPTED",
    "QUEUED",
    "RUNNING",
    "WAITING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
)

CANONICAL_ROLES_EXPECTED = ("planner", "coder", "reviewer", "steward", "executor")

EXECUTOR_ADAPTER_METHODS = (
    "capabilities",
    "validate_package",
    "dispatch",
    "status",
    "result",
    "cancel",
    "resume",
)

INGRESS_EXECUTION_DESCRIPTORS = (
    "execution.task_start",
    "execution.task_status",
    "execution.task_result",
    "execution.task_cancel",
    "execution.executor_list",
    "execution.executor_capabilities",
)

CLI_EXECUTION_ROUTES: dict[tuple[str, str], str] = {
    ("task", "start"): "execution.task_start",
    ("task", "status"): "execution.task_status",
    ("task", "result"): "execution.task_result",
    ("task", "cancel"): "execution.task_cancel",
    ("executor", "list"): "execution.executor_list",
    ("executor", "capabilities"): "execution.executor_capabilities",
}

M4_MUTATION_OPERATIONS = ("plan_init", "plan_retirement")

REGISTRY_SELECTION_MARKERS = (
    "EXECUTOR_NOT_FOUND",
    "NEEDS_SEMANTIC_CHOICE",
    "RESOLVED",
    "CAPABILITY_MISMATCH",
)

DISPATCHER_STRUCTURAL_MARKERS = (
    "class ExecutionDispatcher",
    "NeedsSemanticChoiceError",
    "UNKNOWN",
)

FORBIDDEN_EXECUTOR_PRIVATE_PREFIXES = ("hermes_", "codex_", "profile_", "command_", "shell_")

FORBIDDEN_PREFERENCE_FIELDS = (
    "preferred_executor",
    "fallback_executor",
    "heuristic_quality_rating",
    "semantic_routing_score",
    "best_role",
)

EXECUTOR_SPECIFIC_ROLE_TOKENS = ("hermes_coder", "codex_coder", "codex_specialist")


def git(*args: str, root: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root or ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def try_git(*args: str, root: Path | None = None) -> str | None:
    try:
        return git(*args, root=root)
    except subprocess.CalledProcessError:
        return None


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def read_commit_blob(commit: str, path: str, root: Path | None = None) -> tuple[str, bytes] | None:
    sha = try_git("rev-parse", f"{commit}:{path}", root=root)
    if sha is None:
        return None
    out = subprocess.run(
        ["git", "-C", str(root or ROOT), "cat-file", "blob", sha],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return sha, out


def read_worktree_bytes(source_root: Path, rel_path: str) -> bytes:
    return (source_root / rel_path).read_bytes()


def freeze_mode_for(path: str) -> str:
    return PATH_FREEZE_MODE_OVERRIDE.get(path, FREEZE_MODE_BYTE)


@dataclass
class GuardInputs:
    repo_root: Path
    source_root: Path
    m5_kgc: str = M5_KGC
    planning_base: str = M6_ACCEPTED_PLANNING_BASE
    plan_dir: Path = PLAN_DIR
    evidence_dir: Path = M6_1_EVIDENCE_DIR
    head_commit: str | None = None
    expected_blob_overrides: dict[str, str] = dataclasses.field(default_factory=dict)
    freeze_doc_override: dict[str, Any] | None = None
    dag_doc_override: dict[str, Any] | None = None
    mode_overrides: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclass
class FrozenContractRecord:
    contract_id: str
    paths: tuple[str, ...]
    freeze_modes: tuple[str, ...]
    kgc_blobs: dict[str, str]
    planning_base_blobs: dict[str, str]
    head_blobs: dict[str, str]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Artifact {path} root is not a JSON object")
    return data


def build_frozen_contract_records(
    inputs: GuardInputs,
) -> tuple[list[FrozenContractRecord], list[str]]:
    problems: list[str] = []
    freeze_doc = inputs.freeze_doc_override
    if freeze_doc is None:
        freeze_path = inputs.repo_root / inputs.plan_dir / FREEZE_ARTIFACT
        if not freeze_path.is_file():
            return [], [f"missing planning artifact: {inputs.plan_dir / FREEZE_ARTIFACT}"]
        try:
            freeze_doc = load_json(freeze_path)
        except Exception as exc:
            return [], [f"unreadable planning artifact {FREEZE_ARTIFACT}: {exc}"]
    declared = freeze_doc.get("frozen_core_execution_contracts")
    if not isinstance(declared, list):
        return [], ["architecture freeze artifact lacks frozen_core_execution_contracts"]

    seen_ids: list[str] = []
    for entry in declared:
        cid = entry.get("contract") if isinstance(entry, dict) else None
        if not isinstance(cid, str) or not cid:
            problems.append("frozen contract entry without contract id")
            continue
        seen_ids.append(cid)
    if len(seen_ids) != len(set(seen_ids)):
        duplicates = sorted({c for c in seen_ids if seen_ids.count(c) > 1})
        problems.append(f"duplicate contract id(s) in accepted freeze authority: {duplicates}")

    declared_ids = set(seen_ids)
    if declared_ids != set(FROZEN_CONTRACT_PATHS):
        problems.append(
            "declared frozen contract set differs from guard authority: "
            f"missing={sorted(set(FROZEN_CONTRACT_PATHS) - declared_ids)} "
            f"extra={sorted(declared_ids - set(FROZEN_CONTRACT_PATHS))}"
        )

    records: list[FrozenContractRecord] = []
    for cid, paths in FROZEN_CONTRACT_PATHS.items():
        modes = tuple(inputs.mode_overrides.get(p, freeze_mode_for(p)) for p in paths)
        for p, mode in zip(paths, modes):
            if mode not in KNOWN_FREEZE_MODES:
                problems.append(f"unknown freeze mode {mode!r} for {p}")
        kgc_blobs: dict[str, str] = {}
        base_blobs: dict[str, str] = {}
        head_blobs: dict[str, str] = {}
        for p in paths:
            for commit, target in (
                (inputs.m5_kgc, kgc_blobs),
                (inputs.planning_base, base_blobs),
                (inputs.head_commit, head_blobs) if inputs.head_commit else (None, head_blobs),
            ):
                if commit is None:
                    continue
                got = read_commit_blob(commit, p, root=inputs.repo_root)
                if got is None:
                    problems.append(f"frozen source missing at {commit[:12]}: {p}")
                    continue
                target[p] = got[0]
            wt = inputs.source_root / p
            if not wt.is_file():
                problems.append(f"frozen source missing in evaluated tree: {p}")
                continue
            try:
                head_blobs[f"worktree:{p}"] = blob_sha(read_worktree_bytes(inputs.source_root, p))
            except OSError as exc:
                problems.append(f"unreadable frozen source {p}: {exc}")
        records.append(
            FrozenContractRecord(
                contract_id=cid,
                paths=paths,
                freeze_modes=modes,
                kgc_blobs=kgc_blobs,
                planning_base_blobs=base_blobs,
                head_blobs=head_blobs,
            )
        )
    return records, problems


def _class_defs(tree: ast.AST) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def extract_dataclass_fields(src: str, class_name: str) -> tuple[str, ...] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    cls = _class_defs(tree).get(class_name)
    if cls is None:
        return None
    fields: list[str] = []
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.append(node.target.id)
    return tuple(fields)


def extract_enum_members(src: str, class_name: str) -> tuple[str, ...] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    cls = _class_defs(tree).get(class_name)
    if cls is None:
        return None
    members: list[str] = []
    for node in cls.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            members.append(node.value.value)
    return tuple(members)


def extract_abstract_methods(src: str, class_name: str) -> tuple[str, ...] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    cls = _class_defs(tree).get(class_name)
    if cls is None:
        return None
    methods: list[str] = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                name = dec.id if isinstance(dec, ast.Name) else (
                    dec.attr if isinstance(dec, ast.Attribute) else ""
                )
                if name == "abstractmethod":
                    methods.append(node.name)
                    break
    return tuple(methods)


def _assigned_name(node: ast.stmt) -> str | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return node.target.id
    return None


def extract_module_tuple_strings(src: str, var_name: str) -> tuple[str, ...] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        target_name = _assigned_name(node)
        if target_name != var_name:
            continue
        value = getattr(node, "value", None)
        if isinstance(value, (ast.Tuple, ast.List)):
            values: list[str] = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    values.append(elt.value)
            return tuple(values)
    return None


def extract_operation_descriptor_names(src: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ()
    constants: dict[str, str] = {}
    for node in tree.body:
        target_name = _assigned_name(node)
        value = getattr(node, "value", None)
        if target_name and isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants[target_name] = value.value
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if called != "OperationContractDescriptor":
            continue
        for kw in node.keywords:
            if kw.arg != "name":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                names.append(kw.value.value)
            elif isinstance(kw.value, ast.Name) and kw.value.id in constants:
                names.append(constants[kw.value.id])
    return tuple(sorted(names))


def extract_execution_literal_names(src: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("execution.") and value.count(".") == 1:
                tail = value.split(".", 1)[1]
                if tail.replace("_", "").isalpha():
                    found.add(value)
    return tuple(sorted(found))


def extract_cli_routes(src: str) -> dict[tuple[str, str], str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    routes: dict[tuple[str, str], str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if key is None or not isinstance(key, ast.Tuple) or len(key.elts) != 2:
                continue
            if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in key.elts):
                continue
            pair = (key.elts[0].value, key.elts[1].value)
            target = ""
            if isinstance(value, ast.Tuple) and value.elts:
                first = value.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    target = first.value
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                target = value.value
            routes[pair] = target
    return routes


def extract_cli_command_flags(src: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ()
    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("--"):
            flags.add(node.value)
    return tuple(sorted(flags))


def structural_signature(rel_path: str, src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return json.dumps({"path": rel_path, "syntax_error": True}, sort_keys=True)
    classes: dict[str, list[str]] = {}
    top_functions: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = sorted(
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_functions.append(node.name)
    signature: dict[str, Any] = {
        "path": rel_path,
        "classes": {k: classes[k] for k in sorted(classes)},
        "functions": sorted(top_functions),
    }
    if rel_path.endswith("core/ingress.py"):
        signature["execution_descriptors"] = list(extract_execution_literal_names(src))
    if rel_path.endswith("cli/__main__.py"):
        routes = extract_cli_routes(src)
        signature["routes"] = sorted(f"{k[0]}|{k[1]}->{v}" for k, v in routes.items())
    if rel_path.endswith("cli/commands/execution.py"):
        signature["execution_descriptors"] = list(extract_execution_literal_names(src))
    return json.dumps(signature, sort_keys=True)


def scan_executor_private_hits(src: str) -> list[str]:
    hits: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ["<syntax-error>"]
    forbidden = set(FORBIDDEN_PREFERENCE_FIELDS)
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            candidates.append(node.target.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if "_" in value and not value.startswith("--") and " " not in value:
                candidates.append(value)
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            continue
        for name in candidates:
            if name in forbidden:
                hits.append(name)
                continue
            if any(name.startswith(prefix) for prefix in FORBIDDEN_EXECUTOR_PRIVATE_PREFIXES):
                if name not in {"hermes_profile_object", "hermes_worker_object", "hermes_session_object",
                                "hermes_task_object", "hermes_toolset_object", "hermes_home_path"}:
                    hits.append(name)
    return sorted(set(hits))


def evaluate_source_tree(inputs: GuardInputs, records: list[FrozenContractRecord]) -> list[CheckResult]:
    results: list[CheckResult] = []

    def add(name: str, passed: bool, detail: str) -> None:
        results.append(CheckResult(name=name, passed=bool(passed), detail=detail))

    def src_of(rel: str) -> str | None:
        p = inputs.source_root / rel
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None

    byte_mismatches: list[str] = []
    structural_mismatches: list[str] = []
    for rec in records:
        for idx, rel in enumerate(rec.paths):
            mode = rec.freeze_modes[idx]
            expected_sha = inputs.expected_blob_overrides.get(rel, rec.kgc_blobs.get(rel))
            if expected_sha is None:
                byte_mismatches.append(f"{rel}: no M5 KGC blob identity available")
                continue
            try:
                current_sha = blob_sha(read_worktree_bytes(inputs.source_root, rel))
            except OSError:
                byte_mismatches.append(f"{rel}: unreadable in evaluated tree")
                continue
            if mode == FREEZE_MODE_BYTE:
                if current_sha != expected_sha:
                    byte_mismatches.append(
                        f"{rel}: worktree blob {current_sha[:12]} != frozen expectation {expected_sha[:12]}"
                    )
            else:
                kgc_text: str | None = None
                if rel in rec.kgc_blobs:
                    raw = read_commit_blob(inputs.m5_kgc, rel, root=inputs.repo_root)
                    kgc_text = raw[1].decode("utf-8", errors="replace") if raw else None
                current_text = src_of(rel)
                if kgc_text is None or current_text is None:
                    structural_mismatches.append(f"{rel}: cannot extract structural signature")
                    continue
                if structural_signature(rel, kgc_text) != structural_signature(rel, current_text):
                    structural_mismatches.append(f"{rel}: structural signature drifted from M5 KGC")
                if current_sha != rec.kgc_blobs.get(rel) and rel not in FUTURE_AUTHORIZED_WRITER_LANES:
                    structural_mismatches.append(f"{rel}: modified outside authorized future writer lane")

    add(
        "byte_frozen_contracts_identical_to_m5_kgc",
        not byte_mismatches,
        "; ".join(byte_mismatches) or f"all byte-frozen paths identical to {M5_KGC[:12]}",
    )
    add(
        "structural_mode_signatures_equal_to_m5_kgc",
        not structural_mismatches,
        "; ".join(structural_mismatches) or "all structural-mode signatures equal to M5 KGC",
    )

    rewrite_signals: list[str] = []
    core_contract_files = [
        p
        for cid in (
            "ExecutorCapabilities",
            "ExecutionPackage",
            "CanonicalTaskState",
            "CanonicalResult",
            "RoleMapping",
            "ExecutorAdapter",
            "ExecutorRegistry",
        )
        for p in FROZEN_CONTRACT_PATHS[cid]
    ]
    for rel in core_contract_files:
        expected = next((r.kgc_blobs.get(rel) for r in records if rel in r.kgc_blobs), None)
        if expected is None:
            rewrite_signals.append(f"{rel}: missing KGC identity")
            continue
        try:
            if blob_sha(read_worktree_bytes(inputs.source_root, rel)) != expected:
                rewrite_signals.append(f"{rel}: canonical Core contract file rewritten")
        except OSError:
            rewrite_signals.append(f"{rel}: unreadable")
    add(
        "core_schema_rewrite_count_zero",
        not rewrite_signals,
        f"CORE_SCHEMA_REWRITE_COUNT={len(rewrite_signals)}"
        + ("" if not rewrite_signals else f"; {'; '.join(rewrite_signals)}"),
    )

    cap_src = src_of("aota_forge/core/execution/capabilities.py")
    fields = extract_dataclass_fields(cap_src, "ExecutorCapabilities") if cap_src else None
    add(
        "executor_capabilities_field_set_frozen",
        fields is not None and set(fields) == set(EXECUTOR_CAPABILITIES_FIELDS) and len(fields) == len(EXECUTOR_CAPABILITIES_FIELDS),
        f"fields={sorted(fields) if fields else None}",
    )

    pkg_rel = "aota_forge/core/execution/package.py"
    pkg_src = src_of(pkg_rel)
    pkg_fields = extract_dataclass_fields(pkg_src, "ExecutionPackage") if pkg_src else None
    add(
        "execution_package_field_set_frozen",
        pkg_fields is not None
        and set(pkg_fields) == set(EXECUTION_PACKAGE_FIELDS)
        and len(pkg_fields) == len(EXECUTION_PACKAGE_FIELDS),
        f"fields={sorted(pkg_fields) if pkg_fields else None}",
    )

    res_src = src_of("aota_forge/core/execution/results.py")
    res_fields = extract_dataclass_fields(res_src, "CanonicalResult") if res_src else None
    add(
        "canonical_result_field_set_frozen",
        res_fields is not None
        and set(res_fields) == set(CANONICAL_RESULT_FIELDS)
        and len(res_fields) == len(CANONICAL_RESULT_FIELDS),
        f"fields={sorted(res_fields) if res_fields else None}",
    )

    state_src = src_of("aota_forge/core/execution/state.py")
    states = extract_enum_members(state_src, "CanonicalTaskState") if state_src else None
    add(
        "canonical_task_state_set_frozen",
        states is not None and set(states) == set(CANONICAL_TASK_STATES) and len(states) == len(CANONICAL_TASK_STATES),
        f"states={sorted(states) if states else None}",
    )

    roles_src = src_of("aota_forge/core/execution/roles.py")
    roles = extract_module_tuple_strings(roles_src, "CANONICAL_ROLES") if roles_src else None
    executor_specific = [r for r in (roles or ()) if r in EXECUTOR_SPECIFIC_ROLE_TOKENS]
    add(
        "canonical_role_set_frozen",
        roles is not None
        and tuple(sorted(roles)) == tuple(sorted(CANONICAL_ROLES_EXPECTED))
        and not executor_specific,
        f"roles={roles} EXECUTOR_SPECIFIC_CANONICAL_ROLE_COUNT={len(executor_specific)}",
    )

    adapter_src = src_of("aota_forge/core/execution/adapter.py")
    methods = extract_abstract_methods(adapter_src, "ExecutorAdapter") if adapter_src else None
    add(
        "executor_adapter_method_set_frozen",
        methods is not None and tuple(methods) == EXECUTOR_ADAPTER_METHODS,
        f"methods={methods}",
    )

    reg_src = src_of("aota_forge/core/execution/registry.py")
    reg_ok = False
    reg_detail = "registry source unavailable"
    if reg_src:
        missing_markers = [m for m in REGISTRY_SELECTION_MARKERS if m not in reg_src]
        pref_hits = [f for f in FORBIDDEN_PREFERENCE_FIELDS if re_identifier_present(reg_src, f)]
        rank_hits = [
            tok
            for tok in ("quality_rank", "_score", "rank_executors", "first_registered_wins")
            if tok in reg_src
        ]
        reg_ok = not missing_markers and not pref_hits and not rank_hits
        reg_detail = (
            f"missing_markers={missing_markers} preference_hits={pref_hits} ranking_hits={rank_hits}"
        )
    add("registry_selection_cardinality_contract_intact", reg_ok, reg_detail)

    disp_rel = "aota_forge/core/execution/dispatcher.py"
    disp_src = src_of(disp_rel)
    kgc_disp = read_commit_blob(inputs.m5_kgc, disp_rel, root=inputs.repo_root)
    kgc_disp_src = kgc_disp[1].decode("utf-8", errors="replace") if kgc_disp else None
    disp_ok = False
    disp_detail = "dispatcher source unavailable"
    if disp_src and kgc_disp_src:
        missing_now = [m for m in DISPATCHER_STRUCTURAL_MARKERS if m not in disp_src]
        missing_kgc = [m for m in DISPATCHER_STRUCTURAL_MARKERS if m not in kgc_disp_src]
        sig_equal = structural_signature(disp_rel, kgc_disp_src) == structural_signature(disp_rel, disp_src)
        disp_ok = not missing_now and not missing_kgc and sig_equal
        disp_detail = f"missing_current={missing_now} missing_kgc={missing_kgc} signature_equal={sig_equal}"
    add("dispatcher_structural_markers_intact", disp_ok, disp_detail)

    ing_rel = "aota_forge/core/ingress.py"
    ing_src = src_of(ing_rel)
    descriptors = extract_execution_literal_names(ing_src) if ing_src else ()
    add(
        "ingress_execution_descriptor_surface_frozen",
        tuple(sorted(descriptors)) == tuple(sorted(INGRESS_EXECUTION_DESCRIPTORS)),
        f"descriptors={descriptors}",
    )

    cli_cmd_rel = "aota_forge/cli/commands/execution.py"
    cli_main_rel = "aota_forge/cli/__main__.py"
    cli_cmd_src = src_of(cli_cmd_rel)
    cli_main_src = src_of(cli_main_rel)
    cli_ok = False
    cli_detail = "CLI sources unavailable"
    if cli_cmd_src and cli_main_src:
        routes = extract_cli_routes(cli_main_src)
        route_map = {k: v for k, v in routes.items() if v.startswith("execution.")}
        expected_routes = {k: v for k, v in CLI_EXECUTION_ROUTES.items()}
        flags = set(extract_cli_command_flags(cli_main_src)) | set(extract_cli_command_flags(cli_cmd_src))
        executor_flags = sorted(f for f in flags if f.startswith(("--codex", "--hermes")))
        cli_ok = route_map == expected_routes and not executor_flags
        cli_detail = (
            f"routes={sorted(route_map.items())} executor_specific_flags={executor_flags}"
        )
    add("cli_execution_projection_surface_frozen", cli_ok, cli_detail)

    desc_rel = "aota_forge/core/contracts/descriptor.py"
    desc_src = src_of(desc_rel)
    kgc_desc = read_commit_blob(inputs.m5_kgc, desc_rel, root=inputs.repo_root)
    kgc_desc_src = kgc_desc[1].decode("utf-8", errors="replace") if kgc_desc else None
    m4_ok = False
    m4_detail = "mutation descriptor source unavailable"
    if desc_src and kgc_desc_src:
        current_ops = extract_operation_descriptor_names(desc_src)
        kgc_ops = extract_operation_descriptor_names(kgc_desc_src)
        missing = [op for op in M4_MUTATION_OPERATIONS if op not in current_ops]
        m4_ok = tuple(current_ops) == tuple(kgc_ops) and not missing
        m4_detail = (
            f"current_operations={current_ops} kgc_operations_equal={current_ops == kgc_ops} "
            f"missing_required={missing}"
        )
    add("m4_mutation_surface_frozen", m4_ok, m4_detail)

    neutral_hits = scan_executor_private_hits(pkg_src) if pkg_src else ["<package source unavailable>"]
    add(
        "execution_package_executor_neutral_field_count_zero",
        not neutral_hits,
        f"EXECUTION_PACKAGE_EXECUTOR_PRIVATE_FIELD_COUNT={len(neutral_hits)} hits={neutral_hits}",
    )

    return results


def re_identifier_present(src: str, ident: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ident in src
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == ident:
            return True
        if isinstance(node, ast.Name) and node.id == ident:
            return True
        if isinstance(node, ast.Attribute) and node.attr == ident:
            return True
        if isinstance(node, (ast.keyword,)) and node.arg == ident:
            return True
    return False


def runtime_contract_results(source_root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    repo_root = ROOT
    sys.path.insert(0, str(repo_root))
    try:
        state_mod = importlib.import_module("aota_forge.core.execution.state")
        roles_mod = importlib.import_module("aota_forge.core.execution.roles")
        adapter_mod = importlib.import_module("aota_forge.core.execution.adapter")
        package_mod = importlib.import_module("aota_forge.core.execution.package")

        runtime_states = tuple(s.value for s in state_mod.CanonicalTaskState)
        results.append(
            CheckResult(
                name="runtime_canonical_task_states_match",
                passed=set(runtime_states) == set(CANONICAL_TASK_STATES) and len(runtime_states) == 9,
                detail=f"runtime_states={runtime_states}",
            )
        )
        runtime_roles = tuple(roles_mod.CANONICAL_ROLES)
        results.append(
            CheckResult(
                name="runtime_canonical_roles_match",
                passed=tuple(sorted(runtime_roles)) == tuple(sorted(CANONICAL_ROLES_EXPECTED)),
                detail=f"runtime_roles={runtime_roles}",
            )
        )
        runtime_methods = tuple(
            sorted(n for n in vars(adapter_mod.ExecutorAdapter) if callable(getattr(adapter_mod.ExecutorAdapter, n))
                   and getattr(getattr(adapter_mod.ExecutorAdapter, n), "__isabstractmethod__", False))
        )
        results.append(
            CheckResult(
                name="runtime_executor_adapter_methods_match",
                passed=runtime_methods == tuple(sorted(EXECUTOR_ADAPTER_METHODS)),
                detail=f"runtime_methods={runtime_methods}",
            )
        )
        runtime_pkg_fields = tuple(f.name for f in dataclasses.fields(package_mod.ExecutionPackage))
        results.append(
            CheckResult(
                name="runtime_execution_package_fields_match",
                passed=runtime_pkg_fields == EXECUTION_PACKAGE_FIELDS,
                detail=f"runtime_fields={runtime_pkg_fields}",
            )
        )
        role_rejected = False
        try:
            roles_mod.validate_canonical_role("codex_specialist")
        except ValueError:
            role_rejected = True
        results.append(
            CheckResult(
                name="runtime_rejects_executor_specific_role",
                passed=role_rejected,
                detail=f"validate_canonical_role('codex_specialist') rejected={role_rejected}",
            )
        )
        hermes_rejected = False
        try:
            payload = dict(_minimal_package_payload())
            payload["hermes_session_object"] = {"worker": "x"}
            package_mod.ExecutionPackage.from_dict(payload)
        except ValueError:
            hermes_rejected = True
        except Exception:
            hermes_rejected = False
        results.append(
            CheckResult(
                name="runtime_rejects_hermes_private_package_field",
                passed=hermes_rejected,
                detail=f"from_dict(hermes_session_object=...) raised ValueError={hermes_rejected}",
            )
        )
    finally:
        sys.path.pop(0)
    return results


def _minimal_package_payload() -> dict[str, Any]:
    package_mod = sys.modules.get("aota_forge.core.execution.package")
    if package_mod is None:
        raise RuntimeError("package module not imported")
    return {
        "package_id": "pkg-m61-proof",
        "protocol_version": package_mod.PROTOCOL_VERSION,
        "contract_hash": package_mod.EXECUTION_CONTRACT_HASH,
        "operation": "task_dispatch",
        "canonical_task_id": "task-m61-proof",
        "project_id": "aota_forge",
        "canonical_role": "coder",
        "instruction": "M6-1 contract freeze proof instruction",
        "input_artifacts": [],
        "working_context": {},
        "capability_requirements": {},
        "constraints": {},
        "idempotency_key": "idem-m61-proof",
        "intent_fingerprint": package_mod.compute_intent_fingerprint(
            canonical_role="coder",
            instruction="M6-1 contract freeze proof instruction",
            input_artifacts=(),
            project_id="aota_forge",
        ),
        "correlation_id": "corr-m61-proof",
        "result_expectations": {},
    }


def pos_m6_02_result() -> CheckResult:
    sys.path.insert(0, str(ROOT))
    try:
        package_mod = importlib.import_module("aota_forge.core.execution.package")
        package = package_mod.ExecutionPackage.create(
            canonical_task_id="task-pos-m6-02",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="Execute coding step via codex-targeting constraints",
            capability_requirements={"supported_execution_modes": ["sync"]},
            constraints={"execution_target": "codex"},
        )
        dumped = package.to_dict()
        forbidden_keys = [
            k
            for k in dumped
            if any(k.startswith(prefix) for prefix in FORBIDDEN_EXECUTOR_PRIVATE_PREFIXES)
            or k in set(FORBIDDEN_PREFERENCE_FIELDS)
        ]
        recomputed = package_mod.compute_intent_fingerprint(
            canonical_role=package.canonical_role,
            instruction=package.instruction,
            input_artifacts=(),
            project_id=package.project_id,
        )
        deterministic = package.intent_fingerprint == recomputed
        passed = not forbidden_keys and deterministic
        detail = (
            f"private_field_count={len(forbidden_keys)} forbidden_keys={forbidden_keys} "
            f"intent_fingerprint_deterministic={deterministic}"
        )
        return CheckResult(name="pos_m6_02_package_neutral_for_codex_target", passed=passed, detail=detail)
    except Exception as exc:
        return CheckResult(name="pos_m6_02_package_neutral_for_codex_target", passed=False, detail=f"error: {exc}")
    finally:
        try:
            sys.path.pop(0)
        except ValueError:
            pass


def _copy_source_tree(destination: Path) -> Path:
    shutil.copytree(ROOT / "aota_forge", destination / "aota_forge")
    return destination


def negative_probe_n_m6_01() -> CheckResult:
    """Schema widening of ExecutorCapabilities must be rejected."""
    with tempfile.TemporaryDirectory(prefix="m61-nm601-") as tmp:
        tree = _copy_source_tree(Path(tmp))
        rel = "aota_forge/core/execution/capabilities.py"
        target = tree / rel
        src = target.read_text(encoding="utf-8")
        mutated = src.replace(
            "    concurrency_limit: int | None = None\n",
            "    concurrency_limit: int | None = None\n    codex_native_binding: str\n",
            1,
        )
        if mutated == src:
            return CheckResult(name="negative_probe_n_m6_01_core_schema_widening_rejected", passed=False,
                               detail="probe mutation could not be applied")
        target.write_text(mutated, encoding="utf-8")
        inputs = GuardInputs(repo_root=ROOT, source_root=tree)
        records, problems = build_frozen_contract_records(inputs)
        results = evaluate_source_tree(inputs, records)
        failed_names = {r.name for r in results if not r.passed} | (
            {"frozen_contract_manifest_problems"} if problems else set()
        )
        rejected = bool(problems) or {
            "byte_frozen_contracts_identical_to_m5_kgc",
            "executor_capabilities_field_set_frozen",
            "core_schema_rewrite_count_zero",
        } & failed_names != set()
        detail = f"failed_checks={sorted(failed_names)}"
        return CheckResult(name="negative_probe_n_m6_01_core_schema_widening_rejected", passed=rejected, detail=detail)


def negative_probe_n_m6_02() -> CheckResult:
    """Executor-private field entering ExecutionPackage must be rejected."""
    with tempfile.TemporaryDirectory(prefix="m61-nm602-") as tmp:
        tree = _copy_source_tree(Path(tmp))
        rel = "aota_forge/core/execution/package.py"
        target = tree / rel
        src = target.read_text(encoding="utf-8")
        mutated = src.replace(
            "    result_expectations: dict\n",
            "    result_expectations: dict\n    codex_session_object: dict\n",
            1,
        )
        mutated = mutated.replace(
            '            "result_expectations": self.result_expectations,\n',
            '            "result_expectations": self.result_expectations,\n            "codex_session_object": self.codex_session_object,\n',
            1,
        )
        if mutated == src:
            return CheckResult(name="negative_probe_n_m6_02_executor_private_field_rejected", passed=False,
                               detail="probe mutation could not be applied")
        target.write_text(mutated, encoding="utf-8")
        inputs = GuardInputs(repo_root=ROOT, source_root=tree)
        records, problems = build_frozen_contract_records(inputs)
        results = evaluate_source_tree(inputs, records)
        failed_names = {r.name for r in results if not r.passed} | (
            {"frozen_contract_manifest_problems"} if problems else set()
        )
        rejected = bool(problems) or {
            "byte_frozen_contracts_identical_to_m5_kgc",
            "execution_package_field_set_frozen",
            "execution_package_executor_neutral_field_count_zero",
        } & failed_names != set()
        detail = f"failed_checks={sorted(failed_names)}"
        return CheckResult(name="negative_probe_n_m6_02_executor_private_field_rejected", passed=rejected, detail=detail)


def negative_probe_n_m6_21() -> CheckResult:
    """Redefinition of the frozen M4 mutation surface must be rejected."""
    with tempfile.TemporaryDirectory(prefix="m61-nm621-") as tmp:
        tree = _copy_source_tree(Path(tmp))
        rel = "aota_forge/core/contracts/descriptor.py"
        target = tree / rel
        src = target.read_text(encoding="utf-8")
        mutated = src.replace(
            'PLAN_RETIREMENT_OPERATION = "plan_retirement"',
            'PLAN_RETIREMENT_OPERATION = "plan_retirement_v2"',
            1,
        )
        if mutated == src:
            return CheckResult(name="negative_probe_n_m6_21_m4_mutation_redefinition_rejected", passed=False,
                               detail="probe mutation could not be applied")
        target.write_text(mutated, encoding="utf-8")
        inputs = GuardInputs(repo_root=ROOT, source_root=tree)
        records, problems = build_frozen_contract_records(inputs)
        results = evaluate_source_tree(inputs, records)
        failed_names = {r.name for r in results if not r.passed}
        rejected = "m4_mutation_surface_frozen" in failed_names
        detail = f"failed_checks={sorted(failed_names)}"
        return CheckResult(name="negative_probe_n_m6_21_m4_mutation_redefinition_rejected", passed=rejected, detail=detail)


def future_sensitivity_result() -> CheckResult:
    """A new, unrelated adapter file must NOT trip the frozen-contract boundary."""
    with tempfile.TemporaryDirectory(prefix="m61-future-") as tmp:
        tree = _copy_source_tree(Path(tmp))
        codex_dir = tree / "aota_forge" / "adapters" / "codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        (codex_dir / "__init__.py").write_text(
            '"""Codex adapter lane (authorized future M6-2 work)."""\n', encoding="utf-8"
        )
        inputs = GuardInputs(repo_root=ROOT, source_root=tree)
        records, problems = build_frozen_contract_records(inputs)
        results = evaluate_source_tree(inputs, records)
        failed = [r.name for r in results if not r.passed]
        passed = not problems and not failed
        detail = f"failed_checks={failed} manifest_problems={problems}"
        return CheckResult(name="future_sensitive_boundary_not_overbroad", passed=passed, detail=detail)


def fail_closed_probe_results(records_factory: Callable[[GuardInputs], tuple[list[FrozenContractRecord], list[str]]]) -> list[CheckResult]:
    probes: list[CheckResult] = []

    def run_probe(name: str, inputs: GuardInputs, expect_failed_check: str | None = None) -> None:
        try:
            _, problems = records_factory(inputs)
            if expect_failed_check is None:
                passed = bool(problems)
                detail = f"problems={problems}"
            else:
                results = evaluate_source_tree(inputs, _force(records_factory, inputs))
                failed = {r.name for r in results if not r.passed}
                passed = expect_failed_check in failed
                detail = f"failed_checks={sorted(failed)}"
        except Exception as exc:
            passed = True
            detail = f"raised={type(exc).__name__}: {exc}"
        probes.append(CheckResult(name=name, passed=passed, detail=detail))

    def _force(factory: Callable[[GuardInputs], tuple[list[FrozenContractRecord], list[str]]],
               inputs: GuardInputs) -> list[FrozenContractRecord]:
        records, problems = factory(inputs)
        if problems:
            raise RuntimeError(f"manifest problems: {problems}")
        return records

    run_probe(
        "fail_closed_missing_m5_kgc",
        GuardInputs(repo_root=ROOT, source_root=ROOT, m5_kgc="0" * 40),
        None,
    )
    run_probe(
        "fail_closed_missing_planning_base",
        GuardInputs(repo_root=ROOT, source_root=ROOT, planning_base="0" * 40),
        None,
    )
    run_probe(
        "fail_closed_missing_planning_artifact",
        GuardInputs(repo_root=ROOT, source_root=ROOT, plan_dir=Path("deploy/evidence/issues/9/does-not-exist")),
        None,
    )
    run_probe(
        "fail_closed_missing_frozen_source_path",
        GuardInputs(repo_root=ROOT, source_root=Path(tempfile.mkdtemp(prefix="m61-empty-"))),
        "byte_frozen_contracts_identical_to_m5_kgc",
    )
    dup_override = {
        "frozen_core_execution_contracts": [
            {"contract": "ExecutorCapabilities"},
            {"contract": "ExecutorCapabilities"},
        ]
    }
    run_probe(
        "fail_closed_duplicate_contract_id",
        GuardInputs(repo_root=ROOT, source_root=ROOT, freeze_doc_override=dup_override),
        None,
    )
    unknown_mode_inputs = GuardInputs(repo_root=ROOT, source_root=ROOT)
    unknown_mode_inputs.mode_overrides["aota_forge/core/execution/state.py"] = "fuzzy"
    run_probe(
        "fail_closed_unknown_freeze_mode",
        unknown_mode_inputs,
        None,
    )
    malformed_inputs = GuardInputs(repo_root=ROOT, source_root=ROOT)
    malformed_inputs.expected_blob_overrides["aota_forge/core/execution/state.py"] = "not-a-valid-blob-hash"
    run_probe(
        "fail_closed_malformed_expected_blob",
        malformed_inputs,
        "byte_frozen_contracts_identical_to_m5_kgc",
    )
    return probes


def non_tautology_result() -> CheckResult:
    """Guard verdict must be invariant under poisoned M6-1 evidence content."""
    with tempfile.TemporaryDirectory(prefix="m61-poison-") as tmp:
        poisoned = Path(tmp) / "m6-1"
        poisoned.mkdir(parents=True)
        (poisoned / "fake-result.json").write_text(
            json.dumps({"everything": "PASS", "core_schema_rewrite": "none"}),
            encoding="utf-8",
        )
        baseline_inputs = GuardInputs(repo_root=ROOT, source_root=ROOT, evidence_dir=M6_1_EVIDENCE_DIR)
        poisoned_inputs = GuardInputs(repo_root=ROOT, source_root=ROOT, evidence_dir=poisoned)
        base_records, base_problems = build_frozen_contract_records(baseline_inputs)
        poison_records, poison_problems = build_frozen_contract_records(poisoned_inputs)
        base_results = evaluate_source_tree(baseline_inputs, base_records)
        poison_results = evaluate_source_tree(poisoned_inputs, poison_records)
        identical = (
            [(r.name, r.passed) for r in base_results] == [(r.name, r.passed) for r in poison_results]
            and base_problems == poison_problems
            and not base_problems
            and all(r.passed for r in base_results)
        )
        return CheckResult(
            name="nontautology_independent_of_evidence_content",
            passed=identical,
            detail="guard inputs derive solely from Git objects + accepted planning artifacts",
        )


def path_isolation_result(inputs: GuardInputs) -> CheckResult:
    failures: list[str] = []
    base = inputs.planning_base
    try:
        merge_base = git("merge-base", base, "HEAD", root=inputs.repo_root)
        if merge_base != base:
            failures.append(f"HEAD is not descendant of planning base ({merge_base})")
    except subprocess.CalledProcessError as exc:
        failures.append(f"merge-base failed: {exc}")

    changed: set[str] = set()
    try:
        for diff_args in (
            ("diff", "--name-only", base),
            ("diff", "--cached", "--name-only"),
            ("diff", "--name-only"),
        ):
            changed.update(filter(None, git(*diff_args, root=inputs.repo_root).splitlines()))
        status_out = git("status", "--porcelain", "--untracked-files=all", root=inputs.repo_root)
        for line in status_out.splitlines():
            if len(line) >= 4:
                changed.add(line[3:].split(" -> ", 1)[-1].strip().strip('"'))
    except subprocess.CalledProcessError as exc:
        failures.append(f"git diff/status failed: {exc}")

    runtime_mutations = sorted(p for p in changed if p.startswith("aota_forge/"))
    plan_mutations = sorted(p for p in changed if p.startswith("deploy/evidence/issues/9/m6-0-plan/"))
    if runtime_mutations:
        failures.append(f"AOTA_FORGE_RUNTIME_SOURCE_MUTATION_COUNT={len(runtime_mutations)}: {runtime_mutations}")
    if plan_mutations:
        failures.append(f"M6_0_PLANNING_ARTIFACT_MUTATION_COUNT={len(plan_mutations)}: {plan_mutations}")

    unauthorized: list[str] = []
    for path in sorted(changed):
        if not path or path.endswith(".pyc") or "__pycache__" in path:
            continue
        if not any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in AUTHORIZED_WRITE_PREFIXES
        ):
            unauthorized.append(path)
    if unauthorized:
        failures.append(f"M6_1_UNAUTHORIZED_CHANGED_PATH_COUNT={len(unauthorized)}: {unauthorized}")

    return CheckResult(
        name="path_isolation_authorized_writes_only",
        passed=not failures,
        detail="; ".join(failures)
        or (
            f"AOTA_FORGE_RUNTIME_SOURCE_MUTATION_COUNT=0 "
            f"M6_0_PLANNING_ARTIFACT_MUTATION_COUNT=0 "
            f"M6_1_UNAUTHORIZED_CHANGED_PATH_COUNT=0 "
            f"changed={sorted(changed)}"
        ),
    )


def run_full_guard(inputs: GuardInputs) -> tuple[list[CheckResult], int]:
    results: list[CheckResult] = []

    kgc_sha = try_git("rev-parse", inputs.m5_kgc, root=inputs.repo_root)
    results.append(
        CheckResult("anchors_m5_kgc_present", kgc_sha == inputs.m5_kgc, f"resolved={kgc_sha}")
    )
    base_sha = try_git("rev-parse", inputs.planning_base, root=inputs.repo_root)
    results.append(
        CheckResult(
            "anchors_planning_base_present",
            base_sha == inputs.planning_base,
            f"resolved={base_sha}",
        )
    )
    parent = try_git("rev-parse", f"{inputs.planning_base}^", root=inputs.repo_root)
    results.append(
        CheckResult(
            "planning_base_direct_parent_of_m5_kgc",
            parent == inputs.m5_kgc,
            f"{inputs.planning_base[:12]}^ = {parent}",
        )
    )

    freeze_path = inputs.repo_root / inputs.plan_dir / FREEZE_ARTIFACT
    dag_path = inputs.repo_root / inputs.plan_dir / DAG_ARTIFACT
    own_path = inputs.repo_root / inputs.plan_dir / OWNERSHIP_ARTIFACT
    inv_path = inputs.repo_root / inputs.plan_dir / INVENTORY_ARTIFACT
    artifacts_ok = True
    artifact_detail = []
    for p in (freeze_path, dag_path, own_path, inv_path):
        if not p.is_file():
            artifacts_ok = False
            artifact_detail.append(f"missing {p}")
    freeze_doc: dict[str, Any] = {}
    dag_doc: dict[str, Any] = {}
    if artifacts_ok:
        try:
            freeze_doc = load_json(freeze_path)
            dag_doc = load_json(dag_path)
        except Exception as exc:
            artifacts_ok = False
            artifact_detail.append(f"parse error: {exc}")
        else:
            contracts = freeze_doc.get("frozen_core_execution_contracts", [])
            if len(contracts) != 12:
                artifacts_ok = False
                artifact_detail.append(f"frozen contract count={len(contracts)}")
    results.append(
        CheckResult(
            "planning_authority_artifacts_loaded",
            artifacts_ok,
            "; ".join(artifact_detail) or f"loaded {inputs.plan_dir}",
        )
    )

    dag_slice_ok = False
    dag_detail = "M6-1 slice not recovered"
    slices = dag_doc.get("slices", []) if isinstance(dag_doc, dict) else []
    m61 = next((s for s in slices if s.get("slice_id") == "M6-1"), None)
    if m61:
        authorized = sorted(m61.get("authorized_write_paths", []))
        expected_paths = sorted(M6_1_DAG_AUTHORIZED_PATHS)
        dag_slice_ok = (
            authorized == expected_paths
            and m61.get("objective") == M6_1_OBJECTIVE
            and m61.get("prerequisites") == ["M6-0 accepted plan"]
        )
        dag_detail = f"authorized={authorized} objective_match={m61.get('objective') == M6_1_OBJECTIVE}"
    results.append(CheckResult("m6_1_authorized_paths_match_accepted_dag", dag_slice_ok, dag_detail))

    records, manifest_problems = build_frozen_contract_records(inputs)
    results.append(
        CheckResult(
            "frozen_contract_set_is_exact_twelve",
            not manifest_problems and len(records) == 12 and inputs.freeze_doc_override is None,
            "; ".join(manifest_problems) or f"M5_FROZEN_CONTRACT_COUNT={len(records)}",
        )
    )

    missing_sources: list[str] = []
    for rec in records:
        for rel in rec.paths:
            if rel not in rec.kgc_blobs:
                missing_sources.append(f"{rel}@M5_KGC")
            if inputs.head_commit and rel not in rec.head_blobs:
                missing_sources.append(f"{rel}@HEAD")
            if not (inputs.source_root / rel).is_file():
                missing_sources.append(f"{rel}@worktree")
    results.append(
        CheckResult(
            "frozen_contract_sources_exist_at_kgc_head_and_worktree",
            not missing_sources,
            "; ".join(missing_sources) or "all 13 frozen paths present at all three refs",
        )
    )

    results.extend(evaluate_source_tree(inputs, records))
    results.extend(runtime_contract_results(inputs.source_root))
    results.append(pos_m6_02_result())
    results.append(negative_probe_n_m6_01())
    results.append(negative_probe_n_m6_02())
    results.append(negative_probe_n_m6_21())
    results.append(future_sensitivity_result())

    def factory(inp: GuardInputs) -> tuple[list[FrozenContractRecord], list[str]]:
        return build_frozen_contract_records(inp)

    results.extend(fail_closed_probe_results(factory))
    results.append(non_tautology_result())
    results.append(path_isolation_result(inputs))

    passed = sum(1 for r in results if r.passed)
    return results, passed


def emit_report(results: list[CheckResult]) -> int:
    failures = [r for r in results if not r.passed]
    print("=== M6-1 Contract Freeze Guard ===")
    print(f"M5_KGC={M5_KGC}")
    print(f"M6_ACCEPTED_PLANNING_BASE={M6_ACCEPTED_PLANNING_BASE}")
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.name}: {r.detail}")
    print(f"M6_1_CONTRACT_GUARD_TOTAL={len(results)}")
    print(f"M6_1_CONTRACT_GUARD_PASS={len(results) - len(failures)}")
    print(f"M6_1_CONTRACT_GUARD_FAIL={len(failures)}")
    if failures:
        print("M6_1_CONTRACT_GUARD=FAIL")
        return 1
    print("M6_1_CONTRACT_GUARD=PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic M6-1 Contract Freeze Guard")
    parser.add_argument(
        "--head",
        dest="head",
        default=None,
        help="Explicit HEAD commit to include in triple-ref comparisons",
    )
    args = parser.parse_args(argv)

    head = args.head or try_git("rev-parse", "HEAD")
    inputs = GuardInputs(repo_root=ROOT, source_root=ROOT, head_commit=head)
    results, _passed = run_full_guard(inputs)
    return emit_report(results)


if __name__ == "__main__":
    sys.exit(main())
