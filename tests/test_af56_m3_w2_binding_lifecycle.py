"""AF #56 M3/W2 — task-scoped binding lifecycle & role-separated instance authority.

Proves the NB-3 closure rule mechanically:

- The OpenCode host instance directory is a trusted task/session-scoped
  mechanical namespace; the AF authorized worktree is carried inside the
  digest-bound binding envelope (HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE=no).
- Same-worktree sequential task lifetimes get DISTINCT instance namespaces with
  DISTINCT staged bindings; a stale task A binding can never authorize task B.
- task-main and Worker MCP authorities live in separate instance namespaces.
- The production per-instance MCP wrapper resolves only the binding staged for
  that exact instance and never defaults the synthetic project-evidence seam
  (NB-4 closure).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from aota_forge.adapters.opencode.task_main import (
    BINDING_KIND_TASK_MAIN,
    BINDING_KIND_WORKER,
    instance_directory,
    stage_envelope_in_instance,
    task_main_instance_key,
    worker_instance_key,
    write_binding_pointer,
)
from aota_forge.composition.execution import opencode_worker_directory_resolver
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    create_worker_envelope,
)
from aota_forge.work_plane.handoff import TaskHandoff

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py"


def _make_worktree(tmp_path: Path, project_id: str = "aota_forge", name: str = "w") -> Path:
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


def _handoff(task_id: str) -> TaskHandoff:
    return TaskHandoff(
        work_role="coder",
        task_kind="work_item",
        objective=f"objective for {task_id}",
        bounded_scope="bounded scope",
        validation_expectations=("v1",),
        semantic_stop_expectations=("stop",),
    )


def _package(task_id: str, project_id: str = "aota_forge") -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id=project_id,
        canonical_role="coder",
        instruction="bounded instruction",
        operation="task_dispatch",
        capability_requirements={},
    )


def _worker_resolver_with_envelopes(root: Path):
    def _resolver(payload):
        task_id = payload["context"]["canonical_task_id"]
        envelope = create_worker_envelope(
            worktree_root=root,
            project_id=payload["context"]["project_id"],
            worktree_id="wt",
            canonical_task_id=task_id,
            handoff=_handoff(task_id),
        )
        return {PRE_RESOLVED_BINDING_ENV: str(envelope)}

    return opencode_worker_directory_resolver(_resolver)


def _pointer(instance_dir: str | Path) -> dict:
    return json.loads(
        (Path(instance_dir) / ".aota" / "opencode" / "active_binding.json").read_text(encoding="utf-8")
    )


def _envelope_payload(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["payload"]


# ---------------------------------------------------------------------------
# Same-worktree sequential redispatch (NB-3)
# ---------------------------------------------------------------------------


def test_same_worktree_redispatch_gets_distinct_instance_namespaces(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    resolver = _worker_resolver_with_envelopes(root)
    task_a = "aota_forge:M3:W2:task-a:11111111"
    task_b = "aota_forge:M3:W2:task-b:22222222"

    dir_a = resolver(_package(task_a))
    dir_b = resolver(_package(task_b))

    assert dir_a is not None and dir_b is not None
    assert dir_a != dir_b
    # Both are mechanical namespaces under the trusted boundary, NOT the worktree.
    for directory in (dir_a, dir_b):
        resolved = Path(directory).resolve()
        assert resolved != root.resolve()
        assert resolved.relative_to(root.resolve() / ".aota" / "opencode" / "instances")

    pointer_a = _pointer(dir_a)
    pointer_b = _pointer(dir_b)
    assert pointer_a["kind"] == BINDING_KIND_WORKER
    assert pointer_b["kind"] == BINDING_KIND_WORKER
    assert pointer_a["binding_root"] == str(root.resolve())
    assert pointer_b["binding_root"] == str(root.resolve())

    payload_a = _envelope_payload(pointer_a["envelope_path"])
    payload_b = _envelope_payload(pointer_b["envelope_path"])
    assert payload_a["canonical_task_id"] == task_a
    assert payload_b["canonical_task_id"] == task_b
    # The AF authorized worktree travels inside the binding, not as the instance dir.
    assert payload_a["worktree_root"] == str(root.resolve())
    assert payload_b["worktree_root"] == str(root.resolve())

    # Task B's namespace contains ONLY task B's binding material; task A's
    # binding cannot be reached from task B's instance.
    env_b = Path(pointer_b["envelope_path"]).resolve()
    assert env_b.relative_to(Path(dir_b).resolve())
    env_a = Path(pointer_a["envelope_path"]).resolve()
    assert not env_a.is_relative_to(Path(dir_b).resolve())
    assert not env_b.is_relative_to(Path(dir_a).resolve())


def test_stale_task_a_binding_cannot_authorize_task_b(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    resolver = _worker_resolver_with_envelopes(root)
    task_a = "aota_forge:M3:W2:task-a:aaaaaaaa"
    task_b = "aota_forge:M3:W2:task-b:bbbbbbbb"

    dir_a = resolver(_package(task_a))
    pointer_a_before = _pointer(dir_a)

    # Task B dispatch on the SAME worktree.
    dir_b = resolver(_package(task_b))
    assert dir_b != dir_a

    # The old task A instance/binding still exists but is never the resolution
    # for task B; B's binding identity disagrees with A's by construction.
    pointer_b = _pointer(dir_b)
    assert pointer_b["envelope_path"] != pointer_a_before["envelope_path"]
    assert _envelope_payload(pointer_b["envelope_path"])["canonical_task_id"] == task_b
    assert _envelope_payload(pointer_a_before["envelope_path"])["canonical_task_id"] == task_a


def test_role_instance_namespaces_are_disjoint(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    task_id = "aota_forge:M3:W2:same-id:cccccccc"
    worker_dir = instance_directory(root, worker_instance_key(task_id))
    task_main_dir = instance_directory(root, task_main_instance_key("run-1"))
    assert worker_dir != task_main_dir
    assert task_main_dir.name.startswith("task-main-")
    assert worker_dir.name.startswith("worker-")


def test_worker_resolver_fails_closed_for_non_worker_envelope(tmp_path: Path) -> None:
    root = _make_worktree(tmp_path)
    # A task-main envelope must never be returned for a Worker dispatch.
    from aota_forge.runtime.trusted_runtime_binding import create_task_main_envelope

    bootstrap = root / ".aota" / "task-main-thin-bootstrap.json"
    bootstrap.write_text(json.dumps({"runtime_path": "thin"}), encoding="utf-8")
    envelope = create_task_main_envelope(worktree_root=root, bootstrap_path=bootstrap)

    def _resolver(payload):
        return {PRE_RESOLVED_BINDING_ENV: str(envelope)}

    resolver = opencode_worker_directory_resolver(_resolver)
    assert resolver(_package("aota_forge:M3:W2:x:dddddddd")) is None


# ---------------------------------------------------------------------------
# Production per-instance MCP wrapper
# ---------------------------------------------------------------------------


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("m3_opencode_wrapper", WRAPPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stage_instance(root: Path, kind: str, tmp_path: Path, key: str) -> Path:
    instance = instance_directory(root, key)
    payload = {"kind": kind, "worktree_root": str(root.resolve()), "dummy": True}
    envelope = tmp_path / f"{key}.json"
    envelope.write_text(
        json.dumps(
            {"version": "1", "kind": kind, "digest": "0" * 64, "payload": payload},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    staged = stage_envelope_in_instance(instance, envelope)
    if kind == BINDING_KIND_TASK_MAIN:
        bootstrap = root / ".aota" / "task-main-thin-bootstrap.json"
        bootstrap.write_text(json.dumps({"runtime_path": "thin"}), encoding="utf-8")
        write_binding_pointer(
            instance,
            kind=kind,
            envelope_path=staged,
            binding_root=root,
            bootstrap_path=bootstrap,
        )
    else:
        write_binding_pointer(
            instance, kind=kind, envelope_path=staged, binding_root=root
        )
    return instance


def _run_wrapper_capture(module, monkeypatch, env: dict) -> dict:
    captured: dict = {}

    def _fake_execve(path, argv, child_env):
        captured["path"] = path
        captured["argv"] = list(argv)
        captured["env"] = dict(child_env)

    monkeypatch.setattr(module.os, "execve", _fake_execve)
    old = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ[k] = v
    try:
        module.main()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return captured


def test_wrapper_resolves_worker_binding_and_no_synthetic_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", "1")
    root = _make_worktree(tmp_path)
    instance = _stage_instance(root, BINDING_KIND_WORKER, tmp_path, worker_instance_key("t:1"))
    module = _load_wrapper()
    captured = _run_wrapper_capture(
        module,
        monkeypatch,
        {
            "AOTA_OPENCODE_MCP_INSTANCE_DIR": str(instance),
            "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
        },
    )
    assert captured["argv"][-1] == "--mcp-server"
    assert captured["env"][PRE_RESOLVED_BINDING_ENV] == str(
        Path(_pointer(instance)["envelope_path"])
    )
    assert "AOTA_TASK_MAIN_BOOTSTRAP" not in captured["env"]
    # NB-4: the production wrapper never provides the synthetic-evidence seam.
    assert "AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE" not in captured["env"]
    assert module.SYNTHETIC_PROJECT_EVIDENCE_PROVIDED is False


def test_wrapper_resolves_task_main_binding(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    instance = _stage_instance(root, BINDING_KIND_TASK_MAIN, tmp_path, task_main_instance_key("run"))
    module = _load_wrapper()
    captured = _run_wrapper_capture(
        module,
        monkeypatch,
        {
            "AOTA_OPENCODE_MCP_INSTANCE_DIR": str(instance),
            "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
        },
    )
    pointer = _pointer(instance)
    assert captured["env"][PRE_RESOLVED_BINDING_ENV] == str(Path(pointer["envelope_path"]))
    assert captured["env"]["AOTA_TASK_MAIN_BOOTSTRAP"] == str(Path(pointer["bootstrap_path"]))


def test_wrapper_fails_closed_without_pointer(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    instance = instance_directory(root, worker_instance_key("t:1"))
    module = _load_wrapper()
    with pytest.raises(SystemExit):
        _run_wrapper_capture(
            module,
            monkeypatch,
            {
                "AOTA_OPENCODE_MCP_INSTANCE_DIR": str(instance),
                "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
            },
        )


def test_wrapper_fails_closed_on_cross_instance_envelope(tmp_path, monkeypatch) -> None:
    root = _make_worktree(tmp_path)
    instance_a = _stage_instance(root, BINDING_KIND_WORKER, tmp_path, worker_instance_key("t:a"))
    instance_b = instance_directory(root, worker_instance_key("t:b"))
    pointer = _pointer(instance_a)
    # Forge a pointer in B's namespace that references A's envelope.
    pointer_dir = instance_b / ".aota" / "opencode"
    pointer_dir.mkdir(parents=True, exist_ok=True)
    forged = dict(pointer)
    (pointer_dir / "active_binding.json").write_text(json.dumps(forged), encoding="utf-8")
    module = _load_wrapper()
    with pytest.raises(SystemExit):
        _run_wrapper_capture(
            module,
            monkeypatch,
            {
                "AOTA_OPENCODE_MCP_INSTANCE_DIR": str(instance_b),
                "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
            },
        )


def test_production_paths_do_not_default_synthetic_project_evidence() -> None:
    production_sources = [
        REPO_ROOT / "aota_forge" / "composition" / "execution.py",
        REPO_ROOT / "aota_forge" / "composition" / "thin_task_main_host.py",
        REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py",
        REPO_ROOT / "aota_forge" / "adapters" / "opencode" / "task_main.py",
        WRAPPER_PATH,
    ]
    banned_setters = (
        'setdefault("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE"',
        "setdefault('AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE'",
        'AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE"] = "1"',
        "AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE'] = '1'",
        'AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE = "1"',
    )
    for path in production_sources:
        text = path.read_text(encoding="utf-8")
        for banned in banned_setters:
            assert banned not in text, (path, banned)
