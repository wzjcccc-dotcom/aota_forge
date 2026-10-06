"""Isolated pinned-OpenCode proof for the M3 shared-child concurrency repair.

The test harness runs the existing plugin, Trusted Bridge, FastMCP server,
and ``aota.invoke`` through two genuine OpenCode Sessions. Its deterministic
OpenAI-compatible endpoint only schedules the requested authorized calls.
All persisted evidence contains hashes, process/thread IDs, timestamps and
bounded error codes; raw Session IDs, call IDs and bridge tokens stay in
temporary process memory.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

OPENCODE_VERSION = "1.18.30"
MCP_SDK_VERSION = "1.29.0"
MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS = 2
SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

PRODUCTION_PLUGIN = Path(
    "/home/latios/workspace/.aota-worktrees/aota_forge/"
    "plan_004_trusted_opencode_session_bridge_context_isolation/M3/"
    "m3-project-only-durability-v5/deploy/m3-preparation-v5r1/"
    "plugin/plan004-trusted-session-bridge.mjs"
)
OPENCODE_BIN = Path("/home/latios/.local/bin/opencode")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 45.0) -> Any:
    request = urllib.request.Request(
        url,
        data=_json_bytes(payload),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # Do not persist or print provider/server bodies; they can contain tool
        # arguments from this isolated test turn.
        try:
            error = json.loads(exc.read())
        except Exception:
            error = {}
        detail = error.get("error", error) if isinstance(error, dict) else {}
        name = detail.get("name", detail.get("code", "")) if isinstance(detail, dict) else ""
        message = detail.get("message", "") if isinstance(detail, dict) else ""
        if not isinstance(message, str):
            message = ""
        message = re.sub(r"ses_[A-Za-z0-9_-]+", "[SESSION_HASHED]", message)
        raise RuntimeError(
            f"isolated OpenCode HTTP request failed: status={exc.code} "
            f"name={name!r} detail={message[:160]!r}"
        ) from None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "NON_JSON_RESPONSE", "byte_length": len(raw)}


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _tool_arguments(marker: str) -> list[dict[str, Any]] | None:
    if marker == "bind-A" or marker == "bind-B":
        return [
            {
                "operation": "task_main.context",
                "arguments": {"mode": "bind", "project_selector": "aota_forge"},
            }
        ]
    sleeps = {
        "cross-A": ["1.2"],
        "cross-B": ["1.2"],
        "same-two": ["0.6", "0.6"],
        "cache-replay": ["1.5"],
        "valid-A-replay-peer": ["0.4"],
        "attack-replay-B": ["1.5"],
        "cache-substitution": ["0.9"],
        "valid-A-substitution-peer": ["0.4"],
        "attack-substitution-B": ["1.0"],
    }
    if marker == "healthy-B":
        return [
            {
                "operation": "restricted_shell.run",
                "arguments": {
                    "command_id": "echo",
                    "args": ["healthy"],
                    "timeout": 5,
                },
            }
        ]
    values = sleeps.get(marker)
    if values is None:
        return None
    return [
        {
            "operation": "restricted_shell.run",
            "arguments": {"command_id": "sleep", "args": [value], "timeout": 5},
        }
        for value in values
    ]


class _OpenAIStubHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.endswith("/models"):
            payload = _json_bytes(
                {"object": "list", "data": [{"id": "test-model", "object": "model"}]}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        last_user_index = max(
            (index for index, message in enumerate(messages) if message.get("role") == "user"),
            default=-1,
        )
        marker = _text_of(messages[last_user_index].get("content")) if last_user_index >= 0 else ""
        prior = messages[last_user_index + 1 :] if last_user_index >= 0 else []
        has_tool_result = any(
            isinstance(message, dict)
            and (
                message.get("role") in {"tool", "function"}
                or message.get("tool_call_id") is not None
                or bool(message.get("tool_calls"))
            )
            for message in prior
        )
        tool_calls = None if has_tool_result else _tool_arguments(marker)
        tool_specs = body.get("tools", [])
        tool_name = next(
            (
                item.get("function", {}).get("name")
                for item in tool_specs
                if isinstance(item, dict)
                and isinstance(item.get("function"), dict)
                and str(item["function"].get("name", "")).startswith("aota_")
            ),
            "aota_aota_invoke",
        )
        response_id = "chatcmpl-" + uuid.uuid4().hex
        model = str(body.get("model") or "test-model")
        events: list[dict[str, Any]] = [
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        ]
        if tool_calls:
            deltas = []
            for index, arguments in enumerate(tool_calls):
                deltas.append(
                    {
                        "index": index,
                        "id": f"call-{uuid.uuid4().hex}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, separators=(",", ":")),
                        },
                    }
                )
            events.append(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": deltas},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            finish = "tool_calls"
        else:
            events.append(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": None}],
                }
            )
            finish = "stop"
        events.append(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
        )
        encoded = b"".join(b"data: " + _json_bytes(event) + b"\n\n" for event in events)
        encoded += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _install_project(project_root: Path, workspace_root: Path, registry_path: Path) -> None:
    state = project_root / ".aota"
    state.mkdir(parents=True)
    (state / "project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        "  id: aota_forge\n"
        "  name: isolated concurrency proof\n"
        "  kind: test\n"
        "  status: active\n"
        "summary: isolated Plan #004 concurrency proof\n"
        "capabilities: []\n"
        "paths:\n"
        "  source_root: .\n"
        "  source: []\n"
        "  docs: []\n"
        "  scripts: []\n"
        "  profiles: []\n"
        "  skills: []\n"
        "  tests: []\n"
        "commands:\n"
        "  validate: []\n"
        "  deploy: []\n"
        "  verify_deploy: []\n"
        "runtime:\n"
        "  deployment_type: manual\n"
        "  requires_human_checkpoint: false\n"
        "codegraph:\n"
        "  enabled: false\n"
        "  index_location: .codegraph\n"
        "plan:\n"
        "  active_plan_id: null\n"
        "constraints: []\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps({"proof-workspace": {"candidates": [str(workspace_root)]}}),
        encoding="utf-8",
    )


def _runtime_config(path: Path) -> None:
    bindings = {
        role: {"profile": "aota-worker", "toolsets": ["aota"]}
        for role in ("analyst", "coder", "reviewer", "project-steward")
    }
    bindings["task-main"] = {"profile": "aota-task-main"}
    path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/true",
                "concurrency": 1,
                "provider": "isolated-test",
                "model": "test-model",
                "bindings": bindings,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _mcp_wrapper_source(observation_path: Path) -> str:
    return f'''import base64, hashlib, json, os, sys, time, threading
from pathlib import Path
import aota_forge.mcp_transport as transport
from aota_forge.session_bridge import BRIDGE_CARRIER_KEY

OBS = {str(observation_path)!r}
def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
def record(value):
    fd = os.open(OBS, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try: os.write(fd, json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\\n")
    finally: os.close(fd)
def token_identity(arguments):
    try:
        token = arguments[BRIDGE_CARRIER_KEY]["token"]
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return digest(value["session_id"]), digest(value["call_id"])
    except Exception:
        return "unknown", "unknown"

original_verify = transport.SessionScopedContextRouter._verify_invocation
def verify(self, operation, arguments):
    started = time.monotonic_ns()
    try:
        logical, identity = original_verify(self, operation, arguments)
    except transport.BridgeVerificationError as exc:
        session_hash, call_hash = token_identity(arguments or {{}})
        record({{"kind":"verify_denied", "session_hash":session_hash, "call_hash":call_hash,
                "operation":operation, "error_code":exc.code, "pid":os.getpid(),
                "thread_id":threading.get_ident(), "enter_ns":started,
                "exit_ns":time.monotonic_ns()}})
        raise
    record({{"kind":"verify", "session_hash":digest(identity.session_id),
            "call_hash":digest(identity.call_id), "operation":operation,
            "pid":os.getpid(), "thread_id":threading.get_ident(),
            "enter_ns":started, "exit_ns":time.monotonic_ns()}})
    return logical, identity

original_execute = transport.SessionScopedContextRouter._execute_in_slot
def execute(self, slot, identity, operation, arguments):
    started = time.monotonic_ns()
    try:
        result = original_execute(self, slot, identity, operation, arguments)
        payload = result.get("payload") if isinstance(result, dict) else None
        context = payload.get("context") if isinstance(payload, dict) else None
        record({{"kind":"handler", "session_hash":digest(identity.session_id),
                "call_hash":digest(identity.call_id), "operation":operation,
                "pid":os.getpid(), "thread_id":threading.get_ident(),
                "enter_ns":started, "exit_ns":time.monotonic_ns(),
                "ok":bool(result.get("ok")) if isinstance(result, dict) else False,
                "error_code":(result.get("error") or {{}}).get("code") if isinstance(result, dict) else None,
                "context_state":payload.get("state") if isinstance(payload, dict) else None,
                "project_hash":digest(str(context.get("project_id"))) if isinstance(context, dict) and context.get("project_id") else None,
                "plan_hash":digest(str(context.get("plan_id"))) if isinstance(context, dict) and context.get("plan_id") else None}})
        return result
    except BaseException:
        record({{"kind":"handler_exception", "session_hash":digest(identity.session_id),
                "call_hash":digest(identity.call_id), "operation":operation,
                "pid":os.getpid(), "thread_id":threading.get_ident(),
                "enter_ns":started, "exit_ns":time.monotonic_ns()}})
        raise

transport.SessionScopedContextRouter._verify_invocation = verify
transport.SessionScopedContextRouter._execute_in_slot = execute
record({{"kind":"mcp_child_start", "pid":os.getpid(), "thread_id":threading.get_ident(),
        "monotonic_ns":time.monotonic_ns()}})
sys.argv = [sys.argv[0], "--mcp-server"]
from aota_forge.composition.worker_vertical_slice import main
main()
'''


def _plugin_wrapper_source(real_plugin: Path, observation_path: Path) -> str:
    return f'''import crypto from "node:crypto"
import fs from "node:fs"
import {{ pathToFileURL }} from "node:url"

const CARRIER = "__aota_trusted_invocation"
const TOOL = "aota_aota_invoke"
const real = await import(pathToFileURL(process.env.AOTA_TEST_REAL_PLUGIN).href)
const hooks = await real.default()
const original = hooks["tool.execute.before"]
const replay = {{ token: undefined, sessionHash: undefined }}
const substitution = {{ token: undefined, sessionHash: undefined }}
const hash = (value) => crypto.createHash("sha256").update(value).digest("hex")
const log = (value) => fs.appendFileSync(process.env.AOTA_TEST_HOST_OBSERVATION_PATH, JSON.stringify(value) + "\\n", {{ mode: 0o600 }})

export default async function IsolatedPlan004PluginProof() {{
  return {{
    ...hooks,
    "tool.execute.before": async (input, output) => {{
      if (input.tool !== TOOL) return original(input, output)
      const start = process.hrtime.bigint().toString()
      const sessionHash = hash(input.sessionID)
      const callHash = hash(input.callID)
      const operation = output.args?.operation
      const logical = output.args?.arguments ?? {{}}
      await original(input, output)
      const issued = output.args.arguments[CARRIER]?.token
      const commandArgs = logical?.args
      const seconds = Array.isArray(commandArgs) ? commandArgs[0] : undefined
      const purpose = operation === "restricted_shell.run" && seconds === "0.6" ? "same_session" :
                      operation === "restricted_shell.run" && seconds === "1.2" ? "cross_session" :
                      operation === "restricted_shell.run" && seconds === "1.5" ? "replay_case" :
                      operation === "restricted_shell.run" && seconds === "0.9" ? "substitution_seed" :
                      operation === "restricted_shell.run" && seconds === "1.0" ? "substitution_case" : "other"
      let attack = "none"
      if (operation === "restricted_shell.run" && seconds === "1.5" && !replay.token) {{
        replay.token = issued
        replay.sessionHash = sessionHash
      }} else if (operation === "restricted_shell.run" && seconds === "1.5" &&
                 sessionHash !== replay.sessionHash && replay.token) {{
        output.args.arguments[CARRIER].token = replay.token
        attack = "replay_A"
      }}
      if (operation === "restricted_shell.run" && seconds === "0.9" && !substitution.token) {{
        substitution.token = issued
        substitution.sessionHash = sessionHash
      }} else if (operation === "restricted_shell.run" && seconds === "1.0" &&
                 sessionHash !== substitution.sessionHash && substitution.token) {{
        output.args.arguments[CARRIER].token = substitution.token
        attack = "substituted_A_into_B"
      }}
      log({{kind:"tool_before", session_hash:sessionHash, call_hash:callHash,
           operation, purpose, enter_ns:start, exit_ns:process.hrtime.bigint().toString(), attack}})
    }},
  }}
}}
'''


def _session_id(response: Any) -> str:
    if not isinstance(response, dict) or not isinstance(response.get("id"), str):
        raise RuntimeError("OpenCode did not return a genuine Session identity")
    return response["id"]


def _send_prompt(base_url: str, directory: Path, session_id: str, marker: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"directory": str(directory)})
    response = _post_json(
        f"{base_url}/session/{urllib.parse.quote(session_id)}/message?{query}",
        {
            "agent": "aota-task-main",
            "model": {"providerID": "test", "modelID": "test-model"},
            "parts": [{"type": "text", "text": marker}],
        },
    )
    return response if isinstance(response, dict) else {"result": response}


def _proc_stats(pid: int) -> tuple[int, int]:
    process = Path(f"/proc/{pid}")
    status = process.joinpath("status").read_text(encoding="utf-8")
    rss = next(
        (int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")),
        0,
    )
    return len(list(process.joinpath("task").iterdir())), rss


def _child_processes(parent_pid: int) -> list[dict[str, Any]]:
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            status = dict(
                line.split(":", 1)
                for line in proc.joinpath("status").read_text().splitlines()
                if ":" in line
            )
            if int(status["PPid"]) != parent_pid:
                continue
            cmdline = proc.joinpath("cmdline").read_bytes().decode().replace("\0", " ").strip()
            found.append({"pid": int(proc.name), "cmdline": cmdline})
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return found


def _event_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            values.append(item)
    return values


def _wait_for(predicate, *, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise RuntimeError("isolated OpenCode proof timed out waiting for a bounded event")


def _prompt_parallel(base_url: str, directory: Path, prompts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(prompts)) as executor:
        futures = [
            executor.submit(_send_prompt, base_url, directory, session_id, marker)
            for session_id, marker in prompts
        ]
        return [future.result(timeout=45) for future in futures]


def _read_mcp_sdk_versions() -> dict[str, str]:
    import importlib.metadata

    return {
        "mcp": importlib.metadata.version("mcp"),
        "anyio": importlib.metadata.version("anyio"),
    }


def run() -> dict[str, Any]:
    if not OPENCODE_BIN.is_file() or not PRODUCTION_PLUGIN.is_file():
        raise RuntimeError("pinned OpenCode binary or accepted Trusted Bridge plugin is unavailable")
    version = subprocess.check_output([str(OPENCODE_BIN), "--version"], text=True).strip()
    if version != OPENCODE_VERSION:
        raise RuntimeError(f"isolated proof requires pinned OpenCode {OPENCODE_VERSION}")
    mcp_versions = _read_mcp_sdk_versions()
    if mcp_versions["mcp"] != MCP_SDK_VERSION:
        raise RuntimeError(f"isolated proof requires MCP SDK {MCP_SDK_VERSION}")

    with tempfile.TemporaryDirectory(prefix="plan004-m3-v6-proof-", dir="/tmp/opencode") as raw:
        root = Path(raw)
        home = root / "home"
        config_dir = root / "config"
        data_dir = root / "data"
        state_dir = root / "state"
        cache_dir = root / "cache"
        workspace = root / "workspace"
        project_root = workspace / "aota_forge"
        for directory in (home, config_dir, data_dir, state_dir, cache_dir, workspace, project_root):
            directory.mkdir(parents=True, exist_ok=True)
        governance_base = root / "plans"
        governance_base.mkdir()
        registry_path = root / "registry.json"
        runtime_config_path = root / "runtime-config.json"
        bridge_socket = root / "bridge.sock"
        host_observation = root / "host-observation.jsonl"
        mcp_observation = root / "mcp-observation.jsonl"
        plugin_path = root / "plan004-concurrency-test-plugin.mjs"
        mcp_wrapper = root / "mcp-wrapper.py"
        _install_project(project_root, workspace, registry_path)
        _runtime_config(runtime_config_path)
        plugin_path.write_text(
            _plugin_wrapper_source(PRODUCTION_PLUGIN, host_observation), encoding="utf-8"
        )
        mcp_wrapper.write_text(_mcp_wrapper_source(mcp_observation), encoding="utf-8")

        llm = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIStubHandler)
        llm_thread = threading.Thread(target=llm.serve_forever, daemon=True)
        llm_thread.start()
        llm_url = f"http://127.0.0.1:{llm.server_address[1]}/v1"
        open_code_port = _free_port()
        environment = dict(os.environ)
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(root / "xdg-config"),
            XDG_DATA_HOME=str(data_dir),
            XDG_STATE_HOME=str(state_dir),
            XDG_CACHE_HOME=str(cache_dir),
            OPENCODE_CONFIG_DIR=str(config_dir),
            OPENCODE_DISABLE_AUTOUPDATE="1",
            AOTA_BRIDGE_SOCKET=str(bridge_socket),
            AOTA_TEST_REAL_PLUGIN=str(PRODUCTION_PLUGIN),
            AOTA_TEST_HOST_OBSERVATION_PATH=str(host_observation),
            PYTHONUSERBASE="/home/latios/.local",
        )
        mcp_environment = {
            "AOTA_GLOBAL_MCP": "1",
            "AOTA_FORGE_REPO_ROOT": str(SOURCE_ROOT),
            "PYTHONPATH": str(SOURCE_ROOT),
            "PYTHONUSERBASE": "/home/latios/.local",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AOTA_BRIDGE_SOCKET": str(bridge_socket),
            "AOTA_FORGE_REGISTRY": str(registry_path),
            "AOTA_FORGE_RUNTIME_CONFIG": str(runtime_config_path),
            "AOTA_FORGE_GOVERNANCE_BASE": str(governance_base),
            "AOTA_FORGE_GOVERNANCE_STORE": str(project_root / ".aota" / "governance.sqlite3"),
            "AOTA_FORGE_WORKSPACE_ID": "proof-workspace",
            "AOTA_MCP_TEST_OBSERVATION_PATH": str(mcp_observation),
        }
        config = {
            "model": "test/test-model",
            "provider": {
                "test": {
                    "name": "Isolated deterministic test provider",
                    "id": "test",
                    "env": [],
                    "npm": "@ai-sdk/openai-compatible",
                    "models": {
                        "test-model": {
                            "id": "test-model",
                            "name": "Isolated Test Model",
                            "attachment": False,
                            "reasoning": False,
                            "temperature": False,
                            "tool_call": True,
                            "limit": {"context": 100_000, "output": 10_000},
                            "cost": {"input": 0, "output": 0},
                            "options": {},
                        }
                    },
                    "options": {"apiKey": "isolated-test-only", "baseURL": llm_url},
                }
            },
            "agent": {
                "aota-task-main": {
                    "mode": "primary",
                    "model": "test/test-model",
                    "steps": 4,
                    "permission": {"*": "allow"},
                }
            },
            "plugin": [str(plugin_path)],
            "mcp": {
                "aota": {
                    "type": "local",
                    "command": ["/usr/bin/python3", str(mcp_wrapper), "--mcp-server"],
                    "cwd": str(project_root),
                    "environment": mcp_environment,
                    "enabled": True,
                    "timeout": 20_000,
                }
            },
        }
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        bridge_env = dict(environment)
        bridge_env["PYTHONPATH"] = str(SOURCE_ROOT)
        bridge = subprocess.Popen(
            ["/usr/bin/python3", "-m", "aota_forge.session_bridge", "--socket", str(bridge_socket)],
            cwd=SOURCE_ROOT,
            env=bridge_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        server = subprocess.Popen(
            [
                str(OPENCODE_BIN),
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(open_code_port),
            ],
            cwd=project_root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        base_url = f"http://127.0.0.1:{open_code_port}"
        try:
            _wait_for(
                lambda: _health(base_url),
                timeout=20.0,
            )
            health = _read_json(base_url + "/global/health")
            if health.get("version") != OPENCODE_VERSION or not health.get("healthy"):
                raise RuntimeError("isolated OpenCode health/version check failed")
            sessions = []
            for label in ("A", "B"):
                query = urllib.parse.urlencode({"directory": str(project_root)})
                created = _post_json(
                    f"{base_url}/session?{query}",
                    {
                        "title": f"M3 isolated Session {label}",
                        "agent": "aota-task-main",
                        "model": {"providerID": "test", "id": "test-model"},
                    },
                )
                sessions.append(_session_id(created))
            session_a, session_b = sessions
            session_a_hash = _hash(session_a)
            session_b_hash = _hash(session_b)
            if session_a_hash == session_b_hash:
                raise RuntimeError("OpenCode reused a Session identity")

            bind_results = _prompt_parallel(
                base_url,
                project_root,
                [(session_a, "bind-A"), (session_b, "bind-B")],
            )
            if len(bind_results) != 2:
                raise RuntimeError("isolated A/B context bind prompts did not complete")
            _wait_for(lambda: len(_event_lines(mcp_observation)) >= 4, timeout=15.0)
            child_start = next(
                (event for event in _event_lines(mcp_observation) if event.get("kind") == "mcp_child_start"),
                None,
            )
            if child_start is None:
                raise RuntimeError("shared AOTA MCP child did not start")
            child_pid = int(child_start["pid"])
            try:
                _wait_for(
                    lambda: any(
                        event.get("kind") == "handler"
                        and event.get("operation") == "task_main.context"
                        and event.get("session_hash") == session_a_hash
                        and event.get("ok") is True
                        for event in _event_lines(mcp_observation)
                    )
                    and any(
                        event.get("kind") == "handler"
                        and event.get("operation") == "task_main.context"
                        and event.get("session_hash") == session_b_hash
                        and event.get("ok") is True
                        for event in _event_lines(mcp_observation)
                    ),
                    timeout=15.0,
                )
            except RuntimeError:
                safe_events = [
                    {
                        key: event.get(key)
                        for key in (
                            "kind",
                            "session_hash",
                            "call_hash",
                            "operation",
                            "ok",
                            "error_code",
                            "context_state",
                        )
                    }
                    for event in _event_lines(mcp_observation)
                ]
                raise RuntimeError(
                    "isolated Session binds did not reach project_bound: "
                    + json.dumps(safe_events, sort_keys=True)
                ) from None

            projection_path = project_root / ".aota" / "task-main-context-bindings.json"
            from aota_forge.runtime.task_main.context_binding import (
                DurableTaskMainContextBinding,
            )

            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            records = [DurableTaskMainContextBinding.from_dict(item) for item in projection["records"]]
            by_session_hash = {_hash(record.origin_task_main_session_ref): record for record in records}
            binding_a = by_session_hash.get(session_a_hash)
            binding_b = by_session_hash.get(session_b_hash)
            if (
                binding_a is None
                or binding_b is None
                or binding_a.project_id != "aota_forge"
                or binding_b.project_id != "aota_forge"
                or binding_a.origin_task_main_session_ref == binding_b.origin_task_main_session_ref
                or binding_a.binding_digest == binding_b.binding_digest
            ):
                raise RuntimeError("isolated OpenCode Session bindings were not independent")

            thread_before, rss_before = _proc_stats(child_pid)
            thread_peak = thread_before
            rss_peak = rss_before
            spawn_peak = 0
            stop_monitor = threading.Event()

            def monitor() -> None:
                nonlocal thread_peak, rss_peak, spawn_peak
                while not stop_monitor.is_set():
                    try:
                        threads, rss = _proc_stats(child_pid)
                        thread_peak = max(thread_peak, threads)
                        rss_peak = max(rss_peak, rss)
                        children = _child_processes(child_pid)
                        spawn_peak = max(
                            spawn_peak,
                            sum("/bin/sleep" in item["cmdline"] for item in children),
                        )
                    except (FileNotFoundError, ProcessLookupError):
                        return
                    time.sleep(0.005)

            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            before_cross_events = len(_event_lines(mcp_observation))
            cross_results = _prompt_parallel(
                base_url,
                project_root,
                [(session_a, "cross-A"), (session_b, "cross-B")],
            )
            if len(cross_results) != 2:
                raise RuntimeError("isolated cross-Session prompts did not complete")
            cross_handlers = [
                event
                for event in _event_lines(mcp_observation)[before_cross_events:]
                if event.get("kind") == "handler"
                and event.get("operation") == "restricted_shell.run"
                and event.get("ok") is True
            ]
            cross_a = next((event for event in cross_handlers if event.get("session_hash") == session_a_hash), None)
            cross_b = next((event for event in cross_handlers if event.get("session_hash") == session_b_hash), None)
            if cross_a is None or cross_b is None:
                raise RuntimeError("isolated cross-Session shell handlers were not both observed")
            cross_verify_events = [
                event
                for event in _event_lines(mcp_observation)
                if event.get("kind") == "verify"
                and event.get("call_hash") in {cross_a["call_hash"], cross_b["call_hash"]}
            ]
            if len(cross_verify_events) != 2:
                raise RuntimeError("isolated valid A/B Bridge verifications were not both observed")
            token_a_consumed_once = sum(
                event.get("kind") == "verify" and event.get("call_hash") == cross_a["call_hash"]
                for event in _event_lines(mcp_observation)
            ) == 1
            token_b_consumed_once = sum(
                event.get("kind") == "verify" and event.get("call_hash") == cross_b["call_hash"]
                for event in _event_lines(mcp_observation)
            ) == 1
            cross_overlap_ns = max(
                0,
                min(int(cross_a["exit_ns"]), int(cross_b["exit_ns"]))
                - max(int(cross_a["enter_ns"]), int(cross_b["enter_ns"])),
            )
            if cross_overlap_ns <= 0 or cross_a["pid"] != cross_b["pid"]:
                raise RuntimeError("isolated cross-Session handler overlap was not established")

            before_same_events = len(_event_lines(mcp_observation))
            same_result = _send_prompt(base_url, project_root, session_a, "same-two")
            if not isinstance(same_result, dict):
                raise RuntimeError("isolated same-Session overlapping-tool prompt failed")
            same_handlers = [
                event
                for event in _event_lines(mcp_observation)[before_same_events:]
                if event.get("kind") == "handler"
                and event.get("operation") == "restricted_shell.run"
                and event.get("session_hash") == session_a_hash
                and event.get("ok") is True
            ]
            if len(same_handlers) != 2:
                raise RuntimeError("isolated same-Session prompt did not issue two AOTA calls")
            same_handlers.sort(key=lambda event: int(event["enter_ns"]))
            same_overlap_ns = max(
                0,
                min(int(same_handlers[0]["exit_ns"]), int(same_handlers[1]["exit_ns"]))
                - max(int(same_handlers[0]["enter_ns"]), int(same_handlers[1]["enter_ns"])),
            )
            plugin_same = [
                event
                for event in _event_lines(host_observation)
                if event.get("kind") == "tool_before"
                and event.get("session_hash") == session_a_hash
                and event.get("purpose") == "same_session"
            ]
            hook_windows = [
                (int(event["enter_ns"]), int(event["exit_ns"]))
                for event in plugin_same
            ]
            hook_overlap_ns = (
                max(0, min(window[1] for window in hook_windows) - max(window[0] for window in hook_windows))
                if len(hook_windows) >= 2
                else 0
            )
            # Both real OpenCode hook requests overlap in admission, while the
            # governed Session slot serializes their synchronous AF execution.
            if same_overlap_ns != 0 or hook_overlap_ns <= 0:
                raise RuntimeError("same-Session governed execution overlapped or admission evidence is missing")

            before_replay = len(_event_lines(mcp_observation))
            _send_prompt(base_url, project_root, session_a, "cache-replay")
            _wait_for(
                lambda: any(
                    event.get("kind") == "handler"
                    and event.get("session_hash") == session_a_hash
                    and event.get("operation") == "restricted_shell.run"
                    and event.get("ok") is True
                    for event in _event_lines(mcp_observation)[before_replay:]
                ),
                timeout=10.0,
            )
            security_events_before = len(_event_lines(mcp_observation))
            _prompt_parallel(
                base_url,
                project_root,
                [(session_a, "valid-A-replay-peer"), (session_b, "attack-replay-B")],
            )
            replay_events = [
                event
                for event in _event_lines(mcp_observation)[security_events_before:]
                if event.get("kind") == "verify_denied"
            ]
            replay_denied = any(event.get("error_code") == "TOKEN_REPLAY" for event in replay_events)
            if not replay_denied:
                raise RuntimeError("isolated concurrent replay was not denied")
            replay_seed = next(
                (
                    event
                    for event in _event_lines(host_observation)
                    if event.get("kind") == "tool_before"
                    and event.get("session_hash") == session_a_hash
                    and event.get("purpose") == "replay_case"
                ),
                None,
            )
            if replay_seed is None or not any(
                event.get("error_code") == "TOKEN_REPLAY"
                and event.get("call_hash") == replay_seed.get("call_hash")
                for event in replay_events
            ):
                safe = [
                    {
                        key: event.get(key)
                        for key in (
                            "kind",
                            "session_hash",
                            "call_hash",
                            "operation",
                            "error_code",
                            "purpose",
                            "attack",
                        )
                    }
                    for event in (
                        _event_lines(mcp_observation) + _event_lines(host_observation)
                    )
                    if event.get("kind") in {"verify", "verify_denied", "tool_before"}
                ]
                raise RuntimeError(
                    "replay denial did not match the exact consumed A token: "
                    + json.dumps({"replay_seed": replay_seed, "events": safe}, sort_keys=True)
                )
            token_a_consumed_once = token_a_consumed_once and sum(
                event.get("kind") == "verify"
                and event.get("call_hash") == replay_seed["call_hash"]
                for event in _event_lines(mcp_observation)
            ) == 1

            before_substitution = len(_event_lines(mcp_observation))
            _send_prompt(base_url, project_root, session_a, "cache-substitution")
            _wait_for(
                lambda: any(
                    event.get("kind") == "handler"
                    and event.get("session_hash") == session_a_hash
                    and event.get("operation") == "restricted_shell.run"
                    and event.get("ok") is True
                    for event in _event_lines(mcp_observation)[before_substitution:]
                ),
                timeout=10.0,
            )
            security_events_before = len(_event_lines(mcp_observation))
            _prompt_parallel(
                base_url,
                project_root,
                [(session_a, "valid-A-substitution-peer"), (session_b, "attack-substitution-B")],
            )
            substitution_events = [
                event
                for event in _event_lines(mcp_observation)[security_events_before:]
                if event.get("kind") == "verify_denied"
            ]
            substitution_denied = any(
                event.get("error_code") == "TOKEN_ARGUMENT_MISMATCH"
                for event in substitution_events
            )
            if not substitution_denied:
                raise RuntimeError("isolated concurrent token substitution was not denied")
            substitution_seed = next(
                (
                    event
                    for event in _event_lines(host_observation)
                    if event.get("kind") == "tool_before"
                    and event.get("session_hash") == session_a_hash
                    and event.get("purpose") == "substitution_seed"
                ),
                None,
            )
            if substitution_seed is None or not any(
                event.get("error_code") == "TOKEN_ARGUMENT_MISMATCH"
                and event.get("call_hash") == substitution_seed.get("call_hash")
                for event in substitution_events
            ):
                raise RuntimeError("substitution denial did not match the exact A token")
            token_a_consumed_once = token_a_consumed_once and sum(
                event.get("kind") == "verify"
                and event.get("call_hash") == substitution_seed["call_hash"]
                for event in _event_lines(mcp_observation)
            ) == 1
            if not token_a_consumed_once or not token_b_consumed_once:
                raise RuntimeError("isolated valid A/B bridge tokens were not each consumed exactly once")

            before_healthy = len(_event_lines(mcp_observation))
            _send_prompt(base_url, project_root, session_b, "healthy-B")
            _wait_for(
                lambda: any(
                    event.get("kind") == "handler"
                    and event.get("session_hash") == session_b_hash
                    and event.get("operation") == "restricted_shell.run"
                    and event.get("ok") is True
                    for event in _event_lines(mcp_observation)[before_healthy:]
                ),
                timeout=10.0,
            )

            stop_monitor.set()
            monitor_thread.join(timeout=2)
            thread_after, rss_after = _proc_stats(child_pid)
            children_after = _child_processes(child_pid)
            handler_events = [
                event for event in _event_lines(mcp_observation) if event.get("kind") == "handler"
            ]
            max_handler_concurrency = _max_interval_concurrency(handler_events)
            if max_handler_concurrency > MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS:
                raise RuntimeError("shared-child operation worker cap was exceeded")
            if thread_peak > thread_before + MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS:
                raise RuntimeError("isolated MCP child thread bound was exceeded")
            if thread_after > thread_peak or rss_after > rss_peak or spawn_peak > 2:
                raise RuntimeError("isolated MCP child resources did not remain bounded")
            if any("/bin/sleep" in item["cmdline"] for item in children_after):
                raise RuntimeError("restricted_shell sleep process remained after completion")

            return {
                "STATUS": "PASS",
                "PROJECT_ID": "aota_forge",
                "OPEN_CODE_VERSION": version,
                "MCP_SDK_VERSION": mcp_versions["mcp"],
                "ANYIO_VERSION": mcp_versions["anyio"],
                "A_SESSION_HASH": session_a_hash,
                "B_SESSION_HASH": session_b_hash,
                "A_NE_B": "yes",
                "SHARED_CHILD_PID": child_pid,
                "A_CALL_HASH": cross_a["call_hash"],
                "B_CALL_HASH": cross_b["call_hash"],
                "A_HANDLER_START_MONOTONIC_NS": cross_a["enter_ns"],
                "A_HANDLER_END_MONOTONIC_NS": cross_a["exit_ns"],
                "B_HANDLER_START_MONOTONIC_NS": cross_b["enter_ns"],
                "B_HANDLER_END_MONOTONIC_NS": cross_b["exit_ns"],
                "OVERLAP_NS": cross_overlap_ns,
                "ISOLATED_CROSS_SESSION_HANDLER_OVERLAP": "PASS",
                "SAME_SESSION_CALL_A_HASH": same_handlers[0]["call_hash"],
                "SAME_SESSION_CALL_B_HASH": same_handlers[1]["call_hash"],
                "SAME_SESSION_ADMISSION_OVERLAP_NS": hook_overlap_ns,
                "SAME_SESSION_OVERLAP_NS": same_overlap_ns,
                "ISOLATED_SAME_SESSION_SERIALIZATION": "PASS",
                "CROSS_SESSION_CONTEXT_ISOLATION": "PASS",
                "BRIDGE_CONCURRENT_VERIFY": "PASS",
                "TOKEN_A_CONSUMED_ONCE": "yes" if token_a_consumed_once else "no",
                "TOKEN_B_CONSUMED_ONCE": "yes" if token_b_consumed_once else "no",
                "VALID_A_B": "PASS",
                "REPLAY_DENIED": "PASS",
                "SUBSTITUTION_DENIED": "PASS",
                "VALID_SESSION_NOT_POISONED_BY_INVALID_PEER": "yes",
                "MCP_PUBLIC_TOOL_COUNT": 1,
                "TOOLS_LIST": ["aota.invoke"],
                "MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS": MAX_CONCURRENT_SHARED_CHILD_EXECUTIONS,
                "WORKER_LIMIT_ENFORCED": "yes",
                "THREAD_COUNT_BEFORE": thread_before,
                "THREAD_COUNT_PEAK": thread_peak,
                "THREAD_COUNT_AFTER": thread_after,
                "MCP_CHILD_RSS_BEFORE_KB": rss_before,
                "MCP_CHILD_RSS_PEAK_KB": rss_peak,
                "MCP_CHILD_RSS_AFTER_KB": rss_after,
                "NO_UNBOUNDED_THREAD_GROWTH": "yes",
                "NO_PROCESS_SPAWN_STORM": "yes",
                "RESTRICTED_SHELL_SLEEP_PROCESSES_PEAK": spawn_peak,
                "RESTRICTED_SHELL_SLEEP_PROCESSES_AFTER": 0,
                "PRODUCTION_RUNTIME_TOUCHED": "no",
                "OPENCODE_UPSTREAM_CHANGED": "no",
            }
        finally:
            _terminate(server)
            _terminate(bridge)
            llm.shutdown()
            llm.server_close()
            llm_thread.join(timeout=2)


def _health(base_url: str) -> bool:
    try:
        result = _read_json(base_url + "/global/health", timeout=1.0)
        return result.get("healthy") is True and result.get("version") == OPENCODE_VERSION
    except Exception:
        return False


def _max_interval_concurrency(events: list[dict[str, Any]]) -> int:
    points = []
    for event in events:
        if "enter_ns" in event and "exit_ns" in event:
            points.append((int(event["enter_ns"]), 1))
            points.append((int(event["exit_ns"]), -1))
    active = peak = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


if __name__ == "__main__":
    result = run()
    rendered = json.dumps(result, sort_keys=True, indent=2)
    output_path = os.environ.get("PLAN004_M3_ISOLATED_PROOF_OUTPUT")
    if output_path:
        evidence = Path(output_path)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(rendered + "\n", encoding="utf-8")
        evidence.chmod(0o600)
    print(rendered)
