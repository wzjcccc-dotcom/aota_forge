"""M3-B9 deterministic subject binding / mechanical recovery (Issue #9, lane M3-B9).

B9 builds deterministic executor-neutral infrastructure to bind a semantic
public reference to a canonical Subject/ObjectRef:

* semantic reference boundary and rejection (B4 reuse)
* deterministic candidate enumeration from the canonical graph
* candidate validity filtering outside semantic choice
* 0 / 1 / many classification (``NEEDS_SEMANTIC_CHOICE`` never resolved here)
* bounded read-only recovery classification
* a concrete B4 ``SemanticRefResolver`` implementation over the graph
  repository candidate-query primitives

Authority separation: binding identifies the canonical Subject/ObjectRef and
NEVER grants mutation authority (``SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY=no``,
``B9_PERFORMS_AUTHORITY_DECISION=no``, ``OBJECT_REF_IS_AUTHORITY=no``).  B9
performs no semantic choice, uses no heuristics (no recency / current pointer /
title / LLM similarity), and performs no graph write (``BINDING_GRAPH_WRITE_COUNT=0``).
"""

from __future__ import annotations

from aota_forge.core.binding.request import BindingRequest
from aota_forge.core.binding.binder import (
    B9_DEPENDS_ON_B8,
    B9_NEEDS_B7_SOURCE_MUTATION,
    HEURISTIC_SUBJECT_SELECTION_ALLOWED,
    MODEL_INTERNAL_IDS_NORMAL_INPUT,
    SubjectBindingResolver,
)
from aota_forge.core.binding.result import (
    BindingCandidate,
    BindingResult,
    BindingStatus,
    candidates_only,
    plain,
    success_bound,
)

# Frozen required end state for B9.
BINDING_RECOVERY_IMPLEMENTED = True
HEURISTIC_SUBJECT_SELECTION_ALLOWED = False
CURRENT_POINTER_SUBJECT_AUTHORITY = False
SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY = False
B9_PERFORMS_AUTHORITY_DECISION = False
B9_DEPENDS_ON_B8 = False
B9_NEEDS_B7_SOURCE_MUTATION = False
CURRENT_POINTER_SUBJECT_AUTHORITY_NO = True

__all__ = [
    "B9_DEPENDS_ON_B8",
    "B9_NEEDS_B7_SOURCE_MUTATION",
    "BINDING_RECOVERY_IMPLEMENTED",
    "CURRENT_POINTER_SUBJECT_AUTHORITY",
    "HEURISTIC_SUBJECT_SELECTION_ALLOWED",
    "MODEL_INTERNAL_IDS_NORMAL_INPUT",
    "BindingCandidate",
    "BindingRequest",
    "BindingResult",
    "BindingStatus",
    "SubjectBindingResolver",
    "SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY",
    "candidates_only",
    "plain",
    "success_bound",
]