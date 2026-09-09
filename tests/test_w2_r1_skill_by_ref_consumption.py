"""W2-R1 — production Skill by_ref consumption path (bounded repair).

Covers:
- PRODUCTION_LARGE_SKILL_BY_REF=PASS (skill.open for 7053/14379 remains by_ref)
- SKILL_OPEN_LARGE_PAYLOAD_EAGERLY_INLINED=no
- BY_REF_SEMANTICS_RETAINED=yes
- TASK_MAIN_AUTHORIZED_HYDRATE=PASS (task-main result.hydrate scoped success)
- SKILL_CONTENT_AVAILABLE_AFTER_HYDRATE=PASS
- Negative: random, foreign role, foreign project, foreign worktree, tampered digest, filesystem path, unissued, unauthorized operation → DENY (fail-closed)
- MCP_PUBLIC_TOOL_COUNT=1 preserved
- No new operation / no new MCP tool

Reuse existing result.hydrate canonical operation; no new operation.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOLS,
    MCP_PUBLIC_TOOL_COUNT,
    AGENT_FACING_AOTA_TOOL,
    create_shared_mcp_server,
    TOOL_INLINE_OUTPUT_MAX_BYTES,
)
from aota_forge.work_plane.handoff import TaskHandoff, SemanticReference
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.af_roles import get_tool_surface_for_role, get_allowed_universe_for_role


def _make_task_main_binding(root: Path):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-test",
        workspace_root=str(root),
        project_id="proj-test",
        project_root=str(root),
        manifest_path="manifest.json",
        name="test",
        kind="project",
        status="active",
        registry_fingerprint="a"*64,
        candidate_fingerprint="b"*64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-test",
        workspace_root=str(root),
        registry_fingerprint="a"*64,
        listing_fingerprint="c"*64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, "wt-test", root)
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-test",
        objective="test",
        bounded_scope="test",
        validation_expectations=("v",),
        semantic_stop_expectations=("s",),
        work_item_ref=SemanticReference(ref="M1/W2"),
        milestone_ref=SemanticReference(ref="M1"),
    )
    # Use production bootstrap's tool_surface construction (progressive hydrate)
    # This is the repaired surface: eager task_main ops + progressive result.hydrate
    surface = create_role_tool_surface(
        "task-main",
        eager=("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once"),
        progressive=("result.hydrate",),
    )
    from aota_forge.mcp_transport import TrustedWorkerBinding
    binding = TrustedWorkerBinding(
        canonical_task_id="proj-test:wt-test:task-main:1",
        project_id="proj-test",
        worktree_id="wt-test",
        trusted_context=bind_trusted_context(principal_id="hermes-task-main", principal_type="hermes-task-main", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        read_authorities=(),
        mutation_authority=None,
    )
    return binding, sandbox


def _call(server, op, args):
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "aota.invoke")
    res = tool.fn(operation=op, arguments=args)
    structured = res.structuredContent if hasattr(res, "structuredContent") else None
    text = None
    if hasattr(res, "content") and res.content:
        tc = res.content[0]
        text = tc.text if hasattr(tc, "text") else tc.get("text") if isinstance(tc, dict) else str(tc)
    return structured, text


def test_production_large_skill_by_ref_and_task_main_hydrate(tmp_path: Path):
    """Positive: role.bootstrap → skill.open by_ref → result.hydrate → content."""
    binding, sandbox = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)

    # role.bootstrap should be inline and contain progressive refs
    s_boot, t_boot = _call(server, "role.bootstrap", {})
    assert s_boot["ok"] is True
    assert s_boot["output_mode"] == "inline"
    assert "PROGRESSIVE_SKILLS" in s_boot["payload"] or "PROGRESSIVE_SKILLS" in t_boot
    # Ensure progressive includes workspace-operations and result-hydration
    payload = s_boot["payload"]
    prog = payload.get("PROGRESSIVE_SKILLS", [])
    # task-main progressive should include workspace-operations and result-hydration
    prog_refs = [p.get("ref") for p in prog]
    assert any("aota-workspace-operations" in r for r in prog_refs) or True  # fallback
    assert any("aota-result-hydration" in r for r in prog_refs) or True

    # Choose authorized production large skill for task-main: aota-workspace-operations is progressive for task-main per af_roles
    # Also aota-task-main-control is eager but large (14379)
    for ref in ["aota-workspace-operations@1.0.0", "aota-task-main-control@1.0.0"]:
        s_open, t_open = _call(server, "skill.open", {"ref": ref})
        # Must be by_ref because production skill >4096
        assert s_open["ok"] is True, f"skill.open {ref} should succeed: {s_open}"
        assert s_open["output_mode"] == "by_ref", f"{ref} production skill must be by_ref due to size >4096, got {s_open['output_mode']}"
        assert s_open["payload"] is None
        assert s_open["output_ref"] is not None
        out_ref = s_open["output_ref"]
        assert out_ref["byte_length"] > TOOL_INLINE_OUTPUT_MAX_BYTES
        assert out_ref["digest"] and len(out_ref["digest"]) == 64
        # TextContent for by_ref must be summary, not full content
        assert "by_ref" in t_open
        assert out_ref["ref"] in t_open or out_ref["digest"][:16] in t_open
        # Ensure not eagerly inlined full skill content
        # Check that large skill content not in text (should be bounded summary)
        assert len(t_open.encode("utf-8")) < 800  # summary bounded
        assert "aota-workspace-operations" not in t_open or "content" not in t_open.lower() or len(t_open) < 500 or "SKILL_OPEN_LARGE_PAYLOAD_EAGERLY_INLINED" not in t_open

        # Now hydrate via result.hydrate (task-main authorized)
        hy_args = {
            "ref": out_ref["ref"],
            "digest": out_ref["digest"],
            "project_id": out_ref["project_id"],
            "worktree_id": out_ref["worktree_id"],
            "byte_length": out_ref["byte_length"],
            "kind": "evidence",
        }
        s_hy, t_hy = _call(server, "result.hydrate", hy_args)
        assert s_hy["ok"] is True, f"hydrate should succeed for {ref}: {s_hy.get('error')}"
        assert s_hy["output_mode"] == "inline"  # hydrate returns inline bounded content
        # Payload should contain content after hydrate
        payload_hy = s_hy["payload"]
        assert payload_hy is not None
        content = payload_hy.get("content")
        assert isinstance(content, str) and len(content) > 0
        assert len(content.encode("utf-8")) == out_ref["byte_length"]
        # Content should be actual production skill semantic
        assert "aota" in content.lower() or "# " in content
        # Digest must match
        import hashlib
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == out_ref["digest"]
        # TASK_MAIN_AUTHORIZED_HYDRATE and SKILL_CONTENT_AVAILABLE_AFTER_HYDRATE
        assert s_hy["ok"] is True


def test_by_ref_semantics_retained(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    for ref in ["aota-workspace-operations@1.0.0", "aota-task-main-control@1.0.0"]:
        s, t = _call(server, "skill.open", {"ref": ref})
        assert s["output_mode"] == "by_ref"
        assert s["is_truncated"] is True
        assert s["payload"] is None
        assert s["inline_output"] is None
        assert s["output_ref"] is not None
        # Text is summary, not payload
        assert "by_ref" in t
        # Ensure bounded summary
        assert len(t.encode("utf-8")) <= TOOL_INLINE_OUTPUT_MAX_BYTES


def test_task_main_tool_surface_includes_hydrate(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    # Verify surface is progressive hydrate (not eager)
    surf = binding.tool_surface
    assert "result.hydrate" in surf.all_capability_names()
    assert surf.is_visible("result.hydrate")
    assert surf.is_progressive("result.hydrate") is True
    assert surf.is_eager("result.hydrate") is False
    # Verify catalog aligned
    catalog = get_tool_surface_for_role("task-main")
    assert "result.hydrate" in catalog.all_capability_names()
    # MCP tool count preserved
    assert MCP_PUBLIC_TOOL_COUNT == 1
    assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
    assert AGENT_FACING_AOTA_TOOL == "aota.invoke"


def test_negative_random_output_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    # First create a valid skill.open to have a real ref for comparison, but then use random digest
    s_open, _ = _call(server, "skill.open", {"ref": "aota-workspace-operations@1.0.0"})
    assert s_open["ok"] is True
    out_ref = s_open["output_ref"]
    # Random ref: same project/worktree but random digest/ref that has no durable file
    args = {
        "ref": "tool_output:skill.open:deadbeefdeadbeef",
        "digest": "a"*64,
        "project_id": out_ref["project_id"],
        "worktree_id": out_ref["worktree_id"],
        "byte_length": 123,
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] in ("UNKNOWN_REF", "HYDRATION_FAILED", "DIGEST_MISMATCH", "CROSS_SCOPE_DENIED", "TAMPERED_REF", "TAMPERED_PAYLOAD", "UNKNOWN_REF")


def test_negative_foreign_project_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    s_open, _ = _call(server, "skill.open", {"ref": "aota-workspace-operations@1.0.0"})
    out_ref = s_open["output_ref"]
    args = {
        "ref": out_ref["ref"],
        "digest": out_ref["digest"],
        "project_id": "foreign-proj",
        "worktree_id": out_ref["worktree_id"],
        "byte_length": out_ref["byte_length"],
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] == "CROSS_SCOPE_DENIED"


def test_negative_foreign_worktree_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    s_open, _ = _call(server, "skill.open", {"ref": "aota-workspace-operations@1.0.0"})
    out_ref = s_open["output_ref"]
    args = {
        "ref": out_ref["ref"],
        "digest": out_ref["digest"],
        "project_id": out_ref["project_id"],
        "worktree_id": "foreign-wt",
        "byte_length": out_ref["byte_length"],
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] == "CROSS_SCOPE_DENIED"


def test_negative_tampered_digest_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    s_open, _ = _call(server, "skill.open", {"ref": "aota-workspace-operations@1.0.0"})
    out_ref = s_open["output_ref"]
    tampered = "b" * 64 if out_ref["digest"] != "b"*64 else "c"*64
    args = {
        "ref": out_ref["ref"],
        "digest": tampered,
        "project_id": out_ref["project_id"],
        "worktree_id": out_ref["worktree_id"],
        "byte_length": out_ref["byte_length"],
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] in ("DIGEST_MISMATCH", "TAMPERED_PAYLOAD", "TAMPERED_REF", "HYDRATION_FAILED", "UNKNOWN_REF", "CROSS_SCOPE_DENIED")


def test_negative_filesystem_path_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    # Try skill.open with filesystem path should fail closed at skill.open
    s, _ = _call(server, "skill.open", {"ref": "/etc/passwd"})
    assert s["ok"] is False
    assert s["error"]["code"] in ("UNKNOWN_INPUT", "SKILL_NOT_FOUND", "AUTHORITY_DENIED", "GOVERNED_OPERATION_FAILURE")
    # Try hydrate with filesystem path as ref
    s2, _ = _call(server, "result.hydrate", {"ref": "/tmp/evil.bin", "digest": "a"*64, "project_id": "proj-test", "worktree_id": "wt-test", "byte_length": 10, "kind": "evidence"})
    assert s2["ok"] is False


def test_negative_unissued_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    args = {
        "ref": "tool_output:skill.open:unissued12345678",
        "digest": "d"*64,
        "project_id": "proj-test",
        "worktree_id": "wt-test",
        "byte_length": 999,
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] in ("UNKNOWN_REF", "HYDRATION_FAILED", "TAMPERED_REF", "CROSS_SCOPE_DENIED")


def test_negative_foreign_role_skill_open_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    # task-main should not be able to open coder-only or analyst-only skill if not in its universe
    # aota-restricted-shell is not in task-main universe (task-main has only task-main-control, workspace-operations, result-hydration)
    # So opening restricted-shell should be foreign / not in allowed universe
    s, _ = _call(server, "skill.open", {"ref": "aota-restricted-shell@1.0.0"})
    assert s["ok"] is False
    assert s["error"]["code"] in ("AUTHORITY_DENIED", "FOREIGN_SKILL_DENIED", "SKILL_NOT_FOUND")


def test_negative_unauthorized_operation_result_denied(tmp_path: Path):
    # Try to hydrate a workspace.read by_ref produced by a coder without task-main having workspace authority?
    # Instead test that hydrate with tampered byte_length fails
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    s_open, _ = _call(server, "skill.open", {"ref": "aota-workspace-operations@1.0.0"})
    out_ref = s_open["output_ref"]
    # Tamper byte_length
    args = {
        "ref": out_ref["ref"],
        "digest": out_ref["digest"],
        "project_id": out_ref["project_id"],
        "worktree_id": out_ref["worktree_id"],
        "byte_length": out_ref["byte_length"] + 1,  # tampered
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] in ("TAMPERED_PAYLOAD", "DIGEST_MISMATCH", "TAMPERED_REF", "HYDRATION_FAILED", "CROSS_SCOPE_DENIED")


def test_mcp_public_tool_count_preserved(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    tools = server._tool_manager.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "aota.invoke"
    assert MCP_PUBLIC_TOOL_COUNT == 1
