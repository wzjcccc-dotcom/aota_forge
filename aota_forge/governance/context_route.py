"""AF #57 M2/W2 — bounded typed Governance Context Route.

The Context Route is one derived navigation projection over accepted W1
Governance Cards and trusted binding facts:

    trusted bootstrap
            v
    Governance Cards
            v
    Context Route
            v
    small active semantic working set
            v
    existing read/search/hydration/Skills on demand

It tells the model:

* what compact context is already available;
* what deeper refs exist;
* when a deeper ref may be useful.

It never decides semantic retrieval intent, Work Item choice, mutation
choice, authority or approval:

    CONTEXT_ROUTE_IS_AUTHORITY=no
    CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT=no
    CONTEXT_ROUTE_DETERMINISTIC=yes
    ROUTE_CONTAINS_HOST_PATHS=no
    ROUTE_REFS_ARE_BOUNDED=yes
    SECOND_RETRIEVAL_FRAMEWORK=no
    NEW_GOVERNANCE_SEARCH_OPERATION=no
    NEW_GOVERNANCE_OPEN_OPERATION=no

The same authoritative/projection inputs always yield byte-identical route
output (deterministic sort + canonical digest).  The route works for either
explicit Plan authority binding (Governance 1.x ``github_issue`` or
Governance 2.0 ``local_governance``); no local-governance Plan is required for
a GitHub-bound Plan.

The route is explicitly not an Agent Tool and adds no public operation.  It is
exposed as compact model-visible context by the existing ``role.bootstrap``
composition only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.cards import (
    CARD_KINDS,
    GovernanceCard,
    GovernanceProjectionBundle,
    MAX_CARD_REF_LENGTH,
    require_logical_ref,
)
from aota_forge.governance.working_set import (
    RETRIEVAL_INTENT_FULL,
    RETRIEVAL_INTENT_HYDRATE,
    RETRIEVAL_INTENT_QUERY,
    RETRIEVAL_INTENT_SECTION,
    RETRIEVAL_INTENTS,
    WorkingSet,
)

# ---------------------------------------------------------------------------
# Truthful gate flags (tests / review)
# ---------------------------------------------------------------------------

CONTEXT_ROUTE_IS_AUTHORITY: bool = False
CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT: bool = False
CONTEXT_ROUTE_DETERMINISTIC: bool = True
CONTEXT_ROUTE_IS_DERIVED_PROJECTION: bool = True
ROUTE_CONTAINS_HOST_PATHS: bool = False
ROUTE_REFS_ARE_BOUNDED: bool = True
SECOND_RETRIEVAL_FRAMEWORK: bool = False
NEW_GOVERNANCE_SEARCH_OPERATION: bool = False
NEW_GOVERNANCE_OPEN_OPERATION: bool = False
FULL_PLAN_EAGER_HYDRATION: bool = False
FULL_AGENTS_EAGER_LOAD: bool = False

MAX_ROUTE_ENTRIES = 64
MAX_ROUTE_USE_WHEN_LENGTH = 256
MAX_ROUTE_SCOPE_LENGTH = 256

AUTHORITY_SOURCE_KINDS = frozenset({"github_issue", "local_governance"})

ROUTE_ENTRY_PROJECT_CARD = "project_card"
ROUTE_ENTRY_PLAN_CARD = "plan_card"
ROUTE_ENTRY_MILESTONE_CARD = "milestone_card"
ROUTE_ENTRY_ARCHITECTURE_CARD = "architecture_card"
ROUTE_ENTRY_PROGRESS_CARD = "progress_card"
ROUTE_ENTRY_PLAN_AUTHORITY = "plan_authority"
ROUTE_ENTRY_BLOCKER = "blocker"
ROUTE_ENTRY_HUMAN_BRAKE = "human_brake"
ROUTE_ENTRY_AUTHORIZED_ROOT = "authorized_root"
ROUTE_ENTRY_AGENTS_POLICY = "agents_policy"
ROUTE_ENTRY_SKILL = "skill"
ROUTE_ENTRY_EVIDENCE = "evidence"
ROUTE_ENTRY_RESULT = "result"
ROUTE_ENTRY_DOCUMENT = "document"
ROUTE_ENTRY_OBSERVATION = "observation"
ROUTE_ENTRY_CANDIDATE_SET = "candidate_set"

ROUTE_ENTRY_KINDS = frozenset(
    {
        ROUTE_ENTRY_PROJECT_CARD,
        ROUTE_ENTRY_PLAN_CARD,
        ROUTE_ENTRY_MILESTONE_CARD,
        ROUTE_ENTRY_ARCHITECTURE_CARD,
        ROUTE_ENTRY_PROGRESS_CARD,
        ROUTE_ENTRY_PLAN_AUTHORITY,
        ROUTE_ENTRY_BLOCKER,
        ROUTE_ENTRY_HUMAN_BRAKE,
        ROUTE_ENTRY_AUTHORIZED_ROOT,
        ROUTE_ENTRY_AGENTS_POLICY,
        ROUTE_ENTRY_SKILL,
        ROUTE_ENTRY_EVIDENCE,
        ROUTE_ENTRY_RESULT,
        ROUTE_ENTRY_DOCUMENT,
        ROUTE_ENTRY_OBSERVATION,
        ROUTE_ENTRY_CANDIDATE_SET,
    }
)

_ROUTE_KIND_ORDER = (
    ROUTE_ENTRY_PROJECT_CARD,
    ROUTE_ENTRY_PLAN_CARD,
    ROUTE_ENTRY_MILESTONE_CARD,
    ROUTE_ENTRY_ARCHITECTURE_CARD,
    ROUTE_ENTRY_PROGRESS_CARD,
    ROUTE_ENTRY_PLAN_AUTHORITY,
    ROUTE_ENTRY_BLOCKER,
    ROUTE_ENTRY_HUMAN_BRAKE,
    ROUTE_ENTRY_AUTHORIZED_ROOT,
    ROUTE_ENTRY_AGENTS_POLICY,
    ROUTE_ENTRY_SKILL,
    ROUTE_ENTRY_EVIDENCE,
    ROUTE_ENTRY_RESULT,
    ROUTE_ENTRY_DOCUMENT,
    ROUTE_ENTRY_OBSERVATION,
    ROUTE_ENTRY_CANDIDATE_SET,
)
_KIND_ORDER_INDEX = {kind: index for index, kind in enumerate(_ROUTE_KIND_ORDER)}

CARD_ROUTE_KINDS = frozenset(
    {
        ROUTE_ENTRY_PROJECT_CARD,
        ROUTE_ENTRY_PLAN_CARD,
        ROUTE_ENTRY_MILESTONE_CARD,
        ROUTE_ENTRY_ARCHITECTURE_CARD,
        ROUTE_ENTRY_PROGRESS_CARD,
    }
)

_ROUTE_SUPPORTED_INTENTS: dict[str, tuple[str, ...]] = {
    ROUTE_ENTRY_PROJECT_CARD: (),
    ROUTE_ENTRY_PLAN_CARD: (),
    ROUTE_ENTRY_MILESTONE_CARD: (),
    ROUTE_ENTRY_ARCHITECTURE_CARD: (),
    ROUTE_ENTRY_PROGRESS_CARD: (),
    ROUTE_ENTRY_PLAN_AUTHORITY: (RETRIEVAL_INTENT_FULL, RETRIEVAL_INTENT_SECTION, RETRIEVAL_INTENT_QUERY),
    ROUTE_ENTRY_BLOCKER: (),
    ROUTE_ENTRY_HUMAN_BRAKE: (),
    ROUTE_ENTRY_AUTHORIZED_ROOT: (),
    ROUTE_ENTRY_AGENTS_POLICY: (RETRIEVAL_INTENT_FULL, RETRIEVAL_INTENT_SECTION, RETRIEVAL_INTENT_QUERY),
    ROUTE_ENTRY_SKILL: (),
    ROUTE_ENTRY_EVIDENCE: (
        RETRIEVAL_INTENT_FULL,
        RETRIEVAL_INTENT_SECTION,
        RETRIEVAL_INTENT_QUERY,
        RETRIEVAL_INTENT_HYDRATE,
    ),
    ROUTE_ENTRY_RESULT: (RETRIEVAL_INTENT_SECTION, RETRIEVAL_INTENT_QUERY, RETRIEVAL_INTENT_HYDRATE),
    ROUTE_ENTRY_DOCUMENT: (
        RETRIEVAL_INTENT_FULL,
        RETRIEVAL_INTENT_SECTION,
        RETRIEVAL_INTENT_QUERY,
        RETRIEVAL_INTENT_HYDRATE,
    ),
    ROUTE_ENTRY_OBSERVATION: (RETRIEVAL_INTENT_HYDRATE,),
    ROUTE_ENTRY_CANDIDATE_SET: (),
}

WORKING_SET_KIND_TO_ROUTE_KIND = {
    "card": "",  # resolved through the entry semantic role (card kind)
    "skill": ROUTE_ENTRY_SKILL,
    "evidence": ROUTE_ENTRY_EVIDENCE,
    "result": ROUTE_ENTRY_RESULT,
    "document": ROUTE_ENTRY_DOCUMENT,
    "observation": ROUTE_ENTRY_OBSERVATION,
    "candidate": ROUTE_ENTRY_CANDIDATE_SET,
}


class ContextRouteError(ValueError):
    """A context-route input/value is structurally invalid (fail closed)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _require_text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise ContextRouteError("INVALID_FIELD", f"{label} must be a string")
    text = value.strip()
    if not text:
        raise ContextRouteError("INVALID_FIELD", f"{label} must be a non-empty string")
    if len(text) > max_length:
        raise ContextRouteError("INVALID_FIELD", f"{label} exceeds maximum {max_length}")
    if "\x00" in text:
        raise ContextRouteError("INVALID_FIELD", f"{label} must not contain NUL")
    return text


def _optional_text(value: Any, label: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, label, max_length=max_length)


def _ref(value: Any, label: str, *, max_length: int = MAX_CARD_REF_LENGTH) -> str:
    try:
        return require_logical_ref(value, label, max_length=max_length)
    except Exception as exc:  # GovernanceCardError shares the same semantics
        code = getattr(exc, "code", "HOST_PATH_REJECTED")
        message = getattr(exc, "message", str(exc))
        raise ContextRouteError(code, message) from exc


def _optional_digest(value: Any, label: str) -> str | None:
    if value is None:
        return None
    text = _require_text(value, label, max_length=64)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ContextRouteError("INVALID_FIELD", f"{label} must be 64 lowercase hex")
    return text


def _clean_tuple(value: Any, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise ContextRouteError("INVALID_FIELD", f"{label} must be a tuple/list")
    return tuple(value)


# ---------------------------------------------------------------------------
# Route entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRouteEntry:
    """One bounded navigation ref inside the Context Route.

    Entries are pointers, never authority.  ``supported_intents`` lists which
    LLM-selected retrieval intents are *possible* for the ref (a hint for
    reasoning), never which one is chosen.
    """

    kind: str
    ref: str
    card_kind: str | None = None
    projection_id: str | None = None
    source_kind: str | None = None
    revision: str | None = None
    digest: str | None = None
    complete: bool = True
    missing_facts: tuple[str, ...] = ()
    use_when: str | None = None
    supported_intents: tuple[str, ...] = ()
    scope: str | None = None
    read_path: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ROUTE_ENTRY_KINDS:
            raise ContextRouteError("UNKNOWN_ROUTE_ENTRY_KIND", f"kind must be one of {sorted(ROUTE_ENTRY_KINDS)}")
        object.__setattr__(self, "ref", _ref(self.ref, "route_entry.ref"))
        if self.card_kind is not None and self.card_kind not in CARD_KINDS:
            raise ContextRouteError("INVALID_CARD_KIND", f"card_kind must be one of {sorted(CARD_KINDS)}")
        object.__setattr__(
            self,
            "projection_id",
            _optional_text(self.projection_id, "route_entry.projection_id", max_length=64),
        )
        if self.source_kind is not None and self.source_kind not in AUTHORITY_SOURCE_KINDS:
            raise ContextRouteError(
                "UNKNOWN_SOURCE_KIND",
                f"source_kind must be one of {sorted(AUTHORITY_SOURCE_KINDS)}",
            )
        object.__setattr__(self, "revision", _optional_text(self.revision, "route_entry.revision", max_length=128))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "route_entry.digest"))
        if type(self.complete) is not bool:
            raise ContextRouteError("INVALID_FIELD", "route_entry.complete must be a bool")
        missing = tuple(self.missing_facts or ())
        if self.complete and missing:
            raise ContextRouteError("COMPLETENESS_CONTRADICTION", "a complete route entry must not carry missing facts")
        if not self.complete and not missing:
            raise ContextRouteError("COMPLETENESS_NOT_EXPLICIT", "an incomplete route entry must carry missing facts")
        object.__setattr__(self, "missing_facts", missing)
        object.__setattr__(
            self,
            "use_when",
            _optional_text(self.use_when, "route_entry.use_when", max_length=MAX_ROUTE_USE_WHEN_LENGTH),
        )
        intents = tuple(self.supported_intents or ())
        for intent in intents:
            if intent not in RETRIEVAL_INTENTS:
                raise ContextRouteError("UNKNOWN_RETRIEVAL_INTENT", f"supported intent {intent!r} is not a retrieval intent")
        object.__setattr__(self, "supported_intents", intents)
        object.__setattr__(self, "scope", _optional_text(self.scope, "route_entry.scope", max_length=MAX_ROUTE_SCOPE_LENGTH))
        object.__setattr__(self, "read_path", _ref(self.read_path, "route_entry.read_path") if self.read_path is not None else None)
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise ContextRouteError("INVALID_FIELD", "route_entry.size_bytes must be a non-negative int or None")

    def is_authority(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "ref": self.ref,
            "complete": self.complete,
            "IS_AUTHORITY": False,
        }
        for key, value in (
            ("card_kind", self.card_kind),
            ("projection_id", self.projection_id),
            ("source_kind", self.source_kind),
            ("revision", self.revision),
            ("digest", self.digest),
            ("missing_facts", list(self.missing_facts) or None),
            ("use_when", self.use_when),
            ("supported_intents", list(self.supported_intents) or None),
            ("scope", self.scope),
            ("read_path", self.read_path),
            ("size_bytes", self.size_bytes),
        ):
            if value is not None:
                payload[key] = value
        return payload


# ---------------------------------------------------------------------------
# Bounded route inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutePolicyRef:
    """One applicable bounded policy ref (AGENTS projection; refs not bodies)."""

    ref: str
    scope: str | None = None
    digest: str | None = None
    read_path: str | None = None
    use_when: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "policy_ref.ref"))
        object.__setattr__(self, "scope", _optional_text(self.scope, "policy_ref.scope", max_length=MAX_ROUTE_SCOPE_LENGTH))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "policy_ref.digest"))
        object.__setattr__(self, "read_path", _ref(self.read_path, "policy_ref.read_path") if self.read_path is not None else None)
        object.__setattr__(
            self,
            "use_when",
            _optional_text(self.use_when, "policy_ref.use_when", max_length=MAX_ROUTE_USE_WHEN_LENGTH),
        )


@dataclass(frozen=True)
class RouteSkillRef:
    """One progressive Skill ref with its use_when routing hint."""

    ref: str
    use_when: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "skill_ref.ref"))
        object.__setattr__(
            self,
            "use_when",
            _optional_text(self.use_when, "skill_ref.use_when", max_length=MAX_ROUTE_USE_WHEN_LENGTH),
        )


@dataclass(frozen=True)
class RouteBoundedRef:
    """One bounded evidence/result/document ref with observed identity facts."""

    ref: str
    revision: str | None = None
    digest: str | None = None
    complete: bool = True
    missing_facts: tuple[str, ...] = ()
    use_when: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "bounded_ref.ref"))
        object.__setattr__(self, "revision", _optional_text(self.revision, "bounded_ref.revision", max_length=128))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "bounded_ref.digest"))
        if type(self.complete) is not bool:
            raise ContextRouteError("INVALID_FIELD", "bounded_ref.complete must be a bool")
        missing = tuple(self.missing_facts or ())
        if self.complete and missing:
            raise ContextRouteError("COMPLETENESS_CONTRADICTION", "a complete ref must not carry missing facts")
        if not self.complete and not missing:
            raise ContextRouteError("COMPLETENESS_NOT_EXPLICIT", "an incomplete ref must carry missing facts")
        object.__setattr__(self, "missing_facts", missing)
        object.__setattr__(
            self,
            "use_when",
            _optional_text(self.use_when, "bounded_ref.use_when", max_length=MAX_ROUTE_USE_WHEN_LENGTH),
        )
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise ContextRouteError("INVALID_FIELD", "bounded_ref.size_bytes must be a non-negative int or None")


@dataclass(frozen=True)
class ContextRouteInput:
    """Storage-neutral inputs for one deterministic Context Route rebuild.

    ``bundle`` carries the accepted W1 Governance Cards; ``plan_id`` /
    ``authority_*`` carry the trusted plan binding facts.  Missing sources stay
    absent and are reported as explicit missing facts — never fabricated.
    """

    project_id: str
    plan_id: str | None = None
    authority_source_kind: str | None = None
    authority_ref: str | None = None
    authority_revision: str | None = None
    authority_digest: str | None = None
    bundle: GovernanceProjectionBundle | None = None
    human_brake_ref: str | None = None
    blocker_refs: tuple[str, ...] = ()
    authorized_root_refs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    agents_policy_refs: tuple[RoutePolicyRef, ...] = ()
    skill_refs: tuple[RouteSkillRef, ...] = ()
    evidence_refs: tuple[RouteBoundedRef, ...] = ()
    result_refs: tuple[RouteBoundedRef, ...] = ()
    working_set: WorkingSet | None = None

    def __post_init__(self) -> None:
        project_id = _require_text(self.project_id, "project_id", max_length=96)
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ContextRouteError("INVALID_PROJECT_ID", "project_id must match the canonical project identity grammar")
        object.__setattr__(self, "project_id", project_id)
        if self.plan_id is not None and not is_plan_id(self.plan_id):
            raise ContextRouteError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        if self.authority_source_kind is not None and self.authority_source_kind not in AUTHORITY_SOURCE_KINDS:
            raise ContextRouteError(
                "UNKNOWN_SOURCE_KIND",
                f"authority_source_kind must be one of {sorted(AUTHORITY_SOURCE_KINDS)}",
            )
        object.__setattr__(self, "authority_ref", _ref(self.authority_ref, "authority_ref") if self.authority_ref is not None else None)
        object.__setattr__(
            self,
            "authority_revision",
            _optional_text(self.authority_revision, "authority_revision", max_length=128),
        )
        object.__setattr__(self, "authority_digest", _optional_digest(self.authority_digest, "authority_digest"))
        if self.bundle is not None and not isinstance(self.bundle, GovernanceProjectionBundle):
            raise ContextRouteError("INVALID_FIELD", "bundle must be a GovernanceProjectionBundle or None")
        if self.bundle is not None and self.bundle.project.project_id != project_id:
            raise ContextRouteError("PROJECT_MISMATCH", "bundle belongs to another project")
        object.__setattr__(
            self,
            "human_brake_ref",
            _ref(self.human_brake_ref, "human_brake_ref") if self.human_brake_ref is not None else None,
        )
        blocker_refs = tuple(_ref(item, "blocker_ref") for item in _clean_tuple(self.blocker_refs, "blocker_refs"))
        object.__setattr__(self, "blocker_refs", blocker_refs)
        roots = _clean_tuple(self.authorized_root_refs, "authorized_root_refs")
        clean_roots: list[tuple[str, tuple[str, ...]]] = []
        for index, item in enumerate(roots):
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ContextRouteError("INVALID_FIELD", f"authorized_root_refs[{index}] must be a [root_ref, capabilities] pair")
            root_ref = _ref(item[0], f"authorized_root_refs[{index}].root_ref", max_length=64)
            capabilities = _clean_tuple(item[1], f"authorized_root_refs[{index}].capabilities")
            clean_caps = tuple(
                _require_text(cap, f"authorized_root_refs[{index}].capability", max_length=32) for cap in capabilities
            )
            clean_roots.append((root_ref, clean_caps))
        object.__setattr__(self, "authorized_root_refs", tuple(clean_roots))
        for label in ("agents_policy_refs", "skill_refs", "evidence_refs", "result_refs"):
            values = _clean_tuple(getattr(self, label), label)
            expected = {
                "agents_policy_refs": RoutePolicyRef,
                "skill_refs": RouteSkillRef,
                "evidence_refs": RouteBoundedRef,
                "result_refs": RouteBoundedRef,
            }[label]
            for index, item in enumerate(values):
                if not isinstance(item, expected):
                    raise ContextRouteError("INVALID_FIELD", f"{label}[{index}] must be {expected.__name__}")
            object.__setattr__(self, label, values)
        if self.working_set is not None and not isinstance(self.working_set, WorkingSet):
            raise ContextRouteError("INVALID_FIELD", "working_set must be a WorkingSet or None")


# ---------------------------------------------------------------------------
# Context route
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRoute:
    """Deterministic derived navigation projection (never authority)."""

    project_id: str
    plan_id: str | None
    authority_source_kind: str | None
    entries: tuple[ContextRouteEntry, ...]
    cards_present: bool
    missing_facts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        project_id = _require_text(self.project_id, "project_id", max_length=96)
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ContextRouteError("INVALID_PROJECT_ID", "project_id must match the canonical project identity grammar")
        object.__setattr__(self, "project_id", project_id)
        if self.plan_id is not None and not is_plan_id(self.plan_id):
            raise ContextRouteError("INVALID_PLAN_ID", "plan_id must be one canonical internal Plan ID")
        if self.authority_source_kind is not None and self.authority_source_kind not in AUTHORITY_SOURCE_KINDS:
            raise ContextRouteError("UNKNOWN_SOURCE_KIND", "authority_source_kind must be a known source kind")
        entries = tuple(self.entries or ())
        for index, entry in enumerate(entries):
            if not isinstance(entry, ContextRouteEntry):
                raise ContextRouteError("INVALID_FIELD", f"entries[{index}] must be a ContextRouteEntry")
        if len(entries) > MAX_ROUTE_ENTRIES:
            raise ContextRouteError("ROUTE_NOT_BOUNDED", f"context route exceeds {MAX_ROUTE_ENTRIES} entries")
        object.__setattr__(self, "entries", entries)
        if type(self.cards_present) is not bool:
            raise ContextRouteError("INVALID_FIELD", "cards_present must be a bool")
        object.__setattr__(self, "missing_facts", tuple(sorted(dict.fromkeys(self.missing_facts or ()))))
        object.__setattr__(self, "warnings", tuple(sorted(dict.fromkeys(self.warnings or ()))))

    def is_authority(self) -> bool:
        return False

    def entries_for_kind(self, kind: str) -> tuple[ContextRouteEntry, ...]:
        if kind not in ROUTE_ENTRY_KINDS:
            raise ContextRouteError("UNKNOWN_ROUTE_ENTRY_KIND", f"kind must be one of {sorted(ROUTE_ENTRY_KINDS)}")
        return tuple(entry for entry in self.entries if entry.kind == kind)

    def entry_for_ref(self, ref: str) -> ContextRouteEntry | None:
        target = _ref(ref, "entry_for_ref.ref")
        for entry in self.entries:
            if entry.ref == target:
                return entry
        return None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "authority_source_kind": self.authority_source_kind,
            "cards_present": self.cards_present,
            "entries": [entry.to_dict() for entry in self.entries],
            "missing_facts": list(self.missing_facts),
            "warnings": list(self.warnings),
        }

    def projection_id(self) -> str:
        digest = hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()
        return f"groute-{digest[:32]}"

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["projection_id"] = self.projection_id()
        payload["is_authority"] = False
        payload["CONTEXT_ROUTE_IS_AUTHORITY"] = False
        payload["CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT"] = False
        return payload


def _card_entry(
    *,
    card: GovernanceCard,
    kind: str,
    use_when: str,
) -> ContextRouteEntry:
    revision: str | None = None
    digest: str | None = None
    for source_ref in card.source_refs:
        if source_ref.revision is not None:
            revision = source_ref.revision
        if source_ref.digest is not None:
            digest = source_ref.digest
        if revision is not None or digest is not None:
            break
    return ContextRouteEntry(
        kind=kind,
        ref=f"card:{card.card_kind}:{card.projection_id()}",
        card_kind=card.card_kind,
        projection_id=card.projection_id(),
        revision=revision,
        digest=digest,
        complete=card.complete,
        missing_facts=card.missing_facts,
        use_when=use_when,
        supported_intents=_ROUTE_SUPPORTED_INTENTS[kind],
    )


def _route_kind_for_working_set_entry(entry_kind: str, semantic_role: str | None) -> str:
    if entry_kind == "card":
        if semantic_role in CARD_KINDS:
            return semantic_role
        return ROUTE_ENTRY_OBSERVATION
    route_kind = WORKING_SET_KIND_TO_ROUTE_KIND[entry_kind]
    if not route_kind:
        raise ContextRouteError("UNKNOWN_ROUTE_ENTRY_KIND", f"working-set kind {entry_kind!r} has no route kind")
    return route_kind


def build_context_route(source: ContextRouteInput) -> ContextRoute:
    """Build the deterministic derived Context Route for one trusted input."""
    if not isinstance(source, ContextRouteInput):
        raise ContextRouteError("INVALID_FIELD", "source must be a ContextRouteInput")

    entries: list[ContextRouteEntry] = []
    missing: list[str] = []
    warnings: list[str] = []

    bundle = source.bundle
    if bundle is None:
        missing.append("governance_cards")
    else:
        entries.append(
            _card_entry(
                card=bundle.project,
                kind=ROUTE_ENTRY_PROJECT_CARD,
                use_when="project identity, trusted Plan inventory and authorized governance roots",
            )
        )
        if not bundle.plans:
            missing.append("plan_card")
        for plan_card in bundle.plans:
            is_current = source.plan_id is not None and plan_card.plan_id == source.plan_id
            entries.append(
                _card_entry(
                    card=plan_card,
                    kind=ROUTE_ENTRY_PLAN_CARD,
                    use_when=(
                        "current Plan authority, lifecycle and current Milestone"
                        if is_current
                        else "other Plan in this project; navigate only when the task needs it"
                    ),
                )
            )
        if not bundle.milestones:
            missing.append("current_milestone_card")
        for milestone_card in bundle.milestones:
            entries.append(
                _card_entry(
                    card=milestone_card,
                    kind=ROUTE_ENTRY_MILESTONE_CARD,
                    use_when=(
                        "current Milestone contract: objective, work items, dependencies"
                        if milestone_card.is_current
                        else "Milestone contract"
                    ),
                )
            )
        if bundle.architecture is None:
            missing.append("architecture_card")
        else:
            entries.append(
                _card_entry(
                    card=bundle.architecture,
                    kind=ROUTE_ENTRY_ARCHITECTURE_CARD,
                    use_when="accepted Architecture baseline and Plan delta reference",
                )
            )
        progress = bundle.progress
        if progress is None:
            missing.append("progress_card")
        else:
            entries.append(
                _card_entry(
                    card=progress,
                    kind=ROUTE_ENTRY_PROGRESS_CARD,
                    use_when="aggregate progress, blockers and Human Brake state",
                )
            )
            if not source.blocker_refs and progress.open_blockers:
                source_blocker_ref = f"progress:{source.project_id}#current-blocker"
                entries.append(
                    ContextRouteEntry(
                        kind=ROUTE_ENTRY_BLOCKER,
                        ref=source_blocker_ref,
                        use_when="current blocker; address or report it before advancing",
                    )
                )
            if source.human_brake_ref is None and progress.human_brake_active:
                entries.append(
                    ContextRouteEntry(
                        kind=ROUTE_ENTRY_HUMAN_BRAKE,
                        ref=f"progress:{source.project_id}#human-brake",
                        use_when="Human Brake is active; stop and report rather than bypass",
                    )
                )

    if source.authority_ref is None:
        missing.append("plan_authority_ref")
    else:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_PLAN_AUTHORITY,
                ref=source.authority_ref,
                source_kind=source.authority_source_kind,
                revision=source.authority_revision,
                digest=source.authority_digest,
                complete=source.authority_digest is not None,
                missing_facts=() if source.authority_digest is not None else ("authority_digest",),
                use_when=(
                    "exact bounded read/hydrate of the authoritative Plan when deeper detail is needed"
                ),
                supported_intents=_ROUTE_SUPPORTED_INTENTS[ROUTE_ENTRY_PLAN_AUTHORITY],
            )
        )

    for blocker_ref in source.blocker_refs:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_BLOCKER,
                ref=blocker_ref,
                use_when="current blocker; address or report it before advancing",
            )
        )
    if source.human_brake_ref is not None:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_HUMAN_BRAKE,
                ref=source.human_brake_ref,
                use_when="Human Brake state; stop and report when active",
            )
        )

    for root_ref, capabilities in source.authorized_root_refs:
        capability_text = "/".join(capabilities) if capabilities else "bounded"
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_AUTHORIZED_ROOT,
                ref=f"root:{root_ref}",
                use_when=f"authorized {capability_text} scope for this session (root_ref, not a path)",
            )
        )

    for policy_ref in source.agents_policy_refs:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_AGENTS_POLICY,
                ref=policy_ref.ref,
                digest=policy_ref.digest,
                scope=policy_ref.scope,
                read_path=policy_ref.read_path,
                use_when=policy_ref.use_when or "applicable AGENTS policy for this scope; read the policy before relying on it",
                supported_intents=_ROUTE_SUPPORTED_INTENTS[ROUTE_ENTRY_AGENTS_POLICY],
            )
        )

    for skill_ref in source.skill_refs:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_SKILL,
                ref=skill_ref.ref,
                use_when=skill_ref.use_when or "progressive Skill procedure; open on demand",
            )
        )

    for bounded_ref in source.evidence_refs:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_EVIDENCE,
                ref=bounded_ref.ref,
                revision=bounded_ref.revision,
                digest=bounded_ref.digest,
                complete=bounded_ref.complete,
                missing_facts=bounded_ref.missing_facts,
                use_when=bounded_ref.use_when or "bounded evidence when the current claim needs it",
                supported_intents=_ROUTE_SUPPORTED_INTENTS[ROUTE_ENTRY_EVIDENCE],
                size_bytes=bounded_ref.size_bytes,
            )
        )
    for bounded_ref in source.result_refs:
        entries.append(
            ContextRouteEntry(
                kind=ROUTE_ENTRY_RESULT,
                ref=bounded_ref.ref,
                revision=bounded_ref.revision,
                digest=bounded_ref.digest,
                complete=bounded_ref.complete,
                missing_facts=bounded_ref.missing_facts,
                use_when=bounded_ref.use_when or "prior result; hydrate only through the exact attached claims",
                supported_intents=_ROUTE_SUPPORTED_INTENTS[ROUTE_ENTRY_RESULT],
                size_bytes=bounded_ref.size_bytes,
            )
        )

    if source.working_set is not None:
        for entry in source.working_set.entries:
            route_kind = _route_kind_for_working_set_entry(entry.kind, entry.semantic_role)
            entries.append(
                ContextRouteEntry(
                    kind=route_kind,
                    ref=entry.ref,
                    card_kind=entry.semantic_role if route_kind in CARD_ROUTE_KINDS else None,
                    projection_id=entry.card_projection_id,
                    revision=entry.revision,
                    digest=entry.digest,
                    complete=entry.complete,
                    missing_facts=entry.missing_facts,
                    use_when=entry.use_when or "already-materialized observation; reuse while ref/digest is unchanged",
                    supported_intents=_ROUTE_SUPPORTED_INTENTS[route_kind],
                    size_bytes=entry.size_bytes,
                )
            )

    deduped: dict[tuple[str, str], ContextRouteEntry] = {}
    for entry in entries:
        key = (entry.kind, entry.ref)
        if key not in deduped:
            deduped[key] = entry
    ordered = sorted(deduped.values(), key=lambda entry: (_KIND_ORDER_INDEX[entry.kind], entry.ref))
    if len(ordered) > MAX_ROUTE_ENTRIES:
        ordered = ordered[:MAX_ROUTE_ENTRIES]
        warnings.append("route_entries_bounded")

    return ContextRoute(
        project_id=source.project_id,
        plan_id=source.plan_id,
        authority_source_kind=source.authority_source_kind,
        entries=tuple(ordered),
        cards_present=bundle is not None,
        missing_facts=tuple(missing),
        warnings=tuple(warnings),
    )


def build_bootstrap_governance_context(
    *,
    project_id: str,
    plan_id: str | None = None,
    authority_source_kind: str | None = None,
    authority_ref: str | None = None,
    authority_revision: str | None = None,
    authority_digest: str | None = None,
    bundle: GovernanceProjectionBundle | None = None,
    working_set: WorkingSet | None = None,
    authorized_root_refs: Sequence[tuple[str, Sequence[str]]] = (),
    agents_policy_refs: Sequence[RoutePolicyRef] = (),
    skill_refs: Sequence[RouteSkillRef] = (),
    blocker_refs: Sequence[str] = (),
    human_brake_ref: str | None = None,
    evidence_refs: Sequence[RouteBoundedRef] = (),
    result_refs: Sequence[RouteBoundedRef] = (),
) -> dict[str, Any]:
    """Compose the compact trusted Governance-context payload for bootstrap.

    The trusted runtime/composition builds this payload from already-authorized
    inputs (accepted W1 Cards, trusted binding facts, progressive Skill refs,
    applicable AGENTS policy refs, authorized root refs) and exposes it through
    the existing ``role.bootstrap`` result.  It is a derived projection:

        FULL_PLAN_EAGER_HYDRATION=no
        FULL_AGENTS_EAGER_LOAD=no
        GOVERNANCE_CONTEXT_IS_AUTHORITY=no

    It adds no public operation; ``role.bootstrap`` embeds the payload as
    compact model-visible context and validates its non-authority markers.
    """
    route = build_context_route(
        ContextRouteInput(
            project_id=project_id,
            plan_id=plan_id,
            authority_source_kind=authority_source_kind,
            authority_ref=authority_ref,
            authority_revision=authority_revision,
            authority_digest=authority_digest,
            bundle=bundle,
            working_set=working_set,
            authorized_root_refs=tuple(authorized_root_refs or ()),
            agents_policy_refs=tuple(agents_policy_refs or ()),
            skill_refs=tuple(skill_refs or ()),
            blocker_refs=tuple(blocker_refs or ()),
            human_brake_ref=human_brake_ref,
            evidence_refs=tuple(evidence_refs or ()),
            result_refs=tuple(result_refs or ()),
        )
    )
    return {
        "CONTEXT_ROUTE": route.to_dict(),
        "CARDS": bundle.to_dict() if bundle is not None else None,
        "CARDS_PRESENT": bundle is not None,
        "WORKING_SET": working_set.to_dict() if working_set is not None else None,
        "IS_AUTHORITY": False,
        "GOVERNANCE_CONTEXT_IS_AUTHORITY": False,
        "CARD_FIRST": True,
        "FULL_PLAN_EAGER_HYDRATION": False,
        "FULL_AGENTS_EAGER_LOAD": False,
        "CONTEXT_WORKING_SET_IS_EPHEMERAL": True,
        "CONTEXT_ROUTE_DETERMINISTIC": True,
        "LLM_OWNS_RETRIEVAL_INTENT": True,
    }


__all__ = [
    "AUTHORITY_SOURCE_KINDS",
    "CARD_ROUTE_KINDS",
    "CONTEXT_ROUTE_DETERMINISTIC",
    "CONTEXT_ROUTE_IS_AUTHORITY",
    "CONTEXT_ROUTE_IS_DERIVED_PROJECTION",
    "CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT",
    "FULL_AGENTS_EAGER_LOAD",
    "FULL_PLAN_EAGER_HYDRATION",
    "MAX_ROUTE_ENTRIES",
    "NEW_GOVERNANCE_OPEN_OPERATION",
    "NEW_GOVERNANCE_SEARCH_OPERATION",
    "ROUTE_CONTAINS_HOST_PATHS",
    "ROUTE_ENTRY_AGENTS_POLICY",
    "ROUTE_ENTRY_ARCHITECTURE_CARD",
    "ROUTE_ENTRY_AUTHORIZED_ROOT",
    "ROUTE_ENTRY_BLOCKER",
    "ROUTE_ENTRY_CANDIDATE_SET",
    "ROUTE_ENTRY_DOCUMENT",
    "ROUTE_ENTRY_EVIDENCE",
    "ROUTE_ENTRY_HUMAN_BRAKE",
    "ROUTE_ENTRY_KINDS",
    "ROUTE_ENTRY_MILESTONE_CARD",
    "ROUTE_ENTRY_OBSERVATION",
    "ROUTE_ENTRY_PLAN_AUTHORITY",
    "ROUTE_ENTRY_PLAN_CARD",
    "ROUTE_ENTRY_PROGRESS_CARD",
    "ROUTE_ENTRY_PROJECT_CARD",
    "ROUTE_ENTRY_RESULT",
    "ROUTE_ENTRY_SKILL",
    "ROUTE_REFS_ARE_BOUNDED",
    "SECOND_RETRIEVAL_FRAMEWORK",
    "ContextRoute",
    "ContextRouteEntry",
    "ContextRouteError",
    "ContextRouteInput",
    "RouteBoundedRef",
    "RoutePolicyRef",
    "RouteSkillRef",
    "build_bootstrap_governance_context",
    "build_context_route",
]
