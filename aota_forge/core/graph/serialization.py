"""Deterministic bounded serialization for M3-B3 canonical graph records.

Round-trip stable for Workflow / Subject / Execution / Completion / Decision /
FollowupEdge.  Guarantees:

* canonical field projection is deterministic (stable key order per record)
* opaque IDs serialize to their bounded plain token string; no authority is
  inferred from them
* unknown/private executor data is not promoted into canonical identity
* private host paths are never present in canonical fields
* Completion serializes WITHOUT any direct subject_ref (no such field exists)
"""

from __future__ import annotations

import json
from typing import Any

from aota_forge.core import graph


def to_dict(record: Any) -> dict:
    """Deterministic canonical dict projection of a graph record.

    Accepts any of the canonical graph record models or any object exposing a
    ``canonical_fields`` projection.
    """
    if isinstance(record, tuple(graph.RECORD_TYPES)):
        return dict(record.canonical_fields())
    if hasattr(record, "canonical_fields"):
        projected = record.canonical_fields()
        if not isinstance(projected, dict):
            raise TypeError("canonical_fields must return a dict")
        return dict(projected)
    raise TypeError(f"cannot serialize unknown record type: {type(record).__name__}")


def to_bytes(record: Any) -> bytes:
    """Deterministic canonical JSON encoding of a graph record."""
    return json.dumps(to_dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def serialize(record: Any, pretty: bool = False) -> str:
    """Deterministic canonical JSON string of a graph record."""
    if pretty:
        return json.dumps(to_dict(record), ensure_ascii=False, sort_keys=True, indent=2)
    return json.dumps(to_dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def round_trip(record: Any) -> tuple[bytes, str]:
    """Produce a stable (bytes, text) serialization for round-trip equality."""
    canonical = to_dict(record)
    text = serialize(record)
    return canonical, text