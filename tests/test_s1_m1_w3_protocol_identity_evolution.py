"""S1/M1/W3 — Protocol Identity & Evolution Rule.

Minimal executor-neutral protocol identity, version semantics, compatible
additive vs breaking evolution, legacy compatibility, hash semantics,
and fail-closed guarantees.

No destructive rename, no mass migration, no negotiation framework.

Covers A-H per construction prompt §24-§31.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pathlib

import pytest

from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import (
    BREAKING_KINDS,
    COMPATIBLE_ADDITIVE_KINDS,
    CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT,
    CURRENT_PROTOCOL_IDENTITY,
    DESCRIPTOR_HASH_SCHEMA_CHANGED,
    DESTRUCTIVE_PROTOCOL_NAMESPACE_MIGRATION,
    GENERIC_AOTA_PROTOCOL_DIRECTION,
    GENERIC_AOTA_PROTOCOL_FAMILY,
    GENERIC_PROTOCOL_DIRECTION_DEFINED,
    GENERIC_PROTOCOL_IDENTITY_MODEL_DEFINED,
    GENERIC_TARGET_PROTOCOL_ACTIVE,
    LEGACY_COMPATIBILITY_STRATEGY_DEFINED,
    LEGACY_FORGE_PROTOCOL_PRESERVED,
    OPERATION_CONTRACT_PROTOCOL,
    PROTOCOL_EVOLUTION_RULE_DEFINED,
    PROTOCOL_FAMILY_DISTINCT_FROM_CONTRACT_HASH,
    PROTOCOL_FAMILY_DISTINCT_FROM_PROTOCOL_VERSION,
    PROTOCOL_NAMESPACE_CURRENTLY_IN_CONTRACT_HASH,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_DISTINCT_FROM_CONTRACT_HASH,
    RUNTIME_PROTOCOL_NEGOTIATION_FRAMEWORK_CREATED,
    SEMANTIC_CHANGE_CLASSIFICATION_REQUIRED,
    UNKNOWN_EVOLUTION_FAILS_CLOSED,
    VERSION_NUMBER_ALONE_PROVES_COMPATIBILITY,
    ProtocolIdentity,
    classify_evolution,
    is_breaking_evolution,
    is_compatible_evolution,
    is_generic_direction_family,
    is_supported_protocol_family,
    project_to_generic_family,
    protocol_version,
)
from aota_forge.core.contracts.vocabulary import (
    ContractRevisionFingerprint,
    OperationSemanticIdentity,
    contract_revision_of,
    operation_semantic_identity_of,
)


def _make_descriptor(
    name: str = "w3.test_op",
    description: str = "w3 test operation",
    errors: tuple[str, ...] = ("E1",),
    protocol_version: str = PROTOCOL_VERSION,
) -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name=name,
        description=description,
        inputs=(InputSpec("x", "str"),),
        required_context=("principal",),
        optional_context=(),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write="write",
        mutation_scope="test",
        required_authority="test_auth",
        approval_required=False,
        valid_predecessor_state="s1",
        valid_successor_state="s2",
        idempotency="test",
        errors=errors,
        protocol_version=protocol_version,
        decision_required=False,
        subject_revision_precondition=False,
        external_authority_precondition=False,
        result_contract="test.contract.v1",
    )


# ---------------------------------------------------------------------------
# A. Legacy preservation
# ---------------------------------------------------------------------------


class TestALegacyPreservation:
    def test_legacy_protocol_and_version_unchanged(self):
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert PROTOCOL_VERSION == "1.0"
        assert protocol_version() == "1.0"
        assert LEGACY_FORGE_PROTOCOL_PRESERVED is True
        assert DESTRUCTIVE_PROTOCOL_NAMESPACE_MIGRATION is False
        assert GENERIC_TARGET_PROTOCOL_ACTIVE is False

    def test_current_protocol_identity_is_legacy(self):
        assert CURRENT_PROTOCOL_IDENTITY.family == "aota-forge.operation-contract"
        assert CURRENT_PROTOCOL_IDENTITY.version == "1.0"
        assert CURRENT_PROTOCOL_IDENTITY == ProtocolIdentity(
            family=OPERATION_CONTRACT_PROTOCOL, version=PROTOCOL_VERSION
        )

    def test_generic_direction_is_explicit_but_not_active(self):
        assert GENERIC_PROTOCOL_DIRECTION_DEFINED is True
        assert GENERIC_AOTA_PROTOCOL_FAMILY == "aota.operation-contract"
        assert GENERIC_AOTA_PROTOCOL_DIRECTION == "aota.operation-contract"
        assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
        assert is_generic_direction_family(GENERIC_AOTA_PROTOCOL_FAMILY) is True
        assert is_supported_protocol_family(GENERIC_AOTA_PROTOCOL_FAMILY) is False
        # Legacy remains the only supported runtime family
        assert is_supported_protocol_family(OPERATION_CONTRACT_PROTOCOL) is True


# ---------------------------------------------------------------------------
# B. Identity distinction — family != version != hash (semantic, not trivial)
# ---------------------------------------------------------------------------


class TestBIdentityDistinction:
    def test_protocol_identity_distinct_from_operation_semantic_identity(self):
        # ProtocolIdentity = family + version
        # OperationSemanticIdentity = family + operation name
        d = _make_descriptor(name="distinct_op")
        proto = CURRENT_PROTOCOL_IDENTITY
        sem = operation_semantic_identity_of(d)
        assert isinstance(proto, ProtocolIdentity)
        assert isinstance(sem, OperationSemanticIdentity)
        assert type(proto) is not type(sem)
        # Values overlap on family string but types are distinct
        assert proto.family == sem.protocol_family
        # operation name vs version are different domains
        assert proto.version != sem.operation_name

    def test_protocol_version_distinct_from_contract_hash_via_revision(self):
        assert PROTOCOL_VERSION_DISTINCT_FROM_CONTRACT_HASH is True
        d = _make_descriptor(name="rev_op")
        rev = contract_revision_of(d)
        assert isinstance(rev, ContractRevisionFingerprint)
        # version and hash are separate fields on the revision
        assert rev.protocol_version == PROTOCOL_VERSION
        assert isinstance(rev.contract_hash, str) and len(rev.contract_hash) == 64
        assert rev.protocol_version != rev.contract_hash
        # version alone is not the hash
        assert PROTOCOL_VERSION != rev.contract_hash

    def test_protocol_family_distinct_from_contract_hash(self):
        assert PROTOCOL_FAMILY_DISTINCT_FROM_CONTRACT_HASH is True
        d = _make_descriptor(name="fam_hash_op")
        rev = contract_revision_of(d)
        assert OPERATION_CONTRACT_PROTOCOL != rev.contract_hash
        assert GENERIC_AOTA_PROTOCOL_FAMILY != rev.contract_hash
        # family string never appears as hash value
        assert rev.contract_hash not in (OPERATION_CONTRACT_PROTOCOL, GENERIC_AOTA_PROTOCOL_FAMILY)

    def test_three_domains_structurally_separate(self):
        assert PROTOCOL_FAMILY_DISTINCT_FROM_PROTOCOL_VERSION is True
        proto = CURRENT_PROTOCOL_IDENTITY
        d = _make_descriptor(name="three_domains_op")
        rev = contract_revision_of(d)
        sem = operation_semantic_identity_of(d)
        # All three identity types are distinct classes
        assert type(proto) is ProtocolIdentity
        assert type(sem) is OperationSemanticIdentity
        assert type(rev) is ContractRevisionFingerprint
        assert {type(proto), type(sem), type(rev)} == {
            ProtocolIdentity,
            OperationSemanticIdentity,
            ContractRevisionFingerprint,
        }
        # Family vs version are separate fields on ProtocolIdentity itself
        assert proto.family != proto.version
        # Generic identity model is defined
        assert GENERIC_PROTOCOL_IDENTITY_MODEL_DEFINED is True

    def test_current_and_generic_identities_are_distinct(self):
        # Current identity is concrete: legacy family + 1.0
        assert CURRENT_PROTOCOL_IDENTITY.family == OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert CURRENT_PROTOCOL_IDENTITY.version == PROTOCOL_VERSION == "1.0"
        # Generic direction is family-only, inactive, no version committed in W3
        assert GENERIC_AOTA_PROTOCOL_FAMILY == "aota.operation-contract"
        assert GENERIC_AOTA_PROTOCOL_FAMILY != CURRENT_PROTOCOL_IDENTITY.family
        assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
        # No ProtocolIdentity exists for generic target — version undecided in W3
        import aota_forge.core.contracts.version as ver

        assert not hasattr(ver, "GENERIC_TARGET_PROTOCOL_IDENTITY")
        assert is_supported_protocol_family(GENERIC_AOTA_PROTOCOL_FAMILY) is False


# ---------------------------------------------------------------------------
# C. Hash reality — descriptor canonical serialization
# ---------------------------------------------------------------------------


class TestCHashReality:
    def test_canonical_serialization_contains_protocol_version(self):
        d = _make_descriptor(name="hash_real_op")
        data = d.to_dict()
        assert "protocol_version" in data
        assert data["protocol_version"] == PROTOCOL_VERSION
        json_str = d.to_canonical_json()
        assert '"protocol_version":"1.0"' in json_str
        assert PROTOCOL_VERSION in json_str

    def test_canonical_serialization_does_not_contain_protocol_namespace(self):
        d = _make_descriptor(name="hash_no_ns_op")
        data = d.to_dict()
        # No namespace/family field in current descriptor schema
        for forbidden_key in ("protocol_family", "protocol_namespace", "operation_protocol", "OPERATION_CONTRACT_PROTOCOL"):
            assert forbidden_key not in data
        json_str = d.to_canonical_json()
        # Family string must not appear in canonical bytes (namespace not serialized)
        # Current truth: namespace is NOT part of hash input (§14)
        assert OPERATION_CONTRACT_PROTOCOL not in json_str
        assert GENERIC_AOTA_PROTOCOL_FAMILY not in json_str
        # Also check raw bytes
        raw = d.to_canonical_bytes()
        assert OPERATION_CONTRACT_PROTOCOL.encode() not in raw
        assert b"protocol_family" not in raw

    def test_contract_hash_derived_from_canonical_bytes(self):
        d = _make_descriptor(name="hash_derive_op")
        expected = hashlib.sha256(d.to_canonical_bytes()).hexdigest()
        assert d.contract_hash() == expected
        assert contract_revision_of(d).contract_hash == expected
        assert CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT is True
        assert PROTOCOL_NAMESPACE_CURRENTLY_IN_CONTRACT_HASH is False
        assert DESCRIPTOR_HASH_SCHEMA_CHANGED is False

    def test_namespace_change_alone_would_not_change_existing_hash(self):
        # Because namespace is not serialized, changing the constant alone
        # cannot affect current descriptor hash. Prove by constructing a
        # descriptor with same fields — hash is independent of the constant's
        # value, only of protocol_version field.
        d1 = _make_descriptor(name="ns_hash_op")
        h1 = d1.contract_hash()
        # Simulate what would happen if namespace string changed but descriptor
        # fields stay identical: hash stays same because canonical bytes unchanged.
        # We verify the constant is not inside the bytes.
        assert OPERATION_CONTRACT_PROTOCOL.encode() not in d1.to_canonical_bytes()
        assert h1 == hashlib.sha256(d1.to_canonical_bytes()).hexdigest()
        # Changing protocol_version WOULD change hash (it is serialized)
        d2 = _make_descriptor(name="ns_hash_op", protocol_version="2.0")
        assert d2.contract_hash() != h1

    def test_hash_is_deterministic_across_processes_simulated(self):
        d = _make_descriptor(name="deterministic_op")
        assert d.contract_hash() == d.contract_hash()
        # Rebuild from dict round-trip preserves hash
        rebuilt = OperationContractDescriptor.from_dict(d.to_dict())
        assert rebuilt.contract_hash() == d.contract_hash()


# ---------------------------------------------------------------------------
# D. Additive evolution — same family + additive kind -> compatible
# ---------------------------------------------------------------------------


class TestDAdditiveEvolution:
    def test_compatible_additive_kinds_are_compatible_on_legacy_family(self):
        assert COMPATIBLE_ADDITIVE_KINDS  # non-empty
        for kind in COMPATIBLE_ADDITIVE_KINDS:
            assert is_compatible_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) is True
            assert is_breaking_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) is False
            assert classify_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) == "compatible"

    def test_add_optional_field_with_safe_default_is_compatible(self):
        assert is_compatible_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="add_optional_field_with_safe_default"
        ) is True

    def test_add_error_code_is_compatible(self):
        assert is_compatible_evolution(
            family=OPERATION_CONTRACT_PROTOCOL,
            change_kind="add_new_error_code_without_changing_existing_meaning",
        ) is True

    def test_add_optional_capability_is_compatible(self):
        assert is_compatible_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="add_optional_capability"
        ) is True

    def test_add_additive_metadata_is_compatible(self):
        assert is_compatible_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="add_additive_metadata_safe_to_ignore"
        ) is True

    def test_version_number_alone_does_not_prove_compatibility(self):
        # Even if version were "2.0", compatibility requires semantic kind
        assert VERSION_NUMBER_ALONE_PROVES_COMPATIBILITY is False
        assert SEMANTIC_CHANGE_CLASSIFICATION_REQUIRED is True
        # Same kind on different family is NOT compatible (family boundary matters, not version string)
        assert is_compatible_evolution(
            family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind="add_optional_field_with_safe_default"
        ) is False
        # Without kind, unknown fails closed even on same family
        assert is_compatible_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="unknown_future_kind") is False


# ---------------------------------------------------------------------------
# E. Breaking evolution — breaking kind or family change -> incompatible
# ---------------------------------------------------------------------------


class TestEBreakingEvolution:
    def test_breaking_kinds_are_breaking_on_legacy_family(self):
        assert BREAKING_KINDS
        for kind in BREAKING_KINDS:
            assert is_breaking_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) is True
            assert is_compatible_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) is False
            assert classify_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind=kind) == "breaking"

    def test_remove_required_field_is_breaking(self):
        assert is_breaking_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="remove_required_field") is True

    def test_rename_without_projection_is_breaking(self):
        assert is_breaking_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="rename_required_field_without_projection"
        ) is True

    def test_reinterpret_field_is_breaking(self):
        assert is_breaking_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="reinterpret_field_incompatibly"
        ) is True

    def test_change_authority_is_breaking(self):
        assert is_breaking_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="change_required_authority_incompatibly"
        ) is True

    def test_change_serialization_is_breaking(self):
        assert is_breaking_evolution(
            family=OPERATION_CONTRACT_PROTOCOL, change_kind="change_serialization_incompatibly"
        ) is True

    def test_family_change_without_projection_is_breaking_boundary(self):
        # Even a nominally compatible kind becomes breaking when family changes
        for kind in COMPATIBLE_ADDITIVE_KINDS:
            assert is_compatible_evolution(family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind=kind) is False
            assert is_breaking_evolution(family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind=kind) is True
            assert classify_evolution(family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind=kind) == "breaking"
        # Explicit breaking family change kind
        assert is_breaking_evolution(
            family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind="change_family_without_projection"
        ) is True
        assert classify_evolution(
            family=GENERIC_AOTA_PROTOCOL_FAMILY, change_kind="change_family_without_projection"
        ) == "breaking"

    def test_unknown_change_fails_closed(self):
        assert UNKNOWN_EVOLUTION_FAILS_CLOSED is True
        assert is_compatible_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="future_unknown_kind_9999") is False
        assert is_breaking_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="future_unknown_kind_9999") is True
        assert classify_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="future_unknown_kind_9999") == "unknown"
        # Empty / invalid change_kind also fails closed
        assert classify_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="") == "unknown"
        assert is_compatible_evolution(family=OPERATION_CONTRACT_PROTOCOL, change_kind="") is False
        assert classify_evolution(family="", change_kind="add_optional_field_with_safe_default") == "unknown"
        assert is_compatible_evolution(family="", change_kind="add_optional_field_with_safe_default") is False


# ---------------------------------------------------------------------------
# F. Legacy / generic boundary
# ---------------------------------------------------------------------------


class TestFLegacyGenericBoundary:
    def test_legacy_remains_supported_current(self):
        assert LEGACY_FORGE_PROTOCOL_PRESERVED is True
        assert LEGACY_COMPATIBILITY_STRATEGY_DEFINED is True
        assert PROTOCOL_EVOLUTION_RULE_DEFINED is True
        assert is_supported_protocol_family(OPERATION_CONTRACT_PROTOCOL) is True
        assert is_supported_protocol_family("aota.operation-contract") is False
        assert is_supported_protocol_family("unknown.family") is False

    def test_generic_direction_does_not_replace_legacy_automatically(self):
        assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
        assert DESTRUCTIVE_PROTOCOL_NAMESPACE_MIGRATION is False
        # Current identity is still legacy (family + 1.0)
        assert CURRENT_PROTOCOL_IDENTITY.family == OPERATION_CONTRACT_PROTOCOL
        assert CURRENT_PROTOCOL_IDENTITY.version == PROTOCOL_VERSION == "1.0"
        # Generic direction is family string only, not an active ProtocolIdentity
        assert GENERIC_AOTA_PROTOCOL_FAMILY == "aota.operation-contract"
        assert GENERIC_AOTA_PROTOCOL_DIRECTION == GENERIC_AOTA_PROTOCOL_FAMILY
        assert is_generic_direction_family(GENERIC_AOTA_PROTOCOL_FAMILY) is True
        assert GENERIC_AOTA_PROTOCOL_FAMILY != CURRENT_PROTOCOL_IDENTITY.family
        # No ProtocolIdentity for generic target — version not decided in W3
        import aota_forge.core.contracts.version as ver

        assert not hasattr(ver, "GENERIC_TARGET_PROTOCOL_IDENTITY")
        # No silent mass migration: generic family is not implicitly supported
        assert is_supported_protocol_family(GENERIC_AOTA_PROTOCOL_FAMILY) is False

    def test_projection_is_explicit_and_additive(self):
        # Explicit projection helper returns generic only for legacy source
        assert project_to_generic_family(OPERATION_CONTRACT_PROTOCOL) == GENERIC_AOTA_PROTOCOL_FAMILY
        assert project_to_generic_family(GENERIC_AOTA_PROTOCOL_FAMILY) is None
        assert project_to_generic_family("unknown.family") is None
        # Projection does not mutate CURRENT identity — it is a function return
        assert CURRENT_PROTOCOL_IDENTITY.family == OPERATION_CONTRACT_PROTOCOL
        # Legacy protocol constant itself unchanged after projection call
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"

    def test_no_destructive_alias_rewrite(self):
        # Ensure version module does not expose a destructive alias table
        import aota_forge.core.contracts.version as ver

        for forbidden in ("PROTOCOL_ALIAS_TABLE", "DESTRUCTIVE_ALIAS", "MIGRATED_FAMILIES"):
            assert not hasattr(ver, forbidden)
        # Existing descriptors remain bound to legacy family semantics via CURRENT identity
        d = _make_descriptor(name="boundary_op")
        sem = operation_semantic_identity_of(d)
        assert sem.protocol_family == OPERATION_CONTRACT_PROTOCOL
        assert sem.protocol_family != GENERIC_AOTA_PROTOCOL_FAMILY


# ---------------------------------------------------------------------------
# F2. Generic target version non-commitment — W3-R1 regression
# ---------------------------------------------------------------------------


class TestF2GenericVersionNonCommitment:
    """W3-R1: future generic family version is NOT committed in W3.

    API-level semantic check — not brittle source grep. Proves W3 does NOT
    establish ``aota.operation-contract / 1.0`` as the selected future
    generic protocol identity. Generic direction remains family-only and
    inactive; only CURRENT_PROTOCOL_IDENTITY is a concrete ProtocolIdentity.
    """

    def test_generic_target_version_not_committed(self):
        import aota_forge.core.contracts.version as ver

        # No ProtocolIdentity for generic target — version undecided
        assert not hasattr(ver, "GENERIC_TARGET_PROTOCOL_IDENTITY")
        # No generic version constant is materialized in W3
        for forbidden in (
            "GENERIC_TARGET_PROTOCOL_VERSION",
            "GENERIC_PROTOCOL_VERSION",
            "GENERIC_TARGET_VERSION",
        ):
            assert not hasattr(ver, forbidden)
        # Generic family remains a separate direction constant, inactive
        assert GENERIC_AOTA_PROTOCOL_FAMILY == "aota.operation-contract"
        assert GENERIC_TARGET_PROTOCOL_ACTIVE is False
        assert is_supported_protocol_family(GENERIC_AOTA_PROTOCOL_FAMILY) is False

    def test_only_current_is_concrete_active_protocol_identity(self):
        import aota_forge.core.contracts.version as ver

        # Enumerate all ProtocolIdentity values exported by version module
        identities = [
            v for v in vars(ver).values() if isinstance(v, ProtocolIdentity)
        ]
        # Only CURRENT_PROTOCOL_IDENTITY should exist as concrete identity
        assert len(identities) == 1
        assert identities[0] == CURRENT_PROTOCOL_IDENTITY
        assert identities[0].family == OPERATION_CONTRACT_PROTOCOL
        assert identities[0].version == PROTOCOL_VERSION == "1.0"
        # Generic direction is string-only, not a ProtocolIdentity
        assert isinstance(GENERIC_AOTA_PROTOCOL_FAMILY, str)
        assert GENERIC_AOTA_PROTOCOL_FAMILY != CURRENT_PROTOCOL_IDENTITY.family

    def test_generic_family_has_no_authoritative_version_in_w3(self):
        # Project helper remains family-only; it never assigns a version
        assert project_to_generic_family(OPERATION_CONTRACT_PROTOCOL) == GENERIC_AOTA_PROTOCOL_FAMILY
        # Generic direction does not carry version "1.0" via any API
        import aota_forge.core.contracts.version as ver

        assert not hasattr(ver, "GENERIC_TARGET_PROTOCOL_IDENTITY")
        # Ensure no accidental version leakage via ProtocolIdentity construction
        # for generic family with "1.0" exists in module
        for name in dir(ver):
            obj = getattr(ver, name)
            if isinstance(obj, ProtocolIdentity):
                assert obj.family != GENERIC_AOTA_PROTOCOL_FAMILY


# ---------------------------------------------------------------------------
# G. No negotiation framework
# ---------------------------------------------------------------------------


class TestGNoNegotiationFramework:
    def test_no_runtime_negotiation_framework_created(self):
        assert RUNTIME_PROTOCOL_NEGOTIATION_FRAMEWORK_CREATED is False
        import aota_forge.core.contracts.version as ver
        import aota_forge.core.contracts.descriptor as dmod
        import aota_forge.core.contracts.vocabulary as vocab

        for mod in (ver, dmod, vocab):
            for forbidden in (
                "ProtocolRegistry",
                "ProtocolNegotiator",
                "ProtocolResolver",
                "CompatibilityMatrix",
                "ProtocolPlugin",
                "VersionBroker",
                "SchemaRegistry",
                "MigrationRegistry",
                "HandshakeProtocol",
                "CapabilityNegotiator",
                "NegotiationFramework",
            ):
                assert not hasattr(mod, forbidden), f"{mod.__name__} must not expose {forbidden}"

    def test_contracts_usable_without_negotiation(self):
        # Core contracts are usable without any negotiation call
        d = _make_descriptor(name="no_negotiation_op")
        assert d.contract_hash()
        assert d.to_canonical_json()
        # ProtocolIdentity is a simple value, not a framework
        pid = ProtocolIdentity(family=OPERATION_CONTRACT_PROTOCOL, version=PROTOCOL_VERSION)
        assert pid.family == OPERATION_CONTRACT_PROTOCOL
        # version module source should not contain negotiation framework implementation
        # (bare mention in docstring listing forbidden names is allowed; only
        # class/def implementation is prohibited).
        ver_path = pathlib.Path(inspect.getfile(importlib.import_module("aota_forge.core.contracts.version")))
        src = ver_path.read_text(encoding="utf-8")
        for needle in ("class ProtocolNegotiat", "class Handshake", "def handshake", "fallback chain"):
            assert needle.lower() not in src.lower()

    def test_protocol_identity_is_tiny_value_object(self):
        pid = ProtocolIdentity(family=OPERATION_CONTRACT_PROTOCOL, version="1.0")
        assert pid.family == "aota-forge.operation-contract"
        assert pid.version == "1.0"
        # Immutable
        with pytest.raises((AttributeError, TypeError)):
            pid.family = "other"  # type: ignore[misc]
        # Distinct from contract revision
        rev = contract_revision_of(_make_descriptor(name="value_obj_op"))
        assert type(pid) is not type(rev)


# ---------------------------------------------------------------------------
# H. Time-invariant regression (no progress flags)
# ---------------------------------------------------------------------------


class TestHTimeInvariantRegression:
    def test_no_construction_progress_flags(self):
        import aota_forge.core.contracts.version as ver
        import aota_forge.core.contracts.vocabulary as vocab

        for mod in (ver, vocab):
            for forbidden in (
                "W3_IMPLEMENTED",
                "M1_COMPLETE",
                "M2_IMPLEMENTED",
                "W3_ACCEPTED",
                "PROTOCOL_REVIEW_PASS",
                "W3_DONE",
                "M1_DONE",
            ):
                assert not hasattr(mod, forbidden), f"{mod.__name__} must not contain progress flag {forbidden}"

    def test_permanent_semantics_are_time_invariant(self):
        # These flags must remain valid after future milestones complete
        assert GENERIC_PROTOCOL_IDENTITY_MODEL_DEFINED is True
        assert GENERIC_PROTOCOL_DIRECTION_DEFINED is True
        assert PROTOCOL_EVOLUTION_RULE_DEFINED is True
        assert LEGACY_FORGE_PROTOCOL_PRESERVED is True
        assert CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT is True
        # Core identity values are stable, not milestone-gated
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert PROTOCOL_VERSION == "1.0"
        assert CURRENT_PROTOCOL_IDENTITY.family == OPERATION_CONTRACT_PROTOCOL

    def test_tests_do_not_assert_m2_yaml_absence_as_w3_progress(self):
        # W3 tests must not assert M2 not implemented
        ver_path = pathlib.Path(inspect.getfile(importlib.import_module("aota_forge.core.contracts.version")))
        src = ver_path.read_text(encoding="utf-8")
        # Ensure no M2_IMPLEMENTED flag couples W3 to future milestone state
        assert "M2_IMPLEMENTED" not in src
        # Ensure no W1/W2 test file is modified to indicate progression (checked via git diff elsewhere)
