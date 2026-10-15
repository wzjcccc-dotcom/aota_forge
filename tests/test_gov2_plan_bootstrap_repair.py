"""Focused Governance 2.0 Plan bootstrap bridge proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import local_plan_authority_reference
from aota_forge.core_ingress import (
    CanonicalDispatchBinding,
    dispatch_via_core,
    resolve_descriptor,
    validate_operation_input,
)
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeError, GovernanceIntakeService
from aota_forge.governance.plan_semantics import (
    ADD_WORK_ITEM_AMENDMENT_CONTRACT,
    M2_RV2_WAIVER_DECISION_REF,
    M2_RV2_WAIVER_REASON,
    WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT,
    PlanSemanticError,
    normalize_add_work_item,
    normalize_plan_amendment,
    plan_amendment_contracts_for_help,
)
from aota_forge.mcp_transport import help_operation_projection


PROJECT_ID = "gov2planbootstrap"
PLAN_ID = "plan_gov2_bootstrap"

MANIFEST = f"""schema_version: 1
project:
  id: {PROJECT_ID}
  name: Gov2 bootstrap fixture
  kind: test
  status: active
summary: bounded semantic amendment fixture
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

PLAN_BODY = f"""# [PLAN] Gov2 bootstrap fixture

## Plan Identity
PROJECT_ID={PROJECT_ID}
PLAN_ID={PLAN_ID}
PLAN_REVISION=R1

## Current State
PLAN_STATUS=active
PLAN_ACCEPTED=yes
CURRENT_MILESTONE=M2
CURRENT_STATUS=bootstrap_repair_ready
CURRENT_BLOCKER=none
M1_STATUS=completed
M2_STATUS=in_progress
M2_DAG=W1 -> W2 -> W3 -> W4 -> W5
M2_WORK_ITEMS=W1, W2, W3, W4, W5

## M2 - Governance Bootstrap
Repair the structured Plan amendment path.

#### M2/W1 - Authority read
Read the Plan from its trusted current authority.

#### M2/W2 - Semantic index
Index semantic milestone and Work identities.

#### M2/W3 - Bounded section read
Hydrate one semantic section at a time.

#### M2/W4 - Typed amendment
Apply a structured amendment through the existing lifecycle.

#### M2/W5 - Readback and projection
Verify durable CAS and refresh projections.

## Decision D1 - Keep Plan authority canonical
plan.md and the durable journal remain authoritative.

## Risks
Stale semantic sections must fail closed.

## Runtime
Use the existing reference runtime.

## Configuration
The model-facing operation contract is canonical.
"""

PLAN_BODY_WITH_W6 = (
    PLAN_BODY.replace(
        "M2_DAG=W1 -> W2 -> W3 -> W4 -> W5\nM2_WORK_ITEMS=W1, W2, W3, W4, W5",
        "M2_DAG=W1 -> W2 -> W3 -> W4 -> W5, W6\nM2_WORK_ITEMS=W1, W2, W3, W4, W5, W6",
    ).replace(
        "## Decision D1 - Keep Plan authority canonical",
        "#### M2/W6 - Bounded sibling fixture\n"
        "Verify that a Work heading remains distinct from its milestone.\n\n"
        "## Decision D1 - Keep Plan authority canonical",
    )
)


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
        workspace_id="gov2-bootstrap-workspace",
        governance_base=governance_base,
        registry_path=registry,
        workspace_root=workspace,
        project_root=project_root,
    )


def _service(
    tmp_path: Path, *, plan_body: str = PLAN_BODY
) -> tuple[GovernanceIntakeContext, GovernanceIntakeService]:
    context = _context(tmp_path)
    service = GovernanceIntakeService(
        context,
        principal="bootstrap-operator",
        decision_source={
            "owner_type": "task-main",
            "owner_id": "bootstrap-operator",
            "channel": "test",
            "provenance": "trusted_fixture",
        },
    )
    service.plan("init", project_id=PROJECT_ID, plan_id=PLAN_ID, candidate_body=plan_body)
    return context, service


def _inspect(service: GovernanceIntakeService, view: str = "card", **extra: object) -> dict[str, object]:
    return service.plan(
        "inspect",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        view=view,
        **extra,
    )


def _add_work_item(*, work_id: str = "W6", dependencies: list[str] | None = None) -> dict[str, object]:
    return {
        "kind": "add_work_item",
        "milestone_id": "M2",
        "work_item": {
            "id": work_id,
            "title": "Bootstrap repair validation",
            "objective": "Prove the structured amendment and authority readback.",
            "scope": ["Use the existing Plan lifecycle pipeline."],
            "dependencies": list(dependencies or []),
            "acceptance": ["The resulting Plan preserves W1-W5 and adds this exact Work Item."],
        },
    }


def test_progressive_plan_inspection_is_authority_bound_and_bounded(tmp_path: Path) -> None:
    _context_value, service = _service(tmp_path)

    card = _inspect(service)
    assert card["view"] == "card"
    assert card["plan_revision"] == 1
    assert card["plan_status"] == "active"
    assert card["current_milestone"] == "M2"
    assert card["current_blocker"] == "none"
    assert "plan_body" not in card
    assert card["plan_card"]["plan_id"] == PLAN_ID
    assert card["milestone_card"]["milestone_id"] == "M2"
    assert card["authority_source_revision"] == card["source_digest"]

    first_index = _inspect(service, "index")["semantic_index"]
    second_index = _inspect(service, "index")["semantic_index"]
    refs = {item["section_ref"] for item in first_index["sections"]}
    assert {"M2", "M2/W1", "M2/W5", "Decisions/D1", "Risks", "Runtime", "Configuration"} <= refs
    assert first_index == second_index
    assert all("line_range" not in item for item in first_index["sections"])
    assert first_index["line_number_is_authority"] is False

    section = _inspect(service, "section", section_ref="M2/W3")["section"]
    assert section["section_ref"] == "M2/W3"
    assert section["section_digest"]
    assert section["content"].startswith("#### M2/W3")
    assert "M2/W2" not in section["content"]
    assert section["line_hint"] is None or isinstance(section["line_hint"], int)

    full = _inspect(service, "full")
    assert full["read_through"] == "LocalPlanAuthorityReadAdapter"
    assert full["plan_body"] == PLAN_BODY
    assert full["normalized_plan"]["source_digest"]


def test_milestone_section_excludes_work_headings_and_preserves_siblings(tmp_path: Path) -> None:
    _context_value, service = _service(tmp_path, plan_body=PLAN_BODY_WITH_W6)

    index = _inspect(service, "index")["semantic_index"]
    sections = index["sections"]
    assert sum(item["section_ref"] == "M2/W6" for item in sections) == 1

    milestone = _inspect(service, "section", section_ref="M2")["section"]
    assert milestone["content"].count("#### M2/W6 - ") == 1
    assert milestone["content"].count("#### M2/W5 - ") == 1

    work_w6 = _inspect(service, "section", section_ref="M2/W6")["section"]
    assert work_w6["section_ref"] == "M2/W6"
    assert work_w6["content"].count("#### M2/W6 - ") == 1

    work_w5 = _inspect(service, "section", section_ref="M2/W5")["section"]
    assert work_w5["section_ref"] == "M2/W5"
    assert work_w5["content"].count("#### M2/W5 - ") == 1


def test_structured_add_work_item_mints_decision_and_refreshes_projections(tmp_path: Path) -> None:
    context, service = _service(tmp_path)
    before = _inspect(service, "full")
    index = _inspect(service, "index")["semantic_index"]
    milestone_digest = next(item["section_digest"] for item in index["sections"] if item["section_ref"] == "M2")

    result = service.plan(
        "amend",
        project_id=PROJECT_ID,
        plan_id=PLAN_ID,
        expected_plan_revision=1,
        amendment=_add_work_item(work_id="W6", dependencies=[]),
        target_ref="M2",
        expected_section_digest=milestone_digest,
    )

    readback = result["amendment_readback"]
    after = _inspect(service, "full")
    assert "M2/W6" in after["plan_body"]
    assert "M2_WORK_ITEMS=W1, W2, W3, W4, W5, W6" in after["plan_body"]
    assert "M2_DAG=W1 -> W2, W2 -> W3, W3 -> W4, W4 -> W5, W6" in after["plan_body"]
    assert "M2/W1 - Authority read" in after["plan_body"]
    assert readback["plan_revision_before"] == 1
    assert readback["plan_revision_after"] == 2
    assert readback["plan_digest_before"] == before["source_digest"]
    assert readback["plan_digest_after"] == after["source_digest"]
    assert readback["plan_digest_before"] != readback["plan_digest_after"]
    assert readback["semantic_decision_ref"].startswith("plan-amendment:")
    assert readback["amendment_digest"]
    decision = readback["semantic_decision_receipt"]
    assert decision["decision_kind"] == "plan_amendment"
    assert decision["decision_source"]["owner_id"] == "bootstrap-operator"
    assert decision["request_provenance"]["provenance"] == "trusted_fixture"
    assert readback["journal_cas_outcome"]["journal_state"] in {"VERIFIED", "VERIFIED_RECOVERED"}
    assert readback["journal_cas_outcome"]["authority_write_verified"] is True
    assert readback["projection_reconciliation_status"] == "RECONCILED"
    assert result["projection_receipt"] is not None
    milestone_projection = next(
        card for card in result["projection"]["milestones"] if card["milestone_id"] == "M2"
    )
    assert milestone_projection["work_item_refs"] == ["W1", "W2", "W3", "W4", "W5", "W6"]
    assert any(
        item.get("digest") == readback["plan_digest_after"]
        for item in after["context_route"]["entries"]
    )
    assert (Path(context.governance_base) / PROJECT_ID / "MAP.md").is_file()
    assert (Path(context.governance_base) / PROJECT_ID / "STATUS.md").is_file()


def _plan005_r9_body() -> str:
    body = PLAN_BODY_WITH_W6.replace(PROJECT_ID, "aota_forge").replace(
        PLAN_ID,
        "plan_005_governance_self_hosting_routing_runtime_lifecycle",
    )
    body = body.replace("PLAN_REVISION=R1", "PLAN_REVISION=R9")
    body = body.replace(
        "Verify that a Work heading remains distinct from its milestone.",
        "Verify that a Work heading remains distinct from its milestone.\n"
        "Lifecycle is W6 → integration → RV2 → Doctor → M2 closure; "
        "RV2 is a separate Milestone-level gate, not a Work Item.\n"
        "AC12: No second Plan authority; M2_RV2_REQUIRED=yes and RV2 remains a separate Milestone-level gate.",
    )
    return body


def test_plan005_rv2_waiver_uses_governance_cas_and_preserves_work_semantics(tmp_path: Path) -> None:
    project_id = "aota_forge"
    plan_id = "plan_005_governance_self_hosting_routing_runtime_lifecycle"
    workspace = tmp_path / "workspace"
    project_root = workspace / "checkout"
    (project_root / ".aota").mkdir(parents=True)
    (project_root / ".aota" / "project.yaml").write_text(MANIFEST.replace(PROJECT_ID, project_id), encoding="utf-8")
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text("{}", encoding="utf-8")
    context = GovernanceIntakeContext(
        workspace_id="plan005-rv2-waiver-workspace",
        governance_base=governance_base,
        registry_path=registry,
        workspace_root=workspace,
        project_root=project_root,
    )
    service = GovernanceIntakeService(
        context,
        principal="task-main",
        decision_source={
            "owner_type": "task-main",
            "owner_id": "task-main",
            "channel": "test",
            "provenance": "trusted_fixture",
        },
    )
    service.plan("init", project_id=project_id, plan_id=plan_id, candidate_body=_plan005_r9_body())
    before = service.plan("inspect", project_id=project_id, plan_id=plan_id, view="full")
    index = service.plan("inspect", project_id=project_id, plan_id=plan_id, view="index")["semantic_index"]
    milestone_digest = next(item["section_digest"] for item in index["sections"] if item["section_ref"] == "M2")
    before_work_digests = {
        item["section_ref"]: item["section_digest"]
        for item in index["sections"]
        if item["section_ref"].startswith("M2/") and item["section_ref"] != "M2/W6"
    }

    result = service.plan(
        "amend",
        project_id=project_id,
        plan_id=plan_id,
        expected_plan_revision=1,
        target_ref="M2",
        expected_section_digest=milestone_digest,
        amendment={
            "kind": "waive_m2_rv2_gate",
            "milestone_id": "M2",
            "decision_ref": M2_RV2_WAIVER_DECISION_REF,
        },
    )

    after = service.plan("inspect", project_id=project_id, plan_id=plan_id, view="full")
    after_index = service.plan("inspect", project_id=project_id, plan_id=plan_id, view="index")["semantic_index"]
    body = after["plan_body"]
    readback = result["amendment_readback"]
    assert "PLAN_REVISION=R10" in body
    assert "M2_RV2_REQUIRED=no" in body
    assert "M2_RV2_WAIVED_BY_USER=yes" in body
    assert f"M2_RV2_WAIVER_DECISION={M2_RV2_WAIVER_DECISION_REF}" in body
    assert f"M2_RV2_WAIVER_REASON={M2_RV2_WAIVER_REASON}" in body
    assert "W6 → integration → Doctor → M2 closure" in body
    assert "W6 → integration → RV2 → Doctor → M2 closure" not in body
    assert "AC12: No second Plan authority;" in body
    assert "M2_RV2_REQUIRED=yes and RV2 remains a separate Milestone-level gate." not in body
    assert after["normalized_plan"]["milestone_work_items"]["M2"] == ["W1", "W2", "W3", "W4", "W5", "W6"]
    assert before["normalized_plan"]["milestone_dependencies"]["M2"] == after["normalized_plan"]["milestone_dependencies"]["M2"]
    assert {
        item["section_ref"]: item["section_digest"]
        for item in after_index["sections"]
        if item["section_ref"].startswith("M2/") and item["section_ref"] != "M2/W6"
    } == before_work_digests
    assert readback["plan_revision_before"] == 1
    assert readback["plan_revision_after"] == 2
    assert readback["plan_digest_before"] == before["source_digest"]
    assert readback["plan_digest_after"] == after["source_digest"]
    assert readback["plan_digest_before"] != readback["plan_digest_after"]
    assert readback["journal_cas_outcome"]["authority_write_verified"] is True
    assert readback["journal_cas_outcome"]["journal_state"] in {"VERIFIED", "VERIFIED_RECOVERED"}


def test_add_work_item_fail_closed_for_duplicate_stale_revision_and_section_digest(tmp_path: Path) -> None:
    _context_value, service = _service(tmp_path)
    original = _inspect(service, "full")["source_digest"]

    with pytest.raises(GovernanceIntakeError, match="WORK_ITEM_ALREADY_EXISTS"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=1,
            amendment=_add_work_item(work_id="W5"),
        )
    with pytest.raises(GovernanceIntakeError, match="STALE_PLAN_REVISION"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=2,
            amendment=_add_work_item(),
        )
    with pytest.raises(GovernanceIntakeError, match="STALE_SECTION_DIGEST"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=1,
            amendment=_add_work_item(),
            target_ref="M2",
            expected_section_digest="0" * 64,
        )
    with pytest.raises(GovernanceIntakeError, match="DAG_UNKNOWN_WORK_ITEM"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=1,
            amendment=_add_work_item(dependencies=["W99"]),
        )
    with pytest.raises(GovernanceIntakeError, match="WHOLE_BODY_AMENDMENT_NOT_SUPPORTED"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=1,
            candidate_body=PLAN_BODY,
        )
    with pytest.raises(GovernanceIntakeError, match="AMENDMENT_SCOPE_INVALID"):
        service.plan(
            "amend",
            project_id=PROJECT_ID,
            plan_id=PLAN_ID,
            expected_plan_revision=1,
            amendment={
                "kind": "waive_m2_rv2_gate",
                "milestone_id": "M2",
                "decision_ref": M2_RV2_WAIVER_DECISION_REF,
            },
        )
    assert _inspect(service, "full")["source_digest"] == original


def test_plan_help_and_core_identity_binding_describe_the_repaired_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    help_contract = help_operation_projection("governance.plan")
    assert help_contract["default_inspect_view"] == "card"
    assert help_contract["inspect_views"] == ["card", "index", "section", "full"]
    assert help_contract["read_or_write"] == "mode-dependent"
    assert help_contract["implemented_amendment_kinds"] == ["add_work_item", "waive_m2_rv2_gate"]
    assert help_contract["implemented_amendment_kinds"] == [
        contract["fields"]["kind"]["const"]
        for contract in (ADD_WORK_ITEM_AMENDMENT_CONTRACT, WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT)
    ]
    assert help_contract["amendment_contracts"] == plan_amendment_contracts_for_help()
    amendment_schema = help_contract["amendment_contracts"]["add_work_item"]
    amendment_fields = ADD_WORK_ITEM_AMENDMENT_CONTRACT["fields"]
    assert amendment_schema["required"] == list(amendment_fields)
    assert amendment_schema["fields"]["kind"]["const"] == "add_work_item"
    assert amendment_schema["fields"]["milestone_id"]["type"] == "milestone_id"
    work_item_schema = amendment_schema["fields"]["work_item"]
    work_item_fields = amendment_fields["work_item"]["fields"]
    assert work_item_schema["required"] == list(work_item_fields)
    assert work_item_schema["fields"] == work_item_fields
    assert work_item_schema["fields"]["id"]["type"] == "work_item_id"
    assert work_item_schema["fields"]["title"]["max_utf8_bytes"] == 256
    assert work_item_schema["fields"]["objective"]["max_utf8_bytes"] == 4096
    assert work_item_schema["fields"]["scope"]["type"] == "array[string]"
    assert work_item_schema["fields"]["dependencies"]["type"] == "array[work_item_id]"
    assert work_item_schema["fields"]["acceptance"]["type"] == "array[string]"
    waiver_schema = help_contract["amendment_contracts"]["waive_m2_rv2_gate"]
    assert waiver_schema["required"] == list(WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT["fields"])
    assert waiver_schema["fields"]["milestone_id"]["const"] == "M2"
    assert waiver_schema["fields"]["decision_ref"]["const"] == M2_RV2_WAIVER_DECISION_REF
    assert normalize_plan_amendment(
        {"kind": "waive_m2_rv2_gate", "milestone_id": "M2", "decision_ref": M2_RV2_WAIVER_DECISION_REF}
    )["kind"] == "waive_m2_rv2_gate"
    with pytest.raises(PlanSemanticError, match="AMENDMENT_DECISION_INVALID"):
        normalize_plan_amendment(
            {"kind": "waive_m2_rv2_gate", "milestone_id": "M2", "decision_ref": "user-decision:other"}
        )

    payload = _add_work_item()
    max_title_bytes = work_item_fields["title"]["max_utf8_bytes"]
    payload["work_item"]["title"] = "x" * (max_title_bytes + 1)
    with pytest.raises(PlanSemanticError, match="AMENDMENT_FIELD_INVALID"):
        normalize_add_work_item(payload)
    monkeypatch.setitem(work_item_fields["title"], "max_utf8_bytes", max_title_bytes + 1)
    payload["work_item"]["title"] = "x" * (max_title_bytes + 1)
    assert len(normalize_add_work_item(payload)["work_item"]["title"]) == max_title_bytes + 1
    assert (
        help_operation_projection("governance.plan")["amendment_contracts"]["add_work_item"]["fields"]
        ["work_item"]["fields"]["title"]["max_utf8_bytes"]
        == max_title_bytes + 1
    )
    assert "structured semantic delta" in help_contract["amendment_semantics"]
    validated = validate_operation_input(
        resolve_descriptor("governance.plan"),
        {
            "mode": "amend",
            "project_id": PROJECT_ID,
            "plan_id": PLAN_ID,
            "expected_plan_revision": 1,
            "amendment": _add_work_item(),
        },
    )
    assert "candidate_body" not in validated

    binding = CanonicalDispatchBinding(
        project_id=PROJECT_ID,
        plan_authority_binding=PlanAuthorityBinding(
            plan_id=PLAN_ID,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=local_plan_authority_reference(PROJECT_ID, PLAN_ID),
            source_revision="a" * 64,
            source_digest="a" * 64,
        ),
    )
    rejected = dispatch_via_core(
        "governance.plan",
        {"mode": "inspect", "project_id": PROJECT_ID, "plan_id": "plan_other"},
        binding,
    )
    assert rejected.ok is False
    assert rejected.error["code"] == "AUTHORITY_DENIED"
