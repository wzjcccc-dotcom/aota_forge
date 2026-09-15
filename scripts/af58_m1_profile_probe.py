#!/usr/bin/env python3
"""AF #58 M1 — live host-profile contract probe (read-only + disposable sessions).

Subcommands:

  config     read the operator reference config + GET /agent and prove the AF
             host-profile contract (names, modes, hidden, effective tool
             surface) against the real pinned :4096 host.
  task-main  create a disposable OpenCode session through the AF task-main
             mechanics with agent=aota-task-main, submit one stub-model turn,
             and prove the session row + the persisted user message carry the
             exact profile and the model-visible tool surface is AOTA-only.
  worker     dispatch one bounded AF OpenCode Worker package through the real
             ExecutorAdapter with agent=aota-worker and prove the same.
  unknown    prove an unknown profile fails closed at the real host: no user
             message is persisted and no default-agent run starts.
  new-session-smoke
             raw disposable session create + first prompt carrying
             agent=<profile> (used for the OpenChamber New Chat boundary proof).

Disposable sessions only; no AF authority is claimed for them
(TRUSTED_INTERACTIVE_AF_BINDING_PROVEN=no).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOST = os.environ.get("AF58_M1_HOST", "http://127.0.0.1:4096")
STUB = os.environ.get("AF58_M1_STUB", "http://127.0.0.1:4097")
EVIDENCE_DIR = pathlib.Path(
    os.environ.get(
        "AF58_M1_EVIDENCE_DIR",
        "/home/latios/workspace/.aota-evidence/aota_forge/issue-58/M1/probe",
    )
)
PROBE_ROOT = pathlib.Path(
    os.environ.get("AF58_M1_PROBE_ROOT", "/tmp/af58-m1-probe")
)
STUB_MODEL = {"providerID": "afstub", "modelID": "stub-model"}

TASK_MAIN_PROFILE = "aota-task-main"
WORKER_PROFILE = "aota-worker"


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 30.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return response.status, None
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = None
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:  # noqa: BLE001 - bounded probe
            payload = {"raw": raw.decode("utf-8", "replace")[:512]}
        return exc.code, payload


def _write(name: str, payload: object) -> pathlib.Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _stub_reset() -> None:
    _http_json("POST", f"{STUB}/_admin/reset")


def _stub_captures() -> list[dict]:
    status, payload = _http_json("GET", f"{STUB}/_admin/captures")
    assert status == 200 and isinstance(payload, dict), (status, payload)
    return list(payload.get("captures") or [])


def _wait_for_captures(min_count: int = 1, timeout: float = 30.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    captures = _stub_captures()
    while len(captures) < min_count and time.monotonic() < deadline:
        time.sleep(0.25)
        captures = _stub_captures()
    return captures


def _effective_tool_surface(agent: dict) -> dict:
    """Pinned semantics: last matching rule wins; disabled only on final *-deny."""
    import fnmatch

    rules = agent.get("permission") or []
    tools = (
        "aota_aota_invoke",
        "bash",
        "edit",
        "read",
        "glob",
        "grep",
        "list",
        "webfetch",
        "websearch",
        "task",
        "todowrite",
        "lsp",
        "skill",
    )
    effective: dict[str, str] = {}
    for tool in tools:
        match = None
        for rule in rules:
            if fnmatch.fnmatchcase(tool, rule.get("permission", "")):
                match = rule
        effective[tool] = (match or {}).get("action", "allow")
    return effective


def cmd_config() -> int:
    agents_status, agents = _http_json("GET", f"{HOST}/agent")
    assert agents_status == 200 and isinstance(agents, list), (agents_status, agents)
    by_name = {agent.get("name"): agent for agent in agents}
    result = {
        "host": HOST,
        "agent_list_fetched": True,
        "agent_count": len(agents),
        "agents": [
            {
                "name": agent.get("name"),
                "mode": agent.get("mode"),
                "hidden": bool(agent.get("hidden", False)),
                "native": bool(agent.get("native", False)),
            }
            for agent in agents
        ],
    }
    for profile in (TASK_MAIN_PROFILE, WORKER_PROFILE):
        agent = by_name.get(profile)
        assert agent is not None, f"missing AF profile {profile!r} on {HOST}"
        surface = _effective_tool_surface(agent)
        native_tools = [
            tool
            for tool in (
                "bash",
                "edit",
                "read",
                "glob",
                "grep",
                "list",
                "webfetch",
                "websearch",
                "task",
                "todowrite",
                "lsp",
            )
        ]
        result[profile] = {
            "present": True,
            "mode": agent.get("mode"),
            "hidden": bool(agent.get("hidden", False)),
            "native": bool(agent.get("native", False)),
            "prompt_length": len(agent.get("prompt") or ""),
            "effective_tool_surface": surface,
            "native_tools_all_denied": all(surface[t] == "deny" for t in native_tools),
            "builtin_skill_denied": surface.get("skill") == "deny",
            "aota_mcp_allowed": surface.get("aota_aota_invoke") == "allow",
        }
    result["task_main_primary_selectable"] = (
        result[TASK_MAIN_PROFILE]["mode"] in ("primary", "all")
        and not result[TASK_MAIN_PROFILE]["hidden"]
    )
    result["worker_not_in_operator_selector"] = (
        result[WORKER_PROFILE]["hidden"] is True
        or result[WORKER_PROFILE]["mode"] == "subagent"
    )
    path = _write("opencode-agent-config-proof.json", result)
    print(f"config proof -> {path}")
    return 0


def _new_disposable_dir(name: str) -> str:
    root = PROBE_ROOT / name
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def cmd_task_main() -> int:
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.adapters.opencode.task_main import (
        create_task_main_session,
        submit_task_main_turn,
    )

    client = OpenCodeHostClient(HOST)
    directory = _new_disposable_dir(f"task-main-{int(time.time())}")
    _stub_reset()
    session = create_task_main_session(
        client,
        directory=directory,
        instance_key=f"af58-m1-probe-{int(time.time())}",
        model=STUB_MODEL,
        agent=TASK_MAIN_PROFILE,
    )
    session_id = session["id"]
    turn = submit_task_main_turn(
        client,
        session_id=session_id,
        directory=session.get("directory") or directory,
        text="AF58 M1 task-main profile propagation probe",
        model=STUB_MODEL,
        agent=TASK_MAIN_PROFILE,
        timeout_seconds=60,
        poll_interval_seconds=0.25,
    )
    messages = client.fetch_session_messages(
        session_id, directory=session.get("directory") or directory
    )
    user_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "user"
    ]
    captures = _stub_captures()
    tools_seen = sorted({tool for capture in captures for tool in capture.get("tools", [])})
    result = {
        "session_id": session_id,
        "directory": session.get("directory"),
        "session_row_agent": session.get("agent"),
        "user_message_agents": user_agents,
        "turn_completed": turn.completed,
        "model_calls": len(captures),
        "model_visible_tools": tools_seen,
        "expected_profile": TASK_MAIN_PROFILE,
        "profile_on_session_row": session.get("agent") == TASK_MAIN_PROFILE,
        "profile_on_first_af_turn": bool(user_agents)
        and all(agent == TASK_MAIN_PROFILE for agent in user_agents),
        "aota_only_tool_surface": tools_seen == ["aota_aota_invoke"],
    }
    path = _write("task-main-profile-propagation.json", result)
    print(f"task-main proof -> {path}")
    return 0


def cmd_worker() -> int:
    import stat

    from aota_forge.adapters.opencode.executor import (
        OPENCODE_EXECUTOR_ID,
        OpenCodeAdapter,
    )
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.core.execution.package import ExecutionPackage
    from aota_forge.core.execution.roles import RoleMapping
    from aota_forge.runtime.config import (
        EXECUTOR_OPENCODE,
        RuntimeBinding,
        RuntimeConfig,
    )

    directory = _new_disposable_dir(f"worker-{int(time.time())}")
    exe = PROBE_ROOT / "opencode-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    config = RuntimeConfig(
        executor=EXECUTOR_OPENCODE,
        executable=str(exe),
        concurrency=1,
        provider="afstub",
        model="stub-model",
        bindings=tuple(
            RuntimeBinding(
                work_role=role,
                executor=EXECUTOR_OPENCODE,
                profile=WORKER_PROFILE if role != "task-main" else TASK_MAIN_PROFILE,
                provider="afstub",
                model="stub-model",
                concurrency=1,
                executable=str(exe),
            )
            for role in ("analyst", "coder", "reviewer", "project-steward", "task-main")
        ),
        host_endpoint=HOST,
    )
    client = OpenCodeHostClient(HOST)
    adapter = OpenCodeAdapter(
        host_client=client,
        role_mapping=RoleMapping.create(OPENCODE_EXECUTOR_ID, {"coder": WORKER_PROFILE}),
        runtime_config=config,
        trusted_worker_directory=directory,
    )
    package = ExecutionPackage.create(
        canonical_task_id=f"aota_forge:af58-m1:probe:attempt-{int(time.time())}",
        project_id="aota_forge",
        canonical_role="coder",
        instruction="AF58 M1 worker profile propagation probe",
    )
    _stub_reset()
    dispatch = adapter.dispatch(package)
    time.sleep(1.0)
    session = client.get_session(dispatch.adapter_handle, directory=directory)
    messages = client.fetch_session_messages(dispatch.adapter_handle, directory=directory)
    user_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "user"
    ]
    captures = _stub_captures()
    tools_seen = sorted({tool for capture in captures for tool in capture.get("tools", [])})
    result = {
        "session_id": dispatch.adapter_handle,
        "session_row_agent": session.get("agent"),
        "user_message_agents": user_agents,
        "model_calls": len(captures),
        "model_visible_tools": tools_seen,
        "expected_profile": WORKER_PROFILE,
        "af_role_expected": "coder",
        "profile_on_session_row": session.get("agent") == WORKER_PROFILE,
        "profile_on_af_prompt": bool(user_agents)
        and all(agent == WORKER_PROFILE for agent in user_agents),
        "aota_only_tool_surface": tools_seen == ["aota_aota_invoke"],
    }
    path = _write("worker-profile-propagation.json", result)
    print(f"worker proof -> {path}")
    return 0


def cmd_unknown() -> int:
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient

    client = OpenCodeHostClient(HOST)
    directory = _new_disposable_dir(f"unknown-{int(time.time())}")
    session = client.create_session(directory=directory, title="af58-m1-unknown-profile")
    session_id = session["id"]
    baseline = client.fetch_session_messages(session_id, directory=directory)
    _stub_reset()
    client.submit_prompt_async(
        session_id,
        directory=directory,
        parts=[{"type": "text", "text": "this must never run under a default agent"}],
        model=STUB_MODEL,
        agent="aota-profile-that-does-not-exist",
    )
    # Bounded observation window: an unknown profile must never start a
    # default-agent run and must never persist a user message.
    deadline = time.monotonic() + 6.0
    captures = _stub_captures()
    after = baseline
    while time.monotonic() < deadline:
        time.sleep(0.5)
        captures = _stub_captures()
        after = client.fetch_session_messages(session_id, directory=directory)
        if captures or len(after) != len(baseline):
            break
    result = {
        "session_id": session_id,
        "baseline_messages": len(baseline),
        "messages_after_unknown_agent_prompt": len(after),
        "model_calls_after_unknown_agent_prompt": len(captures),
        "no_user_message_persisted": len(after) == len(baseline),
        "no_default_agent_run_started": len(captures) == 0,
        "expected": "PROFILE_FALLBACK_TO_DEFAULT=no (bounded host failure)",
    }
    path = _write("unknown-profile-fail-closed.json", result)
    print(f"unknown-profile proof -> {path}")
    return 0


def cmd_new_session_smoke() -> int:
    profile = sys.argv[2] if len(sys.argv) > 2 else TASK_MAIN_PROFILE
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient

    client = OpenCodeHostClient(HOST)
    directory = _new_disposable_dir(f"smoke-{int(time.time())}")
    _stub_reset()
    session = client.create_session(
        directory=directory, title="af58-m1-openchamber-smoke", agent=profile
    )
    session_id = session["id"]
    client.submit_prompt_async(
        session_id,
        directory=session.get("directory") or directory,
        parts=[{"type": "text", "text": "AF58 M1 disposable new-chat profile smoke"}],
        model=STUB_MODEL,
        agent=profile,
    )
    captures = _wait_for_captures(min_count=1, timeout=30.0)
    messages = client.fetch_session_messages(
        session_id, directory=session.get("directory") or directory
    )
    user_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "user"
    ]
    tools_seen = sorted({tool for capture in captures for tool in capture.get("tools", [])})
    result = {
        "session_id": session_id,
        "selected_profile": profile,
        "session_row_agent": session.get("agent"),
        "user_message_agents": user_agents,
        "model_visible_tools": tools_seen,
        "profile_on_session_row": session.get("agent") == profile,
        "profile_on_message_dispatch": bool(user_agents) and all(a == profile for a in user_agents),
        "TRUSTED_INTERACTIVE_AF_BINDING_PROVEN": "no",
    }
    path = _write("openchamber-new-chat-profile-propagation.json", result)
    print(f"new-session smoke -> {path}")
    return 0


def cmd_mcp_surface() -> int:
    """Model-visible tool surface for a profile session in a BOUND instance dir.

    M1 claims only the model-visible tool surface (AOTA MCP only). The binding
    itself is #56 accepted reference state, read-only reused as mechanical
    routing; this probe makes no binding-authority claim.
    """
    import shutil

    bound_raw = os.environ.get("AF58_M1_BOUND_INSTANCE_DIR", "").strip()
    assert bound_raw, "AF58_M1_BOUND_INSTANCE_DIR is required"
    bound = pathlib.Path(bound_raw)
    assert (bound / ".aota" / "opencode" / "active_binding.json").is_file()

    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient

    # Mechanical instance namespace under the bound root: never mutate the
    # accepted reference instance directory itself.
    stamp = str(int(time.time()))
    instance = bound.parent / f"af58-m1-probe-{stamp}"
    instance.mkdir(parents=True, exist_ok=False)
    shutil.copytree(bound / ".aota", instance / ".aota", symlinks=True)
    # Rewrite the pointer to the copied envelope path inside this instance.
    pointer_path = instance / ".aota" / "opencode" / "active_binding.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    old_envelope = pathlib.Path(pointer["envelope_path"])
    new_envelope = instance / ".aota" / old_envelope.parent.name / old_envelope.name
    if not new_envelope.is_file():
        # Envelope may live in a sibling namespace subdir; locate by name.
        candidates = list((instance / ".aota").rglob(old_envelope.name))
        assert candidates, "staged envelope copy not found"
        new_envelope = candidates[0]
    pointer["envelope_path"] = str(new_envelope)
    old_bootstrap = pointer.get("bootstrap_path")
    if old_bootstrap:
        pointer["bootstrap_path"] = old_bootstrap  # bootstrap stays in original worktree .aota
    pointer_path.write_text(json.dumps(pointer, indent=2, sort_keys=True), encoding="utf-8")

    client = OpenCodeHostClient(HOST)
    _stub_reset()
    session = client.create_session(
        directory=str(instance), title=f"af58-m1-mcp-surface-{stamp}", agent=TASK_MAIN_PROFILE
    )
    session_id = session["id"]
    client.submit_prompt_async(
        session_id,
        directory=session.get("directory") or str(instance),
        parts=[{"type": "text", "text": "AF58 M1 model-visible tool surface probe"}],
        model=STUB_MODEL,
        agent=TASK_MAIN_PROFILE,
    )
    captures = _wait_for_captures(min_count=1, timeout=30.0)
    messages = client.fetch_session_messages(
        session_id, directory=session.get("directory") or str(instance)
    )
    tools_seen = sorted({tool for capture in captures for tool in capture.get("tools", [])})
    mcp_status, _ = _http_json("GET", f"{HOST}/mcp?directory={instance}")
    result = {
        "session_id": session_id,
        "profile": TASK_MAIN_PROFILE,
        "mechanical_instance_dir": str(instance),
        "bound_reference_dir": str(bound),
        "binding_source": "#56 accepted reference instance (read-only mechanical reuse)",
        "binding_authority_claimed_by_m1": False,
        "mcp_status": mcp_status,
        "model_calls": len(captures),
        "model_visible_tools": tools_seen,
        "aota_only_tool_surface": tools_seen == ["aota_aota_invoke"],
        "user_message_agents": [
            message.get("info", {}).get("agent")
            for message in messages
            if message.get("info", {}).get("role") == "user"
        ],
    }
    path = _write("mcp-tool-surface.json", result)
    print(f"mcp surface proof -> {path}")
    return 0


def cmd_completion_reentry() -> int:
    """Real :4096 proof: completion re-entry into the exact parent session
    carries agent=aota-task-main on the AF-submitted envelope prompt."""
    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.adapters.opencode.session_reentry import OpenCodeExactSessionReentry
    from aota_forge.adapters.opencode.task_main import create_task_main_session

    client = OpenCodeHostClient(HOST)
    directory = _new_disposable_dir(f"reentry-{int(time.time())}")
    session = create_task_main_session(
        client,
        directory=directory,
        instance_key=f"af58-m1-reentry-{int(time.time())}",
        model=STUB_MODEL,
        agent=TASK_MAIN_PROFILE,
    )
    session_id = session["id"]
    _stub_reset()
    reentry = OpenCodeExactSessionReentry(
        client,
        timeout_seconds=6.0,
        poll_interval_seconds=0.5,
        default_model=dict(STUB_MODEL),
        agent=TASK_MAIN_PROFILE,
    )
    outcome = reentry.reenter(session_id, "AF58 M1 completion envelope probe")
    messages = client.fetch_session_messages(session_id, directory=directory)
    user_agents = [
        message.get("info", {}).get("agent")
        for message in messages
        if message.get("info", {}).get("role") == "user"
    ]
    result = {
        "session_id": session_id,
        "reentry_outcome": outcome.outcome,
        "reentry_accepted": outcome.accepted,
        "user_message_agents": user_agents,
        "expected_profile": TASK_MAIN_PROFILE,
        "completion_reentry_preserves_task_main_profile": bool(user_agents)
        and all(agent == TASK_MAIN_PROFILE for agent in user_agents),
    }
    path = _write("completion-reentry-profile-proof.json", result)
    print(f"completion reentry proof -> {path}")
    return 0


COMMANDS = {
    "config": cmd_config,
    "task-main": cmd_task_main,
    "worker": cmd_worker,
    "unknown": cmd_unknown,
    "new-session-smoke": cmd_new_session_smoke,
    "mcp-surface": cmd_mcp_surface,
    "completion-reentry": cmd_completion_reentry,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: {sys.argv[0]} {'|'.join(sorted(COMMANDS))}", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
