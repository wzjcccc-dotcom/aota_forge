"""AF #57 M2/W2 — Progressive Context, Retrieval Policy & Guidance convergence.

V1 contract tests + bounded V2 composition:

* deterministic Governance Context Route over accepted W1 Cards (authority=no,
  bounded logical refs, no host paths, GitHub- and local-bound support);
* ephemeral, reconstructible active Working Set (no second session/context DB)
  with observation reuse and candidate-set semantics;
* CARD_FIRST retrieval policy with FULL/SECTION/QUERY/HYDRATE semantic intents,
  semantic-section-first Markdown retrieval and fixed continuation fallback;
* semantic reuse stays distinct from authority freshness / CAS;
* role.bootstrap exposes compact Governance context from real accepted W1 Cards
  through the accepted #59 bootstrap/Skill/help composition without eager full
  Plan / Architecture / AGENTS / Skill-body loading;
* existing #54 telemetry foundation and the one canonical operation surface are
  reused; no Reader implementation dependency is introduced.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.governance import (
    CANDIDATE_MISS_IS_SEARCH_MISS,
    CARD_FIRST,
    CARD_IS_AUTHORITY,
    CARD_KINDS,
    CONTEXT_ROUTE_IS_AUTHORITY,
    CONTEXT_WORKING_SET_IS_EPHEMERAL,
    CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT,
    FIXED_CONTINUATION_IS_FALLBACK,
    LLM_OWNS_RETRIEVAL_INTENT,
    NO_ARBITRARY_TOKEN_THRESHOLD,
    OBSERVATION_REUSE_FIRST,
    PERSISTENT_OBSERVATION_CACHE_CREATED,
    REDUNDANT_RETRIEVAL_DEFAULT,
    RESEARCH_INVALIDATION_REASONS,
    RETRIEVAL_INTENT_FULL,
    RETRIEVAL_INTENT_HYDRATE,
    RETRIEVAL_INTENT_QUERY,
    RETRIEVAL_INTENT_SECTION,
    RETRIEVAL_INTENTS,
    SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK,
    SEMANTIC_REUSE_IS_AUTHORITY_FRESHNESS,
    SEMANTIC_SECTION_FIRST,
    SESSION_MEMORY_DB_CREATED,
    WORKING_SET_DATABASE_CREATED,
    WORKING_SET_IS_AUTHORITY,
    WORKING_SET_IS_DURABLE,
    CardFirstFacts,
    CandidateMissDisposition,
    CandidateRef,
    CandidateSet,
    ContextRouteEntry,
    ContextRouteError,
    ContextRouteInput,
    GovernanceProjectionEngine,
    GovernanceProjectionInput,
    RetrievalIntentSelection,
    RouteBoundedRef,
    RoutePolicyRef,
    RouteSkillRef,
    build_bootstrap_governance_context,
    WorkingSet,
    WorkingSetEntry,
    WorkingSetError,
    build_context_route,
    continuation_fallback,
    discover_semantic_sections,
    may_research,
    plan_document_input,
)
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.work_plane.agents_applicability import compute_policy_digest
from aota_forge.work_plane.role_bootstrap import RoleBootstrapError, handle_role_bootstrap

REPO_ROOT = Path(__file__).resolve().parent.parent
GOVERNANCE_DIR = REPO_ROOT / "aota_forge" / "governance"
W2_MODULES = ("context_route.py", "working_set.py")

PROJECT_ID = "aota_forge"
PLAN_A = "plan_alpha"
PLAN_B = "plan_beta"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#57"
BASELINE_ID = "AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1"
FULL_PLAN_BODY_MARKER = "FULL_PLAN_BODY_MARKER_MUST_NOT_BE_EAGER"
ARCHITECTURE_BODY_MARKER = "ARCHITECTURE_BODY_MARKER_MUST_NOT_BE_EAGER"
AGENTS_BODY_MARKER = "AGENTS_BODY_MARKER_MUST_NOT_BE_EAGER"

BANNED_GOVERNANCE_IMPORT_PREFIXES = (
    "aota_reader",
    "chatgpt_hermes",
    "chatgpt-hermes",
    "aota_forge.adapters",
)
BANNED_GOVERNANCE_IMPORT_MODULES = {
    "sqlite3",
    "shelve",
    "dbm",
    "threading",
    "asyncio",
    "watchdog",
    "pickle",
}

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


def _plan_body(label: str, *, current: str = "M2") -> str:
    return "\n".join(
        [
            f"# [PLAN] {label} progressive context fixture",
            "",
            "## Current state",
            "",
            "```text",
            "PLAN_TYPE=portable_plan",
            "PLAN_KIND=portable_plan",
            "PLAN_STATUS=active",
            f"FROZEN_BASELINE_ID={BASELINE_ID}",
            "FROZEN_BASELINE_STATUS=approved",
            "PRIMARY_GOAL=bounded W2 fixture goal",
            "```",
            "",
            "## Milestones",
            "",
            "MILESTONE_FIRST=yes",
            "",
            "### M1 — Foundation",
            "M1_STATUS=completed",
            "M1_WORK_ITEMS=W1, W2",
            "M1_DAG=W1 -> W2",
            "",
            "### M2 — Progressive Context",
            "M2_STATUS=in_progress",
            "M2_WORK_ITEMS=W1, W2, W3, W4",
            "M2_DAG=W1 -> W2 -> (W3 || W4) -> RV1",
            "",
            f"{current} objective prose for {label}.",
            "",
            "## Appendix A — long historical body (must never be eager)",
            "",
            f"{FULL_PLAN_BODY_MARKER} " + ("long historical body " * 400),
            f"{ARCHITECTURE_BODY_MARKER} " + ("long architecture narrative " * 200),
            "",
        ]
    )


def _document(label: str, *, revision: str | None = None):
    return normalize_portable_plan(_plan_body(label), source_revision=revision)


def _binding(plan_id: str, *, source_kind: str = "local_governance", revision: str = "r1") -> PlanAuthorityBinding:
    if source_kind == "github_issue":
        authority_ref = PLAN_REF
    else:
        authority_ref = f"local-governance:{PROJECT_ID}/{plan_id}"
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=source_kind,
        authority_ref=authority_ref,
        source_revision=revision,
        source_digest="a" * 64,
    )


def _source(*, plan_id: str = PLAN_A, binding: PlanAuthorityBinding | None = None) -> GovernanceProjectionInput:
    authority = binding or _binding(plan_id)
    return GovernanceProjectionInput(
        project_id=PROJECT_ID,
        project_ref=f"project:{PROJECT_ID}",
        project_status="active",
        governance_root_refs=("local-governance",),
        plan_records=(
            ProjectPlanRecord(
                project_id=PROJECT_ID,
                plan_id=plan_id,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=authority,
                revision=3,
            ),
        ),
        plan_documents=(
            plan_document_input(
                plan_id=plan_id,
                document=_document(plan_id, revision="r1"),
                current_milestone="M2",
                title=f"{plan_id} fixture",
                authority=authority,
            ),
        ),
        architecture=None,
        coordinator_states=(),
        execution_records=(),
    )


def _bundle(*, plan_id: str = PLAN_A, binding: PlanAuthorityBinding | None = None):
    return GovernanceProjectionEngine().rebuild(_source(plan_id=plan_id, binding=binding))


def _route_input(*, bundle=None, **overrides) -> ContextRouteInput:
    values = {
        "project_id": PROJECT_ID,
        "plan_id": PLAN_A,
        "authority_source_kind": "local_governance",
        "authority_ref": f"local-governance:{PROJECT_ID}/{PLAN_A}",
        "authority_revision": "r1",
        "authority_digest": "a" * 64,
        "bundle": bundle,
        "human_brake_ref": None,
        "blocker_refs": (),
        "authorized_root_refs": (("project-main", ("read", "search")),),
        "agents_policy_refs": (),
        "skill_refs": (RouteSkillRef(ref="aota-task-main-governance@1.0.0", use_when="before governance mutation"),),
        "evidence_refs": (),
        "result_refs": (),
        "working_set": None,
    }
    values.update(overrides)
    return ContextRouteInput(**values)


def _module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _checkout(parent: Path, folder: str, project_id: str, origin: str) -> Path:
    root = parent / folder
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(MANIFEST.format(project_id=project_id), encoding="utf-8")
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


def _compose_host(
    tmp_path: Path,
    *,
    plan_ref: str | None = PLAN_REF,
    plan_id: str | None = PLAN_A,
    governance_context=None,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    _checkout(workspace, "checkout-w2", PROJECT_ID, "https://github.com/wzjcccc-dotcom/aota-hermes-tools.git")
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({PROJECT_ID: {"candidates": [str(workspace)]}}), encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir(exist_ok=True)
    return compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-af57-m2w2",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260917_af57_m2w2_session",
        source_repository="wzjcccc-dotcom/aota-hermes-tools",
        registry_path=registry,
        plan_ref=plan_ref,
        plan_id=plan_id,
        governance_context=governance_context,
    )


# ---------------------------------------------------------------------------
# V1 — Context Route
# ---------------------------------------------------------------------------


class TestContextRouteV1:
    def test_five_w1_cards_represented_deterministically(self):
        bundle = _bundle()
        first = build_context_route(_route_input(bundle=bundle))
        second = build_context_route(_route_input(bundle=bundle))
        assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
        assert first.projection_id() == second.projection_id()
        kinds = [entry.kind for entry in first.entries]
        for card_kind in ("project_card", "plan_card", "milestone_card", "architecture_card", "progress_card"):
            assert card_kind in kinds, card_kind
        card_entries = {entry.card_kind: entry for entry in first.entries if entry.card_kind}
        bundle_cards = {card.card_kind: card for card in bundle.cards()}
        for card_kind, card in bundle_cards.items():
            entry = card_entries[card_kind]
            assert entry.projection_id == card.projection_id()
            assert entry.ref == f"card:{card.card_kind}:{card.projection_id()}"
        assert first.cards_present is True
        assert first.missing_facts == ()

    def test_context_route_is_never_authority(self):
        route = build_context_route(_route_input(bundle=_bundle()))
        assert route.is_authority() is False
        assert CONTEXT_ROUTE_IS_AUTHORITY is False
        assert CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT is False
        payload = route.to_dict()
        assert payload["is_authority"] is False
        assert payload["CONTEXT_ROUTE_IS_AUTHORITY"] is False
        for entry in route.entries:
            assert entry.is_authority() is False
            assert entry.to_dict()["IS_AUTHORITY"] is False

    def test_route_rejects_host_paths_and_keeps_bounded_refs(self):
        with pytest.raises(ContextRouteError):
            ContextRouteEntry(kind="plan_authority", ref="/etc/passwd")
        with pytest.raises(ContextRouteError):
            ContextRouteEntry(kind="plan_authority", ref="~/.ssh/id_rsa")
        route = build_context_route(_route_input(bundle=_bundle()))
        serialized = json.dumps(route.to_dict())
        assert "/home/" not in serialized
        assert "\\" not in serialized
        assert all(not entry.ref.startswith(("/", "~")) for entry in route.entries)

    def test_route_supports_github_and_local_plan_bindings(self):
        github = build_context_route(
            _route_input(
                bundle=_bundle(binding=_binding(PLAN_A, source_kind="github_issue")),
                authority_source_kind="github_issue",
                authority_ref=PLAN_REF,
            )
        )
        local = build_context_route(
            _route_input(
                bundle=_bundle(binding=_binding(PLAN_A, source_kind="local_governance")),
                authority_source_kind="local_governance",
                authority_ref=f"local-governance:{PROJECT_ID}/{PLAN_A}",
            )
        )
        github_authority = github.entries_for_kind("plan_authority")
        local_authority = local.entries_for_kind("plan_authority")
        assert github_authority[0].source_kind == "github_issue"
        assert github_authority[0].ref == PLAN_REF
        assert local_authority[0].source_kind == "local_governance"
        assert github.authority_source_kind == "github_issue"
        assert local.authority_source_kind == "local_governance"

    def test_route_is_bounded_and_reports_missing_sources(self):
        many = tuple(
            RouteBoundedRef(ref=f"evidence:af57/{index:03d}", complete=False, missing_facts=("digest",))
            for index in range(70)
        )
        route = build_context_route(_route_input(bundle=_bundle(), evidence_refs=many))
        assert len(route.entries) <= 64
        assert "route_entries_bounded" in route.warnings

        thin = build_context_route(ContextRouteInput(project_id=PROJECT_ID))
        assert thin.cards_present is False
        assert "governance_cards" in thin.missing_facts
        assert "plan_authority_ref" in thin.missing_facts
        assert thin.entries == ()


# ---------------------------------------------------------------------------
# V1 — Working Set
# ---------------------------------------------------------------------------


class TestWorkingSetV1:
    def test_working_set_is_ephemeral_and_non_durable(self):
        assert CONTEXT_WORKING_SET_IS_EPHEMERAL is True
        assert WORKING_SET_IS_DURABLE is False
        assert WORKING_SET_IS_AUTHORITY is False
        assert WORKING_SET_DATABASE_CREATED is False
        assert SESSION_MEMORY_DB_CREATED is False
        assert PERSISTENT_OBSERVATION_CACHE_CREATED is False
        ws = WorkingSet(project_id=PROJECT_ID)
        ws.add(WorkingSetEntry(ref="doc:plan", kind="document", digest="b" * 64, revision="r1"))
        payload = ws.to_dict()
        assert payload["CONTEXT_WORKING_SET_IS_EPHEMERAL"] is True
        assert payload["WORKING_SET_IS_DURABLE"] is False
        assert payload["IS_AUTHORITY"] is False
        assert payload["working_set_id"].startswith("wset-")
        for name in ("save", "persist", "flush", "dump", "open"):
            assert not hasattr(ws, name)

    def test_working_set_imports_no_storage_or_runtime(self):
        for filename in W2_MODULES:
            imports = _module_imports(GOVERNANCE_DIR / filename)
            for imported in imports:
                assert imported not in BANNED_GOVERNANCE_IMPORT_MODULES, (filename, imported)
                assert not imported.startswith(BANNED_GOVERNANCE_IMPORT_PREFIXES), (filename, imported)

    def test_same_identity_reusable_changed_identity_stale(self):
        ws = WorkingSet(project_id=PROJECT_ID)
        ws.add(
            WorkingSetEntry(
                ref="result:af57/w1",
                kind="result",
                digest="c" * 64,
                revision="rev-7",
                use_when="prior W1 result",
            )
        )
        reuse = ws.observation_status("result:af57/w1", digest="c" * 64, revision="rev-7")
        assert reuse.may_reuse_semantically() is True
        assert reuse.status == "reusable"
        changed_digest = ws.observation_status("result:af57/w1", digest="d" * 64)
        assert changed_digest.may_reuse_semantically() is False
        assert changed_digest.status == "stale"
        assert changed_digest.reason == "source_changed"
        changed_revision = ws.observation_status("result:af57/w1", revision="rev-8")
        assert changed_revision.status == "stale"
        absent = ws.observation_status("result:af57/missing")
        assert absent.status == "absent"
        assert absent.requires_fresh_authority_read() is True
        assert OBSERVATION_REUSE_FIRST is True
        assert REDUNDANT_RETRIEVAL_DEFAULT is False

    def test_incomplete_result_never_presented_as_complete(self):
        with pytest.raises(WorkingSetError):
            WorkingSetEntry(ref="result:x", kind="result", complete=True, missing_facts=("digest",))
        with pytest.raises(WorkingSetError):
            WorkingSetEntry(ref="result:x", kind="result", complete=False)
        ws = WorkingSet(project_id=PROJECT_ID)
        ws.add(
            WorkingSetEntry(
                ref="result:y",
                kind="result",
                complete=False,
                missing_facts=("content",),
            )
        )
        status = ws.observation_status("result:y")
        assert status.status == "incomplete"
        assert status.may_reuse_semantically() is False

    def test_real_w1_card_becomes_bounded_working_set_entry(self):
        bundle = _bundle()
        ws = WorkingSet(project_id=PROJECT_ID)
        entry = ws.add_card(bundle.project)
        assert entry.kind == "card"
        assert entry.ref.startswith("card:project_card:gcard-")
        assert entry.card_projection_id == bundle.project.projection_id()
        assert entry.complete is True
        facts = entry.card_first_facts()
        assert isinstance(facts, CardFirstFacts)
        assert facts.complete is True
        assert facts.ref == entry.ref
        assert facts.size_bytes is None  # unknown size is never fabricated


# ---------------------------------------------------------------------------
# V1 — Retrieval policy
# ---------------------------------------------------------------------------


class TestRetrievalPolicyV1:
    def test_card_first_facts_exposed_for_unknown_sources(self):
        bundle = _bundle()
        plan_card = next(card for card in bundle.cards() if card.card_kind == "plan_card")
        facts = CardFirstFacts.from_card(plan_card)
        payload = facts.to_dict()
        assert payload["kind"] == "plan_card"
        assert payload["complete"] is True
        assert payload["ref"] == f"card:plan_card:{plan_card.projection_id()}"
        assert payload["IS_AUTHORITY"] is False
        assert CARD_FIRST is True
        missing = CardFirstFacts(
            ref="result:incomplete",
            kind="result",
            complete=False,
            missing_facts=("structure",),
        )
        assert missing.to_dict()["missing_facts"] == ["structure"]

    def test_all_four_intents_are_llm_selectable_without_thresholds(self):
        assert RETRIEVAL_INTENTS == {
            RETRIEVAL_INTENT_FULL,
            RETRIEVAL_INTENT_SECTION,
            RETRIEVAL_INTENT_QUERY,
            RETRIEVAL_INTENT_HYDRATE,
        }
        selection_full = RetrievalIntentSelection(intent=RETRIEVAL_INTENT_FULL, ref="doc:plan")
        selection_section = RetrievalIntentSelection(
            intent=RETRIEVAL_INTENT_SECTION, ref="doc:plan", section="milestones"
        )
        selection_query = RetrievalIntentSelection(intent=RETRIEVAL_INTENT_QUERY, ref="doc:plan", query="M2 DAG")
        selection_hydrate = RetrievalIntentSelection(intent=RETRIEVAL_INTENT_HYDRATE, ref="result:af57/w1")
        assert [selection.intent for selection in (selection_full, selection_section, selection_query, selection_hydrate)] == [
            "full",
            "section",
            "query",
            "hydrate",
        ]
        with pytest.raises(WorkingSetError):
            RetrievalIntentSelection(intent="scan", ref="doc:plan")
        with pytest.raises(WorkingSetError):
            RetrievalIntentSelection(intent=RETRIEVAL_INTENT_QUERY, ref="doc:plan")
        assert LLM_OWNS_RETRIEVAL_INTENT is True
        assert CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT is False
        assert NO_ARBITRARY_TOKEN_THRESHOLD is True
        source = (GOVERNANCE_DIR / "working_set.py").read_text(encoding="utf-8")
        assert not re.search(r"if .*size_bytes.*(?:RETRIEVAL_INTENT|intent)", source)
        assert not re.search(r"(?:bytes|size)[^\n]{0,40}(?:->|then)\s*(?:section|full)", source, re.IGNORECASE)

    def test_semantic_section_discovery_prefers_headings(self):
        text = "\n".join(
            [
                "# Plan",
                "",
                "preamble",
                "",
                "## Milestones",
                "",
                "M1, M2",
                "",
                "### M2 — Progress",
                "",
                "details",
                "",
                "```text",
                "# not a heading inside a fence",
                "```",
                "",
                "## Milestones",
                "",
                "duplicate marker",
                "",
            ]
        )
        sections = discover_semantic_sections(text, ref="doc:plan")
        markers = [section.marker for section in sections]
        assert "not a heading inside a fence" not in markers
        assert markers.count("Milestones") == 2
        refs = [section.ref for section in sections]
        assert len(refs) == len(set(refs))
        assert refs[0] == "doc:plan#plan"
        milestones_sections = [section for section in sections if section.marker == "Milestones"]
        assert milestones_sections[0].level == 2
        assert milestones_sections[0].end_offset <= milestones_sections[0].start_offset + len(text)
        first_slice = milestones_sections[0].bounded_slice(text)
        assert "M1, M2" in first_slice
        assert "details" in first_slice  # nested subsections stay in the parent section
        assert "duplicate marker" not in first_slice  # section ends at the next same-level heading
        assert "duplicate marker" in milestones_sections[1].bounded_slice(text)
        assert SEMANTIC_SECTION_FIRST is True

    def test_fixed_continuation_is_fallback(self):
        fallback = continuation_fallback(ref="doc:plan", offset=1024, limit=2048)
        payload = fallback.to_dict()
        assert payload["FIXED_CONTINUATION_IS_FALLBACK"] is True
        assert payload["reason"] == "fallback_after_semantic_sections"
        assert FIXED_CONTINUATION_IS_FALLBACK is True

    def test_candidate_miss_preserves_remaining_candidates(self):
        candidates = CandidateSet(
            candidate_set_id="cand:af57/search-1",
            candidates=(
                CandidateRef(ref="evidence:c"),
                CandidateRef(ref="evidence:d"),
                CandidateRef(ref="evidence:e"),
            ),
            query="progressive context",
            scope="project-main:docs",
            intent=RETRIEVAL_INTENT_QUERY,
        )
        disposition = CandidateMissDisposition(candidate_set=candidates, missed_ref="evidence:c")
        remaining = [candidate.ref for candidate in disposition.reuse_remaining()]
        assert remaining == ["evidence:d", "evidence:e"]
        assert disposition.search_miss is False
        assert disposition.to_dict()["search_miss"] is False
        assert CANDIDATE_MISS_IS_SEARCH_MISS is False
        assert candidates.without("evidence:e").remaining() == (CandidateRef(ref="evidence:c"), CandidateRef(ref="evidence:d"))
        assert candidates.is_exhausted(("evidence:c", "evidence:d", "evidence:e")) is True

    def test_bounded_invalidation_permits_research(self):
        expected = {
            "candidate_set_exhausted",
            "source_changed",
            "query_changed",
            "scope_changed",
            "intent_changed",
            "prior_observation_incomplete",
            "host_compaction_removed_required_material",
            "explicit_freshness_or_precondition_required",
        }
        assert RESEARCH_INVALIDATION_REASONS == expected
        for reason in sorted(expected):
            assert may_research(reason) is True
        with pytest.raises(WorkingSetError):
            may_research("felt_like_searching_again")


# ---------------------------------------------------------------------------
# V1 — semantic reuse vs authority freshness
# ---------------------------------------------------------------------------


class TestFreshnessBoundaryV1:
    def test_semantic_reuse_is_not_authority_freshness(self):
        assert SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK is False
        assert SEMANTIC_REUSE_IS_AUTHORITY_FRESHNESS is False
        ws = WorkingSet(project_id=PROJECT_ID)
        ws.add(WorkingSetEntry(ref="doc:plan", kind="document", digest="e" * 64, revision="r1"))
        reusable = ws.observation_status("doc:plan", digest="e" * 64, revision="r1")
        assert reusable.may_reuse_semantically() is True
        assert reusable.to_dict()["SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK"] is False
        for filename in W2_MODULES:
            imports = _module_imports(GOVERNANCE_DIR / filename)
            assert not any(imported.startswith("aota_forge.adapters") for imported in imports), filename
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            assert "PlanAuthorityMutationPort" not in source
            assert "verify_after_write" not in source
            assert "require_bound_authority" not in source


# ---------------------------------------------------------------------------
# V2 — progressive Governance bootstrap over real accepted inputs
# ---------------------------------------------------------------------------


def _agents_policy_refs(host) -> tuple[RoutePolicyRef, ...]:
    from aota_forge.work_plane.agents_applicability import resolve_applicable_policies
    from aota_forge.work_plane.agents_discovery import discover_agents

    sandbox = host.trusted_binding.sandbox
    resolved = resolve_applicable_policies(discover_agents(sandbox), sandbox.project_id)
    return tuple(
        RoutePolicyRef(
            ref=str(candidate.provenance_ref or f"agents:{candidate.scope or 'root'}"),
            scope=candidate.scope or "root",
            digest=candidate.content_digest,
            read_path=f"{candidate.scope}/AGENTS.md" if candidate.scope else "AGENTS.md",
            use_when="applicable AGENTS policy for this scope; read the policy before relying on it",
        )
        for candidate in resolved
    )


def _governance_payload(host, *, bundle, include_agents: bool = False):
    from aota_forge.work_plane.af_roles import progressive_skill_metadata

    plan_authority_binding = host.trusted_binding.plan_authority_binding
    assert plan_authority_binding is not None
    revision = getattr(plan_authority_binding, "source_revision", None)
    return build_bootstrap_governance_context(
        project_id=PROJECT_ID,
        plan_id=plan_authority_binding.plan_id,
        authority_source_kind=plan_authority_binding.source_kind,
        authority_ref=plan_authority_binding.authority_ref,
        authority_revision=str(revision) if revision is not None else None,
        authority_digest=getattr(plan_authority_binding, "source_digest", None),
        bundle=bundle,
        authorized_root_refs=(("project-main", ("read", "search")),),
        agents_policy_refs=_agents_policy_refs(host) if include_agents else (),
        skill_refs=tuple(
            RouteSkillRef(ref=entry["ref"], use_when=entry.get("use_when"))
            for entry in progressive_skill_metadata("task-main")
        ),
    )


class TestBootstrapV2:
    def test_compact_governance_context_exposed_from_real_w1_cards(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        bundle = _bundle(binding=_binding(PLAN_A, source_kind="github_issue"))
        payload = _governance_payload(host, bundle=bundle)
        result = handle_role_bootstrap(host.trusted_binding, {}, governance_context=payload)
        governance = result["GOVERNANCE_CONTEXT"]
        assert governance["CARDS_PRESENT"] is True
        assert governance["IS_AUTHORITY"] is False
        assert governance["FULL_PLAN_EAGER_HYDRATION"] is False
        assert governance["FULL_AGENTS_EAGER_LOAD"] is False
        assert governance["CONTEXT_WORKING_SET_IS_EPHEMERAL"] is True
        cards = governance["CARDS"]
        assert set(cards) >= {"project", "plans", "milestones", "architecture", "progress", "projection_digest"}
        assert cards["projection_digest"] == bundle.projection_digest()
        route = governance["CONTEXT_ROUTE"]
        kinds = {entry["kind"] for entry in route["entries"]}
        assert {"project_card", "plan_card", "milestone_card", "architecture_card", "progress_card"} <= kinds
        assert {"plan_authority", "authorized_root", "skill"} <= kinds
        assert route["is_authority"] is False
        authority = [entry for entry in route["entries"] if entry["kind"] == "plan_authority"]
        assert authority and authority[0]["source_kind"] == "github_issue"
        assert authority[0]["ref"] == PLAN_REF
        assert result["PROGRESSIVE_SKILLS"]
        assert all("content" not in entry and "materialized" not in entry for entry in result["PROGRESSIVE_SKILLS"])
        assert result["GOVERNANCE_CONTEXT"]["CONTEXT_ROUTE"]["projection_id"] == route["projection_id"]

    def test_trusted_carrier_is_normalized_to_bounded_plain_data(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        payload = _governance_payload(host, bundle=_bundle(binding=_binding(PLAN_A, source_kind="github_issue")))
        payload["EXTRA_FACTS"] = {"b": 1, "a": (2, 3)}
        result = handle_role_bootstrap(host.trusted_binding, {}, governance_context=payload)
        embedded = result["GOVERNANCE_CONTEXT"]
        assert embedded["EXTRA_FACTS"] == {"a": [2, 3], "b": 1}
        assert isinstance(embedded["CARDS"]["project"], dict)
        assert isinstance(embedded["CONTEXT_ROUTE"]["entries"], list)

    def test_full_plan_architecture_and_agents_bodies_not_eager(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        agents_body = f"# Project AGENTS policy\n{AGENTS_BODY_MARKER}\n"
        (tmp_path / "active-worktree" / "AGENTS.md").write_text(agents_body, encoding="utf-8")
        bundle = _bundle(binding=_binding(PLAN_A, source_kind="github_issue"))
        payload = _governance_payload(host, bundle=bundle, include_agents=True)
        result = handle_role_bootstrap(host.trusted_binding, {}, governance_context=payload)
        rendered = json.dumps(result)
        assert FULL_PLAN_BODY_MARKER not in rendered
        assert ARCHITECTURE_BODY_MARKER not in rendered
        assert AGENTS_BODY_MARKER not in rendered
        governance = result["GOVERNANCE_CONTEXT"]
        route = governance["CONTEXT_ROUTE"]
        policy_entries = [entry for entry in route["entries"] if entry["kind"] == "agents_policy"]
        assert len(policy_entries) == 1
        assert policy_entries[0]["read_path"] == "AGENTS.md"
        assert policy_entries[0]["digest"] == compute_policy_digest(agents_body)
        assert policy_entries[0]["scope"] == "root"
        architecture = governance["CARDS"]["architecture"]
        assert architecture["baseline_id"] == BASELINE_ID
        assert "body" not in architecture
        plan_entries = governance["CARDS"]["plans"]
        assert plan_entries and all("body" not in plan for plan in plan_entries)

    def test_ephemeral_working_set_enters_compact_context(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        bundle = _bundle(binding=_binding(PLAN_A, source_kind="github_issue"))
        working_set = WorkingSet(project_id=PROJECT_ID)
        working_set.add_card(bundle.project)
        working_set.add(
            WorkingSetEntry(
                ref="result:af57/w1",
                kind="result",
                digest="f" * 64,
                use_when="prior W1 source-ready result; reuse while digest is unchanged",
            )
        )
        plan_authority_binding = host.trusted_binding.plan_authority_binding
        payload = build_bootstrap_governance_context(
            project_id=PROJECT_ID,
            plan_id=plan_authority_binding.plan_id,
            authority_source_kind=plan_authority_binding.source_kind,
            authority_ref=plan_authority_binding.authority_ref,
            bundle=bundle,
            working_set=working_set,
        )
        result = handle_role_bootstrap(host.trusted_binding, {}, governance_context=payload)
        governance = result["GOVERNANCE_CONTEXT"]
        assert governance["CONTEXT_WORKING_SET_IS_EPHEMERAL"] is True
        assert governance["WORKING_SET"]["entry_count"] == 2
        assert governance["WORKING_SET"]["IS_AUTHORITY"] is False
        route = governance["CONTEXT_ROUTE"]
        observation_entries = [entry for entry in route["entries"] if entry["kind"] == "result"]
        assert observation_entries and observation_entries[0]["ref"] == "result:af57/w1"
        assert observation_entries[0]["digest"] == "f" * 64

    def test_github_bound_context_requires_no_local_governance(self, tmp_path: Path):
        host = _compose_host(tmp_path, plan_ref=PLAN_REF, plan_id=PLAN_A)
        bundle = _bundle(binding=_binding(PLAN_A, source_kind="github_issue"))
        payload = _governance_payload(host, bundle=bundle)
        result = handle_role_bootstrap(host.trusted_binding, {}, governance_context=payload)
        governance = result["GOVERNANCE_CONTEXT"]
        assert governance["CONTEXT_ROUTE"]["authority_source_kind"] == "github_issue"
        assert result["PLAN_AUTHORITY"]["source_kind"] == "github_issue"
        assert result["PLAN_AUTHORITY"]["authority_ref"] == PLAN_REF
        assert "local-governance" not in json.dumps(governance["CONTEXT_ROUTE"])

    def test_no_carrier_means_no_governance_context(self, tmp_path: Path):
        host = _compose_host(tmp_path, plan_ref=None, plan_id=None)
        result = handle_role_bootstrap(host.trusted_binding, {})
        assert "GOVERNANCE_CONTEXT" not in result

    def test_production_host_carries_context_through_canonical_dispatch(self, tmp_path: Path):
        seed_root = tmp_path / "seed"
        seed_root.mkdir()
        source_host = _compose_host(seed_root)
        bundle = _bundle(binding=_binding(PLAN_A, source_kind="github_issue"))
        payload = _governance_payload(source_host, bundle=bundle)

        bound_root = tmp_path / "bound"
        bound_root.mkdir()
        host = _compose_host(bound_root, governance_context=payload)
        assert host.governance_context == payload
        assert host.trusted_binding.governance_context == payload

        direct = host.role_guidance()
        assert direct["GOVERNANCE_CONTEXT"]["CONTEXT_ROUTE"]["projection_id"] == payload["CONTEXT_ROUTE"]["projection_id"]

        response = host.invoke("role.bootstrap", {})
        assert response["ok"] is True, response
        if response["payload"] is not None:
            projected = response["payload"]
        else:
            assert response["output_mode"] == "by_ref"
            hydrated = host.invoke("result.hydrate", dict(response["output_ref"]))
            assert hydrated["ok"] is True, hydrated
            content = hydrated["payload"].get("content")
            projected = json.loads(content) if isinstance(content, str) else hydrated["payload"]
        assert projected["GOVERNANCE_CONTEXT"]["CONTEXT_ROUTE"]["projection_id"] == payload["CONTEXT_ROUTE"]["projection_id"]

    def test_invalid_carrier_fails_closed(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        with pytest.raises(RoleBootstrapError) as non_mapping:
            handle_role_bootstrap(host.trusted_binding, {}, governance_context=object())
        assert non_mapping.value.code == "INVALID_INPUT"

        payload = _governance_payload(host, bundle=_bundle(binding=_binding(PLAN_A, source_kind="github_issue")))
        authority_claim = dict(payload)
        authority_claim["IS_AUTHORITY"] = True
        with pytest.raises(RoleBootstrapError) as claimed:
            handle_role_bootstrap(host.trusted_binding, {}, governance_context=authority_claim)
        assert claimed.value.code == "INVALID_INPUT"

        route_claim = dict(payload)
        route_claim["CONTEXT_ROUTE"] = dict(payload["CONTEXT_ROUTE"])
        route_claim["CONTEXT_ROUTE"]["is_authority"] = True
        with pytest.raises(RoleBootstrapError) as authoritative_route:
            handle_role_bootstrap(host.trusted_binding, {}, governance_context=route_claim)
        assert authoritative_route.value.code == "INVALID_INPUT"

        unbounded = dict(payload)
        unbounded["LIVE_OBJECT"] = object()
        with pytest.raises(RoleBootstrapError) as not_plain:
            handle_role_bootstrap(host.trusted_binding, {}, governance_context=unbounded)
        assert not_plain.value.code == "INVALID_INPUT"

    def test_role_bootstrap_stays_free_of_governance_import_closure(self):
        imports = _module_imports(REPO_ROOT / "aota_forge" / "work_plane" / "role_bootstrap.py")
        assert not [name for name in imports if name.startswith("aota_forge.governance")]
        assert not [name for name in imports if name == "aota_forge.runtime.task_main" or name.startswith("aota_forge.runtime.task_main.")]


# ---------------------------------------------------------------------------
# V2 — guidance / surface / telemetry guards
# ---------------------------------------------------------------------------


class TestGuidanceSurfaceGuardsV2:
    def test_happy_path_operation_use_does_not_require_help_every_call(self, tmp_path: Path):
        host = _compose_host(tmp_path)
        result = handle_role_bootstrap(host.trusted_binding, {})
        base_skills = result["BASE_SKILLS"]
        assert base_skills
        assert all(entry.get("materialized") for entry in base_skills)
        assert all(entry.get("is_truncated") is False for entry in base_skills)
        control = (REPO_ROOT / "skills" / "aota-task-main-control" / "SKILL.md").read_text(encoding="utf-8")
        assert "## Retrieval policy (card-first, section-first)" in control
        assert "CARD_FIRST=yes" in control
        assert "SEMANTIC_SECTION_FIRST=yes" in control
        assert "LLM_OWNS_RETRIEVAL_INTENT=yes" in control
        assert "help(operation=...)" in control
        governance = (REPO_ROOT / "skills" / "aota-task-main-governance" / "SKILL.md").read_text(encoding="utf-8")
        assert "CONTEXT_ROUTE_IS_AUTHORITY=no" in governance
        assert "SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK=no" in governance
        assert "CARD_FIRST=yes" in governance

    def test_no_new_public_operation_surface(self):
        from aota_forge import mcp_transport

        assert len(mcp_transport.LOGICAL_OPERATIONS) == 29
        assert len(set(mcp_transport.LOGICAL_OPERATIONS)) == len(mcp_transport.LOGICAL_OPERATIONS)
        for banned in (
            "governance.search",
            "governance.open",
            "context.route.open",
            "working_set.search",
            "working_set.open",
            "context.route",
            "working_set",
        ):
            assert banned not in mcp_transport.LOGICAL_OPERATIONS, banned
        from aota_forge.governance import context_route as context_route_module

        assert context_route_module.NEW_GOVERNANCE_SEARCH_OPERATION is False
        assert context_route_module.NEW_GOVERNANCE_OPEN_OPERATION is False

    def test_help_remains_mechanical_projection_of_descriptors(self):
        from aota_forge.core_ingress import resolve_descriptor
        from aota_forge.mcp_transport import HELP_OPERATION, help_operation_projection

        descriptor = resolve_descriptor("workspace.read")
        projection = help_operation_projection("workspace.read")
        declared = {spec.name: spec.type for spec in descriptor.inputs}
        required = sorted(name for name, type_text in declared.items() if not type_text.endswith("?"))
        optional = sorted(name for name, type_text in declared.items() if type_text.endswith("?"))
        assert projection["operation"] == descriptor.name
        assert projection["required_inputs"] == required
        assert projection["optional_inputs"] == optional
        assert projection["read_or_write"] == descriptor.read_write
        assert projection["IS_AUTHORITY"] is False
        assert HELP_OPERATION == "help"

    def test_existing_telemetry_foundation_reused(self):
        from aota_forge.work_plane.telemetry_effectiveness_card import build_tool_effectiveness_card

        card = build_tool_effectiveness_card(project_id=PROJECT_ID)
        payload = card.to_dict()
        assert payload["project_id"] == PROJECT_ID
        for fact in (
            "exact_duplicate_search_count",
            "repeated_file_read_count",
            "tool_calls_per_completed_work",
        ):
            assert fact in payload, fact
        for filename in W2_MODULES:
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            assert "telemetry" not in source
            assert "ToolEffectiveness" not in source

    def test_no_reader_implementation_dependency(self):
        from aota_forge.governance import context_route as context_route_module
        from aota_forge.governance import working_set as working_set_module

        assert context_route_module.SECOND_RETRIEVAL_FRAMEWORK is False
        assert working_set_module.READER_IMPLEMENTATION_IMPORTED is False
        for filename in W2_MODULES:
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            assert "aota_reader" not in source
            assert "chatgpt_hermes" not in source
        assert "Reader" not in (GOVERNANCE_DIR / "context_route.py").read_text(encoding="utf-8")

    def test_w1_projection_contracts_unchanged(self):
        bundle = _bundle()
        kinds = [card.card_kind for card in bundle.cards()]
        assert kinds == [
            "project_card",
            "plan_card",
            "milestone_card",
            "architecture_card",
            "progress_card",
        ]
        assert CARD_IS_AUTHORITY is False
        assert all(card.is_authority() is False for card in bundle.cards())
        first = GovernanceProjectionEngine().rebuild(_source())
        second = GovernanceProjectionEngine().rebuild(_source())
        assert first.projection_digest() == second.projection_digest()
        assert GovernanceProjectionEngine().CARD_KINDS_IMPLEMENTED == CARD_KINDS
