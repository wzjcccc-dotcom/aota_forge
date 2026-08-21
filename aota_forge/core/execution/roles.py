"""Canonical execution roles and role mapping foundation (M5-1).

Defines canonical execution roles: planner, coder, reviewer, steward, executor.
Provides executor-neutral RoleMapping without Hermes-private ontology or semantic
decision authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from aota_forge.core.contracts.canonical import canonical_json

CANONICAL_ROLES: tuple[str, ...] = (
    "planner",
    "coder",
    "reviewer",
    "steward",
    "executor",
)

CANONICAL_ROLE_SET: frozenset[str] = frozenset(CANONICAL_ROLES)


class CanonicalRole(str, Enum):
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    STEWARD = "steward"
    EXECUTOR = "executor"

    @classmethod
    def is_valid(cls, role: str) -> bool:
        return role in CANONICAL_ROLE_SET


def is_canonical_role(role: str) -> bool:
    return role in CANONICAL_ROLE_SET


def validate_canonical_role(role: str) -> str:
    if not isinstance(role, str):
        raise TypeError(f"canonical_role must be a string, got {type(role).__name__}")
    if role not in CANONICAL_ROLE_SET:
        raise ValueError(
            f"Invalid canonical role: {role!r}. Must be one of {sorted(CANONICAL_ROLE_SET)}"
        )
    return role


class RoleMappingNotFoundError(KeyError):
    """Raised when an executor adapter does not map a requested canonical role."""

    def __init__(self, executor_id: str, canonical_role: str) -> None:
        self.executor_id = executor_id
        self.canonical_role = canonical_role
        self.code = "ROLE_MAPPING_NOT_FOUND"
        super().__init__(
            f"Executor {executor_id!r} has no mapping for canonical role {canonical_role!r}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": False,
            "details": {
                "executor_id": self.executor_id,
                "canonical_role": self.canonical_role,
            },
        }


FORBIDDEN_ROLE_DECISION_FIELDS: frozenset[str] = frozenset({
    "preferred_executor",
    "best_role",
    "default_profile",
    "similarity_threshold",
    "heuristic_role_ranking",
})


@dataclass(frozen=True)
class RoleMapping:
    """Frozen, deterministic container mapping canonical roles to executor-local roles/profiles.

    Core carries opaque adapter-local profile identifiers but knows no Hermes ontology.
    Fails closed when a role is unmapped (no heuristic fallback, no similarity ranking).
    """

    executor_id: str
    mappings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.executor_id, str) or not self.executor_id.strip():
            raise ValueError("executor_id must be a non-empty string")

        validated: list[tuple[str, str]] = []
        seen_roles: set[str] = set()
        for item in self.mappings:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError(f"Each mapping must be a (canonical_role, target_role) pair, got {item!r}")
            role, target = item
            validate_canonical_role(role)
            if not isinstance(target, str) or not target.strip():
                raise ValueError(f"Target role mapping for {role!r} must be a non-empty string")
            if role in seen_roles:
                raise ValueError(f"Duplicate mapping for canonical role {role!r}")
            seen_roles.add(role)
            validated.append((role, target.strip()))

        # Deterministically sort by canonical role
        sorted_mappings = tuple(sorted(validated, key=lambda x: x[0]))
        object.__setattr__(self, "mappings", sorted_mappings)

    def get_target_role(self, canonical_role: str) -> str:
        """Resolve canonical role to executor-local target role/profile.

        Fails closed with RoleMappingNotFoundError if not mapped.
        """
        validate_canonical_role(canonical_role)
        for role, target in self.mappings:
            if role == canonical_role:
                return target
        raise RoleMappingNotFoundError(self.executor_id, canonical_role)

    def has_role(self, canonical_role: str) -> bool:
        return any(role == canonical_role for role, _ in self.mappings)

    def mapped_roles(self) -> tuple[str, ...]:
        return tuple(role for role, _ in self.mappings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "mappings": dict(self.mappings),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoleMapping:
        if not isinstance(data, Mapping):
            raise TypeError(f"data must be a mapping, got {type(data).__name__}")

        for field in FORBIDDEN_ROLE_DECISION_FIELDS:
            if field in data:
                raise ValueError(f"Forbidden role decision field rejected: {field!r}")

        executor_id = data.get("executor_id")
        if not isinstance(executor_id, str):
            raise ValueError("executor_id is required in RoleMapping data")
        raw_mappings = data.get("mappings", {})
        if isinstance(raw_mappings, Mapping):
            mapping_pairs = tuple(raw_mappings.items())
        elif isinstance(raw_mappings, (list, tuple)):
            mapping_pairs = tuple(raw_mappings)
        else:
            raise TypeError(f"mappings must be a mapping or list of pairs, got {type(raw_mappings).__name__}")
        return cls(executor_id=executor_id, mappings=mapping_pairs)

    @classmethod
    def create(cls, executor_id: str, mapping_dict: Mapping[str, str]) -> RoleMapping:
        return cls.from_dict({"executor_id": executor_id, "mappings": mapping_dict})
