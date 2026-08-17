"""Canonical deterministic derivation (M3-B4).

One canonical algorithm is used for every deterministic ID
(``DETERMINISTIC_DERIVATION``): a stable canonical (sorted, minimal-whitespace,
UTF-8) JSON serialization of the identity tuple is fed to SHA-256 and the
resulting hex digest is the identity value.

Guarantees required by the plan:

* stable — same canonical tuple always yields the same ID
* canonical serialization precedes derivation
* irrelevant metadata change -> same ID (only canonical components participate)
* different canonical owner tuple -> different ID
* PlanSubject identity is derived from the stable Plan id + owning
  ProjectSubject identity + subject namespace, never from a content digest

Hash choice: SHA-256 (``hashlib.sha256``), a deterministic standard available
in the runtime.  The collision resistance and 256-bit width are an
implementation detail; only stability and determinism are architectural.
"""

from __future__ import annotations

import hashlib
import json

DIGEST_ALGORITHM = "sha256"
DERIVATION_NAMESPACE_PREFIX = "aota.forge.identity"
CANONICAL_SUBJECT_NAMESPACE = "aota.forge.subject"


def canonical_json(value: object) -> str:
    """Deterministic JSON serialization for derivation inputs.

    ``sort_keys`` / minimal separators / non-ASCII kept literal make the
    serialization stable across Python versions and hosts.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive_id(namespace: str, *components: object) -> str:
    """Derive a deterministic internal identity value from a canonical tuple.

    ``namespace`` scopes the derivation domain so the same components under
    different namespaces cannot collide; components are canonicalized and the
    whole tuple hashed once.
    """
    material = canonical_json((namespace, *components))
    return sha256_hex(material)


def workspace_subject_value(workspace_id: str) -> str:
    """Deterministic WorkspaceSubject identity value.

    Canonical tuple: (subject namespace, WorkspaceSubject, workspace_id).
    Independent of title/timestamp/executor/host/path (``SUBJECT_ID_INDEPENDENT_OF
    .workspace_id is not a determinant; workspace_id is the authoritative
    bootstrap input``).
    """
    return derive_id(CANONICAL_SUBJECT_NAMESPACE, "WorkspaceSubject", workspace_id)


def project_subject_value(project_id: str, owning_workspace_subject: str) -> str:
    """Deterministic ProjectSubject identity value.

    Canonical tuple: (subject namespace, ProjectSubject, project_id,
    owning_workspace_subject identity).  A different owning WorkspaceSubject
    changes the ProjectSubject identity (different canonical owner tuple).
    """
    return derive_id(
        CANONICAL_SUBJECT_NAMESPACE,
        "ProjectSubject",
        project_id,
        owning_workspace_subject,
    )


def plan_subject_value(plan_id: str, owning_project_subject: str) -> str:
    """Deterministic PlanSubject identity value (stable across revisions).

    Canonical tuple: (subject namespace, PlanSubject, plan_id,
    owning_project_subject identity).  NO content digest participates, so an
    ordinary Plan document revision keeps the same PlanSubject identity
    (``PLAN_CONTENT_DIGEST_NOT_IDENTITY=yes``).  A different owning
    ProjectSubject changes the PlanSubject identity.
    """
    return derive_id(
        CANONICAL_SUBJECT_NAMESPACE,
        "PlanSubject",
        plan_id,
        owning_project_subject,
    )