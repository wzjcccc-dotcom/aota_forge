"""AF #57 M2/W2 — ephemeral active semantic working set & retrieval policy.

The active working context of a model-visible Governance session is an
*ephemeral* bounded set of refs/cards/results that are currently useful:

    WorkingSet
      = bounded refs/cards/results currently useful to the model

It is reconstructible from authoritative/projection inputs at any time:

    CONTEXT_WORKING_SET_IS_EPHEMERAL=yes
    WORKING_SET_IS_DURABLE=no
    WORKING_SET_IS_AUTHORITY=no
    WORKING_SET_IS_CONVERSATION_HISTORY=no
    WORKING_SET_IS_WORKFLOW_STATE=no
    CONVERSATION_HISTORY_IS_ACTIVE_WORKING_SET=no
    WORKING_SET_DATABASE_CREATED=no
    SESSION_MEMORY_DB_CREATED=no
    PERSISTENT_OBSERVATION_CACHE_CREATED=no

There is deliberately no WorkingSet SQLite DB, session memory DB, persistent
observation cache or context daemon.  Nothing in this module persists, and the
module deliberately has no storage imports.

Retrieval policy semantics carried here (model-visible contract, never a
retrieval engine):

    CARD_FIRST=yes
    OBSERVATION_REUSE_FIRST=yes
    REDUNDANT_RETRIEVAL_DEFAULT=no
    LLM_OWNS_RETRIEVAL_INTENT=yes
    CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT=no
    NO_ARBITRARY_TOKEN_THRESHOLD=yes
    SEMANTIC_SECTION_FIRST=yes
    FIXED_CONTINUATION_IS_FALLBACK=yes
    CANDIDATE_MISS_IS_SEARCH_MISS=no

The module *represents* the retrieval intents (``FULL`` / ``SECTION`` /
``QUERY`` / ``HYDRATE``) and the observations/candidates needed to reason
about reuse; it never decides retrieval intent from size, bytes, or any other
mechanical predicate.  Intent selection stays LLM semantic reasoning.

Freshness boundary (explicit and permanent):

    SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK=no
    SEMANTIC_REUSE_IS_AUTHORITY_FRESHNESS=no

Semantic reuse means "continue model reasoning from still-valid context".  It
never bypasses read-before-write, expected revision/digest, CAS,
verify-after-write or explicit freshness validation.  Authority freshness and
model-context freshness are different concerns.

This module is explicitly not a second retrieval framework: no Reader import,
no index, no ranking, no generic engine, no public operation.

    SECOND_RETRIEVAL_FRAMEWORK=no
    READER_IMPLEMENTATION_IMPORTED=no
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Sequence

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.cards import (
    MAX_CARD_REF_LENGTH,
    GovernanceCard,
    GovernanceCardError,
    require_logical_ref,
    require_marker_tuple,
)

# ---------------------------------------------------------------------------
# Truthful gate flags (tests / review)
# ---------------------------------------------------------------------------

CARD_FIRST: bool = True
OBSERVATION_REUSE_FIRST: bool = True
REDUNDANT_RETRIEVAL_DEFAULT: bool = False
SEMANTIC_SECTION_FIRST: bool = True
FIXED_CONTINUATION_IS_FALLBACK: bool = True
CONVERSATION_HISTORY_IS_ACTIVE_WORKING_SET: bool = False
CONTEXT_WORKING_SET_IS_EPHEMERAL: bool = True
WORKING_SET_IS_DURABLE: bool = False
WORKING_SET_IS_AUTHORITY: bool = False
WORKING_SET_IS_CONVERSATION_HISTORY: bool = False
WORKING_SET_IS_WORKFLOW_STATE: bool = False
LLM_OWNS_RETRIEVAL_INTENT: bool = True
CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT: bool = False
NO_ARBITRARY_TOKEN_THRESHOLD: bool = True
CANDIDATE_MISS_IS_SEARCH_MISS: bool = False
SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK: bool = False
SEMANTIC_REUSE_IS_AUTHORITY_FRESHNESS: bool = False
SECOND_RETRIEVAL_FRAMEWORK: bool = False
READER_IMPLEMENTATION_IMPORTED: bool = False
WORKING_SET_DATABASE_CREATED: bool = False
SESSION_MEMORY_DB_CREATED: bool = False
PERSISTENT_OBSERVATION_CACHE_CREATED: bool = False

# ---------------------------------------------------------------------------
# Retrieval intents (LLM-owned semantic choices; never mechanical thresholds)
# ---------------------------------------------------------------------------

RETRIEVAL_INTENT_FULL = "full"
RETRIEVAL_INTENT_SECTION = "section"
RETRIEVAL_INTENT_QUERY = "query"
RETRIEVAL_INTENT_HYDRATE = "hydrate"

RETRIEVAL_INTENTS = frozenset(
    {
        RETRIEVAL_INTENT_FULL,
        RETRIEVAL_INTENT_SECTION,
        RETRIEVAL_INTENT_QUERY,
        RETRIEVAL_INTENT_HYDRATE,
    }
)

MAX_RETRIEVAL_QUERY_LENGTH = 2048
MAX_RETRIEVAL_SECTION_LENGTH = 512

# Working-set / observation bounds (explicit representation bounds; not
# retrieval-intent thresholds).
MAX_WORKING_SET_ENTRIES = 128
MAX_WORKING_SET_REF_LENGTH = MAX_CARD_REF_LENGTH
MAX_WORKING_SET_TEXT_LENGTH = 256
MAX_WORKING_SET_STRUCTURE_ENTRIES = 32
MAX_CANDIDATE_SET_CANDIDATES = 64
MAX_CANDIDATE_SET_ID_LENGTH = 256
MAX_OBSERVATION_REASON_LENGTH = 256

# Semantic section discovery bounds.
MAX_SEMANTIC_SECTIONS = 128
MAX_SEMANTIC_SECTION_SCAN_BYTES = 2 * 1024 * 1024
MAX_SEMANTIC_MARKER_LENGTH = 200
MAX_CONTINUATION_LIMIT = 64 * 1024

# ---------------------------------------------------------------------------
# Research invalidation reasons (bounded vocabulary)
# ---------------------------------------------------------------------------

INVALIDATION_CANDIDATE_SET_EXHAUSTED = "candidate_set_exhausted"
INVALIDATION_SOURCE_CHANGED = "source_changed"
INVALIDATION_QUERY_CHANGED = "query_changed"
INVALIDATION_SCOPE_CHANGED = "scope_changed"
INVALIDATION_INTENT_CHANGED = "intent_changed"
INVALIDATION_PRIOR_INCOMPLETE = "prior_observation_incomplete"
INVALIDATION_HOST_COMPACTION = "host_compaction_removed_required_material"
INVALIDATION_EXPLICIT_FRESHNESS = "explicit_freshness_or_precondition_required"

RESEARCH_INVALIDATION_REASONS = frozenset(
    {
        INVALIDATION_CANDIDATE_SET_EXHAUSTED,
        INVALIDATION_SOURCE_CHANGED,
        INVALIDATION_QUERY_CHANGED,
        INVALIDATION_SCOPE_CHANGED,
        INVALIDATION_INTENT_CHANGED,
        INVALIDATION_PRIOR_INCOMPLETE,
        INVALIDATION_HOST_COMPACTION,
        INVALIDATION_EXPLICIT_FRESHNESS,
    }
)

WORKING_SET_ENTRY_KINDS = frozenset(
    {
        "card",
        "skill",
        "evidence",
        "result",
        "document",
        "observation",
        "candidate",
    }
)

_SECTION_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SECTION_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


class WorkingSetError(ValueError):
    """A working-set / retrieval-policy value is structurally invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _require_text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorkingSetError("INVALID_FIELD", f"{label} must be a string")
    text = value.strip()
    if not text:
        raise WorkingSetError("INVALID_FIELD", f"{label} must be a non-empty string")
    if len(text) > max_length:
        raise WorkingSetError("INVALID_FIELD", f"{label} exceeds maximum {max_length}")
    if "\x00" in text:
        raise WorkingSetError("INVALID_FIELD", f"{label} must not contain NUL")
    return text


def _optional_text(value: Any, label: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, label, max_length=max_length)


def _optional_digest(value: Any, label: str) -> str | None:
    if value is None:
        return None
    text = _require_text(value, label, max_length=64)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise WorkingSetError("INVALID_FIELD", f"{label} must be 64 lowercase hex")
    return text


def _ref(value: Any, label: str, *, max_length: int = MAX_WORKING_SET_REF_LENGTH) -> str:
    try:
        return require_logical_ref(value, label, max_length=max_length)
    except GovernanceCardError as exc:
        raise WorkingSetError(exc.code, exc.message) from exc


def _markers(value: Any, label: str, *, max_entries: int) -> tuple[str, ...]:
    try:
        return require_marker_tuple(value, label, max_entries=max_entries)
    except GovernanceCardError as exc:
        raise WorkingSetError(exc.code, exc.message) from exc


# ---------------------------------------------------------------------------
# Small value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalIntentSelection:
    """One LLM-selected semantic retrieval intent for one bounded ref.

    This is a *selection value*, not a decision.  The model constructs it; the
    Control Plane never selects retrieval intent
    (``CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT=no``).  No size/byte predicate
    exists anywhere in this module.
    """

    intent: str
    ref: str
    query: str | None = None
    section: str | None = None

    def __post_init__(self) -> None:
        if self.intent not in RETRIEVAL_INTENTS:
            raise WorkingSetError("UNKNOWN_RETRIEVAL_INTENT", f"intent must be one of {sorted(RETRIEVAL_INTENTS)}")
        object.__setattr__(self, "ref", _ref(self.ref, "intent.ref"))
        object.__setattr__(self, "query", _optional_text(self.query, "intent.query", max_length=MAX_RETRIEVAL_QUERY_LENGTH))
        object.__setattr__(
            self,
            "section",
            _optional_text(self.section, "intent.section", max_length=MAX_RETRIEVAL_SECTION_LENGTH),
        )
        if self.intent == RETRIEVAL_INTENT_QUERY and self.query is None:
            raise WorkingSetError("INVALID_FIELD", "query intent requires the semantic query text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "ref": self.ref,
            "query": self.query,
            "section": self.section,
            "IS_AUTHORITY": False,
        }


@dataclass(frozen=True)
class CardFirstFacts:
    """Safe facts about a document/source before any deep retrieval.

    ``CARD_FIRST=yes``: an unknown document/source is inspected through these
    facts (size where known, structure where known, completeness,
    snapshot/revision/digest, navigable refs) before the LLM chooses a
    retrieval intent.  Facts are never a decision; unknown facts stay ``None``
    / empty and are never fabricated.
    """

    ref: str
    kind: str
    size_bytes: int | None = None
    structure: tuple[str, ...] = ()
    complete: bool = False
    missing_facts: tuple[str, ...] = ()
    revision: str | None = None
    digest: str | None = None
    navigable_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "card_first.ref"))
        object.__setattr__(self, "kind", _require_text(self.kind, "card_first.kind", max_length=64))
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise WorkingSetError("INVALID_FIELD", "card_first.size_bytes must be a non-negative int or None")
        structure = tuple(self.structure or ())
        if len(structure) > MAX_WORKING_SET_STRUCTURE_ENTRIES:
            raise WorkingSetError("INVALID_FIELD", f"card_first.structure exceeds {MAX_WORKING_SET_STRUCTURE_ENTRIES} entries")
        for index, item in enumerate(structure):
            _require_text(item, f"card_first.structure[{index}]", max_length=MAX_SEMANTIC_MARKER_LENGTH)
        object.__setattr__(self, "structure", structure)
        if type(self.complete) is not bool:
            raise WorkingSetError("INVALID_FIELD", "card_first.complete must be a bool")
        missing = _markers(self.missing_facts, "card_first.missing_facts", max_entries=MAX_WORKING_SET_STRUCTURE_ENTRIES)
        if self.complete and missing:
            raise WorkingSetError("COMPLETENESS_CONTRADICTION", "a complete result must not carry missing-fact markers")
        if not self.complete and not missing:
            raise WorkingSetError("COMPLETENESS_NOT_EXPLICIT", "an incomplete result must carry explicit missing-fact markers")
        object.__setattr__(self, "missing_facts", missing)
        object.__setattr__(self, "revision", _optional_text(self.revision, "card_first.revision", max_length=128))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "card_first.digest"))
        navigable = tuple(self.navigable_refs or ())
        if len(navigable) > MAX_WORKING_SET_STRUCTURE_ENTRIES:
            raise WorkingSetError("INVALID_FIELD", "card_first.navigable_refs exceeds bound")
        object.__setattr__(self, "navigable_refs", tuple(_ref(item, "card_first.navigable_ref") for item in navigable))

    @classmethod
    def from_card(cls, card: GovernanceCard) -> "CardFirstFacts":
        """Derive safe facts from one accepted W1 Governance Card."""
        if not isinstance(card, GovernanceCard):
            raise WorkingSetError("INVALID_FIELD", "from_card requires a GovernanceCard")
        revision: str | None = None
        digest: str | None = None
        for source_ref in card.source_refs:
            if source_ref.revision is not None:
                revision = source_ref.revision
            if source_ref.digest is not None:
                digest = source_ref.digest
            if revision is not None or digest is not None:
                break
        return cls(
            ref=f"card:{card.card_kind}:{card.projection_id()}",
            kind=card.card_kind,
            size_bytes=None,
            structure=(),
            complete=card.complete,
            missing_facts=card.missing_facts,
            revision=revision,
            digest=digest,
            navigable_refs=tuple(ref.ref for ref in card.navigation_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ref": self.ref,
            "kind": self.kind,
            "complete": self.complete,
            "IS_AUTHORITY": False,
        }
        for key, value in (
            ("size_bytes", self.size_bytes),
            ("structure", list(self.structure) or None),
            ("missing_facts", list(self.missing_facts) or None),
            ("revision", self.revision),
            ("digest", self.digest),
            ("navigable_refs", list(self.navigable_refs) or None),
        ):
            if value is not None:
                payload[key] = value
        return payload


# ---------------------------------------------------------------------------
# Observation / candidate reuse
# ---------------------------------------------------------------------------

OBSERVATION_STATUS_ABSENT = "absent"
OBSERVATION_STATUS_REUSABLE = "reusable"
OBSERVATION_STATUS_STALE = "stale"
OBSERVATION_STATUS_INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ObservationReuse:
    """Semantic reuse status of one already-materialized observation.

    ``may_reuse_semantically()`` only means "continue reasoning from this
    still-valid model-visible material".  It never satisfies an authority
    freshness / CAS precondition
    (``SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK=no``).
    """

    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in (
            OBSERVATION_STATUS_ABSENT,
            OBSERVATION_STATUS_REUSABLE,
            OBSERVATION_STATUS_STALE,
            OBSERVATION_STATUS_INCOMPLETE,
        ):
            raise WorkingSetError("INVALID_FIELD", f"unknown observation status {self.status!r}")
        object.__setattr__(self, "reason", _require_text(self.reason, "observation.reason", max_length=MAX_OBSERVATION_REASON_LENGTH))

    def may_reuse_semantically(self) -> bool:
        return self.status == OBSERVATION_STATUS_REUSABLE

    def requires_fresh_authority_read(self) -> bool:
        """True whenever this observation cannot stand in for authority truth."""
        return self.status != OBSERVATION_STATUS_REUSABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "may_reuse_semantically": self.may_reuse_semantically(),
            "SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK": False,
        }


@dataclass(frozen=True)
class CandidateRef:
    """One bounded candidate ref inside a candidate set."""

    ref: str
    revision: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "candidate.ref"))
        object.__setattr__(self, "revision", _optional_text(self.revision, "candidate.revision", max_length=128))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "candidate.digest"))

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "revision": self.revision, "digest": self.digest}


@dataclass(frozen=True)
class CandidateSet:
    """Bounded candidate set from one prior search/card result.

    ``candidate_miss != search_miss``: a candidate that turns out irrelevant
    is removed from the remaining candidates; the rest stay reusable.
    """

    candidate_set_id: str
    candidates: tuple[CandidateRef, ...]
    query: str | None = None
    scope: str | None = None
    intent: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_set_id",
            _require_text(self.candidate_set_id, "candidate_set.id", max_length=MAX_CANDIDATE_SET_ID_LENGTH),
        )
        candidates = tuple(self.candidates or ())
        if len(candidates) > MAX_CANDIDATE_SET_CANDIDATES:
            raise WorkingSetError("INVALID_FIELD", f"candidate set exceeds {MAX_CANDIDATE_SET_CANDIDATES} candidates")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, CandidateRef):
                raise WorkingSetError("INVALID_FIELD", f"candidates[{index}] must be CandidateRef")
        deduped: list[CandidateRef] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.ref not in seen:
                seen.add(candidate.ref)
                deduped.append(candidate)
        object.__setattr__(self, "candidates", tuple(deduped))
        object.__setattr__(self, "query", _optional_text(self.query, "candidate_set.query", max_length=MAX_RETRIEVAL_QUERY_LENGTH))
        object.__setattr__(self, "scope", _optional_text(self.scope, "candidate_set.scope", max_length=MAX_WORKING_SET_TEXT_LENGTH))
        if self.intent is not None and self.intent not in RETRIEVAL_INTENTS:
            raise WorkingSetError("UNKNOWN_RETRIEVAL_INTENT", f"candidate set intent must be one of {sorted(RETRIEVAL_INTENTS)}")
        object.__setattr__(self, "intent", self.intent)

    def remaining(self, consumed: Sequence[str] = ()) -> tuple[CandidateRef, ...]:
        """Candidates not yet consumed/irrelevant (order preserved)."""
        consumed_set = {str(ref) for ref in consumed}
        return tuple(candidate for candidate in self.candidates if candidate.ref not in consumed_set)

    def without(self, ref: str) -> "CandidateSet":
        """A copy excluding one irrelevant candidate (no search implied)."""
        target = _ref(ref, "candidate_set.without.ref")
        return CandidateSet(
            candidate_set_id=self.candidate_set_id,
            candidates=tuple(candidate for candidate in self.candidates if candidate.ref != target),
            query=self.query,
            scope=self.scope,
            intent=self.intent,
        )

    def is_exhausted(self, consumed: Sequence[str] = ()) -> bool:
        return not self.remaining(consumed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_set_id": self.candidate_set_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "candidate_count": len(self.candidates),
            "query": self.query,
            "scope": self.scope,
            "intent": self.intent,
            "CANDIDATE_MISS_IS_SEARCH_MISS": False,
        }


@dataclass(frozen=True)
class CandidateMissDisposition:
    """Result of one candidate miss: reuse the rest; do not re-search."""

    candidate_set: CandidateSet
    missed_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_set, CandidateSet):
            raise WorkingSetError("INVALID_FIELD", "candidate_set must be a CandidateSet")
        object.__setattr__(self, "missed_ref", _ref(self.missed_ref, "candidate_miss.ref"))

    @property
    def search_miss(self) -> bool:
        return False

    def reuse_remaining(self, consumed: Sequence[str] = ()) -> tuple[CandidateRef, ...]:
        return self.candidate_set.without(self.missed_ref).remaining(consumed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_missed": self.missed_ref,
            "search_miss": False,
            "remaining_candidates": [candidate.to_dict() for candidate in self.reuse_remaining()],
            "CANDIDATE_MISS_IS_SEARCH_MISS": False,
        }


def may_research(reason: str) -> bool:
    """True only for the bounded invalidation reasons that permit re-search."""
    if reason not in RESEARCH_INVALIDATION_REASONS:
        raise WorkingSetError("UNKNOWN_INVALIDATION_REASON", f"reason must be one of {sorted(RESEARCH_INVALIDATION_REASONS)}")
    return True


# ---------------------------------------------------------------------------
# Semantic sections / fixed continuation fallback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticSection:
    """One bounded semantic (heading) section of a Markdown document."""

    marker: str
    level: int
    start_offset: int
    end_offset: int
    ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "marker", _require_text(self.marker, "section.marker", max_length=MAX_SEMANTIC_MARKER_LENGTH))
        if type(self.level) is not int or not 1 <= self.level <= 6:
            raise WorkingSetError("INVALID_FIELD", "section.level must be an int between 1 and 6")
        if type(self.start_offset) is not int or self.start_offset < 0:
            raise WorkingSetError("INVALID_FIELD", "section.start_offset must be a non-negative int")
        if type(self.end_offset) is not int or self.end_offset < self.start_offset:
            raise WorkingSetError("INVALID_FIELD", "section.end_offset must be >= start_offset")
        object.__setattr__(self, "ref", _ref(self.ref, "section.ref"))

    def bounded_slice(self, text: str) -> str:
        if not isinstance(text, str):
            raise WorkingSetError("INVALID_FIELD", "text must be a string")
        return text[self.start_offset : self.end_offset]

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "level": self.level,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "ref": self.ref,
            "use_when": "preferred retrieval unit for this section's semantic content",
        }


@dataclass(frozen=True)
class FixedContinuation:
    """Fixed offset continuation — the fallback, never the default."""

    ref: str
    offset: int
    limit: int
    reason: str = "fallback_after_semantic_sections"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "continuation.ref"))
        if type(self.offset) is not int or self.offset < 0:
            raise WorkingSetError("INVALID_FIELD", "continuation.offset must be a non-negative int")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_CONTINUATION_LIMIT:
            raise WorkingSetError("INVALID_FIELD", "continuation.limit must be a bounded positive int")
        object.__setattr__(self, "reason", _require_text(self.reason, "continuation.reason", max_length=MAX_OBSERVATION_REASON_LENGTH))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "offset": self.offset,
            "limit": self.limit,
            "reason": self.reason,
            "FIXED_CONTINUATION_IS_FALLBACK": True,
        }


def _slug(marker: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", marker.strip().lower()).strip("-")
    return slug[:64] or "section"


def discover_semantic_sections(
    text: str,
    *,
    ref: str,
    max_sections: int = MAX_SEMANTIC_SECTIONS,
) -> tuple[SemanticSection, ...]:
    """Bounded heading/section discovery over Markdown text.

    The smallest mechanism that lets Governance Markdown be retrieved
    section-first instead of by blind offset continuation.  No persistent
    index, no parser service, no vector DB: one bounded line scan with fence
    tracking (headings inside fenced code blocks are not sections).
    """
    if not isinstance(text, str):
        raise WorkingSetError("INVALID_FIELD", "text must be a string")
    base_ref = _ref(ref, "sections.ref")
    if type(max_sections) is not int or not 1 <= max_sections <= MAX_SEMANTIC_SECTIONS:
        raise WorkingSetError("INVALID_FIELD", f"max_sections must be an int between 1 and {MAX_SEMANTIC_SECTIONS}")
    if len(text.encode("utf-8")) > MAX_SEMANTIC_SECTION_SCAN_BYTES:
        raise WorkingSetError(
            "DOCUMENT_EXCEEDS_SCAN_BOUND",
            f"semantic section discovery is bounded to {MAX_SEMANTIC_SECTION_SCAN_BYTES} bytes",
        )

    found: list[tuple[int, str, int]] = []
    offset = 0
    fence_marker: str | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        fence_match = _SECTION_FENCE_RE.match(stripped)
        if fence_match is not None:
            token = fence_match.group(1)
            if fence_marker is None:
                fence_marker = token
            elif token == fence_marker:
                fence_marker = None
            offset += len(line)
            continue
        if fence_marker is None:
            heading = _SECTION_HEADING_RE.match(stripped)
            if heading is not None:
                found.append((len(heading.group(1)), heading.group(2).strip(), offset))
        offset += len(line)

    if not found:
        return ()

    sections: list[SemanticSection] = []
    seen_refs: set[str] = set()
    for index, (level, marker, start) in enumerate(found[:max_sections]):
        end = len(text)
        for next_level, _, next_start in found[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        section_ref = f"{base_ref}#{_slug(marker)}"
        if section_ref in seen_refs:
            suffix = 2
            while f"{section_ref}~{suffix}" in seen_refs:
                suffix += 1
            section_ref = f"{section_ref}~{suffix}"
        seen_refs.add(section_ref)
        sections.append(
            SemanticSection(marker=marker, level=level, start_offset=start, end_offset=end, ref=section_ref)
        )
    return tuple(sections)


def continuation_fallback(*, ref: str, offset: int, limit: int) -> FixedContinuation:
    """Represent fixed offset continuation (used only when sections are absent)."""
    return FixedContinuation(ref=ref, offset=offset, limit=limit)


# ---------------------------------------------------------------------------
# Working set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkingSetEntry:
    """One bounded, non-authoritative, ephemeral working-set entry."""

    ref: str
    kind: str
    revision: str | None = None
    digest: str | None = None
    complete: bool = True
    missing_facts: tuple[str, ...] = ()
    semantic_role: str | None = None
    use_when: str | None = None
    candidate_set_id: str | None = None
    card_projection_id: str | None = None
    size_bytes: int | None = None
    structure: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _ref(self.ref, "entry.ref"))
        kind = _require_text(self.kind, "entry.kind", max_length=64)
        if kind not in WORKING_SET_ENTRY_KINDS:
            raise WorkingSetError("UNKNOWN_ENTRY_KIND", f"entry.kind must be one of {sorted(WORKING_SET_ENTRY_KINDS)}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "revision", _optional_text(self.revision, "entry.revision", max_length=128))
        object.__setattr__(self, "digest", _optional_digest(self.digest, "entry.digest"))
        if type(self.complete) is not bool:
            raise WorkingSetError("INVALID_FIELD", "entry.complete must be a bool")
        missing = _markers(self.missing_facts, "entry.missing_facts", max_entries=MAX_WORKING_SET_STRUCTURE_ENTRIES)
        if self.complete and missing:
            raise WorkingSetError("COMPLETENESS_CONTRADICTION", "a complete entry must not carry missing-fact markers")
        if not self.complete and not missing:
            raise WorkingSetError("COMPLETENESS_NOT_EXPLICIT", "an incomplete entry must carry explicit missing-fact markers")
        object.__setattr__(self, "missing_facts", missing)
        object.__setattr__(self, "semantic_role", _optional_text(self.semantic_role, "entry.semantic_role", max_length=64))
        object.__setattr__(self, "use_when", _optional_text(self.use_when, "entry.use_when", max_length=MAX_WORKING_SET_TEXT_LENGTH))
        object.__setattr__(
            self,
            "candidate_set_id",
            _optional_text(self.candidate_set_id, "entry.candidate_set_id", max_length=MAX_CANDIDATE_SET_ID_LENGTH),
        )
        object.__setattr__(
            self,
            "card_projection_id",
            _optional_text(self.card_projection_id, "entry.card_projection_id", max_length=MAX_WORKING_SET_TEXT_LENGTH),
        )
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise WorkingSetError("INVALID_FIELD", "entry.size_bytes must be a non-negative int or None")
        structure = tuple(self.structure or ())
        if len(structure) > MAX_WORKING_SET_STRUCTURE_ENTRIES:
            raise WorkingSetError("INVALID_FIELD", "entry.structure exceeds bound")
        for index, item in enumerate(structure):
            _require_text(item, f"entry.structure[{index}]", max_length=MAX_SEMANTIC_MARKER_LENGTH)
        object.__setattr__(self, "structure", structure)

    @classmethod
    def from_card(cls, card: GovernanceCard) -> "WorkingSetEntry":
        facts = CardFirstFacts.from_card(card)
        return cls(
            ref=facts.ref,
            kind="card",
            revision=facts.revision,
            digest=facts.digest,
            complete=facts.complete,
            missing_facts=facts.missing_facts,
            semantic_role=card.card_kind,
            use_when=f"compact {card.card_kind} context",
            card_projection_id=card.projection_id(),
        )

    def card_first_facts(self) -> CardFirstFacts:
        return CardFirstFacts(
            ref=self.ref,
            kind=self.kind,
            size_bytes=self.size_bytes,
            structure=self.structure,
            complete=self.complete,
            missing_facts=self.missing_facts,
            revision=self.revision,
            digest=self.digest,
            navigable_refs=(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ref": self.ref,
            "kind": self.kind,
            "complete": self.complete,
            "IS_AUTHORITY": False,
        }
        for key, value in (
            ("revision", self.revision),
            ("digest", self.digest),
            ("missing_facts", list(self.missing_facts) or None),
            ("semantic_role", self.semantic_role),
            ("use_when", self.use_when),
            ("candidate_set_id", self.candidate_set_id),
            ("card_projection_id", self.card_projection_id),
            ("size_bytes", self.size_bytes),
            ("structure", list(self.structure) or None),
        ):
            if value is not None:
                payload[key] = value
        return payload


class WorkingSet:
    """The ephemeral active semantic working set.

    Plain in-memory object: it cannot be persisted, is reconstructible at any
    time from authoritative/projection inputs, and is never authority,
    conversation history or workflow state.
    """

    def __init__(self, *, project_id: str, plan_id: str | None = None) -> None:
        project = _require_text(project_id, "project_id", max_length=96)
        if not PROJECT_ID_RE.fullmatch(project):
            raise WorkingSetError("INVALID_PROJECT_ID", "project_id must match the canonical project identity grammar")
        self._project_id = project
        self._plan_id = plan_id
        self._entries: list[WorkingSetEntry] = []

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def plan_id(self) -> str | None:
        return self._plan_id

    @property
    def entries(self) -> tuple[WorkingSetEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, entry: WorkingSetEntry) -> None:
        if not isinstance(entry, WorkingSetEntry):
            raise WorkingSetError("INVALID_FIELD", "entry must be a WorkingSetEntry")
        for index, existing in enumerate(self._entries):
            if existing.ref == entry.ref:
                if existing.digest == entry.digest and existing.revision == entry.revision and existing.complete == entry.complete:
                    return
                self._entries[index] = entry
                return
        if len(self._entries) >= MAX_WORKING_SET_ENTRIES:
            raise WorkingSetError("WORKING_SET_FULL", f"working set is bounded to {MAX_WORKING_SET_ENTRIES} entries")
        self._entries.append(entry)

    def add_card(self, card: GovernanceCard) -> WorkingSetEntry:
        entry = WorkingSetEntry.from_card(card)
        self.add(entry)
        return entry

    def get(self, ref: str) -> WorkingSetEntry | None:
        target = _ref(ref, "get.ref")
        for entry in self._entries:
            if entry.ref == target:
                return entry
        return None

    def observation_status(
        self,
        ref: str,
        *,
        digest: str | None = None,
        revision: str | None = None,
    ) -> ObservationReuse:
        """Reuse status for one already-materialized observation.

        Same ref + same digest/revision is semantically reusable; a changed
        source identity makes the old observation invalid for reuse (it may
        still be useful historical material, but not as current truth).
        """
        target = _ref(ref, "observation_status.ref")
        claimed_digest = _optional_digest(digest, "observation_status.digest")
        claimed_revision = _optional_text(revision, "observation_status.revision", max_length=128)
        entry = self.get(target)
        if entry is None:
            return ObservationReuse(status=OBSERVATION_STATUS_ABSENT, reason="not_materialized")
        if not entry.complete:
            return ObservationReuse(status=OBSERVATION_STATUS_INCOMPLETE, reason="incomplete_for_current_need")
        if claimed_digest is not None:
            if entry.digest is None:
                return ObservationReuse(status=OBSERVATION_STATUS_STALE, reason="observation_identity_unknown")
            if entry.digest != claimed_digest:
                return ObservationReuse(status=OBSERVATION_STATUS_STALE, reason=INVALIDATION_SOURCE_CHANGED)
        if claimed_revision is not None:
            if entry.revision is None:
                return ObservationReuse(status=OBSERVATION_STATUS_STALE, reason="observation_revision_unknown")
            if entry.revision != claimed_revision:
                return ObservationReuse(status=OBSERVATION_STATUS_STALE, reason=INVALIDATION_SOURCE_CHANGED)
        return ObservationReuse(status=OBSERVATION_STATUS_REUSABLE, reason="unchanged_ref_identity")

    def ordered_entries(self) -> tuple[WorkingSetEntry, ...]:
        return tuple(sorted(self._entries, key=lambda entry: (entry.kind, entry.ref)))

    def to_dict(self) -> dict[str, Any]:
        ordered = self.ordered_entries()
        payload = [entry.to_dict() for entry in ordered]
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return {
            "project_id": self._project_id,
            "plan_id": self._plan_id,
            "entries": payload,
            "entry_count": len(ordered),
            "working_set_id": f"wset-{digest[:32]}",
            "IS_AUTHORITY": False,
            "CONTEXT_WORKING_SET_IS_EPHEMERAL": True,
            "WORKING_SET_IS_DURABLE": False,
        }


__all__ = [
    "CANDIDATE_MISS_IS_SEARCH_MISS",
    "CARD_FIRST",
    "CONTEXT_WORKING_SET_IS_EPHEMERAL",
    "CONTROL_PLANE_SELECTS_RETRIEVAL_INTENT",
    "CONVERSATION_HISTORY_IS_ACTIVE_WORKING_SET",
    "FIXED_CONTINUATION_IS_FALLBACK",
    "INVALIDATION_CANDIDATE_SET_EXHAUSTED",
    "INVALIDATION_EXPLICIT_FRESHNESS",
    "INVALIDATION_HOST_COMPACTION",
    "INVALIDATION_INTENT_CHANGED",
    "INVALIDATION_PRIOR_INCOMPLETE",
    "INVALIDATION_QUERY_CHANGED",
    "INVALIDATION_SCOPE_CHANGED",
    "INVALIDATION_SOURCE_CHANGED",
    "LLM_OWNS_RETRIEVAL_INTENT",
    "MAX_SEMANTIC_SECTIONS",
    "MAX_SEMANTIC_SECTION_SCAN_BYTES",
    "MAX_WORKING_SET_ENTRIES",
    "NO_ARBITRARY_TOKEN_THRESHOLD",
    "OBSERVATION_REUSE_FIRST",
    "OBSERVATION_STATUS_ABSENT",
    "OBSERVATION_STATUS_INCOMPLETE",
    "OBSERVATION_STATUS_REUSABLE",
    "OBSERVATION_STATUS_STALE",
    "PERSISTENT_OBSERVATION_CACHE_CREATED",
    "READER_IMPLEMENTATION_IMPORTED",
    "REDUNDANT_RETRIEVAL_DEFAULT",
    "RESEARCH_INVALIDATION_REASONS",
    "RETRIEVAL_INTENTS",
    "RETRIEVAL_INTENT_FULL",
    "RETRIEVAL_INTENT_HYDRATE",
    "RETRIEVAL_INTENT_QUERY",
    "RETRIEVAL_INTENT_SECTION",
    "SECOND_RETRIEVAL_FRAMEWORK",
    "SEMANTIC_REUSE_BYPASSES_AUTHORITY_CHECK",
    "SEMANTIC_REUSE_IS_AUTHORITY_FRESHNESS",
    "SEMANTIC_SECTION_FIRST",
    "SESSION_MEMORY_DB_CREATED",
    "WORKING_SET_DATABASE_CREATED",
    "WORKING_SET_ENTRY_KINDS",
    "WORKING_SET_IS_AUTHORITY",
    "WORKING_SET_IS_CONVERSATION_HISTORY",
    "WORKING_SET_IS_DURABLE",
    "WORKING_SET_IS_WORKFLOW_STATE",
    "CandidateMissDisposition",
    "CandidateRef",
    "CandidateSet",
    "CardFirstFacts",
    "FixedContinuation",
    "ObservationReuse",
    "RetrievalIntentSelection",
    "SemanticSection",
    "WorkingSet",
    "WorkingSetEntry",
    "WorkingSetError",
    "continuation_fallback",
    "discover_semantic_sections",
    "may_research",
]
