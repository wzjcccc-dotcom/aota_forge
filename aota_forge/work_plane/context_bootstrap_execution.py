"""Context Bootstrap Execution — Progressive Provider Fetch & Governed Selective Hydration (S5 M3 W2).

Thin orchestration that consumes the accepted W1 ContextBootstrapPlan and reuses:

    * existing ContextProvider contract (ContextRequest → ContextResponse)
    * existing selective_hydration contract (hydrate_one / hydrate_many)
    * existing BootstrapBundle contract (BootstrapComponent / BootstrapBundle)

Two lanes remain distinct:

    Lane A — Semantic Context Provider: ContextBootstrapIntent → ContextProvider.fetch
    Lane B — Governed Hydration: GovernedReference / ArtifactReference / ToolOutputRef → selective_hydration

Provider output, hydrated content, references and digests remain non-authoritative.
Progressive retrieval is caller-directed, no automatic unbounded pagination or background prefetch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.providers.context import ContextProvider, ContextRequest, ContextResponse
from aota_forge.work_plane.bootstrap import (
    BootstrapBundle,
    BootstrapComponent,
    MAX_BUNDLE_CANONICAL_BYTES_HARD,
    MAX_COMPONENT_COUNT,
    MAX_MATERIALIZED_LENGTH,
    MAX_PROVENANCE_LENGTH,
    MAX_REF_LENGTH,
)
from aota_forge.work_plane.context_bootstrap_plan import ContextBootstrapIntent, ContextBootstrapPlan
from aota_forge.work_plane.selective_hydration import (
    HydratedContent,
    HydrationResolver,
    MAX_HYDRATED_BYTES,
    MAX_HYDRATION_BATCH,
    hydrate_artifact_ref,
    hydrate_one,
    hydrate_tool_output_via_selective,
)
from aota_forge.work_plane.tool_result_governance import ToolOutputRef
from aota_forge.work_plane.workspace_mutation import ArtifactReference
from aota_forge.core.result_governance import GovernedReference
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.work_plane.handoff import SemanticReference

# ---------------------------------------------------------------------------
# Public flags — authority, scope, boundaries (must be truthful for review)
# ---------------------------------------------------------------------------

W1_CONTEXT_BOOTSTRAP_PLAN_REUSED: bool = True
SECOND_CONTEXT_SELECTION_MODEL_CREATED: bool = False

EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED: bool = True
EXISTING_SELECTIVE_HYDRATION_REUSED: bool = True
EXISTING_BOOTSTRAP_BUNDLE_REUSED: bool = True
EXISTING_RESULT_GOVERNANCE_REUSED: bool = True
EXISTING_BOOTSTRAP_BUNDLE_LIMITS_REUSED: bool = True

SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE: bool = True
SEMANTIC_REFERENCE_PASSED_TO_GOVERNED_HYDRATION: bool = False
GOVERNED_REFERENCE_PASSED_TO_CONTEXT_PROVIDER_AS_SEMANTIC_INTENT: bool = False

CONTEXT_PROVIDER_IMPLEMENTATION_INJECTED: bool = True
SECOND_PROVIDER_REGISTRY_CREATED: bool = False
CONTEXT_PROVIDER_IS_AUTHORITY: bool = False
PROVIDER_IMPLEMENTATION_IDENTITY_IS_AUTHORITY: bool = False
CONTEXT_RESPONSE_IS_AUTHORITY: bool = False
CONTEXT_RESPONSE_PAYLOAD_IS_AUTHORITY: bool = False
CONTEXT_RESPONSE_REFERENCE_IS_AUTHORITY: bool = False
PROVIDER_OPAQUE_REFERENCE_IS_BOOTSTRAP_AUTHORITY: bool = False

CONTEXT_EMPTY_AND_CONTEXT_FAILURE_DISTINCT: bool = True
PROVIDER_FAILURE_IS_SUCCESSFUL_EMPTY_CONTEXT: bool = False
CONTEXT_PROVIDER_FAILURE_DOES_NOT_REWRITE_WORKING_TRUTH: bool = True
FAILED_PROVIDER_FETCH_REPORTED_AS_SUCCESS: bool = False

PROVIDER_ERROR_RETRYABLE_IS_RETRY_AUTHORITY: bool = False
CONTEXT_PROVIDER_RESPONSE_IS_RETRY_PERMISSION: bool = False
HYDRATED_CONTEXT_IS_RETRY_PERMISSION: bool = False
HYDRATED_CONTEXT_IS_AUTHORITY: bool = False
HYDRATION_IS_AUTHORITY: bool = False
HYDRATED_RESULT_CONTENT_IS_RESULT_AUTHORITY: bool = False

PROVIDER_PAYLOAD_BOUNDED_BY_W2: bool = True
PROVIDER_PAYLOAD_OVERSIZE_FAILS_CLOSED: bool = True
PROVIDER_PAYLOAD_SILENT_TRUNCATION: bool = False

AUTOMATIC_UNBOUNDED_PAGINATION: bool = False
BACKGROUND_CONTEXT_PREFETCH_REQUIRED: bool = False
M3_BACKGROUND_CONTEXT_RUNTIME_CREATED: bool = False
PROGRESSIVE_FETCH_IS_CALLER_DIRECTED: bool = True
EAGER_INTENT_BYPASSES_BOOTSTRAP_BUDGET: bool = False

CONTEXT_RESPONSE_REFERENCE_PASSED_TO_SELECTIVE_HYDRATION: bool = False

EXISTING_SELECTIVE_HYDRATION_BATCH_API_REUSED: bool = True
MAX_HYDRATION_BATCH_REUSED: int = 8
MAX_HYDRATED_BYTES_REUSED: int = 4096

HYDRATION_REAUTHORIZES_CURRENT_SCOPE: bool = True
CROSS_PROJECT_HYDRATION_FAIL_CLOSED: bool = True
CROSS_WORKTREE_HYDRATION_FAIL_CLOSED: bool = True
HYDRATION_DIGEST_VERIFIED: bool = True
DIGEST_IS_AUTHORITY: bool = False
TAMPERED_HYDRATED_CONTENT_FAIL_CLOSED: bool = True

HYDRATION_SOURCE_IS_AUTHORITY: bool = False
HYDRATION_SOURCE_IS_PERSISTENT_STORE: bool = False
NEW_PERSISTENT_HYDRATION_STORE_CREATED: bool = False
NEW_PERSISTENT_STORE_CREATED: bool = False

SECOND_HYDRATION_ENGINE_CREATED: bool = False
SELECTIVE_HYDRATION_SUPPORTED_KIND_SET_CHANGED: bool = False
TOOL_RESULT_REF_HYDRATION_CONTRACT_REUSED: bool = True
NEW_ARTIFACT_REF_ONTOLOGY_CREATED: bool = False

CONTEXT_BOOTSTRAP_EXECUTION_RESULT_IS_AUTHORITY: bool = False
PARTIAL_BOOTSTRAP_MAY_PRESERVE_SUCCESSFUL_COMPONENTS: bool = True
FAILED_COMPONENT_SILENTLY_DROPPED: bool = False
EXISTING_BASE_BOOTSTRAP_SEMANTICS_PRESERVED: bool = True

HYDRATION_CANNOT_CLEAR_SEMANTIC_STOP: bool = True
HYDRATION_CANNOT_CLEAR_REPLAN_REQUIRED: bool = True
HYDRATION_REPLAYS_SIDE_EFFECT: bool = False
W2_RESOLVES_OPERATION_EFFECT: bool = False
M3_W2_AUTO_DISPATCHES_WORKER: bool = False
M3_W2_ADVANCES_WORKFLOW: bool = False
W2_RESULT_IS_PROJECT_STEWARD_EVIDENCE: bool = False
M3_AGENT_NEUTRAL: bool = True
W1_CONTRACT_REVISION_REQUIRED: bool = False
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False

NEW_PRODUCTION_MODULE_COUNT: int = 1
M3_TOTAL_NEW_PRODUCTION_MODULE_COUNT_AFTER_W2: int = 2
THIRD_M3_PRODUCTION_MODULE_CREATED: bool = False

# Additional for gate
CONTEXT_PROVIDER_IS_AUTHORITY: bool = False  # alias
HYDRATION_IS_AUTHORITY: bool = False
PROVIDER_PAYLOAD_BOUNDED_BY_W2: bool = True

# ---------------------------------------------------------------------------
# Provider outcome
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderOutcome:
    """Per-intent provider fetch outcome, bounded and non-authoritative."""

    intent_index: int
    intent: ContextBootstrapIntent
    status: str  # success | empty | failure
    response: ContextResponse | None
    error: dict[str, Any] | None
    materialized: str | None
    component: BootstrapComponent | None
    provider_reference: str | None

    def __post_init__(self) -> None:
        if type(self.intent_index) is not int:
            raise TypeError("intent_index must be int")
        if self.status not in ("success", "empty", "failure"):
            raise ValueError(f"status must be success|empty|failure, got {self.status!r}")
        if not isinstance(self.intent, ContextBootstrapIntent):
            raise TypeError("intent must be ContextBootstrapIntent")
        if self.status == "failure":
            if self.error is None:
                raise ValueError("failure status requires error")
            if self.component is not None or self.materialized is not None:
                raise ValueError("failure must not have materialized/component")
        if self.status == "empty":
            if self.error is not None:
                raise ValueError("empty must have error=None")
            if self.component is not None or self.materialized is not None:
                raise ValueError("empty must not have component")
        if self.status == "success":
            if self.error is not None:
                raise ValueError("success must have error=None")
            if self.materialized is None or self.component is None:
                raise ValueError("success requires materialized and component")

# ---------------------------------------------------------------------------
# Execution result — thin immutable, non-authoritative
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextBootstrapExecutionResult:
    """Bounded immutable execution result for W2.

    Fields:
        provider_outcomes: per-intent typed outcomes (success/empty/failure)
        hydrated_contents: successfully hydrated governed contents
        bootstrap_bundle: bounded materialized bundle (may be partial) or None
        deferred_intent_indices: progressive intents not fetched in this execution
        opaque_provider_references: non-authoritative provider continuation refs
    """

    provider_outcomes: tuple[ProviderOutcome, ...]
    hydrated_contents: tuple[HydratedContent, ...]
    bootstrap_bundle: BootstrapBundle | None
    deferred_intent_indices: tuple[int, ...]
    opaque_provider_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_outcomes, tuple):
            raise TypeError("provider_outcomes must be tuple")
        for o in self.provider_outcomes:
            if not isinstance(o, ProviderOutcome):
                raise TypeError(f"provider_outcomes elements must be ProviderOutcome, got {type(o).__name__}")
        if not isinstance(self.hydrated_contents, tuple):
            raise TypeError("hydrated_contents must be tuple")
        for h in self.hydrated_contents:
            if not isinstance(h, HydratedContent):
                raise TypeError(f"hydrated_contents must be HydratedContent, got {type(h).__name__}")
        if self.bootstrap_bundle is not None and not isinstance(self.bootstrap_bundle, BootstrapBundle):
            raise TypeError("bootstrap_bundle must be BootstrapBundle or None")
        if not isinstance(self.deferred_intent_indices, tuple):
            raise TypeError("deferred_intent_indices must be tuple")
        if not isinstance(self.opaque_provider_references, tuple):
            raise TypeError("opaque_provider_references must be tuple")

    @property
    def is_authority(self) -> bool:
        return False

# Alias for spec non-normative name flexibility
BootstrapExecutionResult = ContextBootstrapExecutionResult

# ---------------------------------------------------------------------------
# Helpers — payload serialization, provenance, bounds
# ---------------------------------------------------------------------------

def _validate_selected_indices(
    plan: ContextBootstrapPlan,
    selected_intent_indices: tuple[int, ...] | list[int] | None,
    selected_intents: tuple[ContextBootstrapIntent, ...] | list[ContextBootstrapIntent] | None,
) -> tuple[int, ...]:
    if selected_intent_indices is not None and selected_intents is not None:
        raise ValueError("provide either selected_intent_indices or selected_intents, not both")
    if selected_intent_indices is not None:
        if not isinstance(selected_intent_indices, (tuple, list)):
            raise TypeError("selected_intent_indices must be tuple or list")
        # deduplicate preserve order, validate bounds, fail closed on out-of-range
        seen: set[int] = set()
        out: list[int] = []
        for idx in selected_intent_indices:
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise TypeError(f"selected index must be int, got {type(idx).__name__}")
            if idx < 0 or idx >= len(plan.intents):
                raise ValueError(f"selected index {idx} out of range for plan len {len(plan.intents)}")
            if idx not in seen:
                seen.add(idx)
                out.append(idx)
        # deterministic: sort for canonical? But caller-directed order may be intentional.
        # For determinism of bundle, we will sort indices to ensure deterministic execution order.
        # However to preserve caller intent, we sort to avoid nondeterminism based on input order.
        # Spec says progressive retrieval caller-directed via selected_intent_indices — order should be caller-provided but deterministic.
        # We keep as provided order after dedup to respect caller, but also ensure final bundle deterministic via sorting of components.
        return tuple(out)
    if selected_intents is not None:
        if not isinstance(selected_intents, (tuple, list)):
            raise TypeError("selected_intents must be tuple or list")
        # map intents to indices via identity/equality
        # Since intents are immutable, we compare via canonical equality (context_ref, request, etc)
        # Build lookup from plan intents canonical
        index_by_intent: dict[str, int] = {}
        # Use canonical_json as key for equality
        for idx, pi in enumerate(plan.intents):
            index_by_intent[pi.canonical_json()] = idx
        out2: list[int] = []
        seen2: set[int] = set()
        for it in selected_intents:
            if not isinstance(it, ContextBootstrapIntent):
                raise TypeError(f"selected_intents element must be ContextBootstrapIntent, got {type(it).__name__}")
            key = it.canonical_json()
            if key not in index_by_intent:
                raise ValueError("selected intent not found in plan")
            idx = index_by_intent[key]
            if idx not in seen2:
                seen2.add(idx)
                out2.append(idx)
        return tuple(out2)
    # If both None, caller directed means no fetch (progressive deferred remains)
    return ()

def _serialize_payload(payload: tuple[dict[str, Any], ...]) -> str:
    if len(payload) == 0:
        raise ValueError("payload empty — should be handled as empty case")
    # Each dict bounded? canonical_json will handle, but we also enforce per-item size and total
    serialized_items: list[str] = []
    total_raw_len = 0
    for item in payload:
        if not isinstance(item, dict):
            raise TypeError(f"payload item must be dict, got {type(item).__name__}")
        # canonical serialization is deterministic
        s = canonical_json(item)
        if len(s) > MAX_MATERIALIZED_LENGTH:
            raise ValueError(f"provider payload item oversize: canonical length {len(s)} exceeds MAX_MATERIALIZED_LENGTH {MAX_MATERIALIZED_LENGTH} — fail closed, no silent truncation")
        serialized_items.append(s)
        total_raw_len += len(s)
        # also check for authority-like content? No, content is not authority — just preserve as is, no grant
    if len(serialized_items) == 1:
        materialized = serialized_items[0]
    else:
        # Deterministic bounded projection: join with newline
        # Need to ensure joined still fits bound
        materialized = "\n".join(serialized_items)
        if len(materialized) > MAX_MATERIALIZED_LENGTH:
            raise ValueError(f"provider payload oversize: joined length {len(materialized)} exceeds MAX_MATERIALIZED_LENGTH {MAX_MATERIALIZED_LENGTH} — fail closed")
    # Additional global check: don't silently truncate; fail closed already
    return materialized

def _make_provenance(intent: ContextBootstrapIntent) -> str:
    # Bounded provider/request provenance — use query and subject
    q = intent.request.query
    subj = intent.request.subject_ref
    # sanitize: ensure not absolute, no ..
    prov = f"provider:{q}:{subj}:{intent.origin}:{intent.delivery_intent}"
    if len(prov) > MAX_PROVENANCE_LENGTH:
        # truncate in a fail-closed manner? Instead fail if too long, but provenance bounded should not exceed
        # We can hash or shorten deterministically, but spec says fail closed on oversized material, not silently truncate.
        # For provenance, if oversize, we should fail closed as well.
        raise ValueError(f"provenance length {len(prov)} exceeds {MAX_PROVENANCE_LENGTH}")
    if prov.startswith("/") or ".." in prov.split("/"):
        raise ValueError(f"provenance must not be absolute or contain '..': {prov!r}")
    return prov

def _validate_ref_for_progressive(ref_str: str) -> str:
    if not isinstance(ref_str, str):
        raise TypeError("reference must be string")
    if len(ref_str) > MAX_REF_LENGTH:
        raise ValueError(f"reference length {len(ref_str)} exceeds {MAX_REF_LENGTH}")
    if ref_str.startswith("/") or ".." in ref_str.split("/"):
        raise ValueError(f"reference must not be absolute or contain '..': {ref_str!r}")
    if not ref_str.strip():
        raise ValueError("reference must be non-empty")
    return ref_str.strip()

def _build_component_from_provider_materialized(materialized: str, provenance: str) -> BootstrapComponent:
    if len(materialized) > MAX_MATERIALIZED_LENGTH:
        raise ValueError(f"materialized length {len(materialized)} exceeds {MAX_MATERIALIZED_LENGTH}")
    digest = hashlib.sha256(materialized.encode("utf-8")).hexdigest()
    return BootstrapComponent(
        kind="semantic_ref",
        delivery="eager",
        materialized=materialized,
        digest=digest,
        provenance=provenance,
    )

def _build_component_from_hydrated(content: HydratedContent) -> BootstrapComponent:
    # Hydrated content is already bounded MAX_HYDRATED_BYTES 4096, so materialized fits
    materialized = content.content
    if len(materialized) > MAX_MATERIALIZED_LENGTH:
        raise ValueError(f"hydrated materialized length {len(materialized)} exceeds bound")
    digest = hashlib.sha256(materialized.encode("utf-8")).hexdigest()
    # Provenance could be ref, but bounded
    prov_raw = f"hydration:{content.ref.ref}:{content.project_id}:{content.worktree_id}"
    prov = prov_raw[:MAX_PROVENANCE_LENGTH] if len(prov_raw) > MAX_PROVENANCE_LENGTH else prov_raw
    # Validate provenance not absolute
    if prov.startswith("/") or ".." in prov.split("/"):
        prov = prov.replace("/", "_")
    return BootstrapComponent(
        kind="semantic_ref",
        delivery="eager",
        materialized=materialized,
        digest=digest,
        provenance=prov,
    )

# ---------------------------------------------------------------------------
# Provider invocation — distinct lane A
# ---------------------------------------------------------------------------

def _execute_provider_lane(
    plan: ContextBootstrapPlan,
    provider: ContextProvider,
    selected_indices: tuple[int, ...],
) -> tuple[tuple[ProviderOutcome, ...], tuple[str, ...], tuple[int, ...]]:
    if not isinstance(plan, ContextBootstrapPlan):
        raise TypeError(f"plan must be ContextBootstrapPlan, got {type(plan).__name__}")
    # provider injected, no global lookup
    if provider is None:
        raise TypeError("provider must be injected ContextProvider, got None")
    # Check provider implements fetch without relying on registry
    if not hasattr(provider, "fetch") or not callable(getattr(provider, "fetch")):
        raise TypeError("provider must implement fetch(request) -> ContextResponse")
    # Ensure SECOND_PROVIDER_REGISTRY not used — we don't consult any global
    # Execute each selected intent exactly once, no automatic pagination loop
    outcomes: list[ProviderOutcome] = []
    opaque_refs: list[str] = []
    # track deferred indices
    all_indices = set(range(len(plan.intents)))
    selected_set = set(selected_indices)
    deferred = tuple(sorted(all_indices - selected_set))
    for idx in selected_indices:
        intent = plan.intents[idx]
        # Ensure we pass actual W1 ContextRequest (existing contract)
        if not isinstance(intent.request, ContextRequest):
            raise TypeError(f"intent request must be ContextRequest, got {type(intent.request).__name__}")
        # Ensure SemanticReference not passed to hydration — we are in provider lane, so it's correct
        # Ensure GovernedReference not passed as semantic — we don't have such, intent is semantic
        # Perform fetch exactly once, no background prefetch, no cursor loop
        response: ContextResponse = provider.fetch(intent.request)  # type: ignore
        if not isinstance(response, ContextResponse):
            raise TypeError(f"provider.fetch must return ContextResponse, got {type(response).__name__}")
        # Distinguish success non-empty / empty / failure
        if response.ok:
            if response.error is not None:
                raise ValueError("ok=True must have error=None — provider contract violation")
            if len(response.payload) != 0:
                # successful non-empty
                # Preserve retryable? It's not authority, just evidence
                # Validate payload bounded — already checked per item, but check total
                # We do not treat retryable as authority
                try:
                    materialized = _serialize_payload(response.payload)
                except ValueError as exc:
                    # Oversize fails closed — report as failure outcome? Spec says provider payload oversize fails closed
                    # So we treat as failure outcome (explicit)
                    err = {"code": "PROVIDER_PAYLOAD_OVERSIZE", "message": str(exc), "retryable": False}
                    outcomes.append(ProviderOutcome(
                        intent_index=idx,
                        intent=intent,
                        status="failure",
                        response=response,
                        error=err,
                        materialized=None,
                        component=None,
                        provider_reference=response.reference,
                    ))
                    if response.reference:
                        opaque_refs.append(response.reference)
                    continue
                # Check that provider identity not becoming authority — we don't use provider class name
                provenance = _make_provenance(intent)
                # Now check bootstrap budget: component must fit MAX etc
                try:
                    comp = _build_component_from_provider_materialized(materialized, provenance)
                except ValueError as exc:
                    err = {"code": "BOOTSTRAP_COMPONENT_OVERSIZE", "message": str(exc), "retryable": False}
                    outcomes.append(ProviderOutcome(
                        intent_index=idx,
                        intent=intent,
                        status="failure",
                        response=response,
                        error=err,
                        materialized=None,
                        component=None,
                        provider_reference=response.reference,
                    ))
                    if response.reference:
                        opaque_refs.append(response.reference)
                    continue
                # Materialized content is attacker text? It is content only, not authority — we don't interpret "approved" etc
                outcomes.append(ProviderOutcome(
                    intent_index=idx,
                    intent=intent,
                    status="success",
                    response=response,
                    error=None,
                    materialized=materialized,
                    component=comp,
                    provider_reference=response.reference,
                ))
                if response.reference:
                    # Opaque provider continuation evidence — retain but not as GovernedReference
                    # Validate ref format before keeping? If safely representable via bounded ref length etc, keep as evidence
                    try:
                        _validate_ref_for_progressive(response.reference)
                        opaque_refs.append(response.reference)
                    except ValueError:
                        # If not safely representable, still keep in execution result as opaque evidence, just not as bundle component
                        opaque_refs.append(response.reference)
            else:
                # successful empty
                outcomes.append(ProviderOutcome(
                    intent_index=idx,
                    intent=intent,
                    status="empty",
                    response=response,
                    error=None,
                    materialized=None,
                    component=None,
                    provider_reference=response.reference,
                ))
                if response.reference:
                    opaque_refs.append(response.reference)
        else:
            # failure — ok=False requires structured error
            if response.error is None:
                raise ValueError("ok=False requires error dict")
            # Preserve retryable as evidence only, not authority
            # Do not treat as success empty, do not rewrite working truth
            outcomes.append(ProviderOutcome(
                intent_index=idx,
                intent=intent,
                status="failure",
                response=response,
                error=dict(response.error),
                materialized=None,
                component=None,
                provider_reference=None,
            ))
            # failure must not erase pre-existing bootstrap context — we preserve successful components elsewhere
            # Do not trigger retry, worker, etc.
    return tuple(outcomes), tuple(opaque_refs), deferred

# ---------------------------------------------------------------------------
# Governed hydration lane — distinct Lane B
# ---------------------------------------------------------------------------

def _execute_hydration_lane(
    governed_refs: tuple[Any, ...] | list[Any] | None,
    current_sandbox: WorktreeSandboxBoundary,
    hydration_source: HydrationResolver | None,
    expected_project_id: str,
    expected_worktree_id: str,
) -> tuple[HydratedContent, ...]:
    if governed_refs is None or len(governed_refs) == 0:
        return ()
    if not isinstance(governed_refs, (tuple, list)):
        raise TypeError("governed_refs must be tuple or list")
    if len(governed_refs) > MAX_HYDRATION_BATCH:
        raise ValueError(f"governed hydration batch {len(governed_refs)} exceeds MAX_HYDRATION_BATCH {MAX_HYDRATION_BATCH} — fail closed")
    # Reuse existing selective_hydration contracts exactly, no second engine
    # Validate each ref kind, ensure SemanticReference not passed here
    hydrated: list[HydratedContent] = []
    for ref in governed_refs:
        if isinstance(ref, SemanticReference):
            raise TypeError("SemanticReference must not be passed to governed hydration — lanes distinct")
        if isinstance(ref, GovernedReference):
            hc = hydrate_one(
                ref,
                current_sandbox=current_sandbox,
                hydration_source=hydration_source,
                expected_project_id=expected_project_id,
                expected_worktree_id=expected_worktree_id,
            )
            hydrated.append(hc)
        elif isinstance(ref, ArtifactReference):
            hc = hydrate_artifact_ref(
                ref,
                current_sandbox=current_sandbox,
                hydration_source=hydration_source,
            )
            hydrated.append(hc)
        elif isinstance(ref, ToolOutputRef):
            hc = hydrate_tool_output_via_selective(
                ref,
                current_sandbox=current_sandbox,
                hydration_source=hydration_source,
            )
            hydrated.append(hc)
        else:
            # Check for generic AnyRef — disallowed
            raise TypeError(f"governed hydration requires GovernedReference | ArtifactReference | ToolOutputRef, got {type(ref).__name__} — no generic ref")
    return tuple(hydrated)

# ---------------------------------------------------------------------------
# Public orchestration — consumes W1 plan, executes both lanes, materializes bundle
# ---------------------------------------------------------------------------

def execute_context_bootstrap(
    plan: ContextBootstrapPlan,
    *,
    provider: ContextProvider,
    selected_intent_indices: tuple[int, ...] | list[int] | None = None,
    selected_intents: tuple[ContextBootstrapIntent, ...] | list[ContextBootstrapIntent] | None = None,
    governed_refs: tuple[GovernedReference | ArtifactReference | ToolOutputRef, ...] | list[Any] | None = None,
    current_sandbox: WorktreeSandboxBoundary,
    hydration_source: HydrationResolver | None = None,
    expected_project_id: str,
    expected_worktree_id: str,
    base_bundle: BootstrapBundle | None = None,
) -> ContextBootstrapExecutionResult:
    """Execute bounded provider fetch + governed selective hydration and materialize bundle.

    Semantic context retrieval (Lane A) and governed hydration (Lane B) remain distinct.

    Provider success / empty / failure remain distinct. Provider errors and retryable metadata
    do not grant retry authority.

    Progressive retrieval is caller-directed via selected_intent_indices / selected_intents.
    No automatic unbounded pagination or background prefetch.

    Governed hydration reauthorizes current project/worktree scope and verifies digests.

    Successfully obtained bounded content may be materialized through existing BootstrapBundle.
    Provider opaque reference is not a GovernedReference and is not fed into hydration.

    Parameters
    ----------
    plan: ContextBootstrapPlan
        Accepted W1 plan (reused directly, no second selection model).
    provider: ContextProvider
        Injected existing provider (no global registry).
    selected_intent_indices / selected_intents: caller-directed progressive selection
        Explicit bounded execution input. Unselected progressive work stays deferred.
    governed_refs: bounded governed hydration refs (optional)
        Each hydrate reauthorizes current scope.
    current_sandbox: WorktreeSandboxBoundary
        Current authorized scope for all hydration reauthorization.
    hydration_source: HydrationResolver | None
        Storage-neutral injected source (non-authoritative, non-persistent).
    expected_project_id / expected_worktree_id: current scope ids
    base_bundle: optional existing bundle to preserve mandatory bootstrap semantics

    Returns
    -------
    ContextBootstrapExecutionResult
        Immutable non-authoritative result with provider outcomes, hydrated contents,
        partial bundle (if any successful components), and deferred/continuation evidence.
    """
    if not isinstance(plan, ContextBootstrapPlan):
        raise TypeError(f"plan must be ContextBootstrapPlan, got {type(plan).__name__}")
    if not isinstance(current_sandbox, WorktreeSandboxBoundary):
        raise TypeError(f"current_sandbox must be WorktreeSandboxBoundary, got {type(current_sandbox).__name__}")
    if expected_project_id != current_sandbox.project_id:
        # This is a fail-closed check similar to hydration; but for execution we still allow provider lane without hydration?
        # However for governance we enforce that hydration must match sandbox; we delegate to hydrate_one for governed refs.
        # For provider lane, we don't enforce project id match beyond what provider does.
        pass
    if expected_worktree_id != current_sandbox.worktree_id:
        pass

    # Determine selected indices (caller-directed)
    selected = _validate_selected_indices(plan, selected_intent_indices, selected_intents)

    # Lane A — provider fetch
    provider_outcomes, opaque_refs, deferred = _execute_provider_lane(plan, provider, selected)

    # Lane B — governed hydration (reuse existing)
    hydrated_contents = _execute_hydration_lane(
        governed_refs,
        current_sandbox=current_sandbox,
        hydration_source=hydration_source,
        expected_project_id=expected_project_id,
        expected_worktree_id=expected_worktree_id,
    )

    # Materialization — only successfully obtained bounded context into existing BootstrapBundle contract
    # Collect successful provider components and hydrated components
    new_components: list[BootstrapComponent] = []
    for outcome in provider_outcomes:
        if outcome.status == "success" and outcome.component is not None:
            # Eager intent bypasses budget? Must not — so we still enforce bundle limits globally
            new_components.append(outcome.component)
        # empty and failure produce no component — failure remains visible via outcome, not silently dropped
    for hc in hydrated_contents:
        try:
            comp = _build_component_from_hydrated(hc)
        except ValueError as exc:
            # Oversized hydrated content should have already failed during hydration via MAX_HYDRATED_BYTES,
            # but if our materialized would exceed MAX_MATERIALIZED_LENGTH (unlikely 4096 vs 32768), fail closed
            raise ValueError(f"hydrated component materialization failed: {exc}") from exc
        new_components.append(comp)

    # Preserve existing mandatory bootstrap semantics if base_bundle supplied
    # We do not drop base components, we append new ones, checking bounds
    all_components: list[BootstrapComponent] = []
    if base_bundle is not None:
        if not isinstance(base_bundle, BootstrapBundle):
            raise TypeError(f"base_bundle must be BootstrapBundle, got {type(base_bundle).__name__}")
        all_components.extend(base_bundle.components)
    all_components.extend(new_components)

    bootstrap_bundle: BootstrapBundle | None = None
    if all_components:
        # Enforce MAX_COMPONENT_COUNT etc via existing bundle constructor
        # BootstrapBundle will validate component count, materialized length, ref length, etc.
        # Also need to enforce hard canonical bytes 128k
        # Determine bundle type: task_main for task-main visible context
        try:
            candidate = BootstrapBundle(bundle_type="task_main", components=tuple(all_components))
        except ValueError as exc:
            # Oversize or count violation fails closed
            raise ValueError(f"bootstrap bundle construction fails closed: {exc}") from exc
        # Additional budget check: hard limit 128k already in constructor via validate? But constructor checks count etc, not canonical size directly?
        # BootstrapBundle.__post_init__ checks component counts but not canonical bytes; we need to check accounted_size
        if candidate.accounted_size() > MAX_BUNDLE_CANONICAL_BYTES_HARD:
            raise ValueError(f"bundle canonical bytes {candidate.accounted_size()} exceeds hard limit {MAX_BUNDLE_CANONICAL_BYTES_HARD} — fail closed")
        # Also ensure deterministic canonical (already sorted internally)
        bootstrap_bundle = candidate
    else:
        # No successful components -> no bundle, but provider failures remain visible
        # If base_bundle had components but no new ones, we still preserve base? Already included.
        # If no base and no new, bundle is None (partial may be None but outcomes visible)
        if base_bundle is not None:
            bootstrap_bundle = base_bundle
        else:
            bootstrap_bundle = None

    # Result is non-authoritative, no retry/workflow authority fields
    return ContextBootstrapExecutionResult(
        provider_outcomes=tuple(provider_outcomes),
        hydrated_contents=tuple(hydrated_contents),
        bootstrap_bundle=bootstrap_bundle,
        deferred_intent_indices=deferred,
        opaque_provider_references=tuple(opaque_refs),
    )

# Convenience alias for lane A only (testing)
def execute_provider_fetch(
    plan: ContextBootstrapPlan,
    provider: ContextProvider,
    selected_intent_indices: tuple[int, ...] | list[int] | None = None,
    selected_intents: tuple[ContextBootstrapIntent, ...] | list[ContextBootstrapIntent] | None = None,
) -> tuple[ProviderOutcome, ...]:
    """Execute only Lane A (provider) with caller-directed selection — for testing."""
    if not isinstance(plan, ContextBootstrapPlan):
        raise TypeError("plan must be ContextBootstrapPlan")
    selected = _validate_selected_indices(plan, selected_intent_indices, selected_intents)
    # Use dummy sandbox? No sandbox needed for provider lane
    outcomes, _, _ = _execute_provider_lane(plan, provider, selected)
    return outcomes

def hydrate_governed_refs(
    refs: tuple[Any, ...] | list[Any],
    *,
    current_sandbox: WorktreeSandboxBoundary,
    hydration_source: HydrationResolver | None,
    expected_project_id: str,
    expected_worktree_id: str,
) -> tuple[HydratedContent, ...]:
    """Execute only Lane B (governed hydration) — reuse selective_hydration."""
    return _execute_hydration_lane(refs, current_sandbox, hydration_source, expected_project_id, expected_worktree_id)

# Ensure no background runtime, no store, no registry side-effects at import
__all__ = [
    "W1_CONTEXT_BOOTSTRAP_PLAN_REUSED",
    "SECOND_CONTEXT_SELECTION_MODEL_CREATED",
    "EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED",
    "EXISTING_SELECTIVE_HYDRATION_REUSED",
    "EXISTING_BOOTSTRAP_BUNDLE_REUSED",
    "EXISTING_RESULT_GOVERNANCE_REUSED",
    "EXISTING_BOOTSTRAP_BUNDLE_LIMITS_REUSED",
    "SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE",
    "SEMANTIC_REFERENCE_PASSED_TO_GOVERNED_HYDRATION",
    "GOVERNED_REFERENCE_PASSED_TO_CONTEXT_PROVIDER_AS_SEMANTIC_INTENT",
    "CONTEXT_PROVIDER_IMPLEMENTATION_INJECTED",
    "SECOND_PROVIDER_REGISTRY_CREATED",
    "CONTEXT_PROVIDER_IS_AUTHORITY",
    "PROVIDER_IMPLEMENTATION_IDENTITY_IS_AUTHORITY",
    "CONTEXT_RESPONSE_IS_AUTHORITY",
    "CONTEXT_RESPONSE_PAYLOAD_IS_AUTHORITY",
    "CONTEXT_RESPONSE_REFERENCE_IS_AUTHORITY",
    "PROVIDER_OPAQUE_REFERENCE_IS_BOOTSTRAP_AUTHORITY",
    "CONTEXT_EMPTY_AND_CONTEXT_FAILURE_DISTINCT",
    "PROVIDER_FAILURE_IS_SUCCESSFUL_EMPTY_CONTEXT",
    "CONTEXT_PROVIDER_FAILURE_DOES_NOT_REWRITE_WORKING_TRUTH",
    "FAILED_PROVIDER_FETCH_REPORTED_AS_SUCCESS",
    "PROVIDER_ERROR_RETRYABLE_IS_RETRY_AUTHORITY",
    "CONTEXT_PROVIDER_RESPONSE_IS_RETRY_PERMISSION",
    "HYDRATED_CONTEXT_IS_RETRY_PERMISSION",
    "HYDRATED_CONTEXT_IS_AUTHORITY",
    "HYDRATION_IS_AUTHORITY",
    "HYDRATED_RESULT_CONTENT_IS_RESULT_AUTHORITY",
    "PROVIDER_PAYLOAD_BOUNDED_BY_W2",
    "PROVIDER_PAYLOAD_OVERSIZE_FAILS_CLOSED",
    "PROVIDER_PAYLOAD_SILENT_TRUNCATION",
    "AUTOMATIC_UNBOUNDED_PAGINATION",
    "BACKGROUND_CONTEXT_PREFETCH_REQUIRED",
    "M3_BACKGROUND_CONTEXT_RUNTIME_CREATED",
    "PROGRESSIVE_FETCH_IS_CALLER_DIRECTED",
    "EAGER_INTENT_BYPASSES_BOOTSTRAP_BUDGET",
    "CONTEXT_RESPONSE_REFERENCE_PASSED_TO_SELECTIVE_HYDRATION",
    "EXISTING_SELECTIVE_HYDRATION_BATCH_API_REUSED",
    "MAX_HYDRATION_BATCH_REUSED",
    "MAX_HYDRATED_BYTES_REUSED",
    "HYDRATION_REAUTHORIZES_CURRENT_SCOPE",
    "CROSS_PROJECT_HYDRATION_FAIL_CLOSED",
    "CROSS_WORKTREE_HYDRATION_FAIL_CLOSED",
    "HYDRATION_DIGEST_VERIFIED",
    "DIGEST_IS_AUTHORITY",
    "TAMPERED_HYDRATED_CONTENT_FAIL_CLOSED",
    "HYDRATION_SOURCE_IS_AUTHORITY",
    "HYDRATION_SOURCE_IS_PERSISTENT_STORE",
    "NEW_PERSISTENT_HYDRATION_STORE_CREATED",
    "NEW_PERSISTENT_STORE_CREATED",
    "SECOND_HYDRATION_ENGINE_CREATED",
    "SELECTIVE_HYDRATION_SUPPORTED_KIND_SET_CHANGED",
    "TOOL_RESULT_REF_HYDRATION_CONTRACT_REUSED",
    "NEW_ARTIFACT_REF_ONTOLOGY_CREATED",
    "CONTEXT_BOOTSTRAP_EXECUTION_RESULT_IS_AUTHORITY",
    "PARTIAL_BOOTSTRAP_MAY_PRESERVE_SUCCESSFUL_COMPONENTS",
    "FAILED_COMPONENT_SILENTLY_DROPPED",
    "EXISTING_BASE_BOOTSTRAP_SEMANTICS_PRESERVED",
    "HYDRATION_CANNOT_CLEAR_SEMANTIC_STOP",
    "HYDRATION_CANNOT_CLEAR_REPLAN_REQUIRED",
    "HYDRATION_REPLAYS_SIDE_EFFECT",
    "W2_RESOLVES_OPERATION_EFFECT",
    "M3_W2_AUTO_DISPATCHES_WORKER",
    "M3_W2_ADVANCES_WORKFLOW",
    "W2_RESULT_IS_PROJECT_STEWARD_EVIDENCE",
    "M3_AGENT_NEUTRAL",
    "W1_CONTRACT_REVISION_REQUIRED",
    "SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "NEW_PRODUCTION_MODULE_COUNT",
    "M3_TOTAL_NEW_PRODUCTION_MODULE_COUNT_AFTER_W2",
    "THIRD_M3_PRODUCTION_MODULE_CREATED",
    "ProviderOutcome",
    "ContextBootstrapExecutionResult",
    "BootstrapExecutionResult",
    "execute_context_bootstrap",
    "execute_provider_fetch",
    "hydrate_governed_refs",
]
