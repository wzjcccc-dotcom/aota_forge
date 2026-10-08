"""W4 Doctor V0 and Agent-facing Plan candidate preflight proofs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    local_plan_authority_reference,
)
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    FileBackedExecutionStateStore,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.governance.doctor import (
    DOCTOR_DOMAINS,
    TRANSITIONS,
    DoctorContext,
    run_governance_doctor,
)
from aota_forge.governance.generated_views import render_map_markdown, render_status_markdown
from aota_forge.governance.intake import (
    GovernanceIntakeContext,
    GovernanceIntakeService,
)
from aota_forge.governance.projection import (
    GovernanceProjectionEngine,
    GovernanceProjectionInput,
    plan_document_input,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.governance.terminal_projection import create_terminal_projection_reconciler
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator import default_coordinator_id
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

PROJECT_ID = "doctor-project"
PLAN_ID = "plan_doctor_v0"
MILESTONE_ID = "M1"
CLEAN_SOURCE = {"available": True, "complete": True, "is_dirty": False}

VALID_BODY = f"""# Doctor V0 fixture

## Plan Identity
PROJECT_ID={PROJECT_ID}
PLAN_ID={PLAN_ID}
PLAN_REVISION=R1

## Current State
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress

## M1 — Doctor readiness
Exercise current trusted Governance and runtime truth.

#### M1/W1 — Read-only check
Verify deterministic lifecycle readiness.
"""

COMPLETED_BODY = VALID_BODY.replace("PLAN_STATUS=active", "PLAN_STATUS=completed").replace(
    "M1_STATUS=in_progress", "M1_STATUS=completed"
)
WRONG_CURRENT_SECTION_BODY = VALID_BODY.replace(
    "## Current State", "## Historical State"
)
NO_CURRENT_MILESTONE_BODY = VALID_BODY.replace("CURRENT_MILESTONE=M1\n", "")
NO_MILESTONE_STATUS_BODY = VALID_BODY.replace("M1_STATUS=in_progress\n", "")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest(project_id: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "project": {"id": project_id, "name": "Doctor fixture", "kind": "test", "status": "active"},
            "summary": "Doctor temporary Project",
            "capabilities": [],
            "paths": {
                "source_root": ".",
                "source": [],
                "docs": [],
                "scripts": [],
                "profiles": [],
                "skills": [],
                "tests": [],
            },
            "commands": {"validate": [], "deploy": [], "verify_deploy": []},
            "runtime": {"deployment_type": "manual", "requires_human_checkpoint": True},
            "codegraph": {"enabled": False, "index_location": ".codegraph"},
            "plan": {"active_plan_id": PLAN_ID},
            "constraints": [],
        }
    )


@dataclass
class DoctorWorld:
    project_root: Path
    governance_base: Path
    governance_root: LocalGovernanceRootBinding
    plan_path: Path
    map_path: Path
    status_path: Path
    store_path: Path
    journal_path: Path
    coordinator_path: Path
    execution_path: Path
    closure_receipts_path: Path
    body: str
    binding: PlanAuthorityBinding
    governance_store: SQLiteProjectGovernanceStore
    coordinator_store: FileBackedTaskMainCoordinatorStore
    execution_store: FileBackedExecutionStateStore

    def context(self, **overrides: Any) -> DoctorContext:
        values = {
            "project_id": PROJECT_ID,
            "plan_id": PLAN_ID,
            "plan_authority_binding": self.binding,
            "governance_root": self.governance_root,
            "governance_store": self.governance_store,
            "project_root": self.project_root,
            "coordinator_store": self.coordinator_store,
            "execution_store": self.execution_store,
            "expected_coordinator_id": default_coordinator_id(
                project_id=PROJECT_ID,
                plan_id=PLAN_ID,
                milestone_id=MILESTONE_ID,
            ),
            "runtime_plan_authority_binding": self.binding,
            "session_ref": "doctor-session",
        }
        values.update(overrides)
        return DoctorContext(**values)

    def refresh_projection(self) -> None:
        records = tuple(self.governance_store.list_plans(PROJECT_ID))
        plan_documents = []
        for record in records:
            destination = LocalPlanAuthorityDestination(
                self.governance_root,
                record.plan_id,
                object_ref_subject(make_id(IdKind.SUBJECT, record.plan_id, sub_kind=SubjectKind.PLAN)),
            )
            snapshot = LocalPlanAuthorityReadAdapter(destination, binding=record.authority).load()
            document = normalize_portable_plan(
                snapshot.body,
                source_revision=snapshot.revision,
                source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
            )
            plan_documents.append(
                plan_document_input(
                    plan_id=record.plan_id,
                    document=document,
                    title=getattr(document, "title", None),
                    current_milestone=document.current_milestone,
                    authority=record.authority,
                )
            )
        source = GovernanceProjectionInput(
            project_id=PROJECT_ID,
            project_ref=f"project:{PROJECT_ID}",
            project_status="active",
            governance_root_refs=("local-governance",),
            plan_records=records,
            plan_documents=tuple(plan_documents),
            coordinator_states=tuple(self.coordinator_store.list_all()),
            execution_records=tuple(self.execution_store.list_all()),
        )
        bundle = GovernanceProjectionEngine().rebuild(source)
        self.map_path.write_text(render_map_markdown(bundle), encoding="utf-8")
        self.status_path.write_text(render_status_markdown(bundle), encoding="utf-8")

    def snapshot(self) -> dict[str, bytes | None]:
        paths = (
            self.plan_path,
            self.store_path,
            self.journal_path,
            self.coordinator_path,
            self.execution_path,
            self.map_path,
            self.status_path,
            self.closure_receipts_path,
        )
        return {str(path): path.read_bytes() if path.exists() else None for path in paths}

    def close(self) -> None:
        self.governance_store.close()
        self.coordinator_store.close()
        self.execution_store.close()


def _world(
    tmp_path: Path,
    *,
    body: str = VALID_BODY,
    lifecycle_state: str = PLAN_LIFECYCLE_ACTIVE,
) -> DoctorWorld:
    project_root = tmp_path / "project"
    state_dir = project_root / ".aota"
    state_dir.mkdir(parents=True)
    (state_dir / "project.yaml").write_text(_manifest(PROJECT_ID), encoding="utf-8")
    governance_base = tmp_path / "plans"
    governance_base.mkdir(parents=True)
    (governance_base / PROJECT_ID).mkdir()
    governance_root = LocalGovernanceRootBinding.from_trusted_base(
        governance_base, project_id=PROJECT_ID
    )
    plan_path = Path(governance_root.root_path) / PLAN_ID / "plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(body, encoding="utf-8")
    digest = _digest(body)
    binding = PlanAuthorityBinding(
        plan_id=PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN_ID),
        source_revision=digest,
        source_digest=digest,
    )
    store_path = state_dir / "governance.sqlite3"
    governance_store = SQLiteProjectGovernanceStore(store_path)
    governance_store.put_plan(
        ProjectPlanRecord(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            lifecycle_state=lifecycle_state,
            authority=binding,
        )
    )
    journal_path = state_dir / "governance-journal.json"
    journal_path.write_text("[]", encoding="utf-8")
    closure_receipts_path = state_dir / "closure-receipts.json"
    closure_receipts_path.write_text("[]", encoding="utf-8")
    coordinator_path = state_dir / "coordinator.json"
    coordinator_store = FileBackedTaskMainCoordinatorStore(coordinator_path)
    execution_path = state_dir / "execution.json"
    execution_store = FileBackedExecutionStateStore(execution_path)
    world = DoctorWorld(
        project_root=project_root,
        governance_base=governance_base,
        governance_root=governance_root,
        plan_path=plan_path,
        map_path=Path(governance_root.root_path) / "MAP.md",
        status_path=Path(governance_root.root_path) / "STATUS.md",
        store_path=store_path,
        journal_path=journal_path,
        coordinator_path=coordinator_path,
        execution_path=execution_path,
        closure_receipts_path=closure_receipts_path,
        body=body,
        binding=binding,
        governance_store=governance_store,
        coordinator_store=coordinator_store,
        execution_store=execution_store,
    )
    world.refresh_projection()
    return world


@pytest.fixture
def doctor_world(tmp_path: Path):
    world = _world(tmp_path)
    try:
        yield world
    finally:
        world.close()


def _coordinator_state(
    world: DoctorWorld,
    *,
    status: CoordinatorStatus = CoordinatorStatus.ACTIVE,
    coordinator_id: str | None = None,
    plan_authority: str | None = None,
    digest: str | None = None,
    revision: str | None = None,
    bindings: dict[str, dict[str, Any]] | None = None,
) -> TaskMainCoordinatorState:
    return TaskMainCoordinatorState(
        coordinator_id=coordinator_id
        or default_coordinator_id(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            milestone_id=MILESTONE_ID,
        ),
        plan_authority=plan_authority or world.binding.authority_ref,
        plan_digest=digest or world.binding.source_digest,
        plan_source_revision=revision or str(world.binding.source_revision),
        milestone_id=MILESTONE_ID,
        entry_base="source-frontier",
        origin_task_main_session_ref="doctor-session",
        project_id=PROJECT_ID,
        executor_id="doctor-test-executor",
        work_items=("W1",),
        status=status,
        wi_status={"W1": WorkItemCoordinatorStatus.PENDING},
        bindings=bindings or {},
    )


def _finding_codes(result: Any) -> set[str]:
    return {finding.code for finding in result.findings}


def test_D1_healthy_current_plan_returns_deterministic_domains(doctor_world: DoctorWorld) -> None:
    first = run_governance_doctor(doctor_world.context(), source_observation=CLEAN_SOURCE)
    second = run_governance_doctor(doctor_world.context(), source_observation=CLEAN_SOURCE)

    assert first.to_dict() == second.to_dict()
    assert len(json.dumps(first.to_dict(), sort_keys=True).encode("utf-8")) <= 3000
    assert tuple(first.domains) == DOCTOR_DOMAINS
    assert tuple(first.transition_readiness) == TRANSITIONS
    assert all(item["status"] == "ADVISORY" for item in first.transition_readiness.values())
    assert first.overall_status.value == "READY"
    assert first.domains["AUTHORITY"] == "READY"
    assert first.domains["PLAN_LIFECYCLE"] == "READY"
    assert first.domains["PROJECTION"] == "READY"
    assert "RUNTIME_ACTIVE_ELIGIBILITY_NOT_YET_PROVEN" in _finding_codes(first)

    dirty_source = run_governance_doctor(
        doctor_world.context(),
        source_observation={"available": True, "complete": True, "is_dirty": True},
    )
    assert dirty_source.domains["SOURCE_FRONTIER"] == "DEGRADED"
    assert dirty_source.overall_status.value == "DEGRADED"
    assert "SOURCE_RUNTIME_CLASSIFICATION_PENDING" in _finding_codes(dirty_source)


def test_D2_retired_plan_active_coordinator_is_reported_without_deletion(tmp_path: Path) -> None:
    world = _world(tmp_path, body=COMPLETED_BODY, lifecycle_state=PLAN_LIFECYCLE_RETIRED)
    try:
        state = _coordinator_state(world)
        world.coordinator_store.create(state)
        world.refresh_projection()
        before = world.coordinator_store.list_all()

        result = run_governance_doctor(world.context(), source_observation=CLEAN_SOURCE)

        assert "RETIRED_PLAN_ACTIVE_COORDINATOR" in _finding_codes(result)
        assert world.coordinator_store.list_all() == before
    finally:
        world.close()


def test_D3_store_binding_and_local_body_drift_is_reported(doctor_world: DoctorWorld) -> None:
    doctor_world.plan_path.write_text(
        VALID_BODY.replace("Verify deterministic lifecycle readiness.", "Body changed without a binding update."),
        encoding="utf-8",
    )

    result = run_governance_doctor(doctor_world.context(), source_observation=CLEAN_SOURCE)

    assert "PLAN_AUTHORITY_DRIFT" in _finding_codes(result)


@pytest.mark.parametrize(
    ("body", "lifecycle"),
    (
        (COMPLETED_BODY, PLAN_LIFECYCLE_ACTIVE),
        (VALID_BODY, PLAN_LIFECYCLE_RETIRED),
    ),
)
def test_doctor_reports_store_body_lifecycle_drift(
    tmp_path: Path,
    body: str,
    lifecycle: str,
) -> None:
    world = _world(tmp_path, body=body, lifecycle_state=lifecycle)
    try:
        result = run_governance_doctor(world.context(), source_observation=CLEAN_SOURCE)
        assert "PLAN_LIFECYCLE_INCONSISTENT" in _finding_codes(result)
    finally:
        world.close()


def test_D4_stale_projection_is_reported_then_existing_reconcile_repairs_it(tmp_path: Path) -> None:
    world = _world(tmp_path, body=COMPLETED_BODY, lifecycle_state=PLAN_LIFECYCLE_RETIRED)
    try:
        world.map_path.write_text("# intentionally stale\n", encoding="utf-8")
        before = world.snapshot()

        result = run_governance_doctor(world.context(), source_observation=CLEAN_SOURCE)

        assert "PROJECTION_STALE" in _finding_codes(result)
        assert world.snapshot() == before

        reconcile = create_terminal_projection_reconciler(
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            project_root=world.project_root,
            governance_root=world.governance_root,
            governance_store=world.governance_store,
            coordinator_store=world.coordinator_store,
            execution_store=world.execution_store,
        )
        repaired = reconcile()
        assert repaired.status == "VERIFIED"
        assert "PROJECTION_STALE" not in _finding_codes(
            run_governance_doctor(world.context(), source_observation=CLEAN_SOURCE)
        )
    finally:
        world.close()


def test_D5_runtime_binding_mismatch_is_reported(doctor_world: DoctorWorld) -> None:
    runtime_binding = replace(doctor_world.binding, source_digest="e" * 64)

    result = run_governance_doctor(
        doctor_world.context(runtime_plan_authority_binding=runtime_binding),
        source_observation=CLEAN_SOURCE,
    )

    assert "RUNTIME_BINDING_MISMATCH" in _finding_codes(result)


def test_D6_unowned_nonterminal_execution_is_a_typed_conflict(doctor_world: DoctorWorld) -> None:
    doctor_world.execution_store.create(
        DurableExecutionRecord(
            canonical_task_id=f"{PROJECT_ID}:orphan-task",
            executor_id="doctor-test-executor",
            package_id="orphan-package",
            correlation_id="orphan-correlation",
            dispatch_attempt_id="orphan-attempt",
            idempotency_key="orphan-idempotency",
            intent_fingerprint="a" * 64,
        )
    )
    doctor_world.refresh_projection()

    result = run_governance_doctor(doctor_world.context(), source_observation=CLEAN_SOURCE)

    assert "NONTERMINAL_EXECUTION_CONFLICT" in _finding_codes(result)


def test_D7_doctor_is_read_only_including_git_and_all_durable_fixture_owners(
    doctor_world: DoctorWorld,
) -> None:
    repo = Path(__file__).resolve().parents[1]

    def git_snapshot() -> tuple[bytes, bytes, bytes]:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "-z"], cwd=repo
        )
        index_path = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"],
                cwd=repo,
            )
            .decode()
            .strip()
        )
        return head, status, index_path.read_bytes()

    before = doctor_world.snapshot()
    git_before = git_snapshot()
    result = run_governance_doctor(doctor_world.context(), source_observation=CLEAN_SOURCE)
    git_after = git_snapshot()

    assert result.to_dict()
    assert doctor_world.snapshot() == before
    assert git_after == git_before
    assert len(doctor_world.governance_store.list_plans(PROJECT_ID)) == 1


def test_D8_reopened_durable_owners_reconstruct_equivalent_result(doctor_world: DoctorWorld) -> None:
    before = run_governance_doctor(
        doctor_world.context(), source_observation=CLEAN_SOURCE
    ).to_dict()
    doctor_world.close()
    doctor_world.governance_store = SQLiteProjectGovernanceStore(doctor_world.store_path)
    doctor_world.coordinator_store = FileBackedTaskMainCoordinatorStore(
        doctor_world.coordinator_path
    )
    doctor_world.execution_store = FileBackedExecutionStateStore(doctor_world.execution_path)

    after = run_governance_doctor(
        doctor_world.context(), source_observation=CLEAN_SOURCE
    ).to_dict()

    assert after == before


def _intake_service(world: DoctorWorld) -> GovernanceIntakeService:
    return GovernanceIntakeService(
        GovernanceIntakeContext(
            workspace_id="doctor-workspace",
            governance_base=world.governance_base,
            project_root=world.project_root,
        )
    )


def test_D9_valid_init_preflight_is_semantic_only(doctor_world: DoctorWorld) -> None:
    before = doctor_world.snapshot()
    result = _intake_service(doctor_world).preflight_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=VALID_BODY,
    )

    assert result.semantic_readiness == "READY"
    assert result.mutation_authorization == "NOT_EVALUATED"
    assert result.durable_materialization == "NOT_ATTEMPTED"
    assert doctor_world.snapshot() == before


@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (WRONG_CURRENT_SECTION_BODY, "PLAN_STATUS_MISSING"),
        (NO_CURRENT_MILESTONE_BODY, "CURRENT_MILESTONE_MISSING"),
        (NO_MILESTONE_STATUS_BODY, "CURRENT_MILESTONE_STATUS_MISSING"),
        (COMPLETED_BODY, "PLAN_STATUS_NOT_INITIALIZABLE"),
        (VALID_BODY.replace(f"PLAN_ID={PLAN_ID}", "PLAN_ID=plan_other"), "CANDIDATE_PLAN_ID_MISMATCH"),
        ("", "PLAN_BODY_INVALID"),
    ),
)
def test_D10_invalid_init_preflight_reuses_w2_findings(
    doctor_world: DoctorWorld,
    candidate: str,
    expected: str,
) -> None:
    before = doctor_world.snapshot()
    result = _intake_service(doctor_world).preflight_plan_candidate(
        candidate_kind="init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=candidate,
    )

    assert result.semantic_readiness == "BLOCKED"
    assert expected in result.finding_codes
    assert result.mutation_authorization == "NOT_EVALUATED"
    assert result.durable_materialization == "NOT_ATTEMPTED"
    if candidate == WRONG_CURRENT_SECTION_BODY:
        assert result.normalized_current_state_usable is False
    assert doctor_world.snapshot() == before


def test_D11_valid_amend_preflight_does_not_claim_authorization(
    doctor_world: DoctorWorld,
) -> None:
    result = _intake_service(doctor_world).preflight_plan_candidate(
        candidate_kind="amend",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=VALID_BODY.replace("R1", "R2"),
    )

    assert result.semantic_readiness == "READY"
    assert result.mutation_authorization == "NOT_EVALUATED"
    assert result.durable_materialization == "NOT_ATTEMPTED"


def _trusted_task_main_binding(
    world: DoctorWorld,
    *,
    plan_bound: bool,
) -> Any:
    from aota_forge.core.project.resolver import (
        ProjectCandidateEvidence,
        ProjectResolutionEvidence,
    )
    from aota_forge.mcp_transport import TrustedWorkerBinding
    from aota_forge.work_plane.af_roles import get_tool_surface_for_role
    from aota_forge.work_plane.handoff import TaskHandoff
    from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

    candidate = ProjectCandidateEvidence(
        workspace_id="doctor-workspace",
        workspace_root=str(world.project_root.parent),
        project_id=PROJECT_ID,
        project_root=str(world.project_root),
        manifest_path=str(world.project_root / ".aota" / "project.yaml"),
        name="Doctor fixture",
        kind="test",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="doctor-workspace",
        workspace_root=str(world.project_root.parent),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    sandbox = bind_worktree_sandbox(evidence, "doctor-worktree", world.project_root)
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="doctor-test",
        objective="Inspect trusted Governance readiness",
        bounded_scope="one temporary Project and Plan",
        validation_expectations=("deterministic Doctor result",),
        semantic_stop_expectations=("fail closed without trusted context",),
    )
    intake_context = GovernanceIntakeContext(
        workspace_id="doctor-workspace",
        governance_base=world.governance_base,
        project_root=world.project_root,
        governance_store_path=world.store_path,
        journal_path=world.journal_path,
    )
    trusted_context = bind_trusted_context(
        principal_id="doctor-task-main",
        principal_type="task-main",
        channel="mcp",
        governance_intake_context=intake_context,
    )
    doctor = None
    plan_binding = world.binding if plan_bound else None
    git_authorities = ()
    if (world.project_root / ".git").exists():
        from aota_forge.work_plane.git_tools import (
            GIT_STATUS_DESCRIPTOR,
            create_git_authority,
        )

        git_authorities = (
            create_git_authority(sandbox, handoff, (), GIT_STATUS_DESCRIPTOR),
        )
    if plan_bound:
        doctor_context = world.context()

        def doctor(*, source_observation: Any) -> dict[str, Any]:
            return run_governance_doctor(
                doctor_context,
                source_observation=source_observation,
            ).to_dict()

    return TrustedWorkerBinding(
        canonical_task_id="doctor-task-main",
        project_id=PROJECT_ID,
        worktree_id="doctor-worktree",
        trusted_context=trusted_context,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=get_tool_surface_for_role("task-main"),
        read_authorities=(),
        git_authorities=git_authorities,
        plan_authority_binding=plan_binding,
        governance_doctor=doctor,
    )


def _mcp_call(server: Any, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def call() -> dict[str, Any]:
        tool = next(item for item in server._tool_manager.list_tools() if item.name == "aota.invoke")
        response = await tool.fn(operation=operation, arguments=arguments)
        if hasattr(response, "structuredContent") and response.structuredContent is not None:
            return response.structuredContent
        return response

    return asyncio.run(call())


def test_D12_canonical_aota_invoke_exposure_and_fail_closed_scope(
    doctor_world: DoctorWorld,
) -> None:
    from aota_forge.mcp_transport import (
        UnboundHostContext,
        _UnboundAotaAdapter,
        create_shared_mcp_server,
    )
    from aota_forge.work_plane.af_roles import get_tool_surface_for_role
    from aota_forge.work_plane.task_main_exposure import canonical_task_main_operations

    root = doctor_world.project_root
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Doctor Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "doctor@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    subprocess.run(["git", "status", "--porcelain=v1", "-z"], cwd=root, check=True)

    git_index = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"],
            cwd=root,
        )
        .decode()
        .strip()
    )
    git_before = (
        git_index.read_bytes(),
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root),
        subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=root),
    )
    doctor_op = "governance.doctor"
    preflight_op = "governance.plan.preflight"
    task_main_operations = set(canonical_task_main_operations())
    assert {doctor_op, preflight_op} <= task_main_operations
    for role in ("analyst", "coder", "reviewer", "project-steward"):
        names = set(get_tool_surface_for_role(role).all_capability_names())
        assert doctor_op not in names
        assert preflight_op not in names

    plan_bound = _trusted_task_main_binding(doctor_world, plan_bound=True)
    doctor_before = doctor_world.snapshot()
    doctor_result = _mcp_call(create_shared_mcp_server(plan_bound), doctor_op, {})
    assert doctor_result["ok"] is True
    assert set(doctor_result["payload"]["domains"]) == set(DOCTOR_DOMAINS)
    assert doctor_result["payload"]["domains"]["SOURCE_FRONTIER"] == "READY"
    assert doctor_world.snapshot() == doctor_before
    assert (
        git_index.read_bytes(),
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root),
        subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=root),
    ) == git_before

    project_bound = _trusted_task_main_binding(doctor_world, plan_bound=False)
    preflight_before = doctor_world.snapshot()
    preflight_result = _mcp_call(
        create_shared_mcp_server(project_bound),
        preflight_op,
        {
            "candidate_kind": "init",
            "project_id": PROJECT_ID,
            "plan_id": PLAN_ID,
            "candidate_body": VALID_BODY,
        },
    )
    assert preflight_result["ok"] is True
    assert preflight_result["payload"]["semantic_readiness"] == "READY"
    assert preflight_result["payload"]["mutation_authorization"] == "NOT_EVALUATED"
    assert preflight_result["payload"]["durable_materialization"] == "NOT_ATTEMPTED"
    assert doctor_world.snapshot() == preflight_before

    mismatch = _mcp_call(
        create_shared_mcp_server(project_bound),
        preflight_op,
        {
            "candidate_kind": "init",
            "project_id": "foreign-project",
            "plan_id": PLAN_ID,
            "candidate_body": VALID_BODY,
        },
    )
    assert mismatch["ok"] is False
    assert mismatch["error"]["code"] == "AUTHORITY_DENIED"

    plan_mismatch = _mcp_call(
        create_shared_mcp_server(plan_bound),
        preflight_op,
        {
            "candidate_kind": "amend",
            "project_id": PROJECT_ID,
            "plan_id": "plan_other",
            "candidate_body": VALID_BODY,
        },
    )
    assert plan_mismatch["ok"] is False
    assert plan_mismatch["error"]["code"] == "AUTHORITY_DENIED"
    assert doctor_world.snapshot() == preflight_before

    unbound = _UnboundAotaAdapter(UnboundHostContext(repo_root=str(doctor_world.project_root)))
    listing = unbound.invoke("operations.list", {})
    assert listing["ok"] is True
    discovered = {
        entry["operation"]
        for entry in listing["payload"]["data"]["operations"]
    }
    assert {doctor_op, preflight_op} <= discovered
    for operation in (doctor_op, preflight_op):
        denied = unbound.invoke(operation, {})
        assert denied["ok"] is False
        assert denied["error"]["code"] == "AUTHORITY_DENIED"
