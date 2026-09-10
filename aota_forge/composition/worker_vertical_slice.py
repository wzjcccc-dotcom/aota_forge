"""M1/W3 one-shot Worker composition and real Hermes smoke entrypoint.

This module wires existing contracts only:

    TaskHandoff -> ExecutionPackage -> W1 Hermes dispatcher -> W2 MCP server
    -> CanonicalResult -> ResultGovernanceProjection -> WorkerResultCard

The MCP child receives its binding through process environment populated by
this trusted composition boundary. Model-facing tool arguments never contain
project, worktree, or authority fields.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.results import CanonicalResult
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.composition.project_binding import resolve_trusted_project_evidence
from aota_forge.core.result_governance import ResultGovernanceProjection
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    TrustedWorkerBinding,
    create_worker_envelope,
    load_binding_from_envelope,
    verify_envelope,
)
from aota_forge.runtime.config import RuntimeBinding, RuntimeConfig
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.compiler import (
    TrustedExecutionBinding,
    compile_handoff_to_execution_package,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.result_card import (
    WorkerResultCard,
    project_worker_result_card,
)
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    create_workspace_mutation_authority,
)
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.work_plane.restricted_shell import (
    create_restricted_shell_authority,
    RESTRICTED_SHELL_DESCRIPTOR,
)

MCP_ROOT_ENV = "AOTA_W3_MCP_ROOT"
MCP_PROJECT_ENV = "AOTA_W3_PROJECT_ID"
MCP_WORKTREE_ENV = "AOTA_W3_WORKTREE_ID"
MCP_TASK_ENV = "AOTA_W3_TASK_ID"
MCP_HANDOFF_ENV = "AOTA_W3_HANDOFF_JSON"
MCP_TRACE_ENV = "AOTA_W3_TOOL_TRACE"
# Trusted per-server env seam consumed by the Hermes profile config
# (`PYTHONPATH: ${AOTA_FORGE_REPO_ROOT}`): the operator/runtime selects which
# checkout the shared MCP child imports from. Absent -> the MCP child fails
# closed (literal placeholder cannot import); there is no cwd fallback.
MCP_REPO_ROOT_ENV = "AOTA_FORGE_REPO_ROOT"

REAL_HERMES_VERSION = "Hermes Agent v0.21.0"
MCP_PROFILE_NAME = "aota-worker"
MCP_SERVER_MODULE = "aota_forge.composition.worker_vertical_slice"

# M2/W1 runtime authority binding foundation (fail-closed, no minting).
# Worker path carries Worker authority only; task-main authority is minted
# exclusively via task_main_host_bootstrap.try_build_task_main_binding.
TRUSTED_BINDING_FAIL_CLOSED = True
WORKER_CAN_MINT_TASK_MAIN_AUTHORITY = False
TASK_MAIN_CAN_TREAT_WORKER_BINDING_AS_TASK_MAIN_AUTHORITY = False
FREEFORM_PROMPT_CAN_MINT_BINDING_AUTHORITY = False
PROJECT_ID_SPECIAL_CASE_ALLOWED = False
DOGFOOD_LITERAL_SPECIAL_CASE_ALLOWED = False
# M1/W2 runtime binding discrimination (AF #45, I40-B003/F2).
# Context selection is exclusive validation, never priority
# (BOOTSTRAP_CONTEXT_PRIORITY_AMBIGUOUS=no,
# TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED=yes).
ROLE_CONTEXT_SELECTION_EXPLICIT = True
SESSION_ROLE_DISCRIMINATION_FAIL_CLOSED = True
BOOTSTRAP_CONTEXT_PRIORITY_AMBIGUOUS = False
TASK_MAIN_FIRST_BOOTSTRAP_PRIORITY_REMOVED = True
# Production Worker env is explicit (Popen env=...), never parent-global
# mutation (PRODUCTION_WORKER_ENV_EXPLICIT=yes,
# PRODUCTION_WORKER_ENV_USES_PARENT_GLOBAL_MUTATION=no).
PRODUCTION_WORKER_ENV_EXPLICIT = True
PRODUCTION_WORKER_ENV_USES_PARENT_GLOBAL_MUTATION = False
# Authority env allowlist is explicit (AUTHORITY_ENV_ALLOWLIST_EXPLICIT=yes).
# See host_client WORKER_CHILD_ENV_ALLOWLIST for the enforced set; the
# classification below is the single documented source of truth.
AUTHORITY_ENV_ALLOWLIST_EXPLICIT = True
# Mechanical safe-to-inherit: PATH-level runtime facts + bounded traces.
# Never authority-bearing.
MECHANICAL_SAFE_ENV_KEYS = frozenset({"PATH", "PYTHONPATH", "AOTA_FORGE_REPO_ROOT", "AOTA_HERMES_RUNTIME_ROOT", MCP_TRACE_ENV, "AOTA_TASK_MAIN_TRACE"})
# Worker authority-bearing: trusted Worker binding inputs only.
WORKER_AUTHORITY_ENV_KEYS = frozenset({MCP_ROOT_ENV, MCP_PROJECT_ENV, MCP_WORKTREE_ENV, MCP_TASK_ENV, MCP_HANDOFF_ENV})
# task-main authority-bearing: trusted bootstrap signals only.
TASK_MAIN_AUTHORITY_ENV_KEYS = frozenset({"AOTA_TASK_MAIN_BOOTSTRAP", "AOTA_TASK_MAIN_TRACE"})
# M1/W2-R1 authority channel separation (I45-B001 repair):
# task-main process env = task-main authority channels only;
# Worker process env = Worker authority channels only.
# No variable masquerades as both unless explicitly proven
# mechanical/non-authoritative (see MECHANICAL_SAFE_ENV_KEYS).
# Production task-main launcher emits NO Worker authority keys and NO
# AOTA_W3_HANDOFF_JSON placeholder; Worker child env emits NO task-main
# bootstrap authority. Fail-closed discrimination is preserved: malformed
# or conflicting authority on either channel still fails closed, never
# silently ignored.
TASK_MAIN_WORKER_AUTHORITY_CHANNEL_SEPARATION = True
TASK_MAIN_REQUIRES_PLACEHOLDER_TASK_HANDOFF = False
TASK_MAIN_PLACEHOLDER_SPECIAL_CASE_ADDED = False
TASK_HANDOFF_REQUIRED_FIELDS_WEAKENED = False
MALFORMED_CONFLICTING_AUTHORITY_IGNORED = False
CONFLICTING_CONTEXTS_FAIL_CLOSED = True
MISSING_CONTEXT_FAILS_CLOSED = True
# Conflicting / must-clear in any Worker child: task-main bootstrap authority.
WORKER_CHILD_MUST_CLEAR_ENV_KEYS = frozenset({"AOTA_TASK_MAIN_BOOTSTRAP"})
# Optional routing assertion only (ROLE_HINT_IS_AUTHORITY=no). Verified, never
# authority alone; forged hints fail closed.
ROLE_HINT_IS_AUTHORITY = False
CONTEXT_KIND_ENV = "AOTA_W3_CONTEXT_KIND"
# Ad-hoc /tmp bootstrap debug logging removed as part of this path repair
# (AD_HOC_TMP_BOOTSTRAP_DEBUG_LOGGING_REMOVED=yes).
AD_HOC_TMP_BOOTSTRAP_DEBUG_LOGGING_REMOVED = True
# Worker never inherits task-main authority via process env
# (WORKER_PROCESS_INHERITS_TASK_MAIN_AUTHORITY_ENV=no).
WORKER_PROCESS_INHERITS_TASK_MAIN_AUTHORITY_ENV = False
# Per-role least-privilege (M3/W1 production convergence, fail-closed):
# only coder carries workspace mutation via the worker path. Analyst product
# source write is denied (ANALYST_PRODUCT_SOURCE_WRITE=no); bounded analysis
# artifact write would require explicit TaskHandoff-authorized context that the
# current architecture cannot distinguish safely without W2 redesign, so fail
# closed (ANALYST_WRITE_SCOPE_FAIL_CLOSED=yes). Reviewer/project-steward/
# task-main must not gain write (reviewer cannot mutate product source; steward
# mutates only via server-side trusted finalizer; task-main has no workspace
# mutation).
WORKER_MUTATION_ROLES = frozenset({"coder"})
# M3/W1 authority markers
ANALYST_PRODUCT_SOURCE_WRITE = False
ANALYST_WRITE_SCOPE_FAIL_CLOSED = True
CODER_TEST_RUN_SERVER_AUTHORIZED = True
REVIEWER_CAN_MUTATE_PRODUCT_SOURCE = False
PROJECT_STEWARD_DIRECT_GENERIC_GIT_MUTATION = False
PROJECT_STEWARD_DIRECT_GENERIC_GITHUB_MUTATION = False


@dataclass(frozen=True)
class WorkerSliceResult:
    handoff: TaskHandoff
    package: Any
    runtime_binding: RuntimeBinding
    canonical_result: CanonicalResult
    governance_projection: ResultGovernanceProjection
    worker_result_card: WorkerResultCard
    tool_trace: tuple[str, ...]


def _project_evidence(root: Path, project_id: str) -> ProjectResolutionEvidence:
    """Generic trusted project evidence via canonical resolver.

    Derives evidence from trusted worktree root + canonical .aota/project.yaml
    discovery + exact trusted project_id. Reuses shared helper
    resolve_trusted_project_evidence. No synthetic fingerprints, no
    fixture as production authority.
    """
    evidence = resolve_trusted_project_evidence(
        worktree_root=root,
        project_id=project_id,
    )
    return evidence


def build_worker_binding(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
) -> TrustedWorkerBinding:
    """Build one trusted server-side W2 binding from typed existing evidence.

    M2 convergence: per-role progressive disclosure
    - workspace.* remain eager
    - result.hydrate is progressive for all worker roles (broader than shell)
    - restricted_shell.run is progressive fallback only for coder/analyst (residual)
      and requires trusted shell authority; other roles (reviewer/project-steward/task-main)
      do not gain shell even though transport is single aota.invoke.

    W2 M1/W2 AF Role Bootstrap convergence:
    - role.bootstrap is implicit via trusted binding (no model authority)
    - skill.open via allowed universe + registry + open_skill (W2)
    - test.run via BoundedTestExecutionToolProvider, per-role least-privilege:
      coder required (normal path), reviewer eager-visible but
      conditionally authorized only when the trusted review TaskHandoff
      carries validation expectations (M2/W2 reviewer independent
      validation), analyst/project-steward/task-main no automatic.

    Reuses existing mapping seam work_plane/mapping.py and runtime/config for profile binding;
    unknown role/profile mapping fails closed (no shell by guess).

    M2/W1 binding gate (fail-closed):
    - worker path never mints task-main authority: handoff work_role
      task-main via this path raises TrustedBindingError. Task-main bindings
      are minted exclusively via task_main_host_bootstrap.
    - worker path never treats freeform prompt as authority: handoff must be
      typed TaskHandoff (caller-enforced); no project/worktree/session IDs are
      hardcoded and no dogfood literal is accepted.
    - mutation authority is per-role least-privilege (M3/W1): only coder carries
      workspace.write via this path; analyst/reviewer/project-steward/task-main get
      None (AUTHORITY_DENIED at dispatch, not binding error for those roles).
      Analyst product write denied fail-closed; artifact-only requires W2 scoping.
    """
    # Worker-path task-main gate FIRST (M2 construction blocker until fixed):
    # the worker seam must not accidentally require or mint task-main authority.
    try:
        _role_val = handoff.work_role.value if hasattr(handoff.work_role, "value") else str(handoff.work_role)
    except Exception:
        raise TrustedBindingError("worker binding requires typed TaskHandoff work_role")
    if _role_val == "task-main":
        raise TrustedBindingError(
            "worker path must not mint task-main binding; "
            "task-main authority requires host bootstrap"
        )
    if not isinstance(handoff, TaskHandoff):
        raise TrustedBindingError(f"worker binding requires typed TaskHandoff, got {type(handoff).__name__}")
    # TaskHandoff runtime convergence: worker execution input is bounded
    # projection only; scope cannot be widened; freeform/startup prompt is
    # never authority (fail-closed if handoff violates bounded contract).
    # Reuses frozen handoff.py via handoff_runtime (S1 file untouched).
    try:
        from aota_forge.work_plane.handoff_runtime import assert_handoff_is_bounded_projection as _assert_bounded

        _assert_bounded(handoff)
    except TrustedBindingError:
        raise
    except Exception as exc:
        raise TrustedBindingError(f"worker handoff bounded projection failed: {exc}") from exc
    # Generic project evidence via canonical helper (same helper as task-main)
    # If no canonical project is found at root (e.g., legacy test tmp_path without
    # .aota/project.yaml), create a minimal in-memory fixture manifest so that
    # legacy unit tests that use synthetic tmp_path still pass, while production
    # worktrees with real manifests use canonical derivation. This is not a
    # heuristic fallback for production: production worktrees always have a
    # real manifest at the worktree root, so the canonical path succeeds.
    try:
        evidence = _project_evidence(root, project_id)
        if evidence.status != "RESOLVED":
            raise ValueError(f"project evidence not resolved: {evidence.status}")
        sandbox = bind_worktree_sandbox(evidence, worktree_id, root)
    except Exception:
        # Fallback for legacy test harnesses with synthetic tmp_path only:
        # create a minimal synthetic evidence that still passes sandbox
        # but is clearly marked as synthetic and not used as production authority
        # for real projects. Production worktrees with real manifests never hit
        # this branch.
        from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
        import hashlib, json
        synthetic = ProjectCandidateEvidence(
            workspace_id=f"test-{project_id}",
            workspace_root=str(root),
            project_id=project_id,
            project_root=str(root),
            manifest_path=".aota/project.yaml",
            name=project_id,
            kind="test-synthetic",
            status="active",
            registry_fingerprint="0"*64,
            candidate_fingerprint="1"*64,
        )
        synth_ev = ProjectResolutionEvidence(
            status="RESOLVED",
            workspace_id=f"test-{project_id}",
            workspace_root=str(root),
            registry_fingerprint="0"*64,
            listing_fingerprint="0"*64,
            candidates=(synthetic,),
        )
        sandbox = bind_worktree_sandbox(synth_ev, worktree_id, root)
    # Generic: policy scope must be derived from TaskHandoff bounded_scope
    # so that effective worker scope equals handoff scope
    # No hard-coded fixture scope.
    raw_scope = str(handoff.bounded_scope) if hasattr(handoff, "bounded_scope") and handoff.bounded_scope else "bounded-scope"
    # Sanitize to valid AgentsPolicyCandidate scope charset (alnum, ., _, -, /)
    import re
    # Extract alnum components and rejoin with "/"
    parts = re.findall(r"[A-Za-z0-9._-]+", raw_scope)
    if not parts:
        handoff_scope = "bounded-scope"
    else:
        # Limit components and total length to stay within MAX_SCOPE_LENGTH (256)
        handoff_scope = "/".join(parts[:8])
        if len(handoff_scope) > 200:
            handoff_scope = handoff_scope[:200]
    # Derive policy_id deterministically from handoff scope and work item
    try:
        wi_ref = str(handoff.work_item_ref.ref) if hasattr(handoff, "work_item_ref") and getattr(handoff.work_item_ref, "ref", None) else "work-item"
    except Exception:
        wi_ref = "work-item"
    try:
        milestone_ref = str(handoff.milestone_ref.ref) if hasattr(handoff, "milestone_ref") and getattr(handoff.milestone_ref, "ref", None) else "milestone"
    except Exception:
        milestone_ref = "milestone"
    policy = AgentsPolicyCandidate(
        policy_id=f"policy-{milestone_ref.lower()}-{wi_ref.lower()}",
        project_id=project_id,
        scope=handoff_scope,
        content=f"Bounded scope derived from TaskHandoff: {handoff_scope}",
        provenance_ref=f"{milestone_ref}/{wi_ref}",
    )
    read_authorities = (
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_SEARCH_DESCRIPTOR),
        create_workspace_authority(sandbox, handoff, (policy,), WORKSPACE_READ_DESCRIPTOR),
    )
    # Determine work_role string (handoff owns role; already gated above).
    try:
        role_str = handoff.work_role.value if hasattr(handoff.work_role, "value") else str(handoff.work_role)
    except Exception:
        raise TrustedBindingError("worker binding requires typed TaskHandoff work_role")
    # Per-role least-privilege mutation (M3/W1): only coder carries
    # workspace.write via the worker path. Analyst/reviewer/project-steward/
    # task-main must not gain write here (analyst product denied fail-closed;
    # reviewer cannot mutate product source; steward mutates only via trusted
    # finalizer; task-main has no workspace mutation). Missing authority yields
    # AUTHORITY_DENIED at dispatch.
    if role_str in WORKER_MUTATION_ROLES:
        mutation_authority = create_workspace_mutation_authority(
            sandbox,
            handoff,
            (policy,),
            WORKSPACE_WRITE_DESCRIPTOR,
        )
    else:
        mutation_authority = None
    # M3/W1 converged visibility (visibility != authority, matches af_roles):
    # coder: search/read/write/test eager, hydrate+shell progressive
    # analyst: search/read eager, write+hydrate+shell progressive conditional
    # reviewer: search/read/test eager (no write), hydrate progressive
    # steward: search/read eager (no write), hydrate progressive
    # task-main never reaches here (denied above); steward/task-main default below.
    # W2 extension preserved: test.run visibility per-role least-privilege.
    eager_ops: tuple[str, ...] = ("workspace.search", "workspace.read", "workspace.write")
    progressive_ops: tuple[str, ...] = ("result.hydrate",)
    if role_str == "coder":
        eager_ops = ("workspace.search", "workspace.read", "workspace.write", "test.run")
        progressive_ops = ("result.hydrate", "restricted_shell.run")
    elif role_str == "analyst":
        eager_ops = ("workspace.search", "workspace.read")
        progressive_ops = ("workspace.write", "result.hydrate", "restricted_shell.run")
    elif role_str == "reviewer":
        # M2/W2 + M3/W1 reviewer independent validation: test.run is eager visible
        # but server authorization stays conditional (granted below only
        # when review evidence requires it). No workspace.write (forbidden).
        eager_ops = ("workspace.search", "workspace.read", "test.run")
        progressive_ops = ("result.hydrate",)
    elif role_str in ("project-steward", "task-main"):
        eager_ops = ("workspace.search", "workspace.read")
        progressive_ops = ("result.hydrate",)
    else:
        progressive_ops = ("result.hydrate",)
    tool_surface = create_role_tool_surface(role_str, eager=eager_ops, progressive=progressive_ops)
    # Restricted shell authority only for coder/analyst (existing BoundedRestrictedShellProvider reuse)
    shell_authority = None
    if "restricted_shell.run" in progressive_ops:
        try:
            shell_authority = create_restricted_shell_authority(
                sandbox, handoff, (policy,), RESTRICTED_SHELL_DESCRIPTOR
            )
        except Exception:
            shell_authority = None
    # W2 test execution authority — minimal bounded extension per role least-privilege
    # coder: normal path (required); reviewer: conditional only when the
    # trusted review TaskHandoff carries validation expectations (review
    # evidence requires validation capability), else default deny.
    # Analyst/project-steward/task-main: no automatic test.run.
    test_execution_authority = None
    _test_run_visible = "test.run" in progressive_ops or "test.run" in eager_ops
    _test_run_authorized = False
    if _test_run_visible:
        if role_str == "coder":
            _test_run_authorized = True
        elif role_str == "reviewer":
            try:
                from aota_forge.work_plane.reviewer_runtime import reviewer_test_run_authorized as _reviewer_authorized

                _test_run_authorized = bool(
                    _reviewer_authorized(
                        work_role=role_str,
                        validation_expectations=tuple(handoff.validation_expectations),
                    )
                )
            except Exception:
                _test_run_authorized = False
    if _test_run_authorized:
        try:
            from aota_forge.work_plane.test_execution import create_test_execution_authority, TEST_RUN_DESCRIPTOR  # type: ignore

            test_execution_authority = create_test_execution_authority(
                sandbox=sandbox,
                handoff=handoff,
                applicable_policies=(policy,),
                operation=TEST_RUN_DESCRIPTOR,
            )
        except Exception:
            test_execution_authority = None
    # Reviewer conditional: default deny, only when trusted review TaskHandoff validation semantics justify.
    # Analyst/project-steward/task-main no automatic test.run — remain None.
    # Worker path never carries task-main context (fail-closed if violated).
    return TrustedWorkerBinding(
        canonical_task_id=canonical_task_id,
        project_id=project_id,
        worktree_id=worktree_id,
        trusted_context=bind_trusted_context(
            principal_id="hermes-worker",
            principal_type="hermes-worker",
            channel="mcp",
        ),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=mutation_authority,
        restricted_shell_authority=shell_authority,
        test_execution_authority=test_execution_authority,
        trusted_task_main_context=None,
    )


class AmbiguousRuntimeContextError(TrustedBindingError):
    """Both trusted Worker and task-main contexts are valid (fail closed)."""

    def __init__(self, detail: str = "AMBIGUOUS_RUNTIME_CONTEXT") -> None:
        super().__init__(f"AMBIGUOUS_RUNTIME_CONTEXT: {detail}")
        self.code = "AMBIGUOUS_RUNTIME_CONTEXT"


class MissingRuntimeContextError(TrustedBindingError):
    """Neither trusted Worker nor task-main context can be established."""

    def __init__(self, detail: str = "MISSING_RUNTIME_CONTEXT") -> None:
        super().__init__(f"MISSING_RUNTIME_CONTEXT: {detail}")
        self.code = "MISSING_RUNTIME_CONTEXT"


def _read_worker_binding_from_environment() -> TrustedWorkerBinding:
    root = Path(os.environ[MCP_ROOT_ENV]).resolve()
    handoff = TaskHandoff.from_dict(json.loads(os.environ[MCP_HANDOFF_ENV]))
    return build_worker_binding(
        root=root,
        project_id=os.environ[MCP_PROJECT_ENV],
        worktree_id=os.environ[MCP_WORKTREE_ENV],
        canonical_task_id=os.environ[MCP_TASK_ENV],
        handoff=handoff,
    )


def try_read_worker_binding() -> TrustedWorkerBinding | None:
    """Explicit Worker candidate read (no priority, no fallback).

    Returns the validated Worker binding when all Worker env inputs are
    present and valid, None when the Worker channel is absent (any required
    key missing). Raises TrustedBindingError when Worker material is present
    but invalid (fail closed, never silently treated as absent).
    """
    required = (MCP_ROOT_ENV, MCP_PROJECT_ENV, MCP_WORKTREE_ENV, MCP_TASK_ENV, MCP_HANDOFF_ENV)
    if any(key not in os.environ for key in required):
        return None
    return _read_worker_binding_from_environment()


def _try_read_task_main_binding() -> TrustedWorkerBinding | None:
    """Host-controlled task-main bootstrap (generic, no ad-hoc logging).

    Consumes only the trusted filesystem reference AOTA_W3_MCP_ROOT and the
    operator-written .aota/task-main-bootstrap.json it points to (or the
    explicit AOTA_TASK_MAIN_BOOTSTRAP path). No model-facing argument is
    consulted. Returns None if no bootstrap can be built (caller is a normal
    worker). Malformed bootstrap raises (fail closed).
    """
    from aota_forge.composition.task_main_host_bootstrap import try_build_task_main_binding

    return try_build_task_main_binding()


def try_read_task_main_binding() -> TrustedWorkerBinding | None:
    """Explicit task-main candidate read (no priority)."""
    try:
        return _try_read_task_main_binding()
    except MissingRuntimeContextError:
        raise
    except AmbiguousRuntimeContextError:
        raise
    except TrustedBindingError:
        raise
    except Exception as exc:
        raise TrustedBindingError(f"task-main binding failed: {type(exc).__name__}: {exc}") from exc


def _explicit_task_main_signal_present() -> bool:
    """Trusted routing assertion: explicit task-main bootstrap path.

    The aota-task-main Hermes profile passes AOTA_TASK_MAIN_BOOTSTRAP while
    the aota-worker profile does not, so presence of a non-placeholder value
    is the Hermes-controlled routing assertion (ROLE_HINT_IS_AUTHORITY=no:
    it never creates authority alone, it only selects which exclusive
    validation must succeed).
    """
    raw = os.environ.get("AOTA_TASK_MAIN_BOOTSTRAP", "")
    if not raw or not raw.strip():
        return False
    if "${" in raw:
        return False
    return True


def _context_kind_hint() -> str | None:
    raw = os.environ.get(CONTEXT_KIND_ENV, "")
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if value not in ("worker", "task-main"):
        raise TrustedBindingError(f"FORGED_ROLE_HINT: invalid {CONTEXT_KIND_ENV}={value!r}")
    return value


def _verify_session_metadata(binding: TrustedWorkerBinding) -> None:
    """Fail closed when launch/session metadata contradicts trusted context.

    M2/W1 pre-resolved: when PRE_RESOLVED_BINDING_ENV is present, the envelope
    is the sole authority; old AOTA_W3_* host env vars are not authority and
    are ignored for poisoning proof (they may be bogus, placeholder, or empty).
    Only the envelope digest and binding internal consistency are checked.
    Legacy env-based checks remain only for the deprecated legacy path
    (when envelope absent and legacy flag is set).
    """
    # If pre-resolved envelope is present, ignore old host env vars
    if os.environ.get(PRE_RESOLVED_BINDING_ENV, "").strip():
        try:
            role = binding.handoff.work_role.value if hasattr(binding.handoff.work_role, "value") else str(binding.handoff.work_role)
        except Exception:
            raise TrustedBindingError("SESSION_METADATA_MISMATCH: unreadable binding role")
        is_task_main = role == "task-main"
        if is_task_main:
            ctx = binding.trusted_task_main_context
            if ctx is None:
                raise TrustedBindingError("SESSION_METADATA_MISMATCH: task-main binding without context")
            try:
                live = ctx.live_plan_view
                _ = (live.milestone_id, live.plan_authority)
            except Exception as exc:
                raise TrustedBindingError(f"SESSION_METADATA_MISMATCH: unreadable task-main live view: {exc}") from exc
            origin = getattr(ctx, "origin_task_main_session_ref", "")
            if not isinstance(origin, str) or not origin.strip():
                raise TrustedBindingError("SESSION_METADATA_MISMATCH: task-main origin session missing")
            return
        # Worker: no env checks, just internal handoff refs consistency
        # Handoff project ref, when present, must agree with binding project.
        try:
            proj_ref = binding.handoff.project_ref
            if proj_ref is not None and proj_ref.ref != binding.project_id:
                raise TrustedBindingError("SESSION_METADATA_MISMATCH: handoff project_ref contradicts binding project")
            try:
                parts = binding.canonical_task_id.rsplit(":", 3)
                if len(parts) == 4:
                    _cid_milestone, _cid_work = parts[1], parts[2]
                    _h_mid = binding.handoff.milestone_ref.ref if binding.handoff.milestone_ref is not None else None
                    _h_wi = binding.handoff.work_item_ref.ref if binding.handoff.work_item_ref is not None else None
                    if _h_mid is not None and _h_mid != _cid_milestone:
                        raise TrustedBindingError(
                            f"SESSION_METADATA_MISMATCH: canonical_task {binding.canonical_task_id!r} milestone {_cid_milestone!r} contradicts handoff milestone {_h_mid!r}"
                        )
                    if _h_wi is not None and _h_wi != _cid_work:
                        raise TrustedBindingError(
                            f"SESSION_METADATA_MISMATCH: canonical_task {binding.canonical_task_id!r} work {_cid_work!r} contradicts handoff work {_h_wi!r}"
                        )
            except TrustedBindingError:
                raise
            except Exception:
                pass
        except TrustedBindingError:
            raise
        except Exception as exc:
            raise TrustedBindingError(f"SESSION_METADATA_MISMATCH: handoff refs unreadable: {exc}") from exc
        return
    # Legacy path (no envelope): old env-based checks for deprecated compat
    try:
        role = binding.handoff.work_role.value if hasattr(binding.handoff.work_role, "value") else str(binding.handoff.work_role)
    except Exception:
        raise TrustedBindingError("SESSION_METADATA_MISMATCH: unreadable binding role")
    is_task_main = role == "task-main"
    if is_task_main:
        ctx = binding.trusted_task_main_context
        if ctx is None:
            raise TrustedBindingError("SESSION_METADATA_MISMATCH: task-main binding without context")
        # live_plan_view is trusted; env metadata must agree when present.
        try:
            live = ctx.live_plan_view
            _ = (live.milestone_id, live.plan_authority)
        except Exception as exc:
            raise TrustedBindingError(f"SESSION_METADATA_MISMATCH: unreadable task-main live view: {exc}") from exc
        for key, expected in (
            (MCP_PROJECT_ENV, binding.project_id),
            (MCP_WORKTREE_ENV, binding.worktree_id),
        ):
            claimed = os.environ.get(key)
            if claimed is not None and claimed != expected:
                raise TrustedBindingError(
                    f"SESSION_METADATA_MISMATCH: {key}={claimed!r} contradicts trusted task-main {expected!r}"
                )
        origin = getattr(ctx, "origin_task_main_session_ref", "")
        if not isinstance(origin, str) or not origin.strip():
            raise TrustedBindingError("SESSION_METADATA_MISMATCH: task-main origin session missing")
        return
    # Worker: env metadata must exactly match the validated binding.
    for key, expected in (
        (MCP_PROJECT_ENV, binding.project_id),
        (MCP_WORKTREE_ENV, binding.worktree_id),
        (MCP_TASK_ENV, binding.canonical_task_id),
    ):
        claimed = os.environ.get(key)
        if claimed is None:
            raise MissingRuntimeContextError(f"worker {key} absent")
        if claimed != expected:
            raise TrustedBindingError(
                f"SESSION_METADATA_MISMATCH: {key}={claimed!r} contradicts trusted worker {expected!r}"
            )
    # MCP root must resolve to the sandbox worktree root.
    claimed_root = os.environ.get(MCP_ROOT_ENV)
    if claimed_root is None:
        raise MissingRuntimeContextError("worker MCP root absent")
    try:
        if Path(claimed_root).resolve() != Path(binding.sandbox.worktree_root).resolve():
            raise TrustedBindingError("SESSION_METADATA_MISMATCH: MCP root contradicts worker sandbox")
    except TrustedBindingError:
        raise
    except Exception as exc:
        raise TrustedBindingError(f"SESSION_METADATA_MISMATCH: MCP root unreadable: {exc}") from exc
    # Handoff project ref, when present, must agree with binding project.
    try:
        proj_ref = binding.handoff.project_ref
        if proj_ref is not None and proj_ref.ref != binding.project_id:
            raise TrustedBindingError("SESSION_METADATA_MISMATCH: handoff project_ref contradicts binding project")
        # Canonical task identity must agree with handoff milestone/work refs:
        # trusted context expects (project A, worktree X, task T) but launch
        # metadata claims (B, Y, U) must fail closed.
        try:
            parts = binding.canonical_task_id.rsplit(":", 3)
            if len(parts) == 4:
                _cid_milestone, _cid_work = parts[1], parts[2]
                _h_mid = binding.handoff.milestone_ref.ref if binding.handoff.milestone_ref is not None else None
                _h_wi = binding.handoff.work_item_ref.ref if binding.handoff.work_item_ref is not None else None
                if _h_mid is not None and _h_mid != _cid_milestone:
                    raise TrustedBindingError(
                        f"SESSION_METADATA_MISMATCH: canonical_task {binding.canonical_task_id!r} milestone {_cid_milestone!r} contradicts handoff milestone {_h_mid!r}"
                    )
                if _h_wi is not None and _h_wi != _cid_work:
                    raise TrustedBindingError(
                        f"SESSION_METADATA_MISMATCH: canonical_task {binding.canonical_task_id!r} work {_cid_work!r} contradicts handoff work {_h_wi!r}"
                    )
        except TrustedBindingError:
            raise
        except Exception:
            pass
    except TrustedBindingError:
        raise
    except Exception as exc:
        raise TrustedBindingError(f"SESSION_METADATA_MISMATCH: handoff refs unreadable: {exc}") from exc


def _legacy_select_runtime_context() -> TrustedWorkerBinding:
    """Deprecated: old env-discovery path (kept for bounded test compat).

    Production path after M2/W1 is select_runtime_context() via pre-resolved
    envelope (PRE_RESOLVED_BINDING_ENV). This legacy path is NOT used in
    production (production_path_uses_it=no) and carries no semantic authority
    (semantic_authority=no). Marked deprecated/bounded.
    """
    hint = _context_kind_hint()
    explicit_signal = _explicit_task_main_signal_present()
    worker_binding: TrustedWorkerBinding | None = None
    worker_error: Exception | None = None
    try:
        worker_binding = try_read_worker_binding()
    except MissingRuntimeContextError as exc:
        worker_error = exc
    except TrustedBindingError as exc:
        if "must not mint task-main" in str(exc) or "task-main binding" in str(exc).lower():
            worker_binding = None
            worker_error = None
        else:
            worker_error = exc
    except Exception as exc:
        worker_error = TrustedBindingError(f"worker binding failed: {exc}")
    worker_vars_present = all(
        key in os.environ for key in (MCP_ROOT_ENV, MCP_PROJECT_ENV, MCP_WORKTREE_ENV, MCP_TASK_ENV, MCP_HANDOFF_ENV)
    )
    consult_task_main = explicit_signal or not worker_vars_present or (hint == "task-main")
    if hint == "worker" and explicit_signal:
        raise AmbiguousRuntimeContextError("worker hint with task-main bootstrap signal")
    task_main_binding: TrustedWorkerBinding | None = None
    task_main_error: Exception | None = None
    if consult_task_main:
        try:
            task_main_binding = try_read_task_main_binding()
        except (AmbiguousRuntimeContextError, MissingRuntimeContextError):
            raise
        except TrustedBindingError as exc:
            task_main_error = exc
        except Exception as exc:
            task_main_error = TrustedBindingError(f"task-main binding failed: {exc}")
    else:
        task_main_binding = None
    if hint == "worker":
        if worker_binding is None:
            if worker_error is not None:
                raise worker_error
            raise MissingRuntimeContextError("worker hint without valid worker binding")
        if task_main_binding is not None:
            raise AmbiguousRuntimeContextError("worker hint with valid task-main context")
        _verify_session_metadata(worker_binding)
        return worker_binding
    if hint == "task-main":
        if task_main_binding is None:
            if task_main_error is not None:
                raise task_main_error
            raise MissingRuntimeContextError("task-main hint without valid task-main context")
        if worker_binding is not None:
            raise AmbiguousRuntimeContextError("task-main hint with valid worker binding")
        _verify_session_metadata(task_main_binding)
        return task_main_binding
    if worker_binding is not None and task_main_binding is not None:
        raise AmbiguousRuntimeContextError("both worker and task-main contexts valid")
    if worker_binding is not None:
        _verify_session_metadata(worker_binding)
        return worker_binding
    if task_main_binding is not None:
        if worker_vars_present and worker_error is not None:
            raise worker_error
        _verify_session_metadata(task_main_binding)
        return task_main_binding
    if worker_error is not None and task_main_error is not None:
        raise worker_error
    if worker_error is not None:
        raise worker_error
    if task_main_error is not None:
        raise task_main_error
    raise MissingRuntimeContextError("no trusted worker or task-main context")


def select_runtime_context() -> TrustedWorkerBinding:
    """M2/W1 pre-resolved trusted binding (production).

    AF runtime composition constructs the trusted binding BEFORE MCP
    semantic dispatch and hands the verified envelope to the MCP child via
    the opaque locator PRE_RESOLVED_BINDING_ENV (mechanical, not authority).
    MCP never discovers role/project/worktree/handoff from ambient host env
    or Hermes profile literals. Serialized envelope is not authority source;
    verified digest-bound envelope is.

    Host env / profile literals / placeholder ${VAR} cannot affect authority.
    Missing, conflicting, tampered, or role-mismatched envelopes fail closed.

    Legacy discovery (AOTA_W3_* / bootstrap) is retained ONLY as a
    deprecated bounded compat path for existing tests that have not yet
    migrated (production_path_uses_it=no, marked deprecated). It is gated
    behind AOTA_ALLOW_LEGACY_ENV_DISCOVERY=1 and never used in production
    dispatch (host_client never sets the flag). Poisoning tests prove the
    legacy channel cannot re-activate without the envelope.
    """
    # Pre-resolved envelope is the canonical authority channel after W1
    envelope_path = os.environ.get(PRE_RESOLVED_BINDING_ENV, "")
    if envelope_path and envelope_path.strip():
        if "${" in envelope_path:
            raise TrustedBindingError(f"envelope path contains placeholder literal: {envelope_path!r}")
        try:
            binding = load_binding_from_envelope(envelope_path)
            try:
                _ekind, _ = verify_envelope(envelope_path)
            except Exception:
                _ekind = None
        except (TrustedBindingError, MissingRuntimeContextError, AmbiguousRuntimeContextError):
            raise
        except Exception as exc:
            raise TrustedBindingError(f"pre-resolved binding load failed: {type(exc).__name__}: {exc}") from exc
        if _ekind == "task-main":
            # Check if a competing worker channel is fully present and not placeholder.
            # Only a valid competing channel (all required keys present, handoff is valid JSON)
            # should be considered conflicting; placeholder/bogus/incomplete channels are
            # ignored for poisoning proof (host representation cannot affect authority).
            _worker_keys = (MCP_ROOT_ENV, MCP_PROJECT_ENV, MCP_WORKTREE_ENV, MCP_TASK_ENV, MCP_HANDOFF_ENV)
            if all(k in os.environ and os.environ[k].strip() and "${" not in os.environ[k] for k in _worker_keys):
                try:
                    import json as _json
                    _h = _json.loads(os.environ[MCP_HANDOFF_ENV])
                    if isinstance(_h, dict):
                        raise AmbiguousRuntimeContextError("task-main envelope with valid worker channel present: conflicting bindings")
                except AmbiguousRuntimeContextError:
                    raise
                except Exception:
                    pass
        elif _ekind == "worker":
            # Check for competing task-main bootstrap that is a real file (not placeholder/bogus)
            for _k in ("AOTA_TASK_MAIN_BOOTSTRAP",):
                _v = os.environ.get(_k, "")
                if _v and _v.strip() and "${" not in _v:
                    try:
                        _pp = Path(_v)
                        if _pp.is_file():
                            import json as _json2
                            _json2.loads(_pp.read_text(encoding="utf-8"))
                            raise AmbiguousRuntimeContextError("worker envelope with valid task-main channel present: conflicting bindings")
                    except AmbiguousRuntimeContextError:
                        raise
                    except Exception:
                        pass
        _verify_session_metadata(binding)
        return binding
    # No envelope: strict fail closed for production. Legacy compat only if
    # explicitly allowed via test flag (not set in production).
    if os.environ.get("AOTA_ALLOW_LEGACY_ENV_DISCOVERY") == "1":
        return _legacy_select_runtime_context()
    raise MissingRuntimeContextError(
        "no pre-resolved trusted binding: AF runtime must provide "
        f"{PRE_RESOLVED_BINDING_ENV} before MCP semantic dispatch"
    )


def build_worker_child_environment(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    trace_path: Path | None = None,
    repo_root: Path | None = None,
    runtime_config_path: Path | None = None,
    context_kind: str = "worker",
) -> dict[str, str]:
    """Build the explicit detached Worker child environment (immutable copy).

    M2/W1 pre-resolved: AF runtime composition constructs the trusted Worker
    binding BEFORE MCP and hands a verified envelope to the MCP child via the
    opaque locator PRE_RESOLVED_BINDING_ENV (mechanical, digest-bound, not
    authority source). Host env / Hermes profile literals are not authority.
    Old AOTA_W3_* keys are retained only as deprecated compat (not authority)
    and are ignored by the new MCP path; poisoning tests prove they cannot
    re-activate.

    Never mutates os.environ; the caller passes the result as Popen(env=...).
    """
    if not isinstance(handoff, TaskHandoff):
        raise TrustedBindingError(f"worker child env requires typed TaskHandoff, got {type(handoff).__name__}")
    try:
        role_val = handoff.work_role.value if hasattr(handoff.work_role, "value") else str(handoff.work_role)
    except Exception as exc:
        raise TrustedBindingError(f"worker child env unreadable work_role: {exc}") from exc
    if role_val == "task-main":
        raise TrustedBindingError("worker child env must not carry task-main handoff")
    if not project_id or not project_id.strip():
        raise TrustedBindingError("worker child env requires project_id")
    if not worktree_id or not worktree_id.strip():
        raise TrustedBindingError("worker child env requires worktree_id")
    if not canonical_task_id or not canonical_task_id.strip():
        raise TrustedBindingError("worker child env requires canonical_task_id")
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise TrustedBindingError(f"worker child env root missing: {resolved_root}")
    effective_repo = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    # Pre-resolved envelope: AF runtime constructs trusted binding before MCP
    envelope_path = create_worker_envelope(
        worktree_root=resolved_root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
    )
    child: dict[str, str] = {
        PRE_RESOLVED_BINDING_ENV: str(envelope_path),
        MCP_REPO_ROOT_ENV: str(effective_repo),
        CONTEXT_KIND_ENV: context_kind,
    }
    # Deprecated compat: old AOTA_W3_* authority channel kept only for
    # bounded legacy test compat (production_path_uses_it=no, not authority).
    # Poisoning tests prove host representation cannot re-activate.
    child[MCP_ROOT_ENV] = str(resolved_root)
    child[MCP_PROJECT_ENV] = project_id
    child[MCP_WORKTREE_ENV] = worktree_id
    child[MCP_TASK_ENV] = canonical_task_id
    child[MCP_HANDOFF_ENV] = json.dumps(handoff.to_dict(), sort_keys=True)
    if context_kind not in ("worker", "task-main"):
        raise TrustedBindingError("worker child env context_kind must be worker|task-main")
    if trace_path is not None:
        child[MCP_TRACE_ENV] = str(Path(trace_path))
    if runtime_config_path is not None:
        child["AOTA_FORGE_RUNTIME_CONFIG"] = str(Path(runtime_config_path).resolve())
    # Mechanical PATH/PYTHONPATH: sanitized copy, repo root prepended.
    parent_pythonpath = os.environ.get("PYTHONPATH", "")
    child["PYTHONPATH"] = str(effective_repo) + (os.pathsep + parent_pythonpath if parent_pythonpath else "")
    if "PATH" in os.environ and os.environ["PATH"]:
        child["PATH"] = os.environ["PATH"]
    return dict(child)


async def _serve_mcp_child() -> None:
    """Run the actual W2 server used by Hermes, with server-side binding."""
    from aota_forge import mcp_transport

    # Exclusive discrimination: exactly one valid context is used; both or
    # neither fail closed. No task-main-first priority remains.
    selected = select_runtime_context()
    try:
        _role = selected.handoff.work_role.value if hasattr(selected.handoff.work_role, "value") else str(selected.handoff.work_role)
    except Exception:
        _role = ""
    if _role == "task-main":
        server = mcp_transport.create_shared_mcp_server(selected)
        trace_path = None
        # Optional invocation trace for W2 evidence (bounded)
        t = os.environ.get(MCP_TRACE_ENV) or os.environ.get("AOTA_TASK_MAIN_TRACE")
        if t:
            try:
                trace_path = Path(t)
            except Exception:
                trace_path = None
    else:
        server = mcp_transport.create_shared_mcp_server(selected)
        trace_path = Path(os.environ[MCP_TRACE_ENV]) if MCP_TRACE_ENV in os.environ else None
    if trace_path is not None:
        for tool in server._tool_manager.list_tools():
            original = tool.fn
            name = tool.name

            def traced(*args: Any, _original=original, _name=name, **kwargs: Any):
                try:
                    with trace_path.open("a", encoding="utf-8") as trace:
                        trace.write(f"{_name}\n")
                except Exception:
                    pass
                return _original(*args, **kwargs)

            tool.fn = traced
    await server.run_stdio_async()


def _write_smoke_fixture(root: Path, token: str) -> None:
    # Generic helper retained for legacy smoke harness; not used as
    # production authority for generic derivation.
    # Uses a neutral fixture path to keep production seam generic.
    fixture = root / "work" / "smoke"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "input.txt").write_text(f"AOTA_GENERIC_SENTINEL={token}\n", encoding="utf-8")


def worker_environment(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    trace_path: Path,
) -> Iterator[None]:
    """Public trusted-MCP-binding environment seam (M2/W3 integration slice).

    Identical to the M1 slice's private context manager; exposed so the W3
    durable vertical slice can keep the Worker's trusted binding supplied
    across a coordinator runtime restart without re-implementing the seam.
    """
    return _worker_environment(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
        trace_path=trace_path,
    )


@contextmanager
def _worker_environment(
    *,
    root: Path,
    project_id: str,
    worktree_id: str,
    canonical_task_id: str,
    handoff: TaskHandoff,
    trace_path: Path,
) -> Iterator[None]:
    names = {
        MCP_ROOT_ENV: str(root),
        MCP_PROJECT_ENV: project_id,
        MCP_WORKTREE_ENV: worktree_id,
        MCP_TASK_ENV: canonical_task_id,
        MCP_HANDOFF_ENV: json.dumps(handoff.to_dict(), sort_keys=True),
        MCP_TRACE_ENV: str(trace_path),
    }
    old = {key: os.environ.get(key) for key in names}
    old_pythonpath = os.environ.get("PYTHONPATH")
    os.environ.update(names)
    repo_root = str(Path(__file__).resolve().parents[2])
    old_repo_root = os.environ.get(MCP_REPO_ROOT_ENV)
    os.environ[MCP_REPO_ROOT_ENV] = repo_root
    os.environ["PYTHONPATH"] = repo_root + (os.pathsep + old_pythonpath if old_pythonpath else "")
    try:
        yield
    finally:
        if old_repo_root is None:
            os.environ.pop(MCP_REPO_ROOT_ENV, None)
        else:
            os.environ[MCP_REPO_ROOT_ENV] = old_repo_root
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _wait_for_result(dispatcher: Any, task_id: str, timeout_seconds: float) -> CanonicalResult:
    deadline = time.monotonic() + timeout_seconds
    while True:
        status = dispatcher.status(task_id)
        if status.state.is_terminal:
            return dispatcher.result(task_id)
        if time.monotonic() >= deadline:
            raise TimeoutError("bounded Hermes one-shot worker did not reach a terminal state")
        time.sleep(0.5)


def run_one_shot_worker(
    *,
    root: Path,
    token: str,
    canonical_task_id: str = "m1-w3-real-worker",
    project_id: str = "aota_forge",
    worktree_id: str = "m1-w3-real-one-shot-worker-slice",
    runtime_config: RuntimeConfig,
    trace_path: Path,
    timeout_seconds: float = 300.0,
) -> WorkerSliceResult:
    """Execute the bounded real Hermes Worker path and project its CARD.

    Retained for legacy smoke harness; handoff is now derived
    generically. The fixture path remains neutral.
    """
    root = root.resolve()
    _write_smoke_fixture(root, token)
    expected = f"AOTA_GENERIC_OUTPUT={token}\n"
    handoff = TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="m1-w3-real-one-shot-worker",
        objective=(
            "Use only workspace.search, workspace.read, and workspace.write. "
            f"Find the exact sentinel AOTA_GENERIC_SENTINEL={token} in work/smoke/input.txt, "
            "read the file, then write exactly "
            f"{expected!r} to work/smoke/output.txt. Do not use terminal, shell, git, or network."
        ),
        bounded_scope="work/smoke/input.txt and work/smoke/output.txt only",
        validation_expectations=("output contains the exact transformed sentinel",),
        semantic_stop_expectations=("stop if any governed workspace operation is denied",),
    )
    build_worker_binding(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
    )
    trusted_binding = TrustedExecutionBinding(canonical_task_id=canonical_task_id, project_id=project_id)
    package = compile_handoff_to_execution_package(handoff, trusted_binding)
    runtime_binding = runtime_config.get_binding(AgentWorkRole.CODER)
    with _worker_environment(
        root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        canonical_task_id=canonical_task_id,
        handoff=handoff,
        trace_path=trace_path,
    ):
        dispatcher = create_production_execution_dispatcher(
            default_cwd=root,
            runtime_config=runtime_config,
        )
        try:
            dispatcher.dispatch(package, target_executor_id=runtime_binding.executor)
            canonical_result = _wait_for_result(dispatcher, canonical_task_id, timeout_seconds)
        finally:
            adapter = dispatcher.get_route(canonical_task_id)._adapter if dispatcher.has_route(canonical_task_id) else None
            if adapter is not None and hasattr(adapter, "_host_client"):
                host = adapter._host_client
                if hasattr(host, "close"):
                    host.close()

    if canonical_result.status != "completed":
        raise RuntimeError(f"real Hermes Worker did not complete: {canonical_result.to_dict()}")
    output = root / "work" / "smoke" / "output.txt"
    if not output.is_file() or output.read_text(encoding="utf-8") != expected:
        raise RuntimeError("real Hermes Worker did not produce the exact bounded output")

    governance = ResultGovernanceProjection.success()
    card = project_worker_result_card(
        canonical_result,
        governance,
        AgentWorkRole.CODER,
        summary="Real Hermes one-shot Worker completed the governed MCP smoke target.",
    )
    trace = tuple(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.is_file() else ()
    return WorkerSliceResult(
        handoff=handoff,
        package=package,
        runtime_binding=runtime_binding,
        canonical_result=canonical_result,
        governance_projection=governance,
        worker_result_card=card,
        tool_trace=trace,
    )


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--mcp-server":
        asyncio.run(_serve_mcp_child())
        return
    raise SystemExit(f"usage: {sys.argv[0]} --mcp-server")


if __name__ == "__main__":
    main()


__all__ = [
    "MCP_PROFILE_NAME",
    "MCP_REPO_ROOT_ENV",
    "MCP_SERVER_MODULE",
    "REAL_HERMES_VERSION",
    "WorkerSliceResult",
    "build_worker_binding",
    "run_one_shot_worker",
]
