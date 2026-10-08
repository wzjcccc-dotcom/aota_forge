"""AF #57 M3/W2 — canonical Project/Plan governance intake."""

from __future__ import annotations

from pathlib import Path

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.ingress import execute
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeService
from aota_forge.governance.local_lifecycle import (
    PlanCloseMaterializationEvidence,
    project_terminal_plan_body,
)
from aota_forge.mcp_transport import GOVERNANCE_OPERATIONS, help_operation_projection
from aota_forge.governance.project_store import PLAN_LIFECYCLE_RETIRED
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore


PROJECT_ID = "af57m3w2"
PLAN_ID = "plan_af57_m3_w2"

MANIFEST = f"""schema_version: 1
project:
  id: {PROJECT_ID}
  name: intake fixture
  kind: test
  status: active
summary: bounded intake fixture
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph
plan:
  active_plan_id: null
constraints: []
"""

PLAN_BODY = """# [PLAN] AF #57 M3/W2 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_KIND=portable_plan
PROJECT_ID=af57m3w2
PLAN_ID=plan_af57_m3_w2
PLAN_REVISION=R1
PLAN_STATUS=active
PLAN_ACCEPTED=yes
CURRENT_MILESTONE=M3
CURRENT_STATUS=m3_user_acceptance_ready
CURRENT_BLOCKER=none
M1_STATUS=completed
M1_ACCEPTED=yes
M2_STATUS=completed
M2_ACCEPTED=yes
M3_STATUS=in_progress
M3_ACCEPTED=no
M3_USER_APPROVAL_SATISFIED=yes
M3_DAG=W1 -> W2
M3_WORK_ITEMS=W1, W2
```

## M3

#### M3/W2 - Governance intake
Bounded intake proof.
"""


def _context(tmp_path: Path) -> GovernanceIntakeContext:
    workspace = tmp_path / "workspace"
    project_root = workspace / "checkout"
    (project_root / ".aota").mkdir(parents=True)
    (project_root / ".aota" / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text("{}", encoding="utf-8")
    workspace.mkdir(exist_ok=True)
    return GovernanceIntakeContext(
        workspace_id="af57-m3-w2-workspace",
        governance_base=governance_base,
        registry_path=registry,
        workspace_root=workspace,
        project_root=project_root,
    )


def test_intake_composes_project_plan_amend_and_close(tmp_path: Path) -> None:
    context = _context(tmp_path)
    service = GovernanceIntakeService(context, principal="af57-operator")

    project = service.project("init", project_id=PROJECT_ID)
    assert project["status"] == "VERIFIED"
    assert project["project"]["status"] == "RECONCILED"
    assert (tmp_path / "plans" / PROJECT_ID / "MAP.md").is_file()
    assert (tmp_path / "plans" / PROJECT_ID / "STATUS.md").is_file()

    initialized = service.plan(
        "init",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=PLAN_BODY,
    )
    assert initialized["status"] == "VERIFIED"
    assert initialized["plan_id"] == PLAN_ID

    amended = service.plan(
        "amend",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        candidate_body=PLAN_BODY.replace("Bounded intake proof.", "Amended bounded intake proof."),
        expected_plan_revision=1,
    )
    assert amended["status"] == "VERIFIED"

    evidence = PlanCloseMaterializationEvidence(
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        milestone_id="M3",
        accepted_frontier="a" * 40,
        final_known_good="a" * 40,
        milestone_checkpoint_id="stewardship:" + "b" * 64,
        milestone_checkpoint_digest="c" * 64,
        milestone_receipt_id="mfr-" + "d" * 16,
        milestone_receipt_digest="e" * 64,
        milestone_steward_digest="f" * 64,
        plan_close_checkpoint_id="stewardship:" + "1" * 64,
        plan_close_checkpoint_digest="2" * 64,
        plan_close_receipt_id="mfr-" + "3" * 16,
        plan_close_receipt_digest="4" * 64,
        plan_close_steward_digest="5" * 64,
    )
    terminal_body = project_terminal_plan_body(
        PLAN_BODY.replace("Bounded intake proof.", "Amended bounded intake proof."),
        evidence,
    )

    closed = service.plan(
        "close",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_plan_revision=2,
        candidate_body=terminal_body,
        closure_evidence=evidence.to_dict(),
    )
    assert closed["status"] == "VERIFIED"

    store = SQLiteProjectGovernanceStore(tmp_path / "workspace" / "checkout" / ".aota" / "governance.sqlite3")
    try:
        record = store.get_plan(PROJECT_ID, PLAN_ID)
        assert record is not None
        assert record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        assert record.revision == 4
    finally:
        store.close()


def test_project_inspect_does_not_rewrite_projection(tmp_path: Path) -> None:
    context = _context(tmp_path)
    service = GovernanceIntakeService(context)
    service.project("init", project_id=PROJECT_ID)
    map_path = tmp_path / "plans" / PROJECT_ID / "MAP.md"
    before = map_path.read_bytes()

    inspected = service.project("inspect", project_id=PROJECT_ID)

    assert inspected["status"] == "INSPECTED"
    assert map_path.read_bytes() == before


def test_governance_intake_uses_canonical_core_ingress(tmp_path: Path) -> None:
    context = _context(tmp_path)
    trusted = bind_trusted_context(
        principal_id="af57-operator",
        principal_type="task-main",
        governance_intake_context=context,
    )

    result = execute(
        "governance.project",
        {"mode": "init", "project_id": PROJECT_ID},
        trusted_context=trusted,
    )

    assert result["ok"] is True
    assert result["data"]["project_id"] == PROJECT_ID


def test_governance_intake_fails_closed_without_trusted_context(tmp_path: Path) -> None:
    _context(tmp_path)
    result = execute(
        "governance.project",
        {"mode": "init", "project_id": PROJECT_ID},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "AUTHORITY_DENIED"


def test_governance_families_use_descriptor_help_and_single_transport() -> None:
    assert set(GOVERNANCE_OPERATIONS) == {
        "governance.project",
        "governance.plan",
        "governance.terminal_projection.reconcile",
    }
    project_help = help_operation_projection("governance.project")
    plan_help = help_operation_projection("governance.plan")
    terminal_reconcile_help = help_operation_projection(
        "governance.terminal_projection.reconcile"
    )
    assert "mode" in project_help["required_inputs"]
    assert "project_id" in plan_help["required_inputs"]
    assert project_help["relevant_skill_ref"] == "aota-project-governance@1.0.0"
    assert plan_help["relevant_skill_ref"] == "aota-plan-governance@1.0.0"
    assert terminal_reconcile_help["required_inputs"] == []
    assert terminal_reconcile_help["optional_inputs"] == []
    assert terminal_reconcile_help["read_or_write"] == "write"
    assert terminal_reconcile_help["relevant_skill_ref"] == "aota-plan-governance@1.0.0"
