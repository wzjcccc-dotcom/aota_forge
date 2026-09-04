"""Context Bootstrap Selection & Provider Binding Contract (S5 M3 W1).

Pure deterministic thin planning contract that composes:

    TaskHandoff.context_refs  (current intent)
  + WorkingTruthProjection.context_refs (recovered continuity)

into bounded ContextRequest bindings with delivery intent, without
fetching provider content, hydrating refs, or materializing BootstrapBundle.

Invariants
----------
* W1_CONTRACT_ONLY=yes, PURE_BOOTSTRAP_SELECTION_SEPARATE_FROM_IO=yes
* EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED=yes (ContextRequest)
* EXISTING_SEMANTIC_REFERENCE_REUSED=yes, EXISTING_BOOTSTRAP_BUNDLE_REUSED=yes
* SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE=yes
* CURRENT_TASK_SCOPE_WINS_OVER_RECOVERED_CONTEXT=yes
* CROSS_PROJECT / CROSS_PLAN recovered continuity fails closed
* Candidate composition bounded: TaskHandoff <=16, WorkingTruth <=16, union <=32,
  no silent truncation
* Exact duplicate (ref,digest) deduplicated; current wins same-ref differing
  digest; same-origin conflicting digest fails closed
* Stable ordering: (ref, digest-or-empty) within each origin, current group
  precedes recovered group
* ContextRequest binding uses existing schema (subject_ref, scope, query,
  limit 1..100, optional cursor/capability_ref/correlation_id) without ACF knobs
* Eager vs progressive is delivery intent only; no fake eager materialization
* Budget: WITHIN_BUDGET → current may be eager, recovered progressive;
  otherwise all progressive; missing evidence progressive-only
* Compact governance metadata only, not full Plan/history
* No provider fetch, no hydration execution, no BootstrapBundle materialization,
  no dispatch of execution, no retry authority, agent-neutral
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.providers.context import ContextRequest
from aota_forge.work_plane.context_lifecycle import RolloverDecision, RolloverDisposition
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.session_checkpoint import WorkingTruthProjection

# ---------------------------------------------------------------------------
# Public flags — authority, scope, boundaries
# ---------------------------------------------------------------------------

W1_CONTRACT_ONLY: bool = True
PURE_BOOTSTRAP_SELECTION_SEPARATE_FROM_IO: bool = True
W1_FINAL_BOOTSTRAP_MATERIALIZATION_REQUIRED: bool = False
W1_PROVIDER_FETCH_REQUIRED: bool = False
W1_SELECTIVE_HYDRATION_REQUIRED: bool = False
W1_EAGER_IS_DELIVERY_INTENT_ONLY: bool = True
W1_FAKE_EAGER_MATERIALIZATION_ALLOWED: bool = False

EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED: bool = True
EXISTING_SEMANTIC_REFERENCE_REUSED: bool = True
EXISTING_BOOTSTRAP_BUNDLE_REUSED: bool = True
EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED: bool = True
TASK_HANDOFF_CONTEXT_REFS_REUSED: bool = True
M2_WORKING_TRUTH_CONTEXT_REFS_REUSED: bool = True

SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE: bool = True

CURRENT_TASK_SCOPE_WINS_OVER_RECOVERED_CONTEXT: bool = True
CROSS_PROJECT_RECOVERED_CONTEXT_FAILS_CLOSED: bool = True
CROSS_PLAN_RECOVERED_CONTEXT_FAILS_CLOSED: bool = True

CONTEXT_REF_COMPOSITION_BOUNDED: bool = True
CONTEXT_REF_COMPOSITION_NO_SILENT_TRUNCATION: bool = True
M3_CONTEXT_REF_CANDIDATE_MAX: int = 32

CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE: bool = True
CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_IS_AUTHORITY: bool = False
CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_PROVES_FRESHNESS: bool = False
SAME_ORIGIN_CONTEXT_DIGEST_CONFLICT_FAILS_CLOSED: bool = True
CONTEXT_REF_ORDERING_DETERMINISTIC: bool = True

CONTEXT_REQUEST_SCHEMA_CHANGE_REQUIRED: bool = False
CONTEXT_REQUEST_BINDING_CONTRACT: bool = True
CONTEXT_REQUEST_SUBJECT_IS_CURRENT_SCOPE_DERIVED: bool = True
CONTEXT_REQUEST_QUERY_IS_BOUNDED_CONTEXT_INTENT: bool = True
CONTEXT_REQUEST_EXISTING_LIMIT_BOUND_REUSED: bool = True

ACF_PRIVATE_RETRIEVAL_KNOBS_CANONICALIZED_IN_FORGE: bool = False

BOOTSTRAP_COMPONENT_BOUND_REUSED: bool = True

FULL_PLAN_BODY_BOOTSTRAP_REQUIRED: bool = False
FULL_GOVERNANCE_HISTORY_BOOTSTRAP_REQUIRED: bool = False
BOOTSTRAPPED_GOVERNANCE_METADATA_IS_AUTHORITY: bool = False

W1_PROVIDER_INVOCATION_COUNT: int = 0
W1_HYDRATION_INVOCATION_COUNT: int = 0

PROVIDER_FETCH_IS_HYDRATION_AUTHORITY: bool = False
CONTEXT_RESPONSE_IS_HYDRATION_AUTHORITY: bool = False
RECOVERED_CONTEXT_REF_POSSESSION_IS_HYDRATION_AUTHORITY: bool = False
DIGEST_IS_AUTHORITY: bool = False

CONTEXT_BOOTSTRAP_PLAN_IS_RETRY_PERMISSION: bool = False
CONTEXT_REQUEST_IS_RETRY_PERMISSION: bool = False
CONTEXT_REF_IS_RETRY_PERMISSION: bool = False
M2_RECOVERY_AUTHORITY_FIREWALL_RETAINED: bool = True
BOOTSTRAP_PLANNING_CANNOT_CLEAR_SEMANTIC_STOP: bool = True
BOOTSTRAP_PLANNING_CANNOT_CLEAR_REPLAN_REQUIRED: bool = True
M3_W1_AUTO_DISPATCHES_WORKER: bool = False
M3_AGENT_NEUTRAL: bool = True
MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False

SECOND_PROVIDER_REGISTRY_CREATED: bool = False
NEW_CONTEXT_STORE_CREATED: bool = False
NEW_PROVIDER_CACHE_CREATED: bool = False
NEW_HYDRATION_STORE_CREATED: bool = False
M3_BACKGROUND_CONTEXT_RUNTIME_CREATED: bool = False
NEW_CONTEXT_FRESHNESS_ENGINE_CREATED: bool = False
NEW_BOOTSTRAP_ONTOLOGY_CREATED: bool = False
NEW_CONTEXT_RUNTIME_CREATED: bool = False

M3_NEW_PRODUCTION_MODULE_COUNT_AFTER_W1: int = 1
M3_NEW_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX: int = 2

M2_PREDECESSOR_CONTRACT_REVISION_REQUIRED: bool = False
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
AGGREGATOR_CHANGE_REQUIRED_FOR_W1: bool = False
W2_SCOPE_PULLED_FORWARD_BY_W1: bool = False
ARBITRARY_CONTEXT_BOOTSTRAP_METADATA_ALLOWED: bool = False

CONTEXT_BOOTSTRAP_PLAN_DIGEST_IS_AUTHORITY: bool = False
CONTEXT_BOOTSTRAP_INTENT_IS_AUTHORITY: bool = False
CONTEXT_BOOTSTRAP_PLAN_CONTRACT: bool = True
NEW_PRODUCTION_MODULE_COUNT: int = 1

# Slice semantics
M3_ADDED_CONTEXT_WHEN_BUDGET_UNDETERMINED: str = "PROGRESSIVE_ONLY"
MISSING_CONTEXT_BUDGET_EVIDENCE_ALLOWS_UNBOUNDED_EAGER_HYDRATION: bool = False

ROLLOVER_DISPOSITION_IS_CONTEXT_AUTHORITY: bool = False

# ---------------------------------------------------------------------------
# Allowed fields — strict fail-closed
# ---------------------------------------------------------------------------

_ALLOWED_INTENT_FIELDS: frozenset[str] = frozenset({
    "context_ref",
    "request",
    "delivery_intent",
    "origin",
})

_ALLOWED_PLAN_FIELDS: frozenset[str] = frozenset({
    "intents",
    "project_ref",
    "plan_ref",
    "milestone_ref",
    "work_item_ref",
    "accepted_frontier_ref",
    "reviewed_frontier_ref",
    "workflow_disposition_ref",
})

_FORBIDDEN_AUTHORITY_FIELDS: frozenset[str] = frozenset({
    "authorized",
    "approved",
    "retry_authorized",
    "safe_to_hydrate",
    "safe_to_retry",
    "fresh",
    "trusted",
    "authority",
    "is_authority",
    "dispatch_authorized",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_semantic_ref(value: Any, label: str) -> SemanticReference:
    try:
        return SemanticReference.from_value(value)
    except (TypeError, ValueError) as exc:
        raise type(exc)(f"{label}: {exc}") from exc


def _refs_equal(a: SemanticReference | None, b: SemanticReference | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.ref == b.ref and a.digest == b.digest


def _derive_subject(handoff: TaskHandoff, recovered: WorkingTruthProjection | None) -> str:
    # most-specific current semantic subject available (work_item → milestone → plan → project)
    # with safe fallback to reconciled WorkingTruth subject
    for attr in ("work_item_ref", "milestone_ref", "plan_ref", "project_ref"):
        val = getattr(handoff, attr, None)
        if val is not None:
            if isinstance(val, SemanticReference):
                return val.ref
            # fallback string
            return str(val)
    if recovered is not None:
        for attr in ("active_work_item_ref", "milestone_ref", "plan_ref", "project_ref"):
            val = getattr(recovered, attr, None)
            if val is not None:
                if isinstance(val, SemanticReference):
                    return val.ref
                return str(val)
    # last resort: bounded_scope as subject? but spec says subject derived from current scope
    # if still none, fail closed
    raise ValueError("cannot derive subject_ref from current handoff or recovered truth")


def _normalize_disposition(
    disposition: RolloverDisposition | RolloverDecision | str | None,
) -> RolloverDisposition | None:
    if disposition is None:
        return None
    if isinstance(disposition, RolloverDecision):
        return disposition.disposition
    if isinstance(disposition, RolloverDisposition):
        return disposition
    if isinstance(disposition, str) and type(disposition) is str:
        try:
            return RolloverDisposition(disposition)
        except ValueError:
            return None
    return None


def _detect_same_origin_conflict(refs: tuple[SemanticReference, ...], label: str) -> None:
    # group by ref string, collect distinct digests
    groups: dict[str, set[str | None]] = {}
    for r in refs:
        groups.setdefault(r.ref, set()).add(r.digest)
    for ref_str, digests in groups.items():
        if len(digests) > 1:
            raise ValueError(f"{label} conflicting digests for same ref {ref_str!r}: {sorted(d or '' for d in digests)!r} — fail closed")


def _deduplicate_exact(refs: tuple[SemanticReference, ...]) -> tuple[SemanticReference, ...]:
    seen: set[tuple[str, str | None]] = set()
    out: list[SemanticReference] = []
    for r in refs:
        key = (r.ref, r.digest)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return tuple(out)


def _sorted_refs(refs: tuple[SemanticReference, ...]) -> tuple[SemanticReference, ...]:
    return tuple(sorted(refs, key=lambda r: (r.ref, r.digest or "")))


# ---------------------------------------------------------------------------
# ContextBootstrapIntent — per-ref immutable intent
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextBootstrapIntent:
    """Immutable per-ref selection + binding intent.

    Fields
    ------
    context_ref: SemanticReference (bounded context intent)
    request: ContextRequest (existing contract binding)
    delivery_intent: eager | progressive (intent only, not materialization)
    origin: current_handoff | recovered_continuity (provenance only)
    """

    context_ref: SemanticReference
    request: ContextRequest
    delivery_intent: str
    origin: str

    def __post_init__(self) -> None:
        # context_ref
        if not isinstance(self.context_ref, SemanticReference):
            if isinstance(self.context_ref, Mapping):
                norm = SemanticReference.from_value(self.context_ref)
                object.__setattr__(self, "context_ref", norm)
            else:
                raise TypeError(f"context_ref must be SemanticReference, got {type(self.context_ref).__name__}")
        # request
        if not isinstance(self.request, ContextRequest):
            raise TypeError(f"request must be ContextRequest, got {type(self.request).__name__}")
        # delivery_intent
        if not isinstance(self.delivery_intent, str) or type(self.delivery_intent) is not str:
            raise TypeError(f"delivery_intent must be str, got {type(self.delivery_intent).__name__}")
        di = self.delivery_intent.strip().lower()
        if di not in ("eager", "progressive"):
            raise ValueError(f"delivery_intent must be eager or progressive, got {self.delivery_intent!r}")
        object.__setattr__(self, "delivery_intent", di)
        # origin
        if not isinstance(self.origin, str) or type(self.origin) is not str:
            raise TypeError(f"origin must be str, got {type(self.origin).__name__}")
        o = self.origin.strip().lower()
        if o not in ("current_handoff", "recovered_continuity"):
            raise ValueError(f"origin must be current_handoff or recovered_continuity, got {self.origin!r}")
        object.__setattr__(self, "origin", o)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "context_ref": self.context_ref.to_dict(),
            "delivery_intent": self.delivery_intent,
            "origin": self.origin,
            "request": self.request.to_dict(),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextBootstrapIntent":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        # forbid authority fields
        for bad in _FORBIDDEN_AUTHORITY_FIELDS:
            if bad in data:
                raise ValueError(f"forbidden authority field in ContextBootstrapIntent: {bad!r}")
        extra = set(data.keys()) - _ALLOWED_INTENT_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ContextBootstrapIntent: {sorted(extra)}")
        for req in ("context_ref", "request", "delivery_intent", "origin"):
            if req not in data:
                raise ValueError(f"Missing required field in ContextBootstrapIntent: {req!r}")
        ctx = SemanticReference.from_value(data["context_ref"])
        req_raw = data["request"]
        if isinstance(req_raw, ContextRequest):
            req = req_raw
        elif isinstance(req_raw, Mapping):
            # ContextRequest fields: subject_ref, scope, query, limit, cursor, capability_ref, correlation_id
            req = ContextRequest(
                subject_ref=req_raw["subject_ref"],
                scope=req_raw["scope"],
                query=req_raw["query"],
                limit=req_raw.get("limit"),
                cursor=req_raw.get("cursor"),
                capability_ref=req_raw.get("capability_ref"),
                correlation_id=req_raw.get("correlation_id"),
            )
        else:
            raise TypeError(f"request must be ContextRequest or mapping, got {type(req_raw).__name__}")
        return cls(context_ref=ctx, request=req, delivery_intent=data["delivery_intent"], origin=data["origin"])


# ---------------------------------------------------------------------------
# ContextBootstrapPlan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextBootstrapPlan:
    """Immutable pure bootstrap selection plan.

    Contains bounded selected semantic context intents bound to existing
    ContextRequest contracts with delivery intent and compact governance
    metadata. No provider content, no hydration, no eager materialization.
    """

    intents: tuple[ContextBootstrapIntent, ...]
    project_ref: SemanticReference | None = None
    plan_ref: SemanticReference | None = None
    milestone_ref: SemanticReference | None = None
    work_item_ref: SemanticReference | None = None
    accepted_frontier_ref: SemanticReference | None = None
    reviewed_frontier_ref: SemanticReference | None = None
    workflow_disposition_ref: SemanticReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intents, (tuple, list)):
            raise TypeError(f"intents must be tuple or list, got {type(self.intents).__name__}")
        intents = tuple(self.intents)
        if len(intents) > M3_CONTEXT_REF_CANDIDATE_MAX:
            raise ValueError(f"intents count {len(intents)} exceeds candidate max {M3_CONTEXT_REF_CANDIDATE_MAX}")
        for idx, it in enumerate(intents):
            if not isinstance(it, ContextBootstrapIntent):
                raise TypeError(f"intents[{idx}] must be ContextBootstrapIntent, got {type(it).__name__}")
        object.__setattr__(self, "intents", intents)
        # normalize optional refs
        for label in (
            "project_ref",
            "plan_ref",
            "milestone_ref",
            "work_item_ref",
            "accepted_frontier_ref",
            "reviewed_frontier_ref",
            "workflow_disposition_ref",
        ):
            val = getattr(self, label)
            if val is not None and not isinstance(val, SemanticReference):
                if isinstance(val, Mapping) or isinstance(val, str):
                    norm = SemanticReference.from_value(val)
                    object.__setattr__(self, label, norm)
                else:
                    raise TypeError(f"{label} must be SemanticReference or None, got {type(val).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        def _ref_dict(r: SemanticReference | None) -> dict[str, Any] | None:
            return r.to_dict() if r is not None else None

        return {
            "accepted_frontier_ref": _ref_dict(self.accepted_frontier_ref),
            "intents": [it.canonical_dict() for it in self.intents],
            "milestone_ref": _ref_dict(self.milestone_ref),
            "plan_ref": _ref_dict(self.plan_ref),
            "project_ref": _ref_dict(self.project_ref),
            "reviewed_frontier_ref": _ref_dict(self.reviewed_frontier_ref),
            "work_item_ref": _ref_dict(self.work_item_ref),
            "workflow_disposition_ref": _ref_dict(self.workflow_disposition_ref),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextBootstrapPlan":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be mapping, got {type(data).__name__}")
        for bad in _FORBIDDEN_AUTHORITY_FIELDS:
            if bad in data:
                raise ValueError(f"forbidden authority field in ContextBootstrapPlan: {bad!r}")
        extra = set(data.keys()) - _ALLOWED_PLAN_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ContextBootstrapPlan: {sorted(extra)}")
        if "intents" not in data:
            raise ValueError("Missing required field in ContextBootstrapPlan: 'intents'")
        raw_intents = data["intents"]
        if not isinstance(raw_intents, (list, tuple)):
            raise TypeError(f"intents must be list or tuple, got {type(raw_intents).__name__}")
        intents: list[ContextBootstrapIntent] = []
        for idx, item in enumerate(raw_intents):
            if isinstance(item, ContextBootstrapIntent):
                intents.append(item)
            elif isinstance(item, Mapping):
                intents.append(ContextBootstrapIntent.from_dict(item))
            else:
                raise TypeError(f"intents[{idx}] must be ContextBootstrapIntent or mapping, got {type(item).__name__}")
        # helper to parse optional ref
        def _parse_opt(key: str) -> SemanticReference | None:
            v = data.get(key)
            if v is None:
                return None
            return SemanticReference.from_value(v)

        return cls(
            intents=tuple(intents),
            project_ref=_parse_opt("project_ref"),
            plan_ref=_parse_opt("plan_ref"),
            milestone_ref=_parse_opt("milestone_ref"),
            work_item_ref=_parse_opt("work_item_ref"),
            accepted_frontier_ref=_parse_opt("accepted_frontier_ref"),
            reviewed_frontier_ref=_parse_opt("reviewed_frontier_ref"),
            workflow_disposition_ref=_parse_opt("workflow_disposition_ref"),
        )

    @property
    def plan_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        return self.plan_digest

    @property
    def plan_id(self) -> str:
        return f"bootstrap-plan:{self.plan_digest}"


# ---------------------------------------------------------------------------
# Pure deterministic builder
# ---------------------------------------------------------------------------

def create_context_bootstrap_plan(
    handoff: TaskHandoff,
    recovered: WorkingTruthProjection | None = None,
    *,
    rollover_disposition: RolloverDisposition | RolloverDecision | str | None = None,
    provider_limit: int | None = None,
) -> ContextBootstrapPlan:
    """Bounded deterministic bootstrap selection + provider binding.

    Composes current TaskHandoff.context_refs and recovered
    WorkingTruthProjection.context_refs into a ContextBootstrapPlan.

    Pure, side-effect-free, no I/O, no provider fetch, no hydration.

    Parameters
    ----------
    handoff: TaskHandoff
        Current task intent (required). Its bounded_scope and subject refs
        drive ContextRequest binding.
    recovered: WorkingTruthProjection | None
        Recovered continuity (optional). May be None for current-only.
    rollover_disposition: RolloverDisposition | RolloverDecision | str | None
        Budget evidence. None / UNDETERMINED / RECOMMENDED / REQUIRED →
        all progressive. WITHIN_BUDGET → current may be eager.
    provider_limit: int | None
        Explicit bounded limit 1..100 for each ContextRequest. None means
        no limit. Bool rejected. Reuses ContextRequest validation.
    """
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if recovered is not None and not isinstance(recovered, WorkingTruthProjection):
        raise TypeError(f"recovered must be WorkingTruthProjection or None, got {type(recovered).__name__}")

    # Validate provider_limit strictly (reuse ContextRequest bounds)
    if provider_limit is not None:
        if isinstance(provider_limit, bool) or not isinstance(provider_limit, int):
            raise TypeError(f"provider_limit must be int (not bool), got {type(provider_limit).__name__}")
        if not (1 <= provider_limit <= 100):
            raise ValueError(f"provider_limit must be between 1 and 100, got {provider_limit}")
        # also let ContextRequest validate later

    # Cross-project / cross-Plan fails closed (both exist must match)
    if recovered is not None:
        if handoff.project_ref is not None and recovered.project_ref is not None:
            if not _refs_equal(handoff.project_ref, recovered.project_ref):
                raise ValueError(f"cross-project recovered context blocked: handoff {handoff.project_ref.ref!r} != recovered {recovered.project_ref.ref!r}")
        if handoff.plan_ref is not None and recovered.plan_ref is not None:
            if not _refs_equal(handoff.plan_ref, recovered.plan_ref):
                raise ValueError(f"cross-Plan recovered context blocked: handoff {handoff.plan_ref.ref!r} != recovered {recovered.plan_ref.ref!r}")

    # Collect candidate refs (bounded)
    current_raw = tuple(handoff.context_refs) if handoff.context_refs else ()
    recovered_raw = tuple(recovered.context_refs) if recovered is not None and recovered.context_refs else ()

    if len(current_raw) > 16:
        raise ValueError(f"TaskHandoff.context_refs exceeds 16: {len(current_raw)}")
    if len(recovered_raw) > 16:
        raise ValueError(f"recovered context_refs exceeds 16: {len(recovered_raw)}")

    # Same-origin conflicting digest detection (fail closed)
    if current_raw:
        _detect_same_origin_conflict(current_raw, "TaskHandoff.context_refs")
    if recovered_raw:
        _detect_same_origin_conflict(recovered_raw, "WorkingTruthProjection.context_refs")

    # Deduplicate exact and sort within each origin
    current_dedup = _deduplicate_exact(current_raw)
    recovered_dedup = _deduplicate_exact(recovered_raw)
    current_sorted = _sorted_refs(current_dedup)
    recovered_sorted = _sorted_refs(recovered_dedup)

    # Composition: current group precedes recovered, dedup exact, current wins same-ref differing digest
    # Build lookup for current refs by ref string -> set of digests (already sorted)
    # For precedence, if recovered ref string already present in current with any digest but different digest, skip
    current_ref_strings: set[str] = {r.ref for r in current_sorted}
    # Map ref -> digest for current (for exact duplicate check we use (ref,digest))
    current_keys: set[tuple[str, str | None]] = {(r.ref, r.digest) for r in current_sorted}
    # Also map ref -> digest for quick check of same ref different digest
    current_by_ref: dict[str, str | None] = {}
    for r in current_sorted:
        # if multiple entries same ref already deduplicated and conflict checked, there will be at most one per ref
        # but if there were two same ref with same digest they'd have been deduped, so one remains
        current_by_ref[r.ref] = r.digest

    composed: list[SemanticReference] = list(current_sorted)
    # To keep deterministic ordering for recovered group appended after current, we already sorted recovered
    for r in recovered_sorted:
        key = (r.ref, r.digest)
        if key in current_keys:
            # exact duplicate across origins -> dedup, skip recovered
            continue
        if r.ref in current_by_ref:
            # same ref but different digest -> current wins, skip recovered
            # need to check if digest differs
            if current_by_ref[r.ref] != r.digest:
                continue
            # if digest same but key not in current_keys? that would be exact duplicate already handled
            # but if digest same, then it's exact duplicate case already filtered, so skip anyway
            continue
        # else unique recovered ref, add
        composed.append(r)

    if len(composed) > M3_CONTEXT_REF_CANDIDATE_MAX:
        raise ValueError(f"candidate union {len(composed)} exceeds max {M3_CONTEXT_REF_CANDIDATE_MAX} — no silent truncation")

    # Derive subject and scope
    subject_ref = _derive_subject(handoff, recovered)
    scope = handoff.bounded_scope  # already validated bounded non-empty

    # Determine disposition
    disp = _normalize_disposition(rollover_disposition)
    # Missing or undetermined etc -> progressive only
    # WITHIN_BUDGET allows current eager
    is_within_budget = disp == RolloverDisposition.WITHIN_BUDGET

    intents: list[ContextBootstrapIntent] = []
    # Build mapping from ref to origin for delivery decision
    # current_sorted keys already tell origin
    current_key_set = {(r.ref, r.digest) for r in current_sorted}
    for ref in composed:
        # Determine origin provenance
        key = (ref.ref, ref.digest)
        if key in current_key_set:
            origin = "current_handoff"
        else:
            # check if ref string in current_by_ref but digest differs? then this ref would have been skipped, so not here
            # So remaining must be recovered
            origin = "recovered_continuity"
        # Delivery intent
        if is_within_budget and origin == "current_handoff":
            delivery = "eager"
        else:
            delivery = "progressive"
        # Build ContextRequest — reuse existing schema
        # query = selected SemanticReference.ref (bounded), do not inject digest
        # limit = provider_limit
        req = ContextRequest(
            subject_ref=subject_ref,
            scope=scope,
            query=ref.ref,
            limit=provider_limit,
        )
        # Ensure no ACF knobs: ContextRequest has no such fields by construction
        intent = ContextBootstrapIntent(
            context_ref=ref,
            request=req,
            delivery_intent=delivery,
            origin=origin,
        )
        intents.append(intent)

    # Deterministic ordering of intents already: current group sorted, then recovered sorted, preserved composition order
    # But we should ensure intents are in composed order which is deterministic
    # No further sorting needed (preserves group precedence)

    # Compact governance metadata
    # project_ref etc from current handoff where exists, else recovered
    def _pick_ref(
        handoff_val: SemanticReference | None,
        recovered_val: SemanticReference | None,
    ) -> SemanticReference | None:
        if handoff_val is not None:
            return handoff_val
        return recovered_val

    project_ref = _pick_ref(handoff.project_ref, recovered.project_ref if recovered else None)
    plan_ref = _pick_ref(handoff.plan_ref, recovered.plan_ref if recovered else None)
    milestone_ref = _pick_ref(handoff.milestone_ref, recovered.milestone_ref if recovered else None)
    work_item_ref = _pick_ref(handoff.work_item_ref, recovered.active_work_item_ref if recovered else None)
    accepted_frontier_ref = recovered.accepted_frontier_ref if recovered else None
    reviewed_frontier_ref = recovered.reviewed_frontier_ref if recovered else None
    workflow_disposition_ref = recovered.workflow_disposition_ref if recovered else None

    plan = ContextBootstrapPlan(
        intents=tuple(intents),
        project_ref=project_ref,
        plan_ref=plan_ref,
        milestone_ref=milestone_ref,
        work_item_ref=work_item_ref,
        accepted_frontier_ref=accepted_frontier_ref,
        reviewed_frontier_ref=reviewed_frontier_ref,
        workflow_disposition_ref=workflow_disposition_ref,
    )
    return plan


# Backwards compatibility alias
build_context_bootstrap_plan = create_context_bootstrap_plan

__all__ = [
    "W1_CONTRACT_ONLY",
    "PURE_BOOTSTRAP_SELECTION_SEPARATE_FROM_IO",
    "W1_FINAL_BOOTSTRAP_MATERIALIZATION_REQUIRED",
    "W1_PROVIDER_FETCH_REQUIRED",
    "W1_SELECTIVE_HYDRATION_REQUIRED",
    "W1_EAGER_IS_DELIVERY_INTENT_ONLY",
    "W1_FAKE_EAGER_MATERIALIZATION_ALLOWED",
    "EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED",
    "EXISTING_SEMANTIC_REFERENCE_REUSED",
    "EXISTING_BOOTSTRAP_BUNDLE_REUSED",
    "EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED",
    "TASK_HANDOFF_CONTEXT_REFS_REUSED",
    "M2_WORKING_TRUTH_CONTEXT_REFS_REUSED",
    "SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE",
    "CURRENT_TASK_SCOPE_WINS_OVER_RECOVERED_CONTEXT",
    "CROSS_PROJECT_RECOVERED_CONTEXT_FAILS_CLOSED",
    "CROSS_PLAN_RECOVERED_CONTEXT_FAILS_CLOSED",
    "CONTEXT_REF_COMPOSITION_BOUNDED",
    "CONTEXT_REF_COMPOSITION_NO_SILENT_TRUNCATION",
    "M3_CONTEXT_REF_CANDIDATE_MAX",
    "CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE",
    "CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_IS_AUTHORITY",
    "CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_PROVES_FRESHNESS",
    "SAME_ORIGIN_CONTEXT_DIGEST_CONFLICT_FAILS_CLOSED",
    "CONTEXT_REF_ORDERING_DETERMINISTIC",
    "CONTEXT_REQUEST_SCHEMA_CHANGE_REQUIRED",
    "CONTEXT_REQUEST_BINDING_CONTRACT",
    "CONTEXT_REQUEST_SUBJECT_IS_CURRENT_SCOPE_DERIVED",
    "CONTEXT_REQUEST_QUERY_IS_BOUNDED_CONTEXT_INTENT",
    "CONTEXT_REQUEST_EXISTING_LIMIT_BOUND_REUSED",
    "ACF_PRIVATE_RETRIEVAL_KNOBS_CANONICALIZED_IN_FORGE",
    "BOOTSTRAP_COMPONENT_BOUND_REUSED",
    "FULL_PLAN_BODY_BOOTSTRAP_REQUIRED",
    "FULL_GOVERNANCE_HISTORY_BOOTSTRAP_REQUIRED",
    "BOOTSTRAPPED_GOVERNANCE_METADATA_IS_AUTHORITY",
    "W1_PROVIDER_INVOCATION_COUNT",
    "W1_HYDRATION_INVOCATION_COUNT",
    "PROVIDER_FETCH_IS_HYDRATION_AUTHORITY",
    "CONTEXT_RESPONSE_IS_HYDRATION_AUTHORITY",
    "RECOVERED_CONTEXT_REF_POSSESSION_IS_HYDRATION_AUTHORITY",
    "DIGEST_IS_AUTHORITY",
    "CONTEXT_BOOTSTRAP_PLAN_IS_RETRY_PERMISSION",
    "CONTEXT_REQUEST_IS_RETRY_PERMISSION",
    "CONTEXT_REF_IS_RETRY_PERMISSION",
    "M2_RECOVERY_AUTHORITY_FIREWALL_RETAINED",
    "BOOTSTRAP_PLANNING_CANNOT_CLEAR_SEMANTIC_STOP",
    "BOOTSTRAP_PLANNING_CANNOT_CLEAR_REPLAN_REQUIRED",
    "M3_W1_AUTO_DISPATCHES_WORKER",
    "M3_AGENT_NEUTRAL",
    "MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "SECOND_PROVIDER_REGISTRY_CREATED",
    "NEW_CONTEXT_STORE_CREATED",
    "NEW_PROVIDER_CACHE_CREATED",
    "NEW_HYDRATION_STORE_CREATED",
    "M3_BACKGROUND_CONTEXT_RUNTIME_CREATED",
    "NEW_CONTEXT_FRESHNESS_ENGINE_CREATED",
    "NEW_BOOTSTRAP_ONTOLOGY_CREATED",
    "NEW_CONTEXT_RUNTIME_CREATED",
    "M3_NEW_PRODUCTION_MODULE_COUNT_AFTER_W1",
    "M3_NEW_PRODUCTION_MODULE_COUNT_PREFERENCE_MAX",
    "M2_PREDECESSOR_CONTRACT_REVISION_REQUIRED",
    "SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "AGGREGATOR_CHANGE_REQUIRED_FOR_W1",
    "W2_SCOPE_PULLED_FORWARD_BY_W1",
    "ARBITRARY_CONTEXT_BOOTSTRAP_METADATA_ALLOWED",
    "CONTEXT_BOOTSTRAP_PLAN_DIGEST_IS_AUTHORITY",
    "CONTEXT_BOOTSTRAP_INTENT_IS_AUTHORITY",
    "CONTEXT_BOOTSTRAP_PLAN_CONTRACT",
    "NEW_PRODUCTION_MODULE_COUNT",
    "M3_ADDED_CONTEXT_WHEN_BUDGET_UNDETERMINED",
    "MISSING_CONTEXT_BUDGET_EVIDENCE_ALLOWS_UNBOUNDED_EAGER_HYDRATION",
    "ROLLOVER_DISPOSITION_IS_CONTEXT_AUTHORITY",
    "ContextBootstrapIntent",
    "ContextBootstrapPlan",
    "create_context_bootstrap_plan",
    "build_context_bootstrap_plan",
]
