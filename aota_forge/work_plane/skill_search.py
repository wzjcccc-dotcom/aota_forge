"""Bounded lexical Skill search / discovery (S3 M2-W2).

Lexical discovery only — no vector search, no embeddings, no hydration.

Invariants
----------
* LEXICAL_SEARCH_V0=yes
* VECTOR_SEARCH_REQUIRED=no
* EMBEDDING_DEPENDENCY_REQUIRED=no
* SEARCH_RESULT_BOUNDED=yes
* SEARCH_RESULT_REF_ONLY=yes
* SEARCH_NAMESPACE_SCOPED=yes
* SEARCH_NAMESPACE_REQUIRED=yes
* FOREIGN_NAMESPACE_RESULT=no
* SEARCH_MATCH_GRANTS_AUTHORITY=no
* SKILL_IS_AUTHORITY=no
* NAMESPACE_GRANTS_AUTHORITY=no
* SEARCH_RESULT_GRANTS_AUTHORITY=no
* SKILL_CONTENT_HYDRATION=no
* CONTENT_REF_DEREFERENCE=no
* SEARCH_RESULT_ORDER_DETERMINISTIC=yes
* INPUT_ORDER_IS_AUTHORITY=no
* LATEST_VERSION_FIRST=no
* SEMVER_ORDERING=no
* SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP=no
* DUPLICATE_SEARCH_DOCUMENT_FAIL_CLOSED=yes
* POST_CONSTRUCTION_INPUT_MUTATION_CHANGES_SEARCH_INDEX=no
* SEARCH_LIMIT_REQUIRED=yes
* SEARCH_LIMIT_IMPLEMENTATION_BOUND=yes
* SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP=no

Search operates only over registry/search corpus supplied explicitly.
It must not determine ultimate execution authorization (W3).
Search is discovery only — does NOT hydrate Skill content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill_registry import StaticSkillRegistry
from aota_forge.work_plane.skill import MAX_SKILL_ID_LENGTH, MAX_VERSION_LENGTH

# ---------------------------------------------------------------------------
# Invariant markers (descriptive, not authority)
# ---------------------------------------------------------------------------

LEXICAL_SEARCH_V0: bool = True
VECTOR_SEARCH_REQUIRED: bool = False
EMBEDDING_DEPENDENCY_REQUIRED: bool = False

SEARCH_RESULT_BOUNDED: bool = True
SEARCH_RESULT_REF_ONLY: bool = True
SEARCH_NAMESPACE_SCOPED: bool = True

SEARCH_NAMESPACE_REQUIRED: bool = True
FOREIGN_NAMESPACE_RESULT: bool = False

SEARCH_MATCH_GRANTS_AUTHORITY: bool = False
SKILL_IS_AUTHORITY: bool = False
NAMESPACE_GRANTS_AUTHORITY: bool = False
SEARCH_RESULT_GRANTS_AUTHORITY: bool = False

SKILL_CONTENT_HYDRATION: bool = False
CONTENT_REF_DEREFERENCE: bool = False

SEARCH_RESULT_ORDER_DETERMINISTIC: bool = True
INPUT_ORDER_IS_AUTHORITY: bool = False

LATEST_VERSION_FIRST: bool = False
SEMVER_ORDERING: bool = False

SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP: bool = False

DUPLICATE_SEARCH_DOCUMENT_FAIL_CLOSED: bool = True
POST_CONSTRUCTION_INPUT_MUTATION_CHANGES_SEARCH_INDEX: bool = False

SEARCH_LIMIT_REQUIRED: bool = True
SEARCH_LIMIT_IMPLEMENTATION_BOUND: bool = True

SEARCH_METADATA_ONLY: bool = True
SKILL_IDENTITY_FIELDS_UNCHANGED: bool = True

# ---------------------------------------------------------------------------
# Bounds — implementation-local, not Plan constants
# ---------------------------------------------------------------------------

# Reuse registry bound where appropriate
from aota_forge.work_plane.skill_registry import MAX_REGISTRY_ENTRIES as _MAX_REGISTRY_ENTRIES

MAX_SEARCH_DOCUMENTS: int = _MAX_REGISTRY_ENTRIES  # 64, bounded by registry
MAX_TITLE_LENGTH: int = 128
MAX_DESCRIPTION_LENGTH: int = 512

MAX_QUERY_LENGTH: int = 128
MAX_QUERY_TOKENS: int = 10

MAX_SEARCH_LIMIT: int = 32
# Implementation maximum for result truncation
SEARCH_RESULT_LIMIT_MAX: int = MAX_SEARCH_LIMIT
# Alias for compatibility
MAX_SEARCH_RESULTS: int = MAX_SEARCH_LIMIT

# ---------------------------------------------------------------------------
# Validation helpers — bounded, explicit, fail-closed
# ---------------------------------------------------------------------------

def _validate_skill_id(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"skill_id must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("skill_id must be non-empty")
    if not value.strip():
        raise ValueError("skill_id must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("skill_id must not have leading/trailing whitespace")
    if len(value) > MAX_SKILL_ID_LENGTH:
        raise ValueError(f"skill_id length ({len(value)}) exceeds maximum {MAX_SKILL_ID_LENGTH}")
    return value


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"version must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("version must be non-empty")
    if not value.strip():
        raise ValueError("version must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        raise ValueError("version must not have leading/trailing whitespace")
    if len(value) > MAX_VERSION_LENGTH:
        raise ValueError(f"version length ({len(value)}) exceeds maximum {MAX_VERSION_LENGTH}")
    return value


def _validate_namespace(value: object) -> AgentWorkRole:
    # Fail-closed via parse_agent_work_role
    return parse_agent_work_role(value)


def _validate_title(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"title must be a string or None, got {type(value).__name__}")
    # Reject whitespace-only when provided? Allow empty? Spec says optional explicit metadata; bounded.
    # We treat empty string as invalid if provided (must be non-empty when present)
    if value != value.strip():
        raise ValueError("title must not have leading/trailing whitespace")
    if not value.strip():
        raise ValueError("title when provided must be non-empty (whitespace-only rejected)")
    if len(value) > MAX_TITLE_LENGTH:
        raise ValueError(f"title length ({len(value)}) exceeds maximum {MAX_TITLE_LENGTH}")
    return value


def _validate_description(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"description must be a string or None, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError("description must not have leading/trailing whitespace")
    if not value.strip():
        raise ValueError("description when provided must be non-empty (whitespace-only rejected)")
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"description length ({len(value)}) exceeds maximum {MAX_DESCRIPTION_LENGTH}")
    return value


def _validate_query(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"query must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("query must be non-empty")
    if not value.strip():
        raise ValueError("query must be non-empty (whitespace-only rejected)")
    if value != value.strip():
        # Query with leading/trailing whitespace is not canonical but we
        # normalize deterministically via strip before validation?
        # Spec requires fail-closed on empty/whitespace-only but not necessarily
        # on surrounding whitespace — but to avoid aliasing, we reject if
        # value has leading/trailing whitespace? However typical search should
        # tolerate surrounding whitespace by stripping. For determinism we will
        # strip and then validate length, but also ensure raw value not
        # considered distinct. The spec says reject whitespace-only; we choose
        # to allow surrounding whitespace as it will be stripped, but to stay
        # fail-closed we can accept stripped version. Simpler: we normalize
        # by stripping and enforce length on stripped form. Allow raw with
        # surrounding whitespace as equivalent to stripped. But to prove
        # determinism we will treat them as same query after strip.
        # However spec says "query must be a real bounded non-empty string"
        # and whitespace-only fails. So we accept outer whitespace and strip.
        pass
    stripped = value.strip()
    if len(stripped) > MAX_QUERY_LENGTH:
        raise ValueError(f"query length ({len(stripped)}) exceeds maximum {MAX_QUERY_LENGTH}")
    # Also reject non-string already handled
    return stripped


def _tokenize_query(normalized_query: str) -> tuple[str, ...]:
    # normalized_query is already stripped
    # case-fold deterministically
    folded = normalized_query.casefold()
    # Split into bounded lexical tokens using simple deterministic semantics
    # Use whitespace split
    raw_tokens = folded.split()
    if not raw_tokens:
        raise ValueError("query must contain at least one token (whitespace-only rejected)")
    if len(raw_tokens) > MAX_QUERY_TOKENS:
        raise ValueError(f"query token count ({len(raw_tokens)}) exceeds maximum {MAX_QUERY_TOKENS}")
    # Validate each token non-empty and bounded (implicitly via query length)
    # Filter empty (split already does)
    tokens = tuple(raw_tokens)
    return tokens


def _validate_limit(value: object) -> int:
    if value is None:
        raise TypeError("limit is required (SEARCH_LIMIT_REQUIRED)")
    # Reject bool which is subclass of int
    if isinstance(value, bool):
        raise TypeError(f"limit must be an int, got {type(value).__name__}")
    if not isinstance(value, int) or type(value) is not int:
        raise TypeError(f"limit must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError("limit must be positive")
    if value > MAX_SEARCH_LIMIT:
        raise ValueError(f"limit ({value}) exceeds implementation maximum {MAX_SEARCH_LIMIT}")
    return value


# ---------------------------------------------------------------------------
# Search corpus / metadata projection — search-only immutable
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillSearchDocument:
    """Search-only immutable projection for lexical discovery.

    Does NOT extend SkillIdentity. Title/description are SEARCH_METADATA_ONLY.
    Corresponds to an accepted registry entry (namespace, skill_id, version).

    Fields:
        namespace   — AgentWorkRole member (canonical)
        skill_id    — bounded identifier (must match registry)
        version     — bounded version (must match registry)
        title       — optional bounded title (search metadata only)
        description — optional bounded description (search metadata only)

    Invariants:
        * Composite key is (namespace.value, skill_id, version)
        * No authority, no filesystem, no hydration
        * Bounded, deterministic, fail-closed
        * SEARCH_METADATA_ONLY=yes
        * SKILL_IDENTITY_FIELDS_UNCHANGED=yes
    """

    namespace: AgentWorkRole
    skill_id: str
    version: str
    title: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        ns = _validate_namespace(self.namespace)
        object.__setattr__(self, "namespace", ns)

        sid = _validate_skill_id(self.skill_id)
        object.__setattr__(self, "skill_id", sid)

        ver = _validate_version(self.version)
        object.__setattr__(self, "version", ver)

        t = _validate_title(self.title)
        object.__setattr__(self, "title", t)

        d = _validate_description(self.description)
        object.__setattr__(self, "description", d)

    @property
    def composite_key(self) -> tuple[str, str, str]:
        return (self.namespace.value, self.skill_id, self.version)

    @property
    def namespace_value(self) -> str:
        return self.namespace.value

    def searchable_text(self) -> str:
        """Deterministic searchable metadata concatenation, case-folded."""
        parts = [self.skill_id]
        if self.title is not None:
            parts.append(self.title)
        if self.description is not None:
            parts.append(self.description)
        # Join with space and casefold for matching
        return " ".join(parts).casefold()

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "namespace": self.namespace.value,
            "skill_id": self.skill_id,
            "version": self.version,
        }
        if self.title is not None:
            d["title"] = self.title
        if self.description is not None:
            d["description"] = self.description
        return d


# ---------------------------------------------------------------------------
# Search reference projection — ref-only / metadata-only
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillSearchResult:
    """Compact immutable reference-only search hit.

    Contains:
        namespace — canonical work role
        skill_id  — identifier
        version   — version
        digest    — deterministic registry digest (ref)
        title     — optional bounded title (if present in document)

    Does NOT include:
        full Skill content, opened SKILL.md, filesystem object,
        Tool authorization, execution authorization.

    A search hit is not an opened Skill.
    """

    namespace: AgentWorkRole
    skill_id: str
    version: str
    digest: str
    title: str | None = None

    def __post_init__(self) -> None:
        ns = _validate_namespace(self.namespace)
        object.__setattr__(self, "namespace", ns)

        sid = _validate_skill_id(self.skill_id)
        object.__setattr__(self, "skill_id", sid)

        ver = _validate_version(self.version)
        object.__setattr__(self, "version", ver)

        # Digest is already validated by registry (64 lower hex) but we validate shape
        if not isinstance(self.digest, str) or type(self.digest) is not str:
            raise TypeError(f"digest must be a string, got {type(self.digest).__name__}")
        # Digest must be 64 lower hex — reuse validation minimal
        if len(self.digest) != 64 or self.digest != self.digest.lower():
            # Allow any 64 lower hex; strict check
            import re

            if not re.fullmatch(r"^[0-9a-f]{64}$", self.digest):
                raise ValueError(f"digest must be 64 lowercase hex chars: {self.digest!r}")

        # Title optional bounded
        t = _validate_title(self.title)
        object.__setattr__(self, "title", t)

    @property
    def namespace_value(self) -> str:
        return self.namespace.value

    @property
    def composite_key(self) -> tuple[str, str, str]:
        return (self.namespace.value, self.skill_id, self.version)

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "namespace": self.namespace.value,
            "skill_id": self.skill_id,
            "version": self.version,
            "digest": self.digest,
        }
        if self.title is not None:
            d["title"] = self.title
        return d


# ---------------------------------------------------------------------------
# Lexical search index — immutable bounded projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LexicalSkillSearchIndex:
    """Immutable bounded lexical search index / projection.

    Constructed from:
        StaticSkillRegistry (source of membership truth)
        + explicit bounded search metadata (SkillSearchDocument list)

    Validates:
        * Each document corresponds to an accepted registry entry
        * No phantom membership creation
        * Duplicate composite keys fail closed
        * Bounded document count
        * Immutability / input-mutation isolation
        * No filesystem hydration

    Search:
        namespace-scoped, deterministic lexical, bounded limit,
        ref-only results, deterministic ordering (skill_id, version).

    Invariants match module markers.
    """

    _registry: StaticSkillRegistry
    _documents: tuple[SkillSearchDocument, ...]
    _index: Mapping[tuple[str, str, str], SkillSearchDocument]
    # Precomputed searchable texts for determinism (composite key -> searchable text)
    _searchable: Mapping[tuple[str, str, str], str]

    def __init__(
        self,
        registry: StaticSkillRegistry,
        documents: Iterable[SkillSearchDocument],
    ) -> None:
        # Validate registry
        if not isinstance(registry, StaticSkillRegistry):
            raise TypeError(f"registry must be StaticSkillRegistry, got {type(registry).__name__}")

        if documents is None:
            raise TypeError("documents must be iterable, got None")
        try:
            doc_list = list(documents)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TypeError(f"documents must be iterable, got {type(documents).__name__}") from exc

        if len(doc_list) > MAX_SEARCH_DOCUMENTS:
            raise ValueError(f"search document count ({len(doc_list)}) exceeds maximum {MAX_SEARCH_DOCUMENTS}")

        for idx, d in enumerate(doc_list):
            if not isinstance(d, SkillSearchDocument):
                raise TypeError(f"documents[{idx}] must be SkillSearchDocument, got {type(d).__name__}")

        # Canonical deterministic ordering for internal storage
        # Sort by composite key (namespace.value, skill_id, version)
        # plus title/description for tie determinism (but duplicate keys already rejected)
        def _sort_key(doc: SkillSearchDocument) -> tuple[str, str, str, str, str]:
            return (
                doc.namespace.value,
                doc.skill_id,
                doc.version,
                doc.title or "",
                doc.description or "",
            )

        sorted_docs = tuple(sorted(doc_list, key=_sort_key))

        # Duplicate detection + phantom membership check
        index: dict[tuple[str, str, str], SkillSearchDocument] = {}
        searchable: dict[tuple[str, str, str], str] = {}
        for doc in sorted_docs:
            key = doc.composite_key
            if key in index:
                raise ValueError(f"duplicate search document composite key rejected: {key!r}")
            # Verify membership in registry — no phantom index creation
            found = registry.get(doc.namespace, doc.skill_id, doc.version)
            if found is None:
                raise ValueError(f"phantom search document — registry entry absent for key {key!r}")
            index[key] = doc
            searchable[key] = doc.searchable_text()

        object.__setattr__(self, "_registry", registry)
        object.__setattr__(self, "_documents", sorted_docs)
        object.__setattr__(self, "_index", dict(index))
        object.__setattr__(self, "_searchable", dict(searchable))

    @property
    def registry(self) -> StaticSkillRegistry:
        return self._registry

    @property
    def documents(self) -> tuple[SkillSearchDocument, ...]:
        return self._documents

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def __len__(self) -> int:
        return len(self._documents)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._documents)

    # -----------------------------------------------------------------------
    # Search — namespace-scoped, bounded lexical, deterministic ordering
    # -----------------------------------------------------------------------

    def search(
        self,
        namespace: object,
        query: object,
        limit: object,
    ) -> tuple[SkillSearchResult, ...]:
        """Namespace-scoped bounded lexical search.

        Semantics:
            * namespace must be valid AgentWorkRole (fail-closed)
            * query must be bounded non-empty string, tokenized deterministically
            * limit must be bounded int within implementation maximum (required)
            * All normalized query tokens must occur in bounded searchable metadata
              (skill_id, title, description case-folded). No filesystem content.
            * Results are ref-only, deterministic ordered by (skill_id, version)
              regardless of input order. No latest/SemVer preference.
            * Result set bounded by limit (explicit truncation semantics).
            * No authority granted.

        Args:
            namespace: AgentWorkRole member or canonical string
            query: bounded lexical query string
            limit: bounded result limit (required, 1..MAX_SEARCH_LIMIT)

        Returns:
            Tuple of SkillSearchResult (immutable, bounded, deterministic).

        Raises:
            TypeError / ValueError fail-closed on invalid inputs.
        """
        ns = _validate_namespace(namespace)
        q_stripped = _validate_query(query)
        tokens = _tokenize_query(q_stripped)
        lim = _validate_limit(limit)

        # Filter to namespace
        candidates: list[SkillSearchDocument] = [
            doc for doc in self._documents if doc.namespace == ns
        ]

        # Lexical matching: all tokens must occur in searchable text (casefolded)
        matches: list[SkillSearchDocument] = []
        for doc in candidates:
            searchable = self._searchable[doc.composite_key]
            # Check all tokens present
            if all(token in searchable for token in tokens):
                matches.append(doc)

        # Deterministic ordering: by skill_id, version (lexicographic)
        # Since candidates already sorted deterministically, we sort matches again
        # explicitly to guarantee input-order independence.
        matches_sorted = sorted(matches, key=lambda d: (d.skill_id, d.version))

        # Bound by limit — explicit deterministic truncation
        bounded = matches_sorted[:lim]

        # Build ref-only results (lookup digest from registry)
        results: list[SkillSearchResult] = []
        for doc in bounded:
            entry = self._registry.get(doc.namespace, doc.skill_id, doc.version)
            # entry must exist (phantom check during construction ensures)
            assert entry is not None  # for type checker
            digest = entry.identity.digest
            result = SkillSearchResult(
                namespace=doc.namespace,
                skill_id=doc.skill_id,
                version=doc.version,
                digest=digest,
                title=doc.title,
            )
            results.append(result)

        return tuple(results)

    # Convenience alias: search with same semantics
    def search_lexical(
        self,
        namespace: object,
        query: object,
        limit: object,
    ) -> tuple[SkillSearchResult, ...]:
        return self.search(namespace, query, limit)


# ---------------------------------------------------------------------------
# Aliases for compatibility — same immutable implementation
# ---------------------------------------------------------------------------

SkillSearchIndex = LexicalSkillSearchIndex
BoundedLexicalSkillSearch = LexicalSkillSearchIndex
BoundedLexicalSkillSearchIndex = LexicalSkillSearchIndex
StaticLexicalSkillSearchIndex = LexicalSkillSearchIndex


# ---------------------------------------------------------------------------
# Builder helpers — explicit bounded declarative construction
# ---------------------------------------------------------------------------

def build_lexical_skill_search_index(
    registry: StaticSkillRegistry,
    documents: Iterable[SkillSearchDocument],
) -> LexicalSkillSearchIndex:
    """Build deterministic bounded lexical search index from explicit inputs."""
    return LexicalSkillSearchIndex(registry, documents)


def build_skill_search_index(
    registry: StaticSkillRegistry,
    documents: Iterable[SkillSearchDocument],
) -> LexicalSkillSearchIndex:
    return LexicalSkillSearchIndex(registry, documents)


def create_lexical_search_index(
    registry: StaticSkillRegistry,
    documents: Iterable[SkillSearchDocument],
) -> LexicalSkillSearchIndex:
    return LexicalSkillSearchIndex(registry, documents)


# Backwards alias
create_skill_search_index = create_lexical_search_index

__all__ = [
    "SkillSearchDocument",
    "SkillSearchResult",
    "LexicalSkillSearchIndex",
    "SkillSearchIndex",
    "BoundedLexicalSkillSearch",
    "BoundedLexicalSkillSearchIndex",
    "StaticLexicalSkillSearchIndex",
    "build_lexical_skill_search_index",
    "build_skill_search_index",
    "create_lexical_search_index",
    "create_skill_search_index",
    "MAX_SEARCH_DOCUMENTS",
    "MAX_TITLE_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_QUERY_LENGTH",
    "MAX_QUERY_TOKENS",
    "MAX_SEARCH_LIMIT",
    "SEARCH_RESULT_LIMIT_MAX",
    "MAX_SEARCH_RESULTS",
    "LEXICAL_SEARCH_V0",
    "VECTOR_SEARCH_REQUIRED",
    "EMBEDDING_DEPENDENCY_REQUIRED",
    "SEARCH_RESULT_BOUNDED",
    "SEARCH_RESULT_REF_ONLY",
    "SEARCH_NAMESPACE_SCOPED",
    "SEARCH_NAMESPACE_REQUIRED",
    "FOREIGN_NAMESPACE_RESULT",
    "SEARCH_MATCH_GRANTS_AUTHORITY",
    "SKILL_IS_AUTHORITY",
    "NAMESPACE_GRANTS_AUTHORITY",
    "SEARCH_RESULT_GRANTS_AUTHORITY",
    "SKILL_CONTENT_HYDRATION",
    "CONTENT_REF_DEREFERENCE",
    "SEARCH_RESULT_ORDER_DETERMINISTIC",
    "INPUT_ORDER_IS_AUTHORITY",
    "LATEST_VERSION_FIRST",
    "SEMVER_ORDERING",
    "SEARCH_INDEX_CAN_CREATE_SKILL_MEMBERSHIP",
    "DUPLICATE_SEARCH_DOCUMENT_FAIL_CLOSED",
    "POST_CONSTRUCTION_INPUT_MUTATION_CHANGES_SEARCH_INDEX",
    "SEARCH_LIMIT_REQUIRED",
    "SEARCH_LIMIT_IMPLEMENTATION_BOUND",
    "SEARCH_METADATA_ONLY",
    "SKILL_IDENTITY_FIELDS_UNCHANGED",
]
