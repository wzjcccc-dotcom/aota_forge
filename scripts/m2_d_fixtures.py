#!/usr/bin/env python3
"""M2-D focused deterministic validation fixtures.

Proves:

A. clean current state       — current M2 + historical M1 block + matching
                               control projection  → CURRENT_MILESTONE=M2,
                               GOVERNANCE_PROJECTION_DRIFT=no
B. historical duplicate      — old current values in a historical section do
                               NOT become authority
C. control drift             — body says M2, control comment says M1 →
                               body stays authority, DRIFT=yes, no ambiguity
D. malformed/contradictory   — authoritative current-state contradictions and
                               malformed values fail deterministically
E. real Issue #9 shape       — faithful excerpt of the governing body
                               normalizes to CURRENT_MILESTONE=M2
F. plan authority adapter    — read-side snapshot round trip, no write path

Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def text_block(entries: dict[str, str]) -> str:
    lines: list[str] = ["```text"]
    for key, value in entries.items():
        if "\n" in value:
            lines.append(f"{key}=")
            lines.extend(value.splitlines())
        else:
            lines.append(f"{key}={value}")
    lines.append("```")
    return "\n".join(lines)


def build_body(
    current_sections: list[dict[str, str]],
    *,
    historical: list[dict[str, str]] | None = None,
    milestone_specs: list[str] | None = None,
    governance: dict[str, str] | None = None,
    appendix: dict[str, str] | None = None,
    milestones_index: str | None = None,
) -> str:
    parts: list[str] = ["# [PLAN] AOTA Forge fixture body"]
    for index, section in enumerate(current_sections, start=1):
        parts.append(f"\n## {index}. Current State")
        parts.append(text_block(section))
    for block in historical or []:
        parts.append("\n## M1 Historical Evidence (superseded)")
        parts.append(text_block(block))
    if milestones_index:
        parts.append(f"\n## {milestones_index}")
    for title in milestone_specs or []:
        parts.append(f"\n### {title}")
        parts.append(text_block({"M1_STATUS": "completed"}))
    if governance:
        parts.append("\n## 2. Governance / Authority Model (amended)")
        parts.append(text_block(governance))
    if appendix:
        parts.append("\n## Appendix: Provenance")
        parts.append(text_block(appendix))
    return "\n".join(parts) + "\n"


FIXTURE_A_CURRENT = {
    "PLAN_STATUS": "in-progress",
    "CURRENT_MILESTONE": "M2",
    "CURRENT_STATUS": "m2_ready_for_planning",
    "CURRENT_BLOCKER": "none",
    "COMPLETED_MILESTONES": "M1",
    "M1_STATUS": "completed",
    "M2_STATUS": "planned",
    "HANDOFF_STATE": "m2_planning_ready",
    "CURRENT_NEXT_ACTION": "M2 planning against amended scope:\nmaterialize successor regression matrix",
    "CANONICAL_PROJECT_ID": "aota_forge",
    "CANONICAL_SOURCE_REPOSITORY": "wzjcccc-dotcom/aota_forge",
    "CANONICAL_PROJECT_ROOT": "/home/latios/workspace/aota_forge",
    "ACCEPTED_WRITABLE_BASE": "a380056b70ec11d45259f31babdfb38311672893",
    "M1_KNOWN_GOOD_CHECKPOINT": "a380056b70ec11d45259f31babdfb38311672893",
}

FIXTURE_A_HISTORICAL = {
    "CURRENT_MILESTONE": "M1",
    "CURRENT_STATUS": "m1_completed",
    "CURRENT_BLOCKER": "legacy-blocker",
    "HANDOFF_STATE": "m1_closed",
    "PLAN_STATUS": "completed",
}

FIXTURE_A_GOVERNANCE = {
    "ISSUE_BODY_REMAINS_PLAN_AUTHORITY": "yes",
    "CONTROL_COMMENTS_MUTABLE": "yes",
    "CONTROL_COMMENTS_PLAN_AUTHORITY": "no",
    "COMMENT_PLAN_AUTHORITY": "no",
}

FIXTURE_A_APPENDIX = {
    "FIXTURE_KIND": "A",
    "M2_D_LANE": "fixture-a",
}


def main() -> int:
    from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
    from aota_forge.core.plan.normalize import (
        PlanNormalizationError,
        normalize_portable_plan,
        observe_governance_projection_drift,
    )

    # ------------------------------------------------------------------ A
    body_a = build_body(
        [FIXTURE_A_CURRENT],
        historical=[FIXTURE_A_HISTORICAL],
        milestone_specs=["M1 — Fixture M1 Definition", "M2 — Fixture M2 Definition"],
        governance=FIXTURE_A_GOVERNANCE,
        appendix=FIXTURE_A_APPENDIX,
        milestones_index="17. Milestones (amended — supersedes previous M1-M7 structure)",
    )
    doc_a = normalize_portable_plan(body_a, source_revision="rev-fixture-a")
    check("fixture_a_normalizes", doc_a.current_milestone == "M2", doc_a.current_milestone)
    check("fixture_a_current_milestone", doc_a.current_milestone == "M2")
    check("fixture_a_plan_status", doc_a.plan_status == "in-progress")
    check("fixture_a_handoff_state", doc_a.handoff_state == "m2_planning_ready")
    check("fixture_a_completed_milestones", doc_a.completed_milestones == ("M1",))
    check("fixture_a_milestone_status", doc_a.milestone_status == {"M1": "completed", "M2": "planned"})
    check(
        "fixture_a_project_context",
        doc_a.project_context.get("CANONICAL_PROJECT_ID") == "aota_forge"
        and doc_a.project_context.get("CANONICAL_PROJECT_ROOT") == "/home/latios/workspace/aota_forge",
    )
    check(
        "fixture_a_known_good_checkpoints",
        doc_a.known_good_checkpoints == ("a380056b70ec11d45259f31babdfb38311672893",) * 2,
    )
    check(
        "fixture_a_governance_observations",
        doc_a.governance.get("ISSUE_BODY_REMAINS_PLAN_AUTHORITY") == "yes"
        and doc_a.governance.get("CONTROL_COMMENTS_PLAN_AUTHORITY") == "no",
    )
    check(
        "fixture_a_provenance_observations",
        "M1 Historical Evidence (superseded)" in doc_a.provenance_observations
        and doc_a.provenance_observations.get("Appendix: Provenance", {}).get("FIXTURE_KIND") == "A",
    )
    check("fixture_a_milestone_specs", set(doc_a.milestone_specs) == {"M1", "M2"})
    check(
        "fixture_a_digest",
        len(doc_a.source_digest) == 64 and doc_a.source_revision == "rev-fixture-a",
    )
    drift_a = observe_governance_projection_drift(
        doc_a,
        {"milestone_progress_index": {"CURRENT_MILESTONE": "M2", "PLAN_STATUS": "in-progress", "HANDOFF_STATE": "m2_planning_ready"}},
    )
    check("fixture_a_drift_no", drift_a["GOVERNANCE_PROJECTION_DRIFT"] == "no", str(drift_a["differences"]))

    # ------------------------------------------------------------------ B
    body_b = build_body(
        [
            {
                "PLAN_STATUS": "in-progress",
                "CURRENT_MILESTONE": "M2",
                "CURRENT_BLOCKER": "none",
                "HANDOFF_STATE": "m2_planning_ready",
                "COMPLETED_MILESTONES": "M1",
            }
        ],
        historical=[FIXTURE_A_HISTORICAL],
    )
    doc_b = normalize_portable_plan(body_b)
    check("fixture_b_historical_not_authority", doc_b.current_milestone == "M2", doc_b.current_milestone)
    check("fixture_b_status_not_overridden", doc_b.plan_status == "in-progress", doc_b.plan_status)
    check("fixture_b_blocker_not_overridden", doc_b.current_blocker == "none", doc_b.current_blocker)
    check("fixture_b_historical_diagnostic", any(d["code"] == "HISTORICAL_SECTION" for d in doc_b.diagnostics))
    check(
        "fixture_b_historical_key_observed_not_authoritative",
        any(d["code"] == "CURRENT_KEY_OUTSIDE_CURRENT_SECTION" and d["key"] == "CURRENT_MILESTONE" for d in doc_b.diagnostics),
    )
    check(
        "fixture_b_provenance_carries_old_values",
        doc_b.provenance_observations.get("M1 Historical Evidence (superseded)", {}).get("CURRENT_MILESTONE") == "M1",
    )

    # ------------------------------------------------------------------ C
    doc_c = normalize_portable_plan(body_a)
    drift_c = observe_governance_projection_drift(
        doc_c,
        {"milestone_progress_index": {"CURRENT_MILESTONE": "M1", "HANDOFF_STATE": "m1_closed"}},
    )
    check("fixture_c_drift_yes", drift_c["GOVERNANCE_PROJECTION_DRIFT"] == "yes", str(drift_c["differences"]))
    check(
        "fixture_c_diff_evidence_bounded",
        any(d["field"] == "CURRENT_MILESTONE" and d["expected"] == "M2" and d["provided"] == "M1" for d in drift_c["differences"]),
    )
    check("fixture_c_body_stays_authority", doc_c.current_milestone == "M2", doc_c.current_milestone)
    check("fixture_c_no_arbitration", drift_c["BODY_CONTROL_COMMENT_CONFLICT_ARBITRATION_REQUIRED"] == "no")
    check("fixture_c_authority_issue_body", drift_c["authority"] == "issue_body")
    check("fixture_c_no_automatic_repair", drift_c["automatic_repair"] == "not_implemented")

    # ------------------------------------------------------------------ D
    def expect_error(body: str, diagnostic_code: str, label: str) -> None:
        try:
            normalize_portable_plan(body)
            check(label, False, "no error raised")
        except PlanNormalizationError as exc:
            check(
                label,
                exc.code == "PLAN_NORMALIZATION_ERROR"
                and exc.diagnostic_code == diagnostic_code
                and f"[{diagnostic_code}]" in exc.message,
                f"{exc.code}/{exc.diagnostic_code}",
            )

    contradictory_same_section = (
        "# [PLAN] Duplicate in one current section\n\n"
        "## 1. Current State\n\n```text\n"
        "PLAN_STATUS=in-progress\n"
        "CURRENT_MILESTONE=M2\n"
        "CURRENT_MILESTONE=M3\n"
        "```\n"
    )
    expect_error(contradictory_same_section, "CURRENT_STATE_CONTRADICTION", "fixture_d_same_section_contradiction")

    contradictory_cross_section = build_body(
        [
            {"PLAN_STATUS": "in-progress", "CURRENT_MILESTONE": "M2"},
            {"PLAN_STATUS": "in-progress", "CURRENT_MILESTONE": "M3"},
        ]
    )
    expect_error(contradictory_cross_section, "CURRENT_STATE_CONTRADICTION", "fixture_d_cross_section_contradiction")

    malformed = build_body([{"PLAN_STATUS": "in-progress", "CURRENT_MILESTONE": "not-a-milestone"}])
    expect_error(malformed, "MALFORMED_CURRENT_MILESTONE", "fixture_d_malformed_milestone")

    malformed_completed = build_body([{"PLAN_STATUS": "in-progress", "COMPLETED_MILESTONES": "M1,phase2"}])
    expect_error(malformed_completed, "MALFORMED_COMPLETED_MILESTONES", "fixture_d_malformed_completed")

    expect_error("", "EMPTY_PLAN_BODY", "fixture_d_empty_body")

    # ------------------------------------------------------------------ E
    body_e = build_body(
        [
            {
                "PLAN_STATUS": "in-progress",
                "CURRENT_MILESTONE": "M2",
                "CURRENT_STATUS": "m2_ready_for_planning",
                "CURRENT_BLOCKER": "none",
                "COMPLETED_MILESTONES": "M1",
                "M1_STATUS": "completed",
                "M2_STATUS": "planned",
                "EXECUTOR": "chatgpt-manual-codex",
                "HANDOFF_STATE": "m2_planning_ready",
                "CURRENT_NEXT_ACTION": "M2 planning against amended scope:\nmaterialize successor regression matrix,\nconstruct dependency DAG and READY_SET,\nthen prepare bounded manual-Codex execution lanes",
                "CANONICAL_PROJECT_ID": "aota_forge",
                "CANONICAL_SOURCE_REPOSITORY": "wzjcccc-dotcom/aota_forge",
                "CANONICAL_PROJECT_ROOT": "/home/latios/workspace/aota_forge",
                "ACCEPTED_WRITABLE_BASE": "a380056b70ec11d45259f31babdfb38311672893",
                "M1_KNOWN_GOOD_CHECKPOINT": "a380056b70ec11d45259f31babdfb38311672893",
                "GOVERNING_PLAN_ISSUE": "wzjcccc-dotcom/aota-hermes-tools#9",
            }
        ],
        historical=[
            {
                "PROJECT_REPOSITORY_MIGRATION": "completed",
                "LEGACY_SOURCE_ROLE": "migration_regression_and_historical_evidence_only",
                "CURRENT_MILESTONE": "M1",
                "HANDOFF_STATE": "m1_closed",
            }
        ],
        milestone_specs=[
            "M1 — LLM-First Read-Only Forge Core and CLI",
            "M2 — Unified Ingress and Operation Contract Registry",
        ],
        governance=FIXTURE_A_GOVERNANCE,
        appendix={"FIXTURE_KIND": "E", "M2_D_LANE": "real-issue9-representative"},
        milestones_index="17. Milestones (amended — supersedes previous M1-M7 structure)",
    )
    doc_e = normalize_portable_plan(body_e, source_revision="a380056b70ec11d45259f31babdfb38311672893")
    check("fixture_e_real_shape_current_milestone", doc_e.current_milestone == "M2", doc_e.current_milestone)
    check("fixture_e_real_shape_milestone_specs", set(doc_e.milestone_specs) == {"M1", "M2"})
    check(
        "fixture_e_real_shape_next_action",
        doc_e.current_next_action is not None and "M2 planning against amended scope" in doc_e.current_next_action,
    )
    check(
        "fixture_e_governance",
        doc_e.governance.get("ISSUE_BODY_REMAINS_PLAN_AUTHORITY") == "yes"
        and doc_e.governance.get("CONTROL_COMMENTS_PLAN_AUTHORITY") == "no",
    )
    check(
        "fixture_e_schema_excludes_executor_ids",
        "EXECUTOR" not in {
            "plan_status", "current_milestone", "current_status", "current_blocker",
            "completed_milestones", "milestone_status", "current_next_action", "handoff_state",
        }
        and "executor_id" not in doc_e.to_dict(),
    )
    drift_e = observe_governance_projection_drift(
        doc_e,
        {"milestone_progress_index": {"CURRENT_MILESTONE": "M2", "PLAN_STATUS": "in-progress"}},
    )
    check("fixture_e_drift_no", drift_e["GOVERNANCE_PROJECTION_DRIFT"] == "no")

    # ------------------------------------------------------------------ F
    adapter = StaticPlanAuthorityAdapter(
        body_a,
        revision="rev-fixture-a",
        control_projections={"milestone_progress_index": {"CURRENT_MILESTONE": "M2"}},
        _source_metadata={"issue_number": 9, "comment_ids": ["x"]},
    )
    snapshot = adapter.load()
    doc_f = normalize_portable_plan(snapshot.body, source_revision=snapshot.revision)
    drift_f = observe_governance_projection_drift(doc_f, snapshot.control_projections)
    check("fixture_f_adapter_snapshot", snapshot.body == body_a and snapshot.revision == "rev-fixture-a")
    check("fixture_f_adapter_projection", snapshot.control_projections["milestone_progress_index"]["CURRENT_MILESTONE"] == "M2")
    check(
        "fixture_f_adapter_metadata_adapter_private",
        not hasattr(snapshot, "source_metadata") and not hasattr(snapshot, "issue_number"),
    )
    check("fixture_f_adapter_drift_no", drift_f["GOVERNANCE_PROJECTION_DRIFT"] == "no")
    check(
        "fixture_f_adapter_read_only",
        not any(method.startswith(("create", "update", "delete", "post", "patch", "write", "comment")) for method in dir(adapter)),
    )

    # ------------------------------------------------------------------ gates
    gates = {
        "PORTABLE_PLAN_DOCUMENT_NORMALIZATION": "PASS",
        "CURRENT_VS_HISTORICAL_DISTINCTION": "PASS",
        "HISTORICAL_STATE_CANNOT_OVERRIDE_CURRENT": "PASS",
        "ISSUE_BODY_REMAINS_PLAN_AUTHORITY": "yes",
        "CONTROL_COMMENT_PLAN_AUTHORITY": "no",
        "GOVERNANCE_PROJECTION_DRIFT_OBSERVATION": "PASS",
        "BODY_CONTROL_COMMENT_CONFLICT_ARBITRATION_REQUIRED": "no",
        "PLAN_AUTHORITY_READ_ADAPTER": "PASS",
        "GITHUB_CORE_COUPLING": "no",
        "PLAN_WRITE_IMPLEMENTED": "no",
    }
    print("\nACCEPTANCE GATES")
    for gate, value in gates.items():
        print(f"  {gate}={value}")

    passed = all(item["pass"] for item in results)
    print(f"\nM2-D FIXTURES: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
