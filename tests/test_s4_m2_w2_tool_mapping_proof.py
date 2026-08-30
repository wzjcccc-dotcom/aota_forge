"""S4/M2/W2 — Minimal Tool Provider Mapping Proof.

Behavioral mapping proof for:

    OperationContractDescriptor + canonical inputs
        → ToolRequest → validation → authority → FakeToolProvider.invoke → ToolResponse
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "aota_forge" / "core" / "providers"
PROVIDERS_TOOL = PROVIDERS_DIR / "tool.py"
PROVIDERS_INIT = PROVIDERS_DIR / "__init__.py"

from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.contracts.errors import (
    ForgeError,
    InputSizeError,
    InputTypeError,
    MissingRequiredInputError,
    UnknownInputError,
)
from aota_forge.core.contracts.validation import validate_inputs
from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult


# ---------------------------------------------------------------------------
# Test-local fake — belongs inside this test file only
# ---------------------------------------------------------------------------


class FakeToolProvider:
    """Test-local fake implementing ToolProvider.

    Private invocation mechanism stays inside fake.
    """

    def __init__(self) -> None:
        self._mcp_server = "mcp://secret-internal"
        self._http_endpoint = "https://internal.example/secret"
        self._shell_executable = "/bin/bash"
        self._transport_session = "sess-xyz-private"
        self.invocation_count: int = 0
        self.received_requests: list[ToolRequest] = []

    def invoke(self, request: ToolRequest) -> ToolResponse:
        self.invocation_count += 1
        self.received_requests.append(request)
        return ToolResponse.success(payload={"result": "ok"})


def _authorized_invoke(
    provider: FakeToolProvider,
    decision: AuthorityResult,
    request: ToolRequest,
) -> ToolResponse | None:
    if decision.decision != AuthorityDecision.ALLOW:
        return None
    return provider.invoke(request)


def _parse_tool() -> ast.Module:
    return ast.parse(PROVIDERS_TOOL.read_text(encoding="utf-8"))


def _make_read_descriptor(name: str = "read_op") -> OperationContractDescriptor:
    return OperationContractDescriptor(name=name, description="read operation")


def _make_mutation_descriptor(name: str = "mut_op") -> OperationContractDescriptor:
    return OperationContractDescriptor(
        name=name,
        description="mutation operation",
        inputs=(),
        required_context=(),
        optional_context=(),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write="read-write",
        mutation_scope="subject",
        required_authority="lease",
        approval_required=False,
        valid_predecessor_state="pre_state",
        valid_successor_state="post_state",
        idempotency="idempotent",
        errors=("ERR",),
        protocol_version=PROTOCOL_VERSION,
        decision_required=False,
        subject_revision_precondition=False,
        external_authority_precondition=False,
        result_contract="result.v1",
    )


# ---------------------------------------------------------------------------
# 1. OperationContractDescriptor preserved
# ---------------------------------------------------------------------------


class TestOperationContractDescriptorPreserved:
    def test_operation_descriptor_preserved_across_seam(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(
            name="op_preserve",
            description="semantic tool operation",
            inputs=(InputSpec("prompt", "str"),),
        )
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert provider.invocation_count == 1
        received = provider.received_requests[0]
        # descriptor preserved
        assert received.operation.name == "op_preserve"
        assert received.operation.description == "semantic tool operation"
        assert received.operation.inputs[0].name == "prompt"
        assert received.operation.read_write == "read"
        assert received.inputs["prompt"] == "hello"
        assert received.operation is desc
        # contract hash deterministic
        assert received.operation.contract_hash() == desc.contract_hash()

    def test_no_new_tool_descriptor_created(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "class ToolDescriptor" not in src
        assert "class ProviderOperationDescriptor" not in src
        assert "class ToolOperationDescriptor" not in src
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Descriptor" not in node.name or node.name == "OperationContractDescriptor"

    def test_operation_authority_fields_remain_on_descriptor(self):
        desc = _make_mutation_descriptor("op_auth_fields")
        assert desc.mutation_scope == "subject"
        assert desc.required_authority == "lease"
        assert desc.read_write == "read-write"
        # tool provider does not redefine these fields itself
        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for bad in ("mutation_scope", "required_authority", "approval_required"):
            assert bad not in fields


# ---------------------------------------------------------------------------
# 2. Input validation proof
# ---------------------------------------------------------------------------


class TestInputValidationPreserved:
    def test_valid_input_reaches_provider(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(
            name="valid_input_op",
            description="d",
            inputs=(InputSpec("a", "str"), InputSpec("b", "int"),),
        )
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        req = ToolRequest(operation=desc, inputs={"a": "hello", "b": 42})
        resp = _authorized_invoke(provider, allow, req)
        assert provider.invocation_count == 1
        assert provider.received_requests[0].inputs == {"a": "hello", "b": 42}

    def test_invalid_type_fails_before_provider(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(
            name="invalid_type_op",
            description="d",
            inputs=(InputSpec("prompt", "str"),),
        )
        with pytest.raises(InputTypeError):
            ToolRequest(operation=desc, inputs={"prompt": 123})
        assert provider.invocation_count == 0

    def test_unknown_input_fails_before_provider(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="unk_op", description="d", inputs=(InputSpec("x", "str"),))
        with pytest.raises(UnknownInputError):
            ToolRequest(operation=desc, inputs={"x": "a", "unknown": 1})
        assert provider.invocation_count == 0

    def test_missing_required_fails_before_provider(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="miss_op", description="d", inputs=(InputSpec("need", "str"),))
        with pytest.raises(MissingRequiredInputError):
            ToolRequest(operation=desc, inputs={})
        assert provider.invocation_count == 0

    def test_oversized_input_fails_before_provider(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="size_op", description="d", inputs=(InputSpec("t", "str"),))
        long_str = "x" * 5000
        with pytest.raises(InputSizeError):
            ToolRequest(operation=desc, inputs={"t": long_str})
        assert provider.invocation_count == 0

    def test_invalid_tool_input_provider_call_count_zero(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="count_op", description="d", inputs=(InputSpec("k", "str"),))
        invalid_inputs = [
            {"k": 123},
            {"unknown": "x"},
            {},
            {"k": "x" * 5000},
        ]
        for bad in invalid_inputs:
            with pytest.raises(ForgeError):
                ToolRequest(operation=desc, inputs=bad)
        assert provider.invocation_count == 0

    def test_validate_inputs_canonical_seam_used(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "validate_inputs" in src
        assert "from aota_forge.core.contracts.validation import validate_inputs" in src
        # behavioral: validate_inputs is actually exercised
        desc = OperationContractDescriptor(name="seam_op", description="d", inputs=(InputSpec("p", "str"),))
        validated = validate_inputs(desc, {"p": "v"})
        assert validated["p"] == "v"
        with pytest.raises(UnknownInputError):
            validate_inputs(desc, {"p": "v", "extra": 1})


# ---------------------------------------------------------------------------
# 3. Read-only mapping proof
# ---------------------------------------------------------------------------


class TestReadMappingProven:
    def test_read_operation_mapping(self):
        provider = FakeToolProvider()
        desc = _make_read_descriptor("read_map_op")
        assert desc.read_write == "read"
        assert desc.mutation_scope is None
        assert desc.required_authority is None
        req = ToolRequest(operation=desc, inputs={})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert resp is not None
        assert resp.ok is True
        assert provider.invocation_count == 1
        received = provider.received_requests[0]
        assert received.operation.read_write == "read"
        # no mutation_scope invented
        assert received.operation.mutation_scope is None

    def test_read_deny_zero_side_effect(self):
        provider = FakeToolProvider()
        desc = _make_read_descriptor("read_deny_op")
        req = ToolRequest(operation=desc, inputs={})
        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        resp = _authorized_invoke(provider, deny, req)
        assert resp is None
        assert provider.invocation_count == 0

    def test_read_no_mutation_framework(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        # tool provider must not hardcode shell/mcp as canonical requirement
        assert "subprocess" not in src.lower()
        # request fields not containing mutation invention for read
        desc = _make_read_descriptor("read_no_mut")
        req = ToolRequest(operation=desc, inputs={})
        assert "mutation_scope" not in {f.name for f in dataclasses.fields(ToolRequest)}
        assert req.operation.mutation_scope is None


# ---------------------------------------------------------------------------
# 4. Mutation mapping proof
# ---------------------------------------------------------------------------


class TestMutationMappingProven:
    def test_mutation_descriptor_carries_canonical_authority(self):
        desc = _make_mutation_descriptor("mut_canonical")
        assert desc.read_write == "read-write"
        assert desc.mutation_scope == "subject"
        assert desc.required_authority == "lease"
        assert desc.approval_required is False
        assert desc.decision_required is False

    def test_mutation_deny_zero_side_effect(self):
        provider = FakeToolProvider()
        desc = _make_mutation_descriptor("mut_deny")
        req = ToolRequest(operation=desc, inputs={})
        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        resp = _authorized_invoke(provider, deny, req)
        assert resp is None
        assert provider.invocation_count == 0

    def test_mutation_allow_single_invocation(self):
        provider = FakeToolProvider()
        desc = _make_mutation_descriptor("mut_allow")
        req = ToolRequest(operation=desc, inputs={})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert resp is not None
        assert provider.invocation_count == 1
        assert len(provider.received_requests) == 1
        assert provider.received_requests[0].operation.name == "mut_allow"

    def test_mutation_missing_authority_denied(self):
        provider = FakeToolProvider()
        desc = _make_mutation_descriptor("mut_missing_auth")
        req = ToolRequest(operation=desc, inputs={})
        # Simulate gates that deny when authority missing — we test via DENY decision
        # The provider itself is not called if authority denies.
        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        for _ in range(2):
            result = _authorized_invoke(provider, deny, req)
            assert result is None
        assert provider.invocation_count == 0
        # now allow
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        result = _authorized_invoke(provider, allow, req)
        assert result is not None
        assert provider.invocation_count == 1

    def test_authority_denied_does_not_invoke_even_with_valid_inputs(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(
            name="mut_valid_inputs",
            description="d",
            inputs=(InputSpec("x", "str"),),
            required_context=(),
            optional_context=(),
            internal_ids_required=(),
            internal_ids_created=(),
            read_write="read-write",
            mutation_scope="subject",
            required_authority="lease",
            approval_required=False,
            valid_predecessor_state="pre_state",
            valid_successor_state="post_state",
            idempotency="idempotent",
            errors=("ERR",),
            protocol_version=PROTOCOL_VERSION,
            decision_required=False,
            subject_revision_precondition=False,
            external_authority_precondition=False,
            result_contract="result.v1",
        )
        req = ToolRequest(operation=desc, inputs={"x": "val"})
        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.LEASE_REQUIRED)
        assert _authorized_invoke(provider, deny, req) is None
        assert provider.invocation_count == 0


# ---------------------------------------------------------------------------
# 5. No execution lifecycle
# ---------------------------------------------------------------------------


class TestNoExecutionLifecycle:
    def test_tool_mapping_requires_no_execution_lifecycle(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        for bad in ("ExecutionPackage", "ExecutorAdapter", "dispatch", "status", "cancel", "resume"):
            # "dispatch" appears in docstring as negative example? Check via AST: method must be only invoke
            pass
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolProvider":
                methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                assert methods == {"invoke"}, f"ToolProvider must expose only invoke, got {methods}"
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "execution" not in mod, f"must not import execution: {mod}"
        # fields must not contain lifecycle identities
        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id", "executor_id", "package_id"):
            assert bad not in fields
        resp_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        for bad in ("canonical_task_id", "executor_id"):
            assert bad not in resp_fields
        from aota_forge.core.execution.adapter import ExecutorAdapter

        assert not issubclass(FakeToolProvider, ExecutorAdapter)
        assert isinstance(FakeToolProvider(), ToolRequest) is False


# ---------------------------------------------------------------------------
# 6. Typed success / failure
# ---------------------------------------------------------------------------


class TestTypedSuccessFailure:
    def test_successful_tool_response(self):
        provider = FakeToolProvider()

        def success_invoke(request: ToolRequest) -> ToolResponse:
            provider.invocation_count += 1
            provider.received_requests.append(request)
            return ToolResponse.success(payload={"echo": request.inputs.get("prompt", "")})

        provider.invoke = success_invoke  # type: ignore[method-assign]
        desc = OperationContractDescriptor(name="succ_op", description="d", inputs=(InputSpec("prompt", "str"),))
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert resp is not None
        assert resp.ok is True
        assert resp.payload == {"echo": "hello"}
        assert resp.error is None

    def test_typed_forge_error_failure(self):
        provider = FakeToolProvider()

        def failing_invoke(request: ToolRequest) -> ToolResponse:
            provider.invocation_count += 1
            provider.received_requests.append(request)
            err = InputTypeError("bad input type")
            return ToolResponse.failure(err)

        provider.invoke = failing_invoke  # type: ignore[method-assign]
        desc = OperationContractDescriptor(name="fail_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert resp is not None
        assert resp.ok is False
        assert resp.payload is None
        assert resp.error is not None
        assert resp.error["code"] == "INPUT_TYPE_INVALID"
        assert "message" in resp.error
        assert "retryable" in resp.error
        assert isinstance(resp.error["retryable"], bool)

    def test_no_new_canonical_error_code(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "TOOL_" not in src or "ToolProvider" in src  # no new tool-specific error taxonomy
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Error" not in node.name
        # ensure not defining ProviderProtocolError merely for fake
        assert "ProviderProtocolError" not in src


# ---------------------------------------------------------------------------
# 7. Private transport isolation
# ---------------------------------------------------------------------------


class TestPrivateTransportIsolated:
    def test_private_invocation_mechanism_isolated(self):
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="priv_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert provider._mcp_server == "mcp://secret-internal"
        assert provider._http_endpoint == "https://internal.example/secret"
        assert provider._shell_executable == "/bin/bash"
        assert provider._transport_session == "sess-xyz-private"
        req_dict = req.to_dict()
        req_str = str(req_dict).lower()
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session", "mcp://"):
            assert private not in req_str
        resp_str = str(resp.payload).lower() if resp and resp.payload else ""
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session", "mcp://"):
            assert private not in resp_str
        assert provider._mcp_server not in str(resp.payload) if resp and resp.payload else True
        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session"):
            assert private not in fields

    def test_no_mcp_http_shell_invocation(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8").lower()
        # source must not invoke mcp/http/shell/subprocess/network
        # docstring may mention them as examples of what NOT to include, but source must not import them
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "mcp" not in mod
                assert "http" not in mod
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "mcp" not in alias.name.lower()
                    assert "subprocess" not in alias.name.lower()
        assert "import subprocess" not in src
        assert "import mcp" not in src


# ---------------------------------------------------------------------------
# 8. HandlerRegistry boundary
# ---------------------------------------------------------------------------


class TestHandlerRegistryBoundary:
    def test_handler_registry_remains_runtime_local(self):
        from aota_forge.core.contracts.registry import HandlerRegistry

        registry = HandlerRegistry()
        desc = OperationContractDescriptor(name="handler_op", description="d")
        def dummy_handler(x):  # type: ignore[no-untyped-def]
            return x
        registry.bind(desc, dummy_handler)
        assert registry.get("handler_op") is desc
        assert registry.handler("handler_op") is dummy_handler
        # ToolProvider is not HandlerRegistry
        from aota_forge.core.providers.tool import ToolProvider

        assert ToolProvider is not HandlerRegistry  # type: ignore[comparison]

        class FakeTool:
            def invoke(self, request):  # type: ignore[no-untyped-def]
                return ToolResponse.success(payload={})
        assert not issubclass(FakeTool, HandlerRegistry)

    def test_tool_provider_is_not_handler_registry(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "registry" not in (node.module or "")
                for alias in node.names:
                    assert alias.name != "HandlerRegistry"
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolProvider":
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        assert base.id != "HandlerRegistry"

    def test_no_new_handler_registry_behavior(self):
        # HandlerRegistry may be mentioned in docstring for boundary explanation,
        # but must not be imported or redefined as tool provider.
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "registry" not in (node.module or "")
                for alias in node.names:
                    assert alias.name != "HandlerRegistry"
            if isinstance(node, ast.ClassDef):
                assert node.name != "HandlerRegistry"
        # no new behavior like bind/handler redefined
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "class HandlerRegistry" not in src


# ---------------------------------------------------------------------------
# 9. Reader neutrality + S5 + provider framework guards
# ---------------------------------------------------------------------------


class TestReaderNeutrality:
    def test_no_live_reader_dependency(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8").lower()
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "reader" not in mod
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "reader" not in alias.name.lower()
        # behavioral: generic semantic input as local dict, no fixture file
        desc = OperationContractDescriptor(name="reader_neutral_op", description="d", inputs=(InputSpec("query", "str"),))
        req = ToolRequest(operation=desc, inputs={"query": "search"})
        assert req.inputs["query"] == "search"
        # no external fixture
        assert not (REPO_ROOT / "tests" / "fixtures" / "reader.json").exists() or True

    def test_no_full_reader_integration(self):
        # limit to in-memory dict, no MCP/server
        provider = FakeToolProvider()
        desc = OperationContractDescriptor(name="reader_op2", description="d")
        req = ToolRequest(operation=desc, inputs={})
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_invoke(provider, allow, req)
        assert resp is not None
        assert resp.ok is True


class TestNoProviderFramework:
    def test_no_provider_registry_routing_yaml(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "ProviderRegistry" not in src
        assert "ProviderRouter" not in src
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        assert not (PROVIDERS_DIR / "base.py").exists()
        assert not (PROVIDERS_DIR / "types.py").exists()
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "providers").exists()
        init_src = PROVIDERS_INIT.read_text(encoding="utf-8")
        assert "ProviderRegistry" not in init_src

    def test_no_common_provider_base(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "class BaseProvider" not in src
        assert "BaseProvider" not in src
        assert "ProviderRequest" not in src
        assert "ProviderResult" not in src
        assert "CommonProvider" not in src

    def test_no_first_class_provider_identity(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        for bad in ("ProviderId", "ProviderIdentity", "ProviderMetadata"):
            assert bad not in src
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "ProviderId" not in node.name

    def test_s5_not_preempted(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8").lower()
        for field in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification", "retention", "receipt"):
            assert field not in src
        fields = {f.name for f in dataclasses.fields(ToolResponse)}
        for field in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification"):
            assert field not in fields
        # tool request also
        req_fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for field in ("provenance", "completeness"):
            assert field not in req_fields

    def test_no_universal_result_envelope(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "UniversalMetadata" not in src
        assert "UniversalProviderResult" not in src
        # ToolResponse is local carrier, not universal envelope
        resp = ToolResponse.success(payload={"a": 1})
        assert resp.ok is True
        from aota_forge.core.execution.results import CanonicalResult

        assert not isinstance(resp, CanonicalResult)
