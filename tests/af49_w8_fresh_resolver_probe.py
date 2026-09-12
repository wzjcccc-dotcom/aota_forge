"""AF #49 M1/W8 fresh-process probe: recover the Worker binding from durable state only.

Run as a subprocess with ``AOTA_W3_MCP_ROOT`` pointing at the trusted worktree
bootstrap root. This probe authors no Worker binding environment itself:
the production task-main bootstrap rebuilds the trusted task-main binding from
the durable bootstrap + stores, and the production host client derives the
Worker child environment through the governed resolver.

Outputs bounded JSON: {"ok": bool, "env"?: {...}, "error"?: str}.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    out = Path(os.environ["AOTA_W8_PROBE_OUTPUT"])
    payload = json.loads(os.environ["AOTA_W8_PROBE_PAYLOAD"])
    result: dict[str, object]
    try:
        from aota_forge.composition.task_main_host_bootstrap import (
            try_build_task_main_binding,
        )

        binding = try_build_task_main_binding()
        if binding is None:
            raise RuntimeError("no trusted task-main bootstrap binding")
        dispatcher = binding.trusted_task_main_context.control_service._dispatcher
        host = None
        for adapter in dispatcher.registry._adapters.values():
            host = getattr(adapter, "_host_client", None)
            if host is not None:
                break
        if host is None:
            raise RuntimeError("no Hermes host client on the fresh dispatcher graph")
        env = host._build_explicit_supervisor_env(payload)
        result = {
            "ok": True,
            "env": {str(k): str(v) for k, v in env.items()},
            "project_id": binding.project_id,
            "worktree_id": binding.worktree_id,
            "canonical_task_id": binding.canonical_task_id,
        }
    except Exception as exc:  # bounded probe failure reporting only
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out.write_text(json.dumps(result), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
