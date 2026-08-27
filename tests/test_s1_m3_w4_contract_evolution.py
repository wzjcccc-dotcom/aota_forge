"""S1/M3/W4 - contract evolution and adapter-private metadata boundaries.

These tests exercise existing production seams only.  Fixtures are temporary or
test-local; no canonical declarative documents or production runtime state are
modified.
"""

from __future__ import annotations

from dataclasses import replace
import pathlib
from typing import Any

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.adapters.hermes.executor import hermes_output_to_canonical_result
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.loader import (
    DECLARATIVE_DOCUMENT_SCHEMA_VERSION,
    ERR_INVALID,
    ERR_UNSUPPORTED_VERSION,
    DeclarativeContractError,
    load_document,
    load_operation_descriptors,
    load_results,
)
from aota_forge.core.contracts.version import (
    OPERATION_CONTRACT_PROTOCOL,
    PROTOCOL_VERSION,
    classify_evolution,
    is_breaking_evolution,
    is_compatible_evolution,
)
from aota_forge.core.contracts.vocabulary import operation_semantic_identity_of
from aota_forge.core.execution import CanonicalTaskState, ExecutionPackage
from aota_forge.core.execution.dispatcher import CapabilityMismatchError, ExecutionDispatcher
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.results import CanonicalResult


DOMAIN_DOCUMENTS = (
    ("operations", "operations.yaml"),
    ("capabilities", "capabilities.yaml"),
    ("results", "results.yaml"),
)


def write_document(root: pathlib.Path, filename: str, text: str) -> None:
    contract_root = root / ".aota" / "contracts"
    contract_root.mkdir(parents=True, exist_ok=True)
    (contract_root / filename).write_text(text, encoding="utf-8")


def operations_document(*, protocol_version: str = PROTOCOL_VERSION, schema_version: int = 1) -> str:
    return f'''schema_version: {schema_version}
kind: operations
contracts:
  - name: fixture.op.status
    description: Read a fixture status.
    read_write: read
    inputs: []
    required_context: []
    optional_context: []
    errors: []
    protocol_version: "{protocol_version}"
'''


def results_document(*, protocol_version: str = PROTOCOL_VERSION, schema_version: int = 1) -> str:
    return f'''schema_version: {schema_version}
kind: results
contracts:
  - name: fixture.result.v1
    description: Fixture result.
    compatible_operations: [fixture.op.status]
    protocol: {OPERATION_CONTRACT_PROTOCOL}
    protocol_version: "{protocol_version}"
'''


@pytest.mark.parametrize(("kind", "filename"), DOMAIN_DOCUMENTS)
def test_supported_schema_version_is_accepted_for_each_domain(
    tmp_path: pathlib.Path, kind: str, filename: str
) -> None:
    write_document(
        tmp_path,
        filename,
        f"schema_version: {DECLARATIVE_DOCUMENT_SCHEMA_VERSION}\n"
        f"kind: {kind}\ncontracts: []\n",
    )

    document = load_document(tmp_path, kind)

    assert document["schema_version"] == DECLARATIVE_DOCUMENT_SCHEMA_VERSION
    assert document["kind"] == kind


@pytest.mark.parametrize(("kind", "filename"), DOMAIN_DOCUMENTS)
def test_unknown_schema_version_fails_closed_for_each_domain(
    tmp_path: pathlib.Path, kind: str, filename: str
) -> None:
    write_document(tmp_path, filename, f"schema_version: 99\nkind: {kind}\ncontracts: []\n")

    with pytest.raises(DeclarativeContractError) as exc_info:
        load_document(tmp_path, kind)

    assert exc_info.value.code == ERR_UNSUPPORTED_VERSION


def test_unknown_operation_protocol_version_fails_at_descriptor_loader(
    tmp_path: pathlib.Path,
) -> None:
    write_document(tmp_path, "operations.yaml", operations_document(protocol_version="999.0"))

    with pytest.raises(DeclarativeContractError) as exc_info:
        load_operation_descriptors(tmp_path)

    assert exc_info.value.code == ERR_INVALID
    assert "unsupported operation protocol_version" in str(exc_info.value)


def test_unknown_result_protocol_version_fails_at_result_loader(
    tmp_path: pathlib.Path,
) -> None:
    write_document(tmp_path, "results.yaml", results_document(protocol_version="999.0"))

    with pytest.raises(DeclarativeContractError) as exc_info:
        load_results(tmp_path)

    assert exc_info.value.code == ERR_INVALID
    assert "active version" in str(exc_info.value)


def make_descriptor(*, required_context: tuple[str, ...] = ("principal",)) -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name="fixture.op.evolution",
        description="Evolution fixture.",
        inputs=(InputSpec("subject", "str"),),
        required_context=required_context,
        optional_context=(),
        errors=("E_EXISTING",),
        protocol_version=PROTOCOL_VERSION,
    )


def test_additive_evolution_keeps_semantic_identity_and_changes_revision() -> None:
    base = make_descriptor()
    additive = replace(base, errors=base.errors + ("E_ADDITIVE",))

    assert classify_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="add_new_error_code_without_changing_existing_meaning",
    ) == "compatible"
    assert is_compatible_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="add_new_error_code_without_changing_existing_meaning",
    ) is True
    assert is_breaking_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="add_new_error_code_without_changing_existing_meaning",
    ) is False
    assert additive.protocol_version == base.protocol_version == PROTOCOL_VERSION
    assert additive.contract_hash() != base.contract_hash()
    assert operation_semantic_identity_of(additive) == operation_semantic_identity_of(base)


def test_breaking_evolution_is_incompatible_and_unknown_is_not_compatible() -> None:
    base = make_descriptor()
    breaking = replace(base, required_context=())

    assert breaking.contract_hash() != base.contract_hash()
    assert classify_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="remove_required_field",
    ) == "breaking"
    assert is_compatible_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="remove_required_field",
    ) is False
    assert is_breaking_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="remove_required_field",
    ) is True
    assert classify_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="future_unknown_kind",
    ) == "unknown"
    assert is_compatible_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="future_unknown_kind",
    ) is False
    assert is_breaking_evolution(
        family=OPERATION_CONTRACT_PROTOCOL,
        change_kind="future_unknown_kind",
    ) is True


def make_package(
    task_id: str,
    *,
    working_context: dict[str, Any] | None = None,
    result_expectations: dict[str, Any] | None = None,
    capability_requirements: dict[str, Any] | None = None,
) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="W4 metadata isolation probe",
        working_context=working_context or {},
        result_expectations=result_expectations or {},
        capability_requirements=capability_requirements or {},
        idempotency_key=f"idem-{task_id}",
        correlation_id=f"corr-{task_id}",
    )


@pytest.mark.parametrize("private_key", ("hermes_session", "remote_poll_cursor"))
def test_private_metadata_key_cannot_become_capability_requirement(private_key: str) -> None:
    adapter = ReferenceFakeExecutorAdapter()
    registry = ExecutorRegistry()
    registry.register(adapter)
    dispatcher = ExecutionDispatcher(registry)
    package = make_package(
        f"w4-unknown-{private_key}",
        capability_requirements={private_key: "private-value"},
    )

    with pytest.raises(CapabilityMismatchError) as exc_info:
        dispatcher.dispatch(package, target_executor_id=adapter.capabilities().executor_id)

    assert any("Unknown capability requirement key" in reason for reason in exc_info.value.reasons)
    assert adapter.validation_count == 0
    assert adapter.dispatch_count == 0
    assert dispatcher.has_route(package.canonical_task_id) is False


def test_existing_generic_mappings_preserve_private_data_without_changing_core_identity() -> None:
    base = make_package("w4-mapping-base")
    package = make_package(
        "w4-mapping-private",
        working_context={
            "repo_root": "/workspace/aota_forge",
            "remote_provider_session": "session-1",
            "remote_poll_cursor": "cursor-1",
        },
        result_expectations={"remote_queue_internal_state": {"queued": True}},
    )
    adapter = ReferenceFakeExecutorAdapter(auto_complete=True)
    registry = ExecutorRegistry()
    registry.register(adapter)
    dispatcher = ExecutionDispatcher(registry)

    dispatch_result = dispatcher.dispatch(package)
    route = dispatcher.get_route(package.canonical_task_id)
    result = dispatcher.result(package.canonical_task_id)

    assert package.intent_fingerprint == base.intent_fingerprint
    assert package.package_fingerprint() != base.package_fingerprint()
    assert dispatch_result.initial_state == CanonicalTaskState.COMPLETED
    assert route.canonical_task_id == package.canonical_task_id
    assert route.executor_id == adapter.capabilities().executor_id
    assert route.adapter_handle != route.canonical_task_id
    assert "remote_provider_session" not in route.to_dict()
    assert result.canonical_task_id == package.canonical_task_id
    assert result.status == "completed"
    assert package.working_context["remote_provider_session"] == "session-1"
    assert package.result_expectations["remote_queue_internal_state"] == {"queued": True}


def test_hermes_projection_strips_private_keys_but_preserves_generic_result_data() -> None:
    result = hermes_output_to_canonical_result(
        {
            "status": "done",
            "result_data": {
                "answer": "ok",
                "hermes_session": "private-session",
                "nested": {
                    "hermes_worker": "private-worker",
                    "remote_provider_request_id": "request-1",
                },
            },
            "execution_stats": {
                "hermes_trace": "private-trace",
                "remote_latency_ms": 12,
            },
            "output_artifacts": [
                {"path": "result.txt", "hermes_task": "private-task", "provider_digest": "digest-1"}
            ],
        },
        canonical_task_id="w4-hermes-result",
        correlation_id="corr-w4-hermes-result",
    )

    assert isinstance(result, CanonicalResult)
    assert result.status == "completed"
    assert result.result_data == {
        "answer": "ok",
        "nested": {"remote_provider_request_id": "request-1"},
    }
    assert result.execution_stats == {"remote_latency_ms": 12}
    assert result.output_artifacts == ({"path": "result.txt", "provider_digest": "digest-1"},)
    assert "hermes_session" not in result.to_dict()
