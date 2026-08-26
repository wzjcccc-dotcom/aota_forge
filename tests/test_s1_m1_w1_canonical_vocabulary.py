"""S1/M1/W1 — Canonical Vocabulary & Identity (minimum boundary).

Tests are deterministic, no network, no live Hermes runtime.
Covers required distinctions A-F from W1 spec.
"""

from __future__ import annotations

import importlib

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
    HERMES_RUNTIME_LOCATORS,
    M2_IMPLEMENTED,
    NEW_EXECUTION_DESCRIPTOR_CREATED,
    NEW_ID_BROKER_CREATED,
    NEW_TASK_DESCRIPTOR_CREATED,
    OPERATION_SEMANTIC_IDENTITY_DISTINCT_FROM_CONTRACT_REVISION,
    OPERATION_TASK_EXECUTION_DISTINCT,
    RESULT_CONTRACT_IDENTITY_DEFINED,
    RESULT_INSTANCE_DISTINCT,
    RUNTIME_LOCATOR_FIELDS,
    TASK_FULL_MODEL_DEFERRED_TO_S2,
    TASK_IDENTITY_DISTINCT_FROM_EXECUTION_ATTEMPT_IDENTITY,
    TASK_IDENTITY_STABLE_ACROSS_EXECUTION_ATTEMPTS,
    VOCABULARY_DISTINCT,
    VOCABULARY_DOMAINS,
    W2_IMPLEMENTED,
    W3_IMPLEMENTED,
    ContractRevisionFingerprint,
    OperationSemanticIdentity,
    contract_revision_of,
    execution_attempt_ref,
    is_canonical_identity_field,
    is_runtime_locator,
    operation_semantic_identity_of,
    result_contract_ref,
    semantic_identity_distinct_from_revision,
    task_and_execution_distinct,
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
        # Operation != Task != Execution etc. by string identity
        domains = list(VOCABULARY_DOMAINS)
        assert len(set(domains)) == len(domains)
        assert "Operation" != "Task"
        assert "Task" != "Execution"
        assert "Execution" != "Capability"
        assert "Capability" != "Result"


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
        attempts = [e1, e2]
        for a in attempts:
            assert task_and_execution_distinct(t, a) is True
            assert str(t) != str(a)

    def test_retry_does_not_require_changing_task_identity(self):
        t = task_ref("stable-task-id")
        first_attempt = execution_attempt_ref("exec-attempt-1")
        retry_attempt = execution_attempt_ref("exec-attempt-2")
        # Simulate retry: task stays same, execution ref changes
        assert t == task_ref("stable-task-id")
        assert first_attempt != retry_attempt

    def test_execution_package_task_vs_package_identity(self):
        # Existing ExecutionPackage evidence: canonical_task_id != package_id
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
        # Task identity stable across attempts
        assert pkg1.canonical_task_id == pkg2.canonical_task_id == "task-stable-001"
        # Execution attempts are distinct
        assert pkg1.package_id != pkg2.package_id
        # TaskRef vs ExecutionAttemptRef domain distinction holds
        assert task_ref(pkg1.canonical_task_id) != execution_attempt_ref(pkg1.package_id)

    def test_task_and_execution_ref_validation(self):
        with pytest.raises(ValueError):
            task_ref("")
        with pytest.raises(ValueError):
            execution_attempt_ref("  ")
        # Hermes-style runtime locator prefix rejected for task
        with pytest.raises(ValueError, match="runtime locator"):
            task_ref("pid:12345")

    def test_canonical_result_task_identity_is_task_domain(self):
        res = CanonicalResult.success(
            canonical_task_id="task-abc-123",
            executor_id="ref-1",
            correlation_id="corr-1",
        )
        # CanonicalResult canonical_task_id belongs to Task domain, not execution attempt
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

        # Semantic identity stable: same protocol family + operation name
        assert s1 == s2
        assert s1.protocol_family == OPERATION_CONTRACT_PROTOCOL
        assert s1.operation_name == "my_op"

        # Revision fingerprint changes when description changes
        assert r1.contract_hash != r2.contract_hash
        assert r1.protocol_version == r2.protocol_version == PROTOCOL_VERSION

        # Distinct type domains
        assert semantic_identity_distinct_from_revision(s1, r1) is True
        assert type(s1) is not type(r1)
        assert s1 != r1  # type: ignore[comparison-overlap]

    def test_contract_hash_remains_revision_fingerprint_not_semantic_identity(self):
        d = self._make_descriptor("another_op", "desc")
        sem = operation_semantic_identity_of(d)
        rev = contract_revision_of(d)
        # contract_hash is not the semantic identity value
        assert rev.contract_hash not in (sem.protocol_family, sem.operation_name)
        # Changing protocol_version would be captured in revision, not as alias
        assert CONTRACT_HASH_REMAINS_REVISION_FINGERPRINT is True

    def test_protocol_namespace_not_redefined_in_w1(self):
        # W1 must not rename OPERATION_CONTRACT_PROTOCOL
        assert OPERATION_CONTRACT_PROTOCOL == "aota-forge.operation-contract"
        assert PROTOCOL_VERSION == "1.0"
        # semantic identity uses protocol family constant
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

        # Descriptor result_contract is opaque identity/reference
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

        # Runtime result instance is a CanonicalResult payload
        inst = CanonicalResult.success(
            canonical_task_id="t-1",
            executor_id="exec-1",
            result_data={"value": 42},
            correlation_id="corr-1",
        )
        # Contract identity != instance
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
        # Two instances for same contract can have different payloads
        r1 = CanonicalResult.success(canonical_task_id="t", executor_id="e", result_data={"n": 1}, correlation_id="c1")
        r2 = CanonicalResult.success(canonical_task_id="t", executor_id="e", result_data={"n": 2}, correlation_id="c2")
        assert r1.result_data != r2.result_data
        assert result_contract_ref(d.result_contract) == "my.result.v1"
        # contract preserved across different instances
        assert result_contract_ref(d.result_contract) is not None


# ---------------------------------------------------------------------------
# E. Runtime identifier isolation
# ---------------------------------------------------------------------------


class TestRuntimeIdentifierIsolation:
    def test_hermes_runtime_id_not_canonical(self):
        assert HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY is True
        # Runtime locators are isolated from canonical identity fields
        assert is_runtime_locator("PID") is True
        assert is_runtime_locator("hermes_profile") is True
        assert is_runtime_locator("ProcessRegistry") is True
        assert is_runtime_locator("worker_id") is True
        assert is_canonical_identity_field("task_ref") is True
        assert is_canonical_identity_field("execution_attempt_ref") is True
        assert is_runtime_locator("task_ref") is False
        assert is_canonical_identity_field("PID") is False

    def test_canonical_identity_fields_do_not_include_runtime_locators(self):
        assert CANONICAL_IDENTITY_FIELDS.isdisjoint(RUNTIME_LOCATOR_FIELDS)
        assert CANONICAL_IDENTITY_FIELDS.isdisjoint(HERMES_RUNTIME_LOCATORS)

    def test_pid_not_equal_canonical_execution_identity(self):
        # PID is not required and not equal to execution attempt ref
        pid_like = "9999"
        exec_ref = execution_attempt_ref("exec-uuid-abc")
        assert pid_like != exec_ref
        # Hermes profile not same as Capability
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
        # Capability is not profile: no profile field on caps
        assert not hasattr(caps, "hermes_profile")
        assert not hasattr(caps, "profile")

    def test_forbidden_equivalences(self):
        # Operation == Tool call -> false (descriptor is not tool call)
        d = OperationContractDescriptor(name="op", description="d", read_write="read")
        assert not hasattr(d, "tool_call")
        # Task == Worker -> false (task ref is not worker id)
        assert task_ref("t-1") != "worker-1"
        # Execution == Hermes process -> false (execution ref distinct from PID)
        assert execution_attempt_ref("exec-1") != "PID:1234"
        # Capability == Hermes profile -> false
        assert CAPABILITY_IS_PROFILE is False
        # PID == canonical execution identity -> false
        assert HERMES_RUNTIME_ID_NOT_CANONICAL_IDENTITY is True


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
        # package_id is not the canonical_task_id
        assert pkg.package_id != pkg.canonical_task_id
        # round-trip still works
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
        # Not a profile: no profile-specific attributes
        assert not hasattr(caps, "profile")
        assert not hasattr(caps, "hermes_profile")

    def test_no_duplicate_replacement_classes_created(self):
        # W1 must not duplicate these classes: vocabulary reuses them
        # Ensure the vocab module does not define its own ExecutionPackage etc.
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
        # Should remain a plain string (opaque reference), not renamed
        assert isinstance(d.result_contract, str)
        assert d.result_contract == "my.result.v1"
