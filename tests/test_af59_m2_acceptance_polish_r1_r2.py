"""AF #59 M2 acceptance-polish R1/R2 — focused usage-contract tests.

Deterministic, network-free coverage for the two bounded repairs exposed by
real :3002 manual acceptance:

* R1 — ambiguous Plan locator fast-stop: a bare issue number (``#39``) is not
  a canonical ``plan_ref``; the model-visible guidance asks for
  ``owner/repo#number`` immediately and forbids host/runtime/workspace/profile/
  session/search repository inference; the frozen authority rules are
  unchanged and the canonical ``owner/repo#number`` stays usable.
* R2 — known context reuse first: the bootstrap-materialized base guidance and
  the canonical base Skill instruct reuse of an already-present Skill/ref/
  digest, candidate-card continuation after a candidate miss, no repeated
  hydrate, and preserve the freshness/CAS exception. No cache/working-set
  engine is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _eager() -> str:
    from aota_forge.work_plane import af_roles

    return af_roles.thin_task_main_eager_guidance()


def _base_skill() -> str:
    return (REPO_ROOT / "skills" / "aota-task-main-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def _governance_skill() -> str:
    return (REPO_ROOT / "skills" / "aota-task-main-governance" / "SKILL.md").read_text(
        encoding="utf-8"
    )


class TestR1AmbiguousPlanLocatorFastStop:
    def test_bare_number_is_not_a_plan_ref_in_model_visible_guidance(self) -> None:
        eager = _eager()
        skill = _base_skill()
        assert "not a plan_ref" in eager
        assert "owner/repo#number" in eager
        assert "bare issue number" in skill
        assert "canonical `plan_ref` is insufficient" in skill
        assert "owner/repo#number" in skill
        assert "AMBIGUOUS_PLAN_NUMBER_IS_NOT_PLAN_REF=yes" in skill
        assert "MISSING_PLAN_REF_FAST_STOP=yes" in skill
        assert "SESSION_DIRECTORY_PROFILE_REPO_INFERENCE=no" in skill
        assert "UNNECESSARY_DISCOVERY_BEFORE_NEEDS_INPUT=no" in skill

    def test_guidance_forbids_repository_inference_from_host_runtime_workspace(self) -> None:
        eager = _eager()
        skill = _base_skill()
        gov = _governance_skill()
        assert "never infer" in eager
        assert "host/runtime/workspace/cwd/profile/session/search" in eager
        assert "Do **not** \"resolve\" the repository by exploring `host.status`" in skill
        for source in (
            "host.status",
            "runtime.status",
            "workspace root",
            "profile",
            "session metadata",
            "project search",
        ):
            assert source in skill, source
        assert "never explore `host.status`" in gov
        assert "ask for `owner/repo#number`" in gov

    def test_missing_plan_ref_is_needs_input_not_discovery(self) -> None:
        skill = _base_skill()
        assert "do not treat the missing\nref as an operation-schema problem" in skill
        assert "needs_input: ask for owner/repo#number" in skill

    def test_canonical_plan_ref_stays_usable_and_bare_number_fails_closed(self) -> None:
        from aota_forge.work_plane.github_tools import (
            GitHubAuthorityError,
            TrustedPlanGitHubBinding,
            parse_plan_ref,
        )

        repo, owner, number = parse_plan_ref("wzjcccc-dotcom/aota-hermes-tools#39")
        assert (repo, owner, number) == (
            "wzjcccc-dotcom/aota-hermes-tools",
            "wzjcccc-dotcom",
            39,
        )
        with pytest.raises(GitHubAuthorityError):
            parse_plan_ref("#39")
        with pytest.raises(GitHubAuthorityError):
            TrustedPlanGitHubBinding.from_plan_ref("#39")

    def test_full_issue_url_is_documented_as_deterministic_normalization(self) -> None:
        skill = _base_skill()
        assert "GitHub Issue URL" in skill
        assert "normalizing it deterministically to `owner/repo#number`" in skill

    def test_authority_rules_unchanged(self) -> None:
        from aota_forge.work_plane import af_roles

        assert af_roles.ROLE_BOOTSTRAP_IS_AUTHORITY is False
        assert af_roles.ROLE_BOOTSTRAP_BINDS_PLAN is False
        assert af_roles.ROLE_BOOTSTRAP_BINDS_SESSION is False
        assert af_roles.ROLE_BOOTSTRAP_MECHANICAL_GATE is False
        assert af_roles.AMBIGUOUS_PLAN_NUMBER_IS_NOT_PLAN_REF is True
        assert af_roles.MISSING_PLAN_REF_FAST_STOP is True
        assert af_roles.SESSION_DIRECTORY_PROFILE_REPO_INFERENCE is False
        assert af_roles.UNNECESSARY_DISCOVERY_BEFORE_NEEDS_INPUT is False
        assert "plan_ref is an operation locator, never authority" in _eager()

    def test_unbound_bootstrap_materializes_fast_stop_guidance(self) -> None:
        from aota_forge.mcp_transport import _unbound_task_main_skill_metadata

        metadata = _unbound_task_main_skill_metadata()
        entry = next(
            e for e in metadata["BASE_SKILLS"] if e["skill_id"] == "aota-task-main-control"
        )
        assert entry["materialized"] == _eager()
        assert "A bare #N is not a plan_ref" in entry["materialized"]
        assert "ask for owner/repo#number" in entry["materialized"]


class TestR2KnownContextReuseFirst:
    def test_bootstrap_materialized_base_guidance_includes_reuse_first(self) -> None:
        from aota_forge.mcp_transport import _unbound_task_main_skill_metadata

        metadata = _unbound_task_main_skill_metadata()
        entry = next(
            e for e in metadata["BASE_SKILLS"] if e["skill_id"] == "aota-task-main-control"
        )
        materialized = entry["materialized"]
        assert materialized == _eager()
        assert "Reuse known context first" in materialized
        assert "reopen a Skill already in context" in materialized
        assert "same ref/digest" in materialized
        assert "candidate miss" in materialized
        assert "remaining refs" in materialized

    def test_same_skill_ref_not_instructed_to_reopen_by_default(self) -> None:
        skill = _base_skill()
        assert "KNOWN_CONTEXT_REUSE_FIRST=yes" in skill
        assert "is not reopened; keep using it" in skill
        assert "same version/digest" in skill
        assert "bootstrap-materialized base guidance is likewise not re-requested" in skill
        assert "don't re-run role.bootstrap or reopen a Skill already in context" in _eager()

    def test_candidate_miss_is_not_search_miss(self) -> None:
        skill = _base_skill()
        assert "A candidate miss is **not** a search miss" in skill
        assert "reuse the prior card" in skill
        assert "continue with D/E" in skill
        assert "do not\n   rerun the identical query by default" in skill

    def test_repeated_hydration_discouraged_and_freshness_exception_preserved(self) -> None:
        skill = _base_skill()
        assert "hydrated again by default" in skill
        for marker in (
            "source/digest changed",
            "candidate set is exhausted",
            "query/scope/intent changed",
            "host compaction",
            "freshness/CAS explicitly",
            "prior result is incomplete",
        ):
            assert marker in skill, marker
        assert "Read-before-write means a fresh authority read before a mutation/CAS" in skill
        assert "not a re-read at every reasoning step" in skill

    def test_governance_skill_carries_reuse_and_locator_discipline(self) -> None:
        gov = _governance_skill()
        assert "KNOWN_CONTEXT_REUSE_FIRST=yes" in gov
        assert "do not repeat\n`skill.open`" in gov

    def test_no_cache_or_working_set_engine_added(self) -> None:
        from aota_forge.work_plane import af_roles

        src = (
            REPO_ROOT / "aota_forge" / "work_plane" / "af_roles.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "ContextCache",
            "RetrievalCache",
            "WorkingSet",
            "KnownContextRegistry",
            "def resolve_known_context",
        ):
            assert forbidden not in src, forbidden
        rb = (
            REPO_ROOT / "aota_forge" / "work_plane" / "role_bootstrap.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "KNOWN_CONTEXT_REUSE_CACHE",
            "skill_open_cache",
            "retrieval_cache",
            "context_registry",
        ):
            assert forbidden not in rb, forbidden
        # the discipline is declared as guidance flags + text only
        assert af_roles.KNOWN_CONTEXT_REUSE_FIRST is True
        assert isinstance(af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE, str)

    def test_eager_bound_is_explicit_and_enforced(self) -> None:
        from aota_forge.work_plane import af_roles, role_bootstrap

        assert (
            len(af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE)
            <= af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS
        )
        rb_src = (
            REPO_ROOT / "aota_forge" / "work_plane" / "role_bootstrap.py"
        ).read_text(encoding="utf-8")
        assert "THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS" in rb_src
        assert hasattr(role_bootstrap, "BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS")

    def test_startup_seed_carries_fast_stop_and_reuse(self) -> None:
        seed = (REPO_ROOT / "prompts" / "task-main-startup.default.md").read_text(
            encoding="utf-8"
        )
        assert "owner/repo#number" in seed
        assert "先重用" in seed
        assert "不要用 host/runtime 狀態、目錄、workspace root、profile" in seed
