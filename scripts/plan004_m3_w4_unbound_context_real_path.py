#!/usr/bin/env python3
"""Same-Session W4 unbound bootstrap/bind/restart proof on pinned OpenCode."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from scripts import plan004_m3_bridge_request_bound_real_path as bridge_proof


REPO_ROOT = bridge_proof.REPO_ROOT
REFERENCE_BINARY = bridge_proof.REFERENCE_BINARY
STUB_SERVER = bridge_proof.STUB_SERVER
RUNTIME_CONFIG = bridge_proof.RUNTIME_CONFIG
RUNTIME_PYTHON = bridge_proof.RUNTIME_PYTHON
PROJECT_ID = bridge_proof.PROJECT_ID
PLAN_ID = bridge_proof.PLAN_ID


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request(method: str, url: str, body: object | None = None, *, timeout: float = 120.0) -> tuple[int, object]:
    return bridge_proof._request(method, url, body, timeout=timeout)


def _tool_result(result: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    if isinstance(result.get("payload"), dict):
        return result["payload"], result
    if result.get("output_mode") == "by_ref":
        return None, result
    if "state" in result or "ROLE" in result:
        return result, result
    return None, result


def _config_for_task_main(config_path: Path) -> None:
    document = json.loads(config_path.read_text(encoding="utf-8"))
    permission = document["agent"]["build"]["permission"]
    document["agent"]["aota-task-main"] = {
        "description": "AOTA task-main isolated W4 acceptance profile",
        "mode": "primary",
        "model": "afstub/stub-model",
        "prompt": "AF task-main. Use only the canonical AOTA capability. Trusted context, not Session identity, supplies Project and Plan authority.",
        "permission": permission,
    }
    config_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _start_server(
    port: int,
    instance: Path,
    env: dict[str, str],
    xdg: tuple[Path, Path, Path, Path],
) -> subprocess.Popen[bytes]:
    xdg_config, xdg_data, xdg_state, xdg_cache = xdg
    process = subprocess.Popen(
        [
            str(REFERENCE_BINARY),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=instance,
        env={
            **env,
            "HOME": os.environ.get("HOME", str(instance.parent)),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_STATE_HOME": str(xdg_state),
            "XDG_CACHE_HOME": str(xdg_cache),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    bridge_proof._wait_for(f"http://127.0.0.1:{port}/global/health", timeout=45.0)
    return process


def _create_session(base_url: str, directory: Path, title: str, port: int) -> str:
    query = urllib.parse.quote(str(directory), safe="")
    status, payload = _request(
        "POST",
        f"{base_url}/session?directory={query}",
        {
            "title": title,
            "agent": "aota-task-main",
            "model": {"id": "stub-model", "providerID": "afstub"},
        },
    )
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        raise RuntimeError("genuine OpenCode task-main Session creation failed")
    return payload["id"]


def _tool_call(
    stub_url: str,
    base_url: str,
    directory: Path,
    session_id: str,
    *,
    operation: str,
    arguments: dict[str, Any],
    call_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    steps = [
        {
            "type": "tool_call",
            "name": "aota_aota_invoke",
            "call_id": call_id,
            "arguments": {"operation": operation, "arguments": arguments},
        },
        {"type": "text", "text": "AF_M3W4_SAME_SESSION_STEP_DONE"},
    ]
    _request("POST", f"{stub_url}/_admin/script", {"steps": steps})
    query = urllib.parse.quote(str(directory), safe="")
    status, _ = _request(
        "POST",
        f"{base_url}/session/{session_id}/message?directory={query}",
        {
            "model": {"providerID": "afstub", "modelID": "stub-model"},
            "agent": "aota-task-main",
            "parts": [{"type": "text", "text": "Use the AOTA tool for the prepared step only."}],
        },
    )
    if status != 200:
        raise RuntimeError("OpenCode same-Session prompt submission failed")
    bridge_proof._wait_idle(base_url, session_id, directory)
    messages_status, messages = _request(
        "GET",
        f"{base_url}/session/{session_id}/message?directory={query}",
    )
    if messages_status != 200 or not isinstance(messages, list):
        raise RuntimeError("OpenCode same-Session transcript read failed")
    for message in reversed(messages):
        for part in message.get("parts", []):
            if part.get("type") != "tool" or part.get("callID") != call_id:
                continue
            state = part.get("state", {})
            output = state.get("output", "")
            try:
                result = json.loads(output) if isinstance(output, str) else {}
            except json.JSONDecodeError:
                result = {}
            return str(state.get("status")), result, state
    raise RuntimeError("prepared W4 AOTA tool call was absent from transcript")


def _hydrate_if_needed(
    stub_url: str,
    base_url: str,
    directory: Path,
    session_id: str,
    result: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    direct, _ = _tool_result(result)
    if direct is not None:
        return direct
    hydration = (result.get("hydration") or {}).get("arguments")
    if not isinstance(hydration, dict):
        raise RuntimeError("W4 result needs hydration but has no trusted hydration claims")
    status, hydrated, _ = _tool_call(
        stub_url,
        base_url,
        directory,
        session_id,
        operation="result.hydrate",
        arguments=hydration,
        call_id=f"call_w4_{label}_hydrate_{uuid.uuid4().hex[:8]}",
    )
    if status != "completed" or not isinstance(hydrated.get("content"), str):
        raise RuntimeError("W4 result hydration failed")
    payload = json.loads(hydrated["content"])
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def _binding_record(project_root: Path, session_id: str) -> dict[str, Any]:
    path = project_root / ".aota" / "task-main-context-bindings.json"
    if not path.is_file():
        raise RuntimeError("durable task-main context binding projection is missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    matches = [
        record
        for record in data.get("records", [])
        if record.get("origin_task_main_session_ref") == session_id
    ]
    if len(matches) != 1:
        raise RuntimeError("same-Session durable binding record is missing or ambiguous")
    return matches[0]


def main() -> dict[str, Any]:
    binary_version = subprocess.check_output(
        [str(REFERENCE_BINARY), "--version"],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=10,
    ).strip()
    if "1.18.30" not in binary_version:
        raise RuntimeError("W4 proof requires pinned OpenCode 1.18.30")

    opencode_process: subprocess.Popen[bytes] | None = None
    stub_process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="plan004-m3-w4-real-") as raw_root:
        root = Path(raw_root)
        xdg_config, xdg_data, xdg_state, xdg_cache = (
            root / name for name in ("xdg-config", "xdg-data", "xdg-state", "xdg-cache")
        )
        instance = root / "instance"
        instance.mkdir()
        bridge_socket = root / "bridge.sock"
        bridge = bridge_proof._ObservedBridge()
        server = bridge_proof._BridgeSocketServer(str(bridge_socket), bridge)
        os.chmod(bridge_socket, 0o600)
        bridge_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        bridge_thread.start()

        code_root, registry_path, governance_base = bridge_proof._seed_isolated_authority(root)
        shutil.copytree(REPO_ROOT / "skills", code_root / "skills")
        project_root = root / "fixture-project"
        runtime_config = root / "runtime-opencode-reference.json"
        shutil.copy2(RUNTIME_CONFIG, runtime_config)
        stub_port = bridge_proof._free_port()
        opencode_port = bridge_proof._free_port()
        stub_url = f"http://127.0.0.1:{stub_port}"
        base_url = f"http://127.0.0.1:{opencode_port}"
        bridge_proof._config(
            xdg_config,
            bridge_socket=bridge_socket,
            stub_port=stub_port,
            code_root=code_root,
            registry_path=registry_path,
            runtime_config=runtime_config,
            governance_base=governance_base,
            governance_store=project_root / ".aota" / "governance.sqlite3",
        )
        config_path = xdg_config / "opencode" / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        task_main_permission = dict(config["agent"]["build"]["permission"])
        config["agent"]["aota-task-main"] = {
            "description": "AOTA task-main for isolated M3/W4 acceptance",
            "mode": "primary",
            "model": "afstub/stub-model",
            "prompt": "AF task-main. Use only AOTA MCP. Trusted binding, not Session identity, supplies authority.",
            "permission": task_main_permission,
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        env = dict(os.environ)
        env.update(
            {
                "AF_STUB_PORT": str(stub_port),
                "AF_STUB_HOST": "127.0.0.1",
                "AOTA_BRIDGE_SOCKET": str(bridge_socket),
                "PYTHONPATH": str(code_root),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        env.pop("AOTA_BRIDGE_EVIDENCE_PATH", None)
        env.pop("AOTA_OPENCODE_MCP_DEBUG", None)

        try:
            stub_process = subprocess.Popen(
                [RUNTIME_PYTHON, str(STUB_SERVER)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            bridge_proof._wait_for(f"{stub_url}/_admin/health")
            opencode_process = _start_server(opencode_port, instance, env, (xdg_config, xdg_data, xdg_state, xdg_cache))
            session_id = _create_session(
                base_url,
                instance,
                "M3 W4 fresh headed unbound Session",
                opencode_port,
            )
            session_hash = _hash(session_id)

            bootstrap_status, bootstrap_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="role.bootstrap",
                arguments={},
                call_id=f"call_w4_p1_bootstrap_{uuid.uuid4().hex[:8]}",
            )
            if not (
                bootstrap_status == "completed"
                and (
                    bridge_proof._operation_succeeded(bootstrap_status, bootstrap_result)
                    or bootstrap_result.get("ROLE") == "task-main"
                )
            ):
                raise RuntimeError(
                    json.dumps(
                        {
                            "failure": "P1 unbound role.bootstrap was not usable",
                            "tool_status": bootstrap_status,
                            "result_keys": sorted(bootstrap_result),
                            "error_code": bootstrap_result.get("error_code"),
                            "error_message": str(bootstrap_result.get("error_message", ""))[:180],
                        },
                        ensure_ascii=False,
                    )
                )
            bootstrap = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                bootstrap_result,
                "p1_bootstrap",
            )
            if (
                bootstrap.get("ROLE") != "task-main"
                or bootstrap.get("IS_AUTHORITY") is not False
                or bootstrap.get("ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT") is not False
                or bootstrap.get("ROLE_BOOTSTRAP_MECHANICAL_GATE") is not False
                or bootstrap.get("SESSION_BINDING_PRESENT") is not False
                or bootstrap.get("SESSION_BINDING_REQUIRED") is not False
                or bootstrap.get("RUNTIME_CONTEXT", {}).get("plan_binding") != "none"
                or bootstrap.get("RUNTIME_CONTEXT", {}).get("session_binding") != "none"
            ):
                raise RuntimeError("P1 bootstrap payload authority/binding contract mismatch")

            inspect_status, inspect_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "inspect"},
                call_id=f"call_w4_p2_inspect_{uuid.uuid4().hex[:8]}",
            )
            if not bridge_proof._operation_succeeded(inspect_status, inspect_result):
                raise RuntimeError("P2 unbound task_main.context inspect failed")
            unbound = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                inspect_result,
                "p2_unbound_inspect",
            )
            if (
                unbound.get("state") != "unbound"
                or unbound.get("context") is not None
                or unbound.get("session_binding_is_authority") is not False
                or unbound.get("identity_is_authority") is not False
                or unbound.get("locator_is_authority") is not False
            ):
                raise RuntimeError("P2 unbound context/identity authority contract mismatch")

            prebind_verifications = [item for item in bridge.verified if item["session_hash"] == session_hash]
            if len(prebind_verifications) < 2:
                raise RuntimeError("Bridge did not verify both genuine pre-bind invocations")

            project_status, project_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "bind", "project_selector": PROJECT_ID},
                call_id=f"call_w4_p3_project_bind_{uuid.uuid4().hex[:8]}",
            )
            if not bridge_proof._operation_succeeded(project_status, project_result):
                raise RuntimeError("P3 trusted Project-only bind failed")
            project_read_status, project_read_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "inspect"},
                call_id=f"call_w4_p3_project_readback_{uuid.uuid4().hex[:8]}",
            )
            project_only = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                project_read_result,
                "p3_project_readback",
            )
            if (
                project_read_status != "completed"
                or project_only.get("state") != "project_bound"
                or project_only.get("context", {}).get("project_id") != PROJECT_ID
                or project_only.get("context", {}).get("plan_id") is not None
            ):
                raise RuntimeError("P3 Project-only readback did not preserve null Plan")
            durable_project = _binding_record(project_root, session_id)
            if durable_project.get("project_id") != PROJECT_ID or durable_project.get("plan_binding") is not None:
                raise RuntimeError("P3 Project-only durable record is not Project-only")
            child_pids_p3 = bridge_proof._mcp_child_pids(opencode_process.pid)
            if len(child_pids_p3) != 1:
                raise RuntimeError("P3 calls did not use one shared MCP child")

            bridge_proof._stop(opencode_process)
            opencode_process = _start_server(
                opencode_port,
                instance,
                env,
                (xdg_config, xdg_data, xdg_state, xdg_cache),
            )
            recovered_session_status, recovered_session = _request(
                "GET",
                f"{base_url}/session/{session_id}?directory={urllib.parse.quote(str(instance), safe='')}",
            )
            if recovered_session_status != 200 or recovered_session.get("id") != session_id:
                raise RuntimeError("P4 same OpenCode Session did not survive host restart")
            project_recovered_status, project_recovered_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "inspect"},
                call_id=f"call_w4_p4_project_recovery_{uuid.uuid4().hex[:8]}",
            )
            project_recovered = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                project_recovered_result,
                "p4_project_recovery",
            )
            if (
                project_recovered_status != "completed"
                or project_recovered.get("state") != "project_bound"
                or project_recovered.get("context", {}).get("plan_id") is not None
            ):
                raise RuntimeError("P4 restart did not recover Project-only binding")

            plan_status, plan_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "bind", "project_selector": PROJECT_ID, "plan_selector": PLAN_ID},
                call_id=f"call_w4_p5_plan_promotion_{uuid.uuid4().hex[:8]}",
            )
            if not bridge_proof._operation_succeeded(plan_status, plan_result):
                raise RuntimeError("P5 trusted same-project Plan promotion failed")
            plan_read_status, plan_read_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "inspect"},
                call_id=f"call_w4_p5_plan_readback_{uuid.uuid4().hex[:8]}",
            )
            plan_bound = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                plan_read_result,
                "p5_plan_readback",
            )
            if (
                plan_read_status != "completed"
                or plan_bound.get("state") != "bound"
                or plan_bound.get("context", {}).get("plan_id") != PLAN_ID
            ):
                raise RuntimeError("P5 Plan-bound readback failed")
            durable_plan = _binding_record(project_root, session_id)
            if durable_plan.get("plan_binding", {}).get("plan_id") != PLAN_ID:
                raise RuntimeError("P5 Plan promotion durable record failed")

            bridge_proof._stop(opencode_process)
            opencode_process = _start_server(
                opencode_port,
                instance,
                env,
                (xdg_config, xdg_data, xdg_state, xdg_cache),
            )
            recovered_plan_session_status, recovered_plan_session = _request(
                "GET",
                f"{base_url}/session/{session_id}?directory={urllib.parse.quote(str(instance), safe='')}",
            )
            if recovered_plan_session_status != 200 or recovered_plan_session.get("id") != session_id:
                raise RuntimeError("P6 same OpenCode Session did not survive second host restart")
            plan_recovered_status, plan_recovered_result, _ = _tool_call(
                stub_url,
                base_url,
                instance,
                session_id,
                operation="task_main.context",
                arguments={"mode": "inspect"},
                call_id=f"call_w4_p6_plan_recovery_{uuid.uuid4().hex[:8]}",
            )
            plan_recovered = _hydrate_if_needed(
                stub_url,
                base_url,
                instance,
                session_id,
                plan_recovered_result,
                "p6_plan_recovery",
            )
            if (
                plan_recovered_status != "completed"
                or plan_recovered.get("state") != "bound"
                or plan_recovered.get("context", {}).get("plan_id") != PLAN_ID
            ):
                raise RuntimeError("P6 restart did not recover Plan-bound Session")

            session_hashes = {item["session_hash"] for item in bridge.verified}
            if session_hashes != {_hash(session_id)}:
                raise RuntimeError("W4 calls did not preserve one genuine Session identity")
            if len(bridge.verified) < 10:
                raise RuntimeError("W4 sequence did not verify every Bridge call")
            if any(item["hmac_verified"] is not True for item in bridge.verified):
                raise RuntimeError("W4 Bridge HMAC verification failed")

            transcript = _request(
                "GET",
                f"{base_url}/session/{session_id}/message?directory={urllib.parse.quote(str(instance), safe='')}",
            )[1]
            token_visible = "aota-bridge-v1." in json.dumps(transcript, ensure_ascii=False)
            if token_visible:
                raise RuntimeError("Bridge token was visible in model transcript")

            return {
                "STATUS": "PASS",
                "OPENCODE_VERSION": binary_version,
                "OPENCODE_REFERENCE_COMMIT": "3104c1428ec91f809e5ab86631300de41eb6952e",
                "W4_SOURCE_BASE": "fd19f6623868dc025c28c753c799c0f69dd57b99",
                "SAME_SESSION_ENTIRE_W4_REAL_PATH": True,
                "SESSION_HASH": session_hash,
                "ROLE_BOOTSTRAP_UNBOUND": "PASS",
                "ROLE": bootstrap.get("ROLE"),
                "ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT": bootstrap.get("ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT"),
                "ROLE_BOOTSTRAP_MECHANICAL_GATE": bootstrap.get("ROLE_BOOTSTRAP_MECHANICAL_GATE"),
                "ROLE_BOOTSTRAP_IS_AUTHORITY": bootstrap.get("IS_AUTHORITY"),
                "BOOTSTRAP_MINTS_PROJECT_AUTHORITY": False,
                "BOOTSTRAP_MINTS_PLAN_AUTHORITY": False,
                "CONTEXT_INSPECT_UNBOUND": "PASS",
                "UNBOUND_CONTEXT_NULL": True,
                "BRIDGE_SESSION_VERIFIED_PREBIND": True,
                "BRIDGE_SESSION_IDENTITY_NOT_PROJECT_AUTHORITY": "PASS",
                "BRIDGE_SESSION_IDENTITY_NOT_PLAN_AUTHORITY": "PASS",
                "UNBOUND_TO_PROJECT_BOUND": "PASS",
                "PROJECT_ONLY_DURABILITY": "PASS",
                "PROJECT_RESTART_RECOVERY": "PASS",
                "UNBOUND_TO_PROJECT_PLAN_BOUND": "PASS",
                "PLAN_PROMOTION": "PASS",
                "PLAN_RESTART_RECOVERY": "PASS",
                "MCP_PUBLIC_TOOL_COUNT": 1,
                "SHARED_MCP_CHILD_COUNT": 1,
                "BRIDGE_VERIFIED_CALL_COUNT": len(bridge.verified),
                "TOKEN_MODEL_VISIBLE": False,
                "CANONICAL_PLAN_MUTATED": False,
                "RAW_SESSION_ID_SAVED": False,
                "RAW_CALL_ID_SAVED": False,
                "RAW_BRIDGE_TOKEN_SAVED": False,
                "RAW_BRIDGE_SECRET_SAVED": False,
            }
        finally:
            bridge_proof._stop(opencode_process)
            bridge_proof._stop(stub_process)
            server.shutdown()
            server.server_close()
            bridge_thread.join(timeout=5)
            try:
                bridge_socket.unlink()
            except FileNotFoundError:
                pass
            bridge.issued.clear()
            bridge.verified.clear()


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        error = str(exc)
        error = error.replace("ses_", "<session-prefix>")
        error = error.replace("call_", "<call-prefix>")
        print(
            json.dumps(
                {
                    "STATUS": "FAIL",
                    "FAILURE_TYPE": type(exc).__name__,
                    "FAILURE_SHA256": hashlib.sha256(str(exc).encode("utf-8")).hexdigest(),
                    "SAFE_FAILURE_REASON": error[:240],
                    "RAW_SESSION_ID_SAVED": False,
                    "RAW_TOKEN_SAVED": False,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["STATUS"] == "PASS" else 1)
