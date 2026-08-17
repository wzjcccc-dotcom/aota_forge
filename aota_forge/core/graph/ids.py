"""Graph record ID boundary — B4 canonical identity primitives.

The temporary B3 ``CanonicalId`` value object has been RETIRED.  Graph records
now consume B4's ``InternalId`` as the canonical ID primitive
(``B4_INTERNAL_ID_IS_CANONICAL_ID_PRIMITIVE=yes``,
``B3_TEMPORARY_CANONICAL_ID_RETIRED=yes``, ``DUPLICATE_CANONICAL_ID_MODELS=no``).

This module re-exports the B4 identity primitives consumed by the graph layer so
graph-side imports remain stable.  B3 does NOT redefine minting, collision,
no-reuse, deterministic identity derivation, or the ObjectRef wire format — all
of those are owned by M3-B4 (``RECORD_ID_PRIMITIVES_OWNER=M3-B4``).

``ID_IS_AUTHORITY=no``: an ``InternalId`` identifies and validates shape only;
it never grants authority (``SUBJECT_ID_IS_AUTHORITY=no``).
"""

from __future__ import annotations

from aota_forge.core.identity.ids import (
    InternalId,
    canonical_id_str,
    is_raw_canonical_id_string,
    make_id,
    parse_internal_id,
    subject_id_from_value,
)
from aota_forge.core.identity.kinds import (
    DETERMINISTIC_SUBJECT_KINDS,
    GRAPH_OBJECT_KINDS,
    IdKind,
    MINTED_SUBJECT_KINDS,
    SUBJECT_KINDS,
    SubjectKind,
    is_known_graph_kind,
    is_known_subject_kind,
    require_graph_kind,
    require_subject_kind,
)

__all__ = [
    "DETERMINISTIC_SUBJECT_KINDS",
    "GRAPH_OBJECT_KINDS",
    "IdKind",
    "MINTED_SUBJECT_KINDS",
    "InternalId",
    "SUBJECT_KINDS",
    "SubjectKind",
    "canonical_id_str",
    "is_known_graph_kind",
    "is_known_subject_kind",
    "is_raw_canonical_id_string",
    "make_id",
    "parse_internal_id",
    "require_graph_kind",
    "require_subject_kind",
    "subject_id_from_value",
]
