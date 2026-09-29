"""Plan #003 M3/W3-R2 bounded Worker binding integration."""

from __future__ import annotations

from pathlib import Path

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.mcp_transport import _SharedAotaMcpAdapter
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff


def test_analyst_binding_bootstrap_search_read_and_write_denial(tmp_path: Path) -> None:
    (tmp_path / "evidence.txt").write_text("canonical owner durable type\n", encoding="utf-8")
    handoff = TaskHandoff(
        work_role="analyst",
        task_kind="plan003-m3-w3-r2-integration",
        objective="inspect bounded source evidence",
        bounded_scope="read-only source inspection",
        validation_expectations=("focused validation",),
        semantic_stop_expectations=("stop on authority denial",),
        project_ref=SemanticReference(ref="aota_forge"),
        milestone_ref=SemanticReference(ref="M3"),
        work_item_ref=SemanticReference(ref="M3/W3"),
    )
    binding = build_worker_binding(
        root=tmp_path,
        project_id="aota_forge",
        worktree_id="wt-r2-integration",
        canonical_task_id=(
            "aota_forge:plan_003_interactive_session_first_context_repair:"
            "M3:M3/W3:attempt-r2"
        ),
        handoff=handoff,
    )
    adapter = _SharedAotaMcpAdapter(binding)

    bootstrap = adapter.invoke("role.bootstrap", {})
    search = adapter.invoke("workspace.search", {"query": "canonical owner", "root_ref": "active-worktree"})
    read = adapter.invoke("workspace.read", {"path": "evidence.txt", "root_ref": "active-worktree", "max_bytes": 200})
    denied_write = adapter.invoke(
        "workspace.write",
        {"path": "evidence.txt", "content": "mutation", "mode": "replace_existing"},
    )

    assert bootstrap["ok"] is True, bootstrap
    assert search["ok"] is True, search
    assert read["ok"] is True, read
    assert read["payload"]["content"] == "canonical owner durable type\n"
    assert denied_write["ok"] is False
    assert denied_write["error"]["code"] == "AUTHORITY_DENIED"
    assert (tmp_path / "evidence.txt").read_text(encoding="utf-8") == "canonical owner durable type\n"
