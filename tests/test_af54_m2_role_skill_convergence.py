"""AF #54 M2/W3 — coder / reviewer / shared Skill convergence.

Semantic-marker proof (no prose-snapshot tests) that the converged Skills
match ACTUAL server-side authority and that eager/detail guidance channels
are consistent with a single canonical detailed owner:

  C1  coder Skill body matches real coder authority (write mode enum,
      4096 bound, no delete/move grant, test.run, restricted_shell is
      residual + binding-dependent, task.return required completion)
  C2  reviewer Skill body matches real restricted authority (no write,
      conditional test policy, verdict vocabulary, bounded sizing,
      generic child role — not a workflow state, returns findings rather
      than repairing)
  C3  reviewer workspace.write stays denied at the server no matter what
      prose claims (SKILL_IS_AUTHORITY=no)
  C4  shared workspace eager summary and canonical SKILL.md detail are not
      contradictory (same mode enum; role policy delegated to Role Skills)
  C5  test.run reviewer conditional is owned by the reviewer Role Skill
      channels (+1 SOUL cannot-do line) and no longer duplicated in the
      shared workspace eager/detail guidance
  C6  _SKILL_META use_when stays bounded procedural discovery text (no
      authority/permission language, no legacy coordinator verbs)

PROVES=guidance-authority alignment and progressive-disclosure ownership at
       the Skill/eager channel level + live server-side enforcement.
DOES_NOT_PROVE=runtime LLM usage quality (M4) or telemetry (M3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.work_plane import af_roles
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

ROOT = Path(__file__).resolve().parents[1]


def _skill(skill_id: str) -> str:
    return (ROOT / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")


_CODER = _skill("aota-spec-driven-implementation")
_REVIEWER = _skill("aota-implementation-review")
_WORKSPACE_BODY = _skill("aota-workspace-operations")
_WORKSPACE_EAGER = af_roles.curated_eager_guidance("aota-workspace-operations")


def _sandbox(root: Path) -> WorktreeSandboxBoundary:
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af54m2w3",
        workspace_root=str(root),
        project_id="proj-af54m2w3",
        project_root=str(root),
        worktree_id="wt-af54m2w3",
        worktree_root=str(root.resolve()),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _reviewer_binding(sandbox: WorktreeSandboxBoundary) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role="reviewer",
        task_kind="af54-m2w3",
        objective="review",
        bounded_scope="review scope",
        validation_expectations=["focused"],
        semantic_stop_expectations=["stop"],
    )
    return CanonicalDispatchBinding(
        canonical_task_id="reviewer-af54m2w3",
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface(
            "reviewer",
            eager=(
                "workspace.search",
                "workspace.read",
                "test.run",
                "handoff.open",
                "handoff.write",
                "task.return",
            ),
            progressive=(),
        ),
    )


class TestC1CoderSkillMatchesAuthority:
    def test_write_contract_exact(self) -> None:
        assert "create_only|replace_existing|create_or_replace" in _CODER
        assert "4096" in _CODER
        assert "no delete/move operation" in _CODER

    def test_inspect_before_mutate_and_test_verification(self) -> None:
        assert "workspace.search" in _CODER
        assert "inspect before mutating" in _CODER
        assert "test.run" in _CODER

    def test_shell_residual_and_binding_conditional(self) -> None:
        assert "restricted_shell.run" in _CODER
        assert "only when your binding actually grants it" in _CODER
        assert "residual fallback" in _CODER

    def test_task_return_required_completion_not_process_exit(self) -> None:
        assert "task.return" in _CODER
        assert "Process exit is never semantic completion" in _CODER
        assert '"mode": "result"' in _CODER


class TestC2ReviewerSkillMatchesAuthority:
    def test_read_only_and_no_repair(self) -> None:
        assert "No `workspace.write`" in _REVIEWER
        assert "Never fix findings yourself" in _REVIEWER
        assert "no source writes" in _WORKSPACE_BODY

    def test_conditional_test_policy_taught_once_in_own_skill(self) -> None:
        assert "validation_expectations" in _REVIEWER
        assert "conditionally authorized" in _REVIEWER

    def test_bounded_sizing_and_generic_role(self) -> None:
        assert "trusted Worker budget" in _REVIEWER
        assert "decomposed semantically by task-main" in _REVIEWER
        assert "generic child role started by task-main" in _REVIEWER
        assert "not a special workflow state" in _REVIEWER

    def test_verdict_vocabulary_and_governed_return(self) -> None:
        for verdict in ("PASS", "PASS_WITH_FINDINGS", "NEEDS_FIX", "BLOCKED", "INCONCLUSIVE"):
            assert verdict in _REVIEWER
        assert "task.return" in _REVIEWER
        assert "process exit alone is not completion" in _REVIEWER


class TestC3ReviewerWriteDeniedServerSide:
    def test_authority_denied_regardless_of_prose(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "c3")
        binding = _reviewer_binding(sandbox)
        response = dispatch_via_core(
            "workspace.write",
            {"path": "src/file.py", "content": "repair", "mode": "create_or_replace"},
            binding,
        )
        assert response.ok is False
        assert response.error["code"] == "AUTHORITY_DENIED"
        assert binding.tool_surface.is_eager("workspace.write") is False
        assert binding.tool_surface.is_progressive("workspace.write") is False
        assert bool(getattr(binding.tool_surface, "is_authority", False)) is False


class TestC4EagerDetailConsistent:
    def test_same_mode_enum_no_invention(self) -> None:
        modes = "create_only|replace_existing|create_or_replace"
        assert modes in _WORKSPACE_EAGER
        assert modes in _WORKSPACE_BODY

    def test_eager_is_short_pointer_and_not_contradictory(self) -> None:
        assert len(_WORKSPACE_EAGER) < 1024
        assert "open this Skill" in _WORKSPACE_EAGER
        # eager defers the per-role test policy to Role Skills (same ownership
        # as the body); it must not carry a rival reviewer-conditional essay
        assert "reviewer" not in _WORKSPACE_EAGER.lower()

    def test_shared_body_points_role_test_policy_to_role_skill(self) -> None:
        assert "your Role Skill states your concrete policy" in _WORKSPACE_BODY


class TestC5ReviewerConditionalOwnership:
    def test_conditional_phrase_not_in_shared_channels(self) -> None:
        for channel in (_WORKSPACE_EAGER, _WORKSPACE_BODY):
            assert "validation_expectations" not in channel

    def test_conditional_still_in_reviewer_channels_and_soul_boundary(self) -> None:
        eager = af_roles.curated_eager_guidance("aota-implementation-review")
        assert "validation_expectations" in eager
        assert "validation_expectations" in _REVIEWER
        soul = (ROOT / "aota_forge" / "roles" / "reviewer.md").read_text(encoding="utf-8")
        assert "cannot-do" in soul.lower()
        assert "test.run" in soul


class TestC6UseWhenMetaProceduralOnly:
    def test_use_when_bounded_and_non_authoritative(self) -> None:
        for skill_id, (_short, use_when) in af_roles._SKILL_META.items():
            lowered = use_when.lower()
            assert len(use_when) < 200, skill_id
            for authority_word in ("must", "denied", "allowed", "permission", "grant"):
                assert authority_word not in lowered, (skill_id, authority_word)
            for stale in ("activate", "advance", "recover"):
                # word-boundary check: legacy coordinator verbs must not
                # advertise any Skill anymore
                import re

                assert re.search(rf"\b{stale}\b", lowered) is None, (skill_id, stale)
