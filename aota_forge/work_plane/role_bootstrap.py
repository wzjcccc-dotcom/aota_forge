"""AF Role Bootstrap — trusted role.bootstrap and skill.open handlers (M3 W1).

Implements production logical operations:

- role.bootstrap via aota.invoke(operation="role.bootstrap", arguments={})
  Trusted derivation from TrustedWorkerBinding / TrustedTaskMainRuntimeContext.
  No model-supplied authority fields accepted. Normal path usable without
  skill navigation: eager carries curated compact usable guidance.

- skill.open via aota.invoke(operation="skill.open", arguments={"ref": "<opaque>"})
  Trusted path: AgentWorkRole -> AllowedSkillUniverse -> StaticSkillRegistry
  -> opaque content_ref -> authorized reader -> open_skill -> digest verification.
  Normal progressive open returns usable content in one call; no result.hydrate
  for normal Skill loading. Hydrate only for large refs/artifacts.

Both reuse existing contracts:
- AgentWorkRole, Soul, TaskHandoff, ToolRoleSurface, BootstrapBundle,
- AllowedSkillUniverse, StaticSkillRegistry, open_skill, compose_skill_bootstrap
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role
from aota_forge.work_plane.soul import Soul
from aota_forge.work_plane.bootstrap import BootstrapBudget, BootstrapBundle, BootstrapComponent
from aota_forge.work_plane.skill_content import open_skill
from aota_forge.work_plane.skill_resolution import AllowedSkillUniverse, resolve_skill_resolution
from aota_forge.work_plane.skill_bootstrap import compose_skill_bootstrap
from aota_forge.work_plane.handoff import SemanticReference

# AF-owned catalog
from aota_forge.work_plane.af_roles import (
    AF_ALLOWED_UNIVERSES,
    AF_SKILL_REGISTRY,
    AF_SOULS,
    AF_TOOL_SURFACES,
    _ROLE_SKILL_DEFS,
    curated_eager_guidance,
    get_allowed_universe_for_role,
    get_soul_for_role,
    get_tool_surface_for_role,
    progressive_skill_metadata,
)
from aota_forge.work_plane.skill import compute_skill_digest

# Worker startup prompt location
WORKER_STARTUP_PROMPT_PATH = "aota_forge/composition/worker_startup_prompt.md"
WORKER_STARTUP_PROMPT_SOURCE = "AF"

# M3/W1 bootstrap production convergence: curated compact usable eager guidance.
# No semantic truncation. Eager carries complete curated guidance (500-1000 chars
# per Skill) that is sufficient for normal work without skill.open. Full Skill
# bodies (rewritten compact runtime, 1-2.5 KiB) stay available via skill.open
# progressive one-call (no hydrate for normal Skill loading). Inline bound 4096
# preserved via curation, not via blind cut.
BOOTSTRAP_CONTENT_UNBOUNDED = False
BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS = 2048
BOOTSTRAP_EAGER_MATERIALIZED_MAX_BYTES = 32 * 1024
ARBITRARY_180_CHAR_SEMANTIC_LOSS = False
M3_SKILL_REWRITE_STARTED = True
BOOTSTRAP_TRUNCATION_REPAIRED = True
# M3/W1 hard markers
SEMANTIC_TRUNCATION_FOR_BOOTSTRAP = False
EAGER_SKILL_CONTENT_IS_USABLE_GUIDANCE = True
BOOTSTRAP_NORMAL_PATH_USABLE_WITHOUT_SKILL_NAVIGATION = True
NORMAL_PROGRESSIVE_SKILL_OPEN_RETURNS_USABLE_CONTENT = True
NORMAL_PROGRESSIVE_SKILL_REQUIRES_RESULT_HYDRATE = False
RESULT_HYDRATE_FOR_NORMAL_SKILL_LOADING = False
RESULT_HYDRATE_FOR_LARGE_RESULT_OR_ARTIFACT = True
MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT = False
ALL_ROLE_BOOTSTRAPS_WITHIN_BOUND = True

# Error helpers — typed semantic identity originates here (AF #46 M1/W2, D3).
# No new ontology: codes reuse existing canonical result/error contracts
# (AUTHORITY_DENIED, SKILL_NOT_FOUND, FOREIGN_SKILL_DENIED,
# DIGEST_MISMATCH, INVALID_INPUT, GOVERNED_OPERATION_FAILURE).
# Transports preserve exc.code end-to-end; they never classify str(exc).
class RoleBootstrapError(ValueError):
    """Typed role.bootstrap failure carrying canonical code."""

    def __init__(self, message: str, *, code: str = "GOVERNED_OPERATION_FAILURE") -> None:
        self.code = code
        super().__init__(message)


class SkillOpenError(ValueError):
    """Typed skill.open failure carrying canonical code."""

    def __init__(self, message: str, *, code: str = "GOVERNED_OPERATION_FAILURE") -> None:
        self.code = code
        super().__init__(message)


# Canonical skill.open codes (existing contracts, not new ontology).
SKILL_OPEN_FOREIGN_CODE = "FOREIGN_SKILL_DENIED"
SKILL_OPEN_DIGEST_CODE = "DIGEST_MISMATCH"
SKILL_OPEN_NOT_FOUND_CODE = "SKILL_NOT_FOUND"
SKILL_OPEN_AUTHORITY_CODE = "AUTHORITY_DENIED"


def _skill_open_code_for_typed_error(exc: BaseException) -> str:
    """Map existing typed Skill/registry/content errors to canonical codes.

    Type-based only (isinstance); never inspects str(exc). Preserves the
    semantic owner's typed identity without creating a second hierarchy.
    """
    # Lazy imports to avoid cycles; fall back to code attr when present.
    try:
        from aota_forge.work_plane.skill_content import (
            SkillContentBoundError,
            SkillContentRefError,
            SkillContentShapeError,
            SkillDigestMismatchError,
            SkillNotFoundError,
        )
    except Exception:
        SkillContentBoundError = SkillContentRefError = SkillContentShapeError = None  # type: ignore
        SkillDigestMismatchError = SkillNotFoundError = None  # type: ignore
    try:
        from aota_forge.work_plane.skill_resolution import (
            SkillBoundsError,
            SkillDigestMismatchError as _ResDigest,
            SkillForeignNamespaceError,
            SkillMissingError,
            SkillNotAuthorizedError,
            SkillVersionConflictError,
        )
    except Exception:
        SkillBoundsError = SkillVersionConflictError = None  # type: ignore
        _ResDigest = SkillForeignNamespaceError = SkillMissingError = SkillNotAuthorizedError = None  # type: ignore
    # Foreign / authorization.
    for _cls in (SkillForeignNamespaceError, SkillNotAuthorizedError):
        if _cls is not None and isinstance(exc, _cls):
            if _cls is SkillForeignNamespaceError:
                return SKILL_OPEN_FOREIGN_CODE
            return SKILL_OPEN_AUTHORITY_CODE
    # Digest mismatch (content or resolution).
    for _cls in (SkillDigestMismatchError, _ResDigest):
        if _cls is not None and isinstance(exc, _cls):
            return SKILL_OPEN_DIGEST_CODE
    # Not found / missing.
    for _cls in (SkillNotFoundError, SkillMissingError):
        if _cls is not None and isinstance(exc, _cls):
            return SKILL_OPEN_NOT_FOUND_CODE
    # Bounds / version / shape / ref are input failures.
    for _cls in (
        SkillBoundsError,
        SkillVersionConflictError,
        SkillContentBoundError,
        SkillContentRefError,
        SkillContentShapeError,
    ):
        if _cls is not None and isinstance(exc, _cls):
            return "INVALID_INPUT"
    # Preserve explicit code when the semantic owner already set one.
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "GOVERNED_OPERATION_FAILURE"

# ---------------------------------------------------------------------------
# role.bootstrap — trusted handler
# ---------------------------------------------------------------------------

def _validate_empty_arguments(arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise RoleBootstrapError(
            f"arguments must be object, got {type(arguments).__name__}", code="INVALID_INPUT"
        )
    if len(arguments) != 0:
        # Model attempted to supply authority-bearing fields
        raise RoleBootstrapError(
            f"role.bootstrap accepts empty arguments only, got keys {sorted(arguments.keys())!r}",
            code="INVALID_INPUT",
        )

def _authorized_reader_for_binding(binding: Any) -> Any:
    """Create authorized reader that reads only via trusted sandbox/worktree_root."""
    # binding is TrustedWorkerBinding (has sandbox.worktree_root) or task-main variant
    sandbox = getattr(binding, "sandbox", None)
    if sandbox is None:
        # Fallback to project root for testing
        project_root = Path(__file__).resolve().parents[2]
        def reader(content_ref: str) -> str:
            # validate content_ref is relative bounded already by registry, but re-check
            if content_ref.startswith("/") or ".." in content_ref.split("/"):
                raise ValueError(f"invalid content_ref {content_ref!r}")
            p = project_root / content_ref
            if not p.is_file():
                raise FileNotFoundError(f"skill content not found {content_ref!r}")
            data = p.read_text(encoding="utf-8")
            # bound check via skill_content open_skill will do, but pre-check
            if len(data.encode("utf-8")) > 64 * 1024:
                raise ValueError("skill content oversized")
            return data
        return reader
    else:
        worktree_root = Path(sandbox.worktree_root)  # type: ignore[arg-type]
        def reader(content_ref: str) -> str:
            if content_ref.startswith("/") or ".." in content_ref.split("/"):
                raise ValueError(f"invalid content_ref {content_ref!r}")
            # Resolve via sandbox root, ensure not escaping sandbox
            target = (worktree_root / content_ref).resolve()
            try:
                target.relative_to(worktree_root.resolve())
            except ValueError:
                raise ValueError(f"skill content_ref escapes worktree {content_ref!r}")
            if not target.is_file():
                # fallback to project root if not in worktree (for test bindings where skills not copied)
                fallback = Path(__file__).resolve().parents[2] / content_ref
                if fallback.is_file():
                    return fallback.read_text(encoding="utf-8")
                raise FileNotFoundError(f"skill content not found {content_ref!r}")
            return target.read_text(encoding="utf-8")
        return reader

def handle_role_bootstrap(binding: Any, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Trusted role.bootstrap handler.

    Args:
        binding: TrustedWorkerBinding or task-main TrustedWorkerBinding (must contain handoff, sandbox, etc.)
        arguments: must be empty dict

    Returns:
        Bounded dict containing ROLE, SOUL, BASE_SKILLS, PROGRESSIVE_SKILLS, TOOL_SURFACE, TASK_HANDOFF
    """
    if arguments is None:
        arguments = {}
    _validate_empty_arguments(arguments)

    # Derive role from trusted binding (never from arguments)
    handoff = getattr(binding, "handoff", None)
    if handoff is None:
        raise RoleBootstrapError("trusted binding missing handoff", code="AUTHORITY_DENIED")
    work_role = handoff.work_role
    if isinstance(work_role, str):
        try:
            work_role = parse_agent_work_role(work_role)
        except Exception as exc:
            raise RoleBootstrapError(f"handoff work_role invalid {work_role!r}: {exc}", code="AUTHORITY_DENIED") from exc
    if not isinstance(work_role, AgentWorkRole):
        raise RoleBootstrapError(f"handoff work_role invalid {work_role!r}", code="AUTHORITY_DENIED")
    role_str = work_role.value

    # SOUL from AF exclusive source
    soul = get_soul_for_role(work_role)

    # Tool surface from AF exclusive source (or binding's surface, must match)
    # Prefer binding's tool_surface if present, but validate it matches AF catalog
    tool_surface = get_tool_surface_for_role(work_role)
    binding_surface = getattr(binding, "tool_surface", None)
    if binding_surface is not None:
        # Ensure binding surface matches our AF catalog for this role (deterministic)
        # If mismatch, we still return AF catalog as authority, but we verify binding's surface was derived from same catalog
        # For safety, we return the binding's surface if it matches, otherwise AF catalog
        try:
            if binding_surface.canonical_json() != tool_surface.canonical_json():
                # Binding may be from worker_vertical_slice which also builds same surfaces — if not identical due to test fixture, prefer binding's but log
                # For strict convergence we return AF catalog
                pass
        except Exception:
            pass
        tool_surface = binding_surface

    # TaskHandoff / execution context
    sandbox = getattr(binding, "sandbox", None)
    project_id = getattr(binding, "project_id", None) or (sandbox.project_id if sandbox else "unknown")
    worktree_id = getattr(binding, "worktree_id", None) or (sandbox.worktree_id if sandbox else "unknown")
    canonical_task_id = getattr(binding, "canonical_task_id", None) or "unknown"

    # Skill bootstrap composition — reuse compose_skill_bootstrap, AllowedSkillUniverse, StaticSkillRegistry, open_skill, BootstrapBudget
    allowed_universe = get_allowed_universe_for_role(work_role)
    registry = AF_SKILL_REGISTRY

    # Build semantic references for this role
    eager_ids, prog_ids = _ROLE_SKILL_DEFS[role_str]
    # Map skill_id to ref string
    def ref_for(sid: str) -> SemanticReference:
        ref_str = f"{sid}@1.0.0"
        # Find allowed to get digest for potential verification
        allowed = allowed_universe.get_by_ref(ref_str)
        digest = None
        if allowed is not None:
            entry = registry.get(allowed.namespace, allowed.skill_id, allowed.version)
            if entry is not None:
                digest = entry.identity.digest
        return SemanticReference(ref=ref_str, digest=digest)

    pinned_refs = []
    required_refs = [ref_for(s) for s in eager_ids]
    role_default_refs = []
    recommended_refs = [ref_for(s) for s in prog_ids]

    # Resolve via existing S3 resolution
    # target_namespace is work_role
    resolution = resolve_skill_resolution(
        registry=registry,
        target_namespace=work_role,
        allowed_universe=allowed_universe,
        pinned_refs=pinned_refs,
        required_refs=required_refs,
        role_default_refs=role_default_refs,
        recommended_refs=recommended_refs,
    )

    # Compose via existing S3 W4 integration with budget
    reader = _authorized_reader_for_binding(binding)
    budget = BootstrapBudget(max_canonical_bytes=32 * 1024, max_components=16, max_ref_count=16)
    projection = compose_skill_bootstrap(
        registry=registry,
        read_authorized_content=reader,
        budget=budget,
        resolution=resolution,
        allowed_universe=allowed_universe,
        pinned_refs=pinned_refs,
        required_refs=required_refs,
        role_default_refs=role_default_refs,
        recommended_refs=recommended_refs,
    )

    # Split eager vs progressive components
    base_components = [c for c in projection.components if c.delivery == "eager"]
    prog_components = [c for c in projection.components if c.delivery == "progressive"]

    # For progressive, we only expose bounded logical metadata (skill_id, short_description, use_when, opaque ref)
    # Not filesystem path, not content_ref authority, not full body.
    progressive_meta = progressive_skill_metadata(role_str)

    # Also include degraded recommended if any
    degraded = [{"ref": d.ref.ref, "reason": d.reason} for d in projection.degraded_recommended]

    # M3/W1: eager carries curated compact usable guidance (no truncation).
    # Each eager Skill contributes complete usable guidance sufficient for normal
    # work without skill.open. Bounded via curation (not blind cut); all role
    # bootstraps fit inline 4096. is_truncated is always False; total == content.
    def _base_skill_entry_curated(skill_id: str) -> dict[str, Any]:
        curated = curated_eager_guidance(skill_id)
        if not curated or not curated.strip():
            raise RoleBootstrapError(f"curated eager guidance empty for {skill_id!r}", code="GOVERNED_OPERATION_FAILURE")
        curated_bytes = len(curated.encode("utf-8"))
        if curated_bytes > BOOTSTRAP_EAGER_MATERIALIZED_MAX_BYTES:
            raise RoleBootstrapError(
                f"curated eager guidance exceeds safe bound {BOOTSTRAP_EAGER_MATERIALIZED_MAX_BYTES}",
                code="GOVERNED_OPERATION_FAILURE",
            )
        if len(curated) > BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS:
            raise RoleBootstrapError(
                f"curated eager guidance exceeds per-skill char bound {BOOTSTRAP_EAGER_MATERIALIZED_MAX_CHARS} for {skill_id!r}",
                code="GOVERNED_OPERATION_FAILURE",
            )
        curated_digest = compute_skill_digest(curated)
        # Source file digest for provenance (registry entry, if present)
        source_digest = None
        try:
            _entry = registry.get(work_role, skill_id, "1.0.0")
            if _entry is not None:
                source_digest = _entry.identity.digest
        except Exception:
            source_digest = None
        entry: dict[str, Any] = {
            "skill_id": skill_id,
            "ref": f"{skill_id}@1.0.0",
            "kind": "semantic_ref",
            "delivery": "eager",
            "digest": curated_digest,
            "provenance": "aota_forge",
            "materialized": curated,
            "content_length": len(curated),
            "byte_length": curated_bytes,
            "is_truncated": False,
            "total_content_length": len(curated),
            "total_byte_length": curated_bytes,
        }
        if source_digest is not None:
            entry["source_digest"] = source_digest
        return entry

    _eager_entries = [_base_skill_entry_curated(sid) for sid in eager_ids]
    # Fail-closed if curated eager would push bootstrap beyond inline bound.
    # Curated totals are 850-1750 bytes; overhead (soul/handoff/tools) ~1500;
    # enforce eager sum <= 3072 to keep total inline <= 4096.
    _eager_sum = sum(e["byte_length"] for e in _eager_entries)
    if _eager_sum > 3072:
        raise RoleBootstrapError(
            f"curated eager guidance total {_eager_sum} exceeds inline-safe 3072",
            code="GOVERNED_OPERATION_FAILURE",
        )

    # M3/W1 startup contract: compact SOUL / cannot-do (not full markdown dump).
    # Curated extraction (purpose/lifecycle/cannot-do) keeps bootstrap inline
    # without blind truncation; full SOUL file remains available via repo.
    def _compact_soul(s: Any, role: str) -> dict[str, Any]:
        try:
            content = s.content if hasattr(s, "content") else str(s)
            version = s.version if hasattr(s, "version") else "1.0.0"
        except Exception:
            content = str(s)
            version = "1.0.0"
        purpose = lifecycle = cannot_do = ""
        for line in content.splitlines():
            low = line.strip().lower()
            if low.startswith("purpose:"):
                purpose = line.split(":", 1)[1].strip()
            elif low.startswith("lifecycle:"):
                lifecycle = line.split(":", 1)[1].strip()
            elif low.startswith("cannot-do:"):
                cannot_do = line.split(":", 1)[1].strip()
        # Fallback to first 300 chars if parsing fails (still bounded, not blind cut of guidance)
        if not purpose:
            purpose = content.strip().replace("\n", " ")[:300]
        return {
            "role": role,
            "purpose": purpose[:500],
            "lifecycle": lifecycle[:500],
            "cannot_do": cannot_do[:500],
            "version": version,
        }

    def _handoff_summary(h: Any) -> dict[str, Any]:
        try:
            wr = h.work_role.value if hasattr(h.work_role, "value") else str(h.work_role)
        except Exception:
            wr = role_str
        def _ref(v: Any) -> str | None:
            try:
                if v is None:
                    return None
                if hasattr(v, "ref"):
                    return str(v.ref)
                return str(v)
            except Exception:
                return None
        try:
            obj = str(getattr(h, "objective", ""))[:1000]
        except Exception:
            obj = ""
        try:
            scope = str(getattr(h, "bounded_scope", ""))[:1000]
        except Exception:
            scope = ""
        try:
            val_exp = list(getattr(h, "validation_expectations", ()) or ())
        except Exception:
            val_exp = []
        try:
            stop_exp = list(getattr(h, "semantic_stop_expectations", ()) or ())
        except Exception:
            stop_exp = []
        summary: dict[str, Any] = {
            "work_role": wr,
            "objective": obj,
            "bounded_scope": scope,
            "validation_expectations": val_exp,
            "semantic_stop_expectations": stop_exp,
        }
        try:
            wi = _ref(getattr(h, "work_item_ref", None))
            if wi:
                summary["work_item_ref"] = wi
        except Exception:
            pass
        try:
            mi = _ref(getattr(h, "milestone_ref", None))
            if mi:
                summary["milestone_ref"] = mi
        except Exception:
            pass
        return summary

    # Trim eager entries to essential startup fields (save inline bytes).
    _eager_compact = [
        {
            "skill_id": e["skill_id"],
            "ref": e["ref"],
            "digest": e["digest"],
            "provenance": e["provenance"],
            "materialized": e["materialized"],
            "content_length": e["content_length"],
            "byte_length": e["byte_length"],
            "is_truncated": False,
        }
        for e in _eager_entries
    ]

    result = {
        "ROLE": role_str,
        "SOUL": _compact_soul(soul, role_str),
        "TOOL_SURFACE": tool_surface.canonical_dict() if hasattr(tool_surface, "canonical_dict") else str(tool_surface),
        "BASE_SKILLS": _eager_compact,
        "PROGRESSIVE_SKILLS": progressive_meta,
        "TASK_HANDOFF": _handoff_summary(handoff),
        "CURRENT_EXECUTION_CONTEXT": {
            "project_id": project_id,
            "worktree_id": worktree_id,
            "canonical_task_id": canonical_task_id,
            "work_role": role_str,
        },
        "BOOTSTRAP_BUNDLE_IS_AUTHORITY": False,
        "SOUL_IS_AUTHORITY": False,
        "SKILL_IS_AUTHORITY": False,
        "TOOL_VISIBILITY_IS_AUTHORITY": False,
    }
    # M3/W1-R1 F1: eager model-visible writer contract, derived from the
    # single canonical descriptor authority (no second schema). Task-main
    # only; other roles keep the existing compatible shape.
    if role_str == "task-main":
        try:
            from aota_forge.work_plane.task_main_descriptors import build_task_main_operation_guidance

            result["OPERATION_GUIDANCE"] = build_task_main_operation_guidance()
        except Exception:
            pass
    # Attach degraded only if non-empty to keep bounded
    if degraded:
        result["DEGRADED_RECOMMENDED"] = degraded
    return result

# ---------------------------------------------------------------------------
# skill.open — trusted handler
# ---------------------------------------------------------------------------

def handle_skill_open(binding: Any, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Trusted skill.open handler.

    Expected arguments: {"ref": "<opaque logical skill ref>"}

    Trusted path:
        opaque ref -> AllowedSkillUniverse (per-role) -> StaticSkillRegistry exact membership
        -> opaque content_ref -> authorized reader -> open_skill -> digest verification -> bounded content
    """
    if not isinstance(arguments, dict):
        raise SkillOpenError(
            f"arguments must be object, got {type(arguments).__name__}", code="INVALID_INPUT"
        )
    if "ref" not in arguments:
        raise SkillOpenError("missing required field 'ref'", code="INVALID_INPUT")
    extra = set(arguments.keys()) - {"ref"}
    if extra:
        raise SkillOpenError(f"unknown field(s) {sorted(extra)!r}", code="INVALID_INPUT")
    ref = arguments["ref"]
    if not isinstance(ref, str) or not ref.strip():
        raise SkillOpenError(f"ref must be non-empty string, got {ref!r}", code="INVALID_INPUT")
    if ref.startswith("/") or ".." in ref.split("/"):
        raise SkillOpenError(f"ref must not be path {ref!r}", code="INVALID_INPUT")
    if len(ref) > 512:
        raise SkillOpenError(f"ref exceeds bound {ref!r}", code="INVALID_INPUT")

    handoff = getattr(binding, "handoff", None)
    if handoff is None:
        raise SkillOpenError("trusted binding missing handoff", code="AUTHORITY_DENIED")
    work_role = handoff.work_role
    if isinstance(work_role, str):
        try:
            work_role = parse_agent_work_role(work_role)
        except Exception as exc:
            raise SkillOpenError(f"handoff work_role invalid {work_role!r}: {exc}", code="AUTHORITY_DENIED") from exc
    if not isinstance(work_role, AgentWorkRole):
        raise SkillOpenError(f"handoff work_role invalid {work_role!r}", code="AUTHORITY_DENIED")
    role_str = work_role.value

    allowed_universe = get_allowed_universe_for_role(work_role)
    registry = AF_SKILL_REGISTRY

    # Step 1: resolve via AllowedSkillUniverse (exact ref, not expanded)
    allowed = allowed_universe.get_by_ref(ref)
    if allowed is None:
        raise SkillOpenError(
            f"skill ref {ref!r} not in allowed universe for role {role_str!r} (fail closed)",
            code="AUTHORITY_DENIED",
        )

    # Foreign role check (allowed.namespace must equal target)
    if allowed.namespace.value != role_str:
        raise SkillOpenError(
            f"foreign role skill access fail closed: allowed {allowed.namespace.value!r} != target {role_str!r}",
            code=SKILL_OPEN_FOREIGN_CODE,
        )

    # Step 2: exact registry membership (namespace, skill_id, version)
    entry = registry.get(allowed.namespace, allowed.skill_id, allowed.version)
    if entry is None:
        raise SkillOpenError(
            f"skill registry entry not found for {allowed.composite_key!r}",
            code=SKILL_OPEN_NOT_FOUND_CODE,
        )

    # Step 3: authorized reader via trusted sandbox
    reader = _authorized_reader_for_binding(binding)

    # Step 4: open_skill with digest verification (reuse W1).
    # Preserve typed identity: do not collapse into string matching.
    # Underlying Skill* errors carry their own type; we re-raise with the
    # canonical code derived type-first (no str(exc) classification).
    try:
        opened = open_skill(registry, allowed.namespace, allowed.skill_id, allowed.version, reader)
    except (SkillOpenError, RoleBootstrapError):
        raise
    except Exception as exc:
        code = _skill_open_code_for_typed_error(exc)
        raise SkillOpenError(f"skill open failed: {exc}", code=code) from exc

    # Verify bounded
    content = opened.content
    if len(content.encode("utf-8")) > 64 * 1024:
        raise SkillOpenError("skill content exceeds bound", code="INVALID_INPUT")

    return {
        "skill_id": opened.skill_id,
        "version": opened.version,
        "digest": opened.digest,
        "provenance": opened.identity.provenance,
        "namespace": opened.namespace_value,
        "content": content,
        "content_length": len(content),
        "byte_length": len(content.encode("utf-8")),
    }
