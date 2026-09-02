"""Deterministic Skill resolution for handoff-pinned / required / role_default / recommended (S3 M2-W3).

Architecture (normative)
------------------------
already-authorized Skill universe
        ↓
deterministic S3 selection
        ↓
resolved ordered Skill references

Forbidden: Skill request → S3 decides authority

Authority gate is INPUT, not W3 logic. W3 MUST NOT implement filesystem sandbox,
AGENTS discovery, project authority, Tool authorization. W3 receives bounded
already-authorized Skill universe (immutable projection) and selects within it.

Invariants
----------
* ALLOWED_UNIVERSE_IS_INPUT=yes
* W3_EXPANDS_ALLOWED_UNIVERSE=no
* TARGET_NAMESPACE_REQUIRED=yes
* SEMANTIC_REFERENCE_REMAINS_GENERIC=yes (no skill_id/version added to S1)
* S1_TASK_HANDOFF_CHANGE_REQUIRED=no
* S1_BOOTSTRAP_CONTRACT_CHANGE_REQUIRED=no
* PINNED_SKILL_OUTSIDE_ALLOWED_UNIVERSE=FAIL_CLOSED
* PINNED_SKILL_MISSING=FAIL_CLOSED
* PINNED_DIGEST_MISMATCH=FAIL_CLOSED
* HANDOFF_PIN_GRANTS_AUTHORITY=no
* SEARCH_RESULT_BYPASSES_AUTHORITY_GATE=no
* SELECTION_PRECEDENCE=pinned>required>role_default>recommended
* RESOLUTION_ORDER_DETERMINISTIC=yes
* INPUT_ORDER_IS_AUTHORITY=no
* DUPLICATE_RESOLUTION_OUTPUT=no
* HIGHEST_SELECTION_PRECEDENCE_WINS=yes
* LATEST_VERSION_SELECTION=no
* SEMVER_REQUIRED=no
* VERSION_FALLBACK=no
* FOREIGN_NAMESPACE_SELECTION=no
* SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED=yes
* W3_SKILL_CONTENT_HYDRATION=no
* REQUIRED_OR_PINNED_SILENT_TRUNCATION=no

Bounds — implementation-local, not Plan constants, bounded deterministic fail-closed.
No silent truncation for required/pinned.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Any

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.skill import MAX_SKILL_ID_LENGTH, MAX_VERSION_LENGTH, compute_skill_digest
from aota_forge.work_plane.skill_registry import StaticSkillRegistry, SkillRegistryEntry, MAX_REGISTRY_ENTRIES
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff

# ---------------------------------------------------------------------------
# Invariant markers (descriptive)
# ---------------------------------------------------------------------------

ALLOWED_UNIVERSE_IS_INPUT: bool = True
W3_EXPANDS_ALLOWED_UNIVERSE: bool = False
TARGET_NAMESPACE_REQUIRED: bool = True
S1_TASK_HANDOFF_CHANGE_REQUIRED: bool = False
S1_BOOTSTRAP_CONTRACT_CHANGE_REQUIRED: bool = False

PINNED_SKILL_OUTSIDE_ALLOWED_UNIVERSE_FAIL_CLOSED: bool = True
PINNED_SKILL_MISSING_FAIL_CLOSED: bool = True
PINNED_DIGEST_MISMATCH_FAIL_CLOSED: bool = True
HANDOFF_PIN_GRANTS_AUTHORITY: bool = False

REQUIRED_INVALID_FAIL_CLOSED: bool = True
RECOMMENDED_UNAVAILABLE_NON_AUTHORITATIVE_DEGRADATION: bool = True
RECOMMENDED_SKILL_AUTO_PROMOTED_TO_REQUIRED: bool = False

SELECTION_PRECEDENCE_PRESENT: bool = True
# precedence string for acceptance boundary
SELECTION_PRECEDENCE = "pinned>required>role_default>recommended"

RESOLUTION_ORDER_DETERMINISTIC: bool = True
INPUT_ORDER_IS_AUTHORITY: bool = False

DUPLICATE_RESOLUTION_OUTPUT: bool = False
HIGHEST_SELECTION_PRECEDENCE_WINS: bool = True

LATEST_VERSION_SELECTION: bool = False
SEMVER_REQUIRED: bool = False
VERSION_FALLBACK: bool = False

FOREIGN_NAMESPACE_SELECTION: bool = False
SEARCH_RESULT_BYPASSES_AUTHORITY_GATE: bool = False

SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED: bool = True
W3_SKILL_CONTENT_HYDRATION: bool = False
SEMANTIC_REFERENCE_MUTATED: bool = False
TASK_HANDOFF_MUTATED: bool = False
BOOTSTRAP_CONTRACT_MUTATED: bool = False

S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False
S2_UNACCEPTED_CANDIDATE_DEPENDENCY: bool = False

SKILL_IS_AUTHORITY: bool = False
SEARCH_MATCH_GRANTS_AUTHORITY: bool = False

# ---------------------------------------------------------------------------
# Bounds — implementation-local, deterministic
# ---------------------------------------------------------------------------

MAX_ALLOWED_UNIVERSE_SIZE: int = MAX_REGISTRY_ENTRIES  # 64
MAX_PINNED_REFS: int = 16
MAX_REQUIRED_REFS: int = 16
MAX_ROLE_DEFAULT_REFS: int = 16
MAX_RECOMMENDED_REFS: int = 32
MAX_SELECTED_SKILLS: int = MAX_REGISTRY_ENTRIES
MAX_DEGRADED_RECOMMENDED: int = 32
MAX_REF_LENGTH: int = 512  # same as SemanticReference

# For test introspection
ALLOWED_SKILL_REF_IS_AUTHORITY: bool = False
ALLOWED_UNIVERSE_MEMBERSHIP_IS_PRECOMPUTED_AUTHORITY_GATE: bool = True

# ---------------------------------------------------------------------------
# Exceptions — small deterministic local failure model
# ---------------------------------------------------------------------------

class SkillResolutionError(RuntimeError):
    """Fail-closed Skill resolution error (mandatory)."""


class SkillNotAuthorizedError(SkillResolutionError):
    pass


class SkillMissingError(SkillResolutionError):
    pass


class SkillDigestMismatchError(SkillResolutionError):
    pass


class SkillVersionConflictError(SkillResolutionError):
    pass


class SkillForeignNamespaceError(SkillResolutionError):
    pass


class SkillBoundsError(SkillResolutionError):
    pass


# ---------------------------------------------------------------------------
# Validation helpers
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


def _validate_ref(value: object | None, allow_none: bool = True) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise TypeError("ref must be a string, got None")
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"ref must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError("ref when provided must be non-empty")
    if len(stripped) > MAX_REF_LENGTH:
        raise ValueError(f"ref length ({len(stripped)}) exceeds maximum {MAX_REF_LENGTH}")
    if value != value.strip():
        raise ValueError("ref must not have leading/trailing whitespace")
    return stripped


def _validate_namespace(value: object) -> AgentWorkRole:
    return parse_agent_work_role(value)


def _normalize_semantic_refs(refs: object, max_count: int, label: str) -> tuple[SemanticReference, ...]:
    if refs is None:
        raise TypeError(f"{label} must be iterable, got None")
    try:
        lst = list(refs)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{label} must be iterable, got {type(refs).__name__}") from exc
    if len(lst) > max_count:
        raise SkillBoundsError(f"{label} count ({len(lst)}) exceeds maximum {max_count}")
    out: list[SemanticReference] = []
    for idx, item in enumerate(lst):
        if isinstance(item, SemanticReference):
            # Validate via construction to ensure normalized
            sr = SemanticReference(ref=item.ref, digest=item.digest)
        else:
            try:
                sr = SemanticReference.from_value(item)
            except Exception as exc:
                raise TypeError(f"{label}[{idx}] must be SemanticReference, got {type(item).__name__}: {exc}") from exc
        out.append(sr)
    # Return immutable tuple; caller may sort later deterministically
    return tuple(out)


# ---------------------------------------------------------------------------
# Allowed universe representation — minimal immutable projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllowedSkill:
    """Immutable already-authorized Skill projection.

    Each allowed Skill identifies exact M1 registry key (namespace, skill_id, version)
    and may carry bounded logical ref string for matching S1 SemanticReference.ref.

    Fields:
        ref       — bounded logical ref string used for matching (optional, not authority)
        namespace — AgentWorkRole member
        skill_id  — bounded identifier
        version   — bounded opaque version

    Invariants:
        * ref is not authority (ALLOWED_SKILL_REF_IS_AUTHORITY=no)
        * membership is precomputed authority gate
    """

    ref: str | None
    namespace: AgentWorkRole
    skill_id: str
    version: str

    def __post_init__(self) -> None:
        # ref optional bounded
        r = _validate_ref(self.ref, allow_none=True)
        object.__setattr__(self, "ref", r)
        ns = _validate_namespace(self.namespace)
        object.__setattr__(self, "namespace", ns)
        sid = _validate_skill_id(self.skill_id)
        object.__setattr__(self, "skill_id", sid)
        ver = _validate_version(self.version)
        object.__setattr__(self, "version", ver)

    @property
    def composite_key(self) -> tuple[str, str, str]:
        return (self.namespace.value, self.skill_id, self.version)

    @property
    def namespace_value(self) -> str:
        return self.namespace.value


@dataclass(frozen=True)
class AllowedSkillUniverse:
    """Immutable bounded already-authorized Skill universe (input gate).

    Contains exact authorized Skills already validated by combination of
    trusted project/worktree authority + TaskHandoff authority + AGENTS policy.
    W3 consumes gate; does not create it.

    Immutability:
        * Input mutation after construction does not affect universe.
        * Deterministic canonical ordering.
        * Duplicate composite keys fail closed.
        * Duplicate logical refs (ambiguous) fail closed.
    """

    _skills: tuple[AllowedSkill, ...]
    _by_key: Mapping[tuple[str, str, str], AllowedSkill]
    _by_ref: Mapping[str, AllowedSkill]

    def __init__(self, skills: Iterable[AllowedSkill]) -> None:
        if skills is None:
            raise TypeError("skills must be iterable, got None")
        try:
            lst = list(skills)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TypeError(f"skills must be iterable, got {type(skills).__name__}") from exc
        if len(lst) > MAX_ALLOWED_UNIVERSE_SIZE:
            raise SkillBoundsError(f"allowed universe size ({len(lst)}) exceeds maximum {MAX_ALLOWED_UNIVERSE_SIZE}")
        for idx, s in enumerate(lst):
            if not isinstance(s, AllowedSkill):
                raise TypeError(f"skills[{idx}] must be AllowedSkill, got {type(s).__name__}")
        # Deterministic ordering: (namespace.value, skill_id, version, ref or "")
        def _key(a: AllowedSkill):
            return (a.namespace.value, a.skill_id, a.version, a.ref or "")
        sorted_skills = tuple(sorted(lst, key=_key))
        # Duplicate composite key check
        by_key: dict[tuple[str, str, str], AllowedSkill] = {}
        by_ref: dict[str, AllowedSkill] = {}
        for sk in sorted_skills:
            ck = sk.composite_key
            if ck in by_key:
                raise ValueError(f"duplicate allowed Skill composite key: {ck!r}")
            by_key[ck] = sk
            if sk.ref is not None:
                if sk.ref in by_ref:
                    # duplicate logical ref ambiguous — fail closed
                    raise ValueError(f"duplicate allowed Skill logical ref (ambiguous): {sk.ref!r}")
                by_ref[sk.ref] = sk
        object.__setattr__(self, "_skills", sorted_skills)
        object.__setattr__(self, "_by_key", dict(by_key))
        object.__setattr__(self, "_by_ref", dict(by_ref))

    @property
    def skills(self) -> tuple[AllowedSkill, ...]:
        return self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._skills)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, AllowedSkill):
            return item.composite_key in self._by_key and self._by_key[item.composite_key] == item
        return False

    def get_by_ref(self, ref: str) -> AllowedSkill | None:
        return self._by_ref.get(ref)

    def get_by_key(self, namespace: object, skill_id: str, version: str) -> AllowedSkill | None:
        try:
            ns = _validate_namespace(namespace)
        except Exception:
            return None
        key = (ns.value, skill_id, version)
        return self._by_key.get(key)

    def contains_key(self, namespace: object, skill_id: str, version: str) -> bool:
        return self.get_by_key(namespace, skill_id, version) is not None


# Aliases for conceptual naming
AllowedSkillSet = AllowedSkillUniverse
AlreadyAuthorizedSkillUniverse = AllowedSkillUniverse


# ---------------------------------------------------------------------------
# Resolution result — immutable bounded projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DegradedRecommended:
    """Non-authoritative degraded recommended Skill record."""
    ref: SemanticReference
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SemanticReference):
            raise TypeError(f"ref must be SemanticReference, got {type(self.ref).__name__}")
        if not isinstance(self.reason, str) or type(self.reason) is not str:
            raise TypeError(f"reason must be string, got {type(self.reason).__name__}")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")


@dataclass(frozen=True)
class SkillResolutionResult:
    """Immutable deterministic Skill resolution result.

    Fields:
        selected — tuple of SkillRegistryEntry ordered by precedence then deterministic tie-break
        degraded_recommended — tuple of DegradedRecommended or SemanticReference degraded
        target_namespace — target AgentWorkRole
    """

    selected: tuple[SkillRegistryEntry, ...]
    degraded_recommended: tuple[DegradedRecommended, ...]
    target_namespace: AgentWorkRole

    def __post_init__(self) -> None:
        if not isinstance(self.target_namespace, AgentWorkRole):
            raise TypeError(f"target_namespace must be AgentWorkRole, got {type(self.target_namespace).__name__}")
        if not isinstance(self.selected, tuple):
            raise TypeError("selected must be tuple")
        for idx, e in enumerate(self.selected):
            if not isinstance(e, SkillRegistryEntry):
                raise TypeError(f"selected[{idx}] must be SkillRegistryEntry, got {type(e).__name__}")
        if not isinstance(self.degraded_recommended, tuple):
            raise TypeError("degraded_recommended must be tuple")
        for idx, d in enumerate(self.degraded_recommended):
            if not isinstance(d, DegradedRecommended):
                raise TypeError(f"degraded_recommended[{idx}] must be DegradedRecommended, got {type(d).__name__}")
        if len(self.selected) > MAX_SELECTED_SKILLS:
            raise SkillBoundsError(f"selected count ({len(self.selected)}) exceeds maximum {MAX_SELECTED_SKILLS}")
        if len(self.degraded_recommended) > MAX_DEGRADED_RECOMMENDED:
            raise SkillBoundsError(f"degraded count ({len(self.degraded_recommended)}) exceeds maximum {MAX_DEGRADED_RECOMMENDED}")

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    @property
    def degraded_count(self) -> int:
        return len(self.degraded_recommended)


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

def _resolve_one(
    sref: SemanticReference,
    universe: AllowedSkillUniverse,
    registry: StaticSkillRegistry,
    target_ns: AgentWorkRole,
) -> tuple[AllowedSkill, SkillRegistryEntry] | tuple[None, str]:
    """Resolve single SemanticReference via allowed universe and registry.

    Returns (allowed_skill, registry_entry) on success, else (None, reason) for degradation.
    For mandatory failure, caller will convert reason to fail-closed exception.
    """
    # Lookup allowed universe by exact ref
    allowed = universe.get_by_ref(sref.ref)
    if allowed is None:
        return None, f"pinned/outside_allowed_universe: ref {sref.ref!r} not in allowed universe"
    # Foreign namespace check
    if allowed.namespace != target_ns:
        return None, f"foreign_namespace: allowed {allowed.namespace.value!r} != target {target_ns.value!r}"
    # Registry membership
    entry = registry.get(allowed.namespace, allowed.skill_id, allowed.version)
    if entry is None:
        return None, f"missing_registry: {allowed.composite_key!r} not in registry"
    # Digest verification if supplied
    if sref.digest is not None:
        if sref.digest != entry.identity.digest:
            return None, f"digest_mismatch: supplied {sref.digest!r} != registry {entry.identity.digest!r}"
    return allowed, entry


def resolve_skill_resolution(
    registry: StaticSkillRegistry,
    target_namespace: object,
    allowed_universe: AllowedSkillUniverse,
    pinned_refs: Iterable[SemanticReference] = (),
    required_refs: Iterable[SemanticReference] = (),
    role_default_refs: Iterable[SemanticReference] = (),
    recommended_refs: Iterable[SemanticReference] = (),
) -> SkillResolutionResult:
    """Deterministic Skill resolution inside already-authorized universe.

    Args:
        registry: StaticSkillRegistry for membership validation
        target_namespace: AgentWorkRole member or string (required)
        allowed_universe: Already-authorized Skill universe (input gate)
        pinned_refs: handoff-pinned SemanticReference refs (from TaskHandoff.skill_refs)
        required_refs: authoritative required refs
        role_default_refs: role bootstrap default refs
        recommended_refs: optional recommended refs (non-authoritative degradation)

    Returns:
        SkillResolutionResult with deterministic ordered selected and degraded.

    Fail-closed (raises SkillResolutionError) for:
        * unknown pin / outside allowed universe
        * required outside allowed universe / missing registry
        * pinned digest mismatch
        * required/pinned version conflict
        * required/pinned foreign namespace
        * duplicate ambiguous logical ref (via universe construction)
        * bounds exceeded / silent truncation attempt
    """
    # Validate registry
    if not isinstance(registry, StaticSkillRegistry):
        raise TypeError(f"registry must be StaticSkillRegistry, got {type(registry).__name__}")
    # Validate target namespace required
    if target_namespace is None:
        raise TypeError("target_namespace is required (TARGET_NAMESPACE_REQUIRED)")
    try:
        tns = _validate_namespace(target_namespace)
    except Exception as exc:
        raise SkillResolutionError(f"invalid target_namespace: {exc}") from exc
    # Validate allowed universe
    if not isinstance(allowed_universe, AllowedSkillUniverse):
        raise TypeError(f"allowed_universe must be AllowedSkillUniverse, got {type(allowed_universe).__name__}")
    # Normalize refs — bounded, deterministic, no silent truncation
    pinned = _normalize_semantic_refs(pinned_refs, MAX_PINNED_REFS, "pinned_refs")
    required = _normalize_semantic_refs(required_refs, MAX_REQUIRED_REFS, "required_refs")
    role_defaults = _normalize_semantic_refs(role_default_refs, MAX_ROLE_DEFAULT_REFS, "role_default_refs")
    recommended = _normalize_semantic_refs(recommended_refs, MAX_RECOMMENDED_REFS, "recommended_refs")

    # No silent truncation checks already done; also check total selected bound later.

    # Resolve mandatory categories fail-closed
    # Use lists to collect entries per category (dedup later but need version conflict check)
    pinned_entries: list[SkillRegistryEntry] = []
    required_entries: list[SkillRegistryEntry] = []
    role_entries: list[SkillRegistryEntry] = []
    # Keep track of pinned/required/role allowed skills for version conflict (need skill_id mapping)
    # For version conflict detection, need to consider all mandatory resolved distinct keys
    mandatory_by_skill: dict[tuple[str, str], set[str]] = {}  # (ns.value, skill_id) -> set(version)

    def _mandatory_resolve(refs: tuple[SemanticReference, ...], label: str) -> list[SkillRegistryEntry]:
        entries: list[SkillRegistryEntry] = []
        for sr in refs:
            allowed = allowed_universe.get_by_ref(sr.ref)
            if allowed is None:
                raise SkillResolutionError(f"{label} outside allowed universe: ref {sr.ref!r}")
            if allowed.namespace != tns:
                raise SkillForeignNamespaceError(f"{label} foreign namespace: allowed {allowed.namespace.value!r} != target {tns.value!r} for ref {sr.ref!r}")
            entry = registry.get(allowed.namespace, allowed.skill_id, allowed.version)
            if entry is None:
                raise SkillMissingError(f"{label} missing registry entry for {allowed.composite_key!r} ref {sr.ref!r}")
            if sr.digest is not None and sr.digest != entry.identity.digest:
                raise SkillDigestMismatchError(f"{label} digest mismatch for ref {sr.ref!r}: supplied {sr.digest!r} != {entry.identity.digest!r}")
            entries.append(entry)
            # track version conflict
            key = (entry.namespace.value, entry.skill_id)
            vers = mandatory_by_skill.setdefault(key, set())
            vers.add(entry.version)
            if len(vers) > 1:
                raise SkillVersionConflictError(
                    f"mandatory version conflict for skill_id {entry.skill_id!r} namespace {entry.namespace.value!r}: versions {sorted(vers)!r}"
                )
        return entries

    pinned_entries = _mandatory_resolve(pinned, "pinned")
    required_entries = _mandatory_resolve(required, "required")
    role_entries = _mandatory_resolve(role_defaults, "role_default")

    # Process recommended — non-authoritative degradation
    degraded: list[DegradedRecommended] = []
    recommended_entries: list[SkillRegistryEntry] = []
    for sr in recommended:
        allowed = allowed_universe.get_by_ref(sr.ref)
        if allowed is None:
            degraded.append(DegradedRecommended(ref=sr, reason="outside_allowed_universe"))
            continue
        if allowed.namespace != tns:
            degraded.append(DegradedRecommended(ref=sr, reason="foreign_namespace"))
            continue
        entry = registry.get(allowed.namespace, allowed.skill_id, allowed.version)
        if entry is None:
            degraded.append(DegradedRecommended(ref=sr, reason="missing_registry"))
            continue
        if sr.digest is not None and sr.digest != entry.identity.digest:
            degraded.append(DegradedRecommended(ref=sr, reason="digest_mismatch"))
            continue
        # Check version conflict against mandatory: if same skill_id exists in mandatory with different version, degrade
        key = (entry.namespace.value, entry.skill_id)
        if key in mandatory_by_skill:
            # If mandatory has that skill_id, check if version matches any mandatory version
            # mandatory_by_skill[key] is set of mandatory versions; if entry.version not in that set, it's conflicting
            if entry.version not in mandatory_by_skill[key]:
                degraded.append(DegradedRecommended(ref=sr, reason="version_conflict_with_mandatory"))
                continue
            else:
                # same exact skill already selected via mandatory — will be deduped, so degrade this duplicate recommended
                # But per spec highest precedence wins, so we should not select duplicate again. Record as degraded? Actually spec says duplicate same Skill appears once at highest precedence, not degraded. Should not count as degraded.
                # If same exact composite key already in mandatory, we skip adding to recommended (dedup)
                # Check if composite key already in mandatory
                pass
        # Duplicate exact key within mandatory: we will dedup, but recommended duplicate of mandatory should be skipped, not degraded
        # Determine if this exact composite key already in mandatory entries
        mandatory_keys = {e.composite_key for e in pinned_entries + required_entries + role_entries}
        if entry.composite_key in mandatory_keys:
            # duplicate same exact Skill via multiple sources — highest precedence wins, do not add as separate, not degraded
            continue
        # Check recommended internal version conflict: same skill_id different version among recommended themselves
        # We need to track recommended version conflict similarly but degrade rather than fail closed
        # For simplicity, if recommended_entries already has same skill_id with different version, degrade this one
        existing_recommended_versions: dict[tuple[str, str], set[str]] = {}
        for e in recommended_entries:
            k = (e.namespace.value, e.skill_id)
            existing_recommended_versions.setdefault(k, set()).add(e.version)
        if key in existing_recommended_versions and entry.version not in existing_recommended_versions[key]:
            degraded.append(DegradedRecommended(ref=sr, reason="recommended_version_conflict"))
            continue
        recommended_entries.append(entry)

    # Deduplicate within mandatory already via version conflict check, but need to handle same exact composite key duplicates -> keep one
    # Build precedence map for final selected (composite_key -> (entry, precedence))
    # precedence: 0 pinned, 1 required, 2 role_default, 3 recommended
    precedence_map: dict[tuple[str, str, str], tuple[SkillRegistryEntry, int]] = {}
    # Helper to insert category sorted
    def insert_category(entries: list[SkillRegistryEntry], prec: int):
        # Sort deterministically by (skill_id, version) regardless of input order
        sorted_entries = sorted(entries, key=lambda e: (e.skill_id, e.version, e.namespace.value, e.identity.digest))
        for e in sorted_entries:
            ck = e.composite_key
            if ck not in precedence_map:
                precedence_map[ck] = (e, prec)
            # else higher precedence already present — keep existing

    insert_category(pinned_entries, 0)
    insert_category(required_entries, 1)
    insert_category(role_entries, 2)
    insert_category(recommended_entries, 3)

    # Check bounds on selected
    if len(precedence_map) > MAX_SELECTED_SKILLS:
        raise SkillBoundsError(f"selected count ({len(precedence_map)}) exceeds maximum {MAX_SELECTED_SKILLS}")
    if len(degraded) > MAX_DEGRADED_RECOMMENDED:
        raise SkillBoundsError(f"degraded count ({len(degraded)}) exceeds maximum {MAX_DEGRADED_RECOMMENDED}")

    # Build ordered selected tuple: group by precedence, sorted within group
    # Need to collect entries per precedence based on map's precedence value
    buckets: dict[int, list[SkillRegistryEntry]] = {0: [], 1: [], 2: [], 3: []}
    for ck, (entry, prec) in precedence_map.items():
        buckets[prec].append(entry)
    ordered_selected: list[SkillRegistryEntry] = []
    for prec in [0, 1, 2, 3]:
        lst = buckets[prec]
        lst_sorted = sorted(lst, key=lambda e: (e.skill_id, e.version, e.namespace.value, e.identity.digest))
        ordered_selected.extend(lst_sorted)

    # Degraded ordering deterministic: sort by (ref, digest or "")
    degraded_sorted = tuple(sorted(degraded, key=lambda d: (d.ref.ref, d.ref.digest or "", d.reason)))

    result = SkillResolutionResult(
        selected=tuple(ordered_selected),
        degraded_recommended=degraded_sorted,
        target_namespace=tns,
    )
    return result


def resolve_from_handoff(
    registry: StaticSkillRegistry,
    target_namespace: object,
    allowed_universe: AllowedSkillUniverse,
    handoff: TaskHandoff,
    required_refs: Iterable[SemanticReference] = (),
    role_default_refs: Iterable[SemanticReference] = (),
    recommended_refs: Iterable[SemanticReference] = (),
) -> SkillResolutionResult:
    """Convenience wrapper using TaskHandoff.skill_refs as pinned."""
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    pinned = handoff.skill_refs
    return resolve_skill_resolution(
        registry=registry,
        target_namespace=target_namespace,
        allowed_universe=allowed_universe,
        pinned_refs=pinned,
        required_refs=required_refs,
        role_default_refs=role_default_refs,
        recommended_refs=recommended_refs,
    )


# Convenience aliases
resolve_skills = resolve_skill_resolution
resolve_governed_skills = resolve_skill_resolution

# Helper to build allowed universe from registry + ref mapping (for tests)
def build_allowed_universe(
    skills: Iterable[AllowedSkill],
) -> AllowedSkillUniverse:
    return AllowedSkillUniverse(skills)


__all__ = [
    "AllowedSkill",
    "AllowedSkillUniverse",
    "AllowedSkillSet",
    "AlreadyAuthorizedSkillUniverse",
    "DegradedRecommended",
    "SkillResolutionResult",
    "SkillResolutionError",
    "SkillNotAuthorizedError",
    "SkillMissingError",
    "SkillDigestMismatchError",
    "SkillVersionConflictError",
    "SkillForeignNamespaceError",
    "SkillBoundsError",
    "resolve_skill_resolution",
    "resolve_from_handoff",
    "resolve_skills",
    "resolve_governed_skills",
    "build_allowed_universe",
    "MAX_ALLOWED_UNIVERSE_SIZE",
    "MAX_PINNED_REFS",
    "MAX_REQUIRED_REFS",
    "MAX_ROLE_DEFAULT_REFS",
    "MAX_RECOMMENDED_REFS",
    "MAX_SELECTED_SKILLS",
    "MAX_DEGRADED_RECOMMENDED",
    # markers
    "ALLOWED_UNIVERSE_IS_INPUT",
    "W3_EXPANDS_ALLOWED_UNIVERSE",
    "TARGET_NAMESPACE_REQUIRED",
    "S1_TASK_HANDOFF_CHANGE_REQUIRED",
    "S1_BOOTSTRAP_CONTRACT_CHANGE_REQUIRED",
    "PINNED_SKILL_OUTSIDE_ALLOWED_UNIVERSE_FAIL_CLOSED",
    "PINNED_SKILL_MISSING_FAIL_CLOSED",
    "PINNED_DIGEST_MISMATCH_FAIL_CLOSED",
    "HANDOFF_PIN_GRANTS_AUTHORITY",
    "REQUIRED_INVALID_FAIL_CLOSED",
    "RECOMMENDED_UNAVAILABLE_NON_AUTHORITATIVE_DEGRADATION",
    "RECOMMENDED_SKILL_AUTO_PROMOTED_TO_REQUIRED",
    "RESOLUTION_ORDER_DETERMINISTIC",
    "INPUT_ORDER_IS_AUTHORITY",
    "DUPLICATE_RESOLUTION_OUTPUT",
    "HIGHEST_SELECTION_PRECEDENCE_WINS",
    "LATEST_VERSION_SELECTION",
    "SEMVER_REQUIRED",
    "VERSION_FALLBACK",
    "FOREIGN_NAMESPACE_SELECTION",
    "SEARCH_RESULT_BYPASSES_AUTHORITY_GATE",
    "SELECTED_SKILL_REGISTRY_MEMBERSHIP_REQUIRED",
    "W3_SKILL_CONTENT_HYDRATION",
    "SEMANTIC_REFERENCE_MUTATED",
    "TASK_HANDOFF_MUTATED",
    "S1_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "S2_UNACCEPTED_CANDIDATE_DEPENDENCY",
    "SKILL_IS_AUTHORITY",
    "SEARCH_MATCH_GRANTS_AUTHORITY",
]
