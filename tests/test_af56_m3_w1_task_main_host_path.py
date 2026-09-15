"""AF #56 M3/W1 — OpenCode task-main operator path & effective authority surface.

Focused mechanical tests for:

- the task/session-scoped host instance namespace (mechanical namespace, NOT
  the AF authorized worktree);
- digest-bound binding staging + per-instance routing pointer;
- exact task-main session create/prompt mechanics;
- ROLE_BOOTSTRAP_FIRST mechanical enforcement;
- the production DailyTaskMainLauncher executor=opencode branch (shared trusted
  preparation, bounded host launch translation, no Hermes binary requirement);
- write-boundary facts (task-main role surface carries no workspace.write
  authority).

No network access: the host client is a bounded structural double.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aota_forge.adapters.opencode import task_main as opcode_task_main
from aota_forge.adapters.opencode.host_client import OpenCodeHostClient
from aota_forge.adapters.opencode.task_main import (
    BINDING_KIND_TASK_MAIN,
    BINDING_KIND_WORKER,
    HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE,
    HOST_INSTANCE_DIRECTORY_IS_MECHANICAL_NAMESPACE,
    STALE_BINDING_REUSED,
    OpenCodeRoleBootstrapFirstViolation,
    OpenCodeTaskMainBindingError,
    OpenCodeTaskMainInstanceError,
    assert_role_bootstrap_first,
    create_task_main_session,
    instance_directory,
    native_tool_parts,
    resolve_operator_prompt_model,
    stage_envelope_in_instance,
    submit_task_main_turn,
    task_main_instance_key,
    worker_instance_key,
    write_binding_pointer,
)
from aota_forge.composition.task_main_daily_launcher import (
    AfMcpSurfaceProbe,
    DailyTaskMainLauncher,
    TaskMainToolSurfaceUnavailable,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_worktree(tmp_path: Path, project_id: str = "proj_m3w1", name: str = "wt") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        "schema_version: 1\n"
        f"project:\n  id: {project_id}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test project\ncapabilities: []\n"
        "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n"
        "  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    return root


def _opencode_runtime_config(tmp_path: Path, *, executor: str = "opencode", name: str = "rt") -> Path:
    cfg = tmp_path / f"{name}-{executor}.json"
    data = {
        "executor": executor,
        "concurrency": 1,
        "provider": "commandcode",
        "model": "deepseek/deepseek-v4.1-flash",
        "worker_execution_timeout_seconds": 900,
        "bindings": {
            "task-main": {"profile": "aota-task-main"},
            "coder": {"profile": "aota-worker"},
            "reviewer": {"profile": "aota-worker"},
            "analyst": {"profile": "aota-worker"},
            "project-steward": {"profile": "aota-worker"},
        },
    }
    if executor == "opencode":
        data["host_endpoint"] = "http://127.0.0.1:4096"
        data["executable"] = "/usr/bin/true"
    else:
        data["executable"] = "/bin/false"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


def _fake_envelope(path: Path, worktree_root: Path, kind: str = "worker") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kind": kind, "worktree_root": str(worktree_root), "dummy": True}
    path.write_text(
        json.dumps(
            {"version": "1", "kind": kind, "digest": "0" * 64, "payload": payload},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


class _FakeHostClient:
    """Bounded structural double for the pinned host client surface."""

    def __init__(self, *, healthy: bool = True, first_tool: str = "aota_aota_invoke",
                 first_operation: str = "role.bootstrap"):
        self._healthy = healthy
        self._first_tool = first_tool
        self._first_operation = first_operation
        self.created: list[dict] = []
        self.prompts: list[dict] = []
        self._messages: list[dict] = []
        self._sessions: dict[str, dict] = {}

    # -- creation ----------------------------------------------------------
    def create_session(self, **kwargs):
        self.created.append(kwargs)
        sid = f"ses_test{len(self.created):02d}"
        row = {
            "id": sid,
            "directory": kwargs["directory"],
            "metadata": kwargs.get("metadata") or {},
            "parentID": kwargs.get("parent_id"),
        }
        self._sessions[sid] = row
        return row

    def probe_versions(self):
        return {"healthy": self._healthy, "version": "1.18.30"}

    # -- prompt / observation ---------------------------------------------
    def submit_prompt_async(self, session_id, *, directory, parts, model=None, agent=None, system=None):
        self.prompts.append(
            {"session_id": session_id, "directory": directory, "parts": parts,
             "model": dict(model) if model else None}
        )
        tool_part = {
            "type": "tool",
            "tool": self._first_tool,
            "state": {
                "status": "completed",
                "input": json.dumps(
                    {"operation": self._first_operation, "arguments": {}}
                ),
            },
        }
        self._messages.append({"info": {"role": "user"}, "parts": [{"type": "text", "text": "start"}]})
        self._messages.append(
            {"info": {"role": "assistant", "finish": "stop"}, "parts": [tool_part, {"type": "text", "text": "ok"}]}
        )

    def fetch_session_messages(self, session_id, *, directory):
        return list(self._messages)

    def get_session(self, session_id, *, directory=None, scope_hint=None):
        return dict(self._sessions[session_id])

    def session_status(self, session_id, *, directory):
        return None

    def query_status(self, *, directory):
        return {}


# ---------------------------------------------------------------------------
# Instance namespace lifecycle
# ---------------------------------------------------------------------------


def test_instance_namespace_is_not_the_af_worktree(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, task_main_instance_key("run-1"))
    assert instance.is_dir()
    assert instance != root
    assert instance.relative_to(root / ".aota" / "opencode" / "instances")
    assert HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE is False
    assert HOST_INSTANCE_DIRECTORY_IS_MECHANICAL_NAMESPACE is True
    assert STALE_BINDING_REUSED is False


def test_instance_keys_are_mechanical_and_bounded(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    key = task_main_instance_key("1234-99")
    assert key == "task-main-1234-99"
    worker_key = worker_instance_key("proj:M1:W1:attempt-1")
    assert worker_key.startswith("worker-proj_M1_W1_attempt-1-")
    # Distinct task ids can never collide into one instance namespace.
    assert worker_instance_key("a:M:W:attempt-1") != worker_instance_key("a:M:W:attempt-2")
    for bad in ("", "..", "../escape", "a/b", "x" * 200):
        with pytest.raises(OpenCodeTaskMainInstanceError):
            instance_directory(root, bad)


def test_binding_pointer_and_envelope_are_contained(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, worker_instance_key("proj:M1:W1:attempt-1"))
    envelope = _fake_envelope(tmp_path / "outside" / "worker.json", root)
    # Envelope staged into the instance namespace is byte-identical and routed.
    staged = stage_envelope_in_instance(instance, envelope)
    assert staged.read_bytes() == envelope.read_bytes()
    pointer = write_binding_pointer(
        instance, kind=BINDING_KIND_WORKER, envelope_path=staged, binding_root=root
    )
    data = json.loads(pointer.read_text(encoding="utf-8"))
    assert data["kind"] == "worker"
    assert data["binding_root"] == str(root)
    assert "bootstrap_path" not in data

    # An envelope that is NOT inside this instance's .aota boundary fails closed.
    rogue = instance / "rogue.json"
    rogue.write_text("{}", encoding="utf-8")
    with pytest.raises(OpenCodeTaskMainBindingError):
        write_binding_pointer(
            instance, kind=BINDING_KIND_WORKER, envelope_path=rogue, binding_root=root
        )

    # task-main pointers require a real bootstrap path.
    with pytest.raises(OpenCodeTaskMainBindingError):
        write_binding_pointer(
            instance, kind=BINDING_KIND_TASK_MAIN, envelope_path=stage_envelope_in_instance(instance, envelope),
            binding_root=root,
        )


# ---------------------------------------------------------------------------
# Exact task-main session mechanics
# ---------------------------------------------------------------------------


def test_create_task_main_session_uses_instance_directory_and_metadata(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, task_main_instance_key("run"))
    host = _FakeHostClient()
    row = create_task_main_session(
        host, directory=instance, instance_key=instance.name,
        plan_ref="owner/repo#56", model={"providerID": "commandcode", "modelID": "m"},
    )
    call = host.created[0]
    assert call["directory"] == str(instance.resolve())
    assert call["metadata"][opcode_task_main.TASK_MAIN_METADATA_ROLE_KEY] == "task-main"
    assert call["metadata"][opcode_task_main.TASK_MAIN_METADATA_INSTANCE_KEY] == instance.name
    assert call["metadata"][opcode_task_main.TASK_MAIN_METADATA_PLAN_REF_KEY] == "owner/repo#56"
    # Pinned session-create model shape is {id, providerID}.
    assert call["model"] == {"id": "m", "providerID": "commandcode"}
    assert row["id"].startswith("ses_")


def test_submit_turn_waits_for_exact_session_termination(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, task_main_instance_key("run"))
    host = _FakeHostClient()
    session = create_task_main_session(host, directory=instance, instance_key=instance.name)
    result = submit_task_main_turn(
        host,
        session_id=session["id"],
        directory=session["directory"],
        text="startup",
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    assert result.completed is True
    assert result.first_semantic_tool == "aota_aota_invoke"


def test_submit_turn_timeout_fails_closed(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, task_main_instance_key("run"))
    host = _FakeHostClient()
    session = create_task_main_session(host, directory=instance, instance_key=instance.name)
    # No assistant response is scripted on this double.
    host.submit_prompt_async = lambda *a, **k: None  # type: ignore[method-assign]
    with pytest.raises(opcode_task_main.OpenCodeTaskMainTurnError):
        submit_task_main_turn(
            host,
            session_id=session["id"],
            directory=session["directory"],
            text="startup",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )


def test_role_bootstrap_first_mechanical_enforcement() -> None:
    good = [
        {"info": {"role": "assistant", "finish": "stop"},
         "parts": [{"type": "tool", "tool": "aota_aota_invoke",
                    "state": {"input": {"operation": "role.bootstrap"}}}]},
    ]
    assert_role_bootstrap_first(good)

    native_first = [
        {"info": {"role": "assistant"},
         "parts": [{"type": "tool", "tool": "bash", "state": {"input": {}}}]},
    ]
    with pytest.raises(OpenCodeRoleBootstrapFirstViolation):
        assert_role_bootstrap_first(native_first)

    wrong_op = [
        {"info": {"role": "assistant"},
         "parts": [{"type": "tool", "tool": "aota_aota_invoke",
                    "state": {"input": json.dumps({"operation": "github.issue.read"})}}]},
    ]
    with pytest.raises(OpenCodeRoleBootstrapFirstViolation):
        assert_role_bootstrap_first(wrong_op)

    with pytest.raises(OpenCodeRoleBootstrapFirstViolation):
        assert_role_bootstrap_first([])


def test_native_tool_parts_reads_attempts_not_af_entry() -> None:
    messages = [
        {"info": {"role": "assistant"},
         "parts": [
             {"type": "tool", "tool": "aota_aota_invoke", "state": {"status": "completed"}},
             {"type": "tool", "tool": "invalid", "state": {"status": "error"}},
             {"type": "tool", "tool": "bash", "state": {"status": "error"}},
             {"type": "tool", "tool": "write", "state": {"status": "error"}},
         ]},
    ]
    attempts = native_tool_parts(messages)
    assert [item["tool"] for item in attempts] == ["bash", "write"]


def test_prompt_model_requires_operator_binding() -> None:
    class _Cfg:
        provider = "commandcode"
        model = "deepseek/deepseek-v4.1-flash"

    assert resolve_operator_prompt_model(_Cfg()) == {
        "providerID": "commandcode",
        "modelID": "deepseek/deepseek-v4.1-flash",
    }

    class _Empty:
        provider = None
        model = None

    assert resolve_operator_prompt_model(_Empty()) is None

    class _Partial:
        provider = "commandcode"
        model = None

    with pytest.raises(opcode_task_main.OpenCodeTaskMainError):
        resolve_operator_prompt_model(_Partial())


# ---------------------------------------------------------------------------
# Production launcher executor=opencode branch
# ---------------------------------------------------------------------------


def test_launcher_prepare_opencode_requires_no_hermes_binary(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    cfg = _opencode_runtime_config(tmp_path)
    launcher = DailyTaskMainLauncher()
    ctx = launcher.prepare(
        worktree_root=root,
        project_id="proj_m3w1",
        worktree_id="wt-1",
        runtime_config_path=cfg,
        origin_task_main_session_ref="pending-test",
    )
    assert ctx.runtime_path == "thin"
    assert ctx.runtime_config.executor == "opencode"
    assert ctx.hermes_bin == ""
    bootstrap = json.loads((root / ".aota" / "task-main-thin-bootstrap.json").read_text(encoding="utf-8"))
    assert bootstrap["executor_id"] == "opencode"


def test_launcher_refuses_legacy_runtime_path_for_opencode(tmp_path: Path) -> None:
    from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter

    root = _make_worktree(tmp_path)
    cfg = _opencode_runtime_config(tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["runtime_path"] = "legacy"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    launcher = DailyTaskMainLauncher(
        plan_adapter=StaticPlanAuthorityAdapter(body="# P\n", plan_authority="o/r#1")
    )
    with pytest.raises(RuntimeError):
        launcher.prepare(
            worktree_root=root,
            project_id="proj_m3w1",
            worktree_id="wt-1",
            runtime_config_path=cfg,
        )


def test_launcher_launch_opencode_through_production_path(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    cfg = _opencode_runtime_config(tmp_path)
    host = _FakeHostClient()
    monkeypatch.setattr(
        "aota_forge.adapters.opencode.host_client.OpenCodeHostClient",
        lambda base_url, **kwargs: host,
    )
    monkeypatch.setattr(
        "aota_forge.composition.task_main_daily_launcher.probe_production_af_mcp_surface",
        lambda **kwargs: AfMcpSurfaceProbe(True, ("aota.invoke",), None),
    )
    launcher = DailyTaskMainLauncher()
    ctx, session_id = launcher.launch(
        worktree_root=root,
        project_id="proj_m3w1",
        worktree_id="wt-1",
        runtime_config_path=cfg,
        timeout_seconds=5,
    )
    assert session_id.startswith("ses_")
    # The session was created in a mechanical task-main instance namespace,
    # never in the worktree root itself.
    instance_dir = Path(host.created[0]["directory"])
    assert instance_dir != root.resolve()
    assert instance_dir.name.startswith("task-main-")
    assert instance_dir.relative_to(root / ".aota" / "opencode" / "instances")
    # Task-main MCP binding staged inside that exact instance namespace.
    pointer = json.loads(
        (instance_dir / ".aota" / "opencode" / "active_binding.json").read_text(encoding="utf-8")
    )
    assert pointer["kind"] == "task-main"
    assert Path(pointer["bootstrap_path"]).is_file()
    assert Path(pointer["envelope_path"]).is_file()
    # Trusted bootstrap bound to the exact session identity (no placeholder).
    bootstrap = json.loads((root / ".aota" / "task-main-thin-bootstrap.json").read_text(encoding="utf-8"))
    assert bootstrap["origin_task_main_session_ref"] == session_id
    # The startup turn was delivered to the exact session in the instance scope.
    assert host.prompts[-1]["session_id"] == session_id
    assert host.prompts[-1]["directory"] == str(instance_dir)


def test_launcher_surface_gate_fails_closed(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    cfg = _opencode_runtime_config(tmp_path)
    host = _FakeHostClient()
    monkeypatch.setattr(
        "aota_forge.adapters.opencode.host_client.OpenCodeHostClient",
        lambda base_url, **kwargs: host,
    )
    monkeypatch.setattr(
        "aota_forge.composition.task_main_daily_launcher.probe_production_af_mcp_surface",
        lambda **kwargs: AfMcpSurfaceProbe(False, (), "child failed"),
    )
    launcher = DailyTaskMainLauncher()
    with pytest.raises(TaskMainToolSurfaceUnavailable):
        launcher.launch(
            worktree_root=root,
            project_id="proj_m3w1",
            worktree_id="wt-1",
            runtime_config_path=cfg,
            timeout_seconds=5,
        )
    # No startup prompt was ever delivered when the surface gate failed.
    assert host.prompts == []


def test_launcher_native_first_tool_fails_closed(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    cfg = _opencode_runtime_config(tmp_path)
    host = _FakeHostClient(first_tool="bash")
    monkeypatch.setattr(
        "aota_forge.adapters.opencode.host_client.OpenCodeHostClient",
        lambda base_url, **kwargs: host,
    )
    monkeypatch.setattr(
        "aota_forge.composition.task_main_daily_launcher.probe_production_af_mcp_surface",
        lambda **kwargs: AfMcpSurfaceProbe(True, ("aota.invoke",), None),
    )
    launcher = DailyTaskMainLauncher()
    with pytest.raises(OpenCodeRoleBootstrapFirstViolation):
        launcher.launch(
            worktree_root=root,
            project_id="proj_m3w1",
            worktree_id="wt-1",
            runtime_config_path=cfg,
            timeout_seconds=5,
        )
