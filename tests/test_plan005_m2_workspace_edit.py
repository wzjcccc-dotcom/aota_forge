from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.core.providers.tool import ToolRequest
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.authorized_roots import authorized_roots_for_worker
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.workspace_mutation import (
    MAX_EDIT_TARGET_BYTES,
    MAX_EDIT_TEXT_BYTES,
    WORKSPACE_EDIT_DESCRIPTOR,
    WORKSPACE_WRITE_DESCRIPTOR,
    BoundedWorkspaceMutationProvider,
    WorkspaceMutationAuthorityError,
    create_workspace_mutation_authority,
)
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    BoundedWorkspaceToolProvider,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


def _sandbox(
    root: Path,
    *,
    worktree_id: str = "wt-edit-test",
    workspace_root: Path | None = None,
    project_root: Path | None = None,
):
    root.mkdir(parents=True, exist_ok=True)
    ws_root = workspace_root or root
    proj_root = project_root or root
    candidate = ProjectCandidateEvidence(
        workspace_id="ws-edit-test",
        workspace_root=str(ws_root),
        project_id="proj-edit-test",
        project_root=str(proj_root),
        manifest_path=".aota/project.yaml",
        name="edit-test",
        kind="test",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws-edit-test",
        workspace_root=str(ws_root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id=worktree_id, worktree_root=root)


def _handoff(role: str = "coder") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="workspace-edit-test",
        objective="Exercise one bounded workspace operation",
        bounded_scope="test files only",
        validation_expectations=("exact result",),
        semantic_stop_expectations=("stop on authority denial",),
    )


def _policy() -> AgentsPolicyCandidate:
    return AgentsPolicyCandidate(
        policy_id="policy-edit-test",
        project_id="proj-edit-test",
        scope="",
        content="test policy context",
        provenance_ref="test:AGENTS.md",
    )


def _provider(root: Path, *, role: str = "coder", sandbox=None):
    trusted_sandbox = sandbox or _sandbox(root)
    handoff = _handoff(role)
    roots = authorized_roots_for_worker(trusted_sandbox)
    authority = create_workspace_mutation_authority(
        trusted_sandbox,
        handoff,
        (_policy(),),
        WORKSPACE_EDIT_DESCRIPTOR,
        authorized_roots=roots,
    )
    return BoundedWorkspaceMutationProvider(authority), trusted_sandbox, handoff


def _edit(provider, path: str, old_text: str, new_text: str, digest: str, *, matches: int = 1):
    return provider.invoke(
        ToolRequest(
            operation=WORKSPACE_EDIT_DESCRIPTOR,
            inputs={
                "path": path,
                "old_text": old_text,
                "new_text": new_text,
                "expected_matches": matches,
                "expected_sha256": digest,
            },
        )
    )


def test_t01_coder_exact_edit_returns_bounded_mutation_evidence(tmp_path: Path) -> None:
    provider, sandbox, _ = _provider(tmp_path)
    target = tmp_path / "source.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    target.chmod(0o640)
    response = _edit(provider, "source.py", "value = 1", "value = 2", hashlib.sha256(before).hexdigest())
    after = b"value = 2\n"
    assert response.ok
    assert target.read_bytes() == after
    assert target.stat().st_mode & 0o777 == 0o640
    assert response.payload == {
        "path": "source.py",
        "operation": "workspace.edit",
        "matches_replaced": 1,
        "pre_sha256": hashlib.sha256(before).hexdigest(),
        "post_sha256": hashlib.sha256(after).hexdigest(),
        "pre_bytes": len(before),
        "post_bytes": len(after),
        "project_id": sandbox.project_id,
        "worktree_id": sandbox.worktree_id,
        "root_ref": "active-worktree",
    }
    assert "canonical_path" not in response.payload
    assert "content" not in response.payload


def test_t02_small_edit_supports_existing_file_larger_than_4096_bytes(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    before = ("padding\n" * 1800 + "unique_anchor = 1\n" + "tail\n" * 1800).encode()
    assert len(before) > 4096
    assert len(before) < MAX_EDIT_TARGET_BYTES
    (tmp_path / "large.py").write_bytes(before)
    response = _edit(provider, "large.py", "unique_anchor = 1", "unique_anchor = 2", hashlib.sha256(before).hexdigest())
    assert response.ok
    assert b"unique_anchor = 2" in (tmp_path / "large.py").read_bytes()


def test_target_file_finite_upper_bound_is_enforced(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "oversized.py"
    target.write_bytes(b"x" * (MAX_EDIT_TARGET_BYTES + 1))
    response = _edit(provider, "oversized.py", "x", "y", "0" * 64)
    assert response.ok is False
    assert response.error["code"] == "TARGET_FILE_TOO_LARGE"


def test_t03_stale_sha256_fails_closed(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "stale.py"
    target.write_text("value = 2\n", encoding="utf-8")
    before = target.read_bytes()
    response = _edit(provider, "stale.py", "value = 2", "value = 3", "0" * 64)
    assert response.ok is False
    assert response.error["code"] == "STALE_FILE"
    assert target.read_bytes() == before


def test_t04_zero_literal_matches_fails_closed(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "zero.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    response = _edit(provider, "zero.py", "value = 2", "value = 3", hashlib.sha256(before).hexdigest())
    assert response.ok is False
    assert response.error["code"] == "MATCH_COUNT_MISMATCH"
    assert target.read_bytes() == before


def test_t05_two_literal_matches_fail_closed(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "twice.py"
    before = b"value = 1\nvalue = 1\n"
    target.write_bytes(before)
    response = _edit(provider, "twice.py", "value = 1", "value = 2", hashlib.sha256(before).hexdigest())
    assert response.ok is False
    assert response.error["code"] == "MATCH_COUNT_MISMATCH"
    assert target.read_bytes() == before


@pytest.mark.parametrize("role", ["analyst", "reviewer", "task-main"])
def test_t06_t08_non_coder_roles_are_denied(tmp_path: Path, role: str) -> None:
    provider, _, _ = _provider(tmp_path, role=role)
    target = tmp_path / f"{role}.py"
    before = b"value = 1\n"
    target.write_bytes(before)
    response = _edit(provider, target.name, "value = 1", "value = 2", hashlib.sha256(before).hexdigest())
    assert response.ok is False
    assert response.error["code"] == "AUTHORITY_DENIED"
    assert target.read_bytes() == before


def test_workspace_edit_surface_is_coder_only() -> None:
    assert get_tool_surface_for_role("coder").is_eager("workspace.edit")
    for role in ("task-main", "analyst", "reviewer", "project-steward"):
        surface = get_tool_surface_for_role(role)
        assert surface.is_eager("workspace.edit") is False
        assert surface.is_progressive("workspace.edit") is False


def test_t09_active_worktree_is_the_only_edit_root(tmp_path: Path) -> None:
    project_main = tmp_path / "project-main"
    active_worktree = tmp_path / "worker-wt"
    project_main.mkdir()
    active_worktree.mkdir()
    protected = project_main / "protected.py"
    protected.write_bytes(b"main = 1\n")
    active = active_worktree / "active.py"
    active.write_bytes(b"active = 1\n")
    sandbox = _sandbox(
        active_worktree,
        workspace_root=tmp_path,
        project_root=project_main,
    )
    provider, _, _ = _provider(active_worktree, sandbox=sandbox)
    assert provider.authorized_roots.names() == ("active-worktree",)
    assert provider.authorized_roots.default_write_root().root_ref == "active-worktree"
    allowed = _edit(
        provider,
        "active.py",
        "active = 1",
        "active = 2",
        hashlib.sha256(active.read_bytes()).hexdigest(),
    )
    denied = _edit(
        provider,
        "project-main/protected.py",
        "main = 1",
        "main = 2",
        hashlib.sha256(protected.read_bytes()).hexdigest(),
    )
    assert allowed.ok
    assert denied.ok is False
    assert protected.read_bytes() == b"main = 1\n"
    assert sandbox.worktree_root == str(active_worktree.resolve())
    with pytest.raises(Exception):
        ToolRequest(
            operation=WORKSPACE_EDIT_DESCRIPTOR,
            inputs={
                "path": "active.py",
                "old_text": "active = 2",
                "new_text": "active = 3",
                "expected_matches": 1,
                "expected_sha256": hashlib.sha256(active.read_bytes()).hexdigest(),
                "root_ref": "project-main",
            },
        )


def test_t11_symlink_escape_is_denied(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path / "worktree")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret = 1\n")
    (tmp_path / "worktree" / "link.py").symlink_to(outside)
    response = _edit(provider, "link.py", "secret = 1", "secret = 2", hashlib.sha256(outside.read_bytes()).hexdigest())
    assert response.ok is False
    assert "SYMLINK" in response.error["code"]
    assert outside.read_bytes() == b"secret = 1\n"


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/outside.py"])
def test_t12_traversal_and_absolute_paths_are_denied(tmp_path: Path, path: str) -> None:
    provider, _, _ = _provider(tmp_path)
    response = _edit(provider, path, "x", "y", "0" * 64)
    assert response.ok is False
    assert response.error["code"] in {"PATH_ESCAPE", "INVALID_PATH"}


def test_t13_invalid_utf8_file_fails_deterministically(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    before = b"valid\xff\n"
    (tmp_path / "binary.py").write_bytes(before)
    response = _edit(provider, "binary.py", "valid", "changed", hashlib.sha256(before).hexdigest())
    assert response.ok is False
    assert response.error["code"] == "INVALID_ENCODING"


@pytest.mark.parametrize("field", ["old_text", "new_text"])
def test_t14_edit_text_utf8_byte_bound_is_enforced(tmp_path: Path, field: str) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "bounded.py"
    target.write_bytes(b"x\n")
    args = {
        "path": "bounded.py",
        "old_text": "x",
        "new_text": "y",
        "expected_matches": 1,
        "expected_sha256": hashlib.sha256(b"x\n").hexdigest(),
    }
    args[field] = "é" * (MAX_EDIT_TEXT_BYTES // 2 + 1)
    response = provider.invoke(ToolRequest(operation=WORKSPACE_EDIT_DESCRIPTOR, inputs=args))
    assert response.ok is False
    assert response.error["code"] == "OVERSIZED_EDIT"
    assert target.read_bytes() == b"x\n"


def test_t15_post_write_digest_matches_file(tmp_path: Path) -> None:
    provider, _, _ = _provider(tmp_path)
    target = tmp_path / "digest.py"
    before = b"value = 10\n"
    target.write_bytes(before)
    response = _edit(provider, "digest.py", "10", "11", hashlib.sha256(before).hexdigest())
    assert response.ok
    assert response.payload["post_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_t16_workspace_write_keeps_4096_byte_whole_file_contract(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    handoff = _handoff()
    authority = create_workspace_mutation_authority(sandbox, handoff, (_policy(),), WORKSPACE_WRITE_DESCRIPTOR)
    provider = BoundedWorkspaceMutationProvider(authority)
    valid = provider.invoke(
        ToolRequest(
            operation=WORKSPACE_WRITE_DESCRIPTOR,
            inputs={"path": "write.txt", "content": "w" * 4096, "mode": "create_only"},
        )
    )
    assert valid.ok
    with pytest.raises(Exception):
        ToolRequest(
            operation=WORKSPACE_WRITE_DESCRIPTOR,
            inputs={"path": "too-large.txt", "content": "w" * 4097, "mode": "create_only"},
        )


def test_t17_workspace_read_sha256_covers_full_ranged_file(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    handoff = _handoff()
    authority = create_workspace_authority(sandbox, handoff, (_policy(),), WORKSPACE_READ_DESCRIPTOR)
    provider = BoundedWorkspaceToolProvider(authority)
    raw = b"0123456789abcdefghijklmnopqrstuvwxyz"
    (tmp_path / "ranged.txt").write_bytes(raw)
    response = provider.invoke(
        ToolRequest(
            operation=WORKSPACE_READ_DESCRIPTOR,
            inputs={"path": "ranged.txt", "offset": 5, "max_bytes": 8},
        )
    )
    assert response.ok
    assert response.payload["content"] == raw[5:13].decode()
    assert response.payload["truncated"] is True
    assert response.payload["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "canonical_path" not in response.payload
    assert str(tmp_path) not in str(response.payload)


def test_t18_workspace_write_and_edit_authorities_cannot_substitute(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    handoff = _handoff()
    write_authority = create_workspace_mutation_authority(sandbox, handoff, (_policy(),), WORKSPACE_WRITE_DESCRIPTOR)
    edit_authority = create_workspace_mutation_authority(sandbox, handoff, (_policy(),), WORKSPACE_EDIT_DESCRIPTOR)
    write_provider = BoundedWorkspaceMutationProvider(write_authority)
    edit_provider = BoundedWorkspaceMutationProvider(edit_authority)
    (tmp_path / "target.py").write_bytes(b"x\n")
    edit_request = ToolRequest(
        operation=WORKSPACE_EDIT_DESCRIPTOR,
        inputs={
            "path": "target.py",
            "old_text": "x",
            "new_text": "y",
            "expected_matches": 1,
            "expected_sha256": hashlib.sha256(b"x\n").hexdigest(),
        },
    )
    write_request = ToolRequest(
        operation=WORKSPACE_WRITE_DESCRIPTOR,
        inputs={"path": "other.txt", "content": "x", "mode": "create_only"},
    )
    assert write_provider.invoke(edit_request).error["code"] == "OPERATION_MISMATCH"
    assert edit_provider.invoke(write_request).error["code"] == "OPERATION_MISMATCH"
    with pytest.raises(WorkspaceMutationAuthorityError):
        create_workspace_mutation_authority(
            sandbox,
            handoff,
            (_policy(),),
            replace(WORKSPACE_EDIT_DESCRIPTOR, description="forged descriptor"),
        )


def test_public_descriptor_help_and_core_ingress_use_coder_edit_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aota_forge.composition.worker_vertical_slice import build_worker_binding
    from aota_forge.core_ingress import PROVIDER_BACKED_OPERATIONS, dispatch_via_core, resolve_descriptor
    from aota_forge.mcp_transport import (
        WORKSPACE_OPERATIONS,
        SUPPORTED_OPERATIONS,
        _to_canonical_binding,
        help_operation_projection,
    )
    from aota_forge.work_plane.task_main_exposure import TASK_MAIN_UNBOUND_OPERATIONS

    descriptor = resolve_descriptor("workspace.edit")
    assert descriptor.contract_hash() == WORKSPACE_EDIT_DESCRIPTOR.contract_hash()
    assert "workspace.edit" in PROVIDER_BACKED_OPERATIONS
    assert "workspace.edit" in WORKSPACE_OPERATIONS
    assert "workspace.edit" in SUPPORTED_OPERATIONS
    help_contract = help_operation_projection("workspace.edit")
    assert set(help_contract["required_inputs"]) == {
        "path", "old_text", "new_text", "expected_matches", "expected_sha256"
    }
    assert help_contract["CAS_semantics"] == "expected_sha256=full_file_sha256_precondition"
    assert help_contract["edit_semantics"] == "one_exact_literal_match"
    assert "workspace.edit" not in TASK_MAIN_UNBOUND_OPERATIONS

    monkeypatch.setenv("AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE", "1")
    coder_root = tmp_path / "coder"
    coder_root.mkdir()
    coder_handoff = _handoff("coder")
    coder_binding = build_worker_binding(
        root=coder_root,
        project_id="proj-edit-test",
        worktree_id="wt-coder",
        canonical_task_id="task-edit-coder",
        handoff=coder_handoff,
    )
    assert coder_binding.workspace_edit_authority.operation.name == "workspace.edit"
    canonical_binding = _to_canonical_binding(coder_binding)
    assert canonical_binding.workspace_edit_authority is coder_binding.workspace_edit_authority
    target = coder_root / "integrated.py"
    target.write_bytes(b"item = 1\n")
    coder_response = dispatch_via_core(
        "workspace.edit",
        {
            "path": "integrated.py",
            "old_text": "item = 1",
            "new_text": "item = 2",
            "expected_matches": 1,
            "expected_sha256": hashlib.sha256(b"item = 1\n").hexdigest(),
        },
        canonical_binding,
    )
    assert coder_response.ok
    assert target.read_bytes() == b"item = 2\n"

    analyst_root = tmp_path / "analyst"
    analyst_root.mkdir()
    analyst_binding = build_worker_binding(
        root=analyst_root,
        project_id="proj-edit-test",
        worktree_id="wt-analyst",
        canonical_task_id="task-edit-analyst",
        handoff=_handoff("analyst"),
    )
    assert analyst_binding.workspace_edit_authority is None
    assert not analyst_binding.tool_surface.is_visible("workspace.edit")
    analyst_response = dispatch_via_core(
        "workspace.edit",
        {
            "path": "integrated.py",
            "old_text": "item = 1",
            "new_text": "item = 2",
            "expected_matches": 1,
            "expected_sha256": hashlib.sha256(b"item = 1\n").hexdigest(),
        },
        _to_canonical_binding(analyst_binding),
    )
    assert analyst_response.ok is False
    assert analyst_response.error["code"] == "AUTHORITY_DENIED"
