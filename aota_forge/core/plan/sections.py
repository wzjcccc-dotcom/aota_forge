"""Issue-body section parsing for Portable Plan normalization (M2-D).

Deterministic, executor-neutral parsing of Portable Plan issue bodies.

Sections are split on markdown headings (``#`` .. ``######``).  Content
before the first heading is the preamble and is treated as current-state
authoritative text.  Each section is classified by its title into a bounded
set of kinds:

    appendix        structured appendices / provenance
    control         control-comment projection snapshots
    historical      superseded / historical evidence blocks
    milestone_spec  Milestone definition sections
    governance      governance / authority-model observations
    current         current authoritative Plan state
    unclassified    everything else (observations only)

KEY=VALUE lines are extracted from every section (single-line and
multi-line values).  Only ``current`` sections contribute current-state
fields; historical blocks must never override current state, and repeated
KEY=VALUE occurrences in different sections are NOT all treated as current.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
KV_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$")

KIND_APPENDIX = "appendix"
KIND_CONTROL = "control"
KIND_HISTORICAL = "historical"
KIND_MILESTONE = "milestone_spec"
KIND_GOVERNANCE = "governance"
KIND_CURRENT = "current"
KIND_UNCLASSIFIED = "unclassified"

SECTION_KINDS = (
    KIND_APPENDIX,
    KIND_CONTROL,
    KIND_HISTORICAL,
    KIND_MILESTONE,
    KIND_GOVERNANCE,
    KIND_CURRENT,
    KIND_UNCLASSIFIED,
)

APPENDIX_KEYWORDS = ("appendix", "annex", "provenance")
CONTROL_KEYWORDS = ("control projection", "control-comment", "control comment", "projection snapshot")
HISTORICAL_KEYWORDS = ("historical", "superseded", "archived", "completed", "previous milestone")
GOVERNANCE_KEYWORDS = ("governance", "authority model")
CURRENT_KEYWORDS = ("current", "handoff", "plan status", "status", "active")
MILESTONE_PREFIX_RE = re.compile(r"^m(\d+)(\b|$)")


@dataclass(frozen=True)
class BodySection:
    """One classified body section with its extracted KEY=VALUE map."""

    title: str
    kind: str
    key_values: dict[str, str]
    duplicates: tuple[tuple[str, tuple[str, ...]], ...]
    start_line: int
    end_line: int


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title.lower())).strip()


def classify_section(title: str) -> str:
    """Classify a section title into a bounded section kind (deterministic)."""
    normalized = normalize_title(title)
    if any(keyword in normalized for keyword in APPENDIX_KEYWORDS):
        return KIND_APPENDIX
    if any(keyword in normalized for keyword in CONTROL_KEYWORDS):
        return KIND_CONTROL
    if any(keyword in normalized for keyword in HISTORICAL_KEYWORDS):
        return KIND_HISTORICAL
    if "milestone" in normalized or MILESTONE_PREFIX_RE.match(normalized):
        return KIND_MILESTONE
    if any(keyword in normalized for keyword in GOVERNANCE_KEYWORDS):
        return KIND_GOVERNANCE
    if any(keyword in normalized for keyword in CURRENT_KEYWORDS):
        return KIND_CURRENT
    return KIND_UNCLASSIFIED


def _extract_key_values(lines: list[str]) -> tuple[dict[str, str], tuple[tuple[str, tuple[str, ...]], ...]]:
    """Extract KEY=VALUE pairs, honoring multi-line values.

    A value line continues until a blank line, a fenced-code marker, a new
    KEY= line, or the end of the section.  The FIRST occurrence of a key
    wins; later occurrences are recorded under ``duplicates``.
    """
    key_values: dict[str, str] = {}
    duplicates: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(lines):
        match = KV_RE.match(lines[index])
        if not match:
            index += 1
            continue
        key, first_value = match.group(1), match.group(2).strip()
        if first_value:
            value = first_value
            index += 1
        else:
            continuation: list[str] = []
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor].strip()
                if not candidate or candidate.startswith("```") or KV_RE.match(lines[cursor]):
                    break
                continuation.append(lines[cursor])
                cursor += 1
            value = "\n".join(continuation).strip()
            index = cursor
        if key in key_values:
            duplicates.append((key, (key_values[key], value)))
        else:
            key_values[key] = value
    return key_values, tuple(duplicates)


def parse_body_sections(body: str) -> list[BodySection]:
    """Split a body into classified sections (deterministic).

    Preamble (before the first heading) is classified as current state.
    Empty sections and bodies are tolerated at parse time; emptiness is
    escalated by the normalizer.
    """
    lines = body.splitlines()
    blocks: list[tuple[str | None, int, list[str]]] = []
    current_title: str | None = None
    current_start = 0
    current_lines: list[str] = []
    for line_number, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading:
            if current_lines or current_title is not None:
                blocks.append((current_title, current_start, current_lines))
            current_title = heading.group(2).strip()
            current_start = line_number
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or current_title is not None:
        blocks.append((current_title, current_start, current_lines))

    sections: list[BodySection] = []
    for title, start_line, block_lines in blocks:
        if title is None:
            kind = KIND_CURRENT
            section_title = "(preamble)"
        else:
            kind = classify_section(title)
            section_title = title
        key_values, duplicates = _extract_key_values(block_lines)
        sections.append(
            BodySection(
                title=section_title,
                kind=kind,
                key_values=key_values,
                duplicates=duplicates,
                start_line=start_line,
                end_line=start_line + len(block_lines),
            )
        )
    return sections


def section_diagnostics(section: BodySection) -> list[dict[str, Any]]:
    """Bounded diagnostics for one parsed section (parser-level)."""
    diagnostics: list[dict[str, Any]] = []
    if section.kind == KIND_UNCLASSIFIED:
        diagnostics.append({"code": "UNCLASSIFIED_SECTION", "severity": "info", "section": section.title})
    elif section.kind == KIND_HISTORICAL:
        diagnostics.append({"code": "HISTORICAL_SECTION", "severity": "info", "section": section.title})
    elif section.kind == KIND_APPENDIX:
        diagnostics.append({"code": "APPENDIX_SECTION", "severity": "info", "section": section.title})
    for key, values in section.duplicates:
        distinct = len(set(values))
        diagnostics.append(
            {
                "code": "DUPLICATE_KEY_IN_SECTION",
                "severity": "warning" if distinct > 1 else "info",
                "section": section.title,
                "key": key,
                "values": list(values),
            }
        )
    return diagnostics
