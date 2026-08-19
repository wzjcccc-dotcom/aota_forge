#!/usr/bin/env python3
"""Deterministic M4-1 contract-plan guard.

This guard validates planning evidence only.  It deliberately does not import
or activate a write-capable Core path.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


EXPECTED_BASE = "5a665cee791692b3ccad0ded331cb2d771fc7362"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy/evidence/issues/9/m4-1"
FILES = {
    "gap": EVIDENCE / "m4-1-current-contract-gap-map.json",
    "intent": EVIDENCE / "m4-1-mutation-intent-contract.json",
    "descriptor": EVIDENCE / "m4-1-write-operation-descriptor-contract.json",
    "result": EVIDENCE / "m4-1-canonical-mutation-result-contract.json",
    "regression": EVIDENCE / "m4-1-regression-handoff-map.json",
    "downstream": EVIDENCE / "m4-1-downstream-handoff.json",
}
ALLOWED_PREFIXES = ("deploy/evidence/issues/9/m4-1/", "scripts/m4_1_contract_plan_guard.py")
REQUIRED_COMPONENTS = {
    "OperationContractDescriptor",
    "HandlerRegistry",
    "Unified Ingress",
    "OperationContext",
    "canonical result envelope",
    "canonical errors",
    "idempotency metadata",
    "authority metadata",
    "Capability Lease",
    "Subject revision/CAS",
    "Portable Plan read model",
    "Portable Plan Authority Adapter",
    "CLI schema projection",
    "Hermes schema projection",
}
REQUIRED_FAILURE_CLASSES = {"DRIFT-1", "RC2-1", "B014-F", "B011", "B013", "B014", "RECOVERY-1", "BIND-1", "WCTX-1"}
EFFECTS = {"NO_EFFECT", "APPLIED_VERIFIED", "REPLAYED_VERIFIED", "BLOCKED", "CONFLICT", "NEEDS_SEMANTIC_CHOICE", "OUTCOME_UNKNOWN", "FAILED_NO_EFFECT"}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"artifact root is not an object: {path}")
    return value


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def common_violations(artifacts: dict[str, dict]) -> list[str]:
    failures: list[str] = []
    for name, artifact in artifacts.items():
        if artifact.get("project_id") != "aota_forge":
            failures.append(f"{name}: project_id")
        if artifact.get("governing_plan_issue") != "wzjcccc-dotcom/aota-hermes-tools#9":
            failures.append(f"{name}: governing_plan_issue")
        if artifact.get("milestone") != "M4":
            failures.append(f"{name}: milestone")
        if artifact.get("work_item") != "M4-1-P0":
            failures.append(f"{name}: work_item")
        if artifact.get("expected_base_sha") != EXPECTED_BASE:
            failures.append(f"{name}: expected_base_sha")
        if artifact.get("production_behavior_claimed") is not False:
            failures.append(f"{name}: production_behavior_claimed")
    return failures


def boundary_violations(artifacts: dict[str, dict]) -> list[str]:
    failures: list[str] = []
    for key, artifact in artifacts.items():
        text = json.dumps(artifact, sort_keys=True)
        if "github_issue_number" in text or "github_comment_id" in text or "github_etag" in text or "github_api_payload" in text:
            failures.append(f"{key}: platform-specific Core field")

    gap = artifacts["gap"].get("boundary", {})
    descriptor_artifact = artifacts["descriptor"]
    result_artifact = artifacts["result"]
    descriptor = descriptor_artifact.get("implementation_boundary", {})
    result = result_artifact.get("implementation_boundary", {})
    downstream = artifacts["downstream"].get("implementation_boundary", {})
    expected_no = {
        "M4_1_SOURCE_IMPLEMENTATION_AUTHORIZED": [gap, descriptor, result, downstream],
        "M4_1_SOURCE_MUTATION_ALLOWED": [gap, downstream],
        "M4_1_DURABLE_JOURNAL_IMPLEMENTATION": [gap, descriptor, result, downstream],
        "M4_1_EXTERNAL_AUTHORITY_WRITE": [gap, descriptor, result, downstream],
        "M4_1_CAPABILITY_LEASE_ISSUER_IMPLEMENTED": [gap, descriptor, downstream],
        "M4_1_SEMANTIC_AUTHORIZATION_ENGINE_IMPLEMENTED": [gap, descriptor],
        "M4_3_WRITE_INGRESS_IMPLEMENTED": [gap, descriptor, downstream],
        "M4_4_TRANSITION_IMPLEMENTATION": [descriptor],
        "M4_4_TRANSITIONS_IMPLEMENTED": [gap],
    }
    for key, sources in expected_no.items():
        for source in sources:
            if source.get(key) != "no":
                failures.append(f"boundary: {key}")
    descriptor_platform = descriptor_artifact.get("platform_neutrality", {})
    result_platform = result_artifact.get("platform_neutrality", {})
    if gap.get("GITHUB_IS_CORE_ONTOLOGY") != "no" or descriptor_platform.get("GITHUB_IS_CORE_ONTOLOGY") != "no" or result_platform.get("GITHUB_IS_CORE_ONTOLOGY") != "no":
        failures.append("boundary: GITHUB_IS_CORE_ONTOLOGY")
    if gap.get("GITHUB_API_IS_CORE_CONTRACT") != "no" or descriptor_platform.get("GITHUB_API_IS_CORE_CONTRACT") != "no" or result_platform.get("GITHUB_API_IS_CORE_CONTRACT") != "no":
        failures.append("boundary: GITHUB_API_IS_CORE_CONTRACT")
    if gap.get("GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID") != "no" or descriptor_platform.get("GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID") != "no" or result_platform.get("GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID") != "no":
        failures.append("boundary: GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID")
    if artifacts["downstream"].get("M4_1_PASS_AUTOMATICALLY_AUTHORIZES_M4_3") != "no":
        failures.append("boundary: M4_1_PASS_AUTOMATICALLY_AUTHORIZES_M4_3")
    return failures


def contract_violations(artifacts: dict[str, dict]) -> list[str]:
    failures: list[str] = []
    intent = artifacts["intent"]
    if intent.get("MUTATION_INTENT_IS_AUTHORITY") != "no":
        failures.append("intent: authority")
    if intent.get("MUTATION_INTENT_IS_SEMANTIC_DECISION_MAKER") != "no":
        failures.append("intent: semantic decision maker")
    model = intent.get("model_boundary", {})
    for key in ("MODEL_MAY_SELF_ASSERT_TRUSTED_PRINCIPAL", "MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE", "MODEL_MAY_SELF_ASSERT_INTERNAL_AUTHORITY", "MODEL_INTERNAL_IDS_NORMAL_INPUT", "MODEL_RAW_AUTHORITY_REVISION_NORMAL_INPUT"):
        if model.get(key) != "no":
            failures.append(f"intent: {key}")
    intent_fields = set(intent.get("canonical_semantic_fields", {}))
    for field in ("operation", "semantic_inputs", "logical_target", "mutation_scope", "idempotency_key", "intent_fingerprint", "correlation_id"):
        if field not in intent_fields or intent["canonical_semantic_fields"][field].get("required") is not True:
            failures.append(f"intent: missing field {field}")
    fingerprint = intent.get("intent_fingerprint", {})
    if fingerprint.get("INTENT_FINGERPRINT_DETERMINISTIC") != "yes":
        failures.append("intent: nondeterministic fingerprint")
    if fingerprint.get("INTENT_FINGERPRINT_EXCLUDES_RUNTIME_CALLABLE_IDENTITY") != "yes":
        failures.append("intent: callable identity included")
    if "runtime Python callable identity" not in fingerprint.get("excludes", []):
        failures.append("intent: callable exclusion missing")

    descriptor = artifacts["descriptor"]
    if descriptor.get("SINGLE_OPERATION_CONTRACT_DESCRIPTOR_MODEL") != "yes":
        failures.append("descriptor: second model")
    if descriptor.get("READ_AND_WRITE_OPERATIONS_SHARE_REGISTRY") != "yes":
        failures.append("descriptor: separate registry")
    authority = descriptor.get("authority_boundary", {})
    for key in ("DESCRIPTOR_IS_AUTHORITY", "DESCRIPTOR_MINTS_CAPABILITY_LEASE", "DESCRIPTOR_SELECTS_SUBJECT", "DESCRIPTOR_SELECTS_PROJECT", "DESCRIPTOR_ACCEPTS_MILESTONE"):
        if authority.get(key) != "no":
            failures.append(f"descriptor: {key}")
    required_semantics = set(descriptor.get("required_descriptor_semantics", {}))
    for field in ("semantic_operation_name", "protocol_version", "declared_semantic_inputs", "required_context", "optional_context", "read_write_classification", "mutation_scope", "required_authority_class", "approval_requirement", "materialized_decision_requirement", "valid_predecessor_semantics", "valid_successor_semantics", "subject_revision_precondition_requirement", "external_authority_precondition_requirement", "idempotency_semantics", "result_contract", "machine_readable_errors"):
        if field not in required_semantics:
            failures.append(f"descriptor: missing semantic {field}")
    hashing = descriptor.get("contract_hash", {})
    if hashing.get("CONTRACT_HASH_DETERMINISTIC") != "yes":
        failures.append("descriptor: nondeterministic hash")
    if hashing.get("CONTRACT_HASH_INCLUDES_MUTATION_SEMANTICS") != "yes":
        failures.append("descriptor: mutation semantics absent from hash")
    if hashing.get("CONTRACT_HASH_EXCLUDES_RUNTIME_AUTHORITY_VALUES") != "yes":
        failures.append("descriptor: runtime authority in hash")
    if "mutation scope" not in hashing.get("includes", []):
        failures.append("descriptor: mutation scope absent from hash")

    result = artifacts["result"]
    effects = result.get("mutation_effect_states", {})
    for effect in EFFECTS:
        if effect not in effects:
            failures.append(f"result: missing effect {effect}")
    if result.get("effect_status_separation", {}).get("authoritative_effect_confirmed_is_explicit") != "yes":
        failures.append("result: effect confirmation missing")
    if result.get("error_choice_separation", {}).get("distinct") != "yes":
        failures.append("result: error/choice collapse")
    safety = result.get("B014_F_RC2_1_safety", {})
    for key in ("SILENT_MATERIALIZATION_FAILURE_ALLOWED", "AUTHORITATIVE_EFFECT_UNKNOWN_MAY_REPORT_VERIFIED_SUCCESS", "LOCAL_HANDLER_SUCCESS_EQUALS_AUTHORITATIVE_EFFECT_SUCCESS"):
        if safety.get(key) != "no":
            failures.append(f"result: unsafe {key}")
    if safety.get("RC2_1_RESULT_CONTRACT_READY") != "yes" or safety.get("B014_F_RESULT_CONTRACT_READY") != "yes":
        failures.append("result: RC2-1/B014-F readiness")
    if effects.get("APPLIED_VERIFIED", {}).get("authoritative_effect_confirmed") != "yes" or effects.get("REPLAYED_VERIFIED", {}).get("authoritative_effect_confirmed") != "yes":
        failures.append("result: verified confirmation")
    if effects.get("OUTCOME_UNKNOWN", {}).get("authoritative_effect_confirmed") != "unknown":
        failures.append("result: unknown confirmation")

    regression = artifacts["regression"]
    entries = regression.get("entries", [])
    if {entry.get("failure_class") for entry in entries} != REQUIRED_FAILURE_CLASSES:
        failures.append("regression: required classes")
    if regression.get("M4_1_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT") != 0 or regression.get("current_behavioral_closure_claimed") != "no":
        failures.append("regression: closure claim")
    for entry in entries:
        if entry.get("current_behavioral_closure_claimed") != "no":
            failures.append(f"regression: closure {entry.get('failure_class')}")

    downstream = artifacts["downstream"]
    for key in ("M4_2", "M4_4", "M4_3"):
        if not isinstance(downstream.get(key), dict):
            failures.append(f"downstream: {key}")
    if downstream.get("dependency_dag_preserved", {}).get("unchanged") != "yes":
        failures.append("downstream: DAG")
    return failures


def validate(artifacts: dict[str, dict]) -> list[str]:
    return common_violations(artifacts) + contract_violations(artifacts) + boundary_violations(artifacts)


def negative_cases(artifacts: dict[str, dict]) -> tuple[int, int, int]:
    cases: list[tuple[str, object]] = [
        ("NEG-M41-01", lambda a: a["intent"].__setitem__("MUTATION_INTENT_IS_AUTHORITY", "yes")),
        ("NEG-M41-02", lambda a: a["intent"]["model_boundary"].__setitem__("MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE", "yes")),
        ("NEG-M41-03", lambda a: (a["intent"]["intent_fingerprint"]["excludes"].remove("runtime Python callable identity"), a["intent"]["intent_fingerprint"].__setitem__("INTENT_FINGERPRINT_EXCLUDES_RUNTIME_CALLABLE_IDENTITY", "no"))),
        ("NEG-M41-04", lambda a: a["descriptor"]["authority_boundary"].__setitem__("DESCRIPTOR_IS_AUTHORITY", "yes")),
        ("NEG-M41-05", lambda a: a["descriptor"]["contract_hash"]["includes"].remove("mutation scope")),
        ("NEG-M41-06", lambda a: a["result"]["B014_F_RC2_1_safety"].__setitem__("AUTHORITATIVE_EFFECT_UNKNOWN_MAY_REPORT_VERIFIED_SUCCESS", "yes")),
        ("NEG-M41-07", lambda a: a["result"]["B014_F_RC2_1_safety"].__setitem__("SILENT_MATERIALIZATION_FAILURE_ALLOWED", "yes")),
        ("NEG-M41-08", lambda a: a["result"]["error_choice_separation"].__setitem__("distinct", "no")),
        ("NEG-M41-09", lambda a: a["result"]["platform_neutrality"].__setitem__("GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID", "yes")),
        ("NEG-M41-10", lambda a: a["gap"]["boundary"].__setitem__("M4_1_CAPABILITY_LEASE_ISSUER_IMPLEMENTED", "yes")),
        ("NEG-M41-11", lambda a: a["result"]["implementation_boundary"].__setitem__("M4_1_DURABLE_JOURNAL_IMPLEMENTATION", "yes")),
        ("NEG-M41-12", lambda a: a["downstream"]["implementation_boundary"].__setitem__("M4_1_SOURCE_IMPLEMENTATION_AUTHORIZED", "yes")),
    ]
    rejected = 0
    unexpected = 0
    for label, mutation in cases:
        candidate = deepcopy(artifacts)
        mutation(candidate)
        if validate(candidate):
            rejected += 1
        else:
            unexpected += 1
            print(f"UNEXPECTED_ACCEPT={label}")
    return len(cases), rejected, unexpected


def path_violations() -> list[str]:
    failures: list[str] = []
    if git("merge-base", EXPECTED_BASE, "HEAD") != EXPECTED_BASE:
        failures.append("accepted base is not the exact source ancestor")
    changed = set(filter(None, git("diff", "--name-only", EXPECTED_BASE).splitlines()))
    changed.update(filter(None, git("diff", "--cached", "--name-only", EXPECTED_BASE).splitlines()))
    status = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout
    for line in status.splitlines():
        if len(line) >= 4:
            changed.add(line[3:])
    for path in sorted(changed):
        if not any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            failures.append(f"disallowed changed path: {path}")
    if any(path.startswith("aota_forge/") for path in changed):
        failures.append("functional Core source changed")
    if any(path.startswith("deploy/evidence/issues/9/m4-0/") or path.startswith("deploy/evidence/issues/9/m4-0-review/") or path.startswith("deploy/evidence/issues/9/m4-0-r2-review/") or path.startswith("deploy/evidence/issues/9/m4-0-r2-guard-rereview/") for path in changed):
        failures.append("M4-0 evidence changed")
    if "deploy/evidence/issues/9/m2-successor-regression-matrix.json" in changed:
        failures.append("M2 regression matrix changed")
    if any(path.startswith("deploy/evidence/issues/9/m3-") for path in changed):
        failures.append("M3 accepted evidence changed")
    return failures


def main() -> int:
    try:
        artifacts = {name: load_json(path) for name, path in FILES.items()}
        missing = [str(path) for path in FILES.values() if not path.is_file()]
        failures = [f"missing artifact: {path}" for path in missing]
        if not failures:
            failures.extend(validate(artifacts))
            failures.extend(path_violations())
        count, rejected, unexpected = negative_cases(artifacts) if not failures else (12, 0, 12)
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"M4_1_CONTRACT_PLAN_GUARD=FAIL")
        print(f"GUARD_ERROR={type(exc).__name__}: {exc}")
        return 1
    print(f"GAP_MAP_ENTRY_COUNT={len(artifacts['gap'].get('entries', []))}")
    print(f"NEGATIVE_CASE_COUNT={count}")
    print(f"NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={unexpected}")
    if failures:
        print("M4_1_CONTRACT_PLAN_GUARD=FAIL")
        for failure in failures:
            print(f"FAILURE={failure}")
        return 1
    if count != 12 or rejected != 12 or unexpected != 0:
        print("M4_1_CONTRACT_PLAN_GUARD=FAIL")
        return 1
    print("M4_1_CONTRACT_PLAN_GUARD=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
