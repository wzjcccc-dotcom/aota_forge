"""Canonical CLI adapter for AOTA Forge (M2-F).

The CLI is a transport adapter only.  Every canonical CLI operation:

    parse transport arguments
    -> construct semantic input
    -> resolve explicitly allowed trusted adapter resources (M2-E)
    -> call the same canonical Unified Ingress
    -> project the canonical result to CLI (--json envelope / human)
    -> map the canonical envelope onto the frozen exit code projection

The CLI never implements project resolution, error interpretation,
contract validation, authority logic, Host path resolution or Plan
normalization.  Model-facing filesystem paths are denied: registry /
pidfile / receipt are logical resource ids resolved through the operator
trusted configuration channel (``AOTA_FORGE_ADAPTER_CONFIG``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

from aota_forge.cli.commands.host import status_params as host_status_params
from aota_forge.cli.commands.plan import (
    plan_init_params,
    plan_retire_params,
)
from aota_forge.cli.commands.project import inspect_params, resolve_params
from aota_forge.cli.commands.runtime import status_params as runtime_status_params
from aota_forge.cli.config import (
    ADAPTER_CONFIG_ENV,
    AdapterTrustedConfig,
    load_adapter_trusted_config,
)
from aota_forge.cli.exit_codes import EXIT_USAGE, classify
from aota_forge.cli.projection import attach_semantic_arguments
from aota_forge.core import execute as forge_execute
from aota_forge.core.contracts.descriptor import READ_ONLY
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import failure_from_error
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.contracts.validation import TRUSTED_ADAPTER_KEYS
from aota_forge.core.ingress import MutationIngressRequest, execute_mutation

ParamBuilder = Callable[[argparse.Namespace, AdapterTrustedConfig], dict[str, Any]]


def _transport_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aota",
        description="AOTA Forge canonical CLI machine adapter.",
    )
    _transport_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="project/workspace resolution")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    resolve = project_sub.add_parser("resolve", help="resolve one project deterministically")
    attach_semantic_arguments(resolve, "project.resolve")
    resolve.add_argument("--registry-id", default=None, help="logical project registry resource id (trusted channel)")
    _transport_flags(resolve)

    git = sub.add_parser("git", help="git read-only inspection")
    git_sub = git.add_subparsers(dest="git_command", required=True)
    inspect = git_sub.add_parser("inspect", help="inspect git state within project boundary")
    attach_semantic_arguments(inspect, "git.inspect")
    inspect.add_argument("--registry-id", default=None, help="logical project registry resource id (trusted channel)")
    _transport_flags(inspect)

    runtime = sub.add_parser("runtime", help="runtime read-only inspection")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_status = runtime_sub.add_parser("status", help="runtime status")
    attach_semantic_arguments(runtime_status, "runtime.status")
    _transport_flags(runtime_status)

    host = sub.add_parser("host", help="host mechanical read-only inspection")
    host_sub = host.add_subparsers(dest="host_command", required=True)
    host_status = host_sub.add_parser("status", help="host status (no Plan/SPEC/Profile Task required)")
    attach_semantic_arguments(host_status, "host.status")
    host_status.add_argument("--pidfile-id", default=None, help="logical runtime pidfile resource id (trusted channel)")
    host_status.add_argument("--receipt-id", default=None, help="logical deployment receipt resource id (trusted channel)")
    _transport_flags(host_status)

    plan = sub.add_parser("plan", help="plan lifecycle mechanical mutation commands")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_init = plan_sub.add_parser("init", help="mechanically initialize one exact Plan Subject")
    attach_semantic_arguments(plan_init, "plan_init")
    _transport_flags(plan_init)

    plan_retire = plan_sub.add_parser("retire", help="mechanically retire one exact Plan Subject")
    attach_semantic_arguments(plan_retire, "plan_retirement")
    _transport_flags(plan_retire)

    operations = sub.add_parser("operations", help="list canonical operations")
    _transport_flags(operations)
    return parser


ROUTES: dict[tuple[str, str | None], tuple[str, ParamBuilder]] = {
    ("operations", None): ("operations.list", lambda args, config: {}),
    ("project", "resolve"): ("project.resolve", resolve_params),
    ("git", "inspect"): ("git.inspect", inspect_params),
    ("runtime", "status"): ("runtime.status", runtime_status_params),
    ("host", "status"): ("host.status", host_status_params),
    ("plan", "init"): ("plan_init", plan_init_params),
    ("plan", "retire"): ("plan_retirement", plan_retire_params),
}


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return
    from aota_forge.cli.render import render_human

    print(render_human(payload))


def _main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    route = ROUTES.get((args.command, getattr(args, f"{args.command}_command", None)))
    if route is None:
        parser.print_help()
        return EXIT_USAGE
    operation, builder = route
    try:
        config = load_adapter_trusted_config(os.environ)
        params = builder(args, config)
    except ForgeError as exc:
        payload = failure_from_error(
            operation,
            exc,
            next_action=f"provide a valid operator adapter configuration via {ADAPTER_CONFIG_ENV}",
        )
        _emit(args, payload)
        return classify(payload)
    # The CLI transport does not get to self-assert a principal.  It carries
    # operator-resolved resources through the typed trusted boundary; a
    # runtime may add a bound principal at its integration boundary.
    trusted_metadata = {
        key: value for key, value in params.items() if key in TRUSTED_ADAPTER_KEYS
    }
    trusted_context = (
        bind_trusted_context(
            channel="cli_adapter",
            provenance="operator_config",
            metadata=trusted_metadata,
        )
        if trusted_metadata
        else None
    )
    descriptor = DEFAULT_REGISTRY.get(operation)
    if descriptor and descriptor.read_write != READ_ONLY:
        if isinstance(params, MutationIngressRequest):
            payload = execute_mutation(params)
        elif isinstance(params, dict) and "request" in params and "store" in params:
            payload = execute_mutation(
                MutationIngressRequest(
                    operation=operation,
                    store=params["store"],
                    request=params["request"],
                )
            )
        else:
            store = params.get("store") if isinstance(params, dict) else None
            request = params.get("request") if isinstance(params, dict) else None
            payload = execute_mutation(
                MutationIngressRequest(
                    operation=operation,
                    store=store,
                    request=request,
                )
            )
    else:
        payload = forge_execute(operation, params, trusted_context=trusted_context)
    _emit(args, payload)
    return classify(payload)


def run() -> None:
    sys.exit(_main())


if __name__ == "__main__":
    run()
