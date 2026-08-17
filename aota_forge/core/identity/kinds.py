"""Typed ID namespaces (M3-B4).

Canonical graph objects get distinct durable ID namespaces rather than a
single untyped bare-string API (``TYPED_ID_NAMESPACES=yes``).

Graph object kinds mirror the M3-B3 canonical records: Workflow, Subject,
Execution, Completion, Decision, FollowupEdge.  Subject carries an additional
deterministic subject-kind discriminator so a WorkspaceSubject ID can never be
mistaken for a ProjectSubject / PlanSubject / WorkSubject ID
(``WRONG_KIND_ID_REJECTED=yes``).

``BARE_ID_IMPLICIT_KIND_ALLOWED=no``: a kind must always be supplied
explicitly; a bare value is never auto-typed.
"""

from __future__ import annotations

from aota_forge.core.identity.errors import UnknownIdKindError


class IdKind:
    """Graph object kinds for canonical durable objects."""

    WORKFLOW = "workflow"
    SUBJECT = "subject"
    EXECUTION = "execution"
    COMPLETION = "completion"
    DECISION = "decision"
    EDGE = "edge"


class SubjectKind:
    """Subject-kind discriminator for the ``Subject`` graph object kind.

    Workspace / Project / Plan are deterministic; Work and authorized
    semantic child subjects are minted.  The set is open for later authorized
    semantic child kinds defined by canonical schema (M3-B3 owner), but the
    three deterministic bootstrap kinds are frozen here.
    """

    WORKSPACE = "workspace"
    PROJECT = "project"
    PLAN = "plan"
    WORK = "work"


GRAPH_OBJECT_KINDS: frozenset[str] = frozenset(
    {
        IdKind.WORKFLOW,
        IdKind.SUBJECT,
        IdKind.EXECUTION,
        IdKind.COMPLETION,
        IdKind.DECISION,
        IdKind.EDGE,
    }
)

SUBJECT_KINDS: frozenset[str] = frozenset(
    {
        SubjectKind.WORKSPACE,
        SubjectKind.PROJECT,
        SubjectKind.PLAN,
        SubjectKind.WORK,
    }
)

DETERMINISTIC_SUBJECT_KINDS: frozenset[str] = frozenset(
    {
        SubjectKind.WORKSPACE,
        SubjectKind.PROJECT,
        SubjectKind.PLAN,
    }
)

MINTED_SUBJECT_KINDS: frozenset[str] = frozenset(
    {
        SubjectKind.WORK,
    }
)


def is_known_graph_kind(kind: object) -> bool:
    return kind in GRAPH_OBJECT_KINDS


def is_known_subject_kind(sub_kind: object) -> bool:
    return sub_kind in SUBJECT_KINDS


def require_graph_kind(kind: str) -> str:
    if not isinstance(kind, str) or kind not in GRAPH_OBJECT_KINDS:
        raise UnknownIdKindError(f"unknown graph object kind: {kind!r}")
    return kind


def require_subject_kind(sub_kind: str) -> str:
    if not isinstance(sub_kind, str) or sub_kind not in SUBJECT_KINDS:
        raise UnknownIdKindError(f"unknown subject kind: {sub_kind!r}")
    return sub_kind