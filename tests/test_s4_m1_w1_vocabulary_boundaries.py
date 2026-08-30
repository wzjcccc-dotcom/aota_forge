"""S4/M1/W1 — Provider Vocabulary & Ownership Boundary.

Behavioral/structural contract tests that freeze:

    PROVIDER_IS_EXECUTOR=no
    TOOL_PROVIDER_IS_TOOL_CALL=no
    CONTEXT_PROVIDER_IS_CONTEXT_STORE=no
    CAPABILITY_IS_PROVIDER_IDENTITY=no
    PROVIDER_PRIVATE_RUNTIME_METADATA_NOT_CANONICAL=yes

and prove:

    FIRST_CLASS_PROVIDER_IDENTITY_REQUIRED=no
    SHARED_PROVIDER_BASE_PROTOCOL_REQUIRED=no
    COMMON_PROVIDER_REQUEST_MODEL_REQUIRED=no
    COMMON_PROVIDER_RESULT_MODEL_REQUIRED=no
    COMMON_PROVIDER_BASE_INTERFACE_REQUIRED=no
    NEW_PROVIDER_CAPABILITY_TYPE_REQUIRED=no

No ContextProvider / ToolProvider contract is implemented here;
W1 only guards the vocabulary boundary.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import importlib.util
import inspect
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROVIDERS_INIT = REPO_ROOT / "aota_forge" / "core" / "providers" / "__init__.py"
CORE_ROOT = REPO_ROOT / "aota_forge" / "core"


def _load_providers_via_spec():
    """Load providers package from the git-tracked path, bypassing outer shadowing."""
    spec = importlib.util.spec_from_file_location(
        "aota_forge.core.providers._w1_probe", str(PROVIDERS_INIT)
    )
    assert spec is not None and spec.loader is not None, "providers spec not found"
    mod = importlib.util.module_from_spec(spec)
    # Ensure parent package exists for loader; not required for minimal ns
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _providers_has_attr(name: str) -> bool:
    try:
        mod = _load_providers_via_spec()
        return hasattr(mod, name)
    except Exception:
        # Fallback to filesystem search
        return name in PROVIDERS_INIT.read_text(encoding="utf-8")

# Forbidden universal provider abstractions (supplement guard §16)
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

# Forbidden canonical dependencies for Context Provider isolation
FORBIDDEN_CONTEXT_CANONICAL_DEPS = (
    "vector",
    "embedding",
    "reranker",
    "knowledge_card",
    "knowledge card",
    "hot/warm/archive",
    "memory promotion",
)

# Representative provider-private metadata examples (must NOT become canonical)
PRIVATE_METADATA_EXAMPLES = (
    "database_connection",
    "endpoint",
    "transport",
    "mcp_server_identity",
    "shell_syntax",
    "http_session",
    "vector_index",
    "reranker_parameters",
    "storage_backend",
)

# Executor lifecycle surface that must NOT be required for providers
EXECUTOR_LIFECYCLE_METHODS = ("dispatch", "status", "result", "cancel", "resume")
EXECUTOR_LIFECYCLE_FIELDS = ("adapter_handle", "dispatch_attempt_id", "canonical_task_id")


# ---------------------------------------------------------------------------
# 1. Provider != Executor
# ---------------------------------------------------------------------------

class TestProviderExecutorSeparation:
    """PROVIDER_IS_EXECUTOR=no — provider must not inherit executor lifecycle."""

    def test_executor_adapter_is_lifecycle_abstraction(self):
        from aota_forge.core.execution.adapter import ExecutorAdapter
        abstract = {m for m in dir(ExecutorAdapter) if not m.startswith("_")}
        # ExecutorAdapter owns the 7 abstract lifecycle/capability methods
        assert "dispatch" in abstract
        assert "status" in abstract
        assert "result" in abstract
        assert "cancel" in abstract
        assert "resume" in abstract
        assert "capabilities" in abstract
        assert "validate_package" in abstract
        # Confirm abstract methods via inspect
        abstract_methods = getattr(ExecutorAdapter, "__abstractmethods__", frozenset())
        for name in EXECUTOR_LIFECYCLE_METHODS:
            assert name in abstract_methods, f"{name} should be abstract on ExecutorAdapter"
        sig = inspect.signature(ExecutorAdapter.dispatch)
        assert "package" in sig.parameters

    def test_execution_package_is_task_execution_oriented(self):
        from aota_forge.core.execution.package import ExecutionPackage

        field_names = {f.name for f in dataclasses.fields(ExecutionPackage)}
        assert "canonical_task_id" in field_names
        assert "package_id" in field_names
        assert "intent_fingerprint" in field_names
        assert "correlation_id" in field_names
        assert "instruction" in field_names
        # ExecutionPackage is explicitly task/execution-bound, not provider-bound
        assert "provider" not in " ".join(field_names).lower()

    def test_providers_package_does_not_inherit_executor_adapter(self):
        # providers/__init__.py must not import or subclass ExecutorAdapter / ExecutionPackage
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        assert "ExecutorAdapter" not in src
        assert "ExecutionPackage" not in src
        assert "dispatch" not in src.lower() or "dispatch" in src.lower() and len(src) < 500  # minimal docstring only
        # Parse AST to verify no class def, no import of execution
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                pytest.fail(f"providers/__init__.py must not define class {node.name}")
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "execution" not in mod, f"providers package must not import from execution: {mod}"
                assert "adapter" not in mod

    def test_provider_does_not_require_executor_lifecycle_surface(self):
        """A fake provider interface needs none of the executor lifecycle methods."""
        # Define a minimal conceptual provider protocol without lifecycle methods
        import dataclasses

        @dataclasses.dataclass(frozen=True)
        class FakeProviderBinding:
            """Composition/binding metadata only — no lifecycle."""
            capability_ref: str
            provider_hint: str | None = None

        # Verify it has none of the executor lifecycle attributes
        for m in EXECUTOR_LIFECYCLE_METHODS:
            assert not hasattr(FakeProviderBinding, m)
        # Verify existing canonical provider-relevant concepts (capability, operation)
        # do not force lifecycle fields
        from aota_forge.core.contracts.vocabulary import OperationSemanticIdentity

        op = OperationSemanticIdentity(protocol_family="aota-forge.operation-contract", operation_name="test_op")
        assert not hasattr(op, "adapter_handle")
        assert not hasattr(op, "dispatch_attempt_id")

    def test_no_execution_package_reuse_for_provider(self):
        """Provider contract must not reuse ExecutionPackage."""
        # ExecutionPackage must not be reachable via providers package (probe via spec)
        assert not _providers_has_attr("ExecutionPackage")
        # Ensure providers __init__ source has no reference to package lifecycle identities
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        for field in ("adapter_handle", "dispatch_attempt_id", "ExecutionPackage"):
            assert field not in src


# ---------------------------------------------------------------------------
# 2. Provider identity — no first-class canonical identity required
# ---------------------------------------------------------------------------

class TestProviderIdentityNotFirstClass:
    """FIRST_CLASS_PROVIDER_IDENTITY_REQUIRED=no"""

    def test_no_provider_identity_class_in_core(self):
        # Walk core source tree for forbidden provider identity symbols
        forbidden = {"ProviderId", "ProviderIdentity", "CanonicalProviderId"}
        for py in CORE_ROOT.rglob("*.py"):
            # skip __pycache__
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for sym in forbidden:
                # Allow comment mentions but not class/def definitions
                # Check AST for class/def named exactly sym
                try:
                    tree = ast.parse(text)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert node.name != sym, f"{py} defines forbidden {sym}"
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == sym:
                                pytest.fail(f"{py} assigns forbidden {sym}")

    def test_provider_identity_may_remain_composition_binding_metadata(self):
        """Composition binding reference suffices; no canonical ProviderIdentity type needed."""
        # Demonstrate that a plain string hint + capability reference is sufficient
        binding = {"capability": "search.context", "provider_hint": "future-acf"}
        # No canonical type enforces provider_hint; it is opaque composition metadata
        assert isinstance(binding["provider_hint"], str)
        # Verify core has no registry that would require canonical provider ID
        assert not _providers_has_attr("ProviderIdentity")
        assert not _providers_has_attr("ProviderId")

    def test_importing_core_does_not_expose_provider_identity(self):
        for mod_name in [
            "aota_forge.core.contracts.vocabulary",
            "aota_forge.core.contracts.descriptor",
            "aota_forge.core.execution.capabilities",
            "aota_forge.core.authority",
        ]:
            mod = importlib.import_module(mod_name)
            for sym in ("ProviderId", "ProviderIdentity", "CanonicalProviderId"):
                assert not hasattr(mod, sym), f"{mod_name} must not expose {sym}"


# ---------------------------------------------------------------------------
# 3. Capability != Provider identity
# ---------------------------------------------------------------------------

class TestCapabilityProviderIdentitySeparation:
    """CAPABILITY_IS_PROVIDER_IDENTITY=no, NEW_PROVIDER_CAPABILITY_TYPE_REQUIRED=no"""

    def test_capability_is_not_executor_id_and_not_provider_id(self):
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
        # capability descriptor carries executor_id as executor physical identity
        assert caps.executor_id == "hermes"
        # but capability semantic identity is the set of supported modes/roles, not executor_id alone
        assert caps.supported_execution_modes != ("hermes",)
        # No provider identity field exists on ExecutorCapabilities
        assert not hasattr(caps, "provider_id")
        assert not hasattr(caps, "provider_identity")
        # Capability class is not renamed to ProviderCapabilities
        assert ExecutorCapabilities.__name__ == "ExecutorCapabilities"
        assert ExecutorCapabilities.__name__ != "ProviderCapabilities"

    def test_capability_semantic_identity_distinct_from_binding_identity(self):
        from aota_forge.core.contracts.vocabulary import CAPABILITY_IS_EXECUTOR, CAPABILITY_IS_PROFILE, CAPABILITY_FIRST_CLASS

        assert CAPABILITY_FIRST_CLASS is True
        assert CAPABILITY_IS_EXECUTOR is False
        assert CAPABILITY_IS_PROFILE is False
        # Capability is first-class but not provider identity; vocabulary has no provider identity flag
        import aota_forge.core.contracts.vocabulary as vocab
        assert not hasattr(vocab, "PROVIDER_IDENTITY_FIRST_CLASS")
        assert not hasattr(vocab, "CAPABILITY_IS_PROVIDER_IDENTITY")

    def test_no_new_provider_capability_type_created(self):
        # Scan providers package for any Capability type
        if PROVIDERS_INIT.exists():
            src = PROVIDERS_INIT.read_text(encoding="utf-8")
            assert "ProviderCapabilities" not in src
            assert "Capability" not in src or "Provider" not in src  # minimal docstring has neither
        # Walk core for ProviderCapabilities definition
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "ProviderCapabilities":
                    pytest.fail(f"{py} defines ProviderCapabilities — not allowed in W1")

    def test_executor_capabilities_literal_reuse_is_not_provider_canonical(self):
        """ExecutorCapabilities exists as executor evidence; not auto-promoted to provider model."""
        from aota_forge.core.execution.capabilities import ExecutorCapabilities

        # Verify that ExecutorCapabilities is tied to executor domain (executor_id, adapter_kind)
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ExecutorCapabilities)}
        assert "executor_id" in field_names
        assert "adapter_kind" in field_names
        # No provider-specific field
        assert "provider_id" not in field_names
        assert "provider_kind" not in field_names


# ---------------------------------------------------------------------------
# 4. Tool provider != tool call
# ---------------------------------------------------------------------------

class TestToolProviderSeparation:
    """TOOL_PROVIDER_IS_TOOL_CALL=no"""

    def test_tool_provider_is_interface_not_single_invocation(self):
        # Conceptual tool provider maps canonical operation semantics to invocation mechanism
        # It is not itself one shell/MCP/python/http call
        class FakeToolProviderBinding:
            """Interface/binding role — holds mapping, not an invocation."""
            def map_operation(self, operation_name: str) -> dict:
                # Returns a binding description, not an execution
                return {"operation": operation_name, "invocation_mechanism": "deferred"}

        binding = FakeToolProviderBinding()
        result = binding.map_operation("test_op")
        assert result["operation"] == "test_op"
        # The provider itself is not a shell command string, not an MCP call dict with method
        assert not isinstance(binding, str)
        assert result["invocation_mechanism"] != "shell command"

    def test_providers_package_has_no_tool_invocation_mechanisms(self):
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        # Must not import or reference shell/MCP/http mechanisms
        lowered = src.lower()
        for kw in ("mcp", "shell", "http", "subprocess"):
            assert kw not in lowered, f"providers/__init__.py must not reference {kw}"

    def test_operation_descriptor_is_semantic_not_tool_call(self):
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor

        desc = OperationContractDescriptor(
            name="tool_op",
            description="semantic description of what should happen",
        )
        # Descriptor carries semantic fields, not tool-call fields
        assert desc.name == "tool_op"
        assert not hasattr(desc, "shell_command")
        assert not hasattr(desc, "mcp_call")
        assert not hasattr(desc, "http_invocation")


# ---------------------------------------------------------------------------
# 5. Context provider != context store
# ---------------------------------------------------------------------------

class TestContextProviderSeparation:
    """CONTEXT_PROVIDER_IS_CONTEXT_STORE=no"""

    def test_context_provider_independent_of_storage_implementation(self):
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        lowered = src.lower()
        for kw in ("vector", "embedding", "reranker", "knowledge", "memory", "archive", "hot/warm"):
            assert kw not in lowered

        # Core contracts must not depend on storage backend classes
        for mod_name in [
            "aota_forge.core.contracts.vocabulary",
            "aota_forge.core.contracts.descriptor",
            "aota_forge.core.authority",
        ]:
            mod = importlib.import_module(mod_name)
            src_mod = inspect.getsource(mod)
            lowered_mod = src_mod.lower()
            for kw in ("vector db", "vector_db", "embedding engine", "reranker", "knowledge_card"):
                assert kw not in lowered_mod

    def test_no_acf_storage_class_imported_in_core(self):
        # Verify no ACF persistence/storage class is canonical dependency
        forbidden_imports = ("acf", "vector", "embedding", "reranker")
        for py in (CORE_ROOT / "contracts").rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore").lower()
            # Allow mentions in comments about NOT being canonical, but not imports
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for kw in forbidden_imports:
                        assert kw not in node.module.lower(), f"{py} imports forbidden {node.module}"

    def test_fake_context_provider_has_no_storage_backend(self):
        class FakeContextProviderBinding:
            subject: str
            scope: str

            def __init__(self, subject: str, scope: str):
                self.subject = subject
                self.scope = scope

        binding = FakeContextProviderBinding(subject="plan_subject", scope="bounded_retrieval")
        assert binding.subject == "plan_subject"
        assert not hasattr(binding, "vector_index")
        assert not hasattr(binding, "embedding_engine")


# ---------------------------------------------------------------------------
# 6. Private metadata isolation
# ---------------------------------------------------------------------------

class TestProviderPrivateMetadataIsolation:
    """PROVIDER_PRIVATE_RUNTIME_METADATA_NOT_CANONICAL=yes"""

    def _canonical_field_names(self):
        import dataclasses
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor
        from aota_forge.core.execution.capabilities import ExecutorCapabilities
        from aota_forge.core.execution.package import ExecutionPackage
        from aota_forge.core.execution.results import CanonicalResult

        fields_sets = []
        for cls in (OperationContractDescriptor, ExecutorCapabilities, ExecutionPackage, CanonicalResult):
            try:
                fields_sets.append({f.name for f in dataclasses.fields(cls)})
            except TypeError:
                fields_sets.append(set())
        # Vocabulary module level constants
        return set().union(*fields_sets)

    def test_canonical_models_do_not_consume_private_metadata(self):
        canonical_fields = self._canonical_field_names()
        for meta in PRIVATE_METADATA_EXAMPLES:
            assert meta not in canonical_fields, f"private metadata {meta!r} must not be canonical field"
            # also check no substring match like "endpoint" in field names
            # endpoint itself should not be a field
            assert meta not in {f.lower() for f in canonical_fields}

    def test_fake_private_metadata_stays_outside_canonical_envelope(self):
        from aota_forge.core.execution.package import ExecutionPackage

        pkg = ExecutionPackage.create(
            canonical_task_id="task-private-meta-test",
            project_id="aota_forge",
            canonical_role="coder",
            instruction="test private metadata isolation",
            idempotency_key="idem-private-meta",
            correlation_id="corr-private-meta",
        )
        # Simulate provider-private metadata that a provider might hold locally
        fake_private = {
            "database_connection": "postgres://secret",
            "endpoint": "https://internal.example",
            "vector_index": "idx-123",
        }
        # Canonical package dict must not contain private metadata keys
        pkg_dict = pkg.to_dict()
        for k in fake_private:
            assert k not in pkg_dict
            assert k not in pkg_dict.get("working_context", {})
            assert k not in pkg_dict.get("constraints", {})

    def test_no_universal_metadata_schema_in_providers(self):
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        # Must not create a universal metadata schema
        lowered = src.lower()
        assert "metadata" not in lowered or len(src) < 500  # docstring only, no schema
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert "Metadata" not in node.name


# ---------------------------------------------------------------------------
# 7. Universal Provider rejection (supplement guard)
# ---------------------------------------------------------------------------

class TestNoUniversalProviderAbstractions:
    """Guard against premature W1 abstractions."""

    def test_no_provider_base_symbols_in_core(self):
        for sym in FORBIDDEN_PROVIDER_SYMBOLS:
            # Check no class/def with that exact name exists in core
            for py in CORE_ROOT.rglob("*.py"):
                if "__pycache__" in str(py):
                    continue
                try:
                    tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == sym:
                        pytest.fail(f"{py} defines forbidden {sym}")
                    if isinstance(node, ast.FunctionDef) and node.name == sym:
                        pytest.fail(f"{py} defines forbidden {sym}")

    def test_providers_init_does_not_define_forbidden_symbols(self):
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        for sym in FORBIDDEN_PROVIDER_SYMBOLS:
            assert sym not in src, f"providers/__init__.py must not contain {sym}"

    def test_no_common_provider_request_result_model(self):
        # COMMON_PROVIDER_REQUEST_MODEL_REQUIRED=no etc — verify no such model
        for sym in ("ProviderRequest", "ProviderResult", "BaseProvider"):
            assert not _providers_has_attr(sym)
        # Also check execution and contracts modules
        for mod_name in [
            "aota_forge.core.execution.package",
            "aota_forge.core.execution.results",
            "aota_forge.core.contracts.descriptor",
        ]:
            mod = importlib.import_module(mod_name)
            for sym in ("ProviderRequest", "ProviderResult"):
                assert not hasattr(mod, sym)

    def test_no_provider_registry_or_router(self):
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ("ProviderRegistry", "ProviderRouter"):
                    pytest.fail(f"{py} defines {node.name}")


# ---------------------------------------------------------------------------
# 8. Providers package namespace decision
# ---------------------------------------------------------------------------

class TestProvidersPackageNamespace:
    """PROVIDER_PACKAGE_NAMESPACE_REQUIRED_NOW=yes — W1 owns __init__.py creation."""

    def test_providers_package_exists_and_is_minimal(self):
        assert PROVIDERS_INIT.exists(), "providers/__init__.py must exist for W2/W3 concurrency"
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        # Must be small: only docstring, no logic
        assert len(src) < 500, f"providers/__init__.py should be minimal, got {len(src)} chars"
        tree = ast.parse(src)
        # Only allowed: Module docstring + maybe imports (none) + no classes/functions/assignments
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue  # docstring
            pytest.fail(f"providers/__init__.py must contain only docstring, found {ast.dump(node)}")

    def test_no_w2_w3_files_created_in_w1(self):
        """W1 historical source scope is Git/provenance evidence, not a descendant invariant.

        Historical proof: `git diff 61c19e30..53ac289e` shows W1 created only
        `providers/__init__.py`.  Descendants W2/W3 legitimately introduce
        `context.py`/`tool.py`; the persistent invariants are semantic
        boundaries, not absence of those files.
        """
        # Persistent semantic boundary: providers package must not define
        # universal abstractions that W1 forbids, regardless of descendant files.
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        for sym in FORBIDDEN_PROVIDER_SYMBOLS:
            assert sym not in src, f"providers/__init__.py must not define {sym}"
        # No provider YAML / registry / universal base remains absent
        assert not (REPO_ROOT / ".aota" / "providers.yaml").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "base.py").exists()
        assert not (REPO_ROOT / "aota_forge" / "core" / "providers" / "registry.py").exists()
        # ContextProvider and ToolProvider, when present, must remain distinct
        # (verify distinctness without requiring either to be absent)
        import importlib.util as _ilu

        context_spec = _ilu.spec_from_file_location(
            "_w1_ctx_check", str(REPO_ROOT / "aota_forge" / "core" / "providers" / "context.py")
        )
        tool_spec = _ilu.spec_from_file_location(
            "_w1_tool_check", str(REPO_ROOT / "aota_forge" / "core" / "providers" / "tool.py")
        )
        if context_spec is not None and tool_spec is not None:
            # Both may legitimately exist in descendants; verify they are distinct protocols
            import ast as _ast

            ctx_src = (REPO_ROOT / "aota_forge" / "core" / "providers" / "context.py").read_text(
                encoding="utf-8", errors="ignore"
            )
            tool_src = (REPO_ROOT / "aota_forge" / "core" / "providers" / "tool.py").read_text(
                encoding="utf-8", errors="ignore"
            )
            # No common ProviderRequest/ProviderResult base
            assert "ProviderRequest" not in ctx_src
            assert "ProviderRequest" not in tool_src
            assert "BaseProvider" not in ctx_src
            assert "BaseProvider" not in tool_src

    def test_providers_package_is_importable_namespace(self):
        # Load via spec to avoid outer shadowing; verify git-tracked ns is clean
        mod = _load_providers_via_spec()
        # Module spec name is probe; verify file exists and is minimal
        assert PROVIDERS_INIT.exists()
        for sym in FORBIDDEN_PROVIDER_SYMBOLS:
            assert not hasattr(mod, sym)
        # Also verify outer path (if it were imported) would not contain forbidden symbols
        # via filesystem check on git-tracked path
        src = PROVIDERS_INIT.read_text(encoding="utf-8")
        for sym in FORBIDDEN_PROVIDER_SYMBOLS:
            assert sym not in src


# ---------------------------------------------------------------------------
# 9. Architecture variant A — distinct protocols, shared low-level reuse, no universal framework
# ---------------------------------------------------------------------------

class TestArchitectureVariantA:
    """RECOMMENDED_ARCHITECTURE_VARIANT=A"""

    def test_no_universal_provider_framework_introduced(self):
        # Variant A says: no universal Provider framework
        for py in CORE_ROOT.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            # Supplement guard: check no file defines BaseProvider umbrella
            if "class BaseProvider" in text:
                pytest.fail(f"{py} introduces universal BaseProvider")

    def test_shared_low_level_reuse_remains_possible(self):
        # Variant A allows shared low-level reuse (capability semantics, authority, result)
        # Verify those remain importable and not provider-shaped
        from aota_forge.core.execution.capabilities import ExecutorCapabilities
        from aota_forge.core.authority import AuthorityEngine
        from aota_forge.core.execution.results import CanonicalResult

        assert ExecutorCapabilities is not None
        assert AuthorityEngine is not None
        assert CanonicalResult is not None
        # But they are not provider-specific
        assert "Provider" not in ExecutorCapabilities.__name__
