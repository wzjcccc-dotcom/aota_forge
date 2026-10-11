"""Deterministic normalization helpers for runtime provenance."""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from .provenance_base import *

_SAFE = re.compile(r"^[^\x00]{1,512}$")

def get_field(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default

def as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    fn = getattr(value, "to_dict", None)
    if callable(fn):
        result = fn()
        return result if isinstance(result, Mapping) else None
    return None

def is_unknown_fingerprint(value: Any) -> bool:
    return not isinstance(value, str) or value.strip().lower() in UNKNOWN_FINGERPRINT_MARKERS

def normalize_fingerprint(value: Any, label: str = "fingerprint") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SAFE.fullmatch(value.strip()):
        raise RuntimeContractError(f"{label} is unknown or malformed", (REASON_UNKNOWN_FINGERPRINT,))
    return value.strip()

def coalesce_fingerprint(label: str, values: Sequence[Any]) -> str | None:
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    values = tuple(normalize_fingerprint(value, label) for value in present)
    if any(is_unknown_fingerprint(value) for value in values):
        if len(values) > 1:
            raise RuntimeContractError(f"{label} aliases conflict", (REASON_FINGERPRINT_CONFLICT, REASON_UNKNOWN_FINGERPRINT))
        return values[0]
    if len(set(values)) != 1:
        raise RuntimeContractError(f"{label} values conflict", (REASON_FINGERPRINT_CONFLICT,))
    return values[0]

def normalize_capabilities(value: Any, label: str = "capabilities") -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        values: Iterable[Any] = (value,)
    elif isinstance(value, Mapping):
        if any(not isinstance(name, str) or type(enabled) is not bool for name, enabled in value.items()):
            raise RuntimeContractError(f"{label} is unknown", (REASON_CAPABILITY_UNKNOWN,))
        values = [name for name, enabled in value.items() if enabled]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise RuntimeContractError(f"{label} is unknown", (REASON_CAPABILITY_UNKNOWN,))
    result: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise RuntimeContractError(f"{label} contains an invalid name", (REASON_CAPABILITY_UNKNOWN,))
        result.add(item.strip())
    return frozenset(result)

def compute_source_fingerprint(source: Any) -> str:
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, bytearray):
        raw = bytes(source)
    elif isinstance(source, str):
        raw = source.encode("utf-8")
    elif isinstance(source, Mapping) or (isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray))):
        try:
            raw = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise RuntimeContractError("source cannot be canonicalized", (REASON_UNKNOWN_FINGERPRINT,)) from exc
    else:
        fn = getattr(source, "to_dict", None)
        if not callable(fn):
            raise RuntimeContractError("source must be supplied material, not a host path", (REASON_UNKNOWN_FINGERPRINT,))
        return compute_source_fingerprint(fn())
    return hashlib.sha256(raw).hexdigest()

fingerprint_source = compute_source_fingerprint
source_fingerprint = compute_source_fingerprint
