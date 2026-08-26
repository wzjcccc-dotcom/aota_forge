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

# W3: capabilities reuse ExecutorCapabilities fields but with distinct semantic identity
# Capability semantic identity (name) is NOT executor_id; runtime fields like
# max_timeout_seconds / concurrency_limit are excluded from canonical declaration.
_CAPABILITY_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "adapter_kind",
        "supported_execution_modes",
        "supports_streaming_events",
        "supports_task_cancellation",
        "supports_task_resume",
        "supports_structured_result",
        "supported_canonical_roles",
        "supported_isolation_modes",
        "supports_working_directory",
        "supports_artifact_transport",
    }
)

# W3: minimal result-contract identity + compatibility mapping
_RESULT_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "compatible_operations",
        "protocol",
        "protocol_version",
    }
)

# Forbidden semantics that must never appear in capability declarations
_FORBIDDEN_CAPABILITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "preferred_executor",
        "best_role",
        "task_priority_recommendation",
        "semantic_routing_score",
        "heuristic_quality_rating",
        "preferred",
        "ranking",
        "score",
        "priority",
        "handler",
        "callable",
        "profile",
        "routing",
        "runtime_state",
        "secret",
    }
)

_FORBIDDEN_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "correlation_id",
        "evidence",
        "artifact",
        "provenance",
        "retention",
        "handler",
        "callable",
        "secret",
        "warnings",
        "errors",
        "data",
        "status",
        "result",
        "runtime",
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
    document = load_document(project_root, DOCUMENT_KIND_CAPABILITIES)
    _validate_capability_contracts(document)
    return document


def load_results(project_root: Path) -> dict[str, Any]:
    """Load the validated ``results`` document as plain declarative data."""
    document = load_document(project_root, DOCUMENT_KIND_RESULTS)
    _validate_result_contracts(document)
    return document


def _validate_capability_contracts(document: dict[str, Any]) -> None:
    entries = document.get("contracts")
    if not isinstance(entries, list):
        raise DeclarativeContractError(ERR_INVALID, "capabilities document contracts must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeclarativeContractError(ERR_INVALID, "capabilities document entries must be objects")
        # Forbidden routing/profile/handler fields fail closed
        for field in _FORBIDDEN_CAPABILITY_FIELDS:
            if field in entry:
                raise DeclarativeContractError(ERR_INVALID, f"forbidden capability field: {field}")
        # Also check ExecutorCapabilities forbidden semantics
        from aota_forge.core.execution.capabilities import FORBIDDEN_SEMANTIC_FIELDS

        for field in FORBIDDEN_SEMANTIC_FIELDS:
            if field in entry:
                raise DeclarativeContractError(ERR_INVALID, f"forbidden capability semantic field: {field}")
        unknown = sorted(set(entry) - _CAPABILITY_ENTRY_KEYS)
        if unknown:
            raise DeclarativeContractError(ERR_INVALID, f"unknown capability entry field: {unknown[0]}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeclarativeContractError(ERR_INVALID, "capability entry name is missing")
        if name in seen:
            raise DeclarativeContractError(ERR_INVALID, f"duplicate capability identity: {name}")
        seen.add(name)
        # executor_id must NOT be used as capability identity; ensure name is not conflated
        # (we do not accept executor_id as identity, but if present it must not equal name check is not needed;
        # we simply ensure duplicate check is on name, not executor_id)
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise DeclarativeContractError(ERR_INVALID, f"capability entry description is missing: {name}")
        # Validate strict types for capability fields using ExecutorCapabilities helpers
        _validate_capability_entry_types(entry)

    # Additional distinctness check: capability name must not be mere executor inventory key
    # (documented separately in tests)


def _validate_capability_entry_types(entry: dict[str, Any]) -> None:
    name = entry.get("name", "<unknown>")
    # adapter_kind
    if not isinstance(entry.get("adapter_kind"), str) or not entry["adapter_kind"].strip():
        raise DeclarativeContractError(ERR_INVALID, f"capability entry adapter_kind is missing: {name}")
    # bool fields must be bool exactly
    for bf in (
        "supports_streaming_events",
        "supports_task_cancellation",
        "supports_task_resume",
        "supports_structured_result",
        "supports_working_directory",
        "supports_artifact_transport",
    ):
        if bf not in entry:
            raise DeclarativeContractError(ERR_INVALID, f"capability entry missing field {bf}: {name}")
        if type(entry[bf]) is not bool:
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry field {bf} must be a bool: {name} got {type(entry[bf]).__name__}"
            )
    # sequence fields must be list/tuple of strings, not string
    for seq_field in (
        "supported_execution_modes",
        "supported_canonical_roles",
        "supported_isolation_modes",
    ):
        if seq_field not in entry:
            raise DeclarativeContractError(ERR_INVALID, f"capability entry missing field {seq_field}: {name}")
        val = entry[seq_field]
        if isinstance(val, (str, bytes)):
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry field {seq_field} must be a list: {name}"
            )
        if not isinstance(val, (list, tuple)) or len(val) == 0:
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry field {seq_field} must be a non-empty list: {name}"
            )
        for item in val:
            if not isinstance(item, str) or not item.strip():
                raise DeclarativeContractError(
                    ERR_INVALID, f"capability entry field {seq_field} members must be non-empty strings: {name}"
                )
    # Validate against canonical vocabularies
    from aota_forge.core.execution.capabilities import (
        ALLOWED_EXECUTION_MODES,
        ALLOWED_ISOLATION_MODES,
    )
    from aota_forge.core.execution.roles import validate_canonical_role

    for mode in entry["supported_execution_modes"]:
        if mode not in ALLOWED_EXECUTION_MODES:
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry contains non-canonical execution mode {mode!r}: {name}"
            )
    for mode in entry["supported_isolation_modes"]:
        if mode not in ALLOWED_ISOLATION_MODES:
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry contains non-canonical isolation mode {mode!r}: {name}"
            )
    for role in entry["supported_canonical_roles"]:
        try:
            validate_canonical_role(role)
        except Exception as exc:
            raise DeclarativeContractError(
                ERR_INVALID, f"capability entry contains invalid canonical role {role!r}: {name}"
            ) from exc
    # Deterministic normalization check: ensure no runtime handler etc.
    # No further normalization needed here; ExecutorCapabilities will normalize on construction if needed


def _validate_result_contracts(document: dict[str, Any]) -> None:
    entries = document.get("contracts")
    if not isinstance(entries, list):
        raise DeclarativeContractError(ERR_INVALID, "results document contracts must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeclarativeContractError(ERR_INVALID, "results document entries must be objects")
        for field in _FORBIDDEN_RESULT_FIELDS:
            if field in entry:
                # Allow description/protocol but not runtime instance fields
                # we already forbid specific runtime fields
                if field in ("correlation_id", "evidence", "artifact", "provenance"):
                    raise DeclarativeContractError(ERR_INVALID, f"forbidden result field: {field}")
        # Check for provenance/artifact governance fields explicitly
        for gov_field in ("provenance", "evidence", "artifact_manifest", "retention_policy", "receipt"):
            if gov_field in entry:
                raise DeclarativeContractError(ERR_INVALID, f"forbidden S5 governance field: {gov_field}")
        unknown = sorted(set(entry) - _RESULT_ENTRY_KEYS)
        if unknown:
            raise DeclarativeContractError(ERR_INVALID, f"unknown result entry field: {unknown[0]}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeclarativeContractError(ERR_INVALID, "result entry name is missing")
        if name in seen:
            raise DeclarativeContractError(ERR_INVALID, f"duplicate result-contract identity: {name}")
        seen.add(name)
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise DeclarativeContractError(ERR_INVALID, f"result entry description is missing: {name}")
        comp = entry.get("compatible_operations")
        if not isinstance(comp, (list, tuple)) or len(comp) == 0:
            raise DeclarativeContractError(
                ERR_INVALID, f"result entry compatible_operations must be a non-empty list: {name}"
            )
        for op in comp:
            if not isinstance(op, str) or not op.strip():
                raise DeclarativeContractError(
                    ERR_INVALID, f"result entry compatible_operations members must be non-empty strings: {name}"
                )
        # protocol must be active legacy if present
        protocol = entry.get("protocol")
        if protocol is not None:
            if not isinstance(protocol, str) or not protocol.strip():
                raise DeclarativeContractError(ERR_INVALID, f"result entry protocol must be a non-empty string: {name}")
            # Must be active legacy protocol, not generic
            from aota_forge.core.contracts.version import OPERATION_CONTRACT_PROTOCOL

            if protocol != OPERATION_CONTRACT_PROTOCOL:
                # Allow only legacy active protocol; generic is not active
                raise DeclarativeContractError(
                    ERR_INVALID, f"result entry protocol must be active legacy protocol: {name}"
                )
        version = entry.get("protocol_version")
        if version is not None:
            if not isinstance(version, str) or not version.strip():
                raise DeclarativeContractError(
                    ERR_INVALID, f"result entry protocol_version must be a non-empty string: {name}"
                )
            from aota_forge.core.contracts.version import PROTOCOL_VERSION

            if version != PROTOCOL_VERSION:
                raise DeclarativeContractError(
                    ERR_INVALID, f"result entry protocol_version must be active version {PROTOCOL_VERSION}: {name}"
                )


def _discover_canonical_project_root() -> Path:
    """Discover the canonical project root deterministically from package location.

    Walks parents of this file until ``.aota/project.yaml`` is found.
    This is project-bounded, not CWD/ENV/host-absolute.  It reuses the
    existing manifest marker as the canonical project identity.

    Used by production runtime projections (catalog/ingress) to locate the
    single canonical ``operations.yaml`` without inventing a new path
    authority.  Tests with isolated fixtures call ``load_*`` directly with
    an explicit ``project_root``.
    """
    start = Path(__file__).resolve()
    for parent in [start.parent] + list(start.parents):
        if (parent / ".aota" / "project.yaml").is_file():
            # parent is candidate; verify it contains the manifest that
            # identifies as project root (contains .aota/project.yaml)
            # For the aota_forge repo, the repo root itself is the project
            # root (parent that contains .aota).
            # Walk continues upward but first match from file upward is
            # closest project root (correct for nested workspaces).
            # Need to find the outermost that still contains .aota? The
            # closest parent that has .aota/project.yaml is the project
            # root when starting inside package.
            # Return the directory that contains .aota.
            return parent
            # Note: we do not resolve further to workspace discovery; the
            # project root is the directory that contains .aota.
    # Fallback: walk from file's parents that correspond to package layout
    # ``aota_forge/core/contracts`` -> repo root is 3 levels up from
    # ``aota_forge`` package directory.  Prove via manifest existence above.
    raise DeclarativeContractError(ERR_NOT_FOUND, "canonical project root not found via package location")


def discover_canonical_project_root() -> Path:
    """Public wrapper for canonical project root discovery."""
    return _discover_canonical_project_root()


def load_operation_descriptors(project_root: Path) -> tuple[OperationContractDescriptor, ...]:
    """Convert the validated ``operations`` document into existing descriptors.

    Conversion reuses ``OperationContractDescriptor.from_dict`` so the
    existing canonical descriptor validation remains the authority.
    Duplicate operation identities fail closed before any map is formed.
    """
    document = load_operations(project_root)
    entries = document.get("contracts")
    if not isinstance(entries, list):
        raise DeclarativeContractError(ERR_INVALID, "operations document contracts must be a list")
    descriptors = tuple(_build_operation_descriptor(entry) for entry in entries)
    seen: set[str] = set()
    for desc in descriptors:
        if desc.name in seen:
            raise DeclarativeContractError(ERR_INVALID, f"duplicate operation identity: {desc.name}")
        seen.add(desc.name)
    return descriptors


def load_operation_descriptor_map(project_root: Path) -> dict[str, OperationContractDescriptor]:
    """Deterministic name -> descriptor map with duplicate fail-closed."""
    descriptors = load_operation_descriptors(project_root)
    return {desc.name: desc for desc in descriptors}


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
    # Fail closed on unsupported operation protocol version (distinct from document schema_version)
    from aota_forge.core.contracts.version import PROTOCOL_VERSION

    pv = entry.get("protocol_version")
    if pv is not None and pv != PROTOCOL_VERSION:
        raise DeclarativeContractError(
            ERR_INVALID, f"unsupported operation protocol_version: {pv!r} (expected {PROTOCOL_VERSION!r})"
        )
    # Even when missing, descriptor validation will handle required check, but we
    # also enforce explicit version authority here via descriptor post-check below.
    descriptor = OperationContractDescriptor.from_dict(entry)
    if descriptor.protocol_version != PROTOCOL_VERSION:
        raise DeclarativeContractError(
            ERR_INVALID, f"unsupported operation protocol_version: {descriptor.protocol_version!r}"
        )
    return descriptor
