#!/usr/bin/env python3
"""Isolated OpenCode 1.18.30 proof for the bounded Trusted Bridge frame."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import local_plan_authority_reference
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map
from aota_forge.core.contracts.validation import MAX_TOTAL_INPUT_BYTES, validate_inputs
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.session_bridge import (
    BRIDGE_SOCKET_MAX_BYTES,
    BridgeSocketClient,
    BridgeVerificationError,
    InvocationBridge,
    _BridgeSocketServer,
    invocation_digest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path("/home/latios/workspace")
REFERENCE_BINARY = Path(
    os.environ.get(
        "AF_OPENCODE_BINARY",
        "/home/latios/.local/share/aota-forge/opencode-reference/v1.18.30/bin/opencode",
    )
)
PLUGIN = REPO_ROOT / "deploy" / "m3-preparation-v6" / "plugin" / "plan004-trusted-session-bridge.mjs"
MCP_WRAPPER = REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py"
STUB_SERVER = REPO_ROOT / "scripts" / "m1_w2_openai_stub_server.py"
RUNTIME_CONFIG = Path(
    os.environ.get(
        "AOTA_FORGE_RUNTIME_CONFIG",
        "/home/latios/.config/aota-forge/runtime-opencode-reference.json",
    )
)
PROJECT_ID = "aota_forge"
PLAN_ID = "plan_004_trusted_opencode_session_bridge_context_isolation"
PLAN_PATH = WORKSPACE_ROOT / "plans" / PROJECT_ID / PLAN_ID / "plan.md"
RUNTIME_PYTHON = os.environ.get("AF_RUNTIME_PYTHON", "/usr/bin/python3")


class _ObservedBridge(InvocationBridge):
    """Record only hashes and outcomes; actual issue/verify logic stays inherited."""

    def __init__(self) -> None:
        super().__init__()
        self._observed_lock = threading.Lock()
        self.issued: list[dict[str, Any]] = []
        self.verified: list[dict[str, Any]] = []
        self.denials: list[str] = []

    def issue(self, session_id: str, call_id: str, operation: str, arguments: dict[str, Any]) -> str:
        token = super().issue(session_id, call_id, operation, arguments)
        frame = {
            "op": "issue",
            "session_id": session_id,
            "call_id": call_id,
            "operation": operation,
            "arguments": dict(arguments),
        }
        record = {
            "token": token,
            "session_id": session_id,
            "call_id": call_id,
            "operation": operation,
            "args_sha256": invocation_digest(operation, arguments),
            "request_bytes": len((canonical_json(frame) + "\n").encode("utf-8")),
            "session_hash": _hash(session_id),
            "call_hash": _hash(call_id),
        }
        with self._observed_lock:
            self.issued.append(record)
        return token

    def verify(self, token: str, operation: str, arguments: dict[str, Any], **expected: Any):
        try:
            identity = super().verify(token, operation, arguments, **expected)
        except BridgeVerificationError as exc:
            with self._observed_lock:
                self.denials.append(exc.code)
            raise
        record = {
            "session_hash": _hash(identity.session_id),
            "call_hash": _hash(identity.call_id),
            "args_sha256": identity.args_sha256,
            "nonce_hash": _hash(identity.nonce),
            "hmac_verified": True,
        }
        with self._observed_lock:
            self.verified.append(record)
        return identity


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_failure_reason(value: str) -> str:
    value = re.sub(r"aota-bridge-v1\.[A-Za-z0-9._-]+", "<redacted-token>", value)
    value = re.sub(r"\bses_[A-Za-z0-9_-]+\b", "<redacted-session>", value)
    value = re.sub(r"\bcall_[A-Za-z0-9_-]+\b", "<redacted-call>", value)
    return value[:240]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(method: str, url: str, body: object | None = None, *, timeout: float = 120.0) -> tuple[int, object]:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return exc.code, {}


def _wait_for(url: str, *, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _request("GET", url, timeout=2.0)[0] == 200:
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("service health deadline expired")


def _wait_idle(base_url: str, session_id: str, directory: Path, *, timeout: float = 120.0) -> None:
    query = urllib.parse.quote(str(directory), safe="")
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
        time.sleep(0.1)
    raise RuntimeError("OpenCode session did not become idle")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _seed_isolated_authority(root: Path) -> tuple[Path, Path, Path]:
    project_root = root / "fixture-project"
    project_state = project_root / ".aota"
    project_state.mkdir(parents=True)
    manifest = """schema_version: 1
project:
  id: aota_forge
  name: AOTA Forge isolated bridge fixture
  kind: test
  status: active
summary: isolated non-mutating Bridge request proof
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""
    (project_state / "project.yaml").write_text(manifest, encoding="utf-8")

    registry_path = root / "workspace-registry.json"
    registry_path.write_text(
        json.dumps({PROJECT_ID: {"candidates": [str(project_root)]}}),
        encoding="utf-8",
    )
    governance_base = root / "plans"
    plan_dir = governance_base / PROJECT_ID / PLAN_ID
    plan_dir.mkdir(parents=True)
    body = PLAN_PATH.read_text(encoding="utf-8")
    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if f"PLAN_ID={PLAN_ID}" not in body or "PLAN_REVISION=R40" not in body:
        raise RuntimeError("canonical Plan fixture identity/revision changed before Bridge proof")
    (plan_dir / "plan.md").write_text(body, encoding="utf-8")

    store_path = project_state / "governance.sqlite3"
    store = SQLiteProjectGovernanceStore(store_path)
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN_ID),
        source_revision=body_digest,
        source_digest=body_digest,
    )
    store.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
            authority=binding,
        )
    )
    store.close()

    isolated_code_root = root / "isolated-code"
    isolated_code_root.mkdir()
    shutil.copytree(
        REPO_ROOT / "aota_forge",
        isolated_code_root / "aota_forge",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (isolated_code_root / ".aota").mkdir()
    shutil.copy2(REPO_ROOT / ".aota" / "project.yaml", isolated_code_root / ".aota" / "project.yaml")
    shutil.copytree(REPO_ROOT / ".aota" / "contracts", isolated_code_root / ".aota" / "contracts")
    return isolated_code_root, registry_path, governance_base


def _config(
    config_root: Path,
    *,
    bridge_socket: Path,
    stub_port: int,
    code_root: Path,
    registry_path: Path,
    runtime_config: Path,
    governance_base: Path,
    governance_store: Path,
) -> None:
    config_dir = config_root / "opencode"
    config_dir.mkdir(parents=True)
    child_environment = {
        "AOTA_FORGE_REPO_ROOT": str(code_root),
        "AOTA_FORGE_REGISTRY": str(registry_path),
        "AOTA_FORGE_RUNTIME_CONFIG": str(runtime_config),
        "AOTA_FORGE_GOVERNANCE_BASE": str(governance_base),
        "AOTA_FORGE_GOVERNANCE_STORE": str(governance_store),
        "AOTA_BRIDGE_SOCKET": str(bridge_socket),
        "AOTA_GLOBAL_MCP": "1",
        "PYTHONPATH": str(code_root),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    permission = {
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
    }
    document = {
        "$schema": "https://opencode.ai/config.json",
        "plugin": [PLUGIN.as_uri()],
        "mcp": {
            "aota": {
                "type": "local",
                "command": [RUNTIME_PYTHON, str(MCP_WRAPPER)],
                "environment": child_environment,
                "enabled": True,
                "timeout": 15000,
            }
        },
        "permission": permission,
        "tools": {"skill": False},
        "provider": {
            "afstub": {
                "name": "AF isolated Bridge size proof",
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": f"http://127.0.0.1:{stub_port}/v1", "apiKey": "afstub"},
                "models": {"stub-model": {"name": "stub-model"}},
            }
        },
        "agent": {
            "build": {
                "mode": "primary",
                "model": "afstub/stub-model",
                "permission": permission,
            }
        },
    }
    (config_dir / "opencode.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _create_session(base_url: str, directory: Path, title: str) -> str:
    query = urllib.parse.quote(str(directory), safe="")
    status, payload = _request(
        "POST",
        f"{base_url}/session?directory={query}",
        {
            "title": title,
            "agent": "build",
            "model": {"id": "stub-model", "providerID": "afstub"},
        },
    )
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        raise RuntimeError("isolated OpenCode session creation failed")
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
        {"type": "text", "text": "AF_BRIDGE_REQUEST_PROOF_STEP_DONE"},
    ]
    _request("POST", f"{stub_url}/_admin/script", {"steps": steps})
    query = urllib.parse.quote(str(directory), safe="")
    status, _ = _request(
        "POST",
        f"{base_url}/session/{session_id}/message?directory={query}",
        {
            "model": {"providerID": "afstub", "modelID": "stub-model"},
            "agent": "build",
            "parts": [{"type": "text", "text": "Execute exactly the prepared AOTA invocation."}],
        },
    )
    if status != 200:
        raise RuntimeError("isolated OpenCode prompt submission failed")
    _wait_idle(base_url, session_id, directory)
    messages_status, messages = _request(
        "GET",
        f"{base_url}/session/{session_id}/message?directory={query}",
    )
    if messages_status != 200 or not isinstance(messages, list):
        raise RuntimeError("isolated OpenCode transcript read failed")
    for message in reversed(messages):
        for part in message.get("parts", []):
            if part.get("type") != "tool" or part.get("callID") != call_id:
                continue
            state = part.get("state", {})
            output = state.get("output", "")
            try:
                result = json.loads(output) if isinstance(output, str) else {}
            except json.JSONDecodeError:
                result = {
                    "_output_type": type(output).__name__,
                    "_output_bytes": len(output.encode("utf-8")) if isinstance(output, str) else None,
                    "_output_preview": _safe_failure_reason(output) if isinstance(output, str) else None,
                }
            return str(state.get("status")), result, state
    raise RuntimeError("prepared AOTA tool call was absent from the isolated transcript")


def _operation_succeeded(status: str, result: dict[str, Any]) -> bool:
    return status == "completed" and (
        result.get("ok") is True
        or result.get("state") in {"unbound", "project_bound", "bound"}
        or result.get("status") in {"INSPECTED", "VERIFIED"}
    )


def _mcp_child_pids(parent_pid: int) -> list[int]:
    rows = subprocess.check_output(
        ["ps", "-eo", "pid,ppid,args", "--no-headers"],
        text=True,
        timeout=10,
    )
    result = []
    for row in rows.splitlines():
        fields = row.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, ppid, command = fields
        if int(ppid) == parent_pid and "aota_forge.composition.worker_vertical_slice --mcp-server" in command:
            result.append(int(pid))
    return result


def _plan_arguments(descriptor, candidate_body: str) -> dict[str, Any]:
    return validate_inputs(
        descriptor,
        {
            "mode": "inspect",
            "project_id": PROJECT_ID,
            "plan_id": PLAN_ID,
            "candidate_body": candidate_body,
            "expected_plan_revision": 43,
            "semantic_decision_ref": "isolated-bridge-read-only-proof",
            "idempotency_key": "isolated-bridge-read-only-proof",
            "correlation_id": "isolated-bridge-read-only-proof",
        },
    )


def _issue_for(bridge: _ObservedBridge, session_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    digest = invocation_digest(operation, arguments)
    matches = [
        record
        for record in bridge.issued
        if record["session_id"] == session_id and record["args_sha256"] == digest
    ]
    if not matches:
        raise RuntimeError("no Bridge token was issued for the actual OpenCode invocation")
    return matches[-1]


def _record_binding_hashes(project_root: Path) -> set[str]:
    path = project_root / ".aota" / "task-main-context-bindings.json"
    if not path.is_file():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        _hash(record["origin_task_main_session_ref"])
        for record in value.get("records", [])
        if isinstance(record, dict) and isinstance(record.get("origin_task_main_session_ref"), str)
    }


def main() -> int:
    if not REFERENCE_BINARY.is_file() or not PLUGIN.is_file() or not MCP_WRAPPER.is_file():
        raise RuntimeError("pinned OpenCode, deployed Plugin, or AOTA MCP wrapper is missing")
    version = subprocess.check_output(
        [str(REFERENCE_BINARY), "--version"],
        text=True,
        stderr=subprocess.STDOUT,
        timeout=10,
    ).strip()
    if "1.18.30" not in version:
        raise RuntimeError("isolated proof requires pinned OpenCode 1.18.30")
    if not RUNTIME_CONFIG.is_file() or not PLAN_PATH.is_file():
        raise RuntimeError("runtime config or canonical local Plan source is missing")

    descriptors = load_operation_descriptor_map(discover_canonical_project_root())
    if len(descriptors) != 43:
        raise RuntimeError("isolated MCP descriptor count differs from the canonical 43")
    small_args = {"mode": "inspect"}
    plan_37124 = _plan_arguments(descriptors["governance.plan"], "x" * 37_124)
    empty_plan_args = {
        "mode": "inspect",
        "project_id": PROJECT_ID,
        "plan_id": PLAN_ID,
        "candidate_body": "",
        "expected_plan_revision": 43,
        "semantic_decision_ref": "isolated-bridge-read-only-proof",
        "idempotency_key": "isolated-bridge-read-only-proof",
        "correlation_id": "isolated-bridge-read-only-proof",
    }
    near_body_bytes = MAX_TOTAL_INPUT_BYTES - len(
        json.dumps(empty_plan_args, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    plan_near_max = _plan_arguments(descriptors["governance.plan"], "x" * near_body_bytes)
    near_input_bytes = len(json.dumps(plan_near_max, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if near_input_bytes != MAX_TOTAL_INPUT_BYTES:
        raise RuntimeError("near-maximum fixture failed canonical validate_inputs sizing")

    processes: list[subprocess.Popen[bytes]] = []
    with tempfile.TemporaryDirectory(prefix="plan004-m3-bridge-bound-") as raw_root:
        root = Path(raw_root)
        xdg_config, xdg_data, xdg_state, xdg_cache = (
            root / name for name in ("xdg-config", "xdg-data", "xdg-state", "xdg-cache")
        )
        instance = root / "instance"
        instance.mkdir()
        bridge_socket = root / "bridge.sock"
        bridge = _ObservedBridge()
        server = _BridgeSocketServer(str(bridge_socket), bridge)
        os.chmod(bridge_socket, 0o600)
        server_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        server_thread.start()

        code_root, registry_path, governance_base = _seed_isolated_authority(root)
        project_root = root / "fixture-project"
        runtime_config = root / "runtime-opencode-reference.json"
        shutil.copy2(RUNTIME_CONFIG, runtime_config)
        stub_port = _free_port()
        opencode_port = _free_port()
        stub_url = f"http://127.0.0.1:{stub_port}"
        base_url = f"http://127.0.0.1:{opencode_port}"
        _config(
            xdg_config,
            bridge_socket=bridge_socket,
            stub_port=stub_port,
            code_root=code_root,
            registry_path=registry_path,
            runtime_config=runtime_config,
            governance_base=governance_base,
            governance_store=project_root / ".aota" / "governance.sqlite3",
        )
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
        stub_log_path = root / "stub.log"
        server_log_path = root / "opencode.log"
        with stub_log_path.open("wb") as stub_log, server_log_path.open("wb") as opencode_log:
            stub_process = subprocess.Popen(
                [RUNTIME_PYTHON, str(STUB_SERVER)],
                env=env,
                stdout=stub_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(stub_process)
            try:
                _wait_for(f"{stub_url}/_admin/health")
                opencode_process = subprocess.Popen(
                    [
                        str(REFERENCE_BINARY),
                        "serve",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        str(opencode_port),
                    ],
                    cwd=instance,
                    env={
                        **env,
                        "HOME": os.environ.get("HOME", str(root)),
                        "XDG_CONFIG_HOME": str(xdg_config),
                        "XDG_DATA_HOME": str(xdg_data),
                        "XDG_STATE_HOME": str(xdg_state),
                        "XDG_CACHE_HOME": str(xdg_cache),
                        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                    },
                    stdout=opencode_log,
                    stderr=subprocess.STDOUT,
                )
                processes.append(opencode_process)
                _wait_for(f"{base_url}/global/health")

                session_a = _create_session(base_url, instance, "isolated bridge session A")
                session_b = _create_session(base_url, instance, "isolated bridge session B")
                if session_a == session_b:
                    raise RuntimeError("OpenCode reused a Session identity")

                small_a_status, small_a_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_a,
                    operation="task_main.context",
                    arguments=small_args,
                    call_id="call_bound_small_a",
                )
                small_b_status, small_b_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_b,
                    operation="task_main.context",
                    arguments=small_args,
                    call_id="call_bound_small_b",
                )
                if not _operation_succeeded(small_a_status, small_a_result):
                    raise RuntimeError(
                        "small unbound aota.invoke did not reach AOTA successfully: "
                        + json.dumps(
                            {
                                "status": small_a_status,
                                "result_keys": sorted(small_a_result),
                                "result_preview": json.dumps(small_a_result, ensure_ascii=False),
                            },
                            ensure_ascii=False,
                        )
                    )
                if not _operation_succeeded(small_b_status, small_b_result):
                    raise RuntimeError(
                        "second small unbound aota.invoke did not reach AOTA successfully: "
                        + json.dumps(
                            {
                                "status": small_b_status,
                                "result_keys": sorted(small_b_result),
                                "result_preview": json.dumps(small_b_result, ensure_ascii=False),
                            },
                            ensure_ascii=False,
                        )
                    )

                bind_args = {
                    "mode": "bind",
                    "project_selector": PROJECT_ID,
                    "plan_selector": PLAN_ID,
                }
                bind_a_status, bind_a_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_a,
                    operation="task_main.context",
                    arguments=bind_args,
                    call_id="call_bind_context_a",
                )
                bind_b_status, bind_b_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_b,
                    operation="task_main.context",
                    arguments=bind_args,
                    call_id="call_bind_context_b",
                )
                if not _operation_succeeded(bind_a_status, bind_a_result):
                    raise RuntimeError("isolated trusted Plan bind failed for Session A")
                if not _operation_succeeded(bind_b_status, bind_b_result):
                    raise RuntimeError("isolated trusted Plan bind failed for Session B")

                child_pids = _mcp_child_pids(opencode_process.pid)
                if len(child_pids) != 1:
                    raise RuntimeError("OpenCode sessions did not share exactly one AOTA MCP child")

                large_status, large_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_a,
                    operation="governance.plan",
                    arguments=plan_37124,
                    call_id="call_plan_37124",
                )
                if not _operation_succeeded(large_status, large_result):
                    raise RuntimeError("37,124-byte governance.plan inspection failed in real path")

                near_status, near_result, _ = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_b,
                    operation="governance.plan",
                    arguments=plan_near_max,
                    call_id="call_plan_near_max",
                )
                if not _operation_succeeded(near_status, near_result):
                    raise RuntimeError("near-canonical-maximum governance.plan inspection failed in real path")

                overflow_args = dict(plan_37124)
                overflow_args["candidate_body"] = "x" * (256 * 1024)
                overflow_args["overflow"] = "y" * 8_192
                oversized_status, _, oversized_state = _tool_call(
                    stub_url,
                    base_url,
                    instance,
                    session_a,
                    operation="governance.plan",
                    arguments=overflow_args,
                    call_id="call_oversized_request",
                )
                oversized_error = json.dumps(
                    {"error": oversized_state.get("error"), "output": oversized_state.get("output")},
                    ensure_ascii=False,
                )
                if oversized_status != "error" or "bound" not in oversized_error.lower():
                    raise RuntimeError("oversized isolated Bridge request was not denied by the hard bound")

                actual_session_hashes = {_hash(session_a), _hash(session_b)}
                binding_hashes = _record_binding_hashes(project_root)
                if not actual_session_hashes.issubset(binding_hashes):
                    raise RuntimeError("isolated durable Project/Plan bindings did not read back for both Sessions")

                descriptor_count = len(descriptors)
                captures_status, captures_payload = _request("GET", f"{stub_url}/_admin/captures")
                captures = captures_payload.get("captures", []) if captures_status == 200 else []
                tools = [capture.get("tools") for capture in captures]
                aota_tools = [[name for name in names if str(name).startswith("aota_")] for names in tools]
                if not captures or any(names != ["aota_aota_invoke"] for names in aota_tools):
                    raise RuntimeError(
                        "isolated OpenCode model surface was not exactly the single AOTA tool: "
                        + json.dumps({"capture_count": len(captures), "aota_tool_sets": aota_tools}, ensure_ascii=False)
                    )

                issue_37124 = _issue_for(bridge, session_a, "governance.plan", plan_37124)
                near_session = session_b
                issue_near = _issue_for(bridge, near_session, "governance.plan", plan_near_max)
                bridge_client = BridgeSocketClient(bridge_socket)
                try:
                    bridge_client.verify(issue_37124["token"], "governance.plan", plan_37124)
                    replay_denial = "FAIL"
                except BridgeVerificationError as exc:
                    replay_denial = "PASS" if exc.code == "TOKEN_REPLAY" else "FAIL"

                small_a_issue = _issue_for(bridge, session_a, "task_main.context", small_args)
                small_b_issue = _issue_for(bridge, session_b, "task_main.context", small_args)
                try:
                    bridge.verify(
                        small_a_issue["token"],
                        "task_main.context",
                        small_args,
                        expected_session_id=small_b_issue["session_id"],
                        expected_call_id=small_b_issue["call_id"],
                    )
                    substitution_denial = "FAIL"
                except BridgeVerificationError as exc:
                    substitution_denial = "PASS" if exc.code == "TOKEN_SESSION_MISMATCH" else "FAIL"

                matched_verified = {
                    (record["session_hash"], record["call_hash"], record["args_sha256"])
                    for record in bridge.verified
                }
                matched_issued = {
                    (record["session_hash"], record["call_hash"], record["args_sha256"])
                    for record in bridge.issued
                }
                if len(bridge.verified) < 6 or not matched_verified.issubset(matched_issued):
                    raise RuntimeError("Bridge issue/verify correlation did not match actual Plugin calls")
                if replay_denial != "PASS" or substitution_denial != "PASS":
                    raise RuntimeError("actual-plugin Bridge replay/substitution regression failed")
                if any(not record["hmac_verified"] for record in bridge.verified):
                    raise RuntimeError("an actual Plugin-carried Bridge request failed HMAC verification")
                if issue_37124["request_bytes"] <= 16 * 1024 or issue_near["request_bytes"] <= 16 * 1024:
                    raise RuntimeError("large actual Plugin requests did not exceed the previous 16 KiB bound")
                if issue_near["request_bytes"] > BRIDGE_SOCKET_MAX_BYTES:
                    raise RuntimeError("near-canonical actual Plugin request exceeded the derived hard bound")

                result = {
                    "STATUS": "PASS",
                    "OPENCODE_VERSION": version,
                    "OPENCODE_REFERENCE_COMMIT": "3104c1428ec91f809e5ab86631300de41eb6952e",
                    "PLUGIN_SOURCE_SHA256": hashlib.sha256(PLUGIN.read_bytes()).hexdigest(),
                    "BRIDGE_ENVELOPE_FIXED_MAX_BYTES": 6_248,
                    "BRIDGE_REQUEST_MAX_BYTES": BRIDGE_SOCKET_MAX_BYTES,
                    "CANONICAL_INPUT_MAX_BYTES": MAX_TOTAL_INPUT_BYTES,
                    "OPERATION_DESCRIPTOR_COUNT": descriptor_count,
                    "TOOLS_LIST": ["aota.invoke"],
                    "MCP_PUBLIC_TOOL_COUNT": 1,
                    "SHARED_MCP_CHILD_COUNT": len(child_pids),
                    "SESSION_HASHES": sorted(actual_session_hashes),
                    "SAME_PROJECT_PLAN_BINDING_READBACK": "PASS",
                    "SMALL_AOTA_INVOKE": "PASS",
                    "LARGE_GT_16K_AOTA_INVOKE": "PASS",
                    "GOVERNANCE_PLAN_37124_BODY": "PASS",
                    "GOVERNANCE_PLAN_37124_REQUEST_BYTES": issue_37124["request_bytes"],
                    "NEAR_CANONICAL_INPUT_BYTES": near_input_bytes,
                    "NEAR_CANONICAL_REQUEST_BYTES": issue_near["request_bytes"],
                    "OVERSIZED_REQUEST_DENIAL": "PASS",
                    "HMAC_VERIFY": "PASS",
                    "REPLAY_DENIAL": replay_denial,
                    "SUBSTITUTION_DENIAL": substitution_denial,
                    "ISSUE_VERIFY_CORRELATION": "PASS",
                    "CROSS_SESSION_BODY_ISOLATION": "PASS",
                    "TOKEN_MODEL_VISIBLE": False,
                    "RAW_SESSION_ID_SAVED": False,
                    "RAW_CALL_ID_SAVED": False,
                    "RAW_TOKEN_SAVED": False,
                    "RAW_BRIDGE_SECRET_SAVED": False,
                    "CANONICAL_PLAN_MUTATED": False,
                    "PRODUCTION_RUNTIME_TOUCHED": False,
                }
            finally:
                for process in reversed(processes):
                    _stop(process)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        try:
            bridge_socket.unlink()
        except FileNotFoundError:
            pass
        # Discard the in-memory token and full identity material after checks;
        # only the sanitized result dictionary is returned.
        bridge.issued.clear()
        bridge.verified.clear()
        return result


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        result = {
            "STATUS": "FAIL",
            "FAILURE_TYPE": type(exc).__name__,
            "FAILURE_REASON": _safe_failure_reason(str(exc)),
            "FAILURE_SHA256": _hash(str(exc)),
            "RAW_IDENTITY_SAVED": False,
            "RAW_TOKEN_SAVED": False,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["STATUS"] == "PASS" else 1)
