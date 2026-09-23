"""M1/W3 ChatGPT planning convergence and Gov1 drift guards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_GOVERNANCE = ROOT / "chat_governance"
OPERATIONS = ROOT / ".aota" / "contracts" / "operations.yaml"
RESULTS = ROOT / ".aota" / "contracts" / "results.yaml"
INTAKE = ROOT / "aota_forge" / "governance" / "intake.py"
TRANSPORT = ROOT / "aota_forge" / "mcp_transport.py"


def _doc(name: str) -> str:
    return (CHAT_GOVERNANCE / name).read_text(encoding="utf-8")


def test_canonical_chat_governance_guard_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_chat_governance.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHAT_GOVERNANCE_STATIC_GUARD_PASS" in result.stdout


def test_shared_core_is_carrier_neutral_and_legacy_is_bounded() -> None:
    core = _doc("aota-portable-plan-governance.md")
    planning = _doc("aota-chatgpt-project-planning.md")
    legacy = _doc("aota-github-issue-planning.md")

    for marker in (
        "PORTABLE_PLAN_SEMANTICS_SINGLE_CORE=yes",
        "PORTABLE_PLAN_AUTHORITY_CARRIER_NEUTRAL=yes",
        "PLAN_AUTHORITY_RESOLVED_BY_BINDING=yes",
        "AUTHORITY_SELECTED_BY_PLAN_AUTHORITY_BINDING=yes",
        "PLAN_ID_IS_AUTHORITY=no",
        "GITHUB_5_COMMENT_MODEL_ADAPTER_SPECIFIC=yes",
        "GITHUB_5_COMMENT_MODEL_GOV2_REQUIRED=no",
        "LOCAL_GOV2_CREATES_FIVE_COMMENT_FILES=no",
    ):
        assert marker in core, marker

    assert "NEW_CHATGPT_PLAN_DEFAULT_AUTHORITY=local_governance" in planning
    assert "NEW_CHATGPT_PLAN_REQUIRES_GITHUB_ISSUE=no" in planning
    assert "LEGACY_GITHUB_ENTRY_ALIAS=yes" in legacy
    assert "NORMAL_NEW_PLAN_ENTRY=no" in legacy
    assert "ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes" in legacy

    # The legacy marker is allowed only in the explicitly marked compatibility
    # section, never as an unqualified normal-path claim.
    occurrence = core.index("ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes")
    assert "## legacy" in core[:occurrence].lower()
    assert "LOCAL_GOV2_REQUIRES_FIVE_COMMENT_FILES=yes" not in core


def test_chat_planning_contract_matches_runtime_modes_and_help() -> None:
    planning = _doc("aota-chatgpt-project-planning.md")
    intake = INTAKE.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")
    operations = OPERATIONS.read_text(encoding="utf-8")
    results = RESULTS.read_text(encoding="utf-8")

    assert "governance.project: init | inspect | reconcile" in planning
    assert "governance.plan: init | inspect | amend | close" in planning
    assert 'GOVERNANCE_OPERATIONS = (GOVERNANCE_PROJECT_OPERATION, GOVERNANCE_PLAN_OPERATION)' in intake
    assert 'GOVERNANCE_PROJECT_MODES = frozenset({"init", "inspect", "reconcile"})' in intake
    assert 'GOVERNANCE_PLAN_MODES = frozenset({"init", "inspect", "amend", "close"})' in intake
    assert '"governance.project",' in transport
    assert '"governance.plan",' in transport
    assert 'aota-project-governance@1.0.0' in transport
    assert 'aota-plan-governance@1.0.0' in transport

    plan_contract = operations[operations.index("- name: governance.plan") :]
    project_contract = operations[
        operations.index("- name: governance.project") : operations.index("- name: role.bootstrap")
    ]
    assert "- name: mode" in project_contract
    assert "- name: project_id" in project_contract
    assert "- name: mode" in plan_contract
    assert "- name: project_id" in plan_contract
    assert "- name: plan_id" in plan_contract
    assert "- name: candidate_body" in plan_contract
    assert "subject_revision_precondition: true" in plan_contract
    assert "result_contract: canonical_mutation_result.v1" in plan_contract
    assert "- name: governance.plan" not in results

    for marker in (
        "CHATGPT_PROJECT_INIT_CONTRACT_ALIGNED=yes",
        "CHATGPT_PLAN_INIT_CONTRACT_ALIGNED=yes",
        "CHATGPT_PLAN_AMEND_CONTRACT_ALIGNED=yes",
        "CODEX_USES_CANONICAL_AF_LIFECYCLE=yes",
        "AOTA_READER_ROLE=readonly_observation",
        "AOTA_READER_GOVERNANCE_WRITE=no",
        "MAP_STATUS_MANUAL_MAINTENANCE=no",
        "PROJECT_STEWARD_IS_NORMAL_WORKER=no",
        "CHATGPT_GOVERNANCE_DUPLICATES_RUNTIME_SKILL=no",
    ):
        assert marker in planning, marker


def test_legacy_route_does_not_define_local_gov2_behavior() -> None:
    legacy = _doc("aota-github-issue-planning.md")
    assert "LOCAL_GOVERNANCE_NORMAL_SUPPORTED=yes" in legacy
    assert "GOV1_IS_NORMAL_NEW_PLAN_PATH=no" in legacy
    assert "LOCAL_GOV2_CREATES_FIVE_COMMENT_FILES=no" in legacy
    assert "LEGACY_ISSUE_BODY_MUTATION_ONLY_WHEN_BOUND=yes" in legacy
    assert "aota-chatgpt-project-planning.md" in legacy
