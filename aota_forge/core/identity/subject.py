"""Subject identity primitives (M3-B4, ``SUBJECT_ID_PRIMITIVES``).

Implements the accepted M3-A identity rules:

* deterministic identity — WorkspaceSubject, ProjectSubject, PlanSubject
* minted identity — WorkSubject and any authorized new semantic child Subject
* PlanSubject identity derives from the stable Plan identity + owning
  ProjectSubject namespace, NOT from the Plan content digest
* retry does not create a new Subject; executor change does not create a new
  Subject; an explicit semantic followup creates a new child Subject

Forbidden canonical Subject identity determinants are never consumed here:
title, timestamp, executor, session, filesystem path, Git SHA, current
pointer, and similarity are absent by construction.
"""

from __future__ import annotations

import os

from aota_forge.core.identity.digest import (
    plan_subject_value,
    project_subject_value,
    workspace_subject_value,
)
from aota_forge.core.identity.ids import (
    InternalId,
    subject_id_from_value,
)
from aota_forge.core.identity.kinds import SubjectKind

MINT_BYTES = 24


def workspace_subject(workspace_id: str) -> InternalId:
    """Deterministic WorkspaceSubject (stable across metadata change)."""
    return subject_id_from_value(SubjectKind.WORKSPACE, workspace_subject_value(workspace_id))


def project_subject(project_id: str, owning_workspace_subject: InternalId) -> InternalId:
    """Deterministic ProjectSubject derived from project.id + owning workspace."""
    if owning_workspace_subject.kind != "subject" or owning_workspace_subject.sub_kind != SubjectKind.WORKSPACE:
        raise TypeError("owning_workspace_subject must be a WorkspaceSubject InternalId")
    value = project_subject_value(project_id, owning_workspace_subject.to_canonical())
    return subject_id_from_value(SubjectKind.PROJECT, value)


def plan_subject(plan_id: str, owning_project_subject: InternalId) -> InternalId:
    """Deterministic PlanSubject, stable across Plan content/revision changes.

    Only the stable Plan id and the owning ProjectSubject identity are
    canonical inputs; the Plan content digest never participates
    (``PLAN_CONTENT_DIGEST_NOT_IDENTITY=yes``).  A different owning
    ProjectSubject yields a different PlanSubject identity.
    """
    if owning_project_subject.kind != "subject" or owning_project_subject.sub_kind != SubjectKind.PROJECT:
        raise TypeError("owning_project_subject must be a ProjectSubject InternalId")
    value = plan_subject_value(plan_id, owning_project_subject.to_canonical())
    return subject_id_from_value(SubjectKind.PLAN, value)


def _random_token() -> str:
    return os.urandom(MINT_BYTES).hex()


def mint_work_subject_value() -> str:
    """Non-inferable minted value for a WorkSubject.

    Drawn from the OS CSPRNG: not guessable from executor/session/title, and
    never derived from any canonical content tuple.  Collision resistance is
    overwhelming; any collision is still caught fail-closed by the ID Broker.
    """
    return "wid_" + _random_token()


def mint_subject_value(sub_kind: str, prefix: str) -> str:
    """Mint a non-inferable identity value for an authorized semantic subject."""
    return f"{prefix}_{_random_token()}"