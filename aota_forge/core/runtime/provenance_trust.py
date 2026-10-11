"""Trusted/current runtime provenance checks."""
from __future__ import annotations
import time
from typing import Any
from .provenance_base import *
from .provenance_helpers import as_mapping, get_field, normalize_fingerprint
from .provenance_time import epoch

def validate_provenance(value: Any, expected_fp: str | None = None, expected_generation: Any = None, now: float | None = None, max_age: float | None = None) -> list[str]:
    data = as_mapping(value)
    if data is None: return [REASON_UNKNOWN_PROVENANCE]
    result: list[str] = []
    source = get_field(data, "source", "source_kind", "issuer", "origin")
    authority = get_field(data, "authority_ref", "authority", "issuer", "owner")
    evidence = get_field(data, "evidence_ref", "evidence_id", "observation_id", "ref", "id")
    if not all(isinstance(x, str) and x.strip() for x in (source, authority, evidence)): result.append(REASON_UNKNOWN_PROVENANCE)
    trusted = get_field(data, "trusted", "is_trusted", "trusted_provenance")
    if type(trusted) is not bool: result.append(REASON_UNKNOWN_PROVENANCE)
    elif not trusted: result.append(REASON_UNTRUSTED_PROVENANCE)
    status = get_field(data, "status", "lifecycle", "lifecycle_state")
    active, current = get_field(data, "active", "is_active"), get_field(data, "current", "is_current")
    historical, stale = get_field(data, "historical", "is_historical"), get_field(data, "stale", "is_stale")
    conflict = get_field(data, "conflict", "conflicting", "ambiguous")
    for name, flag in (("active", active), ("current", current), ("historical", historical), ("stale", stale), ("conflict", conflict)):
        if flag is not None and type(flag) is not bool: result.append(REASON_PROVENANCE_CONFLICT)
        elif name == "active" and flag is False: result.append(REASON_INACTIVE_RUNTIME)
        elif name == "current" and flag is False: result.append(REASON_STALE_RUNTIME)
        elif name == "historical" and flag is True: result.append(REASON_HISTORICAL_RUNTIME)
        elif name == "stale" and flag is True: result.append(REASON_STALE_RUNTIME)
        elif name == "conflict" and flag is True: result.append(REASON_CONFLICTING_EVIDENCE)
    if status is None:
        if active is None and current is None: result.append(REASON_PROVENANCE_CURRENTNESS)
    elif not isinstance(status, str): result.append(REASON_PROVENANCE_CONFLICT)
    elif status.strip().lower() in RETIRED_STATUSES: result.append(REASON_HISTORICAL_RUNTIME if status.strip().lower() == "historical" else REASON_STALE_RUNTIME)
    elif status.strip().lower() not in ACTIVE_STATUSES: result.append(REASON_PROVENANCE_CURRENTNESS)
    observed = get_field(data, "fingerprint", "runtime_fingerprint", "running_fingerprint", "subject_fingerprint")
    if observed is not None:
        try: observed = normalize_fingerprint(observed, "provenance fingerprint")
        except RuntimeContractError as exc: result.extend(exc.reason_codes or (REASON_UNKNOWN_FINGERPRINT,)); observed = None
        if expected_fp is not None and observed != expected_fp: result.append(REASON_PROVENANCE_FINGERPRINT)
    generation = get_field(data, "generation", "observation_generation", "runtime_generation", "revision")
    if expected_generation is not None and (generation is None or str(generation) != str(expected_generation)): result.append(REASON_PROVENANCE_GENERATION)
    now = time.time() if now is None else now
    expires = get_field(data, "expires_at", "valid_until", "not_after")
    if expires is not None and (epoch(expires) is None or now >= epoch(expires)): result.append(REASON_PROVENANCE_EXPIRED)
    if max_age is not None:
        captured = epoch(get_field(data, "observed_at", "observed_timestamp", "captured_at", "timestamp"))
        if not isinstance(max_age, (int, float)) or isinstance(max_age, bool) or max_age < 0 or captured is None: result.append(REASON_PROVENANCE_CURRENTNESS)
        elif captured > now + 1.0 or now - captured > float(max_age): result.append(REASON_STALE_RUNTIME)
    return list(dict.fromkeys(result))
