#!/usr/bin/env python3
"""Zero-LLM guard for the Issue #9 M4-3 planning package."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy/evidence/issues/9/m4-3-plan"
EXPECTED_BASE = "16826b551b62f4874bfc6ec734cb571cb3d9dc6d"
BRANCH = "aota/m4/m4-3-plan"
ALLOWED_PREFIX = "deploy/evidence/issues/9/m4-3-plan/"
ALLOWED_SCRIPT = "scripts/m4_3_plan_guard.py"

REQUIRED = {
    "m4-3-current-ingress-capability-audit.json",
    "m4-3-write-request-envelope-contract.json",
    "m4-3-operation-dispatch-contract.json",
    "m4-3-authorization-composition-contract.json",
    "m4-3-plan-init-ingress-path.json",
    "m4-3-retirement-ingress-path.json",
    "m4-3-result-error-projection-contract.json",
    "m4-3-cli-adapter-scope.json",
    "m4-3-source-ownership-partition.json",
    "m4-3-dependency-dag.json",
    "m4-3-regression-handoff-map.json",
    "m4-3-negative-scope-and-invariants.json",
    "m4-3-planning-acceptance-gate.json",
    "m4-3-m4-5-handoff.json",
}

META = {
    "project_id": "aota_forge",
    "repository": "wzjcccc-dotcom/aota_forge",
    "governing_plan_issue": "wzjcccc-dotcom/aota-hermes-tools#9",
    "milestone": "M4",
    "work_item": "M4-3-PLANNING",
    "expected_base_sha": EXPECTED_BASE,
    "planning_only": True,
    "production_behavior_claimed": False,
    "source_implementation_authorized": False,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_documents() -> dict[str, dict]:
    require(EVIDENCE.is_dir(), "M4-3 evidence directory is missing")
    actual = {path.name for path in EVIDENCE.glob("*.json")}
    require(actual == REQUIRED, f"required artifact set mismatch: {sorted(actual)}")
    documents: dict[str, dict] = {}
    for name in sorted(REQUIRED):
        with (EVIDENCE / name).open(encoding="utf-8") as stream:
            value = json.load(stream)
        require(isinstance(value, dict), f"artifact is not an object: {name}")
        documents[name] = value
    return documents


def assert_metadata(documents: dict[str, dict]) -> None:
    for name, document in documents.items():
        for key, expected in META.items():
            require(document.get(key) == expected, f"{name}: metadata {key} mismatch")


def assert_contracts(documents: dict[str, dict]) -> None:
    audit = documents["m4-3-current-ingress-capability-audit.json"]
    envelope = documents["m4-3-write-request-envelope-contract.json"]
    dispatch = documents["m4-3-operation-dispatch-contract.json"]
    authorization = documents["m4-3-authorization-composition-contract.json"]
    plan_init = documents["m4-3-plan-init-ingress-path.json"]
    retirement = documents["m4-3-retirement-ingress-path.json"]
    result = documents["m4-3-result-error-projection-contract.json"]
    cli = documents["m4-3-cli-adapter-scope.json"]
    ownership = documents["m4-3-source-ownership-partition.json"]
    dag = documents["m4-3-dependency-dag.json"]
    regressions = documents["m4-3-regression-handoff-map.json"]
    negative = documents["m4-3-negative-scope-and-invariants.json"]
    gate = documents["m4-3-planning-acceptance-gate.json"]
    handoff = documents["m4-3-m4-5-handoff.json"]

    require(audit["current_status"] == "m4_3_ready_for_planning", "current status")
    require(len(audit["current_capabilities"]) >= 7, "capability audit coverage")
    require(len(audit["exact_gaps"]) >= 7, "exact gap coverage")
    require("M4_3_EXECUTION_AUTHORIZED=no" in audit["invariants_to_preserve"], "execution gate")
    require("PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no" in audit["invariants_to_preserve"], "graph gate")

    require(envelope["envelope_type"] == "MutationIngressRequest", "envelope type")
    require(envelope["representation"] == "internal typed request; not a model-facing JSON payload", "envelope surface")
    payload_operations = {item["operation"] for item in envelope["operation_payloads"]}
    require(payload_operations == {"plan_init", "plan_retirement"}, "payload operation set")
    never_model = set(envelope["field_partition"]["never_model_asserted"])
    require({"trusted principal", "CapabilityLease", "contract hash", "Subject revision"} <= never_model, "trusted field partition")
    require("PlanInitRequest" in envelope["request_reuse_rule"] and "PlanRetirementRequest" in envelope["request_reuse_rule"], "request reuse")
    require(any("Reject a plain dict" in rule for rule in envelope["construction_rules"]), "typed request boundary")

    operations = {item["operation"] for item in dispatch["canonical_operations"]}
    require(operations == {"plan_init", "plan_retirement"}, "dispatch operation set")
    for item in dispatch["canonical_operations"]:
        require(item["read_write"] == "write", f"not a write descriptor: {item['operation']}")
        require(item["contract_hash_source"].endswith(".contract_hash()"), f"hard-coded contract authority: {item['operation']}")
        require("aota_forge/core/transitions.py:" in item["dispatch_target"], f"wrong transition target: {item['operation']}")
    require(dispatch["write_entry_point"]["symbol"] == "execute_mutation", "write entry point")
    require(dispatch["generic_mutation_api"] == "forbidden; the dispatch set is an explicit closed set of accepted lifecycle operations", "generic mutation ban")
    read_route = dispatch["write_entry_point"]["relationship_to_read_path"]
    require("execute" in read_route and "read-only" in read_route, "read route preservation")

    require(authorization["lease_issuance_variant"] == "A", "lease variant")
    require(authorization["issuer_semantic_decision_maker"] is False, "issuer semantic role")
    require(authorization["ingress_issuance_allowed"] is False, "ingress issuer role")
    required_binding = {
        "principal", "operation", "typed_target", "mutation_scope", "contract_hash",
        "intent_fingerprint", "subject_expected_revision", "external_authority_precondition",
        "authority_source_revision", "authority_observed_raw_digest", "candidate_raw_digest",
        "normalized_plan_digest",
    }
    require(required_binding <= set(authorization["exact_binding_fields"]), "exact binding fields")
    require("CapabilityLeaseIssuer.validate_lease" in " ".join(authorization["composition_sequence"]), "lease validation")
    require(authorization["evidence_rule"]["forged_or_fixture_lease_positive_allowed"] is False, "real lease positive rule")
    require(authorization["revision_domains"]["normalized_plan_digest_can_authorize_external_CAS"] is False, "revision domain separation")

    require(plan_init["operation"] == "plan_init", "PLAN_INIT operation")
    require(plan_init["request_type"] == "PlanInitRequest", "PLAN_INIT request")
    require(plan_init["semantic_choice_boundary"]["core_selects_project_under_ambiguity"] is False, "PLAN_INIT project selection")
    require(plan_init["semantic_choice_boundary"]["relationship_evidence_is_binding"] is False, "PLAN_INIT binding distinction")
    require("plan_init(store, request)" in " ".join(plan_init["dispatch_steps"]), "PLAN_INIT transition dispatch")
    require(plan_init["success_and_failure_rules"]["missing_lease"].startswith("blocked"), "PLAN_INIT missing lease")

    require(retirement["operation"] == "plan_retirement", "retirement operation")
    require(retirement["request_type"] == "PlanRetirementRequest", "retirement request")
    require(retirement["two_stage_boundary"]["candidate_discovery_is_write"] is False, "candidate discovery mutation")
    require(retirement["two_stage_boundary"]["ingress_selects_candidate"] is False, "retirement selection")
    forbidden_selection = set(retirement["selection_rules"]["forbidden_selection"])
    require({"newest", "latest", "first", "similarity", "stale current pointer"} <= forbidden_selection, "heuristic retirement selection")
    require(retirement["retirement_rules"]["abandoned"]["resulting_state"] == "cancelled", "abandoned state")
    require(retirement["retirement_rules"]["superseded"]["successor_required"] is True, "successor requirement")
    require(retirement["retirement_rules"]["superseded"]["successor_must_not_equal_target"] is True, "self successor")
    require(retirement["retirement_rules"]["implicit_resurrection"] is False, "resurrection")

    effects = {item["effect"] for item in result["effect_projection"]}
    require(effects == {"APPLIED_VERIFIED", "REPLAYED_VERIFIED", "NO_EFFECT", "NEEDS_SEMANTIC_CHOICE", "BLOCKED", "CONFLICT", "OUTCOME_UNKNOWN", "FAILED_NO_EFFECT"}, "effect coverage")
    unknown = next(item for item in result["effect_projection"] if item["effect"] == "OUTCOME_UNKNOWN")
    require(unknown["ok"] is False and unknown["confirmation"] == "unknown", "unknown projection")
    choice = next(item for item in result["effect_projection"] if item["effect"] == "NEEDS_SEMANTIC_CHOICE")
    require(choice["ok"] is True and choice["confirmation"] == "no", "choice projection")
    require("lifecycle_code" in " ".join(result["projection_rules"]), "lifecycle code projection")

    require(cli["current_cli"]["all_current_commands_are_read_only"] is True, "current CLI write state")
    require(cli["write_command_surface"]["currently_exposed"] == [], "premature CLI writes")
    future_commands = {item["command"] for item in cli["write_command_surface"]["future_allowed_commands"]}
    require(future_commands == {"aota plan init", "aota plan retire"}, "future CLI command set")
    for item in cli["write_command_surface"]["future_allowed_commands"]:
        require(item["route"] == "execute_mutation", f"CLI bypass: {item['command']}")
    require(cli["m4_3_scope"]["cli_source_changes_in_this_plan"] is False, "CLI source scope")
    require(cli["m4_3_scope"]["cli_is_exclusive_entrypoint"] is False, "CLI exclusivity")

    exclusive = set(ownership["M4_3_EXCLUSIVE_WRITE_PATHS"])
    shared = set(ownership["M4_3_SHARED_READ_ONLY_PATHS"])
    integration = set(ownership["M4_3_INTEGRATION_ONLY_PATHS"])
    forbidden = set(ownership["M4_3_FORBIDDEN_PATHS"])
    require(exclusive and not exclusive & shared and not exclusive & integration, "ownership overlap")
    require("aota_forge/core/ingress.py" in exclusive, "ingress ownership")
    require("aota_forge/core/authority.py" in forbidden, "M4-2 authority protection")
    require("aota_forge/core/transitions.py" in forbidden, "M4-4 transition protection")
    require("aota_forge/adapters/github/**" in forbidden, "GitHub adapter protection")
    require("any unlisted aota_forge/** functional source path" in forbidden, "unlisted path protection")

    nodes = set(dag["nodes"])
    edges = [tuple(edge) for edge in dag["edges"]]
    require(dag["acyclic"] is True, "DAG flag")
    require(all(len(edge) == 2 and set(edge) <= nodes for edge in edges), "DAG edge shape")
    indegree = {node: 0 for node in nodes}
    outgoing = {node: [] for node in nodes}
    for source, target in edges:
        outgoing[source].append(target)
        indegree[target] += 1
    queue = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for target in outgoing[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    require(visited == len(nodes), "DAG contains a cycle")
    require("accepted_base" in nodes and "M4_5_handoff" in nodes, "DAG endpoints")

    require(regressions["behavioral_closure_claimed"] is False, "behavioral closure claim")
    require(regressions["M4_3_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT"] == 0, "false closure count")
    require(len(regressions["entries"]) >= 20, "regression coverage")
    require(regressions["positive_test_rule"].startswith("Ingress-positive authorization cases must use CapabilityLeaseIssuer.issue"), "real lease regression rule")
    baseline = regressions["known_non_blocking_baseline_finding"]
    require(set(baseline["cases"]) == {
        "test_i5_valid_authorization_and_plan_init_are_compatible",
        "test_i8_abandoned_retirement_preserves_cancelled_semantics",
        "test_i9_exact_successor_retirement_is_valid",
        "test_i12_changed_intent_conflicts_on_same_idempotency_identity",
    }, "baseline fixture finding cases")
    require(baseline["acceptance_status"] == "non-acceptance evidence; do not repair during M4-3 planning", "baseline finding status")

    accepted_base = negative["accepted_base"]
    require(accepted_base["sha"] == EXPECTED_BASE, "accepted base")
    require(accepted_base["failed_integration_sha_used_as_base"] is False, "failed integration base")
    required_invariants = negative["required_invariants"]
    require(all(value is False for key, value in required_invariants.items() if key != "M4_3_PLANNING_READY"), "runtime invariant")
    forbidden_scope = " ".join(negative["explicitly_out_of_scope"])
    for phrase in ("Portable Plan authority-store writes", "GitHub Issue", "durable external mutation journal", "runtime activation", "generic mutate(operation, payload)"):
        require(phrase in forbidden_scope, f"negative scope missing: {phrase}")

    required_gate = gate["planning_gate"]["required"]
    require("scripts/m4_3_plan_guard.py passes all negative cases" in required_gate, "guard gate")
    require(gate["independent_review_gate"]["required"] is True, "review gate")
    require(gate["project_steward_acceptance"]["required"] is True, "Steward gate")
    require(gate["source_implementation_gate"]["M4_3_SOURCE_IMPLEMENTATION_AUTHORIZED"] is False, "source gate")
    require(handoff["downstream_authorization"]["M4_5_EXECUTION_AUTHORIZED"] is False, "M4-5 gate")
    require(handoff["downstream_authorization"]["M4_3_ACCEPTANCE_AUTO_AUTHORIZES_M4_5"] is False, "downstream auto authorization")


def negative_cases(documents: dict[str, dict]) -> int:
    cases = [
        ("NEG-M43-01", lambda value: value["m4-3-authorization-composition-contract.json"].update(lease_issuance_variant="B")),
        ("NEG-M43-02", lambda value: value["m4-3-authorization-composition-contract.json"].update(ingress_issuance_allowed=True)),
        ("NEG-M43-03", lambda value: value["m4-3-operation-dispatch-contract.json"]["canonical_operations"].append({"operation": "mutate"})),
        ("NEG-M43-04", lambda value: value["m4-3-write-request-envelope-contract.json"]["field_partition"]["never_model_asserted"].remove("CapabilityLease")),
        ("NEG-M43-05", lambda value: value["m4-3-plan-init-ingress-path.json"]["semantic_choice_boundary"].update(core_selects_project_under_ambiguity=True)),
        ("NEG-M43-06", lambda value: value["m4-3-retirement-ingress-path.json"]["selection_rules"]["forbidden_selection"].remove("newest")),
        ("NEG-M43-07", lambda value: next(item for item in value["m4-3-result-error-projection-contract.json"]["effect_projection"] if item["effect"] == "OUTCOME_UNKNOWN").update(ok=True)),
        ("NEG-M43-08", lambda value: value["m4-3-cli-adapter-scope.json"]["write_command_surface"]["currently_exposed"].append("aota plan init")),
        ("NEG-M43-09", lambda value: value["m4-3-source-ownership-partition.json"]["M4_3_SHARED_READ_ONLY_PATHS"].append("aota_forge/core/ingress.py")),
        ("NEG-M43-10", lambda value: value["m4-3-dependency-dag.json"].update(acyclic=False)),
        ("NEG-M43-11", lambda value: value["m4-3-negative-scope-and-invariants.json"]["required_invariants"].update(PRODUCTION_GRAPH_AUTHORITY_ACTIVE=True)),
        ("NEG-M43-12", lambda value: value["m4-3-planning-acceptance-gate.json"]["source_implementation_gate"].update(M4_3_SOURCE_IMPLEMENTATION_AUTHORIZED=True)),
    ]
    rejected = 0
    for case_id, mutate in cases:
        candidate = deepcopy(documents)
        mutate(candidate)
        try:
            assert_contracts(candidate)
        except (AssertionError, KeyError, TypeError, ValueError, StopIteration):
            rejected += 1
        else:
            raise AssertionError(f"{case_id} unexpectedly accepted")
    require(rejected == len(cases), f"negative rejection count: {rejected}")
    return rejected


def changed_paths() -> set[str]:
    paths = set(filter(None, git("diff", "--name-only", EXPECTED_BASE, "HEAD").splitlines()))
    for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines():
        value = line[3:] if len(line) >= 3 else ""
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value:
            paths.add(value)
    return paths


def assert_git_scope() -> tuple[str, set[str]]:
    branch = git("branch", "--show-current")
    require(branch == BRANCH, f"branch mismatch: {branch}")
    subprocess.check_call(["git", "merge-base", "--is-ancestor", EXPECTED_BASE, "HEAD"], cwd=ROOT)
    subprocess.check_call(["git", "diff", "--check", EXPECTED_BASE, "HEAD"], cwd=ROOT)
    paths = changed_paths()
    for path in paths:
        allowed = path == ALLOWED_SCRIPT or path.startswith(ALLOWED_PREFIX)
        require(allowed, f"forbidden changed path: {path}")
        require(not path.startswith("aota_forge/"), f"functional source changed: {path}")
    return git("rev-parse", "HEAD"), paths


def parse_all_json() -> int:
    count = 0
    for path in ROOT.rglob("*.json"):
        if ".git" in path.parts:
            continue
        with path.open(encoding="utf-8") as stream:
            json.load(stream)
        count += 1
    return count


def main() -> int:
    head, paths = assert_git_scope()
    documents = load_documents()
    assert_metadata(documents)
    assert_contracts(documents)
    rejected = negative_cases(documents)
    parsed = parse_all_json()
    print(f"M4_3_ACTUAL_HEAD={head}")
    print(f"M4_3_BRANCH={BRANCH}")
    print(f"M4_3_ARTIFACT_COUNT={len(documents)}")
    print(f"M4_3_CHANGED_PATH_COUNT={len(paths)}")
    print(f"M4_3_JSON_PARSE_COUNT={parsed}")
    print(f"M4_3_NEGATIVE_CASE_COUNT={rejected}")
    print(f"M4_3_NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print("M4_3_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT=0")
    print("M4_3_SOURCE_IMPLEMENTATION_AUTHORIZED=no")
    print("M4_3_PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no")
    print("M4_3_EXTERNAL_AUTHORITY_WRITE=no")
    print("M4_3_PLAN_GUARD=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"M4_3_PLAN_GUARD=FAIL: {exc}")
        raise SystemExit(1)
