#!/usr/bin/env python3
"""Isolated real 1.18.30 Plugin -> Bridge -> shared MCP lifecycle proof.

The deterministic provider schedules tool calls only; OpenCode executes the
real Plugin and MCP transport. Project/Plan owners, XDG state and process groups
are isolated. No production Session or production projection is used.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import plan004_m2_real_opencode_mcp_integration as host

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeService
from aota_forge.runtime.task_main.context_binding import FileBackedTaskMainContextBindingProjection

PLAN = "plan_004_trusted_opencode_session_bridge_context_isolation"

def stop_group(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)

def main():
    binary_version = subprocess.check_output([str(host.REFERENCE_BINARY), "--version"], text=True).strip()
    assert binary_version == "1.18.30", binary_version
    with tempfile.TemporaryDirectory(prefix="plan004-v5-recovery-", dir="/tmp/opencode") as temporary:
        root = Path(temporary)
        project = root / "workspace" / "aota_forge"
        (project / ".aota").mkdir(parents=True)
        (project / ".aota/project.yaml").write_bytes((Path("/home/latios/workspace/aota_forge/.aota/project.yaml")).read_bytes())
        registry = root / "registry.json"
        registry.write_text(json.dumps({"aota_forge": {"candidates": [str(project)]}}))
        plans = root / "plans"
        plans.mkdir()
        store = project / ".aota/governance.sqlite3"
        service = GovernanceIntakeService(GovernanceIntakeContext(
            workspace_id="aota_forge", governance_base=plans, registry_path=registry,
            workspace_root=project, project_root=project, governance_store_path=store,
            journal_path=project / ".aota/governance-journal.json",
        ))
        # Copy current semantics as an isolated authority fixture via canonical
        # initialization, never by direct plan.md/store/journal writes.
        body = (Path("/home/latios/workspace/plans/aota_forge") / PLAN / "plan.md").read_text()
        service.plan("init", project_id="aota_forge", plan_id=PLAN, candidate_body=body,
                     semantic_decision_ref="plan004-v5-isolated-proof-fixture")
        host.REGISTRY, host.GOVERNANCE_BASE, host.GOVERNANCE_STORE = registry, plans, store
        host.PLUGIN = REPO / "deploy/m3-preparation-v4/plugin/plan004-trusted-session-bridge.mjs"
        xdg_config = root / "config"
        bridge_socket = root / "bridge.sock"
        bridge_evidence = root / "bridge-evidence.jsonl"
        mcp_debug = root / "mcp-debug.jsonl"
        stub_port, server_port = host._free_port(), host._free_port()
        host._config(xdg_config, bridge_socket, stub_port, mcp_debug)
        env = dict(os.environ)
        env.pop("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", None)
        env.update({
            "PYTHONPATH": str(REPO), "AF_STUB_PORT": str(stub_port), "AF_STUB_HOST": "127.0.0.1",
            "AOTA_BRIDGE_SOCKET": str(bridge_socket), "AOTA_BRIDGE_EVIDENCE_PATH": str(bridge_evidence),
        })
        server_env = {**env, "XDG_CONFIG_HOME": str(xdg_config), "XDG_DATA_HOME": str(root / "data"),
                      "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache"),
                      "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}
        server = bridge = stub = None
        logs = []
        def spawn(args, name, environment=env, cwd=None):
            log = (root / f"{name}.log").open("wb")
            logs.append(log)
            return subprocess.Popen(args, env=environment, cwd=cwd, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        def start_bridge():
            process = spawn([host.RUNTIME_PYTHON, "-m", "aota_forge.session_bridge", "--socket", str(bridge_socket)], "bridge")
            from aota_forge.session_bridge import BridgeSocketClient, BridgeVerificationError
            client = BridgeSocketClient(bridge_socket)
            for _ in range(200):
                try:
                    epoch = client.rotate()
                    return process, epoch
                except BridgeVerificationError:
                    if process.poll() is not None:
                        raise RuntimeError("isolated Bridge failed to start")
                    time.sleep(0.05)
            raise RuntimeError("isolated Bridge startup timed out")
        base = f"http://127.0.0.1:{server_port}"
        query = urllib.parse.quote(str(project))
        def start_server():
            process = spawn([str(host.REFERENCE_BINARY), "serve", "--hostname", "127.0.0.1", "--port", str(server_port)],
                            "opencode", server_env, project)
            host._wait_for(f"{base}/global/health", timeout=45)
            status, payload = host._request("GET", f"{base}/mcp?directory={query}")
            assert status == 200 and payload["aota"]["status"] == "connected", payload
            return process
        sequence = 0
        outputs = []
        def invoke(session, operation, arguments, *, expected_error=None):
            nonlocal sequence
            sequence += 1
            call_id = f"call_v5_{sequence}"
            host._request("POST", f"http://127.0.0.1:{stub_port}/_admin/script", {"steps": [
                {"type": "tool_call", "name": "aota_aota_invoke", "call_id": call_id,
                 "arguments": {"operation": operation, "arguments": arguments}},
                {"type": "text", "text": f"V5_STAGE_{sequence}_DONE"},
            ]})
            status, response = host._request("POST", f"{base}/session/{session}/message?directory={query}", {
                "model": {"providerID": "afstub", "modelID": "stub-model"}, "agent": "build",
                "parts": [{"type": "text", "text": f"Execute bounded durability proof stage {sequence}."}],
            }, timeout=120)
            assert status == 200, response
            assert "aota-bridge-v1." not in json.dumps(response), "token visible in model output"
            parts = response.get("parts", [])
            selected = [part for part in parts if part.get("type") == "tool" and part.get("callID") == call_id]
            if not selected:
                messages = host._request("GET", f"{base}/session/{session}/message?directory={query}")[1]
                selected = [part for message in messages for part in message.get("parts", [])
                            if part.get("type") == "tool" and part.get("callID") == call_id]
            assert len(selected) == 1, {"stage": sequence, "response": response}
            state = selected[0]["state"]
            assert state["status"] == "completed", state
            envelope = json.loads(state["output"])
            if expected_error is not None:
                assert envelope.get("ok") is False and envelope.get("error_code") == expected_error, envelope
                return envelope
            assert envelope.get("ok") is not False, envelope
            outputs.append({"call_id": call_id, "session": session, "operation": operation,
                            "arguments": arguments, "result": envelope})
            if isinstance(envelope.get("payload"), dict):
                return envelope["payload"]
            if envelope.get("output_mode") != "by_ref":
                return envelope
            hydrated = invoke(session, "result.hydrate", envelope["hydration"]["arguments"])
            content = hydrated.get("content")
            return json.loads(content) if isinstance(content, str) else hydrated
        projection = FileBackedTaskMainContextBindingProjection(project / ".aota/task-main-context-bindings.json")
        try:
            stub = spawn([host.RUNTIME_PYTHON, str(REPO / "scripts/m1_w2_openai_stub_server.py")], "stub")
            host._wait_for(f"http://127.0.0.1:{stub_port}/_admin/health")
            bridge, epoch1 = start_bridge()
            tools = host._probe_stdio_tools({**env, "AOTA_FORGE_REPO_ROOT": str(REPO),
                "AOTA_FORGE_REGISTRY": str(registry), "AOTA_FORGE_RUNTIME_CONFIG": str(host.RUNTIME_CONFIG),
                "AOTA_GLOBAL_MCP": "1"})
            assert tools == ["aota.invoke"]
            server = start_server()
            sessions = []
            for title in ("v5-project-recovery-A", "v5-unbound-B"):
                status, created = host._request("POST", f"{base}/session?directory={query}", {"title": title})
                assert status == 200
                sessions.append(created["id"])
            a, b = sessions
            bootstrap = invoke(a, "role.bootstrap", {}, expected_error="CONTEXT_BINDING_REQUIRED")
            assert invoke(a, "task_main.context", {"mode": "inspect"})["state"] == "unbound"
            assert invoke(b, "task_main.context", {"mode": "inspect"})["state"] == "unbound"
            assert invoke(a, "task_main.context", {"mode": "bind", "project_selector": "aota_forge"})["state"] == "project_bound"
            durable_project = projection.get_for_session(a)
            assert durable_project and durable_project.binding_kind == "PROJECT_BOUND" and durable_project.plan_binding is None
            assert projection.get_for_session(b) is None
            phase1_end = sequence
            stop_group(server)
            server = None
            stop_group(bridge)
            bridge = None
            bridge, epoch2 = start_bridge()
            server = start_server()
            recovered = invoke(a, "task_main.context", {"mode": "inspect"})
            assert recovered["state"] == "project_bound", recovered
            assert recovered["context"]["project_id"] == "aota_forge" and recovered["context"]["plan_id"] is None
            assert recovered["origin_session_ref"] == a
            assert invoke(b, "task_main.context", {"mode": "inspect"})["state"] == "unbound"
            promoted = invoke(a, "task_main.context", {"mode": "bind", "plan_selector": PLAN})
            assert promoted["state"] == "bound", promoted
            assert projection.get_for_session(a).plan_binding.plan_id == PLAN
            phase2_end = sequence
            stop_group(server)
            server = None
            stop_group(bridge)
            bridge = None
            bridge, epoch3 = start_bridge()
            server = start_server()
            recovered_plan = invoke(a, "task_main.context", {"mode": "inspect"})
            assert recovered_plan["state"] == "bound" and recovered_plan["context"]["plan_id"] == PLAN
            assert recovered_plan["origin_session_ref"] == a
            assert invoke(b, "task_main.context", {"mode": "inspect"})["state"] == "unbound"
            assert len({epoch1, epoch2, epoch3}) == 3
            observations = [json.loads(line) for line in bridge_evidence.read_text().splitlines() if line]
            assert {item["session_id"] for item in observations} == set(sessions)
            verified_epochs = []
            for low, high in ((0, phase1_end), (phase1_end, phase2_end), (phase2_end, sequence)):
                phase = [item for item in observations if low < int(item["call_id"].rsplit("_", 1)[1]) <= high]
                epochs = {item["bridge_id"] for item in phase}
                assert len(epochs) == 1 and {item["session_id"] for item in phase} == set(sessions)
                verified_epochs.append(next(iter(epochs)))
            assert len(set(verified_epochs)) == 3
            child_starts = [json.loads(line) for line in mcp_debug.read_text().splitlines() if line]
            project_child_starts = [item for item in child_starts
                                    if item.get("event") == "af59_unbound_mcp_mode" and item.get("instance_dir") == str(project)]
            assert len(project_child_starts) == 3, project_child_starts
            assert "aota-bridge-v1." not in projection.path.read_text()
            result = {
                "status": "PASS", "proof_kind": "production-shaped isolated real OpenCode deterministic provider",
                "opencode_version": binary_version, "session_A": a, "session_B": b, "SAME_SESSION": True,
                "PROJECT_ONLY_DURABLE_RECORD_CREATED": "PASS", "PROJECT_ONLY_RESTART_RECOVERY": "PASS",
                "PLAN_PROMOTION_AFTER_PROJECT_RECOVERY": "PASS", "PLAN_BOUND_RESTART_RECOVERY": "PASS",
                "SESSION_B_ISOLATION": "PASS", "BRIDGE_EPOCH_ROTATION": "PASS", "bridge_epochs": verified_epochs,
                "bridge_startup_epochs": [epoch1, epoch2, epoch3],
                "SHARED_MCP_CHILD_PER_LIFECYCLE": "PASS", "MCP_CHILD_START_COUNT": len(project_child_starts),
                "durable_project_record": durable_project.to_dict(), "durable_plan_record": projection.get_for_session(a).to_dict(),
                "TOOLS_LIST": tools, "MCP_PUBLIC_TOOL_COUNT": len(tools), "bridge_observations": observations,
                "tool_results": outputs, "production_reactivation_performed": False,
                "P1_BOOTSTRAP": "BASELINE_DENIED_CONTEXT_BINDING_REQUIRED",
                "P1_BOOTSTRAP_RESULT": bootstrap,
                "P1_INSPECT": "PASS",
                "source_frontier": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            }
            output = os.environ.get("AF_PLAN004_V5_EVIDENCE_OUTPUT")
            if output:
                Path(output).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps({key: value for key, value in result.items() if key not in (
                "tool_results", "bridge_observations", "durable_project_record", "durable_plan_record")}, indent=2))
            return 0
        finally:
            stop_group(server)
            stop_group(bridge)
            stop_group(stub)
            for log in logs:
                log.close()

if __name__ == "__main__":
    raise SystemExit(main())
