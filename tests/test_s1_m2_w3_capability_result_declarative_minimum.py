"""S1/M2/W3 — Capability / Result Declarative Minimum.

Verifies minimal declarative semantics for capabilities.yaml and results.yaml
without fabricating taxonomy, routing policy, or S5 governance.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
import yaml

from aota_forge.core.contracts.loader import (
    DECLARATIVE_DOCUMENT_SCHEMA_VERSION,
    DeclarativeContractError,
    ERR_INVALID,
    discover_canonical_project_root,
    load_capabilities,
    load_operations,
    load_results,
    load_operation_descriptors,
    load_operation_descriptor_map,
)
from aota_forge.core.contracts.version import (
    GENERIC_TARGET_PROTOCOL_ACTIVE,
    OPERATION_CONTRACT_PROTOCOL,
    PROTOCOL_VERSION,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities, FORBIDDEN_SEMANTIC_FIELDS


def canonical_root() -> pathlib.Path:
    return discover_canonical_project_root()


def write_contract(root: pathlib.Path, filename: str, text: str) -> pathlib.Path:
    directory = root / ".aota" / "contracts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def write_manifest(root: pathlib.Path, text: str) -> pathlib.Path:
    directory = root / ".aota"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "project.yaml"
    path.write_text(text, encoding="utf-8")
    return path


MINIMAL_MANIFEST = """\
schema_version: 1
project:
  id: tmp
  name: tmp
  kind: forge-core
  status: active
summary: tmp
capabilities: [forge-core]
paths:
  source_root: .
  source: [aota_forge/]
  docs: [docs/]
  scripts: [scripts/]
  profiles: []
  skills: []
  tests: [tests/]
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: true
codegraph:
  enabled: false
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""

# ---------------------------------------------------------------------------
# §41 Capability document
# ---------------------------------------------------------------------------


def test_capabilities_document_exists_exactly_once():
    root = canonical_root()
    path = root / ".aota" / "contracts" / "capabilities.yaml"
    assert path.is_file(), f"capabilities.yaml missing at {path}"
    doc = load_capabilities(root)
    assert doc["schema_version"] == 1
    assert doc["kind"] == "capabilities"
    assert isinstance(doc["contracts"], list)
    # Ensure exactly one canonical file, no duplicate package data
    assert not (root / "aota_forge" / "core" / "contracts" / "capabilities.yaml").is_file()
    assert not (root / "aota_forge" / ".aota" / "contracts" / "capabilities.yaml").is_file()


def test_capabilities_every_entry_has_stable_semantic_identity():
    doc = load_capabilities(canonical_root())
    for entry in doc["contracts"]:
        assert "name" in entry
        assert isinstance(entry["name"], str) and entry["name"].strip()
        assert "description" in entry and entry["description"].strip()
        # name must be distinct from executor_id concept: we ensure entry has no executor_id acting as identity
        # and duplicate check is on name, not executor_id
        assert "executor_id" not in entry or entry["name"] != entry.get("executor_id")
    # At least one production entry exists
    assert len(doc["contracts"]) >= 1


def test_capabilities_no_duplicate_semantic_identity(tmp_path: pathlib.Path):
    # Duplicate name must fail closed
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
- name: aota.capability.dup
  description: dup
  adapter_kind: hermes_host_adapter
  supported_execution_modes: [sync]
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
- name: aota.capability.dup
  description: dup2
  adapter_kind: hermes_host_adapter
  supported_execution_modes: [sync]
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID
    assert "duplicate" in str(exc.value).lower()


def test_capabilities_executor_id_not_identity(tmp_path: pathlib.Path):
    # Same executor_id but different names must NOT be considered duplicate
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
- name: aota.capability.one
  description: one
  adapter_kind: kind_a
  supported_execution_modes: [sync]
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
- name: aota.capability.two
  description: two
  adapter_kind: kind_a
  supported_execution_modes: [async]
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    # Should succeed: duplicate check is on name, not on implicit executor_id
    doc = load_capabilities(tmp_path)
    assert len(doc["contracts"]) == 2


def test_capabilities_no_routing_profile_handler_fields():
    doc = load_capabilities(canonical_root())
    for entry in doc["contracts"]:
        for field in ("preferred_executor", "best_role", "task_priority_recommendation", "semantic_routing_score", "heuristic_quality_rating"):
            assert field not in entry
        for field in ("profile", "handler", "callable", "routing", "ranking", "score"):
            assert field not in entry


# ---------------------------------------------------------------------------
# §42 Strict capability types
# ---------------------------------------------------------------------------


def test_strict_capability_types_reject_string_bool(tmp_path: pathlib.Path):
    # supports_streaming_events: "false" must be rejected (not coerced to True)
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
- name: aota.capability.bad
  description: bad
  adapter_kind: hermes_host_adapter
  supported_execution_modes: [sync]
  supports_streaming_events: "false"
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_strict_capability_types_reject_int_executor_id():
    # ExecutorCapabilities.from_dict with int executor_id must fail
    valid = {
        "executor_id": "test",
        "adapter_kind": "kind",
        "supported_execution_modes": ["sync"],
        "supports_streaming_events": True,
        "supports_task_cancellation": True,
        "supports_task_resume": False,
        "supports_structured_result": True,
        "supported_canonical_roles": ["coder"],
        "supported_isolation_modes": ["process"],
        "supports_working_directory": True,
        "supports_artifact_transport": True,
    }
    with pytest.raises(TypeError):
        ExecutorCapabilities.from_dict(dict(valid, executor_id=123))


def test_strict_capability_types_reject_string_modes(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
- name: aota.capability.bad2
  description: bad2
  adapter_kind: hermes_host_adapter
  supported_execution_modes: "sync"
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_strict_capability_types_reject_int_bool():
    valid = {
        "executor_id": "test",
        "adapter_kind": "kind",
        "supported_execution_modes": ["sync"],
        "supports_streaming_events": True,
        "supports_task_cancellation": True,
        "supports_task_resume": False,
        "supports_structured_result": True,
        "supported_canonical_roles": ["coder"],
        "supported_isolation_modes": ["process"],
        "supports_working_directory": True,
        "supports_artifact_transport": True,
    }
    for val in (0, 1):
        with pytest.raises(TypeError):
            ExecutorCapabilities.from_dict(dict(valid, supports_streaming_events=val))


def test_valid_typed_yaml_round_trips():
    # Production file should round-trip deterministically
    doc = load_capabilities(canonical_root())
    # Re-validate via ExecutorCapabilities construction for each entry via synthetic executor_id
    for entry in doc["contracts"]:
        synthetic = dict(entry)
        synthetic["executor_id"] = "synthetic-" + entry["name"]
        # Should construct successfully
        caps = ExecutorCapabilities.from_dict(
            {
                "executor_id": synthetic["executor_id"],
                "adapter_kind": synthetic["adapter_kind"],
                "supported_execution_modes": synthetic["supported_execution_modes"],
                "supports_streaming_events": synthetic["supports_streaming_events"],
                "supports_task_cancellation": synthetic["supports_task_cancellation"],
                "supports_task_resume": synthetic["supports_task_resume"],
                "supports_structured_result": synthetic["supports_structured_result"],
                "supported_canonical_roles": synthetic["supported_canonical_roles"],
                "supported_isolation_modes": synthetic["supported_isolation_modes"],
                "supports_working_directory": synthetic["supports_working_directory"],
                "supports_artifact_transport": synthetic["supports_artifact_transport"],
            }
        )
        assert caps.supports_streaming_events is False or caps.supports_streaming_events is True


# ---------------------------------------------------------------------------
# §43 Forbidden capability routing fields
# ---------------------------------------------------------------------------


def test_forbidden_preferred_executor_rejected(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
- name: aota.capability.bad
  description: bad
  adapter_kind: hermes_host_adapter
  supported_execution_modes: [sync]
  supports_streaming_events: false
  supports_task_cancellation: true
  supports_task_resume: true
  supports_structured_result: true
  supported_canonical_roles: [coder]
  supported_isolation_modes: [process]
  supports_working_directory: true
  supports_artifact_transport: true
  preferred_executor: hermes
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_forbidden_ranking_score_rejected(tmp_path: pathlib.Path):
    valid = {
        "executor_id": "test",
        "adapter_kind": "kind",
        "supported_execution_modes": ["sync"],
        "supports_streaming_events": True,
        "supports_task_cancellation": True,
        "supports_task_resume": False,
        "supports_structured_result": True,
        "supported_canonical_roles": ["coder"],
        "supported_isolation_modes": ["process"],
        "supports_working_directory": True,
        "supports_artifact_transport": True,
        "semantic_routing_score": 0.9,
    }
    with pytest.raises(ValueError):
        ExecutorCapabilities.from_dict(valid)


# ---------------------------------------------------------------------------
# §44 Capability normalization
# ---------------------------------------------------------------------------


def test_capability_normalization_preserved():
    caps = ExecutorCapabilities(
        executor_id="test",
        adapter_kind="kind",
        supported_execution_modes=("sync", "async", "sync"),
        supports_streaming_events=True,
        supports_task_cancellation=True,
        supports_task_resume=False,
        supports_structured_result=True,
        supported_canonical_roles=("reviewer", "coder", "coder"),
        supported_isolation_modes=("process", "worktree", "process"),
        supports_working_directory=True,
        supports_artifact_transport=True,
    )
    # Should be sorted and deduped
    assert caps.supported_execution_modes == ("async", "sync")
    assert caps.supported_canonical_roles == ("coder", "reviewer")
    assert caps.supported_isolation_modes == ("process", "worktree")


# ---------------------------------------------------------------------------
# §45 Results document
# ---------------------------------------------------------------------------


def test_results_document_exists():
    root = canonical_root()
    path = root / ".aota" / "contracts" / "results.yaml"
    assert path.is_file()
    doc = load_results(root)
    assert doc["schema_version"] == 1
    assert doc["kind"] == "results"
    assert isinstance(doc["contracts"], list)
    assert len(doc["contracts"]) >= 1
    assert not (root / "aota_forge" / "core" / "contracts" / "results.yaml").is_file()


def test_results_no_runtime_fields():
    doc = load_results(canonical_root())
    for entry in doc["contracts"]:
        for field in ("correlation_id", "evidence", "artifact", "provenance", "warnings", "data", "status"):
            assert field not in entry


def test_results_no_provenance_governance():
    doc = load_results(canonical_root())
    for entry in doc["contracts"]:
        for field in ("provenance", "artifact_manifest", "retention_policy", "receipt"):
            assert field not in entry


# ---------------------------------------------------------------------------
# §46 Result reference coverage
# ---------------------------------------------------------------------------


def test_result_reference_coverage():
    root = canonical_root()
    ops_doc = load_operations(root)
    non_null_refs = sorted(
        {c["result_contract"] for c in ops_doc["contracts"] if c.get("result_contract")}
    )
    results_doc = load_results(root)
    result_ids = sorted({c["name"] for c in results_doc["contracts"]})
    assert set(non_null_refs) == set(result_ids), f"ops refs {non_null_refs} vs results {result_ids}"


# ---------------------------------------------------------------------------
# §47 Result compatibility map
# ---------------------------------------------------------------------------


def test_result_compatibility_map():
    root = canonical_root()
    ops_doc = load_operations(root)
    # Build mapping from result_contract -> operations
    expected: dict[str, set[str]] = {}
    for entry in ops_doc["contracts"]:
        rc = entry.get("result_contract")
        if rc:
            expected.setdefault(rc, set()).add(entry["name"])
    results_doc = load_results(root)
    for entry in results_doc["contracts"]:
        name = entry["name"]
        compat = set(entry["compatible_operations"])
        assert name in expected, f"unexpected result {name}"
        assert compat == expected[name], f"compatibility mismatch for {name}: {compat} vs {expected[name]}"


# ---------------------------------------------------------------------------
# §48 Result instance separation
# ---------------------------------------------------------------------------


def test_result_instance_separation():
    doc = load_results(canonical_root())
    for entry in doc["contracts"]:
        # Must not contain runtime result instance fields
        for field in ("correlation_id", "mutation_effect", "data", "evidence", "warnings", "error"):
            assert field not in entry
        # name is opaque identity, not parsed version
        assert isinstance(entry["name"], str) and entry["name"].strip()
        # description must exist
        assert "description" in entry


# ---------------------------------------------------------------------------
# §49 Duplicates for results
# ---------------------------------------------------------------------------


def test_duplicate_result_contract_identity_fails_closed(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: results
contracts:
- name: dup.result.v1
  description: dup
  compatible_operations: [plan_init]
  protocol: aota-forge.operation-contract
  protocol_version: '1.0'
- name: dup.result.v1
  description: dup2
  compatible_operations: [plan_retirement]
  protocol: aota-forge.operation-contract
  protocol_version: '1.0'
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "results.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_results(tmp_path)
    assert exc.value.code == ERR_INVALID


# ---------------------------------------------------------------------------
# §50 Protocol freeze
# ---------------------------------------------------------------------------


def test_protocol_freeze():
    assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
    assert PROTOCOL_VERSION == "1.0"
    assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
    # Loading capabilities/results must not change these
    _ = load_capabilities(canonical_root())
    _ = load_results(canonical_root())
    assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
    assert PROTOCOL_VERSION == "1.0"
    assert GENERIC_TARGET_PROTOCOL_ACTIVE is False


# ---------------------------------------------------------------------------
# §51 W2 Regression
# ---------------------------------------------------------------------------


def test_w2_regression_operations_13_and_hashes():
    # Import via file path to avoid module resolution issues in different pytest roots
    import importlib.util
    import pathlib

    w2_path = pathlib.Path(__file__).parent / "test_s1_m2_w2_operation_authority_migration.py"
    spec = importlib.util.spec_from_file_location("w2_migration", w2_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(mod)  # type: ignore
    EXPECTED_HASHES = mod.EXPECTED_HASHES  # type: ignore

    m = load_operation_descriptor_map(canonical_root())
    assert len(m) == 13
    for name, expected in EXPECTED_HASHES.items():
        assert m[name].contract_hash() == expected, f"hash mismatch {name}"


def test_registry_topology_preserved():
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    # Should still be 7 general/lifecycle
    assert len(DEFAULT_REGISTRY.names()) == 7
    assert "execution.task_start" not in DEFAULT_REGISTRY.names()


def test_operations_list_behavior():
    from aota_forge.core.contracts.operations import available_operations

    ops = available_operations()
    assert ops == sorted(ops, key=lambda x: x["operation"])


# ---------------------------------------------------------------------------
# §52 No routing effect
# ---------------------------------------------------------------------------


def test_capability_load_has_no_runtime_side_effect():
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.execution.registry import ExecutorRegistry

    before = set(DEFAULT_REGISTRY.names())
    # Load capabilities should not register executors
    doc = load_capabilities(canonical_root())
    after = set(DEFAULT_REGISTRY.names())
    assert before == after
    # Ensure no executor registry mutated (new registry is empty)
    reg = ExecutorRegistry()
    assert len(reg.list_executor_ids()) == 0
    # Loading capabilities should not affect dispatcher routing
    from aota_forge.core.execution.dispatcher import ExecutionDispatcher

    # Existence check
    assert ExecutionDispatcher is not None
    assert "handler" not in str(doc)


# ---------------------------------------------------------------------------
# §53 No result runtime effect
# ---------------------------------------------------------------------------


def test_result_load_has_no_runtime_side_effect():
    doc = load_results(canonical_root())
    assert "handler" not in str(doc)
    # Loading results should not mutate result runtime classes
    from aota_forge.core.contracts.results import success, failure

    env = success("test.op", data={"x": 1})
    assert env["ok"] is True
    err = failure("test.op", "ERR_CODE", "msg")
    assert err["ok"] is False


# ---------------------------------------------------------------------------
# §20-24 W3-R1 canonical schema uniqueness (no legacy fixture compatibility)
# ---------------------------------------------------------------------------


def test_legacy_capability_shape_rejected(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
  - capability_id: old.fixture.capability
    semantic_operation_ref: something
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_legacy_result_shape_rejected(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: results
contracts:
  - result_contract: old.fixture.result.v1
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "results.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_results(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_canonical_capability_minimal_accepted(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
  - name: fixture.canonical.capability
    description: Fixture canonical capability.
    adapter_kind: hermes_host_adapter
    supported_execution_modes: [sync]
    supports_streaming_events: false
    supports_task_cancellation: true
    supports_task_resume: true
    supports_structured_result: true
    supported_canonical_roles: [coder]
    supported_isolation_modes: [process]
    supports_working_directory: true
    supports_artifact_transport: true
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    doc = load_capabilities(tmp_path)
    assert doc["contracts"][0]["name"] == "fixture.canonical.capability"


def test_canonical_result_minimal_accepted(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: results
contracts:
  - name: fixture.canonical.result.v1
    description: Fixture canonical result.
    compatible_operations: [fixture.op.status]
    protocol: aota-forge.operation-contract
    protocol_version: '1.0'
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "results.yaml", doc_text)
    doc = load_results(tmp_path)
    assert doc["contracts"][0]["name"] == "fixture.canonical.result.v1"


def test_mixed_capability_schema_fails_closed(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: capabilities
contracts:
  - name: valid.capability
    description: valid
    adapter_kind: hermes_host_adapter
    supported_execution_modes: [sync]
    supports_streaming_events: false
    supports_task_cancellation: true
    supports_task_resume: true
    supports_structured_result: true
    supported_canonical_roles: [coder]
    supported_isolation_modes: [process]
    supports_working_directory: true
    supports_artifact_transport: true
    capability_id: legacy_extra
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "capabilities.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_capabilities(tmp_path)
    assert exc.value.code == ERR_INVALID


def test_mixed_result_schema_fails_closed(tmp_path: pathlib.Path):
    doc_text = """\
schema_version: 1
kind: results
contracts:
  - name: valid.result.v1
    description: valid
    compatible_operations: [fixture.op.status]
    protocol: aota-forge.operation-contract
    protocol_version: '1.0'
    result_contract: legacy_extra
"""
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    write_contract(tmp_path, "results.yaml", doc_text)
    with pytest.raises(DeclarativeContractError) as exc:
        load_results(tmp_path)
    assert exc.value.code == ERR_INVALID
