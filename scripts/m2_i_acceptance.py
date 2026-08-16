#!/usr/bin/env python3
"""M2-I focused deterministic acceptance validation — Serial Integration.

Cross-lane acceptance facts for the M2 source candidate:

  P  provenance / exact lineage
  C  contract foundation gates
  I  package import isolation + handler bootstrap determinism/idempotence
  D  descriptor-handler registry consistency
  T  host trusted-resource integration + model-facing path denial
  E  canonical error registration + I9-B004/B005/B006 integrated regressions
  G  canonical ingress gates
  A  adapter convergence + F1/F2 + Hermes source proof + CLI smoke
  R  successor regression corpus reconciliation
  M  forbidden M3/M4 capability scan
  V  lane validator availability

Source-only; no network, no live Hermes runtime, no deploy.
Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AOTA_FORGE_ROOT = REPO_ROOT / "aota_forge"
CLI_ROOT = AOTA_FORGE_ROOT / "cli"
sys.path.insert(0, str(REPO_ROOT))

results: list[dict[str, object]] = []

M2_COMMON_BASE = "a380056b70ec11d45259f31babdfb38311672893"
M2_A = "7beffe91a76c07d3b8570ceb84ce63e8115def55"
M2_B = "1a717f7e579d17c863263510617a1ee0595d36b4"
M2_D_ORIGINAL = "427b97167abcae6d05ce66e1db13e1bf683f1786"
M2_D_ASSEMBLED = "1bb18d25b68cdfb3be93b59fccb685a0ca642a2c"
M2_E_ORIGINAL = "d0194473c5a7094836bf9c770dc0a1e8aae09a32"
M2_E_ASSEMBLED = "59a5af31aff046b1dae0ce956daad36878171531"
M2_F = "66eb6697e785e25e6e1e097f291e409fd1d3b454"
M2_C_ORIGINAL = "6ef62cb67c3ab1f19b47dd09b35369050e88843c"
M2_C_INTEGRATED = "11498198582dc58989d7ad07076540e6ecc9978c"
M2_I_BRANCH = "aota/m2/integration"

FORBIDDEN_DEF_NAMES = frozenset(
    {
        "create_subject", "bind_subject", "unbind_subject", "mint_authority",
        "mint_internal_id", "grant_lease", "revoke_lease", "mutate_revision",
        "write_revision", "cas_update", "compare_and_swap", "transition_plan",
        "recover_plan", "recovery_engine", "write_plan", "save_plan",
        "update_github", "comment_github", "dispatch_worker", "run_worker",
        "dispatch_task", "materialize",
    }
)

FORBIDDEN_CLASS_NAMES = frozenset(
    {
        "DurableSubjectGraph", "InternalIdBroker", "CapabilityLease",
        "TransitionEngine", "RecoveryEngine", "PlanWriteAdapter",
        "GithubPlanAuthority",
    }
)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def run_py(source: str, env: dict[str, str] | None = None, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=run_env, timeout=timeout,
    )


def run_cli(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    run_env.pop("AOTA_FORGE_ADAPTER_CONFIG", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "aota_forge.cli", *argv],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=run_env, timeout=120,
    )


def git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=60)


def git_ok(args: list[str]) -> bool:
    return git(args).returncode == 0


def is_ancestor(sha: str, head: str = "HEAD") -> bool:
    return git_ok(["merge-base", "--is-ancestor", sha, head])


def load_corpus_module():
    spec = importlib.util.spec_from_file_location(
        "m2_regression_corpus", REPO_ROOT / "scripts" / "m2_regression_corpus.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_project(workspace: Path, project_id: str) -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
        "summary": "M2-I acceptance fixture project",
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
    (project_dir / ".aota" / "project.yaml").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return project_dir


def scan_forbidden_imports(package_root: Path, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{path}: syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                folded = name.replace("-", "_").casefold()
                if any(pattern in folded for pattern in forbidden):
                    violations.append(f"{path}:{node.lineno}: {name}")
    return sorted(set(violations))


def part_p() -> None:
    chain = [M2_COMMON_BASE, M2_A, M2_B, M2_D_ASSEMBLED, M2_E_ASSEMBLED, M2_F, M2_C_INTEGRATED]
    present = all(git_ok(["cat-file", "-t", sha]) for sha in chain)
    ordered = all(is_ancestor(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
    check("p1_lineage_present", present)
    check("p2_lineage_ordered", ordered)
    parent = git(["rev-parse", f"{M2_C_INTEGRATED}^"]).stdout.strip()
    check("p3_m2c_parent_is_m2f", parent == M2_F, parent)
    originals_exist = all(git_ok(["cat-file", "-t", sha]) for sha in (M2_D_ORIGINAL, M2_E_ORIGINAL, M2_C_ORIGINAL))
    check("p4_originals_exist", originals_exist)
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    check("p5_integration_branch", branch == M2_I_BRANCH, branch)
    provenance_path = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m2-integration-provenance.json"
    if not provenance_path.is_file():
        check("p6_provenance_artifact_matches", False, "artifact missing")
        return
    artifact = json.loads(provenance_path.read_text(encoding="utf-8"))
    ok = (
        artifact.get("schema_version") == 1
        and artifact.get("m2_common_base") == M2_COMMON_BASE
        and artifact.get("starting_head") == M2_F
        and artifact.get("integration_branch") == M2_I_BRANCH
        and artifact.get("lanes", {}).get("M2-A", {}).get("commit") == M2_A
        and artifact.get("lanes", {}).get("M2-B", {}).get("commit") == M2_B
        and artifact.get("lanes", {}).get("M2-F", {}).get("commit") == M2_F
        and artifact.get("lanes", {}).get("M2-D", {}).get("original_commit") == M2_D_ORIGINAL
        and artifact.get("lanes", {}).get("M2-D", {}).get("assembled_commit") == M2_D_ASSEMBLED
        and artifact.get("lanes", {}).get("M2-E", {}).get("original_commit") == M2_E_ORIGINAL
        and artifact.get("lanes", {}).get("M2-E", {}).get("assembled_commit") == M2_E_ASSEMBLED
        and artifact.get("lanes", {}).get("M2-C", {}).get("original_commit") == M2_C_ORIGINAL
        and artifact.get("lanes", {}).get("M2-C", {}).get("integrated_commit") == M2_C_INTEGRATED
    )
    check("p6_provenance_artifact_matches", ok)


def part_c() -> None:
    from aota_forge.core.bootstrap import ensure_handlers_bound
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.contracts.version import PROTOCOL_VERSION

    ensure_handlers_bound()
    names = DEFAULT_REGISTRY.names()
    expected = {"git.inspect", "host.status", "operations.list", "project.resolve", "runtime.status"}
    check("c0_canonical_surface_exact", set(names) == expected, str(sorted(names)))

    serializable = True
    roundtrip = True
    for name in names:
        descriptor = DEFAULT_REGISTRY.get(name)
        try:
            parsed = json.loads(descriptor.to_canonical_json())
            if parsed != descriptor.to_dict():
                serializable = False
            rebuilt = OperationContractDescriptor.from_dict(descriptor.to_dict())
            if rebuilt.to_dict() != descriptor.to_dict() or rebuilt.contract_hash() != descriptor.contract_hash():
                roundtrip = False
        except Exception:
            serializable = False
            roundtrip = False
    check("c1_descriptor_serializable", serializable)
    check("c1_descriptor_roundtrip", roundtrip)

    separated = True
    for name in names:
        descriptor = DEFAULT_REGISTRY.get(name)
        if callable(descriptor) or not all(
            isinstance(value, (str, bool, int, float, list, dict, type(None)))
            for value in descriptor.to_dict().values()
        ):
            separated = False
    check("c2_handler_binding_separated", separated and all(callable(DEFAULT_REGISTRY.handler(n)) for n in names))

    deterministic = all(
        DEFAULT_REGISTRY.get(n).contract_hash() == DEFAULT_REGISTRY.get(n).contract_hash() for n in names
    )
    check("c3_contract_hash_deterministic_local", deterministic)

    sub_source = (
        "from aota_forge.core.bootstrap import ensure_handlers_bound;"
        "from aota_forge.core.contracts.registry import DEFAULT_REGISTRY;"
        "ensure_handlers_bound();"
        "print('|'.join(DEFAULT_REGISTRY.get(n).contract_hash() for n in sorted(DEFAULT_REGISTRY.names())))"
    )
    local_joined = "|".join(DEFAULT_REGISTRY.get(n).contract_hash() for n in sorted(names))
    proc = run_py(sub_source)
    check(
        "c3_contract_hash_cross_process",
        proc.returncode == 0 and proc.stdout.strip() == local_joined,
        proc.stderr.strip()[:200],
    )

    check(
        "c4_protocol_version",
        PROTOCOL_VERSION == "1.0" and all(DEFAULT_REGISTRY.get(n).protocol_version == PROTOCOL_VERSION for n in names),
    )


def part_i() -> None:
    declarative = (
        "import sys;"
        "import aota_forge.core.contracts.descriptor;"
        "import aota_forge.core.contracts.registry;"
        "import aota_forge.core.contracts.version;"
        "import aota_forge.core.contracts.errors;"
        "import aota_forge.core.plan.read_model;"
        "print('isolated' if 'aota_forge.core.handlers' not in sys.modules else 'bound')"
    )
    proc = run_py(declarative)
    check("i1_declarative_import_no_handler_binding", proc.returncode == 0 and proc.stdout.strip() == "isolated", proc.stdout.strip() + proc.stderr.strip()[:200])

    bootstrap_source = (
        "from aota_forge.core.bootstrap import ensure_handlers_bound;"
        "from aota_forge.core.contracts.registry import DEFAULT_REGISTRY;"
        "ensure_handlers_bound();"
        "print('|'.join(f'{n}:{DEFAULT_REGISTRY.handler(n).__module__}' for n in sorted(DEFAULT_REGISTRY.names())))"
    )
    run_a = run_py(bootstrap_source)
    run_b = run_py(bootstrap_source)
    deterministic = (
        run_a.returncode == 0
        and run_b.returncode == 0
        and bool(run_a.stdout.strip())
        and run_a.stdout.strip() == run_b.stdout.strip()
    )
    check("i2_handler_bootstrap_deterministic", deterministic, f"{run_a.stdout.strip()[:100]} vs {run_b.stdout.strip()[:100]}")

    lazy = (
        "import sys;"
        "from aota_forge.core import execute;"
        "before = 'aota_forge.core.handlers' in sys.modules;"
        "result = execute('operations.list', {});"
        "after = 'aota_forge.core.handlers' in sys.modules;"
        "print(before, after, result.get('ok'))"
    )
    proc = run_py(lazy)
    check("i3_lazy_bootstrap_on_execution", proc.returncode == 0 and proc.stdout.strip() == "False True True", proc.stdout.strip() + proc.stderr.strip()[:200])

    from aota_forge.core.bootstrap import ensure_handlers_bound
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    ensure_handlers_bound()
    snapshot_a = {n: DEFAULT_REGISTRY.handler(n) for n in DEFAULT_REGISTRY.names()}
    ensure_handlers_bound()
    snapshot_b = {n: DEFAULT_REGISTRY.handler(n) for n in DEFAULT_REGISTRY.names()}
    idempotent = snapshot_a == snapshot_b and all(handler is snapshot_a[n] for n, handler in snapshot_b.items())
    check("i4_handler_bootstrap_idempotent", idempotent)


def part_d() -> None:
    from aota_forge.adapters import hermes as hermes_adapter
    from aota_forge.cli import projection as cli_projection
    from aota_forge.core.bootstrap import ensure_handlers_bound
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS
    from aota_forge.core.contracts.version import PROTOCOL_VERSION

    ensure_handlers_bound()
    sha_re = re.compile(r"^[0-9a-f]{64}$")
    consistency = True
    projection_ok = True
    for name in DEFAULT_REGISTRY.names():
        descriptor = DEFAULT_REGISTRY.get(name)
        handler = DEFAULT_REGISTRY.handler(name)
        if descriptor is None or not callable(handler):
            consistency = False
            continue
        if descriptor.protocol_version != PROTOCOL_VERSION or not sha_re.fullmatch(descriptor.contract_hash()):
            consistency = False
        hs = hermes_adapter.operation_request_schema(descriptor)
        expected_args = [
            {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
            for spec in descriptor.inputs
        ]
        if (
            hs["arguments"] != expected_args
            or hs["protocol_version"] != descriptor.protocol_version
            or hs["contract_hash"] != descriptor.contract_hash()
            or hs["trusted_metadata"] != sorted(TRUSTED_ADAPTER_KEYS)
        ):
            projection_ok = False
        cs = cli_projection.operation_schema(name)
        semantic_args = [
            {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
            for spec in descriptor.inputs
            if spec.name not in TRUSTED_ADAPTER_KEYS
        ]
        if (
            cs is None
            or cs["arguments"] != semantic_args
            or cs["protocol_version"] != descriptor.protocol_version
            or cs["contract_hash"] != descriptor.contract_hash()
        ):
            projection_ok = False
    check("d1_descriptor_handler_consistency", consistency)
    check("d2_adapter_projection_derives_from_descriptor", projection_ok)


def part_t() -> None:
    from aota_forge.core import execute

    workdir = Path(tempfile.mkdtemp(prefix="m2i-trusted-"))
    try:
        pid_root = workdir / "pidfiles"
        receipt_root = workdir / "receipts"
        registry_root = workdir / "registries"
        workspace = workdir / "workspace"
        for root in (pid_root, receipt_root, registry_root, workspace):
            root.mkdir(parents=True, exist_ok=True)
        (pid_root / "alive.pid").write_text(str(os.getpid()), encoding="utf-8")
        (receipt_root / "dep-1").mkdir()
        (receipt_root / "dep-1" / "deployment.json").write_text(
            json.dumps({"schema_version": 1, "deployment": "ok"}), encoding="utf-8"
        )
        make_project(workspace, "fixture-alpha")
        (registry_root / "canonical.json").write_text(
            json.dumps({"fixture-ws": {"candidates": [str(workspace)]}}), encoding="utf-8"
        )

        env = {
            "AOTA_FORGE_HOST_ROOT_PIDFILE": str(pid_root),
            "AOTA_FORGE_HOST_ROOT_RECEIPTS": str(receipt_root),
            "AOTA_FORGE_HOST_ROOT_REGISTRY": str(registry_root),
        }
        saved = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        try:
            pid_result = execute("host.status", {"pidfile_id": "alive"}, principal="m2i")
            check(
                "t1_host_logical_pidfile_resolves",
                pid_result.get("ok") is True
                and pid_result.get("data", {}).get("process", {}).get("running") is True
                and pid_result.get("data", {}).get("checks", {}).get("process", {}).get("state") == "available",
            )

            receipt_result = execute("host.status", {"receipt_id": "dep-1"}, principal="m2i")
            check(
                "t2_host_logical_receipt_resolves",
                receipt_result.get("ok") is True
                and receipt_result.get("data", {}).get("deployment_receipt", {}).get("schema_version") == 1
                and receipt_result.get("data", {}).get("checks", {}).get("deployment_receipt", {}).get("state") == "available",
            )

            project_result = execute(
                "host.status",
                {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_id": "canonical"},
                principal="m2i",
            )
            check(
                "t3_host_logical_registry_resolves",
                project_result.get("ok") is True
                and project_result.get("data", {}).get("project", {}).get("project_id") == "fixture-alpha"
                and project_result.get("data", {}).get("checks", {}).get("project", {}).get("state") == "available",
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        denied_result = execute("host.status", {"pidfile_id": "alive"}, principal="m2i")
        check(
            "t4_host_logical_unconfigured_denied",
            denied_result.get("ok") is True
            and denied_result.get("warnings")
            and denied_result.get("data", {}).get("checks", {}).get("process", {}).get("state") == "failed"
            and denied_result.get("data", {}).get("checks", {}).get("process", {}).get("error_code") == "HOST_RESOURCE_DENIED",
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    from aota_forge.adapters import hermes as hermes_adapter
    from aota_forge.core.bootstrap import ensure_handlers_bound
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS

    ensure_handlers_bound()
    path_flag_hits = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("--registry", "--pidfile", "--receipt", "--path")
            ):
                path_flag_hits.append(f"{path}:{node.lineno}")
    check("t5_cli_no_path_flags", not path_flag_hits, "; ".join(path_flag_hits[:3]))

    semantic_path = any(
        "path" in spec.name and spec.name not in TRUSTED_ADAPTER_KEYS
        for name in DEFAULT_REGISTRY.names()
        for spec in DEFAULT_REGISTRY.get(name).inputs
    )
    check("t6_descriptor_semantic_no_path_inputs", not semantic_path)

    hermes_path = any(
        "path" in arg["name"]
        for name in DEFAULT_REGISTRY.names()
        for arg in hermes_adapter.operation_request_schema(DEFAULT_REGISTRY.get(name))["arguments"]
        if arg["name"] not in TRUSTED_ADAPTER_KEYS
    )
    check("t7_hermes_semantic_no_path_arguments", not hermes_path)

    process_text = (AOTA_FORGE_ROOT / "adapters" / "host" / "process.py").read_text(encoding="utf-8")
    check("t8_low_level_bounded_readers_present", "read_pidfile" in process_text and "read_receipt" in process_text)


def part_e() -> None:
    from aota_forge.core import execute
    from aota_forge.core.contracts.errors import (
        ERROR_CLASSES,
        HostResourceDeniedError,
        error_from_dict,
    )
    from aota_forge.core.project.resolver import resolve_project_with_fingerprint

    registered = "HOST_RESOURCE_DENIED" in ERROR_CLASSES
    instance = HostResourceDeniedError("host resource denied", retryable=False, details={"kind": "x"})
    restored = error_from_dict(instance.to_dict())
    roundtrip_ok = (
        isinstance(restored, HostResourceDeniedError)
        and restored.code == "HOST_RESOURCE_DENIED"
        and restored.message == instance.message
        and restored.retryable is False
        and restored.details == instance.details
    )
    check("e1_host_resource_error_machine_semantics", registered and roundtrip_ok)

    from aota_forge.core.plan.normalize import PlanNormalizationError, normalize_portable_plan

    private = "PLAN_NORMALIZATION_ERROR" not in ERROR_CLASSES
    handlers_text = (AOTA_FORGE_ROOT / "core" / "handlers.py").read_text(encoding="utf-8")
    ingress_text = (AOTA_FORGE_ROOT / "core" / "ingress.py").read_text(encoding="utf-8")
    not_surfaced = "PlanNormalizationError" not in handlers_text and "PlanNormalizationError" not in ingress_text
    try:
        normalize_portable_plan("")
        deterministic = False
    except PlanNormalizationError as exc:
        deterministic = exc.code == "PLAN_NORMALIZATION_ERROR" and exc.diagnostic_code == "EMPTY_PLAN_BODY"
    check("e2_plan_normalization_error_private_and_deterministic", private and not_surfaced and deterministic)

    codes = sorted(ERROR_CLASSES)
    distinct = len(codes) >= 25 and len(set(codes)) == len(codes)
    check("e3_b005_distinct_codes", distinct, f"len={len(codes)}")

    binding = execute("host.status", {"workspace_id": "w", "project_id": "p"}, principal="m2i")
    check(
        "e4_b004_binding_missing_surfaced",
        binding.get("ok") is False and binding.get("errors", [{}])[0].get("code") == "PROJECT_BINDING_MISSING",
    )

    workdir = Path(tempfile.mkdtemp(prefix="m2i-b006-"))
    try:
        ws = workdir / "ws"
        ws.mkdir()
        for index in range(55):
            make_project(ws, f"proj-{index:03d}")
        registry = workdir / "registry.json"
        registry.write_text(json.dumps({"ws55": {"candidates": [str(ws)]}}), encoding="utf-8")
        resolved = resolve_project_with_fingerprint("ws55", registry, "proj-054")
        check(
            "e5_b006_completeness_beyond_listing",
            resolved["project_id"] == "proj-054"
            and resolved["project_count"] == 55
            and resolved["listing_truncated"] is True,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def part_g() -> None:
    from aota_forge.core import execute

    unknown = execute("runtime.status", {"pid": os.getpid(), "banana": 1}, principal="m2i")
    check("g1_unknown_input_rejected", unknown.get("ok") is False and unknown["errors"][0]["code"] == "UNKNOWN_INPUT")
    missing = execute("runtime.status", {}, principal="m2i")
    check("g2_required_input_enforced", missing.get("ok") is False and missing["errors"][0]["code"] == "REQUIRED_INPUT_MISSING")
    wrong = execute("runtime.status", {"pid": "not-int"}, principal="m2i")
    check("g3_input_type_validated", wrong.get("ok") is False and wrong["errors"][0]["code"] == "INPUT_TYPE_INVALID")
    big = execute("runtime.status", {"pid": 2**70}, principal="m2i")
    check("g4_input_size_bounded", big.get("ok") is False and big["errors"][0]["code"] == "INPUT_SIZE_EXCEEDED")
    distinct_codes = {
        execute("project.resolve", {}, principal="m2i").get("errors", [{}])[0].get("code"),
        execute("runtime.status", {"pid": "x"}, principal="m2i").get("errors", [{}])[0].get("code"),
        execute("m2i.unknown-op", {}, principal="m2i").get("errors", [{}])[0].get("code"),
    }
    check("g5_error_semantics_distinct", len(distinct_codes) == 3 and all(distinct_codes), str(distinct_codes))

    from aota_forge.core.plan.normalize import normalize_portable_plan, observe_governance_projection_drift

    body = (
        "# [PLAN] m2i\n\n"
        "## 1. Current State\n\n```text\n"
        "PLAN_STATUS=in-progress\nCURRENT_MILESTONE=M2\nHANDOFF_STATE=m2_planning_ready\n```\n"
    )
    doc = normalize_portable_plan(body)
    drift = observe_governance_projection_drift(doc, {"index": {"CURRENT_MILESTONE": "M1"}})
    check("g6_portable_plan_normalization", doc.current_milestone == "M2")
    check(
        "g7_governance_projection_drift_observation",
        drift["GOVERNANCE_PROJECTION_DRIFT"] == "yes"
        and drift["authority"] == "issue_body"
        and drift["automatic_repair"] == "not_implemented",
    )


def part_a() -> None:
    from aota_forge.adapters import hermes as hermes_adapter
    from aota_forge.cli import exit_codes as ec
    from aota_forge.core import execute

    def normalized(payload: dict) -> dict:
        return {k: v for k, v in payload.items() if k not in ("correlation_id", "audit")}

    direct = execute("operations.list", {}, principal="m2i")
    hermes_result = hermes_adapter.execute_request(hermes_adapter.build_request("operations.list", {}))
    cli_proc = run_cli(["operations", "--json"])
    try:
        cli_payload = json.loads(cli_proc.stdout)
    except json.JSONDecodeError:
        cli_payload = {}
    check(
        "a1_surface_machine_equality",
        cli_proc.returncode == 0
        and cli_payload.get("ok") is True
        and normalized(direct) == normalized(hermes_result) == normalized(cli_payload),
    )

    check(
        "a2_exit_constants_frozen",
        (ec.EXIT_SUCCESS, ec.EXIT_ERROR, ec.EXIT_USAGE, ec.EXIT_NEEDS_SEMANTIC_CHOICE, ec.EXIT_BLOCKED) == (0, 1, 2, 3, 4),
    )
    drift_payload = hermes_adapter.execute_request(
        hermes_adapter.build_request("operations.list", {}, expected_contract_hash="0" * 64)
    )
    check(
        "a3_drift_blocked_projection",
        drift_payload.get("ok") is False
        and drift_payload.get("errors", [{}])[0].get("code") == "CONTRACT_VERSION_MISMATCH"
        and ec.classify(drift_payload) == 4,
    )

    hermes_root = AOTA_FORGE_ROOT / "adapters" / "hermes"
    hermes_violations = scan_forbidden_imports(hermes_root, ("plugin", "hermes", "docker", "requests", "webui", "outbox"))
    mega_names = {"control", "run_any", "invoke_any", "dispatch", "execute_any", "passthrough", "call_operation"}
    mega_hits = []
    for path in sorted(hermes_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and node.name in mega_names:
                mega_hits.append(f"{path}:{node.lineno}:{node.name}")
    markers_ok = (
        hermes_adapter.HERMES_ADAPTER_LIVE_RUNTIME_REQUIRED == "no"
        and hermes_adapter.HERMES_ADAPTER_LEGACY_CONTROL_PLANE_DEPENDENCY == "no"
    )
    check("a4_hermes_source_convergence", not hermes_violations and not mega_hits and markers_ok)

    legacy_tokens = ("aota-hermes-tools", "canonical-workspace", "/home/latios/workspace/.aota", "CANONICAL_REGISTRY_CANDIDATES", "default_registry_path")
    legacy_hits = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in legacy_tokens:
            if token in text:
                legacy_hits.append(f"{path}: {token}")
    check("a5_f1_legacy_registry_fallback_absent", not legacy_hits, "; ".join(legacy_hits[:3]))

    workdir = Path(tempfile.mkdtemp(prefix="m2i-cli-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        make_project(workspace, "fixture-alpha")
        subprocess.run(["git", "init", "-q", str(workspace / "fixture-alpha")], check=True, capture_output=True)
        (workspace / "fixture-alpha" / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(workspace / "fixture-alpha"), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(workspace / "fixture-alpha"), "-c", "user.email=m2i@t", "-c", "user.name=m2i", "commit", "-qm", "m2i"],
            check=True, capture_output=True,
        )
        trusted = workdir / "trusted"
        trusted.mkdir()
        (trusted / "canonical.json").write_text(
            json.dumps({"fixture-ws": {"candidates": [str(workspace)]}}), encoding="utf-8"
        )
        (trusted / "alive.pid").write_text(str(os.getpid()), encoding="utf-8")
        adapter_config = workdir / "adapter-config.json"
        adapter_config.write_text(
            json.dumps(
                {
                    "registry_id": "canonical",
                    "trusted_roots": {
                        "project_registry": str(trusted),
                        "runtime_pidfile": str(trusted),
                    },
                }
            ),
            encoding="utf-8",
        )
        cli_env = {"AOTA_FORGE_ADAPTER_CONFIG": str(adapter_config)}

        ops_proc = run_cli(["operations", "--json"])
        ops_ok = ops_proc.returncode == 0 and json.loads(ops_proc.stdout).get("ok") is True
        resolve_proc = run_cli(["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"], cli_env)
        resolve_ok = resolve_proc.returncode == 0 and json.loads(resolve_proc.stdout).get("data", {}).get("project_id") == "fixture-alpha"
        git_proc = run_cli(["git", "inspect", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"], cli_env)
        git_ok_check = (
            git_proc.returncode == 0
            and json.loads(git_proc.stdout).get("data", {}).get("available") is True
            and len(json.loads(git_proc.stdout).get("data", {}).get("head_sha", "")) == 40
        )
        runtime_proc = run_cli(["runtime", "status", "--pid", str(os.getpid()), "--json"])
        runtime_ok = runtime_proc.returncode == 0 and json.loads(runtime_proc.stdout).get("data", {}).get("running") is True
        host_proc = run_cli(["host", "status", "--pidfile-id", "alive", "--json"], cli_env)
        host_ok = host_proc.returncode == 0 and json.loads(host_proc.stdout).get("data", {}).get("process", {}).get("running") is True
        check("a6_cli_smoke", ops_ok and resolve_ok and git_ok_check and runtime_ok and host_ok)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def part_r() -> None:
    corpus = load_corpus_module()
    matrix_path = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m2-successor-regression-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    schema_errors = corpus.validate_matrix(matrix)
    check("r1_corpus_schema_valid", not schema_errors, str(schema_errors[:3]))
    if schema_errors:
        return
    evidence = corpus.execute_evidence(matrix)
    all_pass = all(item.get("pass") for item in evidence.values())
    check("r2_corpus_evidence_executes", all_pass, str([k for k, v in evidence.items() if not v.get("pass")][:5]))
    classes = matrix["failure_classes"]
    nyi = [e for e in classes if e["expected_successor_disposition"] == "NOT_YET_IMPLEMENTED"]
    passed = [e for e in classes if e["expected_successor_disposition"] != "NOT_YET_IMPLEMENTED"]
    check("r3_corpus_class_counts", len(classes) == 16 and len(nyi) == 12 and len(passed) == 4, f"classes={len(classes)} nyi={len(nyi)} pass={len(passed)}")
    evidence_ok = all(
        any(
            isinstance(item, dict) and item.get("kind") == "fixture" and item.get("proves") == "successor_pass"
            for item in entry["executable_evidence"]
        )
        for entry in passed
    )
    check("r4_pass_entries_have_executable_evidence", evidence_ok)
    violations = corpus.scan_forbidden_imports(AOTA_FORGE_ROOT)
    check("r5_no_legacy_control_plane_imports", not violations, str(violations[:3]))


def part_m() -> None:
    def_hits = []
    class_hits = []
    for path in sorted(AOTA_FORGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.casefold() in FORBIDDEN_DEF_NAMES:
                def_hits.append(f"{path}:{node.lineno}:{node.name}")
            elif isinstance(node, ast.ClassDef) and node.name in FORBIDDEN_CLASS_NAMES:
                class_hits.append(f"{path}:{node.lineno}:{node.name}")
    check("m1_no_forbidden_m34_symbols", not def_hits and not class_hits, "; ".join((def_hits + class_hits)[:3]))

    github_violations = scan_forbidden_imports(AOTA_FORGE_ROOT, ("github", "pygithub", "requests", "urllib", "httpx", "aiohttp"))
    check("m2_github_core_coupling", not github_violations, str(github_violations[:3]))

    from aota_forge.core.context import OperationContext

    ctx = OperationContext(operation="x", correlation_id="c", principal="p", protocol_version="1.0", contract_hash="h")
    placeholders_none = all(
        getattr(ctx, field) is None
        for field in ("workflow", "subject", "internal_ids", "revision", "authority", "capability_lease")
    )
    check("m3_future_placeholders_stay_none", placeholders_none)


def part_v() -> None:
    lane_validators = (
        "m2a_contract_foundation.py",
        "m2b_ingress_contract.py",
        "m2_regression_corpus.py",
        "m2_d_fixtures.py",
        "m2_d_guard.py",
        "m2_e_fixtures.py",
        "m2f_adapter_convergence.py",
        "m1_fixtures.py",
        "m1_dependency_guard.py",
    )
    missing = [name for name in lane_validators if not (REPO_ROOT / "scripts" / name).is_file()]
    check("v1_all_lane_validators_present", not missing, str(missing))


def main() -> int:
    part_p()
    part_c()
    part_i()
    part_d()
    part_t()
    part_e()
    part_g()
    part_a()
    part_r()
    part_m()
    part_v()

    by_name = {r["check"]: r["pass"] for r in results}
    gate_map = {
        "OPERATION_CONTRACT_DESCRIPTOR_SERIALIZABLE": "c1_descriptor_serializable",
        "HANDLER_BINDING_SEPARATED_FROM_DESCRIPTOR": "c2_handler_binding_separated",
        "CONTRACT_HASH_DETERMINISTIC": "c3_contract_hash_cross_process",
        "PROTOCOL_VERSION=1.0": "c4_protocol_version",
        "UNKNOWN_INPUT_REJECTED": "g1_unknown_input_rejected",
        "REQUIRED_INPUT_ENFORCED": "g2_required_input_enforced",
        "INPUT_TYPE_VALIDATED": "g3_input_type_validated",
        "INPUT_SIZE_BOUNDED": "g4_input_size_bounded",
        "ERROR_SEMANTICS_DISTINCT": "g5_error_semantics_distinct",
        "MODEL_FACING_ARBITRARY_FILESYSTEM_PATH=denied": "t5_cli_no_path_flags",
        "TRUSTED_RESOURCE_RESOLUTION": "t1_host_logical_pidfile_resolves",
        "PROJECT_RESOLUTION_COMPLETE": "e5_b006_completeness_beyond_listing",
        "PORTABLE_PLAN_DOCUMENT_NORMALIZATION": "g6_portable_plan_normalization",
        "GOVERNANCE_PROJECTION_DRIFT_OBSERVATION": "g7_governance_projection_drift_observation",
        "ISSUE_BODY_AUTHORITY_PRESERVED=yes": "g7_governance_projection_drift_observation",
        "GITHUB_CORE_COUPLING=no": "m2_github_core_coupling",
        "DIRECT_LIBRARY_CONVERGENCE": "a1_surface_machine_equality",
        "CLI_CONVERGENCE": "a1_surface_machine_equality",
        "HERMES_ADAPTER_CONVERGENCE": "a1_surface_machine_equality",
        "HERMES_ADAPTER_SOURCE_CONVERGENCE": "a4_hermes_source_convergence",
        "CONTRACT_HASH_CONVERGENCE": "a1_surface_machine_equality",
        "CLI_EXIT_MAPPING": "a2_exit_constants_frozen",
        "MODEL_FACING_MEGA_TOOL=no": "a4_hermes_source_convergence",
        "ISSUE_8_SUCCESSOR_REGRESSION_MATRIX=materialized": "r1_corpus_schema_valid",
        "M2_REGRESSION_CORPUS_RECONCILED": "r2_corpus_evidence_executes",
        "UNSUPPORTED_SUCCESS_CLAIMS=0": "r1_corpus_schema_valid",
    }
    print("\nACCEPTANCE GATES")
    for gate, check_name in gate_map.items():
        value = "PASS" if by_name.get(check_name) else "FAIL"
        print(f"  {gate}={value}")

    passed = all(item["pass"] for item in results)
    print(f"\nM2-I ACCEPTANCE: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
