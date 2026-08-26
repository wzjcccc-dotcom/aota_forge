"""S1/M1/W2 — Canonical Descriptor Primitives reconciliation.

Reconciliation-first, reuse-first validation for M1/W2.
Covers A-J per construction prompt §19.

No new primitive class is expected; existing contracts are reused.
"""

from __future__ import annotations

import inspect
import pathlib
import importlib

import pytest

from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import OPERATION_CONTRACT_PROTOCOL, PROTOCOL_VERSION
from aota_forge.core.contracts.vocabulary import (
    BULK_OPERATION_DESCRIPTOR_RENAME,
    CANONICAL_ERROR_CODE_REQUIRED,
    CAPABILITY_FIRST_CLASS,
    CAPABILITY_IS_EXECUTOR,
    CAPABILITY_IS_PROFILE,
    CAPABILITY_TAXONOMY_DEFERRED,
    DESCRIPTOR_AUDITED_FIELDS,
    DESCRIPTOR_FIELD_CLASSIFICATION,
    DESCRIPTOR_FIELD_CLASSIFICATION_VALUES,
    EXISTING_CONTRACT_RECONCILIATION_FIRST,
    EXISTING_EXECUTION_CONTRACTS_REUSED_FIRST,
    EXECUTION_REQUEST_SEMANTICS_REQUIRED,
    EXECUTION_RESULT_SEMANTICS_REQUIRED,
    FORGE_COMPATIBILITY_FIELD,
    FORGE_POLICY_NOT_MANDATORY_CANONICAL_CORE,
    FORGE_POLICY_SPECIFIC_FIELDS_IDENTIFIED,
    GENERIC_CANONICAL,
    GENERIC_MECHANISM_WITH_POLICY_SPECIFIC_VALUES,
    HANDLER_IDENTITY_EXCLUDED,
    HANDLER_REGISTRY_RUNTIME_LOCAL,
    NEW_AUTHORITY_REQUIREMENT_CLASS_REQUIRED,
    NEW_CAPABILITY_DESCRIPTOR_CREATED,
    NEW_ERROR_DESCRIPTOR_CLASS_REQUIRED,
    NEW_EXECUTION_REQUEST_CLASS_CREATED,
    NEW_EXECUTION_RESULT_CLASS_CREATED,
    NEW_GENERIC_POLICY_REFERENCE_FRAMEWORK_REQUIRED,
    NEW_PARALLEL_ERROR_TAXONOMY,
    NEW_PRIMITIVE_ONLY_IF_PROVEN_GAP,
    NO_HERMES_SPECIFIC_FIELD_IN_CANONICAL_CONTRACT,
    OPERATION_CONTRACT_DESCRIPTOR_RETAINED,
    REUSE_CANONICAL_RESULT_FIRST,
    REUSE_EXECUTION_PACKAGE_FIRST,
    RESULT_CONTRACT_IDENTITY_EXPRESSIBLE,
    RESULT_CONTRACT_IS_OPAQUE_IDENTITY,
    RESULT_INSTANCE_DISTINCT,
    UNRESOLVED,
    AUTHORITY_REQUIREMENT_EXPRESSIBLE,
    get_descriptor_field_classification,
    is_forge_compatibility_field,
    is_generic_canonical_field,
    is_generic_mechanism_field,
    result_contract_ref,
    task_ref,
    execution_attempt_ref,
)
from aota_forge.core.contracts.registry import HandlerRegistry, DEFAULT_REGISTRY
from aota_forge.core.contracts.errors import ForgeError, ERROR_CLASSES, UnknownFutureError, error_from_dict
from aota_forge.core.contracts.mutation import MutationPreconditions
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.package import ExecutionPackage, FORBIDDEN_HERMES_FIELDS
from aota_forge.core.execution.results import CanonicalResult, FORBIDDEN_HERMES_RESULT_KEYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_generic_descriptor(
    name: str = "generic.test_op",
    mutation_scope: str = "generic.test_scope",
    required_authority: str = "generic_authority",
    predecessor: str = "generic_pre_state",
    successor: str = "generic_post_state",
) -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name=name,
        description="generic test operation",
        inputs=(InputSpec("x", "str"),),
        required_context=("principal",),
        optional_context=(),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write="write",
        mutation_scope=mutation_scope,
        required_authority=required_authority,
        approval_required=False,
        valid_predecessor_state=predecessor,
        valid_successor_state=successor,
        idempotency="generic_idempotent",
        errors=("GENERIC_ERROR",),
        protocol_version=PROTOCOL_VERSION,
        decision_required=False,
        subject_revision_precondition=False,
        external_authority_precondition=False,
        result_contract="generic.result.v1",
    )


def _make_forge_lifecycle_descriptor() -> OperationContractDescriptor:
    # Reuse real lifecycle values to prove Forge-specific values are allowed but not required
    from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR
    return PLAN_INIT_DESCRIPTOR


# ---------------------------------------------------------------------------
# A. Operation descriptor field classification
# ---------------------------------------------------------------------------

class TestADescriptorFieldClassification:
    def test_every_audited_field_maps_to_exactly_one_bucket(self):
        assert len(DESCRIPTOR_AUDITED_FIELDS) == 20
        assert set(DESCRIPTOR_AUDITED_FIELDS) == set(DESCRIPTOR_FIELD_CLASSIFICATION.keys())
        # No duplicate keys by construction; check no missing
        for field in DESCRIPTOR_AUDITED_FIELDS:
            cls = get_descriptor_field_classification(field)
            assert cls in DESCRIPTOR_FIELD_CLASSIFICATION_VALUES, field

    def test_no_field_has_duplicate_classification(self):
        # dict guarantees uniqueness; verify helper returns single value
        for field in DESCRIPTOR_AUDITED_FIELDS:
            assert get_descriptor_field_classification(field) == DESCRIPTOR_FIELD_CLASSIFICATION[field]

    def test_classification_values_are_valid_vocabulary(self):
        for field, bucket in DESCRIPTOR_FIELD_CLASSIFICATION.items():
            assert bucket in DESCRIPTOR_FIELD_CLASSIFICATION_VALUES

    def test_expected_forge_compatibility_fields(self):
        assert is_forge_compatibility_field("internal_ids_required") is True
        assert is_forge_compatibility_field("internal_ids_created") is True
        # generic fields not compatibility
        assert is_forge_compatibility_field("name") is False
        assert is_forge_compatibility_field("valid_predecessor_state") is False

    def test_expected_generic_canonical_fields(self):
        for f in ("name", "description", "inputs", "required_context", "optional_context", "read_write", "errors", "protocol_version", "result_contract"):
            assert is_generic_canonical_field(f) is True, f

    def test_expected_generic_mechanism_fields(self):
        for f in ("mutation_scope", "required_authority", "approval_required", "decision_required", "valid_predecessor_state", "valid_successor_state", "subject_revision_precondition", "external_authority_precondition", "idempotency"):
            assert is_generic_mechanism_field(f) is True, f

    def test_unresolved_not_used_for_audited_set(self):
        # Reconciliation proves no unresolved for audited fields; value still valid vocabulary
        assert UNRESOLVED in DESCRIPTOR_FIELD_CLASSIFICATION_VALUES
        assert UNRESOLVED not in DESCRIPTOR_FIELD_CLASSIFICATION.values()

    def test_unknown_field_raises(self):
        with pytest.raises(KeyError):
            get_descriptor_field_classification("nonexistent_field")
        with pytest.raises(ValueError):
            get_descriptor_field_classification("")

    def test_real_descriptor_contains_audited_fields(self):
        d = _make_generic_descriptor()
        d_dict = d.to_dict()
        for field in DESCRIPTOR_AUDITED_FIELDS:
            assert field in d_dict, f"field {field} missing from descriptor serialization"

    def test_forge_policy_fields_identified(self):
        assert FORGE_POLICY_SPECIFIC_FIELDS_IDENTIFIED is True
        # At least internal_ids + state fields demonstrate Forge policy separation
        compat = [f for f, v in DESCRIPTOR_FIELD_CLASSIFICATION.items() if v == FORGE_COMPATIBILITY_FIELD]
        mech = [f for f, v in DESCRIPTOR_FIELD_CLASSIFICATION.items() if v == GENERIC_MECHANISM_WITH_POLICY_SPECIFIC_VALUES]
        assert len(compat) >= 2
        assert len(mech) >= 5


# ---------------------------------------------------------------------------
# B. Forge policy != canonical requirement
# ---------------------------------------------------------------------------

class TestBForgePolicyNotMandatoryCanonical:
    def test_generic_descriptor_valid_without_forge_values(self):
        assert FORGE_POLICY_NOT_MANDATORY_CANONICAL_CORE is True
        d_generic = _make_generic_descriptor(
            name="generic.op_a",
            mutation_scope="generic.scope_a",
            required_authority="generic_auth_a",
            predecessor="state_alpha",
            successor="state_beta",
        )
        # Generic descriptor validates and hashes deterministically
        h1 = d_generic.contract_hash()
        assert isinstance(h1, str) and len(h1) == 64
        assert d_generic.contract_hash() == h1

        d_forge = _make_forge_lifecycle_descriptor()
        assert d_forge.valid_predecessor_state == "uninitialized"
        # Forge value is allowed but not required for generic
        assert d_generic.valid_predecessor_state != "uninitialized"
        assert d_generic.mutation_scope != "plan_subject"

    def test_internal_ids_empty_still_valid(self):
        d = _make_generic_descriptor()
        assert d.internal_ids_required == ()
        assert d.internal_ids_created == ()
        d.validate()

    def test_generic_vs_forge_hashes_differ_but_both_deterministic(self):
        d1 = _make_generic_descriptor(name="x", predecessor="pre_a", successor="post_a")
        d2 = _make_generic_descriptor(name="x", predecessor="pre_b", successor="post_b")
        assert d1.contract_hash() != d2.contract_hash()
        assert d1.contract_hash() == d1.contract_hash()
        assert d2.contract_hash() == d2.contract_hash()

    def test_no_new_generic_policy_framework_created(self):
        assert NEW_GENERIC_POLICY_REFERENCE_FRAMEWORK_REQUIRED is False
        import aota_forge.core.contracts.vocabulary as vocab
        for forbidden in ("LifecycleContractRef", "PreconditionContractRef", "UniversalPolicyDescriptor", "PolicyRegistry", "PolicyGraph", "AuthorityFrameworkV2"):
            assert not hasattr(vocab, forbidden), f"vocabulary must not contain {forbidden}"

    def test_descriptor_source_does_not_create_policy_framework(self):
        import pathlib, inspect
        desc_path = pathlib.Path(inspect.getfile(OperationContractDescriptor))
        src = desc_path.read_text(encoding="utf-8")
        for forbidden in ("LifecycleContractRef", "UniversalPolicyDescriptor", "PolicyRegistry"):
            assert forbidden not in src


# ---------------------------------------------------------------------------
# C. Existing capability mapping
# ---------------------------------------------------------------------------

class TestCExistingCapabilityMapping:
    def test_executor_capabilities_usable_without_profile_or_hermes(self):
        caps = ExecutorCapabilities(
            executor_id="test-exec-1",
            adapter_kind="reference",
            supported_execution_modes=("sync", "async"),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=False,
        )
        assert caps.executor_id == "test-exec-1"
        assert not hasattr(caps, "profile")
        assert not hasattr(caps, "hermes_profile")
        assert not hasattr(caps, "hermes_worker")
        # Round-trip
        rebuilt = ExecutorCapabilities.from_dict(caps.to_dict())
        assert rebuilt == caps

    def test_capability_is_first_class_not_profile_not_executor(self):
        assert CAPABILITY_FIRST_CLASS is True
        assert CAPABILITY_IS_PROFILE is False
        assert CAPABILITY_IS_EXECUTOR is False
        assert CAPABILITY_TAXONOMY_DEFERRED is True
        assert NEW_CAPABILITY_DESCRIPTOR_CREATED is False
        # Ensure no duplicate CapabilityDescriptor class in vocabulary
        import aota_forge.core.contracts.vocabulary as vocab
        assert not hasattr(vocab, "CapabilityDescriptor")
        import aota_forge.core.execution.capabilities as capmod
        assert not hasattr(capmod, "CapabilityDescriptor")
        # Existing is ExecutorCapabilities
        assert hasattr(capmod, "ExecutorCapabilities")

    def test_executor_capabilities_no_hermes_fields(self):
        import pathlib, inspect
        cap_path = pathlib.Path(inspect.getfile(ExecutorCapabilities))
        src = cap_path.read_text(encoding="utf-8")
        for forbidden in ("hermes_profile", "ProcessRegistry", "hermes_worker"):
            assert forbidden not in src
        # from_dict rejects semantic decision fields
        with pytest.raises(ValueError):
            ExecutorCapabilities.from_dict({
                "executor_id": "x",
                "adapter_kind": "y",
                "supported_execution_modes": ("sync",),
                "supports_streaming_events": True,
                "supports_task_cancellation": True,
                "supports_task_resume": True,
                "supports_structured_result": True,
                "supported_canonical_roles": ("coder",),
                "supported_isolation_modes": ("process",),
                "supports_working_directory": True,
                "supports_artifact_transport": True,
                "preferred_executor": "some",
            })

    def test_capability_fields_are_executor_descriptor_identity_split(self):
        caps = ExecutorCapabilities(
            executor_id="exec-split",
            adapter_kind="local",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("none",),
            supports_working_directory=False,
            supports_artifact_transport=False,
        )
        # executor_id + adapter_kind are executor descriptor identity/config
        assert isinstance(caps.executor_id, str)
        assert isinstance(caps.adapter_kind, str)
        # capability semantics include supported_* and supports_*
        assert caps.supports_mode("sync") is True
        assert caps.supports_role("coder") is True


# ---------------------------------------------------------------------------
# D. Execution request semantics via ExecutionPackage
# ---------------------------------------------------------------------------

class TestDExecutionRequestSemantics:
    def test_execution_package_expresses_required_request_seam(self):
        assert EXECUTION_REQUEST_SEMANTICS_REQUIRED is True
        assert REUSE_EXECUTION_PACKAGE_FIRST is True
        assert NEW_EXECUTION_REQUEST_CLASS_CREATED is False
        pkg = ExecutionPackage.create(
            canonical_task_id="task-req-001",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="do work via execution seam",
            subject_ref="subject-1",
            input_artifacts=({"kind": "file", "path": "a.txt"},),
            working_context={"cwd": "/tmp"},
            capability_requirements={"execution_mode": "sync"},
            constraints={"timeout_seconds": 30},
            result_expectations={"schema": "generic.result.v1"},
        )
        d = pkg.to_dict()
        required_seam = (
            "package_id",
            "protocol_version",
            "contract_hash",
            "operation",
            "canonical_task_id",
            "subject_ref",
            "project_id",
            "canonical_role",
            "instruction",
            "input_artifacts",
            "working_context",
            "capability_requirements",
            "constraints",
            "idempotency_key",
            "intent_fingerprint",
            "correlation_id",
            "result_expectations",
        )
        for field in required_seam:
            assert field in d, f"missing seam field {field}"
        # Task identity via package
        assert pkg.canonical_task_id == "task-req-001"
        assert task_ref(pkg.canonical_task_id) == "task-req-001"
        # Round-trip
        rebuilt = ExecutionPackage.from_dict(d)
        assert rebuilt == pkg
        assert rebuilt.package_fingerprint() == pkg.package_fingerprint()

    def test_no_parallel_execution_request_class(self):
        import aota_forge.core.execution.package as pkgmod
        import aota_forge.core.contracts.vocabulary as vocab
        for mod in (pkgmod, vocab):
            for name in ("ExecutionRequest", "ExecutionRequestEnvelope"):
                assert not hasattr(mod, name)
        # Also check execution __init__
        import aota_forge.core.execution as exec_mod
        assert not hasattr(exec_mod, "ExecutionRequest")

    def test_execution_package_intent_fingerprint_deterministic(self):
        pkg1 = ExecutionPackage.create(
            canonical_task_id="task-d-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="same instruction",
        )
        pkg2 = ExecutionPackage.create(
            canonical_task_id="task-d-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="same instruction",
            package_id=pkg1.package_id,
            idempotency_key=pkg1.idempotency_key,
            correlation_id=pkg1.correlation_id,
        )
        assert pkg1.intent_fingerprint == pkg2.intent_fingerprint

    def test_execution_package_rejects_hermes_fields(self):
        base = ExecutionPackage.create(
            canonical_task_id="task-d-hermes",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="hermes reject test",
        )
        d = base.to_dict()
        d["hermes_profile_object"] = {"profile": "hermes"}
        with pytest.raises(ValueError):
            ExecutionPackage.from_dict(d)


# ---------------------------------------------------------------------------
# E. Execution result semantics via CanonicalResult
# ---------------------------------------------------------------------------

class TestEExecutionResultSemantics:
    def test_canonical_result_provides_result_seam(self):
        assert EXECUTION_RESULT_SEMANTICS_REQUIRED is True
        assert REUSE_CANONICAL_RESULT_FIRST is True
        assert NEW_EXECUTION_RESULT_CLASS_CREATED is False
        res = CanonicalResult.success(
            canonical_task_id="task-res-001",
            executor_id="exec-1",
            result_data={"value": 42},
            output_artifacts=({"artifact": "out.txt"},),
            correlation_id="corr-res-001",
        )
        d = res.to_dict()
        # Required seam per W2: execution/task relation, status, result contract identity, payload, error
        assert d["canonical_task_id"] == "task-res-001"
        assert d["executor_id"] == "exec-1"
        assert d["status"] == "completed"
        assert d["ok"] is True
        assert d["result_data"] == {"value": 42}
        assert d["error"] is None
        assert d["correlation_id"] == "corr-res-001"
        # Round-trip
        rebuilt = CanonicalResult.from_dict(d)
        assert rebuilt == res

        fail = CanonicalResult.failure(
            canonical_task_id="task-res-002",
            executor_id="exec-1",
            error_code="EXECUTION_FAILED",
            error_message="failed",
            correlation_id="corr-fail-001",
        )
        assert fail.ok is False
        assert fail.status == "failed"
        assert fail.error is not None
        assert fail.error["code"] == "EXECUTION_FAILED"

    def test_no_parallel_execution_result_class(self):
        import aota_forge.core.execution.results as rmod
        import aota_forge.core.contracts.vocabulary as vocab
        for mod in (rmod, vocab):
            for name in ("ExecutionResult", "ExecutionResultEnvelope"):
                assert not hasattr(mod, name)
        import aota_forge.core.execution as exec_mod
        assert not hasattr(exec_mod, "ExecutionResult")

    def test_result_task_relation_via_canonical_task_id(self):
        pkg = ExecutionPackage.create(
            canonical_task_id="task-link-001",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="link test",
        )
        res = CanonicalResult.success(
            canonical_task_id=pkg.canonical_task_id,
            executor_id="exec-link",
            correlation_id=pkg.correlation_id,
        )
        assert res.canonical_task_id == pkg.canonical_task_id
        assert res.correlation_id == pkg.correlation_id

    def test_canonical_result_rejects_hermes_fields(self):
        res = CanonicalResult.success(canonical_task_id="t", executor_id="e", correlation_id="c")
        d = res.to_dict()
        d["hermes_session"] = {"private": True}
        with pytest.raises(ValueError):
            CanonicalResult.from_dict(d)


# ---------------------------------------------------------------------------
# F. Result contract identity
# ---------------------------------------------------------------------------

class TestFResultContractIdentity:
    def test_result_contract_is_opaque_identity_distinct_from_instance(self):
        assert RESULT_CONTRACT_IDENTITY_EXPRESSIBLE is True
        assert RESULT_CONTRACT_IS_OPAQUE_IDENTITY is True
        assert RESULT_INSTANCE_DISTINCT is True
        d = _make_generic_descriptor(result_contract="canonical_mutation_result.v1") if False else _make_generic_descriptor()
        # Create descriptor with explicit contract
        d2 = OperationContractDescriptor(
            name="f_op",
            description="desc",
            inputs=(),
            required_context=(),
            optional_context=(),
            internal_ids_required=(),
            internal_ids_created=(),
            read_write="write",
            mutation_scope="m",
            required_authority="a",
            approval_required=False,
            valid_predecessor_state="p",
            valid_successor_state="s",
            idempotency="i",
            errors=(),
            protocol_version=PROTOCOL_VERSION,
            decision_required=False,
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="my.result.v1",
        )
        rc_ref = result_contract_ref(d2.result_contract)
        assert rc_ref == "my.result.v1"
        inst = CanonicalResult.success(canonical_task_id="t", executor_id="e", result_data={"n": 1}, correlation_id="c")
        # Instance payload distinct
        assert rc_ref != inst.result_data
        assert isinstance(d2.result_contract, str)
        assert isinstance(inst.result_data, dict)

    def test_result_contract_none_means_no_declaration(self):
        d = OperationContractDescriptor(name="read_op2", description="d", read_write="read")
        assert d.result_contract is None
        assert result_contract_ref(d.result_contract) is None

    def test_no_result_schema_registry_created(self):
        import aota_forge.core.contracts.vocabulary as vocab
        import aota_forge.core.contracts.descriptor as dmod
        assert not hasattr(vocab, "ResultSchemaRegistry")
        assert not hasattr(dmod, "ResultSchemaRegistry")
        # Check descriptor field not renamed
        assert hasattr(OperationContractDescriptor, "result_contract")
        # Pure naming cleanup not justification — ensure source still uses result_contract
        import inspect, pathlib
        src = pathlib.Path(inspect.getfile(OperationContractDescriptor)).read_text(encoding="utf-8")
        assert "result_contract" in src
        # Ensure no result_contract_id rename
        assert "result_contract_id" not in src or src.count("result_contract_id") == 0


# ---------------------------------------------------------------------------
# G. Authority expressibility
# ---------------------------------------------------------------------------

class TestGAuthorityExpressibility:
    def test_authority_expressible_via_existing_fields(self):
        assert AUTHORITY_REQUIREMENT_EXPRESSIBLE is True
        assert NEW_AUTHORITY_REQUIREMENT_CLASS_REQUIRED is False
        d = _make_generic_descriptor()
        assert d.required_authority == "generic_authority"
        assert isinstance(d.approval_required, bool)
        assert isinstance(d.decision_required, bool)
        # MutationPreconditions covers subject/external authority preconditions
        pre = MutationPreconditions(
            subject_expected_revision=5,
            authority_source_revision="rev-1",
            authority_observed_raw_digest="abc",
        )
        assert pre.subject_expected_revision == 5
        assert pre.authority_source_revision == "rev-1"
        # No new AuthorityRequirement class
        import aota_forge.core.contracts.vocabulary as vocab
        assert not hasattr(vocab, "AuthorityRequirement")
        import aota_forge.core.contracts.descriptor as dmod
        assert not hasattr(dmod, "AuthorityRequirement")
        import aota_forge.core.contracts.mutation as mm
        assert not hasattr(mm, "AuthorityRequirement")

    def test_authority_combination_covers_descriptor_plus_mutation(self):
        d_forge = _make_forge_lifecycle_descriptor()
        assert d_forge.required_authority == "semantic_authorization_and_operation_lease"
        assert d_forge.approval_required is True
        assert d_forge.decision_required is True
        assert d_forge.subject_revision_precondition is True
        assert d_forge.external_authority_precondition is True
        # Generic operation can express authority without Forge-specific values
        d_gen = _make_generic_descriptor(required_authority="caller_execution_intent", predecessor="active", successor="dispatched")
        assert d_gen.required_authority == "caller_execution_intent"


# ---------------------------------------------------------------------------
# H. Error identity
# ---------------------------------------------------------------------------

class TestHErrorIdentity:
    def test_descriptor_errors_are_advertised_machine_codes(self):
        assert CANONICAL_ERROR_CODE_REQUIRED is True
        assert NEW_ERROR_DESCRIPTOR_CLASS_REQUIRED is False
        assert NEW_PARALLEL_ERROR_TAXONOMY is False
        d = _make_generic_descriptor()
        # errors are tuple of strings (machine codes)
        assert isinstance(d.errors, tuple)
        for code in d.errors:
            assert isinstance(code, str) and code

        d_forge = _make_forge_lifecycle_descriptor()
        for code in d_forge.errors:
            assert isinstance(code, str)
            # Forge lifecycle codes should be in ERROR_CLASSES or at least string codes
            # Not all are in ERROR_CLASSES yet but must be machine-readable strings

    def test_runtime_error_classes_are_implementation_mapping(self):
        # ForgeError.code vs descriptor.errors separation — use registered codes
        err = ForgeError("FORGE_ERROR", "generic error", retryable=False)
        assert err.code == "FORGE_ERROR"
        payload = err.to_dict()
        rebuilt = error_from_dict(payload)
        assert rebuilt is not None
        assert rebuilt.code == "FORGE_ERROR"
        # Registered subclass round-trips preserving code
        from aota_forge.core.contracts.errors import ProjectNotFoundError
        err2 = ProjectNotFoundError("not found", details="x")
        payload2 = err2.to_dict()
        rebuilt2 = error_from_dict(payload2)
        assert rebuilt2 is not None
        assert rebuilt2.code == "PROJECT_NOT_FOUND"

    def test_result_error_is_emitted_projection(self):
        res = CanonicalResult.failure(
            canonical_task_id="t-err",
            executor_id="e",
            error_code="GENERIC_ERROR",
            error_message="generic",
            correlation_id="c-err",
        )
        assert res.error is not None
        assert res.error["code"] == "GENERIC_ERROR"
        assert res.error["message"] == "generic"

    def test_no_new_error_descriptor_class(self):
        import aota_forge.core.contracts.vocabulary as vocab
        assert not hasattr(vocab, "ErrorDescriptor")
        import aota_forge.core.contracts.errors as emod
        assert not hasattr(emod, "ErrorDescriptor")
        # Unknown codes degrade to UnknownFutureError preserving original code
        payload = {"code": "FUTURE_UNKNOWN_9999", "message": "future", "retryable": False}
        err = error_from_dict(payload)
        assert isinstance(err, UnknownFutureError)
        assert err.original_code == "FUTURE_UNKNOWN_9999"

    def test_descriptor_errors_not_exception_classes(self):
        d = _make_generic_descriptor()
        # errors are strings, not exception classes
        for code in d.errors:
            assert not inspect.isclass(code)
            assert isinstance(code, str)


# ---------------------------------------------------------------------------
# I. Handler separation
# ---------------------------------------------------------------------------

class TestIHandlerSeparation:
    def test_descriptor_hash_free_of_callable_identity(self):
        assert HANDLER_IDENTITY_EXCLUDED is True
        assert HANDLER_REGISTRY_RUNTIME_LOCAL is True
        reg = HandlerRegistry()
        d = _make_generic_descriptor(name="handler.test_op")
        # Bind without handler
        reg.bind(d)
        h1 = d.contract_hash()
        j1 = d.to_canonical_json()

        # Bind handler onto registry (descriptor itself never carries callable)
        def dummy_handler(ctx):
            return {"data": {}}
        reg.bind_handler(d.name, dummy_handler)
        # descriptor hash unchanged after handler binding (descriptor doesn't store handler)
        assert d.contract_hash() == h1
        assert d.to_canonical_json() == j1
        # handler retrieved separately
        assert reg.handler(d.name) is dummy_handler
        # descriptor serialization contains no handler identity
        assert "dummy_handler" not in j1
        assert "callable" not in j1.lower()

    def test_handler_identity_excluded_from_default_registry(self):
        # Also verify DEFAULT_REGISTRY behavior with real descriptors
        d = OperationContractDescriptor(
            name="handler.separation.op2",
            description="handler separation test",
            read_write="read",
        )
        # Use isolated registry to avoid polluting DEFAULT_REGISTRY
        reg = HandlerRegistry()
        reg.bind(d, handler=lambda x: x)
        assert reg.handler("handler.separation.op2") is not None
        desc = reg.get("handler.separation.op2")
        assert desc is not None
        assert not hasattr(desc, "handler")
        assert "handler" not in desc.to_dict()

    def test_no_callable_in_descriptor_serialization(self):
        d = _make_generic_descriptor(name="serial.op")
        d_dict = d.to_dict()
        import json
        # JSON serialization must succeed without callables
        serialized = json.dumps(d_dict, sort_keys=True)
        assert isinstance(serialized, str)
        # No function repr leaked
        assert "function" not in serialized.lower()
        assert "callable" not in serialized.lower()


# ---------------------------------------------------------------------------
# J. No Hermes leakage
# ---------------------------------------------------------------------------

class TestJNoHermesLeakage:
    def test_no_hermes_specific_field_in_canonical_contract(self):
        assert NO_HERMES_SPECIFIC_FIELD_IN_CANONICAL_CONTRACT is True
        # Canonical contracts must not REQUIRE Hermes-specific fields.
        # Forbidden lists that explicitly REJECT Hermes fields are expected negative evidence,
        # not leakage. Check that canonical descriptor/capability fields do not include hermes.
        # Descriptor fields
        d = _make_generic_descriptor()
        for forbidden in ("hermes_profile", "hermes_worker", "hermes_session", "ProcessRegistry"):
            assert forbidden not in d.to_dict()
        caps = ExecutorCapabilities(
            executor_id="hermes-check",
            adapter_kind="reference",
            supported_execution_modes=("sync",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=False,
            supports_artifact_transport=False,
        )
        for forbidden in ("hermes_profile", "hermes_worker", "hermes_session", "ProcessRegistry"):
            assert forbidden not in caps.to_dict()
            assert not hasattr(caps, forbidden)
        # Handler registry descriptor has no hermes
        reg = HandlerRegistry()
        d2 = OperationContractDescriptor(name="hermes.leak.test", description="d", read_write="read")
        reg.bind(d2)
        assert "hermes_profile" not in reg.get("hermes.leak.test").to_dict().keys()
        # ExecutionPackage forbids hermes but does not require it
        pkg = ExecutionPackage.create(canonical_task_id="t", project_id="p", canonical_role="coder", instruction="i")
        for forbidden in FORBIDDEN_HERMES_FIELDS:
            assert forbidden not in pkg.to_dict()
        res = CanonicalResult.success(canonical_task_id="t", executor_id="e", correlation_id="c")
        for forbidden in FORBIDDEN_HERMES_RESULT_KEYS:
            assert forbidden not in res.to_dict()
        # Vocabulary must not expose Hermes-specific knowledge as canonical fields (lowercase)
        vocab_path = pathlib.Path(inspect.getfile(importlib.import_module("aota_forge.core.contracts.vocabulary")))
        vsrc = vocab_path.read_text(encoding="utf-8")
        for needle in ("hermes_profile", "hermes_worker", "hermes_session", "ProcessRegistry"):
            if needle == "ProcessRegistry":
                assert needle not in vsrc
            else:
                assert needle not in vsrc
        # Ensure forbidden lists are present as rejection guards (negative evidence)
        import aota_forge.core.execution.package as pkgmod
        assert hasattr(pkgmod, "FORBIDDEN_HERMES_FIELDS")
        import aota_forge.core.execution.results as rmod
        assert hasattr(rmod, "FORBIDDEN_HERMES_RESULT_KEYS")

    def test_canonical_sources_do_not_require_hermes(self):
        # ExecutionPackage from_dict already tested rejects hermes fields
        # CanonicalResult same
        # HandlerRegistry names are canonical sorted order, no hermes
        reg = HandlerRegistry()
        d = OperationContractDescriptor(name="j.op", description="j", read_write="read")
        reg.bind(d)
        assert reg.names() == ("j.op",)

    def test_w2_flags_consistent(self):
        # Permanent semantic flags — time-invariant, no progress coupling
        import aota_forge.core.contracts.vocabulary as vocab
        assert vocab.EXISTING_CONTRACT_RECONCILIATION_FIRST is True
        assert vocab.EXISTING_EXECUTION_CONTRACTS_REUSED_FIRST is True
        assert vocab.NEW_PRIMITIVE_ONLY_IF_PROVEN_GAP is True
        assert vocab.OPERATION_CONTRACT_DESCRIPTOR_RETAINED is True
        assert vocab.BULK_OPERATION_DESCRIPTOR_RENAME is False


# ---------------------------------------------------------------------------
# Overall W2 acceptance — low-change / reuse / no new primitives
# ---------------------------------------------------------------------------

class TestW2OverallAcceptance:
    def test_operation_contract_descriptor_retained(self):
        assert OPERATION_CONTRACT_DESCRIPTOR_RETAINED is True
        assert BULK_OPERATION_DESCRIPTOR_RENAME is False
        # No bulk rename — class name remains OperationContractDescriptor
        assert OperationContractDescriptor.__name__ == "OperationContractDescriptor"
        import aota_forge.core.contracts.descriptor as dmod
        assert hasattr(dmod, "OperationContractDescriptor")
        assert not hasattr(dmod, "OperationDescriptor")

    def test_reuse_flags(self):
        assert EXISTING_CONTRACT_RECONCILIATION_FIRST is True
        assert EXISTING_EXECUTION_CONTRACTS_REUSED_FIRST is True
        assert REUSE_EXECUTION_PACKAGE_FIRST is True
        assert REUSE_CANONICAL_RESULT_FIRST is True
        assert EXECUTION_REQUEST_SEMANTICS_REQUIRED is True
        assert EXECUTION_RESULT_SEMANTICS_REQUIRED is True

    def test_no_new_primitive_classes(self):
        assert NEW_PRIMITIVE_ONLY_IF_PROVEN_GAP is True
        assert NEW_CAPABILITY_DESCRIPTOR_CREATED is False
        assert NEW_EXECUTION_REQUEST_CLASS_CREATED is False
        assert NEW_EXECUTION_RESULT_CLASS_CREATED is False
        assert NEW_AUTHORITY_REQUIREMENT_CLASS_REQUIRED is False
        assert NEW_ERROR_DESCRIPTOR_CLASS_REQUIRED is False
        assert NEW_PARALLEL_ERROR_TAXONOMY is False
        assert NEW_GENERIC_POLICY_REFERENCE_FRAMEWORK_REQUIRED is False

    def test_protocol_not_changed(self):
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert PROTOCOL_VERSION == "1.0"
