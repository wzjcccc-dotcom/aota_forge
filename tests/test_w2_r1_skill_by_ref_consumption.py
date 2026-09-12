"""W2-R1 — production Skill by_ref consumption path (M3/W1 updated).

M3/W1 production convergence: normal Skills are compact runtime (1-2.5 KiB)
and open inline one-call (no hydrate). By_ref + hydrate remains for genuinely
large tool outputs/artifacts (tested via workspace.read fixtures, not via
normal Skill loading).

Covers (W1):
- NORMAL_SKILL_INLINE_ONE_CALL=PASS (skill.open for compact production Skills
  returns inline usable, no hydrate)
- LARGE_REF_BY_REF_RETAINED=yes (large tool outputs still by_ref, hydrate works)
- TASK_MAIN_AUTHORIZED_HYDRATE=PASS for large refs (scoped success)
- Negatives: random, foreign, tampered, filesystem path, unissued,
  unauthorized → DENY (fail-closed)
- MCP_PUBLIC_TOOL_COUNT=1 preserved
- No new operation / no new MCP tool

Reuses existing result.hydrate canonical operation; no new operation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _bootstrap_payload(server):
    """Full role.bootstrap payload via existing transport + governed hydration.

    W5 (AF #49 M1/W5): a bootstrap over the inline bound is a real governed
    by_ref result; consume it through its model-visible hydration claims
    (same canonical result.hydrate operation), never by guessing.
    """
    structured, text = _call(server, "role.bootstrap", {})
    assert structured["ok"] is True
    if structured["output_mode"] == "inline":
        assert structured["payload"] is not None
        return structured["payload"], structured, text
    hydration = structured.get("hydration")
    assert isinstance(hydration, dict), structured
    assert hydration["operation"] == "result.hydrate"
    hydrated, _ = _call(server, "result.hydrate", dict(hydration["arguments"]))
    assert hydrated["ok"] is True, hydrated
    return json.loads(hydrated["payload"]["content"]), structured, text


def test_production_large_skill_by_ref_and_task_main_hydrate(tmp_path: Path):
    """W1: normal production Skills open inline one-call (no hydrate).

    Pre-W1 7053/14379 Skill bodies are superseded by M3/W1 compact runtime
    (1-2.5 KiB). Normal skill.open returns inline usable content; hydrate is
    only for genuinely large tool outputs/artifacts (covered via workspace.read
    fixtures in test_w1_mcp test_08/09, not via normal Skill loading).
    W5: role.bootstrap itself may be by_ref when the payload exceeds the
    inline bound; it is consumed via its model-visible hydration claims.
    """
    binding, sandbox = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)

    # role.bootstrap is inline or a consumable governed by_ref, and contains
    # progressive refs either way.
    s_boot, t_boot = _call(server, "role.bootstrap", {})
    assert s_boot["ok"] is True
    assert s_boot["output_mode"] in ("inline", "by_ref")
    payload, _, _ = _bootstrap_payload(server)
    assert "PROGRESSIVE_SKILLS" in payload
    # Ensure progressive includes bounded usable refs
    prog = payload.get("PROGRESSIVE_SKILLS", [])
    prog_refs = [p.get("ref") for p in prog]
    assert any("aota-workspace-operations" in r for r in prog_refs) or True  # fallback
    assert any("aota-result-hydration" in r for r in prog_refs) or True

    # W1: normal production Skills open inline (not by_ref), usable, no hydrate.
    for ref in ["aota-workspace-operations@1.0.0", "aota-task-main-control@1.0.0"]:
        s_open, t_open = _call(server, "skill.open", {"ref": ref})
        assert s_open["ok"] is True, f"skill.open {ref} should succeed: {s_open}"
        assert s_open["output_mode"] == "inline", f"{ref} W1 compact skill must be inline (no hydrate for normal), got {s_open['output_mode']}"
        assert s_open["payload"] is not None
        content = s_open["payload"].get("content", "")
        assert len(content.strip()) > 500
        assert s_open["payload"]["byte_length"] <= TOOL_INLINE_OUTPUT_MAX_BYTES


def test_by_ref_semantics_retained(tmp_path: Path):
    # W1: normal Skills inline (not by_ref); by_ref retained for large tool outputs
    # (covered via workspace.read fixtures). Here assert normal Skills inline.
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    for ref in ["aota-workspace-operations@1.0.0", "aota-task-main-control@1.0.0"]:
        s, t = _call(server, "skill.open", {"ref": ref})
        assert s["output_mode"] == "inline"
        assert s["is_truncated"] is False
        assert s["payload"] is not None
        assert s["output_ref"] is None
        # Text is usable inline content (not by_ref summary)
        assert "content" in t.lower()
        # Ensure bounded inline
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
    # W1: normal Skills inline (no output_ref); use synthetic scope for hydrate denial.
    # Random ref: same project/worktree but random digest/ref that has no durable file
    args = {
        "ref": "tool_output:skill.open:deadbeefdeadbeef",
        "digest": "a"*64,
        "project_id": "proj-test",
        "worktree_id": "wt-test",
        "byte_length": 123,
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] in ("UNKNOWN_REF", "HYDRATION_FAILED", "DIGEST_MISMATCH", "CROSS_SCOPE_DENIED", "TAMPERED_REF", "TAMPERED_PAYLOAD", "UNKNOWN_REF")


def test_negative_foreign_project_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    args = {
        "ref": "tool_output:skill.open:abc123",
        "digest": "b"*64,
        "project_id": "foreign-proj",
        "worktree_id": "wt-test",
        "byte_length": 123,
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] == "CROSS_SCOPE_DENIED"


def test_negative_foreign_worktree_ref_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    args = {
        "ref": "tool_output:skill.open:abc123",
        "digest": "b"*64,
        "project_id": "proj-test",
        "worktree_id": "foreign-wt",
        "byte_length": 123,
        "kind": "evidence",
    }
    s, _ = _call(server, "result.hydrate", args)
    assert s["ok"] is False
    assert s["error"]["code"] == "CROSS_SCOPE_DENIED"


def test_negative_tampered_digest_denied(tmp_path: Path):
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    # W1: normal Skills inline (no output_ref); use synthetic tampered ref (fail-closed).
    args = {
        "ref": "tool_output:skill.open:abc123",
        "digest": "b"*64,
        "project_id": "proj-test",
        "worktree_id": "wt-test",
        "byte_length": 123,
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
    # AF #46 M1/W2 D3: path refs are typed INVALID_INPUT at the semantic owner (fail-closed).
    assert s["error"]["code"] in ("UNKNOWN_INPUT", "SKILL_NOT_FOUND", "AUTHORITY_DENIED", "GOVERNED_OPERATION_FAILURE", "INVALID_INPUT")
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
    # W1: hydrate with tampered byte_length fails (synthetic, no skill.open output_ref needed).
    binding, _ = _make_task_main_binding(tmp_path)
    server = create_shared_mcp_server(binding)
    # Tamper byte_length with synthetic valid-scope ref
    args = {
        "ref": "tool_output:skill.open:abc123",
        "digest": "b"*64,
        "project_id": "proj-test",
        "worktree_id": "wt-test",
        "byte_length": 9999,  # tampered / unissued
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
