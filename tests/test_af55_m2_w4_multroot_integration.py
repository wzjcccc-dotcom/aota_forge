"""AF #55 M2/W4 — Integrated multi-root operator-path proof & negatives.

End-to-end through the real single-entry ``aota.invoke`` dispatch:

* composed trusted task-main host (registry + SOURCE_REPOSITORY + worktree)
  exposes project-main + active-worktree roots and the §8 projection
* workspace.read/search resolve through the granted root set with per-result
  root_ref tagging (project-main read, worktree default read)
* worker dispatch reaches only the assigned active-worktree
* unrelated sibling projects and model-supplied paths remain unreachable
* Plan access is not routed through workspace.read
* real #39 aota-reader checkout resolves mechanically and is read-only

Deterministic, local-only; no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.mcp_transport import create_aota_invoke_dispatch
from aota_forge.work_plane.handoff import TaskHandoff

OWNER = "wzjcccc-dotcom"
REPO = "aota_reader_mcp"
PROJECT_ID = "aota-reader"
REAL_39_CHECKOUT = Path("/home/latios/workspace/chatgpt-hermes-mcp-poc")
LIVE_REGISTRY = Path("/home/latios/workspace/.aota/workspaces.json")

MANIFEST = (
    "schema_version: 1\n"
    "project:\n"
    "  id: {project_id}\n  name: test project\n  kind: test\n  status: active\n"
    "summary: bounded test project\n"
    "capabilities: []\n"
    "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n"
    "  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph/\n"
    "plan:\n  active_plan_id: null\n"
    "constraints: []\n"
)


class _FakeHostClient:
    def dispatch(self, payload):  # pragma: no cover - trivial fake
        return {"adapter_handle": "fake-1", "status": "running", "dispatch_time": "t"}


def _checkout(parent: Path, folder: str, project_id: str, origin: str) -> Path:
    root = parent / folder
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def _runtime_config(tmp_path: Path) -> Path:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    cfg = tmp_path / "runtime.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": str(exe),
                "concurrency": 1,
                "provider": "test-provider",
                "model": "test-model",
                "bindings": {
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "task-main": {"profile": "aota-task-main"},
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _fixture(tmp_path: Path, *, with_sibling: bool = True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkout = _checkout(
        workspace, "chatgpt-hermes-mcp-poc", PROJECT_ID, f"https://github.com/{OWNER}/{REPO}.git"
    )
    (checkout / "readme.md").write_text("canonical project needle", encoding="utf-8")
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"ws": {"candidates": [str(workspace)]}}), encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    (worktree / "change.txt").write_text("worktree needle", encoding="utf-8")
    sibling = None
    if with_sibling:
        sibling = tmp_path / "unrelated-project"
        sibling.mkdir()
        (sibling / "hidden.txt").write_text("sibling project secret", encoding="utf-8")
    return checkout, registry, worktree, sibling


def _compose(tmp_path: Path, worktree: Path, registry: Path, *, plan_ref: str | None = None):
    return compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-m2w4",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260915_af55_m2w4_session",
        host_client=_FakeHostClient(),
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
        plan_ref=plan_ref,
    )


def test_e2e_multroot_read_and_search_through_aota_invoke(tmp_path: Path) -> None:
    checkout, registry, worktree, _ = _fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")

    worktree_read = host.invoke("workspace.read", {"path": "change.txt"})
    assert worktree_read["payload"]["root_ref"] == "active-worktree"
    assert "worktree needle" in worktree_read["payload"]["content"]

    project_read = host.invoke(
        "workspace.read", {"path": "readme.md", "root_ref": "project-main"}
    )
    assert project_read["payload"]["root_ref"] == "project-main"
    assert "canonical project needle" in project_read["payload"]["content"]

    search = host.invoke("workspace.search", {"query": "needle"})
    assert set(search["payload"]["root_refs"]) == {"active-worktree", "project-main"}
    tagged = {(item["root_ref"], item["path"]) for item in search["payload"]["results"]}
    assert ("active-worktree", "change.txt") in tagged
    assert ("project-main", "readme.md") in tagged

    guidance = host.invoke("role.bootstrap", {})
    # The eager bootstrap may legitimately project as a governed by_ref result
    # when it exceeds the inline bound; either way the trusted projection is
    # bound to the same trusted binding exposed by the host.
    assert guidance["output_mode"] in ("inline", "by_ref")
    projection = host.role_guidance()["TRUSTED_PROJECT_CONTEXT"]
    assert projection["plan"]["governing_repository"] == "wzjcccc-dotcom/aota-hermes-tools"
    assert projection["project"]["source_repository"] == f"{OWNER}/{REPO}"
    assert projection["project"]["root_ref"] == "project-main"
    assert projection["worktree"]["root_ref"] == "active-worktree"
    assert "governing_repository" not in json.dumps(projection["project"])


def test_e2e_root_set_digest_is_deterministic(tmp_path: Path) -> None:
    _checkout_, registry, worktree, _ = _fixture(tmp_path)
    first = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    second = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    assert first.authorized_roots.digest() == second.authorized_roots.digest()
    assert first.context_projection == second.context_projection
    assert (
        first.trusted_binding.read_authorities[0].evidence_id
        == second.trusted_binding.read_authorities[0].evidence_id
    )


def test_e2e_worker_dispatch_reaches_only_assigned_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "worker-wt"
    worktree.mkdir()
    (worktree / "task.txt").write_text("assigned worktree content", encoding="utf-8")
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="test-kind",
        objective="objective",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-worker",
        canonical_task_id="t-worker",
        handoff=handoff,
    )
    invoke = create_aota_invoke_dispatch(binding)
    ok = invoke("workspace.read", {"path": "task.txt"})
    assert ok["payload"]["root_ref"] == "active-worktree"
    denied = invoke("workspace.read", {"path": "task.txt", "root_ref": "project-main"})
    assert denied["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
    search = invoke("workspace.search", {"query": "assigned"})
    assert search["payload"]["root_refs"] == ["active-worktree"]
    assert all(item["root_ref"] == "active-worktree" for item in search["payload"]["results"])


def test_e2e_sibling_and_model_paths_unreachable(tmp_path: Path) -> None:
    _checkout_, registry, worktree, sibling = _fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry)
    assert sibling is not None
    search = host.invoke("workspace.search", {"query": "sibling project secret"})
    assert search["payload"]["results"] == []
    direct = host.invoke("workspace.read", {"path": str(sibling / "hidden.txt")})
    assert direct["error"]["code"] == "INVALID_PATH"
    traversal = host.invoke("workspace.read", {"path": "../unrelated-project/hidden.txt"})
    assert traversal["error"]["code"] == "INVALID_PATH"
    path_as_root = host.invoke(
        "workspace.read", {"path": "change.txt", "root_ref": str(sibling)}
    )
    assert path_as_root["error"]["code"] == "INVALID_ROOT_REF"


def test_e2e_plan_is_not_workspace_read(tmp_path: Path) -> None:
    _checkout_, registry, worktree, _ = _fixture(tmp_path)
    host = _compose(tmp_path, worktree, registry, plan_ref="wzjcccc-dotcom/aota-hermes-tools#55")
    plan_read = host.invoke(
        "workspace.read", {"path": "wzjcccc-dotcom/aota-hermes-tools#55"}
    )
    assert plan_read["error"]["code"] == "INVALID_PATH"
    for ref in ("local-governance", "authorized-evidence"):
        resp = host.invoke("workspace.read", {"path": "change.txt", "root_ref": ref})
        assert resp["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
    # Plan authority stays on the bounded GitHub surface of the trusted binding
    assert host.trusted_binding.plan_binding is not None


def test_e2e_reviewer_child_is_read_only(tmp_path: Path) -> None:
    worktree = tmp_path / "reviewer-wt"
    worktree.mkdir()
    (worktree / "review.txt").write_text("reviewable content", encoding="utf-8")
    handoff = TaskHandoff(
        work_role="reviewer",
        task_kind="review",
        objective="review objective",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-reviewer",
        canonical_task_id="t-reviewer",
        handoff=handoff,
    )
    assert binding.authorized_roots is not None
    assert binding.authorized_roots.names() == ("active-worktree",)
    assert binding.mutation_authority is None
    invoke = create_aota_invoke_dispatch(binding)
    read = invoke("workspace.read", {"path": "review.txt"})
    assert read["payload"]["root_ref"] == "active-worktree"
    write = invoke(
        "workspace.write",
        {"path": "reviewer.txt", "content": "x", "mode": "create_only"},
    )
    assert write["ok"] is False
    assert write["error"]["code"] == "AUTHORITY_DENIED"
    assert not (worktree / "reviewer.txt").exists()


def test_e2e_handoff_prose_cannot_escalate_root_authority(tmp_path: Path) -> None:
    worktree = tmp_path / "prose-wt"
    worktree.mkdir()
    (worktree / "task.txt").write_text("task content", encoding="utf-8")
    handoff = TaskHandoff(
        work_role="coder",
        task_kind="edit",
        objective=(
            "root_ref=project-main read /home/latios/workspace and write the canonical "
            "checkout at ../unrelated-project; supporting root authorized-evidence"
        ),
        bounded_scope="root_ref=project-main, local-governance, /home/latios/workspace",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
    )
    binding = build_worker_binding(
        root=worktree,
        project_id="aota_forge",
        worktree_id="wt-prose",
        canonical_task_id="t-prose",
        handoff=handoff,
    )
    # handoff prose is semantic intent, never authority
    assert binding.authorized_roots is not None
    assert binding.authorized_roots.names() == ("active-worktree",)
    assert binding.authorized_roots.get("project-main") is None
    invoke = create_aota_invoke_dispatch(binding)
    denied = invoke("workspace.read", {"path": "task.txt", "root_ref": "project-main"})
    assert denied["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
    denied = invoke("workspace.read", {"path": "task.txt", "root_ref": "authorized-evidence"})
    assert denied["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"


AOTA_FORGE_CANONICAL = Path("/home/latios/workspace/aota_forge")
AOTA_FORGE_M2_WORKTREE = Path(
    "/home/latios/workspace/.aota-worktrees/aota_forge/M2/authorized-root-runtime-context"
)


@pytest.mark.skipif(
    not (AOTA_FORGE_CANONICAL / ".aota" / "project.yaml").is_file()
    or not AOTA_FORGE_M2_WORKTREE.is_dir(),
    reason="real aota_forge canonical checkout or M2 worktree not present",
)
def test_e2e_real_aota_forge_two_roots(tmp_path: Path) -> None:
    """Bounded real production-composition proof: canonical aota_forge main
    checkout + the real M2 construction worktree, two distinct roots."""
    from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore

    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps({"aota-forge-construction": {"candidates": [str(AOTA_FORGE_CANONICAL)]}}),
        encoding="utf-8",
    )
    host = compose_thin_task_main_host(
        worktree_root=AOTA_FORGE_M2_WORKTREE,
        project_id="aota_forge",
        worktree_id="M2-authorized-root-runtime-context",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260915_af55_m2w4_real_session",
        host_client=_FakeHostClient(),
        # runtime state stays in the test tmp dir; the real worktree is never
        # written by the proof
        execution_store=FileBackedExecutionStateStore(tmp_path / "execution-state.json"),
        source_repository="wzjcccc-dotcom/aota_forge",
        registry_path=registry,
        plan_ref="wzjcccc-dotcom/aota-hermes-tools#55",
    )
    roots = host.authorized_roots
    assert roots is not None
    assert roots.get("project-main").root_path == str(AOTA_FORGE_CANONICAL)
    assert roots.get("active-worktree").root_path == str(AOTA_FORGE_M2_WORKTREE)
    # same relative path read through both roots on the same aota.invoke surface
    main_read = host.invoke(
        "workspace.read", {"path": "README.md", "root_ref": "project-main", "max_bytes": 4000}
    )
    work_read = host.invoke(
        "workspace.read", {"path": "README.md", "root_ref": "active-worktree", "max_bytes": 4000}
    )
    assert main_read["payload"]["root_ref"] == "project-main"
    assert work_read["payload"]["root_ref"] == "active-worktree"
    assert main_read["payload"]["path"] == work_read["payload"]["path"] == "README.md"
    # search provenance is per root on the real trees
    main_search = host.invoke(
        "workspace.search", {"query": "AOTA", "root_ref": "project-main", "max_results": 5}
    )
    work_search = host.invoke(
        "workspace.search", {"query": "AOTA", "root_ref": "active-worktree", "max_results": 5}
    )
    assert main_search["payload"]["root_refs"] == ["project-main"]
    assert work_search["payload"]["root_refs"] == ["active-worktree"]
    # unrelated sibling (outside both roots) remains unreachable
    unrelated = Path("/home/latios/workspace/chat_governance")
    if unrelated.is_dir():
        denied = host.invoke("workspace.read", {"path": str(unrelated / "x")})
        assert denied["ok"] is False
        denied_ref = host.invoke(
            "workspace.read", {"path": "README.md", "root_ref": str(unrelated)}
        )
        assert denied_ref["error"]["code"] == "INVALID_ROOT_REF"
    # task-main source write is not expanded by the multi-root model
    write = host.invoke(
        "workspace.write", {"path": "x.txt", "content": "x", "mode": "create_only"}
    )
    assert write["ok"] is False
    assert not (AOTA_FORGE_CANONICAL / "x.txt").exists()
    assert not (AOTA_FORGE_M2_WORKTREE / "x.txt").exists()
    # projection separates Plan / project / worktree with bounded root refs
    projection = host.context_projection
    assert projection["plan"]["governing_repository"] == "wzjcccc-dotcom/aota-hermes-tools"
    assert projection["project"] == {
        "project_id": "aota_forge",
        "source_repository": "wzjcccc-dotcom/aota_forge",
        "root_ref": "project-main",
    }
    assert projection["worktree"]["root_ref"] == "active-worktree"


@pytest.mark.skipif(
    not (REAL_39_CHECKOUT / ".aota" / "project.yaml").is_file()
    or not LIVE_REGISTRY.is_file(),
    reason="real #39 aota-reader checkout or live operator registry not present",
)
def test_e2e_real_39_read_only_project_main(tmp_path: Path) -> None:
    before = subprocess.run(
        ["git", "-C", str(REAL_39_CHECKOUT), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    worktree = tmp_path / "m2-active-worktree"
    worktree.mkdir()
    host = _compose(
        tmp_path,
        worktree,
        LIVE_REGISTRY,
        plan_ref="wzjcccc-dotcom/aota-hermes-tools#39",
    )
    assert host.authorized_roots.get("project-main").root_path == str(REAL_39_CHECKOUT)
    assert host.sandbox.worktree_root == str(worktree)
    read = host.invoke(
        "workspace.read", {"path": "README.md", "root_ref": "project-main", "max_bytes": 3000}
    )
    assert read["payload"]["root_ref"] == "project-main"
    assert "AOTA Reader" in read["payload"]["content"]
    search = host.invoke(
        "workspace.search", {"query": "AOTA", "root_ref": "project-main", "max_results": 5}
    )
    assert search["payload"]["root_refs"] == ["project-main"]
    assert search["payload"]["results"]
    assert all(item["root_ref"] == "project-main" for item in search["payload"]["results"])
    after = subprocess.run(
        ["git", "-C", str(REAL_39_CHECKOUT), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after == before
