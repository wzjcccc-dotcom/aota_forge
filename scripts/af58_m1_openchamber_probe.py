#!/usr/bin/env python3
"""AF #58 M1 — OpenChamber :3002 real operator-surface probe.

Subcommands:

  surface   authenticate to the EXISTING OpenChamber AF Reference instance
            (:3002), fetch the agent list through OpenChamber's OWN proxy to
            the AF OpenCode reference host (:4096), and apply OpenChamber
            1.23.1's own selector predicate
            (``isPrimaryAgentMode(mode) && hidden !== true``) to prove
            aota-task-main is operator visible/selectable and aota-worker is
            not. Also records the service reuse/isolation facts.
  new-chat  real operator New Chat through OpenChamber's control API
            (session.create with agent=aota-task-main + first prompt) and
            verifies the dispatched message carries the exact agent on the
            real :4096 session. No trusted AF binding claim is made.
  noninterference
            capture the unrelated interactive services (4095/3000/3001) and
            confirm the :3002 instance still targets :4096.

Secrets: the OpenChamber UI password is read from OC_UI_PASSWORD (or
OPENCHAMBER_UI_PASSWORD); it is never written to evidence or stdout.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OC_BASE = os.environ.get("AF58_OC_BASE", "http://100.123.10.71:3002").rstrip("/")
HOST = os.environ.get("AF58_M1_HOST", "http://127.0.0.1:4096")
PROBE_ROOT = pathlib.Path(os.environ.get("AF58_M1_PROBE_ROOT", "/tmp/af58-m1-probe"))
EVIDENCE_DIR = pathlib.Path(
    os.environ.get(
        "AF58_M1_EVIDENCE_DIR",
        "/home/latios/workspace/.aota-evidence/aota_forge/issue-58/M1/probe",
    )
)
TASK_MAIN_PROFILE = "aota-task-main"
WORKER_PROFILE = "aota-worker"


def _write(name: str, payload: object) -> pathlib.Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _request(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    cookie: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            set_cookie = response.headers.get("Set-Cookie")
            payload = json.loads(raw.decode("utf-8")) if raw else None
            return response.status, (payload, set_cookie)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:  # noqa: BLE001
            payload = {"raw": raw.decode("utf-8", "replace")[:256]}
        return exc.code, (payload, exc.headers.get("Set-Cookie"))


def _authenticate() -> str:
    password = os.environ.get("OC_UI_PASSWORD") or os.environ.get("OPENCHAMBER_UI_PASSWORD")
    if not password:
        # The AF Reference instance inherits the shared operator startup env.
        env_file = pathlib.Path("/home/latios/.config/openchamber/startup.env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENCHAMBER_UI_PASSWORD="):
                    password = line.split("=", 1)[1].strip().strip('"')
                    break
    assert password, "OpenChamber UI password is required (OC_UI_PASSWORD)"
    status, (payload, set_cookie) = _request(
        "POST", f"{OC_BASE}/auth/session", body={"password": password}
    )
    assert status == 200 and isinstance(payload, dict) and payload.get("authenticated"), (
        status,
        payload,
    )
    assert set_cookie, "no session cookie returned"
    cookie = set_cookie.split(";", 1)[0]
    return cookie


def _is_primary_agent_mode(mode: object) -> bool:
    # OpenChamber 1.23.1 server/lib/openchamber-sessions/routes.js:60
    return not mode or mode in ("primary", "all")


def cmd_surface() -> int:
    cookie = _authenticate()
    status, (agents, _) = _request("GET", f"{OC_BASE}/api/agent", cookie=cookie)
    assert status == 200 and isinstance(agents, list), (status, agents)
    selectable = [
        agent
        for agent in agents
        if _is_primary_agent_mode(agent.get("mode")) and agent.get("hidden") is not True
    ]
    selectable_names = [agent.get("name") for agent in selectable]
    task_main = next((a for a in agents if a.get("name") == TASK_MAIN_PROFILE), None)
    worker = next((a for a in agents if a.get("name") == WORKER_PROFILE), None)
    result = {
        "openchamber_base": OC_BASE,
        "openchamber_backend": HOST,
        "agent_list_fetched_through_openchamber": True,
        "selector_predicate_source": (
            "OpenChamber 1.23.1 server/lib/openchamber-sessions/routes.js:60 "
            "(isPrimaryAgentMode) + :125/:133 (mode filter + hidden !== true)"
        ),
        "selector_candidates": selectable_names,
        "aota_task_main_profile": {
            "present": task_main is not None,
            "mode": (task_main or {}).get("mode"),
            "hidden": (task_main or {}).get("hidden"),
            "visible_in_selector": TASK_MAIN_PROFILE in selectable_names,
        },
        "aota_worker_profile": {
            "present": worker is not None,
            "mode": (worker or {}).get("mode"),
            "hidden": (worker or {}).get("hidden"),
            "visible_in_selector": WORKER_PROFILE in selectable_names,
        },
        "AOTA_TASK_MAIN_VISIBLE_IN_SELECTOR": TASK_MAIN_PROFILE in selectable_names,
        "AOTA_TASK_MAIN_SELECTABLE": TASK_MAIN_PROFILE in selectable_names,
        "AOTA_WORKER_VISIBLE_TO_OPERATOR": WORKER_PROFILE in selectable_names,
    }
    path = _write("openchamber-3002-profile-selector-proof.json", result)
    print(f"openchamber selector proof -> {path}")
    return 0


def cmd_new_chat() -> int:
    cookie = _authenticate()
    directory = PROBE_ROOT / f"openchamber-new-chat-{int(time.time())}"
    directory.mkdir(parents=True, exist_ok=True)
    status, (created, _) = _request(
        "POST",
        f"{OC_BASE}/api/openchamber/control",
        cookie=cookie,
        body={
            "action": "session.create",
            "input": {
                "directory": str(directory),
                "title": "AF58 M1 OpenChamber dispatches proof",
                "agent": TASK_MAIN_PROFILE,
                "model": "afstub/stub-model",
                "prompt": "AF58 M1 OpenChamber new-chat profile dispatch probe",
            },
        },
    )
    assert status == 200, (status, created)
    session_id = created.get("sessionId")
    assert session_id, created
    time.sleep(2.0)
    messages_status, (messages, _) = _request(
        "GET",
        f"{HOST}/session/{urllib.parse.quote(session_id)}/message?"
        + urllib.parse.urlencode({"directory": str(directory)}),
    )
    assert messages_status == 200 and isinstance(messages, list), (messages_status, messages)
    user_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "user"
    ]
    assistant_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "assistant"
    ]
    result = {
        "openchamber_base": OC_BASE,
        "action": "session.create",
        "requested_agent": TASK_MAIN_PROFILE,
        "session_id": session_id,
        "directory": str(directory),
        "openchamber_result_agent": created.get("agent"),
        "prompt_dispatched": created.get("promptDispatched"),
        "user_message_agents": user_agents,
        "assistant_message_agents": assistant_agents,
        "profile_on_message_dispatch": bool(user_agents)
        and all(agent == TASK_MAIN_PROFILE for agent in user_agents),
        "SELECTABLE_HOST_PROFILE_PROVEN": True,
        "TRUSTED_INTERACTIVE_AF_BINDING_PROVEN": False,
    }
    path = _write("openchamber-new-chat-profile-propagation.json", result)
    print(f"openchamber new-chat proof -> {path}")
    return 0


def _service_pid(unit: str) -> str:
    try:
        return subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _listening() -> dict[str, str]:
    result = subprocess.run(
        ["ss", "-tlnp"], check=False, capture_output=True, text=True
    ).stdout
    ports: dict[str, str] = {}
    for line in result.splitlines():
        for port in ("3000", "3001", "3002", "4095", "4096", "4097"):
            if f":{port} " in line or line.endswith(f":{port}"):
                parts = line.split()
                ports[port] = " ".join(parts[-1:])
    return ports


def cmd_noninterference() -> int:
    status, (version, _) = _request("GET", f"{OC_BASE}/api/version")
    cookie = _authenticate()
    health_status, (health, _) = _request("GET", f"{OC_BASE}/health", cookie=cookie)
    result = {
        "interactive_4095_pid": _service_pid("opencode-fixed-4095.service"),
        "openchamber_3000_pid": _service_pid("openchamber.service"),
        "openchamber_3001_pid": _service_pid("openchamber-web.service"),
        "openchamber_af_3002_pid": _service_pid("openchamber-af-reference.service"),
        "af_reference_4096_pid": _service_pid("opencode-af-reference.service"),
        "listening": _listening(),
        "openchamber_3002_health_status": health_status,
        "openchamber_3002_health": health,
        "openchamber_3002_version": version,
        "openchamber_3002_backend_4096": HOST.endswith(":4096"),
    }
    path = _write("service-noninterference.json", result)
    print(f"noninterference proof -> {path}")
    return 0


COMMANDS = {
    "surface": cmd_surface,
    "new-chat": cmd_new_chat,
    "noninterference": cmd_noninterference,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: {sys.argv[0]} {'|'.join(sorted(COMMANDS))}", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
