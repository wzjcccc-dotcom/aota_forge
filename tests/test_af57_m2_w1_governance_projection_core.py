"""AF #57 M2/W1 — Governance Projection Core & Working-Set Card Contracts.

V1 contract tests + bounded V2 composition over real accepted M1 stores and
interfaces:

    Project Governance Store (SQLite via composition seam)
    trusted local-governance root read adapter
    TaskMainCoordinatorState / FileBackedTaskMainCoordinatorStore
    DurableExecutionRecord / FileBackedExecutionStateStore
    disposable MAP/STATUS generation

Proves W1-AC1..W1-AC14 semantics: five deterministic derived Cards, explicit
completeness, bounded source/projection identity, no duplicated state owners,
narrow non-generic projection engine, generated non-authoritative views, and
no watcher/event bus/projection DB/Reader dependency.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
)
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    ExecutionPhase,
    FileBackedExecutionStateStore,
)
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.governance import (
    CARD_DIGEST_IS_AUTHORITY,
    CARD_IS_AUTHORITY,
    CARD_KINDS,
    CARD_REF_IS_AUTHORITY,
    MANUAL_MAP_PROGRESS_TRUTH,
    MANUAL_STATUS_PROGRESS_TRUTH,
    MAP_AUTHORITY,
    MAP_GENERATED,
    PROJECT_GOVERNANCE_STATE_DUPLICATED,
    PROJECT_GOVERNANCE_STORE_OWNS_COORDINATOR_STATE,
    PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_STATE,
    READER_CARD_ONTOLOGY_IMPORTED,
    READER_IMPLEMENTATION_IMPORTED,
    SECOND_GENERIC_PROJECTION_FRAMEWORK,
    STATUS_AUTHORITY,
    STATUS_GENERATED,
    ArchitectureStateInput,
    CoordinatorProgressFact,
    GovernanceCard,
    GovernanceCardError,
    GovernanceProjectionEngine,
    GovernanceProjectionError,
    GovernanceProjectionInput,
    MilestoneCard,
    PlanDocumentInput,
    PlanLifecycleFact,
    ProjectCard,
    ProjectionRefreshRequest,
    architecture_state_from_plan_document,
    generate_views,
    plan_document_input,
    render_map_markdown,
    render_status_markdown,
    write_generated_views,
)
from aota_forge.governance import projection as projection_module
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectPlanRecord,
)
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

REPO_ROOT = Path(__file__).resolve().parent.parent
GOVERNANCE_DIR = REPO_ROOT / "aota_forge" / "governance"
W1_MODULES = ("cards.py", "projection.py", "generated_views.py")

PROJECT_ID = "aota_forge"
PLAN_A = "plan_alpha"
PLAN_B = "plan_beta"
BASELINE_ID = "AF-PROJECT-GOVERNANCE-2.0-FROZEN-v1"

BANNED_IMPORT_PREFIXES = ("aota_forge.core.projection", "aota_reader", "chatgpt_hermes", "chatgpt-hermes")
BANNED_IMPORT_MODULES = {"sqlite3", "threading", "asyncio", "watchdog", "sqlalchemy", "os", "sys"}
BANNED_SYMBOLS = ("EventBus", "PluginRegistry", "ProjectionDatabase", "FilesystemWatcher", "FilePathWatcher")


def _plan_body(label: str, *, baseline_id: str | None = BASELINE_ID, current: str = "M2") -> str:
    lines = [
        f"# [PLAN] {label} projection fixture",
        "",
        "## Current state",
        "",
        "```text",
        "PLAN_TYPE=portable_plan",
        "PLAN_KIND=portable_plan",
        f"PROJECT_ID={PROJECT_ID}",
        "PLAN_STATUS=active",
        f"CURRENT_MILESTONE={current}",
    ]
    if baseline_id is not None:
        lines.append(f"FROZEN_BASELINE_ID={baseline_id}")
        lines.append("FROZEN_BASELINE_STATUS=approved")
    lines += [
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
    ]
    return "\n".join(lines)


def _document(label: str, *, baseline_id: str | None = BASELINE_ID, revision: str | None = None):
    return normalize_portable_plan(
        _plan_body(label, baseline_id=baseline_id),
        source_revision=revision,
    )


def _binding(plan_id: str, *, revision: str | None = None, digest: str | None = None) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind="local_governance",
        authority_ref=f"local-governance:{PROJECT_ID}/{plan_id}",
        source_revision=revision,
        source_digest=digest,
    )


def _record(
    plan_id: str,
    *,
    lifecycle: str = PLAN_LIFECYCLE_ACTIVE,
    binding: PlanAuthorityBinding | None = None,
    revision: int = 1,
) -> ProjectPlanRecord:
    return ProjectPlanRecord(
        project_id=PROJECT_ID,
        plan_id=plan_id,
        lifecycle_state=lifecycle,
        authority=binding or _binding(plan_id),
        revision=revision,
    )


def _plan_input(
    plan_id: str,
    *,
    baseline_id: str | None = BASELINE_ID,
    revision: str | None = None,
    current: str | None = "M2",
    binding: PlanAuthorityBinding | None = None,
):
    return plan_document_input(
        plan_id=plan_id,
        document=_document(plan_id, baseline_id=baseline_id, revision=revision),
        current_milestone=current,
        title=f"{plan_id} fixture",
        authority=binding or _binding(plan_id),
    )


def _source(
    *,
    records=(),
    documents=(),
    architecture=None,
    coordinators=(),
    executions=(),
    project_ref: str | None = "project:aota_forge",
):
    return GovernanceProjectionInput(
        project_id=PROJECT_ID,
        project_ref=project_ref,
        project_status="active",
        plan_records=tuple(records),
        plan_documents=tuple(documents),
        architecture=architecture,
        coordinator_states=tuple(coordinators),
        execution_records=tuple(executions),
    )


def _engine() -> GovernanceProjectionEngine:
    return GovernanceProjectionEngine()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# W1-AC1/W1-AC3/W1-AC11 — card contract, determinism, projection identity
# ---------------------------------------------------------------------------


class TestCardContractsAndDeterminism:
    def test_all_five_card_kinds_exist(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        kinds = [card.card_kind for card in bundle.cards()]
        assert kinds == [
            "project_card",
            "plan_card",
            "milestone_card",
            "architecture_card",
            "progress_card",
        ]
        assert set(kinds) == CARD_KINDS
        assert all(isinstance(card, GovernanceCard) for card in bundle.cards())

    def test_same_snapshot_yields_identical_bytes_and_projection_id(self):
        source = _source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),))
        first = _engine().rebuild(source)
        second = _engine().rebuild(source)
        assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
        assert first.projection_digest() == second.projection_digest()
        for card_a, card_b in zip(first.cards(), second.cards()):
            assert card_a.projection_id() == card_b.projection_id()
            assert card_a.projection_id().startswith("gcard-")

    def test_upstream_revision_change_changes_projection_identity_deterministically(self):
        source_r1 = _source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A, revision="r1"),))
        source_r2 = _source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A, revision="r2"),))
        card_r1 = _engine().rebuild(source_r1).plans[0]
        card_r2 = _engine().rebuild(source_r2).plans[0]
        assert card_r1.source_revision == "r1"
        assert card_r2.source_revision == "r2"
        assert card_r1.projection_id() != card_r2.projection_id()
        repeated = _engine().rebuild(source_r2).plans[0]
        assert repeated.projection_id() == card_r2.projection_id()

    def test_document_digest_change_changes_projection_identity(self):
        changed_document = _document(PLAN_A, baseline_id="AF-OTHER-BASELINE-v9")
        changed_input = plan_document_input(
            plan_id=PLAN_A,
            document=changed_document,
            current_milestone="M2",
            authority=_binding(PLAN_A),
        )
        base = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        changed = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(changed_input,)))
        assert base.plans[0].projection_id() != changed.plans[0].projection_id()
        assert base.architecture.projection_id() != changed.architecture.projection_id()

    def test_cards_are_never_authority(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        assert CARD_IS_AUTHORITY is False
        assert CARD_DIGEST_IS_AUTHORITY is False
        assert CARD_REF_IS_AUTHORITY is False
        assert PROJECT_GOVERNANCE_STATE_DUPLICATED is False
        assert READER_CARD_ONTOLOGY_IMPORTED is False
        assert READER_IMPLEMENTATION_IMPORTED is False
        for card in bundle.cards():
            assert card.is_authority() is False
            assert card.to_dict()["is_authority"] is False

    def test_bounded_refs_and_no_host_paths_in_cards(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        payload = json.dumps(bundle.to_dict(), ensure_ascii=False)
        assert "/home/" not in payload
        assert "checkout" not in payload
        for card in bundle.cards():
            for ref in card.source_refs:
                assert not ref.ref.startswith("/")
            for ref in card.navigation_refs:
                assert not ref.ref.startswith("/")

    def test_projection_refresh_contract_is_bounded_vocabulary(self):
        assert projection_module.CONSUMER_REQUIRED_BEFORE_TRIGGER_WIRING is True
        request = ProjectionRefreshRequest(
            trigger=projection_module.REFRESH_TRIGGER_WORK_RECONCILIATION,
            source=_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)),
        )
        first = _engine().rebuild_refresh(request)
        second = _engine().rebuild(request.source)
        assert first.projection_digest() == second.projection_digest()
        with pytest.raises(GovernanceProjectionError) as excinfo:
            ProjectionRefreshRequest(trigger="unknown_trigger", source=request.source)
        assert excinfo.value.code == "UNKNOWN_REFRESH_TRIGGER"


# ---------------------------------------------------------------------------
# W1-AC2/W1-AC4 — non-authority, explicit completeness, no fabricated truth
# ---------------------------------------------------------------------------


class TestCompletenessAndFailClosed:
    def test_complete_card_with_missing_markers_is_rejected(self):
        with pytest.raises(GovernanceCardError) as excinfo:
            ProjectCard(project_id=PROJECT_ID, complete=True, missing_facts=("plan_records",))
        assert excinfo.value.code == "COMPLETENESS_CONTRADICTION"

    def test_incomplete_card_without_markers_is_rejected(self):
        with pytest.raises(GovernanceCardError) as excinfo:
            ProjectCard(project_id=PROJECT_ID, complete=False)
        assert excinfo.value.code == "COMPLETENESS_NOT_EXPLICIT"

    def test_missing_plan_document_is_explicit_not_fabricated(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=()))
        card = bundle.plans[0]
        assert card.complete is False
        assert card.missing_facts == ("plan_document",)
        assert card.plan_status is None
        assert card.current_milestone is None
        assert card.source_digest is None

    def test_missing_plan_record_is_explicit_not_fabricated(self):
        bundle = _engine().rebuild(_source(records=(), documents=(_plan_input(PLAN_A),)))
        card = bundle.plans[0]
        assert card.complete is False
        assert card.missing_facts == ("plan_lifecycle_state",)
        assert card.lifecycle_state is None
        assert card.record_revision is None

    def test_unread_truth_sources_produce_explicit_incomplete_progress(self):
        bundle = _engine().rebuild(
            GovernanceProjectionInput(
                project_id=PROJECT_ID,
                plan_records=None,
                coordinator_states=None,
                execution_records=None,
            )
        )
        card = bundle.progress
        assert card.complete is False
        assert set(card.missing_facts) == {
            "plan_lifecycle_records",
            "task_main_coordinator_state",
            "execution_state_store",
        }
        assert card.execution_aggregate is None
        assert card.human_brake_active is None
        project = bundle.project
        assert project.complete is False
        assert project.missing_facts == ("plan_records",)

    def test_unknown_milestone_fails_closed(self):
        source = _source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),))
        with pytest.raises(GovernanceProjectionError) as excinfo:
            _engine().milestone_card(source, PLAN_A, "M9")
        assert excinfo.value.code == "MILESTONE_NOT_FOUND"

    def test_missing_architecture_baseline_is_explicit(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A, baseline_id=None),)))
        card = bundle.architecture
        assert card.complete is False
        assert set(card.missing_facts) == {"architecture_baseline", "accepted_architecture_ref"}
        assert card.baseline_id is None

    def test_invalid_project_identity_is_rejected(self):
        with pytest.raises(GovernanceProjectionError) as excinfo:
            GovernanceProjectionInput(project_id="/home/latios/workspace/aota_forge")
        assert excinfo.value.code == "INVALID_PROJECT_ID"
        with pytest.raises(GovernanceCardError):
            ProjectCard(project_id="Not A Project", complete=True if False else False, missing_facts=("x",))


# ---------------------------------------------------------------------------
# W1-AC2/W1-AC4 — trusted identities + Plan semantic ownership
# ---------------------------------------------------------------------------


class TestTrustedSources:
    def test_project_card_uses_trusted_project_identity_and_plan_inventory(self):
        bundle = _engine().rebuild(
            _source(records=(_record(PLAN_A), _record(PLAN_B, lifecycle=PLAN_LIFECYCLE_RETIRED)), documents=())
        )
        card = bundle.project
        assert card.project_id == PROJECT_ID
        assert card.plan_count == 2
        assert card.plan_refs == (PLAN_A, PLAN_B)
        assert card.source_digest is not None
        assert card.complete is True

    def test_plan_card_reuses_authority_binding_and_portable_plan_document(self):
        binding = _binding(PLAN_A, revision="rev-7", digest="a" * 64)
        record = _record(PLAN_A, binding=binding)
        card = _engine().rebuild(
            _source(records=(record,), documents=(_plan_input(PLAN_A, binding=binding),))
        ).plans[0]
        assert card.authority_source_kind == "local_governance"
        assert card.authority_ref == binding.authority_ref
        assert card.authority_revision == "rev-7"
        assert card.authority_digest == "a" * 64
        assert card.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert card.record_revision == 1
        assert card.plan_status == "active"
        assert card.current_milestone == "M2"
        assert card.source_digest == _document(PLAN_A).source_digest
        roles = {ref.role for ref in card.source_refs}
        assert roles == {"project_governance_store", "plan_authority", "plan_document"}

    def test_plan_card_rejects_conflicting_dual_authority(self):
        conflicting = PlanDocumentInput(
            plan_id=PLAN_A,
            document=_document(PLAN_A),
            current_milestone="M2",
            authority=PlanAuthorityBinding(
                plan_id=PLAN_A,
                source_kind="github_issue",
                authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
            ),
        )
        with pytest.raises(GovernanceProjectionError) as excinfo:
            _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(conflicting,)))
        assert excinfo.value.code == "PLAN_AUTHORITY_CONFLICT"

    def test_milestone_card_preserves_plan_semantic_ownership(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        card = bundle.milestones[0]
        assert isinstance(card, MilestoneCard)
        assert card.milestone_id == "M2"
        assert card.milestone_status == "in_progress"
        assert card.is_current is True
        assert card.work_item_refs == ("W1", "W2", "W3", "W4")
        assert card.dependency_edges == (("W1", "W2"), ("W2", "W3"), ("W2", "W4"))
        assert card.objective_summary is not None
        payload = card.to_dict()
        assert "wi_status" not in payload
        assert "execution_phase" not in payload
        assert "human_brake" not in payload

    def test_architecture_card_reflects_accepted_baseline_only(self):
        bundle = _engine().rebuild(_source(records=(_record(PLAN_A),), documents=(_plan_input(PLAN_A),)))
        card = bundle.architecture
        assert card.baseline_id == BASELINE_ID
        assert card.baseline_status == "approved"
        assert card.accepted_ref == _binding(PLAN_A).authority_ref
        assert card.complete is True

    def test_architecture_state_from_plan_document_helper(self):
        state = architecture_state_from_plan_document(
            project_id=PROJECT_ID,
            plan_id=PLAN_A,
            document=_document(PLAN_A),
            authority_ref="local-governance:aota_forge/plan_alpha",
        )
        assert isinstance(state, ArchitectureStateInput)
        assert state.baseline_id == BASELINE_ID
        assert state.baseline_status == "approved"


# ---------------------------------------------------------------------------
# W1-AC6/W1-AC11 — progress aggregate + multi-Plan isolation
# ---------------------------------------------------------------------------


class TestProgressAndIsolation:
    def test_progress_card_aggregates_existing_truth_without_duplication(self, tmp_path: Path):
        coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinators.json")
        state = TaskMainCoordinatorState(
            coordinator_id="coord-a",
            plan_authority=_binding(PLAN_A).authority_ref,
            plan_digest="d" * 64,
            milestone_id="M2",
            entry_base="a" * 40,
            origin_task_main_session_ref="session-1",
            project_id=PROJECT_ID,
            executor_id="executor-1",
            work_items=("W1", "W2", "W3"),
            wi_status={"W1": "PENDING", "W2": "ACTIVE", "W3": "PENDING"},
            open_blockers=("blocker: waiting for user decision",),
            next_action="continue M2/W1",
            human_brake={"state": "USER_GATE_REQUIRED", "scope": "WHOLE_MILESTONE"},
        )
        created = coordinator_store.create(state)

        execution_store = FileBackedExecutionStateStore(tmp_path / "executions.json")
        pending = execution_store.create(_execution_record("task-a"))
        done = execution_store.create(_execution_record("task-b"))
        execution_store.compare_and_swap(
            "task-b",
            done.record_revision,
            {
                "execution_phase": ExecutionPhase.DISPATCHED,
                "adapter_handle": "handle-b",
                "initial_state": CanonicalTaskState.RUNNING,
                "dispatched_at": "2026-09-16T00:00:00+00:00",
                "canonical_task_state": CanonicalTaskState.RUNNING.value,
            },
        )
        done = execution_store.get("task-b")
        execution_store.compare_and_swap(
            "task-b",
            done.record_revision,
            {
                "terminal_result": CanonicalResult.success(
                    canonical_task_id="task-b",
                    executor_id="executor-1",
                    result_data={"files_changed": ["src/x.py"]},
                    stdout_summary="ok",
                    execution_stats={"duration_ms": 5},
                    correlation_id="corr-b",
                ).to_dict(),
                "canonical_task_state": CanonicalTaskState.COMPLETED.value,
                "delivery_state": "acknowledged",
            },
        )

        governance_store = open_project_governance_store(tmp_path / "governance.sqlite3")
        try:
            governance_store.put_plan(_record(PLAN_A))
            records = tuple(governance_store.list_plans(PROJECT_ID))
        finally:
            governance_store.close()

        before_coordinator = coordinator_store.get("coord-a").to_dict()
        before_execution = {
            task_id: execution_store.get(task_id).to_dict() for task_id in ("task-a", "task-b")
        }

        source = _source(
            records=records,
            documents=(_plan_input(PLAN_A),),
            coordinators=tuple(coordinator_store.list_all()),
            executions=tuple(execution_store.list_all()),
        )
        card = _engine().rebuild(source).progress

        assert card.complete is True
        assert card.plan_lifecycle == (
            PlanLifecycleFact(
                plan_id=PLAN_A,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority_source_kind="local_governance",
            ),
        )
        assert card.active_coordinator_count == 1
        fact = card.coordinator_facts[0]
        assert isinstance(fact, CoordinatorProgressFact)
        assert fact.coordinator_id == "coord-a"
        assert fact.status == "ACTIVE"
        assert fact.work_item_count == 3
        assert dict((status, count) for status, count in fact.wi_status_counts) == {
            "PENDING": 2,
            "ACTIVE": 1,
        }
        assert fact.human_brake_active is True
        assert card.human_brake_active is True
        assert card.open_blockers == ("blocker: waiting for user decision",)
        assert card.next_action == "continue M2/W1"
        aggregate = card.execution_aggregate
        assert aggregate.total == 2
        assert dict(aggregate.by_canonical_task_state)["COMPLETED"] == 1
        assert dict(aggregate.by_canonical_task_state)["CREATED"] == 1
        assert aggregate.terminal_ok == 1
        assert aggregate.terminal_failed == 0
        assert aggregate.requiring_recovery == 1
        assert PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_STATE is False
        assert PROJECT_GOVERNANCE_STORE_OWNS_COORDINATOR_STATE is False

        assert coordinator_store.get("coord-a").to_dict() == before_coordinator
        assert {
            task_id: execution_store.get(task_id).to_dict() for task_id in ("task-a", "task-b")
        } == before_execution
        assert created.coordinator_revision == coordinator_store.get("coord-a").coordinator_revision
        assert pending.record_revision == execution_store.get("task-a").record_revision

    def test_two_plans_in_one_project_remain_isolated(self):
        records = (_record(PLAN_A), _record(PLAN_B, lifecycle=PLAN_LIFECYCLE_RETIRED, revision=2))
        both = _engine().rebuild(
            _source(records=records, documents=(_plan_input(PLAN_A), _plan_input(PLAN_B)))
        )
        assert [card.plan_id for card in both.plans] == [PLAN_A, PLAN_B]
        card_a = next(card for card in both.plans if card.plan_id == PLAN_A)
        card_b = next(card for card in both.plans if card.plan_id == PLAN_B)
        assert card_a.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert card_b.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        assert card_a.projection_id() != card_b.projection_id()
        assert card_a.authority_ref == _binding(PLAN_A).authority_ref
        assert card_b.authority_ref == _binding(PLAN_B).authority_ref
        milestone_keys = {(card.plan_id, card.milestone_id) for card in both.milestones}
        assert milestone_keys == {(PLAN_A, "M2"), (PLAN_B, "M2")}

        plan_b_changed = _engine().rebuild(
            _source(
                records=records,
                documents=(_plan_input(PLAN_A), _plan_input(PLAN_B, baseline_id="AF-OTHER-BASELINE-v2")),
            )
        )
        changed_a = next(card for card in plan_b_changed.plans if card.plan_id == PLAN_A)
        changed_b = next(card for card in plan_b_changed.plans if card.plan_id == PLAN_B)
        assert changed_a.projection_id() == card_a.projection_id()
        assert changed_b.projection_id() != card_b.projection_id()


# ---------------------------------------------------------------------------
# W1-AC7/W1-AC8 — generated MAP/STATUS views
# ---------------------------------------------------------------------------


class TestGeneratedViews:
    def _bundle(self):
        return _engine().rebuild(
            _source(
                records=(_record(PLAN_A),),
                documents=(_plan_input(PLAN_A),),
                coordinators=(),
                executions=(),
            )
        )

    def test_map_and_status_are_deterministic_projections(self):
        bundle = self._bundle()
        map_text = render_map_markdown(bundle)
        status_text = render_status_markdown(bundle)
        assert map_text == render_map_markdown(bundle)
        assert status_text == render_status_markdown(bundle)
        assert map_text == generate_views(bundle).map_markdown
        assert status_text == generate_views(bundle).status_markdown
        assert MAP_GENERATED is True and STATUS_GENERATED is True
        assert MAP_AUTHORITY is False and STATUS_AUTHORITY is False
        assert MANUAL_MAP_PROGRESS_TRUTH is False and MANUAL_STATUS_PROGRESS_TRUTH is False

    def test_map_contains_bounded_navigation_to_projection_objects(self):
        text = render_map_markdown(self._bundle())
        assert f"# MAP — {PROJECT_ID}" in text
        assert f"plan: {PLAN_A}" in text
        assert "milestone: M2" in text
        assert BASELINE_ID in text
        assert text.count("projection_id: gcard-") >= 4
        assert "AUTHORITY=no" in text

    def test_status_contains_progress_facts_and_no_wall_clock(self):
        text = render_status_markdown(self._bundle())
        assert f"# STATUS — {PROJECT_ID}" in text
        assert "plan_lifecycle" in text
        assert "source_digest" in text
        assert "AUTHORITY=no" in text
        assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)

    def test_write_generated_views_materializes_disposable_files(self, tmp_path: Path):
        bundle = self._bundle()
        map_path, status_path = write_generated_views(tmp_path / "plans" / PROJECT_ID, bundle)
        assert map_path.name == "MAP.md" and status_path.name == "STATUS.md"
        assert map_path.read_text(encoding="utf-8") == render_map_markdown(bundle)
        assert status_path.read_text(encoding="utf-8") == render_status_markdown(bundle)

    def test_views_are_not_inputs_to_the_projection_engine(self):
        import dataclasses as _dc

        field_names = {field.name for field in _dc.fields(GovernanceProjectionInput)}
        assert not field_names & {"map_markdown", "status_markdown", "generated_views", "markdown"}
        engine_attrs = {name for name in dir(GovernanceProjectionEngine) if not name.startswith("_")}
        assert not {name for name in engine_attrs if "markdown" in name or "view" in name}


# ---------------------------------------------------------------------------
# W1-AC5/W1-AC9/W1-AC10/W1-AC12/W1-AC14 — architectural guards
# ---------------------------------------------------------------------------


def _module_ast(filename: str) -> ast.Module:
    return ast.parse((GOVERNANCE_DIR / filename).read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestArchitectureGuards:
    def test_no_generic_framework_watcher_event_bus_or_db_imports(self):
        for filename in W1_MODULES:
            tree = _module_ast(filename)
            imports = _imports(tree)
            for module in imports:
                assert not module.startswith(BANNED_IMPORT_PREFIXES), (filename, module)
                assert module not in BANNED_IMPORT_MODULES, (filename, module)
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            for symbol in BANNED_SYMBOLS:
                assert symbol not in source, (filename, symbol)
        assert SECOND_GENERIC_PROJECTION_FRAMEWORK is False
        assert projection_module.GENERIC_WATCHER_CREATED is False
        assert projection_module.EVENT_BUS_CREATED is False
        assert projection_module.PROJECTION_DATABASE_CREATED is False
        assert not (GOVERNANCE_DIR / "watcher.py").exists()
        assert not (GOVERNANCE_DIR / "event_bus.py").exists()

    def test_projection_and_card_modules_do_not_touch_storage_or_threads(self):
        for filename in ("cards.py", "projection.py"):
            tree = _module_ast(filename)
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            assert "connect(" not in source
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in {"open", "mkdtemp"}, (filename, node.func.id)

    def test_no_graph_projection_ownership_change(self):
        for filename in W1_MODULES:
            imports = _imports(_module_ast(filename))
            assert all(not module.startswith("aota_forge.core.projection") for module in imports)
        assert projection_module.OLD_GRAPH_PROJECTION_OWNERSHIP_CHANGED is False

        code = (
            "import sys; import aota_forge.governance.projection, aota_forge.governance.cards, "
            "aota_forge.governance.generated_views; "
            "assert 'aota_forge.core.projection.rebuild' not in sys.modules; "
            "from aota_forge.core.projection.rebuild import ProjectionRebuildService; print('OK')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        assert proc.stdout.strip().endswith("OK")

    def test_no_reader_implementation_dependency(self):
        for filename in W1_MODULES:
            tree = _module_ast(filename)
            source = (GOVERNANCE_DIR / filename).read_text(encoding="utf-8")
            for token in ("aota_reader", "chatgpt", "openchamber", "toolresultcard", "mcp_server"):
                assert token not in source.lower(), (filename, token)
            for module in _imports(tree):
                lowered = module.lower()
                assert "reader" not in lowered, (filename, module)
                assert "mcp" not in lowered, (filename, module)

    def test_existing_governance_store_contract_unchanged(self):
        from aota_forge.governance import project_store

        assert project_store.PROJECT_GOVERNANCE_SCHEMA_VERSION == 1
        assert project_store.PROJECT_GOVERNANCE_STORE_SCOPE == "project_and_cross_plan_governance_only"
        assert project_store.PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS is False
        assert project_store.PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION is False
        assert project_store.GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE is False
        assert project_store.GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE is False


# ---------------------------------------------------------------------------
# V2 — bounded composition over real accepted M1 stores/interfaces
# ---------------------------------------------------------------------------


def _local_plan_destination(tmp_path: Path, plan_id: str) -> LocalPlanAuthorityDestination:
    base = tmp_path / "plans"
    (base / PROJECT_ID).mkdir(parents=True, exist_ok=True)
    binding = LocalGovernanceRootBinding.from_trusted_base(base, project_id=PROJECT_ID)
    return LocalPlanAuthorityDestination(
        governance_root=binding,
        plan_id=plan_id,
        expected_ref=object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)),
    )


def _local_plan_binding(destination: LocalPlanAuthorityDestination) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=destination.plan_id,
        source_kind="local_governance",
        authority_ref=destination.authority_ref,
    )


def _execution_record(task_id: str) -> DurableExecutionRecord:
    return DurableExecutionRecord(
        canonical_task_id=task_id,
        executor_id="af57-m2w1-test",
        package_id=f"{task_id}:pkg",
        correlation_id=f"corr-{task_id}",
        dispatch_attempt_id=f"attempt-{task_id}",
        idempotency_key=f"idem-{task_id}",
        intent_fingerprint="f" * 32,
    )


class TestV2RealStoresDisposableViews:
    def test_v2_projection_over_real_stores_and_disposable_map_status(self, tmp_path: Path):
        destination_a = _local_plan_destination(tmp_path, PLAN_A)
        destination_a.plan_directory().mkdir(parents=True)
        destination_a.plan_document_path().write_text(_plan_body(PLAN_A), encoding="utf-8")
        adapter_a = LocalPlanAuthorityReadAdapter(destination_a, binding=_local_plan_binding(destination_a))
        document_a = load_local_portable_plan(adapter_a)
        assert document_a.source_kind == "portable_plan_local"
        assert document_a.source_digest

        destination_b = _local_plan_destination(tmp_path, PLAN_B)
        destination_b.plan_directory().mkdir(parents=True)
        destination_b.plan_document_path().write_text(
            _plan_body(PLAN_B, baseline_id=None), encoding="utf-8"
        )
        adapter_b = LocalPlanAuthorityReadAdapter(destination_b, binding=_local_plan_binding(destination_b))
        document_b = load_local_portable_plan(adapter_b)

        governance_store = open_project_governance_store(tmp_path / "governance.sqlite3")
        try:
            governance_store.put_plan(
                ProjectPlanRecord(
                    project_id=PROJECT_ID,
                    plan_id=PLAN_A,
                    lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                    authority=_local_plan_binding(destination_a),
                )
            )
            records = tuple(governance_store.list_plans(PROJECT_ID))
        finally:
            governance_store.close()

        coordinator_store = FileBackedTaskMainCoordinatorStore(tmp_path / "coordinators.json")
        coordinator_store.create(
            TaskMainCoordinatorState(
                coordinator_id="coord-v2",
                plan_authority=_local_plan_binding(destination_a).authority_ref,
                plan_digest=document_a.source_digest,
                milestone_id="M2",
                entry_base="a" * 40,
                origin_task_main_session_ref="session-v2",
                project_id=PROJECT_ID,
                executor_id="executor-v2",
                work_items=("W1", "W2"),
                wi_status={"W1": "ACTIVE", "W2": "PENDING"},
                next_action="start M2/W1",
            )
        )
        execution_store = FileBackedExecutionStateStore(tmp_path / "executions.json")
        execution_store.create(_execution_record("task-v2-1"))
        execution_store.create(_execution_record("task-v2-2"))

        source = GovernanceProjectionInput(
            project_id=PROJECT_ID,
            project_ref=f"project:{PROJECT_ID}",
            project_status="active",
            governance_root_refs=("local-governance",),
            plan_records=records,
            plan_documents=(
                plan_document_input(
                    plan_id=PLAN_A,
                    document=document_a,
                    current_milestone="M2",
                    authority=_local_plan_binding(destination_a),
                ),
                plan_document_input(
                    plan_id=PLAN_B,
                    document=document_b,
                    current_milestone="M2",
                    authority=_local_plan_binding(destination_b),
                ),
            ),
            coordinator_states=tuple(coordinator_store.list_all()),
            execution_records=tuple(execution_store.list_all()),
        )

        bundle = _engine().rebuild(source)
        assert bundle.project.complete is True
        assert f"local-governance:{PROJECT_ID}/{PLAN_A}" in json.dumps(bundle.to_dict(), ensure_ascii=False)
        assert bundle.progress.complete is True
        assert bundle.progress.execution_aggregate.total == 2
        assert bundle.architecture.baseline_id == BASELINE_ID
        assert len(bundle.plans) == 2
        assert len(bundle.milestones) == 2

        output_dir = tmp_path / "generated"
        map_path, status_path = write_generated_views(output_dir, bundle)
        first_map = map_path.read_text(encoding="utf-8")
        first_status = status_path.read_text(encoding="utf-8")
        assert f"plan: {PLAN_A}" in first_map
        assert f"plan: {PLAN_B}" in first_map
        assert "## Progress" in first_status
        assert all(
            forbidden not in first_map + first_status
            for forbidden in (str(tmp_path), "/home/", "MAP_AUTHORITY=yes")
        )

        write_generated_views(output_dir, bundle)
        assert map_path.read_text(encoding="utf-8") == first_map
        assert status_path.read_text(encoding="utf-8") == first_status
        assert _sha(first_map) == _sha(render_map_markdown(bundle))
