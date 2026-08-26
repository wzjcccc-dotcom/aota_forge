"""Explicit operation contract protocol version (M2-A) + W3 protocol identity & evolution rule.

``PROTOCOL_VERSION`` is the canonical, machine-readable version of the
operation contract descriptor protocol. It is explicit and stable: it is
NOT derived from the package version, and every consumer (Core, CLI adapter,
future Hermes adapter, future MCP/API adapter) MUST read this constant
instead of inventing its own version signal.

W3 (S1/M1/W3) adds minimal executor-neutral protocol identity and evolution
semantics without destructive migration or runtime negotiation:

- protocol family   = which semantic protocol family the contract belongs to
- protocol version  = compatibility/evolution signal within that family
- contract hash     = exact deterministic descriptor revision/content fingerprint

These three are distinct (family != version != hash). The current runtime
identity is (family="aota-forge.operation-contract", version="1.0").

Generic AOTA direction is declared as an explicitly non-active target
``aota.operation-contract``. Legacy remains the active runtime identity;
generic is reached via explicit compatibility/projection, not silent rename.

Evolution is classified by semantic kind, not version number alone. Unknown
changes fail closed (incompatible).

This module is the primary authorized production path for W3. No
ProtocolRegistry / ProtocolNegotiator / CompatibilityMatrix / plugin
framework is created here. ``ProtocolIdentity`` is the only tiny immutable
value object allowed (W3 §7).

Hash reality: ``OPERATION_CONTRACT_PROTOCOL`` is NOT serialized into
``OperationContractDescriptor`` canonical bytes (see descriptor.to_dict /
to_canonical_json); ``protocol_version`` IS. Therefore changing protocol
namespace alone does NOT recompute stored ``contract_hash``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Legacy/current runtime protocol — must remain usable (W3 §5)
# ---------------------------------------------------------------------------

OPERATION_CONTRACT_PROTOCOL: Final[str] = "aota-forge.operation-contract"
PROTOCOL_VERSION: Final[str] = "1.0"


def protocol_version() -> str:
    """Return the canonical operation contract protocol version."""
    return PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Generic AOTA direction — explicitly non-active (W3 §6, §23, §38)
# ---------------------------------------------------------------------------

# Concrete target direction for future canonical family.  NOT active in S1/M1:
# legacy remains the sole runtime authority.
GENERIC_AOTA_PROTOCOL_FAMILY: Final[str] = "aota.operation-contract"
GENERIC_AOTA_PROTOCOL_DIRECTION: Final[str] = GENERIC_AOTA_PROTOCOL_FAMILY

# Permanent policy constants — stable semantics, not construction progress.
LEGACY_FORGE_PROTOCOL_PRESERVED: Final[bool] = True
DESTRUCTIVE_PROTOCOL_NAMESPACE_MIGRATION: Final[bool] = False
LEGACY_COMPATIBILITY_STRATEGY_DEFINED: Final[bool] = True
GENERIC_TARGET_PROTOCOL_ACTIVE: Final[bool] = False
GENERIC_PROTOCOL_DIRECTION_DEFINED: Final[bool] = True
GENERIC_PROTOCOL_IDENTITY_MODEL_DEFINED: Final[bool] = True
PROTOCOL_EVOLUTION_RULE_DEFINED: Final[bool] = True

# Version semantics — number alone does not prove compatibility
VERSION_NUMBER_ALONE_PROVES_COMPATIBILITY: Final[bool] = False
SEMANTIC_CHANGE_CLASSIFICATION_REQUIRED: Final[bool] = True
UNKNOWN_EVOLUTION_FAILS_CLOSED: Final[bool] = True

# Hash semantics — contract_hash is revision fingerprint, namespace not in hash
CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT: Final[bool] = True
PROTOCOL_NAMESPACE_CURRENTLY_IN_CONTRACT_HASH: Final[bool] = False
DESCRIPTOR_HASH_SCHEMA_CHANGED: Final[bool] = False

# No negotiation framework
RUNTIME_PROTOCOL_NEGOTIATION_FRAMEWORK_CREATED: Final[bool] = False

# Distinctness — family / version / hash are separate domains
PROTOCOL_FAMILY_DISTINCT_FROM_PROTOCOL_VERSION: Final[bool] = True
PROTOCOL_VERSION_DISTINCT_FROM_CONTRACT_HASH: Final[bool] = True
PROTOCOL_FAMILY_DISTINCT_FROM_CONTRACT_HASH: Final[bool] = True

# Additive / breaking evolution markers (W3 §11, §12)
COMPATIBLE_ADDITIVE_EVOLUTION_DEFINED: Final[bool] = True
BREAKING_SEMANTIC_EVOLUTION_DEFINED: Final[bool] = True


# ---------------------------------------------------------------------------
# Protocol identity — family + version (W3 §8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolIdentity:
    """Minimal immutable protocol identity: family + version.

    Distinct from Operation semantic identity (family + operation name)
    and from ContractRevisionFingerprint (version + contract_hash).
    """

    family: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.family, str) or not self.family.strip():
            raise ValueError("family must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")


CURRENT_PROTOCOL_IDENTITY: Final[ProtocolIdentity] = ProtocolIdentity(
    family=OPERATION_CONTRACT_PROTOCOL,
    version=PROTOCOL_VERSION,
)

# Generic direction remains family-only in W3; no version is committed
# for the future generic family (GENERIC_TARGET_PROTOCOL_ACTIVE=no).
# Do not construct a ProtocolIdentity for the generic target until its
# version is actually decided.


# ---------------------------------------------------------------------------
# Evolution classification — compatible additive vs breaking semantic
# ---------------------------------------------------------------------------

# Compatible additive examples (§11) — same family + safe additive semantics.
# Old consumers remain valid when these occur alone.
COMPATIBLE_ADDITIVE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "add_optional_field_with_safe_default",
        "add_new_error_code_without_changing_existing_meaning",
        "add_optional_capability",
        "add_additive_metadata_safe_to_ignore",
    }
)

# Breaking semantic examples (§12) — require compatibility boundary / migration.
BREAKING_KINDS: Final[frozenset[str]] = frozenset(
    {
        "remove_required_field",
        "rename_required_field_without_projection",
        "reinterpret_field_incompatibly",
        "change_required_authority_incompatibly",
        "change_serialization_incompatibly",
        "change_family_without_projection",
    }
)

# Union for validation — unknown kinds exist (e.g. future kinds)
ALL_KNOWN_KINDS: Final[frozenset[str]] = COMPATIBLE_ADDITIVE_KINDS | BREAKING_KINDS


def is_supported_protocol_family(family: str) -> bool:
    """Return True iff *family* is currently supported as runtime identity.

    W3: only the legacy Forge family is active. The generic direction is
    declared but NOT active (``GENERIC_TARGET_PROTOCOL_ACTIVE=no``). Future
    generic family may be added via explicit migration/projection — not by
    silently treating unknown families as compatible.
    """
    if not isinstance(family, str):
        return False
    return family == OPERATION_CONTRACT_PROTOCOL


def is_legacy_family(family: str) -> bool:
    """Alias for legacy check — legacy Forge protocol preserved."""
    return family == OPERATION_CONTRACT_PROTOCOL


def is_generic_direction_family(family: str) -> bool:
    """Return True iff family equals the declared generic AOTA direction."""
    return family == GENERIC_AOTA_PROTOCOL_FAMILY


def project_to_generic_family(source_family: str) -> str | None:
    """Explicit additive projection from legacy to generic direction.

    Returns the generic target family only when source is the legacy family
    and projection is explicitly requested.  Never silently rewrites an
    arbitrary family, and never mutates stored descriptors automatically.
    This is additive/explicit (W3 §13), not a destructive alias rewrite.
    """
    if source_family == OPERATION_CONTRACT_PROTOCOL:
        return GENERIC_AOTA_PROTOCOL_FAMILY
    return None


def classify_evolution(*, family: str, change_kind: str) -> str:
    """Classify a protocol evolution by semantic kind (fail-closed).

    Returns:
        "compatible"  — same family + known compatible additive kind
        "breaking"    — known breaking kind or family boundary without projection
        "unknown"     — unrecognized change_kind (must not be treated as compatible)

    ``family`` is the *resulting* family after the change (or the family
    being evaluated).  Changing family without an explicit projection is
    always a breaking boundary (W3 §12 last bullet, §10).

    Version number alone is never inspected here — semantic classification
    is required (``SEMANTIC_CHANGE_CLASSIFICATION_REQUIRED=yes``).
    """
    if not isinstance(family, str) or not family.strip():
        return "unknown"
    if not isinstance(change_kind, str) or not change_kind.strip():
        return "unknown"

    # Family transition without projection is breaking, even if kind looks additive
    if family != OPERATION_CONTRACT_PROTOCOL:
        # Only additive/explicit projection could make a family transition
        # compatible, and no such implicit projection is granted in W3.
        # Caller must use project_to_generic_family explicitly and treat the
        # projection as a bounded migration decision later.
        return "breaking"

    if change_kind in COMPATIBLE_ADDITIVE_KINDS:
        return "compatible"
    if change_kind in BREAKING_KINDS:
        return "breaking"
    return "unknown"


def is_compatible_evolution(*, family: str, change_kind: str) -> bool:
    """Return True only for explicitly classified compatible additive evolutions.

    Unknown or breaking kinds return False (fail-closed).  Family must be
    the legacy family; any family change returns False without explicit
    projection.  Never infer compatibility from version number alone.
    """
    return classify_evolution(family=family, change_kind=change_kind) == "compatible"


def is_breaking_evolution(*, family: str, change_kind: str) -> bool:
    """Return True for breaking or unknown evolutions (fail-closed).

    Compatible additive returns False.  Unknown returns True to enforce
    fail-closed (``UNKNOWN_EVOLUTION_FAILS_CLOSED=yes``).
    """
    result = classify_evolution(family=family, change_kind=change_kind)
    return result in ("breaking", "unknown")
