"""M3-B4 internal ID and Subject identity mechanics.

Executor-neutral identity primitives, ID Broker mechanics, typed ObjectRef,
canonical parse/validation/serialization, and model/internal boundary helpers
for Issue #9 lane M3-B4.

Boundaries honored here:

* graph aggregate semantics            — NOT implemented (M3-B3)
* authority engine                     — NOT implemented (M3-A2/B5)
* capability lease                     — NOT implemented
* CAS / idempotency / durable minting  — NOT implemented (deferred to M3-B6)
* binding / recovery                   — NOT implemented (M3-B9)
* authoritative graph writes           — NOT enabled
* migration / cutover                  — NOT performed

``ID_IS_AUTHORITY=no``: identities identify objects and validate shape; they
never grant authority.
"""

from aota_forge.core.identity.boundary import (
    RefCategory,
    RefClassification,
    classify,
    classify_ref,
    is_legacy_id,
    is_raw_internal_id,
    reject_non_semantic_ref,
)
from aota_forge.core.identity.broker import (
    IdBroker,
    IdState,
    ReservationRecord,
    allocate_minted_subject,
)
from aota_forge.core.identity.digest import (
    DIGEST_ALGORITHM,
    canonical_json,
    derive_id,
    plan_subject_value,
    project_subject_value,
    sha256_hex,
    workspace_subject_value,
)
from aota_forge.core.identity.errors import (
    BareIdError,
    CollisionError,
    IdReuseError,
    IdentityError,
    MalformedIdError,
    ObjectRefError,
    SemanticRefRejectedError,
    UnknownIdKindError,
    WrongKindIdError,
)
from aota_forge.core.identity.ids import (
    InternalId,
    canonical_id_str,
    is_raw_canonical_id_string,
    make_id,
    parse_internal_id,
    subject_id_from_value,
)
from aota_forge.core.identity.kinds import (
    DETERMINISTIC_SUBJECT_KINDS,
    GRAPH_OBJECT_KINDS,
    IdKind,
    MINTED_SUBJECT_KINDS,
    SUBJECT_KINDS,
    SubjectKind,
    is_known_graph_kind,
    is_known_subject_kind,
    require_graph_kind,
    require_subject_kind,
)
from aota_forge.core.identity.refs import (
    ObjectRef,
    canonical_id_of,
    is_raw_ref_canonical_string,
    make_object_ref,
    object_ref_subject,
    object_ref_value_to_canonical,
    object_ref_workflow,
    parse_object_ref,
)
from aota_forge.core.identity.semantic import (
    DelegatingSemanticResolver,
    SemanticRefResolver,
    StructuralSemanticResolver,
)
from aota_forge.core.identity.subject import (
    mint_subject_value,
    mint_work_subject_value,
    plan_subject,
    project_subject,
    workspace_subject,
)

__all__ = [
    "BareIdError",
    "CollisionError",
    "DETERMINISTIC_SUBJECT_KINDS",
    "DIGEST_ALGORITHM",
    "DelegatingSemanticResolver",
    "GRAPH_OBJECT_KINDS",
    "IdBroker",
    "IdKind",
    "IdReuseError",
    "IdState",
    "IdentityError",
    "InternalId",
    "MINTED_SUBJECT_KINDS",
    "MalformedIdError",
    "ObjectRef",
    "ObjectRefError",
    "RefCategory",
    "RefClassification",
    "ReservationRecord",
    "SemanticRefRejectedError",
    "SemanticRefResolver",
    "StructuralSemanticResolver",
    "SUBJECT_KINDS",
    "SubjectKind",
    "UnknownIdKindError",
    "WrongKindIdError",
    "allocate_minted_subject",
    "canonical_id_of",
    "canonical_id_str",
    "canonical_json",
    "classify",
    "classify_ref",
    "derive_id",
    "is_known_graph_kind",
    "is_known_subject_kind",
    "is_legacy_id",
    "is_raw_canonical_id_string",
    "is_raw_internal_id",
    "is_raw_ref_canonical_string",
    "make_id",
    "make_object_ref",
    "mint_subject_value",
    "mint_work_subject_value",
    "object_ref_subject",
    "object_ref_value_to_canonical",
    "object_ref_workflow",
    "parse_internal_id",
    "parse_object_ref",
    "plan_subject",
    "plan_subject_value",
    "project_subject",
    "project_subject_value",
    "reject_non_semantic_ref",
    "require_graph_kind",
    "require_subject_kind",
    "sha256_hex",
    "subject_id_from_value",
    "workspace_subject",
    "workspace_subject_value",
]