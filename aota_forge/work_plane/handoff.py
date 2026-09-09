"""Task Handoff semantic contract (S1 M1-W3).

Defines bounded immutable semantic task handoff between task-main and Forge
execution preparation. Distinct from lower-level execution envelope — does not construct
execution packages and carries no execution authority.

Invariants
----------
* TASK_HANDOFF_REQUIRED = yes
* HANDOFF_IS_SEMANTIC_INPUT_PROJECTION = yes
* HANDOFF_IS_EXECUTIONPACKAGE_REPLACEMENT = no
* HANDOFF_IS_EXECUTION_AUTHORITY = no
* HANDOFF_REFERENCE_IS_TRUSTED_BINDING = no
* ID_IS_AUTHORITY = no
* DIGEST_IS_AUTHORITY = no
* HANDOFF_DIGEST_IS_EXECUTION_INTENT_FINGERPRINT = no
* HANDOFF_SEMANTIC_DIGEST_COVERS_ALL_EXECUTION_RELEVANT_HANDOFF_FIELDS = yes
* Exactly six required semantic core fields:
  work_role, task_kind, objective, bounded_scope, validation_expectations,
  semantic_stop_expectations.
* work_role reuses AgentWorkRole; rejects CanonicalRole members.
* Mechanical field injection fails closed (package_id, idempotency_key, etc.).
* Bounded representation on string lengths and reference collections.
* Deterministic canonical serialization and SHA-256 handoff digest.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.work_plane.roles import AgentWorkRole, parse_agent_work_role

# Bounded capacity constraints
MAX_OBJECTIVE_LENGTH: int = 4096
MAX_TASK_KIND_LENGTH: int = 128
MAX_SCOPE_LENGTH: int = 4096
MAX_EXPECTATION_LENGTH: int = 1024
MAX_EXPECTATIONS_COUNT: int = 32
MAX_REFS_PER_COLLECTION: int = 16
MAX_TOTAL_REFS: int = 64
MAX_REF_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 128

# Mechanical fields blacklist — forbidden from semantic handoff
FORBIDDEN_MECHANICAL_FIELDS: frozenset[str] = frozenset({
    "package_id",
    "protocol_version",
    "contract_hash",
    "canonical_task_id",
    "idempotency_key",
    "intent_fingerprint",
    "correlation_id",
    "dispatch_attempt_id",
    "executor_id",
    "adapter_handle",
    "runtime_identity",
    "process_identity",
    "runtime_id",
    "process_id",
    "cas_revision",
    "revision_authority",
})

REQUIRED_CORE_FIELDS: tuple[str, ...] = (
    "work_role",
    "task_kind",
    "objective",
    "bounded_scope",
    "validation_expectations",
    "semantic_stop_expectations",
)

OPTIONAL_REFERENCE_FIELDS: tuple[str, ...] = (
    "project_ref",
    "plan_ref",
    "milestone_ref",
    "work_item_ref",
    "policy_refs",
    "context_refs",
    "evidence_refs",
    "skill_refs",
    "process_depth_or_risk_projection_ref",
)

ALL_HANDOFF_FIELDS: frozenset[str] = frozenset(
    REQUIRED_CORE_FIELDS + OPTIONAL_REFERENCE_FIELDS
)


@dataclass(frozen=True)
class SemanticReference:
    """Compact bounded stable semantic reference with optional integrity digest.

    Invariant: REFERENCE != AUTHORITY BINDING.
    Carries stable identity + optional integrity digest. Does not embed
    full Plan, Skill, AGENTS, Context, or Risk policy bodies.
    """

    ref: str
    digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or type(self.ref) is not str:
            raise TypeError(f"ref must be a str, got {type(self.ref).__name__}")
        stripped_ref = self.ref.strip()
        if not stripped_ref:
            raise ValueError("ref must be a non-empty string")
        if len(stripped_ref) > MAX_REF_LENGTH:
            raise ValueError(
                f"ref length ({len(stripped_ref)}) exceeds maximum {MAX_REF_LENGTH} chars"
            )
        object.__setattr__(self, "ref", stripped_ref)

        if self.digest is not None:
            if not isinstance(self.digest, str) or type(self.digest) is not str:
                raise TypeError(f"digest must be a str or None, got {type(self.digest).__name__}")
            stripped_digest = self.digest.strip()
            if not stripped_digest:
                raise ValueError("digest when provided must be a non-empty string")
            if len(stripped_digest) > MAX_DIGEST_LENGTH:
                raise ValueError(
                    f"digest length ({len(stripped_digest)}) exceeds maximum {MAX_DIGEST_LENGTH} chars"
                )
            object.__setattr__(self, "digest", stripped_digest)

    def to_dict(self) -> dict[str, Any]:
        """Serialize reference to a clean dictionary."""
        d: dict[str, Any] = {"ref": self.ref}
        if self.digest is not None:
            d["digest"] = self.digest
        return d

    @classmethod
    def from_value(cls, val: Any) -> SemanticReference:
        """Construct SemanticReference fail-closed from string, dict, or instance."""
        if isinstance(val, cls):
            return val
        if isinstance(val, str) and type(val) is str:
            return cls(ref=val)
        if isinstance(val, Mapping):
            for forbidden in FORBIDDEN_MECHANICAL_FIELDS:
                if forbidden in val:
                    raise ValueError(f"Forbidden mechanical field in reference: {forbidden!r}")
            allowed_keys = {"ref", "digest"}
            extra_keys = set(val.keys()) - allowed_keys
            if extra_keys:
                raise ValueError(f"Unknown field(s) in SemanticReference: {sorted(extra_keys)}")
            if "ref" not in val:
                raise ValueError("Missing 'ref' in SemanticReference mapping")
            return cls(ref=val["ref"], digest=val.get("digest"))
        raise TypeError(f"Cannot construct SemanticReference from {type(val).__name__}")


@dataclass(frozen=True)
class TaskHandoff:
    """Bounded immutable semantic task handoff contract (task-main -> Forge).

    Carries semantic intent from task-main into execution preparation.
    Does NOT create execution packages, select executor, or assign mechanical IDs.
    """

    # 6 Required Core Fields
    work_role: AgentWorkRole
    task_kind: str
    objective: str
    bounded_scope: str
    validation_expectations: tuple[str, ...]
    semantic_stop_expectations: tuple[str, ...]

    # Optional Semantic References
    project_ref: SemanticReference | None = None
    plan_ref: SemanticReference | None = None
    milestone_ref: SemanticReference | None = None
    work_item_ref: SemanticReference | None = None
    policy_refs: tuple[SemanticReference, ...] = ()
    context_refs: tuple[SemanticReference, ...] = ()
    evidence_refs: tuple[SemanticReference, ...] = ()
    skill_refs: tuple[SemanticReference, ...] = ()
    process_depth_or_risk_projection_ref: SemanticReference | None = None

    def __post_init__(self) -> None:
        # 1. Validate work_role: must be AgentWorkRole, reject CanonicalRole fail-closed
        if isinstance(self.work_role, AgentWorkRole):
            pass
        elif isinstance(self.work_role, Enum):
            raise TypeError(
                f"work_role must be an AgentWorkRole or valid work role string, got foreign Enum {type(self.work_role).__name__}"
            )
        elif isinstance(self.work_role, str) and type(self.work_role) is str:
            object.__setattr__(self, "work_role", parse_agent_work_role(self.work_role))
        else:
            raise TypeError(
                f"work_role must be an AgentWorkRole or valid string, got {type(self.work_role).__name__}"
            )

        # 2. Validate task_kind
        if not isinstance(self.task_kind, str) or type(self.task_kind) is not str:
            raise TypeError(f"task_kind must be a string, got {type(self.task_kind).__name__}")
        stripped_kind = self.task_kind.strip()
        if not stripped_kind:
            raise ValueError("task_kind must be a non-empty string")
        if len(stripped_kind) > MAX_TASK_KIND_LENGTH:
            raise ValueError(
                f"task_kind length ({len(stripped_kind)}) exceeds maximum {MAX_TASK_KIND_LENGTH} chars"
            )
        if stripped_kind in FORBIDDEN_MECHANICAL_FIELDS:
            raise ValueError(f"task_kind cannot be a mechanical field name: {stripped_kind!r}")
        object.__setattr__(self, "task_kind", stripped_kind)

        # 3. Validate objective
        if not isinstance(self.objective, str) or type(self.objective) is not str:
            raise TypeError(f"objective must be a string, got {type(self.objective).__name__}")
        stripped_objective = self.objective.strip()
        if not stripped_objective:
            raise ValueError("objective must be a non-empty string")
        if len(stripped_objective) > MAX_OBJECTIVE_LENGTH:
            raise ValueError(
                f"objective length ({len(stripped_objective)}) exceeds maximum {MAX_OBJECTIVE_LENGTH} chars"
            )
        object.__setattr__(self, "objective", stripped_objective)

        # 4. Validate bounded_scope
        if not isinstance(self.bounded_scope, str) or type(self.bounded_scope) is not str:
            raise TypeError(f"bounded_scope must be a string, got {type(self.bounded_scope).__name__}")
        stripped_scope = self.bounded_scope.strip()
        if not stripped_scope:
            raise ValueError("bounded_scope must be a non-empty string")
        if len(stripped_scope) > MAX_SCOPE_LENGTH:
            raise ValueError(
                f"bounded_scope length ({len(stripped_scope)}) exceeds maximum {MAX_SCOPE_LENGTH} chars"
            )
        object.__setattr__(self, "bounded_scope", stripped_scope)

        # 5. Validate validation_expectations
        if not isinstance(self.validation_expectations, (tuple, list)):
            raise TypeError(
                f"validation_expectations must be a tuple or list, got {type(self.validation_expectations).__name__}"
            )
        if len(self.validation_expectations) > MAX_EXPECTATIONS_COUNT:
            raise ValueError(
                f"validation_expectations count ({len(self.validation_expectations)}) exceeds maximum {MAX_EXPECTATIONS_COUNT}"
            )
        norm_val: list[str] = []
        for idx, item in enumerate(self.validation_expectations):
            if not isinstance(item, str) or type(item) is not str:
                raise TypeError(
                    f"validation_expectations[{idx}] must be a string, got {type(item).__name__}"
                )
            s = item.strip()
            if not s:
                raise ValueError(f"validation_expectations[{idx}] must be a non-empty string")
            if len(s) > MAX_EXPECTATION_LENGTH:
                raise ValueError(
                    f"validation_expectations[{idx}] length ({len(s)}) exceeds maximum {MAX_EXPECTATION_LENGTH} chars"
                )
            norm_val.append(s)
        object.__setattr__(self, "validation_expectations", tuple(norm_val))

        # 6. Validate semantic_stop_expectations
        if not isinstance(self.semantic_stop_expectations, (tuple, list)):
            raise TypeError(
                f"semantic_stop_expectations must be a tuple or list, got {type(self.semantic_stop_expectations).__name__}"
            )
        if len(self.semantic_stop_expectations) > MAX_EXPECTATIONS_COUNT:
            raise ValueError(
                f"semantic_stop_expectations count ({len(self.semantic_stop_expectations)}) exceeds maximum {MAX_EXPECTATIONS_COUNT}"
            )
        norm_stop: list[str] = []
        for idx, item in enumerate(self.semantic_stop_expectations):
            if not isinstance(item, str) or type(item) is not str:
                raise TypeError(
                    f"semantic_stop_expectations[{idx}] must be a string, got {type(item).__name__}"
                )
            s = item.strip()
            if not s:
                raise ValueError(f"semantic_stop_expectations[{idx}] must be a non-empty string")
            if len(s) > MAX_EXPECTATION_LENGTH:
                raise ValueError(
                    f"semantic_stop_expectations[{idx}] length ({len(s)}) exceeds maximum {MAX_EXPECTATION_LENGTH} chars"
                )
            norm_stop.append(s)
        object.__setattr__(self, "semantic_stop_expectations", tuple(norm_stop))

        # 7. Validate single optional references
        single_ref_fields = (
            "project_ref",
            "plan_ref",
            "milestone_ref",
            "work_item_ref",
            "process_depth_or_risk_projection_ref",
        )
        for field_name in single_ref_fields:
            val = getattr(self, field_name)
            if val is not None:
                norm_ref = SemanticReference.from_value(val)
                object.__setattr__(self, field_name, norm_ref)

        # 8. Validate collection optional references
        collection_ref_fields = (
            "policy_refs",
            "context_refs",
            "evidence_refs",
            "skill_refs",
        )
        for field_name in collection_ref_fields:
            val = getattr(self, field_name)
            if val is None:
                object.__setattr__(self, field_name, ())
            elif not isinstance(val, (tuple, list)):
                raise TypeError(
                    f"{field_name} must be a tuple or list, got {type(val).__name__}"
                )
            else:
                if len(val) > MAX_REFS_PER_COLLECTION:
                    raise ValueError(
                        f"{field_name} count ({len(val)}) exceeds maximum {MAX_REFS_PER_COLLECTION}"
                    )
                norm_collection = tuple(SemanticReference.from_value(item) for item in val)
                object.__setattr__(self, field_name, norm_collection)

        # 9. Validate total references bound
        total_refs = (
            sum(1 for f in single_ref_fields if getattr(self, f) is not None)
            + len(self.policy_refs)
            + len(self.context_refs)
            + len(self.evidence_refs)
            + len(self.skill_refs)
        )
        if total_refs > MAX_TOTAL_REFS:
            raise ValueError(
                f"Total semantic references ({total_refs}) exceeds maximum limit ({MAX_TOTAL_REFS})"
            )

    def canonical_dict(self) -> dict[str, Any]:
        """Return deterministic canonical dict representation of TaskHandoff.

        Design decision for reference ordering:
        Collection references (policy_refs, context_refs, evidence_refs, skill_refs)
        are normalized and sorted deterministically by (ref, digest or "") to guarantee
        digest stability regardless of input insertion order.
        Expectations (validation_expectations, semantic_stop_expectations) preserve
        their declared sequence order as step order may carry semantic meaning.
        """
        def _sort_refs(refs: tuple[SemanticReference, ...]) -> list[dict[str, Any]]:
            sorted_refs = sorted(refs, key=lambda r: (r.ref, r.digest or ""))
            return [r.to_dict() for r in sorted_refs]

        return {
            "bounded_scope": self.bounded_scope,
            "context_refs": _sort_refs(self.context_refs),
            "evidence_refs": _sort_refs(self.evidence_refs),
            "milestone_ref": self.milestone_ref.to_dict() if self.milestone_ref else None,
            "objective": self.objective,
            "plan_ref": self.plan_ref.to_dict() if self.plan_ref else None,
            "policy_refs": _sort_refs(self.policy_refs),
            "process_depth_or_risk_projection_ref": (
                self.process_depth_or_risk_projection_ref.to_dict()
                if self.process_depth_or_risk_projection_ref
                else None
            ),
            "project_ref": self.project_ref.to_dict() if self.project_ref else None,
            "semantic_stop_expectations": list(self.semantic_stop_expectations),
            "skill_refs": _sort_refs(self.skill_refs),
            "task_kind": self.task_kind,
            "validation_expectations": list(self.validation_expectations),
            "work_item_ref": self.work_item_ref.to_dict() if self.work_item_ref else None,
            "work_role": self.work_role.value,
        }

    def canonical_json(self) -> str:
        """Serialize canonical representation without incidental ordering."""
        return canonical_json(self.canonical_dict())

    def compute_handoff_digest(self) -> str:
        """Compute deterministic SHA-256 digest of canonical TaskHandoff payload.

        Covers all execution-relevant semantic handoff fields (the 6 core fields + all refs).
        Distinct domain from lower execution envelope intent fingerprint.
        """
        encoded = self.canonical_json().encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def handoff_digest(self) -> str:
        """Deterministic SHA-256 digest covering all execution-relevant handoff fields."""
        return self.compute_handoff_digest()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary representation."""
        return {
            "work_role": self.work_role.value,
            "task_kind": self.task_kind,
            "objective": self.objective,
            "bounded_scope": self.bounded_scope,
            "validation_expectations": list(self.validation_expectations),
            "semantic_stop_expectations": list(self.semantic_stop_expectations),
            "project_ref": self.project_ref.to_dict() if self.project_ref else None,
            "plan_ref": self.plan_ref.to_dict() if self.plan_ref else None,
            "milestone_ref": self.milestone_ref.to_dict() if self.milestone_ref else None,
            "work_item_ref": self.work_item_ref.to_dict() if self.work_item_ref else None,
            "policy_refs": [r.to_dict() for r in self.policy_refs],
            "context_refs": [r.to_dict() for r in self.context_refs],
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "skill_refs": [r.to_dict() for r in self.skill_refs],
            "process_depth_or_risk_projection_ref": (
                self.process_depth_or_risk_projection_ref.to_dict()
                if self.process_depth_or_risk_projection_ref
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskHandoff:
        """Construct TaskHandoff from mapping with strict fail-closed validation."""
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")

        # Fail closed on mechanical blacklist injection
        for field in FORBIDDEN_MECHANICAL_FIELDS:
            if field in data:
                raise ValueError(
                    f"Forbidden mechanical field in TaskHandoff rejected: {field!r}"
                )

        # Fail closed on unknown fields
        extra = set(data.keys()) - ALL_HANDOFF_FIELDS
        if extra:
            raise ValueError(f"Unknown field(s) in TaskHandoff payload: {sorted(extra)}")

        # Check required fields
        for req in REQUIRED_CORE_FIELDS:
            if req not in data:
                raise ValueError(f"Missing required field in TaskHandoff: {req!r}")

        return cls(
            work_role=data["work_role"],
            task_kind=data["task_kind"],
            objective=data["objective"],
            bounded_scope=data["bounded_scope"],
            validation_expectations=tuple(data["validation_expectations"]),
            semantic_stop_expectations=tuple(data["semantic_stop_expectations"]),
            project_ref=data.get("project_ref"),
            plan_ref=data.get("plan_ref"),
            milestone_ref=data.get("milestone_ref"),
            work_item_ref=data.get("work_item_ref"),
            policy_refs=tuple(data.get("policy_refs") or ()),
            context_refs=tuple(data.get("context_refs") or ()),
            evidence_refs=tuple(data.get("evidence_refs") or ()),
            skill_refs=tuple(data.get("skill_refs") or ()),
            process_depth_or_risk_projection_ref=data.get(
                "process_depth_or_risk_projection_ref"
            ),
        )


def compute_handoff_digest(handoff_or_dict: TaskHandoff | Mapping[str, Any]) -> str:
    """Compute deterministic SHA-256 digest of canonical TaskHandoff payload."""
    if isinstance(handoff_or_dict, TaskHandoff):
        return handoff_or_dict.compute_handoff_digest()
    if isinstance(handoff_or_dict, Mapping):
        parsed = TaskHandoff.from_dict(handoff_or_dict)
        return parsed.compute_handoff_digest()
    raise TypeError(
        f"Expected TaskHandoff or Mapping, got {type(handoff_or_dict).__name__}"
    )
