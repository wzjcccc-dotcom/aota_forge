#!/usr/bin/env python3
"""M2-F focused deterministic validation — Adapter Projection & Convergence.

Proves that direct library invocation, the CLI and the Hermes source
adapter converge on the same canonical Forge operation contract and the
same Unified Ingress:

    1  descriptor-driven adapter schema projection
    2  direct-library invocation
    3  CLI --json equivalent invocation
    4  Hermes adapter equivalent invocation
    5  protocol_version identical across surfaces
    6  contract_hash identical across surfaces
    7  invalid unknown input converges
    8  wrong-type input converges
    9  semantic error code converges
    10 PROJECT_AMBIGUOUS / semantic-choice exit projection = 3
    11 ordinary error exit = 1
    12 usage error exit = 2
    13 blocked projection = 4
    14 success exit = 0
    15 F1 legacy canonical default absent/contained
    16 arbitrary model filesystem path not reintroduced
    17 no model-facing mega-tool
    18 Hermes adapter has no legacy control-plane imports
    19 no duplicated adapter authority logic
    20 no live Hermes dependency

Plus success-path and error-path convergence (§22/§23).  Source/isolated
only: no network, no runtime/live validation, no deploy dependency.
Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
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


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def make_project(workspace: Path, project_id: str, with_git: bool = False) -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
        "summary": "M2-F fixture project",
        "capabilities": ["fixture"],
        "paths": {"source_root": ".", "source": ["."], "docs": ["docs"], "scripts": ["scripts"], "profiles": ["profiles"], "skills": ["skills"], "tests": ["tests"]},
        "commands": {"validate": ["validate"], "deploy": ["deploy"], "verify_deploy": ["verify"]},
        "runtime": {"deployment_type": "managed-files", "requires_human_checkpoint": False},
        "codegraph": {"enabled": False, "index_location": ".codegraph/"},
        "plan": {"active_plan_id": None},
        "constraints": [],
    }
    (project_dir / ".aota" / "project.yaml").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if with_git:
        subprocess.run(["git", "init", "-q"], cwd=str(project_dir), capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=str(project_dir), capture_output=True, check=False)
        subprocess.run(["git", "config", "user.name", "fixture"], cwd=str(project_dir), capture_output=True, check=False)
        (project_dir / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(project_dir), capture_output=True, check=False)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=str(project_dir), capture_output=True, check=False)
    return project_dir


def run_cli(argv: list[str], config_path: Path | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if config_path is None:
        env.pop("AOTA_FORGE_ADAPTER_CONFIG", None)
    else:
        env["AOTA_FORGE_ADAPTER_CONFIG"] = str(config_path)
    return subprocess.run(
        [sys.executable, "-m", "aota_forge.cli", *argv],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=120,
    )


def cli_payload(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def normalized(payload: dict) -> dict:
    """Drop transport-only differences for machine-result comparison."""
    copy = {k: v for k, v in payload.items() if k not in ("correlation_id", "audit")}
    return copy


def scan_python_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.py")))
    return files


def import_violations(files: list[Path], forbidden_modules: tuple, forbidden_names: dict) -> list[str]:
    violations: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{path}: syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules or alias.name.split(".")[0] in forbidden_modules:
                        violations.append(f"{path}:{node.lineno}: forbidden import: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in forbidden_modules or node.module.split(".")[0] in forbidden_modules:
                    violations.append(f"{path}:{node.lineno}: forbidden import: {node.module}")
                banned = forbidden_names.get(node.module, ())
                for alias in node.names:
                    if alias.name in banned:
                        violations.append(f"{path}:{node.lineno}: forbidden name import: {node.module}.{alias.name}")
    return sorted(set(violations))


def main() -> int:
    from aota_forge.core import execute
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS
    from aota_forge.core.contracts.version import PROTOCOL_VERSION
    from aota_forge.cli import exit_codes as ec
    from aota_forge.cli import projection as cli_projection
    from aota_forge.adapters import hermes as hermes_adapter

    workdir = Path(tempfile.mkdtemp(prefix="m2f-fixtures-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        make_project(workspace, "fixture-alpha", with_git=True)
        make_project(workspace, "fixture-beta")
        make_project(workspace, "fixture-noversion")

        trusted = workdir / "trusted"
        trusted.mkdir()
        registry_doc = {"fixture-ws": {"candidates": [str(workspace)]}}
        canonical_registry = trusted / "canonical.json"
        canonical_registry.write_text(json.dumps(registry_doc), encoding="utf-8")
        pidfile = trusted / "alive.pid"
        pidfile.write_text(str(os.getpid()), encoding="utf-8")

        adapter_config = workdir / "adapter-config.json"
        adapter_config.write_text(
            json.dumps(
                {
                    "registry_id": "canonical",
                    "trusted_roots": {
                        "project_registry": str(trusted),
                        "runtime_pidfile": str(trusted),
                        "managed_deployment_receipt": str(trusted),
                    },
                }
            ),
            encoding="utf-8",
        )

        registry_param = {"registry_path": str(canonical_registry)}
        operations = ("operations.list", "project.resolve", "git.inspect", "runtime.status", "host.status")

        # --- 1. descriptor-driven adapter schema projection -------------------------
        schemas_ok = True
        for op in operations:
            descriptor = DEFAULT_REGISTRY.get(op)
            hs = hermes_adapter.operation_request_schema(descriptor)
            cs = cli_projection.operation_schema(op)
            expected_args = [
                {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
                for spec in descriptor.inputs
            ]
            semantic_args = [
                {"name": spec.name, "type": spec.type, "required": not spec.type.endswith("?")}
                for spec in descriptor.inputs
                if spec.name not in TRUSTED_ADAPTER_KEYS
            ]
            if (
                hs["arguments"] != expected_args
                or hs["protocol_version"] != descriptor.protocol_version
                or hs["contract_hash"] != descriptor.contract_hash()
                or hs["trusted_metadata"] != sorted(TRUSTED_ADAPTER_KEYS)
                or cs["arguments"] != semantic_args
                or cs["protocol_version"] != descriptor.protocol_version
                or cs["contract_hash"] != descriptor.contract_hash()
            ):
                schemas_ok = False
        check("m2f_01_descriptor_driven_schema_projection", schemas_ok)

        # --- 2. direct-library invocation -------------------------------------------
        direct_ops = execute("operations.list", {}, principal="library")
        check(
            "m2f_02_direct_library_invocation",
            direct_ops.get("ok") is True and isinstance(direct_ops.get("data", {}).get("operations"), list),
        )
        direct_proj = execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", **registry_param},
            principal="library",
        )
        check("m2f_02_direct_project_resolve", direct_proj.get("ok") is True)

        # --- 3. CLI --json equivalent invocation ------------------------------------
        cli_ops_proc = run_cli(["operations", "--json"], adapter_config)
        cli_ops = cli_payload(cli_ops_proc)
        check(
            "m2f_03_cli_json_equivalent",
            cli_ops_proc.returncode == 0 and cli_ops.get("ok") is True,
            f"rc={cli_ops_proc.returncode}",
        )
        check("m2f_03_cli_direct_machine_equality", normalized(direct_ops) == normalized(cli_ops))
        cli_proj_proc = run_cli(
            ["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"],
            adapter_config,
        )
        cli_proj = cli_payload(cli_proj_proc)
        check(
            "m2f_03_cli_project_resolve_equivalent",
            cli_proj_proc.returncode == 0 and cli_proj.get("ok") is True,
        )
        check(
            "m2f_03_cli_project_data_converges",
            direct_proj.get("data", {}).get("project_id") == cli_proj.get("data", {}).get("project_id"),
        )

        # --- 4. Hermes adapter equivalent invocation --------------------------------
        hermes_ops = hermes_adapter.execute_request(hermes_adapter.build_request("operations.list", {}))
        check(
            "m2f_04_hermes_equivalent",
            hermes_ops.get("ok") is True,
        )
        check("m2f_04_hermes_direct_machine_equality", normalized(direct_ops) == normalized(hermes_ops))
        hermes_proj = hermes_adapter.execute_request(
            hermes_adapter.build_request(
                "project.resolve",
                {"workspace_id": "fixture-ws", "project_id": "fixture-alpha"},
                trusted={"registry_path": str(canonical_registry)},
            )
        )
        check(
            "m2f_04_hermes_project_data_converges",
            hermes_proj.get("ok") is True
            and hermes_proj.get("data", {}).get("project_id") == direct_proj.get("data", {}).get("project_id"),
        )

        # --- 5. protocol_version identical across surfaces --------------------------
        surface_protocols = {
            "direct": direct_ops.get("audit", {}).get("protocol_version"),
            "cli": cli_ops.get("audit", {}).get("protocol_version"),
            "hermes": hermes_ops.get("audit", {}).get("protocol_version"),
        }
        check(
            "m2f_05_protocol_version_convergence",
            all(v == PROTOCOL_VERSION == "1.0" for v in surface_protocols.values()),
            str(surface_protocols),
        )

        # --- 6. contract_hash identical across surfaces -----------------------------
        surface_hashes = {
            "direct": direct_ops.get("audit", {}).get("contract_hash"),
            "cli": cli_ops.get("audit", {}).get("contract_hash"),
            "hermes": hermes_ops.get("audit", {}).get("contract_hash"),
        }
        canonical_hash = DEFAULT_REGISTRY.get("operations.list").contract_hash()
        check(
            "m2f_06_contract_hash_convergence",
            all(h == canonical_hash for h in surface_hashes.values()),
            str(surface_hashes),
        )
        proj_hashes = {
            "direct": direct_proj.get("audit", {}).get("contract_hash"),
            "cli": cli_proj.get("audit", {}).get("contract_hash"),
            "hermes": hermes_proj.get("audit", {}).get("contract_hash"),
        }
        check(
            "m2f_06_contract_hash_convergence_project",
            len(set(proj_hashes.values())) == 1
            and next(iter(proj_hashes.values())) == DEFAULT_REGISTRY.get("project.resolve").contract_hash(),
            str(proj_hashes),
        )

        # --- 7. invalid unknown input converges -------------------------------------
        direct_unknown = execute("runtime.status", {"pid": os.getpid(), "banana": 1})
        hermes_unknown = hermes_adapter.execute_request(
            hermes_adapter.build_request("runtime.status", {"pid": os.getpid(), "banana": 1})
        )
        check(
            "m2f_07_unknown_input_converges",
            direct_unknown.get("ok") is False
            and hermes_unknown.get("ok") is False
            and direct_unknown["errors"][0]["code"] == hermes_unknown["errors"][0]["code"] == "UNKNOWN_INPUT",
        )
        try:
            hermes_adapter.build_request(
                "project.resolve",
                {"workspace_id": "x", "registry_path": "/tmp/whatever"},
            )
            check("m2f_07_trusted_key_via_arguments_rejected", False, "not rejected")
        except hermes_adapter.AdapterRequestInvalidError:
            check("m2f_07_trusted_key_via_arguments_rejected", True)
        direct_oversized = execute("project.resolve", {"workspace_id": "x" * 5000, "project_id": "p", **registry_param})
        hermes_oversized = hermes_adapter.execute_request(
            hermes_adapter.build_request(
                "project.resolve",
                {"workspace_id": "x" * 5000, "project_id": "p"},
                trusted={"registry_path": str(canonical_registry)},
            )
        )
        check(
            "m2f_07_oversized_field_reaches_ingress",
            direct_oversized.get("ok") is False
            and hermes_oversized.get("ok") is False
            and direct_oversized["errors"][0]["code"] == hermes_oversized["errors"][0]["code"] == "INPUT_SIZE_EXCEEDED",
        )
        cli_unknown = run_cli(["runtime", "status", "--pid", str(os.getpid()), "--banana", "x"], adapter_config)
        check(
            "m2f_07_cli_unknown_flag_transport_rejection",
            cli_unknown.returncode == ec.EXIT_USAGE,
            f"rc={cli_unknown.returncode}",
        )

        # --- 8. wrong-type input converges ------------------------------------------
        direct_wrong = execute("runtime.status", {"pid": "not-an-int"})
        hermes_wrong = hermes_adapter.execute_request(
            hermes_adapter.build_request("runtime.status", {"pid": "not-an-int"})
        )
        check(
            "m2f_08_wrong_type_converges",
            direct_wrong.get("ok") is False
            and hermes_wrong.get("ok") is False
            and direct_wrong["errors"][0]["code"] == hermes_wrong["errors"][0]["code"] == "INPUT_TYPE_INVALID",
        )
        cli_wrong = run_cli(["runtime", "status", "--pid", "not-an-int"], adapter_config)
        check(
            "m2f_08_cli_wrong_type_transport_rejection",
            cli_wrong.returncode == ec.EXIT_USAGE,
            f"rc={cli_wrong.returncode}",
        )
        cli_files = scan_python_files([CLI_ROOT])
        isinstance_lines = []
        # Operator trusted configuration parsing (cli/config.py) and envelope
        # shape reading (cli/exit_codes.py) are adapter transport mechanics,
        # not semantic operation input validation.
        semantic_cli_files = [p for p in cli_files if p.name not in ("config.py", "exit_codes.py")]
        for path in semantic_cli_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                    isinstance_lines.append(f"{path}:{node.lineno}")
        check("m2f_08_cli_no_semantic_type_validation", not isinstance_lines, "; ".join(isinstance_lines[:3]))

        # --- 9. semantic error code converges ---------------------------------------
        missing = {"workspace_id": "fixture-ws", "project_id": "fixture-missing"}
        direct_missing = execute("project.resolve", {**missing, **registry_param})
        hermes_missing = hermes_adapter.execute_request(
            hermes_adapter.build_request("project.resolve", missing, trusted={"registry_path": str(canonical_registry)})
        )
        cli_missing_proc = run_cli(
            ["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-missing", "--json"],
            adapter_config,
        )
        cli_missing = cli_payload(cli_missing_proc)
        check(
            "m2f_09_semantic_error_code_converges",
            direct_missing["errors"][0]["code"]
            == hermes_missing["errors"][0]["code"]
            == cli_missing.get("errors", [{}])[0].get("code")
            == "PROJECT_NOT_FOUND",
            f"cli_rc={cli_missing_proc.returncode}",
        )
        check("m2f_09_error_payload_converges", direct_missing["error"] == hermes_missing["error"] == cli_missing.get("error"))

        # --- 10. PROJECT_AMBIGUOUS / semantic-choice exit projection = 3 -------------
        dup_dir = workspace / "fixture-alpha-duplicate"
        (dup_dir / ".aota").mkdir(parents=True)
        (dup_dir / ".aota" / "project.yaml").write_text(
            (workspace / "fixture-alpha" / ".aota" / "project.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        cli_amb_proc = run_cli(
            ["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"],
            adapter_config,
        )
        cli_amb = cli_payload(cli_amb_proc)
        direct_amb = execute("project.resolve", {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", **registry_param})
        hermes_amb = hermes_adapter.execute_request(
            hermes_adapter.build_request(
                "project.resolve",
                {"workspace_id": "fixture-ws", "project_id": "fixture-alpha"},
                trusted={"registry_path": str(canonical_registry)},
            )
        )
        check(
            "m2f_10_ambiguity_converges",
            cli_amb.get("errors", [{}])[0].get("code")
            == direct_amb["errors"][0]["code"]
            == hermes_amb["errors"][0]["code"]
            == "PROJECT_AMBIGUOUS",
        )
        check("m2f_10_cli_ambiguous_exit_3", cli_amb_proc.returncode == ec.EXIT_NEEDS_SEMANTIC_CHOICE == 3, f"rc={cli_amb_proc.returncode}")
        check("m2f_10_classify_ambiguous_is_3", ec.classify(cli_amb) == 3)
        check(
            "m2f_10_classify_needs_choice_is_3",
            ec.classify({"ok": False, "error": {"code": "NEEDS_SEMANTIC_CHOICE", "message": "m", "retryable": False}}) == 3,
        )
        shutil.rmtree(dup_dir, ignore_errors=True)

        # --- 11. ordinary error exit = 1 --------------------------------------------
        check("m2f_11_ordinary_error_exit_1", cli_missing_proc.returncode == ec.EXIT_ERROR == 1, f"rc={cli_missing_proc.returncode}")
        check(
            "m2f_11_classify_ordinary_is_1",
            ec.classify({"ok": False, "error": {"code": "PROJECT_NOT_FOUND", "message": "m", "retryable": False}}) == 1,
        )

        # --- 12. usage error exit = 2 ------------------------------------------------
        usage_cases = [
            (["bogus"], "unknown command"),
            (["project"], "missing subcommand"),
            (["runtime", "status"], "missing required arg"),
            (["project", "resolve", "--workspace-id", "x"], "missing required arg"),
        ]
        usage_ok = True
        for argv, label in usage_cases:
            proc = run_cli(argv, adapter_config)
            if proc.returncode != ec.EXIT_USAGE:
                usage_ok = False
                check(f"m2f_12_usage_exit_2_{label}", False, f"rc={proc.returncode}")
        check("m2f_12_usage_exit_2", usage_ok)

        # --- 13. blocked projection = 4 ----------------------------------------------
        drift_hash = hermes_adapter.execute_request(
            hermes_adapter.build_request("operations.list", {}, expected_contract_hash="0" * 64)
        )
        drift_proto = hermes_adapter.execute_request(
            hermes_adapter.build_request("operations.list", {}, expected_protocol_version="9.9")
        )
        check(
            "m2f_13_drift_detected_bounded",
            drift_hash.get("ok") is False
            and drift_hash["errors"][0]["code"] == "CONTRACT_VERSION_MISMATCH"
            and drift_proto.get("ok") is False
            and drift_proto["errors"][0]["code"] == "CONTRACT_VERSION_MISMATCH",
        )
        check("m2f_13_drift_classify_blocked_4", ec.classify(drift_hash) == ec.EXIT_BLOCKED == 4)
        check("m2f_13_drift_not_silently_executed", drift_hash.get("data") == {} and drift_hash.get("audit") is None)
        cli_noconfig_proc = run_cli(
            ["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"],
            None,
        )
        cli_noconfig = cli_payload(cli_noconfig_proc)
        check(
            "m2f_13_cli_unconfigured_blocked_4",
            cli_noconfig_proc.returncode == 4
            and cli_noconfig.get("errors", [{}])[0].get("code") == "HOST_RESOURCE_DENIED",
            f"rc={cli_noconfig_proc.returncode}",
        )
        cli_unsafe_proc = run_cli(
            ["project", "resolve", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha",
             "--registry-id", "../../etc/passwd", "--json"],
            adapter_config,
        )
        cli_unsafe = cli_payload(cli_unsafe_proc)
        check(
            "m2f_13_unsafe_resource_id_blocked_4",
            cli_unsafe_proc.returncode == 4
            and cli_unsafe.get("errors", [{}])[0].get("code") == "HOST_RESOURCE_DENIED",
            f"rc={cli_unsafe_proc.returncode}",
        )
        direct_unsupported = execute("no.such.operation", {})
        check(
            "m2f_13_unsupported_operation_classify_4",
            direct_unsupported.get("ok") is False
            and direct_unsupported["errors"][0]["code"] == "UNSUPPORTED_OPERATION"
            and ec.classify(direct_unsupported) == 4,
        )

        # --- 14. success exit = 0 ---------------------------------------------------
        check("m2f_14_success_exit_0", cli_ops_proc.returncode == ec.EXIT_SUCCESS == 0, f"rc={cli_ops_proc.returncode}")
        cli_runtime_ok = run_cli(["runtime", "status", "--pid", str(os.getpid()), "--json"], adapter_config)
        cli_runtime_payload = cli_payload(cli_runtime_ok)
        check(
            "m2f_14_runtime_success_exit_0",
            cli_runtime_ok.returncode == 0 and cli_runtime_payload.get("data", {}).get("running") is True,
        )
        cli_host_ok = run_cli(["host", "status", "--pidfile-id", "alive", "--json"], adapter_config)
        cli_host_payload = cli_payload(cli_host_ok)
        check(
            "m2f_14_host_success_exit_0",
            cli_host_ok.returncode == 0
            and cli_host_payload.get("data", {}).get("process", {}).get("running") is True,
        )
        check("m2f_14_classify_ok_is_0", ec.classify(cli_ops) == 0)

        # --- 15. F1 legacy canonical default absent/contained ------------------------
        legacy_tokens = ("aota-hermes-tools", "canonical-workspace", "/home/latios/workspace/.aota", "CANONICAL_REGISTRY_CANDIDATES", "default_registry_path")
        legacy_hits = []
        for path in cli_files:
            text = path.read_text(encoding="utf-8")
            for token in legacy_tokens:
                if token in text:
                    legacy_hits.append(f"{path}: {token}")
        check("m2f_15_legacy_registry_default_absent", not legacy_hits, "; ".join(legacy_hits[:3]))

        # --- 16. arbitrary model filesystem path not reintroduced --------------------
        path_flag_hits = []
        for path in cli_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    flag = node.args[0].value
                    if flag in ("--registry", "--pidfile", "--receipt", "--path"):
                        path_flag_hits.append(f"{path}:{node.lineno}: {flag}")
                    for kw in node.keywords:
                        if kw.arg == "type" and isinstance(kw.value, ast.Name) and kw.value.id == "Path":
                            path_flag_hits.append(f"{path}:{node.lineno}: type=Path for {flag}")
        check("m2f_16_no_arbitrary_path_flags", not path_flag_hits, "; ".join(path_flag_hits[:3]))
        allowed_cli_flags = {"--workspace-id", "--project-id", "--pid", "--registry-id", "--pidfile-id", "--receipt-id", "--json"}
        for path in cli_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.startswith("--")
                    and node.args[0].value not in allowed_cli_flags
                ):
                    path_flag_hits.append(f"{path}:{node.lineno}: {node.args[0].value}")
        check("m2f_16_cli_flags_bounded", not path_flag_hits, "; ".join(path_flag_hits[:3]))
        schema_has_path_surface = any(
            "path" in arg["name"]
            for op in operations
            for arg in [
                a
                for a in hermes_adapter.operation_request_schema(DEFAULT_REGISTRY.get(op))["arguments"]
                if a["name"] not in TRUSTED_ADAPTER_KEYS
            ]
        )
        check(
            "m2f_16_hermes_semantic_surface_no_path_arguments",
            not schema_has_path_surface,
        )

        # --- 17. no model-facing mega-tool -------------------------------------------
        hermes_files = scan_python_files([AOTA_FORGE_ROOT / "adapters" / "hermes"])
        allowed_hermes_functions = {
            "operation_request_schema",
            "build_request",
            "execute_request",
            "detect_contract_drift",
            "error_code",  # not present; guard against accidental generic surfaces below
        }
        forbidden_surface_names = {
            "control", "run_any", "invoke_any", "dispatch", "execute_any", "passthrough", "call_operation",
        }
        mega_hits = []
        for path in hermes_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    if node.name in forbidden_surface_names:
                        mega_hits.append(f"{path}:{node.lineno}: {node.name}")
        check("m2f_17_no_generic_control_surface_hermes", not mega_hits, "; ".join(mega_hits[:3]))
        help_proc = run_cli(["--help"], adapter_config)
        check(
            "m2f_17_cli_fixed_subcommands_only",
            "--operation" not in help_proc.stdout and "control" not in help_proc.stdout.casefold(),
        )

        # --- 18. Hermes adapter has no legacy control-plane imports ------------------
        legacy_imports = import_violations(
            hermes_files,
            ("plugin", "hermes", "docker", "requests", "webui", "outbox"),
            {},
        )
        check("m2f_18_no_legacy_control_plane_imports", not legacy_imports, "; ".join(legacy_imports[:3]))

        # --- 19. no duplicated adapter authority logic -------------------------------
        adapter_files = cli_files + hermes_files
        authority_imports = import_violations(
            adapter_files,
            (
                "aota_forge.core.project",
                "aota_forge.core.git",
                "aota_forge.core.runtime",
                "aota_forge.core.plan",
                "aota_forge.core.handlers",
                "aota_forge.adapters.host",
            ),
            {
                "aota_forge.core.contracts.validation": ("validate_inputs",),
                "aota_forge.core.contracts.operations": ("register", "register_operations"),
            },
        )
        check("m2f_19_no_duplicated_authority_imports", not authority_imports, "; ".join(authority_imports[:3]))
        authority_tokens = ("DEFAULT_REGISTRY.bind", "resolve_project", "inspect_git(", "process_status(", "register_operations(")
        token_hits = []
        for path in adapter_files:
            text = path.read_text(encoding="utf-8")
            for token in authority_tokens:
                if token in text:
                    token_hits.append(f"{path}: {token}")
        check("m2f_19_no_duplicated_authority_tokens", not token_hits, "; ".join(token_hits[:3]))

        # --- 20. no live Hermes dependency -------------------------------------------
        purged = [name for name in list(sys.modules) if "hermes" in name.casefold()]
        for name in purged:
            del sys.modules[name]
        for name in [n for n in list(sys.modules) if n.startswith("aota_forge.adapters")]:
            del sys.modules[name]
        fresh_hermes = importlib.import_module("aota_forge.adapters.hermes")
        check(
            "m2f_20_imports_without_live_hermes",
            fresh_hermes.HERMES_ADAPTER_LIVE_RUNTIME_REQUIRED == "no",
        )
        check(
            "m2f_20_marker_no_legacy_control_plane",
            fresh_hermes.HERMES_ADAPTER_LEGACY_CONTROL_PLANE_DEPENDENCY == "no",
        )
        fresh_exec = fresh_hermes.execute_request(fresh_hermes.build_request("operations.list", {}))
        check("m2f_20_executes_source_only", fresh_exec.get("ok") is True)

        # --- success-path convergence (§22) ------------------------------------------
        git_direct = execute("git.inspect", {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", **registry_param})
        git_hermes = hermes_adapter.execute_request(
            hermes_adapter.build_request(
                "git.inspect",
                {"workspace_id": "fixture-ws", "project_id": "fixture-alpha"},
                trusted={"registry_path": str(canonical_registry)},
            )
        )
        git_cli_proc = run_cli(
            ["git", "inspect", "--workspace-id", "fixture-ws", "--project-id", "fixture-alpha", "--json"],
            adapter_config,
        )
        git_cli = cli_payload(git_cli_proc)
        git_ok = all(
            p.get("ok") is True
            and p.get("data", {}).get("available") is True
            and len(p.get("data", {}).get("head_sha", "")) == 40
            for p in (git_direct, git_hermes, git_cli)
        )
        check("m2f_22_git_inspect_success_converges", git_ok and git_cli_proc.returncode == 0)
        git_shas = {p["data"]["head_sha"] for p in (git_direct, git_hermes, git_cli)}
        check("m2f_22_git_same_head_sha", len(git_shas) == 1)
        check(
            "m2f_22_git_contract_hash_converges",
            len({p.get("audit", {}).get("contract_hash") for p in (git_direct, git_hermes, git_cli)}) == 1,
        )
        runtime_direct = execute("runtime.status", {"pid": os.getpid()})
        runtime_hermes = hermes_adapter.execute_request(
            hermes_adapter.build_request("runtime.status", {"pid": os.getpid()})
        )
        runtime_cli = cli_payload(run_cli(["runtime", "status", "--pid", str(os.getpid()), "--json"], adapter_config))
        check(
            "m2f_22_runtime_success_converges",
            all(p.get("ok") is True and p.get("data", {}).get("running") is True for p in (runtime_direct, runtime_hermes, runtime_cli)),
        )
        host_direct = execute("host.status", {"pidfile": str(pidfile)})
        host_hermes = hermes_adapter.execute_request(
            hermes_adapter.build_request("host.status", {}, trusted={"pidfile": str(pidfile)})
        )
        host_cli = cli_payload(run_cli(["host", "status", "--pidfile-id", "alive", "--json"], adapter_config))
        check(
            "m2f_22_host_success_converges",
            all(p.get("ok") is True and p.get("data", {}).get("process", {}).get("running") is True for p in (host_direct, host_hermes, host_cli)),
        )

        # --- error-path convergence (§23) --------------------------------------------
        git_missing = {"workspace_id": "fixture-ws", "project_id": "fixture-beta"}
        git_notfound_direct = execute("git.inspect", {**git_missing, **registry_param})
        git_notfound_hermes = hermes_adapter.execute_request(
            hermes_adapter.build_request("git.inspect", git_missing, trusted={"registry_path": str(canonical_registry)})
        )
        git_notfound_cli_proc = run_cli(
            ["git", "inspect", "--workspace-id", "fixture-ws", "--project-id", "fixture-beta", "--json"],
            adapter_config,
        )
        git_notfound_cli = cli_payload(git_notfound_cli_proc)
        check(
            "m2f_23_git_notfound_converges",
            git_notfound_direct["errors"][0]["code"]
            == git_notfound_hermes["errors"][0]["code"]
            == git_notfound_cli.get("errors", [{}])[0].get("code")
            == "GIT_NOT_FOUND",
        )
        check("m2f_23_git_notfound_exit_1", git_notfound_cli_proc.returncode == 1)
        missing_pid = str(trusted / "missing.pid")
        host_fail_direct = execute("host.status", {"pidfile": missing_pid})
        host_fail_hermes = hermes_adapter.execute_request(
            hermes_adapter.build_request("host.status", {}, trusted={"pidfile": missing_pid})
        )
        host_fail_cli_proc = run_cli(["host", "status", "--pidfile-id", "missing", "--json"], adapter_config)
        host_fail_cli = cli_payload(host_fail_cli_proc)
        fail_states = [
            (
                p.get("ok") is True
                and p.get("warnings")
                and p.get("data", {}).get("checks", {}).get("process", {}).get("state") == "failed"
                and p.get("data", {}).get("checks", {}).get("process", {}).get("error_code") == "RECEIPT_INVALID"
            )
            for p in (host_fail_direct, host_fail_hermes, host_fail_cli)
        ]
        check("m2f_23_host_resource_failure_converges", all(fail_states), str(fail_states))

        # --- frozen exit constants ---------------------------------------------------
        check(
            "m2f_exit_constants_frozen",
            (ec.EXIT_SUCCESS, ec.EXIT_ERROR, ec.EXIT_USAGE, ec.EXIT_NEEDS_SEMANTIC_CHOICE, ec.EXIT_BLOCKED) == (0, 1, 2, 3, 4),
        )
        check(
            "m2f_exit_projection_markers",
            ec.CORE_ERROR_SEMANTICS_ARE_AUTHORITY == "yes" and ec.CLI_EXIT_CODE_IS_ADAPTER_PROJECTION == "yes",
        )

        passed = all(item["pass"] for item in results)
        print(f"\nM2-F ADAPTER PROJECTION & CONVERGENCE: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
        return 0 if passed else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
