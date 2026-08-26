"""Declarative contract document loader foundation (S1/M2/W1).

Bounded strict safe YAML loading for the canonical declarative contract
root (``.aota/contracts``).  Infrastructure only: the loader never holds
or binds executable handlers, never reads or mutates registry state,
never activates the generic target protocol, and owns no canonical
contract content.  Python remains the schema/validation authority; the
loader reuses ``OperationContractDescriptor.from_dict()`` and its
validation unchanged.

Document envelope (declarative document format only):

* ``schema_version`` — declarative document schema version, distinct from
  the operation protocol version (``aota_forge.core.contracts.version``)
* ``kind`` — ``operations`` | ``capabilities`` | ``results``
* ``contracts`` — list of plain declarative entries

Contract-root discovery is a single deterministic rule: project manifest
``contracts.root`` metadata if explicitly and cleanly present, otherwise
the fixed default ``.aota/contracts``.  The manifest value is a portable
project-relative logical path only: host absolute paths are always
rejected, even when they happen to point inside the project.  The
resolved root must stay inside the project boundary; an explicit invalid
``contracts.root`` fails closed rather than silently falling back.

The strict SafeLoader and duplicate-mapping-key rejection pattern are
reused from ``aota_forge.core.project.manifest``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from aota_forge.core.contracts.descriptor import OperationContractDescriptor
from aota_forge.core.contracts.errors import ForgeError

FIXED_DEFAULT_CONTRACT_ROOT_NAME: Final[str] = ".aota/contracts"

DECLARATIVE_DOCUMENT_SCHEMA_VERSION: Final[int] = 1

DOCUMENT_KIND_OPERATIONS: Final[str] = "operations"
DOCUMENT_KIND_CAPABILITIES: Final[str] = "capabilities"
DOCUMENT_KIND_RESULTS: Final[str] = "results"
DOCUMENT_KINDS: Final[frozenset[str]] = frozenset(
    {DOCUMENT_KIND_OPERATIONS, DOCUMENT_KIND_CAPABILITIES, DOCUMENT_KIND_RESULTS}
)

DOCUMENT_FILE_NAMES: Final[dict[str, str]] = {
    DOCUMENT_KIND_OPERATIONS: "operations.yaml",
    DOCUMENT_KIND_CAPABILITIES: "capabilities.yaml",
    DOCUMENT_KIND_RESULTS: "results.yaml",
}

MAX_DOCUMENT_BYTES: Final[int] = 4 * 1024 * 1024

ERR_NOT_FOUND: Final[str] = "DECLARATIVE_CONTRACT_NOT_FOUND"
ERR_INVALID: Final[str] = "DECLARATIVE_CONTRACT_INVALID"
ERR_UNSUPPORTED_VERSION: Final[str] = "DECLARATIVE_CONTRACT_UNSUPPORTED_VERSION"
ERR_KIND: Final[str] = "DECLARATIVE_CONTRACT_KIND_INVALID"

_DOCUMENT_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"schema_version", "kind", "contracts"})

_OPERATION_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "inputs",
        "required_context",
        "optional_context",
        "internal_ids_required",
        "internal_ids_created",
        "read_write",
        "mutation_scope",
        "required_authority",
        "approval_required",
        "decision_required",
        "valid_predecessor_state",
        "valid_successor_state",
        "subject_revision_precondition",
        "external_authority_precondition",
        "idempotency",
        "result_contract",
        "errors",
        "protocol_version",
    }
)


class DeclarativeContractError(ForgeError):
    """Bounded fail-closed error raised by declarative contract loading."""

    code = "DECLARATIVE_CONTRACT_ERROR"


class _StrictLoader(yaml.SafeLoader):
    pass


def _strict_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise DeclarativeContractError(ERR_INVALID, "declarative document mapping keys must be strings")
        if key in mapping:
            raise DeclarativeContractError(ERR_INVALID, f"duplicate mapping key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)


def _parse_yaml_document(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DeclarativeContractError(ERR_INVALID, f"document is unsafe or exceeds size bound: {path}")
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except DeclarativeContractError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DeclarativeContractError(ERR_INVALID, f"invalid declarative document: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise DeclarativeContractError(ERR_INVALID, "declarative document must be an object")
    return data


def _contract_root_candidate(root: Path, relative: str) -> Path:
    """Return ``root / relative`` only if ``relative`` is a project-relative
    logical path that stays inside the project.

    Host absolute paths are rejected up front regardless of whether they
    happen to point inside the project, outside the project or at the
    project root itself: the manifest value is a portable project-relative
    logical path, never a host path.  Containment is evaluated on resolved
    canonical paths so lexical ``..`` traversal and symlink-based escapes
    both fail closed.  ``resolve(strict=False)`` keeps a non-existent
    project-local leaf valid; document loading later reports
    ``DECLARATIVE_CONTRACT_NOT_FOUND``.
    """
    if Path(relative).is_absolute():
        raise DeclarativeContractError(
            ERR_INVALID, "contract root must be a project-relative path, not a host absolute path"
        )
    project = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(project)
    except ValueError as exc:
        raise DeclarativeContractError(
            ERR_INVALID, f"contract root escapes project boundary: {relative}"
        ) from exc
    return root / relative


def resolve_contract_root(project_root: Path) -> Path:
    """Resolve the single deterministic canonical contract root for a project.

    Rule: project manifest ``contracts.root`` metadata if explicitly and
    cleanly present, otherwise the fixed default ``.aota/contracts``.

    The manifest value must be a project-relative logical path.  An
    explicit ``contracts.root`` that is present but invalid (wrong type,
    empty, host absolute, or escaping the project) fails closed; it never
    silently falls back to the default.  Both the override and the default
    must resolve inside the project boundary.  A project manifest that is
    not parseable at all preserves the existing fallback-to-default
    semantics.
    """
    root = Path(project_root)
    manifest = root / ".aota" / "project.yaml"
    if manifest.is_file():
        try:
            data = _parse_yaml_document(manifest)
        except DeclarativeContractError:
            data = None
        if data is not None:
            contracts = data.get("contracts")
            if contracts is not None and not isinstance(contracts, dict):
                raise DeclarativeContractError(
                    ERR_INVALID, "project manifest contracts must be a mapping"
                )
            if isinstance(contracts, dict) and "root" in contracts:
                explicit = contracts["root"]
                if not isinstance(explicit, str) or not explicit.strip():
                    raise DeclarativeContractError(
                        ERR_INVALID, "project manifest contracts.root must be a non-empty string"
                    )
                return _contract_root_candidate(root, explicit)
    return _contract_root_candidate(root, FIXED_DEFAULT_CONTRACT_ROOT_NAME)


def _document_path(contract_root: Path, kind: str) -> Path:
    return Path(contract_root) / DOCUMENT_FILE_NAMES[kind]


def load_document(project_root: Path, kind: str) -> dict[str, Any]:
    """Load, parse and envelope-validate one domain-scoped declarative document.

    Fails closed for a missing domain document, malformed YAML, duplicate
    mapping keys, unknown document fields, unsupported schema version and
    kind/domain mismatch.
    """
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"unknown declarative document kind: {kind}")
    path = _document_path(resolve_contract_root(project_root), kind)
    if not path.is_file():
        raise DeclarativeContractError(ERR_NOT_FOUND, f"declarative {kind} document missing at {path}")
    data = _parse_yaml_document(path)
    _validate_envelope(data, requested_kind=kind)
    return data


def _validate_envelope(data: dict[str, Any], requested_kind: str) -> None:
    unknown = sorted(set(data) - _DOCUMENT_TOP_LEVEL_KEYS)
    if unknown:
        raise DeclarativeContractError(ERR_INVALID, f"unknown declarative document field: {unknown[0]}")
    if "schema_version" not in data:
        raise DeclarativeContractError(ERR_INVALID, "declarative document schema_version is missing")
    schema_version = data["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise DeclarativeContractError(
            ERR_UNSUPPORTED_VERSION, "declarative document schema_version must be an integer"
        )
    if schema_version != DECLARATIVE_DOCUMENT_SCHEMA_VERSION:
        raise DeclarativeContractError(
            ERR_UNSUPPORTED_VERSION, f"unsupported declarative document schema_version: {schema_version}"
        )
    if not isinstance(data.get("kind"), str) or not data["kind"]:
        raise DeclarativeContractError(ERR_KIND, "declarative document kind is missing")
    if data["kind"] != requested_kind:
        raise DeclarativeContractError(
            ERR_KIND,
            f"document kind {data['kind']!r} does not match requested domain {requested_kind!r}",
        )
    if not isinstance(data.get("contracts"), list):
        raise DeclarativeContractError(ERR_INVALID, "declarative document contracts must be a list")


def load_operations(project_root: Path) -> dict[str, Any]:
    """Load the validated ``operations`` document as plain declarative data."""
    return load_document(project_root, DOCUMENT_KIND_OPERATIONS)


def load_capabilities(project_root: Path) -> dict[str, Any]:
    """Load the validated ``capabilities`` document as plain declarative data."""
    return load_document(project_root, DOCUMENT_KIND_CAPABILITIES)


def load_results(project_root: Path) -> dict[str, Any]:
    """Load the validated ``results`` document as plain declarative data."""
    return load_document(project_root, DOCUMENT_KIND_RESULTS)


def load_operation_descriptors(project_root: Path) -> tuple[OperationContractDescriptor, ...]:
    """Convert the validated ``operations`` document into existing descriptors.

    Conversion reuses ``OperationContractDescriptor.from_dict`` so the
    existing canonical descriptor validation remains the authority.
    """
    document = load_operations(project_root)
    entries = document.get("contracts")
    if not isinstance(entries, list):
        raise DeclarativeContractError(ERR_INVALID, "operations document contracts must be a list")
    return tuple(_build_operation_descriptor(entry) for entry in entries)


def _build_operation_descriptor(entry: Any) -> OperationContractDescriptor:
    if not isinstance(entry, dict):
        raise DeclarativeContractError(ERR_INVALID, "operations document entries must be objects")
    unknown = sorted(set(entry) - _OPERATION_ENTRY_KEYS)
    if unknown:
        raise DeclarativeContractError(ERR_INVALID, f"unknown operation entry field: {unknown[0]}")
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise DeclarativeContractError(ERR_INVALID, "operation entry name is missing")
    if not isinstance(entry.get("description"), str) or not entry["description"].strip():
        raise DeclarativeContractError(ERR_INVALID, f"operation entry description is missing: {entry.get('name')}")
    return OperationContractDescriptor.from_dict(entry)
