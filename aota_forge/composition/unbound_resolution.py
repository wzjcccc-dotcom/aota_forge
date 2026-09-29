"""Trusted selector resolution for an ordinary unbound task-main session.

Selectors locate server-owned Project/Plan records only. They never carry a
filesystem root, session identity, authority binding, or capability. The
returned values reuse the existing trusted Project binding, ProjectPlanRecord,
and PlanAuthorityBinding contracts for the later same-session bind seam.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
)
from aota_forge.composition.plan_authority import (
    PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
    PlanAuthorityCompositionError,
    resolve_bound_plan_authority,
)
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.composition.project_binding import (
    TrustedProjectBindingEvidence,
    resolve_trusted_project_binding,
)
from aota_forge.core.contracts.errors import (
    ProjectAmbiguousError,
    ProjectNotFoundError,
    ProjectRegistryInvalidError,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.project.resolver import (
    ProjectResolutionEvidence,
    resolve_project_selector_across_workspaces,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    ProjectPlanRecord,
)
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


TRUSTED_PROJECT_SELECTOR_RESOLUTION_IMPLEMENTED = True
TRUSTED_OPTIONAL_PLAN_SELECTOR_RESOLUTION_IMPLEMENTED = True
PROJECT_SELECTOR_IS_AUTHORITY = False
SELECTOR_IS_LOCATOR_ONLY = True
MODEL_MINTS_AUTHORITY = False
CWD_IS_AUTHORITY = False
FILESYSTEM_PATH_IS_AUTHORITY = False
CONTROL_PLANE_SEMANTIC_RANKING = False
LOCAL_GOV2_RESOLUTION_INDEPENDENT_OF_GITHUB = True
NEW_PROJECT_DISCOVERY_FRAMEWORK = False
NEW_AUTHORITY_ENGINE = False
NEW_CONTEXT_ENGINE = False
NEW_SESSION_DATABASE = False
SAME_SESSION_BIND_MATERIALIZATION_EXECUTED = False

PROJECT_SELECTOR_INVALID = "PROJECT_SELECTOR_INVALID"
PLAN_SELECTOR_INVALID = "PLAN_SELECTOR_INVALID"
PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
PLAN_AMBIGUOUS = "PLAN_AMBIGUOUS"


class UnboundResolutionError(ValueError):
    """Typed fail-closed Plan selector result."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        candidates: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.code = code
        self.candidates = tuple(candidates)
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TrustedUnboundResolution:
    """Existing trusted Project evidence plus an optional local Plan record."""

    project: TrustedProjectBindingEvidence
    plan_record: ProjectPlanRecord | None = None
    plan_binding: PlanAuthorityBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project, TrustedProjectBindingEvidence):
            raise TypeError("project must be TrustedProjectBindingEvidence")
        if self.plan_record is None:
            if self.plan_binding is not None:
                raise ValueError("plan_binding requires a selected plan_record")
            return
        if not isinstance(self.plan_record, ProjectPlanRecord):
            raise TypeError("plan_record must be ProjectPlanRecord or None")
        if self.plan_record.project_id != self.project.project_id:
            raise ValueError("Plan resolution escaped the resolved Project")
        if not isinstance(self.plan_binding, PlanAuthorityBinding):
            raise TypeError("selected Plan requires a PlanAuthorityBinding")
        if self.plan_binding.plan_id != self.plan_record.plan_id:
            raise ValueError("plan_binding does not match plan_record")
        if self.plan_binding != self.plan_record.authority:
            raise ValueError("plan_binding must be the trusted ProjectPlanRecord authority")

    @property
    def project_id(self) -> str:
        return self.project.project_id

    @property
    def plan_id(self) -> str | None:
        return self.plan_record.plan_id if self.plan_record is not None else None


def _selector_text(value: object, *, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or type(value) is not str:
        raise UnboundResolutionError(
            PROJECT_SELECTOR_INVALID if label == "project_selector" else PLAN_SELECTOR_INVALID,
            f"{label} must be a bounded selector string",
        )
    candidate = value.strip()
    if not candidate or len(candidate) > 256 or "\x00" in candidate:
        raise UnboundResolutionError(
            PROJECT_SELECTOR_INVALID if label == "project_selector" else PLAN_SELECTOR_INVALID,
            f"{label} is empty or exceeds the bounded selector limit",
        )
    return candidate


def _project_candidate_metadata(evidence: ProjectResolutionEvidence) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "workspace_id": candidate.workspace_id,
            "project_id": candidate.project_id,
            "name": candidate.name,
            "kind": candidate.kind,
            "status": candidate.status,
        }
        for candidate in evidence.candidates
    )


def _resolve_project_selector(
    selector: str,
    *,
    registry_path: Path,
    workspace_id: str | None,
) -> TrustedProjectBindingEvidence:
    if selector.startswith(("/", "~")) or "/" in selector or "\\" in selector:
        raise UnboundResolutionError(
            PROJECT_SELECTOR_INVALID,
            "project_selector is a semantic Project id/name, not a filesystem path",
        )
    evidence = resolve_project_selector_across_workspaces(
        registry_path,
        selector,
        workspace_id=workspace_id,
    )
    metadata = _project_candidate_metadata(evidence)
    if evidence.status == "PROJECT_NOT_FOUND":
        raise ProjectNotFoundError(
            f"no trusted Project matched selector {selector!r}",
            details={"selector": selector, "candidates": list(metadata)},
        )
    if evidence.status == "NEEDS_SEMANTIC_CHOICE":
        raise ProjectAmbiguousError(
            f"trusted Project selector {selector!r} matched multiple candidates",
            details={"selector": selector, "candidates": list(metadata)},
        )
    if evidence.status != "RESOLVED" or len(evidence.candidates) != 1:
        raise ProjectRegistryInvalidError("trusted Project selector did not produce singular evidence")
    candidate = evidence.candidates[0]
    return resolve_trusted_project_binding(
        project_id=candidate.project_id,
        registry_path=registry_path,
        workspace_id=candidate.workspace_id,
    )


def _plan_candidate_metadata(records: tuple[ProjectPlanRecord, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "plan_id": record.plan_id,
            "authority_ref": record.authority.authority_ref,
            "lifecycle_state": record.lifecycle_state,
        }
        for record in records
    )


def _resolve_plan_selector(
    project: TrustedProjectBindingEvidence,
    selector: str,
    *,
    governance_base: Path | str | None,
    governance_store_path: Path | str | None,
) -> tuple[ProjectPlanRecord, PlanAuthorityBinding]:
    if selector.startswith(("/", "~")) or "\\" in selector:
        raise UnboundResolutionError(
            PLAN_SELECTOR_INVALID,
            "plan_selector is a canonical Plan identity/reference, not a filesystem path",
        )
    if governance_base is None or governance_store_path is None:
        raise PlanAuthorityCompositionError(
            "trusted local governance inputs are required for Plan resolution",
            code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
        )
    try:
        governance_root = LocalGovernanceRootBinding.from_trusted_base(
            governance_base,
            project_id=project.project_id,
        )
        store = open_project_governance_store(governance_store_path)
    except Exception as exc:
        raise PlanAuthorityCompositionError(
            "trusted local governance store is unavailable",
            code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
        ) from exc
    try:
        active_records = tuple(
            record
            for record in store.list_plans(project.project_id)
            if record.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        )
        matches = tuple(
            record
            for record in active_records
            if selector in (record.plan_id, record.authority.authority_ref)
        )
        if not matches:
            raise UnboundResolutionError(
                PLAN_NOT_FOUND,
                f"no active Plan in Project {project.project_id!r} matched selector {selector!r}",
            )
        if len(matches) > 1:
            raise UnboundResolutionError(
                PLAN_AMBIGUOUS,
                f"Plan selector {selector!r} is ambiguous within Project {project.project_id!r}",
                candidates=_plan_candidate_metadata(matches),
            )
        record = matches[0]
        binding = record.authority
        if binding.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            raise PlanAuthorityCompositionError(
                "selected Plan is not backed by the trusted local Gov2 authority",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        if binding.source_revision is None or binding.source_digest is None:
            raise PlanAuthorityCompositionError(
                "selected local Plan has no observed source revision/digest",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        destination = LocalPlanAuthorityDestination(
            governance_root,
            record.plan_id,
            object_ref_subject(make_id(IdKind.SUBJECT, record.plan_id, sub_kind=SubjectKind.PLAN)),
        )
        reader = resolve_bound_plan_authority(
            binding,
            local_destination=destination,
            governance_store=store,
        )
        if not isinstance(reader, LocalPlanAuthorityReadAdapter):
            raise PlanAuthorityCompositionError(
                "selected local Plan did not resolve to the local read adapter",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        snapshot = reader.load()
        if snapshot.revision != binding.source_revision or snapshot.digest != binding.source_digest:
            raise PlanAuthorityCompositionError(
                "selected local Plan source changed after trusted observation",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        try:
            load_local_portable_plan(reader)
            verified_snapshot = reader.load()
        except Exception as exc:
            raise PlanAuthorityCompositionError(
                "selected local Plan could not be normalized",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            ) from exc
        if (
            verified_snapshot.revision != snapshot.revision
            or verified_snapshot.digest != snapshot.digest
            or verified_snapshot.body != snapshot.body
        ):
            raise PlanAuthorityCompositionError(
                "selected local Plan source changed during trusted read",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        current_record = store.get_plan(project.project_id, record.plan_id)
        if (
            current_record is None
            or current_record != record
            or current_record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE
        ):
            raise PlanAuthorityCompositionError(
                "selected local Plan governance record changed during trusted read",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        return record, binding
    finally:
        store.close()


def resolve_trusted_unbound_resolution(
    project_selector: str,
    plan_selector: str | None = None,
    *,
    registry_path: Path | str,
    workspace_id: str | None = None,
    governance_base: Path | str | None = None,
    governance_store_path: Path | str | None = None,
) -> TrustedUnboundResolution:
    """Resolve semantic selectors using only operator-owned trusted inputs."""
    project_value = _selector_text(project_selector, label="project_selector")
    assert project_value is not None
    registry = Path(registry_path)
    project = _resolve_project_selector(
        project_value,
        registry_path=registry,
        workspace_id=workspace_id,
    )
    plan_value = _selector_text(plan_selector, label="plan_selector", allow_none=True)
    if plan_value is None:
        return TrustedUnboundResolution(project=project)
    plan_record, plan_binding = _resolve_plan_selector(
        project,
        plan_value,
        governance_base=governance_base,
        governance_store_path=governance_store_path,
    )
    return TrustedUnboundResolution(
        project=project,
        plan_record=plan_record,
        plan_binding=plan_binding,
    )


def recompose_trusted_context_from_evidence(
    *,
    durable_binding: Any,
    origin_session_ref: str,
    registry_path: Path | str,
    workspace_id: str | None,
    runtime_config_path: Path | str,
    worktree_root: Path | str | None,
    worktree_id: str | None,
    governance_base: Path | str | None,
    governance_store_path: Path | str | None,
    git_integration_branch: str | None = None,
    git_remote: str | None = None,
) -> tuple[Any, Any, Any, DurableTaskMainContextBinding]:
    """Re-resolve live authority and compose a fresh thin bound runtime.

    ``durable_binding`` is only a locator/evidence record. The Project
    registry, Project Governance Store, Plan source and AuthorizedRootSet are
    read again from their current trusted owners before a runtime object is
    returned. No historical ``TrustedWorkerBinding`` is deserialized.
    """

    from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
    from aota_forge.core.execution.durable_state import is_bound_origin_session_ref
    from aota_forge.runtime.task_main.context_binding import (
        ContextBindingError,
        DurableTaskMainContextBinding,
    )
    from aota_forge.work_plane.authorized_roots import validate_root_set_against_sandbox

    if not isinstance(durable_binding, DurableTaskMainContextBinding):
        raise ContextBindingError(
            "CONTEXT_BINDING_CORRUPT",
            "resume requires a typed durable context binding",
        )
    if origin_session_ref != durable_binding.origin_task_main_session_ref:
        raise ContextBindingError(
            "SESSION_RECOVERY_REQUIRED",
            "exact task-main session differs from durable context evidence",
        )
    if not is_bound_origin_session_ref(origin_session_ref):
        raise ContextBindingError(
            "SESSION_RECOVERY_REQUIRED",
            "resume requires an exact non-placeholder session",
        )

    plan_id = durable_binding.plan_binding.plan_id
    resolution = resolve_trusted_unbound_resolution(
        durable_binding.project_id,
        plan_id,
        registry_path=registry_path,
        workspace_id=workspace_id,
        governance_base=governance_base,
        governance_store_path=governance_store_path,
    )
    candidate = resolution.project.candidate
    if candidate is None or candidate.candidate_fingerprint != durable_binding.project_fingerprint:
        raise ContextBindingError(
            "PROJECT_CONTEXT_MISMATCH",
            "current trusted Project evidence differs from durable context evidence",
        )
    if not isinstance(resolution.plan_binding, PlanAuthorityBinding):
        raise ContextBindingError(
            "STALE_CONTEXT",
            "current trusted Plan binding is unavailable",
        )

    trusted_worktree_root = (
        str(worktree_root).strip()
        if worktree_root is not None and str(worktree_root).strip()
        else candidate.project_root
    )
    trusted_worktree_id = (
        str(worktree_id).strip()
        if worktree_id is not None and str(worktree_id).strip()
        else f"{resolution.project_id}-unbound"
    )
    host = compose_thin_task_main_host(
        worktree_root=trusted_worktree_root,
        project_id=resolution.project_id,
        worktree_id=trusted_worktree_id,
        runtime_config_path=runtime_config_path,
        origin_task_main_session_ref=origin_session_ref,
        plan_ref=resolution.plan_binding.authority_ref,
        source_repository=resolution.project.source_repository or None,
        registry_path=registry_path,
        workspace_id=workspace_id,
        governance_base=governance_base,
        governance_store_path=governance_store_path,
        plan_id=resolution.plan_binding.plan_id,
        git_integration_branch=git_integration_branch,
        git_remote=git_remote,
    )
    try:
        actual_evidence = getattr(host, "project_evidence", None)
        actual_candidates = tuple(getattr(actual_evidence, "candidates", ()) or ())
        if (
            getattr(actual_evidence, "status", None) != "RESOLVED"
            or len(actual_candidates) != 1
            or actual_candidates[0].candidate_fingerprint != durable_binding.project_fingerprint
        ):
            raise ContextBindingError(
                "PROJECT_CONTEXT_MISMATCH",
                "trusted Project changed during resume composition",
            )
        actual_plan_binding = getattr(host.trusted_binding, "plan_authority_binding", None)
        if not isinstance(actual_plan_binding, PlanAuthorityBinding):
            raise ContextBindingError(
                "STALE_CONTEXT",
                "fresh trusted runtime did not carry a Plan authority binding",
            )
        roots = host.trusted_binding.effective_authorized_roots
        validate_root_set_against_sandbox(roots, host.trusted_binding.sandbox)
        current_binding = DurableTaskMainContextBinding(
            project_id=resolution.project_id,
            project_fingerprint=actual_candidates[0].candidate_fingerprint,
            plan_binding=actual_plan_binding,
            origin_task_main_session_ref=origin_session_ref,
            authorized_roots_digest=roots.digest(),
        )
        durable_binding.assert_resume_identity_compatible(current_binding)
        if durable_binding.authorized_roots_digest != current_binding.authorized_roots_digest:
            raise ContextBindingError(
                "AUTHORITY_DENIED",
                "trusted AuthorizedRootSet differs from durable context evidence",
            )
        governance_context = materialize_trusted_governance_context(
            resolution,
            binding=host.trusted_binding,
            authorized_roots=roots,
            governance_store=getattr(host, "governance_store", None),
            governance_root=getattr(host, "local_governance_binding", None),
        )
        bound_binding = replace(
            host.trusted_binding,
            governance_context=governance_context,
        )
        return host, bound_binding, roots, current_binding
    except Exception:
        for resource in (
            getattr(host, "governance_store", None),
            getattr(host, "execution_store", None),
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise


def materialize_trusted_governance_context(
    resolution: TrustedUnboundResolution,
    *,
    binding: object,
    authorized_roots: object,
    governance_store: object | None = None,
    governance_root: LocalGovernanceRootBinding | None = None,
) -> dict[str, object]:
    """Build the existing Cards/ContextRoute projection from trusted facts.

    This is a projection-only step.  Project/Plan authority remains owned by
    the resolver and runtime binding; the returned mapping is explicitly
    non-authoritative and is consumed by the existing role/bootstrap path.
    """
    from aota_forge.governance.context_route import build_bootstrap_governance_context
    from aota_forge.governance.projection import (
        GovernanceProjectionEngine,
        GovernanceProjectionInput,
        plan_document_input,
    )
    from aota_forge.work_plane.authorized_roots import AuthorizedRootSet

    if not isinstance(resolution, TrustedUnboundResolution):
        raise PlanAuthorityCompositionError("trusted unbound resolution is required")
    if not isinstance(authorized_roots, AuthorizedRootSet):
        raise PlanAuthorityCompositionError("trusted AuthorizedRootSet is required")

    plan_record = resolution.plan_record
    plan_binding = getattr(binding, "plan_authority_binding", None)
    plan_documents: tuple[object, ...] = ()
    governance_root_refs: tuple[str, ...] = ()
    if plan_record is not None:
        if governance_store is None or not isinstance(governance_root, LocalGovernanceRootBinding):
            raise PlanAuthorityCompositionError(
                "Plan-bound projection requires the trusted local governance store and root"
            )
        if not isinstance(plan_binding, PlanAuthorityBinding):
            raise PlanAuthorityCompositionError("Plan-bound runtime binding is missing")
        current_record = governance_store.get_plan(resolution.project_id, plan_record.plan_id)  # type: ignore[attr-defined]
        if current_record is None or current_record != plan_record:
            raise PlanAuthorityCompositionError(
                "Project Governance record changed during context materialization",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        if current_record.authority != plan_binding:
            raise PlanAuthorityCompositionError(
                "runtime Plan authority differs from the trusted Project record",
                code=PLAN_AUTHORITY_COMPOSITION_INVALID,
            )
        destination = LocalPlanAuthorityDestination(
            governance_root,
            plan_record.plan_id,
            object_ref_subject(
                make_id(IdKind.SUBJECT, plan_record.plan_id, sub_kind=SubjectKind.PLAN)
            ),
        )
        reader = LocalPlanAuthorityReadAdapter(destination, binding=current_record.authority)
        snapshot = reader.load()
        if (
            snapshot.revision != plan_binding.source_revision
            or snapshot.digest != plan_binding.source_digest
        ):
            raise PlanAuthorityCompositionError(
                "Plan source changed during context materialization",
                code=PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
            )
        document = load_local_portable_plan(reader)
        plan_documents = (
            plan_document_input(
                plan_id=plan_record.plan_id,
                document=document,
                title=getattr(document, "title", None),
                current_milestone=getattr(document, "current_milestone", None),
                authority=current_record.authority,
            ),
        )
        governance_root_refs = ("local-governance",)

    candidate = resolution.project.candidate
    if candidate is None:
        raise PlanAuthorityCompositionError("trusted Project candidate is missing")
    source = GovernanceProjectionInput(
        project_id=resolution.project_id,
        project_ref=f"project:{resolution.project_id}",
        project_status=candidate.status,
        governance_root_refs=governance_root_refs,
        # None explicitly means the Project plan inventory was not read for a
        # project-only bind; it is not an assertion that no Plans exist.
        plan_records=(plan_record,) if plan_record is not None else None,
        plan_documents=plan_documents,
    )
    bundle = GovernanceProjectionEngine().rebuild(source)
    root_refs = tuple(
        (root.root_ref, tuple(sorted(root.capabilities)))
        for root in authorized_roots.roots
    )
    return build_bootstrap_governance_context(
        project_id=resolution.project_id,
        plan_id=plan_binding.plan_id if isinstance(plan_binding, PlanAuthorityBinding) else None,
        authority_source_kind=(
            plan_binding.source_kind if isinstance(plan_binding, PlanAuthorityBinding) else None
        ),
        authority_ref=(
            plan_binding.authority_ref if isinstance(plan_binding, PlanAuthorityBinding) else None
        ),
        authority_revision=(
            str(plan_binding.source_revision)
            if isinstance(plan_binding, PlanAuthorityBinding)
            and plan_binding.source_revision is not None
            else None
        ),
        authority_digest=(
            plan_binding.source_digest if isinstance(plan_binding, PlanAuthorityBinding) else None
        ),
        bundle=bundle,
        authorized_root_refs=root_refs,
    )


__all__ = [
    "TRUSTED_PROJECT_SELECTOR_RESOLUTION_IMPLEMENTED",
    "TRUSTED_OPTIONAL_PLAN_SELECTOR_RESOLUTION_IMPLEMENTED",
    "PROJECT_SELECTOR_IS_AUTHORITY",
    "SELECTOR_IS_LOCATOR_ONLY",
    "MODEL_MINTS_AUTHORITY",
    "CWD_IS_AUTHORITY",
    "FILESYSTEM_PATH_IS_AUTHORITY",
    "CONTROL_PLANE_SEMANTIC_RANKING",
    "LOCAL_GOV2_RESOLUTION_INDEPENDENT_OF_GITHUB",
    "NEW_PROJECT_DISCOVERY_FRAMEWORK",
    "NEW_AUTHORITY_ENGINE",
    "NEW_CONTEXT_ENGINE",
    "NEW_SESSION_DATABASE",
    "SAME_SESSION_BIND_MATERIALIZATION_EXECUTED",
    "PROJECT_SELECTOR_INVALID",
    "PLAN_SELECTOR_INVALID",
    "PLAN_NOT_FOUND",
    "PLAN_AMBIGUOUS",
    "UnboundResolutionError",
    "TrustedUnboundResolution",
    "materialize_trusted_governance_context",
    "recompose_trusted_context_from_evidence",
    "resolve_trusted_unbound_resolution",
]
