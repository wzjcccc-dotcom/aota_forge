"""TaskHandoff runtime convergence helpers (M2/W1 foundation).

Reuses the frozen S1 TaskHandoff contract (handoff.py) without mutating the
S1 high-conflict file. Provides bounded Worker execution input validation:

  TASK_HANDOFF_IS_BOUNDED, scope containment, bounded-projection checks.

No Plan authority duplication: handoff carries refs, not Plan bodies.

M1/W1 bounded Worker scope contract (AF repair #45, defect I40-B003/F1):

  The production task-main Work projection carries usable bounded execution
  semantics (objective + bounded_scope + validation/stop expectations) as a
  trusted operator-supplied projection (WorkSemanticProjection) transported
  through the existing bootstrap channel. The runtime faithfully transports
  that projection into the existing TaskHandoff; it never re-interprets
  natural-language Plan semantics and never invents requirements.

  When the projection is absent or insufficient, resolution fails closed
  with WorkScopeInsufficientError (WORK_SCOPE_INSUFFICIENT): no Worker is
  dispatched and no scope-free generic handoff is substituted silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.work_plane.handoff import (
    MAX_EXPECTATION_LENGTH,
    MAX_EXPECTATIONS_COUNT,
    MAX_OBJECTIVE_LENGTH,
    MAX_SCOPE_LENGTH,
    SemanticReference,
    TaskHandoff,
)

# M1/W1 bounded Worker scope contract markers (AF repair #45, I40-B003/F1).
# The typed semantic handoff remains the execution projection; freeform or
# startup prompts are never authority; the handoff is a bounded projection
# of Plan authority, never Plan authority itself; a scope-insufficient Work
# Item must never dispatch a Worker.
TASK_HANDOFF_IS_PLAN_AUTHORITY = False
TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY = False
WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION = False
WORKER_NORMAL_EXECUTION_REQUIRES_PLAN_FETCH = False
FULL_PLAN_DUMP_TO_WORKER = False
OBJECTIVE_ONLY_WORKER_INSTRUCTION = False
SCOPE_INSUFFICIENT_DISPATCH_ALLOWED = False
WORK_SCOPE_INSUFFICIENT_DISPATCH_ALLOWED = False

# Typed fail-closed code for scope-insufficient Work projection. Carried on
# WorkScopeInsufficientError.code so the MCP transport maps it without
# importing this module (see mcp_transport._map_task_main_exception).
WORK_SCOPE_INSUFFICIENT = "WORK_SCOPE_INSUFFICIENT"


class WorkScopeInsufficientError(ValueError):
    """task-main cannot derive enough Work semantics: do not dispatch Worker."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{WORK_SCOPE_INSUFFICIENT}: {detail}")
        self.code = WORK_SCOPE_INSUFFICIENT


# M2/W1 runtime convergence markers (bounded execution projection only).
TASK_HANDOFF_IS_BOUNDED = True
TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE = False
WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE = False
TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY = False
WORKER_STARTUP_PROMPT_IS_AUTHORITY = False

# M2/W2 validation integrity firewall (M1 frozen acceptance freeze).
# The TaskHandoff freezes acceptance + validation expectations; Coder
# cannot redefine or weaken them to pass, nor silently weaken existing
# tests (test modifications require typed justification).
TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS = True
TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS = True
CODER_CAN_REDEFINE_ACCEPTANCE = False
CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS = False
CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS = False
TEST_MODIFICATION_REQUIRES_JUSTIFICATION = True


def validate_handoff_scope_containment(*, handoff_scope: str, worker_scope: str) -> None:
    """Fail-closed scope containment: worker scope must equal handoff scope.

    W1 foundation: effective worker scope is derived from TaskHandoff
    bounded_scope (see worker_vertical_slice policy derivation). Any attempt
    to widen scope beyond the handoff fails closed; narrowing is also denied
    here to keep the seam exact (W2 may relax with explicit policy).
    """
    if not isinstance(handoff_scope, str) or not handoff_scope.strip():
        raise ValueError("handoff_scope must be non-empty")
    if not isinstance(worker_scope, str) or not worker_scope.strip():
        raise ValueError("worker_scope must be non-empty")
    if worker_scope.strip() != handoff_scope.strip():
        raise ValueError(
            f"worker scope {worker_scope!r} must equal handoff bounded_scope {handoff_scope!r}; "
            "scope expansion/narrowing denied"
        )


def assert_handoff_is_bounded_projection(handoff: TaskHandoff) -> None:
    """Validate that a handoff is a bounded projection (no Plan authority duplication)."""
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    for attr in ("project_ref", "plan_ref", "milestone_ref", "work_item_ref"):
        val = getattr(handoff, attr)
        if val is not None and not isinstance(val, SemanticReference):
            raise TypeError(f"{attr} must be SemanticReference or None")
    if not handoff.bounded_scope.strip():
        raise ValueError("bounded_scope must be non-empty")


def frozen_acceptance_expectations(handoff: TaskHandoff) -> tuple[str, ...]:
    """Return the frozen acceptance surface (task-main owned, Coder immutable).

    The frozen S1 TaskHandoff contract carries acceptance intent as the
    exact objective text plus the validation expectations tuple (there is
    no separate acceptance field by design — no Plan authority
    duplication). Both are frozen: the Coder Result must preserve them
    exactly.
    """
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    return (handoff.objective, *tuple(handoff.validation_expectations))


def frozen_validation_expectations(handoff: TaskHandoff) -> tuple[str, ...]:
    """Return the frozen validation expectations (task-main owned, Coder immutable)."""
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    return tuple(handoff.validation_expectations)


def assert_result_preserves_frozen_expectations(
    *,
    handoff: TaskHandoff,
    claimed_acceptance: tuple[str, ...] | list[str],
    claimed_validation: tuple[str, ...] | list[str],
) -> None:
    """Validation integrity firewall: fail closed when a Coder Result
    claims redefined or weakened acceptance/validation expectations.

    Delegates to the typed coder_lifecycle firewall (no string-policy
    heuristics); the narrowest reliable seam is exact expectation-set
    equality against the frozen TaskHandoff.
    """
    from aota_forge.work_plane.coder_lifecycle import assert_acceptance_expectations_frozen

    assert_acceptance_expectations_frozen(
        handoff_acceptance=frozen_acceptance_expectations(handoff),
        handoff_validation=frozen_validation_expectations(handoff),
        claimed_acceptance=tuple(claimed_acceptance),
        claimed_validation=tuple(claimed_validation),
    )


# ---------------------------------------------------------------------------
# M1/W1 bounded Worker scope contract: trusted Work semantic projection
# ---------------------------------------------------------------------------

# Maximum Work items carrying explicit semantics in one bootstrap projection.
# Bounded to the existing graph capacity (not a database, not a queue).
MAX_WORK_SEMANTICS_ENTRIES: int = 64


def _sanitize_scope_segment(value: str, *, max_len: int, fallback: str) -> str:
    """Lowercase alnum slug used by the generic fallback template.

    Identical rule to the task-main generic derivation: keep alnum plus
    ``-_``, replace anything else (including ``/``) with ``-``.
    """
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.lower())[:max_len]
    return slug or fallback


def generic_fallback_scope_template(work_item_id: str, milestone_ref: str) -> str:
    """Exact scope string the generic (semantics-free) derivation produces.

    Single source of truth shared by the task-main generic derivation and
    the Worker-usability gate below, so the gate can structurally recognize
    a scope-free fallback without hardcoding English phrases.
    """
    mid = (milestone_ref or "").strip() or "M1"
    wid = (work_item_id or "").strip() or "W1"
    safe_mid = _sanitize_scope_segment(mid, max_len=32, fallback="m1")
    safe_wi = _sanitize_scope_segment(wid.replace("/", "-"), max_len=48, fallback="w1")
    return f"{safe_mid}/{safe_wi}/bounded-scope"


def generic_fallback_task_kind(work_item_id: str, milestone_ref: str) -> str:
    """Exact task_kind the generic (semantics-free) derivation produces."""
    mid = (milestone_ref or "").strip() or "M1"
    wid = (work_item_id or "").strip() or "W1"
    safe_mid = _sanitize_scope_segment(mid, max_len=32, fallback="m1")
    safe_wi = _sanitize_scope_segment(wid.replace("/", "-"), max_len=48, fallback="w1")
    task_kind = f"{safe_mid}-{safe_wi}-implementation"
    return task_kind[:100] if len(task_kind) > 100 else task_kind


def _require_bounded_text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise WorkScopeInsufficientError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise WorkScopeInsufficientError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise WorkScopeInsufficientError(
            f"{label} length ({len(stripped)}) exceeds maximum {max_length}"
        )
    return stripped


def _require_bounded_text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise WorkScopeInsufficientError(f"{label} must be a tuple or list, got {type(value).__name__}")
    if len(value) > MAX_EXPECTATIONS_COUNT:
        raise WorkScopeInsufficientError(
            f"{label} count ({len(value)}) exceeds maximum {MAX_EXPECTATIONS_COUNT}"
        )
    if not value:
        raise WorkScopeInsufficientError(f"{label} must be non-empty")
    items: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or type(item) is not str:
            raise WorkScopeInsufficientError(f"{label}[{idx}] must be a string")
        s = item.strip()
        if not s:
            raise WorkScopeInsufficientError(f"{label}[{idx}] must be a non-empty string")
        if len(s) > MAX_EXPECTATION_LENGTH:
            raise WorkScopeInsufficientError(
                f"{label}[{idx}] length ({len(s)}) exceeds maximum {MAX_EXPECTATION_LENGTH}"
            )
        items.append(s)
    return tuple(items)


@dataclass(frozen=True)
class WorkSemanticProjection:
    """Trusted bounded Work execution semantics (operator channel, never model).

    Carries exactly the four semantic fields task-main has already reasoned
    about for one governed Work Item:

    * objective: short goal (never the sole construction semantic)
    * bounded_scope: actual bounded implementation scope
    * validation_expectations: what must be validated before the Worker returns
    * semantic_stop_expectations: when the Worker must stop/escalate

    Reference identities (project/Plan/Milestone/Work) are NOT carried here;
    the resolver binds them from the trusted runtime binding, so a projection
    can never smuggle foreign authority. The full Plan body is never carried
    (bounded projection only; semantic compaction over character slicing is
    the operator's derivation duty, enforced here by the handoff bounds).
    """

    objective: str
    bounded_scope: str
    validation_expectations: tuple[str, ...]
    semantic_stop_expectations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "objective", _require_bounded_text(self.objective, "objective", max_length=MAX_OBJECTIVE_LENGTH)
        )
        object.__setattr__(
            self,
            "bounded_scope",
            _require_bounded_text(self.bounded_scope, "bounded_scope", max_length=MAX_SCOPE_LENGTH),
        )
        object.__setattr__(
            self,
            "validation_expectations",
            _require_bounded_text_list(self.validation_expectations, "validation_expectations"),
        )
        object.__setattr__(
            self,
            "semantic_stop_expectations",
            _require_bounded_text_list(self.semantic_stop_expectations, "semantic_stop_expectations"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "bounded_scope": self.bounded_scope,
            "validation_expectations": list(self.validation_expectations),
            "semantic_stop_expectations": list(self.semantic_stop_expectations),
        }

    @classmethod
    def from_dict(cls, data: Any) -> WorkSemanticProjection:
        if not isinstance(data, Mapping):
            raise WorkScopeInsufficientError(
                f"work semantic projection must be a mapping, got {type(data).__name__}"
            )
        extra = set(data.keys()) - {
            "objective",
            "bounded_scope",
            "validation_expectations",
            "semantic_stop_expectations",
        }
        if extra:
            raise WorkScopeInsufficientError(
                f"unknown field(s) in work semantic projection: {sorted(extra)}"
            )
        for req in ("objective", "bounded_scope", "validation_expectations", "semantic_stop_expectations"):
            if req not in data:
                raise WorkScopeInsufficientError(
                    f"missing required field in work semantic projection: {req!r}"
                )
        return cls(
            objective=data["objective"],
            bounded_scope=data["bounded_scope"],
            validation_expectations=tuple(data["validation_expectations"]),
            semantic_stop_expectations=tuple(data["semantic_stop_expectations"]),
        )


def parse_work_semantics_table(raw: Any) -> dict[str, WorkSemanticProjection]:
    """Strictly parse the bootstrap ``work_semantics`` table (fail-closed).

    Keys are governed Work Item ids; values are WorkSemanticProjection
    mappings. Empty table is allowed (means: no explicit semantics).
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise WorkScopeInsufficientError(
            f"work_semantics must be a mapping, got {type(raw).__name__}"
        )
    if len(raw) > MAX_WORK_SEMANTICS_ENTRIES:
        raise WorkScopeInsufficientError(
            f"work_semantics entries ({len(raw)}) exceed maximum {MAX_WORK_SEMANTICS_ENTRIES}"
        )
    table: dict[str, WorkSemanticProjection] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise WorkScopeInsufficientError("work_semantics keys must be non-empty strings")
        if len(key.strip()) > 128:
            raise WorkScopeInsufficientError(f"work_semantics key too long: {key!r}")
        table[key.strip()] = WorkSemanticProjection.from_dict(val)
    return table


def is_worker_usable_handoff(handoff: TaskHandoff, *, work_item_id: str, milestone_ref: str) -> bool:
    """Bounded deterministic Worker-usability criterion (structural only).

    A handoff is Worker-usable exactly when each semantic field carries its
    own responsibility (no vague LLM-quality judgment):

    * objective is a non-empty short goal distinct from bounded_scope
      (objective must not carry everything alone);
    * bounded_scope is non-empty and distinct from the generic scope-free
      fallback template (no generic boilerplate substitution);
    * validation_expectations and semantic_stop_expectations are non-empty;
    * required trusted refs (project, plan, milestone, work item) are present
      and the milestone/work refs match the governed identities.

    No English phrases are hardcoded; the fallback template is the code's own
    generic-derivation rule (see generic_fallback_scope_template).
    """
    if not isinstance(handoff, TaskHandoff):
        return False
    try:
        objective = handoff.objective.strip()
        scope = handoff.bounded_scope.strip()
        if not objective or not scope:
            return False
        if scope == objective:
            return False
        if scope == generic_fallback_scope_template(work_item_id, milestone_ref):
            return False
        if not tuple(handoff.validation_expectations):
            return False
        if not tuple(handoff.semantic_stop_expectations):
            return False
        if handoff.project_ref is None or handoff.plan_ref is None:
            return False
        if handoff.milestone_ref is None or handoff.milestone_ref.ref != milestone_ref:
            return False
        if handoff.work_item_ref is None or handoff.work_item_ref.ref != work_item_id:
            return False
        return True
    except Exception:
        return False


def assert_worker_usable_handoff(handoff: TaskHandoff, *, work_item_id: str, milestone_ref: str) -> None:
    """Fail closed when a handoff is not Worker-usable (no dispatch)."""
    if not is_worker_usable_handoff(handoff, work_item_id=work_item_id, milestone_ref=milestone_ref):
        raise WorkScopeInsufficientError(
            f"handoff for Work Item {work_item_id!r} (Milestone {milestone_ref!r}) is not "
            "Worker-usable: objective/bounded_scope/validation/stop expectations or "
            "trusted refs are missing, generic, or undifferentiated"
        )


def resolve_bounded_work_handoff(
    *,
    work_item_id: str,
    milestone_ref: str,
    projection: WorkSemanticProjection,
    project_id: str | None = None,
    plan_authority: str | None = None,
    plan_digest: str | None = None,
    task_kind: str | None = None,
) -> TaskHandoff:
    """Build the existing TaskHandoff from a trusted Work projection (REUSE).

    No new parallel Worker execution contract is created: the six core
    semantic fields plus stable refs of the frozen TaskHandoff are populated
    with usable semantics. Reference identities come from the trusted runtime
    binding arguments, never from the projection. The Plan body stays Plan
    authority: this resolver compiles/reduces trusted projection semantics
    and cannot invent requirements (unknown/foreign refs are never minted).

    Raises WorkScopeInsufficientError when the projection cannot yield a
    Worker-usable handoff.
    """
    if not isinstance(projection, WorkSemanticProjection):
        raise WorkScopeInsufficientError(
            f"projection must be WorkSemanticProjection, got {type(projection).__name__}"
        )
    wid = work_item_id.strip() if isinstance(work_item_id, str) else ""
    mid = milestone_ref.strip() if isinstance(milestone_ref, str) and milestone_ref.strip() else "M1"
    if not wid:
        raise WorkScopeInsufficientError("work_item_id must be a non-empty string")
    kind = task_kind.strip() if isinstance(task_kind, str) and task_kind.strip() else generic_fallback_task_kind(wid, mid)
    project_ref = SemanticReference(ref=project_id) if project_id else None
    plan_ref = SemanticReference(ref=plan_authority, digest=plan_digest) if plan_authority else None
    handoff = TaskHandoff(
        work_role="coder",
        task_kind=kind,
        objective=projection.objective,
        bounded_scope=projection.bounded_scope,
        validation_expectations=tuple(projection.validation_expectations),
        semantic_stop_expectations=tuple(projection.semantic_stop_expectations),
        work_item_ref=SemanticReference(ref=wid),
        milestone_ref=SemanticReference(ref=mid),
        project_ref=project_ref,
        plan_ref=plan_ref,
    )
    assert_worker_usable_handoff(handoff, work_item_id=wid, milestone_ref=mid)
    return handoff


__all__ = [
    "TASK_HANDOFF_IS_BOUNDED",
    "TASK_HANDOFF_CAN_EXPAND_PLAN_SCOPE",
    "WORKER_CAN_EXPAND_TASK_HANDOFF_SCOPE",
    "TASK_MAIN_FREEFORM_PROMPT_IS_SOLE_WORKER_AUTHORITY",
    "WORKER_STARTUP_PROMPT_IS_AUTHORITY",
    "TASK_HANDOFF_FREEZES_ACCEPTANCE_EXPECTATIONS",
    "TASK_HANDOFF_FREEZES_VALIDATION_EXPECTATIONS",
    "CODER_CAN_REDEFINE_ACCEPTANCE",
    "CODER_CAN_WEAKEN_ACCEPTANCE_TO_PASS",
    "CODER_CAN_SILENTLY_WEAKEN_EXISTING_TESTS",
    "TEST_MODIFICATION_REQUIRES_JUSTIFICATION",
    "TASK_HANDOFF_IS_PLAN_AUTHORITY",
    "TASK_HANDOFF_CAN_EXPAND_PLAN_AUTHORITY",
    "WORKER_REQUIRES_GITHUB_PLAN_READ_FOR_NORMAL_EXECUTION",
    "WORKER_NORMAL_EXECUTION_REQUIRES_PLAN_FETCH",
    "FULL_PLAN_DUMP_TO_WORKER",
    "OBJECTIVE_ONLY_WORKER_INSTRUCTION",
    "SCOPE_INSUFFICIENT_DISPATCH_ALLOWED",
    "WORK_SCOPE_INSUFFICIENT_DISPATCH_ALLOWED",
    "WORK_SCOPE_INSUFFICIENT",
    "MAX_WORK_SEMANTICS_ENTRIES",
    "WorkScopeInsufficientError",
    "WorkSemanticProjection",
    "parse_work_semantics_table",
    "generic_fallback_scope_template",
    "generic_fallback_task_kind",
    "is_worker_usable_handoff",
    "assert_worker_usable_handoff",
    "resolve_bounded_work_handoff",
    "validate_handoff_scope_containment",
    "assert_handoff_is_bounded_projection",
    "frozen_acceptance_expectations",
    "frozen_validation_expectations",
    "assert_result_preserves_frozen_expectations",
]
