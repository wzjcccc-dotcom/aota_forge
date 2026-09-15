"""AF #56 M3/W3 — production authority generality & task-main write boundary.

- Production M3 paths contain no hardcoded Plan/issue/project authority
  (no ``if issue == 56`` / ``aota-reader`` / source-root literal branches):
  the dogfood harness may select issues as operator input, production logic
  stays generic.
- The thin task-main trusted surface exposes read/coordination operations but
  no direct source-mutation operation (task-main workspace.write authority is
  absent; source mutation belongs to a delegated coder Worker).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_PATHS = (
    REPO_ROOT / "aota_forge" / "adapters" / "opencode" / "task_main.py",
    REPO_ROOT / "aota_forge" / "composition" / "execution.py",
    REPO_ROOT / "aota_forge" / "composition" / "task_main_daily_launcher.py",
    REPO_ROOT / "aota_forge" / "composition" / "thin_task_main_host.py",
    REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py",
)

BANNED_PATTERNS = (
    r"==\s*56\b",
    r"!=\s*56\b",
    r"==\s*39\b",
    r"!=\s*39\b",
    r"['\"]aota-reader['\"]",
    r"chatgpt-hermes-mcp-poc",
    r"project_id\s*==\s*['\"]",
    r"if\s+issue(_number)?\s*==",
)


def test_no_hardcoded_plan_or_project_authority_in_m3_production_paths() -> None:
    for path in PRODUCTION_PATHS:
        text = path.read_text(encoding="utf-8")
        for pattern in BANNED_PATTERNS:
            assert not re.search(pattern, text), (path, pattern)


def _make_worktree(tmp_path: Path, project_id: str = "generic_proj") -> Path:
    root = tmp_path / "wt"
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


def _opencode_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "rt-opencode.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "opencode",
                "host_endpoint": "http://127.0.0.1:4096",
                "executable": "/usr/bin/true",
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
        ),
        encoding="utf-8",
    )
    return cfg


def test_thin_task_main_surface_has_no_workspace_write(tmp_path: Path) -> None:
    from aota_forge.composition.thin_task_main_host import (
        THIN_TASK_MAIN_EAGER_OPERATIONS,
        THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS,
        compose_thin_task_main_host,
    )

    root = _make_worktree(tmp_path)
    cfg = _opencode_config(tmp_path)
    host = compose_thin_task_main_host(
        worktree_root=root,
        project_id="generic_proj",
        worktree_id="wt",
        runtime_config_path=cfg,
        origin_task_main_session_ref="ses_generic0001",
        plan_ref="owner/repo#123",
    )
    assert host.trusted_binding.mutation_authority is None
    visible = set(THIN_TASK_MAIN_EAGER_OPERATIONS) | set(THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS)
    assert "workspace.write" not in visible
    # The trusted bootstrap projection reports no direct mutation authority.
    guidance = host.role_guidance()
    assert isinstance(guidance, dict)
    surface = json.dumps(guidance)
    assert "workspace.write" not in surface
    # Coordination + read operations are visible (authorization is minted by
    # the trusted authorities, not by the model).
    assert "task.start" in visible
    assert "handoff.write" in visible
    assert "github.issue.read" in visible
    assert "github.issue.comments.read" in visible


def test_completion_transport_follows_operator_executor(tmp_path: Path) -> None:
    from aota_forge.adapters.opencode import delivery as opencode_delivery
    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

    root = _make_worktree(tmp_path)
    cfg = _opencode_config(tmp_path)
    host = compose_thin_task_main_host(
        worktree_root=root,
        project_id="generic_proj",
        worktree_id="wt",
        runtime_config_path=cfg,
        origin_task_main_session_ref="ses_generic0001",
    )
    assert isinstance(host.completion_transport, opencode_delivery.OpenCodeCompletionDeliveryTransport)


def test_mcp_transport_conversion_preserves_trusted_project_context(tmp_path: Path) -> None:
    """MCP-path role.bootstrap must expose the SAME trusted project context.

    Regression for the M3/W3 dogfood finding: the transport-to-Core binding
    conversion dropped ``authorized_roots``/``source_repository``, so the
    model-visible TRUSTED_PROJECT_CONTEXT lost project-main + SOURCE_REPOSITORY
    even though the trusted binding carried them.
    """
    import dataclasses

    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
    from aota_forge.mcp_transport import _to_canonical_binding
    from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

    root = _make_worktree(tmp_path)
    cfg = _opencode_config(tmp_path)
    host = compose_thin_task_main_host(
        worktree_root=root,
        project_id="generic_proj",
        worktree_id="wt",
        runtime_config_path=cfg,
        origin_task_main_session_ref="ses_generic0001",
        plan_ref="owner/repo#123",
    )
    canonical = _to_canonical_binding(host.trusted_binding)
    result = handle_role_bootstrap(canonical, {})
    projection = result["TRUSTED_PROJECT_CONTEXT"]
    assert projection["project"]["project_id"] == "generic_proj"
    assert projection["project"]["root_ref"] == "project-main"
    assert set(projection["roots"]) == {"project-main", "active-worktree"}
    # Direct-binding and MCP-path projections agree.
    assert projection == handle_role_bootstrap(host.trusted_binding, {})["TRUSTED_PROJECT_CONTEXT"]

    # A grounded SOURCE_REPOSITORY carrier reaches the model-visible projection
    # through the same conversion (mechanical carrier only).
    with_source = dataclasses.replace(canonical, source_repository="owner/source_repo")
    assert (
        handle_role_bootstrap(with_source, {})["TRUSTED_PROJECT_CONTEXT"]["project"][
            "source_repository"
        ]
        == "owner/source_repo"
    )

    # The mechanical carriers are the only difference; no other conversion path
    # exists (single transport-to-Core conversion).
    without = dataclasses.replace(canonical, authorized_roots=None, source_repository="")
    fallback = handle_role_bootstrap(without, {})["TRUSTED_PROJECT_CONTEXT"]
    assert set(fallback["roots"]) == {"active-worktree"}
