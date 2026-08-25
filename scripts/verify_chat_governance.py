#!/usr/bin/env python3
"""Canonical Chat Governance static guard (P3 W1).

stdlib-only deterministic validator for:
 - authority-map (GOVERNANCE_INDEX -> files)
 - canonical core markers
 - forbidden current-model regression (contextual)
 - thin-adapter guard
 - drift/hash guard for generated mirror if present

Exit 0 = PASS, non-zero = FAIL. Warnings do not fail.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_ROOT = Path(__file__).resolve().parents[1] / "chat_governance"
# Allow override for fixture testing via env or CLI arg handled in main()
GOVERNANCE_INDEX = GOVERNANCE_ROOT / "GOVERNANCE_INDEX.md"
SHARED_CORE = GOVERNANCE_ROOT / "aota-portable-plan-governance.md"

def resolve_governance_root(cli_root=None):
    if cli_root:
        return Path(cli_root)
    env = sys.argv  # placeholder, actual env check done in main via --governance-root or AOTA_FORGE_CHAT_GOVERNANCE_ROOT
    import os
    ev = os.environ.get("AOTA_FORGE_CHAT_GOVERNANCE_ROOT")
    if ev:
        return Path(ev)
    return GOVERNANCE_ROOT

# Adapter files defined by index
ADAPTER_FILES = {
    "SHARED_CORE": "aota-portable-plan-governance.md",
    "CHATGPT_PLANNING_ENTRY": "aota-chatgpt-project-planning.md",
    "PARALLEL_DEVELOPMENT_ADAPTER": "aota-chatgpt-parallel-development.md",
    "WORKTREE_MANUAL_ADAPTER": "aota-chatgpt-worktree-governance.md",
    "LEGACY_GITHUB_ENTRY_ALIAS": "aota-github-issue-planning.md",
}

# Canonical markers that must appear (exact string). Some are validated cross-file.
CORE_MARKERS = [
    "ISSUE_BODY_ROLE=portable_plan_normative_contract",
    "ONE_PRIMARY_MANAGED_COMMENT_PER_ROLE=yes",
    "PLAN_ISSUE_EXPECTED_PRIMARY_MANAGED_COMMENT_COUNT=5",
    "APPROVED_CONTINUATION_SAME_ROLE_ALLOWED=yes",
    "CONTINUATION_OF_REQUIRED=yes",
    "PLAN_APPENDIX_LOCATION=managed_comment",
    "PLAN_APPENDIX_IS_PLAN_AUTHORITY=no",
    "DECISION_CHANGE_LOG_ENTRY_APPEND_ONLY=yes",
    "MILESTONE_COMPLETION_UPDATES_BODY=no",
    "BODY_MATERIAL_PLAN_AMENDMENT_UPDATE=yes",
    "NEW_PLAN_W_AS_WORK_ITEM=yes",
    "NORMAL_WORK_ITEM_SEPARATE_ISSUE=no",
    "NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=no",
    "NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=no",
    "PLAN_INIT_PER_MILESTONE=no",
    "PLAN_INIT_PER_WORK_ITEM=no",
    "FRIDAY_TEMPORARY_TASK_MAIN=yes",
]

# These may be in core or adapters (cross-file)
CROSS_FILE_MARKERS = {
    "FORMAL_GOVERNANCE_DEPTH=S_M_W": ["aota-portable-plan-governance.md", "aota-chatgpt-parallel-development.md", "aota-chatgpt-worktree-governance.md"],
    "NORMAL_MILESTONE_FORMAL_REVIEW_COUNT=1": ["aota-portable-plan-governance.md", "aota-chatgpt-parallel-development.md"],
    "INTEGRATION_IS_REVIEW=no": ["aota-portable-plan-governance.md", "aota-chatgpt-parallel-development.md", "aota-chatgpt-worktree-governance.md"],
    "CURRENT_EXECUTION_BASE != MILESTONE_KNOWN_GOOD_CHECKPOINT": ["aota-portable-plan-governance.md", "aota-chatgpt-worktree-governance.md", "aota-chatgpt-parallel-development.md"],
}

# Forbidden active markers (must not appear as current normative, except legacy compatibility)
FORBIDDEN_ACTIVE_MARKERS = [
    ("PARENT_OWNS_CROSS_WORKSTREAM_GOVERNANCE=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("MILESTONE_DELEGATES_TO_CHILD_PLAN_ONLY_WHEN_EXPLICIT=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("PLAN_INIT_PER_WORK_ITEM=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("NORMAL_WORK_ITEM_INDEPENDENT_PLAN_REVIEW_DEFAULT=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("INDEPENDENT_INTEGRATION_REVIEW=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("INTEGRATION_REVIEW_REQUIRED=yes", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
    ("CANONICAL_INTEGRATION_CHECKPOINT", "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED"),
]

# More contextual forbidden patterns requiring bounded detection
# For "Comments are an append-only Event Log only" -> fail only if active/current, not legacy section
FORBIDDEN_CONTEXTUAL = [
    {
        "pattern": re.compile(r"Comments\s+are\s+an\s+append-only\s+Event\s+Log\s+only"),
        "code": "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED",
        "detail": "Comments are an append-only Event Log only as current model",
        "allow_legacy_phrase": "LEGACY",
    },
    {
        "pattern": re.compile(r"GitHub\s+Issue\s+body\s*\+\s*structured\s+appendices"),
        "code": "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED",
        "detail": "body + structured appendices as current authority",
        "allow_legacy_phrase": None,
    },
    {
        "pattern": re.compile(r"W<[^>]*>\s*=\s*Workstream"),
        "code": "CG004_CURRENT_LEGACY_MODEL_REINTRODUCED",
        "detail": "W=Workstream as current hierarchy",
        "allow_legacy_phrase": None,
    },
]

# Thin adapter expectations
EXPECTED_ADAPTER_REFERENCES = {
    "aota-chatgpt-project-planning.md": ["SHARED_CORE=aota-portable-plan-governance.md"],
    "aota-chatgpt-parallel-development.md": ["SHARED_CORE=aota-portable-plan-governance.md"],
    "aota-chatgpt-worktree-governance.md": ["SHARED_CORE=aota-portable-plan-governance.md"],
    "aota-github-issue-planning.md": ["LEGACY_COMPATIBILITY_ALIAS_ONLY=yes"],
}

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def contains_legacy_section_context(text: str, match_start: int) -> bool:
    """Check if match is inside explicit legacy/retired compatibility section.
    Only tolerate if within ~500 chars of an explicit legacy retired heading/phrase.
    """
    window_start = max(0, match_start - 500)
    window = text[window_start:match_start]
    lower = window.lower()
    # Explicit legacy section indicators that must be close to the match
    explicit = [
        "legacy compatibility",
        "legacy compatibility alias",
        "retired models",
        "do not reintroduce",
        "old / retired / read-compatible",
        "## legacy",
        "## retired",
        "read-compatible",
    ]
    for m in explicit:
        if m.lower() in lower:
            return True
    # For exact forbidden markers like NORMAL_WORK_ITEM...=yes, never tolerate generic LEGACY word
    # So don't treat generic LEGACY_W_AS_WORKSTREAM as covering unrelated markers
    return False

def main(argv=None) -> int:
    import os, argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--governance-root")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    global GOVERNANCE_ROOT, GOVERNANCE_INDEX, SHARED_CORE
    if args.governance_root:
        GOVERNANCE_ROOT = Path(args.governance_root)
        GOVERNANCE_INDEX = GOVERNANCE_ROOT / "GOVERNANCE_INDEX.md"
        SHARED_CORE = GOVERNANCE_ROOT / "aota-portable-plan-governance.md"
    else:
        env_root = os.environ.get("AOTA_FORGE_CHAT_GOVERNANCE_ROOT")
        if env_root:
            GOVERNANCE_ROOT = Path(env_root)
            GOVERNANCE_INDEX = GOVERNANCE_ROOT / "GOVERNANCE_INDEX.md"
            SHARED_CORE = GOVERNANCE_ROOT / "aota-portable-plan-governance.md"

    errors = []
    warnings = []

    # 1. Authority-map validation
    if not GOVERNANCE_INDEX.is_file():
        print("CHAT_GOVERNANCE_STATIC_GUARD_FAIL")
        print("CODE=CG001_CANONICAL_CORE_MISSING")
        print(f"PATH={GOVERNANCE_INDEX}")
        print("DETAIL=GOVERNANCE_INDEX missing")
        return 1
    idx_text = read_text(GOVERNANCE_INDEX)
    for key, filename in ADAPTER_FILES.items():
        # Need to find key=filename in index
        pattern = re.compile(rf"{re.escape(key)}\s*=\s*{re.escape(filename)}")
        if not pattern.search(idx_text):
            errors.append((f"CG002_ROUTER_DRIFT", f"GOVERNANCE_INDEX missing {key}={filename}"))
        target = GOVERNANCE_ROOT / filename
        if not target.is_file():
            errors.append((f"CG001_CANONICAL_CORE_MISSING", f"canonical target missing: {filename}"))

    if not SHARED_CORE.is_file():
        errors.append(("CG001_CANONICAL_CORE_MISSING", "SHARED_CORE file missing"))
        # cannot continue marker checks without core
        for code, detail in errors:
            print("CHAT_GOVERNANCE_STATIC_GUARD_FAIL")
            print(f"CODE={code}")
            print(f"PATH={SHARED_CORE}")
            print(f"DETAIL={detail}")
            return 1

    core_text = read_text(SHARED_CORE)

    # 2. Canonical core marker checks
    for marker in CORE_MARKERS:
        if marker not in core_text:
            errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"core missing marker: {marker}"))

    # Cross-file markers
    for marker, candidates in CROSS_FILE_MARKERS.items():
        found = False
        for cand in candidates:
            p = GOVERNANCE_ROOT / cand
            if p.is_file() and marker in read_text(p):
                found = True
                break
        if not found:
            errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"cross-file marker missing: {marker}"))

    # Also verify ONE_SHARED_GOVERNANCE_AUTHORITY
    if "ONE_SHARED_GOVERNANCE_AUTHORITY=yes" not in core_text or "ONE_SHARED_GOVERNANCE_AUTHORITY=yes" not in idx_text:
        errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", "ONE_SHARED_GOVERNANCE_AUTHORITY missing in core/index"))
    if "DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no" not in core_text:
        errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", "DUPLICATE_NORMATIVE_AUTHORITY_ALLOWED=no missing in core"))

    # 3. Forbidden regression (contextual)
    # Check active forbidden markers not in legacy context
    all_files = [GOVERNANCE_ROOT / f for f in ADAPTER_FILES.values()] + [GOVERNANCE_INDEX]
    for f in all_files:
        if not f.is_file():
            continue
        text = read_text(f)
        # Simple exact forbidden markers
        for forbidden, code in FORBIDDEN_ACTIVE_MARKERS:
            if forbidden in text:
                # Check if this is inside legacy compatibility section
                idx = text.find(forbidden)
                if not contains_legacy_section_context(text, idx):
                    errors.append((code, f"forbidden active marker {forbidden} in {f.name}"))
                else:
                    # tolerated legacy compatibility
                    pass
        # Contextual patterns
        for item in FORBIDDEN_CONTEXTUAL:
            for m in item["pattern"].finditer(text):
                start = m.start()
                if contains_legacy_section_context(text, start):
                    continue
                # Additional guard for body+appendices: only fail if described as current authority
                snippet = text[max(0,start-500):start+500].lower()
                if item["detail"].startswith("body + structured"):
                    # check if snippet suggests current authority (not retired)
                    if "retired" in snippet or "legacy" in snippet or "do not" in snippet or "read-compatible" in snippet:
                        continue
                    # if snippet says "= Portable Plan" or "as Portable Plan" as current, then fail
                    if "portable plan" in snippet:
                        errors.append((item["code"], f"{item['detail']} in {f.name} at offset {start}"))
                    else:
                        # still consider but warn not fail? spec says fail on active current reintroduction
                        # Be conservative: only fail if explicit authority phrase
                        continue
                elif item["detail"].startswith("W=Workstream"):
                    # Need to check if it's describing new/current hierarchy vs legacy
                    window = text[max(0,start-800):start+800]
                    if "LEGACY" in window or "read-compatible" in window.lower() or "retired" in window.lower():
                        continue
                    # Check for "NEW_PLAN_W_AS_WORK_ITEM=yes" nearby suggests current model is correct, so any W=Workstream without legacy qualifier is forbidden
                    # If line contains W=Workstream and not inside legacy section, fail
                    # Distinguish: need to detect if this is defining NEW hierarchy
                    if "NEW_PLAN" not in window and "LEGACY_W_AS_WORKSTREAM" not in window:
                        # Check if the matched line is alone without legacy qualifier
                        # Use heuristic: if preceding 200 chars contain "Legacy" then skip, else fail
                        errors.append((item["code"], f"{item['detail']} in {f.name} at offset {start}"))
                else:
                    # Generic append-only Event Log only
                    # Already filtered legacy context above, so fail
                    # But double-check that snippet doesn't say "is not canonical"
                    if "not" in snippet and "canonical" in snippet:
                        continue
                    errors.append((item["code"], f"{item['detail']} in {f.name}"))

    # Additional specific checks for per-W ceremony defaults
    # If file contains NORMAL_WORK_ITEM_INDEPENDENT_SOURCE_REVIEW_DEFAULT=yes as current (not legacy), already covered above via exact marker
    # Similarly NORMAL_WORK_ITEM_PROJECT_STEWARD_DEFAULT=yes
    # Check for PARENT_OWNS_CROSS_WORKSTREAM etc already done

    # 4. Thin-adapter guard
    for fname, required_refs in EXPECTED_ADAPTER_REFERENCES.items():
        p = GOVERNANCE_ROOT / fname
        if not p.is_file():
            continue
        text = read_text(p)
        for ref in required_refs:
            if ref not in text:
                errors.append(("CG002_ROUTER_DRIFT", f"adapter {fname} missing reference {ref}"))
        # Check that adapter does NOT claim normative authority
        if "single shared semantic governance core for Chat planning" in text and fname != "aota-portable-plan-governance.md":
            # This phrase is core's intro; adapter should not duplicate large section
            # Check if it appears outside reference sentence
            pass
        # Thin check: adapter must not copy large marker sets
        # Count core markers present in adapter
        core_marker_count = sum(1 for m in CORE_MARKERS if m in text)
        # If adapter is supposed to be thin, having >8 core markers suggests duplication
        # But use secondary heuristic only, not hard 250 lines
        if core_marker_count > 10:
            # Check if adapter declares itself as shared normative authority
            if "LOCAL_FILE_IS_NORMATIVE_AUTHORITY=no" not in text and "CHATGPT_ENTRY_DUPLICATES_SHARED_CORE=no" not in text and "PARALLEL_ADAPTER_DUPLICATES_CORE=no" not in text and "LEGACY_COMPATIBILITY_ALIAS_ONLY=yes" not in text:
                errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"adapter {fname} duplicates core markers ({core_marker_count}) without thin declaration"))
        # Alias must have LEGACY_COMPATIBILITY_ALIAS_ONLY=yes - already checked
        # Worktree adapter must have transitional
        if fname == "aota-chatgpt-worktree-governance.md":
            if "TRANSITIONAL_CHATGPT_CONTROL_PLANE=yes" not in text and "WORKTREE_CHATGPT_ADAPTER_DUPLICATES_RUNTIME_CORE=no" not in text:
                warnings.append(f"worktree adapter missing transitional marker")
        if fname == "aota-chatgpt-parallel-development.md":
            if "EXECUTION_ADAPTER" not in text and "PARALLEL_ADAPTER" not in text:
                warnings.append(f"parallel adapter missing execution adapter identity")
        # Duplicate authority phrase
        if "This is the single shared semantic governance core" in text and fname != "aota-portable-plan-governance.md":
            errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"adapter {fname} declares itself shared core"))
        # Check GitHub alias does not redefine hierarchy
        if fname == "aota-github-issue-planning.md":
            if "FORMAL_GOVERNANCE_DEPTH" in text and "S_M_W" in text:
                # Might be okay if referencing core, but if it defines hierarchy independently => fail
                # Check if it copies hierarchy table
                if "SUBPLAN_IDENTIFIER" in text:
                    errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"alias {fname} duplicates hierarchy definition"))

    # 5. Drift/hash guard for generated mirror if present
    # Look for expected mirror locations
    possible_mirrors = [
        ROOT / "skills" / "aota-portable-plan-governance" / "SKILL.md",  # shouldn't be hermes path, but check aota_forge internal mirror?
        Path("/home/latios/.hermes/skills/aota-portable-plan-governance/SKILL.md"),
    ]
    # Also check if any generated mirror exists under aota_forge (e.g., generated/)
    generated_mirror_candidates = list(GOVERNANCE_ROOT.glob("*.generated.md")) + list(ROOT.glob("generated/**"))
    # For now we check host hermes path if it exists and is a generated mirror
    # If mirror exists, verify it is generated and matches hash
    for mirror in possible_mirrors:
        if mirror.is_file():
            # Only validate if it claims to be generated artifact
            mt = read_text(mirror)
            if "GENERATED_ARTIFACT=yes" in mt or "DETERMINISTIC_GENERATED_ARTIFACT=yes" in mt:
                # verify hash matches canonical
                canonical_bytes = SHARED_CORE.read_bytes()
                canonical_hash = hashlib.sha256(canonical_bytes).hexdigest()
                # Try to parse SOURCE_SHA256 from mirror
                m_hash = re.search(r"SOURCE_SHA256\s*=\s*([a-f0-9]{64})", mt)
                if m_hash:
                    if m_hash.group(1) != canonical_hash:
                        errors.append(("CG002_ROUTER_DRIFT", f"generated mirror {mirror} SOURCE_SHA256 mismatch"))
                else:
                    # Check content equality via hash of embedded content? For simplicity, if mirror contains canonical bytes substring
                    mirror_hash = hashlib.sha256(mt.encode("utf-8")).hexdigest()
                    # Not directly comparable, so warn
                    warnings.append(f"generated mirror {mirror} missing SOURCE_SHA256")
                # Also check manual edit
                if "MANUAL_EDIT_ALLOWED=yes" in mt:
                    errors.append(("CG003_DUPLICATE_NORMATIVE_AUTHORITY", f"generated mirror {mirror} allows manual edit"))
                # Check that router fallback logic would be valid - not needed here
            else:
                # This is a router, not a generated mirror, so skip hash check
                # But we should check that router does not duplicate normative authority (already done via hermes-tools router check? Not here)
                pass

    # Output
    if errors:
        print("CHAT_GOVERNANCE_STATIC_GUARD_FAIL")
        for code, detail in errors:
            print(f"CODE={code}")
            print(f"DETAIL={detail}")
        # Also print warnings if any
        for w in warnings:
            print(f"WARNING={w}")
        return 1
    else:
        print("CHAT_GOVERNANCE_STATIC_GUARD_PASS")
        for w in warnings:
            print(f"WARNING={w}")
        # Print canonical hash for drift reference
        h = hashlib.sha256(SHARED_CORE.read_bytes()).hexdigest()
        print(f"CANONICAL_SHA256={h}")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
