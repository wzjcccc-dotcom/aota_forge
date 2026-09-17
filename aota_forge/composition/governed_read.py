"""AF #57 M2/W4 — bounded composition seam for governed read extensions.

Bridges the three trusted pieces without creating a second authority chain:

    existing trusted Project resolution (composition.project_binding)
  + durable CrossProjectGrant (governance.cross_project_grant)
  + AuthorizedRootSet materialization (work_plane.authorized_roots)

No new resolution logic, no new sandbox, no new store and no public Tool.
The model never supplies a path, a target project identity or an authority
basis through this seam.

Invariants
----------
* GOVERNED_READ_COMPOSITION_IMPLEMENTED=yes
* GRANT_MATERIALIZATION_REUSES_TRUSTED_REGISTRY=yes
* GRANT_STORE_OWNER=project_governance_store
* SECOND_GRANT_DATABASE_CREATED=no
* SECOND_EVIDENCE_STORE_CREATED=no
* CROSS_PROJECT_WRITE_ALLOWED=no
* DEFAULT_ROOT_SET_UNCHANGED_WITHOUT_GRANTS=yes
* EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING=yes
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from aota_forge.composition.project_binding import (
    TrustedProjectBindingEvidence,
    resolve_trusted_project_binding,
)
from aota_forge.core.project.resolver import ProjectResolutionEvidence
from aota_forge.governance.cross_project_grant import (
    CROSS_PROJECT_GRANT_CAPABILITIES,
    END_CONDITION_UNTIL_REVOKED,
    ROOT_KIND_PROJECT_MAIN,
    CrossProjectGrant,
    CrossProjectGrantAuthorityError,
    CrossProjectGrantStore,
    ExplicitUserApproval,
    PreapprovedByPlan,
    TargetProjectResolution,
    create_cross_project_grant,
    require_live_cross_project_grant,
)
from aota_forge.work_plane.authorized_roots import (
    AuthorizedEvidenceRootBinding,
    AuthorizedRootSet,
    CrossProjectGrantRootBinding,
    CrossProjectGrantRootError,
    authorize_cross_project_read_root,
    resolve_cross_project_grant_root,
)
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

GOVERNED_READ_COMPOSITION_IMPLEMENTED = True
GRANT_MATERIALIZATION_REUSES_TRUSTED_REGISTRY = True
GRANT_STORE_OWNER = "project_governance_store"
SECOND_GRANT_DATABASE_CREATED = False
SECOND_EVIDENCE_STORE_CREATED = False
CROSS_PROJECT_WRITE_ALLOWED = False
DEFAULT_ROOT_SET_UNCHANGED_WITHOUT_GRANTS = True
EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING = True
EVIDENCE_ROOT_MODEL_PATH_ACCEPTED = False


def resolve_cross_project_target_project(
    *,
    project_id: str,
    registry_path: Path | str | None = None,
    workspace_id: str | None = None,
    workspace_root: Path | str | None = None,
) -> TrustedProjectBindingEvidence:
    """Resolve the logical target project through the existing trusted binding.

    Delegates exactly to ``resolve_trusted_project_binding`` (canonical
    discovery/resolver, no heuristic fallback, no cwd, no folder/repo-name
    guessing).  Never accepts a model physical path.
    """
    return resolve_trusted_project_binding(
        project_id=project_id,
        registry_path=registry_path,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
    )


def trusted_target_resolution(
    binding: TrustedProjectBindingEvidence,
) -> ProjectResolutionEvidence:
    """Extract the singular RESOLVED evidence from a trusted binding."""
    if not isinstance(binding, TrustedProjectBindingEvidence):
        raise CrossProjectGrantAuthorityError(
            "target project requires the trusted TrustedProjectBindingEvidence"
        )
    evidence = binding.resolution
    if not isinstance(evidence, ProjectResolutionEvidence):
        raise CrossProjectGrantAuthorityError(
            "trusted binding does not carry canonical Project resolution evidence"
        )
    if evidence.status != "RESOLVED" or len(evidence.candidates) != 1:
        raise CrossProjectGrantAuthorityError(
            "trusted target binding must be singular RESOLVED (no heuristic fallback)"
        )
    if evidence.candidates[0].project_id != binding.project_id:
        raise CrossProjectGrantAuthorityError(
            "trusted binding candidate identity does not match its project identity"
        )
    return evidence


def create_bound_cross_project_grant(
    *,
    store: CrossProjectGrantStore,
    requesting_project: str,
    target_binding: TrustedProjectBindingEvidence,
    authority: PreapprovedByPlan | ExplicitUserApproval,
    root_kind: str = ROOT_KIND_PROJECT_MAIN,
    capabilities: object = CROSS_PROJECT_GRANT_CAPABILITIES,
    bounded_scope: str = "",
    end_condition: str = END_CONDITION_UNTIL_REVOKED,
    bound_plan_id: str = "",
) -> CrossProjectGrant:
    """Create a durable grant from a trusted target binding (no raw paths)."""
    evidence = trusted_target_resolution(target_binding)
    target = TargetProjectResolution.from_evidence(
        evidence, expected_project_id=target_binding.project_id
    )
    return create_cross_project_grant(
        store=store,
        requesting_project=requesting_project,
        target=target,
        authority=authority,
        root_kind=root_kind,
        capabilities=capabilities,
        bounded_scope=bounded_scope,
        end_condition=end_condition,
        bound_plan_id=bound_plan_id,
    )


def bind_authorized_evidence_root(
    *, evidence_base: object, project_id: object
) -> AuthorizedEvidenceRootBinding:
    """Trusted evidence-base binding seam (never a model-supplied path)."""
    return AuthorizedEvidenceRootBinding.from_trusted_base(
        evidence_base, project_id=project_id
    )


def materialize_cross_project_grant_binding(
    *,
    grant: CrossProjectGrant,
    target_binding: TrustedProjectBindingEvidence,
) -> CrossProjectGrantRootBinding:
    """Build the bounded foreign root binding for one live grant.

    The grant stores logical identity only; the physical target root comes
    exclusively from the fresh trusted resolution supplied here, and the
    grant's bounded scope becomes the mechanically enforced root path.
    """
    if not isinstance(grant, CrossProjectGrant):
        raise CrossProjectGrantRootError("materialization requires a CrossProjectGrant")
    if target_binding.project_id != grant.target_project:
        raise CrossProjectGrantRootError(
            "trusted target binding project does not match the grant target project"
        )
    evidence = trusted_target_resolution(target_binding)
    candidate = evidence.candidates[0]
    scoped_root = resolve_cross_project_grant_root(
        target_project_root=candidate.project_root,
        bounded_scope=grant.bounded_scope,
    )
    return CrossProjectGrantRootBinding(
        grant_id=grant.grant_id,
        requesting_project=grant.requesting_project,
        target_project=grant.target_project,
        root_kind=grant.root_kind,
        granted_capabilities=grant.capabilities,
        bounded_scope=grant.bounded_scope,
        root_path=scoped_root,
        authority_digest=grant.authority.authority_digest,
        revision=grant.revision,
    )


def materialize_live_cross_project_read_roots(
    *,
    sandbox: WorktreeSandboxBoundary,
    roots: AuthorizedRootSet,
    store: CrossProjectGrantStore,
    target_bindings: Mapping[str, TrustedProjectBindingEvidence],
) -> AuthorizedRootSet:
    """Materialize every live grant of the session project into the root set.

    Default behavior is preserved exactly: without any live grant the caller's
    root set is returned untouched.  Each live grant additionally requires a
    fresh trusted target resolution; a missing resolution fails closed rather
    than guessing.
    """
    if not isinstance(sandbox, WorktreeSandboxBoundary):
        raise CrossProjectGrantRootError("sandbox must be WorktreeSandboxBoundary")
    if not isinstance(roots, AuthorizedRootSet):
        raise CrossProjectGrantRootError("roots must be an AuthorizedRootSet")
    if not isinstance(store, CrossProjectGrantStore):
        raise CrossProjectGrantRootError("materialization requires the grant store surface")
    if not isinstance(target_bindings, Mapping):
        raise CrossProjectGrantRootError("target_bindings must be a mapping by target project")
    grants = store.list_cross_project_grants(sandbox.project_id, active_only=True)
    if not grants:
        return roots
    materialized = roots
    for grant in grants:
        require_live_cross_project_grant(grant, store=store)
        binding = target_bindings.get(grant.target_project)
        if not isinstance(binding, TrustedProjectBindingEvidence):
            raise CrossProjectGrantRootError(
                f"live grant {grant.grant_id} has no fresh trusted target resolution"
            )
        grant_binding = materialize_cross_project_grant_binding(
            grant=grant, target_binding=binding
        )
        materialized = authorize_cross_project_read_root(
            sandbox, materialized, grant_binding
        )
    return materialized


__all__ = [
    "GOVERNED_READ_COMPOSITION_IMPLEMENTED",
    "GRANT_MATERIALIZATION_REUSES_TRUSTED_REGISTRY",
    "GRANT_STORE_OWNER",
    "SECOND_GRANT_DATABASE_CREATED",
    "SECOND_EVIDENCE_STORE_CREATED",
    "CROSS_PROJECT_WRITE_ALLOWED",
    "DEFAULT_ROOT_SET_UNCHANGED_WITHOUT_GRANTS",
    "EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING",
    "EVIDENCE_ROOT_MODEL_PATH_ACCEPTED",
    "resolve_cross_project_target_project",
    "trusted_target_resolution",
    "create_bound_cross_project_grant",
    "bind_authorized_evidence_root",
    "materialize_cross_project_grant_binding",
    "materialize_live_cross_project_read_roots",
]
