"""Canonical CLI adapter for AOTA Forge.

CLI is an interface only: it is NOT Forge Core, NOT Plan authority, and NOT
an exclusive entrypoint.  All commands route through the same internal Core
ingress used by direct library invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aota_forge.core import execute as forge_execute
from aota_forge.core.contracts.operations import available_operations

CANONICAL_REGISTRY_CANDIDATES = (
    Path("/home/latios/workspace/.aota/workspaces.json"),
    Path("/home/latios/workspace/aota-hermes-tools/deploy/canonical-workspace/workspaces.json"),
)


def default_registry_path() -> Path | None:
    for candidate in CANONICAL_REGISTRY_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aota",
        description="AOTA Forge canonical CLI machine adapter (read-only in M1).",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--registry", type=Path, default=None, help="workspaces.json path")
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="project/workspace resolution")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    resolve = project_sub.add_parser("resolve", help="resolve one project deterministically")
    resolve.add_argument("--workspace", required=True)
    resolve.add_argument("--project", required=True)
    resolve.add_argument("--json", action="store_true", help="machine-readable output")
    resolve.add_argument("--registry", type=Path, default=None, help="workspaces.json path")

    host = sub.add_parser("host", help="host mechanical read-only inspection")
    host_sub = host.add_subparsers(dest="host_command", required=True)
    status = host_sub.add_parser("status", help="host status (no Plan/SPEC/Profile Task required)")
    status.add_argument("--pidfile", type=Path, default=None, help="managed pidfile for process observation")
    status.add_argument("--receipt", type=Path, default=None, help="managed deployment receipt for evidence")
    status.add_argument("--json", action="store_true", help="machine-readable output")

    git = sub.add_parser("git", help="git read-only inspection")
    git_sub = git.add_subparsers(dest="git_command", required=True)
    inspect = git_sub.add_parser("inspect", help="inspect git state within project boundary")
    inspect.add_argument("--workspace", required=True)
    inspect.add_argument("--project", required=True)
    inspect.add_argument("--json", action="store_true", help="machine-readable output")
    inspect.add_argument("--registry", type=Path, default=None, help="workspaces.json path")

    runtime = sub.add_parser("runtime", help="runtime read-only inspection")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    status_r = runtime_sub.add_parser("status", help="runtime status")
    status_r.add_argument("--pid", type=int, required=True)
    status_r.add_argument("--json", action="store_true", help="machine-readable output")

    ops = sub.add_parser("operations", help="list canonical operations")
    ops.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _resolve_registry(args: argparse.Namespace) -> Path | None:
    if getattr(args, "registry", None) is not None:
        return args.registry
    return default_registry_path()


def _is_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    registry = _resolve_registry(args)

    if args.command == "operations":
        payload = forge_execute("operations.list", {}, principal="cli")
        _emit(args, payload)
        return 0 if payload.get("ok") else 1

    if args.command == "project" and args.project_command == "resolve":
        if registry is None:
            _emit(args, _registry_missing())
            return 1
        from aota_forge.cli.commands.project import resolve as project_resolve

        payload = project_resolve(args.workspace, args.project, registry)
        _emit(args, payload)
        return 0 if payload.get("ok") else 1

    if args.command == "git" and args.git_command == "inspect":
        if registry is None:
            _emit(args, _registry_missing())
            return 1
        payload = forge_execute(
            "git.inspect",
            {"workspace_id": args.workspace, "project_id": args.project, "registry_path": str(registry)},
            principal="cli",
        )
        _emit(args, payload)
        return 0 if payload.get("ok") else 1

    if args.command == "runtime" and args.runtime_command == "status":
        payload = forge_execute("runtime.status", {"pid": args.pid}, principal="cli")
        _emit(args, payload)
        return 0 if payload.get("ok") else 1

    if args.command == "host" and args.host_command == "status":
        from aota_forge.cli.commands.host import status as host_status

        payload = host_status(args.pidfile, args.receipt)
        _emit(args, payload)
        return 0 if payload.get("ok") else 1

    parser.print_help()
    return 2


def _registry_missing() -> dict[str, Any]:
    return {
        "ok": False,
        "operation": "registry",
        "error": {"code": "PROJECT_REGISTRY_INVALID", "message": "workspaces.json registry not found", "retryable": False},
        "data": {},
        "evidence": {},
        "warnings": [],
        "status": "error",
        "result": "error",
        "errors": [],
        "blockers": [],
        "semantic_choices": [],
        "next_action": "provide --registry or place workspaces.json at a canonical path",
        "correlation_id": "",
    }


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if _is_json(args):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return
    from aota_forge.cli.render import render_human

    print(render_human(payload))


def run() -> None:
    sys.exit(_main())


if __name__ == "__main__":
    run()
