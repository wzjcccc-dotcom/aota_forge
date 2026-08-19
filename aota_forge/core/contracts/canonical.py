"""Small JSON canonicalization helpers shared by Core contract values."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


def canonicalize(value: Any, *, path: str = "value") -> Any:
    """Return a JSON-native, deterministic copy or reject the value."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            result[key] = canonicalize(item, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [canonicalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} must contain only JSON-native values")


def canonical_json(value: Any) -> str:
    """Serialize a previously validated value without incidental ordering."""
    return json.dumps(
        canonicalize(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
