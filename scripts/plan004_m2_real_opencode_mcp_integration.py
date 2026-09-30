#!/usr/bin/env python3
"""Bounded real OpenCode -> bridge -> MCP carrier proof for Plan #004 M2."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_BINARY = Path(
    os.environ.get(
        "AF_OPENCODE_BINARY",
        "/home/latios/.local/share/aota-forge/opencode-reference/v1.18.30/bin/opencode",
    )
)
WRAPPER = REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py"
PLUGIN = REPO_ROOT / "tests" / "fixtures" / "plan004-trusted-opencode-plugin.mjs"
REGISTRY = Path("/home/latios/workspace/.aota/workspaces.json")
RUNTIME_CONFIG = Path(os.environ.get("AOTA_FORGE_RUNTIME_CONFIG", "/home/latios/.config/aota-forge/runtime.json"))
GOVERNANCE_BASE = Path("/home/latios/workspace/plans")
GOVERNANCE_STORE = REPO_ROOT / ".aota" / "governance.sqlite3"
RUNTIME_PYTHON = os.environ.get("AF_RUNTIME_PYTHON", "/usr/bin/python3")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(method: str, url: str, body: object | None = None, timeout: float = 10.0) -> tuple[int, object]:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return response.status, {}
            try:
                return response.status, json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return response.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, raw.decode("utf-8", errors="replace")


def _wait_for(url: str, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = _request("GET", url, timeout=2.0)
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {url}")


def _wait_idle(base_url: str, session_id: str, directory: Path, *, timeout: float = 90.0) -> None:
    query = urllib.parse.quote(str(directory))
    deadline = time.monotonic() + timeout
    observed_activity = False
    while time.monotonic() < deadline:
        status, payload = _request("GET", f"{base_url}/session/status?directory={query}")
        if status == 200 and isinstance(payload, dict):
            state = payload.get(session_id, {}).get("type", "idle")
            observed_activity = observed_activity or state in {"busy", "retry"}
            message_status, messages = _request(
                "GET",
                f"{base_url}/session/{session_id}/message?directory={query}",
            )
            if state == "idle" and (observed_activity or (message_status == 200 and messages)):
                return
        time.sleep(0.2)
    raise RuntimeError(f"session {session_id} did not become idle")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _probe_stdio_tools(environment: dict[str, str]) -> list[str]:
    """Run a real MCP tools/list against the same AF wrapper and child path."""
    code = """
import asyncio, json, os
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    params = StdioServerParameters(
        command=os.environ["AF_RUNTIME_PYTHON"],
        args=[os.environ["AF_MCP_WRAPPER"]],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.list_tools()
            print(json.dumps(sorted(tool.name for tool in result.tools)))

asyncio.run(main())
"""
    probe_env = dict(environment)
    probe_env["AF_RUNTIME_PYTHON"] = RUNTIME_PYTHON
    probe_env["AF_MCP_WRAPPER"] = str(WRAPPER)
    process = subprocess.run(
        [RUNTIME_PYTHON, "-c", code],
        env=probe_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        raise RuntimeError(f"real MCP tools/list probe failed: {process.stderr[-1000:]}")
    lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("real MCP tools/list probe returned no result")
    tools = json.loads(lines[-1])
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise RuntimeError("real MCP tools/list probe returned malformed names")
    return tools


def _config(config_root: Path, bridge_socket: Path, stub_port: int, debug_path: Path) -> None:
    config_dir = config_root / "opencode"
    config_dir.mkdir(parents=True)
    environment = {
        "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
        "AOTA_FORGE_REGISTRY": str(REGISTRY),
        "AOTA_FORGE_RUNTIME_CONFIG": str(RUNTIME_CONFIG),
        "AOTA_FORGE_GOVERNANCE_BASE": str(GOVERNANCE_BASE),
        "AOTA_FORGE_GOVERNANCE_STORE": str(GOVERNANCE_STORE),
        "AOTA_BRIDGE_SOCKET": str(bridge_socket),
        "AOTA_GLOBAL_MCP": "1",
        "AOTA_OPENCODE_MCP_DEBUG": str(debug_path),
    }
    document = {
        "$schema": "https://opencode.ai/config.json",
        "plugin": [PLUGIN.as_uri()],
        "mcp": {
            "aota": {
                "type": "local",
                "command": [RUNTIME_PYTHON, str(WRAPPER)],
                "environment": environment,
                "enabled": True,
                "timeout": 15000,
            }
        },
        "permission": {
            "aota_aota_invoke": "allow",
            "bash": "deny",
            "read": "deny",
            "edit": "deny",
            "list": "deny",
            "grep": "deny",
            "task": "deny",
            "question": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "lsp": "deny",
            "todowrite": "deny",
            "doom_loop": "deny",
            "external_directory": "deny",
        },
        "provider": {
            "afstub": {
                "name": "AF M2 bounded stub",
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": f"http://127.0.0.1:{stub_port}/v1", "apiKey": "afstub"},
                "models": {"stub-model": {"name": "stub-model"}},
            }
        },
        "agent": {
            "build": {
                "mode": "primary",
                "model": "afstub/stub-model",
                "permission": {
                    "aota_aota_invoke": "allow",
                    "bash": "deny",
                    "read": "deny",
                    "edit": "deny",
                    "list": "deny",
                    "grep": "deny",
                    "task": "deny",
                    "question": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "lsp": "deny",
                    "todowrite": "deny",
                    "doom_loop": "deny",
                    "external_directory": "deny",
                },
            }
        },
    }
    (config_dir / "opencode.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if not REFERENCE_BINARY.is_file():
        raise SystemExit(f"reference binary missing: {REFERENCE_BINARY}")
    if not PLUGIN.is_file():
        raise SystemExit(f"trusted plugin missing: {PLUGIN}")
    try:
        binary_version = subprocess.check_output(
            [str(REFERENCE_BINARY), "--version"], text=True, stderr=subprocess.STDOUT, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not verify pinned OpenCode binary: {exc}") from exc
    if "1.18.30" not in binary_version:
        raise SystemExit(f"unexpected OpenCode binary version: {binary_version!r}")

    opencode: subprocess.Popen[bytes] | None = None
    stub: subprocess.Popen[bytes] | None = None
    bridge: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="plan004-m2-real-") as raw_root:
        root = Path(raw_root)
        xdg_config = root / "xdg-config"
        xdg_data = root / "xdg-data"
        xdg_state = root / "xdg-state"
        xdg_cache = root / "xdg-cache"
        instance = root / "instance"
        instance.mkdir()
        bridge_socket = root / "bridge.sock"
        bridge_evidence = root / "bridge-evidence.jsonl"
        mcp_debug = root / "mcp-debug.jsonl"
        opencode_log = (root / "opencode.log").open("wb")
        stub_log = (root / "stub.log").open("wb")
        bridge_log = (root / "bridge.log").open("wb")
        stub_port = _free_port()
        opencode_port = _free_port()
        _config(xdg_config, bridge_socket, stub_port, mcp_debug)
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "AF_STUB_PORT": str(stub_port),
                "AF_STUB_HOST": "127.0.0.1",
                "AOTA_BRIDGE_SOCKET": str(bridge_socket),
                "AOTA_BRIDGE_EVIDENCE_PATH": str(bridge_evidence),
            }
        )
        stub = subprocess.Popen(
            [RUNTIME_PYTHON, str(REPO_ROOT / "scripts" / "m1_w2_openai_stub_server.py")],
            env=env,
            stdout=stub_log,
            stderr=subprocess.STDOUT,
        )
        bridge = subprocess.Popen(
            [RUNTIME_PYTHON, "-m", "aota_forge.session_bridge", "--socket", str(bridge_socket)],
            env=env,
            stdout=bridge_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for(f"http://127.0.0.1:{stub_port}/_admin/health")
            for _ in range(100):
                if bridge_socket.exists():
                    break
                time.sleep(0.01)
            if not bridge_socket.exists():
                raise RuntimeError("bridge socket did not start")
            probe_environment = {
                **env,
                "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
                "AOTA_FORGE_REGISTRY": str(REGISTRY),
                "AOTA_FORGE_RUNTIME_CONFIG": str(RUNTIME_CONFIG),
                "AOTA_GLOBAL_MCP": "1",
                "AOTA_BRIDGE_SOCKET": str(bridge_socket),
            }
            mcp_tool_names = _probe_stdio_tools(probe_environment)
            steps = [
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "call_id": "call_plan004_A",
                    "arguments": {"operation": "task_main.context", "arguments": {"mode": "inspect"}},
                },
                {"type": "text", "text": "AF_PLAN004_SESSION_A_DONE"},
                {
                    "type": "tool_call",
                    "name": "aota_aota_invoke",
                    "call_id": "call_plan004_B",
                    "arguments": {"operation": "task_main.context", "arguments": {"mode": "inspect"}},
                },
                {"type": "text", "text": "AF_PLAN004_SESSION_B_DONE"},
            ]
            _request("POST", f"http://127.0.0.1:{stub_port}/_admin/script", {"steps": steps})
            opencode = subprocess.Popen(
                [str(REFERENCE_BINARY), "--print-logs", "--log-level", "DEBUG", "serve", "--hostname", "127.0.0.1", "--port", str(opencode_port)],
                cwd=instance,
                env={
                    **env,
                    "HOME": os.environ.get("HOME", "/tmp"),
                    "XDG_CONFIG_HOME": str(xdg_config),
                    "XDG_DATA_HOME": str(xdg_data),
                    "XDG_STATE_HOME": str(xdg_state),
                    "XDG_CACHE_HOME": str(xdg_cache),
                    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                },
                stdout=opencode_log,
                stderr=subprocess.STDOUT,
            )
            base_url = f"http://127.0.0.1:{opencode_port}"
            _wait_for(f"{base_url}/global/health", timeout=45.0)
            mcp_status = _request("GET", f"{base_url}/mcp")[1]
            created: list[str] = []
            for title in ("plan004-session-A", "plan004-session-B"):
                status, payload = _request(
                    "POST",
                    f"{base_url}/session?directory={urllib.parse.quote(str(instance))}",
                    {
                        "title": title,
                        "agent": "build",
                        "model": {"id": "stub-model", "providerID": "afstub"},
                    },
                )
                if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                    raise RuntimeError(f"session creation failed: {status} {payload}")
                created.append(payload["id"])
            prompt_results: list[object] = []
            for session_id in created:
                status, payload = _request(
                    "POST",
                    f"{base_url}/session/{session_id}/message?directory={urllib.parse.quote(str(instance))}",
                    {
                        "model": {"providerID": "afstub", "modelID": "stub-model"},
                        "agent": "build",
                        "parts": [{"type": "text", "text": "run the bounded Plan #004 bridge inspection"}],
                    },
                    timeout=120.0,
                )
                prompt_results.append({"status": status, "payload": payload})
                if status != 200:
                    raise RuntimeError(f"prompt submission failed: {status} {payload}")
            messages = {
                session_id: _request(
                    "GET",
                    f"{base_url}/session/{session_id}/message?directory={urllib.parse.quote(str(instance))}",
                )[1]
                for session_id in created
            }
            observations = [
                json.loads(line)
                for line in bridge_evidence.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ] if bridge_evidence.exists() else []
            session_ids = {item.get("session_id") for item in observations}
            call_ids = {item.get("call_id") for item in observations}
            bridge_ids = {item.get("bridge_id") for item in observations}
            real_correlation = set(created) <= session_ids
            exact_correlation = len(call_ids) >= 2
            one_epoch = len(bridge_ids) == 1
            token_not_model_visible = "aota-bridge-v1." not in json.dumps(messages, ensure_ascii=False)
            evidence_text = bridge_evidence.read_text(encoding="utf-8") if bridge_evidence.exists() else ""
            secret_in_evidence = "aota-bridge-v1." in evidence_text or "plan004-test-secret" in evidence_text
            source_frontier = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, timeout=10
            ).strip()
            reference_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd="/home/latios/workspace/opencode-reference", text=True, timeout=10
            ).strip()
            result = {
                "status": "PASS" if real_correlation and exact_correlation and one_epoch and token_not_model_visible and not secret_in_evidence and mcp_tool_names == ["aota.invoke"] else "FAIL",
                "real_opencode_version": binary_version,
                "reference_commit": reference_commit,
                "source_frontier": source_frontier,
                "sessions": created,
                "bridge_observations": observations,
                "mcp_debug": mcp_debug.read_text(encoding="utf-8") if mcp_debug.exists() else "",
                "mcp_status": mcp_status,
                "messages": messages,
                "prompt_results": prompt_results,
                "REAL_MCP_CORRELATION": real_correlation,
                "EXACT_CALL_CORRELATION": exact_correlation,
                "ONE_SHARED_BRIDGE_EPOCH": one_epoch,
                "TOKEN_NOT_MODEL_VISIBLE": token_not_model_visible,
                "SECRET_IN_EVIDENCE": secret_in_evidence,
                "MCP_PUBLIC_TOOL_COUNT": len(mcp_tool_names),
                "MCP_TOOL_NAMES": mcp_tool_names,
                "generated_at": time.time(),
                "PROVES": "pinned OpenCode plugin hook mutation reached the real AF MCP child and was verified before task_main.context dispatch",
                "DOES_NOT_PROVE": "M3 production activation or normal 4096 service configuration",
            }
            if result["status"] != "PASS":
                opencode_log.flush()
                stub_log.flush()
                bridge_log.flush()
                result["opencode_log_tail"] = opencode_log.name and Path(opencode_log.name).read_text(
                    encoding="utf-8", errors="replace"
                )[-4000:]
                result["stub_log_tail"] = Path(stub_log.name).read_text(encoding="utf-8", errors="replace")[-2000:]
                result["bridge_log_tail"] = Path(bridge_log.name).read_text(encoding="utf-8", errors="replace")[-2000:]
            evidence_output = os.environ.get("AF_PLAN004_M2_EVIDENCE_OUTPUT")
            if evidence_output:
                evidence_path = Path(evidence_output)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "PASS" else 1
        finally:
            _stop(opencode)
            _stop(stub)
            _stop(bridge)
            opencode_log.close()
            stub_log.close()
            bridge_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
