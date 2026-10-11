"""Bounded timestamp normalization for runtime evidence."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

def epoch(value: Any) -> float | None:
    if value is None or isinstance(value, bool): return None
    if isinstance(value, (int, float)): return float(value)
    if isinstance(value, datetime): return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    if isinstance(value, str):
        try: return float(value)
        except ValueError: pass
        try: dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError: return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None
