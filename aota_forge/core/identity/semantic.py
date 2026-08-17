"""Semantic reference -> ObjectRef structural interface (M3-B4).

B4 may define a trusted *structural* interface for converting a semantic
public reference into a typed ``ObjectRef``.  It must NOT implement:

* heuristic Subject selection              (``HEURISTIC_SUBJECT_IDENTITY_SELECTION_ALLOWED=no``)
* 0/1/many binding / recovery policy      (owned by M3-B9)
* graph lookup authority                  (owned by M3-B3)

The concrete resolver provided here recognizes only well-formed semantic
public references and rejects raw internal IDs and legacy IDs.  Binding to a
canonical record, looking a Subject up in a graph, and any authority decision
are intentionally out of scope for B4.
"""

from __future__ import annotations

from typing import Protocol

from aota_forge.core.identity.boundary import reject_non_semantic_ref
from aota_forge.core.identity.errors import SemanticRefRejectedError
from aota_forge.core.identity.refs import ObjectRef


class SemanticRefResolver(Protocol):
    """Trusted structural semantic ref -> ObjectRef resolver.

    Implementations MUST map a semantic public reference to a canonical
    ObjectRef in a deterministic way and MUST NOT perform binding/recovery or
    authority decisions.  A real resolver is owned by a later binding lane
    (M3-B9) layered on B3 graph lookups.
    """

    def semantic_ref_to_object_ref(self, semantic_ref: str) -> ObjectRef:
        """Return the trusted ObjectRef for one semantic public reference."""
        ...


class StructuralSemanticResolver:
    """Executor-neutral structural resolver usable before B3/B9 integration.

    ``lookup_registry`` maps a semantic key to a pre-resolved ObjectRef.  In
    production this registry is filled by a binding lane (M3-B9) / B3 graph
    lookup; B4 itself only validates shape and rejects non-semantic input.  It
    never makes a heuristic choice, never performs an authority decision and
    never queries a graph.
    """

    HEURISTIC_SUBJECT_IDENTITY_SELECTION_ALLOWED = False
    SEMANTIC_REF_RESOLVER_PERFORMS_AUTHORITY_DECISION = False

    def __init__(self, registry: dict[str, ObjectRef]) -> None:
        self._registry = dict(registry)

    def semantic_ref_to_object_ref(self, semantic_ref: str) -> ObjectRef:
        key = reject_non_semantic_ref(semantic_ref)
        ref = self._registry.get(key)
        if ref is None:
            raise SemanticRefRejectedError(
                "semantic reference is not a known public ref",
                details={"semantic_ref": semantic_ref},
            )
        return ref


class DelegatingSemanticResolver:
    """Resolver that forwards to a later authoritative resolver.

    Useful as the integration hook for M3-B3 (graph lookup) / M3-B9 (binding):
    B4 ships the structural gate and a supplied delegate does the
    binding-aware resolution without B4 implementing binding policy itself.
    """

    SEMANTIC_REF_RESOLVER_PERFORMS_AUTHORITY_DECISION = False

    def __init__(self, delegate: SemanticRefResolver) -> None:
        self._delegate = delegate

    def semantic_ref_to_object_ref(self, semantic_ref: str) -> ObjectRef:
        reject_non_semantic_ref(semantic_ref)
        return self._delegate.semantic_ref_to_object_ref(semantic_ref)


class GraphBindingSemanticResolver:
    """Concrete binding-backed resolver (M3-B9, ``B4 SemanticRefResolver``).

    Layers the B9 ``SubjectBindingResolver`` (canonical-graph candidate
    enumeration + deterministic 0/1/many classification) behind the B4
    ``SemanticRefResolver`` Protocol.  Structural gates from this module
    (``classify_ref`` / ``reject_non_semantic_ref``) still reject raw internal
    IDs and legacy pointers before any binding happens
    (``MODEL_INTERNAL_IDS_NORMAL_INPUT=no``).

    ``semantic_ref_to_object_ref`` returns the canonical ObjectRef ONLY for a
    deterministic single-candidate BOUND.  Any other bounded outcome raises
    ``SemanticRefRejectedError`` with deterministic details; no heuristic
    selection and no authority decision is ever performed here
    (``HEURISTIC_SUBJECT_SELECTION_ALLOWED=no``,
    ``SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY=no``,
    ``B9_PERFORMS_AUTHORITY_DECISION=no``).
    """

    SEMANTIC_REF_RESOLVER_PERFORMS_AUTHORITY_DECISION = False
    B9_PERFORMS_AUTHORITY_DECISION = False
    SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY = False
    HEURISTIC_SUBJECT_SELECTION_ALLOWED = False

    def __init__(self, binder: object, subject_sub_kind: str | None = None) -> None:
        from aota_forge.core.binding.request import BindingRequest

        self._binder = binder
        self._subject_sub_kind = subject_sub_kind
        self._BindingRequest = BindingRequest

    def semantic_ref_to_object_ref(self, semantic_ref: str) -> ObjectRef:
        reject_non_semantic_ref(semantic_ref)
        request = self._BindingRequest(
            semantic_ref=semantic_ref,
            subject_sub_kind=self._subject_sub_kind,
        )
        result = self._binder.bind(request)
        if result.status.value == "BOUND" and result.subject_ref is not None:
            return result.subject_ref
        raise SemanticRefRejectedError(
            f"semantic reference did not resolve to exactly one subject: {result.status.value}",
            details={"status": result.status.value},
        )
