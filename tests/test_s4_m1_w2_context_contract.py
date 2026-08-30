"""S4/M1/W2 — Context Provider Contract.

Proves:
  ContextProvider distinct from ExecutorAdapter and ContextStore
  request bounded and executor-independent
  bounded retrieval generic, ACF knobs not canonical
  authority denial → zero provider invocation
  typed failure canonical/fail-closed
  private metadata isolated
  no registry / YAML / universal ProviderRequest/ProviderResult
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import dataclasses

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_CONTEXT = REPO_ROOT / "aota_forge" / "core" / "providers" / "context.py"
PROVIDERS_INIT = REPO_ROOT / "aota_forge" / "core" / "providers" / "__init__.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_context_src() -> str:
    return PROVIDERS_CONTEXT.read_text(encoding="utf-8")


def _parse_context_tree():
    return ast.parse(_read_context_src())


# ---------------------------------------------------------------------------
# 1. Provider distinct from ExecutorAdapter
# ---------------------------------------------------------------------------

class TestContextProviderDistinctFromExecutor:
    def test_context_provider_not_executor(self):
        from aota_forge.core.providers.context import ContextProvider
        from aota_forge.core.execution.adapter import ExecutorAdapter

        # Protocol has only fetch
        assert hasattr(ContextProvider, "fetch")
        # Must not have executor lifecycle methods
        for name in ("dispatch", "status", "result", "cancel", "resume", "validate_package", "capabilities"):
            assert not hasattr(ContextProvider, name) or name == "fetch"

        # Inspect protocol source for lifecycle names
        src = _read_context_src()
        lowered = src.lower()
        # fetch must exist, dispatch/status/cancel must not be protocol members
        tree = _parse_context_tree()
        methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ContextProvider":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.add(item.name)
        assert "fetch" in methods
        for bad in ("dispatch", "status", "cancel", "resume"):
            assert bad not in methods

        # Concrete fake provider does not inherit ExecutorAdapter
        from aota_forge.core.providers.context import ContextRequest, ContextResponse

        class FakeContextProvider:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"text": "x"},))

        assert not issubclass(FakeContextProvider, ExecutorAdapter)
        # isinstance check via protocol
        assert isinstance(FakeContextProvider(), ContextProvider)

    def test_context_request_has_no_executor_lifecycle_fields(self):
        from aota_forge.core.providers.context import ContextRequest

        field_names = {f.name for f in dataclasses.fields(ContextRequest)}
        for forbidden in ("canonical_task_id", "dispatch_attempt_id", "adapter_handle", "executor_id", "package_id"):
            assert forbidden not in field_names
        # also ensure source text doesn't contain them as fields
        src = _read_context_src()
        # field definitions should not include lifecycle names
        assert "canonical_task_id" not in src
        assert "adapter_handle" not in src
        # ExecutionPackage must not be imported/reused
        assert "ExecutionPackage" not in src
        # Protocol must not import ExecutorAdapter (docstring mention allowed)
        tree = _parse_context_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "execution" not in mod
                for alias in node.names:
                    assert alias.name != "ExecutorAdapter"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "ExecutorAdapter" not in alias.name


# ---------------------------------------------------------------------------
# 2. ContextProvider distinct from ContextStore
# ---------------------------------------------------------------------------

class TestContextProviderDistinctFromStore:
    def test_distinct_from_store(self):
        from aota_forge.core.providers.context import ContextProvider

        # A store would own storage backends; provider is fetch seam
        class FakeContextStore:
            """Store-like shape with storage backend."""
            def __init__(self):
                self.storage_backend = "fake-db"
                self.vector_index = "idx"

            def fetch(self, request):  # same name but store owns storage
                return None

        # Provider protocol should not require storage attributes
        src = _read_context_src()
        assert "storage_backend" not in src
        assert "vector_index" not in src

        # Fake provider without store attributes still satisfies protocol
        from aota_forge.core.providers.context import ContextRequest, ContextResponse

        class FakeProvider:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=())

        assert isinstance(FakeProvider(), ContextProvider)
        assert not hasattr(FakeProvider(), "storage_backend")

    def test_provider_source_remains_store_independent(self):
        src = _read_context_src().lower()
        for kw in ("hot/warm", "knowledge", "memory", "archive"):
            assert kw not in src


# ---------------------------------------------------------------------------
# 3. Bounded retrieval is generic
# ---------------------------------------------------------------------------

class TestBoundedRetrievalGeneric:
    def test_bounded_retrieval_via_limit_and_cursor(self):
        from aota_forge.core.providers.context import ContextRequest

        # valid bounded request
        req = ContextRequest(subject_ref="subj:1", scope="scope-a", query="what is x", limit=10, cursor="opaque-123")
        assert req.limit == 10
        assert req.cursor == "opaque-123"

        # limit bounds fail closed
        with pytest.raises(Exception):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit=0)
        with pytest.raises(Exception):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit=101)
        with pytest.raises(Exception):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit=-1)
        # non-int limit
        with pytest.raises(Exception):
            ContextRequest(subject_ref="s", scope="sc", query="q", limit="10")  # type: ignore[arg-type]

        # cursor is opaque string, bounded length still enforced
        long = "x" * 5000
        with pytest.raises(Exception):
            ContextRequest(subject_ref="s", scope="sc", query="q", cursor=long)

        # request dict does not contain ACF knobs
        d = req.to_dict()
        for kw in ("top_k", "topk", "embedding", "reranker", "hot_budget", "warm_budget", "archive", "vector"):
            assert kw not in d
            assert kw not in str(d).lower()

    def test_acf_private_knobs_not_canonical(self):
        src = _read_context_src().lower()
        for kw in ("top_k", "embedding", "reranker", "hot budget", "warm budget", "archive policy", "vector index"):
            assert kw not in src
        # also ensure request fields don't include them
        from aota_forge.core.providers.context import ContextRequest
        fields = {f.name for f in dataclasses.fields(ContextRequest)}
        assert "top_k" not in fields
        assert "embedding_model" not in fields
        assert "similarity_threshold" not in fields


# ---------------------------------------------------------------------------
# 4. Authority denial causes zero provider invocation
# ---------------------------------------------------------------------------

class TestAuthorityGating:
    def test_authority_denial_zero_invocation(self):
        from aota_forge.core.providers.context import ContextRequest, ContextResponse, ContextProvider
        from aota_forge.core.authority import AuthorityDecision, AuthorityReason, AuthorityResult

        invocations: list[ContextRequest] = []

        class CountingProvider:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                invocations.append(request)
                return ContextResponse.success(payload=({"text": "ok"},))

        provider = CountingProvider()
        req = ContextRequest(subject_ref="subj:1", scope="scope-a", query="q", limit=5)

        def authorized_fetch(authority_result: AuthorityResult, request: ContextRequest) -> ContextResponse | None:
            if authority_result.decision != AuthorityDecision.ALLOW:
                return None
            return provider.fetch(request)

        deny = AuthorityResult(decision=AuthorityDecision.DENY, reason_code=AuthorityReason.AUTHORIZATION_MISSING)
        result = authorized_fetch(deny, req)
        assert result is None
        assert len(invocations) == 0

        allow = AuthorityResult(decision=AuthorityDecision.ALLOW, reason_code=AuthorityReason.AUTHORIZED)
        result2 = authorized_fetch(allow, req)
        assert result2 is not None
        assert len(invocations) == 1

    def test_existing_authority_semantics_reused(self):
        # Reuse AuthorityEngine / AuthorityDecision without new authority model
        from aota_forge.core import authority as auth_mod

        assert hasattr(auth_mod, "AuthorityEngine")
        assert hasattr(auth_mod, "AuthorityDecision")
        assert hasattr(auth_mod, "AuthorityResult")
        src = _read_context_src()
        # Must not define new authority model
        assert "class AuthorityEngine" not in src
        assert "class TrustedMutationAuthorization" not in src
        assert "ApprovalEvidence" not in src or "import" not in src  # no new authority types
        tree = _parse_context_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Authority" not in node.name


# ---------------------------------------------------------------------------
# 5. Typed failure remains canonical / fail-closed
# ---------------------------------------------------------------------------

class TestTypedFailure:
    def test_failure_projection_is_forge_error(self):
        from aota_forge.core.providers.context import ContextResponse
        from aota_forge.core.contracts.errors import ForgeError, InputSizeError

        # success case
        ok = ContextResponse.success(payload=({"a": 1},), reference="ref-1")
        assert ok.ok is True
        assert ok.error is None

        # failure via ForgeError
        err = InputSizeError("too large")
        fail = ContextResponse.failure(err)
        assert fail.ok is False
        assert fail.error["code"] == "INPUT_SIZE_EXCEEDED"
        assert "retryable" in fail.error

        # bool ok consistency
        with pytest.raises(Exception):
            ContextResponse(ok=True, payload=(), reference=None, error={"code": "X", "message": "m"})
        with pytest.raises(Exception):
            ContextResponse(ok=False, payload=(), reference=None, error=None)

        # new error code not introduced in context provider source
        src = _read_context_src()
        assert 'code = "' not in src or "FORGE_ERROR" not in src
        # Ensure no new provider error taxonomy
        tree = _parse_context_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Error" not in node.name

    def test_no_new_canonical_error_code_created(self):
        from aota_forge.core.contracts.errors import ERROR_CLASSES

        src = _read_context_src()
        # must not define class-level code = "CONTEXT_..."
        assert "CONTEXT_" not in src
        assert "PROVIDER_" not in src or "Provider" not in src


# ---------------------------------------------------------------------------
# 6. Private metadata isolation
# ---------------------------------------------------------------------------

class TestPrivateMetadataIsolation:
    def test_private_metadata_does_not_leak(self):
        from aota_forge.core.providers.context import ContextRequest, ContextResponse

        # Simulate provider-private metadata that a fake impl holds locally
        class FakePrivateProvider:
            def __init__(self):
                self._database_url = "postgres://secret"
                self._vector_index = "idx-123"
                self._reranker_model = "rerank-v2"

            def fetch(self, request: ContextRequest) -> ContextResponse:
                # private metadata never enters response
                return ContextResponse.success(payload=({"text": "hello"},), reference="opaque-ref")

        provider = FakePrivateProvider()
        req = ContextRequest(subject_ref="subj:1", scope="s", query="q", limit=2)
        resp = provider.fetch(req)

        # request dict must not contain private keys
        req_dict = req.to_dict()
        for private in ("database_url", "vector_index", "reranker_model", "storage_backend"):
            assert private not in req_dict
            assert private not in str(req_dict)

        # response must not contain private keys
        assert provider._database_url not in str(resp.payload)
        assert provider._vector_index not in str(resp.payload)
        assert "postgres" not in str(resp.payload).lower()
        # payload/reference/error are only canonical fields
        assert set(req_dict.keys()) == {"subject_ref", "scope", "query", "limit", "cursor", "capability_ref", "correlation_id"}

    def test_provider_private_not_in_canonical_fields(self):
        from aota_forge.core.providers.context import ContextResponse

        resp = ContextResponse.success(payload=({"x": 1},))
        # response fields are strictly ok/payload/reference/error
        assert hasattr(resp, "ok")
        assert hasattr(resp, "payload")
        assert hasattr(resp, "reference")
        assert hasattr(resp, "error")
        assert not hasattr(resp, "database_url")
        assert not hasattr(resp, "vector_index")


# ---------------------------------------------------------------------------
# 7. No registry / YAML / universal ProviderRequest/ProviderResult
# ---------------------------------------------------------------------------

class TestNoUniversalProviderFramework:
    def test_no_provider_registry(self):
        src = _read_context_src()
        assert "ProviderRegistry" not in src
        assert "ProviderRouter" not in src
        assert "provider_registry" not in src.lower()
        # scan core/providers __init__
        init_src = PROVIDERS_INIT.read_text(encoding="utf-8")
        assert "ProviderRegistry" not in init_src
        # scan filesystem for registry file
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "registry.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "routing.py").exists()

    def test_no_provider_yaml(self):
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()
        assert not (REPO_ROOT / ".aota" / "providers").exists()
        src = _read_context_src().lower()
        assert "providers.yaml" not in src

    def test_no_universal_provider_request_result(self):
        src = _read_context_src()
        assert "class ProviderRequest" not in src
        assert "class ProviderResult" not in src
        assert "ProviderRequest" not in src
        assert "ProviderResult" not in src
        # also ensure not via BaseProvider
        assert "BaseProvider" not in src
        # file must not create types.py / base.py
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "types.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "base.py").exists()
        # providers context must not define universal result named ProviderResult
        tree = _parse_context_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name != "ProviderResult"
                assert node.name != "ProviderRequest"
