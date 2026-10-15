"""Bounded semantic navigation and amendments for authoritative local Plans."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL, PortablePlanDocument
from aota_forge.core.plan.sections import BodySection, parse_body_sections
from aota_forge.core.plan.validation import (
    MILESTONE_ID_RE,
    WORK_ITEM_ID_RE,
    is_milestone_id,
    is_work_item_id,
)


MAX_PLAN_SEMANTIC_SECTIONS = 2048
MAX_PLAN_SECTION_READ_BYTES = 64 * 1024
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_KV_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$")
_WORK_HEADING_RE = re.compile(r"^(M[0-9]+)\s*/\s*([A-Za-z0-9][A-Za-z0-9._-]*)\b")
_MILESTONE_HEADING_RE = re.compile(r"^(?:milestone\s+)?(M[0-9]+)(?:\b|$)", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


ADD_WORK_ITEM_AMENDMENT_CONTRACT: dict[str, Any] = {
    "fields": {
        "kind": {"type": "string", "const": "add_work_item"},
        "milestone_id": {"type": "milestone_id", "pattern": MILESTONE_ID_RE.pattern},
        "work_item": {
            "type": "object",
            "fields": {
                "id": {"type": "work_item_id", "pattern": WORK_ITEM_ID_RE.pattern},
                "title": {
                    "type": "string",
                    "max_utf8_bytes": 256,
                    "non_empty": True,
                    "single_line": True,
                    "rejects_plan_headings": True,
                    "rejects_nul": True,
                },
                "objective": {
                    "type": "string",
                    "max_utf8_bytes": 4096,
                    "non_empty": True,
                    "rejects_plan_headings": True,
                    "rejects_nul": True,
                },
                "scope": {
                    "type": "array[string]",
                    "also_accepts": "newline_delimited_string",
                    "blank_lines_ignored": True,
                    "min_items": 1,
                    "max_total_utf8_bytes": 8192,
                    "items": {
                        "type": "string",
                        "max_utf8_bytes": 2048,
                        "non_empty": True,
                        "single_line": True,
                        "rejects_plan_headings": True,
                        "rejects_nul": True,
                    },
                },
                "dependencies": {
                    "type": "array[work_item_id]",
                    "min_items": 0,
                    "unique_items": True,
                    "must_reference_existing_work_items": True,
                    "same_milestone_only": True,
                    "items": {
                        "type": "work_item_id",
                        "pattern": WORK_ITEM_ID_RE.pattern,
                        "also_accepts_ref": "{milestone_id}/{work_item_id}",
                    },
                },
                "acceptance": {
                    "type": "array[string]",
                    "also_accepts": "newline_delimited_string",
                    "blank_lines_ignored": True,
                    "min_items": 1,
                    "max_total_utf8_bytes": 8192,
                    "items": {
                        "type": "string",
                        "max_utf8_bytes": 2048,
                        "non_empty": True,
                        "single_line": True,
                        "rejects_plan_headings": True,
                        "rejects_nul": True,
                    },
                },
            },
        },
    }
}
M2_RV2_WAIVER_DECISION_REF = "user-decision:AF005-M2-RV2-WAIVER-20261015"
M2_RV2_WAIVER_REASON = (
    "User cancels the independent M2 RV2 acceptance gate: W1-W6 are complete, "
    "Evidence Verifier has passed, Doctor reported evidence readiness, and Thin Host "
    "lacks independent RV2 acceptance integration; do not develop it for this closure."
)
_M2_RV2_OLD_LIFECYCLE = "W6 → integration → RV2 → Doctor → M2 closure"
_M2_RV2_NEW_LIFECYCLE = "W6 → integration → Doctor → M2 closure"
_M2_RV2_OLD_GATE_CLAUSE = "RV2 is a separate Milestone-level gate, not a Work Item."
_M2_RV2_NEW_GATE_CLAUSE = (
    "M2_RV2_REQUIRED=no; M2_RV2_WAIVED_BY_USER=yes by "
    f"{M2_RV2_WAIVER_DECISION_REF}."
)
_M2_RV2_OLD_AC12 = "M2_RV2_REQUIRED=yes and RV2 remains a separate Milestone-level gate."
_M2_RV2_NEW_AC12 = (
    "M2_RV2_REQUIRED=no; M2_RV2_WAIVED_BY_USER=yes by "
    f"{M2_RV2_WAIVER_DECISION_REF}; {M2_RV2_WAIVER_REASON}"
)

WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT: dict[str, Any] = {
    "fields": {
        "kind": {"type": "string", "const": "waive_m2_rv2_gate"},
        "milestone_id": {"type": "milestone_id", "const": "M2", "pattern": MILESTONE_ID_RE.pattern},
        "decision_ref": {"type": "string", "const": M2_RV2_WAIVER_DECISION_REF},
    }
}
PLAN_AMENDMENT_CONTRACTS = (
    ADD_WORK_ITEM_AMENDMENT_CONTRACT,
    WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT,
)


def plan_amendment_contracts_for_help() -> dict[str, dict[str, Any]]:
    """Project the canonical amendment field contracts into model-visible help."""
    projected: dict[str, dict[str, Any]] = {}
    for contract in PLAN_AMENDMENT_CONTRACTS:
        fields = contract["fields"]
        projected_fields = {name: deepcopy(field) for name, field in fields.items()}
        for name, field in fields.items():
            nested_fields = field.get("fields")
            if isinstance(nested_fields, Mapping):
                projected_fields[name]["required"] = list(nested_fields)
        projected[fields["kind"]["const"]] = {
            "required": list(fields),
            "fields": projected_fields,
        }
    return projected


class PlanSemanticError(ValueError):
    """Typed fail-closed Plan semantic-index/amendment error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_plan_amendment_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanSemanticSection:
    section_ref: str
    section_kind: str
    semantic_identity: str
    summary: str
    section_digest: str
    status: str | None
    line_hint: int | None
    content: str

    def index_dict(self) -> dict[str, Any]:
        return {
            "section_ref": self.section_ref,
            "section_kind": self.section_kind,
            "semantic_identity": self.semantic_identity,
            "summary": self.summary,
            "status": self.status,
            "section_digest": self.section_digest,
            "line_hint": self.line_hint,
        }


@dataclass(frozen=True)
class PlanSemanticIndex:
    """Deterministic semantic navigation derived from one current Plan read."""

    project_id: str
    plan_id: str
    plan_revision: int
    authority_revision: str | int | None
    source_digest: str
    sections: tuple[PlanSemanticSection, ...]

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        plan_id: str,
        plan_revision: int,
        authority_revision: str | int | None,
        source_digest: str,
        body: str,
        document: PortablePlanDocument,
    ) -> "PlanSemanticIndex":
        headings = _heading_blocks(body)
        sections: list[PlanSemanticSection] = []

        milestone_ids = (
            set(document.milestone_specs)
            | set(document.milestone_status)
            | set(document.milestone_work_items)
        )
        if document.current_milestone:
            milestone_ids.add(document.current_milestone)
        for milestone_id in sorted(milestone_ids, key=_natural_ref_key):
            if not is_milestone_id(milestone_id):
                continue
            blocks = [
                item
                for item in headings
                if (match := _milestone_heading_match(item.title)) is not None
                and f"M{match.group(1)[1:]}".upper() == milestone_id
            ]
            content = "\n\n".join(item.content for item in blocks)
            if not content:
                content = _field_section_content(body, milestone_id)
            spec = document.milestone_specs.get(milestone_id, {})
            status = document.milestone_status.get(milestone_id)
            if status is None and isinstance(spec, Mapping):
                raw_status = spec.get("status")
                status = raw_status if isinstance(raw_status, str) else None
            summary = (
                document.milestone_section_prose.get(milestone_id)
                or (str(spec.get("title") or "") if isinstance(spec, Mapping) else "")
                or _summary(content)
            )
            sections.append(
                _semantic_section(
                    section_ref=milestone_id,
                    section_kind="milestone",
                    semantic_identity=milestone_id,
                    summary=summary,
                    status=status,
                    content=content or milestone_id,
                    line_hint=min((item.start_line + 1 for item in blocks), default=None),
                )
            )

        for milestone_id in sorted(document.milestone_work_items, key=_natural_ref_key):
            for work_item_id in sorted(document.milestone_work_items[milestone_id], key=_natural_ref_key):
                section_ref = f"{milestone_id}/{work_item_id}"
                blocks = [
                    item
                    for item in headings
                    if (match := _WORK_HEADING_RE.match(item.title.strip())) is not None
                    and match.group(1).upper() == milestone_id
                    and match.group(2) == work_item_id
                ]
                content = "\n\n".join(item.content for item in blocks)
                work_slice = next(
                    (
                        item
                        for item in document.work_source_slices.get(milestone_id, ())
                        if getattr(item, "work_item_id", None) == work_item_id
                    ),
                    None,
                )
                title = str(getattr(work_slice, "title", "") or "")
                if not content and work_slice is not None:
                    content = str(getattr(work_slice, "source_text", "") or "")
                if not content:
                    content = section_ref
                status = document.current_fields.get(f"{milestone_id}_{work_item_id}_STATUS")
                sections.append(
                    _semantic_section(
                        section_ref=section_ref,
                        section_kind="work_item",
                        semantic_identity=section_ref,
                        summary=title or _summary(content),
                        status=status,
                        content=content,
                        line_hint=min((item.start_line + 1 for item in blocks), default=None),
                    )
                )

        category_groups: dict[str, list[_HeadingBlock]] = {}
        for item in headings:
            if _WORK_HEADING_RE.match(item.title.strip()) or _MILESTONE_HEADING_RE.match(item.title.strip()):
                continue
            categorized = _category_section(item.title)
            if categorized is None:
                continue
            ref, kind, identity = categorized
            category_groups.setdefault(ref, []).append(item)
            # Duplicate section refs intentionally aggregate under their stable semantic category.
            _ = kind, identity
        for section_ref in sorted(category_groups, key=_natural_ref_key):
            blocks = category_groups[section_ref]
            _ref, kind, identity = _category_section(blocks[0].title) or (
                section_ref,
                "plan_section",
                section_ref,
            )
            content = "\n\n".join(item.content for item in blocks)
            sections.append(
                _semantic_section(
                    section_ref=section_ref,
                    section_kind=kind,
                    semantic_identity=identity,
                    summary=_summary(content),
                    status=None,
                    content=content,
                    line_hint=min(item.start_line + 1 for item in blocks),
                )
            )

        if len(sections) > MAX_PLAN_SEMANTIC_SECTIONS:
            raise PlanSemanticError(
                "PLAN_INDEX_TOO_LARGE",
                f"semantic index exceeds {MAX_PLAN_SEMANTIC_SECTIONS} sections",
            )
        seen_refs: set[str] = set()
        for item in sections:
            if item.section_ref in seen_refs:
                raise PlanSemanticError("SECTION_REF_AMBIGUOUS", f"duplicate semantic ref {item.section_ref!r}")
            seen_refs.add(item.section_ref)
        sections.sort(key=lambda item: _section_sort_key(item.section_kind, item.section_ref))
        return cls(
            project_id=project_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            authority_revision=authority_revision,
            source_digest=source_digest,
            sections=tuple(sections),
        )

    def resolve(self, section_ref: str) -> PlanSemanticSection:
        for item in self.sections:
            if item.section_ref == section_ref:
                return item
        raise PlanSemanticError("PLAN_SECTION_NOT_FOUND", f"semantic section {section_ref!r} was not found")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "authority_revision": self.authority_revision,
            "source_digest": self.source_digest,
            "sections": [item.index_dict() for item in self.sections],
            "line_number_is_authority": False,
        }


@dataclass(frozen=True)
class _HeadingBlock:
    title: str
    level: int
    start_line: int
    end_line: int
    content: str


def _heading_blocks(body: str) -> tuple[_HeadingBlock, ...]:
    lines = body.splitlines()
    raw: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match is not None:
            raw.append((index, len(match.group(1)), match.group(2).strip()))
    blocks: list[_HeadingBlock] = []
    for index, (start, level, title) in enumerate(raw):
        end = len(lines)
        for next_start, next_level, _next_title in raw[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        blocks.append(
            _HeadingBlock(
                title=title,
                level=level,
                start_line=start,
                end_line=end,
                content="\n".join(lines[start:end]).strip(),
            )
        )
    return tuple(blocks)


def _milestone_heading_match(title: str) -> re.Match[str] | None:
    """Match milestone headings without classifying a Work heading as its parent."""
    normalized = title.strip()
    if _WORK_HEADING_RE.match(normalized) is not None:
        return None
    return _MILESTONE_HEADING_RE.match(normalized)


def _natural_ref_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"([0-9]+)", value))


def _section_sort_key(section_kind: str, section_ref: str) -> tuple[Any, ...]:
    rank = {"milestone": 0, "work_item": 1, "decision": 2, "risk": 3, "runtime": 4, "configuration": 5}
    return rank.get(section_kind, 9), _natural_ref_key(section_ref)


def _summary(content: str, *, limit: int = 240) -> str:
    for line in content.splitlines()[1:]:
        text = line.strip().lstrip("-*> ").strip()
        if not text or text.startswith("```") or _KV_RE.match(text):
            continue
        return text[:limit]
    first = content.splitlines()[0].lstrip("# ").strip() if content.splitlines() else ""
    return first[:limit]


def _semantic_section(
    *,
    section_ref: str,
    section_kind: str,
    semantic_identity: str,
    summary: str,
    status: str | None,
    content: str,
    line_hint: int | None,
) -> PlanSemanticSection:
    normalized = content.strip()
    if not normalized:
        normalized = section_ref
    if not summary.strip():
        summary = _summary(normalized) or section_ref
    return PlanSemanticSection(
        section_ref=section_ref,
        section_kind=section_kind,
        semantic_identity=semantic_identity,
        summary=summary.strip()[:240],
        section_digest=_digest(normalized),
        status=status,
        line_hint=line_hint,
        content=normalized,
    )


def _category_section(title: str) -> tuple[str, str, str] | None:
    match = re.match(r"^(Decision|Risk|Runtime|Configuration|Config)s?\b\s*(.*)$", title.strip(), re.IGNORECASE)
    if match is None:
        return None
    category = match.group(1).casefold()
    suffix = match.group(2).strip(" :-—–")
    if category == "decision":
        root, kind = "Decisions", "decision"
    elif category == "risk":
        root, kind = "Risks", "risk"
    elif category == "runtime":
        root, kind = "Runtime", "runtime"
    else:
        root, kind = "Configuration", "configuration"
    identity_match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\b|$)", suffix)
    identity = identity_match.group(1) if identity_match is not None else ""
    section_ref = f"{root}/{identity}" if identity else root
    return section_ref, kind, section_ref


def _field_section_content(body: str, milestone_id: str) -> str:
    selected: list[str] = []
    for section in parse_body_sections(body):
        for line in section.raw_text.splitlines():
            match = _KV_RE.match(line)
            if match and match.group(1).startswith(f"{milestone_id}_"):
                selected.append(f"{match.group(1)}={match.group(2)}")
    return "\n".join(selected)


def normalize_add_work_item(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PlanSemanticError("AMENDMENT_INVALID", "amendment must be an object")
    amendment_fields = ADD_WORK_ITEM_AMENDMENT_CONTRACT["fields"]
    if set(payload) != set(amendment_fields):
        raise PlanSemanticError("AMENDMENT_INVALID", "add_work_item amendment has unknown or missing fields")
    if payload.get("kind") != amendment_fields["kind"]["const"]:
        raise PlanSemanticError("AMENDMENT_KIND_UNSUPPORTED", "only add_work_item is currently supported")
    milestone_id = payload.get("milestone_id")
    if not _matches_semantic_id(milestone_id, amendment_fields["milestone_id"]):
        raise PlanSemanticError("MILESTONE_ID_INVALID", "milestone_id must match M<digits>")
    raw_item = payload.get("work_item")
    if not isinstance(raw_item, Mapping):
        raise PlanSemanticError("WORK_ITEM_INVALID", "work_item must be an object")
    work_item_fields = amendment_fields["work_item"]["fields"]
    if set(raw_item) != set(work_item_fields):
        raise PlanSemanticError("WORK_ITEM_INVALID", "work_item has unknown or missing fields")
    work_id = raw_item.get("id")
    if not _matches_semantic_id(work_id, work_item_fields["id"]):
        raise PlanSemanticError("WORK_ITEM_ID_INVALID", "work_item.id is not a legal Work ID")
    title = _plain_text(raw_item.get("title"), "work_item.title", contract=work_item_fields["title"])
    objective = _plain_text(raw_item.get("objective"), "work_item.objective", contract=work_item_fields["objective"])
    scope = _text_lines(raw_item.get("scope"), "work_item.scope", contract=work_item_fields["scope"])
    acceptance = _text_lines(
        raw_item.get("acceptance"), "work_item.acceptance", contract=work_item_fields["acceptance"]
    )
    dependency_contract = work_item_fields["dependencies"]
    dependencies_raw = raw_item.get("dependencies")
    if dependency_contract["type"] != "array[work_item_id]" or not isinstance(dependencies_raw, (list, tuple)):
        raise PlanSemanticError("WORK_ITEM_DEPENDENCIES_INVALID", "dependencies must be a list")
    if len(dependencies_raw) < dependency_contract["min_items"]:
        raise PlanSemanticError("WORK_ITEM_DEPENDENCIES_INVALID", "dependencies must not be empty")
    dependencies: list[str] = []
    for dependency in dependencies_raw:
        if not isinstance(dependency, str):
            raise PlanSemanticError("WORK_ITEM_DEPENDENCIES_INVALID", "each dependency must be a Work ID or semantic ref")
        ref = dependency.strip()
        ref_template = dependency_contract["items"]["also_accepts_ref"]
        prefix = ref_template.split("{work_item_id}", 1)[0].format(milestone_id=milestone_id)
        if dependency_contract["same_milestone_only"] and ref.startswith(prefix):
            ref = ref[len(prefix) :]
        if not _matches_semantic_id(ref, dependency_contract["items"]):
            raise PlanSemanticError("WORK_ITEM_DEPENDENCIES_INVALID", f"invalid dependency ref {dependency!r}")
        if dependency_contract["unique_items"] and ref in dependencies:
            raise PlanSemanticError("WORK_ITEM_DEPENDENCIES_INVALID", f"duplicate dependency ref {dependency!r}")
        dependencies.append(ref)
    return {
        "kind": amendment_fields["kind"]["const"],
        "milestone_id": milestone_id,
        "work_item": {
            "id": work_id,
            "title": title,
            "objective": objective,
            "scope": scope,
            "dependencies": dependencies,
            "acceptance": acceptance,
        },
    }


def normalize_waive_m2_rv2_gate(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PlanSemanticError("AMENDMENT_INVALID", "amendment must be an object")
    fields = WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT["fields"]
    if set(payload) != set(fields):
        raise PlanSemanticError("AMENDMENT_INVALID", "waive_m2_rv2_gate amendment has unknown or missing fields")
    if payload.get("kind") != fields["kind"]["const"]:
        raise PlanSemanticError("AMENDMENT_KIND_UNSUPPORTED", "unsupported Plan amendment kind")
    if payload.get("milestone_id") != fields["milestone_id"]["const"]:
        raise PlanSemanticError("AMENDMENT_SCOPE_INVALID", "RV2 waiver is limited to milestone M2")
    if payload.get("decision_ref") != fields["decision_ref"]["const"]:
        raise PlanSemanticError("AMENDMENT_DECISION_INVALID", "RV2 waiver requires the exact user decision reference")
    return {
        "kind": fields["kind"]["const"],
        "milestone_id": fields["milestone_id"]["const"],
        "decision_ref": fields["decision_ref"]["const"],
    }


def normalize_plan_amendment(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PlanSemanticError("AMENDMENT_INVALID", "amendment must be an object")
    kind = payload.get("kind")
    if kind == "add_work_item":
        return normalize_add_work_item(payload)
    if kind == "waive_m2_rv2_gate":
        return normalize_waive_m2_rv2_gate(payload)
    raise PlanSemanticError("AMENDMENT_KIND_UNSUPPORTED", "unsupported Plan amendment kind")


def _matches_semantic_id(value: object, contract: Mapping[str, Any]) -> bool:
    if contract.get("type") == "milestone_id":
        return is_milestone_id(value)
    if contract.get("type") == "work_item_id":
        return is_work_item_id(value)
    return False


def _plain_text(value: object, label: str, *, contract: Mapping[str, Any]) -> str:
    if (
        not isinstance(value, str)
        or (contract["non_empty"] and not value.strip())
        or len(value.encode("utf-8")) > contract["max_utf8_bytes"]
    ):
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} must be bounded non-empty text")
    text = value.strip()
    if (contract["rejects_nul"] and "\x00" in text) or (
        contract["rejects_plan_headings"]
        and any(re.match(r"^\s*#{1,6}\s", line) for line in text.splitlines())
    ):
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} must not inject Plan headings or NUL")
    if contract.get("single_line") and ("\n" in text or "\r" in text):
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} must be one line")
    return text


def _text_lines(value: object, label: str, *, contract: Mapping[str, Any]) -> list[str]:
    if isinstance(value, str) and contract.get("also_accepts") == "newline_delimited_string":
        lines = value.splitlines()
        raw_lines = (
            [line.strip() for line in lines if line.strip()]
            if contract["blank_lines_ignored"]
            else [line.strip() for line in lines]
        )
    elif isinstance(value, (list, tuple)) and contract["type"] == "array[string]":
        raw_lines = [_plain_text(item, label, contract=contract["items"]) for item in value]
    else:
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} must be text or a list of text")
    if len(raw_lines) < contract["min_items"]:
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} must not be empty")
    if sum(len(item.encode("utf-8")) for item in raw_lines) > contract["max_total_utf8_bytes"]:
        raise PlanSemanticError("AMENDMENT_FIELD_INVALID", f"{label} exceeds its bounded size")
    return [_plain_text(item, label, contract=contract["items"]) for item in raw_lines]


def add_work_item_to_plan(body: str, document: PortablePlanDocument, amendment: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    normalized = normalize_add_work_item(amendment)
    milestone_id = normalized["milestone_id"]
    item = normalized["work_item"]
    work_id = item["id"]
    dependency_contract = ADD_WORK_ITEM_AMENDMENT_CONTRACT["fields"]["work_item"]["fields"]["dependencies"]
    known_milestones = set(document.milestone_specs) | set(document.milestone_status) | set(document.milestone_work_items)
    if milestone_id not in known_milestones:
        raise PlanSemanticError("MILESTONE_NOT_FOUND", f"milestone {milestone_id!r} does not exist")
    existing_ids = set(document.milestone_work_items.get(milestone_id, ()))
    if work_id in existing_ids:
        raise PlanSemanticError("WORK_ITEM_ALREADY_EXISTS", f"{milestone_id}/{work_id} already exists")
    if any(
        (match := _WORK_HEADING_RE.match(section.title.strip())) is not None
        and match.group(1).upper() == milestone_id
        and match.group(2) == work_id
        for section in parse_body_sections(body)
    ):
        raise PlanSemanticError("WORK_ITEM_ALREADY_EXISTS", f"{milestone_id}/{work_id} already has a semantic section")

    dependencies = set(item["dependencies"])
    if dependency_contract["must_reference_existing_work_items"]:
        unknown = dependencies - existing_ids
        if unknown:
            raise PlanSemanticError("DAG_UNKNOWN_WORK_ITEM", f"dependencies reference unknown Work IDs: {sorted(unknown)}")
    old_edges = set(document.milestone_dependencies.get(milestone_id, ()))
    if any(source not in existing_ids or target not in existing_ids for source, target in old_edges):
        raise PlanSemanticError("DAG_UNKNOWN_WORK_ITEM", "current milestone dependencies are structurally invalid")
    if _has_cycle(existing_ids, old_edges):
        raise PlanSemanticError("DAG_CYCLE", "current milestone dependency graph contains a cycle")
    new_edges = old_edges | {(dependency, work_id) for dependency in dependencies}
    all_work_items = existing_ids | {work_id}
    edge_nodes = {node for edge in new_edges for node in edge}
    dag_parts = [f"{source} -> {target}" for source, target in sorted(new_edges)]
    dag_parts.extend(sorted(all_work_items - edge_nodes, key=_natural_ref_key))
    dag_value = ", ".join(dag_parts)
    work_items_value = ", ".join(sorted(all_work_items, key=_natural_ref_key))

    next_revision = _next_text_plan_revision(body)
    revised = _replace_plan_revision(body, next_revision)
    revised = _replace_milestone_values(
        revised,
        {
            f"{milestone_id}_DAG": dag_value,
            f"{milestone_id}_WORK_ITEMS": work_items_value,
        },
    )
    candidate = _insert_work_section(revised, milestone_id, item)
    try:
        result = normalize_portable_plan(candidate, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    except Exception as exc:
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", f"structured Plan materialization failed admission: {exc}") from exc
    if set(result.milestone_work_items.get(milestone_id, ())) != all_work_items:
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", "rendered milestone Work identities did not verify")
    if set(result.milestone_dependencies.get(milestone_id, ())) != new_edges:
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", "rendered dependency graph did not verify")
    revision_readback = any(
        section.key_values.get("PLAN_REVISION") == next_revision
        for section in parse_body_sections(candidate)
        if section.title == "Plan Identity" or section.kind == "current"
    )
    if not revision_readback:
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", "rendered Plan revision did not verify")
    return candidate, normalized


def waive_m2_rv2_gate_in_plan(
    body: str,
    document: PortablePlanDocument,
    amendment: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    normalized = normalize_waive_m2_rv2_gate(amendment)
    expected_work = ("W1", "W2", "W3", "W4", "W5", "W6")
    if (
        document.current_milestone != "M2"
        or _next_text_plan_revision(body) != "R10"
        or tuple(document.milestone_work_items.get("M2", ())) != expected_work
        or "M2_RV2_REQUIRED" in document.current_fields
        or "M2_RV2_WAIVED_BY_USER" in document.current_fields
        or "M2_RV2_WAIVER_DECISION" in document.current_fields
        or "M2_RV2_WAIVER_REASON" in document.current_fields
    ):
        raise PlanSemanticError(
            "AMENDMENT_PRECONDITION_INVALID",
            "RV2 waiver applies only to the unamended #005/M2 R9 W1-W6 requirement",
        )
    if body.count(_M2_RV2_OLD_LIFECYCLE) != 1:
        raise PlanSemanticError("AMENDMENT_PRECONDITION_INVALID", "R9 RV2 closure lifecycle text is not uniquely present")
    if body.count(_M2_RV2_OLD_GATE_CLAUSE) != 1:
        raise PlanSemanticError("AMENDMENT_PRECONDITION_INVALID", "R9 RV2 gate clause is not uniquely present")
    if body.count(_M2_RV2_OLD_AC12) != 1:
        raise PlanSemanticError("AMENDMENT_PRECONDITION_INVALID", "R9 M2 AC12 RV2 requirement is not uniquely present")
    current_field_matches = 0
    field_lines, spans = _section_spans(body)
    for start, end, section in spans:
        if section.kind != "current":
            continue
        for index in range(start, end):
            match = _KV_RE.match(field_lines[index].rstrip("\r\n"))
            if match is not None and match.group(1) == "M2_RV2_REQUIRED":
                current_field_matches += 1
    if current_field_matches != 0:
        raise PlanSemanticError("AMENDMENT_PRECONDITION_INVALID", "R9 must not already materialize an M2_RV2_REQUIRED field")

    candidate = _replace_plan_revision(body, "R10")
    candidate = _replace_milestone_values(
        candidate,
        {
            "M2_RV2_REQUIRED": "no",
            "M2_RV2_WAIVED_BY_USER": "yes",
            "M2_RV2_WAIVER_DECISION": normalized["decision_ref"],
            "M2_RV2_WAIVER_REASON": M2_RV2_WAIVER_REASON,
        },
    )
    candidate = candidate.replace(_M2_RV2_OLD_LIFECYCLE, _M2_RV2_NEW_LIFECYCLE, 1)
    candidate = candidate.replace(_M2_RV2_OLD_GATE_CLAUSE, _M2_RV2_NEW_GATE_CLAUSE, 1)
    candidate = candidate.replace(_M2_RV2_OLD_AC12, _M2_RV2_NEW_AC12, 1)
    try:
        result = normalize_portable_plan(candidate, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    except Exception as exc:
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", f"RV2 waiver Plan materialization failed admission: {exc}") from exc
    if (
        _next_text_plan_revision(candidate) != "R11"
        or result.current_fields.get("M2_RV2_REQUIRED") != "no"
        or result.current_fields.get("M2_RV2_WAIVED_BY_USER") != "yes"
        or result.current_fields.get("M2_RV2_WAIVER_DECISION") != normalized["decision_ref"]
        or result.current_fields.get("M2_RV2_WAIVER_REASON") != M2_RV2_WAIVER_REASON
        or tuple(result.milestone_work_items.get("M2", ())) != expected_work
        or result.milestone_dependencies.get("M2") != document.milestone_dependencies.get("M2")
    ):
        raise PlanSemanticError("AMENDMENT_RENDER_INVALID", "RV2 waiver readback changed unrelated M2 Work semantics")
    return candidate, normalized


def apply_plan_amendment(
    body: str,
    document: PortablePlanDocument,
    amendment: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    normalized = normalize_plan_amendment(amendment)
    if normalized["kind"] == "add_work_item":
        return add_work_item_to_plan(body, document, normalized)
    return waive_m2_rv2_gate_in_plan(body, document, normalized)


def _has_cycle(nodes: set[str], edges: set[tuple[str, str]]) -> bool:
    outgoing: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        outgoing.setdefault(source, set()).add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(target) for target in outgoing.get(node, ())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(nodes))


def _next_text_plan_revision(body: str) -> str:
    values: list[int] = []
    for section in parse_body_sections(body):
        if section.title != "Plan Identity" and section.kind != "current":
            continue
        raw = section.key_values.get("PLAN_REVISION")
        match = re.fullmatch(r"R([0-9]+)", raw or "")
        if match is not None:
            values.append(int(match.group(1)))
    if not values:
        raise PlanSemanticError("PLAN_REVISION_MISSING", "current Plan has no canonical PLAN_REVISION=R<n>")
    return f"R{max(values) + 1}"


def _line_ending(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def _section_spans(body: str) -> tuple[list[str], list[tuple[int, int, BodySection]]]:
    lines = body.splitlines(keepends=True)
    parsed = parse_body_sections(body)
    spans: list[tuple[int, int, BodySection]] = []
    for index, section in enumerate(parsed):
        start = section.start_line
        end = parsed[index + 1].start_line if index + 1 < len(parsed) else len(lines)
        spans.append((start, end, section))
    return lines, spans


def _replace_key_values(body: str, replacements: Mapping[str, str], *, plan_revision: bool = False) -> str:
    lines, spans = _section_spans(body)
    ending = _line_ending(lines)
    edits: list[tuple[int, int, str]] = []
    found: dict[str, int] = {key: 0 for key in replacements}
    for start, end, section in spans:
        if plan_revision and section.title != "Plan Identity" and section.kind != "current":
            continue
        for line_index in range(start, end):
            text = lines[line_index].rstrip("\r\n")
            match = _KV_RE.match(text)
            if match is None or match.group(1) not in replacements:
                continue
            key = match.group(1)
            found[key] += 1
            replace_end = line_index + 1
            if not match.group(2).strip():
                while replace_end < end:
                    continuation = lines[replace_end].strip()
                    if not continuation or continuation.startswith("```") or _KV_RE.match(continuation) or _HEADING_RE.match(continuation):
                        break
                    replace_end += 1
            edits.append((line_index, replace_end, f"{key}={replacements[key]}{ending}"))
    for key in replacements:
        if found[key] == 0 and plan_revision:
            raise PlanSemanticError("PLAN_REVISION_MISSING", "Plan revision field was not found in current Plan identity")
    for start, end, replacement in reversed(edits):
        lines[start:end] = [replacement]
    missing = [key for key in replacements if found[key] == 0]
    if missing and not plan_revision:
        current_span = next(((start, end) for start, end, section in spans if section.kind == "current"), None)
        if current_span is None:
            raise PlanSemanticError("PLAN_CURRENT_SECTION_MISSING", "cannot add milestone execution fields without a current Plan section")
        # Existing edits can alter line counts; locate the current block again before insertion.
        current_section = next(section for _start, _end, section in spans if section.kind == "current")
        refreshed_lines, refreshed_spans = _section_spans("".join(lines))
        current_start, current_end, _ = next(
            span for span in refreshed_spans if span[2].title == current_section.title and span[2].kind == "current"
        )
        insertion = "".join(f"{key}={replacements[key]}{ending}" for key in missing)
        refreshed_lines[current_end:current_end] = [insertion]
        lines = refreshed_lines
    return "".join(lines)


def _replace_plan_revision(body: str, revision: str) -> str:
    return _replace_key_values(body, {"PLAN_REVISION": revision}, plan_revision=True)


def _replace_milestone_values(body: str, replacements: Mapping[str, str]) -> str:
    return _replace_key_values(body, replacements)


def _insert_work_section(body: str, milestone_id: str, item: Mapping[str, Any]) -> str:
    lines, _spans = _section_spans(body)
    plain = [line.rstrip("\r\n") for line in lines]
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(plain):
        match = _HEADING_RE.match(line)
        if match is not None:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    work_matches: list[tuple[int, int]] = []
    milestone_matches: list[tuple[int, int]] = []
    for heading_index, (start, level, title) in enumerate(headings):
        end = len(plain)
        for next_start, next_level, _next_title in headings[heading_index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        work_match = _WORK_HEADING_RE.match(title)
        if work_match is not None and work_match.group(1).upper() == milestone_id:
            work_matches.append((start, end))
        milestone_match = _MILESTONE_HEADING_RE.match(title)
        if milestone_match is not None and f"M{milestone_match.group(1)[1:]}".upper() == milestone_id:
            milestone_matches.append((start, end))
    if work_matches:
        insert_line = max(end for _start, end in work_matches)
    elif milestone_matches:
        insert_line = max(end for _start, end in milestone_matches)
    else:
        insert_line = len(lines)

    work_id = item["id"]
    ending = _line_ending(lines)
    block = [
        f"#### {milestone_id}/{work_id} - {item['title']}",
        f"Objective: {item['objective']}",
        "Scope:",
        *(f"- {entry}" for entry in item["scope"]),
        f"Dependencies: {', '.join(item['dependencies']) if item['dependencies'] else 'none'}",
        "Acceptance:",
        *(f"- {entry}" for entry in item["acceptance"]),
    ]
    before = "".join(lines[:insert_line])
    after = "".join(lines[insert_line:])
    prefix = "" if not before else ("" if before.endswith(ending + ending) else ending if before.endswith(ending) else ending + ending)
    suffix = "" if not after else ending + ending
    return before + prefix + ending.join(block) + ending + (suffix if after else "") + after


__all__ = [
    "ADD_WORK_ITEM_AMENDMENT_CONTRACT",
    "WAIVE_M2_RV2_GATE_AMENDMENT_CONTRACT",
    "M2_RV2_WAIVER_DECISION_REF",
    "M2_RV2_WAIVER_REASON",
    "MAX_PLAN_SEMANTIC_SECTIONS",
    "MAX_PLAN_SECTION_READ_BYTES",
    "PLAN_AMENDMENT_CONTRACTS",
    "PlanSemanticError",
    "PlanSemanticSection",
    "PlanSemanticIndex",
    "canonical_plan_amendment_digest",
    "normalize_add_work_item",
    "normalize_waive_m2_rv2_gate",
    "normalize_plan_amendment",
    "add_work_item_to_plan",
    "waive_m2_rv2_gate_in_plan",
    "apply_plan_amendment",
    "plan_amendment_contracts_for_help",
]
