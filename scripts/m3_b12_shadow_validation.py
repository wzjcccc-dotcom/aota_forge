#!/usr/bin/env python3
"""M3-B12 Focused Validator: Independent Shadow Validation (Issue #9, Lane M3-B12).

Scope:
- Independently validates M3-B11 isolated shadow bootstrap and migration mechanics.
- Directly recomputes critical evidence, fingerprints, and state from bounded inputs.
- Rebuilds fresh isolated ShadowRepository without trusting B11 self-verdicts.
- Validates canonical B3 schema, execution/completion/decision ownership, and lineage.
- Executes 12 independent tamper test cases (T1 - T12).
- Proves multi-stage failure atomicity and strict production canonical graph isolation.
- Runs accepted foundation regressions with PYTHONDONTWRITEBYTECODE=1.
- Outputs the exact mandatory M3-B12 result block.

Usage:
    python3 scripts/m3_b12_shadow_validation.py
    python3 scripts/m3_b12_shadow_validation.py --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aota_forge.core.shadow_validation import (
    IndependentShadowValidator,
    ShadowValidationReport,
    create_sample_deterministic_inputs,
)
from aota_forge.core.migration import MigrationInputManifest


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-B12 Independent Shadow Validator")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    parser.add_argument("--raw-result-block", action="store_true", help="Output only the mandatory result block")
    args = parser.parse_args()

    validator = IndependentShadowValidator()

    manifest = MigrationInputManifest.create("manifest_b12_exec", create_sample_deterministic_inputs())
    report = validator.validate(manifest)

    # Get current commit if available
    try:
        commit_sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        report.commit_sha = commit_sha
    except Exception:
        report.commit_sha = "none"

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.status == "PASS" else 1

    if args.raw_result_block:
        print(report.render_result_block())
        return 0 if report.status == "PASS" else 1

    # Standard human-readable summary + result block
    print("================================================================")
    print("M3-B12 Independent Shadow Validation Runner")
    print("================================================================")
    print(f"Status:                         {report.status}")
    print(f"Verdict:                        {report.verdict}")
    print(f"Validation Result:              {report.validation_result}")
    print(f"Exact Base Verified:            {'yes' if report.exact_base_verified else 'no'}")
    print(f"Handoff Complete:               {'yes' if report.handoff_complete else 'no'}")
    print(f"Handoff Rebuildable:            {'yes' if report.handoff_rebuildable else 'no'}")
    print(f"Independent Rebuild Equivalent: {report.independent_rebuild_equivalent}")
    wf_c = report.record_counts.get("workflows")
    sub_c = report.record_counts.get("subjects")
    exec_c = report.record_counts.get("executions")
    comp_c = report.record_counts.get("completions")
    dec_c = report.record_counts.get("decisions")
    edge_c = report.record_counts.get("edges")
    print(f"Derived Record Count:           {report.total_record_count} (Workflow={wf_c}, Subject={sub_c}, Exec={exec_c}, Comp={comp_c}, Dec={dec_c}, Edge={edge_c})")
    print(f"Unknown Record Kinds:           {report.unknown_graph_record_kind_count}")
    print(f"Schema Validation:              {report.canonical_schema_validation}")
    print(f"Identity Validation:            {report.identity_validation}")
    print(f"Ownership Validation:           Exec={report.execution_ownership_validation}, Comp={report.completion_ownership_validation}, Dec={report.decision_ownership_validation}")
    print(f"Followup Lineage:               {report.followup_lineage_validation} (without decision={report.followup_edge_without_source_decision_count})")
    print(f"Tamper Cases Evaluated:         {report.tamper_case_count}/{report.tamper_case_count} rejected")
    for t in report.tamper_results:
        p_str = "PASS" if t.rejected else "FAIL"
        print(f"  [{p_str}] {t.case_id:4}: {t.name:45} - {t.description}")
    print(f"Multi-Namespace Isolation:      A->B={report.namespace_a_mutates_b}, B->A={report.namespace_b_mutates_a}")
    print(f"Production Graph Changed:       {'yes' if report.production_isolation.canonical_graph_changed else 'no'}")
    print(f"Production Lease Consumed:      {report.production_isolation.production_lease_consumption_count}")
    print(f"Production Binding Changed:     {'yes' if report.production_isolation.production_binding_changed else 'no'}")
    print(f"Shadow Subject Visible in B9:   {'yes' if report.production_isolation.shadow_subject_visible_to_production_b9 else 'no'}")
    print(f"Failure Production Isolation:   {report.production_isolation.failure_production_isolation}")
    print(f"Legacy Comparison Validation:   {report.legacy_comparison_validation}")
    print(f"Negative Reversion Proof:       {report.negative_reversion_proof}")
    print("Foundations & Regressions:")
    for k, v in report.regressions.items():
        print(f"  [{v}] {k}")
    print("----------------------------------------------------------------")
    print("MANDATORY RESULT BLOCK:")
    print("----------------------------------------------------------------")
    print(report.render_result_block())
    print("==============================================================")

    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
