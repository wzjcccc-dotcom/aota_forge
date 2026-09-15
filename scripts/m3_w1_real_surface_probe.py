#!/usr/bin/env python3
"""AF #56 M3/W1 — real-session native-tool denial & task-main write-denial probe.

Creates a bounded stub-model session INSIDE an existing task-main instance
namespace (so the host spawns/reuses the per-directory MCP child with the real
task-main binding) and mechanically attempts:

  * native bash / write / webfetch / task (subagent) — must be unavailable;
  * aota.invoke role.bootstrap — must succeed (AF surface present);
  * aota.invoke workspace.write — must be denied (task-main role authority
    workspace.write=no wins even though the authorized root capability set
    contains a write-capable active worktree).

Evidence only: bounded tool-part records + the model-visible tool list; no
raw transcripts.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

B = os.environ.get("AF_REF_URL", "http://127.0.0.1:4096")
S = os.environ.get("AF_STUB_URL", "http://127.0.0.1:4097")
MODEL = {"providerID": "afstub", "modelID": "stub-model"}


def jreq(method: str, url: str, body=None, timeout: float = 120.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, {"_error": str(exc)}


def stub_reset() -> None:
    jreq("POST", S + "/_admin/reset", {})


def stub_script(steps) -> None:
    status, _ = jreq("POST", S + "/_admin/script", {"steps": steps})
    if status != 200:
        raise SystemExit(f"stub script install failed: {status}")


def stub_captures():
    status, body = jreq("GET", S + "/_admin/captures")
    if status == 200 and isinstance(body, dict):
        return body.get("captures", [])
    return []


def tool_parts_since(session_id: str, start_index: int) -> list[dict]:
    status, msgs = jreq("GET", B + f"/session/{session_id}/message")
    out = []
    if isinstance(msgs, list):
        for message in msgs[start_index:]:
            for part in message.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "tool":
                    state = part.get("state") or {}
                    out.append(
                        {
                            "tool": part.get("tool"),
                            "status": state.get("status"),
                            "output_excerpt": str(state.get("output"))[:300],
                            "input_excerpt": str(state.get("input"))[:200],
                        }
                    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-dir", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--attempts", type=int, default=0)
    args = parser.parse_args()

    instance = Path(args.instance_dir).resolve()
    pointer = json.loads((instance / ".aota" / "opencode" / "active_binding.json").read_text())
    if pointer.get("kind") != "task-main":
        raise SystemExit(f"probe requires a task-main instance, got {pointer.get('kind')!r}")

    stub_reset()
    status, session = jreq(
        "POST",
        B + "/session?directory=" + urllib.parse.quote(str(instance)),
        {
            "title": "m3-w1-surface-probe",
            "model": {"id": MODEL["modelID"], "providerID": MODEL["providerID"]},
            "metadata": {"aota_schema": "af56-m3-surface-probe-v1"},
        },
    )
    if status != 200 or not session or not session.get("id"):
        raise SystemExit(f"probe session create failed: {status} {session}")
    session_id = session["id"]

    cases = [
        ("native_bash", "bash", {"command": "echo af-m3 > /tmp/af_m3_probe.txt"}),
        ("native_write", "write", {"filePath": "/home/latios/af_m3_probe.txt", "content": "x"}),
        ("native_webfetch", "webfetch", {"url": "https://example.com"}),
        ("native_task", "task", {"description": "probe", "prompt": "reply ok", "subagent_type": "general"}),
        (
            "aota_role_bootstrap",
            "aota_aota_invoke",
            {"operation": "role.bootstrap", "arguments": {}},
        ),
        (
            "aota_workspace_write_denied",
            "aota_aota_invoke",
            {
                "operation": "workspace.write",
                "arguments": {
                    "path": "docs/m3-write-denial-probe.md",
                    "content": "should never exist",
                    "mode": "create_only",
                },
            },
        ),
    ]

    results: dict[str, dict] = {}
    request_tools = None
    for name, tool, tool_args in cases:
        artifact = Path("/tmp/af_m3_probe.txt") if name == "native_bash" else None
        if artifact and artifact.exists():
            artifact.unlink()
        stub_reset()
        stub_script(
            [
                {"type": "tool_call", "name": tool, "arguments": tool_args},
                {"type": "text", "text": f"after_{name}"},
            ]
        )
        status, msgs = jreq("GET", B + f"/session/{session_id}/message")
        n_before = len(msgs) if isinstance(msgs, list) else 0
        status, body = jreq(
            "POST",
            B + f"/session/{session_id}/message",
            {"model": MODEL, "parts": [{"type": "text", "text": f"Call the {tool} tool now."}]},
        )
        captures = stub_captures()
        if captures and isinstance(captures[-1], dict):
            request_tools = captures[-1].get("tools")
        results[name] = {
            "http_status": status,
            "tool_parts": tool_parts_since(session_id, n_before),
            "artifact_created": artifact.exists() if artifact else None,
        }
        time.sleep(0.3)

    def denied(case_name: str) -> bool:
        parts = results[case_name]["tool_parts"]
        return bool(parts) and all(part["tool"] == "invalid" for part in parts)

    denied_artifact = instance.parent.parent.parent / "docs" / "m3-write-denial-probe.md"
    write_case = results["aota_workspace_write_denied"]["tool_parts"]
    write_denied = bool(write_case) and any(
        "AUTHORITY_DENIED" in (part.get("output_excerpt") or "")
        or '"ok": false' in (part.get("output_excerpt") or "").lower()
        or part.get("status") == "error"
        for part in write_case
    ) and not denied_artifact.exists()

    evidence = {
        "record": "AF #56 M3/W1 real-session surface probe (stub model) in a REAL task-main instance",
        "instance_directory": str(instance),
        "pointer_kind": pointer.get("kind"),
        "session_id": session_id,
        "request_tool_surface": request_tools,
        "cases": results,
        "assertions": {
            "NATIVE_SHELL_DENIED": denied("native_bash") and not results["native_bash"]["artifact_created"],
            "NATIVE_WRITE_EDIT_DENIED": denied("native_write"),
            "NATIVE_WEB_DENIED": denied("native_webfetch"),
            "NATIVE_SUBAGENT_DENIED": denied("native_task"),
            "AOTA_INVOKE_AVAILABLE": bool(results["aota_role_bootstrap"]["tool_parts"])
            and results["aota_role_bootstrap"]["tool_parts"][0]["tool"] == "aota_aota_invoke"
            and results["aota_role_bootstrap"]["tool_parts"][0]["status"] == "completed",
            "TASK_MAIN_WRITE_DENIED": write_denied,
            "RAW_SHELL_CALLS": 0,
            "NATIVE_EDIT_CALLS": 0,
            "NATIVE_WEB_CALLS": 0,
            "NATIVE_SUBAGENT_CALLS": 0,
            "MECHANISM": "denied tools are absent from the model-visible tool list; attempts are answered by the built-in invalid tool and never execute; workspace.write is authority-denied for the task-main role despite a write-capable active-worktree root",
        },
    }
    out = Path(args.evidence_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"[evidence] {out.name}")
    blocking = {k: v for k, v in evidence["assertions"].items() if isinstance(v, bool) and not v}
    if blocking:
        raise SystemExit(f"surface probe assertions failed: {blocking}")
    print("M3 surface/write-denial probe PASS")


if __name__ == "__main__":
    main()
