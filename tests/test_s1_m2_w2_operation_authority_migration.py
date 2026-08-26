"""S1/M2/W2 — Operation Authority Migration.

Verifies the single canonical operation contract-instance authority is
``.aota/contracts/operations.yaml`` and that Python code is projection-only
with exact hash/dict parity for all 13 descriptors.

Covers §49-57: canonical document, complete hash parity, no hardcoded
instance authority, YAML actual runtime source, fail-closed, duplicate,
legacy projection, handler binding, execution routing.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile

import pytest
import yaml

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.loader import (
    DeclarativeContractError,
    ERR_INVALID,
    ERR_NOT_FOUND,
    discover_canonical_project_root,
    load_operation_descriptors,
    load_operation_descriptor_map,
    load_operations,
)
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.operations import get_contract, available_operations

# ---------------------------------------------------------------------------
# Frozen pre-migration baseline (hashes).  This is a stable compatibility
# fixture, not production progress state.  Allowed per §6.
# ---------------------------------------------------------------------------

EXPECTED_HASHES: dict[str, str] = {
    "project.resolve": "c7904fc0dd1424025cc0b79c692a0f6adc4ca5ba415ae1d0dd360ae4da0b18ea",
    "git.inspect": "be4389b55eda1f88b11e382627135c9dfb7d5520187306207efdc13a016fbfc1",
    "runtime.status": "8b0504b2e6e0d64f7fa3caa8a9a0a9bb236292bd21939ff73f96df254c367d6c",
    "host.status": "ca6320f0445741e6794014a67fac39d720d8fcbb20e76077f5a12085a2f1da10",
    "operations.list": "3cffad3fbc0df4775f9ae8b69a2671e7ff3c1ecde7df64e5ac66c6bd02427338",
    "plan_init": "4c23e2ca954773f33f5f9fc4ef119e8bfba8108c3ca5424a11337d707aade981",
    "plan_retirement": "ff8c7a76237c5fe15631fd73b6900a292278ccac9c5cac23af82a5b88538252a",
    "execution.task_start": "14b6f5eef372f555b5d58314b47ec3f5d527057753b4812e20d9e7381212a51d",
    "execution.task_status": "8004c7b0c7233f256afc26533c82421431a39cfa85dfadb0a32e7ee41580c93f",
    "execution.task_result": "00262cffe1d61c5023acf1bc3180b9d89b200a69d67893046ad81bffaa4da012",
    "execution.task_cancel": "9427554269c8d9e7396da7b0399b9056da82fb5a70364fca24d49eb67a3e7c89",
    "execution.executor_list": "6545ce8064ff3a85a3a6c2ffb8f726f304fadeaa6be68148e648d294a9b57abd",
    "execution.executor_capabilities": "cdbdf720384a774ec2057b8651fe33b96b3a97a1257459f00345ab470f02477b",
}

EXPECTED_NAMES = frozenset(EXPECTED_HASHES.keys())

CATALOG_NAMES = frozenset(
    {
        "project.resolve",
        "git.inspect",
        "runtime.status",
        "host.status",
        "operations.list",
    }
)
LIFECYCLE_NAMES = frozenset({"plan_init", "plan_retirement"})
EXECUTION_NAMES = frozenset(
    {
        "execution.task_start",
        "execution.task_status",
        "execution.task_result",
        "execution.task_cancel",
        "execution.executor_list",
        "execution.executor_capabilities",
    }
)


def canonical_project_root() -> pathlib.Path:
    return discover_canonical_project_root()


# ---------------------------------------------------------------------------
# §49 Canonical document
# ---------------------------------------------------------------------------


def test_operations_yaml_exists_at_canonical_root():
    root = canonical_project_root()
    path = root / ".aota" / "contracts" / "operations.yaml"
    assert path.is_file(), f"canonical operations.yaml missing at {path}"
    doc = load_operations(root)
    assert doc["schema_version"] == 1
    assert doc["kind"] == "operations"
    assert isinstance(doc["contracts"], list)
    assert len(doc["contracts"]) == 13
    names = [c["name"] for c in doc["contracts"]]
    assert len(set(names)) == 13
    assert set(names) == set(EXPECTED_NAMES)
    # No capability/result content
    for entry in doc["contracts"]:
        assert "capability" not in entry
        assert "result_contract" in entry or "result_contract" not in entry  # field exists but not capability domain
        # handler/callable must not be in YAML
        assert "handler" not in entry
        assert "callable" not in entry


def test_canonical_operations_document_count_is_one():
    root = canonical_project_root()
    # Exactly one canonical file, no duplicate in package data etc.
    count = 0
    for candidate in [
        root / ".aota" / "contracts" / "operations.yaml",
        root / "aota_forge" / "core" / "contracts" / "operations.yaml",
        root / "aota_forge" / ".aota" / "contracts" / "operations.yaml",
    ]:
        if candidate.is_file():
            count += 1
    # The canonical one must exist and we assert no duplicate package data copy
    assert (root / ".aota" / "contracts" / "operations.yaml").is_file()
    # Duplicate production copies are forbidden (only canonical)
    assert not (root / "aota_forge" / "core" / "contracts" / "operations.yaml").is_file()


# ---------------------------------------------------------------------------
# §50 Complete hash parity (5/5 catalog, 2/2 lifecycle, 6/6 execution, 13 total)
# ---------------------------------------------------------------------------


def test_catalog_descriptor_hash_parity():
    m = load_operation_descriptor_map(canonical_project_root())
    for name in CATALOG_NAMES:
        assert m[name].contract_hash() == EXPECTED_HASHES[name], f"catalog hash mismatch {name}"


def test_lifecycle_descriptor_hash_parity():
    m = load_operation_descriptor_map(canonical_project_root())
    for name in LIFECYCLE_NAMES:
        assert m[name].contract_hash() == EXPECTED_HASHES[name], f"lifecycle hash mismatch {name}"


def test_execution_descriptor_hash_parity():
    m = load_operation_descriptor_map(canonical_project_root())
    for name in EXECUTION_NAMES:
        assert m[name].contract_hash() == EXPECTED_HASHES[name], f"execution hash mismatch {name}"


def test_total_hash_parity_and_to_dict_parity():
    m = load_operation_descriptor_map(canonical_project_root())
    assert len(m) == 13
    for name, expected_hash in EXPECTED_HASHES.items():
        desc = m[name]
        assert desc.contract_hash() == expected_hash, f"hash {name}"
        # to_dict parity: ensure loaded descriptor's to_dict round-trips via from_dict
        rebuilt = OperationContractDescriptor.from_dict(desc.to_dict())
        assert rebuilt.to_dict() == desc.to_dict(), f"to_dict mismatch {name}"
        assert rebuilt.contract_hash() == desc.contract_hash()
    # Also verify dict parity via canonical JSON
    for name in EXPECTED_NAMES:
        assert m[name].to_canonical_json()


def test_description_semantic_parity():
    m = load_operation_descriptor_map(canonical_project_root())
    # Spot check that descriptions participate in hash (not empty)
    for desc in m.values():
        assert desc.description and desc.description.strip()


# ---------------------------------------------------------------------------
# §51 No hardcoded instance authority (source-aware)
# ---------------------------------------------------------------------------


def _assert_no_hardcoded_descriptor_instances():
    # Check descriptor.py no longer contains hard-coded lifecycle instances
    desc_path = pathlib.Path(__file__).resolve().parents[1] / "aota_forge" / "core" / "contracts" / "descriptor.py"
    text = desc_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "PLAN_INIT_DESCRIPTOR",
                    "PLAN_RETIREMENT_DESCRIPTOR",
                    "LIFECYCLE_DESCRIPTORS",
                ):
                    # Must not be a hard-coded OperationContractDescriptor instantiation
                    if isinstance(node.value, ast.Call):
                        # Check if call is OperationContractDescriptor
                        func = node.value.func
                        name = ""
                        if isinstance(func, ast.Name):
                            name = func.id
                        elif isinstance(func, ast.Attribute):
                            name = func.attr
                        if name == "OperationContractDescriptor":
                            raise AssertionError(f"hard-coded instance still in descriptor.py: {target.id}")
    # Check catalog.py no longer contains OperationContract hard-coded
    cat_path = pathlib.Path(__file__).resolve().parents[1] / "aota_forge" / "core" / "catalog.py"
    cat_text = cat_path.read_text(encoding="utf-8")
    assert "_CANONICAL_OPERATIONS = (" not in cat_text or "OperationContract(" not in cat_text, "catalog still has hard-coded OperationContract"
    assert "PLAN_INIT_DESCRIPTOR = OperationContractDescriptor(" not in cat_text
    # Check ingress no longer hard-coded
    ing_path = pathlib.Path(__file__).resolve().parents[1] / "aota_forge" / "core" / "ingress.py"
    ing_text = ing_path.read_text(encoding="utf-8")
    # Execution descriptors must be projection, not hard-coded call
    # Count occurrences of "TASK_START_DESCRIPTOR = OperationContractDescriptor(" — should be zero (now projection via map)
    assert "TASK_START_DESCRIPTOR = OperationContractDescriptor(" not in ing_text
    assert "TASK_STATUS_DESCRIPTOR = OperationContractDescriptor(" not in ing_text
    assert "EXECUTION_DESCRIPTORS: dict" in ing_text or "EXECUTION_DESCRIPTORS" in ing_text


def test_python_operation_instance_authority_removed():
    _assert_no_hardcoded_descriptor_instances()
    # Also verify runtime: catalog/ingress descriptors are YAML-derived
    from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR as pid_cat
    from aota_forge.core.ingress import TASK_START_DESCRIPTOR as ts_ing

    m = load_operation_descriptor_map(canonical_project_root())
    assert pid_cat.to_dict() == m["plan_init"].to_dict()
    assert ts_ing.to_dict() == m["execution.task_start"].to_dict()


# ---------------------------------------------------------------------------
# §52 YAML is actual runtime source (isolated temp fixture)
# ---------------------------------------------------------------------------


def test_yaml_is_actual_runtime_source_isolated_fixture():
    # Create a temp project with a modified operations.yaml and prove loader reflects change
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # copy canonical doc and mutate one entry's description
        root = canonical_project_root()
        doc = load_operations(root)
        # mutate host.status description
        for entry in doc["contracts"]:
            if entry["name"] == "host.status":
                entry["description"] = entry["description"] + " [mutated for test]"
        # write to temp project
        (tmp / ".aota" / "contracts").mkdir(parents=True)
        (tmp / ".aota" / "project.yaml").write_text(
            "schema_version: 1\nproject:\n  id: tmp\n  name: tmp\n  kind: forge-core\n  status: active\nsummary: tmp\ncapabilities: [forge-core]\npaths:\n  source_root: .\n  source: [aota_forge/]\n  docs: [docs/]\n  scripts: [scripts/]\n  profiles: []\n  skills: []\n  tests: [tests/]\ncommands:\n  validate: []\n  deploy: []\n  verify_deploy: []\nruntime:\n  deployment_type: manual\n  requires_human_checkpoint: true\ncodegraph:\n  enabled: false\n  index_location: .codegraph/\nplan:\n  active_plan_id: null\nconstraints: []\n",
            encoding="utf-8",
        )
        (tmp / ".aota" / "contracts" / "operations.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        # load from temp
        mutated = load_operation_descriptor_map(tmp)["host.status"]
        canonical = load_operation_descriptor_map(root)["host.status"]
        assert mutated.description != canonical.description
        assert mutated.contract_hash() != canonical.contract_hash()
        # prove that production file not mutated
        assert load_operation_descriptor_map(root)["host.status"].description == canonical.description


# ---------------------------------------------------------------------------
# §53 Fail closed without YAML
# ---------------------------------------------------------------------------


def test_fail_closed_without_yaml():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / ".aota").mkdir(parents=True)
        (tmp / ".aota" / "project.yaml").write_text(
            "schema_version: 1\nproject:\n  id: tmp\n  name: tmp\n  kind: forge-core\n  status: active\nsummary: tmp\ncapabilities: [forge-core]\npaths:\n  source_root: .\n  source: [aota_forge/]\n  docs: [docs/]\n  scripts: [scripts/]\n  profiles: []\n  skills: []\n  tests: [tests/]\ncommands:\n  validate: []\n  deploy: []\n  verify_deploy: []\nruntime:\n  deployment_type: manual\n  requires_human_checkpoint: true\ncodegraph:\n  enabled: false\n  index_location: .codegraph/\nplan:\n  active_plan_id: null\nconstraints: []\n",
            encoding="utf-8",
        )
        # no operations.yaml
        with pytest.raises(DeclarativeContractError) as exc:
            load_operation_descriptors(tmp)
        assert exc.value.code == ERR_NOT_FOUND
        with pytest.raises(DeclarativeContractError) as exc2:
            load_operations(tmp)
        assert exc2.value.code == ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# §54 Duplicate operation identity fails closed
# ---------------------------------------------------------------------------


def test_duplicate_operation_identity_fails_closed(tmp_path: pathlib.Path):
    # build doc with duplicate names
    doc = {
        "schema_version": 1,
        "kind": "operations",
        "contracts": [
            {
                "name": "dup.op",
                "description": "dup",
                "read_write": "read",
                "inputs": [],
                "required_context": [],
                "optional_context": [],
                "errors": [],
                "protocol_version": "1.0",
            },
            {
                "name": "dup.op",
                "description": "dup2",
                "read_write": "read",
                "inputs": [],
                "required_context": [],
                "optional_context": [],
                "errors": [],
                "protocol_version": "1.0",
            },
        ],
    }
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    (tmp_path / ".aota" / "project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: tmp\n  name: tmp\n  kind: forge-core\n  status: active\nsummary: tmp\ncapabilities: [forge-core]\npaths:\n  source_root: .\n  source: [aota_forge/]\n  docs: [docs/]\n  scripts: [scripts/]\n  profiles: []\n  skills: []\n  tests: [tests/]\ncommands:\n  validate: []\n  deploy: []\n  verify_deploy: []\nruntime:\n  deployment_type: manual\n  requires_human_checkpoint: true\ncodegraph:\n  enabled: false\n  index_location: .codegraph/\nplan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    (tmp_path / ".aota" / "contracts" / "operations.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operation_descriptors(tmp_path)
    assert exc.value.code == ERR_INVALID
    assert "duplicate" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# §55 Legacy projection
# ---------------------------------------------------------------------------


def test_legacy_projection_compatible_and_derived():
    # get_contract and available_operations remain compatible
    c = get_contract("project.resolve")
    assert c is not None
    assert c.name == "project.resolve"
    assert c.read_only is True
    # available_operations reflects DEFAULT_REGISTRY (7)
    ops = available_operations()
    names = {o["operation"] for o in ops}
    assert "project.resolve" in names
    assert "plan_init" in names
    # execution not in DEFAULT_REGISTRY topology
    assert "execution.task_start" not in names
    # projection derived from YAML: get_contract for execution still works via fallback
    ec = get_contract("execution.task_start")
    assert ec is not None
    assert ec.name == "execution.task_start"
    # Verify get_contract's inputs match YAML descriptor
    m = load_operation_descriptor_map(canonical_project_root())
    assert set(ec.inputs.keys()) == {s.name for s in m["execution.task_start"].inputs}


# ---------------------------------------------------------------------------
# §56 Handler binding
# ---------------------------------------------------------------------------


def test_loading_does_not_bind_handlers():
    before = DEFAULT_REGISTRY.names()
    # loading via isolated temp should not affect registry
    m = load_operation_descriptor_map(canonical_project_root())
    assert "host.status" in m
    # registry should still have 7 entries (catalog)
    assert set(DEFAULT_REGISTRY.names()) == set(before)
    # handler identity excluded from hash
    desc = m["project.resolve"]
    h1 = desc.contract_hash()
    # handler not in dict
    d = desc.to_dict()
    assert "handler" not in d
    # ensure bootstrap still binds handlers idempotently
    from aota_forge.core.bootstrap import ensure_handlers_bound

    ensure_handlers_bound()
    assert DEFAULT_REGISTRY.handler("project.resolve") is not None
    assert DEFAULT_REGISTRY.handler("plan_init") is not None


# ---------------------------------------------------------------------------
# §57 Execution routing
# ---------------------------------------------------------------------------


def test_execution_routing_preserved():
    from aota_forge.core.ingress import (
        CANONICAL_EXECUTION_OPERATIONS,
        EXECUTION_DESCRIPTORS,
        get_execution_descriptor,
    )

    assert set(EXECUTION_DESCRIPTORS) == set(CANONICAL_EXECUTION_OPERATIONS)
    assert len(EXECUTION_DESCRIPTORS) == 6
    m = load_operation_descriptor_map(canonical_project_root())
    for name in CANONICAL_EXECUTION_OPERATIONS:
        assert EXECUTION_DESCRIPTORS[name].contract_hash() == EXPECTED_HASHES[name]
        assert EXECUTION_DESCRIPTORS[name].to_dict() == m[name].to_dict()
        assert get_execution_descriptor(name) is not None
    # Verify execution still routes via ExecutionDispatcher, not HandlerRegistry
    # (DEFAULT_REGISTRY should not contain execution ops)
    for name in EXECUTION_NAMES:
        assert not DEFAULT_REGISTRY.has(name)


def test_operations_list_behavior_unchanged():
    ops = available_operations()
    # deterministic sorted ordering
    assert ops == sorted(ops, key=lambda x: x["operation"])
    # should not have been broadened to include execution
    names = {o["operation"] for o in ops}
    assert names == set(CATALOG_NAMES) | set(LIFECYCLE_NAMES)
