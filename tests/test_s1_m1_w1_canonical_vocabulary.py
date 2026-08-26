"""S1/M1/W1 — Canonical Vocabulary & Identity (minimum boundary).

Tests are deterministic, no network, no live Hermes runtime.
Covers required distinctions A-F from W1 spec plus R1 repairs.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib

import pytest

from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import OPERATION_CONTRACT_PROTOCOL, PROTOCOL_VERSION
from aota_forge.core.contracts.vocabulary import (
    CANONICAL_IDENTITY_FIELDS,
    CANONICAL_VOCABULARY_DEFINED,
    CAPABILITY_FIRST_CLASS,
    CAPABILITY_IS_EXECUTOR,
    CAPABILITY_IS_PROFILE,
    CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT,
    HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY,
    M2_IMPLEMENTED,
    NEW_EXECUTION_DESCRIPTOR_CREATED,
    NEW_ID_BROKER_CREATED,
    NEW_TASK_DESCRIPTOR_CREATED,
    NEWTYPE_RUNTIME_NOMINAL_SEPARATION,
    NEWTYPE_RUNTIME_REPRESENTATION,
    NEWTYPE_STATIC_DOMAIN_SEPARATION,
    OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION,
    OPERATION_TASK_EXECUTION_DISTINCT,
    RESULT_CONTRACT_IDENTITY_DEFINED,
    RESULT_INSTANCE_DISTINCT,
    RUNTIME_LOCATOR_FIELDS,
    RUNTIME_LOCATOR_IS_NOT_CANONICAL_IDENTITY,
    RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY,
    TASK_FULL_MODEL_DEFERRED_TO_S2,
    TASK_IDENTITY_DISTINCT_FROM_EXECUTION_ATTEMPT_IDENTITY,
    TASK_IDENTITY_STABLE_ACROSS_EXECUTION_ATTEMPTS,
    VOCABULARY_DISTINCT,
    VOCABULARY_DOMAINS,
    W2_IMPLEMENTED,
    W3_IMPLEMENTED,
    CapabilityRef,
    ContractRevisionFingerprint,
    ExecutionAttemptRef,
    OperationSemanticIdentity,
    ResultContractRef,
    TaskRef,
    contract_revision_of,
    execution_attempt_ref,
    is_canonical_identity_field,
    is_runtime_locator,
    operation_semantic_identity_of,
    result_contract_ref,
    semantic_identity_distinct_from_revision,
    task_ref,
)
from aota_forge.core.execution.capabilities import ExecutorCapabilities
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.results import CanonicalResult

# ---------------------------------------------------------------------------
# A. Vocabulary distinction — 5 domains are not aliases
# ---------------------------------------------------------------------------


class TestVocabularyDistinction:
    def test_canonical_vocabulary_defined(self):
        assert CANONICAL_VOCABULARY_DEFINED is True
        assert OPERATION_TASK_EXECUTION_DISTINCT is True
        assert VOCABULARY_DISTINCT is True
        assert set(VOCABULARY_DOMAINS) == {"Operation", "Task", "Execution", "Capability", "Result"}
        assert len(VOCABULARY_DOMAINS) == 5

    def test_operation_task_execution_capability_result_are_distinct_types(self):
        # Each domain has a distinct type/ref representation
        assert OperationSemanticIdentity is not ContractRevisionFingerprint
        assert task_ref is not execution_attempt_ref
        assert result_contract_ref is not task_ref
        # Capability domain is ExecutorCapabilities, not a profile string
        assert ExecutorCapabilities.__name__ == "ExecutorCapabilities"

    def test_no_work_item_creates_new_task_or_execution_descriptor(self):
        assert NEW_TASK_DESCRIPTOR_CREATED is False
        assert NEW_EXECUTION_DESCRIPTOR_CREATED is False
        assert NEW_ID_BROKER_CREATED is False

    def test_w2_w3_m2_not_implemented(self):
        assert W2_IMPLEMENTED is False
        assert W3_IMPLEMENTED is False
        assert M2_IMPLEMENTED is False

    def test_domain_strings_are_not_aliases(self):
        domains = list(VOCABULARY_DOMAINS)
        assert len(set(domains)) == len(domains)
        assert "Operation" != "Task"
        assert "Task" != "Execution"
        assert "Execution" != "Capability"
        assert "Capability" != "Result"


# ---------------------------------------------------------------------------
# A2. NewType runtime semantics — truthful separation
# ---------------------------------------------------------------------------


class TestNewTypeRuntimeSemantics:
    def test_newtype_runtime_representation_is_str(self):
        assert NEWTYPE_RUNTIME_REPRESENTATION == "str"
        assert NEWTYPE_STATIC_DOMAIN_SEPARATION is True
        assert NEWTYPE_RUNTIME_NOMINAL_SEPARATION is False

    def test_task_ref_behaves_as_runtime_str(self):
        t = task_ref("x")
        # NewType values remain ordinary runtime strings
        assert isinstance(t, str)
        assert type(t) is str
        assert t == "x"
        assert str(t) == "x"

    def test_execution_attempt_ref_behaves_as_runtime_str(self):
        e = execution_attempt_ref("x")
        assert isinstance(e, str)
        assert type(e) is str
        assert e == "x"
        assert str(e) == "x"

    def test_runtime_type_is_not_domain_discriminator(self):
        t = task_ref("same")
        e = execution_attempt_ref("same")
        # Both are plain str at runtime — type identity cannot distinguish domains
        assert type(t) is str
        assert type(e) is str
        assert type(t) is type(e)
        # isinstance against NewType is not meaningful for nominal separation;
        # at runtime both are just str instances.
        assert isinstance(t, str) and isinstance(e, str)

    def test_static_domain_separation_via_newtype_declarations(self):
        # Static distinction is via the NewType declarations themselves, not runtime values.
        assert TaskRef is not ExecutionAttemptRef
        assert TaskRef is not ResultContractRef
        assert ExecutionAttemptRef is not ResultContractRef
        assert CapabilityRef is not TaskRef
        # Functions that construct the refs are also distinct objects
        assert task_ref is not execution_attempt_ref
        # NewType supertypes are str (static typing layer)
        # Use __supertype__ where available (Python 3.10+)
        for nt in (TaskRef, ExecutionAttemptRef, ResultContractRef, CapabilityRef):
            # NewType objects expose __supertype__ == str
            assert getattr(nt, "__supertype__", str) is str

    def test_always_true_domain_guard_removed(self):
        import aota_forge.core.contracts.vocabulary as vocab

        assert not hasattr(vocab, "task_and_execution_distinct"), (
            "task_and_execution_distinct must be removed — it was an always-True guard"
        )


# ---------------------------------------------------------------------------
# A3. Same lexical value cross-domain case
# ---------------------------------------------------------------------------


class TestSameLexicalValueCrossDomain:
    def test_same_string_across_task_and_execution_domains(self):
        task = task_ref("same-id")
        attempt = execution_attempt_ref("same-id")

        # Lexical equality does NOT collapse conceptual domains.
        # With NewType (runtime str), values with same text are equal as strings.
        assert str(task) == str(attempt)
        assert task == attempt  # type: ignore[comparison-overlap]  # expected: runtime str equality
        # Domain distinction is static-semantic, not string inequality.
        # Code must NOT rely on str(task) != str(attempt) to prove separation.
        assert TaskRef is not ExecutionAttemptRef

    def test_same_string_across_result_contract_and_task(self):
        task = task_ref("shared-lexical")
        rc = result_contract_ref("shared-lexical")
        assert rc is not None
        assert str(task) == str(rc)
        assert task == rc  # runtime str equality holds
        # Yet TaskRef and ResultContractRef remain distinct static domains
        assert TaskRef is not ResultContractRef

    def test_task_stability_with_same_lexical_attempt(self):
        # Even when lexical value collides, task identity stability holds
        t = task_ref("collision")
        e1 = execution_attempt_ref("collision")
        e2 = execution_attempt_ref("other-attempt")
        assert str(t) == str(e1)
        assert t != e2
        # Task identity remains stable across attempts — no requirement that lexical values differ
        assert t == task_ref("collision")


# ---------------------------------------------------------------------------
# B. Task vs Execution identity
# ---------------------------------------------------------------------------


class TestTaskVsExecutionIdentity:
    def test_task_identity_stable_across_execution_attempts(self):
        assert TASK_IDENTITY_STABLE_ACROSS_EXECUTION_ATTEMPTS is True
        assert TASK_IDENTITY_DISTINCT_FROM_EXECUTION_ATTEMPT_IDENTITY is True
        assert TASK_FULL_MODEL_DEFERRED_TO_S2 is True

        t = task_ref("task-abc-123")
        e1 = execution_attempt_ref("attempt-1-for-task-abc-123")
        e2 = execution_attempt_ref("attempt-2-for-task-abc-123")

        # Task ref remains identical across attempts
        assert t == task_ref("task-abc-123")
        assert e1 != e2
        # The same canonical task identity can map to 0..n execution attempts
        # Domain separation is proven via NewType declarations, not string inequality
        assert TaskRef is not ExecutionAttemptRef

    def test_retry_does_not_require_changing_task_identity(self):
        t = task_ref("stable-task-id")
        first_attempt = execution_attempt_ref("exec-attempt-1")
        retry_attempt = execution_attempt_ref("exec-attempt-2")
        assert t == task_ref("stable-task-id")
        assert first_attempt != retry_attempt

    def test_execution_package_task_vs_package_identity(self):
        pkg1 = ExecutionPackage.create(
            canonical_task_id="task-stable-001",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="do work",
            package_id="pkg-attempt-001",
        )
        pkg2 = ExecutionPackage.create(
            canonical_task_id="task-stable-001",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="do work",
            package_id="pkg-attempt-002",
        )
        assert pkg1.canonical_task_id == pkg2.canonical_task_id == "task-stable-001"
        assert pkg1.package_id != pkg2.package_id
        # TaskRef vs ExecutionAttemptRef domain distinction is static, not via value check
        assert TaskRef is not ExecutionAttemptRef
        assert task_ref(pkg1.canonical_task_id) == "task-stable-001"
        assert execution_attempt_ref(pkg1.package_id) == "pkg-attempt-001"

    def test_task_and_execution_ref_validation(self):
        with pytest.raises(ValueError):
            task_ref("")
        with pytest.raises(ValueError):
            execution_attempt_ref("  ")
        with pytest.raises(ValueError):
            task_ref("   ")
        with pytest.raises(ValueError):
            execution_attempt_ref("")

    def test_task_ref_opaque_generic_allows_prefix_like_strings(self):
        # W1-R1: canonical Core must not reject pid:/hermes:/worker:/process_registry: prefixes
        # These are opaque strings — prefix policy is at mapping boundary, not canonical constructor.
        assert task_ref("pid:12345") == "pid:12345"
        assert task_ref("hermes:abc") == "hermes:abc"
        assert task_ref("worker:xyz") == "worker:xyz"
        assert task_ref("process_registry:123") == "process_registry:123"
        assert task_ref("PID:9999") == "PID:9999"
        # Generic validation still requires non-empty after strip
        assert task_ref("  pid:123  ") == "pid:123"

    def test_canonical_result_task_identity_is_task_domain(self):
        res = CanonicalResult.success(
            canonical_task_id="task-abc-123",
            executor_id="ref-1",
            correlation_id="corr-1",
        )
        assert res.canonical_task_id == "task-abc-123"
        assert res.executor_id != res.canonical_task_id


# ---------------------------------------------------------------------------
# C. Operation semantic identity vs contract revision
# ---------------------------------------------------------------------------


class TestOperationSemanticVsRevision:
    def _make_descriptor(self, name: str, description: str, errors: tuple[str, ...] = ("E1",)) -> OperationContractDescriptor:
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
            protocol_version=PROTOCOL_VERSION,
            decision_required=False,
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="test.contract.v1",
        )

    def test_semantic_identity_stable_across_revision_change(self):
        assert OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION is True
        assert CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT is True

        d1 = self._make_descriptor("my_op", "original description")
        d2 = self._make_descriptor("my_op", "updated description with more detail")

        s1 = operation_semantic_identity_of(d1)
        s2 = operation_semantic_identity_of(d2)
        r1 = contract_revision_of(d1)
        r2 = contract_revision_of(d2)

        assert s1 == s2
        assert s1.protocol_family == OPERATION_CONTRACT_PROTOCOL
        assert s1.operation_name == "my_op"
        assert r1.contract_hash != r2.contract_hash
        assert r1.protocol_version == r2.protocol_version == PROTOCOL_VERSION
        assert semantic_identity_distinct_from_revision(s1, r1) is True
        assert type(s1) is not type(r1)
        assert s1 != r1  # type: ignore[comparison-overlap]

    def test_contract_hash_remains_revision_fingerprint_not_semantic_identity(self):
        d = self._make_descriptor("another_op", "desc")
        sem = operation_semantic_identity_of(d)
        rev = contract_revision_of(d)
        assert rev.contract_hash not in (sem.protocol_family, sem.operation_name)
        assert CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT is True

    def test_protocol_namespace_not_redefined_in_w1(self):
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert PROTOCOL_VERSION == "1.0"
        d = self._make_descriptor("x_op", "desc")
        sem = operation_semantic_identity_of(d)
        assert sem.protocol_family == OPERATION_CONTRACT_PROTOCOL

    def test_contract_hash_is_deterministic(self):
        d = self._make_descriptor("det_op", "desc")
        assert d.contract_hash() == d.contract_hash()
        assert contract_revision_of(d).contract_hash == d.contract_hash()


# ---------------------------------------------------------------------------
# D. Result contract vs result instance
# ---------------------------------------------------------------------------


class TestResultContractVsInstance:
    def test_result_contract_identity_defined_and_distinct(self):
        assert RESULT_CONTRACT_IDENTITY_DEFINED is True
        assert RESULT_INSTANCE_DISTINCT is True

        d = OperationContractDescriptor(
            name="result_op",
            description="has result contract",
            inputs=(InputSpec("x", "str"),),
            required_context=(),
            optional_context=(),
            internal_ids_required=(),
            internal_ids_created=(),
            read_write="write",
            mutation_scope="test",
            required_authority="auth",
            approval_required=False,
            valid_predecessor_state="a",
            valid_successor_state="b",
            idempotency="idem",
            errors=("E1",),
            protocol_version=PROTOCOL_VERSION,
            decision_required=False,
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="canonical_mutation_result.v1",
        )
        rc_ref = result_contract_ref(d.result_contract)
        assert rc_ref == "canonical_mutation_result.v1"

        inst = CanonicalResult.success(
            canonical_task_id="t-1",
            executor_id="exec-1",
            result_data={"value": 42},
            correlation_id="corr-1",
        )
        assert rc_ref != inst.result_data
        assert isinstance(inst.result_data, dict)
        assert rc_ref != inst

    def test_result_contract_none_when_not_declared(self):
        d = OperationContractDescriptor(
            name="read_op",
            description="read op no result contract",
            read_write="read",
        )
        assert d.result_contract is None
        assert result_contract_ref(d.result_contract) is None

    def test_result_instance_is_distinct_from_contract_string(self):
        d = OperationContractDescriptor(
            name="w_op",
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
        r1 = CanonicalResult.success(canonical_task_id="t", executor_id="e", result_data={"n": 1}, correlation_id="c1")
        r2 = CanonicalResult.success(canonical_task_id="t", executor_id="e", result_data={"n": 2}, correlation_id="c2")
        assert r1.result_data != r2.result_data
        assert result_contract_ref(d.result_contract) == "my.result.v1"
        assert result_contract_ref(d.result_contract) is not None


# ---------------------------------------------------------------------------
# E. Runtime identifier isolation (generic)
# ---------------------------------------------------------------------------


class TestRuntimeIdentifierIsolation:
    def test_runtime_locator_not_canonical_identity(self):
        assert RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY is True
        assert RUNTIME_LOCATOR_IS_NOT_CANONICAL_IDENTITY is True
        assert HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY is True
        # Generic principle holds — runtime locators are not canonical identity
        assert is_runtime_locator("process_id") is True or is_runtime_locator("worker_handle") is True
        assert is_canonical_identity_field("task_ref") is True
        assert is_canonical_identity_field("execution_attempt_ref") is True

    def test_canonical_identity_fields_do_not_include_runtime_locators(self):
        assert CANONICAL_IDENTITY_FIELDS.isdisjoint(RUNTIME_LOCATOR_FIELDS)
        # No Hermes-specific strings in canonical fields
        for forbidden in ("hermes_profile", "hermes_worker", "hermes_session", "ProcessRegistry"):
            assert forbidden not in CANONICAL_IDENTITY_FIELDS
            assert forbidden not in RUNTIME_LOCATOR_FIELDS
        # Generic check: canonical fields are exactly the expected set
        assert CANONICAL_IDENTITY_FIELDS == frozenset(
            {"task_ref", "execution_attempt_ref", "operation_semantic_identity", "result_contract_ref"}
        )

    def test_hermes_as_external_example_not_canonical(self):
        # Hermes identifiers are used only as external negative examples — not required as canonical
        external_hermes_examples = ["hermes_profile", "ProcessRegistry", "PID", "hermes_worker"]
        for ex in external_hermes_examples:
            assert is_canonical_identity_field(ex) is False
            assert ex not in CANONICAL_IDENTITY_FIELDS
        # Canonical task identity is generic — external runtime IDs are not canonical
        assert HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY is True
        assert RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY is True

    def test_pid_not_equal_canonical_execution_identity(self):
        # PID is external runtime locator, not required canonical execution identity
        # Use generic vocabulary to prove: generic runtime fields != canonical fields
        assert "process_id" not in CANONICAL_IDENTITY_FIELDS
        assert is_canonical_identity_field("process_id") is False
        # Capability is not profile: no profile field on caps
        caps = ExecutorCapabilities(
            executor_id="hermes-fake",
            adapter_kind="hermes",
            supported_execution_modes=("async",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        assert caps.executor_id == "hermes-fake"
        assert not hasattr(caps, "hermes_profile")
        assert not hasattr(caps, "profile")

    def test_forbidden_equivalences(self):
        d = OperationContractDescriptor(name="op", description="d", read_write="read")
        assert not hasattr(d, "tool_call")
        assert task_ref("t-1") != "worker-1"
        # ExecutionAttemptRef with same lexical text as Hermes PID would still be distinct domain statically
        exec_ref = execution_attempt_ref("PID:1234")
        # At runtime it is a plain string equal to "PID:1234", but domain separation is static
        assert exec_ref == "PID:1234"
        assert ExecutionAttemptRef is not TaskRef
        assert CAPABILITY_IS_PROFILE is False
        assert HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY is True


# ---------------------------------------------------------------------------
# E2. Canonical Core Hermes leakage guard
# ---------------------------------------------------------------------------


class TestCanonicalCoreHermesLeakage:
    def test_production_vocabulary_does_not_expose_hermes_locator_sets(self):
        import aota_forge.core.contracts.vocabulary as vocab

        assert not hasattr(vocab, "HERMES_RUNTIME_LOCATORS"), "canonical Core must not expose HERMES_RUNTIME_LOCATORS"
        # Production API must not require Hermes-specific fields
        assert "hermes_profile" not in CANONICAL_IDENTITY_FIELDS
        assert "hermes_worker" not in CANONICAL_IDENTITY_FIELDS
        assert "hermes_session" not in CANONICAL_IDENTITY_FIELDS
        assert "ProcessRegistry" not in CANONICAL_IDENTITY_FIELDS

    def test_production_source_contains_no_hermes_specific_runtime_vocab(self):
        # Focused architectural guard: read production source file directly
        vocab_path = pathlib.Path(inspect.getfile(importlib.import_module("aota_forge.core.contracts.vocabulary")))
        source = vocab_path.read_text(encoding="utf-8")
        # Forbidden Hermes-specific knowledge that must not appear in canonical Core
        forbidden_substrings = [
            "hermes_profile",
            "hermes_worker",
            "hermes_session",
            "ProcessRegistry",
            "hermes:",
            "process_registry:",
        ]
        for needle in forbidden_substrings:
            # Allow the flag name HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY which contains "HERMES" uppercase,
            # but not the lowercase hermes_profile etc. enumerated above.
            assert needle not in source, f"vocabulary.py must not contain Hermes-specific knowledge: {needle!r}"

    def test_runtime_locator_fields_are_generic(self):
        # RUNTIME_LOCATOR_FIELDS must be generic, not Hermes-specific enumeration
        for forbidden in ("hermes_profile", "hermes_worker", "hermes_session", "ProcessRegistry"):
            assert forbidden not in RUNTIME_LOCATOR_FIELDS
        # Generic principle flag must be present
        assert RUNTIME_LOCATOR_NOT_CANONICAL_IDENTITY is True
        # Canonical identity fields remain disjoint from generic runtime locators
        assert CANONICAL_IDENTITY_FIELDS.isdisjoint(RUNTIME_LOCATOR_FIELDS)


# ---------------------------------------------------------------------------
# F. Existing execution contract compatibility
# ---------------------------------------------------------------------------


class TestExistingExecutionContractCompatibility:
    def test_execution_package_remains_usable(self):
        pkg = ExecutionPackage.create(
            canonical_task_id="task-compat-1",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="compat test",
        )
        assert pkg.canonical_task_id == "task-compat-1"
        assert pkg.package_id != pkg.canonical_task_id
        rebuilt = ExecutionPackage.from_dict(pkg.to_dict())
        assert rebuilt == pkg

    def test_canonical_result_remains_usable(self):
        res = CanonicalResult.success(
            canonical_task_id="task-compat-2",
            executor_id="compat-exec",
            correlation_id="corr-compat",
        )
        assert res.ok is True
        assert res.status == "completed"
        rebuilt = CanonicalResult.from_dict(res.to_dict())
        assert rebuilt == res

    def test_executor_capabilities_remains_usable_and_not_profile(self):
        caps = ExecutorCapabilities(
            executor_id="compat-exec-2",
            adapter_kind="reference",
            supported_execution_modes=("sync",),
            supports_streaming_events=True,
            supports_task_cancellation=True,
            supports_task_resume=True,
            supports_structured_result=True,
            supported_canonical_roles=("coder", "reviewer"),
            supported_isolation_modes=("process",),
            supports_working_directory=True,
            supports_artifact_transport=True,
        )
        assert caps.supports_mode("sync") is True
        assert CAPABILITY_FIRST_CLASS is True
        assert CAPABILITY_IS_EXECUTOR is False
        assert not hasattr(caps, "profile")
        assert not hasattr(caps, "hermes_profile")

    def test_no_duplicate_replacement_classes_created(self):
        import aota_forge.core.contracts.vocabulary as vocab

        assert not hasattr(vocab, "ExecutionPackage")
        assert not hasattr(vocab, "CanonicalResult")
        assert not hasattr(vocab, "ExecutorCapabilities")
        assert NEW_TASK_DESCRIPTOR_CREATED is False

    def test_descriptor_result_contract_still_opaque_string(self):
        d = OperationContractDescriptor(
            name="compat_op",
            description="compat",
            inputs=(InputSpec("a", "str"),),
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
        assert isinstance(d.result_contract, str)
        assert d.result_contract == "my.result.v1"
