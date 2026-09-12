"""Canonical AF Worker startup-guidance loading and model-prompt composition.

AF #49 M1/W11 (I49-B003 refined production boundary): the canonical Worker
startup guidance at ``aota_forge/composition/worker_startup_prompt.md`` had no
production model-facing consumer.  The fourth real M1 V3 rerun proved the real
Worker received only ``ExecutionPackage.instruction``, never invoked
``role.bootstrap``, guessed AF operations/workspace write modes and timed out.

This module is the bounded AF runtime-composition seam that:

* loads the ONE canonical startup-guidance source from the AF source root
  (never the current working directory; deterministic and fresh-process
  reproducible);
* fails closed with a typed identity when the canonical source is missing,
  unreadable, empty, escapes the authorized AF source root, or otherwise
  cannot be trusted;
* composes the real model-facing Worker prompt as

      canonical startup guidance
      + deterministic separator
      + exact ``ExecutionPackage.instruction``

The composition runs DOWNSTREAM of execution-package identity: the package
instruction, working context, and semantic ``intent_fingerprint`` are never
rewritten, and startup guidance never becomes Plan/Work authority.  It is
applied mechanically by the optional adapter hook
(``HermesAdapter(model_prompt_composer=...)``); AF runtime composition owns
the policy and installs the Worker composer, the Hermes adapter owns no AF
startup policy.  Generic/non-AF adapter consumers keep ``default=None``
behavior unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.runtime.config import (
    RuntimeConfig,
    RuntimeConfigError,
    WORKER_ROLES,
    resolve_binding_for_canonical_role,
)

# One canonical source; this module must never carry equivalent prose.
WORKER_STARTUP_GUIDANCE_SOURCE = "AF"
WORKER_STARTUP_PROMPT_IS_AUTHORITY = False
WORKER_STARTUP_PROMPT_SOURCE = WORKER_STARTUP_GUIDANCE_SOURCE
ONE_CANONICAL_WORKER_STARTUP_GUIDANCE_SOURCE = True
STARTUP_GUIDANCE_DELIVERY_SEAM = "AF_RUNTIME_COMPOSITION"
STARTUP_GUIDANCE_FINGERPRINT_POLICY = "outside_semantic_intent_fingerprint"
STARTUP_GUIDANCE_FIRST = True
MISSING_WORKER_STARTUP_GUIDANCE_BEHAVIOR = "FAIL_CLOSED"
WORKER_DISPATCH_WITHOUT_STARTUP_GUIDANCE_ALLOWED = False
STARTUP_GUIDANCE_INJECTED_ROLES = tuple(sorted(WORKER_ROLES))

# Deterministic separator between startup guidance and the bounded Work
# instruction.  The instruction itself remains byte-exact after it.
WORKER_MODEL_PROMPT_SEPARATOR = (
    "\n\n===== BOUNDED WORK INSTRUCTION (TaskHandoff scope) =====\n\n"
)

WORKER_STARTUP_GUIDANCE_UNAVAILABLE_CODE = "WORKER_STARTUP_GUIDANCE_UNAVAILABLE"
WORKER_STARTUP_GUIDANCE_MISSING_CODE = "WORKER_STARTUP_GUIDANCE_MISSING"
WORKER_STARTUP_GUIDANCE_UNREADABLE_CODE = "WORKER_STARTUP_GUIDANCE_UNREADABLE"
WORKER_STARTUP_GUIDANCE_EMPTY_CODE = "WORKER_STARTUP_GUIDANCE_EMPTY"
WORKER_STARTUP_GUIDANCE_UNTRUSTED_SOURCE_CODE = "WORKER_STARTUP_GUIDANCE_UNTRUSTED_SOURCE"

# AF source-root anchor: derived from this module's own real path, never from
# the caller working directory.
AF_PACKAGE_ROOT: Path = Path(__file__).resolve().parents[1]
AF_SOURCE_ROOT: Path = AF_PACKAGE_ROOT.parent
_AUTHORIZED_PACKAGE_DIRNAME = "aota_forge"


class WorkerStartupGuidanceError(ValueError):
    """Typed fail-closed identity for governed Worker startup guidance."""

    code = WORKER_STARTUP_GUIDANCE_UNAVAILABLE_CODE

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code if isinstance(code, str) and code else type(self).code
        super().__init__(message)


def _load_prompt_relpath() -> str:
    # Single path constant owner: the existing canonical Worker startup
    # contract in the work plane.  Lazy import keeps this composition module
    # free of the full work-plane import surface.
    from aota_forge.work_plane.role_bootstrap import WORKER_STARTUP_PROMPT_PATH

    return str(WORKER_STARTUP_PROMPT_PATH)


def _authorized_package_root(source_root: Path) -> Path:
    return (source_root / _AUTHORIZED_PACKAGE_DIRNAME).resolve()


def resolve_worker_startup_prompt_path(
    source_root: str | Path | None = None,
) -> Path:
    """Resolve the canonical startup-guidance file from the AF source root.

    ``source_root`` is the directory containing the ``aota_forge`` package
    (repository root or installed site-packages); it defaults to this module's
    own source root, so the result never depends on the current working
    directory.  A resolution that escapes the authorized AF package root is
    rejected fail-closed.
    """
    root = Path(source_root).resolve() if source_root is not None else AF_SOURCE_ROOT
    relpath = _load_prompt_relpath()
    candidate = (root / relpath).resolve()
    authorized = _authorized_package_root(root)
    try:
        candidate.relative_to(authorized)
    except ValueError as exc:
        raise WorkerStartupGuidanceError(
            "canonical Worker startup guidance resolves outside the authorized "
            f"AF source root: {candidate}",
            code=WORKER_STARTUP_GUIDANCE_UNTRUSTED_SOURCE_CODE,
        ) from exc
    return candidate


def load_worker_startup_guidance(*, source_root: str | Path | None = None) -> str:
    """Load and validate the canonical Worker startup guidance (fail-closed)."""
    path = resolve_worker_startup_prompt_path(source_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkerStartupGuidanceError(
            f"canonical Worker startup guidance is missing: {path}",
            code=WORKER_STARTUP_GUIDANCE_MISSING_CODE,
        ) from exc
    except OSError as exc:
        raise WorkerStartupGuidanceError(
            f"canonical Worker startup guidance is unreadable: {path} ({type(exc).__name__})",
            code=WORKER_STARTUP_GUIDANCE_UNREADABLE_CODE,
        ) from exc
    if not text.strip():
        raise WorkerStartupGuidanceError(
            f"canonical Worker startup guidance is empty: {path}",
            code=WORKER_STARTUP_GUIDANCE_EMPTY_CODE,
        )
    return text


def compose_worker_model_prompt(startup_guidance: str, instruction: str) -> str:
    """Compose ``guidance + separator + exact instruction`` (deterministic)."""
    if not isinstance(startup_guidance, str) or not startup_guidance.strip():
        raise WorkerStartupGuidanceError(
            "startup guidance must be a non-empty string",
            code=WORKER_STARTUP_GUIDANCE_EMPTY_CODE,
        )
    if not isinstance(instruction, str) or not instruction.strip():
        raise WorkerStartupGuidanceError(
            "bounded Work instruction must be a non-empty string",
            code=WORKER_STARTUP_GUIDANCE_UNAVAILABLE_CODE,
        )
    return startup_guidance + WORKER_MODEL_PROMPT_SEPARATOR + instruction


def is_governed_worker_work_role(work_role: object) -> bool:
    """Trusted role/runtime-binding discrimination for the Worker contract.

    Only the canonical accepted Worker work roles (analyst, coder, reviewer,
    project-steward) are governed by the Worker startup guidance.  task-main
    and any unmapped/generic execution are not.
    """
    return isinstance(work_role, str) and type(work_role) is str and work_role in WORKER_ROLES


def resolve_governed_worker_work_role(
    package: ExecutionPackage,
    runtime_config: RuntimeConfig,
) -> str | None:
    """Resolve the trusted Work Role for a dispatched package, or None.

    Discrimination reuses the existing operator-owned runtime binding
    (canonical role -> work role); it never infers Worker status from model
    text and never falls back to a default role.
    """
    if not isinstance(package, ExecutionPackage):
        raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")
    try:
        binding = resolve_binding_for_canonical_role(package.canonical_role, runtime_config)
    except RuntimeConfigError:
        # Not a governed Worker canonical role (e.g. executor): no injection.
        return None
    work_role = binding.work_role
    return work_role if is_governed_worker_work_role(work_role) else None


def build_worker_model_prompt_composer(
    *,
    runtime_config: RuntimeConfig,
    guidance_loader: Callable[[], str] | None = None,
) -> Callable[[ExecutionPackage], str]:
    """Build the AF-owned Worker model-prompt composer (fail-closed).

    The composer returns the exact package instruction unchanged for packages
    whose trusted runtime binding is not a governed Worker role, so
    generic/non-AF behavior is preserved.  For a governed Worker role the
    canonical startup guidance is mandatory: any inability to obtain trusted
    guidance raises a typed error (the caller must reject dispatch before any
    physical Worker spawn).
    """
    if not isinstance(runtime_config, RuntimeConfig):
        raise RuntimeConfigError(
            "governed Worker startup composition requires an operator RuntimeConfig, "
            f"got {type(runtime_config).__name__}"
        )
    loader = guidance_loader if guidance_loader is not None else load_worker_startup_guidance

    def compose_worker_model_prompt_for_package(package: ExecutionPackage) -> str:
        if not isinstance(package, ExecutionPackage):
            raise TypeError(f"package must be an ExecutionPackage, got {type(package).__name__}")
        if resolve_governed_worker_work_role(package, runtime_config) is None:
            return package.instruction
        try:
            guidance = loader()
        except WorkerStartupGuidanceError:
            raise
        except Exception as exc:
            raise WorkerStartupGuidanceError(
                "canonical Worker startup guidance could not be loaded: "
                f"{type(exc).__name__}",
                code=WORKER_STARTUP_GUIDANCE_UNAVAILABLE_CODE,
            ) from exc
        return compose_worker_model_prompt(guidance, package.instruction)

    return compose_worker_model_prompt_for_package


__all__ = [
    "AF_PACKAGE_ROOT",
    "AF_SOURCE_ROOT",
    "MISSING_WORKER_STARTUP_GUIDANCE_BEHAVIOR",
    "ONE_CANONICAL_WORKER_STARTUP_GUIDANCE_SOURCE",
    "STARTUP_GUIDANCE_DELIVERY_SEAM",
    "STARTUP_GUIDANCE_FINGERPRINT_POLICY",
    "STARTUP_GUIDANCE_FIRST",
    "STARTUP_GUIDANCE_INJECTED_ROLES",
    "WORKER_DISPATCH_WITHOUT_STARTUP_GUIDANCE_ALLOWED",
    "WORKER_MODEL_PROMPT_SEPARATOR",
    "WORKER_STARTUP_GUIDANCE_SOURCE",
    "WORKER_STARTUP_PROMPT_IS_AUTHORITY",
    "WORKER_STARTUP_PROMPT_SOURCE",
    "WorkerStartupGuidanceError",
    "build_worker_model_prompt_composer",
    "compose_worker_model_prompt",
    "is_governed_worker_work_role",
    "load_worker_startup_guidance",
    "resolve_governed_worker_work_role",
    "resolve_worker_startup_prompt_path",
]
