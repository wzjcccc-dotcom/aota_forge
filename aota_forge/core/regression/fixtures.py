"""M3-B10 Isolated Deterministic Fixtures for Behavioral Proofs (Issue #9, lane M3-B10).

Provides fully isolated, deterministic, in-memory fixture infrastructure:
- TransactionStore and GraphRepository instantiation
- TrustedContext generation (trusted runtime, operator, user, unprivileged)
- CapabilityLease generation (valid, expired, revoked, mismatched)
- Subject, Execution, Completion, Decision, and FollowupEdge graph seeding
- Temporary isolated workspaces and project trees for boundary tests
- Strict isolation assertions:
    FIXTURES_USE_PRODUCTION_AUTHORITY_STATE = False
    FIXTURES_REQUIRE_DEPLOYMENT = False
    FIXTURES_REQUIRE_SHADOW_GRAPH = False
    FIXTURES_REQUIRE_LIVE_GITHUB = False
    FIXTURES_REQUIRE_HERMES_RUNTIME = False
    FIXTURES_REQUIRE_NETWORK = False
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from aota_forge.core.authority import AuthorityEngine, MaterializedDecisionEvidence
from aota_forge.core.binding.binder import SubjectBindingResolver
from aota_forge.core.capability_lease import (
    CapabilityLease,
    LEASE_CONSUMED,
    LEASE_ISSUED,
    LEASE_REVOKED,
)
from aota_forge.core.context import Principal, TrustedContext, bind_trusted_context
from aota_forge.core.graph import records
from aota_forge.core.graph.repository import GraphRepository, OwningSubjectResolver
from aota_forge.core.identity.broker import IdBroker
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.identity.subject import (
    mint_subject_value,
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.projection.rebuild import ProjectionRebuildService
from aota_forge.core.transaction import TransactionStore


# Module-level isolation flags
FIXTURES_USE_PRODUCTION_AUTHORITY_STATE = False
FIXTURES_REQUIRE_DEPLOYMENT = False
FIXTURES_REQUIRE_SHADOW_GRAPH = False
FIXTURES_REQUIRE_LIVE_GITHUB = False
FIXTURES_REQUIRE_HERMES_RUNTIME = False
FIXTURES_REQUIRE_NETWORK = False


def fixture_time(offset_seconds: int = 0) -> datetime:
    """Deterministic reference timestamp for test execution."""
    base = datetime(2026, 8, 18, 0, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def make_test_principal(
    principal_id: str = "reg-operator",
    principal_type: str = "operator_debug",
    provenance: str = "runtime_attestation",
    channel: str = "regression_fixture",
    freshness: str | None = "fixture-epoch-1",
) -> Principal:
    """Create a typed Principal for testing."""
    return Principal(
        principal_id=principal_id,
        principal_type=principal_type,
        provenance=provenance,
        channel=channel,
        freshness=freshness,
    )


def make_test_context(
    principal_id: str = "reg-operator",
    principal_type: str = "operator_debug",
    provenance: str = "runtime_attestation",
    channel: str = "regression_fixture",
    freshness: str | None = "fixture-epoch-1",
    metadata: Mapping[str, str] | None = None,
) -> TrustedContext:
    """Create a typed TrustedContext for testing."""
    principal = make_test_principal(
        principal_id=principal_id,
        principal_type=principal_type,
        provenance=provenance,
        channel=channel,
        freshness=freshness,
    )
    return bind_trusted_context(
        principal=principal,
        provenance=provenance,
        channel=channel,
        freshness=freshness,
        metadata=metadata,
    )


def make_test_store() -> TransactionStore:
    """Create an isolated, in-memory TransactionStore."""
    return TransactionStore(broker=IdBroker())


def seed_test_workflow(
    store: TransactionStore,
    wf_id: InternalId | None = None,
) -> InternalId:
    """Seed a workflow record in the store."""
    if wf_id is None:
        wf_id = make_id(IdKind.WORKFLOW, f"wf_{os.urandom(6).hex()}")
    wf_record = records.workflow(
        workflow_id=wf_id,
        semantic_intent="Deterministic regression testing workflow",
        creation_context={"lane": "M3-B10"},
        goal="Behavioral Proof",
    )
    store._put_staged(wf_record)
    return wf_id


def seed_test_subject(
    store: TransactionStore,
    subject_id: InternalId,
    kind: str = "work",
    workflow_ref: InternalId | None = None,
    revision: int = 1,
    mechanical_state: Mapping[str, Any] | None = None,
) -> ObjectRef:
    """Seed a subject record in the store."""
    if workflow_ref is None:
        workflow_ref = seed_test_workflow(store)
    state = dict(mechanical_state or {"state": "open"})
    state["revision"] = revision
    sub_rec = records.subject(
        subject_id=subject_id,
        kind=kind,
        mechanical_state=state,
        id_derivation="fixture",
        workflow_ref=workflow_ref,
    )
    store._put_staged(sub_rec)
    return make_object_ref(IdKind.SUBJECT, subject_id)


def make_test_lease(
    principal_id: str = "reg-operator",
    operation: str = "create_execution",
    target_ref: ObjectRef | None = None,
    expected_revision: int = 1,
    validity_seconds: int = 300,
    scope: Mapping[str, str] | None = None,
    revoked: bool = False,
    consumed: bool = False,
    issued_at: datetime | None = None,
    decision_basis: Any = None,
    lease_id: str | None = None,
) -> CapabilityLease:
    """Create a CapabilityLease for testing."""
    if issued_at is None:
        issued_at = fixture_time(0)
    expires_at = issued_at + timedelta(seconds=validity_seconds)
    if target_ref is None:
        target_ref = make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "wid_fixture_root", sub_kind=SubjectKind.WORK),
        )
    principal = make_test_principal(principal_id=principal_id)
    if lease_id is None:
        lease_id = f"b10-{operation}-{os.urandom(6).hex()}"
    return CapabilityLease(
        lease_id=lease_id,
        principal=principal,
        operation=operation,
        target=target_ref,
        scope=dict(scope or {"mode": "write"}),
        issued_at=issued_at,
        expires_at=expires_at,
        expected_revision=expected_revision,
        authority_basis=("authority_engine",),
        decision_basis=decision_basis,
        revocation_state=LEASE_REVOKED if revoked else LEASE_ISSUED,
        consumption_state=LEASE_CONSUMED if consumed else LEASE_ISSUED,
    )


@dataclass
class RegressionGraphFixture:
    """Aggregated test fixture containing store, repo, resolver, and rebuild service."""

    store: TransactionStore
    repo: GraphRepository
    resolver: OwningSubjectResolver
    binder: SubjectBindingResolver
    rebuilder: ProjectionRebuildService
    context: TrustedContext
    time: datetime

    # Seeded references
    workflow_id: InternalId
    workspace_subject_ref: ObjectRef
    project_subject_ref: ObjectRef
    plan_subject_ref: ObjectRef
    work_subject_ref: ObjectRef
    execution_ref: ObjectRef
    completion_ref: ObjectRef
    decision_ref: ObjectRef
    child_work_subject_ref: ObjectRef
    edge_ref: ObjectRef


def setup_standard_regression_graph(
    workspace_name: str = "fixture-workspace",
    project_name: str = "fixture-project",
    plan_name: str = "plan_20260818T000000_b10",
) -> RegressionGraphFixture:
    """Build a complete, seeded in-memory regression graph with all record kinds."""
    store = make_test_store()
    repo = store
    resolver = OwningSubjectResolver(repo)
    binder = SubjectBindingResolver(repo)
    rebuilder = ProjectionRebuildService(repo)
    ctx = make_test_context()
    t0 = fixture_time(0)

    # 1. Workflow
    wf_id = make_id(IdKind.WORKFLOW, "wf_b10_reg_01")
    wf_record = records.workflow(
        workflow_id=wf_id,
        semantic_intent="Establish deterministic behavioral regression foundation proof",
        creation_context={"lane": "M3-B10"},
        goal="B10 Proof",
    )
    store._put_staged(wf_record)

    # 2. Workspace Subject
    ws_id = make_id(
        IdKind.SUBJECT,
        workspace_subject(workspace_name).value,
        sub_kind=SubjectKind.WORKSPACE,
    )
    ws_sub = records.subject(
        subject_id=ws_id,
        workflow_ref=wf_id,
        kind="workspace",
        id_derivation="deterministic",
        mechanical_state={"workspace_name": workspace_name, "revision": 1, "state": "active"},
    )
    ws_ref = make_object_ref(IdKind.SUBJECT, ws_id)
    store._put_staged(ws_sub)

    # 3. Project Subject
    owning_ws = workspace_subject(workspace_name)
    proj_id = make_id(
        IdKind.SUBJECT,
        project_subject(project_name, owning_ws).value,
        sub_kind=SubjectKind.PROJECT,
    )
    proj_sub = records.subject(
        subject_id=proj_id,
        workflow_ref=wf_id,
        kind="project",
        id_derivation="deterministic",
        mechanical_state={"project_name": project_name, "revision": 1, "state": "active"},
    )
    proj_ref = make_object_ref(IdKind.SUBJECT, proj_id)
    store._put_staged(proj_sub)

    # 4. Plan Subject
    owning_proj = project_subject(project_name, owning_ws)
    plan_id = make_id(
        IdKind.SUBJECT,
        plan_subject(plan_name, owning_proj).value,
        sub_kind=SubjectKind.PLAN,
    )
    plan_sub = records.subject(
        subject_id=plan_id,
        workflow_ref=wf_id,
        kind="plan",
        id_derivation="deterministic",
        mechanical_state={
            "plan_name": plan_name,
            "current_milestone": "M3",
            "milestone": "M3",
            "state": "in_progress",
            "handoff_state": "m3_b10_ready",
            "revision": 1,
        },
    )
    plan_ref = make_object_ref(IdKind.SUBJECT, plan_id)
    store._put_staged(plan_sub)

    # 5. Parent Work Subject
    work_id = make_id(
        IdKind.SUBJECT,
        mint_subject_value("work", "wid_b10_parent_01"),
        sub_kind=SubjectKind.WORK,
    )
    work_sub = records.subject(
        subject_id=work_id,
        workflow_ref=wf_id,
        kind="work",
        id_derivation="minted",
        mechanical_state={"state": "running", "step": "step_1", "revision": 1},
    )
    work_ref = make_object_ref(IdKind.SUBJECT, work_id)
    store._put_staged(work_sub)

    # 6. Execution under Work Subject
    exec_id = make_id(IdKind.EXECUTION, "exec_b10_01")
    exec_record = records.execution(
        execution_id=exec_id,
        subject_ref=work_id,
        executor_kind="local_evaluator",
        mechanical_status="running",
        started_at=t0.isoformat(),
        correlation_id="corr-b10-01",
    )
    exec_ref = make_object_ref(IdKind.EXECUTION, exec_id)
    store._put_staged(exec_record)

    # 7. Completion for Execution
    comp_id = make_id(IdKind.COMPLETION, "comp_b10_01")
    comp_record = records.completion(
        completion_id=comp_id,
        execution_ref=exec_id,
        outcome="success",
        evidence_refs=["evidence-artifact-01"],
        recorded_at=(t0 + timedelta(seconds=10)).isoformat(),
    )
    comp_ref = make_object_ref(IdKind.COMPLETION, comp_id)
    store._put_staged(comp_record)

    # 8. Materialized Decision under Work Subject
    dec_id = make_id(IdKind.DECISION, "dec_b10_01")
    dec_record = records.decision(
        decision_id=dec_id,
        subject_ref=work_id,
        decision_kind="branch_followup",
        statement="Authorize followup child task creation for regression validation",
        target_refs=[work_ref.serialize()],
        evidence_refs=["evidence-artifact-01"],
        decision_time=(t0 + timedelta(seconds=15)).isoformat(),
    )
    dec_ref = make_object_ref(IdKind.DECISION, dec_id)
    store._put_staged(dec_record)

    # 9. Child Work Subject
    child_work_id = make_id(
        IdKind.SUBJECT,
        mint_subject_value("work", "wid_b10_child_01"),
        sub_kind=SubjectKind.WORK,
    )
    child_work_sub = records.subject(
        subject_id=child_work_id,
        workflow_ref=wf_id,
        kind="work",
        id_derivation="minted",
        mechanical_state={"state": "open", "step": "step_followup", "revision": 1},
    )
    child_work_ref = make_object_ref(IdKind.SUBJECT, child_work_id)
    store._put_staged(child_work_sub)

    # 10. FollowupEdge linking parent to child backed by decision
    edge_id = make_id(IdKind.EDGE, "edge_b10_01")
    edge_record = records.followup_edge(
        edge_id=edge_id,
        parent_subject_ref=work_id,
        child_subject_ref=child_work_id,
        source_decision_ref=dec_id,
        rationale="Followup execution authorized by dec_b10_01",
    )
    edge_ref = make_object_ref(IdKind.EDGE, edge_id)
    store._put_staged(edge_record)

    return RegressionGraphFixture(
        store=store,
        repo=repo,
        resolver=resolver,
        binder=binder,
        rebuilder=rebuilder,
        context=ctx,
        time=t0,
        workflow_id=wf_id,
        workspace_subject_ref=ws_ref,
        project_subject_ref=proj_ref,
        plan_subject_ref=plan_ref,
        work_subject_ref=work_ref,
        execution_ref=exec_ref,
        completion_ref=comp_ref,
        decision_ref=dec_ref,
        child_work_subject_ref=child_work_ref,
        edge_ref=edge_ref,
    )


class TempWorkspaceFixture:
    """Context manager for temporary isolated filesystem projects."""

    def __init__(self, prefix: str = "reg-ws-") -> None:
        self.prefix = prefix
        self.workdir: Path | None = None

    def __enter__(self) -> TempWorkspaceFixture:
        self.workdir = Path(tempfile.mkdtemp(prefix=self.prefix))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.workdir and self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)

    def create_project(self, project_id: str, parent_dir: Path | None = None) -> Path:
        if not self.workdir:
            raise RuntimeError("TempWorkspaceFixture not entered")
        target_parent = parent_dir or self.workdir
        project_dir = target_parent / project_id
        (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
            "summary": "B10 behavioral regression fixture project",
            "capabilities": ["fixture"],
            "paths": {
                "source_root": ".",
                "source": ["."],
                "docs": ["docs"],
                "scripts": ["scripts"],
                "profiles": ["profiles"],
                "skills": ["skills"],
                "tests": ["tests"],
            },
            "commands": {"validate": ["validate"], "deploy": ["deploy"], "verify_deploy": ["verify"]},
            "runtime": {"deployment_type": "managed-files", "requires_human_checkpoint": False},
            "codegraph": {"enabled": False, "index_location": ".codegraph/"},
            "plan": {"active_plan_id": None},
            "constraints": [],
        }
        for sub in ("docs", "scripts", "profiles", "skills", "tests"):
            (project_dir / sub).mkdir(exist_ok=True)
        (project_dir / ".aota" / "project.yaml").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        return project_dir

    def create_registry(self, workspace_name: str = "fixture-ws", workspace_dir: Path | None = None) -> Path:
        if not self.workdir:
            raise RuntimeError("TempWorkspaceFixture not entered")
        registry = self.workdir / "workspaces.json"
        target_ws = str(workspace_dir or self.workdir)
        registry.write_text(
            json.dumps({workspace_name: {"candidates": [target_ws]}}),
            encoding="utf-8",
        )
        return registry


__all__ = [
    "FIXTURES_REQUIRE_DEPLOYMENT",
    "FIXTURES_REQUIRE_HERMES_RUNTIME",
    "FIXTURES_REQUIRE_LIVE_GITHUB",
    "FIXTURES_REQUIRE_NETWORK",
    "FIXTURES_REQUIRE_SHADOW_GRAPH",
    "FIXTURES_USE_PRODUCTION_AUTHORITY_STATE",
    "RegressionGraphFixture",
    "TempWorkspaceFixture",
    "fixture_time",
    "make_test_context",
    "make_test_lease",
    "make_test_principal",
    "make_test_store",
    "seed_test_subject",
    "seed_test_workflow",
    "setup_standard_regression_graph",
]
