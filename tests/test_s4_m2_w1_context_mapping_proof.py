"""S4/M2/W1 — Minimal Context Provider Mapping Proof.

Behavioral mapping proof for:

    ContextRequest  →  ContextProvider.fetch  →  ContextResponse

Tests canonical semantic translation through a test-local fake provider,
not merely protocol imports or isinstance checks.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "aota_forge" / "core" / "providers"
PROVIDERS_CONTEXT = PROVIDERS_DIR / "context.py"
PROVIDERS_INIT = PROVIDERS_DIR / "__init__.py"

# ---------------------------------------------------------------------------
# Test-local fake — belongs inside this test file only
# ---------------------------------------------------------------------------

from aota_forge.core.providers.context import ContextRequest, ContextResponse
from aota_forge.core.contracts.errors import (
    ForgeError,
    InputSizeError,
    InputTypeError,
)
from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult


class FakeContextProvider:
    """Test-local fake implementing ContextProvider.

    Private backend metadata remains implementation-local and must not leak
    into canonical request/response seams.
    """

    def __init__(self) -> None:
        # private backend metadata — illustrative only
        self._database_url = "postgres://secret-internal"
        self._vector_index = "idx-private-001"
        self._reranker_model = "reranker-v2-private"
        self.invocation_count: int = 0
        self.received_requests: list[ContextRequest] = []

    def fetch(self, request: ContextRequest) -> ContextResponse:
        self.invocation_count += 1
        self.received_requests.append(request)
        # default: direct payload success unless manipulated by caller
        return ContextResponse.success(payload=({"text": "hello"},), reference=None)


def _authorized_fetch(
    provider: FakeContextProvider,
    decision: AuthorityResult,
    request: ContextRequest,
) -> ContextResponse | None:
    """Gate provider call behind authority decision.

    No provider call before authority decision — proves authority-before-call.
    """
    if decision.decision != AuthorityDecision.ALLOW:
        return None
    return provider.fetch(request)


def _parse_ctx() -> ast.Module:
    return ast.parse(PROVIDERS_CONTEXT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Canonical request preservation
# ---------------------------------------------------------------------------


class TestCanonicalRequestPreserved:
    def test_canonical_request_preserved_across_seam(self):
        provider = FakeContextProvider()
        req = ContextRequest(
            subject_ref="subject:abc-123",
            scope="scope:general",
            query="what is the policy for X",
            limit=25,
            cursor="cursor-opaque-xyz",
            capability_ref="cap:search.v1",
            correlation_id="corr-001",
        )
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert resp is not None
        assert provider.invocation_count == 1
        received = provider.received_requests[0]
        # verify same semantic values — not just serialization text
        assert received.subject_ref == "subject:abc-123"
        assert received.scope == "scope:general"
        assert received.query == "what is the policy for X"
        assert received.limit == 25
        assert received.cursor == "cursor-opaque-xyz"
        assert received.capability_ref == "cap:search.v1"
        assert received.correlation_id == "corr-001"
        # object identity preserved semantically
        assert received.to_dict() == req.to_dict()

    def test_request_without_optional_fields(self):
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        assert req.limit is None
        assert req.cursor is None
        assert req.capability_ref is None
        assert req.correlation_id is None
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert provider.invocation_count == 1
        received = provider.received_requests[0]
        assert received.limit is None
        assert received.cursor is None

    def test_optional_fields_bounded_and_preserved(self):
        provider = FakeContextProvider()
        req = ContextRequest(
            subject_ref="subj",
            scope="scope",
            query="q2",
            limit=1,
            cursor="c",
            capability_ref="cap2",
            correlation_id="corr2",
        )
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        _authorized_fetch(provider, allow, req)
        r = provider.received_requests[0]
        assert r.subject_ref == "subj"
        assert r.scope == "scope"
        assert r.query == "q2"
        assert r.limit == 1
        assert r.cursor == "c"
        assert r.capability_ref == "cap2"
        assert r.correlation_id == "corr2"


# ---------------------------------------------------------------------------
# 2. Bounded retrieval proof
# ---------------------------------------------------------------------------


class TestBoundedRetrievalPreserved:
    def test_valid_limit_succeeds_and_reaches_provider_once(self):
        provider = FakeContextProvider()
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        for limit in (1, 50, 100):
            provider.invocation_count = 0
            provider.received_requests.clear()
            req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=limit)
            resp = _authorized_fetch(provider, allow, req)
            assert resp is not None
            assert provider.invocation_count == 1
            assert provider.received_requests[0].limit == limit

    def test_invalid_limit_fails_before_provider_call(self):
        provider = FakeContextProvider()
        # Each invalid construction must not result in provider call
        invalid_limits = [0, -1, 101, 9999, 1.5, "10", True]
        for bad_limit in invalid_limits:
            provider.invocation_count = 0
            provider.received_requests.clear()
            with pytest.raises((InputSizeError, InputTypeError, ForgeError, ValueError, TypeError)):
                req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=bad_limit)  # type: ignore[arg-type]
                # if construction unexpectedly succeeded, ensure gated path would still not be called
                allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
                _authorized_fetch(provider, allow, req)
            assert provider.invocation_count == 0, f"invalid limit {bad_limit!r} must not reach provider"

    def test_oversized_bounded_string_fails_closed(self):
        provider = FakeContextProvider()
        long_str = "x" * 5000
        # cursor exceeds bound
        with pytest.raises((InputSizeError, ForgeError, ValueError)):
            ContextRequest(subject_ref="s", scope="sc", query="q", cursor=long_str)
        assert provider.invocation_count == 0
        # capability_ref exceeds bound
        with pytest.raises((InputSizeError, ForgeError, ValueError)):
            ContextRequest(subject_ref="s", scope="sc", query="q", capability_ref=long_str)
        assert provider.invocation_count == 0
        # correlation_id exceeds bound
        with pytest.raises((InputSizeError, ForgeError, ValueError)):
            ContextRequest(subject_ref="s", scope="sc", query="q", correlation_id=long_str)
        assert provider.invocation_count == 0
        # subject_ref exceeds bound
        with pytest.raises((InputSizeError, ForgeError, ValueError)):
            ContextRequest(subject_ref=long_str, scope="sc", query="q")
        assert provider.invocation_count == 0

    def test_invalid_context_request_provider_call_count_zero(self):
        """Global check: multiple invalid constructions leave provider untouched."""
        provider = FakeContextProvider()
        # attempt several invalid requests
        invalid_cases = [
            dict(subject_ref="", scope="sc", query="q"),
            dict(subject_ref="s", scope="", query="q"),
            dict(subject_ref="s", scope="sc", query=""),
            dict(subject_ref="s", scope="sc", query="q", limit=0),
            dict(subject_ref="s", scope="sc", query="q", limit=200),
        ]
        for kwargs in invalid_cases:
            with pytest.raises((InputSizeError, InputTypeError, ForgeError, ValueError, TypeError)):
                ContextRequest(**kwargs)  # type: ignore[arg-type]
        assert provider.invocation_count == 0


# ---------------------------------------------------------------------------
# 3. Authority before call
# ---------------------------------------------------------------------------


class TestAuthorityBeforeCall:
    def test_deny_zero_call(self):
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=10)
        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        result = _authorized_fetch(provider, deny, req)
        assert result is None
        assert provider.invocation_count == 0
        assert len(provider.received_requests) == 0

    def test_allow_single_call(self):
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=10)
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        result = _authorized_fetch(provider, allow, req)
        assert result is not None
        assert provider.invocation_count == 1
        assert len(provider.received_requests) == 1

    def test_blocked_also_zero_call(self):
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        blocked = AuthorityResult(decision=AuthorityDecision.BLOCKED, reason_code=AuthorityReason.TARGET_REQUIRED)
        result = _authorized_fetch(provider, blocked, req)
        assert result is None
        assert provider.invocation_count == 0

    def test_no_provider_call_before_authority_decision(self):
        # prove ordering: constructing request alone does not call provider
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        assert provider.invocation_count == 0
        # only after explicit allow decision does invocation happen
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        _authorized_fetch(provider, allow, req)
        assert provider.invocation_count == 1


# ---------------------------------------------------------------------------
# 4. Direct payload proof
# ---------------------------------------------------------------------------


class TestDirectPayloadProven:
    def test_direct_payload_success(self):
        provider = FakeContextProvider()
        # override fetch to return direct payload
        def direct_fetch(request: ContextRequest) -> ContextResponse:
            provider.invocation_count += 1
            provider.received_requests.append(request)
            return ContextResponse.success(payload=({"text": "direct-payload-1"}, {"text": "direct-payload-2"}))

        # monkey patch
        provider.fetch = direct_fetch  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert resp is not None
        assert resp.ok is True
        assert resp.error is None
        assert len(resp.payload) == 2
        assert resp.payload[0]["text"] == "direct-payload-1"
        assert resp.payload[1]["text"] == "direct-payload-2"
        assert resp.reference is None
        # no executor lifecycle identity in payload
        for item in resp.payload:
            for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id", "executor_id"):
                assert bad not in item

    def test_direct_payload_empty_is_still_success(self):
        resp = ContextResponse.success(payload=())
        assert resp.ok is True
        assert resp.payload == ()
        assert resp.error is None


# ---------------------------------------------------------------------------
# 5. Opaque reference proof
# ---------------------------------------------------------------------------


class TestOpaqueReferenceProven:
    def test_opaque_reference_success(self):
        provider = FakeContextProvider()

        def ref_fetch(request: ContextRequest) -> ContextResponse:
            provider.invocation_count += 1
            provider.received_requests.append(request)
            return ContextResponse.success(payload=(), reference="opaque://ref-123")

        provider.fetch = ref_fetch  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert resp is not None
        assert resp.ok is True
        assert resp.reference == "opaque://ref-123"
        assert resp.payload == ()
        # reference remains opaque — not interpreted as manifest etc.
        assert "artifact" not in resp.reference.lower()
        assert "provenance" not in resp.reference.lower()

    def test_opaque_reference_not_interpreted(self):
        # The reference is stored as a plain string; the mapping does not
        # expand it into S5 governance objects.
        resp = ContextResponse.success(payload=(), reference="ref-opaque-xyz")
        assert resp.reference == "ref-opaque-xyz"
        # response fields are strictly ok/payload/reference/error
        fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for s5_field in ("provenance", "artifact_graph", "evidence_graph", "verification", "retention"):
            assert s5_field not in fields


# ---------------------------------------------------------------------------
# 6. Typed failure
# ---------------------------------------------------------------------------


class TestTypedFailureProven:
    def test_typed_failure_via_forge_error(self):
        provider = FakeContextProvider()

        def failing_fetch(request: ContextRequest) -> ContextResponse:
            provider.invocation_count += 1
            provider.received_requests.append(request)
            err = InputSizeError("context store timeout")
            # also test generic ForgeError projection
            return ContextResponse.failure(err)

        provider.fetch = failing_fetch  # type: ignore[method-assign]
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert resp is not None
        assert resp.ok is False
        assert resp.payload == ()
        assert resp.reference is None
        assert resp.error is not None
        assert resp.error["code"] == "INPUT_SIZE_EXCEEDED"
        assert "message" in resp.error
        assert "retryable" in resp.error
        assert isinstance(resp.error["retryable"], bool)

    def test_no_new_canonical_error_code(self):
        # Provider module must not define new error class/code
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        assert "CONTEXT_" not in src
        tree = _parse_ctx()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Error" not in node.name, f"context provider must not define {node.name}"
        # Use existing ForgeError codes only
        err = ForgeError("INPUT_TYPE_INVALID", "bad", retryable=False)
        resp = ContextResponse.failure(err)
        assert resp.error["code"] == "INPUT_TYPE_INVALID"

    def test_failure_projection_preserves_code_message_retryable(self):
        err = InputTypeError("bad type", retryable=False)
        resp = ContextResponse.failure(err)
        assert resp.error["code"] == "INPUT_TYPE_INVALID"
        assert "message" in resp.error
        assert resp.error["retryable"] is False


# ---------------------------------------------------------------------------
# 7. Private metadata isolation
# ---------------------------------------------------------------------------


class TestPrivateMetadataIsolated:
    def test_private_backend_metadata_isolated(self):
        provider = FakeContextProvider()
        req = ContextRequest(subject_ref="s", scope="sc", query="q", limit=5)
        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        resp = _authorized_fetch(provider, allow, req)
        assert resp is not None
        # private fields exist on fake
        assert provider._database_url == "postgres://secret-internal"
        assert provider._vector_index == "idx-private-001"
        assert provider._reranker_model == "reranker-v2-private"
        # they do NOT appear in request serialization
        req_dict = req.to_dict()
        req_str = str(req_dict).lower()
        for private in ("database_url", "vector_index", "reranker_model", "postgres"):
            assert private not in req_str
        # nor in response payload/reference
        resp_str = str(resp.payload).lower() + str(resp.reference or "").lower()
        for private in ("database_url", "vector_index", "reranker_model", "postgres"):
            assert private not in resp_str
        # private not in canonical fields
        fields = {f.name for f in dataclasses.fields(ContextRequest)}
        for private in ("database_url", "vector_index", "reranker_model"):
            assert private not in fields
        resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for private in ("database_url", "vector_index", "reranker_model"):
            assert private not in resp_fields

    def test_private_not_in_serialization_unless_explicit_payload(self):
        provider = FakeContextProvider()
        # explicitly ensure provider could return payload containing user data,
        # but private metadata is not injected there
        req = ContextRequest(subject_ref="s", scope="sc", query="q")
        resp = provider.fetch(req)
        assert provider._database_url not in str(resp.payload)
        assert provider._database_url not in str(req.to_dict())


# ---------------------------------------------------------------------------
# 8. Provider identity boundary + execution neutrality + governance guards
# ---------------------------------------------------------------------------


class TestProviderIdentityBoundary:
    def test_no_first_class_provider_identity(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        for forbidden in ("ProviderId", "ProviderIdentity", "ProviderMetadata", "UniversalMetadata"):
            assert forbidden not in src
        tree = _parse_ctx()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "ProviderId" not in node.name
                assert "ProviderIdentity" not in node.name
        # fake's python name is not canonical identity
        provider = FakeContextProvider()
        assert provider.__class__.__name__ == "FakeContextProvider"
        # capability_ref is not provider identity
        req = ContextRequest(subject_ref="s", scope="sc", query="q", capability_ref="cap:v1")
        assert req.capability_ref == "cap:v1"
        assert "ProviderId" not in req.to_dict().keys()

    def test_no_provider_registry_or_routing_dependency(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        assert "ProviderRegistry" not in src
        assert "ProviderRouter" not in src
        assert "ProviderRouting" not in src
        assert not (PROVIDERS_DIR / "registry.py").exists()
        assert not (PROVIDERS_DIR / "routing.py").exists()
        assert not (PROVIDERS_DIR / "base.py").exists()
        assert not (PROVIDERS_DIR / "types.py").exists()
        # init also clean
        init_src = PROVIDERS_INIT.read_text(encoding="utf-8")
        assert "ProviderRegistry" not in init_src
        assert "ProviderId" not in init_src


class TestExecutionNeutrality:
    def test_no_execution_lifecycle_dependency(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        # lifecycle identifiers must not be canonical fields
        for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id"):
            assert bad not in src
        # verify via AST that no execution imports or inheritance exists
        # docstring may mention "Not an ExecutorAdapter" for separation intent
        tree = _parse_ctx()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "execution" not in mod, f"must not import from execution: {mod}"
                for alias in node.names:
                    assert alias.name not in ("ExecutorAdapter", "ExecutionPackage", "ExecutionDispatcher")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "ExecutorAdapter" not in alias.name
                    assert "ExecutionPackage" not in alias.name
            if isinstance(node, ast.ClassDef) and node.name == "ContextProvider":
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        assert base.id not in ("ExecutorAdapter", "BaseProvider")
        # request/response have no lifecycle fields
        req_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for bad in ("canonical_task_id", "adapter_handle", "dispatch_attempt_id", "task", "executor"):
            assert bad not in req_fields
            assert bad not in resp_fields
        # fake provider is not an ExecutorAdapter
        from aota_forge.core.execution.adapter import ExecutorAdapter

        assert not issubclass(FakeContextProvider, ExecutorAdapter)


class TestAcfAndS5Guards:
    def test_no_acf_specific_canonical_fields(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower()
        for kw in ("top_k", "embedding", "reranker", "hot/warm", "knowledge card", "memory promotion", "archive policy"):
            assert kw not in src
        req_fields = {f.name for f in dataclasses.fields(ContextRequest)}
        for bad in ("top_k", "embedding_model", "similarity_threshold", "vector_index", "hot_budget"):
            assert bad not in req_fields

    def test_s5_not_preempted(self):
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8").lower()
        for field in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification", "retention", "receipt"):
            assert field not in src
        resp_fields = {f.name for f in dataclasses.fields(ContextResponse)}
        for field in ("provenance", "completeness", "artifact_graph", "evidence_graph", "verification", "retention", "receipt"):
            assert field not in resp_fields

    def test_no_full_acf_integration_required(self):
        # Mapping proof does not import ACF
        src = PROVIDERS_CONTEXT.read_text(encoding="utf-8")
        assert "acf" not in src.lower() or "acf" in src.lower() and False  # strictly no ACF import
        # we assert by ensuring no acf import appears
        tree = _parse_ctx()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "acf" not in mod
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "acf" not in alias.name.lower()

    def test_protocol_imports_coexist_without_provider_framework(self):
        # Importing both providers does not pull universal framework
        from aota_forge.core.providers.context import ContextProvider as CP
        from aota_forge.core.providers.tool import ToolProvider as TP

        assert CP is not TP
        # No common base
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        assert "BaseProvider" not in src
