"""AF OpenCode host-profile contract helpers (AF #58 M1/W2).

Profile NAME authority stays in the operator-owned RuntimeConfig
(``aota_forge.runtime.config``: ``TASK_MAIN_PROFILE`` / ``SHARED_WORKER_PROFILE``
and the per-role bindings). This module only provides the mechanical,
fail-closed "exact profile string" handling for the lower-level adapter
mechanics, because the pinned v1.18.30 host resolves message-time ``agent``
authoritatively: a prompt without ``agent`` falls back to the host default
agent, NOT to the session row's persisted agent.

Hard boundaries (AF #58 M1):

* the profile is always an exact non-empty, whitespace-free string;
* a missing profile resolves to the accepted AF contract constant, never to a
  host default agent (``build``/``plan``/``default_agent``);
* TaskHandoff free text, model output, directory names and host state never
  supply the profile.
"""

from __future__ import annotations

AF_HOST_PROFILE_IS_AUTHORITY = False
HOST_DEFAULT_AGENT_FALLBACK_ALLOWED = False


def require_exact_profile(value: object, *, label: str) -> str:
    """Exact, bounded AF host-profile string; fail closed on anything else."""
    if not isinstance(value, str) or type(value) is not str:
        raise ValueError(f"{label} must be a string, got {type(value).__name__}")
    profile = value.strip()
    if not profile:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ch.isspace() for ch in profile):
        raise ValueError(f"{label} must not contain whitespace: {profile!r}")
    return profile


def task_main_profile_default() -> str:
    """Accepted AF task-main host-profile contract constant (operator-owned)."""
    from aota_forge.runtime.config import TASK_MAIN_PROFILE

    return require_exact_profile(TASK_MAIN_PROFILE, label="task-main profile")


def worker_profile_default() -> str:
    """Accepted AF worker host-profile contract constant (operator-owned)."""
    from aota_forge.runtime.config import SHARED_WORKER_PROFILE

    return require_exact_profile(SHARED_WORKER_PROFILE, label="worker profile")


__all__ = [
    "AF_HOST_PROFILE_IS_AUTHORITY",
    "HOST_DEFAULT_AGENT_FALLBACK_ALLOWED",
    "require_exact_profile",
    "task_main_profile_default",
    "worker_profile_default",
]
