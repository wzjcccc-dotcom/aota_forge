"""AF #58 M2 — bounded interactive ingress CLI (OpenChamber :3002 seam).

One-shot process entry, no daemon, no independent authority: ``reserve`` and
``bind`` are thin argument adapters over ``aota_forge.interactive_ingress``
composition.  The OpenChamber AF-interactive instance calls this CLI before
and during the first message dispatch; failures are bounded JSON and always
fail closed (no session binding, therefore no model prompt).

Usage:

    python -m aota_forge.interactive_ingress.cli reserve --json
    python -m aota_forge.interactive_ingress.cli bind \\
        --session-id ses_... --profile aota-task-main --message-file - --json
    python -m aota_forge.interactive_ingress.cli sweep --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 3


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_message(message_file: str) -> str:
    if message_file == "-":
        return sys.stdin.read()
    with open(message_file, "rb") as handle:
        return handle.read().decode("utf-8", "strict")


def _cmd_reserve(args: argparse.Namespace) -> int:
    from aota_forge.interactive_ingress.service import reserve_interactive_session

    result = reserve_interactive_session(
        workspace_root=args.workspace_root,
        ttl_seconds=args.ttl_seconds,
        runtime_config_path=args.runtime_config,
        registry_path=args.registry,
    )
    _emit(result)
    return EXIT_OK


def _cmd_bind(args: argparse.Namespace) -> int:
    from aota_forge.interactive_ingress.service import bind_interactive_session

    message = _read_message(args.message_file)
    result = bind_interactive_session(
        session_id=args.session_id,
        message_text=message,
        profile=args.profile,
        workspace_root=args.workspace_root,
        runtime_config_path=args.runtime_config,
        registry_path=args.registry,
    )
    _emit(result)
    return EXIT_OK


def _cmd_sweep(args: argparse.Namespace) -> int:
    from aota_forge.interactive_ingress.service import sweep_interactive_preparations

    result = sweep_interactive_preparations(workspace_root=args.workspace_root)
    _emit(result)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aota_forge.interactive_ingress.cli",
        description="AF #58 M2 trusted interactive ingress (bounded one-shot seam)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reserve = sub.add_parser("reserve", help="mint a mechanical preparation + instance namespace")
    reserve.add_argument("--workspace-root", default=None)
    reserve.add_argument("--runtime-config", default=None)
    reserve.add_argument("--registry", default=None)
    reserve.add_argument("--ttl-seconds", type=float, default=None)
    reserve.add_argument("--json", action="store_true", help="emit machine JSON (always JSON)")
    reserve.set_defaults(handler=_cmd_reserve)

    bind = sub.add_parser("bind", help="verify exact S0 and materialize the trusted binding")
    bind.add_argument("--session-id", required=True)
    bind.add_argument("--profile", required=True)
    bind.add_argument("--message-file", default="-")
    bind.add_argument("--workspace-root", default=None)
    bind.add_argument("--runtime-config", default=None)
    bind.add_argument("--registry", default=None)
    bind.add_argument("--json", action="store_true", help="emit machine JSON (always JSON)")
    bind.set_defaults(handler=_cmd_bind)

    sweep = sub.add_parser("sweep", help="remove expired never-bound preparations")
    sweep.add_argument("--workspace-root", default=None)
    sweep.add_argument("--json", action="store_true", help="emit machine JSON (always JSON)")
    sweep.set_defaults(handler=_cmd_sweep)
    return parser


def main(argv: list[str] | None = None) -> int:
    from aota_forge.interactive_ingress.contract import (
        DEFAULT_PREPARATION_TTL_SECONDS,
        InteractiveIngressError,
    )

    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "ttl_seconds", None) is None:
        args.ttl_seconds = DEFAULT_PREPARATION_TTL_SECONDS
    try:
        return int(args.handler(args))
    except InteractiveIngressError as exc:
        _emit({"ok": False, "code": exc.code, "message": exc.message})
        return EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - bounded fail-closed CLI boundary
        _emit({"ok": False, "code": "INTERACTIVE_INGRESS_FAILED", "message": str(exc)[:512]})
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
