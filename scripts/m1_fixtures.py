#!/usr/bin/env python3
"""M1 focused deterministic validation fixtures.

Proves:

1. CLI calls the same Core ingress used by direct library invocation.
2. project ambiguity fails closed (0/1/many).
3. Git inspection cannot walk above the resolved project root.
4. machine JSON returns full HEAD SHA.
5. Host inspection requires no Plan/SPEC/Profile Task.
6. legacy current_* pointers are not Core authority.
7. canonical success/error envelope shapes.

Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def make_project(workspace: Path, project_id: str, status: str = "active") -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": status},
        "summary": "M1 fixture project",
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


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="m1-fixtures-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        p1 = make_project(workspace, "fixture-alpha")
        make_project(workspace, "fixture-beta")
        make_project(workspace, "aota_forge")
        registry = workdir / "workspaces.json"
        registry.write_text(json.dumps({"fixture-ws": {"candidates": [str(workspace)]}}), encoding="utf-8")

        # 1. Direct library invocation through the same ingress.
        sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))
        from aota_forge.core import execute as library_execute

        lib_result = library_execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(registry)},
            principal="library",
        )
        check("library_invocation_ok", lib_result.get("ok") is True and lib_result["data"]["project_id"] == "fixture-alpha")

        # 1a. I9-B003: underscore project id accepted through real manifest
        # loading/validation path (regression: kebab-only PROJECT_ID_RE).
        # 1b. I9-B001: resolved root is the canonical project root, never the
        # .aota metadata directory (regression: root derivation).
        underscore_result = library_execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "aota_forge", "registry_path": str(registry)},
            principal="library",
        )
        canonical_root = str((workspace / "aota_forge").resolve())
        aota_metadata_root = str((workspace / "aota_forge" / ".aota").resolve())
        check("underscore_project_id_accepted", underscore_result.get("ok") is True)
        check("underscore_project_id_identity", underscore_result.get("data", {}).get("project_id") == "aota_forge")
        check("project_root_is_canonical_project_root", underscore_result.get("data", {}).get("project_root") == canonical_root)
        check("project_root_is_not_aota_metadata", underscore_result.get("data", {}).get("project_root") != aota_metadata_root)

        # 2. CLI calls the same ingress (invoke CLI subprocess, compare envelope).
        cli_out = subprocess.run(
            [sys.executable, "-m", "aota_forge.cli", "project", "resolve",
             "--workspace", "fixture-ws", "--project", "fixture-alpha",
             "--registry", str(registry), "--json"],
            capture_output=True, text=True, cwd=str(AOTA_FORGE_ROOT.parent), timeout=60,
        )
        cli_ok = cli_out.returncode == 0
        try:
            cli_payload = json.loads(cli_out.stdout)
        except json.JSONDecodeError:
            cli_payload = {}
        check("cli_same_ingress", cli_ok and cli_payload.get("ok") is True and cli_payload["data"]["project_id"] == "fixture-alpha")
        check(
            "cli_and_library_same_ingress_contract",
            lib_result.get("ok") == cli_payload.get("ok") and lib_result.get("data", {}).get("project_id") == cli_payload.get("data", {}).get("project_id"),
        )

        # 3. Project ambiguity fails closed (duplicate manifests at different paths).
        dup_dir = workspace / "fixture-alpha-duplicate"
        (dup_dir / ".aota").mkdir(parents=True)
        (dup_dir / ".aota" / "project.yaml").write_text(
            (workspace / "fixture-alpha" / ".aota" / "project.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        dup_result = library_execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(registry)},
            principal="library",
        )
        check("project_ambiguity_fails_closed", dup_result.get("ok") is False and dup_result["errors"][0]["code"] == "PROJECT_AMBIGUOUS", dup_result["errors"][0]["code"])

        # Remove the duplicate so later checks resolve uniquely.
        shutil.rmtree(dup_dir, ignore_errors=True)

        # 4. Project not found.
        nf_result = library_execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "fixture-missing", "registry_path": str(registry)},
            principal="library",
        )
        check("project_not_found", nf_result.get("ok") is False and nf_result["errors"][0]["code"] == "PROJECT_NOT_FOUND")

        # 5. Git inspection: repo present with full SHA; nested path; absent; boundary escape.
        from aota_forge.core.git.inspect import find_git_root, inspect_git
        from aota_forge.core.contracts.errors import GitBoundaryViolationError, GitNotFoundError

        subprocess.run(["git", "init", "-q", str(p1)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(p1), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(p1), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True, capture_output=True)
        full_sha = subprocess.run(["git", "-C", str(p1), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        git_result = inspect_git(p1, boundary=p1)
        check("git_repo_present", git_result["available"] is True)
        check("git_full_sha_machine", len(git_result["head_sha"]) == 40 and git_result["head_sha"] == full_sha)
        check("git_short_sha_display_only", git_result["head_short"] == full_sha[:12])

        # dirty status
        (p1 / "dirty-file.txt").write_text("dirty", encoding="utf-8")
        dirty_result = inspect_git(p1, boundary=p1)
        check("git_dirty_status", dirty_result["is_dirty"] is True and dirty_result["untracked"]["count"] >= 1)

        # nested path inside repo
        nested = p1 / "nested" / "deep"
        nested.mkdir(parents=True)
        nested_result = inspect_git(nested, boundary=p1)
        check("git_nested_path", nested_result["repo_root"] == ".")

        # boundary escape attempt: start outside boundary
        try:
            inspect_git(workdir / "outside", boundary=p1)
            check("git_boundary_escape_fails_closed", False, "no exception raised")
        except GitBoundaryViolationError:
            check("git_boundary_escape_fails_closed", True)

        # repo absent
        bare = workdir / "norepo"
        bare.mkdir()
        try:
            find_git_root(bare, boundary=bare)
            check("git_repo_absent", False, "no exception raised")
        except GitNotFoundError:
            check("git_repo_absent", True)

        # 6. Host inspection requires no Plan/SPEC/Profile Task.
        host_result = library_execute("host.status", {}, principal="library")
        check("host_inspection_no_plan_required", host_result.get("ok") is True)

        # 7. legacy current_* pointers are not Core authority.
        from aota_forge.core.plan.read_model import LegacyPlanStateReader, snapshot_sha256

        reader = LegacyPlanStateReader()
        plans_dir = p1 / ".aota" / "forge" / "plans" / "plan_20260729T075202_ff38a6a0"
        plans_dir.mkdir(parents=True)
        (plans_dir / "plan.json").write_text(json.dumps({
            "plan_id": "plan_20260729T075202_ff38a6a0", "revision": 3, "status": "planned",
            "current_milestone": "M2", "current_work_item": "legacy-pointer-not-authority",
            "milestones": {"M1": {"status": "completed", "work_items": [{"work_item_id": "M1-A", "status": "completed"}]}},
        }), encoding="utf-8")
        snapshot = reader.load(p1, "plan_20260729T075202_ff38a6a0")
        check("legacy_plan_read_model_shadow", snapshot is not None and snapshot.source == "legacy_shadow")
        check("legacy_current_pointer_not_authority", snapshot is not None and snapshot.current_milestone == "M2" and snapshot.to_dict().get("current_work_item") is None)

        # 8. Core importable without Hermes.
        env = dict(os.environ)
        env.pop("HERMES_HOME", None)
        import_proc = subprocess.run(
            [sys.executable, "-c", "import aota_forge.core; print('import-ok')"],
            capture_output=True, text=True, cwd=str(AOTA_FORGE_ROOT.parent), env=env, timeout=60,
        )
        check("core_import_without_hermes", import_proc.returncode == 0 and "import-ok" in import_proc.stdout)

        # 9. Canonical envelope shapes.
        from aota_forge.core.contracts import results as contract_results

        success_env = contract_results.success("test.op", data={"a": 1})
        failure_env = contract_results.failure("test.op", "RUNTIME_NOT_RUNNING", "msg", retryable=False)
        check("canonical_success_shape", set(success_env) >= {"ok", "operation", "data", "evidence", "warnings"} and success_env["ok"] is True)
        check("canonical_error_shape", failure_env["ok"] is False and failure_env["error"]["code"] == "RUNTIME_NOT_RUNNING" and failure_env["error"]["retryable"] is False)
        check("machine_fields_present", {"status", "result", "errors", "blockers", "semantic_choices", "next_action", "correlation_id"} <= set(success_env) and {"status", "result", "errors", "blockers", "semantic_choices", "next_action", "correlation_id"} <= set(failure_env))

        # 10. UNSUPPORTED_OPERATION.
        unsupported = library_execute("control.mega", {"payload": "x"})
        check("unsupported_operation", unsupported.get("ok") is False and unsupported["errors"][0]["code"] == "UNSUPPORTED_OPERATION")

        passed = all(item["pass"] for item in results)
        print(f"\nM1 FIXTURES: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
        return 0 if passed else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
