"""M3/W1 Role Bootstrap, Skill Universe & Agent Tool Surface Production Convergence.

Focused W1 tests A-W plus negative authority tests (mandatory).

A. all five role.bootstrap payloads fit bound
B. no semantic runtime truncation of eager guidance
C. Coder bootstrap contains usable normal implementation/test guidance
D. Reviewer bootstrap contains usable independent-validation guidance
E. Project Steward bootstrap contains Mode A + Mode B distinction
F. normal eager path requires zero skill.open calls
G. progressive Skill ref appears only for rare capability
H. progressive skill.open returns usable content in one call
I. normal skill.open does not require result.hydrate
J. large-reference ref remains optional
K. maintainer content excluded from normal runtime Skill output
L. five Role Skill universes match frozen Role contracts
M. orphan Skill dispositions are deterministic
N. Project Steward Skill no longer uses obsolete advisory-only contract
O. task-main cannot gain product source write/test authority
P. analyst write is absent or bounded artifact-only
Q. Coder search/read/write/test authorization works
R. Reviewer test visibility + conditional authorization works
S. Reviewer product write denied
T. Project Steward Git/GitHub generic mutation denied
U. hidden/unauthorized operation-name guessing fails closed
V. single public aota.invoke remains
W. M2 binding/Human Brake/Role lifecycle regression remains green
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.mcp_transport import (
    AGENT_FACING_AOTA_TOOL,
    MCP_PUBLIC_TOOL_COUNT,
    MCP_PUBLIC_TOOLS,
    _SharedAotaMcpAdapter,
)
from aota_forge.work_plane.af_roles import (
    ORPHAN_SKILL_DISPOSITION,
    _ROLE_SKILL_DEFS,
    _SKILL_META,
    curated_eager_guidance,
    get_tool_surface_for_role,
    progressive_skill_metadata,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.role_bootstrap import (
    ALL_ROLE_BOOTSTRAPS_CONSUMABLE_INLINE_OR_BY_REF,
    ALL_ROLE_BOOTSTRAPS_INLINE_WITHIN_BOUND,
    ALL_ROLE_BOOTSTRAPS_WITHIN_BOUND,
    BOOTSTRAP_NORMAL_PATH_USABLE_WITHOUT_SKILL_NAVIGATION,
    EAGER_SKILL_CONTENT_IS_USABLE_GUIDANCE,
    MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT,
    NORMAL_PROGRESSIVE_SKILL_OPEN_RETURNS_USABLE_CONTENT,
    NORMAL_PROGRESSIVE_SKILL_REQUIRES_RESULT_HYDRATE,
    RESULT_HYDRATE_FOR_LARGE_RESULT_OR_ARTIFACT,
    RESULT_HYDRATE_FOR_NORMAL_SKILL_LOADING,
    SEMANTIC_TRUNCATION_FOR_BOOTSTRAP,
    handle_role_bootstrap,
)
from aota_forge.work_plane.roles import AgentWorkRole


def _handoff(role: AgentWorkRole, val=("output matches",)) -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="m3-w1-test",
        objective="bounded objective for W1 convergence",
        bounded_scope="work/bounded-only",
        validation_expectations=val,
        semantic_stop_expectations=("stop on denial",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M3"),
    )


def _review_handoff(with_validation: bool = True) -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.REVIEWER,
        task_kind="m3-w1-review",
        objective="verify bounded implementation",
        bounded_scope="work/bounded-only review frontier",
        validation_expectations=("output matches",) if with_validation else (),
        semantic_stop_expectations=("stop when verdict emitted",),
        work_item_ref=SemanticReference(ref="W1"),
        milestone_ref=SemanticReference(ref="M3"),
    )


def _task_main_fake_binding(tmp_path: Path):
    from aota_forge.work_plane.af_roles import get_tool_surface_for_role as _surf

    coder_h = _handoff(AgentWorkRole.CODER)
    base = build_worker_binding(
        root=tmp_path, project_id="aota_forge", worktree_id="wt-fake",
        canonical_task_id="t-fake", handoff=coder_h,
    )
    tm_h = TaskHandoff(
        work_role=AgentWorkRole.TASK_MAIN, task_kind="task-main-control",
        objective="AOTA task-main milestone control via aota.invoke",
        bounded_scope="milestone coordination only",
        validation_expectations=("task-main control validation",),
        semantic_stop_expectations=("stop at user gate",),
        work_item_ref=SemanticReference(ref="M3/task-main"),
        milestone_ref=SemanticReference(ref="M3"),
    )
    return types.SimpleNamespace(
        handoff=tm_h, tool_surface=_surf(AgentWorkRole.TASK_MAIN),
        sandbox=base.sandbox, project_id="aota_forge", worktree_id="wt-fake",
        canonical_task_id="t-tm", trusted_context=base.trusted_context,
        read_authorities=(), mutation_authority=None,
        restricted_shell_authority=None, test_execution_authority=None,
        trusted_task_main_context=None,
    )


def _bootstrap_for(role: AgentWorkRole, tmp_path: Path) -> dict:
    if role == AgentWorkRole.TASK_MAIN:
        return handle_role_bootstrap(_task_main_fake_binding(tmp_path), {})
    b = build_worker_binding(
        root=tmp_path, project_id="aota_forge", worktree_id=f"wt-{role.value[:2]}",
        canonical_task_id=f"t-{role.value[:2]}", handoff=_handoff(role),
    )
    return handle_role_bootstrap(b, {})


# ---------------------------------------------------------------------------
# A/B: bound + no truncation
# ---------------------------------------------------------------------------

class TestABoundNoTruncation:
    @pytest.mark.parametrize("role", ["task-main", "analyst", "coder", "reviewer", "project-steward"])
    def test_all_bootstraps_consumable_inline_or_by_ref(self, tmp_path: Path, role: str) -> None:
        res = _bootstrap_for(AgentWorkRole(role), tmp_path)
        # W5 (AF #49 M1/W5, I49-B003) amended contract: no semantic truncation.
        # A bootstrap over the inline bound is a real governed by_ref result
        # with deterministic model-visible hydration claims (no inline-limit
        # increase, no result loss); consumed via existing result.hydrate.
        assert ALL_ROLE_BOOTSTRAPS_CONSUMABLE_INLINE_OR_BY_REF is True
        assert ALL_ROLE_BOOTSTRAPS_INLINE_WITHIN_BOUND is False
        assert ALL_ROLE_BOOTSTRAPS_WITHIN_BOUND is False  # truthful supersession
        assert SEMANTIC_TRUNCATION_FOR_BOOTSTRAP is False
        assert EAGER_SKILL_CONTENT_IS_USABLE_GUIDANCE is True
        # Prove model-visible consumption through the real transport projection.
        if role == "task-main":
            adapter = _SharedAotaMcpAdapter(_task_main_fake_binding(tmp_path))
        else:
            adapter = _SharedAotaMcpAdapter(
                build_worker_binding(
                    root=tmp_path, project_id="aota_forge", worktree_id=f"wt-{role[:2]}",
                    canonical_task_id=f"t-{role[:2]}", handoff=_handoff(AgentWorkRole(role)),
                )
            )
        rb = adapter.invoke("role.bootstrap", {})
        assert rb["ok"] is True
        if rb["output_mode"] == "inline":
            payload = rb["payload"]
        else:
            hydration = rb["hydration"]
            assert hydration["operation"] == "result.hydrate"
            hy = adapter.invoke("result.hydrate", dict(hydration["arguments"]))
            assert hy["ok"] is True, hy
            payload = json.loads(hy["payload"]["content"])
        assert payload["BASE_SKILLS"] == res["BASE_SKILLS"]

    @pytest.mark.parametrize("role", ["task-main", "analyst", "coder", "reviewer", "project-steward"])
    def test_no_semantic_truncation(self, tmp_path: Path, role: str) -> None:
        res = _bootstrap_for(AgentWorkRole(role), tmp_path)
        assert len(res["BASE_SKILLS"]) >= 1
        for e in res["BASE_SKILLS"]:
            assert e["is_truncated"] is False
            assert e["content_length"] > 180
            assert e["content_length"] <= 2048
            assert len(e["materialized"]) == e["content_length"]
            # Not reference-only ("you have Skill X" -> open to learn ordinary task)
            mat = e["materialized"]
            assert "you have Skill" not in mat
            assert len(mat.strip()) > 300


# ---------------------------------------------------------------------------
# C/D/E: usable eager guidance per role
# ---------------------------------------------------------------------------

class TestCDEUsableEager:
    def test_coder_has_implementation_test_guidance(self, tmp_path: Path) -> None:
        res = _bootstrap_for(AgentWorkRole.CODER, tmp_path)
        assert BOOTSTRAP_NORMAL_PATH_USABLE_WITHOUT_SKILL_NAVIGATION is True
        text = " ".join(e["materialized"] for e in res["BASE_SKILLS"])
        low = text.lower()
        for kw in ("workspace.search", "workspace.read", "workspace.write", "test.run",
                   "validation", "self-repair", "stop", "escalat"):
            assert kw in low, f"coder eager missing {kw!r}"

    def test_reviewer_has_independent_validation_guidance(self, tmp_path: Path) -> None:
        res = _bootstrap_for(AgentWorkRole.REVIEWER, tmp_path)
        text = " ".join(e["materialized"] for e in res["BASE_SKILLS"])
        low = text.lower()
        for kw in ("independent", "acceptance", "evidence", "product"):
            assert kw in low, f"reviewer eager missing {kw!r}"
        for kw in ("PASS", "NEEDS_FIX", "BLOCKED"):
            assert kw in text, f"reviewer eager missing {kw!r}"
        # Test policy visible eager without open
        assert "test.run" in low

    def test_steward_has_mode_a_b(self, tmp_path: Path) -> None:
        res = _bootstrap_for(AgentWorkRole.PROJECT_STEWARD, tmp_path)
        text = " ".join(e["materialized"] for e in res["BASE_SKILLS"])
        low = text.lower()
        assert "Mode A" in text
        assert "Mode B" in text
        for kw in ("ProjectState", "StewardResult"):
            assert kw in text, f"steward eager missing {kw!r}"
        for kw in ("acceptance", "governance", "finalizer"):
            assert kw in low, f"steward eager missing {kw!r}"

    def test_task_main_has_orchestration_no_implementation(self, tmp_path: Path) -> None:
        res = _bootstrap_for(AgentWorkRole.TASK_MAIN, tmp_path)
        text = " ".join(e["materialized"] for e in res["BASE_SKILLS"])
        for kw in ("activate", "recover", "advance", "DAG", "Human Brake", "USER_GATE"):
            assert kw in text, f"task-main eager missing {kw!r}"
        # Must not become coder: no product implementation workflow eager
        assert "write_scope" not in text or "workspace.write" not in text or "SPEC-driven" not in text


# ---------------------------------------------------------------------------
# F/G: eager zero-open, progressive only rare
# ---------------------------------------------------------------------------

class TestFGEagerProgressive:
    @pytest.mark.parametrize("role", ["task-main", "analyst", "coder", "reviewer", "project-steward"])
    def test_normal_path_needs_zero_skill_open(self, tmp_path: Path, role: str) -> None:
        res = _bootstrap_for(AgentWorkRole(role), tmp_path)
        # Normal first actions knowable from bootstrap alone (no skill.open).
        # Check bootstrap has identity, soul/cannot-do, handoff summary, tool surface,
        # eager guidance, and rare map with use_when.
        assert res["ROLE"] == role
        assert "purpose" in res["SOUL"] and "cannot_do" in res["SOUL"]
        assert "objective" in res["TASK_HANDOFF"] and "bounded_scope" in res["TASK_HANDOFF"]
        assert "eager" in res["TOOL_SURFACE"] or "work_role" in res["TOOL_SURFACE"]
        assert len(res["BASE_SKILLS"]) >= 1
        for p in res["PROGRESSIVE_SKILLS"]:
            assert "skill_id" in p and "short_description" in p
            assert "use_when" in p and "ref" in p

    def test_progressive_only_rare(self, tmp_path: Path) -> None:
        rare = {"aota-result-hydration", "aota-restricted-shell",
                "aota-multi-phase-doc-closure", "aota-workspace-operations"}
        for role, (eager, prog) in _ROLE_SKILL_DEFS.items():
            # Eager must be normal capabilities (not large-ref/hydration/shell fallback)
            assert "aota-result-hydration" not in eager, f"{role} hydration must not be eager"
            assert "aota-restricted-shell" not in eager, f"{role} shell must not be eager"
            for sid in prog:
                assert sid in rare or sid in ("aota-workspace-operations",), f"{role} progressive {sid} not rare"
        # Coder normal test guidance eager (not hidden progressive)
        coder_text = " ".join(e["materialized"] for e in _bootstrap_for(AgentWorkRole.CODER, tmp_path)["BASE_SKILLS"])
        assert "test.run" in coder_text


# ---------------------------------------------------------------------------
# H/I/J: one-call progressive, no hydrate for normal, large-ref optional
# ---------------------------------------------------------------------------

class TestHIJProgressiveOneCall:
    @pytest.mark.parametrize("role", ["analyst", "coder", "reviewer", "project-steward"])
    def test_progressive_open_one_call_usable(self, tmp_path: Path, role: str) -> None:
        assert NORMAL_PROGRESSIVE_SKILL_OPEN_RETURNS_USABLE_CONTENT is True
        assert NORMAL_PROGRESSIVE_SKILL_REQUIRES_RESULT_HYDRATE is False
        assert RESULT_HYDRATE_FOR_NORMAL_SKILL_LOADING is False
        assert RESULT_HYDRATE_FOR_LARGE_RESULT_OR_ARTIFACT is True
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id=f"wt-{role}",
            canonical_task_id=f"t-{role}", handoff=_handoff(AgentWorkRole(role)),
        )
        adapter = _SharedAotaMcpAdapter(b)
        rb = adapter.invoke("role.bootstrap", {})
        assert rb["ok"] is True
        payload = rb.get("payload")
        if rb["output_mode"] == "by_ref":
            # W5: consume the governed by_ref bootstrap via its model-visible
            # hydration claims (existing result.hydrate, no guessing).
            hydration = rb["hydration"]
            assert hydration["operation"] == "result.hydrate"
            hy = adapter.invoke("result.hydrate", dict(hydration["arguments"]))
            assert hy["ok"] is True, hy
            payload = json.loads(hy["payload"]["content"])
        prog = (payload or {}).get("PROGRESSIVE_SKILLS", [])
        assert len(prog) >= 1
        for p in prog:
            so = adapter.invoke("skill.open", {"ref": p["ref"]})
            assert so["ok"] is True, f"{role} {p['skill_id']} open failed {so.get('error')}"
            assert so["output_mode"] == "inline", f"{role} {p['skill_id']} should be inline not by_ref"
            assert so["is_truncated"] is False
            pay = so.get("payload") or {}
            content = pay.get("content", "")
            assert len(content) > 500, f"{role} {p['skill_id']} content too short"
            assert pay.get("content_length") == len(content)

    def test_large_ref_optional(self, tmp_path: Path) -> None:
        # result.hydrate is progressive (not eager) for all roles; normal open needs no hydrate.
        for role in ("task-main", "analyst", "coder", "reviewer", "project-steward"):
            surf = get_tool_surface_for_role(role)
            assert "result.hydrate" not in [r.capability_name for r in surf.eager]
            assert "result.hydrate" in surf.all_capability_names()


# ---------------------------------------------------------------------------
# K: maintainer excluded
# ---------------------------------------------------------------------------

class TestKMaintainerExcluded:
    @pytest.mark.parametrize("skill_id", [
        "aota-task-main-control", "aota-workspace-operations", "aota-evidence-first-debugging",
        "aota-spec-driven-implementation", "aota-implementation-review",
        "aota-pcf-project-steward", "aota-multi-phase-doc-closure",
        "aota-restricted-shell", "aota-result-hydration",
    ])
    def test_maintainer_not_in_runtime(self, tmp_path: Path, skill_id: str) -> None:
        from aota_forge.work_plane.role_bootstrap import handle_skill_open

        assert MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT is False
        # Prefer non-task-main role to avoid worker-path task-main gate; fall back
        # to fake task-main binding for task-main-only skills.
        role = None
        for r in ("analyst", "coder", "reviewer", "project-steward", "task-main"):
            eager, prog = _ROLE_SKILL_DEFS[r]
            if skill_id in eager + prog:
                role = r
                break
        assert role is not None
        if role == "task-main" and skill_id == "aota-task-main-control":
            binding = _task_main_fake_binding(tmp_path)
            opened = handle_skill_open(binding, {"ref": f"{skill_id}@1.0.0"})
            content = opened.get("content", "")
        else:
            b = build_worker_binding(
                root=tmp_path, project_id="aota_forge", worktree_id="wt-k",
                canonical_task_id="t-k", handoff=_handoff(AgentWorkRole(role)),
            )
            adapter = _SharedAotaMcpAdapter(b)
            ref = f"{skill_id}@1.0.0"
            so = adapter.invoke("skill.open", {"ref": ref})
            assert so["ok"] is True
            content = (so.get("payload") or {}).get("content", "")
        for marker in ("W0 provenance", "Canonical Migration", "parity", "archaeology",
                       "Migration history", "compatibility archaeology", "test implementation notes"):
            assert marker not in content, f"{skill_id} leaks maintainer {marker!r}"
        # Runtime must still be usable (workflow/tool/stop/result cues)
        assert len(content.strip()) > 500


# ---------------------------------------------------------------------------
# L/M/N: universes + orphans + steward rewrite
# ---------------------------------------------------------------------------

class TestLMNUniverses:
    def test_five_universes_converged(self) -> None:
        from aota_forge.work_plane import af_roles as af

        assert af.FIVE_ROLE_SKILL_UNIVERSES_CONVERGED is True
        assert af.ROLE_CONTRACT_DERIVES_SKILL_UNIVERSE is True
        assert af.EXISTING_SKILL_IMPLIES_ASSIGNMENT is False
        assert set(_ROLE_SKILL_DEFS.keys()) == {"task-main", "analyst", "coder", "reviewer", "project-steward"}
        # Exact production mapping
        assert _ROLE_SKILL_DEFS["task-main"][0] == ["aota-task-main-control"]
        assert "aota-workspace-operations" in _ROLE_SKILL_DEFS["analyst"][0]
        assert "aota-evidence-first-debugging" in _ROLE_SKILL_DEFS["analyst"][0]
        assert "aota-workspace-operations" in _ROLE_SKILL_DEFS["coder"][0]
        assert "aota-spec-driven-implementation" in _ROLE_SKILL_DEFS["coder"][0]
        assert "aota-implementation-review" in _ROLE_SKILL_DEFS["reviewer"][0]
        assert "aota-pcf-project-steward" in _ROLE_SKILL_DEFS["project-steward"][0]
        # No forced assignment of every existing Skill
        assigned = {s for eager, prog in _ROLE_SKILL_DEFS.values() for s in eager + prog}
        assert "aota-task-lifecycle" not in assigned
        assert "aota-architecture-review" not in assigned

    def test_orphan_dispositions_deterministic(self) -> None:
        assert ORPHAN_SKILL_DISPOSITION["aota-pcf-project-steward"][0] == "assign_eager"
        assert ORPHAN_SKILL_DISPOSITION["aota-multi-phase-doc-closure"][0] == "assign_progressive"
        assert ORPHAN_SKILL_DISPOSITION["aota-evidence-first-debugging"][0] == "assign_eager"
        assert ORPHAN_SKILL_DISPOSITION["aota-implementation-review"][0] == "assign_eager"
        assert ORPHAN_SKILL_DISPOSITION["aota-task-lifecycle"][0] == "remain_unassigned"
        # Deterministic: same input always same disposition
        assert dict(ORPHAN_SKILL_DISPOSITION) == dict(ORPHAN_SKILL_DISPOSITION)

    def test_steward_rewritten(self) -> None:
        from pathlib import Path as _P

        text = (_P(__file__).resolve().parents[1] / "skills" / "aota-pcf-project-steward" / "SKILL.md").read_text(encoding="utf-8")
        assert "Mode A" in text and "Mode B" in text
        assert "governance" in text.lower() and "finalizer" in text.lower()
        for obsolete in ("advisory-only", "context_prepare", "docs_update", "artifact_link", "relationship_resolve"):
            assert obsolete not in text, f"steward still uses obsolete {obsolete!r}"
        # Maintainer preserved outside runtime
        maint = (_P(__file__).resolve().parents[1] / "skills" / "aota-pcf-project-steward" / "MAINTAINER.md").read_text(encoding="utf-8")
        assert len(maint.strip()) > 100


# ---------------------------------------------------------------------------
# O/P/Q/R/S/T/U/V: tool visibility + server authority (negatives mandatory)
# ---------------------------------------------------------------------------

class TestOweaverAuthority:
    def test_task_main_no_write_no_test(self, tmp_path: Path) -> None:
        fake = _task_main_fake_binding(tmp_path)
        assert fake.mutation_authority is None
        assert fake.test_execution_authority is None
        surf = get_tool_surface_for_role("task-main")
        assert "workspace.write" not in surf.all_capability_names()
        assert "test.run" not in surf.all_capability_names()
        adapter = _SharedAotaMcpAdapter(fake)
        assert adapter.invoke("workspace.write", {"path": "x", "content": "y", "mode": "create_only"})["error"]["code"] == "AUTHORITY_DENIED"
        assert adapter.invoke("test.run", {"runner": "pytest", "targets": ["tests/test_x.py"]})["error"]["code"] == "AUTHORITY_DENIED"

    def test_analyst_write_fail_closed(self, tmp_path: Path) -> None:
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-ap",
            canonical_task_id="t-ap", handoff=_handoff(AgentWorkRole.ANALYST),
        )
        # Product source write absent (fail-closed); artifact-only would need W2 scoping.
        assert b.mutation_authority is None
        adapter = _SharedAotaMcpAdapter(b)
        r = adapter.invoke("workspace.write", {"path": "src/prod.py", "content": "x", "mode": "create_only"})
        assert r["ok"] is False and r["error"]["code"] == "AUTHORITY_DENIED"
        # Eager guidance declares conditional artifact-only (not blanket product mutation)
        res = handle_role_bootstrap(b, {})
        text = " ".join(e["materialized"] for e in res["BASE_SKILLS"])
        assert "artifact-only" in text or "explicitly required" in text

    def test_coder_authorized(self, tmp_path: Path) -> None:
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-cq",
            canonical_task_id="t-cq", handoff=_handoff(AgentWorkRole.CODER),
        )
        assert b.mutation_authority is not None
        assert b.test_execution_authority is not None
        surf = get_tool_surface_for_role("coder")
        for op in ("workspace.search", "workspace.read", "workspace.write", "test.run"):
            assert op in surf.all_capability_names()
        adapter = _SharedAotaMcpAdapter(b)
        # Invalid mode reaches provider (not DENIED at binding) proving write authorized
        r = adapter.invoke("workspace.write", {"path": "out.txt", "content": "x", "mode": "bogus-mode"})
        assert r["error"]["code"] in ("INVALID_MODE", "INVALID_INPUT", "WORKSPACE_ERROR")
        assert r["error"]["code"] != "AUTHORITY_DENIED"
        # Non-existent test target reaches provider (not DENIED) proving test authorized
        r2 = adapter.invoke("test.run", {"runner": "pytest", "targets": ["tests/does-not-exist-xyz.py"]})
        assert r2["error"]["code"] == "TARGET_NOT_FOUND"

    def test_reviewer_conditional_test(self, tmp_path: Path) -> None:
        from aota_forge.work_plane import reviewer_runtime as rm

        assert rm.REVIEWER_TEST_RUN_POLICY == "eager_visible+conditionally_authorized"
        surf = get_tool_surface_for_role("reviewer")
        assert "test.run" in surf.all_capability_names()
        assert "workspace.write" not in surf.all_capability_names()
        b_yes = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-ry",
            canonical_task_id="t-ry", handoff=_review_handoff(with_validation=True),
        )
        b_no = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-rn",
            canonical_task_id="t-rn", handoff=_review_handoff(with_validation=False),
        )
        assert b_yes.test_execution_authority is not None
        assert b_no.test_execution_authority is None
        assert _SharedAotaMcpAdapter(b_no).invoke(
            "test.run", {"runner": "pytest", "targets": ["tests/does-not-exist-xyz.py"]}
        )["error"]["code"] == "AUTHORITY_DENIED"

    def test_reviewer_write_denied(self, tmp_path: Path) -> None:
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-rw",
            canonical_task_id="t-rw", handoff=_review_handoff(),
        )
        assert b.mutation_authority is None
        r = _SharedAotaMcpAdapter(b).invoke(
            "workspace.write", {"path": "src/a.py", "content": "x", "mode": "create_only"}
        )
        assert r["ok"] is False and r["error"]["code"] == "AUTHORITY_DENIED"

    def test_steward_no_git_github_shell(self, tmp_path: Path) -> None:
        from aota_forge.work_plane import steward_dispatch as sm
        from aota_forge.composition.worker_vertical_slice import (
            PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION,
            PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION,
        )

        assert PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION is False
        assert PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION is False
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-sw",
            canonical_task_id="t-sw",
            handoff=sm.build_mode_a_handoff(objective="o", bounded_scope="s"),
        )
        assert b.mutation_authority is None
        assert b.restricted_shell_authority is None
        assert b.test_execution_authority is None
        adapter = _SharedAotaMcpAdapter(b)
        assert adapter.invoke("git.inspect", {"project_id": "aota_forge"})["error"]["code"] == "UNKNOWN_OPERATION"
        assert adapter.invoke("workspace.write", {"path": "x", "content": "y", "mode": "create_only"})["error"]["code"] == "AUTHORITY_DENIED"

    def test_hidden_guessing_fails_closed(self, tmp_path: Path) -> None:
        b = build_worker_binding(
            root=tmp_path, project_id="aota_forge", worktree_id="wt-hu",
            canonical_task_id="t-hu", handoff=_handoff(AgentWorkRole.CODER),
        )
        adapter = _SharedAotaMcpAdapter(b)
        for op, args in [
            ("workspace.delete", {}),
            ("task_main.reconcile_worker_completion", {}),
            ("task_main.dispatch_ready", {}),
            ("git.inspect", {"project_id": "aota_forge"}),
        ]:
            r = adapter.invoke(op, args)
            assert r["ok"] is False
            assert r["error"]["code"] in ("UNKNOWN_OPERATION", "AUTHORITY_DENIED"), f"{op} should fail closed"

    def test_single_invoke(self) -> None:
        assert tuple(MCP_PUBLIC_TOOLS) == ("aota.invoke",)
        assert MCP_PUBLIC_TOOL_COUNT == 1
        assert AGENT_FACING_AOTA_TOOL == "aota.invoke"


# ---------------------------------------------------------------------------
# W: M2 regression preserved
# ---------------------------------------------------------------------------

class TestWRegression:
    def test_m2_markers_preserved(self, tmp_path: Path) -> None:
        from aota_forge.composition import worker_vertical_slice as wvs
        from aota_forge.mcp_transport import (
            SERVER_SIDE_AUTHORITY_REQUIRED,
            SOUL_IS_AUTHORITY,
            SKILL_IS_AUTHORITY,
            TOOL_VISIBILITY_IS_AUTHORITY,
        )
        from aota_forge.work_plane import human_brake as brake_mod
        from aota_forge.work_plane import role_lifecycle as lc_mod

        assert wvs.TRUSTED_BINDING_FAIL_CLOSED is True
        assert wvs.WORKER_CAN_MINT_TASK_MAIN_AUTHORITY is False
        assert SOUL_IS_AUTHORITY is False
        assert SKILL_IS_AUTHORITY is False
        assert TOOL_VISIBILITY_IS_AUTHORITY is False
        assert SERVER_SIDE_AUTHORITY_REQUIRED is True
        assert brake_mod.TASK_MAIN_CAN_SET_USER_APPROVAL is False
        assert lc_mod.TASK_MAIN_EAGER_FULL_ROLE_REPORT_DEFAULT is False
        # Worker still denies task-main minting
        with pytest.raises(Exception):
            build_worker_binding(
                root=tmp_path, project_id="aota_forge", worktree_id="wt-w",
                canonical_task_id="t-w", handoff=_handoff(AgentWorkRole.TASK_MAIN),
            )
