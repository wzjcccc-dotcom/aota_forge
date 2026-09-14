"""AF #54 M5/W3 — transitional task-main governance Skill.

PROVES (focused):

* aota-task-main-governance is registered PROGRESSIVE for task-main only
  (never eager, never in any other role's universe);
* the full Governance 1.x procedure has exactly one canonical owner (the
  progressive Skill); the base task-main Skill carries only a short pointer;
  GitHub/git mechanics never leak into SOUL;
* the base Skill points at the governance Skill and states task-main
  ownership of reconciliation + lifecycle decisions;
* no-normal-delegation to project-steward is asserted while the legacy
  project-steward role/Skill remains registered and loadable (compatibility);
* aota-task-lifecycle remains unassigned;
* Skills grant no authority: opening the governance Skill is universe-gated
  (coder denied), and SKILL_IS_AUTHORITY stays False.

DOES_NOT_PROVE: real session behavior (M5/W4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.work_plane import af_roles
from aota_forge.work_plane.af_roles import (
    _ROLE_SKILL_DEFS,
    ORPHAN_SKILL_DISPOSITION,
    curated_eager_guidance,
    progressive_skill_metadata,
)
from aota_forge.work_plane.skill import compute_skill_digest
from aota_forge.work_plane.skill_registry import StaticSkillRegistry
from aota_forge.work_plane.skill_content import open_skill
from aota_forge.work_plane.af_roles import AF_SKILL_REGISTRY, get_allowed_universe_for_role

ROOT = Path(__file__).resolve().parents[1]


def _skill_text(skill_id: str) -> str:
    return (ROOT / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate():
    yield
    reset_execution_dispatcher()


class TestW3ProgressiveRegistration:
    def test_governance_skill_registered_progressive_for_task_main(self) -> None:
        eager, prog = _ROLE_SKILL_DEFS["task-main"]
        assert "aota-task-main-governance" in prog
        assert "aota-task-main-governance" not in eager
        for role, (e, p) in _ROLE_SKILL_DEFS.items():
            if role != "task-main":
                assert "aota-task-main-governance" not in e + p, role

    def test_not_eager_dumped_into_bootstrap(self) -> None:
        meta = progressive_skill_metadata("task-main")
        refs = [m["ref"] for m in meta]
        assert "aota-task-main-governance@1.0.0" in refs
        # eager guidance must stay short and must not contain the manual
        eager = af_roles.thin_task_main_eager_guidance()
        assert len(eager) < 1200
        assert "milestone_progress_index" not in eager
        assert "decision_change_log" not in eager

    def test_registry_membership_and_digest(self) -> None:
        entry = AF_SKILL_REGISTRY.get("task-main", "aota-task-main-governance", "1.0.0")
        assert entry is not None
        content = _skill_text("aota-task-main-governance")
        assert entry.identity.digest == compute_skill_digest(content)


class TestW3CanonicalOwnership:
    def test_base_skill_points_to_governance_skill(self) -> None:
        base = _skill_text("aota-task-main-control")
        assert "aota-task-main-governance" in base
        # short pointer, not the manual itself
        assert base.count("milestone_progress_index") == 0
        assert "COMMENT_ROLE" not in base

    def test_governance_procedure_canonical_markers(self) -> None:
        gov = _skill_text("aota-task-main-governance")
        for marker in (
            "milestone_progress_index",
            "development_notes",
            "defect_register",
            "plan_appendix",
            "decision_change_log",
            "COMMENT_ROLE",
            "read-before-write",
            "expected_updated_at",
            "expected_digest",
            "git.integrate",
            "git.push",
            "TASK_MAIN_TEMPORARY_STEWARDSHIP=yes",
            "PROJECT_STEWARD_NEW_NORMAL_DELEGATION=no",
            "PROJECT_STEWARD_LEGACY_COMPATIBILITY=yes",
            "AOTA_TASK_LIFECYCLE_REINTRODUCED=no",
            "GIT_VIA_RESTRICTED_SHELL=no",
            "GITHUB_VIA_RESTRICTED_SHELL=no",
        ):
            assert marker in gov, marker

    def test_no_three_way_duplication(self) -> None:
        # GitHub mechanics canonical in the governance Skill (procedure) and
        # thin primitive contracts only in OPERATION_GUIDANCE; never in SOUL.
        soul = (ROOT / "aota_forge" / "roles" / "task-main.md").read_text(encoding="utf-8")
        for leak in ("expected_updated_at", "github.issue.", "milestone_progress_index", "COMMENT_ROLE"):
            assert leak not in soul
        assert len(soul) < 2500

    def test_thin_operation_guidance_carries_only_thin_contracts(self) -> None:
        from aota_forge.work_plane.task_main_descriptors import (
            build_thin_task_main_operation_guidance,
        )

        guidance = str(build_thin_task_main_operation_guidance())
        assert "milestone_progress_index" not in guidance
        assert "decision_change_log" not in guidance
        assert "github.issue.read" in guidance  # thin primitive contract present


class TestW3StewardAndLifecycleDisposition:
    def test_steward_not_normal_governance_delegation(self) -> None:
        base = _skill_text("aota-task-main-control")
        gov = _skill_text("aota-task-main-governance")
        eager = af_roles.thin_task_main_eager_guidance()
        assert "never delegate governance to a" in (base + gov + eager).lower()
        assert "PROJECT_STEWARD_NEW_NORMAL_DELEGATION=no" in gov

    def test_legacy_project_steward_remains_loadable(self) -> None:
        eager, prog = _ROLE_SKILL_DEFS["project-steward"]
        assert "aota-pcf-project-steward" in eager
        entry = AF_SKILL_REGISTRY.get("project-steward", "aota-pcf-project-steward", "1.0.0")
        assert entry is not None
        assert (ROOT / "skills" / "aota-pcf-project-steward" / "SKILL.md").is_file()
        assert "Tools: search/read/hydrate. No write/shell/test/git." in curated_eager_guidance(
            "aota-pcf-project-steward"
        )

    def test_task_lifecycle_not_reintroduced(self) -> None:
        assert ORPHAN_SKILL_DISPOSITION["aota-task-lifecycle"][0] == "remain_unassigned"
        for role, (e, p) in _ROLE_SKILL_DEFS.items():
            assert "aota-task-lifecycle" not in e + p, role


class TestW3SkillIsNotAuthority:
    def test_governance_skill_open_universe_gated(self) -> None:
        universe = get_allowed_universe_for_role("task-main")
        assert universe.get_by_ref("aota-task-main-governance@1.0.0") is not None
        coder_universe = get_allowed_universe_for_role("coder")
        assert coder_universe.get_by_ref("aota-task-main-governance@1.0.0") is None

    def test_open_skill_returns_content_not_authority(self) -> None:
        reader = lambda ref: _skill_text("aota-task-main-governance")  # noqa: E731
        opened = open_skill(
            AF_SKILL_REGISTRY, "task-main", "aota-task-main-governance", "1.0.0", reader
        )
        assert "COMMENT_ROLE" in opened.content
        # SKILL_IS_AUTHORITY=no is asserted at every real bootstrap elsewhere;
        # here: the opened projection is a passive value object, no methods.
        assert not any(callable(getattr(opened, a, None)) for a in ("authorize", "grant"))

    def test_orphan_disposition_records_progressive_assignment(self) -> None:
        assert ORPHAN_SKILL_DISPOSITION["aota-task-main-governance"][0] == "assign_progressive"
