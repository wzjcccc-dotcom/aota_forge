#!/usr/bin/env python3
"""M2-E focused deterministic validation fixtures.

Part A — F2 model-facing resource boundary:

    arbitrary absolute model path denied
    path traversal denied
    symlink escape denied
    unknown resource kind denied
    unconfigured trusted root denied
    logical resource resolution (pidfile / receipt / runtime identity /
        project registry / backup readiness)
    file-size bounds preserved
    JSON validation preserved
    trusted roots from operator env allowlist only

Part B — I9-B006 resolver completeness:

    55-project exact resolution (target beyond display limit)
    duplicate target beyond the first-50 listing boundary → ambiguous
    truly missing project → not found after complete scan
    listing remains bounded/truncated
    semantic vs listing fingerprint distinction

Part C — existing safety:

    0/1/many fail-closed preserved
    .aota-worktrees excluded from project scan
    M1 B001/B003 regression fixtures still PASS

All fixtures live under an isolated /tmp directory.  No runtime/live
validation.  Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "aota_forge" / ".."))

from aota_forge.adapters.host import resources as host_resources
from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ReceiptInvalidError,
)
from aota_forge.core.project import discovery
from aota_forge.core.project.resolver import resolve_project_with_fingerprint
from aota_forge.core.resources.resolver import (
    RESOURCE_KINDS,
    HostResourceDeniedError,
    TrustedResourceConfig,
    TrustedResourceResolver,
)

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def expect_denied(name: str, kind: str, resource_id: str, config: TrustedResourceConfig) -> None:
    try:
        TrustedResourceResolver(config).resolve(kind, resource_id)
        check(name, False, f"{kind}/{resource_id} was not denied")
    except HostResourceDeniedError:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"wrong error: {type(exc).__name__}")


def make_project(workspace: Path, project_id: str) -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
        "summary": "M2-E fixture project",
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


def part_a(workdir: Path) -> None:
    roots = workdir / "resource-roots"
    pid_root, receipt_root = roots / "pidfiles", roots / "receipts"
    runtime_root, registry_root, backup_root = roots / "runtimes", roots / "registries", roots / "backups"
    for root in (pid_root, receipt_root, runtime_root, registry_root, backup_root):
        root.mkdir(parents=True, exist_ok=True)
    config = TrustedResourceConfig({
        "runtime_pidfile": pid_root,
        "managed_deployment_receipt": receipt_root,
        "runtime_identity": runtime_root,
        "project_registry": registry_root,
        "backup_receipt": backup_root,
    })

    # Structural: model-facing primitives accept no filesystem path.
    ref_api = {
        "resolve_host_resource", "host_process_status_ref", "read_deployment_receipt_ref",
        "host_runtime_identity_ref", "read_project_registry_ref", "inspect_backup_readiness_ref",
    }
    path_params = [
        name for name in ref_api
        if "path" in inspect.signature(getattr(host_resources, name)).parameters
    ]
    check("model_facing_api_has_no_path_parameter", not path_params, str(path_params))
    check(
        "no_generic_filesystem_reader_created",
        not any(name in ("read_any_file", "filesystem_open") for name in dir(host_resources))
        and set(RESOURCE_KINDS) == {
            "runtime_pidfile", "managed_deployment_receipt", "runtime_identity",
            "project_registry", "backup_receipt",
        },
    )

    # Absolute / traversal / unsafe ids denied.
    expect_denied("absolute_path_rejected", "runtime_pidfile", "/etc/passwd", config)
    expect_denied("absolute_path_rejected_2", "runtime_pidfile", "/proc/self/status", config)
    expect_denied("path_traversal_rejected", "runtime_pidfile", "..", config)
    expect_denied("path_traversal_rejected_2", "runtime_pidfile", "../escape", config)
    expect_denied("path_traversal_rejected_3", "runtime_pidfile", "a/../../x", config)
    expect_denied("path_traversal_rejected_backslash", "managed_deployment_receipt", "a\\..\\x", config)
    expect_denied("unknown_kind_rejected", "read_any_file", "x", config)
    expect_denied("unconfigured_root_rejected", "backup_receipt", "x", TrustedResourceConfig({}))
    expect_denied("empty_id_rejected", "runtime_pidfile", "", config)

    # Symlink escape denied.
    outside = workdir / "outside"
    outside.mkdir()
    (outside / "deployment.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    os.symlink(outside, receipt_root / "escape")
    os.symlink("/etc/passwd", pid_root / "link.pid")
    expect_denied("symlink_escape_rejected", "managed_deployment_receipt", "escape", config)
    expect_denied("symlink_file_rejected", "runtime_pidfile", "link", config)
    pid_root_link = roots / "pidfiles-link"
    os.symlink(outside, pid_root_link)
    expect_denied("symlink_root_rejected", "runtime_pidfile", "agent", TrustedResourceConfig({"runtime_pidfile": pid_root_link}))

    # Logical resolution: pidfile.
    (pid_root / "agent.pid").write_text(str(os.getpid()), encoding="utf-8")
    status = host_resources.host_process_status_ref("agent", config)
    check("pidfile_logical_resolution", status.get("running") is True and status.get("pid") == os.getpid(), str(status))

    # Size bound preserved on pidfile.
    (pid_root / "big.pid").write_text("9" * 5000, encoding="utf-8")
    try:
        host_resources.host_process_status_ref("big", config)
        check("pidfile_size_bound", False)
    except ReceiptInvalidError:
        check("pidfile_size_bound", True)
    except Exception as exc:  # noqa: BLE001
        check("pidfile_size_bound", False, f"wrong error: {type(exc).__name__}")

    # Non-numeric pidfile denied.
    (pid_root / "nan.pid").write_text("not-a-pid", encoding="utf-8")
    try:
        host_resources.host_process_status_ref("nan", config)
        check("pidfile_strict_int", False)
    except ReceiptInvalidError:
        check("pidfile_strict_int", True)

    # Logical resolution: deployment receipt + JSON validation.
    (receipt_root / "dep-1").mkdir(exist_ok=True)
    (receipt_root / "dep-1" / "deployment.json").write_text(
        json.dumps({"schema_version": 1, "deployment": "ok"}), encoding="utf-8"
    )
    receipt = host_resources.read_deployment_receipt_ref("dep-1", config)
    check("receipt_logical_resolution", receipt.get("schema_version") == 1 and receipt.get("deployment") == "ok")

    (receipt_root / "dep-bad").mkdir(exist_ok=True)
    (receipt_root / "dep-bad" / "deployment.json").write_text("{not-json", encoding="utf-8")
    try:
        host_resources.read_deployment_receipt_ref("dep-bad", config)
        check("receipt_json_validation", False)
    except ReceiptInvalidError:
        check("receipt_json_validation", True)

    (receipt_root / "dep-big").mkdir(exist_ok=True)
    (receipt_root / "dep-big" / "deployment.json").write_text("x" * (70 * 1024), encoding="utf-8")
    try:
        host_resources.read_deployment_receipt_ref("dep-big", config)
        check("receipt_size_bound", False)
    except ReceiptInvalidError:
        check("receipt_size_bound", True)

    # Logical resolution: runtime identity.
    (runtime_root / "rt-1").mkdir(exist_ok=True)
    (runtime_root / "rt-1" / "VERSION").write_text("2.0.0", encoding="utf-8")
    identity = host_resources.host_runtime_identity_ref("rt-1", config)
    check("runtime_identity_logical_resolution", identity.get("name") == "aota-forge" and identity.get("version") == "2.0.0", str(identity))

    # Logical resolution: project registry.
    (registry_root / "workspaces.json").write_text(
        json.dumps({"ws": {"candidates": [str(workdir)]}}), encoding="utf-8"
    )
    registry = host_resources.read_project_registry_ref("workspaces", config)
    check("registry_logical_resolution", "ws" in registry and registry["ws"]["candidates"] == [str(workdir)])

    (registry_root / "broken.json").write_text("[]", encoding="utf-8")
    try:
        host_resources.read_project_registry_ref("broken", config)
        check("registry_json_validation", False)
    except Exception as exc:  # noqa: BLE001
        check("registry_json_validation", getattr(exc, "code", None) == "PROJECT_REGISTRY_INVALID")

    # Logical resolution: backup readiness.
    (backup_root / "bk-1").mkdir(exist_ok=True)
    (backup_root / "bk-1" / "backup.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    readiness = host_resources.inspect_backup_readiness_ref("bk-1", ["dep-1"], config)
    check("backup_readiness_ready", readiness.get("ready") is True, str(readiness.get("receipts")))
    readiness_bad = host_resources.inspect_backup_readiness_ref("bk-1", ["dep-1", "missing"], config)
    check("backup_readiness_fail_closed", readiness_bad.get("ready") is False)

    # Env allowlist only.
    saved = {name: os.environ.get(name) for name in ("AOTA_FORGE_HOST_ROOT_PIDFILE", "AOTA_FORGE_HOST_ROOT_RECEIPTS")}
    try:
        os.environ["AOTA_FORGE_HOST_ROOT_PIDFILE"] = str(pid_root)
        os.environ.pop("AOTA_FORGE_HOST_ROOT_RECEIPTS", None)
        env_config = host_resources.host_resource_config_from_env()
        resolved = host_resources.host_process_status_ref("agent", env_config)
        check("env_root_allowlist_pidfile", resolved.get("pid") == os.getpid())
        expect_denied("env_root_allowlist_receipt_unconfigured", "managed_deployment_receipt", "dep-1", env_config)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # Arbitrary env ignored.
    os.environ["AOTA_FORGE_HOST_ROOT_ANYTHING_ELSE"] = "/etc"
    env_config2 = host_resources.host_resource_config_from_env()
    check("env_allowlist_ignores_other_vars", env_config2.root_for("runtime_pidfile") is None)
    os.environ.pop("AOTA_FORGE_HOST_ROOT_ANYTHING_ELSE", None)


def part_b(workdir: Path) -> None:
    # Case 1: 55 valid projects, target sorted after the first 50.
    ws55 = workdir / "ws55"
    ws55.mkdir()
    for i in range(55):
        make_project(ws55, f"proj-{i:03d}")
    registry55 = workdir / "registry-55.json"
    registry55.write_text(json.dumps({"ws55": {"candidates": [str(ws55)]}}), encoding="utf-8")

    resolved = resolve_project_with_fingerprint("ws55", registry55, "proj-054")
    check(
        "project_gt50_exact_resolution",
        resolved["project_id"] == "proj-054" and resolved["project_count"] == 55,
        f"project_count={resolved['project_count']}",
    )
    resolved_first = resolve_project_with_fingerprint("ws55", registry55, "proj-000")
    check("project_within_listing_still_resolves", resolved_first["project_id"] == "proj-000")

    listed = discovery.scan_projects(ws55)
    check(
        "listing_remains_bounded",
        len(listed["projects"]) == 50 and listed["truncated"] is True and listed["project_count"] == 55,
        f"listed={len(listed['projects'])} truncated={listed['truncated']}",
    )
    complete = discovery.scan_projects(ws55, limit=None)
    check("complete_scan_not_truncated", len(complete["projects"]) == 55 and complete["truncated"] is False)

    # Fingerprint distinction: semantic (complete) vs listing (bounded).
    semantic_fp = discovery.fingerprint_registry("ws55", complete["projects"], complete["invalid"])
    listing_fp = discovery.fingerprint_registry("ws55", listed["projects"], listed["invalid"])
    check(
        "fingerprint_completeness_split",
        resolved["registry_fingerprint"] == semantic_fp
        and resolved["listing_fingerprint"] == listing_fp
        and semantic_fp != listing_fp
        and resolved["listing_truncated"] is True
        and resolved["listed_project_count"] == 50,
    )
    check("fingerprint_is_semantically_complete", resolved["registry_fingerprint"] != resolved["listing_fingerprint"])

    # Case 3: truly missing project → PROJECT_NOT_FOUND after complete scan.
    try:
        resolve_project_with_fingerprint("ws55", registry55, "proj-zzz")
        check("project_true_missing", False)
    except ProjectNotFoundError:
        check("project_true_missing", True)

    # Case 2: duplicate beyond the first-50 listing boundary → ambiguous.
    ws51 = workdir / "ws51"
    ws51.mkdir()
    for i in range(49):
        make_project(ws51, f"proj-{i:03d}")
    make_project(ws51, "proj-049")
    dup_dir = ws51 / "proj-049-copy"  # same project_id as proj-049, distinct path
    dup_manifest = ws51 / "proj-049" / ".aota" / "project.yaml"
    (dup_dir / ".aota").mkdir(parents=True)
    (dup_dir / ".aota" / "project.yaml").write_text(
        dup_manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    registry51 = workdir / "registry-51.json"
    registry51.write_text(json.dumps({"ws51": {"candidates": [str(ws51)]}}), encoding="utf-8")
    try:
        resolve_project_with_fingerprint("ws51", registry51, "proj-049")
        check("duplicate_beyond_50_ambiguous", False)
    except ProjectAmbiguousError:
        check("duplicate_beyond_50_ambiguous", True)

    # 0/1/many preserved on a small workspace.
    ws3 = workdir / "ws3"
    ws3.mkdir()
    for pid in ("alpha", "beta", "gamma"):
        make_project(ws3, pid)
    registry3 = workdir / "registry-3.json"
    registry3.write_text(json.dumps({"ws3": {"candidates": [str(ws3)]}}), encoding="utf-8")
    ok3 = resolve_project_with_fingerprint("ws3", registry3, "beta")
    check("single_match_preserved", ok3["project_id"] == "beta" and ok3["listing_truncated"] is False)
    try:
        resolve_project_with_fingerprint("ws3", registry3, "nope")
        check("zero_match_preserved", False)
    except ProjectNotFoundError:
        check("zero_match_preserved", True)

    # .aota-worktrees excluded.
    hidden = ws3 / ".aota-worktrees" / "zz"
    make_project(hidden, "hidden-zz")
    listed3 = discovery.scan_projects(ws3)
    check(
        "aota_worktrees_excluded",
        "hidden-zz" not in {p["project_id"] for p in listed3["projects"]} and listed3["project_count"] == 3,
    )
    try:
        resolve_project_with_fingerprint("ws3", registry3, "hidden-zz")
        check("aota_worktrees_excluded_from_resolution", False)
    except ProjectNotFoundError:
        check("aota_worktrees_excluded_from_resolution", True)


def part_c() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "m1_fixtures.py")],
        capture_output=True, text=True, timeout=300,
    )
    check("m1_fixtures_still_pass", proc.returncode == 0, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:])


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="m2-e-fixtures-"))
    try:
        part_a(workdir)
        part_b(workdir)
        part_c()
        passed = all(item["pass"] for item in results)
        print(f"\nM2-E FIXTURES: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
        return 0 if passed else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
