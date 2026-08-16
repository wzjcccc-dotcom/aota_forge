"""Portable Plan authority read adapter boundary (M2-D).

Read-side ONLY.  The adapter isolates the authoritative Plan source (the
governing GitHub issue today; a future authority store tomorrow) behind a
bounded snapshot:

    PlanAuthoritySnapshot
    - body                     (raw authoritative document text)
    - revision/digest          (source-provided revision and digest)
    - control projections      (canonical control-comment snapshots)

Core NEVER depends on GitHub issue numbers, comment IDs, gh CLI, pagination,
requests, or PyGithub.  GitHub-specific mechanics and platform metadata
belong to concrete adapters and stay adapter-private (never part of the
snapshot).  M2 implements NO write path.

Core ontology markers (mirrored from the Plan):

    GITHUB_IS_FORGE_CORE_ONTOLOGY=no
    GITHUB_API_IS_CORE_CONTRACT=no
    GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID=no
    RAW_GH_OPERATION_IN_CORE=no
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

GITHUB_IS_FORGE_CORE_ONTOLOGY = "no"
GITHUB_API_IS_CORE_CONTRACT = "no"
GITHUB_COMMENT_ID_IS_CORE_SEMANTIC_ID = "no"
RAW_GH_OPERATION_IN_CORE = "no"

WRITE_METHOD_PREFIXES = ("create", "update", "delete", "post", "patch", "write", "comment")


@dataclass(frozen=True)
class PlanAuthoritySnapshot:
    """Executor-neutral authoritative Plan snapshot (read side).

    ``body`` and ``control_projections`` are the only Core-relevant
    payloads.  ``revision``/``digest`` are opaque source-provided values.
    Any platform metadata lives inside the concrete adapter, never here.
    """

    body: str
    revision: str | None = None
    digest: str | None = None
    control_projections: dict[str, dict[str, str]] = field(default_factory=dict)


class PlanAuthorityReadAdapter(ABC):
    """Read abstraction over the authoritative Plan source (M2-D, read-only)."""

    @abstractmethod
    def load(self) -> PlanAuthoritySnapshot:
        """Return the authoritative Plan snapshot; never mutates anything."""


class StaticPlanAuthorityAdapter(PlanAuthorityReadAdapter):
    """Concrete read-only adapter backed by provided text.

    Fixture/offline adapter used by M2-D validators.  Any GitHub-specific
    source metadata passed at construction time is kept adapter-private and
    is NOT exposed on the snapshot.
    """

    def __init__(
        self,
        body: str,
        *,
        revision: str | None = None,
        digest: str | None = None,
        control_projections: dict[str, dict[str, str]] | None = None,
        _source_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._body = body
        self._revision = revision
        self._digest = digest
        self._control_projections = dict(control_projections or {})
        self._source_metadata = dict(_source_metadata or {})  # adapter-private

    def load(self) -> PlanAuthoritySnapshot:
        return PlanAuthoritySnapshot(
            body=self._body,
            revision=self._revision,
            digest=self._digest,
            control_projections={label: dict(projection) for label, projection in self._control_projections.items()},
        )
