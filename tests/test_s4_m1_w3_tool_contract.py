"""S4/M1/W3 — Tool Provider Contract.

Covers:
  ToolProvider distinct from ExecutorAdapter and tool call
  OperationContractDescriptor remains authority
  HandlerRegistry remains runtime-local
  request has no executor lifecycle identities
  validation reuses canonical seam
  read-only vs mutation authority, deny → zero side effect
  typed failure, private transport isolation
  no registry / YAML / universal ProviderRequest/ProviderResult
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_TOOL = REPO_ROOT / "aota_forge" / "core" / "providers" / "tool.py"
PROVIDERS_INIT = REPO_ROOT / "aota_forge" / "core" / "providers" / "__init__.py"


def _read_tool_src() -> str:
    return PROVIDERS_TOOL.read_text(encoding="utf-8")


def _parse_tool_tree():
    return ast.parse(_read_tool_src())


# ---------------------------------------------------------------------------
# 1. ToolProvider distinct from ExecutorAdapter
# ---------------------------------------------------------------------------

class TestToolProviderDistinctFromExecutor:
    def test_tool_provider_not_executor(self):
        from aota_forge.core.providers.tool import ToolProvider
        from aota_forge.core.execution.adapter import ExecutorAdapter

        # protocol has only invoke
        tree = _parse_tool_tree()
        methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolProvider":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.add(item.name)
        assert "invoke" in methods
        for bad in ("dispatch", "status", "result", "cancel", "resume"):
            assert bad not in methods

        class FakeToolProvider:
            def invoke(self, request):  # type: ignore[no-untyped-def]
                from aota_forge.core.providers.tool import ToolResponse
                return ToolResponse.success(payload={})

        assert not issubclass(FakeToolProvider, ExecutorAdapter)
        assert isinstance(FakeToolProvider(), ToolProvider)

    def test_tool_request_has_no_executor_lifecycle_fields(self):
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        desc = OperationContractDescriptor(name="op_check", description="d")
        field_names = {f.name for f in dataclasses.fields(ToolRequest)}
        for forbidden in ("canonical_task_id", "dispatch_attempt_id", "adapter_handle", "executor_id", "package_id"):
            assert forbidden not in field_names
        src = _read_tool_src()
        assert "canonical_task_id" not in src
        assert "adapter_handle" not in src
        assert "ExecutionPackage" not in src
        # ensure no import of execution package
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "execution" not in mod or "contracts" in mod  # contracts is allowed
                for alias in node.names:
                    assert alias.name != "ExecutionPackage"
                    assert alias.name != "ExecutorAdapter"


# ---------------------------------------------------------------------------
# 2. ToolProvider != tool call
# ---------------------------------------------------------------------------

class TestToolProviderNotToolCall:
    def test_provider_is_not_single_call(self):
        from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        class FakeProvider:
            def invoke(self, request: ToolRequest) -> ToolResponse:
                return ToolResponse.success(payload={"result": "deferred"})

        provider = FakeProvider()
        desc = OperationContractDescriptor(name="tool_op", description="semantic op")
        req = ToolRequest(operation=desc, inputs={})
        resp = provider.invoke(req)
        assert isinstance(resp, ToolResponse)
        assert provider is not resp  # provider is interface, not a string/call dict
        assert not isinstance(provider, str)
        assert not isinstance(provider, dict)

    def test_provider_source_has_no_invocation_mechanism(self):
        src = _read_tool_src().lower()
        # provider module must not hardcode shell/mcp/http mechanisms
        for kw in ("subprocess",):
            assert kw not in src

    def test_operation_descriptor_is_semantic_authority(self):
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor
        from aota_forge.core.providers.tool import ToolRequest

        desc = OperationContractDescriptor(name="semantic_tool", description="what should happen")
        req = ToolRequest(operation=desc, inputs={})
        assert req.operation.name == "semantic_tool"
        assert not hasattr(req.operation, "shell_command")
        assert not hasattr(req.operation, "mcp_call")

        src = _read_tool_src()
        assert "OperationContractDescriptor" in src
        # must not create ToolDescriptor etc
        assert "class ToolDescriptor" not in src
        assert "class ProviderOperationDescriptor" not in src
        assert "ToolOperationDescriptor" not in src


# ---------------------------------------------------------------------------
# 3. HandlerRegistry remains runtime-local
# ---------------------------------------------------------------------------

class TestHandlerRegistryBoundary:
    def test_handler_registry_runtime_local(self):
        from aota_forge.core.contracts.registry import HandlerRegistry
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor
        from aota_forge.core.providers.tool import ToolProvider

        registry = HandlerRegistry()
        desc = OperationContractDescriptor(name="op_handler_test", description="d")
        def handler(x):  # type: ignore[no-untyped-def]
            return x
        registry.bind(desc, handler)
        assert registry.get("op_handler_test") is desc
        assert registry.handler("op_handler_test") is handler

        # ToolProvider is not HandlerRegistry
        assert ToolProvider is not HandlerRegistry  # type: ignore[comparison]

        # ToolProvider protocol not inheriting HandlerRegistry
        class FakeTool:
            def invoke(self, request):  # type: ignore[no-untyped-def]
                from aota_forge.core.providers.tool import ToolResponse
                return ToolResponse.success(payload={})
        assert not issubclass(FakeTool, HandlerRegistry)

        src = _read_tool_src()
        # must not import HandlerRegistry as ToolProvider
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "registry" not in (node.module or "")
                for alias in node.names:
                    assert alias.name != "HandlerRegistry"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "HandlerRegistry" not in alias.name

    def test_tool_provider_is_not_handler_registry(self):
        src = _read_tool_src()
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolProvider":
                # ensure no inheritance from HandlerRegistry
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        assert base.id != "HandlerRegistry"


# ---------------------------------------------------------------------------
# 4. Input validation reuses canonical seam
# ---------------------------------------------------------------------------

class TestInputValidationReuse:
    def test_validation_reuses_canonical_seam(self):
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
        from aota_forge.core.contracts.errors import UnknownInputError, MissingRequiredInputError

        desc = OperationContractDescriptor(
            name="validated_op",
            description="d",
            inputs=(InputSpec(name="prompt", type="str"),),
        )
        # valid
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        assert req.inputs["prompt"] == "hello"

        # unknown input rejected via canonical seam
        with pytest.raises(UnknownInputError):
            ToolRequest(operation=desc, inputs={"prompt": "hello", "unknown": 1})

        # missing required
        with pytest.raises(MissingRequiredInputError):
            ToolRequest(operation=desc, inputs={})

        # type invalid
        from aota_forge.core.contracts.errors import InputTypeError
        with pytest.raises(InputTypeError):
            ToolRequest(operation=desc, inputs={"prompt": 123})

        # ensure validate_inputs is imported/used
        src = _read_tool_src()
        assert "validate_inputs" in src
        assert "from aota_forge.core.contracts.validation import validate_inputs" in src


# ---------------------------------------------------------------------------
# 5. Read-only vs mutation authority
# ---------------------------------------------------------------------------

class TestMutationAuthority:
    def test_read_only_authority_path(self):
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        read_desc = OperationContractDescriptor(
            name="read_op", description="read", read_write="read"
        )
        req = ToolRequest(operation=read_desc, inputs={})
        assert req.operation.read_write == "read"
        # read-only should not require mutation scope
        assert req.operation.mutation_scope is None

    def test_mutation_descriptor_carries_authority(self):
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
        from aota_forge.core.contracts.version import PROTOCOL_VERSION
        write_desc = OperationContractDescriptor(
            name="write_op",
            description="write",
            inputs=(InputSpec("x", "str"),),
            required_context=("principal",),
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
        assert write_desc.read_write == "read-write"
        assert write_desc.mutation_scope == "subject"
        assert write_desc.required_authority == "lease"

    def test_unauthorized_mutation_zero_side_effect(self):
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
        from aota_forge.core.contracts.version import PROTOCOL_VERSION
        from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult

        invocations: list[ToolRequest] = []

        class CountingProvider:
            def invoke(self, request: ToolRequest) -> ToolResponse:
                invocations.append(request)
                return ToolResponse.success(payload={"ok": True})

        provider = CountingProvider()
        desc = OperationContractDescriptor(
            name="mut_op",
            description="mut",
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
        req = ToolRequest(operation=desc, inputs={})

        def authorized_invoke(authority_result: AuthorityResult, request: ToolRequest) -> ToolResponse | None:
            if authority_result.decision != AuthorityDecision.ALLOW:
                return None
            return provider.invoke(request)

        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        result = authorized_invoke(deny, req)
        assert result is None
        assert len(invocations) == 0

        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        result2 = authorized_invoke(allow, req)
        assert result2 is not None
        assert len(invocations) == 1

        # ensure existing authority semantics reused, no new taxonomy
        src = _read_tool_src()
        assert "class AuthorityEngine" not in src
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Authority" not in node.name


# ---------------------------------------------------------------------------
# 6. Typed failure projection
# ---------------------------------------------------------------------------

class TestTypedFailure:
    def test_failure_projection(self):
        from aota_forge.core.providers.tool import ToolResponse
        from aota_forge.core.contracts.errors import ForgeError, InputSizeError

        ok = ToolResponse.success(payload={"a": 1})
        assert ok.ok is True
        assert ok.error is None

        err = InputSizeError("too large")
        fail = ToolResponse.failure(err)
        assert fail.ok is False
        assert fail.error["code"] == "INPUT_SIZE_EXCEEDED"
        assert "retryable" in fail.error

        with pytest.raises(Exception):
            ToolResponse(ok=True, payload={}, error={"code": "X", "message": "m"})

        with pytest.raises(Exception):
            ToolResponse(ok=False, payload=None, error=None)

        src = _read_tool_src()
        assert "CONTEXT_" not in src
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Error" not in node.name


# ---------------------------------------------------------------------------
# 7. Capability semantics reuse
# ---------------------------------------------------------------------------

class TestCapabilitySemantics:
    def test_capability_semantics_reused(self):
        from aota_forge.core.execution.capabilities import ExecutorCapabilities

        caps = ExecutorCapabilities(
            executor_id="hermes",
            adapter_kind="hermes",
            supported_execution_modes=("async",),
            supports_streaming_events=False,
            supports_task_cancellation=False,
            supports_task_resume=False,
            supports_structured_result=True,
            supported_canonical_roles=("coder",),
            supported_isolation_modes=("process",),
            supports_working_directory=False,
            supports_artifact_transport=True,
        )
        assert caps.executor_id == "hermes"
        # provider does not promote ExecutorCapabilities directly
        src = _read_tool_src()
        assert "ExecutorCapabilities" not in src
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name != "ProviderCapabilities"
                assert node.name != "ToolCapabilities"

    def test_no_new_capability_type(self):
        src = _read_tool_src()
        assert "class ProviderCapabilities" not in src
        assert "class ToolCapabilities" not in src


# ---------------------------------------------------------------------------
# 8. Private transport metadata isolation
# ---------------------------------------------------------------------------

class TestPrivateTransportIsolation:
    def test_private_metadata_not_in_request_result(self):
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        class FakePrivateProvider:
            def __init__(self):
                self._mcp_server = "mcp://secret"
                self._http_endpoint = "https://internal.example"
                self._shell = "/bin/bash"

            def invoke(self, request: ToolRequest) -> ToolResponse:
                return ToolResponse.success(payload={"done": True})

        provider = FakePrivateProvider()
        desc = OperationContractDescriptor(name="tool_priv", description="d")
        req = ToolRequest(operation=desc, inputs={})
        resp = provider.invoke(req)

        req_dict = req.to_dict()
        for private in ("mcp_server", "http_endpoint", "shell", "endpoint", "database"):
            assert private not in str(req_dict).lower()
            assert private not in str(resp.payload).lower() if resp.payload else True
        assert provider._mcp_server not in str(resp.payload)
        assert provider._http_endpoint not in str(resp.payload)

        # request fields are strictly operation/inputs/correlation
        assert set(req_dict.keys()) == {"operation", "inputs", "correlation_id"}


# ---------------------------------------------------------------------------
# 9. No registry / YAML / universal ProviderRequest/ProviderResult
# ---------------------------------------------------------------------------

class TestNoUniversalFramework:
    def test_no_provider_registry(self):
        src = _read_tool_src()
        assert "ProviderRegistry" not in src
        assert "ProviderRouter" not in src
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "registry.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "routing.py").exists()

    def test_no_provider_yaml(self):
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "providers").exists()

    def test_no_universal_provider_request_result(self):
        src = _read_tool_src()
        assert "class ProviderRequest" not in src
        assert "class ProviderResult" not in src
        assert "ProviderRequest" not in src
        assert "ProviderResult" not in src
        assert "BaseProvider" not in src
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "types.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "base.py").exists()
        tree = _parse_tool_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name != "ProviderResult"
                assert node.name != "ProviderRequest"
