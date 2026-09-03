"""task-main Context Lifecycle Budget & Rollover Decision Contract (S5 M1 W1).

Thin typed task-main lifecycle budget semantic contract and deterministic
rollover decision evaluator.

W1 solves:
  Define a deterministic bounded task-main context-budget and
  rollover-decision semantic contract without implementing rollover
  runtime or creating authority.

Invariants
----------
* W1_CONTRACT_ONLY=yes, M1_IMPLEMENT_ACTUAL_ROLLOVER=no,
  M1_IMPLEMENT_ACTUAL_RECOVERY=no, M2 owns execution.
* CONTEXT_BUDGET_REQUIRED=yes, CONTEXT_BUDGET_DIMENSIONS_INTERFACE_FIRST=yes,
  EXACT_CONTEXT_BUDGET_LIMIT_DEFERRED=yes (caller-governed bounds, fixture
  thresholds bounded; no Program-wide magic freeze).
* Portable dimensions (optional bounded fields, each independent, not all
  mandatory): canonical_bytes, component_count, eager_materialized_bytes,
  progressive_ref_count (candidate set; implementation may choose subset
  but must keep interface-first and not require all).
* MODEL_TOKENIZER_IS_CONTEXT_BUDGET_AUTHORITY=no; provider token counts
  are not canonical portable dimensions.
* S6_CONTEXT_METRIC_IS_CONTEXT_POLICY_AUTHORITY=no.
* All numeric budget dimensions: integers, not bool, >=0, deterministically
  serialized, fail closed on malformed type/value, bounded sane hard caps
  to prevent pathological representations; no silent coerce/clamp/truncate.
  Reuses BootstrapBudget bounded-accounting / canonical-byte / fail-closed
  pattern without duplicating a second generic byte-budget framework.
* CONTEXT_BUDGET_IS_OPERATION_AUTHORITY=no,
  CONTEXT_USAGE_IS_OPERATION_AUTHORITY=no,
  BUDGET_DISPOSITION_IS_OPERATION_AUTHORITY=no,
  BUDGET_DISPOSITION_IS_MILESTONE_APPROVAL=no,
  BUDGET_DISPOSITION_IS_WORK_ITEM_COMPLETION_AUTHORITY=no;
  no budget object satisfies S2 operation-authority contracts.
* RolloverDisposition bounded vocabulary: WITHIN_BUDGET,
  ROLLOVER_RECOMMENDED, ROLLOVER_REQUIRED, UNDETERMINED
  (distinct; unknown/incomplete evidence must not collapse to safe).
* MISSING_CONTEXT_BUDGET_EVIDENCE_IS_WITHIN_BUDGET=no;
  if required evidence for a configured budget dimension is unavailable:
  decision → UNDETERMINED (fail-closed); do not assume missing=zero or
  missing bound=infinite.
* Hard exceedance (configured observed > bound) → ROLLOVER_REQUIRED,
  but ROLLOVER_REQUIRED_IS_ROLLOVER_EXECUTION_AUTHORITY=no
  (lifecycle evidence only, does not create session).
* Milestone boundary: MILESTONE_DEFAULT_ROLLOVER_SUPPORTED=yes,
  MILESTONE_BOUNDARY_ALWAYS_FORCES_ROLLOVER=no;
  within-budget + Milestone boundary → ROLLOVER_RECOMMENDED,
  not forced execution. Mid-Milestone rollover semantic eligibility also
  supported without Plan/WorkItem restart.
* Explicit task-main lifecycle signal may participate as evidence only;
  TASK_MAIN_ROLLOVER_REQUEST_IS_OPERATION_AUTHORITY=no.
* Continuity-risk indication may support RECOMMENDED; no S4 risk engine
  duplicated (NEW_CONTEXT_RISK_ENGINE_CREATED=no).
* Deterministic precedence (pure, stateless, no wall-clock or
  nondeterministic behavior):
    invalid / insufficient required evidence → UNDETERMINED
    configured hard bound exceeded → ROLLOVER_REQUIRED
    non-hard trigger (Milestone boundary / explicit request / continuity
      risk) → ROLLOVER_RECOMMENDED
    otherwise → WITHIN_BUDGET
* Pure evaluator: stateless, deterministic, side-effect free; no
  external I/O or background activity or store/recovery creation.
* Agent-neutral: no Hermes/OpenAI/provider/model identity required.
* Serialization deterministic via aota_forge.core.contracts.canonical,
  unknown fields fail closed, no arbitrary metadata bag.
* Firewall: CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION=no,
  ROLLOVER_DOES_NOT_RESET_WORKFLOW_AUTHORITY=yes,
  ROLLOVER_DOES_NOT_MINT_OPERATION_AUTHORITY=yes,
  ROLLOVER_DECISION_IS_RETRY_PERMISSION=no, etc.
* M1_M2 boundary retained; no SessionCheckpoint / WorkingTruthProjection
  / provider integration / session runtime introduced.

Reuse
-----
* Reuses aota_forge.core.contracts.canonical (no parallel canonicalizer).
* Reuses BootstrapBudget semantic pattern (bounded, deterministic,
  fail-closed) without subclass duplication or second generic framework.

This module is a semantic contract only — not a runtime manager and
carries no authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json

# ---------------------------------------------------------------------------
# Public flags — authority-negative, interface, deferral
# ---------------------------------------------------------------------------

# Budget / dimensions interface
CONTEXT_BUDGET_REQUIRED: bool = True
CONTEXT_BUDGET_DIMENSIONS_INTERFACE_FIRST: bool = True
EXACT_CONTEXT_BUDGET_LIMIT_DEFERRED: bool = True
MODEL_TOKENIZER_IS_CONTEXT_BUDGET_AUTHORITY: bool = False
S6_CONTEXT_METRIC_IS_CONTEXT_POLICY_AUTHORITY: bool = False

# Authority firewalls — budget / disposition / rollover
CONTEXT_BUDGET_IS_OPERATION_AUTHORITY: bool = False
CONTEXT_USAGE_IS_OPERATION_AUTHORITY: bool = False
BUDGET_DISPOSITION_IS_OPERATION_AUTHORITY: bool = False
BUDGET_DISPOSITION_IS_MILESTONE_APPROVAL: bool = False
BUDGET_DISPOSITION_IS_WORK_ITEM_COMPLETION_AUTHORITY: bool = False
MISSING_CONTEXT_BUDGET_EVIDENCE_IS_WITHIN_BUDGET: bool = False

ROLLOVER_REQUIRED_IS_ROLLOVER_EXECUTION_AUTHORITY: bool = False

# Milestone / mid-Milestone policies (semantic only, not execution)
MILESTONE_DEFAULT_ROLLOVER_SUPPORTED: bool = True
MILESTONE_BOUNDARY_ALWAYS_FORCES_ROLLOVER: bool = False
MID_MILESTONE_ROLLOVER_SUPPORTED: bool = True
MID_MILESTONE_ROLLOVER_REQUIRES_PLAN_RESTART: bool = False
MID_MILESTONE_ROLLOVER_REQUIRES_WORK_ITEM_RESTART: bool = False

# Explicit lifecycle request invariant
TASK_MAIN_ROLLOVER_REQUEST_IS_OPERATION_AUTHORITY: bool = False

# Continuity risk — no new risk engine
NEW_CONTEXT_RISK_ENGINE_CREATED: bool = False

# Determinism / purity
ROLLOVER_DECISION_EVALUATOR_PURE: bool = True

# Runtime / store / recovery — must be zero for M1
NEW_SESSION_RUNTIME_REQUIRED_FOR_M1: bool = False
NEW_SESSION_MANAGER_REQUIRED_FOR_M1: bool = False
NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1: bool = False
NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1: bool = False
W1_RUNTIME_CREATED: bool = False

# Workflow / retry / effect firewalls
CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION: bool = False
ROLLOVER_DOES_NOT_RESET_WORKFLOW_AUTHORITY: bool = True
ROLLOVER_DOES_NOT_MINT_OPERATION_AUTHORITY: bool = True
ROLLOVER_DECISION_IS_RETRY_PERMISSION: bool = False
ROLLOVER_DECISION_IS_REPAIR_AUTHORITY: bool = False
ROLLOVER_DECISION_IS_SIDE_EFFECT_REPLAY_AUTHORITY: bool = False

# Agent neutrality
CONTEXT_LIFECYCLE_CONTRACT_AGENT_NEUTRAL: bool = True
MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY: bool = False

# Serialization / canonicalization
EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED: bool = True
DUPLICATE_GENERIC_BUDGET_FRAMEWORK_CREATED: bool = False
EXISTING_CANONICALIZATION_REUSED: bool = True
NEW_CANONICALIZATION_FRAMEWORK_CREATED: bool = False
ARBITRARY_CONTEXT_METADATA_BAG_ALLOWED: bool = False

# Generic frameworks not created
NEW_GENERIC_POLICY_FRAMEWORK_CREATED: bool = False
NEW_RESULT_ONTOLOGY_CREATED: bool = False
NEW_AUTHORITY_ONTOLOGY_CREATED: bool = False

# Checkpoint / provider / context integration not owned by W1
SESSION_CHECKPOINT_IMPLEMENTED_BY_W1: bool = False
WORKING_TRUTH_IMPLEMENTED_BY_W1: bool = False
CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W1: bool = False
W2_SCOPE_PULLED_FORWARD_BY_W1: bool = False
AGGREGATOR_CHANGE_REQUIRED_FOR_W1: bool = False

# Shared accepted contracts
SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED: bool = False

# W1 scope
W1_CONTRACT_ONLY: bool = True
M1_IMPLEMENT_ACTUAL_ROLLOVER: bool = False
M1_IMPLEMENT_ACTUAL_RECOVERY: bool = False
M2_OWNS_ROLLOVER_EXECUTION: bool = True
M2_OWNS_WORKING_TRUTH_RECOVERY_BEHAVIOR: bool = True
M1_M2_BOUNDARY_RETAINED: bool = True

# ---------------------------------------------------------------------------
# Bounds — sane hard validation (prevents pathological representations;
#          not a frozen Program-wide policy)
# ---------------------------------------------------------------------------

# Reuse bootstrap hard-limit pattern: caller-governed bounds must still
# fit within sane hard caps; exact policy thresholds are deferred.
MAX_CONTEXT_CANONICAL_BYTES_HARD: int = 50 * 1024 * 1024  # 50 MiB
MAX_CONTEXT_COMPONENT_COUNT_HARD: int = 10_000_000
MAX_EAGER_MATERIALIZED_BYTES_HARD: int = 50 * 1024 * 1024
MAX_PROGRESSIVE_REF_COUNT_HARD: int = 10_000_000

# ---------------------------------------------------------------------------
# Rollover disposition vocabulary
# ---------------------------------------------------------------------------

@unique
class RolloverDisposition(str, Enum):
    WITHIN_BUDGET = "WITHIN_BUDGET"
    ROLLOVER_RECOMMENDED = "ROLLOVER_RECOMMENDED"
    ROLLOVER_REQUIRED = "ROLLOVER_REQUIRED"
    UNDETERMINED = "UNDETERMINED"


ROLLOVER_DISPOSITION_VALUES: frozenset[str] = frozenset(v.value for v in RolloverDisposition)

# ---------------------------------------------------------------------------
# Helpers — strict numeric validation (int not bool, >=0, hard cap)
# ---------------------------------------------------------------------------

def _validate_optional_int(
    value: object | None,
    label: str,
    hard_limit: int,
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or type(value) is not int:
        # bool is subclass of int — reject explicitly
        raise TypeError(f"{label} must be int (not bool), got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{label} must be >= 0, got {value}")
    if value > hard_limit:
        raise ValueError(f"{label} {value} exceeds hard limit {hard_limit}")
    return value


def _validate_optional_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be bool, got {type(value).__name__}")
    return value


# Allowed fields — strict fail-closed
_ALLOWED_BUDGET_FIELDS: frozenset[str] = frozenset({
    "max_canonical_bytes",
    "max_component_count",
    "max_eager_materialized_bytes",
    "max_progressive_ref_count",
})

_ALLOWED_USAGE_FIELDS: frozenset[str] = frozenset({
    "canonical_bytes",
    "component_count",
    "eager_materialized_bytes",
    "progressive_ref_count",
})

_ALLOWED_DECISION_FIELDS: frozenset[str] = frozenset({
    "disposition",
})

# ---------------------------------------------------------------------------
# ContextBudget — caller/governed configured bounds (portable dimensions)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextBudget:
    """Thin typed task-main lifecycle budget (portable dimensions).

    Each dimension is optional; non-configured dimensions carry no
    implicit authority. Caller/governed policy supplies bounded
    fixture thresholds; production contract accepts caller-configured
    bounds without freezing Program-wide magic numbers.

    Candidate dimensions: canonical_bytes, component_count,
    eager_materialized_bytes, progressive_ref_count.

    All numeric dimensions: int, not bool, >=0, deterministically
    serialized, bounded hard caps, fail-closed on malformed.

    This is policy evidence only — not operation authority.
    """

    max_canonical_bytes: int | None = None
    max_component_count: int | None = None
    max_eager_materialized_bytes: int | None = None
    max_progressive_ref_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_canonical_bytes",
            _validate_optional_int(self.max_canonical_bytes, "max_canonical_bytes", MAX_CONTEXT_CANONICAL_BYTES_HARD),
        )
        object.__setattr__(
            self,
            "max_component_count",
            _validate_optional_int(self.max_component_count, "max_component_count", MAX_CONTEXT_COMPONENT_COUNT_HARD),
        )
        object.__setattr__(
            self,
            "max_eager_materialized_bytes",
            _validate_optional_int(
                self.max_eager_materialized_bytes,
                "max_eager_materialized_bytes",
                MAX_EAGER_MATERIALIZED_BYTES_HARD,
            ),
        )
        object.__setattr__(
            self,
            "max_progressive_ref_count",
            _validate_optional_int(
                self.max_progressive_ref_count,
                "max_progressive_ref_count",
                MAX_PROGRESSIVE_REF_COUNT_HARD,
            ),
        )

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.max_canonical_bytes is not None:
            d["max_canonical_bytes"] = self.max_canonical_bytes
        if self.max_component_count is not None:
            d["max_component_count"] = self.max_component_count
        if self.max_eager_materialized_bytes is not None:
            d["max_eager_materialized_bytes"] = self.max_eager_materialized_bytes
        if self.max_progressive_ref_count is not None:
            d["max_progressive_ref_count"] = self.max_progressive_ref_count
        return d

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextBudget":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_BUDGET_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ContextBudget: {sorted(extra)}")
        # Reject model-tokenizer and arbitrary metadata authority fields explicitly
        # (they are caught by extra above, but keep explicit comment for audit)
        return cls(
            max_canonical_bytes=data.get("max_canonical_bytes"),
            max_component_count=data.get("max_component_count"),
            max_eager_materialized_bytes=data.get("max_eager_materialized_bytes"),
            max_progressive_ref_count=data.get("max_progressive_ref_count"),
        )


# Backwards-compatible alias (spec allows ContextBudgetPolicy naming)
ContextBudgetPolicy = ContextBudget
ContextBudgetEvidence = ContextBudget  # not used; keep alias for search parity

# ---------------------------------------------------------------------------
# ContextUsage — observed evidence (lifecycle evidence, not authority)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextUsage:
    """Observed task-main context evidence (operation evidence, not authority).

    Mirrors the portable budget dimensions; each observed value is optional
    and, when a budget dimension is configured, must be present to evaluate
    deterministically — otherwise decision is UNDETERMINED (fail-closed).

    No silent coercion of floats / numeric strings / negatives / overflow.
    """

    canonical_bytes: int | None = None
    component_count: int | None = None
    eager_materialized_bytes: int | None = None
    progressive_ref_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canonical_bytes",
            _validate_optional_int(self.canonical_bytes, "canonical_bytes", MAX_CONTEXT_CANONICAL_BYTES_HARD),
        )
        object.__setattr__(
            self,
            "component_count",
            _validate_optional_int(self.component_count, "component_count", MAX_CONTEXT_COMPONENT_COUNT_HARD),
        )
        object.__setattr__(
            self,
            "eager_materialized_bytes",
            _validate_optional_int(
                self.eager_materialized_bytes,
                "eager_materialized_bytes",
                MAX_EAGER_MATERIALIZED_BYTES_HARD,
            ),
        )
        object.__setattr__(
            self,
            "progressive_ref_count",
            _validate_optional_int(
                self.progressive_ref_count,
                "progressive_ref_count",
                MAX_PROGRESSIVE_REF_COUNT_HARD,
            ),
        )

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.canonical_bytes is not None:
            d["canonical_bytes"] = self.canonical_bytes
        if self.component_count is not None:
            d["component_count"] = self.component_count
        if self.eager_materialized_bytes is not None:
            d["eager_materialized_bytes"] = self.eager_materialized_bytes
        if self.progressive_ref_count is not None:
            d["progressive_ref_count"] = self.progressive_ref_count
        return d

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextUsage":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_USAGE_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in ContextUsage: {sorted(extra)}")
        return cls(
            canonical_bytes=data.get("canonical_bytes"),
            component_count=data.get("component_count"),
            eager_materialized_bytes=data.get("eager_materialized_bytes"),
            progressive_ref_count=data.get("progressive_ref_count"),
        )


# ---------------------------------------------------------------------------
# RolloverDecision — lifecycle disposition (evidence only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RolloverDecision:
    """Bounded rollover disposition (lifecycle evidence, not authority).

    Disposition semantics:
      WITHIN_BUDGET       — all observed <= configured bounds, no trigger
      ROLLOVER_RECOMMENDED — non-hard trigger (Milestone boundary,
                              explicit request, continuity risk)
      ROLLOVER_REQUIRED   — configured hard bound exceeded (does not
                              itself create a new session)
      UNDETERMINED        — invalid / insufficient required evidence
                              (fail-closed, not safe)

    RolloverDecision is not: operation authority, Milestone approval,
    Work Item completion authority, retry/repair/side-effect replay
    authority, workflow disposition, execution authority.

    Deterministic serialization; unknown fields fail closed; no
    arbitrary metadata bag.
    """

    disposition: RolloverDisposition

    def __post_init__(self) -> None:
        if isinstance(self.disposition, RolloverDisposition):
            return
        if isinstance(self.disposition, str) and type(self.disposition) is str:
            try:
                parsed = RolloverDisposition(self.disposition)
            except ValueError as exc:
                raise ValueError(f"Unknown RolloverDisposition: {self.disposition!r}") from exc
            object.__setattr__(self, "disposition", parsed)
            return
        if isinstance(self.disposition, Enum):
            raise TypeError(
                f"disposition must be RolloverDisposition or valid string, "
                f"got foreign Enum {type(self.disposition).__name__}"
            )
        raise TypeError(f"disposition must be RolloverDisposition or string, got {type(self.disposition).__name__}")

    def canonical_dict(self) -> dict[str, Any]:
        return {"disposition": self.disposition.value}

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"disposition": self.disposition.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RolloverDecision":
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")
        extra = set(data.keys()) - _ALLOWED_DECISION_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in RolloverDecision: {sorted(extra)}")
        if "disposition" not in data:
            raise ValueError("Missing required field in RolloverDecision: 'disposition'")
        return cls(disposition=data["disposition"])


# ---------------------------------------------------------------------------
# Pure deterministic evaluator
# ---------------------------------------------------------------------------

def evaluate_rollover_decision(
    budget: ContextBudget | None,
    usage: ContextUsage | None,
    *,
    milestone_boundary: bool = False,
    explicit_rollover_requested: bool = False,
    continuity_risk: bool = False,
) -> RolloverDecision:
    """Evaluate deterministic rollover disposition (pure, stateless, side-effect free).

    Precedence (deterministic, no wall-clock or nondeterministic):
      1. invalid / insufficient required evidence → UNDETERMINED
      2. configured hard bound exceeded         → ROLLOVER_REQUIRED
      3. non-hard trigger (Milestone boundary / explicit lifecycle request /
         continuity risk)                      → ROLLOVER_RECOMMENDED
      4. otherwise                             → WITHIN_BUDGET

    Hard-bound semantics
    --------------------
    * Any configured observed dimension > bound → ROLLOVER_REQUIRED.
    * Observed == bound → not exceeded (deterministic boundary).
    * ROLLOVER_REQUIRED is lifecycle contract only; it does NOT itself
      create a new session, authorize Tool/Git/ retry / repair /
      side-effect replay, approve Milestone, or complete Work Item.

    Trigger semantics
    -----------------
    * Milestone boundary with no hard exceedance may produce
      ROLLOVER_RECOMMENDED (MILESTONE_DEFAULT_ROLLOVER_SUPPORTED but
      never forces execution; MILESTONE_BOUNDARY_ALWAYS_FORCES_ROLLOVER=no).
    * Explicit task-main lifecycle request and continuity-risk indication
      are evidence only; they cannot grant operation authority or launch
      a new session. Continuity risk does not duplicate S4 Risk model.

    Fail-closed
    -----------
    * Missing required evidence for a configured dimension (budget field
      set but usage field absent) → UNDETERMINED, not zero.
    * Non-bool trigger values, non-ContextBudget/ContextUsage inputs →
      UNDETERMINED (deterministic fail-closed, not exception propagation
      for evaluator callers who prefer disposition over exception).
    * Numeric dimension validation itself fails closed at construction
      (bool-as-int, float, numeric string, negative, overflow → TypeError
      / ValueError) — evaluator assumes constructed typed values.

    No side effects: remains stateless and deterministic.

    The function is intentionally pure; callers needing exceptions for
    malformed construction should rely on dataclass validators.
    """
    # Validate trigger types — fail-closed to UNDETERMINED if malformed
    # (bool check: type is bool, not just isinstance bool subclass of int)
    if type(milestone_boundary) is not bool:
        return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
    if type(explicit_rollover_requested) is not bool:
        return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
    if type(continuity_risk) is not bool:
        return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)

    # Validate budget/usage types — fail-closed to UNDETERMINED
    if budget is not None and not isinstance(budget, ContextBudget):
        return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
    if usage is not None and not isinstance(usage, ContextUsage):
        return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)

    # If no budget configured, treat as no hard bounds (only triggers matter)
    # But if budget is None and usage is None, that's valid empty policy
    # We skip missing-evidence and hard-exceedance checks when budget None

    if budget is not None:
        # 1. Missing required evidence → UNDETERMINED
        # Check each configured dimension has corresponding observed value
        if budget.max_canonical_bytes is not None:
            if usage is None or usage.canonical_bytes is None:
                return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
        if budget.max_component_count is not None:
            if usage is None or usage.component_count is None:
                return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
        if budget.max_eager_materialized_bytes is not None:
            if usage is None or usage.eager_materialized_bytes is None:
                return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)
        if budget.max_progressive_ref_count is not None:
            if usage is None or usage.progressive_ref_count is None:
                return RolloverDecision(disposition=RolloverDisposition.UNDETERMINED)

        # 2. Hard bound exceeded → ROLLOVER_REQUIRED
        # Any dimension where observed > bound
        if usage is not None:
            if (
                budget.max_canonical_bytes is not None
                and usage.canonical_bytes is not None
                and usage.canonical_bytes > budget.max_canonical_bytes
            ):
                return RolloverDecision(disposition=RolloverDisposition.ROLLOVER_REQUIRED)
            if (
                budget.max_component_count is not None
                and usage.component_count is not None
                and usage.component_count > budget.max_component_count
            ):
                return RolloverDecision(disposition=RolloverDisposition.ROLLOVER_REQUIRED)
            if (
                budget.max_eager_materialized_bytes is not None
                and usage.eager_materialized_bytes is not None
                and usage.eager_materialized_bytes > budget.max_eager_materialized_bytes
            ):
                return RolloverDecision(disposition=RolloverDisposition.ROLLOVER_REQUIRED)
            if (
                budget.max_progressive_ref_count is not None
                and usage.progressive_ref_count is not None
                and usage.progressive_ref_count > budget.max_progressive_ref_count
            ):
                return RolloverDecision(disposition=RolloverDisposition.ROLLOVER_REQUIRED)

    # 3. Non-hard triggers → ROLLOVER_RECOMMENDED
    if milestone_boundary or explicit_rollover_requested or continuity_risk:
        return RolloverDecision(disposition=RolloverDisposition.ROLLOVER_RECOMMENDED)

    # 4. Otherwise → WITHIN_BUDGET
    return RolloverDecision(disposition=RolloverDisposition.WITHIN_BUDGET)


__all__ = [
    # flags
    "CONTEXT_BUDGET_REQUIRED",
    "CONTEXT_BUDGET_DIMENSIONS_INTERFACE_FIRST",
    "EXACT_CONTEXT_BUDGET_LIMIT_DEFERRED",
    "MODEL_TOKENIZER_IS_CONTEXT_BUDGET_AUTHORITY",
    "S6_CONTEXT_METRIC_IS_CONTEXT_POLICY_AUTHORITY",
    "CONTEXT_BUDGET_IS_OPERATION_AUTHORITY",
    "CONTEXT_USAGE_IS_OPERATION_AUTHORITY",
    "BUDGET_DISPOSITION_IS_OPERATION_AUTHORITY",
    "BUDGET_DISPOSITION_IS_MILESTONE_APPROVAL",
    "BUDGET_DISPOSITION_IS_WORK_ITEM_COMPLETION_AUTHORITY",
    "MISSING_CONTEXT_BUDGET_EVIDENCE_IS_WITHIN_BUDGET",
    "ROLLOVER_REQUIRED_IS_ROLLOVER_EXECUTION_AUTHORITY",
    "MILESTONE_DEFAULT_ROLLOVER_SUPPORTED",
    "MILESTONE_BOUNDARY_ALWAYS_FORCES_ROLLOVER",
    "MID_MILESTONE_ROLLOVER_SUPPORTED",
    "MID_MILESTONE_ROLLOVER_REQUIRES_PLAN_RESTART",
    "MID_MILESTONE_ROLLOVER_REQUIRES_WORK_ITEM_RESTART",
    "TASK_MAIN_ROLLOVER_REQUEST_IS_OPERATION_AUTHORITY",
    "NEW_CONTEXT_RISK_ENGINE_CREATED",
    "ROLLOVER_DECISION_EVALUATOR_PURE",
    "NEW_SESSION_RUNTIME_REQUIRED_FOR_M1",
    "NEW_SESSION_MANAGER_REQUIRED_FOR_M1",
    "NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1",
    "NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1",
    "W1_RUNTIME_CREATED",
    "CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION",
    "ROLLOVER_DOES_NOT_RESET_WORKFLOW_AUTHORITY",
    "ROLLOVER_DOES_NOT_MINT_OPERATION_AUTHORITY",
    "ROLLOVER_DECISION_IS_RETRY_PERMISSION",
    "ROLLOVER_DECISION_IS_REPAIR_AUTHORITY",
    "ROLLOVER_DECISION_IS_SIDE_EFFECT_REPLAY_AUTHORITY",
    "CONTEXT_LIFECYCLE_CONTRACT_AGENT_NEUTRAL",
    "MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY",
    "EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED",
    "DUPLICATE_GENERIC_BUDGET_FRAMEWORK_CREATED",
    "EXISTING_CANONICALIZATION_REUSED",
    "NEW_CANONICALIZATION_FRAMEWORK_CREATED",
    "ARBITRARY_CONTEXT_METADATA_BAG_ALLOWED",
    "NEW_GENERIC_POLICY_FRAMEWORK_CREATED",
    "NEW_RESULT_ONTOLOGY_CREATED",
    "NEW_AUTHORITY_ONTOLOGY_CREATED",
    "SESSION_CHECKPOINT_IMPLEMENTED_BY_W1",
    "WORKING_TRUTH_IMPLEMENTED_BY_W1",
    "CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W1",
    "W2_SCOPE_PULLED_FORWARD_BY_W1",
    "AGGREGATOR_CHANGE_REQUIRED_FOR_W1",
    "SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED",
    "W1_CONTRACT_ONLY",
    "M1_IMPLEMENT_ACTUAL_ROLLOVER",
    "M1_IMPLEMENT_ACTUAL_RECOVERY",
    "M2_OWNS_ROLLOVER_EXECUTION",
    "M2_OWNS_WORKING_TRUTH_RECOVERY_BEHAVIOR",
    "M1_M2_BOUNDARY_RETAINED",
    "MAX_CONTEXT_CANONICAL_BYTES_HARD",
    "MAX_CONTEXT_COMPONENT_COUNT_HARD",
    "MAX_EAGER_MATERIALIZED_BYTES_HARD",
    "MAX_PROGRESSIVE_REF_COUNT_HARD",
    # core types
    "RolloverDisposition",
    "ROLLOVER_DISPOSITION_VALUES",
    "ContextBudget",
    "ContextBudgetPolicy",
    "ContextUsage",
    "RolloverDecision",
    "evaluate_rollover_decision",
]
