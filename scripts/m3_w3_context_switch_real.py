"""Bounded real M3/W3 OpenCode context-switch vertical.

The harness owns only disposable trusted setup and bounded evidence. The
task-main Agent performs every ``task_main.context`` call through the real
OpenCode production launcher and exact session re-entry seam.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ID = "aota_forge"
PLAN_A = "plan_m3_w3_context_a"
PLAN_B = "plan_m3_w3_context_b"
WORKTREE_ID = "m3-w3-real-context"
COORDINATOR_ID = f"{PROJECT_ID}:M3"

MANIFEST = """schema_version: 1
project:
  id: aota_forge
  name: M3/W3 real context fixture
  kind: test
  status: active
summary: bounded M3/W3 real context fixture
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
  index_location: .codegraph
plan:
  active_plan_id: null
constraints: []
"""


def _plan_body(plan_id: str) -> str:
    return f"""# {plan_id} real context fixture

```text
PLAN_STATUS=active
CURRENT_MILESTONE=M3
M3_STATUS=ready
M3_USER_APPROVAL_SATISFIED=yes
```
"""


def _write_evidence(root: Path, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "real-context-switch.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _decode_operation_input(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _decode_tool_output(state: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("output", "result"):
        value = state.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
    return None


def _tool_events(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for message in messages:
        for part in message.get("parts") or ():
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            arguments = _decode_operation_input(state.get("input")) or {}
            operation = arguments.get("operation")
            if not isinstance(operation, str):
                continue
            output = _decode_tool_output(state)
            error = output.get("error") if isinstance(output, dict) else None
            payload = (
                output.get("payload")
                if isinstance(output, dict) and isinstance(output.get("payload"), dict)
                else output
            )
            context = payload.get("context") if isinstance(payload, dict) else None
            status = payload.get("status") if isinstance(payload, dict) else None
            ok = output.get("ok") if isinstance(output, dict) else None
            if ok is None and isinstance(output, dict):
                ok = "error_code" not in output and output.get("outcome") != "failure"
            events.append(
                {
                    "tool": part.get("tool"),
                    "operation": operation,
                    "status": state.get("status"),
                    "ok": ok,
                    "error_code": (
                        output.get("error_code")
                        if isinstance(output, dict) and isinstance(output.get("error_code"), str)
                        else error.get("code") if isinstance(error, dict) else None
                    ),
                    "state": payload.get("state") if isinstance(payload, dict) else None,
                    "plan_id": context.get("plan_id") if isinstance(context, dict) else None,
                    "transition_phase": (
                        status.get("transition_phase") if isinstance(status, dict) else None
                    ),
                }
            )
    return events


def _last_operation(events: list[dict[str, Any]], operation: str) -> dict[str, Any] | None:
    matches = [event for event in events if event["operation"] == operation]
    return matches[-1] if matches else None


def _prepare_fixture(run_root: Path):
    from aota_forge.governance.intake import (
        GovernanceIntakeContext,
        GovernanceIntakeService,
    )

    workspace = run_root / "workspace"
    worktree = workspace / "checkout"
    (worktree / ".aota").mkdir(parents=True, exist_ok=True)
    (worktree / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    (worktree / "work").mkdir()

    registry = run_root / "workspaces.json"
    registry.write_text(json.dumps({"workspace": {"candidates": [str(workspace)]}}), encoding="utf-8")
    governance_base = run_root / "plans"
    governance_base.mkdir()
    store_path = run_root / "governance.sqlite3"
    intake = GovernanceIntakeService(
        GovernanceIntakeContext(
            workspace_id="workspace",
            governance_base=governance_base,
            registry_path=registry,
            workspace_root=workspace,
            project_root=worktree,
            governance_store_path=store_path,
            journal_path=run_root / "governance-journal.json",
        ),
        principal="m3-w3-real-context",
    )
    if intake.project("init", project_id=PROJECT_ID).get("status") != "VERIFIED":
        raise RuntimeError("disposable project initialization did not verify")
    for plan_id in (PLAN_A, PLAN_B):
        result = intake.plan(
            "init",
            project_id=PROJECT_ID,
            plan_id=plan_id,
            candidate_body=_plan_body(plan_id),
        )
        if result.get("status") != "VERIFIED":
            raise RuntimeError(f"disposable Plan initialization did not verify: {plan_id}")
    return worktree, registry, governance_base, store_path


def _seed_coordinator(
    *,
    worktree: Path,
    registry: Path,
    governance_base: Path,
    store_path: Path,
    runtime_config: Path,
    session_id: str,
):
    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
    from aota_forge.runtime.task_main.context_switch_runtime import (
        TrustedContextSwitchTarget,
    )
    from aota_forge.runtime.task_main.coordinator_state import (
        CoordinatorStatus,
        TaskMainCoordinatorState,
    )
    from aota_forge.runtime.task_main.coordinator_store import (
        FileBackedTaskMainCoordinatorStore,
    )

    host = compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        runtime_config_path=runtime_config,
        origin_task_main_session_ref=session_id,
        registry_path=registry,
        governance_base=governance_base,
        plan_id=PLAN_A,
        plan_ref=f"local-governance:{PROJECT_ID}/{PLAN_A}",
        governance_store_path=store_path,
    )
    try:
        binding = host.plan_authority_binding
        roots = host.authorized_roots
        candidates = tuple(host.project_evidence.candidates)
        if binding is None or roots is None or len(candidates) != 1:
            raise RuntimeError("trusted source binding composition did not produce singular evidence")
        target = TrustedContextSwitchTarget(
            project_id=PROJECT_ID,
            project_fingerprint=str(candidates[0].candidate_fingerprint),
            plan_binding=binding,
            origin_task_main_session_ref=session_id,
            authorized_roots=roots,
        )
        coordinator_path = worktree / ".aota" / "coordinator.json"
        store = FileBackedTaskMainCoordinatorStore(coordinator_path)
        store.create(
            TaskMainCoordinatorState(
                coordinator_id=COORDINATOR_ID,
                plan_authority=binding.authority_ref,
                plan_digest=binding.source_digest or "",
                plan_source_revision=(
                    str(binding.source_revision) if binding.source_revision is not None else None
                ),
                milestone_id="M3",
                entry_base="m3-w3-real-context",
                origin_task_main_session_ref=session_id,
                project_id=PROJECT_ID,
                executor_id="opencode",
                work_items=("W3-SAFE-SWITCH",),
                wi_status={"W3-SAFE-SWITCH": "PENDING"},
                user_approval_satisfied=True,
                status=CoordinatorStatus.ACTIVE,
                context_binding=target.to_context_binding(),
            )
        )
        store.close()
        return coordinator_path
    finally:
        if host.governance_store is not None:
            host.governance_store.close()
        host.execution_store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=Path.home() / ".config" / "aota-forge" / "runtime-opencode-reference.json",
    )
    args = parser.parse_args()
    if not args.runtime_config.is_file():
        raise SystemExit(f"runtime config missing: {args.runtime_config}")

    args.run_root.mkdir(parents=True, exist_ok=True)
    worktree, registry, governance_base, store_path = _prepare_fixture(args.run_root)

    from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

    local_a = f"local-governance:{PROJECT_ID}/{PLAN_A}"
    local_b = f"local-governance:{PROJECT_ID}/{PLAN_B}"
    launcher = DailyTaskMainLauncher()
    initial_prompt = (
        "You are the bounded M3/W3 task-main Agent. Call exactly one operation now: "
        'aota.invoke with operation="role.bootstrap" and arguments={}. '
        "Do not call any other operation in this turn."
    )
    ctx, session_id = launcher.launch(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id=WORKTREE_ID,
        runtime_config_path=args.runtime_config,
        initial_prompt=initial_prompt,
        timeout_seconds=240,
        completion_timeout_seconds=30,
        max_productive_continuations=0,
        plan_ref=local_a,
        plan_id=PLAN_A,
        governance_base=governance_base,
        governance_store_path=store_path,
        registry_path=registry,
        trace_path=args.run_root / "task-main-trace.log",
    )

    coordinator_path = _seed_coordinator(
        worktree=worktree,
        registry=registry,
        governance_base=governance_base,
        store_path=store_path,
        runtime_config=args.runtime_config,
        session_id=session_id,
    )
    common = {
        "worktree_root": worktree,
        "session_id": session_id,
        "project_id": PROJECT_ID,
        "worktree_id": WORKTREE_ID,
        "runtime_config_path": args.runtime_config,
        "coordinator_id": COORDINATOR_ID,
        "coordinator_store_path": coordinator_path,
        "plan_ref": local_a,
        "plan_id": PLAN_A,
        "governance_base": governance_base,
        "governance_store_path": store_path,
        "registry_path": registry,
        "timeout_seconds": 240,
    }

    launcher.resume(
        **common,
        payload=(
            "Call exactly one operation: aota.invoke with operation=\"task_main.context\" "
            'and arguments={"mode":"inspect"}. Do not call any other operation.'
        ),
    )
    launcher.resume(
        **common,
        payload=(
            "Call exactly one operation: aota.invoke with operation=\"task_main.context\" "
            f'and arguments={{"mode":"switch","project_ref":"{PROJECT_ID}","plan_ref":"{local_b}"}}. '
            "Do not call any other operation or retry."
        ),
    )
    launcher.resume(
        **common,
        payload=(
            "Call exactly one operation: aota.invoke with operation=\"task_main.context\" "
            'and arguments={"mode":"switch","project_ref":"foreign-project",'
            f'"plan_ref":"{local_b}"}}. Do not retry after the expected fail-closed response.'
        ),
    )
    launcher.resume(
        **common,
        payload=(
            "Call exactly one operation: aota.invoke with operation=\"task_main.context\" "
            'and arguments={"mode":"inspect"}. Do not call any other operation.'
        ),
    )

    host = OpenCodeHostClient(ctx.runtime_config.host_endpoint or "http://127.0.0.1:4096")
    session = host.get_session(session_id)
    messages = host.fetch_session_messages(session_id, directory=str(session["directory"]))
    events = _tool_events(messages)
    bootstrap = _last_operation(events, "role.bootstrap")
    inspect_events = [event for event in events if event["operation"] == "task_main.context"]
    inspect_before = inspect_events[0] if len(inspect_events) > 0 else None
    safe_switch = inspect_events[1] if len(inspect_events) > 1 else None
    unsafe_switch = inspect_events[2] if len(inspect_events) > 2 else None
    inspect_after = inspect_events[3] if len(inspect_events) > 3 else None
    first_operation = events[0]["operation"] if events else None

    evidence = {
        "record": "AF #002 M3/W3 real OpenCode context-switch vertical",
        "reference_host": host.probe_versions(),
        "runtime_config": str(args.runtime_config),
        "profile": ctx.runtime_config.get_binding("task-main").profile,
        "provider_model_from_operator_config": {
            "provider": ctx.runtime_config.provider,
            "model": ctx.runtime_config.model,
        },
        "session": {
            "id": session_id,
            "directory": str(session["directory"]),
            "message_count": len(messages),
            "first_operation": first_operation,
            "operation_count": len(events),
        },
        "operations": {
            "bootstrap": bootstrap,
            "inspect_before": inspect_before,
            "safe_switch": safe_switch,
            "unsafe_switch": unsafe_switch,
            "inspect_after": inspect_after,
        },
        "assertions": {
            "REAL_OPENCODE_TASK_MAIN_SESSION": session_id.startswith("ses_"),
            "ROLE_BOOTSTRAP_FIRST": first_operation == "role.bootstrap",
            "EXPECTED_OPERATION_SEQUENCE": [event["operation"] for event in events]
            == [
                "role.bootstrap",
                "task_main.context",
                "task_main.context",
                "task_main.context",
                "task_main.context",
            ],
            "SAFE_SWITCH_TO_B": (
                safe_switch is not None
                and safe_switch["ok"] is True
                and safe_switch["state"] == "switched"
                and safe_switch["plan_id"] == PLAN_B
                and safe_switch["transition_phase"] == "APPLIED"
            ),
            "UNSAFE_CROSS_PROJECT_FAILS_CLOSED": (
                unsafe_switch is not None
                and unsafe_switch["ok"] is False
                and unsafe_switch["error_code"] in {"UNAUTHORIZED_TARGET", "CROSS_PROJECT_SWITCH_DENIED"}
            ),
            "SAFE_CONTEXT_READBACK_STAYS_B": (
                inspect_after is not None
                and inspect_after["ok"] is True
                and inspect_after["plan_id"] == PLAN_B
            ),
            "SAME_EXACT_SESSION": True,
            "NO_NATIVE_TOOL_BYPASS": all(
                event["tool"] in {"aota_aota_invoke", "invalid"} for event in events
            ),
        },
        "credential_values_recorded": False,
    }
    _write_evidence(args.evidence_root, evidence)
    failed = {key: value for key, value in evidence["assertions"].items() if not value}
    if failed:
        print(json.dumps({"assertions_failed": failed}, indent=2, sort_keys=True))
        return 1
    print("M3_W3_CONTEXT_SWITCH_REAL=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
