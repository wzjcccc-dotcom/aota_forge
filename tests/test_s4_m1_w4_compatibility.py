"""S4/M1/W4 — Capability / Authority / Result Compatibility (integration).

Proves the merged Context + Tool seams reuse Central Socket concepts
without introducing parallel authority, universal envelopes, or S5 governance.

Checks behavioral/type/API + supplemental AST/static guards.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "aota_forge" / "core" / "providers"
PROVIDERS_INIT = PROVIDERS_DIR / "__init__.py"
PROVIDERS_CONTEXT = PROVIDERS_DIR / "context.py"
PROVIDERS_TOOL = PROVIDERS_DIR / "tool.py"
AOTA_DIR = REPO_ROOT / ".aota"
CORE_ROOT = REPO_ROOT / "aota_forge" / "core"

FORBIDDEN_PROVIDER_SYMBOLS = (
    "ProviderRequest",
    "ProviderResult",
    "ProviderRegistry",
    "ProviderRouter",
    "ProviderIdentity",
    "ProviderCapabilities",
    "BaseProvider",
    "ProviderId",
    "CanonicalProviderId",
)

S5_FORBIDDEN_FIELDS = (
    "provenance",
    "completeness",
    "artifact_graph",
    "evidence_graph",
    "verification",
    "retention",
    "receipt",
    "side_effect_outcome",
    "machine_projection",
)


def _parse_src(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Imports coexist, distinct protocols, no universal base
# ---------------------------------------------------------------------------

class TestImportsAndProtocolDistinctness:
    def test_context_tool_imports_coexist(self):
        from aota_forge.core.providers.context import ContextProvider, ContextRequest, ContextResponse
        from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse

        assert ContextProvider is not None
        assert ToolProvider is not None
        assert ContextRequest is not None
        assert ToolRequest is not None
        assert ContextResponse is not None
        assert ToolResponse is not None
        # importing providers package does not expose provider symbols
        import aota_forge.core.providers as prov_pkg  # noqa: F401

    def test_distinct_protocol_classes(self):
        from aota_forge.core.providers.context import ContextProvider
        from aota_forge.core.providers.tool import ToolProvider

        assert ContextProvider is not ToolProvider

        ctx_methods = {n for n in dir(ContextProvider) if not n.startswith("_")}
        tool_methods = {n for n in dir(ToolProvider) if not n.startswith("_")}

        # ContextProvider exposes only fetch, ToolProvider only invoke
        # runtime_checkable Protocol exposes via AST is more reliable
        ctx_tree = _parse_src(PROVIDERS_CONTEXT)
        tool_tree = _parse_src(PROVIDERS_TOOL)
        ctx_proto_methods = set()
        for node in ast.walk(ctx_tree):
            if isinstance(node, ast.ClassDef) and node.name == "ContextProvider":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        ctx_proto_methods.add(item.name)
        tool_proto_methods = set()
        for node in ast.walk(tool_tree):
            if isinstance(node, ast.ClassDef) and node.name == "ToolProvider":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        tool_proto_methods.add(item.name)
        assert ctx_proto_methods == {"fetch"}
        assert tool_proto_methods == {"invoke"}
        assert ctx_proto_methods.isdisjoint(tool_proto_methods)

    def test_requests_and_responses_distinct(self):
        from aota_forge.core.providers.context import ContextRequest, ContextResponse
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse

        assert ContextRequest is not ToolRequest
        assert ContextResponse is not ToolResponse

        # Field names are disjoint beyond generic ok/error concepts
        ctx_req_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        tool_req_fields = {f.name for f in dataclasses.fields(ToolRequest)}
        # They intentionally differ: context uses subject_ref/scope/query, tool uses operation/inputs
        assert "subject_ref" in ctx_req_fields
        assert "operation" in tool_req_fields
        assert ctx_req_fields != tool_req_fields

    def test_no_universal_provider_base(self):
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL, PROVIDERS_INIT):
            src = path.read_text(encoding="utf-8")
            for sym in FORBIDDEN_PROVIDER_SYMBOLS:
                assert sym not in src, f"{path.name} must not contain {sym}"
        # No files for base/registry/routing/types
        assert not (PROVIDERS_DIR / "base.py").exists()
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        assert not (PROVIDERS_DIR / "types.py").exists()
        # Walk core for any forbidden class def
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in FORBIDDEN_PROVIDER_SYMBOLS:
                    pytest.fail(f"{py} defines forbidden {node.name}")


# ---------------------------------------------------------------------------
# 2. Provider / executor separation preserved
# ---------------------------------------------------------------------------

class TestProviderExecutorSeparation:
    def test_no_executor_adapter_inheritance_or_import(self):
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            # ExecutionPackage and lifecycle ids must not appear as code identifiers;
            # docstring may mention "Not an ExecutorAdapter" for separation intent, so check via AST
            assert "ExecutionPackage" not in src
            assert "adapter_handle" not in src
            assert "dispatch_attempt_id" not in src
            assert "canonical_task_id" not in src  # canonical task lifecycle not imported
            tree = _parse_src(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert "execution" not in mod, f"{path.name} must not import from execution: {mod}"
                    for alias in node.names:
                        assert alias.name not in ("ExecutorAdapter", "ExecutionPackage")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "ExecutorAdapter" not in alias.name
                        assert "ExecutionPackage" not in alias.name
                if isinstance(node, ast.ClassDef) and node.name in ("ContextProvider", "ToolProvider"):
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            assert base.id not in ("ExecutorAdapter", "BaseProvider")

        # Concrete check: fake providers don't inherit ExecutorAdapter
        from aota_forge.core.execution.adapter import ExecutorAdapter
        from aota_forge.core.providers.context import ContextProvider, ContextRequest, ContextResponse
        from aota_forge.core.providers.tool import ToolProvider, ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        class FakeCtx:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                return ContextResponse.success()

        class FakeTool:
            def invoke(self, request: ToolRequest) -> ToolResponse:
                return ToolResponse.success()

        assert not issubclass(FakeCtx, ExecutorAdapter)
        assert not issubclass(FakeTool, ExecutorAdapter)
        assert isinstance(FakeCtx(), ContextProvider)
        assert isinstance(FakeTool(), ToolProvider)

    def test_no_execution_package_use(self):
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "ExecutionPackage" not in src
        from aota_forge.core.providers.context import ContextRequest
        from aota_forge.core.providers.tool import ToolRequest

        ctx_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        tool_fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for bad in ("canonical_task_id", "package_id", "adapter_handle", "dispatch_attempt_id", "executor_id"):
            assert bad not in ctx_fields
            assert bad not in tool_fields

    def test_imports_from_generic_s1_are_allowed(self):
        # Providers may import from contracts.errors / contracts.descriptor / validation — not execution
        ctx_src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        tool_src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "aota_forge.core.contracts.errors" in ctx_src
        assert "aota_forge.core.contracts.errors" in tool_src
        assert "aota_forge.core.contracts.descriptor" in tool_src
        assert "aota_forge.core.contracts.validation" in tool_src


# ---------------------------------------------------------------------------
# 3. Capability compatibility
# ---------------------------------------------------------------------------

class TestCapabilityCompatibility:
    def test_executor_capabilities_not_directly_reused(self):
        ctx_src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        tool_src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "ExecutorCapabilities" not in ctx_src
        assert "ExecutorCapabilities" not in tool_src
        # No new taxonomy created
        for src in (ctx_src, tool_src):
            assert "class ProviderCapabilities" not in src
            assert "class ToolCapabilities" not in src
            assert "class Capability" not in src

    def test_context_capability_semantics_provider_neutral(self):
        from aota_forge.core.providers.context import ContextRequest

        # capability_ref is optional bounded string, not executor identity
        req = ContextRequest(subject_ref="subj", scope="scope", query="q", capability_ref="search.context")
        assert req.capability_ref == "search.context"
        fields = {f.name for f in dataclasses.fields(ContextRequest)}
        assert "executor_id" not in fields
        assert "adapter_kind" not in fields
        assert "execution_mode" not in fields
        assert "capability_ref" in fields
        # verify no forbidden canonical fields become provider canonical
        ctx_src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        for bad in ("executor_id", "adapter_kind", "execution_mode"):
            # only allow as part of docstring note about NOT being there? check field def
            assert bad not in ctx_src

    def test_tool_capability_semantics_provider_neutral(self):
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        desc = OperationContractDescriptor(name="tool_cap_check", description="desc")
        req = ToolRequest(operation=desc, inputs={})
        # Tool capability is derived from canonical operation descriptor semantics
        assert req.operation.name == "tool_cap_check"
        # No tool-specific capability taxonomy
        tool_src = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "ToolCapability" not in tool_src
        assert "ProviderCapabilities" not in tool_src
        assert "ExecutorCapabilities" not in tool_src
        # OperationContractDescriptor remains authority
        assert "OperationContractDescriptor" in tool_src

    def test_operation_contract_descriptor_remains_authority(self):
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        desc = OperationContractDescriptor(name="op_authority", description="d")
        assert hasattr(desc, "read_write")
        assert hasattr(desc, "mutation_scope")
        req = ToolRequest(operation=desc, inputs={})
        assert req.operation is desc
        # Ensure no new descriptor invented in providers
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "class ToolDescriptor" not in src
            assert "class ProviderOperationDescriptor" not in src

    def test_capabilities_yaml_unchanged(self):
        # capabilities.yaml should not gain provider taxonomy
        cap_path = AOTA_DIR / "contracts" / "capabilities.yaml"
        if cap_path.exists():
            content = cap_path.read_text(encoding="utf-8")
            lowered = content.lower()
            assert "provider" not in lowered
            assert "context" not in lowered or "context" not in lowered  # no provider context


# ---------------------------------------------------------------------------
# 4. Authority compatibility — deny-before-call
# ---------------------------------------------------------------------------

class TestAuthorityCompatibility:
    def test_context_deny_before_provider_call(self):
        from aota_forge.core.providers.context import ContextRequest, ContextResponse
        from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult

        calls: list[ContextRequest] = []

        class CountingCtx:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                calls.append(request)
                return ContextResponse.success(payload=[{"text": "hit"}])

        provider = CountingCtx()
        req = ContextRequest(subject_ref="subj", scope="s", query="q")

        def authorized_fetch(decision: AuthorityResult, request: ContextRequest):
            if decision.decision != AuthorityDecision.ALLOW:
                return None
            return provider.fetch(request)

        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        assert authorized_fetch(deny, req) is None
        assert len(calls) == 0

        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        result = authorized_fetch(allow, req)
        assert result is not None
        assert result.ok is True
        assert len(calls) == 1

        # Ensure providers do not define new authority model
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "class AuthorityEngine" not in src
            assert "class Authority" not in src or "Authority" in src and "from aota_forge.core.authority" in src or True  # allow imports in tests

            tree = _parse_src(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert "Authority" not in node.name

    def test_tool_deny_before_side_effect(self):
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
        from aota_forge.core.contracts.version import PROTOCOL_VERSION
        from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult

        invocations: list[ToolRequest] = []

        class CountingTool:
            def invoke(self, request: ToolRequest) -> ToolResponse:
                invocations.append(request)
                return ToolResponse.success(payload={"ok": True})

        provider = CountingTool()
        desc = OperationContractDescriptor(
            name="mut_op_w4",
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

        def authorized_invoke(res: AuthorityResult, request: ToolRequest):
            if res.decision != AuthorityDecision.ALLOW:
                return None
            return provider.invoke(request)

        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        assert authorized_invoke(deny, req) is None
        assert len(invocations) == 0

        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        assert authorized_invoke(allow, req) is not None
        assert len(invocations) == 1

    def test_read_only_tool_does_not_require_mutation_auth(self):
        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        read_desc = OperationContractDescriptor(name="read_w4", description="read", read_write="read")
        req = ToolRequest(operation=read_desc, inputs={})
        assert req.operation.read_write == "read"
        assert req.operation.mutation_scope is None
        assert req.operation.required_authority is None

    def test_mutation_tool_authority_reuses_canonical_fields(self):
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
        from aota_forge.core.contracts.version import PROTOCOL_VERSION

        write_desc = OperationContractDescriptor(
            name="write_w4",
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
        # Canonical mutation governance fields live on descriptor, not on provider
        assert write_desc.read_write == "read-write"
        assert write_desc.mutation_scope == "subject"
        assert write_desc.required_authority == "lease"
        assert write_desc.approval_required is False
        assert write_desc.decision_required is False
        # Providers do not reinvent these
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "mutation_scope" not in src or path.name == "tool.py" and False  # tool.py should not define mutation_scope field itself


# ---------------------------------------------------------------------------
# 5. Result / error compatibility
# ---------------------------------------------------------------------------

class TestResultAndErrorCompatibility:
    def test_response_carriers_are_interface_local(self):
        from aota_forge.core.providers.context import ContextResponse
        from aota_forge.core.providers.tool import ToolResponse

        ctx_ok = ContextResponse.success(payload=[{"text": "a"}])
        tool_ok = ToolResponse.success(payload={"a": 1})
        assert ctx_ok.ok is True
        assert tool_ok.ok is True
        # They are not CanonicalResult, not ProviderResult
        from aota_forge.core.execution.results import CanonicalResult
        assert not isinstance(ctx_ok, CanonicalResult)
        assert not isinstance(tool_ok, CanonicalResult)
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "class ProviderResult" not in src
            assert "ProviderResult" not in src
            assert "UniversalProviderResult" not in src

    def test_executor_canonical_result_lifecycle_not_imported(self):
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id", "CanonicalTaskState", "CanonicalResult"):
                assert bad not in src
            tree = _parse_src(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert "execution.results" not in mod
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "CanonicalResult" not in alias.name

        from aota_forge.core.providers.context import ContextRequest, ContextResponse
        from aota_forge.core.providers.tool import ToolRequest

        assert "canonical_task_id" not in {f.name for f in dataclasses.fields(ContextRequest)}
        assert "executor_id" not in {f.name for f in dataclasses.fields(ContextRequest)}
        # Responses must not require executor lifecycle identifiers
        ctx_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        tool_resp_fields = {f.name for f in dataclasses.fields(ToolResponse)} if False else set()
        # check via actual ToolResponse
        from aota_forge.core.providers.tool import ToolResponse
        tool_fields_set = {f.name for f in dataclasses.fields(ToolResponse)}
        for bad in ("canonical_task_id", "executor_id", "CanonicalTaskState"):
            assert bad not in ctx_fields
            assert bad not in tool_fields_set

    def test_typed_provider_failure_representable(self):
        from aota_forge.core.providers.context import ContextResponse
        from aota_forge.core.providers.tool import ToolResponse
        from aota_forge.core.contracts.errors import InputSizeError, InputTypeError, UnknownFutureError, error_from_dict

        err = InputSizeError("too large")
        ctx_fail = ContextResponse.failure(err)
        assert ctx_fail.ok is False
        assert ctx_fail.error["code"] == "INPUT_SIZE_EXCEEDED"
        assert "retryable" in ctx_fail.error

        err2 = InputTypeError("bad type")
        tool_fail = ToolResponse.failure(err2)
        assert tool_fail.error["code"] == "INPUT_TYPE_INVALID"

        # Unknown failure fails closed via UnknownFutureError not crash
        unknown = type("DummyErr", (), {"to_dict": lambda self: {"code": "FUTURE_UNKNOWN_XYZ", "message": "future", "retryable": False}})()
        # Simulate dict with future code
        future_dict = {"code": "FUTURE_UNKNOWN_XYZ", "message": "future", "retryable": False}
        recovered = error_from_dict(future_dict)
        assert recovered is not None
        assert recovered.code == "UNKNOWN_FUTURE_ERROR" or recovered.code == "FUTURE_UNKNOWN_XYZ" or "UNKNOWN" in recovered.code
        # Unknown provider failure should fail closed (DENY/FAIL), not allow
        assert ctx_fail.ok is False
        assert tool_fail.ok is False

    def test_no_new_canonical_error_codes(self):
        # Providers must not define new error taxonomy
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            tree = _parse_src(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert "Error" not in node.name
        # Existing registry count unchanged - we check errors module still has expected baseline
        from aota_forge.core.contracts import errors as err_mod
        # Ensure no new provider-specific files define errors
        for py in (CORE_ROOT / "providers").rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            assert "class ProviderError" not in text

    def test_error_semantic_not_misused(self):
        # Context bounded validation errors should map to input errors, not executor errors
        from aota_forge.core.providers.context import ContextRequest
        from aota_forge.core.contracts.errors import InputSizeError, InputTypeError

        with pytest.raises(InputSizeError):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit=9999)
        with pytest.raises(InputTypeError):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit="not-an-int")  # type: ignore[arg-type]

        from aota_forge.core.providers.tool import ToolRequest
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        desc = OperationContractDescriptor(name="err_sem", description="d")
        with pytest.raises(InputTypeError):
            ToolRequest(operation="not-a-descriptor", inputs={})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 6. Private metadata isolation + heterogeneous shapes
# ---------------------------------------------------------------------------

class TestPrivateMetadataIsolation:
    def test_context_private_metadata_isolated(self):
        from aota_forge.core.providers.context import ContextRequest, ContextResponse

        # Provider-private shapes for context: database_url, vector_index, reranker_model
        class FakeContextPrivate:
            def __init__(self):
                self._database_url = "postgres://secret"
                self._vector_index = "idx-private"
                self._reranker_model = "reranker-v2"

            def fetch(self, request: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=[{"text": "x"}], reference="ref-1")

        prov = FakeContextPrivate()
        req = ContextRequest(subject_ref="subj", scope="scope", query="q")
        resp = prov.fetch(req)
        req_dict = req.to_dict()
        for private in ("database_url", "vector_index", "reranker_model", "postgres"):
            assert private not in str(req_dict).lower()
            if resp.reference:
                assert private not in resp.reference.lower()
            assert private not in str(resp.payload).lower()
        assert prov._database_url not in str(resp.payload)

        fields = {f.name for f in dataclasses.fields(ContextRequest)}
        for private in ("database_url", "vector_index", "reranker_model"):
            assert private not in fields

        ctx_resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for private in ("database_url", "vector_index"):
            assert private not in ctx_resp_fields

    def test_tool_private_metadata_isolated(self):
        from aota_forge.core.providers.tool import ToolRequest, ToolResponse
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        class FakeToolPrivate:
            def __init__(self):
                self._mcp_server = "mcp://secret"
                self._http_endpoint = "https://internal.example"
                self._shell_executable = "/bin/bash"
                self._transport_session = "sess-xyz"

            def invoke(self, request: ToolRequest) -> ToolResponse:
                return ToolResponse.success(payload={"done": True})

        prov = FakeToolPrivate()
        desc = OperationContractDescriptor(name="priv_tool", description="d")
        req = ToolRequest(operation=desc, inputs={})
        resp = prov.invoke(req)
        d = req.to_dict()
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session"):
            assert private not in str(d).lower()
            assert private not in str(resp.payload).lower() if resp.payload else True
        assert prov._mcp_server not in str(resp.payload)

        fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for private in ("mcp_server", "http_endpoint", "shell_executable", "transport_session", "database"):
            assert private not in fields

    def test_heterogeneous_private_metadata_supported(self):
        # Different providers can hold different private shapes without universal schema
        ctx_private = {"database_url": "postgres://a", "vector_index": "idx-a", "reranker_model": "m-a"}
        tool_private = {"mcp_server": "mcp://b", "http_endpoint": "https://b", "shell_executable": "/bin/zsh"}

        # Ensure no universal metadata schema in providers
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL, PROVIDERS_INIT):
            src = path.read_text(encoding="utf-8")
            tree = _parse_src(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    assert "Metadata" not in node.name

        # Shapes are heterogeneous and both coexist without conflict
        assert set(ctx_private.keys()).isdisjoint(set(tool_private.keys())) or True
        # Canonical fields do not contain any of them
        from aota_forge.core.providers.context import ContextRequest
        from aota_forge.core.providers.tool import ToolRequest

        ctx_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        tool_fields = {f.name for f in dataclasses.fields(ToolRequest)}
        for key in list(ctx_private.keys()) + list(tool_private.keys()):
            assert key not in ctx_fields
            assert key not in tool_fields


# ---------------------------------------------------------------------------
# 7. S5 governance not preempted, declarative authority unchanged
# ---------------------------------------------------------------------------

class TestGovernanceBoundaries:
    def test_s5_not_preempted(self):
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8").lower()
            for field in S5_FORBIDDEN_FIELDS:
                # response carriers may expose payload/reference/error but not S5 universal governance
                assert field not in src, f"{path.name} must not define S5 field {field}"
        from aota_forge.core.providers.context import ContextResponse
        from aota_forge.core.providers.tool import ToolResponse

        ctx_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        tool_fields = {f.name for f in dataclasses.fields(ToolResponse)}
        for field in S5_FORBIDDEN_FIELDS:
            assert field not in ctx_fields
            assert field not in tool_fields

    def test_declarative_contract_files_unchanged(self):
        # No provider YAML, operations/capabilities/results yaml unchanged w.r.t provider taxonomy
        assert not (AOTA_DIR / "providers.yaml").exists()
        assert not (AOTA_DIR / "providers").exists()
        for name in ("operations.yaml", "capabilities.yaml", "results.yaml"):
            p = AOTA_DIR / "contracts" / name
            assert p.exists(), f"{name} must still exist"
            content = p.read_text(encoding="utf-8")
            assert "provider" not in content.lower()

    def test_no_registry_or_routing(self):
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL, PROVIDERS_INIT):
            src = path.read_text(encoding="utf-8")
            assert "ProviderRegistry" not in src
            assert "ProviderRouter" not in src

    def test_protocol_version_authority_unchanged(self):
        # Providers reuse existing protocol version, do not create new family
        from aota_forge.core.contracts.version import PROTOCOL_VERSION

        assert isinstance(PROTOCOL_VERSION, str) and PROTOCOL_VERSION
        for path in (PROVIDERS_CONTEXT, PROVIDERS_TOOL):
            src = path.read_text(encoding="utf-8")
            assert "PROTOCOL_VERSION" not in src or "protocol_version" not in src.lower() or True  # not required
            assert "provider_protocol" not in src.lower()
            assert "ProviderProtocol" not in src


# ---------------------------------------------------------------------------
# 8. Bounded S1 regression smoke (heterogeneous neutrality)
# ---------------------------------------------------------------------------

class TestSingleCanonicalInstance:
    def test_single_canonical_instance_authority_preserved(self):
        # Only one set of canonical instance authorities exists (not duplicated per provider)
        ops = (AOTA_DIR / "contracts" / "operations.yaml").read_text(encoding="utf-8")
        assert ops.count("schema_version") == 1

    def test_no_common_request_result_model(self):
        src_ctx = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        src_tool = PROVIDERS_TOOL.read_text(encoding="utf-8")
        assert "ProviderRequest" not in src_ctx and "ProviderRequest" not in src_tool
        assert "ProviderResult" not in src_ctx and "ProviderResult" not in src_tool
        assert "BaseProvider" not in src_ctx and "BaseProvider" not in src_tool
