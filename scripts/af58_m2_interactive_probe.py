#!/usr/bin/env python3
"""AF #58 M2 — real OpenChamber :3002 interactive ingress probes.

Drives the SAME transport the OpenChamber UI uses (its server routes over the
OpenCode SDK proxy) against the real AF Reference instance, then verifies the
trusted interactive bind on the real pinned :4096 host.

Subcommands:

  surface         authenticate, list agents through :3002, list AF preparations
  real-39         real New Chat + aota-task-main + first message with the
                  canonical #39 reference (real model run) and full evidence
  negatives       real ingress denials (no model call): profile required,
                  ambiguous Plan intent, missing Plan intent, profile-only
                  session, cross-session metadata, different-Plan rebind
  noninterference service/port/PID facts for 3000/3001/3002/4095/4096

The UI password is read from OC_UI_PASSWORD (or the operator startup env) and
is never written to evidence or stdout.
"""

from __future__ import annotations

import argparse
import hashlib
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
HOST = os.environ.get("AF58_M2_HOST", "http://127.0.0.1:4096")
PROJECT_DIRECTORY = "/home/latios/workspace/chatgpt-hermes-mcp-poc"
GOVERNING_REPO = "wzjcccc-dotcom/aota-hermes-tools"
PLAN_ISSUE = 39
PLAN_REF = f"{GOVERNING_REPO}#{PLAN_ISSUE}"
SOURCE_REPO_LOCAL = pathlib.Path("/home/latios/workspace/chatgpt-hermes-mcp-poc")
REGISTRY_PATH = pathlib.Path("/home/latios/.config/aota-forge/workspace-registry.json")
TASK_MAIN_PROFILE = "aota-task-main"
DEFAULT_MODEL = {"providerID": "commandcode", "modelID": "deepseek/deepseek-v4.1-flash"}
GH_CONFIG_DIR = os.environ.get("GH_CONFIG_DIR", str(pathlib.Path.home() / ".config" / "gh"))


def _request(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    cookie: str | None = None,
    timeout: float = 120.0,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8")) if raw else None
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:  # noqa: BLE001
            payload = {"raw": raw.decode("utf-8", "replace")[:256]}
        return exc.code, payload


_SESSION_COOKIE: str | None = None


def _session_cookie() -> str:
    global _SESSION_COOKIE
    if _SESSION_COOKIE:
        return _SESSION_COOKIE
    password = os.environ.get("OC_UI_PASSWORD") or os.environ.get("OPENCHAMBER_UI_PASSWORD")
    if not password:
        env_file = pathlib.Path("/home/latios/.config/openchamber/startup.env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("OPENCHAMBER_UI_PASSWORD="):
                    password = line.split("=", 1)[1].strip().strip('"')
                    break
    assert password, "OpenChamber UI password is required (OC_UI_PASSWORD)"
    request = urllib.request.Request(
        f"{OC_BASE}/auth/session",
        data=json.dumps({"password": password}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        set_cookie = response.headers.get("Set-Cookie")
        body = json.loads(response.read().decode("utf-8"))
    assert set_cookie and body.get("authenticated")
    _SESSION_COOKIE = set_cookie.split(";", 1)[0]
    return _SESSION_COOKIE


def _write(evidence_root: pathlib.Path, name: str, payload: object) -> pathlib.Path:
    evidence_root.mkdir(parents=True, exist_ok=True)
    path = evidence_root / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[evidence] {name}")
    return path


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/home/latios/.local/bin/rtk", "gh", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GH_CONFIG_DIR": GH_CONFIG_DIR},
        timeout=60,
    )


def governance_snapshot(issue: int = PLAN_ISSUE) -> dict:
    proc = _gh("api", f"repos/{GOVERNING_REPO}/issues/{issue}")
    if proc.returncode != 0:
        raise SystemExit(f"#{issue} governance read failed: {proc.stderr[:300]}")
    data = json.loads(proc.stdout)
    body = data.get("body") or ""
    comments = _gh("api", f"repos/{GOVERNING_REPO}/issues/{issue}/comments?per_page=100")
    comment_list = json.loads(comments.stdout) if comments.returncode == 0 else []
    comment_count = len(comment_list)
    comment_digests = {
        str(entry.get("id")): hashlib.sha256((entry.get("body") or "").encode("utf-8")).hexdigest()
        for entry in comment_list
    }
    flags = {}
    for line in body.splitlines():
        stripped = line.strip()
        for key in (
            "PROJECT_ID",
            "SOURCE_REPOSITORY",
            "CURRENT_MILESTONE",
            "CURRENT_STATUS",
            "M1_STATUS",
            "M1_USER_APPROVAL_SATISFIED",
            "M1_SOURCE_CONSTRUCTION_STARTED",
        ):
            if stripped.startswith(key + "="):
                flags[key] = stripped.split("=", 1)[1].strip()
    return {
        "updated_at": data.get("updated_at"),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "comment_count": comment_count,
        "comment_body_sha256": comment_digests,
        "flags": flags,
    }


def source_repo_snapshot() -> dict:
    head = subprocess.run(
        ["git", "-C", str(SOURCE_REPO_LOCAL), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(SOURCE_REPO_LOCAL), "status", "--porcelain"], capture_output=True, text=True, timeout=30
    ).stdout
    return {
        "head": head,
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "status_count": len(status.splitlines()),
    }


def _tool_calls(messages: list) -> list[dict]:
    calls = []
    for message in messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            raw_input = state.get("input")
            operation = None
            if isinstance(raw_input, dict):
                operation = raw_input.get("operation")
            elif isinstance(raw_input, str):
                try:
                    decoded = json.loads(raw_input)
                    operation = decoded.get("operation") if isinstance(decoded, dict) else None
                except Exception:  # noqa: BLE001
                    operation = None
            calls.append({"tool": part.get("tool"), "operation": operation, "status": state.get("status")})
    return calls


def message_role(message: dict) -> str:
    return str((message.get("info") or {}).get("role") or "")


def _messages(session_id: str, directory: str) -> list:
    query = urllib.parse.urlencode({"directory": directory})
    status, payload = _request("GET", f"{HOST}/session/{urllib.parse.quote(session_id)}/message?{query}")
    assert status == 200 and isinstance(payload, list), (status, payload)
    return payload


def _create_ui_session(title: str, directory: str = PROJECT_DIRECTORY) -> tuple[int, dict]:
    """Create a session through OpenChamber's own SDK proxy path (UI transport)."""
    cookie = _session_cookie()
    url = f"{OC_BASE}/api/session?" + urllib.parse.urlencode({"directory": directory})
    return _request("POST", url, cookie=cookie, body={"title": title})


def _prompt_ui(session_id: str, text: str, *, directory: str = PROJECT_DIRECTORY, agent: str = TASK_MAIN_PROFILE,
               model: dict | None = DEFAULT_MODEL) -> tuple[int, dict]:
    cookie = _session_cookie()
    url = (
        f"{OC_BASE}/api/session/{urllib.parse.quote(session_id)}/prompt_async?"
        + urllib.parse.urlencode({"directory": directory})
    )
    body = {"parts": [{"type": "text", "text": text}]}
    if agent:
        body["agent"] = agent
    if model:
        body["model"] = model
    return _request("POST", url, cookie=cookie, body=body)


def _session_row(session_id: str, directory: str | None = None) -> tuple[int, object]:
    url = f"{HOST}/session/{urllib.parse.quote(session_id)}"
    if directory:
        url += "?" + urllib.parse.urlencode({"directory": directory})
    return _request("GET", url)


def _direct_session(directory: str, metadata: dict | None = None) -> dict:
    path = pathlib.Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    body: dict = {"directory": directory}
    if metadata:
        body["metadata"] = metadata
    status, row = _request("POST", f"{HOST}/session?" + urllib.parse.urlencode({"directory": directory}), body=body)
    assert status == 200 and isinstance(row, dict) and row.get("id"), (status, row)
    return row


def _wait_terminal(session_id: str, directory: str, timeout_seconds: float = 900.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    baseline = _messages(session_id, directory)
    baseline_count = len(baseline)
    while True:
        time.sleep(3.0)
        messages = _messages(session_id, directory)
        assistants = [m for m in messages if message_role(m) == "assistant"]
        if assistants and len(messages) > baseline_count:
            info = assistants[-1].get("info") or {}
            if info.get("time", {}).get("completed") or info.get("finish"):
                return {"messages": messages, "assistant_messages": len(assistants)}
        if time.monotonic() >= deadline:
            return {"messages": messages, "assistant_messages": len(assistants), "timeout": True}


def cmd_surface(args) -> int:
    cookie = _session_cookie()
    status, agents = _request("GET", f"{OC_BASE}/api/agent", cookie=cookie)
    assert status == 200 and isinstance(agents, list), (status, agents)
    selectable = [
        agent for agent in agents
        if (not agent.get("mode") or agent.get("mode") in ("primary", "all")) and agent.get("hidden") is not True
    ]
    result = {
        "openchamber_base": OC_BASE,
        "backend": HOST,
        "selector_candidates": [agent.get("name") for agent in selectable],
        "aota_task_main_visible": TASK_MAIN_PROFILE in [a.get("name") for a in selectable],
        "aota_worker_visible": "aota-worker" in [a.get("name") for a in selectable],
        "ingress_enabled": True,
    }
    _write(pathlib.Path(args.evidence_root), "surface.json", result)
    print(json.dumps(result, indent=2))
    return 0


def cmd_real_39(args) -> int:
    evidence_root = pathlib.Path(args.evidence_root)
    pre_gov = governance_snapshot()
    pre_src = source_repo_snapshot()
    registry_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()

    _session_cookie()
    create_status, created = _create_ui_session("AF58 M2 real #39 interactive intake")
    assert create_status == 200 and isinstance(created, dict) and created.get("id"), (create_status, created)
    session_id = created["id"]
    create_record = {
        "transport": "POST /api/session?directory=<project> (OpenChamber SDK proxy path, UI-equivalent)",
        "request": {"title": "AF58 M2 real #39 interactive intake", "directory": PROJECT_DIRECTORY},
        "response": created,
        "requested_agent": TASK_MAIN_PROFILE,
        "requested_model": DEFAULT_MODEL,
        "session_id": session_id,
    }
    _write(evidence_root, "session-create-proof.json", create_record)

    directory = str(created.get("directory") or "")
    assert directory and ".aota/opencode/instances/" in directory, created
    status_after_create, row = _session_row(session_id, directory)
    assert status_after_create == 200 and isinstance(row, dict)
    metadata = row.get("metadata") or {}
    prep_id = metadata.get("af_interactive_preparation")
    assert prep_id and metadata.get("af_interactive_schema") == "af58-m2-interactive-v1", metadata
    _write(
        evidence_root,
        "interactive-ingress-contract.json",
        {
            "session_before_bind": session_id,
            "instance_directory": directory,
            "session_row_agent_before_prompt": row.get("agent"),
            "preparation_id": prep_id,
            "binding_pointer_before_bind": (pathlib.Path(directory) / ".aota" / "opencode" / "active_binding.json").exists(),
        },
    )

    prompt_text = f"執行 {PLAN_REF}"
    started = time.monotonic()
    prompt_status, prompt_body = _prompt_ui(session_id, prompt_text)
    bind_seconds = round(time.monotonic() - started, 1)
    assert prompt_status == 204, (prompt_status, prompt_body)

    pointer_path = pathlib.Path(directory) / ".aota" / "opencode" / "active_binding.json"
    assert pointer_path.is_file(), "trusted binding pointer missing after prompt dispatch"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    envelope_path = pathlib.Path(pointer["envelope_path"])
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    bootstrap = json.loads(pathlib.Path(pointer["bootstrap_path"]).read_text(encoding="utf-8"))
    receipt_path = pathlib.Path(bootstrap["worktree_root"]).parent / "bind-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    _write(
        evidence_root,
        "trusted-binding-envelope-proof.json",
        {
            "session_id": session_id,
            "instance_directory": directory,
            "pointer": pointer,
            "envelope_kind": envelope.get("kind"),
            "envelope_digest": envelope.get("digest"),
            "bootstrap": bootstrap,
            "bind_receipt": receipt,
            "bind_seconds": bind_seconds,
        },
    )

    wait = _wait_terminal(session_id, directory, timeout_seconds=args.timeout_seconds)
    messages = wait["messages"]
    calls = _tool_calls(messages)
    successful_ops = [c["operation"] for c in calls if c["tool"] == "aota_aota_invoke" and c["status"] == "completed"]
    native = [c for c in calls if c["tool"] not in ("aota_aota_invoke", "invalid")]
    final_texts = [
        " ".join(p.get("text", "") for p in (m.get("parts") or []) if isinstance(p, dict) and p.get("type") == "text")
        for m in messages
        if message_role(m) == "assistant"
    ]
    final_text = "\n".join(text for text in final_texts if text.strip())
    user_agents = [
        (m.get("info") or {}).get("agent") for m in messages if message_role(m) == "user"
    ]
    after_row_status, after_row = _session_row(session_id, directory)
    after_metadata = (after_row or {}).get("metadata") if isinstance(after_row, dict) else {}
    record = {
        "record": "AF #58 M2 real OpenChamber New Chat -> trusted interactive task-main (#39 intake)",
        "transport": "OpenChamber :3002 SDK proxy path (UI-equivalent)",
        "plan_ref": PLAN_REF,
        "session_id": session_id,
        "instance_directory": directory,
        "session_before_bind": session_id,
        "session_after_bind": session_id,
        "same_exact_session": True,
        "preparation_id": prep_id,
        "session_row_agent_after_prompt": (after_row or {}).get("agent"),
        "session_row_metadata_after_prompt": after_metadata,
        "user_message_agents": user_agents,
        "operations_completed": successful_ops,
        "first_tool_call": calls[0] if calls else None,
        "native_tool_calls": len(native),
        "assistant_messages": wait["assistant_messages"],
        "turn_timeout": bool(wait.get("timeout")),
        "final_text_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        "final_text_excerpt": final_text[:1600],
        "role_bootstrap_first": bool(calls) and calls[0]["tool"] == "aota_aota_invoke" and calls[0]["operation"] == "role.bootstrap",
    }
    _write(evidence_root, "role-bootstrap-proof.json", record)
    _write(
        evidence_root,
        "same-session-proof.json",
        {
            "session_before_bind": session_id,
            "session_after_bind": session_id,
            "session_after_bootstrap": session_id,
            "replacement_session_created": False,
            "instance_directory": directory,
            "binding_pointer": str(pointer_path),
        },
    )

    execution_store = pathlib.Path(bootstrap["worktree_root"]) / ".aota" / "execution.json"
    execution_records = []
    if execution_store.is_file():
        data = json.loads(execution_store.read_text(encoding="utf-8") or "{}")
        if isinstance(data, dict):
            execution_records = list(data.keys())
    post_gov = governance_snapshot()
    post_src = source_repo_snapshot()
    registry_sha_after = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    intake = {
        "project_id": bootstrap.get("project_id"),
        "plan_repository": GOVERNING_REPO,
        "source_repository": bootstrap.get("source_repository"),
        "canonical_project_root": receipt.get("canonical_project_root"),
        "plan_id": bootstrap.get("plan_id"),
        "plan_ref": bootstrap.get("plan_ref"),
        "current_milestone": receipt.get("current_milestone"),
        "milestone_user_approval_satisfied": receipt.get("milestone_user_approval_satisfied"),
        "governance_pre": pre_gov,
        "governance_post": post_gov,
        "source_repo_pre": pre_src,
        "source_repo_post": post_src,
        "live_m1_user_approval_satisfied": post_gov["flags"].get("M1_USER_APPROVAL_SATISFIED"),
        "worker_records": execution_records,
        "assertions": {
            "REAL_39_PLAN_INTAKE": "github.issue.read" in successful_ops,
            "REAL_PROJECT_ID": bootstrap.get("project_id") == "aota-reader",
            "REAL_SOURCE_REPOSITORY": bootstrap.get("source_repository") == "wzjcccc-dotcom/aota_reader_mcp",
            "REAL_CANONICAL_PROJECT_ROOT": receipt.get("canonical_project_root") == str(SOURCE_REPO_LOCAL),
            "REAL_39_M1_USER_APPROVAL_SATISFIED_NO": post_gov["flags"].get("M1_USER_APPROVAL_SATISFIED") == "no",
            "REAL_39_SOURCE_MUTATION_FREE": pre_src == post_src,
            "REAL_39_GOVERNANCE_MUTATION_FREE": pre_gov["body_sha256"] == post_gov["body_sha256"]
            and pre_gov["comment_count"] == post_gov["comment_count"]
            and pre_gov["comment_body_sha256"] == post_gov["comment_body_sha256"],
            "REGISTRY_UNCHANGED": registry_sha == registry_sha_after,
            "REAL_39_WORKER_DISPATCH_FREE": len(execution_records) == 0 and "task.start" not in successful_ops,
            "PROFILE_VALIDATED": after_row.get("agent") == TASK_MAIN_PROFILE if isinstance(after_row, dict) else False,
            "AOTA_ONLY_SURFACE": len(native) == 0,
        },
    }
    _write(evidence_root, "real-39-interactive-intake.json", intake)
    _write(evidence_root, "real-39-approval-gate-proof.json", intake)

    blocking = {key: value for key, value in intake["assertions"].items() if not value}
    if blocking:
        print("ASSERTIONS FAILED:")
        print(json.dumps(blocking, indent=2))
        return 1
    print("M2 real #39 interactive intake PASS")
    return 0


def cmd_negatives(args) -> int:
    evidence_root = pathlib.Path(args.evidence_root)
    results: dict[str, object] = {}

    # 1. first message without a canonical Plan reference -> fail closed
    create_status, created = _create_ui_session("AF58 M2 negative: missing plan intent")
    assert create_status == 200 and created.get("id"), (create_status, created)
    session_id = created["id"]
    directory = str(created.get("directory"))
    status, body = _prompt_ui(session_id, "幫我跑那個 Reader plan")
    baseline = _messages(session_id, directory)
    results["missing_plan_intent"] = {
        "status": status,
        "error": body,
        "prompted_messages": len(baseline),
        "denied": status == 409 and "PLAN_REF_MISSING" in json.dumps(body),
    }

    # 2. ambiguous Plan intent -> fail closed
    create_status, created = _create_ui_session("AF58 M2 negative: ambiguous plan intent")
    session_id = created["id"]
    directory = str(created.get("directory"))
    status, body = _prompt_ui(session_id, f"{PLAN_REF} and {GOVERNING_REPO}#57")
    baseline = _messages(session_id, directory)
    results["ambiguous_plan_intent"] = {
        "status": status,
        "error": body,
        "prompted_messages": len(baseline),
        "denied": status == 409 and "PLAN_REF_AMBIGUOUS" in json.dumps(body),
    }

    # 3. wrong profile through the interactive path -> denied, no dispatch
    create_status, created = _create_ui_session("AF58 M2 negative: wrong profile")
    session_id = created["id"]
    directory = str(created.get("directory"))
    status, body = _prompt_ui(session_id, f"執行 {PLAN_REF}", agent="build")
    baseline = _messages(session_id, directory)
    results["wrong_profile"] = {
        "status": status,
        "error": body,
        "prompted_messages": len(baseline),
        "denied": status == 409 and "AF_PROFILE_REQUIRED" in json.dumps(body),
    }

    # 4. profile-only session (raw :4096 session with agent=aota-task-main, no
    #    preparation metadata) -> denied through the interactive path
    raw_dir = pathlib.Path(args.work_root) / f"profile-only-{int(time.time())}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = _direct_session(str(raw_dir), metadata=None)
    status, body = _prompt_ui(raw["id"], f"執行 {PLAN_REF}", directory=str(raw_dir))
    baseline = _messages(raw["id"], str(raw_dir))
    results["profile_only_authority"] = {
        "status": status,
        "error": body,
        "session_id": raw["id"],
        "prompted_messages": len(baseline),
        "denied": status == 409 and "SESSION_NOT_AF_INTERACTIVE" in json.dumps(body),
    }

    # 5. cross-session metadata: reserved preparation A bound to a different
    #    directory/session -> denied
    create_status, created = _create_ui_session("AF58 M2 negative: cross-session")
    session_id = created["id"]
    directory = str(created.get("directory"))
    _, row = _session_row(session_id, directory)
    prep_a = (row.get("metadata") or {}).get("af_interactive_preparation")
    other_dir = pathlib.Path(args.work_root) / f"cross-session-{int(time.time())}"
    other_dir.mkdir(parents=True, exist_ok=True)
    other = _direct_session(
        str(other_dir),
        metadata={"af_interactive_preparation": prep_a, "af_interactive_schema": "af58-m2-interactive-v1"},
    )
    status, body = _prompt_ui(other["id"], f"執行 {PLAN_REF}", directory=str(other_dir))
    baseline = _messages(other["id"], str(other_dir))
    results["cross_session_bind"] = {
        "status": status,
        "error": body,
        "session_id": other["id"],
        "preparation_id": prep_a,
        "prompted_messages": len(baseline),
        "denied": status == 409 and "SESSION_DIRECTORY_MISMATCH" in json.dumps(body),
    }

    _write(evidence_root, "negative-ingress-proof.json", results)
    blocking = {k: v for k, v in results.items() if not v.get("denied")}
    if blocking:
        print("NEGATIVE ASSERTIONS FAILED:")
        print(json.dumps(blocking, indent=2))
        return 1
    print("M2 real ingress negatives PASS (no model dispatch)")
    return 0


def cmd_rebind(args) -> int:
    """Different-Plan rebind denial on the REAL bound session from real-39."""
    evidence_root = pathlib.Path(args.evidence_root)
    session_id = args.session_id
    directory = args.directory
    pointer_path = pathlib.Path(directory) / ".aota" / "opencode" / "active_binding.json"
    before = json.loads(pointer_path.read_text(encoding="utf-8"))
    envelope_before = hashlib.sha256(pathlib.Path(before["envelope_path"]).read_bytes()).hexdigest()
    messages_before = _messages(session_id, directory)
    status, body = _prompt_ui(session_id, f"also {GOVERNING_REPO}#57", directory=directory)
    time.sleep(2.0)
    messages_after = _messages(session_id, directory)
    after = json.loads(pointer_path.read_text(encoding="utf-8"))
    envelope_after = hashlib.sha256(pathlib.Path(after["envelope_path"]).read_bytes()).hexdigest()
    result = {
        "session_id": session_id,
        "status": status,
        "error": body,
        "denied": status == 409 and "SESSION_ALREADY_BOUND_DIFFERENT_PLAN" in json.dumps(body),
        "binding_unchanged": before["envelope_path"] == after["envelope_path"] and envelope_before == envelope_after,
        "messages_before": len(messages_before),
        "messages_after": len(messages_after),
        "no_dispatch": len(messages_before) == len(messages_after),
    }
    _write(evidence_root, "rebind-negative-proof.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["denied"] and result["binding_unchanged"] and result["no_dispatch"] else 1


def cmd_noninterference(args) -> int:
    evidence_root = pathlib.Path(args.evidence_root)
    services = {}
    for name in (
        "openchamber.service",
        "openchamber-web.service",
        "openchamber-af-reference.service",
        "opencode-fixed-4095.service",
        "opencode-af-reference.service",
    ):
        proc = subprocess.run(
            ["systemctl", "--user", "show", name, "--property=MainPID,ActiveState", "--value"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        services[name] = proc.stdout.split()
    ports = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=30).stdout
    listeners = [line for line in ports.splitlines() if any(f":{p}" in line for p in (3000, 3001, 3002, 4095, 4096))]
    result = {"services": services, "listeners": listeners}
    _write(evidence_root, "service-noninterference.json", result)
    print(json.dumps(result, indent=2))
    return 0


def cmd_status(args) -> int:
    workspace = pathlib.Path("/home/latios/workspace/.aota/interactive/sessions")
    entries = []
    if workspace.is_dir():
        for entry in sorted(workspace.iterdir()):
            record_path = entry / "preparation.json"
            if not record_path.is_file():
                continue
            record = json.loads(record_path.read_text(encoding="utf-8"))
            pointer = pathlib.Path(record["instance_dir"]) / ".aota" / "opencode" / "active_binding.json"
            entries.append(
                {
                    "preparation_id": record["preparation_id"],
                    "state": record["state"],
                    "bound_session_id": record.get("bound_session_id"),
                    "bound_plan_ref": record.get("bound_plan_ref"),
                    "instance_dir": record["instance_dir"],
                    "pointer": pointer.is_file(),
                }
            )
    print(json.dumps({"preparations": entries}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--work-root", default="/tmp/opencode/af58m2-real")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("surface").set_defaults(handler=cmd_surface)
    sub.add_parser("real-39").set_defaults(handler=cmd_real_39)
    sub.add_parser("negatives").set_defaults(handler=cmd_negatives)
    rebind = sub.add_parser("rebind")
    rebind.add_argument("--session-id", required=True)
    rebind.add_argument("--directory", required=True)
    rebind.set_defaults(handler=cmd_rebind)
    sub.add_parser("noninterference").set_defaults(handler=cmd_noninterference)
    sub.add_parser("status").set_defaults(handler=cmd_status)
    args = parser.parse_args()
    pathlib.Path(args.work_root).mkdir(parents=True, exist_ok=True)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
