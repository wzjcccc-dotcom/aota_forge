"""Model / internal-ID boundary helpers (M3-B4).

These isolated helpers let a future ingress/adapter integration distinguish:

* a semantic public reference (model-facing, e.g. a plan_id / project_id /
  workspace key)
* a typed trusted internal ``ObjectRef``
* a raw internal ID string

Policy enforced at this boundary:

* ``MODEL_INTERNAL_IDS_NORMAL_INPUT=no`` — normal model-facing input must not
  accept raw internal graph IDs by default
* ``RAW_INTERNAL_ID_ACCEPTED_AS_SEMANTIC_REF=no``
* ``LEGACY_PT_ID_IS_CANONICAL_SUBJECT_ID=no``
* ``LEGACY_OD_ID_IS_CANONICAL_DECISION_ID=no``
* ``LEGACY_POINTER_IS_CANONICAL_ID=no``

No ingress/validation shared hot file is modified here; this module only
reports shape so a later lane can wire rejection without duplicating policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aota_forge.core.identity.errors import SemanticRefRejectedError
from aota_forge.core.identity.ids import is_raw_canonical_id_string
from aota_forge.core.identity.refs import is_raw_ref_canonical_string


class RefCategory(Enum):
    SEMANTIC = "semantic"
    TRUSTED_OBJECT_REF = "trusted_object_ref"
    RAW_INTERNAL_ID = "raw_internal_id"
    LEGACY = "legacy"
    UNKNOWN = "unknown"


LEGACY_ID_PREFIXES: tuple[str, ...] = (
    "pt_",          # legacy Profile Task IDs
    "od_",          # legacy Decision IDs
    "ho_",          # legacy handoff IDs
    "current_",     # current_* pointers
    "corr_",        # correlation IDs
)
RAW_INTERNAL_ID_PREFIX = "forge:"
OBJECT_REF_PREFIX = "ref:"


def classify_ref(value: object) -> RefCategory:
    """Classify a reference-shaped value into one boundary category."""
    if not isinstance(value, str) or not value:
        return RefCategory.UNKNOWN
    if is_raw_ref_canonical_string(value):
        return RefCategory.TRUSTED_OBJECT_REF
    if value.startswith(RAW_INTERNAL_ID_PREFIX):
        return RefCategory.RAW_INTERNAL_ID
    lowered = value.casefold()
    if any(lowered.startswith(p) for p in LEGACY_ID_PREFIXES):
        return RefCategory.LEGACY
    return RefCategory.SEMANTIC


def reject_non_semantic_ref(value: object) -> str:
    """Gate a normal model-facing semantic reference.

    Raises ``SemanticRefRejectedError`` when the value is a raw internal ID, a
    legacy ID, or a typed ObjectRef string that should not arrive through the
    normal semantic input path.  Returns the value unchanged when it is a
    legitimate semantic public reference.
    """
    category = classify_ref(value)
    if category in (RefCategory.RAW_INTERNAL_ID, RefCategory.LEGACY):
        raise SemanticRefRejectedError(
            "raw/legacy id rejected as a semantic reference",
            details={"value": value, "category": category.value},
        )
    return str(value)


def is_legacy_id(value: object) -> bool:
    cat = classify_ref(value)
    return cat in (RefCategory.LEGACY, RefCategory.RAW_INTERNAL_ID)


def is_raw_internal_id(value: object) -> bool:
    return classify_ref(value) in (RefCategory.RAW_INTERNAL_ID, RefCategory.TRUSTED_OBJECT_REF)


@dataclass(frozen=True)
class RefClassification:
    value: str
    category: RefCategory
    is_trusted_object_ref: bool
    is_raw_internal_id: bool
    is_legacy: bool


def classify(value: object) -> RefClassification:
    v = str(value) if value is not None else ""
    cat = classify_ref(v)
    return RefClassification(
        value=v,
        category=cat,
        is_trusted_object_ref=cat is RefCategory.TRUSTED_OBJECT_REF,
        is_raw_internal_id=cat in (RefCategory.RAW_INTERNAL_ID, RefCategory.TRUSTED_OBJECT_REF),
        is_legacy=cat is RefCategory.LEGACY,
    )