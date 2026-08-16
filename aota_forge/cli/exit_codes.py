"""CLI exit status projection (M2-F).

Frozen deterministic numeric mapping, owned by the CLI adapter only:

    success               -> 0
    error                 -> 1
    usage                 -> 2
    needs_semantic_choice -> 3
    blocked               -> 4

0 = conventional success; 2 = conventional argparse/usage error; 3/4
distinguish semantic choice from deterministic blocking.

Core error semantics remain the authority: the CLI never rewrites Core
error codes, it only projects the canonical envelope onto a shell exit
code.  The classification tables below are explicit and deterministic —
no hidden heuristics.

Markers:

    CORE_ERROR_SEMANTICS_ARE_AUTHORITY=yes
    CLI_EXIT_CODE_IS_ADAPTER_PROJECTION=yes
"""

from __future__ import annotations

from typing import Any

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NEEDS_SEMANTIC_CHOICE = 3
EXIT_BLOCKED = 4

# Canonical codes where human choice is required before the operation can
# semantically proceed.
SEMANTIC_CHOICE_CODES = frozenset({"NEEDS_SEMANTIC_CHOICE", "PROJECT_AMBIGUOUS"})

# Canonical deterministic blocks: the environment/milestone/contract
# boundary deterministically refuses execution.  HOST_RESOURCE_DENIED is
# the M2-E trusted-resource denial; ADAPTER_CONFIG_INVALID is the
# adapter-level operator configuration failure (adapter transport, not a
# Core semantic error).
BLOCKED_CODES = frozenset(
    {
        "UNSUPPORTED_OPERATION",
        "CONTEXT_NOT_SUPPORTED",
        "GOVERNANCE_PROJECTION_DRIFT",
        "CONTRACT_VERSION_MISMATCH",
        "HOST_RESOURCE_DENIED",
        "ADAPTER_CONFIG_INVALID",
    }
)

CORE_ERROR_SEMANTICS_ARE_AUTHORITY = "yes"
CLI_EXIT_CODE_IS_ADAPTER_PROJECTION = "yes"


def error_code(payload: dict[str, Any]) -> str | None:
    """Read the canonical error code from a canonical envelope."""
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str) and error["code"]:
        return error["code"]
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict) and isinstance(errors[0].get("code"), str):
        return errors[0]["code"]
    return None


def classify(payload: dict[str, Any]) -> int:
    """Project one canonical envelope onto the frozen CLI exit mapping."""
    if payload.get("ok"):
        return EXIT_SUCCESS
    code = error_code(payload)
    if code in SEMANTIC_CHOICE_CODES:
        return EXIT_NEEDS_SEMANTIC_CHOICE
    if code in BLOCKED_CODES:
        return EXIT_BLOCKED
    return EXIT_ERROR
