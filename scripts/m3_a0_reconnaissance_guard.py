#!/usr/bin/env python3
"""M3-A0 reconnaissance guard (Issue #9, lane M3-A0).

Validates deploy/evidence/issues/9/m3-a/a0-input-map.json against the
M3-A0 lane contract at the exact M2 known-good base:

- artifact parses as JSON
- exact base SHA recorded
- all five required classification categories supported
- every migration input carries exactly one primary classification
- Git history / Issue Event Log / control comments are never classified
  as graph authority (each FORBIDDEN_AUTHORITY_SOURCE with evidence utility)
- current pointers are never classified as future authority
- no graph implementation/storage location is declared authoritative
- regression class count remains 16 with unchanged dispositions (cross-check
  against m2-successor-regression-matrix.json)
- no unsupported success claims added
- NF2 and I9-B008 are represented as design inputs, not silently fixed
  (static seam markers in the source must still be present)
- M3-A authoritative graph writes remain denied

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a0_reconnaissance_guard.py            # human report
    python3 scripts/m3_a0_reconnaissance_guard.py --json     # machine summary
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m3-a" / "a0-input-map.json"
MATRIX_PATH = REPO_ROOT / "deploy" / "evidence" / "issues" / "9" / "m2-successor-regression-matrix.json"
AOTA_FORGE_PACKAGE = REPO_ROOT / "aota_forge"

EXPECTED_BASE_SHA = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

REQUIRED_CLASSIFICATION_CATEGORIES = frozenset(
    {
        "AUTHORITATIVE_BOOTSTRAP_INPUT_CANDIDATE",
        "MIGRATION_EVIDENCE_ONLY",
        "PROJECTION_ONLY",
        "HISTORICAL_ONLY",
        "FORBIDDEN_AUTHORITY_SOURCE",
    }
)

# Future classifications a current pointer may carry: never an authority class.
POINTER_ALLOWED_FUTURE_CLASSES = frozenset(
    {"PROJECTION_ONLY", "MIGRATION_EVIDENCE_ONLY", "HISTORICAL_ONLY"}
)

FORBIDDEN_SOURCES = ("Git history", "Issue Event Log", "control comments")

ALLOWED_PHASE_OWNERS = frozenset({"M3-A", "M3-B", "M4+", "not_M3"})

EXPECTED_REGRESSION_CLASS_COUNT = 16

# Static seam markers that prove the two carried-forward findings are still
# represented as design inputs (not silently fixed in this lane).
I9_B008_SEAM_MARKER = "data=payload.get(\"data\", {})"
I9_B008_SEAM_FILE = "core/ingress.py"
NF2_PRINCIPAL_MARKER = "principal: str"
NF2_PRINCIPAL_FILE = "core/context.py"
NF2_ADAPTER_SELF_ASSERTION_MARKER = "PRINCIPAL = \"hermes\""
NF2_ADAPTER_SELF_ASSERTION_FILE = "adapters/hermes/invoke.py"
READ_ONLY_GATE_MARKER = "operation is not read-only in M2"

# Bounded static scan: no graph storage implementation may exist in Core.
GRAPH_STORE_CLASS_RE = re.compile(
    r"class\s+\w*(?:Graph|Subject|Authority|Lease)\w*(?:Store|Repository|Engine|Broker)\w*\s*:"
)
GRAPH_WRITE_OPERATION_RE = re.compile(
    r'name\s*=\s*"(?:graph|subject|authority|lease)\.[a-z0-9_.-]+"'
)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def run_checks(artifact: dict, matrix: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. artifact parses (caller raises on parse failure) + top-level shape.
    for key in ("schema_version", "project_id", "milestone", "phase", "lane", "base_sha"):
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    results.append(_check(artifact.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(artifact.get("project_id") == "aota_forge", "project_id must be aota_forge"))
    results.append(_check(artifact.get("milestone") == "M3" and artifact.get("phase") == "M3-A", "milestone/phase must be M3 / M3-A"))
    results.append(_check(artifact.get("lane") == "M3-A0", "lane must be M3-A0"))

    # 2. exact base SHA recorded.
    recorded = artifact.get("base_sha")
    results.append(_check(
        recorded == EXPECTED_BASE_SHA,
        f"base_sha must be {EXPECTED_BASE_SHA} (recorded: {recorded!r})",
    ))

    # 3. all required classification categories supported.
    categories = set(artifact.get("classification_categories", []))
    results.append(_check(
        categories == REQUIRED_CLASSIFICATION_CATEGORIES,
        f"classification_categories must be exactly {sorted(REQUIRED_CLASSIFICATION_CATEGORIES)} (got {sorted(categories)})",
    ))

    # 4. every discovered migration input has exactly one primary classification.
    migration_inputs = artifact.get("migration_inputs", [])
    results.append(_check(isinstance(migration_inputs, list) and migration_inputs, "migration_inputs must be a non-empty list"))
    for item in migration_inputs:
        primary = item.get("primary_class")
        results.append(_check(
            isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"],
            f"migration input must carry a non-empty id: {item!r}",
        ))
        results.append(_check(
            primary in REQUIRED_CLASSIFICATION_CATEGORIES,
            f"migration input {item.get('id')}: primary_class {primary!r} not in classification categories",
        ))
    results.append(_check(
        len({item.get("id") for item in migration_inputs}) == len(migration_inputs),
        "migration input ids must be unique",
    ))

    # 5/6/7. Git history / Event Log / control comments never graph authority.
    by_source = {item.get("source"): item for item in migration_inputs if isinstance(item.get("source"), str)}
    for source in FORBIDDEN_SOURCES:
        entry = by_source.get(source)
        results.append(_check(
            entry is not None,
            f"migration input for forbidden source missing: {source}",
        ))
        if entry is None:
            continue
        results.append(_check(
            entry.get("primary_class") == "FORBIDDEN_AUTHORITY_SOURCE",
            f"'{source}' must be FORBIDDEN_AUTHORITY_SOURCE (got {entry.get('primary_class')!r})",
        ))
    forbidden_section = artifact.get("forbidden_authority_sources", [])
    results.append(_check(isinstance(forbidden_section, list), "forbidden_authority_sources must be a list"))
    for entry in forbidden_section:
        results.append(_check(
            entry.get("primary_class") == "FORBIDDEN_AUTHORITY_SOURCE",
            f"forbidden source {entry.get('source')!r}: primary_class must be FORBIDDEN_AUTHORITY_SOURCE",
        ))
        results.append(_check(
            isinstance(entry.get("evidence_utility"), str) and entry["evidence_utility"],
            f"forbidden source {entry.get('source')!r} must carry bounded evidence_utility",
        ))
    if len(forbidden_section) == 3:
        results.append(_check(
            {entry.get("source") for entry in forbidden_section} == set(FORBIDDEN_SOURCES),
            "forbidden_authority_sources must list exactly Git history, Issue Event Log, control comments",
        ))

    # 8. current pointers never classified as future authority.
    pointers = artifact.get("current_pointer_inventory", [])
    results.append(_check(isinstance(pointers, list) and pointers, "current_pointer_inventory must be a non-empty list"))
    pointer_fields = {"name", "source_location", "semantic_target", "producer", "consumer",
                      "durable", "reconstructable", "currently_treated_as_authority_in_legacy",
                      "future_m3_classification"}
    for item in pointers:
        missing = pointer_fields - set(item)
        results.append(_check(not missing, f"current pointer {item.get('name')!r} missing fields: {sorted(missing)}"))
        future = item.get("future_m3_classification")
        results.append(_check(
            future in POINTER_ALLOWED_FUTURE_CLASSES,
            f"current pointer {item.get('name')!r}: future_m3_classification {future!r} must not be an authority class",
        ))
    # The three discovered pointer families must each be represented:
    # manifest-declared current plan, Plan-document projection fields,
    # and legacy runtime pointers.
    pointer_names = " | ".join(str(item.get("name", "")).lower() for item in pointers)
    for family, marker in (("manifest-declared plan pointer", "active_plan_id"),
                           ("plan-document projection fields", "projection"),
                           ("legacy runtime pointers", "legacy")):
        results.append(_check(
            marker in pointer_names,
            f"current pointer inventory must cover the {family} seam (marker {marker!r})",
        ))

    # 9. no graph implementation/storage location declared authoritative.
    results.append(_check(
        isinstance(artifact.get("graph_storage_locations"), list) and artifact["graph_storage_locations"] == [],
        "graph_storage_locations must be an empty list (no graph storage declared)",
    ))
    for key in ("source_seams", "lifecycle_concepts"):
        for entry in artifact.get(key, []):
            name = entry.get("seam") or entry.get("concept") or entry.get("name") or "?"
            text = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            results.append(_check(
                "graph_storage" not in text.lower() or "authoritative" not in text.lower(),
                f"{key} entry declares authoritative graph storage: {name}",
            ))

    # 10. regression class count remains 16 with unchanged dispositions.
    matrix_classes = matrix.get("failure_classes", [])
    results.append(_check(
        len(matrix_classes) == EXPECTED_REGRESSION_CLASS_COUNT,
        f"canonical matrix failure class count must be {EXPECTED_REGRESSION_CLASS_COUNT} (got {len(matrix_classes)})",
    ))
    ownership = artifact.get("successor_regression_ownership", [])
    results.append(_check(isinstance(ownership, list), "successor_regression_ownership must be a list"))
    results.append(_check(
        len(ownership) == EXPECTED_REGRESSION_CLASS_COUNT,
        f"successor_regression_ownership must cover exactly {EXPECTED_REGRESSION_CLASS_COUNT} classes (got {len(ownership)})",
    ))
    matrix_by_class = {entry.get("failure_class"): entry for entry in matrix_classes}
    artifact_by_class = {entry.get("failure_class"): entry for entry in ownership}
    results.append(_check(
        set(matrix_by_class) == set(artifact_by_class),
        "successor_regression_ownership class set must equal the canonical matrix class set (no classes added/removed)",
    ))
    for failure_class, entry in artifact_by_class.items():
        expected_disposition = matrix_by_class[failure_class].get("expected_successor_disposition")
        results.append(_check(
            entry.get("current_disposition") == expected_disposition,
            f"{failure_class}: disposition must remain {expected_disposition} (got {entry.get('current_disposition')!r})",
        ))
        results.append(_check(
            entry.get("current_owner") == matrix_by_class[failure_class].get("successor_owner_milestone"),
            f"{failure_class}: current_owner must match matrix owner {matrix_by_class[failure_class].get('successor_owner_milestone')!r}",
        ))
        results.append(_check(
            entry.get("proposed_m3_phase_owner") in ALLOWED_PHASE_OWNERS,
            f"{failure_class}: proposed_m3_phase_owner must be one of {sorted(ALLOWED_PHASE_OWNERS)}",
        ))
        results.append(_check(
            entry.get("m3_relevance") in ("yes", "no"),
            f"{failure_class}: m3_relevance must be yes|no",
        ))

    # 11. no unsupported success claims added.
    unsupported: list[str] = []
    for failure_class, entry in artifact_by_class.items():
        disposition = entry.get("current_disposition")
        owner = entry.get("current_owner")
        if disposition not in ("ELIMINATED_BY_CONSTRUCTION", "DETERMINISTICALLY_RECONCILED", "NEEDS_SEMANTIC_CHOICE"):
            continue
        if owner and re.fullmatch(r"M[3-9]", str(owner)):
            unsupported.append(failure_class)
    results.append(_check(
        not unsupported,
        f"unsupported success claims on M3+ owned classes: {unsupported}",
    ))
    results.append(_check(
        artifact.get("report_metrics", {}).get("unsupported_success_claims") == 0,
        "report_metrics.unsupported_success_claims must be 0",
    ))
    results.append(_check(
        artifact.get("report_metrics", {}).get("regression_class_count") == EXPECTED_REGRESSION_CLASS_COUNT,
        "report_metrics.regression_class_count must be 16",
    ))
    results.append(_check(
        artifact.get("report_metrics", {}).get("regression_not_yet_implemented_count")
        == sum(1 for entry in matrix_classes if entry.get("expected_successor_disposition") == "NOT_YET_IMPLEMENTED"),
        "report_metrics.regression_not_yet_implemented_count must match the canonical matrix",
    ))

    # 12. NF2 and I9-B008 represented as design inputs, not silently fixed.
    nf2 = artifact.get("nf2_design_input", {})
    for key in ("NF2_CURRENT_MECHANICS", "NF2_AUTHORITY_GAP", "NF2_M3_A2_INPUT"):
        results.append(_check(
            key in nf2 and nf2[key],
            f"nf2_design_input missing field: {key}",
        ))
    b008 = artifact.get("i9_b008_design_input", {})
    for key in ("I9_B008_CURRENT_SEAM", "I9_B008_DESIGN_IMPACT", "I9_B008_M3B_PREREQUISITE"):
        results.append(_check(
            key in b008 and b008[key],
            f"i9_b008_design_input missing field: {key}",
        ))
    results.append(_check(
        b008.get("I9_B008_M3B_PREREQUISITE") == "yes",
        "I9_B008_M3B_PREREQUISITE must be yes (M3-B prerequisite)",
    ))
    for marker, rel_path in (
        (I9_B008_SEAM_MARKER, I9_B008_SEAM_FILE),
        (NF2_PRINCIPAL_MARKER, NF2_PRINCIPAL_FILE),
        (NF2_ADAPTER_SELF_ASSERTION_MARKER, NF2_ADAPTER_SELF_ASSERTION_FILE),
    ):
        source_file = AOTA_FORGE_PACKAGE / rel_path
        results.append(_check(
            source_file.is_file() and marker in source_file.read_text(encoding="utf-8"),
            f"design-input seam marker missing (would mean silent fix): {marker!r} in {rel_path}",
        ))

    # 13. M3-A authoritative graph writes remain denied.
    constraints = artifact.get("invariants", {}).get("m3_a0_constraints", {})
    results.append(_check(
        constraints.get("AUTHORITATIVE_GRAPH_WRITES_ALLOWED") == "no",
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED must be 'no'",
    ))
    results.append(_check(
        constraints.get("AUTHORITATIVE_STATE_MUTATION_ALLOWED") == "no",
        "AUTHORITATIVE_STATE_MUTATION_ALLOWED must be 'no'",
    ))
    results.append(_check(
        artifact.get("declared_scope", {}).get("source_mutation_scope") == "design_artifacts_only",
        "declared_scope.source_mutation_scope must be design_artifacts_only",
    ))

    # Static source checks: no graph write surface exists at the base.
    ingress_text = (AOTA_FORGE_PACKAGE / I9_B008_SEAM_FILE).read_text(encoding="utf-8")
    results.append(_check(
        READ_ONLY_GATE_MARKER in ingress_text,
        f"read-only ingress gate marker missing: {READ_ONLY_GATE_MARKER!r} in {I9_B008_SEAM_FILE}",
    ))
    store_hits: list[str] = []
    for py in sorted(AOTA_FORGE_PACKAGE.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for match in GRAPH_STORE_CLASS_RE.finditer(text):
            store_hits.append(f"{py.relative_to(REPO_ROOT)}:{match.group(0)}")
    results.append(_check(not store_hits, f"graph storage implementation class found in Core: {store_hits}"))
    catalog_text = (AOTA_FORGE_PACKAGE / "core" / "catalog.py").read_text(encoding="utf-8")
    results.append(_check(
        not GRAPH_WRITE_OPERATION_RE.search(catalog_text),
        "catalog declares a graph/subject/authority/lease write operation",
    ))

    # Open design questions structure.
    questions = artifact.get("open_design_questions", [])
    results.append(_check(isinstance(questions, list) and questions, "open_design_questions must be a non-empty list"))
    for question in questions:
        for key in ("QUESTION_ID", "QUESTION", "WHY_IT_MATTERS", "OWNER_LANE"):
            results.append(_check(
                key in question and question[key],
                f"open design question {question.get('QUESTION_ID', '?')} missing field: {key}",
            ))

    # Subject / authority inventories present.
    results.append(_check(
        isinstance(artifact.get("subject_identity_ambiguities"), list)
        and artifact["subject_identity_ambiguities"],
        "subject_identity_ambiguities must be a non-empty list",
    ))
    results.append(_check(
        isinstance(artifact.get("authority_candidates"), list)
        and artifact["authority_candidates"],
        "authority_candidates must be a non-empty list",
    ))
    results.append(_check(
        isinstance(artifact.get("source_seams"), list) and artifact["source_seams"],
        "source_seams must be a non-empty list",
    ))

    # report_metrics must match the actual inventory lengths (deterministic artifact).
    expected_metrics = {
        "current_pointer_inventory_count": len(pointers),
        "subject_identity_ambiguity_count": len(artifact.get("subject_identity_ambiguities", [])),
        "authority_candidate_count": len(artifact.get("authority_candidates", [])),
        "open_design_question_count": len(questions),
        "migration_input_count": len(migration_inputs),
    }
    for metric, expected in expected_metrics.items():
        results.append(_check(
            artifact.get("report_metrics", {}).get(metric) == expected,
            f"report_metrics.{metric} must be {expected} (got {artifact.get('report_metrics', {}).get(metric)!r})",
        ))

    return results


def summarize(artifact: dict, results: list[tuple[bool, str]]) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    metrics = artifact.get("report_metrics", {})
    return {
        "artifact": "m3-a0-input-map",
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "current_pointer_inventory_count": len(artifact.get("current_pointer_inventory", [])),
        "subject_identity_ambiguity_count": len(artifact.get("subject_identity_ambiguities", [])),
        "authority_candidate_count": len(artifact.get("authority_candidates", [])),
        "open_design_question_count": len(artifact.get("open_design_questions", [])),
        "migration_input_count": len(artifact.get("migration_inputs", [])),
        "regression_class_count": metrics.get("regression_class_count"),
        "regression_not_yet_implemented_count": metrics.get("regression_not_yet_implemented_count"),
        "unsupported_success_claims": metrics.get("unsupported_success_claims"),
    }


def main() -> int:
    try:
        artifact = _read_json(ARTIFACT_PATH)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        matrix = _read_json(MATRIX_PATH)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: canonical matrix unreadable/invalid: {exc}")
        return 1

    results = run_checks(artifact, matrix)
    summary = summarize(artifact, results)
    if "--json" in sys.argv:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

    print(f"artifact: {ARTIFACT_PATH.relative_to(REPO_ROOT)}")
    print(f"base SHA recorded: {artifact.get('base_sha')}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
