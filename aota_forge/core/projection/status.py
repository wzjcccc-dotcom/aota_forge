"""M3-B8 Projection Reconstruction — projection revision / staleness metadata.

B8 is storage-neutral: it does NOT persist projections and does NOT create a
second revision system.  Staleness is represented purely in-memory by comparing
the Subject aggregate revision the projection was built from (``built_from``)
against the canonical Subject's current revision (``current``)
(``PROJECTION_STALENESS_USES_SUBJECT_REVISION=yes``,
``PROJECTION_REVISION_SOURCE=Subject_aggregate_revision``).

The authoritative revision source is the accepted B6 Subject aggregate revision
read through the repository/transaction interface — never a Git SHA, Issue body
revision, control-comment timestamp, event-log position, or legacy current
field.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionRevision:
    """Bounded in-memory staleness metadata for one projection.

    ``built_from_revision`` is the canonical Subject aggregate revision number
    at the moment the projection was rebuilt; ``current_revision`` is the
    canonical Subject's revision at comparison time.
    """

    subject_ref: str
    built_from_revision: int
    current_revision: int

    @property
    def stale(self) -> bool:
        """True when the source Subject revision advanced past the build."""
        return self.built_from_revision != self.current_revision

    def canonical(self) -> dict:
        return {
            "subject_ref": self.subject_ref,
            "built_from_revision": self.built_from_revision,
            "current_revision": self.current_revision,
            "stale": self.stale,
        }


def compare_revision(subject_ref: str, built_from: int, current: int) -> ProjectionRevision:
    """Build staleness metadata without introducing any revision system."""
    return ProjectionRevision(
        subject_ref=subject_ref,
        built_from_revision=built_from,
        current_revision=current,
    )


__all__ = ["ProjectionRevision", "compare_revision"]
