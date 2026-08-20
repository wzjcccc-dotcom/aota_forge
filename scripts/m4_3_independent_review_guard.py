#!/usr/bin/env python3
"""Independent review guard for the Issue #9 M4-3 planning package.

This guard is intentionally separate from the candidate planning guard.  It
checks the candidate's lineage and scope, reads the planning contracts through
an independent assertion set, and mutates isolated document copies to prove
that unsafe planning claims are rejected.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVIDENCE = ROOT / "deploy/evidence/issues/9/m4-3-plan"
BASE = "16826b551b62f4874bfc6ec734cb571cb3d9dc6d"
TARGET = "5842a51527c9941d8577efc157109f68b1ae0325"
BRANCH = "aota/m4/m4-3-plan-independent-review"
REVIEW_PREFIX = "deploy/evidence/issues/9/m4-3-plan-review/"
REVIEW_SCRIPT = "scripts/m4_3_independent_review_guard.py"

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
    "expected_base_sha": BASE,
    "planning_only": True,
    "production_behavior_claimed": False,
    "source_implementation_authorized": False,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], cwd=ROOT, check=False).returncode == 0


def load_documents() -> dict[str, dict]:
    actual = {path.name for path in EVIDENCE.glob("*.json")}
    require(actual == REQUIRED, "candidate artifact set changed")
    documents: dict[str, dict] = {}
    for name in sorted(REQUIRED):
        with (EVIDENCE / name).open(encoding="utf-8") as stream:
            value = json.load(stream)
        require(isinstance(value, dict), f"artifact is not an object: {name}")
        documents[name] = value
    return documents


def assert_contract_documents(documents: dict[str, dict]) -> None:
    for name, document in documents.items():
        for key, expected in META.items():
            require(document.get(key) == expected, f"{name}: metadata {key}")

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

    require(audit["current_status"] == "m4_3_ready_for_planning", "current ingress status")
    require(len(audit["current_capabilities"]) >= 7, "capability audit coverage")
    require(len(audit["exact_gaps"]) >= 7, "exact gap coverage")
    require("M4_3_EXECUTION_AUTHORIZED=no" in audit["invariants_to_preserve"], "execution gate")
    require("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no" in audit["invariants_to_preserve"], "graph write gate")

    require(envelope["envelope_type"] == "MutationIngressRequest", "typed envelope")
    require(envelope["representation"].startswith("internal typed request"), "model-facing envelope")
    payload_operations = {item["operation"] for item in envelope["operation_payloads"]}
    require(payload_operations == {"plan_init", "plan_retirement"}, "envelope operation set")
    never_model = set(envelope["field_partition"]["never_model_asserted"])
    require({"trusted principal", "CapabilityLease", "contract hash", "Subject revision"} <= never_model, "trusted partition")
    require("PlanInitRequest" in envelope["request_reuse_rule"], "PLAN_INIT request reuse")
    require("PlanRetirementRequest" in envelope["request_reuse_rule"], "retirement request reuse")
    require(any("Reject a plain dict" in rule for rule in envelope["construction_rules"]), "typed construction boundary")

    operations = {item["operation"] for item in dispatch["canonical_operations"]}
    require(operations == {"plan_init", "plan_retirement"}, "closed dispatch set")
    require(dispatch["write_entry_point"]["symbol"] == "execute_mutation", "write ingress symbol")
    require("read-only" in dispatch["write_entry_point"]["relationship_to_read_path"], "read path preservation")
    require(dispatch["generic_mutation_api"].startswith("forbidden"), "generic mutation ban")
    for item in dispatch["canonical_operations"]:
        require(item["read_write"] == "write", f"write descriptor: {item['operation']}")
        require(item["contract_hash_source"].endswith(".contract_hash()"), "descriptor hash source")
        require("aota_forge/core/transitions.py:" in item["dispatch_target"], "transition target")

    required_bindings = {
        "principal",
        "operation",
        "typed_target",
        "mutation_scope",
        "contract_hash",
        "intent_fingerprint",
        "subject_expected_revision",
        "external_authority_precondition",
        "authority_source_revision",
        "authority_observed_raw_digest",
        "candidate_raw_digest",
        "normalized_plan_digest",
    }
    require(authorization["lease_issuance_variant"] == "A", "lease variant")
    require(authorization["ingress_issuance_allowed"] is False, "ingress lease issuance")
    require(authorization["issuer_semantic_decision_maker"] is False, "issuer semantic role")
    require(required_bindings <= set(authorization["exact_binding_fields"]), "lease bindings")
    require("CapabilityLeaseIssuer.validate_lease" in " ".join(authorization["composition_sequence"]), "lease validation")
    require(authorization["evidence_rule"]["forged_or_fixture_lease_positive_allowed"] is False, "real lease rule")
    require(authorization["revision_domains"]["normalized_plan_digest_can_authorize_external_CAS"] is False, "digest domain")

    require(plan_init["operation"] == "plan_init", "PLAN_INIT operation")
    require(plan_init["request_type"] == "PlanInitRequest", "PLAN_INIT request type")
    require(plan_init["semantic_choice_boundary"]["core_selects_project_under_ambiguity"] is False, "project ambiguity")
    require(plan_init["semantic_choice_boundary"]["relationship_evidence_is_binding"] is False, "relationship binding")
    require("plan_init(store, request)" in " ".join(plan_init["dispatch_steps"]), "PLAN_INIT transition")
    require(plan_init["success_and_failure_rules"]["missing_lease"].startswith("blocked"), "missing lease")
    require("same idempotency key" in plan_init["success_and_failure_rules"]["replay"], "PLAN_INIT replay")
    require(plan_init["success_and_failure_rules"]["changed_intent"] == "CONFLICT with no effect", "PLAN_INIT conflict")

    require(retirement["operation"] == "plan_retirement", "retirement operation")
    require(retirement["request_type"] == "PlanRetirementRequest", "retirement request type")
    require(retirement["two_stage_boundary"]["candidate_discovery_is_write"] is False, "candidate discovery")
    require(retirement["two_stage_boundary"]["ingress_selects_candidate"] is False, "candidate selection")
    forbidden_selection = set(retirement["selection_rules"]["forbidden_selection"])
    require({"newest", "latest", "first", "similarity", "stale current pointer"} <= forbidden_selection, "heuristic selection")
    require(retirement["retirement_rules"]["abandoned"]["resulting_state"] == "cancelled", "abandoned state")
    require(retirement["retirement_rules"]["superseded"]["successor_required"] is True, "successor required")
    require(retirement["retirement_rules"]["superseded"]["successor_must_not_equal_target"] is True, "self successor")
    require(retirement["retirement_rules"]["implicit_resurrection"] is False, "resurrection")

    expected_effects = {
        "APPLIED_VERIFIED",
        "REPLAYED_VERIFIED",
        "NO_EFFECT",
        "NEEDS_SEMANTIC_CHOICE",
        "BLOCKED",
        "CONFLICT",
        "OUTCOME_UNKNOWN",
        "FAILED_NO_EFFECT",
    }
    effects = {item["effect"] for item in result["effect_projection"]}
    require(effects == expected_effects, "effect coverage")
    unknown = next(item for item in result["effect_projection"] if item["effect"] == "OUTCOME_UNKNOWN")
    require(unknown["ok"] is False and unknown["confirmation"] == "unknown", "unknown effect")
    choice = next(item for item in result["effect_projection"] if item["effect"] == "NEEDS_SEMANTIC_CHOICE")
    require(choice["ok"] is True and choice["confirmation"] == "no", "choice effect")
    require("lifecycle_code" in " ".join(result["projection_rules"]), "lifecycle projection")

    require(cli["current_cli"]["all_current_commands_are_read_only"] is True, "current CLI state")
    require(cli["write_command_surface"]["currently_exposed"] == [], "premature CLI writes")
    future_commands = {item["command"] for item in cli["write_command_surface"]["future_allowed_commands"]}
    require(future_commands == {"aota plan init", "aota plan retire"}, "future CLI set")
    require(all(item["route"] == "execute_mutation" for item in cli["write_command_surface"]["future_allowed_commands"]), "CLI route")
    require(cli["m4_3_scope"]["cli_source_changes_in_this_plan"] is False, "CLI source scope")
    require(cli["m4_3_scope"]["cli_is_plan_authority"] is False, "CLI authority")

    exclusive = set(ownership["M4_3_EXCLUSIVE_WRITE_PATHS"])
    shared = set(ownership["M4_3_SHARED_READ_ONLY_PATHS"])
    integration = set(ownership["M4_3_INTEGRATION_ONLY_PATHS"])
    forbidden = set(ownership["M4_3_FORBIDDEN_PATHS"])
    require(exclusive == {"aota_forge/core/ingress.py", "aota_forge/core/catalog.py", "aota_forge/core/handlers.py"}, "exclusive ownership")
    require(not exclusive & shared and not exclusive & integration, "ownership overlap")
    require("aota_forge/core/authority.py" in forbidden, "M4-2 protection")
    require("aota_forge/core/transitions.py" in forbidden, "M4-4 protection")
    require("aota_forge/adapters/github/**" in forbidden, "GitHub protection")
    require("any unlisted aota_forge/** functional source path" in forbidden, "unlisted source protection")

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
    require(visited == len(nodes), "DAG cycle")
    require(handoff["downstream_authorization"]["M4_5_EXECUTION_AUTHORIZED"] is False, "M4-5 authorization")
    require(handoff["downstream_authorization"]["M4_3_ACCEPTANCE_AUTO_AUTHORIZES_M4_5"] is False, "M4-5 auto authorization")

    require(regressions["behavioral_closure_claimed"] is False, "behavioral closure")
    require(len(regressions["entries"]) >= 20, "regression map coverage")
    require(regressions["positive_test_rule"].startswith("Ingress-positive authorization cases must use CapabilityLeaseIssuer.issue"), "positive lease rule")
    baseline = regressions["known_non_blocking_baseline_finding"]
    require(set(baseline["cases"]) == {
        "test_i5_valid_authorization_and_plan_init_are_compatible",
        "test_i8_abandoned_retirement_preserves_cancelled_semantics",
        "test_i9_exact_successor_retirement_is_valid",
        "test_i12_changed_intent_conflicts_on_same_idempotency_identity",
    }, "fixture debt cases")
    require(baseline["acceptance_status"] == "non-acceptance evidence; do not repair during M4-3 planning", "fixture debt status")

    required_invariants = negative["required_invariants"]
    require(all(value is False for key, value in required_invariants.items() if key != "M4_3_PLANNING_READY"), "runtime invariants")
    forbidden_scope = " ".join(negative["explicitly_out_of_scope"])
    for phrase in ("Portable Plan authority-store writes", "GitHub Issue", "durable external mutation journal", "runtime activation", "generic mutate(operation, payload)"):
        require(phrase in forbidden_scope, f"negative scope: {phrase}")
    require(gate["independent_review_gate"]["required"] is True, "independent review gate")
    require(gate["project_steward_acceptance"]["required"] is True, "Steward gate")
    require(gate["source_implementation_gate"]["M4_3_SOURCE_IMPLEMENTATION_AUTHORIZED"] is False, "source gate")


def assert_candidate_lineage_and_scope() -> None:
    require(git_ok("merge-base", "--is-ancestor", TARGET, "HEAD"), "review commit must descend from candidate")
    require(git("show", "-s", "--format=%P", TARGET) == BASE, "candidate parent is not exact base")
    require(git_ok("merge-base", "--is-ancestor", BASE, TARGET), "base is not candidate ancestor")
    require(git("rev-parse", "refs/heads/aota/m4/m4-3-plan") == TARGET, "candidate local ref drift")
    require(git("rev-parse", "refs/remotes/origin/aota/m4/m4-3-plan") == TARGET, "candidate remote ref drift")
    candidate_paths = set(git("diff", "--name-only", BASE, TARGET).splitlines())
    expected_paths = {f"deploy/evidence/issues/9/m4-3-plan/{name}" for name in REQUIRED}
    expected_paths.add("scripts/m4_3_plan_guard.py")
    require(candidate_paths == expected_paths, "candidate changed-path set")
    require(not any(path.startswith("aota_forge/") or path.startswith("tests/") for path in candidate_paths), "candidate functional change")
    require(git_ok("diff", "--check", BASE, TARGET), "candidate diff check")


def assert_review_scope() -> None:
    require(git("branch", "--show-current") == BRANCH, "review branch")
    paths = set(git("diff", "--name-only", TARGET, "HEAD").splitlines())
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    for line in status.splitlines():
        if len(line) >= 3:
            paths.add(line[3:])
    require(paths <= {REVIEW_SCRIPT} | {path for path in paths if path.startswith(REVIEW_PREFIX)}, "review scope")
    require(not any(path.startswith("aota_forge/") or path.startswith("tests/") for path in paths), "review functional change")


def assert_current_source_is_read_only() -> None:
    from aota_forge.core import authorization, transitions
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    require(authorization.M4_2_WRITE_INGRESS_IMPLEMENTED is False, "M4-2 write ingress active")
    require(authorization.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False, "M4-2 external write active")
    require(transitions.M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED is False, "M4-4 write ingress active")
    require(transitions.M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False, "M4-4 external write active")
    require(transitions.PRODUCTION_INGRESS_TRANSITION_WIRING_IMPLEMENTED is False, "production transition wiring active")
    require(transitions.AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False, "authoritative graph write active")
    require(all(descriptor.read_write == "read" for descriptor, _handler in DEFAULT_REGISTRY._bindings.values()), "read-only registry drift")


def run_independent_negative_cases(documents: dict[str, dict]) -> int:
    cases = [
        ("NEG-IR-01", lambda value: value["m4-3-authorization-composition-contract.json"].update(lease_issuance_variant="B")),
        ("NEG-IR-02", lambda value: value["m4-3-authorization-composition-contract.json"].update(ingress_issuance_allowed=True)),
        ("NEG-IR-03", lambda value: value["m4-3-operation-dispatch-contract.json"]["canonical_operations"].append({"operation": "mutate"})),
        ("NEG-IR-04", lambda value: value["m4-3-write-request-envelope-contract.json"]["field_partition"]["never_model_asserted"].remove("CapabilityLease")),
        ("NEG-IR-05", lambda value: value["m4-3-authorization-composition-contract.json"]["exact_binding_fields"].remove("normalized_plan_digest")),
        ("NEG-IR-06", lambda value: value["m4-3-plan-init-ingress-path.json"]["semantic_choice_boundary"].update(core_selects_project_under_ambiguity=True)),
        ("NEG-IR-07", lambda value: value["m4-3-retirement-ingress-path.json"]["selection_rules"]["forbidden_selection"].remove("newest")),
        ("NEG-IR-08", lambda value: next(item for item in value["m4-3-result-error-projection-contract.json"]["effect_projection"] if item["effect"] == "OUTCOME_UNKNOWN").update(ok=True)),
        ("NEG-IR-09", lambda value: value["m4-3-cli-adapter-scope.json"]["write_command_surface"]["currently_exposed"].append("aota plan init")),
        ("NEG-IR-10", lambda value: value["m4-3-source-ownership-partition.json"]["M4_3_SHARED_READ_ONLY_PATHS"].append("aota_forge/core/ingress.py")),
        ("NEG-IR-11", lambda value: value["m4-3-dependency-dag.json"].update(acyclic=False)),
        ("NEG-IR-12", lambda value: value["m4-3-negative-scope-and-invariants.json"]["required_invariants"].update(PRODUCTION_GRAPH_AUTHORITY_ACTIVE=True)),
        ("NEG-IR-13", lambda value: value["m4-3-planning-acceptance-gate.json"]["source_implementation_gate"].update(M4_3_SOURCE_IMPLEMENTATION_AUTHORIZED=True)),
        ("NEG-IR-14", lambda value: value["m4-3-regression-handoff-map.json"]["known_non_blocking_baseline_finding"].update(acceptance_status="blocking semantic regression")),
    ]
    rejected = 0
    for case_id, mutate in cases:
        candidate = deepcopy(documents)
        mutate(candidate)
        try:
            assert_contract_documents(candidate)
        except (AssertionError, KeyError, TypeError, ValueError, StopIteration):
            rejected += 1
            print(f"PASS {case_id} rejected")
        else:
            print(f"FAIL {case_id} unexpectedly accepted")
    require(rejected == len(cases), f"independent rejection count: {rejected}")
    return rejected


def main() -> int:
    documents = load_documents()
    assert_candidate_lineage_and_scope()
    assert_review_scope()
    assert_contract_documents(documents)
    assert_current_source_is_read_only()
    rejected = run_independent_negative_cases(documents)
    print(f"M4_3_REVIEW_BASE={BASE}")
    print(f"M4_3_REVIEW_TARGET={TARGET}")
    print(f"M4_3_REVIEW_BRANCH={BRANCH}")
    print(f"M4_3_REVIEW_INDEPENDENT_CHECK_COUNT=14")
    print(f"M4_3_REVIEW_INDEPENDENT_CHECK_REJECT_COUNT={rejected}")
    print("M4_3_REVIEW_BLOCKING_FINDING_COUNT=0")
    print("M4_3_REVIEW_GUARD=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"M4_3_REVIEW_GUARD=FAIL: {exc}")
        raise SystemExit(1)
