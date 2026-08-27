"""S1/M3/W1 - Existing Forge Mapping Proof.

The mapping matrix is derived from the canonical operations YAML through the
production loader.  Test-local descriptors are deliberately not declared:
the only canonical descriptor instances used here are loader/catalog/registry
projections.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from aota_forge.core.bootstrap import ensure_handlers_bound
from aota_forge.core.catalog import get_catalog_descriptor
from aota_forge.core.contracts.loader import (
    discover_canonical_project_root,
    load_operation_descriptor_map,
    load_operations,
    load_results,
)
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY, HandlerRegistry
from aota_forge.core.ingress import MutationIngressRequest, execute, execute_mutation
from aota_forge.core.regression.fixtures import TempWorkspaceFixture
from aota_forge.core.contracts.mutation import MutationEffect

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_m4_2_m4_4_integration import (  # noqa: E402
    _issue_authorization,
    _lifecycle_fixture,
    _make_intent,
    _plan_init_request,
    _retirement_request,
)
from aota_forge.core.transitions import capture_retirement_snapshot  # noqa: E402


MAPPED_OPERATIONS = (
    "project.resolve",
    "git.inspect",
    "runtime.status",
    "host.status",
    "plan_init",
    "plan_retirement",
)


def _canonical_root() -> Path:
    return discover_canonical_project_root()


def _normalized_yaml_entry(entry: dict) -> dict:
    normalized = dict(entry)
    normalized["inputs"] = [
        {"name": item["name"], "type": item["type"]}
        for item in sorted(entry["inputs"], key=lambda item: (item["name"], item["type"]))
    ]
    return normalized


def _mapping_matrix(project_root: Path) -> list[dict[str, object]]:
    """Build and validate the W1 matrix from live loader output."""
    document = load_operations(project_root)
    entries = {entry["name"]: entry for entry in document["contracts"]}
    descriptors = load_operation_descriptor_map(project_root)
    missing = sorted(set(MAPPED_OPERATIONS) - set(entries) - set(descriptors))
    if missing:
        raise AssertionError(f"missing canonical operations: {missing}")

    result_entries = {entry["name"]: entry for entry in load_results(project_root)["contracts"]}
    ensure_handlers_bound()
    matrix: list[dict[str, object]] = []
    for operation in MAPPED_OPERATIONS:
        raw = entries.get(operation)
        descriptor = descriptors.get(operation)
        if raw is None or descriptor is None:
            raise AssertionError(f"missing canonical operation: {operation}")

        # This compares the model projection with the live YAML entry without
        # creating a second descriptor authority in the test.
        assert descriptor.to_dict() == _normalized_yaml_entry(raw)
        catalog_descriptor = get_catalog_descriptor(operation)
        assert catalog_descriptor is not None
        assert catalog_descriptor.to_dict() == descriptor.to_dict()
        registry_descriptor = DEFAULT_REGISTRY.get(operation)
        assert registry_descriptor is not None
        assert registry_descriptor.to_dict() == descriptor.to_dict()
        assert callable(DEFAULT_REGISTRY.handler(operation))

        if descriptor.read_write == "read":
            assert descriptor.mutation_scope is None
            assert descriptor.required_authority is None
            assert descriptor.approval_required is False
            assert descriptor.decision_required is False
            assert descriptor.valid_predecessor_state is None
            assert descriptor.valid_successor_state is None
            assert descriptor.subject_revision_precondition is False
            assert descriptor.external_authority_precondition is False
            assert descriptor.result_contract is None
            assert descriptor.idempotency in (None, "read")
        else:
            assert descriptor.read_write == "write"
            assert isinstance(descriptor.required_authority, str)
            assert descriptor.required_authority.strip()
            assert descriptor.approval_required is True
            assert descriptor.decision_required is True
            assert isinstance(descriptor.valid_predecessor_state, str)
            assert isinstance(descriptor.valid_successor_state, str)
            assert descriptor.subject_revision_precondition is True
            assert descriptor.external_authority_precondition is True
            assert isinstance(descriptor.idempotency, str)
            assert "same intent" in descriptor.idempotency
            assert "conflict" in descriptor.idempotency
            assert isinstance(descriptor.result_contract, str)
            result_contract = result_entries.get(descriptor.result_contract)
            assert result_contract is not None
            assert operation in result_contract["compatible_operations"]

        matrix.append(
            {
                "operation": operation,
                "descriptor_source": str(project_root / ".aota" / "contracts" / "operations.yaml"),
                "read_write": descriptor.read_write,
                "authority": descriptor.required_authority,
                "state_transition": (
                    descriptor.valid_predecessor_state,
                    descriptor.valid_successor_state,
                ),
                "idempotency": descriptor.idempotency,
                "result_contract": descriptor.result_contract,
                "runtime_binding": True,
            }
        )
    return matrix


def _write_operations_fixture(tmp_path: Path, document: dict) -> Path:
    (tmp_path / ".aota" / "contracts").mkdir(parents=True)
    root = _canonical_root()
    shutil.copy2(root / ".aota" / "project.yaml", tmp_path / ".aota" / "project.yaml")
    (tmp_path / ".aota" / "contracts" / "operations.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    shutil.copy2(root / ".aota" / "contracts" / "results.yaml", tmp_path / ".aota" / "contracts" / "results.yaml")
    return tmp_path


def _assert_runtime_binding(
    operation: str, canonical_descriptor, registry: HandlerRegistry = DEFAULT_REGISTRY
) -> None:
    bound = registry.get(operation)
    assert bound is not None, f"runtime binding missing: {operation}"
    assert bound.to_dict() == canonical_descriptor.to_dict(), f"runtime binding mismatch: {operation}"
    assert callable(registry.handler(operation))


def _bound_request(request, authorization):
    return replace(
        request,
        external_authority_precondition=authorization.authorization.external_authority_precondition,
        normalized_plan_digest=authorization.authorization.normalized_plan_digest,
    )


def _run_mutation(fixture, request):
    return execute_mutation(
        MutationIngressRequest(
            operation=request.intent.operation,
            store=fixture.store,
            request=request,
        )
    )


def test_mapping_matrix_covers_live_yaml_semantic_shapes():
    matrix = _mapping_matrix(_canonical_root())

    assert [row["operation"] for row in matrix] == list(MAPPED_OPERATIONS)
    assert all(row["runtime_binding"] for row in matrix)
    assert {row["read_write"] for row in matrix} == {"read", "write"}
    assert all(row["descriptor_source"].endswith(".aota/contracts/operations.yaml") for row in matrix)


def test_live_yaml_authority_is_exercised_without_mutating_canonical_yaml(tmp_path: Path):
    root = _canonical_root()
    document = deepcopy(load_operations(root))
    for entry in document["contracts"]:
        if entry["name"] == "host.status":
            entry["description"] += " [isolated W1 authority probe]"

    isolated = _write_operations_fixture(tmp_path, document)
    isolated_descriptor = load_operation_descriptor_map(isolated)["host.status"]
    canonical_descriptor = load_operation_descriptor_map(root)["host.status"]

    assert isolated_descriptor.description != canonical_descriptor.description
    assert isolated_descriptor.contract_hash() != canonical_descriptor.contract_hash()
    assert load_operation_descriptor_map(root)["host.status"].description == canonical_descriptor.description


def test_missing_canonical_operation_fails_mapping_proof(tmp_path: Path):
    document = deepcopy(load_operations(_canonical_root()))
    document["contracts"] = [
        entry for entry in document["contracts"] if entry["name"] != "project.resolve"
    ]
    isolated = _write_operations_fixture(tmp_path, document)

    with pytest.raises(AssertionError, match="missing canonical operation"):
        _mapping_matrix(isolated)


def test_runtime_binding_mismatch_is_detected_not_authorized():
    operation = "git.inspect"
    canonical_descriptor = get_catalog_descriptor(operation)
    assert canonical_descriptor is not None
    isolated_registry = HandlerRegistry()
    altered_local_value = replace(canonical_descriptor, description="untrusted local redeclaration")
    isolated_registry.bind(altered_local_value, lambda _context: {"data": {}})

    with pytest.raises(AssertionError, match="runtime binding mismatch"):
        _assert_runtime_binding(operation, canonical_descriptor, isolated_registry)


def test_read_operations_dispatch_through_existing_ingress_and_bindings():
    with TempWorkspaceFixture(prefix="m3-w1-read-") as workspace:
        workspace.create_project("p1")
        registry = workspace.create_registry("w1")
        project = workspace.workdir / "p1"
        subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
        (project / "README").write_text("isolated mapping proof\n", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=project, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=W1",
                "-c",
                "user.email=w1@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            cwd=project,
            check=True,
        )

        params_by_operation = {
            "project.resolve": {"workspace_id": "w1", "registry_path": str(registry), "project_id": "p1"},
            "git.inspect": {"workspace_id": "w1", "registry_path": str(registry), "project_id": "p1"},
            "runtime.status": {"pid": os.getpid()},
            "host.status": {},
        }
        for operation, params in params_by_operation.items():
            result = execute(operation, params)
            descriptor = get_catalog_descriptor(operation)
            assert descriptor is not None
            assert result["ok"] is True
            assert result["operation"] == operation
            assert result["audit"]["contract_hash"] == descriptor.contract_hash()
            assert result["audit"]["handler"] == "success"


def test_plan_init_yaml_descriptor_dispatches_and_replays_via_ingress():
    descriptor = get_catalog_descriptor("plan_init")
    assert descriptor is not None
    _assert_runtime_binding("plan_init", descriptor)

    with _lifecycle_fixture("m3-w1-plan-init") as fixture:
        intent = _make_intent(
            "plan_init",
            fixture.plan_ref,
            "m3-w1-plan-init",
            project_id="p1",
            requested_state="initialized",
        )
        authorization = _issue_authorization(fixture, descriptor=descriptor, intent=intent)
        request = _bound_request(
            _plan_init_request(fixture, intent=intent, lease=authorization.lease), authorization
        )

        first = _run_mutation(fixture, request)
        replay = _run_mutation(fixture, request)

        assert first["ok"] is True
        assert first["lifecycle_code"] == "PLAN_INIT_APPLIED"
        assert first["mutation_effect"] == MutationEffect.APPLIED_VERIFIED.value
        assert replay["lifecycle_code"] == "PLAN_INIT_REPLAYED"
        assert replay["mutation_effect"] == MutationEffect.REPLAYED_VERIFIED.value
        assert replay["audit"]["contract_hash"] == descriptor.contract_hash()
        assert fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "initialized"
        assert fixture.store.current_revision(fixture.plan_ref).revision_number == 2


def test_plan_retirement_yaml_descriptor_dispatches_and_replays_via_ingress():
    descriptor = get_catalog_descriptor("plan_retirement")
    assert descriptor is not None
    _assert_runtime_binding("plan_retirement", descriptor)

    with _lifecycle_fixture("m3-w1-plan-retirement", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "m3-w1-plan-retirement",
            retirement_kind="abandoned",
        )
        authorization = _issue_authorization(fixture, descriptor=descriptor, intent=intent)
        request = _bound_request(
            _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease), authorization
        )

        first = _run_mutation(fixture, request)
        replay = _run_mutation(fixture, request)

        assert first["ok"] is True
        assert first["lifecycle_code"] == "RETIREMENT_APPLIED"
        assert first["data"]["resulting_plan_state"] == "cancelled"
        assert first["mutation_effect"] == MutationEffect.APPLIED_VERIFIED.value
        assert replay["lifecycle_code"] == "RETIREMENT_REPLAYED"
        assert replay["mutation_effect"] == MutationEffect.REPLAYED_VERIFIED.value
        assert replay["audit"]["contract_hash"] == descriptor.contract_hash()
        assert fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "cancelled"
        assert fixture.store.current_revision(fixture.plan_ref).revision_number == 2
