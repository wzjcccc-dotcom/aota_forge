"""S4/M2/W3 — Heterogeneous Provider Challenge.

Proves Context Provider and Tool Provider coexist as genuinely different
seams without a universal Provider abstraction/registry/routing/identity
or new Core schema.

Heterogeneous private shapes, distinct protocols, direct vs referenced
results, authority guards, and metadata isolation.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "aota_forge" / "core" / "providers"
PROVIDERS_CONTEXT = PROVIDERS_DIR / "context.py"
PROVIDERS_TOOL = PROVIDERS_DIR / "tool.py"
PROVIDERS_INIT = PROVIDERS_DIR / "__init__.py"

from aota_forge.core.providers.context import ContextRequest, ContextResponse
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
from aota_forge.core.contracts.version import PROTOCOL_VERSION
from aota_forge.core.contracts.errors import ForgeError, InputSizeError, InputTypeError
from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult


# ---------------------------------------------------------------------------
# Test-local heterogeneous fakes — intentionally different private shapes
# ---------------------------------------------------------------------------


class HeterogeneousContextProvider:
    """Context fake with vector/reranker private metadata (never canonical)."""

    def __init__(self) -> None:
        self._database_url = "postgres://secret-internal"
        self._vector_index = "idx-private-001"
        self._reranker_model = "reranker-v2-private"
        self.call_count: int = 0
        self.received: list[ContextRequest] = []

    def fetch(self, request: ContextRequest) -> ContextResponse:
        self.call_count += 1
        self.received.append(request)
        return ContextResponse.success(payload=({"text": "ctx-direct"},), reference=None)


class HeterogeneousToolProvider:
    """Tool fake with MCP/HTTP/shell private metadata (never canonical)."""

    def __init__(self) -> None:
        self._mcp_server = "mcp://secret-internal"
        self._http_endpoint = "https://internal.example/secret"
        self._shell_executable = "/bin/bash"
        self._transport_session = "sess-xyz-private"
        self.invocation_count: int = 0
        self.received: list[ToolRequest] = []

    def invoke(self, request: ToolRequest) -> ToolResponse:
        self.invocation_count += 1
        self.received.append(request)
        return ToolResponse.success(payload={"result": "ok"})


def _authorized_fetch(provider: HeterogeneousContextProvider, decision: AuthorityResult, request: ContextRequest):
    if decision.decision != AuthorityDecision.ALLOW:
        return None
    return provider.fetch(request)


def _authorized_invoke(provider: HeterogeneousToolProvider, decision: AuthorityResult, request: ToolRequest):
    if decision.decision != AuthorityDecision.ALLOW:
        return None
    return provider.invoke(request)


def _parse_ctx() -> ast.Module:
    return ast.parse(PROVIDERS_CONTEXT.read_text(encoding="utf-8"))


def _parse_tool() -> ast.Module:
    return ast.parse(PROVIDERS_TOOL.read_text(encoding="utf-8"))


def _parse_init() -> ast.Module:
    return ast.parse(PROVIDERS_INIT.read_text(encoding="utf-8"))


ALLOW = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
DENY = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
BLOCKED = AuthorityResult(decision=AuthorityDecision.BLOCKED, reason_code=AuthorityReason.TARGET_REQUIRED)


# ---------------------------------------------------------------------------
# 1. Heterogeneous seams coexist
# ---------------------------------------------------------------------------


class TestHeterogeneousSeamsCoexist:
    def test_both_providers_instantiated_together(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert ctx is not tool
        assert ctx.call_count == 0
        assert tool.invocation_count == 0

    def test_context_and_tool_seams_are_distinct(self):
        # Protocol types are distinct
        assert ContextRequest is not ToolRequest
        assert ContextResponse is not ToolResponse
        assert HeterogeneousContextProvider is not HeterogeneousToolProvider
        # Methods differ
        assert hasattr(HeterogeneousContextProvider, "fetch")
        assert not hasattr(HeterogeneousContextProvider, "invoke")
        assert hasattr(HeterogeneousToolProvider, "invoke")
        assert not hasattr(HeterogeneousToolProvider, "fetch")
        # Core protocols distinct
        from aota_forge.core.providers.context import ContextProvider as CP
        from aota_forge.core.providers.tool import ToolProvider as TP

        assert CP is not TP

    def test_no_common_provider_base(self):
        src_ctx = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        src_tool = PROVIDERS_TOOL.read_text(encoding="utf-8")
        src_init = PROVIDERS_INIT.read_text(encoding="utf-8")
        for src in (src_ctx, src_tool, src_init):
            assert "class BaseProvider" not in src
            assert "BaseProvider" not in src
            assert "class CommonProvider" not in src
        # AST: no BaseProvider inheritance
        for tree in (_parse_ctx(), _parse_tool()):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert node.name != "BaseProvider"
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            assert base.id != "BaseProvider"

    def test_no_common_request_result(self):
        src_ctx = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        src_tool = PROVIDERS_TOOL.read_text(encoding="utf-8")
        for src in (src_ctx, src_tool):
            assert "ProviderRequest" not in src
            assert "ProviderResult" not in src
            assert "UniversalProviderResult" not in src
        fields_ctx = {f.name for f in dataclasses.fields(ContextRequest)}
        fields_tool = {f.name for f in dataclasses.fields(ToolRequest)}
        # no shared base field name forced to be common
        # Just verify distinct fields
        assert fields_ctx != fields_tool
        # response fields also distinct
        resp_ctx_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        resp_tool_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        # ContextResponse has reference, ToolResponse does not — proves distinct
        assert "reference" in resp_ctx_fields
        assert "reference" not in resp_tool_fields

    def test_both_protocols_coexist_in_same_core(self):
        from aota_forge.core.providers.context import ContextProvider
        from aota_forge.core.providers.tool import ToolProvider

        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert isinstance(ctx, ContextProvider)
        assert isinstance(tool, ToolProvider)
        assert not isinstance(ctx, ToolProvider)
        assert not isinstance(tool, ContextProvider)


# ---------------------------------------------------------------------------
# 2. Semantic mapping
# ---------------------------------------------------------------------------


class TestSemanticMapping:
    def test_context_semantic_request_maps_correctly(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(
            subject_ref="subject:abc-123",
            scope="scope:general",
            query="what is policy X",
            limit=10,
            cursor="cur-opaque",
            capability_ref="cap:search.v1",
            correlation_id="corr-001",
        )
        resp = _authorized_fetch(ctx, ALLOW, req)
        assert resp is not None
        assert ctx.call_count == 1
        r = ctx.received[0]
        assert r.subject_ref == "subject:abc-123"
        assert r.scope == "scope:general"
        assert r.query == "what is policy X"
        assert r.limit == 10
        assert r.cursor == "cur-opaque"
        assert r.capability_ref == "cap:search.v1"
        assert r.correlation_id == "corr-001"
        assert r.to_dict() == req.to_dict()

    def test_tool_semantic_operation_maps_correctly(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(
            name="tool_semantic_op",
            description="semantic tool operation",
            inputs=(InputSpec("prompt", "str"),),
        )
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        resp = _authorized_invoke(tool, ALLOW, req)
        assert resp is not None
        assert tool.invocation_count == 1
        r = tool.received[0]
        assert r.operation.name == "tool_semantic_op"
        assert r.operation is desc
        assert r.inputs["prompt"] == "hello"
        assert r.operation.contract_hash() == desc.contract_hash()

    def test_tool_mutation_descriptor_preserved(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(
            name="mut_op",
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
        req = ToolRequest(operation=desc, inputs={})
        _authorized_invoke(tool, ALLOW, req)
        assert tool.received[0].operation.read_write == "read-write"
        assert tool.received[0].operation.mutation_scope == "subject"
        assert tool.received[0].operation.required_authority == "lease"


# ---------------------------------------------------------------------------
# 3. Read-only vs mutation-capable challenge
# ---------------------------------------------------------------------------


class TestReadOnlyVsMutation:
    def test_context_deny_zero_calls(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        result = _authorized_fetch(ctx, DENY, req)
        assert result is None
        assert ctx.call_count == 0

    def test_context_allow_one_call(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        result = _authorized_fetch(ctx, ALLOW, req)
        assert result is not None
        assert ctx.call_count == 1

    def test_context_blocked_zero_calls(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        result = _authorized_fetch(ctx, BLOCKED, req)
        assert result is None
        assert ctx.call_count == 0

    def test_tool_deny_zero_side_effect(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(name="read_op", description="read")
        req = ToolRequest(operation=desc, inputs={})
        result = _authorized_invoke(tool, DENY, req)
        assert result is None
        assert tool.invocation_count == 0

    def test_tool_mutation_deny_zero_side_effect(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(
            name="mut_deny",
            description="mutation",
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
        result = _authorized_invoke(tool, DENY, req)
        assert result is None
        assert tool.invocation_count == 0

    def test_tool_allow_one_invocation(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(name="allow_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        result = _authorized_invoke(tool, ALLOW, req)
        assert result is not None
        assert tool.invocation_count == 1

    def test_unauthorized_counts_remain_zero_across_matrix(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        req_c = ContextRequest(subject_ref="s", scope="sc", query="q")
        desc = OperationContractDescriptor(name="m", description="d")
        req_t = ToolRequest(operation=desc, inputs={})
        for d in (DENY, BLOCKED):
            _authorized_fetch(ctx, d, req_c)
            _authorized_invoke(tool, d, req_t)
        assert ctx.call_count == 0
        assert tool.invocation_count == 0
        # now allow
        _authorized_fetch(ctx, ALLOW, req_c)
        _authorized_invoke(tool, ALLOW, req_t)
        assert ctx.call_count == 1
        assert tool.invocation_count == 1


# ---------------------------------------------------------------------------
# 4. Direct vs referenced results coexist
# ---------------------------------------------------------------------------


class TestDirectAndReferencedResults:
    def test_context_direct_payload(self):
        ctx = HeterogeneousContextProvider()

        def direct(request: ContextRequest) -> ContextResponse:
            ctx.call_count += 1
            ctx.received.append(request)
            return ContextResponse.success(payload=({"text": "direct-payload-1"}, {"text": "direct-payload-2"}))

        ctx.fetch = direct  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        resp = _authorized_fetch(ctx, ALLOW, req)
        assert resp is not None
        assert resp.ok is True
        assert len(resp.payload) == 2
        assert resp.payload[0]["text"] == "direct-payload-1"
        assert resp.reference is None

    def test_context_opaque_reference(self):
        ctx = HeterogeneousContextProvider()

        def ref(request: ContextRequest) -> ContextResponse:
            ctx.call_count += 1
            ctx.received.append(request)
            return ContextResponse.success(payload=(), reference="opaque://ref-123")

        ctx.fetch = ref  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        resp = _authorized_fetch(ctx, ALLOW, req)
        assert resp is not None
        assert resp.ok is True
        assert resp.reference == "opaque://ref-123"
        assert resp.payload == ()
        assert "artifact" not in resp.reference.lower()

    def test_context_opaque_remains_opaque(self):
        resp = ContextResponse.success(payload=(), reference="ref-opaque-xyz")
        assert resp.reference == "ref-opaque-xyz"
        fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for bad in ("provenance", "artifact_graph", "evidence_graph", "verification", "retention"):
            assert bad not in fields

    def test_tool_direct_payload(self):
        tool = HeterogeneousToolProvider()

        def success(request: ToolRequest) -> ToolResponse:
            tool.invocation_count += 1
            tool.received.append(request)
            return ToolResponse.success(payload={"echo": request.inputs.get("prompt", "")})

        tool.invoke = success  # type: ignore[method-assign]
        desc = OperationContractDescriptor(name="succ_op", description="d", inputs=(InputSpec("prompt", "str"),))
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        resp = _authorized_invoke(tool, ALLOW, req)
        assert resp is not None
        assert resp.ok is True
        assert resp.payload == {"echo": "hello"}

    def test_mixed_matrix_coexistence(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        # context direct
        req_c = ContextRequest(subject_ref="s", scope="sc", query="q")
        resp_c = _authorized_fetch(ctx, ALLOW, req_c)
        assert resp_c.ok is True
        # tool direct
        desc = OperationContractDescriptor(name="mix_op", description="d")
        req_t = ToolRequest(operation=desc, inputs={})
        resp_t = _authorized_invoke(tool, ALLOW, req_t)
        assert resp_t.ok is True
        # context reference
        ctx2 = HeterogeneousContextProvider()
        ctx2.fetch = lambda r: ContextResponse.success(payload=(), reference="opaque://ref-mix")  # type: ignore[method-assign]
        resp_ref = _authorized_fetch(ctx2, ALLOW, req_c)
        assert resp_ref.reference == "opaque://ref-mix"
        assert resp_c.ok and resp_t.ok and resp_ref.ok


# ---------------------------------------------------------------------------
# 5. Private metadata isolation and heterogeneity
# ---------------------------------------------------------------------------


class TestPrivateMetadataHeterogeneous:
    def test_both_private_shapes_coexist(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert ctx._database_url == "postgres://secret-internal"
        assert ctx._vector_index == "idx-private-001"
        assert ctx._reranker_model == "reranker-v2-private"
        assert tool._mcp_server == "mcp://secret-internal"
        assert tool._http_endpoint == "https://internal.example/secret"
        assert tool._shell_executable == "/bin/bash"
        assert tool._transport_session == "sess-xyz-private"

    def test_context_private_not_in_canonical(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        resp = _authorized_fetch(ctx, ALLOW, req)
        req_str = str(req.to_dict()).lower()
        resp_str = str(resp.payload).lower() + str(resp.reference or "").lower()
        for private in ("database_url", "vector_index", "reranker_model", "postgres"):
            assert private not in req_str
            assert private not in resp_str
        fields = {f.name for f in dataclasses.fields(ContextRequest)}
        for private in ("database_url", "vector_index", "reranker_model"):
            assert private not in fields
        resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for private in ("database_url", "vector_index", "reranker_model"):
            assert private not in resp_fields

    def test_tool_private_not_in_canonical(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(name="priv_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        resp = _authorized_invoke(tool, ALLOW, req)
        req_str = str(req.to_dict()).lower()
        resp_str = str(resp.payload).lower() if resp and resp.payload else ""
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session", "mcp://"):
            assert private not in req_str
            assert private not in resp_str
        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session"):
            assert private not in fields
        resp_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session"):
            assert private not in resp_fields

    def test_no_universal_metadata_created(self):
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8"), PROVIDERS_TOOL.read_text(encoding="utf-8"), PROVIDERS_INIT.read_text(encoding="utf-8")):
            assert "ProviderMetadata" not in src
            assert "UniversalMetadata" not in src
        # also check W3 file itself does not define universal metadata as canonical type (via AST)
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in ("ProviderMetadata", "UniversalMetadata")

    def test_no_private_metadata_in_serialization_even_after_call(self):
        ctx = HeterogeneousContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        _authorized_fetch(ctx, ALLOW, req)
        # ensure private strings not leaked into payload
        assert ctx._database_url not in str(ctx.received[0].to_dict())
        assert ctx._database_url not in str(ContextResponse.success(payload=({"text": "x"},)).payload)


# ---------------------------------------------------------------------------
# 6. Provider identity: no first-class ProviderId
# ---------------------------------------------------------------------------


class TestProviderIdentity:
    def test_no_first_class_provider_identity(self):
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8"), PROVIDERS_TOOL.read_text(encoding="utf-8"), PROVIDERS_INIT.read_text(encoding="utf-8")):
            assert "ProviderId" not in src
            assert "ProviderIdentity" not in src
        for tree in (_parse_ctx(), _parse_tool(), _parse_init()):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert "ProviderId" not in node.name
                    assert "ProviderIdentity" not in node.name
        # fake python identity is test-local only, not canonical
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert ctx.__class__.__name__ == "HeterogeneousContextProvider"
        assert tool.__class__.__name__ == "HeterogeneousToolProvider"
        # semantic identity remains on request/operation, not provider id
        req = ContextRequest(subject_ref="s", scope="sc", query="q", capability_ref="cap:v1")
        assert req.capability_ref == "cap:v1"
        desc = OperationContractDescriptor(name="id_op", description="d")
        t_req = ToolRequest(operation=desc, inputs={})
        assert t_req.operation.name == "id_op"

    def test_binding_hints_can_differ_without_canonical_id(self):
        ctx1 = HeterogeneousContextProvider()
        ctx2 = HeterogeneousContextProvider()
        # different object identities are sufficient for test-local binding
        assert ctx1 is not ctx2
        assert id(ctx1) != id(ctx2)
        tool1 = HeterogeneousToolProvider()
        tool2 = HeterogeneousToolProvider()
        assert tool1 is not tool2


# ---------------------------------------------------------------------------
# 7. Capability reuse, no new taxonomy
# ---------------------------------------------------------------------------


class TestCapabilityReuse:
    def test_capability_reuse_first(self):
        src_ctx = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        src_tool = PROVIDERS_TOOL.read_text(encoding="utf-8")
        for src in (src_ctx, src_tool):
            assert "ProviderCapabilities" not in src
            assert "ToolCapabilities" not in src
            assert "ContextCapabilities" not in src
        # context capability remains bounded generic reference string
        req = ContextRequest(subject_ref="s", scope="sc", query="q", capability_ref="cap:search.v1")
        assert req.capability_ref == "cap:search.v1"
        # tool semantics via OperationContractDescriptor
        desc = OperationContractDescriptor(name="cap_op", description="d")
        assert desc.name == "cap_op"
        t_req = ToolRequest(operation=desc, inputs={})
        assert t_req.operation is desc

    def test_operation_contract_descriptor_preserved(self):
        tool = HeterogeneousToolProvider()
        desc = OperationContractDescriptor(name="op_preserve", description="semantic", inputs=(InputSpec("prompt", "str"),))
        req = ToolRequest(operation=desc, inputs={"prompt": "hello"})
        _authorized_invoke(tool, ALLOW, req)
        received = tool.received[0]
        assert received.operation.name == "op_preserve"
        assert received.operation.contract_hash() == desc.contract_hash()
        # no new authority descriptor created
        src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "class ToolDescriptor" not in src
        assert "class ProviderOperationDescriptor" not in src


# ---------------------------------------------------------------------------
# 8. Authority reuse
# ---------------------------------------------------------------------------


class TestAuthorityReuse:
    def test_existing_authority_sufficient(self):
        # Both seams gate on same AuthorityResult without new model
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        req_c = ContextRequest(subject_ref="s", scope="sc", query="q")
        desc = OperationContractDescriptor(name="auth_op", description="d")
        req_t = ToolRequest(operation=desc, inputs={})
        assert _authorized_fetch(ctx, DENY, req_c) is None
        assert _authorized_invoke(tool, DENY, req_t) is None
        assert ctx.call_count == 0
        assert tool.invocation_count == 0
        assert _authorized_fetch(ctx, ALLOW, req_c) is not None
        assert _authorized_invoke(tool, ALLOW, req_t) is not None

    def test_no_new_provider_authority_model(self):
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8"), PROVIDERS_TOOL.read_text(encoding="utf-8"), PROVIDERS_INIT.read_text(encoding="utf-8")):
            assert "ProviderAuthority" not in src
            assert "ContextAuthority" not in src
            assert "ToolAuthority" not in src
        # context not forced through Tool operation semantics
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        assert not hasattr(req, "operation")


# ---------------------------------------------------------------------------
# 9. Typed failures + unknown fail-closed
# ---------------------------------------------------------------------------


class TestTypedFailures:
    def test_typed_context_failure_representable(self):
        ctx = HeterogeneousContextProvider()

        def failing(request: ContextRequest) -> ContextResponse:
            ctx.call_count += 1
            ctx.received.append(request)
            return ContextResponse.failure(InputSizeError("too big"))

        ctx.fetch = failing  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        resp = _authorized_fetch(ctx, ALLOW, req)
        assert resp.ok is False
        assert resp.error["code"] == "INPUT_SIZE_EXCEEDED"
        assert "message" in resp.error
        assert "retryable" in resp.error
        assert isinstance(resp.error["retryable"], bool)

    def test_typed_tool_failure_representable(self):
        tool = HeterogeneousToolProvider()

        def failing(request: ToolRequest) -> ToolResponse:
            tool.invocation_count += 1
            tool.received.append(request)
            return ToolResponse.failure(InputTypeError("bad type"))

        tool.invoke = failing  # type: ignore[method-assign]
        desc = OperationContractDescriptor(name="fail_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        resp = _authorized_invoke(tool, ALLOW, req)
        assert resp.ok is False
        assert resp.error["code"] == "INPUT_TYPE_INVALID"
        assert "message" in resp.error
        assert isinstance(resp.error["retryable"], bool)

    def test_no_new_error_taxonomy(self):
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8"), PROVIDERS_TOOL.read_text(encoding="utf-8")):
            assert "ProviderProtocolError" not in src
            assert "CONTEXT_" not in src
        for tree in (_parse_ctx(), _parse_tool()):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert "Error" not in node.name

    def test_unknown_future_error_fail_closed(self):
        # Unknown code must round-trip via UnknownFutureError semantics (existing M1)
        unknown = ForgeError("FUTURE_UNKNOWN_CODE_XYZ", "future", retryable=False)
        resp_c = ContextResponse.failure(unknown)
        assert resp_c.error["code"] == "FUTURE_UNKNOWN_CODE_XYZ"
        resp_t = ToolResponse.failure(unknown)
        assert resp_t.error["code"] == "FUTURE_UNKNOWN_CODE_XYZ"
        # Verify existing error registry handles unknown via error_from_dict
        from aota_forge.core.contracts.errors import error_from_dict

        err = error_from_dict(resp_c.error)
        assert err is not None
        assert err.code in ("FUTURE_UNKNOWN_CODE_XYZ", "UNKNOWN_FUTURE_ERROR")


# ---------------------------------------------------------------------------
# 10. No executor lifecycle
# ---------------------------------------------------------------------------


class TestNoExecutorLifecycle:
    def test_no_execution_lifecycle_required(self):
        for tree in (_parse_ctx(), _parse_tool()):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ("ContextProvider", "ToolProvider"):
                    methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
                    if node.name == "ContextProvider":
                        assert methods == {"fetch"}
                    else:
                        assert methods == {"invoke"}
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert "execution" not in mod, f"must not import execution: {mod}"
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "ExecutorAdapter" not in alias.name
                        assert "ExecutionPackage" not in alias.name
            # ensure not inheriting from execution lifecycle
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ("ContextProvider", "ToolProvider"):
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            assert base.id not in ("ExecutorAdapter", "ExecutionPackage", "BaseProvider")
        # fields must not contain lifecycle identities (strict)
        fields_ctx = {f.name for f in dataclasses.fields(ContextRequest)}
        fields_tool = {f.name for f in dataclasses.fields(ToolRequest)}
        for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id", "executor_id"):
            assert bad not in fields_ctx
            assert bad not in fields_tool
        resp_ctx_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        resp_tool_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        for bad in ("canonical_task_id", "executor_id"):
            assert bad not in resp_ctx_fields
            assert bad not in resp_tool_fields

    def test_provider_not_executor(self):
        from aota_forge.core.execution.adapter import ExecutorAdapter

        assert not issubclass(HeterogeneousContextProvider, ExecutorAdapter)
        assert not issubclass(HeterogeneousToolProvider, ExecutorAdapter)


# ---------------------------------------------------------------------------
# 11. No universal Provider framework
# ---------------------------------------------------------------------------


class TestNoUniversalProviderFramework:
    def test_no_universal_framework(self):
        combined = PROVIDERS_CONTEXT.read_text(encoding="utf-8") + PROVIDERS_TOOL.read_text(encoding="utf-8") + PROVIDERS_INIT.read_text(encoding="utf-8")
        for bad in ("BaseProvider", "ProviderRequest", "ProviderResult", "ProviderRegistry", "ProviderRouter", "ProviderIdentity", "ProviderId", "ProviderMetadata"):
            assert bad not in combined
        # also structure guards
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        assert not (PROVIDERS_DIR / "base.py").exists()
        assert not (PROVIDERS_DIR / "types.py").exists()

    def test_direct_composition_sufficient(self):
        # Call providers directly via authorized helpers — no registry indirection
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        req_c = ContextRequest(subject_ref="s", scope="sc", query="q")
        desc = OperationContractDescriptor(name="direct_op", description="d")
        req_t = ToolRequest(operation=desc, inputs={})
        r1 = _authorized_fetch(ctx, ALLOW, req_c)
        r2 = _authorized_invoke(tool, ALLOW, req_t)
        assert r1.ok is True
        assert r2.ok is True


# ---------------------------------------------------------------------------
# 12. Registry / routing / YAML not required
# ---------------------------------------------------------------------------


class TestNoRegistryRoutingYaml:
    def test_no_registry_routing_yaml(self):
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "providers").exists()
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8"), PROVIDERS_TOOL.read_text(encoding="utf-8"), PROVIDERS_INIT.read_text(encoding="utf-8")):
            assert "ProviderRegistry" not in src
            assert "ProviderRouter" not in src


# ---------------------------------------------------------------------------
# 13. ACF / Reader neutrality
# ---------------------------------------------------------------------------


class TestNeutrality:
    def test_provider_seams_not_acf_specific(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower()
        for kw in ("top_k", "embedding", "reranker", "hot/warm", "knowledge card"):
            assert kw not in src
        req_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        for bad in ("top_k", "embedding_model", "similarity_threshold", "vector_index", "hot_budget"):
            assert bad not in req_fields
        # even though fake has acf-like private labels, canonical remains generic
        ctx = HeterogeneousContextProvider()
        assert ctx._vector_index == "idx-private-001"
        assert ctx._reranker_model == "reranker-v2-private"
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        assert "vector_index" not in req.to_dict()

    def test_provider_seams_not_reader_specific(self):
        src = PROVIDERS_TOOL.read_text(encoding="utf-8").lower()
        tree = _parse_tool()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "reader" not in (node.module or "").lower()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "reader" not in alias.name.lower()
        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for bad in ("mcp_server", "http_endpoint", "transport"):
            assert bad not in fields
        tool = HeterogeneousToolProvider()
        assert tool._mcp_server == "mcp://secret-internal"
        desc = OperationContractDescriptor(name="reader_neutral", description="d")
        req = ToolRequest(operation=desc, inputs={})
        assert "mcp_server" not in str(req.to_dict()).lower()

    def test_no_live_reader_required(self):
        # No file dependency needed for provider instantiation
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert ctx is not None and tool is not None

    def test_canonical_tool_request_no_transport_encoding(self):
        desc = OperationContractDescriptor(name="transport_op", description="d")
        req = ToolRequest(operation=desc, inputs={})
        assert "mcp" not in str(req.to_dict()).lower()
        assert "http" not in str(req.to_dict()).lower()

    def test_no_mcp_http_shell_invocation_in_source(self):
        tree = _parse_tool()
        src_lower = PROVIDERS_TOOL.read_text(encoding="utf-8").lower()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "mcp" not in alias.name.lower()
                    assert "subprocess" not in alias.name.lower()
        assert "import subprocess" not in src_lower
        assert "import mcp" not in src_lower


# ---------------------------------------------------------------------------
# 14. S5 not preempted
# ---------------------------------------------------------------------------


class TestS5NotPreempted:
    def test_s5_governance_not_preempted(self):
        for src in (PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower(), PROVIDERS_TOOL.read_text(encoding="utf-8").lower()):
            for field in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification", "retention", "receipt", "artifact graph", "artifact manifest"):
                assert field not in src
        for fields in [
            {f.name for f in dataclasses.fields(ContextRequest)},
            {f.name for f in dataclasses.fields(ContextResponse)},
            {f.name for f in dataclasses.fields(ToolRequest)},
            {f.name for f in dataclasses.fields(ToolResponse)},
        ]:
            for bad in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification"):
                assert bad not in fields

    def test_opaque_reference_remains_opaque(self):
        resp = ContextResponse.success(payload=(), reference="opaque://s5-ref")
        assert resp.reference == "opaque://s5-ref"
        # not decoded into governance structures
        assert not hasattr(resp, "provenance")
        assert not hasattr(resp, "artifact_graph")


# ---------------------------------------------------------------------------
# 15. Second-provider core-schema challenge: no core change needed
# ---------------------------------------------------------------------------


class TestCoreSchemaUnchanged:
    def test_production_delta_zero(self):
        # Providers interfaces remain as M1, no new fields added
        ctx_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        assert ctx_fields == {"subject_ref", "scope", "query", "limit", "cursor", "capability_ref", "correlation_id"}
        resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        assert resp_fields == {"ok", "payload", "reference", "error"}
        tool_req_fields = {f.name for f in dataclasses.fields(ToolRequest)}
        assert tool_req_fields == {"operation", "inputs", "correlation_id"}
        tool_resp_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        assert tool_resp_fields == {"ok", "payload", "error"}

    def test_core_schema_coexistence_without_modification(self):
        # Importing both providers does not require universal envelope
        from aota_forge.core.providers.context import ContextProvider
        from aota_forge.core.providers.tool import ToolProvider

        assert ContextProvider is not ToolProvider
        # Both work without modifying core contracts
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        assert _authorized_fetch(ctx, ALLOW, ContextRequest(subject_ref="s", scope="sc", query="q")).ok is True
        assert _authorized_invoke(tool, ALLOW, ToolRequest(operation=OperationContractDescriptor(name="core_op", description="d"), inputs={})).ok is True


# ---------------------------------------------------------------------------
# 16. Mapping-proof sufficiency: behavioral evidence
# ---------------------------------------------------------------------------


class TestMappingProofSufficiency:
    def test_existing_or_fake_provider_proof_sufficient(self):
        """Architectural sufficiency: exercises canonical semantics, authority,
        invocation, success/failure projection, and heterogeneous shapes."""
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        # canonical request/operation semantics
        req_c = ContextRequest(subject_ref="s", scope="sc", query="q", limit=10)
        desc = OperationContractDescriptor(name="suff_op", description="d", inputs=(InputSpec("prompt", "str"),))
        req_t = ToolRequest(operation=desc, inputs={"prompt": "hi"})
        # authority boundary
        assert _authorized_fetch(ctx, DENY, req_c) is None
        assert _authorized_invoke(tool, DENY, req_t) is None
        assert ctx.call_count == 0 and tool.invocation_count == 0
        # invocation
        resp_c = _authorized_fetch(ctx, ALLOW, req_c)
        resp_t = _authorized_invoke(tool, ALLOW, req_t)
        assert resp_c.ok is True and resp_t.ok is True
        # success projection
        assert resp_c.payload == ({"text": "ctx-direct"},)
        assert resp_t.payload == {"result": "ok"}
        # failure projection
        ctx_fail = HeterogeneousContextProvider()
        ctx_fail.fetch = lambda r: ContextResponse.failure(InputTypeError("bad"))  # type: ignore[method-assign]
        assert _authorized_fetch(ctx_fail, ALLOW, req_c).ok is False
        tool_fail = HeterogeneousToolProvider()
        tool_fail.invoke = lambda r: ToolResponse.failure(InputSizeError("big"))  # type: ignore[method-assign]
        assert _authorized_invoke(tool_fail, ALLOW, req_t).ok is False
        # heterogeneous private shapes
        assert ctx._database_url != tool._mcp_server
        assert ctx._vector_index != tool._http_endpoint

    def test_heterogeneous_private_metadata_supported(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()
        # both shapes present and isolated
        assert hasattr(ctx, "_database_url")
        assert hasattr(tool, "_mcp_server")
        assert not hasattr(ctx, "_mcp_server")
        assert not hasattr(tool, "_database_url")


# ---------------------------------------------------------------------------
# 17. Combined behavioral matrix (section 29 minimum)
# ---------------------------------------------------------------------------


class TestW3RequiredBehavioralMatrix:
    def test_full_matrix(self):
        ctx = HeterogeneousContextProvider()
        tool = HeterogeneousToolProvider()

        # instantiate together already done

        # Context semantic request maps correctly (already tested) — re-verify one
        req_c = ContextRequest(subject_ref="subj", scope="scope", query="q", capability_ref="cap:v1", limit=1)
        assert req_c.subject_ref == "subj"

        # Tool semantic operation maps correctly
        desc = OperationContractDescriptor(name="matrix_op", description="d")
        req_t = ToolRequest(operation=desc, inputs={})
        assert req_t.operation.name == "matrix_op"

        # DENY paths
        assert _authorized_fetch(ctx, DENY, req_c) is None
        assert ctx.call_count == 0
        assert _authorized_invoke(tool, DENY, req_t) is None
        assert tool.invocation_count == 0

        # ALLOW paths
        assert _authorized_fetch(ctx, ALLOW, req_c) is not None
        assert ctx.call_count == 1
        assert _authorized_invoke(tool, ALLOW, req_t) is not None
        assert tool.invocation_count == 1

        # result styles
        direct = ContextResponse.success(payload=({"text": "direct"},))
        assert direct.payload[0]["text"] == "direct"
        ref = ContextResponse.success(payload=(), reference="opaque://ref")
        assert ref.reference == "opaque://ref"
        tool_resp = ToolResponse.success(payload={"a": 1})
        assert tool_resp.payload == {"a": 1}

        # different private metadata coexist already proven
        assert ctx._vector_index == "idx-private-001"
        assert tool._shell_executable == "/bin/bash"

        # no private in canonical
        assert "vector_index" not in str(req_c.to_dict())
        assert "mcp_server" not in str(req_t.to_dict())

        # no common base/request/result — proven via class identity
        assert ContextRequest is not ToolRequest

        # no registry/routing/yaml — existence checks
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()

        # no ProviderId
        assert "ProviderId" not in PROVIDERS_CONTEXT.read_text(encoding="utf-8")

        # no executor lifecycle
        assert "canonical_task_id" not in PROVIDERS_CONTEXT.read_text(encoding="utf-8")

        # no ACF fields
        assert "top_k" not in PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower()

        # no Reader/MCP transport in canonical
        assert "mcp_server" not in {f.name for f in dataclasses.fields(ToolRequest)}

        # no S5 governance
        assert "provenance" not in PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower()
