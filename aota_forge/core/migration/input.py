"""M3-B11 bounded migration input model and deterministic normalization.

Scope (Issue #9, Lane M3-B11):
- Explicit source categories with typed classification:
    legacy_portable_plan_state                 -> migration_evidence
    accepted_plan_authority_snapshot           -> semantic_source_fact
    bounded_legacy_graph_evidence              -> migration_evidence
    project_workspace_manifest_facts           -> semantic_source_fact
    explicit_migration_evidence_artifacts      -> migration_evidence
    legacy_current_pointers_and_control_comments -> forbidden_authority_source
- Deterministic normalization: order-independent, canonical key sorting.
- Boundary enforcement:
    LEGACY_GRAPH_INPUT_IS_AUTHORITY = False
    LEGACY_CURRENT_POINTER_IS_AUTHORITY = False
    CONTROL_COMMENT_IS_SUBJECT_AUTHORITY = False
    CURRENT_POINTER_SUBJECT_AUTHORITY = False
    LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY = False
    ARBITRARY_HOST_PATH_IS_MIGRATION_AUTHORITY = False
    UNBOUNDED_FILESYSTEM_SCAN_ALLOWED = False
    SOURCE_FINGERPRINT_INCLUDES_HOST_ABSOLUTE_PATH = False
    INPUT_ORDERING_AFFECTS_SEMANTIC_OUTPUT = False
    SAME_SEMANTIC_INPUT_NORMALIZES_IDENTICALLY = True
    SOURCE_FINGERPRINT_DETERMINISTIC = True
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from aota_forge.core.idempotency import canonical_fingerprint

# Boundary signals
LEGACY_GRAPH_INPUT_IS_AUTHORITY = False
LEGACY_CURRENT_POINTER_IS_AUTHORITY = False
CONTROL_COMMENT_IS_SUBJECT_AUTHORITY = False
CURRENT_POINTER_SUBJECT_AUTHORITY = False
LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY = False
ARBITRARY_HOST_PATH_IS_MIGRATION_AUTHORITY = False
UNBOUNDED_FILESYSTEM_SCAN_ALLOWED = False
SOURCE_FINGERPRINT_INCLUDES_HOST_ABSOLUTE_PATH = False
INPUT_ORDERING_AFFECTS_SEMANTIC_OUTPUT = False
SAME_SEMANTIC_INPUT_NORMALIZES_IDENTICALLY = True
SOURCE_FINGERPRINT_DETERMINISTIC = True


class SourceCategory:
    """Bounded migration input source categories."""

    LEGACY_PORTABLE_PLAN_STATE = "legacy_portable_plan_state"
    ACCEPTED_PLAN_AUTHORITY_SNAPSHOT = "accepted_plan_authority_snapshot"
    BOUNDED_LEGACY_GRAPH_EVIDENCE = "bounded_legacy_graph_evidence"
    PROJECT_WORKSPACE_MANIFEST_FACTS = "project_workspace_manifest_facts"
    EXPLICIT_MIGRATION_EVIDENCE_ARTIFACTS = "explicit_migration_evidence_artifacts"
    LEGACY_CURRENT_POINTERS_AND_CONTROL_COMMENTS = (
        "legacy_current_pointers_and_control_comments"
    )


class SemanticClassification:
    """Semantic classification of migration inputs."""

    SEMANTIC_SOURCE_FACT = "semantic_source_fact"
    MIGRATION_EVIDENCE = "migration_evidence"
    FORBIDDEN_AUTHORITY_SOURCE = "forbidden_authority_source"


CATEGORY_CLASSIFICATION_MAP: dict[str, str] = {
    SourceCategory.LEGACY_PORTABLE_PLAN_STATE: SemanticClassification.MIGRATION_EVIDENCE,
    SourceCategory.ACCEPTED_PLAN_AUTHORITY_SNAPSHOT: SemanticClassification.SEMANTIC_SOURCE_FACT,
    SourceCategory.BOUNDED_LEGACY_GRAPH_EVIDENCE: SemanticClassification.MIGRATION_EVIDENCE,
    SourceCategory.PROJECT_WORKSPACE_MANIFEST_FACTS: SemanticClassification.SEMANTIC_SOURCE_FACT,
    SourceCategory.EXPLICIT_MIGRATION_EVIDENCE_ARTIFACTS: SemanticClassification.MIGRATION_EVIDENCE,
    SourceCategory.LEGACY_CURRENT_POINTERS_AND_CONTROL_COMMENTS: SemanticClassification.FORBIDDEN_AUTHORITY_SOURCE,
}


def _sanitize_no_host_path(value: str) -> str:
    """Strip or sanitize host-specific absolute paths unless purely logical."""
    if not isinstance(value, str):
        return value
    # Check for Unix absolute paths like /home/..., /tmp/..., /var/... or Windows C:\...
    if value.startswith(("/home/", "/tmp/", "/var/", "/etc/", "/usr/")) or re.match(r"^[a-zA-Z]:\\", value):
        # Return logical relative basename to avoid environmental host path leakage in fingerprints
        return value.rstrip("/").split("/")[-1]
    return value


def normalize_source_payload(obj: Any) -> Any:
    """Recursively normalize data structure into deterministic sorted representation."""
    if isinstance(obj, dict):
        normalized = {}
        for k in sorted(obj.keys()):
            val = obj[k]
            # Strip host paths if found in string values
            if isinstance(val, str):
                normalized[str(k)] = _sanitize_no_host_path(val)
            else:
                normalized[str(k)] = normalize_source_payload(val)
        return normalized
    if isinstance(obj, (list, tuple)):
        return [normalize_source_payload(x) for x in obj]
    if isinstance(obj, set):
        return [normalize_source_payload(x) for x in sorted(obj, key=lambda s: str(s))]
    if isinstance(obj, str):
        return _sanitize_no_host_path(obj)
    return obj


@dataclass(frozen=True)
class MigrationInput:
    """One typed, bounded migration input item with explicit classification."""

    source_category: str
    logical_source_identity: str
    payload: dict
    version_id: str = "v1"
    semantic_classification: str = ""
    target_kind: str = ""
    target_identity: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_category not in CATEGORY_CLASSIFICATION_MAP:
            raise ValueError(f"Unknown migration source category: {self.source_category!r}")

        expected_class = CATEGORY_CLASSIFICATION_MAP[self.source_category]
        if not self.semantic_classification:
            object.__setattr__(self, "semantic_classification", expected_class)
        elif self.semantic_classification != expected_class:
            raise ValueError(
                f"Source category {self.source_category!r} classified as "
                f"{self.semantic_classification!r}, expected {expected_class!r}"
            )

        # Sanitize logical identity
        clean_id = _sanitize_no_host_path(self.logical_source_identity)
        if clean_id != self.logical_source_identity:
            object.__setattr__(self, "logical_source_identity", clean_id)

    def normalized_payload(self) -> dict:
        return normalize_source_payload(self.payload)

    def source_fingerprint(self) -> str:
        """Deterministic content fingerprint of this normalized input."""
        canonical_dict = {
            "source_category": self.source_category,
            "logical_source_identity": self.logical_source_identity,
            "version_id": self.version_id,
            "semantic_classification": self.semantic_classification,
            "target_kind": self.target_kind,
            "target_identity": self.target_identity,
            "payload": self.normalized_payload(),
        }
        return canonical_fingerprint(canonical_dict)

    def to_dict(self) -> dict:
        return {
            "source_category": self.source_category,
            "logical_source_identity": self.logical_source_identity,
            "version_id": self.version_id,
            "semantic_classification": self.semantic_classification,
            "target_kind": self.target_kind,
            "target_identity": self.target_identity,
            "payload": self.normalized_payload(),
            "source_fingerprint": self.source_fingerprint(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MigrationInputManifest:
    """Deterministic collection of typed migration inputs."""

    manifest_id: str
    inputs: tuple[MigrationInput, ...] = ()

    @classmethod
    def create(cls, manifest_id: str, raw_inputs: list[MigrationInput]) -> MigrationInputManifest:
        """Create a deterministic manifest from a list of inputs (order independent)."""
        # Deduplicate and sort deterministically by (source_category, logical_source_identity, version_id)
        seen: dict[tuple[str, str, str], MigrationInput] = {}
        for inp in raw_inputs:
            key = (inp.source_category, inp.logical_source_identity, inp.version_id)
            if key in seen:
                existing = seen[key]
                if existing.source_fingerprint() != inp.source_fingerprint():
                    raise ValueError(
                        f"Conflicting inputs for same logical key {key}: "
                        f"{existing.source_fingerprint()} vs {inp.source_fingerprint()}"
                    )
            else:
                seen[key] = inp

        sorted_inputs = tuple(
            seen[k]
            for k in sorted(
                seen.keys(),
                key=lambda t: (t[0], t[1], t[2]),
            )
        )
        return cls(manifest_id=manifest_id, inputs=sorted_inputs)

    def fingerprint(self) -> str:
        """Deterministic digest of the entire manifest."""
        payload = {
            "manifest_id": self.manifest_id,
            "inputs": [inp.to_dict() for inp in self.inputs],
        }
        return canonical_fingerprint(payload)

    def source_fingerprints(self) -> dict[str, str]:
        return {inp.logical_source_identity: inp.source_fingerprint() for inp in self.inputs}

    def to_dict(self) -> dict:
        return {
            "manifest_id": self.manifest_id,
            "manifest_fingerprint": self.fingerprint(),
            "input_count": len(self.inputs),
            "inputs": [inp.to_dict() for inp in self.inputs],
            "source_fingerprints": self.source_fingerprints(),
        }


__all__ = [
    "LEGACY_GRAPH_INPUT_IS_AUTHORITY",
    "LEGACY_CURRENT_POINTER_IS_AUTHORITY",
    "CONTROL_COMMENT_IS_SUBJECT_AUTHORITY",
    "CURRENT_POINTER_SUBJECT_AUTHORITY",
    "LEGACY_POINTER_CAN_DETERMINE_SHADOW_CANONICAL_IDENTITY",
    "ARBITRARY_HOST_PATH_IS_MIGRATION_AUTHORITY",
    "UNBOUNDED_FILESYSTEM_SCAN_ALLOWED",
    "SOURCE_FINGERPRINT_INCLUDES_HOST_ABSOLUTE_PATH",
    "INPUT_ORDERING_AFFECTS_SEMANTIC_OUTPUT",
    "SAME_SEMANTIC_INPUT_NORMALIZES_IDENTICALLY",
    "SOURCE_FINGERPRINT_DETERMINISTIC",
    "SourceCategory",
    "SemanticClassification",
    "CATEGORY_CLASSIFICATION_MAP",
    "normalize_source_payload",
    "MigrationInput",
    "MigrationInputManifest",
]
