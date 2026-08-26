"""S1/M2/W1 — Declarative Layout & Loader Foundation.

Infrastructure-only tests for the strict safe declarative contract
loader.  All fixtures are temporary files under pytest ``tmp_path``;
no production canonical documents are created and no registry or
runtime state is mutated.

Covers the W1 required tests: document envelope, safe YAML,
domain scoping, missing domain, operation model conversion,
handler isolation, mapping-key order, version-domain separation,
generic protocol non-activation and production progress-state absence.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.contracts.loader import (
    DECLARATIVE_DOCUMENT_SCHEMA_VERSION,
    ERR_INVALID,
    ERR_KIND,
    ERR_NOT_FOUND,
    ERR_UNSUPPORTED_VERSION,
    FIXED_DEFAULT_CONTRACT_ROOT_NAME,
    DeclarativeContractError,
    load_capabilities,
    load_document,
    load_operation_descriptors,
    load_operations,
    load_results,
    resolve_contract_root,
)
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.version import (
    GENERIC_TARGET_PROTOCOL_ACTIVE,
    PROTOCOL_VERSION,
)

VALID_OPERATIONS_DOC = """\
schema_version: 1
kind: operations
contracts:
  - name: fixture.op.status
    description: Read a fixture status.
    read_write: read
    inputs: []
    required_context: []
    optional_context: []
    errors: []
    protocol_version: "1.0"
"""

VALID_CAPABILITIES_DOC = """\
schema_version: 1
kind: capabilities
contracts:
  - capability_id: fixture.capability
    semantic_operation_ref: fixture.op.status
"""

VALID_RESULTS_DOC = """\
schema_version: 1
kind: results
contracts:
  - result_contract: fixture.result.v1
"""


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


def assert_error(exc_info: pytest.ExceptionInfo, code: str) -> None:
    assert isinstance(exc_info.value, DeclarativeContractError)
    assert exc_info.value.code == code


def assert_no_callable(value: object) -> None:
    if callable(value):
        raise AssertionError(f"callable value found: {value!r}")
    if isinstance(value, dict):
        for item in value.values():
            assert_no_callable(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_callable(item)


# ---------------------------------------------------------------------------
# §31 Document envelope
# ---------------------------------------------------------------------------


def test_valid_document_envelope_accepted(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    document = load_operations(tmp_path)
    assert document["schema_version"] == DECLARATIVE_DOCUMENT_SCHEMA_VERSION
    assert document["kind"] == "operations"
    assert isinstance(document["contracts"], list)
    descriptors = load_operation_descriptors(tmp_path)
    assert len(descriptors) == 1
    assert descriptors[0].name == "fixture.op.status"


def test_missing_schema_version_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "kind: operations\ncontracts: []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


def test_unsupported_schema_version_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 2\nkind: operations\ncontracts: []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_UNSUPPORTED_VERSION)


def test_wrong_type_schema_version_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        'schema_version: "1"\nkind: operations\ncontracts: []\n',
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_UNSUPPORTED_VERSION)


def test_missing_kind_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\ncontracts: []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_KIND)


def test_domain_kind_mismatch_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: capabilities\ncontracts: []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_KIND)


def test_unknown_document_field_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts: []\nhandler: some_callable\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


# ---------------------------------------------------------------------------
# §32 Strict safe YAML
# ---------------------------------------------------------------------------


def test_normal_safe_yaml_accepted(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    document = load_operations(tmp_path)
    assert document["kind"] == "operations"


def test_duplicate_mapping_key_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nschema_version: 2\nkind: operations\ncontracts: []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


def test_nested_duplicate_mapping_key_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts:\n"
        "  - name: a\n    name: b\n    description: d\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


def test_unsafe_object_yaml_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts: !!python/object/apply:os.system []\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


def test_invalid_yaml_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts: [\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_INVALID)


# ---------------------------------------------------------------------------
# §33 Domain scoping
# ---------------------------------------------------------------------------


def test_load_operations_loads_operations_document_only(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    write_contract(tmp_path, "capabilities.yaml", VALID_CAPABILITIES_DOC)
    write_contract(tmp_path, "results.yaml", VALID_RESULTS_DOC)
    document = load_operations(tmp_path)
    assert document["kind"] == "operations"
    assert document["contracts"][0]["name"] == "fixture.op.status"


def test_load_capabilities_does_not_implicitly_require_operations_or_results(
    tmp_path: pathlib.Path,
) -> None:
    write_contract(tmp_path, "capabilities.yaml", VALID_CAPABILITIES_DOC)
    document = load_capabilities(tmp_path)
    assert document["kind"] == "capabilities"


def test_load_results_does_not_implicitly_require_operations_or_capabilities(
    tmp_path: pathlib.Path,
) -> None:
    write_contract(tmp_path, "results.yaml", VALID_RESULTS_DOC)
    document = load_results(tmp_path)
    assert document["kind"] == "results"


def test_loader_has_no_milestone_state_parameter(tmp_path: pathlib.Path) -> None:
    import inspect as _inspect

    signature = _inspect.signature(load_operations)
    assert signature.parameters
    for name in ("milestone", "stage", "progress"):
        assert name not in signature.parameters


# ---------------------------------------------------------------------------
# §34 Missing domain document
# ---------------------------------------------------------------------------


def test_missing_domain_document_fails_closed(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "capabilities.yaml", VALID_CAPABILITIES_DOC)
    with pytest.raises(DeclarativeContractError) as exc:
        load_operations(tmp_path)
    assert_error(exc, ERR_NOT_FOUND)


# ---------------------------------------------------------------------------
# §35 Operation model conversion
# ---------------------------------------------------------------------------


def test_operation_fixture_converts_to_existing_descriptor(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    (descriptor,) = load_operation_descriptors(tmp_path)
    assert isinstance(descriptor, OperationContractDescriptor)
    assert descriptor.name == "fixture.op.status"
    assert descriptor.description == "Read a fixture status."
    assert descriptor.inputs == ()
    assert descriptor.protocol_version == "1.0"
    assert descriptor.to_canonical_json()
    assert isinstance(descriptor.contract_hash(), str)


def test_incomplete_write_descriptor_rejected_by_existing_validation(
    tmp_path: pathlib.Path,
) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts:\n"
        "  - name: fixture.op.incomplete\n"
        "    description: Incomplete write fixture.\n"
        "    read_write: write\n",
    )
    with pytest.raises(ValueError):
        load_operation_descriptors(tmp_path)


def test_unknown_operation_entry_field_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 1\nkind: operations\ncontracts:\n"
        "  - name: fixture.op.bad\n    description: d\n"
        "    handler: some_callable\n",
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operation_descriptors(tmp_path)
    assert_error(exc, ERR_INVALID)


# ---------------------------------------------------------------------------
# §36 Handler isolation
# ---------------------------------------------------------------------------


def test_loading_does_not_bind_handlers(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    before = DEFAULT_REGISTRY.names()
    document = load_operations(tmp_path)
    (descriptor,) = load_operation_descriptors(tmp_path)
    assert_no_callable(document)
    assert_no_callable(descriptor.to_dict())
    assert DEFAULT_REGISTRY.names() == before
    assert DEFAULT_REGISTRY.get("fixture.op.status") is None


# ---------------------------------------------------------------------------
# §37 Mapping-key order non-semantic
# ---------------------------------------------------------------------------


def test_mapping_key_order_yields_equivalent_document(tmp_path: pathlib.Path) -> None:
    first = VALID_OPERATIONS_DOC
    second = """\
kind: operations
contracts:
  - protocol_version: "1.0"
    read_write: read
    required_context: []
    errors: []
    optional_context: []
    inputs: []
    description: Read a fixture status.
    name: fixture.op.status
schema_version: 1
"""
    write_contract(tmp_path, "operations.yaml", first)
    document_a = load_operations(tmp_path)
    write_contract(tmp_path, "operations.yaml", second)
    document_b = load_operations(tmp_path)
    assert document_a == document_b
    (descriptor_a,) = load_operation_descriptors(tmp_path)
    write_contract(tmp_path, "operations.yaml", first)
    (descriptor_b,) = load_operation_descriptors(tmp_path)
    assert descriptor_a.to_canonical_json() == descriptor_b.to_canonical_json()
    assert descriptor_a.contract_hash() == descriptor_b.contract_hash()


# ---------------------------------------------------------------------------
# §38 Version-domain separation
# ---------------------------------------------------------------------------


def test_document_schema_version_distinct_from_protocol_version(
    tmp_path: pathlib.Path,
) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    document = load_operations(tmp_path)
    (descriptor,) = load_operation_descriptors(tmp_path)
    assert document["schema_version"] == DECLARATIVE_DOCUMENT_SCHEMA_VERSION
    assert descriptor.protocol_version == PROTOCOL_VERSION == "1.0"
    assert str(DECLARATIVE_DOCUMENT_SCHEMA_VERSION) != PROTOCOL_VERSION


def test_envelope_version_rejection_does_not_alter_protocol_authority(
    tmp_path: pathlib.Path,
) -> None:
    write_contract(
        tmp_path,
        "operations.yaml",
        "schema_version: 99\nkind: operations\ncontracts:\n"
        "  - name: fixture.op.status\n"
        "    description: d\n"
        "    read_write: read\n"
        "    inputs: []\n"
        "    required_context: []\n"
        "    optional_context: []\n"
        "    errors: []\n"
        '    protocol_version: "1.0"\n',
    )
    with pytest.raises(DeclarativeContractError) as exc:
        load_operation_descriptors(tmp_path)
    assert_error(exc, ERR_UNSUPPORTED_VERSION)
    assert PROTOCOL_VERSION == "1.0"


# ---------------------------------------------------------------------------
# §39 Generic protocol not activated
# ---------------------------------------------------------------------------


def test_generic_protocol_not_activated_by_loader(tmp_path: pathlib.Path) -> None:
    import aota_forge.core.contracts.loader as loader_module

    source = inspect.getsource(loader_module)
    assert "aota.operation-contract" not in source
    assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    (descriptor,) = load_operation_descriptors(tmp_path)
    assert descriptor.protocol_version == "1.0"
    assert descriptor.protocol_version != "aota.operation-contract"


# ---------------------------------------------------------------------------
# Contract root single rule
# ---------------------------------------------------------------------------


def test_contract_root_default_without_manifest(tmp_path: pathlib.Path) -> None:
    assert resolve_contract_root(tmp_path) == tmp_path / FIXED_DEFAULT_CONTRACT_ROOT_NAME


def test_contract_root_manifest_pointer_honored(tmp_path: pathlib.Path) -> None:
    write_manifest(tmp_path, "schema_version: 1\ncontracts:\n  root: custom/contracts\n")
    custom = tmp_path / "custom" / "contracts"
    custom.mkdir(parents=True)
    (custom / "operations.yaml").write_text(VALID_OPERATIONS_DOC, encoding="utf-8")
    assert resolve_contract_root(tmp_path) == custom
    (descriptor,) = load_operation_descriptors(tmp_path)
    assert descriptor.name == "fixture.op.status"


def test_unknown_document_kind_argument_rejected(tmp_path: pathlib.Path) -> None:
    write_contract(tmp_path, "operations.yaml", VALID_OPERATIONS_DOC)
    with pytest.raises(ValueError):
        load_document(tmp_path, "bogus")


# ---------------------------------------------------------------------------
# §40 No production progress state
# ---------------------------------------------------------------------------


def test_production_source_has_no_progress_state() -> None:
    import aota_forge.core.contracts.loader as loader_module

    source = inspect.getsource(loader_module)
    forbidden = (
        "W1_IMPLEMENTED",
        "W2_IMPLEMENTED",
        "M2_STAGE",
        "CURRENT_MILESTONE",
        "CURRENT_WORK_ITEM",
        "migration_complete",
    )
    for token in forbidden:
        assert token not in source
